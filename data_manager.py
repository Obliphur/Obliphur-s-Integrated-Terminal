

import os
import json
import shutil
from datetime import datetime, timedelta
from typing import Optional, Dict

import config
from config import (
    APP_NAME, OLD_APP_DIR, AUTO_TEXT, HOUR_LOG_ENABLED
)


def month_prefixes(now: datetime = None):
    """(本月, 上月) 的 'YYYY-MM' 前缀;数据只保留这两个月."""
    now = now or datetime.now()
    this = f"{now.year}-{now.month:02d}"
    if now.month == 1:
        last = f"{now.year - 1}-12"
    else:
        last = f"{now.year}-{now.month - 1:02d}"
    return this, last


def split_items(text: str):
    """标准语法: 一个小时 / 一次申请的多项活动用 / 连接, 如 '学习/吃饭'."""
    parts = str(text or "").replace("／", "/").split("/")
    return [p.strip() for p in parts if p.strip()]


class DataManager:
    def __init__(self):
        low_dir = os.path.join(
            os.environ.get('USERPROFILE', os.path.expanduser('~')),
            'AppData', 'LocalLow'
        )
        self.low_dir = low_dir
        self.data_dir = os.path.join(low_dir, APP_NAME)
        os.makedirs(self.data_dir, exist_ok=True)
        self.old_dir = os.path.join(low_dir, OLD_APP_DIR)


        # 旧版本在数据目录外记录卸载次数的文件; 现在不再记录, 重置时一并删除
        self.legacy_uninstall_file = os.path.join(low_dir, f"{APP_NAME}.uninstall.json")
        self.resetting = False

        self.study_file = os.path.join(self.data_dir, 'study_log.json')
        self.hour_file = os.path.join(self.data_dir, 'hour_log.json')
        # 计时器的每一段专注 / 休息 (起止时刻), Statistics 页画每天的时间分布用; 只留最近 7 天
        self.focus_file = os.path.join(self.data_dir, 'focus_log.json')
        self.focus_sessions = []
        # 窗口使用时间: {日期: {程序名: 秒}}, 程序名 "" 是空白; 只留本月和上月
        self.window_file = os.path.join(self.data_dir, 'window_usage.json')
        self.window_days: Dict[str, Dict[str, float]] = {}
        # 同一份数据按分钟的时间线: {日期: [[开始分钟, 结束分钟, 程序名], …]};
        # 统计页看"全部设备"时用它判断几台设备是不是同时在用 (重叠的分钟以母端为准)
        self.window_runs: Dict[str, list] = {}
        self._window_dirty = False

        self.study_data: Dict[str, float] = {}
        # 设备同步: 其他设备的账 (devsync.SyncService.peer_ledgers), 显示合计用
        self.peer_ledgers = lambda: []
        # 有设备离线的时间段 [(开始毫秒, 结束毫秒)] (devsync.offline_spans): 正在进行的那段也按它画成空白
        self.offline_spans = lambda a, b: []
        self.peer_ledgers_by_id = lambda: {}     # {设备 id: 账}
        self.my_id = lambda: ""
        self.mother_id = lambda: None            # 母端的设备 id
        self.hour_days: Dict[str, Dict[str, str]] = {}
        # 合约的细化规范 (MECHANICS 18.4): 一小时分成两个半小时各填一格 {日期: {小时: [前半, 后半]}};
        # hour_days 里那一小时是两格用 "/" 连起来的内容, 统计照常按它算.
        self.hour_halves: Dict[str, Dict[str, list]] = {}
        self.hour_cursor: Optional[datetime] = None

        self.hour_enabled = bool(getattr(config, "HOUR_LOG_ENABLED", HOUR_LOG_ENABLED))
        self.today_date = datetime.now().strftime('%Y-%m-%d')
        self.prompt_slot: Optional[datetime] = None
        self.prompt_len = 60                     # 待填写的那一格有几分钟 (细化规范: 30)

        self._migrate_old_data()
        self.load_study_data()
        self.prune_study_data()
        self.load_hour_log()
        self.finalize_hours()
        self.load_focus_log()
        self.load_window_usage()


    @property
    def auto_text(self) -> str:
        return str(getattr(config, "AUTO_TEXT", AUTO_TEXT))


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
                with open(self.study_file, 'r', encoding='utf-8') as f:
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
            with open(self.study_file, 'w', encoding='utf-8') as f:
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
        return {k: self._study_all(k) for k in days}


    # ---- 专注计时: 每一段专注 / 休息的起止时刻 ----

    FOCUS_DAYS = 7
    _TS = '%Y-%m-%d %H:%M:%S'

    def _focus_floor(self, now: datetime = None) -> datetime:
        now = now or datetime.now()
        return now.replace(hour=0, minute=0, second=0, microsecond=0) \
            - timedelta(days=self.FOCUS_DAYS - 1)

    def load_focus_log(self):
        try:
            with open(self.focus_file, 'r', encoding='utf-8') as f:
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
            if r.get("k") in ("study", "break") and en > st:
                out.append({"k": r["k"], "s": r["s"], "e": r["e"]})
            elif r.get("k") == "idle" and en > st:
                out.append({"k": "idle", "s": r["s"], "e": r["e"], "f": str(r.get("f") or "")})
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
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({"sessions": self.focus_sessions}, f, indent=1)
            os.replace(tmp, self.focus_file)
        except (IOError, OSError):
            pass

    def add_focus_session(self, kind: str, start: datetime, end: datetime, focus: datetime = None):
        """kind: 'study' 专注 / 'break' 休息 / 'idle' 空闲.
        空闲: 共享专注中本机 OIT 被关掉的那一段; focus 是那段专注的开始时刻 (MECHANICS 8.2)."""
        if kind not in ("study", "break", "idle") or start is None or end is None:
            return
        if (end - start).total_seconds() < 1:
            return
        rec = {"k": kind, "s": start.strftime(self._TS), "e": end.strftime(self._TS)}
        if kind == "idle":
            rec["f"] = focus.strftime(self._TS) if focus else ""
        self.focus_sessions.append(rec)
        self.save_focus_log()

    def focus_hours(self, day: datetime, live=None):
        """某一天 24 个小时各自的 (专注秒数, 休息秒数).

        live: 正在进行的一段 (kind, start), 计到此刻为止."""
        day0 = day.replace(hour=0, minute=0, second=0, microsecond=0)
        study = [0.0] * 24
        rest = [0.0] * 24
        segs = []
        recs = list(self.focus_sessions)
        for led in self.peer_ledgers() or []:
            recs += [r for r in (led.get("focus") or []) if isinstance(r, dict)]
        idle = {}                                  # 专注的开始时刻 → [(空闲开始, 空闲结束)]
        for r in recs:
            try:
                k, st, en = r["k"], datetime.strptime(r["s"], self._TS), datetime.strptime(r["e"], self._TS)
            except (KeyError, TypeError, ValueError):
                continue
            if k == "idle":
                idle.setdefault(str(r.get("f") or ""), []).append((st, en))
            else:
                segs.append((k, st, en))
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

    # ---- 窗口使用时间: 每隔几秒看一次前台程序, 把这几秒记给它 ----

    def load_window_usage(self):
        try:
            with open(self.window_file, 'r', encoding='utf-8') as f:
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
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({"days": {k: {n: round(v, 1) for n, v in day.items()}
                                    for k, day in self.window_days.items()},
                       "runs": self.window_runs},
                          f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.window_file)
            self._window_dirty = False
        except (IOError, OSError):
            pass

    def load_hour_log(self):
        try:
            with open(self.hour_file, 'r', encoding='utf-8') as f:
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
            with open(self.hour_file, 'w', encoding='utf-8') as f:
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
                self.set_half_text(ds, h, i, self.auto_text, save=False)
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
                self.hour_days.setdefault(ds, {})[str(h)] = self.auto_text
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


    def erase_all_data(self):
        """重置: 删除本程序留下的全部数据, 不留任何记录."""
        shutil.rmtree(self.data_dir, ignore_errors=True)
        shutil.rmtree(self.old_dir, ignore_errors=True)
        try:
            os.remove(self.legacy_uninstall_file)
        except OSError:
            pass


