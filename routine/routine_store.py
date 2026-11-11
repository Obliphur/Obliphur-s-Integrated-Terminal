"""对方的实际数据: 时间线 / 整点记录 / 联合行动 / 按天统计的存档 (routine_<编号>.json)。

从 routine.py 拆出来的部分; 依赖方向: routine.py 和后面的 parts 导入它, 它从不导入 routine.py.
"""

import json
from datetime import datetime, timedelta, date as _date

from routine.routine_common import (
    KEEP_JOINT,
    KEEP_DAYS,
    SLEEP_CELL,
    BACKFILL_DAYS,
    SYNC_MAX_DAYS,
    NET_SHARE,
    resolve_list,
    plain_label,
    _iso,
    _dt,
)
from routine.routine_parse import (
    Routine,
)
from routine.routine_plan import (
    fluct_list,
    pick,
    chat_pct,
)
from routine.routine_query import (
    contracts_before,
)

class Store:
    """对方这个人设的"实际发生过的"数据: 时间线 (一段段 [kind, 开始, 结束, 名字]) 和整点记录的格子. 随 data_dir 存盘."""

    def __init__(self, path: str, r: Routine, seed: str):
        self.path, self.r, self.seed = path, r, seed
        self.upto = None                   # 时间线记到什么时候
        self.tl = []                       # [kind, 开始 iso, 结束 iso, 名字]
        self.cells = {}                    # "2026-11-05T09:00" → [分钟数, 文字 ("" = 没有记录)]
        self.joint = []                    # 和用户真的一起完成过的联合行动的合约记录 (按完成先后), 同合约记录的格式 + "id"
        self.dirty = False
        self.load()

    # ---- 存取 ----
    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(d, dict) or d.get("seed") != self.seed:
            return
        try:
            self.upto = _dt(d["upto"]) if d.get("upto") else None
            self.tl = [x for x in d.get("tl", []) if isinstance(x, list) and len(x) == 4]
            self.cells = {k: v for k, v in (d.get("cells") or {}).items() if isinstance(v, list) and len(v) == 2}
            self.joint = [x for x in (d.get("joint") or []) if isinstance(x, dict) and x.get("id") and x.get("end")]
        except (ValueError, TypeError):
            self.upto, self.tl, self.cells = None, [], {}

    def payload(self) -> dict:
        return {"seed": self.seed, "upto": _iso(self.upto) if self.upto else "",
                "tl": [list(x) for x in self.tl], "cells": {k: list(v) for k, v in self.cells.items()},
                "joint": [dict(x) for x in self.joint]}     # 副本: 后台线程写盘时主线程还在改

    # ---- 真的完成过的联合行动 ----
    def add_joint(self, rec: dict) -> bool:
        """记一条真实完成的联合行动合约 (rec["id"] 去重). 返回是不是新记的."""
        if not rec.get("id") or any(x.get("id") == rec["id"] for x in self.joint):
            return False
        self.joint.append(dict(rec))
        self.joint = sorted(self.joint, key=lambda x: x["end"])[-KEEP_JOINT:]
        self.dirty = True
        return True

    def recent_contracts(self, now: datetime, n: int = 5) -> list:
        """合约记录 (最近完成的 n 个, 从新到旧): 真的一起完成过的联合行动排在前面, 不够的用按日程生成的补."""
        real = sorted((x for x in self.joint if _dt(x["end"]) <= now), key=lambda x: x["end"], reverse=True)
        return (real + contracts_before(self.r, now, self.seed))[:n]

    # ---- 时间线 ----
    def _append(self, kind: str, a: datetime, b: datetime, label: str):
        if b <= a:
            return
        if self.tl and self.tl[-1][0] == kind and self.tl[-1][3] == label and self.tl[-1][2] == _iso(a):
            self.tl[-1][2] = _iso(b)
        else:
            self.tl.append([kind, _iso(a), _iso(b), label])

    def fill_timeline(self, now: datetime, segs_fn, override=None):
        """把时间线补到 now (整分钟). segs_fn(a, b) → [seg] (现在生效的日程, 联合行动之后重算过的); override: (kind, label[, since]) = 对方在联合行动里,
        从 since (没有 = 整段) 起的时间按这个记, since 之前的按日程补.
        没记到的部分 (好几天没调用) 按日程补齐. 第一次: 从 BACKFILL_DAYS 天前的 0 点开始."""
        now = now.replace(second=0, microsecond=0)
        if self.upto is None:
            self.upto = datetime.combine(now.date() - timedelta(days=BACKFILL_DAYS), datetime.min.time())
        floor = datetime.combine(now.date() - timedelta(days=SYNC_MAX_DAYS), datetime.min.time())
        if self.upto < floor:
            self.upto = floor
        if now <= self.upto:
            return
        a = self.upto
        since = override[2].replace(second=0, microsecond=0) if override and len(override) > 2 and override[2] else None
        if override and (since is None or since <= a):
            self._append(override[0], a, now, override[1])
        else:
            stop = now if not override else min(now, since)       # 联合行动开始之前没记到的部分 (好几分钟没调用) 按日程补, 不算联合行动
            cur = a
            for s in segs_fn(a, stop):
                s0, s1 = max(cur, s["start"]), min(stop, s["end"])
                if s0 > cur:
                    self._append("idle", cur, s0, "空闲")
                if s1 > s0:
                    self._append(s["kind"], s0, s1, s["label"])
                    cur = s1
            if cur < stop:
                self._append("idle", cur, stop, "空闲")
            if override and stop < now:
                self._append(override[0], stop, now, override[1])
        self.upto = now
        self.dirty = True
        cut = _iso(datetime.combine(now.date() - timedelta(days=KEEP_DAYS), datetime.min.time()))
        self.tl = [x for x in self.tl if x[2] > cut]
        self.cells = {k: v for k, v in self.cells.items() if k >= cut}

    def segments(self, a: datetime, b: datetime) -> list:
        """[a, b) 里已经发生的时间线段 (截断到 [a, min(b, upto)])."""
        out = []
        if self.upto is None:
            return out
        b = min(b, self.upto)
        for kind, s0, s1, label in self.tl:
            x, y = max(a, _dt(s0)), min(b, _dt(s1))
            if y > x:
                out.append((kind, x, y, label))
        return out

    def minutes(self, a: datetime, b: datetime, kinds=None) -> float:
        return sum((y - x).total_seconds() / 60.0 for k, x, y, _l in self.segments(a, b) if kinds is None or k in kinds)

    # ---- 整点记录 ----
    def fill_cells(self, now: datetime, msgs_fn, fine_fn=None):
        """把还没记的整点记录格子补上 (时间线覆盖到的、已经结束的所有格子; 好几天没调用时一起补). 每小时一格, 细化规范生效的小时
        (fine_fn(整点) 为真) 分成两个半小时. msgs_fn(a, b) → 这个区间里用户发了几条消息."""
        if self.upto is None or not self.tl:
            return
        first, limit = _dt(self.tl[0][1]), min(now, self.upto)
        d = max(first.date(), limit.date() - timedelta(days=SYNC_MAX_DAYS))
        while d <= limit.date():
            day0 = datetime.combine(d, datetime.min.time())
            for h in range(24):
                t = day0 + timedelta(hours=h)
                cells = [(t, 30), (t + timedelta(minutes=30), 30)] if (fine_fn and fine_fn(t)) else [(t, 60)]
                for st, ln in cells:
                    if st < first or st + timedelta(minutes=ln) > limit:
                        continue
                    key = f"{st:%Y-%m-%dT%H:%M}"
                    if self.cells.get(key, ["", ""])[1] != "":      # 空文本 = 旧版本留下的睡着的格子 (1.0.368 之前不写"睡眠"): 重新算一次
                        continue
                    n = msgs_fn(st, st + timedelta(minutes=ln)) if msgs_fn else 0
                    self.cells[key] = [ln, self.cell_text(st, ln, n)]
                    self.dirty = True
            d += timedelta(days=1)

    def cell_text(self, st: datetime, ln: int, n_msgs: int) -> str:
        """这一格对方做了什么 ("" = 时间线里没有这段时间). 睡眠占一半以上 → "睡眠" (同 OIT 的整点记录); 专注为主 → 专注填写列表; 吃饭这类有名字的活动为主 → 活动的名字; 其余 → 休息填写列表 (聊天的概率随用户发的消息数变)."""
        en = st + timedelta(minutes=ln)
        by = {}                                            # (kind, 名字) → 分钟
        for kind, x, y, label in self.segments(st, en):
            by[(kind, label)] = by.get((kind, label), 0.0) + (y - x).total_seconds() / 60.0
        if not by:
            return ""
        sleep = sum(v for (k, _l), v in by.items() if k == "sleep")
        if sleep >= ln / 2.0:                              # OIT 的规则 (MECHANICS 8.2): 睡眠占了这一格一半以上 (一小时 = 30 分钟以上) 记成"睡眠"
            return SLEEP_CELL
        awake = sum(v for (k, _l), v in by.items() if k != "sleep")
        focus = sum(v for (k, _l), v in by.items() if k == "focus")
        day = f"{st:%Y-%m-%d}"
        key = f"{self.seed}|cell|{st:%Y-%m-%dT%H:%M}"
        if focus >= awake / 2.0 and focus > 0:
            return pick(resolve_list(self.r.lists.get("整点记录专注填写列表", [])), key) or (self.r.focus_label or "专注")
        named = {}
        for (k, label), v in by.items():
            if k == "idle" and not self.plain(label):
                named[label] = named.get(label, 0.0) + v
        if named:
            label, v = max(named.items(), key=lambda kv: kv[1])
            if v >= (awake - focus) / 2.0:
                return label
        items = self.r.lists.get("整点记录休息填写列表", [])
        p = chat_pct(n_msgs, ln)
        return pick(resolve_list(items, p), key) or "休息"

    @staticmethod
    def plain(label: str) -> bool:
        return plain_label(label)

    def log_lines(self, now: datetime) -> list:
        """今天已经结束的格子: ["09:00 英语", ...]; 半小时的格子写 "09:30"."""
        day = f"{now:%Y-%m-%d}"
        out = []
        for key in sorted(self.cells):
            ln, text = self.cells[key]
            if not key.startswith(day) or not text:
                continue
            st = _dt(key)
            if st + timedelta(minutes=ln) > now:           # 没结束的格子不给
                continue
            out.append(f"{st:%H:%M} {text}")
        return out

    # ---- 按天的统计 ----
    def day_range(self, d: _date) -> tuple:
        a = datetime.combine(d, datetime.min.time())
        return a, a + timedelta(days=1)

    def day_minutes(self, d: _date, kinds) -> float:
        a, b = self.day_range(d)
        return self.minutes(a, b, kinds)

    def awake_free_minutes(self, d: _date) -> float:
        """清醒但不在专注的分钟 (休息 + 空闲): 联网 / 第二台设备的使用都落在这段时间里."""
        return self.day_minutes(d, ("rest", "idle"))

    def month_days(self, now: datetime) -> list:
        n = now.date()
        return [_date(n.year, n.month, i) for i in range(1, n.day + 1)]

    def net_by_reason(self, d: _date) -> dict:
        """d 这天每个联网理由的分钟数 (只算已经过去的部分)."""
        total = self.awake_free_minutes(d) * NET_SHARE
        items = self.r.lists.get("联网时间理由列表", [])
        if not items or total <= 0:
            return {}
        weights = fluct_list(items, f"{self.seed}|net|{d}", "联网时间理由列表" in self.r.base)
        return {n: total * p / 100.0 for n, p in weights if p > 0}

    def window_seconds(self, d: _date, dev: int) -> dict:
        """d 这天第 dev 台设备每个程序的秒数 (第一台用在专注的时间, 其余用在休息 / 空闲的时间; 名单没写满 100% 的剩下算"空白")."""
        name = self.r.windows.get(dev)
        items = self.r.lists.get(name or "", [])
        if not items:
            return {}
        used = self.day_minutes(d, ("focus",)) if dev == 0 else self.awake_free_minutes(d)
        weights = fluct_list(items, f"{self.seed}|win{dev}|{d}", name in self.r.base)
        out = {}
        for n, p in weights:
            if p > 0:
                k = "" if n == "空白" else n            # 名单里明写的"空白"和没写满的剩余是同一项
                out[k] = out.get(k, 0.0) + used * 60.0 * p / 100.0
        rest = max(0.0, 100.0 - sum(p for _n, p in weights))
        if rest > 0.05:
            out[""] = out.get("", 0.0) + used * 60.0 * rest / 100.0
        return out

