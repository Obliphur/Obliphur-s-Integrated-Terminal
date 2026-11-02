# -*- coding: utf-8 -*-
"""💬 沟通页: 和人设聊天 (逻辑在 comm.py). 左边对方, 右边自己; 头像默认不显示, 在 设置 → 沟通 里打开.

消息是增量画的: 新消息只加一行, 不重画整页 (整页重画会有卡顿感); 宽度 / 头像设置变了才重画."""

import os
import tkinter as tk
from datetime import datetime
from tkinter import ttk

import avatar as avatar_mod
import comm as comm_mod
import comm_data
import config
import toast
from config import COLORS
from i18n import tr
from .widgets import Modal, ScrollArea, cfg

# 这些原因是"还没配置好", 提示之后直接带去 设置 → 沟通
NEEDS_SETUP = ("还没有选人设",)
RENDER_MAX = 80          # 打开聊天页时画最近多少条 (全部画出来要等一阵); 往上翻到顶会再接着加载更早的
LOAD_MORE = 40           # 每次往上翻到顶, 多加载多少条


class CommPage(tk.Frame):

    def __init__(self, master, comm, username=None, on_open_settings=None):
        super().__init__(master, bg=COLORS["bg"])
        self.comm = comm
        self.username = username or (lambda: "")
        self.on_open_settings = on_open_settings
        self._ver = -1
        self._blocked = ""
        self._resize_job = None
        self._rendered = []          # 已经画出来的消息 id (从旧到新)
        self._rows = []              # 对应的行控件
        self._cap = RENDER_MAX + 40  # 最多同时画多少行
        self._top_pad = self._bottom_pad = None
        self._sig = None             # 画法变了 (宽度 / 头像) 才需要重画
        self._photos = {}            # (路径, 大小, 修改时间) → PhotoImage
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
        self.area.canvas.bind("<MouseWheel>", lambda _e: self.after_idle(self._check_top), add="+")
        self.area.scroll.bind("<B1-Motion>", lambda _e: self.after_idle(self._check_top), add="+")
        self.area.scroll.bind("<ButtonRelease-1>", lambda _e: self.after_idle(self._check_top), add="+")

        self.lbl_status = tk.Label(self, text="", bg=bg, fg=COLORS["text_dim"], font=config.font("small"),
                                   anchor="w")
        self.lbl_status.pack(fill="x", padx=30)

        tools = tk.Frame(self, bg=bg)
        tools.pack(fill="x", padx=28, pady=(2, 0))
        ttk.Button(tools, text="📎", width=3, command=self._attach).pack(side="left")
        self.lbl_attach = tk.Label(tools, text="", bg=bg, fg=COLORS["accent"], font=config.font("small"), cursor="hand2")
        self.lbl_attach.pack(side="left", padx=(8, 0))
        self.lbl_attach.bind("<Button-1>", lambda _e: self._attach())
        self.btn_attach_x = tk.Label(tools, text="✕", bg=bg, fg=COLORS["text_dim"], font=config.font("small"), cursor="hand2")
        self.btn_attach_x.bind("<Button-1>", lambda _e: self.comm.set_attach({}))

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
        self._to_end()

    def hide(self):
        self.comm.set_viewing(False)

    def _on_resize(self, _event):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(200, self.refresh)

    def refresh(self, force: bool = False):
        c = self.comm
        self._resize_job = None
        blocked = c.blocked()
        if not force and blocked == self._blocked and c.version == self._ver and self._sig == self._signature():
            return
        self._ver = c.version
        self._blocked = blocked
        cfg(self.lbl_who, text=comm_mod.persona_title(c.persona_name()))
        label = c.attach_label()
        cfg(self.lbl_attach, text=label)
        if label:
            if self.btn_attach_x.winfo_manager() != "pack":
                self.btn_attach_x.pack(side="left", padx=(6, 0))
        elif self.btn_attach_x.winfo_manager() == "pack":
            self.btn_attach_x.pack_forget()
        cfg(self.lbl_status, text=blocked or (tr(c.error) if c.error else ""), fg=COLORS["danger"])
        self.entry.config(state="disabled" if blocked else "normal")
        self._sync_rows()

    def _wrap(self) -> int:
        return int(max(300, self.area.canvas.winfo_width()) * 0.6) // 40 * 40     # 取整: 窗口小幅拖动不会引起重画

    def _signature(self):
        c = self.comm
        on = bool(c.cfg.get("show_avatar"))
        marks = tuple((w, c.avatar_path(w), os.path.getmtime(c.avatar_path(w)) if c.avatar_path(w) else 0)
                      for w in ("me", "ai")) if on else ()
        return (self._wrap(), on, marks, c.persona_name() if on else "", self.username() if on else "")

    def _photo(self, path: str, size: int):
        try:
            key = (path, size, os.path.getmtime(path))
            ph = self._photos.get(key)
            if ph is None:
                ph = self._photos[key] = tk.PhotoImage(file=avatar_mod.sized(path, size))
            return ph
        except (OSError, tk.TclError):
            return None

    def _avatar(self, parent, text: str, mine: bool):
        size = config.scaled(34)
        img = self.comm.avatar_path("me" if mine else "ai")
        photo = self._photo(img, size) if img else None
        if photo is not None:
            return tk.Label(parent, image=photo, bg=COLORS["bg"], bd=0)
        cv = tk.Canvas(parent, width=size, height=size, bg=COLORS["bg"], highlightthickness=0)
        cv.create_oval(1, 1, size - 1, size - 1, outline=COLORS["border"],
                       fill=COLORS.get("accent_fill", COLORS["accent"]) if mine else COLORS["bg_card"])
        cv.create_text(size // 2, size // 2, text=text,
                       fill=COLORS.get("on_accent_fill", COLORS["on_accent"]) if mine else COLORS["text"],
                       font=config.font("body", bold=True))
        return cv

    def _pad_height(self) -> int:
        return max(40, int(self.area.canvas.winfo_height() * 0.45))      # 上下各留半屏左右的空白: 第一条 / 最后一条也能继续滑过去

    def _ensure_pads(self):
        inner = self.area.inner
        h = self._pad_height()
        if self._top_pad is None or not self._top_pad.winfo_exists():
            self._top_pad = tk.Frame(inner, bg=COLORS["bg"], height=h)
            self._top_pad.pack(fill="x")
            self._bottom_pad = tk.Frame(inner, bg=COLORS["bg"], height=h)
            self._bottom_pad.pack(fill="x")
            self._top_pad.bind("<MouseWheel>", self.area._wheel, add="+")
            self._bottom_pad.bind("<MouseWheel>", self.area._wheel, add="+")
        elif int(self._top_pad.cget("height")) != h:
            self._top_pad.config(height=h)
            self._bottom_pad.config(height=h)

    def _sync_rows(self):
        inner = self.area.inner
        msgs = self.comm.visible()
        ids = [m["id"] for m in msgs]
        sig = self._signature()
        if sig != self._sig or not self._rendered or self._rendered[-1] not in ids \
                or self._rendered[0] not in ids:
            for w in inner.winfo_children():
                w.destroy()
            self._rendered, self._rows, self._top_pad, self._sig = [], [], None, sig
            self._cap = RENDER_MAX + 40
            new = msgs[-RENDER_MAX:]                        # 先画最近的; 往上翻到顶再加载更早的
        else:
            new = msgs[ids.index(self._rendered[-1]) + 1:]
        self._ensure_pads()
        if not new:
            return
        for m in new:
            self._rows.append(self._add_row(m))
            self._rendered.append(m["id"])
        while len(self._rendered) > self._cap:              # 一直聊下去: 顶上最老的行去掉
            self._rows.pop(0).destroy()
            self._rendered.pop(0)
        self._to_end()

    def _check_top(self):
        """滑到最上面 (第一条已经在视野里) 了: 再往前加载一批更早的消息, 视野不跳."""
        if not self._rendered or self._top_pad is None:
            return
        canvas, inner = self.area.canvas, self.area.inner
        total = max(1, inner.winfo_height())
        if canvas.yview()[0] * total > self._pad_height() - 4:
            return
        msgs = self.comm.visible()
        ids = [m["id"] for m in msgs]
        if self._rendered[0] not in ids:
            return
        idx = ids.index(self._rendered[0])
        older = msgs[max(0, idx - LOAD_MORE):idx]
        if not older:
            return
        top = canvas.yview()[0] * total
        for m in reversed(older):
            self._rows.insert(0, self._add_row(m, before=self._rows[0]))
            self._rendered.insert(0, m["id"])
        self._cap += len(older)
        inner.update_idletasks()
        canvas.yview_moveto((top + (inner.winfo_height() - total)) / max(1, inner.winfo_height()))

    def _add_row(self, m, before=None):
        c = self.comm
        bg = COLORS["bg"]
        avatars = bool(c.cfg.get("show_avatar"))
        mine = m["role"] == "me"
        row = tk.Frame(self.area.inner, bg=bg)
        if before is not None:
            row.pack(fill="x", pady=4, padx=4, before=before)
        else:
            row.pack(fill="x", pady=4, padx=4, before=self._bottom_pad)
        side = "right" if mine else "left"
        if avatars:
            char = (self.username() or "我")[:1] if mine else (comm_mod.persona_title(c.persona_name())[:1] or "?")
            self._avatar(row, char, mine).pack(side=side, anchor="n", padx=6)
        box = tk.Frame(row, bg=bg)
        box.pack(side=side)
        if mine:
            fill, fg, rim = COLORS.get("accent_fill", COLORS["accent"]), COLORS.get("on_accent_fill", COLORS["on_accent"]), None
        else:
            fill, fg, rim = COLORS["bg_card"], COLORS["text"], COLORS["border"]
        tk.Label(box, text=m["text"], bg=fill, fg=fg, font=config.font("body"), justify="left",
                 anchor="w", wraplength=self._wrap(), padx=12, pady=8,
                 highlightthickness=1 if rim else 0, highlightbackground=rim or fill).pack(anchor="e" if mine else "w")
        try:
            when = datetime.fromisoformat(m["at"]).strftime("%H:%M")
        except (KeyError, ValueError):
            when = ""
        tk.Label(box, text=when, bg=bg, fg=COLORS["text_dim"], font=config.font("small")).pack(
            anchor="e" if mine else "w")
        self._bind_wheel(row)
        return row

    def _bind_wheel(self, w):
        if not getattr(w, "_oit_wheel", False):
            w.bind("<MouseWheel>", self.area._wheel, add="+")
            w._oit_wheel = True
        for ch in w.winfo_children():
            self._bind_wheel(ch)

    def _to_end(self):
        """最后一条贴着视野底部 (下面还有空白可以继续往下滑)."""
        def go():
            canvas, inner = self.area.canvas, self.area.inner
            total = max(1, inner.winfo_height())
            canvas.yview_moveto(max(0.0, (total - self._pad_height() - canvas.winfo_height()) / total))
        self.after_idle(go)

    # ---- 操作 ----

    def _on_return(self, event):
        if event.state & 0x1:                  # Shift+Enter: 换行
            return None
        self._send()
        return "break"

    def _report(self, why: str):
        """不能发的原因: 提示; 是没配置好的(没填 API / 没选人设), 直接带去 设置 → 沟通."""
        toast.warn(tr(why))
        fixable = why in NEEDS_SETUP or (not self.comm.blocked() and any(k in why for k in ("API", "Base URL", "模型")))
        if fixable and self.on_open_settings:
            self.on_open_settings()

    def _send(self):
        text = self.entry.get("1.0", "end").strip()
        why = self.comm.send(text)             # "/clear" 在 send 里处理: 清空记录; 确定过的 📎 附加数据一起发
        if why == "empty":
            return
        if why:
            self._report(why)
            return
        self.entry.delete("1.0", "end")
        self.refresh(force=True)

    def _clear(self):
        if not self.comm.messages:
            return
        if toast.ask(tr("清空聊天记录?"), title=tr("清空"), danger=True):
            self.comm.clear()
            self.refresh(force=True)

    # ---- 📎 发送数据: 统计的几页, 日历 (选类别), 合约的词条 ----

    def _attach(self):
        dlg = Modal(self.winfo_toplevel(), tr("发送数据"), width=config.scaled(320), height=config.scaled(450))
        bg = COLORS["bg"]
        vars_ = {}

        def group(title, items, prefix):
            tk.Label(dlg.body, text=tr(title), bg=bg, fg=COLORS["text"], font=config.font("body", bold=True),
                     anchor="w").pack(fill="x", pady=(8, 2))
            for key, text in items:
                cur = self.comm.attach
                v = tk.BooleanVar(value=bool(cur.get("contract")) if prefix == "contract" else key in cur.get(prefix, []))
                vars_[(prefix, key)] = v
                tk.Checkbutton(dlg.body, text=tr(text), variable=v, bg=bg, fg=COLORS["text"], selectcolor=COLORS["bg_card"],
                               activebackground=bg, activeforeground=COLORS["text"], font=config.font("body"),
                               anchor="w").pack(fill="x", padx=8)

        group("📊  统计", comm_data.STATS_KEYS, "stats")
        group("📅  日历", comm_data.CAL_KEYS, "cal")
        group("⚔  合约", (("terms", "合约词条"),), "contract")

        def go():
            sel = {"stats": [k for (p, k), v in vars_.items() if p == "stats" and v.get()],
                   "cal": [k for (p, k), v in vars_.items() if p == "cal" and v.get()],
                   "contract": vars_[("contract", "terms")].get()}
            self.comm.set_attach(sel)          # 不立刻发: 记下来, 随下一条消息一起发
            dlg.finish(True)
            self.refresh(force=True)

        ttk.Button(dlg.buttons, text=tr("确定"), style="Accent.TButton", command=go).pack(side="right")
        ttk.Button(dlg.buttons, text=tr("取消"), command=dlg.cancel).pack(side="right", padx=(0, 8))
        dlg.wait()