def half_fill() -> timedelta:
    """细化规范的半小时格: 补填时限只有 30 min (补填时限设得更短时照设置)."""
    return min(config.FILL_WINDOW, config.FINE_FILL_WINDOW)


def _sum_days(days: dict, prefix: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for ds, day in (days or {}).items():
        if str(ds).startswith(prefix) and isinstance(day, dict):
            for name, sec in day.items():
                try:
                    out[name] = out.get(name, 0.0) + float(sec)
                except (TypeError, ValueError):
                    pass
    return out


def _subtract(st: datetime, en: datetime, holes) -> list:
    """[st, en) 去掉 holes 里的各段之后剩下的 [(开始, 结束)]."""
    pieces = [(st, en)]
    for hs, he in holes:
        nxt = []
        for a, b in pieces:
            if he <= a or hs >= b:
                nxt.append((a, b))
                continue
            if hs > a:
                nxt.append((a, hs))
            if he < b:
                nxt.append((he, b))
        pieces = nxt
    return pieces


def combine_window(sources, prefix: str) -> Dict[str, float]:
    """几台设备的窗口 / 应用时间合在一起. sources 按优先级排 (母端在前), 每个是
    {"seconds": {日期: {程序: 秒}}, "runs": {日期: [[开始分钟, 结束分钟, 程序], …]}}.
    有时间线的日子按分钟合并: 同一分钟以排在前面的设备为准, 只有它是空白 (或没有记录) 时才看后面的;
    没有时间线的日子 (旧数据) 直接把秒数加起来."""
    dates = set()
    for src in sources:
        for key in ("seconds", "runs"):
            dates |= {str(d) for d in (src.get(key) or {}) if str(d).startswith(prefix)}
    out: Dict[str, float] = {}
    for d in dates:
        minutes = [None] * 1440
        for src in sources:
            runs = (src.get("runs") or {}).get(d)
            if runs:
                for r in runs:
                    try:
                        s, e, app = int(r[0]), int(r[1]), str(r[2])
                    except (TypeError, ValueError, IndexError):
                        continue
                    for m in range(max(0, s), min(1440, e)):
                        cur = minutes[m]
                        if cur is None or (cur == "" and app != ""):
                            minutes[m] = app
            else:
                for app, sec in ((src.get("seconds") or {}).get(d) or {}).items():
                    try:
                        out[app] = out.get(app, 0.0) + float(sec)
                    except (TypeError, ValueError):
                        pass
        for app in minutes:
            if app is not None:
                out[app] = out.get(app, 0.0) + 60.0
    return out


def tally_items(pairs) -> Dict[str, float]:
    """[(文本, 秒)] → {活动: 秒};按 / 拆分均分,同名(忽略大小写)合并."""
    totals: Dict[str, float] = {}
    names: Dict[str, str] = {}
    for text, secs in pairs:
        items = split_items(text)
        if not items or secs <= 0:
            continue
        share = float(secs) / len(items)
        for item in items:
            key = item.casefold()
            names.setdefault(key, item)
            totals[key] = totals.get(key, 0.0) + share
    return {names[k]: v for k, v in totals.items()}
