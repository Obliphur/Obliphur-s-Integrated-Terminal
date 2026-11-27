# -*- coding: utf-8 -*-
"""通用页面 WebView2 外壳。

现有页面的业务逻辑仍由 Tk 页面对象持有；这里把它的控件树投影到本地
HTML，并把网页操作安全地调度回 Tk 主线程。这样页面可以统一使用计时器 /
沟通页的 WebView2 体验，同时在没有 WebView2 时保留原来的界面。
"""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable

import config
import contract as C
from config import COLORS
from i18n import tr
from .contract_page import marks, palette as contract_palette, term_desc, term_short
from .webview2_host import LocalWebServer, WebView2Host, is_webview2_available


def _blend_hex(fg: str, bg: str, ratio: float = 0.45) -> str:
    try:
        a = fg.lstrip("#")
        b = bg.lstrip("#")
        if len(a) == 6 and len(b) == 6:
            r = int(int(a[0:2], 16) * (1 - ratio) + int(b[0:2], 16) * ratio)
            g = int(int(a[2:4], 16) * (1 - ratio) + int(b[2:4], 16) * ratio)
            bl = int(int(a[4:6], 16) * (1 - ratio) + int(b[4:6], 16) * ratio)
            return f"#{r:02x}{g:02x}{bl:02x}"
    except Exception:
        pass
    return fg


def _text(widget) -> str:
    for key in ("text", "label"):
        try:
            value = widget.cget(key)
            if value is not None:
                return str(value)
        except Exception:
            pass
    return ""


_FONT_CACHE: dict[object, tuple[int, bool]] = {}


def _font_info(widget) -> tuple[int, bool]:
    try:
        f = widget.cget("font")
        if not f:
            return (0, False)
        key = str(f)
        cached = _FONT_CACHE.get(key)
        if cached is not None:
            return cached
        actual = tkfont.Font(font=f).actual()
        res = (abs(int(actual.get("size", 0))), str(actual.get("weight", "")).lower() == "bold")
        _FONT_CACHE[key] = res
        return res
    except Exception:
        return (0, False)


def _cget_str(widget, key: str) -> str:
    try:
        v = widget.cget(key)
        return str(v) if v is not None else ""
    except Exception:
        return ""


