# -*- coding: utf-8 -*-
"""💬 沟通页: 和人设聊天 (逻辑在 comm.py). 左边对方, 右边自己; 头像默认不显示, 在 设置 → 沟通 里打开."""

import tkinter as tk
from datetime import datetime
from tkinter import ttk

import avatar as avatar_mod
import comm as comm_mod
import config
import toast
from config import COLORS
from i18n import tr
from .widgets import ScrollArea, cfg


class CommPage(tk.Frame):

    def __init__(self, master, comm, username=None, on_open_settings=None):
        super().__init__(master, bg=COLORS["bg"])
        self.comm = comm
        self.username = username or (lambda: "")
        self.on_open_settings = on_open_settings
        self._ver = -1
        self._width = 0
        self._resize_job = None
        self._photos = []
        self._build()

    def _build(self):
        bg = COLORS["bg"]
        hdr = tk.Frame(self, bg=bg)
        hdr.pack(fill="x", padx=28, pady=(18, 6))
        tk.Label(hdr, text=tr("💬  沟通"), bg=bg, fg=COLORS["text"],
                 font=config.font("h1", bold=True)).pack(side="left")
        ttk.Button(hdr, text=tr("清空"), command=self._clear).pack(side="right")
        self.lbl_who = tk.Label(hdr, text="", bg=bg, fg=COLORS["text_dim"], font=config.font("body"))
        self.lbl_who.pack(side="right", padx=(0, 12))

        self.area = ScrollArea(self, height=300, bg=bg)
        self.area.pack(fill="both", expand=True, padx=26, pady=(4, 4))
        self.area.canvas.bind("<Configure>", self._on_resize, add="+")

        self.lbl_status = tk.Label(self, text="", bg=bg, fg=COLORS["text_dim"], font=config.font("small"),
                                   anchor="w")
        self.lbl_status.pack(fill="x", padx=30)

        row = tk.Frame(self, bg=bg)
        row.pack(fill="x", padx=28, pady=(4, 14))
        self.entry = tk.Text(row, height=3, wrap="word", font=config.font("body"),
                             bg=COLORS["bg_card"], fg=COLORS["text"], insertbackground=COLORS["text"],
                             relief="flat", highlightthickness=1, highlightbackground=COLORS["border"],
                             highlightcolor=COLORS["accent"])
        self.entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text=tr("发送"), style="Accent.TButton", command=self._send).pack(
            side="left", padx=(8, 0), anchor="s")
        self.entry.bind("<Return>", self._on_return)

    # ---- 显示 ----

    def show(self):
        self.comm.set_viewing(True)
        self.refresh(force=True)
        self.entry.focus_set()

    def hide(self):
        self.comm.set_viewing(False)

    def _on_resize(self, event):
        if abs(event.width - self._width) < 60:
            return
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(200, lambda: self.refresh(force=True))

    def refresh(self, force: bool = False):
        c = self.comm
        if not force and c.version == self._ver:
            return
        self._ver = c.version
        persona = comm_mod.persona_title(c.persona_name())
        cfg(self.lbl_who, text=persona)
        if c.error:
            cfg(self.lbl_status, text=tr(c.error), fg=COLORS["danger"])
        else:
            cfg(self.lbl_status, text="")
        self._draw()

    def _avatar(self, parent, text: str, mine: bool):
        size = config.scaled(34)
        img = self.comm.avatar_path("me" if mine else "ai")
        if img:
            try:
                photo = tk.PhotoImage(file=avatar_mod.sized(img, size))
                self._photos.append(photo)
                lb = tk.Label(parent, image=photo, bg=COLORS["bg"], bd=0)
                return lb
            except (OSError, tk.TclError):
                pass
        cv = tk.Canvas(parent, width=size, height=size, bg=COLORS["bg"], highlightthickness=0)
        cv.create_oval(1, 1, size - 1, size - 1, outline=COLORS["border"],
                       fill=COLORS.get("accent_fill", COLORS["accent"]) if mine else COLORS["bg_card"])
        cv.create_text(size // 2, size // 2, text=text,
                       fill=COLORS.get("on_accent_fill", COLORS["on_accent"]) if mine else COLORS["text"],
                       font=config.font("body", bold=True))
        return cv

    def _draw(self):
        c = self.comm
        inner = self.area.inner
        for w in inner.winfo_children():
            w.destroy()
        self._photos = []
        width = max(300, self.area.canvas.winfo_width())
        self._width = width
        wrap = int(width * 0.6)
        avatars = bool(c.cfg.get("show_avatar"))
        me_char = (self.username() or "我")[:1] if avatars else ""
        ai_char = (comm_mod.persona_title(c.persona_name())[:1] or "?") if avatars else ""
        bg = COLORS["bg"]
        for m in c.visible():
            mine = m["role"] == "me"
            row = tk.Frame(inner, bg=bg)
            row.pack(fill="x", pady=4, padx=4)
            side = "right" if mine else "left"
            if avatars:
                self._avatar(row, me_char if mine else ai_char, mine).pack(side=side, anchor="n", padx=6)
            box = tk.Frame(row, bg=bg)
            box.pack(side=side)
            if mine:
                fill, fg, rim = COLORS.get("accent_fill", COLORS["accent"]), COLORS.get("on_accent_fill", COLORS["on_accent"]), None
            else:
                fill, fg, rim = COLORS["bg_card"], COLORS["text"], COLORS["border"]
            bubble = tk.Label(box, text=m["text"], bg=fill, fg=fg, font=config.font("body"), justify="left",
                              anchor="w", wraplength=wrap, padx=12, pady=8,
                              highlightthickness=1 if rim else 0, highlightbackground=rim or fill)
            bubble.pack(anchor="e" if mine else "w")
            try:
                when = datetime.fromisoformat(m["at"]).strftime("%H:%M")
            except (KeyError, ValueError):
                when = ""
            tk.Label(box, text=when, bg=bg, fg=COLORS["text_dim"], font=config.font("small")).pack(
                anchor="e" if mine else "w")
        self.area.bind_children()
        self.update_idletasks()
        self.area.canvas.yview_moveto(1.0)

    # ---- 操作 ----

    def _on_return(self, event):
        if event.state & 0x1:                  # Shift+Enter: 换行
            return None
        self._send()
        return "break"

    def _send(self):
        text = self.entry.get("1.0", "end").strip()
        if not text:
            return
        why = self.comm.send(text)             # "/clear" 在 send 里处理: 清空记录
        if why and why != "empty":
            toast.warn(tr(why))
            if self.on_open_settings:
                self.on_open_settings()
            return
        self.entry.delete("1.0", "end")
        self.refresh(force=True)

    def _clear(self):
        if not self.comm.messages:
            return
        if toast.ask(tr("清空聊天记录?"), title=tr("清空"), danger=True):
            self.comm.clear()
            self.refresh(force=True)
