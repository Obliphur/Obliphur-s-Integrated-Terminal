# -*- coding: utf-8 -*-
"""share data (1.0.357): 打开后, 在 设置 / 沟通 / 指令 / 重置 以外的页面下方有一个聊天悬浮窗 (ShareDock), 可以收发消息;
计时器 / 整点记录 / 日历 / 统计 页上可以切换"我的数据 / AI 的数据" (AiDataView 盖在原页面上, 画的是 comm.share_ai_view 给的内容)."""

import calendar as cal_mod
import json
import tkinter as tk
from datetime import datetime
from tkinter import ttk

import config
from config import COLORS
from i18n import tr
from .widgets import ScrollArea, cfg

CAL_KINDS = (("focus", "专注"), ("rest", "休息"), ("idle", "空闲"), ("sleep", "睡眠"))
SHOW_MSGS = 12             # 悬浮窗里显示最近多少条
DOCK_LINES = 7             # 悬浮窗里消息区的高度 (行)


class AiDataView(tk.Frame):
    """AI 的数据: 盖在原页面上 (只读). 内容是 comm.share_ai_view(page) 给的 blocks."""

    def __init__(self, master):
        super().__init__(master, bg=COLORS["bg"])
        self._sig = None
        self._kind = "focus"
        self.area = ScrollArea(self, height=300, bg=COLORS["bg"])
        self.area.pack(fill="both", expand=True)

    def render(self, view: dict, name: str = "AI"):
        sig = json.dumps(view, sort_keys=True, ensure_ascii=False) + self._kind + "|" + name
        if sig == self._sig:
            return
        self._sig = sig
        inner = self.area.inner
        for w in inner.winfo_children():
            w.destroy()
        bg, card = COLORS["bg"], COLORS["bg_card"]
        if not view:
            tk.Label(inner, text=tr("这一页没有 {name} 的数据.", name=name), bg=bg, fg=COLORS["text_dim"], font=config.font("body")).pack(padx=28, pady=28, anchor="w")
            return
        tk.Label(inner, text="🤖  " + tr("{name} 的数据", name=name) + " · " + tr(view.get("title", "")), bg=bg, fg=COLORS["text"],
                 font=config.font("h1", bold=True), anchor="w").pack(fill="x", padx=28, pady=(22, 8))
        for b in view.get("blocks", []):
            t = b.get("t")
            if t == "card":
                self._card(inner, b)
            elif t == "rows":
                self._rows(inner, b)
            elif t == "cal":
                self._cal(inner, b)
        self.area.bind_children()

    def _card(self, parent, b):
        card = COLORS["bg_card"]
        fr = tk.Frame(parent, bg=card, highlightbackground=COLORS["border"], highlightthickness=1)
        fr.pack(fill="x", padx=26, pady=(0, 12))
        inner = tk.Frame(fr, bg=card)
        inner.pack(fill="x", padx=22, pady=16)
        lines = list(b.get("lines", []))
        row = tk.Frame(inner, bg=card)
        row.pack(anchor="w", pady=(0, 8))
        dot = tk.Canvas(row, width=12, height=12, bg=card, highlightthickness=0)
        dot.pack(side="left", padx=(0, 8))
        dot.create_oval(1, 1, 11, 11, fill=b.get("dot") or COLORS["text_dim"], outline="")
        tk.Label(row, text=tr(lines[0]) if lines else "", bg=card, fg=COLORS["text_dim"], font=config.font("body")).pack(side="left")
        if len(lines) > 1:
            tk.Label(inner, text=tr(lines[1]), bg=card, fg=COLORS["text"], font=config.font("big", bold=True), anchor="w").pack(anchor="w", pady=(2, 6))
        for extra in lines[2:]:
            tk.Label(inner, text=tr(extra) if not extra[:1].isdigit() else extra, bg=card, fg=COLORS["accent"], font=config.font("body", bold=True),
                     anchor="w").pack(anchor="w")

    def _rows(self, parent, b):
        bg, card = COLORS["bg"], COLORS["bg_card"]
        tk.Label(parent, text=tr(b.get("title", "")), bg=bg, fg=COLORS["text"], font=config.font("body", bold=True), anchor="w").pack(
            fill="x", padx=28, pady=(8, 2))
        fr = tk.Frame(parent, bg=card, highlightbackground=COLORS["border"], highlightthickness=1)
        fr.pack(fill="x", padx=26, pady=(0, 8))
        for left, right, frac in b.get("rows", []):
            r = tk.Frame(fr, bg=card)
            r.pack(fill="x", padx=12, pady=2)
            tk.Label(r, text=tr(left), bg=card, fg=COLORS["text"], font=config.font("small"), anchor="w", width=16).pack(side="left")
            if frac is not None:
                bar = tk.Canvas(r, height=10, bg=card, highlightthickness=0)
                bar.pack(side="left", fill="x", expand=True, padx=(4, 8))
                bar.bind("<Configure>", lambda e, c=bar, f=frac: (c.delete("all"), c.create_rectangle(0, 2, max(2, int(e.width * f)), 9, fill=COLORS["accent"], outline="")))
            tk.Label(r, text=tr(right), bg=card, fg=COLORS["text_dim"], font=config.font("small"), anchor="w" if frac is None else "e").pack(
                side="left", fill="x", expand=frac is None)

    def _cal(self, parent, b):
        bg, card = COLORS["bg"], COLORS["bg_card"]
        bar = tk.Frame(parent, bg=bg)
        bar.pack(fill="x", padx=28, pady=(0, 6))
        year, month = b["year"], b["month"]
        days = b.get("days", {}).get(self._kind, {})
        tk.Label(bar, text=tr("{year} 年 {month} 月").format(year=year, month=month) if "{year}" in tr("{year} 年 {month} 月") else f"{year}-{month:02d}",
                 bg=bg, fg=COLORS["text"], font=config.font("h2", bold=True)).pack(side="left")
        tk.Label(bar, text=tr("合计: {h:.1f} h").format(h=sum(days.values())), bg=bg, fg=COLORS["accent"],
                 font=config.font("body", bold=True)).pack(side="left", padx=14)
        for kind, name in reversed(CAL_KINDS):
            btn = ttk.Button(bar, text=tr(name), command=lambda k=kind: self._set_kind(k))
            btn.pack(side="right", padx=(4, 0))
            if kind == self._kind:
                btn.state(["disabled"])
        grid = tk.Frame(parent, bg=bg)
        grid.pack(fill="x", padx=26, pady=(0, 12))
        for i in range(7):
            grid.columnconfigure(i, weight=1)
        for i, d in enumerate(("周一", "周二", "周三", "周四", "周五", "周六", "周日")):
            tk.Label(grid, text=tr(d), bg=card, fg=COLORS["text_dim"], font=config.font("small", bold=True)).grid(row=0, column=i, padx=1, pady=1, sticky="nsew")
        for r, week in enumerate(cal_mod.monthcalendar(year, month), start=1):
            for c, day in enumerate(week):
                if not day:
                    continue
                ds = f"{year}-{month:02d}-{day:02d}"
                h = days.get(ds, 0.0)
                today = ds == b.get("today")
                tk.Label(grid, text=f"{day}\n{h:.1f}h" if h > 0 else str(day), bg=COLORS["accent"] if today else card,
                         fg=COLORS["on_accent"] if today else COLORS["text"], font=config.font("small"), height=2).grid(
                    row=r, column=c, padx=1, pady=1, sticky="nsew")

    def _set_kind(self, kind):
        self._kind = kind
        self._sig = None
        self.event_generate("<<AiKind>>")


