

import tkinter as tk
from tkinter import ttk

import config
from i18n import tr


_RUNTIME = {
    "root": None,
    "toasts": [],
    "queue": [],
    "recording": None,
}

KIND_STYLE = {
    "info": ("accent", 4200),
    "success": ("success", 4200),
    "warn": ("warn", 6500),
    "error": ("danger", 9000),
}
MAX_VISIBLE = 3
TOAST_W = 380
PAD = 18
GAP = 8


def font_of(size_key: str, bold: bool = False):
    return config.font(size_key, bold)


def bind_root(root):
    _RUNTIME["root"] = root


def _root():
    return _RUNTIME["root"]



class Toast(tk.Toplevel):
    def __init__(self, master, text: str, kind: str = "info", timeout: int = None):
        super().__init__(master, bg=config.COLORS["bg"])
        color_key, default_ms = KIND_STYLE.get(kind, KIND_STYLE["info"])
        accent = config.COLORS.get(color_key, config.COLORS["accent"])
        self._alive = True
        self._ms = timeout if timeout is not None else default_ms

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.97)
        except Exception:
            pass

        outer = tk.Frame(self, bg=accent, bd=0, highlightthickness=0)
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg=config.COLORS["bg_card"])
        inner.pack(fill="both", expand=True, padx=config.scaled(2), pady=config.scaled(2))

        tk.Label(
            inner, text=text, bg=config.COLORS["bg_card"], fg=config.COLORS["text"],
            font=font_of("small"), justify="left", anchor="w",
            wraplength=config.scaled(TOAST_W - 2 * PAD)
        ).pack(fill="x", padx=config.scaled(PAD), pady=config.scaled(12))


        for w in (self, outer, inner):
            w.bind("<Button-1>", lambda e: self.close())

        self._reposition()
        self.after(self._ms, self.close)

    def _reposition(self):
        root = _root()
        if root is None:
            return
        try:
            self.update_idletasks()
            w = max(config.scaled(TOAST_W), self.winfo_reqwidth())
            h = self.winfo_reqheight()
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            index = _RUNTIME["toasts"].index(self) if self in _RUNTIME["toasts"] else 0
            x = sw - w - config.scaled(PAD)
            y = sh - config.scaled(PAD) - (index + 1) * (h + config.scaled(GAP))
            self.geometry(f"{w}x{h}+{max(0, x)}+{max(config.scaled(PAD), y)}")
        except Exception:
            pass

    def close(self):
        if not self._alive:
            return
        self._alive = False
        try:
            if self in _RUNTIME["toasts"]:
                _RUNTIME["toasts"].remove(self)
            self.destroy()
        except Exception:
            pass
        _restack()
        _drain()


def _restack():
    for t in list(_RUNTIME["toasts"]):
        t._reposition()


def _drain():
    while _RUNTIME["queue"] and len(_RUNTIME["toasts"]) < MAX_VISIBLE:
        text, kind, timeout = _RUNTIME["queue"].pop(0)
        _show_now(text, kind, timeout)


def _show_now(text: str, kind: str, timeout):
    root = _root()
    if root is None:

        _record(text, kind)
        return
    try:
        if not root.winfo_exists():
            _record(text, kind)
            return
        t = Toast(root, text, kind, timeout)
        _RUNTIME["toasts"].append(t)
        _restack()
    except Exception:
        _record(text, kind)


def _record(text: str, kind: str):
    if _RUNTIME["recording"] is not None:
        _RUNTIME["recording"].append((kind, text))


def toast(text, kind: str = "info", timeout=None):
    text = str(text)
    _record(text, kind)
    if len(_RUNTIME["toasts"]) >= MAX_VISIBLE:
        _RUNTIME["queue"].append((text, kind, timeout))
        return None
    _show_now(text, kind, timeout)
    return None


def info(text, timeout=None):
    return toast(text, "info", timeout)


def success(text, timeout=None):
    return toast(text, "success", timeout)


def warn(text, timeout=None):
    return toast(text, "warn", timeout)


def error(text, timeout=None):
    return toast(text, "error", timeout)



