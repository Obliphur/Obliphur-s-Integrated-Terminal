"""行为模式 (设置 → 日常) 与"外出" (MECHANICS 21).

三种模式:

- 自由学习: 什么都不做, 和以前一样.
- 封闭式管理 / 工作: Timer 页多一个"外出"按钮, 可以设置持续多少小时 (可以跨天).
  外出期间按下面的规则记录, 不写 study_log / focus_log, 统计和日历显示时叠加:
    * 每天最多记 10 小时; 每一段 (每个自然日) 向下取整 (不足 1 小时的部分不计).
    * 7:00 ~ 睡觉时间 (MECHANICS 19, 按星期) 之内的部分按 80% 专注 / 20% 休息填充到专注计时和统计;
      其他时间 (夜里) 只算休息.
    * 整点记录的超时自动填充: 外出的小时 → 「外出」, 其他时间 → 「休息」
      (这两个文字可以在 设置 → 日常 改, 48 小时生效).
    * 当天有外出时, 联网额度 ×0.2; 正在外出时, 联网冷静期 ×0.3.
    * 外出结束后: 被记录的小时数 × 6 分钟休息时间, 最多 3 小时, 存进积攒的休息时间.

外出是几台设备共用的 (doc 通过 devsync 同步). 休息时间只由开始外出的那台设备发
(和计时器的记录只记一次一样); 它不在时其他设备不重复发.

文件 behavior.json: {"mode": "closed", "mode_since": "...", "outings": [...]}.
mode_since 是本机切换到当前模式的时间, 不同步: 整点记录只把切换之后的小时标成
「外出 / 休息」, 不会把以前的记录翻出来改掉.
"""

import math
import os
import uuid
from datetime import datetime, timedelta

import config


_TS = "%Y-%m-%d %H:%M:%S"
_KEEP_DAYS = 62


def _iso(dt):
    return dt.strftime(_TS) if isinstance(dt, datetime) else ""


def _dt(text):
    try:
        return datetime.strptime(str(text), _TS)
    except (TypeError, ValueError):
        return None


def fill_window(t: datetime):
    """t 所在的那段可以按 80% / 20% 填充的时段: [7:00, 这之后的第一次睡觉时间) (MECHANICS 21.2).
    睡觉时间在凌晨 (例如 00:30) 时跨零点; 最晚到第二天 7:00. t 在这段之外时它就是 t 之前最近的那段."""
    lo = timedelta(minutes=int(config.OUTING_WINDOW_START))
    ws = _day0(t - lo) + lo
    we = min(config.next_sleep(ws), ws + timedelta(days=1))
    return ws, we


def _day0(d):
    """日期 / datetime / "YYYY-MM-DD" → 那一天的 00:00."""
    if isinstance(d, datetime):
        return d.replace(hour=0, minute=0, second=0, microsecond=0)
    if isinstance(d, str):
        try:
            d = datetime.strptime(d[:10], "%Y-%m-%d")
        except ValueError:
            d = datetime.now()
    return datetime(d.year, d.month, d.day)


