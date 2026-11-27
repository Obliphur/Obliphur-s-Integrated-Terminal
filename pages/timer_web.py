# -*- coding: utf-8 -*-
"""⏱ Focus Timer 原生 WebView2 页面（与沟通页同款本地 HTTP + SSE 架构，零 Tk 残留）。"""

import json
import queue
import threading
import tkinter as tk
from typing import Callable

import config
from config import COLORS
from i18n import tr
from .webview2_host import LocalWebServer, WebView2Host, is_webview2_available


class _StateProxy:
    """轻量状态代理：替代旧 Tk 控件接收 app_timer / app_break / app_daily 的更新并自动推送到 WebView2。"""

    def __init__(self, on_change: Callable[[], None], **initial):
        self._on_change = on_change
        self._props: dict = dict(initial)
        self._manager: str = initial.pop("_manager", "")
        self._items: dict[int, dict] = {}
        self._next_item = 1

    def configure(self, **kw):
        changed = False
        for k, v in kw.items():
            if self._props.get(k) != v:
                self._props[k] = v
                changed = True
        if changed:
            self._on_change()

    config = configure

    def cget(self, key: str):
        return self._props.get(key, "")

    def __getitem__(self, key: str):
        return self.cget(key)

    def __setitem__(self, key: str, value):
        self.configure(**{key: value})

    def winfo_exists(self) -> bool:
        return True

    def winfo_manager(self) -> str:
        return self._manager

    def winfo_ismapped(self) -> bool:
        return bool(self._manager)

    def pack(self, **_kw):
        if self._manager != "pack":
            self._manager = "pack"
            self._on_change()

    def pack_forget(self):
        if self._manager:
            self._manager = ""
            self._on_change()

    def place(self, **_kw):
        if self._manager != "place":
            self._manager = "place"
            self._on_change()

    def place_forget(self):
        if self._manager:
            self._manager = ""
            self._on_change()

    def lift(self):
        return

    def bind(self, *_args, **_kw):
        return ""

    # Canvas 兼容接口（供 _set_dot 使用）
    def create_oval(self, *_args, **kw) -> int:
        iid = self._next_item
        self._next_item += 1
        self._items[iid] = dict(kw)
        self._on_change()
        return iid

    def itemconfig(self, iid: int, **kw):
        item = self._items.setdefault(iid, {})
        changed = False
        for k, v in kw.items():
            if item.get(k) != v:
                item[k] = v
                changed = True
        if changed:
            self._on_change()

    def item_prop(self, iid: int, key: str, default=""):
        return (self._items.get(iid) or {}).get(key, default)

    @property
    def disabled(self) -> bool:
        st = str(self._props.get("state") or "")
        return st == "disabled"


