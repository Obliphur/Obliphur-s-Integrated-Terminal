"""Offline tactical retrieval and cycle-safe DAG ranking."""
import math
import re
from datetime import datetime


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
        match = (0.55 * overlap(words, " ".join(triggers)) +
                 0.25 * overlap(words, card.get("title", "") + " " + " ".join(card.get("aliases") or [])) +
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
