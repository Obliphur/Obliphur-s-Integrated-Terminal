# -*- coding: utf-8 -*-
"""拟真增强（声明式模板驱动）：角色慢变量生活线、延迟回调与跨周趋势、工程层防复读。

纯数据计算，不依赖 UI 或网络；双端（Windows Python / Android Kotlin）使用完全一致的算法与 CRC32 种子。
"""

from collections import Counter
from datetime import date, timedelta
import re
import zlib

from comm.comm_rules import (
    anti_repeat_rules,
    callback_rules,
    life_arcs_specs,
    persona_clock_rules,
    persona_milestones_rules,
    trend_rules,
)

_EPOCH_2024 = date(2024, 1, 1).toordinal()
_CALLBACK_RE = re.compile(r"^\s*(?:-\s*)?(?:\[#[A-Za-z][A-Za-z0-9_-]{0,11}\]\s*)?\[(?:回访\s*[:：]|@)\s*(\d{4}-\d{2}-\d{2})\s*\]\s*(.+?)\s*$")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_STRIP_EDGE_RE = re.compile(r"^[\s，。！？、；：“”‘’\"'()（）\[\]【】《》—…·.,!?;:~`]+|[\s，。！？、；：“”‘’\"'()（）\[\]【】《》—…·.,!?;:~`]+$")
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")

# 常见中文功能词 / 高频连接词：避免被误报为“口癖复读”
_STOP_PHRASES = frozenset({
    "你的", "我的", "我的", "你是", "我是", "不是", "没有", "可以", "什么", "这个", "那个", "自己",
    "现在", "如果", "还是", "已经", "不要", "应该", "只是", "一个", "这么", "那么", "这样", "那样",
    "因为", "所以", "但是", "不过", "而且", "或者", "虽然", "今天", "明天", "昨天", "时候", "事情",
    "一点", "一些", "一下", "这些", "那些", "他们", "我们", "你们", "这里", "那里", "如何", "怎么",
    "为什么", "没什么", "不需要", "不用", "不会", "不能", "不想", "觉得", "知道", "明白", "确实",
    "其实", "比较", "有点", "可能", "大概", "继续", "开始", "结束", "时间", "问题", "办法", "状态",
    "的话", "就好", "就行", "而已", "罢了", "之前", "之后", "刚才", "目前", "暂时", "自然",
})


def _crc32(seed: str) -> int:
    return zlib.crc32(seed.encode("utf-8")) & 0xFFFFFFFF


def _safe_birthday_in_year(birth: date, year: int) -> date:
    """闰日 2/29 在平年按 2/28 计。"""
    try:
        return date(year, birth.month, birth.day)
    except ValueError:
        return date(year, 2, 28)


def _milestone_state(milestones: list, today: date):
    """返回 (当前生效里程碑或 None, 已到达的里程碑 ID 集合, 下一个未来里程碑或 None)。"""
    reached = set()
    active = None
    upcoming = None
    today_iso = today.isoformat()
    for item in milestones:
        if item["date"] <= today_iso:
            reached.add(item["id"])
            active = item
        elif upcoming is None:
            upcoming = item
    return active, reached, upcoming


def _arc_allowed(spec: dict, active_id: str, reached: set) -> bool:
    mode = spec.get("when_mode") or ""
    if not mode:
        return True
    targets = spec.get("when_stages") or ()
    if mode == "before":
        return targets[0] not in reached
    if mode == "since":
        return targets[0] in reached
    if mode == "stage":
        return bool(active_id) and active_id in targets
    return True


def _block_shuffle(owner: str, arc_id: str, block: int, count: int) -> list:
    order = list(range(count))
    for i in range(count - 1, 0, -1):
        j = _crc32(f"{owner}:{arc_id}:shuf:{block}:{i}") % (i + 1)
        order[i], order[j] = order[j], order[i]
    return order


def _pick_topic_index(owner: str, arc_id: str, cycle: int, count: int) -> int:
    """确定性无相邻重复的主题轮换：每 count 轮遍历全部主题且跨轮不撞车。"""
    if count <= 1:
        return 0
    if count == 2:
        base = _crc32(f"{owner}:{arc_id}:t0") % 2
        return (cycle + base) % 2
    block = cycle // count
    slot = cycle % count
    order = _block_shuffle(owner, arc_id, block, count)
    if block > 0:
        prev_last = _block_shuffle(owner, arc_id, block - 1, count)[-1]
        if order[0] == prev_last:
            order[0], order[1] = order[1], order[0]
    return order[slot]


