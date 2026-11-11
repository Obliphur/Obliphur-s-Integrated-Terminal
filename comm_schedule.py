# -*- coding: utf-8 -*-
"""人设日程文本的解析: 段名 / 循环 / 发起谈话概率 (从 comm.py 拆出; 纯函数, 不依赖对象状态)."""

import re

import config


PROACTIVE_P = {"idle": 80, "rest": 60, "long": 70, "sleep": 60}   # I~IV 发不发的概率 (%) 的默认值: 人设里没写对应 "xxx发起谈话概率:n%" 时用


# 人设日程里的一行: "12:30-13:00:空闲" (可以缩进 / 带 - 或 * 开头); 时间可以是全角冒号, 连接符 - – — ~ 至 到
_SEG_RE = re.compile(r"^[ \t]*(?:[-*•][ \t]*)?(\d{1,2})[:：](\d{2})[ \t]*[-–—~～至到][ \t]*(\d{1,2})[:：](\d{2})"
                     r"[ \t]*[:：][ \t]*(\S[^\n]*)$", re.M)
# 循环段: "[循环 7:00-12:00][100min]" / "[Loop 7:00-12:00][100min]" ([Nmin] 可省, 默认 100min); 英文不分大小写
_CYCLE_RE = re.compile(r"^[ \t]*(?:[-*•][ \t]*)?[\[［][ \t]*(?:循环|loop|cycle|repeat)[ \t]*"
                       r"(\d{1,2})[:：](\d{2})[ \t]*[-–—~～至到][ \t]*(\d{1,2})[:：](\d{2})[ \t]*[\]］]"
                       r"[ \t]*(?:[\[［][ \t]*(\d+)[ \t]*(?:min|mins|分钟)?[ \t]*[\]］])?", re.I | re.M)


def _seg_kind_in(s: str) -> str:
    s = re.sub(r"[不无没非](?:休息|空闲|专注|睡眠|rest|idle|free|focus|sleep)", "", str(s or "").lower())   # "不休息" 之类不算
    if "空闲" in s or "idle" in s or "free" in s:
        return "idle"
    if "休息" in s or "rest" in s or "break" in s:
        return "rest"
    if "专注" in s or "focus" in s:
        return "focus"
    if "睡" in s or "sleep" in s:
        return "sleep"
    return ""


_SEG_NEG_RE = re.compile(r"[不无没非][^\s,，、;；]{0,2}(?:休息|空闲|专注|睡眠|rest|idle|free|focus|sleep)")   # "不休息" 之类


def seg_kind(label: str) -> str:
    """日程段的名字 → "idle" (空闲) / "rest" (休息) / "focus" (专注) / "sleep" (睡眠) / "" (不算任何一段).

    中英文的**标准用语**都认, 不分大小写: 空闲 / idle / free, 休息 / rest / break, 专注 / focus, 睡眠 / sleep.
    标准用语后面可以加修饰 (例如 "专注(学习)", "Focus(study)"), 修饰只是说明具体活动, 不影响这是哪一段:
    先看括号前面的标准用语, 这样 "专注(休息)" 仍然是专注段.

    别的名字 (午饭 / 晚饭 / 上课 …) 一律当**空闲**段: 也按空闲安排主动发消息, 状态也显示空闲.
    空名字 / "不休息" "不专注" 这类否定写法不算 ("").
    """
    s = str(label or "").strip().lower()
    if not s:
        return ""
    head = re.split(r"[(（]", s, maxsplit=1)[0].strip()
    k = _seg_kind_in(head) or _seg_kind_in(s)
    if k:
        return k
    return "" if _SEG_NEG_RE.search(s) else "idle"


def seg_detail(label: str) -> str:
    """日程段名字后面括号里的修饰 (具体的活动): "专注(学习)" → "学习"; 没有 = ""."""
    m = re.search(r"[(（]([^()（）]*)[)）]", str(label or ""))
    return m.group(1).strip() if m else ""


