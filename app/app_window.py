"""窗口 / 主题 / 布局 / 页面切换 (MECHANICS 11).

从 app.py 的 BreakTimerApp 拆出来的 mixin: 状态都在 self 上, 这里只放方法.
"""

import sys
from datetime import datetime

import tkinter as tk
from tkinter import font as tkfont, ttk

import closedtest
import comm as comm_mod
import config
import i18n
import toast
from app.app_base import URBAN_NOTICE_FLAG, URBAN_NOTICE_HINTS
from config import APP_NAME, COLORS
from i18n import tr
from pages import (AchievementsPage, CalendarPage, CommPage, ContractPage, HourLogPage,
                   PrescriptPage, SettingsPage, StatsPage)
from pages.share_dock import AiDataView, ShareDock
from pages.widgets import LoadingCover, cfg, frozen


class WindowMixin:
    def _work_area(self):
        """主屏幕去掉任务栏之后的区域 (左, 上, 右, 下); 取不到就用整个屏幕."""
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes
                rect = wintypes.RECT()
                if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):      # SPI_GETWORKAREA
                    return rect.left, rect.top, rect.right, rect.bottom
            except Exception:
                pass
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _apply_geometry(self, initial=False):
        """initial: 程序刚打开时设窗口大小, 并放到屏幕正中; 重建界面 (换主题 / 语言) 时不动窗口的大小和位置."""
        if initial:
            left, top, right, bottom = self._work_area()
            frame = config.scaled(40)                    # 标题栏和边框的高度: 居中时把它算进窗口高度
            w = min(config.scaled(config.WINDOW_W), int((right - left) * 0.96))
            h = min(config.scaled(config.WINDOW_H), int((bottom - top) * 0.96) - frame)
            x = left + max(0, (right - left - w) // 2)
            y = top + max(0, (bottom - top - h - frame) // 2)
            self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(config.scaled(config.MIN_WINDOW_W), config.scaled(config.MIN_WINDOW_H))
        if initial and getattr(config, "FULLSCREEN_DEFAULT", 0):
            self._set_fullscreen(True)                     # 设置 → 默认全屏
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                f = tkfont.nametofont(name)
                f.configure(family=config.FONT_FAMILY, size=config.font("body")[1])
            except Exception:
                pass


    def _apply_theme(self):
        """按设置里的 THEME 换色: 根窗口底色, tk 控件默认色, ttk 样式, 标题栏."""
        config.apply_theme(getattr(config, "THEME", config.DEFAULT_THEME))
        self.root.configure(bg=COLORS["bg"])
        self._apply_tk_options()
        self.style = ttk.Style(self.root)
        self.style.theme_use("clam")
        self._theme_ttk()
        self._apply_titlebar()
        # 都市之意: 主窗口标题是"指令终端" (其他主题用程序名)
        self.root.title((tr("指令终端") if getattr(config, "THEME", "") == "urban_will" else APP_NAME) + closedtest.tag())

    def _apply_tk_options(self):
        """没有单独指定颜色的 tk 控件 (列表框, 多行文本, 下拉框弹出列表) 的默认色."""
        t, r = config.TTK_COLORS, self.root
        for cls in ("Listbox", "Text"):
            r.option_add(f"*{cls}.background", t["list_bg"], "interactive")
            r.option_add(f"*{cls}.foreground", t["list_fg"], "interactive")
            r.option_add(f"*{cls}.selectBackground", t["list_select"], "interactive")
            r.option_add(f"*{cls}.selectForeground", t["list_on_select"], "interactive")
            r.option_add(f"*{cls}.highlightColor", COLORS.get("focus", COLORS["accent"]), "interactive")
        r.option_add("*Text.insertBackground", t["list_fg"], "interactive")
        r.option_add("*Button.highlightThickness", 0, "interactive")
        r.option_add("*Frame.highlightColor", COLORS["bg"], "interactive")
        r.option_add("*Frame.highlightBackground", COLORS["bg"], "interactive")
        r.option_add("*Entry.highlightColor", COLORS.get("input_focus", COLORS["accent"]), "interactive")
        if (config.THEME_DATA or {}).get("seamless"):
            # 全黑主题: 去掉 Listbox / Text 自带的 3D 边框和灰线, 让它们融进背景.
            for cls in ("Listbox", "Text"):
                r.option_add(f"*{cls}.borderWidth", 0, "interactive")
                r.option_add(f"*{cls}.relief", "flat", "interactive")
                r.option_add(f"*{cls}.highlightThickness", 0, "interactive")
        r.option_add("*TCombobox*Listbox.background", t["list_bg"], "interactive")
        r.option_add("*TCombobox*Listbox.foreground", t["list_fg"], "interactive")
        r.option_add("*TCombobox*Listbox.selectBackground", t["list_select"], "interactive")
        r.option_add("*TCombobox*Listbox.selectForeground", t["list_on_select"], "interactive")

    def _apply_titlebar(self):
        """Windows 10/11: 深色主题时让标题栏也变成深色 (之后创建的弹窗由 config.install_titlebar_hook 处理)."""
        config.install_titlebar_hook()
        self.root.update_idletasks()
        config.apply_titlebar(self.root)

    def _theme_ttk(self):
        s = self.style
        bg, card = COLORS["bg"], COLORS["bg_card"]
        ac, tx = COLORS["accent"], COLORS["text"]
        td = COLORS["text_dim"]
        on_ac = COLORS["on_accent"]
        acf = COLORS.get("accent_fill", ac)
        on_acf = COLORS.get("on_accent_fill", on_ac)
        acf_hover = COLORS.get("accent_fill_hover", COLORS["accent_hover"])
        t = config.TTK_COLORS

        # clam 的基础色: 普通按钮 / 输入框 / 滚动条 / 下拉框等都从这里取色
        s.configure(
            ".", background=t["frame"], foreground=t["fg"],
            bordercolor=t["border"], lightcolor=t["light"], darkcolor=t["dark"],
            troughcolor=t["trough"], fieldbackground=t["field"],
            selectbackground=t["select"], selectforeground=t["on_select"],
            insertcolor=t["fg"], arrowcolor=t["fg"], focuscolor=t["border"]
        )
        s.map(".", foreground=[("disabled", t["disabled_fg"])],
              background=[("disabled", t["frame"]), ("active", t["frame_hover"])])
        s.map("TButton",
              background=[("disabled", t["frame"]), ("pressed", t["dark"]),
                          ("active", t["frame_hover"])],
              foreground=[("disabled", t["disabled_fg"])])
        if (config.THEME_DATA or {}).get("focus_halo"):
            # 有暖色焦点灯圈的主题: 下拉框获得焦点时字段保持黑底 (镂空), 只靠灯圈提示.
            s.configure("TCombobox", bordercolor=t["border"], lightcolor=t["light"],
                        darkcolor=t["dark"], focuscolor=t["border"])
            s.map("TCombobox",
                  bordercolor=[("focus", t["border"])],
                  lightcolor=[("focus", t["light"])],
                  darkcolor=[("focus", t["dark"])],
                  focuscolor=[("focus", t["border"])],
                  fieldbackground=[("readonly", t["field"]), ("disabled", t["frame"])],
                  foreground=[("readonly", t["on_select"]),
                              ("disabled", t["disabled_fg"])],
                  background=[("active", t["frame_hover"]), ("pressed", t["frame_hover"])])
            for cls in ("TEntry", "TSpinbox"):
                s.configure(cls, bordercolor=t["border"], lightcolor=t["light"],
                            darkcolor=t["dark"], fieldbackground=t["field"],
                            focuscolor=t["border"])
                s.map(cls,
                      bordercolor=[("focus", t["border"])],
                      lightcolor=[("focus", t["light"])],
                      darkcolor=[("focus", t["dark"])],
                      focuscolor=[("focus", t["border"])])
        else:
            # 从有灯圈的主题切回来时, 把 Combobox / Entry 的焦点色和边框恢复成普通主题的.
            s.configure("TCombobox", bordercolor=t["border"], lightcolor=t["light"],
                        darkcolor=t["dark"], focuscolor=t["border"])
            s.map("TCombobox",
                  bordercolor=[], lightcolor=[], darkcolor=[], focuscolor=[],
                  fieldbackground=[("readonly", "focus", t["select"]),
                                   ("readonly", t["frame"]), ("disabled", t["frame"])],
                  foreground=[("readonly", "focus", t["on_select"]),
                              ("disabled", t["disabled_fg"])],
                  background=[("active", t["frame_hover"]), ("pressed", t["frame_hover"])])
            for cls in ("TEntry", "TSpinbox"):
                s.configure(cls, bordercolor=t["border"], lightcolor=t["light"],
                            darkcolor=t["dark"], fieldbackground=t["field"],
                            focuscolor=t["border"])
                s.map(cls, bordercolor=[], lightcolor=[], darkcolor=[], focuscolor=[])
        s.map("TEntry", fieldbackground=[("readonly", t["frame"]),
                                         ("disabled", t["frame"])])
        s.map("TSpinbox", fieldbackground=[("readonly", t["frame"]),
                                           ("disabled", t["frame"])])
        s.map("TScrollbar", background=[("active", t["frame_hover"]),
                                        ("pressed", t["frame_hover"])])
        s.configure("TCheckbutton", background=bg, foreground=tx,
                    indicatorbackground=t["field"], indicatorforeground=t["fg"],
                    upperbordercolor=t["border"], lowerbordercolor=t["border"])
        s.map("TCheckbutton",
              background=[("active", bg)],
              indicatorbackground=[("pressed", t["frame"]), ("selected", t["field"])])
        s.configure("TLabel", background=bg, foreground=tx)
        s.configure("TFrame", background=bg)

        s.configure("Card.TFrame", background=card)
        s.configure("TopBar.TFrame", background=COLORS.get("topbar", ac))

        s.configure("UI.TLabel", background=bg, foreground=td, font=config.font("body"))
        s.configure("UI.Bold.TLabel", background=bg, foreground=tx, font=config.font("body", bold=True))
        s.configure("Card.TLabel", background=card, foreground=tx, font=config.font("body"))
        s.configure("Card.Bold.TLabel", background=card, foreground=COLORS["text_strong"],
                    font=config.font("body", bold=True))
        s.configure("UI.TCheckbutton", background=bg, foreground=td, font=config.font("small"))
        s.map("UI.TCheckbutton", background=[("active", bg)],
              foreground=[("active", tx)])

        s.configure(
            "Accent.TButton",
            background=acf, foreground=on_acf,
            bordercolor=acf, lightcolor=acf, darkcolor=acf,
            font=config.font("body", bold=True),
            borderwidth=0, focusthickness=0, padding=(18, 10)
        )
        s.map(
            "Accent.TButton",
            background=[("disabled", COLORS["accent_disabled"]),
                        ("active", acf_hover)],
            foreground=[("disabled", COLORS["on_accent_disabled"])],
            bordercolor=[("disabled", COLORS["accent_disabled"]),
                         ("active", acf_hover)],
            lightcolor=[("disabled", COLORS["accent_disabled"]),
                        ("active", acf_hover)],
            darkcolor=[("disabled", COLORS["accent_disabled"]),
                       ("active", acf_hover)]
        )

        s.configure("UI.TSeparator", background=COLORS["border"],
                    lightcolor=COLORS["border"], darkcolor=COLORS["border"],
                    bordercolor=COLORS["border"])
        if (config.THEME_DATA or {}).get("seamless"):
            # 全黑主题不需要分隔线: 清空布局, 连那条 1px 灰线也不画.
            s.layout("UI.TSeparator", [])
        else:
            s.layout("UI.TSeparator", s.layout("TSeparator"))
        s.configure(
            "Quota.Horizontal.TProgressbar",
            troughcolor=COLORS["border"], background=ac,
            bordercolor=COLORS["border"], lightcolor=ac, darkcolor=ac,
            borderwidth=0, thickness=8
        )
        dg = COLORS["danger"]
        df = COLORS.get("danger_fill", dg)
        df_hover = COLORS.get("danger_fill_hover", COLORS["danger_hover"])
        on_df = COLORS.get("on_danger_fill", on_ac)
        s.configure(
            "Danger.TButton",
            background=df, foreground=on_df,
            bordercolor=df, lightcolor=df, darkcolor=df,
            font=config.font("body", bold=True),
            borderwidth=0, focusthickness=0, padding=(18, 10)
        )
        s.map(
            "Danger.TButton",
            background=[("disabled", COLORS["danger_disabled"]),
                        ("active", df_hover)],
            foreground=[("disabled", on_ac)],
            bordercolor=[("disabled", COLORS["danger_disabled"]),
                         ("active", df_hover)],
            lightcolor=[("disabled", COLORS["danger_disabled"]),
                        ("active", df_hover)],
            darkcolor=[("disabled", COLORS["danger_disabled"]),
                       ("active", df_hover)]
        )


    # _layout_steps 每建好一部分 yield 一次 (供加载页前进进度条); 次数 = LAYOUT_STEPS
    LAYOUT_STEPS = 10

    def _build_layout(self, parent, cover=None):
        for _ in self._layout_steps(parent):
            if cover is not None:
                cover.step()

    def _prepare_page(self, page):
        """在加载页后面把页面先排一次版 (创建好所有子窗口), 再收起来.
        这样第一次切到这页时不用再从头创建, 切换更快."""
        page.grid(row=0, column=0, sticky="nsew")
        try:
            page.update_idletasks()
        except Exception:
            pass
        page.grid_remove()

    def _layout_steps(self, parent):
        bg, card = COLORS["bg"], COLORS["bg_card"]
        self._marks_shown = None            # 按钮是新建的: 标记要重新画
        self._mark_labels = {}

        self.topbar = tk.Frame(parent, bg=COLORS.get("topbar", COLORS["accent"]), height=4)
        self.topbar.pack(fill="x")

        self.shell = tk.Frame(parent, bg=bg)
        self.shell.pack(fill="both", expand=True)


        side = tk.Frame(self.shell, bg=card, highlightbackground=COLORS["border"],
                        highlightthickness=1)
        side.pack(side="left", fill="y")
        self.sidebar = side
        tk.Frame(side, bg=card, height=14).pack()

        self.nav_btns = {}


        self.nav_labels = {}
        # 指令是都市之意主题独占的模块, 放在所有模块的第一位.
        if self._prescript_enabled():
            self.nav_labels["prescript"] = tr("📜  指令")
        self.nav_labels.update({
            "timer": tr("⏱  计时器"),
            "net": tr("🌐  联网"),
            "log": tr("📝  整点记录"),
            "cal": tr("📅  日历"),
            "stats": tr("📊  统计"),
            "contract": tr("⚔  合约"),
            "settings": tr("⚙  设置"),
            "comm": tr("💬  沟通"),
            "reset": tr("🔄  重置")
        })
        for key, label in self.nav_labels.items():
            if key == "comm":                    # 沟通和重置一起放在最下面 (和设置之间空开)

                tk.Frame(side, bg=card).pack(fill="both", expand=True)
            b = tk.Button(
                side, text=label, anchor="w", width=15,
                font=config.font("nav"), relief="flat", bd=0,
                padx=14, pady=12, cursor="hand2",
                command=lambda k=key: self.show_page(k)
            )
            b.pack(fill="x", pady=(0, 2))
            self.nav_btns[key] = b


        self.content = tk.Frame(self.shell, bg=bg)
        self.content.pack(side="left", fill="both", expand=True)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.pages = {}
        if self._prescript_enabled():
            # 指令页整页盖在窗口上 (遮住侧边栏), 不放进 content 的 grid.
            self.pages["prescript"] = PrescriptPage(
                self.shell, self.prescripts, on_exit=self._exit_prescript)
        yield

        # 只有当前页挂在界面上 (grid), 其余页 grid_remove:
        # 看不见的页面不参与布局和重画, 每秒的刷新也不会去画它们.
        self.pages["cal"] = CalendarPage(self.content, self.dm)
        self.pages["cal"].fog = self._fog_seed
        self.pages["cal"].live = self._calendar_live
        self._prepare_page(self.pages["cal"])
        yield
        self.pages["stats"] = StatsPage(self.content, self.dm, self.net,
                                        focus_live=self._focus_live)
        self.pages["stats"].fog = self._fog_seed
        # 日志的 2 小时冷却随设备同步: 几台设备共用 (MECHANICS 8.7)
        self.pages["stats"].journal_get = self.prescripts.journal_view
        self.pages["stats"].journal_set = self.prescripts.note_journal
        self.pages["stats"].journal_logs = self.prescripts.logs_newest
        self.pages["stats"].journal_synced = lambda: self.sync.sync_now() if self.sync.peers() else None
        self.pages["stats"].contract_log = lambda: self.contract.history
        self.pages["stats"].violation_log = lambda: self.dm.recent_violations()
        self._prepare_page(self.pages["stats"])
        yield
        self.pages["log"] = HourLogPage(
            self.content, self.dm,
            on_text_changed=self.update_log_badge,
            on_toggle=self.on_hour_log_toggle,
            on_open_settings=lambda: self.show_page("settings"),
            locked=lambda a, b, text, now: self.contract.hour_locked(
                a, b, text, now, config.FILL_WINDOW, self.dm.auto_text_for_key),
            split=self.contract.fine_hour,
            split_all=lambda: self.contract.has("fine"),     # 细化规范 + 黑雾: 所有格子都分成两段 (18.3)
            on_filled=self._seal_extra,                      # 记录封锁: 完成填写时另外封锁两个格子 (18.4)
            fog=self._fog_words,                             # 黑雾: 只露出"现在"和待填写的格子 (18.3)
            rest=lambda a, b, text: self.contract.rest_cell(a, b, text, self.dm.auto_text_for_key),   # 休息中没填写的格子也露出来
            sealed=lambda a, b: self.contract.hour_sealed(a, b)   # 记录封锁: 按 Enter 提交前先确认
        )
        self._prepare_page(self.pages["log"])
        yield
        self.pages["contract"] = ContractPage(self.content, self.contract, on_start=self.start_contract,
                                              on_abandon=self.abandon_contract,
                                              blocked=self.contract_block,
                                              net_used=lambda: self.net.used_all,
                                              on_change=self._paint_sidebar,
                                              rec_text=self._rec_text,
                                              on_stop_screen=self.terminate_recording,
                                              on_close_rand=self.close_random_term,
                                              on_lift_ban=self.lift_net_ban,
                                              on_pick=self._warn_pick_term, on_joint_pick=self._joint_pick_done,
                                              invite_fn=self._joint_invite_info, on_invite=self._joint_invite_respond)
        self._prepare_page(self.pages["contract"])
        self.pages["settings"] = SettingsPage(
            self.content, self.settings,
            on_changed=self.on_settings_changed, on_sound=self.play_chime,
            on_dev_mode=self.on_dev_toggled,
            on_language=self.on_language_changed,
            on_font_scale=self.on_font_scale_changed,
            on_theme=self.on_theme_changed,
            on_contract_theme=self.rebuild_ui,
            on_end_setup=self.end_setup_period,
            updater=self.updater,
            on_update_check=lambda: self.check_update(auto=False),
            on_update_install=self.install_update,
            on_update_relay=lambda: self.relay_updates(manual=True),
            sync=self.sync, achievements=self.ach,
            on_open_achievements=self._open_achievements,
            data_dir=self.dm.data_dir, recording=self.recording, on_delete_record=self.delete_recording,
            comm=self.comm, on_open_rules=self.open_rules_popup,
            alarm=self.alarm, ringer=self.ringer, co_alarm_fn=self._co_alarm_fn,
            ring_busy=lambda: self._alarm_win is not None
        )
        self._prepare_page(self.pages["settings"])
        yield
        # 成就 (MECHANICS 17): 单独一页, 左侧导航里没有它 (由 设置 → 基本设置 的"🏆 成就"按钮打开)
        self.pages["ach"] = AchievementsPage(self.content, self.ach,
                                             on_back=lambda: self.show_page("settings"))
        self._prepare_page(self.pages["ach"])
        yield
        self.pages["comm"] = CommPage(
            self.content, self.comm, username=lambda: self.dm.username,
            on_open_settings=self._open_comm_settings, on_joint=self._joint_pick_start)
        self._prepare_page(self.pages["comm"])
        yield
        self.pages["net"] = tk.Frame(self.content, bg=bg)
        self._build_net_page(self.pages["net"])
        self._prepare_page(self.pages["net"])
        yield
        self.pages["timer"] = tk.Frame(self.content, bg=bg)
        self._build_timer_page(self.pages["timer"])
        self._prepare_page(self.pages["timer"])
        yield
        self.pages["reset"] = tk.Frame(self.content, bg=bg)
        self._build_reset_page(self.pages["reset"])
        self._prepare_page(self.pages["reset"])
        # share data: 页面下方的聊天悬浮窗 (第 1 行), 和盖在原页面上的"AI 的数据" (第 0 行, 在所有页面上面)
        self.share_dock = ShareDock(self.content, self.comm, on_view=lambda: self._share_sync_ui(True))
        self.ai_data_view = AiDataView(self.content)
        self.ai_data_view.bind("<<AiKind>>", lambda _e: self._share_sync_ui(True))
        self._share_ai_at = 0.0
        yield


    def rebuild_ui(self):
        if self._rebuilding or self.shutting_down:
            return
        self._rebuilding = True
        # 加载页盖住整个窗口; 旧界面在它后面拆掉, 新界面在它后面一页一页建好
        # (进度条随之前进), 最后在冻结中撤掉加载页, 新界面整窗一次显示.
        cover = LoadingCover(self.root, tr("正在重新加载界面…"),
                             tr("请稍候"),
                             steps=self.LAYOUT_STEPS + 1).show()
        try:
            self._rebuild_ui(cover)
        finally:
            try:
                with frozen(self.root):
                    cover.close()
            finally:
                self._rebuilding = False

    def _rebuild_ui(self, cover=None):
        for job in (self.midnight_job, self.hourly_job, self.update_job,
                    self.net_job, getattr(self, "apply_job", None)):
            if job:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
        self.midnight_job = self.hourly_job = self.update_job = None
        self.net_job = self.apply_job = None

        for w in (getattr(self, "topbar", None), getattr(self, "shell", None)):
            if w is not None:
                try:
                    w.destroy()
                except Exception:
                    pass
        self._apply_theme()
        if cover is not None:
            cover.recolor()
            cover.step()
        self._close_rules_popup()
        self.pages = {}
        self.nav_btns = {}
        self._apply_geometry()
        self._build_layout(self.root, cover)
        key = self.current_page if self.current_page in self.pages else "timer"
        self.show_page(key)
        self.ach_popups.close_all_windows()       # 达成提示按新的主题 / 语言重新画
        self.ach_popups.sync()

        self.refresh_display()
        self.update_log_badge()
        self.update_settings_badge()
        self._start_midnight_timer()
        self._schedule_hourly()
        self._restore_timer_view()
        if self.sleep_since is not None:
            self._show_sleep_cover()                  # 界面重建后按新的主题 / 语言重新盖上
        self._schedule_apply_check()
        self._net_tick()

    def on_language_changed(self):
        i18n.init_from_config()
        self.dm.localize_auto_texts()          # 默认的自动记录内容 (睡眠 / Sleeping 等) 换成新语言, 同一项合并
        comm_mod.ensure_personas(self.comm.personas)   # 人设文件夹里的 README 也换成新语言
        self.rebuild_ui()

    def on_theme_changed(self):
        self.rebuild_ui()
        # 切到都市之意时弹一次: 标题 + 那段话 + 确定 (只在用户主动换主题时出现).
        if config.THEME == "urban_will":
            self.root.after(120, self._city_will_notice)

    def _city_will_notice(self):
        if config.THEME != "urban_will" or self.shutting_down:
            return
        # 切到都市之意的前两次, 弹窗多一行半透明的小字: 提示可以改日程 (次数记在 daily.json, 每台设备各自数)
        shown = int(self.dm.daily_flag(URBAN_NOTICE_FLAG) or 0)
        toast.notice(
            tr("都市之意"),
            tr("也许我们时刻都在随波逐流,只不过是被赋予了选择漂流对象的那一缕所谓的自由..."),
            [(tr("确定"), None, True)],
            hint=tr("(可以在设置-日常修改日程...)") if shown < URBAN_NOTICE_HINTS else None,
        )
        if shown < URBAN_NOTICE_HINTS:
            self.dm.set_daily_flag(URBAN_NOTICE_FLAG, str(shown + 1))

    def on_font_scale_changed(self):
        self.ui_scale = config.detect_ui_scale()
        base = float(self.settings.value("FONT_SCALE") or 1.0)
        config.UI_SCALE = self.ui_scale * base
        self.rebuild_ui()


    def _cancel_jobs(self):
        self.shutting_down = True
        try:
            self.comm.flush(2.0)              # 聊天记录是后台写盘的, 关程序前等它写完
        except Exception:
            pass
        try:
            self.focus_glow.close()
        except Exception:
            pass
        for job in (self.midnight_job, self.hourly_job, self.update_job,
                    self.net_job, getattr(self, "apply_job", None),
                    getattr(self, "window_job", None),
                    getattr(self, "window_focus_job", None),
                    getattr(self, "app_rules_job", None),
                    getattr(self, "prestart_job", None),
                    getattr(self, "prescript_job", None),
                    getattr(self, "sync_job", None)):
            if not job:
                continue
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self.midnight_job = self.hourly_job = self.update_job = None
        self.net_job = self.apply_job = self.window_job = self.window_focus_job = None
        self.app_rules_job = None
        self.prestart_job = None
        self.prescript_job = None
        try:
            self.prescripts.save()
        except Exception:
            pass

    def show_page(self, key: str):
        # 合约 · 黑雾 (软规则): 要切到计时器以外的页面记一次, 一个钟点里满 50 次算一次违反 (MECHANICS 18.3)
        if key != "timer" and key != getattr(self, "current_page", None) and not getattr(self, "_page_from_sync", False):
            tid = self.contract.fog_switch(datetime.now(), self.sync.my_id)
            if tid:
                self.contract_violation(tr("黑雾中频繁切换页面"), tid)
        if key in ("settings", "ach") and self.contract.fog():
            toast.warn(tr("黑雾笼罩: 合约期间无法打开设置."))
            return
        # 先在冻结状态下刷新目标页、再置顶, 最后整窗一次性显示 —— 避免切换时的撕裂
        with frozen(self.root):
            self._show_page(key)

    def _show_page(self, key: str):
        if getattr(self, "_joint_pick", False) and key != "contract":
            self._joint_pick_cancel()                 # 联合行动选词条时点了别的页面: 取消
        if key == "prescript" and getattr(self, "current_page", "") != "prescript":
            self._prescript_back = getattr(self, "current_page", "timer")
        self.current_page = key
        # 进入 / 退出指令页: 记成事件并马上同步一次, 其他设备跟着进入 / 退出 (MECHANICS 22.8); 跟着别的设备做的不再发回去
        on_now = key == "prescript"
        if on_now != getattr(self, "_prescript_noted", False):
            self._prescript_noted = on_now
            if not getattr(self, "_page_from_sync", False):
                try:
                    self.prescripts.note_page(on_now, self.sync.my_id)
                    if self.sync.peers():
                        self.sync.sync_now()
                except Exception:
                    pass
        # V 要求一直在指令页, VI 要连续停留: 切页的这一刻就判一次, 不用等下一秒的 tick.
        try:
            self.prescripts.page_changed()
        except Exception:
            pass
        # 成就页是从设置里打开的, 侧边栏仍把"设置"标成当前页 (和手机版的抽屉一样)
        look = "settings" if key == "ach" else key
        for k, b in self.nav_btns.items():
            if k == look:
                cfg(b,
                    bg=COLORS.get("accent_fill", COLORS["accent"]),
                    fg=COLORS.get("on_accent_fill", COLORS["on_accent"]),
                    activebackground=COLORS.get("accent_fill_hover", COLORS["accent_hover"]),
                    activeforeground=COLORS.get("on_accent_fill", COLORS["on_accent"])
                )
            else:
                cfg(b,
                    bg=COLORS["bg_card"], fg=COLORS["text"],
                    activebackground=COLORS["border"], activeforeground=COLORS["text"]
                )
        # 合约页连侧边栏字体也用自己的默认字体, 不跟界面主题的像素字体.
        nav_font = config.contract_font("nav") if key == "contract" else config.font("nav")
        for b in self.nav_btns.values():
            try:
                b.configure(font=nav_font)
            except Exception:
                pass
        self._paint_sidebar()
        # 只把目标页挂到界面上, 其余页收起 (不参与布局 / 重画)
        target = self.pages[key]
        if key != "prescript" and target.winfo_manager() != "grid":
            target.grid(row=0, column=0, sticky="nsew")
        for k, f in self.pages.items():
            if k == key:
                continue
            if k == "prescript":
                if f.winfo_manager() == "place":
                    f.place_forget()
                    f.hide()
            elif f.winfo_manager() == "grid":
                f.grid_remove()
        if key == "prescript":
            page = self.pages["prescript"]
            page.place(x=0, y=0, relwidth=1, relheight=1)
            page.lift()
            page.show()
        elif key == "cal":
            self.pages["cal"].show()
        elif key == "stats":
            self.pages["stats"].show()
        elif key == "contract":
            self.pages["contract"].show()
        elif key == "net":
            self.refresh_net(force=True)
        elif key == "comm":
            self.pages["comm"].show()
        elif key == "log":
            self.pages["log"].show()
        elif key == "settings":
            self.pages["settings"].refresh()
        elif key == "ach":
            self.pages["ach"].show()
        elif key == "reset":
            self.refresh_reset()
        if key != "comm":
            self.comm.set_viewing(False)
        try:
            self.comm.share_page_changed(key)           # share data 打开着: 开始 / 暂停 / 结束分享 (回到沟通 = 结束并总结)
            self._share_sync_ui(True)
        except Exception as e:      # noqa: BLE001
            self._log_window(f"ERROR    share {type(e).__name__}: {e}")
        self.update_log_badge()
        self.update_settings_badge()
        self.update_comm_badge()
