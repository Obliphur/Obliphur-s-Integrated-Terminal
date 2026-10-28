

import sys
import os
import re
import math
import threading
import tkinter as tk
from tkinter import messagebox
from datetime import datetime, timedelta


if sys.platform == "win32":
    import ctypes
    try:
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
    except Exception:
        pass


IDLE, RUNNING, READY_BREAK, ON_BREAK = range(4)


DAILY_LIMIT = 2 * 3600       
COOLDOWN = 5 * 60            
ENFORCE_EVERY = 60           
TICK_CLAMP = 30              # 联网计时每一步最多计入的秒数(睡眠/卡顿后不会一次扣一大段)

QUICK_MULTIPLIER = 2.0       # 快速访问: 免冷静期, 每联网 1 秒消耗 2 秒今日额度
LOAN_RATE = 2.0              # 紧急借贷: 今日额度 +1 秒, 明天额度 -2 秒
BREAK_COOLDOWN_RATE = 0.4    # 休息中提交的普通申请(时长不超过剩余休息): 冷静期 ×0.4
BREAK_CARRY_RATE = 0.7       # 暂停休息(含关闭程序): 剩余休息时间 ×0.7 计入下一次休息
BREAK_TIME_SCALE = 1.0       # 休息时间倍率: 建议休息时长 F(t) 再乘以它

# 紧急借贷借掉明天额度的这个比例以上时: 提醒增加休息时间, 并让下一次修改每日联网额度免等待
LOAN_WARN_SHARE = 0.5

# 休息结束后迟迟不开始下一轮: 结束后 IDLE_NUDGE_FIRST 秒弹出提醒,
# 之后仍是空闲就每隔 IDLE_NUDGE_EVERY 秒再试一次 (提醒窗还开着就不重复弹)
IDLE_NUDGE_FIRST = 5 * 60
IDLE_NUDGE_EVERY = 60 * 60

# 专注中每满 FOCUS_CHECK_EVERY 秒弹出一个"还在吗"的提醒窗; 提醒窗 FOCUS_CHECK_GRACE 秒内
# 没有被手动关掉, 就自动结束专注: 从最后一次确认 (关掉提醒窗) 到自动结束的这一段
# 只按 FOCUS_CHECK_PENALTY 计 (统计里只算这一段的前这一部分), 之前确认过的部分照常计
FOCUS_CHECK_EVERY = 60 * 60
FOCUS_CHECK_GRACE = 30 * 60
FOCUS_CHECK_PENALTY = 0.3

# 绑定的设备非关机离线累计超过这么多秒 (MECHANICS 15.4): 自动结束专注 (关机算在线, 不计)
OFFLINE_FOCUS_END = 5 * 60

# 专注状态每 FOCUS_SAVE_EVERY 秒写进 timer_state.json, 程序异常退出也不会丢 (最多丢这么久).
# 下次打开时: 距最后一次保存不超过 FOCUS_RESUME_GAP 秒就接着计时, 否则记到最后一次保存为止
FOCUS_SAVE_EVERY = 4 * 60
FOCUS_RESUME_GAP = 5 * 60
# 绑定了其他设备时 (MECHANICS 8.2): 专注中每 FOCUS_ALIVE_EVERY 秒就写一次, 以便知道 OIT 是什么时候被关掉的.
# OIT 关掉超过 CLOSED_GAP 秒 (例如被强行结束; 更新后重启这类很快重开的不算) 再打开时, 共享的专注被终止,
# 关掉的这一段记为空闲. 先等 CLOSED_DECIDE_WAIT 秒内的第一次同步, 看其他设备是不是已经结束了这段专注
FOCUS_ALIVE_EVERY = 30
CLOSED_GAP = 2 * 60
CLOSED_DECIDE_WAIT = 60

