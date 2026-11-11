"""
合约 (MECHANICS.md 第 18 节): 词条, 进行中的合约, 结束后的记录. 界面在 pages/contract_page.py.

存档 contract.json: 进行中的合约 (live), 结束了的合约 (history, 整点记录的锁定也看它), 没开始的草稿 (draft).
live 和 history 随设备同步 (doc / merge), draft 只留在本机.
"""

import os
import random
from datetime import datetime, timedelta

import config
from config import load_json_file, save_json_file


from contract_parts.contract_rules import (
    BASE_SECONDS, IDLE_LIMIT, OFFLINE_LIMIT, VIOLATION_LIMIT, HISTORY_KEEP, FRESH_MS, TERMS,
    BY_ID, GROUPS, NEEDS, BOARD, PAGE_SPLIT, LAYOUT, BOARD_COLS, MAX_LEVEL, PAGES, COMBO_PAGE,
    SOFT, LOWER, SINGLE, DURATION, NET_SHARE, BASE_REST, REST, REST_BONUS, LONG_REWARD, WINDOWS,
    SUPPLY, HOUR_TERMS, LIFE, RECORD, BANS, SUPPLY_WARN, DEEP, FOG_SWITCHES, JAM_BLOCKS,
    SEAL_EXTRA, COOLDOWN_X, RANDOM, RANDOM_LEVELS, RANDOM_SKIP, RANDOM_DELAY, RANDOM_TERM_P,
    RANDOM_EFFECTS, RANDOM_REST, RANDOM_SLOW, RANDOM_ROLLS, ECHO_COOLDOWN, MUSIC_EXES,
    AUDIO_EXTS, SOCIAL_EXES, SOCIAL_TITLES, SEARCH_TITLES, site_title, VIDEO_EXES, VIDEO_TITLES,
    BROWSER_EXES, GAME_EXES, BAN_EXES, NET_BAN_EXES, BAN_DOMAINS, BAN_TITLES, COMBOS,
    COMBOS_BY_ID, THEMES, DEFAULT_THEME, THEME_COMBO, THEME_END, clean_theme, theme_end,
    theme_note, COMBO_COLORS, GROUP_NAMES, req_name, combo_done, combo_state, combo_level,
    combo_rest, combo_gift, combo_end, combo_marks, level_of, life_of, rest_factor_of,
    level_label, rest_minutes, rest_reward, check_mark, REST_SHARE_FROM, REST_SHARE_NEAR,
    rest_share, rest_minutes_total, rest_gift, duration_of, conflicts, blocked_by, needs_of,
    clean_terms, _iso, recent_completed, terms_of_record, record_rows, record_marks, _parse,
    _now_ms, FOG_SLOTS, FOG_POOL, _fog_words
)

from contract_parts.contract_hours import HoursMixin, cell_fill
from contract_parts.contract_store import StoreMixin
from contract_parts.contract_live import LiveMixin
from contract_parts.contract_net import NetMixin
from contract_parts.contract_random import RandomMixin
from contract_parts.contract_watch import WatchMixin
from contract_parts.contract_effects import EffectsMixin


class Contract(StoreMixin, LiveMixin, NetMixin, RandomMixin, WatchMixin, EffectsMixin, HoursMixin):
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "contract.json")
        self.live = {"state": "none"}
        self.history = []
        self.draft = []
        # 时间重叠的两个合约只能留一个 (MECHANICS 15.5): 作废了的合约 id (随同步), 等用户选的冲突 (只在本机)
        self.voided = set()
        self.conflicts = []                   # [(a, b)]: 两个合约的摘要 (见 _brief), a 是本机原来的
        self._watch = {}                      # 软规则: 此刻是不是已经超了 (只记在本机)
        self.blocked = lambda: False          # 重置中: 不写文件
        self.load()


