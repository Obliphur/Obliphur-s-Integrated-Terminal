

import os
import sys
import json
import time
import queue
import threading
import subprocess
from datetime import datetime, timedelta

import config
from config import fmt_hm
from netproxy import ProxyController
from data_manager import month_prefixes


# 倍率类数值在 config.py (可在 Settings → 具体设置 中修改), 这里每次用时现读.


class NetGuard:
    gift = 0.0                        # 今天合约奖励的额度 (见 __init__)

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
        self.gift = 0.0               # 今天合约奖励的额度 (完成带"互联网娱乐的败退"的合约), 不用还
        self.tomorrow_used = 0.0
        self.debt = 0.0
        self.grant_left = 0.0
        self.grant_rate = 1.0
        self.pending = None
        # 设备同步: 其他设备今天的 {used, bonus, tomorrow_used, debt} 之和 (见 devsync.peer_net)
        self.peer_net = lambda: {"used": 0.0, "bonus": 0.0, "tomorrow_used": 0.0, "debt": 0.0, "gift": 0.0}
        # 设备同步: 其他设备的联网会话 (按理由统计用)
        self.peer_usage = lambda: []
        self.proxy = ProxyController(
            data_dir, mode_getter=lambda: ("blockall" if getattr(config, "NET_MODE", "") == "proxy_all"
                                           else getattr(config, "FILTER_MODE", "blacklist")),
            dev_getter=config.changes_instant)
        self._nic_off_by_fallback = False
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


    def _day(self, offset: int = 0):
        """self.date 所指的那一天 (+offset 天). 跨零点由 tick() 里的 _rollover 推进 self.date."""
        try:
            d = datetime.strptime(self.date, '%Y-%m-%d')
        except (AttributeError, TypeError, ValueError):
            d = datetime.now()
        return d + timedelta(days=offset)

    @property
    def limit(self) -> float:
        """今天的每日额度 = DAILY_LIMIT × 今天(星期几)的倍率."""
        return config.daily_quota(self._day())

    @property
    def tomorrow_limit(self) -> float:
        """明天的每日额度, 按明天是星期几算 (紧急借贷借的是它)."""
        return config.daily_quota(self._day(1))

    @property
    def quota_multiplier(self) -> float:
        return config.quota_multiplier(self._day())

    @property
    def cooldown(self) -> float:
        return float(config.COOLDOWN)

    @property
    def enforce_every(self) -> float:
        return max(5.0, float(config.ENFORCE_EVERY))

    @property
    def tick_clamp(self) -> float:
        return float(config.TICK_CLAMP)

    _ZERO = {"used": 0.0, "bonus": 0.0, "tomorrow_used": 0.0, "debt": 0.0, "gift": 0.0}

    def _pn(self) -> dict:
        fn = getattr(self, "peer_net", None)
        try:
            return fn() if fn else self._ZERO
        except Exception:
            return self._ZERO

    # 绑定了其他设备时, 额度是所有设备共用的: 下面的 *_all 是所有设备的合计
    @property
    def bonus_all(self) -> float:
        return self.bonus + self._pn()["bonus"]

    @property
    def gift_all(self) -> float:
        return self.gift + float(self._pn().get("gift", 0.0))

    @property
    def tomorrow_used_all(self) -> float:
        return self.tomorrow_used + self._pn()["tomorrow_used"]

    @property
    def debt_all(self) -> float:
        return self.debt + self._pn()["debt"]

    @property
    def used_all(self) -> float:
        return self.used + self._pn()["used"]

    @property
    def today_total(self) -> float:
        """今天的总额度 = 每日额度 + 今天借到的 + 今天合约奖励的."""
        return self.limit + self.bonus_all + self.gift_all

    @property
    def remaining(self) -> float:
        return max(0.0, self.today_total - self.used_all - self.debt_all)

    @property
    def tomorrow_remaining(self) -> float:
        return max(0.0, self.tomorrow_limit - self.tomorrow_used_all)

    @property
    def quick_left(self) -> float:
        """剩余额度按快速访问能换到多少联网时间."""
        return self.remaining / config.QUICK_MULTIPLIER

    @property
    def loan_left(self) -> float:
        """还能借多少今日额度."""
        return self.tomorrow_remaining / config.LOAN_RATE

    @property
    def grant_quick(self) -> bool:
        # 倍率可改 (甚至改成 1.0), 所以不能再用 grant_rate > 1 判断是不是快速访问.
        return bool(getattr(self, "_grant_is_quick", self.grant_rate > 1.0))

    @property
    def dev_mode(self) -> bool:
        return config.dev_mode_enabled()

    @property
    def net_mode(self) -> str:
        # proxy_all (代理-全屏蔽 (Proxy block-all)) 和 proxy 用同一个后端 (本地过滤代理), 只是不看名单
        return "proxy" if getattr(config, "NET_MODE", "adapter") in ("proxy", "proxy_all") else "adapter"

    @property
    def online(self) -> bool:
        if self.dev_mode:
            return True
        return self.grant_left > 0

    @property
    def mode(self) -> str:
        if self.dev_mode:
            return "dev"
        if self.online:
            return "online"
        if self.pending:
            return "pending"
        return "offline"

    @property
    def quota_used(self) -> float:
        return min(self.today_total, self.used_all + self.debt_all)


    def _load(self):
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                d = json.load(f)
        except Exception:
            return
        if not isinstance(d, dict):
            return
        saved = str(d.get("date") or "")

        def num(key):
            try:
                return max(0.0, float(d.get(key, 0.0)))
            except (TypeError, ValueError):
                return 0.0

        if saved == self.date:
            self.used = num("used")
            self.bonus = num("bonus")
            self.gift = num("gift")
            self.tomorrow_used = num("tomorrow_used")
            self.debt = num("debt")
        elif saved and saved < self.date:
            # 存档是之前某一天的: 程序关着跨过了零点.
            # 借贷要还的部分照样变成今天的欠款 —— 以前这里什么都不读,
            # 借完额度后关掉程序, 第二天再打开, 欠款就凭空消失了.
            # 中间隔了几天也一样: 欠款记到借贷之后第一次打开程序的那一天.
            self.debt = num("tomorrow_used")
            if self.debt > 0:
                self._save()
                self._log(f"LOAN     repay {int(self.debt // 60)} min today "
                          f"(borrowed on {saved})")

    def _save(self):
        if self.dead:
            return
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "date": self.date,
                    "used": int(self.used),
                    "bonus": int(self.bonus),
                    "gift": int(self.gift),
                    "tomorrow_used": int(self.tomorrow_used),
                    "debt": int(self.debt),
                }, f)
        except IOError:
            pass
        self._save_usage()
        self._last_save = time.monotonic()

    def _load_usage(self):
        try:
            with open(self.usage_file, 'r', encoding='utf-8') as f:
                d = json.load(f)
            recs = d.get("sessions", []) if isinstance(d, dict) else []
            self.usage = [r for r in recs if isinstance(r, dict) and "date" in r]
        except Exception:
            self.usage = []
        keep = month_prefixes()
        self.usage = [r for r in self.usage if str(r.get("date", ""))[:7] in keep]

    def _save_usage(self):
        if self.dead or not self._usage_dirty:
            return
        keep = month_prefixes()
        self.usage = [r for r in self.usage if str(r.get("date", ""))[:7] in keep]
        if self._session is not None and self._session not in self.usage:
            self._session = None
        try:
            tmp = self.usage_file + ".tmp"
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({"sessions": self.usage}, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.usage_file)
            self._usage_dirty = False
        except (IOError, OSError):
            pass

    def _open_session(self, reason: str, quick: bool, minutes: int, start: datetime = None):
        start = start or datetime.now()
        self._session = {
            "date": start.strftime('%Y-%m-%d'),
            "start": start.strftime('%H:%M'),
            "reason": reason,
            "quick": bool(quick),
            "minutes": int(minutes),
            "seconds": 0,
        }
        self.usage.append(self._session)
        self._usage_dirty = True

    def _count_online(self, dt: float):
        """实际联网的秒数(不乘快速访问倍数),跨零点自动拆到新的一天."""
        if dt <= 0 or self._session is None:
            return
        now = datetime.now()
        if self._session["date"] != now.strftime('%Y-%m-%d'):
            s = self._session
            self._open_session(s["reason"], s["quick"], s["minutes"],
                               start=now.replace(hour=0, minute=0, second=0, microsecond=0))
        self._session["seconds"] = round(self._session["seconds"] + dt, 1)
        self._usage_dirty = True

    def usage_by_reason(self, prefix: str):
        """某个月 [(理由, 实际联网秒数)]."""
        peer = getattr(self, "peer_usage", None)
        recs = list(self.usage) + list((peer() if peer else None) or [])
        return [(r.get("reason", ""), float(r.get("seconds", 0)))
                for r in recs if isinstance(r, dict) and str(r.get("date", "")).startswith(prefix)]

    def _log(self, text: str):
        if self.dead:
            return
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")
        except IOError:
            pass


    def _cmd(self, online: bool) -> str:
        verb = "Enable" if online else "Disable"
        return f"Get-NetAdapter -Physical | {verb}-NetAdapter -Confirm:$false"

    @staticmethod
    def _run(cmd):
        if callable(cmd):
            try:
                cmd()
            except Exception:
                pass
            return
        if sys.platform != "win32":
            return
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True,
                creationflags=0x08000000
            )
        except Exception:
            pass

    def _worker(self):

        while True:
            self._run(self.q.get())

    def _proxy_apply(self, online: bool):
        """proxy 模式的「联网 / 断网」:网卡保持开着,只切换过滤代理的放行策略.

        任何一步失败都不能留下「没有任何限制」的状态 —— 离线时退回禁用网卡."""
        try:
            self.proxy.start()
            self.proxy.set_online(online)
            if self._nic_off_by_fallback:
                self._nic_off_by_fallback = False
                self._run(self._cmd(True))
        except Exception as e:
            self._log(f"PROXY    failed ({e}); falling back to adapter cut-off")
            if not online:
                self._nic_off_by_fallback = True
                self._run(self._cmd(False))

    def _enter_proxy(self):
        """先让过滤代理就位,再放开网卡 —— 切换瞬间不留空窗."""
        self._proxy_apply(self.online)
        if not self._nic_off_by_fallback:
            self._run(self._cmd(True))

    def _backend_set(self, online: bool, sync: bool = False):
        job = (lambda: self._proxy_apply(online)) if self.net_mode == "proxy" \
            else self._cmd(online)
        if sync:
            self._run(job)
        else:
            self.q.put(job)

    def block(self, sync: bool = False):

        if self.dead or self.dev_mode:
            return
        self._backend_set(False, sync)

    def unblock(self):
        self._backend_set(True)

    def ensure_online(self):
        """彻底解除限制:网卡全开 + 过滤代理关闭并还原系统代理."""
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        self._nic_off_by_fallback = False
        self._run(self._cmd(True))
        self._run(lambda: self.proxy.stop(restore=True))

    def sync_mode(self):
        """设置里的 NET_MODE 变了(立即生效或冷静期到期)就切换后端;没变则什么都不做."""
        if self.dead or self._applied_mode == self.net_mode:
            return False
        self._applied_mode = self.net_mode
        if self.dev_mode:
            return True
        if self.net_mode == "proxy":
            self.q.put(self._enter_proxy)
        else:
            self.q.put(self._cmd(self.online))
            self.q.put(lambda: self.proxy.stop(restore=True))
        self._log(f"MODE     switched to {self.net_mode}")
        return True


    def can_borrow(self, minutes: float):
        add = max(0.0, float(minutes)) * 60.0
        if add <= 0:
            return False, "Minimum is 1 minute."
        if add > self.loan_left + 1e-6:
            if self.loan_left < 60:
                return False, "Tomorrow's quota is already fully borrowed."
            return False, (f"You can borrow at most {fmt_hm(self.loan_left)} today "
                           f"(tomorrow has {fmt_hm(self.tomorrow_remaining)} left, "
                           f"{config.LOAN_RATE:g}x per minute borrowed).")
        return True, ""

    def borrow(self, minutes: int, reason: str):
        """紧急借贷: 立即给今天加额度, 代价是明天扣两倍. 不联网, 联网仍需申请."""
        if self.dev_mode:
            return False, "Already online."
        minutes = int(minutes)
        ok, msg = self.can_borrow(minutes)
        if not ok:
            return False, msg
        add = minutes * 60.0
        self.bonus += add
        self.tomorrow_used += add * config.LOAN_RATE
        self._save()
        self._log(f"LOAN     +{minutes} min today, -{int(minutes * config.LOAN_RATE)} min tomorrow"
                  f"  reason: {reason}")
        return True, ""

    def add_gift(self, seconds: float, reason: str):
        """合约的奖励: 今天的额度多给 seconds 秒 (不用还, 零点清零)."""
        self.gift += max(0.0, float(seconds))
        self._save()
        self._log(f"GIFT     +{int(seconds // 60)} min today  reason: {reason}")

    def can_quick(self, minutes: float):
        need = max(0.0, float(minutes)) * 60.0
        if need <= 0:
            return False, "Minimum is 1 minute."
        if need * config.QUICK_MULTIPLIER > self.remaining + 1e-6:
            if self.quick_left < 60:
                return False, "Not enough quota left today for quick access."
            return False, (f"Quick access can cover at most {fmt_hm(self.quick_left)} "
                           f"({fmt_hm(self.remaining)} left today, {config.QUICK_MULTIPLIER:g}x cost).")
        return True, ""


    def request(self, minutes: int, reason: str, quick: bool = False,
                cooldown_rate: float = 1.0):
        if self.dev_mode:
            return False, "Already online."          # 开发者模式在界面上不显示
        if self.online:
            return False, "Already online."
        if self.pending:
            return False, "A request is already pending."
        minutes = int(minutes)
        if minutes < 1:
            return False, "Minimum is 1 minute."

        if quick:
            ok, msg = self.can_quick(minutes)
            if not ok:
                return False, msg
            self.pending = {"minutes": minutes, "at": time.time(),
                            "reason": reason, "quick": True}
            self._log(f"QUICK    {minutes} min (cost {int(minutes * config.QUICK_MULTIPLIER)} min quota)"
                      f"  reason: {reason}")
            return True, ""

        if minutes * 60 > self.remaining:
            extra = ""
            if self.loan_left >= 60:
                extra = f" An emergency loan can add up to {fmt_hm(self.loan_left)}."
            return False, f"Only {fmt_hm(self.remaining)} left today.{extra}"
        rate = min(1.0, max(0.0, float(cooldown_rate)))
        self.pending = {
            "minutes": minutes,
            "at": time.time() + self.cooldown * rate,
            "reason": reason,
            "quick": False,
        }
        self._log(f"REQUEST  {minutes} min"
                  + (f" (on break, cooldown {rate:g}x)" if rate < 1.0 else "")
                  + f"  reason: {reason}")
        return True, ""

    def convert_to_quick(self):
        if not self.pending:
            return False, "No pending request."
        if self.pending.get("quick"):
            return False, "This request is already quick access."
        p = self.pending
        ok, msg = self.can_quick(p["minutes"])
        if not ok:
            return False, msg
        self.pending = {"minutes": p["minutes"], "at": time.time(),
                        "reason": p.get("reason", ""), "quick": True}
        self._log(f"QUICK    upgrade {p['minutes']} min "
                  f"(cost {int(p['minutes'] * config.QUICK_MULTIPLIER)} min quota)")
        return True, ""

    def cancel(self):
        if self.pending:
            p = self.pending
            self._log(f"CANCEL   {p['minutes']} min {'quick ' if p.get('quick') else ''}request")
        self.pending = None

    def end_early(self):
        if self.dev_mode:
            return
        if self.online:
            self._log(f"END      early, {int(self.grant_left)}s unused")
        self._reset_grant()
        self.block()
        self._save()

    def _reset_grant(self):
        self.grant_left = 0.0
        self.grant_rate = 1.0
        self._grant_is_quick = False
        if self._session is not None:
            self._session = None
            self._save_usage()

    def shutdown(self):
        self.pending = None
        self._reset_grant()
        self._save()
        if not self.dev_mode:
            self.block(sync=True)

    def release(self):
        self.dead = True
        self.pending = None
        self._reset_grant()
        self.ensure_online()


    def release_restrictions(self):
        self.pending = None
        self._reset_grant()
        self._last_enforce = time.monotonic()
        self.ensure_online()
        self._log("DEV      network restrictions released")


    def _rollover(self, today: str):
        self.date = today
        self.debt = max(0.0, self.tomorrow_used)
        self.tomorrow_used = 0.0
        self.bonus = 0.0
        self.gift = 0.0
        self.used = 0.0
        self._save()


    def tick(self, dt: float):
        if self.dev_mode:

            if self.pending:
                self.pending = None
                self._log("DEV      pending request dropped (developer mode)")
            return None

        today = datetime.now().strftime('%Y-%m-%d')
        if today != self.date:
            self._rollover(today)

        event = None
        if self.pending and time.time() >= self.pending["at"]:
            p, self.pending = self.pending, None
            self._reset_grant()
            quick = bool(p.get("quick"))
            rate = max(1.0, float(config.QUICK_MULTIPLIER)) if quick else 1.0
            secs = min(p["minutes"] * 60.0, self.remaining / rate)
            if secs > 0:
                self.grant_left = secs
                self.grant_rate = rate
                self._grant_is_quick = quick
                self._open_session(p.get("reason", ""), quick, p["minutes"])
                self.unblock()
                self._save()
                self._log(f"START    {int(secs // 60)} min granted"
                          + (f" (quick, {rate:g}x)" if quick else ""))
                event = "started"

        now = time.monotonic()
        if self.online:
            dt = min(max(dt, 0.0), self.tick_clamp, self.grant_left)
            self.grant_left -= dt
            self._count_online(dt)

            self.used += dt * self.grant_rate

            if self.grant_left <= 1e-6:
                self._reset_grant()
                self.block()
                self._save()
                self._log("END      time is up")
                event = "ended"
            elif now - self._last_save >= 5:
                self._save()
            if self.net_mode == "proxy" and now - self._last_enforce >= self.enforce_every:
                self._last_enforce = now
                if self.q.empty():
                    self.q.put(lambda: self._proxy_apply(self.online))
        elif now - self._last_enforce >= self.enforce_every:
            self._last_enforce = now
            if self.q.empty():
                self.block()
        return event



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
        assert n.tick(100.0) is None and n.used == 0.0, "开发者模式不计时"
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
        return "net_guard selftest OK"
    finally:
        (config.DAILY_LIMIT, config.COOLDOWN,
         config.TICK_CLAMP, config.DEV_MODE) = saved
        config.NET_MODE = saved_mode
        if not had_dev and hasattr(config, "DEV_MODE"):
            del config.DEV_MODE


if __name__ == "__main__":
    print(_selftest())
