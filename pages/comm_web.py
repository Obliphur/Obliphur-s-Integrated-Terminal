# -*- coding: utf-8 -*-
"""沟通页面本地 WebView2 服务与纯本地单页前端（零外部依赖，支持离线运行）。"""

import http.server
import json
import mimetypes
import os
import queue
import secrets
import threading
import urllib.parse
from typing import Callable


class CommWebServer:
    """仅监听 127.0.0.1 随机端口的沟通页本地 HTTP + SSE 服务。"""

    def __init__(
        self,
        get_state: Callable[[int], dict],
        handle_action: Callable[[dict], dict],
        resolve_media: Callable[[str, str], str],
    ):
        self._get_state = get_state
        self._handle_action = handle_action
        self._resolve_media = resolve_media
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

            def _check_token(self) -> tuple[bool, urllib.parse.ParseResult, dict]:
                parsed = urllib.parse.urlparse(self.path)
                qs = urllib.parse.parse_qs(parsed.query)
                tok = (qs.get("token") or [""])[0]
                if not tok:
                    auth = self.headers.get("Authorization") or ""
                    if auth.startswith("Bearer "):
                        tok = auth[7:].strip()
                return (bool(tok) and secrets.compare_digest(tok, srv_ref.token)), parsed, qs

            def _send_bytes(self, code: int, body: bytes, content_type: str, cache: str = "no-store"):
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", cache)
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    pass

            def _send_json(self, code: int, payload: dict):
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self._send_bytes(code, raw, "application/json; charset=utf-8")

            def do_GET(self):
                ok, parsed, qs = self._check_token()
                if not ok:
                    self._send_json(403, {"ok": False, "error": "forbidden"})
                    return
                path = parsed.path
                if path in ("/", "/index.html"):
                    html = _COMM_HTML.replace("__TOKEN__", json.dumps(srv_ref.token))
                    self._send_bytes(200, html.encode("utf-8"), "text/html; charset=utf-8")
                    return
                if path == "/api/state":
                    try:
                        limit = int((qs.get("limit") or ["80"])[0])
                    except ValueError:
                        limit = 80
                    limit = max(20, min(2000, limit))
                    try:
                        data = srv_ref._get_state(limit)
                        self._send_json(200, {"ok": True, "state": data})
                    except Exception as exc:
                        self._send_json(500, {"ok": False, "error": str(exc)})
                    return
                if path == "/api/media":
                    kind = (qs.get("kind") or [""])[0]
                    name = (qs.get("name") or [""])[0]
                    try:
                        fpath = srv_ref._resolve_media(kind, name)
                    except Exception:
                        fpath = ""
                    if not fpath or not os.path.isfile(fpath):
                        self._send_json(404, {"ok": False, "error": "not_found"})
                        return
                    try:
                        with open(fpath, "rb") as f:
                            raw = f.read()
                        ctype = mimetypes.guess_type(fpath)[0] or "application/octet-stream"
                        self._send_bytes(200, raw, ctype, cache="private, max-age=60")
                    except OSError:
                        self._send_json(404, {"ok": False, "error": "read_failed"})
                    return
                if path == "/api/events":
                    self._handle_sse()
                    return
                self._send_json(404, {"ok": False, "error": "not_found"})

            def do_POST(self):
                ok, parsed, _qs = self._check_token()
                if not ok:
                    self._send_json(403, {"ok": False, "error": "forbidden"})
                    return
                if parsed.path != "/api/action":
                    self._send_json(404, {"ok": False, "error": "not_found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length") or "0")
                    if length < 0 or length > 10 * 1024 * 1024:
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
                q: queue.Queue = queue.Queue(maxsize=64)
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
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="oit-comm-web")
        self._thread.start()
        return self.url

    def notify(self, event: str = "update", data: dict | None = None):
        payload = f"event: {event}\ndata: {json.dumps(data or {}, ensure_ascii=False)}\n\n".encode("utf-8")
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


