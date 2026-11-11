"""合约开始前积攒的休息时间、支援短缺, 以及词条对设置 / 组合 / 休息结算的影响。

从 contract.py 拆出来的 mixin; 依赖方向: contract.py 导入它们, 它们从不导入 contract.py.
"""

import random
from datetime import datetime, timedelta

from contract_parts.contract_rules import (
    NET_SHARE, WINDOWS, SUPPLY, HOUR_TERMS, COOLDOWN_X, COMBOS, combo_state, combo_marks, rest_factor_of,
    _iso, _parse, _fog_words,
)


class EffectsMixin:
    @property
    def pre(self) -> float:
        """开始时积攒的休息时间 (秒)."""
        return float(self.live.get("pre") or 0) if self.active else 0.0

    @property
    def pre_left(self) -> float:
        """还在积攒的休息时间里的那部分 (没用掉, 也没有拿进正在进行的休息)."""
        return float(self.live.get("pre_left") or 0) if self.active else 0.0

    @property
    def pre_used(self) -> float:
        return float(self.live.get("pre_used") or 0) if self.active else 0.0

    def pre_take(self, carry: float) -> float:
        """开始休息, 积攒的 carry 秒全部拿进这次休息: 其中合约开始前的有多少 (从 pre_left 里拿走)."""
        if not self.active:
            return 0.0
        x = min(max(0.0, float(carry)), self.pre_left)
        if x > 0:
            self.live["pre_left"] = round(self.pre_left - x, 1)
            self.save()
        return x

    def pre_settle(self, taken: float, back: float):
        """一次休息结束 (back = 暂停时还给积攒的那部分合约开始前的时间, 自然结束时 0)."""
        if not self.active:
            return
        taken, back = max(0.0, float(taken)), max(0.0, min(float(taken), float(back)))
        self.live["pre_left"] = round(self.pre_left + back, 1)
        self.live["pre_used"] = round(self.pre_used + taken - back, 1)
        self.save()

    @staticmethod
    def _supply_share(terms):
        shares = [SUPPLY[t] for t in terms or [] if t in SUPPLY]
        return min(shares) if shares else None

    def supply_term(self) -> str:
        return next((t for t in self.terms if t in SUPPLY), "")

    @property
    def supply_limit(self):
        """支援短缺: 合约开始前积攒的休息时间最多能用多少秒 (None = 不限)."""
        v = self.live.get("supply") if self.supply_term() else None
        return None if v is None else float(v)

    def supply_until(self, left: float, taken: float):
        """正在休息 (还剩 left 秒, 这次休息里有 taken 秒是合约开始前的): 再过多少秒就用超了 (None = 这次用不超)."""
        lim = self.supply_limit
        if lim is None or taken <= 0:
            return None
        cross = float(taken) - (lim - self.pre_used)       # 剩余时间降到这里时用超
        if cross <= 0:
            return None
        return max(0.0, float(left) - cross)

    def supply_watch(self) -> str:
        """支援短缺要检查的词条: 选着的, 或者已经失效的 (按失效时的上限)."""
        if self.supply_term():
            return self.supply_term()
        return self._lost_of(SUPPLY) if self.live.get("supply_lost") is not None else ""

    def supply_check(self, left: float, taken: float) -> str:
        """支援短缺: 合约开始前积攒的休息时间用超了 (正在休息时按已经用掉的算).
        已经失效的: 按失效时的上限, 还在继续用就返回它 (只响警报)."""
        used = self.pre_used + max(0.0, float(taken) - max(0.0, float(left)))
        tid = self.supply_term()
        if not tid:
            tid = self.supply_watch()
            if not tid:
                return ""
            w = self.__dict__.setdefault("_watch", {})
            last = w.get("supply_used", used)
            w["supply_used"] = used
            return tid if used > float(self.live["supply_lost"]) + 0.5 and used > last + 0.5 else ""
        lim = self.supply_limit
        if lim is None:
            return ""
        return tid if used > lim + 0.5 else ""

    # ---- 词条的效果 ----

    def overrides(self) -> dict:
        """合约中盖过设置的项 (config 里的值, 硬规则): 全频道阻塞干扰 → 断网方式换成
        代理-全屏蔽 (Proxy block-all), 合约结束后回到设置里的断网方式."""
        if not self.has("jam"):
            return {}
        return {"NET_MODE": "proxy_all"}

    def window_limit(self) -> int:
        return min((WINDOWS[t] for t in self.terms if t in WINDOWS), default=0)

    def net_share(self):
        """只能用剩余联网时间的几成 (None = 不限)."""
        shares = [NET_SHARE[t] for t in self.terms if t in NET_SHARE]
        return min(shares) if shares else None

    def fog(self) -> bool:
        return self.has("fog")

    def fog_words(self) -> list:
        """黑雾时整点记录的假内容: 开始合约时抽好的 FOG_SLOTS 组 [动词, 量词, 名词] 编号 (随合约同步, 两端一样).
        以前开始的合约没有这一项: 用合约 id 当种子补一份."""
        w = self.live.get("fog_words") if self.active else None
        if isinstance(w, list) and w and all(isinstance(x, list) and len(x) == 3 for x in w):
            return w
        return _fog_words(random.Random(str(self.live.get("id"))))

    def hoard_check(self) -> str:
        """禁止囤囤鼠!: 提前结束了休息 (软规则: 照样能结束, 算一次违反)."""
        return "hoard" if self.watching("hoard") else ""

    def cooldown_factor(self) -> float:
        """侵蚀: 网络申请的冷静期乘多少."""
        return max((COOLDOWN_X[t] for t in self.terms if t in COOLDOWN_X), default=1.0)

    def hour_forced(self) -> bool:
        """选了整点记录的词条: 整点记录关着时, 这次合约中照样开启. 未知变量启用过整点记录的词条: 这次合约中一直开启."""
        return any(t in HOUR_TERMS for t in self.terms) or (self.active and bool(self.live.get("hour_forced")))

    def fine_hour(self, slot_start: datetime) -> bool:
        """细化规范: 这一小时 (按开始时刻) 和进行中的合约重叠, 记录改为每 30 分钟一次.
        细化规范生效过的小时 (mark_fine 记下的) 合约中一直分成两格: 词条中途去掉 (未知变量到时移除) 时, 还没到的那半小时照样提示 / 填写 / 算超时.
        合约结束后全部恢复成一小时一格 (不留多出来的格子)."""
        if not self.active:
            return False
        if _iso(slot_start.replace(minute=0, second=0, microsecond=0)) in (self.live.get("fine_h") or []):
            return True
        if not self.has("fine"):
            return False
        a, b = self.start_at, self.end_at
        return bool(a and b and a < slot_start + timedelta(hours=1) and slot_start < b)

    def mark_fine(self, now: datetime) -> bool:
        """细化规范生效时记下这个钟点 (每秒调用): 之后词条没了, 这一小时也照样分成两格. 返回记了没有."""
        if not self.active or not self.has("fine"):
            return False
        key = _iso(now.replace(minute=0, second=0, microsecond=0))
        marks = list(self.live.get("fine_h") or [])
        if key in marks:
            return False
        self.live["fine_h"] = marks + [key]
        self.save()
        return True

    def rest_factor(self) -> float:
        return rest_factor_of(self.mine)

    def combos(self) -> list:
        """(组合, 状态) 现在的样子: 效果栏 (第 3 页) 照着画."""
        terms, orig = self.mine, (self.orig if self.active else [])
        return [(c, combo_state(c, terms, orig if self.active else None)) for c in COMBOS]

    def combo_marks(self) -> dict:
        """凑齐了的组合的成员词条 → 颜色 (界面上框住它们)."""
        return combo_marks(self.mine)

    def book_rest(self, seconds: float, end: datetime = None) -> float:
        """一段专注换来的休息时间 (秒, 已乘"休息时间倍率"). 返回给出的休息时间.
        合约中: 乘疲惫的倍率 (记进合约里给出的休息时间). 完成合约的奖励另外按等级给 (reward)."""
        seconds = max(0.0, float(seconds))
        end = end or datetime.now()
        if self.active and self.start_at and self.start_at <= end:
            now = seconds * self.rest_factor()
            self.live["rest"] = round(self.rest + now, 1)
            self.save()
            return now
        return seconds

    def book_rest_after(self, seconds: float, start: datetime) -> float:
        """完成之后才结束的专注, 开始于刚完成的合约里: 按那个合约的疲惫给 (未知变量选中的不算组合)."""
        h = self.last
        hs, he = _parse(h.get("start")), _parse(h.get("end"))
        if h.get("result") == "completed" and hs and he and hs <= start < he:
            rand = set(h.get("rand") or [])
            terms = [t for t in (h.get("terms") or []) if t not in rand]
            return seconds * rest_factor_of(terms)
        return seconds

    def focus_tag(self, start: datetime) -> str:
        """一段专注的标签, 按开始时刻 (MECHANICS 18.2): 开始于哪个合约里 (进行中的, 或已经结束的) 就是那个合约的 id
        ("合约"专注), 不在任何合约里是 "" ("普通"专注)."""
        if start is None:
            return ""
        if self.active and self.start_at and self.start_at <= start:
            return str(self.live.get("id"))
        for h in self.history:
            hs, he = _parse(h.get("start")), _parse(h.get("end"))
            if hs and he and hs <= start < he:
                return str(h.get("id"))
        return ""

    def completed(self, cid: str) -> bool:
        """这个 id 的合约已经顺利完成了."""
        return bool(cid) and any(str(h.get("id")) == str(cid) and h.get("result") == "completed" for h in self.history)

    def take_first_break(self) -> bool:
        """完成合约后的第一次休息: 用掉这个标记."""
        if self.active or not self.live.get("first_break"):
            return False
        self.live["first_break"] = False
        self.save()
        return True

    @staticmethod
    def first_break_rate(x: float) -> float:
        """完成合约后的第一次休息: 暂停时的结转比例 1 - (1 - x) / 2."""
        x = min(1.0, max(0.0, float(x)))
        return 1.0 - (1.0 - x) / 2.0