# Timer 页的小按钮: BONUS_BREAK_WINDOW 秒内连点 BONUS_BREAK_CLICKS 次,
# 获得 BONUS_BREAK_SECONDS 秒休息时间; 每周 (周一 0 点起) 最多 BONUS_BREAK_WEEKLY 秒
BONUS_BREAK_CLICKS = 10
BONUS_BREAK_WINDOW = 60
BONUS_BREAK_SECONDS = 20 * 60
BONUS_BREAK_WEEKLY = 2 * 3600
BONUS_TIRED_TIMES = 5          # 一周内第 5 次领取: 弹窗劝调休息时间倍率, 它的下一次修改免等待

# 按星期调整每日联网额度: 当天额度 = DAILY_LIMIT × 当天的倍率 (周一 ~ 周日)
WEEKDAY_KEYS = ("QUOTA_MULT_MON", "QUOTA_MULT_TUE", "QUOTA_MULT_WED", "QUOTA_MULT_THU",
                "QUOTA_MULT_FRI", "QUOTA_MULT_SAT", "QUOTA_MULT_SUN")
QUOTA_MULT_MON = 1.0
QUOTA_MULT_TUE = 1.0
QUOTA_MULT_WED = 1.0
QUOTA_MULT_THU = 1.0
QUOTA_MULT_FRI = 1.0
QUOTA_MULT_SAT = 1.0
QUOTA_MULT_SUN = 1.0


def quota_multiplier(day) -> float:
    """某一天 (date / datetime) 的联网额度倍率."""
    try:
        return max(0.0, float(globals().get(WEEKDAY_KEYS[day.weekday()], 1.0)))
    except (TypeError, ValueError):
        return 1.0


def daily_quota(day) -> float:
    """某一天的联网额度 (秒) = 每日联网额度 × 当天倍率."""
    return float(globals().get("DAILY_LIMIT", 0)) * quota_multiplier(day)


# 睡觉时间 (设置 → 日常): 周一到周日各自可以改, 默认 23:00 (从 0:00 起的分钟数).
# 睡觉时间之后 SLEEP_WINDOW 秒之内第一次暂停休息: 弹一段提示, 那次休息的暂停结转比例 +SLEEP_BREAK_BONUS;
# 合约的结束时间晚于睡觉时间时, 当天第一次按"开始行动"先弹一段提示 (MECHANICS 19).
SLEEP_DEFAULT = 23 * 60
SLEEP_WINDOW = 3 * 3600
SLEEP_BREAK_BONUS = 0.05
SLEEP_KEYS = ("SLEEP_MON", "SLEEP_TUE", "SLEEP_WED", "SLEEP_THU",
              "SLEEP_FRI", "SLEEP_SAT", "SLEEP_SUN")
SLEEP_MON = SLEEP_DEFAULT
SLEEP_TUE = SLEEP_DEFAULT
SLEEP_WED = SLEEP_DEFAULT
SLEEP_THU = SLEEP_DEFAULT
SLEEP_FRI = SLEEP_DEFAULT
SLEEP_SAT = SLEEP_DEFAULT
SLEEP_SUN = SLEEP_DEFAULT


def sleep_minutes(day) -> int:
    """某一天 (date / datetime) 的睡觉时间: 从 0:00 起的分钟数."""
    try:
        v = int(round(float(globals().get(SLEEP_KEYS[day.weekday()], SLEEP_DEFAULT))))
    except (TypeError, ValueError, AttributeError):
        v = SLEEP_DEFAULT
    return max(0, min(24 * 60 - 1, v))


def sleep_time_on(day) -> datetime:
    """某一天的睡觉时刻 (按那一天的睡觉时间, 不看别的)."""
    d = day.date() if isinstance(day, datetime) else day
    return datetime(d.year, d.month, d.day) + timedelta(minutes=sleep_minutes(d))


def last_sleep(now: datetime) -> datetime:
    """现在之前最近的一次睡觉时刻. 凌晨还没睡时算的是昨天那次 (跨零点)."""
    t = sleep_time_on(now)
    return t if t <= now else sleep_time_on(now - timedelta(days=1))


def next_sleep(now: datetime) -> datetime:
    """现在之后最近的一次睡觉时刻."""
    t = sleep_time_on(now)
    return t if t > now else sleep_time_on(now + timedelta(days=1))


