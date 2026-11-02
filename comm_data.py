# -*- coding: utf-8 -*-
"""沟通页里"📎 数据"能发出去的内容: 统计的几页, 日历 (选类别), 合约的词条. 手机版: core/Core.kt 的 commData.

    python comm_data.py     自检 (用假数据)
"""

from datetime import datetime, timedelta

import contract as C

STATS_KEYS = (("log", "整点记录"), ("net", "联网理由"), ("focus", "专注计时"), ("window", "窗口"), ("contracts", "合约记录"))
CAL_KEYS = (("study", "专注"), ("break", "休息"), ("idle", "空闲"), ("sleep", "睡眠"))
WEEKDAY = "一二三四五六日"
TOP = 12
MAX_CHARS = 6000


def label(sel: dict) -> str:
    """聊天里显示的那一行."""
    parts = []
    names = dict(STATS_KEYS)
    st = [names[k] for k in sel.get("stats", []) if k in names]
    if st:
        parts.append("统计·" + "、".join(st))
    cal = dict(CAL_KEYS)
    cl = [cal[k] for k in sel.get("cal", []) if k in cal]
    if cl:
        parts.append("日历·" + "、".join(cl))
    if sel.get("contract"):
        parts.append("合约词条")
    return "📎 " + " / ".join(parts) if parts else ""


def _top(d: dict, unit: str = "h", n: int = TOP) -> str:
    rows = sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))
    total = sum(d.values())
    out = []
    for k, v in rows[:n]:
        out.append(f"  {k or '空白'}: {v / 3600:.1f} h" if unit == "h" else f"  {k or '空白'}: {v / 60:.0f} 分钟")
    if len(rows) > n:
        out.append(f"  其他 {len(rows) - n} 项: {sum(v for _k, v in rows[n:]) / 3600:.1f} h")
    out.append(f"  合计: {total / 3600:.1f} h")
    return "\n".join(out)


def term_text(tid: str) -> str:
    t = C.BY_ID.get(tid)
    return f"{t[3].split(':', 1)[-1]} (等级{t[2]}): {t[4]}" if t else tid


def _record(rec: dict) -> str:
    rows = C.record_rows(rec)
    head = f"危机等级 {C.level_label(int(rec.get('level') or 0), int(rec.get('orig_level') or rec.get('level') or 0))}"
    body = "; ".join(C.BY_ID[tid][3].split(":", 1)[-1] + ("(已失效)" if void else "") for _lv, tid, void, _o in rows)
    return f"  {head}: {body}"


