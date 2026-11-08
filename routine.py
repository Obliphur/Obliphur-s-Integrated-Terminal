"""人设日程引擎 (1.0.355): 读 <人设>_routine.txt, 按"持续时间"一段接一段地算出对方每天的日程, 并生成对方的 OIT 数据.

和旧的"每天重复的固定钟点"日程 (comm.schedule_entries) 不同:
  · 日程是一串**持续时间**: 起床 (初始起点 + 起床波动) 之后, 一项接一项; 做完最后一项就进入睡眠, 睡到第二天起床. 没有固定的睡觉钟点.
  · 每天的波动 (起床 / 专注开始 / 专注结束 / 睡眠开始) 由 (日期 + 人设) 决定, 同一天任何设备算出来都一样 (sha256, Python 和 Kotlin 一致).
    起床波动平移整天; 专注开始 / 结束的波动挪动专注段的两端 (晚开始则前一段变长, 早开始则前一段被截短, 不留空档); 睡眠开始的延迟算"空闲".
  · 循环 (1.0.400) 总是以休息结束: 不直接截断最后一轮, 而是让专注提前结束, 休息的末尾对上循环的末尾; 最后那次休息再比循环的结束晚 [循环末尾休息结束时间波动] 分钟, 后面的日程整体往后推.
  · 所有数据都是**实时算**的: 只给已经发生的部分 (整点记录只给已经结束的格子, 计时器 / 日历 / 统计只算到现在).
  · 算过的会存下来 (Store): 时间线 (实际发生的一段段) 和整点记录的格子. 好几天没被调用 (程序没开 / 没人查) 之后, 缺的部分按规则补齐.

这个模块不依赖 comm (comm 依赖它); 只用 config.compute_F 算休息时长, contract 算合约记录.
"""
import hashlib
import json
import math
import os
import re
from datetime import datetime, timedelta, date as _date

import config

KEEP_JOINT = 120                  # 存下来的真实联合行动合约记录最多留多少条
KEEP_DAYS = 35                    # 存下来的时间线 / 整点记录最多留多少天
SLEEP_CELL = "睡眠"                # 睡着的整点记录格子的内容 (同 OIT 自己的整点记录)
BACKFILL_DAYS = 7                # 第一次使用 (没有存档) 时往前补多少天
SYNC_MAX_DAYS = 14                # 一次最多补多少天 (很久没开程序也只补最近这些)
CHAT_FULL = 50.0                  # 上一个区间用户发了 >= CHAT_FULL_MSGS 条 → "聊天" 的概率 (%)
CHAT_FULL_MSGS = 20               # (一个小时的区间; 半小时的格子按比例是 10 条)
BASE_FLUCT = 0.02                 # (基准数据) 每一项概率的波动比例 ±2%
NET_SHARE = 0.25                  # 联网总时长 = 清醒但不在专注的时间 × 这个比例
NET_QUOTA_S = 2 * 3600.0          # 对方的每日联网额度 (秒; 和用户自己的设置无关, 电脑和手机一样)
CONTRACT_DAY_P = 0.3              # 每天完成一个合约的概率 (对方的合约记录)
NAMES_NO_PROB = ("设备列表",)

_NUM = r"(\d+(?:\.\d+)?)"
_UNIT = r"(min|mins|分钟|h|hr|hrs|小时)"
_DUR_RE = re.compile(r"^[ \t]*(?:[-*•][ \t]*)?" + _NUM + r"[ \t]*" + _UNIT + r"[ \t]*[:：][ \t]*(\S[^\n]*)$", re.I | re.M)
_LOOP_RE = re.compile(r"^[ \t]*(?:[-*•][ \t]*)?[\[［][ \t]*(?:循环|loop|cycle|repeat)[ \t]*" + _NUM + r"[ \t]*" + _UNIT + r"?[ \t]*[\]］]"
                      r"[ \t]*(?:[\[［][ \t]*" + _NUM + r"[ \t]*" + _UNIT + r"?[ \t]*[\]］])?", re.I | re.M)
_START_RE = re.compile(r"初始起点[ \t]*[:：][ \t]*(\d{1,2})[:：](\d{2})")
_FOCUS_RE = re.compile(r"^[ \t]*专注时段[ \t]*[:：][ \t]*(\S[^\n]*)$", re.M)
_RANGE = r"[\[［][ \t]*([+-]?\d+(?:\.\d+)?)[ \t]*[,，~～][ \t]*([+-]?\d+(?:\.\d+)?)[ \t]*[\]］]"
_FLUCT_KEYS = (("focus_start", "下一个专注开始节点波动"), ("focus_end", "下一个专注结束节点波动"),
               ("sleep_start", "睡眠开始时间波动"), ("sleep_end", "睡眠结束时间波动"),
               ("rest_end", "循环末尾休息结束时间波动"))          # 1.0.400: 每个循环最后那次休息比循环的结束晚多少分钟 (没写 = 不晚, 正好对上)
_LIST_RE = re.compile(r"^[ \t]*(?:[-*•][ \t]*)?([^\n{}]+?)[ \t]*[:：][ \t]*[{｛]([^{}｛｝\n]*)[}｝][ \t]*(\([ \t]*基准数据[ \t]*\)|（[ \t]*基准数据[ \t]*）)?", re.M)
_DEV_WIN_RE = re.compile(r"^设备[-－](\d+)[ \t]*[:：][ \t]*窗口使用时间$")
_ITEM_RE = re.compile(r"^[ \t]*(X|x|\d+(?:\.\d+)?)[ \t]*[%％][ \t]*[:：][ \t]*(\S.*)$")
_DIFF_RE = re.compile(r"合约记录难度[ \t]*[:：]" + r"[ \t]*" + _RANGE)


