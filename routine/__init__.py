"""人设日程引擎 (1.0.355): 读 <人设>_routine.txt, 按"持续时间"一段接一段地算出对方每天的日程, 并生成对方的 OIT 数据.

和旧的"每天重复的固定钟点"日程 (comm.schedule_entries) 不同:
  · 日程是一串**持续时间**: 起床 (初始起点 + 起床波动) 之后, 一项接一项; 做完最后一项就进入睡眠, 睡到第二天起床. 没有固定的睡觉钟点.
  · 每天的波动 (起床 / 专注开始 / 专注结束 / 睡眠开始) 由 (日期 + 人设) 决定, 同一天任何设备算出来都一样 (sha256, Python 和 Kotlin 一致).
    起床波动平移整天; 专注开始 / 结束的波动挪动专注段的两端 (晚开始则前一段变长, 早开始则前一段被截短, 不留空档); 睡眠开始的延迟算"空闲".
  · 循环 (1.0.400) 总是以休息结束: 不直接截断最后一轮, 而是让专注提前结束, 休息的末尾对上循环的末尾; 最后那次休息再比循环的结束晚 [循环末尾休息结束时间波动] 分钟, 后面的日程整体往后推.
  · 所有数据都是**实时算**的: 只给已经发生的部分 (整点记录只给已经结束的格子, 计时器 / 日历 / 统计只算到现在).
  · 算过的会存下来 (Store): 时间线 (实际发生的一段段) 和整点记录的格子. 好几天没被调用 (程序没开 / 没人查) 之后, 缺的部分按规则补齐.

这个模块不依赖 comm (comm 依赖它); 只用 config.compute_F 算休息时长, contract 算合约记录.
"""
import json
import os
from datetime import datetime, timedelta, date as _date

import config

from .routine_common import (
    KEEP_JOINT,
    KEEP_DAYS,
    SLEEP_CELL,
    BACKFILL_DAYS,
    SYNC_MAX_DAYS,
    CHAT_FULL,
    CHAT_FULL_MSGS,
    BASE_FLUCT,
    NET_SHARE,
    NET_QUOTA_S,
    CONTRACT_DAY_P,
    NAMES_NO_PROB,
    _NUM,
    _UNIT,
    _DUR_RE,
    _LOOP_RE,
    _START_RE,
    _FOCUS_RE,
    _RANGE,
    _FLUCT_KEYS,
    _LIST_RE,
    _DEV_WIN_RE,
    _ITEM_RE,
    _DIFF_RE,
    u01,
    rint,
    _minutes,
    parse_list,
    resolve_list,
    _iso,
    _dt,
)
from .routine_parse import (
    Routine,
    parse,
)
from .routine_plan import (
    _secs,
    _rest_min,
    loop_pieces,
    loop_segs,
    seg,
    wake_at,
    wake_times,
    day_plan,
    _day_plan,
    plan_between,
    shift_for_joint,
    seg_class,
    status_in,
    fluct_list,
    pick,
    chat_pct,
)
from .routine_store import (
    Store,
)
from .routine_query import (
    contract_for_day,
    banned_ids,
    contracts_before,
)


