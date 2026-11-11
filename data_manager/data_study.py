"""学习时长: 旧数据迁移、study_log.json 的读写、当天 / 昨天 / 本月合计。

从 data_manager.py 拆出来的 mixin; 依赖方向: data_manager.py 导入它们, 它们从不导入 data_manager.py.
"""

import os
import json
import shutil
from datetime import datetime, timedelta
from typing import Dict
import securefile

from data_manager.data_common import (
    month_prefixes,
)


class StudyMixin:
    def _migrate_old_data(self):
        if not os.path.isdir(self.old_dir):
            return
        for name in ("study_log.json", "net_state.json", "net_requests.log"):
            src = os.path.join(self.old_dir, name)
            dst = os.path.join(self.data_dir, name)
            if os.path.exists(src) and not os.path.exists(dst):
                try:
                    shutil.copy2(src, dst)
                except OSError:
                    pass


    def load_study_data(self):
        if os.path.exists(self.study_file):
            try:
                with securefile.open(self.study_file, 'r', encoding='utf-8') as f:
                    self.study_data = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.study_data = {}
        else:
            self.study_data = {}
            self.save_study_data()

    def save_study_data(self):
        if self.resetting:
            return
        try:
            with securefile.open(self.study_file, 'w', encoding='utf-8') as f:
                json.dump(self.study_data, f, indent=2)
        except IOError:
            pass

    def _study_all(self, day: str) -> float:
        total = self.study_data.get(day, 0.0)
        for led in self.peer_ledgers() or []:
            try:
                total += float((led.get("study") or {}).get(day, 0.0))
            except (TypeError, ValueError, AttributeError):
                pass
        try:
            total += float(self.extra_study(day))
        except Exception:
            pass
        return total

    def get_today_hours(self) -> float:
        return self._study_all(self.today_date)

    def get_yesterday_hours(self) -> float:
        yesterday = (datetime.strptime(self.today_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        return self._study_all(yesterday)

    def add_study_session(self, wall_start: datetime, wall_end: datetime, duration_minutes: float):
        if duration_minutes <= 0:
            return

        start_date = wall_start.strftime('%Y-%m-%d')
        end_date = wall_end.strftime('%Y-%m-%d')

        if start_date == end_date:
            hours = duration_minutes / 60.0
            self.study_data[start_date] = self.study_data.get(start_date, 0.0) + hours
        else:
            midnight = wall_start.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            first_part = (midnight - wall_start).total_seconds() / 60.0
            second_part = (wall_end - midnight).total_seconds() / 60.0
            total = first_part + second_part
            if total > 0:
                hours_first = duration_minutes * (first_part / total) / 60.0
                hours_second = duration_minutes * (second_part / total) / 60.0
                self.study_data[start_date] = self.study_data.get(start_date, 0.0) + hours_first
                self.study_data[end_date] = self.study_data.get(end_date, 0.0) + hours_second
            else:
                hours = duration_minutes / 60.0
                self.study_data[start_date] = self.study_data.get(start_date, 0.0) + hours

        self.save_study_data()

    def prune_study_data(self):
        """只保留本月与上月的学习时长(日历可查看这两个月)."""
        keep = month_prefixes()
        stale = [k for k in self.study_data if k[:7] not in keep]
        if stale:
            for key in stale:
                del self.study_data[key]
            self.save_study_data()

    def month_study(self, prefix: str) -> Dict[str, float]:
        days = {k for k in self.study_data if k.startswith(prefix)}
        for led in self.peer_ledgers() or []:
            days |= {k for k in (led.get("study") or {}) if str(k).startswith(prefix)}
        try:
            days |= {k for k in self.extra_days() if str(k).startswith(prefix)}
        except Exception:
            pass
        return {k: self._study_all(k) for k in days}
