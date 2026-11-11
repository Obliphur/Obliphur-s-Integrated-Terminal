"""日程推算: 起床、一天的安排、循环与波动、联合行动的平移、当前状态 (MECHANICS 22 的对方日程)。

从 routine.py 拆出来的部分; 依赖方向: routine.py 和后面的 parts 导入它, 它从不导入 routine.py.
"""

import math
import re
from datetime import datetime, timedelta, date as _date
import config

from routine.routine_common import (
    CHAT_FULL,
    CHAT_FULL_MSGS,
    BASE_FLUCT,
    u01,
    rint,
    resolve_list,
    plain_label,
)
from routine.routine_parse import (
    Routine,
)

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
    return "idle" if plain_label(s.get("label")) else "key"


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

