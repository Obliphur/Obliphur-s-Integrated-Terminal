"""进行中的合约: 状态 / 词条 / 危机等级 / 开始与结束 / 到期与空闲累计 (MECHANICS 18.1, 18.2)。

从 contract.py 拆出来的 mixin; 依赖方向: contract.py 导入它们, 它们从不导入 contract.py.
"""

import random
from datetime import datetime, timedelta

from contract_parts.contract_rules import (
    IDLE_LIMIT, BY_ID, ECHO_COOLDOWN, clean_theme, combo_level, level_of, life_of, level_label,
    rest_reward, rest_share, duration_of, clean_terms, _iso, _parse, _now_ms, _fog_words,
)


class LiveMixin:
    @property
    def active(self) -> bool:
        return self.live.get("state") == "active"

    @property
    def terms(self) -> list:
        return list(self.live.get("terms") or []) if self.active else []

    def has(self, tid: str) -> bool:
        return tid in self.terms

    @property
    def lost(self) -> list:
        """被违反到失效的词条 (失效前的最低一档; 不算未知变量选中的和录像)."""
        v = self.live.get("lost") if self.active else None
        return [t for t in v if t in BY_ID] if isinstance(v, list) else []

    def watching(self, tid: str) -> bool:
        """这个词条要不要检查: 选着, 或者已经被违反到失效."""
        return self.has(tid) or tid in self.lost

    def echo(self, tid: str) -> bool:
        """触发的是已经失效的词条: 只响警报, 不扣目标生命值."""
        return bool(tid) and not self.has(tid) and tid in self.lost

    def echo_due(self, tid: str, now: datetime = None) -> bool:
        """已失效的词条再次触发的警报: 每个词条 ECHO_COOLDOWN 秒最多响一次 (只记在本机)."""
        now = now or datetime.now()
        e = self.__dict__.setdefault("_echo", {})
        last = e.get(tid)
        if last is not None and (now - last).total_seconds() < ECHO_COOLDOWN:
            return False
        e[tid] = now
        return True

    def _lost_of(self, table) -> str:
        """已经失效的词条里属于 table 的那个 (这一类现在没有选着的)."""
        if any(t in table for t in self.terms):
            return ""
        return next((t for t in self.lost if t in table), "")

    @property
    def start_at(self):
        return _parse(self.live.get("start")) if self.active else None

    @property
    def end_at(self):
        return _parse(self.live.get("end")) if self.active else None

    @property
    def cut(self) -> dict:
        """只有一档的词条被降了几级."""
        c = self.live.get("cut") if self.active else None
        return c if isinstance(c, dict) else {}

    def term_level(self, tid: str) -> int:
        return BY_ID[tid][2] - int(self.cut.get(tid) or 0)

    @property
    def rand(self) -> list:
        """未知变量随机启用的词条 (不算进危机等级; 可以点击关闭, 算一次违反)."""
        r = self.live.get("rand") if self.active else None
        return [t for t in r if t in self.terms] if isinstance(r, list) else []

    @property
    def mine(self) -> list:
        """开始时选的词条现在还在的 (降级后算现在的; 不算未知变量选中的). 词条组合只看这些."""
        rand = self.rand
        return [t for t in self.terms if t in BY_ID and t not in rand]

    @property
    def level(self) -> int:
        mine = self.mine
        return sum(self.term_level(t) for t in mine) + combo_level(mine)

    @property
    def orig(self) -> list:
        """开始时选的词条 (降级前)."""
        return clean_terms(self.live.get("orig") or self.terms) if self.active else []

    @property
    def orig_level(self) -> int:
        return level_of(self.orig)

    def level_text(self) -> str:
        """合约页 / 侧边栏上的危机等级 (见 level_label); 没有进行中的合约时按草稿算."""
        return level_label(self.level, self.orig_level) if self.active else str(level_of(self.draft))

    @property
    def violations(self) -> int:
        return int(self.live.get("violations") or 0) if self.active else 0

    @property
    def violation_limit(self) -> int:
        """初始目标生命值."""
        return life_of(self.terms)

    @property
    def life(self) -> int:
        """目标生命值还剩多少 (每违反一次减一, 减到 0 合约失败)."""
        return max(0, self.violation_limit - self.violations)

    @property
    def rest(self) -> float:
        """合约中已经给出的休息时间 (秒, 已乘疲惫)."""
        return float(self.live.get("rest") or 0) if self.active else 0.0

    @property
    def reward(self) -> float:
        """完成时给的休息时间 (秒): 按现在的等级查表, 加上凑齐的组合的结束后收益和主题加成; 结算按降级后的等级."""
        return float(rest_reward(self.level, self.mine, self.theme)) if self.active else 0.0

    @property
    def theme(self) -> str:
        """开始合约时用的主题 (起源 ops / 都市之意 urban; 没记的 = "")."""
        return clean_theme(self.live.get("theme")) if self.active else ""

    @property
    def owner(self) -> str:
        return str(self.live.get("owner") or "")

    def set_draft(self, terms):
        self.draft = clean_terms(terms, prereq=True)
        self.save()

    def start(self, terms, owner: str = "", now: datetime = None, pre: float = 0, cam: str = "",
              theme: str = "", stash: float = 0) -> dict:
        """pre: 开始时积攒的休息时间 (秒). 合约中暂停休息时这部分不打折; 支援短缺限制它能用几成.
        stash: 联合行动 (1.0.404): 合约开始前积攒的休息时间收起来 (pre = 0: 合约中看不到, 不能用, 也不会触发支援短缺), 合约结束后原样还回去.
        cam: 目光由哪台设备的摄像头录 (设备 id, MECHANICS 18.8). theme: 合约主题 (THEMES, 完成时的主题加成)."""
        now = (now or datetime.now()).replace(microsecond=0)
        terms = clean_terms(terms, prereq=True)
        pre = max(0.0, float(pre))
        share = self._supply_share(terms)
        self.live = {"state": "active", "id": _now_ms(), "start": _iso(now),
                     "end": _iso(now + timedelta(seconds=duration_of(terms))),
                     "terms": terms, "orig": list(terms), "cut": {}, "violations": 0, "rest": 0,
                     "owner": owner, "idle_since": None, "idle_spans": [], "net": None, "first_break": False,
                     "pre": round(pre, 1), "pre_left": round(pre, 1), "pre_used": 0,
                     "supply": None if share is None else round(pre * share, 1), "hl": [],
                     "rand": [], "rand_n": 0, "fog_words": _fog_words(random.Random())}
        if "gaze" in terms and cam:
            self.live["cam"] = str(cam)
        if clean_theme(theme):
            self.live["theme"] = clean_theme(theme)
        if float(stash) > 0:
            self.live["stash"] = round(float(stash), 1)
        self._watch = {}
        self.save()
        return self.live

    @property
    def stash(self) -> float:
        """联合行动开始时收起来的休息时间 (秒), 合约结束才还回去."""
        return float(self.live.get("stash") or 0) if self.active else 0.0

    def unstash(self) -> float:
        """把收起来的休息时间拿出来 (联合行动其实没有开始: 还回去). 返回秒数."""
        x = self.stash
        if x > 0:
            share = self._supply_share(self.live.get("terms"))
            self.live.pop("stash", None)
            self.live.update({"pre": round(x, 1), "pre_left": round(x, 1), "pre_used": 0,
                              "supply": None if share is None else round(x * share, 1)})      # 像一开始就没收起来一样: 这部分又是合约前积攒的 (支援短缺照常)
            self.save()
        return x

    def tag_joint(self, name: str):
        """进行中的合约是联合行动: 记下对象的名字 (结束时进合约记录)."""
        name = str(name or "").strip()[:40]
        if self.active and name and self.live.get("joint") != name:
            self.live["joint"] = name
            self.save()

    def finish(self, result: str, now: datetime = None, by: str = "") -> dict:
        """结束合约: result = completed / failed / idle / offline / abandoned. 返回这一条记录 (含给出的休息时间)."""
        if not self.active:
            return {}
        now = (now or datetime.now()).replace(microsecond=0)
        end = min(now, self.end_at or now)
        reward = int(round(self.reward * rest_share(1.0 if result == "completed" else self.progress(end))))
        rec = {"id": self.live.get("id"), "start": self.live.get("start"), "end": _iso(end),
               "planned": self.live.get("end"), "terms": self.terms, "orig": self.orig,
               "level": self.level, "orig_level": self.orig_level, "cut": dict(self.cut),
               "result": result, "violations": self.violations, "reward": reward,
               "pre_left": self.pre_left, "rand": self.rand, "owner": self.live.get("owner", "")}
        if self.stash > 0:
            rec["stash"] = self.stash                       # 联合行动开始时收起来的休息时间: 合约结束时还回去
        if self.theme:
            rec["theme"] = self.theme
        if self.live.get("joint"):
            rec["joint"] = str(self.live["joint"])         # 联合行动的对象 (统计 → 合约界面显示名字)
        if self.live.get("seals"):
            rec["seals"] = list(self.live["seals"])        # 封锁了的记录格 (合约结束后照样锁着)
        slow = self.live.get("slow")
        if isinstance(slow, dict) and slow.get("start"):
            rec["slow"] = slow["start"]                   # 阻滞的那段专注可能比合约结束得晚
        self.history.append(rec)
        self.live = {"state": "none", "owner": by, "first_break": result == "completed"}
        self.save()
        return rec

    def progress(self, now: datetime) -> float:
        """k = 合约进行了多久 / 总时长 (0 ~ 1)."""
        a, b = self.start_at, self.end_at
        if not (a and b) or b <= a:
            return 0.0
        return min(1.0, max(0.0, (now - a).total_seconds() / (b - a).total_seconds()))

    def reward_at(self, now: datetime) -> int:
        """现在结束能给多少休息时间 (秒, 见 rest_share)."""
        return int(round(self.reward * rest_share(self.progress(now)))) if self.active else 0

    @property
    def last(self) -> dict:
        """最近结束的一个合约."""
        return max(self.history, key=lambda h: str(h.get("end")), default={})

    def _idle_spans(self, now: datetime) -> list:
        """合约里空闲过的时间段 [(开始, 结束)]: 已经结束的, 加上正在进行的 (idle_since 到此刻)."""
        out = []
        for ab in self.live.get("idle_spans") or []:
            try:
                a, b = _parse(ab[0]), _parse(ab[1])
            except (TypeError, IndexError):
                continue
            if a is not None and b is not None and b > a:
                out.append((a, b))
        since = _parse(self.live.get("idle_since"))
        if since is not None and now > since:
            out.append((since, now))
        return out

    def _cum(self, now: datetime, offline=None):
        """空闲和设备离线共用累计 IDLE_LIMIT 秒 (MECHANICS 18.1, 不要求连续, 两种重叠的部分只算一次):
        累计到了就 (到的时刻, 到的时候是不是在设备离线), 还没到 None. offline: [(开始, 结束)] 设备离线的时间段
        (已经去掉外出期间和本机关着的那段; 结束可以晚于此刻); 不给就只算空闲."""
        lo = self.start_at
        if not self.active or lo is None:
            return None
        iv = [(max(a, lo), min(b, now), False) for a, b in self._idle_spans(now)]
        iv += [(max(a, lo), min(b, now), True) for a, b in (offline or [])]
        iv = sorted(x for x in iv if x[1] > x[0])
        total, edge = 0.0, None
        for a, b, _off in iv:
            s = a if edge is None else max(a, edge)
            edge = b if edge is None else max(edge, b)
            if b <= s:
                continue
            seg = (b - s).total_seconds()
            if total + seg >= IDLE_LIMIT:
                t = s + timedelta(seconds=IDLE_LIMIT - total)
                return t, any(oa < t <= ob for oa, ob, o in iv if o)
            total += seg
        return None

    def due(self, now: datetime, timer_idle: bool, offline=None):
        """该结束了吗: ('completed' / 'idle', 结束的时刻) 或 ('', None).
        同时记下空闲从什么时候开始 / 什么时候结束 (只由做自动结束的设备调用). 空闲累计 (不要求连续) 超过 IDLE_LIMIT 就结束;
        offline: 设备离线的时间段, 给了就和空闲合起来累计 (到的时候在设备离线的不在这里判, 见 offline_due)."""
        if not self.active:
            return "", None
        end = self.end_at
        since = _parse(self.live.get("idle_since"))
        if timer_idle:
            if since is None:
                self.live["idle_since"] = _iso(now)
                self.save()
        elif since is not None:
            spans = list(self.live.get("idle_spans") or [])
            if now > since:
                spans.append([_iso(since), _iso(now)])
            self.live["idle_spans"] = spans[-300:]
            self.live["idle_since"] = None
            self.save()
        hit = self._cum(now, offline)
        if hit is not None and not hit[1] and hit[0] <= min(now, end):
            return "idle", hit[0]
        if now >= end:
            return "completed", end
        return "", None

    def offline_due(self, devices, now: datetime, floor: datetime, spans=None):
        """设备离线累计超过 OFFLINE_LIMIT (和空闲共用): (设备名, 失败的时刻) 或 None. devices: [(名字, 离线开始的时刻)] 此刻离线的设备;
        spans: 合约以来设备离线的时间段 [(开始, 结束)] (不给就只用 devices 里这一段, 到此刻为止).
        离线从合约开始, 以及 floor (本机这次打开程序的时刻: 本机关着时的事由其他设备判定) 之后才算."""
        if not self.active or not self.start_at:
            return None
        off = spans if spans is not None else [(since, now) for _n, since in devices]
        hit = self._cum(now, [(max(a, floor), b) for a, b in off])
        if hit is None or not hit[1]:
            return None
        return (devices[0][0] if devices else "?"), hit[0]

    def idle_left(self, now: datetime):
        """空闲还能持续多久 (秒, 累计的: 之前空闲过的也扣掉); 不在空闲时 None."""
        if not self.active or _parse(self.live.get("idle_since")) is None:
            return None
        used = sum((b - a).total_seconds() for a, b in self._idle_spans(now))
        return max(0.0, IDLE_LIMIT - used)
