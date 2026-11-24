# -*- coding: utf-8 -*-
"""💬 沟通页: 和人设聊天 (逻辑在 comm.py). 左边对方, 右边自己; 头像默认不显示, 在 设置 → 沟通 里打开.

消息是增量画的: 新消息只加一行, 不重画整页 (整页重画会有卡顿感); 宽度 / 头像设置变了才重画."""

import os
import json
import sys
import tkinter as tk
from datetime import datetime
from tkinter import ttk

import avatar as avatar_mod
import ai_assist
import comm as comm_mod
from comm import comm_data
import config
import toast
from config import COLORS
from i18n import tr
from .widgets import Modal, ScrollArea, cfg, focus_name
from .comm_voice import CommVoiceMixin

# 这些原因是"还没配置好", 提示之后直接带去 设置 → 沟通
NEEDS_SETUP = ("还没有选人设",)
RENDER_MAX = 80          # 打开聊天页时画最近多少条 (全部画出来要等一阵); 往上翻到顶会再接着加载更早的
LOAD_MORE = 40           # 每次往上翻到顶, 多加载多少条


class CommPage(CommVoiceMixin, tk.Frame):

    def __init__(self, master, comm, username=None, on_open_settings=None, on_joint=None, on_coread=None,
                 on_coread_end=None):
        super().__init__(master, bg=COLORS["bg"])
        self.comm = comm
        self.username = username or (lambda: "")
        self.on_open_settings = on_open_settings
        self.on_joint = on_joint            # 🤝 联合行动: 界面移到合约页选词条 (app._joint_pick_start)
        self.on_coread = on_coread
        self.on_coread_end = on_coread_end
        self._reading_lock = None
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
        self.lbl_tension = tk.Label(self, text="", bg=bg, fg=COLORS["text_dim"],
                                    font=config.font("small"), anchor="w")
        # AI 配置: 每个聊天对象各用一份 (在 设置 → 模型 里建 / 改)
        self.var_cfg = tk.StringVar()
        self._cfg_ids = []
        self.cb_cfg = ttk.Combobox(hdr, textvariable=self.var_cfg, state="readonly", width=18)
        self.cb_cfg.pack(side="right", padx=(0, 8))
        self.cb_cfg.bind("<<ComboboxSelected>>", lambda _e: self._pick_config())

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
        self.btn_scenario = ttk.Button(tools, text="📱", width=3,
                                       command=lambda: self._set_mode(not self.comm.scenario_on()))
        self.btn_scenario.pack(side="left", padx=(4, 0))
        self.btn_mute = ttk.Button(tools, text="🔔", width=3, command=self._toggle_mute)        # 🔔 可以主动发起 / 🔕 禁止这个人主动发起
        self.btn_mute.pack(side="left", padx=(4, 0))
        ttk.Button(tools, text="🤝", width=3, command=self._joint).pack(side="left", padx=(4, 0))   # 联合行动
        self.btn_alarm = ttk.Button(tools, text="⏲", width=3, command=self._co_alarm)           # ⏰ 现在的对象是共时闹钟 / ⏲ 不是
        self.btn_alarm.pack(side="left", padx=(4, 0))
        self.btn_shift = ttk.Button(tools, text="🕰", width=3, command=self._shift)              # 起点平移：范围由人设模板声明 (⏳ = 等对方回复)
        self.btn_shift.pack(side="left", padx=(4, 0))
        # share data: 打开后离开沟通页, 在别的页面里对方能看到那一页上你们的数据, 页面下方有聊天悬浮窗; 回到沟通自动关闭
        self.btn_share = ttk.Button(tools, text="🙈", width=3, command=self._toggle_share)         # 👀 share data 开着 / 🙈 关着
        self.btn_share.pack(side="left", padx=(4, 0))
        self.btn_ocr = ttk.Button(tools, text="🖼", width=3, command=self._ocr)                    # 独立识字, 图片不进入聊天消息
        self.btn_ocr.pack(side="left", padx=(4, 0))
        self.btn_image = ttk.Button(tools, text="📎", width=3, command=self._pick_image)
        self.btn_image.pack(side="left", padx=(4, 0))
        self.btn_review = ttk.Button(tools, text="🎼", width=3, command=self._review_performance)
        self.btn_review.pack(side="left", padx=(4, 0))
        self.btn_coread = ttk.Button(tools, text="📚", width=3, command=self._reading)
        self.btn_coread.pack(side="left", padx=(4, 0))
        self.coread_btns = tk.Frame(tools, bg=bg)
        self.lbl_coread_inv = tk.Label(self.coread_btns, text="", bg=bg, fg=COLORS["accent"], font=config.font("small"))
        self.lbl_coread_inv.pack(side="left")
        ttk.Button(self.coread_btns, text=tr("接受"), width=6,
                   command=lambda: self._coread_respond(True)).pack(side="left", padx=(6, 0))
        ttk.Button(self.coread_btns, text=tr("拒绝"), width=6,
                   command=lambda: self._coread_respond(False)).pack(side="left", padx=(4, 0))
        self._pending_image = ""
        # 对方发了 [share data]: 等用户点 接受 / 拒绝
        self.share_btns = tk.Frame(tools, bg=bg)
        self.lbl_share_inv = tk.Label(self.share_btns, text="", bg=bg, fg=COLORS["accent"], font=config.font("small"))
        self.lbl_share_inv.pack(side="left")
        ttk.Button(self.share_btns, text=tr("接受"), width=6, command=lambda: self._share_respond(True)).pack(side="left", padx=(6, 0))
        ttk.Button(self.share_btns, text=tr("拒绝"), width=6, command=lambda: self._share_respond(False)).pack(side="left", padx=(4, 0))
        # 草稿附件和联合行动状态放在工具栏下方。
        draft_row = tk.Frame(self, bg=bg)
        draft_row.pack(fill="x", padx=28, pady=(2, 0))
        self.lbl_joint = tk.Label(draft_row, text="", bg=bg, fg=COLORS["accent"], font=config.font("small"), anchor="w")
        self.lbl_joint.pack(side="left")
        self.btn_joint_x = tk.Label(draft_row, text="✕", bg=bg, fg=COLORS["text_dim"], font=config.font("small"), cursor="hand2")
        self.btn_joint_x.bind("<Button-1>", lambda _e: self._clear_package_drafts())
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
        self.entry.bind("<<Paste>>", self._paste_image)

    # ---- 显示 ----

    def show(self):
        self.comm.set_viewing(True)
        self.refresh(force=True)
        self.entry.focus_set()
        self._to_end()
        self._voice_start()
        self._voice_job = self.after(100, self._voice_poll)

    def hide(self):
        self._voice_cancel()
        self.comm.set_viewing(False)

    def set_reading_mode(self, enabled):
        """阅读网页聊天独占当前人设时, 遮住原沟通页直到结束阅读."""
        enabled = bool(enabled)
        if enabled and self._reading_lock is None:
            lock = self._reading_lock = tk.Frame(self, bg=COLORS["bg"])
            tk.Label(lock, text=tr("正在共读，请在阅读网页里的聊天框继续沟通。"),
                     bg=COLORS["bg"], fg=COLORS["text"], font=config.font("h2", bold=True),
                     wraplength=config.scaled(520)).pack(padx=24, pady=(32, 16))
            ttk.Button(lock, text=tr("结束阅读并恢复沟通"), command=self.on_coread_end or (lambda: None)).pack(pady=(0, 28))
            lock.place(x=0, y=0, relwidth=1, relheight=1)
            lock.lift()
        elif not enabled and self._reading_lock is not None:
            self._reading_lock.destroy()
            self._reading_lock = None
        if enabled:
            self._reading_lock.lift()

    def _reading(self):
        """先选独自阅读或共读, 再交给本机阅读服务打开网页."""
        dlg = Modal(self.winfo_toplevel(), tr("阅读"), width=config.scaled(340), height=config.scaled(180))
        for label, together in (("自己阅读", False), ("发起 coread", True)):
            ttk.Button(dlg.body, text=tr(label), command=lambda value=together: dlg.finish(value)).pack(fill="x", pady=8)
        result = dlg.wait()
        if result is True:
            why = self.comm.toggle_request_draft("coread")
            if why:
                toast.warn(tr(why))
            self.refresh(force=True)
        elif result is False and self.on_coread:
            self.on_coread(together=False)

    def _coread_respond(self, accept):
        why = self.comm.coread_offer_respond(accept)
        if why:
            toast.warn(tr(why))
        self.refresh(force=True)

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

    def _pick_config(self):
        label = self.var_cfg.get()
        values = list(self.cb_cfg.cget("values"))
        idx = values.index(label) if label in values else -1
        if 0 <= idx < len(self._cfg_ids):
            self.comm.set_config(self._cfg_ids[idx])

    def _set_mode(self, enabled):
        self.comm.set_scenario(enabled)
        self.refresh(force=True)

    def _sync_status(self):
        """状态行: 不能用的原因 > 出错 (每秒看一次, 不用重画消息)."""
        c = self.comm
        blocked = c.blocked()
        text = blocked or (tr(c.error) if c.error else "")
        cfg(self.lbl_status, text=text, fg=COLORS["danger"] if (blocked or c.error) else COLORS["text_dim"])
        other, kind = c.other_status_display()
        cfg(self.lbl_other_dot, text="●" if other else "", fg=comm_mod.STATUS_COLORS.get(kind, COLORS["text_dim"]))
        cfg(self.lbl_other, text=other)
        tension = c.tension_display(config.COMM_SHOW_TENSION)
        cfg(self.lbl_tension, text=tension)
        if tension and not self.lbl_tension.winfo_manager():
            self.lbl_tension.pack(fill="x", padx=30, pady=(0, 2), after=self.other_row)
        elif not tension and self.lbl_tension.winfo_manager():
            self.lbl_tension.pack_forget()
        scenario = c.scenario_on()
        cfg(self.btn_scenario, text="🎭" if scenario else "📱")

    def refresh(self, force: bool = False):
        c = self.comm
        self._resize_job = None
        self._sync_status()
        if focus_name(self.cb_cfg) not in (str(self.cb_cfg), "popdown"):          # 正在选配置 (下拉框开着) 时不动它
            options = c.config_options()
            labels = [o["name"] for o in options]
            self._cfg_ids = [o["id"] for o in options]
            if list(self.cb_cfg.cget("values")) != labels:
                self.cb_cfg.config(values=labels)
            cur = next((o["name"] for o in options if o["id"] == c.current_config()), "")
            if self.var_cfg.get() != cur:
                self.var_cfg.set(cur)
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
        requests = c.request_draft_labels()
        img_label = self._pending_image_label()
        pending = " · ".join(([draft] if draft else []) + requests + ([img_label] if img_label else []))
        cfg(self.lbl_joint, text=pending or c.joint_status_text(), fg=COLORS["accent"] if pending else COLORS["text_dim"])
        if pending:
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
        if c.coread_offer():
            cfg(self.lbl_coread_inv, text=tr("📚 {name} 想和你共同阅读", name=c.share_ai_name()))
            if self.coread_btns.winfo_manager() != "pack":
                self.coread_btns.pack(side="left", padx=(8, 0))
        elif self.coread_btns.winfo_manager() == "pack":
            self.coread_btns.pack_forget()
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
        return (self._wrap(), on, marks, c.pid, self.username() if on else "", c.voice.data["enabled"])

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
            lab = tk.Label(parent, image=photo, bg=COLORS["bg"], bd=0,
                           highlightthickness=1, highlightbackground=COLORS["border"])
            if not mine:
                lab.configure(cursor="hand2")
                lab.bind("<Button-1>", lambda e: self._show_avatar())
            return lab
        cv = tk.Canvas(parent, width=size, height=size, bg=COLORS["bg"], highlightthickness=0)
        cv.create_rectangle(1, 1, size - 1, size - 1, outline=COLORS["border"],
                            fill=COLORS.get("accent_fill", COLORS["accent"]) if mine else COLORS["bg_card"])
        cv.create_text(size // 2, size // 2, text=text,
                       fill=COLORS.get("on_accent_fill", COLORS["on_accent"]) if mine else COLORS["text"],
                       font=config.font("body", bold=True))
        return cv

    def _show_avatar(self):
        """点对方的头像: 弹窗显示完整原图 (不裁, 等比缩到屏幕放得下; 老头像没有完整图就显示方形那份)."""
        c = self.comm
        cur = c.avatar_path("ai")
        if not cur:
            return
        p = avatar_mod.full_path(c.avatar_dest("ai"))
        if not os.path.isfile(p):
            p = cur
        max_side = max(200, min(self.winfo_screenwidth() - 160, self.winfo_screenheight() - 220))
        try:
            p = avatar_mod.fit(p, max_side)
            iw, ih = avatar_mod.image_size(p)
            photo = tk.PhotoImage(file=p)
        except (OSError, tk.TclError):
            return
        dlg = Modal(self.winfo_toplevel(), tr("头像"), width=iw + 48, height=ih + 104)
        tk.Label(dlg.body, image=photo, bg=COLORS["bg"], bd=0).pack(expand=True)
        dlg._photo = photo                                  # 别让图片被回收
        ttk.Button(dlg.buttons, text=tr("关闭"), command=dlg.cancel).pack(side="right")
        dlg.wait()

    def _show_image(self, path: str):
        """点开聊天记录里的图片: 弹窗显示完整大图 (不裁); GDI+ 打不开的格式 (如 WEBP) 交给系统查看."""
        if not path or not os.path.isfile(path):
            return
        max_side = max(200, min(self.winfo_screenwidth() - 160, self.winfo_screenheight() - 220))
        try:
            fitted = avatar_mod.fit(path, max_side)
            iw, ih = avatar_mod.image_size(fitted)
            photo = tk.PhotoImage(file=fitted)
        except (OSError, tk.TclError):
            if sys.platform == "win32":
                try:
                    os.startfile(path)
                except OSError:
                    pass
            return
        dlg = Modal(self.winfo_toplevel(), tr("图片"), width=iw + 48, height=ih + 104)
        tk.Label(dlg.body, image=photo, bg=COLORS["bg"], bd=0).pack(expand=True)
        dlg._photo = photo
        ttk.Button(dlg.buttons, text=tr("关闭"), command=dlg.cancel).pack(side="right")
        dlg.wait()

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
        previous_ids = getattr(self, "_visible_ids", set(ids))
        self._visible_ids = set(ids)
        # 等待中的回复可能排在后来发送的用户消息之前，到点后也要补出整个气泡。
        new_before_view = bool(self._rendered and self._rendered[0] in ids and
                               any(mid not in previous_ids for mid in ids[:ids.index(self._rendered[0])]))
        sig = self._signature()
        if sig != self._sig or not self._rendered or self._rendered[-1] not in ids \
                or self._rendered[0] not in ids or new_before_view:
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
        if follow and not getattr(self, "_voice_queue", []) and not getattr(self, "_voice_active", False):
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
        bubble = tk.Frame(box, bg=fill, highlightthickness=1 if rim else 0, highlightbackground=rim or fill)
        bubble.pack(anchor="e" if mine else "w")
        img_name = str(m.get("img") or "").strip()
        img_path = c.chat_image_path(img_name) if img_name else ""
        thumb_loaded = False
        if img_path and os.path.isfile(img_path):
            thumb_side = config.scaled(220)
            key = f"msg:{m.get('uid') or m.get('id')}:{thumb_side}"
            photo = self._photos.get(key)
            if photo is None:
                try:
                    thumb_path = avatar_mod.fit(img_path, thumb_side)
                    photo = self._photos[key] = tk.PhotoImage(file=thumb_path)
                except (OSError, tk.TclError):
                    photo = None
            if photo is not None:
                thumb_loaded = True
                img_lab = tk.Label(bubble, image=photo, bg=fill, bd=0, cursor="hand2")
                img_lab.pack(anchor="e" if mine else "w", padx=6, pady=(6, 4 if m["text"] != "[图片]" else 6))
                img_lab.bind("<Button-1>", lambda _e, p=img_path: self._show_image(p))
        if not thumb_loaded or m["text"] != "[图片]":
            text_label = tk.Label(bubble, text=m["text"], bg=fill, fg=fg, font=config.font("body"), justify="left",
                                  anchor="w", wraplength=self._wrap(), padx=12, pady=8,
                                  highlightthickness=0)
            text_label.pack(anchor="e" if mine else "w")
            if not thumb_loaded and img_path and os.path.isfile(img_path):
                text_label.config(cursor="hand2")
                text_label.bind("<Button-1>", lambda _e, p=img_path: self._show_image(p))
        if not mine and c.voice.data["enabled"]:
            tk.Button(bubble, text="▶", command=lambda: self._voice_manual(m), bg=fill, fg=COLORS["text_dim"],
                      activebackground=fill, activeforeground=COLORS["accent"], relief="flat", bd=0,
                      padx=3, pady=0, cursor="hand2", font=config.font("small"), takefocus=True).pack(anchor="sw", padx=1, pady=1)
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
        if getattr(self.comm, "reading_mode", False):
            toast.info(tr("正在共读，请在阅读网页里的聊天框继续沟通."))
            return
        text = self.entry.get("1.0", "end").strip()
        image = None
        if self._pending_image:
            try:
                if os.path.getsize(self._pending_image) > ai_assist.OCR_MAX_BYTES:
                    toast.warn(tr("图片太大了 (最大 4.5 MB)"))
                    return
                with open(self._pending_image, "rb") as stream:
                    image = stream.read()
                if not ai_assist.image_mime(image):
                    toast.warn(tr("只支持 PNG / JPG / WEBP / GIF 图片"))
                    return
            except OSError:
                toast.warn(tr("打不开这张图片."))
                return
        why = self.comm.send(text, image=image)             # "/clear" 在 send 里处理: 清空记录
        if why == "empty":
            return
        if why:
            self._report(why)
            return
        self.entry.delete("1.0", "end")
        self._pending_image = ""
        self.refresh(force=True)

    def _pending_image_label(self) -> str:
        if not self._pending_image:
            return ""
        name = os.path.basename(self._pending_image)
        if name.startswith("comm-paste-"):
            return tr("📎 剪贴板图片")
        return f"📎 {name}"

    def _set_pending_image(self, path: str) -> bool:
        """暂存待发送图片；附件按钮始终保持 📎，避免和识字按钮混淆。"""
        try:
            if not os.path.isfile(path):
                return False
            if os.path.getsize(path) > ai_assist.OCR_MAX_BYTES:
                toast.warn(tr("图片太大了 (最大 4.5 MB)"))
                return False
            with open(path, "rb") as stream:
                data = stream.read()
            if not ai_assist.image_mime(data):
                toast.warn(tr("只支持 PNG / JPG / WEBP / GIF 图片"))
                return False
        except OSError:
            toast.warn(tr("打不开这张图片."))
            return False
        self._pending_image = path
        self.btn_image.config(text="📎")
        self.refresh(force=True)
        self.entry.focus_set()
        return True

    def _pick_image(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.winfo_toplevel(), title=tr("选择图片"),
                filetypes=[(tr("图片"), "*.png *.jpg *.jpeg *.webp *.gif"), (tr("所有文件"), "*.*")])
        if path:
            self._set_pending_image(path)

    def _paste_image(self, _event=None):
        try:
            candidates = avatar_mod.clipboard_paths()
            path = next((p for p in candidates if os.path.splitext(p)[1].lower() in
                         (".png", ".jpg", ".jpeg", ".webp", ".gif")), "")
            if path:
                if self._set_pending_image(path):
                    return "break"
            from tempstore import mkstemp
            fd, target = mkstemp("misc", prefix="comm-paste-", suffix=".png")
            os.close(fd)
            os.unlink(target)
            if avatar_mod.clipboard_image(target):
                if not self._set_pending_image(target):
                    try:
                        os.unlink(target)
                    except OSError:
                        pass
                return "break"
        except Exception:
            return None
        return None

    def _review_performance(self):
        """选择参考谱和演奏文件，或录制实时 MIDI，再交给现有评判流程。"""
        from tkinter import filedialog
        dlg = Modal(self.winfo_toplevel(), tr("演奏评判"), width=config.scaled(560), height=config.scaled(280))
        paths = {"reference": "", "performance": ""}
        recorded_midi = None
        body = tk.Frame(dlg.body, bg=COLORS["bg"])
        body.pack(fill="both", expand=True)
        for col, (key, title) in enumerate((("reference", "参考乐谱 / MIDI"), ("performance", "演奏 MIDI / 音频"))):
            frame = tk.Frame(body, bg=COLORS["bg"])
            frame.grid(row=0, column=col, sticky="nsew", padx=(0, 12) if col == 0 else (12, 0))
            body.grid_columnconfigure(col, weight=1)
            tk.Label(frame, text=tr(title), bg=COLORS["bg"], fg=COLORS["text"],
                     font=config.font("body", bold=True)).pack(anchor="w", pady=(4, 8))
            label = tk.Label(frame, text=tr("未选择"), bg=COLORS["bg"], fg=COLORS["text_dim"],
                             font=config.font("small"), anchor="w", wraplength=config.scaled(220), justify="left")
            label.pack(fill="x", pady=(0, 10))
            def choose(k=key, lab=label):
                nonlocal recorded_midi
                exts = "*.mid *.midi *.mp3 *.wav *.flac *.ogg *.png *.jpg *.jpeg *.pdf *.tif *.tiff *.bmp"
                path = filedialog.askopenfilename(parent=dlg, title=tr("选择文件"),
                    filetypes=[(tr("支持的音乐文件"), exts), (tr("所有文件"), "*.*")])
                if path:
                    paths[k] = path
                    if k == "performance":
                        recorded_midi = None
                    lab.config(text=os.path.basename(path))
            ttk.Button(frame, text=tr("选择文件"), command=choose).pack(anchor="w")
            if key == "performance" and sys.platform == "win32":
                def record(lab=label):
                    nonlocal recorded_midi
                    from pages.midi_input_dialog import record_midi
                    data = record_midi(dlg)
                    if data:
                        recorded_midi = data
                        paths["performance"] = ""
                        lab.config(text=tr("已录制 MIDI"))
                ttk.Button(frame, text=tr("录制 MIDI…"), command=record).pack(anchor="w", pady=(8, 0))

        def start():
            if not paths["reference"] or not (paths["performance"] or recorded_midi):
                toast.warn(tr("请先选择参考文件和演奏文件，或录制 MIDI."))
                return
            target_pid = self.comm.pid
            target_config = self.comm.current_config()
            dlg.finish(True)
            toast.info(tr("正在分析演奏文件…"))
            def work():
                tmp = ""
                try:
                    import external_software
                    import music_tools
                    import tempstore
                    ext = external_software.manager(self.comm.dir)
                    music = music_tools.manager(self.comm.dir, ext)
                    tmp = tempstore.mkdtemp("misc", prefix="comm-review-")
                    def as_midi(path, name):
                        suffix = os.path.splitext(path)[1].lower()
                        if suffix in (".mid", ".midi"):
                            return path
                        out = os.path.join(tmp, name + ".mid")
                        music.convert(path, out)
                        return out
                    reference = as_midi(paths["reference"], "reference")
                    if recorded_midi:
                        from pathlib import Path
                        performance = Path(tmp, "performance.mid")
                        performance.write_bytes(recorded_midi)
                    else:
                        performance = as_midi(paths["performance"], "performance")
                    data = music.compare_midi(reference, performance)
                    session = "music:" + self.comm.persona_name()[:40] + ":" + os.path.basename(paths["reference"] or "reference")[:80]
                    review = self.comm.subagent_call(
                        "music_review", session,
                        "本次结构化演奏差异:\n" + json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                        max_tokens=500, temperature=0.2, timeout=120)
                    duration = max(0, int(data.get("summary", {}).get("performance_duration_ms") or 0)) / 1000
                    if os.path.splitext(paths["performance"])[1].lower() in (".mp3", ".wav", ".flac", ".ogg"):
                        duration = max(duration, music.audio_duration(paths["performance"]))
                    prompt = (
                        "请结合独立评判和结构化演奏差异，以你平常的语气自然评判用户演奏，并给出简洁建议。"
                        "额外留意用户对这一首的处理意图：根据整体速度比例、起音与时值误差、漏音/多音分布及独立评判，"
                        "谨慎判断是否存在连贯且有依据的有意处理；只有能与偶发失误区分时，才自然提及你观察到的意图。"
                        "证据不足、只有零散偏差或无法区分意图与失误时，不要推测或提及演奏意图，也不要把技术误差解释成表达。\n"
                        "独立评判：\n" + str(review).strip()
                        + "\n结构化差异：\n"
                        + json.dumps(data, ensure_ascii=False, separators=(",", ":"))
                    )
                    outcome = ("ok", prompt, 60 + duration)
                except Exception as error:
                    outcome = ("err", str(error))
                finally:
                    if tmp:
                        import shutil
                        shutil.rmtree(tmp, ignore_errors=True)
                try:
                    self.after(0, lambda: finish(outcome))
                except Exception:
                    pass
            def finish(outcome):
                if outcome[0] == "err":
                    toast.warn(tr(outcome[1]))
                else:
                    if self.comm.pid != target_pid or self.comm.current_config() != target_config:
                        toast.warn(tr("聊天对象或模型已更改，评判结果未发送."))
                        return
                    self.comm.set_music_review_draft(outcome[1], outcome[2])
                    self.refresh(force=True)
            import threading
            threading.Thread(target=work, daemon=True).start()

        ttk.Button(dlg.buttons, text=tr("开始评判"), style="Accent.TButton", command=start).pack(side="right")
        ttk.Button(dlg.buttons, text=tr("取消"), command=dlg.cancel).pack(side="right", padx=(0, 8))
        dlg.wait()

    def _clear(self):
        if not self.comm.messages:
            return
        if toast.ask(tr("清空聊天记录?"), title=tr("清空"), danger=True):
            self.comm.clear()
            self.refresh(force=True)

    # ---- share data ----
    def _toggle_share(self):
        if self.comm.share_on() or self.comm.share_asking():
            why = self.comm.share_enable(False)
        else:
            why = self.comm.toggle_request_draft("share")
        if why:
            toast.warn(tr(why))
        self.refresh(force=True)

    def _clear_package_drafts(self):
        self.comm.clear_joint_draft()
        self.comm.clear_request_draft()
        self._pending_image = ""
        self.refresh(force=True)

    # ---- 起点平移 ----
    def _shift(self):
        """🕰 起点平移：范围由人设模板声明，明天起生效；等回复时再点即取消。"""
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
        bounds = c.shift_range()
        if bounds is None:
            toast.warn(tr("起点平移模板格式错误"))
            return
        bg = COLORS["bg"]
        width = config.scaled(460)
        dlg = Modal(self.winfo_toplevel(), tr("起点平移"), width=width, height=config.scaled(300))
        wrap = width - config.scaled(60)
        tk.Label(dlg.body, text=tr("请求对方把日程的起点平移到新的时刻 ({lo} - {hi}). 对方同意后明天起生效, 今天的日程不变; "
                                   "只记在 OIT 里, 不会修改人设文件. 对方可以拒绝.", lo=comm_mod.hm_text(bounds[0]), hi=comm_mod.hm_text(bounds[1])),
                 bg=bg, fg=COLORS["text_dim"], font=config.font("small"), justify="left", anchor="w", wraplength=wrap).pack(fill="x")
        tk.Label(dlg.body, text=tr("明天的起点: {t}", t=comm_mod.hm_text(cur)), bg=bg, fg=COLORS["text"],
                 font=config.font("body"), anchor="w").pack(fill="x", pady=(10, 4))
        row = tk.Frame(dlg.body, bg=bg)
        row.pack(fill="x")
        lo_h, hi_h = 0, 23
        var_h = tk.StringVar(value=str(cur // 60))
        var_m = tk.StringVar(value=f"{cur % 60:02d}")
        ttk.Spinbox(row, from_=lo_h, to=hi_h, textvariable=var_h, width=4, wrap=False).pack(side="left")
        tk.Label(row, text=":", bg=bg, fg=COLORS["text"], font=config.font("body", bold=True)).pack(side="left", padx=4)
        minute_values = tuple(f"{m:02d}" for m in range(cur % config.COMM_SHIFT_STEP_MINUTES, 60, config.COMM_SHIFT_STEP_MINUTES))
        ttk.Spinbox(row, values=minute_values, textvariable=var_m, width=4, wrap=True, state="readonly").pack(side="left")

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
        """🖼 识字只把识别结果放入输入框; 用 📎 发送图片本身."""
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
        """帮助: 沟通页上每个小按钮是干什么的 (分两栏, 说明尽量短), 还有"无言为真"会禁止沟通的提醒."""
        width = config.scaled(780)
        dlg = Modal(self.winfo_toplevel(), tr("帮助"), width=width, height=config.scaled(400))
        bg = COLORS["bg"]
        rows = (("❓", "帮助", "打开这个说明."),
                ("📚", "阅读", "选择「自己阅读」可独自打开书库；选择「发起 coread」会作为附件加入待发送内容，随下一条消息发送，待对方明确同意后才打开共读室。共读期间请在阅读网页聊天，结束后恢复沟通。手机需连接 Windows 的阅读服务。"),
                ("💬", "说教", "你可以请求对方针对现实中想改变的状态、行为、习惯或语言进行说教，分条分析问题与解决方案；主题会简短记入记忆卡，实际完成改变后才移除。"),
                ("📱 / 🎭", "聊天模式", "输入框上方的 📱 / 🎭 按钮切换软件聊天与情景模拟。📱 = 软件聊天，不描写动作；🎭 = 情景模拟，按当前时段与状态描写虚构动作。模式按聊天对象保存。"),
                ("👀 / 🙈", "分享数据 (share data)", "🙈 会把请求作为附件加入待发送内容，随下一条消息询问对方；同意后才打开. 👀 表示已打开，去别的页面时对方会收到那一页上你们的数据；回到沟通自动关闭. 对方也可以发邀请，点「接受」才会打开."),
                ("🔔 / 🔕", "主动发起", "🔔 = 允许这个对象主动找你, 🔕 = 禁止."),
                ("⏰ / ⏲", "共时闹钟", "⏰ = 闹钟跟随这个对象的起床时间; 响铃的设备手动选, 一次只能有一个对象."),
                ("🕰", "起点平移", "请求对方平移日程起点, 允许范围由人设模板决定, 同意后明天起生效."),
                ("📎", "图片消息", "选择图片，或在输入框按 Ctrl+V 粘贴剪贴板图片，然后随消息发送."),
                ("🖼", "识别字体", "选一张图片，把识别出的文字放进聊天框供你编辑；用 📎 发送图片本身."),
                ("🎼", "演奏评判", "左边选参考乐谱或 MIDI，右边选演奏文件或录制 MIDI。分析摘要会交给聊天对象."),
                ("🤝", "联合行动", "邀请对方一起做一次合约: 在合约页选词条, 输入开始时间; 对方回 [Yes] 就按约开始. Claudia 的剩余休息按她的日程接续和结转。"))
        area = ScrollArea(dlg.body, height=config.scaled(400), bg=bg)
        area.pack(fill="both", expand=True)
        cols = tk.Frame(area.inner, bg=bg)
        cols.pack(fill="x")
        cols.columnconfigure(0, weight=1, uniform="help")
        cols.columnconfigure(1, weight=1, uniform="help")
        half = (len(rows) + 1) // 2                                   # 左栏先排满, 剩下的放第二栏
        cell = (width - config.scaled(60)) // 2
        for i, (icon, name, text) in enumerate(rows):
            col = 0 if i < half else 1
            f = tk.Frame(cols, bg=bg)
            f.grid(row=i if col == 0 else i - half, column=col, sticky="nw", padx=(0, config.scaled(12)) if col == 0 else 0, pady=(0, 8))
            tk.Label(f, text=icon, bg=bg, fg=COLORS["text"], font=config.font("body", bold=True), width=7, anchor="nw").pack(side="left", anchor="n")
            tk.Label(f, text=tr(name) + ": " + tr(text), bg=bg, fg=COLORS["text"], font=config.font("body"), justify="left",
                     anchor="w", wraplength=cell - config.scaled(100)).pack(side="left", fill="x", expand=True)
        tk.Label(area.inner, text="⚠  " + tr("特别提醒: 「无言为真」生效时无法使用「沟通」."),
                 bg=bg, fg=COLORS["danger"], font=config.font("body", bold=True), justify="left", anchor="w",
                 wraplength=width - config.scaled(60)).pack(fill="x", pady=(6, 0))
        ttk.Button(dlg.buttons, text=tr("知道了"), style="Accent.TButton", command=lambda: dlg.finish(True)).pack(side="right")
        dlg.update_idletasks()
        dlg._center(self.winfo_toplevel(), min(width, int(dlg.winfo_screenwidth() * .9)),
                    min(dlg.winfo_reqheight(), int(dlg.winfo_screenheight() * .85)))
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