# 行为模式 (设置 → 日常, MECHANICS 21): 第一次打开软件时可以选, 之后修改要等 24 × 7 小时.
# 封闭式管理 / 工作: 专注页多一个"外出"按钮, 外出期间按下面的规则记录.
BEHAVIOR_CLOSED = "closed"
BEHAVIOR_WORK = "work"
BEHAVIOR_FREE = "free"
BEHAVIOR_MODES = (BEHAVIOR_CLOSED, BEHAVIOR_WORK, BEHAVIOR_FREE)
BEHAVIOR_MODE = BEHAVIOR_FREE
BEHAVIOR_DELAY_HOURS = 24 * 7
# 整点记录在外出期间的自动填充: 外出的小时 → 外出, 其他时间 → 休息 (都可以在设置 → 日常 改, 48 小时生效).
OUTING_TEXT = "外出"
REST_TEXT = "休息"
# 记录规则 (MECHANICS 21): 每天最多记 10 小时; 7:00 ~ 睡觉时间 (上面的 SLEEP_*) 之内的部分按 80% 专注 / 20% 休息填充.
OUTING_WINDOW_START = 7 * 60
OUTING_DAILY_CAP = 10 * 3600
OUTING_FOCUS_SHARE = 0.8
# 外出结束: 被记录的小时数 × 6 分钟, 最多 3 小时.
OUTING_REST_PER_HOUR = 6 * 60
OUTING_REST_MAX = 3 * 3600
# 外出期间: 当天的联网额度 ×0.2, 联网冷静期 ×0.3.
OUTING_QUOTA_FACTOR = 0.2
OUTING_COOLDOWN_FACTOR = 0.3
OUTING_MAX_HOURS = 24 * 7





APP_NAME = "Obliphur's Integrated Terminal"
APP_VERSION = "1.0.174"                         # 和 GitHub 上发布的版本号比较 (见 updater.py)
UPDATE_CHECK = 1                              # 打开程序时检查更新
UPDATE_AUTO = 1                               # 发现新版本时自动下载安装
OLD_APP_DIR = "mrsTimer"
FILL_WINDOW = timedelta(hours=1)              
FINE_FILL_WINDOW = timedelta(minutes=30)      # 细化规范的半小时格补填时限最多 30 min (MECHANICS 18.4)
AUTO_TEXT = "Sleeping"
HOUR_LOG_ENABLED = True                       
CHIME_WAV = ""
LANGUAGE = "zh"
NET_MODE = "proxy"                            # "adapter" 禁用网卡 / "proxy" 本地代理过滤 / "proxy_all" 代理-全屏蔽 (Proxy block-all)
FILTER_MODE = "blacklist"                     # proxy 模式下生效的名单:"blacklist" / "whitelist"

# 任务栏窗口数上限: 每 WINDOW_CHECK_EVERY 秒数一次任务栏上的窗口 (同一程序的多个窗口算一个, 浏览器的各算一个), 超过 WINDOW_LIMIT 个
# 就提醒并响一次违约音效; 提醒之后 WINDOW_WARN_COOLDOWN 秒内不重复, 过了还没解决再响 (见 window_guard.py)
WINDOW_LIMIT_ENABLED = 1
WINDOW_LIMIT = 6
WINDOW_CHECK_EVERY = 30
WINDOW_WARN_COOLDOWN = 120                   # 窗口数超限的提醒冷却 (秒)
WINDOW_MIN_AGE = 120                         # 窗口开着超过这么多秒才计入窗口数 (窗口上限, 通道阻塞)
# 窗口使用时间统计: 每 WINDOW_FOCUS_EVERY 秒看一次前台窗口属于哪个程序, 把这几秒记给它
# (见 window_guard.foreground_program, DataManager.add_window_time). 桌面,文件资源管理器和命令行窗口记为空白.
WINDOW_FOCUS_ENABLED = 1
WINDOW_FOCUS_EVERY = 10





