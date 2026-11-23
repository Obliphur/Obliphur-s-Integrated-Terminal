# -*- coding: utf-8 -*-
"""设置 → 沟通里查看简介与创建人设的弹窗。"""

import queue
import threading
import tkinter as tk
from tkinter import ttk

import ai_assist
import config
from comm import comm_author
from config import COLORS
from i18n import tr
from .widgets import Modal, ScrollArea, wrap_label


def _text(parent, height, value=""):
    row = tk.Frame(parent, bg=COLORS["bg"])
    row.pack(fill="x", pady=(2, 8))
    box = tk.Text(row, height=height, width=1, wrap="word", font=config.font("body"),
                  bg=COLORS["bg_card"], fg=COLORS["text"], insertbackground=COLORS["text"])
    bar = ttk.Scrollbar(row, orient="vertical", command=box.yview)
    box.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y")
    box.pack(side="left", fill="both", expand=True)
    box.insert("1.0", value)
    return box


def open_profile(parent, text):
    dlg = Modal(parent, tr("人设简介"), min(620, parent.winfo_screenwidth() - 40), 330)
    box = _text(dlg.body, 1, text or tr("当前人设还没有简介。"))
    box.master.pack_configure(fill="both", expand=True)
    box.configure(state="disabled")
    ttk.Button(dlg.buttons, text=tr("关闭"), command=dlg.cancel).pack(side="right")


def create_persona(parent, comm):
    if ai_assist.ai_config.chat_takeover() == "antigravity":
        from . import ai_dialogs
        ai_dialogs.open_takeover_feature_help(parent, "persona")
        return
    dlg = Modal(parent, tr("创建人设"), min(710, parent.winfo_screenwidth() - 40),
                min(620, parent.winfo_screenheight() - 100))
    area = ScrollArea(dlg.body)
    area.pack(fill="both", expand=True)
    body = area.inner
    drafts = queue.Queue()
    status = tk.Label(body, bg=COLORS["bg"], fg=COLORS["text"], justify="left", anchor="w",
                      font=config.font("small"))
    wrap_label(status)

    def label(key):
        tk.Label(body, text=tr(key), bg=COLORS["bg"], fg=COLORS["text"],
                 font=config.font("body"), anchor="w").pack(fill="x")

    label("人设名称")
    name = ttk.Entry(body)
    name.pack(fill="x", pady=(2, 8))
    label("创作要求")
    requirements = _text(body, 6)
    label("人设简介（可手写，留空由 AI 生成）")
    manual = _text(body, 4)
    status.pack(fill="x")
    area.bind_children()
    name.focus_set()

    def value(box):
        return box.get("1.0", "end-1c")

    def generate():
        title, request, profile = name.get().strip(), value(requirements).strip(), value(manual)
        try:
            comm_author.file_names(comm.personas, title)
            if not request:
                raise ValueError("请填写创作要求。")
            model = comm.ai.profile()
            ai_assist.check_profile(model)
        except (OSError, ValueError, ai_assist.AIError) as exc:
            status.configure(text=tr(str(exc)))
            return
        for widget in (name, requirements, manual, submit):
            widget.configure(state="disabled")
        status.configure(text=tr("正在生成人设草稿…"))

        def work():
            try:
                drafts.put((comm_author.generate(model, title, request, profile), ""))
            except (OSError, ValueError, ai_assist.AIError) as exc:
                drafts.put((None, str(exc)))

        def poll():
            try:
                draft, error = drafts.get_nowait()
            except queue.Empty:
                dlg.after(100, poll)
                return
            if draft is None:
                for widget in (name, requirements, manual, submit):
                    widget.configure(state="normal")
                status.configure(text=tr(error))
                return

            def save():
                edited = comm_author.Draft(value(persona), value(templates), value(intro))
                try:
                    saved = comm_author.save(comm.personas, title, edited)
                except OSError:
                    status.configure(text=tr("保存失败."))
                except ValueError as exc:
                    status.configure(text=tr(str(exc)))
                else:
                    dlg.finish(saved)

            for widget in body.winfo_children():
                if widget is not status:
                    widget.destroy()
            label("人设正文")
            persona = _text(body, 10, draft.persona)
            label("配套模板（可选）")
            templates = _text(body, 5, draft.templates)
            label("人设简介")
            intro = _text(body, 5, draft.profile)
            status.pack_configure(after=intro.master)
            status.configure(text="")
            area.bind_children()
            area.scroll_to_top()
            submit.configure(text=tr("保存"), state="normal", command=save)

        threading.Thread(target=work, daemon=True).start()
        dlg.after(100, poll)

    ttk.Button(dlg.buttons, text=tr("取消"), command=dlg.cancel).pack(side="right")
    submit = ttk.Button(dlg.buttons, text=tr("生成草稿"), command=generate)
    submit.pack(side="right", padx=8)
    return dlg.wait()
