# -*- coding: utf-8 -*-
"""检查拟真增强三项：角色慢变量生活线、延迟回调与跨周趋势、工程层防复读，以及双端模板同步。"""

from datetime import date, datetime, timedelta
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comm import Comm
from comm.comm_builtin import BUILTIN_PERSONAS, DEFAULT_PERSONA, DEFAULT_ROUTINE
from comm.comm_realism import (
    anti_repeat_note,
    callback_note,
    life_arcs_note,
    realism_note,
    weekly_trend_note,
)
from comm.comm_rules import (
    anti_repeat_rules,
    callback_rules,
    life_arcs_rules,
    trend_rules,
)


def main():
    # 1. 内置 Claudia 模板解析与源文件同步
    tmpl = BUILTIN_PERSONAS[DEFAULT_ROUTINE]
    claudia = BUILTIN_PERSONAS[DEFAULT_PERSONA]
    assert tmpl.strip() == (ROOT / "personas" / "Claudia_templates.txt").read_text(encoding="utf-8").strip()
    assert claudia.strip() == (ROOT / "personas" / "Claudia.txt").read_text(encoding="utf-8").strip()

    arcs = life_arcs_rules(tmpl)
    assert len(arcs) == 4 and "piano_piece" in arcs and "reading_book" in arcs
    assert trend_rules(tmpl) == {"window": 7, "min_days": 3, "delta_pct": 15}
    assert callback_rules(tmpl) == {"lookahead_days": 0, "expire_days": 2, "max_due": 2}
    ar = anti_repeat_rules(tmpl)
    assert ar == {"window": 16, "min_hits": 3, "max_items": 6, "forbid": ("接住", "记下")}

    # 非法格式整组拒绝
    assert life_arcs_rules("[life:arcs]\nbad:[9,5] a | b\n[/life:arcs]") == {}
    assert life_arcs_rules("[life:arcs]\nbad:[3,5] only_one\n[/life:arcs]") == {}
    assert trend_rules("[trend]\nwindow:99\n[/trend]") is None
    assert callback_rules("[callback]\nexpire_days:0\n[/callback]") is None
    assert anti_repeat_rules("[anti_repeat]\nwindow:2\n[/anti_repeat]") is None
    assert life_arcs_rules("") == {} and trend_rules("") is None and callback_rules("") is None and anti_repeat_rules("") is None

    # 2. 角色慢变量生活线：确定性 & 跨天连续推进
    d0 = date(2026, 11, 24)
    n0 = life_arcs_note(tmpl, "Claudia.txt", d0)
    assert n0 == life_arcs_note(tmpl, "claudia.txt", d0), "同日同角色输出必须完全确定"
    assert "[OIT_LIFE_ARC]" in n0 and "★今日焦点 " in n0
    seen_stages = {life_arcs_note(tmpl, "Claudia.txt", d0 + timedelta(days=i)) for i in range(12)}
    assert len(seen_stages) >= 4, "跨天应持续演进阶段"
    assert life_arcs_note("无生活线模板", "Assistant.txt", d0) == ""

    # 3. 跨周趋势洞察：share_enabled 门控 + 升降判断 + 连续趋势
    cond_days = {}
    # 前7天（day 8..14 ago）：每天 3h 专注, 2h 空闲
    for i in range(8, 15):
        cond_days[(d0 - timedelta(days=i)).isoformat()] = [3 * 3600, 2 * 3600]
    # 近7天（day 1..7 ago）：每天逐步走高，平均约 5h 专注, 1h 空闲
    for idx, i in enumerate(range(7, 0, -1)):
        cond_days[(d0 - timedelta(days=i)).isoformat()] = [(4 + idx * 0.3) * 3600, 3600]
    cond_days[d0.isoformat()] = [int(2.5 * 3600), 1800]

    assert weekly_trend_note(tmpl, cond_days, d0, share_enabled=False) == "", "未开启 share_data 不得注入趋势"
    tr_note = weekly_trend_note(tmpl, cond_days, d0, share_enabled=True)
    assert "[OIT_WEEKLY_TREND:" in tr_note and "上升" in tr_note and "近3日专注连续走高" in tr_note and "今日已专注 2.5h" in tr_note, tr_note

    # 记录天数不足 min_days 时不输出
    assert weekly_trend_note(tmpl, {d0.isoformat(): [7200, 0]}, d0, share_enabled=True) == ""

    # 4. 延迟回调：到期、昨天到期、未来未到期、已过期清理
    mem_live = "\n".join([
        "- 说教目标：改掉熬夜刷手机",
        "- [回访:2026-11-24] 论文预答辩",
        "- [回访:2026-11-23] 英语六级模拟考",
        "- [回访:2026-11-28] 下周钢琴汇报演出",
        "- [回访:2026-11-18] 上周旧事项",
    ])
    cb_note = callback_note(tmpl, mem_live, d0)
    assert "[OIT_CALLBACK_DUE]" in cb_note
    assert "今天到期: 论文预答辩" in cb_note
    assert "昨天到期(可顺口追问结果): 英语六级模拟考" in cb_note
    assert "钢琴汇报演出" not in cb_note, "未到 lookahead 不应提前提醒"
    assert "已过期可清理" in cb_note and "上周旧事项" in cb_note

    # 5. 工程层防复读：不误报 2 字正常话题词与「稳定」，只抓高频 3-4 字套话 / 4+次开头 / 禁用词
    clean_msgs = [
        {"role": "ai", "text": "先去洗把脸，保持稳定再继续专注。"},
        {"role": "ai", "text": "这段钢琴谱子的左手伴奏太赶了，专注一点。"},
        {"role": "ai", "text": "钢琴练完记得休息，今天专注还算平稳。"},
        {"role": "ai", "text": "钢琴音色比昨天好，专注时长也够了。"},
    ]
    assert anti_repeat_note(tmpl, clean_msgs) == "", "正常2字话题词与「稳定」不应误报"

    rep_msgs = [
        {"role": "ai", "text": "既然如此，先把桌上的书理干净，端正态度。"},
        {"role": "ai", "text": "既然答应了，就按计划走，端正态度。"},
        {"role": "ai", "text": "既然已经开始，别东张西望，端正态度，我能接住你的节奏。"},
        {"role": "ai", "text": "既然没睡好，今晚早点收尾。"},
    ]
    ar_note = anti_repeat_note(tmpl, rep_msgs)
    assert "[OIT_ANTI_REPEAT:" in ar_note, ar_note
    assert "开头「既然」×4" in ar_note, ar_note
    assert "端正态度" in ar_note, ar_note
    assert "禁用词(接住×1" in ar_note, ar_note

    # 6. Comm 实例端到端整合检查（使用临时目录，不碰真实数据）
    with tempfile.TemporaryDirectory() as tmp:
        c = Comm(tmp, str(Path(tmp) / "personas"))
        c.now = lambda: datetime(2026, 11, 24, 21, 0, 0)
        c.user_day_fn = lambda _d: (0, 0)
        c.cfg["mem_live"] = mem_live
        c.cfg["cond_days"] = cond_days
        c.messages = list(rep_msgs)
        note_no_share = c.realism_note()
        assert "[OIT_LIFE_ARC]" in note_no_share
        assert "[OIT_CALLBACK_DUE]" in note_no_share
        assert "[OIT_ANTI_REPEAT:" in note_no_share
        assert "[OIT_WEEKLY_TREND:" not in note_no_share

        c.share["on"] = True
        note_with_share = c.realism_note()
        assert "[OIT_WEEKLY_TREND:" in note_with_share

    # 7. 双端同步核验：Android Kotlin 文件包含对应解析与实现
    kt_rules = (ROOT / "Android version/app/src/main/java/com/obliphur/oit/core/CommRules.kt").read_text(encoding="utf-8")
    kt_realism = (ROOT / "Android version/app/src/main/java/com/obliphur/oit/core/CommRealism.kt").read_text(encoding="utf-8")
    kt_comm = (ROOT / "Android version/app/src/main/java/com/obliphur/oit/core/Comm.kt").read_text(encoding="utf-8")
    for token in ("fun lifeArcs(", "fun trend(", "fun callback(", "fun antiRepeat("):
        assert token in kt_rules, f"CommRules.kt 缺少 {token}"
    for token in ("object CommRealism", "[OIT_LIFE_ARC]", "[OIT_WEEKLY_TREND:", "[OIT_CALLBACK_DUE]", "[OIT_ANTI_REPEAT:"):
        assert token in kt_realism, f"CommRealism.kt 缺少 {token}"
    assert "CommRealism.realismNote(" in kt_comm

    print("comm_realism_check OK")


if __name__ == "__main__":
    main()
