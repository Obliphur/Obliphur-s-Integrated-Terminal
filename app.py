import os
import time
import atexit
import threading
import tkinter as tk
from tkinter import font as tkfont, ttk

import config
import i18n
import toast
import window_guard
import appban
import clockguard
import continuity
import closedtest
import devsync
import updater
import achievements
import contract as contract_mod
import recorder
import behavior
import prestart
import media_pause
import prescript
import comm as comm_mod
import alarm as alarm_mod
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
                   ContractPage, AchievementsPage, AchievementPopups, PrescriptPage, CommPage)
from pages.file_channel import FileChannel, fmt_size
from pages.widgets import (frozen, LoadingCover, FocusGlow, cfg, flavor,
                           show_pack, hide_pack)
from app_parts.app_comm import CommMixin
from app_parts.app_sync import SyncMixin
from app_parts.app_contract import ContractMixin
from app_parts.app_contract_media import ContractMediaMixin
from app_parts.app_timer import TimerMixin
from app_parts.app_break import BreakMixin
from app_parts.app_daily import DailyMixin
from app_parts.app_net import NetMixin
from app_parts.app_achievements import AchievementsMixin
from app_parts.app_prescript import PrescriptMixin
from app_parts.app_update import UpdateMixin
from app_parts.app_settings import SettingsMixin
from app_parts.app_window import WindowMixin
from app_parts.app_pages import PagesMixin
from app_parts.app_guard import GuardMixin
from app_parts.app_misc import MiscMixin


from app_parts.app_base import (_blend, _enable_per_monitor_dpi, _hours_text, _play_audio_file,
                      tired_text, URBAN_NOTICE_FLAG, URBAN_NOTICE_HINTS)


