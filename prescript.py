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
from i18n import tr


TYPES = ("I", "II", "III", "IV", "V", "VI")      # 池子里的六列; V 是空闲词条 (见 IDLE_ENTRIES)

# 每个池子里六条指令的概率: I, II, III, IV, V, VI
POOLS = {
    "focus": (0.8, 0.03, 0.03, 0.10, 0.00, 0.04),
    "break": (0.0, 0.10, 0.70, 0.10, 0.05, 0.05),
    "idle":  (0.05, 0.01, 0.01, 0.02, 0.90, 0.01),
}

# 领取后多久作废 (秒); IV 在这一天结束时作废, 由日期换日处理. 空闲词条 (IDLE_ENTRIES) 没有时限.
EXPIRY_SECONDS = {
    "I": 60 * 60,
    "II": 8 * 60 * 60,
    "III": 60 * 60,
    "VI": 60 * 60,
}

# 空闲池里 V 那 0.9 由这几个词条分; 每个词条: 正文 / 自动完成要几分钟 (0 = 没有计时) / 蓝色代码的词.
# 设备时间的哪几段里它的概率被抬高在设置 → 日常 里改 (config.PRESCRIPT_WIN_*, 默认值在 config.PRESCRIPT_WIN_DEFAULTS).
# t = 分钟数 × 0.02 (5 分钟 0.1); X 没有计时, t 固定 0.1.
IDLE_ENTRIES = {
    "V": {"body": "立即停下任何活动,接下来的5min什么也不做.", "minutes": 5, "word": "STOP"},
    "VII": {"body": "到开阔场地进行运动,持续20min.", "minutes": 20, "word": "EXERCISE"},
    "VIII": {"body": "进行一次淋浴.", "minutes": 10, "word": "SHOWER"},
    "IX": {"body": "凝视天空.", "minutes": 5, "word": "GAZE"},
    "X": {"body": "记录,核对并反思这一天的活动.", "minutes": 0, "word": "RECORD"},
    "XI": {"body": "打扫并整理房间.", "minutes": 15, "word": "CLEAN"},
}
IDLE_KINDS = tuple(IDLE_ENTRIES)
ALL_TYPES = TYPES + tuple(k for k in IDLE_KINDS if k not in TYPES)
IDLE_BOOST = 0.8                # 时间段对得上的词条 (几个就均分) 拿走这一格概率的 80%, 其余词条平分剩下的
FOCUS_CHANCE_START = 0.25       # 每次空闲池抽取之后, 下一次改抽专注池的概率; 没触发 +25%, 触发后回到 25%
FOCUS_CHANCE_STEP = 0.25
LOG_KEEP_DAYS = 30
PAGE_EVENT_FRESH = 180          # 其他设备进入 / 退出指令页的事件, 多少秒之内收到才跟着做 (太旧的是以前留下的, 不理)

FIXED_T = {"I": 1.0, "III": 1.0, "IV": 1.0, "VI": 1.0}
FIXED_T.update({k: (0.1 if not e["minutes"] else round(e["minutes"] * 0.02, 3))
                for k, e in IDLE_ENTRIES.items()})
FOCUS_TARGET = 50 * 60          # I: 50 分钟专注
BREAK_TARGET = 20 * 60          # III (旧存档里没有分钟数的): 20 分钟休息
REST_SHARE = 0.5                # III: 要休息的分钟数 = 用户剩余休息时间的 50%
REST_MAX_MIN = 30               # III: 最多 30 分钟
REFLECT_TARGET = 3 * 60         # VI: 统计页连续停留 3 分钟
DAILY_T_TARGET = 10.0           # 今天完成的指令 t 合计到 10
DAILY_BONUS_SECONDS = 20 * 60   # 额外 20 分钟休息

# 每条指令的蓝色代码用的词 (补位后正好 8 个字符以内)
TYPE_WORDS = {"I": "FOCUS", "II": "CONTRACT", "III": "REST", "IV": "DAILY", "VI": "REFLECT"}
TYPE_WORDS.update({k: e["word"] for k, e in IDLE_ENTRIES.items()})
CODE_WIDTH = 8                  # 蓝色代码的宽度 (和网页上的指令代码一样, 8 个字符)
CODE_LEFT = "_%"                # 左边补位, 从最外面一格开始依次用
CODE_RIGHT = "%."               # 右边补位, 从最里面一格往外依次用 (最后一格是 .)

KEEP = 3                        # 每个用户同时最多持有几条指令, 满了就不能再领 (没有领取冷却)
DEV_KEEP = KEEP                 # 旧名字

