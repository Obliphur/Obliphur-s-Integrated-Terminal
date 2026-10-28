

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
import appban
import autostart
import devsync
import firewall
import updater
import shortcut
import achievements
import contract as contract_mod
import recorder
import daily
import behavior
import prestart
import media_pause
import prescript
from sync_local import PcLocal
from config import (
    COLORS, APP_NAME, IDLE, RUNNING, READY_BREAK, ON_BREAK,
    ensure_builtin_chime, load_json_file, save_json_file,
    compute_F, fmt_hm, fmt_ms
)
from i18n import tr
from net_guard import NetGuard
from data_manager import DataManager, USERNAME_MAX
from settings_manager import SettingsManager
from pages import (CalendarPage, HourLogPage, SettingsPage, RulesPage, StatsPage,
                   ContractPage, AchievementsPage, AchievementPopups, PrescriptPage)
from pages.prestart_dialog import PrestartDialog
from pages.outing_dialog import OutingDialog
from pages.file_channel import FileChannel, fmt_size
from pages.widgets import (frozen, LoadingCover, FocusGlow, cfg, flavor,
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


ALARM_WAV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds", "target_alarm.wav")


TIRED_TEXT = ("主管{name}你好.我是Obliphur.\n"
              "你的休息时间不够吗?你可能暂时还无法做到长时间专注 导致休息效率低下.\n"
              "或者说,你处于低谷期,没有什么精力长时间专注.\n"
              "没关系.到设置调整休息时间倍率吧,请不要直接卸载,哪怕RESET也好...")


def tired_text(name: str) -> str:
    """一周内第 5 次领"我累了"时弹窗的文字 (MECHANICS 6.5)."""
    return tr(TIRED_TEXT, name=name or "")


def _hours_text(h) -> str:
    """外出记录的小时数, 最多一位小数 (2.0 → "2")."""
    try:
        return f"{round(float(h), 1):g}"
    except (TypeError, ValueError):
        return "0"


def contract_page_results() -> dict:
    from pages.contract_page import RESULTS
    return RESULTS


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
        self._contract_break_active = False  # 完成合约后的第一次休息: 暂停时结转比例 1 - (1 - x) / 2
        self._break_pre = 0.0         # 这次休息里有多少秒是合约开始前积攒的 (放在最后用; 暂停时原样还回去)
        self._break_cid = None        # 这次休息是在哪个合约中开始的 (合约结束后暂停: 只还合约开始前的那部分)
        self._rest_tag = ""           # 刚结束的那段专注的标签 (开始于哪个合约, "" = 普通专注): 下一次休息按它的规则
        self._contract_rest_over = False  # 合约在休息中自然结束了, 这次休息继续 (手动暂停立即清空合约内获得的时间, 自然走完变空闲)
        self._supply_warned = set()   # 支援短缺: 这次休息已经提醒过的 (还差 3 / 1 分钟)
        self._offline_name = ""       # 合约因为哪台设备离线而失败
        self.rest_base = 0.0          # 刚结束的那段专注换来的休息时间 (秒, 已乘倍率; 合约中已乘疲惫)
        self._contract_seen = None    # 上次看到的合约 (id, 状态): 同步来的开始 / 结束要刷新
        self._recorders = {}          # 合约的录像 (目光 / 刻痕): camera / screen → recorder.Recorder
        self._rec_skip = {}           # 本机这个合约不录的: kind → (合约 id, 原因: none 没有摄像头 / ask 等着回答 / declined)
        self._rec_pending = False     # 开始合约前正在打开摄像头
        self._rec_retry = {}          # 录像出错 / 询问被关掉后, 什么时候再试 (monotonic)
        self._rec_started_cid = None  # 在本机开始的合约 (开始时已经确认过录像)
        self._rec_ok = set()          # 别的设备开始的合约, 本机答应过录像的 (路, 合约 id)
        self._outbox_lock = threading.Lock()   # 待发给合约主人的录像片段 (rec_outbox.json)
        self._outbox_busy = False
        self._outbox_at = 0.0
        self._scar_focus_id = None    # 刻痕的焦点设备 (录屏的那台); 每 20 秒判定一次
        self._scar_focus_at = 0.0
        self._merging = set()         # 正在合并的 (cid, kind), 防止重入
        self._merge_again = set()     # 合并中又收到了新片段的 (cid, kind): 合并完再合并一次
        self._focus_checks = 0        # 这一段专注里已经被手动关掉的"还在吗"提醒数
        self._focus_ok = 0.0          # 最后一次确认 (关掉提醒窗) 时已专注的秒数
        self._check_win = None        # 当前开着的"还在吗"提醒窗
        self._focus_saved = 0.0       # 上一次把专注状态写进文件的时刻 (monotonic)
        # 绑定了其他设备, 上次在共享的专注中 OIT 被关掉了: 等第一次同步后决定要不要终止这段专注 (MECHANICS 8.2)
        self._close_pending = None
        # 关机 (MECHANICS 15.4): 收到系统的关机指令 (power_off), 本机电量 (只在用电池时, 其他时候 None)
        self.power_off = False
        self.battery = None
        self._battery_at = 0.0
        # 开机自启动 (MECHANICS 3.4): 任务计划程序里的任务开着 ("on") 才算; 关机前提 (15.4)
        self.autostart_state = "unknown"
        self._autostart_at = 0.0
        # 合约的网络禁令 (MECHANICS 18.3): 防火墙出站规则 (过滤代理之外的程序), 每 5 秒按正在运行的程序调整
        self.appban = appban.AppBan()
        self._appban_at = 0.0
        self._appban_busy = False
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
        self._win_over = False        # 专注中窗口数是不是已经超过上限
        self._win_warn_at = 0.0       # 上一次提醒的时刻 (monotonic); 0 = 还没提醒过
        self._win_ages = window_guard.WindowAges()   # 每个窗口开了多久: 超过 2 分钟才计入窗口数 (MECHANICS 12)
        self._desk_away = False       # 用户切到了 OIT 不在的虚拟桌面: 本机算作离线 (MECHANICS 15.7)
        self._desk_at = 0.0
        self.prestart = prestart.Prestart()   # 提前启动 (仅 PC, MECHANICS 20)
        self.prestart_job = None
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



        self.settings = SettingsManager()
        # 第一次打开软件 (还没有设置文件): 开始 72 小时配置期, 界面建好后弹出介绍
        self._first_run = self.settings.fresh
        if self._first_run:
            self.settings.start_setup()
            # 第一次打开时可以设置行为模式 (这一次立即生效); 之后再改要等 7 天 (MECHANICS 21)
            self.settings.grant_free_change("BEHAVIOR_MODE")
        self._setup_was_active = self.settings.in_setup()
        i18n.init_from_config()
        config.UI_SCALE = self.ui_scale * float(self.settings.value("FONT_SCALE") or 1.0)
        self._apply_theme()
        self.focus_glow = FocusGlow(self.root)
        # 行为模式 / 外出 (MECHANICS 21): 不写 study_log / focus_log, 显示时叠加.
        # 要在 DataManager 里补记整点记录之前接上, 否则启动时会先用普通的自动记录内容补一遍.
        self.behavior = behavior.Behavior(config.app_data_dir())
        self.dm = DataManager(hour_auto_text=self.behavior.hour_auto_text)
        self.timer_file = os.path.join(self.dm.data_dir, "timer_state.json")
        self.break_carry = self._load_break_carry()
        # 非手动结束的专注 (没回应"还在吗", 程序退出, 共享的专注被终止) 存进积攒的休息时间、还没折算的部分 (6.8)
        self.auto_rest = min(self.break_carry, self._load_timer_int("auto_rest"))
        self.dm.hour_enabled = bool(self.settings.value("HOUR_LOG_ENABLED"))
        self.dm.extra_study = self.behavior.study_hours
        self.dm.extra_days = self.behavior.days
        self.dm.extra_focus = self.behavior.focus_segments
        self._outing_win = None
        self._outing_was_active = False
        # 成就 (MECHANICS 17): 没有存档时 (新安装 / 旧版本升级上来) 估计第一次启动的时刻
        self.ach = achievements.AchievementStore(self.dm.data_dir, lambda: achievements.guess_first_launch(
            self.dm.data_dir, self.settings.setup_until, list(self.dm.study_data) + list(self.dm.hour_days)))
        self.ach.blocked = lambda: self.dm.resetting
        # 合约 (MECHANICS 18): 先把盖过设置的项套上, 断网方式才会一开始就对
        self.contract = contract_mod.Contract(self.dm.data_dir)
        self.contract.blocked = lambda: self.dm.resetting
        self._apply_contract_overrides()
        self.net = NetGuard(self.dm.data_dir)
        self.net.quota_factor = self.behavior.quota_factor
        self.net.cooldown_factor = self.behavior.cooldown_factor
        # 设备同步 (MECHANICS 15): 其他设备的账并进额度和统计
        self.sync_runner = devsync.MainRunner()
        self.sync = devsync.SyncService(self.dm.data_dir, PcLocal(self), self.sync_runner)
        self.dm.peer_ledgers = self.sync.peer_ledgers
        self.dm.offline_spans = self.sync.offline_spans     # 正在进行的那段: 有设备离线的部分画成空白
        self.dm.peer_ledgers_by_id = self.sync.peer_ledgers_by_id
        self.net.before_adapter = self._before_adapter        # 换成 Adapter 之前: 告诉其他设备本机断网了 (15.5)
        self.dm.my_id = lambda: self.sync.my_id
        self.dm.mother_id = self.sync.mother_id
        self.net.peer_net = lambda: devsync.peer_net(self.sync.peer_ledgers(), self.net.date)
        # 联网状态随设备同步 (MECHANICS 2.8): 同一次联网由发起的设备扣额度, 它不在线时由本机扣
        self.net.my_id = lambda: self.sync.my_id
        self.sync.away = lambda: self._desk_away
        self.file_channel = FileChannel(self)                  # 文件通道 (MECHANICS 15.6)
        self.net.charge_ok = self._owner_away
        self.net.peer_usage = lambda: [r for led in self.sync.peer_ledgers()
                                       for r in (led.get("usage") or [])]
        self.sync_job = None
        # 指令 (Prescript): 都市之意主题独占的模块; 判定和存档不依赖界面, 主题切走也继续走.
        self.prescripts = prescript.PrescriptEngine(
            self.dm.data_dir,
            state_fn=lambda: ("focus" if self.state == RUNNING else
                              "break" if self.state == ON_BREAK else "idle"),
            today_hours_fn=self._today_hours_live,
            contracts_fn=lambda: self.contract.history,
            page_fn=lambda: self.current_page,
            minimized_fn=lambda: self.root.state() in ("iconic", "withdrawn"),
            grant_rest_fn=self._grant_prescript_rest,
            username_fn=lambda: self.dm.username,
            blocked_fn=lambda: self.dm.resetting,
            on_change=self._prescript_changed,
            dev_fn=config.dev_mode_enabled,
            pause_media_fn=media_pause.pause_all,
            remaining_rest_fn=self._prescript_remaining_rest,
            contract_active_fn=lambda: self.contract.active,
        )
        self.prescript_job = None
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
        toast.set_fog(self.contract.fog)         # 黑雾: 遮住除违规记录外的所有提示 (显示 [???], 18.3)
        self.ach_popups = AchievementPopups(root, self.ach, on_open=self._open_achievements)
        self.ach_popups.hold = lambda: self.contract.fog()
        # 先定好窗口大小, 再在加载页后面一页一页地建界面
        self._apply_geometry()
        cover = LoadingCover(root, tr("正在加载…"), "", steps=self.LAYOUT_STEPS).show()
        try:
            self._build_layout(root, cover)
            self.show_page("timer")
            self.refresh_display()
            self.update_log_badge()
            self.update_settings_badge()
            self._behavior_tick(initial=True)
        finally:
            with frozen(root):
                cover.close()

        self._start_midnight_timer()
        self._schedule_hourly()
        self._schedule_apply_check()
        try:
            self.prescripts.tick()
        except Exception as e:
            self._log_window(f"ERROR    prescript tick {type(e).__name__}: {e}")
        self.prescript_job = self.root.after(1000, self._prescript_tick)
        self._net_tick()
        self.window_job = self.root.after(5000, self._schedule_window_guard)
        self.prestart_job = self.root.after(1000, self._prestart_tick)
        self.window_focus_job = self.root.after(self._window_focus_ms(), self._window_focus_tick)
        self.app_rules_job = self.root.after(1000, self._schedule_app_rules)
        try:
            self.sync.start()
        except Exception as e:
            self._log_window(f"ERROR    sync start {type(e).__name__}: {e}")
        self._pump_sync()
        # 防火墙放行同步端口, 否则别的设备找不到本机 / 连不进来 (MECHANICS 15.3). netsh 较慢, 放到后台
        threading.Thread(target=self._check_firewall, daemon=True).start()
        # 合约的网络禁令: 上次留下的防火墙规则 (例如崩溃时没来得及删) 先删掉, 之后按合约重新加
        threading.Thread(target=self._appban_sync, args=(set(),), daemon=True).start()
        # 录像 (目光 / 刻痕): 上次没来得及加密的 (崩溃时留在临时文件夹里的, 旧版本留在数据目录里的 mp4) 先加密起来
        threading.Thread(target=lambda: recorder.seal_leftovers(self.dm.data_dir), daemon=True).start()
        # 开机自启动: 默认开启 (第一次运行时建好), 用户关掉了就不再打开 (MECHANICS 3.4)
        threading.Thread(target=self._check_autostart, args=(True,), daemon=True).start()
        if getattr(config, "UPDATE_CHECK", 1):
            self.root.after(3000, lambda: self.check_update(auto=True))

        self.root.bind("<Map>", self._on_window_map, add="+")
        self.root.bind("<Unmap>", self._on_window_unmap, add="+")
        self.root.bind_all("<ButtonPress>", self._touch, add="+")
        self.root.bind_all("<KeyPress>", self._touch, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        # 关机 / 注销时 Windows 会先通知窗口; 程序因异常退出时 atexit 也会跑. 都先存下专注状态
        self.root.protocol("WM_SAVE_YOURSELF", self._on_power_off)
        atexit.register(self._save_focus_now)
        self.var_confirm.trace_add("write", self._on_confirm_change)
        self._note_power_back()
        self._recover_break()
        self._recover_focus()
        if not self.dm.username:
            # 第一次打开 (或者还没填过用户名的旧版本升级上来): 先填用户名, 再显示介绍窗口
            self.root.after(600, self._ask_username)
        elif self._first_run:
            self.root.after(600, self._show_welcome)
        who, leave = self.sync.pop_wiped_by()
        if who:
            # 别的设备重置 / 卸载时请本机清空了数据 (MECHANICS 9); 别的设备解除了和本机的绑定 (15.1)
            msg = (tr("{name} 解除了和本机的绑定, 本机的数据已经清空.", name=who) if leave
                   else tr("{name} 重置了 OIT, 本机的数据也一起清空了.", name=who))
            self.root.after(900, lambda: toast.notice(tr("🔄  数据已清空"), msg))
        self.root.after(800, self.check_achievements)


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
        self.root.title(tr("指令终端") if getattr(config, "THEME", "") == "urban_will" else APP_NAME)

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
        """Windows 10/11: 深色主题时让标题栏也变成深色."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            dark = ctypes.c_int(1 if getattr(config, "THEME_DARK", config.THEME.endswith("_dark")) else 0)
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
            "rules": tr("🛡  规则"),
            "log": tr("📝  整点记录"),
            "cal": tr("📅  日历"),
            "stats": tr("📊  统计"),
            "contract": tr("⚔  合约"),
            "settings": tr("⚙  设置"),
            "reset": tr("🔄  重置")
        })
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
        if self._prescript_enabled():
            # 指令页整页盖在窗口上 (遮住侧边栏), 不放进 content 的 grid.
            self.pages["prescript"] = PrescriptPage(
                self.shell, self.prescripts, on_exit=self._exit_prescript)
        yield

        # 只有当前页挂在界面上 (grid), 其余页 grid_remove:
        # 看不见的页面不参与布局和重画, 每秒的刷新也不会去画它们.
        self.pages["cal"] = CalendarPage(self.content, self.dm)
        self.pages["cal"].fog = self._fog_seed
        self._prepare_page(self.pages["cal"])
        yield
        self.pages["stats"] = StatsPage(self.content, self.dm, self.net,
                                        focus_live=self._focus_live)
        self.pages["stats"].fog = self._fog_seed
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
                                              on_pick=self._warn_pick_term)
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
            data_dir=self.dm.data_dir, recording=self.recording, on_delete_record=self.delete_recording
        )
        self._prepare_page(self.pages["settings"])
        yield
        # 成就 (MECHANICS 17): 单独一页, 左侧导航里没有它 (由 设置 → 基本设置 的"🏆 成就"按钮打开)
        self.pages["ach"] = AchievementsPage(self.content, self.ach,
                                             on_back=lambda: self.show_page("settings"))
        self._prepare_page(self.pages["ach"])
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
        self._schedule_apply_check()
        self._net_tick()

    def on_language_changed(self):
        i18n.init_from_config()
        self.rebuild_ui()

    def on_theme_changed(self):
        self.rebuild_ui()
        # 切到都市之意时弹一次: 标题 + 那段话 + 确定 (只在用户主动换主题时出现).
        if config.THEME == "urban_will":
            self.root.after(120, self._city_will_notice)

    def _city_will_notice(self):
        if config.THEME != "urban_will" or self.shutting_down:
            return
        toast.notice(
            tr("都市之意"),
            tr("也许我们时刻都在随波逐流,只不过是被赋予了选择漂流对象的那一缕所谓的自由..."),
            [(tr("确定"), None, True)],
        )

    def on_font_scale_changed(self):
        self.ui_scale = config.detect_ui_scale()
        base = float(self.settings.value("FONT_SCALE") or 1.0)
        config.UI_SCALE = self.ui_scale * base
        self.rebuild_ui()


    def _cancel_jobs(self):
        self.shutting_down = True
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
        if key != "timer" and key != getattr(self, "current_page", None):
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
        if key == "prescript" and getattr(self, "current_page", "") != "prescript":
            self._prescript_back = getattr(self, "current_page", "timer")
        self.current_page = key
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
        elif key == "rules":
            self.pages["rules"].refresh(force=True)
        elif key == "log":
            self.pages["log"].show()
        elif key == "settings":
            self.pages["settings"].refresh()
        elif key == "ach":
            self.pages["ach"].show()
        elif key == "reset":
            self.refresh_reset()
        self.update_log_badge()
        self.update_settings_badge()


    # ---- 合约页上的侧边栏: 换成合约页主题的颜色 (MECHANICS 18) ----

    def _paint_sidebar(self):
        """合约页 (主题不是"跟随界面主题"时): 侧边栏换成合约页主题的颜色; 其他页照常."""
        if not hasattr(self, "sidebar"):
            return
        from pages.contract_page import palette
        P = palette()
        on = self.current_page == "contract" and P.get("side")
        side_bg = P["side"] if on else COLORS["bg_card"]
        cfg(self.sidebar, bg=side_bg, highlightbackground=P["side_rim"] if on else COLORS["border"])
        for w in self.sidebar.winfo_children():
            if isinstance(w, tk.Frame):
                cfg(w, bg=side_bg)
        cfg(self.topbar, bg=P["line"] if on else COLORS.get("topbar", COLORS["accent"]))
        for k, b in self.nav_btns.items():
            if not on:
                break
            if k == self.current_page:
                cfg(b, bg=P["side_on"], fg=P["side_on_text"], activebackground=P["side_on"],
                    activeforeground=P["side_on_text"])
            else:
                cfg(b, bg=P["side"], fg=P["side_text"], activebackground=P["side_hover"],
                    activeforeground=P["side_on_text"])

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

        # 提前启动 (仅 PC, MECHANICS 20): 专注中把选中的程序先跑起来, 窗口藏起来 + 静音
        self.btn_prestart = ttk.Button(btns, text=tr("⏩  提前启动"), command=self._prestart_click)

        # 行为模式 (MECHANICS 21): 封闭式管理 / 工作 时才出现
        self.btn_outing = ttk.Button(btns, text=tr("🚪  外出"), command=self._outing_click)

        # 连点按钮: 1 分钟内连点 10 次, 换 20 分钟休息时间 (每周最多 2 小时)
        self.lbl_bonus = ttk.Button(btns, text="", command=self._on_bonus_click)
        self.lbl_bonus.pack(side="left", padx=(24, 0))
        self._bonus_show(self._bonus_idle_text())

        # 作者的话 (MECHANICS.md 6.2): 一直显示在页面下方的空白处
        flavor(body, tr("在第二段区间内 学的时间越长得到的休息时间越长.请尽量长时间地专注."), bg=bg).pack(
            side="bottom", fill="x", padx=26, pady=(0, 22))
        self._sync_timer_buttons()
        self._sync_bonus_button()

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
        if self.behavior.active() is not None and self.state == IDLE:
            toast.info(tr("外出中, 先结束外出再开始专注."))
            return
        if self.state == IDLE:
            self._settle_auto_rest()             # 非手动结束的专注留下的休息时间: 先折算一次 (6.8)
            self._enter_focus(datetime.now(), time.monotonic())
            if self.contract.slow_bind(self.start_wall):
                toast.warn(tr("阻滞: 合约内这段专注的时间按 {p}% 增长", p=int(round(contract_mod.RANDOM_SLOW * 100))),
                           timeout=10000)
                if self.sync.peers():
                    self.sync.sync_now()

        elif self.state == RUNNING:
            end_wall = datetime.now()
            # 阻滞: 只减慢合约内那部分专注的时间 (MECHANICS 18.3)
            elapsed_minutes = self.contract.focus_minutes(
                self.start_wall, time.monotonic() - self.start_mono)
            self.last_t_minutes = elapsed_minutes      # 建议休息时长照常按整段专注算
            self.rest_base = self._focus_rest(elapsed_minutes, self.start_wall, end_wall)
            off = self._cut_offline(self.start_wall, end_wall)
            self.dm.add_study_session(self.start_wall, end_wall, max(0.0, elapsed_minutes - off / 60.0))
            self._add_study_pieces(self.start_wall, end_wall)
            self._close_check_win()
            self.state = READY_BREAK          # 专注已记下: 先清掉文件里的专注状态
            self._save_timer_state()
            self.refresh_display()

            self.cancel_update()
            # 合约专注 (开始于合约里) 的下一次休息按合约规则 (合约在专注中完成了也一样, MECHANICS 18.2)
            self._rest_tag = self.contract.focus_tag(self.start_wall)
            # 结束专注后直接进入休息, 不再停在"去休息"这一步
            self._start_break(announce=True)
            # 纵深推进: 这段专注不到要求的时长, 算一次违反
            tid = self.contract.focus_check(elapsed_minutes * 60)
            if tid:
                self.contract_violation(tr("专注只有 {m} 分钟 (至少 {n} 分钟)", m=int(elapsed_minutes),
                                           n=contract_mod.DEEP[tid] // 60), tid)

        elif self.state == READY_BREAK:
            # 旧流程留下的状态 (正常不会再进入): 按一下照样开始休息
            self._start_break()

        elif self.state == ON_BREAK:
            if self._break_locked():
                toast.warn(self._pause_block())
                return
            self.end_break_early()
            if self.state == IDLE:
                self.on_action()

    def on_break_button(self):
        """副按钮: 空闲 → 用积攒的休息时间直接开始休息; 休息中 → 暂停休息."""
        self._mine()
        if self.behavior.active() is not None and self.state == IDLE:
            toast.info(tr("外出中, 先结束外出再休息."))
            return
        if self.state == IDLE:
            if self.break_carry <= 0:
                toast.info(tr("还没有积攒的休息时间."))
                return
            self.last_t_minutes = 0.0
            self.rest_base = 0.0
            self._rest_tag = ""                  # 不是接着专注的休息: 按普通规则
            total = int(self.break_carry)
            self._start_break()
            if self.state == ON_BREAK:
                toast.success(tr("开始休息 {t}.").format(t=fmt_ms(total)))
        elif self.state == ON_BREAK:
            self.end_break_early()

    def _sync_timer_buttons(self):
        """按当前状态设置两个按钮的文字与可用状态; 提前启动按钮只在专注中、还没选过时出现."""
        if not hasattr(self, "btn_break"):
            return
        locked = self._break_locked()
        outing = self.behavior.active()
        try:
            if outing is not None:
                self.btn_action.config(text=tr("🚪  外出中"), state="disabled")
                self.btn_break.config(text=tr("☕  开始休息"), state="disabled")
            elif self.state == RUNNING:
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
        self._sync_prestart_button()
        self._sync_outing_button()

    def _sync_outing_button(self):
        """外出按钮: 只有封闭式管理 / 工作 时出现; 外出中变成"结束外出"."""
        btn = getattr(self, "btn_outing", None)
        if btn is None:
            return
        try:
            if not self.behavior.enabled():
                if btn.winfo_ismapped():
                    btn.pack_forget()
                return
            active = self.behavior.active()
            text = tr("🚪  结束外出") if active is not None else tr("🚪  外出")
            if btn.cget("text") != text:
                btn.config(text=text)
            if not btn.winfo_ismapped():
                try:
                    btn.pack(side="left", ipadx=8, padx=(18, 0), before=self.lbl_bonus)
                except Exception:
                    btn.pack(side="left", ipadx=8, padx=(18, 0))
        except Exception:
            pass

    # ---- 行为模式: 外出 (MECHANICS 21) ----

    def _can_reward_outing(self, outing=None) -> bool:
        """休息时间只由开始这次外出的设备发 (和计时器的记录只记一次一样)."""
        try:
            mine = self.sync.my_id
            if not mine or not self.sync.peers():
                return True
        except Exception:
            return True
        owner = str((outing or {}).get("owner") or "")
        return not owner or owner == mine

    def _outing_click(self):
        if not self.behavior.enabled():
            return
        active = self.behavior.active()
        if active is not None:
            h = self.behavior.recorded_hours(active)
            if toast.ask(
                tr("结束外出?"),
                tr("已经记录了 {h} 小时. 现在结束外出吗?").format(h=_hours_text(h)),
                ok_text=tr("结束外出"), danger=True
            ):
                self._end_outing()
            return
        if self.contract.active:
            toast.warn(tr("合约中不能外出."))
            return
        if self.state != IDLE:
            toast.warn(tr("请先结束专注 / 休息再外出."))
            return
        win = getattr(self, "_outing_win", None)
        try:
            if win is not None and win.winfo_exists():
                win.lift()
                return
        except Exception:
            pass
        self._outing_win = None
        self._outing_win = OutingDialog(self.root, self._start_outing,
                                        max_hours=int(config.OUTING_MAX_HOURS))

    def _start_outing(self, hours: int):
        # 填时长的窗口开着时, 可能已经开始了合约 / 专注 (MECHANICS 21.1)
        if self.contract.active:
            toast.warn(tr("合约中不能外出."))
            return
        if self.state != IDLE:
            toast.warn(tr("请先结束专注 / 休息再外出."))
            return
        ok, msg, o = self.behavior.start(hours, owner=(self.sync.my_id or ""))
        if not ok:
            toast.warn(tr(msg))
            return
        self._outing_was_active = True
        self._log_notice(f"OUTING start {hours}h until {o['end']:%Y-%m-%d %H:%M}")
        toast.success(tr("外出 {h} 小时, 到 {at} 结束.").format(
            h=hours, at=f"{o['end']:%m-%d %H:%M}"))
        self._sync_timer_buttons()
        self._behavior_tick()
        if self.sync.peers():
            self.sync.sync_now()

    def _end_outing(self):
        res = self.behavior.stop(can_reward=self._can_reward_outing)
        self._outing_was_active = False
        self._apply_outing_result(res)
        self._sync_timer_buttons()
        if self.state == IDLE:
            self.reset_to_idle()
        if self.sync.peers():
            self.sync.sync_now()

    def _apply_outing_result(self, res: dict) -> bool:
        reward = int((res or {}).get("reward") or 0)
        if reward <= 0:
            return False
        hours = float((res or {}).get("hours") or 0)
        self._set_break_carry(self.break_carry + reward)
        toast.success(tr("外出结束: 记录了 {h} 小时, 获得 {t} 休息时间.").format(
            h=_hours_text(hours), t=fmt_ms(reward)), timeout=10000)
        self._sync_bonus_button()
        self._refresh_if_visible("stats", "cal")
        return True

    def _show_outing(self, active: dict):
        now = datetime.now()
        left = max(0, int((active["end"] - now).total_seconds()))
        h = self.behavior.recorded_hours(active, now)
        self.lbl_time.config(text=fmt_hm(left))
        self.lbl_sub.config(text=tr("外出中 · 已经记录 {h} 小时").format(h=_hours_text(h)))
        self._status(tr("外出中 🚪"), COLORS["warn"])

    def _behavior_tick(self, initial: bool = False):
        """每秒: 到时间的外出自动结束, 发休息时间; 外出中更新 Timer 页."""
        if self.shutting_down or not hasattr(self, "behavior"):
            return
        now = datetime.now()
        was = self._outing_was_active
        self.behavior.sync_mode(now)
        if not self.behavior.enabled() and self.behavior.active(now) is not None:
            self._apply_outing_result(self.behavior.stop(now, can_reward=self._can_reward_outing))
            was = True
        c = getattr(self, "contract", None)
        if c is not None and c.active and self.behavior.active(now) is not None:
            # 别的设备同步来的合约 / 外出撞上了: 外出在合约开始时结束 (MECHANICS 21.1)
            o = self.behavior.yield_to_contract(c.start_at, now)
            if o is not None:
                self._log_notice(f"OUTING yield to contract, stopped {o['stopped']:%Y-%m-%d %H:%M}")
                toast.warn(tr("合约进行中, 外出已结束."))
                was = True
                if self.sync.peers():
                    self.sync.sync_now()
        ev = self.behavior.tick(now, can_reward=self._can_reward_outing)
        if ev.get("reward"):
            self._apply_outing_result(ev)
            was = True
        elif ev.get("ended"):
            toast.info(tr("外出结束."))
            was = True
            if self.sync.peers():
                self.sync.sync_now()
        active = self.behavior.active(now)
        if active is not None:
            self._outing_was_active = True
            if self.state == IDLE:
                self._show_outing(active)
        elif (was or self._outing_was_active) and self.state == IDLE:
            self._outing_was_active = False
            self.reset_to_idle()
        self._sync_outing_button()
        self._sync_bonus_button()

    def _sync_prestart_button(self):
        """提前启动按钮: 专注中显示; 选过以后在操作空窗期里变成"提前终止"."""
        btn = getattr(self, "btn_prestart", None)
        if btn is None:
            return
        try:
            show = (self.state == RUNNING and not self.behavior.active()
                    and (not self.prestart.chosen or self.prestart.in_grace))
            text = tr("⏹  提前终止") if self.prestart.in_grace else tr("⏩  提前启动")
            if btn.cget("text") != text:
                btn.config(text=text)
            if show and not btn.winfo_ismapped():
                try:
                    btn.pack(side="left", ipadx=6, padx=(18, 0), before=self.lbl_bonus)
                except Exception:
                    btn.pack(side="left", ipadx=6, padx=(18, 0))
            elif not show and btn.winfo_ismapped():
                btn.pack_forget()
        except Exception:
            pass

    # ---- 提前启动 (仅 PC, MECHANICS 20) ----

    def _prestart_click(self):
        """专注中点"提前启动": 扫一遍程序, 弹窗选至多两个; 空窗期里点它就是"提前终止"."""
        if self.state != RUNNING:
            return
        if self.prestart.in_grace:                   # 操作空窗期: 提前终止, 马上藏 + 静音
            self.prestart.terminate()
            toast.info(tr("提前终止: 现在就藏起来并静音."))
            self._sync_prestart_button()
            return
        if self.prestart.chosen:
            return
        win = getattr(self, "_prestart_win", None)
        try:
            if win is not None and win.winfo_exists():
                win.lift()
                return
        except Exception:
            pass
        self._prestart_win = None
        try:
            apps = prestart.scan_programs()
        except Exception as e:
            toast.warn(tr("扫描程序失败: {e}", e=f"{type(e).__name__}: {e}"))
            return
        if not apps:
            toast.warn(tr("没有找到可以提前启动的程序."))
            return
        self._prestart_win = PrestartDialog(self.root, apps, self._prestart_chosen,
                                           limit=prestart.MAX_APPS)

    def _prestart_chosen(self, items):
        """选好了: 启动 (没在跑的), 进入 3 分钟的操作空窗期; 空窗期过了才藏窗口 / 静音."""
        if not items or self.state != RUNNING:
            return
        failed = self.prestart.start(items)
        names = "、".join(str(i.get("name") or i.get("exe")) for i in items)
        msg = tr("提前启动: {names}. 3 分钟内可以先操作, 之后窗口藏起来、静音.", names=names)
        if failed:
            msg += "\n" + tr("打不开: {names}", names="、".join(failed))
        toast.info(msg, timeout=8000)
        self._log_notice("PRESTART " + ", ".join(str(i.get("exe")) for i in items))
        self._sync_timer_buttons()

    def _prestart_tick(self):
        """每秒: 跟着这些进程 (含子进程); 空窗期过了 (或提前终止) 就藏窗口 / 静音; 专注结束就放出来."""
        if self.shutting_down:
            return
        self._behavior_tick()
        if self.prestart.active and self.state != RUNNING:
            self.prestart.stop()
            self._sync_timer_buttons()
        if self.prestart.active:
            try:
                self.prestart.step()
            except Exception as e:
                self._log_window(f"ERROR    prestart {type(e).__name__}: {e}")
        self._sync_prestart_exempt()
        self._sync_prestart_button()
        self.prestart_job = self.root.after(1000, self._prestart_tick)

    def _sync_prestart_exempt(self):
        """提前启动的程序联网不受网站 / 应用名单和代理-全屏蔽限制 (MECHANICS 20)."""
        try:
            self.net.proxy.rules.exempt = self.prestart.exe_names()
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
        if self.contract.active:
            return                          # 合约中"我累了"按钮隐藏
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
        count = (int(d.get("bonus_count", 0) or 0) if d.get("bonus_week") == week else 0) + 1
        self._timer_extra = {"bonus_week": week, "bonus_used": used + add, "bonus_count": count}
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
        if count == int(config.BONUS_TIRED_TIMES):
            self._tired_notice()

    def _tired_notice(self):
        """一周内第 5 次领"我累了" (MECHANICS 6.5): 劝去调休息时间倍率, 它的下一次修改免等待
        (已经有排队中的修改时, 那一项立即生效)."""
        if self.settings.make_due_now("BREAK_TIME_SCALE"):
            self._log_notice("BREAK_TIME_SCALE pending change applied now — 5 bonus breaks this week")
            self.check_pending_settings()
        elif self.settings.grant_free_change("BREAK_TIME_SCALE"):
            self._log_notice("BREAK_TIME_SCALE next change free — 5 bonus breaks this week")
        self.update_settings_badge()
        if "settings" in self.pages:
            self.pages["settings"].refresh()
        toast.notice(tr("☕  来自 Obliphur"), tired_text(self.dm.username), [
            (tr("去设置"), self._open_detail_settings, True), (tr("知道了"), None, False)])

    def _sleep_notice(self):
        """睡觉时间之后 3 小时内第一次暂停休息: 提醒去睡觉 (MECHANICS 19)."""
        toast.notice(tr("🌙  该睡觉了"), daily.sleep_text(self.dm.username),
                     [(tr("晚安"), None, True)])

    def _start_break(self, announce: bool = False):
        """开始休息倒计时 = 建议休息时长 + 之前暂停休息结转的时间."""
        total = self._break_seconds()
        if total <= 0:
            toast.info(tr("剩余时间为 0,已重置."))
            self.reset_to_idle()
            return
        carry = int(self.break_carry)
        tag, self._rest_tag = self._rest_tag, ""
        c = self.contract
        # 合约专注的下一次休息, 合约已经完成了: 仍按合约规则 —— 这段专注换来的是合约休息时间 (暂停时清空),
        # 积攒的休息时间 (合约开始前剩下的 + 完成奖励) 是正常休息时间, 放在最后用, 暂停时原样留下不打折
        after = (not c.active) and c.completed(tag)
        bonus = bool(self._bonus_state().get("bonus_pending"))
        if bonus:
            self._bonus_break_active = True
            self._timer_extra = {"bonus_pending": False}
        if not c.active and not after and c.take_first_break():
            self._contract_break_active = True
            toast.info(tr("合约完成后的第一次休息: 暂停时剩余时间 ×{r:g} 留给下次.",
                          r=contract_mod.Contract.first_break_rate(config.BREAK_CARRY_RATE)), timeout=8000)
        self._break_pre = c.pre_take(carry) if c.active else 0.0
        self._break_cid = c.live.get("id") if c.active else None
        self._contract_rest_over = False
        if after:
            self._break_pre, self._break_cid, self._contract_rest_over = float(carry), tag, True
        self._supply_warned = set()
        self.auto_rest = 0                       # 一起用进这次休息, 结束休息时照常折算 (6.8)
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
        base = int(round(max(0.0, self.rest_base)))
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

    def _pause_block(self) -> str:
        """不能暂停休息的原因 ("" = 可以): 网络正在使用 (领到休息时间后的第一次休息除外)."""
        if self.state != ON_BREAK:
            return ""
        if self.net.mode == "online" and not self._bonus_break_active:
            return tr("网络正在使用,不能暂停休息.")
        return ""

    def _break_locked(self) -> bool:
        return bool(self._pause_block())

    def _sync_break_lock(self):
        if self.state == ON_BREAK:
            self._sync_timer_buttons()

    def end_break_early(self, silent: bool = False):
        """暂停休息(关闭程序也算):剩余时间 ×BREAK_CARRY_RATE 计入下一次休息."""
        if self.state != ON_BREAK:
            return
        if not silent and self._break_locked():
            toast.warn(self._pause_block())
            return
        if silent and not self._timer_auto_ok():
            self.break_start_wall = None
        self._record_break_end()
        left = max(0, int(self.countdown_seconds))
        full = self._bonus_break_active
        rate = float(config.BREAK_CARRY_RATE)
        if self._contract_break_active:
            rate = contract_mod.Contract.first_break_rate(rate)
        # 睡觉时间 (MECHANICS 19): 窗口内、非合约期间的第一次暂停休息, 结转比例 +5%
        night = (not silent and not self.contract.active
                 and daily.take_break_notice(self.dm, datetime.now()))
        if night:
            rate = daily.break_rate(rate)
        self._bonus_break_active = False
        self._contract_break_active = False
        # 合约中: 剩余时间里合约开始前积攒的那部分 (放在最后用) 原样还回去, 不打折;
        # 合约中开始的休息, 合约已经结束了: 只还这部分 (合约中得到的休息时间不留到合约之后)
        pre = min(left, int(self._break_pre))
        c = self.contract
        same = c.active and c.live.get("id") == self._break_cid
        ended = self._break_cid is not None and not same
        if same:
            c.pre_settle(self._break_pre, pre)
        self._break_pre, self._break_cid = 0.0, None
        if ended:
            add = pre
        # 领到休息时间后的第一次休息: 暂停时剩余时间全部保留, 不按倍率打折 (盖过合约完成后的比例)
        elif full:
            add = left
        else:
            add = pre + int(round((left - pre) * rate))
        if add > 0:
            self._set_break_carry(self.break_carry + add)
            if not silent:
                if full or add == left:
                    toast.info(tr("休息已暂停,剩余 {left} 留给下次.").format(
                        left=fmt_ms(left)))
                else:
                    toast.info(tr("休息已暂停,下次休息 +{add}.").format(
                        left=fmt_ms(left), r=rate, add=fmt_ms(add)))
        if silent:
            self.state = IDLE
            self.countdown_seconds = 0
            self._contract_rest_over = False
        else:
            self.reset_to_idle()
            self._arm_idle_nudge()
            # 禁止囤囤鼠!: 提前结束休息照样能结束, 算一次违反 (软规则)
            tid = c.hoard_check()
            if tid:
                self.contract_violation(tr("提前结束了休息"), tid)
            if night:
                self._sleep_notice()

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
        if self.behavior.active() is not None:
            return                          # 外出中不提醒"该开始下一轮了"
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
        if self.state == IDLE and self.behavior.active() is None:
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
            text = tr("积攒的休息时间: {c}").format(
                c=fmt_ms(self.break_carry))
            if self.auto_rest > 0:
                text += "\n" + tr("立即开始专注:先进行一次休息时间的折算")
            return text
        return ""

    def _load_break_carry(self) -> int:
        return self._load_timer_int("break_carry")

    def _load_timer_int(self, key: str) -> int:
        d = load_json_file(self.timer_file, {})
        try:
            return max(0, int(d.get(key, 0))) if isinstance(d, dict) else 0
        except (TypeError, ValueError):
            return 0

    def _set_break_carry(self, seconds: int):
        self.break_carry = max(0, int(seconds))
        self.auto_rest = min(int(getattr(self, "auto_rest", 0)), self.break_carry)
        self._save_timer_state()

    def _add_auto_rest(self, seconds: int):
        """非手动结束的专注: 休息时间存进积攒的休息时间, 记下这部分还没折算 (6.8)."""
        self.auto_rest = min(self.break_carry, self.auto_rest + max(0, int(seconds)))
        self._save_timer_state()

    def _settle_auto_rest(self):
        """手动开始专注 (6.8): 非手动结束的专注留下的那部分休息时间, 先按暂停休息的结转比例折算一次."""
        a = min(int(self.auto_rest), int(self.break_carry))
        self.auto_rest = 0
        if a <= 0:
            self._save_timer_state()
            return
        keep = int(round(a * float(config.BREAK_CARRY_RATE)))
        self._set_break_carry(self.break_carry - (a - keep))
        if self.contract.active:
            self.contract.pre_cap(self.break_carry)
        toast.info(tr("休息时间折算: 专注自动结束留下的 {a} ×{r:g} = {k}.", a=fmt_ms(a),
                      r=float(config.BREAK_CARRY_RATE), k=fmt_ms(keep)), timeout=8000)

    def _save_timer_state(self):
        """timer_state.json: 积攒的休息时间 + 本周连点领取的记录 + 进行中的专注."""
        if self.dm.resetting:
            return
        d = self._bonus_state()
        d.update(getattr(self, "_timer_extra", None) or {})
        self._timer_extra = None
        d["break_carry"] = self.break_carry
        d["auto_rest"] = int(getattr(self, "auto_rest", 0))
        d["focus"] = self._focus_record()
        # 进行中的休息: 关掉程序再打开时接着休息 (6.3)
        d["break"] = self.timer_live() if self.state == ON_BREAK and self.break_end_wall is not None else None
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
                "owner": self.timer_owner,
                # 关机 / 电量很低: 重新打开时不把关着的这段记成空闲, 跟着其他设备 (MECHANICS 15.4)
                "off": bool(self._went_off())}

    def _on_power_off(self):
        """Windows 要关机 / 注销了 (MECHANICS 15.4): 存下专注状态 (记上"关机"), 告诉其他设备本机关机了 ——
        它们把本机算作"关机" (效果和在线一样), 不会因为本机离线把专注记成空闲 / 让合约失败.
        关机被取消 (程序 1 分钟后还在运行) 时恢复正常."""
        self.power_off = True
        self._beat_power(force=True)
        self._save_focus_now()
        try:
            if self.sync.peers():
                self.sync.push_final(timeout=2.0)
        except Exception:
            pass
        try:
            self.root.after(60_000, self._power_off_cancelled)
        except Exception:
            pass

    # 关机的时间段 (MECHANICS 15.4): 每 30 秒写一次 power.json (此刻, 算不算关机); 重新打开时据此知道关了多久
    def _power_file(self) -> str:
        return os.path.join(self.dm.data_dir, "power.json")

    def _beat_power(self, force: bool = False):
        if not force and time.monotonic() - getattr(self, "_power_at", 0.0) < 30:
            return
        self._power_at = time.monotonic()
        save_json_file(self._power_file(), {"alive": int(time.time() * 1000), "off": bool(self._went_off())})

    def _note_power_back(self):
        """启动时: 上次是关机 / 没电停下的, 关着的这段 [最后一次心跳, 现在] 记下来 —— 本机之后记下的专注里这段记为空闲, 不计入统计."""
        d = load_json_file(self._power_file(), {})
        try:
            alive, off = int(d.get("alive") or 0), bool(d.get("off"))
        except (TypeError, ValueError, AttributeError):
            return
        now = int(time.time() * 1000)
        if off and alive and now - alive > float(config.CLOSED_GAP) * 1000:
            self.sync.add_self_off(alive, now)
            self._log_notice(f"POWER    was off {datetime.fromtimestamp(alive / 1000):%m-%d %H:%M} ~ now")
        self._beat_power(force=True)

    def _power_off_cancelled(self):
        self.power_off = False
        if self.state == RUNNING:
            self._save_timer_state()

    def _went_off(self) -> bool:
        """本机这次停下来算不算关机: 开着开机自启动, 并且收到了关机指令 / 电量很低 (见 devsync.went_off)."""
        return self.autostart_on and (self.power_off or (self.battery is not None and self.battery < devsync.LOW_BATTERY))

    def _save_focus_now(self):
        self.dm.save_window_usage()
        if self.state in (RUNNING, ON_BREAK) and not self.dm.resetting:
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

    def _resume_break(self, live: dict, end: datetime, left: int):
        """照着计时器状态 (同步来的 / 关闭前存下的) 进入休息, 还剩 left 秒."""
        self.state = ON_BREAK
        self.start_wall = None
        self.break_start_wall = datetime.fromisoformat(live["break_start"])
        self.break_end_wall = end
        self.countdown_seconds = left
        self._bonus_break_active = bool(live.get("bonus"))
        self._contract_break_active = bool(live.get("cbreak"))
        self._break_pre = float(live.get("cpre") or 0)
        self._break_cid = live.get("ccid")
        self._contract_rest_over = bool(live.get("crestover"))
        self._show_on_break()
        self.schedule_update()

    def _recover_break(self):
        """启动时: 上次关闭时在休息 → 还没到结束时刻就接着休息; 已经过了就记下这段休息, 回到空闲 (6.3)."""
        rec = self._bonus_state().get("break")
        if not isinstance(rec, dict) or rec.get("state") != "break":
            return
        now = datetime.now()
        try:
            start = datetime.fromisoformat(rec["break_start"])
            end = datetime.fromisoformat(rec["break_end"])
        except (KeyError, TypeError, ValueError):
            self._save_timer_state()          # 记录坏了: 丢掉
            return
        self.timer_owner = str(rec.get("owner") or "")
        left = int((end - now).total_seconds())
        if left > 0 and start <= now:
            self._resume_break(rec, end, left)
            self._save_timer_state()
            toast.info(tr("上次休息时程序退出了.已接着休息(到 {at}).", at=f"{end:%H:%M}"), timeout=8000)
            return
        if start < end <= now:
            self.dm.add_focus_session("break", start, end)
            c = self.contract
            if c.active and c.live.get("id") == rec.get("ccid"):
                c.pre_settle(float(rec.get("cpre") or 0), 0)
            self.idle_since, self._nudges = end, 0      # 休息结束后迟迟不开始的提醒照常从结束时刻算 (6.4)
        self._save_timer_state()

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
        if rec.get("off") and gap > float(config.CLOSED_GAP):
            # 本机是关机 / 没电停下的 (MECHANICS 15.4): 效果和在线一样, 专注照常接着计时 ("还在吗"照常按开始时刻算);
            # 关着的这段本机的数据不计入 (见 _cut_offline). 绑定了其他设备时, 同步上以后跟着它们
            self._enter_focus(start, time.monotonic() - (now - start).total_seconds(), checks, ok)
            toast.info(tr("关机期间专注照常进行, 已接着计时."), timeout=8000)
            return
        if self.sync.peers() and gap > float(config.CLOSED_GAP):
            # 绑定了其他设备, 共享的专注中 OIT 被关掉了: 关掉的这段记为空闲 (总统计里优先级最高),
            # 专注照常恢复, 等第一次同步看其他设备是不是已经结束了它, 没有就由本机终止 (_check_close_pending).
            # 本机是关机 / 没电了 (MECHANICS 15.4): 其他设备把本机算作在线, 这段不记空闲; 同步上了就跟着它们
            off = bool(rec.get("off"))
            if not off:
                self.dm.add_focus_session("idle", alive, now, focus=start)
            self._enter_focus(start, time.monotonic() - (now - start).total_seconds(), checks, ok)
            self._close_pending = {"start": start, "alive": alive, "t": time.monotonic(),
                                   "ms": int(time.time() * 1000), "off": off, "now": now}
            return
        if gap <= float(config.FOCUS_RESUME_GAP):
            self._enter_focus(start, time.monotonic() - (now - start).total_seconds(), checks, ok)
            toast.info(tr("上次专注时程序退出了.已接着计时(从 {at} 开始).").format(
                at=f"{start:%H:%M}"), timeout=8000)
            return
        # 退出很久了: 专注记到最后一次保存为止, 对应的休息时间存进积攒的休息时间
        sec = (alive - start).total_seconds()
        limit = self._check_mark(checks + 1, start) + float(config.FOCUS_CHECK_GRACE)
        penalized = sec >= limit
        if penalized:
            sec = limit
        minutes, brk = self._book_focus(start, sec, ok if penalized else None)
        self._set_break_carry(self.break_carry + brk)   # 同时清掉文件里的专注状态
        self._add_auto_rest(brk)
        if not rec.get("off"):
            self._deep_check_closed(start, sec)
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
            msg += "\n" + tr("立即开始专注:先进行一次休息时间的折算")
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
        if p.get("off"):
            if self.sync.synced_since(p["ms"]):
                # 关机期间其他设备把本机算作在线, 共享的专注照常进行: 接着计时
                toast.info(tr("关机期间共享的专注照常进行, 已接着计时."), timeout=8000)
                return
            # 没能和任何设备同步上: 和普通的被关掉一样, 关着的那段记为空闲, 专注终止
            self.dm.add_focus_session("idle", p["alive"], p["now"], focus=p["start"])
        start, alive = p["start"], p["alive"]
        sec = (alive - start).total_seconds()
        limit = self._check_mark(self._focus_checks + 1, start) + float(config.FOCUS_CHECK_GRACE)
        penalized = sec >= limit
        if penalized:
            sec = limit
        self._close_check_win()
        self.cancel_update()
        minutes, brk = self._book_focus(start, sec, self._focus_ok if penalized else None)
        self._mine()                           # 终止由本机做, 其他设备照着结束
        self.reset_to_idle()
        self._set_break_carry(self.break_carry + brk)
        self._add_auto_rest(brk)
        if not p.get("off"):
            self._deep_check_closed(start, sec)
        self.lbl_sub.config(text=self._idle_sub_text())
        self.refresh_display()
        self._refresh_if_visible("stats", "cal")
        msg = tr("这台设备上的 OIT 在 {at} 被关掉了, 共享的专注已终止.").format(at=f"{alive:%m-%d %H:%M}")
        msg += "\n" + tr("专注记到被关掉时为止: 计入 {m:.1f} 分钟; 关掉的这段记为空闲.").format(m=minutes)
        if brk > 0:
            msg += "\n" + tr("积攒的休息时间 +{t}.").format(t=fmt_ms(brk))
            msg += "\n" + tr("立即开始专注:先进行一次休息时间的折算")
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
        self._add_auto_rest(brk)                 # 同时写进文件
        if not self._went_off():
            self._deep_check_closed(start, elapsed)

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
        minutes = self.contract.focus_minutes(start, seconds)   # 阻滞: 只减慢合约内的那部分时间
        # 有设备离线的那段记为空闲, 不计入学习时长; 建议休息时长照常按计入前的时长算
        counted = max(0.0, minutes - self._cut_offline(start, end) / 60.0)
        self.dm.add_study_session(start, end, counted)
        self._add_study_pieces(start, end)
        brk = int(round(self._focus_rest(minutes, start, end)))
        return counted, brk

    def _deep_check_closed(self, start: datetime, seconds: float):
        """专注停止 (手动结束 / 提醒超时自动结束 / OIT 被关掉等, 非关机) 时按纵深推进检查这段够不够长 (18.3).
        关机 / 断电导致的停止由调用方用 off 判断豁免 (MECHANICS 15.4), 不走到这里."""
        minutes = self.contract.focus_minutes(start, seconds)
        tid = self.contract.focus_check(minutes * 60)
        if tid:
            self.contract_violation(tr("专注只有 {m} 分钟 (至少 {n} 分钟)", m=int(minutes),
                                       n=contract_mod.DEEP[tid] // 60), tid)

    def _focus_rest(self, minutes: float, start: datetime, end: datetime) -> float:
        """一段专注换来的休息时间 (秒): F(t) × 休息时间倍率, 再按合约 (MECHANICS 18) 调整."""
        sec = max(0.0, compute_F(minutes)) * float(config.BREAK_TIME_SCALE) * 60.0
        if self.contract.active:
            return self.contract.book_rest(sec, end)
        return self.contract.book_rest_after(sec, start) if start else sec

    def _cut_offline(self, start: datetime, end: datetime) -> float:
        """[start, end] 这段专注里不计入学习时长的部分, 返回一共多少秒:
        - 有绑定的设备离线的时间段: 记为空闲 (统计里压过同一段专注);
        - 本机关机的时间段 (MECHANICS 15.4): 本机的数据不计入 (见 _add_study_pieces);
          这段只有关机的设备 (没有别的设备记下专注 / 休息) 时记为空闲."""
        a_ms, b_ms = start.timestamp() * 1000, end.timestamp() * 1000
        try:
            spans = self.sync.offline_spans(a_ms, b_ms, own=False)
            mine = self.sync.self_off_spans(a_ms, b_ms)
        except Exception:
            return 0.0
        idle = list(spans)
        for a, b in mine:
            if not self.dm.peer_active(datetime.fromtimestamp(a / 1000), datetime.fromtimestamp(b / 1000)):
                idle.append((a, b))
        for a, b in idle:
            self.dm.add_focus_session("idle", datetime.fromtimestamp(a / 1000), datetime.fromtimestamp(b / 1000),
                                      focus=start)
        total, last = 0.0, None
        for a, b in sorted(list(spans) + list(mine)):          # 合并后的长度 (两种重叠时不重复算)
            if last is not None and a < last:
                a = last
            if b > a:
                total += (b - a) / 1000.0
            last = b if last is None else max(last, b)
        return total

    def _add_study_pieces(self, start: datetime, end: datetime):
        """记下本机这段专注; 本机关机的那几段 (MECHANICS 15.4) 不记, 不参与专注计时的计算."""
        try:
            mine = self.sync.self_off_spans(start.timestamp() * 1000, end.timestamp() * 1000)
        except Exception:
            mine = []
        a = start
        for x, y in mine:
            x, y = datetime.fromtimestamp(x / 1000), datetime.fromtimestamp(y / 1000)
            if x > a:
                self.dm.add_focus_session("study", a, x)
            a = max(a, y)
        if end > a:
            self.dm.add_focus_session("study", a, end)

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
        # 绑定的设备非关机离线累计超过 5 分钟: 自动结束专注 (MECHANICS 15.4; 关机算在线, 不计)
        if self.sync.peers() and self._timer_auto_ok() \
                and self._offline_secs(self.start_wall) >= float(config.OFFLINE_FOCUS_END):
            self._offline_end_focus(time.monotonic() - self.start_mono)
            return
        if self._check_win is not None and not self._check_win.alive():
            self._check_win = None             # 被手动关掉了 = 人还在
            self._mine()
            self._focus_checks += 1
            self._focus_ok = time.monotonic() - self.start_mono
            self._save_timer_state()
        grace = float(config.FOCUS_CHECK_GRACE)
        elapsed = time.monotonic() - self.start_mono
        mark = self._check_mark(self._focus_checks + 1)
        if elapsed >= mark + grace:
            if self._timer_auto_ok():          # 别的设备是主人时等它结束, 结果同步过来
                self._auto_end_focus(mark + grace)
            return
        if elapsed >= mark and self._check_win is None:
            due = datetime.now() + timedelta(seconds=mark + grace - elapsed)
            # 合约专注: 写准确的分钟数 (间隔是随机的); 黑雾时不写专注了多久 (18.3)
            if not self.contract.focus_tag(self.start_wall):
                spent = tr("已经专注 {t} 了.").format(t=fmt_hm(mark)) + "\n"
            elif self.contract.fog():
                spent = ""
            else:
                spent = tr("已经专注 {n} 分钟了.").format(n=int(round(mark / 60))) + "\n"
            self._check_win = toast.notice(
                tr("⏱  还在专注吗?"),
                spent + tr("请在{at}之前关掉这个窗口,否则专注会自动结束.").format(
                    at=f"{due:%H:%M}", p=float(config.FOCUS_CHECK_PENALTY)),
                [(tr("还在,继续专注"), None, True), (tr("■  结束专注"), self._end_from_check, False)])
        # 绑定了其他设备时写得勤一些: 被关掉之后才知道是什么时候关的
        every = float(config.FOCUS_ALIVE_EVERY if self.sync.peers() else config.FOCUS_SAVE_EVERY)
        if time.monotonic() - self._focus_saved >= every:
            self._save_timer_state()

    def _check_mark(self, n: int, start: datetime = None) -> float:
        """第 n 次"还在吗"提醒在开始专注后多少秒: 合约专注每次随机 40 ~ 60 分钟 (contract.check_mark)."""
        start = start or self.start_wall
        return contract_mod.check_mark(n, start, bool(self.contract.focus_tag(start)),
                                       float(config.FOCUS_CHECK_EVERY))

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
        self._mine()                             # 自动结束由本机做, 其他设备照着结束
        self.reset_to_idle()
        self._set_break_carry(self.break_carry + brk)   # 同时清掉文件里的专注状态
        self._add_auto_rest(brk)
        self._deep_check_closed(start, seconds)  # 纵深推进: 自动结束的这段也查够不够长 (任何原因停止都查)
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
            msg += "\n" + tr("立即开始专注:先进行一次休息时间的折算")
        toast.notice(tr("⏹  专注已自动结束"), msg)

    def _offline_secs(self, start: datetime) -> float:
        """这段专注里绑定设备非关机离线累计了多少秒 (关机算在线, 不计)."""
        try:
            spans = self.sync.offline_spans(start.timestamp() * 1000, time.time() * 1000, own=False)
        except Exception:
            return 0.0
        return sum((b - a) for a, b in spans) / 1000.0

    def _offline_end_focus(self, seconds: float):
        """绑定设备非关机离线累计超过 5 分钟: 自动结束专注 (MECHANICS 15.4). 休息时间存进积攒的休息时间."""
        start = self.start_wall
        self._close_check_win()
        self.cancel_update()
        minutes, brk = self._book_focus(start, seconds)
        self._mine()                             # 离线判定由本机做, 其他设备照着结束
        self.reset_to_idle()
        self._set_break_carry(self.break_carry + brk)
        self._add_auto_rest(brk)
        self._deep_check_closed(start, seconds)  # 任何原因停止都查纵深推进
        self.lbl_sub.config(text=self._idle_sub_text())
        self._sync_timer_buttons()
        self.refresh_display()
        self._refresh_if_visible("stats", "cal")
        msg = tr("绑定的设备离线累计超过 {n} 分钟, 专注已自动结束.").format(n=int(float(config.OFFLINE_FOCUS_END) // 60))
        if brk > 0:
            msg += "\n" + tr("积攒的休息时间 +{t}.").format(t=fmt_ms(brk))
            msg += "\n" + tr("立即开始专注:先进行一次休息时间的折算")
        toast.notice(tr("⏹  专注已自动结束"), msg)

    def _restore_timer_view(self):
        """界面重建 (换语言 / 字号) 后按当前状态恢复计时器页."""
        self._sync_timer_buttons()
        active = self.behavior.active()
        if active is not None and self.state == IDLE:
            self._show_outing(active)
        elif self.state == RUNNING:
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
            rate = self.contract.focus_rate(self.start_wall)   # 现在还是不是阻滞中 (合约结束后就不再减慢)
            cur_m = self.contract.focus_minutes(self.start_wall, elapsed)
            if self.contract.fog():
                # 黑雾: 专注时看不到专注时长 (预计休息时长也能推出时长, 一起不显示)
                self.lbl_time.config(text="???")
                self.lbl_sub.config(text="")
                self.update_job = self.root.after(200, self.update)
                return
            self.lbl_time.config(text=f"{cur_m:.2f} min")
            rem = compute_F(cur_m) * float(config.BREAK_TIME_SCALE) * self.contract.rest_factor()
            if rem > 0:
                sub = tr("预计停止时的休息时长:  {m:.2f} 分钟").format(m=rem)
            else:
                sub = tr("预计休息时长: 0.00 分钟")
            if rate < 1:
                sub += "\n" + tr("阻滞: 这段专注的时间按 {p}% 增长", p=int(round(rate * 100)))
            self.lbl_sub.config(text=sub)
            self.update_job = self.root.after(200, self.update)

        elif self.state == ON_BREAK:
            if self.countdown_seconds > 0:
                m = self.countdown_seconds // 60
                s = self.countdown_seconds % 60
                self.lbl_time.config(text=f"{m:02d}:{s:02d}")
                sub = tr("休息剩余 {m} 分 {s:02d} 秒").format(m=m, s=s)
                if self._contract_rest_over:
                    sub += "\n" + tr("暂停休息: 立即消耗所有的合约休息时间, 但是不对正常休息打折")
                elif self.contract.active and self._break_pre > 0:
                    sub += "\n" + tr("合约前保存的休息时间: {t}").format(t=fmt_ms(int(self._break_pre)))
                if self._break_locked():
                    sub += "\n" + self._pause_block()
                self.lbl_sub.config(text=sub)
                self.countdown_seconds -= 1
                self.update_job = self.root.after(1000, self.update)
            else:
                if not self._timer_auto_ok():
                    self.break_start_wall = None      # 这一段由开始休息的那台设备记
                self._record_break_end()
                self._bonus_break_active = False
                self._contract_break_active = False
                if self.contract.active and self.contract.live.get("id") == self._break_cid:
                    self.contract.pre_settle(self._break_pre, 0)
                self._break_pre, self._break_cid = 0.0, None
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
        self.rest_base = 0.0
        self.countdown_seconds = 0
        self._contract_rest_over = False
        self._sync_timer_buttons()
        active = self.behavior.active() if getattr(self, "behavior", None) is not None else None
        if active is not None:
            self._show_outing(active)
        else:
            self.lbl_time.config(text="0.00 min")
            self.lbl_sub.config(text=self._idle_sub_text())
            self._status(tr("准备就绪"), COLORS["text_dim"])
        if hasattr(self, "timer_file"):
            self._save_timer_state()            # 清掉文件里的进行中的休息, 重开时不会再记一遍

    def refresh_display(self):
        if self.contract.fog():
            self.lbl_today.config(text=tr("今日: ???"))
            return
        self.lbl_today.config(text=tr("今日: {h:.1f} h").format(h=self.dm.get_today_hours()))


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
        # 每个整点和半点各醒一次; 半点只在合约的细化规范 (MECHANICS 18.4) 时响铃
        now = datetime.now()
        base = now.replace(minute=0, second=0, microsecond=0)
        self.next_hour = base + timedelta(minutes=30) if now.minute < 30 else base + timedelta(hours=1)
        remaining = (self.next_hour - now).total_seconds()
        if remaining <= 0:
            remaining = 1800.0
        delay_ms = int(remaining * 1000) + 100
        self.hourly_job = self.root.after(delay_ms, self._on_hour)

    def _on_hour(self):
        at = self.next_hour
        self._schedule_hourly()
        if "log" in self.pages:
            self.pages["log"].refresh()          # 到了填写时限的格子 (记录封锁) 锁上
        if at.minute == 30:
            hour = at - timedelta(minutes=30)
            if not (self.dm.hour_enabled and self.contract.fine_hour(hour)):
                return
            slot, length = hour, 30                         # 前半小时
        else:
            hour = at - timedelta(hours=1)
            self.dm.finalize_hours()
            if not self.dm.hour_enabled:
                self.update_log_badge()
                return
            if self.contract.fine_hour(hour):
                slot, length = at - timedelta(minutes=30), 30   # 后半小时
            else:
                slot, length = hour, 60
        if self._slot_locked(slot, length):
            self.update_log_badge()                         # 记录封锁另外封锁了这一格: 填不了, 不提示
            return
        # 绑定了其他设备时, 只有最后有人操作的那台响 (其他设备只标出待填写)
        chime = self._play_chime() if self.sync.is_latest_active() else None
        self._wait_chime(chime, slot, length=length)

    def _play_chime(self, choice=None):
        """choice: 试听时下拉框里选中的 (还没提交); None = 用设置里的."""
        if sys.platform != "win32":
            try:
                self.root.bell()
            except Exception:
                pass
            return None

        path = (self.settings.value("CHIME_WAV") if choice is None else choice) or ""
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

    def play_chime(self, choice=None):
        self._play_chime(choice)

    def _wait_chime(self, t, slot, waited: int = 0, length: int = 60):
        # 等提示音放完再提示填写; 自选的音频可能很长, 最多等 10 秒
        if t is not None and t.is_alive() and waited < 10000:
            self.root.after(200, lambda: self._wait_chime(t, slot, waited + 200, length))
            return
        if not self.dm.hour_enabled:
            return
        self.dm.prompt_slot = slot
        self.dm.prompt_len = length
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


    def _on_window_unmap(self, event):
        """OIT 最小化 / 藏起来: V 要求不能最小化, 等窗口状态更新后马上判一次."""
        if event.widget is not self.root:
            return
        try:
            self.root.after(0, self.prescripts.page_changed)
        except Exception:
            pass


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
        self._check_unbind()
        self._check_offline_chime()
        self.check_achievements()
        self.apply_job = self.root.after(5000, self._schedule_apply_check)

    # ---- 任务栏窗口数上限 (window_guard.py) ----

    @staticmethod
    def _window_check_ms() -> int:
        return int(max(5.0, float(getattr(config, "WINDOW_CHECK_EVERY", 30))) * 1000)

    def _schedule_window_guard(self):
        if self.shutting_down:
            return
        ws = self._aged_windows()
        # 同一个程序的多个窗口算一个, 浏览器的每个窗口各算一个 (窗口数上限和通道阻塞一样, MECHANICS 12)
        self._check_windows(None if ws is None else window_guard.channel_count(ws, contract_mod.BROWSER_EXES))
        try:
            self._check_contract_windows(ws)
        except Exception as e:
            self._log_window(f"ERROR    contract windows {type(e).__name__}: {e}")
        self.window_job = self.root.after(self._window_check_ms(), self._schedule_window_guard)

    def _aged_windows(self):
        """任务栏上开着超过 WINDOW_MIN_AGE 秒的窗口 (MECHANICS 12); 数不出来时 None.
        不在专注时也记着各窗口开了多久, 开始专注时早就开着的窗口马上算."""
        if self.dm.resetting or not (getattr(config, "WINDOW_LIMIT_ENABLED", 0) or self.contract.window_watch()[1]):
            self._win_ages.first = {}
            return None
        try:
            ws = window_guard.taskbar_windows(include_own=True, exclude_pids=self.prestart.pids)
        except Exception as e:
            self._log_window(f"ERROR    {type(e).__name__}: {e}")
            return None
        now = time.monotonic()
        self._win_ages.update(ws, now)
        return self._win_ages.old(ws, now, float(getattr(config, "WINDOW_MIN_AGE", 120)))

    def _check_windows(self, n):
        """专注中窗口数超过上限: 提醒 + 响一次违约音效; 提醒后 2 分钟冷却, 还没解决再响一次.
        n: 开着超过 2 分钟的窗口数 (_aged_windows; 同一个程序的多个窗口算一个, 浏览器的各算一个)."""
        if self.dm.resetting or not getattr(config, "WINDOW_LIMIT_ENABLED", 0) \
                or self.state != RUNNING:
            self._win_over = False
            self._win_warn_at = 0.0
            return
        limit = int(getattr(config, "WINDOW_LIMIT", 6))
        if n is None:
            return
        over = n > limit
        if not over:
            self._win_over = False
            self._win_warn_at = 0.0
            return
        now = time.monotonic()
        cooldown = float(getattr(config, "WINDOW_WARN_COOLDOWN", 120))
        if window_guard.remind_due(True, self._win_warn_at, now, cooldown):
            self._win_warn_at = now
            self._log_window(f"OVER     {n} > {limit}")
            toast.warn(tr("窗口超过上限 {x} 个 (现在 {n} 个), 关掉一些吧.", n=n, x=limit))
            self._play_alarm()                       # 和违约同一个音效 (MECHANICS 12)
        self._win_over = True


    # ---- 设备同步 (MECHANICS 15) ----

    def _check_autostart(self, first: bool = False):
        try:
            self.autostart_state = (autostart.ensure(self.dm.data_dir) if first else autostart.state())
        except Exception:
            self.autostart_state = "error"

    @property
    def autostart_on(self) -> bool:
        return self.autostart_state == "on"

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
        return self._owner_away(self.timer_owner)

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
                    "bonus": bool(self._bonus_break_active),
                    "cbreak": bool(self._contract_break_active),
                    "cpre": round(float(self._break_pre), 1), "ccid": self._break_cid,
                    "crestover": bool(self._contract_rest_over)}
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
                    self._resume_break(live, end, left)
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
        try:
            self.root.withdraw()            # 旧窗口马上消失: 下面的收尾 (保存, 同步, 断网) 可能要几秒
        except tk.TclError:
            pass
        try:
            self.on_close()
        finally:
            # 收尾中途出错 (例如内存里的旧代码碰上刚覆盖的新文件) 时, 以前停在 root.destroy() 之前:
            # 旧窗口一直开着, main.py 也等不到主循环结束, 不会重新打开程序
            try:
                if self.root.winfo_exists():
                    self.root.destroy()
            except tk.TclError:
                pass

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
        if "BEHAVIOR_MODE" in keys and getattr(self, "behavior", None) is not None:
            self.behavior.sync_mode()
            self._behavior_tick()
        if "FILTER_MODE" in keys and "rules" in self.pages:
            self.pages["rules"].refresh(force=True)
        if "settings" in self.pages:
            self.pages["settings"].refresh()
        self.update_settings_badge()
        self.refresh_net(force=True)

    def after_behavior_synced(self):
        """同步来的外出记录: 刷新按钮 / 倒计时; 到时间还没发的休息时间由本机发 (如果是主人)."""
        if getattr(self, "behavior", None) is None:
            return
        self.behavior.sync_mode()
        self._behavior_tick()
        self._sync_timer_buttons()
        self._refresh_if_visible("stats", "cal")

    # ---- Adapter 断网 (MECHANICS 15.5) ----

    def _before_adapter(self):
        """马上要换成 Adapter (网卡还在): 本机状态设为"断网"发给每台设备, 之后不再同步. 其他设备把本机算作在线."""
        if not self.sync.peers():
            return
        self._log_notice("SYNC     adapter: telling peers this device goes offline")
        try:
            self.sync.mark_adapter()
        except Exception as e:
            self._log_window(f"ERROR    adapter farewell {type(e).__name__}: {e}")

    def _check_adapter_overlap(self):
        """每秒: 断网期间和其他设备重叠的记录, 按 休息 > 专注 > 空闲 处理 (只改本机自己的记录, MECHANICS 15.5).
        本机断网回来: 第一次同步上之后和每台设备比一遍 (一样的由本机去掉); 别的设备断网回来: 和它比一遍."""
        if self.net.net_mode == "proxy":
            self.sync.note_proxy_back()
        todo = []
        win = self.sync.adapter_window()
        if win and self.sync.synced_since(win[1]):
            led = self.sync.peer_ledgers_by_id() or {}
            todo += [(did, win[0], win[1], True) for did in led]
            self.sync.clear_adapter()
        todo += [(did, a, b, False) for did, a, b in self.sync.pop_adapter_back()]
        total = {"study": 0.0, "break": 0.0, "idle": 0.0}
        for did, a, b, mine in todo:
            led = (self.sync.peer_ledgers_by_id() or {}).get(did) or {}
            res = self.dm.resolve_overlap(led.get("focus") or [], datetime.fromtimestamp(a / 1000),
                                          datetime.fromtimestamp(b / 1000), tie_mine=mine)
            for k in total:
                total[k] += res.get(k, 0.0)
            self._log_notice(f"SYNC     adapter overlap with {did} ({'mine' if mine else 'theirs'}): {res}")
        if any(v > 0 for v in total.values()):
            toast.info(tr("断网期间和其他设备重叠的记录已按 休息 > 专注 > 空闲 处理: 去掉了专注 {s}, 休息 {b}, 空闲 {i}.",
                          s=fmt_hm(total["study"]), b=fmt_hm(total["break"]), i=fmt_hm(total["idle"])), timeout=10000)
            self._refresh_if_visible("stats", "cal")
            self.refresh_display()
            if self.sync.peers():
                self.sync.sync_now()

    def _contract_line(self, b: dict) -> str:
        """冲突对话框里一个合约的一行: 起止, 危机等级, 结果 / 进行中, 词条."""
        from pages.contract_page import RESULTS, term_short
        a0, a1 = contract_mod._parse(b.get("start")), contract_mod._parse(b.get("end"))
        when = f"{a0:%m-%d %H:%M} — {a1:%H:%M}" if a0 and a1 else "?"
        if self.contract.fog():
            when = "??:?? — ??:??"                   # 黑雾: 不显示合约的开始 / 结束时刻
        state = tr("进行中") if not b.get("result") else tr(RESULTS.get(b["result"], b["result"]))
        terms = " · ".join(term_short(t) for t in b.get("terms") or [] if t in contract_mod.BY_ID) or "—"
        return tr("{when} · 危机等级 {lv} · {state}", when=when, lv=b.get("level", 0), state=state) + "\n    " + terms

    def _check_contract_conflicts(self):
        """时间重叠的两个合约 (MECHANICS 15.5): 问用户留哪个. 关掉窗口不选的话 1 分钟后再问."""
        c = self.contract
        win = getattr(self, "_conflict_win", None)
        if not c.conflicts:
            if win is not None:
                try:
                    win.destroy()                    # 在别的设备上已经选过了
                except Exception:
                    pass
                self._conflict_win = None
            return
        try:
            if win is not None and win.winfo_exists():
                return
        except Exception:
            pass
        if time.monotonic() < getattr(self, "_conflict_next", 0.0):
            return
        self._conflict_next = time.monotonic() + 60
        a, b = c.conflicts[0]
        msg = (tr("断网期间两台设备各自进行了合约, 时间有重叠. 只能保留一个, 另一个作废 (已经给出的休息时间不收回).")
               + "\n\nA  " + self._contract_line(a) + "\n\nB  " + self._contract_line(b))
        self._conflict_win = toast.notice(tr("⚔ 合约冲突"), msg, buttons=[
            (tr("保留 A"), lambda i=a["id"]: self._keep_contract(i), True),
            (tr("保留 B"), lambda i=b["id"]: self._keep_contract(i), True),
        ])

    def _keep_contract(self, keep_id: str):
        c = self.contract
        if not c.resolve_conflict(keep_id):
            return
        self._conflict_win = None
        self._conflict_next = 0.0
        self._log_notice(f"CONTRACT conflict: keep {keep_id}")
        self._check_contract()
        self._contract_changed()
        if self.sync.peers():
            self.sync.sync_now()

    def _peer_line(self):
        """Timer 页: 绑定的每台设备此刻的状态; 有设备离线时提醒离线的那段专注不会被记录. 返回 (文字, 有离线吗)."""
        lines = []
        offline = []
        for st in self.sync.device_states():
            if st.get("off"):
                lines.append(f"🔗 {st['name']}: {tr('关机')}")     # 效果和在线一样, 只是不同步 (15.4)
                continue
            if st.get("net_off"):
                lines.append(f"🔗 {st['name']}: {tr('断网')}")     # 换成了 Adapter: 效果和在线一样, 只是不同步 (15.5)
                continue
            if not st["online"]:
                offline.append(st["name"])
                if st.get("outdated"):
                    lines.append(f"🔗 {st['name']}: " + tr("离线 (版本 {v} 较低, 请更新)", v=st.get("version")))
                else:
                    lines.append(f"🔗 {st['name']}: {tr('离线')}")
                continue
            t = {"running": tr("专注中…"), "break": tr("休息中 ☕")}.get(st.get("timer"), tr("准备就绪"))
            if st.get("timer") == "running" and st.get("since") and not self.contract.fog():
                try:
                    mins = (datetime.now() - datetime.fromisoformat(st["since"])).total_seconds() / 60
                    t += f" {mins:.0f} min"
                except ValueError:
                    pass
            net = {"online": tr("联网中"), "pending": tr("冷静期中")}.get(st.get("net"), "")
            lines.append(f"🔗 {st.get('name', '?')}: {t}" + (f" · {net}" if net else ""))
        if offline:
            lines.append(tr("⚠ 有设备离线: 离线的这段时间专注不会被记录."))
        for name, v, newer in self.sync.version_mismatch():
            if newer:          # 本机版本低: 对方把本机算作离线
                lines.append(tr("⚠ 本机版本 {me} 低于 {name} ({v}): 其他设备把本机算作离线, 请更新.",
                                me=config.APP_VERSION, name=name, v=v))
        return "\n".join(lines), bool(offline)

    # ---- Rules 页的应用名单: 前台窗口的程序被禁用时立即关掉 (MECHANICS 4.7) ----

    def _schedule_app_rules(self):
        if self.shutting_down:
            return
        try:
            self._check_app_rules()
        except Exception as e:
            self._log_window(f"ERROR    app rules {type(e).__name__}: {e}")
        try:
            self._apply_net_bans()
        except Exception as e:
            self._log_window(f"ERROR    bans {type(e).__name__}: {e}")
        self.app_rules_job = self.root.after(1000, self._schedule_app_rules)

    def _check_app_rules(self):
        """Proxy / 代理-全屏蔽 (Proxy block-all) 断网方式下生效 (全屏蔽时只剩应用名单起作用);
        开发者模式下不管 (和网络限制一样解除)."""
        if self.dm.resetting or self.net.dev_mode or self.net.net_mode != "proxy":
            return
        rules = self.net.proxy.rules
        mode = getattr(config, "FILTER_MODE", "blacklist")
        rest = self.state == ON_BREAK              # 只管专注 / 空闲, 休息中全部放行; 和联不联网无关 (4.1)
        hit = window_guard.check_focus_rules(lambda exe: rules.app_allowed(exe, rest, mode))
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
            # 全频道阻塞干扰 (软): 被禁止的程序 (同一个程序 5 秒内算一次) 这个钟点里各设备合计满 20 次算一次违反
            tid = self.contract.jam_block(datetime.now(), self.sync.my_id)
            if tid:
                self.contract_violation(tr("全频道阻塞干扰中频繁打开被禁止的程序"), tid)

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

    # ---- 合约 (MECHANICS 18) ----

    def _contract_peers(self) -> bool:
        """有没有绑定 / 连接的其他设备 (全频道阻塞干扰要不要用代理-全屏蔽)."""
        sync = getattr(self, "sync", None)
        try:
            return bool(sync.peers()) if sync is not None else True
        except Exception:
            return True

    def _apply_contract_overrides(self) -> bool:
        """合约中盖过设置的项 (全频道阻塞干扰: 断网方式) 套到 config 上; 变了才动."""
        c = self.contract
        over = c.overrides() if c.active else {}
        if over == getattr(config, "CONTRACT_OVERRIDES", {}):
            return False
        config.CONTRACT_OVERRIDES = over
        self.settings.apply_to_config()
        net = getattr(self, "net", None)
        if net is not None and net.sync_mode():
            self._log_notice(f"NET_MODE -> {net.net_mode} (contract)")
        pages = getattr(self, "pages", {})
        if "rules" in pages:
            pages["rules"].refresh(force=True)
        if net is not None and pages:
            self.refresh_net(force=True)
        return True

    def _fog_seed(self):
        """黑雾: 统计 / 日历显示假数据用的种子 (同一个合约里不变); 没有黑雾时 None."""
        return self.contract.live.get("id") if self.contract.fog() else None

    def _fog_words(self):
        """黑雾: 整点记录假内容用的编号 (开始合约时抽好); 没有黑雾时 None."""
        return self.contract.fog_words() if self.contract.fog() else None

    def _check_desktop(self):
        """每 2 秒: OIT 的窗口在不在当前的虚拟桌面上 (MECHANICS 15.7). 切到别的桌面期间本机算作离线 ——
        不同步, 也不接受同步, 其他设备照常把本机算作离线, 本机也把它们算作离线 (专注里这段记为空闲, 合约的离线判定 …)."""
        if sys.platform != "win32" or time.monotonic() - self._desk_at < 2:
            return
        self._desk_at = time.monotonic()
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        except Exception:
            return
        away = window_guard.on_current_desktop(hwnd) is False
        if away == self._desk_away:
            return
        self._desk_away = away
        self._log_notice("DESKTOP  " + ("left OIT's virtual desktop: offline" if away else "back on OIT's virtual desktop"))
        if not away and self.sync.peers():
            toast.info(tr("回到了 OIT 所在的桌面. 切到其他桌面期间本机算作离线."), timeout=8000)
            self.sync.sync_now()

    def adopt_net(self, live: dict):
        """别的设备上改动了联网状态 (申请 / 联网 / 结束): 照着切换 (MECHANICS 2.8)."""
        event = self.net.adopt(live)
        self.refresh_net(force=True)
        self._sync_break_lock()
        if event == "started":
            toast.success(tr("网络已开启."))
        elif event == "ended":
            toast.warn(tr("联网已在其他设备上结束,网络已断开."))

    def _owner_away(self, owner: str) -> bool:
        """这个状态的主人是本机, 或者主人不在线 (2 分钟没同步): 自动的操作由本机来做."""
        if not owner or owner == self.sync.my_id:
            return True
        now = time.time() * 1000
        return not any(st.get("id") == owner and now - float(st.get("ts", 0)) < devsync.FRESH_MS
                       for st in self.sync.peer_status())

    def _contract_changed(self):
        """合约开始 / 结束之后 (本机或同步来的): 盖过设置的项, "我累了"按钮, 黑雾, 各页面."""
        self._apply_contract_overrides()
        self._sync_hour_log_setting()
        self._sync_bonus_button()
        self.refresh_display()
        self.refresh_net(force=True)
        s = self.dm.prompt_slot
        if s is not None and self.dm.prompt_len == 30 and not self.contract.fine_hour(s):
            self.dm.prompt_slot = None           # 合约结束, 细化规范的半小时格合回一格: 不再等那半小时
            self.update_log_badge()
        if "log" in self.pages:
            self.pages["log"].refresh()
        self._refresh_if_visible("contract")
        self._paint_sidebar()
        self.ach_popups.sync()                   # 黑雾: 合约中达成的成就等结束后再弹出
        self._refresh_if_visible("stats", "cal")
        if self.contract.fog() and self.current_page in ("settings", "ach"):
            self.show_page("timer")

    def _check_contract(self):
        """每秒: 到时间就完成, 空闲太久就失败 (由合约的主人做), 刷新合约页."""
        c = self.contract
        # 整点记录的词条 (记录封锁 / 细化规范) 可能随同步 / 未知变量中途出现: 每秒看一次要不要强制开启整点记录 (18.4),
        # 不然整点记录关着的设备上既不响提示音, 也不能填写, 超时也不判定
        if (bool(self.settings.value("HOUR_LOG_ENABLED")) or c.hour_forced()) != self.dm.hour_enabled:
            self._sync_hour_log_setting()
        seen = (c.live.get("id"), "active") if c.active else None
        if seen != self._contract_seen:
            was = self._contract_seen
            self._contract_seen = seen
            self._contract_changed()
            if was is not None and seen is None and c.last:
                toast.info(tr("合约已结束: {r}", r=tr(contract_page_results().get(c.last.get("result"), ""))))
            elif seen is not None and was is not None:
                toast.info(tr("合约开始."))
        try:
            self._sync_recorders()
        except Exception as e:
            self._log_window(f"ERROR    record {type(e).__name__}: {e}")
        if not c.active:
            self._refresh_if_visible("contract")     # 休息 / 联网中开始按钮变灰
            return
        self._apply_contract_overrides()
        if c.mark_fine(datetime.now()):          # 细化规范生效过的小时一直分两格 (未知变量到时移除后也是, 18.4)
            self._refresh_if_visible("log")
        self._contract_hours()
        self._check_supply()
        if not c.active:
            return
        # 绑定的设备离线超过 15 分钟: 合约失败 (看到它离线的设备都可以判定)
        if self.sync.peers():
            devices = [(d["name"], datetime.fromtimestamp((d["seen"] or 0) / 1000)) for d in self.sync.device_states()
                       if not d["online"]]
            hit = c.offline_due(devices, datetime.now(), datetime.fromtimestamp(self.sync._started_ms / 1000))
            if hit:
                self._offline_name = hit[0]
                self.end_contract("offline", hit[1])
                return
        if self._owner_away(c.owner):
            why, at = c.due(datetime.now(), self.state in (IDLE, READY_BREAK))
            if why:
                self.end_contract(why, at)
                return
            # 信息阻断: 额度是各设备共用的, 只由一台设备判定
            tid, kind = c.net_check(self.net.used_all, self.net.bonus_all, self.net.remaining, self.net.date)
            if tid:
                self.contract_violation(tr("使用了紧急借贷") if kind == "loan" else tr("联网超过了合约的额度"), tid)
                if not c.active:
                    return
            # 未知变量: 合约的每个小时开始时 (刚开始时也算) 随机抽一个词条, 3 分钟后生效, 那个小时结束时移除
            now = datetime.now()
            gone = c.expire(now)
            for tid in gone:
                self._log_notice(f"CONTRACT unknown expired {tid}")
                toast.info(tr("未知变量: {name} 已移除", name=tr(contract_mod.BY_ID[tid][3])), clear=True)
            picked = c.roll(now, text_of=self.dm.unit_text, auto=self.dm.auto_text_for_key,
                            fill=config.FILL_WINDOW)
            woke = c.wake(now)
            for kind, val in picked:
                self._log_notice(f"CONTRACT unknown -> {kind} {val}")
                self._random_effect(kind, val)
            for tid in woke:
                self._log_notice(f"CONTRACT unknown on {tid}")
                toast.warn(tr("未知变量: 启用了 {name}", name=tr(contract_mod.BY_ID[tid][3])), timeout=10000, clear=True)
            if picked or woke or gone:
                self._contract_changed()
                if self.sync.peers():
                    self.sync.sync_now()
        # 静默协议: 专注中这台设备处于联网状态 (各设备看自己的)
        tid = c.quiet_check(self.state == RUNNING and self.net.mode == "online")
        if tid:
            self.contract_violation(tr("专注中处于联网状态"), tid)
            if not c.active:
                return
        self._refresh_if_visible("contract")
        if self.current_page == "contract":
            self._paint_sidebar()

    def contract_block(self) -> str:
        """休息 / 联网 / 外出中不能开始合约: 返回提示 (没有限制时 "")."""
        if self.state == ON_BREAK:
            return tr("请先结束休息再开始合约")
        if getattr(self, "behavior", None) is not None and self.behavior.active() is not None:
            return tr("请先结束外出再开始合约")
        if getattr(self, "net", None) is not None and self.net.mode == "online":
            return tr("请先结束联网再开始合约")
        sync = getattr(self, "sync", None)
        diff = sync.version_mismatch() if sync is not None else []
        if diff:
            name, v, _newer = diff[0]
            return tr("{name} 的版本 ({v}) 和本机 ({me}) 不一样, 不能开始合约", name=name, v=v, me=config.APP_VERSION)
        return ""

    def start_contract(self, terms):
        c = self.contract
        if c.active or self._rec_pending:
            return
        why = self.contract_block()
        if why:
            toast.warn(why)
            return
        terms = contract_mod.clean_terms(terms)
        lv = contract_mod.level_of(terms)
        dur = contract_mod.duration_of(terms)
        now = datetime.now()
        end = now + timedelta(seconds=dur)
        # 睡觉时间 (MECHANICS 19): 合约结束时间晚于"这次要睡的时间"时, 当天第一次先确认一次
        late = daily.contract_delay(self.dm, end, now)
        if late and not toast.ask(daily.late_text(late), title=tr("🌙  该睡觉了"),
                                  ok_text=tr("开始行动"), danger=True):
            return
        msg = tr("危机等级 {lv}", lv=lv)                        # 不显示时长和结束时刻
        msg += "\n" + tr("完成后休息 +{n} 分钟", n=contract_mod.rest_minutes_total(lv, terms, contract_mod.clean_theme(config.CONTRACT_THEME)))
        if not toast.ask(msg, title=tr("▶  开始行动"), ok_text=tr("开始行动"), danger=True):
            return
        rec = self._record_lines(terms)
        if rec and self.settings.value("REC_ASK") and not toast.ask(
                "\n".join(rec), title=tr("⚔ 合约要求录像"), ok_text=tr("开始录像")):
            return
        if self.contract_block():
            return
        self._pending_cam = ""
        if "gaze" in terms:
            # 目光: 用设置 → 录像里选的那台 (本机的某个摄像头 / 在线的手机); 没选 (自动) 时
            # 本机能录就本机录, 否则找绑定的手机 (MECHANICS 18.8).
            how, which = self._camera_choice()
            if how == "phone":
                phone = self._camera_phone(which)
                if phone:
                    self._pending_cam = phone
                    self._begin_contract(terms)
                    return
                self._log_notice("RECORD camera choice: the phone camera is not online, falling back")
            local_ok = recorder.camera_consent() != "deny" and recorder.camera_count()
            if local_ok:
                self._pending_cam = self.sync.my_id
                self._rec_pending = True
                self._stop_recorder("camera")
                r = self._start_recorder("camera")
                toast.info(tr("正在打开摄像头…"))
                self._await_camera(terms, r, time.monotonic())
                return
            phone = self._camera_phone()
            if not phone:
                toast.warn(tr("没有可用的摄像头: 本机没有摄像头, 也没有绑定在线的手机.") + "\n"
                           + tr("「目光」需要本机或手机有摄像头."))
                return
            self._pending_cam = phone
        self._begin_contract(terms)

    def _camera_choice(self) -> tuple:
        """设置 → 录像 里选的摄像头: ("auto" / "pc" / "phone", 摄像头 id / 设备 id)."""
        raw = str(self.settings.value("REC_CAMERA") or "").strip()
        if raw.startswith("pc|"):
            return "pc", raw[3:]
        if raw.startswith("phone|"):
            return "phone", raw[6:]
        return "auto", ""

    def _local_camera_id(self) -> str:
        """本机录像用哪一台摄像头 (camera_devices 的 id); "" = 自动 (第一台)."""
        how, which = self._camera_choice()
        return which if how == "pc" else ""

    def _camera_phone(self, want: str = "") -> str:
        """绑定的、在线的手机 (目光可以用它的摄像头录): 返回设备 id, 没有则 "".
        want: 想用哪一台 (不在线 / 不是手机 / 没绑定 → ""). 不给时找任意一台在线的手机."""
        plat = self.sync.peers()
        states = self.sync.device_states()
        if want:
            p = plat.get(want) or {}
            if str(p.get("platform", "")).lower().startswith("android") \
                    and any(s["id"] == want and s.get("online") for s in states):
                return want
            return ""
        for s in states:
            if s.get("online") and str((plat.get(s["id"]) or {}).get("platform", "")).lower().startswith("android"):
                return s["id"]
        return ""

    def _await_camera(self, terms, r, t0):
        if r.state == "running":
            self._rec_pending = False
            self._begin_contract(terms)
            return
        if r.state in ("failed", "lost", "stopped") or time.monotonic() - t0 > 15:
            self._rec_pending = False
            self._stop_recorder("camera")
            toast.warn(tr("摄像头打不开, 合约没有开始.") + (f"\n{r.error}" if r.error else ""))
            return
        self.root.after(200, lambda: self._await_camera(terms, r, t0))

    def _begin_contract(self, terms):
        c = self.contract
        if c.active or self.contract_block():
            self._stop_recorder("camera")
            return
        lv = contract_mod.level_of(terms)
        c.start(terms, owner=self.sync.my_id, pre=self.break_carry, cam=getattr(self, "_pending_cam", ""),
                theme=config.CONTRACT_THEME)
        # 目光: 摄像头在合约开始前就打开了 (要先确认能录), 那时的 recorder 还不知道合约 id;
        # 现在补上, 不然这一段 / 这一整场录像在结束后按合约 id 找不到 (MECHANICS 18.8).
        cid = str(c.live.get("id") or "")
        for r in self._recorders.values():
            if not r.tag:                      # 只有合约开始前打开的录像才没有 tag
                r.set_tag(cid)
        # 凑齐的词条组合: 积攒的休息时间 +N 分钟 (在 pre 之后加: 算合约中得到的, 合约结束后不留下)
        gift = contract_mod.rest_gift(terms)
        if gift > 0:
            self._set_break_carry(self.break_carry + gift)
            if self.state == IDLE:
                self.lbl_sub.config(text=self._idle_sub_text())
            self._sync_timer_buttons()
        self._rec_started_cid = c.live.get("id")
        self._log_notice(f"CONTRACT start level {lv} terms {','.join(terms) or '-'} until {c.end_at:%Y-%m-%d %H:%M}"
                         + (f" gift {gift}s" if gift else ""))
        self._contract_seen = (c.live.get("id"), "active")
        self._contract_changed()
        self._sync_recorders()
        toast.success(tr("合约开始.") + ("\n" + tr("积攒的休息时间 +{t}.", t=fmt_ms(gift)) if gift else ""))
        if self.sync.peers():
            self.sync.sync_now()

    def abandon_contract(self):
        c = self.contract
        if not c.active:
            return
        msg = tr("提前结束合约?")
        if c.reward >= 1:
            msg += "\n" + tr("现在结束只给 {m} 分钟休息时间 (完成给 {n} 分钟).",
                             m=round(c.reward_at(datetime.now()) / 60), n=round(c.reward / 60))
        if not toast.ask(msg, title=tr("■  提前结束"), danger=True):
            return
        self.end_contract("abandoned")

    def _stop_break_for_contract(self):
        """结束合约前先停止休息 (MECHANICS 18.1): 剩下的休息时间按"暂停休息"算.
        合约开始前积攒的那部分原样留下, 合约中得到的部分由 end_contract 作废; 不算禁止囤囤鼠! 的违反."""
        if self.state != ON_BREAK:
            return
        left = max(0, int(self.countdown_seconds))
        self._record_break_end()
        pre = min(left, int(self._break_pre))
        c = self.contract
        if c.active and c.live.get("id") == self._break_cid:
            c.pre_settle(self._break_pre, pre)
        rate = float(config.BREAK_CARRY_RATE)
        if self._contract_break_active:
            rate = contract_mod.Contract.first_break_rate(rate)
        add = left if self._bonus_break_active else pre + int(round((left - pre) * rate))
        self._break_pre, self._break_cid = 0.0, None
        self._bonus_break_active = False
        self._contract_break_active = False
        if add > 0:
            self._set_break_carry(self.break_carry + add)
        self.reset_to_idle()
        self._save_timer_state()          # 关掉程序再打开时不能又从休息中恢复
        self._arm_idle_nudge()

    def _stop_focus_for_contract(self):
        """合约非自然结束 (失败 / 手动终止, MECHANICS 18.1): 正在进行的专注立即结束. 学习时长照常记,
        这段专注换来的休息时间是合约中获得的, 随合约一起清空 —— 不开始休息, 回到空闲."""
        if self.state == READY_BREAK:
            self.reset_to_idle()
            self._save_timer_state()
            return
        if self.state != RUNNING or self.start_wall is None:
            return
        start = self.start_wall
        elapsed = time.monotonic() - self.start_mono
        self._close_check_win()
        self.cancel_update()
        self._book_focus(start, elapsed)         # 休息时间不要
        self._rest_tag = ""
        self._mine()
        self.reset_to_idle()
        self._save_timer_state()
        self._arm_idle_nudge()

    def end_contract(self, result: str, at: datetime = None):
        c = self.contract
        if not c.active:
            return
        # 合约完成时正在休息: 不立即结束这次休息, 继续休息 (仍算合约内的休息);
        # 结算奖励照常计入积攒的休息时间 (算正常休息时间); 手动暂停会清空合约内获得的时间, 自然走完变空闲 (MECHANICS 18.1)
        rest_over = (result == "completed" and self.state == ON_BREAK
                     and self._break_cid is not None and self._break_cid == c.live.get("id"))
        if rest_over:
            self._contract_rest_over = True
        else:
            self._stop_break_for_contract()      # 结束合约前先停止休息
        if result != "completed":
            self._stop_focus_for_contract()      # 失败 / 手动终止: 正在进行的专注也立即结束 (不给休息时间)
        pending = c.reward
        rec = c.finish(result, at, by=self.sync.my_id)
        self._contract_seen = None
        reward = int(rec.get("reward") or 0)
        keep = min(int(self.break_carry), int(float(rec.get("pre_left") or 0)))
        if reward > 0 or keep != self.break_carry:
            self._set_break_carry(keep + reward)
            if self.state == IDLE:
                self.lbl_sub.config(text=self._idle_sub_text())
            self._sync_timer_buttons()
        self._log_notice(f"CONTRACT {result} level {rec.get('level')} violations {rec.get('violations')} "
                         f"reward {reward}s")
        self.check_achievements()
        self._contract_changed()
        lv = rec.get("level", 0)
        if result == "completed":
            msg = tr("危机等级 {lv}", lv=lv)
            if reward > 0:
                msg += "\n" + tr("积攒的休息时间 +{t}.", t=fmt_ms(reward))
            toast.notice(tr("⚔  合约完成"), msg)
        elif result == "abandoned":
            toast.info(tr("⚔ 行动终止") + ("\n" + tr("积攒的休息时间 +{t}.", t=fmt_ms(reward)) if reward > 0 else ""))
        else:
            msg = {"failed": tr("目标生命值归零 (违反 {n} 次), 合约终止.", n=rec.get("violations", 0)),
                   "offline": tr("{name} 离线超过 15 分钟, 合约终止.", name=self._offline_name or "?")
                   }.get(result, tr("空闲超过 15 分钟, 合约终止."))
            if pending >= 1:
                msg += "\n" + tr("按进行的比例给出 {m} 分钟休息时间 (完成给 {n} 分钟).",
                                 m=round(reward / 60), n=round(pending / 60))
            toast.notice(tr("⚔ 行动失败"), msg)
        if self.sync.peers():
            self.sync.sync_now()
        rec_terms = set(rec.get("orig") or []) | set(rec.get("terms") or [])
        kinds = [k for t, k in contract_mod.RECORD.items() if t in rec_terms]
        if kinds:
            self._after_contract(str(rec.get("id") or ""), str(rec.get("owner") or ""), kinds)

    def _after_contract(self, cid: str, owner: str, kinds):
        """录像结束 (MECHANICS 18.8): 本机是合约主人 → 合并各路并把最终录像同步给所有设备;
        不是主人 → 把本机录的片段 (各路) 自动发给主人合并 (不再逐条询问).
        注意: 这里把正在录的 recorder 交给后台线程去 stop + 等加密, 不能先 _stop_recorder 把它丢掉 ——
        否则合并 / 发送时最后一段 (录屏常常整段就一个, 结束时才加密) 还没落盘就扫不到, 结果就是 0 个."""
        if not cid:
            return
        recs = {}
        for kind in kinds:
            r = self._recorders.pop(kind, None)
            recs[kind] = r
            if r is not None:
                self._log_notice(f"RECORD {kind} stop")
        if owner and owner == self.sync.my_id:
            for kind in kinds:
                self._merge_and_share(cid, kind, recs.get(kind))
        else:
            self._send_segments(cid, owner, kinds, recs)

    def _merge_and_share(self, cid: str, kind: str, rec=None):
        """合约主人: 把本机的 + 收到的片段接成一个视频, 再把最终结果同步给所有设备. 收到新片段时会再调用 (重合并 + 重发).
        rec: 刚结束的这一路 recorder (有的话先 stop + 等最后一段加密, 再扫描片段)."""
        key = (cid, kind)
        if not cid:
            return
        if key in self._merging:
            self._merge_again.add(key)      # 正在合并时又收到片段: 合并完再合并一次 (长的录像合并要好几分钟)
            return
        self._merging.add(key)

        def run():
            try:
                if rec is not None:
                    rec.stop(12.0)                 # 录完当前片段 (Finalize)
                    rec.wait_sealed(12.0)          # 等最后一段加密进数据目录
                if not recorder.files_of(self.dm.data_dir, kind, cid) \
                        and not os.path.isdir(recorder.incoming_dir(self.dm.data_dir, cid, kind)):
                    return
                try:
                    path = recorder.merge(self.dm.data_dir, cid, kind)
                except Exception as e:      # noqa: BLE001
                    self._log_notice(f"RECORD merge {cid}/{kind} failed: {e}")
                    return
                if not path:
                    return
                self._log_notice(f"RECORD merged {cid}/{kind} -> {os.path.basename(path)}")
                self.root.after(0, lambda: self._refresh_if_visible("settings"))
                self._distribute_merged(cid, kind, path)
            finally:
                self._merging.discard(key)
                if key in self._merge_again:
                    self._merge_again.discard(key)
                    self.root.after(0, lambda: self._merge_and_share(cid, kind))
        threading.Thread(target=run, daemon=True, name=f"merge-{cid}-{kind}").start()

    def _distribute_merged(self, cid: str, kind: str, path: str):
        """合约主人: 把合并好的最终录像自动同步给所有在线的绑定设备 (不问)."""
        for s in self.sync.device_states():
            if not s.get("online") or s["id"] == self.sync.my_id:
                continue
            try:
                self.sync.send_file(s["id"], path, cid=cid, kind=kind, final=True)
            except Exception as e:      # noqa: BLE001
                self._log_notice(f"RECORD share {cid}/{kind} to {s['id']} failed: {e}")

    def _send_segments(self, cid: str, owner: str, kinds, recs=None):
        """不是合约主人的设备: 把本机录的片段 (各路) 自动发给主人合并 (MECHANICS 18.8).
        recs: 刚结束的各路 recorder (先 stop + 等加密, 否则最后一段还没落盘就发不出去)."""
        recs = recs or {}

        def go():
            for kind in kinds:                     # 先把本机还在录的收尾 (不管主人在不在线, 片段都要落盘)
                r = recs.get(kind)
                if r is not None:
                    r.stop(12.0)
                    r.wait_sealed(60.0)
            # 记进待发送的清单 (存在数据目录里, 重开 OIT 也还在), 每 30 秒试一次, 全部发成功才划掉
            with self._outbox_lock:
                box = self._outbox_read()
                if not any(e.get("cid") == cid for e in box):
                    box.append({"cid": cid, "owner": owner, "at": time.time()})
                    self._outbox_write(box)
            self._flush_outbox()
        threading.Thread(target=go, daemon=True, name=f"rec-send-{cid}").start()

    def _outbox_path(self) -> str:
        return os.path.join(self.dm.data_dir, "rec_outbox.json")

    def _outbox_read(self) -> list:
        d = config.load_json_file(self._outbox_path(), {})
        return [e for e in (d.get("items") or []) if isinstance(e, dict)] if isinstance(d, dict) else []

    def _outbox_write(self, box: list):
        if box:
            config.save_json_file(self._outbox_path(), {"items": box})
        else:
            try:
                os.remove(self._outbox_path())
            except OSError:
                pass

    def _flush_outbox(self):
        """待发送清单里的片段发给各自的合约主人 (主人当时不在线 / 传输失败的, 之后再试). 接收端按文件名覆盖, 重发不会重复."""
        if self._outbox_busy:
            return
        self._outbox_busy = True

        def run():
            try:
                with self._outbox_lock:
                    box = self._outbox_read()
                left = []
                for e in box:
                    cid, owner = str(e.get("cid") or ""), str(e.get("owner") or "")
                    if not cid or time.time() - float(e.get("at") or 0) > 7 * 86400:
                        continue                   # 太久了: 放弃 (片段仍在本机)
                    if not self._send_all(cid, owner):
                        left.append(e)
                with self._outbox_lock:
                    # 发送期间又加进来的 (另一个合约) 也留着
                    sent = {e.get("cid") for e in box} - {e.get("cid") for e in left}
                    self._outbox_write([e for e in self._outbox_read() if e.get("cid") not in sent])
            finally:
                self._outbox_busy = False
        threading.Thread(target=run, daemon=True, name="rec-outbox").start()

    def _send_all(self, cid: str, owner: str) -> bool:
        """这个合约本机录的各路片段全部发给主人了吗 (主人不在线 / 不是电脑时返回 False, 等下次)."""
        if any(str(getattr(r, "tag", "")) == cid for r in self._recorders.values()):
            return False                           # 还在录 (同一个合约)
        p = self.sync.peers().get(owner)
        if not p:
            return True                            # 已经解绑了: 不用再发
        online = any(s["id"] == owner and s.get("online") for s in self.sync.device_states())
        # 主人必须是在线的电脑 (手机不能合并); 不是就先留在本机
        if not online or not str(p.get("platform", "")).lower().startswith("win"):
            return False
        done = True
        for kind in recorder.KINDS:
            segs = [s for s in recorder.files_of(self.dm.data_dir, kind, cid) if not s.get("partial")]
            ok = 0
            for s in segs:
                try:
                    r = self.sync.send_file(owner, s["path"], cid=cid, kind=kind)
                    if r == "sent":
                        ok += 1
                    else:
                        self._log_notice(f"RECORD send {cid}/{kind} {s['name']}: {r}")
                except Exception as e:      # noqa: BLE001
                    self._log_notice(f"RECORD send {cid}/{kind} failed: {e}")
            if ok < len(segs):
                done = False
            if segs:
                self._log_notice(f"RECORD sent {ok}/{len(segs)} {kind} segments for {cid} to {owner}")
        return done

    def recording_offered(self, peer_id: str, who: str, o: dict) -> str:
        """收到别的设备发来的录像 (MECHANICS 18.8): final 是合约主人发来的最终录像 (直接存进 merged 展示);
        否则是片段 (本机是主人, 存进 incoming, 重合并 + 重同步)."""
        cid = str(o.get("cid") or "")
        kind = str(o.get("kind") or "screen")
        if not cid or kind not in recorder.KINDS:
            return "bad-request"
        final = bool(o.get("final"))
        d = recorder.work_dir(self.dm.data_dir, "recv") if final else recorder.incoming_dir(self.dm.data_dir, cid, kind)
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            return "busy"
        dest = os.path.join(d, devsync.safe_name(o["name"]))
        n = 1
        # 片段的文件名 (开始时刻 + 合约 id) 是唯一的: 同名的就是同一段 (对方重发), 直接覆盖, 不能合并两次
        while final and os.path.exists(dest):
            stem, ext = os.path.splitext(os.path.basename(o["name"]))
            dest = os.path.join(d, devsync.safe_name(f"{stem}-{n}{ext}"))
            n += 1

        def pull():
            try:
                self.sync.fetch_file(peer_id, o, dest)
            except Exception as e:      # noqa: BLE001
                self._log_notice(f"RECORD recv {cid}/{kind} failed: {e}")
                return
            if final:
                recorder.merged_file(self.dm.data_dir, cid, kind, dest)
                self._log_notice(f"RECORD got merged {cid}/{kind} from {who}")
                self.root.after(0, lambda: self._refresh_if_visible("settings"))
            else:
                self._log_notice(f"RECORD recv {cid}/{kind} segment from {who}")
                # 主人: 收到后重新合并 (稍等一下, 让同一批的几段都到了再合)
                self.root.after(4000, lambda: self._merge_and_share(cid, kind))
        threading.Thread(target=pull, daemon=True, name=f"rec-recv-{cid}").start()
        return ""

    def rec_deleted(self, cid: str, kind: str):
        """别的设备删了某个录像 (MECHANICS 18.8): 本机也删掉对应的合并录像."""
        if recorder.delete_merged(self.dm.data_dir, cid, kind):
            self._log_notice(f"RECORD deleted {cid}/{kind} (synced)")
            self.root.after(0, lambda: self._refresh_if_visible("settings"))

    def delete_recording(self, cid: str, kind: str):
        """用户在 设置 → 录像 删掉一个录像: 本机删, 并通知所有绑定设备也删 (MECHANICS 18.8)."""
        recorder.delete_merged(self.dm.data_dir, cid, kind)
        if self.sync.peers():
            threading.Thread(target=lambda: self.sync.del_recording(cid, kind), daemon=True).start()
        self._refresh_if_visible("settings")

    def _seal_extra(self):
        """记录封锁 (18.4): 完成了一次填写, 另外随机封锁两个可以封锁的格子."""
        c = self.contract
        keys = c.seal_extra(datetime.now(), text_of=self.dm.unit_text, auto=self.dm.auto_text_for_key,
                            fill=config.FILL_WINDOW)
        if not keys:
            return
        self._log_notice(f"CONTRACT seal locks {', '.join(keys)}")
        aw = self.dm.awaiting_slot()
        if aw is not None and self._slot_locked(aw, self.dm.prompt_len):
            self.dm.prompt_slot = None                      # 待填写的那格被封锁了: 不再等它
            self.update_log_badge()
        toast.warn(tr("记录封锁: 另外封锁了 {t} (不能再修改)", t=", ".join(self._cell_label(k) for k in keys)),
                   timeout=10000)
        self._contract_changed()
        if self.sync.peers():
            self.sync.sync_now()

    def _slot_locked(self, slot: datetime, length: int) -> bool:
        """这个记录格被未知变量 / 记录封锁另外封锁了 (填不了)."""
        return self.contract.hour_locked(slot, slot + timedelta(minutes=length), "", datetime.now(),
                                         config.FILL_WINDOW, self.dm.auto_text_for_key)

    @staticmethod
    def _cell_label(key: str) -> str:
        """整点记录格的键 ("日期/小时", 细化规范的半小时 "日期/小时/1") → "10-18 09:00"."""
        parts = key.split("/")
        return f"{parts[0][5:]} {int(parts[1]):02d}:{'30' if parts[2:] == ['2'] else '00'}"

    def _random_effect(self, kind: str, val: str):
        """未知变量的一次判定结果: 抽中的词条 (提示), 或者效果 (休息时间 ±10 分钟由本机改计时器)."""
        c = self.contract
        if kind == "term":
            if val in c.waiting:
                toast.warn(tr("未知变量: 抽中了 {name}, {t} 生效", name=tr(contract_mod.BY_ID[val][3]),
                              t=f"{c.waiting[val][0]:%H:%M}"), timeout=10000, clear=True)
        elif kind in ("rest_cut", "rest_add"):
            delta = contract_mod.RANDOM_REST if kind == "rest_add" else -contract_mod.RANDOM_REST
            self._contract_rest(delta)
            msg = tr("未知变量: 休息时间 +{n} 分钟") if delta > 0 else tr("未知变量: 休息时间 -{n} 分钟")
            toast.warn(msg.format(n=contract_mod.RANDOM_REST // 60), timeout=10000, clear=True)
        elif kind == "lock":
            if val:
                toast.warn(tr("未知变量: 封锁了整点记录 {t} (不能再修改)", t=self._cell_label(val)), timeout=10000, clear=True)
            else:
                toast.info(tr("未知变量: 没有可以封锁的整点记录, 无事发生"), clear=True)
        elif kind == "double":
            toast.info(tr("未知变量: 这次判定作废, 改为判定两次"), clear=True)
        elif kind == "slow":
            toast.warn(tr("未知变量: 下一次专注启用阻滞 (只减慢合约内的时间, 按 {p}% 增长)",
                          p=int(round(contract_mod.RANDOM_SLOW * 100))), timeout=10000, clear=True)
        else:
            toast.info(tr("未知变量: 无事发生"), clear=True)

    def _contract_rest(self, delta: int):
        """未知变量: 休息时间 +/- delta 秒. 正在休息时改这次休息 (先减合约中得到的, 合约开始前的放在最后),
        否则改积攒的休息时间."""
        self._mine()
        c = self.contract
        if self.state == ON_BREAK:
            left = max(0, int(self.countdown_seconds))
            new = max(0, left + int(delta))
            if new < left:
                pre_in = min(left, int(self._break_pre))
                lost = max(0, (left - new) - (left - pre_in))
                self._break_pre = max(0.0, float(self._break_pre) - lost)
            self.countdown_seconds = new
            if self.break_end_wall is not None:
                self.break_end_wall += timedelta(seconds=new - left)
            self._save_timer_state()
        else:
            self._set_break_carry(self.break_carry + int(delta))
            c.pre_cap(self.break_carry)
        self.refresh_display()

    def close_random_term(self, tid: str):
        """未知变量选中的词条: 点击关闭, 算一次违反."""
        c = self.contract
        if not c.active or tid not in c.rand:
            return
        name = tr(contract_mod.BY_ID[tid][3])
        if not toast.ask(tr("关闭未知变量选中的 {name}?", name=name) + "\n" + tr("会算作一次违反."),
                         title=tr("关闭词条"), danger=True):
            return
        self.contract_violation(tr("关闭了未知变量选中的 {name}", name=name), tid, drop=True)
        self._contract_changed()

    def contract_violation(self, why: str, tid: str, drop: bool = False):
        """违反了词条 tid (软规则): 响警报, 记一次, 这个词条降一级 (drop: 直接去掉); 到上限就结束合约."""
        c = self.contract
        if c.active and not self.dm.resetting and c.echo(tid):
            # 已经失效的词条再次触发: 照常响警报 (每个词条 2 分钟最多一次), 不扣目标生命值
            if c.echo_due(tid):
                self._play_alarm()
                self._log_notice(f"CONTRACT echo {tid}  {why}")
                toast.warn(tr("⚠ {why} · {name} 已失效, 不扣目标生命值", why=why,
                              name=tr(contract_mod.BY_ID[tid][3])), timeout=10000)
            return
        if not c.active or self.dm.resetting or (tid and not c.has(tid)):
            return
        self._play_alarm()
        old = tr(contract_mod.BY_ID[tid][3]) if tid else ""
        over, new = c.violate(tid, drop)
        if not tid:
            after = tr("违反一次")
        elif not new:
            after = tr("{a} 失效", a=old)
        elif new == tid:
            after = tr("{a} 降一级", a=old)
        else:
            after = tr("{a} 降为 {b}", a=old, b=tr(contract_mod.BY_ID[new][3]))
        self._log_notice(f"CONTRACT violation {c.violations}/{c.violation_limit}: {tid or '-'} -> {new or '-'}  {why}")
        msg = (tr("⚠ 违反合约: {why} · 目标生命值 {n} / {m}", why=why, n=c.life, m=c.violation_limit)
               + "\n" + after + "  ·  " + tr("危机等级 {lv}", lv=c.level))
        toast.error(msg)
        self.dm.add_violation(msg)               # 违规记录页 (MECHANICS 8.1): 内容照搬这段提示
        if over:
            self.end_contract("failed")
        else:
            self._refresh_if_visible("contract")
            if self.sync.peers():
                self.sync.sync_now()

    def _contract_hours(self):
        """整点记录 (MECHANICS 18.4): 细化规范的半小时补记; 合约中过了填写时限还没填写算一次违反 (由合约的主人判定)."""
        c = self.contract
        if not self.dm.hour_enabled:
            return
        now = datetime.now()
        units = c.hour_units(now)
        self.dm.finalize_halves(units, now)
        if self.state == ON_BREAK and c.mark_rest_cells(now, config.FILL_WINDOW, self.dm.unit_text,
                                                         self.dm.auto_text_for_key):
            if self.current_page == "log":
                self.pages["log"].refresh()
        # 记录封锁: 合约里填写了的整小时格记下来, 合约结束后照样锁着 (18.4)
        if c.seal_filled(now, self.dm.unit_text, self.dm.auto_text_for_key):
            self._refresh_if_visible("log")
            if self.sync.peers():
                self.sync.sync_now()
        if not self._owner_away(c.owner):
            return
        for key in c.hour_misses(now, config.FILL_WINDOW, self.dm.unit_text,
                                 self.dm.auto_text_for_key):
            parts = key.split("/")
            label = f"{int(parts[1]):02d}:{'30' if parts[2:] == ['2'] else '00'}"   # 写这一格的起点 (同整点记录页)
            self.contract_violation(tr("整点记录 {t} 超时未填写", t=label), "")
            if not c.active:
                return

    def _check_supply(self):
        """支援短缺: 休息用到合约开始前积攒的休息时间之前 3 / 1 分钟各提醒一次; 用超了算一次违反."""
        c = self.contract
        if self.state != ON_BREAK or not c.supply_watch() or c.live.get("id") != self._break_cid:
            return
        left = max(0, int(self.countdown_seconds))
        until = c.supply_until(left, self._break_pre)
        for sec in contract_mod.SUPPLY_WARN:
            key = (self._break_cid, round(c.supply_limit or 0), sec)
            if until is not None and 0 < until <= sec and key not in self._supply_warned:
                self._supply_warned.add(key)
                toast.warn(tr("支援短缺: {n} 分钟后将超出可用的合约开始前休息时间", n=sec // 60), timeout=10000, clear=True)
        if self._owner_away(c.owner):
            tid = c.supply_check(left, self._break_pre)
            if tid:
                self.contract_violation(tr("用超了合约开始前积攒的休息时间"), tid)

    def on_contract_synced(self, changes: dict):
        self.contract.merge(changes)
        self._check_contract()

    def _apply_net_bans(self):
        """乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒 (MECHANICS 18.3): 这些软件禁止联网 (打开它们不算违反).
        每秒: 过滤代理按 exe 名拦; 每 5 秒: 给正在运行的这些程序加 / 删防火墙出站规则 (不走系统代理的也拦住)."""
        c = self.contract
        on = c.active and not self.dm.resetting and not config.dev_mode_enabled()
        banned = c.banned_exes() if on else set()
        try:
            self.net.proxy.rules.banned = banned
            self.net.proxy.rules.banned_domains = c.banned_domains() if on else set()   # 这一类的网站也封锁
        except Exception:
            pass
        if (banned or self.appban.paths) and not self._appban_busy and time.monotonic() - self._appban_at >= 5:
            self._appban_at = time.monotonic()
            self._appban_busy = True
            threading.Thread(target=self._appban_sync, args=(banned,), daemon=True).start()

    def _appban_sync(self, banned):
        try:
            if banned:
                self.appban.sync(appban.process_paths(banned))
            else:
                self.appban.clear_all()
        except Exception as e:
            self._log_window(f"ERROR    appban {type(e).__name__}: {e}")
        finally:
            self._appban_busy = False

    def lift_net_ban(self, tid: str):
        """合约页上点击乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒: 解除这一类软件的网络禁令,
        扣掉词条等级那么多目标生命值 (MECHANICS 18.3). 先确认."""
        c = self.contract
        if not c.can_lift(tid):
            return
        name = tr(contract_mod.BY_ID[tid][3])
        n = c.lift_cost(tid)
        msg = tr("解除「{name}」的网络禁令?", name=name) + "\n" + \
            tr("将扣除目标生命值 {n} (现在 {life} / {m}).", n=n, life=c.life, m=c.violation_limit)
        if n >= c.life:
            msg += "\n" + tr("目标生命值会归零, 合约失败.")
        if not toast.ask(msg, title=tr("解除网络禁令"), ok_text=tr("解除"), danger=True):
            return
        if not c.can_lift(tid):
            return
        over, n = c.lift_ban(tid)
        self._log_notice(f"CONTRACT lift ban {tid} -{n}: {c.violations}/{c.violation_limit}")
        text = (tr("解除了「{name}」的网络禁令 · 目标生命值 -{n} ({life} / {m})", name=name, n=n,
                   life=c.life, m=c.violation_limit) + "\n" + tr("危机等级 {lv}", lv=c.level))
        toast.warn(text)
        self.dm.add_violation(text)              # 违规记录页 (MECHANICS 8.1): 目标生命值的变化都记下来
        self._apply_net_bans()
        if over:
            self.end_contract("failed")
            return
        self._contract_changed()
        if self.sync.peers():
            self.sync.sync_now()

    # ---- 合约 · 录像 (目光 / 刻痕, MECHANICS 18.3) ----

    def _start_recorder(self, kind: str):
        r = recorder.Recorder(self.dm.data_dir, kind, audio=self._rec_audio, tag=str(self.contract.live.get("id") or ""),
                              camera=self._local_camera_id() if kind == "camera" else "",
                              on_file=lambda path, k=kind: self._log_notice(f"RECORD {k} saved {os.path.basename(path)}"))
        self._recorders[kind] = r
        r.start()
        self._log_notice(f"RECORD {kind} start")
        return r

    def _rec_audio(self):
        """录像要不要录声音 (设置 → 录像, 默认都录): (麦克风, 系统音)."""
        return bool(self.settings.value("REC_MIC")), bool(self.settings.value("REC_SYSTEM"))

    def _warn_pick_term(self, tid: str) -> bool:
        """选中录像词条 (目光 / 刻痕) 前警告一次: 会录下什么, 加密只能在 OIT 里看, 刻痕会在各设备间传输合并.
        选不选是用户自己的决定 (MECHANICS 18.8). 返回要不要选上."""
        if tid == "gaze":
            return toast.ask(
                tr("该词条会使用摄像头记录你的行为(和声音,可在设置关闭),但记录的视频会被加密,只能在OIT终端查看,防止自动上传类似的功能泄露隐私."),
                title=tr("主管!"), ok_text=tr("确定"), cancel_text=tr("取消"), danger=True)
        if tid == "scar":
            return toast.ask(
                tr("该词条会使用录屏记录你的行为(和系统声音,可在设置关闭),但记录的视频会被加密,只能在OIT终端查看,防止自动上传类似的功能泄露隐私."),
                title=tr("主管!"), ok_text=tr("确定"), cancel_text=tr("取消"), danger=True)
        return True

    def _scar_focus(self) -> str:
        """刻痕的焦点设备 (MECHANICS 18.8): 哪台设备有操作就录哪台的屏幕, 每 20 秒判定一次;
        20 秒内两台都有操作时优先录电脑. 都没有操作时保持上一个焦点设备. 返回焦点设备的 id."""
        mono = time.monotonic()
        if self._scar_focus_id and mono - self._scar_focus_at < recorder.FOCUS_EVERY:
            return self._scar_focus_id
        self._scar_focus_at = mono
        nowms = time.time() * 1000
        win = recorder.FOCUS_EVERY * 1000
        cands = [(True, float(self.last_op), self.sync.my_id)]        # 本机 (电脑)
        plat = self.sync.peers()
        stat = {s["id"]: s for s in self.sync.peer_status()}
        for s in self.sync.device_states():
            if not s.get("online"):
                continue
            did = s["id"]
            is_pc = str((plat.get(did) or {}).get("platform", "")).lower().startswith("win")
            cands.append((is_pc, float((stat.get(did) or {}).get("last_op", 0)), did))
        active = [c for c in cands if nowms - c[1] <= win]
        if not active:
            if self._scar_focus_id in {c[2] for c in cands}:
                return self._scar_focus_id                           # 都没操作: 保持上一个焦点设备
            active = cands
        if any(c[0] for c in active):
            active = [c for c in active if c[0]]                      # 两台都有操作: 优先电脑
        self._scar_focus_id = max(active, key=lambda c: c[1])[2]
        return self._scar_focus_id

    def _stop_recorder(self, kind: str):
        r = self._recorders.pop(kind, None)
        if r is not None:
            threading.Thread(target=r.stop, daemon=True).start()     # Finalize 可能要一两秒, 不卡界面
            self._log_notice(f"RECORD {kind} stop")

    def _stop_all_recorders(self, wait: float = 0.0):
        recs = list(self._recorders.values())
        self._recorders.clear()
        for r in recs:
            r.stop(0)
        for r in recs:
            r.stop(wait)
            if wait:
                r.wait_sealed(wait)

    def recording(self, kind: str) -> bool:
        """本机此刻在录这一路吗 (设置 → 录像: 正在写的文件不能删)."""
        r = self._recorders.get(kind)
        return r is not None and r.alive

    def _rec_text(self) -> str:
        """合约页: 录像的状态 —— 各设备在录哪一路也同步显示 (MECHANICS 18.8)."""
        out = []
        cid = self.contract.live.get("id")
        # 别的设备此刻在录的几路 (同步来的状态): kind -> [设备名]
        peer_rec = {}
        try:
            online = {s["id"] for s in self.sync.device_states() if s.get("online")}
            for s in self.sync.peer_status():
                if s.get("id") not in online:
                    continue
                for k in (s.get("rec") or []):
                    peer_rec.setdefault(k, []).append(str(s.get("name") or "?"))
        except Exception:      # noqa: BLE001
            pass
        for kind, label in (("camera", tr("📷 摄像头")), ("screen", tr("🖥 屏幕"))):
            if kind not in self.contract.records():
                continue
            who = []
            if self.recording(kind):
                who.append(tr("本机"))
            who += peer_rec.get(kind, [])
            if who:
                out.append(label + " " + tr("{who} 录像中", who="、".join(who)))
                continue
            r = self._recorders.get(kind)
            skip = self._rec_skip.get(kind)
            if r is not None:
                out.append(label + " " + tr("正在开始录像…"))
            elif skip and skip[0] == cid and skip[1] == "none":
                out.append(label + " " + tr("本机没有摄像头, 不录"))
            elif skip and skip[0] == cid and skip[1] == "ask":
                out.append(label + " " + tr("等待确认录像"))
            else:
                out.append(label + " " + tr("未录像"))
        return "   ·   ".join(out)

    def _sync_recorders(self):
        """每秒: 合约要录的 (摄像头 / 屏幕) 在录, 不要录的停下. 摄像头录着录着没了 (被关掉 / 拔掉 / 被禁用),
        或者打不开, 算违反目光 (本机没有摄像头时不录, 不算). 录屏出错不算, 过一会儿再试."""
        if self._rec_pending:
            return
        c = self.contract
        want = set(c.records(self.sync.my_id)) if c.active and not self.dm.resetting else set()
        # 刻痕: 只有焦点设备录屏 (哪台有操作录哪台, 每 20 秒判定一次; 两台都有操作时录电脑, MECHANICS 18.8)
        if "screen" in want and self._scar_focus() != self.sync.my_id:
            want.discard("screen")
        # 只在专注期间录像 (设置 → 录像, 默认开启): 休息 / 空闲时停下 (不算违反), 再开始专注时另起一段, 最后拼接成一个视频
        if self.settings.value("REC_FOCUS_ONLY") and self.state != RUNNING:
            want = set()
        cid = c.live.get("id")
        for kind in recorder.KINDS:
            r = self._recorders.get(kind)
            if kind not in want:
                if r is not None:
                    self._stop_recorder(kind)
                continue
            skip = self._rec_skip.get(kind)
            if skip and skip[0] == cid:
                continue
            if r is None:
                if time.monotonic() < self._rec_retry.get(kind, 0):
                    continue
                if cid != self._rec_started_cid and self.settings.value("REC_ASK") and (kind, cid) not in self._rec_ok:
                    self._ask_recording(kind, cid)
                    continue
                self._start_recorder(kind)
                continue
            if r.state == "failed" and kind == "camera" and r.error == "no camera":
                self._recorders.pop(kind, None)
                self._rec_skip[kind] = (cid, "none")
            elif kind == "camera" and (r.state in ("failed", "lost", "stopped") or r.stalled()):
                err = r.error
                self._stop_recorder(kind)
                self._log_notice(f"RECORD camera lost: {err}")
                self.contract_violation(tr("摄像头被关闭 / 打不开"), "gaze")
            elif r.state in ("failed", "lost", "stopped"):
                self._log_notice(f"RECORD {kind} {r.state}: {r.error}")
                self._recorders.pop(kind, None)
                self._rec_retry[kind] = time.monotonic() + 30

    def _ask_recording(self, kind: str, cid):
        """别的设备开始的合约要录像, 设置里开着"录像前询问": 问一次. 拒绝算一次违反, 这个词条失效;
        ✕ 关掉: 2 分钟后再问."""
        self._rec_skip[kind] = (cid, "ask")
        what = tr("用摄像头录像") if kind == "camera" else tr("录制整个屏幕")
        tid = "gaze" if kind == "camera" else "scar"

        def yes():
            self._rec_ok.add((kind, cid))          # 这次合约答应过了: 只在专注期间录像时, 之后每次开始专注不再问
            if self._rec_skip.get(kind) == (cid, "ask"):
                self._rec_skip.pop(kind, None)
            if self.contract.live.get("id") == cid and kind in self.contract.records() and kind not in self._recorders:
                self._start_recorder(kind)

        def no():
            self._rec_skip[kind] = (cid, "declined")
            if self.contract.live.get("id") == cid:
                self.contract_violation(tr("拒绝了录像"), tid)

        def closed(_opts=None):
            if self._rec_skip.get(kind) == (cid, "ask"):
                self._rec_skip.pop(kind, None)
                self._rec_retry[kind] = time.monotonic() + 120

        dlg = toast.notice(
            tr("⚔ 合约要求录像"),
            tr("进行中的合约要求本机{what} ({term}).", what=what, term=tr(contract_mod.BY_ID[tid][3])),
            buttons=[(tr("开始录像"), yes, True), (tr("拒绝"), no, False)])
        try:
            dlg.bind("<Destroy>", lambda e: e.widget is dlg and self.root.after(100, closed), add="+")
        except Exception:
            pass

    def terminate_recording(self):
        """终止整个录制过程 (MECHANICS 18.8): 任意一端都能终止 —— 正在录的每一路各算一次违反 (对应词条失效),
        停掉本机的录像并通知所有设备也停 (违反随合约同步). 已经录下的照样保存."""
        terms = [t for t, k in contract_mod.RECORD.items() if self.contract.has(t) and k in self.contract.records()]
        if not terms:
            return
        if not toast.ask(
                tr("终止整个录制过程?"),
                title=tr("■ 终止录像"), danger=True):
            return
        cid = str(self.contract.live.get("id") or "")
        for t in terms:
            if self.contract.has(t):
                self._stop_recorder(contract_mod.RECORD[t])
                self.contract_violation(tr("终止了录像"), t)
        self.rec_stop(cid)
        if self.sync.peers():
            threading.Thread(target=lambda: self.sync.stop_recording(cid), daemon=True).start()
            self.sync.sync_now()

    def rec_stop(self, cid: str):
        """别的设备 (或本机) 终止了录制 (MECHANICS 18.8): 停掉本机正在录的各路, 这个合约不再自动重开 (违反随合约同步)."""
        cur = str(self.contract.live.get("id") or "")
        if cid and cur and cid != cur:
            return
        for kind in recorder.KINDS:
            if kind in self._recorders:
                self._stop_recorder(kind)
            self._rec_skip[kind] = (cur, "stopped")

    def _record_lines(self, terms) -> list:
        kinds = [contract_mod.RECORD[t] for t in terms if t in contract_mod.RECORD]
        out = []
        if "camera" in kinds:
            out.append(tr("📷 合约期间用摄像头录像"))
        if "screen" in kinds:
            out.append(tr("🖥 合约期间录制整个屏幕"))
        return out

    def _check_contract_windows(self, ws):
        """通道阻塞: 专注中任务栏上的窗口 (开着超过 2 分钟的, ws) 超过词条的上限 (不关窗口, 只算违反; 越过的那一下算一次).
        同一个程序的多个窗口算一个, 浏览器的每个窗口各算一个."""
        c = self.contract
        _tid, lim = c.window_watch()                 # 已经失效的按失效前的上限 (再次超过只响警报)
        if not lim or self.dm.resetting:
            return
        if self.state != RUNNING:                    # 只在专注时段算, 离开专注重新计
            c.window_reset()
            return
        if ws is None:
            return
        tid = c.window_check(window_guard.channel_count(ws, contract_mod.BROWSER_EXES))
        if tid:
            self.contract_violation(tr("窗口超过 {n} 个", n=lim), tid)

    def _contract_quick(self):
        """抑制: 用了快速访问."""
        tid = self.contract.quick_check()
        if tid:
            self.contract_violation(tr("使用了快速访问"), tid)

    def _play_alarm(self):
        """违反合约的警报 (sounds/target_alarm.wav); 放不出来时蜂鸣."""
        def run():
            if _play_audio_file(ALARM_WAV):
                return
            try:
                import winsound
                for _ in range(3):
                    winsound.Beep(1400, 140)
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    def _sync_bonus_button(self):
        """合约中 / 外出中 / 本周已经领完时隐藏"我累了"按钮 (下周一再出现)."""
        if not hasattr(self, "lbl_bonus"):
            return
        if self.contract.active or self.behavior.active() is not None or self._bonus_left_this_week() <= 0:
            hide_pack(self.lbl_bonus)
        else:
            show_pack(self.lbl_bonus, side="left", padx=(24, 0))

    # ---- 配置期 ----

    def _ask_username(self):
        def done(name):
            self.dm.set_username(name)
            self._log_notice(f"USERNAME set: {name}")
            if self._first_run:
                self.root.after(200, self._show_welcome)
        toast.prompt_name(tr("👤  你的名字"), tr("初次见面.怎么称呼你?"), done, limit=USERNAME_MAX)

    def _show_welcome(self):
        hours = int(config.SETUP_HOURS)
        msg = (tr("联网要先申请,等过冷静期才连上,每天有额度.")
               + "\n\n" + tr("接下来{h}小时是配置期:所有修改立即生效.").format(h=hours)
               + "\n" + tr("之后每次修改都要等 48 小时.")
               + "\n\n" + tr("第一次可以在 设置 → 日常 选择行为模式 (这一次立即生效)."))
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
        self.check_achievements()

    def _check_setup_period(self):
        """配置期自然到期时提示一次, 并刷新界面上的标记."""
        active = self.settings.in_setup()
        if self._setup_was_active and not active:
            self._setup_was_active = False
            self._log_notice("SETUP period ended (time is up)")
            self._after_setup_change()
            toast.notice(tr("⏳  配置期结束了"),
                         tr("之后的修改都要等 48 小时."))

    # ---- 成就 (MECHANICS 17) ----

    def check_achievements(self):
        """每 5 秒 (以及启动时) 判定一遍; 新达成的弹出提示, 浮在窗口上直到手动关掉."""
        if self.shutting_down or self.dm.resetting:
            return
        study = {d: self.dm._study_all(d) for d in set(self.dm.study_data).union(
            *((led.get("study") or {}) for led in (self.dm.peer_ledgers() or [])))}
        facts = achievements.Facts(datetime.now(), self.ach.first, self.settings.in_setup(),
                                   study, self.dm.hour_days, self.contract.history,
                                   carry=self.break_carry)
        self._after_achievements(self.ach.check(facts))

    def on_achievements_synced(self, changes: dict):
        """别的设备达成的成就 (设备同步): 本机也算达成, 同样弹出提示."""
        self._after_achievements(self.ach.merge(changes))

    def _after_achievements(self, new):
        if new and "settings" in self.pages:
            self.pages["settings"].refresh_achievements()
        if new and self.current_page == "ach" and "ach" in self.pages:
            self.pages["ach"].refresh()
        self.ach_popups.sync()

    def _open_achievements(self):
        try:
            if self.root.state() in ("iconic", "withdrawn"):
                self.root.deiconify()
            self.root.lift()
        except tk.TclError:
            pass
        self.show_page("ach")

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
        if "BEHAVIOR_MODE" in due:
            self.behavior.sync_mode()
            self._behavior_tick()
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
        # 整点记录关着, 合约选了整点记录的词条: 这次合约中照样开启, 结束后关闭 (MECHANICS 18.4)
        want = bool(self.settings.value("HOUR_LOG_ENABLED")) or self.contract.hour_forced()
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
        # 提前启动藏起来的窗口要放出来, 不然关掉 OIT 以后它们一直藏着
        try:
            self.prestart.stop()
        except Exception:
            pass
        # 绑定了其他设备: 关闭程序就结束共享的专注, 并马上告诉其他设备 (MECHANICS 8.2)
        ended = False
        if self.state == RUNNING and self.start_mono is not None and self.sync.peers() \
                and not self.dm.resetting and not self._close_pending \
                and not getattr(self, "_update_restart", False):
            self._end_focus_for_close()
            ended = True
        # 专注 / 休息中关闭: 状态留在文件里, 下次打开时接着算 (见 _recover_focus / _recover_break)
        self._save_focus_now()
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
        self._stop_all_recorders(wait=8.0)     # 把正在录的文件写完 (Finalize)
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
        if key in ("BEHAVIOR_MODE", "OUTING_TEXT", "REST_TEXT") and \
                getattr(self, "behavior", None) is not None:
            self.behavior.sync_mode()
            self._behavior_tick()
            if "log" in self.pages:
                self.pages["log"].refresh()
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


    # ---- 指令 (Prescript, 都市之意主题独占) ----

    def _prescript_enabled(self) -> bool:
        return getattr(config, "THEME", "") == "urban_will"

    def _today_hours_live(self) -> float:
        """今天的专注小时数, 包括正在进行、还没记进 study_log 的这一段."""
        hours = float(self.dm.get_today_hours())
        if self.state == RUNNING and self.start_wall is not None:
            now = datetime.now()
            start = max(self.start_wall,
                        now.replace(hour=0, minute=0, second=0, microsecond=0))
            if now > start:
                hours += (now - start).total_seconds() / 3600.0
        return hours

    def _prescript_tick(self):
        self.prescript_job = None
        try:
            if not self.dm.resetting:
                self.prescripts.tick()
        except Exception as e:
            self._log_window(f"ERROR    prescript tick {type(e).__name__}: {e}")
        if not self.shutting_down:
            self.prescript_job = self.root.after(1000, self._prescript_tick)

    def _exit_prescript(self):
        back = getattr(self, "_prescript_back", "timer")
        self.show_page(back if back in self.pages and back != "prescript" else "timer")

    def _prescript_remaining_rest(self) -> float:
        """用户剩余的休息时间 (秒): 正在休息是这次休息的倒计时, 否则是积攒的休息时间 (指令 III 按它的 50% 算)."""
        if self.state == ON_BREAK:
            return max(0, int(self.countdown_seconds))
        return max(0, int(self.break_carry))

    def _prescript_changed(self):
        page = getattr(self, "pages", {}).get("prescript")
        if page is None:
            return
        try:
            page.refresh()
        except Exception:
            pass

    def _grant_prescript_rest(self, seconds: int, code: str = ""):
        """指令给的休息时间: 正在休息就加进这次休息, 否则存进积攒的休息时间."""
        seconds = max(0, int(seconds))
        if seconds <= 0 or self.dm.resetting:
            return
        self._log_notice(f"PRESCRIPT +{seconds}s {code}".rstrip())
        if self.state == ON_BREAK:
            self.countdown_seconds += seconds
            if self.break_end_wall is not None:
                self.break_end_wall += timedelta(seconds=seconds)
            self._save_timer_state()
        else:
            self._set_break_carry(self.break_carry + seconds)
            if self.state == IDLE and hasattr(self, "lbl_sub"):
                try:
                    self.lbl_sub.config(text=self._idle_sub_text())
                except Exception:
                    pass


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

    def relay_updates(self, manual: bool = False, only: str = None) -> bool:
        """only: 只发给这一台 (它请本机发过去的). 返回 False: 正在发送 / 没有设备."""
        if self._relaying:
            if manual:
                toast.info(tr("正在发送…"))
            return False
        peers = {pid: p for pid, p in self.sync.peers().items()
                 if p.get("platform") in ("windows", "android") and (only is None or pid == only)}
        if not peers:
            if manual:
                toast.info(tr("没有绑定的设备."))
            return False
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
        return True

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
        ).pack(side="left", anchor="w")
        # 文件通道 (MECHANICS 15.6): 把文件 / 图片发给绑定的设备
        ttk.Button(hdr, text=tr("📁  文件通道"), command=self.file_channel.open).pack(side="right")

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
        # 合约的侵蚀: 冷静期 ×2 / ×3
        ok, msg = self.net.request(minutes, reason, cooldown_rate=rate, cooldown_mult=self.contract.cooldown_factor())
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
        self._contract_quick()

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
        self._contract_quick()

    def _net_set_status(self, text: str, color: str):
        # 状态后面写上当前的断网方式, 一眼能看出是不是"代理-全屏蔽 (Proxy block-all)"
        text = f"{text}  ·  {tr(self.settings.display_value('NET_MODE', config.NET_MODE))}"
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
        if abs(n.outing_factor - 1.0) > 1e-9:
            bits.append(tr("外出: 今天只能用总额度的 {p}% (总额度 {full} → 能用 {total})").format(
                p=f"{n.outing_factor * 100:g}", full=fmt_hm(n.full_total), total=fmt_hm(n.today_total)))
        if n.outing_cooldown < 1.0:
            bits.append(tr("外出中: 联网冷静期 ×{f}").format(f=f"{n.outing_cooldown:g}"))
        if n.bonus_all > 0:
            bits.append(tr("今天已借 {bonus},明天将扣 {cost}(明天剩余 {left})").format(
                bonus=fmt_hm(n.bonus_all), cost=fmt_hm(n.tomorrow_used_all),
                left=fmt_hm(n.tomorrow_remaining)))
        if n.debt_all > 0:
            bits.append(tr("今天要先还昨天借的 {debt}").format(debt=fmt_hm(n.debt_all)))
        if n.gift_all > 0:
            bits.append(tr("今天合约奖励的联网额度 {t}", t=fmt_hm(n.gift_all)))
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
        self._beat_power()
        self._check_desktop()
        if time.monotonic() - self._battery_at >= 20:   # 本机电量: 发给其他设备 (MECHANICS 15.4)
            self._battery_at = time.monotonic()
            self.battery = devsync.battery_percent()
        if time.monotonic() - self._autostart_at >= 300 and self.autostart_state != "unknown":
            self._autostart_at = time.monotonic()        # 开机自启动有没有被用户关掉: 5 分钟看一次
            threading.Thread(target=self._check_autostart, daemon=True).start()
        try:
            self._check_adapter_overlap()
            self._check_contract_conflicts()
        except Exception as e:
            self._log_window(f"ERROR    adapter overlap {type(e).__name__}: {e}")
        self.sync.track_offline()               # 每秒: 同步失败的设备马上算离线 (MECHANICS 8.4)
        if time.monotonic() - self._outbox_at >= 30:   # 没发出去的录像片段: 每 30 秒再试一次 (18.8)
            self._outbox_at = time.monotonic()
            if os.path.exists(self._outbox_path()):
                self._flush_outbox()
        try:
            self._check_contract()
        except Exception as e:
            self._log_window(f"ERROR    contract {type(e).__name__}: {e}")
        self._sync_bonus_button()               # "我累了": 本周领完了就藏起来, 新的一周 (或同步来的领取记录) 随之变化
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

    def _check_offline_chime(self):
        """每 5 秒: 有绑定的设备刚离线时, 在焦点设备 (最后有人操作的那台) 上响一次提示音 (MECHANICS 15.2)."""
        try:
            peers = self.sync.peers()
            sts = {st["id"]: st for st in self.sync.device_states() if st["id"] in peers and not st["online"]}
            now_off = set(sts)
        except Exception:
            return
        was = getattr(self, "_offline_seen", None)
        self._offline_seen = now_off
        if was is None or not (now_off - was) or not self.sync.is_latest_active():
            return                                   # 刚打开时已经离线的不响
        names = ", ".join(str(peers[i].get("name") or "?") for i in sorted(now_off - was))
        # 记下为什么算离线 (最近一次同步失败的原因, 多久没有它的消息), 以后好查
        why = "; ".join(f"{peers[i].get('name') or '?'}: err={sts[i].get('err') or '-'} "
                        f"silent={int((time.time() * 1000 - float(sts[i].get('seen') or 0)) / 1000)}s"
                        f"{' outdated' if sts[i].get('outdated') else ''}" for i in sorted(now_off - was))
        self._log_notice(f"SYNC offline {names} ({why})")
        self._play_chime()
        toast.warn(tr("{name} 离线了.", name=names), clear=True)   # 离线消息黑雾也不遮 (18.3)

    def _check_unbind(self):
        """每 5 秒: 冷静期到了的解除绑定就解除, 并请那台设备清空全部数据 (MECHANICS 15.1)."""
        try:
            for name in self.sync.check_unbind():
                self._log_notice(f"SYNC unbind done {name}")
                toast.info(tr("已解除和 {name} 的绑定, {name} 上的数据会被清空 (离线时等它上线).", name=name))
        except Exception as e:
            self._log_window(f"ERROR    unbind {type(e).__name__}: {e}")

    def _on_confirm_change(self, *_):
        ok = self.var_confirm.get().strip() == "RESET"
        self.btn_reset.state(["!disabled" if ok else "disabled"])

    def refresh_reset(self):
        self.var_confirm.set("")

    def on_reset(self):
        if self.var_confirm.get().strip() != "RESET":
            return
        if not toast.ask(
            tr("这将永久删除全部数据,不可撤销.") + ("\n" + tr("绑定的设备也会清空数据 (绑定保留).")
                                                    if self.sync.peers() else ""),
            title=tr("重置确认"), danger=True
        ):
            return
        self.do_reset()

    def do_reset(self, wipe_id: str = None, exclude=(), by: str = "", leave: bool = False):
        """重置: 先请绑定的设备也清空 OIT 的数据 (绑定保留), 解除网络限制 (还原系统代理, 启用网卡),
        删除全部数据 (只留下设备同步的绑定), 再重新打开程序. 新进程找不到设置文件, 就像第一次安装:
        弹出介绍窗口, 开始配置期, 网络按默认设置接管.
        wipe_id / exclude / by: 是别的设备 (名字 by) 请本机清空的 —— 同一个请求转告给本机绑定的其他设备.
        leave: 是它解除了和本机的绑定 (15.1): 只重置本机, 不转告其他设备 (和它们的绑定照样留着)."""
        if self.dm.resetting:
            return
        self.dm.resetting = True
        self._cancel_jobs()
        self._close_check_win()
        kept = None
        try:
            if not leave:
                self.sync.send_wipe(wipe_id or self.sync.new_wipe_id(), exclude=exclude)   # MECHANICS 9
            kept = self.sync.kept_state(by, leave)
        except Exception:
            pass
        self.sync.stop()
        self._stop_all_recorders(wait=8.0)
        self.net.release()
        self.dm.erase_all_data()
        if kept:
            try:
                devsync.write_state(self.sync.file, kept)
            except OSError:
                pass
        self.restart_requested = True      # main.py 在主循环结束后重新打开程序
        self.root.destroy()
