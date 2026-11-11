"""自动记录的内容: 小时 / 半小时格子的自动填充 (分心 / 睡眠 / 行为模式), 睡眠的填写与取消。

从 data_manager.py 拆出来的 mixin; 依赖方向: data_manager.py 导入它们, 它们从不导入 data_manager.py.
"""

from datetime import datetime, timedelta
import config
import i18n
from config import AUTO_TEXT

from data_manager.data_common import (
    _merge_spans,
)


class AutoMixin:
    @property
    def auto_text(self) -> str:
        """非睡眠时间的自动记录内容 (默认 分心 / Distracted, 跟随界面语言)."""
        return i18n.auto_name(getattr(config, "AUTO_TEXT", AUTO_TEXT))

    @property
    def sleep_text(self) -> str:
        return i18n.auto_name("Sleeping")

    def sleep_seconds(self, start: datetime, end: datetime) -> float:
        """[start, end] 里有多少秒在睡眠 (本机和其他设备记下的睡眠, 加上正在进行的这一段)."""
        spans = []
        recs = list(self.focus_sessions)
        for led in self.peer_ledgers() or []:
            recs += [r for r in (led.get("focus") or []) if isinstance(r, dict)]
        for r in recs:
            if r.get("k") != "sleep":
                continue
            try:
                spans.append((datetime.strptime(r["s"], self._TS), datetime.strptime(r["e"], self._TS)))
            except (KeyError, TypeError, ValueError):
                continue
        try:
            live = self.sleep_live()
        except Exception:
            live = None
        if live is not None:
            spans.append((live, max(live, datetime.now())))
        return sum(max(0.0, (min(b, end) - max(a, start)).total_seconds()) for a, b in _merge_spans(spans))

    def auto_text_for_hour(self, hour_start: datetime) -> str:
        """这一小时的超时自动记录内容: 行为模式优先 (MECHANICS 21); 睡眠占了这一小时一半以上记成"睡眠";
        否则用全局的那一项 (默认 分心)."""
        try:
            text = self.hour_auto_text(hour_start)
        except Exception:
            text = ""
        if str(text):
            return str(text)
        try:
            # 正在睡眠: 睡眠占着的每一小时 (哪怕才睡了几分钟) 马上记成"睡眠"并锁定; 起床后睡不满半小时的那几格退回去 (unfill_short_sleep)
            live = self.sleep_live()
            if live is not None and live < hour_start + timedelta(hours=1) and hour_start <= datetime.now():
                return self.sleep_text
            if self.sleep_seconds(hour_start, hour_start + timedelta(hours=1)) >= 1800:
                return self.sleep_text
        except Exception:
            pass
        return self.auto_text

    def localize_auto_texts(self) -> bool:
        """切换语言时: 整点记录里 睡眠 / Sleeping, 分心 / Distracted 这些默认的内容换成现在的语言, 同一项合并到一起."""
        changed = False
        for day in self.hour_days.values():
            for h, text in list(day.items()):
                new = i18n.auto_names_in(text)
                if new != text:
                    day[h] = new
                    changed = True
        for day in self.hour_halves.values():
            for h, pair in list(day.items()):
                if isinstance(pair, list):
                    new = [i18n.auto_names_in(t) for t in pair]
                    if new != pair:
                        day[h] = new
                        changed = True
        if changed:
            self.save_hour_log()
        return changed

    def relabel_sleep(self, start: datetime, end: datetime) -> bool:
        """起床后: [start, end] 里已经自动记成非睡眠内容的小时 (睡眠记录晚到时) 改成"睡眠"."""
        changed = False
        h = start.replace(minute=0, second=0, microsecond=0)
        while h < end:
            ds, key = h.strftime("%Y-%m-%d"), str(h.hour)
            cur = self.hour_days.get(ds, {}).get(key)
            if cur and cur == self.auto_text and self.sleep_seconds(h, h + timedelta(hours=1)) >= 1800:
                self.hour_days[ds][key] = self.sleep_text
                changed = True
            h += timedelta(hours=1)
        if changed:
            self.save_hour_log()
        return changed

    def fill_sleep_now(self, now: datetime = None) -> bool:
        """正在睡眠 (睡眠开始之后每一小时, 直到现在这一小时): 还空着的整点记录格马上填上"睡眠" (填上之后就是固定的自动内容, 不能改).
        整点记录没开着不填. 返回有没有填."""
        if not self.hour_enabled:
            return False
        try:
            live = self.sleep_live()
        except Exception:
            live = None
        if live is None:
            return False
        now = now or datetime.now()
        changed = False
        h = live.replace(minute=0, second=0, microsecond=0)
        while h <= now:
            ds, key = h.strftime("%Y-%m-%d"), str(h.hour)
            if not self.get_hour_text(ds, h.hour) and not self.get_half_text(ds, h.hour, 1):
                self.hour_days.setdefault(ds, {})[key] = self.sleep_text
                changed = True
            h += timedelta(hours=1)
        if changed:
            self.save_hour_log()
        return changed

    def unfill_short_sleep(self, start: datetime, end: datetime) -> bool:
        """起床后: 这段睡眠 [start, end] 里, 睡眠不满半小时的那几小时 (睡着时马上记成"睡眠"的) 退回空白, 照常补记."""
        changed = False
        h = start.replace(minute=0, second=0, microsecond=0)
        while h < end:
            ds, key = h.strftime("%Y-%m-%d"), str(h.hour)
            cur = self.hour_days.get(ds, {}).get(key)
            if cur and i18n.auto_names_in(cur) == i18n.auto_names_in(self.sleep_text) \
                    and self.sleep_seconds(h, h + timedelta(hours=1)) < 1800 and h + timedelta(hours=1) <= end + timedelta(hours=1):
                self.hour_days[ds].pop(key, None)
                changed = True
            h += timedelta(hours=1)
        if changed:
            self.save_hour_log()
        return changed

    def auto_text_for_key(self, key: str) -> str:
        """整点记录的键 ("日期/小时" 或 "日期/小时/1") → 这一小时的自动记录内容."""
        parts = str(key or "").split("/")
        try:
            day = datetime.strptime(parts[0], "%Y-%m-%d")
            h = int(parts[1])
            return self.auto_text_for_hour(day.replace(hour=h, minute=0, second=0, microsecond=0))
        except (ValueError, IndexError, TypeError):
            return self.auto_text

    def is_fixed_auto(self, text, key: str) -> bool:
        """这一格的内容是不是固定的自动填充 (睡眠 / 上学 / 工作 / 外出 / 休息): 是就不能改.
        只有「超时自动记录内容」(默认 Distracted) 自动填的格子可以改."""
        if not str(text or ""):
            return False
        auto = self.auto_text_for_key(key)
        if i18n.auto_names_in(auto) == i18n.auto_names_in(self.auto_text):
            return False
        return i18n.auto_names_in(text) == i18n.auto_names_in(auto)

    def is_auto_text(self, text, key: str) -> bool:
        auto = self.auto_text_for_key(key)
        return str(text or "") in ("", auto) or i18n.auto_names_in(text) == i18n.auto_names_in(auto)
