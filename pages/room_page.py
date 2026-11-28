# -*- coding: utf-8 -*-
"""🏠 情景房间子页面（PC 优先）：分人设房间、钥匙与内部反锁、按条数解锁部位、定向听觉隔离与 NPC 自主移动/对话。"""

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import ai_assist
import config
import toast
from comm.comm_persona import room_personas, persona_system, persona_title
from comm.comm_room import (
    ACTION_CHANNEL_LABELS,
    ATTENTION_SUMMON,
    BODY_PARTS,
    CUSTOM_PART_MIN_MSGS,
    HOLD_HAND_MIN_MSGS,
    RoomStore,
    VOLUMES,
    auto_step_plan,
    build_actor_turn_prompt,
    build_map_generation_prompt,
    parse_actor_reply,
    parse_generated_room,
    part_allowed,
    position_interactions_for,
    render_room_map,
    summon_targets,
)
from config import COLORS
from i18n import tr
from .room_web import RoomWebServer
from .webview2_host import WebView2Host, is_webview2_available
from .widgets import Modal, cfg


def apply_room_vt(comm, actor_id: str, actions: list[dict]):
    """小屋动作的 V/T 结算：施动方与受动方各自记一次（用户没有 V/T 档案，只结算人设一侧）。"""
    if not comm or not hasattr(comm, "apply_room_action_vt"):
        return
    acts = [a for a in (actions or []) if a.get("part") != "设施"]
    if not acts:
        return
    if actor_id and actor_id != "user":
        comm.apply_room_action_vt(actor_id, acts)
    by_target: dict[str, list[dict]] = {}
    for a in acts:
        tid = str(a.get("target_id") or "")
        if tid and tid not in ("user", actor_id):
            by_target.setdefault(tid, []).append(a)
    for tid, t_acts in by_target.items():
        comm.apply_room_action_vt(tid, t_acts)


