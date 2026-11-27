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
from knowledge.kb_auto_import import _anki_groups, _source_groups, auto_generate, clear_auto_progress, load_auto_progress


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
prog_events = []
auto_generate(kb, fake_ai, source, ["txt", "md", "image"], on_progress=lambda d, t: prog_events.append((d, t)))
assert prog_events[0] == (0, 4) and prog_events[-1] == (4, 4) and all(0 <= d <= t == 4 for d, t in prog_events)
assert any(image for _, image in created), "同名配套图片应与正文一起交给 AI"
assert any(card["title"] == "自动导入概念" for card in kb.conceptual_cards())
assert load_auto_progress(kb) is None, "全部完成后应自动清理进度文件"

# 断点保存与续传：中途暂停（或意外中断）后保留进度，再次调用跳过已完成组并累计统计，完成后自动清除
import threading
resume_dir = base / "resume_src"
resume_dir.mkdir()
(resume_dir / "a.txt").write_text("知识点A：正弦定理", encoding="utf-8")
(resume_dir / "b.txt").write_text("知识点B：余弦定理", encoding="utf-8")
(resume_dir / "c.txt").write_text("知识点C：中线长公式", encoding="utf-8")
seen_calls = []
cancel_ev = threading.Event()
def resumable_ai(prompt, *, image=None, **kwargs):
    seen_calls.append(prompt)
    if len(seen_calls) == 1:
        cancel_ev.set()  # 处理完第 1 组后暂停，模拟中途关闭/暂停
        return json.dumps(generated("续传概念A"))
    if "知识点B" in prompt:
        return json.dumps(generated("续传概念B"))
    return json.dumps(generated("续传概念C"))

paused_rep = auto_generate(kb, resumable_ai, resume_dir, ["txt"], cancel_event=cancel_ev)
assert paused_rep["paused"] is True and paused_rep["created"] == 1 and len(seen_calls) == 1, paused_rep
saved_prog = load_auto_progress(kb)
assert saved_prog is not None and saved_prog["done_files"] == 1 and saved_prog["total_files"] == 3, saved_prog
assert len(saved_prog["done_groups"]) == 1
# 恢复执行：应跳过已完成的 a.txt，只对 b.txt 和 c.txt 调用 AI，累计 created == 3，完成后清理进度
resumed_prog_events = []
final_rep = auto_generate(kb, resumable_ai, resume_dir, ["txt"], on_progress=lambda d, t: resumed_prog_events.append((d, t)))
assert final_rep["paused"] is False and final_rep["resumed"] == 1 and final_rep["created"] == 3, final_rep
assert len(seen_calls) == 3, f"不应重复处理已完成的组，实际调用 {len(seen_calls)} 次"
assert resumed_prog_events[0] == (1, 3) and resumed_prog_events[-1] == (3, 3), resumed_prog_events
assert load_auto_progress(kb) is None, "续传全部完成后应清理进度文件"

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

# 4) AI 返回含未转义 LaTeX 反斜杠（且混有已转义 \\cdot）与前后说明文字的 JSON 时，应能正常解析
from knowledge.kb_ai import json_result
latex_reply = '以下是提取结果：\n```json\n{"knowledge":true,"cards":[{"kind":"tactical","subject":"math","title":"共轭模长","statement":"$z \\\\cdot \\overline{z} = |z|^2$, \\sin\\alpha + \\\\cos\\beta"}]}\n```'
parsed_latex = json_result(latex_reply)
assert parsed_latex["knowledge"] is True and parsed_latex["cards"][0]["title"] == "共轭模长"
assert "\\cdot" in parsed_latex["cards"][0]["statement"] and "\\sin" in parsed_latex["cards"][0]["statement"]

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

# 8) 知识库初始化默认隐藏 Obsidian 笔记属性，不覆盖已有用户配置
obs_app_cfg = json.loads((kb.root / ".obsidian" / "app.json").read_text(encoding="utf-8"))
assert obs_app_cfg.get("propertiesInDocument") == "hidden"
(kb.root / ".obsidian" / "app.json").write_text(json.dumps({"propertiesInDocument": "source"}), encoding="utf-8")
KnowledgeBase(kb.root)
assert json.loads((kb.root / ".obsidian" / "app.json").read_text(encoding="utf-8"))["propertiesInDocument"] == "source"

