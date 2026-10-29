

import os
import tkinter as tk
from datetime import datetime
from tkinter import ttk

import config
import toast
from config import COLORS
from i18n import tr
from netproxy import (
    WHITE_DOMAINS, WHITE_APPS, BLACK_DOMAINS, BLACK_APPS,
)
from netproxy.rules import (MODE_BLACKLIST, MODE_WHITELIST, WHITE_UPS, BLACK_UPS, keys_for_mode,
                            up_key_for, up_key_for_mode, up_site)
from .widgets import (Modal, ScrollArea, wrap_label, smooth, cfg, flavor,
                      show_pack, hide_pack, hide_grid)
from . import ai_dialogs, up_dialog

_WHY_TEXT = {
    "offline": "离线",
    "blacklist-app": "应用黑名单",
    "blacklist-domain": "网站黑名单",
    "blacklist-up": "UP 主黑名单",
    "not-whitelisted-up": "不在 UP 主白名单",
    "self": "回环",
    "not-whitelisted": "不在白名单",
    "blockall": "全屏蔽",
    "ban": "合约禁止联网",
}

_PANELS = (
    (WHITE_DOMAINS, "✅  白名单 · 网站"),
    (WHITE_APPS, "✅  白名单 · 应用"),
    (BLACK_DOMAINS, "⛔  黑名单 · 网站"),
    (BLACK_APPS, "⛔  黑名单 · 应用"),
)