# ══════════════ 确定性随机 (sha256: Python 和 Kotlin 算出一样的数) ══════════════
def u01(key: str) -> float:
    h = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") / float(1 << 64)


def rint(key: str, lo, hi) -> int:
    """[lo, hi] 里的整数 (两端都可能取到)."""
    lo, hi = int(round(min(lo, hi))), int(round(max(lo, hi)))
    return lo + min(hi - lo, int(u01(key) * (hi - lo + 1)))


# ══════════════ 解析 ══════════════
def _minutes(v: float, unit: str) -> float:
    u = (unit or "min").lower()
    return float(v) * (60.0 if u.startswith("h") or u == "小时" else 1.0)


def parse_list(body: str) -> list:
    """"{30%:英语,数学,语文}" 里的内容 → [{"name", "pct" (没写 = None), "x" (写 X% = 随聊天变)}]; 没写概率的项按剩下的概率平分 (resolve_list)."""
    out = []
    for raw in re.split(r"[,，]", body or ""):
        raw = raw.strip()
        if not raw:
            continue
        m = _ITEM_RE.match(raw)
        if m:
            x = m.group(1) in ("X", "x")
            out.append({"name": m.group(2).strip(), "pct": None if x else float(m.group(1)), "x": x})
        else:
            out.append({"name": raw, "pct": None, "x": False})
    return out


def resolve_list(items: list, x_value: float = None) -> list:
    """没写概率的项按其他项剩下的概率平分 (规则 IV). x_value: 写成 X% 的项此刻的概率 (None = 当 0). 返回 [(名字, 概率%)], 总和 <= 100."""
    fixed = sum((x_value or 0.0) if it["x"] else (it["pct"] or 0.0) for it in items if it["x"] or it["pct"] is not None)
    free = [it for it in items if not it["x"] and it["pct"] is None]
    share = max(0.0, 100.0 - fixed) / len(free) if free else 0.0
    out = []
    for it in items:
        p = (x_value or 0.0) if it["x"] else (it["pct"] if it["pct"] is not None else share)
        out.append((it["name"], max(0.0, p)))
    return out


class Routine:
    def __init__(self):
        self.focus_label = ""
        self.start = None             # 初始起点 (从 0:00 起的分钟)
        self.shifts = []              # 起点平移 (1.0.402): [(日期, 分钟)] 按日期排, 从那一天起起点换成这个分钟数; 记在聊天状态里, 不改日程文件
        self.items = []               # {"type": "block", "dur", "label"} / {"type": "loop", "dur", "focus"}
        self.fluct = {k: (0, 0) for k, _n in _FLUCT_KEYS}
        self.lists = {}               # 列表名 → [item]
        self.base = set()             # 标了 (基准数据) 的列表名
        self.devices = []
        self.windows = {}             # 设备序号 (从 0) → 列表名
        self.diff = (20, 30)
        self.skip_terms = []
        self.bad_terms = []

    def ok(self) -> bool:
        return self.start is not None and bool(self.items)

    def start_for(self, d: _date) -> int:
        """d 这一天的初始起点 (从 0:00 起的分钟): 日程文件里写的, 被平移过的话是最近一次生效的平移 (生效日期 <= d)."""
        s = self.start
        for day, m in self.shifts:
            if day <= d:
                s = m
            else:
                break
        return s

    def set_shifts(self, shifts) -> bool:
        """换一份起点平移记录 [(日期, 分钟)]; 变了就清掉算好的日程缓存. 返回变没变. Kotlin: Routine.setShifts."""
        sh = sorted((d, int(m)) for d, m in shifts)
        if sh == self.shifts:
            return False
        self.shifts = sh
        self.__dict__.pop("_plans", None)
        return True


def parse(text: str):
    """routine 文件 → Routine; 没有"初始起点"或一项活动也没有 → None."""
    text = str(text or "")
    r = Routine()
    m = _START_RE.search(text)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        r.start = int(m.group(1)) * 60 + int(m.group(2))
    m = _FOCUS_RE.search(text)
    r.focus_label = m.group(1).strip() if m else ""
    pos = []
    for m in _DUR_RE.finditer(text):
        dur = _minutes(float(m.group(1)), m.group(2))
        if dur > 0:
            pos.append((m.start(), {"type": "block", "dur": dur, "label": m.group(3).strip()}))
    for m in _LOOP_RE.finditer(text):
        dur = _minutes(float(m.group(1)), m.group(2) or "min")
        foc = _minutes(float(m.group(3)), m.group(4) or "min") if m.group(3) else 100.0
        if dur > 0:
            pos.append((m.start(), {"type": "loop", "dur": dur, "focus": foc if foc > 0 else 100.0}))
    r.items = [it for _p, it in sorted(pos, key=lambda x: x[0])]
    for key, name in _FLUCT_KEYS:
        m = re.search(re.escape(name) + r"[ \t]*[:：][ \t]*" + _RANGE, text)
        if m:
            r.fluct[key] = (float(m.group(1)), float(m.group(2)))
    for m in _LIST_RE.finditer(text):
        name, body, base = m.group(1).strip(), m.group(2), m.group(3)
        if name in NAMES_NO_PROB:
            r.devices = [x.strip() for x in re.split(r"[,，]", body) if x.strip()]
            continue
        r.lists[name] = parse_list(body)
        if base:
            r.base.add(name)
        dm = _DEV_WIN_RE.match(name)
        if dm:
            r.windows[int(dm.group(1)) - 1] = name
    m = _DIFF_RE.search(text)
    if m:
        r.diff = (int(float(m.group(1))), int(float(m.group(2))))
    for key, name in (("skip_terms", "不选择的词条"), ("bad_terms", "违规词条")):
        items = r.lists.get(name)
        if items is None:
            m = re.search(re.escape(name) + r"[ \t]*[:：][ \t]*[{｛]([^{}｛｝\n]*)[}｝]", text)
            items = parse_list(m.group(1)) if m else []
        setattr(r, key, [it["name"] for it in items])
    return r if r.ok() else None


