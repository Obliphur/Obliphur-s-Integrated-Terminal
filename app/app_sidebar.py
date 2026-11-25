"""Local sidebar order, draggable divider and click/drag arbitration."""
import tkinter as tk
from pathlib import Path
import config
from config import COLORS
from i18n import tr
from knowledge.kb_store import read_json, write_json
from .app_base import _blend

DIVIDER = "__divider__"
DEFAULT_ORDER = ["prescript", "timer", "net", "log", "cal", "stats", "contract", "knowledge", "settings", DIVIDER, "comm", "reset"]


def normalize_order(value, keys=DEFAULT_ORDER):
    if not isinstance(value, list):
        value = DEFAULT_ORDER
    result = list(dict.fromkeys(k for k in value if isinstance(k, str) and k))
    if DIVIDER not in result:
        result.append(DIVIDER)
    for key in keys:
        if key not in result:
            result.insert(result.index(DIVIDER), key)
    return result


class SidebarMixin:
    def _build_sidebar_order(self):
        self._sidebar_prefs = Path(self.dm.data_dir) / "ui_prefs.json"
        self._nav_prefs = self._sidebar_prefs
        self.sidebar_order = normalize_order(read_json(self._sidebar_prefs).get("sidebar_order"), DEFAULT_ORDER)
        self.nav_divider = tk.Frame(self.sidebar, height=12, bg=COLORS["bg_card"], cursor="sb_v_double_arrow")
        self.nav_divider.pack_propagate(False)
        self.nav_line = tk.Frame(self.nav_divider, height=2, bg=COLORS["text_dim"])
        self.nav_line.pack(fill="x", padx=10, pady=5)
        self._nav_indicator = tk.Frame(self.sidebar, height=2, bg=COLORS["accent"])
        self._nav_drag = None
        self._nav_widgets = {**self.nav_btns, DIVIDER: self.nav_divider}
        for key, widget in self._nav_widgets.items():
            targets = [widget, self.nav_line] if key == DIVIDER else [widget]
            for target in targets:
                target.bind("<ButtonPress-1>", lambda e, k=key: self._nav_press(e, k))
                target.bind("<B1-Motion>", self._nav_motion)
                target.bind("<ButtonRelease-1>", self._nav_release)
            if key != DIVIDER:
                widget.bind("<Return>", lambda e, k=key: self._nav_activate(k))
                widget.bind("<space>", lambda e, k=key: self._nav_activate(k))
        self._pack_nav()
        self._init_minimized_dock()

    def _init_minimized_dock(self):
        if not hasattr(self, "_minimized_windows"):
            self._minimized_windows = []
        self._min_dock = tk.Frame(self.sidebar, bg=COLORS["bg_card"])
        if hasattr(self, "root") and self.root is not None:
            self.root._oit_window_dock = self
        self._render_minimized_dock()

    def dock_minimized_window(self, window, title: str = ""):
        if not hasattr(self, "_minimized_windows"):
            self._minimized_windows = []
        self._minimized_windows = [(w, t) for w, t in self._minimized_windows if w is not window and self._win_alive(w)]
        self._minimized_windows.append((window, str(title or "").strip() or tr("窗口")))
        self._render_minimized_dock()

    def undock_minimized_window(self, window):
        if not hasattr(self, "_minimized_windows"):
            return
        before = len(self._minimized_windows)
        self._minimized_windows = [(w, t) for w, t in self._minimized_windows if w is not window and self._win_alive(w)]
        if len(self._minimized_windows) != before:
            self._render_minimized_dock()

    @staticmethod
    def _win_alive(window) -> bool:
        try:
            return bool(window and window.winfo_exists())
        except tk.TclError:
            return False

    def _render_minimized_dock(self):
        dock = getattr(self, "_min_dock", None)
        if dock is None or not self._win_alive(dock):
            return
        self._minimized_windows = [(w, t) for w, t in getattr(self, "_minimized_windows", []) if self._win_alive(w)]
        for child in dock.winfo_children():
            child.destroy()
        if not self._minimized_windows:
            dock.pack_forget()
            return
        bg_card = COLORS["bg_card"]
        bg_item = COLORS["bg"]
        border = COLORS["border"]
        dock.configure(bg=bg_card)
        tk.Frame(dock, bg=border, height=1).pack(fill="x", padx=6, pady=(2, 6))
        tk.Label(
            dock, text=tr("已最小化 ({n})", n=len(self._minimized_windows)),
            bg=bg_card, fg=COLORS["text_dim"], font=config.font("small"), anchor="w",
        ).pack(fill="x", padx=8, pady=(0, 4))
        for win, raw_title in self._minimized_windows:
            short = raw_title if len(raw_title) <= 10 else raw_title[:9] + "…"
            row = tk.Frame(dock, bg=bg_item, highlightbackground=border, highlightthickness=1)
            row.pack(fill="x", padx=6, pady=(0, 4))
            lbl = tk.Label(
                row, text=short, bg=bg_item, fg=COLORS["text"],
                font=config.font("small"), anchor="w", cursor="hand2", padx=6, pady=4,
            )
            lbl.pack(side="left", fill="x", expand=True)
            close_btn = tk.Label(
                row, text="×", bg=bg_item, fg=COLORS["text_dim"],
                font=config.font("small", bold=True), cursor="hand2", padx=5, pady=4,
            )
            close_btn.pack(side="right")
            def _restore(_e=None, w=win):
                if hasattr(w, "restore"):
                    w.restore()
            def _close(_e=None, w=win):
                if hasattr(w, "cancel"):
                    w.cancel()
                elif self._win_alive(w):
                    w.destroy()
            for widget in (row, lbl):
                widget.bind("<Button-1>", _restore)
                widget.bind("<Enter>", lambda _e, r=row, l=lbl, c=close_btn: (
                    r.configure(highlightbackground=COLORS["accent"]),
                    l.configure(fg=COLORS["accent"]),
                    c.configure(fg=COLORS["text"]),
                ))
                widget.bind("<Leave>", lambda _e, r=row, l=lbl, c=close_btn: (
                    r.configure(highlightbackground=border),
                    l.configure(fg=COLORS["text"]),
                    c.configure(fg=COLORS["text_dim"]),
                ))
            close_btn.bind("<Button-1>", _close)
            close_btn.bind("<Enter>", lambda _e, c=close_btn: c.configure(fg=COLORS["warn"]))
            close_btn.bind("<Leave>", lambda _e, c=close_btn: c.configure(fg=COLORS["text_dim"]))
        if not dock.winfo_ismapped():
            dock.pack(side="bottom", fill="x", pady=(4, 8))

    def _nav_activate(self, key):
        self.show_page(key)
        return "break"

    def _pack_nav(self):
        for widget in self._nav_widgets.values():
            widget.pack_forget()
        for key in self.sidebar_order:
            if key in self._nav_widgets:
                self._nav_widgets[key].pack(fill="x", pady=(0, 2))

    def _is_nav_dimmed(self, key):
        order = getattr(self, "sidebar_order", DEFAULT_ORDER)
        return key in order and order.index(key) > order.index(DIVIDER)

    def _nav_color(self, key, normal, background):
        return _blend(normal, background, .45) if self._is_nav_dimmed(key) else normal

    def _nav_press(self, event, key):
        self._nav_drag = {"key": key, "y": event.y_root, "dragging": False, "before": None}
        event.widget.focus_set()
        return "break"

    def _nav_motion(self, event):
        drag = self._nav_drag
        if not drag:
            return "break"
        if abs(event.y_root - drag["y"]) >= 6:
            drag["dragging"] = True
        if not drag["dragging"]:
            return "break"
        self._nav_widgets[drag["key"]].configure(cursor="fleur", highlightthickness=1)
        items = [(key, self._nav_widgets[key]) for key in self.sidebar_order if key in self._nav_widgets and key != drag["key"]]
        before = next((key for key, w in items if event.y_root < w.winfo_rooty() + w.winfo_height()/2), None)
        drag["before"] = before
        if before:
            y = self._nav_widgets[before].winfo_y()
        elif items:
            w = items[-1][1]
            y = w.winfo_y() + w.winfo_height()
        else:
            y = 0
        self._nav_indicator.place(x=2, y=y, relwidth=1, width=-4)
        self._nav_indicator.lift()
        return "break"

    def _nav_release(self, event):
        drag, self._nav_drag = self._nav_drag, None
        if not drag:
            return "break"
        self._nav_indicator.place_forget()
        key = drag["key"]
        self._nav_widgets[key].configure(cursor="sb_v_double_arrow" if key == DIVIDER else "hand2", highlightthickness=0)
        if not drag["dragging"]:
            if key != DIVIDER:
                self.show_page(key)
            return "break"
        self.sidebar_order.remove(key)
        before = drag["before"]
        self.sidebar_order.insert(self.sidebar_order.index(before) if before else len(self.sidebar_order), key)
        prefs = read_json(self._sidebar_prefs)
        prefs["sidebar_order"] = self.sidebar_order
        write_json(self._sidebar_prefs, prefs)
        self._pack_nav()
        self._paint_sidebar()
        return "break"