# 9) 双击/查看已保存卡片优先直接跳转到 Obsidian，提案仍留在 OIT 内审核
opened_obs, reviewed, detailed = [], [], []
fake_page = SimpleNamespace(
    kb=kb, data_dir=str(base),
    lists={"tactical": SimpleNamespace(selection=lambda: (universal["id"],)),
           "proposals": SimpleNamespace(selection=lambda: ("pr_1",))},
    review=lambda key: reviewed.append(key),
    details=lambda kind: detailed.append(kind),
    show=lambda: None,
)
with patch("pages.knowledge_page.open_obsidian", side_effect=lambda p, d=None: (opened_obs.append(p) or True)):
    KnowledgePage.open_selected(fake_page, "tactical")
    KnowledgePage.open_selected(fake_page, "proposals")
assert opened_obs == [universal["file_path"]] and reviewed == ["pr_1"] and not detailed

# 10) 多选、全选/全不选、批量批准提案与批量删除卡片
pr_a = kb.propose("ADD_CARD", subject="math", title="批量技巧甲",
                  summary="这是一段纯自然语言审核说明，不含公式。",
                  proposed_card={"statement": "结论甲", "preconditions": "前提甲", "proof_sketch": "补证甲", "generality": "explain_first"})
pr_b = kb.propose("ADD_CARD", subject="math", title="批量技巧乙",
                  proposed_card={"statement": "结论乙", "preconditions": "前提乙", "proof_sketch": "补证乙", "generality": "universal"})
_root2 = _tk.Tk()
_root2.withdraw()
try:
    # 先验证 WebView2 模式下的状态输出与无 Tk 残留
    page_wv = KnowledgePage(_root2, str(base))
    page_wv.kb = kb
    st_wv = page_wv._build_web_state()
    assert not page_wv._tk_built, "WebView2 模式下不应残留旧 Tk 界面控件"
    assert any(x["id"] == pr_a["pr_id"] for x in st_wv["items"]["proposals"])
    page_wv.destroy()

    with patch("pages.knowledge_page.is_webview2_available", return_value=False):
        page2 = KnowledgePage(_root2, str(base))
    page2.kb = kb
    page2.show()
    page2.tabs.select(3)  # 待审提案
    page2.select_all("proposals")
    assert set(page2.lists["proposals"].selection()) >= {pr_a["pr_id"], pr_b["pr_id"]}
    page2.clear_selection("proposals")
    assert page2.lists["proposals"].selection() == ()
    page2.lists["proposals"].selection_set([pr_a["pr_id"], pr_b["pr_id"]])
    page2.approve_selected()
    assert not any(p["pr_id"] in {pr_a["pr_id"], pr_b["pr_id"]} for p in kb.proposals())
    merged_ids = [cid for cid, c in kb.cards.items() if c["title"] in {"批量技巧甲", "批量技巧乙"}]
    assert len(merged_ids) == 2
    page2.tabs.select(0)
    page2.lists["tactical"].selection_set(merged_ids)
    with patch("pages.knowledge_page.messagebox.askyesno", return_value=True):
        page2.delete_selected("tactical")
    assert all(cid not in kb.cards for cid in merged_ids)
    # 搜索：技巧库、概念库、待审提案均可按关键词/多词检索且不报 KeyError
    pr_search = kb.propose("ADD_CARD", subject="math", title="复数旋转",
                           summary="用复数乘法求旋转坐标",
                           proposed_card={"statement": "乘以单位复数", "preconditions": "前提", "proof_sketch": "补证", "generality": "explain_first"})
    page2.query.set("比例")
    page2.show()
    assert universal["id"] in page2.lists["tactical"].get_children()
    page2.query.set("复数 坐标")
    page2.show()
    assert pr_search["pr_id"] in page2.lists["proposals"].get_children()
    page2.query.set("")
    page2.show()
    page2.destroy()
finally:
    _root2.destroy()