_COMM_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>OIT 沟通</title>
<style>
  :root {
    --bg: #0f131b;
    --bg-card: #171c28;
    --border: #2a3346;
    --text: #e6edf7;
    --text-dim: #8b97ad;
    --accent: #67d5f7;
    --accent-hover: #8be0fa;
    --accent-fill: #2563eb;
    --on-accent-fill: #ffffff;
    --danger: #ef4444;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0; width: 100%; height: 100%;
    overflow: hidden;
    background: var(--bg);
    color: var(--text);
    font-family: "Microsoft YaHei UI", "Segoe UI", system-ui, -apple-system, sans-serif;
    font-size: 13.5px;
    user-select: none;
    -webkit-font-smoothing: antialiased;
  }
  .app {
    display: flex; flex-direction: column;
    width: 100%; height: 100%;
    padding: 16px 26px 14px;
    gap: 6px;
    position: relative;
  }

  /* 顶栏 */
  .hdr {
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
    min-height: 36px;
  }
  .hdr-title {
    font-size: 20px; font-weight: 700; letter-spacing: 0.3px; white-space: nowrap;
  }
  .hdr-right {
    display: flex; align-items: center; gap: 8px; flex-wrap: nowrap;
  }
  .select {
    background: var(--bg-card); color: var(--text);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 5px 9px; font-size: 12.5px; font-family: inherit;
    outline: none; cursor: pointer; max-width: 190px;
  }
  .select:focus { border-color: var(--accent); }
  .btn {
    appearance: none; border: 1px solid var(--border);
    background: var(--bg-card); color: var(--text);
    padding: 5px 11px; border-radius: 6px;
    font-size: 12.5px; cursor: pointer; font-family: inherit;
    transition: background .12s, border-color .12s, transform .08s;
    white-space: nowrap;
  }
  .btn:hover:not(:disabled) { border-color: var(--accent); background: rgba(127,127,127,0.12); }
  .btn:active:not(:disabled) { transform: translateY(1px); }
  .btn:disabled { opacity: 0.45; cursor: not-allowed; }
  .btn-accent {
    background: var(--accent-fill); color: var(--on-accent-fill);
    border-color: var(--accent-fill); font-weight: 600;
  }
  .btn-accent:hover:not(:disabled) { filter: brightness(1.08); }
  .btn-icon {
    padding: 4px 8px; min-width: 32px; text-align: center; font-size: 14px;
  }

  /* 状态小行 */
  .sub-meta {
    display: flex; flex-direction: column; gap: 2px; min-height: 0;
  }
  .other-row {
    display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-dim);
  }
  .other-dot { font-size: 11px; line-height: 1; }
  .tension-row {
    font-size: 12px; color: var(--text-dim);
  }

  /* 消息流 */
  .chat-wrap {
    flex: 1; min-height: 0;
    overflow-y: auto;
    padding: 6px 4px 8px;
    display: flex; flex-direction: column; gap: 10px;
  }
  .load-more {
    align-self: center;
    font-size: 12px; color: var(--text-dim);
    background: transparent; border: 1px dashed var(--border);
    border-radius: 14px; padding: 3px 14px; cursor: pointer;
    margin-bottom: 4px;
  }
  .load-more:hover { color: var(--accent); border-color: var(--accent); }

  .msg-row {
    display: flex; align-items: flex-start; gap: 9px;
    width: 100%;
  }
  .msg-row.mine { flex-direction: row-reverse; }

  .avatar {
    width: 36px; height: 36px; flex-shrink: 0;
    border-radius: 6px; border: 1px solid var(--border);
    background: var(--bg-card); color: var(--text);
    display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 14px; overflow: hidden;
  }
  .avatar.mine { background: var(--accent-fill); color: var(--on-accent-fill); }
  .avatar.clickable { cursor: pointer; }
  .avatar img { width: 100%; height: 100%; object-fit: cover; display: block; }

  .msg-col {
    display: flex; flex-direction: column; max-width: 72%; min-width: 0;
    align-items: flex-start;
  }
  .msg-row.mine .msg-col { align-items: flex-end; }

  .bubble {
    background: var(--bg-card); color: var(--text);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 9px 13px;
    line-height: 1.55;
    white-space: pre-wrap; word-break: break-word;
    user-select: text;
    position: relative;
  }
  .msg-row.mine .bubble {
    background: var(--accent-fill); color: var(--on-accent-fill);
    border-color: transparent;
  }
  .bubble-img {
    display: block; max-width: 240px; max-height: 240px;
    border-radius: 6px; cursor: pointer; margin-bottom: 6px;
    object-fit: contain;
  }
  .bubble-img.only { margin-bottom: 0; }
  .bubble-text { white-space: pre-wrap; word-break: break-word; }

  .kb-list {
    display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px;
  }
  .kb-btn {
    background: transparent; border: none; padding: 2px 0;
    color: var(--accent); font-size: 12px; cursor: pointer; font-family: inherit;
    text-align: left;
  }
  .kb-btn:hover { text-decoration: underline; }

  .voice-btn {
    margin-top: 4px; background: transparent; border: none;
    color: var(--text-dim); cursor: pointer; font-size: 12px; padding: 0 2px;
  }
  .voice-btn:hover { color: var(--accent); }

  .msg-time {
    font-size: 11px; color: var(--text-dim); margin-top: 3px; padding: 0 2px;
  }
  .msg-row.highlight .bubble {
    box-shadow: 0 0 0 2px var(--accent);
  }

  /* 状态错误提示 */
  .status-line {
    font-size: 12px; color: var(--text-dim); min-height: 16px;
  }
  .status-line.danger { color: var(--danger); }

  /* 工具栏与邀请条 */
  .tools-bar {
    display: flex; align-items: center; gap: 5px; flex-wrap: wrap;
  }
  .invite-chip {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 3px 8px; border-radius: 6px;
    border: 1px solid var(--border); background: var(--bg-card);
    font-size: 12px; color: var(--accent); margin-left: 4px;
  }
  .invite-chip .btn { padding: 2px 8px; font-size: 12px; }

  /* 草稿行 */
  .draft-row {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-height: 0;
  }
  .draft-text { font-size: 12px; color: var(--text-dim); }
  .draft-text.active { color: var(--accent); }
  .draft-x {
    cursor: pointer; color: var(--text-dim); font-size: 12px; padding: 0 4px;
  }
  .draft-x:hover { color: var(--danger); }
  .pending-thumb-wrap {
    display: inline-flex; align-items: center; gap: 5px;
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: 6px; padding: 3px 6px;
  }
  .pending-thumb {
    max-width: 68px; max-height: 68px; border-radius: 4px; display: block; object-fit: cover;
  }

  /* 输入区 */
  .inp-row {
    display: flex; gap: 8px; align-items: flex-end;
  }
  .entry {
    flex: 1; min-height: 64px; max-height: 140px; resize: none;
    background: var(--bg-card); color: var(--text);
    border: 1px solid var(--border); border-radius: 8px;
    padding: 8px 11px; font-family: inherit; font-size: 13.5px; line-height: 1.48;
    outline: none;
  }
  .entry:focus { border-color: var(--accent); }
  .entry:disabled { opacity: 0.5; cursor: not-allowed; }

  /* 共读独占遮罩 */
  .reading-lock {
    position: absolute; inset: 0; z-index: 800;
    background: var(--bg);
    display: none; flex-direction: column; align-items: center; justify-content: center;
    gap: 18px; padding: 28px; text-align: center;
  }
  .reading-lock.open { display: flex; }
  .reading-lock-title { font-size: 17px; font-weight: 700; max-width: 520px; line-height: 1.5; }

  /* 大图灯箱 */
  .lightbox {
    position: fixed; inset: 0; z-index: 1000;
    background: rgba(0,0,0,0.78);
    display: none; align-items: center; justify-content: center; padding: 24px;
    cursor: zoom-out;
  }
  .lightbox.open { display: flex; }
  .lightbox img {
    max-width: 92vw; max-height: 90vh; border-radius: 8px;
    box-shadow: 0 18px 50px rgba(0,0,0,0.6); cursor: default;
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
  }
  .toast.warn { border-color: #f59e0b; color: #fde68a; }

  ::-webkit-scrollbar { width: 7px; height: 7px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: rgba(139,151,173,0.28); border-radius: 4px; }
  ::-webkit-scrollbar-thumb:hover { background: rgba(139,151,173,0.48); }
</style>
</head>
<body>
<div class="app">
  <div class="hdr">
    <div class="hdr-title" id="hdrTitle">💬  沟通</div>
    <div class="hdr-right">
      <select class="select" id="cfgSelect" title="AI 模型配置"></select>
      <select class="select" id="whoSelect" title="聊天对象"></select>
      <button class="btn" id="btnClear">清空</button>
    </div>
  </div>

  <div class="sub-meta">
    <div class="other-row" id="otherRow" style="display:none;">
      <span class="other-dot" id="otherDot">●</span>
      <span id="otherText"></span>
    </div>
    <div class="tension-row" id="tensionRow" style="display:none;"></div>
  </div>

  <div class="chat-wrap" id="chatWrap"></div>

  <div class="status-line" id="statusLine"></div>

  <div class="tools-bar" id="toolsBar">
    <button class="btn btn-icon" id="btnHelp" title="帮助">❓</button>
    <button class="btn btn-icon" id="btnRoom" title="小屋">🏠</button>
    <button class="btn btn-icon" id="btnMute" title="主动发起开关">🔔</button>
    <button class="btn btn-icon" id="btnJoint" title="联合行动">🤝</button>
    <button class="btn btn-icon" id="btnAlarm" title="共时闹钟">⏲</button>
    <button class="btn btn-icon" id="btnShift" title="起点平移">🕰</button>
    <button class="btn btn-icon" id="btnShare" title="分享数据">🙈</button>
    <button class="btn btn-icon" id="btnOcr" title="独立识字">🖼</button>
    <button class="btn btn-icon" id="btnImage" title="发送图片">📎</button>
    <button class="btn btn-icon" id="btnCamera" title="拍照">📷</button>
    <button class="btn btn-icon" id="btnReview" title="演奏复盘">🎼</button>
    <button class="btn btn-icon" id="btnCoread" title="阅读 / 共读">📚</button>
    <button class="btn btn-icon" id="btnConsent" title="同意声明" style="display:none;">🛡</button>
    <div id="invitesWrap" style="display:inline-flex;align-items:center;flex-wrap:wrap;gap:6px;"></div>
  </div>

  <div class="draft-row" id="draftRow"></div>

  <div class="inp-row">
    <textarea class="entry" id="entry" placeholder="输入消息（Enter 发送，Shift+Enter 换行，支持粘贴图片）…"></textarea>
    <button class="btn btn-accent" id="btnSend" style="height:36px;padding:0 16px;">发送</button>
  </div>

  <div class="reading-lock" id="readingLock">
    <div class="reading-lock-title" id="readingLockTitle">正在共读，请在阅读网页里的聊天框继续沟通。</div>
    <button class="btn btn-accent" id="btnEndReading">结束阅读并恢复沟通</button>
  </div>
</div>

<div class="lightbox" id="lightbox"><img id="lightboxImg" alt="" /></div>
<div class="toast-wrap" id="toastWrap"></div>

<script>
const TOKEN = __TOKEN__;
let STATE = null;
let curLimit = 80;
let lastMsgKey = "";
let lastPersonaKey = "";
let renderedIds = [];
let firstRender = true;

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

function openLightbox(url) {
  if (!url) return;
  document.getElementById("lightboxImg").src = url;
  document.getElementById("lightbox").classList.add("open");
}
document.getElementById("lightbox").addEventListener("click", (e) => {
  if (e.target.id === "lightbox") document.getElementById("lightbox").classList.remove("open");
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") document.getElementById("lightbox").classList.remove("open");
});

async function fetchState() {
  try {
    const r = await fetch(apiUrl("/api/state?limit=" + curLimit));
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
    return { ok: false };
  }
}

function applyTheme(c) {
  if (!c) return;
  const s = document.documentElement.style;
  if (c.bg) s.setProperty("--bg", c.bg);
  if (c.bg_card) s.setProperty("--bg-card", c.bg_card);
  if (c.border) s.setProperty("--border", c.border);
  if (c.text) s.setProperty("--text", c.text);
  if (c.text_dim) s.setProperty("--text-dim", c.text_dim);
  if (c.accent) s.setProperty("--accent", c.accent);
  if (c.accent_hover) s.setProperty("--accent-hover", c.accent_hover);
  if (c.accent_fill) s.setProperty("--accent-fill", c.accent_fill);
  if (c.on_accent_fill) s.setProperty("--on-accent-fill", c.on_accent_fill);
  if (c.danger) s.setProperty("--danger", c.danger);
}

function syncSelect(el, options, current) {
  const sig = options.map(o => o.value + "|" + o.label).join("\n");
  if (el.dataset.sig !== sig) {
    el.dataset.sig = sig;
    el.innerHTML = "";
    for (const o of options) {
      const opt = document.createElement("option");
      opt.value = o.value;
      opt.textContent = o.label;
      el.appendChild(opt);
    }
  }
  if (document.activeElement !== el && el.value !== current) {
    el.value = current;
  }
}

function buildMsgRow(m, st) {
  const mine = m.role === "me";
  const row = document.createElement("div");
  row.className = "msg-row" + (mine ? " mine" : "");
  row.dataset.msgId = String(m.id);

  if (st.show_avatar) {
    const av = document.createElement("div");
    av.className = "avatar" + (mine ? " mine" : "") + (!mine && st.ai_avatar_url ? " clickable" : "");
    const url = mine ? st.me_avatar_url : st.ai_avatar_url;
    if (url) {
      const img = document.createElement("img");
      img.src = url;
      av.appendChild(img);
      if (!mine) {
        av.addEventListener("click", () => openLightbox(st.ai_full_avatar_url || url));
      }
    } else {
      av.textContent = mine ? st.me_char : st.ai_char;
    }
    row.appendChild(av);
  }

  const col = document.createElement("div");
  col.className = "msg-col";

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  if (m.img_url) {
    const img = document.createElement("img");
    img.className = "bubble-img" + (m.text === "[图片]" ? " only" : "");
    img.src = m.img_url;
    img.loading = "lazy";
    img.addEventListener("click", () => openLightbox(m.img_url));
    bubble.appendChild(img);
  }
  if (!m.img_url || m.text !== "[图片]") {
    const txt = document.createElement("div");
    txt.className = "bubble-text";
    txt.textContent = m.text || "";
    bubble.appendChild(txt);
  }

  if (!mine && m.kb_citations && m.kb_citations.length) {
    const kb = document.createElement("div");
    kb.className = "kb-list";
    for (const cit of m.kb_citations) {
      const b = document.createElement("button");
      b.className = "kb-btn";
      b.textContent = (st.labels.trace_prefix || "结论溯源: ") + cit.title;
      b.addEventListener("click", () => doAction({ type: "open_trace", card_id: cit.card_id }));
      kb.appendChild(b);
    }
    bubble.appendChild(kb);
  }

  if (!mine && m.voice_enabled) {
    const vb = document.createElement("button");
    vb.className = "voice-btn";
    vb.textContent = "▶";
    vb.title = "播放语音";
    vb.addEventListener("click", () => doAction({ type: "voice_play", msg_id: m.id }));
    bubble.appendChild(vb);
  }

  col.appendChild(bubble);
  if (m.time) {
    const tm = document.createElement("div");
    tm.className = "msg-time";
    tm.textContent = m.time;
    col.appendChild(tm);
  }
  row.appendChild(col);
  return row;
}

function renderMessages(st) {
  const wrap = document.getElementById("chatWrap");
  const msgs = st.messages || [];
  const pKey = `${st.who_current}|${st.show_avatar}|${st.me_avatar_url}|${st.ai_avatar_url}|${st.voice_enabled}`;
  const ids = msgs.map(m => m.id);
  const sig = pKey + "|" + st.has_more + "|" + ids.join(",") + "|" + (msgs.length ? msgs[msgs.length - 1].text : "");
  if (sig === lastMsgKey) return;

  const atBottom = firstRender || (wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight) < 80;
  const oldHeight = wrap.scrollHeight;
  const oldTop = wrap.scrollTop;

  // 同人设且仅尾部追加了新消息：增量 appendChild，零闪烁
  const samePersona = (pKey === lastPersonaKey);
  const isAppend = samePersona && renderedIds.length > 0 && ids.length >= renderedIds.length
    && renderedIds.every((id, i) => ids[i] === id);

  if (isAppend && ids.length > renderedIds.length) {
    const frag = document.createDocumentFragment();
    for (let i = renderedIds.length; i < msgs.length; i++) {
      frag.appendChild(buildMsgRow(msgs[i], st));
    }
    wrap.appendChild(frag);
    renderedIds = ids;
    lastMsgKey = sig;
    if (atBottom) wrap.scrollTop = wrap.scrollHeight;
    return;
  }

  // 全量渲染（切换对象 / 清空 / 加载更早历史）
  const wasPrepend = samePersona && renderedIds.length > 0 && ids.length > renderedIds.length
    && ids[ids.length - 1] === renderedIds[renderedIds.length - 1];

  wrap.innerHTML = "";
  if (st.has_more) {
    const moreBtn = document.createElement("button");
    moreBtn.className = "load-more";
    moreBtn.textContent = "加载更早记录";
    moreBtn.addEventListener("click", () => {
      curLimit += 80;
      fetchState();
    });
    wrap.appendChild(moreBtn);
  }
  const frag = document.createDocumentFragment();
  for (const m of msgs) frag.appendChild(buildMsgRow(m, st));
  wrap.appendChild(frag);

  lastPersonaKey = pKey;
  renderedIds = ids;
  lastMsgKey = sig;

  if (wasPrepend) {
    wrap.scrollTop = oldTop + (wrap.scrollHeight - oldHeight);
  } else if (atBottom || !samePersona) {
    wrap.scrollTop = wrap.scrollHeight;
  }
  firstRender = false;
}

function applyState(st) {
  STATE = st;
  applyTheme(st.colors);

  document.getElementById("hdrTitle").textContent = st.labels.title;
  document.getElementById("btnClear").textContent = st.labels.clear;
  document.getElementById("btnSend").textContent = st.labels.send;
  document.getElementById("readingLockTitle").textContent = st.labels.reading_lock;
  document.getElementById("btnEndReading").textContent = st.labels.end_reading;

  syncSelect(document.getElementById("cfgSelect"), st.cfg_options, st.cfg_current);
  syncSelect(document.getElementById("whoSelect"), st.who_options, st.who_current);

  const otherRow = document.getElementById("otherRow");
  if (st.other_text) {
    otherRow.style.display = "flex";
    document.getElementById("otherDot").style.color = st.other_dot_color || "var(--text-dim)";
    document.getElementById("otherText").textContent = st.other_text;
  } else {
    otherRow.style.display = "none";
  }

  const tensionRow = document.getElementById("tensionRow");
  if (st.tension_text) {
    tensionRow.style.display = "block";
    tensionRow.textContent = st.tension_text;
  } else {
    tensionRow.style.display = "none";
  }

  const statusLine = document.getElementById("statusLine");
  statusLine.textContent = st.status_text || "";
  statusLine.className = "status-line" + (st.status_danger ? " danger" : "");

  document.getElementById("btnMute").textContent = st.muted ? "🔕" : "🔔";
  document.getElementById("btnAlarm").textContent = st.co_alarm_active ? "⏰" : "⏲";
  document.getElementById("btnShare").textContent = st.share_state === "on" ? "👀" : (st.share_state === "asking" ? "⏳" : "🙈");
  document.getElementById("btnShift").textContent = st.shift_asking ? "⏳" : "🕰";
  document.getElementById("btnConsent").style.display = st.show_consent_btn ? "" : "none";

  // 邀请条
  const invWrap = document.getElementById("invitesWrap");
  const invSig = JSON.stringify(st.invites);
  if (invWrap.dataset.sig !== invSig) {
    invWrap.dataset.sig = invSig;
    invWrap.innerHTML = "";
    for (const inv of st.invites) {
      const chip = document.createElement("div");
      chip.className = "invite-chip";
      if (inv.text) {
        const span = document.createElement("span");
        span.textContent = inv.text;
        chip.appendChild(span);
      }
      for (const b of inv.buttons) {
        const btn = document.createElement("button");
        btn.className = "btn";
        btn.textContent = b.label;
        btn.addEventListener("click", () => doAction(b.action));
        chip.appendChild(btn);
      }
      invWrap.appendChild(chip);
    }
  }

  // 草稿与待发图片
  const draftRow = document.getElementById("draftRow");
  const draftSig = JSON.stringify({
    t: st.draft_text, p: st.draft_is_pending, img: st.pending_image_url, lbl: st.pending_image_label
  });
  if (draftRow.dataset.sig !== draftSig) {
    draftRow.dataset.sig = draftSig;
    draftRow.innerHTML = "";
    if (st.draft_text) {
      const sp = document.createElement("span");
      sp.className = "draft-text" + (st.draft_is_pending ? " active" : "");
      sp.textContent = st.draft_text;
      draftRow.appendChild(sp);
      if (st.draft_is_pending) {
        const x = document.createElement("span");
        x.className = "draft-x";
        x.textContent = "✕";
        x.addEventListener("click", () => doAction({ type: "clear_package_drafts" }));
        draftRow.appendChild(x);
      }
    }
    if (st.pending_image_url || st.pending_image_label) {
      const box = document.createElement("div");
      box.className = "pending-thumb-wrap";
      if (st.pending_image_url) {
        const img = document.createElement("img");
        img.className = "pending-thumb";
        img.src = st.pending_image_url;
        box.appendChild(img);
      } else {
        const sp = document.createElement("span");
        sp.className = "draft-text";
        sp.textContent = st.pending_image_label;
        box.appendChild(sp);
      }
      const x = document.createElement("button");
      x.className = "btn";
      x.style.padding = "1px 6px";
      x.textContent = "✕";
      x.addEventListener("click", () => doAction({ type: "clear_pending_image" }));
      box.appendChild(x);
      draftRow.appendChild(box);
    }
  }

  const entry = document.getElementById("entry");
  entry.disabled = !!st.blocked;
  document.getElementById("btnSend").disabled = !!st.blocked;

  const lock = document.getElementById("readingLock");
  lock.classList.toggle("open", !!st.reading_mode);

  renderMessages(st);
}

/* 顶部与工具栏按钮绑定 */
document.getElementById("cfgSelect").addEventListener("change", (e) => doAction({ type: "pick_config", id: e.target.value }));
document.getElementById("whoSelect").addEventListener("change", (e) => {
  curLimit = 80;
  firstRender = true;
  doAction({ type: "pick_who", name: e.target.value });
});
document.getElementById("btnClear").addEventListener("click", () => doAction({ type: "clear" }));
document.getElementById("btnHelp").addEventListener("click", () => doAction({ type: "help" }));
document.getElementById("btnRoom").addEventListener("click", () => doAction({ type: "open_room" }));
document.getElementById("btnMute").addEventListener("click", () => doAction({ type: "toggle_mute" }));
document.getElementById("btnJoint").addEventListener("click", () => doAction({ type: "joint" }));
document.getElementById("btnAlarm").addEventListener("click", () => doAction({ type: "co_alarm" }));
document.getElementById("btnShift").addEventListener("click", () => doAction({ type: "shift" }));
document.getElementById("btnShare").addEventListener("click", () => doAction({ type: "toggle_share" }));
document.getElementById("btnOcr").addEventListener("click", () => doAction({ type: "ocr" }));
document.getElementById("btnImage").addEventListener("click", () => doAction({ type: "pick_image" }));
document.getElementById("btnCamera").addEventListener("click", () => doAction({ type: "take_photo" }));
document.getElementById("btnReview").addEventListener("click", () => doAction({ type: "review_performance" }));
document.getElementById("btnCoread").addEventListener("click", () => doAction({ type: "reading" }));
document.getElementById("btnConsent").addEventListener("click", () => doAction({ type: "open_consent" }));
document.getElementById("btnEndReading").addEventListener("click", () => doAction({ type: "end_reading" }));

async function sendNow() {
  const el = document.getElementById("entry");
  const text = el.value.trim();
  if (!text && !(STATE && (STATE.pending_image_url || STATE.pending_image_label))) return;
  const r = await doAction({ type: "send", text });
  if (r.ok) el.value = "";
}
document.getElementById("btnSend").addEventListener("click", sendNow);
document.getElementById("entry").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendNow();
  }
});

/* 支持在输入框直接 Ctrl+V 粘贴图片 */
document.getElementById("entry").addEventListener("paste", (e) => {
  const items = e.clipboardData && e.clipboardData.items;
  if (!items) return;
  for (const it of items) {
    if (it.type && it.type.startsWith("image/")) {
      const file = it.getAsFile();
      if (!file) continue;
      e.preventDefault();
      const reader = new FileReader();
      reader.onload = () => {
        doAction({ type: "paste_image_data", data_url: reader.result });
      };
      reader.readAsDataURL(file);
      return;
    }
  }
});

/* SSE 实时更新与滚动定位 */
fetchState();
const es = new EventSource(apiUrl("/api/events"));
es.addEventListener("update", () => fetchState());
es.addEventListener("scroll_to", (ev) => {
  try {
    const d = JSON.parse(ev.data || "{}");
    const row = document.querySelector(`.msg-row[data-msg-id="${d.msg_id}"]`);
    if (row) {
      row.scrollIntoView({ behavior: "smooth", block: "center" });
      row.classList.add("highlight");
      setTimeout(() => row.classList.remove("highlight"), 1400);
    }
  } catch (e) {}
});
</script>
</body>
</html>
"""