class RoomPage(tk.Frame):
    """独立全尺寸情景房间子页面，由沟通页进入（Windows 优先使用原生内嵌 WebView2，无运行时自动回退 Tk）。"""

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
            comm.on_room_changed = lambda: self.after(0, self.refresh) if (not self._destroyed and self.winfo_exists()) else None
        self._visible = False
        self._destroyed = False
        self._auto_job = None
        self._busy_actors: set[str] = set()
        self._actor_request_generation: dict[str, int] = {}
        self._room_log_generation = 0
        self._generating_map = False
        self._hotspots: list[dict] = []
        self._var_volume = tk.StringVar(value="normal")
        self._room_ids: list[str] = []

        self._use_webview = is_webview2_available()
        self._wv_host: WebView2Host | None = None
        self._wv_loading: tk.Label | None = None
        self._web_server: RoomWebServer | None = None
        self._ui_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._ui_poll_job = None
        self._wv_show_job = None
        self._tk_built = False

        self.bind("<Destroy>", self._on_page_destroy)
        self._sync_persona_rooms()
        if self._use_webview:
            self._build_webview()
        else:
            self._build()
        self.refresh()

    def _sync_persona_rooms(self):
        """确保所有人设（含新建人设）都拥有专属房间，且专注/睡眠期间自动锁在自己房间里。"""
        if not self.comm:
            return
        personas = room_personas(self.comm.personas, self.comm.cfg.get("mute_pro"))
        plist = [(p, persona_title(p)) for p in personas]
        self.store.sync_personas(plist)
        self.store.attn_half_min = max(1, int(self.comm.cfg.get("room_npc_idle_min") or 15))
        names = {p: title for p, title in plist}
        status_map = self._schedule_status_map(personas)
        self.store.sync_schedule_locks(status_map, names)

    def _schedule_status_map(self, personas: list[str] = None) -> dict[str, str]:
        if not self.comm or not hasattr(self.comm, "persona_status_kind"):
            return {}
        ps = personas if personas is not None else room_personas(self.comm.personas, self.comm.cfg.get("mute_pro"))
        return {p: self.comm.persona_status_kind(p) for p in ps}

    def _persona_names(self) -> dict[str, str]:
        names = {"user": (self.username() or "你").strip() or "你"}
        if self.comm:
            for p in room_personas(self.comm.personas, self.comm.cfg.get("mute_pro")):
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

    def pair_msgs(self, a: str, b: str) -> int:
        """任意两个实体的互动条数：与用户之间算聊天 + 小屋，角色之间按小屋累计。"""
        if not a or not b or a == b:
            return 0
        if "user" in (a, b):
            return self.total_msgs_with(b if a == "user" else a)
        return self.store.pair_msg_count(a, b)

    def _hands_tag(self, actor_id: str) -> str:
        """在场成员的手部状态标签（被牵着 / 束缚 / 握着别人时才有内容）。"""
        if all(self.store.hand_state(actor_id, s) in ("free", "used") for s in ("left", "right")):
            return ""
        return self.store.hands_brief(actor_id)

    # ══════════════ WebView2 原生内嵌与本地服务 ══════════════

    def _build_webview(self):
        self._web_server = RoomWebServer(
            get_state=lambda: self._call_on_ui(self._build_web_state),
            handle_action=lambda body: self._call_on_ui(lambda: self._handle_web_action(body)),
        )
        self._schedule_ui_poll()

        self._wv_host = WebView2Host(
            self,
            bg=COLORS["bg"],
            on_ready=self._on_webview_ready,
            on_error=self._fallback_to_tk,
        )
        self._wv_host.pack(fill="both", expand=True)

        self._wv_loading = tk.Label(
            self,
            text=tr("正在加载小屋界面…"),
            bg=COLORS["bg"],
            fg=COLORS["text_dim"],
            font=config.font("body"),
        )
        self._wv_loading.place(relx=0.5, rely=0.5, anchor="center")

    def _on_webview_ready(self):
        if self._wv_loading is not None:
            try:
                self._wv_loading.place_forget()
            except tk.TclError:
                pass
        if self._wv_host is not None:
            self._wv_host.set_visible(self._visible)
            if self._visible:
                self.after_idle(self._sync_webview_visible)
                self.after(250, self._sync_webview_visible)
                self.after(800, self._sync_webview_visible)
                self.after(1000, self._dump_webview_diag)

    def _dump_webview_diag(self):
        """将当前 WebView2 嵌入状态写入临时诊断日志，便于排查显示问题。"""
        if self._destroyed or not self._use_webview or self._wv_host is None:
            return
        try:
            import ctypes
            import tempfile
            from ctypes import wintypes

            u32 = ctypes.windll.user32
            h = self._wv_host._host_hwnd
            hr = wintypes.RECT()
            u32.GetClientRect(h, ctypes.byref(hr))
            ch = u32.GetWindow(h, 5) if h else 0
            cr = wintypes.RECT()
            if ch:
                u32.GetWindowRect(ch, ctypes.byref(cr))
            anc = []
            cur = h
            buf = ctypes.create_unicode_buffer(64)
            for _ in range(6):
                if not cur:
                    break
                u32.GetClassNameW(cur, buf, 64)
                st = u32.GetWindowLongW(cur, -16)
                anc.append(f"{cur}:{buf.value}(vis={int(bool(u32.IsWindowVisible(cur)))},ws={int(bool(st & 0x10000000))})")
                cur = u32.GetParent(cur)
            log_dir = os.path.join(tempfile.gettempdir(), "OIT")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "room_webview_diag.txt"), "w", encoding="utf-8") as f:
                f.write(
                    f"ready={self._wv_host.ready} visible={self._visible} "
                    f"host_hwnd={h} host_vis={bool(u32.IsWindowVisible(h)) if h else False} "
                    f"host_client=({hr.left},{hr.top},{hr.right},{hr.bottom}) "
                    f"child_hwnd={ch} child_vis={bool(u32.IsWindowVisible(ch)) if ch else False} "
                    f"child_rect=({cr.left},{cr.top},{cr.right},{cr.bottom}) "
                    f"ancestors={' -> '.join(anc)} "
                    f"url={self._web_server.url if self._web_server else ''}\n"
                )
        except Exception:
            pass

    def _sync_webview_visible(self):
        if self._destroyed or not self._use_webview or self._wv_host is None:
            return
        self._wv_host.sync_bounds()
        self._wv_host.set_visible(self._visible)
        if self._visible:
            self._wv_host.notify_parent_moved()

    def _fallback_to_tk(self, _reason: str = ""):
        if self._destroyed or not self._use_webview:
            return
        self._use_webview = False
        if self._wv_host is not None:
            try:
                self._wv_host.close()
                self._wv_host.destroy()
            except Exception:
                pass
            self._wv_host = None
        if self._wv_loading is not None:
            try:
                self._wv_loading.destroy()
            except Exception:
                pass
            self._wv_loading = None
        if self._web_server is not None:
            try:
                self._web_server.stop()
            except Exception:
                pass
            self._web_server = None
        if not self._tk_built:
            self._build()
        self.refresh()

    def _on_page_destroy(self, event=None):
        if event is not None and event.widget is not self:
            return
        self._destroyed = True
        self._visible = False
        if self._auto_job:
            try:
                self.after_cancel(self._auto_job)
            except tk.TclError:
                pass
            self._auto_job = None
        if self._ui_poll_job:
            try:
                self.after_cancel(self._ui_poll_job)
            except tk.TclError:
                pass
            self._ui_poll_job = None
        if self._wv_host is not None:
            try:
                self._wv_host.close()
            except Exception:
                pass
        if self._web_server is not None:
            try:
                self._web_server.stop()
            except Exception:
                pass

    def _schedule_ui_poll(self):
        if self._destroyed or not self._use_webview:
            return
        self._drain_ui_queue()
        try:
            self._ui_poll_job = self.after(30, self._schedule_ui_poll)
        except tk.TclError:
            self._ui_poll_job = None

    def _drain_ui_queue(self):
        while not self._destroyed:
            try:
                fn, done, box = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                box["val"] = fn()
            except Exception as exc:
                box["err"] = exc
            finally:
                done.set()

    def _call_on_ui(self, fn):
        """将 HTTP 工作线程的请求安全切回 Tk 主线程执行。"""
        if self._destroyed:
            return {"ok": False, "error": "closed"}
        if threading.current_thread() is threading.main_thread():
            return fn()
        done = threading.Event()
        box: dict = {}
        self._ui_queue.put((fn, done, box))
        try:
            self.after(0, self._drain_ui_queue)
        except (tk.TclError, RuntimeError):
            pass
        if not done.wait(timeout=5.0):
            return {"ok": False, "error": "timeout"}
        if "err" in box:
            raise box["err"]
        return box.get("val")

    def _build_web_state(self) -> dict:
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

        occ_ids = self.store.occupants_in(rid)
        occupants_pairs = [(aid, names.get(aid, aid)) for aid in occ_ids]
        map_text, hotspots = render_room_map(room, occupants_pairs)
        self._hotspots = hotspots
        self.store.name_map = names

        sched = self._schedule_status_map()
        occ_list = []
        for aid in occ_ids:
            name = names.get(aid, aid)
            is_me = (aid == "user")
            pos_tag = self.store.position_of(aid)
            act_tag = "" if is_me else self.store.activity_label(aid, schedule_kind=sched.get(aid, ""))
            extras = [x for x in (pos_tag, act_tag, self._hands_tag(aid)) if x and x != "房间中央"]
            suffix = f" ({' · '.join(extras)})" if extras else ""
            occ_list.append({
                "id": aid,
                "name": name,
                "is_me": is_me,
                "hands": self.store.hands_brief(aid),
                "chip_text": f"@{name}" + (" [我]" if is_me else "") + suffix,
            })

        rooms = self.store.room_list()
        self._room_ids = [r["id"] for r in rooms]
        here_neighbors = set(self.store.neighbors(rid))
        rooms_ui = []
        rooms_overview = []
        shareable_rooms = []
        for r in rooms:
            cnt = len(self.store.occupants_in(r["id"]))
            badge = self._room_badge(r)
            rooms_ui.append({
                "id": r["id"],
                "name": r["name"],
                "label": f"{badge} {r['name']} ({cnt}人)",
                "linked": r["id"] in here_neighbors or r["id"] == rid,
                "deletable": (not r.get("builtin")) and r["id"] != "lounge",
            })
            occ_txt = "、".join(names.get(a, a) for a in self.store.occupants_in(r["id"])) or tr("空")
            if r.get("is_public"):
                key_txt = tr("公共空间（无需钥匙）")
            else:
                key_txt = tr("钥匙持有：{k}", k="、".join(names.get(k, k) for k in (r.get("keys") or [])))
                if self.store.has_key("user", r["id"]):
                    shareable_rooms.append({"id": r["id"], "name": r["name"]})
            lock_txt = tr(" [已内部反锁]") if r.get("locked_inside") else ""
            door_txt = self.store.rooms_brief(r["id"])
            rooms_overview.append(f"{badge} {r['name']}{lock_txt} — {key_txt} | 门通向：{door_txt} | 在场：{occ_txt}")

        personas_ui = []
        if self.comm:
            for p in room_personas(self.comm.personas, self.comm.cfg.get("mute_pro")):
                personas_ui.append({"id": p, "name": names.get(p, p)})

        npc_on = bool(self.comm.cfg.get("room_npc_chat", False)) if self.comm else False
        interval = int(self.comm.cfg.get("room_npc_interval") or 30) if self.comm else 30
        prob = int(self.comm.cfg.get("room_npc_prob") or 30) if self.comm else 30
        idle_m = int(self.comm.cfg.get("room_npc_idle_min") or 15) if self.comm else 15
        auto_status_text = (
            tr("● 自动行动已开 ({s}s/{p}% · 注意力半衰期 {m}m)", s=interval, p=prob, m=idle_m)
            if npc_on else tr("○ 自动行动未开 (点此设置)")
        )

        vol = self._var_volume.get() or "normal"
        if vol == "whisper":
            pos = self.store.position_of("user")
            listeners = [names.get(a, a) for a in self.store.listeners_for_volume(rid, "user", "whisper") if a != "user"]
            vol_hint = (
                tr("仅「{pos}」可听：{who}", pos=pos, who="、".join(listeners))
                if listeners else tr("仅「{pos}」可听（附近暂无其他人）", pos=pos)
            )
        elif vol == "shout":
            nbs = "、".join((self.store.rooms.get(n) or {}).get("name", n) for n in self.store.neighbors(rid))
            vol_hint = (tr("隔壁也能听见：{who}（会吵到正在看书 / 弹琴的人）", who=nbs) if nbs
                        else tr("大声呼喊会吵到正在看书 / 弹琴的人"))
        else:
            vol_hint = tr("房间内所有人都能听见")

        if self._generating_map:
            busy_text = tr("正在绘制字符画地图…")
        elif self._busy_actors:
            who = "、".join(names.get(a, a) for a in sorted(self._busy_actors))
            busy_text = tr("{who} 正在行动…", who=who)
        else:
            busy_text = ""

        return {
            "colors": {
                "bg": COLORS.get("bg", "#0f131b"),
                "bg_card": COLORS.get("bg_card", "#171c28"),
                "border": COLORS.get("border", "#2a3346"),
                "text": COLORS.get("text", "#e6edf7"),
                "text_dim": COLORS.get("text_dim", "#8b97ad"),
                "accent": COLORS.get("accent", "#67d5f7"),
                "accent_hover": COLORS.get("accent_hover", "#8be0fa"),
            },
            "room": {
                "id": rid,
                "name": room["name"],
                "theme": room.get("theme") or "",
                "ambience": room.get("ambience") or "",
                "locked_inside": bool(room.get("locked_inside")),
            },
            "lock_tag": lock_tag,
            "rooms": rooms_ui,
            "shareable_rooms": shareable_rooms,
            "rooms_overview": rooms_overview,
            "personas": personas_ui,
            "map_text": map_text,
            "hotspots": hotspots,
            "occupants": occ_list,
            "hands": {
                side: {"state": h["state"], "note": h.get("note") or "",
                       "label": self.store.hand_label("user", side)}
                for side, h in self.store.hands_of("user").items()
            },
            "pending_actions": self.store.pending_of("user"),
            "logs": self.store.user_display_logs(rid, names),
            "clear_logs_label": tr("清空记录"),
            "clear_logs_confirm": tr("清空所有小屋聊天记录？房间与当前状态会保留。"),
            "clear_logs_done": tr("小屋聊天记录已清空。"),
            "volume": vol,
            "vol_hint": vol_hint,
            "npc_on": npc_on,
            "auto_status_text": auto_status_text,
            "busy_text": busy_text,
            "generating_map": self._generating_map,
            "links": [{"id": n, "name": (self.store.rooms.get(n) or {}).get("name", n),
                       "locked": bool(self.store.door_between(rid, n).get("locked"))}
                      for n in self.store.neighbors(rid)],
        }

    def _handle_web_action(self, body: dict) -> dict:
        atype = str(body.get("type") or "")
        if atype == "back":
            self.after(0, self._go_back)
            return {"ok": True}
        if atype == "open_settings":
            self.after(0, self._open_comm_settings)
            return {"ok": True}
        if atype == "open_page":
            page = str(body.get("page") or "")
            if page and callable(self.on_open_page):
                self.after(0, lambda p=page: self.on_open_page(p))
            return {"ok": True}
        if atype == "open_music":
            if callable(self.on_music):
                self.after(0, self.on_music)
                return {"ok": True}
            return {"ok": True, "toast": tr("请从沟通页打开音乐演奏功能。")}
        if atype == "open_coread":
            if callable(self.on_coread):
                self.after(0, self.on_coread)
                return {"ok": True}
            return {"ok": True, "toast": tr("共读入口暂不可用。")}
        if atype == "open_joint":
            if callable(self.on_joint):
                self.after(0, self.on_joint)
                return {"ok": True}
            return {"ok": True, "toast": tr("联合行动入口暂不可用。")}
        if atype == "switch_room":
            rid = str(body.get("room_id") or "")
            err = self.store.walk_to("user", rid, self._actor_name("user"), name_map=self._persona_names())
            st = self._build_web_state()
            if err:
                return {"ok": False, "toast": err, "warn": True, "state": st}
            return {"ok": True, "state": st}
        if atype == "delete_room":
            rid = str(body.get("room_id") or "")
            done = self.store.delete_room(rid, "user")
            st = self._build_web_state()
            if not done:
                return {"ok": False, "toast": tr("内置房间或公共客厅不能删除。"), "warn": True, "state": st}
            return {"ok": True, "toast": tr("房间已删除，里面的人回到了公共客厅。"), "state": st}
        if atype == "clear_room_logs":
            self._room_log_generation += 1
            self._busy_actors.clear()
            self._actor_request_generation.clear()
            self.store.clear_logs()
            self.refresh()
            return {"ok": True, "toast": tr("小屋聊天记录已清空。"), "state": self._build_web_state()}
        if atype == "toggle_lock":
            room = self.store.current_room()
            err = self.store.set_room_lock(room["id"], "user", not room.get("locked_inside"), self._actor_name("user"))
            st = self._build_web_state()
            if err:
                return {"ok": False, "toast": tr(err), "warn": True, "state": st}
            return {"ok": True, "state": st}
        if atype == "move_position":
            label = str(body.get("label") or "")
            if self.store.move_position("user", label, self._actor_name("user")):
                self._notify_room_action_change()
                return {
                    "ok": True,
                    "toast": tr("你移动到了「{pos}」，现在点击人物可触发相关位置互动。", pos=label),
                    "state": self._build_web_state(),
                }
            return {"ok": True, "state": self._build_web_state()}
        if atype == "move_near":
            target_pos = str(body.get("target_pos") or "")
            if target_pos:
                self.store.move_position("user", target_pos, self._actor_name("user"))
                self._notify_room_action_change()
            return {"ok": True, "state": self._build_web_state()}
        if atype == "release_hand":
            side = str(body.get("side") or "")
            if side in ("left", "right") and self.store.hand_state("user", side) in ("holding", "held"):
                self.store.release_hand("user", side, self._persona_names(), self._actor_name("user"))
                self._notify_room_action_change()
            return {"ok": True, "state": self._build_web_state()}
        if atype == "actor_interact_info":
            actor_id = str(body.get("actor_id") or "")
            msg_count = self.pair_msgs("user", actor_id)
            user_pos = self.store.position_of("user")
            target_pos = self.store.position_of(actor_id)
            pos_opts = position_interactions_for(user_pos, target_pos)
            holding_side = next((s for s in ("left", "right")
                                 if self.store.holding_of("user", s) == actor_id), "")
            parts = []
            for _pid, plabel, req in BODY_PARTS:
                ok, _ = part_allowed(plabel, msg_count)
                parts.append({"label": plabel, "req": req, "allowed": ok})
            pos_parts = []
            for _pid, plabel, req in pos_opts:
                ok, _ = part_allowed(plabel, msg_count)
                pos_parts.append({"label": plabel, "req": req, "allowed": ok})
            return {
                "ok": True,
                "info": {
                    "msg_count": msg_count,
                    "user_pos": user_pos,
                    "target_pos": target_pos,
                    "holding_side": holding_side,
                    "available_hands": self.store.available_hands("user"),
                    "hands": {
                        side: {"state": h["state"]}
                        for side, h in self.store.hands_of("user").items()
                    },
                    "target_hands": self.store.hands_brief(actor_id),
                    "parts": parts,
                    "pos_parts": pos_parts,
                    "custom_ok": msg_count >= CUSTOM_PART_MIN_MSGS,
                    "custom_min": CUSTOM_PART_MIN_MSGS,
                    "hold_ok": msg_count >= HOLD_HAND_MIN_MSGS,
                    "hold_min": HOLD_HAND_MIN_MSGS,
                },
            }
        if atype == "queue_actions":
            items = body.get("actions") or []
            if not isinstance(items, list) or not items:
                return {"ok": False, "toast": tr("请至少勾选一项互动。"), "warn": True}
            for item in items:
                if not isinstance(item, dict):
                    continue
                hand = str(item.get("hand") or "")
                part = str(item.get("part") or "")
                target_id = str(item.get("target_id") or "")
                target_name = str(item.get("target_name") or "")
                text = str(item.get("text") or "").strip()
                secret = bool(item.get("secret", False))
                if item.get("is_facility") or part == "设施":
                    err = self.store.queue_hand_action("user", hand, target_id, target_name, "设施", text)
                else:
                    msg_count = self.pair_msgs("user", target_id)
                    err = self.store.queue_hand_action(
                        "user", hand, target_id, target_name, part, text, msg_count=msg_count, secret=secret
                    )
                if err:
                    return {"ok": False, "toast": tr(err), "warn": True, "state": self._build_web_state()}
            return {"ok": True, "state": self._build_web_state()}
        if atype == "queue_action":
            hand = str(body.get("hand") or "")
            part = str(body.get("part") or "")
            if not hand and part not in ("gaze", "目光注视"):
                return {"ok": False, "toast": tr("请先选择左手或右手。"), "warn": True}
            target_id = str(body.get("target_id") or "")
            target_name = str(body.get("target_name") or "")
            text = str(body.get("text") or "").strip()
            secret = bool(body.get("secret", False))
            if body.get("is_facility"):
                err = self.store.queue_hand_action("user", hand, target_id, target_name, "设施", text)
            else:
                msg_count = self.pair_msgs("user", target_id)
                err = self.store.queue_hand_action(
                    "user", hand, target_id, target_name, part, text, msg_count=msg_count, secret=secret
                )
            if err:
                return {"ok": False, "toast": tr(err), "warn": True}
            return {"ok": True, "state": self._build_web_state()}
        if atype == "remove_pending":
            idx = int(body.get("index", -1))
            self.store.remove_pending_action("user", idx)
            return {"ok": True, "state": self._build_web_state()}
        if atype == "set_volume":
            vol = str(body.get("volume") or "normal")
            if vol in VOLUMES:
                self._var_volume.set(vol)
            return {"ok": True, "state": self._build_web_state()}
        if atype == "send":
            text = str(body.get("text") or "").strip()
            self._send_text(text)
            return {"ok": True, "state": self._build_web_state()}
        if atype == "grant_key":
            rid = str(body.get("room_id") or "")
            to_id = str(body.get("to_id") or "")
            names = self._persona_names()
            err = self.store.grant_key(rid, "user", to_id, self._actor_name("user"), names.get(to_id, to_id))
            st = self._build_web_state()
            if err:
                return {"ok": False, "toast": tr(err), "warn": True, "state": st}
            rname = (self.store.rooms.get(rid) or {}).get("name", rid)
            return {
                "ok": True,
                "toast": tr("已把「{r}」的钥匙分给 {p}", r=rname, p=names.get(to_id, to_id)),
                "state": st,
            }
        if atype == "create_room":
            theme = str(body.get("theme") or "").strip()
            desc = str(body.get("desc") or "").strip()
            is_pub = bool(body.get("is_public", False))
            if not theme:
                return {"ok": False, "toast": tr("请先输入房间主题"), "warn": True}
            self._generate_room_async(theme, desc, is_pub)
            return {"ok": True, "state": self._build_web_state()}
        if atype == "renovate_room":
            desc = str(body.get("desc") or "").strip()
            if not desc:
                return {"ok": False, "toast": tr("请先输入装修要求"), "warn": True}
            self._renovate_room_async(self.store.current_room_id(), desc)
            return {"ok": True, "state": self._build_web_state()}
        return {"ok": False, "error": f"unknown_action:{atype}"}

    # ══════════════ Tk 降级界面构建（无 WebView2 时使用） ══════════════

    def _build(self):
        self._tk_built = True
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
        ttk.Button(hdr, text=tr("🛠 装修"), command=self._open_renovate_room_dialog).pack(side="right", padx=(6, 0))
        self.var_room = tk.StringVar()
        self.cb_room = ttk.Combobox(hdr, textvariable=self.var_room, state="readonly", width=24)
        self.cb_room.pack(side="right", padx=(6, 0))
        self.cb_room.bind("<<ComboboxSelected>>", lambda _e: self._pick_room())


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
        ttk.Button(log_head, text=tr("🗑 清空记录"), command=self._clear_room_logs).pack(
            side="right", padx=(8, 0))

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
        ttk.Radiobutton(vol_bar, text=tr("大声呼喊"), value="shout", variable=self._var_volume,
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

    def _start_webview_after_show(self):
        self._wv_show_job = None
        if self._destroyed or not self._visible or not self._use_webview:
            return
        if self._wv_host is None or self._web_server is None:
            return
        url = self._web_server.start()
        self._wv_host.start(url)
        self._wv_host.set_visible(True)
        self.after(120, self._sync_webview_visible)
        if self._web_server is not None:
            self._web_server.notify_update("show")

    def show(self):
        self._visible = True
        if self.comm is not None:
            self.comm.room_viewing = True
        self._sync_persona_rooms()
        if self._use_webview and self._wv_host is not None and self._web_server is not None:
            if self._wv_show_job is not None:
                try:
                    self.after_cancel(self._wv_show_job)
                except tk.TclError:
                    pass
            # 用短延时彻底避开 frozen() 内的 settle()/update_idletasks() 冻结期
            self._wv_show_job = self.after(60, self._start_webview_after_show)
            self.refresh()
            self._schedule_auto_tick()
            return
        self.refresh()
        self._schedule_auto_tick()
        if self._tk_built:
            self.entry.focus_set()

    def hide(self):
        self._visible = False
        if self._wv_show_job is not None:
            try:
                self.after_cancel(self._wv_show_job)
            except tk.TclError:
                pass
            self._wv_show_job = None
        if self.comm is not None:
            self.comm.room_viewing = False
        if self._use_webview and self._wv_host is not None:
            self._wv_host.set_visible(False)
        if self._auto_job:
            try:
                self.after_cancel(self._auto_job)
            except tk.TclError:
                pass
            self._auto_job = None

    def _go_back(self):
        if callable(self.on_back):
            self.on_back()

    def _clear_room_logs(self):
        if not messagebox.askyesno(tr("清空记录"), tr("清空所有小屋聊天记录？房间与当前状态会保留。"),
                                   parent=self.winfo_toplevel()):
            return
        self._handle_web_action({"type": "clear_room_logs"})
        toast.info(tr("小屋聊天记录已清空。"))

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
        if self._destroyed:
            return
        self._sync_persona_rooms()
        room = self.store.current_room()
        rid = room["id"]
        names = self._persona_names()

        if self._use_webview:
            occ_ids = self.store.occupants_in(rid)
            occupants = [(aid, names.get(aid, aid)) for aid in occ_ids]
            _, hotspots = render_room_map(room, occupants)
            self._hotspots = hotspots
            self._room_ids = [r["id"] for r in self.store.room_list()]
            if self._web_server is not None:
                self._web_server.notify_update("refresh")
            return

        if not self._tk_built:
            return

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
            extras = [x for x in (pos_tag, act_tag, self._hands_tag(aid)) if x and x != "房间中央"]
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
        elif vol == "shout":
            nbs = "、".join((self.store.rooms.get(n) or {}).get("name", n) for n in self.store.neighbors(rid))
            hint = (tr("隔壁也能听见：{who}（会吵到正在看书 / 弹琴的人）", who=nbs) if nbs
                    else tr("大声呼喊会吵到正在看书 / 弹琴的人"))
        else:
            hint = tr("房间内所有人都能听见")
        cfg(self.lbl_vol_hint, text=hint)

    def _render_hands(self):
        for side, lbl in (("left", self.lbl_left_hand), ("right", self.lbl_right_hand)):
            st = self.store.hand_state("user", side)
            fg = "#ef4444" if st in ("bound", "held") else (COLORS["accent"] if st in ("used", "holding") else COLORS["text"])
            cfg(lbl, text=f"[{self.store.hand_label('user', side)}]", fg=fg)

        for child in self.pending_frame.winfo_children():
            child.destroy()
        for idx, act in enumerate(self.store.pending_of("user")):
            row = tk.Frame(self.pending_frame, bg=COLORS["bg"], highlightthickness=1,
                           highlightbackground=COLORS["border"])
            row.pack(fill="x", pady=1)
            hand_txt = ACTION_CHANNEL_LABELS.get(act["hand"], "手部")
            sec_txt = "·隐秘" if act.get("secret") else ""
            txt = f"[{hand_txt}{sec_txt} → {act['target_name']}·{act['part']}] {act['text']}"
            tk.Label(row, text=txt, bg=COLORS["bg"], fg=COLORS["accent"],
                     font=config.font("small"), anchor="w").pack(side="left", fill="x", expand=True, padx=6, pady=2)
            xbtn = tk.Label(row, text="✕", bg=COLORS["bg"], fg=COLORS["text_dim"],
                            font=config.font("small"), cursor="hand2", padx=6)
            xbtn.pack(side="right")
            xbtn.bind("<Button-1>", lambda _e, i=idx: self._remove_pending(i))

    def _remove_pending(self, idx: int):
        self.store.remove_pending_action("user", idx)
        self._render_hands()

    def _toggle_hand_bind(self, side: str):
        st = self.store.hand_state("user", side)
        if st in ("holding", "held"):
            self.store.release_hand("user", side, self._persona_names(), self._actor_name("user"))
            self.refresh()
            self._notify_room_action_change()
            return
        cur_bound = (st == "bound")
        self.store.set_hand_bound("user", side, not cur_bound, note="束缚" if not cur_bound else "")
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
                elif e.get("volume") == "shout":
                    header = f"{e['sender_name']}（大声呼喊）：\n"
                else:
                    header = f"{e['sender_name']}：\n"
                if e.get("from_room"):
                    header = f"[从「{e['from_room']}」隔墙传来] " + header
                w.insert("end", header, tag)
                for a in e.get("actions") or []:
                    h = ACTION_CHANNEL_LABELS.get(a.get("hand"), "手部")
                    sec = "·隐秘" if a.get("secret") else ""
                    w.insert("end", f"  [{h}{sec}→{a.get('target_name')}·{a.get('part')}] {a.get('text')}\n", "act")
                if e.get("text"):
                    w.insert("end", f"  {e['text']}\n", "speech")
                w.insert("end", "\n")
        w.see("end")
        w.configure(state="disabled")

    def _update_busy_label(self, names: dict[str, str]):
        if self._use_webview:
            if self._web_server is not None:
                self._web_server.notify_update("busy")
            return
        if not self._tk_built:
            return
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
        elif action == "timer":
            if callable(self.on_open_page): self.on_open_page("timer")
        elif action == "contract":
            if callable(self.on_open_page): self.on_open_page("contract")
        elif action == "knowledge":
            if callable(self.on_open_page): self.on_open_page("knowledge")
        elif action == "music":
            if callable(self.on_music): self.on_music()
        elif action == "coread":
            if callable(self.on_coread): self.on_coread()
        elif action == "joint":
            if callable(self.on_joint): self.on_joint()
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
        err = self.store.walk_to("user", room_id, self._actor_name("user"), name_map=self._persona_names())
        if err:
            toast.warn(err)
            self.refresh()
            return
        self.refresh()

    # ══════════════ 部位互动弹窗（按条数解锁） ══════════════

    def _open_actor_interact(self, actor_id: str, actor_name: str):
        avail = self.store.available_hands("user")
        msg_count = self.pair_msgs("user", actor_id)
        user_pos = self.store.position_of("user")
        target_pos = self.store.position_of(actor_id)
        pos_opts = position_interactions_for(user_pos, target_pos)
        holding_side = next((s for s in ("left", "right") if self.store.holding_of("user", s) == actor_id), "")
        bg = COLORS["bg"]
        dlg = Modal(self.winfo_toplevel(), tr("与 {name} 互动", name=actor_name),
                    width=config.scaled(540), height=config.scaled(540 if pos_opts else 490))

        top_info = tk.Frame(dlg.body, bg=bg)
        top_info.pack(fill="x", pady=(0, 6))
        tk.Label(top_info, text=tr("累计消息：{n} 条 | 位置：你在「{up}」· 对方在「{tp}」",
                                   n=msg_count, up=user_pos, tp=target_pos),
                 bg=bg, fg=COLORS["accent"], font=config.font("small"), anchor="w").pack(side="left", fill="x", expand=True)
        tk.Label(top_info, text=f"（对方双手：{self.store.hands_brief(actor_id)}）",
                 bg=bg, fg=COLORS["text_dim"], font=config.font("small"), anchor="w").pack(side="left", padx=(6, 0))

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
            h = self.store.hands_of("user")[side]
            suffix = ""
            if h["state"] == "held":
                suffix = " (被牵着)"
            elif h["state"] == "holding":
                suffix = " (牵手中)"
            elif h["state"] == "bound":
                suffix = " (被束缚)"
            elif h["state"] == "used":
                suffix = " (刚用过)"
            rb = ttk.Radiobutton(hand_row, text=tr(label) + suffix, value=side, variable=var_hand)
            if side not in avail:
                rb.state(["disabled"])
            rb.pack(side="left", padx=(8, 0))

        if not avail:
            tk.Label(dlg.body, text=tr("当前没有空闲的手（被牵着或被束缚），无法添加新的手部互动。"),
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
            err = self.store.queue_hand_action("user", hand, actor_id, actor_name, part, action_text,
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
        avail = self.store.available_hands("user")
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
            rb = ttk.Radiobutton(hrow, text=tr(label), value=side, variable=var_hand)
            if side not in avail:
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
            err = self.store.queue_hand_action("user", hand, hs["id"], hs["label"], "设施", txt.get("1.0", "end").strip())
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
        personas = [p for p in (room_personas(self.comm.personas, self.comm.cfg.get("mute_pro")) if self.comm else [])]
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
            row = tk.Frame(dlg.body, bg=bg)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=f"{self._room_badge(r)} {r['name']}{lock_txt} — {key_txt} | 门通向："
                               f"{self.store.rooms_brief(r['id'])} | 在场：{occ}",
                     bg=bg, fg=COLORS["text_dim"], font=config.font("small"), anchor="w", wraplength=420,
                     justify="left").pack(side="left", fill="x", expand=True)
            if not r.get("builtin") and r["id"] != "lounge":
                ttk.Button(row, text=tr("删除"), width=6,
                           command=lambda rid=r["id"], nm=r["name"]: self._confirm_delete_room(rid, nm, dlg)).pack(
                    side="right", padx=(6, 0))

        ttk.Button(dlg.buttons, text=tr("关闭"), command=dlg.cancel).pack(side="right")
        dlg.wait()

    def _confirm_delete_room(self, room_id: str, room_name: str, dlg=None):
        """删除自建房间：门一起断掉，里面的人回到公共客厅。"""
        ok = self.store.delete_room(room_id, "user")
        if not ok:
            toast.warn(tr("内置房间或公共客厅不能删除。"))
            return
        toast.info(tr("已删除「{name}」，里面的人回到了公共客厅。", name=room_name))
        if dlg is not None:
            dlg.finish(True)
        self.refresh()

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
            profile = self.comm.main_profile() if hasattr(self.comm, "main_profile") else self.comm.profile("")
            ai_assist.check_profile(profile)
        except Exception as exc:
            toast.warn(tr("AI 模型未配置：{err}", err=exc))
            return

        self._generating_map = True
        self._update_busy_label(self._persona_names())
        topology = self.store.map_description()
        system, msgs = build_map_generation_prompt(theme, desc, old_map=self.store.map_snapshot(), topology=topology)

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
        toast.info(tr("已创建并进入新房间「{name}」（门开在原来的房间）", name=saved["name"]))
        self.refresh()

    def _open_renovate_room_dialog(self):
        room = self.store.current_room()
        dlg = Modal(self.winfo_toplevel(), tr("装修当前房间"), width=config.scaled(480), height=config.scaled(300))
        tk.Label(dlg.body, text=tr("AI 会参考旧地图与世界房间连接，按你的要求修改地形或接入设施。"),
                 bg=COLORS["bg"], fg=COLORS["text_dim"], font=config.font("small"), anchor="w",
                 wraplength=430).pack(fill="x", pady=(0, 8))
        tk.Label(dlg.body, text=tr("装修要求："), bg=COLORS["bg"], fg=COLORS["text"],
                 font=config.font("body"), anchor="w").pack(fill="x")
        txt = tk.Text(dlg.body, height=6, wrap="word", font=config.font("body"), bg=COLORS["bg_card"],
                      fg=COLORS["text"], insertbackground=COLORS["text"], relief="flat",
                      highlightthickness=1, highlightbackground=COLORS["border"])
        txt.pack(fill="both", expand=True, pady=(2, 6))
        def submit():
            desc = txt.get("1.0", "end").strip()
            if not desc:
                toast.warn(tr("请先输入装修要求")); return
            dlg.finish(True)
            self._renovate_room_async(room["id"], desc)
        ttk.Button(dlg.buttons, text=tr("由 AI 装修"), style="Accent.TButton", command=submit).pack(side="right")
        ttk.Button(dlg.buttons, text=tr("取消"), command=dlg.cancel).pack(side="right", padx=(0, 8))
        dlg.wait()

    def _renovate_room_async(self, room_id: str, desc: str):
        if self._generating_map or not self.comm:
            return
        try:
            profile = self.comm.main_profile() if hasattr(self.comm, "main_profile") else self.comm.profile("")
            ai_assist.check_profile(profile)
        except Exception as exc:
            toast.warn(tr("AI 模型未配置：{err}", err=exc)); return
        old = self.store.rooms.get(room_id)
        if not old:
            toast.warn(tr("当前房间不存在。")); return
        self._generating_map = True; self._update_busy_label(self._persona_names())
        old_text = self.store.map_snapshot()
        system, msgs = build_map_generation_prompt(old.get("theme") or old.get("name") or "装修房间", desc,
                                                   old_text, self.store.map_description())
        def work():
            try:
                fn = self.comm.remote_chat if profile.get("kind") == "device" and callable(self.comm.remote_chat) else self.comm.chat_fn
                reply = fn(profile, system, msgs, max_tokens=1600, temperature=0.8, timeout=90.0)
                room = parse_generated_room(reply, fallback_theme=old.get("theme") or "自定义场景", room_id=room_id)
                self.after(0, lambda: self._finish_renovate_room(room, room_id, ""))
            except Exception as exc:
                self.after(0, lambda e=exc: self._finish_renovate_room(None, room_id, str(e)))
        threading.Thread(target=work, daemon=True, name="oit-room-renovate").start()

    def _finish_renovate_room(self, room: dict | None, room_id: str, err: str):
        self._generating_map = False
        if err or not room:
            toast.warn(tr("装修房间失败：{err}", err=err or "返回格式无效"))
        else:
            saved = self.store.renovate_room(room_id, room)
            if saved:
                toast.info(tr("已完成房间装修：{name}", name=saved["name"]))
        self._update_busy_label(self._persona_names()); self.refresh()

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
        if not text and not self.store.pending_of("user"):
            return
        self.entry.delete("1.0", "end")
        self._send_text(text)

    def _send_text(self, text: str):
        room_id = self.store.current_room_id()
        volume = self._var_volume.get() or "normal"
        targets = self.store.listeners_for_volume(room_id, "user", volume)
        actions = self.store.take_pending("user", self._actor_name("user"))
        if not text and not actions:
            return

        self.store.append_log(room_id, "user", self._actor_name("user"), targets,
                              "action" if actions and not text else "say", text,
                              actions=actions, volume=volume, pos=self.store.position_of("user"))
        self.store.reset_turn_hands("user", save=True)
        apply_room_vt(self.comm, "user", actions)
        self.refresh()

        occ_npcs = [aid for aid in self.store.occupants_in(room_id) if aid != "user"]
        names = self._persona_names()
        sched = self._schedule_status_map()
        blocked = {aid for aid, k in sched.items() if k in ("focus", "sleep")}
        room_name = (self.store.rooms.get(room_id) or {}).get("name", "")

        # 点名：被叫到名字的人无论在哪、是否还在「对话窗口」里都要听见；专注 / 睡眠时先记下来，之后补上
        named_here, named_far = [], []
        if text:
            for aid in summon_targets(self.store, text, names):
                if aid in blocked:
                    self.store.add_heard(aid, text, room_name, names.get("user", "你"))
                    continue
                if aid in occ_npcs:
                    self.store.raise_attention(aid, ATTENTION_SUMMON)
                    named_here.append(aid)
                else:
                    named_far.append(aid)
        if not occ_npcs and not named_far:
            return
        has_public_action = any(not a.get("secret") for a in actions)
        acted_targets = list(dict.fromkeys(a["target_id"] for a in actions if a.get("target_id") in occ_npcs))

        # 直接听见说话或被接触的角色为主回应者；同房里看见公开动作的旁观者以 observe 触发（可 silent）
        direct = []
        if text:
            direct = list(occ_npcs) if "all" in targets else [a for a in targets if a in occ_npcs]
        for aid in acted_targets + named_here:
            if aid not in direct:
                direct.append(aid)
        observers = [aid for aid in occ_npcs if aid not in direct] if has_public_action else []

        for aid in direct[:2]:
            self._trigger_actor_reply(room_id, aid, trigger_reason="user")
        for aid in observers[:2]:
            self._trigger_actor_reply(room_id, aid, trigger_reason="observe")
        # 隔着房间也叫得动：过去看看，或干脆不过来
        for aid in named_far[:2]:
            self._trigger_actor_reply(self.store.occupants.get(aid) or room_id, aid,
                                      trigger_reason="summon", heard=text)

    def _trigger_actor_reply(self, room_id: str, actor_id: str, trigger_reason: str = "user",
                             heard: str = ""):
        if not self.comm or actor_id in self._busy_actors:
            return
        try:
            profile = self.comm.profile()
            ai_assist.check_profile(profile)
        except Exception:
            if trigger_reason == "user":
                toast.warn(tr("请先在 设置 → 模型 中配置 AI 接口。"))
            return

        room_id = self.store.occupants.get(actor_id) or room_id
        room = self.store.rooms.get(room_id)
        if not room:
            return
        names = self._persona_names()
        occ_ids = self.store.occupants_in(room_id)
        visible = self.store.visible_logs_for(actor_id, room_id)
        psys = persona_system(self.comm.personas, actor_id, False)
        mem_block, phone_hist = (
            self.comm.room_persona_context(actor_id)
            if (self.comm and hasattr(self.comm, "room_persona_context"))
            else ("", "")
        )
        note = ""
        if trigger_reason == "summon":
            who = names.get("user", "你")
            here = (self.store.rooms.get(self.store.current_room_id()) or {}).get("name", "")
            note = (f"【有人在叫你】{who} 在「{here}」叫了你的名字，原话：「{heard}」。"
                    f"你现在在「{room.get('name', '')}」。")
        missed = self.store.take_heard(actor_id)
        if missed:
            lines = "\n".join(
                f"- 在「{m.get('room') or '某处'}」{m.get('who') or '有人'}：{m.get('text') or ''}"
                for m in missed
            )
            note = (note + "\n" if note else "") + f"【你当时没答上的呼唤】\n{lines}\n（现在可以补上。）"
        aware = self.store.awareness_brief(actor_id)
        system, msgs = build_actor_turn_prompt(
            psys, self.store, room, actor_id, names.get(actor_id, actor_id),
            names, visible, trigger_reason=trigger_reason,
            memory_block=mem_block, phone_history=phone_hist, extra_note=note,
            awareness=aware,
        )

        self._busy_actors.add(actor_id)
        generation = self._room_log_generation
        self._actor_request_generation[actor_id] = generation
        self._update_busy_label(names)
        summon = (trigger_reason == "summon")

        def work():
            try:
                fn = self.comm.remote_chat if profile.get("kind") == "device" and callable(self.comm.remote_chat) else self.comm.chat_fn
                reply = fn(profile, system, msgs, max_tokens=1200, temperature=0.85, timeout=90.0)
                parsed = parse_actor_reply(reply, set(occ_ids), set(self.store.rooms.keys()), default_target="user")
                self.after(0, lambda: self._finish_actor_reply(
                    room_id, actor_id, parsed, "", summon=summon, generation=generation))
            except Exception as exc:
                self.after(0, lambda e=exc: self._finish_actor_reply(
                    room_id, actor_id, None, str(e), summon=summon, generation=generation))

        threading.Thread(target=work, daemon=True, name=f"oit-room-{actor_id}").start()

    def _finish_actor_reply(self, room_id: str, actor_id: str, parsed: dict | None, err: str,
                            summon: bool = False, generation: int | None = None):
        if generation is not None and generation != self._room_log_generation:
            if self._actor_request_generation.get(actor_id) == generation:
                self._actor_request_generation.pop(actor_id, None)
                self._busy_actors.discard(actor_id)
            self.refresh()
            return
        self._actor_request_generation.pop(actor_id, None)
        self._busy_actors.discard(actor_id)
        if err or not parsed:
            self._update_busy_label(self._persona_names())
            return

        names = self._persona_names()
        actor_name = names.get(actor_id, actor_id)
        room_id = self.store.occupants.get(actor_id) or room_id
        self.store.update_impressions(actor_id, parsed.get("impressions"),
                                      set(self.store.occupants_in(room_id)))

        # 被隔房叫到名字：先走过去，之后说的话就落在走进去的那个房间里
        if summon:
            move_to = parsed.get("move_to")
            if move_to and move_to != room_id and move_to in self.store.rooms:
                bring = ["user"] if parsed.get("bring_user") else []
                self.store.enter_room(actor_id, move_to, actor_name, bring_ids=bring, name_map=names)
                room_id = self.store.occupants.get(actor_id) or room_id
                parsed["move_to"] = None

        # 1. 束缚/牵手：角色与用户是对等实体，动作按同一套规则结算
        bind = parsed.get("bind_hands")
        bind_target = str(parsed.get("bind_target") or "user")
        if bind and self.store.occupants.get(bind_target) == self.store.occupants.get(actor_id):
            if bind == "release":
                self.store.set_hand_bound(bind_target, "both", False)
            elif bind in ("left", "right", "both"):
                self.store.set_hand_bound(bind_target, bind, True, note=actor_name)
        hold = parsed.get("hold_hand")
        if hold == "release":
            for side in ("left", "right"):
                if self.store.hand_state(actor_id, side) in ("holding", "held"):
                    self.store.release_hand(actor_id, side, names, actor_name)
        elif hold in ("left", "right"):
            target_id = str(parsed.get("hold_target") or "user")
            err = self.store.hold_hand(actor_id, hold, target_id, actor_name,
                                       names.get(target_id, target_id),
                                       msg_count=self.pair_msgs(actor_id, target_id))
            if not err:
                apply_room_vt(self.comm, actor_id, [{"hand": hold, "target_id": target_id,
                                                     "part": "牵手带走", "secret": False}])

        # 2. 手部 / 目光动作：与用户在界面里排的动作完全同一套校验与结算
        for a in parsed.get("actions") or []:
            target_id = str(a.get("target_id") or "")
            self.store.queue_hand_action(actor_id, a.get("hand"), target_id,
                                         names.get(target_id, target_id), a.get("part"), a.get("text"),
                                         msg_count=self.pair_msgs(actor_id, target_id),
                                         secret=bool(a.get("secret")))
        acts = self.store.take_pending(actor_id, actor_name)

        # 3. 更新室内位置
        pos = parsed.get("position")
        if pos:
            self.store.move_position(actor_id, pos, actor_name)

        # 4. 更新跟随状态（言语请求跟随）
        fol = parsed.get("follow")
        if fol == "none":
            self.store.set_following(actor_id, None, actor_name)
        elif fol:
            self.store.set_following(actor_id, fol, actor_name, names.get(fol, fol))

        # 5. 分发钥匙
        gk = parsed.get("give_key")
        if gk:
            to_id = str(gk.get("to") or "")
            self.store.grant_key(str(gk.get("room_id") or ""), actor_id, to_id,
                                 actor_name, names.get(to_id, to_id))

        # 6. 发言（若 silent=True 且无动作则保持安静不刷屏；否则按音量 normal / whisper 自动决定听众）
        is_silent = bool(parsed.get("silent", False))
        speech = "" if is_silent else (parsed.get("speech") or "")
        action = parsed.get("action") or ""
        combined = f"（{action}）{speech}".strip() if action and speech else (f"（{action}）" if action else speech)
        if combined or acts:
            vol = parsed.get("volume") or "normal"
            listeners = self.store.listeners_for_volume(room_id, actor_id, vol)
            self.store.append_log(room_id, actor_id, actor_name, listeners,
                                  "say" if combined else "action", combined,
                                  actions=acts, volume=vol, pos=self.store.position_of(actor_id))
            apply_room_vt(self.comm, actor_id, acts)

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
        sched = self._schedule_status_map()
        blocked = {aid for aid, k in sched.items() if k in ("focus", "sleep")}
        rid = self.store.current_room_id()
        plan = auto_step_plan(self.store, rid, self._busy_actors, enabled, prob,
                             include_other_rooms=True, blocked_ids=blocked,
                             always_model=bool(self.comm.cfg.get("room_npc_always_api", True)))
        if plan:
            kind, aid, actor_room = plan
            if kind == "model":
                self.store.spend_auto_budget(aid)
                self._trigger_actor_reply(actor_room, aid, trigger_reason="auto")
            else:
                self.store.perform_idle_system_step(aid, self._actor_name(aid))
                self.refresh()
        self._schedule_auto_tick()