class TimerPage(tk.Frame):
    """独立计时器页面（Windows 优先 WebView2，无运行时自动降级 Tk）。"""

    def __init__(self, master, app):
        super().__init__(master, bg=COLORS["bg"])
        self.app = app
        self._destroyed = False
        self._visible = False
        self._use_webview = is_webview2_available()
        self._tk_built = False
        self._wv_host: WebView2Host | None = None
        self._wv_loading: tk.Label | None = None
        self._web_server: LocalWebServer | None = None
        self._ui_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._ui_poll_job = None
        self._wv_show_job = None
        self._notify_job = None

        self.bind("<Destroy>", self._on_destroy)
        if self._use_webview:
            self._init_proxies()
            self._build_webview()
        else:
            self._tk_built = True
            self.app._build_timer_page_tk(self)

    def _init_proxies(self):
        app = self.app
        cb = self._schedule_notify
        app.dot_canvas = _StateProxy(cb)
        app.dot_id = app.dot_canvas.create_oval(1, 1, 11, 11, fill=COLORS["text_dim"], outline="")
        app.lbl_status = _StateProxy(cb, text=tr("准备就绪"), fg=COLORS["text_dim"])
        app.lbl_time = _StateProxy(cb, text="0.00 min")
        app.lbl_sub = _StateProxy(cb, text=app._idle_sub_text())
        app.lbl_today = _StateProxy(cb, text=tr("今日: {h:.1f} h").format(h=0.0))
        app.lbl_peers = _StateProxy(cb, text="", fg=COLORS["text_dim"])
        app.btn_action = _StateProxy(cb, text=tr("▶  开始专注"), state="!disabled", _manager="pack")
        app.btn_break = _StateProxy(cb, text=tr("☕  开始休息"), state="disabled", _manager="pack")
        app.btn_pause = _StateProxy(cb, text=tr("⏸  暂停专注"), state="disabled", _manager="pack")
        app.btn_sleep = _StateProxy(cb, text=tr("😴  睡眠"), state="!disabled", _manager="pack")
        app.btn_prestart = _StateProxy(cb, text=tr("⏩  提前启动"), state="!disabled", _manager="")
        app.btn_outing = _StateProxy(cb, text=tr("🚪  外出"), state="!disabled", _manager="pack")
        app.lbl_bonus = _StateProxy(cb, text="", _manager="")
        app._bonus_show(app._bonus_idle_text())
        app._sync_timer_buttons()
        app._sync_bonus_button()
        app._set_dot(COLORS["text_dim"])

    def _build_webview(self):
        self._web_server = LocalWebServer(
            html_provider=lambda tok: _TIMER_HTML.replace("__TOKEN__", json.dumps(tok)),
            get_state=lambda: self._call_on_ui(self._build_web_state),
            handle_action=lambda body: self._call_on_ui(lambda: self._handle_web_action(body)),
            thread_name="oit-timer-web",
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
            text=tr("正在加载计时器界面…"),
            bg=COLORS["bg"],
            fg=COLORS["text_dim"],
            font=config.font("body"),
        )
        self._wv_loading.place(relx=0.5, rely=0.5, anchor="center")

    def _schedule_notify(self):
        if self._destroyed or not self._use_webview or self._web_server is None:
            return
        if self._notify_job is not None:
            return
        try:
            self._notify_job = self.after(16, self._flush_notify)
        except tk.TclError:
            self._notify_job = None

    def _flush_notify(self):
        self._notify_job = None
        if not self._destroyed and self._web_server is not None:
            self._web_server.notify("update")

    def _start_webview_after_show(self):
        self._wv_show_job = None
        if self._destroyed or not self._visible or not self._use_webview:
            return
        if self._wv_host is None or self._web_server is None:
            return
        url = self._web_server.start()
        self._wv_host.lift()
        self._wv_host.start(url)
        self._wv_host.set_visible(True)
        self.after(120, self._sync_webview_visible)
        self._web_server.notify("update")

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
                self.after(200, self._sync_webview_visible)

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
            self._tk_built = True
            self.app._build_timer_page_tk(self)
            try:
                self.app.refresh_display()
            except Exception:
                pass

    def show(self):
        self._visible = True
        if self._use_webview:
            if self._wv_show_job is not None:
                try:
                    self.after_cancel(self._wv_show_job)
                except tk.TclError:
                    pass
            self._wv_show_job = self.after(60, self._start_webview_after_show)
            self._schedule_notify()

    def hide(self):
        self._visible = False
        if self._wv_show_job is not None:
            try:
                self.after_cancel(self._wv_show_job)
            except tk.TclError:
                pass
            self._wv_show_job = None
        if self._wv_host is not None:
            self._wv_host.set_visible(False)

    def _on_destroy(self, event=None):
        if event is not None and event.widget is not self:
            return
        self._destroyed = True
        self._visible = False
        for attr in ("_wv_show_job", "_ui_poll_job", "_notify_job"):
            job = getattr(self, attr, None)
            if job:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
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
        app = self.app
        dot_color = app.dot_canvas.item_prop(app.dot_id, "fill", COLORS["text_dim"])
        peers_fg = str(app.lbl_peers.cget("fg") or COLORS["text_dim"])
        return {
            "colors": {
                "bg": COLORS.get("bg", "#0f131b"),
                "bg_card": COLORS.get("bg_card", "#171c28"),
                "border": COLORS.get("border", "#2a3346"),
                "text": COLORS.get("text", "#e6edf7"),
                "text_dim": COLORS.get("text_dim", "#8b97ad"),
                "accent": COLORS.get("accent", "#67d5f7"),
                "accent_hover": COLORS.get("accent_hover", "#8be0fa"),
                "accent_fill": COLORS.get("accent_fill", COLORS.get("accent", "#2563eb")),
                "on_accent_fill": COLORS.get("on_accent_fill", COLORS.get("on_accent", "#ffffff")),
                "danger": COLORS.get("danger", "#ef4444"),
                "warn": COLORS.get("warn", "#f59e0b"),
                "success": COLORS.get("success", "#22c55e"),
            },
            "title": tr("⏱  Focus Timer"),
            "dot_color": dot_color,
            "status": str(app.lbl_status.cget("text") or ""),
            "time_text": str(app.lbl_time.cget("text") or "0.00 min"),
            "sub_text": str(app.lbl_sub.cget("text") or ""),
            "today_text": str(app.lbl_today.cget("text") or ""),
            "peers_text": str(app.lbl_peers.cget("text") or ""),
            "peers_warn": peers_fg == COLORS.get("warn"),
            "bonus": {
                "visible": bool(app.lbl_bonus.winfo_manager() == "place"),
                "text": str(app.lbl_bonus.cget("text") or ""),
            },
            "buttons": {
                "action": {"text": str(app.btn_action.cget("text") or ""), "disabled": app.btn_action.disabled},
                "break": {"text": str(app.btn_break.cget("text") or ""), "disabled": app.btn_break.disabled},
                "pause": {"text": str(app.btn_pause.cget("text") or ""), "disabled": app.btn_pause.disabled},
                "sleep": {"text": str(app.btn_sleep.cget("text") or ""), "disabled": app.btn_sleep.disabled},
                "prestart": {
                    "text": str(app.btn_prestart.cget("text") or ""),
                    "disabled": app.btn_prestart.disabled,
                    "visible": app.btn_prestart.winfo_ismapped(),
                },
                "outing": {"text": str(app.btn_outing.cget("text") or ""), "disabled": app.btn_outing.disabled},
            },
            "flavor": tr("在第二段区间内 学的时间越长得到的休息时间越长.请尽量长时间地专注."),
        }

    def _handle_web_action(self, body: dict) -> dict:
        atype = str(body.get("type") or "")
        app = self.app
        mapping = {
            "action": app.on_action,
            "break": app.on_break_button,
            "pause": app.toggle_focus_pause,
            "sleep": app._sleep_click,
            "prestart": app._prestart_click,
            "outing": app._outing_click,
            "bonus": app._on_bonus_click,
        }
        fn = mapping.get(atype)
        if fn is None:
            return {"ok": False, "error": f"unknown_action:{atype}"}
        self.after(0, fn)
        return {"ok": True}


