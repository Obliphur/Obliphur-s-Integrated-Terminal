"""未知变量: 每个小时随机启用一个词条或施加一个效果; 记录封锁的格子 (MECHANICS 18.4)。

从 contract.py 拆出来的 mixin; 依赖方向: contract.py 导入它们, 它们从不导入 contract.py.
"""

import random
from datetime import datetime, timedelta

from contract_parts.contract_rules import (
    TERMS, BY_ID, NET_SHARE, SUPPLY, HOUR_TERMS, LIFE, SEAL_EXTRA, RANDOM, RANDOM_LEVELS, RANDOM_SKIP,
    RANDOM_DELAY, RANDOM_TERM_P, RANDOM_EFFECTS, RANDOM_SLOW, RANDOM_ROLLS, life_of, blocked_by,
    needs_of, clean_terms, _iso, _parse,
)


class RandomMixin:
    def random_candidates(self) -> list:
        """现在能被未知变量选中的词条: 1-2 级, 能选 (没有选着, 也不和选着的冲突), 不是录像;
        退路封锁不会让目标生命值直接归零. 不选已经选过的; 通过违反失效的词条可以再选中一次
        (选中期间违反照常扣目标生命值)."""
        terms = self.terms + list(self.waiting)
        used = set(self.live.get("rand_used") or [])
        viol = set(self.lost) | set(self.live.get("rand_viol") or [])
        again = set(self.live.get("rand_again") or [])
        out = []
        for t in TERMS:
            tid = t[0]
            if not (tid not in used and tid not in viol) and not (tid in viol and tid not in again):
                continue
            if t[2] not in RANDOM_LEVELS or tid in RANDOM_SKIP or tid in terms or blocked_by(tid, terms) \
                    or needs_of(tid, terms) or tid in self.waiting:
                continue
            if tid in LIFE and life_of(terms + [tid]) <= self.violations:
                continue
            out.append(tid)
        return out

    def roll(self, now: datetime, rng=None, text_of=None, auto="", fill: timedelta = None) -> list:
        """到了新的一个小时就判定一次: 抽一个词条 (3 分钟后生效 (wake), 到这个小时结束时移除) 或者施加一个效果.
        返回这次的结果 [(种类, 值)]: ("term", 词条) / ("rest_cut", "") / ("rest_add", "") / ("lock", 封锁的格子键或 "")
        / ("double", "") / ("slow", "") / ("none", ""); 休息时间的增减由调用方做 (只由做自动结束的设备调用).
        程序关着时错过的小时依次补上 (那时抽中的词条到了它那个小时的结束就移除, 不再返回)."""
        if not self.active or not self.has(RANDOM) or not self.start_at:
            return []
        rng = rng or random
        end = self.end_at or now
        due = int((min(now, end) - self.start_at).total_seconds() // 3600) + 1 if now < end else 0
        done = int(self.live.get("rand_n") or 0)
        out = []
        while done < due:
            done += 1
            at = self.start_at + timedelta(hours=done - 1)
            self.expire(at)
            n, k = 1, 0
            while n > 0 and k < RANDOM_ROLLS:
                n, k = n - 1, k + 1
                pool = self.random_candidates()
                if pool and rng.random() < RANDOM_TERM_P:
                    tid = rng.choice(pool)
                    self._pick(tid, at)
                    out.append(("term", tid))
                    continue
                eff = self._effect(rng)
                if eff == "double":
                    n += 2
                elif eff == "slow":
                    self.live["slow"] = {"pending": True}
                elif eff == "lock":
                    out.append(("lock", self.lock_cell(now, rng, text_of, auto, fill)))
                    continue
                out.append((eff, ""))
            self.live["rand_n"] = done
            self.save()
        self.expire(now)
        wait = self.waiting
        return [e for e in out if e[0] != "term" or e[1] in wait]

    @staticmethod
    def _effect(rng) -> str:
        r = rng.random() * sum(w for _e, w in RANDOM_EFFECTS)
        for eff, w in RANDOM_EFFECTS:
            if r < w:
                return eff
            r -= w
        return RANDOM_EFFECTS[-1][0]

    def _pick(self, tid: str, at: datetime):
        """未知变量在 at 开始的这个小时抽中了 tid: 记下选过 (通过违反失效后再选中的记下用掉了这一次)."""
        viol = set(self.lost) | set(self.live.get("rand_viol") or [])
        used = list(self.live.get("rand_used") or [])
        if tid in viol:
            again = list(self.live.get("rand_again") or [])
            self.live["rand_again"] = again + [tid]
        if tid not in used:
            self.live["rand_used"] = used + [tid]
        wait = self.waiting
        wait[tid] = (at + RANDOM_DELAY, at + timedelta(hours=1))
        self.live["rand_wait"] = {t: [_iso(a), _iso(b)] for t, (a, b) in wait.items()}

    # ---- 未知变量的效果 ----

    # ---- 封锁 (未知变量 / 记录封锁, MECHANICS 18.4): 只锁已经填写了的整小时格, 合约结束后照样锁着 ----
    # 存在 "seals" 里 ([开始, 结束] 都是整小时). 旧版本的 "locks" (会锁还没填写 / 细化规范的半小时格) 不再算.

    def _filled(self, a: datetime, key: str, text: str, text_of, auto) -> bool:
        """这一小时已经填写了 (自己填的或超时自动记录的): 细化规范的小时要两个半小时都填了; 休息中没填写的格子 (rest_cell) 不算."""
        if not str(text or "").strip():
            return False
        if self.fine_hour(a) and not all(str(text_of(f"{key}/{i}") or "").strip() for i in (1, 2)):
            return False
        return not self.rest_cell(a, a + timedelta(hours=1), text, auto)

    def lock_pool(self, now: datetime, text_of, auto, fill: timedelta) -> list:
        """可以封锁的格子: 今天的整点记录页上的一行 (昨天 23:00 ~ 今天 23:00, 整小时, 不是细化规范 / 黑雾切出来的半小时格),
        已经结束、已经填写了、还没被锁. [(键, 开始, 结束)]."""
        day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        out = []
        for r in range(24):
            a = day0 + timedelta(hours=r - 1)
            b = a + timedelta(hours=1)
            if b > now:
                break
            key = f"{a:%Y-%m-%d}/{a.hour}"
            text = str(text_of(key) or "")
            if self._filled(a, key, text, text_of, auto) and not self.hour_locked(a, b, text, now, fill, auto, key):
                out.append((key, a, b))
        return out

    def _add_seals(self, spans) -> None:
        have = [list(v) for v in self.live.get("seals") or []]
        self.live["seals"] = have + [[_iso(a), _iso(b)] for a, b in spans if [_iso(a), _iso(b)] not in have]

    def lock_cell(self, now: datetime, rng=None, text_of=None, auto="", fill: timedelta = None) -> str:
        """未知变量: 随机封锁一个可以封锁的格子 (lock_pool). 返回格子的键 ("日期/小时"); 没有可以封锁的返回 ""."""
        if not self.active or text_of is None or fill is None:
            return ""
        pool = self.lock_pool(now, text_of, auto, fill)
        if not pool:
            return ""
        k, a, b = (rng or random).choice(pool)
        self._add_seals([(a, b)])
        self.save()
        return k

    def seal_extra(self, now: datetime, rng=None, text_of=None, auto="", fill: timedelta = None) -> list:
        """记录封锁 (18.4): 每次完成填写时, 另外随机封锁 SEAL_EXTRA 个可以封锁的格子 (lock_pool).
        返回封锁了的格子的键; 没有选记录封锁 / 没有可以封锁的时少于 SEAL_EXTRA 个."""
        if not self.active or not self.has("seal") or text_of is None or fill is None:
            return []
        pool = self.lock_pool(now, text_of, auto, fill)
        picks = []
        for _ in range(min(SEAL_EXTRA, len(pool))):
            p = (rng or random).choice(pool)
            pool.remove(p)
            picks.append(p)
        if not picks:
            return []
        self._add_seals([(a, b) for _k, a, b in picks])
        self.save()
        return [k for k, _a, _b in picks]

    def seal_filled(self, now: datetime, text_of, auto) -> bool:
        """记录封锁 (每秒调用): 和合约重叠、已经结束、已经填写了的整小时格记进 seals, 合约结束后照样锁着.
        只记今天的整点记录页上的行. 返回记了新的没有."""
        if not self.active or not self.start_at or not ("seal" in self.orig or self.has("seal")):
            return False
        have = {tuple(v) for v in (self.live.get("seals") or []) if isinstance(v, (list, tuple))}
        day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        new = []
        for r in range(24):
            a = day0 + timedelta(hours=r - 1)
            b = a + timedelta(hours=1)
            if b > now:
                break
            if not (self.start_at < b) or (_iso(a), _iso(b)) in have:
                continue
            key = f"{a:%Y-%m-%d}/{a.hour}"
            if self._filled(a, key, str(text_of(key) or ""), text_of, auto):
                new.append((a, b))
        if not new:
            return False
        self._add_seals(new)
        self.save()
        return True

    def locks(self) -> list:
        """封锁了的记录格: [(开始, 结束)]. 进行中的合约的, 加上结束了的所有合约的 (合约结束后照样锁着)."""
        out = []
        srcs = [self.live.get("seals") if self.active else None] + [h.get("seals") for h in self.history]
        for src in srcs:
            for v in src or []:
                a, b = (_parse(v[0]), _parse(v[1])) if isinstance(v, (list, tuple)) and len(v) == 2 else (None, None)
                if a and b:
                    out.append((a, b))
        return out

    def slow_bind(self, start: datetime) -> bool:
        """开始专注: 等着的阻滞落到这段专注上 (专注时间按 RANDOM_SLOW 增长). 返回落上了没有.
        阻滞只对合约内的专注生效: 合约已经结束 / 这段专注开始于合约结束之后时不落上."""
        slow = self.live.get("slow") if self.active else None
        if not (isinstance(slow, dict) and slow.get("pending")):
            return False
        end = self.end_at
        if not end or start >= end:
            return False
        self.live["slow"] = {"start": _iso(start)}
        self.save()
        return True

    def slow_until(self, start: datetime):
        """阻滞让这段专注按 RANDOM_SLOW 增长到什么时候 (合约结束的时刻); 没有阻滞时 None."""
        if start is None:
            return None
        mark = _iso(start)
        slow = self.live.get("slow") if self.active else None
        if isinstance(slow, dict) and slow.get("start") == mark:
            return self.end_at
        for h in reversed(self.history[-3:]):
            if h.get("slow") == mark:
                return _parse(h.get("end"))
        return None

    def focus_rate(self, start: datetime, now: datetime = None) -> float:
        """开始于 start 的这段专注现在按多少增长: 阻滞 RANDOM_SLOW (只到合约结束), 之后 1."""
        until = self.slow_until(start)
        if until is None:
            return 1.0
        return RANDOM_SLOW if (now or datetime.now()) < until else 1.0

    def focus_minutes(self, start: datetime, seconds: float) -> float:
        """这一段专注计入的分钟数: 阻滞只减慢合约内的那部分时间, 合约结束后的部分按正常速度 (MECHANICS 18.3)."""
        seconds = max(0.0, float(seconds))
        until = self.slow_until(start)
        if until is None or start is None:
            return seconds / 60.0
        end = start + timedelta(seconds=seconds)
        inside = max(0.0, (min(end, until) - start).total_seconds())
        return (inside * RANDOM_SLOW + (seconds - inside)) / 60.0

    @property
    def slow_pending(self) -> bool:
        slow = self.live.get("slow") if self.active else None
        return isinstance(slow, dict) and bool(slow.get("pending"))

    def pre_cap(self, carry: float):
        """积攒的休息时间减少到 carry: 合约开始前的那部分最多剩这么多 (先减合约中得到的)."""
        if self.active and self.pre_left > float(carry):
            self.live["pre_left"] = round(max(0.0, float(carry)), 1)
            self.save()

    @property
    def waiting(self) -> dict:
        """未知变量抽中了, 还没生效的词条 → (生效的时刻, 移除的时刻)."""
        w = self.live.get("rand_wait") if self.active else None
        out = {}
        for tid, v in (w.items() if isinstance(w, dict) else ()):
            a, b = (_parse(v[0]), _parse(v[1])) if isinstance(v, (list, tuple)) and len(v) == 2 else (None, None)
            if tid in BY_ID and a and b:
                out[tid] = (a, b)
        return out

    def wake(self, now: datetime) -> list:
        """抽中的词条到了生效的时刻就启用 (到它那个小时结束时移除). 返回启用的词条."""
        wait = self.waiting
        due = [t for t, (a, b) in wait.items() if a <= now]
        if not due:
            return []
        self.live["rand_wait"] = {t: [_iso(a), _iso(b)] for t, (a, b) in wait.items() if t not in due}
        out = []
        for t in due:
            a, b = wait[t]
            if now < b and t not in self.terms and not blocked_by(t, self.terms) and not needs_of(t, self.terms):
                self._enable(t, b)
                out.append(t)
        self.save()
        return out

    def _enable(self, tid: str, until: datetime = None):
        """合约中途启用一个词条: 开始时才定下来的效果在这里补上 (支援短缺: 能用多少; 信息阻断: 重新记下额度).
        until: 到这个时刻移除."""
        terms = clean_terms(self.terms + [tid])
        self.live["terms"] = terms
        self.live["rand"] = self.rand + [tid]
        if tid in HOUR_TERMS:
            self.live["hour_forced"] = True          # 整点记录在这次合约中一直开启 (词条移除后也是)
        till = dict(self.live.get("rand_until") or {})
        till.pop(tid, None)
        if until is not None:
            till[tid] = _iso(until)
        self.live["rand_until"] = till
        if tid in SUPPLY:
            self.live["supply"] = round(max(self.pre * self._supply_share(terms), self.pre_used), 1)
        if tid in NET_SHARE:
            self.live.pop("net", None)       # 之前选中过又移除了: 按现在的剩余额度重新算
        self.save()

    def rand_until(self, tid: str):
        """未知变量选中的这个词条什么时候移除 (选中的那个小时结束时); 一直有效时 None."""
        if tid not in self.rand:
            return None
        return _parse((self.live.get("rand_until") or {}).get(tid))

    def expire(self, now: datetime) -> list:
        """未知变量选中的词条: 那个小时结束了就移除 (不算违反; 还没生效的也去掉). 返回移除的 (已生效的) 词条."""
        wait = self.waiting
        if any(b <= now for a, b in wait.values()):
            self.live["rand_wait"] = {t: [_iso(a), _iso(b)] for t, (a, b) in wait.items() if b > now}
            self.save()
        till = dict(self.live.get("rand_until") or {}) if self.active else {}
        gone = [t for t, at in till.items() if _parse(at) is None or _parse(at) <= now]
        if not gone:
            return []
        rand = self.rand
        out = [t for t in gone if t in rand]
        self.live["terms"] = [t for t in self.terms if t not in out]
        self.live["rand"] = [t for t in rand if t not in out]
        self.live["rand_until"] = {t: at for t, at in till.items() if t not in gone}
        self.save()
        return out
