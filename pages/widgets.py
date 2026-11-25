

import sys
import tkinter as tk
from contextlib import contextmanager
from tkinter import ttk

import config
from config import COLORS
from i18n import tr


class Switch(tk.Canvas):

    W, H = 46, 24

    def __init__(self, master, value: bool = True, command=None, bg: str = None):
        super().__init__(
            master, width=self.W, height=self.H, bg=bg or COLORS["bg"],
            highlightthickness=0, bd=0, cursor="hand2"
        )
        self._cmd = command
        self._on = bool(value)
        r = self.H / 2.0
        self._track = [
            self.create_oval(1, 1, self.H - 1, self.H - 1, outline=""),
            self.create_oval(self.W - self.H + 1, 1, self.W - 1, self.H - 1, outline=""),
            self.create_rectangle(r, 1, self.W - r, self.H - 1, outline=""),
        ]
        self._knob = self.create_oval(0, 0, 0, 0, fill=COLORS["on_accent"], outline="")
        self.bind("<Button-1>", self._on_click)
        self._draw()

    def _on_click(self, _event=None):
        self.set(not self._on, notify=True)

    def get(self) -> bool:
        return self._on

    def set(self, value: bool, notify: bool = False):
        value = bool(value)
        changed = value != self._on
        self._on = value
        self._draw()
        if notify and changed and self._cmd:
            self._cmd(self._on)

    def _draw(self):
        for item in self._track:
            self.itemconfig(item, fill=COLORS["accent"] if self._on else COLORS["switch_off"])
        pad = 3
        d = self.H - 2 * pad
        x = (self.W - self.H + pad) if self._on else pad
        self.coords(self._knob, x, pad, x + d, pad + d)


class ScrollArea(tk.Frame):

    def __init__(self, master, height: int = 300, bg: str = None, **kw):
        bg = bg or COLORS["bg"]
        super().__init__(master, bg=bg, **kw)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, height=height)
        self.scroll = ttk.Scrollbar(self, orient="vertical")
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self._scroller = attach_smooth_scroll(self.canvas, self.scroll, self.inner, self._win)
        self._oit_scroller = self._scroller
        self.canvas._oit_scroller = self._scroller
        self._install_wheel_dispatcher()
        self.bind_children()

    def bind_children(self):
        """兼容旧调用方; 新控件只绑定一次，实际路由仍可由顶层分发器完成。"""
        def walk(widget):
            if not getattr(widget, "_oit_wheel", False):
                try:
                    widget.bind("<MouseWheel>", self._wheel, add="+")
                    widget._oit_wheel = True
                except Exception:
                    return
            for child in widget.winfo_children():
                walk(child)
        walk(self.inner)

    def _install_wheel_dispatcher(self):
        try:
            root = self.winfo_toplevel()
            if getattr(root, "_oit_wheel_dispatch_bound", False):
                return
            root.bind_all("<MouseWheel>", self._dispatch_wheel, add="+")
            root._oit_wheel_dispatch_bound = True
        except Exception:
            pass

    @staticmethod
    def _dispatch_wheel(event):
        widget = getattr(event, "widget", None)
        if widget is None or isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview, ttk.Combobox)):
            return None
        cur = widget
        while cur is not None:
            scroller = getattr(cur, "_oit_scroller", None)
            if scroller is not None:
                return scroller.on_wheel(event)
            try:
                cur = cur.master
            except Exception:
                break
        return None

    def _bind_children_legacy(self):
        """给 inner 里现有的所有控件绑上滚轮 (动态加进来的控件要再调一次).

        标记放在控件对象上而不是 winfo_id(): 控件销毁重建时 id 可能会被重用,
        那样新控件就绑不上了 (提前启动的清单每次点都重画)."""
        def walk(w):
            if not getattr(w, "_oit_wheel", False):
                try:
                    w.bind("<MouseWheel>", self._wheel, add="+")
                    w._oit_wheel = True
                except Exception:
                    return
            for c in w.winfo_children():
                walk(c)
        walk(self.inner)

    def _wheel(self, event):
        return self._scroller.on_wheel(event)

    def scroll_to_top(self):
        self._scroller.moveto(0.0)

    def scroll_to(self, widget):
        """滚到 widget (inner 里直接放着的一块) 的顶部."""
        try:
            self.update_idletasks()
            total = max(1, self.inner.winfo_height())
            self._scroller.moveto(max(0.0, widget.winfo_y() - 4) / total)
        except Exception:
            pass