class ShareDock(tk.Frame):
    """页面下方的聊天悬浮窗: 最近的几条消息 + 输入框; 还有 我的数据 / AI 的数据 的切换."""

    def __init__(self, master, comm, on_view=None):
        super().__init__(master, bg=COLORS["bg_card"], highlightbackground=COLORS["border"], highlightthickness=1)
        self.comm = comm
        self.on_view = on_view or (lambda: None)
        self._sig = None
        self._folded = False
        bg = COLORS["bg_card"]
        hdr = tk.Frame(self, bg=bg)
        hdr.pack(fill="x", padx=10, pady=(6, 2))
        self.lbl_title = tk.Label(hdr, text="", bg=bg, fg=COLORS["text"], font=config.font("small", bold=True))
        self.lbl_title.pack(side="left")
        self.btn_fold = ttk.Button(hdr, text="▾", width=3, command=self._fold)
        self.btn_fold.pack(side="right")
        self.btn_ai = ttk.Button(hdr, text=tr("AI 的数据"), command=lambda: self._view("ai"))
        self.btn_ai.pack(side="right", padx=(0, 6))
        self.btn_me = ttk.Button(hdr, text=tr("我的数据"), command=lambda: self._view("me"))
        self.btn_me.pack(side="right", padx=(0, 4))
        self.body = tk.Frame(self, bg=bg)
        self.body.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.txt = tk.Text(self.body, height=DOCK_LINES, wrap="word", state="disabled", font=config.font("small"), bg=COLORS["bg"],
                           fg=COLORS["text"], relief="flat", highlightthickness=0)
        self.txt.pack(fill="both", expand=True)
        self.txt.tag_configure("me", foreground=COLORS["accent"], justify="right")
        self.txt.tag_configure("ai", foreground=COLORS["text"])
        self.lbl_err = tk.Label(self.body, text="", bg=bg, fg=COLORS["danger"], font=config.font("small"), anchor="w")
        self.lbl_err.pack(fill="x")
        row = tk.Frame(self.body, bg=bg)
        row.pack(fill="x", pady=(2, 0))
        self.entry = tk.Entry(row, font=config.font("body"), bg=COLORS["bg"], fg=COLORS["text"], insertbackground=COLORS["text"],
                              relief="flat", highlightthickness=1, highlightbackground=COLORS["border"], highlightcolor=COLORS["accent"])
        self.entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.entry.bind("<Return>", lambda _e: self._send())
        ttk.Button(row, text=tr("发送"), style="Accent.TButton", command=self._send).pack(side="left", padx=(6, 0))

    def _fold(self):
        self._folded = not self._folded
        if self._folded:
            self.body.pack_forget()
        else:
            self.body.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        cfg(self.btn_fold, text="▴" if self._folded else "▾")

    def _view(self, view):
        self.comm.set_share_view(view)
        self.on_view()

    def _send(self):
        text = self.entry.get().strip()
        if not text:
            return
        why = self.comm.send(text)
        if why and why != "empty":
            cfg(self.lbl_err, text=tr(why))
            return
        cfg(self.lbl_err, text="")
        self.entry.delete(0, "end")
        self._sig = None
        self.refresh()

    def refresh(self, has_ai: bool = False):
        """has_ai: 这一页能看 AI 的数据 (有切换按钮)."""
        c = self.comm
        page = c.share_page()
        name = comm_mod_names().get(page, page)
        cfg(self.lbl_title, text="💬  " + tr("share data") + " · " + tr(name))
        cfg(self.btn_ai, text=tr("{name} 的数据", name=c.share_ai_name()))      # 对方的数据按钮写人设名
        for btn, on in ((self.btn_me, c.share_view() == "me"), (self.btn_ai, c.share_view() == "ai")):
            if btn.instate(["disabled"]) != on:
                btn.state(["disabled" if on else "!disabled"])
        if has_ai and self.btn_me.winfo_manager() != "pack":
            self.btn_ai.pack(side="right", padx=(0, 6), before=self.btn_fold)
            self.btn_me.pack(side="right", padx=(0, 4), before=self.btn_ai)
        elif not has_ai and self.btn_me.winfo_manager() == "pack":
            self.btn_me.pack_forget()
            self.btn_ai.pack_forget()
        msgs = c.visible()[-SHOW_MSGS:]
        sig = (c.version, len(c.messages), msgs[-1]["id"] if msgs else 0)
        if sig == self._sig:
            return
        self._sig = sig
        self.txt.config(state="normal")
        self.txt.delete("1.0", "end")
        for m in msgs:
            who = "ai" if m["role"] == "ai" else "me"
            self.txt.insert("end", ("" if who == "ai" else "") + m["text"] + "\n", who)
        self.txt.config(state="disabled")
        self.txt.see("end")


def comm_mod_names() -> dict:
    import comm as comm_mod
    return comm_mod.SHARE_PAGE_NAMES
