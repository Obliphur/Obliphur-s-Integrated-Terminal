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
from .widgets import Modal, ScrollArea, cfg, focus_name

# 这些原因是"还没配置好", 提示之后直接带去 设置 → 沟通
NEEDS_SETUP = ("还没有选人设",)
RENDER_MAX = 80          # 打开聊天页时画最近多少条 (全部画出来要等一阵); 往上翻到顶会再接着加载更早的
LOAD_MORE = 40           # 每次往上翻到顶, 多加载多少条


class CommPage(tk.Frame):

    def __init__(self, master, comm, username=None, on_open_settings=None, on_joint=None):
        super().__init__(master, bg=COLORS["bg"])
        self.comm = comm
        self.username = username or (lambda: "")
        self.on_open_settings = on_open_settings
        self.on_joint = on_joint            # 🤝 联合行动: 界面移到合约页选词条 (app._joint_pick_start)
        self._ver = -1
        self._blocked = ""
        self._resize_job = None
        self._rendered = []          # 已经画出来的消息 id (从旧到新)
        self._rows = []              # 对应的行控件
        self._cap = RENDER_MAX + 40  # 最多同时画多少行
        self._top_pad = self._bottom_pad = None
        self._first_draw = True
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
        # 聊天对象: 换了就换成那个对象自己的聊天记录 (各聊各的)
        self.var_who = tk.StringVar()
        self._who_labels = []
        self.cb_who = ttk.Combobox(hdr, textvariable=self.var_who, state="readonly", width=16)
        self.cb_who.pack(side="right", padx=(0, 12))
        self.cb_who.bind("<<ComboboxSelected>>", lambda _e: self._pick_who())
        # 对方现在在做什么 (按人设日程; 每秒刷新, 没有选人设时不显示): 小圆点的颜色跟着状态变
        self.other_row = tk.Frame(self, bg=bg)
        self.other_row.pack(fill="x", padx=30, pady=(0, 2))
        self.lbl_other_dot = tk.Label(self.other_row, text="", bg=bg, fg=COLORS["text_dim"], font=config.font("small"))
        self.lbl_other_dot.pack(side="left")
        self.lbl_other = tk.Label(self.other_row, text="", bg=bg, fg=COLORS["text_dim"], font=config.font("small"), anchor="w")
        self.lbl_other.pack(side="left", padx=(4, 0))
        # 模型: 和 AI 名单建议用同一份模型设置, 选了两边都生效
        self.var_model = tk.StringVar()
        self.cb_model = ttk.Combobox(hdr, textvariable=self.var_model, width=22)
        self.cb_model.pack(side="right", padx=(0, 8))
        self.cb_model.bind("<<ComboboxSelected>>", lambda _e: self._pick_model())
        self.cb_model.bind("<Return>", lambda _e: self._pick_model())
        self.btn_models = ttk.Button(hdr, text="↻", width=3, command=self._fetch_models)
        self.btn_models.pack(side="right", padx=(0, 2))

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
        ttk.Button(tools, text="❓", width=3, command=self._help).pack(side="left")                # 帮助: 每个按钮是干什么的
        self.btn_mute = ttk.Button(tools, text="🔔", width=3, command=self._toggle_mute)        # 🔔 可以主动发起 / 🔕 禁止这个人主动发起
        self.btn_mute.pack(side="left", padx=(4, 0))
        ttk.Button(tools, text="🤝", width=3, command=self._joint).pack(side="left", padx=(4, 0))   # 联合行动
        self.btn_alarm = ttk.Button(tools, text="⏲", width=3, command=self._co_alarm)           # ⏰ 现在的对象是共时闹钟 / ⏲ 不是
        self.btn_alarm.pack(side="left", padx=(4, 0))
        self.btn_shift = ttk.Button(tools, text="🕰", width=3, command=self._shift)              # 起点平移: 请求对方把日程起点换到 4:00-9:00 之间 (⏳ = 等对方回复)
        self.btn_shift.pack(side="left", padx=(4, 0))
        # share data: 打开后离开沟通页, 在别的页面里对方能看到那一页上你们的数据, 页面下方有聊天悬浮窗; 回到沟通自动关闭
        self.btn_share = ttk.Button(tools, text="🙈", width=3, command=self._toggle_share)         # 👀 share data 开着 / 🙈 关着
        self.btn_share.pack(side="left", padx=(4, 0))
        self.btn_ocr = ttk.Button(tools, text="🖼", width=3, command=self._ocr)                    # 识别字体: 选一张图片, 把上面的文字放进聊天框 (不能发送图片)
        self.btn_ocr.pack(side="left", padx=(4, 0))
        # 对方发了 [share data]: 等用户点 接受 / 拒绝
        self.share_btns = tk.Frame(tools, bg=bg)
        self.lbl_share_inv = tk.Label(self.share_btns, text="", bg=bg, fg=COLORS["accent"], font=config.font("small"))
        self.lbl_share_inv.pack(side="left")
        ttk.Button(self.share_btns, text=tr("接受"), width=6, command=lambda: self._share_respond(True)).pack(side="left", padx=(6, 0))
        ttk.Button(self.share_btns, text=tr("拒绝"), width=6, command=lambda: self._share_respond(False)).pack(side="left", padx=(4, 0))
        # 联合行动: 选好的 (还没发, 带 ✕ 可以取消) 或发出去之后的进展; 结束时间自动算好写在这里 (聊天框上方)
        self.lbl_joint = tk.Label(tools, text="", bg=bg, fg=COLORS["accent"], font=config.font("small"))
        self.lbl_joint.pack(side="left", padx=(8, 0))
        self.btn_joint_x = tk.Label(tools, text="✕", bg=bg, fg=COLORS["text_dim"], font=config.font("small"), cursor="hand2")
        self.btn_joint_x.bind("<Button-1>", lambda _e: self.comm.clear_joint_draft())
        # 对方发起的联合行动: 等用户点 接受 / 拒绝 (只有 invite 的时候显示)
        self.joint_btns = tk.Frame(tools, bg=bg)
        ttk.Button(self.joint_btns, text=tr("接受"), width=6, command=lambda: self._joint_respond(True)).pack(side="left")
        ttk.Button(self.joint_btns, text=tr("拒绝"), width=6, command=lambda: self._joint_respond(False)).pack(side="left", padx=(4, 0))
        # 对方向你告白 (特殊空闲时由 OIT 判定): 等用户点 接受 / 拒绝 (拒绝后 10 天内不再判定)
        self.love_btns = tk.Frame(tools, bg=bg)
        self.lbl_love_inv = tk.Label(self.love_btns, text="", bg=bg, fg=COLORS["accent"], font=config.font("small"))
        self.lbl_love_inv.pack(side="left")
        ttk.Button(self.love_btns, text=tr("接受"), width=6, command=lambda: self._love_respond(True)).pack(side="left", padx=(6, 0))
        ttk.Button(self.love_btns, text=tr("拒绝"), width=6, command=lambda: self._love_respond(False)).pack(side="left", padx=(4, 0))

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

    def _pick_who(self):
        label = self.var_who.get()
        name = next((n for n, lab in self._who_labels if lab == label), "")
        if name and name != self.comm.persona_name():
            self.comm.set_cfg(persona=name)
            self.refresh(force=True)
            self.entry.focus_set()

    def _pick_model(self):
        self.comm.set_model(self.var_model.get())

    def _fetch_models(self):
        """向服务商取模型列表 (后台线程), 填进下拉框."""
        import ai_assist
        prof = self.comm.ai.profile()
        self.btn_models.config(state="disabled")

        def work():
            try:
                res = ("ok", ai_assist.list_models(prof))
            except ai_assist.AIError as e:
                res = ("err", e.message)
            except Exception as e:      # noqa: BLE001
                res = ("err", type(e).__name__)
            try:
                self.after(0, lambda: done(res))
            except Exception:
                pass

        def done(res):
            if not self.winfo_exists():
                return
            self.btn_models.config(state="normal")
            if res[0] == "ok":
                self.cb_model.config(values=res[1])
                toast.success(tr("取到 {n} 个模型.").format(n=len(res[1])))
            else:
                toast.warn(tr(res[1]))
        import threading
        threading.Thread(target=work, daemon=True).start()

    def _sync_status(self):
        """状态行: 不能用的原因 > 出错 (每秒看一次, 不用重画消息)."""
        c = self.comm
        blocked = c.blocked()
        text = blocked or (tr(c.error) if c.error else "")
        cfg(self.lbl_status, text=text, fg=COLORS["danger"] if (blocked or c.error) else COLORS["text_dim"])
        other, kind = c.other_status_display()
        cfg(self.lbl_other_dot, text="●" if other else "", fg=comm_mod.STATUS_COLORS.get(kind, COLORS["text_dim"]))
        cfg(self.lbl_other, text=other)

    def refresh(self, force: bool = False):
        c = self.comm
        self._resize_job = None
        self._sync_status()
        if focus_name(self.cb_model) not in (str(self.cb_model), "popdown"):          # 正在选模型 (下拉框开着) 时不动它
            options = c.model_options()
            if list(self.cb_model.cget("values")) != options:
                self.cb_model.config(values=options)
            if self.var_model.get() != c.current_model():
                self.var_model.set(c.current_model())
        blocked = c.blocked()
        if not force and blocked == self._blocked and c.version == self._ver and self._sig == self._signature():
            return
        self._ver = c.version
        self._blocked = blocked
        self._who_labels = comm_mod.persona_labels(comm_mod.list_personas(c.personas))
        labels = [lab for _n, lab in self._who_labels]
        if list(self.cb_who.cget("values")) != labels:
            self.cb_who.config(values=labels)
        cur = next((lab for n, lab in self._who_labels if n == c.persona_name()), "")
        if self.var_who.get() != cur:
            self.var_who.set(cur)
        cfg(self.btn_mute, text="🔕" if c.is_muted() else "🔔")
        cfg(self.btn_alarm, text="⏰" if c.co_alarm_pid() and c.co_alarm_pid() == c.pid else "⏲")
        draft = c.joint_label()
        cfg(self.lbl_joint, text=draft or c.joint_status_text(), fg=COLORS["accent"] if draft else COLORS["text_dim"])
        if draft:
            if self.btn_joint_x.winfo_manager() != "pack":
                self.btn_joint_x.pack(side="left", padx=(6, 0))
        elif self.btn_joint_x.winfo_manager() == "pack":
            self.btn_joint_x.pack_forget()
        if c.joint_invite():
            if self.joint_btns.winfo_manager() != "pack":
                self.joint_btns.pack(side="left", padx=(8, 0))
        elif self.joint_btns.winfo_manager() == "pack":
            self.joint_btns.pack_forget()
        if c.love_invite():
            cfg(self.lbl_love_inv, text=tr("{name} 向你告白", name=c.share_ai_name()))
            if self.love_btns.winfo_manager() != "pack":
                self.love_btns.pack(side="left", padx=(8, 0))
        elif self.love_btns.winfo_manager() == "pack":
            self.love_btns.pack_forget()
        cfg(self.btn_share, text="👀" if c.share_on() else ("⏳" if c.share_asking() else "🙈"))      # ⏳ = 等对方同意
        cfg(self.btn_shift, text="⏳" if c.shift_asking() else "🕰")
        if c.share_invite():
            cfg(self.lbl_share_inv, text=tr("{name} 想看你们的数据", name=c.share_ai_name()))
            if self.share_btns.winfo_manager() != "pack":
                self.share_btns.pack(side="left", padx=(8, 0))
        elif self.share_btns.winfo_manager() == "pack":
            self.share_btns.pack_forget()
        self.entry.config(state="disabled" if blocked else "normal")
        self._sync_rows()

    def _wrap(self) -> int:
        return int(max(300, self.area.canvas.winfo_width()) * 0.6) // 40 * 40     # 取整: 窗口小幅拖动不会引起重画

    def _signature(self):
        c = self.comm
        on = bool(c.cfg.get("show_avatar"))
        marks = tuple((w, c.avatar_path(w), os.path.getmtime(c.avatar_path(w)) if c.avatar_path(w) else 0)
                      for w in ("me", "ai")) if on else ()
        return (self._wrap(), on, marks, c.pid, self.username() if on else "")

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
            self._first_draw = True
            self._cap = RENDER_MAX + 40
            new = msgs[-RENDER_MAX:]                        # 先画最近的; 往上翻到顶再加载更早的
        elif ids[ids.index(self._rendered[0]):ids.index(self._rendered[-1]) + 1] != self._rendered:
            for w in inner.winfo_children():                # 别的设备的消息插在已经画出的中间: 重画
                w.destroy()
            self._rendered, self._rows, self._top_pad = [], [], None
            self._first_draw = True
            new = msgs[-RENDER_MAX:]
        else:
            new = msgs[ids.index(self._rendered[-1]) + 1:]
        self._ensure_pads()
        if not new:
            return
        follow = self._first_draw or self._near_end() or any(m["role"] == "me" for m in new)
        for m in new:
            self._rows.append(self._add_row(m))
            self._rendered.append(m["id"])
        while len(self._rendered) > self._cap:              # 一直聊下去: 顶上最老的行去掉
            self._rows.pop(0).destroy()
            self._rendered.pop(0)
        self._first_draw = False
        if follow:
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

    def _near_end(self) -> bool:
        """视野离最新一条不远 (3 屏之内): 新消息来了才自动滑下去; 正在往上翻很早的记录就不打断."""
        canvas = self.area.canvas
        box = canvas.bbox("all")
        if not box or not self._rendered:
            return True
        total = max(1, box[3] - box[1])
        bottom = canvas.yview()[1] * total
        return bottom >= total - self._pad_height() - 3 * max(1, canvas.winfo_height())

    def _to_end(self):
        """最后一条贴着视野底部 (下面还有空白可以继续往下滑). 新行的高度要等排完版才知道, 所以先排版再算, 稍后再对一次."""
        def go():
            canvas, inner = self.area.canvas, self.area.inner
            inner.update_idletasks()
            box = canvas.bbox("all")
            if not box:
                return
            canvas.configure(scrollregion=box)
            total = max(1, box[3] - box[1])
            canvas.yview_moveto(max(0.0, (total - self._pad_height() - canvas.winfo_height()) / total))
        self.after_idle(go)
        self.after(80, go)

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
        why = self.comm.send(text)             # "/clear" 在 send 里处理: 清空记录
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

    # ---- share data ----
    def _toggle_share(self):
        why = self.comm.share_enable(not (self.comm.share_on() or self.comm.share_asking()))      # 等对方同意的时候再点 = 取消
        if why:
            toast.warn(tr(why))
        self.refresh(force=True)

    # ---- 起点平移 ----
    def _shift(self):
        """🕰 起点平移: 请求对方把日程起点 (初始起点) 换到 4:00-9:00 之间的某个时刻, 明天起生效; 对方回 [Yes] / [No]. 等回复的时候再点 = 取消."""
        c = self.comm
        if c.shift_asking():
            c.shift_cancel()
            self.refresh(force=True)
            return
        if not c.pid:
            toast.warn(tr("先选一个聊天对象."))
            return
        cur = c.shift_start()
        if cur is None:
            toast.warn(tr("这个人设没有日程文件, 不能平移起点"))
            return
        bg = COLORS["bg"]
        width = config.scaled(460)
        dlg = Modal(self.winfo_toplevel(), tr("起点平移"), width=width, height=config.scaled(300))
        wrap = width - config.scaled(60)
        tk.Label(dlg.body, text=tr("请求对方把日程的起点平移到新的时刻 (4:00 - 9:00). 对方同意后明天起生效, 今天的日程不变; "
                                   "只记在 OIT 里, 不会修改人设文件. 对方可以拒绝."),
                 bg=bg, fg=COLORS["text_dim"], font=config.font("small"), justify="left", anchor="w", wraplength=wrap).pack(fill="x")
        tk.Label(dlg.body, text=tr("明天的起点: {t}", t=comm_mod.hm_text(cur)), bg=bg, fg=COLORS["text"],
                 font=config.font("body"), anchor="w").pack(fill="x", pady=(10, 4))
        row = tk.Frame(dlg.body, bg=bg)
        row.pack(fill="x")
        lo_h, hi_h = comm_mod.SHIFT_LO // 60, comm_mod.SHIFT_HI // 60
        var_h = tk.StringVar(value=str(min(hi_h, max(lo_h, cur // 60))))
        var_m = tk.StringVar(value=f"{cur % 60:02d}")
        ttk.Spinbox(row, from_=lo_h, to=hi_h, textvariable=var_h, width=4, wrap=False).pack(side="left")
        tk.Label(row, text=":", bg=bg, fg=COLORS["text"], font=config.font("body", bold=True)).pack(side="left", padx=4)
        ttk.Spinbox(row, from_=0, to=59, textvariable=var_m, width=4, format="%02.0f", wrap=True).pack(side="left")

        def go():
            try:
                mins = int(var_h.get()) * 60 + int(var_m.get())
            except ValueError:
                toast.warn(tr("时间不对"))
                return
            why = c.shift_request(mins)
            if why:
                toast.warn(tr(why))
                return
            toast.info(tr("已向 {name} 提出请求, 等待回复", name=c.share_ai_name()))
            dlg.finish(True)

        ttk.Button(dlg.buttons, text=tr("请求"), style="Accent.TButton", command=go).pack(side="right")
        ttk.Button(dlg.buttons, text=tr("取消"), command=lambda: dlg.finish(False)).pack(side="right", padx=(0, 8))
        dlg.update_idletasks()
        dlg._center(self.winfo_toplevel(), width, dlg.winfo_reqheight())
        dlg.wait()
        self.refresh(force=True)

    def _love_respond(self, accept: bool):
        why = self.comm.love_respond(accept)
        if why:
            toast.warn(tr(why))
        self.refresh(force=True)

    def _share_respond(self, accept: bool):
        why = self.comm.share_respond(accept)
        if why:
            toast.warn(tr(why))
        self.refresh(force=True)

    def _toggle_mute(self):
        """禁止 / 允许这个人主动发起对话 (主动发消息和追问都不发); 没选人的时候不动."""
        if self.comm.pid:
            self.comm.set_muted(not self.comm.is_muted())
            self.refresh(force=True)

    def _co_alarm(self):
        """⏰ 共时闹钟: 闹钟的时间跟随这个对象的实际起床时间, 手动选响铃的设备."""
        from .alarm_ui import open_co_alarm
        open_co_alarm(self.winfo_toplevel(), self.comm)
        self.refresh(force=True)

    def _ocr(self):
        """🖼 识别字体: 选一张图片, 让模型读出上面的文字, 放进聊天框 (可以再改); 图片本身不会发出去."""
        from tkinter import filedialog
        import ai_assist
        path = filedialog.askopenfilename(
            parent=self.winfo_toplevel(), title=tr("选择要识别文字的图片"),
            filetypes=[(tr("图片"), "*.png *.jpg *.jpeg *.webp *.gif"), (tr("所有文件"), "*.*")])
        if not path:
            return
        self.btn_ocr.config(state="disabled", text="…")
        toast.info(tr("正在识别图片上的文字…"))

        def work():
            try:
                res = ("ok", self.comm.ocr_image(path))
            except ai_assist.AIError as e:
                res = ("err", e.message)
            except Exception as e:      # noqa: BLE001
                res = ("err", type(e).__name__)
            try:
                self.after(0, lambda: done(res))
            except Exception:
                pass

        def done(res):
            if not self.winfo_exists():
                return
            self.btn_ocr.config(state="normal", text="🖼")
            if res[0] != "ok":
                toast.warn(tr(res[1]))
            elif not res[1].strip():
                toast.warn(tr("图片上没有识别到文字."))
            else:
                cur = self.entry.get("1.0", "end-1c")
                self.entry.insert("end", ("\n" if cur.strip() else "") + res[1])
                self.entry.focus_set()
                self.entry.see("end")
                toast.success(tr("文字已放进聊天框."))
        import threading
        threading.Thread(target=work, daemon=True).start()

    def _help(self):
        """帮助: 沟通页上每个小按钮是干什么的, 还有"无言为真"会禁止沟通的提醒."""
        width = config.scaled(460)
        dlg = Modal(self.winfo_toplevel(), tr("帮助"), width=width, height=config.scaled(520))
        bg = COLORS["bg"]
        rows = (("❓", "帮助", "打开这个说明."),
                ("👀 / 🙈", "分享数据 (share data)", "点一下切换 (👀 = 打开; 打开前要先征得对方同意: ⏳ = 等对方回复, 再点一下取消). 打开后去别的页面 (设置 / 沟通 / 指令 / 重置 以外): 对方会收到那一页上你和它自己的数据 (30 秒冷却), "
                                      "页面下方有聊天悬浮窗, 计时器 / 整点记录 / 日历 / 统计页还能切换看自己 / 对方的数据; 回到沟通自动关闭, 对方会总结一次. "
                                      "对方也可以发 [share data] 邀请你, 点「接受」才会打开."),
                ("🔔 / 🔕", "主动发起", "点一下切换: 🔔 = 允许这个对象主动给你发消息 / 追问, 🔕 = 禁止."),
                ("⏰ / ⏲", "共时闹钟", "点开设置: 把这个对象设为共时闹钟后 (⏰), 闹钟的时间跟随它的日程里的实际起床时间 (睡眠中也会响); "
                                    "哪些设备响铃由你手动选, 铃声和音量用「设置 → 闹钟」里的. 一次只有一个对象是共时闹钟."),
                ("🕰", "起点平移", "请求对方把日程的起点平移到新的时刻 (4:00 - 9:00). 对方同意后明天起生效, 今天的日程不变; 只记在 OIT 里, 不会修改人设文件. 对方可以拒绝."),
                ("🖼", "识别字体", "选一张图片, OIT 让模型把图片上的文字读出来, 放进聊天框 (可以再改). 不能发送图片, 只能提取文字; 图片不会被保存, 对方也看不到."),
                ("🤝", "联合行动", "邀请对方一起完成一次合约: 界面会移到合约页, 选好词条后点「确定选择」(原来「开始行动」的位置), "
                                "再输入开始时间; 词条和合约的基础规则会打包发给对方, 结束时间自动算好写在聊天框上方. "
                                "对方回 [Yes] 就按约开始合约; 合约期间你们的专注 / 休息同步, 合约后得到的休息也同步."))
        for icon, name, text in rows:
            f = tk.Frame(dlg.body, bg=bg)
            f.pack(fill="x", pady=(0, 8))
            tk.Label(f, text=icon, bg=bg, fg=COLORS["text"], font=config.font("body", bold=True), width=7, anchor="nw").pack(side="left", anchor="n")
            tk.Label(f, text=tr(name) + ": " + tr(text), bg=bg, fg=COLORS["text"], font=config.font("body"), justify="left",
                     anchor="w", wraplength=width - config.scaled(120)).pack(side="left", fill="x", expand=True)
        tk.Label(dlg.body, text="⚠  " + tr("特别提醒: 合约词条「无言为真」生效时会禁止「沟通」的使用 (不能发消息, 对方的消息也收不到), 合约结束后才能恢复."),
                 bg=bg, fg=COLORS["danger"], font=config.font("body", bold=True), justify="left", anchor="w",
                 wraplength=width - config.scaled(60)).pack(fill="x", pady=(6, 0))
        ttk.Button(dlg.buttons, text=tr("知道了"), style="Accent.TButton", command=lambda: dlg.finish(True)).pack(side="right")
        dlg.update_idletasks()
        dlg._center(self.winfo_toplevel(), width, dlg.winfo_reqheight())
        dlg.wait()

    def _joint_respond(self, accept: bool):
        why = self.comm.joint_respond(accept)
        if why:
            toast.warn(tr(why))

    def _joint(self):
        """🤝 联合行动: 界面移到合约页, 选好词条再回来输入开始时间."""
        if self.on_joint:
            self.on_joint()

    def ask_joint_time(self, terms: list):
        """合约页点了"确定选择"之后: 输入开始时间 (时:分, 已经过了的算明天), 下面实时算出结束时间; 确定后打包成联合行动, 随下一条消息发出."""
        import re
        from datetime import timedelta
        import contract as C
        width = config.scaled(360)
        dlg = Modal(self.winfo_toplevel(), tr("联合行动"), width=width, height=config.scaled(260))
        bg = COLORS["bg"]
        dur = C.duration_of(C.clean_terms(terms, prereq=True))
        now = datetime.now()
        tk.Label(dlg.body, text=tr("合约开始时间 (时:分)"), bg=bg, fg=COLORS["text"], font=config.font("body", bold=True),
                 anchor="w").pack(fill="x")
        var = tk.StringVar(value=(now + timedelta(minutes=30)).strftime("%H:%M"))
        ent = tk.Entry(dlg.body, textvariable=var, font=config.font("body"), bg=COLORS["bg_card"], fg=COLORS["text"],
                       insertbackground=COLORS["text"], relief="flat", highlightthickness=1,
                       highlightbackground=COLORS["border"], highlightcolor=COLORS["accent"])
        ent.pack(fill="x", pady=(4, 8))
        lbl = tk.Label(dlg.body, text="", bg=bg, fg=COLORS["text_dim"], font=config.font("small"), anchor="w", justify="left",
                       wraplength=width - config.scaled(60))
        lbl.pack(fill="x")

        def parse():
            m = re.fullmatch(r"\s*(\d{1,2})\s*[:：]?\s*(\d{2})\s*", var.get())
            if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
                return None
            t = datetime.now()
            start = t.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
            return start if start > t else start + timedelta(days=1)

        def show(*_a):
            start = parse()
            if start is None:
                cfg(lbl, text=tr("请输入 时:分, 例如 14:30"), fg=COLORS["danger"])
                return
            end = start + timedelta(seconds=dur)
            day = "" if start.date() == datetime.now().date() else tr("(明天)") + " "
            cfg(lbl, text=tr("预计 {a} - {b} (合约时长 {d})", a=day + start.strftime("%H:%M"), b=end.strftime("%H:%M"),
                             d=config.fmt_hm(dur)), fg=COLORS["text_dim"])
        var.trace_add("write", show)
        show()

        def go():
            start = parse()
            if start is None:
                return
            why = self.comm.set_joint_draft(terms, start)
            if why:
                toast.warn(tr(why))
                return
            dlg.finish(True)
            self.refresh(force=True)
            self.entry.focus_set()

        ttk.Button(dlg.buttons, text=tr("确定"), style="Accent.TButton", command=go).pack(side="right")
        ttk.Button(dlg.buttons, text=tr("取消"), command=dlg.cancel).pack(side="right", padx=(0, 8))
        ent.bind("<Return>", lambda _e: go())
        ent.focus_set()
        ent.select_range(0, "end")
        dlg.wait()

