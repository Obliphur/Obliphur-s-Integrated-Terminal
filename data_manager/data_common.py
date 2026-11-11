"""数据层的公共部分: 月份前缀、活动拆分、违规 / 用户名的上限, 以及统计用的纯工具。

从 data_manager.py 拆出来的公共部分; 依赖方向: data_manager.py 和 data_manager 下的 mixin 都导入它,
它从不导入 data_manager.py.
"""

from datetime import datetime, timedelta
from typing import Dict
import config
import i18n

def month_prefixes(now: datetime = None):
    """(本月, 上月) 的 'YYYY-MM' 前缀;数据只保留这两个月."""
    now = now or datetime.now()
    this = f"{now.year}-{now.month:02d}"
    if now.month == 1:
        last = f"{now.year - 1}-12"
    else:
        last = f"{now.year}-{now.month - 1:02d}"
    return this, last


def split_items(text: str):
    """标准语法: 一个小时 / 一次申请的多项活动用 / 连接, 如 '学习/吃饭'."""
    parts = str(text or "").replace("／", "/").split("/")
    return [p.strip() for p in parts if p.strip()]


VIOLATION_KEEP = 15          # 违规记录只留最近这么多条 (MECHANICS 8.1)
USERNAME_MAX = 24


def half_fill() -> timedelta:
    """细化规范的半小时格: 补填时限只有 30 min (补填时限设得更短时照设置)."""
    return min(config.FILL_WINDOW, config.FINE_FILL_WINDOW)


def _sum_days(days: dict, prefix: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for ds, day in (days or {}).items():
        if str(ds).startswith(prefix) and isinstance(day, dict):
            for name, sec in day.items():
                try:
                    out[name] = out.get(name, 0.0) + float(sec)
                except (TypeError, ValueError):
                    pass
    return out


# 断网期间重叠的记录按这个优先级叠加 (MECHANICS 15.5): 休息 > 专注 > 空闲
_RANK = {"break": 3, "study": 2, "idle": 1}


def _merge_spans(spans) -> list:
    """把可能重叠的 (开始, 结束) 合成互不重叠的几段."""
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _subtract(st: datetime, en: datetime, holes) -> list:
    """[st, en) 去掉 holes 里的各段之后剩下的 [(开始, 结束)]."""
    pieces = [(st, en)]
    for hs, he in holes:
        nxt = []
        for a, b in pieces:
            if he <= a or hs >= b:
                nxt.append((a, b))
                continue
            if hs > a:
                nxt.append((a, hs))
            if he < b:
                nxt.append((he, b))
        pieces = nxt
    return pieces


def combine_window(sources, prefix: str) -> Dict[str, float]:
    """几台设备的窗口 / 应用时间合在一起. sources 按优先级排 (母端在前), 每个是
    {"seconds": {日期: {程序: 秒}}, "runs": {日期: [[开始分钟, 结束分钟, 程序], …]}}.
    有时间线的日子按分钟合并: 同一分钟以排在前面的设备为准, 只有它是空白 (或没有记录) 时才看后面的;
    没有时间线的日子 (旧数据) 直接把秒数加起来."""
    dates = set()
    for src in sources:
        for key in ("seconds", "runs"):
            dates |= {str(d) for d in (src.get(key) or {}) if str(d).startswith(prefix)}
    out: Dict[str, float] = {}
    for d in dates:
        minutes = [None] * 1440
        for src in sources:
            runs = (src.get("runs") or {}).get(d)
            if runs:
                for r in runs:
                    try:
                        s, e, app = int(r[0]), int(r[1]), str(r[2])
                    except (TypeError, ValueError, IndexError):
                        continue
                    for m in range(max(0, s), min(1440, e)):
                        cur = minutes[m]
                        if cur is None or (cur == "" and app != ""):
                            minutes[m] = app
            else:
                for app, sec in ((src.get("seconds") or {}).get(d) or {}).items():
                    try:
                        out[app] = out.get(app, 0.0) + float(sec)
                    except (TypeError, ValueError):
                        pass
        for app in minutes:
            if app is not None:
                out[app] = out.get(app, 0.0) + 60.0
    return out


def tally_items(pairs) -> Dict[str, float]:
    """[(文本, 秒)] → {活动: 秒};按 / 拆分均分,同名(忽略大小写)合并."""
    totals: Dict[str, float] = {}
    names: Dict[str, str] = {}
    for text, secs in pairs:
        items = split_items(i18n.auto_names_in(text))      # 睡眠 / Sleeping 这类默认内容合并成一项
        if not items or secs <= 0:
            continue
        share = float(secs) / len(items)
        for item in items:
            key = item.casefold()
            names.setdefault(key, item)
            totals[key] = totals.get(key, 0.0) + share
    return {names[k]: v for k, v in totals.items()}
