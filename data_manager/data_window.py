"""窗口使用时间: 每隔几秒看一次前台程序, 把这几秒记给它 (window_usage.json)。

从 data_manager.py 拆出来的 mixin; 依赖方向: data_manager.py 导入它们, 它们从不导入 data_manager.py.
"""

import os
import json
from datetime import datetime, timedelta
from typing import Dict
import securefile

from data_manager.data_common import (
    month_prefixes,
    _sum_days,
    combine_window,
)


class WindowMixin:
    # ---- 窗口使用时间: 每隔几秒看一次前台程序, 把这几秒记给它 ----

    def load_window_usage(self):
        try:
            with securefile.open(self.window_file, 'r', encoding='utf-8') as f:
                d = json.load(f)
            days = d.get("days", {}) if isinstance(d, dict) else {}
        except Exception:
            d, days = {}, {}             # 还没有这个文件 (新安装 / 重置后): 以前这里 d 没有定义, 启动就崩溃
        runs = d.get("runs", {}) if isinstance(d, dict) else {}
        self.window_runs = {str(k): [list(r) for r in v if isinstance(r, list) and len(r) == 3]
                            for k, v in (runs.items() if isinstance(runs, dict) else ()) if isinstance(v, list)}
        out = {}
        for ds, day in (days.items() if isinstance(days, dict) else ()):
            if not isinstance(day, dict):
                continue
            clean = {}
            for name, sec in day.items():
                try:
                    sec = float(sec)
                except (TypeError, ValueError):
                    continue
                if sec > 0:
                    clean[str(name)] = sec
            if clean:
                out[str(ds)] = clean
        self.window_days = out
        self._prune_windows()

    def _prune_windows(self):
        keep = month_prefixes()
        self.window_days = {k: v for k, v in self.window_days.items() if k[:7] in keep}
        self.window_runs = {k: v for k, v in self.window_runs.items() if k[:7] in keep}

    def add_window_time(self, name: str, seconds: float, end: datetime = None):
        """把截止到 end 的这 seconds 秒记给程序 name; 跨过零点的部分记到前一天."""
        if seconds <= 0:
            return
        end = end or datetime.now()
        start = end - timedelta(seconds=seconds)
        while start < end:
            day0 = start.replace(hour=0, minute=0, second=0, microsecond=0)
            cut = min(end, day0 + timedelta(days=1))
            day = self.window_days.setdefault(start.strftime('%Y-%m-%d'), {})
            day[name] = day.get(name, 0.0) + (cut - start).total_seconds()
            start = cut
        self._note_run(name, end - timedelta(seconds=seconds / 2))
        self._window_dirty = True

    def _note_run(self, name: str, when: datetime):
        """把 when 所在的这一分钟记给 name (同一分钟里换了程序, 记给最后一个)."""
        ds = when.strftime('%Y-%m-%d')
        m = when.hour * 60 + when.minute
        runs = self.window_runs.setdefault(ds, [])
        if runs:
            s, e, app = runs[-1]
            if app == name and m <= e:
                runs[-1][1] = max(e, m + 1)
                return
            if m < e - 1:
                return                       # 时间往回走了 (改过系统时间): 不记
            if m == e - 1:                   # 这一分钟刚记给了别的程序: 改记给最新的
                if s >= e - 1:
                    runs.pop()
                else:
                    runs[-1][1] = e - 1
                if runs and runs[-1][2] == name and runs[-1][1] == m:
                    runs[-1][1] = m + 1
                    return
        runs.append([m, m + 1, name])

    def window_devices(self):
        """统计页可选的设备: [(键, 名字)]. 没有绑定其他设备时只有本机."""
        peers = self.peer_ledgers_by_id() or {}
        out = [("self", None)]
        out += [(k, str(v.get("name") or k)) for k, v in peers.items()]
        return out

    def window_seconds(self, prefix: str, device: str = "all") -> Dict[str, float]:
        """某个月每个程序的前台时间 (秒); 键 "" 是空白.
        device: "self" 本机 / 某台设备的 id / "all" 全部设备 (同一分钟几台设备都在用时以母端为准)."""
        peers = self.peer_ledgers_by_id() or {}
        if device == "self" or not peers:
            return _sum_days(self.window_days, prefix)
        if device in peers:
            return _sum_days(peers[device].get("window") or {}, prefix)
        mine = self.my_id()
        mother = self.mother_id()
        ids = sorted([mine] + list(peers), key=lambda i: 0 if i == mother else 1)
        sources = [{"seconds": self.window_days, "runs": self.window_runs} if i == mine else
                   {"seconds": peers[i].get("window") or {}, "runs": peers[i].get("window_runs") or {}}
                   for i in ids]
        return combine_window(sources, prefix)

    def save_window_usage(self, force: bool = False):
        if self.resetting or not (self._window_dirty or force):
            return
        self._prune_windows()
        try:
            tmp = self.window_file + ".tmp"
            with securefile.open(tmp, 'w', encoding='utf-8') as f:
                json.dump({"days": {k: {n: round(v, 1) for n, v in day.items()}
                                    for k, day in self.window_days.items()},
                       "runs": self.window_runs},
                          f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.window_file)
            self._window_dirty = False
        except (IOError, OSError):
            pass