class WebPageAdapter(tk.Frame):
    """把一个已有页面适配成 WebView2 页面。

    ``builder`` 接收一个隐藏的 Tk 容器并返回原页面对象。所有原页面方法
    和属性通过代理继续暴露给 app，避免迁移期间破坏状态机和自检。
    """

    def __init__(self, master, builder: Callable[[tk.Frame], object], title: str = ""):
        super().__init__(master, bg=COLORS["bg"])
        object.__setattr__(self, "_web_adapter_ready", False)
        self._destroyed = False
        self._visible = False
        self._title = title
        self._use_webview = is_webview2_available()
        self._native_host = tk.Frame(self, bg=COLORS["bg"])
        self._native_host.place(x=0, y=0, relwidth=1, relheight=1)
        self._delegate = builder(self._native_host)
        try:
            self._delegate.pack(fill="both", expand=True)
        except Exception:
            try:
                self._delegate.grid(row=0, column=0, sticky="nsew")
            except Exception:
                pass
        self._ui_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._ui_poll_job = None
        self._wv_show_job = None
        self._notify_job = None
        self._wv_started = False
        self._wv_host: WebView2Host | None = None
        self._wv_loading: tk.Label | None = None
        self._web_server: LocalWebServer | None = None
        self._control_map: dict[str, object] = {}
        self._last_state = None
        self.bind("<Destroy>", self._on_destroy, add="+")
        if self._use_webview:
            self._build_webview()
        object.__setattr__(self, "_web_adapter_ready", True)

    def __getattr__(self, name):
        delegate = self.__dict__.get("_delegate")
        if delegate is not None:
            return getattr(delegate, name)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name.startswith("_") or not self.__dict__.get("_web_adapter_ready"):
            object.__setattr__(self, name, value)
            return
        delegate = self.__dict__.get("_delegate")
        if delegate is not None and hasattr(delegate, name):
            setattr(delegate, name, value)
        else:
            object.__setattr__(self, name, value)

    @staticmethod
    def _colors():
        return {
            "bg": COLORS.get("bg", ""),
            "bg_card": COLORS.get("bg_card", ""),
            "border": COLORS.get("border", ""),
            "text": COLORS.get("text", ""),
            "text_dim": COLORS.get("text_dim", ""),
            "accent": COLORS.get("accent", ""),
            "accent_hover": COLORS.get("accent_hover", ""),
            "accent_fill": COLORS.get("accent_fill", COLORS.get("accent", "")),
            "on_accent_fill": COLORS.get("on_accent_fill", COLORS.get("on_accent", "#ffffff")),
            "danger": COLORS.get("danger", ""),
            "warn": COLORS.get("warn", ""),
            "success": COLORS.get("success", ""),
            "danger_bg": COLORS.get("danger_bg", ""),
        }

    def _build_webview(self):
        self._web_server = LocalWebServer(
            html_provider=lambda tok: _GENERIC_HTML.replace("__TOKEN__", json.dumps(tok)),
            get_state=lambda: self._call_on_ui(self._build_state),
            handle_action=lambda body: self._call_on_ui(lambda: self._handle_action(body)),
            thread_name="oit-page-web",
        )
        self._schedule_ui_poll()
        self._wv_host = WebView2Host(self, bg=COLORS["bg"], on_ready=self._on_webview_ready,
                                     on_error=self._fallback_to_tk)
        self._wv_host.pack(fill="both", expand=True)
        self._wv_loading = tk.Label(self, text=tr("正在加载网页界面…"), bg=COLORS["bg"],
                                    fg=COLORS["text_dim"], font=config.font("body"))
        self._wv_loading.place(relx=0.5, rely=0.5, anchor="center")

    def _start_webview_after_show(self):
        self._wv_show_job = None
        if self._destroyed or not self._visible or not self._use_webview:
            return
        if self._wv_host is None or self._web_server is None:
            return
        if not self._wv_started:
            self._wv_started = True
            self._web_server.start()
            self._wv_host.lift()
            self._wv_host.start(self._web_server.url)
        else:
            self._wv_host.lift()
            self._wv_host.sync_bounds()
        self._wv_host.set_visible(True)
        self.after(40, self._sync_webview_visible)
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

    def _sync_webview_visible(self):
        if self._destroyed or not self._use_webview or self._wv_host is None:
            return
        self._wv_host.sync_bounds()
        self._wv_host.set_visible(self._visible)
        if self._visible:
            self._wv_host.notify_parent_moved()

    def _fallback_to_tk(self, _reason=""):
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
            self._web_server.stop()
            self._web_server = None
        self._native_host.pack(fill="both", expand=True)
        try:
            self._delegate.show()
        except Exception:
            try:
                self._delegate.refresh()
            except Exception:
                pass

    def show(self):
        self._visible = True
        try:
            self._delegate.show()
        except AttributeError:
            try:
                self._delegate.refresh()
            except Exception:
                pass
        if self._use_webview:
            if self._wv_show_job:
                try:
                    self.after_cancel(self._wv_show_job)
                except tk.TclError:
                    pass
            if self._wv_started and self._wv_host is not None:
                self._wv_host.lift()
                self._wv_host.sync_bounds()
                self._wv_host.set_visible(True)
                self._notify()
            else:
                self._wv_show_job = self.after_idle(self._start_webview_after_show)
                self._notify()

    def hide(self):
        self._visible = False
        if self._wv_show_job:
            try:
                self.after_cancel(self._wv_show_job)
            except tk.TclError:
                pass
            self._wv_show_job = None
        if self._wv_host is not None:
            self._wv_host.set_visible(False)

    def refresh(self, *args, **kwargs):
        result = self._delegate.refresh(*args, **kwargs)
        self._notify()
        return result

    def _notify(self):
        if self._web_server is None or self._destroyed:
            return
        if self._notify_job is not None:
            return
        try:
            self._notify_job = self.after(50, self._flush_notify)
        except tk.TclError:
            self._notify_job = None

    def _flush_notify(self):
        self._notify_job = None
        if self._web_server is not None and not self._destroyed:
            self._web_server.notify("update")

    def _schedule_ui_poll(self):
        if self._destroyed or not self._use_webview:
            return
        self._drain_ui_queue()
        try:
            self._ui_poll_job = self.after(10, self._schedule_ui_poll)
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
        box = {}
        self._ui_queue.put((fn, done, box))
        try:
            self.after(0, self._drain_ui_queue)
        except (tk.TclError, RuntimeError):
            return {"ok": False, "error": "closed"}
        if not done.wait(5.0):
            return {"ok": False, "error": "timeout"}
        if "err" in box:
            raise box["err"]
        return box.get("val")

    @staticmethod
    def _layout_info(children):
        sides = set()
        grid_cols = set()
        grid_rows = set()
        for ch in children:
            try:
                mgr = ch.winfo_manager()
                if mgr == "pack":
                    sides.add(ch.pack_info().get("side", "top"))
                elif mgr == "grid":
                    info = ch.grid_info()
                    grid_cols.add(int(info.get("column", 0)))
                    grid_rows.add(int(info.get("row", 0)))
            except Exception:
                pass
        if grid_cols and (len(grid_cols) > 1 or len(grid_rows) > 1):
            return ("grid", max(grid_cols) + 1)
        if sides and sides.issubset({"left", "right"}):
            return ("row", 0)
        return ("col", 0)

    @staticmethod
    def _canvas_svg(canvas):
        try:
            items = canvas.find_all()
        except Exception:
            return None
        if not items:
            return None
        try:
            w = max(int(canvas.winfo_width()), int(float(canvas.cget("width") or 0)), 1)
            h = max(int(canvas.winfo_height()), int(float(canvas.cget("height") or 0)), 1)
        except Exception:
            w, h = 200, 120
        shapes = []
        for iid in items[:600]:
            try:
                t = canvas.type(iid)
                coords = [round(float(x), 1) for x in canvas.coords(iid)]
                if not coords:
                    continue
                if t == "rectangle" and len(coords) >= 4:
                    shapes.append({
                        "t": "rect", "x": min(coords[0], coords[2]), "y": min(coords[1], coords[3]),
                        "w": abs(coords[2] - coords[0]), "h": abs(coords[3] - coords[1]),
                        "fill": _cget_str(canvas, "bg") if canvas.itemcget(iid, "fill") == "" else canvas.itemcget(iid, "fill"),
                        "stroke": canvas.itemcget(iid, "outline"),
                        "no_fill": canvas.itemcget(iid, "fill") == "",
                    })
                elif t == "oval" and len(coords) >= 4:
                    shapes.append({
                        "t": "oval", "cx": (coords[0] + coords[2]) / 2, "cy": (coords[1] + coords[3]) / 2,
                        "rx": abs(coords[2] - coords[0]) / 2, "ry": abs(coords[3] - coords[1]) / 2,
                        "fill": canvas.itemcget(iid, "fill"), "stroke": canvas.itemcget(iid, "outline"),
                    })
                elif t == "line" and len(coords) >= 4:
                    shapes.append({
                        "t": "line", "pts": coords,
                        "stroke": canvas.itemcget(iid, "fill"),
                        "sw": canvas.itemcget(iid, "width"),
                    })
                elif t == "polygon" and len(coords) >= 6:
                    shapes.append({
                        "t": "poly", "pts": coords,
                        "fill": canvas.itemcget(iid, "fill"), "stroke": canvas.itemcget(iid, "outline"),
                    })
                elif t == "text" and len(coords) >= 2:
                    shapes.append({
                        "t": "text", "x": coords[0], "y": coords[1],
                        "text": canvas.itemcget(iid, "text"),
                        "fill": canvas.itemcget(iid, "fill"),
                        "anchor": canvas.itemcget(iid, "anchor"),
                    })
            except Exception:
                continue
        if not shapes:
            return None
        return {"w": w, "h": h, "bg": _cget_str(canvas, "bg"), "shapes": shapes}

    def _walk(self, widget, path="0", parent_bg=""):
        self._control_map[path] = widget
        kind = widget.winfo_class().lower()
        if kind.startswith("t"):
            kind = kind[1:]
        if kind == "scrollbar":
            return {"id": path, "kind": "skip", "text": "", "children": []}
        try:
            if kind == "label" and widget.bind("<Button-1>"):
                kind = "button"
        except Exception:
            pass

        bg = _cget_str(widget, "bg")
        fg = _cget_str(widget, "fg")
        style_name = _cget_str(widget, "style")
        size, bold = _font_info(widget)
        is_card = False
        border_col = ""
        if kind in ("frame", "labelframe"):
            try:
                ht = int(widget.cget("highlightthickness") or 0)
            except Exception:
                ht = 0
            if ht > 0:
                is_card = True
                border_col = _cget_str(widget, "highlightbackground")
            elif bg and bg == COLORS.get("bg_card") and parent_bg != bg:
                is_card = True

        node = {
            "id": path,
            "kind": kind,
            "text": _text(widget),
            "children": [],
        }
        try:
            if widget.winfo_manager() == "grid":
                gi = widget.grid_info()
                node["gr"] = int(gi.get("row", 0))
                node["gc"] = int(gi.get("column", 0))
                cs = int(gi.get("columnspan", 1))
                if cs > 1:
                    node["gcs"] = cs
        except Exception:
            pass

        if is_card:
            node["card"] = True
            if bg and bg.startswith("#"):
                node["bg"] = bg
            if border_col and border_col.startswith("#"):
                node["border"] = border_col
        if fg and fg.startswith("#"):
            node["fg"] = fg
        if bg and bg.startswith("#") and kind in ("button", "label"):
            node["bg"] = bg
        if style_name:
            if "Accent" in style_name:
                node["variant"] = "accent"
            elif "Danger" in style_name:
                node["variant"] = "danger"
        if size:
            node["size"] = size
        if bold:
            node["bold"] = True

        if kind == "text":
            try:
                node["text"] = widget.get("1.0", "end-1c")
            except Exception:
                pass
        elif kind == "progressbar":
            try:
                node["value"] = float(widget.cget("value") or 0)
                node["max"] = float(widget.cget("maximum") or 100)
            except Exception:
                node["value"], node["max"] = 0.0, 100.0
        elif kind in ("entry", "spinbox", "combobox"):
            try:
                node["value"] = widget.get()
            except Exception:
                node["value"] = ""
            if kind == "combobox":
                try:
                    node["options"] = list(widget.cget("values"))
                except Exception:
                    node["options"] = []
        elif kind == "treeview":
            try:
                node["rows"] = self._tree_rows(widget)
            except Exception:
                node["rows"] = []
        elif kind in ("checkbutton", "radiobutton"):
            try:
                var_name = widget.cget("variable")
                if var_name:
                    val = widget.tk.globalgetvar(var_name)
                    if kind == "radiobutton":
                        node["checked"] = str(val) == str(widget.cget("value"))
                    else:
                        node["checked"] = bool(int(val)) if str(val).isdigit() else bool(val)
            except Exception:
                pass

        if kind in ("button", "checkbutton", "radiobutton", "entry", "spinbox", "combobox", "scale"):
            try:
                node["disabled"] = "disabled" in widget.state()
            except Exception:
                try:
                    node["disabled"] = str(widget.cget("state")) == "disabled"
                except Exception:
                    node["disabled"] = False

        try:
            all_children = widget.winfo_children()
            if kind == "canvas":
                raw_children = list(all_children)
            else:
                raw_children = [ch for ch in all_children if ch.winfo_manager()]
        except Exception:
            raw_children = []

        if kind == "canvas" and not raw_children:
            svg = self._canvas_svg(widget)
            if svg:
                node["svg"] = svg

        if raw_children:
            layout, cols = self._layout_info(raw_children)
            if layout != "col":
                node["layout"] = layout
            if cols:
                node["cols"] = cols

        for i, child in enumerate(raw_children):
            child_node = self._walk(child, f"{path}/{i}", bg or parent_bg)
            if child_node.get("kind") == "skip":
                continue
            if child_node.get("text") or child_node.get("children") or child_node.get("svg") or child_node.get("kind") in (
                    "button", "checkbutton", "radiobutton", "entry", "spinbox", "combobox",
                    "treeview", "separator", "progressbar"):
                node["children"].append(child_node)
        return node

    def _tree_rows(self, tree):
        out = []
        for iid in tree.get_children(""):
            vals = tree.item(iid)
            out.append({"text": vals.get("text", ""), "values": list(vals.get("values", ()))})
        return out[:200]

    def _build_state(self):
        self._control_map = {}
        root = self._walk(self._delegate, "0", COLORS.get("bg", ""))
        return {"title": self._title, "colors": self._colors(), "tree": root}

    def _handle_action(self, body):
        path = str(body.get("id") or "")
        widget = self._control_map.get(path)
        if widget is None:
            return {"ok": False, "error": "unknown_control"}
        kind = widget.winfo_class().lower()
        if kind.startswith("t"):
            kind = kind[1:]
        try:
            if body.get("type") == "set" and kind in ("entry", "spinbox", "combobox"):
                value = str(body.get("value") or "")
                if kind == "combobox":
                    widget.set(value)
                    widget.event_generate("<<ComboboxSelected>>")
                else:
                    widget.delete(0, "end")
                    widget.insert(0, value)
                    widget.event_generate("<Return>")
            elif hasattr(widget, "invoke"):
                widget.invoke()
            else:
                widget.event_generate("<Button-1>")
            self.after_idle(self._notify)
            return {"ok": True, "state": self._build_state()}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "state": self._build_state()}

    def _on_destroy(self, event=None):
        if event is not None and event.widget is not self:
            return
        self._destroyed = True
        for attr in ("_ui_poll_job", "_wv_show_job", "_notify_job"):
            job = getattr(self, attr, None)
            if job:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
        if self._wv_host is not None:
            try:
                self._wv_host.close()
            except Exception:
                pass
        if self._web_server is not None:
            self._web_server.stop()


