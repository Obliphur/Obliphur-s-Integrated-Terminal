# -*- coding: utf-8 -*-
"""情景房间本地 WebView2 服务与纯本地单页前端（零外部依赖，支持离线运行）。"""

import http.server
import json
import queue
import secrets
import threading
import urllib.parse
from typing import Callable


class RoomWebServer:
    """仅监听 127.0.0.1 随机端口的小屋本地 HTTP + SSE 服务。"""

    def __init__(
        self,
        get_state: Callable[[], dict],
        handle_action: Callable[[dict], dict],
    ):
        self._get_state = get_state
        self._handle_action = handle_action
        self.token = secrets.token_urlsafe(16)
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port: int = 0
        self._sse_lock = threading.Lock()
        self._sse_clients: list[queue.Queue] = []
        self._stopped = False

    @property
    def url(self) -> str:
        if not self._port:
            return ""
        return f"http://127.0.0.1:{self._port}/?token={urllib.parse.quote(self.token)}"

    def start(self) -> str:
        if self._server is not None:
            return self.url

        srv_ref = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _fmt, *_args):
                return

            def _check_token(self) -> tuple[bool, urllib.parse.ParseResult]:
                parsed = urllib.parse.urlparse(self.path)
                qs = urllib.parse.parse_qs(parsed.query)
                tok = (qs.get("token") or [""])[0]
                if not tok:
                    auth = self.headers.get("Authorization") or ""
                    if auth.startswith("Bearer "):
                        tok = auth[7:].strip()
                return (bool(tok) and secrets.compare_digest(tok, srv_ref.token)), parsed

            def _send_bytes(self, code: int, body: bytes, content_type: str):
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    pass

            def _send_json(self, code: int, payload: dict):
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self._send_bytes(code, raw, "application/json; charset=utf-8")

            def do_GET(self):
                ok, parsed = self._check_token()
                if not ok:
                    self._send_json(403, {"ok": False, "error": "forbidden"})
                    return
                path = parsed.path
                if path in ("/", "/index.html"):
                    html = _ROOM_HTML.replace("__TOKEN__", json.dumps(srv_ref.token))
                    self._send_bytes(200, html.encode("utf-8"), "text/html; charset=utf-8")
                    return
                if path == "/api/state":
                    try:
                        data = srv_ref._get_state()
                        self._send_json(200, {"ok": True, "state": data})
                    except Exception as exc:
                        self._send_json(500, {"ok": False, "error": str(exc)})
                    return
                if path == "/api/events":
                    self._handle_sse()
                    return
                self._send_json(404, {"ok": False, "error": "not_found"})

            def do_POST(self):
                ok, parsed = self._check_token()
                if not ok:
                    self._send_json(403, {"ok": False, "error": "forbidden"})
                    return
                if parsed.path != "/api/action":
                    self._send_json(404, {"ok": False, "error": "not_found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length") or "0")
                    if length < 0 or length > 2 * 1024 * 1024:
                        self._send_json(413, {"ok": False, "error": "payload_too_large"})
                        return
                    raw = self.rfile.read(length) if length else b"{}"
                    body = json.loads(raw.decode("utf-8") or "{}")
                    if not isinstance(body, dict):
                        body = {}
                except Exception:
                    self._send_json(400, {"ok": False, "error": "invalid_json"})
                    return
                try:
                    res = srv_ref._handle_action(body)
                    self._send_json(200, res if isinstance(res, dict) else {"ok": True})
                except Exception as exc:
                    self._send_json(500, {"ok": False, "error": str(exc)})

            def _handle_sse(self):
                q: queue.Queue = queue.Queue(maxsize=32)
                with srv_ref._sse_lock:
                    srv_ref._sse_clients.append(q)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    self.wfile.write(b": connected\n\n")
                    self.wfile.flush()
                    while not srv_ref._stopped:
                        try:
                            msg = q.get(timeout=15.0)
                            if msg is None:
                                break
                            self.wfile.write(msg)
                            self.wfile.flush()
                        except queue.Empty:
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    pass
                finally:
                    with srv_ref._sse_lock:
                        if q in srv_ref._sse_clients:
                            srv_ref._sse_clients.remove(q)

        self._stopped = False
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="oit-room-web")
        self._thread.start()
        return self.url

    def notify_update(self, reason: str = "refresh"):
        """通知所有已连接的 WebView2 前端增量拉取最新状态。"""
        payload = f"event: update\ndata: {json.dumps({'reason': reason})}\n\n".encode("utf-8")
        with self._sse_lock:
            clients = list(self._sse_clients)
        for q in clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass

    def stop(self):
        self._stopped = True
        with self._sse_lock:
            clients = list(self._sse_clients)
            self._sse_clients.clear()
        for q in clients:
            try:
                q.put_nowait(None)
            except queue.Full:
                pass
        srv = self._server
        self._server = None
        self._port = 0
        if srv is not None:
            try:
                srv.shutdown()
            except Exception:
                pass
            try:
                srv.server_close()
            except Exception:
                pass


