# -*- coding: utf-8 -*-
"""原生 Windows Edge WebView2 嵌入宿主（纯标准库 ctypes，零外部依赖）。

直接加载系统已安装的 Edge WebView2 Runtime（EmbeddedBrowserWebView.dll），
将 ICoreWebView2Controller 同进程挂载到 Tkinter Frame 的原生 HWND 上，
随主窗口自动移动、缩放、隐藏与销毁，不依赖完整 Edge 浏览器进程。
"""

import ctypes
import http.server
import json
import os
import platform
import queue
import secrets
import struct
import sys
import tempfile
import threading
import tkinter as tk
import urllib.parse
from typing import Callable

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import winreg
    from ctypes import wintypes

    _COINIT_APARTMENTTHREADED = 0x2
    _WEBVIEW2_CLIENT_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

    _QI_TYPE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
    _ADDREF_TYPE = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
    _RELEASE_TYPE = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
    _INVOKE_TYPE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p)

    class _HandlerVTable(ctypes.Structure):
        _fields_ = [
            ("QueryInterface", _QI_TYPE),
            ("AddRef", _ADDREF_TYPE),
            ("Release", _RELEASE_TYPE),
            ("Invoke", _INVOKE_TYPE),
        ]

    class _HandlerObj(ctypes.Structure):
        _fields_ = [("lpVtbl", ctypes.POINTER(_HandlerVTable))]

    def _vcall(ptr: int, idx: int, restype, *argtypes):
        vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        fn_t = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        return fn_t(vtbl[idx])

    def _com_release(ptr: int | None):
        if ptr:
            try:
                _vcall(ptr, 2, ctypes.c_ulong)(ptr)
            except Exception:
                pass

    def _arch_folder() -> str:
        mach = (platform.machine() or "").lower()
        if "arm" in mach:
            return "arm64" if struct.calcsize("P") == 8 else "arm"
        return "x64" if struct.calcsize("P") == 8 else "x86"

    def _ver_key(name: str) -> tuple[int, ...]:
        parts = []
        for seg in name.split("."):
            try:
                parts.append(int(seg))
            except ValueError:
                return ()
        return tuple(parts)


_CACHED_DLL_PATH: str | None = None
_DLL_SEARCHED = False
# 保持 COM 回调对象生命周期与进程一致，防止 WebView2 异步关闭后调用 Release() 踩空
_GLOBAL_COM_REFS: list = []
# 进程内按 user_data_dir 复用的 WebView2 Environment，避免多页面反复冷启动多个浏览器进程
_SHARED_ENVS: dict[str, dict] = {}


def default_webview_profile_dir() -> str:
    """所有 OIT 内嵌 WebView2 页面共用的正常完整性用户目录（禁止放 LocalLow，防提权黑屏）。"""
    local_app = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(local_app, "OIT", "webview2_shared")


def find_webview2_dll() -> str | None:
    """查找系统 Edge WebView2 Runtime 的 EmbeddedBrowserWebView.dll。"""
    global _CACHED_DLL_PATH, _DLL_SEARCHED
    if not IS_WINDOWS:
        return None
    if _DLL_SEARCHED:
        return _CACHED_DLL_PATH
    _DLL_SEARCHED = True

    arch = _arch_folder()
    roots: list[str] = []

    # 1. 注册表（优先按官方客户端 GUID 查找）
    reg_paths = [
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2_CLIENT_GUID}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2_CLIENT_GUID}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2_CLIENT_GUID}"),
    ]
    for hive, subkey in reg_paths:
        try:
            with winreg.OpenKey(hive, subkey) as k:
                for val_name in ("location", "ebv"):
                    try:
                        loc, _ = winreg.QueryValueEx(k, val_name)
                        if loc and os.path.isdir(loc):
                            roots.append(loc)
                    except OSError:
                        pass
        except OSError:
            pass

    # 2. 标准安装目录兜底
    for env_var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(env_var)
        if base:
            cand = os.path.join(base, "Microsoft", "EdgeWebView", "Application")
            if os.path.isdir(cand):
                roots.append(cand)

    # 在所有候选目录中按版本号降序挑选最新的 EmbeddedBrowserWebView.dll
    found: list[tuple[tuple[int, ...], str]] = []
    seen_roots: set[str] = set()
    for root in roots:
        norm = os.path.normcase(os.path.abspath(root))
        if norm in seen_roots:
            continue
        seen_roots.add(norm)
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for entry in entries:
            vk = _ver_key(entry)
            if not vk:
                continue
            dll = os.path.join(root, entry, "EBWebView", arch, "EmbeddedBrowserWebView.dll")
            if os.path.isfile(dll):
                found.append((vk, dll))

    if found:
        found.sort(key=lambda item: item[0], reverse=True)
        _CACHED_DLL_PATH = found[0][1]
    return _CACHED_DLL_PATH


