# -*- coding: utf-8 -*-
"""🏠 情景房间子页面（PC 优先）：分人设房间、钥匙与内部反锁、按条数解锁部位、定向听觉隔离与 NPC 自主移动/对话。"""

import sys
import threading
import tkinter as tk
from tkinter import ttk

import ai_assist
import config
import toast
from comm.comm_persona import list_personas, persona_system, persona_title
from comm.comm_room import (
    BODY_PARTS,
    CUSTOM_PART_MIN_MSGS,
    RoomStore,
    build_actor_turn_prompt,
    build_map_generation_prompt,
    parse_actor_reply,
    parse_generated_room,
    part_allowed,
    pick_auto_speaker,
    position_interactions_for,
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

        self.store = comm.room_store if (comm is not None and hasattr(comm, "room_store")) else RoomStore(data_dir)
        if comm is not None:
            comm.on_room_changed = lambda: self.after(0, self.refresh) if self.winfo_exists() else None
        self._visible = False
        self._auto_job = None
        self._busy_actors: set[str] = set()
        self._generating_map = False
        self._hotspots: list[dict] = []
        self._var_volume = tk.StringVar(value="normal")
        self._room_ids: list[str] = []

        self._sync_persona_rooms()
        self._build()
        self.refresh()

    def _sync_persona_rooms(self):
        """确保所有人设（含新建人设）都拥有专属房间，且专注/睡眠期间自动锁在自己房间里。"""
        if not self.comm:
            return
        personas = list_personas(self.comm.personas)
        plist = [(p, persona_title(p)) for p in personas]
        self.store.sync_personas(plist)
        names = {p: title for p, title in plist}
        status_map = self._schedule_status_map(personas)
        self.store.sync_schedule_locks(status_map, names)

    def _schedule_status_map(self, personas: list[str] = None) -> dict[str, str]:
        if not self.comm or not hasattr(self.comm, "persona_status_kind"):
            return {}
        ps = personas if personas is not None else list_personas(self.comm.personas)
        return {p: self.comm.persona_status_kind(p) for p in ps}

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

    def total_msgs_with(self, actor_id: str) -> int:
        """与该角色的总互动条数 = 沟通记录条数（含分条气泡） + 房间内互动条数。"""
        chat_n = self.comm.msg_total_for(actor_id) if (self.comm and hasattr(self.comm, "msg_total_for")) else 0
        return chat_n + self.store.room_msg_count(actor_id)

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

        ttk.Button(hdr, text=tr("＋ 协商建房"), command=self._open_create_room_dialog).pack(side="right", padx=(6, 0))
        ttk.Button(hdr, text=tr("🔑 钥匙管理"), command=self._open_keys_dialog).pack(side="right", padx=(6, 0))
        self.var_room = tk.StringVar()
        self.cb_room = ttk.Combobox(hdr, textvariable=self.var_room, state="readonly", width=24)
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

        map_font_family = "NSimSun" if sys.platform == "win32" else "TkFixedFont"
        self.map_text = tk.Text(
            left, wrap="none", state="disabled",
            font=(map_font_family, max(11, config.scaled(12))),
            bg="#12161f", fg="#dce4f2",
            relief="flat", bd=0, padx=14, pady=12,
            cursor="arrow", highlightthickness=1, highlightbackground="#293245",
        )
        self.map_text.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.map_text.tag_configure("fac", foreground="#67d5f7", underline=False)
        self.map_text.tag_configure("pos", foreground="#94e2d5", underline=False)
        self.map_text.tag_configure("door", foreground="#f6c177", underline=False)
        self.map_text.tag_configure("actor", foreground="#c4a7e7", underline=False)
        self.map_text.tag_configure("actor_me", foreground="#a6e3a1", underline=False)
        self.map_text.tag_configure("hover", background="#24344d", underline=True)

        self.lbl_hover = tk.Label(left, text=tr("点击地图中的 [设施] 打开对应功能，点击 [@人物] 展开部位互动。"),
                                  bg=card, fg=COLORS["accent"], font=config.font("small"), anchor="w")
        self.lbl_hover.pack(fill="x", padx=12, pady=(0, 6))

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
        self.lbl_log_title = tk.Label(log_head, text=tr("📜 房间记录"), bg=card, fg=COLORS["text"],
                                      font=config.font("body", bold=True))
        self.lbl_log_title.pack(side="left")
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
        tk.Label(hand_bar, text=tr("(点手部可松开牵手或切换束缚)"), bg=card, fg=COLORS["text_dim"],
                 font=config.font("small")).pack(side="left")

        self.pending_frame = tk.Frame(right, bg=card)
        self.pending_frame.pack(fill="x", padx=12, pady=(2, 2))

        vol_bar = tk.Frame(right, bg=card)
        vol_bar.pack(fill="x", padx=12, pady=(2, 4))
        tk.Label(vol_bar, text=tr("音量："), bg=card, fg=COLORS["text_dim"],
                 font=config.font("small")).pack(side="left")
        ttk.Radiobutton(vol_bar, text=tr("正常音量"), value="normal", variable=self._var_volume,
                        command=self._update_volume_hint).pack(side="left", padx=(2, 8))
        ttk.Radiobutton(vol_bar, text=tr("轻语"), value="whisper", variable=self._var_volume,
                        command=self._update_volume_hint).pack(side="left", padx=(0, 8))
        self.lbl_vol_hint = tk.Label(vol_bar, text="", bg=card, fg=COLORS["text_dim"],
                                     font=config.font("small"), anchor="w")
        self.lbl_vol_hint.pack(side="left", fill="x", expand=True)

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
        if self.comm is not None:
            self.comm.room_viewing = True
        self._sync_persona_rooms()
        self.refresh()
        self._schedule_auto_tick()
        self.entry.focus_set()

    def hide(self):
        self._visible = False
        if self.comm is not None:
            self.comm.room_viewing = False
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

    def _room_badge(self, r: dict) -> str:
        if r.get("locked_inside"):
            return "⛔"
        if r.get("is_public"):
            return "🌐"
        return "🔑" if self.store.has_key("user", r["id"]) else "🔒"

    def refresh(self):
        self._sync_persona_rooms()
        room = self.store.current_room()
        rid = room["id"]
        names = self._persona_names()

        lock_reason = room.get("locked_reason") or ""
        if room.get("locked_inside"):
            if lock_reason == "focus":
                lock_tag = tr(" · 专注中已反锁")
            elif lock_reason == "sleep":
                lock_tag = tr(" · 睡眠中已反锁")
            else:
                lock_tag = tr(" · 已内部反锁")
        else:
            lock_tag = ""
        cfg(self.lbl_room_title, text=f"🏠 {room['name']}")
        cfg(self.lbl_room_theme, text=f"[{room['theme']}{lock_tag}]",
            fg="#ef4444" if room.get("locked_inside") else COLORS["accent"])
        cfg(self.lbl_ambience, text=room.get("ambience") or tr("安静的文字空间。"))
        cfg(self.lbl_log_title, text=tr("📜 房间记录（即时）"))

        rooms = self.store.room_list()
        self._room_ids = [r["id"] for r in rooms]
        labels = [f"{self._room_badge(r)} {r['name']} ({len(self.store.occupants_in(r['id']))}人)" for r in rooms]
        self.cb_room.configure(values=labels)
        if rid in self._room_ids:
            self.var_room.set(labels[self._room_ids.index(rid)])

        npc_on = bool(self.comm.cfg.get("room_npc_chat", False)) if self.comm else False
        interval = int(self.comm.cfg.get("room_npc_interval") or 30) if self.comm else 30
        prob = int(self.comm.cfg.get("room_npc_prob") or 30) if self.comm else 30
        idle_m = int(self.comm.cfg.get("room_npc_idle_min") or 15) if self.comm else 15
        if npc_on:
            cfg(self.lbl_auto_status, text=tr("● 自动行动已开 ({s}s/{p}% · {m}m静默)", s=interval, p=prob, m=idle_m),
                fg=COLORS["accent"])
        else:
            cfg(self.lbl_auto_status, text=tr("○ 自动行动未开 (点此设置)"), fg=COLORS["text_dim"])

        self._render_map(room, names)
        self._render_occupant_chips(rid, names)
        self._update_volume_hint()
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
            elif hs["action"] == "door":
                base_tag = "door"
            elif hs["action"] == "position":
                base_tag = "pos"
            else:
                base_tag = "fac"
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
        sched = self._schedule_status_map()
        for aid in self.store.occupants_in(room_id):
            name = names.get(aid, aid)
            is_me = (aid == "user")
            pos_tag = self.store.position_of(aid)
            act_tag = "" if is_me else self.store.activity_label(aid, schedule_kind=sched.get(aid, ""))
            extras = [x for x in (pos_tag, act_tag) if x and x != "房间中央"]
            suffix = f" ({' · '.join(extras)})" if extras else ""
            chip_text = f"@{name}" + (" [我]" if is_me else "") + suffix
            lbl = tk.Label(
                self.occ_chips,
                text=chip_text,
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

    def _update_volume_hint(self):
        rid = self.store.current_room_id()
        names = self._persona_names()
        vol = self._var_volume.get()
        if vol == "whisper":
            pos = self.store.position_of("user")
            listeners = [names.get(a, a) for a in self.store.listeners_for_volume(rid, "user", "whisper") if a != "user"]
            if listeners:
                hint = tr("仅「{pos}」可听：{who}", pos=pos, who="、".join(listeners))
            else:
                hint = tr("仅「{pos}」可听（附近暂无其他人）", pos=pos)
        else:
            hint = tr("房间内所有人都能听见")
        cfg(self.lbl_vol_hint, text=hint)

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
            sec_txt = "·隐秘" if act.get("secret") else ""
            txt = f"[{hand_txt}{sec_txt} → {act['target_name']}·{act['part']}] {act['text']}"
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
        st = self.store.hands[side]["state"]
        if st == "holding":
            self.store.release_hand(side, self._persona_names())
            self.refresh()
            self._notify_room_action_change()
            return
        cur_bound = (st == "bound")
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
                tag = "who_me" if e.get("sender") == "user" else "who_npc"
                if e.get("volume") == "whisper":
                    header = f"{e['sender_name']}（在「{e.get('pos') or '原处'}」轻语）：\n"
                else:
                    header = f"{e['sender_name']}：\n"
                w.insert("end", header, tag)
                for a in e.get("actions") or []:
                    h = "左手" if a.get("hand") == "left" else "右手"
                    sec = "·隐秘" if a.get("secret") else ""
                    w.insert("end", f"  [{h}{sec}→{a.get('target_name')}·{a.get('part')}] {a.get('text')}\n", "act")
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
            cfg(self.lbl_busy, text=tr("{who} 正在行动…", who=who))
        else:
            cfg(self.lbl_busy, text="")

    # ══════════════ 地图点击、门锁与钥匙 ══════════════

    def _toggle_lock(self):
        room = self.store.current_room()
        err = self.store.set_room_lock(room["id"], "user", not room.get("locked_inside"), self._actor_name("user"))
        if err:
            toast.warn(tr(err))
        self.refresh()

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
        elif action == "position":
            if self.store.move_position("user", hs["label"], self._actor_name("user")):
                toast.info(tr("你移动到了「{pos}」，现在点击人物可触发相关位置互动。", pos=hs["label"]))
                self.refresh()
                self._notify_room_action_change()
            else:
                self._open_custom_facility_interact(hs)
        elif action == "door":
            items = []
            cur_room = self.store.current_room()
            cur_rid = cur_room["id"]
            lock_label = tr("🔓 解除房门反锁") if cur_room.get("locked_inside") else tr("🔒 从内部反锁房门")
            items.append((lock_label, self._toggle_lock))
            for r in self.store.room_list():
                cnt = len(self.store.occupants_in(r["id"]))
                mark = "✓ " if r["id"] == cur_rid else f"{self._room_badge(r)} "
                items.append((f"{mark}{r['name']} ({cnt}人)", lambda rid=r["id"]: self._switch_user_room(rid)))
            items.append((tr("🔑 管理房间钥匙…"), self._open_keys_dialog))
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
        err = self.store.enter_room("user", room_id, self._actor_name("user"), name_map=self._persona_names())
        if err:
            toast.warn(err)
            self.refresh()
            return
        self.refresh()

    # ══════════════ 部位互动弹窗（按条数解锁） ══════════════

    def _open_actor_interact(self, actor_id: str, actor_name: str):
        avail = self.store.available_hands()
        msg_count = self.total_msgs_with(actor_id)
        user_pos = self.store.position_of("user")
        target_pos = self.store.position_of(actor_id)
        pos_opts = position_interactions_for(user_pos, target_pos)
        holding_side = next((s for s in ("left", "right") if self.store.holding_hands.get(s) == actor_id), "")
        bg = COLORS["bg"]
        dlg = Modal(self.winfo_toplevel(), tr("与 {name} 互动", name=actor_name),
                    width=config.scaled(540), height=config.scaled(540 if pos_opts else 490))

        top_info = tk.Frame(dlg.body, bg=bg)
        top_info.pack(fill="x", pady=(0, 6))
        tk.Label(top_info, text=tr("累计消息：{n} 条 | 位置：你在「{up}」· 对方在「{tp}」",
                                   n=msg_count, up=user_pos, tp=target_pos),
                 bg=bg, fg=COLORS["accent"], font=config.font("small"), anchor="w").pack(side="left", fill="x", expand=True)

        def toggle_hold():
            if holding_side:
                self.store.release_hand(holding_side, self._persona_names())
                dlg.finish(True)
                self.refresh()
                self._notify_room_action_change()
                return
            free = self.store.available_hands()
            if not free:
                toast.warn(tr("当前没有空闲的手来牵住对方。"))
                return
            err = self.store.hold_hand(free[0], actor_id, actor_name)
            if err:
                toast.warn(tr(err))
                return
            toast.info(tr("已牵住 {name} 的手，切换房间时会把对方一起带出去。", name=actor_name))
            dlg.finish(True)
            self.refresh()
            self._notify_room_action_change()

        ttk.Button(top_info, text=tr("松开牵手") if holding_side else tr("🤝 牵手带走"),
                   command=toggle_hold).pack(side="right")
        if user_pos != target_pos:
            def move_near():
                self.store.move_position("user", target_pos, self._actor_name("user"))
                dlg.finish(True)
                self.refresh()
                self._notify_room_action_change()
            ttk.Button(top_info, text=tr("走到 TA 身边"), command=move_near).pack(side="right", padx=(0, 6))

        hand_row = tk.Frame(dlg.body, bg=bg)
        hand_row.pack(fill="x", pady=(0, 8))
        tk.Label(hand_row, text=tr("使用哪只手："), bg=bg, fg=COLORS["text"],
                 font=config.font("body")).pack(side="left")
        var_hand = tk.StringVar(value=avail[0] if avail else "")
        for side, label in (("left", "左手"), ("right", "右手")):
            st = self.store.hands[side]["state"]
            suffix = "" if st == "free" else (" (已用)" if st == "used" else (" (牵手中)" if st == "holding" else " (被束缚)"))
            rb = ttk.Radiobutton(hand_row, text=tr(label) + suffix, value=side, variable=var_hand)
            if st != "free":
                rb.state(["disabled"])
            rb.pack(side="left", padx=(8, 0))

        if not avail:
            tk.Label(dlg.body, text=tr("当前没有空闲的手（已用/牵手中或被束缚），无法添加新的手部互动。"),
                     bg=bg, fg="#ef4444", font=config.font("small"), anchor="w", wraplength=490).pack(
                fill="x", pady=(0, 6))

        tk.Label(dlg.body, text=tr("选择互动部位："), bg=bg, fg=COLORS["text"],
                 font=config.font("body"), anchor="w").pack(fill="x")
        var_part = tk.StringVar(value=BODY_PARTS[0][1])
        grid = tk.Frame(dlg.body, bg=bg)
        grid.pack(fill="x", pady=(4, 6))
        for i, (_pid, plabel, req) in enumerate(BODY_PARTS):
            ok, _ = part_allowed(plabel, msg_count)
            text = tr(plabel) if ok else tr("{label} (需{req}条)", label=plabel, req=req)
            rb = ttk.Radiobutton(grid, text=text, value=plabel, variable=var_part)
            if not ok:
                rb.state(["disabled"])
            rb.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 14), pady=2)

        if pos_opts:
            tk.Label(dlg.body, text=tr("基于当前位置的互动："), bg=bg, fg=COLORS["accent"],
                     font=config.font("small", bold=True), anchor="w").pack(fill="x", pady=(2, 2))
            pgrid = tk.Frame(dlg.body, bg=bg)
            pgrid.pack(fill="x", pady=(0, 6))
            for i, (_pid, plabel, req) in enumerate(pos_opts):
                ok, _ = part_allowed(plabel, msg_count)
                text = tr(plabel) if ok else tr("{label} (需{req}条)", label=plabel, req=req)
                rb = ttk.Radiobutton(pgrid, text=text, value=plabel, variable=var_part)
                if not ok:
                    rb.state(["disabled"])
                rb.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 14), pady=2)

        custom_ok = msg_count >= CUSTOM_PART_MIN_MSGS
        custom_row = tk.Frame(dlg.body, bg=bg)
        custom_row.pack(fill="x", pady=(0, 8))
        tk.Label(custom_row, text=tr("或自定义部位：") if custom_ok else tr("自定义部位 (需{n}条)：", n=CUSTOM_PART_MIN_MSGS),
                 bg=bg, fg=COLORS["text_dim"], font=config.font("small")).pack(side="left")
        var_custom_part = tk.StringVar()
        ent_custom = ttk.Entry(custom_row, textvariable=var_custom_part, width=22,
                               state="normal" if custom_ok else "disabled")
        ent_custom.pack(side="left", padx=(6, 0))

        var_secret = tk.BooleanVar(value=False)
        tk.Checkbutton(dlg.body, text=tr("隐秘进行（仅你与 {name} 知晓，房间内其他人看不见）", name=actor_name),
                       variable=var_secret, bg=bg, fg=COLORS["text_dim"], selectcolor=bg,
                       activebackground=bg, activeforeground=COLORS["text"],
                       font=config.font("small"), anchor="w").pack(fill="x", pady=(4, 2))

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
            part = (var_custom_part.get().strip() if custom_ok else "") or var_part.get()
            action_text = txt.get("1.0", "end").strip()
            err = self.store.queue_hand_action(hand, actor_id, actor_name, part, action_text,
                                               msg_count=msg_count, secret=bool(var_secret.get()))
            if err:
                toast.warn(err)
                return
            dlg.finish(True)
            self._render_hands()

        ttk.Button(dlg.buttons, text=tr("加入待发动作"), style="Accent.TButton",
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

        ttk.Button(dlg.buttons, text=tr("加入待发动作"), style="Accent.TButton",
                   command=submit, state="normal" if avail else "disabled").pack(side="right")
        ttk.Button(dlg.buttons, text=tr("取消"), command=dlg.cancel).pack(side="right", padx=(0, 8))
        dlg.wait()

    # ══════════════ 钥匙分发管理 & 协商建房 ══════════════

    def _open_keys_dialog(self):
        bg = COLORS["bg"]
        names = self._persona_names()
        personas = [p for p in (list_personas(self.comm.personas) if self.comm else [])]
        dlg = Modal(self.winfo_toplevel(), tr("🔑 钥匙与门禁管理"),
                    width=config.scaled(540), height=config.scaled(430))
        tk.Label(dlg.body, text=tr("每个人默认拥有自己房间的钥匙，公共空间无需钥匙。你可以把手里拥有的钥匙分发给其他人；门从内部反锁后即使有钥匙也打不开。"),
                 bg=bg, fg=COLORS["text_dim"], font=config.font("small"), anchor="w", wraplength=490).pack(
            fill="x", pady=(0, 8))

        shareable = [r for r in self.store.room_list() if not r.get("is_public") and self.store.has_key("user", r["id"])]
        if shareable and personas:
            box = tk.Frame(dlg.body, bg=COLORS["bg_card"], highlightthickness=1, highlightbackground=COLORS["border"])
            box.pack(fill="x", pady=(0, 10))
            tk.Label(box, text=tr("分发我持有的钥匙"), bg=COLORS["bg_card"], fg=COLORS["text"],
                     font=config.font("body", bold=True), anchor="w").pack(fill="x", padx=10, pady=(8, 4))
            row = tk.Frame(box, bg=COLORS["bg_card"])
            row.pack(fill="x", padx=10, pady=(0, 8))
            r_labels = [r["name"] for r in shareable]
            r_ids = [r["id"] for r in shareable]
            var_r = tk.StringVar(value=r_labels[0])
            ttk.Combobox(row, textvariable=var_r, values=r_labels, state="readonly", width=16).pack(side="left")
            tk.Label(row, text=tr(" 分给 "), bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                     font=config.font("small")).pack(side="left")
            p_labels = [names.get(p, p) for p in personas]
            var_p = tk.StringVar(value=p_labels[0])
            ttk.Combobox(row, textvariable=var_p, values=p_labels, state="readonly", width=14).pack(side="left")

            def do_grant():
                rid = r_ids[r_labels.index(var_r.get())]
                pid = personas[p_labels.index(var_p.get())]
                err = self.store.grant_key(rid, "user", pid, self._actor_name("user"), names.get(pid, pid))
                if err:
                    toast.warn(tr(err))
                else:
                    toast.info(tr("已把「{r}」的钥匙分给 {p}", r=var_r.get(), p=var_p.get()))
                    dlg.finish(True)
                    self.refresh()

            ttk.Button(row, text=tr("分发"), style="Accent.TButton", command=do_grant).pack(side="left", padx=(8, 0))

        # 所有房间钥匙持有与门锁总览
        tk.Label(dlg.body, text=tr("房间门禁与当前位置总览："), bg=bg, fg=COLORS["text"],
                 font=config.font("body", bold=True), anchor="w").pack(fill="x", pady=(0, 4))
        for r in self.store.room_list():
            occ = "、".join(names.get(a, a) for a in self.store.occupants_in(r["id"])) or tr("空")
            if r.get("is_public"):
                key_txt = tr("公共空间（无需钥匙）")
            else:
                key_txt = tr("钥匙持有：{k}", k="、".join(names.get(k, k) for k in (r.get("keys") or [])))
            lock_txt = tr(" [已内部反锁]") if r.get("locked_inside") else ""
            tk.Label(dlg.body, text=f"{self._room_badge(r)} {r['name']}{lock_txt} — {key_txt} | 在场：{occ}",
                     bg=bg, fg=COLORS["text_dim"], font=config.font("small"), anchor="w", wraplength=490).pack(
                fill="x", pady=2)

        ttk.Button(dlg.buttons, text=tr("关闭"), command=dlg.cancel).pack(side="right")
        dlg.wait()

    def _open_create_room_dialog(self):
        if self._generating_map:
            toast.info(tr("正在生成地图，请稍候…"))
            return
        bg = COLORS["bg"]
        dlg = Modal(self.winfo_toplevel(), tr("协商新建字符画房间"),
                    width=config.scaled(480), height=config.scaled(350))
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
        txt.pack(fill="x", pady=(2, 6))
        var_public = tk.BooleanVar(value=False)
        tk.Checkbutton(dlg.body, text=tr("设为公共空间（所有人无需钥匙皆可进入；否则默认归创建者持有钥匙）"),
                       variable=var_public, bg=bg, fg=COLORS["text"], selectcolor=bg,
                       activebackground=bg, activeforeground=COLORS["text"],
                       font=config.font("small"), anchor="w").pack(fill="x")

        def start_gen():
            theme = var_theme.get().strip()
            if not theme:
                toast.warn(tr("请先输入房间主题"))
                return
            desc = txt.get("1.0", "end").strip()
            is_pub = bool(var_public.get())
            dlg.finish(True)
            self._generate_room_async(theme, desc, is_pub)

        ttk.Button(dlg.buttons, text=tr("由 AI 生成字符画地图"), style="Accent.TButton",
                   command=start_gen).pack(side="right")
        ttk.Button(dlg.buttons, text=tr("取消"), command=dlg.cancel).pack(side="right", padx=(0, 8))
        dlg.wait()

    def _generate_room_async(self, theme: str, desc: str, is_public: bool = False):
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
                self.after(0, lambda: self._finish_generate_room(room, is_public, ""))
            except Exception as exc:
                self.after(0, lambda e=exc: self._finish_generate_room(None, is_public, str(e)))

        threading.Thread(target=work, daemon=True, name="oit-room-map-gen").start()

    def _finish_generate_room(self, room: dict | None, is_public: bool, err: str):
        self._generating_map = False
        if err or not room:
            toast.warn(tr("生成房间地图失败：{err}", err=err or "返回格式无效"))
            self._update_busy_label(self._persona_names())
            return
        saved = self.store.add_room(room, creator_id="user", is_public=is_public)
        self.store.enter_room("user", saved["id"], self._actor_name("user"))
        toast.info(tr("已创建并进入新房间「{name}」", name=saved["name"]))
        self.refresh()

    # ══════════════ 发送互动、回合推进与多角色回复 ══════════════

    def _on_return(self, event):
        if event.state & 0x1:
            return None
        self._send()
        return "break"

    def _notify_room_action_change(self):
        """切换公开动作（牵手/松手/换位置）时，通知同房角色观察并决定是否回应（可 silent）。"""
        room_id = self.store.current_room_id()
        occ_npcs = [aid for aid in self.store.occupants_in(room_id) if aid != "user"]
        for aid in occ_npcs[:3]:
            self._trigger_actor_reply(room_id, aid, trigger_reason="observe")

    def _send(self):
        text = self.entry.get("1.0", "end").strip()
        actions = list(self.store.pending_actions)
        if not text and not actions:
            return
        room_id = self.store.current_room_id()
        volume = self._var_volume.get() or "normal"
        targets = self.store.listeners_for_volume(room_id, "user", volume)

        self.entry.delete("1.0", "end")
        self.store.append_log(room_id, "user", self._actor_name("user"), targets,
                              "action" if actions and not text else "say", text,
                              actions=actions, volume=volume, pos=self.store.position_of("user"))
        self.store.reset_turn_hands(save=True)
        self.refresh()

        occ_npcs = [aid for aid in self.store.occupants_in(room_id) if aid != "user"]
        if not occ_npcs:
            return
        has_public_action = any(not a.get("secret") for a in actions)
        acted_targets = list(dict.fromkeys(a["target_id"] for a in actions if a.get("target_id") in occ_npcs))

        # 直接听见说话或被接触的角色为主回应者；同房里看见公开动作的旁观者以 observe 触发（可 silent）
        direct = []
        if text:
            direct = list(occ_npcs) if "all" in targets else [a for a in targets if a in occ_npcs]
        for aid in acted_targets:
            if aid not in direct:
                direct.append(aid)
        observers = [aid for aid in occ_npcs if aid not in direct] if has_public_action else []

        for aid in direct[:2]:
            self._trigger_actor_reply(room_id, aid, trigger_reason="user")
        for aid in observers[:2]:
            self._trigger_actor_reply(room_id, aid, trigger_reason="observe")

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
        visible = self.store.visible_logs_for(actor_id, room_id)
        psys = persona_system(self.comm.personas, actor_id, False)
        system, msgs = build_actor_turn_prompt(
            psys, self.store, room, actor_id, names.get(actor_id, actor_id),
            names, visible, trigger_reason=trigger_reason,
        )

        self._busy_actors.add(actor_id)
        self._update_busy_label(names)

        def work():
            try:
                fn = self.comm.remote_chat if profile.get("kind") == "device" and callable(self.comm.remote_chat) else self.comm.chat_fn
                reply = fn(profile, system, msgs, max_tokens=1200, temperature=0.85, timeout=90.0)
                parsed = parse_actor_reply(reply, set(occ_ids), set(self.store.rooms.keys()), default_target="user")
                self.after(0, lambda: self._finish_actor_reply(room_id, actor_id, parsed, ""))
            except Exception as exc:
                self.after(0, lambda e=exc: self._finish_actor_reply(room_id, actor_id, None, str(e)))

        threading.Thread(target=work, daemon=True, name=f"oit-room-{actor_id}").start()

    def _finish_actor_reply(self, room_id: str, actor_id: str, parsed: dict | None, err: str):
        self._busy_actors.discard(actor_id)
        if err or not parsed:
            self._update_busy_label(self._persona_names())
            return

        names = self._persona_names()
        actor_name = names.get(actor_id, actor_id)

        # 1. 束缚/解除用户双手（仅当该角色与用户同房间）
        if self.store.occupants.get("user") == room_id:
            bind = parsed.get("bind_hands")
            if bind == "release":
                self.store.set_hand_bound("both", False)
            elif bind in ("left", "right", "both"):
                self.store.set_hand_bound(bind, True, note=actor_name)

        # 2. 更新室内位置
        pos = parsed.get("position")
        if pos:
            self.store.move_position(actor_id, pos, actor_name)

        # 3. 更新跟随状态（言语请求跟随）
        fol = parsed.get("follow")
        if fol == "none":
            self.store.set_following(actor_id, None, actor_name)
        elif fol:
            self.store.set_following(actor_id, fol, actor_name, names.get(fol, fol))

        # 4. 分发钥匙
        gk = parsed.get("give_key")
        if gk:
            to_id = str(gk.get("to") or "")
            self.store.grant_key(str(gk.get("room_id") or ""), actor_id, to_id,
                                 actor_name, names.get(to_id, to_id))

        # 5. 发言（若 silent=True 且无动作则保持安静不刷屏；否则按音量 normal / whisper 自动决定听众）
        is_silent = bool(parsed.get("silent", False))
        speech = "" if is_silent else (parsed.get("speech") or "")
        action = parsed.get("action") or ""
        combined = f"（{action}）{speech}".strip() if action and speech else (f"（{action}）" if action else speech)
        if combined:
            vol = parsed.get("volume") or "normal"
            listeners = self.store.listeners_for_volume(room_id, actor_id, vol)
            self.store.append_log(room_id, actor_id, actor_name, listeners, "say", combined,
                                  volume=vol, pos=self.store.position_of(actor_id))

        # 6. 自主移动 / 拜访其他房间（bring_user=True 或牵手时可把用户一起带出去）
        move_to = parsed.get("move_to")
        if move_to and move_to != room_id:
            bring = ["user"] if parsed.get("bring_user") else []
            self.store.enter_room(actor_id, move_to, actor_name, bring_ids=bring, name_map=names)
            room_id = self.store.occupants.get(actor_id, room_id)

        # 7. 从内部锁门 / 开门
        if parsed.get("lock_door") is not None:
            self.store.set_room_lock(room_id, actor_id, bool(parsed["lock_door"]), actor_name)

        self.refresh()

    # ══════════════ NPC 自动对话与自主拜访调度 ══════════════

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
        self._sync_persona_rooms()
        enabled = bool(self.comm.cfg.get("room_npc_chat", False))
        prob = int(self.comm.cfg.get("room_npc_prob") or 30)
        idle_min = int(self.comm.cfg.get("room_npc_idle_min") or 15)
        sched = self._schedule_status_map()
        blocked = {aid for aid, k in sched.items() if k in ("focus", "sleep")}
        rid = self.store.current_room_id()
        picked = pick_auto_speaker(self.store, rid, self._busy_actors, enabled, prob,
                                   include_other_rooms=True, blocked_ids=blocked)
        if picked:
            speaker_id, speaker_room = picked
            if self.store.is_recently_interacted(speaker_id, idle_min):
                self._trigger_actor_reply(speaker_room, speaker_id, trigger_reason="auto")
            else:
                self.store.perform_idle_system_step(speaker_id, self._actor_name(speaker_id))
                self.refresh()
        self._schedule_auto_tick()
