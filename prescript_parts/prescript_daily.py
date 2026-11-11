"""奖励与当天合计: 结算记录、当天 t 合计、凌晨的休息时间 (跟着记录走, 多台设备算出来一样)。

从 prescript.py 拆出来的 mixin; 依赖方向: prescript.py 导入它们, 它们从不导入 prescript.py.
"""

from datetime import datetime

from prescript_parts.prescript_common import (
    DAILY_BONUS_SECONDS, DAILY_T_TARGET, DAWN_HOUR, DAWN_SECONDS, GRANTED_KEEP, _iso, rest_code,
)


class DailyMixin:
    @staticmethod
    def _reward_id(item: dict) -> str:
        rid = str(item.get("id") or "")
        return rid or f"legacy-{item.get('at')}-{item.get('code')}"

    def _has_reward(self, rid: str) -> bool:
        return rid in (self._data.get("settled") or {}) or \
            any(isinstance(p, dict) and self._reward_id(p) == rid for p in self._data.get("pending") or [])

    def _add_reward(self, item: dict) -> bool:
        """排进待结算; 同一个 id (别的设备也完成了同一条) 已经有了就不重复."""
        item["id"] = self._reward_id(item)
        if self._has_reward(item["id"]):
            return False
        self._data.setdefault("pending", []).append(item)
        return True

    def _recompute_daily(self, now: datetime):
        """今天完成的 t 合计 / IV 完成过 / 20 分钟奖励给过没有: 从记录里算, 多台设备同步之后也一样."""
        daily = self._data.get("daily") or {}
        day = str(daily.get("date") or now.strftime("%Y-%m-%d"))
        total, iv = 0.0, False
        for rec in self._data.get("history") or []:
            if isinstance(rec, dict) and rec.get("result") == "completed" and str(rec.get("ended") or "").startswith(day):
                total += float(rec.get("t") or 0.0)
                iv = iv or rec.get("type") == "IV"
        daily["completed_t"], daily["iv_done"] = total, iv
        daily["bonus"] = self._has_reward(f"daily-{day}")

    def _check_daily_bonus(self, now: datetime):
        """今天的 t 合计到 10: 20 分钟当场排进待结算 (换日后打开页面也不会丢)."""
        daily = self._daily(now)
        day = str(daily.get("date"))
        if not daily.get("bonus") and float(daily.get("completed_t", 0.0) or 0.0) >= DAILY_T_TARGET - 1e-9:
            daily["bonus"] = True
            self._add_reward({"id": f"daily-{day}", "kind": "daily", "code": rest_code(20), "t": None,
                              "seconds": DAILY_BONUS_SECONDS, "at": _iso(now)})
            self._dirty = True

    def _check_dawn(self, now: datetime):
        """每天 4:00 之后的第一次判定: 当场给 20 分钟休息时间 (id 带日期, 同一天只给一次, 别的设备结算过就不再给).
        跟完成指令的奖励不同, 不排进待结算, 不用等打开指令页."""
        if now.hour < DAWN_HOUR:
            return
        item = {"id": f"dawn-{now.strftime('%Y-%m-%d')}", "kind": "dawn", "code": rest_code(DAWN_SECONDS // 60),
                "t": None, "seconds": DAWN_SECONDS, "at": _iso(now)}
        if self._has_reward(item["id"]):
            return
        if self.grant_rest_fn is not None:
            try:
                self.grant_rest_fn(DAWN_SECONDS, item["code"])
            except Exception:       # noqa: BLE001
                pass
        self._data.setdefault("granted", []).append(dict(item))
        self._data.setdefault("settled", {})[item["id"]] = dict(item)    # 其他设备看到就不再给
        settled = self._data["settled"]
        for old in sorted(settled, key=lambda k: str(settled[k].get("at") or ""))[:-GRANTED_KEEP]:
            settled.pop(old, None)
        granted = self._data["granted"]
        if len(granted) > GRANTED_KEEP:
            del granted[:-GRANTED_KEEP]
        self._dirty = True
        self.save()
