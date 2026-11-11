"""联网会话: 申请 / 快速访问 / 借贷 / 提前结束 / 取消, 额度的扣除与奖励。

从 net_guard.py 拆出来的 mixin; 依赖方向: net_guard.py 导入它们, 它们从不导入 net_guard.py.
"""

import time
import config
from config import fmt_hm


class SessionMixin:
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
                cooldown_rate: float = 1.0, cooldown_mult: float = 1.0):
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
            self._touch()
            self._log(f"QUICK    {minutes} min (cost {int(minutes * config.QUICK_MULTIPLIER)} min quota)"
                      f"  reason: {reason}")
            return True, ""

        if minutes * 60 > self.remaining:
            extra = ""
            if self.loan_left >= 60:
                extra = f" An emergency loan can add up to {fmt_hm(self.loan_left)}."
            return False, f"Only {fmt_hm(self.remaining)} left today.{extra}"
        rate = min(1.0, max(0.0, float(cooldown_rate) * self._cooldown_factor()))
        mult = max(1.0, float(cooldown_mult))          # 合约的侵蚀: 冷静期 ×2 / ×3
        self.pending = {
            "minutes": minutes,
            "at": time.time() + self.cooldown * rate * mult,
            "reason": reason,
            "quick": False,
        }
        self._touch()
        self._log(f"REQUEST  {minutes} min"
                  + (f" (cooldown {rate:g}x)" if rate < 1.0 else "")
                  + (f" (contract, cooldown x{mult:g})" if mult > 1.0 else "")
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
        self._touch()
        self._log(f"QUICK    upgrade {p['minutes']} min "
                  f"(cost {int(p['minutes'] * config.QUICK_MULTIPLIER)} min quota)")
        return True, ""

    def cancel(self):
        if self.pending:
            p = self.pending
            self._log(f"CANCEL   {p['minutes']} min {'quick ' if p.get('quick') else ''}request")
            self._touch()
        self.pending = None
        self._save()

    def end_early(self):
        if self.dev_mode:
            return
        if self.online:
            self._log(f"END      early, {int(self.grant_left)}s unused")
            self._touch()
        self._reset_grant()
        self.block()
        self._save()

    def _reset_grant(self):
        self.grant_left = 0.0
        self.grant_rate = 1.0
        self._grant_is_quick = False
        self.grant_end = 0.0
        self._grant_info = {}
        if self._session is not None:
            self._session = None
            self._save_usage()

    def shutdown(self):
        """关闭程序: 断网 (fail-closed), 联网状态留在文件里, 下次打开时接着用 (2.7)."""
        self._save()
        if not self.dev_mode:
            self.block(sync=True)
