"""Offline knowledge fields, generality gating, deduplication and auto-import parsing."""
import json
import os
import sqlite3
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
assert calls and "role" not in calls[0][1], "分类任务应沿用统一的解题子代理入口"

# 两个知识库入口都使用解题配置与同一子代理，且不携带聊天或解题会话历史。
from ai_assist.ai_subagent import SYSTEMS
from comm.comm_tools import ToolsMixin
from pages.knowledge_page import KnowledgePage
from knowledge.kb_ai import classify_problem, complete_card, import_previews

agent_calls = []
def subject_profile(role, fallback=None):
    assert role == "subject" and fallback == "chat-model"
    return {"config_id": "solver-model"}

def subject_chat(profile, system, messages, **kwargs):
    assert profile["config_id"] == "solver-model" and system == SYSTEMS["subject"]
    assert len(messages) == 1 and messages[0]["role"] == "user"
    prompt = messages[0]["content"]
    agent_calls.append(prompt)
    if "problem_mode" in prompt:
        return '{"problem_mode":"requires_working"}'
    if "used_card_ids" in prompt:
        return '{"used_card_ids":[],"failed_card_ids":[],"proposals":[]}'
    card = {"kind": "conceptual", "title": "比例定义", "body": "由定义说明比例。"}
    return json.dumps([card] if "JSON 数组" in prompt else card)

context = SimpleNamespace(ai=SimpleNamespace(task_profile=subject_profile, config_for=lambda _: "chat-model"),
                          persona_name=lambda: "测试人设", chat_fn=subject_chat)
notes = base / "routing.md"
notes.write_text("比例的定义。", encoding="utf-8")
for call in (lambda *a, **k: ToolsMixin._knowledge_call(context, *a, **k),
             lambda *a, **k: KnowledgePage.call_ai(SimpleNamespace(comm=context), *a, **k)):
    assert classify_problem(call, "分析三角形") == "requires_working"
    assert complete_card(kb, call, {"kind": "conceptual", "subject": "math"})["title"] == "比例定义"
    assert import_previews(kb, call, [notes])[0]["title"] == "比例定义"
    with patch("knowledge.kb_ai.threading.Thread", side_effect=lambda target, **_: SimpleNamespace(start=target)):
        finish_subject(kb, call, "math", "分析比例", "按定义逐步计算。" * 100, [])
assert len(agent_calls) == 8


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

# Obsidian 图片引用：跨目录 URL 编码相对路径（../../../%E9%99%84%E4%BB%B6/...）、![[...]] 与手动导入
obs_vault = base / "obsidian_vault"
(obs_vault / ".obsidian").mkdir(parents=True)
attach_dir = obs_vault / "附件"
attach_dir.mkdir()
pasted_jpg = attach_dir / "paste-a7b60f4d36daf0dae6041c0dee35a2ab9d804ec5.jpg"
pasted_jpg.write_bytes(b"\xff\xd8\xff\xe0obsidian-jpg")
wiki_png = attach_dir / "Pasted image 2026.png"
wiki_png.write_bytes(b"\x89PNG\r\n\x1a\nobsidian-wiki")
note_dir = obs_vault / "高中" / "数学" / "VI-复数"
note_dir.mkdir(parents=True)
obs_note = note_dir / "复数技巧 共轭与模长的合并.md"
obs_note.write_text(
    "# 复数技巧\n\n![](../../../%E9%99%84%E4%BB%B6/paste-a7b60f4d36daf0dae6041c0dee35a2ab9d804ec5.jpg)\n\n"
    "![[Pasted image 2026.png|300]]\n\n共轭与模长的合并。\n",
    encoding="utf-8",
)
# 1) 扫描整个仓库且勾选图片时，附件图片应配对给笔记，不重复生成孤立图片组
obs_groups, _ = _source_groups(obs_vault, {".md", ".jpg", ".png"})
assert len(obs_groups) == 1 and Path(obs_groups[0]["source"]) == obs_note.resolve()
assert [Path(name).resolve() for name, _ in obs_groups[0]["images"]] == [pasted_jpg.resolve(), wiki_png.resolve()]
# 2) 仅扫描子目录且仅选 .md 时，依然能跨回仓库根目录的「附件」解析到引用的图片
sub_groups, _ = _source_groups(obs_vault / "高中" / "数学", {".md"})
assert len(sub_groups) == 1 and [Path(name).resolve() for name, _ in sub_groups[0]["images"]] == [pasted_jpg.resolve(), wiki_png.resolve()]
# 3) 手动「导入笔记」单选该 .md（仅含一张 URL 编码图片）时，应把图片字节直接传给 AI
single_note = note_dir / "单图复数.md"
single_note.write_text("# 复数\n\n![](../../../%E9%99%84%E4%BB%B6/paste-a7b60f4d36daf0dae6041c0dee35a2ab9d804ec5.jpg)\n", encoding="utf-8")
manual_images = []
def manual_ai(prompt, *, image=None, **kwargs):
    manual_images.append(image)
    return json.dumps([{"kind": "tactical", "subject": "math", "title": "复数共轭与模长", "statement": "z*conj(z)=|z|^2"}])
