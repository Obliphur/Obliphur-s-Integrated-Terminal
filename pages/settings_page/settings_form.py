"""设置页的表单: 每个模块页的选项 / 数值行、取值与暂存、保存 / 取消、试听。

从 pages/settings_page.py 拆出来的 mixin; 依赖方向: settings_page.py 导入它们, 它们从不导入 settings_page.py.
"""

import tkinter as tk
from tkinter import filedialog
from tkinter import ttk
import config
import toast
from config import COLORS
from i18n import tr
from settings_manager import APPLY_INSTANT
from settings_manager import SCHEMA_BY_KEY
from settings_manager import page_specs
from settings_manager import chime_name
from sound_effects import EVENTS, display as sound_display

from ..widgets import (frozen, set_var)


class FormMixin:
    def _build_page_items(self, page: str):
        area = self.areas[page]
        body = area.inner
        choices, nums = page_specs(page)
        for spec in choices:
            self._build_row(body, spec, area)
        groups = []
        for spec in nums:
            g = spec.get("group")
            if g not in groups:
                groups.append(g)
        for g in groups:
            if page == "daily" and g == "sleep":
                # 日常页的睡眠时间: 以前只有 7 行没有标题, 补一个标题说明 (MECHANICS 19)
                self._track(tk.Label(
                    body, text=tr("🌙  睡觉时间"), bg=COLORS["bg"], fg=COLORS["text"],
                    font=config.font("body", bold=True), anchor="w"), "🌙  睡觉时间").pack(
                    fill="x", pady=(10, 4))
            self._build_num_page(body, [s for s in nums if s.get("group") == g], area)
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
        # 选项行右上角的 "当前: …" 小字不再显示 (控件里本来就是当前值), 标签留着不画

        ctrl = tk.Frame(inner, bg=card)
        ctrl.pack(fill="x", padx=12, pady=(6, 4))

        custom_var = None
        if key in EVENTS:
            var = tk.StringVar()
            widget = ttk.Combobox(ctrl, textvariable=var, state="readonly", width=28)
            widget.pack(side="left", fill="x", expand=True)
            ctrl = tk.Frame(inner, bg=card)
            ctrl.pack(fill="x", padx=12, pady=(2, 4))
            custom_var = tk.StringVar()          # 选好的音频文件的完整路径 (不显示)
            self._track(ttk.Button(ctrl, text=tr("选择音频…"),
                                   command=lambda: self._pick_wav(widget, custom_var, key)),
                        "选择音频…").pack(side="left", padx=(8, 0))
            self._track(ttk.Button(ctrl, text=tr("▶ 试听"), command=lambda k=key: self._preview_chime(k)),
                        "▶ 试听").pack(side="left", padx=(8, 0))
            widget.bind("<<ComboboxSelected>>", lambda _e: self._rows[key].update(
                picked=True))
        elif kind in ("choice", "combo", "float"):
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
        elif kind == "text":
            # 自由填写的一行字 (行为模式的外出 / 休息自动记录内容)
            var = tk.StringVar()
            widget = ttk.Entry(ctrl, textvariable=var, width=30)
            widget.pack(side="left")
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

        if key not in EVENTS and kind in ("choice", "combo", "float"):
            self._choices_widgets.append((key, widget))

        area.bind_children()

    def _chime_values(self, path: str = "", key="CHIME_WAV"):
        """内置音效与选好的文件；自选音频只显示文件名。"""
        out = [(v, lab) for v, lab in self.sm.labeled_values(key, translate=True)]
        if path:
            out.append((path, chime_name(path)))
        return out

    def _widget_value(self, key: str):
        spec = SCHEMA_BY_KEY[key]
        row = self._rows[key]
        if key in EVENTS:
            label = row["var"].get().strip()
            for value, lab in self._chime_values(row["custom"].get().strip(), key):
                if lab == label:
                    return value
            return self.sm.value(key)
        raw = row["var"].get().strip()
        custom = (row["custom"].get().strip() if row.get("custom") else "")

        if spec["kind"] in ("choice", "combo", "instant"):

            if custom:
                return self.sm._coerce(key, custom)
            for value, label in self.sm.labeled_values(key, translate=True):
                if label == raw:
                    return value
            if key in EVENTS and not raw:
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
        if key in EVENTS:
            path = str(current).strip() if str(current).strip() not in ("", "__beep__", *dict(self.sm.labeled_values(key))) else ""
            if row.get("picked"):
                path = row["custom"].get().strip()     # 选了文件还没提交: 不要被刷新盖掉
            else:
                set_var(row["custom"], path)
            values = self._chime_values(path, key)
            labels = [lab for _v, lab in values]
            if list(row["widget"].cget("values")) != labels:
                row["widget"].config(values=labels)
            if not row.get("picked"):
                set_var(row["var"], sound_display(current))
            return

        if spec["kind"] in ("choice", "combo", "instant", "float"):
            label = self.sm.label_for(key, current, translate=True)
            set_var(row["var"], label if label is not None else self.sm.display(key))
        else:
            set_var(row["var"], str(current))


    def _stage(self, key: str):
        if key in ("LANGUAGE", "FONT_SCALE", "THEME", "CONTRACT_THEME"):
            # 这几项会重建整个界面, 由 rebuild_ui 自己显示加载页
            return self._stage_now(key)
        with frozen(self):
            return self._stage_now(key)

    def _adapter_ok(self, key: str, value) -> bool:
        """绑定了其他设备时选 Adapter 断网方式: 先警告 (MECHANICS 15.5). 取消时把选项改回去."""
        if key != "NET_MODE" or value != "adapter" or self.sm.value(key) == "adapter":
            return True
        try:
            peers = bool(self.sync and self.sync.peers())
        except Exception:
            peers = False
        if not peers:
            return True
        if toast.ask(tr("该模式没有正确适配所有功能,你真的要选择这个模式吗?"), title=tr("Adapter 断网方式"),
                     ok_text=tr("选择"), danger=True):
            return True
        self._sync_widget(key)
        return False

    def _stage_now(self, key: str):
        value = self._widget_value(key)
        if not self._adapter_ok(key, value):
            return
        if key in EVENTS:
            self._rows[key]["picked"] = False
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
        if "CONTRACT_THEME" in keys and self.on_contract_theme:      # 只重建界面, 不弹界面主题的提示
            self.on_contract_theme()
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
                if key in EVENTS:
                    self._rows[key]["picked"] = False
                continue
            if value == self.sm.value(key):
                same += 1
                continue
            if not self._adapter_ok(key, value):
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

    def _preview_chime(self, key="CHIME_WAV"):
        """试听下拉框里此刻选中的提示音 (还没提交也可以试听)."""
        if not self.on_sound:
            return
        try:
            value = self._widget_value(key)
        except Exception:
            value = None
        if key == "CHIME_WAV":
            self.on_sound(value)
        else:
            import sound_effects
            sound_effects.play(key, self.data_dir or config.app_data_dir(), value)

    def _pick_wav(self, widget, custom_var=None, key="CHIME_WAV"):
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
            values = self._chime_values(path, key)
            widget.config(values=[lab for _v, lab in values])
            widget.set(chime_name(path))
            self._rows[key]["picked"] = True
        else:
            widget.set(path)



    def _card(self, body, title_zh):
        card = COLORS["bg_card"]
        fr = tk.Frame(body, bg=card, highlightbackground=COLORS["border"], highlightthickness=1)
        fr.pack(fill="x", pady=(2, 8))
        head = tk.Frame(fr, bg=card)
        head.pack(fill="x", padx=12, pady=(10, 2))
        self._track(tk.Label(head, text=tr(title_zh), bg=card, fg=COLORS["text"],
                             font=config.font("body", bold=True)), title_zh).pack(side="left")
        return fr, head