class ContractWebPageAdapter(WebPageAdapter):
    """合约专用网页：保留起源 / 都市之意两套词条板视觉和交互。"""

    def _build_webview(self):
        self._web_server = LocalWebServer(
            html_provider=lambda tok: _CONTRACT_HTML.replace("__TOKEN__", json.dumps(tok)),
            get_state=lambda: self._call_on_ui(self._build_state),
            handle_action=lambda body: self._call_on_ui(lambda: self._handle_action(body)),
            thread_name="oit-contract-web",
        )
        self._schedule_ui_poll()
        self._wv_host = WebView2Host(self, bg=COLORS["bg"], on_ready=self._on_webview_ready,
                                     on_error=self._fallback_to_tk)
        self._wv_host.pack(fill="both", expand=True)
        self._wv_loading = tk.Label(self, text=tr("正在加载合约界面…"), bg=COLORS["bg"],
                                    fg=COLORS["text_dim"], font=config.contract_font("body"))
        self._wv_loading.place(relx=0.5, rely=0.5, anchor="center")

    @staticmethod
    def _label(widget, default=""):
        try:
            return str(widget.cget("text") or default)
        except Exception:
            return default

    def _build_state(self):
        d = self._delegate
        p = d.P
        state = {
            "theme": d.theme,
            "colors": {k: p.get(k, COLORS.get(k, "")) for k in (
                "bg", "text", "dim", "line", "board", "band", "band_text", "band_dim",
                "tile", "tile_text", "tile_rim", "tile_on", "tile_on_text", "tile_on_rim",
                "tile_off", "tile_off_text", "tile_off_rim", "tile_rand", "tile_rand_text",
                "tile_rand_rim", "tile_ban", "tile_ban_text", "tile_ban_rim", "sel", "sel_text",
                "sel_dim", "bar", "start", "start_hover", "start_text", "start_off",
                "start_off_text", "clear", "clear_hover", "clear_text", "danger", "idle",
                "live", "live_rim", "hint", "nav", "nav_off", "badge_hi", "badge_hi_text",
                "badge_lo", "badge_lo_text")},
            "title": tr("⚔  合约"), "brand": p.get("brand", ""),
            "tag": tr(p.get("tag_name", "")), "page": d.page + 1, "page_count": d._page_count(),
            "combo_page": d._on_combo_page(), "tiles": [], "cols": 1, "rows": 1,
            "combos": [], "selected": [],
            "level": self._label(d.lbl_level, "0"), "info": self._label(d.lbl_info),
            "state": self._label(d.lbl_state), "left": self._label(d.lbl_left),
            "viol": self._label(d.lbl_viol), "live": self._label(d.lbl_live),
            "idle": self._label(d.lbl_idle), "last": self._label(d.lbl_last),
            "block": self._label(d.lbl_block), "rules": [self._label(x) for x in d.lbl_rules],
            "active": bool(d.c.active), "button": self._label(d.btn_end if d.c.active else d.btn_start),
            "button_disabled": bool(getattr(d, "_start_off", False)) and not d.c.active,
        }
        if d._on_combo_page():
            for combo, st in d._combos():
                state["combos"].append({
                    "id": combo["id"], "name": tr(combo["name"]), "state": st,
                    "need": " · ".join(tr(C.req_name(r)) for r in combo["reqs"]),
                    "desc": d._combo_desc(combo), "color": d._combo_color(combo),
                })
        else:
            a, b = d.pages[d.page]
            max_col = 1
            max_row = 1
            for tid, _g, lv, _n, _desc, _x in C.TERMS:
                col, row = C.LAYOUT[tid]
                if not a <= col < b:
                    continue
                c_idx = col - a
                max_col = max(max_col, c_idx + 1)
                max_row = max(max_row, row + 1)
                st = d._tile_state.get(tid, (("tile", "tile_text", "tile_rim"), ""))[0]
                state["tiles"].append({
                    "id": tid, "level": lv, "mark": marks(lv), "name": term_short(tid),
                    "desc": term_desc(tid), "col": c_idx, "row": row,
                    "fill": p.get(st[0], p["tile"]),
                    "fg": p.get(st[1], p["tile_text"]), "rim": p.get(st[2], p["tile_rim"]),
                })
            state["cols"] = max_col
            state["rows"] = max_row
        for lv, text, void, rand, tid in d._rows(d._selected()):
            state["selected"].append({
                "level": lv, "mark": marks(lv) or "—", "text": text, "void": void,
                "rand": bool(rand), "tid": tid,
            })
        return state

    def _handle_action(self, body):
        d = self._delegate
        typ = str(body.get("type") or "")
        try:
            if typ == "tile":
                d._click(str(body.get("id") or ""))
            elif typ == "flip":
                d._flip(int(body.get("step") or 0))
            elif typ == "clear":
                d._clear()
            elif typ == "start":
                d._start()
            elif typ == "end" and d.on_abandon:
                d.on_abandon()
            elif typ == "respond":
                d._respond(bool(body.get("accept")))
            elif typ == "selected" and body.get("tid"):
                if d.on_close_rand and body.get("rand"):
                    d.on_close_rand(body["tid"])
                elif d.on_lift_ban:
                    d.on_lift_ban(body["tid"])
            else:
                return {"ok": False, "error": "unknown_action"}
            self.after_idle(self._notify)
            return {"ok": True, "state": self._build_state()}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "state": self._build_state()}