class Behavior:
    def __init__(self, data_dir: str, now: datetime = None):
        self.path = os.path.join(data_dir, "behavior.json")
        self.outings = []
        existed = os.path.exists(self.path)
        self._mode = str(getattr(config, "BEHAVIOR_MODE", config.BEHAVIOR_FREE))
        self._mode_since = None
        self.load()
        if not existed or self._mode_since is None:
            # 第一次打开 (或旧文件没有这个字段): 从这一次开始算, 不回头改以前的整点记录
            self._mode_since = (now or datetime.now()).replace(microsecond=0)
            self.save()
        self.sync_mode(now)

    # ---- 文件 ----

    def load(self):
        d = config.load_json_file(self.path, {})
        if not isinstance(d, dict):
            d = {}
        self._mode = str(d.get("mode") or getattr(config, "BEHAVIOR_MODE", config.BEHAVIOR_FREE))
        self._mode_since = _dt(d.get("mode_since"))
        out = []
        for raw in (d.get("outings") or []):
            o = self._clean(raw)
            if o is not None:
                out.append(o)
        self.outings = sorted(out, key=lambda o: o["start"])
        self._prune()

    def _clean(self, raw) -> dict:
        if not isinstance(raw, dict):
            return None
        start = _dt(raw.get("start"))
        end = _dt(raw.get("end"))
        if start is None or end is None or end <= start:
            return None
        try:
            hours = int(raw.get("hours") or 0)
        except (TypeError, ValueError):
            hours = 0
        if hours <= 0:
            hours = max(1, int(math.ceil((end - start).total_seconds() / 3600.0)))
        hours = min(int(config.OUTING_MAX_HOURS), hours)
        oid = str(raw.get("id") or uuid.uuid4().hex[:12])
        return {
            "id": oid,
            "start": start,
            "hours": hours,
            "end": end,
            "stopped": _dt(raw.get("stopped")),
            "rewarded": bool(raw.get("rewarded")),
            "owner": str(raw.get("owner") or ""),
            "mode": str(raw.get("mode") or ""),
        }

    def save(self):
        payload = {
            "mode": self._mode,
            "mode_since": _iso(self._mode_since) or None,
            "outings": [{
                "id": o["id"], "start": _iso(o["start"]), "hours": int(o["hours"]),
                "end": _iso(o["end"]),
                "stopped": _iso(o.get("stopped")) or "",
                "rewarded": bool(o.get("rewarded")),
                "owner": o.get("owner") or "",
                "mode": o.get("mode") or "",
            } for o in self.outings],
        }
        return config.save_json_file(self.path, payload)

    def _prune(self, now: datetime = None):
        now = now or datetime.now()
        floor = now - timedelta(days=_KEEP_DAYS)
        keep = []
        for o in self.outings:
            if self._stopped(o) is None or self._effective_end(o, now) >= floor:
                keep.append(o)
        if len(keep) != len(self.outings):
            self.outings = keep
            self.save()

    # ---- 模式 ----

    @staticmethod
    def mode() -> str:
        m = str(getattr(config, "BEHAVIOR_MODE", config.BEHAVIOR_FREE))
        return m if m in config.BEHAVIOR_MODES else config.BEHAVIOR_FREE

    @classmethod
    def enabled(cls) -> bool:
        """这个模式有没有外出功能 / 特殊记录 (封闭式管理 / 工作)."""
        return cls.mode() in (config.BEHAVIOR_CLOSED, config.BEHAVIOR_WORK)

    def sync_mode(self, now: datetime = None):
        """设置里的行为模式变了 (或第一次打开): 记下本机从什么时候开始用这个模式."""
        if self._mode == self.mode():
            return False
        self._mode = self.mode()
        self._mode_since = (now or datetime.now()).replace(microsecond=0)
        self.save()
        return True

    @property
    def mode_since(self):
        return self._mode_since

    # ---- 外出记录 ----

    def _by_id(self, oid: str):
        for o in self.outings:
            if o["id"] == oid:
                return o
        return None

    @staticmethod
    def _stopped(o) -> datetime:
        s = o.get("stopped")
        return s if isinstance(s, datetime) else None

    def _effective_end(self, o, now: datetime = None) -> datetime:
        """这段外出实际结束 (或到此刻) 的时刻."""
        now = now or datetime.now()
        stopped = self._stopped(o)
        if stopped is not None:
            return min(stopped, o["end"])
        return min(now, o["end"])

    def active(self, now: datetime = None) -> dict:
        now = now or datetime.now()
        cur = None
        for o in self.outings:
            if self._stopped(o) is not None:
                continue
            if o["start"] <= now < o["end"]:
                if cur is None or o["start"] > cur["start"]:
                    cur = o
        return cur

    def sorted(self):
        return sorted(self.outings, key=lambda o: o["start"])

    def start(self, hours, now: datetime = None, owner: str = ""):
        """开始一次外出. 返回 (ok, 提示, 记录)."""
        now = (now or datetime.now()).replace(microsecond=0)
        if not self.enabled():
            return False, "当前行为模式没有外出.", None
        try:
            hours = int(hours)
        except (TypeError, ValueError):
            return False, "持续时间必须是整数小时.", None
        if hours < 1 or hours > int(config.OUTING_MAX_HOURS):
            return False, f"持续时间要在 1 ~ {int(config.OUTING_MAX_HOURS)} 小时之间.", None
        self.tick(now, can_reward=False)      # 先把已经到时间的处理掉
        if self.active(now) is not None:
            return False, "已经在外面了.", None
        o = {
            "id": now.strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:4],
            "start": now,
            "hours": hours,
            "end": now + timedelta(hours=hours),
            "stopped": None,
            "rewarded": False,
            "owner": str(owner or ""),
            "mode": self.mode(),
        }
        self.outings.append(o)
        self._prune(now)
        self.save()
        return True, "", o

    def stop(self, now: datetime = None, can_reward: bool = True) -> dict:
        """结束当前这次外出 (提前结束也算). 返回 tick 一样的结果."""
        now = (now or datetime.now()).replace(microsecond=0)
        o = self.active(now)
        if o is None:
            o = self._latest_unstopped(now)
        if o is not None and self._stopped(o) is None:
            o["stopped"] = min(now, o["end"])
            self.save()
        return self.tick(now, can_reward=can_reward)

    def yield_to_contract(self, at: datetime, now: datetime = None):
        """合约和外出不能同时进行 (MECHANICS 21.1). 同步来的合约和进行中的外出撞上时, 外出让路:
        在合约开始的时刻结束 (外出比合约开始得晚就整次作废, 什么都不记). 返回被结束的外出, 没有时 None."""
        now = (now or datetime.now()).replace(microsecond=0)
        o = self.active(now)
        if o is None:
            return None
        at = (at or now).replace(microsecond=0)
        o["stopped"] = min(max(at, o["start"]), now, o["end"])
        self.save()
        return o

    def _latest_unstopped(self, now: datetime = None):
        out = [o for o in self.outings if self._stopped(o) is None]
        return max(out, key=lambda o: o["start"]) if out else None

    def tick(self, now: datetime = None, can_reward: bool = True) -> dict:
        """到时间的外出自动结束; 把还没发的休息时间发掉. 返回
        {"ended": [记录], "reward": 秒, "hours": 小时}; 没有变化时 reward=0 / ended=[]."""
        now = now or datetime.now()
        ended = []
        for o in self.outings:
            if self._stopped(o) is None and o["end"] <= now:
                o["stopped"] = o["end"]
                ended.append(o)
        if ended:
            self.save()
        reward, hours, granted = self._grant_rewards(can_reward)
        return {"ended": ended, "reward": reward, "hours": hours, "granted": granted}

    def _grant_rewards(self, can_reward):
        reward, hours, granted = 0, 0.0, []
        changed = False
        for o in self.sorted():
            if self._stopped(o) is None or o.get("rewarded"):
                continue
            granted.append(o)
            try:
                allowed = bool(can_reward(o)) if callable(can_reward) else bool(can_reward)
            except Exception:
                allowed = False
            if not allowed:
                continue
            h = self.recorded_hours(o)
            reward += min(int(config.OUTING_REST_MAX), int(round(h * config.OUTING_REST_PER_HOUR)))
            hours += h
            o["rewarded"] = True
            changed = True
        if changed:
            self.save()
        return reward, hours, granted

    # ---- 记录 ----

    def _overlap(self, o, day, now: datetime = None):
        """这段外出和某一天重叠的 [开始, 结束)."""
        d0 = _day0(day)
        a = max(o["start"], d0)
        b = min(self._effective_end(o, now), d0 + timedelta(days=1))
        return (a, b) if b > a else None

    def _day_seconds(self, o, day, now: datetime = None) -> float:
        ov = self._overlap(o, day, now)
        return (ov[1] - ov[0]).total_seconds() if ov else 0.0

    def _recorded_hours_on(self, o, day, now: datetime = None) -> float:
        """这段外出在某一天被记录的小时数: 向下取整, 扣掉同一天更早的外出已经记过的小时."""
        now = now or datetime.now()
        sec = self._day_seconds(o, day, now)
        if sec <= 0:
            return 0.0
        used = 0.0
        for prev in self.sorted():
            if prev.get("id") == o.get("id"):
                break
            psec = self._day_seconds(prev, day, now)
            if psec > 0:
                used += min(10.0, float(math.floor(psec / 3600.0 + 1e-9)))
        return min(max(0.0, 10.0 - used), float(math.floor(sec / 3600.0 + 1e-9)))

    def recorded_hours(self, o, now: datetime = None) -> float:
        """这段外出被记录的小时数: 每天各自向下取整, 每天最多 10 小时.

        不足 1 小时的部分不计; 同一天有多次外出时, 先开始的先记; 后面的只记当天剩下的小时数."""
        if not isinstance(o, dict) or o.get("start") is None:
            return 0.0
        now = now or datetime.now()
        day = _day0(o["start"])
        last = _day0(self._effective_end(o, now))
        total = 0.0
        while day <= last:
            total += self._recorded_hours_on(o, day, now)
            day += timedelta(days=1)
        return total

    def days(self):
        """有外出的日期 ("YYYY-MM-DD"), 统计 / 日历用."""
        out = set()
        now = datetime.now()
        for o in self.outings:
            day = _day0(o["start"])
            last = _day0(self._effective_end(o, now))
            while day <= last:
                if self._day_seconds(o, day, now) > 0:
                    out.add(day.strftime("%Y-%m-%d"))
                day += timedelta(days=1)
        return out

    def focus_segments(self, day, now: datetime = None):
        """某一天外出记录出来的一段段 (kind, 开始, 结束): study / break.

        每一段按**被记录的小时数** (向下取整) 填充: 7:00 ~ 睡觉时间之内按 80% / 20% 分开,
        夜里只算休息. 睡觉时间不在整点 (例如 23:30) 时, 那一小时睡觉前的部分照样按 80% / 20% 分.
        不足 1 小时的部分不计, 也不画.
        """
        now = now or datetime.now()
        d0 = _day0(day)
        out = []
        for o in self.sorted():
            hours = self._recorded_hours_on(o, d0, now)
            if hours <= 0:
                continue
            ov = self._overlap(o, d0, now)
            if not ov:
                continue
            a = ov[0]
            end = min(a + timedelta(hours=hours), d0 + timedelta(days=1))
            cur = a
            while cur < end:
                h0 = cur.replace(minute=0, second=0, microsecond=0)
                h1 = h0 + timedelta(hours=1)
                pe = min(end, h1)
                sec = (pe - cur).total_seconds()
                if sec <= 0:
                    break
                ws, we = fill_window(cur)
                b = max(cur, min(pe, we))           # 睡觉前的部分到 b 为止
                if b > cur:
                    study = (b - cur).total_seconds() * float(config.OUTING_FOCUS_SHARE)
                    mid = cur + timedelta(seconds=study)
                    if mid > cur:
                        out.append(("study", cur, mid))
                    if b > mid:
                        out.append(("break", mid, b))
                if pe > b:
                    out.append(("break", b, pe))      # 睡觉时间以后 / 7:00 之前: 只算休息
                cur = pe
        return out

    def study_hours(self, day, now: datetime = None) -> float:
        """某一天外出的专注时长 (小时), 加到学习时长 / 日历上."""
        now = now or datetime.now()
        total = 0.0
        for kind, a, b in self.focus_segments(day, now):
            if kind == "study":
                total += (b - a).total_seconds()
        return total / 3600.0

    # ---- 联网 ----

    def quota_factor(self, day) -> float:
        """当天的联网额度倍率: 有外出的日子是 ×0.2 (MECHANICS 21)."""
        if not self.enabled():
            return 1.0
        now = datetime.now()
        for o in self.outings:
            if self._overlap(o, day, now):
                return float(config.OUTING_QUOTA_FACTOR)
        return 1.0

    def cooldown_factor(self, now: datetime = None) -> float:
        """正在外出: 联网冷静期 ×0.3."""
        if not self.enabled():
            return 1.0
        return float(config.OUTING_COOLDOWN_FACTOR) if self.active(now) else 1.0

    # ---- 整点记录 ----

    def hour_auto_text(self, hour_start: datetime) -> str:
        """这一小时的超时自动记录内容; "" = 用普通的超时自动记录内容."""
        if not self.enabled():
            return ""
        hour_end = hour_start + timedelta(hours=1)
        if self._mode_since is not None and hour_end <= self._mode_since:
            return ""
        for o in self.outings:
            end = self._effective_end(o)
            if o["start"] < hour_end and end > hour_start and end > o["start"]:   # 作废的外出 (0 长度) 不算
                return str(getattr(config, "OUTING_TEXT", config.OUTING_TEXT) or config.OUTING_TEXT)
        return str(getattr(config, "REST_TEXT", config.REST_TEXT) or config.REST_TEXT)

    # ---- 设备同步 ----

    def doc(self) -> dict:
        now = datetime.now()
        out = {}
        for o in self.outings:
            out["o:" + o["id"]] = {
                "id": o["id"], "start": _iso(o["start"]), "hours": int(o["hours"]),
                "end": _iso(o["end"]), "stopped": _iso(o.get("stopped")) or "",
                "rewarded": bool(o.get("rewarded")), "owner": o.get("owner") or "",
                "mode": o.get("mode") or "",
            }
        return out

    def merge(self, changes: dict):
        """同步来的外出记录: 每一项单独比较 (由 devsync 决定哪一份新)."""
        changed = False
        for key, val in (changes or {}).items():
            if not str(key).startswith("o:"):
                continue
            oid = str(key)[2:]
            old = self._by_id(oid)
            if val is None:
                if old is not None:
                    self.outings.remove(old)
                    changed = True
                continue
            new = self._clean(val)
            if new is None or new["id"] != oid:
                continue
            if old is not None:
                # 已经结束 / 已经发过休息时间的外出, 不会被一份旧的记录复活
                if self._stopped(old) is not None and self._stopped(new) is None:
                    new["stopped"] = old["stopped"]
                new["rewarded"] = bool(old.get("rewarded")) or bool(new.get("rewarded"))
                if not new.get("owner"):
                    new["owner"] = old.get("owner") or ""
                old.update(new)
            else:
                self.outings.append(new)
            changed = True
        if changed:
            self.outings.sort(key=lambda o: o["start"])
            self._normalize()
            self.save()
        return changed

    def _normalize(self):
        """万一两台设备同时开始: 只保留最晚开始的这次, 早的那次在后者开始时结束."""
        active = [o for o in self.sorted() if self._stopped(o) is None]
        for o in active[:-1]:
            later = max((x for x in active if x is not o and x["start"] >= o["start"]),
                        key=lambda x: x["start"], default=None)
            at = later["start"] if later else o["end"]
            o["stopped"] = min(max(at, o["start"]), o["end"])

    def clear_history(self):
        """子端第一次绑定: 自己的外出记录换成母端的."""
        self.outings = []
        self.save()

    def erase(self):
        self.outings = []
        try:
            os.remove(self.path)
        except OSError:
            pass