class Modal(tk.Toplevel):

    def __init__(self, parent, title: str, width: int = 460, height: int = 260, minimizable: bool = True):
        super().__init__(parent, bg=COLORS["bg"])
        self.title(title)
        self._modal_title = str(title or "")
        self._minimized = False
        self.resizable(False, False)
        self.transient(parent)
        self.result = None

        # 右上角最小化按钮（缩到左侧边栏，支持同时最小化多个窗口）
        if minimizable:
            top_bar = tk.Frame(self, bg=COLORS["bg"])
            top_bar.pack(side="top", fill="x", padx=(20, 12), pady=(6, 0))
            min_btn = tk.Label(
                top_bar, text=tr("收起"), bg=COLORS["bg"], fg=COLORS["text_dim"],
                font=config.font("small"), cursor="hand2", padx=8, pady=1,
            )
            min_btn.pack(side="right")
            min_btn.bind("<Button-1>", lambda _e: self.minimize())
            min_btn.bind("<Enter>", lambda _e: min_btn.configure(bg=COLORS["bg_card"], fg=COLORS["text"]))
            min_btn.bind("<Leave>", lambda _e: min_btn.configure(bg=COLORS["bg"], fg=COLORS["text_dim"]))
            self.minimize_button = min_btn

        # 按钮先占住底部: 内容再长也只会把 body 挤小, 不会把"确定 / 取消"顶出窗口外
        self.buttons = tk.Frame(self, bg=COLORS["bg"])
        self.buttons.pack(side="bottom", fill="x", padx=20, pady=(0, 16))

        body = tk.Frame(self, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=20, pady=(4, 16) if minimizable else 16)
        self.body = body

        self.bind("<Escape>", lambda e: self.cancel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self._center(parent, width, height)
        self.grab_set()

    def _dock_host(self):
        try:
            root = self.nametowidget(".")
            return getattr(root, "_oit_window_dock", None)
        except Exception:
            return None

    def minimize(self):
        """将悬浮窗口最小化到主窗口左侧边栏（释放模态锁，允许同时最小化多个窗口）。"""
        if self._minimized:
            return
        self._minimized = True
        try:
            self.grab_release()
        except tk.TclError:
            pass
        dock = self._dock_host()
        if dock is not None and hasattr(dock, "dock_minimized_window"):
            self.withdraw()
            dock.dock_minimized_window(self, self._modal_title or self.title())
        else:
            try:
                self.iconify()
            except tk.TclError:
                self.withdraw()

    def restore(self):
        """从侧边栏恢复悬浮窗口。"""
        if not self.winfo_exists():
            return
        self._minimized = False
        dock = self._dock_host()
        if dock is not None and hasattr(dock, "undock_minimized_window"):
            dock.undock_minimized_window(self)
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
            self.grab_set()
        except tk.TclError:
            pass

    def destroy(self):
        dock = self._dock_host()
        if dock is not None and hasattr(dock, "undock_minimized_window"):
            try:
                dock.undock_minimized_window(self)
            except Exception:
                pass
        super().destroy()

    def _center(self, parent, width, height):
        self.update_idletasks()
        try:
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            x = px + max(0, (pw - width) // 2)
            y = py + max(0, (ph - height) // 3)
        except Exception:
            x = y = 200
        self.geometry(f"{width}x{height}+{x}+{y}")

    def cancel(self):
        self.result = None
        self.destroy()

    def finish(self, result):
        self.result = result
        self.destroy()

    def wait(self):
        self.wait_window(self)
        return self.result


def focus_name(widget) -> str:
    """现在有键盘焦点的控件的名字 (没有 = ""). 下拉框打开时焦点在它的列表窗口里, tkinter 认不出来, focus_get() 会抛 KeyError: 'popdown'
    (界面每秒刷新一次, 抛出来页面就刷不动了): 这时返回 "popdown"."""
    try:
        w = widget.focus_get()
    except KeyError as e:
        return str(e.args[0]) if e.args else "popdown"
    except tk.TclError:
        return ""
    return str(w) if w is not None else ""


def wrap_label(widget, pad: int = 16):
    def on_configure(event):
        width = max(120, event.width - pad)
        if widget.cget("wraplength") != width:
            widget.configure(wraplength=width)
    widget.bind("<Configure>", on_configure, add="+")
    return widget


class _NoFlavor(tk.Frame):
    """都市之意不显示风味文本: 一个不占位的空外框, 调用方照常 pack / 取 .label (都是空操作)."""

    def __init__(self, parent, text):
        super().__init__(parent, width=0, height=0)
        self.label = tk.Label(self, text=text)          # 不显示, 只让调用方改 .label 的文字时有对象

    def pack(self, *args, **kw):
        pass

    pack_configure = grid = grid_configure = place = place_configure = pack


def flavor(parent, text: str, bg: str = None, pad: int = 24):
    """作者写的风味文本 (来自 MECHANICS.md): 左边一道细竖线, 暗色小字, 和说明文字区分开.
    返回外框; 文字标签是 .label. 都市之意主题不显示风味文本."""
    if getattr(config, "THEME", "") == "urban_will":
        return _NoFlavor(parent, text)
    bg = bg or COLORS["bg"]
    fr = tk.Frame(parent, bg=bg)
    tk.Frame(fr, bg=COLORS["accent"], width=max(2, config.scaled(3))).pack(side="left", fill="y")
    lbl = tk.Label(fr, text=text, bg=bg, fg=COLORS["text_dim"], font=config.font("small"),
                   anchor="w", justify="left")
    lbl.pack(side="left", fill="x", expand=True, padx=(config.scaled(10), 0), pady=2)
    wrap_label(lbl, pad + config.scaled(14))
    fr.label = lbl

    # 滚轮: 鼠标停在风味文本上时, 滚动它所在的那个可以滚动的区域 (往上找最近的带滚动条的 Canvas).
    # 不靠 ScrollFrame.bind_children —— 页面建好以后才加进来的风味文本它绑不上.
    def wheel(e):
        w = fr.master
        while w is not None:
            if isinstance(w, tk.Canvas) and str(w.cget("yscrollcommand")):
                fn = getattr(w, "_oit_wheel_fn", None)
                if fn is not None:
                    return fn(e)
                w.yview_scroll(int(-e.delta / 120), "units")
                break
            w = w.master
        return "break"
    for w in [fr] + fr.winfo_children():
        w.bind("<MouseWheel>", wheel)
        w._oit_wheel = True              # ScrollFrame.bind_children 不要再绑一次 (会滚两倍)
    return fr


def section_title(parent, text: str, bg: str = None, sub: str = None):
    bg = bg or COLORS["bg"]
    wrap = tk.Frame(parent, bg=bg)
    lbl = tk.Label(
        wrap, text=text, bg=bg, fg=COLORS["text"],
        font=config.font("h2", bold=True), anchor="w"
    )
    lbl.pack(anchor="w")
    sub_lbl = None
    if sub:
        sub_lbl = tk.Label(
            wrap, text=sub, bg=bg, fg=COLORS["text_dim"],
            font=config.font("small"), anchor="w", justify="left"
        )
        sub_lbl.pack(anchor="w", pady=(2, 0))

    wrap.title_label = lbl
    wrap.sub_label = sub_lbl
    return wrap


# ---- 渲染: 一次性换帧 ----
#
# Tk 在 Windows 上每个控件都是一个独立的子窗口, 各自重绘.
# 切页面 / 重建控件时, 它们会一块一块地画出来, 看起来像"撕裂".
# frozen() 先让整个窗口停止重绘, 等所有改动和布局 (包括自动换行这类
# "布局完再调整一次" 的二次布局) 都做完, 再一次性把整窗画出来.
# 非 Windows 或调用失败时退化为普通更新, 不影响功能.

try:
    import _tkinter
    _WINDOW_EVENTS = _tkinter.WINDOW_EVENTS | _tkinter.DONT_WAIT
except Exception:
    _WINDOW_EVENTS = None

_WM_SETREDRAW = 0x000B
_RDW_INVALIDATE = 0x0001
_RDW_ALLCHILDREN = 0x0080
_RDW_UPDATENOW = 0x0100

_user32 = None
if sys.platform == "win32":
    try:
        import ctypes
        from ctypes import wintypes
        _user32 = ctypes.WinDLL("user32")
        _user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                         wintypes.WPARAM, wintypes.LPARAM]
        _user32.SendMessageW.restype = ctypes.c_ssize_t
        _user32.RedrawWindow.argtypes = [wintypes.HWND, ctypes.c_void_p,
                                         ctypes.c_void_p, wintypes.UINT]
        _user32.RedrawWindow.restype = wintypes.BOOL
    except Exception:
        _user32 = None

_freeze_depth = {}


# ---- 定格画面: 新页面画完之前一直显示上一页 ----
#
# 光靠 frozen() 还不够: 解冻后各个控件仍然是一个接一个地画到屏幕上的,
# 页面复杂时能看出分块. 所以在改动开始前, 先把窗口内部当前的样子截下来,
# 用一个没有边框的小窗口原样盖在上面 (看起来什么都没变).
# 新页面在它后面完整画好, 等系统把这一帧真正显示出来, 再撤掉盖板 ——
# 屏幕上直接从旧页面跳到新页面, 中间没有半成品.
# (Windows 8 以后每个窗口都画在自己的后台画布上, 被别的窗口盖住时照样能画.)

class _Snapshot:
    _api = None

    @classmethod
    def api(cls):
        if cls._api is not None:
            return cls._api or None
        cls._api = False
        if _user32 is None:
            return None
        try:
            import ctypes
            from ctypes import wintypes as wt
            u, g = _user32, ctypes.WinDLL("gdi32")
            k = ctypes.WinDLL("kernel32")
            try:
                d = ctypes.WinDLL("dwmapi")
                d.DwmFlush.argtypes = []
                d.DwmFlush.restype = ctypes.c_long
            except Exception:
                d = None
            H = ctypes.c_void_p
            u.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
            u.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
            u.GetAncestor.argtypes = [wt.HWND, wt.UINT]
            u.GetAncestor.restype = wt.HWND
            u.IsWindowVisible.argtypes = [wt.HWND]
            u.IsIconic.argtypes = [wt.HWND]
            u.GetDC.argtypes = [wt.HWND]
            u.GetDC.restype = H
            u.ReleaseDC.argtypes = [wt.HWND, H]
            u.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                          ctypes.c_int, wt.HWND, H, H, H]
            u.CreateWindowExW.restype = wt.HWND
            u.UpdateLayeredWindow.argtypes = [wt.HWND, H, ctypes.POINTER(wt.POINT),
                                              ctypes.POINTER(wt.SIZE), H,
                                              ctypes.POINTER(wt.POINT), wt.DWORD,
                                              H, wt.DWORD]
            u.UpdateLayeredWindow.restype = wt.BOOL
            u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
            u.DestroyWindow.argtypes = [wt.HWND]
            u.GetSystemMetrics.argtypes = [ctypes.c_int]
            u.GetSystemMetrics.restype = ctypes.c_int
            g.CreateCompatibleDC.argtypes = [H]
            g.CreateCompatibleDC.restype = H
            g.CreateCompatibleBitmap.argtypes = [H, ctypes.c_int, ctypes.c_int]
            g.CreateCompatibleBitmap.restype = H
            g.SelectObject.argtypes = [H, H]
            g.SelectObject.restype = H
            g.BitBlt.argtypes = [H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 H, ctypes.c_int, ctypes.c_int, wt.DWORD]
            g.BitBlt.restype = wt.BOOL
            g.DeleteObject.argtypes = [H]
            g.DeleteDC.argtypes = [H]
            g.GdiFlush.argtypes = []
            k.GetModuleHandleW.argtypes = [wt.LPCWSTR]
            k.GetModuleHandleW.restype = H
            cls._api = (ctypes, wt, u, g, k, d)
        except Exception:
            cls._api = False
        return cls._api or None

    @staticmethod
    def _on_screen(u, pt, w, h) -> bool:
        vx = u.GetSystemMetrics(76)     # SM_XVIRTUALSCREEN
        vy = u.GetSystemMetrics(77)     # SM_YVIRTUALSCREEN
        vw = u.GetSystemMetrics(78)     # SM_CXVIRTUALSCREEN
        vh = u.GetSystemMetrics(79)     # SM_CYVIRTUALSCREEN
        if vw <= 0 or vh <= 0:
            return True
        return not (pt.x + w <= vx or pt.x >= vx + vw or pt.y + h <= vy or pt.y >= vy + vh)

    def __init__(self, top):
        self.hwnd = None
        api = self.api()
        if api is None:
            return
        ctypes, wt, u, g, k, _d = api
        client = hdc = mem = bmp = old = None
        try:
            client = int(top.winfo_id())
            owner = u.GetAncestor(client, 2)            # GA_ROOT: 带标题栏的外层窗口
            if not owner or not u.IsWindowVisible(owner) or u.IsIconic(owner):
                return
            rc = wt.RECT()
            u.GetClientRect(client, ctypes.byref(rc))
            w, h = rc.right - rc.left, rc.bottom - rc.top
            if w <= 0 or h <= 0:
                return
            pt = wt.POINT(0, 0)
            u.ClientToScreen(client, ctypes.byref(pt))
            if not self._on_screen(u, pt, w, h):
                return

            # 直接从窗口自身的 DWM 重定向表面截取: 不受其他遮挡窗口影响, 也不会在提权桌面下 GetDC(None) 失败
            hdc = u.GetDC(client)
            if not hdc:
                return
            mem = g.CreateCompatibleDC(hdc)
            bmp = g.CreateCompatibleBitmap(hdc, w, h)
            old = g.SelectObject(mem, bmp)
            if not g.BitBlt(mem, 0, 0, w, h, hdc, 0, 0, 0x00CC0020):       # SRCCOPY
                return
            ex = 0x00080000 | 0x00000080 | 0x08000000 | 0x00000020
            #    LAYERED      TOOLWINDOW   NOACTIVATE   TRANSPARENT(鼠标穿透)
            ov = u.CreateWindowExW(ex, "STATIC", None, 0x80000000,   # WS_POPUP
                                   pt.x, pt.y, w, h, owner, None,
                                   k.GetModuleHandleW(None), None)
            if not ov:
                return
            size = wt.SIZE(w, h)
            src = wt.POINT(0, 0)
            if not u.UpdateLayeredWindow(ov, None, ctypes.byref(pt), ctypes.byref(size),
                                         mem, ctypes.byref(src), 0, None,
                                         0x00000004):              # ULW_OPAQUE
                u.DestroyWindow(ov)
                return
            u.ShowWindow(ov, 4)                         # SW_SHOWNOACTIVATE, 不抢焦点
            self.hwnd = ov
        except Exception:
            self.hwnd = None
        finally:
            try:
                if old:
                    g.SelectObject(mem, old)
                if bmp:
                    g.DeleteObject(bmp)
                if mem:
                    g.DeleteDC(mem)
                if hdc and client:
                    u.ReleaseDC(client, hdc)
            except Exception:
                pass

    def close(self):
        if not self.hwnd:
            return
        api = self.api()
        ctypes, wt, u, g, k, d = api
        try:
            g.GdiFlush()                                # 新页面的绘制全部交给系统
            if d is not None:
                d.DwmFlush()                            # 等这一帧真正显示到屏幕上
        except Exception:
            pass
        try:
            u.DestroyWindow(self.hwnd)
        except Exception:
            pass
        self.hwnd = None


class _RegionCover:
    """可复用的局部双缓冲盖板: 滚动 Canvas 期间盖住旧画面, 等所有子控件完整画完再一次性揭开.

    与 _Snapshot 原理相同, 但复用了底层分层窗口与兼容位图, 避免滚动时反复创建/销毁 HWND 和 GDI 对象;
    控件销毁时自动释放全部句柄.
    """

    def __init__(self, widget):
        self.widget = widget
        self._ov = None
        self._mem = None
        self._bmp = None
        self._old = None
        self._size = None
        self._active = False
        try:
            widget.bind("<Destroy>", self._on_destroy, add="+")
        except Exception:
            pass

    def _on_destroy(self, event):
        if event.widget is self.widget:
            self.close()

    def capture(self) -> bool:
        if self._active:
            return True
        api = _Snapshot.api()
        if api is None:
            return False
        w_obj = self.widget
        try:
            if not w_obj.winfo_exists() or not w_obj.winfo_ismapped():
                return False
            top = w_obj.winfo_toplevel()
            if _freeze_depth.get(str(top), 0) > 0:
                return False
            hwnd = int(w_obj.winfo_id())
        except Exception:
            return False

        ctypes, wt, u, g, k, _d = api
        hdc = None
        try:
            owner = u.GetAncestor(hwnd, 2)              # GA_ROOT
            if not owner or not u.IsWindowVisible(owner) or u.IsIconic(owner):
                return False
            rc = wt.RECT()
            u.GetClientRect(hwnd, ctypes.byref(rc))
            w, h = rc.right - rc.left, rc.bottom - rc.top
            if w <= 0 or h <= 0:
                return False
            pt = wt.POINT(0, 0)
            u.ClientToScreen(hwnd, ctypes.byref(pt))
            if not _Snapshot._on_screen(u, pt, w, h):
                return False

            hdc = u.GetDC(hwnd)
            if not hdc:
                return False
            if self._mem is None:
                self._mem = g.CreateCompatibleDC(hdc)
                if not self._mem:
                    return False
            if self._size != (w, h) or not self._bmp:
                if self._old and self._mem:
                    g.SelectObject(self._mem, self._old)
                    self._old = None
                if self._bmp:
                    g.DeleteObject(self._bmp)
                    self._bmp = None
                self._bmp = g.CreateCompatibleBitmap(hdc, w, h)
                if not self._bmp:
                    return False
                self._old = g.SelectObject(self._mem, self._bmp)
                self._size = (w, h)

            if not g.BitBlt(self._mem, 0, 0, w, h, hdc, 0, 0, 0x00CC0020):
                return False
            if not self._ov:
                ex = 0x00080000 | 0x00000080 | 0x08000000 | 0x00000020
                self._ov = u.CreateWindowExW(ex, "STATIC", None, 0x80000000,
                                             pt.x, pt.y, w, h, owner, None,
                                             k.GetModuleHandleW(None), None)
                if not self._ov:
                    return False
            size = wt.SIZE(w, h)
            src = wt.POINT(0, 0)
            if not u.UpdateLayeredWindow(self._ov, None, ctypes.byref(pt), ctypes.byref(size),
                                         self._mem, ctypes.byref(src), 0, None,
                                         0x00000004):
                return False
            u.ShowWindow(self._ov, 4)                   # SW_SHOWNOACTIVATE
            self._active = True
            return True
        except Exception:
            return False
        finally:
            if hdc:
                try:
                    u.ReleaseDC(hwnd, hdc)
                except Exception:
                    pass

    def release(self):
        if not self._active:
            return
        self._active = False
        api = _Snapshot.api()
        if api is None or not self._ov:
            return
        _ctypes, _wt, u, g, _k, d = api
        try:
            g.GdiFlush()
        except Exception:
            pass
        try:
            u.ShowWindow(self._ov, 0)                   # SW_HIDE
        except Exception:
            pass

    def close(self):
        self._active = False
        api = _Snapshot.api()
        if api is None:
            self._ov = self._mem = self._bmp = self._old = self._size = None
            return
        _ctypes, _wt, u, g, _k, _d = api
        try:
            if self._ov:
                u.DestroyWindow(self._ov)
        except Exception:
            pass
        try:
            if self._old and self._mem:
                g.SelectObject(self._mem, self._old)
            if self._bmp:
                g.DeleteObject(self._bmp)
            if self._mem:
                g.DeleteDC(self._mem)
        except Exception:
            pass
        self._ov = self._mem = self._bmp = self._old = self._size = None


class _SmoothScroll:
    """把 Canvas + Scrollbar + inner Frame 的滚动合并成按帧双缓冲切换:
    1) inner 仅在宽高变化时才更新 scrollregion (滚动引起的 y 位移不反复算 bbox);
    2) 高频滚轮 / 拖拽滚动条合并到单次 idle 刷新, 避免事件积压;
    3) 每次实际滚动用 _RegionCover 盖住旧画面, 后台画完所有子控件后再整帧切出, 消除分块渲染.
    """

    def __init__(self, canvas: tk.Canvas, scrollbar, inner: tk.Frame, win_id: int):
        self.canvas = canvas
        self.scrollbar = scrollbar
        self.inner = inner
        self.win_id = win_id
        self._cover = _RegionCover(canvas)
        self._inner_size = None
        self._canvas_width = None
        self._pending_yview = None
        self._pending_wheel = 0
        self._wheel_accum = 0
        self._scroll_job = None

        self.on_after_scroll = None

        scrollbar.configure(command=self.on_yview)
        inner.bind("<Configure>", self._on_inner_configure, add="+")
        canvas.bind("<Configure>", self._on_canvas_configure, add="+")
        canvas.bind("<MouseWheel>", self.on_wheel, add="+")
        canvas._oit_wheel_fn = self.on_wheel

    def _on_inner_configure(self, event):
        sz = (event.width, event.height)
        if sz != self._inner_size:
            self._inner_size = sz
            try:
                self.canvas.configure(scrollregion=self.canvas.bbox("all"))
            except Exception:
                pass

    def _on_canvas_configure(self, event):
        if event.width != self._canvas_width:
            self._canvas_width = event.width
            try:
                self.canvas.itemconfig(self.win_id, width=event.width)
            except Exception:
                pass

    def on_yview(self, *args):
        if not args:
            return self.canvas.yview()
        if args[0] == "scroll" and len(args) >= 3 and self._pending_yview \
                and self._pending_yview[0] == "scroll" and self._pending_yview[2] == args[2]:
            try:
                n = int(self._pending_yview[1]) + int(args[1])
                self._pending_yview = ("scroll", str(n), args[2])
            except Exception:
                self._pending_yview = args
        else:
            self._pending_yview = args
        self._schedule()

    def on_wheel(self, event):
        try:
            lo, hi = self.canvas.yview()
            if lo <= 0.0 and hi >= 1.0:
                return "break"
        except Exception:
            pass
        delta = getattr(event, "delta", 0)
        if not delta:
            return "break"
        self._wheel_accum += -delta
        steps = int(self._wheel_accum / 120)
        if steps == 0:
            return "break"
        self._wheel_accum -= steps * 120
        self._pending_wheel += steps
        self._schedule()
        return "break"

    def moveto(self, fraction: float):
        self._cancel_pending()
        self._apply_covered(lambda: self.canvas.yview_moveto(fraction))
        self._after_scroll()

    def _after_scroll(self):
        if self.on_after_scroll is not None:
            self.on_after_scroll()

    def _cancel_pending(self):
        self._pending_yview = None
        self._pending_wheel = 0
        self._wheel_accum = 0
        if self._scroll_job is not None:
            try:
                self.canvas.after_cancel(self._scroll_job)
            except Exception:
                pass
            self._scroll_job = None

    def _schedule(self):
        if self._scroll_job is None:
            try:
                self._scroll_job = self.canvas.after(16, self._flush)
            except Exception:
                self._scroll_job = None

    def _apply_covered(self, action):
        covered = self._cover.capture()
        try:
            action()
            if covered:
                settle(self.canvas, passes=1)
        finally:
            if covered:
                self._cover.release()

    def _flush(self):
        self._scroll_job = None
        yv = self._pending_yview
        wh = self._pending_wheel
        self._pending_yview = None
        self._pending_wheel = 0
        if yv is None and not wh:
            return
        try:
            if not self.canvas.winfo_exists():
                return
        except Exception:
            return

        def do():
            if yv is not None:
                self.canvas.yview(*yv)
            if wh:
                self.canvas.yview_scroll(wh, "units")

        if yv is not None and yv[0] == "moveto":
            self._apply_covered(do)
        else:
            do()
        self._after_scroll()


def attach_smooth_scroll(canvas: tk.Canvas, scrollbar, inner: tk.Frame, win_id: int) -> _SmoothScroll:
    """给任意 Canvas + Scrollbar + inner Frame 装上双缓冲防分块滚动."""
    return _SmoothScroll(canvas, scrollbar, inner, win_id)


def _client_hwnd(top):
    # 只冻结窗口内部 (客户区), 不冻结带标题栏的外层窗口:
    # 对顶层窗口用 WM_SETREDRAW 会让系统误以为窗口不可见, 反而会闪.
    if _user32 is None:
        return None
    try:
        return int(top.winfo_id())
    except Exception:
        return None


def _drain(widget, limit: int = 400) -> int:
    """处理已经排队的窗口事件 (尺寸变化 / 重绘), 不等待新事件. 返回处理了几个."""
    if _WINDOW_EVENTS is None:
        return 0
    n = 0
    try:
        tk_ = widget.tk
        while n < limit and tk_.dooneevent(_WINDOW_EVENTS):
            n += 1
    except Exception:
        pass
    return n


def settle(widget, passes: int = 4):
    """把布局真正算完: 布局 → 尺寸变化事件 (会触发自动换行等二次调整) → 再布局…"""
    for _ in range(passes):
        try:
            widget.update_idletasks()
        except Exception:
            return
        if not _drain(widget):
            break
    try:
        widget.update_idletasks()
    except Exception:
        pass


@contextmanager
def frozen(widget):
    """with frozen(w): ... 里的所有改动, 结束时一次性显示."""
    top = widget.winfo_toplevel()
    key = str(top)
    depth = _freeze_depth.get(key, 0)
    hwnd = _client_hwnd(top) if depth == 0 else None
    snap = _Snapshot(top) if depth == 0 else None
    _freeze_depth[key] = depth + 1
    if hwnd:
        try:
            _user32.SendMessageW(hwnd, _WM_SETREDRAW, 0, 0)
        except Exception:
            hwnd = None
    try:
        yield
    finally:
        try:
            if depth == 0:
                settle(top, passes=2)       # 冻结状态下把布局 (含二次布局) 算完
        finally:
            _freeze_depth[key] -= 1
            if _freeze_depth[key] <= 0:
                _freeze_depth.pop(key, None)
                if hwnd:
                    try:
                        _user32.SendMessageW(hwnd, _WM_SETREDRAW, 1, 0)
                        _user32.RedrawWindow(hwnd, None, None,
                                             _RDW_INVALIDATE | _RDW_ALLCHILDREN
                                             | _RDW_UPDATENOW)
                    except Exception:
                        pass
                # 重绘请求已经排队, 立刻一口气画完, 不留到下一轮事件循环里零碎地画
                try:
                    settle(top, passes=1)
                finally:
                    if snap is not None:
                        snap.close()            # 新页面已经完整显示, 撤掉定格画面


def smooth(method):
    """装饰器: 让控件自己的刷新方法在 frozen() 中执行."""
    def wrapper(self, *args, **kwargs):
        with frozen(self):
            return method(self, *args, **kwargs)
    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


def cfg(widget, **options):
    """只修改真正变了的属性.

    Tk 对 config(text=同样的文字) 也会重新排版, 重画一次; 每秒刷新的页面
    (联网页 / 规则页) 和切页面时的刷新, 大部分属性其实没变, 跳过它们能省掉
    大量无用的重绘."""
    changed = {}
    for k, v in options.items():
        try:
            if str(widget.cget(k)) == str(v):
                continue
        except Exception:
            pass
        changed[k] = v
    if changed:
        setter = getattr(widget, "configure", None) or getattr(widget, "config")
        setter(**changed)
    return bool(changed)


def set_var(var, value):
    """StringVar 只在值变化时才写入 (写入会触发重画)."""
    if var.get() != value:
        var.set(value)


def show_grid(widget, **kw):
    """grid_remove 过的控件重新显示; 已经显示的不动 (重复 grid 会触发重新布局)."""
    if widget.winfo_manager() != "grid":
        widget.grid(**kw)


def hide_grid(widget):
    if widget.winfo_manager() == "grid":
        widget.grid_remove()


def show_pack(widget, **kw):
    if widget.winfo_manager() != "pack":
        widget.pack(**kw)


def hide_pack(widget):
    if widget.winfo_manager() == "pack":
        widget.pack_forget()


class LoadingCover(tk.Frame):
    """盖在整个窗口上的加载页 (带进度条). 用在耗时较长, 无法避免停顿的操作前:
    启动, 以及切换语言 / 字号 / 主题时重建界面.

    下面的页面在加载页后面一页一页地建好, 每建好一页进度条前进一格;
    最后在 frozen() 里撤掉加载页, 新界面整窗一次显示."""

    def __init__(self, root, text: str = "", sub: str = "", steps: int = 1):
        super().__init__(root)
        self._steps = max(1, int(steps))
        self._done = 0
        self.box = tk.Frame(self)
        self.box.place(relx=0.5, rely=0.45, anchor="center")

        self.track = tk.Frame(self.box, width=config.scaled(220), height=config.scaled(4))
        self.track.pack(pady=(0, config.scaled(16)))
        self.track.pack_propagate(False)
        self.fill = tk.Frame(self.track)
        self.fill.place(x=0, y=0, relheight=1, relwidth=0.04)

        self.lbl = tk.Label(self.box, text=text, font=config.font("h2", bold=True))
        self.lbl.pack()
        self.lbl_sub = tk.Label(self.box, text=sub, font=config.font("small"))
        if sub:
            self.lbl_sub.pack(pady=(6, 0))
        self.recolor()

    def recolor(self):
        """换主题后跟着换色 (COLORS 会被原地更新)."""
        bg = COLORS["bg"]
        try:
            for w in (self, self.box):
                w.configure(bg=bg)
            self.track.configure(bg=COLORS["border"])
            self.fill.configure(bg=COLORS["accent"])
            self.lbl.configure(bg=bg, fg=COLORS["text"])
            self.lbl_sub.configure(bg=bg, fg=COLORS["text_dim"])
        except Exception:
            pass

    def show(self):
        self.place(x=0, y=0, relwidth=1, relheight=1)
        self.paint()
        return self

    def paint(self):
        """把加载页 (只有它自己, 很快) 立刻画到屏幕上."""
        try:
            self.lift()                 # 后建的控件默认叠在上面, 每次都提到最上层
            settle(self, passes=2)
        except Exception:
            pass

    def step(self, sub: str = None):
        self._done = min(self._steps, self._done + 1)
        try:
            self.fill.place_configure(relwidth=max(0.04, self._done / self._steps))
            if sub is not None:
                self.lbl_sub.configure(text=sub)
                if not self.lbl_sub.winfo_manager():
                    self.lbl_sub.pack(pady=(6, 0))
        except Exception:
            pass
        self.paint()

    def close(self):
        try:
            self.destroy()
        except Exception:
            pass


class FocusGlow:
    """都市之意: 给获得焦点的输入框 / 下拉框套一圈镂空的暖色灯圈.

    用四块很窄的 Canvas 贴在控件外侧 (放在控件下面一层), 中间保持透明 ——
    不改变控件本身的边框, 也不挡输入. 灯圈颜色 / 宽度来自主题的 focus_halo.
    """

    INPUTS = (tk.Entry, tk.Text, tk.Listbox, ttk.Entry, ttk.Combobox, ttk.Spinbox)

    def __init__(self, root):
        self.root = root
        self.widget = None
        self.parent = None
        self.strips = []
        self._job = None
        self._geom = None
        root.bind_all("<FocusIn>", self._on_focus, add="+")
        root.bind_all("<FocusOut>", self._on_blur, add="+")

    # ---- 主题 ----

    def _halo(self):
        return (getattr(config, "THEME_DATA", None) or {}).get("focus_halo")

    def _colors(self):
        halo = self._halo() or {}
        return halo.get("colors") or ("#2A1B0C", "#5C3C16", "#A8702C", "#E7C082")

    def _pad(self):
        return int((self._halo() or {}).get("pad") or len(self._colors()))

    # ---- 事件 ----

    def _on_focus(self, event):
        if not self._halo():
            return
        w = event.widget
        if not isinstance(w, self.INPUTS):
            return
        self.show(w)

    def _on_blur(self, event):
        if self.widget is not None and event.widget is self.widget:
            self.hide()

    # ---- 绘制 ----

    def show(self, widget):
        if self.widget is widget and self.strips:
            self._place()
            return
        self.hide()
        halo = self._halo()
        if not halo:
            return
        try:
            parent = widget.winfo_toplevel()
        except Exception:
            return
        if parent is None:
            return
        self.widget = widget
        self.parent = parent
        self._geom = None
        bg = COLORS.get("bg", "#000000")
        for name in ("top", "bottom", "left", "right"):
            c = tk.Canvas(parent, bg=bg, highlightthickness=0, bd=0,
                          takefocus=0, cursor="arrow")
            c._oit_glow = name
            c.bind("<Button-1>", lambda e, w=widget: w.focus_set())
            self.strips.append(c)
        self._place()
        self._job = self.root.after(90, self._tick)

    def _paint(self, w, h, pad, colors):
        for name, c in zip(("top", "bottom", "left", "right"), self.strips):
            c.delete("all")
            if name in ("top", "bottom"):
                width, height = w + pad * 2, pad
                c.configure(width=width, height=height)
                for i, color in enumerate(colors):
                    y = i if name == "top" else (pad - 1 - i)
                    c.create_line(0, y + 0.5, width, y + 0.5, fill=color)
            else:
                width, height = pad, h
                c.configure(width=width, height=height)
                for i, color in enumerate(colors):
                    x = i if name == "left" else (pad - 1 - i)
                    c.create_line(x + 0.5, 0, x + 0.5, height, fill=color)

    def _place(self):
        w = self.widget
        if w is None:
            return
        try:
            if not w.winfo_exists() or not w.winfo_ismapped():
                self.hide()
                return
        except Exception:
            self.hide()
            return
        if not self._halo():
            self.hide()
            return
        pad = self._pad()
        colors = self._colors()
        try:
            x = w.winfo_rootx() - self.parent.winfo_rootx()
            y = w.winfo_rooty() - self.parent.winfo_rooty()
        except Exception:
            x, y = w.winfo_x(), w.winfo_y()
        ww, hh = w.winfo_width(), w.winfo_height()
        if ww <= 1 or hh <= 1:
            return
        if self._geom != (x, y, ww, hh, pad, tuple(colors)):
            self._paint(ww, hh, pad, colors)
            self._geom = (x, y, ww, hh, pad, tuple(colors))
        else:
            return
        boxes = {
            "top":    (x - pad, y - pad, ww + pad * 2, pad),
            "bottom": (x - pad, y + hh, ww + pad * 2, pad),
            "left":   (x - pad, y, pad, hh),
            "right":  (x + ww, y, pad, hh),
        }
        for c in self.strips:
            bx, by, bw, bh = boxes[c._oit_glow]
            c.place(x=bx, y=by, width=bw, height=bh)
            try:
                c.lift()
            except Exception:
                pass

    def _tick(self):
        self._job = None
        if not self.widget or not self._halo():
            self.hide()
            return
        try:
            self._place()
        except Exception:
            self.hide()
            return
        self._job = self.root.after(90, self._tick)

    def hide(self):
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        for c in self.strips:
            try:
                c.destroy()
            except Exception:
                pass
        self.strips = []
        self.widget = None
        self.parent = None
        self._geom = None

    def close(self):
        self.hide()
