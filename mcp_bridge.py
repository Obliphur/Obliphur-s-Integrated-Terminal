# -*- coding: utf-8 -*-
"""OIT 本地 Loopback HTTP / SSE Bridge，供 MCP Server、Antigravity Hooks 与 Sidecar 调用。

严格仅使用 Python 标准库；所有触碰 Comm / tkinter 状态的操作都通过 call_main 切回主线程执行。
"""

import base64
import http.server
import json
import mimetypes
import os
import queue
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from datetime import datetime, timedelta

import config
import music_library


BRIDGE_FILE = "mcp_bridge.json"
SESSIONS_FILE = "mcp_sessions.json"
BRIDGE_PROTOCOL = 1
BODY_MAX = 4 * 1024 * 1024
SSE_HEARTBEAT_S = 15.0

SUBAGENT_ROLES = ("subject", "mental_health", "music_review", "memory_compact")
_ROLE_TASK_KEY = {
    "subject": "subject",
    "mental_health": "mental_health",
    "music_review": "review",
    "memory_compact": "memory_compact",
}


def _display_persona_name(comm_obj) -> str:
    """返回不含 .txt 等扩展名的人设显示名。"""
    raw = comm_obj.persona_name() if hasattr(comm_obj, "persona_name") else ""
    if not raw:
        return ""
    try:
        from comm.comm_persona import persona_title
        return str(persona_title(raw) or raw)
    except Exception:
        return os.path.splitext(str(raw))[0]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _json_default(obj):
    if isinstance(obj, datetime):
        return obj.isoformat(timespec="seconds")
    return str(obj)


