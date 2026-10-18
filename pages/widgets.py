

import sys
import tkinter as tk
from contextlib import contextmanager
from tkinter import ttk

import config
from config import COLORS


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
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfig(self._win, width=e.width)
        )
        self._wheel_bound = set()
        self.bind_children()
        self.canvas.bind("<MouseWheel>", self._wheel)

    def bind_children(self):
        def walk(w):
            try:
                key = w.winfo_id()
            except Exception:
                return
            if key not in self._wheel_bound:
                self._wheel_bound.add(key)
                w.bind("<MouseWheel>", self._wheel, add="+")
            for c in w.winfo_children():
                walk(c)
        walk(self.inner)

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def scroll_to_top(self):
        self.canvas.yview_moveto(0.0)

    def scroll_to(self, widget):
        """滚到 widget (inner 里直接放着的一块) 的顶部."""
        try:
            self.update_idletasks()
            total = max(1, self.inner.winfo_height())
            self.canvas.yview_moveto(max(0.0, widget.winfo_y() - 4) / total)
        except Exception:
            pass


class Modal(tk.Toplevel):

    def __init__(self, parent, title: str, width: int = 460, height: int = 260):
        super().__init__(parent, bg=COLORS["bg"])
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.result = None

        body = tk.Frame(self, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=20, pady=16)
        self.body = body

        self.buttons = tk.Frame(self, bg=COLORS["bg"])
        self.buttons.pack(fill="x", padx=20, pady=(0, 16))

        self.bind("<Escape>", lambda e: self.cancel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self._center(parent, width, height)
        self.grab_set()

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


def wrap_label(widget, pad: int = 16):
    def on_configure(event):
        width = max(120, event.width - pad)
        if widget.cget("wraplength") != width:
            widget.configure(wraplength=width)
    widget.bind("<Configure>", on_configure, add="+")
    return widget


def flavor(parent, text: str, bg: str = None, pad: int = 24):
    """作者写的风味文本 (来自 MECHANICS.md): 左边一道细竖线, 暗色小字, 和说明文字区分开.
    返回外框; 文字标签是 .label."""
    bg = bg or COLORS["bg"]
    fr = tk.Frame(parent, bg=bg)
    tk.Frame(fr, bg=COLORS["accent"], width=max(2, config.scaled(3))).pack(side="left", fill="y")
    lbl = tk.Label(fr, text=text, bg=bg, fg=COLORS["text_dim"], font=config.font("small"),
                   anchor="w", justify="left")
    lbl.pack(side="left", fill="x", expand=True, padx=(config.scaled(10), 0), pady=2)
    wrap_label(lbl, pad + config.scaled(14))
    fr.label = lbl
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

    def __init__(self, top):
        self.hwnd = None
        api = self.api()
        if api is None:
            return
        ctypes, wt, u, g, k, _d = api
        hdc = mem = bmp = old = None
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

            hdc = u.GetDC(None)                         # 截屏幕上实际看到的样子
            mem = g.CreateCompatibleDC(hdc)
            bmp = g.CreateCompatibleBitmap(hdc, w, h)
            old = g.SelectObject(mem, bmp)
            if not g.BitBlt(mem, 0, 0, w, h, hdc, pt.x, pt.y, 0x00CC0020):   # SRCCOPY
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
            if not u.UpdateLayeredWindow(ov, hdc, ctypes.byref(pt), ctypes.byref(size),
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
                if hdc:
                    u.ReleaseDC(None, hdc)
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
                settle(top)                 # 冻结状态下把布局 (含二次布局) 全部算完
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
                    settle(top, passes=2)
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
        widget.configure(**changed)
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
