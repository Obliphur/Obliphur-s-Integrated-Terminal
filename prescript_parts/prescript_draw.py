"""当前持有的指令与领取: 池子 / 权重 / 空闲词条时段 / 抽取 (MECHANICS 22.1 ~ 22.3)。

从 prescript.py 拆出来的 mixin; 依赖方向: prescript.py 导入它们, 它们从不导入 prescript.py.
"""

from datetime import datetime, timedelta

import config

from prescript_parts.prescript_common import (
    ALL_TYPES, EXPIRY_SECONDS, FIXED_T, FOCUS_CHANCE_START, FOCUS_CHANCE_STEP, IDLE_BOOST, IDLE_ENTRIES,
    IDLE_KINDS, KEEP, MARK_OF, POOLS, TYPES, _iso, _midnight_after, rest_minutes,
)


class DrawMixin:
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