# ══════════════ 日程 ══════════════
def _secs(minutes: float) -> int:
    """分钟 → 整秒 (四舍五入; Kotlin: Routine.secs 一样)."""
    return int(math.floor(float(minutes) * 60.0 + 0.5))


def _rest_min(f_min: float) -> int:
    """专注 f_min 分钟之后休息多少整分钟 = round(F(f)), 至少 1 分钟."""
    return max(1, int(round(config.compute_F(f_min))))


def loop_pieces(total_s: int, n_s: int) -> list:
    """一个循环 (总长 total_s 秒, 每轮专注 n_s 秒) → [("focus" | "rest", 秒)]. 休息 = F(这一轮的专注) 取整分钟.
    1.0.400 起不再把最后一轮直接截断: 循环**总是以休息结束**, 休息的末尾正好对上循环的末尾, 靠"提前结束专注"凑 ——
      · 满轮 (专注 n + 休息 F(n)) 能排几轮排几轮;
      · 剩下的时间 R 做最后一轮: 专注提前结束, 休息**随这一轮的专注长度重新算** (解 f + F(f) = R, 休息取整分钟, 专注 = R - 休息);
      · 剩下的时间太短 (最后一轮专注不到 1 分钟): 并进上一次休息 (上一次休息一直到循环结束);
      · 总长连这样都放不下 (一轮都排不了): 退回成只有一段专注 (整个循环).
    Kotlin: Routine.loopPieces."""
    rest_n = _rest_min(n_s / 60.0) * 60
    cyc = n_s + rest_n
    k = total_s // cyc
    out = []
    for _ in range(k):
        out.append(("focus", n_s))
        out.append(("rest", rest_n))
    left = total_s - k * cyc
    if left > 0:
        lo, hi = 0.0, left / 60.0                       # 二分解 f + F(f) = left (F 单调增)
        for _ in range(60):
            mid = (lo + hi) / 2.0
            if mid + config.compute_F(mid) < left / 60.0:
                lo = mid
            else:
                hi = mid
        rest_s = _rest_min(hi) * 60
        if left - rest_s >= 60:
            out.append(("focus", left - rest_s))
            out.append(("rest", rest_s))
        elif out:
            out[-1] = ("rest", out[-1][1] + left)
        else:
            out.append(("focus", total_s))
    return out


def loop_segs(start: datetime, end: datetime, n_min: float, extra: timedelta = None) -> list:
    """[start, end) 这个循环里的 专注 / 休息 段 (见 loop_pieces: 以休息结束, 末尾对上 end).
    extra: 最后那次休息的结束比 end 晚多少 (可以是负的, 但休息至少留 1 分钟); 调用的人要把后面的日程也平移同样的时间."""
    out, t = [], start
    fm = 0.0
    for kind, s in loop_pieces(int(math.floor((end - start).total_seconds() + 0.5)), _secs(n_min)):
        e = t + timedelta(seconds=s)
        if kind == "focus":
            fm = s / 60.0
        out.append(seg(kind, t, e, "专注" if kind == "focus" else "休息", fm=fm))      # fm = 这一轮 (专注 + 随后的休息) 计划的专注分钟, 提前结束的比 n 短
        t = e
    if extra and out and out[-1]["kind"] == "rest":
        out[-1]["end"] = max(out[-1]["start"] + timedelta(minutes=1), out[-1]["end"] + extra)
    return out


def seg(kind: str, a: datetime, b: datetime, label: str, **kw) -> dict:
    d = {"kind": kind, "start": a, "end": b, "label": label, "detail": "", "loop": None, "gap": False}
    d.update(kw)
    return d


def wake_at(r: Routine, d: _date, seed: str) -> datetime:
    """d 这一天起床的时刻 = 初始起点 (平移过的用平移后的, 见 Routine.start_for) + 睡眠结束时间波动."""
    lo, hi = r.fluct["sleep_end"]
    return datetime.combine(d, datetime.min.time()) + timedelta(minutes=r.start_for(d) + rint(f"{seed}|wake|{d}", lo, hi))


def wake_times(r: Routine, a: datetime, b: datetime, seed: str) -> list:
    """[a, b] 里对方起床的时刻 (按时间排) = 每天睡眠段的结束 (wake_at). 共时闹钟 (1.0.359) 据此响铃; Kotlin: Routine.wakeTimes."""
    out, d = [], a.date() - timedelta(days=1)
    while d <= b.date() + timedelta(days=1):
        w = wake_at(r, d, seed)
        if a <= w <= b:
            out.append(w)
        d += timedelta(days=1)
    return out


def day_plan(r: Routine, d: _date, seed: str, fluct: bool = True) -> list:
    """d 这一天 (从起床到下一次起床) 的日程 → [seg], 按时间排, 首尾相接: 各项活动 → 睡眠开始前的空闲 (如果有) → 睡眠.
    seg 是 {"kind" (focus / rest / idle / sleep), "start", "end", "label", "detail", "loop" (第几个循环段生成的, 没有 = None), "gap" (睡眠延迟留下的空档)}."""
    cache = r.__dict__.setdefault("_plans", {})
    ck = (d, seed, bool(fluct))
    if ck in cache:
        return cache[ck]
    if len(cache) > 12:
        cache.clear()
    cache[ck] = out = _day_plan(r, d, seed, fluct)
    return out


