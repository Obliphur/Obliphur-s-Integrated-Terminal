"""Desktop knowledge browser, reviewed editing/import and reusable citation dialog."""
import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
import config
import toast
from config import COLORS
from i18n import tr
from knowledge import get_knowledge, select_vault
from knowledge.kb_ai import complete_card, import_previews
from knowledge.kb_store import read_json
from .widgets import Modal, ScrollArea, wrap_label

LABELS = {"title": "标题", "topic": "主题", "aliases": "别名", "triggers": "触发特征",
          "parent_ids": "父结论 ID", "statement": "结论本体", "preconditions": "适用前提与禁区",
          "proof_sketch": "大题补证提示", "examples": "典型例题", "body": "正文"}


def open_obsidian(path, data_dir=None):
    import external_software
    try:
        external_software.manager(data_dir or config.app_data_dir()).open_obsidian(path)
    except (OSError, ValueError) as error:
        toast.warn(str(error))


def modal(parent, title, width=680, height=650):
    root = parent.winfo_toplevel()
    return Modal(root, tr(title), width=min(config.scaled(width), root.winfo_screenwidth()-60),
                 height=min(config.scaled(height), root.winfo_screenheight()-100))


def show_trace(parent, card_id, kb=None, on_open=None, data_dir=None):
    kb = kb or get_knowledge()
    item = kb.resolve_trace(card_id)
    if not item:
        return
    dlg = modal(parent, "结论溯源")
    area = ScrollArea(dlg.body, bg=COLORS["bg"])
    area.pack(fill="both", expand=True)
    body = area.inner
    tk.Label(body, text=item["title"], bg=COLORS["bg"], fg=COLORS["text"], font=config.font("h2", bold=True)).pack(anchor="w")
    state = {"unfamiliar": "未用过", "learning": "刚接触", "familiar": "已熟悉"}[item["familiarity"]]
    tk.Label(body, text=tr(state), bg=COLORS["bg"], fg=COLORS["accent"], font=config.font("body")).pack(anchor="w")
    for key in ("statement", "preconditions", "proof_sketch"):
        tk.Label(body, text=tr(LABELS[key]), bg=COLORS["bg"], fg=COLORS["warn"] if key == "preconditions" else COLORS["text"], font=config.font("body", bold=True)).pack(anchor="w", pady=(12, 4))
        label = tk.Label(body, text=item[key], bg=COLORS["bg"], fg=COLORS["text"], justify="left", anchor="w", font=config.font("body"))
        label.pack(fill="x")
        wrap_label(label)
    for ancestor in item["lineage"]:
        ttk.Button(body, text="← " + ancestor["title"], command=lambda key=ancestor["card_id"]: show_trace(dlg, key, kb, on_open, data_dir)).pack(anchor="w", pady=3)
    children = [c for c in kb.cards.values() if card_id in c.get("parent_ids", [])]
    for child in children:
        ttk.Button(body, text="→ " + child["title"], command=lambda key=child["id"]: show_trace(dlg, key, kb, on_open, data_dir)).pack(anchor="w", pady=3)
    if item.get("file_path"):
        ttk.Button(dlg.buttons, text=tr("在 Obsidian 中打开"), command=lambda: open_obsidian(item["file_path"], data_dir)).pack(anchor="w", pady=3)
    if on_open:
        ttk.Button(dlg.buttons, text=tr("在 OIT 知识库打开"), command=lambda: (dlg.destroy(), on_open(card_id))).pack(anchor="w", pady=3)
    area.bind_children()