class BreakTimerApp(CommMixin, SyncMixin, ContractMixin, ContractMediaMixin,
                    TimerMixin, BreakMixin, DailyMixin, NetMixin,
                    AchievementsMixin, PrescriptMixin, UpdateMixin, SettingsMixin,
                    WindowMixin, PagesMixin, GuardMixin, MiscMixin):
    def __init__(self, root: tk.Tk):
        self.root = root
        self.shutting_down = False
        self._rebuilding = False

        _enable_per_monitor_dpi()
        self.ui_scale = config.detect_ui_scale()
        config.UI_SCALE = self.ui_scale
        if not (root.winfo_exists() and root.winfo_width() > 1):
            pass

        root.title(APP_NAME + closedtest.tag())
        try:
            root.iconbitmap(default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "oit.ico"))   # 图标 (make_icon.py)
        except Exception:
            pass
        root.resizable(True, True)              # 可以最大化 / 拖边缘改大小 (最小尺寸见 _apply_geometry)
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
        self._break_warned = False         # 这一次休息已经提醒过"只剩 1 分钟"
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
        self._focus_from_break = False  # 这一下开始专注是从休息里直接开始的 (提前启动要接着, MECHANICS 20)
        # 绑定了其他设备, 上次在共享的专注中 OIT 被关掉了: 等第一次同步后决定要不要终止这段专注 (MECHANICS 8.2)
        self._close_pending = None
        # 关机 (MECHANICS 15.4): 收到系统的关机指令 (power_off), 本机电量 (只在用电池时, 其他时候 None)
        self.power_off = False
        self.battery = None
        self._battery_at = 0.0
        # 开机自启动 (MECHANICS 3.4): 任务计划程序里的任务开着 ("on") 才算; 关机前提 (15.4)
        self.autostart_state = "unknown"
        self._autostart_at = 0.0
        self._link_at = 0.0
        self._cont_at = 0.0
        self._purge_at = time.monotonic()
        self._cont_broken = False
        self._src_at = time.monotonic()   # 源码被非更新地改动的检查 (srcguard.py, MECHANICS 9.7): 每 5 分钟
        self.clock = None            # 系统时间被改的检测 (clockguard.py, MECHANICS 9.6); 开发版本里是 None
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
        self.sleep_since = None       # 睡眠 (MECHANICS 23): 开始睡眠的时刻; None = 醒着
        self._sleep_cover = None      # 睡眠时盖住整个窗口的页面
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
        self.dm.sleep_live = lambda: self.sleep_since
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
        # 不去标记 / 藏: 黑名单模式下所有程序 (没被禁的本来就放行, 被禁的不能借提前启动绕过); 其它模式只管应用白名单里的
        self.prestart.keep = lambda exe: (getattr(config, "FILTER_MODE", "blacklist") == "blacklist"
                                          or self.net.proxy.rules.in_white_apps(exe))
        self.prestart_list = prestart.ListStore(self.dm.data_dir)   # 提前启动清单的添加 / 删除 (MECHANICS 20)
        self.prestart_list.blocked = lambda: self.dm.resetting
        # 触发隐藏的窗口 (设置 → 窗口): 程序名命中就让程序提前启动藏起来, 但不给联网豁免 (MECHANICS 20)
        self.hidden_windows = prestart.TriggerStore(self.dm.data_dir)
        self.hidden_windows.blocked = lambda: self.dm.resetting
        self.prestart.triggers = lambda: self.hidden_windows.programs
        # 合约 (MECHANICS 18): 先把盖过设置的项套上, 断网方式才会一开始就对
        self.contract = contract_mod.Contract(self.dm.data_dir)
        self.contract.blocked = lambda: self.dm.resetting
        self._apply_contract_overrides()
        if config.dev_auto():
            config.DEV_MODE = True      # 本地开发版本: 自动开发者模式, 启动时不断网 (MECHANICS 3.5); 子端之后跟着母端
        self.net = NetGuard(self.dm.data_dir)
        self.net.on_tamper = self._on_net_tamper
        self.net.proxy.rules.preset_keys = self._preset_rule_keys
        self.net.quota_factor = self.behavior.quota_factor
        self.net.cooldown_factor = self.behavior.cooldown_factor
        # 设备同步 (MECHANICS 15): 其他设备的账并进额度和统计
        self.sync_runner = devsync.MainRunner()
        self.sync = devsync.SyncService(self.dm.data_dir, PcLocal(self), self.sync_runner)
        self.dm.peer_ledgers = self.sync.peer_ledgers
        self.dm.offline_spans = self._offline_spans         # 正在进行的那段: 有设备离线的部分画成空白 (外出期间除外)
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
            on_complete=self._prescript_completed,
            hour_log_fn=self._prescript_hour_log,
            focus_log_fn=self._prescript_focus_log,
            peers_on_page_fn=lambda: self.sync.peer_flag("prescript"),
            page_event_fn=self._prescript_page_event,
        )
        self.prescript_job = None
        # 沟通 (MECHANICS 24): 人设文件夹和数据文件夹并排, 不加密
        self.comm = comm_mod.Comm(self.dm.data_dir, os.path.join(self.dm.low_dir, "OIT_personas"))
        # 闹钟 (1.0.359, MECHANICS 25): 本机的闹钟配置 / 响铃线程; 共时闹钟的设置在 comm 里 (随设备同步)
        self.alarm = alarm_mod.Alarm(self.dm.data_dir)
        self.ringer = alarm_mod.Ringer(self.dm.data_dir)
        self._alarm_win = None
        self.comm.devices = self._comm_devices
        self.comm.snapshot = self._comm_snapshot
        self.comm.on_change = self.update_comm_badge
        self.comm.data_fn = self._comm_data
        self.comm.info_fn = self._comm_info
        self.comm.user_day_fn = self._comm_user_day          # 人设里的 [condition] 判定用: 某一天的 (专注秒, 空闲秒)
        self.comm.share_fn = self._comm_share            # share data (MECHANICS 24.12): 某一页上用户和对方的数据
        self.comm.on_share_open = lambda: self.show_page("timer")      # 用户接受了对方的 share data 邀请: 跳到计时器页
        self.comm.on_share_notice = lambda text: toast.info(tr(text))      # 对方拒绝 / 没回复用户的 share data 请求
        # 设备同步 (MECHANICS 24.6): 聊天记录随同步; 只有最后有人操作的设备调用 AI
        self.comm.device = lambda: self.sync.my_id
        self.comm.leader = self._comm_leader
        self.comm.on_sync = lambda: self.sync.sync_now() if self.sync.peers() else None
        self.comm.fog = lambda: bool(self.contract.fog())
        # 用户只能在休息 / 空闲时用沟通 (专注 / 睡眠 / 外出中不能用); 对方在用户睡眠时仍可主动发, 只是先藏起来
        self.comm.blocked = self._comm_blocked
        self.comm.cut_exempt = config.dev_mode_enabled        # 开发者模式: 不受对方 [cut off] 限制
        self.comm.ai_blocked = self._comm_ai_blocked
        self.comm.state_fn = self._comm_state
        self.comm.break_end_fn = self._comm_break_end
        self.comm.hard_block = lambda: self.contract.has("silent") and not config.dev_mode_enabled()
        self.comm.on_proactive = self._comm_ping
        self.comm.on_joint_due = self._joint_due
        self.comm.contract_fn = lambda: self.contract.active       # 日程跟随: 合约期间自动关闭
        self.comm.on_sf = self._sf_ask
        self._sf_win = None              # 日程跟随: 正在显示的提示窗
        self._joint_pick = False        # 联合行动: 正在合约页选词条 (点"确定选择"回到沟通页)
        self._joint_break_prev = None    # 联合行动: 上一秒用户是不是在休息 (None = 还没看过): 开始 / 结束休息时通知对方
        self._inv_id = ""                # 对方 (AI) 发起的联合行动: 已经跳到合约页的那一个邀请的编号
        self._inv_prev = []              # 邀请把合约页的词条换成对方选的之前, 用户自己选的词条 (拒绝 / 错过后换回去)
        self._rules_win = None
        self._marks_shown = None        # 指令标记现在画在哪些按钮上 (None = 还没画过)
        self._mark_img = None
        self._mark_labels = {}
        self._last_complete_sound = 0.0
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
        # 先定好窗口大小和位置 (屏幕正中), 再在加载页后面一页一页地建界面
        self._apply_geometry(initial=True)
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
        sandbox = closedtest.active()       # 封闭测试 (MECHANICS 3.6): 设备同步 / 防火墙 / 开机自启动都不动, 不影响主设备
        try:
            if not sandbox:
                self.sync.start()
        except Exception as e:
            self._log_window(f"ERROR    sync start {type(e).__name__}: {e}")
        self._pump_sync()
        # 防火墙放行同步端口, 否则别的设备找不到本机 / 连不进来 (MECHANICS 15.3). netsh 较慢, 放到后台
        if not sandbox:
            threading.Thread(target=self._check_firewall, daemon=True).start()
        # 合约的网络禁令: 上次留下的防火墙规则 (例如崩溃时没来得及删) 先删掉, 之后按合约重新加
        if not sandbox:
            threading.Thread(target=self._appban_sync, args=(set(),), daemon=True).start()
        # 系统时间被改的检测: 数据核对之后才有 (开发版本不核对, 也不检测)
        if continuity.current is not None:
            try:
                self.clock = clockguard.ClockGuard(self.dm.data_dir, log=lambda t: self._log_notice("CLOCK " + t))
                self.clock.start()
            except Exception as e:
                self.clock = None
                self._log_window(f"ERROR    clockguard {type(e).__name__}: {e}")
        # 录像 (目光 / 刻痕): 上次没来得及加密的 (崩溃时留在临时文件夹里的, 旧版本留在数据目录里的 mp4) 先加密起来
        threading.Thread(target=lambda: (recorder.seal_leftovers(self.dm.data_dir), self._purge_recordings()), daemon=True).start()
        # 开机自启动: 默认开启 (第一次运行时建好), 被禁用 / 删掉了就重建 (MECHANICS 3.4)
        if sandbox:
            self.root.after(1500, lambda: toast.notice(tr("🧪  封闭测试"), tr("数据全部放在 {p}, 不会影响主设备的数据. 设备同步已关闭.", p=closedtest.low_dir())))
        else:
            threading.Thread(target=self._check_autostart, args=(True,), daemon=True).start()
        if config.dev_mode_enabled():
            self.root.after(800, self._auto_dev_ready)
        if getattr(config, "UPDATE_CHECK", 1):
            self.root.after(3000, lambda: self.check_update(auto=True))

        self.root.bind("<Map>", self._on_window_map, add="+")
        self.root.bind("<Unmap>", self._on_window_unmap, add="+")
        self.root.bind_all("<F11>", self._toggle_fullscreen, add="+")      # F11: 全屏 / 退出全屏
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
        self._recover_sleep()
        if not self.dm.username:
            # 第一次打开 (或者还没填过用户名的旧版本升级上来): 先填用户名, 再显示介绍窗口
            self.root.after(600, self._ask_username)
        elif self._first_run:
            self.root.after(600, self._show_welcome)
        unb = self.sync.pop_unbound_from()
        if unb:
            # 本机解除了和别的设备的绑定: 本机的数据清空了, 对方的数据不受影响 (MECHANICS 15.1)
            self.root.after(900, lambda: toast.notice(tr("🔄  数据已清空"), tr("已解除和 {name} 的绑定, 本机的数据已经清空. {name} 的数据不受影响.", name=unb)))
        who, leave = self.sync.pop_wiped_by()
        if who:
            # 别的设备重置 / 卸载时请本机清空了数据 (MECHANICS 9); 别的设备解除了和本机的绑定 (15.1)
            msg = (tr("{name} 解除了和本机的绑定, 本机的数据已经清空.", name=who) if leave
                   else tr("{name} 重置了 OIT, 本机的数据也一起清空了.", name=who))
            self.root.after(900, lambda: toast.notice(tr("🔄  数据已清空"), msg))
        self.root.after(800, self.check_achievements)