WINDOW_W = 1280
WINDOW_H = 860
MIN_WINDOW_W = 980
MIN_WINDOW_H = 680

FONT_FAMILY = "Microsoft YaHei UI"
FONT_FAMILY_FALLBACK = "Microsoft YaHei"
FONT_MONO = "Consolas"


FONT_SIZE = {
    "h1": 22,
    "h2": 15,
    "big": 44,
    "body": 12,
    "small": 11,
    "tiny": 10,
    "nav": 13,
    "btn": 12,
    "log": 12,
}


# ---- 像素字体主题用的字号 (像素, 不是磅) ----
# Unifont 的字形按 16px 设计, 所以正文字号取 16 的整数倍最清楚; 这里按界面字号整体缩放.
# 其余主题照旧用 FONT_SIZE (磅).
PIXEL_FONT_SIZE = {
    "h1": 32,
    "h2": 24,
    "big": 64,
    "body": 16,
    "small": 16,
    "tiny": 14,
    "nav": 16,
    "btn": 16,
    "log": 14,
}


def detect_ui_scale() -> float:
    if sys.platform != "win32":
        return 1.0
    if 'ctypes' not in globals():
        return 1.0
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi:
            return max(0.75, min(2.0, dpi / 96.0))
    except Exception:
        pass
    return 1.0


UI_SCALE = 1.0


def ui_scale() -> float:
    return UI_SCALE


def scaled(px: float) -> int:
    return int(round(px * ui_scale()))


THEME_PIXEL = False               # 当前主题是否使用像素字体 (字号按 PIXEL_FONT_SIZE 换算)


def font(size_key: str = "body", bold: bool = False, family: str = None):
    size = FONT_SIZE.get(size_key, FONT_SIZE["body"])
    if family is None:
        family = FONT_FAMILY
    if THEME_PIXEL and family in (FONT_FAMILY, FONT_MONO):
        # 像素字体: Tk 的负数字号 = 像素高度. 这样不会被 DPI / 磅值四舍五入弄糊.
        px = PIXEL_FONT_SIZE.get(size_key, PIXEL_FONT_SIZE["body"])
        px = max(8, int(round(px * ui_scale())))
        return (family, -px, "bold") if bold else (family, -px)
    return (family, max(8, scaled(size)), "bold") if bold else (family, max(8, scaled(size)))


def _contract_pixel_family():
    """合约页主题是都市之意时的像素字体名 (字体文件加载不了 = None, 退回默认字体)."""
    if CONTRACT_THEME == "urban":
        spec = THEMES["urban_will"]["font"]
        if _register_private_font(asset_path(*spec["file"])):
            return spec["family"]
    return None


def contract_font(size_key: str = "body", bold: bool = False):
    """合约页自己的字体, 不跟随界面主题: 默认字体; 合约页主题是都市之意时用 Unifont 像素字体 (字号同像素字体主题)."""
    family = _contract_pixel_family()
    if family:
        px = max(8, int(round(PIXEL_FONT_SIZE.get(size_key, PIXEL_FONT_SIZE["body"]) * ui_scale())))
        return (family, -px, "bold") if bold else (family, -px)
    size = FONT_SIZE.get(size_key, FONT_SIZE["body"])
    size = max(8, scaled(size))
    return (FONT_FAMILY_DEFAULT, size, "bold") if bold else (FONT_FAMILY_DEFAULT, size)


def contract_mini(pt: int, bold: bool = False, px: int = 14):
    """合约页格子 / 等级标记用的小字: 默认字体 pt 磅; 都市之意用像素字体 px 像素 (Unifont 太小就糊)."""
    family = _contract_pixel_family()
    if family:
        px = max(8, int(round(px * ui_scale())))
        return (family, -px, "bold") if bold else (family, -px)
    size = max(6, scaled(pt))
    return (FONT_FAMILY_DEFAULT, size, "bold") if bold else (FONT_FAMILY_DEFAULT, size)



def dev_mode_defined() -> bool:
    if "config" not in sys.modules:
        return False
    return "DEV_MODE" in vars(sys.modules["config"])