def schedule_entries(text: str) -> list:
    """人设里每天重复的日程: 所有带时间的段 → [条目], 没有重复. 只认 "H:MM-H:MM:名字" 这样的行.

    条目是 {"kind", "start", "end", "label", "detail"}: kind 是空闲 / 休息 / 专注 / 睡眠之一
    (别的名字都算空闲; 空名字 / "不休息" 这类不算, 是 "");
    时刻都是一天中的第几分钟, 跨零点的段 end > 1440.

    专注 / 休息**只能**用循环段写 (1.0.308 起): "[循环 HH:MM-HH:MM][Nmin]" ([Nmin] 可省, 默认 100min; 英文 Loop / Cycle / Repeat 都认):
    按 N 分钟专注 + F(N) 分钟休息交替生成, 生成到这段结束为止. 显式写出的 "H:MM-H:MM:专注 / 休息" 不再被认出来 (忽略).
    每个条目带 "loop": 它是第几个循环段生成的 (显式写的段没有这个键).
    """
    src = str(text or "")
    out, seen = [], set()
    for m in _SEG_RE.finditer(src):
        h, mi, eh, em = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        label = m.group(5).strip()
        kind = seg_kind(label)
        if kind in ("focus", "rest"):                          # 专注 / 休息只能写成循环段: 逐段写的不认
            continue
        if h > 23 or mi > 59 or eh > 24 or em > 59 or (eh == 24 and em):
            continue
        start, end = h * 60 + mi, eh * 60 + em
        if end <= start:                                       # 跨零点 (23:30-6:30)
            end += 24 * 60
        if (kind, start) in seen:
            continue
        seen.add((kind, start))
        out.append({"kind": kind, "start": start, "end": end, "label": label, "detail": seg_detail(label)})
    for idx, (start, end, n) in enumerate(schedule_loops(src)):      # 循环段: 总是自动生成专注 / 休息
        for e in loop_entries(start, end, n, start):
            e["loop"] = idx
            out.append(e)
    out.sort(key=lambda e: e["start"])                         # 按时间排: 显式段和自动生成的循环段混在一起
    return out


def schedule_loops(text: str) -> list:
    """人设里的循环段: [(开始, 结束, N)] (时刻是一天中的第几分钟, 跨零点的 结束 > 1440; N = 每次专注的分钟数)."""
    out = []
    for m in _CYCLE_RE.finditer(str(text or "")):
        h, mi, eh, em = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        if h > 23 or mi > 59 or eh > 24 or em > 59 or (eh == 24 and em):
            continue
        start, raw_end = h * 60 + mi, eh * 60 + em
        if raw_end == start:
            continue
        end = raw_end if raw_end > start else raw_end + 24 * 60
        n = int(m.group(5) or 100)
        item = (start, end, n if n > 0 else 100)
        if item not in out:
            out.append(item)
    return out


def loop_entries(start: int, end: int, n: int, anchor: int) -> list:
    """循环段 [start, end) 里从 anchor 时刻起: N 分钟专注 + F(N) 分钟休息交替, 到 end 截断 (anchor = start 就是原来的循环;
    联合行动之后休息结束的时刻在循环里, 就从那一刻 "立即开始专注")."""
    rest = max(1, int(round(config.compute_F(n))))
    out = []
    t = anchor
    while t < end:
        f_end = min(t + n, end)
        if f_end > t:
            out.append({"kind": "focus", "start": t, "end": f_end, "label": "专注", "detail": ""})
        t = f_end
        if t >= end:
            break
        r_end = min(t + rest, end)
        if r_end > t:
            out.append({"kind": "rest", "start": t, "end": r_end, "label": "休息", "detail": ""})
        t = r_end
    return out


def schedule_segments(text: str) -> list:
    """人设里每天重复的日程: 所有空闲 / 休息 / 专注 / 睡眠段 → [(kind, 开始时刻 (一天中的第几分钟))], 没有重复."""
    return [(e["kind"], e["start"]) for e in schedule_entries(text) if e["kind"]]


_PRO_KINDS = {"长时间休息": "long", "特殊空闲": "special", "重要活动": "key", "空闲": "idle", "休息": "rest", "睡眠": "sleep"}
_PRO_RE = re.compile(r"(长时间休息|特殊空闲|重要活动|空闲|休息|睡眠)发起谈话概率[ \t]*[:：][ \t]*(\d+(?:\.\d+)?)[ \t]*([%％])?")


def pro_pct(probs: dict, kind: str) -> float:
    """某一种主动发起谈话的概率 (%): 人设里写了就用; 没写: 重要活动 / 特殊空闲 按普通空闲的, 别的用默认."""
    v = probs.get(kind)
    if v is None and kind in ("key", "special"):
        v = probs.get("idle")
    if v is None:
        v = float(PROACTIVE_P.get(kind, PROACTIVE_P["idle"]))
    return float(v)


def persona_probs(text: str) -> dict:
    """人设里写的主动发起谈话的概率: "空闲发起谈话概率:50%" / "休息发起谈话概率:0.4" → {"idle": 50.0, "rest": 40.0} (%, 没写的不在里面; "长时间休息发起谈话概率:n%" 是 "long").
    带 % 的按百分数; 不带 % 的小数 (0~1) 按小数, 大于 1 的当百分数; 超出 0~100 的截到范围内; 同一种写了几行只认第一行."""
    out = {}
    for m in _PRO_RE.finditer(str(text or "")):
        kind = _PRO_KINDS[m.group(1)]
        if kind in out:
            continue
        v = float(m.group(2))
        out[kind] = min(100.0, max(0.0, v if (m.group(3) or v > 1) else v * 100))
    return out