def _selftest():
    import tempfile
    assert level_of(["long3", "tired3", "info3", "last"]) == 12, "最后防线没有额外等级了, 只有初始目标生命值 1"
    assert level_of(["long3", "tired3"]) == 6 and level_of(["last", "long1"]) == 4
    assert life_of([]) == 4 and life_of(["retreat1"]) == 3 and life_of(["retreat2"]) == 2 and life_of(["last"]) == 1
    assert conflicts("retreat1", "last") and conflicts("retreat2", "last"), "退路封锁和最后防线是同一组"
    assert not {"vlc.exe", "potplayermini64.exe", "mpc-hc64.exe", "wmplayer.exe"} & MUSIC_EXES, "视频播放器不算"
    assert level_label(9, 12) == "9[12-3]" and level_label(12, 12) == "12" and level_label(13, 12) == "13"
    assert rest_minutes(0) == 20 and rest_minutes(1) == 21 and rest_minutes(4) == 23 and rest_minutes(19) == 36
    assert rest_minutes(15) == 33 and rest_minutes(29) == 44 and rest_minutes(30) == 45 and rest_minutes(50) == 45 and rest_reward(45) == 45 * 60
    assert sum(rest_minutes(i + 1) - rest_minutes(i) for i in range(30)) == 25, "20 → 45: 每一级约 +0.83 分钟, 均匀填满"
    # 持久战: 结算的休息时长 ×110 / 120 / 130% (只影响完成合约的奖励, 不影响合约中每段专注换来的休息)
    assert rest_reward(10, ["long1"]) == round(rest_minutes(10) * 60 * 1.1)
    assert rest_reward(10, ["long3"]) == round(rest_minutes(10) * 60 * 1.3) and rest_reward(10, ["tired2"]) == rest_minutes(10) * 60
    assert all(rest_minutes(i) <= rest_minutes(i + 1) for i in range(40))
    # 删掉的两个 4 级词条: 互联网娱乐的败退 变成组合, 信息隔绝 (旧词条) 删了
    assert "isolate" not in BY_ID and "ent" not in BY_ID and MAX_LEVEL == 3, "没有了 4 级的词条: 词条板不再留那一行"
    assert BY_ID["silent"][2] == 2, "无言为真调整为 2 级"
    assert (NET_SHARE["info1"], NET_SHARE["info2"], NET_SHARE["info3"]) == (0.8, 0.65, 0.5), "信息阻断: 80% 65% 50%"
    assert conflicts("long1", "long3") and conflicts("short1", "short2"), "同一组互相冲突"
    assert not conflicts("erode1", "info3") and not conflicts("suppress", "quiet") and not conflicts("quiet", "jam")
    assert not conflicts("erode1", "info2") and not conflicts("suppress", "fog")
    assert not conflicts("jam", "music") and not conflicts("seal", "fine")
    assert clean_terms(["long2", "long1", "x", "suppress", "jam"]) == ["long2", "suppress", "jam"]
    assert duration_of([]) == 3 * 3600 and duration_of(["long3"]) == int(round(3 * 3600 * 1.6))
    # 词条板: 每个词条正好一格, 一列里每个等级最多一个; 同一组 (系列) 在同一列; 两页各 18 个词条
    assert sorted(t for col in BOARD for t in col) == sorted(BY_ID) and len(set(LAYOUT.values())) == len(TERMS)
    assert all([BY_ID[t][2] for t in col] == sorted({BY_ID[t][2] for t in col}) for col in BOARD)
    assert all(len({LAYOUT[t][0] for t in BY_ID if BY_ID[t][1] == g}) == 1 for g in GROUPS)
    assert len(BOARD) == 20 and [a for a, b in PAGES] + [PAGES[-1][1]] == [0, 10, 20], "两页各 10 列"
    assert [sum(len(col) for col in BOARD[a:b]) for a, b in PAGES] == [23, 13]
    assert [t[0] for t in TERMS] == [t for col in BOARD for t in col], "TERMS 的顺序同词条板"
    # 相似的放在一起: 无言为真在乐理之灾下面, 细化规范在记录封锁下面, 全频道阻塞干扰在抑制下面 (目光 / 刻痕现在都是 1 级, 各占一列)
    for up, down in (("music", "silent"), ("seal", "fine"), ("suppress", "jam"), ("fog", "unknown")):
        assert LAYOUT[up][0] == LAYOUT[down][0], (up, down)
    assert [LAYOUT[t][0] for t in ("music", "game", "video", "gaze", "scar", "move", "confine")] == \
        list(range(LAYOUT["music"][0], LAYOUT["music"][0] + 7)), "网络禁令, 录像, 现实挨着"
    assert LAYOUT["hoard"][0] == LAYOUT["short1"][0] + 1 and LAYOUT["chan1"][0] == LAYOUT["deep1"][0] + 1
    assert LAYOUT["deep1"][0] < PAGE_SPLIT and LAYOUT["info1"][0] < PAGE_SPLIT <= LAYOUT["suppress"][0] and LAYOUT["seal"][0] < PAGE_SPLIT
    # 高机动只留 1 级 (旧的高机动-I / II 删掉了), 和禁足挨着; 记录封锁的描述换掉了
    assert "move1" not in BY_ID and "move2" not in BY_ID
    assert BY_ID["move"][2] == 1 and BY_ID["move"][4] == "现实中的移动速度+50%"
    assert BY_ID["confine"][2] == 1 and BY_ID["confine"][4] == "在非休息时间不能离开工作岗位进行活动"
    assert LAYOUT["confine"][0] == LAYOUT["move"][0] + 1, "高机动 / 禁足 挨着"
    assert BY_ID["seal"][4] == "无法修改已经被填写的格子 (包括自动填写和手动填写); 每次完成填写时, 另外随机封锁两个可以封锁的格子"
    # 目光 / 刻痕: 都是 1 级, 不再冲突; 高机动 / 禁足的前置条件是目光
    assert BY_ID["gaze"][2] == 1 and BY_ID["scar"][2] == 1 and not conflicts("gaze", "scar") and not conflicts("gaze", "music")
    assert needs_of("move", []) == "gaze" and needs_of("confine", ["scar"]) == "gaze" and needs_of("move", ["gaze"]) == ""
    assert needs_of("music", []) == "" and clean_terms(["move", "music"]) == ["music", "move"]
    assert clean_terms(["move", "confine", "music"], prereq=True) == ["music"], "没选目光: 高机动 / 禁足去掉"
    assert clean_terms(["confine", "gaze", "scar"], prereq=True) == ["gaze", "scar", "confine"]
    assert conflicts("hoard", "short1") and conflicts("short2", "hoard"), "禁止囤囤鼠! 和支援短缺冲突"
    assert clean_terms(["short1", "hoard"]) == ["short1"]
    assert not conflicts("silent", "music") and not conflicts("silent", "game"), "禁止软件的词条可以一起选"

    # 词条组合: 系列任意一档都算; 凑齐了加危机等级 / 乘区 / 开始时或完成时的休息时间
    mochi, nofun, flawless, isolation = (COMBOS_BY_ID[i] for i in ("mochi", "nofun", "flawless", "isolation"))
    assert combo_done(mochi, ["long1", "tired2", "fine", "deep1"])
    assert not combo_done(mochi, ["long1", "tired2", "fine"]) and not combo_done(mochi, ["long1", "tired2"])
    assert combo_state(mochi, ["long1", "tired2"]) == "open"
    assert combo_state(mochi, ["long1", "tired2"], ["long1", "tired2", "fine", "deep1"]) == "void"
    assert combo_state(mochi, ["long1", "tired2", "fine", "deep1"]) == "done"
    assert combo_level(["long1", "tired2", "fine", "deep1"]) == 1
    assert abs(rest_factor_of(["long1", "tired2", "fine", "deep1"]) - 0.6 * 1.2) < 1e-9
    iso = ["info1", "erode1", "chan1", "suppress", "fog", "quiet", "jam"]
    assert sorted(clean_terms(iso)) == sorted(iso), "信息隔绝组合的词条可以一起选"
    assert combo_done(isolation, iso) and combo_level(iso) == 1 and combo_end(iso) == 10 * 60
    assert rest_reward(6, iso) == rest_minutes(6) * 60 + 10 * 60
    assert rest_minutes_total(6, iso) == rest_minutes(6) + 10
    assert combo_gift(["music", "silent", "game", "video"]) == 20 * 60, "互联网娱乐的败退: 开始时 +20 分钟"
    assert combo_gift(["last", "unknown"]) == 0, "无懈可击: 还差 禁足 / 高机动 / 目光 / 刻痕"
    assert combo_gift(["last", "unknown", "move", "confine"]) == 0, "无懈可击: 还差 目光 / 刻痕"
    flaw = ["last", "unknown", "move", "confine", "gaze", "scar"]
    assert combo_gift(flaw) == 20 * 60, "无懈可击: 开始时 +20 分钟"
    assert combo_marks(flaw) == {t: COMBO_COLORS["blue"] for t in flaw}
    assert combo_marks(["long1", "tired2"]) == {}
    assert nofun["color"] == "purple" and flawless["color"] == "blue" and isolation["color"] == "red" \
        and mochi["color"] == "yellow"
    assert req_name("deep") == "纵深推进" and req_name("last") == "最后防线" and req_name("silent") == "无言为真"
    # 合约主题: 起源 + 无懈可击 完成时休息额外 +5 分钟 (别的主题 / 没有主题 / 组合没凑齐都没有; 都市之意没有加成)
    assert theme_end(flaw, "ops") == 5 * 60 and theme_end(flaw, "urban") == 0 and theme_end(flaw, "") == 0
    assert theme_end(iso, "urban") == 0 and theme_end(iso, "ops") == 0 and theme_end(iso, "bogus") == 0
    assert theme_end(["last", "unknown", "move", "confine", "gaze"], "ops") == 0, "无懈可击没凑齐"
    assert combo_end(iso, "urban") == 10 * 60 and combo_end(flaw, "ops") == 5 * 60 and combo_end(iso) == 10 * 60
    assert rest_reward(6, iso, "urban") == rest_reward(6, iso) and rest_reward(6, iso, "ops") == rest_reward(6, iso)
    assert rest_reward(9, flaw, "ops") == rest_minutes(9) * 60 + 5 * 60 and rest_reward(9, flaw, "urban") == rest_minutes(9) * 60
    assert rest_minutes_total(6, iso, "urban") == rest_minutes(6) + 10
    assert theme_note(flawless, "ops") == "起源: 完成合约时 休息额外 +5 分钟"
    assert theme_note(isolation, "urban") == "" and theme_note(isolation, "ops") == "" and theme_note(flawless, "") == ""
    # 统计页: 最近 5 个顺利完成的合约 (失败的 / 提前结束的不要)
    hist = [{"end": f"2026-10-{i:02d}T10:00:00", "result": r, "level": i, "orig_level": i + 2}
            for i, r in enumerate(["completed", "failed", "completed", "abandoned",
                                   "completed", "completed", "completed"], 1)]
    got = recent_completed(hist, 5)
    assert [h["level"] for h in got] == [7, 6, 5, 3, 1], got
    assert recent_completed(hist, 2) == got[:2] and recent_completed([]) == []
    assert recent_completed([{"end": "", "result": "completed"}]) == [], "没有结束时刻的不算"
    # 统计页看词条: 降级的按降级后的名字, 失效的标出来
    rec = {"orig": ["long3", "tired3", "silent", "music"], "terms": ["long3", "tired2", "silent"]}
    assert terms_of_record(rec) == [("环境:持久战-III", False), ("环境:疲惫-II", False),
                                    ("环境:无言为真", False), ("环境:乐理之灾", True)]
    assert terms_of_record({}) == [] and terms_of_record(None) == []
    # 和合约页一样的词条行: 等级 + 要显示的词条 + 失效 + 降级前的原词条
    rr = record_rows(rec)
    assert rr[0] == (BY_ID["long3"][2], "long3", False, "") and rr[1] == (BY_ID["tired2"][2], "tired2", False, "tired3")
    assert rr[2] == (BY_ID["silent"][2], "silent", False, "") and rr[3] == (BY_ID["music"][2], "music", True, "")
    assert record_rows({}) == [] and record_rows(None) == []
    assert record_marks(rec) == {}, "没有凑齐的组合"
    # 完成时凑齐了的组合: 成员放在一起置顶; 有成员失效了的组合不算
    fun4 = ["music", "silent", "game", "video"]
    rec2 = {"orig": ["long3", "music", "tired2", "silent", "game", "video"],
            "terms": ["long3", "music", "tired2", "silent", "game", "video"]}
    assert [r[1] for r in record_rows(rec2)] == fun4 + ["long3", "tired2"]
    assert record_marks(rec2) == {t: "nofun" for t in fun4}
    rec2["terms"] = ["long3", "tired2", "silent", "game", "video"]
    assert record_marks(rec2) == {} and [r[1] for r in record_rows(rec2)][:2] == ["long3", "music"], "乐理之灾失效了: 组合不算"
    for t in ("ChatGPT", "Claude", "(3) 首页 / X", "如何评价…？ - 知乎 - Google Chrome", "微博-随时随地发现新鲜事",
              "DeepSeek - 探索未至之境 - Microsoft Edge", "Reddit - Dive into anything", "Kimi 智能助手"):
        assert site_title(t, SOCIAL_TITLES), t
    for t in ("CLAUDE.md - obliphur_terminal - Visual Studio Code", "知乎 - Google 搜索", "新标签页",
              "Claude Code 文档", "微信读书", "Example Domain", "poem - Wikipedia", "【4K】某视频_哔哩哔哩_bilibili"):
        assert not site_title(t, SOCIAL_TITLES), t
    for t in ("【4K】某视频_哔哩哔哩_bilibili - Google Chrome", "(12) Some video - YouTube", "爱奇艺-在线视频网站"):
        assert site_title(t, VIDEO_TITLES), t
    assert not site_title("youtube-dl - GitHub", VIDEO_TITLES) and not site_title("ChatGPT", VIDEO_TITLES)
    assert not set(SOCIAL_EXES) & set(VIDEO_EXES) and not SOCIAL_TITLES & VIDEO_TITLES, "一个软件只算一个词条"
    best = clean_terms(["long3", "erode2", "info3", "suppress", "jam", "music", "fog", "tired3", "chan2", "last",
                        "short2", "seal", "fine", "silent", "game", "video", "gaze", "scar"])
    assert "scar" in best and level_of(best) == 32, level_of(best)
    assert combo_gift(best) == 20 * 60, "互联网娱乐的败退凑齐了"
    best2 = clean_terms(["info3", "chan2", "erode2", "suppress", "fog", "quiet", "jam", "last", "long3", "tired3", "fine"])
    assert len(best2) == 11, best2
    assert combo_level(best2) == 1 and level_of(best2) == 23 + 1, level_of(best2)

    c = Contract(tempfile.mkdtemp())
    t0 = datetime(2026, 10, 18, 9, 30)
    c.start(["tired2", "info2", "chan1", "long1", "music"], owner="me", now=t0)
    assert c.active and c.level == 2 + 2 + 1 + 1 + 1 and c.end_at == t0 + timedelta(seconds=int(round(3 * 3600 * 1.2)))
    assert c.overrides() == {} and not c.fog() and not c.hour_forced()
    # 信息阻断-II: 开始时剩 100 分钟, 只能用 65 分钟; 用超了 → 降成 -I, 多给 15 分钟
    assert c.net_check(600, 0, 6000, "2026-10-18") == ("", "")
    assert c.net_left(600) == 3900 and c.net_check(4499, 0, 0, "2026-10-18") == ("", "")
    assert c.net_check(4600, 0, 0, "2026-10-18") == ("info2", "over")
    over, new = c.violate("info2")
    assert not over and new == "info1" and c.terms == ["long1", "chan1", "tired2", "info1", "music"], c.terms
    assert c.net_left(4600) == 600 + 3900 + 900 - 4600 and c.level == 6 and c.orig_level == 7
    assert c.net_check(4700, 0, 0, "2026-10-18") == ("", "")
    assert c.net_check(4700, 600, 0, "2026-10-18") == ("info1", "loan"), "借贷也算违反"
    assert c.net_check(4700, 600, 0, "2026-10-18") == ("", ""), "同一次借贷只算一次"
    assert c.downgrade("info1") == "" and not c.net_term() and c.net_check(9999, 9999, 0, "x") == ("", "")
    # 通道阻塞 / 乐理之灾: 只在刚越过的那一下算
    assert c.window_check(6) == "chan1" and c.window_check(7) == "" and c.window_check(4) == ""
    assert c.window_check(6) == "chan1"
    assert c.ban_check("music", True) == "music" and c.ban_check("music", True) == "" and c.ban_check("music", False) == ""
    assert c.downgrade("music") == "" and not c.has("music"), "乐理之灾只有 1 级, 降一级就失效"
    # 无言为真 (2 级) / 禁游令: 违反一次降一级, 再违反才失效; 录像 (目光 / 刻痕) 违反一次就失效
    # 时间重叠的两个合约 (15.5): 进行中的被同步盖掉 → 记下冲突; 选了留下哪个, 另一个作废 (随同步)
    ka, kb = Contract(tempfile.mkdtemp()), Contract(tempfile.mkdtemp())
    ka.start(["long1"], owner="A", now=t0)
    kb.start(["music", "game"], owner="B", now=t0 + timedelta(minutes=30))
    a_id, b_id = str(ka.live["id"]), str(kb.live["id"])
    assert a_id != b_id
    assert ka.merge({"live": kb.doc()["live"]}) and ka.live["id"] == kb.live["id"]
    assert len(ka.conflicts) == 1 and {ka.conflicts[0][0]["id"], ka.conflicts[0][1]["id"]} == {a_id, b_id}
    assert ka.resolve_conflict(a_id) and str(ka.live["id"]) == a_id and b_id in ka.voided and not ka.conflicts
    assert ka.doc()["void:" + b_id] == 1
    assert kb.merge({"void:" + b_id: 1}) and not kb.active, "作废的进行中合约在另一台设备上也结束"
    assert not kb.merge({"live": dict(ka.live, id=int(b_id))}) or str(kb.live.get("id")) != b_id, "作废的不再收"
    # 送来的记录和本机的记录时间重叠 → 冲突; 留下送来的, 本机的删掉
    kc, kd = Contract(tempfile.mkdtemp()), Contract(tempfile.mkdtemp())
    kc.start(["long1"], owner="C", now=t0)
    kc.finish("completed", t0 + timedelta(hours=2))
    kd.start(["fog"], owner="D", now=t0 + timedelta(hours=1))
    kd.finish("abandoned", t0 + timedelta(hours=3))
    kc.merge({"history": kd.doc()["history"]})
    assert len(kc.conflicts) == 1 and len(kc.history) == 2
    keep = kc.conflicts[0][1]["id"]
    assert kc.resolve_conflict(keep) and [str(h["id"]) for h in kc.history] == [keep]
    # 正常情况: 别的设备结束了这个合约又开始了新的 (送来的记录里有本机的这个) → 不算冲突
    kf, kg = Contract(tempfile.mkdtemp()), Contract(tempfile.mkdtemp())
    kf.start(["long1"], owner="F", now=t0)
    kg.merge(kf.doc())
    kg.finish("abandoned", t0 + timedelta(minutes=10))
    kg.start(["fog"], owner="G", now=t0 + timedelta(minutes=11))
    kf.merge(kg.doc())
    assert not kf.conflicts and str(kf.live["id"]) == str(kg.live["id"])
    # 不重叠的不算
    ke = Contract(tempfile.mkdtemp())
    ke.merge({"history": [dict(kd.history[0], id=999, start=_iso(t0 + timedelta(days=1)), end=_iso(t0 + timedelta(days=1, hours=1)))]})
    ke.merge({"history": [dict(kd.history[0], id=998, start=_iso(t0 + timedelta(days=2)), end=_iso(t0 + timedelta(days=2, hours=1)))]})
    assert not ke.conflicts

    # 网络禁令: 点击解除, 扣词条等级那么多目标生命值, 词条去掉 (组合算成员失效)
    cb = Contract(tempfile.mkdtemp())
    cb.start(["music", "silent", "game", "video"], owner="me", now=t0)
    assert cb.net_bans() == ["music", "silent", "game", "video"]
    assert "steam.exe" in cb.banned_exes() and "wechat.exe" in cb.banned_exes() and "cloudmusic.exe" in cb.banned_exes()
    assert cb.lift_cost("silent") == 2 and cb.lift_cost("music") == 1
    assert {"bilibili.com", "zhihu.com", "claude.ai", "music.163.com", "steampowered.com"} <= cb.banned_domains()
    assert cb.lift_ban("silent") == (False, 2) and cb.life == cb.violation_limit - 2
    assert not cb.has("silent") and "silent" not in cb.net_bans() and "wechat.exe" not in cb.banned_exes()
    assert "zhihu.com" not in cb.banned_domains() and "bilibili.com" in cb.banned_domains()
    assert dict((x["id"], s) for x, s in cb.combos())["nofun"] == "void" and not cb.echo("silent")
    assert cb.lift_ban("silent") == (False, 0), "已经解除了"
    assert cb.lift_ban("music") == (False, 1) and cb.life == 1
    assert cb.lift_ban("game") == (True, 1), "目标生命值归零"
    c.start(["silent", "game", "gaze"], owner="me", now=t0)
    assert c.level == 2 + 1 + 1 and c.records() == ["camera"] and c.ban_check("music", True) == ""
    assert c.ban_check("game", True) == "game" and c.ban_check("game", True) == "" and c.ban_check("silent", True) == "silent"
    assert c.violate("gaze") == (False, "") and c.records() == [] and c.level == 3
    assert c.violate("game") == (False, "") and not c.has("game") and c.has("silent")
    c.start(["silent"], owner="me", now=t0)
    assert c.term_level("silent") == 2 and c.level == 2
    assert c.violate("silent") == (False, "silent") and c.has("silent") and c.term_level("silent") == 1
    assert c.level == 1
    assert c.violate("silent") == (False, "") and not c.has("silent") and c.lost == ["silent"]
    # 互联网娱乐的败退 (词条组合): 四类都禁止, 每一类各自只在打开的那一下算; 开始时休息 +20 分钟
    c.start(["music", "silent", "game", "video"], owner="me", now=t0)
    assert c.level == 1 + 2 + 1 + 1 and c.ban_term("game") == "game" and c.ban_check("video", True) == "video"
    assert c.ban_check("video", True) == "" and rest_gift(c.terms) == 20 * 60
    # 凑齐的组合: 成员失效 (降级不算) 后组合的效果全部取消, 开始时给的休息时间不收回
    assert c.combos()[1] == (nofun, "done") and c.combo_marks().get("music") == COMBO_COLORS["purple"]
    assert c.violate("game") == (False, "") and c.combos()[1] == (nofun, "void")
    assert c.combo_marks() == {} and rest_gift(c.terms) == 0
    # 信息隔绝组合: 额外 1 危机等级, 完成时 +10 分钟; 成员失效后都没了
    c.start(iso, owner="me", now=t0)
    assert c.combos()[3] == (isolation, "done") and c.level == 8 + 1
    assert c.reward == rest_minutes(9) * 60 + 10 * 60 and c.combo_marks().get("fog") == COMBO_COLORS["red"]
    assert c.violate("suppress") == (False, "") and c.combos()[3] == (isolation, "void")
    assert c.level == 7 and c.reward == rest_minutes(7) * 60
    # 合约主题记在合约里 (同步 / 结算都按它): 都市之意没有加成
    assert c.theme == "" and c.start(iso, owner="me", now=t0, theme="urban")["theme"] == "urban" and c.theme == "urban"
    assert c.reward == rest_minutes(9) * 60 + 10 * 60
    assert c.violate("suppress") == (False, "") and c.reward == rest_minutes(7) * 60
    assert c.finish("completed", now=t0 + timedelta(hours=9))["theme"] == "urban" and c.theme == ""
    c.start(iso, owner="me", now=t0, theme="ops")
    assert c.reward == rest_minutes(9) * 60 + 10 * 60, "起源没有绑定信息隔绝"
    c.start(iso, owner="me", now=t0, theme="bogus")
    assert c.theme == "" and "theme" not in c.live
    # 打年糕组合: 额外 1 危机等级, 休息时间 ×1.2; 纵深推进降级不算失效
    c.start(["long1", "tired2", "fine", "deep2"], owner="me", now=t0)
    assert c.combos()[0] == (mochi, "done") and c.level == 2 + 1 + 2 + 2 + 1
    assert abs(c.rest_factor() - 0.6 * 1.2) < 1e-9
    assert c.violate("deep2") == (False, "deep1") and c.combos()[0] == (mochi, "done")
    assert abs(c.rest_factor() - 0.6 * 1.2) < 1e-9, "降级不算失效"
    assert c.violate("deep1") == (False, "") and c.combos()[0] == (mochi, "void")
    assert abs(c.rest_factor() - 0.6) < 1e-9, "成员失效了: 乘区取消"
    # 翻页: 词条两页, 第 3 页是组合效果
    assert PAGES == [(0, 10), (10, BOARD_COLS)] and BOARD_COLS == 20 and LAYOUT["unknown"] == (19, 3)
    assert COMBO_PAGE == len(PAGES) and len(COMBOS) == 4
    # 全频道阻塞干扰: 合约期间一律换成 代理-全屏蔽 (proxy block-all), 结束回到设置里的断网方式
    c.start(["jam"], owner="me", now=t0)
    assert c.overrides() == {"NET_MODE": "proxy_all"}
    assert c.overrides() == {"NET_MODE": "proxy_all"}
    # 禁止囤囤鼠!: 提前结束休息算违反 (软规则, 1 级, 违反一次失效), 休息时间 ×1.1 (和疲惫相乘); 侵蚀: 冷静期 ×2 / ×3
    c.start(["hoard", "tired1", "erode2"], owner="me", now=t0)
    assert c.hoard_check() == "hoard" and abs(c.rest_factor() - 0.7 * 1.1) < 1e-9 and c.cooldown_factor() == 3.0
    assert c.violate("hoard") == (False, "") and not c.has("hoard") and abs(c.rest_factor() - 0.7) < 1e-9
    assert c.hoard_check() == "hoard" and c.echo("hoard"), "失效后再提前结束休息: 只响警报"
    c.start(["erode1"], owner="me", now=t0)
    assert c.hoard_check() == "" and c.cooldown_factor() == 2.0 and c.focus_check(60) == "" and c.quiet_check(True) == ""
    # 纵深推进: 专注不到 40 / 80 分钟算违反; -II 降成 -I
    c.start(["deep2", "quiet"], owner="me", now=t0)
    assert c.focus_check(79 * 60) == "deep2" and c.focus_check(80 * 60) == ""
    assert c.violate("deep2") == (False, "deep1") and c.focus_check(59 * 60) == "" and c.focus_check(39 * 60) == "deep1"
    # 静默协议: 专注中联网, 只在刚联网的那一下算; 1 级, 违反一次失效
    assert c.quiet_check(True) == "quiet" and c.quiet_check(True) == "" and c.quiet_check(False) == ""
    assert c.violate("quiet") == (False, "") and not c.has("quiet") and c.level == 1
    # 未知变量: 刚开始和之后每个小时开始时各选一个 1-2 级的词条 (不选录像, 不选冲突的), 不算进危机等级
    import random as _r
    c.start(["unknown", "info2", "long1"], owner="me", now=t0, pre=600)
    pool = c.random_candidates()
    assert "gaze" not in pool and "scar" not in pool and "info1" not in pool and "long2" not in pool
    assert "move" not in pool and "confine" not in pool, "没选目光: 不选高机动 / 禁足"
    assert not {"long1", "long2", "long3"} & set(pool), "不选持久战"
    assert "unknown" not in pool and "music" in pool and "jam" in pool

    class _Rng:
        """测试用: random() 依次给出 rs 里的数 (用完了给 0 = 抽词条), choice 按 pick 选 (没有时随机)."""
        def __init__(self, rs=(), pick=None):
            self.rs, self.pick, self.r = list(rs), pick, _r.Random(1)

        def random(self):
            return self.rs.pop(0) if self.rs else 0.0

        def choice(self, pool):
            return self.pick(pool) if self.pick else self.r.choice(pool)

    def _terms(ev):
        return [v for k, v in ev if k == "term"]
    rng = _Rng()
    first = _terms(c.roll(t0, rng))
    assert len(first) == 1 and c.rand == [] and list(c.waiting) == first, "抽中后还没生效"
    assert c.waiting[first[0]] == (t0 + timedelta(minutes=3), t0 + timedelta(hours=1)) and first[0] not in c.random_candidates()
    assert c.wake(t0 + timedelta(minutes=2, seconds=59)) == [] and not c.has(first[0])
    assert c.wake(t0 + timedelta(minutes=3)) == first and c.rand == first and c.waiting == {} and c.level == 3 + 2 + 1
    assert c.roll(t0 + timedelta(minutes=59), rng) == []
    assert c.rand_until(first[0]) == t0 + timedelta(hours=1), "只在抽中的那个小时里有效"
    more = _terms(c.roll(t0 + timedelta(hours=2, minutes=1), rng))
    assert len(more) == 1 and c.rand == [] and list(c.waiting) == more, "错过的小时补上; 同时只有一个"
    assert c.wake(t0 + timedelta(hours=2, minutes=3)) == more and c.rand == more and c.level == 6
    assert c.rand_until(more[0]) == t0 + timedelta(hours=3) and c.violations == 0, "到时移除不算违反"
    assert all(1 <= BY_ID[t][2] <= 2 and t in c.terms for t in c.rand)
    # 点击关闭: 算一次违反, 直接去掉 (不是降级)
    t = c.rand[0]
    assert c.violate(t, drop=True) == (False, "") and t not in c.terms and t not in c.rand and c.violations == 1
    # 下一个小时开始时再选一个, 这个小时结束时移除
    nxt = _terms(c.roll(t0 + timedelta(hours=3), rng))
    assert c.wake(t0 + timedelta(hours=3, minutes=5)) == nxt and c.rand == nxt
    assert c.expire(t0 + timedelta(hours=4)) == nxt and c.rand == []
    # 还没生效就到了小时结束 (程序关着): 直接去掉, 不再生效
    c.live["rand_wait"] = {"music": [_iso(t0 + timedelta(hours=4, minutes=3)), _iso(t0 + timedelta(hours=5))]}
    assert c.expire(t0 + timedelta(hours=5)) == [] and c.waiting == {} and c.wake(t0 + timedelta(hours=5)) == []
    assert c.violations == 1 and c.level == 6 and c.terms == ["long1", "info2", "unknown"], c.terms
    # 选中的词条降级: 还是它选中的, 移除的时刻不变
    c.start(["unknown"], owner="me", now=t0)
    c._enable("chan2", t0 + timedelta(hours=1))
    assert c.violate("chan2") == (False, "chan1") and c.rand == ["chan1"] and c.rand_until("chan1") == t0 + timedelta(hours=1)
    assert c.expire(t0 + timedelta(hours=1)) == ["chan1"] and c.terms == ["unknown"]
    # 选中支援短缺: 补上能用多少
    c.start(["unknown"], owner="me", now=t0, pre=600)
    c._enable("short1")
    assert c.supply_limit == 300 and c.rand == ["short1"] and c.rand_until("short1") is None
    # 退路封锁: 目标生命值减少后不为 0 才选; 只在选中的那个小时里有效, 小时结束时移除 (不算违反)
    c.violate("")
    c.violate("")
    assert "retreat2" not in c.random_candidates() and "retreat1" in c.random_candidates()
    c.live["rand_n"] = 1

    _Pick = _Rng(pick=lambda pool: "retreat1" if "retreat1" in pool else pool[0])
    assert c.roll(t0 + timedelta(hours=1, minutes=2), _Pick) == [("term", "retreat1")] and c.violation_limit == 4
    assert c.wake(t0 + timedelta(hours=1, minutes=5)) == ["retreat1"]
    assert c.violation_limit == 3 and c.life == 1 and c.rand_until("retreat1") == t0 + timedelta(hours=2)
    assert c.expire(t0 + timedelta(hours=1, minutes=59)) == [] and c.has("retreat1")
    assert c.expire(t0 + timedelta(hours=2)) == ["retreat1"] and not c.has("retreat1") and c.life == 2
    assert c.violations == 2 and c.rand == ["short1"]
    # 选过的不再选
    assert "retreat1" not in c.random_candidates() and "retreat2" not in c.random_candidates()
    rec = c.finish("abandoned", t0 + timedelta(hours=1))
    assert rec["rand"][0] == "short1"
    # 通过违反失效的词条可以再回到词条池, 但只会再被选中一次 (开始时选的, 和未知变量选中的都是)
    c.start(["unknown", "music"], owner="me", now=t0)
    assert "music" not in c.random_candidates() and c.violate("music") == (False, "")
    assert "music" in c.random_candidates(), "失效了: 回到词条池"
    pick_music = _Rng(pick=lambda pool: "music" if "music" in pool else pool[0])
    assert c.roll(t0, pick_music) == [("term", "music")] and c.wake(t0 + timedelta(minutes=3)) == ["music"]
    assert c.violate("music") == (False, "") and "music" not in c.random_candidates(), "只会再选中一次"
    c._pick("game", t0)
    assert c.wake(t0 + timedelta(minutes=3)) == ["game"] and "game" not in c.random_candidates()
    assert c.violate("game", drop=True) == (False, "") and "game" in c.random_candidates(), "关闭也算违反: 回到词条池"
    c._pick("game", t0)
    assert "game" not in c.random_candidates() and c.live["rand_again"] == ["music", "game"]
    # 有能选的词条: 75% 词条, 25% 效果; 效果按 15/15/15/15/10/30
    assert [Contract._effect(_Rng([x])) for x in (0.0, 0.149, 0.15, 0.3, 0.45, 0.6, 0.7, 0.999)] == \
        ["rest_cut", "rest_cut", "lock", "rest_add", "double", "slow", "none", "none"]
    c.start(["unknown", "long1"], owner="me", now=t0)       # 持久战-I: 3.6 小时, 够判定 4 个小时 (基础时长 3 小时只有 3 次)
    assert c.roll(t0, _Rng([0.75, 0.0])) == [("rest_cut", "")] and c.waiting == {}, "0.75 及以上: 效果"
    assert c.roll(t0 + timedelta(hours=1), _Rng([0.7499])) and len(c.waiting) == 1
    # 再判定两次: 这次不生效, 改为判定两次 (这里: 一次词条, 一次 +10 分钟)
    ev = c.roll(t0 + timedelta(hours=2), _Rng([0.9, 0.45, 0.0, 0.9, 0.3]))
    assert [k for k, _v in ev] == ["double", "term", "rest_add"] and len(c.waiting) == 1, ev
    # 没有能选的词条: 一定是效果
    c.live["rand_used"] = [t[0] for t in TERMS]
    assert c.random_candidates() == [] and c.roll(t0 + timedelta(hours=3), _Rng([0.7])) == [("none", "")]
    # 阻滞: 只减慢合约内那部分专注 (合约结束后恢复正常速度); 合约结束后才开始的专注不生效
    c.live["rand_n"] = 3                       # 合约有 3.6 小时: 再判定一次第 4 个小时
    assert c.end_at == t0 + timedelta(hours=3, minutes=36)
    assert c.roll(t0 + timedelta(hours=3, minutes=10), _Rng([0.6])) == [("slow", "")] and c.slow_pending
    late = c.end_at + timedelta(minutes=10)
    assert not c.slow_bind(late) and c.slow_pending, "合约结束后才开始的专注不落上阻滞"
    s1 = c.end_at - timedelta(minutes=30)
    assert c.slow_bind(s1) and not c.slow_pending and not c.slow_bind(s1)
    assert c.focus_rate(s1, s1) == 0.9 and c.focus_rate(s1, late) == 1.0, "合约结束后不再减慢"
    # 这段专注: 合约内 30 分钟按 90% 算, 合约结束后的 30 分钟按正常速度
    assert abs(c.focus_minutes(s1, 3600) - (1800 * 0.9 + 1800) / 60) < 1e-6
    # 封锁 (MECHANICS 18.4): 只挑今天整点记录页上已经结束、已经填写了的整小时格; 没填的不挑
    fill = timedelta(hours=1)
    now = datetime(2026, 10, 18, 12, 10)
    texts = {"2026-10-18/10": "学习", "2026-10-18/8": "Sleeping"}
    pool = [k for k, _a, _b in c.lock_pool(now, texts.get, "Sleeping", fill)]
    assert pool == ["2026-10-18/8", "2026-10-18/10"], pool       # 9 / 11 点没填, 12 点还没结束
    only10 = _Rng(pick=lambda pool: next(x for x in pool if x[0] == "2026-10-18/10"))
    assert c.lock_cell(now, only10, texts.get, "Sleeping", fill) == "2026-10-18/10"
    a, b = datetime(2026, 10, 18, 10), datetime(2026, 10, 18, 11)
    assert c.hour_locked(a, b, "学习", now, fill, "Sleeping")
    assert not c.hour_locked(b, b + timedelta(hours=1), "", now, fill, "Sleeping"), "没填的不锁"
    last = _Rng(pick=lambda pool: pool[-1])
    assert c.lock_cell(now, last, texts.get, "Sleeping", fill) == "2026-10-18/8", "已经封锁的不再选"
    assert c.lock_cell(now, last, texts.get, "Sleeping", fill) == "", "没有可以封锁的"
    assert c.lock_cell(now, None, None, "Sleeping", fill) == ""
    # 记录封锁: 每次完成填写时另外封锁两个; 没选记录封锁时不封. 合约里填了的本来就锁着, 挑的是别的填了的格子
    assert c.seal_extra(now, None, texts.get, "Sleeping", fill) == [], "没选记录封锁"
    c.live["terms"] = list(c.terms) + ["seal"]
    texts.update({"2026-10-18/6": "Sleeping", "2026-10-18/7": "吃饭", "2026-10-18/11": "读书", "2026-10-18/12": "学习"})
    n0 = len(c.locks())
    now2 = datetime(2026, 10, 18, 14, 0)
    got = c.seal_extra(now2, None, texts.get, "Sleeping", fill)
    assert sorted(got) == ["2026-10-18/6", "2026-10-18/7"] and len(c.locks()) == n0 + 2, got
    assert c.seal_extra(now2, None, texts.get, "Sleeping", fill) == [], "合约里填了的本来就锁着, 没有别的可封"
    assert c.seal_filled(now2, texts.get, "Sleeping") and not c.seal_filled(now2, texts.get, "Sleeping")
    c.live["terms"] = [t for t in c.terms if t != "seal"]
    c.finish("abandoned", now)
    # 合约结束后: 封锁了的 (都是填写了的) 照样锁着, 没填的照常能填, 填写不用确认; 之后才开始的这段专注不减速
    later = now + timedelta(days=3)
    assert c.hour_locked(a, b, "学习", later, fill, "Sleeping"), "合约结束后照样锁着"
    assert c.hour_locked(datetime(2026, 10, 18, 11), datetime(2026, 10, 18, 12), "读书", later, fill, "Sleeping")
    assert not c.hour_locked(datetime(2026, 10, 18, 9), datetime(2026, 10, 18, 10), "", later, fill, "Sleeping")
    assert not c.hour_sealed(datetime(2026, 10, 18, 9), datetime(2026, 10, 18, 10), later)
    assert c.focus_rate(s1) == 1.0 and abs(c.focus_minutes(s1, 3600) - 60) < 1e-6
    # 未知变量启用了整点记录的词条: 这次合约中一直强制开启整点记录
    c.start(["unknown"], owner="me", now=t0)
    assert not c.hour_forced()
    c._enable("seal", t0 + timedelta(hours=1))
    assert c.hour_forced() and c.expire(t0 + timedelta(hours=1)) == ["seal"] and c.hour_forced()
    c.finish("abandoned", t0 + timedelta(hours=2))
    assert not c.hour_forced()
    # 积攒的休息时间减少: 合约开始前的那部分最多剩这么多
    c.start(["unknown"], owner="me", now=t0, pre=600)
    c.pre_cap(900)
    assert c.pre_left == 600
    c.pre_cap(250)
    assert c.pre_left == 250
    c = Contract(tempfile.mkdtemp())          # 上面封锁的记录格会影响后面整点记录的测试
    # 打年糕: 休息时间额外 ×1.2 (和疲惫相乘); 成员降级不算失效, 失效后乘区取消
    c.start(["long1", "tired2", "fine", "deep2"], owner="me", now=t0)
    assert abs(c.rest_factor() - 0.6 * 1.2) < 1e-9 and abs(c.book_rest(1000, t0) - 720) < 1e-6
    assert c.level == 8 and c.combo_marks().get("deep2") == COMBO_COLORS["yellow"]
    c.downgrade("deep2")
    assert abs(c.rest_factor() - 0.6 * 1.2) < 1e-9, "降级不算失效"
    c.downgrade("deep1")
    assert abs(c.rest_factor() - 0.6) < 1e-9 and c.level == 5 and c.combo_marks() == {}
    # 没有疲惫: 基础 ×0.8 (打年糕照样相乘)
    assert rest_factor_of([]) == 0.8
    assert abs(rest_factor_of(["long1", "tired2", "fine", "deep1"]) - 0.6 * 1.2) < 1e-9
    c.start(["scar"], owner="me", now=t0)
    assert c.records() == ["screen"] and c.level == 1 and c.violate("scar") == (False, "") and c.terms == []
    # 目光: 只由开始时选定的设备 (cam) 录; 没有 cam 的 (旧版本) 各台电脑都录. 前置失效了高机动 / 禁足照样在
    c.start(["gaze", "scar", "move"], owner="me", now=t0, cam="phone")
    assert c.cam == "phone" and c.records("pc") == ["screen"] and c.records("phone") == ["camera", "screen"]
    assert c.violate("gaze") == (False, "") and c.terms == ["scar", "move"]
    c.start(["move", "scar"], owner="me", now=t0, cam="pc")
    assert c.terms == ["scar"] and c.cam == "" and c.records("x") == ["screen"], "没选目光: 不记 cam, 高机动去掉"
    c.start(["gaze"], owner="me", now=t0)
    assert c.cam == "" and c.records("pc") == ["camera"]
    c.start(["tired2", "chan1", "long1"], owner="me", now=t0)
    assert c.quick_check() == ""
    # 休息: 疲惫 ×0.6; 完成时按降级后的等级给固定的时间 (等级 4 → 23 分钟)
    assert abs(c.book_rest(1000, t0 + timedelta(hours=1)) - 600) < 1e-6
    assert c.level == 4 and c.reward == round(rest_minutes(4) * 60 * 1.1), "持久战 long1: 结算休息时长 ×110%"
    # 空闲累计 15 分钟结束 (不要求连续)
    H = timedelta(hours=1)
    M = lambda n: timedelta(minutes=n)
    assert c.due(t0 + H, True) == ("", None)
    assert c.idle_left(t0 + H + M(10)) == 5 * 60
    # 空闲和设备离线共用 15 分钟: 空闲 10 分钟后有设备离线, 再过 5 分钟就失败 (重叠的部分只算一次)
    assert c.offline_due([("手机", t0 + H + M(10))], t0 + H + M(14), t0) is None
    assert c.offline_due([("手机", t0 + H + M(10))], t0 + H + M(15), t0) == ("手机", t0 + H + M(15))
    # 空闲 10 分钟后开始专注: 不算空闲 (不连续); 之后再空闲, 两段加起来满 15 分钟才结束
    assert c.due(t0 + H + M(10), False)[0] == "", "开始专注就不算空闲"
    assert c.idle_left(t0 + H + M(30)) is None
    assert c.due(t0 + H + M(30), True) == ("", None)
    assert c.idle_left(t0 + H + M(32)) == 3 * 60, "之前空闲过的 10 分钟也要扣"
    assert c.due(t0 + H + M(34), True) == ("", None)
    assert c.due(t0 + H + M(35), True) == ("idle", t0 + H + M(35)), "10 + 5 分钟"
    assert c.due(t0 + H + M(36), False)[0] == "idle", "累计到了就是到了"
    # 设备离线累计 15 分钟失败 (不要求连续): 从合约开始 / 本机打开程序之后才算
    c.live["idle_spans"], c.live["idle_since"] = [], None
    assert c.offline_due([("手机", t0 - timedelta(hours=3))], t0 + M(14), t0) is None
    assert c.offline_due([("手机", t0 - timedelta(hours=3))], t0 + M(16), t0) == ("手机", t0 + M(15))
    assert c.offline_due([("手机", t0)], t0 + H + M(10), t0 + H) is None, "本机刚打开"
    sp = [(t0 + M(1), t0 + M(9)), (t0 + M(20), t0 + M(30))]            # 两段离线: 8 + 10 分钟
    assert c.offline_due([("手机", t0 + M(20))], t0 + M(26), t0, sp) is None, "8 + 6 = 14 分钟"
    assert c.offline_due([("手机", t0 + M(20))], t0 + M(27), t0, sp) == ("手机", t0 + M(27)), "8 + 7 = 15 分钟"
    assert c.offline_due([("手机", t0 + M(20))], t0 + M(40), t0 + M(10), sp) is None, "本机 t0+10 分钟才打开: 第一段不算, 只有 10 分钟"
    # 目标生命值: 默认 4; 退路封锁-I 只有 3, 违反 3 次结束; 整点记录超时未填写也算 (不降词条)
    c.start(["long1"], owner="me", now=t0)
    assert c.violation_limit == 4 and c.life == 4
    c.start(["suppress", "info3", "long1", "retreat1"], owner="me", now=t0)
    assert c.violation_limit == 3 and c.life == 3 and c.quick_check() == "suppress"
    assert c.violate("suppress") == (False, "") and not c.has("suppress") and c.life == 2
    assert c.violate("") == (False, "") and c.violations == 2 and c.level == 3 + 1 + 1 and c.life == 1
    assert c.violate("info3") == (True, "info2") and c.life == 0
    full = c.reward
    assert c.reward_at(t0) == 0 and c.reward_at(t0 + timedelta(hours=5)) == round(full), "k = 0: 0%, k = 1: 100%"
    rec = c.finish("failed", t0 + timedelta(hours=2))
    assert rec["reward"] == round(full * 0.6 * ((7200 / duration_of(["long1"]) - 0.3) / 0.7) ** 2), "没有完成: 按进行的比例 f(k) 给"
    assert rest_share(0.0) == 0.0 and rest_share(0.3) == 0.0 and rest_share(1.0) == 1.0
    assert abs(rest_share(0.65) - 0.15) < 1e-9 and 0.599 < rest_share(0.9999) < 0.6, "k → 1⁻ 趋近 0.6"
    assert rest_share(0.5) - rest_share(0.4) < rest_share(0.9) - rest_share(0.8), "f'' > 0"
    # "还在吗": 普通专注每小时; 合约专注每次随机 40 ~ 60 分钟, 由开始时刻决定 (手机端 ContractTest 用同一组数)
    cs = datetime(2026, 10, 25, 9, 30, 15)
    assert check_mark(2, cs, False, 3600) == 7200
    assert [check_mark(n, cs, True, 3600) / 60 for n in range(1, 5)] == [51, 108, 149, 196]
    gaps = {int(check_mark(1, cs + timedelta(seconds=i), True, 3600) // 60) for i in range(3000)}
    assert min(gaps) == 40 and max(gaps) == 60
    assert rec["orig_level"] == 6 and rec["level"] == 4 and not c.active
    assert not c.take_first_break()

    # 违反到失效的词条照样检查: 再次触发返回它 (echo, 只响警报), 不扣目标生命值
    c.start(["chan1", "deep1", "quiet", "info1", "short1"], owner="me", now=t0, pre=600)
    assert c.window_check(6) == "chan1" and c.violate("chan1") == (False, "")
    assert c.window_watch() == ("chan1", 5) and c.window_check(6) == "" and c.window_check(4) == ""
    assert c.window_check(6) == "chan1" and c.echo("chan1"), "失效后按失效前的上限, 越过的那一下再算"
    c.window_reset()                          # 离开专注时段: 下次专注重新算
    assert c.window_check(6) == "chan1"
    assert c.violate("deep1") == (False, "") and c.focus_check(39 * 60) == "deep1" and c.focus_check(40 * 60) == ""
    assert c.quiet_check(True) == "quiet" and c.violate("quiet") == (False, "") and c.quiet_check(True) == ""
    assert c.quiet_check(False) == "" and c.quiet_check(True) == "quiet" and c.echo("quiet")
    assert c.violations == 3 and c.life == 1
    c.live["violations"] = 0                  # 目标生命值只有 4: 接着测剩下的
    assert c.net_check(0, 0, 4000, "d") == ("", "") and c.net_check(3300, 0, 0, "d") == ("info1", "over")
    assert c.violate("info1") == (False, "") and c.net_term() == "" and c.net_check(3300, 0, 0, "d") == ("", ""), "没有继续用"
    assert c.net_check(3400, 0, 0, "d") == ("info1", "over") and c.net_check(3400, 0, 0, "d") == ("", "")
    assert c.net_check(3400, 300, 0, "d") == ("info1", "loan") and c.echo("info1")
    assert c.supply_check(0, 400) == "short1" and c.violate("short1") == (False, "") and c.supply_limit is None
    assert c.supply_watch() == "short1" and c.supply_check(0, 400) == "" and c.supply_check(0, 400.3) == ""
    assert c.supply_check(0, 410) == "short1" and c.echo("short1")
    assert c.violations == 2 and c.lost == ["chan1", "deep1", "quiet", "info1", "short1"] and c.level == 0
    # 降级 (还没失效) / 关闭未知变量选中的: 不算失效
    c.start(["chan2", "unknown"], owner="me", now=t0)
    c._enable("music", t0 + timedelta(hours=1))
    assert c.violate("chan2") == (False, "chan1") and c.violate("music", drop=True) == (False, "") and c.lost == []
    assert c.ban_term("music") == "" and not c.echo("chan2")
    # 未知变量可以再选中已经失效的词条: 选中期间违反照常扣目标生命值, 移除后又只响警报
    c.start(["unknown", "music", "chan1"], owner="me", now=t0)
    assert c.violate("music") == (False, "") and c.violate("chan1") == (False, "") and c.lost == ["music", "chan1"]
    pool = c.random_candidates()
    assert "music" in pool and "chan1" in pool and "chan2" in pool
    c._enable("music", t0 + timedelta(hours=1))
    assert not c.echo("music") and c.ban_check("music", True) == "music"
    assert c.violate("music") == (False, "") and c.violations == 3 and c.lost == ["music", "chan1"] and c.echo("music")
    c._enable("chan2", t0 + timedelta(hours=1))
    assert c.window_watch() == ("chan2", 4) and not c.echo("chan2")
    assert c.expire(t0 + timedelta(hours=1)) == ["chan2"] and c.window_watch() == ("chan1", 5) and c.echo("chan1")

    # 最后防线: 只有初始目标生命值 1, 没有额外的词条等级
    c.start(["last", "long3", "tired3", "chan1"], owner="me", now=t0)
    assert c.level == 10 and c.orig_level == 10 and c.violation_limit == 1
    assert c.violate("chan1") == (True, "") and c.level == 9   # 目标生命值只有 1

    # 完成: 按等级给固定的休息时间 (等级 4 → 23 分钟), 第一次休息的结转比例
    c.start(["last", "tired1"], owner="me", now=t0)
    assert c.violation_limit == 1 and abs(c.book_rest(600, t0) - 420) < 1e-6 and c.reward == rest_minutes(4) * 60
    assert c.due(t0 + timedelta(hours=3), False) == ("completed", t0 + timedelta(hours=3))
    rec = c.finish("completed", t0 + timedelta(hours=3, minutes=3))
    assert rec["reward"] == rest_minutes(4) * 60 and rec["end"] == (t0 + timedelta(hours=3)).isoformat()
    assert abs(c.book_rest_after(600, t0 + timedelta(hours=2)) - 420) < 1e-6 and c.reward == 0, "完成后才结束的专注只乘疲惫"
    assert c.book_rest_after(600, t0 + timedelta(hours=4)) == 600
    assert c.take_first_break() and not c.take_first_break()
    assert abs(Contract.first_break_rate(0.7) - 0.85) < 1e-9

    # 最近结束的那个, 不是最近开始的那个
    c.start(["long1"], now=t0 - timedelta(days=1))
    c.finish("abandoned", t0 - timedelta(days=1) + timedelta(hours=1))
    assert c.last["result"] == "completed" and abs(c.book_rest_after(600, t0 + timedelta(hours=2)) - 420) < 1e-6

    # 合约开始前积攒的休息时间 (20 分钟) + 支援短缺-II: 一点都不能用
    c.start(["short2"], owner="me", now=t0, pre=1200)
    assert c.pre == 1200 and c.pre_left == 1200 and c.supply_limit == 0
    taken = c.pre_take(1500)                 # 积攒的 25 分钟里有 20 分钟是合约开始前的
    assert taken == 1200 and c.pre_left == 0
    # 这次休息 = 建议 10 分钟 + 积攒 25 分钟; 剩余 > 20 分钟时还没用到合约开始前的
    assert c.supply_until(1800, taken) == 600 and c.supply_check(1300, taken) == ""
    assert c.supply_check(1100, taken) == "short2"
    c.pre_settle(taken, 1100)                # 暂停: 合约开始前的 1100 秒原样还回去
    assert c.pre_left == 1100 and abs(c.pre_used - 100) < 1e-6
    assert c.violate("short2") == (False, "short1") and c.supply_limit == 600, "降级: 多给 50%"
    assert c.supply_check(0, 0) == "" and c.supply_until(1100, 1100) == 1100 - (1100 - 500)
    rec = c.finish("abandoned", t0 + timedelta(hours=1))
    assert rec["pre_left"] == 1100 and "stash" not in rec

    # 联合行动 (1.0.404): 合约开始前积攒的休息时间收起来 (pre = 0: 不能用, 不触发支援短缺), 合约结束时原样还回去
    c.start(["short2"], owner="me", now=t0, pre=0, stash=1200)
    assert c.pre == 0 and c.pre_left == 0 and c.stash == 1200 and c.supply_limit == 0
    assert c.pre_take(1500) == 0 and c.supply_check(0, 0) == "" and c.supply_until(600, 0) is None, "收起来的时间不能用, 也不会触发支援短缺"
    assert c.unstash() == 1200 and c.stash == 0 and c.unstash() == 0
    c.start(["short2"], owner="me", now=t0, pre=0, stash=900)
    rec = c.finish("completed", t0 + timedelta(hours=4))
    assert rec["stash"] == 900 and rec["pre_left"] == 0 and c.stash == 0

    # 整点记录: 超时未填写算违反 (每格一次); 细化规范分成半小时; 记录封锁只在合约中有效
    fill = timedelta(hours=1)
    texts = {"2026-10-18/9/2": "学习"}
    c.start(["seal", "fine"], owner="me", now=t0)
    assert c.hour_forced() and c.fine_hour(datetime(2026, 10, 18, 9)) and not c.fine_hour(datetime(2026, 10, 18, 8))
    units = c.hour_units(datetime(2026, 10, 18, 10, 40))
    assert [u[0] for u in units] == ["2026-10-18/9/2", "2026-10-18/10/1"], units
    # 细化规范的半小时格补填时限只有 30 min (10:00-10:30 那格 11:00 到期)
    assert c.hour_misses(datetime(2026, 10, 18, 10, 59), fill, texts.get, "Sleeping") == []
    assert c.hour_misses(datetime(2026, 10, 18, 11, 1), fill, texts.get, "Sleeping") == ["2026-10-18/10/1"]
    assert c.hour_misses(datetime(2026, 10, 18, 11, 10), fill, texts.get, "Sleeping") == [], "每格只算一次"
    a, b = datetime(2026, 10, 18, 10), datetime(2026, 10, 18, 10, 30)
    assert not c.hour_locked(a, b, "", datetime(2026, 10, 18, 10, 59), fill, "Sleeping")
    assert c.hour_locked(a, b, "Sleeping", datetime(2026, 10, 18, 11, 1), fill, "Sleeping")
    assert c.hour_locked(a, b, "学习", datetime(2026, 10, 18, 11, 1), fill, "Sleeping"), "过了时限, 填了的也锁"
    assert c.hour_locked(a, b, "学习", datetime(2026, 10, 18, 10, 59), fill, "Sleeping"), "填写了就不能再改 (时限之内也一样)"
    assert not c.hour_locked(a, b, "", datetime(2026, 10, 18, 11, 30), fill, "Sleeping"), "没填的照常能填"
    # 细化规范: 两个半小时都填了的整小时格才封锁 (不锁半小时格)
    t2 = {"2026-10-18/10": "学习", "2026-10-18/10/1": "学习", "2026-10-18/10/2": ""}
    now4 = datetime(2026, 10, 18, 11, 10)
    assert not c.seal_filled(now4, t2.get, "Sleeping"), "后半小时没填: 不封"
    assert not c.lock_pool(now4, t2.get, "Sleeping", fill), "后半小时没填: 不能封锁"
    t2.update({"2026-10-18/10/2": "吃饭", "2026-10-18/10": "学习/吃饭"})
    assert c.seal_filled(now4, t2.get, "Sleeping") and not c.seal_filled(now4, t2.get, "Sleeping")
    assert c.live["seals"] == [["2026-10-18T10:00:00", "2026-10-18T11:00:00"]]
    c.finish("abandoned", datetime(2026, 10, 18, 12))
    assert c.hour_locked(a, b, "学习", datetime(2026, 10, 19), fill, "Sleeping"), "合约结束后照样锁着"
    assert not c.hour_sealed(a, b, datetime(2026, 10, 19)), "合约结束后填写不用确认"
    assert not c.hour_locked(datetime(2026, 10, 18, 9), datetime(2026, 10, 18, 9, 30), "", datetime(2026, 10, 19), fill,
                             "Sleeping"), "没填的照常能填"
    old = dict(c.history[-1], seals=None, locks=[["2026-10-18T09:00:00", "2026-10-18T09:30:00"]])
    c.history[-1] = old
    assert not c.hour_locked(datetime(2026, 10, 18, 9), datetime(2026, 10, 18, 9, 30), "", datetime(2026, 10, 19), fill,
                             "Sleeping"), "旧版本的 locks 不再算"
    nos = Contract(tempfile.mkdtemp())
    nos.start(["long1"], owner="me", now=t0)
    assert not nos.hour_sealed(a, b, datetime(2026, 10, 18, 11)), "没选记录封锁: 不用确认"
    assert not nos.hour_locked(a, b, "学习", datetime(2026, 10, 18, 11), fill, "Sleeping"), "没选记录封锁: 不锁"
    assert nos.seal_extra(datetime(2026, 10, 18, 11), None, texts.get, "Sleeping", fill) == [], "没选记录封锁: 不另外封锁"
    assert not c.hour_locked(datetime(2026, 10, 18, 13), datetime(2026, 10, 18, 14), "", datetime(2026, 10, 19),
                             fill, "Sleeping")

    # 黑雾: 整点记录的假内容开始合约时就抽好, 跟着合约同步, 两端一样
    fa, fb = Contract(tempfile.mkdtemp()), Contract(tempfile.mkdtemp())
    fa.start(["fog"], "A")
    w = fa.fog_words()
    assert len(w) == FOG_SLOTS and w == fa.live["fog_words"]
    assert all(len({x[k] for x in w}) == FOG_SLOTS for k in range(3)), "一次抽的词不重复"
    fb.merge({"live": fa.doc()["live"]})
    assert fb.fog_words() == w, "同步来的合约: 同一份"
    fa.live.pop("fog_words")
    assert len(fa.fog_words()) == FOG_SLOTS and fa.fog_words() == fa.fog_words(), "以前开始的合约: 按 id 补一份"
    # 同一个合约: 别的设备后改的进行中状态不会把本机刚记下的违反 / 算过的记录格盖掉
    old_b = dict(fb.live)
    fb.violate("")
    fb.live["hl"] = ["2026-10-18/9"]
    fb.merge({"live": dict(old_b, rec={"x": 1})})
    assert fb.violations == 1 and fb.live["hl"] == ["2026-10-18/9"] and fb.live["rec"] == {"x": 1}
    fb.merge({"live": dict(old_b, violations=3)})
    assert fb.violations == 3, "别的设备违反得更多: 照收"

    # 同步: 记录按 id 合并
    d = Contract(tempfile.mkdtemp())
    d.merge(c.doc())
    assert len(d.history) == len(c.history) and not d.active
    d.merge({"history": c.doc()["history"]})
    assert len(d.history) == len(c.history) >= 3, "同一条记录不重复"
    c2 = Contract(os.path.dirname(c.path))
    assert len(c2.history) == len(c.history), "写进文件, 重开后仍在"
    # 专注的标签 (按开始时刻): 开始于合约里是那个合约 ("合约"专注), 否则 "" ("普通"专注)
    g = Contract(tempfile.mkdtemp())
    g0 = datetime(2026, 10, 20, 9, 0)
    g.start(["long1"], "me", now=g0)
    gid = str(g.live["id"])
    assert g.focus_tag(g0 + timedelta(hours=1)) == gid and g.focus_tag(g0 - timedelta(minutes=1)) == ""
    assert not g.completed(gid)
    g.finish("completed", g0 + timedelta(hours=3, minutes=36))
    assert g.focus_tag(g0 + timedelta(hours=3)) == gid and g.completed(gid), "合约结束后照样认得出来"
    assert g.focus_tag(g0 + timedelta(hours=4)) == "" and not g.completed("")

    # 细化规范生效过的小时一直分两格: 未知变量的细化规范到时移除后, 后半小时照样算
    f = Contract(tempfile.mkdtemp())
    f0 = datetime(2026, 10, 20, 9, 10)
    f.start(["unknown"], "me", now=f0)
    f.live["terms"] = ["unknown", "fine"]
    assert f.mark_fine(datetime(2026, 10, 20, 10, 5)) and not f.mark_fine(datetime(2026, 10, 20, 10, 40))
    f.live["terms"] = ["unknown"]                       # 10:10 到时移除
    assert f.fine_hour(datetime(2026, 10, 20, 10)) and not f.fine_hour(datetime(2026, 10, 20, 11))
    keys = [k for k, _a, _b in f.hour_units(datetime(2026, 10, 20, 11, 5))]
    assert keys == ["2026-10-20/9", "2026-10-20/10/1", "2026-10-20/10/2"], keys
    f.finish("abandoned", datetime(2026, 10, 20, 10, 50))
    assert not f.fine_hour(datetime(2026, 10, 20, 10)), "合约结束后恢复成一小时一格, 不留多出来的格子"

    # 黑雾 (软): 一个钟点里切到计时器以外的页面满 FOG_SWITCHES 次算一次违反; 换钟点清零
    f.start(["fog"], "me", now=f0)
    t = datetime(2026, 10, 20, 9, 20)
    assert all(f.fog_switch(t) == "" for _ in range(FOG_SWITCHES - 1)) and f.fog_switch(t) == "fog"
    assert f.fog_switch(t) == ""
    assert all(f.fog_switch(t) == "" for _ in range(FOG_SWITCHES - 3)) and f.fog_switch(t + timedelta(hours=1)) == ""
    assert f.violate("fog") == (False, "") and not f.fog() and f.echo("fog"), "黑雾只有 1 级: 违反就失效, 之后只响警报"
    # 计数随同步: 两台设备各自记的次数加起来算 (同一个钟点每台取大的)
    f.start(["fog", "jam"], "me", now=f0)
    for _ in range(30):
        f.fog_switch(t, "pc")
    other = dict(f.live, tally={"fog": {"h": f.live["tally"]["fog"]["h"], "n": {"pc": 10, "phone": 19}, "used": 0}})
    f.merge({"live": other})
    assert f.tally_count("fog", t) == 49 and f.fog_switch(t, "pc") == "fog" and f.tally_count("fog", t) == 0
    # 全频道阻塞干扰 (软): 被禁止的程序满 JAM_BLOCKS 次算一次违反; 2 级, 降一级还在
    assert all(f.jam_block(t, "pc") == "" for _ in range(JAM_BLOCKS - 1)) and f.jam_block(t, "pc") == "jam"
    assert f.violate("jam") == (False, "jam") and f.has("jam") and f.overrides()
    # 休息中没填写的记录格: 超时不算违反, 黑雾不盖住, 记录封锁也不锁, 自己填写之后照常
    f.start(["fog", "seal"], "me", now=f0)
    texts = {}
    r0 = f0.replace(minute=0) + timedelta(hours=1)
    key = f"{r0:%Y-%m-%d}/{r0.hour}"
    assert f.mark_rest_cells(r0 + timedelta(hours=1, minutes=5), timedelta(hours=1), texts.get, "Sleeping")
    assert key in f.live["rest_h"] and f.rest_cell(r0, r0 + timedelta(hours=1), "Sleeping", "Sleeping")
    assert key not in f.hour_misses(r0 + timedelta(hours=2, minutes=1), timedelta(hours=1), texts.get, "Sleeping")
    assert not f.hour_locked(r0, r0 + timedelta(hours=1), "Sleeping", r0 + timedelta(hours=3), timedelta(hours=1), "Sleeping")
    assert f.hour_locked(r0, r0 + timedelta(hours=1), "读书", r0 + timedelta(hours=3), timedelta(hours=1), "Sleeping")
    assert not f.rest_cell(r0, r0 + timedelta(hours=1), "读书", "Sleeping"), "自己填写之后照常盖住"

    # 记录封锁: 跨天的合约只封锁今天的格子
    f.finish("abandoned", datetime(2026, 10, 20, 9, 30))
    f.start(["seal"], "me", now=datetime(2026, 10, 20, 22, 10))
    picked = f.seal_extra(datetime(2026, 10, 20, 23, 50), rng=random.Random(1), text_of=lambda k: "x",
                          auto="", fill=timedelta(hours=1))
    rows = {f"2026-10-19/23"} | {f"2026-10-20/{h}" for h in range(22)}       # 今天页上的行, 22 点 (合约里) 本来就锁着
    assert len(picked) == 2 and set(picked) <= rows, picked
    return "contract selftest OK"


if __name__ == "__main__":
    print(_selftest())
