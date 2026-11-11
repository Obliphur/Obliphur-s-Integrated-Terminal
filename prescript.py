"""指令 (Prescript): 都市之意主题独占的模块.

规则见 MECHANICS.md 第 22 节. 这里只放判定 / 计时 / 存档, 界面在
pages/prescript_page.py. 数据存在数据目录的 prescript.json, 不同步.

时间都按墙上时间算; 关闭程序期间照常流逝 (下次打开时按过期 / 完成判定).
"""

import os
import random
from datetime import datetime, timedelta

import config


from prescript_parts.prescript_common import (
    ALL_TYPES, BREAK_TARGET, CODE_LEFT, CODE_RIGHT, CODE_WIDTH, DAILY_BONUS_SECONDS, DAILY_T_TARGET,
    DAWN_HOUR, DAWN_SECONDS, DEV_KEEP, EXPIRY_SECONDS, FIXED_T, FOCUS_CHANCE_START, FOCUS_CHANCE_STEP,
    FOCUS_TARGET, GRANTED_KEEP, HISTORY_KEEP, IDLE_BOOST, IDLE_ENTRIES, IDLE_KINDS, KEEP, LOG_KEEP_DAYS,
    MARK_OF, PAGE_EVENT_FRESH, POOLS, REFLECT_TARGET, REST_MAX_MIN, REST_SHARE, SAVE_EVERY, SYNC_DAYS,
    SYNC_VOLATILE, TYPES, TYPE_WORDS, _iso, _midnight_after, _parse, code_for, contract_duration_hours,
    fmt_clock, fmt_t, pad_code, rest_code, rest_minutes, type_code,
)
from prescript_parts.prescript_store import StoreMixin
from prescript_parts.prescript_draw import DrawMixin
from prescript_parts.prescript_flow import FlowMixin
from prescript_parts.prescript_daily import DailyMixin
from prescript_parts.prescript_sync import SyncMixin
from prescript_parts.prescript_log import LogMixin
from prescript_parts.prescript_settle import SettleMixin


class PrescriptEngine(StoreMixin, DrawMixin, FlowMixin, DailyMixin, SyncMixin, LogMixin, SettleMixin):
    def __init__(self, data_dir, *, state_fn=None, today_hours_fn=None, contracts_fn=None,
                 page_fn=None, minimized_fn=None, grant_rest_fn=None, username_fn=None,
                 blocked_fn=None, on_change=None, dev_fn=None, pause_media_fn=None, remaining_rest_fn=None, contract_active_fn=None, on_complete=None, hour_log_fn=None, focus_log_fn=None, peers_on_page_fn=None, page_event_fn=None, rng=None):
        self.path = os.path.join(data_dir, "prescript.json")
        self.state_fn = state_fn or (lambda: "idle")
        self.today_hours_fn = today_hours_fn or (lambda: 0.0)
        self.contracts_fn = contracts_fn or (lambda: [])
        self.page_fn = page_fn or (lambda: "")
        self.minimized_fn = minimized_fn or (lambda: False)
        self.grant_rest_fn = grant_rest_fn
        self.username_fn = username_fn or (lambda: "")
        self.blocked_fn = blocked_fn or (lambda: False)
        self.dev_fn = dev_fn or (lambda: False)
        self.pause_media_fn = pause_media_fn
        self.remaining_rest_fn = remaining_rest_fn or (lambda: 0.0)
        self.contract_active_fn = contract_active_fn or (lambda: False)
        self.hour_log_fn = hour_log_fn or (lambda: "")
        self.focus_log_fn = focus_log_fn or (lambda: "")
        self.peers_on_page_fn = peers_on_page_fn or (lambda: False)
        # 其他设备进入 / 退出了指令页 (事件): page_event_fn(on) 让本机跟着进入 / 退出 (MECHANICS 22.8)
        self.page_event_fn = page_event_fn
        self._page_rec = None           # 最近一次进入 / 退出指令页的事件 {"on", "at", "by"}, 随同步; 不存档
        self.on_change = on_change
        self.on_complete = on_complete
        self.rng = rng or random.Random()
        self._username = ""

        self._data = self._load()
        now = datetime.now().replace(microsecond=0)
        self._last_tick = now
        self._last_save = now
        self._dirty = False
        self._ensure_day(now)


