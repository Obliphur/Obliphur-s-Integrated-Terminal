"""断网 (Adapter) 期间与其他设备重叠的记录: 同样的时间只算一次 (MECHANICS 15.5)。

从 data_manager.py 拆出来的 mixin; 依赖方向: data_manager.py 导入它们, 它们从不导入 data_manager.py.
"""

from datetime import datetime, timedelta

from data_manager.data_common import (
    _RANK,
    _subtract,
)


class OverlapMixin:
    # ---- 断网 (Adapter) 期间的重叠 (MECHANICS 15.5) ----

    def resolve_overlap(self, peer_recs, start: datetime, end: datetime, tie_mine: bool, priority: bool = False) -> dict:
        """一台设备用 Adapter 断网 (不同步) 的这段 [start, end] 里, 本机和其他设备各自记的专注计时有重叠时:
        按 休息 > 专注 > 空闲 叠加 —— 本机的记录被别的设备优先级更高的记录盖住的部分删掉;
        一样的 (例如两边都记了专注) 只由断网的那台设备删 (tie_mine), 不重复计. 每台设备只改自己的记录.
        priority: 对方刚从自由学习的外出回来 (MECHANICS 21.6), 它的记录优先 —— 不看 休息 > 专注 > 空闲, 本机和它重叠的部分都删掉.
        删掉的专注同时从学习时长里扣掉. 返回 {"study": 扣掉的秒数, "break": …, "idle": …}."""
        out = {"study": 0.0, "break": 0.0, "idle": 0.0}
        if not peer_recs or end <= start:
            return out
        peers = []
        for r in peer_recs:
            try:
                k, a, b = r["k"], datetime.strptime(r["s"], self._TS), datetime.strptime(r["e"], self._TS)
            except (KeyError, TypeError, ValueError):
                continue
            a, b = max(a, start), min(b, end)
            if k in _RANK and b > a:
                peers.append((k, a, b))
        if not peers:
            return out
        kept, study_cut = [], {}
        for r in self.focus_sessions:
            try:
                k, a, b = r["k"], datetime.strptime(r["s"], self._TS), datetime.strptime(r["e"], self._TS)
            except (KeyError, TypeError, ValueError):
                kept.append(r)
                continue
            cover = [(pa, pb) for pk, pa, pb in peers
                     if priority or _RANK[pk] > _RANK.get(k, 0) or (_RANK[pk] == _RANK.get(k, 0) and tie_mine)]
            pieces = _subtract(a, b, cover) if cover else [(a, b)]
            removed = (b - a).total_seconds() - sum((y - x).total_seconds() for x, y in pieces)
            if removed <= 0:
                kept.append(r)
                continue
            out[k] = out.get(k, 0.0) + removed
            if k == "study":
                for x, y in _subtract(a, b, pieces):          # 被删掉的那几段, 按天扣学习时长
                    while x < y:
                        nxt = min(y, x.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
                        ds = x.strftime('%Y-%m-%d')
                        study_cut[ds] = study_cut.get(ds, 0.0) + (nxt - x).total_seconds()
                        x = nxt
            for x, y in pieces:
                if (y - x).total_seconds() >= 1:
                    nr = dict(r, s=x.strftime(self._TS), e=y.strftime(self._TS))
                    kept.append(nr)
        self.focus_sessions = kept
        for ds, sec in study_cut.items():
            if ds in self.study_data:
                self.study_data[ds] = max(0.0, self.study_data[ds] - sec / 3600.0)
        self.save_focus_log()
        if study_cut:
            self.save_study_data()
        return out