_ROOM_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>OIT 小屋</title>
<style>
  :root {
    --bg: #0f131b;
    --bg-card: #171c28;
    --border: #2a3346;
    --text: #e6edf7;
    --text-dim: #8b97ad;
    --accent: #67d5f7;
    --accent-hover: #8be0fa;
    --danger: #ef4444;
    --warn: #f59e0b;
    --npc: #a78bfa;
    --me: #a6e3a1;
    --pos: #94e2d5;
    --door: #f6c177;
    --map-bg: #12161f;
    --map-fg: #dce4f2;
    --map-border: #293245;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0; width: 100%; height: 100%;
    overflow: hidden;
    background: var(--bg);
    color: var(--text);
    font-family: "Microsoft YaHei UI", "Segoe UI", system-ui, -apple-system, sans-serif;
    font-size: 13px;
    user-select: none;
    -webkit-font-smoothing: antialiased;
  }
  .app {
    display: flex; flex-direction: column;
    width: 100%; height: 100%;
    padding: 12px 18px 14px;
    gap: 8px;
  }
  /* 顶栏 */
  .hdr {
    display: flex; align-items: center; gap: 8px; flex-wrap: nowrap; min-height: 34px;
  }
  .hdr-left { display: flex; align-items: center; gap: 10px; min-width: 0; flex: 1; }
  .hdr-right { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
  .room-title {
    font-size: 17px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .room-theme {
    font-size: 12px; color: var(--accent); white-space: nowrap;
  }
  .room-theme.locked { color: var(--danger); }
  .auto-status {
    font-size: 12px; color: var(--text-dim); cursor: pointer;
    padding: 4px 8px; border-radius: 6px; transition: background .15s, color .15s;
    white-space: nowrap;
  }
  .auto-status.on { color: var(--accent); }
  .auto-status:hover { background: rgba(255,255,255,0.06); }

  .btn {
    appearance: none; border: 1px solid var(--border);
    background: var(--bg-card); color: var(--text);
    padding: 5px 11px; border-radius: 6px;
    font-size: 12.5px; cursor: pointer; font-family: inherit;
    transition: background .12s, border-color .12s, transform .08s;
    white-space: nowrap;
  }
  .btn:hover:not(:disabled) { border-color: var(--accent); background: rgba(103,213,247,0.08); }
  .btn:active:not(:disabled) { transform: translateY(1px); }
  .btn:disabled { opacity: 0.45; cursor: not-allowed; }
  .btn-accent {
    background: var(--accent); color: #0c1118; border-color: var(--accent); font-weight: 600;
  }
  .btn-accent:hover:not(:disabled) { background: var(--accent-hover); border-color: var(--accent-hover); }
  .select {
    background: var(--bg-card); color: var(--text);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 5px 8px; font-size: 12.5px; font-family: inherit;
    max-width: 220px; outline: none; cursor: pointer;
  }
  .select:focus { border-color: var(--accent); }

  /* 主体双栏 */
  .main {
    display: grid; grid-template-columns: 11fr 10fr; gap: 12px;
    flex: 1; min-height: 0;
  }
  .panel {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    display: flex; flex-direction: column;
    min-height: 0; overflow: hidden;
    padding: 10px 12px;
    gap: 8px;
  }

  /* 左侧地图 */
  .ambience {
    font-size: 12px; color: var(--text-dim); line-height: 1.45;
  }
  .map-box {
    flex: 1; min-height: 0;
    background: var(--map-bg);
    color: var(--map-fg);
    border: 1px solid var(--map-border);
    border-radius: 8px;
    padding: 12px 14px;
    overflow: auto;
    font-family: "NSimSun", "SimSun", "Consolas", monospace;
    font-size: 14px;
    line-height: 1.38;
    white-space: pre;
  }
  .hs {
    cursor: pointer; border-radius: 3px;
    transition: background .1s;
  }
  .hs:hover { background: #24344d; text-decoration: underline; }
  .hs-fac { color: var(--accent); }
  .hs-pos { color: var(--pos); }
  .hs-door { color: var(--door); }
  .hs-actor { color: #c4a7e7; }
  .hs-actor-me { color: var(--me); }
  .hover-tip {
    font-size: 12px; color: var(--accent); min-height: 18px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .occ-bar {
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
    font-size: 12px; color: var(--text-dim);
  }
  .chip {
    display: inline-flex; align-items: center; gap: 4px;
    background: var(--bg); color: var(--text);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 3px 8px; font-size: 12px;
    transition: border-color .12s, background .12s;
  }
  .chip.me { color: var(--accent); cursor: default; }
  .chip.npc { cursor: pointer; }
  .chip.npc:hover { border-color: var(--accent); background: rgba(103,213,247,0.08); }

  /* 右侧日志与输入 */
  .log-head {
    display: flex; align-items: center; justify-content: space-between;
  }
  .log-title { font-size: 13.5px; font-weight: 700; }
  .busy-lbl { font-size: 12px; color: var(--accent); }
  .log-box {
    flex: 1; min-height: 0;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 12px;
    overflow-y: auto;
    user-select: text;
    line-height: 1.55;
    font-size: 13px;
  }
  .log-sys { color: var(--text-dim); margin: 2px 0; }
  .log-private { color: var(--text-dim); font-style: italic; margin: 2px 0; }
  .log-msg { margin: 6px 0 10px; }
  .log-who-me { color: var(--accent); font-weight: 700; }
  .log-who-npc { color: var(--npc); font-weight: 700; }
  .log-act { color: var(--warn); padding-left: 10px; font-size: 12.5px; }
  .log-speech { color: var(--text); padding-left: 10px; white-space: pre-wrap; word-break: break-word; }

  .hand-bar {
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  }
  .hand-pill {
    background: var(--bg); color: var(--text);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 3px 9px; font-size: 12px; cursor: default;
    transition: border-color .12s;
  }
  .hand-pill.holding { color: var(--accent); cursor: pointer; }
  .hand-pill.holding:hover { border-color: var(--accent); }
  .hand-pill.bound { color: var(--danger); border-color: rgba(239,68,68,0.5); }
  .dim-tip { font-size: 11.5px; color: var(--text-dim); }

  .pending-list { display: flex; flex-direction: column; gap: 4px; max-height: 84px; overflow-y: auto; }
  .pending-item {
    display: flex; align-items: center; justify-content: space-between; gap: 8px;
    background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
    padding: 3px 8px; font-size: 12px; color: var(--accent);
  }
  .pending-del {
    color: var(--text-dim); cursor: pointer; padding: 0 4px; font-weight: 700;
  }
  .pending-del:hover { color: var(--danger); }

  .vol-bar {
    display: flex; align-items: center; gap: 10px; font-size: 12px;
  }
  .vol-bar label { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; }
  .vol-hint { color: var(--text-dim); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }

  .inp-row { display: flex; gap: 8px; align-items: flex-end; }
  .entry {
    flex: 1; min-height: 58px; max-height: 110px; resize: none;
    background: var(--bg); color: var(--text);
    border: 1px solid var(--border); border-radius: 8px;
    padding: 8px 10px; font-family: inherit; font-size: 13px; line-height: 1.45;
    outline: none;
  }
  .entry:focus { border-color: var(--accent); }

  /* 悬浮菜单 */
  .menu {
    position: fixed; z-index: 900; min-width: 190px; max-width: 280px;
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: 8px; padding: 4px;
    box-shadow: 0 10px 28px rgba(0,0,0,0.45);
    display: none;
  }
  .menu.open { display: block; }
  .menu-item {
    padding: 7px 10px; border-radius: 5px; font-size: 12.5px; cursor: pointer;
    color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .menu-item:hover { background: rgba(103,213,247,0.14); color: var(--accent); }

  /* 弹窗 */
  .modal-mask {
    position: fixed; inset: 0; z-index: 1000;
    background: rgba(0,0,0,0.55);
    display: none; align-items: center; justify-content: center;
    padding: 18px;
  }
  .modal-mask.open { display: flex; }
  .modal {
    width: 540px; max-width: 100%; max-height: 88vh;
    background: var(--bg); border: 1px solid var(--border);
    border-radius: 12px; box-shadow: 0 18px 46px rgba(0,0,0,0.55);
    display: flex; flex-direction: column; overflow: hidden;
  }
  .modal-hdr {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 16px; border-bottom: 1px solid var(--border);
    font-size: 14.5px; font-weight: 700;
  }
  .modal-body {
    padding: 14px 16px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px;
  }
  .modal-ftr {
    padding: 10px 16px; border-top: 1px solid var(--border);
    display: flex; justify-content: flex-end; gap: 8px;
  }
  .field-label { font-size: 12.5px; font-weight: 600; color: var(--text); margin-bottom: 4px; }
  .input, .textarea {
    width: 100%; background: var(--bg-card); color: var(--text);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 7px 9px; font-family: inherit; font-size: 13px; outline: none;
  }
  .input:focus, .textarea:focus { border-color: var(--accent); }
  .textarea { min-height: 68px; resize: vertical; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; }
  .part-table {
    display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px;
  }
  .part-row {
    display: flex; align-items: center; justify-content: space-between; gap: 6px;
    padding: 5px 8px; border-radius: 6px;
    border: 1px solid var(--border); background: var(--bg);
    font-size: 12.5px; cursor: pointer;
    transition: border-color .12s, background .12s;
  }
  .part-row:hover:not(.disabled) { border-color: var(--accent); }
  .part-row.checked { border-color: var(--accent); background: rgba(103,213,247,0.1); }
  .part-row.disabled { opacity: 0.45; cursor: not-allowed; }
  .part-row-left { display: flex; align-items: center; gap: 6px; min-width: 0; }
  .part-tags { display: flex; gap: 4px; flex-shrink: 0; }
  .part-tag {
    font-size: 11px; padding: 1px 5px; border-radius: 4px;
    background: rgba(103,213,247,0.18); color: var(--accent);
    white-space: nowrap;
  }
  .part-tag.gaze { background: rgba(196,167,231,0.2); color: #c4a7e7; }
  .sel-summary {
    display: flex; flex-wrap: wrap; gap: 10px;
    font-size: 12px; color: var(--text-dim);
    padding: 5px 8px; border-radius: 6px;
    background: var(--bg); border: 1px solid var(--border);
  }
  .sel-summary b { color: var(--accent); font-weight: 600; }
  .radio-opt {
    display: flex; align-items: center; gap: 6px; font-size: 12.5px; cursor: pointer;
  }
  .radio-opt.disabled { opacity: 0.45; cursor: not-allowed; }
  .row-between { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
  .card-box {
    background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px;
  }

  /* Toast */
  .toast-wrap {
    position: fixed; top: 14px; left: 50%; transform: translateX(-50%);
    z-index: 1200; display: flex; flex-direction: column; gap: 6px; pointer-events: none;
  }
  .toast {
    background: var(--bg-card); color: var(--text);
    border: 1px solid var(--accent); border-radius: 8px;
    padding: 7px 14px; font-size: 12.5px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
    animation: fadeIn .15s ease-out;
  }
  .toast.warn { border-color: var(--warn); color: #fde68a; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: translateY(0); } }

  /* 滚动条 */
  ::-webkit-scrollbar { width: 7px; height: 7px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: rgba(139,151,173,0.28); border-radius: 4px; }
  ::-webkit-scrollbar-thumb:hover { background: rgba(139,151,173,0.48); }
</style>
</head>
<body>
<div class="app">
  <div class="hdr">
    <div class="hdr-left">
      <button class="btn" id="btnBack">← 返回沟通</button>
      <div class="room-title" id="roomTitle">🏠 加载中…</div>
      <div class="room-theme" id="roomTheme"></div>
    </div>
    <div class="hdr-right">
      <div class="auto-status" id="autoStatus"></div>
      <select class="select" id="roomSelect"></select>
      <button class="btn" id="btnKeys">🔑 钥匙管理</button>
      <button class="btn" id="btnCreate">＋ 协商建房</button>
    </div>
  </div>

  <div class="main">
    <div class="panel">
      <div class="ambience" id="ambience"></div>
      <div class="map-box" id="mapBox"></div>
      <div class="hover-tip" id="hoverTip">点击地图中的 [设施] 打开对应功能，点击 [@人物] 展开部位互动。</div>
      <div class="occ-bar">
        <span>当前在场：</span>
        <div id="occChips" style="display:flex;gap:6px;flex-wrap:wrap;flex:1;"></div>
      </div>
    </div>

    <div class="panel">
      <div class="log-head">
        <div class="log-title">📜 房间记录（即时）</div>
        <div class="busy-lbl" id="busyLbl"></div>
      </div>
      <div class="log-box" id="logBox"></div>
      <div class="hand-bar">
        <div class="hand-pill" id="leftHand"></div>
        <div class="hand-pill" id="rightHand"></div>
        <span class="dim-tip">(自由/握住时可脱离作出其他动作；点握住可松开)</span>
      </div>
      <div class="pending-list" id="pendingList"></div>
      <div class="vol-bar">
        <span class="dim-tip">音量：</span>
        <label><input type="radio" name="vol" value="normal" checked /> 正常音量</label>
        <label><input type="radio" name="vol" value="whisper" /> 轻语</label>
        <label><input type="radio" name="vol" value="shout" /> 大声呼喊</label>
        <div class="vol-hint" id="volHint"></div>
      </div>
      <div class="inp-row">
        <textarea class="entry" id="entry" placeholder="输入发言内容（Enter 发送，Shift+Enter 换行）…"></textarea>
        <button class="btn btn-accent" id="btnSend" style="height:36px;padding:0 16px;">发送</button>
      </div>
    </div>
  </div>
</div>

<div class="menu" id="ctxMenu"></div>
<div class="modal-mask" id="modalMask">
  <div class="modal" id="modalBox">
    <div class="modal-hdr">
      <span id="modalTitle"></span>
      <button class="btn" id="modalClose" style="padding:2px 8px;">✕</button>
    </div>
    <div class="modal-body" id="modalBody"></div>
    <div class="modal-ftr" id="modalFtr"></div>
  </div>
</div>
<div class="toast-wrap" id="toastWrap"></div>

<script>
const TOKEN = __TOKEN__;
const DEFAULT_HOVER = "点击地图中的 [设施] 打开对应功能，点击 [@人物] 展开部位互动。";

let STATE = null;
let lastMapSig = "";
let lastOccSig = "";
let lastPendingSig = "";
let lastLogRoom = "";
let lastLogCount = -1;
let lastLogTail = "";
let currentHotspots = [];

function apiUrl(path) {
  const sep = path.includes("?") ? "&" : "?";
  return path + sep + "token=" + encodeURIComponent(TOKEN);
}

function showToast(msg, warn = false) {
  if (!msg) return;
  const wrap = document.getElementById("toastWrap");
  const el = document.createElement("div");
  el.className = "toast" + (warn ? " warn" : "");
  el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transition = "opacity .2s";
    setTimeout(() => el.remove(), 220);
  }, 2400);
}

async function fetchState() {
  try {
    const r = await fetch(apiUrl("/api/state"));
    const j = await r.json();
    if (j.ok && j.state) applyState(j.state);
  } catch (e) {}
}

async function doAction(payload) {
  try {
    const r = await fetch(apiUrl("/api/action"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const j = await r.json();
    if (j.toast) showToast(j.toast, !!j.warn);
    if (!j.ok && j.error) showToast(j.error, true);
    if (j.state) applyState(j.state);
    return j;
  } catch (e) {
    showToast("请求失败", true);
    return { ok: false };
  }
}

function applyTheme(colors) {
  if (!colors) return;
  const r = document.documentElement.style;
  if (colors.bg) r.setProperty("--bg", colors.bg);
  if (colors.bg_card) r.setProperty("--bg-card", colors.bg_card);
  if (colors.border) r.setProperty("--border", colors.border);
  if (colors.text) r.setProperty("--text", colors.text);
  if (colors.text_dim) r.setProperty("--text-dim", colors.text_dim);
  if (colors.accent) r.setProperty("--accent", colors.accent);
  if (colors.accent_hover) r.setProperty("--accent-hover", colors.accent_hover);
}

function applyState(st) {
  STATE = st;
  applyTheme(st.colors);

  document.getElementById("roomTitle").textContent = "🏠 " + st.room.name;
  const themeEl = document.getElementById("roomTheme");
  themeEl.textContent = "[" + st.room.theme + (st.lock_tag || "") + "]";
  themeEl.className = "room-theme" + (st.room.locked_inside ? " locked" : "");
  document.getElementById("ambience").textContent = st.room.ambience || "安静的文字空间。";

  const autoEl = document.getElementById("autoStatus");
  autoEl.textContent = st.auto_status_text;
  autoEl.className = "auto-status" + (st.npc_on ? " on" : "");

  // 房间下拉：只列当前房间与有门相连的房间（只有互相连接的房间能走过去）
  const sel = document.getElementById("roomSelect");
  const doorRooms = (st.rooms || []).filter(r => r.linked);
  const optsSig = doorRooms.map(r => r.id + "|" + r.label).join("\n");
  if (sel.dataset.sig !== optsSig) {
    sel.dataset.sig = optsSig;
    sel.innerHTML = "";
    for (const r of doorRooms) {
      const opt = document.createElement("option");
      opt.value = r.id;
      opt.textContent = r.label;
      sel.appendChild(opt);
    }
  }
  sel.value = st.room.id;

  renderMap(st.map_text, st.hotspots);
  renderOccupants(st.occupants);
  renderHands(st.hands, st.pending_actions);
  renderLogs(st.room.id, st.logs);

  document.getElementById("busyLbl").textContent = st.busy_text || "";
  document.getElementById("volHint").textContent = st.vol_hint || "";
  const volRadios = document.querySelectorAll('input[name="vol"]');
  volRadios.forEach(rad => { rad.checked = (rad.value === st.volume); });
}

function renderMap(text, hotspots) {
  const sig = text + "\n@@" + JSON.stringify(hotspots);
  if (sig === lastMapSig) return;
  lastMapSig = sig;
  currentHotspots = hotspots || [];

  const byLine = new Map();
  currentHotspots.forEach((hs, idx) => {
    const ln = hs.line;
    if (!byLine.has(ln)) byLine.set(ln, []);
    byLine.get(ln).push({ hs, idx });
  });

  const lines = (text || "").split("\n");
  const frag = document.createDocumentFragment();
  for (let i = 0; i < lines.length; i++) {
    const lineNo = i + 1;
    const lineStr = lines[i];
    const items = (byLine.get(lineNo) || []).sort((a, b) => a.hs.col_start - b.hs.col_start);
    let cursor = 0;
    for (const { hs, idx } of items) {
      if (hs.col_start > cursor) {
        frag.appendChild(document.createTextNode(lineStr.slice(cursor, hs.col_start)));
      }
      const sp = document.createElement("span");
      let cls = "hs ";
      if (hs.kind === "actor") cls += (hs.id === "user" ? "hs-actor-me" : "hs-actor");
      else if (hs.action === "door") cls += "hs-door";
      else if (hs.action === "position") cls += "hs-pos";
      else cls += "hs-fac";
      sp.className = cls;
      sp.dataset.hsIdx = String(idx);
      sp.textContent = lineStr.slice(hs.col_start, hs.col_end);
      frag.appendChild(sp);
      cursor = hs.col_end;
    }
    if (cursor < lineStr.length) {
      frag.appendChild(document.createTextNode(lineStr.slice(cursor)));
    }
    if (i < lines.length - 1) frag.appendChild(document.createTextNode("\n"));
  }
  const box = document.getElementById("mapBox");
  box.innerHTML = "";
  box.appendChild(frag);
}

function renderOccupants(occupants) {
  const sig = JSON.stringify(occupants);
  if (sig === lastOccSig) return;
  lastOccSig = sig;
  const wrap = document.getElementById("occChips");
  wrap.innerHTML = "";
  for (const occ of occupants) {
    const el = document.createElement("div");
    el.className = "chip " + (occ.is_me ? "me" : "npc");
    el.textContent = occ.chip_text;
    if (!occ.is_me) {
      el.addEventListener("click", () => openActorModal(occ.id, occ.name));
    }
    wrap.appendChild(el);
  }
}

function channelLabel(ch) {
  if (ch === "left") return "左手";
  if (ch === "right") return "右手";
  if (ch === "gaze") return "目光";
  return "手部";
}

function handSuffix(state) {
  if (state === "holding") return " (握住)";
  if (state === "held") return " (被牵着)";
  if (state === "bound") return " (被束缚)";
  if (state === "used") return " (刚用过)";
  return "";
}

function renderHands(hands, pending) {
  const setPill = (id, h) => {
    const el = document.getElementById(id);
    el.textContent = "[" + h.label + "]";
    const cls = (h.state === "bound" || h.state === "held") ? " bound"
      : (h.state === "holding" || h.state === "used" ? " holding" : "");
    el.className = "hand-pill" + cls;
    el.title = h.state === "holding" ? "点击松开握持"
      : (h.state === "held" ? "点击把手抽回来" : "");
  };
  setPill("leftHand", hands.left);
  setPill("rightHand", hands.right);

  const sig = JSON.stringify(pending);
  if (sig === lastPendingSig) return;
  lastPendingSig = sig;
  const list = document.getElementById("pendingList");
  list.innerHTML = "";
  pending.forEach((act, idx) => {
    const row = document.createElement("div");
    row.className = "pending-item";
    const handTxt = channelLabel(act.hand);
    const secTxt = act.secret ? "·隐秘" : "";
    const next = pending[idx + 1];
    const showText = !next || (next.text || "") !== (act.text || "");
    const txt = document.createElement("span");
    txt.textContent = showText && act.text
      ? `[${handTxt}${secTxt} → ${act.target_name}·${act.part}] ${act.text}`
      : `[${handTxt}${secTxt} → ${act.target_name}·${act.part}]`;
    const del = document.createElement("span");
    del.className = "pending-del";
    del.textContent = "✕";
    del.addEventListener("click", () => doAction({ type: "remove_pending", index: idx }));
    row.append(txt, del);
    list.appendChild(row);
  });
}

function buildLogNode(e) {
  const kind = e.kind;
  if (kind === "sys" || kind === "move") {
    const d = document.createElement("div");
    d.className = "log-sys";
    d.textContent = "* " + e.text;
    return d;
  }
  if (kind === "private_hint") {
    const d = document.createElement("div");
    d.className = "log-private";
    d.textContent = "… " + e.text;
    return d;
  }
  const wrap = document.createElement("div");
  wrap.className = "log-msg";
  const hdr = document.createElement("div");
  hdr.className = e.sender === "user" ? "log-who-me" : "log-who-npc";
  let volTag = "";
  if (e.volume === "whisper") volTag = `（在「${e.pos || "原处"}」轻语）`;
  else if (e.volume === "shout") volTag = "（大声呼喊）";
  const fromTag = e.from_room ? `[从「${e.from_room}」隔墙传来] ` : "";
  hdr.textContent = fromTag + `${e.sender_name}${volTag}：`;
  wrap.appendChild(hdr);
  const acts = e.actions || [];
  acts.forEach((a, idx) => {
    const act = document.createElement("div");
    act.className = "log-act";
    const h = channelLabel(a.hand);
    const sec = a.secret ? "·隐秘" : "";
    const next = acts[idx + 1];
    const showText = !next || (next.text || "") !== (a.text || "");
    act.textContent = showText && a.text
      ? `[${h}${sec}→${a.target_name}·${a.part}] ${a.text}`
      : `[${h}${sec}→${a.target_name}·${a.part}]`;
    wrap.appendChild(act);
  });
  if (e.text) {
    const sp = document.createElement("div");
    sp.className = "log-speech";
    sp.textContent = e.text;
    wrap.appendChild(sp);
  }
  return wrap;
}

function renderLogs(roomId, entries) {
  const box = document.getElementById("logBox");
  const list = entries || [];
  const tail = list.length ? JSON.stringify(list[list.length - 1]) : "";
  const atBottom = (box.scrollHeight - box.scrollTop - box.clientHeight) < 40;

  // 同一房间且仅追加了新日志：增量 appendChild，零闪烁
  if (roomId === lastLogRoom && lastLogCount >= 0 && list.length >= lastLogCount) {
    if (list.length === lastLogCount && tail === lastLogTail) return;
    if (lastLogCount === 0 && list.length > 0) {
      box.innerHTML = "";
    }
    const frag = document.createDocumentFragment();
    for (let i = lastLogCount; i < list.length; i++) {
      frag.appendChild(buildLogNode(list[i]));
    }
    box.appendChild(frag);
    lastLogCount = list.length;
    lastLogTail = tail;
    if (atBottom) box.scrollTop = box.scrollHeight;
    return;
  }

  // 切换房间或日志回退时全量渲染
  lastLogRoom = roomId;
  lastLogCount = list.length;
  lastLogTail = tail;
  box.innerHTML = "";
  if (!list.length) {
    const empty = document.createElement("div");
    empty.className = "log-sys";
    empty.textContent = "* 你环顾四周，房间里安静而温暖。点击左侧地图上的设施或人物开始互动吧。";
    box.appendChild(empty);
  } else {
    const frag = document.createDocumentFragment();
    for (const e of list) frag.appendChild(buildLogNode(e));
    box.appendChild(frag);
  }
  box.scrollTop = box.scrollHeight;
}

/* 悬浮菜单 */
function closeMenu() {
  document.getElementById("ctxMenu").classList.remove("open");
}
function openMenu(x, y, items) {
  const m = document.getElementById("ctxMenu");
  m.innerHTML = "";
  for (const it of items) {
    const d = document.createElement("div");
    d.className = "menu-item";
    d.textContent = it.label;
    d.addEventListener("click", (ev) => {
      ev.stopPropagation();
      closeMenu();
      it.onClick();
    });
    m.appendChild(d);
  }
  m.classList.add("open");
  const mw = m.offsetWidth || 200;
  const mh = m.offsetHeight || 160;
  const left = Math.min(x, window.innerWidth - mw - 10);
  const top = Math.min(y, window.innerHeight - mh - 10);
  m.style.left = Math.max(8, left) + "px";
  m.style.top = Math.max(8, top) + "px";
}
document.addEventListener("click", closeMenu);

/* 弹窗工具 */
function openModal(title, bodyBuilder, footerButtons) {
  document.getElementById("modalTitle").textContent = title;
  const body = document.getElementById("modalBody");
  const ftr = document.getElementById("modalFtr");
  body.innerHTML = "";
  ftr.innerHTML = "";
  bodyBuilder(body);
  for (const b of footerButtons) {
    const btn = document.createElement("button");
    btn.className = "btn" + (b.accent ? " btn-accent" : "");
    btn.textContent = b.label;
    if (b.disabled) btn.disabled = true;
    btn.addEventListener("click", b.onClick);
    ftr.appendChild(btn);
  }
  document.getElementById("modalMask").classList.add("open");
}
function closeModal() {
  document.getElementById("modalMask").classList.remove("open");
}
document.getElementById("modalClose").addEventListener("click", closeModal);
document.getElementById("modalMask").addEventListener("click", (e) => {
  if (e.target.id === "modalMask") closeModal();
});

/* 地图热点交互（事件委托） */
const mapBox = document.getElementById("mapBox");
mapBox.addEventListener("mouseover", (e) => {
  const sp = e.target.closest(".hs");
  if (!sp) return;
  const hs = currentHotspots[Number(sp.dataset.hsIdx)];
  if (hs) document.getElementById("hoverTip").textContent = `[${hs.label}] — ${hs.desc}`;
});
mapBox.addEventListener("mouseout", (e) => {
  if (e.target.closest(".hs")) {
    document.getElementById("hoverTip").textContent = DEFAULT_HOVER;
  }
});
mapBox.addEventListener("click", (e) => {
  const sp = e.target.closest(".hs");
  if (!sp) return;
  e.stopPropagation();
  const hs = currentHotspots[Number(sp.dataset.hsIdx)];
  if (!hs) return;
  if (hs.kind === "actor") {
    if (hs.id === "user") {
      showToast("这是你自己。点击其他角色可展开互动部位列表。");
      return;
    }
    openActorModal(hs.id, hs.label);
    return;
  }
  const act = hs.action || "custom";
  const myOcc = STATE && STATE.occupants ? STATE.occupants.find(o => o.is_me) : null;
  const moveOpt = {
    label: `🚶 移动到「${hs.label}」`,
    onClick: () => doAction({ type: "move_position", label: hs.label, hs }),
  };
  if (act === "piano") {
    openMenu(e.clientX, e.clientY, [
      moveOpt,
      { label: "🎹 打开音乐演奏", onClick: () => doAction({ type: "open_music" }) },
      { label: `✋ 与「${hs.label}」互动…`, onClick: () => openFacilityModal(hs) },
    ]);
  } else if (act === "bookshelf") {
    openMenu(e.clientX, e.clientY, [
      moveOpt,
      { label: "📚 打开共读 / 书库", onClick: () => doAction({ type: "open_coread" }) },
      { label: `✋ 与「${hs.label}」互动…`, onClick: () => openFacilityModal(hs) },
    ]);
  } else if (act === "desk") {
    openMenu(e.clientX, e.clientY, [
      moveOpt,
      { label: "⏱ 打开专注计时器", onClick: () => doAction({ type: "open_page", page: "timer" }) },
      { label: "🤝 发起联合行动", onClick: () => doAction({ type: "open_joint" }) },
      { label: `✋ 与「${hs.label}」互动…`, onClick: () => openFacilityModal(hs) },
    ]);
  } else if (act === "board") {
    openMenu(e.clientX, e.clientY, [
      moveOpt,
      { label: "⚔ 打开合约页", onClick: () => doAction({ type: "open_page", page: "contract" }) },
      { label: "📚 打开知识库", onClick: () => doAction({ type: "open_page", page: "knowledge" }) },
      { label: `✋ 与「${hs.label}」互动…`, onClick: () => openFacilityModal(hs) },
    ]);
  } else if (act === "position") {
    doAction({ type: "move_position", label: hs.label, hs });
  } else if (act === "door") {
    const items = [moveOpt];
    const locked = STATE && STATE.room && STATE.room.locked_inside;
    items.push({
      label: locked ? "🔓 解除房门反锁" : "🔒 从内部反锁房门",
      onClick: () => doAction({ type: "toggle_lock" }),
    });
    for (const r of (STATE ? STATE.rooms : []).filter(r => r.linked)) {
      const mark = r.id === STATE.room.id ? "✓ " : "";
      items.push({
        label: mark + r.label,
        onClick: () => doAction({ type: "switch_room", room_id: r.id }),
      });
    }
    items.push({ label: "🔑 管理房间钥匙与连接…", onClick: openKeysModal });
    items.push({ label: "＋ 协商新建房间…", onClick: openCreateRoomModal });
    openMenu(e.clientX, e.clientY, items);
  } else {
    openMenu(e.clientX, e.clientY, [
      moveOpt,
      { label: `✋ 与「${hs.label}」互动…`, onClick: () => openFacilityModal(hs) },
    ]);
  }
});

/* 部位互动弹窗 */
async function openActorModal(actorId, actorName) {
  const res = await doAction({ type: "actor_interact_info", actor_id: actorId });
  if (!res.ok || !res.info) return;
  const info = res.info;
  const samePos = info.user_pos === info.target_pos;
  const handBusy = (st) => (st === "bound" || st === "held");
  const leftBound = handBusy(info.hands.left.state);
  const rightBound = handBusy(info.hands.right.state);
  const handsBlocked = !samePos || (leftBound && rightBound);
  let activeHand = (!samePos ? "" : (!leftBound ? "left" : (!rightBound ? "right" : "")));
  let gazeChecked = false;
  const handPart = { left: "", right: "" };
  let customPartInput = null;
  let secretCheck = null;
  let textArea = null;

  openModal(`与 ${actorName} 互动`, (body) => {
    const top = document.createElement("div");
    top.className = "row-between";
    const meta = document.createElement("div");
    meta.style.cssText = "font-size:12px;color:var(--accent);";
    meta.textContent = `位置：你在「${info.user_pos}」· 对方在「${info.target_pos}」`
      + (info.target_hands ? ` · 对方双手：${info.target_hands}` : "");
    const btns = document.createElement("div");
    btns.style.cssText = "display:flex;gap:6px;";
    if (!samePos) {
      const bMove = document.createElement("button");
      bMove.className = "btn btn-accent";
      bMove.textContent = `🚶 走到「${info.target_pos}」身旁`;
      bMove.addEventListener("click", async () => {
        closeModal();
        await doAction({ type: "move_near", target_pos: info.target_pos });
        openActorModal(actorId, actorName);
      });
      btns.appendChild(bMove);
    }
    top.append(meta, btns);
    body.appendChild(top);

    // 切换当前正在安排动作的手（左手 / 右手各自独立，可同时选）
    const handRow = document.createElement("div");
    handRow.style.cssText = "display:flex;align-items:center;gap:12px;flex-wrap:wrap;";
    handRow.innerHTML = `<span class="field-label" style="margin:0;">当前设置：</span>`;
    const handDefs = [
      { id: "left", label: "左手" + handSuffix(info.hands.left.state), disabled: !samePos || leftBound },
      { id: "right", label: "右手" + handSuffix(info.hands.right.state), disabled: !samePos || rightBound },
    ];
    for (const ch of handDefs) {
      const lbl = document.createElement("label");
      lbl.className = "radio-opt" + (ch.disabled ? " disabled" : "");
      const inp = document.createElement("input");
      inp.type = "radio"; inp.name = "m_hand"; inp.value = ch.id;
      inp.disabled = ch.disabled;
      inp.checked = activeHand === ch.id;
      inp.addEventListener("change", () => {
        activeHand = ch.id;
        syncTable();
      });
      lbl.append(inp, document.createTextNode(ch.label));
      handRow.appendChild(lbl);
    }
    const tip = document.createElement("span");
    tip.className = "dim-tip";
    tip.textContent = "（切左手/右手各选一项，目光注视可自由多选共存）";
    handRow.appendChild(tip);
    body.appendChild(handRow);

    const summaryEl = document.createElement("div");
    summaryEl.className = "sel-summary";
    body.appendChild(summaryEl);

    if (!samePos) {
      const warn = document.createElement("div");
      warn.style.cssText = "color:var(--danger);font-size:12px;";
      warn.textContent = `你与 ${actorName} 不在同一位置，请先点击「走到「${info.target_pos}」身旁」再使用手部动作（当前仍可勾选「目光注视」）。`;
      body.appendChild(warn);
    } else if (leftBound && rightBound) {
      const warn = document.createElement("div");
      warn.style.cssText = "color:var(--danger);font-size:12px;";
      warn.textContent = "双手正被束缚，当前仅可勾选「目光注视」。";
      body.appendChild(warn);
    }

    const rowRefs = [];
    const makeRow = (p) => {
      const isGaze = p.label === "目光注视";
      const disabled = !p.allowed || (!isGaze && handsBlocked);
      const row = document.createElement("label");
      row.className = "part-row" + (disabled ? " disabled" : "");
      const left = document.createElement("span");
      left.className = "part-row-left";
      const chk = document.createElement("input");
      chk.type = "checkbox";
      chk.disabled = disabled;
      chk.addEventListener("change", () => {
        if (isGaze) {
          gazeChecked = chk.checked;
        } else if (activeHand) {
          if (chk.checked) {
            handPart[activeHand] = p.label;
          } else if (handPart[activeHand] === p.label) {
            handPart[activeHand] = "";
          }
        }
        syncTable();
      });
      const txt = document.createElement("span");
      txt.textContent = p.allowed ? p.label : `${p.label} (未解锁)`;
      left.append(chk, txt);
      const tags = document.createElement("span");
      tags.className = "part-tags";
      row.append(left, tags);
      rowRefs.push({ p, isGaze, disabled, row, chk, tags });
      return row;
    };

    const syncTable = () => {
      for (const r of rowRefs) {
        r.tags.innerHTML = "";
        let anyChecked = false;
        if (r.isGaze) {
          anyChecked = gazeChecked;
          r.chk.checked = gazeChecked;
          if (gazeChecked) {
            const tg = document.createElement("span");
            tg.className = "part-tag gaze";
            tg.textContent = "目光";
            r.tags.appendChild(tg);
          }
        } else {
          const hasL = handPart.left === r.p.label;
          const hasR = handPart.right === r.p.label;
          anyChecked = hasL || hasR;
          r.chk.checked = activeHand ? (handPart[activeHand] === r.p.label) : false;
          if (hasL) {
            const tg = document.createElement("span");
            tg.className = "part-tag";
            tg.textContent = "左手";
            r.tags.appendChild(tg);
          }
          if (hasR) {
            const tg = document.createElement("span");
            tg.className = "part-tag";
            tg.textContent = "右手";
            r.tags.appendChild(tg);
          }
        }
        r.row.className = "part-row" + (r.disabled ? " disabled" : "") + (anyChecked ? " checked" : "");
      }
      const cVal = customPartInput ? customPartInput.value.trim() : "";
      const lTxt = (activeHand === "left" && cVal) ? cVal : (handPart.left || "未选");
      const rTxt = (activeHand === "right" && cVal) ? cVal : (handPart.right || "未选");
      summaryEl.innerHTML =
        `<span>目光：<b>${gazeChecked ? "目光注视" : "未选"}</b></span>` +
        `<span>左手：<b>${lTxt}</b></span>` +
        `<span>右手：<b>${rTxt}</b></span>`;
    };

    // 部位多选表格
    const pTitle = document.createElement("div");
    pTitle.className = "field-label";
    pTitle.textContent = "选择互动部位（多选表格：目光 + 左手 + 右手可共存）：";
    body.appendChild(pTitle);
    const grid = document.createElement("div");
    grid.className = "part-table";
    for (const p of info.parts) {
      grid.appendChild(makeRow(p));
    }
    body.appendChild(grid);

    if (info.pos_parts && info.pos_parts.length) {
      const pt = document.createElement("div");
      pt.className = "field-label";
      pt.style.color = "var(--accent)";
      pt.textContent = "基于当前位置的互动：";
      body.appendChild(pt);
      const pg = document.createElement("div");
      pg.className = "part-table";
      for (const p of info.pos_parts) {
        pg.appendChild(makeRow(p));
      }
      body.appendChild(pg);
    }

    const cRow = document.createElement("div");
    cRow.style.cssText = "display:flex;align-items:center;gap:8px;";
    const cLbl = document.createElement("span");
    cLbl.className = "dim-tip";
    cLbl.textContent = info.custom_ok ? "或为当前手自定义部位：" : "自定义部位 (未解锁)：";
    customPartInput = document.createElement("input");
    customPartInput.className = "input";
    customPartInput.style.flex = "1";
    customPartInput.disabled = !info.custom_ok || handsBlocked;
    customPartInput.addEventListener("input", syncTable);
    cRow.append(cLbl, customPartInput);
    body.appendChild(cRow);
    syncTable();

    const secLbl = document.createElement("label");
    secLbl.className = "radio-opt";
    secLbl.style.color = "var(--text-dim)";
    secretCheck = document.createElement("input");
    secretCheck.type = "checkbox";
    secLbl.append(secretCheck, document.createTextNode(`隐秘进行（仅你与 ${actorName} 知晓，房间内其他人看不见）`));
    body.appendChild(secLbl);

    const tLbl = document.createElement("div");
    tLbl.className = "field-label";
    tLbl.textContent = "输入具体互动文本（可留空）：";
    textArea = document.createElement("textarea");
    textArea.className = "textarea";
    textArea.placeholder = "可留空，直接按所选部位互动…";
    body.append(tLbl, textArea);
    setTimeout(() => textArea.focus(), 30);
  }, [
    { label: "取消", onClick: closeModal },
    {
      label: "加入待发动作", accent: true,
      onClick: async () => {
        const text = textArea.value.trim();
        const secret = !!secretCheck.checked;
        const cVal = (info.custom_ok && customPartInput.value.trim()) ? customPartInput.value.trim() : "";
        const leftPart = (activeHand === "left" && cVal) ? cVal : handPart.left;
        const rightPart = (activeHand === "right" && cVal) ? cVal : handPart.right;
        const actions = [];
        if (gazeChecked) {
          actions.push({ hand: "gaze", target_id: actorId, target_name: actorName, part: "目光注视", text, secret });
        }
        if (leftPart && !leftBound) {
          actions.push({ hand: "left", target_id: actorId, target_name: actorName, part: leftPart, text, secret });
        }
        if (rightPart && !rightBound) {
          actions.push({ hand: "right", target_id: actorId, target_name: actorName, part: rightPart, text, secret });
        }
        if (!actions.length) {
          showToast("请至少勾选一项互动（目光 / 左手 / 右手）。", true);
          return;
        }
        const r = await doAction({ type: "queue_actions", actions });
        if (r.ok) closeModal();
      },
    },
  ]);
}

/* 自定义设施互动弹窗（支持多选左手 / 右手） */
function openFacilityModal(hs) {
  const hands = STATE ? STATE.hands : { left: { state: "free" }, right: { state: "free" } };
  const handBusy = (st) => (st === "bound" || st === "held");
  const leftBound = handBusy(hands.left.state);
  const rightBound = handBusy(hands.right.state);
  const checkedHands = { left: !leftBound, right: false };
  if (leftBound && !rightBound) checkedHands.right = true;
  let textArea = null;

  openModal(`互动设施：${hs.label}`, (body) => {
    if (hs.desc) {
      const desc = document.createElement("div");
      desc.className = "dim-tip";
      desc.textContent = hs.desc;
      body.appendChild(desc);
    }
    const hTitle = document.createElement("div");
    hTitle.className = "field-label";
    hTitle.textContent = "选择使用的手（可多选，同时用左手和右手）：";
    body.appendChild(hTitle);

    const grid = document.createElement("div");
    grid.className = "part-table";
    const channels = [
      { id: "left", label: "左手" + handSuffix(hands.left.state), disabled: leftBound },
      { id: "right", label: "右手" + handSuffix(hands.right.state), disabled: rightBound },
    ];
    for (const ch of channels) {
      const row = document.createElement("label");
      row.className = "part-row" + (ch.disabled ? " disabled" : "") + (checkedHands[ch.id] ? " checked" : "");
      const left = document.createElement("span");
      left.className = "part-row-left";
      const inp = document.createElement("input");
      inp.type = "checkbox";
      inp.disabled = ch.disabled;
      inp.checked = checkedHands[ch.id];
      inp.addEventListener("change", () => {
        checkedHands[ch.id] = inp.checked;
        row.className = "part-row" + (ch.disabled ? " disabled" : "") + (inp.checked ? " checked" : "");
      });
      left.append(inp, document.createTextNode(ch.label));
      row.appendChild(left);
      grid.appendChild(row);
    }
    body.appendChild(grid);

    textArea = document.createElement("textarea");
    textArea.className = "textarea";
    textArea.placeholder = "输入互动动作（可留空）…";
    body.appendChild(textArea);
    setTimeout(() => textArea.focus(), 30);
  }, [
    { label: "取消", onClick: closeModal },
    {
      label: "加入待发动作", accent: true, disabled: leftBound && rightBound,
      onClick: async () => {
        const text = textArea.value.trim();
        const actions = [];
        for (const side of ["left", "right"]) {
          if (checkedHands[side] && !handBusy(hands[side].state)) {
            actions.push({
              hand: side,
              target_id: hs.id,
              target_name: hs.label,
              part: "设施",
              text,
              secret: false,
              is_facility: true,
            });
          }
        }
        if (!actions.length) {
          showToast("请至少勾选一只手。", true);
          return;
        }
        const r = await doAction({ type: "queue_actions", actions });
        if (r.ok) closeModal();
      },
    },
  ]);
}

/* 钥匙管理弹窗 */
function openKeysModal() {
  if (!STATE) return;
  openModal("🔑 钥匙与门禁管理", (body) => {
    const tip = document.createElement("div");
    tip.className = "dim-tip";
    tip.textContent = "每个人默认拥有自己房间的钥匙，公共空间无需钥匙。你可以把手里拥有的钥匙分发给其他人；门从内部反锁后即使有钥匙也打不开。";
    body.appendChild(tip);

    if (STATE.shareable_rooms.length && STATE.personas.length) {
      const box = document.createElement("div");
      box.className = "card-box";
      box.innerHTML = `<div class="field-label">分发我持有的钥匙</div>`;
      const row = document.createElement("div");
      row.style.cssText = "display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:6px;";
      const selR = document.createElement("select");
      selR.className = "select";
      for (const r of STATE.shareable_rooms) {
        const o = document.createElement("option");
        o.value = r.id; o.textContent = r.name;
        selR.appendChild(o);
      }
      const selP = document.createElement("select");
      selP.className = "select";
      for (const p of STATE.personas) {
        const o = document.createElement("option");
        o.value = p.id; o.textContent = p.name;
        selP.appendChild(o);
      }
      const btn = document.createElement("button");
      btn.className = "btn btn-accent";
      btn.textContent = "分发";
      btn.addEventListener("click", async () => {
        const r = await doAction({ type: "grant_key", room_id: selR.value, to_id: selP.value });
        if (r.ok) closeModal();
      });
      row.append(selR, document.createTextNode("分给"), selP, btn);
      box.appendChild(row);
      body.appendChild(box);
    }

    const title = document.createElement("div");
    title.className = "field-label";
    title.textContent = "房间门禁与当前位置总览：";
    body.appendChild(title);
    for (const r of STATE.rooms_overview) {
      const line = document.createElement("div");
      line.className = "dim-tip";
      line.style.lineHeight = "1.5";
      line.textContent = r;
      body.appendChild(line);
    }

    const delTitle = document.createElement("div");
    delTitle.className = "field-label";
    delTitle.textContent = "删除房间（只有自建房间能删；断开的子房间会自动清理）：";
    body.appendChild(delTitle);
    for (const r of (STATE.rooms || []).filter(x => x.deletable)) {
      const row = document.createElement("div");
      row.className = "row-between";
      row.style.cssText = "gap:8px;margin-bottom:4px;";
      const nameEl = document.createElement("span");
      nameEl.textContent = r.name;
      const del = document.createElement("button");
      del.className = "btn";
      del.textContent = "删除";
      del.addEventListener("click", async () => {
        if (!window.confirm(`确定删除「${r.name}」？里面的人会回到公共客厅。`)) return;
        const res = await doAction({ type: "delete_room", room_id: r.id });
        if (res.ok) closeModal();
      });
      row.append(nameEl, del);
      body.appendChild(row);
    }
    const newBtn = document.createElement("button");
    newBtn.className = "btn";
    newBtn.textContent = "＋ 协商新建房间…";
    newBtn.addEventListener("click", () => { closeModal(); openCreateRoomModal(); });
    body.appendChild(newBtn);
  }, [{ label: "关闭", onClick: closeModal }]);
}

/* 协商建房弹窗 */
function openCreateRoomModal() {
  if (STATE && STATE.generating_map) {
    showToast("正在生成地图，请稍候…");
    return;
  }
  let inpTheme = null, txtDesc = null, chkPub = null;
  openModal("协商新建字符画房间", (body) => {
    const tip = document.createElement("div");
    tip.className = "dim-tip";
    tip.textContent = "输入你想和 AI 协商的主题与场景细节，AI 会为你生成一张可点击的字符画地图。";
    body.appendChild(tip);

    const l1 = document.createElement("div");
    l1.className = "field-label";
    l1.textContent = "房间主题（如：深夜天文台、雨夜旧书店）：";
    inpTheme = document.createElement("input");
    inpTheme.className = "input";
    body.append(l1, inpTheme);

    const l2 = document.createElement("div");
    l2.className = "field-label";
    l2.textContent = "场景设定与想要包含的设施：";
    txtDesc = document.createElement("textarea");
    txtDesc.className = "textarea";
    body.append(l2, txtDesc);

    const pubLbl = document.createElement("label");
    pubLbl.className = "radio-opt";
    chkPub = document.createElement("input");
    chkPub.type = "checkbox";
    pubLbl.append(chkPub, document.createTextNode("设为公共空间（所有人无需钥匙皆可进入；否则默认归创建者持有钥匙）"));
    body.appendChild(pubLbl);
    setTimeout(() => inpTheme.focus(), 30);
  }, [
    { label: "取消", onClick: closeModal },
    {
      label: "由 AI 生成字符画地图", accent: true,
      onClick: async () => {
        const theme = inpTheme.value.trim();
        if (!theme) { showToast("请先输入房间主题", true); return; }
        const r = await doAction({
          type: "create_room",
          theme,
          desc: txtDesc.value.trim(),
          is_public: !!chkPub.checked,
        });
        if (r.ok) closeModal();
      },
    },
  ]);
}

/* 顶栏与基础控件事件 */
document.getElementById("btnBack").addEventListener("click", () => doAction({ type: "back" }));
document.getElementById("autoStatus").addEventListener("click", () => doAction({ type: "open_settings" }));
document.getElementById("roomSelect").addEventListener("change", (e) => {
  doAction({ type: "switch_room", room_id: e.target.value });
});
document.getElementById("btnKeys").addEventListener("click", openKeysModal);
document.getElementById("btnCreate").addEventListener("click", openCreateRoomModal);
document.getElementById("leftHand").addEventListener("click", () => {
  if (STATE && STATE.hands && STATE.hands.left.state === "holding") {
    doAction({ type: "release_hand", side: "left" });
  }
});
document.getElementById("rightHand").addEventListener("click", () => {
  if (STATE && STATE.hands && STATE.hands.right.state === "holding") {
    doAction({ type: "release_hand", side: "right" });
  }
});
document.querySelectorAll('input[name="vol"]').forEach(rad => {
  rad.addEventListener("change", () => {
    if (rad.checked) doAction({ type: "set_volume", volume: rad.value });
  });
});

async function sendNow() {
  const el = document.getElementById("entry");
  const text = el.value.trim();
  if (!text && (!STATE || !STATE.pending_actions.length)) return;
  el.value = "";
  await doAction({ type: "send", text });
}
document.getElementById("btnSend").addEventListener("click", sendNow);
document.getElementById("entry").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendNow();
  }
});

/* 启动 SSE + 首屏拉取 */
fetchState();
const es = new EventSource(apiUrl("/api/events"));
es.addEventListener("update", () => fetchState());
</script>
</body>
</html>
"""
