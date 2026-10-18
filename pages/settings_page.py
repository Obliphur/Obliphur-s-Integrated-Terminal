

import tkinter as tk
from datetime import datetime
from tkinter import filedialog, ttk

import config
import toast
from config import COLORS
from i18n import tr
from settings_manager import APPLY_INSTANT, SCHEMA_BY_KEY, SETTINGS_PAGES, page_specs
from .widgets import (ScrollArea, Switch, wrap_label, frozen, smooth, cfg, set_var, flavor,
                      show_pack, hide_pack, show_grid, hide_grid)


class SettingsPage(tk.Frame):
    def __init__(self, master, settings, on_changed=None, on_sound=None,
                 on_dev_mode=None, on_language=None, on_font_scale=None,
                 on_theme=None, on_end_setup=None, updater=None, on_update_check=None,
                 on_update_install=None, on_update_relay=None, sync=None, achievements=None):
        super().__init__(master, bg=COLORS["bg"])
        self.sm = settings
        self.ach_store = achievements
        self.ach_panel = None
        self.updater = updater
        self.on_update_check = on_update_check
        self.on_update_install = on_update_install
        self.on_update_relay = on_update_relay
        self.sync = sync
        self.sync_panel = None
        self.update_card = None
        self.on_changed = on_changed
        self.on_sound = on_sound
        self.on_dev_mode = on_dev_mode
        self.on_language = on_language
        self.on_font_scale = on_font_scale
        self.on_theme = on_theme
        self.on_end_setup = on_end_setup
        self.setup_card = None
        self._rows = {}          # 选项类设置的行 (各页上半部分)
        self._num_rows = {}      # 数值类设置的行 (各页下半部分)
        self.areas = {}          # 每个模块一页
        self._tab = "basic"
        self._syncing = False

        self._i18n_widgets = []
        self._choices_widgets = []
        self._build()


    def _track(self, widget, key=None, *, translate: bool = True):
        self._i18n_widgets.append((widget, key, translate))
        return widget

    def _retranslate(self):
        for widget, key, translate in self._i18n_widgets:
            try:
                if callable(key):
                    widget.config(text=key())
                else:
                    widget.config(text=tr(key) if translate else key)
            except Exception:
                continue

        for key, widget in self._choices_widgets:
            try:
                widget.config(values=[lab for _v, lab in
                                      self.sm.labeled_values(key, translate=True)])
            except Exception:
                continue

    def _build(self):
        bg = COLORS["bg"]

        hdr = tk.Frame(self, bg=bg)
        hdr.pack(fill="x", padx=28, pady=(18, 6))
        self._track(tk.Label(
            hdr, text=tr("⚙  设置"), bg=bg, fg=COLORS["text"],
            font=config.font("h1", bold=True)
        ), "⚙  设置").pack(side="left")

        # 一个模块一页, 顺序和左侧导航一致
        tabs = tk.Frame(self, bg=bg)
        tabs.pack(fill="x", padx=28, pady=(2, 4))
        self._tab_btns = {}
        for key, text in SETTINGS_PAGES:
            b = self._track(tk.Label(
                tabs, text=tr(text), font=config.font("body", bold=True),
                padx=14, pady=4, cursor="hand2"), text)
            b.pack(side="left", padx=(0, 6))
            b.bind("<Button-1>", lambda e, k=key: self._click_tab(k))
            self._tab_btns[key] = b

        self.stack = tk.Frame(self, bg=bg)
        self.stack.pack(fill="both", expand=True, padx=26, pady=(6, 0))

        for key, _text in SETTINGS_PAGES:
            self.areas[key] = ScrollArea(self.stack, height=420, bg=bg)

        # ---- 基本设置: 配置期, 设备同步, 以及整个程序的外观 (语言 / 字号 / 主题) ----
        self.area = self.areas["basic"]
        body = self.area.inner
        self._build_setup_card(body)
        if self.sync is not None:
            from .sync_panel import SyncPanel
            self.sync_panel = SyncPanel(body, self.sync)
            self.sync_panel.pack(fill="x")
        self._build_page_items("basic")
        self._build_update_card(body)
        # 成就 (MECHANICS 17): 不再是单独的一页, 放在基本设置里
        if self.ach_store is not None:
            from .achievements_page import AchievementsPanel
            self.ach_panel = AchievementsPanel(body, self.ach_store, bg=bg)
            self.ach_panel.pack(fill="x", pady=(10, 0))
        # 作者的话: 为什么设置要等 48 小时 (MECHANICS.md 1.1)
        for i, zh in enumerate((
                "在设计预期上 只要不是有绝对的正当理由 应该很少有改变能坚持过48h的等待期.\n这是一个理智与本能的抗争的常见结果.\n这个软件的一个理念就是放大理智优势,保留本能空间.",
                "'放松'方向的改变会被限制,是在希望未来的某一刻,理智只需要占据短暂的上风,就能阻止这个进程.\n为什么'收紧'也需要限制?作者希望用户不要竭泽而渔.你可能想到多的一个小时学习时间有多有价值 但是可能想不到少的一个小时休息时间会对心理健康有多大的压力.所以请在这一段时间好好考虑少的时间/禁用某些关键网站会对你的生活有什么影响.")):
            fv = flavor(body, tr(zh), bg=bg, pad=12)
            self._track(fv.label, zh)
            fv.pack(fill="x", pady=(14 if i == 0 else 8, 6 if i else 0))

        # ---- 其余模块: 上面是选项, 下面是数值 ----
        for key, _text in SETTINGS_PAGES[1:]:
            self._build_page_items(key)

        # ---- 各页共用的底栏 ----
        foot = tk.Frame(self, bg=bg)
        foot.pack(fill="x", padx=28, pady=(4, 10))
        self.lbl_global = tk.Label(
            foot, text="", bg=bg, fg=COLORS["text_dim"],
            font=config.font("small"), anchor="w", justify="left"
        )
        self.lbl_global.pack(fill="x", pady=(0, 6))
        btns = tk.Frame(foot, bg=bg)
        btns.pack(fill="x")
        self._track(ttk.Button(btns, text=tr("💾 保存本页修改"), style="Accent.TButton",
                               command=self._save_all),
                    "💾 保存本页修改").pack(side="left")
        self.btn_cancel_all = self._track(
            ttk.Button(btns, text=tr("↩ 取消全部待生效修改"), command=self._cancel_all),
            "↩ 取消全部待生效修改")
        self.btn_cancel_all.pack(side="left", padx=(8, 0))

        self.show_tab(self._tab)

    @smooth
    def _click_tab(self, key: str):
        self.show_tab(key)

    def show_tab(self, key: str):
        if key not in self.areas:
            key = "basic"
        self._tab = key
        for k, a in self.areas.items():
            if k != key:
                hide_pack(a)
        show_pack(self.areas[key], fill="both", expand=True)
        for k, b in self._tab_btns.items():
            if k == key:
                cfg(b, bg=COLORS["accent"], fg=COLORS["on_accent"])
            else:
                cfg(b, bg=COLORS["bg_card"], fg=COLORS["text"])

    # ---- 标记: 不需要等待的设置项 ----

    def _badge(self, key: str):
        tag = self.sm.wait_tag(key)
        if tag == "instant":
            return tr("⚡ 即时生效"), COLORS["success"]
        if tag == "setup":
            return tr("⚡ 配置期内即时"), COLORS["success"]
        if tag == "free":
            return tr("⚡ 这一次修改免等待"), COLORS["success"]
        return "", COLORS["text_dim"]

    # ---- 一个模块页: 选项 / 开关在上, 数值在下 ----

    def _build_page_items(self, page: str):
        area = self.areas[page]
        body = area.inner
        choices, nums = page_specs(page)
        for spec in choices:
            self._build_row(body, spec, area)
        plain = [s for s in nums if not s.get("group")]
        weekday = [s for s in nums if s.get("group") == "weekday"]
        if plain:
            self._build_num_page(body, plain, area)
        if weekday:
            self._build_num_page(body, weekday, area)
        area.bind_children()

    # ---- 数值项: 直接填写, 一项一行 ----

    def _build_num_page(self, body, specs, area):
        card = COLORS["bg_card"]
        grid = tk.Frame(body, bg=card, highlightbackground=COLORS["border"],
                        highlightthickness=1)
        grid.pack(fill="x", pady=(2, 8))
        grid.grid_columnconfigure(6, weight=1)
        small = config.font("small")
        r = 0
        for i, spec in enumerate(specs):
            key = spec["key"]
            if i:
                tk.Frame(grid, bg=COLORS["border"], height=1).grid(
                    row=r, column=0, columnspan=7, sticky="ew", padx=8)
                r += 1
            py_top = (8, 0)
            name = self._track(tk.Label(
                grid, text=tr(spec["short"]), bg=card, fg=COLORS["text"],
                font=config.font("body", bold=True), anchor="w"), spec["short"])
            name.grid(row=r, column=0, sticky="w", padx=(12, 6), pady=py_top)
            badge = tk.Label(grid, text="", bg=card, font=small, anchor="w")
            badge.grid(row=r, column=1, sticky="w", padx=(0, 8), pady=py_top)

            var = tk.StringVar()
            ent = ttk.Entry(grid, textvariable=var, width=9, justify="right")
            ent.grid(row=r, column=2, sticky="w", pady=py_top)
            ent.bind("<Return>", lambda e, k=key: self._stage_num(k))
            tk.Label(grid, text=spec["unit"], bg=card, fg=COLORS["text_dim"],
                     font=small, width=4, anchor="w").grid(
                row=r, column=3, sticky="w", padx=(4, 4), pady=py_top)
            self._track(ttk.Button(grid, text=tr("提交"), width=6,
                                   command=lambda k=key: self._stage_num(k)), "提交"
                        ).grid(row=r, column=4, sticky="w", pady=py_top)
            lbl_cur = tk.Label(grid, text="", bg=card, fg=COLORS["text_dim"],
                               font=small, anchor="w")
            lbl_cur.grid(row=r, column=5, columnspan=2, sticky="w", padx=(10, 6),
                         pady=py_top)
            r += 1

            # 待生效的改动单独占一行, 只在有改动排队时显示
            pend = tk.Frame(grid, bg=card)
            pend.grid(row=r, column=0, columnspan=7, sticky="w", padx=12, pady=(2, 0))
            lbl_pending = tk.Label(pend, text="", bg=card, fg=COLORS["warn"],
                                   font=small, anchor="w")
            lbl_pending.pack(side="left")
            btn_cancel = self._track(tk.Label(
                pend, text="✕ " + tr("取消"), bg=card, fg=COLORS["danger"],
                font=small, cursor="hand2"), lambda: "✕ " + tr("取消"))
            btn_cancel.pack(side="left", padx=(8, 0))
            btn_cancel.bind("<Button-1>", lambda e, k=key: self._cancel(k))
            pend.grid_remove()
            r += 1

            tk.Frame(grid, bg=card, height=6).grid(row=r, column=0, columnspan=7)
            r += 1

            self._num_rows[key] = {
                "spec": spec, "var": var, "entry": ent, "badge": badge, "name": name,
                "lbl_current": lbl_cur, "lbl_pending": lbl_pending,
                "btn_cancel": btn_cancel, "pend": pend, "synced": None,
            }
        area.bind_children()

    def _sync_num(self, key: str, force: bool = False):
        """把填写框同步成当前生效值; 用户改过但还没提交的框不动 (除非 force)."""
        row = self._num_rows[key]
        text = self.sm.ui_number(key)
        if force or row["synced"] is None or row["var"].get().strip() == row["synced"]:
            set_var(row["var"], text)
        row["synced"] = text

    def _num_edited(self, key: str) -> bool:
        row = self._num_rows[key]
        return row["var"].get().strip() != (row["synced"] or "")

    def _stage_num(self, key: str, quiet: bool = False):
        row = self._num_rows[key]
        value = self.sm.from_ui_number(key, row["var"].get())
        status, msg = self.sm.stage(key, value)
        if status == "dev":
            # 开发者模式开 / 关: 不是真的修改, 填写框恢复成当前值, 不提示
            self._sync_num(key, force=True)
            if self.on_dev_mode:
                self.on_dev_mode(config.dev_mode_enabled())
            return status, msg
        if status == "invalid":
            if not quiet:
                toast.warn(msg)
            return status, msg
        self._sync_num(key, force=True)
        self.refresh()
        if status in ("instant", "staged") and self.on_changed:
            self.on_changed(status, key)
        if not quiet and status in ("instant", "staged", "nochange"):
            toast.info(tr(msg))
        return status, msg

    def _build_setup_card(self, body):
        """配置期: 剩余时间 + 提前结束按钮. 不在配置期时整块隐藏."""
        card = COLORS["bg_card"]
        fr = tk.Frame(body, bg=card, highlightbackground=COLORS["accent"],
                      highlightthickness=1)
        self.setup_card = fr
        row = tk.Frame(fr, bg=card)
        row.pack(fill="x", padx=12, pady=(10, 2))
        self._track(tk.Label(
            row, text=tr("⏳  配置期"), bg=card, fg=COLORS["text"],
            font=config.font("body", bold=True)), "⏳  配置期").pack(side="left")
        self._track(ttk.Button(row, text=tr("结束配置期"), command=self._end_setup),
                    "结束配置期").pack(side="right")
        self.lbl_setup = tk.Label(fr, text="", bg=card, fg=COLORS["text_dim"],
                                  font=config.font("small"), anchor="w", justify="left")
        self.lbl_setup.pack(fill="x", padx=12, pady=(0, 10))
        wrap_label(self.lbl_setup, 24)

    def _end_setup(self):
        if not toast.ask(
            tr("结束后每次修改都要等 48 小时,不能撤销."),
            title=tr("结束配置期"), danger=True
        ):
            return
        if self.on_end_setup:
            self.on_end_setup()
        self.refresh()

    def _build_update_card(self, body):
        """更新 (MECHANICS 16): 当前版本, GitHub 上的最新版本, 检查 / 立即更新."""
        if self.updater is None:
            return
        card = COLORS["bg_card"]
        fr = tk.Frame(body, bg=card, highlightbackground=COLORS["border"], highlightthickness=1)
        fr.pack(fill="x", pady=(2, 8))
        self.update_card = fr
        row = tk.Frame(fr, bg=card)
        row.pack(fill="x", padx=12, pady=(10, 2))
        self._track(tk.Label(row, text=tr("⬆  更新"), bg=card, fg=COLORS["text"],
                             font=config.font("body", bold=True)), "⬆  更新").pack(side="left")
        self.lbl_update = tk.Label(fr, text="", bg=card, fg=COLORS["text_dim"],
                                   font=config.font("small"), anchor="w", justify="left")
        self.lbl_update.pack(fill="x", padx=12)
        btns = tk.Frame(fr, bg=card)
        btns.pack(fill="x", padx=12, pady=(6, 10))
        self._track(ttk.Button(btns, text=tr("检查更新"),
                               command=lambda: self.on_update_check and self.on_update_check()),
                    "检查更新").pack(side="left")
        self.btn_update_now = self._track(ttk.Button(
            btns, text=tr("立即更新"), style="Accent.TButton",
            command=lambda: self.on_update_install and self.on_update_install()), "立即更新")

        # 给绑定的其他设备下载安装包, 直接从局域网发过去 (对方不用自己去 GitHub 下载)
        self._track(ttk.Button(btns, text=tr("📤 发给其他设备"),
                               command=lambda: self.on_update_relay and self.on_update_relay()),
                    "📤 发给其他设备").pack(side="left", padx=(8, 0))

        def open_page():
            import webbrowser
            import updater
            webbrowser.open(updater.RELEASES_PAGE)
        self._track(ttk.Button(btns, text=tr("发布页"), command=open_page), "发布页").pack(
            side="right")

    def _refresh_update_card(self):
        u = self.updater
        if u is None or self.update_card is None:
            return
        text = tr("当前版本 {v}", v=config.APP_VERSION)
        state = {
            "checking": tr("正在检查…"),
            "latest": tr("已是最新版本"),
            "available": tr("发现新版本 {v}", v=u.latest_text),
            "downloading": tr("正在下载更新…"),
            "installed": tr("已更新到 {v}, 重启后生效", v=u.latest_text),
            "dev": tr("这是 git 仓库里的开发版本, 不会自动覆盖, 请用 git pull 更新"),
            "error": tr("检查 / 更新失败: {e}", e=u.error),
        }.get(u.state, "")
        if state:
            text += "  ·  " + state
        cfg(self.lbl_update, text=text,
            fg=COLORS["accent"] if u.state in ("available", "installed") else
            (COLORS["warn"] if u.state in ("error", "dev") else COLORS["text_dim"]))
        if u.state == "available":
            show_pack(self.btn_update_now, side="left", padx=(8, 0))
        else:
            hide_pack(self.btn_update_now)

    def _build_row(self, body, spec, area):
        key, kind = spec["key"], spec["kind"]
        card = COLORS["bg_card"]

        inner = tk.Frame(body, bg=card, highlightbackground=COLORS["border"],
                         highlightthickness=1)
        inner.pack(fill="x", pady=(0, 6))

        head = tk.Frame(inner, bg=card)
        head.pack(fill="x", padx=12, pady=(10, 0))
        self._track(tk.Label(
            head, text=tr(spec["label"]), bg=card, fg=COLORS["text"],
            font=config.font("body", bold=True)), spec["label"]).pack(side="left")
        badge = tk.Label(head, text="", bg=card, font=config.font("small"))
        badge.pack(side="left", padx=(10, 0))
        lbl_cur = tk.Label(head, text="", bg=card, fg=COLORS["text_dim"],
                           font=config.font("small"))
        lbl_cur.pack(side="right")

        ctrl = tk.Frame(inner, bg=card)
        ctrl.pack(fill="x", padx=12, pady=(6, 4))

        custom_var = None
        if kind in ("choice", "combo", "float"):
            var = tk.StringVar()
            widget = ttk.Combobox(
                ctrl, textvariable=var, state="readonly", width=30,
                values=[lab for _v, lab in self.sm.labeled_values(key, translate=True)]
            )
            widget.pack(side="left")
            if spec["apply"] == APPLY_INSTANT and kind == "choice":
                # ⚡ 即时生效的项: 选中就生效 (语言 / 字号 / 主题会显示加载页并重建界面).
                # 稍后再提交: 重建界面会销毁这个下拉框, 不能在它自己的事件里做
                widget.bind("<<ComboboxSelected>>",
                            lambda _e, k=key: self.after(10, lambda: self._stage(k)))
            if kind == "combo":
                custom_var = tk.StringVar()
                ttk.Entry(ctrl, textvariable=custom_var, width=24).pack(
                    side="left", padx=(8, 0))
        else:
            var = tk.StringVar()
            widget = ttk.Entry(ctrl, textvariable=var, width=30)
            widget.pack(side="left")
            custom_var = tk.StringVar()
            ttk.Entry(ctrl, textvariable=custom_var, width=18).pack(
                side="left", padx=(8, 0))

        btn = self._track(ttk.Button(ctrl, text=tr("提交修改"), style="Accent.TButton",
                                     command=lambda k=key: self._stage(k)), "提交修改")
        btn.pack(side="left", padx=(8, 0))
        if key == "CHIME_WAV":
            self._track(ttk.Button(ctrl, text=tr("选择音频…"),
                                   command=lambda: self._pick_wav(widget, custom_var)),
                        "选择音频…").pack(side="left", padx=(8, 0))
            self._track(ttk.Button(ctrl, text=tr("▶ 试听"),
                                   command=lambda: (self.on_sound and self.on_sound())),
                        "▶ 试听").pack(side="left", padx=(8, 0))

        pending = tk.Frame(inner, bg=card)
        pending.pack(fill="x", padx=12, pady=(0, 8))
        lbl_pending = tk.Label(
            pending, text="", bg=card, fg=COLORS["warn"],
            font=config.font("small"), anchor="w", justify="left"
        )
        lbl_pending.pack(side="left")
        btn_cancel = self._track(
            ttk.Button(pending, text=tr("✕ 取消这项修改"),
                       command=lambda k=key: self._cancel(k)), "✕ 取消这项修改")

        self._rows[key] = {
            "spec": spec, "var": var, "widget": widget, "lbl_current": lbl_cur,
            "lbl_pending": lbl_pending, "btn_cancel": btn_cancel,
            "frame": inner, "custom": custom_var, "badge": badge,
        }

        self._choices_widgets.append((key, widget))

        area.bind_children()

    def _widget_value(self, key: str):
        spec = SCHEMA_BY_KEY[key]
        row = self._rows[key]
        raw = row["var"].get().strip()
        custom = (row["custom"].get().strip() if row.get("custom") else "")

        if spec["kind"] in ("choice", "combo", "instant"):

            if custom:
                return self.sm._coerce(key, custom)
            for value, label in self.sm.labeled_values(key, translate=True):
                if label == raw:
                    return value
            if key == "CHIME_WAV" and not raw:
                return "__builtin__"
            return self.sm._coerce(key, raw)
        if spec["kind"] == "float":
            for value, label in self.sm.labeled_values(key, translate=True):
                if label == raw:
                    return value
            return self.sm._coerce(key, raw)
        return self.sm._coerce(key, raw)

    def _sync_widget(self, key: str):
        spec = SCHEMA_BY_KEY[key]
        row = self._rows[key]
        current = self.sm.value(key)

        if spec["kind"] in ("choice", "combo", "instant", "float"):
            label = self.sm.label_for(key, current, translate=True)
            set_var(row["var"], label if label is not None else self.sm.display(key))
        else:
            set_var(row["var"], str(current))


    def _stage(self, key: str):
        if key in ("LANGUAGE", "FONT_SCALE", "THEME"):
            # 这几项会重建整个界面, 由 rebuild_ui 自己显示加载页
            return self._stage_now(key)
        with frozen(self):
            return self._stage_now(key)

    def _stage_now(self, key: str):
        value = self._widget_value(key)
        status, msg = self.sm.stage(key, value)
        if status == "invalid":
            toast.warn(msg)
            return
        self.refresh()
        if status in ("instant", "staged"):
            if self.on_changed:
                self.on_changed(status, key)

            if self._run_ui_hooks([key]):
                return
        if status in ("instant", "staged", "nochange"):
            toast.info(tr(msg))

    def _run_ui_hooks(self, keys) -> bool:
        """语言 / 字号 / 主题改动后需要重建整个界面. 重建会销毁本页,
        所以调用之后不要再碰本页的控件. 返回是否触发了重建."""
        if "LANGUAGE" in keys and self.on_language:
            self.on_language()
            return True
        if "FONT_SCALE" in keys and self.on_font_scale:
            self.on_font_scale()
            return True
        if "THEME" in keys and self.on_theme:
            self.on_theme()
            return True
        return False

    def _cancel(self, key: str):
        if self.sm.cancel(key):
            self.refresh()
            if self.on_changed:
                self.on_changed("cancel", key)

    def _cancel_all(self):
        n = self.sm.staged_count()
        if not n:
            return
        if not toast.ask(
            tr("取消全部 {n} 项待生效修改?").format(n=n),
            title=tr("取消待生效修改")
        ):
            return
        self.sm.cancel_all()
        self.refresh()
        if self.on_changed:
            self.on_changed("cancel", None)

    def _save_all(self):
        """保存当前这一页: 上半部分的选项和下半部分的数值一起提交."""
        choices, nums = page_specs(self._tab)
        staged, instant, same = 0, 0, 0
        ui_keys, errors = [], []
        for spec in choices:
            key = spec["key"]
            try:
                value = self._widget_value(key)
            except Exception:
                continue
            if spec["apply"] == APPLY_INSTANT:
                if value != self.sm.value(key):
                    self.sm.stage(key, value)
                    instant += 1
                    ui_keys.append(key)
                continue
            if value == self.sm.value(key):
                same += 1
                continue
            status, msg = self.sm.stage(key, value)
            if status == "staged":
                staged += 1
            elif status == "instant":
                instant += 1
            elif status == "invalid":
                errors.append(msg)
        for spec in nums:
            key = spec["key"]
            if not self._num_edited(key):
                continue
            status, msg = self._stage_num(key, quiet=True)
            if status == "invalid":
                errors.append(msg)
            elif status == "staged":
                staged += 1
            elif status == "instant":
                instant += 1
        if errors:
            toast.warn("\n".join(errors))
        self._report_saved(staged, instant, silent_if_none=bool(errors))
        self._run_ui_hooks(ui_keys)

    def _report_saved(self, staged: int, instant: int, silent_if_none: bool = False):
        self.refresh()
        if self.on_changed and (staged or instant):
            self.on_changed("staged" if staged else "instant", None)
        if instant and staged:
            toast.success(tr("{n} 项即时生效项已保存\n{m} 项已提交,48 小时后生效").format(
                n=instant, m=staged))
        elif instant:
            toast.success(tr("{n} 项即时生效项已保存").format(n=instant))
        elif staged:
            toast.info(tr("{n} 项已提交,48 小时后生效").format(n=staged))
        elif not silent_if_none:
            toast.info(tr("没有需要保存的修改"))

    def _pick_wav(self, widget, custom_var=None):
        path = filedialog.askopenfilename(
            title=tr("选择提示音"),
            filetypes=[(tr("音频文件"), "*.wav *.mp3"), (tr("WAV 音频"), "*.wav"),
                       (tr("MP3 音频"), "*.mp3"), (tr("所有文件"), "*.*")],
            parent=self.winfo_toplevel()
        )
        if not path:
            return
        if custom_var is not None:
            custom_var.set(path)
        else:
            widget.set(path)


    def show_achievements(self):
        """达成提示被点了: 切到基本设置, 滚到成就那一块."""
        self.show_tab("basic")
        self.refresh()
        if self.ach_panel is not None:
            self.area.scroll_to(self.ach_panel)

    def refresh_achievements(self):
        if self.ach_panel is not None:
            self.ach_panel.refresh()

    def refresh_sync(self):
        """设备同步那一块单独刷新 (每秒一次, 只在看着基本设置时)."""
        if self.sync_panel is not None and self._tab == "basic":
            self.sync_panel.refresh()

    def refresh(self):
        self._refresh_update_card()
        self.refresh_achievements()
        # 文字翻译不用每次刷新都重设: 换语言时整个界面会重建.
        # 下面只改真正变了的属性 (cfg), 避免每次刷新把几十个控件全部重画一遍.
        if self.setup_card is not None:
            left = self.sm.setup_left_seconds()
            if left > 0:
                h, rem = divmod(left, 3600)
                cfg(self.lbl_setup, text=tr("还剩 {h} 小时 {m} 分.").format(h=h, m=rem // 60))
                if self.setup_card.winfo_manager() != "pack":
                    others = [w for w in self.area.inner.pack_slaves() if w is not self.setup_card]
                    kw = {"before": others[0]} if others else {}
                    self.setup_card.pack(fill="x", pady=(2, 8), **kw)
            else:
                hide_pack(self.setup_card)

        self._syncing = True
        try:
            for key, row in self._rows.items():
                self._sync_widget(key)
                cfg(row["lbl_current"],
                    text=f"{tr('当前')}: {tr(self.sm.display(key))}")
                text, color = self._badge(key)
                cfg(row["badge"], text=text, fg=color)

                if self.sm.is_staged(key):
                    cfg(row["lbl_pending"],
                        text=tr("待生效: {value}   ·   {when}").format(
                            value=tr(self.sm.display_value(key, self.sm.staged(key)['value'])),
                            when=self.sm.apply_at_text(key))
                    )
                    show_pack(row["btn_cancel"], side="left", padx=(10, 0))
                else:
                    cfg(row["lbl_pending"], text="")
                    hide_pack(row["btn_cancel"])

            today = datetime.now().weekday()
            for key, row in self._num_rows.items():
                spec = row["spec"]
                self._sync_num(key)
                cur = f"{tr('当前')}: {self.sm.display(key)}"
                if key in config.WEEKDAY_KEYS:
                    # 星期倍率: 顺带显示这一天实际能联网多久, 并标出今天
                    quota = float(config.DAILY_LIMIT) * float(self.sm.value(key))
                    cur += f"  →  {config.fmt_hm(quota)}"
                    is_today = config.WEEKDAY_KEYS.index(key) == today
                    cfg(row["name"], text=tr(spec["short"]) + (
                        f"  ({tr('今天')})" if is_today else ""),
                        fg=COLORS["accent"] if is_today else COLORS["text"])
                cfg(row["lbl_current"], text=cur)
                text, color = self._badge(key)
                cfg(row["badge"], text=text, fg=color)
                if self.sm.is_staged(key):
                    cfg(row["lbl_pending"],
                        text=tr("待生效: {value}   ·   {when}").format(
                            value=self.sm.display_value(key, self.sm.staged(key)['value']),
                            when=self.sm.apply_at_text(key)))
                    show_grid(row["pend"])
                else:
                    cfg(row["lbl_pending"], text="")
                    hide_grid(row["pend"])
        finally:
            self._syncing = False

        n = self.sm.staged_count()
        nxt = self.sm.seconds_until_next()
        if n and nxt is not None:
            h, rem = divmod(int(nxt), 3600)
            m = rem // 60
            cfg(self.lbl_global,
                text=tr("待生效修改 {n} 项 · 最近一项 {h} 小时 {m} 分后生效").format(
                    n=n, h=h, m=m),
                fg=COLORS["warn"]
            )
            if self.btn_cancel_all.instate(["disabled"]):
                self.btn_cancel_all.state(["!disabled"])
        else:
            cfg(self.lbl_global, text=tr("当前没有待生效的修改."), fg=COLORS["text_dim"])
            if not self.btn_cancel_all.instate(["disabled"]):
                self.btn_cancel_all.state(["disabled"])
