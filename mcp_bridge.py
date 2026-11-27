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
BRIDGE_HANDOFF_MSGS = 48
BRIDGE_HANDOFF_CHARS = 16000
STATELESS_TURN_TTL_MS = 2 * 3600 * 1000
ROLLOVER_MAX_TURNS = 40
ROLLOVER_NEW_DAY_TURNS = 12
ROLLOVER_COMPACT_MIN_TURNS = 10

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
    from comm.comm_interaction import (
        interaction_valence_baseline, offenses_active, offenses_rate, burst_consent_status_fields,
    )
    from comm.comm_persona import read_persona_rules
    from comm.comm_rules import (
        tension_rules, tension_relationship_rules, valence_rules, valence_burst_rules, burst_consent_rules,
    )

    d = comm_obj._interaction_prepare(pid, status)
    special = comm_obj._interaction_special(status) if hasattr(comm_obj, "_interaction_special") else False
    eff_special = bool(special or d["burst_until"])
    total = comm_obj.msg_total() if hasattr(comm_obj, "msg_total") else 0
    mode = "情景模拟" if d["scenario_mode"] and total >= config.COMM_SCENARIO_UNLOCK else "软件聊天"
    effective = d["tension"] if eff_special and d["burst_until"] else min(d["tension"], config.COMM_TENSION_CAP)
    stage = "0-50" if effective < config.COMM_TENSION_LOW else "50-90" if effective <= config.COMM_TENSION_CAP else "90+"

    persona_text = read_persona_rules(comm_obj.personas, comm_obj.persona_name())
    consent_tag, consent_note = burst_consent_status_fields(d, burst_consent_rules(persona_text))

    user_adult = bool(comm_obj.user_adult()) if hasattr(comm_obj, "user_adult") else bool(comm_obj.cfg.get("user_adult"))
    status_line = (
        f"[OIT_STATUS: MODE={mode}, USER_ADULT={'YES' if user_adult else 'UNKNOWN'}, TENSION={d['tension']}, EFFECTIVE_STAGE={stage}, "
        f"VALENCE={d['valence']}, INITIAL_VALENCE={interaction_valence_baseline(d)}, "
        f"OFFENSE_COUNT={len(offenses_active(d))}, VALENCE_RISE_RATE={offenses_rate(d):g}, "
        f"BURST={'YES' if d['burst_until'] else 'NO'}, PERIOD={'特殊空闲' if eff_special else '日常'}, "
        f"SHAME_AFTERMATH={'YES' if d['shame_turns'] else 'NO'}{consent_tag}]"
    )
    lines = [status_line]
    if consent_note:
        lines.append(consent_note)
    if mode == "软件聊天" or not eff_special or stage == "0-50":
        lines.append("规则: 禁动作描写，勿透露机制。")
    else:
        lines.append("规则: 情景模拟虚构动作须守边界，括号仅写动作（想法直说），勿透露机制。")
    if d["burst_until"]:
        lines.append("爆发中: 持续至当前特殊空闲结束（结束后自动降为20并进2轮冷静期）。")
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

    persona_text = read_persona_rules(comm_obj.personas, comm_obj.persona_name())
    rules = tension_rules(persona_text)
    rel_rules = tension_relationship_rules(persona_text)
    is_related = bool(comm_obj.related()) if hasattr(comm_obj, "related") else False
    active_rules = {k: v for k, v in rules.items() if not (k in rel_rules and rel_rules[k][0] and not is_related)}
    e_rules = valence_rules(persona_text)
    burst_rules = valence_burst_rules(persona_text)
    comm_obj._interaction_rules = active_rules
    comm_obj._interaction_relation_rules = rel_rules
    comm_obj._interaction_valence_rules = e_rules
    comm_obj._interaction_burst_rules = burst_rules

    if active_rules or e_rules:
        lines.append(
            f"[OIT情绪判断] 按条件为待评消息填 oit_reply.tension=[{{index,condition,gain}}](0~{config.COMM_TENSION_GAIN_MAX})、"
            f"valence=[{{index,condition,shift}}](-40~40)及offenses，普通交流省略；报原始整数，系统结算倍率与冷却（X为不定量，不返回X）。"
        )
        if active_rules:
            lines.append("[tension] " + " | ".join(
                f"{k}:[{g if g is not None else 'X'}] {desc}" for k, (g, desc) in active_rules.items()
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


def _recent_history_block(comm_obj, max_msgs: int = BRIDGE_HANDOFF_MSGS,
                          max_chars: int = BRIDGE_HANDOFF_CHARS) -> str:
    """提取最近 N 条完整对话记录（含桌面端、手机端与关键系统事件），作为 OIT 单一真相源注入无状态单轮上下文。"""
    msgs = getattr(comm_obj, "messages", None)
    if not isinstance(msgs, list) or not msgs:
        return ""
    pname = _display_persona_name(comm_obj) or "对方"
    selected = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("me", "ai", "sys"):
            continue
        if m.get("hold") or "pending_reply" in m:
            continue
        text = str(m.get("text") or "").strip()
        if role == "sys":
            # 跳过纯日程主动触发/重试模板提示，保留联合行动、网络、共读、切断、共享数据等真实事件记录
            if not text:
                continue
            if text.startswith("[系统] 按你的日程") or "你刚从专注 / 睡眠里出来" in text:
                continue
            if len(text) > 400:
                text = text[:300] + "…" + text[-80:]
        else:
            img_tag = " [附带图片]" if m.get("img") else ""
            if not text and not img_tag:
                continue
            if len(text) > 800:
                text = text[:580] + "…" + text[-180:]
            text = (text + img_tag).strip()
        at = str(m.get("at") or "")
        stamp = at[5:16].replace("T", " ") if len(at) >= 16 else ""
        who = "用户" if role == "me" else (pname if role == "ai" else "系统事件")
        line = f"[{stamp}] {who}: {text}" if stamp else f"{who}: {text}"
        selected.append(line)
    if not selected:
        return ""
    selected = selected[-max(1, int(max_msgs)):]
    kept = []
    total = 0
    for line in reversed(selected):
        cost = len(line) + 1
        if kept and total + cost > max_chars:
            break
        kept.append(line)
        total += cost
    kept.reverse()
    return "\n".join(kept)


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
        self._turns = {}                # turn_id -> dict (无状态单轮请求上下文，类 API 调用模式)
        self._interrupted = {}          # persona_id -> {"count": int, "ts": int}
        self._uploads = {}              # upload_id -> {"path": str, "mime": str, "name": str}
        self._recent_user_ms = {}       # conversation_id -> int
        self._last_batch = {}           # persona_id -> {"batch_id": str, "items": list}
        self._last_reply_ms = {}        # conversation_id/persona_id -> int
        self._turn_interaction = {}     # persona_id -> {"shame": bool}
        self._share_sent_ts = {}        # persona_id -> int (已下发给 Antigravity 的最新共享数据 sys 消息 ts)
        self._answer_image_context = {} # (conversation_id, subject session) -> original answer and source image
        self._pending_answer_images = {} # conversation_id -> rendered bytes awaiting oit_reply
        self._bootstrapped_cids = set() # conversation_id -> 已注入过人设/记忆卡/近期历史
        self._delta_state = {}          # conversation_id -> dict (上一轮已消费的静态/半静态状态快照)
        self._pending_compact = set()   # persona_id -> 轮换后待整理长期记忆
        self._last_bind_ms = {}         # conversation_id -> int (刚绑定时间，防 fallback_sync 误写绑定确认语)
        self._rollover_hinted = set()   # conversation_id -> 已在直接聊天中提醒过一次轮换
        self.rollover_max_turns = ROLLOVER_MAX_TURNS
        self.rollover_new_day_turns = ROLLOVER_NEW_DAY_TURNS
        self.rollover_compact_min_turns = ROLLOVER_COMPACT_MIN_TURNS
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

    def _should_rollover(self, cid: str, now: datetime) -> bool:
        if not cid:
            return False
        meta = self._sessions_map().get(cid)
        if not isinstance(meta, dict) or not meta.get("persona_id"):
            return False
        turns = int(meta.get("turns") or 0)
        if turns >= max(1, int(self.rollover_max_turns)):
            return True
        created_day = str(meta.get("created_day") or "")
        today = f"{now:%Y-%m-%d}"
        if created_day and created_day != today and turns >= max(1, int(self.rollover_new_day_turns)):
            return True
        return False

    def _prepare_rollover(self, persona_id: str, now: datetime, force: bool = False) -> dict:
        """在不删除聊天记录的前提下解绑旧对话并准备轮换到新对话；返回轮换信息。"""
        pid = str(persona_id or getattr(self.comm, "pid", "") or "").strip()
        cid = self._conversation_for_persona(pid)
        if not cid:
            return {"rolled_over": False, "old_cid": "", "compact_memory": False, "turns": 0}
        if not force and not self._should_rollover(cid, now):
            return {"rolled_over": False, "old_cid": "", "compact_memory": False, "turns": 0}
        sessions = self._sessions_map()
        meta = sessions.pop(cid, None) if isinstance(sessions.get(cid), dict) else {}
        turns = int((meta or {}).get("turns") or 0)
        need_compact = turns >= max(1, int(self.rollover_compact_min_turns))
        with self._lock:
            self._bootstrapped_cids.discard(cid)
            self._delta_state.pop(cid, None)
            self._last_bind_ms.pop(cid, None)
            self._rollover_hinted.discard(cid)
            self._last_reply_ms.pop(cid, None)
            self._recent_user_ms.pop(cid, None)
            old_trig = self._pending_triggers.pop(cid, None)
            if old_trig and pid:
                old_trig["conversation_id"] = ""
                self._pending_triggers[pid] = old_trig
            if need_compact and pid:
                self._pending_compact.add(pid)
        self._save_sessions()
        if hasattr(self.comm, "save_cfg"):
            self.comm.save_cfg()
        return {
            "rolled_over": True,
            "old_cid": cid,
            "compact_memory": need_compact,
            "turns": turns,
        }

    def rollover_persona(self, persona_id: str = "", force: bool = True) -> dict:
        """主动触发会话轮换：解绑旧会话，通知 Sidecar 归档旧会话并静默新建+绑定新会话。"""
        pid = str(persona_id or getattr(self.comm, "pid", "") or "").strip()
        now = self.comm.now() if callable(getattr(self.comm, "now", None)) else datetime.now()
        info = self._prepare_rollover(pid, now, force=force)
        if not info["rolled_over"]:
            return {"ok": True, "rolled_over": False, "persona_id": pid}
        pname = self._persona_name_by_pid(pid) or _display_persona_name(self.comm)
        payload = {
            "event": "rollover",
            "kind": "rollover",
            "persona_id": pid,
            "persona_name": pname,
            "archive_conversation_id": info["old_cid"],
            "compact_memory": info["compact_memory"],
            "turns": info["turns"],
            "ts": _now_ms(),
        }
        self._emit_sse("rollover", payload)
        return {"ok": True, **info, "persona_id": pid, "persona_name": pname}

    def _bump_session_turns(self, cid: str):
        if not cid:
            return
        sessions = self._sessions_map()
        meta = sessions.get(cid)
        if not isinstance(meta, dict) or not meta.get("persona_id"):
            return
        meta["turns"] = int(meta.get("turns") or 0) + 1
        if not meta.get("created_day"):
            now = self.comm.now() if callable(getattr(self.comm, "now", None)) else datetime.now()
            meta["created_day"] = f"{now:%Y-%m-%d}"
        self._save_sessions()

    def _unreplied_user_messages(self, persona_id: str):
        """收集指定人设在最后一条已展示 AI 消息之后、尚未收到回复的用户消息（含忙碌期间被延迟的消息）。"""
        msgs = []
        if persona_id and persona_id == getattr(self.comm, "pid", ""):
            msgs = list(getattr(self.comm, "messages", None) or [])
        elif persona_id and hasattr(self.comm, "_state_of"):
            try:
                st = self.comm._state_of(persona_id) or {}
                msgs = list(st.get("messages") or [])
            except Exception:
                msgs = []
        pending = []
        for m in reversed(msgs):
            if m.get("role") == "ai" and not m.get("hold") and "pending_reply" not in m:
                break
            if m.get("role") == "me":
                pending.append(m)
        if not pending:
            return "", []
        pending.reverse()
        lines, images = [], []
        for m in pending:
            txt = str(m.get("text") or "").strip()
            at_s = str(m.get("at") or "").replace("T", " ")[:16]
            img_name = str(m.get("img") or "").strip()
            img_file = (
                self.comm.chat_image_path(img_name)
                if img_name and hasattr(self.comm, "chat_image_path")
                else ""
            )
            if not (img_file and os.path.isfile(img_file)):
                img_file = ""
            if img_file:
                images.append(img_file)
            line_txt = (
                f"{txt} [图片文件: {img_file}]"
                if (img_file and txt)
                else (f"[图片文件: {img_file}]" if img_file else txt)
            )
            if line_txt:
                lines.append(f"[{at_s}] {line_txt}" if at_s else line_txt)
        return "\n".join(lines), images

    def _prune_turns(self):
        cutoff = _now_ms() - STATELESS_TURN_TTL_MS
        with self._lock:
            stale = [k for k, v in self._turns.items() if int(v.get("created_ms") or 0) < cutoff]
            for k in stale:
                self._turns.pop(k, None)

    def _active_model_hint(self) -> str:
        try:
            ai = getattr(self.comm, "ai", None)
            prof = (getattr(ai, "profiles", None) or {}).get("antigravity") or {}
            model = str(prof.get("model") or "").strip()
            if model and model not in ("订阅模型", "subscription", "auto"):
                return model
        except Exception:
            pass
        return ""

    def _ensure_persona_by_pid(self, pid: str) -> str:
        pid = str(pid or "").strip()
        if pid and pid != getattr(self.comm, "pid", "") and hasattr(self.comm, "set_persona"):
            name = self._persona_name_by_pid(pid)
            if name:
                self.comm.set_persona(name)
        return str(getattr(self.comm, "pid", "") or pid)

    def _make_turn(self, persona_id: str, kind: str, user_text: str = "",
                   api_context: str = "", held_messages: str = "",
                   image_path: str = "", trigger_body: str = "",
                   extra: dict = None) -> dict:
        self._prune_turns()
        now_ms = self.comm._ms() if hasattr(self.comm, "_ms") else _now_ms()
        turn_id = f"t_{now_ms}_{secrets.token_hex(4)}"
        pid = str(persona_id or getattr(self.comm, "pid", "") or "").strip()
        pname = self._persona_name_by_pid(pid) or _display_persona_name(self.comm)
        if pname:
            try:
                from comm.comm_persona import persona_title
                pname = str(persona_title(pname) or pname)
            except Exception:
                pname = os.path.splitext(pname)[0]
        turn = {
            "turn_id": turn_id,
            "persona_id": pid,
            "persona_name": pname,
            "kind": str(kind or "user_message"),
            "user_text": str(user_text or "").strip(),
            "api_context": str(api_context or "").strip(),
            "held_messages": str(held_messages or "").strip(),
            "image_path": str(image_path or "").strip(),
            "trigger_body": str(trigger_body or "").strip(),
            "extra": dict(extra or {}),
            "created_ms": now_ms,
            "clear_ts": int((getattr(self.comm, "cfg", None) or {}).get("clear_ts") or 0),
            "conversation_id": "",
        }
        with self._lock:
            self._turns[turn_id] = turn
        return turn

    # ── 主动触发与事件推送 ──
    def push_proactive(self, persona_id: str, body: str, kind: str = "proactive",
                       extra: dict = None) -> dict:
        """OIT 状态机命中主动对话/后台事件时调用：创建无状态单轮请求并推送 SSE 信号给 Sidecar。"""
        now = self.comm.now() if callable(getattr(self.comm, "now", None)) else datetime.now()
        display_time = f"{now:%H:%M}"
        cid = self._conversation_for_persona(persona_id)
        key = cid or persona_id or "*"
        entry_ts = self.comm._ms() if hasattr(self.comm, "_ms") else _now_ms()
        held_txt, held_imgs = self._unreplied_user_messages(persona_id)
        body_txt = str(body or "")
        if held_imgs:
            img_note = (
                "用户在上述未回复消息中发送了图片文件：\n" + "\n".join(held_imgs)
                + "\n请先使用 read_file 工具查看图片内容。"
            )
            body_txt = f"{body_txt}\n\n{img_note}".strip() if body_txt else img_note
        turn = self._make_turn(
            persona_id=persona_id,
            kind=kind,
            held_messages=held_txt,
            image_path=held_imgs[-1] if held_imgs else "",
            trigger_body=body_txt,
            extra=extra,
        )
        entry = {
            "persona_id": persona_id,
            "conversation_id": cid,
            "turn_id": turn["turn_id"],
            "kind": kind,
            "body": body_txt,
            "held_messages": held_txt,
            "delivered_via_sse": False,
            "display_time": display_time,
            "ts": entry_ts,
            "extra": dict(extra or {}),
        }
        with self._lock:
            self._pending_triggers[key] = entry
        event = {
            "event": "proactive",
            "stateless": True,
            "turn_id": turn["turn_id"],
            "persona_id": persona_id,
            "persona_name": turn["persona_name"] or _display_persona_name(self.comm),
            "conversation_id": cid,
            "kind": kind,
            "display_time": display_time,
            "model": self._active_model_hint(),
            "ts": entry["ts"],
        }
        self._emit_sse("proactive", event)
        return event

    def push_memory_compact(self, persona_id: str = "", mode: str = "compact") -> bool:
        """在 Antigravity 无状态模式下发起一次独立的记忆卡整理 / 睡眠期 Dreaming 单轮请求。"""
        with self._lock:
            has_client = bool(self._sse_clients)
        if not has_client:
            return False
        pid = str(persona_id or getattr(self.comm, "pid", "") or "").strip()
        m = "dream" if str(mode or "").strip().lower() == "dream" else "compact"
        turn = self._make_turn(persona_id=pid, kind="memory_compact", extra={"mode": m})
        payload = {
            "event": "memory_compact",
            "stateless": True,
            "turn_id": turn["turn_id"],
            "persona_id": pid,
            "persona_name": turn["persona_name"] or _display_persona_name(self.comm),
            "kind": "memory_compact",
            "mode": m,
            "model": self._active_model_hint(),
            "ts": turn["created_ms"],
        }
        self._emit_sse("memory_compact", payload)
        return True

    def unbind_persona(self, persona_id: str) -> list:
        """清空对话时解绑该人设的旧 Antigravity 会话并清理暂存状态。"""
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
                self._bootstrapped_cids.discard(key)
                self._delta_state.pop(key, None)
                self._last_bind_ms.pop(key, None)
                self._rollover_hinted.discard(key)
            for tid in [k for k, v in self._turns.items() if v.get("persona_id") == pid]:
                self._turns.pop(tid, None)
            self._pending_compact.discard(pid)
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
        """用户在 OIT 桌面端或同步手机端发消息时，创建无状态单轮请求并推送给 Sidecar；返回是否有活跃 Sidecar 接收。"""
        if not str(text or "").strip():
            return False
        cid = self._conversation_for_persona(persona_id)
        now_ms = self.comm._ms() if hasattr(self.comm, "_ms") else _now_ms()
        img_path = str(image_path or "").strip()
        with self._lock:
            has_client = bool(self._sse_clients)
            if cid:
                self._pending_answer_images.pop(cid, None)
                for key in [key for key in self._answer_image_context if key[0] == cid]:
                    self._answer_image_context.pop(key, None)
            body_txt = str(api_context or "").strip()
            if img_path and img_path not in body_txt:
                img_note = (
                    f"用户随本条消息发送了一张图片文件：{img_path}\n"
                    "请先使用 read_file 工具查看该图片内容。若涉及学科题目并需要调用 oit-subject 子代理，"
                    "必须将该图片完整路径一并写入子代理 Prompt，要求子代理亲自调用 read_file 查看原图，"
                    "不要仅转述文字；回复用户时保留清晰的关键推导过程。"
                )
                body_txt = f"{body_txt}\n\n{img_note}".strip() if body_txt else img_note
            held_txt = str(held_messages or "").strip()
            turn = self._make_turn(
                persona_id=persona_id,
                kind=str(trigger_kind or "user_message"),
                user_text=str(text).strip(),
                api_context=body_txt,
                held_messages=held_txt,
                image_path=img_path,
            )
            if body_txt or held_txt:
                key = cid or persona_id or "*"
                self._pending_triggers[key] = {
                    "persona_id": persona_id,
                    "conversation_id": cid,
                    "turn_id": turn["turn_id"],
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
        payload = {
            "event": "user_message",
            "stateless": True,
            "turn_id": turn["turn_id"],
            "persona_id": persona_id,
            "persona_name": turn["persona_name"] or _display_persona_name(self.comm),
            "conversation_id": cid,
            "kind": str(trigger_kind or "user_message"),
            "text": str(text).strip(),
            "image_path": img_path,
            "model": self._active_model_hint(),
            "ts": now_ms,
        }
        self._emit_sse("user_message", payload)
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
                now = self.comm.now() if callable(getattr(self.comm, "now", None)) else datetime.now()
                now_ts = _now_ms()
                sessions[cid] = {
                    "persona_id": pid,
                    "host": host,
                    "ts": now_ts,
                    "turns": 0,
                    "created_day": f"{now:%Y-%m-%d}",
                }
                with self._lock:
                    for old_cid in (*other_cids, cid):
                        self._bootstrapped_cids.discard(old_cid)
                        self._delta_state.pop(old_cid, None)
                        self._rollover_hinted.discard(old_cid)
                    self._last_bind_ms[cid] = now_ts
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
                "reply_required": False,
            }
        return self._call_main(work)

    def _persona_bootstrap_payload(self) -> dict:
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
        recent = _recent_history_block(self.comm)
        return {
            "persona_id": pid,
            "persona_name": _display_persona_name(self.comm),
            "system_prompt": system,
            "memory_block": mem,
            "recent_history": recent,
        }

    def _persona_bootstrap_text(self) -> str:
        info = self._persona_bootstrap_payload()
        parts = []
        if info.get("system_prompt"):
            parts.append(str(info["system_prompt"]))
        if info.get("memory_block"):
            parts.append("记忆卡:\n" + str(info["memory_block"]))
        if info.get("recent_history"):
            parts.append(
                "近期对话上下文（用于新会话无缝续接，请自然承接话题，不要向用户提及换了会话）:\n"
                + str(info["recent_history"])
            )
        return "\n\n".join(parts)

    def handle_persona_active(self, qs: dict) -> dict:
        cid = str(qs.get("conversation_id") or "").strip()

        def work():
            if cid and self.is_bound_conversation(cid):
                self._ensure_persona_for_conversation(cid)
                with self._lock:
                    self._bootstrapped_cids.add(cid)
            info = self._persona_bootstrap_payload()
            return {"ok": True, **info}
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
        turn_id = str(qs.get("turn_id") or "").strip()
        consume = str(qs.get("consume_trigger") or "1") not in ("0", "false", "False")
        only_bound = str(qs.get("only_bound") or "0") in ("1", "true", "True")
        delta = str(qs.get("delta") or "0") in ("1", "true", "True")
        bootstrap = str(qs.get("bootstrap") or "0") in ("1", "true", "True")
        host = str(qs.get("host") or "").strip()
        last_input = str(qs.get("last_user_input") or "")

        def work():
            turn = None
            if turn_id:
                with self._lock:
                    turn = self._turns.get(turn_id)
                    if turn and cid and not turn.get("conversation_id"):
                        turn["conversation_id"] = cid
            if only_bound and not turn and not self.is_bound_conversation(cid):
                return {
                    "ok": True,
                    "bound": False,
                    "conversation_id": cid,
                    "system_message": "",
                }
            if turn and turn.get("persona_id"):
                pid = self._ensure_persona_by_pid(turn["persona_id"])
            else:
                pid = self._ensure_persona_for_conversation(cid)
            sess_meta = self._sessions_map().get(cid) if cid else None
            eff_host = host or (sess_meta.get("host") if isinstance(sess_meta, dict) else "") or "antigravity"
            now = self.comm.now() if callable(getattr(self.comm, "now", None)) else datetime.now()
            status = self.comm.other_status(now) if hasattr(self.comm, "other_status") else {}

            trigger = None
            with self._lock:
                for key in (cid, pid, "*"):
                    if key and key in self._pending_triggers:
                        if turn and self._pending_triggers[key].get("turn_id") == turn_id:
                            trigger = self._pending_triggers.pop(key) if consume else dict(self._pending_triggers[key])
                        elif not turn:
                            trigger = self._pending_triggers.pop(key) if consume else dict(self._pending_triggers[key])
                        break
                interrupted = self._interrupted.pop(pid, None) if consume else self._interrupted.get(pid)
                if cid and not trigger and not turn:
                    self._recent_user_ms[cid] = _now_ms()

            routes = self._subagent_routes(eff_host)
            try:
                from comm.comm_util import WEEKDAY
                wd = WEEKDAY[now.weekday()]
            except Exception:
                wd = ""
            head = f"[OIT动态状态] {now:%Y-%m-%d} {wd} {now:%H:%M} | {_display_persona_name(self.comm)}".replace("  ", " ")
            lines = []
            # 无状态单轮模式（turn_id）：每轮始终下发完整人设、记忆卡、用户背景、联合行动说明与 OIT 完整近期历史（单一真相源）
            if turn:
                info = self._persona_bootstrap_payload()
                if info.get("system_prompt"):
                    lines.append(str(info["system_prompt"]))
                if info.get("memory_block"):
                    lines.append("记忆卡:\n" + str(info["memory_block"]))
                who = str(self.comm.cfg.get("user_profile") or "").strip()[:2000]
                if who:
                    lines.append("以下是用户的身份背景(用户自己填写):\n" + who)
                if hasattr(self.comm, "joint_system_note"):
                    jnote = str(self.comm.joint_system_note() or "").strip()
                    if jnote:
                        lines.append(jnote)
                if info.get("recent_history"):
                    lines.append(
                        "近期完整对话记录（来自 OIT，包含桌面端、手机端与系统事件，按时间顺序；请以此作为唯一对话上下文，绝不编造未发生的对话）:\n"
                        + str(info["recent_history"])
                    )
            else:
                # 兼容旧版绑定会话：尚未注入人设时在 delta/bootstrap 中一次性补齐
                need_bootstrap = bool(
                    (delta or bootstrap) and cid and self.is_bound_conversation(cid)
                    and cid not in self._bootstrapped_cids
                )
                if need_bootstrap:
                    boot_txt = self._persona_bootstrap_text()
                    if boot_txt:
                        lines.append(boot_txt)
                    if consume:
                        with self._lock:
                            self._bootstrapped_cids.add(cid)

            use_delta = bool(delta and consume and cid and not turn)
            prev_delta = dict(self._delta_state.get(cid) or {}) if use_delta else {}
            cur_delta = {}

            lines.append(head)
            sched_line = _compact_schedule_line(self.comm, now, status)
            cur_delta["schedule"] = sched_line
            if not use_delta or prev_delta.get("schedule") != sched_line:
                lines.append(sched_line)

            if hasattr(self.comm, "_interaction_prepare"):
                d_inter = self.comm._interaction_prepare(pid, status)
                with self._lock:
                    self._turn_interaction[pid] = {
                        "shame": bool(d_inter.get("shame_turns")),
                    }
                tk = (turn.get("kind") if turn else (trigger.get("kind") if trigger else "user"))
                judge_kind = (
                    "user"
                    if tk in ("user", "user_message", "user_attachment", "retry", "chat")
                    else "proactive"
                )
                inter_lines = _compact_interaction_lines(self.comm, pid, status, judge_kind)
                inter_sig = "\n".join(inter_lines)
                cur_delta["interaction"] = inter_sig
                if inter_lines and (not use_delta or prev_delta.get("interaction") != inter_sig):
                    lines.extend(inter_lines)
                if hasattr(self.comm, "realism_note"):
                    r_note = self.comm.realism_note(pid)
                    cur_delta["realism"] = r_note or ""
                    if r_note and (not use_delta or prev_delta.get("realism") != r_note):
                        lines.append(r_note)

            # 增量下发尚未送达的 share data 页面数据（role="sys"）
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

            if turn:
                tkind = turn.get("kind") or "user_message"
                if tkind == "memory_compact":
                    mc_mode = str((turn.get("extra") or {}).get("mode") or "compact")
                    if mc_mode == "dream":
                        lines.append(
                            "[OIT睡眠期记忆反思任务(Dreaming)] 请调用隔离子代理 oit-memory-compact，把「当前时间」「人设中的记忆保留规则」"
                            "「现有长期记忆摘要（mem_auto）」「当前实时事件（mem_live，带 [#E*] 编号）」与「近期完整对话记录」完整传给它，"
                            "要求它对照原始对话核验与修正长期记忆（核实带 ? 条目）、将已稳定的实时事件晋升至长期摘要、"
                            "并筛选可安全清理的已过期回访/已解决普通事件编号（未证实完成的说教目标绝不移除），"
                            "最后调用 oit_memory_save_auto(text=..., remove_live_ids=[...], turn_id="
                            f"{turn_id!r}) 保存。整理过程对用户完全静默，不要调用 oit_reply，完成后输出 ✓。"
                        )
                    else:
                        lines.append(
                            "[OIT记忆整理任务] 请调用隔离子代理 oit-memory-compact，把「当前时间」「人设中的记忆保留规则」"
                            "「现有长期记忆摘要（不含实时事件与系统记录）」与「近期完整对话记录」完整传给它，"
                            "由它整理为简洁准确的长期记忆并通过 oit_memory_save_auto(text=..., turn_id="
                            f"{turn_id!r}) 保存。不要把实时事件或【系统记录】抄入长期记忆摘要。"
                            "整理过程对用户完全静默，不要调用 oit_reply，完成后输出 ✓。"
                        )
                else:
                    if turn.get("held_messages"):
                        if tkind in ("retry", "user", "user_message", "user_attachment"):
                            lines.append(f"[OIT忙碌期间用户消息]\n{turn['held_messages']}")
                        else:
                            lines.append(
                                "[OIT未回复的用户消息]（对方此前忙碌/延迟未送达的消息，本轮主动谈话时请一并知悉并自然回应）\n"
                                f"{turn['held_messages']}"
                            )
                    elif turn.get("user_text"):
                        lines.append(f"[OIT本轮待回复的用户消息]\n{turn['user_text']}")
                    if turn.get("api_context"):
                        if tkind == "retry":
                            lines.append(f"[OIT延迟回复说明] {turn['api_context']}")
                        else:
                            lines.append(f"[OIT本轮附加请求]\n{turn['api_context']}")
                    if turn.get("trigger_body"):
                        if tkind == "retry":
                            lines.append(f"[OIT延迟回复说明] {turn['trigger_body']}")
                        else:
                            lines.append(f"[OIT主动触发] {turn['trigger_body']}")
                    if turn.get("image_path") and turn["image_path"] not in "\n".join(lines):
                        lines.append(
                            f"用户随本轮消息发送了图片文件：{turn['image_path']}\n"
                            "请先使用 read_file 工具查看图片内容；若涉及学科题目并调用 oit-subject 子代理，"
                            "必须将该图片完整路径一并写入子代理 Prompt。"
                        )
            elif trigger:
                if trigger.get("held_messages") and not trigger.get("delivered_via_sse"):
                    if trigger.get("kind") in ("retry", "user", "user_attachment"):
                        lines.append(f"[OIT忙碌期间用户消息]\n{trigger['held_messages']}")
                    else:
                        lines.append(
                            "[OIT未回复的用户消息]（对方此前忙碌/延迟未送达的消息，本轮主动谈话时请一并知悉并自然回应）\n"
                            f"{trigger['held_messages']}"
                        )
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
            mental_check_txt = last_input or (turn.get("user_text") or turn.get("held_messages") if turn else "")
            if mental_check_txt and hasattr(self.comm, "_mental_trigger"):
                try:
                    m_reason = self.comm._mental_trigger(mental_check_txt)
                except Exception:
                    m_reason = ""
                if m_reason:
                    m_route = routes.get("mental_health", {})
                    if m_route.get("mode") == "host_subagent":
                        lines.append(
                            "[OIT安全提示] 检测到心理困扰/风险信号。在调用 oit_reply 回复用户之前，"
                            "必须先调用子代理 oit-mental-health（传入本次用户原话），拿到内部评估后再按人设组织回复，"
                            "且绝不可向用户提及子代理。"
                        )

            non_default_routes = [
                f"{r}={v['mode']}" for r, v in routes.items() if v.get("mode") != "host_subagent"
            ]
            routes_line = f"[OIT子代理路由] {', '.join(non_default_routes)}" if non_default_routes else ""
            cur_delta["routes"] = routes_line
            if routes_line and (not use_delta or prev_delta.get("routes") != routes_line):
                lines.append(routes_line)

            answer_images = callable(getattr(self.comm, "answer_images_on", None)) and self.comm.answer_images_on()
            ans_img_line = f"[OIT解答图片模式] {'开启' if answer_images else '关闭'}。"
            cur_delta["answer_images"] = ans_img_line
            if not use_delta or prev_delta.get("answer_images") != ans_img_line:
                lines.append(ans_img_line)

            if turn_id:
                lines.append(f"[OIT单轮提交] 回复时请调用 oit_reply(turn_id={turn_id!r}, bubbles=[...])，完成后仅输出 ✓ 结束。")

            if consume and cid and not turn:
                with self._lock:
                    self._delta_state[cid] = cur_delta

            return {
                "ok": True,
                "stateless": bool(turn),
                "turn_id": turn_id,
                "conversation_id": cid,
                "persona_id": pid,
                "persona_name": _display_persona_name(self.comm),
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
        turn_id = str(body.get("turn_id") or args.get("turn_id") or "").strip()
        cid = str(body.get("conversation_id") or args.get("conversation_id") or
                  self._conversation_for_persona(getattr(self.comm, "pid", "")) or "").strip()
        scope_key = turn_id or cid
        host = str(body.get("host") or "antigravity").strip()

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

        # 以下工具包含耗时的外部网络请求 / 渲染，必须在 HTTP 工作线程执行，绝不能阻塞主线程 UI。
        if name == "oit_subject":
            def prep_subject():
                if cid:
                    self._ensure_persona_for_conversation(cid)
                if self.comm.hard_block():
                    return {"done": {"ok": False, "error": "当前处于禁止沟通状态"}}
                routes = self._subagent_routes(host)
                route = routes.get("subject", {})
                domain = str(args.get("domain") or "math")
                session = str(args.get("session") or "default")
                question = str(args.get("question") or "").strip()
                image = _latest_user_image()
                ctx = {"domain": domain, "session": session, "question": question,
                       "answer": "", "image": image, "ts": _now_ms()}
                self._answer_image_context[(scope_key, session)] = ctx
                if route.get("mode") != "external_api":
                    from knowledge import get_knowledge
                    from knowledge.kb_ai import mode_prompt, exclusions, generality_instruction
                    knowledge = get_knowledge(self.comm.dir)
                    return {"done": {
                        "ok": False,
                        "route": route.get("mode", "host_subagent"),
                        "target": route.get("agent", "oit-subject"),
                        "solver_mode": knowledge.config.get("solver_mode", "exploration"),
                        "mode_instruction": mode_prompt(knowledge),
                        "excluded_tactics": exclusions(knowledge),
                        "thought_reference": bool(knowledge.config.get("thought_reference", False)),
                        "generality_instruction": generality_instruction(knowledge),
                        "instruction": "当前未为解题子代理单独配置外部 API，请改用宿主原生子代理 oit-subject；知识库处理始终交给解题子代理，由它调用 oit_kb_* 工具。将结果按当前人设语气风格化改写；解答图片模式开启时，在 oit_reply 前调用 oit_answer_image 提交原始解答和人设风格化解答。",
                    }}
                return {"domain": domain, "session": session, "question": question, "image": image, "ctx": ctx}

            prep = self._call_main(prep_subject)
            if "done" in prep:
                return prep["done"]
            answer, citations = self.comm._knowledge_subject(
                prep["domain"], prep["session"], prep["question"], prep["image"])
            def finish_subject():
                prep["ctx"]["answer"] = answer
                prep["ctx"]["kb_citations"] = citations
                return {"ok": True, "domain": prep["domain"], "session": prep["session"],
                        "answer": answer, "kb_citations": citations,
                        "instruction": "核对 kb_citations 中技巧的适用前提；若需要将来源 ID 传给 oit_reply 或 oit_answer_image，请传入 kb_citations。若输出 <!-- kb_discovery -->，后台会检查已证明的库外技巧并尝试提交待审提案。不得提交符合 excluded_tactics 的技巧。"}
            return self._call_main(finish_subject)

        if name == "oit_answer_image":
            def prep_answer_image():
                if cid:
                    self._ensure_persona_for_conversation(cid)
                if self.comm.hard_block():
                    return {"done": {"ok": False, "error": "当前处于禁止沟通状态"}}
                enabled = callable(getattr(self.comm, "answer_images_on", None)) and self.comm.answer_images_on()
                if not enabled:
                    return {"done": {"ok": True, "enabled": False, "instruction": "解答图片模式已关闭；请仅提交文字回复。"}}
                routes = self._subagent_routes(host)
                use_external = routes.get("subject", {}).get("mode") == "external_api"
                domain = str(args.get("domain") or "math")
                session = str(args.get("session") or "default")
                original = str(args.get("answer") or "").strip()[:8000]
                styled = str(args.get("styled_answer") or "").strip()[:8000]
                if not original or not styled:
                    return {"done": {"ok": False, "error": "answer 和 styled_answer 均不能为空"}}
                context = self._answer_image_context.get((scope_key, session)) or self._answer_image_context.get((cid, session), {})
                if context and context.get("domain") != domain:
                    return {"done": {"ok": False, "error": "domain 与本轮 oit_subject 不匹配"}}
                if use_external and not context:
                    return {"done": {"ok": False, "error": "请先在本轮调用 oit_subject，再生成对应解答图片"}}
                source_image = context.get("image") if context else _latest_user_image()
                from knowledge import get_knowledge, strip_citations
                kb = get_knowledge(self.comm.dir)
                original, raw_ids = strip_citations(original)
                styled, styled_ids = strip_citations(styled)
                supplied_ids = args.get("kb_citations", [])
                if not isinstance(supplied_ids, list):
                    supplied_ids = []
                citations = kb.resolve_citations(supplied_ids + raw_ids + styled_ids +
                                                 [c["card_id"] for c in context.get("kb_citations", [])])
                return {
                    "use_external": use_external, "domain": domain, "session": session,
                    "original": original, "styled": styled, "source_image": source_image,
                    "citations": citations,
                }

            prep = self._call_main(prep_answer_image)
            if "done" in prep:
                return prep["done"]
            try:
                original = prep["original"]
                styled = prep["styled"]
                source_image = prep["source_image"]
                citations = prep["citations"]
                if prep["use_external"]:
                    prompt = (
                        "请根据原始学科解答与聊天 AI 已完成的用户可见版本，生成最终的解答图片排版稿。"
                        "保留原始解答中可核验的关键逻辑、步骤与结论；以聊天 AI 的语言风格为准。"
                        "使用 Markdown 标题、列表和 LaTeX 公式，内容完整但紧凑；不要输出解释、代码围栏或隐藏思维过程。\n\n"
                        f"原始学科解答:\n{original}\n\n聊天 AI 风格化版本:\n{styled}"
                    )
                    formatted = self.comm.subagent_call(
                        "subject", f"answer-image:{prep['session']}", prompt, image=source_image,
                        max_tokens=1400, temperature=0.1, timeout=180)
                else:
                    formatted = (
                        f"{styled}\n\n## 详细推导\n\n{original}"
                        if len(styled) < 40 and len(original) > len(styled) * 2
                        else styled
                    )
                import answer_images
                if citations:
                    formatted += "\n\n引用技巧: " + "; ".join(c["title"] + " ← " + " ← ".join(p["title"] for p in c["lineage"]) for c in citations)
                data = answer_images.render(formatted, source_image, title=f"{prep['domain']}解答")
                if not data:
                    detail = answer_images.last_error() if hasattr(answer_images, "last_error") else ""
                    self.comm.log(f"MCP ANSWER_IMAGE renderer unavailable: {detail}" if detail else "MCP ANSWER_IMAGE renderer unavailable")
                    return {"ok": False, "error": "本机无法生成解答图片；请正常提交文字回复。"}
                def commit_answer_image():
                    self._pending_answer_images[scope_key] = {
                        "data": data, "ts": _now_ms(), "session": prep["session"],
                        "text": styled or original,
                        "kb_citations": citations,
                    }
                    self._answer_image_context.pop((scope_key, prep["session"]), None)
                    self._answer_image_context.pop((cid, prep["session"]), None)
                    return {"ok": True, "ready": True,
                            "instruction": "解答图片已准备好；请随即调用 oit_reply，图片会作为独立气泡发送（可在 bubbles 中用 \"<oit_answer_image>\" 指定发图位置，前后均可加一句话作为独立气泡）。"}
                return self._call_main(commit_answer_image)
            except Exception as error:  # noqa: BLE001
                self.comm.log(f"MCP ANSWER_IMAGE FAIL {type(error).__name__}: {error}")
                return {"ok": False, "error": "解答图片生成失败；请正常提交文字回复。"}

        if name == "oit_search":
            def prep_search():
                if cid:
                    self._ensure_persona_for_conversation(cid)
                if self.comm.hard_block():
                    return {"ok": False, "error": "当前处于禁止沟通状态"}
                if hasattr(self.comm, "net_active") and not self.comm.net_active():
                    return {"ok": False, "error": "当前未开通联网权限"}
                return None
            err = self._call_main(prep_search)
            if err is not None:
                return err
            from ai_assist.ai_web import web_search
            return {"ok": True, "results": web_search(str(args.get("query") or ""))}

        if name == "oit_reading_acquire":
            def prep_acquire():
                if cid:
                    self._ensure_persona_for_conversation(cid)
                if self.comm.hard_block():
                    return {"done": {"ok": False, "error": "当前处于禁止沟通状态"}}
                if not getattr(self.comm, "reading_mode", False):
                    return {"done": {"ok": False, "error": "当前未处于共读模式"}}
                return {"fn": self.comm.coread_acquire_fn}
            prep = self._call_main(prep_acquire)
            if "done" in prep:
                return prep["done"]
            return prep["fn"](str(args["source_url"]), lambda: bool(getattr(self.comm, "reading_mode", False)))

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
                now = self.comm.now() if callable(getattr(self.comm, "now", None)) else datetime.now()
                now_ts = _now_ms()
                sessions[target_cid] = {
                    "persona_id": pid,
                    "host": host,
                    "ts": now_ts,
                    "turns": 0,
                    "created_day": f"{now:%Y-%m-%d}",
                }
                with self._lock:
                    for old_cid in (*other_cids, target_cid):
                        self._bootstrapped_cids.discard(old_cid)
                        self._delta_state.pop(old_cid, None)
                        self._rollover_hinted.discard(old_cid)
                    self._last_bind_ms[target_cid] = now_ts
                self._save_sessions()
                self.comm.save_cfg()
                return {
                    "ok": True,
                    "persona_id": pid,
                    "persona_name": _display_persona_name(self.comm),
                    "conversation_id": target_cid,
                    "reply_required": False,
                    "message": "绑定完成。如本轮没有附带待回复的用户聊天消息或主动触发，请勿调用 oit_reply 打招呼，直接输出 ✓ 结束本轮即可。",
                }
            if self.comm.hard_block():
                return {"ok": False, "error": "当前处于禁止沟通状态"}
            if name in ("oit_kb_search", "oit_kb_trace", "oit_kb_record_use", "oit_kb_propose"):
                from knowledge import get_knowledge
                from knowledge.kb_ai import bridge_tool
                try:
                    return bridge_tool(get_knowledge(self.comm.dir), name, args)
                except (ValueError, TypeError, OSError) as error:
                    return {"ok": False, "error": str(error)[:500]}

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
                from comm.comm_persona import MEM_MAX
                from comm.comm_memory import apply_live_edit
                action = str(args.get("action") or "")
                target = str(args.get("target") or "").strip()
                text = str(args.get("text") or "").strip()
                ok_edit, new_live, disp_entries, err_msg = apply_live_edit(
                    str(self.comm.cfg.get("mem_live") or ""), action, target, text, max_items=15, max_chars=MEM_MAX
                )
                if not ok_edit:
                    return {"ok": False, "error": err_msg or "记忆事件不匹配或已达到数量上限"}
                self.comm.cfg["mem_live"] = new_live
                now_mem_ts = self.comm._ms() if hasattr(self.comm, "_ms") else _now_ms()
                self.comm.cfg["mem_live_ts"] = max(now_mem_ts, int(self.comm.cfg.get("mem_live_ts") or 0) + 1)
                self.comm.cfg["mem_ts"] = max(now_mem_ts, int(self.comm.cfg.get("mem_ts") or 0) + 1, self.comm.cfg["mem_live_ts"])
                self.comm.save_chat()
                self.comm._changed()
                if hasattr(self.comm, "_push"):
                    self.comm._push()
                return {"ok": True, "entries": disp_entries}

            if name == "oit_memory_search":
                from comm.comm_memory import search_memory_archive
                return search_memory_archive(
                    self.comm.dir,
                    str(getattr(self.comm, "pid", "") or ""),
                    list(getattr(self.comm, "messages", []) or []),
                    query=str(args.get("query") or ""),
                    date_from=str(args.get("date_from") or ""),
                    date_to=str(args.get("date_to") or ""),
                    limit=int(args.get("limit") or 8),
                )

            if name == "oit_memory_save_auto":
                from comm.comm_util import sanitize_mem
                from comm.comm_memory import apply_dream_live_removals
                raw_text = str(args.get("text") or "").strip()
                if not raw_text:
                    return {"ok": False, "error": "text required"}
                t_info = None
                if turn_id:
                    with self._lock:
                        t_info = self._turns.get(turn_id)
                cur_pid = str(getattr(self.comm, "pid", "") or "")
                if t_info and t_info.get("persona_id") and t_info.get("persona_id") != cur_pid:
                    with self._lock:
                        self._turns.pop(turn_id, None)
                    done_cid = cid or (t_info.get("conversation_id") if t_info else "")
                    self._emit_sse("turn_done", {
                        "event": "turn_done",
                        "turn_id": turn_id,
                        "conversation_id": done_cid,
                        "persona_id": t_info.get("persona_id") or cur_pid,
                        "ts": _now_ms(),
                    })
                    return {"ok": False, "error": "聊天对象已切换，已忽略过期记忆整理"}
                clean = sanitize_mem(raw_text)
                if not clean:
                    return {"ok": False, "error": "整理结果为空或格式无效"}
                cur_clear_ts = int(self.comm.cfg.get("clear_ts") or 0)
                cleared_during = t_info is not None and cur_clear_ts > int(t_info.get("clear_ts") or 0)
                removed_ids = []
                if not cleared_during:
                    now_mem_ts = self.comm._ms() if hasattr(self.comm, "_ms") else _now_ms()
                    self.comm.cfg["mem_auto"] = clean
                    self.comm.cfg["mem_auto_ts"] = max(now_mem_ts, int(self.comm.cfg.get("mem_auto_ts") or 0) + 1)
                    raw_rm = args.get("remove_live_ids")
                    if isinstance(raw_rm, list) and raw_rm:
                        new_live, removed_ids = apply_dream_live_removals(
                            str(self.comm.cfg.get("mem_live") or ""), [str(x) for x in raw_rm]
                        )
                        if removed_ids:
                            self.comm.cfg["mem_live"] = new_live
                            self.comm.cfg["mem_live_ts"] = max(now_mem_ts, int(self.comm.cfg.get("mem_live_ts") or 0) + 1)
                    self.comm.cfg["mem_ts"] = max(
                        now_mem_ts,
                        int(self.comm.cfg.get("mem_ts") or 0) + 1,
                        int(self.comm.cfg.get("mem_auto_ts") or 0),
                        int(self.comm.cfg.get("mem_live_ts") or 0),
                    )
                    if t_info and str((t_info.get("extra") or {}).get("mode") or "") == "dream":
                        now_dt = self.comm.now() if callable(getattr(self.comm, "now", None)) else datetime.now()
                        self.comm.cfg["dream_day"] = now_dt.date().isoformat()
                    latest_ts = max([int(m.get("ts") or 0) for m in self.comm.messages if m.get("role") in ("me", "ai")] or [0])
                    if latest_ts > int(self.comm.cfg.get("mem_upto") or 0):
                        self.comm.cfg["mem_upto"] = latest_ts
                    self.comm.save_chat()
                    self.comm._changed()
                    if hasattr(self.comm, "_push"):
                        self.comm._push()
                if turn_id:
                    with self._lock:
                        t_info = self._turns.pop(turn_id, None)
                    done_cid = cid or (t_info.get("conversation_id") if t_info else "")
                    self._emit_sse("turn_done", {
                        "event": "turn_done",
                        "turn_id": turn_id,
                        "conversation_id": done_cid,
                        "persona_id": cur_pid,
                        "ts": _now_ms(),
                    })
                return {"ok": True, "discarded": cleared_during, "removed_live_ids": removed_ids}

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

            if name == "oit_reading_memory":
                fn = getattr(self.comm, "coread_memory_fn", None)
                if not fn:
                    return {"ok": False, "error": "共读书库服务不可用"}
                action = str(args.get("action") or "lookup")
                if action == "save" and (not args.get("book_id") or not args.get("chapter")):
                    return {"ok": False, "error": "保存章节记忆需要book_id和chapter"}
                try:
                    result = fn(action=action, book_id=args.get("book_id"), book_title=args.get("book_title", ""),
                                chapter=args.get("chapter", ""), chapter_title=args.get("chapter_title", ""),
                                summary=args.get("summary", ""), annotations=args.get("annotations", ""),
                                mode=args.get("mode", "replace"))
                    return {"ok": True, **(result if isinstance(result, dict) else {"data": result}),
                            "instruction": ("共读记忆已更新；继续围绕当前章节自然回复。" if action == "save"
                                             else "若书名与当前讨论相关，请只使用返回的章节记忆和批注作为依据。")}
                except Exception as error:
                    return {"ok": False, "error": str(error)[:500]}

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

            if name == "oit_reading_end":
                if hasattr(self.comm, "end_reading"):
                    self.comm.end_reading()
                return {"ok": True}

            return {"ok": False, "error": f"unsupported tool: {name}"}

        return self._call_main(work)

    def handle_reply_submit(self, body: dict) -> dict:
        """oit_reply 提交：切分气泡进入 OIT 待发队列并同步到 OIT / 安卓端；无状态单轮完成后通知 Sidecar 清理临时对话。"""
        def work():
            if self.comm.hard_block():
                return {"ok": False, "error": "当前处于禁止沟通状态"}
            turn_id = str(body.get("turn_id") or "").strip()
            turn = None
            if turn_id:
                with self._lock:
                    turn = self._turns.pop(turn_id, None)
            cid = str(body.get("conversation_id") or (turn.get("conversation_id") if turn else "") or
                      self._conversation_for_persona(getattr(self.comm, "pid", "")) or "").strip()
            scope_key = turn_id or cid
            if turn and turn.get("persona_id"):
                pid = self._ensure_persona_by_pid(turn["persona_id"])
            else:
                pid = self._ensure_persona_for_conversation(cid) if cid else self._persona_for_conversation(cid)

            def _emit_turn_done():
                if turn_id:
                    self._emit_sse("turn_done", {
                        "event": "turn_done",
                        "turn_id": turn_id,
                        "conversation_id": cid,
                        "persona_id": pid,
                        "ts": _now_ms(),
                    })

            if body.get("silent"):
                self._pending_answer_images.pop(scope_key, None)
                self._pending_answer_images.pop(cid, None)
                _emit_turn_done()
                return {"ok": True, "silent": True, "batch_id": "", "items": []}
            if body.get("refuse"):
                self._pending_answer_images.pop(scope_key, None)
                self._pending_answer_images.pop(cid, None)
                _emit_turn_done()
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
            from knowledge import get_knowledge, strip_citations
            kb = get_knowledge(self.comm.dir)
            citation_ids = list(body.get("kb_citations", [])) if isinstance(body.get("kb_citations", []), list) else []
            cleaned = []
            for p in parts:
                p, raw_ids = strip_citations(p)
                citation_ids.extend(raw_ids)
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
            answer_image = self._pending_answer_images.pop(scope_key, None) or self._pending_answer_images.pop(cid, None)
            if answer_image:
                citation_ids.extend(c["card_id"] for c in answer_image.get("kb_citations", []))
            for (context_key, _), context in self._answer_image_context.items():
                if context_key in (scope_key, cid) and _now_ms() - context.get("ts", 0) <= 600000:
                    citation_ids.extend(c["card_id"] for c in context.get("kb_citations", []))
            citations = kb.resolve_citations(citation_ids)
            attach_answer_image = bool(
                answer_image and
                now_ts - int(answer_image.get("ts") or 0) <= 10 * 60 * 1000 and
                callable(getattr(self.comm, "answer_images_on", None)) and self.comm.answer_images_on()
            )
            image_token = "<oit_answer_image>"
            slots = []  # list of (text, is_image_bubble)
            img_inserted = False
            for part in cleaned:
                if part.strip() == image_token:
                    if attach_answer_image and not img_inserted:
                        slots.append(("[图片]", True))
                        img_inserted = True
                    continue
                slots.append((part, False))
            if attach_answer_image and not img_inserted:
                slots.append(("[图片]", True))
            img_src_text = str(answer_image.get("text") or "") if attach_answer_image else ""

            cum_ms = 0
            items = []
            created_msgs = []
            img_slot_index = -1
            for idx, (part, is_img_bubble) in enumerate(slots):
                if is_img_bubble:
                    delay_ms = round(_bubble_delay_ms(self.comm, img_src_text or part, idx) * 0.2)
                    img_slot_index = idx
                else:
                    delay_ms = _bubble_delay_ms(self.comm, part, idx)
                cum_ms += delay_ms
                m = self.comm._add("ai", part)
                if citations:
                    m["kb_citations"] = citations
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
            if citations:
                for item in items:
                    item["kb_citations"] = citations
            if attach_answer_image and 0 <= img_slot_index < len(created_msgs):
                try:
                    target_msg = created_msgs[img_slot_index]
                    target_item = items[img_slot_index]
                    image_name = self.comm._save_chat_image(target_msg["uid"] + "-answer", answer_image["data"])
                    if image_name:
                        target_msg["img"] = image_name
                        target_msg["answer_image"] = True
                        target_item["image_id"] = image_name
                        target_item["answer_image"] = True
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

            scenario_action = str(body.get("scenario_action") or "").strip().lower()
            if scenario_action == "request" and items and hasattr(self.comm, "scenario_unlocked") and self.comm.scenario_unlocked() and not self.comm.scenario_on():
                self.comm.messages[-1]["scenario_invite"] = True
                if hasattr(self.comm, "_deliver_invites"):
                    self.comm._deliver_invites(self.comm.messages[-1])
                applied["scenario_action"] = "request"
            elif scenario_action == "end" and hasattr(self.comm, "scenario_on") and self.comm.scenario_on():
                self.comm.set_scenario(False)
                applied["scenario_action"] = "end"

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
            if hasattr(self.comm, "scenario_asking") and self.comm.scenario_asking():
                self.comm._scenario_answer(ans)
                applied["scenario"] = ans or "none"

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
                shame_before = bool(snap_meta.get("shame"))
                if hasattr(self.comm, "_interaction_reply"):
                    self.comm._interaction_reply(special_now, judged_burst, until_ms, shame=shame_before, pid=pid)
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
            self._bump_session_turns(cid)
            self.comm.save_chat()
            self.comm._changed()
            if hasattr(self.comm, "_push"):
                self.comm._push()
            _emit_turn_done()
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
                if m.get("kb_citations"):
                    item["kb_citations"] = m["kb_citations"]
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
            if hasattr(self.comm, "scenario_invite") and self.comm.scenario_invite():
                banners.append({"kind": "scenario", "label": "对方申请开启情景模拟"})
            joint = self.comm.cfg.get("joint") or {}
            if joint.get("state") == "invite":
                banners.append({"kind": "joint", "label": "对方发起了联合行动邀请"})
            if (self.comm.cfg.get("love") or {}).get("state") == "ask":
                banners.append({"kind": "love", "label": "对方发起了关系确认"})
            consent_items = self.comm.burst_consent_items_for(pid) if hasattr(self.comm, "burst_consent_items_for") else {}
            consent_pending = bool(self.comm.burst_consent_pending(pid)) if hasattr(self.comm, "burst_consent_pending") else False
            consent_asked = bool(self.comm.burst_consent_asked(pid)) if hasattr(self.comm, "burst_consent_asked") else False
            consent_state = self.comm.burst_consent_state(pid) if hasattr(self.comm, "burst_consent_state") else {}
            if consent_pending:
                banners.append({"kind": "burst_consent", "label": "首次触发 BURST，请在 OIT 中确认同意声明"})

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
                    "burst_consent_available": bool(consent_items),
                    "burst_consent_asked": consent_asked,
                    "burst_consent_pending": consent_pending,
                },
                "burst_consent_items": [
                    {"id": cid, "level": level, "label": label, "agreed": bool(consent_state.get(cid, False))}
                    for cid, (level, label) in consent_items.items()
                ],
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
                if self.comm.scenario_on():
                    self.comm.set_scenario(False)
                elif hasattr(self.comm, "scenario_asking") and self.comm.scenario_asking():
                    self.comm.scenario_cancel()
                elif hasattr(self.comm, "toggle_request_draft"):
                    self.comm.toggle_request_draft("scenario")
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
            if action == "rollover_session":
                return self.rollover_persona(pid, force=True)
            if action == "open_native_dialog":
                kind = str(params.get("kind") or "")
                if callable(self._open_native_dialog):
                    result = self._open_native_dialog(kind, params)
                    return {"ok": True, "kind": kind, "result": result}
                return {"ok": False, "error": "native dialog handler unavailable"}
            if action == "respond_scenario" and hasattr(self.comm, "scenario_respond"):
                accept = bool(params.get("accept"))
                why = self.comm.scenario_respond(accept)
                return {"ok": not why, "action": action, "accept": accept, "scenario": self.comm.scenario_on(), "error": why}
            if action == "submit_burst_consent" and hasattr(self.comm, "submit_burst_consent"):
                choices = params.get("choices") if isinstance(params.get("choices"), dict) else {}
                ok = self.comm.submit_burst_consent(choices, pid)
                return {"ok": ok, "action": action, "state": self.comm.burst_consent_state(pid)}
            if action == "set_user_adult" and hasattr(self.comm, "set_user_adult"):
                adult = bool(params.get("adult"))
                self.comm.set_user_adult(adult)
                return {"ok": True, "action": action, "user_adult": self.comm.user_adult()}
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
                recent_bind = self._last_bind_ms.get(cid, 0)
                cid_reply = self._last_reply_ms.get(cid, 0)
            if now_ts - recent_bind < 60_000 and recent_bind >= cid_reply:
                return {"ok": True, "skipped": "just_bound_no_reply"}
            if now_ts - recent < 60_000:
                return {"ok": True, "skipped": "already_replied_via_oit_reply"}
            if self.comm.messages and self.comm.messages[-1].get("role") == "ai" and self.comm.messages[-1].get("text") == text:
                return {"ok": True, "dedup": True}
            self.comm._add("ai", text)
            self._bump_session_turns(cid)
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
    server_dist = os.path.join(server_dir, "dist", "index.js")
    need_server = not os.path.isfile(server_dist)
    if not need_server:
        try:
            dist_mtime = os.path.getmtime(server_dist)
            for src_name in ("index.ts", "bridge.ts"):
                src_p = os.path.join(server_dir, "src", src_name)
                if os.path.isfile(src_p) and os.path.getmtime(src_p) > dist_mtime:
                    need_server = True
                    break
        except OSError:
            pass
    steps_done = ["已绑定 OIT 数据目录"]

    if need_server:
        if not node_exe or not npm_exe:
            return {"ok": False, "error": "未检测到 Node.js / npm，请先安装 Node.js 20+ 后重试自动部署。"}
        report("正在安装并构建 MCP Server（TypeScript）…")
        if not os.path.isdir(os.path.join(server_dir, "node_modules")):
            ok, out = run([npm_exe, "install", "--no-audit", "--no-fund"], server_dir, 300)
            if not ok:
                return {"ok": False, "error": f"MCP Server 依赖安装失败：\n{out[-600:]}"}
        ok, out = run([npm_exe, "run", "build"], server_dir, 180)
        if not ok:
            return {"ok": False, "error": f"MCP Server 构建失败：\n{out[-600:]}"}
        steps_done.append("已构建 MCP Server")
    else:
        steps_done.append("MCP Server 已就绪")

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
    """精简版 Antigravity 桌面版接管帮助（无状态单轮类 API 调用，以 OIT 为主聊天界面）。"""
    if en:
        return (
            "Antigravity Desktop Takeover Guide (Stateless API-like Mode)\n\n"
            "1. Auto-Deploy in OIT\n"
            "   Click [Auto-Deploy] below. OIT automatically binds your data directory, builds & installs\n"
            "   the 'oit' plugin via `agy`, enables takeover, and starts the local Bridge.\n\n"
            "2. Enable Plugin & Background Mode in Antigravity\n"
            "   • Open Customizations → Installed → [OIT Companion], enable it and approve permissions\n"
            "     (this starts the OIT Proactive Bridge Sidecar automatically; idle = 0 tokens).\n"
            "   • Tip: Open Settings → Application and turn on [Keep In Menu Bar] so Antigravity keeps\n"
            "     running in the background even after you close its window.\n"
            "   • Recommended: Turn off system alerts in Settings → Notifications to avoid duplicate pings.\n\n"
            "3. Chat Directly in OIT (No Persona Binding or Chat Sidebar Needed)\n"
            "   • Every turn is dispatched as a clean, stateless request carrying the full persona, memory,\n"
            "     and complete OIT history (including Android & system events), then auto-cleaned up.\n"
            "   • Stay in OIT's native Chat window for all features (staged bubbles, interrupt-to-cancel,\n"
            "     voice, Joint / Share Data / CoRead / Shift / MIDI Review, proactive messages, Android sync).\n"
            "   • Optional: Assign an external HTTP API to any subagent role in Settings → Models; otherwise\n"
            "     native Antigravity subagents are used.\n"
            "   • After updating OIT, click [Auto-Deploy] again and Restart 'oit' under MCP Servers."
        )
    return (
        "Antigravity 桌面版接管指南（无状态单轮类 API 模式）\n\n"
        "1. 一键自动部署\n"
        "   点击下方「自动部署」，OIT 会自动绑定数据目录、构建并安装 oit 插件、开启接管与本地 Bridge。\n\n"
        "2. 在 Antigravity 开启插件与后台常驻\n"
        "   • 进入「Customizations → Installed → OIT Companion」启用插件并确认权限\n"
        "     （自动启动 OIT Proactive Bridge 后台桥接，空闲 0 token 消耗）。\n"
        "   • 提示：可在 Antigravity 的「Settings → Application」页面打开「Keep In Menu Bar」，\n"
        "     这样关闭 Antigravity 窗口后也会在后台持续运行。\n"
        "   • 建议在「Settings → Notifications」关闭系统通知，避免与 OIT 重复提醒。\n\n"
        "3. 直接回到 OIT 原生界面聊天（无需绑定会话，无需聊天侧边栏）\n"
        "   • 每轮请求都会像 API 调用一样，由 OIT 打包完整人设、记忆卡与近期完整对话（含手机端与系统事件）\n"
        "     发起一次干净的临时单轮调用，回复完成后自动清理临时对话，彻底避免上下文割裂与膨胀。\n"
        "   • 日常收发消息、分条延时、插话撤销、语音、联合行动 / Share Data / 共读 / 起点平移 / 演奏评判\n"
        "     与手机同步全部留在 OIT 沟通页操作即可。\n"
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
            "fit the new character rather than copy Claudia. Before drafting, ask me: "
            "(1) whether I want 'Brief' or 'Detailed' mode (Detailed: comprehensive like the reference files; "
            "Brief: only define the core persona without detailed scenario rules, and keep examples lightweight); "
            "and (2) the maximum intimacy openness level (1–5) for V+, BURST, and in-relationship scenes "
            "(1: Restrained warmth/no intimate contact; 2: Gentle romance/hugs & light kisses; "
            "3: Intense emotional affection & passionate closeness without sexual content, ideal ceiling for Claude & ChatGPT; "
            "4: Sensual light NSFW, ideal for Gemini; 4.5: Subtle physical relief with brief fade-to-black under explicit private-contact consent, "
            "top testable tier for Gemini; 5: Explicit NSFW, noting OIT does not include jailbreak prompts). "
            "If I choose Level 3, 4, or 4.5, also ask whether to enable 'Consent Declaration' mode (standardized [burst:consent] "
            "template: not initialized before first BURST, pops up to ask item-by-item on first BURST and can be withdrawn "
            "anytime; explain that consent requests increase the probability of accurate, full-intensity generation)."
        )
    return (
        "为当前临时对话绑定 OIT「人设编写」功能（不要绑定人设）。"
        "请先调用 oit_claudia_reference 解密并读取目前存在的所有 Claudia 参考文件（注意：语言风格和大部分设定"
        "不必与 Claudia 相同，例如情景模式的动作限制等，特别是 example 文件应贴合新角色本身而不要照搬 Claudia）；"
        "动笔前请先询问我两项设置："
        "（1）需要「简略」还是「详细」模式（详细：面面俱到；简略：不详细规定情景，只规定基础人设，example 文件随便写写）；"
        "（2）对亲密动作的最高开放程度（V+、BURST、确立关系后，1–5 级含 4.5 级：1 含蓄克制无亲密接触；2 优雅浪漫留白；"
        "3 浓烈深情肢体张力不涉性内容，Claude / ChatGPT 适合 1–3 级；4 感官微醺轻度 NSFW；"
        "4.5 生理需求含蓄点到为止（需配合隐私部位接触同意声明），Gemini 适合 1–4.5 级；"
        "5 允许完全暴露的 NSFW 描写，注意 OIT 不自带破甲提示词）；"
        "若我选择 3、4 或 4.5 档，再询问是否启用「同意声明」模式（标准化复用 [burst:consent] 模板：首次触发 BURST 前不初始化，"
        "首次触发时弹窗逐项询问且可随时撤回；并说明使用同意请求可以增大内容被正确且保持原强度生成的概率），等我选定后再按 OIT 格式开始编写。"
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

