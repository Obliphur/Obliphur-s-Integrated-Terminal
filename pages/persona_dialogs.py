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


def _text(parent, height, value="", fill="x", expand=False):
    row = tk.Frame(parent, bg=COLORS["bg"])
    row.pack(fill=fill, expand=expand, pady=(2, 8))
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
    box = _text(dlg.body, 1, text or tr("当前人设还没有简介。"), fill="both", expand=True)
    box.configure(state="disabled")
    ttk.Button(dlg.buttons, text=tr("关闭"), command=dlg.cancel).pack(side="right")


def create_persona(parent, comm):
    if ai_assist.ai_config.chat_takeover() == "antigravity":
        from . import ai_dialogs
        ai_dialogs.open_takeover_feature_help(parent, "persona")
        return
    dlg = Modal(parent, tr("创建人设"), min(780, parent.winfo_screenwidth() - 40),
                min(740, parent.winfo_screenheight() - 80))
    area = ScrollArea(dlg.body)
    area.pack(fill="both", expand=True)
    body = area.inner
    drafts = queue.Queue()
    status = tk.Label(body, bg=COLORS["bg"], fg=COLORS["text"], justify="left", anchor="w",
                      font=config.font("small"))
    wrap_label(status)

    def label(container, key):
        tk.Label(container, text=tr(key), bg=COLORS["bg"], fg=COLORS["text"],
                 font=config.font("body"), anchor="w").pack(fill="x")

    tip = tk.Label(body, text=tr("提示：编写人设时可以引用外部网站进行参考（比如萌娘百科）。"),
                   bg=COLORS["bg"], fg=COLORS["text_dim"], font=config.font("small"),
                   justify="left", anchor="w")
    wrap_label(tip)
    tip.pack(fill="x", pady=(0, 8))

    label(body, "人设名称")
    name = ttk.Entry(body)
    name.pack(fill="x", pady=(2, 8))

    label(body, "生成详略")
    mode_var = tk.StringVar(value="detailed")
    mode_row = tk.Frame(body, bg=COLORS["bg"])
    mode_row.pack(fill="x", pady=(2, 8))
    rb_det = ttk.Radiobutton(mode_row, text=tr("详细（面面俱到）"), value="detailed", variable=mode_var)
    rb_bri = ttk.Radiobutton(mode_row, text=tr("简略（基础人设 + 轻范例）"), value="brief", variable=mode_var)
    rb_det.pack(side="left", padx=(0, 14))
    rb_bri.pack(side="left")

    label(body, "亲密动作最高开放程度（V+ / BURST / 确立关系后）")
    intimacy_var = tk.IntVar(value=comm_author.intimacy_level_index(comm_author.DEFAULT_INTIMACY_LEVEL))
    intimacy_card = tk.Frame(body, bg=COLORS["bg_card"], padx=10, pady=8)
    intimacy_card.pack(fill="x", pady=(2, 8))

    slider_row = tk.Frame(intimacy_card, bg=COLORS["bg_card"])
    slider_row.pack(fill="x")
    intimacy_title = tk.Label(slider_row, text="", bg=COLORS["bg_card"], fg=COLORS["accent"],
                              font=config.font("body", bold=True), anchor="w")
    intimacy_title.pack(side="left")

    intimacy_desc = tk.Label(intimacy_card, text="", bg=COLORS["bg_card"], fg=COLORS["text"],
                             font=config.font("small"), justify="left", anchor="w")
    wrap_label(intimacy_desc)
    intimacy_fit = tk.Label(intimacy_card, text="", bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                            font=config.font("small"), justify="left", anchor="w")
    wrap_label(intimacy_fit)
    intimacy_summary = tk.Label(intimacy_card, text=tr(comm_author.INTIMACY_MODEL_SUMMARY),
                                bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                                font=config.font("small"), justify="left", anchor="w")
    wrap_label(intimacy_summary)

    consent_var = tk.BooleanVar(value=False)
    consent_chk = ttk.Checkbutton(
        intimacy_card, text=tr(comm_author.CONSENT_MODE_LABEL), variable=consent_var,
    )
    consent_hint = tk.Label(
        intimacy_card, text=tr(comm_author.CONSENT_MODE_HINT),
        bg=COLORS["bg_card"], fg=COLORS["text_dim"],
        font=config.font("small"), justify="left", anchor="w",
    )
    wrap_label(consent_hint)

    def _current_intimacy():
        idx = max(0, min(len(comm_author.INTIMACY_LEVELS) - 1, int(intimacy_var.get())))
        return comm_author.INTIMACY_LEVELS[idx]

    def _sync_intimacy(_val=None):
        info = _current_intimacy()
        intimacy_title.configure(text=tr(info.title))
        intimacy_desc.configure(text=tr(info.description))
        intimacy_fit.configure(text=tr(info.model_fit))
        if info.level in comm_author.CONSENT_MODE_LEVELS:
            consent_chk.configure(state="normal")
        else:
            consent_var.set(False)
            consent_chk.configure(state="disabled")

    intimacy_scale = tk.Scale(
        intimacy_card, from_=0, to=len(comm_author.INTIMACY_LEVELS) - 1, resolution=1,
        orient="horizontal", variable=intimacy_var, showvalue=False,
        bg=COLORS["bg_card"], fg=COLORS["text"], troughcolor=COLORS["bg"],
        highlightthickness=0, activebackground=COLORS["accent"], command=_sync_intimacy,
    )
    intimacy_scale.pack(fill="x", pady=(4, 2))
    ticks = tk.Frame(intimacy_card, bg=COLORS["bg_card"])
    ticks.pack(fill="x", pady=(0, 6))
    for i, item in enumerate(comm_author.INTIMACY_LEVELS):
        ticks.columnconfigure(i, weight=1)
        anchor = "w" if i == 0 else ("e" if i == len(comm_author.INTIMACY_LEVELS) - 1 else "center")
        tk.Label(ticks, text=str(item.level), bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                 font=config.font("small"), anchor=anchor).grid(row=0, column=i, sticky="ew")
    intimacy_desc.pack(fill="x")
    intimacy_fit.pack(fill="x", pady=(4, 0))
    intimacy_summary.pack(fill="x", pady=(4, 0))
    consent_chk.pack(anchor="w", pady=(8, 0))
    consent_hint.pack(fill="x", pady=(2, 0))
    _sync_intimacy()

    label(body, "创作要求")
    requirements = _text(body, 6)
    label(body, "人设简介（可手写，留空由 AI 生成）")
    manual = _text(body, 4)
    status.pack(fill="x")
    area.bind_children()
    name.focus_set()

    def value(box):
        return box.get("1.0", "end-1c")

    def set_text(box, text):
        box.delete("1.0", "end")
        box.insert("1.0", text)

    def generate():
        title, request, profile = name.get().strip(), value(requirements).strip(), value(manual)
        mode = mode_var.get()
        intimacy_level = _current_intimacy().level
        consent_mode = bool(consent_var.get()) and intimacy_level in comm_author.CONSENT_MODE_LEVELS
        try:
            comm_author.all_file_names(comm.personas, title)
            if not request:
                raise ValueError("请填写创作要求。")
            model = comm.ai.profile()
            ai_assist.check_profile(model)
        except (OSError, ValueError, ai_assist.AIError) as exc:
            status.configure(text=tr(str(exc)))
            return
        for widget in (name, rb_det, rb_bri, intimacy_scale, consent_chk, requirements, manual, submit):
            widget.configure(state="disabled")
        status.configure(text=tr("正在参考 Claudia 生成 5 份人设文件…"))

        def work():
            try:
                drafts.put((comm_author.generate(
                    model, title, request, profile, mode=mode, folder=comm.personas,
                    intimacy_level=intimacy_level, consent_mode=consent_mode,
                ), ""))
            except (OSError, ValueError, ai_assist.AIError) as exc:
                drafts.put((None, str(exc)))

        def poll():
            try:
                draft, error = drafts.get_nowait()
            except queue.Empty:
                dlg.after(100, poll)
                return
            if draft is None:
                for widget in (name, rb_det, rb_bri, intimacy_scale, requirements, manual, submit):
                    widget.configure(state="normal")
                _sync_intimacy()
                status.configure(text=tr(error))
                return

            # 切换到 5 文件可读可写编辑区 + 底部修改聊天框
            area.pack_forget()
            editor = tk.Frame(dlg.body, bg=COLORS["bg"])
            editor.pack(fill="both", expand=True)

            nb = ttk.Notebook(editor)
            nb.pack(fill="both", expand=True, pady=(0, 8))
            boxes = {}
            tabs = (
                ("persona", tr("主人设 (.txt)"), draft.persona),
                ("background", tr("背景 (_background.txt)"), draft.background),
                ("example", tr("范例 (_example.txt)"), draft.example),
                ("templates", tr("模板 (_templates.txt)"), draft.templates),
                ("profile", tr("简介 (_profile.txt)"), draft.profile),
            )
            for key, tab_title, init_val in tabs:
                tab = tk.Frame(nb, bg=COLORS["bg"])
                nb.add(tab, text=tab_title)
                boxes[key] = _text(tab, 12, init_val, fill="both", expand=True)

            rev_status = tk.Label(editor, text=tr("可直接编辑以上 5 个文件，或在下方输入意见让 AI 修改；满意后点击「确定」保存。"),
                                  bg=COLORS["bg"], fg=COLORS["text_dim"], font=config.font("small"),
                                  justify="left", anchor="w")
            wrap_label(rev_status)
            rev_status.pack(fill="x", pady=(0, 4))

            chat_row = tk.Frame(editor, bg=COLORS["bg"])
            chat_row.pack(fill="x")
            chat_in = tk.Text(chat_row, height=3, width=1, wrap="word", font=config.font("body"),
                              bg=COLORS["bg_card"], fg=COLORS["text"], insertbackground=COLORS["text"])
            chat_in.pack(side="left", fill="x", expand=True, padx=(0, 8))
            rev_q = queue.Queue()

            def current_draft():
                return comm_author.Draft(
                    persona=value(boxes["persona"]),
                    templates=value(boxes["templates"]),
                    profile=value(boxes["profile"]),
                    background=value(boxes["background"]),
                    example=value(boxes["example"]),
                )

            def do_revise(_event=None):
                instr = value(chat_in).strip()
                if not instr:
                    return "break"
                cur = current_draft()
                for w in (chat_in, btn_rev, submit, *boxes.values()):
                    w.configure(state="disabled")
                rev_status.configure(text=tr("正在按要求修改人设文件…"))

                def rwork():
                    try:
                        updated = comm_author.revise(
                            model, title, cur, instr, mode=mode, folder=comm.personas,
                            intimacy_level=intimacy_level, consent_mode=consent_mode,
                        )
                        rev_q.put((updated, ""))
                    except (OSError, ValueError, ai_assist.AIError) as exc:
                        rev_q.put((None, str(exc)))

                def rpoll():
                    try:
                        updated, rerr = rev_q.get_nowait()
                    except queue.Empty:
                        dlg.after(100, rpoll)
                        return
                    for w in (chat_in, btn_rev, submit, *boxes.values()):
                        w.configure(state="normal")
                    if updated is None:
                        rev_status.configure(text=tr(rerr))
                        return
                    set_text(boxes["persona"], updated.persona)
                    set_text(boxes["background"], updated.background)
                    set_text(boxes["example"], updated.example)
                    set_text(boxes["templates"], updated.templates)
                    set_text(boxes["profile"], updated.profile)
                    chat_in.delete("1.0", "end")
                    rev_status.configure(text=tr("已按要求更新，可继续修改或点击「确定」保存到本地人设文件夹。"))

                threading.Thread(target=rwork, daemon=True).start()
                dlg.after(100, rpoll)
                return "break"

            btn_rev = ttk.Button(chat_row, text=tr("发送修改"), command=do_revise)
            btn_rev.pack(side="right", anchor="s")
            chat_in.bind("<Control-Return>", do_revise)

            def save():
                edited = current_draft()
                try:
                    saved = comm_author.save(comm.personas, title, edited)
                except OSError:
                    rev_status.configure(text=tr("保存失败."))
                except ValueError as exc:
                    rev_status.configure(text=tr(str(exc)))
                else:
                    dlg.finish(saved)

            submit.configure(text=tr("确定"), state="normal", command=save)

        threading.Thread(target=work, daemon=True).start()
        dlg.after(100, poll)

    ttk.Button(dlg.buttons, text=tr("取消"), command=dlg.cancel).pack(side="right")
    submit = ttk.Button(dlg.buttons, text=tr("生成草稿"), style="Accent.TButton", command=generate)
    submit.pack(side="right", padx=8)
    return dlg.wait()