# 11) 小写罗马数字序号未换行时自动补换行，且不破坏公式；Obsidian LaTeX 语法自动归一化（\(..\)/\[..\] -> $/$$）
from knowledge.kb_store import format_roman_lines, normalize_obsidian_math
assert format_roman_lines("i. 先配项 ii. 再取模；iii. 检查 $a\\ne 0$") == "i. 先配项\nii. 再取模；\niii. 检查 $a\\ne 0$"
raw_math = r"i. 圆轨道三式为 \(v=\sqrt{\frac{GM}{r}}\)、\( r\approx R \)，见 \[T=2\pi\sqrt{\frac{r^3}{GM}}\] 与 `$ keep $`"
norm_math = normalize_obsidian_math(raw_math)
assert r"$v=\sqrt{\frac{GM}{r}}$" in norm_math and r"$r\approx R$" in norm_math
assert "$$\nT=2\\pi\\sqrt{\\frac{r^3}{GM}}\n$$" in norm_math
assert "`$ keep $`" in norm_math and r"\(" not in norm_math and r"\[" not in norm_math
assert normalize_obsidian_math("价格 $100 到 $200 不变，公式 $ GM = gR^2 $ 规范化") == "价格 $100 到 $200 不变，公式 $GM = gR^2$ 规范化"

# 12) 生成卡片模型上下文按轮次与超时定时清理
cli_ctx_host = SimpleNamespace(
    comm=SimpleNamespace(
        ai=SimpleNamespace(task_profile=lambda *a, **k: {"kind": "cli", "provider": "antigravity", "command": "agy", "config_id": "agy"},
                           config_for=lambda _: "agy"),
        persona_name=lambda: "测试",
        chat_fn=lambda prof, *a, **k: (prof["_session_store"].setdefault(prof["_session_key"], {"id": f"s-{len(prof['_session_store'])+1}"}) and "ok"),
    )
)
for _ in range(_cfg.KB_AI_CONTEXT_MAX_TURNS):
    assert KnowledgePage.call_ai(cli_ctx_host, "task") == "ok"
assert cli_ctx_host._kb_ctx_turns == _cfg.KB_AI_CONTEXT_MAX_TURNS and cli_ctx_host._kb_sessions
cli_ctx_host._kb_sessions["kb:agy"]["marker"] = "old"
assert KnowledgePage.call_ai(cli_ctx_host, "task-next") == "ok"
assert "marker" not in cli_ctx_host._kb_sessions["kb:agy"] and cli_ctx_host._kb_ctx_turns == 1
cli_ctx_host._kb_sessions["kb:agy"]["marker"] = "stale"
cli_ctx_host._kb_ctx_started -= _cfg.KB_AI_CONTEXT_MAX_AGE + 1
assert KnowledgePage.call_ai(cli_ctx_host, "task-expired") == "ok"
assert "marker" not in cli_ctx_host._kb_sessions["kb:agy"] and cli_ctx_host._kb_ctx_turns == 1

# 13) 悬浮窗口右上角 '-' 最小化到左侧边栏，兼容同时最小化多个窗口并可恢复/销毁清理
from app.app_sidebar import SidebarMixin
from pages.widgets import Modal
_root3 = _tk.Tk()
_root3.geometry("400x300+32000+32000")
_root3.update_idletasks()
try:
    class _FakeSidebar(SidebarMixin):
        def __init__(self, r):
            self.root = r
            self.sidebar = _tk.Frame(r)
            self.sidebar.pack(side="left", fill="y")
            self._init_minimized_dock()
    dock = _FakeSidebar(_root3)
    m1 = Modal(_root3, "自动生成", 320, 200)
    m2 = Modal(_root3, "审核提案", 320, 200)
    assert hasattr(m1, "minimize_button") and m1.minimize_button.cget("text") == "收起"
    m1.minimize()
    m2.minimize()
    assert len(dock._minimized_windows) == 2
    assert m1.state() == "withdrawn" and m2.state() == "withdrawn"
    m1.restore()
    assert len(dock._minimized_windows) == 1 and dock._minimized_windows[0][0] is m2
    assert m1.state() == "normal"
    m2.destroy()
    assert len(dock._minimized_windows) == 0
    m1.destroy()
finally:
    _root3.destroy()

# 14) 提醒库：直接保存、解题命中顺带提醒、触发后进入动态冷却、置顶或重置后重新触发
rem = kb.save_card({"kind": "reminder", "subject": "math", "title": "分母含参判零",
                    "triggers": ["分母含参", "分式方程"], "body": "i. 先讨论分母是否为 0\nii. 检验增根"})