def _selftest():
    import json
    import shutil
    import tempfile

    def devsync_wins(new, old):
        """devsync 的合并规则: 结束的盖过进行中的 (这里只看这一条规则)."""
        import devsync
        svc = devsync.SyncService.__new__(devsync.SyncService)
        return svc._wins("prescript", "i:x", 2, new, (1, old))

    root = tempfile.mkdtemp(prefix="oit_prescript_")
    state = {"mode": "focus", "page": "prescript", "min": False, "contract": False}
    contracts = []
    granted = []
    today = {"hours": 0.0}
    try:
        eng = PrescriptEngine(
            root,
            state_fn=lambda: state["mode"],
            today_hours_fn=lambda: today["hours"],
            contracts_fn=lambda: contracts,
            page_fn=lambda: state["page"],
            minimized_fn=lambda: state["min"],
            grant_rest_fn=lambda s, code: granted.append((s, code)),
            username_fn=lambda: "测试",
            contract_active_fn=lambda: state["contract"],
            rng=random.Random(7),
        )

        def clear():
            eng._data["current"] = None
            eng._data["extra"] = []

        # 概率: 每个池子加起来是 1; IV 已经达成时它的概率平分给池子里其他指令
        for pool, weights in POOLS.items():
            assert abs(sum(weights) - 1.0) < 1e-9, pool
            assert len(weights) == len(TYPES)
        today["hours"] = 9.0
        weights = eng._weights("focus", datetime.now())
        assert abs(sum(weights) - 1.0) < 1e-9
        assert weights[TYPES.index("IV")] == 0.0
        assert abs(weights[TYPES.index("I")] - (0.8 + 0.1 / 4)) < 1e-9
        today["hours"] = 0.0

        # 代码格式
        assert code_for(1) == "REST3" and code_for(0.1) == "REST0.3"
        assert pad_code(type_code("I")) == "_FOCUS%." and len(pad_code(type_code("II"))) == CODE_WIDTH
        assert pad_code("NONE") == "_%NONE%." and pad_code("REST3") == "_REST3%."
        assert pad_code("REST20") == "_REST20." and pad_code("REST0.3") == "REST0.3."
        assert pad_code("1h*3min") == "_REST3%." and pad_code("20min") == "_REST20."
        assert all(len(pad_code(c)) == CODE_WIDTH for c in ("A", "REST120", "REST1200", "REST12345678"))
        assert all(len(type_code(k)) <= CODE_WIDTH for k in ALL_TYPES)
        assert fmt_clock(65) == "01:05" and fmt_clock(3665) == "1:01:05"

        # 空闲词条的 t = 分钟数 × 0.02, X 固定 0.1
        assert [FIXED_T[k] for k in ("V", "VII", "VIII", "IX", "X", "XI")] == [0.1, 0.4, 0.2, 0.1, 0.1, 0.3]

        # 没有领取冷却: 同一个小时里连着领; 手里满 KEEP 条就领不了, 完成 / 作废一条又能领
        t0 = datetime.now().replace(hour=12, minute=5, second=0, microsecond=0)
        assert eng.can_draw(t0) and eng.next_draw_at(t0) is None
        got = [eng.draw(t0, choose="III"), eng.draw(t0, choose="VI"), eng.draw(t0, choose="II")]
        assert all(got) and len(eng.actives()) == KEEP and not eng.can_draw(t0)
        assert eng.draw(t0, choose="IV") is None and len(eng.actives()) == KEEP
        assert eng.marks() == {"break", "stats", "contract"}
        eng._finish("void", t0, cur=eng.actives()[0])
        assert eng.can_draw(t0) and not any(h["result"] == "superseded" for h in eng._data["history"])
        clear()

        # 专注指令 (I) 没完成 / 作废之前不能再领新的; 其他指令不拦
        assert eng.draw(t0, choose="III") is not None and eng.can_draw(t0)
        assert eng.draw(t0, choose="I") is not None and len(eng.actives()) == 2 and not eng.can_draw(t0)
        assert eng.draw(t0, choose="VI") is None
        eng._finish("void", t0, cur=eng.active_of("I"))
        assert eng.can_draw(t0)
        clear()

        # I: 专注 50 分钟完成; 当时不在指令页, 回到页面时结算 t*3min
        assert eng.draw(t0, choose="I")
        state["mode"], state["page"] = "focus", "timer"
        eng._last_tick = t0
        eng.tick(t0 + timedelta(minutes=49))
        assert eng.current is not None
        eng.tick(t0 + timedelta(minutes=50))
        assert eng.current is None
        assert eng._data["pending"] and eng._data["pending"][0]["code"] == "REST3"
        state["page"] = "prescript"
        given = eng.settle_rewards(t0 + timedelta(minutes=51))
        assert given and given[0]["seconds"] == 180
        assert granted[-1] == (180, "REST3")

        # 空闲词条: 没有作废时限, 到 minutes 自动完成, t 按分钟数算
        state["mode"] = "idle"
        t1 = t0 + timedelta(hours=1)
        cur = eng.draw(t1, choose="VII")
        assert cur["type"] == "VII" and cur["expires"] is None and cur["due"] == _iso(t1 + timedelta(minutes=20))
        assert cur["body"] == "到开阔场地进行运动,持续20min." and cur["pool"] == "idle"
        eng._last_tick = t1
        eng.tick(t1 + timedelta(minutes=19, seconds=59))
        assert eng.current is cur
        eng.tick(t1 + timedelta(minutes=20))
        assert eng.current is None and eng._data["history"][-1]["result"] == "completed"
        assert abs(eng._data["history"][-1]["t"] - 0.4) < 1e-9 and granted[-1] == (72, "REST1.2")
        clear()
        # 离开指令页 / 最小化立即作废, 可以马上再领 (没有冷却)
        for leave in ({"page": "stats"}, {"min": True}):
            state.update(page="prescript", min=False)
            cur = eng.draw(t1, choose="VIII")
            state.update(leave)
            eng.tick(t1 + timedelta(minutes=1))
            assert eng.current is None and eng._data["history"][-1]["result"] == "void"
            assert eng.can_draw(t1)
        state.update(page="prescript", min=False)
        clear()
        # 旧存档里的 V (只有 expires) 照样 5 分钟后完成
        cur = eng.draw(t1, choose="V")
        cur["expires"], cur["due"] = _iso(t1 + timedelta(minutes=5)), None
        eng._last_tick = t1
        eng.tick(t1 + timedelta(minutes=5))
        assert eng.current is None and eng._data["history"][-1]["type"] == "V"
        assert eng._data["history"][-1]["result"] == "completed"
        clear()

        # 时间段: 对得上的词条拿走 V 那一格 (0.8) 的 80%, 其余平分; 已经抽到过的换成 V
        def at(h, m=0):
            return t0.replace(hour=h, minute=m)
        eng._data["run"] = []
        def boost(h, m=0):
            return eng.idle_boosts(at(h, m))
        assert boost(5, 29) == ["V"] and boost(5, 30) == ["VII"]
        assert boost(7, 29) == ["VII"] and boost(7, 30) == ["V"]
        assert boost(13) == ["VIII"] and boost(14, 29) == ["VIII"] and boost(14, 30) == ["V"]
        assert boost(19, 30) == ["VIII"] and boost(20, 30) == ["XI"]
        assert boost(9) == ["IX"] and boost(22, 29) == ["IX"] and boost(22, 30) == ["V"]
        assert boost(11, 59) == ["XI"] and boost(23) == ["X"] and boost(23, 59) == ["X"]
        assert boost(0, 30) == ["V"] and boost(15) == ["V"]
        for h, m in ((10, 0), (7, 0), (23, 30), (13, 30), (3, 0)):
            opts = dict(eng.choices("idle", at(h, m)))
            assert abs(sum(opts.values()) - 1.0) < 1e-9
            assert abs(opts["I"] - 0.05) < 1e-9 and abs(opts["IV"] - 0.02) < 1e-9
            group = {k: opts[k] for k in IDLE_KINDS}
            assert abs(sum(group.values()) - 0.9) < 1e-9
            boosted = boost(h, m)[0]
            assert abs(group[boosted] - 0.72) < 1e-9, (h, m)
            assert all(abs(v - 0.036) < 1e-9 for k, v in group.items() if k != boosted)
        # 设置里改时段 (设置 → 日常): 改完立即用新的; 写错了退回默认; 重叠的词条均分被抬高的那一份
        keep_cfg = {k: getattr(config, "PRESCRIPT_WIN_" + k) for k in config.PRESCRIPT_WIN_KINDS}
        try:
            config.PRESCRIPT_WIN_XI = "05:00-08:00"
            assert set(boost(7)) == {"VII", "XI"} and boost(8) == ["V"]
            opts = dict(eng.choices("idle", at(7)))
            assert abs(opts["VII"] - 0.36) < 1e-9 and abs(opts["XI"] - 0.36) < 1e-9
            assert abs(opts["V"] - 0.045) < 1e-9 and abs(sum(opts.values()) - 1.0) < 1e-9
            eng._data["run"] = ["XI"]                   # 抽到过的不再算重叠
            assert boost(7) == ["VII"]
            eng._data["run"] = []
            config.PRESCRIPT_WIN_VII = "写错了"
            assert set(boost(6)) == {"VII", "XI"} and config.prescript_windows("VII") == [(330, 450)]   # 写错了 = 默认
            assert boost(5, 15) == ["XI"]
            config.PRESCRIPT_WIN_V = "00:00-05:00"      # V 也可以设时段 (没有别的词条抬高时它不用兜底)
            assert boost(3) == ["V"] and set(boost(6)) == {"VII", "XI"}
            config.PRESCRIPT_WIN_X = ""
            assert not config.prescript_windows("X") and boost(23, 30) == ["V"]
            config.PRESCRIPT_WIN_IX = "22:00-02:00"      # 跨零点
            assert boost(23, 30) == ["IX"] and set(boost(1)) == {"IX", "V"} and boost(2) == ["V"]
        finally:
            for k, v in keep_cfg.items():
                setattr(config, "PRESCRIPT_WIN_" + k, v)
        # 连续抽取里抽到过的 (V 除外) 不能再抽, 对得上的词条已经抽到就换成 V
        eng._data["run"] = ["VII"]
        opts = dict(eng.choices("idle", at(7)))
        assert "VII" not in opts and abs(opts["V"] - 0.72) < 1e-9 and abs(sum(opts.values()) - 1.0) < 1e-9
        eng._data["run"] = ["II", "VIII", "IX", "X", "XI", "VII"]
        opts = dict(eng.choices("idle", at(7)))
        assert opts["V"] > 0.89 and not any(k in opts for k in ("II", "VII", "VIII", "IX", "X", "XI"))
        assert abs(sum(opts.values()) - 1.0) < 1e-9
        # 专注 / 休息池不拆 V, 也不看这一轮
        assert "VII" not in dict(eng.choices("break", at(7))) and dict(eng.choices("break", at(7)))["V"] == 0.05
        eng._data["run"] = []

        # 一轮连续的空闲抽取: 抽到的 (V 除外) 记下来, V 可以重复; 重新进入空闲池重置
        clear()
        eng._data["last_pool"] = "focus"
        eng._data["force_focus"] = False
        eng._data["focus_chance"] = -1.0           # 这一段不让它触发专注池
        eng.draw(t1, choose="VII", pool="idle")
        eng._finish("completed", t1, 0.4, cur=eng.current)
        eng.draw(t1, choose="V", pool="idle")
        eng._finish("completed", t1, 0.1, cur=eng.current)
        eng.draw(t1, choose="V", pool="idle")
        assert eng._data["run"] == ["VII"], eng._data["run"]
        clear()
        eng._data["last_pool"] = "focus"
        eng.draw(t1, choose="VIII", pool="idle")
        assert eng._data["run"] == ["VIII"]
        clear()
        eng._data["force_focus"] = False

        # 每次空闲抽取有概率让下一次改抽专注池: 初始 25%, 没触发 +25%, 触发后回到 25%
        class Roll:
            def __init__(self, values):
                self.values = list(values)
                self.base = random.Random(3)

            def random(self):
                return self.values.pop(0) if self.values else self.base.random()

            def __getattr__(self, name):
                return getattr(self.base, name)
        eng.rng = Roll([0.9, 0.6, 0.8])
        eng._data["focus_chance"] = FOCUS_CHANCE_START
        eng._data["last_pool"] = "idle"
        eng._data["run"] = []
        for want_chance in (0.5, 0.75, 1.0):
            clear()
            eng.draw(t1, choose="V", pool="idle")
            assert abs(eng._data["focus_chance"] - want_chance) < 1e-9 and eng._data["force_focus"] is False
        clear()
        eng.rng = Roll([0.99])                       # 概率已经到 100%: 必定触发
        eng.draw(t1, choose="V", pool="idle")
        assert eng._data["force_focus"] is True and eng._data["focus_chance"] == FOCUS_CHANCE_START
        # 下一次抽取: 不管状态是什么都抽专注池, 抽完标志清掉
        clear()
        state["mode"] = "idle"
        eng.rng = random.Random(7)
        cur = eng.draw(t1, choose="VI")
        assert cur["pool"] == "focus" and eng._data["force_focus"] is False
        clear()
        eng._data["force_focus"] = False

        # 合约期间空闲: 随机抽专注 / 休息池, 不抽空闲池
        state["contract"] = True
        assert {eng._pool(t1) for _ in range(60)} == {"focus", "break"}
        state["mode"] = "break"
        assert eng._pool(t1) == "break"
        state["contract"] = False
        state["mode"] = "idle"
        assert eng._pool(t1) == "idle"

        # X: 输入框三段文字, 没改保存 = 作废, 改过保存 = 完成
        clear()
        eng.hour_log_fn = lambda: "09:00  学习\n10:00  写代码"
        eng.focus_log_fn = lambda: "09:05-09:55 专注 (50 分钟)\n10:10-10:40 专注 (30 分钟)"
        eng._data["history"].append({"id": "h1", "type": "III", "body": "休息5min.", "accepted": _iso(t1),
                                     "ended": _iso(t1), "result": "completed", "t": 0.2})
        eng._data["history"].append({"id": "h2", "type": "I", "body": "接下来的1h专注50min.", "accepted": _iso(t1),
                                     "ended": _iso(t1), "result": "expired", "t": None})
        eng._data["closes"] = [_iso(t1.replace(hour=8, minute=15)), _iso(t1 - timedelta(days=1))]
        eng._data["last_pool"] = "focus"
        cur = eng.draw(at(23, 10), choose="X", pool="idle")
        assert cur["type"] == "X" and cur["due"] is None and cur["expires"] is None
        base = eng.log_prefill(at(23, 10))
        paras = base.split("\n\n")
        assert len(paras) == 4 and paras[0] == "09:00  学习\n10:00  写代码", paras
        assert paras[1] == "09:05-09:55 专注 (50 分钟)\n10:10-10:40 专注 (30 分钟)", paras[1]
        assert "休息5min. (完成)" in paras[2] and "接下来的1h专注50min. (废弃)" in paras[2] and "(进行中)" in paras[2]
        assert paras[3] == "08:15", paras[3]
        assert eng.log_prefill(at(23, 40)) == base
        eng._last_tick = at(23, 10)
        eng.tick(at(23, 55))                      # 没有计时: 一直等着
        assert eng.current is cur
        assert eng.submit_log(base + "  \n", at(23, 56)) == "void" and eng.current is None
        assert eng._data["history"][-1]["result"] == "void" and not eng._data["logs"]
        cur = eng.draw(at(23, 57), choose="X", pool="idle")
        base = eng.log_prefill(at(23, 57))
        assert eng.submit_log(base + "\n今天读了书", at(23, 58)) == "completed"
        assert eng._data["history"][-1]["result"] == "completed" and abs(eng._data["history"][-1]["t"] - 0.1) < 1e-9
        assert "今天读了书" in eng._data["logs"][at(23, 58).strftime("%Y-%m-%d")]
        assert eng.submit_log("x") is None
        eng.hour_log_fn = lambda: ""              # 没启用整点记录: 第一段空白
        eng.focus_log_fn = lambda: (_ for _ in ()).throw(RuntimeError("x"))   # 取专注时间段出错: 这一段空白, 其他照常
        cur = eng.draw(at(23, 59), choose="X", pool="idle")
        assert eng.log_prefill(at(23, 59)).startswith("\n\n\n\n")
        eng.abandon_log(at(23, 59))
        assert eng.current is None and eng._data["history"][-1]["result"] == "void"
        eng.note_close(at(23, 59))
        assert at(23, 59).strftime("%H:%M") in eng._today_close_lines(at(23, 59))
        # X 也要留在指令页上, 离开就作废
        cur = eng.draw(at(23, 59), choose="X", pool="idle")
        state["page"] = "timer"
        eng.tick(at(23, 59) + timedelta(seconds=1))
        assert eng.current is None and eng._data["history"][-1]["result"] == "void"
        state["page"] = "prescript"
        eng._data["force_focus"] = False

        # IV (每日专注): 手里已经有一条就不能再领 (任何池子都不行), 完成后今天也不能再领
        clear()
        today["hours"] = 0.0
        assert "IV" in dict(eng.choices("focus", at(12)))
        assert eng.draw(at(12), choose="IV", pool="focus")
        for pool in ("focus", "break", "idle"):
            assert "IV" not in dict(eng.choices(pool, at(12))), pool
        assert abs(sum(w for _, w in eng.choices("focus", at(12))) - 1.0) < 1e-9
        eng._finish("completed", at(12), 1.0, cur=eng.active_of("IV"))
        assert "IV" not in dict(eng.choices("focus", at(12)))
        eng._data["daily"]["iv_done"] = False
        assert "IV" in dict(eng.choices("focus", at(12)))
        clear()
        state["mode"] = "idle"

        # II: 下一次正常完成的合约, t = 总时长 (2h)
        clear()
        state["mode"] = "focus"
        t2 = t0 + timedelta(hours=5)
        cur = eng.draw(t2, choose="II")
        assert cur and cur["type"] == "II"
        n = int(cur["n"])
        contracts.append({
            "id": "c1", "result": "completed", "level": n,
            "start": _iso(t2 + timedelta(minutes=5)),
            "planned": _iso(t2 + timedelta(hours=2, minutes=5)),
            "end": _iso(t2 + timedelta(hours=2, minutes=5)),
        })
        state["page"] = "timer"
        eng.tick(t2 + timedelta(hours=2, minutes=6))
        assert eng.current is None and eng._data["history"][-1]["t"] == 2.0
        assert eng._data["pending"][-1]["code"] == "REST6"

        # VI: 统计页连续 3 分钟
        clear()
        state["page"] = "prescript"
        t3 = t2 + timedelta(hours=4)
        cur = eng.draw(t3, choose="VI")
        assert cur and cur["type"] == "VI"
        state["page"] = "stats"
        eng._last_tick = t3
        eng.tick(t3 + timedelta(minutes=1))
        assert eng.current is not None
        eng.tick(t3 + timedelta(minutes=4))
        assert eng.current is None and eng._data["history"][-1]["result"] == "completed"

        # 今天完成的 t 合计到 10: 20 分钟
        eng._data["daily"]["completed_t"] = 10.0
        before = len(granted)
        given = eng.settle_rewards(t3 + timedelta(hours=1))
        assert any(g["code"] == "REST20" and g["seconds"] == 1200 for g in given)
        assert len(granted) >= before + 1
        assert eng.settle_rewards(t3 + timedelta(hours=1, minutes=1)) == []

        # 过了 1h 才醒来的 tick: 只算到作废时刻, 不会把 1h 之后的专注补进来
        clear()
        state["page"] = "prescript"
        state["mode"] = "break"
        t4 = t3 + timedelta(hours=20)
        cur = eng.draw(t4, choose="I")
        assert cur and cur["type"] == "I"
        eng._last_tick = t4
        eng.tick(t4 + timedelta(minutes=59))
        state["mode"] = "focus"
        eng.tick(t4 + timedelta(hours=2))
        assert eng.current is None and eng._data["history"][-1]["result"] == "expired"

        # 合约期间抽不到 VI: 它的概率平分给同池其他还能抽到的指令
        state["contract"] = True
        assert eng._weights("focus", t4)[5] == 0.0 and abs(sum(eng._weights("focus", t4)) - 1.0) < 1e-9
        assert eng._weights("break", t4)[5] == 0.0
        state["contract"] = False
        assert eng._weights("focus", t4)[5] > 0.0

        # III: 休息分钟数 = 剩余休息时间的 50%, 最多 30 分钟; t = 休息时间 (小时) × 2
        assert rest_minutes(3000) == 25 and rest_minutes(7200) == 30 and rest_minutes(0) == 1
        clear()
        eng.remaining_rest_fn = lambda: 3000
        c3 = eng.draw(t4 + timedelta(hours=40), choose="III")
        assert c3["n"] == 25 and c3["body"] == "休息25min." and abs(c3["t"] - 25 / 30) < 1e-9
        eng.remaining_rest_fn = lambda: 0.0
        clear()

        # 领到任何一个空闲词条都暂停后台的音频 / 视频, 其他指令不碰
        paused = []
        eng.pause_media_fn = lambda: paused.append(1)
        t_v = t4 + timedelta(hours=60)
        eng.draw(t_v, choose="I")
        assert not paused
        for i, kind in enumerate(IDLE_KINDS):
            clear()
            eng.draw(t_v + timedelta(hours=1), choose=kind)
            assert len(paused) == i + 1, kind
        eng.pause_media_fn = None
        clear()

        # 完成时通知 (播完成音效); 每个入口的标记: I 专注, III 休息, II 合约, VI 统计, IV / 空闲词条不标记
        done = []
        eng.on_complete = lambda: done.append(1)
        t_m = t4 + timedelta(hours=50)
        for kind in ("II", "VI", "IV", "V", "XI"):
            clear()
            eng.draw(t_m, choose=kind)
            assert eng.marks() == ({"contract"} if kind == "II" else {"stats"} if kind == "VI"
                                   else set()), kind
        clear()
        state["page"] = "prescript"
        eng.draw(t_m, choose="V")
        eng._last_tick = t_m
        eng.tick(t_m + timedelta(minutes=6))
        assert done == [1] and eng.current is None
        eng.on_complete = None

        # 设备同步: 领到的指令 / 奖励 / 结果在两台设备间一致; 结束的盖过进行中的; 空闲词条要所有设备都离开指令页才作废
        import copy
        root2 = tempfile.mkdtemp(prefix="oit_prescript_b_")
        try:
            peer = {"page": True}
            sa = {"page": "prescript", "min": False}
            sb = {"page": "timer", "min": False}
            gb = []
            A = PrescriptEngine(root, state_fn=lambda: state["mode"], page_fn=lambda: sa["page"],
                                minimized_fn=lambda: sa["min"], peers_on_page_fn=lambda: sb["page"] == "prescript",
                                contracts_fn=lambda: contracts, rng=random.Random(1))
            B = PrescriptEngine(root2, state_fn=lambda: state["mode"], page_fn=lambda: sb["page"],
                                minimized_fn=lambda: sb["min"], peers_on_page_fn=lambda: sa["page"] == "prescript",
                                grant_rest_fn=lambda s, c: gb.append((s, c)), contracts_fn=lambda: contracts,
                                rng=random.Random(2))
            for e in (A, B):
                e._data.update(current=None, extra=[], history=[], pending=[], settled={}, closes=[], logs={})
            ts = datetime.now().replace(hour=15, minute=0, second=0, microsecond=0)
            state["mode"] = "idle"

            def push(src, dst):
                dst.sync_apply(copy.deepcopy(json.loads(json.dumps(src.sync_doc(ts)))), ts)
            # A 领一条 III, B 同步到; 进度不同步, B 自己累计
            A.draw(ts, choose="III", pool="break")
            push(A, B)
            assert [c["type"] for c in B.actives()] == ["III"] and B.current["id"] == A.current["id"]
            assert B.current["break_sec"] == 0.0 and "break_sec" not in A.sync_doc(ts)["i:" + A.current["id"]]
            # 两边的数量上限 / 专注指令互相算
            A.draw(ts, choose="I", pool="focus")
            push(A, B)
            assert len(B.actives()) == 2 and not B.can_draw(ts)
            # B 完成 III: A 同步后也结束 (结果盖过进行中, 不重复给奖励)
            iid = [c for c in B.actives() if c["type"] == "III"][0]
            B._finish("completed", ts + timedelta(minutes=5), 0.2, cur=iid)
            before = list(A._data["pending"])
            push(B, A)
            assert [c["type"] for c in A.actives()] == ["I"] and A._data["history"][-1]["result"] == "completed"
            assert A._data["pending"] and len(A._data["pending"]) == 1 and A._data["pending"][0]["id"] == iid["id"]
            # 进行中的旧版本不能把结束的盖回去
            stale = {"i:" + iid["id"]: dict(iid)}
            assert devsync_wins(stale["i:" + iid["id"]], A._data["history"][-1]) is False
            A.sync_apply(stale, ts)
            assert [c["type"] for c in A.actives()] == ["I"]
            # 进入 / 退出指令页是事件: 一台设备进 / 出, 另一台跟着 (够新才跟, 旧的不理; 跟着做的不再发回去)
            seen = []
            B.page_event_fn = seen.append
            A.note_page(True, "devA", ts)
            push(A, B)
            assert seen == [True] and B._page_rec["by"] == "devA"
            push(A, B)
            assert seen == [True], "同一个事件只跟一次"
            A.note_page(False, "devA", ts + timedelta(seconds=30))
            push(A, B)
            assert seen == [True, False]
            old = {"p:page": {"on": True, "at": (ts - timedelta(minutes=10)).isoformat(timespec="seconds"), "by": "devX"}}
            B.sync_apply(old, ts)
            assert seen == [True, False], "太旧的事件不理"
            B.page_event_fn = None
            # 日志的 2 小时冷却随同步: A 看了, B 同步后也要等; 谁看得最晚算谁的
            assert A.journal_view() == "" and B.journal_view() == ""
            A.note_journal(ts)
            push(A, B)
            assert B.journal_view() == A.journal_view() != ""
            B._data["journal_view"] = (ts + timedelta(hours=1)).isoformat(timespec="seconds")
            push(A, B)
            assert B.journal_view() > A.journal_view(), "旧的不能盖掉新的"
            push(B, A)
            assert A.journal_view() == B.journal_view()
            # 奖励: 在 A 的指令页上结算 -> B 同步后不再结算
            A.page_fn = lambda: "prescript"
            given = A.settle_rewards(ts + timedelta(minutes=6))
            assert len(given) == 1 and iid["id"] in A._data["settled"]
            push(A, B)
            assert not B._data["pending"] and B.settle_rewards(ts + timedelta(minutes=7)) == [] and gb == []
            A.page_fn = lambda: sa["page"]
            # 两台设备都完成同一条: 奖励只有一份
            A._finish("completed", ts + timedelta(minutes=9), 1.0, cur=A.active_of("I"))
            B.sync_apply({"i:" + A._data["history"][-1]["id"]: dict(A._data["history"][-1])}, ts)
            assert not B.actives() and len(B._data["history"]) == 2
            # 空闲词条: A 离开页面时 B 还停在页面上就不作废; 都离开才作废
            sb["page"] = "prescript"
            sa["page"] = "prescript"
            idle = A.draw(ts + timedelta(minutes=10), choose="VIII", pool="idle")
            push(A, B)
            assert B.current["type"] == "VIII"
            A._last_tick = ts + timedelta(minutes=10)
            sa["page"] = "timer"
            A.tick(ts + timedelta(minutes=11))
            assert A.current is not None and A.current["id"] == idle["id"], "B 还在指令页上"
            A.tick(ts + timedelta(minutes=21))             # 到点了, 但 A 不在页面上: 不由 A 完成
            assert A.current is not None
            B._last_tick = ts + timedelta(minutes=10)
            B.tick(ts + timedelta(minutes=21))              # B 在页面上: 由 B 完成
            assert B.current is None and B._data["history"][-1]["result"] == "completed"
            push(B, A)
            assert A.current is None and A._data["history"][-1]["result"] == "completed"
            sa["page"] = "prescript"
            A.draw(ts + timedelta(minutes=30), choose="IX", pool="idle")
            push(A, B)
            sa["page"] = "timer"
            sb["page"] = "timer"
            A.tick(ts + timedelta(minutes=31))
            assert A.current is None and A._data["history"][-1]["result"] == "void"
            B.tick(ts + timedelta(minutes=31))
            assert B.current is None
            # 当天合计从记录里算 (两台设备一样)
            A._recompute_daily(ts)
            assert abs(A._data["daily"]["completed_t"] - (0.2 + 1.0 + 0.2)) < 1e-9
        finally:
            shutil.rmtree(root2, ignore_errors=True)

        # 每天 4:00 起自动给 20 分钟休息: 4:00 前不给, 4:00 之后第一次判定给, 同一天只给一次, 别的设备给过的不再给, 第二天再给
        root3 = tempfile.mkdtemp(prefix="oit_prescript_dawn_")
        try:
            gd, gd2 = [], []
            D = PrescriptEngine(root3, state_fn=lambda: "idle", today_hours_fn=lambda: 0.0,
                                grant_rest_fn=lambda s, c: gd.append((s, c)))
            d0 = datetime(2026, 11, 3, 3, 59, 50)
            D.tick(d0)
            assert gd == [], "4:00 前不给"
            D.tick(datetime(2026, 11, 3, 4, 0, 0))
            assert gd == [(1200, "REST20")], gd
            D.tick(datetime(2026, 11, 3, 4, 0, 1))
            D.tick(datetime(2026, 11, 3, 15, 0, 0))
            assert len(gd) == 1, "同一天只给一次"
            assert "dawn-2026-11-03" in D._data["settled"] and not D._data["pending"]
            D.tick(datetime(2026, 11, 4, 3, 0, 0))                # 4 号凌晨 3 点: 还是 3 号那份, 4 号的没到点
            assert len(gd) == 1
            D.tick(datetime(2026, 11, 4, 9, 30, 0))               # 机器 4:00 没开: 之后第一次判定补上
            assert len(gd) == 2 and "dawn-2026-11-04" in D._data["settled"]
            # 另一台设备已经结算过今天的 (同步过来): 这台不再给
            root4 = tempfile.mkdtemp(prefix="oit_prescript_dawn2_")
            try:
                E = PrescriptEngine(root4, state_fn=lambda: "idle", today_hours_fn=lambda: 0.0,
                                    grant_rest_fn=lambda s, c: gd2.append((s, c)))
                E._data.setdefault("settled", {})["dawn-2026-11-05"] = {"id": "dawn-2026-11-05", "seconds": 1200, "at": "2026-11-05T04:00:00"}
                E.tick(datetime(2026, 11, 5, 4, 0, 5))
                assert gd2 == [], "别的设备给过就不再给"
                E.tick(datetime(2026, 11, 6, 4, 0, 5))
                assert gd2 == [(1200, "REST20")]
            finally:
                shutil.rmtree(root4, ignore_errors=True)
        finally:
            shutil.rmtree(root3, ignore_errors=True)

        # 存档再读出来: 历史 / 记录 / 关闭时刻还在
        eng.save()
        eng2 = PrescriptEngine(root, state_fn=lambda: "idle", today_hours_fn=lambda: 0.0)
        assert eng2._data["history"] and eng2._data["logs"] and eng2._data["closes"]
        return "prescript selftest OK"
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    print(_selftest())
