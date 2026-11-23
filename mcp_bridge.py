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
import sys
import threading
import time
import urllib.parse
from datetime import datetime

import config
import music_library


BRIDGE_FILE = "mcp_bridge.json"
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


def _now_ms() -> int:
    return int(time.time() * 1000)


def _bubble_delay_ms(comm_obj, text: str, index: int) -> int:
    """按 OIT 字数节奏计算气泡延时（毫秒）；首条立即可见，后续气泡带自然停顿。"""
    if index <= 0:
        return 0
    try:
        from comm import char_delay
        sec = float(char_delay(text))
    except Exception:
        sec = max(0.8, len(str(text or "")) * 0.12)
    return max(600, min(8000, int(sec * 1000)))


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
        self._running = False

    @property
    def discovery_path(self) -> str:
        return os.path.join(self.data_dir, BRIDGE_FILE)

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
                    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
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
                        elif path == "/turn/context":
                            res = bridge.handle_turn_context(qs)
                        elif path == "/ui/state":
                            res = bridge.handle_ui_state(qs)
                        elif path == "/ui/media":
                            res = bridge.handle_ui_media(qs)
                        elif path == "/persona/active":
                            res = bridge.handle_persona_active(qs)
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
                os.remove(self.discovery_path)
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
        key = cid or persona_id or "*"
        entry = {
            "persona_id": persona_id,
            "conversation_id": cid,
            "kind": kind,
            "body": str(body or ""),
            "display_time": display_time,
            "ts": _now_ms(),
            "extra": dict(extra or {}),
        }
        with self._lock:
            self._pending_triggers[key] = entry
        event = {
            "event": "proactive",
            "persona_id": persona_id,
            "conversation_id": cid,
            "kind": kind,
            "display_time": display_time,
            "ts": entry["ts"],
        }
        self._emit_sse("proactive", event)
        return event

    def _sessions_map(self) -> dict:
        sessions = self.comm.cfg.setdefault("mcp_sessions", {})
        if not isinstance(sessions, dict):
            sessions = self.comm.cfg["mcp_sessions"] = {}
        return sessions

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
                "persona_name": self.comm.persona_name() if hasattr(self.comm, "persona_name") else "",
                "hard_block": bool(self.comm.hard_block()) if callable(getattr(self.comm, "hard_block", None)) else False,
            }
        return self._call_main(work)

    def handle_session_bind(self, body: dict) -> dict:
        cid = str(body.get("conversation_id") or "").strip()
        if not cid:
            return {"ok": False, "error": "conversation_id required"}
        host = str(body.get("host") or "antigravity").strip()

        def work():
            pid = str(body.get("persona_id") or getattr(self.comm, "pid", "") or "").strip()
            sessions = self._sessions_map()
            sessions[cid] = {"persona_id": pid, "host": host, "ts": _now_ms()}
            if hasattr(self.comm, "save_cfg"):
                self.comm.save_cfg()
            return {
                "ok": True,
                "conversation_id": cid,
                "persona_id": pid,
                "persona_name": self.comm.persona_name() if hasattr(self.comm, "persona_name") else "",
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
                "persona_name": name,
                "system_prompt": system,
                "memory_block": mem,
            }
        return self._call_main(work)

    def handle_turn_context(self, qs: dict) -> dict:
        cid = str(qs.get("conversation_id") or "").strip()
        consume = str(qs.get("consume_trigger") or "1") not in ("0", "false", "False")
        host = str(qs.get("host") or "").strip()
        last_input = str(qs.get("last_user_input") or "")

        def work():
            pid = self._persona_for_conversation(cid)
            sess_meta = self._sessions_map().get(cid) if cid else None
            eff_host = host or (sess_meta.get("host") if isinstance(sess_meta, dict) else "") or "antigravity"
            now = self.comm.now() if callable(getattr(self.comm, "now", None)) else datetime.now()
            snap = self.comm.snapshot() if callable(getattr(self.comm, "snapshot", None)) else {}
            status = self.comm.other_status(now) if hasattr(self.comm, "other_status") else {}
            state_txt = self.comm.state_text() if hasattr(self.comm, "state_text") else ""
            inter_note = self.comm.interaction_note(status, pid) if hasattr(self.comm, "interaction_note") else ""
            rt_note = ""
            if getattr(self.comm, "_rt", None) and hasattr(self.comm, "_rt_note") and not status.get("joint"):
                rt_note = self.comm._rt_note(now)

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
            lines = [f"[OIT动态状态] 时间: {now:%Y-%m-%d %H:%M} | 人设: {self.comm.persona_name()}"]
            if snap:
                lines.append(f"用户当前状态: {snap.get('state') or snap.get('kind') or 'unknown'}")
            if state_txt:
                lines.append(state_txt.strip())
            if inter_note:
                lines.append(inter_note.strip())
            if rt_note:
                lines.append(rt_note.strip())
            if interrupted and interrupted.get("count"):
                lines.append(
                    f"[OIT插话撤销] 上一轮回复有 {interrupted['count']} 条待发气泡尚未弹出即被用户新消息打断并作废，请勿假设用户看到了未发出的内容。"
                )
            if trigger and trigger.get("body"):
                lines.append(f"[OIT主动触发] {trigger['body']}")

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

            route_summary = ", ".join(f"{r}={v['mode']}" for r, v in routes.items())
            lines.append(f"[OIT子代理路由] {route_summary}")

            return {
                "ok": True,
                "conversation_id": cid,
                "persona_id": pid,
                "now": now.isoformat(timespec="seconds"),
                "snapshot": snap,
                "status": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in (status or {}).items()},
                "trigger": trigger,
                "interrupted": interrupted,
                "subagent_routes": routes,
                "system_message": "\n".join(x for x in lines if x),
            }
        return self._call_main(work)

    def handle_tool_execute(self, body: dict) -> dict:
        name = str(body.get("name") or "").strip()
        args = body.get("arguments") if isinstance(body.get("arguments"), dict) else {}
        cid = str(body.get("conversation_id") or "").strip()
        host = str(body.get("host") or "antigravity").strip()

        def work():
            if not name:
                return {"ok": False, "error": "tool name required"}
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

            if name == "oit_subject":
                routes = self._subagent_routes(host)
                route = routes.get("subject", {})
                if route.get("mode") != "external_api":
                    return {
                        "ok": False,
                        "route": route.get("mode", "host_subagent"),
                        "target": route.get("agent", "oit-subject"),
                        "instruction": "当前未为学科子代理单独配置外部 API，请改用宿主原生子代理 oit-subject（或按客观步骤直接推导）并风格化回复。",
                    }
                domain = str(args.get("domain") or "math")
                session = str(args.get("session") or "default")
                question = str(args.get("question") or "").strip()
                answer = self.comm.subagent_call(
                    "subject", session, f"学科: {domain}\n题目/追问:\n{question}",
                    max_tokens=900, temperature=0.15, timeout=180)
                return {"ok": True, "domain": domain, "session": session, "answer": answer}

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
            cid = str(body.get("conversation_id") or "").strip()
            pid = self._persona_for_conversation(cid)
            if body.get("silent"):
                return {"ok": True, "silent": True, "batch_id": "", "items": []}
            if body.get("refuse"):
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

            batch_id = f"b_{_now_ms()}_{secrets.token_hex(4)}"
            items = []
            for idx, part in enumerate(cleaned):
                delay_ms = _bubble_delay_ms(self.comm, part, idx)
                m = self.comm._add("ai", part)
                m["pending_reply"] = batch_id
                m["hold"] = True
                m["due"] = _now_ms() + delay_ms
                items.append({
                    "id": str(m.get("uid") or m.get("id") or f"{batch_id}_{idx}"),
                    "text": part,
                    "delay_ms": delay_ms,
                })

            # 处理常用即时控制字段
            if body.get("wait_minutes") and items:
                self.comm.messages[-1]["wait"] = float(body["wait_minutes"])
            if body.get("cut_minutes"):
                mins = max(1.0, min(1440.0, float(body["cut_minutes"])))
                self.comm.cfg["cut_until"] = _now_ms() + int(mins * 60 * 1000)
                self.comm.save_cfg()

            with self._lock:
                self._last_batch[pid] = {"batch_id": batch_id, "items": items}
            self.comm.save_chat()
            self.comm._changed()
            return {
                "ok": True,
                "batch_id": batch_id,
                "persona_id": pid,
                "persona_name": persona,
                "items": items,
            }
        return self._call_main(work)

    def handle_ui_state(self, qs: dict) -> dict:
        def work():
            pid = str(qs.get("persona_id") or getattr(self.comm, "pid", "") or "")
            cursor = int(qs.get("cursor") or 0)
            msgs = []
            pending = []
            for idx, m in enumerate(self.comm.messages):
                if m.get("role") not in ("me", "ai"):
                    continue
                item = {
                    "idx": idx,
                    "id": str(m.get("uid") or m.get("id") or idx),
                    "role": m.get("role"),
                    "text": str(m.get("text") or ""),
                    "ts": int(m.get("ts") or 0),
                    "has_voice": bool(m.get("voice")),
                }
                if m.get("hold") or m.get("pending_reply"):
                    item["delay_ms"] = max(0, int(m.get("due") or 0) - _now_ms())
                    pending.append(item)
                elif idx >= cursor:
                    msgs.append(item)

            banners = []
            if getattr(self.comm, "coread_ask", None):
                banners.append({"kind": "coread", "label": "对方邀请你共同阅读"})
            if getattr(self.comm, "share_ask", None):
                banners.append({"kind": "share", "label": "对方请求查看你的数据"})
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
                "persona_name": self.comm.persona_name() if hasattr(self.comm, "persona_name") else "",
                "cursor": len(self.comm.messages),
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
            found = False
            for m in self.comm.messages:
                if str(m.get("uid") or m.get("id") or "") == msg_id:
                    m.pop("hold", None)
                    m.pop("pending_reply", None)
                    m.pop("due", None)
                    found = True
                    break
            if not found:
                return {"ok": False, "error": "message not found"}
            self.comm.save_chat()
            self.comm._changed()
            if hasattr(self.comm, "_push"):
                self.comm._push()
            if hasattr(self.comm, "_ping"):
                self.comm._ping()
            return {"ok": True, "msg_id": msg_id}
        return self._call_main(work)

    def handle_ui_interrupt(self, body: dict) -> dict:
        user_text = str(body.get("user_text") or "").strip()

        def work():
            pid = str(body.get("persona_id") or getattr(self.comm, "pid", "") or "")
            doomed = [m for m in self.comm.messages if m.get("pending_reply") or m.get("hold")]
            if doomed:
                doomed_ids = {id(m) for m in doomed}
                self.comm.messages[:] = [m for m in self.comm.messages if id(m) not in doomed_ids]
                with self._lock:
                    prev = self._interrupted.get(pid, {}).get("count", 0)
                    self._interrupted[pid] = {"count": prev + len(doomed), "ts": _now_ms()}
            if user_text:
                self.comm._add("me", user_text)
            self.comm.save_chat()
            self.comm._changed()
            if hasattr(self.comm, "_push"):
                self.comm._push()
            return {"ok": True, "cancelled": len(doomed)}
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
            if not path or not os.path.isfile(path):
                return {"ok": False, "error": "media not found"}
            mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
            with open(path, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
            return {"ok": True, "mime": mime, "data_uri": f"data:{mime};base64,{b64}"}
        return self._call_main(work)

    def handle_fallback_sync(self, body: dict) -> dict:
        """Stop Hook 兜底同步：模型未调用 oit_reply 而直接输出纯文本时写入 OIT。"""
        text = str(body.get("text") or "").strip()
        if not text:
            return {"ok": True, "skipped": True}

        def work():
            if self.comm.messages and self.comm.messages[-1].get("role") == "ai" and self.comm.messages[-1].get("text") == text:
                return {"ok": True, "dedup": True}
            self.comm._add("ai", text)
            self.comm.save_chat()
            self.comm._changed()
            if hasattr(self.comm, "_push"):
                self.comm._push()
            return {"ok": True, "synced": True}
        return self._call_main(work)