assert rem["id"].startswith("rem_math_") and len(kb.reminder_cards()) == 1
rem_hits = kb.search_reminders("math", "解一个分母含参的分式方程", 3)
assert len(rem_hits) == 1 and rem_hits[0]["card_id"] == rem["id"]
task_with_rem, _ = prepare_subject(kb, classifier, "math", "解一个分母含参的分式方程")
assert "[个人易错提醒]" in task_with_rem and "分母含参判零" in task_with_rem
# 刚提醒过，处于冷却期内不再重复打扰
assert kb.search_reminders("math", "解一个分母含参的分式方程", 3) == []
# 置顶或重置提醒频率后可立即再次命中
kb.set_stats(rem["id"], pinned=True)
assert len(kb.search_reminders("math", "解一个分母含参的分式方程", 3)) == 1
kb.set_stats(rem["id"], pinned=False, reset_reminder=True)
assert len(kb.search_reminders("math", "解一个分母含参的分式方程", 3)) == 1

# 15) 卡片按 category 分类存放，并在解题检索时按分类缩小候选范围
cat_complex = kb.save_card({"kind": "tactical", "subject": "math", "category": "复数", "title": "复数模长",
                            "generality": "universal", "triggers": ["旋转", "模长"],
                            "statement": "乘单位复数保模长", "preconditions": "复平面", "proof_sketch": "直接取模"})
cat_geo = kb.save_card({"kind": "tactical", "subject": "math", "category": "立体几何", "title": "空间旋转",
                        "generality": "universal", "triggers": ["旋转", "模长"],
                        "statement": "空间旋转体体积", "preconditions": "轴对称", "proof_sketch": "积分"})
assert Path(cat_complex["file_path"]).parent.name == "复数"
scoped_hits = kb.search_tactical("math", "旋转 模长", 4, categories=["复数"])
scoped_ids = [h["card_id"] for h in scoped_hits]
assert cat_complex["id"] in scoped_ids and cat_geo["id"] not in scoped_ids
task_cat, sel_cat = prepare_subject(kb, lambda *a, **k: '{"problem_mode":"requires_working","categories":["复数"]}',
                                    "math", "求复数旋转后的模长")
assert "命中知识分类: 复数" in task_cat
assert cat_complex["id"] in [h["card_id"] for h in sel_cat] and cat_geo["id"] not in [h["card_id"] for h in sel_cat]

# 16) 强化重复检测与「以补充为主，新增为辅」：相近标题/后缀自动转为补充提案、待审提案自动折叠、合并保留原字段
pol = kb.save_card({"kind": "tactical", "subject": "math", "category": "平面向量", "title": "极化恒等式",
                    "generality": "explain_first", "triggers": ["向量点积", "中点"],
                    "statement": "a·b = |OM|^2 - |AB|^2/4", "preconditions": "M 为 AB 中点", "proof_sketch": "展开平方差"})
pr_dup = kb.propose("ADD_CARD", subject="math", title="极化恒等式结论",
                    proposed_card={"triggers": ["三角形中线点积"], "preconditions": "需先确定中点位置",
                                   "statement": "a·b = |OM|^2 - |AB|^2/4"},
                    summary="补充中线点积触发场景与前提说明")
assert pr_dup["action"] == "UPDATE_CARD" and pr_dup["target_card_id"] == pol["id"], pr_dup
assert "极化恒等式结论" in pr_dup["proposed_card"].get("aliases", [])
merged_pol = kb.merge(pr_dup["pr_id"], maintainer=True)
assert "向量点积" in merged_pol["triggers"] and "三角形中线点积" in merged_pol["triggers"]
assert "M 为 AB 中点" in merged_pol["preconditions"] and "需先确定中点位置" in merged_pol["preconditions"]
# 两条相近的待审提案自动折叠为一条
pr_open1 = kb.propose("ADD_CARD", subject="physics", title="等效电源法",
                      proposed_card={"triggers": ["闭合电路", "电源外接"], "statement": "把部分电路等效为新电源"})
pr_open2 = kb.propose("ADD_CARD", subject="physics", title="等效电源法技巧",
                      proposed_card={"triggers": ["戴维南等效"], "statement": "用开路电压和等效内阻替代"})
assert pr_open1["pr_id"] == pr_open2["pr_id"] and pr_open2["squash_count"] >= 2, (pr_open1, pr_open2)
assert "等效电源法技巧" in pr_open2["proposed_card"].get("aliases", [])

# 17) 批量自动生成过程中：同技巧再次出现时合并补充到未审批提案，不生成第二张重复提案
batch_dir = base / "batch_merge_src"
batch_dir.mkdir()
(batch_dir / "1_first.txt").write_text("来源一：拉密定理基础结论", encoding="utf-8")
(batch_dir / "2_second.txt").write_text("来源二：拉密定理补充触发词与前提", encoding="utf-8")
(batch_dir / "3_third.txt").write_text("来源三：通过待审ID补充拉密定理例题", encoding="utf-8")
(batch_dir / "4_dup.txt").write_text("来源四：完全重复无新信息", encoding="utf-8")