def _day_plan(r: Routine, d: _date, seed: str, fluct: bool) -> list:
    base = wake_at(r, d, seed)
    segs, c = [], base
    for idx, it in enumerate(r.items):
        end = c + timedelta(minutes=it["dur"])
        if it["type"] == "block":
            segs.append(seg("idle", c, end, it["label"], block=idx))
        else:
            lo, hi = r.fluct["rest_end"] if fluct else (0, 0)
            extra = timedelta(minutes=rint(f"{seed}|le|{d}|{idx}", lo, hi))
            part = loop_segs(c, end, it["focus"], extra)
            for s in part:
                s["loop"] = idx
                s["n"] = it["focus"]
                segs.append(s)
            if part:
                end = part[-1]["end"]                      # 最后一次休息晚结束了: 后面的日程整体往后推
        c = end
    n = len(segs)
    ns = [s["start"] for s in segs]
    ne = [s["end"] for s in segs]
    if fluct:
        fs, fe = r.fluct["focus_start"], r.fluct["focus_end"]
        k = 0
        for i, s in enumerate(segs):
            if s["kind"] != "focus":
                continue
            ds = rint(f"{seed}|fs|{d}|{k}", *fs)
            de = rint(f"{seed}|fe|{d}|{k}", *fe)
            k += 1
            cs, ce = s["start"] + timedelta(minutes=ds), s["end"] + timedelta(minutes=de)
            if ce - cs < timedelta(minutes=10):
                continue
            if i > 0 and cs != s["start"]:
                if cs < s["start"]:
                    if cs > ns[i - 1] + timedelta(minutes=1):
                        ne[i - 1] = cs
                        ns[i] = cs
                elif cs > ne[i - 1]:
                    ne[i - 1] = cs                             # 循环里晚开始: 前一段 (休息 / 循环前的活动) 变长, 不留空档
                    ns[i] = cs
            if i + 1 < n and ce != s["end"] and ce < ne[i + 1] - timedelta(minutes=1) and ce > ns[i] + timedelta(minutes=5):
                ne[i] = ce
                ns[i + 1] = ce
    out = []
    for i, s in enumerate(segs):
        out.append(dict(s, start=ns[i], end=ne[i]))
    nxt = wake_at(r, d + timedelta(days=1), seed)
    lo, hi = r.fluct["sleep_start"] if fluct else (0, 0)
    sleep_start = max(c, c + timedelta(minutes=rint(f"{seed}|ss|{d}", lo, hi)))
    last_end = out[-1]["end"] if out else c
    if sleep_start > last_end and sleep_start - c > timedelta(0):
        out.append(seg("idle", last_end, min(sleep_start, nxt), "空闲", gap=True))
    if sleep_start < nxt:
        out.append(seg("sleep", sleep_start, nxt, "睡眠"))
    out.sort(key=lambda s: s["start"])
    return out


def plan_between(r: Routine, a: datetime, b: datetime, seed: str, fluct: bool = True) -> list:
    """[a, b) 里的日程段 (整段, 不截断), 按时间排."""
    out = []
    d = a.date() - timedelta(days=1)
    while d <= b.date():
        for s in day_plan(r, d, seed, fluct):
            if s["end"] > a and s["start"] < b:
                out.append(s)
        d += timedelta(days=1)
    out.sort(key=lambda s: s["start"])
    uniq, last = [], None
    for s in out:                                          # 相邻两天的计划在起床时刻交接: 上一天的睡眠结束 = 这一天的起床, 不会重叠
        if last and s["start"] < last["end"]:
            continue
        uniq.append(s)
        last = s
    return uniq


def shift_for_joint(segs: list, t: datetime, now: datetime) -> list:
    """联合行动之后休息结束的时刻 t 落在某个循环里: 从 t 起"立即开始专注", 到那个循环结束 (之后又是原来的日程; 联合行动之后不再有专注波动);
    落在循环外 (和空闲 / 睡眠 / 吃饭重叠) 不影响. 只在 now >= t 且 t 所在的循环还没结束时才改."""
    if now < t:
        return segs
    for s in segs:
        if s.get("loop") is None or not (s["start"] <= t < s["end"]):
            continue
        same = [x for x in segs if x.get("loop") == s["loop"] and abs((x["start"] - s["start"]).total_seconds()) < 12 * 3600]
        end = max(x["end"] for x in same)
        if now >= end:
            return segs
        n = s.get("n") or 100.0
        new = loop_segs(t, end, n)
        for x in new:
            x["loop"] = s["loop"]
            x["n"] = n
        keep = []
        for x in segs:
            if x in same:
                if x["end"] <= t:
                    keep.append(x)
                elif x["start"] < t:
                    keep.append(dict(x, end=t))
            else:
                keep.append(x)
        return sorted(keep + new, key=lambda x: x["start"])
    return segs


def seg_class(s: dict) -> str:
    """日程段 → 主动发起谈话概率的种类: rest 休息 / special 特殊空闲 (名字里有"特殊空闲") / key 重要活动 (吃饭这类有名字的) /
    idle 普通空闲 (名字就是"空闲", 或者睡眠延迟留下的空档) / focus / sleep."""
    if s["kind"] != "idle":
        return s["kind"]
    if s.get("gap"):
        return "idle"
    head = re.split(r"[(（]", str(s.get("label") or ""), maxsplit=1)[0].strip()
    if "特殊空闲" in head:
        return "special"
    return "idle" if Store.plain(s.get("label")) else "key"