def dev_mode_enabled() -> bool:
    if "config" not in sys.modules:
        return False
    return bool(getattr(sys.modules["config"], "DEV_MODE", False))


def toggle_dev_mode() -> bool:
    """开 / 关开发者模式, 返回新的状态."""
    mod = sys.modules["config"]
    mod.DEV_MODE = not dev_mode_enabled()
    return mod.DEV_MODE


# ---- 配置期: 第一次打开软件时开始, 期间所有设置与名单的修改立即生效 ----
SETUP_HOURS = 72
SETUP_UNTIL = None          # datetime; 由 SettingsManager 从 app_settings.json 读入


def setup_active() -> bool:
    until = globals().get("SETUP_UNTIL")
    return isinstance(until, datetime) and datetime.now() < until


def changes_instant() -> bool:
    """设置 / 名单的修改是否立即生效: 开发者模式或配置期内."""
    return dev_mode_enabled() or setup_active()


# 合约中盖过设置的项 (MECHANICS 18): {设置项: 值}, 由 SettingsManager.apply_to_config 套在生效值上面
CONTRACT_OVERRIDES = {}





try:
    from config_local import *  # noqa: F401,F403
except ImportError:
    pass

# 开发者模式: 只能在"每日联网额度"里填 325799 开 / 关 (settings_manager.DEV_TOGGLE_CODE), 只在内存里;
# 每次启动都是关的, 不管 config_local 里写了什么. 界面上不显示.
DEV_MODE = False


DEFAULT_SETTINGS = {
    "DAILY_LIMIT": DAILY_LIMIT,
    "COOLDOWN": COOLDOWN,
    "HOUR_LOG_ENABLED": HOUR_LOG_ENABLED,
    "AUTO_TEXT": AUTO_TEXT,
    "ENFORCE_EVERY": ENFORCE_EVERY,
    "CHIME_WAV": CHIME_WAV,
    "LANGUAGE": LANGUAGE,
    "NET_MODE": NET_MODE,
    "FILTER_MODE": FILTER_MODE,
    "WINDOW_LIMIT_ENABLED": WINDOW_LIMIT_ENABLED,
    "WINDOW_LIMIT": WINDOW_LIMIT,
    "WINDOW_FOCUS_ENABLED": WINDOW_FOCUS_ENABLED,
    "FONT_SCALE": 0.9,
    "UPDATE_CHECK": UPDATE_CHECK,
    "UPDATE_AUTO": UPDATE_AUTO,
    "THEME": "classic",
    "CONTRACT_THEME": "ops",
    "REC_FOCUS_ONLY": 1,
    "REC_ASK": 1,
    "REC_MIC": 1,
    "REC_SYSTEM": 1,
    "REC_CAMERA": "",
    "TICK_CLAMP": TICK_CLAMP,
    "FILL_WINDOW": int(FILL_WINDOW.total_seconds()),
    "QUICK_MULTIPLIER": QUICK_MULTIPLIER,
    "LOAN_RATE": LOAN_RATE,
    "BREAK_COOLDOWN_RATE": BREAK_COOLDOWN_RATE,
    "BREAK_CARRY_RATE": BREAK_CARRY_RATE,
    "BREAK_TIME_SCALE": BREAK_TIME_SCALE,
    "QUOTA_MULT_MON": QUOTA_MULT_MON,
    "QUOTA_MULT_TUE": QUOTA_MULT_TUE,
    "QUOTA_MULT_WED": QUOTA_MULT_WED,
    "QUOTA_MULT_THU": QUOTA_MULT_THU,
    "QUOTA_MULT_FRI": QUOTA_MULT_FRI,
    "QUOTA_MULT_SAT": QUOTA_MULT_SAT,
    "QUOTA_MULT_SUN": QUOTA_MULT_SUN,
    "SLEEP_MON": SLEEP_MON,
    "SLEEP_TUE": SLEEP_TUE,
    "SLEEP_WED": SLEEP_WED,
    "SLEEP_THU": SLEEP_THU,
    "SLEEP_FRI": SLEEP_FRI,
    "SLEEP_SAT": SLEEP_SAT,
    "SLEEP_SUN": SLEEP_SUN,
    "BEHAVIOR_MODE": BEHAVIOR_MODE,
    "OUTING_TEXT": OUTING_TEXT,
    "REST_TEXT": REST_TEXT,
}