def batch_merge_ai(prompt, *, image=None, **kwargs):
    if "来源一" in prompt:
        return json.dumps({"knowledge": True, "cards": [{
            "kind": "tactical", "subject": "physics", "category": "力学与牛顿定律",
            "title": "拉密定理", "triggers": ["三力平衡", "共点力"],
            "statement": "三力平衡时力与对角正弦成正比", "preconditions": "三力共点平衡",
            "proof_sketch": "构成封闭矢量三角形后用正弦定理", "generality": "explain_first",
            "review_summary": "三力平衡快速列比例",
        }]})
    if "来源二" in prompt:
        assert "(待审)" in prompt and "拉密定理" in prompt, "待审提案应出现在目录提示中"
        return json.dumps({"knowledge": True, "cards": [{
            "kind": "tactical", "subject": "physics", "category": "",
            "title": "拉密定理技巧", "triggers": ["静态平衡求张力"],
            "statement": "动态平衡中角度变化时可快速判断力的增减",
            "preconditions": "仅适用于三力汇交", "generality": "explain_first",
        }]})
    if "来源三" in prompt:
        target_pr = next(p for p in kb.proposals() if p["title"] == "拉密定理")
        assert target_pr["pr_id"] in prompt, "目录提示应包含待审提案ID以供补充"
        return json.dumps({"knowledge": True, "cards": [{
            "kind": "tactical", "subject": "physics", "title": "三力正弦比例法",
            "target_card_id": target_pr["pr_id"], "triggers": ["轻绳挂重物"],
            "examples": ["双绳悬挂小球求拉力变化"], "generality": "explain_first",
        }]})
    return json.dumps({"knowledge": True, "cards": [{
        "kind": "tactical", "subject": "physics", "title": "拉密定理",
        "triggers": ["三力平衡"], "statement": "三力平衡时力与对角正弦成正比",
        "preconditions": "三力共点平衡", "generality": "explain_first",
    }]})

batch_rep = auto_generate(kb, batch_merge_ai, batch_dir, ["txt"])
lami_prs = [p for p in kb.proposals() if p["subject"] == "physics" and "拉密" in p["title"]]
assert len(lami_prs) == 1, f"不应生成重复待审提案，实际: {lami_prs}"
lami_pr = lami_prs[0]
assert lami_pr["squash_count"] == 3, lami_pr
assert lami_pr["proposed_card"].get("category") == "力学与牛顿定律", "空分类不应覆盖已有分类"
assert {"三力平衡", "静态平衡求张力", "轻绳挂重物"} <= set(lami_pr["proposed_card"].get("triggers", []))
assert {"拉密定理技巧", "三力正弦比例法"} <= set(lami_pr["proposed_card"].get("aliases", []))
assert "动态平衡中角度变化时可快速判断力的增减" in lami_pr["proposed_card"].get("statement", "")
assert "双绳悬挂小球求拉力变化" in lami_pr["proposed_card"].get("examples", [])
assert lami_pr["summary"] == "三力平衡快速列比例", "空摘要不应覆盖原有摘要"
assert batch_rep["duplicates"] >= 2, batch_rep

# 18) 纯文本分段卡片格式（零 JSON 转义，含裸双引号、LaTeX 反斜杠、多行公式、截断容错）与格式异常重试
from knowledge.kb_ai import parse_cards_result, parse_review_result
plain_block = """
===CARD===
kind: tactical
subject: physics
category: 动量与碰撞
title: 整体法动量守恒
topic: 动量守恒
generality: explain_first
aliases: "孤立系统"整体法 | 系统动量法
triggers:
- 两物块碰撞
- 光滑水平面
- "合外力为零"
statement:
把"孤立系统"看作整体，满足 $\\sum \\vec{F}_{外} = 0$ 时：
$$m_1 v_1 + m_2 v_2 = m_1 v_1' + m_2 v_2'$$
preconditions:
i. 系统所受合外力为零
ii. 或某方向外力远小于内力
proof_sketch:
由牛顿第三定律，内力冲量和为零。
examples:
- 子弹打入木块求共同速度
review_summary:
多个物体相互作用且系统合外力为零时，直接对整体列动量守恒方程。
===END===
===CARD===
kind: conceptual
subject: math
title: 未闭合截断卡片
body:
写到一半被截断了也没有 ===END===，但内容含 $\\frac{1}{2}$ 和 "引号"
"""
parsed_blocks = parse_cards_result(plain_block)
assert len(parsed_blocks) == 2, parsed_blocks
assert parsed_blocks[0]["title"] == "整体法动量守恒"
assert '"孤立系统"' in parsed_blocks[0]["statement"] and "\\sum \\vec{F}_{外}" in parsed_blocks[0]["statement"]
assert parsed_blocks[0]["triggers"] == ["两物块碰撞", "光滑水平面", "合外力为零"]
assert parsed_blocks[1]["title"] == "未闭合截断卡片" and "\\frac{1}{2}" in parsed_blocks[1]["body"]
assert parse_cards_result("===NONE===") == []