_TIMER_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>OIT Timer</title>
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
    --warn: #f59e0b;
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
    padding: 20px 26px 20px;
    gap: 14px;
  }
  .hdr {
    display: flex; align-items: center; justify-content: space-between;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border);
  }
  .hdr-title {
    font-size: 20px; font-weight: 700; letter-spacing: 0.3px;
  }
  .bonus-btn {
    background: transparent; border: none;
    color: var(--text-dim); opacity: 0.58;
    font-size: 12.5px; cursor: pointer; font-family: inherit;
    padding: 4px 8px; border-radius: 6px;
    transition: opacity .15s, background .15s;
  }
  .bonus-btn:hover { opacity: 1; background: rgba(127,127,127,0.12); }
  .card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 22px 24px;
    display: flex; flex-direction: column; gap: 8px;
  }
  .status-row {
    display: flex; align-items: center; gap: 9px;
    font-size: 13.5px; color: var(--text-dim);
  }
  .status-dot {
    width: 11px; height: 11px; border-radius: 50%; flex-shrink: 0;
    transition: background .2s;
  }
  .big-time {
    font-size: 46px; font-weight: 700; line-height: 1.15;
    letter-spacing: 0.5px;
    font-variant-numeric: tabular-nums;
    margin: 2px 0 4px;
  }
  .sub-text {
    color: var(--text-dim); font-size: 13.5px; line-height: 1.5;
    white-space: pre-wrap; min-height: 20px;
  }
  .stats-row {
    display: flex; align-items: center; gap: 24px;
    margin-top: 8px;
  }
  .today-text {
    color: var(--accent); font-weight: 700; font-size: 14px;
  }
  .peers-text {
    color: var(--text-dim); font-size: 12.5px; line-height: 1.45;
    white-space: pre-wrap; margin-top: 2px;
  }
  .peers-text.warn { color: var(--warn); }
  .btn-area {
    display: flex; flex-direction: column; align-items: center; gap: 10px;
    margin-top: 6px;
  }
  .btn-row {
    display: flex; flex-wrap: wrap; justify-content: center; gap: 10px;
  }
  .btn {
    appearance: none; border: 1px solid var(--border);
    background: var(--bg-card); color: var(--text);
    padding: 8px 18px; border-radius: 8px;
    font-size: 13.5px; font-family: inherit; cursor: pointer;
    transition: background .12s, border-color .12s, transform .08s;
    white-space: nowrap;
  }
  .btn:hover:not(:disabled) { border-color: var(--accent); background: rgba(127,127,127,0.14); }
  .btn:active:not(:disabled) { transform: translateY(1px); }
  .btn:disabled { opacity: 0.42; cursor: not-allowed; }
  .btn-accent {
    background: var(--accent-fill); color: var(--on-accent-fill);
    border-color: var(--accent-fill); font-weight: 600;
  }
  .btn-accent:hover:not(:disabled) { filter: brightness(1.08); }
  .spacer { flex: 1; }
  .flavor {
    color: var(--text-dim); opacity: 0.72;
    font-size: 12px; line-height: 1.55;
    padding-top: 6px;
  }
  .hidden { display: none !important; }
