

import os
import time
import queue
import threading
from datetime import datetime

import browserfence
import bypassguard
import config
from netproxy import ProxyController


# 倍率类数值在 config.py (可在 Settings → 具体设置 中修改), 这里每次用时现读.

from .net_quota import QuotaMixin
from .net_store import StoreMixin
from .net_backend import BackendMixin
from .net_session import SessionMixin
from .net_live import LiveMixin
from .net_tick import TickMixin


class NetGuard(QuotaMixin, StoreMixin, BackendMixin, SessionMixin, LiveMixin, TickMixin):
    gift = 0.0                        # 今天合约奖励的额度 (见 __init__)
    # 防绕过 (见 __init__); 自检里不经过 __init__ 造出来的对象没有这些, 不防绕过
    fence = None
    _fence_at = 0.0
    _fence_busy = False
    _fence_clean = False
    bypass = None
    _bp_at = 0.0
    _bp_busy = False
    _bp_clean = False
    _bp_init = False
    _watch_at = 0.0
    tamper = {"count": 0, "at": 0.0}
    on_tamper = None

    def __init__(self, data_dir: str):
        self.dead = False
        self.state_file = os.path.join(data_dir, "net_state.json")
        self.log_file = os.path.join(data_dir, "net_requests.log")
        # 每次联网会话的实际联网时长(按理由统计用;只保留本月与上月)
        self.usage_file = os.path.join(data_dir, "net_usage.json")
        self.usage = []
        self._session = None
        self._usage_dirty = False
        self.q = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

        self.date = datetime.now().strftime('%Y-%m-%d')
        self.used = 0.0
        self.bonus = 0.0
        self.gift = 0.0               # 今天合约奖励的额度, 不用还
        self.tomorrow_used = 0.0
        self.debt = 0.0
        self.grant_left = 0.0
        self.grant_rate = 1.0
        self.pending = None
        # 设备同步: 其他设备今天的 {used, bonus, tomorrow_used, debt} 之和 (见 devsync.peer_net)
        self.peer_net = lambda: {"used": 0.0, "bonus": 0.0, "tomorrow_used": 0.0, "debt": 0.0, "gift": 0.0}
        # 行为模式 (MECHANICS 21): 有外出的日子额度 ×0.2; 正在外出时冷静期 ×0.3
        self.quota_factor = lambda day: 1.0
        # 换成 Adapter (禁用网卡) 之前调用: 设备同步先把本机的"断网"状态发出去 (MECHANICS 15.5)
        self.before_adapter = lambda: None
        self.cooldown_factor = lambda now: 1.0
        # 设备同步: 其他设备的联网会话 (按理由统计用)
        self.peer_usage = lambda: []
        # 联网状态 (申请中 / 联网中, MECHANICS 2.8): 随设备同步, 关掉程序再打开时接着用.
        # live_id: 这一状态的编号 (每次改动换一个); live_owner: 发起的设备, 由它扣额度 (它不在线时由本机扣)
        self.my_id = lambda: ""
        self.charge_ok = lambda owner: True
        self.live_id = ""
        self.live_owner = ""
        self.grant_end = 0.0                  # 这次联网到几点 (墙上时间, 秒)
        self._grant_info = {}                 # 这次联网的理由 / 申请的分钟数
        self._saved_live = None
        self.proxy = ProxyController(
            data_dir, mode_getter=lambda: ("blockall" if getattr(config, "NET_MODE", "") == "proxy_all"
                                           else getattr(config, "FILTER_MODE", "blacklist")),
            dev_getter=config.changes_instant)
        self._nic_off_by_fallback = False
        # 防绕过 (MECHANICS 3.2.2): 系统代理被改了马上改回去; 浏览器围栏 (防火墙出站规则, browserfence.py)
        self.fence = browserfence.BrowserFence()
        self._fence_at = 0.0
        self._fence_busy = False
        self._fence_clean = False             # 围栏规则已经删干净了 (不在 Proxy 方式时只删一次)
        # 不走系统代理的程序 (MECHANICS 3.2.3): 发现它直连外面就封锁 (防火墙规则 + 掐断已有的连接, bypassguard.py)
        self.bypass = bypassguard.BypassGuard()
        self._bp_at = 0.0
        self._bp_busy = False
        self._bp_clean = False
        self._bp_init = False                 # 程序打开后第一次扫描前先删掉上次留下的规则
        self._watch_at = 0.0
        self.tamper = {"count": 0, "at": 0.0}
        self.on_tamper = lambda kind: None    # app 接上: 提示用户
        self._applied_mode = self.net_mode
        self._load()
        self._load_usage()
        self._last_save = time.monotonic()
        self._last_enforce = time.monotonic()
        if self.dev_mode:
            self.ensure_online()
        elif self.net_mode == "proxy":
            # 上次可能是 Adapter 方式关闭的, 或者代理起不来退回过禁用网卡 (fail-closed 时网卡一直禁用着):
            # 过滤代理就位后把网卡打开 —— Proxy 方式下网卡保持开启 (MECHANICS 3.2)
            self.q.put(self._enter_proxy)
        else:
            self.block()
            # 上次是 Proxy 方式关闭的: 系统代理还指向已经退出的过滤代理, 联网时也连不上. 还原掉
            self.q.put(lambda: self.proxy.stop(restore=True))
        # 关闭前的联网状态: 申请还在等就接着等, 联网还没到时间就接着联网 (到原来的结束时刻为止)
        if self._saved_live and not self.dev_mode:
            self.adopt(self._saved_live)
        self._saved_live = None



