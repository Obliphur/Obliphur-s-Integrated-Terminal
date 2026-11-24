"""Offline knowledge fields, generality gating, deduplication and auto-import parsing."""
import json
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempstore
tempstore.session("knowledge_check")

from knowledge import KnowledgeBase
from knowledge.kb_ai import prepare_subject
from knowledge.kb_auto_import import _anki_groups, _source_groups, auto_generate


base = Path(tempstore.mkdtemp("misc", prefix="knowledge-check-"))
kb = KnowledgeBase(base / "vault")


def save(title, generality, triggers):
    return kb.save_card({"kind": "tactical", "subject": "math", "title": title,
                         "generality": generality, "triggers": triggers,
                         "statement": "可核对的结论", "preconditions": "满足前提",
                         "proof_sketch": "由定义推出"})


universal = save("通用比例关系", "universal", ["比例", "相似"])
explained = save("需解释的比例变换", "explain_first", ["比例变换", "相似"])
special = save("特殊图形捷径", "non_universal", ["特殊图形", "相似"])
assert kb.resolve_trace(special["id"])["generality"] == "non_universal"
hits = kb.search_tactical("math", "比例 相似 特殊图形", 20, problem_mode="requires_working")
ids = {item["card_id"] for item in hits}
assert universal["id"] in ids and explained["id"] in ids and special["id"] not in ids
focused = kb.search_tactical("math", "比例 相似 特殊图形", 1, problem_mode="requires_working")
assert len(focused) == 1 and focused[0]["card_id"] != special["id"], "应在排名前筛选，而不是让不可用卡片占满 top_k"
kb.configure(thought_reference=True)
hits = kb.search_tactical("math", "比例 相似 特殊图形", 20, problem_mode="requires_working")
special_hit = next(item for item in hits if item["card_id"] == special["id"])
assert special_hit["reference_only"] is True
focused = kb.search_tactical("math", "特殊图形", 1, problem_mode="requires_working")
assert focused[0]["card_id"] == special["id"] and focused[0]["reference_only"]
from knowledge.kb_ai import finish_subject
clean, citations = finish_subject(kb, lambda *a, **k: '{"used_card_ids":[],"failed_card_ids":[],"proposals":[]}',
                                  "math", "分析特殊图形", "参考方向 <!-- kb_cite: " + special["id"] + " -->", [special_hit])
assert clean == "参考方向" and citations == [], "思路参考卡不能作为书面推导的依据引用"
kb.configure(thought_reference=False)
hits = kb.search_tactical("math", "比例 相似 特殊图形", 20, problem_mode="answer_only")
assert special["id"] in {item["card_id"] for item in hits}


calls = []
def classifier(prompt, **kwargs):
    calls.append((prompt, kwargs))
    return '{"problem_mode":"requires_working"}'


task, selected = prepare_subject(kb, classifier, "math", "分析一个三角形比例关系")
assert "需要书写过程" in task and "通用性筛选" in task
assert all(item["card_id"] != special["id"] for item in selected)
assert calls and calls[0][1].get("role") == "knowledge_classify"


source = base / "source"
source.mkdir()
(source / "lesson.md").write_text("三角形中的比例可以由相似关系推出。\n", encoding="utf-8")
(source / "lesson_image.png").write_bytes(b"\x89PNG\r\n\x1a\nlesson")
(source / "unrelated.txt").write_text("随手记：下午买牛奶。", encoding="utf-8")
groups, file_count = _source_groups(source, {".md", ".txt", ".png"})
lesson = next(item for item in groups if Path(item["source"]).name == "lesson.md")
assert len(lesson["images"]) == 1 and file_count == 3

anki = base / "deck.apkg"
database = base / "collection.anki2"
connection = sqlite3.connect(database)
connection.execute("CREATE TABLE col (models TEXT)")
connection.execute("CREATE TABLE notes (flds TEXT, mid INTEGER)")
connection.execute("INSERT INTO col VALUES (?)", (json.dumps({"1": {"flds": [{"name": "Front"}, {"name": "Back"}]}}),))
connection.execute("INSERT INTO notes VALUES (?, ?)", ("What is similarity?\x1f<img src=\"diagram.png\">", 1))
connection.commit(); connection.close()
with zipfile.ZipFile(anki, "w") as archive:
    archive.write(database, "collection.anki2")
    archive.writestr("media", json.dumps({"0": "diagram.png"}))
    archive.writestr("0", b"\x89PNG\r\n\x1a\nanki-image")
anki_groups = list(_anki_groups(anki))
assert len(anki_groups) == 1 and "Front: What is similarity?" in anki_groups[0]["text"]
assert len(anki_groups[0]["images"]) == 1


def generated(title, kind="conceptual"):
    return {"knowledge": True, "cards": [{"kind": kind, "subject": "math", "title": title,
            "topic": "比例", "triggers": ["相似比例"], "statement": "可核对的结论",
            "preconditions": "满足前提", "proof_sketch": "由定义推出", "generality": "universal"}]}


created = []
def fake_ai(prompt, *, image=None, **kwargs):
    created.append((prompt, image))
    if "随手记" in prompt:
        return '{"knowledge":false,"cards":[]}'
    name = "已存在卡片" if "重复材料" in prompt else "自动导入概念"
    return json.dumps(generated(name))


(source / "duplicate.txt").write_text("重复材料。", encoding="utf-8")
assert kb.save_card({"kind": "conceptual", "subject": "math", "title": "已存在卡片", "body": "已在库中"})
report = auto_generate(kb, fake_ai, source, ["txt", "md", "image"])
assert report["created"] == 1 and report["duplicates"] == 1 and report["ignored"] == 1, report
assert any(image for _, image in created), "同名配套图片应与正文一起交给 AI"
assert any(card["title"] == "自动导入概念" for card in kb.conceptual_cards())

print("knowledge check OK")
