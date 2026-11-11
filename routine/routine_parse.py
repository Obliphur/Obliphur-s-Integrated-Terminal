"""日程文件解析: 初始起点 / 活动持续时间 / 循环 / 波动 / 列表 → Routine。

从 routine.py 拆出来的部分; 依赖方向: routine.py 和后面的 parts 导入它, 它从不导入 routine.py.
"""

import re
from datetime import date as _date

from routine.routine_common import (
    NAMES_NO_PROB,
    _DUR_RE,
    _LOOP_RE,
    _START_RE,
    _FOCUS_RE,
    _RANGE,
    _FLUCT_KEYS,
    _LIST_RE,
    _DEV_WIN_RE,
    _DIFF_RE,
    _minutes,
    parse_list,
)

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