class RulesPage(tk.Frame):
    """🛡 Rules 页:proxy 屏蔽模式的名单.黑 / 白名单二选一,只显示当前模式的两张表.
    代理-全屏蔽 (Proxy block-all) 下网站一律拦截, 只显示当前模式的应用名单."""

    def __init__(self, master, controller, settings=None, on_changed=None,
                 on_open_settings=None, sync=None):
        super().__init__(master, bg=COLORS["bg"])
        self.sync = sync
        self._found_ids = []
        self._peer_ids = []
        self.ctrl = controller
        self.rules = controller.rules
        self.settings = settings
        self.on_changed = on_changed
        self.on_open_settings = on_open_settings
        self._lists = {}
        self._frames = {}
        self._pending_idx = []
        self._entries = {}
        self._shown = {}
        self._rows = {}               # 名单 -> 列表里显示的文字 (网站条目后面带 UP 主的标记)
        self._press = None            # 按下鼠标时的 (名单, 序号, 这一项是不是已经单独选中着)
        self._rules_version = -1
        self._ai_cfg = None
        self._build()


    def _build(self):
        bg, card = COLORS["bg"], COLORS["bg_card"]

        hdr = tk.Frame(self, bg=bg)
        hdr.pack(fill="x", padx=28, pady=(18, 6))
        tk.Label(hdr, text=tr("🛡  规则"), bg=bg, fg=COLORS["text"],
                 font=config.font("h1", bold=True)).pack(anchor="w")

        self.area = ScrollArea(self, height=420, bg=bg)
        self.area.pack(fill="both", expand=True, padx=26, pady=(6, 4))
        body = self.area.inner

        st = tk.Frame(body, bg=card, highlightbackground=COLORS["border"], highlightthickness=1)
        st.pack(fill="x", pady=(0, 8))
        stin = tk.Frame(st, bg=card)
        stin.pack(fill="x", padx=14, pady=10)
        self.lbl_mode = tk.Label(stin, text="", bg=card, fg=COLORS["text"],
                                 font=config.font("body", bold=True), anchor="w",
                                 justify="left")
        self.lbl_mode.pack(fill="x")
        self.lbl_stats = tk.Label(stin, text="", bg=card, fg=COLORS["text_dim"],
                                  font=config.font("small"), anchor="w")
        self.lbl_stats.pack(fill="x", pady=(2, 0))
        wrap_label(self.lbl_mode, 30)
        self.btn_settings = ttk.Button(
            stin, text=tr("去设置里切换断网方式"),
            command=lambda: self.on_open_settings and self.on_open_settings())

        mrow = tk.Frame(stin, bg=card)
        mrow.pack(fill="x", pady=(8, 0))
        self.btn_modes = {}
        for mode, label in ((MODE_BLACKLIST, "⛔  黑名单模式"), (MODE_WHITELIST, "✅  白名单模式")):
            b = ttk.Button(mrow, text=tr(label), command=lambda m=mode: self._set_mode(m))
            b.pack(side="left", padx=(0, 8))
            self.btn_modes[mode] = b
        ttk.Button(mrow, text=tr("⚙ 模型"), command=self._ai_settings).pack(side="right")
        ttk.Button(mrow, text=tr("🤖 AI 建议"), command=self._ai_suggest).pack(
            side="right", padx=(0, 8))
        self.lbl_pending_mode = tk.Label(stin, text="", bg=card, fg=COLORS["warn"],
                                         font=config.font("small"), anchor="w",
                                         justify="left")
        self.lbl_pending_mode.pack(fill="x", pady=(4, 0))
        wrap_label(self.lbl_pending_mode, 30)

        # 作者的话: 为什么默认放行 (MECHANICS.md 4.1)
        flavor(body, tr(
            "你们贴心的作者已经考虑到了戒断反应和设置参数的资料查询需要,所以默认是放行的.\n在完成初始设置后,各项设置的修改限制才会生效.\n作者比较喜欢用proxy block-all.\n对了,应用黑白名单只针对空闲与专注时段,休息时段全部放行,但是联网需要另外申请."), bg=bg).pack(fill="x", pady=(0, 10))

        grid = tk.Frame(body, bg=bg)
        grid.pack(fill="x")
        grid.grid_columnconfigure(0, weight=1, uniform="col")
        grid.grid_columnconfigure(1, weight=1, uniform="col")
        for key, title in _PANELS:
            self._make_panel(grid, key, title, row=0,
                             col=0 if key in (WHITE_DOMAINS, BLACK_DOMAINS) else 1)

        tk.Label(body, text=tr("⏳  等待生效的改动"), bg=bg, fg=COLORS["text"],
                 font=config.font("body", bold=True)).pack(anchor="w", pady=(10, 2))
        pf = tk.Frame(body, bg=bg)
        pf.pack(fill="x", pady=(2, 0))
        self.lb_pending = tk.Listbox(pf, height=4, activestyle="none",
                                     font=config.font("small"), exportselection=False,
                                     highlightbackground=COLORS["border"])
        self.lb_pending.pack(side="left", fill="x", expand=True)
        ttk.Button(pf, text=tr("✕ 取消选中"), command=self._cancel_pending).pack(
            side="left", padx=(8, 0))

        tk.Frame(body, bg=bg, height=14).pack(fill="x")
        self.area.bind_children()

    def _make_panel(self, parent, key, title, row, col):
        card = COLORS["bg_card"]
        fr = tk.Frame(parent, bg=card, highlightbackground=COLORS["border"],
                      highlightthickness=1)
        fr.grid(row=row, column=col, sticky="nsew", padx=(0, 6) if col == 0 else (6, 0),
                pady=(0, 8))
        self._frames[key] = fr
        tk.Label(fr, text=tr(title), bg=card, fg=COLORS["text"],
                 font=config.font("body", bold=True), anchor="w").pack(
            fill="x", padx=10, pady=(8, 0))

        lf = tk.Frame(fr, bg=card)
        lf.pack(fill="both", expand=True, padx=10, pady=(6, 4))
        sb = ttk.Scrollbar(lf, orient="vertical")
        lb = tk.Listbox(lf, height=6, activestyle="none", exportselection=False,
                        font=config.font("small"), yscrollcommand=sb.set,
                        highlightbackground=COLORS["border"], selectmode="extended")
        sb.config(command=lb.yview)
        sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)
        self._lists[key] = lb
        if key in (WHITE_DOMAINS, BLACK_DOMAINS):
            lb.bind("<Button-1>", lambda e, k=key: self._press_item(k, e), add="+")
            lb.bind("<ButtonRelease-1>", lambda e, k=key: self._release_item(k, e), add="+")

        er = tk.Frame(fr, bg=card)
        er.pack(fill="x", padx=10, pady=(0, 4))
        var = tk.StringVar()
        ent = ttk.Entry(er, textvariable=var)
        ent.pack(side="left", fill="x", expand=True)
        ent.bind("<Return>", lambda _e, k=key: self._add(k))
        self._entries[key] = var
        ttk.Button(er, text=tr("添加"), style="Accent.TButton",
                   command=lambda k=key: self._add(k)).pack(side="left", padx=(6, 0))

        br = tk.Frame(fr, bg=card)
        br.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(br, text=tr("删除选中"),
                   command=lambda k=key: self._remove(k)).pack(side="left")
        ttk.Button(br, text=tr("从本机选择…"),
                   command=lambda k=key: self._pick(k)).pack(side="left", padx=(6, 0))
        if key in (WHITE_DOMAINS, BLACK_DOMAINS):
            ttk.Button(br, text=tr("UP 主…"),
                       command=lambda k=key: self._open_ups_selected(k)).pack(side="left", padx=(6, 0))


    def _toast_status(self, status: str, key: str = None, item: str = None):
        if status == "applied":
            toast.success(tr("已生效."))
        elif status == "staged":
            p = self.rules.pending_for(key, item) if key and item else None
            p = p or (self.rules.pending[-1] if self.rules.pending else None)
            at = f"{p['apply_at']:%Y-%m-%d %H:%M}" if p else ""
            toast.info(tr("将于 {at} 生效.").format(at=at))
        elif status == "cancelled":
            toast.info(tr("已取消排队中的改动,名单保持原样."))
        elif status == "exists":
            toast.info(tr("已经在名单里了."))
        elif status == "missing":
            toast.info(tr("名单里没有这一项."))
        else:
            toast.warn(tr("格式不对,请检查后重试."))

    def _add(self, key):
        text = self._entries[key].get().strip()
        if not text:
            return
        status, item = self.rules.add(key, text)
        if status in ("applied", "staged", "cancelled"):
            self._entries[key].set("")
        self._toast_status(status, key, item)
        self.refresh(force=True)

    def _remove(self, key):
        lb = self._lists[key]
        picks = [self._shown[key][i] for i in lb.curselection()
                 if i < len(self._shown.get(key, ()))]
        if not picks:
            toast.info(tr("先在列表里选中要删除的项."))
            return
        statuses = [self.rules.remove(key, item)[0] for item in picks]
        for st in ("staged", "applied", "cancelled"):
            if st in statuses:
                self._toast_status(st, key, picks[-1] if len(picks) == 1 else None)
                break
        else:
            self._toast_status(statuses[-1])
        self.refresh(force=True)

    # ---- 网站条目下面的 UP 主名单 (MECHANICS 4.8) ----

    def _press_item(self, key, event):
        """再点一下已经选中的网站条目 (bilibili.com …) 就打开它的 UP 主名单; 这里只记下按下时的状态."""
        lb = self._lists[key]
        idx = lb.nearest(event.y)
        box = lb.bbox(idx) if idx >= 0 else None
        hit = bool(box) and box[1] <= event.y < box[1] + box[3]
        self._press = (key, idx, hit and tuple(lb.curselection()) == (idx,)
                       and not event.state & 0x5) if hit else None      # 0x5 = Shift / Ctrl

    def _release_item(self, key, event):
        press, self._press = self._press, None
        if not press or press[0] != key or not press[2]:
            return
        lb = self._lists[key]
        idx = lb.nearest(event.y)
        box = lb.bbox(idx)
        if idx != press[1] or not box or not (box[1] <= event.y < box[1] + box[3]):
            return
        items = self._shown.get(key, ())
        if idx < len(items) and up_site(items[idx]):
            self._open_ups(key, items[idx])

    def _open_ups_selected(self, key):
        picks = [self._shown[key][i] for i in self._lists[key].curselection()
                 if i < len(self._shown.get(key, ()))]
        picks = [p for p in picks if up_site(p)]
        if not picks:
            toast.info(tr("先在列表里选中 bilibili.com."))
            return
        self._open_ups(key, picks[0])

    def _open_ups(self, key, item):
        up_dialog.open_ups(self.winfo_toplevel(), self.rules, self.ctrl.up, key, item,
                           self._toast_status, on_changed=lambda: self.refresh(force=True))

    def _row_text(self, key, item) -> str:
        site = up_site(item)
        if not site:
            return item
        n = len(self.rules.up_items(up_key_for(key), site))
        return f"{item}    ▸ UP" + (f" {n}" if n else "")

    def _cancel_pending(self):
        sel = self.lb_pending.curselection()
        if not sel or sel[0] >= len(self._pending_idx):
            return
        if self.rules.cancel_pending(self._pending_idx[sel[0]]):
            self.refresh(force=True)

    def _mode(self) -> str:
        return MODE_WHITELIST if getattr(config, "FILTER_MODE", MODE_BLACKLIST) == MODE_WHITELIST \
            else MODE_BLACKLIST

    @smooth
    def _set_mode(self, mode: str):
        if self.settings is None:
            return
        status, msg = self.settings.stage("FILTER_MODE", mode)
        if status in ("instant", "staged", "nochange"):
            toast.info(tr(msg))
        if self.on_changed and status in ("instant", "staged"):
            self.on_changed(status, "FILTER_MODE")
        self.refresh(force=True)

    def _ai(self):
        if self._ai_cfg is None:
            import ai_assist
            self._ai_cfg = ai_assist.AIConfig(
                os.path.join(os.path.dirname(self.rules.path), "ai_config.json"),
                template=ai_assist.DEFAULT_TEMPLATE)
        return self._ai_cfg

    def _ai_settings(self):
        ai_dialogs.open_model_settings(self.winfo_toplevel(), self._ai())

    def _ai_suggest(self):
        ai_dialogs.open_suggest(self.winfo_toplevel(), self._ai(), self.rules, self._mode(),
                                on_done=lambda: self.refresh(force=True))

    def _pick(self, key):
        """从本机已装应用 / 浏览器常访问域名里多选;采集逻辑见 context_collect.py."""
        import context_collect
        is_app = key in (WHITE_APPS, BLACK_APPS)
        if is_app:
            try:
                apps = context_collect.collect_apps()
            except Exception:
                apps = []
            options = [(a["exe"], f"{a['name']}  ({a['exe']})") for a in apps]
        else:
            if not toast.ask(
                tr("只读取浏览器历史里的域名和访问次数,不保存,不上传."),
                title=tr("读取浏览器记录")
            ):
                return
            try:
                domains, _src = context_collect.collect_browser_domains(days=90)
            except Exception:
                domains = []
            options = [(d["domain"], f"{d['domain']}  —  {d['visits']}") for d in domains]
        if not options:
            toast.info(tr("没有读到可选项."))
            return

        dlg = Modal(self.winfo_toplevel(), tr("从本机选择"), width=480, height=460)
        lf = tk.Frame(dlg.body, bg=COLORS["bg"])
        lf.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(lf, orient="vertical")
        lb = tk.Listbox(lf, selectmode="extended", activestyle="none", exportselection=False,
                        font=config.font("small"), yscrollcommand=sb.set)
        sb.config(command=lb.yview)
        sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)
        for _v, label in options:
            lb.insert("end", label)
        ttk.Button(dlg.buttons, text=tr("添加选中"), style="Accent.TButton",
                   command=lambda: dlg.finish(list(lb.curselection()))).pack(side="right")
        ttk.Button(dlg.buttons, text=tr("取消"), command=dlg.cancel).pack(
            side="right", padx=(0, 8))
        picked = dlg.wait()
        if not picked:
            return
        statuses = [self.rules.add(key, options[i][0])[0] for i in picked]
        self._toast_status("staged" if "staged" in statuses else
                           ("applied" if "applied" in statuses else statuses[0]))
        self.refresh(force=True)


    def refresh(self, force: bool = False):
        r = self.rules
        r.promote_due()
        c = self.ctrl

        net_mode = getattr(config, "NET_MODE", "adapter")
        proxy_on = net_mode in ("proxy", "proxy_all")
        mode = self._mode()
        blockall = net_mode == "proxy_all"
        # 全屏蔽: 网站名单不起作用, 只剩当前模式的应用名单 (MECHANICS 3.2.1)
        active = keys_for_mode(mode)[1:] if blockall else keys_for_mode(mode)

        for key, fr in self._frames.items():
            if key not in active:
                hide_grid(fr)
            elif blockall:
                self._place(fr, col=0, span=2)
            else:
                self._place(fr, col=0 if key in (WHITE_DOMAINS, BLACK_DOMAINS) else 1, span=1)
        for m, b in self.btn_modes.items():
            cfg(b, style="Accent.TButton" if m == mode else "TButton")
        staged = self.settings.staged("FILTER_MODE") if self.settings else None
        if staged:
            target = tr("白名单模式") if staged["value"] == MODE_WHITELIST else tr("黑名单模式")
            cfg(self.lbl_pending_mode,
                text=tr("待生效: 切换到{mode}   ·   {when}").format(
                    mode=target, when=self.settings.apply_at_text("FILTER_MODE")))
        else:
            cfg(self.lbl_pending_mode, text="")

        if proxy_on:
            hide_pack(self.btn_settings)
            run = tr("运行中") if c.running else tr("未运行")
            text = tr("Proxy 模式 · 本地代理 127.0.0.1:{port} · {run}").format(port=c.port, run=run)
            if net_mode == "proxy_all":
                text += "\n" + tr("代理-全屏蔽 (Proxy block-all): 离线时除 OIT 内部需要的连接 (本机, 局域网同步) 外, 所有网站一律拦截; 只启用应用名单.")
            cfg(self.lbl_mode, text=text,
                fg=COLORS["success"] if c.running else COLORS["warn"])
        else:
            cfg(self.lbl_mode,
                text=tr("Adapter 模式 · 规则未启用"),
                fg=COLORS["warn"])
            show_pack(self.btn_settings, anchor="w", pady=(6, 0))
        cfg(self.lbl_stats,
            text=tr("已放行 {a} 次 · 已拦截 {b} 次(本次运行)").format(a=c.allowed, b=c.blocked))

        if force or self._rules_version != r.version:
            self._rules_version = r.version
            for key, lb in self._lists.items():
                items = r.items(key)
                rows = [self._row_text(key, it) for it in items]
                if self._shown.get(key) != items or self._rows.get(key) != rows:
                    sel = {self._shown[key][i] for i in lb.curselection()
                           if i < len(self._shown.get(key, ()))}
                    self._shown[key] = items
                    self._rows[key] = rows
                    lb.delete(0, "end")
                    for i, row in enumerate(rows):
                        lb.insert("end", row)
                        if items[i] in sel:
                            lb.selection_set(i)

        shown_lists = active if blockall else active + (up_key_for_mode(mode),)
        self._pending_idx = [i for i, p in enumerate(r.pending) if p["list"] in shown_lists]
        pend = [f"{'+' if r.pending[i]['op'] == 'add' else '-'} "
                f"{self._list_label(r.pending[i]['list'])}: {r.pending[i]['item']}   →  "
                f"{r.pending[i]['apply_at']:%m-%d %H:%M}" for i in self._pending_idx]
        if self._shown.get("_pending") != pend:
            self._shown["_pending"] = pend
            self.lb_pending.delete(0, "end")
            for line in pend:
                self.lb_pending.insert("end", line)

    @staticmethod
    def _place(fr, col: int, span: int):
        """名单卡片放到第 col 列 (横跨 span 列); 位置没变就不动 (重复 grid 会触发重新布局)."""
        if fr.winfo_manager() == "grid":
            info = fr.grid_info()
            if int(info.get("column", -1)) == col and int(info.get("columnspan", 1)) == span:
                return
        padx = 0 if span > 1 else ((0, 6) if col == 0 else (6, 0))
        fr.grid(row=0, column=col, columnspan=span, sticky="nsew", padx=padx, pady=(0, 8))

    @staticmethod
    def _list_label(key: str) -> str:
        return {
            WHITE_DOMAINS: tr("白名单·网站"), WHITE_APPS: tr("白名单·应用"),
            BLACK_DOMAINS: tr("黑名单·网站"), BLACK_APPS: tr("黑名单·应用"),
            WHITE_UPS: tr("白名单·UP 主"), BLACK_UPS: tr("黑名单·UP 主"),
        }.get(key, key)