class WebSidebar:
    """左侧导航的 WebView2 版本，Tk 导航保留为无运行时降级。"""

    def __init__(self, master, labels: dict[str, str], order_get: Callable[[], list[str]],
                 current_get: Callable[[], str], on_select: Callable[[str], None]):
        self.master = master
        self.labels = labels
        self.order_get = order_get
        self.current_get = current_get
        self.on_select = on_select
        self._destroyed = False
        self._visible = False
        self._host = None
        self._server = None
        self._poll = None
        self._use_webview = is_webview2_available()
        if self._use_webview:
            self._server = LocalWebServer(
                html_provider=lambda tok: _SIDEBAR_HTML.replace("__TOKEN__", json.dumps(tok)),
                get_state=lambda: self._state(),
                handle_action=lambda body: self._action(body),
                thread_name="oit-sidebar-web",
            )
            self._host = WebView2Host(master, bg=COLORS["bg_card"], on_ready=self._ready,
                                      on_error=lambda _reason: self.fallback())
            self._host.place(x=0, y=0, relwidth=1, relheight=1)
            self._poll_queue()

    @property
    def enabled(self):
        return self._use_webview

    def start(self):
        if not self.enabled or self._host is None or self._server is None:
            return
        self._server.start()
        self._host.start(self._server.url)
        self._host.set_visible(self._visible)
        self._server.notify("update")

    def show(self):
        self._visible = True
        if self._host:
            self._host.set_visible(True)
        if self._server:
            self._server.notify("update")

    def hide(self):
        self._visible = False
        if self._host:
            self._host.set_visible(False)

    def notify(self):
        if self._server:
            self._server.notify("update")

    def _state(self):
        current = self.current_get()
        look = "settings" if current == "ach" else current
        P = contract_palette()
        on_contract = current == "contract" and bool(P.get("side"))
        side_bg = P["side"] if on_contract else COLORS["bg_card"]
        text_fg = P["side_text"] if on_contract else COLORS["text"]
        dim_fg = _blend_hex(text_fg, side_bg, 0.45)
        active_bg = P["side_on"] if on_contract else COLORS.get("accent_fill", COLORS["accent"])
        active_fg = P["side_on_text"] if on_contract else COLORS.get("on_accent_fill", COLORS.get("on_accent", "#ffffff"))
        hover_bg = P["side_hover"] if on_contract else COLORS["border"]
        divider_bg = P["side_rim"] if on_contract else COLORS["text_dim"]
        nav_font = config.font("nav")
        nav_size = max(13, round(int(nav_font[1]) * 1.35)) if isinstance(nav_font, (tuple, list)) and len(nav_font) > 1 else 15

        order = []
        divider_at = None
        for x in self.order_get():
            if x == "__divider__":
                divider_at = len(order)
            elif x in self.labels:
                order.append(x)
        if divider_at is None:
            divider_at = len(order)

        return {
            "colors": {
                "side_bg": side_bg,
                "side_text": text_fg,
                "side_dim": dim_fg,
                "side_active_bg": active_bg,
                "side_active_fg": active_fg,
                "side_hover": hover_bg,
                "side_divider": divider_bg,
            },
            "font_size": nav_size,
            "divider_at": divider_at,
            "items": [{"key": k, "label": self.labels[k], "dim": i >= divider_at} for i, k in enumerate(order)],
            "current": look,
        }

    def _action(self, body):
        key = str(body.get("key") or "")
        if key not in self.labels:
            return {"ok": False, "error": "unknown_page"}
        self.on_select(key)
        return {"ok": True, "state": self._state()}

    def _ready(self):
        if self._host:
            self._host.set_visible(self._visible)

    def fallback(self):
        self._use_webview = False
        if self._host:
            try:
                self._host.close()
                self._host.destroy()
            except Exception:
                pass
            self._host = None
        if self._server:
            self._server.stop()
            self._server = None

    def _poll_queue(self):
        if self._destroyed:
            return
        try:
            self._poll = self.master.after(30, self._poll_queue)
        except tk.TclError:
            self._poll = None

    def destroy(self):
        self._destroyed = True
        if self._poll:
            try:
                self.master.after_cancel(self._poll)
            except tk.TclError:
                pass
        if self._host:
            try:
                self._host.close()
            except Exception:
                pass
        if self._server:
            self._server.stop()