class Confirm(tk.Toplevel):
    def __init__(self, master, title: str, message: str,
                 ok_text: str = None, cancel_text: str = None, danger: bool = False):
        super().__init__(master, bg=config.COLORS["bg"])
        self.result = False
        self.title(title if master is None else "")
        self.transient(master)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda e: self._cancel())
        self.bind("<Return>", lambda e: self._ok())

        body = tk.Frame(self, bg=config.COLORS["bg"])
        body.pack(fill="both", expand=True, padx=config.scaled(20), pady=config.scaled(16))

        tk.Label(
            body, text=title, bg=config.COLORS["bg"], fg=config.COLORS["text"],
            font=font_of("h2", bold=True), anchor="w"
        ).pack(anchor="w", pady=(0, config.scaled(8)))

        tk.Label(
            body, text=message, bg=config.COLORS["bg"], fg=config.COLORS["text"],
            font=font_of("body"), justify="left", anchor="w",
            wraplength=config.scaled(520)
        ).pack(anchor="w", fill="x")

        btns = tk.Frame(self, bg=config.COLORS["bg"])
        btns.pack(fill="x", padx=config.scaled(20), pady=(0, config.scaled(16)))
        ttk.Button(
            btns, text=ok_text or tr("确定"),
            style="Danger.TButton" if danger else "Accent.TButton", command=self._ok
        ).pack(side="right")
        ttk.Button(
            btns, text=cancel_text or tr("取消"), command=self._cancel
        ).pack(side="right", padx=(0, config.scaled(8)))

        self._center(master)
        try:
            self.grab_set()
            self.focus_force()
        except Exception:
            pass

    def _center(self, master):
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        try:
            px, py = master.winfo_rootx(), master.winfo_rooty()
            pw, ph = master.winfo_width(), master.winfo_height()
            x = px + max(0, (pw - w) // 2)
            y = py + max(0, (ph - h) // 3)
        except Exception:
            x = y = 160
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _ok(self):
        self.result = True
        self.destroy()

    def _cancel(self):
        self.result = False
        self.destroy()

    def wait(self) -> bool:
        self.wait_window(self)
        return bool(self.result)


def ask(message, title: str = None, ok_text: str = None,
        cancel_text: str = None, danger: bool = False) -> bool:
    root = _root()
    title = title or tr("提示")
    _record(f"[ask] {title}: {message}", "ask")
    if root is None:
        return False
    try:
        if not root.winfo_exists():
            return False
        dlg = Confirm(root, title, message, ok_text, cancel_text, danger)
        return dlg.wait()
    except Exception:
        return False



class Notice(tk.Toplevel):
    """只能手动关闭的提醒窗: 不会自动消失, 点窗口本身也不会关, 只能点按钮或右上角 ✕.

    置顶但不抢占输入 (不 grab), 不妨碍继续操作主窗口.
    buttons: [(文字, 回调或 None, 是否强调)], 点任何一个按钮都会先关闭窗口再执行回调.
    options: [(键, 文字, 默认勾选)] 显示成一列勾选框; 点任何按钮时先把 {键: 是否勾选} 交给 on_options
    (点 ✕ 关掉时不交)."""

    def __init__(self, master, title: str, message: str, buttons=None, options=None, on_options=None):
        super().__init__(master, bg=config.COLORS["bg"],
                         highlightbackground=config.COLORS["accent"], highlightthickness=2)
        self._alive = True
        self.title(title)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self.close)

        body = tk.Frame(self, bg=config.COLORS["bg"])
        body.pack(fill="both", expand=True, padx=config.scaled(22), pady=config.scaled(18))
        tk.Label(
            body, text=title, bg=config.COLORS["bg"], fg=config.COLORS["text"],
            font=font_of("h2", bold=True), anchor="w"
        ).pack(anchor="w", pady=(0, config.scaled(8)))
        self.lbl = tk.Label(
            body, text=message, bg=config.COLORS["bg"], fg=config.COLORS["text"],
            font=font_of("body"), justify="left", anchor="w",
            wraplength=config.scaled(440)
        )
        self.lbl.pack(anchor="w", fill="x")
        self._opts = {}
        self._on_options = on_options
        if options:
            box = tk.Frame(body, bg=config.COLORS["bg"])
            box.pack(anchor="w", fill="x", pady=(config.scaled(12), 0))
            for key, text, default in options:
                var = tk.BooleanVar(value=bool(default))
                tk.Checkbutton(box, text=text, variable=var, bg=config.COLORS["bg"],
                               fg=config.COLORS["text"], activebackground=config.COLORS["bg"],
                               selectcolor=config.COLORS["bg_card"], font=font_of("body"),
                               anchor="w").pack(anchor="w")
                self._opts[key] = var

        btns = tk.Frame(self, bg=config.COLORS["bg"])
        btns.pack(fill="x", padx=config.scaled(22), pady=(0, config.scaled(18)))
        for text, cb, accent in reversed(buttons or [(tr("知道了"), None, True)]):
            ttk.Button(btns, text=text, style="Accent.TButton" if accent else "TButton",
                       command=lambda c=cb: self._press(c)).pack(
                side="right", padx=(config.scaled(8), 0))
        self._place(master)

    def _place(self, master):
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")

    def _press(self, cb):
        values = {k: bool(v.get()) for k, v in self._opts.items()}
        self.close()
        if self._on_options and values:
            self._on_options(values)
        if cb:
            cb()

    def alive(self) -> bool:
        try:
            return self._alive and bool(self.winfo_exists())
        except Exception:
            return False

    def close(self):
        if not self._alive:
            return
        self._alive = False
        try:
            self.destroy()
        except Exception:
            pass


def notice(title: str, message: str, buttons=None, options=None, on_options=None):
    """弹出一个只能手动关闭的提醒窗, 返回窗口对象 (没有主窗口时返回 None)."""
    root = _root()
    _record(f"[notice] {title}: {message}", "notice")
    if root is None:
        return None
    try:
        if not root.winfo_exists():
            return None
        return Notice(root, title, message, buttons, options, on_options)
    except Exception:
        return None


class NamePrompt(tk.Toplevel):
    """填写用户名的窗口: 不填就关不掉 (右上角 ✕ 不起作用), 填好按"确定"或回车.
    置顶但不 grab; 填好后把名字交给 on_done."""

    def __init__(self, master, title: str, message: str, on_done, limit: int = 24):
        super().__init__(master, bg=config.COLORS["bg"],
                         highlightbackground=config.COLORS["accent"], highlightthickness=2)
        self._on_done = on_done
        self._limit = limit
        self.title(title)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", lambda: None)

        body = tk.Frame(self, bg=config.COLORS["bg"])
        body.pack(fill="both", expand=True, padx=config.scaled(22), pady=config.scaled(18))
        tk.Label(
            body, text=title, bg=config.COLORS["bg"], fg=config.COLORS["text"],
            font=font_of("h2", bold=True), anchor="w"
        ).pack(anchor="w", pady=(0, config.scaled(8)))
        tk.Label(
            body, text=message, bg=config.COLORS["bg"], fg=config.COLORS["text"],
            font=font_of("body"), justify="left", anchor="w",
            wraplength=config.scaled(440)
        ).pack(anchor="w", fill="x")
        self.var = tk.StringVar()
        self.entry = ttk.Entry(body, textvariable=self.var, font=font_of("body"), width=30)
        self.entry.pack(anchor="w", fill="x", pady=(config.scaled(12), 0))
        self.entry.bind("<Return>", lambda e: self._ok())

        btns = tk.Frame(self, bg=config.COLORS["bg"])
        btns.pack(fill="x", padx=config.scaled(22), pady=(0, config.scaled(18)))
        self.btn = ttk.Button(btns, text=tr("确定"), style="Accent.TButton", command=self._ok,
                              state="disabled")
        self.btn.pack(side="right")
        self.var.trace_add("write", lambda *_: self._changed())
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        try:
            self.entry.focus_force()
        except Exception:
            pass

    def _changed(self):
        text = self.var.get()
        if len(text) > self._limit:
            text = text[:self._limit]
            self.var.set(text)         # 写回时不会再触发这个回调
        self.btn.config(state="normal" if text.strip() else "disabled")

    def _ok(self):
        name = self.var.get().strip()
        if not name:
            return
        try:
            self.destroy()
        except Exception:
            pass
        self._on_done(name)


def prompt_name(title: str, message: str, on_done, limit: int = 24):
    """弹出填写用户名的窗口 (不填就关不掉), 返回窗口对象 (没有主窗口时返回 None)."""
    root = _root()
    _record(f"[name] {title}: {message}", "notice")
    if root is None:
        return None
    try:
        if not root.winfo_exists():
            return None
        return NamePrompt(root, title, message, on_done, limit)
    except Exception:
        return None


def start_recording():
    _RUNTIME["recording"] = []
    return _RUNTIME["recording"]


def stop_recording():
    out = _RUNTIME["recording"] or []
    _RUNTIME["recording"] = None
    return out


def messages():
    return list(_RUNTIME["recording"] or [])


def close_all():
    for t in list(_RUNTIME["toasts"]):
        t.close()



showinfo = info
showwarning = warn
showerror = error
