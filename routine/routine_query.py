"""按天查询: 对方完成的合约、被禁止的词条、过去的合约记录。

从 routine.py 拆出来的部分; 依赖方向: routine.py 和后面的 parts 导入它, 它从不导入 routine.py.
"""

import re
from datetime import datetime, timedelta, date as _date

from routine.routine_common import (
    CONTRACT_DAY_P,
    u01,
    rint,
    _iso,
    _dt,
)
from routine.routine_parse import (
    Routine,
)

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