def _selftest():
    import tempfile
    import datetime as _dt
    import config

    had_dev = hasattr(config, "DEV_MODE")
    saved = (config.DAILY_LIMIT, config.COOLDOWN,
             config.TICK_CLAMP, getattr(config, "DEV_MODE", False))
    saved_mode = getattr(config, "NET_MODE", "adapter")
    config.DAILY_LIMIT, config.COOLDOWN = 3600, 300
    config.TICK_CLAMP = 10 ** 6
    config.DEV_MODE = False
    config.NET_MODE = "adapter"
    try:
        n = NetGuard(tempfile.mkdtemp())
        n.dead = True
        n.date = _dt.datetime.now().strftime('%Y-%m-%d')
        assert n.remaining == 3600 and not n.online
        assert abs(n.quick_left - 1800) < 1e-6 and abs(n.loan_left - 1800) < 1e-6

        # 普通申请: 走冷静期, 1x 计费
        ok, msg = n.request(30, "查资料")
        assert ok and n.mode == "pending" and n.pending["at"] > time.time() + 200
        assert not n.pending["quick"]
        n.pending["at"] = time.time() - 1
        n.tick(0.0)
        assert n.online and not n.grant_quick and abs(n.grant_left - 1800) < 1e-6
        n.tick(600.0)
        assert abs(n.used - 600) < 1e-6, n.used
        n.end_early()
        assert not n.online and abs(n.used - 600) < 1e-6

        # 快速访问: 立刻联网, 2x 计费, 没有冷静期
        ok, msg = n.request(10, "快", quick=True)
        assert ok and n.pending["quick"] and n.pending["at"] <= time.time() + 1, msg
        n.tick(0.0)
        assert n.online and n.grant_quick and abs(n.grant_left - 600) < 1e-6
        n.tick(60.0)
        assert abs(n.used - 720) < 1e-6, n.used
        n.end_early()
        assert abs(n.used - 720) < 1e-6, "提前结束不再额外扣费"
        # 按理由统计的是实际联网时长, 不乘快速访问倍数
        month = n.date[:7]
        assert n.usage_by_reason(month) == [("查资料", 600.0), ("快", 60.0)], n.usage
        assert n._session is None

        # 快速访问上限 = 剩余 / 2
        n.used = 3000.0
        ok, msg = n.can_quick(6)
        assert not ok and "at most" in msg, msg
        ok, msg = n.request(5, "快", quick=True)
        assert ok, msg
        n.tick(0.0)
        n.tick(10 ** 6)
        assert abs(n.used - 3600) < 1e-6 and not n.online, n.used

        # 冷静期中可升级为快速访问
        n.used = 0.0
        ok, msg = n.request(20, "x")
        assert ok
        ok, msg = n.convert_to_quick()
        assert ok and n.pending["quick"] and n.pending["at"] <= time.time() + 1, msg
        ok, msg = n.convert_to_quick()
        assert not ok and "already" in msg
        n.cancel()
        assert n.pending is None
        n.used = 3000.0
        ok, msg = n.request(5, "x")
        assert ok
        ok, msg = n.convert_to_quick()
        assert ok, msg
        n.cancel()
        ok, msg = n.request(6, "x")
        assert ok
        ok, msg = n.convert_to_quick()
        assert not ok and "at most" in msg, msg
        n.cancel()
        ok, msg = n.convert_to_quick()
        assert not ok and "No pending request" in msg

        # 紧急借贷: 今天 +X, 明天 -2X, 不联网
        n.used = 3600.0
        assert n.remaining == 0.0
        ok, msg = n.request(10, "x")
        assert not ok and "emergency loan" in msg, msg
        ok, msg = n.borrow(20, "急")
        assert ok, msg
        assert not n.online and n.pending is None, "借贷本身不联网"
        assert abs(n.bonus - 1200) < 1e-6 and abs(n.remaining - 1200) < 1e-6
        assert abs(n.tomorrow_used - 2400) < 1e-6 and abs(n.tomorrow_remaining - 1200) < 1e-6
        assert abs(n.today_total - 4800) < 1e-6 and abs(n.quota_used - 3600) < 1e-6
        assert abs(n.loan_left - 600) < 1e-6
        ok, msg = n.can_borrow(11)
        assert not ok and "at most" in msg, msg
        ok, msg = n.borrow(10, "急")
        assert ok and abs(n.tomorrow_remaining) < 1e-6 and abs(n.remaining - 1800) < 1e-6
        ok, msg = n.borrow(1, "急")
        assert not ok and "fully borrowed" in msg, msg

        # 借来的额度既可走冷静期, 也可走快速访问
        ok, msg = n.request(10, "x", quick=True)
        assert ok, msg
        n.tick(0.0)
        n.tick(300.0)
        assert abs(n.used - 4200) < 1e-6, n.used
        n.end_early()

        # 跨日: 明天的 2x 变成欠款, 借到的额度清零
        n._rollover("2099-01-01")
        assert n.used == 0.0 and n.bonus == 0.0 and n.tomorrow_used == 0.0
        assert abs(n.debt - 3600) < 1e-6 and n.remaining == 0.0
        n._rollover("2099-01-02")
        assert n.debt == 0.0 and n.remaining == 3600, "欠款只扣一天, 不会滚雪球"

        # 存档往返
        n.dead = False
        n.date = _dt.datetime.now().strftime('%Y-%m-%d')
        n.used, n.bonus, n.tomorrow_used, n.debt = 100.0, 600.0, 1200.0, 300.0
        n._save()
        m = NetGuard.__new__(NetGuard)
        m.state_file, m.date = n.state_file, n.date
        m.used = m.bonus = m.tomorrow_used = m.debt = 0.0
        m._load()
        assert (m.used, m.bonus, m.tomorrow_used, m.debt) == (100.0, 600.0, 1200.0, 300.0)

        # 借贷后关掉程序, 之后某天才打开: 欠款照样要还, 其余清零
        for later in ("2099-01-01", "2099-01-05"):
            m = NetGuard.__new__(NetGuard)
            m.state_file, m.log_file, m.usage_file = n.state_file, n.log_file, n.usage_file
            m.dead, m.usage, m._usage_dirty, m._session = False, [], False, None
            m.date = later
            m.used = m.bonus = m.tomorrow_used = m.debt = 0.0
            m._load()
            assert (m.used, m.bonus, m.tomorrow_used) == (0.0, 0.0, 0.0)
            assert m.debt == 1200.0, (later, m.debt)
            # 当天再次打开, 欠款还在 (已经写回今天的存档)
            m2 = NetGuard.__new__(NetGuard)
            m2.state_file, m2.date = n.state_file, later
            m2.used = m2.bonus = m2.tomorrow_used = m2.debt = 0.0
            m2._load()
            assert m2.debt == 1200.0, "欠款要写回存档, 当天重开也不能消失"
            n.date = _dt.datetime.now().strftime('%Y-%m-%d')
            n._save()                                   # 恢复成借贷当天的存档
        # 没借过的话, 新的一天什么都不带过来
        n.tomorrow_used = 0.0
        n._save()
        m = NetGuard.__new__(NetGuard)
        m.state_file, m.date = n.state_file, "2099-01-01"
        m.used = m.bonus = m.tomorrow_used = m.debt = 0.0
        m._load()
        assert m.debt == 0.0
        n.dead = True

        n.used, n.bonus, n.tomorrow_used, n.debt = 0.0, 0.0, 0.0, 0.0
        n._reset_grant()
        ok, msg = n.request(10, "x")
        assert ok and n.pending["at"] > time.time() + 200
        n.cancel()

        # 休息中申请: 冷静期 ×0.4 (300s → 120s)
        ok, msg = n.request(10, "x", cooldown_rate=config.BREAK_COOLDOWN_RATE)
        assert ok and 110 < n.pending["at"] - time.time() <= 120, n.pending
        n.cancel()
        assert n.mode == "offline"


        config.DEV_MODE = True
        assert n.mode == "dev" and n.online, "开发者模式下网络应视为一直可用"
        assert n.block() is None
        ok, msg = n.request(30, "x")
        assert not ok and "Already online" in msg, msg
        # 开发者模式照常消耗联网额度, 但只消耗不限制: 额度用完了也不会结束, 也不会阻止开启
        n.used = 0.0
        assert n.tick(100.0) is None and abs(n.used - 100.0) < 1e-6
        assert n.usage and n.usage[-1]["reason"] == "开发者模式" and abs(n.usage[-1]["seconds"] - 100.0) < 1e-6
        assert abs(n.remaining - (3600 - 100)) < 1e-6, "额度跟着减少"
        n.used = n.today_total - 20.0
        assert [n.tick(10.0) for _ in range(5)] == [None] * 5 and n.remaining == 0.0, "额度用完了开发者模式也不会结束"
        assert n.mode == "dev" and n.online and n.used > n.today_total, "额度用光后继续消耗"
        n.used = 0.0
        assert n.tick(7200.0) is None and n.mode == "dev", "也没有每天 1 小时的上限"
        n.dev_closed()
        assert n._session is None
        n.used, n.usage = n.today_total, []
        config.DEV_MODE = False
        config.DEV_MODE = True
        assert n.remaining == 0.0 and n.mode == "dev" and n.online, "额度用完了照样能开"
        assert n.tick(10.0) is None
        n.used, n.usage = 0.0, []
        n.pending = {"minutes": 5, "at": time.time() - 1, "reason": "x", "loan": False}
        n.tick(0.0)
        assert n.pending is None, "开发者模式下挂起的申请应被丢弃"
        config.DEV_MODE = False
        assert n.mode == "offline" and not n.online


        class _FakeProxy:
            def __init__(self):
                self.calls = []
                self.fail = False
                self.online = None

            def start(self):
                self.calls.append("start")
                if self.fail:
                    raise OSError("boom")

            def set_online(self, v):
                self.calls.append(("online", v))
                self.online = v

            def stop(self, restore=True):
                self.calls.append(("stop", restore))

        fp = _FakeProxy()
        n.proxy = fp
        n.dead = False
        ran = []
        n._run = lambda cmd: ran.append(cmd if isinstance(cmd, str) else "<fn>") \
            or (cmd() if callable(cmd) else None)
        config.NET_MODE = "proxy"
        assert n.net_mode == "proxy" and n.sync_mode() and not n.sync_mode()
        while not n.q.empty():
            n._run(n.q.get_nowait())
        assert fp.online is False and "start" in fp.calls, fp.calls
        assert any("Enable-NetAdapter" in str(c) for c in ran), "进入 proxy 模式后网卡必须是开的"

        fp.calls.clear()
        n.block(sync=True)
        assert fp.online is False
        n.unblock()
        n._run(n.q.get_nowait())
        assert fp.online is True and not any("Disable-NetAdapter" in str(c) for c in ran[-3:])

        ran.clear()
        fp.fail = True
        n.block(sync=True)
        assert any("Disable-NetAdapter" in str(c) for c in ran), "代理起不来时必须退回禁用网卡"
        assert n._nic_off_by_fallback
        fp.fail = False
        n.block(sync=True)
        assert not n._nic_off_by_fallback and any("Enable-NetAdapter" in str(c) for c in ran)

        fp.calls.clear()
        config.NET_MODE = "adapter"
        assert n.sync_mode()
        while not n.q.empty():
            n._run(n.q.get_nowait())
        assert ("stop", True) in fp.calls

        fp.calls.clear()
        n.ensure_online()
        assert ("stop", True) in fp.calls
        config.NET_MODE = "adapter"

        # 星期倍率: 今天的额度按今天是星期几, 借贷借的是明天(按明天的倍率)
        saved_mult = {k: getattr(config, k) for k in config.WEEKDAY_KEYS}
        try:
            for k in config.WEEKDAY_KEYS:
                setattr(config, k, 1.0)
            w = NetGuard.__new__(NetGuard)
            w.dead = True
            w.date = "2026-10-16"                      # 周五
            w.used = w.bonus = w.tomorrow_used = w.debt = 0.0
            config.QUOTA_MULT_FRI, config.QUOTA_MULT_SAT = 1.5, 0.0
            assert abs(w.limit - 5400) < 1e-6 and abs(w.remaining - 5400) < 1e-6
            assert w.tomorrow_limit == 0.0 and w.loan_left == 0.0, "明天 ×0 时借不到"
            config.QUOTA_MULT_SAT = 2.0
            assert abs(w.tomorrow_limit - 7200) < 1e-6
            w.date = "2026-10-18"                      # 周日 → 明天周一
            assert abs(w.limit - 3600) < 1e-6 and abs(w.tomorrow_limit - 3600) < 1e-6
            w.date = "2026-10-17"                      # 跨零点换到周六
            w.used = 3000.0
            assert abs(w.remaining - 4200) < 1e-6
            w.date = "not-a-date"                      # 坏数据退回今天
            assert w.limit >= 0
        finally:
            for k, v in saved_mult.items():
                setattr(config, k, v)

        # 联网状态随设备同步 / 关掉再打开时接着用 (MECHANICS 2.8)
        def fresh(me):
            g = NetGuard.__new__(NetGuard)
            g.dead, g.date = False, _dt.datetime.now().strftime('%Y-%m-%d')
            g.used = g.bonus = g.gift = g.tomorrow_used = g.debt = 0.0
            g.grant_left, g.grant_rate, g.pending, g.usage, g._session = 0.0, 1.0, None, [], None
            g._usage_dirty, g.live_id, g.live_owner, g.grant_end, g._grant_info = False, "", "", 0.0, {}
            g.state_file = os.path.join(tempfile.mkdtemp(), "net_state.json")
            g.usage_file = g.state_file + ".usage"
            g.log_file = g.state_file + ".log"
            g.peer_net = lambda: {"used": 0.0, "bonus": 0.0, "tomorrow_used": 0.0, "debt": 0.0, "gift": 0.0}
            g.quota_factor = g.cooldown_factor = lambda *_: 1.0
            g.my_id = lambda: me
            g.charge_ok = lambda owner: owner == me
            g.block = g.unblock = lambda *a, **k: None
            return g
        a, b = fresh("A"), fresh("B")
        ok, msg = a.request(20, "查资料")
        assert ok and a.live()["state"] == "pending" and a.live()["owner"] == "A"
        assert b.adopt(a.live()) is None and b.mode == "pending" and b.live() == a.live(), "申请同步过去, 内容一样"
        a.pending["at"] = b.pending["at"] = time.time() - 1
        a.tick(0.0); b.tick(0.0)
        assert a.online and b.online and a.live()["id"] == b.live()["id"] == a.live_id
        b.adopt(a.live())
        assert a.live() == b.live(), "结束时刻对齐"
        a.tick(60.0); b.tick(60.0)
        assert abs(a.used - 60) < 1e-6 and b.used == 0.0, "同一次联网只由发起的设备扣额度"
        b.end_early()
        assert a.adopt(b.live()) == "ended" and not a.online, "在别的设备上提前结束"
        # 关掉再打开: 文件里的联网状态
        c = fresh("C")
        ok, msg = c.request(10, "x", quick=True)
        c.tick(0.0)
        assert c.online
        c._save()
        d = fresh("C")
        d.state_file = c.state_file
        d._saved_live = None
        d._load()
        assert d._saved_live and d.adopt(d._saved_live) == "started" and d.online and abs(d.grant_left - 600) < 2
        return "net_guard selftest OK"
    finally:
        (config.DAILY_LIMIT, config.COOLDOWN,
         config.TICK_CLAMP, config.DEV_MODE) = saved
        config.NET_MODE = saved_mode
        if not had_dev and hasattr(config, "DEV_MODE"):
            del config.DEV_MODE