def _selftest():
    import tempfile
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    routine_text = open(os.path.join(here, "personas", "Claudia_routine.txt"), encoding="utf-8").read()
    # 起床波动是 [-5,0]; 这里仍用 [-20,10] 测 (Kotlin RoutineTest 的期望值是按它算的, 电脑和手机必须一样), 新范围见 comm.py 自检的共时闹钟
    assert "睡眠结束时间波动:[-5,0]" in routine_text
    routine_text = routine_text.replace("睡眠结束时间波动:[-5,0]", "睡眠结束时间波动:[-20,10]")
    r = parse(routine_text)
    assert r and r.start == 390 and r.focus_label == "学习(高考内容)"
    assert [(it["type"], it["dur"]) for it in r.items] == [("block", 30), ("loop", 300), ("block", 30), ("block", 30), ("loop", 300), ("block", 30), ("block", 30), ("loop", 240), ("block", 30)]
    assert [it.get("focus") for it in r.items if it["type"] == "loop"] == [100, 100, 50]
    assert r.fluct == {"focus_start": (-3, 2), "focus_end": (-2, 3), "sleep_start": (0, 5), "sleep_end": (-20, 10), "rest_end": (0, 5)}
    assert parse(routine_text.replace("循环末尾休息结束时间波动:[0,5]", "")).fluct["rest_end"] == (0, 0), "没写 = 不晚"
    assert parse(routine_text.replace("循环末尾休息结束时间波动:[0,5]", "循环末尾休息结束时间波动:[1,7]")).fluct["rest_end"] == (1, 7), "改了文件自动识别"
    assert r.devices == ["DESKTOP-CLAUDIA", "C1999W"] and set(r.windows) == {0, 1}
    assert r.diff == (20, 30) and r.skip_terms == ["无言为真", "持久战"] and r.bad_terms == ["通道阻塞", "细化规范", "乐理之灾", "未知变量"]
    assert "联网时间理由列表" in r.base and "整点记录专注填写列表" not in r.base
    # 规则 IV: 没写概率的平分剩下的; X% 随聊天变
    foc = resolve_list(r.lists["整点记录专注填写列表"])
    assert foc[0] == ("英语", 30.0) and all(abs(p - 14.0) < 1e-9 for _n, p in foc[1:]) and len(foc) == 6
    rest = resolve_list(r.lists["整点记录休息填写列表"], 20.0)
    assert rest[0] == ("聊天", 20.0) and all(abs(p - 20.0) < 1e-9 for _n, p in rest[1:])
    assert resolve_list(r.lists["整点记录休息填写列表"], 0.0)[1][1] == 25.0
    # 规则 V: 聊天概率
    assert chat_pct(0, 60) == 0 and chat_pct(20, 60) == 50 and chat_pct(40, 60) == 50 and chat_pct(10, 60) == 25
    assert chat_pct(10, 30) == 50 and chat_pct(5, 30) == 25, "细化规范的半小时格: 阈值按比例减半"
    # 规则 VI: 基准数据 ±2%, 加起来是 100% 的归一
    for day in range(30):
        fl = fluct_list(r.lists["联网时间理由列表"], f"s|{day}", True)
        assert abs(sum(p for _n, p in fl) - 100.0) < 1e-6
        for (n, p), (n0, p0) in zip(fl, resolve_list(r.lists["联网时间理由列表"])):
            assert abs(p / p0 - 1) <= 0.0201 * 100 / 100 + 0.021, (n, p, p0)
    w = fluct_list(r.lists["设备-1:窗口使用时间"], "s|w", True)
    assert all(abs(p / p0 - 1) <= 0.045 for (_n, p), (_m, p0) in zip(w, resolve_list(r.lists["设备-1:窗口使用时间"]))), "窗口项: 每项 ±2% (名单加起来 100% 时再归一, 最多约 ±4%)"
    assert fluct_list(r.lists["整点记录专注填写列表"], "s|x", False) == resolve_list(r.lists["整点记录专注填写列表"])
    # 日程: 持续时间依次排下去, 做完最后一项进入睡眠; 起床波动平移整天
    seed = "t"
    saw_early = False
    for k in range(60):
        d = _date(2026, 3, 1) + timedelta(days=k)
        plan = day_plan(r, d, seed)
        wk = wake_at(r, d, seed)
        assert datetime.combine(d, datetime.min.time()) + timedelta(minutes=390 - 20) <= wk <= datetime.combine(d, datetime.min.time()) + timedelta(minutes=390 + 10)
        assert plan[0]["start"] == wk and plan[0]["label"] == "洗漱,吃早饭" and timedelta(minutes=27) <= plan[0]["end"] - plan[0]["start"] <= timedelta(minutes=33)
        for a, b in zip(plan, plan[1:]):
            assert a["end"] == b["start"], (a, b)                    # 首尾相接, 没有重叠也没有缺口
        assert plan[-1]["kind"] == "sleep" and plan[-1]["end"] == wake_at(r, d + timedelta(days=1), seed)
        late = sum(rint(f"{seed}|le|{d}|{i}", 0, 5) for i, it in enumerate(r.items) if it["type"] == "loop")      # 三个循环最后一次休息各自晚结束的分钟, 后面的日程跟着推
        act_end = wk + timedelta(minutes=sum(it["dur"] for it in r.items) + late)
        s_start = plan[-1]["start"]
        assert act_end <= s_start <= act_end + timedelta(minutes=5), "睡眠开始 = 活动做完 + [0,5] 分钟"
        if s_start > act_end:
            assert plan[-2]["kind"] == "idle" and plan[-2]["gap"] and plan[-2]["end"] == s_start, "II: 睡眠被延迟的这段算空闲"
        foc = [s for s in plan if s["kind"] == "focus"]
        assert len(foc) == 10, len(foc)
        # 循环总以休息结束, 休息的末尾 = 循环的末尾 + [0,5] 分钟, 下一项活动紧接着从那里开始
        cur_nom = wk
        for i, it in enumerate(r.items):
            nom_end = cur_nom + timedelta(minutes=it["dur"])
            if it["type"] == "loop":
                ls = [s for s in plan if s["loop"] == i]
                assert ls[-1]["kind"] == "rest" and ls[0]["kind"] == "focus", "循环以休息结束"
                dl = (ls[-1]["end"] - nom_end).total_seconds() / 60
                assert dl == rint(f"{seed}|le|{d}|{i}", 0, 5) and 0 <= dl <= 5, ("休息比循环的结束晚 [0,5] 分钟", dl)
                nom_end = ls[-1]["end"]
                nxt = next((s for s in plan if s["start"] == nom_end and s["loop"] is None), None)
                assert nxt is not None, "循环后面的活动从休息结束处开始"
            cur_nom = nom_end
        assert not any(s["gap"] for s in plan[:-2]), "循环里不留空档 (专注晚开始 → 前一段变长)"
        first = foc[0]
        nominal_first = wk + timedelta(minutes=30)
        assert -3 <= (first["start"] - nominal_first).total_seconds() / 60 <= 2, "专注开始波动 [-3,2]"
        saw_early |= first["start"] < nominal_first
    assert saw_early
    # 休息时长 = F(n): 100 分钟专注后休息 38 分钟
    d0 = _date(2026, 3, 1)
    p0 = day_plan(r, d0, seed, fluct=False)
    mins = lambda ss: [(s["kind"], int((s["end"] - s["start"]).total_seconds() // 60)) for s in ss]
    assert mins(p0[:9]) == [("idle", 30), ("focus", 100), ("rest", 38), ("focus", 100), ("rest", 38), ("focus", 19), ("rest", 5), ("idle", 30), ("idle", 30)][:9], "300min 循环: 最后一轮专注提前结束, 休息随它重新算 (F(19) = 5), 末尾对上循环的末尾"
    assert p0[6]["end"] == p0[0]["end"] + timedelta(minutes=300) and p0[7]["label"] == "午饭"
    assert mins([s for s in p0 if s["loop"] == 7]) == [("focus", 50), ("rest", 16), ("focus", 50), ("rest", 16), ("focus", 50), ("rest", 16), ("focus", 32), ("rest", 10)], "240min / 50min 循环: 最后一轮专注 32 + 休息 10"
    pp = lambda total, n: [(k, s / 60) for k, s in loop_pieces(total * 60, n * 60)]
    assert pp(276, 100) == [("focus", 100), ("rest", 38), ("focus", 100), ("rest", 38)], "正好排满"
    assert pp(200, 100) == [("focus", 100), ("rest", 38), ("focus", 47), ("rest", 15)], "最后一轮的专注提前结束, 休息跟着变短"
    assert pp(100, 100) == [("focus", 74), ("rest", 26)] and pp(20, 100) == [("focus", 16), ("rest", 4)], "放不下一整轮: 专注提前结束"
    assert pp(139, 100) == [("focus", 100), ("rest", 39)], "剩下的太短 (专注不到 1 分钟): 并进上一次休息"
    assert pp(1, 100) == [("focus", 1)], "一轮都排不了: 只剩专注"
    for tot in range(2, 700, 7):
        ps = pp(tot, 100)
        assert ps[-1][0] == "rest" and sum(m for _k, m in ps) == tot and all(m <= 100 for k, m in ps if k == "focus"), (tot, ps)
        for (k1, m1), (k2, m2) in zip(ps, ps[1:]):
            if k1 == "focus" and k2 == "rest" and m1 < 100 and m2 == ps[-1][1]:
                assert abs(m2 - config.compute_F(m1)) <= 1.01, (tot, ps)      # 提前结束的专注: 休息按它的长度算 (取整分钟, 差不到 1 分钟)
    assert [s["label"] for s in p0 if s["kind"] == "idle"] == ["洗漱,吃早饭", "午饭", "空闲", "晚饭", "空闲", "特殊空闲"]
    assert day_plan(r, d0, seed) == day_plan(r, d0, seed), "同一天总是同一个结果 (设备一致)"
    assert day_plan(r, d0, seed) != day_plan(r, d0 + timedelta(days=1), seed)
    # 相邻两天首尾相接
    pb = plan_between(r, datetime(2026, 3, 1, 12), datetime(2026, 3, 4, 12), seed)
    for a, b in zip(pb, pb[1:]):
        assert a["end"] == b["start"]
    # 联合行动之后重算: t 落在循环里 → 立即开始专注; 落在循环外不影响
    segs = plan_between(r, datetime(2026, 3, 2, 6), datetime(2026, 3, 2, 23), seed)
    rest_seg = next(s for s in segs if s["kind"] == "rest")
    t = rest_seg["start"] + timedelta(minutes=10)
    for s in segs:
        if s.get("loop") is not None:
            s["n"] = r.items[s["loop"]]["focus"]
    sh = shift_for_joint(segs, t, t + timedelta(minutes=1))
    cur = status_in(sh, t + timedelta(minutes=1))
    assert cur["kind"] == "focus" and cur["start"] == t, "休息结束后落在循环里 → 立即开始专注"
    meal = next(s for s in segs if s["kind"] == "idle" and s["label"] == "午饭")
    assert shift_for_joint(segs, meal["start"] + timedelta(minutes=5), meal["start"] + timedelta(minutes=6)) == segs, "落在吃饭里不影响"
    # 存档 + 数据
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "routine_data.json")
        st = Store(path, r, seed)
        now = datetime(2026, 3, 10, 10, 20)
        fn = lambda a, b: plan_between(r, a, b, seed)
        st.fill_timeline(now, fn)
        assert st.upto == now.replace(second=0) and st.tl and st.tl[0][1].startswith("2026-03-03")
        n_before = len(st.tl)
        assert abs(st.minutes(datetime(2026, 3, 3), now) - (now - datetime(2026, 3, 3)).total_seconds() / 60) < 1e-6, "时间线没有缺口"
        st.fill_cells(now, lambda a, b: 0)
        lines = st.log_lines(now)
        assert lines and all(not l.startswith("10:") for l in lines), "没结束的格子不给 (10:00 这一格要到 11:00)"
        assert any(" " in l for l in lines)
        # 联合行动 (override 带 since): since 之前没记到的按日程补, since 之后按联合行动里的状态记; 联合行动里的空闲记成"空闲"
        stj = Store(os.path.join(td, "routine_joint.json"), r, seed)
        stj.fill_timeline(now, fn)
        t1, t2 = now + timedelta(minutes=30), now + timedelta(minutes=45)
        stj.fill_timeline(t2, fn, ("focus", "联合行动", t1))
        assert stj.upto == t2 and abs(stj.minutes(now, t2) - 45) < 1e-6, "时间线没有缺口"
        assert abs(stj.minutes(t1, t2, ("focus",)) - 15) < 1e-6 and all(l == "联合行动" for k, a, b, l in stj.segments(t1, t2)), "since 之后记成联合行动"
        assert not any(l == "联合行动" for k, a, b, l in stj.segments(now, t1)), "since 之前不算联合行动"
        t3 = t2 + timedelta(minutes=10)
        stj.fill_timeline(t3, fn, ("idle", "空闲", t1))
        assert [(k, l) for k, a, b, l in stj.segments(t2, t3)] == [("idle", "空闲")], "联合行动里的空闲记成空闲"
        # 没被调用的时候: 往后补
        later = datetime(2026, 3, 13, 9, 40)
        st.fill_timeline(later, fn)
        st.fill_cells(later, lambda a, b: 0)
        assert abs(st.minutes(datetime(2026, 3, 3), later) - (later - datetime(2026, 3, 3)).total_seconds() / 60) < 1e-6
        assert f"2026-03-11T08:00" in st.cells and st.cells["2026-03-11T08:00"][1], "三天没调用: 自动按规则填充"
        # 睡着的格子 (睡眠占一半以上) 记成"睡眠"; 旧版本留下的空格子重新算
        assert st.cells["2026-03-11T03:00"][1] == "睡眠" and "03:00 睡眠" in st.log_lines(datetime(2026, 3, 11, 8, 0))
        for k in [k for k, v in st.cells.items() if v[1] == "睡眠"]:
            sl = st.minutes(datetime.fromisoformat(k), datetime.fromisoformat(k) + timedelta(minutes=st.cells[k][0]), ("sleep",))
            assert sl >= st.cells[k][0] / 2.0, ("不到一半的睡眠不记成睡眠", k, sl)
        assert not any(v[1] == "" for v in st.cells.values())
        _old = Store(os.path.join(td, "old.json"), r, seed)
        _old.tl, _old.upto, _old.cells = list(st.tl), st.upto, {k: [v[0], ""] if v[1] == "睡眠" else list(v) for k, v in st.cells.items()}
        _old.fill_cells(later, lambda a, b: 0)
        assert _old.cells == st.cells, "旧存档里的空格子补成睡眠"
        # 聊天概率: 用户一小时发 40 条 → 休息格里 "聊天" 的概率 50%; 0 条 → 不会出现聊天
        st2 = Store(os.path.join(td, "b.json"), r, "chat")
        st2.fill_timeline(datetime(2026, 3, 12, 0), fn)
        st2.fill_cells(datetime(2026, 3, 12, 0), lambda a, b: 0)
        assert all("聊天" != v[1] for v in st2.cells.values()), "0 条消息: 聊天概率 0"
        st3 = Store(os.path.join(td, "c.json"), r, "chat")
        st3.fill_timeline(datetime(2026, 3, 12, 0), fn)
        st3.fill_cells(datetime(2026, 3, 12, 0), lambda a, b: 40)
        chats = sum(1 for v in st3.cells.values() if v[1] == "聊天")
        rests = sum(1 for k, v in st3.cells.items() if v[1] in ("聊天", "弹琴", "阅读", "打扫", "散步"))
        assert rests and chats / rests > 0.25, (chats, rests)
        # 存盘 + 读回
        open(path, "w", encoding="utf-8").write(json.dumps(st.payload(), ensure_ascii=False))
        st4 = Store(path, r, seed)
        assert st4.upto == st.upto and st4.tl == st.tl and st4.cells == st.cells
        assert Store(path, r, "other").tl == [], "种子不同的存档不读"
        # 联网 / 窗口: 只算已经过去的时间
        d = later.date()
        net = st.net_by_reason(d)
        assert set(net) == {"查资料", "下载资源", "娱乐"} and net["查资料"] > net["娱乐"] > 0
        assert st.net_by_reason(d + timedelta(days=1)) == {}, "明天还没发生: 没有数据"
        w0 = st.window_seconds(d, 0)
        assert w0 and "" in w0 and abs(sum(w0.values()) - st.day_minutes(d, ("focus",)) * 60) < 1e-6
        # 合约记录: 难度 20~30, 不碰不选择 / 违规的词条
        import contract as C
        banned = banned_ids(r)
        assert {"long1", "long2", "long3", "silent", "chan1", "chan2", "fine", "music", "unknown"} <= banned and "tired1" not in banned
        recs = contracts_before(r, datetime(2026, 4, 1), seed, days=60)
        assert recs and all(20 <= x["level"] <= 30 and not (set(x["terms"]) & banned) for x in recs), [x["level"] for x in recs]
        assert all(_dt(x["end"]) <= datetime(2026, 4, 1) for x in recs)
    print("routine selftest OK")