def persona_evolution_note(text: str, today: date) -> str:
    """按日期计算动态年龄与单向人生阶段，覆盖静态人设中的初始时间切片（不修改默认人设文件）。"""
    clock = persona_clock_rules(text)
    milestones = persona_milestones_rules(text)
    if not clock and not milestones:
        return ""

    lines = [
        "[OIT_PERSONA_EVOLUTION] 角色当前时间线状态"
        "(初始人设正文仅代表初始设定，凡年龄、学历/身份或近况与此处冲突时，一律以本区块为准，自然融入言行勿透露机制):"
    ]
    has_item = False

    if clock:
        birth = date.fromisoformat(clock["birth"])
        bday_this = _safe_birthday_in_year(birth, today.year)
        age = today.year - birth.year - (1 if today < bday_this else 0)
        if age >= 0:
            if today == bday_this:
                bday_hint = f"，今天正好是你{age}岁生日"
            else:
                next_bday = bday_this if today < bday_this else _safe_birthday_in_year(birth, today.year + 1)
                days_left = (next_bday - today).days
                bday_hint = f"，距离你{age + 1}岁生日还有{days_left}天" if 1 <= days_left <= 7 else ""
            lines.append(f"- 当前年龄: {age}岁（生日 {birth.month:02d}-{birth.day:02d}{bday_hint}）")
            has_item = True

    if milestones:
        active, _reached, upcoming = _milestone_state(milestones, today)
        if active:
            lines.append(f"- 当前人生阶段: {active['label']}（自 {active['date']} 起）— {active['summary']}")
            has_item = True
        if upcoming:
            up_date = date.fromisoformat(upcoming["date"])
            days_to = (up_date - today).days
            if 1 <= days_to <= 14:
                lines.append(f"- 临近节点: 距离「{upcoming['label']}」（{upcoming['date']}）还有{days_to}天")
                has_item = True

    return "\n".join(lines) if has_item else ""