def status_in(segs: list, now: datetime):
    """now 所在的那一段 (没有 = None)."""
    for s in segs:
        if s["start"] <= now < s["end"]:
            return s
    return None


# ══════════════ 带波动的概率 ══════════════
def fluct_list(items: list, seed: str, base: bool, x_value: float = None) -> list:
    """一天里的某个列表 → [(名字, 概率%)]. 标了 (基准数据) 的项每项按 ±BASE_FLUCT 的比例波动 (同一天同一项总是同一个数); 原来加起来是 100% 的波动后仍归一成 100%."""
    res = resolve_list(items, x_value)
    if not base:
        return res
    total = sum(p for _n, p in res)
    out = [(n, p * (1.0 + (u01(f"{seed}|{n}") * 2.0 - 1.0) * BASE_FLUCT)) for n, p in res]
    s2 = sum(p for _n, p in out)
    if total >= 99.99 and s2 > 0:
        out = [(n, p * total / s2) for n, p in out]
    return out


def pick(weights: list, key: str) -> str:
    """按权重 (名字, 概率) 挑一个 (确定性); 全是 0 时返回第一个."""
    tot = sum(p for _n, p in weights)
    if not weights:
        return ""
    if tot <= 0:
        return weights[0][0]
    x = u01(key) * tot
    for n, p in weights:
        x -= p
        if x < 0:
            return n
    return weights[-1][0]


def chat_pct(n_msgs: int, minutes: float) -> float:
    """用户在这个区间 (整点记录的一格) 发了 n 条消息 → "聊天"的概率 (%): 0 条 → 0, 每小时 >= 20 条 → 50%, 中间均匀分配; 半小时的格子 (细化规范) 按比例算."""
    full = max(1.0, CHAT_FULL_MSGS * float(minutes) / 60.0)
    return CHAT_FULL * min(1.0, max(0.0, n_msgs / full))


# ══════════════ 存档 + 数据 ══════════════
def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat(timespec="seconds")


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


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
        head = re.split(r"[(（]", str(label or ""), maxsplit=1)[0].strip().lower()
        return head in ("空闲", "idle", "free", "休息", "rest", "")

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


def contract_for_day(r: Routine, d: _date, seed: str):
    """d 这天对方完成的合约 (没有 = None): 概率 CONTRACT_DAY_P, 词条随机但不碰"不选择 / 违规"的词条, 危机等级落在 [difficulty] 里."""
    if u01(f"{seed}|ct|{d}") >= CONTRACT_DAY_P:
        return None
    import contract as C
    banned = banned_ids(r)
    pool = [t[0] for t in C.TERMS if t[0] not in banned]
    lo, hi = r.diff
    target = rint(f"{seed}|ct|{d}|lv", lo, hi)           # 这个合约要凑到的危机等级 (落在 [lo, hi] 里, 每个合约不一样)
    best = None
    for attempt in range(40):
        order = sorted(pool, key=lambda t: u01(f"{seed}|ct|{d}|{attempt}|{t}"))
        terms = []
        for t in order:
            if C.blocked_by(t, terms):
                continue
            trial = terms + [t]
            if C.needs_of(t, trial) or C.level_of(trial) > hi:
                continue
            terms = trial
            if C.level_of(terms) >= target:
                break
        terms = C.clean_terms(terms, prereq=True)
        lv = C.level_of(terms)
        best = best or terms
        if lo <= lv <= hi:
            best = terms
            break
    terms = best or []
    lv = C.level_of(terms)
    mins = 10 * 60 + rint(f"{seed}|ct|{d}|t", 0, 11 * 60)
    end = datetime.combine(d, datetime.min.time()) + timedelta(minutes=mins)
    return {"result": "completed", "end": _iso(end), "level": lv, "orig_level": lv, "orig": list(terms), "terms": list(terms), "rand": []}


def banned_ids(r: Routine) -> set:
    """"不选择的词条" + "违规词条" 对应的词条 id (按名字匹配: 去掉"环境:"这类前缀和 -I / -II 后缀, 整个系列都算)."""
    import contract as C
    names = {re.sub(r"\s+", "", n) for n in list(r.skip_terms) + list(r.bad_terms)}
    series = set()
    for t in C.TERMS:
        full = t[3]
        short = full.split(":", 1)[-1]
        base = re.sub(r"-(?:I{1,3}|IV|V)$", "", short)
        if short in names or base in names or full in names:
            series.add(t[1])
    return {t[0] for t in C.TERMS if t[1] in series}


def contracts_before(r: Routine, now: datetime, seed: str, days: int = 14) -> list:
    """最近 days 天里已经结束的合约记录 (从新到旧)."""
    out = []
    for i in range(days):
        d = now.date() - timedelta(days=i)
        rec = contract_for_day(r, d, seed)
        if rec and _dt(rec["end"]) <= now:
            out.append(rec)
    out.sort(key=lambda x: x["end"], reverse=True)
    return out