# ---- 主题 ----
# 每个主题是一整套颜色. 程序各处只从 COLORS / CHART_PALETTE 取色,
# 切换主题时 apply_theme() 原地替换它们的内容, 再由界面重建生效.
#
# ttk 一项是 clam 主题的底色 (输入框/滚动条/普通按钮等系统控件用),
# 经典主题里填的就是 clam 的原始默认值, 所以经典主题外观和以前一样.

from themes import THEMES


THEME_NAMES = tuple(THEMES)
DEFAULT_THEME = "classic"
THEME = DEFAULT_THEME
THEME_DATA = {}                   # 当前主题的完整数据 (含 focus_halo 等主题专属配置)
THEME_DARK = False
FONT_FAMILY_DEFAULT = FONT_FAMILY
FONT_MONO_DEFAULT = FONT_MONO
_FONTS_LOADED = set()
_PIXEL_SAFE_CACHE = {}
CONTRACT_THEME = "ops"           # 合约页主题: ops 起源 / urban 都市之意 (设置 → 基本设置, 不同步; 开始合约时记进合约)
REC_FOCUS_ONLY = 1               # 目光 / 刻痕只在专注期间录像 (设置 → 录像, 随设备同步)
REC_ASK = 1                      # 录像前询问 (合约的目光 / 刻痕, 设置 → 录像)
REC_MIC = 1                      # 录像时录麦克风 (外部音); 默认开 (设置 → 录像, 不同步)
REC_SYSTEM = 1                   # 录像时录系统音; 默认开 (设置 → 录像, 不同步)
REC_CAMERA = ""                  # 目光用哪台摄像头: "" 自动 / "pc|<id>" 本机 / "phone|<设备 id>" 手机 (不同步)

COLORS = dict(THEMES[DEFAULT_THEME]["colors"])
CHART_PALETTE = list(THEMES[DEFAULT_THEME]["chart"])
TTK_COLORS = dict(THEMES[DEFAULT_THEME]["ttk"])


def asset_path(*parts) -> str:
    """程序自带资源的绝对路径 (打包后也从解包目录找)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def _register_private_font(path: str) -> bool:
    """把字体文件只注册给本进程 (不改系统字体). Tk / GDI 之后就能按字体名使用."""
    if sys.platform != "win32" or not path or path in _FONTS_LOADED:
        return bool(path in _FONTS_LOADED)
    if not os.path.isfile(path):
        return False
    try:
        gdi = ctypes.windll.gdi32
        gdi.AddFontResourceExW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
        gdi.AddFontResourceExW.restype = ctypes.c_int
        if gdi.AddFontResourceExW(path, 0x10, None):     # FR_PRIVATE
            _FONTS_LOADED.add(path)
            return True
    except Exception:
        pass
    return False


def _activate_theme_font(theme: dict) -> bool:
    """按主题切换 FONT_FAMILY / FONT_MONO / THEME_PIXEL. 加载失败时退回默认字体."""
    global FONT_FAMILY, FONT_MONO, THEME_PIXEL
    spec = theme.get("font")
    if not spec:
        FONT_FAMILY = FONT_FAMILY_DEFAULT
        FONT_MONO = FONT_MONO_DEFAULT
        THEME_PIXEL = False
        return True
    path = asset_path(*spec["file"]) if spec.get("file") else ""
    if _register_private_font(path):
        FONT_FAMILY = spec.get("family") or FONT_FAMILY_DEFAULT
        FONT_MONO = spec.get("mono") or FONT_FAMILY
        THEME_PIXEL = bool(spec.get("pixel"))
        return True
    FONT_FAMILY = FONT_FAMILY_DEFAULT
    FONT_MONO = FONT_MONO_DEFAULT
    THEME_PIXEL = False
    return False


def apply_titlebar(win) -> None:
    """Windows 10/11: 深色主题时让这个窗口的标题栏也变成深色 (浅色主题还原)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        dark = ctypes.c_int(1 if THEME_DARK else 0)
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (新 / 旧版本号)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(dark), ctypes.sizeof(dark)) == 0:
                break
    except Exception:
        pass


