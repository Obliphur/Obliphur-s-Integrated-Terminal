"""Contributor proposals never write main-library Markdown before approval."""
import difflib
import subprocess
import threading
import uuid
from .kb_store import read_json, write_json, atomic_text, read_card, serialize, update_body
from .kb_stats import now

ACTIONS = {"ADD_CARD", "ADD_DERIVED", "UPDATE_CARD", "MERGE_ALIAS", "REPARENT"}
FIELDS = {"category", "topic", "aliases", "triggers", "statement", "preconditions", "proof_sketch", "examples", "parent_ids", "generality"}


class ProposalMixin:
    def proposals(self, status="open"):
        result = []
        for path in sorted((self.internal / "proposals").glob("*.json")):
            pr = read_json(path)
            if pr.get("status") == status and pr.get("action") in ACTIONS:
                result.append(pr)
        return result

    def propose(self, action, subject="math", title="", proposed_card=None, target_card_id=None,
                parent_ids=None, parents=None, summary="", evidence=None):
        with self.lock:
            self.refresh()
            if action not in ACTIONS or subject not in self.subjects:
                raise ValueError("提案类型或学科无效")
            changes = {k: v for k, v in (proposed_card or {}).items() if k in FIELDS}
            if not isinstance(title, str) or not isinstance(summary, str):
                raise ValueError("提案标题无效")
            for key, value in changes.items():
                if key in ("aliases", "triggers", "parent_ids"):
                    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                        raise ValueError("提案列表字段无效")
                elif key == "generality" and value not in {"universal", "explain_first", "non_universal"}:
                    raise ValueError("通用性无效")
                elif not isinstance(value, (str, list)):
                    raise ValueError("提案字段无效")
            parents = parent_ids if parent_ids is not None else (parents if parents is not None else changes.get("parent_ids", []))
            if not isinstance(parents, list) or any(p not in self.cards or self.cards[p].get("subject") != subject for p in parents):
                raise ValueError("父结论必须是同学科技巧卡片")
            if target_card_id and (target_card_id not in self.cards or self.cards[target_card_id].get("subject") != subject):
                raise ValueError("目标技巧卡片不存在")
            if action in ("ADD_CARD", "ADD_DERIVED"):
                hits = self.search_tactical(subject, title + " " + " ".join(changes.get("triggers", [])), 3)
                duplicate = next((c for c in hits if c.get("title") == title or c["match_score"] >= .82), None)
                if duplicate:
                    action, target_card_id = "MERGE_ALIAS", duplicate["card_id"]
                    changes.setdefault("aliases", []).append(title)
            if action in ("UPDATE_CARD", "MERGE_ALIAS", "REPARENT") and not target_card_id:
                raise ValueError("修改提案必须指定目标")
            if action == "ADD_DERIVED" and not parents:
                raise ValueError("衍生提案需要父结论")
            title = title.strip() or self.cards.get(target_card_id, {}).get("title", "")
            if not title:
                raise ValueError("请填写标题")
            evidence = evidence or []
            if not isinstance(evidence, list) or any(not isinstance(e, dict) for e in evidence):
                raise ValueError("证据格式无效")
            old = next((p for p in self.proposals() if p["subject"] == subject and
                        ((target_card_id and p.get("target_card_id") == target_card_id) or
                         (not target_card_id and not p.get("target_card_id") and p["title"] == title))), None)
            pr = old or {"pr_id": "pr_" + uuid.uuid4().hex, "status": "open", "action": action,
                         "subject": subject, "title": title, "target_card_id": target_card_id,
                         "created_at": now(), "squash_count": 0, "evidence": [], "proposed_card": {}}
            for key, value in changes.items():
                if key in ("aliases", "triggers", "examples") and isinstance(value, list):
                    previous = pr["proposed_card"].get(key, [])
                    if not isinstance(previous, list):
                        previous = [previous]
                    value = list(dict.fromkeys(previous + value))
                pr["proposed_card"][key] = value
            pr.update(parent_ids=parents or pr.get("parent_ids", []), summary=summary or pr.get("summary", ""), updated_at=now())
            pr["evidence"].extend(evidence)
            pr["squash_count"] += 1
            write_json(self.internal / "proposals" / (pr["pr_id"] + ".json"), pr)
            return pr

    def _proposal(self, pr_id):
        return next((p for p in self.proposals() if p["pr_id"] == pr_id), None)

    def proposal_diff(self, pr_id):
        pr = self._proposal(pr_id)
        if not pr:
            return []
        old = self.cards.get(pr.get("target_card_id"), {})
        changes = {**pr["proposed_card"], "parent_ids": pr.get("parent_ids", [])}
        return list(difflib.unified_diff([f"{k}: {old.get(k, '')}\n" for k in changes],
                                         [f"{k}: {v}\n" for k, v in changes.items()], fromfile="before", tofile="after"))

    def merge(self, pr_id, *, maintainer=False, edits=None):
        if not maintainer:
            raise PermissionError("合入需要管理员确认")
        with self.lock:
            self.refresh()
            pr = self._proposal(pr_id)
            if not pr:
                raise ValueError("提案已处理或不存在")
            old = self.cards.get(pr.get("target_card_id"), {})
            if pr["action"] in ("UPDATE_CARD", "MERGE_ALIAS", "REPARENT") and not old:
                raise ValueError("目标卡片已删除")
            changes = {**pr["proposed_card"], **(edits or {})}
            if pr["action"] == "MERGE_ALIAS":
                for key in ("aliases", "triggers", "examples"):
                    if key in changes:
                        a, b = old.get(key, []), changes[key]
                        a = a if isinstance(a, list) else [a] if a else []
                        b = b if isinstance(b, list) else [b]
                        changes[key] = list(dict.fromkeys(a + b))
                changes = {k: v for k, v in changes.items() if k in ("aliases", "triggers", "examples")}
            parents = changes.get("parent_ids", pr.get("parent_ids", old.get("parent_ids", [])))
            card = {**old, **changes, "kind": "tactical", "subject": pr["subject"],
                    "title": old.get("title", pr["title"]), "parent_ids": parents}
            # Save the authoritative card first; backlink updates remain idempotent.
            saved = self.save_card(card, maintainer=True)
            paths = [saved["file_path"]]
            old_parents = set(old.get("parent_ids", []))
            new_parents = set(parents)
            for parent_id in old_parents - new_parents:
                parent = self.cards.get(parent_id)
                if parent:
                    relation_lines = [line for line in str(parent.get("relations", "")).splitlines()
                                      if saved["title"] not in line]
                    self.save_card({**parent, "relations": "\n".join(relation_lines)}, maintainer=True)
                    paths.append(parent["file_path"])
            for parent_id in parents:
                parent = self.cards.get(parent_id)
                if parent and "[[" + saved["title"] + "]]" not in parent.get("relations", ""):
                    parent = {**parent, "relations": parent.get("relations", "") + "\n- [[" + saved["title"] + "]]"}
                    self.save_card(parent, maintainer=True)
                    paths.append(parent["file_path"])
            pr.update(status="merged", updated_at=now(), merged_card_id=saved["id"])
            write_json(self.internal / "proposals" / (pr_id + ".json"), pr)
            self.audit("merge", pr_id=pr_id, card_id=saved["id"])
            if self.config.get("git_auto_commit") and (self.root / ".git").exists():
                def commit():
                    try:
                        subprocess.run(["git", "add", "--", *paths], cwd=self.root, check=True, capture_output=True, timeout=30)
                        subprocess.run(["git", "commit", "--only", "-m", "kb: " + pr["action"] + " " + pr["title"], "--", *paths], cwd=self.root, check=True, capture_output=True, timeout=30)
                    except (OSError, subprocess.SubprocessError) as e:
                        self.audit("git_error", error=type(e).__name__)
                threading.Thread(target=commit, daemon=True).start()
            return saved

    def reject(self, pr_id, *, maintainer=False):
        if not maintainer:
            raise PermissionError("拒绝需要管理员确认")
        with self.lock:
            pr = self._proposal(pr_id)
            if not pr:
                return
            pr.update(status="rejected", updated_at=now())
            write_json(self.internal / "proposals" / (pr_id + ".json"), pr)
            self.audit("reject", pr_id=pr_id)