def is_webview2_available() -> bool:
    """当前系统是否支持原生 WebView2 嵌入。"""
    return bool(find_webview2_dll())


class WebView2Host(tk.Frame):
    """直接嵌入在 Tkinter Frame 内的原生 WebView2 控件。"""

    def __init__(
        self,
        master,
        user_data_dir: str = "",
        bg: str = "#12161f",
        on_ready: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        enable_devtools: bool = False,
    ):
        super().__init__(master, bg=bg, highlightthickness=0, bd=0)
        self._user_data_dir = user_data_dir or default_webview_profile_dir()
        self._on_ready = on_ready
        self._on_error = on_error
        self._enable_devtools = enable_devtools

        self._dll = None
        self._env_ptr: int = 0
        self._ctrl_ptr: int = 0
        self._wv_ptr: int = 0
        self._host_hwnd: int = 0
        self._started = False
        self._ready = False
        self._closed = False
        self._visible = False
        self._last_ctrl_visible = False
        self._current_url: str = ""
        self._pending_url: str = ""
        self._pending_html: str = ""
        self._top_bind_id: str | None = None
        self._top_widget = None
        self._event_q: queue.SimpleQueue = queue.SimpleQueue()
        self._poll_job = None

        self.bind("<Configure>", self._on_configure)
        self.bind("<Destroy>", self._on_destroy)

    @property
    def ready(self) -> bool:
        return self._ready and not self._closed

    def start(self, url: str = "") -> bool:
        """异步初始化 WebView2 并导航到目标 URL（幂等；已就绪且同 URL 时不重复重载）。"""
        if url:
            self._pending_url = url
            self._pending_html = ""
        if self._closed:
            return False
        if self._ready:
            if url and url != self._current_url:
                self.navigate(url)
            return True
        if self._started:
            return True

        dll_path = find_webview2_dll()
        if not dll_path:
            self._notify_error("未检测到 Microsoft Edge WebView2 Runtime")
            return False

        self._started = True
        try:
            self.update_idletasks()
            self._host_hwnd = self.winfo_id()
            os.makedirs(self._user_data_dir, exist_ok=True)
            # 绕过系统/断网代理访问 127.0.0.1，并避免管理员提权进程下 GPU 合成黑屏
            extra = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
            for flag in ("--no-proxy-server", "--disable-gpu-compositing"):
                if flag not in extra:
                    extra = (extra + " " + flag).strip()
            os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = extra

            ctypes.windll.ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
            self._bind_toplevel_move()
            self._schedule_event_poll()

            norm_dir = os.path.normcase(os.path.abspath(self._user_data_dir))
            entry = _SHARED_ENVS.get(norm_dir)
            if entry and entry.get("env"):
                self._attach_to_env(entry["env"])
                return True
            if entry and entry.get("creating"):
                entry["waiters"].append(self)
                return True

            entry = {"env": 0, "creating": True, "waiters": [self]}
            _SHARED_ENVS[norm_dir] = entry

            self._dll = ctypes.WinDLL(dll_path)
            create_env = self._dll.CreateWebViewEnvironmentWithOptionsInternal
            create_env.argtypes = [
                wintypes.BOOL,
                ctypes.c_int,
                wintypes.LPCWSTR,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]
            create_env.restype = ctypes.c_long

            handler = self._make_com_handler(lambda hr, ptr, k=norm_dir: self._on_shared_env_created(k, hr, ptr))
            hr = create_env(True, 0, self._user_data_dir, None, handler)
            if hr != 0:
                _SHARED_ENVS.pop(norm_dir, None)
                self._started = False
                self._notify_error(f"WebView2 环境创建失败 (0x{hr & 0xFFFFFFFF:08X})")
                return False
            return True
        except Exception as exc:
            self._started = False
            self._notify_error(f"WebView2 初始化异常: {exc}")
            return False

    def navigate(self, url: str):
        self._pending_url = url
        self._pending_html = ""
        if self._ready and self._wv_ptr and not self._closed:
            try:
                self._current_url = url
                _vcall(self._wv_ptr, 5, ctypes.c_long, wintypes.LPCWSTR)(self._wv_ptr, url)
            except Exception:
                pass

    def navigate_html(self, html: str):
        self._pending_html = html
        self._pending_url = ""
        if self._ready and self._wv_ptr and not self._closed:
            try:
                self._current_url = ""
                _vcall(self._wv_ptr, 6, ctypes.c_long, wintypes.LPCWSTR)(self._wv_ptr, html)
            except Exception:
                pass

    def set_visible(self, visible: bool):
        self._visible = bool(visible)
        if self._ready and self._ctrl_ptr and not self._closed:
            was_visible = self._last_ctrl_visible
            try:
                if self._host_hwnd and self._visible:
                    u32 = ctypes.windll.user32
                    # 先确保宿主及其直接父容器映射且位于兄弟窗口顶层，避免 grid_remove()/frozen() 切回后被盖住或未重绘
                    p = u32.GetParent(self._host_hwnd)
                    if p and not (u32.GetWindowLongW(p, -16) & 0x10000000):
                        u32.ShowWindow(p, 8)  # SW_SHOWNA
                    if not (u32.GetWindowLongW(self._host_hwnd, -16) & 0x10000000):
                        u32.ShowWindow(self._host_hwnd, 8)
                    u32.SetWindowPos(self._host_hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # HWND_TOP | NOSIZE|NOMOVE|NOACTIVATE

                _vcall(self._ctrl_ptr, 4, ctypes.c_long, wintypes.BOOL)(
                    self._ctrl_ptr, 1 if self._visible else 0
                )
                if self._host_hwnd:
                    u32 = ctypes.windll.user32
                    ch = u32.GetWindow(self._host_hwnd, 5)  # GW_CHILD
                    while ch:
                        u32.ShowWindow(ch, 8 if self._visible else 0)  # SW_SHOWNA / SW_HIDE
                        ch = u32.GetWindow(ch, 2)  # GW_HWNDNEXT
                self._last_ctrl_visible = self._visible
                if self._visible:
                    # 从隐藏恢复可见时，通过 1px 边界微调强制 Chromium 软件合成器立刻提交新帧，彻底根除切回黑屏
                    self.sync_bounds(force_repaint=not was_visible)
                    self.notify_parent_moved()
            except Exception:
                pass

    def sync_bounds(self, force_repaint: bool = False):
        """将 WebView2 边界精确贴合到当前 Frame 的物理客户区。"""
        if not self._ready or not self._ctrl_ptr or self._closed or not self._host_hwnd:
            return
        try:
            u32 = ctypes.windll.user32
            rect = wintypes.RECT()
            if u32.GetClientRect(self._host_hwnd, ctypes.byref(rect)):
                if force_repaint and rect.right - rect.left > 2 and rect.bottom - rect.top > 2:
                    nudge = wintypes.RECT(rect.left, rect.top, rect.right - 1, rect.bottom)
                    _vcall(self._ctrl_ptr, 6, ctypes.c_long, wintypes.RECT)(self._ctrl_ptr, nudge)
                _vcall(self._ctrl_ptr, 6, ctypes.c_long, wintypes.RECT)(self._ctrl_ptr, rect)
                if force_repaint:
                    # RDW_INVALIDATE | RDW_ERASE | RDW_FRAME | RDW_UPDATENOW | RDW_ALLCHILDREN
                    u32.RedrawWindow(self._host_hwnd, None, None, 0x0001 | 0x0004 | 0x0400 | 0x0100 | 0x0080)
        except Exception:
            pass

    def notify_parent_moved(self):
        """通知 WebView2 父窗口已移动（保持下拉菜单与输入法候选框定位准确）。"""
        if not self._ready or not self._ctrl_ptr or self._closed:
            return
        try:
            _vcall(self._ctrl_ptr, 23, ctypes.c_long)(self._ctrl_ptr)
        except Exception:
            pass

    def focus_webview(self):
        if self._ready and self._ctrl_ptr and self._visible and not self._closed:
            try:
                # MoveFocus(COREWEBVIEW2_MOVE_FOCUS_REASON_PROGRAMMATIC = 0)
                _vcall(self._ctrl_ptr, 12, ctypes.c_long, ctypes.c_int)(self._ctrl_ptr, 0)
            except Exception:
                pass

    def close(self):
        """关闭并释放 WebView2 控制器与 COM 资源。"""
        if self._closed:
            return
        self._closed = True
        self._ready = False
        if self._poll_job:
            try:
                self.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
        self._unbind_toplevel_move()

        ctrl = self._ctrl_ptr
        wv = self._wv_ptr
        env = self._env_ptr
        self._ctrl_ptr = 0
        self._wv_ptr = 0
        self._env_ptr = 0

        if ctrl:
            try:
                _vcall(ctrl, 24, ctypes.c_long)(ctrl)  # Close()
            except Exception:
                pass
            _com_release(ctrl)
        _com_release(wv)
        _com_release(env)

    # ══════════════ 内部回调与绑定 ══════════════

    def _schedule_event_poll(self):
        if self._closed:
            return
        while True:
            try:
                kind, payload = self._event_q.get_nowait()
            except queue.Empty:
                break
            if kind == "ready":
                self.sync_bounds()
                self.set_visible(self._visible)
                if callable(self._on_ready):
                    try:
                        self._on_ready()
                    except Exception:
                        pass
            elif kind == "error":
                self._notify_error(payload)
        if not self._ready and not self._closed:
            try:
                self._poll_job = self.after(25, self._schedule_event_poll)
            except tk.TclError:
                self._poll_job = None
        else:
            self._poll_job = None

    def _make_com_handler(self, callback: Callable[[int, int], None]):
        def qi(this, _riid, ppv):
            ppv[0] = this
            return 0

        def addref(_this):
            return 1

        def release(_this):
            return 1

        def invoke(_this, hr, obj_ptr):
            try:
                callback(int(hr), int(obj_ptr or 0))
            except Exception:
                pass
            return 0

        vt = _HandlerVTable(_QI_TYPE(qi), _ADDREF_TYPE(addref), _RELEASE_TYPE(release), _INVOKE_TYPE(invoke))
        co = _HandlerObj(ctypes.pointer(vt))
        _GLOBAL_COM_REFS.extend([vt, co])
        return ctypes.byref(co)

    def _on_shared_env_created(self, norm_dir: str, hr: int, env_ptr: int):
        entry = _SHARED_ENVS.get(norm_dir)
        waiters = list(entry["waiters"]) if entry else [self]
        if entry:
            entry["creating"] = False
            entry["waiters"] = []
        if hr != 0 or not env_ptr:
            _SHARED_ENVS.pop(norm_dir, None)
            msg = f"WebView2 环境回调失败 (0x{hr & 0xFFFFFFFF:08X})"
            for w in waiters:
                if not w._closed:
                    w._event_q.put(("error", msg))
            return

        # 共享缓存保留一份长期引用
        _vcall(env_ptr, 1, ctypes.c_ulong)(env_ptr)  # AddRef
        if entry is not None:
            entry["env"] = env_ptr
        for w in waiters:
            if not w._closed:
                w._attach_to_env(env_ptr)

    def _attach_to_env(self, env_ptr: int):
        if self._closed or not env_ptr:
            return
        _vcall(env_ptr, 1, ctypes.c_ulong)(env_ptr)  # AddRef for this host
        self._env_ptr = env_ptr
        try:
            handler = self._make_com_handler(self._on_ctrl_created)
            hr2 = _vcall(env_ptr, 3, ctypes.c_long, wintypes.HWND, ctypes.c_void_p)(env_ptr, self._host_hwnd, handler)
            if hr2 != 0:
                self._event_q.put(("error", f"WebView2 控制器创建失败 (0x{hr2 & 0xFFFFFFFF:08X})"))
        except Exception as exc:
            self._event_q.put(("error", f"WebView2 挂载宿主失败: {exc}"))

    def _on_ctrl_created(self, hr: int, ctrl_ptr: int):
        if self._closed:
            if ctrl_ptr:
                try:
                    _vcall(ctrl_ptr, 24, ctypes.c_long)(ctrl_ptr)
                except Exception:
                    pass
                _com_release(ctrl_ptr)
            return
        if hr != 0 or not ctrl_ptr:
            self._event_q.put(("error", f"WebView2 控制器回调失败 (0x{hr & 0xFFFFFFFF:08X})"))
            return

        _vcall(ctrl_ptr, 1, ctypes.c_ulong)(ctrl_ptr)  # AddRef
        self._ctrl_ptr = ctrl_ptr

        wv_ptr = ctypes.c_void_p()
        hr2 = _vcall(ctrl_ptr, 25, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))(ctrl_ptr, ctypes.byref(wv_ptr))
        if hr2 != 0 or not wv_ptr.value:
            self._event_q.put(("error", f"获取 ICoreWebView2 失败 (0x{hr2 & 0xFFFFFFFF:08X})"))
            return
        self._wv_ptr = int(wv_ptr.value)

        # 配置 WebView2 Settings：禁用多余右键菜单与状态栏，保持原生应用观感
        settings_ptr = ctypes.c_void_p()
        try:
            if _vcall(self._wv_ptr, 3, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))(
                self._wv_ptr, ctypes.byref(settings_ptr)
            ) == 0 and settings_ptr.value:
                sp = int(settings_ptr.value)
                _vcall(sp, 4, ctypes.c_long, wintypes.BOOL)(sp, 1)   # IsScriptEnabled
                _vcall(sp, 6, ctypes.c_long, wintypes.BOOL)(sp, 1)   # IsWebMessageEnabled
                _vcall(sp, 8, ctypes.c_long, wintypes.BOOL)(sp, 1)   # AreDefaultScriptDialogsEnabled
                _vcall(sp, 10, ctypes.c_long, wintypes.BOOL)(sp, 0)  # IsStatusBarEnabled
                _vcall(sp, 12, ctypes.c_long, wintypes.BOOL)(sp, 1 if self._enable_devtools else 0)
                _vcall(sp, 14, ctypes.c_long, wintypes.BOOL)(sp, 0)  # AreDefaultContextMenusEnabled
                _com_release(sp)
        except Exception:
            pass

        self._ready = True
        self.sync_bounds()
        self.set_visible(self._visible)

        if self._pending_url:
            self.navigate(self._pending_url)
        elif self._pending_html:
            self.navigate_html(self._pending_html)

        self._event_q.put(("ready", ""))

    def _notify_error(self, msg: str):
        if callable(self._on_error):
            try:
                self._on_error(msg)
            except Exception:
                pass

    def _bind_toplevel_move(self):
        if self._top_bind_id is not None:
            return
        try:
            top = self.winfo_toplevel()
            self._top_widget = top
            self._top_bind_id = top.bind("<Configure>", self._on_toplevel_configure, add="+")
        except Exception:
            self._top_bind_id = None

    def _unbind_toplevel_move(self):
        if self._top_bind_id and self._top_widget is not None:
            try:
                self._top_widget.unbind("<Configure>", self._top_bind_id)
            except Exception:
                pass
        self._top_bind_id = None
        self._top_widget = None

    def _on_configure(self, _event=None):
        if self._ready and not self._closed:
            self.sync_bounds()

    def _on_toplevel_configure(self, _event=None):
        if self._ready and self._visible and not self._closed:
            self.notify_parent_moved()

    def _on_destroy(self, event=None):
        if event is None or event.widget is self:
            self.close()


class LocalWebServer:
    """通用 127.0.0.1 随机端口本地 HTTP + SSE 服务，供内嵌 WebView2 页面复用。"""

    def __init__(
        self,
        html_provider: Callable[[str], str],
        get_state: Callable[[], dict],
        handle_action: Callable[[dict], dict],
        thread_name: str = "oit-web",
    ):
        self._html_provider = html_provider
        self._get_state = get_state
        self._handle_action = handle_action
        self._thread_name = thread_name
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
                ok, parsed, _qs = self._check_token()
                if not ok:
                    self._send_json(403, {"ok": False, "error": "forbidden"})
                    return
                path = parsed.path
                if path in ("/", "/index.html"):
                    html = srv_ref._html_provider(srv_ref.token)
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
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name=self._thread_name)
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