def _selftest():
    import tempfile

    keep = (config.BEHAVIOR_MODE, config.OUTING_TEXT, config.REST_TEXT)
    try:
        config.BEHAVIOR_MODE = config.BEHAVIOR_CLOSED
        config.OUTING_TEXT, config.REST_TEXT = "外出", "休息"
        at = datetime(2026, 10, 19, 0, 0)
        b = Behavior(tempfile.mkdtemp(), now=at)
        assert b.mode() == config.BEHAVIOR_CLOSED and b.enabled()
        assert b.active(datetime(2026, 10, 19, 10, 0)) is None

        # 4 小时, 10:00 ~ 14:00: 3.2h 专注 + 0.8h 休息; 4 小时记录 → 24 min 休息
        ok, msg, o = b.start(4, datetime(2026, 10, 19, 10, 0), owner="dev")
        assert ok, msg
        now = datetime(2026, 10, 19, 12, 0)
        assert b.active(now) == o
        assert b.quota_factor(now) == config.OUTING_QUOTA_FACTOR
        assert b.cooldown_factor(now) == config.OUTING_COOLDOWN_FACTOR
        segs = b.focus_segments(datetime(2026, 10, 19, 0, 0), now=now)
        study = sum((y - x).total_seconds() for k, x, y in segs if k == "study")
        rest = sum((y - x).total_seconds() for k, x, y in segs if k == "break")
        assert abs(study - 7200 * 0.8) < 1e-6, (study, segs)
        assert abs(rest - 7200 * 0.2) < 1e-6, (rest, segs)
        assert abs(b.study_hours(datetime(2026, 10, 19, 0, 0), now=now) - 1.6) < 1e-9
        assert b.recorded_hours(o, now) == 2, "进行中的按到此刻算"
        # 整点记录: 外出的小时是"外出", 其他时间是"休息"; 模式切换之前的不改
        assert b.hour_auto_text(datetime(2026, 10, 19, 10, 0)) == "外出"
        assert b.hour_auto_text(datetime(2026, 10, 19, 13, 0)) == "外出"
        assert b.hour_auto_text(datetime(2026, 10, 19, 14, 0)) == "休息"
        assert b.hour_auto_text(datetime(2026, 10, 19, 8, 0)) == "休息"
        assert b.hour_auto_text(datetime(2026, 10, 18, 23, 0)) == ""

        # 提前结束: 记到 12:00, 2 小时 → 12 min 休息
        ev = b.stop(datetime(2026, 10, 19, 12, 0))
        assert ev["reward"] == 2 * config.OUTING_REST_PER_HOUR, ev
        assert abs(ev["hours"] - 2) < 1e-9, ev
        assert b.active(datetime(2026, 10, 19, 12, 0)) is None
        assert b.stop(datetime(2026, 10, 19, 12, 0))["reward"] == 0, "只发一次"

        # 跨天 22:00 ~ 08:00: 第一天 2h (夜里, 只休息), 第二天 8h (7:00~8:00 有 1h 专注)
        b2 = Behavior(tempfile.mkdtemp(), now=at)
        ok, msg, o2 = b2.start(10, datetime(2026, 10, 19, 22, 0))
        assert ok, msg
        ev = b2.tick(datetime(2026, 10, 20, 8, 0))
        assert ev["reward"] == 10 * config.OUTING_REST_PER_HOUR, ev
        assert abs(ev["hours"] - 10) < 1e-9, ev
        d1 = b2.focus_segments(datetime(2026, 10, 19, 0, 0), now=datetime(2026, 10, 20, 8, 0))
        st1 = sum((y - x).total_seconds() for k, x, y in d1 if k == "study")
        assert abs(st1 - 3600 * 0.8) < 1e-6, (st1, d1, "22:00~23:00 还在 7:00~23:00 里")
        assert any(k == "break" for k, _a, _b in d1)
        d2 = b2.focus_segments(datetime(2026, 10, 20, 0, 0), now=datetime(2026, 10, 20, 8, 0))
        st = sum((y - x).total_seconds() for k, x, y in d2 if k == "study")
        assert abs(st - 3600 * 0.8) < 1e-6, (st, d2)

        # 每天最多 10 小时: 7:00 ~ 20:00 (13h) 只记 10h → 60 min
        b3 = Behavior(tempfile.mkdtemp(), now=at)
        b3.start(13, datetime(2026, 10, 19, 7, 0))
        ev = b3.tick(datetime(2026, 10, 19, 20, 0))
        assert ev["reward"] == 10 * config.OUTING_REST_PER_HOUR, ev
        assert abs(ev["hours"] - 10) < 1e-9, ev
        st = sum((y - x).total_seconds() for k, x, y in
                 b3.focus_segments(datetime(2026, 10, 19, 0, 0), now=datetime(2026, 10, 19, 20, 0))
                 if k == "study")
        assert abs(st - 10 * 3600 * 0.8) < 1e-6, st

        # 同步: 记录合并, 已经发过的休息时间不会被旧记录复活
        b4 = Behavior(tempfile.mkdtemp())
        ok, _m, o4 = b4.start(3, datetime(2026, 10, 19, 9, 0), owner="A")
        assert ok
        doc = b4.doc()
        b5 = Behavior(tempfile.mkdtemp())
        b5.merge(doc)
        assert b5.active(datetime(2026, 10, 19, 10, 0)) is not None
        b5.stop(datetime(2026, 10, 19, 10, 0))
        assert b5.outings[0]["rewarded"]
        old = dict(doc["o:" + o4["id"]])
        b5.merge({"o:" + o4["id"]: old})
        assert b5.outings[0]["rewarded"] and b5.outings[0]["stopped"], "旧记录不能复活这次外出"

        # 同一天两次外出: 向下取整, 先开始的先记, 一天合计最多 10h
        b6 = Behavior(tempfile.mkdtemp(), now=at)
        b6.start(2, datetime(2026, 10, 19, 10, 0))
        ev = b6.stop(datetime(2026, 10, 19, 11, 30))
        assert ev["reward"] == config.OUTING_REST_PER_HOUR and abs(ev["hours"] - 1) < 1e-9, ev
        b6.start(9, datetime(2026, 10, 19, 12, 0))
        ev = b6.stop(datetime(2026, 10, 19, 21, 0))
        assert abs(ev["hours"] - 9) < 1e-9, ev
        assert ev["reward"] == 9 * config.OUTING_REST_PER_HOUR, ev
        # 不足 1 小时: 记 0 小时, 不发休息时间
        b7 = Behavior(tempfile.mkdtemp(), now=at)
        b7.start(1, datetime(2026, 10, 19, 10, 0))
        ev = b7.stop(datetime(2026, 10, 19, 10, 30))
        assert ev["reward"] == 0 and ev["hours"] == 0, ev
        assert not b7.focus_segments(datetime(2026, 10, 19, 0, 0),
                                     now=datetime(2026, 10, 19, 10, 30)), "不足 1 小时不画"

        # 合约和外出撞上 (同步来的): 外出在合约开始时结束; 比合约晚开始的外出整次作废
        b8 = Behavior(tempfile.mkdtemp(), now=at)
        b8.start(5, datetime(2026, 10, 19, 10, 0))
        o = b8.yield_to_contract(datetime(2026, 10, 19, 12, 30), datetime(2026, 10, 19, 12, 31))
        assert o is not None and o["stopped"] == datetime(2026, 10, 19, 12, 30), o
        assert b8.active(datetime(2026, 10, 19, 12, 31)) is None
        assert b8.yield_to_contract(datetime(2026, 10, 19, 12, 30)) is None, "没有外出时什么都不做"
        assert abs(b8.recorded_hours(o) - 2) < 1e-9
        b9 = Behavior(tempfile.mkdtemp(), now=at)
        b9.start(3, datetime(2026, 10, 19, 15, 20))
        o = b9.yield_to_contract(datetime(2026, 10, 19, 15, 0), datetime(2026, 10, 19, 15, 21))
        assert o["stopped"] == o["start"] and b9.recorded_hours(o) == 0
        assert b9.quota_factor(datetime(2026, 10, 19)) == 1.0 and not b9.days()
        assert b9.hour_auto_text(datetime(2026, 10, 19, 15, 0)) == config.REST_TEXT, "作废的外出不填「外出」"
        ev = b9.tick(datetime(2026, 10, 19, 15, 22))
        assert ev["reward"] == 0, ev

        # 填充到睡觉时间为止 (MECHANICS 21.2): 周一睡觉时间 23:30 → 23:00 ~ 23:30 照样 80% / 20%, 之后只休息
        sleep_keep = (config.SLEEP_MON, config.SLEEP_TUE)
        try:
            config.SLEEP_MON = 23 * 60 + 30
            b10 = Behavior(tempfile.mkdtemp(), now=at)
            b10.start(2, datetime(2026, 10, 19, 22, 0))
            b10.stop(datetime(2026, 10, 20, 0, 0))
            segs = b10.focus_segments(datetime(2026, 10, 19), now=datetime(2026, 10, 20, 1, 0))
            st = sum((b - a).total_seconds() for k, a, b in segs if k == "study")
            assert abs(st - 0.8 * 5400) < 1e-6, (st, segs)
            assert ("break", datetime(2026, 10, 19, 23, 30), datetime(2026, 10, 20, 0, 0)) in segs, segs
            assert ("study", datetime(2026, 10, 19, 23, 0), datetime(2026, 10, 19, 23, 24)) in segs, segs
            # 睡觉时间在凌晨 00:30 (周一 / 周二都是): 周一 23:00 ~ 周二 00:30 都按 80% / 20%
            config.SLEEP_MON = config.SLEEP_TUE = 30
            assert fill_window(datetime(2026, 10, 19, 23, 0)) == (datetime(2026, 10, 19, 7, 0),
                                                                   datetime(2026, 10, 20, 0, 30))
            assert fill_window(datetime(2026, 10, 20, 3, 0)) == (datetime(2026, 10, 19, 7, 0),
                                                                  datetime(2026, 10, 20, 0, 30)), "凌晨算前一天那段"
            b11 = Behavior(tempfile.mkdtemp(), now=at)
            b11.start(2, datetime(2026, 10, 19, 23, 0))
            b11.stop(datetime(2026, 10, 20, 1, 0))
            n = datetime(2026, 10, 20, 2, 0)
            st1 = b11.study_hours(datetime(2026, 10, 19), n)
            st2 = b11.study_hours(datetime(2026, 10, 20), n)
            assert abs(st1 - 0.8) < 1e-9 and abs(st2 - 0.4) < 1e-9, (st1, st2)
        finally:
            config.SLEEP_MON, config.SLEEP_TUE = sleep_keep

        # 模式切回自由学习: 没有外出, 不填整点记录
        config.BEHAVIOR_MODE = config.BEHAVIOR_FREE
        assert not b5.enabled() and b5.quota_factor(datetime(2026, 10, 19)) == 1.0
        assert b5.hour_auto_text(datetime(2026, 10, 19, 10, 0)) == ""
    finally:
        config.BEHAVIOR_MODE, config.OUTING_TEXT, config.REST_TEXT = keep
    return "behavior selftest OK"


if __name__ == "__main__":
    print(_selftest())