def life_arcs_note(text: str, owner_name: str, today: date) -> str:
    """按日期与确定性 CRC32 种子推进角色慢变量生活线；支持里程碑阶段过滤与结束后自动更新新主题。"""
    specs = life_arcs_specs(text)
    if not specs:
        return ""
    milestones = persona_milestones_rules(text)
    active_ms, reached_ms, _ = _milestone_state(milestones, today)
    active_id = active_ms["id"] if active_ms else ""
    arc_ids = [aid for aid, spec in specs.items() if _arc_allowed(spec, active_id, reached_ms)]
    if not arc_ids:
        return ""

    owner = str(owner_name or "").strip().lower()
    day_ord = today.toordinal() - _EPOCH_2024
    focus_idx = _crc32(f"{owner}:{today.isoformat()}:focus") % len(arc_ids)
    lines = ["[OIT_LIFE_ARC] 你近期的个人生活进展(自然融入闲聊或主动话题，活动结束后自动切换新项，勿透露机制或编号):"]
    for idx, arc_id in enumerate(arc_ids):
        spec = specs[arc_id]
        lo, hi, stages, topics = spec["min_days"], spec["max_days"], spec["stages"], spec["topics"]
        span = lo + (_crc32(f"{owner}:{arc_id}:span") % (hi - lo + 1))
        offset = _crc32(f"{owner}:{arc_id}:off") % span
        total = day_ord + offset
        cycle = total // span
        pos = total % span
        stage_idx = min(len(stages) - 1, (pos * len(stages)) // span)
        stage_text = stages[stage_idx]
        transition = ""
        if topics:
            cur_topic = topics[_pick_topic_index(owner, arc_id, cycle, len(topics))]
            stage_text = stage_text.replace("{topic}", cur_topic)
            if pos == 0:
                prev_topic = topics[_pick_topic_index(owner, arc_id, cycle - 1, len(topics))]
                transition = f" [刚开启新项，上一项「{prev_topic}」已收尾]"
            elif pos == span - 1:
                transition = " [今日收尾，完成后将开启下一项]"
        else:
            if pos == 0:
                transition = " [新轮次开启，换新内容勿重复上轮]"
            elif pos == span - 1:
                transition = " [今日收尾，完成后将开启下一项]"
        mark = "★今日焦点 " if idx == focus_idx else ""
        lines.append(f"- {mark}{arc_id}(第{pos + 1}/{span}天): {stage_text}{transition}")
    return "\n".join(lines)


def weekly_trend_note(text: str, cond_days: dict, today: date, share_enabled: bool) -> str:
    """根据已有 cond_days 账本生成跨周专注/空闲趋势；仅在用户开启 share_data 且声明 [trend] 时生效。"""
    if not share_enabled:
        return ""
    cfg = trend_rules(text)
    if not cfg or not isinstance(cond_days, dict):
        return ""
    window = cfg["window"]
    min_days = cfg["min_days"]
    delta_pct = cfg["delta_pct"]

    cur_keys = [(today - timedelta(days=i)).isoformat() for i in range(1, window + 1)]
    prev_keys = [(today - timedelta(days=i)).isoformat() for i in range(window + 1, window * 2 + 1)]
    cur_vals = [cond_days[k] for k in cur_keys if k in cond_days and isinstance(cond_days[k], (list, tuple)) and len(cond_days[k]) == 2]
    if len(cur_vals) < min_days:
        return ""
    prev_vals = [cond_days[k] for k in prev_keys if k in cond_days and isinstance(cond_days[k], (list, tuple)) and len(cond_days[k]) == 2]

    cur_study_avg = (sum(v[0] for v in cur_vals) / len(cur_vals)) / 3600.0
    cur_idle_avg = (sum(v[1] for v in cur_vals) / len(cur_vals)) / 3600.0
    parts = [f"近{window}天(记录{len(cur_vals)}天)日均专注 {cur_study_avg:.1f}h, 日均空闲 {cur_idle_avg:.1f}h"]

    if len(prev_vals) >= min_days:
        prev_study_avg = (sum(v[0] for v in prev_vals) / len(prev_vals)) / 3600.0
        if prev_study_avg >= 0.2:
            pct = round(((cur_study_avg - prev_study_avg) / prev_study_avg) * 100)
            label = "上升" if pct >= delta_pct else ("下降" if pct <= -delta_pct else "持平")
            parts.append(f"较前{window}天 {pct:+d}%({label})")
        elif cur_study_avg >= 0.5:
            parts.append(f"较前{window}天明显回升")

    # 近3个有记录日子的连续趋势（按时间从旧到新）
    recent_chron = [cond_days[k][0] for k in reversed(cur_keys) if k in cond_days and isinstance(cond_days[k], (list, tuple)) and len(cond_days[k]) == 2]
    if len(recent_chron) >= 3:
        last3 = recent_chron[-3:]
        if last3[0] < last3[1] < last3[2] and (last3[2] - last3[0]) >= 1800:
            parts.append("近3日专注连续走高")
        elif last3[0] > last3[1] > last3[2] and (last3[0] - last3[2]) >= 1800:
            parts.append("近3日专注连续走低")

    today_val = cond_days.get(today.isoformat())
    if isinstance(today_val, (list, tuple)) and len(today_val) == 2:
        parts.append(f"今日已专注 {today_val[0] / 3600.0:.1f}h")

    return f"[OIT_WEEKLY_TREND: {'；'.join(parts)}]"


def callback_note(text: str, mem_live: str, today: date) -> str:
    """扫描实时记忆事件中 [回访:YYYY-MM-DD] 条目，提示到期回访或过期清理。"""
    cfg = callback_rules(text)
    if not cfg or not mem_live:
        return ""
    lookahead = cfg["lookahead_days"]
    expire = cfg["expire_days"]
    max_due = cfg["max_due"]

    due = []
    expired = []
    for raw_line in str(mem_live).splitlines():
        m = _CALLBACK_RE.match(raw_line)
        if not m:
            continue
        try:
            target = date.fromisoformat(m[1])
        except ValueError:
            continue
        diff = (today - target).days
        body = m[2].strip()
        if -lookahead <= diff <= expire:
            if diff == 0:
                when = "今天到期"
            elif diff == 1:
                when = "昨天到期(可顺口追问结果)"
            elif diff > 1:
                when = f"{diff}天前到期(可顺口追问结果)"
            else:
                when = f"{-diff}天后临近"
            due.append((diff, m[1], when, body))
        elif diff > expire:
            expired.append((m[1], body))

    if not due and not expired:
        return ""
    due.sort(key=lambda item: (abs(item[0]), item[1]))
    lines = ["[OIT_CALLBACK_DUE] 待回访事项(自然时机顺口提及，问过之后用 oit_memory_edit 移除):"]
    for _diff, d_str, when, body in due[:max_due]:
        lines.append(f"- [{d_str}] {when}: {body}")
    if expired:
        exp_items = "；".join(f"[{d_str}] {body}" for d_str, body in expired[:3])
        lines.append(f"- 已过期可清理: {exp_items}")
    return "\n".join(lines)


def _clean_text(s: str) -> str:
    return _STRIP_EDGE_RE.sub("", str(s or "").strip())


def anti_repeat_note(text: str, messages: list) -> str:
    """统计最近 window 条 AI 回复中的高频开头、短语、结尾及禁用词命中。"""
    cfg = anti_repeat_rules(text)
    if not cfg or not messages:
        return ""
    window = cfg["window"]
    min_hits = cfg["min_hits"]
    max_items = cfg["max_items"]
    forbid = cfg["forbid"]

    ai_texts = [
        str(m.get("text") or "").strip()
        for m in messages
        if isinstance(m, dict) and m.get("role") == "ai" and not m.get("hold") and "pending_reply" not in m and str(m.get("text") or "").strip()
    ][-window:]
    if not ai_texts:
        return ""

    open_counts = Counter()
    end_counts = Counter()
    phrase_counts = Counter()
    forbid_hits = Counter()

    for body in ai_texts:
        for word in forbid:
            if word in body:
                forbid_hits[word] += 1

        # 按消息去重统计，避免单条长回复刷屏
        msg_opens = set()
        msg_ends = set()
        msg_phrases = set()

        first_clean = _clean_text(body)
        cjk_head = _CJK_RUN_RE.match(first_clean)
        if cjk_head and len(cjk_head.group(0)) >= 2:
            head = cjk_head.group(0)[:2]
            if head not in _STOP_PHRASES:
                msg_opens.add(head)

        for sent in _SENTENCE_SPLIT_RE.split(body):
            sc = _clean_text(sent)
            if len(sc) < 4:
                continue
            runs = _CJK_RUN_RE.findall(sc)
            if runs and len(runs[-1]) >= 2:
                tail = runs[-1][-min(3, len(runs[-1])):]
                if tail not in _STOP_PHRASES:
                    msg_ends.add(tail)
            for run in runs:
                rlen = len(run)
                for n in (3, 4):
                    if rlen < n:
                        continue
                    for i in range(rlen - n + 1):
                        gram = run[i:i + n]
                        if gram not in _STOP_PHRASES:
                            msg_phrases.add(gram)

        for k in msg_opens:
            open_counts[k] += 1
        for k in msg_ends:
            end_counts[k] += 1
        for k in msg_phrases:
            phrase_counts[k] += 1

    # 短语去重：若较短片段被命中次数相当或更高的较长片段包含，保留较长的
    raw_phrases = [(p, c) for p, c in phrase_counts.items() if c >= min_hits]
    raw_phrases.sort(key=lambda kv: (-len(kv[0]), -kv[1], kv[0]))
    kept_phrases = []
    for p, c in raw_phrases:
        if any(p in kp and kc >= c for kp, kc in kept_phrases):
            continue
        kept_phrases.append((p, c))
    kept_phrases.sort(key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))

    edge_min = min_hits + 1
    top_opens = [(k, c) for k, c in open_counts.most_common() if c >= edge_min]
    top_ends = [(k, c) for k, c in end_counts.most_common() if c >= edge_min]

    hints = []
    for k, c in top_opens[:2]:
        hints.append(f"开头「{k}」×{c}")
    for k, c in kept_phrases:
        if len(hints) >= max_items:
            break
        if any(k == ok for ok, _ in top_opens[:2]):
            continue
        hints.append(f"短语「{k}」×{c}")
    for k, c in top_ends[:2]:
        if len(hints) >= max_items:
            break
        if any(k in h for h in hints):
            continue
        hints.append(f"结尾「{k}」×{c}")

    forbid_part = ""
    if forbid_hits:
        hit_list = "、".join(f"{w}×{c}" for w, c in forbid_hits.most_common())
        forbid_part = f"；近期误用禁用词({hit_list}，本轮务必避开)"

    if not hints and not forbid_part:
        return ""
    body_text = "、".join(hints) if hints else "无高频短语"
    return f"[OIT_ANTI_REPEAT: 近期高频表达(请换用其他说法，避免复读): {body_text}{forbid_part}]"


def realism_note(
    persona_text: str,
    owner_name: str,
    today: date,
    messages: list,
    mem_live: str,
    cond_days: dict,
    share_enabled: bool,
) -> str:
    """拼装拟真增强与动态时间线的紧凑动态块；未命中或未声明模板时返回空字符串。"""
    blocks = [
        persona_evolution_note(persona_text, today),
        life_arcs_note(persona_text, owner_name, today),
        weekly_trend_note(persona_text, cond_days, today, share_enabled),
        callback_note(persona_text, mem_live, today),
        anti_repeat_note(persona_text, messages),
    ]
    return "\n".join(b for b in blocks if b)