def _compact_schedule_line(comm_obj, now: datetime, status: dict) -> str:
    """将日程与循环状态压成单行，避免 state_text + _rt_note 重复冗长文本。"""
    if not status:
        return "日程: 空闲 (无固定结束时间)"
    from i18n import tr
    import routine
    from comm.comm_const import STATUS_TEXT

    kind = status.get("kind") or "idle"
    label = str(status.get("label") or "").strip()
    detail = str(status.get("detail") or "").strip()
    name = f"{label}({detail})" if detail else label
    base = tr(STATUS_TEXT.get(kind, "空闲")).rstrip("中")
    if status.get("joint"):
        nature = f"联合行动 · {base}"
    elif kind == "idle":
        nature = base if routine.seg_class(status) == "special" else (name or base)
    elif kind == "special":
        nature = base
    else:
        nature = f"{base}({name})" if name and name != base else base

    begin, end = status.get("begin"), status.get("end")
    if begin and end:
        left = max(0, int((end - now).total_seconds() // 60))
        out = f"日程: {nature} ({begin:%H:%M}-{end:%H:%M}, 剩{left}分)"
    elif begin:
        out = f"日程: {nature} (自{begin:%H:%M}, 结束时间未定)"
    else:
        out = f"日程: {nature} (结束时间未定)"

    if getattr(comm_obj, "_rt", None) and not status.get("joint") and hasattr(comm_obj, "_rt_segs"):
        try:
            segs = comm_obj._rt_segs(
                now - timedelta(hours=14), now + timedelta(hours=2),
                comm_obj._rt, comm_obj.pid, joint=True,
            )
            cur = routine.status_in(segs, now)
            if cur:
                if cur.get("loop") is not None:
                    same = [
                        x for x in segs
                        if x.get("loop") == cur["loop"] and abs((x["start"] - cur["start"]).total_seconds()) < 12 * 3600
                    ]
                    if same:
                        nr = max((x.get("fm") or 0) for x in same) or (cur.get("n") or 100)
                        out += f"; 循环{min(x['start'] for x in same):%H:%M}起每轮{nr:g}分"
                        fm = cur.get("fm") or 0
                        if fm and fm < nr - 0.5:
                            out += f"(本轮提前结束,{fm:g}分)"
                nxt = next((s for s in segs if s["start"] >= cur["end"] and s["kind"] in ("focus", "sleep")), None)
                if nxt and (nxt["start"] - now).total_seconds() <= 5 * 60:
                    nxt_name = tr(STATUS_TEXT[nxt["kind"]]).rstrip("中")
                    out += f"; {nxt_name}将于{nxt['start']:%H:%M}开始"
        except Exception:
            pass
    return out


def _compact_interaction_lines(comm_obj, pid: str, status: dict, judge_kind: str) -> list:
    """精简版状态行与按需情绪判定提示：无待评消息时不注入长篇模板说明，有待评消息时不重复输出规则全文。"""
    from comm.comm_interaction import interaction_valence_baseline, offenses_active, offenses_rate
    from comm.comm_persona import read_persona_full
    from comm.comm_rules import tension_rules, valence_rules, valence_burst_rules

    d = comm_obj._interaction_prepare(pid, status)
    special = comm_obj._interaction_special(status) if hasattr(comm_obj, "_interaction_special") else False
    eff_special = bool(special or d["burst_until"])
    total = comm_obj.msg_total() if hasattr(comm_obj, "msg_total") else 0
    mode = "情景模拟" if d["scenario_mode"] and total >= config.COMM_SCENARIO_UNLOCK else "软件聊天"
    effective = d["tension"] if eff_special and d["burst_until"] else min(d["tension"], config.COMM_TENSION_CAP)
    stage = "0-50" if effective < config.COMM_TENSION_LOW else "50-90" if effective <= config.COMM_TENSION_CAP else "90+"

    status_line = (
        f"[OIT_STATUS: MODE={mode}, TENSION={d['tension']}, EFFECTIVE_STAGE={stage}, "
        f"VALENCE={d['valence']}, INITIAL_VALENCE={interaction_valence_baseline(d)}, "
        f"OFFENSE_COUNT={len(offenses_active(d))}, VALENCE_RISE_RATE={offenses_rate(d):g}, "
        f"BURST={'YES' if d['burst_until'] else 'NO'}, PERIOD={'特殊空闲' if eff_special else '日常'}, "
        f"SHAME_AFTERMATH={'YES' if d['shame_turns'] else 'NO'}]"
    )
    lines = [status_line]
    if mode == "软件聊天" or not eff_special or stage == "0-50":
        lines.append("规则: 禁动作描写，勿透露机制。")
    else:
        lines.append("规则: 情景模拟虚构动作须守边界，括号仅写动作（想法直说），勿透露机制。")
    if d["burst_until"]:
        lines.append("爆发中: 决定结束时填 oit_reply.end_burst=true（后降为20并进2轮冷静期）。")
    if d["shame_turns"]:
        lines.append("冷静期中: 遵守动作限制。")

    candidates = [
        (m["uid"], d["pending"][m["uid"]], m["text"], m["uid"] in d["pending_sleep"])
        for m in comm_obj.messages
        if judge_kind in ("user", "retry") and m.get("role") == "me" and m.get("uid") in d["pending"]
    ]
    comm_obj._interaction_candidates = candidates
    if not candidates:
        return lines

    persona_text = read_persona_full(comm_obj.personas, comm_obj.persona_name())
    rules = tension_rules(persona_text)
    e_rules = valence_rules(persona_text)
    burst_rules = valence_burst_rules(persona_text)
    comm_obj._interaction_rules = rules
    comm_obj._interaction_valence_rules = e_rules
    comm_obj._interaction_burst_rules = burst_rules

    if rules or e_rules:
        lines.append(
            f"[OIT情绪判断] 按条件为待评消息填 oit_reply.tension=[{{index,condition,gain}}](0~{config.COMM_TENSION_GAIN_MAX})、"
            f"valence=[{{index,condition,shift}}](-40~40)及offenses，普通交流省略；报原始整数，系统结算倍率与冷却（X为不定量，不返回X）。"
        )
        if rules:
            lines.append("[tension] " + " | ".join(
                f"{k}:[{g if g is not None else 'X'}] {desc}" for k, (g, desc) in rules.items()
            ))
        if e_rules:
            lines.append("[valence] " + " | ".join(
                f"{k}:[{f'{g:+d}' if g is not None else 'X'}] {desc}" for k, (g, desc) in e_rules.items()
            ))
        items = [
            {"index": i, "text": text, **({"sleep": True} if sleep else {})}
            for i, (_uid, _sp, text, sleep) in enumerate(candidates, 1)
        ]
        lines.append("待评消息: " + json.dumps(items, ensure_ascii=False))
    return lines


def _bubble_delay_ms(comm_obj, text: str, index: int) -> int:
    """按 OIT 字数节奏计算气泡延时（毫秒）；首条立即可见，后续气泡带自然停顿。"""
    if index <= 0:
        return 0
    try:
        if hasattr(comm_obj, "_typing_delay"):
            sec = float(comm_obj._typing_delay(text))
        else:
            from comm import char_delay
            sec = float(char_delay(text))
    except Exception:
        sec = max(0.8, len(str(text or "")) * 0.12)
    return max(600, min(8000, round(sec * 1000)))


class OitMcpBridge:
    """OIT 进程内的本地受密钥保护 HTTP / SSE Bridge。"""

    def __init__(self, comm_obj, data_dir: str = "", call_main=None, open_native_dialog=None):
        self.comm = comm_obj
        self.data_dir = data_dir or getattr(comm_obj, "dir", "")
        self._call_main = call_main or (lambda fn: fn())
        self._open_native_dialog = open_native_dialog
        self.token = secrets.token_hex(32)
        self.port = 0
        self._server = None
        self._thread = None
        self._lock = threading.RLock()
        self._sse_clients = []          # list[queue.Queue]
        self._pending_triggers = {}     # key (conversation_id or persona_id) -> dict
        self._interrupted = {}          # persona_id -> {"count": int, "ts": int}
        self._uploads = {}              # upload_id -> {"path": str, "mime": str, "name": str}
        self._recent_user_ms = {}       # conversation_id -> int
        self._last_batch = {}           # persona_id -> {"batch_id": str, "items": list}
        self._last_reply_ms = {}        # conversation_id/persona_id -> int
        self._turn_interaction = {}     # persona_id -> {"burst_until": int, "shame": bool}
        self._share_sent_ts = {}        # persona_id -> int (已下发给 Antigravity 的最新共享数据 sys 消息 ts)
        self._answer_image_context = {} # (conversation_id, subject session) -> original answer and source image
        self._pending_answer_images = {} # conversation_id -> rendered bytes awaiting oit_reply
        self._running = False
        self._load_sessions()

    @property
    def discovery_path(self) -> str:
        return os.path.join(self.data_dir, BRIDGE_FILE)

    @property
    def sessions_path(self) -> str:
        return os.path.join(self.data_dir, SESSIONS_FILE)

    def _load_sessions(self):
        """启动时从 mcp_sessions.json 恢复 1:1 绑定表，保证重启 OIT 或 Antigravity 后绑定不丢失。"""
        if not self.data_dir or not os.path.isfile(self.sessions_path):
            return
        try:
            with open(self.sessions_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                sessions = self.comm.cfg.setdefault("mcp_sessions", {})
                if not isinstance(sessions, dict):
                    sessions = self.comm.cfg["mcp_sessions"] = {}
                for cid, meta in data.items():
                    if isinstance(cid, str) and isinstance(meta, dict) and meta.get("persona_id"):
                        sessions[cid] = meta
        except Exception:
            pass

    def _save_sessions(self):
        """同步原子落盘 mcp_sessions.json，避免异步写入在快速重启时丢失绑定。"""
        if not self.data_dir:
            return
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            sessions = self._sessions_map()
            tmp = self.sessions_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(sessions, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.sessions_path)
        except OSError:
            pass

    def start(self) -> int:
        with self._lock:
            if self._running and self.port:
                return self.port
            bridge = self

            class Handler(http.server.BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"

                def log_message(self, fmt, *args):
                    return

                def _auth_ok(self) -> bool:
                    auth = str(self.headers.get("Authorization") or "")
                    return auth == f"Bearer {bridge.token}"

                def _send_json(self, code: int, payload: dict):
                    raw = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(raw)

                def _read_json(self) -> dict:
                    length = int(self.headers.get("Content-Length") or 0)
                    if length <= 0:
                        return {}
                    if length > BODY_MAX:
                        raise ValueError("request body too large")
                    raw = self.rfile.read(length)
                    data = json.loads(raw.decode("utf-8"))
                    if not isinstance(data, dict):
                        raise ValueError("json body must be an object")
                    return data

                def do_GET(self):
                    if not self._auth_ok():
                        self._send_json(401, {"ok": False, "error": "unauthorized"})
                        return
                    parsed = urllib.parse.urlsplit(self.path)
                    path = parsed.path
                    qs = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
                    if path == "/events/stream":
                        self._handle_sse()
                        return
                    try:
                        if path == "/health":
                            res = bridge.handle_health()
                        elif path == "/session/check":
                            res = bridge.handle_session_check(qs)
                        elif path == "/turn/context":
                            res = bridge.handle_turn_context(qs)
                        elif path == "/ui/state":
                            res = bridge.handle_ui_state(qs)
                        elif path == "/ui/media":
                            res = bridge.handle_ui_media(qs)
                        elif path == "/persona/active":
                            res = bridge.handle_persona_active(qs)
                        elif path == "/persona/claudia_reference":
                            res = bridge.handle_claudia_reference()
                        else:
                            self._send_json(404, {"ok": False, "error": "not_found"})
                            return
                        self._send_json(200, res)
                    except Exception as exc:  # noqa: BLE001
                        self._send_json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

                def do_POST(self):
                    if not self._auth_ok():
                        self._send_json(401, {"ok": False, "error": "unauthorized"})
                        return
                    parsed = urllib.parse.urlsplit(self.path)
                    path = parsed.path
                    try:
                        body = self._read_json()
                    except Exception as exc:  # noqa: BLE001
                        self._send_json(400, {"ok": False, "error": str(exc)})
                        return
                    try:
                        if path == "/session/bind":
                            res = bridge.handle_session_bind(body)
                        elif path == "/tool/execute":
                            res = bridge.handle_tool_execute(body)
                        elif path == "/reply/submit":
                            res = bridge.handle_reply_submit(body)
                        elif path == "/ui/delivered":
                            res = bridge.handle_ui_delivered(body)
                        elif path == "/ui/interrupt":
                            res = bridge.handle_ui_interrupt(body)
                        elif path == "/ui/action":
                            res = bridge.handle_ui_action(body)
                        elif path == "/turn/fallback_sync":
                            res = bridge.handle_fallback_sync(body)
                        else:
                            self._send_json(404, {"ok": False, "error": "not_found"})
                            return
                        self._send_json(200, res)
                    except Exception as exc:  # noqa: BLE001
                        self._send_json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

                def _handle_sse(self):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.send_header("X-Accel-Buffering", "no")
                    self.end_headers()
                    q = bridge._register_sse()
                    try:
                        self.wfile.write(b": connected\n\n")
                        self.wfile.flush()
                        while bridge._running:
                            try:
                                event_name, payload = q.get(timeout=SSE_HEARTBEAT_S)
                                data = json.dumps(payload, ensure_ascii=False)
                                frame = f"event: {event_name}\ndata: {data}\n\n".encode("utf-8")
                            except queue.Empty:
                                frame = b": ping\n\n"
                            self.wfile.write(frame)
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                        pass
                    finally:
                        bridge._unregister_sse(q)

            class _QuietHTTPServer(http.server.ThreadingHTTPServer):
                def handle_error(self, request, client_address):
                    _, exc, _ = sys.exc_info()
                    if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
                        return
                    super().handle_error(request, client_address)

            self._server = _QuietHTTPServer(("127.0.0.1", 0), Handler)
            self._server.daemon_threads = True
            self.port = int(self._server.server_address[1])
            self._running = True
            self._write_discovery()
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="oit-mcp-bridge")
            self._thread.start()
            return self.port

    def stop(self):
        with self._lock:
            self._running = False
            for q in list(self._sse_clients):
                try:
                    q.put_nowait(("shutdown", {"ok": True}))
                except Exception:
                    pass
            srv, self._server = self._server, None
            self.port = 0
        if srv is not None:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass
        try:
            if os.path.isfile(self.discovery_path):
                with open(self.discovery_path, "r", encoding="utf-8") as fh:
                    cur = json.load(fh)
                if cur.get("token") == self.token and int(cur.get("pid") or 0) == os.getpid():
                    os.remove(self.discovery_path)
        except Exception:
            pass

    def ensure_discovery(self):
        """若运行中发现 mcp_bridge.json 被外部误删，自动补写恢复。"""
        if not self._running or not self.port or not self.data_dir:
            return
        if not os.path.isfile(self.discovery_path):
            try:
                self._write_discovery()
            except OSError:
                pass

    def _write_discovery(self):
        if not self.data_dir:
            return
        os.makedirs(self.data_dir, exist_ok=True)
        payload = {
            "port": self.port,
            "token": self.token,
            "pid": os.getpid(),
            "protocol": BRIDGE_PROTOCOL,
        }
        tmp = self.discovery_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.discovery_path)

    def _register_sse(self) -> queue.Queue:
        q = queue.Queue(maxsize=64)
        with self._lock:
            self._sse_clients.append(q)
        return q

    def _unregister_sse(self, q: queue.Queue):
        with self._lock:
            if q in self._sse_clients:
                self._sse_clients.remove(q)

    def _emit_sse(self, event_name: str, payload: dict):
        with self._lock:
            clients = list(self._sse_clients)
        for q in clients:
            try:
                q.put_nowait((event_name, payload))
            except queue.Full:
                pass

    # ── 主动触发与事件推送 ──
    def push_proactive(self, persona_id: str, body: str, kind: str = "proactive",
                       extra: dict = None) -> dict:
        """OIT 状态机命中主动对话/后台事件时调用：暂存完整系统指令并推送极简 SSE 信号给 Sidecar。"""
        now = self.comm.now() if callable(getattr(self.comm, "now", None)) else datetime.now()
        display_time = f"{now:%H:%M}"
        cid = self._conversation_for_persona(persona_id)
        if (extra or {}).get("bg") and not cid:
            return {"event": "proactive", "skipped": "unbound_background_persona", "persona_id": persona_id}
        key = cid or persona_id or "*"
        entry_ts = self.comm._ms() if hasattr(self.comm, "_ms") else _now_ms()
        entry = {
            "persona_id": persona_id,
            "conversation_id": cid,
            "kind": kind,
            "body": str(body or ""),
            "display_time": display_time,
            "ts": entry_ts,
            "extra": dict(extra or {}),
        }
        with self._lock:
            self._pending_triggers[key] = entry
        event = {
            "event": "proactive",
            "persona_id": persona_id,
            "persona_name": self._persona_name_by_pid(persona_id) or _display_persona_name(self.comm),
            "conversation_id": cid,
            "kind": kind,
            "display_time": display_time,
            "ts": entry["ts"],
        }
        self._emit_sse("proactive", event)
        return event

    def unbind_persona(self, persona_id: str) -> list:
        """清空对话时解绑该人设的旧 Antigravity 会话并清理暂存状态，下次发言由 Sidecar 懒创建全新会话。"""
        pid = str(persona_id or getattr(self.comm, "pid", "") or "").strip()
        if not pid:
            return []
        sessions = self._sessions_map()
        removed = [cid for cid, meta in list(sessions.items())
                   if isinstance(meta, dict) and meta.get("persona_id") == pid]
        for cid in removed:
            sessions.pop(cid, None)
        with self._lock:
            for key in (*removed, pid):
                self._pending_triggers.pop(key, None)
                self._last_reply_ms.pop(key, None)
                self._recent_user_ms.pop(key, None)
            self._interrupted.pop(pid, None)
            self._last_batch.pop(pid, None)
            self._share_sent_ts.pop(pid, None)
        if removed:
            self._save_sessions()
            if hasattr(self.comm, "save_cfg"):
                self.comm.save_cfg()
        return removed

    def push_user_message(self, persona_id: str, text: str, api_context: str = "",
                          trigger_kind: str = "user_attachment", held_messages: str = "",
                          image_path: str = "") -> bool:
        """用户在 OIT 桌面端或同步手机端直接发消息时，转发给 Sidecar 唤醒对应 Antigravity 对话（未绑定时由 Sidecar 自动新建会话并绑定）；返回是否有活跃 Sidecar 接收。"""
        if not str(text or "").strip():
            return False
        cid = self._conversation_for_persona(persona_id)
        pname = _display_persona_name(self.comm) or self._persona_name_by_pid(persona_id)
        now_ms = self.comm._ms() if hasattr(self.comm, "_ms") else _now_ms()
        img_path = str(image_path or "").strip()
        with self._lock:
            self._pending_answer_images.pop(cid, None)
            for key in [key for key in self._answer_image_context if key[0] == cid]:
                self._answer_image_context.pop(key, None)
            has_client = bool(self._sse_clients)
            body_txt = str(api_context or "").strip()
            if img_path and img_path not in body_txt:
                img_note = (
                    f"用户随本条消息发送了一张图片文件：{img_path}\n"
                    "请先使用 read_file 工具查看该图片内容。若涉及数理化题目并需要调用 oit-subject 子代理，"
                    "必须将该图片完整路径一并写入子代理 Prompt，要求子代理亲自调用 read_file 查看原图，"
                    "不要仅转述文字；回复用户时保留清晰的关键推导过程。"
                )
                body_txt = f"{body_txt}\n\n{img_note}".strip() if body_txt else img_note
            held_txt = str(held_messages or "").strip()
            if body_txt or held_txt:
                key = cid or persona_id or "*"
                self._pending_triggers[key] = {
                    "persona_id": persona_id,
                    "conversation_id": cid,
                    "kind": str(trigger_kind or "user_attachment"),
                    "body": body_txt,
                    "held_messages": held_txt,
                    "delivered_via_sse": has_client,
                    "display_time": "",
                    "ts": now_ms,
                    "extra": {},
                }
        if not has_client:
            return False
        self._emit_sse("user_message", {
            "event": "user_message",
            "persona_id": persona_id,
            "persona_name": pname,
            "conversation_id": cid,
            "text": str(text).strip(),
            "image_path": img_path,
            "ts": now_ms,
        })
        return True

    def _sessions_map(self) -> dict:
        sessions = self.comm.cfg.setdefault("mcp_sessions", {})
        if not isinstance(sessions, dict):
            sessions = self.comm.cfg["mcp_sessions"] = {}
        return sessions

    def _persona_name_by_pid(self, pid: str) -> str:
        if not pid or not hasattr(self.comm, "personas"):
            return ""
        try:
            from comm.comm_persona import list_personas, pid_of
            for name in list_personas(self.comm.personas):
                if pid_of(name) == pid:
                    return name
        except Exception:
            pass
        return ""

    def _resolve_persona_name(self, query: str) -> str:
        q = str(query or "").strip()
        if not q or not hasattr(self.comm, "personas"):
            return ""
        try:
            from comm.comm_persona import list_personas, persona_title, pid_of
            names = list_personas(self.comm.personas)
            for name in names:
                if name == q or persona_title(name) == q or pid_of(name) == q:
                    return name
            ql = q.lower()
            for name in names:
                if name.lower() == ql or persona_title(name).lower() == ql:
                    return name
        except Exception:
            pass
        return ""

    def _ensure_persona_for_conversation(self, conversation_id: str) -> str:
        """若该 conversation_id 已绑定人设且与 OIT 当前人设不同，自动切换 OIT 到绑定人设，避免切对话串台。"""
        pid = self._persona_for_conversation(conversation_id)
        if pid and pid != getattr(self.comm, "pid", "") and hasattr(self.comm, "set_persona"):
            name = self._persona_name_by_pid(pid)
            if name:
                self.comm.set_persona(name)
        return str(getattr(self.comm, "pid", "") or pid)

    def _conversation_for_persona(self, persona_id: str) -> str:
        sessions = self._sessions_map()
        pid = persona_id or getattr(self.comm, "pid", "")
        for cid, meta in sessions.items():
            if isinstance(meta, dict) and meta.get("persona_id") == pid:
                return cid
        return ""

    def _persona_for_conversation(self, conversation_id: str) -> str:
        if conversation_id:
            meta = self._sessions_map().get(conversation_id)
            if isinstance(meta, dict) and meta.get("persona_id"):
                return str(meta["persona_id"])
        return str(getattr(self.comm, "pid", "") or "")

    def _subagent_routes(self, host: str = "antigravity") -> dict:
        """按子代理角色返回独立路由：用户单独配了正规外部 HTTP API 的走 external_api，其余走宿主原生子代理或降级。"""
        ai = getattr(self.comm, "ai", None)
        task_assign = getattr(ai, "task_assign", {}) if ai else {}
        profiles = getattr(ai, "profiles", {}) if ai else {}
        host_supports_subagents = host in ("antigravity", "claude_code")
        routes = {}
        for role in SUBAGENT_ROLES:
            task_key = _ROLE_TASK_KEY[role]
            cid = str(task_assign.get(task_key) or "").strip()
            prof = profiles.get(cid) if cid and isinstance(profiles, dict) else None
            is_external_http = bool(
                prof and str(prof.get("kind") or "") != "cli"
                and str(prof.get("protocol") or "") not in ("cli", "device")
                and (str(prof.get("api_key") or "").strip() or str(prof.get("key") or "").strip()
                     or str(prof.get("base_url") or "").strip())
            )
            if is_external_http:
                routes[role] = {"mode": "external_api", "config_id": cid}
            elif host_supports_subagents:
                routes[role] = {"mode": "host_subagent", "agent": f"oit-{role.replace('_', '-')}"}
            else:
                routes[role] = {"mode": "host_inline_fallback"}
        return routes

    # ── HTTP 处理方法（主线程安全） ──
    def handle_health(self) -> dict:
        def work():
            return {
                "ok": True,
                "version": getattr(config, "APP_VERSION", ""),
                "protocol": BRIDGE_PROTOCOL,
                "takeover": str(getattr(config, "AI_CHAT_TAKEOVER", "") or ""),
                "persona_id": getattr(self.comm, "pid", ""),
                "persona_name": _display_persona_name(self.comm),
                "hard_block": bool(self.comm.hard_block()) if callable(getattr(self.comm, "hard_block", None)) else False,
            }
        return self._call_main(work)

    def is_bound_conversation(self, conversation_id: str) -> bool:
        cid = str(conversation_id or "").strip()
        if not cid:
            return False
        meta = self._sessions_map().get(cid)
        return isinstance(meta, dict) and bool(meta.get("persona_id"))

    def handle_session_check(self, qs: dict) -> dict:
        cid = str(qs.get("conversation_id") or "").strip()

        def work():
            if not self.is_bound_conversation(cid):
                return {"ok": True, "bound": False, "conversation_id": cid}
            pid = self._ensure_persona_for_conversation(cid)
            return {
                "ok": True,
                "bound": True,
                "conversation_id": cid,
                "persona_id": pid,
                "persona_name": _display_persona_name(self.comm),
            }
        return self._call_main(work)

    def handle_session_bind(self, body: dict) -> dict:
        cid = str(body.get("conversation_id") or "").strip()
        if not cid:
            return {"ok": False, "error": "conversation_id required"}
        host = str(body.get("host") or "antigravity").strip()
        req_name = str(body.get("persona_name") or "").strip()
        req_pid = str(body.get("persona_id") or "").strip()
        force = bool(body.get("force"))

        def work():
            sessions = self._sessions_map()
            existing = sessions.get(cid) if isinstance(sessions.get(cid), dict) else None
            # 严格 1:1 绑定：已绑定过的会话在未指定 force 且未显式指定新人设时，保持原绑定并切回对应人设
            if existing and existing.get("persona_id") and not force and not req_name and not req_pid:
                pid = str(existing["persona_id"])
                self._ensure_persona_for_conversation(cid)
            else:
                target_name = self._resolve_persona_name(req_name) if req_name else ""
                if req_name and not target_name:
                    return {"ok": False, "error": f"未找到人设 {req_name}"}
                if req_pid and not target_name:
                    target_name = self._persona_name_by_pid(req_pid)
                if target_name:
                    from comm.comm_persona import pid_of
                    pid = pid_of(target_name)
                else:
                    pid = req_pid or str(getattr(self.comm, "pid", "") or "").strip()

                # 严格 1:1 排斥：若该人设已绑定到其他对话且未传 force=True，拒绝重复绑定
                other_cids = [k for k, v in sessions.items()
                              if k != cid and isinstance(v, dict) and v.get("persona_id") == pid]
                if other_cids and not force:
                    disp = _display_persona_name(self.comm)
                    if target_name:
                        from comm.comm_persona import persona_title
                        disp = persona_title(target_name)
                    return {
                        "ok": False,
                        "conflict": True,
                        "persona_id": pid,
                        "bound_conversation_id": other_cids[0],
                        "error": f"人设「{disp}」已绑定到另一个对话（{other_cids[0]}），不能同时绑定多个对话；请切回原对话，或传入 force=true 强制解绑旧对话并改绑到当前对话。",
                    }
                if existing and existing.get("persona_id") and existing.get("persona_id") != pid and not force:
                    return {
                        "ok": False,
                        "conflict": True,
                        "conversation_id": cid,
                        "bound_persona_id": existing.get("persona_id"),
                        "error": "当前对话已绑定其他人设；请新建对话，或传入 force=true 改绑。",
                    }
                if target_name and hasattr(self.comm, "set_persona"):
                    self.comm.set_persona(target_name)
                for old_cid in other_cids:
                    sessions.pop(old_cid, None)
                sessions[cid] = {"persona_id": pid, "host": host, "ts": _now_ms()}
                self._save_sessions()
                if hasattr(self.comm, "save_cfg"):
                    self.comm.save_cfg()
            return {
                "ok": True,
                "conversation_id": cid,
                "persona_id": pid,
                "persona_name": _display_persona_name(self.comm),
                "host": host,
                "subagent_routes": self._subagent_routes(host),
            }
        return self._call_main(work)

    def handle_persona_active(self, qs: dict) -> dict:
        def work():
            name = self.comm.persona_name() if hasattr(self.comm, "persona_name") else ""
            pid = getattr(self.comm, "pid", "")
            system = ""
            if name and hasattr(self.comm, "personas"):
                from comm.comm_persona import persona_system
                try:
                    system = persona_system(self.comm.personas, name, False)
                except Exception:
                    system = ""
            mem = self.comm.memory_block_of(self.comm.cfg) if hasattr(self.comm, "memory_block_of") else ""
            return {
                "ok": True,
                "persona_id": pid,
                "persona_name": _display_persona_name(self.comm),
                "system_prompt": system,
                "memory_block": mem,
            }
        return self._call_main(work)

    def handle_claudia_reference(self) -> dict:
        """解密并返回目前存在的所有 Claudia 参考文件与 OIT 前置协议，供人设编写参考。"""
        def work():
            from comm.comm_author import claudia_reference_files
            from comm.comm_prompt import prefix_prompt
            folder = str(getattr(self.comm, "personas", "") or "")
            files = claudia_reference_files(folder)
            return {
                "ok": True,
                "personas_dir": folder,
                "file_names": list(files.keys()),
                "files": files,
                "prefix_prompt": prefix_prompt(),
            }
        return self._call_main(work)

    def handle_turn_context(self, qs: dict) -> dict:
        cid = str(qs.get("conversation_id") or "").strip()
        consume = str(qs.get("consume_trigger") or "1") not in ("0", "false", "False")
        only_bound = str(qs.get("only_bound") or "0") in ("1", "true", "True")
        host = str(qs.get("host") or "").strip()
        last_input = str(qs.get("last_user_input") or "")

        def work():
            if only_bound and not self.is_bound_conversation(cid):
                return {
                    "ok": True,
                    "bound": False,
                    "conversation_id": cid,
                    "system_message": "",
                }
            pid = self._ensure_persona_for_conversation(cid)
            sess_meta = self._sessions_map().get(cid) if cid else None
            eff_host = host or (sess_meta.get("host") if isinstance(sess_meta, dict) else "") or "antigravity"
            now = self.comm.now() if callable(getattr(self.comm, "now", None)) else datetime.now()
            status = self.comm.other_status(now) if hasattr(self.comm, "other_status") else {}

            trigger = None
            with self._lock:
                for key in (cid, pid, "*"):
                    if key and key in self._pending_triggers:
                        trigger = self._pending_triggers.pop(key) if consume else dict(self._pending_triggers[key])
                        break
                interrupted = self._interrupted.pop(pid, None) if consume else self._interrupted.get(pid)
                if cid and not trigger:
                    self._recent_user_ms[cid] = _now_ms()

            routes = self._subagent_routes(eff_host)
            head = f"[OIT动态状态] {now:%Y-%m-%d %H:%M} | {_display_persona_name(self.comm)}"
            lines = [head, _compact_schedule_line(self.comm, now, status)]
            if hasattr(self.comm, "_interaction_prepare"):
                d_inter = self.comm._interaction_prepare(pid, status)
                with self._lock:
                    self._turn_interaction[pid] = {
                        "burst_until": int(d_inter.get("burst_until") or 0),
                        "shame": bool(d_inter.get("shame_turns")),
                    }
                judge_kind = (
                    "user"
                    if (not trigger or trigger.get("kind") in ("user_attachment", "user", "retry"))
                    else "proactive"
                )
                lines.extend(_compact_interaction_lines(self.comm, pid, status, judge_kind))
                if hasattr(self.comm, "realism_note"):
                    r_note = self.comm.realism_note(pid)
                    if r_note:
                        lines.append(r_note)

            # 增量下发尚未送达 Antigravity 的 share data 页面数据（role="sys"），供悬浮窗聊天与结束总结使用
            from comm.comm_const import SHARE_TAG
            with self._lock:
                last_share_ts = int(self._share_sent_ts.get(pid) or 0)
            share_chunks = []
            max_share_ts = last_share_ts
            for m in self.comm.messages:
                if m.get("role") != "sys":
                    continue
                mts = int(m.get("ts") or 0)
                if mts <= last_share_ts:
                    continue
                mtxt = str(m.get("text") or "")
                mapi = str(m.get("api") or "")
                if mtxt.startswith("共享数据·") or SHARE_TAG in mapi:
                    payload_txt = (mapi or mtxt).strip()
                    if payload_txt:
                        share_chunks.append(payload_txt)
                        if mts > max_share_ts:
                            max_share_ts = mts
            if share_chunks:
                lines.append("[OIT共享数据]\n" + "\n\n".join(share_chunks))
                if consume:
                    with self._lock:
                        self._share_sent_ts[pid] = max_share_ts

            if interrupted and interrupted.get("count"):
                lines.append(
                    f"[OIT插话撤销] 上一轮回复有 {interrupted['count']} 条待发气泡尚未弹出即被用户新消息打断并作废，请勿假设用户看到了未发出的内容。"
                )
            if trigger:
                if trigger.get("held_messages") and not trigger.get("delivered_via_sse"):
                    lines.append(f"[OIT忙碌期间用户消息]\n{trigger['held_messages']}")
                if trigger.get("body"):
                    tkind = trigger.get("kind")
                    if tkind == "user_attachment":
                        lines.append(f"[OIT本轮附加请求]\n{trigger['body']}")
                    elif tkind == "retry":
                        lines.append(f"[OIT延迟回复说明] {trigger['body']}")
                    else:
                        lines.append(f"[OIT主动触发] {trigger['body']}")

            pending_asks = []
            if hasattr(self.comm, "share_asking") and self.comm.share_asking():
                pending_asks.append("share data 请求")
            if hasattr(self.comm, "shift_asking") and self.comm.shift_asking():
                pending_asks.append("日程起点平移请求")
            coread_ask = getattr(self.comm, "coread_ask", None)
            if isinstance(coread_ask, dict) and coread_ask.get("pid") == pid:
                pending_asks.append("共同阅读 (coread) 请求")
            joint_cur = self.comm.cfg.get("joint") or {}
            if isinstance(joint_cur, dict) and joint_cur.get("state") == "wait" and joint_cur.get("pid") == pid:
                pending_asks.append("联合行动邀请")
            if pending_asks:
                lines.append(
                    f"[OIT待答复请求] 当前有用户发来的 {' / '.join(pending_asks)} 正在等待你明确表态："
                    '请在调用 oit_reply 时务必填写 answer="yes"（同意）或 answer="no"（拒绝），未填写 answer 将视为未明确同意。'
                )

            # 心理风险信号检测与子代理路由指示
            mental_hint = ""
            if last_input and hasattr(self.comm, "_mental_trigger"):
                try:
                    m_reason = self.comm._mental_trigger(last_input)
                except Exception:
                    m_reason = ""
                if m_reason:
                    m_route = routes.get("mental_health", {})
                    if m_route.get("mode") == "host_subagent":
                        mental_hint = (
                            "[OIT安全提示] 检测到心理困扰/风险信号。在调用 oit_reply 回复用户之前，"
                            "必须先调用子代理 oit-mental-health（传入本次用户原话），拿到内部评估后再按人设组织回复，"
                            "且绝不可向用户提及子代理。"
                        )
                        lines.append(mental_hint)

            non_default_routes = [
                f"{r}={v['mode']}" for r, v in routes.items() if v.get("mode") != "host_subagent"
            ]
            if non_default_routes:
                lines.append(f"[OIT子代理路由] {', '.join(non_default_routes)}")
            answer_images = callable(getattr(self.comm, "answer_images_on", None)) and self.comm.answer_images_on()
            lines.append(f"[OIT解答图片模式] {'开启' if answer_images else '关闭'}。")

            return {
                "ok": True,
                "conversation_id": cid,
                "persona_id": pid,
                "now": now.isoformat(timespec="seconds"),
                "status": {k: (v.isoformat(timespec="seconds") if isinstance(v, datetime) else v) for k, v in (status or {}).items()},
                "trigger": trigger,
                "interrupted": interrupted,
                "subagent_routes": routes,
                "system_message": "\n".join(x for x in lines if x),
            }
        return self._call_main(work)

    def handle_tool_execute(self, body: dict) -> dict:
        name = str(body.get("name") or "").strip()
        args = body.get("arguments") if isinstance(body.get("arguments"), dict) else {}
        cid = str(body.get("conversation_id") or args.get("conversation_id") or
                  self._conversation_for_persona(getattr(self.comm, "pid", "")) or "").strip()
        host = str(body.get("host") or "antigravity").strip()

        def work():
            if not name:
                return {"ok": False, "error": "tool name required"}
            if cid:
                self._ensure_persona_for_conversation(cid)
            if name == "oit_bind_persona":
                target_cid = str(args.get("conversation_id") or cid or "").strip()
                target_name = str(args.get("persona_name") or "").strip()
                force = bool(args.get("force"))
                if not target_cid:
                    return {"ok": False, "error": "缺少 conversation_id，无法建立 1 对 1 绑定。"}
                if target_name:
                    resolved = self._resolve_persona_name(target_name)
                    if not resolved:
                        from comm.comm_persona import list_personas, persona_title
                        avail = [persona_title(n) for n in list_personas(self.comm.personas)]
                        return {"ok": False, "error": f"未找到人设 {target_name}", "available_personas": avail}
                else:
                    resolved = self.comm.persona_name() if hasattr(self.comm, "persona_name") else ""
                from comm.comm_persona import persona_title, pid_of
                pid = pid_of(resolved) if resolved else str(getattr(self.comm, "pid", "") or "")
                sessions = self._sessions_map()
                other_cids = [k for k, v in sessions.items()
                              if k != target_cid and isinstance(v, dict) and v.get("persona_id") == pid]
                if other_cids and not force:
                    disp = persona_title(resolved) if resolved else _display_persona_name(self.comm)
                    return {
                        "ok": False,
                        "conflict": True,
                        "persona_id": pid,
                        "bound_conversation_id": other_cids[0],
                        "error": f"人设「{disp}」已绑定到另一个对话（{other_cids[0]}），不能同时绑定多个对话；请切回原对话，或传入 force=true 强制解绑旧对话并改绑到当前对话。",
                    }
                existing = sessions.get(target_cid) if isinstance(sessions.get(target_cid), dict) else None
                if existing and existing.get("persona_id") and existing.get("persona_id") != pid and not force:
                    return {
                        "ok": False,
                        "conflict": True,
                        "conversation_id": target_cid,
                        "bound_persona_id": existing.get("persona_id"),
                        "error": "当前对话已绑定其他人设；请新建对话，或传入 force=true 改绑。",
                    }
                if resolved and hasattr(self.comm, "set_persona"):
                    self.comm.set_persona(resolved)
                for old_cid in other_cids:
                    sessions.pop(old_cid, None)
                sessions[target_cid] = {"persona_id": pid, "host": host, "ts": _now_ms()}
                self._save_sessions()
                self.comm.save_cfg()
                return {
                    "ok": True,
                    "persona_id": pid,
                    "persona_name": _display_persona_name(self.comm),
                    "conversation_id": target_cid,
                }
            if self.comm.hard_block():
                return {"ok": False, "error": "当前处于禁止沟通状态"}

            if name == "oit_query":
                from comm.comm_prompt import native_persona_text
                modules = args.get("modules") or []
                values = {}
                for module in modules:
                    try:
                        if module == "condition":
                            values[module] = {"type": "condition", "data": native_persona_text(self.comm.condition_text())}
                        elif module == "messages":
                            values[module] = {"type": "count", "data": native_persona_text(self.comm.messages_text())}
                        else:
                            own = self.comm.routine_info(module) if hasattr(self.comm, "routine_info") else ""
                            text = own or str(self.comm.info_fn(module) or "")
                            values[module] = {
                                "type": "own_data" if own else "reference_template",
                                "data": native_persona_text(text),
                                "available": bool(text),
                            }
                    except Exception:
                        values[module] = {"available": False, "error": "unavailable"}
                return {"ok": True, "data": values}

            if name == "oit_memory_edit":
                action = str(args.get("action") or "")
                target = str(args.get("target") or "").strip()
                text = str(args.get("text") or "").strip()
                entries = [line[2:].strip() for line in str(self.comm.cfg.get("mem_live") or "").splitlines()
                           if line.strip().startswith("- ")]
                valid_text = "\n" not in text and "\r" not in text and "【系统记录】" not in text
                if action == "add" and not target and text and valid_text and text not in entries and len(entries) < 15:
                    entries.append(text)
                elif action == "correct" and target in entries and text and valid_text:
                    entries[entries.index(target)] = text
                elif action == "remove" and target in entries:
                    entries.remove(target)
                else:
                    return {"ok": False, "error": "记忆事件不匹配或已达到数量上限"}
                self.comm.cfg["mem_live"] = "\n".join(f"- {e}" for e in entries)
                self.comm.save_cfg()
                self.comm._changed()
                return {"ok": True, "entries": entries}

            if name == "oit_memory_save_auto":
                text = str(args.get("text") or "").strip()
                if not text:
                    return {"ok": False, "error": "text required"}
                self.comm.cfg["mem_auto"] = text[:4000]
                self.comm.cfg["mem_ts"] = _now_ms()
                self.comm.save_cfg()
                self.comm._changed()
                return {"ok": True}

            def _latest_user_image():
                last_user = next((m for m in reversed(self.comm.messages) if m.get("role") == "me"), {})
                img_name = str(last_user.get("img") or "")
                if img_name and hasattr(self.comm, "chat_image_path"):
                    try:
                        with open(self.comm.chat_image_path(img_name), "rb") as stream:
                            return stream.read()
                    except OSError:
                        return None
                return None

            if name == "oit_subject":
                routes = self._subagent_routes(host)
                route = routes.get("subject", {})
                domain = str(args.get("domain") or "math")
                session = str(args.get("session") or "default")
                question = str(args.get("question") or "").strip()
                image = _latest_user_image()
                ctx = {"domain": domain, "session": session, "question": question,
                       "answer": "", "image": image, "ts": _now_ms()}
                self._answer_image_context[(cid, session)] = ctx
                if route.get("mode") != "external_api":
                    return {
                        "ok": False,
                        "route": route.get("mode", "host_subagent"),
                        "target": route.get("agent", "oit-subject"),
                        "instruction": "当前未为学科子代理单独配置外部 API，请改用宿主原生子代理 oit-subject（或按客观步骤直接推导），并按当前人设语气风格化改写；解答图片模式开启时，在 oit_reply 前调用 oit_answer_image 提交原始解答和人设风格化解答。",
                    }
                answer = self.comm.subagent_call(
                    "subject", session, f"学科: {domain}\n题目/追问:\n{question}",
                    image=image, max_tokens=900, temperature=0.15, timeout=180)
                ctx["answer"] = answer
                return {"ok": True, "domain": domain, "session": session, "answer": answer}

            if name == "oit_answer_image":
                enabled = callable(getattr(self.comm, "answer_images_on", None)) and self.comm.answer_images_on()
                if not enabled:
                    return {"ok": True, "enabled": False, "instruction": "解答图片模式已关闭；请仅提交文字回复。"}
                routes = self._subagent_routes(host)
                use_external = routes.get("subject", {}).get("mode") == "external_api"
                domain = str(args.get("domain") or "math")
                session = str(args.get("session") or "default")
                original = str(args.get("answer") or "").strip()[:8000]
                styled = str(args.get("styled_answer") or "").strip()[:8000]
                if not original or not styled:
                    return {"ok": False, "error": "answer 和 styled_answer 均不能为空"}
                context = self._answer_image_context.get((cid, session), {})
                if context and context.get("domain") != domain:
                    return {"ok": False, "error": "domain 与本轮 oit_subject 不匹配"}
                if use_external and not context:
                    return {"ok": False, "error": "请先在本轮调用 oit_subject，再生成对应解答图片"}
                source_image = context.get("image") if context else _latest_user_image()
                try:
                    if use_external:
                        prompt = (
                            "请根据原始学科解答与聊天 AI 已完成的用户可见版本，生成最终的解答图片排版稿。"
                            "保留原始解答中可核验的关键逻辑、步骤与结论；以聊天 AI 的语言风格为准。"
                            "使用 Markdown 标题、列表和 LaTeX 公式，内容完整但紧凑；不要输出解释、代码围栏或隐藏思维过程。\n\n"
                            f"原始学科解答:\n{original}\n\n聊天 AI 风格化版本:\n{styled}"
                        )
                        formatted = self.comm.subagent_call(
                            "subject", f"answer-image:{session}", prompt, image=source_image,
                            max_tokens=1400, temperature=0.1, timeout=180)
                    else:
                        formatted = (
                            f"{styled}\n\n## 详细推导\n\n{original}"
                            if len(styled) < 40 and len(original) > len(styled) * 2
                            else styled
                        )
                    import answer_images
                    data = answer_images.render(formatted, source_image, title=f"{domain}解答")
                    if not data:
                        detail = answer_images.last_error() if hasattr(answer_images, "last_error") else ""
                        self.comm.log(f"MCP ANSWER_IMAGE renderer unavailable: {detail}" if detail else "MCP ANSWER_IMAGE renderer unavailable")
                        return {"ok": False, "error": "本机无法生成解答图片；请正常提交文字回复。"}
                    self._pending_answer_images[cid] = {"data": data, "ts": _now_ms(), "session": session}
                    self._answer_image_context.pop((cid, session), None)
                    return {"ok": True, "ready": True,
                            "instruction": "解答图片已准备好；请随即调用 oit_reply，图片会附加到最后一条气泡。"}
                except Exception as error:  # noqa: BLE001
                    self.comm.log(f"MCP ANSWER_IMAGE FAIL {type(error).__name__}: {error}")
                    return {"ok": False, "error": "解答图片生成失败；请正常提交文字回复。"}

            if name == "oit_music_catalog":
                related, valence = self.comm._music_access()
                return {"ok": True, "pieces": music_library.catalog_for_ai(self.comm.dir, related, valence)}

            if name == "oit_music_select":
                mid = str(args.get("id") or "").strip()
                related, valence = self.comm._music_access()
                piece = music_library.find_piece(self.comm.dir, mid, related, valence)
                if not piece:
                    return {"ok": False, "error": "曲目不可用"}
                self.comm.cfg["music_selected"] = mid
                self.comm.save_cfg()
                return {"ok": True, "selected": mid, "title": piece.get("title", "")}

            if name == "oit_music_stop":
                self.comm.music_player.stop()
                return {"ok": True}

            if name == "oit_joint_offer":
                if hasattr(self.comm, "_joint_offer_payload"):
                    return {"ok": True, "data": self.comm._joint_offer_payload()}
                return {"ok": True, "data": self.comm.info_fn("contract")}

            if name == "oit_reading_books":
                if not getattr(self.comm, "reading_mode", False):
                    return {"ok": False, "error": "当前未处于共读模式"}
                return {"ok": True, "books": self.comm.coread_books_fn()}

            if name == "oit_reading_page":
                if not getattr(self.comm, "reading_mode", False):
                    return {"ok": False, "error": "当前未处于共读模式"}
                return {"ok": True, "page": self.comm.coread_read_fn(int(args["book_id"]), int(args["page"]))}

            if name == "oit_reading_comment":
                if not getattr(self.comm, "reading_mode", False):
                    return {"ok": False, "error": "当前未处于共读模式"}
                res = self.comm.coread_comment_fn(
                    int(args["book_id"]), int(args["paragraph_idx"]),
                    str(args["content"]), str(args.get("selected_text") or ""))
                return {"ok": True, "result": res}

            if name == "oit_reading_acquire":
                if not getattr(self.comm, "reading_mode", False):
                    return {"ok": False, "error": "当前未处于共读模式"}
                return self.comm.coread_acquire_fn(str(args["source_url"]), lambda: bool(getattr(self.comm, "reading_mode", False)))

            if name == "oit_reading_end":
                if hasattr(self.comm, "end_reading"):
                    self.comm.end_reading()
                return {"ok": True}

            if name == "oit_search":
                if hasattr(self.comm, "net_active") and not self.comm.net_active():
                    return {"ok": False, "error": "当前未开通联网权限"}
                from ai_assist.ai_web import web_search
                return {"ok": True, "results": web_search(str(args.get("query") or ""))}

            return {"ok": False, "error": f"unsupported tool: {name}"}

        return self._call_main(work)

    def handle_reply_submit(self, body: dict) -> dict:
        """oit_reply 提交：切分气泡进入 OIT 待发队列，返回给 aux_pane 按延时逐条弹出。"""
        def work():
            if self.comm.hard_block():
                return {"ok": False, "error": "当前处于禁止沟通状态"}
            cid = str(body.get("conversation_id") or
                      self._conversation_for_persona(getattr(self.comm, "pid", "")) or "").strip()
            pid = self._ensure_persona_for_conversation(cid) if cid else self._persona_for_conversation(cid)
            if body.get("silent"):
                self._pending_answer_images.pop(cid, None)
                return {"ok": True, "silent": True, "batch_id": "", "items": []}
            if body.get("refuse"):
                self._pending_answer_images.pop(cid, None)
                return {"ok": True, "refuse": True, "batch_id": "", "items": []}

            raw_bubbles = body.get("bubbles")
            if isinstance(raw_bubbles, list) and any(str(x).strip() for x in raw_bubbles):
                parts = [str(x).strip() for x in raw_bubbles if str(x).strip()]
            else:
                from comm.comm_delivery import reply_parts
                parts = reply_parts(str(body.get("text") or ""))

            from comm.comm_interaction import tension_clean
            from comm.comm_reply import to_halfwidth
            from comm.comm_util import is_claudia

            persona = self.comm.persona_name() if hasattr(self.comm, "persona_name") else ""
            cleaned = []
            for p in parts:
                p = tension_clean(p).strip()
                if is_claudia(persona):
                    p = to_halfwidth(p)
                if p:
                    cleaned.append(p)

            now_ts = self.comm._ms() if hasattr(self.comm, "_ms") else _now_ms()
            batch_id = f"b_{now_ts}_{secrets.token_hex(4)}"
            req_ts = max([int(x.get("ts") or 0) for x in self.comm.messages if x.get("role") == "me"] or [0])
            self.comm._req_ts = max(int(getattr(self.comm, "_req_ts", 0) or 0), req_ts, now_ts)
            blocked_now = bool(callable(getattr(self.comm, "blocked", None)) and self.comm.blocked())
            cum_ms = 0
            items = []
            created_msgs = []
            for idx, part in enumerate(cleaned):
                delay_ms = _bubble_delay_ms(self.comm, part, idx)
                cum_ms += delay_ms
                m = self.comm._add("ai", part)
                created_msgs.append(m)
                is_late = bool(m.get("late"))
                m["hold"] = True
                if not is_late:
                    m["due"] = max(int(m.get("due") or 0), now_ts + cum_ms)
                if not blocked_now:
                    m["pending_reply"] = req_ts
                    m["reply_ready"] = True
                item_ts = int(m.get("due") or m.get("ts") or now_ts) if is_late else int(m.get("ts") or now_ts)
                item_at = (
                    datetime.fromtimestamp(item_ts / 1000).isoformat(timespec="seconds")
                    if is_late else str(m.get("at") or "")
                )
                items.append({
                    "id": str(m.get("uid") or m.get("id") or f"{batch_id}_{idx}"),
                    "text": part,
                    "at": item_at,
                    "ts": item_ts,
                    "delay_ms": delay_ms,
                })
            held_reply = bool(blocked_now or any(m.get("late") for m in created_msgs))
            answer_image = self._pending_answer_images.pop(cid, None)
            if (answer_image and created_msgs and
                    now_ts - int(answer_image.get("ts") or 0) <= 10 * 60 * 1000 and
                    callable(getattr(self.comm, "answer_images_on", None)) and self.comm.answer_images_on()):
                try:
                    image_name = self.comm._save_chat_image(created_msgs[-1]["uid"] + "-answer", answer_image["data"])
                    if image_name:
                        created_msgs[-1]["img"] = image_name
                        created_msgs[-1]["answer_image"] = True
                        items[-1]["image_id"] = image_name
                        items[-1]["answer_image"] = True
                except Exception as error:  # noqa: BLE001
                    self.comm.log(f"MCP ANSWER_IMAGE SAVE FAIL {type(error).__name__}: {error}")

            # 处理控制字段与请求类事件（coread / share data / shift / joint / love / share_invite）
            applied = {}
            if body.get("wait_minutes") and items:
                self.comm.messages[-1]["wait"] = float(body["wait_minutes"])
            if body.get("cut_minutes"):
                mins = max(1.0, min(1440.0, float(body["cut_minutes"])))
                self.comm.cfg["cut_until"] = _now_ms() + int(mins * 60 * 1000)
                self.comm.save_cfg()
                applied["cut_minutes"] = mins

            if body.get("share_data") and items:
                self.comm.messages[-1]["share_invite"] = True
                if hasattr(self.comm, "_deliver_invites"):
                    self.comm._deliver_invites(self.comm.messages[-1])
                applied["share_data"] = True

            if body.get("accept_relationship") and hasattr(self.comm, "_love_confirm"):
                self.comm._love_confirm("user")
                applied["accept_relationship"] = True

            ans = str(body.get("answer") or "").strip().lower()
            if ans not in ("yes", "no"):
                ans = ""
            joint_cur = self.comm.cfg.get("joint") or {}
            if isinstance(joint_cur, dict) and joint_cur.get("state") == "wait" and joint_cur.get("pid") == pid and ans:
                if hasattr(self.comm, "_joint_answer"):
                    self.comm._joint_answer(ans)
                    applied["joint"] = ans
            if hasattr(self.comm, "share_asking") and self.comm.share_asking():
                self.comm._share_answer(ans)
                applied["share"] = ans or "none"
            if hasattr(self.comm, "shift_asking") and self.comm.shift_asking():
                self.comm._shift_answer(ans)
                applied["shift"] = ans or "none"
            if hasattr(self.comm, "_coread_asking") and self.comm._coread_asking():
                self.comm._coread_answer(ans)
                applied["coread"] = ans or "none"

            # 结算 Tension / Valence / Offenses / Burst
            if hasattr(self.comm, "_interaction_judged") and hasattr(self.comm, "_interaction_prepare"):
                from ai_assist.ai_tools import pack as _pack_tool
                d_cur = self.comm._interaction_prepare(pid)
                if not getattr(self.comm, "_interaction_candidates", None) and d_cur.get("pending"):
                    if hasattr(self.comm, "_interaction_judge_note"):
                        self.comm._interaction_judge_note("user")
                packed_reply = _pack_tool("\n".join(cleaned), "oit_reply", body)
                judged_burst = self.comm._interaction_judged(packed_reply)
                now_dt = self.comm.now() if callable(getattr(self.comm, "now", None)) else datetime.now()
                cur_status = self.comm.other_status(now_dt) if hasattr(self.comm, "other_status") else {}
                special_now = self.comm._interaction_special(cur_status) if hasattr(self.comm, "_interaction_special") else False
                until_ms = int(cur_status["end"].timestamp() * 1000) if special_now and cur_status.get("end") else 0
                with self._lock:
                    snap_meta = self._turn_interaction.pop(pid, {})
                burst_before = int(snap_meta.get("burst_until") or d_cur.get("burst_until") or 0)
                shame_before = bool(snap_meta.get("shame"))
                if hasattr(self.comm, "_interaction_reply"):
                    self.comm._interaction_reply(special_now, judged_burst, until_ms, shame=shame_before, pid=pid)
                if hasattr(self.comm, "_interaction_end"):
                    self.comm._interaction_end(packed_reply, burst_before, pid=pid)
                d_after = self.comm._interaction_prepare(pid)
                applied["tension"] = round(float(d_after.get("tension") or 0), 2)
                applied["valence"] = round(float(d_after.get("valence") or 0), 2)

            with self._lock:
                self._last_batch[pid] = {"batch_id": batch_id, "items": items}
                now_ts = _now_ms()
                if cid:
                    self._last_reply_ms[cid] = now_ts
                if pid:
                    self._last_reply_ms[pid] = now_ts
                self._last_reply_ms["*"] = now_ts
            self.comm.save_chat()
            self.comm._changed()
            if hasattr(self.comm, "_push"):
                self.comm._push()
            return {
                "ok": True,
                "batch_id": batch_id,
                "persona_id": pid,
                "persona_name": _display_persona_name(self.comm),
                "held": held_reply,
                "items": items,
                "applied": applied,
            }
        return self._call_main(work)

    def handle_ui_state(self, qs: dict) -> dict:
        def work():
            pid = str(qs.get("persona_id") or getattr(self.comm, "pid", "") or "")
            cursor = int(qs.get("cursor") or 0)
            now_ms = self.comm._ms() if hasattr(self.comm, "_ms") else _now_ms()
            blocked = bool(callable(getattr(self.comm, "blocked", None)) and self.comm.blocked())
            msgs = []
            pending = []
            next_cursor = None
            for idx, m in enumerate(self.comm.messages):
                if m.get("role") not in ("me", "ai"):
                    continue
                is_held = bool(m.get("hold") or "pending_reply" in m)
                item = {
                    "idx": idx,
                    "id": str(m.get("uid") or m.get("id") or idx),
                    "role": m.get("role"),
                    "text": str(m.get("text") or ""),
                    "at": str(m.get("at") or ""),
                    "ts": int(m.get("ts") or 0),
                    "has_voice": bool(m.get("voice")),
                }
                if m.get("img"):
                    item["image_id"] = os.path.basename(str(m["img"]))
                if m.get("answer_image"):
                    item["answer_image"] = True
                if is_held:
                    if next_cursor is None:
                        next_cursor = idx
                    if not blocked and not m.get("late"):
                        item["delay_ms"] = max(0, int(m.get("due") or 0) - now_ms)
                        pending.append(item)
                elif idx >= cursor:
                    msgs.append(item)
            if next_cursor is None:
                next_cursor = len(self.comm.messages)

            banners = []
            if hasattr(self.comm, "coread_offer") and self.comm.coread_offer():
                banners.append({"kind": "coread", "label": "对方邀请你共同阅读"})
            if hasattr(self.comm, "share_invite") and self.comm.share_invite():
                banners.append({"kind": "share", "label": "对方邀请你开启 share data"})
            joint = self.comm.cfg.get("joint") or {}
            if joint.get("state") == "invite":
                banners.append({"kind": "joint", "label": "对方发起了联合行动邀请"})
            if (self.comm.cfg.get("love") or {}).get("state") == "ask":
                banners.append({"kind": "love", "label": "对方发起了关系确认"})

            now = self.comm.now() if callable(getattr(self.comm, "now", None)) else datetime.now()
            status = self.comm.other_status(now) if hasattr(self.comm, "other_status") else {}
            return {
                "ok": True,
                "persona_id": pid,
                "persona_name": _display_persona_name(self.comm),
                "cursor": next_cursor,
                "blocked": blocked,
                "messages": msgs,
                "pending": pending,
                "toolbar": {
                    "scenario": bool(self.comm.scenario_on()) if hasattr(self.comm, "scenario_on") else False,
                    "muted": bool(self.comm.is_muted(pid)) if hasattr(self.comm, "is_muted") else False,
                    "co_alarm": bool(self.comm.is_co_alarm()) if hasattr(self.comm, "is_co_alarm") else False,
                    "share": bool(getattr(self.comm, "share", {}).get("on")),
                },
                "banners": banners,
                "status_kind": str(status.get("kind") or ""),
            }
        return self._call_main(work)

    def handle_ui_delivered(self, body: dict) -> dict:
        msg_id = str(body.get("msg_id") or "").strip()

        def work():
            if not msg_id:
                return {"ok": False, "error": "msg_id required"}
            if callable(getattr(self.comm, "blocked", None)) and self.comm.blocked():
                return {"ok": False, "blocked": True, "error": self.comm.blocked()}
            found = False
            delivered_msg = None
            now_ms = self.comm._ms() if hasattr(self.comm, "_ms") else _now_ms()
            away = self.comm._away_kind() if hasattr(self.comm, "_away_kind") else ""
            for m in self.comm.messages:
                if str(m.get("uid") or m.get("id") or "") == msg_id:
                    if not m.get("hold") and "pending_reply" not in m:
                        return {"ok": True, "msg_id": msg_id, "already_delivered": True}
                    due = int(m.get("due") or 0)
                    if m.get("late") and away and due > now_ms:
                        return {"ok": False, "held": True, "error": "message still deferred"}
                    m.pop("hold", None)
                    pending = m.pop("pending_reply", None) is not None
                    m.pop("reply_ready", None)
                    m.pop("due", None)
                    send_at = int(m.pop("send_at", 0) or 0)
                    late = bool(m.pop("late", False))
                    m["shown"] = now_ms
                    if late:
                        eff = min(due, now_ms) if (not away and due > 0) else (due or now_ms)
                        m["at"] = datetime.fromtimestamp(eff / 1000).isoformat(timespec="seconds")
                        last_ts = int(getattr(self.comm, "_last_ts", 0) or 0)
                        new_ts = max(now_ms, last_ts + 1)
                        self.comm._last_ts = m["ts"] = new_ts
                        self.comm.messages.remove(m)
                        self.comm.messages.append(m)
                    elif pending:
                        send_ms = send_at or (due if due > 0 else int(m.get("ts") or now_ms))
                        m["at"] = datetime.fromtimestamp(send_ms / 1000).isoformat(timespec="seconds")
                        m["ts"] = max(send_ms, int(m.get("ts") or 0))
                        last_ts = int(getattr(self.comm, "_last_ts", 0) or 0)
                        self.comm._last_ts = max(last_ts, m["ts"])
                    if hasattr(self.comm, "_deliver_invites"):
                        self.comm._deliver_invites(m)
                    delivered_msg = m
                    found = True
                    break
            if not found:
                return {"ok": False, "error": "message not found"}
            if hasattr(self.comm, "_ping") and delivered_msg is not None and not delivered_msg.get("pinged"):
                if self.comm._ping():
                    delivered_msg["pinged"] = True
            self.comm.save_chat()
            self.comm._changed()
            if hasattr(self.comm, "_push"):
                self.comm._push()
            return {"ok": True, "msg_id": msg_id}
        return self._call_main(work)

    def handle_ui_interrupt(self, body: dict) -> dict:
        user_text = str(body.get("user_text") or "").strip()

        def work():
            pid = str(body.get("persona_id") or getattr(self.comm, "pid", "") or "")
            doomed = [m for m in self.comm.messages if "pending_reply" in m]
            if doomed:
                doomed_ids = {id(m) for m in doomed}
                self.comm.messages[:] = [m for m in self.comm.messages if id(m) not in doomed_ids]
                with self._lock:
                    prev = self._interrupted.get(pid, {}).get("count", 0)
                    self._interrupted[pid] = {"count": prev + len(doomed), "ts": _now_ms()}
            cid = self._conversation_for_persona(pid)
            if cid:
                with self._lock:
                    self._pending_answer_images.pop(cid, None)
                    for key in [key for key in self._answer_image_context if key[0] == cid]:
                        self._answer_image_context.pop(key, None)
            added_id = ""
            if user_text:
                um = self.comm._add("me", user_text)
                added_id = str(um.get("uid") or um.get("id") or "")
            self.comm.save_chat()
            self.comm._changed()
            if hasattr(self.comm, "_push"):
                self.comm._push()
            return {"ok": True, "cancelled": len(doomed), "msg_id": added_id}
        return self._call_main(work)

    def handle_ui_action(self, body: dict) -> dict:
        action = str(body.get("action") or "").strip()
        params = body.get("params") if isinstance(body.get("params"), dict) else {}

        def work():
            pid = getattr(self.comm, "pid", "")
            if action == "toggle_scenario" and hasattr(self.comm, "set_scenario"):
                self.comm.set_scenario(not self.comm.scenario_on())
                return {"ok": True, "scenario": self.comm.scenario_on()}
            if action == "toggle_mute" and hasattr(self.comm, "set_muted"):
                self.comm.set_muted(not self.comm.is_muted(pid), pid)
                return {"ok": True, "muted": self.comm.is_muted(pid)}
            if action == "toggle_co_alarm" and hasattr(self.comm, "set_co_alarm"):
                self.comm.set_co_alarm(not self.comm.is_co_alarm())
                return {"ok": True, "co_alarm": self.comm.is_co_alarm()}
            if action == "toggle_share" and hasattr(self.comm, "toggle_share"):
                self.comm.toggle_share()
                return {"ok": True, "share": bool(getattr(self.comm, "share", {}).get("on"))}
            if action == "clear_chat" and hasattr(self.comm, "clear_chat"):
                self.comm.clear_chat()
                return {"ok": True}
            if action == "open_native_dialog":
                kind = str(params.get("kind") or "")
                if callable(self._open_native_dialog):
                    result = self._open_native_dialog(kind, params)
                    return {"ok": True, "kind": kind, "result": result}
                return {"ok": False, "error": "native dialog handler unavailable"}
            if action in ("respond_coread", "respond_share", "respond_joint", "respond_love"):
                accept = bool(params.get("accept"))
                if callable(self._open_native_dialog):
                    result = self._open_native_dialog(action, {"accept": accept})
                    return {"ok": True, "action": action, "accept": accept, "result": result}
                return {"ok": False, "error": "banner handler unavailable"}
            return {"ok": False, "error": f"unknown action: {action}"}
        return self._call_main(work)

    def handle_ui_media(self, qs: dict) -> dict:
        kind = str(qs.get("kind") or "")
        mid = str(qs.get("id") or "")

        def work():
            path = ""
            if kind == "upload":
                with self._lock:
                    info = self._uploads.get(mid)
                if info:
                    path = info.get("path", "")
            elif kind == "avatar" and hasattr(self.comm, "avatar_path"):
                path = str(self.comm.avatar_path() or "")
            elif kind == "voice" and hasattr(self.comm, "voice"):
                path = str(self.comm.voice.cached_path(mid) or "") if hasattr(self.comm.voice, "cached_path") else ""
            elif kind == "chat_image" and hasattr(self.comm, "chat_image_path"):
                path = str(self.comm.chat_image_path(os.path.basename(mid)) or "")
            if not path or not os.path.isfile(path):
                return {"ok": False, "error": "media not found"}
            mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
            with open(path, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
            return {"ok": True, "mime": mime, "data_uri": f"data:{mime};base64,{b64}"}
        return self._call_main(work)

    def handle_fallback_sync(self, body: dict) -> dict:
        """Stop Hook 兜底同步：仅当本轮确实未调用 oit_reply 时才写入纯文本。"""
        text = str(body.get("text") or "").strip()
        cid = str(body.get("conversation_id") or "").strip()
        if not text or text in ("✓", "✔", "ok", "OK", "已发送"):
            return {"ok": True, "skipped": True}

        def work():
            if not cid or not self.is_bound_conversation(cid):
                return {"ok": True, "skipped": "unbound_conversation"}
            pid = self._ensure_persona_for_conversation(cid)
            now_ts = _now_ms()
            with self._lock:
                recent = max(
                    self._last_reply_ms.get(cid, 0),
                    self._last_reply_ms.get(pid, 0),
                    self._last_reply_ms.get("*", 0),
                )
            if now_ts - recent < 60_000:
                return {"ok": True, "skipped": "already_replied_via_oit_reply"}
            if self.comm.messages and self.comm.messages[-1].get("role") == "ai" and self.comm.messages[-1].get("text") == text:
                return {"ok": True, "dedup": True}
            self.comm._add("ai", text)
            self.comm.save_chat()
            self.comm._changed()
            if hasattr(self.comm, "_push"):
                self.comm._push()
            return {"ok": True, "synced": True}
        return self._call_main(work)


def plugin_root_dir() -> str:
    """定位随仓库/程序附带的 antigravity_plugin 目录。"""
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "antigravity_plugin")


def _find_tool(name: str, extra_paths=()) -> str:
    found = shutil.which(name)
    if found:
        return found
    for path in extra_paths:
        if path and os.path.isfile(path):
            return path
    return ""


def _run_step(args: list, cwd: str, timeout: int = 300) -> tuple[bool, str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
            timeout=timeout, creationflags=flags,
        )
    except subprocess.TimeoutExpired:
        return False, f"命令超时: {' '.join(args[:3])}"
    except OSError as exc:
        return False, f"无法启动命令: {exc}"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    detail = "\n".join(x for x in (out, err) if x)
    return proc.returncode == 0, detail


def deploy_antigravity_plugin(data_dir: str, on_progress=None, plugin_dir: str = "",
                              runner=None) -> dict:
    """自动完成 Antigravity 插件的本地构建、校验、安装与数据目录绑定。

    绝不修改用户 ~/.gemini/config/config.json 强行开启 Sidecar；Sidecar 由用户在 Antigravity 面板手动开启。
    """
    report = on_progress or (lambda _msg: None)
    run = runner or _run_step
    pdir = plugin_dir or plugin_root_dir()
    if not os.path.isdir(pdir) or not os.path.isfile(os.path.join(pdir, "plugin.json")):
        return {"ok": False, "error": f"未找到 Antigravity 插件目录：{pdir}"}

    # 1. 绑定 OIT 数据目录，使 MCP Server / Hooks / Sidecar 免配环境变量即可找到 mcp_bridge.json
    if data_dir:
        report("正在绑定 OIT 数据目录…")
        try:
            with open(os.path.join(pdir, ".oit_data_dir"), "w", encoding="utf-8") as fh:
                fh.write(os.path.abspath(data_dir))
        except OSError as exc:
            return {"ok": False, "error": f"写入数据目录标记失败：{exc}"}

    # 2. 检测 Node / npm / agy
    local_app = os.environ.get("LOCALAPPDATA") or ""
    agy_exe = _find_tool("agy", [
        os.path.join(local_app, "agy", "bin", "agy.exe") if local_app else "",
        os.path.join(os.path.expanduser("~"), "AppData", "Local", "agy", "bin", "agy.exe"),
    ])
    npm_exe = _find_tool("npm.cmd" if os.name == "nt" else "npm", [_find_tool("npm")])
    node_exe = _find_tool("node")

    server_dir = os.path.join(pdir, "server")
    ui_dir = os.path.join(pdir, "ui")
    need_server = not os.path.isfile(os.path.join(server_dir, "dist", "index.js"))
    need_ui = not os.path.isfile(os.path.join(ui_dir, "dist", "index.html"))
    steps_done = ["已绑定 OIT 数据目录"]

    if need_server or need_ui:
        if not node_exe or not npm_exe:
            return {"ok": False, "error": "未检测到 Node.js / npm，请先安装 Node.js 20+ 后重试自动部署。"}
        if need_server:
            report("正在安装并构建 MCP Server（TypeScript）…")
            if not os.path.isdir(os.path.join(server_dir, "node_modules")):
                ok, out = run([npm_exe, "install", "--no-audit", "--no-fund"], server_dir, 300)
                if not ok:
                    return {"ok": False, "error": f"MCP Server 依赖安装失败：\n{out[-600:]}"}
            ok, out = run([npm_exe, "run", "build"], server_dir, 180)
            if not ok:
                return {"ok": False, "error": f"MCP Server 构建失败：\n{out[-600:]}"}
            steps_done.append("已构建 MCP Server")
        if need_ui:
            report("正在安装并构建 OIT Chat 侧栏 UI…")
            if not os.path.isdir(os.path.join(ui_dir, "node_modules")):
                ok, out = run([npm_exe, "install", "--no-audit", "--no-fund"], ui_dir, 300)
                if not ok:
                    return {"ok": False, "error": f"OIT Chat UI 依赖安装失败：\n{out[-600:]}"}
            ok, out = run([npm_exe, "run", "build"], ui_dir, 180)
            if not ok:
                return {"ok": False, "error": f"OIT Chat UI 构建失败：\n{out[-600:]}"}
            steps_done.append("已构建 OIT Chat 侧栏 UI")
    else:
        steps_done.append("MCP Server 与侧栏 UI 已就绪")

    # 3. 打包精简插件目录（排除 node_modules 与源码，避免 Windows Defender 锁文件导致 rename Access is denied）
    if not agy_exe:
        return {"ok": False, "error": "未检测到官方 agy 命令，请先安装 Antigravity 桌面版 / CLI 后重试。"}

    stage_root = tempfile.mkdtemp(prefix="oit_ag_plugin_")
    staged = os.path.join(stage_root, "oit")
    try:
        os.makedirs(staged, exist_ok=True)
        for fname in ("plugin.json", "mcp_config.json", "hooks.json", "README.md", ".oit_data_dir"):
            src = os.path.join(pdir, fname)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(staged, fname))
        for dname in ("rules", "agents", "scripts", "sidecars"):
            src = os.path.join(pdir, dname)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(staged, dname),
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for rel in (os.path.join("server", "dist", "index.js"), os.path.join("ui", "dist", "index.html")):
            src = os.path.join(pdir, rel)
            if os.path.isfile(src):
                dst = os.path.join(staged, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
        # 仅当单文件 bundle 尚未启用时才兜底保留 server/package.json，防止 ESM import 识别异常
        srv_pkg = os.path.join(staged, "server", "package.json")
        os.makedirs(os.path.dirname(srv_pkg), exist_ok=True)
        with open(srv_pkg, "w", encoding="utf-8") as fh:
            json.dump({"type": "module"}, fh)

        report("正在校验 Antigravity 插件…")
        ok, out = run([agy_exe, "plugin", "validate", staged], staged, 60)
        if not ok:
            return {"ok": False, "error": f"插件校验失败：\n{out[-600:]}"}
        steps_done.append("已通过 agy plugin validate 校验")

        report("正在安装插件到 Antigravity…")
        ok, out = run([agy_exe, "plugin", "install", staged], staged, 60)
        if not ok and ("Access is denied" in out or "moving the installed plugin" in out):
            # Windows 上 Antigravity 正在运行已安装的 oit 插件时，会锁住 plugins/oit 目录导致 rename 失败；
            # 此时改为原地更新已通过 agy plugin validate 的文件（node 已把 JS 读入内存，单文件覆盖不会冲突）。
            installed_dir = os.path.join(os.path.expanduser("~"), ".gemini", "config", "plugins", "oit")
            if os.path.isdir(installed_dir):
                try:
                    for root_d, _dirs, files in os.walk(staged):
                        rel_d = os.path.relpath(root_d, staged)
                        dst_d = installed_dir if rel_d == "." else os.path.join(installed_dir, rel_d)
                        os.makedirs(dst_d, exist_ok=True)
                        for fname in files:
                            shutil.copy2(os.path.join(root_d, fname), os.path.join(dst_d, fname))
                    ok = True
                    steps_done.append("检测到插件运行中，已原地更新 oit 插件（在 Antigravity MCP Servers 点 Restart 生效）")
                except OSError as exc:
                    return {"ok": False, "error": f"插件正在运行且原地更新失败（请先在 Antigravity 暂停/重启插件后重试）：\n{exc}"}
            else:
                time.sleep(0.8)
                ok, out = run([agy_exe, "plugin", "install", staged], staged, 60)
                if ok:
                    steps_done.append("已安装插件 oit 到 Antigravity")
        elif ok:
            steps_done.append("已安装插件 oit 到 Antigravity")
        if not ok:
            return {"ok": False, "error": f"插件安装失败：\n{out[-600:]}"}
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)

    return {"ok": True, "steps": steps_done, "plugin_dir": pdir}


def antigravity_help_text(en: bool = False) -> str:
    """精简版 Antigravity 桌面版接管帮助（以 OIT 为主聊天界面）。"""
    if en:
        return (
            "Antigravity Desktop Takeover Guide (Chatting Directly in OIT)\n\n"
            "1. Auto-Deploy in OIT\n"
            "   Click [Auto-Deploy] below. OIT automatically binds your data directory, builds & installs\n"
            "   the 'oit' plugin via `agy`, enables takeover, and starts the local Bridge.\n\n"
            "2. Enable Plugin & Background Mode in Antigravity\n"
            "   • Open Customizations → Installed → [OIT Companion], enable it and approve permissions\n"
            "     (this starts the OIT Proactive Bridge Sidecar automatically; idle = 0 tokens).\n"
            "   • Tip: Open Settings → Application and turn on [Keep In Menu Bar] so Antigravity keeps\n"
            "     running in the background even after you close its window.\n"
            "   • Recommended: Turn off system alerts in Settings → Notifications to avoid duplicate pings.\n\n"
            "3. Bind Persona 1-to-1 (Once per Persona)\n"
            "   • Start a new conversation in Antigravity and send: 'Bind <persona name>'.\n"
            "   • Unbound conversations stay completely isolated for normal work. Clearing chat in OIT\n"
            "     unbinds the old conversation and opens a fresh one on your next message.\n\n"
            "4. Chat Directly in OIT\n"
            "   • Stay in OIT's native Chat window for all features (staged bubbles, interrupt-to-cancel,\n"
            "     voice, Joint / Share Data / CoRead / Shift / MIDI Review, proactive messages, Android sync).\n"
            "   • Switching model & thinking effort: Change them directly inside that persona's bound\n"
            "     conversation in Antigravity.\n"
            "   • Optional: Assign an external HTTP API to any subagent role in Settings → Models; otherwise\n"
            "     native Antigravity subagents are used.\n"
            "   • After updating OIT, click [Auto-Deploy] again and Restart 'oit' under MCP Servers."
        )
    return (
        "Antigravity 桌面版接管指南（以 OIT 为主聊天界面）\n\n"
        "1. 一键自动部署\n"
        "   点击下方「自动部署」，OIT 会自动绑定数据目录、构建并安装 oit 插件、开启接管与本地 Bridge。\n\n"
        "2. 在 Antigravity 开启插件与后台常驻\n"
        "   • 进入「Customizations → Installed → OIT Companion」启用插件并确认权限\n"
        "     （自动启动 OIT Proactive Bridge 后台桥接，空闲 0 token 消耗）。\n"
        "   • 提示：可在 Antigravity 的「Settings → Application」页面打开「Keep In Menu Bar」，\n"
        "     这样关闭 Antigravity 窗口后也会在后台持续运行。\n"
        "   • 建议在「Settings → Notifications」关闭系统通知，避免与 OIT 重复提醒。\n\n"
        "3. 人设 1 对 1 绑定（每人设只需一次）\n"
        "   • 在 Antigravity 新建对话并发送「绑定 <人设名>」即可建立 1 对 1 绑定。\n"
        "   • 未绑定的普通对话与 OIT 完全隔离；在 OIT 点「清空」会自动解绑旧会话，下次发消息自动建新会话。\n\n"
        "4. 回到 OIT 原生界面正常聊天\n"
        "   • 日常收发消息、分条延时、插话撤销、语音、联合行动 / Share Data / 共读 / 起点平移 / 演奏评判\n"
        "     与手机同步全部留在 OIT 沟通页操作即可。\n"
        "   • 切换模型与思考强度：需要在 Antigravity 中进入该人设已绑定的对话进行操作。\n"
        "   • 可选：在「设置 → 模型」给单个子代理指定外部 HTTP API，未指定则默认走 Antigravity 原生子代理。\n"
        "   • 更新 OIT 后如插件未生效，重点「自动部署」并在 MCP Servers 中 Restart oit。"
    )


def takeover_feature_prompt(feature: str, en: bool = False) -> str:
    """在 Antigravity 临时对话中用于绑定单次功能的快捷提示词。"""
    if feature == "suggest":
        if en:
            return (
                "Bind the OIT Rule List Suggestion feature for this temporary conversation "
                "(do not bind any persona). Please call oit_query(modules=[\"rules\"]) to inspect my "
                "current blacklist/whitelist, then suggest websites/apps to add or adjust based on my goals."
            )
        return (
            "为当前临时对话绑定 OIT 规则页「名单推荐」功能（不要绑定人设）。"
            "请先调用 oit_query(modules=[\"rules\"]) 读取我当前的黑白名单，再根据我接下来描述的需求给出增删建议。"
        )
    if en:
        return (
            "Bind the OIT Persona Authoring feature for this temporary conversation "
            "(do not bind any persona). First call oit_claudia_reference to decrypt and read all existing "
            "Claudia reference files; note that language style and most settings (such as scenario-mode "
            "action restrictions) do NOT need to match Claudia—especially the example file, which should "
            "fit the new character rather than copy Claudia. Then ask me whether I want 'Brief' or 'Detailed' "
            "mode (Detailed: as comprehensive as Claudia's files; Brief: only define the core persona without "
            "detailed scenario rules, and keep the example file casual/lightweight) before drafting."
        )
    return (
        "为当前临时对话绑定 OIT「人设编写」功能（不要绑定人设）。"
        "请先调用 oit_claudia_reference 解密并读取目前存在的所有 Claudia 参考文件（注意：语言风格和大部分设定"
        "不必与 Claudia 相同，例如情景模式的动作限制等，特别是 example 文件应贴合新角色本身而不要照搬 Claudia）；"
        "然后先询问我需要「简略」还是「详细」模式（详细：和 Claudia 人设一样面面俱到；"
        "简略：不详细规定情景，只规定基础人设，example 文件随便写写），等我选定后再按 OIT 格式开始编写。"
    )


def takeover_feature_help_text(feature: str, en: bool = False) -> str:
    """Antigravity 接管模式下规则名单推荐 / 人设编写的临时绑定使用帮助。"""
    is_suggest = feature == "suggest"
    if en:
        name = "Rule List Suggestion" if is_suggest else "Persona Authoring"
        return (
            f"Using [{name}] under Antigravity Desktop Takeover\n\n"
            f"When Antigravity Desktop Takeover is enabled, OIT does not call external CLIs directly,\n"
            f"so the built-in {name} popup is unavailable. Use a temporary Antigravity conversation instead:\n\n"
            "1. Create a temporary conversation in Antigravity Desktop\n"
            "   • Do NOT reuse a persona-bound conversation.\n"
            "   • Click [Copy Prompt] below and paste it into the new conversation to bind this feature.\n\n"
            "2. Finish your task\n"
            + (
                "   • Describe what sites/apps you want to block or allow, review the suggestions, and add them in OIT Rules.\n\n"
                if is_suggest else
                "   • The AI will decrypt and reference all existing Claudia files, then ask whether you want 'Brief' or 'Detailed' mode before drafting.\n"
                "   • Tip: You can also reference external websites for character info (e.g. Moegirlpedia).\n\n"
            )
            + "3. Delete the temporary conversation when done\n"
            "   • Once finished, simply delete that temporary conversation in Antigravity to keep your workspace clean.\n\n"
            "Tip: If you prefer OIT's built-in popup, temporarily disable Antigravity takeover in Settings → Models\n"
            "and select a standard external HTTP API model."
        )
    name = "规则页名单推荐" if is_suggest else "人设编写"
    return (
        f"Antigravity 接管模式下使用「{name}」\n\n"
        f"开启「由 Antigravity 桌面版接管」后，OIT 不再直接调用外部 CLI，原内置「{name}」弹窗不可用。\n"
        "请改用 Antigravity 临时对话完成，用完即删：\n\n"
        "1. 在 Antigravity 新建一个临时对话并绑定此功能\n"
        "   • 不要在已绑定人设的聊天对话里操作，避免污染人设上下文；\n"
        "   • 点击下方「复制提示词」，粘贴发送到新对话即可临时绑定该功能。\n\n"
        "2. 完成操作\n"
        + (
            "   • 告诉模型你的专注目标或想拦截/放行的网站与应用，确认建议后在 OIT 规则页添加即可。\n\n"
            if is_suggest else
            "   • 模型会先解密并参考目前存在的所有 Claudia 文件，并询问你需要「简略」还是「详细」模式，确认草稿后导入 OIT 人设文件夹即可。\n"
            "   • 提示：编写人设时可以引用外部网站进行参考（比如萌娘百科）。\n\n"
        )
        + "3. 用完删除临时对话\n"
        "   • 完成后直接在 Antigravity 中删除这个临时对话，保持会话列表整洁。\n\n"
        "提示：如果仍想直接使用 OIT 原生弹窗，可在「设置 → 模型」暂时关闭 Antigravity 接管并配置常规外部 HTTP API。"
    )