class KnowledgePage(tk.Frame):
    def __init__(self, master, data_dir, comm=None, show_page=None):
        super().__init__(master, bg=COLORS["bg"])
        self.data_dir, self.comm, self.show_page = data_dir, comm, show_page
        self.kb = get_knowledge(data_dir)
        self._jobs = queue.Queue()
        self._poll_job = self.after(100, self._poll)
        self.bind("<Destroy>", self._destroy, add="+")
        self.subject, self.query, self.sort = tk.StringVar(value=tr("全部")), tk.StringVar(), tk.StringVar(value=tr("综合权重"))
        tk.Label(self, text=tr("📚  知识库"), bg=COLORS["bg"], fg=COLORS["text"], font=config.font("h1", bold=True)).pack(anchor="w", padx=18, pady=12)
        tools = tk.Frame(self, bg=COLORS["bg"])
        tools.pack(fill="x", padx=16)
        for i, (label, fn) in enumerate((("在 Obsidian 中打开", lambda: open_obsidian(self.kb.root, self.data_dir)), ("新建卡片", self.new_card),
                                         ("导入笔记", self.import_notes), ("刷新索引", self.show), ("知识库设置", self.settings))):
            ttk.Button(tools, text=tr(label), command=fn).grid(row=i//3, column=i%3, sticky="ew", padx=2, pady=3)
        for col in range(3):
            tools.columnconfigure(col, weight=1)
        mode_row = tk.Frame(self, bg=COLORS["bg"])
        mode_row.pack(fill="x", padx=18, pady=4)
        self.mode = tk.StringVar(value=self.kb.config.get("solver_mode", "exploration"))
        for value, label in (("exploration", "探索模式"), ("conservative", "保守模式")):
            ttk.Radiobutton(mode_row, text=tr(label), variable=self.mode, value=value,
                            command=lambda: self.kb.configure(solver_mode=self.mode.get())).pack(side="left", padx=(0, 12))
        filters = tk.Frame(self, bg=COLORS["bg"])
        filters.pack(fill="x", padx=18, pady=6)
        self.subject_box = ttk.Combobox(filters, textvariable=self.subject, state="readonly", width=9)
        self.subject_box.pack(side="left")
        self.subject_box.bind("<<ComboboxSelected>>", lambda _: self.show())
        search = ttk.Entry(filters, textvariable=self.query)
        search.pack(side="left", fill="x", expand=True, padx=4)
        search.bind("<Return>", lambda _: self.show())
        self.sort_box = ttk.Combobox(self, textvariable=self.sort, state="readonly", values=[tr(x) for x in ("综合权重", "频次", "最近使用", "层级视图")])
        self.sort_box.pack(fill="x", padx=18, pady=3)
        self.sort_box.bind("<<ComboboxSelected>>", lambda _: self.show())
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=8)
        self.lists = {}
        for key, label in (("tactical", "技巧库"), ("conceptual", "概念库"), ("proposals", "待审提案")):
            frame = tk.Frame(self.tabs, bg=COLORS["bg"])
            self.tabs.add(frame, text=tr(label))
            if key == "conceptual":
                note = tk.Label(frame, text=tr("概念性笔记仅供阅读与复习，AI 解题不会读取，也无权重排序"), bg=COLORS["bg"], fg=COLORS["text_dim"], justify="left", font=config.font("body"))
                note.pack(fill="x", pady=6)
                wrap_label(note)
            tree = ttk.Treeview(frame, columns=("info",), show="tree headings", selectmode="browse")
            tree.heading("#0", text=tr("标题"))
            tree.heading("info", text=tr("学科 / 统计"))
            tree.column("#0", width=240, minwidth=100)
            tree.column("info", width=155, minwidth=100)
            scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            scroll.pack(side="right", fill="y")
            tree.configure(yscrollcommand=scroll.set)
            tree.pack(fill="both", expand=True)
            tree.bind("<Double-1>", lambda e, k=key: self.details(k))
            tree.bind("<Return>", lambda e, k=key: self.details(k))
            self.lists[key] = tree
        ttk.Button(self, text=tr("查看所选卡片或提案"), command=lambda: self.details(self.active_tab())).pack(anchor="w", padx=18, pady=(0, 10))
        self.show()

    def _destroy(self, event):
        if event.widget is self:
            self.after_cancel(self._poll_job)

    def _poll(self):
        while not self._jobs.empty():
            fn, value, error = self._jobs.get_nowait()
            if error:
                toast.warn(str(error))
            else:
                try:
                    fn(value)
                except (ValueError, OSError, tk.TclError) as error:
                    toast.warn(tr(str(error)))
        self._poll_job = self.after(100, self._poll)

    def background(self, work, done):
        def run():
            try:
                self._jobs.put((done, work(), None))
            except Exception as error:
                self._jobs.put((done, None, error))
        threading.Thread(target=run, daemon=True).start()

    def call_ai(self, prompt, *, image=None, timeout=90):
        if not self.comm:
            raise ValueError(tr("请先配置 AI 模型"))
        # Independent invocation: editing notes never contaminates solver session history.
        from ai_assist.ai_subagent import invoke
        profile = self.comm.ai.task_profile("subject", fallback=self.comm.ai.config_for(self.comm.persona_name()))
        return invoke(profile, "subject", prompt, image=image, max_tokens=2400, temperature=.1, timeout=timeout, chat=self.comm.chat_fn)

    def active_tab(self):
        return list(self.lists)[self.tabs.index("current")]

    def show(self):
        self.kb.refresh()
        if hasattr(self, "mode"):
            self.mode.set(self.kb.config.get("solver_mode", "exploration"))
        self.subject_box.configure(values=[tr("全部"), *[tr(v) for v in self.kb.subjects.values()]])
        domain = next((k for k, v in self.kb.subjects.items() if tr(v) == self.subject.get()), None)
        query = self.query.get().strip()
        stats = read_json(self.kb.internal / "stats.json")
        concepts = self.kb.conceptual_cards()
        cards = list(self.kb.cards.values())
        if query:
            ranked = [c for d in ([domain] if domain else self.kb.subjects) for c in self.kb.search_tactical(d, query, 20)]
            cards = ranked
        mode = self.sort.get()
        if mode == tr("频次"):
            cards.sort(key=lambda c: -stats.get(c["id"], {}).get("freq", 0))
        elif mode == tr("最近使用"):
            cards.sort(key=lambda c: stats.get(c["id"], {}).get("last_used", ""), reverse=True)
        elif mode == tr("层级视图"):
            from knowledge.kb_rank import depth
            cards.sort(key=lambda c: (depth(self.kb.cards, c["id"]), c.get("title", "")))
        elif not query:
            cards.sort(key=lambda c: (-stats.get(c["id"], {}).get("weight", 1), c.get("title", "")))
        for key, entries in (("tactical", cards), ("conceptual", concepts), ("proposals", self.kb.proposals())):
            tree = self.lists[key]
            selected = tree.selection()
            tree.delete(*tree.get_children())
            for card in entries:
                if domain and card.get("subject") != domain:
                    continue
                if key != "tactical" and query and query.lower() not in (card.get("title", "") + card.get("body", "")).lower():
                    continue
                ident = card.get("pr_id") or card["id"]
                info = tr(self.kb.subjects.get(card.get("subject"), card.get("subject", "")))
                st = stats.get(ident, {})
                title = card.get("title", "")
                if key == "tactical":
                    info += f"  ×{st.get('freq', 0)}  w={st.get('weight', 1):.2f}"
                    title = ("★ " if st.get("user_pinned") else "") + title
                    if mode == tr("层级视图"):
                        title = "  " * min(len(self.kb.ancestor_ids(ident)), 8) + title
                elif key == "proposals":
                    info += "  " + card["action"] + f" ×{card['squash_count']}"
                tree.insert("", "end", iid=ident, text=title, values=(info,))
            if selected and tree.exists(selected[0]):
                tree.selection_set(selected[0])
        count = len(self.kb.proposals())
        self.tabs.tab(2, text=tr("待审提案") + f" ({count})")

    def open_card(self, card_id):
        if self.show_page:
            self.show_page("knowledge")
        self.tabs.select(0)
        self.query.set("")
        self.subject.set(tr("全部"))
        self.show()
        if self.lists["tactical"].exists(card_id):
            self.lists["tactical"].selection_set(card_id)
            self.lists["tactical"].see(card_id)
            self.details("tactical")

    def details(self, kind):
        selected = self.lists[kind].selection()
        if not selected:
            return
        key = selected[0]
        if kind == "proposals":
            return self.review(key)
        card = self.kb.cards.get(key) if kind == "tactical" else next((c for c in self.kb.conceptual_cards() if c["id"] == key), None)
        if not card:
            return self.show()
        dlg = modal(self, "卡片详情")
        area = ScrollArea(dlg.body, bg=COLORS["bg"])
        area.pack(fill="both", expand=True)
        preview = tk.Text(area.inner, wrap="word", height=22, bg=COLORS["bg_card"], fg=COLORS["text"], font=config.font("body"))
        preview.pack(fill="both", expand=True)
        preview.insert("end", card["title"] + "\n\n" + card["body"])
        preview.tag_configure("warning", foreground=COLORS["warn"])
        start = preview.search("## 适用前提与禁区", "1.0", stopindex="end")
        if start:
            end = preview.search("\n## ", start + "+1line", stopindex="end") or "end"
            preview.tag_add("warning", start, end)
        preview.configure(state="disabled")
        actions = tk.Frame(area.inner, bg=COLORS["bg"])
        actions.pack(fill="x", pady=8)
        for label, fn in (("在 Obsidian 中打开", lambda: open_obsidian(card["file_path"], self.data_dir)),
                          ("编辑卡片", lambda: self.edit(card)), ("删除卡片", lambda: self.delete(card, dlg))):
            ttk.Button(actions, text=tr(label), command=fn).pack(anchor="w", pady=2)
        if kind == "tactical":
            for label, fn in (("结论溯源", lambda: show_trace(dlg, key, self.kb, self.open_card, self.data_dir)),
                              ("置顶 / 取消置顶", lambda: self.pin(key)), ("调整权重", lambda: self.weight(key))):
                ttk.Button(actions, text=tr(label), command=fn).pack(anchor="w", pady=2)
        else:
            ttk.Button(actions, text=tr("AI 补齐"), command=lambda: self.edit(card, complete=True)).pack(anchor="w", pady=2)
        area.bind_children()

    def pin(self, key):
        pinned = read_json(self.kb.internal / "stats.json").get(key, {}).get("user_pinned", False)
        self.kb.set_stats(key, pinned=not pinned)
        self.show()

    def weight(self, key):
        value = simpledialog.askfloat(tr("调整权重"), tr("权重（0.3–3.0）"), parent=self, minvalue=.3, maxvalue=3)
        if value is not None:
            self.kb.set_stats(key, weight=value)
            self.show()

    def delete(self, card, dlg):
        if messagebox.askyesno(tr("删除卡片"), card["title"], parent=dlg):
            self.kb.delete_card(card["id"], maintainer=True)
            dlg.destroy()
            self.show()

    def new_card(self):
        self.edit({"kind": "tactical", "subject": "math", "title": ""})

    def edit(self, card, complete=False, on_save=None):
        dlg = modal(self, "编辑卡片", height=730)
        area = ScrollArea(dlg.body, bg=COLORS["bg"])
        area.pack(fill="both", expand=True)
        fields, image = {}, {"data": None}
        kind = tk.StringVar(value=tr("概念性") if card.get("kind") == "conceptual" else tr("技巧性"))
        subject = tk.StringVar(value=tr(self.kb.subjects.get(card.get("subject"), "数学")))
        for var, values in ((kind, [tr("技巧性"), tr("概念性")]), (subject, [tr(v) for v in self.kb.subjects.values()])):
            ttk.Combobox(area.inner, textvariable=var, values=values, state="readonly").pack(fill="x", pady=3)
        for key, label in LABELS.items():
            tk.Label(area.inner, text=tr(label), bg=COLORS["bg"], fg=COLORS["text"], font=config.font("body")).pack(anchor="w", pady=(6, 2))
            widget = tk.Text(area.inner, height=2 if key in ("title", "topic", "aliases", "parent_ids") else 4, wrap="word", font=config.font("body"), bg=COLORS["bg_card"], fg=COLORS["text"])
            widget.pack(fill="x")
            value = card.get(key, "")
            widget.insert("1.0", "\n".join(value) if isinstance(value, list) else str(value))
            fields[key] = widget
        def value():
            result = {**card, "kind": "conceptual" if kind.get() == tr("概念性") else "tactical",
                      "subject": next(k for k, v in self.kb.subjects.items() if tr(v) == subject.get())}
            for key, widget in fields.items():
                text = widget.get("1.0", "end-1c")
                result[key] = [x.strip() for x in text.splitlines() if x.strip()] if key in ("aliases", "triggers", "parent_ids") else text
            # Tactical structured fields update existing body, preserving custom sections.
            return result
        def save():
            try:
                item = value()
                if on_save:
                    on_save(item)
                else:
                    self.kb.save_card(item, maintainer=True)
                dlg.destroy()
                self.show()
            except (ValueError, OSError, PermissionError) as error:
                toast.warn(tr(str(error)))
        def choose_image():
            path = filedialog.askopenfilename(parent=dlg, filetypes=[(tr("图片"), "*.png *.jpg *.jpeg *.webp *.gif")])
            if path:
                load_image(path)
        def load_image(path):
            import ai_assist
            if Path(path).stat().st_size > ai_assist.OCR_MAX_BYTES:
                toast.warn(tr("图片太大了 (最大 4.5 MB)"))
                return
            data = Path(path).read_bytes()
            if ai_assist.image_mime(data):
                image["data"] = data
                image_label.configure(text=Path(path).name)
        def paste(_event=None):
            import avatar as avatar_mod
            import tempstore
            paths = avatar_mod.clipboard_paths()
            path = next((p for p in paths if Path(p).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif")), None)
            if not path:
                fd, path = tempstore.mkstemp("misc", prefix="kb-paste-", suffix=".png")
                os.close(fd)
                os.unlink(path)
                if not avatar_mod.clipboard_image(path):
                    return None
            load_image(path)
            return "break"
        def fill():
            item, data = value(), image["data"]
            kb = self.kb
            ai_button.state(["disabled"])
            def done(result):
                if not dlg.winfo_exists():
                    return
                for key, widget in fields.items():
                    if key in result:
                        widget.delete("1.0", "end")
                        widget.insert("1.0", "\n".join(result[key]) if isinstance(result[key], list) else str(result[key]))
                ai_button.state(["!disabled"])
            def work():
                try:
                    return complete_card(kb, self.call_ai, item, data)
                except Exception:
                    self._jobs.put((lambda _: ai_button.state(["!disabled"]) if dlg.winfo_exists() else None, None, None))
                    raise
            self.background(work, done)
        actions = tk.Frame(area.inner, bg=COLORS["bg"])
        actions.pack(fill="x", pady=8)
        image_label = tk.Label(actions, text="", bg=COLORS["bg"], fg=COLORS["text_dim"])
        image_label.pack(anchor="w")
        ttk.Button(actions, text=tr("选择图片"), command=choose_image).pack(anchor="w", pady=2)
        ai_button = ttk.Button(actions, text=tr("AI 补齐 / 图片识别"), command=fill)
        ai_button.pack(anchor="w", pady=2)
        ttk.Button(actions, text=tr("确认保存"), command=save).pack(anchor="e", pady=2)
        for widget in fields.values():
            widget.bind("<<Paste>>", paste)
        area.bind_children()
        if complete:
            fill()

    def review(self, key):
        pr = self.kb._proposal(key)
        if not pr:
            return
        dlg = modal(self, "审核提案")
        text = tk.Text(dlg.body, wrap="word", bg=COLORS["bg_card"], fg=COLORS["text"], font=config.font("body"))
        text.pack(fill="both", expand=True)
        text.tag_configure("add", foreground=COLORS["success"])
        text.tag_configure("remove", foreground=COLORS["warn"])
        for line in self.kb.proposal_diff(key):
            text.insert("end", line, "add" if line.startswith("+") else "remove" if line.startswith("-") else "")
        text.insert("end", "\n" + json.dumps(pr.get("evidence", []), ensure_ascii=False, indent=2))
        text.configure(state="disabled")
        def apply(edits=None):
            try:
                self.kb.merge(key, maintainer=True, edits=edits)
                dlg.destroy()
                self.show()
            except (ValueError, OSError) as error:
                toast.warn(tr(str(error)))
        def reject():
            self.kb.reject(key, maintainer=True)
            dlg.destroy()
            self.show()
        actions = tk.Frame(dlg.body, bg=COLORS["bg"])
        actions.pack(fill="x")
        ttk.Button(actions, text=tr("合入主库"), command=apply).pack(side="left", padx=3)
        ttk.Button(actions, text=tr("编辑后合入"), command=lambda: self.edit({**pr["proposed_card"], "title": pr["title"], "kind": "tactical", "subject": pr["subject"]}, on_save=apply)).pack(side="left", padx=3)
        ttk.Button(actions, text=tr("拒绝提案"), command=reject).pack(side="left", padx=3)

    def import_notes(self):
        dlg = modal(self, "导入笔记", height=250)
        def choose(folder=False):
            paths = [filedialog.askdirectory(parent=dlg)] if folder else filedialog.askopenfilenames(parent=dlg, filetypes=[(tr("笔记"), "*.md *.txt")])
            paths = [p for p in paths if p]
            if paths:
                dlg.destroy()
                kb = self.kb
                self.background(lambda: import_previews(kb, self.call_ai, paths), self.import_preview)
        ttk.Button(dlg.body, text=tr("选择文件"), command=choose).pack(fill="x", pady=6)
        ttk.Button(dlg.body, text=tr("选择文件夹"), command=lambda: choose(True)).pack(fill="x", pady=6)

    def import_preview(self, cards):
        dlg = modal(self, "导入预览")
        area = ScrollArea(dlg.body, bg=COLORS["bg"])
        area.pack(fill="both", expand=True)
        selected = []
        for card in cards:
            row = tk.Frame(area.inner, bg=COLORS["bg"])
            row.pack(fill="x", pady=3)
            check = tk.BooleanVar(value=True)
            selected.append((check, card))
            ttk.Checkbutton(row, text=card["title"], variable=check).pack(anchor="w")
            ttk.Button(row, text=tr("编辑 / 改分类"), command=lambda c=card: self.edit(c, on_save=lambda v: c.update(v))).pack(anchor="w")
        as_pr = tk.BooleanVar(value=True)
        ttk.Checkbutton(area.inner, text=tr("技巧卡片生成待审提案"), variable=as_pr).pack(anchor="w")
        def save():
            try:
                for check, card in selected:
                    if not check.get():
                        continue
                    if card["kind"] == "tactical" and as_pr.get():
                        self.kb.propose("ADD_CARD", subject=card.get("subject", "math"), title=card["title"], proposed_card=card,
                                        summary=card.get("source", ""))
                    else:
                        self.kb.save_card(card, maintainer=True)
                self.kb.audit("import", count=sum(v.get() for v, _ in selected))
                dlg.destroy()
                self.show()
            except (ValueError, OSError) as error:
                toast.warn(tr(str(error)))
        ttk.Button(area.inner, text=tr("确认导入"), command=save).pack(anchor="e", pady=6)
        area.bind_children()

    def settings(self):
        dlg = modal(self, "知识库设置")
        area = ScrollArea(dlg.body, bg=COLORS["bg"])
        area.pack(fill="both", expand=True)
        preview = tk.BooleanVar(value=bool(self.kb.config.get("preview_unverified_prs")))
        git = tk.BooleanVar(value=bool(self.kb.config.get("git_auto_commit")))
        for label, var in (("预览未审提案", preview), ("合入后自动 Git 提交", git)):
            ttk.Checkbutton(area.inner, text=tr(label), variable=var).pack(anchor="w", pady=6)
        path = tk.StringVar(value=str(self.kb.root))
        ttk.Entry(area.inner, textvariable=path).pack(fill="x", pady=6)
        def choose():
            value = filedialog.askdirectory(parent=dlg, initialdir=path.get())
            if value:
                path.set(value)
        ttk.Button(area.inner, text=tr("选择知识库目录"), command=choose).pack(anchor="w")
        tk.Label(area.inner, text=tr("不考虑的技巧"), bg=COLORS["bg"], fg=COLORS["text"], font=config.font("body", bold=True)).pack(anchor="w", pady=(12, 4))
        excluded = tk.Text(area.inner, height=8, wrap="word", bg=COLORS["bg_card"], fg=COLORS["text"], font=config.font("body"))
        excluded.pack(fill="x")
        excluded.insert("1.0", self.kb.config.get("excluded_tactics", ""))
        tk.Label(area.inner, text=tr("自定义学科（键:目录名，每行一项）"), bg=COLORS["bg"], fg=COLORS["text"], font=config.font("body")).pack(anchor="w", pady=(12, 4))
        subjects = tk.Text(area.inner, height=4, wrap="word", bg=COLORS["bg_card"], fg=COLORS["text"], font=config.font("body"))
        subjects.pack(fill="x")
        subjects.insert("1.0", "\n".join(k + ":" + v for k, v in self.kb.subjects.items()))
        def save():
            try:
                custom = dict(line.split(":", 1) for line in subjects.get("1.0", "end").splitlines() if line.strip())
                if not all(k.strip() and v.strip() for k, v in custom.items()):
                    raise ValueError(tr("学科格式无效"))
                self.kb = select_vault(self.data_dir, path.get())
                self.kb.configure(preview_unverified_prs=preview.get(), git_auto_commit=git.get(), subjects=custom,
                                  excluded_tactics=excluded.get("1.0", "end-1c").strip())
                self.kb.subjects.update(custom)
                dlg.destroy()
                self.show()
            except (OSError, ValueError) as error:
                toast.warn(tr(str(error)))
        ttk.Button(dlg.buttons, text=tr("确认保存"), command=save).pack(anchor="e")
        ttk.Button(dlg.buttons, text=tr("自动权重变更日志"), command=self.audit_log).pack(anchor="w")
        area.bind_children()

    def audit_log(self):
        dlg = modal(self, "自动权重变更日志")
        text = tk.Text(dlg.body, wrap="word", bg=COLORS["bg_card"], fg=COLORS["text"], font=config.font("body"))
        text.pack(fill="both", expand=True)
        path = self.kb.internal / "audit.jsonl"
        if path.exists():
            text.insert("end", "\n".join(path.read_text(encoding="utf-8").splitlines()[-100:]))
        text.configure(state="disabled")
