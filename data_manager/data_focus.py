"""专注计时: 每一段专注 / 休息的起止时刻 (focus_log.json)、按天的时间分布、空闲 / 睡眠。

从 data_manager.py 拆出来的 mixin; 依赖方向: data_manager.py 导入它们, 它们从不导入 data_manager.py.
"""

import os
import json
from datetime import datetime, timedelta
import securefile

from data_manager.data_common import (
    _merge_spans,
    _subtract,
)


class FocusMixin:
    # ---- 专注计时: 每一段专注 / 休息的起止时刻 ----

    FOCUS_DAYS = 7
    _TS = '%Y-%m-%d %H:%M:%S'

    def _focus_floor(self, now: datetime = None) -> datetime:
        now = now or datetime.now()
        return now.replace(hour=0, minute=0, second=0, microsecond=0) \
            - timedelta(days=self.FOCUS_DAYS - 1)

    def load_focus_log(self):
        try:
            with securefile.open(self.focus_file, 'r', encoding='utf-8') as f:
                d = json.load(f)
            recs = d.get("sessions", []) if isinstance(d, dict) else []
        except Exception:
            recs = []
        out = []
        for r in recs:
            try:
                st = datetime.strptime(r["s"], self._TS)
                en = datetime.strptime(r["e"], self._TS)
            except (KeyError, TypeError, ValueError):
                continue
            if r.get("k") in ("study", "break", "sleep") and en > st:
                out.append({"k": r["k"], "s": r["s"], "e": r["e"]})
            elif r.get("k") == "idle" and en > st:
                rec = {"k": "idle", "s": r["s"], "e": r["e"], "f": str(r.get("f") or "")}
                if r.get("p"):
                    rec["p"] = True          # 专注暂停的时段 (MECHANICS 6.9): 只在统计 / 日历里算空闲
                out.append(rec)
        self.focus_sessions = out
        self._prune_focus()

    def _prune_focus(self) -> bool:
        floor = self._focus_floor().strftime(self._TS)
        before = len(self.focus_sessions)
        self.focus_sessions = [r for r in self.focus_sessions if r["e"] > floor]
        return len(self.focus_sessions) != before

    def save_focus_log(self):
        if self.resetting:
            return
        self._prune_focus()
        try:
            tmp = self.focus_file + ".tmp"
            with securefile.open(tmp, 'w', encoding='utf-8') as f:
                json.dump({"sessions": self.focus_sessions}, f, indent=1)
            os.replace(tmp, self.focus_file)
        except (IOError, OSError):
            pass

    def add_focus_session(self, kind: str, start: datetime, end: datetime, focus: datetime = None,
                          pause: bool = False):
        """kind: 'study' 专注 / 'break' 休息 / 'idle' 空闲 / 'sleep' 睡眠 (睡眠是单独的一种时间, MECHANICS 23).
        空闲: 共享专注中本机 OIT 被关掉的那一段; focus 是那段专注的开始时刻 (MECHANICS 8.2).
        pause: 这是专注暂停的时段 —— 统计 / 日历里记为空闲, 其他模块一律当作专注 (MECHANICS 6.9)."""
        if kind not in ("study", "break", "idle", "sleep") or start is None or end is None:
            return
        if (end - start).total_seconds() < 1:
            return
        rec = {"k": kind, "s": start.strftime(self._TS), "e": end.strftime(self._TS)}
        if kind == "idle":
            rec["f"] = focus.strftime(self._TS) if focus else ""
            if pause:
                rec["p"] = True
        self.focus_sessions.append(rec)
        self.save_focus_log()

    def peer_active(self, start: datetime, end: datetime) -> bool:
        """[start, end] 里有没有别的设备记下的专注 / 休息 (本机关机时有没有别的设备在用, MECHANICS 15.4)."""
        for led in self.peer_ledgers() or []:
            for r in led.get("focus") or []:
                try:
                    if r.get("k") in ("study", "break") and \
                            datetime.strptime(r["s"], self._TS) < end and start < datetime.strptime(r["e"], self._TS):
                        return True
                except (AttributeError, KeyError, TypeError, ValueError):
                    continue
        return False

    def focus_hours(self, day: datetime, live=None, pause_as_idle: bool = True):
        """某一天 24 个小时各自的 (专注秒数, 休息秒数).

        live: 正在进行的一段 (kind, start), 计到此刻为止.
        pause_as_idle: 专注暂停的时段算空闲 (统计 / 日历用); False 时当作专注 (沟通 / 指令用, MECHANICS 6.9)."""
        day0 = day.replace(hour=0, minute=0, second=0, microsecond=0)
        study = [0.0] * 24
        rest = [0.0] * 24
        segs = []
        recs = list(self.focus_sessions)
        for led in self.peer_ledgers() or []:
            recs += [r for r in (led.get("focus") or []) if isinstance(r, dict)]
        if pause_as_idle:
            start, pause = getattr(self, "focus_pause_live", lambda: (None, None))()
            if start is not None and pause is not None:
                recs.append({"k": "idle", "s": pause.strftime(self._TS),
                             "e": datetime.now().strftime(self._TS), "f": start.strftime(self._TS), "p": True})
        idle = {}                                  # 专注的开始时刻 → [(空闲开始, 空闲结束)]
        for r in recs:
            try:
                k, st, en = r["k"], datetime.strptime(r["s"], self._TS), datetime.strptime(r["e"], self._TS)
            except (KeyError, TypeError, ValueError):
                continue
            if k == "idle":
                if pause_as_idle or not r.get("p"):
                    idle.setdefault(str(r.get("f") or ""), []).append((st, en))
            elif k in ("study", "break"):
                segs.append((k, st, en))             # 睡眠不画进专注计时条形图 (留白)
        if live and live[1] is not None:
            now = datetime.now()
            segs.append((live[0], live[1], now))
            try:                                   # 还没记下来的这段专注: 有设备离线的部分现在就画成空白 (休息照常)
                spans = [] if live[0] == "break" else self.offline_spans(live[1].timestamp() * 1000, now.timestamp() * 1000)
                for a, b in spans:
                    idle.setdefault(live[1].strftime(self._TS), []).append(
                        (datetime.fromtimestamp(a / 1000), datetime.fromtimestamp(b / 1000)))
            except Exception:
                pass
        # 行为模式的外出 (MECHANICS 21): 8:2 填充出来的专注 / 休息, 和普通的段落一起画
        try:
            segs += [(k, st, en) for k, st, en in self.extra_focus(day)]
        except Exception:
            pass
        # 空闲的优先级最高: 同一段专注 (开始时刻相同) 里和空闲重叠的部分画成空白 (哪台设备记的都一样); 休息照常
        cut = []
        for kind, st, en in segs:
            if kind == "break":
                cut.append((kind, st, en))
                continue
            cut += [(kind, a, b) for a, b in _subtract(st, en, idle.get(st.strftime(self._TS), []))]
        for kind, st, en in cut:
            for h in range(24):
                h0 = day0 + timedelta(hours=h)
                h1 = h0 + timedelta(hours=1)
                sec = (min(en, h1) - max(st, h0)).total_seconds()
                if sec > 0:
                    (study if kind == "study" else rest)[h] += sec
        for h in range(24):
            # 理论上不会重叠; 万一重叠, 专注优先, 一小时最多 3600 秒
            study[h] = min(3600.0, study[h])
            rest[h] = min(3600.0 - study[h], rest[h])
        return list(zip(study, rest))

    def day_intervals(self, day: datetime, live=None, now: datetime = None) -> list:
        """某一天每一段专注 / 休息 / 睡眠的持续区间 [(kind, 开始, 结束)], 按开始时间排好 (kind: study / break / sleep; 空闲不列).

        和 focus_hours 一样: 专注里空闲 (设备离线) 的部分去掉, 行为模式的外出填充也算; 跨零点的段截到这一天里.
        live: 正在进行的一段 (kind, start), kind 可以是 sleep, 计到此刻为止 (沟通的 📎 专注计时(今天) 用)."""
        now = now or datetime.now()
        day0 = day.replace(hour=0, minute=0, second=0, microsecond=0)
        day1 = day0 + timedelta(days=1)
        recs = list(self.focus_sessions)
        for led in self.peer_ledgers() or []:
            recs += [r for r in (led.get("focus") or []) if isinstance(r, dict)]
        idle, segs = {}, []
        for r in recs:
            try:
                k, st, en = r["k"], datetime.strptime(r["s"], self._TS), datetime.strptime(r["e"], self._TS)
            except (KeyError, TypeError, ValueError):
                continue
            if k == "idle":
                if not r.get("p"):                   # 暂停的时段当作专注 (沟通 / 指令里就是专注, MECHANICS 6.9)
                    idle.setdefault(str(r.get("f") or ""), []).append((st, en))
            elif k in ("study", "break", "sleep"):
                segs.append((k, st, en))
        if live and live[1] is not None:
            segs.append((live[0], live[1], now))
            if live[0] == "study":
                try:
                    for a, b in self.offline_spans(live[1].timestamp() * 1000, now.timestamp() * 1000):
                        idle.setdefault(live[1].strftime(self._TS), []).append(
                            (datetime.fromtimestamp(a / 1000), datetime.fromtimestamp(b / 1000)))
                except Exception:
                    pass
        try:
            segs += [x for x in self.extra_focus(day) if x[0] in ("study", "break")]
        except Exception:
            pass
        out = set()
        for kind, st, en in segs:
            pieces = _subtract(st, en, idle.get(st.strftime(self._TS), [])) if kind == "study" else [(st, en)]
            for a, b in pieces:
                a, b = max(a, day0), min(b, day1)
                if (b - a).total_seconds() >= 1:
                    out.add((kind, a, b))
        return sorted(out, key=lambda x: (x[1], x[2], x[0]))

    def day_kinds(self, day: datetime, live=None, now: datetime = None,
                  pause_as_idle: bool = True) -> dict:
        """某一天四种时间各多少秒 (只有最近 7 天有专注计时记录): 专注 study / 休息 break / 睡眠 sleep / 空闲 idle.

        空闲 = 这一天 (今天只算到此刻) 里不属于前三种的时间. live: 正在进行的一段 (kind, start), kind 可以是 sleep.
        pause_as_idle: 专注暂停的时段算空闲 (统计 / 日历用); False 时当作专注 (沟通的 [condition] 判定用, MECHANICS 6.9)."""
        now = now or datetime.now()
        day0 = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = day0 + timedelta(days=1)
        limit = min(end_of_day, now)
        span = max(0.0, (limit - day0).total_seconds())
        hours = self.focus_hours(day, live if live and live[0] in ("study", "break") else None,
                                 pause_as_idle)
        study = sum(s for s, _r in hours)
        rest = sum(r for _s, r in hours)
        recs = list(self.focus_sessions)
        for led in self.peer_ledgers() or []:
            recs += [r for r in (led.get("focus") or []) if isinstance(r, dict)]
        spans = []
        for r in recs:
            if r.get("k") != "sleep":
                continue
            try:
                spans.append((datetime.strptime(r["s"], self._TS), datetime.strptime(r["e"], self._TS)))
            except (KeyError, TypeError, ValueError):
                continue
        if live and live[0] == "sleep" and live[1] is not None:
            spans.append((live[1], now))
        sleep = 0.0
        for a, b in _merge_spans(spans):
            sleep += max(0.0, (min(b, limit) - max(a, day0)).total_seconds())
        idle = max(0.0, span - study - rest - sleep)
        return {"study": study, "break": rest, "sleep": sleep, "idle": idle}
