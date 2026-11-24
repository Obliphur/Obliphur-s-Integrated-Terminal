"""Tactical-only citation parsing. Never expose conceptual notes to solvers."""
import re
from urllib.parse import quote
from .kb_rank import ancestors, depth

CITE = re.compile(r"<!--\s*kb_cite:\s*(.*?)-->", re.I | re.S)


def strip_citations(text):
    ids = []
    for match in CITE.finditer(str(text)):
        ids.extend(re.findall(r"[\w-]+", match.group(1)))
    clean = CITE.sub("", str(text))
    clean = re.sub(r"<!--\s*kb_discovery\s*-->", "", clean, flags=re.I)
    return clean.strip(), list(dict.fromkeys(ids))


def obsidian_uri(path):
    return "obsidian://open?path=" + quote(str(path), safe="")


def trace(cards, stats, card_id):
    card = cards.get(card_id)
    if not card:
        return None
    freq = stats.get(card_id, {}).get("freq", 0)
    return {"card_id": card_id, "title": card.get("title", ""), "subject": card.get("subject", ""),
            "familiarity": "unfamiliar" if freq == 0 else "learning" if freq < 3 else "familiar",
            "freq": freq, "unverified": bool(card.get("unverified")),
            **{key: card.get(key, "") for key in ("statement", "preconditions", "proof_sketch", "file_path")},
            "lineage": [{"card_id": key, "title": cards[key].get("title", ""), "depth": depth(cards, key)}
                        for key in ancestors(cards, card_id)],
            "obsidian_uri": obsidian_uri(card.get("file_path", "")) if card.get("file_path") else ""}