_SIDEBAR_HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>
:root{--side-bg:#f8f9fa;--side-text:#1e293b;--side-dim:#64748b;--side-active-bg:#2563eb;--side-active-fg:#ffffff;--side-hover:#e2e8f0;--side-divider:#64748b;--nav-size:13px}
*{box-sizing:border-box}
html,body{margin:0;width:100%;height:100%;overflow-x:hidden;overflow-y:auto;background:var(--side-bg);color:var(--side-text);font:var(--nav-size) "Microsoft YaHei UI","Segoe UI",system-ui,sans-serif;user-select:none}
body::-webkit-scrollbar{width:5px}
body::-webkit-scrollbar-thumb{background:var(--side-hover);border-radius:3px}
.nav{min-height:100%;padding:14px 0 10px 0;display:flex;flex-direction:column;gap:2px}
.item{width:100%;border:0;border-radius:0;background:transparent;color:var(--side-text);padding:11px 14px;text-align:left;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font:inherit;line-height:1.25;transition:background-color .1s ease,color .1s ease}
.item:hover{background:var(--side-hover)}
.item.dim{color:var(--side-dim)}
.item.active,.item.dim.active,.item.active:hover{background:var(--side-active-bg);color:var(--side-active-fg);font-weight:600}
.divider{height:2px;background:var(--side-divider);margin:5px 10px}
</style></head><body><div class="nav" id="nav"></div><script>
const TOKEN=__TOKEN__;const $=id=>document.getElementById(id);
function css(c,sz){for(const[k,v]of Object.entries(c||{})){if(v)document.documentElement.style.setProperty('--'+k.replaceAll('_','-'),v)}if(sz)document.documentElement.style.setProperty('--nav-size',sz+'px')}
async function act(key){try{const r=await fetch('/api/action?token='+encodeURIComponent(TOKEN),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});const j=await r.json();if(j.state)render(j.state)}catch(e){}}
function render(s){if(!s)return;css(s.colors,s.font_size);const n=$('nav');n.replaceChildren();const items=s.items||[];items.forEach((x,i)=>{if(i===s.divider_at&&i>0){const d=document.createElement('div');d.className='divider';n.appendChild(d)}const b=document.createElement('button');b.className='item'+(x.dim?' dim':'')+(x.key===s.current?' active':'');b.textContent=x.label;b.onclick=()=>act(x.key);n.appendChild(b)});if(s.divider_at>=items.length&&items.length>0){const d=document.createElement('div');d.className='divider';n.appendChild(d)}}
async function load(){try{const r=await fetch('/api/state?token='+encodeURIComponent(TOKEN));const j=await r.json();render(j.state)}catch(e){}}
const es=new EventSource('/api/events?token='+encodeURIComponent(TOKEN));es.onmessage=load;load();
</script></body></html>'''


_GENERIC_HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OIT</title><style>
:root{--bg:#ffffff;--bg-card:#f8f9fa;--border:#e2e8f0;--text:#1e293b;--text-dim:#64748b;--accent:#2563eb;--accent-hover:#1d4ed8;--accent-fill:#2563eb;--on-accent-fill:#ffffff;--danger:#dc2626;--warn:#d97706;--success:#16a34a;--danger-bg:#fef2f2}
*{box-sizing:border-box}
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:var(--bg);color:var(--text);font:15px "Microsoft YaHei UI","Segoe UI",system-ui,sans-serif}
button,input,select{font:inherit}
button{border:1px solid var(--border);background:var(--bg-card);color:var(--text);border-radius:4px;padding:6px 12px;cursor:pointer;line-height:1.3}
button:hover{border-color:var(--accent);filter:brightness(1.06)}
button:disabled{opacity:.45;cursor:not-allowed}
button.accent{background:var(--accent-fill);color:var(--on-accent-fill);border-color:var(--accent-fill);font-weight:600}
button.danger{background:var(--danger);color:#ffffff;border-color:var(--danger);font-weight:600}
input,select{background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:5px 9px;outline:0}
input:focus,select:focus{border-color:var(--accent)}
.app{height:100%;display:flex;flex-direction:column;padding:18px 26px 14px}
.body{flex:1;min-height:0;overflow:auto;padding:2px 2px 16px}
.col{display:flex;flex-direction:column;gap:7px}
.row{display:flex;flex-direction:row;align-items:center;gap:8px;flex-wrap:wrap}
.grid{display:grid;gap:6px;align-items:stretch}
.card{border:1px solid var(--border);background:var(--bg-card);border-radius:6px;padding:12px 14px;margin:4px 0}
.sep{height:1px;background:var(--border);margin:6px 0;width:100%}
.label{white-space:pre-wrap;line-height:1.45}
.control{display:inline-flex;align-items:center;gap:6px}
.check{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--border);background:var(--bg);color:var(--text);padding:5px 10px;border-radius:4px;cursor:pointer}
.check.on{border-color:var(--accent);background:var(--accent-fill);color:var(--on-accent-fill)}
.pbar{width:100%;height:10px;background:var(--border);border-radius:5px;overflow:hidden;margin:6px 0}
.pbar-fill{height:100%;background:var(--accent-fill);transition:width .2s ease}
.svg-wrap{display:block;max-width:100%}
.rows{display:flex;flex-direction:column;border:1px solid var(--border);border-radius:4px;overflow:hidden}
.trow{padding:6px 10px;border-bottom:1px solid var(--border);background:var(--bg)}
.trow:last-child{border-bottom:0}
.body::-webkit-scrollbar{width:8px}
.body::-webkit-scrollbar-thumb{background:var(--border);border-radius:4px}
</style></head><body><div class="app"><div id="body" class="body"></div></div><script>
const TOKEN=__TOKEN__;const $=id=>document.getElementById(id);const SVG_NS='http://www.w3.org/2000/svg';
function colors(c){if(!c)return;for(const [k,v] of Object.entries(c)){if(v)document.documentElement.style.setProperty('--'+k.replaceAll('_','-'),v)}}
async function action(id,type='invoke',value=''){try{const r=await fetch('/api/action?token='+encodeURIComponent(TOKEN),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,type,value})});const j=await r.json();if(j.state)render(j.state)}catch(e){}}
function renderSvg(svg){
  const el=document.createElementNS(SVG_NS,'svg');
  el.setAttribute('viewBox','0 0 '+svg.w+' '+svg.h);
  el.style.width=svg.w<=32?svg.w+'px':'100%';
  el.style.maxWidth=svg.w+'px';
  el.style.height=svg.h+'px';
  el.classList.add('svg-wrap');
  if(svg.bg&&svg.bg.startsWith('#'))el.style.background=svg.bg;
  (svg.shapes||[]).forEach(sh=>{
    if(sh.t==='rect'){
      const r=document.createElementNS(SVG_NS,'rect');
      r.setAttribute('x',sh.x);r.setAttribute('y',sh.y);r.setAttribute('width',sh.w);r.setAttribute('height',sh.h);
      r.setAttribute('fill',sh.no_fill?'none':(sh.fill||'none'));
      if(sh.stroke)r.setAttribute('stroke',sh.stroke);
      el.appendChild(r);
    }else if(sh.t==='oval'){
      const o=document.createElementNS(SVG_NS,'ellipse');
      o.setAttribute('cx',sh.cx);o.setAttribute('cy',sh.cy);o.setAttribute('rx',sh.rx);o.setAttribute('ry',sh.ry);
      o.setAttribute('fill',sh.fill||'none');if(sh.stroke)o.setAttribute('stroke',sh.stroke);
      el.appendChild(o);
    }else if(sh.t==='line'){
      const p=document.createElementNS(SVG_NS,'polyline');
      const pts=[];for(let i=0;i+1<sh.pts.length;i+=2)pts.push(sh.pts[i]+','+sh.pts[i+1]);
      p.setAttribute('points',pts.join(' '));p.setAttribute('fill','none');
      p.setAttribute('stroke',sh.stroke||'currentColor');if(sh.sw)p.setAttribute('stroke-width',sh.sw);
      el.appendChild(p);
    }else if(sh.t==='poly'){
      const p=document.createElementNS(SVG_NS,'polygon');
      const pts=[];for(let i=0;i+1<sh.pts.length;i+=2)pts.push(sh.pts[i]+','+sh.pts[i+1]);
      p.setAttribute('points',pts.join(' '));p.setAttribute('fill',sh.fill||'none');
      if(sh.stroke)p.setAttribute('stroke',sh.stroke);
      el.appendChild(p);
    }else if(sh.t==='text'){
      const t=document.createElementNS(SVG_NS,'text');
      t.setAttribute('x',sh.x);t.setAttribute('y',sh.y);t.textContent=sh.text||'';
      t.setAttribute('fill',sh.fill||'currentColor');t.setAttribute('font-size','11');
      const a=sh.anchor||'center';
      t.setAttribute('text-anchor',a.includes('w')?'start':(a.includes('e')?'end':'middle'));
      t.setAttribute('dominant-baseline',a.includes('n')?'hanging':(a.includes('s')?'auto':'central'));
      el.appendChild(t);
    }
  });
  return el;
}
function control(n){
  const k=n.kind||'';
  if(k==='separator'){const d=document.createElement('div');d.className='sep';return d}
  if(k==='progressbar'){
    const p=document.createElement('div');p.className='pbar';
    const f=document.createElement('div');f.className='pbar-fill';
    const pct=n.max>0?Math.max(0,Math.min(100,(n.value/n.max)*100)):0;
    f.style.width=pct+'%';p.appendChild(f);return p;
  }
  if(k==='checkbutton'||k==='radiobutton'){
    const b=document.createElement('button');b.className='check'+(n.checked?' on':'');
    b.textContent=(n.checked?'✓ ':'○ ')+(n.text||'');b.disabled=!!n.disabled;
    b.onclick=()=>action(n.id);return b;
  }
  if(k.includes('button')){
    const b=document.createElement('button');b.className='control'+(n.variant?' '+n.variant:'');
    b.textContent=n.text||'操作';b.disabled=!!n.disabled;
    if(!n.variant&&n.bg)b.style.background=n.bg;if(!n.variant&&n.fg)b.style.color=n.fg;if(n.bold)b.style.fontWeight='700';
    b.onclick=()=>action(n.id);return b;
  }
  if(k==='entry'||k==='spinbox'){
    const i=document.createElement('input');i.dataset.id=n.id;i.value=n.value||'';i.disabled=!!n.disabled;
    i.onchange=()=>action(n.id,'set',i.value);i.onkeydown=e=>{if(e.key==='Enter')action(n.id,'set',i.value)};return i;
  }
  if(k==='combobox'){
    const s=document.createElement('select');(n.options||[]).forEach(x=>{const o=document.createElement('option');o.textContent=x;o.value=x;s.appendChild(o)});
    s.value=n.value||'';s.onchange=()=>action(n.id,'set',s.value);return s;
  }
  return null;
}
function node(n,inGrid=false){
  const wrap=document.createElement('div');
  const cls=[];
  if(n.card)cls.push('card');
  cls.push(n.layout==='grid'?'grid':(n.layout==='row'?'row':'col'));
  wrap.className=cls.join(' ');
  if(n.layout==='grid'&&n.cols)wrap.style.gridTemplateColumns='repeat('+n.cols+',minmax(0,1fr))';
  if(inGrid&&typeof n.gc==='number'){
    wrap.style.gridColumn=(n.gc+1)+(n.gcs?' / span '+n.gcs:'');
    if(typeof n.gr==='number')wrap.style.gridRow=(n.gr+1);
  }
  if(n.card){if(n.bg)wrap.style.background=n.bg;if(n.border)wrap.style.borderColor=n.border}
  if(n.svg)wrap.appendChild(renderSvg(n.svg));
  const c=control(n);
  if(c){wrap.appendChild(c)}
  else if(n.text){
    const l=document.createElement('div');l.className='label';l.textContent=n.text;
    if(n.fg)l.style.color=n.fg;if(n.bg)l.style.background=n.bg;if(n.bold)l.style.fontWeight='700';
    if(n.size&&n.size>=14)l.style.fontSize=(n.size+2)+'px';
    wrap.appendChild(l);
  }
  if(n.rows&&n.rows.length){
    const rs=document.createElement('div');rs.className='rows';
    n.rows.forEach(r=>{const x=document.createElement('div');x.className='trow';x.textContent=[r.text,...(r.values||[])].filter(Boolean).join('  ');rs.appendChild(x)});
    wrap.appendChild(rs);
  }
  const childGrid=n.layout==='grid';
  (n.children||[]).forEach(ch=>wrap.appendChild(node(ch,childGrid)));
  return wrap;
}
let lastJson='';
function render(s){
  if(!s)return;
  const raw=JSON.stringify(s);
  if(raw===lastJson)return;
  lastJson=raw;
  colors(s.colors);
  const ae=document.activeElement;
  const fid=ae&&ae.dataset?ae.dataset.id:null;
  const sel=ae&&typeof ae.selectionStart==='number'?ae.selectionStart:null;
  $('body').replaceChildren(node(s.tree,false));
  if(fid){const el=document.querySelector('[data-id="'+CSS.escape(fid)+'"]');if(el){el.focus();if(sel!==null&&el.setSelectionRange)el.setSelectionRange(sel,sel)}}
}
async function load(){try{const r=await fetch('/api/state?token='+encodeURIComponent(TOKEN));const j=await r.json();render(j.state)}catch(e){}}
const es=new EventSource('/api/events?token='+encodeURIComponent(TOKEN));es.onmessage=load;load();
</script></body></html>'''


