"""Offline tactical retrieval, duplicate detection and cycle-safe DAG ranking."""
from difflib import SequenceMatcher
import math
import re
from datetime import datetime


_TITLE_NOISE_RE = re.compile(r"[^a-z0-9\u3400-\u9fff]+")
_GENERIC_SUFFIX_RE = re.compile(
    r"(?:二级结论|快捷结论|解题技巧|解题方法|常用结论|常用技巧|常用方法|技巧|结论|方法|公式|定理|法则|规律|模型|套路|思想|性质|推论|判定|判别法)+$"
)
_SERIAL_TAIL_RE = re.compile(r"^(.*?)([a-z]|[0-9]+|[一二三四五六七八九十甲乙丙丁上下前后左右])$")


def normalize_title(text):
    return _TITLE_NOISE_RE.sub("", str(text or "").casefold())


def core_title(text):
    norm = normalize_title(text)
    stripped = _GENERIC_SUFFIX_RE.sub("", norm)
    return stripped if len(stripped) >= 2 else norm


def _is_sibling_serial(a, b):
    ma, mb = _SERIAL_TAIL_RE.match(a), _SERIAL_TAIL_RE.match(b)
    return bool(ma and mb and ma.group(1) and ma.group(1) == mb.group(1) and ma.group(2) != mb.group(2))


def _card_names(card):
    raw = [card.get("title", ""), *(card.get("aliases") or ())]
    norms = {normalize_title(n) for n in raw if normalize_title(n)}
    cores = {core_title(n) for n in raw if core_title(n)}
    return norms, cores


