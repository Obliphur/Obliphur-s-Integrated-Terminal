"""指令 (Prescript): 都市之意主题独占的模块.

规则见 MECHANICS.md 第 22 节. 这里只放判定 / 计时 / 存档, 界面在
pages/prescript_page.py. 数据存在数据目录的 prescript.json, 不同步.

时间都按墙上时间算; 关闭程序期间照常流逝 (下次打开时按过期 / 完成判定).
"""

import os
import random
import re
from datetime import datetime, timedelta

import config


TYPES = ("I", "II", "III", "IV", "V", "VI")

# 每个池子里六条指令的概率: I, II, III, IV, V, VI
POOLS = {
    "focus": (0.8, 0.03, 0.03, 0.10, 0.00, 0.04),
    "break": (0.0, 0.10, 0.70, 0.10, 0.05, 0.05),
    "idle":  (0.1, 0.02, 0.02, 0.04, 0.80, 0.02),
}

# 领取后多久作废 (秒); IV 在这一天结束时作废, 由日期换日处理.
EXPIRY_SECONDS = {
    "I": 60 * 60,
    "II": 8 * 60 * 60,
    "III": 60 * 60,
    "V": 5 * 60,
    "VI": 60 * 60,
}

FIXED_T = {"I": 1.0, "III": 1.0, "IV": 1.0, "V": 0.1, "VI": 1.0}
FOCUS_TARGET = 50 * 60          # I: 50 分钟专注
BREAK_TARGET = 20 * 60          # III (旧存档里没有分钟数的): 20 分钟休息
REST_SHARE = 0.5                # III: 要休息的分钟数 = 用户剩余休息时间的 50%
REST_MAX_MIN = 30               # III: 最多 30 分钟
REFLECT_TARGET = 3 * 60         # VI: 统计页连续停留 3 分钟
DAILY_T_TARGET = 10.0           # 今天完成的指令 t 合计到 10
DAILY_BONUS_SECONDS = 20 * 60   # 额外 20 分钟休息

# 每条指令的蓝色代码用的词 (补位后正好 8 个字符以内)
TYPE_WORDS = {"I": "FOCUS", "II": "CONTRACT", "III": "REST", "IV": "DAILY", "V": "STOP", "VI": "REFLECT"}
CODE_WIDTH = 8                  # 蓝色代码的宽度 (和网页上的指令代码一样, 8 个字符)
CODE_LEFT = "_%"                # 左边补位, 从最外面一格开始依次用
CODE_RIGHT = "%."               # 右边补位, 从最里面一格往外依次用 (最后一格是 .)

KEEP = 3                        # 每个用户同时最多持有几条指令, 满了就不能再领
DEV_KEEP = KEEP                 # 旧名字

# 每种指令对应界面上哪个入口 (在那个按钮上标记): 专注 / 休息 开始按钮, 合约 / 统计 侧边栏入口
MARK_OF = {"I": "focus", "IV": "focus", "III": "break", "II": "contract", "VI": "stats"}
HISTORY_KEEP = 100
GRANTED_KEEP = 40
SAVE_EVERY = 10                 # 秒; 累计的专注 / 休息秒数最多丢这么多


def _iso(when: datetime) -> str:
    return when.replace(microsecond=0).isoformat(timespec="seconds")