preview_cards = import_previews(kb, manual_ai, [single_note])
assert len(preview_cards) == 1 and manual_images == [pasted_jpg.read_bytes()]

# 4) AI 返回含未转义 LaTeX 反斜杠与前后说明文字的 JSON 时，应能正常解析
from knowledge.kb_ai import json_result
latex_reply = '以下是提取结果：\n```json\n{"knowledge":true,"cards":[{"kind":"tactical","subject":"math","title":"共轭模长","statement":"$z \\cdot \\bar{z} = |z|^2$, \\frac{1}{z}=\\frac{\\bar{z}}{|z|^2}"}]}\n```'.replace("\\\\", "\\")
parsed_latex = json_result(latex_reply)
assert parsed_latex["knowledge"] is True and parsed_latex["cards"][0]["title"] == "共轭模长"

# 5) 订阅 CLI 传入多模态图片时：不把 base64 塞进命令行，落盘临时图片并用完即删（Codex 附带 -i）
import subprocess as _subprocess
from ai_assist import ai_cli, ai_http
cli_seen = []
def fake_cli_run(args, **kwargs):
    cli_seen.append(list(args))
    img_arg = args[args.index("-i") + 1]
    assert Path(img_arg).is_file() and Path(img_arg).read_bytes() == pasted_jpg.read_bytes()
    assert "base64," not in " ".join(args)
    return _subprocess.CompletedProcess(args, 0, '{"result":"ok","thread_id":"t1"}', "")

cli_profile = {"kind": "cli", "provider": "codex", "command": "codex", "model": "auto"}
cli_content = ai_http.image_content(cli_profile, pasted_jpg.read_bytes(), "image/jpeg", "提取知识")
with patch.object(ai_cli, "command_path", return_value="C:/fake/codex.exe"), \
        patch.object(ai_cli, "capabilities", return_value={"effort": False, "model": False}), \
        patch.object(ai_cli.subprocess, "run", side_effect=fake_cli_run):
    assert ai_cli.chat_messages(cli_profile, "sys", [{"role": "user", "content": cli_content}]) == "ok"
temp_img_arg = cli_seen[0][cli_seen[0].index("-i") + 1]
assert not Path(temp_img_arg).exists(), "临时图片目录应在 CLI 返回后清理"

# 6) Antigravity CLI 传图时应附带 --add-dir 与 --disable-slash-commands，且兼容 stdout 前置提示行
agy_seen = []
def fake_agy_run(args, **kwargs):
    agy_seen.append(list(args))
    assert "--add-dir" in args and "--disable-slash-commands" in args
    img_dir = Path(args[args.index("--add-dir") + 1])
    assert img_dir.is_dir() and any(img_dir.iterdir())
    return _subprocess.CompletedProcess(args, 0, 'warning: ignored\n{"status":"SUCCESS","response":"agy-ok"}', "")

agy_profile = {"kind": "cli", "provider": "antigravity", "command": "agy", "model": "auto"}
agy_content = ai_http.image_content(agy_profile, pasted_jpg.read_bytes(), "image/jpeg", "提取知识")
with patch.object(ai_cli, "command_path", return_value="C:/fake/agy.exe"), \
        patch.object(ai_cli, "capabilities", return_value={"effort": False, "model": False, "add_dir": True, "disable_slash": True}), \
        patch.object(ai_cli.subprocess, "run", side_effect=fake_agy_run):
    assert ai_cli.chat_messages(agy_profile, "sys", [{"role": "user", "content": agy_content}]) == "agy-ok"
assert not Path(agy_seen[0][agy_seen[0].index("--add-dir") + 1]).exists()

# 7) 深色 / 纯黑主题下 Treeview 与 Notebook 样式应跟随 TTK_COLORS，避免白底白字
import tkinter as _tk
from tkinter import ttk as _ttk
import config as _cfg
from app.app_window import WindowMixin
_root = _tk.Tk()
_root.withdraw()
try:
    _cfg.apply_theme("urban_will")
    _host = SimpleNamespace(root=_root, style=_ttk.Style(_root))
    _host.style.theme_use("clam")
    WindowMixin._theme_ttk(_host)
    assert _host.style.lookup("Treeview", "background") == _cfg.TTK_COLORS["list_bg"]
    assert _host.style.lookup("Treeview", "foreground") == _cfg.TTK_COLORS["list_fg"]
    assert _host.style.lookup("TNotebook.Tab", "background") == _cfg.TTK_COLORS["frame"]
finally:
    _root.destroy()

print("knowledge check OK")