def _selftest():
    import tempfile
    here = os.path.dirname(os.path.abspath(__file__))
    routine_text = open(os.path.join(here, "personas", "Claudia_routine.txt"), encoding="utf-8").read()
    # 1.0.359 起床波动改成了 [-20,0]; 这里仍用 [-20,10] 测 (Kotlin RoutineTest 的期望值是按它算的, 电脑和手机必须一样), 新范围见 comm.py 自检的共时闹钟
    assert "睡眠结束时间波动:[-20,0]" in routine_text
    routine_text = routine_text.replace("睡眠结束时间波动:[-20,0]", "睡眠结束时间波动:[-20,10]")
    r = parse(routine_text)
    assert r and r.start == 390 and r.focus_label == "学习(高考内容)"
    assert [(it["type"], it["dur"]) for it in r.items] == [("block", 30), ("loop", 300), ("block", 30), ("block", 30), ("loop", 300), ("block", 30), ("block", 30), ("loop", 240), ("block", 30)]
    assert [it.get("focus") for it in r.items if it["type"] == "loop"] == [100, 100, 50]
    assert r.fluct == {"focus_start": (-3, 2), "focus_end": (-2, 3), "sleep_start": (0, 5), "sleep_end": (-20, 10), "rest_end": (0, 5)}
    assert parse(routine_text.replace("循环末尾休息结束时间波动:[0,5]", "")).fluct["rest_end"] == (0, 0), "没写 = 不晚"
    assert parse(routine_text.replace("循环末尾休息结束时间波动:[0,5]", "循环末尾休息结束时间波动:[1,7]")).fluct["rest_end"] == (1, 7), "改了文件自动识别"
    assert r.devices == ["DESKTOP-CLAUDIA", "C1999W"] and set(r.windows) == {0, 1}
    assert r.diff == (20, 30) and r.skip_terms == ["无言为真", "持久战"] and r.bad_terms == ["通道阻塞", "细化规范", "乐理之灾", "未知变量"]
    assert "联网时间理由列表" in r.base and "整点记录专注填写列表" not in r.base
    # 规则 IV: 没写概率的平分剩下的; X% 随聊天变
    foc = resolve_list(r.lists["整点记录专注填写列表"])
    assert foc[0] == ("英语", 30.0) and all(abs(p - 14.0) < 1e-9 for _n, p in foc[1:]) and len(foc) == 6
    rest = resolve_list(r.lists["整点记录休息填写列表"], 20.0)
    assert rest[0] == ("聊天", 20.0) and all(abs(p - 20.0) < 1e-9 for _n, p in rest[1:])
    assert resolve_list(r.lists["整点记录休息填写列表"], 0.0)[1][1] == 25.0
    # 规则 V: 聊天概率
    assert chat_pct(0, 60) == 0 and chat_pct(20, 60) == 50 and chat_pct(40, 60) == 50 and chat_pct(10, 60) == 25
    assert chat_pct(10, 30) == 50 and chat_pct(5, 30) == 25, "细化规范的半小时格: 阈值按比例减半"
    # 规则 VI: 基准数据 ±2%, 加起来是 100% 的归一
    for day in range(30):
        fl = fluct_list(r.lists["联网时间理由列表"], f"s|{day}", True)
        assert abs(sum(p for _n, p in fl) - 100.0) < 1e-6
        for (n, p), (n0, p0) in zip(fl, resolve_list(r.lists["联网时间理由列表"])):
            assert abs(p / p0 - 1) <= 0.0201 * 100 / 100 + 0.021, (n, p, p0)
    w = fluct_list(r.lists["设备-1:窗口使用时间"], "s|w", True)
    assert all(abs(p / p0 - 1) <= 0.045 for (_n, p), (_m, p0) in zip(w, resolve_list(r.lists["设备-1:窗口使用时间"]))), "窗口项: 每项 ±2% (名单加起来 100% 时再归一, 最多约 ±4%)"
    assert fluct_list(r.lists["整点记录专注填写列表"], "s|x", False) == resolve_list(r.lists["整点记录专注填写列表"])
    # 日程: 持续时间依次排下去, 做完最后一项进入睡眠; 起床波动平移整天
    seed = "t"
    saw_early = False
    for k in range(60):
        d = _date(2026, 3, 1) + timedelta(days=k)
        plan = day_plan(r, d, seed)
        wk = wake_at(r, d, seed)
        assert datetime.combine(d, datetime.min.time()) + timedelta(minutes=390 - 20) <= wk <= datetime.combine(d, datetime.min.time()) + timedelta(minutes=390 + 10)
        assert plan[0]["start"] == wk and plan[0]["label"] == "洗漱,吃早饭" and timedelta(minutes=27) <= plan[0]["end"] - plan[0]["start"] <= timedelta(minutes=33)
        for a, b in zip(plan, plan[1:]):
            assert a["end"] == b["start"], (a, b)                    # 首尾相接, 没有重叠也没有缺口
        assert plan[-1]["kind"] == "sleep" and plan[-1]["end"] == wake_at(r, d + timedelta(days=1), seed)
        late = sum(rint(f"{seed}|le|{d}|{i}", 0, 5) for i, it in enumerate(r.items) if it["type"] == "loop")      # 三个循环最后一次休息各自晚结束的分钟, 后面的日程跟着推
        act_end = wk + timedelta(minutes=sum(it["dur"] for it in r.items) + late)
        s_start = plan[-1]["start"]
        assert act_end <= s_start <= act_end + timedelta(minutes=5), "睡眠开始 = 活动做完 + [0,5] 分钟"
        if s_start > act_end:
            assert plan[-2]["kind"] == "idle" and plan[-2]["gap"] and plan[-2]["end"] == s_start, "II: 睡眠被延迟的这段算空闲"
        foc = [s for s in plan if s["kind"] == "focus"]
        assert len(foc) == 10, len(foc)
        # 循环总以休息结束, 休息的末尾 = 循环的末尾 + [0,5] 分钟, 下一项活动紧接着从那里开始
        cur_nom = wk
        for i, it in enumerate(r.items):
            nom_end = cur_nom + timedelta(minutes=it["dur"])
            if it["type"] == "loop":
                ls = [s for s in plan if s["loop"] == i]
                assert ls[-1]["kind"] == "rest" and ls[0]["kind"] == "focus", "循环以休息结束"
                dl = (ls[-1]["end"] - nom_end).total_seconds() / 60
                assert dl == rint(f"{seed}|le|{d}|{i}", 0, 5) and 0 <= dl <= 5, ("休息比循环的结束晚 [0,5] 分钟", dl)
                nom_end = ls[-1]["end"]
                nxt = next((s for s in plan if s["start"] == nom_end and s["loop"] is None), None)
                assert nxt is not None, "循环后面的活动从休息结束处开始"
            cur_nom = nom_end
        assert not any(s["gap"] for s in plan[:-2]), "循环里不留空档 (专注晚开始 → 前一段变长)"
        first = foc[0]
        nominal_first = wk + timedelta(minutes=30)
        assert -3 <= (first["start"] - nominal_first).total_seconds() / 60 <= 2, "专注开始波动 [-3,2]"
        saw_early |= first["start"] < nominal_first
    assert saw_early
    # 休息时长 = F(n): 100 分钟专注后休息 38 分钟
    d0 = _date(2026, 3, 1)
    p0 = day_plan(r, d0, seed, fluct=False)
    mins = lambda ss: [(s["kind"], int((s["end"] - s["start"]).total_seconds() // 60)) for s in ss]
    assert mins(p0[:9]) == [("idle", 30), ("focus", 100), ("rest", 38), ("focus", 100), ("rest", 38), ("focus", 19), ("rest", 5), ("idle", 30), ("idle", 30)][:9], "300min 循环: 最后一轮专注提前结束, 休息随它重新算 (F(19) = 5), 末尾对上循环的末尾"
    assert p0[6]["end"] == p0[0]["end"] + timedelta(minutes=300) and p0[7]["label"] == "午饭"
    assert mins([s for s in p0 if s["loop"] == 7]) == [("focus", 50), ("rest", 16), ("focus", 50), ("rest", 16), ("focus", 50), ("rest", 16), ("focus", 32), ("rest", 10)], "240min / 50min 循环: 最后一轮专注 32 + 休息 10"
    pp = lambda total, n: [(k, s / 60) for k, s in loop_pieces(total * 60, n * 60)]
    assert pp(276, 100) == [("focus", 100), ("rest", 38), ("focus", 100), ("rest", 38)], "正好排满"
    assert pp(200, 100) == [("focus", 100), ("rest", 38), ("focus", 47), ("rest", 15)], "最后一轮的专注提前结束, 休息跟着变短"
    assert pp(100, 100) == [("focus", 74), ("rest", 26)] and pp(20, 100) == [("focus", 16), ("rest", 4)], "放不下一整轮: 专注提前结束"
    assert pp(139, 100) == [("focus", 100), ("rest", 39)], "剩下的太短 (专注不到 1 分钟): 并进上一次休息"
    assert pp(1, 100) == [("focus", 1)], "一轮都排不了: 只剩专注"
    for tot in range(2, 700, 7):
        ps = pp(tot, 100)
        assert ps[-1][0] == "rest" and sum(m for _k, m in ps) == tot and all(m <= 100 for k, m in ps if k == "focus"), (tot, ps)
        for (k1, m1), (k2, m2) in zip(ps, ps[1:]):
            if k1 == "focus" and k2 == "rest" and m1 < 100 and m2 == ps[-1][1]:
                assert abs(m2 - config.compute_F(m1)) <= 1.01, (tot, ps)      # 提前结束的专注: 休息按它的长度算 (取整分钟, 差不到 1 分钟)
    assert [s["label"] for s in p0 if s["kind"] == "idle"] == ["洗漱,吃早饭", "午饭", "空闲", "晚饭", "空闲", "特殊空闲"]
    assert day_plan(r, d0, seed) == day_plan(r, d0, seed), "同一天总是同一个结果 (设备一致)"
    assert day_plan(r, d0, seed) != day_plan(r, d0 + timedelta(days=1), seed)
    # 相邻两天首尾相接
    pb = plan_between(r, datetime(2026, 3, 1, 12), datetime(2026, 3, 4, 12), seed)
    for a, b in zip(pb, pb[1:]):
        assert a["end"] == b["start"]
    # 联合行动之后重算: t 落在循环里 → 立即开始专注; 落在循环外不影响
    segs = plan_between(r, datetime(2026, 3, 2, 6), datetime(2026, 3, 2, 23), seed)
    rest_seg = next(s for s in segs if s["kind"] == "rest")
    t = rest_seg["start"] + timedelta(minutes=10)
    for s in segs:
        if s.get("loop") is not None:
            s["n"] = r.items[s["loop"]]["focus"]
    sh = shift_for_joint(segs, t, t + timedelta(minutes=1))
    cur = status_in(sh, t + timedelta(minutes=1))
    assert cur["kind"] == "focus" and cur["start"] == t, "休息结束后落在循环里 → 立即开始专注"
    meal = next(s for s in segs if s["kind"] == "idle" and s["label"] == "午饭")
    assert shift_for_joint(segs, meal["start"] + timedelta(minutes=5), meal["start"] + timedelta(minutes=6)) == segs, "落在吃饭里不影响"
    # 存档 + 数据
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "routine_data.json")
        st = Store(path, r, seed)
        now = datetime(2026, 3, 10, 10, 20)
        fn = lambda a, b: plan_between(r, a, b, seed)
        st.fill_timeline(now, fn)
        assert st.upto == now.replace(second=0) and st.tl and st.tl[0][1].startswith("2026-03-03")
        n_before = len(st.tl)
        assert abs(st.minutes(datetime(2026, 3, 3), now) - (now - datetime(2026, 3, 3)).total_seconds() / 60) < 1e-6, "时间线没有缺口"
        st.fill_cells(now, lambda a, b: 0)
        lines = st.log_lines(now)
        assert lines and all(not l.startswith("10:") for l in lines), "没结束的格子不给 (10:00 这一格要到 11:00)"
        assert any(" " in l for l in lines)
        # 联合行动 (override 带 since): since 之前没记到的按日程补, since 之后按联合行动里的状态记; 联合行动里的空闲记成"空闲"
        stj = Store(os.path.join(td, "routine_joint.json"), r, seed)
        stj.fill_timeline(now, fn)
        t1, t2 = now + timedelta(minutes=30), now + timedelta(minutes=45)
        stj.fill_timeline(t2, fn, ("focus", "联合行动", t1))
        assert stj.upto == t2 and abs(stj.minutes(now, t2) - 45) < 1e-6, "时间线没有缺口"
        assert abs(stj.minutes(t1, t2, ("focus",)) - 15) < 1e-6 and all(l == "联合行动" for k, a, b, l in stj.segments(t1, t2)), "since 之后记成联合行动"
        assert not any(l == "联合行动" for k, a, b, l in stj.segments(now, t1)), "since 之前不算联合行动"
        t3 = t2 + timedelta(minutes=10)
        stj.fill_timeline(t3, fn, ("idle", "空闲", t1))
        assert [(k, l) for k, a, b, l in stj.segments(t2, t3)] == [("idle", "空闲")], "联合行动里的空闲记成空闲"
        # 没被调用的时候: 往后补
        later = datetime(2026, 3, 13, 9, 40)
        st.fill_timeline(later, fn)
        st.fill_cells(later, lambda a, b: 0)
        assert abs(st.minutes(datetime(2026, 3, 3), later) - (later - datetime(2026, 3, 3)).total_seconds() / 60) < 1e-6
        assert f"2026-03-11T08:00" in st.cells and st.cells["2026-03-11T08:00"][1], "三天没调用: 自动按规则填充"
        # 睡着的格子 (睡眠占一半以上) 记成"睡眠"; 旧版本留下的空格子重新算
        assert st.cells["2026-03-11T03:00"][1] == "睡眠" and "03:00 睡眠" in st.log_lines(datetime(2026, 3, 11, 8, 0))
        for k in [k for k, v in st.cells.items() if v[1] == "睡眠"]:
            sl = st.minutes(datetime.fromisoformat(k), datetime.fromisoformat(k) + timedelta(minutes=st.cells[k][0]), ("sleep",))
            assert sl >= st.cells[k][0] / 2.0, ("不到一半的睡眠不记成睡眠", k, sl)
        assert not any(v[1] == "" for v in st.cells.values())
        _old = Store(os.path.join(td, "old.json"), r, seed)
        _old.tl, _old.upto, _old.cells = list(st.tl), st.upto, {k: [v[0], ""] if v[1] == "睡眠" else list(v) for k, v in st.cells.items()}
        _old.fill_cells(later, lambda a, b: 0)
        assert _old.cells == st.cells, "旧存档里的空格子补成睡眠"
        # 聊天概率: 用户一小时发 40 条 → 休息格里 "聊天" 的概率 50%; 0 条 → 不会出现聊天
        st2 = Store(os.path.join(td, "b.json"), r, "chat")
        st2.fill_timeline(datetime(2026, 3, 12, 0), fn)
        st2.fill_cells(datetime(2026, 3, 12, 0), lambda a, b: 0)
        assert all("聊天" != v[1] for v in st2.cells.values()), "0 条消息: 聊天概率 0"
        st3 = Store(os.path.join(td, "c.json"), r, "chat")
        st3.fill_timeline(datetime(2026, 3, 12, 0), fn)
        st3.fill_cells(datetime(2026, 3, 12, 0), lambda a, b: 40)
        chats = sum(1 for v in st3.cells.values() if v[1] == "聊天")
        rests = sum(1 for k, v in st3.cells.items() if v[1] in ("聊天", "弹琴", "阅读", "打扫", "散步"))
        assert rests and chats / rests > 0.25, (chats, rests)
        # 存盘 + 读回
        open(path, "w", encoding="utf-8").write(json.dumps(st.payload(), ensure_ascii=False))
        st4 = Store(path, r, seed)
        assert st4.upto == st.upto and st4.tl == st.tl and st4.cells == st.cells
        assert Store(path, r, "other").tl == [], "种子不同的存档不读"
        # 联网 / 窗口: 只算已经过去的时间
        d = later.date()
        net = st.net_by_reason(d)
        assert set(net) == {"查资料", "下载资源", "娱乐"} and net["查资料"] > net["娱乐"] > 0
        assert st.net_by_reason(d + timedelta(days=1)) == {}, "明天还没发生: 没有数据"
        w0 = st.window_seconds(d, 0)
        assert w0 and "" in w0 and abs(sum(w0.values()) - st.day_minutes(d, ("focus",)) * 60) < 1e-6
        # 合约记录: 难度 20~30, 不碰不选择 / 违规的词条
        import contract as C
        banned = banned_ids(r)
        assert {"long1", "long2", "long3", "silent", "chan1", "chan2", "fine", "music", "unknown"} <= banned and "tired1" not in banned
        recs = contracts_before(r, datetime(2026, 4, 1), seed, days=60)
        assert recs and all(20 <= x["level"] <= 30 and not (set(x["terms"]) & banned) for x in recs), [x["level"] for x in recs]
        assert all(_dt(x["end"]) <= datetime(2026, 4, 1) for x in recs)
    print("routine selftest OK")


if __name__ == "__main__":
    _selftest()
