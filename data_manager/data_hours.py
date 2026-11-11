"""整点记录: hour_log.json、细化规范的半小时格、待填写的格子 (MECHANICS 8 / 18.4)。

从 data_manager.py 拆出来的 mixin; 依赖方向: data_manager.py 导入它们, 它们从不导入 data_manager.py.
"""

import json
from datetime import datetime, timedelta
from typing import Optional, Dict
from typing import Optional, Dict
import config
import securefile

from data_manager.data_common import (
    month_prefixes,
    half_fill,
    tally_items,
)


class HoursMixin:
    def load_hour_log(self):
        try:
            with securefile.open(self.hour_file, 'r', encoding='utf-8') as f:
                d = json.load(f)
            self.hour_days = d.get("days", {})
            halves = d.get("halves")
            self.hour_halves = halves if isinstance(halves, dict) else {}
            cur = d.get("cursor")
            self.hour_cursor = datetime.fromisoformat(cur) if cur else None
        except Exception:
            self.hour_days, self.hour_cursor, self.hour_halves = {}, None, {}
        self._prune_hours()

    def _prune_hours(self):
        keep = month_prefixes()
        self.hour_days = {k: v for k, v in self.hour_days.items() if k[:7] in keep}
        self.hour_halves = {k: v for k, v in self.hour_halves.items() if k[:7] in keep and isinstance(v, dict)}

    def hour_activity_seconds(self, prefix: str) -> Dict[str, float]:
        """某个月每项活动的总时长(秒).一小时有多项活动时均分."""
        return tally_items(
            (text, 3600.0)
            for ds, day in self.hour_days.items() if ds.startswith(prefix)
            for text in day.values()
        )

    def save_hour_log(self):
        if self.resetting:
            return
        self._prune_hours()
        try:
            with securefile.open(self.hour_file, 'w', encoding='utf-8') as f:
                json.dump({

                    "enabled": bool(self.hour_enabled),
                    "cursor": self.hour_cursor.isoformat() if self.hour_cursor else None,
                    "days": self.hour_days,
                    "halves": self.hour_halves
                }, f, ensure_ascii=False, indent=1)
        except IOError:
            pass

    def apply_hour_enabled(self, enabled: bool):
        enabled = bool(enabled)
        if enabled == self.hour_enabled:
            return
        self.hour_enabled = enabled
        if enabled:

            self.hour_cursor = datetime.now().replace(minute=0, second=0, microsecond=0)
        else:
            self.prompt_slot = None
        self.save_hour_log()

    def set_hour_enabled(self, enabled: bool):
        self.apply_hour_enabled(enabled)

    def get_hour_text(self, date_str: str, hour: int) -> str:
        return self.hour_days.get(date_str, {}).get(str(hour), "")

    def set_hour_text(self, date_str: str, hour: int, text: str):
        self.hour_halves.get(date_str, {}).pop(str(hour), None)     # 整小时一起改了: 不再分两格
        day = self.hour_days.setdefault(date_str, {})
        if text:
            day[str(hour)] = text
        else:
            day.pop(str(hour), None)
        self.save_hour_log()

    # ---- 细化规范: 半小时一格 ----

    def get_half_text(self, date_str: str, hour: int, i: int) -> str:
        """i = 0 前半小时, 1 后半小时. 还没分开填过的小时: 前半显示整小时的内容."""
        pair = self.hour_halves.get(date_str, {}).get(str(hour))
        if isinstance(pair, list) and len(pair) == 2:
            return str(pair[i] or "")
        return self.get_hour_text(date_str, hour) if i == 0 else ""

    def set_half_text(self, date_str: str, hour: int, i: int, text: str, save: bool = True):
        pair = [self.get_half_text(date_str, hour, 0), self.get_half_text(date_str, hour, 1)]
        pair[i] = text
        day = self.hour_halves.setdefault(date_str, {})
        if any(pair):
            day[str(hour)] = pair
        else:
            day.pop(str(hour), None)
        self._join_halves(date_str, hour)
        if save:
            self.save_hour_log()

    def _join_halves(self, date_str: str, hour: int):
        """整小时的内容 = 两个半小时用 "/" 连起来 (一样的只写一次)."""
        pair = self.hour_halves.get(date_str, {}).get(str(hour)) or ["", ""]
        parts = [p for p in pair if p]
        text = "/".join(dict.fromkeys(parts))
        day = self.hour_days.setdefault(date_str, {})
        if text:
            day[str(hour)] = text
        else:
            day.pop(str(hour), None)

    def unit_text(self, key: str) -> str:
        """同步 / 合约用的键: "日期/小时" 整小时, "日期/小时/1" "日期/小时/2" 两个半小时."""
        parts = key.split("/")
        try:
            if len(parts) == 3:
                return self.get_half_text(parts[0], int(parts[1]), int(parts[2]) - 1)
            return self.get_hour_text(parts[0], int(parts[1]))
        except (ValueError, IndexError):
            return ""

    def finalize_halves(self, units, now: datetime = None):
        """细化规范的半小时过了补填时限还空着: 记为超时自动记录的内容. units: [(键, 开始, 结束)]."""
        if not self.hour_enabled:
            return
        now = now or datetime.now()
        changed = False
        for key, _a, b in units:
            parts = key.split("/")
            if len(parts) != 3 or b + half_fill() > now:
                continue
            ds, h, i = parts[0], int(parts[1]), int(parts[2]) - 1
            if not self.get_half_text(ds, h, i):
                try:
                    hour0 = datetime.strptime(ds, "%Y-%m-%d").replace(hour=h)
                except ValueError:
                    continue
                self.set_half_text(ds, h, i, self.auto_text_for_hour(hour0), save=False)
                changed = True
        if changed:
            self.save_hour_log()

    def finalize_hours(self):
        now = datetime.now()
        floor = now.replace(minute=0, second=0, microsecond=0)
        today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if self.hour_cursor is None:
            self.hour_cursor = floor
            self.save_hour_log()
            return
        # 00:00 那一格(昨天 23:00–24:00)显示在今天最上方,所以从昨天 23:00 起补记.
        self.hour_cursor = max(self.hour_cursor, today0 - timedelta(hours=1))
        if not self.hour_enabled:

            if self.hour_cursor < floor:
                self.hour_cursor = floor
                self.save_hour_log()
            return
        changed = False
        while self.hour_cursor + timedelta(hours=1) + config.FILL_WINDOW <= now:
            ds, h = self.hour_cursor.strftime('%Y-%m-%d'), self.hour_cursor.hour

            if not self.get_hour_text(ds, h):
                self.hour_days.setdefault(ds, {})[str(h)] = self.auto_text_for_hour(self.hour_cursor)
            self.hour_cursor += timedelta(hours=1)
            changed = True
        if changed:
            self.save_hour_log()

    def awaiting_slot(self) -> Optional[datetime]:
        if not self.hour_enabled:
            return None
        s = self.prompt_slot
        if s is None:
            return None
        if self.prompt_len == 30:
            text = self.get_half_text(s.strftime('%Y-%m-%d'), s.hour, 1 if s.minute >= 30 else 0)
        else:
            text = self.get_hour_text(s.strftime('%Y-%m-%d'), s.hour)
        fill = half_fill() if self.prompt_len == 30 else config.FILL_WINDOW
        if datetime.now() >= s + timedelta(minutes=self.prompt_len) + fill or text:
            self.prompt_slot = None
            return None
        return s