def _on_first_map(event, win) -> None:
    if event.widget is not win:
        return
    apply_titlebar(win)


def install_titlebar_hook() -> None:
    """所有弹窗 (tk.Toplevel) 一创建就按当前主题设置标题栏颜色, 不用每个弹窗各写一遍."""
    if getattr(tk.Toplevel, "_oit_titlebar", False):
        return
    orig = tk.Toplevel.__init__

    def init(self, *args, **kwargs):
        orig(self, *args, **kwargs)
        # 窗口显示出来之后才有标题栏的窗口句柄, 所以等第一次 <Map> 再设置
        self.bind("<Map>", lambda e, w=self: _on_first_map(e, w), add="+")

    tk.Toplevel.__init__ = init
    tk.Toplevel._oit_titlebar = True


def apply_theme(name: str = None) -> str:
    """把 COLORS / CHART_PALETTE / TTK_COLORS 换成指定主题的颜色 (原地修改,
    已经 import 了 COLORS 的模块也能看到), 并按主题切换字体. 未知名字退回默认主题.
    返回实际主题名."""
    global THEME, THEME_DARK, THEME_DATA
    if name not in THEMES:
        name = DEFAULT_THEME
    t = THEMES[name]
    THEME_DATA = t
    COLORS.clear()
    COLORS.update(t["colors"])
    COLORS.setdefault("topbar", COLORS["accent"])
    # 选中 / 强调块的填充色: 默认就是强调色本身 (经典 / Claude 保持原样);
    # 都市之意换成深青底 + 亮青字, 不在黑底上铺大块亮色.
    COLORS.setdefault("accent_fill", COLORS["accent"])
    COLORS.setdefault("accent_fill_hover", COLORS["accent_hover"])
    COLORS.setdefault("on_accent_fill", COLORS["on_accent"])
    COLORS.setdefault("chip_fill", COLORS["text"])
    COLORS.setdefault("chip_fill_hover", COLORS["border"])
    COLORS.setdefault("chip_text", COLORS["bg"])
    COLORS.setdefault("focus", COLORS["accent"])
    COLORS.setdefault("input_focus", COLORS["accent"])
    COLORS.setdefault("danger_fill", COLORS["danger"])
    COLORS.setdefault("danger_fill_hover", COLORS["danger_hover"])
    COLORS.setdefault("on_danger_fill", COLORS["on_accent"])
    CHART_PALETTE[:] = t["chart"]
    TTK_COLORS.clear()
    TTK_COLORS.update(t["ttk"])
    THEME = name
    THEME_DARK = bool(t.get("dark"))
    _activate_theme_font(t)
    return name


# 都市之意: 不画图标. 把 emoji / 符号图标从界面文字里去掉 (Unifont 没有这些字形,
# 留在界面上会变成方框); 普通箭头 / 几何符号仍保留.
_ICON_TEXT = re.compile(
    "[\U0001F000-\U0001FAFF"      # emoji
    "\u21A9-\u21AA\u21BB"         # ↩ ↪ ↻ (按钮图标, Unifont 缺字)
    "\u2300-\u23FF"               # ⏱ ⏩ ⏳ 等
    "\u2600-\u27BF"               # ⚔ ⚙ ⚡ ⚠ ☕ ✕ 等
    "\u2B00-\u2BFF"               # ⬆ ⬇ 等
    "\uFE0F\u200D\u20E3]"         # emoji 变体 / 连接符 / 键帽
)