</style>
</head>
<body>
<div class="app">
  <div class="hdr">
    <div id="title" class="hdr-title">⏱ Focus Timer</div>
    <button id="btnBonus" class="bonus-btn hidden"></button>
  </div>

  <div class="card">
    <div class="status-row">
      <span id="dot" class="status-dot"></span>
      <span id="status"></span>
    </div>
    <div id="timeText" class="big-time">0.00 min</div>
    <div id="subText" class="sub-text"></div>
    <div class="stats-row">
      <div id="todayText" class="today-text"></div>
    </div>
    <div id="peersText" class="peers-text hidden"></div>
  </div>

  <div class="btn-area">
    <div class="btn-row">
      <button id="btnAction" class="btn btn-accent"></button>
      <button id="btnBreak" class="btn"></button>
      <button id="btnPause" class="btn"></button>
    </div>
    <div class="btn-row">
      <button id="btnSleep" class="btn"></button>
      <button id="btnPrestart" class="btn hidden"></button>
      <button id="btnOuting" class="btn"></button>
    </div>
  </div>

  <div class="spacer"></div>
  <div id="flavor" class="flavor"></div>
</div>
<script>
const TOKEN = __TOKEN__;
const $ = id => document.getElementById(id);

function applyColors(c) {
  if (!c) return;
  const r = document.documentElement.style;
  r.setProperty('--bg', c.bg);
  r.setProperty('--bg-card', c.bg_card);
  r.setProperty('--border', c.border);
  r.setProperty('--text', c.text);
  r.setProperty('--text-dim', c.text_dim);
  r.setProperty('--accent', c.accent);
  r.setProperty('--accent-hover', c.accent_hover || c.accent);
  r.setProperty('--accent-fill', c.accent_fill || c.accent);
  r.setProperty('--on-accent-fill', c.on_accent_fill || '#ffffff');
  r.setProperty('--warn', c.warn || '#f59e0b');
}

function render(s) {
  if (!s) return;
  applyColors(s.colors);
  $('title').textContent = s.title || '';
  $('dot').style.background = s.dot_color || 'var(--text-dim)';
  $('status').textContent = s.status || '';
  $('timeText').textContent = s.time_text || '';
  $('subText').textContent = s.sub_text || '';
  $('todayText').textContent = s.today_text || '';
  const peers = $('peersText');
  if (s.peers_text) {
    peers.textContent = s.peers_text;
    peers.classList.remove('hidden');
    peers.classList.toggle('warn', !!s.peers_warn);
  } else {
    peers.classList.add('hidden');
  }
  const bonus = $('btnBonus');
  if (s.bonus && s.bonus.visible) {
    bonus.textContent = s.bonus.text || '';
    bonus.classList.remove('hidden');
  } else {
    bonus.classList.add('hidden');
  }
  const b = s.buttons || {};
  const setBtn = (id, info) => {
    const el = $(id);
    if (!info) return;
    el.textContent = info.text || '';
    el.disabled = !!info.disabled;
    if ('visible' in info) el.classList.toggle('hidden', !info.visible);
  };
  setBtn('btnAction', b.action);
  setBtn('btnBreak', b.break);
  setBtn('btnPause', b.pause);
  setBtn('btnSleep', b.sleep);
  setBtn('btnPrestart', b.prestart);
  setBtn('btnOuting', b.outing);
  $('flavor').textContent = s.flavor || '';
}

async function fetchState() {
  try {
    const res = await fetch('/api/state?token=' + encodeURIComponent(TOKEN));
    const j = await res.json();
    if (j && j.ok) render(j.state);
  } catch (_) {}
}

async function sendAction(type) {
  try {
    await fetch('/api/action?token=' + encodeURIComponent(TOKEN), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({type})
    });
  } catch (_) {}
}

$('btnAction').onclick = () => sendAction('action');
$('btnBreak').onclick = () => sendAction('break');
$('btnPause').onclick = () => sendAction('pause');
$('btnSleep').onclick = () => sendAction('sleep');
$('btnPrestart').onclick = () => sendAction('prestart');
$('btnOuting').onclick = () => sendAction('outing');
$('btnBonus').onclick = () => sendAction('bonus');

let es = null;
function connectSSE() {
  if (es) { try { es.close(); } catch (_) {} }
  es = new EventSource('/api/events?token=' + encodeURIComponent(TOKEN));
  es.addEventListener('update', () => fetchState());
  es.onerror = () => {
    try { es.close(); } catch (_) {}
    setTimeout(connectSSE, 1500);
  };
}

fetchState();
connectSSE();
</script>
</body>
</html>
"""
