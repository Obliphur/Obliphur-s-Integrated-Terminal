

import tkinter as tk
from datetime import datetime
from tkinter import ttk

import config
from config import COLORS
from i18n import tr
from settings_manager import SETTINGS_PAGES
from ..widgets import (ScrollArea, smooth, cfg, flavor,
                       show_pack, hide_pack, show_grid, hide_grid)



from .settings_form import FormMixin
from .settings_cards import CardsMixin
from .settings_comm import CommSettingsMixin
from .settings_record import RecordSettingsMixin


class SettingsPage(FormMixin, CardsMixin, CommSettingsMixin, RecordSettingsMixin, tk.Frame):
    def __init__(self, master, settings, on_changed=None, on_sound=None,
                 on_dev_mode=None, on_language=None, on_font_scale=None,
                 on_theme=None, on_end_setup=None, updater=None, on_update_check=None,
                 on_update_install=None, on_update_relay=None, sync=None, achievements=None,
                 on_open_achievements=None, data_dir=None, recording=None, on_delete_record=None,
                 on_contract_theme=None, comm=None, on_open_rules=None,
                 alarm=None, ringer=None, co_alarm_fn=None, ring_busy=None,
                 hidden_windows=None, on_hidden_windows=None):
        super().__init__(master, bg=COLORS["bg"])
        self.comm = comm
        self.on_open_rules = on_open_rules
        self.alarm = alarm                              # 闹钟 (1.0.359): alarm.Alarm / alarm.Ringer / 共时闹钟 f() / 现在有没有在响
        self.ringer = ringer
        self._co_alarm_fn = co_alarm_fn or (lambda: None)
        self._ring_busy = ring_busy or (lambda: False)
        self.hidden_windows = hidden_windows      # 触发隐藏的窗口 (prestart.TriggerStore)
        self.on_hidden_windows = on_hidden_windows or (lambda: None)
        self.alarm_ui = None
        self.sm = settings
        self.ach_store = achievements
        self.on_open_achievements = on_open_achievements
        self.data_dir = data_dir                        # 录像: 数据目录 (recordings/merged/<kind>)
        self.recording = recording or (lambda kind: False)   # 本机此刻在录这一路吗 (合约页"录像中"用)
        self.on_delete_record = on_delete_record or (lambda cid, kind: None)   # 删录像 (并同步删除给别的设备)
        self._rec_lists = {}
        self._rec_sig = {}
        self._cam_state = None
        self._cam_devices = []          # 本机的摄像头 [{id, name}] (后台线程枚举)
        self._cam_choices = []          # 选择摄像头下拉框: [(值, 显示的文字)]
        self._cam_sig = None
        self.lbl_ach_count = None
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
        self.on_contract_theme = on_contract_theme
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

        # ---- 基本设置: 配置期, 成就, 设备同步, 以及整个程序的外观 (语言 / 字号 / 主题) ----
        self.area = self.areas["basic"]
        body = self.area.inner
        self._build_setup_card(body)
        self._build_achievements_card(body)
        if self.sync is not None:
            from .sync_panel import SyncPanel
            self.sync_panel = SyncPanel(body, self.sync)
            self.sync_panel.pack(fill="x")
        self._build_page_items("basic")
        self._build_update_card(body)
        # 作者的话: 为什么设置要等 48 小时 (MECHANICS.md 1.1)
        for i, zh in enumerate((
                "在设计预期上 只要不是有绝对的正当理由 应该很少有改变能坚持过48h的等待期.\n这是一个理智与本能的抗争的常见结果.\n这个软件的一个理念就是放大理智优势,保留本能空间.",
                "'放松'方向的改变会被限制,是在希望未来的某一刻,理智只需要占据短暂的上风,就能阻止这个进程.\n为什么'收紧'也需要限制?作者希望用户不要竭泽而渔.你可能想到多的一个小时学习时间有多有价值 但是可能想不到少的一个小时休息时间会对心理健康有多大的压力.所以请在这一段时间好好考虑少的时间/禁用某些关键网站会对你的生活有什么影响.",
                "如果你很有动力,试试合约!")):
            fv = flavor(body, tr(zh), bg=bg, pad=12)
            self._track(fv.label, zh)
            fv.pack(fill="x", pady=(14 if i == 0 else 8, 6 if i else 0))

        # ---- 其余模块: 上面是选项, 下面是数值 ----
        for key, _text in SETTINGS_PAGES[1:]:
            if key == "record":
                self._build_record_page(self.areas[key].inner)
            if key == "net":
                self._build_rules_card(self.areas[key].inner)
            if key == "comm":
                self._build_comm_page(self.areas[key].inner)
            if key == "alarm" and self.alarm is not None:
                from .alarm_ui import AlarmSettings
                self.alarm_ui = AlarmSettings(self, self.areas[key].inner)
            self._build_page_items(key)
            if key == "window":
                self._build_hidden_windows(self.areas[key].inner)
            if key == "record":
                self._build_record_lists(self.areas[key].inner)

        # ---- 各页共用的底栏 ----
        foot = tk.Frame(self, bg=bg)
        foot.pack(fill="x", padx=28, pady=(4, 10))
        self.foot = foot
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

    def co_alarm_fn(self):
        return self._co_alarm_fn()

    def ring_busy(self) -> bool:
        return bool(self._ring_busy())

    def refresh_alarm(self):
        """闹钟页开着时每秒调一次: 下一次响铃的时间 / 试听按钮."""
        if self.alarm_ui is not None and self._tab == "alarm":
            self.alarm_ui.refresh()

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
        if key == "record":
            self._rec_sig = {}
            self._check_camera()
            self.refresh_records()
        if key == "alarm" and self.alarm_ui is not None:
            self.alarm_ui.refresh()
        if key in ("comm", "alarm"):
            if key == "comm":
                self._refresh_comm()
            hide_pack(self.foot)                 # 沟通页 / 闹钟页的设置即时保存, 没有"保存本页修改"
        else:
            show_pack(self.foot, fill="x", padx=28, pady=(4, 10))
        for k, b in self._tab_btns.items():
            if k == key:
                cfg(b, bg=COLORS.get("accent_fill", COLORS["accent"]),
                    fg=COLORS.get("on_accent_fill", COLORS["on_accent"]))
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


    def refresh(self):
        self._refresh_update_card()
        self.refresh_records()
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
                cur = ""                  # 不再显示 "当前: …"
                if key in config.WEEKDAY_KEYS:
                    # 星期倍率: 显示这一天实际能联网多久, 并标出今天
                    quota = float(config.DAILY_LIMIT) * float(self.sm.value(key))
                    cur = f"→  {config.fmt_hm(quota)}"
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




def _provider_label(provider: str) -> str:
    import ai_assist
    return ai_assist.PROVIDERS.get(provider, ai_assist.PROVIDERS["custom"])["label"]


def _fmt_size(n: int) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.2f} GB"
