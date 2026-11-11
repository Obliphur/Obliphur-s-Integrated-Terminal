"""整点记录: 记录格 / 补填时限 / 超时违反 / 休息中的格子 / 记录封锁 (MECHANICS 18.4)。

从 contract.py 拆出来的 mixin; 依赖方向: contract.py 导入它们, 它们从不导入 contract.py.
"""

from datetime import datetime, timedelta

from contract.contract_rules import (
    _parse,
)


class HoursMixin:
    def periods(self, now: datetime = None, term: str = ""):
        """各个合约 (结束了的和进行中的) 的 (开始, 结束); term: 只要选了这个词条的."""
        out = []
        for h in self.history:
            a, b = _parse(h.get("start")), _parse(h.get("end"))
            if a and b and (not term or term in (h.get("orig") or h.get("terms") or [])):
                out.append((a, b))
        if self.active and self.start_at and (not term or term in self.orig):
            out.append((self.start_at, now or datetime.now()))
        return out

    def hour_units(self, now: datetime, ahead: bool = False):
        """进行中的合约里已经结束的记录格: [(键, 开始, 结束)].
        键是 "日期/小时", 细化规范的两个半小时是 "日期/小时/1" "日期/小时/2".
        ahead: 还没结束的也要 (到合约结束为止)."""
        if not self.active or not self.start_at:
            return []
        a, b = self.start_at, (self.end_at or now) if ahead else min(now, self.end_at or now)
        out = []
        h = a.replace(minute=0, second=0, microsecond=0)
        while h < b:
            key = f"{h:%Y-%m-%d}/{h.hour}"
            if self.fine_hour(h):
                parts = [(key + "/1", h, h + timedelta(minutes=30)),
                         (key + "/2", h + timedelta(minutes=30), h + timedelta(hours=1))]
            else:
                parts = [(key, h, h + timedelta(hours=1))]
            out += [p for p in parts if p[1] < b and a < p[2] and (ahead or p[2] <= now)]
            h += timedelta(hours=1)
        return out

    def hour_misses(self, now: datetime, fill: timedelta, text_of, auto) -> list:
        """合约中过了填写时限还没填写的记录格 (空着, 或者是超时自动记录的内容): 每格只算一次违反.
        返回新算的键 (只由做自动结束的设备调用).
        auto: 超时自动记录的内容, 或者 (键) → 那一小时的自动记录内容的函数 (行为模式)."""
        if not self.active:
            return []
        old = list(self.live.get("hl") or [])
        seen = set(old)
        new = []
        rest = set(self.live.get("rest_h") or [])
        for key, a, b in self.hour_units(now):
            if key in seen or b + cell_fill(a, b, fill) > now:
                continue
            seen.add(key)
            if key not in rest and self._is_auto(text_of(key) or "", key, auto):   # 休息中没填写的不算违反
                new.append(key)
        if len(seen) != len(old):
            self.live["hl"] = sorted(seen)
            self.save()
        return new

    @staticmethod
    def _is_auto(text: str, key: str, auto) -> bool:
        try:
            wanted = str(auto(key)) if callable(auto) else str(auto or "")
        except Exception:
            wanted = "" if callable(auto) else str(auto or "")
        return str(text or "") in ("", wanted)

    def mark_rest_cells(self, now: datetime, fill: timedelta, text_of, auto) -> bool:
        """休息中 (每秒调用): 合约里还在补填时限里, 还没填写 (空着 / 超时自动记录的内容) 的记录格记下来 (rest_h, 随同步只增不删).
        这些格子之后超时不算违反, 黑雾不盖住, 一直能填 (记录封锁也不锁), 自己填写了以后才照常 (MECHANICS 18.4). 返回记下了新的没有."""
        if not self.active:
            return False
        have = set(self.live.get("rest_h") or [])
        new = {key for key, a, b in self.hour_units(now)
               if key not in have and now < b + cell_fill(a, b, fill) and self._is_auto(text_of(key) or "", key, auto)}
        if not new:
            return False
        self.live["rest_h"] = sorted(have | new)
        self.save()
        return True

    @staticmethod
    def _key_span(key: str):
        """记录格的键 → (开始, 结束)."""
        parts = str(key).split("/")
        try:
            h = datetime.strptime(parts[0], "%Y-%m-%d").replace(hour=int(parts[1]))
        except (ValueError, IndexError):
            return None
        if len(parts) == 3:
            a = h + timedelta(minutes=30 if parts[2] == "2" else 0)
            return a, a + timedelta(minutes=30)
        return h, h + timedelta(hours=1)

    def rest_cell(self, start: datetime, end: datetime, text: str, auto) -> bool:
        """休息中没填写的记录格 (mark_rest_cells), 现在还没自己填写: 黑雾不盖住, 能填."""
        if not self.active:
            return False
        key = f"{start:%Y-%m-%d}/{start.hour}"
        if not self._is_auto(str(text or ""), key, auto):
            return False
        for k in self.live.get("rest_h") or []:
            span = self._key_span(k)
            if span and span[0] < end and start < span[1]:
                return True
        return False

    def hour_sealed(self, start: datetime, end: datetime, now: datetime = None) -> bool:
        """这个记录格在记录封锁的范围里 (填写后就不能再改, 界面上提交前先确认):
        进行中、选了记录封锁的合约里, 和合约重叠的格子. 合约结束后 / 没选记录封锁时都不算."""
        now = now or datetime.now()
        if start >= now:
            return False
        return bool(self.active and self.start_at and ("seal" in self.orig or self.has("seal")) and self.start_at < end)

    def hour_locked(self, start: datetime, end: datetime, text: str, now: datetime, fill: timedelta,
                    auto, key: str = "") -> bool:
        """记录封锁: 进行中的合约选了这个词条时, 和合约重叠的记录格已经填写了的 (自己填的, 或者超时自动记录的) 就不能再改,
        没填的照常能填 (这条只在合约中). 封锁了的格子 (seals, 都是填写了的) 合约结束后照样锁着."""
        if self.rest_cell(start, end, text, auto):
            return False                     # 休息中没填写的格子: 自己填写之前一直能填
        if any(a < end and start < b for a, b in self.locks()):
            return True
        if not str(text or "").strip():
            return False
        return self.hour_sealed(start, end, now)


def cell_fill(start: datetime, end: datetime, fill: timedelta) -> timedelta:
    """记录格的补填时限: 细化规范的半小时格只有 30 min (MECHANICS 18.4)."""
    return min(fill, timedelta(minutes=30)) if end - start <= timedelta(minutes=30) else fill