# 每种指令对应界面上哪个入口 (在那个按钮上标记): 专注 / 休息 开始按钮, 合约 / 统计 侧边栏入口; IV / V 没有标记
MARK_OF = {"I": "focus", "III": "break", "II": "contract", "VI": "stats"}
HISTORY_KEEP = 100
SYNC_DAYS = 3                   # 同步给其他设备的指令记录: 最近这么多天的
SYNC_VOLATILE = ("focus_sec", "break_sec", "stats_since", "log_base")   # 各设备自己累计的进度, 不同步
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
    dev_fn()          -> 开发者模式 (现在领取对谁都没有冷却, 这个只留着兼容)
    pause_media_fn()  -> 领取空闲词条 (IDLE_ENTRIES) 时暂停后台正在播放的音频 / 视频
    hour_log_fn()     -> 今天的整点记录 (没启用返回空串), 给 X 词条的输入框第一段
    peers_on_page_fn() -> 绑定的其他设备里有没有哪台此刻正停在指令页 (空闲词条要所有设备都离开才作废)
    contract_active_fn() -> 现在有没有进行中的合约 (合约期间抽不到 VI)
    on_complete()     -> 有一条指令被完成了 (播完成音效)
    remaining_rest_fn() -> 用户剩余的休息时间 (秒): 休息中是这次休息的倒计时, 否则是积攒的休息时间
    """

    def __init__(self, data_dir, *, state_fn=None, today_hours_fn=None, contracts_fn=None,
                 page_fn=None, minimized_fn=None, grant_rest_fn=None, username_fn=None,
                 blocked_fn=None, on_change=None, dev_fn=None, pause_media_fn=None, remaining_rest_fn=None, contract_active_fn=None, on_complete=None, hour_log_fn=None, peers_on_page_fn=None, page_event_fn=None, rng=None):
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
        self.hour_log_fn = hour_log_fn or (lambda: "")
        self.peers_on_page_fn = peers_on_page_fn or (lambda: False)
        # 其他设备进入 / 退出了指令页 (事件): page_event_fn(on) 让本机跟着进入 / 退出 (MECHANICS 22.8)
        self.page_event_fn = page_event_fn
        self._page_rec = None           # 最近一次进入 / 退出指令页的事件 {"on", "at", "by"}, 随同步; 不存档
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
            "last_pool": "",
            "last_type": "",
            "run": [],                  # 连续的空闲池抽取里已经抽到过的指令 (空闲5min 除外)
            "force_focus": False,       # 下一次抽取改抽专注池
            "focus_chance": FOCUS_CHANCE_START,
            "closes": [],               # OIT 被手动关闭的时刻
            "logs": {},                 # X 词条保存的记录 {日期: 文字}
            "pending": [],
            "settled": {},              # 已经结算过的奖励 {id: 奖励} (同步给其他设备, 免得重复结算)
            "granted": [],
            "daily": {},
            "history": [],
        }
        base.update(data)
        for key in ("pending", "granted", "history", "run", "closes"):
            if not isinstance(base.get(key), list):
                base[key] = []
        for key in ("daily", "logs", "settled"):
            if not isinstance(base.get(key), dict):
                base[key] = {}
        try:
            base["focus_chance"] = min(1.0, max(0.0, float(base.get("focus_chance"))))
        except (TypeError, ValueError):
            base["focus_chance"] = FOCUS_CHANCE_START
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
        self._data["run"] = []
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
        """现在能不能领: 没有冷却, 但手里已经有 KEEP 条就领不了; 专注指令 (I) 没完成 / 作废之前也不能领新的."""
        acts = self.actives()
        if len(acts) >= KEEP:
            return False
        return not any(c.get("type") == "I" for c in acts)

    def _dev(self) -> bool:
        try:
            return bool(self.dev_fn())
        except Exception:
            return False

    def next_draw_at(self, now: datetime = None):
        """旧接口: 现在没有按整点的冷却, 永远是 None."""
        return None

    def available(self, kind: str, now: datetime = None) -> bool:
        """这条指令现在还能不能被抽到."""
        now = now or datetime.now()
        if kind == "IV":
            daily = self._daily(now)
            if daily.get("iv_done") or self.active_of("IV") is not None:
                return False                                # 今天完成过 / 手里已经有一条: 不能再领
            try:
                return float(self.today_hours_fn()) < float(daily.get("n_iv", 8)) - 1e-9
            except (TypeError, ValueError):
                return True
        if kind in IDLE_ENTRIES:
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
        if mode in ("focus", "break"):
            return mode
        try:
            in_contract = bool(self.contract_active_fn())
        except Exception:       # noqa: BLE001
            in_contract = False
        if in_contract:
            return self.rng.choice(("focus", "break"))    # 合约期间空闲: 随机抽专注 / 休息池
        return "idle"

    def _run(self) -> list:
        run = self._data.get("run")
        return run if isinstance(run, list) else []

    def _weights(self, pool: str, now: datetime) -> list:
        weights = list(POOLS.get(pool, POOLS["idle"]))
        run = set(self._run()) if pool == "idle" else set()   # 连续空闲抽取里抽到过的 (V 除外) 不再抽
        usable = [i for i, w in enumerate(weights)
                  if w > 0 and TYPES[i] not in run and self.available(TYPES[i], now)]
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

    @staticmethod
    def in_window(kind: str, now: datetime) -> bool:
        """设备现在的时间落在这个词条的时间段里吗."""
        minute = now.hour * 60 + now.minute
        return any(a <= minute < b for a, b in config.prescript_windows(kind))

    def idle_boosts(self, now: datetime) -> list:
        """现在概率被抬高的空闲词条们: 时间段对得上、而且这一轮还没抽到过的 (V 可以重复, 不看这一轮);
        时段有重叠就有好几个, 没有就是 V (空闲5min)."""
        run = set(self._run())
        hit = [k for k in IDLE_KINDS if (k == "V" or k not in run) and self.in_window(k, now)]
        return hit or ["V"]

    def _idle_split(self, mass: float, now: datetime) -> dict:
        """空闲池里 V 这一格的概率 (mass) 拆给各个空闲词条: 抬高的 (重叠就几个均分) 拿 80%, 其余还能抽的平分剩下的."""
        run = set(self._run())
        cands = [k for k in IDLE_KINDS if k == "V" or k not in run]
        boosted = self.idle_boosts(now)
        others = [k for k in cands if k not in boosted]
        if not others:
            return {k: mass / len(boosted) for k in boosted}
        out = {k: mass * IDLE_BOOST / len(boosted) for k in boosted}
        for kind in others:
            out[kind] = mass * (1.0 - IDLE_BOOST) / len(others)
        return out

    def choices(self, pool: str, now: datetime = None) -> list:
        """这个池子现在每条指令的概率 [(类型, 概率)]; 空闲池里 V 那一格已经拆给各个空闲词条."""
        now = now or datetime.now()
        weights = self._weights(pool, now)
        out = []
        for i, kind in enumerate(TYPES):
            if weights[i] <= 0:
                continue
            if kind == "V" and pool == "idle":
                out.extend(self._idle_split(weights[i], now).items())
            else:
                out.append((kind, weights[i]))
        return out

    def _pick(self, pool: str, now: datetime) -> str:
        options = self.choices(pool, now)
        roll = self.rng.random()
        acc = 0.0
        for kind, weight in options:
            acc += weight
            if roll < acc:
                return kind
        return options[-1][0] if options else "I"

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
            # 上一次空闲池抽取触发了: 这一次改抽专注池 (不看当时的状态).
            if self._data.get("force_focus"):
                pool = "focus"
            else:
                pool = self._pool(now)
            self._data["force_focus"] = False
        if pool == "idle" and self._data.get("last_pool") != "idle":
            self._data["run"] = []                    # 重新进入空闲池: 这一轮重新算
        kind = choose if choose in ALL_TYPES else self._pick(pool, now)
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
        elif kind in IDLE_ENTRIES:
            body = IDLE_ENTRIES[kind]["body"]
        else:
            body = "检查你的统计数据,并作简要反思."

        expires = due = None
        if kind == "IV":
            expires = _midnight_after(now)
        elif kind in IDLE_ENTRIES:
            minutes = IDLE_ENTRIES[kind]["minutes"]      # 空闲词条没有作废时限, 只有自动完成的时刻
            due = now + timedelta(minutes=minutes) if minutes else None
        else:
            expires = now + timedelta(seconds=EXPIRY_SECONDS.get(kind, 3600))

        cur = {
            "id": now.strftime("%Y%m%d%H%M%S") + f"-{self.rng.randrange(1000):03d}",
            "type": kind,
            "body": body,
            "accepted": _iso(now),
            "expires": _iso(expires) if expires else None,
            "due": _iso(due) if due else None,
            "pool": pool,
            "n": n,
            # III 的 t (小时) = 休息时间 (小时) × 2; 其他是固定值
            "t": (n / 60.0 * 2) if kind == "III" else float(FIXED_T.get(kind, 0.0)),
            "focus_sec": 0.0,
            "break_sec": 0.0,
            "stats_since": None,
        }
        self._data["current"] = cur
        self._data["last_pool"] = pool
        self._data["last_type"] = kind
        if pool == "idle":
            if kind != "V":
                self._data.setdefault("run", []).append(kind)
            # 每次空闲池抽取都有一定概率让下一次改抽专注池, 没触发概率 +25%, 触发后回到 25%
            chance = float(self._data.get("focus_chance", FOCUS_CHANCE_START))
            if self.rng.random() < chance:
                self._data["force_focus"] = True
                self._data["focus_chance"] = FOCUS_CHANCE_START
            else:
                self._data["focus_chance"] = min(1.0, chance + FOCUS_CHANCE_STEP)
        self._last_tick = now
        self._dirty = True
        self.save()
        if kind in IDLE_ENTRIES and self.pause_media_fn is not None:
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
            self._add_reward({
                "id": rec["id"],
                "kind": "complete",
                "code": code_for(done),
                "t": done,
                "seconds": int(round(float(done) * 180)),
                "at": _iso(now),
            })
            self._recompute_daily(now)
            self._check_daily_bonus(now)
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
        """空闲词条: 页面 / 窗口刚一变化就判一次 (不用等下一秒的 tick)."""
        if self.on_page() or self._peers_on_page():
            return
        for cur in list(self.actives()):
            if cur.get("type") in IDLE_ENTRIES:
                now = (now or datetime.now()).replace(microsecond=0)
                self._finish("void", now, cur=cur)

    def page_changed(self, now: datetime = None):
        """切页 / 最小化时调用: 空闲词条立即作废, VI 的连续停留立即清零."""
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

        if kind in IDLE_ENTRIES:
            here = self.on_page()
            if not here and not self._peers_on_page():
                self._finish("void", now, cur=cur)               # 所有设备都离开了指令页 / 最小化: 作废
                return
            # 自动完成的时刻 (旧存档里的 V 只有 expires); X 没有计时, 等输入框保存.
            # 只有停在指令页的设备去完成 (别的设备还停在页面上时, 由它来完成)
            due = _parse(cur.get("due")) or _parse(cur.get("expires"))
            if here and due is not None and now >= due:
                self._finish("completed", now, float(cur.get("t") or FIXED_T.get(kind, 0.1)), cur=cur)
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

    def on_page(self) -> bool:
        """本机此刻是不是停在指令页上 (没有最小化)."""
        return self._page() == "prescript" and not self._minimized()

    def _peers_on_page(self) -> bool:
        try:
            return bool(self.peers_on_page_fn())
        except Exception:       # noqa: BLE001
            return False

    # ---- 奖励 / 当天合计 (跟着记录走, 多台设备算出来一样) ----

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

    # ---- 设备同步 (devsync 的 "prescript" 文档) ----
    # 同步的是: 领到的指令 (进行中的和已经完成 / 作废的; 各设备自己累计的进度不同步), 奖励 (结算过没有), 今天的 n.
    # 已经结束的记录永远盖过进行中的; 其他按修改时间.

    @staticmethod
    def _static(rec: dict) -> dict:
        return {k: v for k, v in rec.items() if k not in SYNC_VOLATILE}

    def journal_view(self) -> str:
        """统计页的日志上一次被看的时刻 ("YYYY-MM-DDTHH:MM:SS", 没看过 = ""); 随同步, 几台设备共用 2 小时的冷却 (MECHANICS 8.7)."""
        return str(self._data.get("journal_view") or "")

    def note_journal(self, now: datetime = None):
        now = (now or datetime.now()).replace(microsecond=0)
        self._data["journal_view"] = _iso(now)
        self._dirty = True
        self.save()

    def note_page(self, on: bool, device_id: str, now: datetime = None):
        """本机进入 / 退出了指令页: 记成一个事件, 随同步发给其他设备, 它们跟着进入 / 退出 (调用的人负责马上同步一次)."""
        now = (now or datetime.now()).replace(microsecond=0)
        self._page_rec = {"on": bool(on), "at": _iso(now), "by": str(device_id or "")}

    def _adopt_page(self, val: dict, now: datetime) -> None:
        """别的设备发来的进入 / 退出指令页的事件: 记下 (本机之后不会又发回去), 够新的话让本机跟着做."""
        if self._page_rec == {"on": bool(val.get("on")), "at": str(val.get("at") or ""), "by": str(val.get("by") or "")}:
            return
        self._page_rec = {"on": bool(val.get("on")), "at": str(val.get("at") or ""), "by": str(val.get("by") or "")}
        at = _parse(val.get("at"))
        if at is None or abs((now - at).total_seconds()) > PAGE_EVENT_FRESH or self.page_event_fn is None:
            return
        try:
            self.page_event_fn(bool(val.get("on")))
        except Exception:       # noqa: BLE001
            pass

    def sync_doc(self, now: datetime = None) -> dict:
        now = (now or datetime.now()).replace(microsecond=0)
        floor = _iso(now - timedelta(days=SYNC_DAYS))
        out = {}
        for rec in self._data.get("history") or []:
            if isinstance(rec, dict) and rec.get("id") and str(rec.get("accepted") or "") >= floor:
                out["i:" + str(rec["id"])] = dict(rec)
        for cur in self.actives():
            if cur.get("id"):
                out["i:" + str(cur["id"])] = self._static(cur)
        for item in self._data.get("pending") or []:
            if isinstance(item, dict):
                out["r:" + self._reward_id(item)] = dict(item, settled=False)
        for rid, item in (self._data.get("settled") or {}).items():
            if isinstance(item, dict):
                out["r:" + str(rid)] = dict(item, settled=True)
        daily = self._data.get("daily") or {}
        if daily.get("date"):
            out["n:" + str(daily["date"])] = {"n_ii": daily.get("n_ii"), "n_iv": daily.get("n_iv")}
        if self._page_rec:
            out["p:page"] = dict(self._page_rec)
        if self.journal_view():
            out["j:view"] = {"at": self.journal_view()}
        return out

    @staticmethod
    def sync_done(value) -> bool:
        """这一项是不是已经到头了 (指令结束 / 奖励结算过): 到头的盖过没到头的."""
        return isinstance(value, dict) and (bool(value.get("result")) or value.get("settled") is True)

    def _all_ids(self) -> set:
        return {str(r.get("id")) for r in (self._data.get("history") or []) if isinstance(r, dict)}

    def sync_apply(self, changes: dict, now: datetime = None):
        """别的设备发来的改动并进来 (界面线程). 完成 / 作废不再给奖励 (奖励有自己的一项), 也不响."""
        now = (now or datetime.now()).replace(microsecond=0)
        floor = _iso(now - timedelta(days=SYNC_DAYS))
        touched = False
        for key, val in (changes or {}).items():
            if not isinstance(val, dict):
                continue
            kind, _, ident = str(key).partition(":")
            if kind == "i":
                touched |= self._adopt_instruction(ident, val, floor)
            elif kind == "r":
                touched |= self._adopt_reward(ident, val)
            elif kind == "p" and ident == "page":
                self._adopt_page(val, now)
            elif kind == "j" and ident == "view":
                at = str(val.get("at") or "")
                if at > self.journal_view():                 # 几台设备谁看得最晚, 冷却就从谁算
                    self._data["journal_view"] = at
                    touched = True
            elif kind == "n" and ident == (self._data.get("daily") or {}).get("date"):
                daily = self._data["daily"]
                for k in ("n_ii", "n_iv"):
                    if isinstance(val.get(k), int) and daily.get(k) != val[k]:
                        daily[k] = val[k]
                        touched = True
        if touched:
            self._recompute_daily(now)
            self._dirty = True
            self.save()
            self._changed()

    def _adopt_instruction(self, ident: str, val: dict, floor: str) -> bool:
        if not ident or str(val.get("accepted") or "") < floor:
            return False
        if ident in self._all_ids():
            return False
        local = next((c for c in self.actives() if str(c.get("id")) == ident), None)
        if val.get("result"):
            if local is not None:
                extra = self._data.get("extra") or []
                if local is self.current:
                    self._data["current"] = extra.pop() if extra else None
                else:
                    extra[:] = [e for e in extra if e is not local]
            history = self._data.setdefault("history", [])
            history.append({k: val.get(k) for k in ("id", "type", "body", "accepted", "ended", "result", "t", "code")})
            history.sort(key=lambda r: str(r.get("ended") or ""))
            if len(history) > HISTORY_KEEP:
                del history[:-HISTORY_KEEP]
            return True
        if local is not None:
            return False
        cur = dict(val, focus_sec=0.0, break_sec=0.0, stats_since=None)
        acts = sorted(self.actives() + [cur], key=lambda c: (str(c.get("accepted") or ""), str(c.get("id"))))
        self._data["current"] = acts[-1]
        self._data["extra"] = acts[:-1]
        return True

    def _adopt_reward(self, ident: str, val: dict) -> bool:
        item = {k: v for k, v in val.items() if k != "settled"}
        item["id"] = ident
        pending = self._data.setdefault("pending", [])
        if val.get("settled") is True:
            before = len(pending)
            pending[:] = [p for p in pending if not (isinstance(p, dict) and self._reward_id(p) == ident)]
            known = ident in self._data.setdefault("settled", {})
            self._data["settled"].setdefault(ident, item)
            return known is False or len(pending) != before
        return self._add_reward(item)

    # ---- X: 记录输入框 ----

    def active_of(self, kind: str):
        for cur in self.actives():
            if cur.get("type") == kind:
                return cur
        return None

    def note_close(self, now: datetime = None):
        """OIT 被手动关闭了 (X 的输入框第三段要写今天的这些时刻)."""
        now = (now or datetime.now()).replace(microsecond=0)
        closes = self._data.setdefault("closes", [])
        closes.append(_iso(now))
        del closes[:-50]
        self._dirty = True
        self.save()

    def _today_instruction_lines(self, now: datetime) -> list:
        day = now.strftime("%Y-%m-%d")
        rows = []
        for rec in self._data.get("history") or []:
            if isinstance(rec, dict) and str(rec.get("accepted") or "").startswith(day):
                word = "完成" if rec.get("result") == "completed" else "废弃"
                rows.append((str(rec.get("accepted")), str(rec.get("body") or ""), word))
        for cur in self.actives():
            if str(cur.get("accepted") or "").startswith(day):
                rows.append((str(cur.get("accepted")), str(cur.get("body") or ""), "进行中"))
        rows.sort(key=lambda r: r[0])
        out = []
        for accepted, body, word in rows:
            when = _parse(accepted)
            out.append(f"{when:%H:%M}  {tr(body)} ({tr(word)})" if when else f"{tr(body)} ({tr(word)})")
        return out

    def _today_close_lines(self, now: datetime) -> list:
        day = now.strftime("%Y-%m-%d")
        out = []
        for value in self._data.get("closes") or []:
            when = _parse(value)
            if when is not None and when.strftime("%Y-%m-%d") == day:
                out.append(f"{when:%H:%M}")
        return out

    def log_prefill(self, now: datetime = None) -> str:
        """X 的输入框一开始的文字: 三段 -- 今天的整点记录 (没启用是空白) / 今天领到的指令和状态 /
        今天 OIT 被手动关闭的时刻. 第一次生成时存下来, 保存时拿它判断用户改没改."""
        cur = self.active_of("X")
        if cur is not None and isinstance(cur.get("log_base"), str):
            return cur["log_base"]
        now = now or datetime.now()
        try:
            hour = str(self.hour_log_fn() or "").strip()
        except Exception:       # noqa: BLE001
            hour = ""
        text = "\n\n".join([hour, "\n".join(self._today_instruction_lines(now)),
                              "\n".join(self._today_close_lines(now))])
        if cur is not None:
            cur["log_base"] = text
            self._dirty = True
        return text

    @staticmethod
    def _norm_log(text) -> str:
        return str(text or "").replace("\r\n", "\n").strip()

    def submit_log(self, text: str, now: datetime = None):
        """保存 X 的输入框: 一点没改视为作废, 改过视为完成 (文字存进 logs). 返回 "completed" / "void" / None."""
        cur = self.active_of("X")
        if cur is None:
            return None
        now = (now or datetime.now()).replace(microsecond=0)
        base = cur.get("log_base")
        if base is None:
            base = self.log_prefill(now)
        if self._norm_log(text) == self._norm_log(base):
            self._finish("void", now, cur=cur)
            return "void"
        logs = self._data.setdefault("logs", {})
        logs[now.strftime("%Y-%m-%d")] = str(text)
        for day in sorted(logs)[:-LOG_KEEP_DAYS]:
            logs.pop(day, None)
        self._finish("completed", now, float(cur.get("t") or FIXED_T["X"]), cur=cur)
        return "completed"

    def abandon_log(self, now: datetime = None):
        """输入框没保存就关了: 等于没改, 作废."""
        cur = self.active_of("X")
        if cur is not None:
            self._finish("void", (now or datetime.now()).replace(microsecond=0), cur=cur)

    # ---- 结算休息时间 ----

    def settle_rewards(self, now: datetime = None) -> list:
        """打开指令页时调用: 把满足条件的休息时间加进计时器, 返回这次给出的代码."""
        now = (now or datetime.now()).replace(microsecond=0)
        self._check_daily_bonus(now)

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
            self._data.setdefault("settled", {})[self._reward_id(item)] = dict(item)    # 其他设备看到就不再结算
        self._data["pending"] = []
        settled = self._data["settled"]
        for old in sorted(settled, key=lambda k: str(settled[k].get("at") or ""))[:-GRANTED_KEEP]:
            settled.pop(old, None)
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
    import json
    import shutil
    import tempfile

    def devsync_wins(new, old):
        """devsync 的合并规则: 结束的盖过进行中的 (这里只看这一条规则)."""
        import devsync
        svc = devsync.SyncService.__new__(devsync.SyncService)
        return svc._wins("prescript", "i:x", 2, new, (1, old))

    root = tempfile.mkdtemp(prefix="oit_prescript_")
    state = {"mode": "focus", "page": "prescript", "min": False, "contract": False}
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
            contract_active_fn=lambda: state["contract"],
            rng=random.Random(7),
        )

        def clear():
            eng._data["current"] = None
            eng._data["extra"] = []

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
        assert all(len(type_code(k)) <= CODE_WIDTH for k in ALL_TYPES)
        assert fmt_clock(65) == "01:05" and fmt_clock(3665) == "1:01:05"

        # 空闲词条的 t = 分钟数 × 0.02, X 固定 0.1
        assert [FIXED_T[k] for k in ("V", "VII", "VIII", "IX", "X", "XI")] == [0.1, 0.4, 0.2, 0.1, 0.1, 0.3]

        # 没有领取冷却: 同一个小时里连着领; 手里满 KEEP 条就领不了, 完成 / 作废一条又能领
        t0 = datetime.now().replace(hour=12, minute=5, second=0, microsecond=0)
        assert eng.can_draw(t0) and eng.next_draw_at(t0) is None
        got = [eng.draw(t0, choose="III"), eng.draw(t0, choose="VI"), eng.draw(t0, choose="II")]
        assert all(got) and len(eng.actives()) == KEEP and not eng.can_draw(t0)
        assert eng.draw(t0, choose="IV") is None and len(eng.actives()) == KEEP
        assert eng.marks() == {"break", "stats", "contract"}
        eng._finish("void", t0, cur=eng.actives()[0])
        assert eng.can_draw(t0) and not any(h["result"] == "superseded" for h in eng._data["history"])
        clear()

        # 专注指令 (I) 没完成 / 作废之前不能再领新的; 其他指令不拦
        assert eng.draw(t0, choose="III") is not None and eng.can_draw(t0)
        assert eng.draw(t0, choose="I") is not None and len(eng.actives()) == 2 and not eng.can_draw(t0)
        assert eng.draw(t0, choose="VI") is None
        eng._finish("void", t0, cur=eng.active_of("I"))
        assert eng.can_draw(t0)
        clear()

        # I: 专注 50 分钟完成; 当时不在指令页, 回到页面时结算 t*3min
        assert eng.draw(t0, choose="I")
        state["mode"], state["page"] = "focus", "timer"
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

        # 空闲词条: 没有作废时限, 到 minutes 自动完成, t 按分钟数算
        state["mode"] = "idle"
        t1 = t0 + timedelta(hours=1)
        cur = eng.draw(t1, choose="VII")
        assert cur["type"] == "VII" and cur["expires"] is None and cur["due"] == _iso(t1 + timedelta(minutes=20))
        assert cur["body"] == "到开阔场地进行运动,持续20min." and cur["pool"] == "idle"
        eng._last_tick = t1
        eng.tick(t1 + timedelta(minutes=19, seconds=59))
        assert eng.current is cur
        eng.tick(t1 + timedelta(minutes=20))
        assert eng.current is None and eng._data["history"][-1]["result"] == "completed"
        assert abs(eng._data["history"][-1]["t"] - 0.4) < 1e-9 and granted[-1] == (72, "REST1.2")
        clear()
        # 离开指令页 / 最小化立即作废, 可以马上再领 (没有冷却)
        for leave in ({"page": "stats"}, {"min": True}):
            state.update(page="prescript", min=False)
            cur = eng.draw(t1, choose="VIII")
            state.update(leave)
            eng.tick(t1 + timedelta(minutes=1))
            assert eng.current is None and eng._data["history"][-1]["result"] == "void"
            assert eng.can_draw(t1)
        state.update(page="prescript", min=False)
        clear()
        # 旧存档里的 V (只有 expires) 照样 5 分钟后完成
        cur = eng.draw(t1, choose="V")
        cur["expires"], cur["due"] = _iso(t1 + timedelta(minutes=5)), None
        eng._last_tick = t1
        eng.tick(t1 + timedelta(minutes=5))
        assert eng.current is None and eng._data["history"][-1]["type"] == "V"
        assert eng._data["history"][-1]["result"] == "completed"
        clear()

        # 时间段: 对得上的词条拿走 V 那一格 (0.8) 的 80%, 其余平分; 已经抽到过的换成 V
        def at(h, m=0):
            return t0.replace(hour=h, minute=m)
        eng._data["run"] = []
        def boost(h, m=0):
            return eng.idle_boosts(at(h, m))
        assert boost(5, 29) == ["V"] and boost(5, 30) == ["VII"]
        assert boost(7, 29) == ["VII"] and boost(7, 30) == ["V"]
        assert boost(13) == ["VIII"] and boost(14, 29) == ["VIII"] and boost(14, 30) == ["V"]
        assert boost(19, 30) == ["VIII"] and boost(20, 30) == ["XI"]
        assert boost(9) == ["IX"] and boost(22, 29) == ["IX"] and boost(22, 30) == ["V"]
        assert boost(11, 59) == ["XI"] and boost(23) == ["X"] and boost(23, 59) == ["X"]
        assert boost(0, 30) == ["V"] and boost(15) == ["V"]
        for h, m in ((10, 0), (7, 0), (23, 30), (13, 30), (3, 0)):
            opts = dict(eng.choices("idle", at(h, m)))
            assert abs(sum(opts.values()) - 1.0) < 1e-9
            assert abs(opts["I"] - 0.05) < 1e-9 and abs(opts["IV"] - 0.02) < 1e-9
            group = {k: opts[k] for k in IDLE_KINDS}
            assert abs(sum(group.values()) - 0.9) < 1e-9
            boosted = boost(h, m)[0]
            assert abs(group[boosted] - 0.72) < 1e-9, (h, m)
            assert all(abs(v - 0.036) < 1e-9 for k, v in group.items() if k != boosted)
        # 设置里改时段 (设置 → 日常): 改完立即用新的; 写错了退回默认; 重叠的词条均分被抬高的那一份
        keep_cfg = {k: getattr(config, "PRESCRIPT_WIN_" + k) for k in config.PRESCRIPT_WIN_KINDS}
        try:
            config.PRESCRIPT_WIN_XI = "05:00-08:00"
            assert set(boost(7)) == {"VII", "XI"} and boost(8) == ["V"]
            opts = dict(eng.choices("idle", at(7)))
            assert abs(opts["VII"] - 0.36) < 1e-9 and abs(opts["XI"] - 0.36) < 1e-9
            assert abs(opts["V"] - 0.045) < 1e-9 and abs(sum(opts.values()) - 1.0) < 1e-9
            eng._data["run"] = ["XI"]                   # 抽到过的不再算重叠
            assert boost(7) == ["VII"]
            eng._data["run"] = []
            config.PRESCRIPT_WIN_VII = "写错了"
            assert set(boost(6)) == {"VII", "XI"} and config.prescript_windows("VII") == [(330, 450)]   # 写错了 = 默认
            assert boost(5, 15) == ["XI"]
            config.PRESCRIPT_WIN_V = "00:00-05:00"      # V 也可以设时段 (没有别的词条抬高时它不用兜底)
            assert boost(3) == ["V"] and set(boost(6)) == {"VII", "XI"}
            config.PRESCRIPT_WIN_X = ""
            assert not config.prescript_windows("X") and boost(23, 30) == ["V"]
            config.PRESCRIPT_WIN_IX = "22:00-02:00"      # 跨零点
            assert boost(23, 30) == ["IX"] and set(boost(1)) == {"IX", "V"} and boost(2) == ["V"]
        finally:
            for k, v in keep_cfg.items():
                setattr(config, "PRESCRIPT_WIN_" + k, v)
        # 连续抽取里抽到过的 (V 除外) 不能再抽, 对得上的词条已经抽到就换成 V
        eng._data["run"] = ["VII"]
        opts = dict(eng.choices("idle", at(7)))
        assert "VII" not in opts and abs(opts["V"] - 0.72) < 1e-9 and abs(sum(opts.values()) - 1.0) < 1e-9
        eng._data["run"] = ["II", "VIII", "IX", "X", "XI", "VII"]
        opts = dict(eng.choices("idle", at(7)))
        assert opts["V"] > 0.89 and not any(k in opts for k in ("II", "VII", "VIII", "IX", "X", "XI"))
        assert abs(sum(opts.values()) - 1.0) < 1e-9
        # 专注 / 休息池不拆 V, 也不看这一轮
        assert "VII" not in dict(eng.choices("break", at(7))) and dict(eng.choices("break", at(7)))["V"] == 0.05
        eng._data["run"] = []

        # 一轮连续的空闲抽取: 抽到的 (V 除外) 记下来, V 可以重复; 重新进入空闲池重置
        clear()
        eng._data["last_pool"] = "focus"
        eng._data["force_focus"] = False
        eng._data["focus_chance"] = -1.0           # 这一段不让它触发专注池
        eng.draw(t1, choose="VII", pool="idle")
        eng._finish("completed", t1, 0.4, cur=eng.current)
        eng.draw(t1, choose="V", pool="idle")
        eng._finish("completed", t1, 0.1, cur=eng.current)
        eng.draw(t1, choose="V", pool="idle")
        assert eng._data["run"] == ["VII"], eng._data["run"]
        clear()
        eng._data["last_pool"] = "focus"
        eng.draw(t1, choose="VIII", pool="idle")
        assert eng._data["run"] == ["VIII"]
        clear()
        eng._data["force_focus"] = False

        # 每次空闲抽取有概率让下一次改抽专注池: 初始 25%, 没触发 +25%, 触发后回到 25%
        class Roll:
            def __init__(self, values):
                self.values = list(values)
                self.base = random.Random(3)

            def random(self):
                return self.values.pop(0) if self.values else self.base.random()

            def __getattr__(self, name):
                return getattr(self.base, name)
        eng.rng = Roll([0.9, 0.6, 0.8])
        eng._data["focus_chance"] = FOCUS_CHANCE_START
        eng._data["last_pool"] = "idle"
        eng._data["run"] = []
        for want_chance in (0.5, 0.75, 1.0):
            clear()
            eng.draw(t1, choose="V", pool="idle")
            assert abs(eng._data["focus_chance"] - want_chance) < 1e-9 and eng._data["force_focus"] is False
        clear()
        eng.rng = Roll([0.99])                       # 概率已经到 100%: 必定触发
        eng.draw(t1, choose="V", pool="idle")
        assert eng._data["force_focus"] is True and eng._data["focus_chance"] == FOCUS_CHANCE_START
        # 下一次抽取: 不管状态是什么都抽专注池, 抽完标志清掉
        clear()
        state["mode"] = "idle"
        eng.rng = random.Random(7)
        cur = eng.draw(t1, choose="VI")
        assert cur["pool"] == "focus" and eng._data["force_focus"] is False
        clear()
        eng._data["force_focus"] = False

        # 合约期间空闲: 随机抽专注 / 休息池, 不抽空闲池
        state["contract"] = True
        assert {eng._pool(t1) for _ in range(60)} == {"focus", "break"}
        state["mode"] = "break"
        assert eng._pool(t1) == "break"
        state["contract"] = False
        state["mode"] = "idle"
        assert eng._pool(t1) == "idle"

        # X: 输入框三段文字, 没改保存 = 作废, 改过保存 = 完成
        clear()
        eng.hour_log_fn = lambda: "09:00  学习\n10:00  写代码"
        eng._data["history"].append({"id": "h1", "type": "III", "body": "休息5min.", "accepted": _iso(t1),
                                     "ended": _iso(t1), "result": "completed", "t": 0.2})
        eng._data["history"].append({"id": "h2", "type": "I", "body": "接下来的1h专注50min.", "accepted": _iso(t1),
                                     "ended": _iso(t1), "result": "expired", "t": None})
        eng._data["closes"] = [_iso(t1.replace(hour=8, minute=15)), _iso(t1 - timedelta(days=1))]
        eng._data["last_pool"] = "focus"
        cur = eng.draw(at(23, 10), choose="X", pool="idle")
        assert cur["type"] == "X" and cur["due"] is None and cur["expires"] is None
        base = eng.log_prefill(at(23, 10))
        paras = base.split("\n\n")
        assert len(paras) == 3 and paras[0] == "09:00  学习\n10:00  写代码", paras
        assert "休息5min. (完成)" in paras[1] and "接下来的1h专注50min. (废弃)" in paras[1] and "(进行中)" in paras[1]
        assert paras[2] == "08:15", paras[2]
        assert eng.log_prefill(at(23, 40)) == base
        eng._last_tick = at(23, 10)
        eng.tick(at(23, 55))                      # 没有计时: 一直等着
        assert eng.current is cur
        assert eng.submit_log(base + "  \n", at(23, 56)) == "void" and eng.current is None
        assert eng._data["history"][-1]["result"] == "void" and not eng._data["logs"]
        cur = eng.draw(at(23, 57), choose="X", pool="idle")
        base = eng.log_prefill(at(23, 57))
        assert eng.submit_log(base + "\n今天读了书", at(23, 58)) == "completed"
        assert eng._data["history"][-1]["result"] == "completed" and abs(eng._data["history"][-1]["t"] - 0.1) < 1e-9
        assert "今天读了书" in eng._data["logs"][at(23, 58).strftime("%Y-%m-%d")]
        assert eng.submit_log("x") is None
        eng.hour_log_fn = lambda: ""              # 没启用整点记录: 第一段空白
        cur = eng.draw(at(23, 59), choose="X", pool="idle")
        assert eng.log_prefill(at(23, 59)).startswith("\n\n")
        eng.abandon_log(at(23, 59))
        assert eng.current is None and eng._data["history"][-1]["result"] == "void"
        eng.note_close(at(23, 59))
        assert at(23, 59).strftime("%H:%M") in eng._today_close_lines(at(23, 59))
        # X 也要留在指令页上, 离开就作废
        cur = eng.draw(at(23, 59), choose="X", pool="idle")
        state["page"] = "timer"
        eng.tick(at(23, 59) + timedelta(seconds=1))
        assert eng.current is None and eng._data["history"][-1]["result"] == "void"
        state["page"] = "prescript"
        eng._data["force_focus"] = False

        # IV (每日专注): 手里已经有一条就不能再领 (任何池子都不行), 完成后今天也不能再领
        clear()
        today["hours"] = 0.0
        assert "IV" in dict(eng.choices("focus", at(12)))
        assert eng.draw(at(12), choose="IV", pool="focus")
        for pool in ("focus", "break", "idle"):
            assert "IV" not in dict(eng.choices(pool, at(12))), pool
        assert abs(sum(w for _, w in eng.choices("focus", at(12))) - 1.0) < 1e-9
        eng._finish("completed", at(12), 1.0, cur=eng.active_of("IV"))
        assert "IV" not in dict(eng.choices("focus", at(12)))
        eng._data["daily"]["iv_done"] = False
        assert "IV" in dict(eng.choices("focus", at(12)))
        clear()
        state["mode"] = "idle"

        # II: 下一次正常完成的合约, t = 总时长 (2h)
        clear()
        state["mode"] = "focus"
        t2 = t0 + timedelta(hours=5)
        cur = eng.draw(t2, choose="II")
        assert cur and cur["type"] == "II"
        n = int(cur["n"])
        contracts.append({
            "id": "c1", "result": "completed", "level": n,
            "start": _iso(t2 + timedelta(minutes=5)),
            "planned": _iso(t2 + timedelta(hours=2, minutes=5)),
            "end": _iso(t2 + timedelta(hours=2, minutes=5)),
        })
        state["page"] = "timer"
        eng.tick(t2 + timedelta(hours=2, minutes=6))
        assert eng.current is None and eng._data["history"][-1]["t"] == 2.0
        assert eng._data["pending"][-1]["code"] == "REST6"

        # VI: 统计页连续 3 分钟
        clear()
        state["page"] = "prescript"
        t3 = t2 + timedelta(hours=4)
        cur = eng.draw(t3, choose="VI")
        assert cur and cur["type"] == "VI"
        state["page"] = "stats"
        eng._last_tick = t3
        eng.tick(t3 + timedelta(minutes=1))
        assert eng.current is not None
        eng.tick(t3 + timedelta(minutes=4))
        assert eng.current is None and eng._data["history"][-1]["result"] == "completed"

        # 今天完成的 t 合计到 10: 20 分钟
        eng._data["daily"]["completed_t"] = 10.0
        before = len(granted)
        given = eng.settle_rewards(t3 + timedelta(hours=1))
        assert any(g["code"] == "REST20" and g["seconds"] == 1200 for g in given)
        assert len(granted) >= before + 1
        assert eng.settle_rewards(t3 + timedelta(hours=1, minutes=1)) == []

        # 过了 1h 才醒来的 tick: 只算到作废时刻, 不会把 1h 之后的专注补进来
        clear()
        state["page"] = "prescript"
        state["mode"] = "break"
        t4 = t3 + timedelta(hours=20)
        cur = eng.draw(t4, choose="I")
        assert cur and cur["type"] == "I"
        eng._last_tick = t4
        eng.tick(t4 + timedelta(minutes=59))
        state["mode"] = "focus"
        eng.tick(t4 + timedelta(hours=2))
        assert eng.current is None and eng._data["history"][-1]["result"] == "expired"

        # 合约期间抽不到 VI: 它的概率平分给同池其他还能抽到的指令
        state["contract"] = True
        assert eng._weights("focus", t4)[5] == 0.0 and abs(sum(eng._weights("focus", t4)) - 1.0) < 1e-9
        assert eng._weights("break", t4)[5] == 0.0
        state["contract"] = False
        assert eng._weights("focus", t4)[5] > 0.0

        # III: 休息分钟数 = 剩余休息时间的 50%, 最多 30 分钟; t = 休息时间 (小时) × 2
        assert rest_minutes(3000) == 25 and rest_minutes(7200) == 30 and rest_minutes(0) == 1
        clear()
        eng.remaining_rest_fn = lambda: 3000
        c3 = eng.draw(t4 + timedelta(hours=40), choose="III")
        assert c3["n"] == 25 and c3["body"] == "休息25min." and abs(c3["t"] - 25 / 30) < 1e-9
        eng.remaining_rest_fn = lambda: 0.0
        clear()

        # 领到任何一个空闲词条都暂停后台的音频 / 视频, 其他指令不碰
        paused = []
        eng.pause_media_fn = lambda: paused.append(1)
        t_v = t4 + timedelta(hours=60)
        eng.draw(t_v, choose="I")
        assert not paused
        for i, kind in enumerate(IDLE_KINDS):
            clear()
            eng.draw(t_v + timedelta(hours=1), choose=kind)
            assert len(paused) == i + 1, kind
        eng.pause_media_fn = None
        clear()

        # 完成时通知 (播完成音效); 每个入口的标记: I 专注, III 休息, II 合约, VI 统计, IV / 空闲词条不标记
        done = []
        eng.on_complete = lambda: done.append(1)
        t_m = t4 + timedelta(hours=50)
        for kind in ("II", "VI", "IV", "V", "XI"):
            clear()
            eng.draw(t_m, choose=kind)
            assert eng.marks() == ({"contract"} if kind == "II" else {"stats"} if kind == "VI"
                                   else set()), kind
        clear()
        state["page"] = "prescript"
        eng.draw(t_m, choose="V")
        eng._last_tick = t_m
        eng.tick(t_m + timedelta(minutes=6))
        assert done == [1] and eng.current is None
        eng.on_complete = None

        # 设备同步: 领到的指令 / 奖励 / 结果在两台设备间一致; 结束的盖过进行中的; 空闲词条要所有设备都离开指令页才作废
        import copy
        root2 = tempfile.mkdtemp(prefix="oit_prescript_b_")
        try:
            peer = {"page": True}
            sa = {"page": "prescript", "min": False}
            sb = {"page": "timer", "min": False}
            gb = []
            A = PrescriptEngine(root, state_fn=lambda: state["mode"], page_fn=lambda: sa["page"],
                                minimized_fn=lambda: sa["min"], peers_on_page_fn=lambda: sb["page"] == "prescript",
                                contracts_fn=lambda: contracts, rng=random.Random(1))
            B = PrescriptEngine(root2, state_fn=lambda: state["mode"], page_fn=lambda: sb["page"],
                                minimized_fn=lambda: sb["min"], peers_on_page_fn=lambda: sa["page"] == "prescript",
                                grant_rest_fn=lambda s, c: gb.append((s, c)), contracts_fn=lambda: contracts,
                                rng=random.Random(2))
            for e in (A, B):
                e._data.update(current=None, extra=[], history=[], pending=[], settled={}, closes=[], logs={})
            ts = datetime.now().replace(hour=15, minute=0, second=0, microsecond=0)
            state["mode"] = "idle"

            def push(src, dst):
                dst.sync_apply(copy.deepcopy(json.loads(json.dumps(src.sync_doc(ts)))), ts)
            # A 领一条 III, B 同步到; 进度不同步, B 自己累计
            A.draw(ts, choose="III", pool="break")
            push(A, B)
            assert [c["type"] for c in B.actives()] == ["III"] and B.current["id"] == A.current["id"]
            assert B.current["break_sec"] == 0.0 and "break_sec" not in A.sync_doc(ts)["i:" + A.current["id"]]
            # 两边的数量上限 / 专注指令互相算
            A.draw(ts, choose="I", pool="focus")
            push(A, B)
            assert len(B.actives()) == 2 and not B.can_draw(ts)
            # B 完成 III: A 同步后也结束 (结果盖过进行中, 不重复给奖励)
            iid = [c for c in B.actives() if c["type"] == "III"][0]
            B._finish("completed", ts + timedelta(minutes=5), 0.2, cur=iid)
            before = list(A._data["pending"])
            push(B, A)
            assert [c["type"] for c in A.actives()] == ["I"] and A._data["history"][-1]["result"] == "completed"
            assert A._data["pending"] and len(A._data["pending"]) == 1 and A._data["pending"][0]["id"] == iid["id"]
            # 进行中的旧版本不能把结束的盖回去
            stale = {"i:" + iid["id"]: dict(iid)}
            assert devsync_wins(stale["i:" + iid["id"]], A._data["history"][-1]) is False
            A.sync_apply(stale, ts)
            assert [c["type"] for c in A.actives()] == ["I"]
            # 进入 / 退出指令页是事件: 一台设备进 / 出, 另一台跟着 (够新才跟, 旧的不理; 跟着做的不再发回去)
            seen = []
            B.page_event_fn = seen.append
            A.note_page(True, "devA", ts)
            push(A, B)
            assert seen == [True] and B._page_rec["by"] == "devA"
            push(A, B)
            assert seen == [True], "同一个事件只跟一次"
            A.note_page(False, "devA", ts + timedelta(seconds=30))
            push(A, B)
            assert seen == [True, False]
            old = {"p:page": {"on": True, "at": (ts - timedelta(minutes=10)).isoformat(timespec="seconds"), "by": "devX"}}
            B.sync_apply(old, ts)
            assert seen == [True, False], "太旧的事件不理"
            B.page_event_fn = None
            # 日志的 2 小时冷却随同步: A 看了, B 同步后也要等; 谁看得最晚算谁的
            assert A.journal_view() == "" and B.journal_view() == ""
            A.note_journal(ts)
            push(A, B)
            assert B.journal_view() == A.journal_view() != ""
            B._data["journal_view"] = (ts + timedelta(hours=1)).isoformat(timespec="seconds")
            push(A, B)
            assert B.journal_view() > A.journal_view(), "旧的不能盖掉新的"
            push(B, A)
            assert A.journal_view() == B.journal_view()
            # 奖励: 在 A 的指令页上结算 -> B 同步后不再结算
            A.page_fn = lambda: "prescript"
            given = A.settle_rewards(ts + timedelta(minutes=6))
            assert len(given) == 1 and iid["id"] in A._data["settled"]
            push(A, B)
            assert not B._data["pending"] and B.settle_rewards(ts + timedelta(minutes=7)) == [] and gb == []
            A.page_fn = lambda: sa["page"]
            # 两台设备都完成同一条: 奖励只有一份
            A._finish("completed", ts + timedelta(minutes=9), 1.0, cur=A.active_of("I"))
            B.sync_apply({"i:" + A._data["history"][-1]["id"]: dict(A._data["history"][-1])}, ts)
            assert not B.actives() and len(B._data["history"]) == 2
            # 空闲词条: A 离开页面时 B 还停在页面上就不作废; 都离开才作废
            sb["page"] = "prescript"
            sa["page"] = "prescript"
            idle = A.draw(ts + timedelta(minutes=10), choose="VIII", pool="idle")
            push(A, B)
            assert B.current["type"] == "VIII"
            A._last_tick = ts + timedelta(minutes=10)
            sa["page"] = "timer"
            A.tick(ts + timedelta(minutes=11))
            assert A.current is not None and A.current["id"] == idle["id"], "B 还在指令页上"
            A.tick(ts + timedelta(minutes=21))             # 到点了, 但 A 不在页面上: 不由 A 完成
            assert A.current is not None
            B._last_tick = ts + timedelta(minutes=10)
            B.tick(ts + timedelta(minutes=21))              # B 在页面上: 由 B 完成
            assert B.current is None and B._data["history"][-1]["result"] == "completed"
            push(B, A)
            assert A.current is None and A._data["history"][-1]["result"] == "completed"
            sa["page"] = "prescript"
            A.draw(ts + timedelta(minutes=30), choose="IX", pool="idle")
            push(A, B)
            sa["page"] = "timer"
            sb["page"] = "timer"
            A.tick(ts + timedelta(minutes=31))
            assert A.current is None and A._data["history"][-1]["result"] == "void"
            B.tick(ts + timedelta(minutes=31))
            assert B.current is None
            # 当天合计从记录里算 (两台设备一样)
            A._recompute_daily(ts)
            assert abs(A._data["daily"]["completed_t"] - (0.2 + 1.0 + 0.2)) < 1e-9
        finally:
            shutil.rmtree(root2, ignore_errors=True)

        # 存档再读出来: 历史 / 记录 / 关闭时刻还在
        eng.save()
        eng2 = PrescriptEngine(root, state_fn=lambda: "idle", today_hours_fn=lambda: 0.0)
        assert eng2._data["history"] and eng2._data["logs"] and eng2._data["closes"]
        return "prescript selftest OK"
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    print(_selftest())