def pixel_safe(text):
    """像素字体主题下把图标字符从文字里拿掉 (图标的空格一起去掉)."""
    if not THEME_PIXEL or not isinstance(text, str) or not text:
        return text
    cached = _PIXEL_SAFE_CACHE.get(text)
    if cached is not None:
        return cached
    out = _ICON_TEXT.sub("\x00", text)
    out = re.sub("\x00[ \t]*", "", out)
    out = re.sub("[ \t]*\x00", "", out)
    out = out.replace("\x00", "").rstrip()
    if len(_PIXEL_SAFE_CACHE) > 2000:
        _PIXEL_SAFE_CACHE.clear()
    _PIXEL_SAFE_CACHE[text] = out
    return out



def compute_F(t: float) -> float:
    if t <= 15.0:
        return t / 6.0
    elif t < 150.0:
        return (7.0 / 14850.0) * t * t + (407.0 / 1485.0) * t - (170.0 / 99.0)
    else:
        return 50.0 + (5.0 / math.log(1.2)) * math.log(t / 150.0)


def _topmost_win(title: str, message: str) -> bool:
    if 'ctypes' not in globals():
        return False
    try:
        flags = 0x00 | 0x40 | 0x10000 | 0x40000
        threading.Thread(
            target=lambda: ctypes.windll.user32.MessageBoxW(None, message, title, flags),
            daemon=True
        ).start()
        return True
    except Exception:
        return False


def show_topmost_info(title: str, message: str, parent=None):
    if sys.platform == "win32" and _topmost_win(title, message):
        return
    if parent is None:
        return
    try:
        tmp = tk.Toplevel(parent)
        tmp.attributes("-topmost", True)
        tmp.geometry("1x1+-100+-100")
        try:
            messagebox.showinfo(title, message, parent=tmp)
        finally:
            tmp.destroy()
    except Exception:
        pass


def fmt_hm(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 3600}h {(sec % 3600) // 60:02d}m"


def fmt_ms(sec: float) -> str:
    m, s = divmod(max(0, int(math.ceil(sec))), 60)
    return f"{m:02d}:{s:02d}"


def is_admin() -> bool:
    if sys.platform != "win32":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate():
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    args = [os.path.abspath(sys.argv[0])] + sys.argv[1:]
    params = " ".join(f'"{a}"' for a in args)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)



def relaunch():
    """重置后重新打开程序. 当前进程已是管理员, 新进程直接继承权限."""
    import subprocess
    if getattr(sys, "frozen", False):
        args = [sys.executable] + sys.argv[1:]
    else:
        exe = sys.executable
        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if sys.platform == "win32" and os.path.exists(pyw):
            exe = pyw
        args = [exe, os.path.abspath(sys.argv[0])] + sys.argv[1:]
    try:
        subprocess.Popen(args, cwd=os.getcwd(), close_fds=True)
        return True
    except OSError:
        return False


def app_data_dir() -> str:
    root = os.path.join(
        os.environ.get('USERPROFILE', os.path.expanduser('~')),
        'AppData', 'LocalLow'
    )
    return os.path.join(root, APP_NAME)


def settings_file() -> str:
    return os.path.join(app_data_dir(), "app_settings.json")


def load_json_file(path: str, default):
    try:
        import json
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(path: str, payload) -> bool:
    import json
    tmp = path + ".tmp"
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False




def ensure_builtin_chime(path: str) -> str:
    if os.path.exists(path) and os.path.getsize(path) > 44:
        return path
    try:
        import struct
        import wave
        rate = 44100
        frames = bytearray()
        for freq, ms in ((784, 200), (988, 200), (1319, 450)):
            n = int(rate * ms / 1000.0)
            for i in range(n):

                fade = min(1.0, i / (rate * 0.015), (n - 1 - i) / (rate * 0.015))
                amp = int(11000 * max(0.0, fade))
                frames += struct.pack('<h', int(amp * math.sin(2 * math.pi * freq * i / rate)))
            frames += b'\x00\x00' * int(rate * 0.04)
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with wave.open(path, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(frames))
        return path
    except Exception:
        return ""

