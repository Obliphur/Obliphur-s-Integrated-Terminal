"""Statistics are separate from note bodies and require no proposal approval."""
from datetime import datetime
from .kb_store import read_json, write_json


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


class StatsMixin:
    def record_use(self, used_card_ids=(), failed_card_ids=(), problem_summary=""):
        with self.lock:
            self.refresh()
            stats = read_json(self.internal / "stats.json")
            used, failed = set(used_card_ids), set(failed_card_ids)
            for key in (used | failed) & self.cards.keys():
                item = stats.setdefault(key, {"freq": 0, "weight": 1., "success_hits": 0, "fail_hits": 0})
                if key in failed:
                    item["fail_hits"] = item.get("fail_hits", 0) + 1
                    item["weight"] = max(.3, round(item.get("weight", 1) - .1, 4))
                else:
                    item["freq"] = item.get("freq", 0) + 1
                    item["success_hits"] = item.get("success_hits", 0) + 1
                    item["last_used"] = now()
                    item["weight"] = min(3., round(item.get("weight", 1) + .05, 4))
                item["recent_problems"] = (item.get("recent_problems", []) + [{"ts": now(), "summary": str(problem_summary)[:600], "outcome": "failed" if key in failed else "used"}])[-20:]
            write_json(self.internal / "stats.json", stats)
            self.audit("auto_weight", used=sorted(used & self.cards.keys()), failed=sorted(failed & self.cards.keys()))

    def set_stats(self, card_id, *, weight=None, pinned=None):
        with self.lock:
            self.refresh()
            if card_id not in self.cards:
                raise ValueError("技巧卡片不存在")
            stats = read_json(self.internal / "stats.json")
            item = stats.setdefault(card_id, {})
            if weight is not None:
                item["weight"] = max(.3, min(3., float(weight)))
            if pinned is not None:
                item["user_pinned"] = bool(pinned)
            write_json(self.internal / "stats.json", stats)