def build(sel: dict, dm, net, contract, now: datetime = None, focus_live=None, cal_live=None,
          month_prefix: str = None, window_names: dict = None) -> str:
    now = now or datetime.now()
    prefix = month_prefix or now.strftime("%Y-%m")
    blocks = []
    stats = list(sel.get("stats", []))
    if "log" in stats:
        blocks.append(f"【统计·整点记录 {prefix}】\n" + _top(dm.hour_activity_seconds(prefix)))
    if "net" in stats:
        from data_manager import tally_items
        blocks.append(f"【统计·联网理由 {prefix}】\n" + _top(tally_items(net.usage_by_reason(prefix)), unit="m"))
    if "window" in stats:
        names = {"": "空白", "?": "未知程序", **(window_names or {})}
        out = {}
        for k, v in dm.window_seconds(prefix, "all").items():
            out[names.get(k, k)] = out.get(names.get(k, k), 0.0) + v
        blocks.append(f"【统计·窗口 {prefix}】\n" + _top(out))
    if "focus" in stats:
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        lines = []
        for i in range(7):
            day = today - timedelta(days=i)
            hours = dm.focus_hours(day, focus_live)
            st = sum(h[0] for h in hours) / 60
            br = sum(h[1] for h in hours) / 60
            lines.append(f"  {day:%m-%d} 周{WEEKDAY[day.weekday()]}: 专注 {st:.0f} 分钟, 休息 {br:.0f} 分钟")
        blocks.append("【统计·专注计时 最近 7 天】\n" + "\n".join(lines))
    if "contracts" in stats:
        recs = C.recent_completed(contract.history, 5)
        blocks.append("【统计·合约记录 最近完成的】\n" + ("\n".join(_record(r) for r in recs) if recs else "  还没有顺利完成的合约"))
    for kind in sel.get("cal", []):
        name = dict(CAL_KEYS).get(kind)
        if not name:
            continue
        lines = []
        if kind == "study":
            days = {d: h for d, h in dm.study_data.items() if d.startswith(prefix)}
        else:
            floor = dm._focus_floor(now).date()
            days = {}
            for n in range(1, 32):
                try:
                    d = datetime.strptime(f"{prefix}-{n:02d}", "%Y-%m-%d")
                except ValueError:
                    break
                if floor <= d.date() <= now.date():
                    days[d.strftime("%Y-%m-%d")] = dm.day_kinds(d, cal_live if d.date() == now.date() else None, now)[kind] / 3600.0
        for d in sorted(days):
            if days[d] > 0:
                lines.append(f"  {d}: {days[d]:.1f} h")
        tail = "" if kind == "study" else " (只有最近 7 天有记录)"
        blocks.append(f"【日历·{name} {prefix}{tail}】\n" + ("\n".join(lines) if lines else "  没有记录")
                      + f"\n  合计: {sum(days.values()):.1f} h")
    if sel.get("contract"):
        if contract.active:
            body = "\n".join("  " + term_text(t) for t in contract.terms) or "  (没有词条)"
            blocks.append(f"【合约词条·进行中 危机等级 {getattr(contract, 'level_text', '')}】\n" + body)
        elif contract.draft:
            blocks.append("【合约词条·准备中(还没开始)】\n" + "\n".join("  " + term_text(t) for t in contract.draft))
        else:
            blocks.append("【合约词条】\n  现在没有进行中的合约, 也没有选好的词条")
    text = "\n\n".join(blocks)
    return text[:MAX_CHARS]


def _selftest():
    class DM:
        study_data = {"2026-11-01": 3.5, "2026-11-02": 1.0, "2026-10-30": 9.0}

        def hour_activity_seconds(self, p):
            return {"写代码": 7200.0, "睡眠": 3600.0}

        def window_seconds(self, p, dev):
            return {"": 600.0, "code.exe": 1800.0}

        def focus_hours(self, day, live):
            return [(1800.0, 600.0)] + [(0.0, 0.0)] * 23

        def _focus_floor(self, now):
            return now - timedelta(days=6)

        def day_kinds(self, d, live, now):
            return {"break": 3600.0, "idle": 0.0, "sleep": 7200.0, "study": 0.0}

    class Net:
        def usage_by_reason(self, p):
            return [("学习资料", 600.0), ("学习资料", 300.0)]

    class Con:
        active = False
        draft = ["long1"]
        history = []
        terms = []

    class Act(Con):
        active = True
        terms = ["long1"]
        level_text = "1"

    now = datetime(2026, 11, 2, 12, 0)
    assert "进行中 危机等级 1" in build({"contract": True}, DM(), Net(), Act(), now=now)
    sel = {"stats": ["log", "net", "focus", "window", "contracts"], "cal": ["study", "sleep"], "contract": True}
    text = build(sel, DM(), Net(), Con(), now=now)
    for k in ("整点记录 2026-11", "写代码: 2.0 h", "学习资料: 15 分钟", "专注 30 分钟, 休息 10 分钟", "空白: 0.2 h", "code.exe",
              "还没有顺利完成的合约", "日历·专注", "2026-11-01: 3.5 h", "合计: 4.5 h", "日历·睡眠", "只有最近 7 天", "准备中", "持久战-I"):
        assert k in text, k
    assert "2026-10-30" not in text
    assert label(sel) == "📎 统计·整点记录、联网理由、专注计时、窗口、合约记录 / 日历·专注、睡眠 / 合约词条"
    assert label({"stats": [], "cal": [], "contract": False}) == ""
    return "comm_data selftest OK"


if __name__ == "__main__":
    print(_selftest())