plain_review = """
used_card_ids: card_1 | card_2
failed_card_ids: card_3
===PROPOSAL===
action: ADD_CARD
subject: math
title: 切线放缩
category: 函数与导数
generality: explain_first
triggers:
- 指数不等式
- 含 e^x 恒成立
statement:
利用 $e^x \\ge x + 1$ 放缩。
summary:
遇到含指数的不等式证明时可用切线放缩简化。
===END===
"""
parsed_rev = parse_review_result(plain_review)
assert parsed_rev["used_card_ids"] == ["card_1", "card_2"] and parsed_rev["failed_card_ids"] == ["card_3"]
assert len(parsed_rev["proposals"]) == 1 and parsed_rev["proposals"][0]["title"] == "切线放缩"

retry_dir = base / "retry_plain_src"
retry_dir.mkdir()
(retry_dir / "broken.txt").write_text("需要重试的笔记内容", encoding="utf-8")
retry_calls = []
def retry_ai(prompt, *, image=None, **kwargs):
    retry_calls.append(prompt)
    if len(retry_calls) == 1:
        return "抱歉，这是一段未按格式输出的纯文字说明。"
    assert "===CARD===" in prompt
    return "===CARD===\nkind: conceptual\nsubject: math\ntitle: 重试成功概念\nbody:\n整理后的定义。\n===END==="

retry_rep = auto_generate(kb, retry_ai, retry_dir, ["txt"])
assert retry_rep["errors"] == 0 and retry_rep["created"] == 1 and len(retry_calls) == 2, (retry_rep, len(retry_calls))

# 自动生成时 AI 返回无效/跨学科/概念卡/待审提案 parent_ids 应自动过滤，仅保留同学科技巧卡 ID，不得导致整组报错
valid_math_parent = next(cid for cid, c in kb.cards.items() if c.get("subject") == "math")
other_subject_parent = next((cid for cid, c in kb.cards.items() if c.get("subject") != "math"), "nonexistent_other")
concept_parent = kb.conceptual_cards()[0]["id"]
open_pr_parent = kb.proposals()[0]["pr_id"]
parent_dir = base / "bad_parent_src"
parent_dir.mkdir()
(parent_dir / "parents.txt").write_text("带非法父结论 ID 的自动导入技巧笔记", encoding="utf-8")
def bad_parent_ai(prompt, *, image=None, **kwargs):
    return (
        "===CARD===\n"
        "kind: tactical\n"
        "subject: math\n"
        "title: 过滤父结论测试技巧\n"
        "category: 代数与不等式\n"
        "triggers: 父结论过滤测试 | 自动导入容错\n"
        f"parent_ids: {valid_math_parent} | {other_subject_parent} | {concept_parent} | {open_pr_parent} | 编造标题\n"
        "statement: 测试非法 parent_ids 不会导致自动生成失败。\n"
        "preconditions: 仅用于单测。\n"
        "proof_sketch: 略。\n"
        "generality: explain_first\n"
        "review_summary: 验证父结论清洗逻辑。\n"
        "===END==="
    )

parent_rep = auto_generate(kb, bad_parent_ai, parent_dir, ["txt"])
assert parent_rep["errors"] == 0 and parent_rep["proposals"] == 1, parent_rep
filtered_pr = next(p for p in kb.proposals() if p.get("title") == "过滤父结论测试技巧")
assert filtered_pr["parent_ids"] == [valid_math_parent], filtered_pr["parent_ids"]
assert filtered_pr["proposed_card"].get("parent_ids") == [valid_math_parent], filtered_pr["proposed_card"]

print("knowledge check OK")
