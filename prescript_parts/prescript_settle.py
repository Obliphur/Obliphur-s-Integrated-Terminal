"""结算休息时间: 完成的指令换成休息时间加进计时器, 已经给过的不再给。

从 prescript.py 拆出来的 mixin; 依赖方向: prescript.py 导入它们, 它们从不导入 prescript.py.
"""

from datetime import datetime

from prescript_parts.prescript_common import (
    GRANTED_KEEP, _iso,
)


class SettleMixin:
    def settle_rewards(self, now: datetime = None) -> list:
        """打开指令页时调用: 把满足条件的休息时间加进计时器, 返回这次给出的代码."""
        now = (now or datetime.now()).replace(microsecond=0)
        self._check_daily_bonus(now)

        pending = self._data.get("pending") or []
        if not pending:
            if self._dirty:
                self.save()
            return []
        given = []
        for item in list(pending):
            if not isinstance(item, dict):
                continue
            try:
                seconds = max(0, int(item.get("seconds") or 0))
            except (TypeError, ValueError):
                seconds = 0
            if seconds > 0 and self.grant_rest_fn is not None:
                try:
                    self.grant_rest_fn(seconds, str(item.get("code") or ""))
                except Exception:
                    pass
            item["at"] = _iso(now)
            given.append(item)
            self._data.setdefault("granted", []).append(dict(item))
            self._data.setdefault("settled", {})[self._reward_id(item)] = dict(item)    # 其他设备看到就不再结算
        self._data["pending"] = []
        settled = self._data["settled"]
        for old in sorted(settled, key=lambda k: str(settled[k].get("at") or ""))[:-GRANTED_KEEP]:
            settled.pop(old, None)
        granted = self._data.get("granted") or []
        if len(granted) > GRANTED_KEEP:
            del granted[:-GRANTED_KEEP]
        self._dirty = True
        self.save()
        return given

    def granted_today(self, now: datetime = None) -> list:
        now = now or datetime.now()
        day = now.strftime("%Y-%m-%d")
        out = [g for g in (self._data.get("granted") or [])
               if isinstance(g, dict) and str(g.get("at") or "").startswith(day)]
        return out[-8:]

    def completed_today(self, now: datetime = None) -> float:
        now = now or datetime.now()
        try:
            return float(self._daily(now).get("completed_t", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0
