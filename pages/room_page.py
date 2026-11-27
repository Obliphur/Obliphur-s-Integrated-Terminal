# -*- coding: utf-8 -*-
"""🏠 情景房间子页面（PC 优先）：古早文字游戏字符画地图、多角色进出、双手部位互动、定向听觉隔离与 NPC 自动对话。"""

import threading
import tkinter as tk
from tkinter import ttk

import ai_assist
import comm as comm_mod
import config
import toast
from comm.comm_persona import list_personas, persona_system, persona_title
from comm.comm_room import (
    BODY_PARTS,
    FACILITY_ACTIONS,
    RoomStore,
    build_actor_turn_prompt,
    build_map_generation_prompt,
    parse_actor_reply,
    parse_generated_room,
    pick_auto_speaker,
    render_room_map,
)
from config import COLORS
from i18n import tr
from .widgets import Modal, cfg


class RoomPage(tk.Frame):
    """独立全尺寸情景房间子页面，由沟通页进入。"""

    def __init__(self, master, comm, data_dir: str, username=None,
                 on_back=None, on_open_page=None, on_open_settings=None,
                 on_music=None, on_coread=None, on_joint=None):
        super().__init__(master, bg=COLORS["bg"])
        self.comm = comm
        self.data_dir = data_dir
        self.username = username or (lambda: "你")
        self.on_back = on_back
        self.on_open_page = on_open_page
        self.on_open_settings = on_open_settings
        self.on_music = on_music
        self.on_coread = on_coread
        self.on_joint = on_joint

        self.store = RoomStore(data_dir)
        self._visible = False
        self._auto_job = None
        self._busy_actors: set[str] = set()
        self._generating_map = False
        self._hotspots: list[dict] = []
        self._target_vars: dict[str, tk.BooleanVar] = {}
        self._var_all_targets = tk.BooleanVar(value=True)
        self._room_ids: list[str] = []

        self._ensure_default_partner_in_room()
        self._build()
        self.refresh()

    def _ensure_default_partner_in_room(self):
        """首次进入时，如果房间里只有用户一个人且已选人设，默认把当前人设放进对方的居室或当前房间方便体验。"""
        cur = self.comm.persona_name() if self.comm else ""
        if cur and cur not in self.store.occupants:
            self.store.occupants[cur] = "my_home"
            self.store.save()

    def _persona_names(self) -> dict[str, str]:
        names = {"user": (self.username() or "你").strip() or "你"}
        if self.comm:
            for p in list_personas(self.comm.personas):
                names[p] = persona_title(p)
        for aid in self.store.occupants:
            if aid not in names:
                names[aid] = persona_title(aid) if aid != "user" else "你"
        return names

    def _actor_name(self, actor_id: str) -> str:
        return self._persona_names().get(actor_id, actor_id)

    # ══════════════ 界面构建 ══════════════

    def _build(self):
        bg = COLORS["bg"]
        card = COLORS["bg_card"]
        border = COLORS["border"]

        # 顶部工具栏
        hdr = tk.Frame(self, bg=bg)
        hdr.pack(fill="x", padx=20, pady=(14, 8))
        ttk.Button(hdr, text=tr("← 返回沟通"), command=self._go_back).pack(side="left")
        self.lbl_room_title = tk.Label(hdr, text="", bg=bg, fg=COLORS["text"],
                                       font=config.font("h2", bold=True))
        self.lbl_room_title.pack(side="left", padx=(14, 8))
        self.lbl_room_theme = tk.Label(hdr, text="", bg=bg, fg=COLORS["accent"],
                                       font=config.font("small"))
        self.lbl_room_theme.pack(side="left")

        ttk.Button(hdr, text=tr("👥 进出管理"), command=self._open_occupants_dialog).pack(side="right", padx=(6, 0))
        ttk.Button(hdr, text=tr("＋ 协商建房"), command=self._open_create_room_dialog).pack(side="right", padx=(6, 0))
        self.var_room = tk.StringVar()
        self.cb_room = ttk.Combobox(hdr, textvariable=self.var_room, state="readonly", width=20)
        self.cb_room.pack(side="right", padx=(6, 0))
        self.cb_room.bind("<<ComboboxSelected>>", lambda _e: self._pick_room())

        self.lbl_auto_status = tk.Label(hdr, text="", bg=bg, fg=COLORS["text_dim"],
                                        font=config.font("small"), cursor="hand2")
        self.lbl_auto_status.pack(side="right", padx=(0, 10))
        self.lbl_auto_status.bind("<Button-1>", lambda _e: self._open_comm_settings())

        # 主体双栏（左：复古字符画地图，右：事件日志与行动区）
        main = tk.Frame(self, bg=bg)
        main.pack(fill="both", expand=True, padx=20, pady=(0, 14))
        main.grid_columnconfigure(0, weight=11, uniform="room_cols")
        main.grid_columnconfigure(1, weight=10, uniform="room_cols")
        main.grid_rowconfigure(0, weight=1)

        # ---- 左栏：字符画地图 ----
        left = tk.Frame(main, bg=card, highlightthickness=1, highlightbackground=border)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.lbl_ambience = tk.Label(left, text="", bg=card, fg=COLORS["text_dim"],
                                     font=config.font("small"), anchor="w", justify="left", wraplength=520)
        self.lbl_ambience.pack(fill="x", padx=12, pady=(10, 6))

        # 复古暗色终端风格画布
        self.map_text = tk.Text(
            left, wrap="none", state="disabled",
            font=("Consolas", max(11, config.scaled(12))),
            bg="#12161f", fg="#dce4f2",
            relief="flat", bd=0, padx=14, pady=12,
            cursor="arrow", highlightthickness=1, highlightbackground="#293245",
        )
        self.map_text.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.map_text.tag_configure("fac", foreground="#67d5f7", underline=False)
        self.map_text.tag_configure("door", foreground="#f6c177", underline=False)
        self.map_text.tag_configure("actor", foreground="#c4a7e7", underline=False)
        self.map_text.tag_configure("actor_me", foreground="#a6e3a1", underline=False)
        self.map_text.tag_configure("hover", background="#24344d", underline=True)

        self.lbl_hover = tk.Label(left, text=tr("点击地图中的 [设施] 打开对应功能，点击 [@人物] 展开部位互动。"),
                                  bg=card, fg=COLORS["accent"], font=config.font("small"), anchor="w")
        self.lbl_hover.pack(fill="x", padx=12, pady=(0, 6))

        # 在场人物快捷栏
        occ_bar = tk.Frame(left, bg=card)
        occ_bar.pack(fill="x", padx=12, pady=(0, 10))
        tk.Label(occ_bar, text=tr("当前在场："), bg=card, fg=COLORS["text_dim"],
                 font=config.font("small")).pack(side="left")
        self.occ_chips = tk.Frame(occ_bar, bg=card)
        self.occ_chips.pack(side="left", fill="x", expand=True)

        # ---- 右栏：复古日志 + 行动与发言区 ----
        right = tk.Frame(main, bg=card, highlightthickness=1, highlightbackground=border)
        right.grid(row=0, column=1, sticky="nsew")

        log_head = tk.Frame(right, bg=card)
        log_head.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(log_head, text=tr("📜 房间记录"), bg=card, fg=COLORS["text"],
                 font=config.font("body", bold=True)).pack(side="left")
        self.lbl_busy = tk.Label(log_head, text="", bg=card, fg=COLORS["accent"], font=config.font("small"))
        self.lbl_busy.pack(side="right")

        log_wrap = tk.Frame(right, bg=card)
        log_wrap.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.log_text = tk.Text(
            log_wrap, wrap="word", state="disabled",
            font=config.font("body"), bg=bg, fg=COLORS["text"],
            relief="flat", padx=10, pady=8,
            highlightthickness=1, highlightbackground=border,
        )
        sb = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.log_text.tag_configure("sys", foreground=COLORS["text_dim"])
        self.log_text.tag_configure("private", foreground=COLORS["text_dim"])
        self.log_text.tag_configure("who_me", foreground=COLORS["accent"], font=config.font("body", bold=True))
        self.log_text.tag_configure("who_npc", foreground="#a78bfa", font=config.font("body", bold=True))
        self.log_text.tag_configure("act", foreground="#f59e0b")
        self.log_text.tag_configure("speech", foreground=COLORS["text"])

        # 双手状态栏
        hand_bar = tk.Frame(right, bg=card)
        hand_bar.pack(fill="x", padx=12, pady=(2, 2))
        self.lbl_left_hand = tk.Label(hand_bar, text="", bg=bg, fg=COLORS["text"],
                                      font=config.font("small"), padx=8, pady=3,
                                      highlightthickness=1, highlightbackground=border, cursor="hand2")
        self.lbl_left_hand.pack(side="left", padx=(0, 6))
        self.lbl_left_hand.bind("<Button-1>", lambda _e: self._toggle_hand_bind("left"))
        self.lbl_right_hand = tk.Label(hand_bar, text="", bg=bg, fg=COLORS["text"],
                                       font=config.font("small"), padx=8, pady=3,
                                       highlightthickness=1, highlightbackground=border, cursor="hand2")
        self.lbl_right_hand.pack(side="left", padx=(0, 6))
        self.lbl_right_hand.bind("<Button-1>", lambda _e: self._toggle_hand_bind("right"))
        tk.Label(hand_bar, text=tr("(点手部可切换/解除束缚)"), bg=card, fg=COLORS["text_dim"],
                 font=config.font("small")).pack(side="left")

        # 待发送手部动作预览
        self.pending_frame = tk.Frame(right, bg=card)
        self.pending_frame.pack(fill="x", padx=12, pady=(2, 2))

        # 指定说话对象栏
        target_bar = tk.Frame(right, bg=card)
        target_bar.pack(fill="x", padx=12, pady=(2, 4))
        tk.Label(target_bar, text=tr("对谁说："), bg=card, fg=COLORS["text_dim"],
                 font=config.font("small")).pack(side="left")
        self.target_box = tk.Frame(target_bar, bg=card)
        self.target_box.pack(side="left", fill="x", expand=True)

        # 输入框 + 发送
        inp = tk.Frame(right, bg=card)
        inp.pack(fill="x", padx=12, pady=(0, 10))
        self.entry = tk.Text(
            inp, height=3, wrap="word", font=config.font("body"),
            bg=bg, fg=COLORS["text"], insertbackground=COLORS["text"],
            relief="flat", highlightthickness=1, highlightbackground=border,
            highlightcolor=COLORS["accent"],
        )
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", self._on_return)
        ttk.Button(inp, text=tr("发送"), style="Accent.TButton", command=self._send).pack(
            side="left", padx=(8, 0), anchor="s")

    # ══════════════ 生命周期与刷新 ══════════════

    def show(self):
        self._visible = True
        self.refresh()
        self._schedule_auto_tick()
        self.entry.focus_set()

    def hide(self):
        self._visible = False
        if self._auto_job:
            try:
                self.after_cancel(self._auto_job)
            except tk.TclError:
                pass
            self._auto_job = None

    def _go_back(self):
        if callable(self.on_back):
            self.on_back()

    def _open_comm_settings(self):
        if callable(self.on_open_settings):
            self.on_open_settings()

    def refresh(self):
        room = self.store.current_room()
        rid = room["id"]
        names = self._persona_names()

        cfg(self.lbl_room_title, text=f"🏠 {room['name']}")
        cfg(self.lbl_room_theme, text=f"[{room['theme']}]")
        cfg(self.lbl_ambience, text=room.get("ambience") or tr("安静的文字空间。"))

        # 房间下拉框
        rooms = self.store.room_list()
        self._room_ids = [r["id"] for r in rooms]
        labels = [f"{r['name']} ({len(self.store.occupants_in(r['id']))}人)" for r in rooms]
        self.cb_room.configure(values=labels)
        if rid in self._room_ids:
            self.var_room.set(labels[self._room_ids.index(rid)])

        # 自动对话状态灯
        npc_on = bool(self.comm.cfg.get("room_npc_chat", False)) if self.comm else False
        interval = int(self.comm.cfg.get("room_npc_interval") or 30) if self.comm else 30
        prob = int(self.comm.cfg.get("room_npc_prob") or 30) if self.comm else 30
        if npc_on:
            cfg(self.lbl_auto_status, text=tr("● 角色对话已开 ({s}s / {p}%)", s=interval, p=prob),
                fg=COLORS["accent"])
        else:
            cfg(self.lbl_auto_status, text=tr("○ 角色对话未开 (点此设置)"), fg=COLORS["text_dim"])

        self._render_map(room, names)
        self._render_occupant_chips(rid, names)
        self._render_targets(rid, names)
        self._render_hands()
        self._render_logs(rid, names)
        self._update_busy_label(names)

    def _render_map(self, room: dict, names: dict[str, str]):
        occ_ids = self.store.occupants_in(room["id"])
        occupants = [(aid, names.get(aid, aid)) for aid in occ_ids]
        text, hotspots = render_room_map(room, occupants)
        self._hotspots = hotspots

        w = self.map_text
        w.configure(state="normal")
        # 清理旧的动态热点标签
        for tag in list(w.tag_names()):
            if tag.startswith("hs_"):
                w.tag_delete(tag)
        w.delete("1.0", "end")
        w.insert("1.0", text)

        for idx, hs in enumerate(hotspots):
            start = f"{hs['line']}.{hs['col_start']}"
            end = f"{hs['line']}.{hs['col_end']}"
            tag = f"hs_{idx}"
            if hs["kind"] == "actor":
                base_tag = "actor_me" if hs["id"] == "user" else "actor"
            else:
                base_tag = "door" if hs["action"] == "door" else "fac"
            w.tag_add(base_tag, start, end)
            w.tag_add(tag, start, end)
            w.tag_bind(tag, "<Enter>", lambda _e, h=hs, s=start, en=end: self._on_hotspot_enter(h, s, en))
            w.tag_bind(tag, "<Leave>", lambda _e, s=start, en=end: self._on_hotspot_leave(s, en))
            w.tag_bind(tag, "<Button-1>", lambda _e, h=hs: self._on_hotspot_click(h))

        w.configure(state="disabled")

    def _on_hotspot_enter(self, hs: dict, start: str, end: str):
        self.map_text.configure(cursor="hand2")
        self.map_text.tag_add("hover", start, end)
        cfg(self.lbl_hover, text=f"[{hs['label']}] — {hs['desc']}")

    def _on_hotspot_leave(self, start: str, end: str):
        self.map_text.configure(cursor="arrow")
        self.map_text.tag_remove("hover", start, end)
        cfg(self.lbl_hover, text=tr("点击地图中的 [设施] 打开对应功能，点击 [@人物] 展开部位互动。"))

    def _render_occupant_chips(self, room_id: str, names: dict[str, str]):
        for child in self.occ_chips.winfo_children():
            child.destroy()
        for aid in self.store.occupants_in(room_id):
            name = names.get(aid, aid)
            is_me = (aid == "user")
            lbl = tk.Label(
                self.occ_chips,
                text=f"@{name}" + (" (我)" if is_me else ""),
                bg=COLORS["bg"],
                fg=COLORS["accent"] if is_me else COLORS["text"],
                font=config.font("small"),
                padx=7, pady=2,
                highlightthickness=1, highlightbackground=COLORS["border"],
                cursor="arrow" if is_me else "hand2",
            )
            lbl.pack(side="left", padx=(0, 6))
            if not is_me:
                lbl.bind("<Button-1>", lambda _e, a=aid, n=name: self._open_actor_interact(a, n))

    def _render_targets(self, room_id: str, names: dict[str, str]):
        for child in self.target_box.winfo_children():
            child.destroy()
        npcs = [aid for aid in self.store.occupants_in(room_id) if aid != "user"]
        old_vars = self._target_vars
        self._target_vars = {}

        def _toggle_all():
            if self._var_all_targets.get():
                for v in self._target_vars.values():
                    v.set(False)
            elif not any(v.get() for v in self._target_vars.values()):
                self._var_all_targets.set(True)

        def _toggle_one():
            if any(v.get() for v in self._target_vars.values()):
                self._var_all_targets.set(False)
            else:
                self._var_all_targets.set(True)

        tk.Checkbutton(
            self.target_box, text=tr("全员"), variable=self._var_all_targets,
            bg=COLORS["bg_card"], fg=COLORS["text"], selectcolor=COLORS["bg_card"],
            activebackground=COLORS["bg_card"], activeforeground=COLORS["text"],
            font=config.font("small"), command=_toggle_all,
        ).pack(side="left", padx=(0, 6))

        for aid in npcs:
            prev = old_vars[aid].get() if aid in old_vars else False
            var = tk.BooleanVar(value=prev)
            self._target_vars[aid] = var
            tk.Checkbutton(
                self.target_box, text=names.get(aid, aid), variable=var,
                bg=COLORS["bg_card"], fg=COLORS["text"], selectcolor=COLORS["bg_card"],
                activebackground=COLORS["bg_card"], activeforeground=COLORS["text"],
                font=config.font("small"), command=_toggle_one,
            ).pack(side="left", padx=(0, 6))

        if not any(v.get() for v in self._target_vars.values()):
            self._var_all_targets.set(True)

    def _selected_targets(self) -> list[str]:
        if self._var_all_targets.get():
            return ["all"]
        picked = [aid for aid, v in self._target_vars.items() if v.get()]
        return picked or ["all"]

    def _render_hands(self):
        for side, lbl in (("left", self.lbl_left_hand), ("right", self.lbl_right_hand)):
            st = self.store.hands[side]["state"]
            fg = "#ef4444" if st == "bound" else (COLORS["accent"] if st == "used" else COLORS["text"])
            cfg(lbl, text=f"[{self.store.hand_label(side)}]", fg=fg)

        for child in self.pending_frame.winfo_children():
            child.destroy()
        for idx, act in enumerate(self.store.pending_actions):
            row = tk.Frame(self.pending_frame, bg=COLORS["bg"], highlightthickness=1,
                           highlightbackground=COLORS["border"])
            row.pack(fill="x", pady=1)
            hand_txt = "左手" if act["hand"] == "left" else "右手"
            txt = f"[{hand_txt} → {act['target_name']}·{act['part']}] {act['text']}"
            tk.Label(row, text=txt, bg=COLORS["bg"], fg=COLORS["accent"],
                     font=config.font("small"), anchor="w").pack(side="left", fill="x", expand=True, padx=6, pady=2)
            xbtn = tk.Label(row, text="✕", bg=COLORS["bg"], fg=COLORS["text_dim"],
                            font=config.font("small"), cursor="hand2", padx=6)
            xbtn.pack(side="right")
            xbtn.bind("<Button-1>", lambda _e, i=idx: self._remove_pending(i))

    def _remove_pending(self, idx: int):
        self.store.remove_pending_action(idx)
        self._render_hands()

    def _toggle_hand_bind(self, side: str):
        cur_bound = self.store.hands[side]["state"] == "bound"
        self.store.set_hand_bound(side, not cur_bound, note="束缚" if not cur_bound else "")
        self._render_hands()

    def _render_logs(self, room_id: str, names: dict[str, str]):
        entries = self.store.user_display_logs(room_id, names)
        w = self.log_text
        w.configure(state="normal")
        w.delete("1.0", "end")
        if not entries:
            w.insert("end", tr("* 你环顾四周，房间里安静而温暖。点击左侧地图上的设施或人物开始互动吧。\n"), "sys")
        for e in entries:
            kind = e.get("kind")
            if kind in ("sys", "move"):
                w.insert("end", f"* {e['text']}\n", "sys")
            elif kind == "private_hint":
                w.insert("end", f"… {e['text']}\n", "private")
            else:
                targets = e.get("targets") or ["all"]
                to_str = tr("全员") if "all" in targets else "、".join(names.get(t, t) for t in targets)
                tag = "who_me" if e.get("sender") == "user" else "who_npc"
                w.insert("end", f"{e['sender_name']} → {to_str}：\n", tag)
                for a in e.get("actions") or []:
                    h = "左手" if a.get("hand") == "left" else "右手"
                    w.insert("end", f"  [{h}→{a.get('target_name')}·{a.get('part')}] {a.get('text')}\n", "act")
                if e.get("text"):
                    w.insert("end", f"  {e['text']}\n", "speech")
                w.insert("end", "\n")
        w.see("end")
        w.configure(state="disabled")

    def _update_busy_label(self, names: dict[str, str]):
        if self._generating_map:
            cfg(self.lbl_busy, text=tr("正在绘制字符画地图…"))
        elif self._busy_actors:
            who = "、".join(names.get(a, a) for a in sorted(self._busy_actors))
            cfg(self.lbl_busy, text=tr("{who} 正在回应…", who=who))
        else:
            cfg(self.lbl_busy, text="")

    # ══════════════ 地图点击与 OIT 功能联动 ══════════════

    def _on_hotspot_click(self, hs: dict):
        if hs["kind"] == "actor":
            if hs["id"] == "user":
                toast.info(tr("这是你自己。点击其他角色可展开互动部位列表。"))
                return
            self._open_actor_interact(hs["id"], hs["label"])
            return

        action = hs.get("action", "custom")
        if action == "piano":
            if callable(self.on_music):
                self.on_music()
            else:
                toast.info(tr("请从沟通页打开音乐演奏功能。"))
        elif action == "bookshelf":
            if callable(self.on_coread):
                self.on_coread()
            else:
                toast.info(tr("共读入口暂不可用。"))
        elif action == "desk":
            self._show_facility_menu([
                (tr("⏱ 打开专注计时器"), lambda: self.on_open_page("timer") if callable(self.on_open_page) else None),
                (tr("🤝 发起联合行动"), lambda: self.on_joint() if callable(self.on_joint) else None),
                (tr("✋ 与「{label}」互动…", label=hs["label"]), lambda: self._open_custom_facility_interact(hs)),
            ])
        elif action == "board":
            self._show_facility_menu([
                (tr("⚔ 打开合约页"), lambda: self.on_open_page("contract") if callable(self.on_open_page) else None),
                (tr("📚 打开知识库"), lambda: self.on_open_page("knowledge") if callable(self.on_open_page) else None),
                (tr("✋ 与「{label}」互动…", label=hs["label"]), lambda: self._open_custom_facility_interact(hs)),
            ])
        elif action == "door":
            items = []
            cur_rid = self.store.current_room_id()
            for r in self.store.room_list():
                cnt = len(self.store.occupants_in(r["id"]))
                mark = "✓ " if r["id"] == cur_rid else "   "
                items.append((f"{mark}{r['name']} ({cnt}人)", lambda rid=r["id"]: self._switch_user_room(rid)))
            items.append((tr("＋ 协商新建房间…"), self._open_create_room_dialog))
            self._show_facility_menu(items)
        else:
            self._open_custom_facility_interact(hs)

    def _show_facility_menu(self, items: list[tuple[str, object]]):
        menu = tk.Menu(self, tearoff=0)
        for label, cb in items:
            menu.add_command(label=label, command=cb)
        try:
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    def _pick_room(self):
        idx = self.cb_room.current()
        if 0 <= idx < len(self._room_ids):
            self._switch_user_room(self._room_ids[idx])

    def _switch_user_room(self, room_id: str):
        if self.store.join_room("user", room_id, self._actor_name("user")):
            self.refresh()

    # ══════════════ 部位互动弹窗 & 设施互动弹窗 ══════════════

    def _open_actor_interact(self, actor_id: str, actor_name: str):
        avail = self.store.available_hands()
        bg = COLORS["bg"]
        dlg = Modal(self.winfo_toplevel(), tr("与 {name} 互动", name=actor_name),
                    width=config.scaled(480), height=config.scaled(430))

        # 选左手 / 右手
        hand_row = tk.Frame(dlg.body, bg=bg)
        hand_row.pack(fill="x", pady=(0, 8))
        tk.Label(hand_row, text=tr("使用哪只手："), bg=bg, fg=COLORS["text"],
                 font=config.font("body")).pack(side="left")
        var_hand = tk.StringVar(value=avail[0] if avail else "")
        for side, label in (("left", "左手"), ("right", "右手")):
            st = self.store.hands[side]["state"]
            suffix = "" if st == "free" else (" (已用)" if st == "used" else " (被束缚)")
            rb = ttk.Radiobutton(hand_row, text=tr(label) + suffix, value=side, variable=var_hand)
            if st != "free":
                rb.state(["disabled"])
            rb.pack(side="left", padx=(8, 0))

        if not avail:
            tk.Label(dlg.body, text=tr("当前没有空闲的手（两只手已用完或被束缚），无法添加新的手部互动。"),
                     bg=bg, fg="#ef4444", font=config.font("small"), anchor="w", wraplength=430).pack(
                fill="x", pady=(0, 8))

        # 部位列表
        tk.Label(dlg.body, text=tr("选择互动部位："), bg=bg, fg=COLORS["text"],
                 font=config.font("body"), anchor="w").pack(fill="x")
        var_part = tk.StringVar(value=BODY_PARTS[0][1])
        grid = tk.Frame(dlg.body, bg=bg)
        grid.pack(fill="x", pady=(4, 8))
        for i, (_pid, plabel) in enumerate(BODY_PARTS):
            rb = ttk.Radiobutton(grid, text=tr(plabel), value=plabel, variable=var_part)
            rb.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 14), pady=2)

        custom_row = tk.Frame(dlg.body, bg=bg)
        custom_row.pack(fill="x", pady=(0, 8))
        tk.Label(custom_row, text=tr("或自定义部位："), bg=bg, fg=COLORS["text_dim"],
                 font=config.font("small")).pack(side="left")
        var_custom_part = tk.StringVar()
        ttk.Entry(custom_row, textvariable=var_custom_part, width=22).pack(side="left", padx=(6, 0))

        tk.Label(dlg.body, text=tr("输入具体互动文本："), bg=bg, fg=COLORS["text"],
                 font=config.font("body"), anchor="w").pack(fill="x")
        txt = tk.Text(dlg.body, height=3, wrap="word", font=config.font("body"),
                      bg=COLORS["bg_card"], fg=COLORS["text"], insertbackground=COLORS["text"],
                      relief="flat", highlightthickness=1, highlightbackground=COLORS["border"])
        txt.pack(fill="x", pady=(4, 0))
        txt.focus_set()

        def submit():
            hand = var_hand.get()
            if not hand:
                toast.warn(tr("当前没有可用的空闲手。"))
                return
            part = var_custom_part.get().strip() or var_part.get()
            action_text = txt.get("1.0", "end").strip()
            err = self.store.queue_hand_action(hand, actor_id, actor_name, part, action_text)
            if err:
                toast.warn(tr(err))
                return
            dlg.finish(True)
            self._render_hands()

        ttk.Button(dlg.buttons, text=tr("加入本回合行动"), style="Accent.TButton",
                   command=submit, state="normal" if avail else "disabled").pack(side="right")
        ttk.Button(dlg.buttons, text=tr("取消"), command=dlg.cancel).pack(side="right", padx=(0, 8))
        dlg.wait()

    def _open_custom_facility_interact(self, hs: dict):
        avail = self.store.available_hands()
        bg = COLORS["bg"]
        dlg = Modal(self.winfo_toplevel(), tr("互动设施：{name}", name=hs["label"]),
                    width=config.scaled(440), height=config.scaled(270))
        tk.Label(dlg.body, text=hs.get("desc") or "", bg=bg, fg=COLORS["text_dim"],
                 font=config.font("small"), anchor="w", wraplength=390).pack(fill="x", pady=(0, 8))
        var_hand = tk.StringVar(value=avail[0] if avail else "")
        hrow = tk.Frame(dlg.body, bg=bg)
        hrow.pack(fill="x", pady=(0, 8))
        tk.Label(hrow, text=tr("使用手部："), bg=bg, fg=COLORS["text"], font=config.font("small")).pack(side="left")
        for side, label in (("left", "左手"), ("right", "右手")):
            st = self.store.hands[side]["state"]
            rb = ttk.Radiobutton(hrow, text=tr(label), value=side, variable=var_hand)
            if st != "free":
                rb.state(["disabled"])
            rb.pack(side="left", padx=(8, 0))

        txt = tk.Text(dlg.body, height=3, wrap="word", font=config.font("body"),
                      bg=COLORS["bg_card"], fg=COLORS["text"], insertbackground=COLORS["text"],
                      relief="flat", highlightthickness=1, highlightbackground=COLORS["border"])
        txt.pack(fill="x")
        txt.focus_set()

        def submit():
            hand = var_hand.get()
            if not hand:
                toast.warn(tr("当前没有空闲的手。"))
                return
            err = self.store.queue_hand_action(hand, hs["id"], hs["label"], "设施", txt.get("1.0", "end").strip())
            if err:
                toast.warn(tr(err))
                return
            dlg.finish(True)
            self._render_hands()

        ttk.Button(dlg.buttons, text=tr("加入本回合行动"), style="Accent.TButton",
                   command=submit, state="normal" if avail else "disabled").pack(side="right")
        ttk.Button(dlg.buttons, text=tr("取消"), command=dlg.cancel).pack(side="right", padx=(0, 8))
        dlg.wait()

    # ══════════════ 进出管理 & 协商建房 ══════════════

    def _open_occupants_dialog(self):
        bg = COLORS["bg"]
        dlg = Modal(self.winfo_toplevel(), tr("房间成员进出管理"),
                    width=config.scaled(520), height=config.scaled(400))
        cur_rid = self.store.current_room_id()
        cur_name = self.store.current_room()["name"]
        tk.Label(dlg.body, text=tr("当前所在房间：{name}。可以为每个人设选择加入当前房间、切换房间或退出房间。", name=cur_name),
                 bg=bg, fg=COLORS["text_dim"], font=config.font("small"), anchor="w", wraplength=470).pack(
            fill="x", pady=(0, 8))

        personas = list_personas(self.comm.personas) if self.comm else []
        rooms = self.store.room_list()
        room_names = [tr("不在任何房间")] + [r["name"] for r in rooms]
        room_ids = [""] + [r["id"] for r in rooms]
        combos = {}

        for pid in personas:
            row = tk.Frame(dlg.body, bg=COLORS["bg_card"], highlightthickness=1,
                           highlightbackground=COLORS["border"])
            row.pack(fill="x", pady=3)
            tk.Label(row, text=persona_title(pid), bg=COLORS["bg_card"], fg=COLORS["text"],
                     font=config.font("body"), width=14, anchor="w").pack(side="left", padx=10, pady=6)
            cur = self.store.occupants.get(pid, "")
            var = tk.StringVar(value=room_names[room_ids.index(cur)] if cur in room_ids else room_names[0])
            cb = ttk.Combobox(row, textvariable=var, values=room_names, state="readonly", width=18)
            cb.pack(side="right", padx=10, pady=6)
            combos[pid] = var

        def apply_all():
            for pid, var in combos.items():
                label = var.get()
                idx = room_names.index(label) if label in room_names else 0
                target_rid = room_ids[idx]
                if not target_rid:
                    self.store.leave_room(pid, persona_title(pid))
                else:
                    self.store.join_room(pid, target_rid, persona_title(pid))
            dlg.finish(True)
            self.refresh()

        ttk.Button(dlg.buttons, text=tr("保存"), style="Accent.TButton", command=apply_all).pack(side="right")
        ttk.Button(dlg.buttons, text=tr("取消"), command=dlg.cancel).pack(side="right", padx=(0, 8))
        dlg.wait()

    def _open_create_room_dialog(self):
        if self._generating_map:
            toast.info(tr("正在生成地图，请稍候…"))
            return
        bg = COLORS["bg"]
        dlg = Modal(self.winfo_toplevel(), tr("协商新建字符画房间"),
                    width=config.scaled(480), height=config.scaled(320))
        tk.Label(dlg.body, text=tr("输入你想和 AI 协商的主题与场景细节，AI 会为你生成一张可点击的字符画地图。"),
                 bg=bg, fg=COLORS["text_dim"], font=config.font("small"), anchor="w", wraplength=430).pack(
            fill="x", pady=(0, 8))
        tk.Label(dlg.body, text=tr("房间主题（如：深夜天文台、雨夜旧书店）："), bg=bg, fg=COLORS["text"],
                 font=config.font("body"), anchor="w").pack(fill="x")
        var_theme = tk.StringVar()
        ttk.Entry(dlg.body, textvariable=var_theme).pack(fill="x", pady=(2, 8))
        tk.Label(dlg.body, text=tr("场景设定与想要包含的设施："), bg=bg, fg=COLORS["text"],
                 font=config.font("body"), anchor="w").pack(fill="x")
        txt = tk.Text(dlg.body, height=4, wrap="word", font=config.font("body"),
                      bg=COLORS["bg_card"], fg=COLORS["text"], insertbackground=COLORS["text"],
                      relief="flat", highlightthickness=1, highlightbackground=COLORS["border"])
        txt.pack(fill="x", pady=(2, 0))

        def start_gen():
            theme = var_theme.get().strip()
            if not theme:
                toast.warn(tr("请先输入房间主题"))
                return
            desc = txt.get("1.0", "end").strip()
            dlg.finish(True)
            self._generate_room_async(theme, desc)

        ttk.Button(dlg.buttons, text=tr("由 AI 生成字符画地图"), style="Accent.TButton",
                   command=start_gen).pack(side="right")
        ttk.Button(dlg.buttons, text=tr("取消"), command=dlg.cancel).pack(side="right", padx=(0, 8))
        dlg.wait()

    def _generate_room_async(self, theme: str, desc: str):
        if not self.comm:
            return
        try:
            profile = self.comm.profile()
            ai_assist.check_profile(profile)
        except Exception as exc:
            toast.warn(tr("AI 模型未配置：{err}", err=exc))
            return

        self._generating_map = True
        self._update_busy_label(self._persona_names())
        system, msgs = build_map_generation_prompt(theme, desc)

        def work():
            try:
                fn = self.comm.remote_chat if profile.get("kind") == "device" and callable(self.comm.remote_chat) else self.comm.chat_fn
                reply = fn(profile, system, msgs, max_tokens=1600, temperature=0.8, timeout=90.0)
                room = parse_generated_room(reply, fallback_theme=theme)
                self.after(0, lambda: self._finish_generate_room(room, ""))
            except Exception as exc:
                self.after(0, lambda e=exc: self._finish_generate_room(None, str(e)))

        threading.Thread(target=work, daemon=True, name="oit-room-map-gen").start()

    def _finish_generate_room(self, room: dict | None, err: str):
        self._generating_map = False
        if err or not room:
            toast.warn(tr("生成房间地图失败：{err}", err=err or "返回格式无效"))
            self._update_busy_label(self._persona_names())
            return
        saved = self.store.add_room(room)
        self.store.join_room("user", saved["id"], self._actor_name("user"))
        toast.info(tr("已创建并进入新房间「{name}」", name=saved["name"]))
        self.refresh()

    # ══════════════ 发送互动与多角色回复 ══════════════

    def _on_return(self, event):
        if event.state & 0x1:  # Shift+Return 换行
            return None
        self._send()
        return "break"

    def _send(self):
        text = self.entry.get("1.0", "end").strip()
        actions = list(self.store.pending_actions)
        if not text and not actions:
            return
        room_id = self.store.current_room_id()
        targets = self._selected_targets()

        self.entry.delete("1.0", "end")
        self.store.append_log(room_id, "user", self._actor_name("user"), targets,
                              "action" if actions and not text else "say", text, actions=actions)
        self.store.reset_turn_hands(save=True)
        self.refresh()

        # 决定哪些在场 NPC 需要回应
        occ_npcs = [aid for aid in self.store.occupants_in(room_id) if aid != "user"]
        if not occ_npcs:
            return
        responders = []
        if "all" not in targets:
            responders = [aid for aid in targets if aid in occ_npcs]
        else:
            # 优先回应被手部互动点到的人，否则当前人设或第一位在场角色
            acted = [a["target_id"] for a in actions if a.get("target_id") in occ_npcs]
            if acted:
                responders = list(dict.fromkeys(acted))
            else:
                cur_p = self.comm.persona_name() if self.comm else ""
                if cur_p in occ_npcs:
                    responders = [cur_p]
                else:
                    responders = [occ_npcs[0]]

        for aid in responders[:2]:
            self._trigger_actor_reply(room_id, aid, trigger_reason="user")

    def _trigger_actor_reply(self, room_id: str, actor_id: str, trigger_reason: str = "user"):
        if not self.comm or actor_id in self._busy_actors:
            return
        try:
            profile = self.comm.profile()
            ai_assist.check_profile(profile)
        except Exception:
            if trigger_reason == "user":
                toast.warn(tr("请先在 设置 → 模型 中配置 AI 接口。"))
            return

        room = self.store.rooms.get(room_id)
        if not room:
            return
        names = self._persona_names()
        occ_ids = self.store.occupants_in(room_id)
        occ_names = {aid: names.get(aid, aid) for aid in occ_ids}
        visible = self.store.visible_logs_for(actor_id, room_id)
        psys = persona_system(self.comm.personas, actor_id, False)
        system, msgs = build_actor_turn_prompt(
            psys, room, actor_id, names.get(actor_id, actor_id),
            occ_names, visible, self.store.hands, trigger_reason=trigger_reason,
        )

        self._busy_actors.add(actor_id)
        self._update_busy_label(names)

        def work():
            try:
                fn = self.comm.remote_chat if profile.get("kind") == "device" and callable(self.comm.remote_chat) else self.comm.chat_fn
                reply = fn(profile, system, msgs, max_tokens=1200, temperature=0.85, timeout=90.0)
                parsed = parse_actor_reply(reply, set(occ_ids), default_target="user")
                self.after(0, lambda: self._finish_actor_reply(room_id, actor_id, parsed, ""))
            except Exception as exc:
                self.after(0, lambda e=exc: self._finish_actor_reply(room_id, actor_id, None, str(e)))

        threading.Thread(target=work, daemon=True, name=f"oit-room-{actor_id}").start()

    def _finish_actor_reply(self, room_id: str, actor_id: str, parsed: dict | None, err: str):
        self._busy_actors.discard(actor_id)
        if err or not parsed:
            self._update_busy_label(self._persona_names())
            return

        # 处理束缚/解除手部状态
        bind = parsed.get("bind_hands")
        if bind == "release":
            self.store.set_hand_bound("both", False)
        elif bind in ("left", "right", "both"):
            self.store.set_hand_bound(bind, True, note=self._actor_name(actor_id))

        speech = parsed.get("speech") or ""
        action = parsed.get("action") or ""
        combined = f"（{action}）{speech}".strip() if action and speech else (f"（{action}）" if action else speech)
        if combined:
            self.store.append_log(room_id, actor_id, self._actor_name(actor_id),
                                  parsed.get("to") or ["user"], "say", combined)
        self.refresh()

    # ══════════════ NPC 自动发起对话调度 ══════════════

    def _schedule_auto_tick(self):
        if self._auto_job:
            try:
                self.after_cancel(self._auto_job)
            except tk.TclError:
                pass
            self._auto_job = None
        if not self._visible:
            return
        interval_s = max(10, min(300, int(self.comm.cfg.get("room_npc_interval") or 30))) if self.comm else 30
        self._auto_job = self.after(interval_s * 1000, self._on_auto_tick)

    def _on_auto_tick(self):
        self._auto_job = None
        if not self._visible or not self.comm:
            return
        enabled = bool(self.comm.cfg.get("room_npc_chat", False))
        prob = int(self.comm.cfg.get("room_npc_prob") or 30)
        rid = self.store.current_room_id()
        speaker = pick_auto_speaker(self.store, rid, self._busy_actors, enabled, prob)
        if speaker:
            self._trigger_actor_reply(rid, speaker, trigger_reason="auto")
        self._schedule_auto_tick()