def _jaccard_overlap_blend(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    jacc = inter / len(a | b)
    if min(len(a), len(b)) < 4:
        return jacc
    ov = inter / min(len(a), len(b))
    return 0.55 * jacc + 0.45 * ov


def card_similarity(candidate, existing):
    """计算两张卡片/提案的重复度（0~1），综合规范标题、核心词、别名、触发词与结论正文。"""
    if not isinstance(candidate, dict) or not isinstance(existing, dict):
        return 0.0
    if candidate.get("subject") and existing.get("subject") and candidate.get("subject") != existing.get("subject"):
        return 0.0
    c_norms, c_cores = _card_names(candidate)
    e_norms, e_cores = _card_names(existing)
    if c_norms & e_norms:
        return 1.0
    if c_cores & e_cores:
        return 0.94

    title_sim = 0.0
    contained = False
    sibling = False
    for cn in c_norms:
        for en in e_norms:
            if _is_sibling_serial(cn, en):
                sibling = True
                continue
            ratio = SequenceMatcher(None, cn, en).ratio()
            if ratio > title_sim:
                title_sim = ratio
            short, long_ = (cn, en) if len(cn) <= len(en) else (en, cn)
            if len(short) >= 3 and short in long_:
                contained = True
                title_sim = max(title_sim, 0.80 + 0.15 * (len(short) / max(1, len(long_))))

    c_trig = tokens(" ".join(candidate.get("triggers") or ()))
    e_trig = tokens(" ".join(existing.get("triggers") or ()))
    trig_sim = _jaccard_overlap_blend(c_trig, e_trig)

    c_body_text = " ".join([
        str(candidate.get("statement") or ""),
        str(candidate.get("body") or ""),
    ])
    e_body_text = " ".join([
        str(existing.get("statement") or ""),
        str(existing.get("body") or ""),
    ])
    c_body = tokens(c_body_text)
    e_body = tokens(e_body_text)
    body_sim = _jaccard_overlap_blend(c_body, e_body)

    c_all = tokens(" ".join([
        str(candidate.get("title") or ""),
        str(candidate.get("topic") or ""),
        " ".join(candidate.get("aliases") or ()),
        " ".join(candidate.get("triggers") or ()),
    ]))
    e_all = tokens(" ".join([
        str(existing.get("title") or ""),
        str(existing.get("topic") or ""),
        " ".join(existing.get("aliases") or ()),
        " ".join(existing.get("triggers") or ()),
    ]))
    meta_sim = _jaccard_overlap_blend(c_all, e_all)

    score = max(
        0.42 * title_sim + 0.28 * trig_sim + 0.30 * body_sim,
        0.55 * title_sim + 0.45 * meta_sim,
    )
    if sibling and body_sim < 0.75:
        score = min(score, 0.50)
    if contained and (body_sim >= 0.22 or trig_sim >= 0.25 or title_sim >= 0.86):
        score = max(score, 0.82)
    if body_sim >= 0.68 and (title_sim >= 0.35 or trig_sim >= 0.35):
        score = max(score, 0.78)
    return min(1.0, score)


def find_similar_card(candidates, target, *, threshold=0.62):
    best, best_score = None, 0.0
    for item in candidates:
        score = card_similarity(target, item)
        if score > best_score:
            best, best_score = item, score
    if best is not None and best_score >= threshold:
        return best, best_score
    return None, best_score


def tokens(text):
    text = str(text).lower()
    result = set(re.findall(r"[a-z0-9_]+|[α-ω]", text))
    for word in re.findall(r"[\u3400-\u9fff]+", text):
        result.update(word[i:i+n] for n in (2, 3) for i in range(len(word)-n+1))
        if len(word) == 1:
            result.add(word)
    return result


def overlap(query, text):
    target = tokens(text)
    return len(query & target) / max(1, min(len(query), len(target)))


def _triggers_score(words, triggers):
    """按单条 trigger 独立匹配，避免增加触发词数量反而稀释命中率；弱局部重合平方衰减防误报。"""
    if not words or not triggers:
        return 0.0
    raw = (overlap(words, t) for t in triggers if str(t or "").strip())
    scores = sorted((s if s >= 0.5 else s * s for s in raw), reverse=True)
    if not scores or scores[0] <= 0:
        return 0.0
    s1 = scores[0]
    s2 = scores[1] if len(scores) > 1 else 0.0
    s3 = scores[2] if len(scores) > 2 else 0.0
    return 0.65 * s1 + 0.25 * s2 + 0.10 * s3


def _name_score(words, title, aliases):
    """标题与每条别名独立匹配取最高分，避免多条别名互相稀释。"""
    if not words:
        return 0.0
    parts = [title, *(aliases or ())]
    return max((overlap(words, p) for p in parts if str(p or "").strip()), default=0.0)


def ancestors(cards, card_id):
    seen, queue, out = {card_id}, list(cards.get(card_id, {}).get("parent_ids") or []), []
    while queue:
        key = queue.pop(0)
        if key in seen or key not in cards:
            continue
        seen.add(key)
        out.append(key)
        queue.extend(cards[key].get("parent_ids") or [])
    return out


def depth(cards, key, seen=None):
    seen = set() if seen is None else seen
    if key in seen or key not in cards:
        return 0
    parents = [p for p in cards[key].get("parent_ids", []) if p in cards and p not in seen | {key}]
    return 1 + max(depth(cards, p, seen | {key}) for p in parents) if parents else 0


def rank(cards, stats, query, top_k, threshold=0.15):
    words = tokens(query)
    candidates = {}
    for key, card in cards.items():
        triggers = card.get("triggers") or []
        match = (0.55 * _triggers_score(words, triggers) +
                 0.25 * _name_score(words, card.get("title", ""), card.get("aliases")) +
                 0.20 * overlap(words, card.get("topic", "") + " " + card.get("body", "")))
        if match >= threshold:
            candidates[key] = match
    result = []
    for key, match in candidates.items():
        card, st = cards[key], stats.get(key, {})
        days = 10000
        try:
            days = max(0, (datetime.now().astimezone() - datetime.fromisoformat(st["last_used"]).astimezone()).total_seconds() / 86400)
        except (ValueError, KeyError, TypeError):
            pass
        level = depth(cards, key)
        bonus = 0.25 * min(level, 3) if any(match >= 0.75 * candidates[p] for p in ancestors(cards, key) if p in candidates) else 0
        score = match * (1 + .30 * math.log1p(max(0, st.get("freq", 0))) / math.log1p(20) +
                         .15 * math.exp(-days / 45) + (.25 if st.get("user_pinned") else 0)) * max(.3, min(3, st.get("weight", 1))) + bonus
        result.append({**card, "card_id": key, "match_score": match, "score": score, "depth": level,
                       "freq": st.get("freq", 0), "weight": st.get("weight", 1)})
    result.sort(key=lambda c: (-c["score"], bool(c.get("unverified")), c["card_id"]))
    out, drafts = [], 0
    for card in result:
        if card.get("unverified"):
            if drafts:
                continue
            drafts += 1
        out.append(card)
        if len(out) >= max(1, min(20, top_k)):
            break
    return out


def reminder_cooldown_days(shown, weight=1.0, pinned=False):
    """提醒卡随提醒次数动态拉长冷却间隔（1→2→4→8→16→30天），受权重调节；置顶或未提醒过时无冷却。"""
    shown = max(0, int(shown or 0))
    if pinned or shown == 0:
        return 0.0
    w = max(0.3, min(3.0, float(weight or 1.0)))
    return min(30.0, (2 ** min(shown - 1, 6)) / w)


def rank_reminders(cards, stats, query, top_k=3, threshold=0.15):
    words = tokens(query)
    if not words:
        return []
    now_dt = datetime.now().astimezone()
    out = []
    for key, card in cards.items():
        triggers = card.get("triggers") or []
        match = (0.55 * _triggers_score(words, triggers) +
                 0.25 * _name_score(words, card.get("title", ""), card.get("aliases")) +
                 0.20 * overlap(words, card.get("topic", "") + " " + card.get("body", "")))
        if match < threshold:
            continue
        st = stats.get(key, {})
        shown = max(0, int(st.get("shown", 0)))
        weight = max(0.3, min(3.0, float(st.get("weight", 1.0))))
        pinned = bool(st.get("user_pinned"))
        cooldown = reminder_cooldown_days(shown, weight, pinned)
        elapsed = 10000.0
        try:
            elapsed = max(0.0, (now_dt - datetime.fromisoformat(st["last_shown"]).astimezone()).total_seconds() / 86400.0)
        except (ValueError, KeyError, TypeError):
            pass
        if cooldown > 0 and elapsed < cooldown:
            continue
        score = match * (weight / (1.0 + 0.35 * shown)) * (1.3 if pinned else 1.0)
        out.append({**card, "card_id": key, "match_score": match, "score": score,
                    "shown": shown, "weight": weight, "cooldown_days": round(cooldown, 2)})
    out.sort(key=lambda c: (-c["score"], c["card_id"]))
    return out[:max(1, min(10, int(top_k)))]