_CONTRACT_HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OIT Contract</title><style>
:root{--bg:#1f2023;--text:#f2f2f2;--dim:#9a9ca1;--line:#c8202f;--board:#d6d7da;--band:#2c2d31;--band-text:#ececec;--band-dim:#9a9ca1;--tile:#45474d;--tile-text:#f4f4f4;--tile-rim:#6b6d73;--tile-on:#1d6fc4;--tile-on-text:#fff;--tile-on-rim:#9cc8ff;--tile-off:#bdbec1;--tile-off-text:#8e9095;--tile-off-rim:#a9aaad;--tile-rand:#f2b33d;--tile-rand-text:#141416;--tile-rand-rim:#ffd27a;--tile-ban:#7c3aed;--tile-ban-text:#fff;--tile-ban-rim:#c4a1ff;--sel:#141416;--sel-text:#f2f2f2;--sel-dim:#77797e;--bar:#26272b;--start:#9e1b28;--start-hover:#bf2433;--start-text:#fff;--start-off:#4a4b51;--start-off-text:#8e9095;--clear:#3a3b40;--clear-hover:#4a4b51;--clear-text:#ececec;--danger:#ff4d5a;--idle:#f2b33d;--live:#18181b;--live-rim:#c8202f;--hint:#2c2d31;--nav:#ececec;--nav-off:#55575c}
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:var(--bg);color:var(--text);font:14.5px "Microsoft YaHei UI","Segoe UI",system-ui,sans-serif}button{font:inherit;border:1px solid var(--clear);background:var(--clear);color:var(--clear-text);padding:7px 14px;border-radius:3px;cursor:pointer}button:hover{background:var(--clear-hover)}button:disabled{opacity:.45;cursor:not-allowed}.app{height:100%;display:flex;flex-direction:column;padding:16px 22px 12px;gap:8px}.head{display:flex;align-items:center;gap:10px;min-height:31px}.tag{background:var(--line);color:#fff;padding:4px 8px;font-weight:700;font-size:12px}.title{font-size:22px;font-weight:700}.brand{color:var(--dim);font-weight:700;font-size:12px;letter-spacing:1px}.head-state{margin-left:auto;color:var(--danger);font-weight:700}.rules{margin-left:auto;border:1px solid var(--line);background:var(--live);padding:8px 12px;white-space:pre-wrap;color:var(--dim);font-size:12px;max-width:440px}.rule-title{color:var(--danger);font-weight:700}.line{height:2px;background:var(--line)}.live{border:2px solid var(--live-rim);background:var(--live);padding:8px 14px;display:flex;flex-wrap:wrap;gap:4px 18px;align-items:center}.left{font-size:24px;font-weight:700}.viol{margin-left:auto;color:var(--danger);font-size:17px;font-weight:700}.live-sub{width:100%;color:var(--dim);white-space:pre-wrap}.idle{color:var(--idle);font-weight:700}.board-wrap{flex:1;min-height:220px;overflow:hidden;background:var(--board);color:var(--band-text);display:flex;flex-direction:column}.board{display:flex;flex:1;min-height:0;overflow:hidden}.band{width:112px;flex:0 0 112px;background:var(--band);display:flex;flex-direction:column;padding:10px 8px}.band-title{writing-mode:vertical-rl;transform:rotate(180deg);color:var(--band-dim);font-weight:700;letter-spacing:1px;margin:auto}.band-sub{writing-mode:vertical-rl;transform:rotate(180deg);color:var(--band-text);font-weight:700;margin:auto}.nav{display:flex;justify-content:space-between;align-items:center;color:var(--nav);padding-top:8px}.nav button{background:transparent;border:0;color:var(--nav);font-size:20px;padding:0 4px}.tiles{flex:1;min-width:0;overflow:auto;display:grid;gap:6px;padding:14px 16px;align-content:start;justify-content:start}.tile{position:relative;display:flex;align-items:center;justify-content:center;text-align:center;white-space:pre-wrap;cursor:pointer;clip-path:polygon(22% 2%,78% 2%,98% 50%,78% 98%,22% 98%,2% 50%);border:0;background:var(--t-rim,var(--tile-rim));padding:2px;height:62px;font-size:12.5px;font-weight:700;line-height:1.2}.tile-in{width:100%;height:100%;clip-path:inherit;background:var(--t-bg,var(--tile));color:var(--t-fg,var(--tile-text));display:flex;align-items:center;justify-content:center;padding:4px 6px}.tile:hover{filter:brightness(1.12)}.combo-grid{flex:1;min-width:0;overflow:auto;display:grid;grid-template-columns:repeat(2,minmax(180px,1fr));gap:9px;padding:12px}.combo{background:var(--sel);border:1px solid var(--tile-rim);padding:12px;min-height:105px}.combo.done{border-color:var(--accent,#a5f3ff)}.combo-name{font-weight:700;font-size:16px}.combo-need{color:var(--sel-dim);margin-top:8px;font-size:13.5px}.combo-desc{color:var(--sel-text);margin-top:7px;white-space:pre-wrap;font-size:13.5px}.selected{width:100%;background:var(--sel);color:var(--sel-text);padding:10px 14px;max-height:175px;overflow-y:auto;overflow-x:hidden;flex-shrink:0}.selected-title{color:var(--sel-dim);font-weight:700;margin-bottom:6px;font-size:13px}.selected-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px 16px}.selected-row{padding:4px 6px;white-space:pre-wrap;cursor:pointer;font-size:13.5px;line-height:1.4}.selected-row.void{color:var(--sel-dim)}.selected-row.rand{color:var(--tile-rand)}.bottom{background:var(--bar);display:flex;align-items:center;gap:18px;padding:9px 14px}.level-label{color:var(--dim);font-size:12px}.level{font-size:22px;font-weight:700}.info{white-space:pre-wrap;color:var(--text);font-size:13.5px}.bottom-actions{margin-left:auto;display:flex;align-items:flex-end;gap:6px;flex-direction:column}.block{color:var(--dim);font-size:12px;white-space:pre-wrap;text-align:right}.start{background:var(--start);color:var(--start-text);font-size:16px;font-weight:700;padding:9px 22px;border:0}.start:hover{background:var(--start-hover)}.start.off{background:var(--start-off);color:var(--start-off-text);cursor:default}.last{color:var(--dim);font-size:12px;padding:2px 4px}.hidden{display:none}@media(max-width:760px){.rules{display:none}.band{width:78px;flex-basis:78px}.selected-grid{grid-template-columns:1fr}.bottom{gap:8px;padding:7px}.info{display:none}}
</style></head><body><div class="app"><div class="head"><div id="tag" class="tag"></div><div class="title" id="title"></div><div class="brand" id="brand"></div><div class="head-state" id="state"></div><div class="rules"><span class="rule-title">合约期间</span><br><span id="rules"></span></div></div><div class="line"></div><div id="live" class="live hidden"><div id="left" class="left"></div><div id="viol" class="viol"></div><div id="liveSub" class="live-sub"></div><div id="idle" class="idle"></div></div><div class="board-wrap"><div class="board"><div class="band"><div id="bandTitle" class="band-title">CONTRACT LIST</div><div id="bandSub" class="band-sub">可选词条</div><div class="nav"><button id="prev">◀</button><span id="page"></span><button id="next">▶</button></div></div><div id="tiles" class="tiles"></div><div id="combos" class="combo-grid hidden"></div></div><div id="selected" class="selected"></div></div><div id="last" class="last"></div><div class="bottom"><button id="clear">🗑 清空</button><div><div class="level-label">危机等级</div><div id="level" class="level">0</div></div><div id="info" class="info"></div><div class="bottom-actions"><div id="block" class="block"></div><button id="start" class="start"></button></div></div></div><script>
const TOKEN=__TOKEN__;const $=id=>document.getElementById(id);function css(c){for(const[k,v]of Object.entries(c||{})){if(v)document.documentElement.style.setProperty('--'+k.replaceAll('_','-'),v)}}async function act(type,extra={}){try{const r=await fetch('/api/action?token='+encodeURIComponent(TOKEN),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type,...extra})});const j=await r.json();if(j.state)render(j.state)}catch(e){}}function render(s){if(!s)return;css(s.colors);$('tag').textContent=s.tag||'';$('title').textContent=s.title||'';$('brand').textContent=s.brand||'';$('state').textContent=s.state||'';$('rules').textContent=(s.rules||[]).join('\n');$('page').textContent=s.page+' / '+s.page_count;$('prev').disabled=s.page<=1;$('next').disabled=s.page>=s.page_count;$('live').classList.toggle('hidden',!s.active);$('left').textContent=s.left||'';$('viol').textContent=s.viol||'';$('liveSub').textContent=s.live||'';$('idle').textContent=s.idle||'';$('bandTitle').textContent=s.combo_page?'CONTRACT EFFECT':'CONTRACT LIST';$('bandSub').textContent=s.combo_page?'词条组合':'可选词条';$('tiles').classList.toggle('hidden',s.combo_page);$('combos').classList.toggle('hidden',!s.combo_page);$('clear').classList.toggle('hidden',s.active);$('level').textContent=s.level||'0';$('info').textContent=s.info||'';$('block').textContent=s.block||'';$('last').textContent=s.last||'';const b=$('start');b.textContent=s.button||'';b.classList.toggle('off',!!s.button_disabled);b.disabled=!!s.button_disabled&&!s.active;const tg=$('tiles');tg.replaceChildren();tg.style.gridTemplateColumns='repeat('+(s.cols||1)+',minmax(64px,82px))';(s.tiles||[]).forEach(t=>{const b=document.createElement('button');b.className='tile';b.style.setProperty('--t-bg',t.fill);b.style.setProperty('--t-fg',t.fg);b.style.setProperty('--t-rim',t.rim);b.style.gridColumn=(t.col+1);b.style.gridRow=(t.row+1);b.title=t.desc;const inner=document.createElement('span');inner.className='tile-in';inner.textContent=t.mark+'\n'+t.name;b.appendChild(inner);b.onclick=()=>act('tile',{id:t.id});tg.appendChild(b)});$('combos').replaceChildren();(s.combos||[]).forEach(c=>{const x=document.createElement('div');x.className='combo '+(c.state==='done'?'done':'');x.style.borderColor=c.color;const badge=c.state==='done'?' <small>已达成</small>':(c.state==='locked'?' <small>未解锁</small>':'');x.innerHTML='<div class="combo-name" style="color:'+c.color+'">'+c.name+badge+'</div><div class="combo-need">需要 '+c.need+'</div><div class="combo-desc">'+c.desc+'</div>';$('combos').appendChild(x)});const sel=$('selected');sel.innerHTML='<div class="selected-title">CONTRACT SELECTED · 已选中词条</div>';const sg=document.createElement('div');sg.className='selected-grid';(s.selected||[]).forEach(x=>{const r=document.createElement('div');r.className='selected-row'+(x.void?' void':'')+(x.rand?' rand':'');r.textContent=x.mark+'  '+x.text;r.onclick=()=>x.tid&&act('selected',{tid:x.tid,rand:x.rand});sg.appendChild(r)});sel.appendChild(sg)}async function load(){try{const r=await fetch('/api/state?token='+encodeURIComponent(TOKEN));const j=await r.json();render(j.state)}catch(e){}}$('prev').onclick=()=>act('flip',{step:-1});$('next').onclick=()=>act('flip',{step:1});$('clear').onclick=()=>act('clear');$('start').onclick=()=>act('start');const es=new EventSource('/api/events?token='+encodeURIComponent(TOKEN));es.onmessage=load;load();
</script></body></html>'''