def _parse(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def fmt_t(t) -> str:
    """t 的小时数写法: 1 → "1", 0.1 → "0.1", 2.5 → "2.5"."""
    try:
        t = float(t)
    except (TypeError, ValueError):
        return "0"
    if abs(t - round(t)) < 1e-9:
        return str(int(round(t)))
    return f"{t:.3f}".rstrip("0").rstrip(".")


def rest_code(minutes) -> str:
    """给出 N 分钟休息的代码正文: 3 → "REST3", 0.3 → "REST0.3"."""
    try:
        minutes = round(float(minutes), 1)
    except (TypeError, ValueError):
        minutes = 0.0
    text = str(int(minutes)) if abs(minutes - round(minutes)) < 1e-9 else f"{minutes:.1f}"
    return "REST" + text


def code_for(t) -> str:
    """完成一条指令时显示的蓝色代码的正文: 休息 t×3 分钟."""
    try:
        return rest_code(float(t) * 3)
    except (TypeError, ValueError):
        return rest_code(0)


_LEGACY_H = re.compile(r"^(\d+(?:\.\d+)?)h\*3min$")       # 旧存档里的 "1h*3min"
_LEGACY_MIN = re.compile(r"^(\d+(?:\.\d+)?)min$")          # 旧存档里的 "20min"


def pad_code(code: str) -> str:
    """蓝色代码补到 8 个字符: 正文居中, 两边用 _ % . 补位 ("NONE" → "_%NONE%.")."""
    code = str(code or "")
    m = _LEGACY_H.match(code)
    if m:
        code = rest_code(float(m.group(1)) * 3)
    else:
        m = _LEGACY_MIN.match(code)
        if m:
            code = rest_code(float(m.group(1)))
    code = code[:CODE_WIDTH]
    gap = CODE_WIDTH - len(code)
    left = gap // 2
    right = gap - left
    return ((CODE_LEFT * CODE_WIDTH)[:left] + code
            + (CODE_RIGHT * CODE_WIDTH)[CODE_WIDTH * 2 - right:])


def rest_minutes(remaining_seconds) -> int:
    """III 要休息几分钟: 剩余休息时间的 50%, 最多 30 分钟, 至少 1 分钟."""
    try:
        minutes = int(float(remaining_seconds) * REST_SHARE // 60)
    except (TypeError, ValueError):
        minutes = 0
    return max(1, min(REST_MAX_MIN, minutes))


def type_code(kind) -> str:
    """一条指令的蓝色代码正文 (还没补位): I → "FOCUS"."""
    return TYPE_WORDS.get(str(kind), "NONE")


def fmt_clock(seconds) -> str:
    """倒计时: 不足一小时写 mm:ss, 否则 h:mm:ss."""
    seconds = max(0, int(round(float(seconds or 0))))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _midnight_after(now: datetime) -> datetime:
    return datetime.combine(now.date() + timedelta(days=1), datetime.min.time())


def contract_duration_hours(rec: dict) -> float:
    """合约记录的总时长 (小时): 按计划结束时刻算, 没有时用实际结束时刻."""
    rec = rec if isinstance(rec, dict) else {}
    start = _parse(rec.get("start"))
    stop = _parse(rec.get("planned")) or _parse(rec.get("end"))
    if start is None or stop is None or stop <= start:
        return 0.0
    return (stop - start).total_seconds() / 3600.0


class PrescriptEngine:
    """一条指令的抽取 / 计时 / 完成 / 存档.

    state_fn()        -> "focus" / "break" / "idle"
    today_hours_fn()  -> 今天已经计入的专注小时数
    contracts_fn()    -> contract.Contract.history
    page_fn()         -> 当前页面 (app.current_page)
    minimized_fn()    -> OIT 是不是最小化了
    grant_rest_fn(seconds, code)  -> 把休息时间加进计时器
    username_fn()     -> 用户名 (显示 "致[username]:")
    blocked_fn()      -> 正在重置数据时不要写文件
    on_change()       -> 状态变了, 让指令页刷新
    dev_fn()          -> 开发者模式: 领取没有冷却, 同时最多留 3 条
    pause_media_fn()  -> 领取 V 时暂停后台正在播放的音频 / 视频
    contract_active_fn() -> 现在有没有进行中的合约 (合约期间抽不到 VI)
    on_complete()     -> 有一条指令被完成了 (播完成音效)
    remaining_rest_fn() -> 用户剩余的休息时间 (秒): 休息中是这次休息的倒计时, 否则是积攒的休息时间
    """

    def __init__(self, data_dir, *, state_fn=None, today_hours_fn=None, contracts_fn=None,
                 page_fn=None, minimized_fn=None, grant_rest_fn=None, username_fn=None,
                 blocked_fn=None, on_change=None, dev_fn=None, pause_media_fn=None, remaining_rest_fn=None, contract_active_fn=None, on_complete=None, rng=None):
        self.path = os.path.join(data_dir, "prescript.json")
        self.state_fn = state_fn or (lambda: "idle")
        self.today_hours_fn = today_hours_fn or (lambda: 0.0)
        self.contracts_fn = contracts_fn or (lambda: [])
        self.page_fn = page_fn or (lambda: "")
        self.minimized_fn = minimized_fn or (lambda: False)
        self.grant_rest_fn = grant_rest_fn
        self.username_fn = username_fn or (lambda: "")
        self.blocked_fn = blocked_fn or (lambda: False)
        self.dev_fn = dev_fn or (lambda: False)
        self.pause_media_fn = pause_media_fn
        self.remaining_rest_fn = remaining_rest_fn or (lambda: 0.0)
        self.contract_active_fn = contract_active_fn or (lambda: False)
        self.on_change = on_change
        self.on_complete = on_complete
        self.rng = rng or random.Random()
        self._username = ""

        self._data = self._load()
        now = datetime.now().replace(microsecond=0)
        self._last_tick = now
        self._last_save = now
        self._dirty = False
        self._ensure_day(now)

    # ---- 存档 ----

    def _load(self) -> dict:
        raw = config.load_json_file(self.path, {})
        data = raw if isinstance(raw, dict) else {}
        base = {
            "current": None,
            "extra": [],
            "last_draw": None,
            "last_pool": "",
            "last_type": "",
            "v_reset": False,
            "pending": [],
            "granted": [],
            "daily": {},
            "history": [],
        }
        base.update(data)
        for key in ("pending", "granted", "history"):
            if not isinstance(base.get(key), list):
                base[key] = []
        if not isinstance(base.get("daily"), dict):
            base["daily"] = {}
        if base.get("current") is not None and not isinstance(base.get("current"), dict):
            base["current"] = None
        extra = base.get("extra")
        base["extra"] = [e for e in extra if isinstance(e, dict)] if isinstance(extra, list) else []
        return base

    def save(self):
        if self.blocked_fn():
            return
        config.save_json_file(self.path, self._data)
        self._last_save = datetime.now()
        self._dirty = False

    def _save_if_due(self, now: datetime):
        if self._dirty and (now - self._last_save).total_seconds() >= SAVE_EVERY:
            self.save()

    def _changed(self):
        if self.on_change is None:
            return
        try:
            self.on_change()
        except Exception:
            pass

    # ---- 当天 ----

    def _ensure_day(self, now: datetime) -> dict:
        daily = self._data.setdefault("daily", {})
        day = now.strftime("%Y-%m-%d")
        if daily.get("date") == day:
            return daily
        for cur in list(self.actives()):
            if cur.get("type") == "IV":
                # 这一天结束了: 还没完成就作废.
                self._finish("expired", datetime.combine(now.date(), datetime.min.time()),
                             save=False, notify=False, settle=False, cur=cur)
        daily.clear()
        daily.update({
            "date": day,
            "n_ii": self.rng.randint(1, 20),
            "n_iv": self.rng.randint(5, 8),
            "completed_t": 0.0,
            "bonus": False,
            "iv_done": False,
        })
        self._dirty = True
        return daily

    def _daily(self, now: datetime) -> dict:
        return self._ensure_day(now)

    # ---- 当前指令 ----

    @property
    def current(self):
        cur = self._data.get("current")
        return cur if isinstance(cur, dict) else None

    def actives(self) -> list:
        """正在进行的指令, 先领的在前; 同时最多 KEEP 条."""
        out = [e for e in (self._data.get("extra") or []) if isinstance(e, dict)]
        cur = self.current
        if cur is not None:
            out.append(cur)
        return out

    def marks(self) -> set:
        """现在持有的指令对应哪些入口要标记: {"focus", "break", "contract", "stats"}."""
        return {MARK_OF[c.get("type")] for c in self.actives() if c.get("type") in MARK_OF}

    def username(self) -> str:
        if self._username:
            return self._username
        try:
            name = str(self.username_fn() or "").strip()
        except Exception:
            name = ""
        if name:
            self._username = name
        return name

    def can_draw(self, now: datetime = None) -> bool:
        """这个整点时段还能不能领取 (V 完成 / 作废后可以立即再领); 手里已经有 KEEP 条就领不了."""
        now = now or datetime.now()
        if len(self.actives()) >= KEEP:
            return False
        if self._dev():
            return True                                 # 开发者模式: 没有冷却
        if self._data.get("v_reset"):
            return True
        last = _parse(self._data.get("last_draw"))
        if last is None or last > now:
            return True
        boundary = last.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return now >= boundary

    def _dev(self) -> bool:
        try:
            return bool(self.dev_fn())
        except Exception:
            return False

    def next_draw_at(self, now: datetime = None):
        now = now or datetime.now()
        if self.can_draw(now):
            return None
        last = _parse(self._data.get("last_draw"))
        if last is None:
            return None
        return last.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    def available(self, kind: str, now: datetime = None) -> bool:
        """这条指令现在还能不能被抽到."""
        now = now or datetime.now()
        if kind == "IV":
            daily = self._daily(now)
            if daily.get("iv_done"):
                return False
            try:
                return float(self.today_hours_fn()) < float(daily.get("n_iv", 8)) - 1e-9
            except (TypeError, ValueError):
                return True
        if kind == "V":
            return self._page() == "prescript" and not self._minimized()
        if kind == "VI":
            try:
                return not self.contract_active_fn()     # 合约期间抽不到 VI
            except Exception:       # noqa: BLE001
                return True
        return True

    def _page(self) -> str:
        try:
            return str(self.page_fn() or "")
        except Exception:
            return ""

    def _minimized(self) -> bool:
        try:
            return bool(self.minimized_fn())
        except Exception:
            return True

    def _pool(self, now: datetime) -> str:
        mode = self.state_fn()
        return mode if mode in POOLS else "idle"

    def _weights(self, pool: str, now: datetime) -> list:
        weights = list(POOLS.get(pool, POOLS["idle"]))
        usable = [i for i, w in enumerate(weights) if w > 0 and self.available(TYPES[i], now)]
        if not usable:
            usable = [i for i, w in enumerate(weights) if w > 0]
        if not usable:
            usable = list(range(len(weights)))
        lost = sum(w for i, w in enumerate(weights) if w > 0 and i not in usable)
        for i in range(len(weights)):
            if i not in usable:
                weights[i] = 0.0
        if lost:
            share = lost / len(usable)
            for i in usable:
                weights[i] += share
        return weights

    def _pick(self, pool: str, now: datetime) -> str:
        weights = self._weights(pool, now)
        roll = self.rng.random()
        acc = 0.0
        for i, weight in enumerate(weights):
            if weight <= 0:
                continue
            acc += weight
            if roll < acc:
                return TYPES[i]
        for i in range(len(weights) - 1, -1, -1):
            if weights[i] > 0:
                return TYPES[i]
        return "I"

    # ---- 领取 ----

    def draw(self, now: datetime = None, choose: str = None, pool: str = None):
        """领取一条指令; 领不了时返回 None.

        choose / pool 只给自检用, 正常调用不带."""
        now = (now or datetime.now()).replace(microsecond=0)
        if not self.can_draw(now):
            return None
        self._ensure_day(now)
        # 上一条不作废, 留在后面继续判定 (同时最多 KEEP 条, can_draw 已经拦住了).
        if self.current is not None:
            self._data.setdefault("extra", []).append(self.current)
            self._data["current"] = None

        if pool is None:
            pool = self._pool(now)
            # 上一次是空闲池里的 V: 这一次从专注池抽.
            if self._data.get("last_pool") == "idle" and self._data.get("last_type") == "V":
                pool = "focus"
        kind = choose if choose in TYPES else self._pick(pool, now)
        daily = self._daily(now)
        n = None
        if kind == "I":
            body = "接下来的1h专注50min."
        elif kind == "II":
            n = int(daily.get("n_ii", 1))
            body = f"完成一次至少{n}级的合约."
        elif kind == "III":
            try:
                remaining = float(self.remaining_rest_fn())
            except Exception:       # noqa: BLE001
                remaining = 0.0
            n = rest_minutes(remaining)
            body = f"休息{n}min."
        elif kind == "IV":
            n = int(daily.get("n_iv", 5))
            body = f"今天专注至少{n} h"
        elif kind == "V":
            body = "立即停下任何活动,接下来的5min什么也不做."
        else:
            body = "检查你的统计数据,并作简要反思."

        if kind == "IV":
            expires = _midnight_after(now)
        else:
            expires = now + timedelta(seconds=EXPIRY_SECONDS.get(kind, 3600))

        cur = {
            "id": now.strftime("%Y%m%d%H%M%S") + f"-{self.rng.randrange(1000):03d}",
            "type": kind,
            "body": body,
            "accepted": _iso(now),
            "expires": _iso(expires),
            "pool": pool,
            "n": n,
            # III 的 t (小时) = 休息时间 (小时) × 2; 其他是固定值
            "t": (n / 60.0 * 2) if kind == "III" else float(FIXED_T.get(kind, 0.0)),
            "focus_sec": 0.0,
            "break_sec": 0.0,
            "stats_since": None,
        }
        self._data["current"] = cur
        self._data["last_draw"] = _iso(now)
        self._data["last_pool"] = pool
        self._data["last_type"] = kind
        self._data["v_reset"] = False
        self._last_tick = now
        self._dirty = True
        self.save()
        if kind == "V" and self.pause_media_fn is not None:
            try:
                self.pause_media_fn()
            except Exception:       # noqa: BLE001
                pass
        self._changed()
        return cur

    # ---- 完成 / 作废 ----

    def _finish(self, result: str, now: datetime, t=None, save: bool = True,
                notify: bool = True, settle: bool = True, cur: dict = None):
        cur = cur if cur is not None else self.current
        if cur is None:
            return None
        done = None
        if result == "completed":
            done = float(t if t is not None else cur.get("t") or 0.0)
        rec = {
            "id": cur.get("id", ""),
            "type": cur.get("type", ""),
            "body": cur.get("body", ""),
            "accepted": cur.get("accepted"),
            "ended": _iso(now),
            "result": result,
            "t": done,
            "code": code_for(done) if result == "completed" else "",
        }
        extra = self._data.get("extra") or []
        if cur is self.current:
            # 当前这条结束了: 后面留着的最新一条顶上来.
            self._data["current"] = extra.pop() if extra else None
        else:
            extra[:] = [e for e in extra if e is not cur]
        history = self._data.setdefault("history", [])
        history.append(rec)
        if len(history) > HISTORY_KEEP:
            del history[:-HISTORY_KEEP]

        if result == "completed":
            daily = self._daily(now)
            daily["completed_t"] = float(daily.get("completed_t", 0.0) or 0.0) + float(done)
            if rec["type"] == "IV":
                daily["iv_done"] = True
            self._data.setdefault("pending", []).append({
                "kind": "complete",
                "code": code_for(done),
                "t": done,
                "seconds": int(round(float(done) * 180)),
                "at": _iso(now),
            })
            # 今天的 t 合计到 10: 20 分钟当场排进待结算 (换日后打开页面也不会丢).
            if not daily.get("bonus") and \
                    float(daily.get("completed_t", 0.0) or 0.0) >= DAILY_T_TARGET - 1e-9:
                daily["bonus"] = True
                self._data.setdefault("pending", []).append({
                    "kind": "daily",
                    "code": rest_code(20),
                    "t": None,
                    "seconds": DAILY_BONUS_SECONDS,
                    "at": _iso(now),
                })
        self._dirty = True
        if save:
            self.save()
        if result == "completed" and settle and self._page() == "prescript":
            self.settle_rewards(now)
        if notify:
            self._changed()
        if result == "completed" and self.on_complete is not None:
            try:
                self.on_complete()
            except Exception:       # noqa: BLE001
                pass
        return rec

    # ---- 每秒判定 ----

    def check_visible(self, now: datetime = None):
        """V: 页面 / 窗口刚一变化就判一次 (不用等下一秒的 tick)."""
        if self._page() == "prescript" and not self._minimized():
            return
        for cur in list(self.actives()):
            if cur.get("type") == "V":
                now = (now or datetime.now()).replace(microsecond=0)
                self._data["v_reset"] = True
                self._finish("void", now, cur=cur)

    def page_changed(self, now: datetime = None):
        """切页 / 最小化时调用: V 立即作废, VI 的连续停留立即清零."""
        self.check_visible(now)
        for cur in self.actives():
            if cur.get("type") == "VI" and cur.get("stats_since"):
                if self._page() != "stats" or self._minimized():
                    cur["stats_since"] = None
                    self._dirty = True
                    self._save_if_due(now or datetime.now())

    def _matching_contract(self, cur: dict):
        """II: 领取之后完成、等级够的下一次合约 (取最早完成的那一个)."""
        accepted = _parse(cur.get("accepted"))
        expires = _parse(cur.get("expires"))
        try:
            need = int(cur.get("n") or 0)
        except (TypeError, ValueError):
            need = 0
        best, best_end = None, None
        for rec in self.contracts_fn() or []:
            if not isinstance(rec, dict) or rec.get("result") != "completed":
                continue
            end = _parse(rec.get("end"))
            if end is None or accepted is None or end <= accepted:
                continue
            if expires is not None and end > expires:
                continue
            try:
                level = int(rec.get("level") or 0)
            except (TypeError, ValueError):
                level = 0
            if level < need:
                continue
            if best_end is None or end < best_end:
                best, best_end = rec, end
        return best

    def tick(self, now: datetime = None):
        """每秒: 累计专注 / 休息, 看合约 / 统计页 / 当天专注, 处理作废."""
        now = (now or datetime.now()).replace(microsecond=0)
        if self.blocked_fn():
            return
        self._ensure_day(now)
        prev = self._last_tick
        delta = max(0.0, (now - prev).total_seconds())
        self._last_tick = now

        for cur in list(self.actives()):
            self._tick_one(cur, now, prev, delta)
        self._save_if_due(now)

    def _tick_one(self, cur: dict, now: datetime, prev: datetime, delta: float):
        kind = cur.get("type", "")
        expires = _parse(cur.get("expires"))

        if kind == "V":
            if self._page() != "prescript" or self._minimized():
                self._data["v_reset"] = True
                self._finish("void", now, cur=cur)
                return
            if expires is not None and now >= expires:
                self._data["v_reset"] = True
                self._finish("completed", now, 0.1, cur=cur)
                return
        elif kind == "I":
            # 只算到作废时刻为止: 过了 1h 之后的那一段不能算进 50min.
            gain = delta
            if expires is not None:
                cutoff = min(now, expires)
                gain = max(0.0, (cutoff - prev).total_seconds()) if cutoff > prev else 0.0
            if self.state_fn() == "focus":
                cur["focus_sec"] = float(cur.get("focus_sec", 0.0) or 0.0) + gain
                self._dirty = True
            if float(cur.get("focus_sec", 0.0) or 0.0) >= FOCUS_TARGET:
                self._finish("completed", now, 1.0, cur=cur)
                return
            if expires is not None and now >= expires:
                self._finish("expired", now, cur=cur)
                return
        elif kind == "III":
            gain = delta
            if expires is not None:
                cutoff = min(now, expires)
                gain = max(0.0, (cutoff - prev).total_seconds()) if cutoff > prev else 0.0
            if self.state_fn() == "break":
                cur["break_sec"] = float(cur.get("break_sec", 0.0) or 0.0) + gain
                self._dirty = True
            target = float(cur.get("n") or 0) * 60 or BREAK_TARGET
            if float(cur.get("break_sec", 0.0) or 0.0) >= target:
                self._finish("completed", now, float(cur.get("t") or 1.0), cur=cur)
                return
            if expires is not None and now >= expires:
                self._finish("expired", now, cur=cur)
                return
        elif kind == "II":
            rec = self._matching_contract(cur)
            if rec is not None:
                t = contract_duration_hours(rec)
                self._finish("completed", now, t, cur=cur)
                return
            if expires is not None and now >= expires:
                self._finish("expired", now, cur=cur)
                return
        elif kind == "IV":
            daily = self._daily(now)
            try:
                hours = float(self.today_hours_fn())
            except (TypeError, ValueError):
                hours = 0.0
            if hours >= float(daily.get("n_iv", 0) or 0) - 1e-9:
                self._finish("completed", now, 1.0, cur=cur)
                return
            if expires is not None and now >= expires:
                self._finish("expired", now, cur=cur)
                return
        elif kind == "VI":
            if self._page() == "stats" and not self._minimized():
                if not cur.get("stats_since"):
                    cur["stats_since"] = _iso(now)
                    self._dirty = True
                since = _parse(cur.get("stats_since"))
                if since is not None and (now - since).total_seconds() >= REFLECT_TARGET:
                    self._finish("completed", now, 1.0, cur=cur)
                    return
            elif cur.get("stats_since"):
                cur["stats_since"] = None
                self._dirty = True
            if expires is not None and now >= expires:
                self._finish("expired", now, cur=cur)
                return

    # ---- 结算休息时间 ----

    def settle_rewards(self, now: datetime = None) -> list:
        """打开指令页时调用: 把满足条件的休息时间加进计时器, 返回这次给出的代码."""
        now = (now or datetime.now()).replace(microsecond=0)
        daily = self._daily(now)
        if not daily.get("bonus") and float(daily.get("completed_t", 0.0) or 0.0) >= DAILY_T_TARGET - 1e-9:
            daily["bonus"] = True
            self._data.setdefault("pending", []).append({
                "kind": "daily",
                "code": rest_code(20),
                "t": None,
                "seconds": DAILY_BONUS_SECONDS,
                "at": _iso(now),
            })
            self._dirty = True

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
        self._data["pending"] = []
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


def _selftest():
    import shutil
    import tempfile

    root = tempfile.mkdtemp(prefix="oit_prescript_")
    state = {"mode": "focus", "page": "prescript", "min": False}
    contracts = []
    granted = []
    today = {"hours": 0.0}
    try:
        eng = PrescriptEngine(
            root,
            state_fn=lambda: state["mode"],
            today_hours_fn=lambda: today["hours"],
            contracts_fn=lambda: contracts,
            page_fn=lambda: state["page"],
            minimized_fn=lambda: state["min"],
            grant_rest_fn=lambda s, code: granted.append((s, code)),
            username_fn=lambda: "测试",
            rng=random.Random(7),
        )

        # 概率: 每个池子加起来是 1; IV 已经达成时它的概率平分给池子里其他指令
        for pool, weights in POOLS.items():
            assert abs(sum(weights) - 1.0) < 1e-9, pool
            assert len(weights) == len(TYPES)
        today["hours"] = 9.0
        weights = eng._weights("focus", datetime.now())
        assert abs(sum(weights) - 1.0) < 1e-9
        assert weights[TYPES.index("IV")] == 0.0
        assert abs(weights[TYPES.index("I")] - (0.8 + 0.1 / 4)) < 1e-9
        today["hours"] = 0.0

        # 代码格式
        assert code_for(1) == "REST3" and code_for(0.1) == "REST0.3"
        assert pad_code(type_code("I")) == "_FOCUS%." and len(pad_code(type_code("II"))) == CODE_WIDTH
        assert pad_code("NONE") == "_%NONE%." and pad_code("REST3") == "_REST3%."
        assert pad_code("REST20") == "_REST20." and pad_code("REST0.3") == "REST0.3."
        assert pad_code("1h*3min") == "_REST3%." and pad_code("20min") == "_REST20."
        assert all(len(pad_code(c)) == CODE_WIDTH for c in ("A", "REST120", "REST1200", "REST12345678"))
        assert fmt_clock(65) == "01:05" and fmt_clock(3665) == "1:01:05"

        # 整点领取: 同一小时只能领一次
        t0 = datetime.now().replace(minute=5, second=0, microsecond=0)
        assert eng.can_draw(t0)
        cur = eng.draw(t0, choose="I")
        assert cur and cur["type"] == "I" and eng.current is cur
        assert not eng.can_draw(t0 + timedelta(minutes=10))
        assert eng.can_draw(t0 + timedelta(hours=1))

        # I: 专注 50 分钟完成; 当时不在指令页, 回到页面时结算 t*3min
        state["mode"] = "focus"
        state["page"] = "timer"
        eng._last_tick = t0
        eng.tick(t0 + timedelta(minutes=49))
        assert eng.current is not None
        eng.tick(t0 + timedelta(minutes=50))
        assert eng.current is None
        assert eng._data["pending"] and eng._data["pending"][0]["code"] == "REST3"
        state["page"] = "prescript"
        given = eng.settle_rewards(t0 + timedelta(minutes=51))
        assert given and given[0]["seconds"] == 180
        assert granted[-1] == (180, "REST3")

        # 新的一小时: 上一条没完成也可以再领, 两条一起持有; 每个入口按持有的指令标记
        t1 = t0 + timedelta(hours=1, minutes=1)
        cur = eng.draw(t1, choose="III")
        assert cur and cur["type"] == "III" and eng.marks() == {"break"}
        rec = eng.draw(t1 + timedelta(hours=1), choose="I")
        assert rec and [c["type"] for c in eng.actives()] == ["III", "I"]
        assert eng.marks() == {"break", "focus"}
        assert not any(h["result"] == "superseded" for h in eng._data["history"])
        eng._data["extra"] = []

        # V: 离开指令页立即作废, 可以马上再领
        eng._data["current"] = None
        cur = eng.draw(t1 + timedelta(hours=2), choose="V")
        assert cur and cur["type"] == "V"
        state["page"] = "stats"
        eng.tick(t1 + timedelta(hours=2, seconds=20))
        assert eng.current is None and eng._data["history"][-1]["result"] == "void"
        assert eng._data["v_reset"] and eng.can_draw(t1 + timedelta(hours=2, seconds=21))
        cur = eng.draw(t1 + timedelta(hours=2, seconds=22), choose="I")
        assert cur and cur["type"] == "I"
        assert eng._data["last_pool"] == "focus", "V 之后抽专注池"

        # 上一次是空闲池的 V: 下一次强制专注池
        eng._data["current"] = None
        eng._data["last_pool"], eng._data["last_type"] = "idle", "V"
        cur = eng.draw(t1 + timedelta(hours=4), choose="I")
        assert cur and cur["pool"] == "focus" and eng._data["last_pool"] == "focus"

        # II: 下一次正常完成的合约, t = 总时长 (2h)
        eng._data["current"] = None
        state["page"] = "prescript"
        cur = eng.draw(t1 + timedelta(hours=5), choose="II")
        assert cur and cur["type"] == "II"
        n = int(cur["n"])
        contracts.append({
            "id": "c1", "result": "completed", "level": n,
            "start": _iso(t1 + timedelta(hours=5, minutes=5)),
            "planned": _iso(t1 + timedelta(hours=7, minutes=5)),
            "end": _iso(t1 + timedelta(hours=7, minutes=5)),
        })
        state["page"] = "timer"
        eng.tick(t1 + timedelta(hours=7, minutes=6))
        assert eng.current is None and eng._data["history"][-1]["t"] == 2.0
        assert eng._data["pending"][-1]["code"] == "REST6"

        # VI: 统计页连续 3 分钟
        eng._data["current"] = None
        state["page"] = "prescript"
        cur = eng.draw(t1 + timedelta(hours=9), choose="VI")
        assert cur and cur["type"] == "VI"
        state["page"] = "stats"
        eng._last_tick = t1 + timedelta(hours=9)
        eng.tick(t1 + timedelta(hours=9, minutes=1))
        assert eng.current is not None
        eng.tick(t1 + timedelta(hours=9, minutes=4))
        assert eng.current is None and eng._data["history"][-1]["result"] == "completed"

        # 今天完成的 t 合计到 10: 20 分钟
        eng._data["daily"]["completed_t"] = 10.0
        before = len(granted)
        given = eng.settle_rewards(t1 + timedelta(hours=10))
        assert any(g["code"] == "REST20" and g["seconds"] == 1200 for g in given)
        assert len(granted) >= before + 1
        assert eng.settle_rewards(t1 + timedelta(hours=10, minutes=1)) == []

        # 过了 1h 才醒来的 tick: 只算到作废时刻, 不会把 1h 之后的专注补进来
        eng._data["current"] = None
        state["page"] = "prescript"
        state["mode"] = "break"
        t2 = t1 + timedelta(hours=20)
        cur = eng.draw(t2, choose="I")
        assert cur and cur["type"] == "I"
        eng._last_tick = t2
        eng.tick(t2 + timedelta(minutes=59))
        state["mode"] = "focus"
        eng.tick(t2 + timedelta(hours=2))
        assert eng.current is None and eng._data["history"][-1]["result"] == "expired"

        # 合约期间抽不到 VI: 它的概率平分给同池其他还能抽到的指令
        eng.contract_active_fn = lambda: True
        assert eng._weights("focus", t2)[5] == 0.0 and abs(sum(eng._weights("focus", t2)) - 1.0) < 1e-9
        assert eng._weights("break", t2)[5] == 0.0
        eng.contract_active_fn = lambda: False
        assert eng._weights("focus", t2)[5] > 0.0

        # III: 休息分钟数 = 剩余休息时间的 50%, 最多 30 分钟; t = 休息时间 (小时) × 2
        assert rest_minutes(3000) == 25 and rest_minutes(7200) == 30 and rest_minutes(0) == 1
        eng._data["current"] = None
        eng._data["last_draw"] = None
        eng.remaining_rest_fn = lambda: 3000
        c3 = eng.draw(t2 + timedelta(hours=40), choose="III")
        assert c3["n"] == 25 and c3["body"] == "休息25min." and abs(c3["t"] - 25 / 30) < 1e-9
        eng.remaining_rest_fn = lambda: 0.0
        eng._data["current"] = None

        # V 被领取时暂停后台的音频 / 视频
        paused = []
        eng.pause_media_fn = lambda: paused.append(1)
        t_v = t2 + timedelta(hours=60)
        eng.draw(t_v, choose="I")
        assert not paused
        eng.draw(t_v + timedelta(hours=2), choose="V")
        assert paused == [1]
        eng.pause_media_fn = None
        eng._data["current"] = None

        # 完成时通知 (播完成音效); 每个入口的标记: I / IV 专注, III 休息, II 合约, VI 统计, V 不标记
        done = []
        eng.on_complete = lambda: done.append(1)
        t_m = t2 + timedelta(hours=50)
        for kind in ("II", "VI", "IV", "V"):
            eng._data["current"] = None
            eng._data["extra"] = []
            eng.draw(t_m, choose=kind)
            assert eng.marks() == ({"contract"} if kind == "II" else {"stats"} if kind == "VI"
                                   else {"focus"} if kind == "IV" else set()), kind
            t_m += timedelta(hours=1)
        eng._data["current"] = None
        eng._data["extra"] = []
        eng.draw(t_m, choose="V")
        eng.tick(t_m + timedelta(minutes=6))
        assert done == [1] and eng.current is None
        eng.on_complete = None

        # 开发者模式: 没有冷却, 同时最多留 KEEP 条, 满了新的领不了; 每条各自判定.
        dev = {"on": True}
        eng.dev_fn = lambda: dev["on"]
        eng._data["current"] = None
        eng._data["extra"] = []
        eng._data["last_draw"] = None
        state["mode"] = "idle"
        t3 = t2 + timedelta(hours=30)
        got = [eng.draw(t3, choose="III"), eng.draw(t3, choose="I"), eng.draw(t3, choose="VI")]
        assert all(got) and len(eng.actives()) == KEEP and not eng.can_draw(t3)
        assert eng.draw(t3, choose="I") is None and len(eng.actives()) == KEEP
        eng._last_tick = t3
        state["mode"] = "break"
        eng.tick(t3 + timedelta(minutes=20, seconds=1))
        left = [c["type"] for c in eng.actives()]
        assert left == ["I", "VI"] and eng._data["history"][-1]["type"] == "III", left
        assert eng.can_draw(t3 + timedelta(minutes=21))
        dev["on"] = False
        # 不是开发者模式: 还有冷却, 而且手里满了 KEEP 条就领不了, 没满也不会顶掉旧的
        eng._data["current"] = None
        eng._data["extra"] = []
        eng._data["last_draw"] = None
        t4 = t3 + timedelta(hours=2)
        assert eng.draw(t4, choose="III") is not None and len(eng.actives()) == 1
        assert not eng.can_draw(t4 + timedelta(minutes=5))
        assert eng.draw(t4 + timedelta(hours=1), choose="I") is not None
        assert eng.draw(t4 + timedelta(hours=2), choose="VI") is not None and len(eng.actives()) == KEEP
        assert not eng.can_draw(t4 + timedelta(hours=3))
        assert eng.draw(t4 + timedelta(hours=3), choose="II") is None and len(eng.actives()) == KEEP
        eng._data["current"] = None
        eng._data["extra"] = []

        # 存档再读出来: 当前 / 历史还在
        eng.save()
        eng2 = PrescriptEngine(root, state_fn=lambda: "idle", today_hours_fn=lambda: 0.0)
        assert eng2._data["history"]
        return "prescript selftest OK"
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    print(_selftest())
