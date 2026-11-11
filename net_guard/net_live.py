"""联网状态 (申请中 / 联网中): live / adopt, 随设备同步, 关掉程序再打开时接着用 (MECHANICS 2.8)。

从 net_guard.py 拆出来的 mixin; 依赖方向: net_guard.py 导入它们, 它们从不导入 net_guard.py.
"""

import time


class LiveMixin:
    # ---- 联网状态: 随设备同步, 关掉程序再打开时接着用 (MECHANICS 2.8) ----

    def _touch(self):
        """本机改动了联网状态: 换一个编号, 本机成为发起的设备."""
        self.live_id = str(int(time.time() * 1000))
        self.live_owner = self.my_id() or ""

    def live(self) -> dict:
        """此刻的联网状态 (只用墙上时间, 没有改动时内容不变; 只用整数: at 毫秒, end 秒, rate ×100, 两端写法一样)."""
        base = {"id": getattr(self, "live_id", ""), "owner": getattr(self, "live_owner", "")}
        if getattr(self, "grant_left", 0) > 0 and getattr(self, "grant_end", 0) and not self.dev_mode:
            return dict(base, state="online", end=int(self.grant_end), rate=int(round(self.grant_rate * 100)),
                        quick=bool(getattr(self, "_grant_is_quick", False)),
                        reason=str(self._grant_info.get("reason", "")),
                        minutes=int(self._grant_info.get("minutes", 0)))
        if getattr(self, "pending", None):
            p = self.pending
            return dict(base, state="pending", at=int(round(float(p["at"]) * 1000)), minutes=int(p["minutes"]),
                        reason=str(p.get("reason", "")), quick=bool(p.get("quick")))
        return dict(base, state="offline")

    def _grant(self, end: float, rate: float, quick: bool, reason: str, minutes: int, left: float = None) -> bool:
        """联网到 end (墙上时间) 为止; 已经过了就不联网. left: 本机刚批下来的秒数 (不用按 end 再算)."""
        self._reset_grant()
        left = float(end) - time.time() if left is None else float(left)
        if left <= 0:
            return False
        self.grant_left = left
        self.grant_end = float(end)
        self.grant_rate = float(rate)
        self._grant_is_quick = bool(quick)
        self._grant_info = {"reason": reason, "minutes": int(minutes)}
        if self.charge_ok(self.live_owner):
            self._open_session(reason, quick, minutes)
        self.unblock()
        return True

    def adopt(self, live: dict):
        """别的设备上 (或关闭前) 的联网状态: 照着切换. 返回 "started" / "ended" / None."""
        if not isinstance(live, dict) or self.dead or self.dev_mode:
            return None
        lid = str(live.get("id") or "")
        if not lid:
            return None
        if lid == self.live_id:
            # 同一次联网, 各设备算出的结束时刻差一两秒: 对齐到同步来的, 免得来回改
            if live.get("state") == "online" and self.online and self.grant_end:
                try:
                    end = float(live.get("end") or 0)
                except (TypeError, ValueError):
                    return None
                if end and end != self.grant_end:
                    self.grant_left = max(0.0, self.grant_left + end - self.grant_end)
                    self.grant_end = end
            return None
        was = self.online
        self.live_id, self.live_owner = lid, str(live.get("owner") or "")
        st = live.get("state")
        event = None
        try:
            if st == "online":
                self.pending = None
                if self._grant(float(live.get("end") or 0), int(live.get("rate") or 100) / 100.0, bool(live.get("quick")),
                               str(live.get("reason") or ""), int(live.get("minutes") or 0)):
                    event = None if was else "started"
                elif was:
                    self.block()
                    event = "ended"
            else:
                if was:
                    self._reset_grant()
                    self.block()
                    event = "ended"
                self.pending = None
                if st == "pending":
                    self.pending = {"minutes": int(live.get("minutes") or 0), "at": int(live.get("at") or 0) / 1000.0,
                                    "reason": str(live.get("reason") or ""), "quick": bool(live.get("quick"))}
        except (TypeError, ValueError):
            return None
        self._log(f"SYNC     net state {st} from {self.live_owner or '?'}")
        self._save()
        return event

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

    def dev_closed(self):
        """开发者模式关了 (手动 / 用完 / 关程序): 把这一段记成的联网收尾."""
        if self._session is not None:
            self._session = None
            self._save_usage()
        self._save()


    def _rollover(self, today: str):
        self.date = today
        self.debt = max(0.0, self.tomorrow_used)
        self.tomorrow_used = 0.0
        self.bonus = 0.0
        self.gift = 0.0
        self.used = 0.0
        self._save()
