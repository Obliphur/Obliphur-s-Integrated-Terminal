"""联网额度: 当天的上限 / 倍率 / 冷静期, 以及各设备合计的剩余额度。

从 net_guard.py 拆出来的 mixin; 依赖方向: net_guard.py 导入它们, 它们从不导入 net_guard.py.
"""

from datetime import datetime, timedelta
import config


class QuotaMixin:
    def _day(self, offset: int = 0):
        """self.date 所指的那一天 (+offset 天). 跨零点由 tick() 里的 _rollover 推进 self.date."""
        try:
            d = datetime.strptime(self.date, '%Y-%m-%d')
        except (AttributeError, TypeError, ValueError):
            d = datetime.now()
        return d + timedelta(days=offset)

    @property
    def limit(self) -> float:
        """今天的每日额度 = DAILY_LIMIT × 今天(星期几)的倍率. 外出的 20% 不在这里, 见 today_total."""
        return config.daily_quota(self._day())

    @property
    def tomorrow_limit(self) -> float:
        """明天的每日额度, 按明天是星期几算 (紧急借贷借的是它)."""
        return config.daily_quota(self._day(1))

    def _quota_factor(self, day) -> float:
        try:
            return max(0.0, float(self.quota_factor(day)))
        except Exception:
            return 1.0

    def _cooldown_factor(self, now: datetime = None) -> float:
        try:
            return max(0.0, float(self.cooldown_factor(now or datetime.now())))
        except Exception:
            return 1.0

    @property
    def outing_factor(self) -> float:
        """外出的日子今天的总额度只能用几成 (1.0 = 没有限制)."""
        return self._quota_factor(self._day())

    @property
    def outing_cooldown(self) -> float:
        """现在的联网冷静期因为外出变成多少 (1.0 = 没有打折)."""
        return self._cooldown_factor()

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
    def full_total(self) -> float:
        """今天的总额度 = 每日额度 + 今天借到的 + 今天合约奖励的."""
        return self.limit + self.bonus_all + self.gift_all

    @property
    def today_total(self) -> float:
        """今天能用的额度: 有外出的日子只能用总额度的 20% (MECHANICS 21.4), 平时就是总额度."""
        return self.full_total * self._quota_factor(self._day())

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
