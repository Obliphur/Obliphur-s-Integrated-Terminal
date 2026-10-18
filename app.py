

import sys
import os
import time
import atexit
import threading
import tkinter as tk
from tkinter import font as tkfont, ttk
from datetime import datetime, timedelta, time as dt_time

import config
import i18n
import toast
import window_guard
import devsync
import firewall
import updater
import shortcut
from sync_local import PcLocal
from config import (
    COLORS, APP_NAME, IDLE, RUNNING, READY_BREAK, ON_BREAK,
    ensure_builtin_chime, load_json_file, save_json_file,
    compute_F, fmt_hm, fmt_ms
)
from i18n import tr
from net_guard import NetGuard
from data_manager import DataManager
from settings_manager import SettingsManager
from pages import CalendarPage, HourLogPage, SettingsPage, RulesPage, StatsPage
from pages.widgets import (frozen, LoadingCover, cfg, flavor,
                           show_pack, hide_pack)


def _enable_per_monitor_dpi():
    if sys.platform != "win32":
        return
    try:
        from ctypes import windll
    except Exception:
        return
    try:

        windll.user32.SetProcessDpiAwarenessContext(-4)
        return
    except Exception:
        pass
    try:
        windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _play_audio_file(path: str) -> bool:
    """播放一个本地音频文件, 播完才返回 (在后台线程里调用). 只用标准库.

    .wav 用 winsound; 其他格式 (.mp3 等) 用 Windows 自带的 MCI (winmm.dll).
    播放失败返回 False, 由调用方退回内置提示音."""
    if sys.platform != "win32" or not path or not os.path.exists(path):
        return False
    path = os.path.normpath(os.path.abspath(path))
    if path.lower().endswith(".wav"):
        try:
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME)
            return True
        except Exception:
            return False
    try:
        import ctypes
        import uuid
        mci = ctypes.windll.winmm.mciSendStringW
        buf = ctypes.create_unicode_buffer(256)
        alias = "oit_chime_" + uuid.uuid4().hex[:8]
        if mci(f'open "{path}" type mpegvideo alias {alias}', buf, 255, 0) != 0:
            return False
        try:
            return mci(f"play {alias} wait", buf, 255, 0) == 0
        finally:
            mci(f"close {alias}", buf, 255, 0)
    except Exception:
        return False


class BreakTimerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.shutting_down = False
        self._rebuilding = False

        _enable_per_monitor_dpi()
        self.ui_scale = config.detect_ui_scale()
        config.UI_SCALE = self.ui_scale
        if not (root.winfo_exists() and root.winfo_width() > 1):
            pass

        root.title(APP_NAME)
        root.resizable(False, False)
        self.style = ttk.Style(root)


        self.state = IDLE
        self.start_mono = None
        self.start_wall = None
        self.break_start_wall = None
        self.idle_since = None        # 休息结束、回到空闲的时刻 (提醒用)
        self._nudges = 0              # 这段空闲里已经到点的提醒次数
        self._nudge_win = None
        self._bonus_clicks = 0        # 小按钮连点计数
        self._bonus_first = 0.0
        self._bonus_job = None
        self._timer_extra = None
        self._bonus_break_active = False   # 这一次休息含领到的时间: 可在联网中暂停, 暂停不打折
        self._focus_checks = 0        # 这一段专注里已经被手动关掉的"还在吗"提醒数
        self._focus_ok = 0.0          # 最后一次确认 (关掉提醒窗) 时已专注的秒数
        self._check_win = None        # 当前开着的"还在吗"提醒窗
        self._focus_saved = 0.0       # 上一次把专注状态写进文件的时刻 (monotonic)
        # 绑定了其他设备, 上次在共享的专注中 OIT 被关掉了: 等第一次同步后决定要不要终止这段专注 (MECHANICS 8.2)
        self._close_pending = None
        self._relaying = False        # 正在给其他设备下载 / 发送安装包
        self._relayed = set()         # (设备, 版本): 这次运行里已经发过的
        self._awake = False           # 正在阻止系统睡眠 (专注 / 休息中, 屏幕可以照常关掉)
        self.last_t_minutes = 0.0
        self.countdown_seconds = 0
        self.update_job = None
        self.midnight_job = None
        self.hourly_job = None
        self.net_job = None
        self.window_job = None        # 任务栏窗口数上限的检查 (与界面无关, 重建界面时不中断)
        self.window_focus_job = None  # 窗口使用时间统计: 每隔几秒看一次前台程序
        self._window_focus_ticks = 0
        self.app_rules_job = None     # Rules 页的应用名单: 每秒看一次前台窗口
        # 设备同步 (MECHANICS 15): 计时器此刻的状态是几台设备共用的
        self.timer_owner = ""         # 最后改动计时器状态的设备 (空 = 本机)
        self.break_end_wall = None    # 这次休息结束的时刻 (墙上时间, 同步用)
        self.last_op = time.time() * 1000   # 本机最后一次有人操作的时刻 (毫秒)
        self._app_rules_toast = {}    # exe -> 上次提示的时刻 (同一个程序 5 秒内只提示一次)
        self.next_hour = None
        self.current_page = "timer"



        self.dm = DataManager()
        self.timer_file = os.path.join(self.dm.data_dir, "timer_state.json")
        self.break_carry = self._load_break_carry()
        self.settings = SettingsManager()
        # 第一次打开软件 (还没有设置文件): 开始 72 小时配置期, 界面建好后弹出介绍
        self._first_run = self.settings.fresh
        if self._first_run:
            self.settings.start_setup()
        self._setup_was_active = self.settings.in_setup()
        i18n.init_from_config()
        config.UI_SCALE = self.ui_scale * float(self.settings.value("FONT_SCALE") or 1.0)
        self._apply_theme()
        self.dm.hour_enabled = bool(self.settings.value("HOUR_LOG_ENABLED"))
        self.net = NetGuard(self.dm.data_dir)
        # 设备同步 (MECHANICS 15): 其他设备的账并进额度和统计
        self.sync_runner = devsync.MainRunner()
        self.sync = devsync.SyncService(self.dm.data_dir, PcLocal(self), self.sync_runner)
        self.dm.peer_ledgers = self.sync.peer_ledgers
        self.dm.offline_spans = self.sync.offline_spans     # 正在进行的那段: 有设备离线的部分画成空白
        self.dm.peer_ledgers_by_id = self.sync.peer_ledgers_by_id
        self.dm.my_id = lambda: self.sync.my_id
        self.dm.mother_id = self.sync.mother_id
        self.net.peer_net = lambda: devsync.peer_net(self.sync.peer_ledgers(), self.net.date)
        self.net.peer_usage = lambda: [r for led in self.sync.peer_ledgers()
                                       for r in (led.get("usage") or [])]
        self.sync_job = None
        self.updater = updater.Updater()
        self._update_retry = False     # 打开时检查失败了 (例如 Adapter 离线): 下一次联网时再查
        self._net_view = None
        self._net_last = time.monotonic()
        self._apply_notice = None
        self.var_min = tk.StringVar(value="15")
        self.var_reason = tk.StringVar()
        self.var_confirm = tk.StringVar()
        self.var_loan = tk.BooleanVar(value=False)

        toast.bind_root(root)
        # 先定好窗口大小, 再在加载页后面一页一页地建界面
        self._apply_geometry()
        cover = LoadingCover(root, tr("正在加载…"), "", steps=self.LAYOUT_STEPS).show()
        try:
            self._build_layout(root, cover)
            self.show_page("timer")
            self.refresh_display()
            self.update_log_badge()
            self.update_settings_badge()
        finally:
            with frozen(root):
                cover.close()

        self._start_midnight_timer()
        self._schedule_hourly()
        self._schedule_apply_check()
        self._net_tick()
        self.window_job = self.root.after(5000, self._schedule_window_guard)
        self.window_focus_job = self.root.after(self._window_focus_ms(), self._window_focus_tick)
        self.app_rules_job = self.root.after(1000, self._schedule_app_rules)
        try:
            self.sync.start()
        except Exception as e:
            self._log_window(f"ERROR    sync start {type(e).__name__}: {e}")
        self._pump_sync()
        # 防火墙放行同步端口, 否则别的设备找不到本机 / 连不进来 (MECHANICS 15.3). netsh 较慢, 放到后台
        threading.Thread(target=self._check_firewall, daemon=True).start()
        if getattr(config, "UPDATE_CHECK", 1):
            self.root.after(3000, lambda: self.check_update(auto=True))

        self.root.bind("<Map>", self._on_window_map)
        self.root.bind_all("<ButtonPress>", self._touch, add="+")
        self.root.bind_all("<KeyPress>", self._touch, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        # 关机 / 注销时 Windows 会先通知窗口; 程序因异常退出时 atexit 也会跑. 都先存下专注状态
        self.root.protocol("WM_SAVE_YOURSELF", self._save_focus_now)
        atexit.register(self._save_focus_now)
        self.var_confirm.trace_add("write", self._on_confirm_change)
        self._recover_focus()
        if self._first_run:
            self.root.after(600, self._show_welcome)


    def _apply_geometry(self):
        w = config.scaled(config.WINDOW_W)
        h = config.scaled(config.WINDOW_H)
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            w = min(w, int(sw * 0.96))
            h = min(h, int(sh * 0.92) - 40)
        except Exception:
            pass
        self.root.geometry(f"{w}x{h}")
        self.root.minsize(config.scaled(config.MIN_WINDOW_W), config.scaled(config.MIN_WINDOW_H))
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                f = tkfont.nametofont(name)
                f.configure(family=config.FONT_FAMILY, size=config.scaled(config.FONT_SIZE["body"]))
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

    def _apply_tk_options(self):
        """没有单独指定颜色的 tk 控件 (列表框, 多行文本, 下拉框弹出列表) 的默认色."""
        t, r = config.TTK_COLORS, self.root
        for cls in ("Listbox", "Text"):
            r.option_add(f"*{cls}.background", t["list_bg"], "interactive")
            r.option_add(f"*{cls}.foreground", t["list_fg"], "interactive")
            r.option_add(f"*{cls}.selectBackground", t["list_select"], "interactive")
            r.option_add(f"*{cls}.selectForeground", t["list_on_select"], "interactive")
            r.option_add(f"*{cls}.highlightColor", COLORS["accent"], "interactive")
        r.option_add("*Text.insertBackground", t["list_fg"], "interactive")
        r.option_add("*Button.highlightThickness", 0, "interactive")
        r.option_add("*TCombobox*Listbox.background", t["list_bg"], "interactive")
        r.option_add("*TCombobox*Listbox.foreground", t["list_fg"], "interactive")
        r.option_add("*TCombobox*Listbox.selectBackground", t["list_select"], "interactive")
        r.option_add("*TCombobox*Listbox.selectForeground", t["list_on_select"], "interactive")

    def _apply_titlebar(self):
        """Windows 10/11: 深色主题时让标题栏也变成深色."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            dark = ctypes.c_int(1 if config.THEME.endswith("_dark") else 0)
            for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (新 / 旧版本号)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(dark), ctypes.sizeof(dark)) == 0:
                    break
        except Exception:
            pass

    def _theme_ttk(self):
        s = self.style
        bg, card = COLORS["bg"], COLORS["bg_card"]
        ac, tx = COLORS["accent"], COLORS["text"]
        td = COLORS["text_dim"]
        on_ac = COLORS["on_accent"]
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
        s.map("TCombobox",
              fieldbackground=[("readonly", "focus", t["select"]),
                               ("readonly", t["frame"]), ("disabled", t["frame"])],
              foreground=[("readonly", "focus", t["on_select"]),
                          ("disabled", t["disabled_fg"])],
              background=[("active", t["frame_hover"]), ("pressed", t["frame_hover"])])
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
        s.configure("TopBar.TFrame", background=COLORS["accent"])

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
            background=ac, foreground=on_ac,
            bordercolor=ac, lightcolor=ac, darkcolor=ac,
            font=config.font("body", bold=True),
            borderwidth=0, focusthickness=0, padding=(18, 10)
        )
        s.map(
            "Accent.TButton",
            background=[("disabled", COLORS["accent_disabled"]),
                        ("active", COLORS["accent_hover"])],
            foreground=[("disabled", COLORS["on_accent_disabled"])],
            bordercolor=[("disabled", COLORS["accent_disabled"]),
                         ("active", COLORS["accent_hover"])],
            lightcolor=[("disabled", COLORS["accent_disabled"]),
                        ("active", COLORS["accent_hover"])],
            darkcolor=[("disabled", COLORS["accent_disabled"]),
                       ("active", COLORS["accent_hover"])]
        )

        s.configure("UI.TSeparator", background=COLORS["border"])
        s.configure(
            "Quota.Horizontal.TProgressbar",
            troughcolor=COLORS["border"], background=ac,
            bordercolor=COLORS["border"], lightcolor=ac, darkcolor=ac,
            borderwidth=0, thickness=8
        )
        dg = COLORS["danger"]
        s.configure(
            "Danger.TButton",
            background=dg, foreground=on_ac,
            bordercolor=dg, lightcolor=dg, darkcolor=dg,
            font=config.font("body", bold=True),
            borderwidth=0, focusthickness=0, padding=(18, 10)
        )
        s.map(
            "Danger.TButton",
            background=[("disabled", COLORS["danger_disabled"]),
                        ("active", COLORS["danger_hover"])],
            foreground=[("disabled", on_ac)],
            bordercolor=[("disabled", COLORS["danger_disabled"]),
                         ("active", COLORS["danger_hover"])],
            lightcolor=[("disabled", COLORS["danger_disabled"]),
                        ("active", COLORS["danger_hover"])],
            darkcolor=[("disabled", COLORS["danger_disabled"]),
                       ("active", COLORS["danger_hover"])]
        )


    # _layout_steps 每建好一部分 yield 一次 (供加载页前进进度条); 次数 = LAYOUT_STEPS
    LAYOUT_STEPS = 9

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

        self.topbar = tk.Frame(parent, bg=COLORS["accent"], height=4)
        self.topbar.pack(fill="x")

        self.shell = tk.Frame(parent, bg=bg)
        self.shell.pack(fill="both", expand=True)


        side = tk.Frame(self.shell, bg=card, highlightbackground=COLORS["border"],
                        highlightthickness=1)
        side.pack(side="left", fill="y")
        self.sidebar = side
        tk.Frame(side, bg=card, height=14).pack()

        self.nav_btns = {}


        self.nav_labels = {
            "timer": tr("⏱  计时器"),
            "net": tr("🌐  联网"),
            "rules": tr("🛡  规则"),
            "log": tr("📝  整点记录"),
            "cal": tr("📅  日历"),
            "stats": tr("📊  统计"),
            "settings": tr("⚙  设置"),
            "reset": tr("🔄  重置")
        }
        for key, label in self.nav_labels.items():
            if key == "reset":

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
        yield

        # 只有当前页挂在界面上 (grid), 其余页 grid_remove:
        # 看不见的页面不参与布局和重画, 每秒的刷新也不会去画它们.
        self.pages["cal"] = CalendarPage(self.content, self.dm)
        self._prepare_page(self.pages["cal"])
        yield
        self.pages["stats"] = StatsPage(self.content, self.dm, self.net,
                                        focus_live=self._focus_live)
        self._prepare_page(self.pages["stats"])
        yield
        self.pages["log"] = HourLogPage(
            self.content, self.dm,
            on_text_changed=self.update_log_badge,
            on_toggle=self.on_hour_log_toggle,
            on_open_settings=lambda: self.show_page("settings")
        )
        self._prepare_page(self.pages["log"])
        yield
        self.pages["settings"] = SettingsPage(
            self.content, self.settings,
            on_changed=self.on_settings_changed, on_sound=self.play_chime,
            on_dev_mode=self.on_dev_toggled,
            on_language=self.on_language_changed,
            on_font_scale=self.on_font_scale_changed,
            on_theme=self.on_theme_changed,
            on_end_setup=self.end_setup_period,
            updater=self.updater,
            on_update_check=lambda: self.check_update(auto=False),
            on_update_install=self.install_update,
            on_update_relay=lambda: self.relay_updates(manual=True),
            sync=self.sync
        )
        self._prepare_page(self.pages["settings"])
        yield
        self.pages["rules"] = RulesPage(
            self.content, self.net.proxy, settings=self.settings, sync=self.sync,
            on_changed=self.on_settings_changed,
            on_open_settings=lambda: self.show_page("settings")
        )
        self._prepare_page(self.pages["rules"])
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
        self.pages = {}
        self.nav_btns = {}
        self._apply_geometry()
        self._build_layout(self.root, cover)
        self.show_page(self.current_page or "timer")

        self.refresh_display()
        self.update_log_badge()
        self.update_settings_badge()
        self._start_midnight_timer()
        self._schedule_hourly()
        self._restore_timer_view()
        self._schedule_apply_check()
        self._net_tick()

    def on_language_changed(self):
        i18n.init_from_config()
        self.rebuild_ui()

    def on_theme_changed(self):
        self.rebuild_ui()

    def on_font_scale_changed(self):
        self.ui_scale = config.detect_ui_scale()
        base = float(self.settings.value("FONT_SCALE") or 1.0)
        config.UI_SCALE = self.ui_scale * base
        self.rebuild_ui()


    def _cancel_jobs(self):
        self.shutting_down = True
        for job in (self.midnight_job, self.hourly_job, self.update_job,
                    self.net_job, getattr(self, "apply_job", None),
                    getattr(self, "window_job", None),
                    getattr(self, "window_focus_job", None),
                    getattr(self, "app_rules_job", None),
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

    def show_page(self, key: str):
        # 先在冻结状态下刷新目标页、再置顶, 最后整窗一次性显示 —— 避免切换时的撕裂
        with frozen(self.root):
            self._show_page(key)

    def _show_page(self, key: str):
        self.current_page = key
        for k, b in self.nav_btns.items():
            if k == key:
                cfg(b,
                    bg=COLORS["accent"], fg=COLORS["on_accent"],
                    activebackground=COLORS["accent_hover"],
                    activeforeground=COLORS["on_accent"]
                )
            else:
                cfg(b,
                    bg=COLORS["bg_card"], fg=COLORS["text"],
                    activebackground=COLORS["border"], activeforeground=COLORS["text"]
                )
        # 只把目标页挂到界面上, 其余页收起 (不参与布局 / 重画)
        target = self.pages[key]
        if target.winfo_manager() != "grid":
            target.grid(row=0, column=0, sticky="nsew")
        for k, f in self.pages.items():
            if k != key and f.winfo_manager() == "grid":
                f.grid_remove()
        if key == "cal":
            self.pages["cal"].show()
        elif key == "stats":
            self.pages["stats"].show()
        elif key == "net":
            self.refresh_net(force=True)
        elif key == "rules":
            self.pages["rules"].refresh(force=True)
        elif key == "log":
            self.pages["log"].show()
        elif key == "settings":
            self.pages["settings"].refresh()
        elif key == "reset":
            self.refresh_reset()
        self.update_log_badge()
        self.update_settings_badge()


    def _build_timer_page(self, body):
        bg, card = COLORS["bg"], COLORS["bg_card"]

        hdr = tk.Frame(body, bg=bg)
        hdr.pack(fill="x", padx=28, pady=(22, 8))
        tk.Label(
            hdr, text=tr("⏱  Focus Timer"), bg=bg, fg=COLORS["text"],
            font=config.font("h1", bold=True)
        ).pack(anchor="w")

        ttk.Separator(body, orient="horizontal", style="UI.TSeparator").pack(fill="x", padx=26)

        card_fr = tk.Frame(
            body, bg=card, highlightbackground=COLORS["border"],
            highlightthickness=1, relief="solid", bd=0
        )
        card_fr.pack(padx=26, pady=20, fill="x")
        inner = tk.Frame(card_fr, bg=card)
        inner.pack(padx=22, pady=18, fill="both")

        status_row = tk.Frame(inner, bg=card)
        status_row.pack(anchor="w", pady=(0, 12))
        self.dot_canvas = tk.Canvas(status_row, width=12, height=12, bg=card, highlightthickness=0)
        self.dot_canvas.pack(side="left", padx=(0, 8))
        self.dot_id = self.dot_canvas.create_oval(1, 1, 11, 11, fill=COLORS["text_dim"], outline="")
        self.lbl_status = tk.Label(
            status_row, text=tr("准备就绪"), bg=card, fg=COLORS["text_dim"],
            font=config.font("body")
        )
        self.lbl_status.pack(side="left")

        self.lbl_time = tk.Label(
            inner, text="0.00 min", bg=card, fg=COLORS["text"],
            font=config.font("big", bold=True), anchor="w"
        )
        self.lbl_time.pack(anchor="w", pady=(2, 6))

        self.lbl_sub = tk.Label(
            inner, text=self._idle_sub_text(),
            bg=card, fg=COLORS["text_dim"], font=config.font("body"), anchor="w",
            justify="left"
        )
        self.lbl_sub.pack(anchor="w")

        stats_frame = tk.Frame(inner, bg=card)
        stats_frame.pack(anchor="w", pady=(14, 0), fill="x")
        self.lbl_today = tk.Label(
            stats_frame, text=tr("今日: {h:.1f} h").format(h=0.0), bg=card, fg=COLORS["accent"],
            font=config.font("body", bold=True)
        )
        self.lbl_today.pack(side="left", padx=(0, 30))
        self.lbl_yesterday = tk.Label(
            stats_frame, text=tr("昨日: {h:.1f} h").format(h=0.0), bg=card,
            fg=COLORS["text_dim"], font=config.font("body")
        )
        self.lbl_yesterday.pack(side="left")
        # 绑定的其他设备此刻的状态 (MECHANICS 15)
        self.lbl_peers = tk.Label(inner, text="", bg=card, fg=COLORS["text_dim"],
                                  font=config.font("small"), anchor="w", justify="left")
        self.lbl_peers.pack(anchor="w", pady=(6, 0))

        btn_row = tk.Frame(body, bg=bg)
        btn_row.pack(fill="x", padx=26, pady=(6, 22))
        btns = tk.Frame(btn_row, bg=bg)
        btns.pack()
        self.btn_action = ttk.Button(
            btns, text=tr("▶  开始专注"), style="Accent.TButton",
            command=self.on_action
        )
        self.btn_action.pack(side="left", ipadx=10)
        self.btn_break = ttk.Button(
            btns, text=tr("☕  开始休息"), command=self.on_break_button
        )
        self.btn_break.pack(side="left", ipadx=10, padx=(10, 0))

        # 连点按钮: 1 分钟内连点 10 次, 换 20 分钟休息时间 (每周最多 2 小时)
        self.lbl_bonus = ttk.Button(btns, text="", command=self._on_bonus_click)
        self.lbl_bonus.pack(side="left", padx=(24, 0))
        self._bonus_show(self._bonus_idle_text())

        # 作者的话 (MECHANICS.md 6.2): 一直显示在页面下方的空白处
        flavor(body, tr("在第二段区间内 学的时间越长得到的休息时间越长.请尽量长时间地专注."), bg=bg).pack(
            side="bottom", fill="x", padx=26, pady=(0, 22))
        self._sync_timer_buttons()

        self._set_dot(COLORS["text_dim"])

    def _set_dot(self, color_hex: str):
        self.dot_canvas.itemconfig(self.dot_id, fill=color_hex)

    def _status(self, text: str, dot_color: str):
        self.lbl_status.config(text=text)
        self._set_dot(dot_color)

    def on_action(self):
        """主按钮: 空闲 → 开始专注; 专注中 → 结束专注 (直接进入休息);
        休息中 → 暂停休息并马上开始专注."""
        self._mine()
        if self.state == IDLE:
            self._enter_focus(datetime.now(), time.monotonic())

        elif self.state == RUNNING:
            end_wall = datetime.now()
            elapsed_minutes = (time.monotonic() - self.start_mono) / 60.0
            self.last_t_minutes = elapsed_minutes      # 建议休息时长照常按整段专注算
            off = self._cut_offline(self.start_wall, end_wall)
            self.dm.add_study_session(self.start_wall, end_wall, max(0.0, elapsed_minutes - off / 60.0))
            self.dm.add_focus_session("study", self.start_wall, end_wall)
            self._close_check_win()
            self.state = READY_BREAK          # 专注已记下: 先清掉文件里的专注状态
            self._save_timer_state()
            self.refresh_display()

            self.cancel_update()
            # 结束专注后直接进入休息, 不再停在"去休息"这一步
            self._start_break(announce=True)

        elif self.state == READY_BREAK:
            # 旧流程留下的状态 (正常不会再进入): 按一下照样开始休息
            self._start_break()

        elif self.state == ON_BREAK:
            if self._break_locked():
                toast.warn(tr("网络正在使用,不能暂停休息."))
                return
            self.end_break_early()
            if self.state == IDLE:
                self.on_action()

    def on_break_button(self):
        """副按钮: 空闲 → 用积攒的休息时间直接开始休息; 休息中 → 暂停休息."""
        self._mine()
        if self.state == IDLE:
            if self.break_carry <= 0:
                toast.info(tr("还没有积攒的休息时间."))
                return
            self.last_t_minutes = 0.0
            total = int(self.break_carry)
            self._start_break()
            if self.state == ON_BREAK:
                toast.success(tr("开始休息 {t}.").format(t=fmt_ms(total)))
        elif self.state == ON_BREAK:
            self.end_break_early()

    def _sync_timer_buttons(self):
        """按当前状态设置两个按钮的文字与可用状态."""
        if not hasattr(self, "btn_break"):
            return
        locked = self._break_locked()
        try:
            if self.state == RUNNING:
                self.btn_action.config(text=tr("■  结束专注"), state="!disabled")
                self.btn_break.config(text=tr("☕  开始休息"), state="disabled")
            elif self.state == ON_BREAK:
                st = "disabled" if locked else "!disabled"
                self.btn_action.config(text=tr("▶  开始专注"), state=st)
                self.btn_break.config(text=tr("⏸  暂停休息"), state=st)
            elif self.state == READY_BREAK:
                self.btn_action.config(text=tr("☕  去休息"), state="!disabled")
                self.btn_break.config(text=tr("☕  开始休息"), state="disabled")
            else:
                self.btn_action.config(text=tr("▶  开始专注"), state="!disabled")
                self.btn_break.config(text=tr("☕  开始休息"),
                                      state="!disabled" if self.break_carry > 0 else "disabled")
        except Exception:
            pass

    # ---- 连点 10 次换休息时间 ----

    def _bonus_state(self) -> dict:
        d = load_json_file(self.timer_file, {})
        return d if isinstance(d, dict) else {}

    def _bonus_left_this_week(self, now: datetime = None) -> int:
        """本周 (周一 0 点起) 还能领多少秒."""
        now = now or datetime.now()
        week = "%d-W%02d" % now.isocalendar()[:2]
        d = self._bonus_state()
        used = int(d.get("bonus_used", 0) or 0) if d.get("bonus_week") == week else 0
        return max(0, int(config.BONUS_BREAK_WEEKLY) - used)

    def _on_bonus_click(self):
        now = time.monotonic()
        window = float(config.BONUS_BREAK_WINDOW)
        if self._bonus_clicks == 0 or now - self._bonus_first > window:
            # 从第一次点击开始计时; 超过 1 分钟还没点满就从头再来
            self._bonus_clicks = 0
            self._bonus_first = now
            if self._bonus_job:
                try:
                    self.root.after_cancel(self._bonus_job)
                except Exception:
                    pass
            self._bonus_job = self.root.after(int(window * 1000) + 50, self._bonus_reset)
        self._bonus_clicks += 1
        need = int(config.BONUS_BREAK_CLICKS)
        if self._bonus_clicks < need:
            self._bonus_show(tr("☕  我累了 ({i})").format(i=self._bonus_clicks))
            return
        self._bonus_reset()
        self._grant_bonus_break()

    def _bonus_reset(self):
        self._bonus_clicks = 0
        if self._bonus_job:
            try:
                self.root.after_cancel(self._bonus_job)
            except Exception:
                pass
        self._bonus_job = None
        self._bonus_show(self._bonus_idle_text())

    def _bonus_idle_text(self) -> str:
        # 按钮上只写几个字, 不写具体规则 (规则见 MECHANICS.md 6.5)
        if self._bonus_left_this_week() <= 0:
            return tr("☕  本周已领完")
        return tr("☕  我累了")

    def _bonus_show(self, text: str):
        try:
            self.lbl_bonus.config(text=text)
        except Exception:
            pass

    def _grant_bonus_break(self):
        now = datetime.now()
        left = self._bonus_left_this_week(now)
        if left <= 0:
            toast.warn(tr("这一周的 {h} 已经领完了,下周一再来.").format(
                h=fmt_hm(config.BONUS_BREAK_WEEKLY)))
            return
        add = min(int(config.BONUS_BREAK_SECONDS), left)
        week = "%d-W%02d" % now.isocalendar()[:2]
        d = self._bonus_state()
        used = int(d.get("bonus_used", 0) or 0) if d.get("bonus_week") == week else 0
        self._timer_extra = {"bonus_week": week, "bonus_used": used + add}
        self._mine()
        if self.state == ON_BREAK:
            self.countdown_seconds += add          # 正在休息: 直接加到这次休息里
            if self.break_end_wall is not None:
                self.break_end_wall += timedelta(seconds=add)
            self._bonus_break_active = True
            self._save_timer_state()
            where = tr("已加到这次休息里.")
            where += "\n" + tr("这次休息可以随时暂停,剩余时间不打折.")
        else:
            # 存进积攒的休息时间; 用到它的下一次休息享受"可暂停、不打折"
            self._timer_extra["bonus_pending"] = True
            self._set_break_carry(self.break_carry + add)
            where = tr("已存入积攒的休息时间.")
            where += "\n" + tr("下一次休息可以随时暂停,剩余时间不打折.")
        msg = tr("获得 {t} 休息时间.").format(t=fmt_ms(add)) + where
        msg += "\n" + tr("本周还能领 {left}.").format(left=fmt_hm(left - add))
        msg += "\n\n" + tr("这么想休息,也许是真的累了.休息不需要理由.")
        msg += "\n" + tr("不要勉强.")
        toast.success(msg, timeout=10000)
        if self.state == IDLE:
            self.lbl_sub.config(text=self._idle_sub_text())
        self._bonus_show(self._bonus_idle_text())
        self._sync_timer_buttons()

    def _start_break(self, announce: bool = False):
        """开始休息倒计时 = 建议休息时长 + 之前暂停休息结转的时间."""
        total = self._break_seconds()
        if total <= 0:
            toast.info(tr("剩余时间为 0,已重置."))
            self.reset_to_idle()
            return
        carry = int(self.break_carry)
        bonus = bool(self._bonus_state().get("bonus_pending"))
        if bonus:
            self._bonus_break_active = True
            self._timer_extra = {"bonus_pending": False}
        if carry > 0 or bonus:
            self._set_break_carry(0)
        self.countdown_seconds = total
        self.state = ON_BREAK
        self.break_start_wall = datetime.now().replace(microsecond=0)
        self.break_end_wall = self.break_start_wall + timedelta(seconds=total)
        if bonus:
            toast.info(tr("这次休息可以随时暂停,剩余时间不打折."),
                       timeout=8000)
        self._show_on_break()
        self.schedule_update()
        if announce:
            msg = tr("本次专注 {m:.1f} 分钟,开始休息 {t}.").format(
                m=self.last_t_minutes, t=fmt_ms(total))
            if carry > 0:
                msg += "\n" + tr("(含之前暂停休息结转的 {c})").format(c=fmt_ms(carry))
            toast.success(msg)

    # ---- 休息: 建议时长 + 之前暂停休息结转的时间 ----

    def _break_seconds(self) -> int:
        """本次休息总秒数 = 建议休息时长 + 结余."""
        fval = max(0.0, compute_F(self.last_t_minutes)) * float(config.BREAK_TIME_SCALE)
        base = int(round(fval * 60.0))
        total = base + int(self.break_carry)
        return max(1, total) if total > 0 else 0

    def _show_ready_break(self):
        self._sync_timer_buttons()
        total = self._break_seconds()
        self.lbl_time.config(text=fmt_ms(total))
        sub = tr("已工作 {m:.2f} 分钟").format(m=self.last_t_minutes)
        if self.break_carry > 0:
            sub += "\n" + tr("(含之前暂停休息结转的 {c})").format(c=fmt_ms(self.break_carry))
        self.lbl_sub.config(text=sub)
        self._status(tr("本次专注结束"), COLORS["success"])

    def _show_on_break(self):
        self._sync_timer_buttons()
        self._status(tr("休息中 ☕"), COLORS["warn"])
        self.lbl_sub.config(text=tr("休息吧,倒计时中…"))
        self._sync_break_lock()

    def _break_locked(self) -> bool:
        """网络正在使用时不允许暂停休息 —— 领到休息时间后的第一次休息除外."""
        return (self.state == ON_BREAK and self.net.mode == "online"
                and not self._bonus_break_active)

    def _sync_break_lock(self):
        if self.state == ON_BREAK:
            self._sync_timer_buttons()

    def end_break_early(self, silent: bool = False):
        """暂停休息(关闭程序也算):剩余时间 ×BREAK_CARRY_RATE 计入下一次休息."""
        if self.state != ON_BREAK:
            return
        if not silent and self._break_locked():
            toast.warn(tr("网络正在使用,不能暂停休息."))
            return
        if silent and not self._timer_auto_ok():
            self.break_start_wall = None
        self._record_break_end()
        left = max(0, int(self.countdown_seconds))
        full = self._bonus_break_active
        self._bonus_break_active = False
        # 领到休息时间后的第一次休息: 暂停时剩余时间全部保留, 不按倍率打折
        add = left if full else int(round(left * config.BREAK_CARRY_RATE))
        if add > 0:
            self._set_break_carry(self.break_carry + add)
            if not silent:
                if full:
                    toast.info(tr("休息已暂停,剩余 {left} 留给下次.").format(
                        left=fmt_ms(left)))
                else:
                    toast.info(tr("休息已暂停,下次休息 +{add}.").format(
                        left=fmt_ms(left), r=config.BREAK_CARRY_RATE, add=fmt_ms(add)))
        if silent:
            self.state = IDLE
            self.countdown_seconds = 0
        else:
            self.reset_to_idle()
            self._arm_idle_nudge()

    def _record_break_end(self):
        """一段休息结束 (自然结束 / 暂停 / 关闭程序): 记入专注计时统计."""
        if self.break_start_wall is not None:
            self.dm.add_focus_session("break", self.break_start_wall, datetime.now())   # 有设备离线也照常记
            self.break_start_wall = None

    # ---- 休息结束后迟迟不开始: 提醒 ----

    def _arm_idle_nudge(self):
        """休息结束 (自然结束或暂停) 回到空闲时调用, 从此刻开始计时."""
        self.idle_since = datetime.now()
        self._nudges = 0

    def _check_idle_nudge(self):
        """每 5 秒检查一次. 第一次在空闲 IDLE_NUDGE_FIRST 秒后, 之后每 IDLE_NUDGE_EVERY 秒一次;
        按墙上时间算, 电脑睡眠醒来后错过的几次只补弹一次."""
        if self.idle_since is None:
            return
        if self.state != IDLE:
            self.idle_since = None          # 已经开始下一轮 (或别的状态), 不再提醒
            return
        if not self.sync.is_latest_active():
            return                          # 只在最后有人操作的设备上提醒
        now = datetime.now()
        first = timedelta(seconds=max(1, int(config.IDLE_NUDGE_FIRST)))
        every = timedelta(seconds=max(60, int(config.IDLE_NUDGE_EVERY)))
        due = self.idle_since + first + every * self._nudges
        if now < due:
            return
        while due <= now:
            self._nudges += 1
            due += every
        if self._nudge_win is not None and self._nudge_win.alive():
            return                          # 上一个提醒还开着, 不重复弹
        idle = fmt_hm((now - self.idle_since).total_seconds())
        self._nudge_win = toast.notice(
            tr("⏱  该开始下一轮了"),
            tr("休息结束已经 {t} 了,还没有开始下一轮专注.").format(t=idle),
            [(tr("▶  开始专注"), self._start_from_nudge, True), (tr("知道了"), None, False)])

    def _start_from_nudge(self):
        if self.state == IDLE:
            self.show_page("timer")
            self.on_action()

    def _focus_live(self):
        """正在进行的一段, 供 Statistics 页画到此刻: (kind, 开始时刻) 或 None."""
        if self.state == RUNNING and self.start_wall is not None:
            return ("study", self.start_wall)
        if self.state == ON_BREAK and self.break_start_wall is not None:
            return ("break", self.break_start_wall)
        return None

    def _idle_sub_text(self) -> str:
        if self.break_carry > 0:
            return tr("积攒的休息时间: {c}").format(
                c=fmt_ms(self.break_carry))
        return ""

    def _load_break_carry(self) -> int:
        d = load_json_file(self.timer_file, {})
        try:
            return max(0, int(d.get("break_carry", 0))) if isinstance(d, dict) else 0
        except (TypeError, ValueError):
            return 0

    def _set_break_carry(self, seconds: int):
        self.break_carry = max(0, int(seconds))
        self._save_timer_state()

    def _save_timer_state(self):
        """timer_state.json: 积攒的休息时间 + 本周连点领取的记录 + 进行中的专注."""
        if self.dm.resetting:
            return
        d = self._bonus_state()
        d.update(getattr(self, "_timer_extra", None) or {})
        self._timer_extra = None
        d["break_carry"] = self.break_carry
        d["focus"] = self._focus_record()
        self._focus_saved = time.monotonic()
        save_json_file(self.timer_file, d)

    # ---- 专注状态: 随时写进文件, 程序异常退出也不丢 ----

    def _focus_record(self):
        """进行中的专注: 开始时刻, 最后一次保存的时刻, 已关掉的提醒数, 最后一次确认时已专注的秒数."""
        if self.state != RUNNING or self.start_wall is None:
            return None
        return {"start": self.start_wall.isoformat(timespec="seconds"),
                "alive": datetime.now().isoformat(timespec="seconds"),
                "checks": int(self._focus_checks),
                "ok": int(self._focus_ok),
                # 重开时原样恢复, 同步的计时器状态才不会因为"主人变了"被当成新的改动 (盖掉其他设备更新的状态)
                "owner": self.timer_owner}

    def _save_focus_now(self):
        self.dm.save_window_usage()
        if self.state == RUNNING and not self.dm.resetting:
            try:
                self._save_timer_state()
            except Exception:
                pass

    def _enter_focus(self, start_wall: datetime, start_mono: float, checks: int = 0,
                     ok: float = 0.0):
        self.start_wall = start_wall
        self.start_mono = start_mono
        self._focus_checks = checks
        self._focus_ok = ok
        self._close_check_win()
        self.state = RUNNING
        self._status(tr("专注中…"), COLORS["accent"])
        self.lbl_sub.config(text="")
        self._sync_timer_buttons()
        self._save_timer_state()
        self.schedule_update()

    def _recover_focus(self):
        """启动时: 上次退出时还在专注 → 不久前退出就接着计时, 否则记到最后一次保存为止."""
        rec = self._bonus_state().get("focus")
        if not rec:
            return
        try:
            start = datetime.fromisoformat(rec["start"])
            alive = datetime.fromisoformat(rec["alive"])
            checks = max(0, int(rec.get("checks", 0)))
            ok = max(0.0, float(rec.get("ok", 0)))
        except Exception:
            self._save_timer_state()          # 记录坏了: 丢掉
            return
        now = datetime.now()
        if start > now:                        # 系统时间被往回调过: 无法判断, 丢掉
            self._save_timer_state()
            return
        alive = min(max(alive, start), now)
        gap = (now - alive).total_seconds()
        self.timer_owner = str(rec.get("owner") or "")
        if self.sync.peers() and gap > float(config.CLOSED_GAP):
            # 绑定了其他设备, 共享的专注中 OIT 被关掉了: 关掉的这段记为空闲 (总统计里优先级最高),
            # 专注照常恢复, 等第一次同步看其他设备是不是已经结束了它, 没有就由本机终止 (_check_close_pending)
            self.dm.add_focus_session("idle", alive, now, focus=start)
            self._enter_focus(start, time.monotonic() - (now - start).total_seconds(), checks, ok)
            self._close_pending = {"start": start, "alive": alive, "t": time.monotonic(),
                                   "ms": int(time.time() * 1000)}
            return
        if gap <= float(config.FOCUS_RESUME_GAP):
            self._enter_focus(start, time.monotonic() - (now - start).total_seconds(), checks, ok)
            toast.info(tr("上次专注时程序退出了.已接着计时(从 {at} 开始).").format(
                at=f"{start:%H:%M}"), timeout=8000)
            return
        # 退出很久了: 专注记到最后一次保存为止, 对应的休息时间存进积攒的休息时间
        sec = (alive - start).total_seconds()
        limit = float(config.FOCUS_CHECK_EVERY) * (checks + 1) + float(config.FOCUS_CHECK_GRACE)
        penalized = sec >= limit
        if penalized:
            sec = limit
        minutes, brk = self._book_focus(start, sec, ok if penalized else None)
        self._set_break_carry(self.break_carry + brk)   # 同时清掉文件里的专注状态
        self.refresh_display()
        if minutes <= 0:
            return
        if penalized:
            ok = min(ok, sec)
            msg = tr("上次专注时程序退出了,专注已按{at}自动结束.").format(
                at=f"{start + timedelta(seconds=sec):%m-%d %H:%M}")
            msg += "\n" + tr("没有确认的部分按×{p:g}计入,共计入{m:.1f}分钟.").format(
                since=f"{start + timedelta(seconds=ok):%H:%M}", seg=fmt_hm(sec - ok),
                p=float(config.FOCUS_CHECK_PENALTY), m=minutes)
        else:
            msg = tr("上次专注时程序退出了,专注记到 {at} 为止: 计入 {m:.1f} 分钟.").format(
                at=f"{alive:%m-%d %H:%M}", m=minutes)
        if brk > 0:
            msg += "\n" + tr("积攒的休息时间 +{t}.").format(t=fmt_ms(brk))
        toast.notice(tr("⏱  上次的专注已记录"), msg)

    def _check_close_pending(self):
        """上次在共享的专注中 OIT 被关掉了 (_recover_focus): 第一次同步之后 (最多等 CLOSED_DECIDE_WAIT 秒),
        这段专注还在进行就终止它 —— 专注记到被关掉的时刻为止, 之后的那段已经记为空闲."""
        p = self._close_pending
        if not p:
            return
        if not self.sync.synced_since(p["ms"]) and \
                time.monotonic() - p["t"] < float(config.CLOSED_DECIDE_WAIT):
            return
        self._close_pending = None
        if self.state != RUNNING or self.start_wall != p["start"]:
            return                             # 其他设备已经结束了这段专注, 本机照着切换过了
        start, alive = p["start"], p["alive"]
        sec = (alive - start).total_seconds()
        limit = float(config.FOCUS_CHECK_EVERY) * (self._focus_checks + 1) + float(config.FOCUS_CHECK_GRACE)
        penalized = sec >= limit
        if penalized:
            sec = limit
        self._close_check_win()
        self.cancel_update()
        minutes, brk = self._book_focus(start, sec, self._focus_ok if penalized else None)
        self._mine()                           # 终止由本机做, 其他设备照着结束
        self.reset_to_idle()
        self._set_break_carry(self.break_carry + brk)
        self.refresh_display()
        self._refresh_if_visible("stats", "cal")
        msg = tr("这台设备上的 OIT 在 {at} 被关掉了, 共享的专注已终止.").format(at=f"{alive:%m-%d %H:%M}")
        msg += "\n" + tr("专注记到被关掉时为止: 计入 {m:.1f} 分钟; 关掉的这段记为空闲.").format(m=minutes)
        if brk > 0:
            msg += "\n" + tr("积攒的休息时间 +{t}.").format(t=fmt_ms(brk))
        toast.notice(tr("⏹  专注已终止"), msg)

    def _end_focus_for_close(self):
        """绑定了其他设备时关闭程序: 共享的专注就此结束 (记到此刻), 不开始休息, 休息时间存进积攒的休息时间."""
        start = self.start_wall
        elapsed = time.monotonic() - self.start_mono
        self._close_check_win()
        self.cancel_update()
        _minutes, brk = self._book_focus(start, elapsed)
        self._mine()
        self.reset_to_idle()
        self.break_carry += brk
        self._save_timer_state()

    def _keep_awake(self):
        """专注 / 休息中不让系统自动睡眠 (屏幕照常可以关掉), 计时和同步不会停."""
        want = self.state in (RUNNING, ON_BREAK)
        if want == self._awake or sys.platform != "win32":
            return
        self._awake = want
        try:
            import ctypes
            # ES_CONTINUOUS | ES_SYSTEM_REQUIRED; 只写 ES_CONTINUOUS = 恢复正常
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if want else 0))
        except Exception:
            pass

    def _book_focus(self, start: datetime, seconds: float, ok=None):
        """记下一段专注, 返回 (计入的分钟数, 对应的休息秒数).
        ok: 没回应"还在吗"被自动结束时, 最后一次确认时已专注的秒数 —— 它之前的照常计,
            之后没有确认的那一段只把前 FOCUS_CHECK_PENALTY 记作专注. 计入的部分在时间上是连续的."""
        seconds = max(0.0, float(seconds))
        if ok is not None:
            ok = min(max(0.0, float(ok)), seconds)
            seconds = ok + (seconds - ok) * float(config.FOCUS_CHECK_PENALTY)
        end = start + timedelta(seconds=seconds)
        minutes = seconds / 60.0
        # 有设备离线的那段记为空闲, 不计入学习时长; 建议休息时长照常按计入前的时长算
        counted = max(0.0, minutes - self._cut_offline(start, end) / 60.0)
        self.dm.add_study_session(start, end, counted)
        self.dm.add_focus_session("study", start, end)
        brk = int(round(max(0.0, compute_F(minutes)) * float(config.BREAK_TIME_SCALE) * 60.0))
        return counted, brk

    def _cut_offline(self, start: datetime, end: datetime) -> float:
        """[start, end] 这段专注里有绑定的设备离线的时间段: 记为空闲 (统计里压过同一段专注), 返回一共多少秒."""
        total = 0.0
        try:
            spans = self.sync.offline_spans(start.timestamp() * 1000, end.timestamp() * 1000)
        except Exception:
            return 0.0
        for a, b in spans:
            self.dm.add_focus_session("idle", datetime.fromtimestamp(a / 1000), datetime.fromtimestamp(b / 1000),
                                      focus=start)
            total += (b - a) / 1000.0
        return total

    # ---- 专注中每 1.5 小时: 还在吗 ----

    def _close_check_win(self):
        w, self._check_win = self._check_win, None
        if w is not None:
            try:
                w.close()
            except Exception:
                pass

    def _check_focus(self):
        """每 5 秒检查一次: 弹出 / 判定"还在吗"提醒, 并定时保存专注状态."""
        if self.state != RUNNING or self.start_mono is None:
            self._close_check_win()
            return
        if self._close_pending:
            return                             # 先等 _check_close_pending 决定这段专注还要不要
        if self._check_win is not None and not self._check_win.alive():
            self._check_win = None             # 被手动关掉了 = 人还在
            self._mine()
            self._focus_checks += 1
            self._focus_ok = time.monotonic() - self.start_mono
            self._save_timer_state()
        every = float(config.FOCUS_CHECK_EVERY)
        grace = float(config.FOCUS_CHECK_GRACE)
        elapsed = time.monotonic() - self.start_mono
        mark = every * (self._focus_checks + 1)
        if elapsed >= mark + grace:
            if self._timer_auto_ok():          # 别的设备是主人时等它结束, 结果同步过来
                self._auto_end_focus(mark + grace)
            return
        if elapsed >= mark and self._check_win is None:
            due = datetime.now() + timedelta(seconds=mark + grace - elapsed)
            self._check_win = toast.notice(
                tr("⏱  还在专注吗?"),
                tr("已经专注 {t} 了.").format(t=fmt_hm(mark)) + "\n"
                + tr("请在{at}之前关掉这个窗口,否则专注会自动结束.").format(
                    at=f"{due:%H:%M}", p=float(config.FOCUS_CHECK_PENALTY)),
                [(tr("还在,继续专注"), None, True), (tr("■  结束专注"), self._end_from_check, False)])
        # 绑定了其他设备时写得勤一些: 被关掉之后才知道是什么时候关的
        every = float(config.FOCUS_ALIVE_EVERY if self.sync.peers() else config.FOCUS_SAVE_EVERY)
        if time.monotonic() - self._focus_saved >= every:
            self._save_timer_state()

    def _end_from_check(self):
        if self.state == RUNNING:
            self.show_page("timer")
            self.on_action()

    def _auto_end_focus(self, seconds: float):
        """提醒窗没人关: 自动结束专注. 不开始休息倒计时 (人不在), 休息时间存进积攒的休息时间."""
        start = self.start_wall
        ok = min(self._focus_ok, seconds)
        self._close_check_win()
        self.cancel_update()
        minutes, brk = self._book_focus(start, seconds, ok)
        self.reset_to_idle()
        self._set_break_carry(self.break_carry + brk)   # 同时清掉文件里的专注状态
        self.lbl_sub.config(text=self._idle_sub_text())
        self._sync_timer_buttons()
        self.refresh_display()
        self._refresh_if_visible("stats", "cal")
        msg = tr("提醒窗{n}分钟内没有被关掉,专注已在{at}自动结束.").format(
            n=int(round(float(config.FOCUS_CHECK_GRACE) / 60)),
            at=f"{start + timedelta(seconds=seconds):%H:%M}")
        msg += "\n" + tr("没有确认的部分按×{p:g}计入,共计入{m:.1f}分钟.").format(
            since=f"{start + timedelta(seconds=ok):%H:%M}", seg=fmt_hm(seconds - ok),
            p=float(config.FOCUS_CHECK_PENALTY), m=minutes)
        if brk > 0:
            msg += "\n" + tr("积攒的休息时间 +{t}.").format(t=fmt_ms(brk))
        toast.notice(tr("⏹  专注已自动结束"), msg)

    def _restore_timer_view(self):
        """界面重建 (换语言 / 字号) 后按当前状态恢复计时器页."""
        self._sync_timer_buttons()
        if self.state == RUNNING:
            self._status(tr("专注中…"), COLORS["accent"])
            self.schedule_update()
        elif self.state == READY_BREAK:
            self._show_ready_break()
        elif self.state == ON_BREAK:
            self._show_on_break()
            self.schedule_update()

    def schedule_update(self):
        self.cancel_update()
        self.update()

    def cancel_update(self):
        if self.update_job:
            self.root.after_cancel(self.update_job)
            self.update_job = None

    def update(self):
        if self.state == RUNNING:
            elapsed = time.monotonic() - self.start_mono
            cur_m = elapsed / 60.0
            self.lbl_time.config(text=f"{cur_m:.2f} min")
            rem = compute_F(cur_m) * float(config.BREAK_TIME_SCALE)
            if rem > 0:
                self.lbl_sub.config(text=tr("预计停止时的休息时长:  {m:.2f} 分钟").format(m=rem))
            else:
                self.lbl_sub.config(text=tr("预计休息时长: 0.00 分钟"))
            self.update_job = self.root.after(200, self.update)

        elif self.state == ON_BREAK:
            if self.countdown_seconds > 0:
                m = self.countdown_seconds // 60
                s = self.countdown_seconds % 60
                self.lbl_time.config(text=f"{m:02d}:{s:02d}")
                sub = tr("休息剩余 {m} 分 {s:02d} 秒").format(m=m, s=s)
                if self._break_locked():
                    sub += "\n" + tr("网络正在使用,不能暂停休息.")
                self.lbl_sub.config(text=sub)
                self.countdown_seconds -= 1
                self.update_job = self.root.after(1000, self.update)
            else:
                if not self._timer_auto_ok():
                    self.break_start_wall = None      # 这一段由开始休息的那台设备记
                self._record_break_end()
                self._bonus_break_active = False
                toast.success(tr("休息结束,可以开始下一轮了!"))
                self.reset_to_idle()
                self._arm_idle_nudge()
        else:
            self.cancel_update()

    def reset_to_idle(self):
        self.cancel_update()
        self.state = IDLE
        self.start_mono = None
        self.start_wall = None
        self.last_t_minutes = 0.0
        self.countdown_seconds = 0
        self._sync_timer_buttons()
        self.lbl_time.config(text="0.00 min")
        self.lbl_sub.config(text=self._idle_sub_text())
        self._status(tr("准备就绪"), COLORS["text_dim"])

    def refresh_display(self):
        self.lbl_today.config(text=tr("今日: {h:.1f} h").format(h=self.dm.get_today_hours()))
        self.lbl_yesterday.config(text=tr("昨日: {h:.1f} h").format(h=self.dm.get_yesterday_hours()))


    def _start_midnight_timer(self):
        self._schedule_midnight()

    def _schedule_midnight(self):
        now = datetime.now()
        next_midnight = datetime.combine(now.date() + timedelta(days=1), dt_time.min)
        remaining = (next_midnight - now).total_seconds()
        if remaining <= 0:
            remaining = 86400.0
        delay_ms = int(remaining * 1000) + 100
        self.midnight_job = self.root.after(delay_ms, self._on_midnight)

    def _on_midnight(self):
        old_date = self.dm.today_date
        self.dm.today_date = datetime.now().strftime('%Y-%m-%d')
        if old_date[:7] != self.dm.today_date[:7]:
            self.dm.prune_study_data()
        # 不清 prompt_slot:昨天 23:00 那格现在显示在今天最上方,仍可填写.
        self.dm.finalize_hours()
        self.dm.save_hour_log()
        self.update_log_badge()
        self.pages["log"].refresh()
        self.refresh_display()
        self._refresh_if_visible("cal", "stats")
        self._schedule_midnight()

    def _refresh_if_visible(self, *keys):
        for key in keys:
            if self.current_page == key and key in self.pages:
                self.pages[key].refresh()

    def _schedule_hourly(self):
        now = datetime.now()
        self.next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        remaining = (self.next_hour - now).total_seconds()
        if remaining <= 0:
            remaining = 3600.0
        delay_ms = int(remaining * 1000) + 100
        self.hourly_job = self.root.after(delay_ms, self._on_hour)

    def _on_hour(self):
        slot = self.next_hour - timedelta(hours=1)
        self._schedule_hourly()
        self.dm.finalize_hours()
        if not self.dm.hour_enabled:
            self.update_log_badge()
            return
        # 绑定了其他设备时, 只有最后有人操作的那台响 (其他设备只标出待填写)
        chime = self._play_chime() if self.sync.is_latest_active() else None
        self._wait_chime(chime, slot)

    def _play_chime(self):
        if sys.platform != "win32":
            try:
                self.root.bell()
            except Exception:
                pass
            return None

        path = self.settings.value("CHIME_WAV") or ""
        if path and not os.path.exists(path):
            path = ""
        builtin = os.path.join(self.dm.data_dir, "chime.wav")
        if not path or path == "__builtin__":
            path = ensure_builtin_chime(builtin) or ""

        def run():
            # 自选文件 (.wav / .mp3) 放不出来时, 退回内置提示音, 再不行就蜂鸣
            if _play_audio_file(path):
                return
            if path != builtin and _play_audio_file(ensure_builtin_chime(builtin) or ""):
                return
            try:
                import winsound
                for freq, ms in ((784, 200), (988, 200), (1319, 450)):
                    winsound.Beep(freq, ms)
            except Exception:
                pass
        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t

    def play_chime(self):
        self._play_chime()

    def _wait_chime(self, t, slot, waited: int = 0):
        # 等提示音放完再提示填写; 自选的音频可能很长, 最多等 10 秒
        if t is not None and t.is_alive() and waited < 10000:
            self.root.after(200, lambda: self._wait_chime(t, slot, waited + 200))
            return
        if not self.dm.hour_enabled:
            return
        self.dm.prompt_slot = slot
        self.update_log_badge()
        if self.current_page == "log":
            self.pages["log"].show()
        else:
            self.pages["log"].refresh()

    def _on_window_map(self, event):
        if hasattr(self, 'dm'):
            expected = datetime.now().strftime('%Y-%m-%d')
            if expected != self.dm.today_date:
                self._on_midnight()
            self.check_pending_settings()


    def _schedule_apply_check(self):
        if self.shutting_down:
            return
        self.check_pending_settings()
        self._check_rule_changes()
        self._check_close_pending()
        self._check_focus()
        self._keep_awake()
        self._check_dev_follow()
        self._maybe_relay()
        self._check_idle_nudge()
        self._check_setup_period()
        self.apply_job = self.root.after(5000, self._schedule_apply_check)

    # ---- 任务栏窗口数上限 (window_guard.py) ----

    @staticmethod
    def _window_check_ms() -> int:
        return int(max(5.0, float(getattr(config, "WINDOW_CHECK_EVERY", 30))) * 1000)

    def _schedule_window_guard(self):
        if self.shutting_down:
            return
        self._check_windows()
        self.window_job = self.root.after(self._window_check_ms(), self._schedule_window_guard)

    def _check_windows(self):
        """超过上限就一次随机关掉多出来的任务栏窗口 (开发者模式下也照常检查)."""
        if not getattr(config, "WINDOW_LIMIT_ENABLED", 0) or self.dm.resetting:
            return
        limit = int(getattr(config, "WINDOW_LIMIT", 4))
        try:
            n, closed = window_guard.enforce(limit)
        except Exception as e:
            self._log_window(f"ERROR    {type(e).__name__}: {e}")
            return
        if not closed:
            return
        lines = []
        for w in closed:
            title = str(w.get("title") or "").strip()
            exe = window_guard.exe_name(w.get("pid") or 0)
            self._log_window(f"CLOSE    {n} > {limit}  {exe or '?'}  {title}")
            if len(title) > 50:
                title = title[:47] + "..."
            if len(lines) < 8:
                lines.append(f"• {title} ({exe})" if exe else f"• {title}")
        if len(closed) > 8:
            lines.append("…")
        toast.warn(tr("窗口超过上限 {x} 个,关闭了:\n{titles}",
                      n=n, x=limit, k=len(closed), titles="\n".join(lines)))

    # ---- 设备同步 (MECHANICS 15) ----

    def _check_firewall(self):
        try:
            self.sync.firewall = firewall.check()
        except Exception as e:
            self.sync.firewall = {"added": [], "blockers": [], "error": f"{type(e).__name__}: {e}"}

    def _pump_sync(self):
        """后台线程交过来的改动在这里 (界面线程) 执行."""
        if self.shutting_down:
            return
        self.sync_runner.pump()
        self.sync_job = self.root.after(100, self._pump_sync)

    # ---- 设备同步: 计时器此刻的状态 (MECHANICS 15.2) ----

    def _touch(self, _e=None):
        self.last_op = time.time() * 1000

    def _mine(self):
        """本机改动了计时器状态: 本机成为这个状态的主人 (自动结束 / 记录由主人做)."""
        self.timer_owner = self.sync.my_id
        self.last_op = time.time() * 1000

    def _timer_auto_ok(self) -> bool:
        """自动结束专注, 记下休息这类没人按按钮的操作, 由状态的主人做;
        主人不在线 (2 分钟没同步) 时本机来做."""
        owner = self.timer_owner
        if not owner or owner == self.sync.my_id:
            return True
        now = time.time() * 1000
        return not any(st.get("id") == owner and now - float(st.get("ts", 0)) < devsync.FRESH_MS
                       for st in self.sync.peer_status())

    def timer_live(self) -> dict:
        owner = self.timer_owner or self.sync.my_id
        if self.state == RUNNING and self.start_wall is not None:
            return {"state": "running", "owner": owner,
                    "start": self.start_wall.replace(microsecond=0).isoformat(timespec="seconds"),
                    "checks": int(self._focus_checks), "ok": int(self._focus_ok)}
        if self.state == ON_BREAK and self.break_end_wall is not None and self.break_start_wall is not None:
            return {"state": "break", "owner": owner,
                    "break_start": self.break_start_wall.isoformat(timespec="seconds"),
                    "break_end": self.break_end_wall.isoformat(timespec="seconds"),
                    "bonus": bool(self._bonus_break_active)}
        return {"state": "idle", "owner": owner}

    def adopt_timer(self, live: dict):
        """别的设备上更新的计时器状态: 照着切换, 不记录本机原来那一段 (那台设备会记)."""
        if not isinstance(live, dict):
            return
        cur = self.timer_live()
        st, owner = live.get("state"), str(live.get("owner") or "")
        if st == cur["state"] and live.get("start") == cur.get("start") \
                and live.get("break_end") == cur.get("break_end"):
            if st == "running" and int(live.get("checks", 0)) > self._focus_checks:
                self._close_check_win()         # 在别的设备上确认过"还在吗"了
                self._focus_checks = int(live.get("checks", 0))
                self._focus_ok = float(live.get("ok", 0))
            self.timer_owner = owner
            self._save_timer_state()
            return
        self._close_check_win()
        self.cancel_update()
        self.idle_since = None
        now = datetime.now()
        try:
            if st == "running":
                start = datetime.fromisoformat(live["start"])
                self.break_start_wall = None
                self._enter_focus(start, time.monotonic() - (now - start).total_seconds(),
                                  int(live.get("checks", 0)), float(live.get("ok", 0)))
            elif st == "break":
                end = datetime.fromisoformat(live["break_end"])
                left = int((end - now).total_seconds())
                if left <= 0:
                    self.reset_to_idle()
                else:
                    self.state = ON_BREAK
                    self.start_wall = None
                    self.break_start_wall = datetime.fromisoformat(live["break_start"])
                    self.break_end_wall = end
                    self.countdown_seconds = left
                    self._bonus_break_active = bool(live.get("bonus"))
                    self._show_on_break()
                    self.schedule_update()
            else:
                self.break_start_wall = None
                self.reset_to_idle()
        except (KeyError, TypeError, ValueError):
            return
        self.timer_owner = owner
        self._save_timer_state()
        self.refresh_display()

    # ---- 更新 (MECHANICS 16) ----

    def _on_main(self, fn):
        """后台线程完成后回到界面线程."""
        try:
            self.sync_runner.call(fn)
        except Exception:
            pass

    def check_update(self, auto: bool):
        if self.shutting_down:
            return
        self.updater.check(done=lambda u: self._on_main(lambda: self._after_check(auto)))
        self._refresh_if_visible("settings")

    def _after_check(self, auto: bool):
        u = self.updater
        self._refresh_if_visible("settings")
        if u.state == "available":
            if auto and getattr(config, "UPDATE_AUTO", 1):
                self.install_update()
                return
            toast.notice(tr("⬆  发现新版本"),
                         tr("当前版本 {v}", v=config.APP_VERSION) + "\n" + tr("发现新版本 {v}", v=u.latest_text), [
                             (tr("立即更新"), self.install_update, True), (tr("稍后"), None, False)])
        elif u.state == "latest" and not auto:
            toast.info(tr("已是最新版本") + f" ({config.APP_VERSION})")
        elif u.state == "error":
            if auto:
                self._update_retry = True
            else:
                toast.warn(tr("检查 / 更新失败: {e}", e=u.error))

    def install_update(self):
        if self.updater.state != "available":
            return
        toast.info(tr("正在下载更新…"))
        self.updater.install(done=lambda u: self._on_main(self._after_install))
        self._refresh_if_visible("settings")

    def _after_install(self):
        u = self.updater
        self._refresh_if_visible("settings")
        if u.state == "installed":
            self._log_notice(f"UPDATE installed {u.latest_text}")
            toast.notice(tr("⬆  已更新"), tr("已更新到 {v}, 重启后生效", v=u.latest_text), [
                (tr("立即重启"), self._restart_for_update, True), (tr("稍后"), None, False)])
        elif u.state == "dev":
            toast.warn(tr("这是 git 仓库里的开发版本, 不会自动覆盖, 请用 git pull 更新"))
        elif u.state == "error":
            toast.warn(tr("检查 / 更新失败: {e}", e=u.error))

    def _restart_for_update(self):
        self.restart_requested = True       # main.py 在主循环结束后重新打开程序
        self._update_restart = True         # 更新后马上重开: 不算"关闭程序", 共享的专注不结束
        self.on_close()

    def on_sync_changed(self):
        self.refresh_display()
        self.update_log_badge()
        self.update_settings_badge()
        self._refresh_if_visible("rules", "stats", "cal", "log")

    def after_sync_settings(self, keys):
        """同步来的设置生效之后: 和本机修改生效时一样刷新."""
        if "LANGUAGE" in keys or "THEME" in keys:
            i18n.init_from_config()
            self.root.after(50, self.rebuild_ui)
            return
        if "HOUR_LOG_ENABLED" in keys:
            self._sync_hour_log_setting()
        if "FILTER_MODE" in keys and "rules" in self.pages:
            self.pages["rules"].refresh(force=True)
        if "settings" in self.pages:
            self.pages["settings"].refresh()
        self.update_settings_badge()
        self.refresh_net(force=True)

    def _peer_line(self):
        """Timer 页: 绑定的每台设备此刻的状态; 有设备离线时提醒离线的那段专注不会被记录. 返回 (文字, 有离线吗)."""
        lines = []
        offline = []
        for st in self.sync.device_states():
            if not st["online"]:
                offline.append(st["name"])
                lines.append(f"🔗 {st['name']}: {tr('离线')}")
                continue
            t = {"running": tr("专注中…"), "break": tr("休息中 ☕")}.get(st.get("timer"), tr("准备就绪"))
            if st.get("timer") == "running" and st.get("since"):
                try:
                    mins = (datetime.now() - datetime.fromisoformat(st["since"])).total_seconds() / 60
                    t += f" {mins:.0f} min"
                except ValueError:
                    pass
            net = {"online": tr("联网中"), "pending": tr("冷静期中")}.get(st.get("net"), "")
            lines.append(f"🔗 {st.get('name', '?')}: {t}" + (f" · {net}" if net else ""))
        if offline:
            lines.append(tr("⚠ 有设备离线: 离线的这段时间专注不会被记录."))
        return "\n".join(lines), bool(offline)

    # ---- Rules 页的应用名单: 前台窗口的程序被禁用时立即关掉 (MECHANICS 4.7) ----

    def _schedule_app_rules(self):
        if self.shutting_down:
            return
        try:
            self._check_app_rules()
        except Exception as e:
            self._log_window(f"ERROR    app rules {type(e).__name__}: {e}")
        self.app_rules_job = self.root.after(1000, self._schedule_app_rules)

    def _check_app_rules(self):
        """Proxy / 代理-全屏蔽 (Proxy block-all) 断网方式下生效 (全屏蔽时只剩应用名单起作用);
        开发者模式下不管 (和网络限制一样解除)."""
        if self.dm.resetting or self.net.dev_mode or self.net.net_mode != "proxy":
            return
        rules = self.net.proxy.rules
        mode = getattr(config, "FILTER_MODE", "blacklist")
        online = self.net.online
        hit = window_guard.check_focus_rules(lambda exe: rules.app_allowed(exe, online, mode))
        if not hit:
            return
        exe, title, why = hit
        self._log_window(f"RULES    {why}  {exe}  {title}")
        try:
            self.net.proxy._on_event(f"🪟 {title[:40]}", exe, False, why)
        except Exception:
            pass
        now = time.monotonic()
        if now - self._app_rules_toast.get(exe, -99.0) >= 5:
            self._app_rules_toast[exe] = now
            msg = (tr("{exe} 在黑名单里,已关闭窗口.", exe=exe) if why == "blacklist-app"
                   else tr("{exe} 不在白名单里,已关闭窗口.", exe=exe))
            toast.warn(msg)

    # ---- 窗口使用时间统计 ----

    @staticmethod
    def _window_focus_sec() -> float:
        return max(1.0, float(getattr(config, "WINDOW_FOCUS_EVERY", 10)))

    def _window_focus_ms(self) -> int:
        return int(self._window_focus_sec() * 1000)

    def _window_focus_tick(self):
        """看一次前台程序, 视为用户在过去这几秒一直在用它 (桌面 / 命令行记为空白).
        电脑睡眠时 after 不会触发, 所以睡眠的时间不会被算进去."""
        if self.shutting_down:
            return
        try:
            if getattr(config, "WINDOW_FOCUS_ENABLED", 0) and not self.dm.resetting:
                self.dm.add_window_time(window_guard.foreground_program(),
                                        self._window_focus_sec())
                self._window_focus_ticks += 1
                if self._window_focus_ticks % 6 == 0:      # 大约每分钟写一次文件
                    self.dm.save_window_usage()
                    self._refresh_if_visible("stats")
        except Exception as e:
            self._log_window(f"ERROR    focus {type(e).__name__}: {e}")
        self.window_focus_job = self.root.after(self._window_focus_ms(), self._window_focus_tick)

    # ---- 配置期 ----

    def _show_welcome(self):
        hours = int(config.SETUP_HOURS)
        msg = (tr("联网要先申请,等过冷静期才连上,每天有额度.")
               + "\n\n" + tr("接下来{h}小时是配置期:所有修改立即生效.").format(h=hours)
               + "\n" + tr("之后每次修改都要等 48 小时."))
        # 快捷方式的三个选项就放在介绍窗口里, 点"开始使用"或"去设置"时照勾选的做
        toast.notice(tr("👋  欢迎使用 OIT"), msg, [
            (tr("去设置"), self._open_basic_settings, True), (tr("开始使用"), None, False)],
            options=[("desktop", tr("创建桌面快捷方式"), True),
                     ("start", tr("固定到开始菜单"), True),
                     ("taskbar", tr("固定到任务栏"), False)],
            on_options=self._make_shortcuts)

    # ---- 第一次打开: 快捷方式 (介绍窗口里勾选) ----

    def _make_shortcuts(self, opts: dict):
        if not any(opts.values()):
            return

        def run():
            res = shortcut.install(opts.get("desktop", False), opts.get("start", False),
                                   opts.get("taskbar", False), APP_NAME)
            self._log_notice("SHORTCUT " + ", ".join(f"{k}={'ok' if ok else 'no'}:{info}"
                                                     for k, (ok, info) in res.items()))
            self._on_main(lambda: self._report_shortcuts(res))
        threading.Thread(target=run, daemon=True).start()

    def _report_shortcuts(self, res: dict):
        names = {"desktop": tr("桌面快捷方式"), "start": tr("开始菜单"), "taskbar": tr("任务栏")}
        done = [names[k] for k, (ok, _i) in res.items() if ok]
        failed = [f"{names[k]}: {info}" for k, (ok, info) in res.items()
                  if not ok and info not in ("manual",)]
        lines = []
        if done:
            lines.append(tr("已完成: {items}", items=", ".join(done)))
        if "start" in res and res["start"][0]:
            lines.append(tr("OIT 在开始菜单的\"所有应用\"里; 没有出现在固定列表时, 在开始菜单里右键 OIT → 固定到\"开始\"."))
        if res.get("taskbar", (True, ""))[1] == "manual":
            lines.append(tr("Windows 不允许程序自己固定到任务栏. 已在资源管理器里选中 OIT 的快捷方式: 右键 → 显示更多选项 → 固定到任务栏."))
        if failed:
            lines.append(tr("失败: {items}", items="; ".join(failed)))
        if lines:
            toast.notice(tr("🖥  快捷方式"), "\n\n".join(lines))

    def _open_basic_settings(self):
        self.show_page("settings")
        try:
            self.pages["settings"].show_tab("basic")
        except Exception:
            pass

    def end_setup_period(self):
        self.settings.end_setup()
        self._setup_was_active = False
        self._log_notice("SETUP period ended by user")
        self._after_setup_change()
        toast.info(tr("配置期已结束."))

    def _check_setup_period(self):
        """配置期自然到期时提示一次, 并刷新界面上的标记."""
        active = self.settings.in_setup()
        if self._setup_was_active and not active:
            self._setup_was_active = False
            self._log_notice("SETUP period ended (time is up)")
            self._after_setup_change()
            toast.notice(tr("⏳  配置期结束了"),
                         tr("之后的修改都要等 48 小时."))

    def _after_setup_change(self):
        self.update_settings_badge()
        for key in ("settings", "rules"):
            if key in self.pages:
                try:
                    self.pages[key].refresh(force=True) if key == "rules" else \
                        self.pages[key].refresh()
                except Exception:
                    pass

    def check_pending_settings(self):
        if self.shutting_down:
            return []
        due = self.settings.promote_due()
        if not due:
            return due

        self._sync_hour_log_setting()
        if "NET_MODE" in due:
            self.net.sync_mode()
        if "rules" in self.pages:
            self.pages["rules"].refresh(force=True)
        self.pages["settings"].refresh()
        self.update_settings_badge()
        self.refresh_net(force=True)
        labels = ",".join(tr(self.settings.display(k)) for k in due)
        toast.success(tr("以下配置已生效:") + "\n" + labels)
        self._log_notice(f"SETTINGS applied: {', '.join(due)}")
        return due

    def _check_rule_changes(self):
        """名单里到期的改动: 以前只在打开 Rules 页时才生效, 不看那一页时过滤代理一直用旧名单."""
        try:
            due = self.net.proxy.rules.promote_due()
        except Exception as e:
            self._log_window(f"ERROR    rules promote {type(e).__name__}: {e}")
            return
        if not due:
            return
        if "rules" in self.pages:
            self.pages["rules"].refresh(force=True)
        toast.success(tr("名单改动已生效:") + "\n" + "\n".join(
            f"{'+' if p['op'] == 'add' else '−'} {p['item']}" for p in due[:8]))
        self._log_notice("RULES applied: " + ", ".join(f"{p['op']} {p['list']} {p['item']}" for p in due))

    def _sync_hour_log_setting(self):
        want = bool(self.settings.value("HOUR_LOG_ENABLED"))
        if want != self.dm.hour_enabled:
            self.dm.apply_hour_enabled(want)
        if "log" in self.pages:
            self.pages["log"].refresh()
        self._refresh_if_visible("stats")
        self.update_log_badge()

    def _log_window(self, text: str):
        """任务栏窗口数上限的流水: 关掉了哪些窗口, 以及检查出错的原因."""
        try:
            with open(os.path.join(self.dm.data_dir, "window_guard.log"),
                      'a', encoding='utf-8') as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")
        except IOError:
            pass

    def _log_notice(self, text: str):
        try:
            with open(os.path.join(self.dm.data_dir, "settings_changes.log"),
                      'a', encoding='utf-8') as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")
        except IOError:
            pass






    def on_close(self):
        # 绑定了其他设备: 关闭程序就结束共享的专注, 并马上告诉其他设备 (MECHANICS 8.2)
        ended = False
        if self.state == RUNNING and self.start_mono is not None and self.sync.peers() \
                and not self.dm.resetting and not self._close_pending \
                and not getattr(self, "_update_restart", False):
            self._end_focus_for_close()
            ended = True
        self._save_focus_now()      # 专注中关闭 (没有绑定设备): 状态留在文件里, 下次打开时接着算 (见 _recover_focus)
        self.end_break_early(silent=True)
        self._cancel_jobs()
        if ended:
            try:
                self.sync.push_final()
            except Exception:
                pass
        self.sync.stop()
        # 关闭程序时开发者模式自动关掉, 否则 shutdown 不断网 (fail-closed 失效); 子端也跟着关
        if config.dev_mode_enabled():
            config.DEV_MODE = False
            if not self._is_child():
                try:
                    self.sync.push_final()
                except Exception:
                    pass
        self.net.shutdown()
        self.root.destroy()

    def update_log_badge(self):
        b = self.nav_btns.get("log")
        if not b:
            return
        if not self.dm.hour_enabled:
            cfg(b, text=self.nav_labels["log"] + "  (off)")
        else:
            cfg(b, text=self.nav_labels["log"] + (" ●" if self.dm.awaiting_slot() else ""))

    def on_hour_log_toggle(self, enabled: bool):
        self.update_log_badge()
        if "log" in self.pages:
            self.pages["log"].refresh()

    def on_settings_changed(self, status: str = "", key: str = None):
        self.update_settings_badge()
        if "rules" in self.pages and key in ("FILTER_MODE", "NET_MODE", None):
            self.pages["rules"].refresh(force=True)
        if key in ("NET_MODE", None) and self.net.sync_mode():
            self.refresh_net(force=True)
            self._log_notice(f"NET_MODE -> {self.net.net_mode}")
        if status != "instant":
            return
        if key in ("HOUR_LOG_ENABLED", "AUTO_TEXT", "FILL_WINDOW", None):

            if key in ("AUTO_TEXT", "FILL_WINDOW", None):
                if "log" in self.pages:
                    self.pages["log"].refresh()
            if key == "HOUR_LOG_ENABLED":
                self._sync_hour_log_setting()

    def update_settings_badge(self):
        b = self.nav_btns.get("settings")
        if not b:
            return
        mark = ""
        staged = self.settings.staged_count()
        if staged:
            mark += f"  ⏳{staged}"
        cfg(b, text=self.nav_labels["settings"] + mark)


    # ---- 开发者模式跟着母端 (设备同步) ----

    def _is_child(self) -> bool:
        m = self.sync.mother_id()
        return bool(m) and m != self.sync.my_id

    def on_dev_toggled(self, enabled: bool):
        """在"每日联网额度"里填了开关的数: 绑定了母端的子端不能自己开关 (跟着母端), 改回去."""
        if self._is_child():
            config.DEV_MODE = not enabled
            return
        self.on_dev_mode_changed(enabled)
        if self.sync.peers():
            self.sync.sync_now()                # 马上告诉子端

    def follow_dev_mode(self, on: bool):
        """同步来的母端状态."""
        if on != config.dev_mode_enabled():
            config.DEV_MODE = on
            self.on_dev_mode_changed(on)

    def _check_dev_follow(self):
        """子端: 母端 2 分钟没同步过了 (关掉了 / 断开了), 开发者模式就关掉."""
        if not config.dev_mode_enabled() or not self._is_child():
            return
        p = self.sync.peers().get(self.sync.mother_id()) or {}
        if time.time() * 1000 - float(p.get("last_ok") or 0) > devsync.FRESH_MS:
            self.follow_dev_mode(False)

    # ---- 安装包直接发给其他设备 / 收其他设备发来的 (MECHANICS 16) ----

    def update_wanted(self, platform: str, version: str) -> str:
        """"" = 要; 否则是不要的原因."""
        if platform != "windows":
            return "platform"
        v = updater.parse_version(version)
        if not v or v <= updater.parse_version(config.APP_VERSION):
            return "not-newer"
        if updater.is_dev_checkout():
            return "dev"
        return ""

    def on_update_received(self, path: str, version: str, name: str):
        def install():
            self.updater.install_file(path, done=lambda u: self._on_main(
                lambda: self._after_relay_install(version, name)))
        if getattr(config, "UPDATE_AUTO", 1):
            install()
            return
        toast.notice(tr("⬆  发现新版本"), tr("{name} 发来了新版本 {v}.", name=name, v=version), [
            (tr("立即更新"), install, True), (tr("稍后"), None, False)])

    def _after_relay_install(self, version: str, name: str):
        u = self.updater
        self._refresh_if_visible("settings")
        if u.state == "installed":
            self._log_notice(f"UPDATE installed {version} (from {name})")
            toast.notice(tr("⬆  已更新"), tr("已从 {name} 收到并安装 {v}, 重启后生效", name=name, v=version), [
                (tr("立即重启"), self._restart_for_update, True), (tr("稍后"), None, False)])
        elif u.state == "dev":
            toast.warn(tr("这是 git 仓库里的开发版本, 不会自动覆盖, 请用 git pull 更新"))
        else:
            toast.warn(tr("检查 / 更新失败: {e}", e=u.error))

    def _maybe_relay(self):
        """自动更新开着: 绑定的设备版本比 GitHub 上的旧, 就给它下载并发过去 (每台设备每个版本一次)."""
        if not getattr(config, "UPDATE_AUTO", 1) or self.updater.latest is None or self._relaying:
            return
        for pid, p in self.sync.peers().items():
            pv = updater.parse_version(p.get("version"))
            if pv and pv < self.updater.latest and (pid, self.updater.latest) not in self._relayed:
                self.relay_updates()
                return

    def relay_updates(self, manual: bool = False):
        if self._relaying:
            if manual:
                toast.info(tr("正在发送…"))
            return
        peers = {pid: p for pid, p in self.sync.peers().items() if p.get("platform") in ("windows", "android")}
        if not peers:
            if manual:
                toast.info(tr("没有绑定的设备."))
            return
        self._relaying = True
        if manual:
            toast.info(tr("正在给其他设备下载新版本…"))
        cache = os.path.join(self.dm.data_dir, "relay")

        def run():
            out = []
            try:
                rel = self.updater.fetch_release()
                latest = updater.release_version(rel)
                for pid, p in peers.items():
                    name = str(p.get("name") or "?")
                    pv = updater.parse_version(p.get("version"))
                    if pv and pv >= latest:
                        out.append((name, "latest"))
                        continue
                    if pv is None and not manual:
                        continue                # 旧版本的 OIT 不报版本号, 也收不了: 只在手动时试
                    self._relayed.add((pid, latest))
                    try:
                        path = self.updater.fetch_package(rel, p["platform"], cache)
                        out.append((name, self.sync.offer_update(pid, path, p["platform"], updater.vtext(latest))))
                    except Exception as e:      # noqa: BLE001
                        out.append((name, str(e) or type(e).__name__))
            except Exception as e:              # noqa: BLE001
                out.append(("GitHub", str(e) or type(e).__name__))
            finally:
                self._relaying = False
            self._on_main(lambda: self._report_relay(out, manual))
        threading.Thread(target=run, daemon=True).start()

    def _report_relay(self, out, manual: bool):
        from pages.sync_panel import sync_error_text
        words = {"sent": tr("已发送, 对方正在接收"), "latest": tr("已是最新版本"),
                 "platform": tr("对方不收这个平台的安装包"), "not-newer": tr("对方的版本不比这个旧"),
                 "dev": tr("对方是 git 仓库里的开发版本"), "no-package": tr("这个发布里没有对方平台的安装包")}
        lines = [f"{n}: {words.get(st) or sync_error_text(st)}" for n, st in out]
        if not lines or (not manual and not any(st == "sent" for _n, st in out)):
            return
        (toast.success if any(st == "sent" for _n, st in out) else toast.info)(
            tr("📤 发给其他设备") + "\n" + "\n".join(lines))

    def on_dev_mode_changed(self, enabled: bool):
        """在"每日联网额度"里填 325799 开 / 关 (settings_manager.DEV_TOGGLE_CODE). 不是设置改动, 不记流水."""
        if enabled:
            self.net.release_restrictions()
        else:
            try:
                self.net.block(sync=True)
            except Exception:
                pass
        self.update_settings_badge()
        self.refresh_net(force=True)
        self.pages["settings"].refresh()


    def _build_net_page(self, page):
        bg, card = COLORS["bg"], COLORS["bg_card"]

        hdr = tk.Frame(page, bg=bg)
        hdr.pack(fill="x", padx=28, pady=(22, 8))
        tk.Label(
            hdr, text=tr("🌐  联网申请"), bg=bg, fg=COLORS["text"],
            font=config.font("h1", bold=True)
        ).pack(anchor="w")

        ttk.Separator(page, orient="horizontal", style="UI.TSeparator").pack(fill="x", padx=26)

        card_fr = tk.Frame(
            page, bg=card, highlightbackground=COLORS["border"],
            highlightthickness=1, relief="solid", bd=0
        )
        card_fr.pack(padx=26, pady=20, fill="x")
        inner = tk.Frame(card_fr, bg=card)
        inner.pack(padx=22, pady=18, fill="both")

        status_row = tk.Frame(inner, bg=card)
        status_row.pack(anchor="w", pady=(0, 12))
        self.net_dot_canvas = tk.Canvas(status_row, width=12, height=12, bg=card, highlightthickness=0)
        self.net_dot_canvas.pack(side="left", padx=(0, 8))
        self.net_dot_id = self.net_dot_canvas.create_oval(1, 1, 11, 11, fill=COLORS["text_dim"], outline="")
        self.net_lbl_status = tk.Label(
            status_row, text="Offline", bg=card, fg=COLORS["text_dim"],
            font=config.font("body")
        )
        self.net_lbl_status.pack(side="left")

        self.net_lbl_time = tk.Label(
            inner, text="Offline", bg=card, fg=COLORS["text"],
            font=config.font("big", bold=True), anchor="w"
        )
        self.net_lbl_time.pack(anchor="w", pady=(2, 6))

        self.net_lbl_sub = tk.Label(
            inner, text="", bg=card, fg=COLORS["text_dim"],
            font=config.font("body"), anchor="w", justify="left"
        )
        self.net_lbl_sub.pack(anchor="w")

        self.net_bar = ttk.Progressbar(
            inner, maximum=float(self.settings.value("DAILY_LIMIT")), value=0,
            style="Quota.Horizontal.TProgressbar"
        )
        self.net_bar.pack(fill="x", pady=(16, 6))
        self.net_lbl_quota = tk.Label(
            inner, text="", bg=card, fg=COLORS["accent"],
            font=config.font("body", bold=True), anchor="w"
        )
        self.net_lbl_quota.pack(anchor="w")
        self.net_lbl_debt = tk.Label(
            inner, text="", bg=card, fg=COLORS["warn"],
            font=config.font("small"), anchor="w", justify="left"
        )
        self.net_lbl_debt.pack(anchor="w")

        area = tk.Frame(page, bg=bg)
        area.pack(fill="x", padx=26, pady=(0, 22))

        self.net_form = tk.Frame(area, bg=bg)
        row1 = tk.Frame(self.net_form, bg=bg)
        row1.pack(anchor="w", pady=(0, 8))
        ttk.Label(row1, text=tr("分钟"), style="UI.TLabel", width=8).pack(side="left")
        ttk.Spinbox(
            row1, from_=1, to=24 * 60, width=6,
            textvariable=self.var_min
        ).pack(side="left")
        row2 = tk.Frame(self.net_form, bg=bg)
        row2.pack(anchor="w", pady=(0, 12))
        ttk.Label(row2, text=tr("理由"), style="UI.TLabel", width=8).pack(side="left")
        ttk.Entry(row2, textvariable=self.var_reason, width=30).pack(side="left")

        btns = tk.Frame(self.net_form, bg=bg)
        btns.pack(anchor="w")
        self.btn_net_req = ttk.Button(
            btns, text=tr("📨  提交申请(需冷静期)"), style="Accent.TButton",
            command=self.on_net_request
        )
        self.btn_net_req.pack(side="left")
        self.btn_net_quick = ttk.Button(
            btns, text=tr("⚡  快速访问(×{a:g})").format(a=config.QUICK_MULTIPLIER),
            style="Danger.TButton", command=self.on_net_quick
        )
        self.btn_net_quick.pack(side="left", padx=(8, 0))
        self.btn_net_loan = ttk.Button(
            btns, text=tr("💳  紧急借贷"), style="Danger.TButton",
            command=self.on_net_loan
        )
        self.btn_net_loan.pack(side="left", padx=(8, 0))
        self.net_form.pack_configure(anchor="w", fill="x")

        self.net_ctrl = tk.Frame(area, bg=bg)
        self.btn_net_ctrl = ttk.Button(
            self.net_ctrl, text="", style="Accent.TButton",
            command=self.on_net_ctrl
        )
        self.btn_net_ctrl.pack(side="left", ipadx=10)
        self.btn_net_upgrade = ttk.Button(
            self.net_ctrl, text=tr("⚡  改为快速访问(×{a:g})").format(a=config.QUICK_MULTIPLIER),
            style="Danger.TButton",
            command=self.on_net_upgrade
        )

    def on_net_request(self):
        ok, minutes, reason = self._read_net_form()
        if not ok:
            return
        rate = 1.0
        if self.state == ON_BREAK and minutes * 60 <= self.countdown_seconds:
            # 休息中, 且申请时长不超过剩余休息时间 → 冷静期 ×BREAK_COOLDOWN_RATE
            rate = config.BREAK_COOLDOWN_RATE
        ok, msg = self.net.request(minutes, reason, cooldown_rate=rate)
        if not ok:
            toast.warn(tr(msg))
            return
        self.var_reason.set("")
        self.refresh_net(force=True)

    def on_net_quick(self):
        ok, minutes, reason = self._read_net_form()
        if not ok:
            return
        ok2, msg2 = self.net.can_quick(minutes)
        if not ok2:
            toast.warn(tr(msg2))
            return
        if not toast.ask(
            tr("快速访问跳过冷静期立刻联网,额度按 ×{a:g} 消耗.").format(
                a=config.QUICK_MULTIPLIER, m=minutes,
                cost=fmt_hm(minutes * 60 * config.QUICK_MULTIPLIER)),
            title=tr("快速访问"), danger=True
        ):
            return
        ok, msg = self.net.request(minutes, reason, quick=True)
        if not ok:
            toast.warn(tr(msg))
            return
        self.var_reason.set("")
        self.refresh_net(force=True)

    def on_net_loan(self):
        ok, minutes, reason = self._read_net_form()
        if not ok:
            return
        ok2, msg2 = self.net.can_borrow(minutes)
        if not ok2:
            toast.warn(tr(msg2))
            return
        if not toast.ask(
            tr("紧急借贷不会联网,只会给今天加额度;明天扣 {b:g} 倍.").format(
                b=config.LOAN_RATE, add=fmt_hm(minutes * 60),
                cost=fmt_hm(minutes * 60 * config.LOAN_RATE)),
            title=tr("紧急借贷"), danger=True
        ):
            return
        before = self.net.tomorrow_used_all
        ok, msg = self.net.borrow(minutes, reason)
        if not ok:
            toast.warn(tr(msg))
            return
        self.var_reason.set("")
        toast.success(tr("今天额度 +{add}.").format(add=fmt_hm(minutes * 60)))
        self.refresh_net(force=True)
        self._check_heavy_loan(before)

    def _check_heavy_loan(self, before: float):
        """借贷借掉明天额度的 LOAN_WARN_SHARE (默认一半) 以上: 提醒增加休息时间,
        并让下一次修改「每日联网额度」立即生效. 每天只在刚越过这条线时触发一次."""
        limit = self.net.tomorrow_limit
        if limit <= 0:
            return
        line = limit * float(config.LOAN_WARN_SHARE)
        after = self.net.tomorrow_used_all
        if not (before < line <= after):
            return
        granted = self.settings.grant_free_change("DAILY_LIMIT")
        if granted:
            self._log_notice("DAILY_LIMIT next change free — loan took "
                             f"{after / limit:.0%} of tomorrow's quota")
            self.update_settings_badge()
            if "settings" in self.pages:
                self.pages["settings"].refresh()
        msg = tr("明天的联网额度已经借掉了 {pct} ({used} / {limit}).").format(
            pct=f"{after / limit:.0%}", used=fmt_hm(after), limit=fmt_hm(limit))
        msg += "\n\n" + tr("也许是休息不够,可以调高「休息时间倍率」.")
        msg += "\n\n" + tr("下一次修改「每日联网额度」免等待.")
        toast.notice(tr("💳  借得有点多了"), msg, [
            (tr("去设置"), self._open_detail_settings, True), (tr("知道了"), None, False)])

    def _open_detail_settings(self):
        self.show_page("settings")
        try:
            self.pages["settings"].show_tab("timer")
        except Exception:
            pass

    def _read_net_form(self):
        try:
            minutes = int(self.var_min.get())
        except ValueError:
            toast.warn(tr("分钟数必须是整数."))
            return False, 0, ""
        reason = self.var_reason.get().strip()
        if not reason:
            toast.warn(tr("请写一个申请理由."))
            return False, 0, ""
        return True, minutes, reason

    def on_net_ctrl(self):
        if self.net.mode == "pending":
            self.net.cancel()
        elif self.net.mode == "online":
            self.net.end_early()
        self.refresh_net(force=True)

    def on_net_upgrade(self):
        p = self.net.pending or {}
        minutes = p.get("minutes", 0)
        ok2, msg2 = self.net.can_quick(minutes)
        if not ok2:
            toast.warn(tr(msg2))
            return
        if not toast.ask(
            tr("立刻结束冷静期并联网?额度按 ×{a:g} 消耗.").format(
                m=minutes, cost=fmt_hm(minutes * 60 * config.QUICK_MULTIPLIER), a=config.QUICK_MULTIPLIER),
            title=tr("快速访问"), danger=True
        ):
            return
        ok, msg = self.net.convert_to_quick()
        if not ok:
            toast.warn(tr(msg))
            return
        self.refresh_net(force=True)

    def _net_set_status(self, text: str, color: str):
        # 状态后面写上当前的断网方式, 一眼能看出是不是"代理-全屏蔽 (Proxy block-all)"
        text = f"{text}  ·  {tr(self.settings.display('NET_MODE'))}"
        cfg(self.net_lbl_status, text=text)
        if self.net_dot_canvas.itemcget(self.net_dot_id, "fill") != color:
            self.net_dot_canvas.itemconfig(self.net_dot_id, fill=color)

    def _net_bar_set(self, n):
        mx, val = max(1.0, n.today_total), float(n.quota_used)
        try:
            if float(self.net_bar["maximum"]) != mx:
                self.net_bar["maximum"] = mx
            if abs(float(self.net_bar["value"]) - val) >= 0.5:
                self.net_bar["value"] = val
        except Exception:
            self.net_bar["maximum"] = mx
            self.net_bar["value"] = val

    def refresh_net(self, force: bool = False):
        n = self.net
        # 开发者模式在界面上不显示: 照常显示成离线
        mode = "offline" if n.mode == "dev" else n.mode

        if mode != self._net_view or force:
            self._net_view = mode
            # 只在需要时才收起 / 显示 (重复 pack 会触发整页重新布局)
            if mode == "offline":
                hide_pack(self.net_ctrl)
                show_pack(self.net_form, anchor="w", fill="x")
            else:
                hide_pack(self.net_form)
                show_pack(self.net_ctrl, anchor="w")
                cfg(self.btn_net_ctrl,
                    text=tr("✕  取消申请") if mode == "pending" else tr("■  结束本次会话")
                )
                if mode == "pending":
                    cfg(self.btn_net_upgrade,
                        text=tr("⚡  改为快速访问(×{a:g})").format(a=config.QUICK_MULTIPLIER))
                    show_pack(self.btn_net_upgrade, side="left", padx=(8, 0))
                else:
                    hide_pack(self.btn_net_upgrade)

        if mode == "offline":
            self._net_set_status(tr("离线"), COLORS["text_dim"])
            cfg(self.net_lbl_time, text=tr("离线"))
            if n.remaining < 60:
                cfg(self.net_lbl_sub, text=tr("今天的额度已用完."))
                cfg(self.btn_net_req, state="disabled")
            else:
                cfg(self.net_lbl_sub, text="")
                cfg(self.btn_net_req, state="!disabled")
            cfg(self.btn_net_quick,
                text=tr("⚡  快速访问(×{a:g})").format(a=config.QUICK_MULTIPLIER),
                state="!disabled" if n.quick_left >= 60 else "disabled")
            cfg(self.btn_net_loan,
                state="!disabled" if n.loan_left >= 60 else "disabled")
        elif mode == "pending":
            p = n.pending
            self._net_set_status(tr("冷静期中 —— 仍可取消"), COLORS["warn"])
            cfg(self.net_lbl_time, text=fmt_ms(p["at"] - time.time()))
            start_at = datetime.fromtimestamp(p["at"]).strftime("%H:%M")
            cfg(self.net_lbl_sub,
                text=tr("已申请 {m} 分钟 · {at} 联网").format(m=p['minutes'], at=start_at)
            )
        else:
            self._net_set_status(tr("联网中"), COLORS["success"])
            cfg(self.net_lbl_time, text=fmt_ms(n.grant_left))
            if n.grant_quick:
                cfg(self.net_lbl_sub,
                    text=tr("⚡ 快速访问 ×{a:g}").format(
                        a=n.grant_rate)
                )
            else:
                cfg(self.net_lbl_sub, text="")

        self._net_bar_set(n)
        cfg(self.net_lbl_quota,
            text=tr("今日已用: {used} / {total}     今日剩余: {left}     可快速访问: {quick}").format(
                used=fmt_hm(n.quota_used), total=fmt_hm(n.today_total),
                left=fmt_hm(n.remaining), quick=fmt_hm(n.quick_left))
        )
        bits = []
        mult = n.quota_multiplier
        if abs(mult - 1.0) > 1e-9:
            bits.append(tr("今天的联网倍率 ×{m}: 每日额度 {base} → 今天 {quota}").format(
                m=f"{mult:g}", base=fmt_hm(float(config.DAILY_LIMIT)), quota=fmt_hm(n.limit)))
        if n.bonus_all > 0:
            bits.append(tr("今天已借 {bonus},明天将扣 {cost}(明天剩余 {left})").format(
                bonus=fmt_hm(n.bonus_all), cost=fmt_hm(n.tomorrow_used_all),
                left=fmt_hm(n.tomorrow_remaining)))
        if n.debt_all > 0:
            bits.append(tr("今天要先还昨天借的 {debt}").format(debt=fmt_hm(n.debt_all)))
        cfg(self.net_lbl_debt, text="\n".join(bits))

    def _safe_minutes(self):
        try:
            return int(self.var_min.get())
        except (TypeError, ValueError):
            return 0

    def _net_tick(self):
        if self.shutting_down:
            return
        now = time.monotonic()
        dt = now - self._net_last
        self._net_last = now
        event = self.net.tick(dt)
        self.refresh_net()
        self._sync_break_lock()
        if self.current_page == "rules" and "rules" in self.pages:
            self.pages["rules"].refresh()
        if self.current_page == "settings" and "settings" in self.pages:
            self.pages["settings"].refresh_sync()
        if event == "started" and self._update_retry and getattr(config, "UPDATE_CHECK", 1):
            self._update_retry = False
            self.check_update(auto=True)
        self.sync.track_offline()               # 每秒: 同步失败的设备马上算离线 (MECHANICS 8.4)
        if self.current_page == "timer" and hasattr(self, "lbl_peers"):
            text, off = self._peer_line()
            cfg(self.lbl_peers, text=text, fg=COLORS["warn"] if off else COLORS["text_dim"])
        self.net_job = self.root.after(1000, self._net_tick)

        if event:
            self._refresh_if_visible("stats")
        if event == "started":
            toast.success(tr("网络已开启."))
        elif event == "ended":
            toast.warn(tr("时间到,网络已断开."))


    def _build_reset_page(self, page):
        bg, card = COLORS["bg"], COLORS["bg_card"]
        danger = COLORS["danger"]

        hdr = tk.Frame(page, bg=bg)
        hdr.pack(fill="x", padx=28, pady=(22, 8))
        tk.Label(
            hdr, text=tr("🔄  重置"), bg=bg, fg=COLORS["text"],
            font=config.font("h1", bold=True)
        ).pack(anchor="w")

        ttk.Separator(page, orient="horizontal", style="UI.TSeparator").pack(fill="x", padx=26)

        warn_fr = tk.Frame(page, bg=COLORS["danger_bg"], highlightbackground=danger, highlightthickness=1)
        warn_fr.pack(padx=26, pady=(16, 10), fill="x")
        tk.Label(
            warn_fr, text=tr("⚠  警告:这会永久删除全部数据"),
            bg=COLORS["danger_bg"], fg=danger, font=config.font("body", bold=True),
            anchor="w"
        ).pack(fill="x", padx=16, pady=10)

        form = tk.Frame(page, bg=bg)
        form.pack(fill="x", padx=26, pady=(0, 16))
        row = tk.Frame(form, bg=bg)
        row.pack(anchor="w", pady=(0, 6))
        ttk.Label(row, text=tr("输入 RESET 以确认"), style="UI.TLabel").pack(side="left", padx=(0, 8))
        ttk.Entry(row, textvariable=self.var_confirm, width=16).pack(side="left")
        self.btn_reset = ttk.Button(
            form, text=tr("🔄  重置并清除数据"),
            style="Danger.TButton", command=self.on_reset
        )
        self.btn_reset.pack(anchor="w", ipadx=10)
        self.btn_reset.state(["disabled"])

        flavor(page, tr("就这样回到一切之初,修正错误的抉择."), bg=bg).pack(fill="x", padx=26, pady=(4, 16))

    def _on_confirm_change(self, *_):
        ok = self.var_confirm.get().strip() == "RESET"
        self.btn_reset.state(["!disabled" if ok else "disabled"])

    def refresh_reset(self):
        self.var_confirm.set("")

    def on_reset(self):
        if self.var_confirm.get().strip() != "RESET":
            return
        if not toast.ask(
            tr("这将永久删除全部数据,不可撤销."),
            title=tr("重置确认"), danger=True
        ):
            return
        self.do_reset()

    def do_reset(self):
        """重置: 先解除网络限制 (还原系统代理, 启用网卡), 删除全部数据, 再重新打开程序.
        新进程找不到设置文件, 就像第一次安装: 弹出介绍窗口, 开始配置期, 网络按默认设置接管."""
        self.dm.resetting = True
        self._cancel_jobs()
        self._close_check_win()
        try:
            self.sync.forget_me()          # 其他设备上保留的本机数据也删掉 (MECHANICS 9)
        except Exception:
            pass
        self.sync.stop()
        self.net.release()
        self.dm.erase_all_data()
        self.restart_requested = True      # main.py 在主循环结束后重新打开程序
        self.root.destroy()
