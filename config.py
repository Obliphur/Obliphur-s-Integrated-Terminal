

import sys
import os
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
FOCUS_CHECK_EVERY = 90 * 60
FOCUS_CHECK_GRACE = 30 * 60
FOCUS_CHECK_PENALTY = 0.3

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





APP_NAME = "Obliphur's Integrated Terminal"
APP_VERSION = "1.0.56"                         # 和 GitHub 上发布的版本号比较 (见 updater.py)
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

# 任务栏窗口数上限: 每 WINDOW_CHECK_EVERY 秒数一次任务栏上的窗口 (同一程序的多个窗口各算一个),
# 超过 WINDOW_LIMIT 个就随机关掉多出来的 (见 window_guard.py)
WINDOW_LIMIT_ENABLED = 1
WINDOW_LIMIT = 5
WINDOW_CHECK_EVERY = 30
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


def font(size_key: str = "body", bold: bool = False, family: str = None):
    size = FONT_SIZE.get(size_key, FONT_SIZE["body"])
    if family is None:
        family = FONT_FAMILY
    return (family, max(8, scaled(size)), "bold") if bold else (family, max(8, scaled(size)))



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
    "REC_ASK": 1,
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
}


# ---- 主题 ----
# 每个主题是一整套颜色. 程序各处只从 COLORS / CHART_PALETTE 取色,
# 切换主题时 apply_theme() 原地替换它们的内容, 再由界面重建生效.
#
# ttk 一项是 clam 主题的底色 (输入框/滚动条/普通按钮等系统控件用),
# 经典主题里填的就是 clam 的原始默认值, 所以经典主题外观和以前一样.

THEMES = {
    "classic": {
        "colors": {
            "bg":           "#ffffff",
            "bg_card":      "#f8f9fa",
            "accent":       "#2563eb",
            "accent_hover": "#1d4ed8",
            "text":         "#1e293b",
            "text_dim":     "#64748b",
            "text_strong":  "#0f172a",
            "success":      "#16a34a",
            "warn":         "#d97706",
            "border":       "#e2e8f0",
            "danger":       "#dc2626",
            "danger_hover": "#b91c1c",
            "danger_bg":    "#fef2f2",
            "on_accent":    "#ffffff",
            "accent_disabled":    "#94a3b8",
            "on_accent_disabled": "#cbd5e1",
            "danger_disabled":    "#fca5a5",
            "switch_off":   "#cbd5e1",
            "chart_other":  "#94a3b8",
            "chart_empty":  "#e2e8f0",
            "chart_future": "#f1f5f9",
            "focus_study":  "#2563eb",
            "focus_break":  "#16a34a",
        },
        "chart": (
            "#2563eb", "#16a34a", "#d97706", "#dc2626", "#7c3aed",
            "#0891b2", "#db2777", "#65a30d", "#ea580c",
        ),
        "ttk": {
            "frame": "#dcdad5", "frame_hover": "#eeebe7", "fg": "#000000",
            "field": "#ffffff", "border": "#9e9a91", "light": "#eeebe7",
            "dark": "#cfcdc8", "trough": "#bab5ab", "select": "#4a6984",
            "on_select": "#ffffff", "disabled_fg": "#999999",
            "list_bg": "#ffffff", "list_fg": "#000000",
            "list_select": "#0078d7", "list_on_select": "#ffffff",
        },
    },

    # 参考 Claude 的浅色界面: 通体米白, 卡片只靠细描边区分, 陶土橙强调色.
    "claude": {
        "colors": {
            "bg":           "#faf9f5",
            "bg_card":      "#faf9f5",
            "accent":       "#c96442",
            "accent_hover": "#b25535",
            "text":         "#141413",
            "text_dim":     "#73726c",
            "text_strong":  "#141413",
            "success":      "#5b7f4a",
            "warn":         "#b8741a",
            "border":       "#e6e3d8",
            "danger":       "#b5412f",
            "danger_hover": "#9a3424",
            "danger_bg":    "#f8ebe5",
            "on_accent":    "#ffffff",
            "accent_disabled":    "#dcb4a5",
            "on_accent_disabled": "#fbf4f0",
            "danger_disabled":    "#e0aca2",
            "switch_off":   "#d4d1c6",
            "chart_other":  "#b3b0a6",
            "chart_empty":  "#e6e3d9",
            "chart_future": "#f4f2eb",
            "focus_study":  "#c96442",
            "focus_break":  "#788c5d",
        },
        "chart": (
            "#c96442", "#6a9bcc", "#788c5d", "#d4a27f", "#9b7bb8",
            "#4a8f86", "#b5577a", "#a68a3e", "#5e6b8a",
        ),
        "ttk": {
            "frame": "#faf9f5", "frame_hover": "#f0eee6", "fg": "#141413",
            "field": "#ffffff", "border": "#d9d6cb", "light": "#faf9f5",
            "dark": "#e8e5db", "trough": "#ebe8df", "select": "#f1d8cc",
            "on_select": "#141413", "disabled_fg": "#a8a69e",
            "list_bg": "#ffffff", "list_fg": "#141413",
            "list_select": "#f1d8cc", "list_on_select": "#141413",
        },
    },

    # 参考 Claude 的深色界面: 炭灰底色, 亮一点的陶土橙.
    "claude_dark": {
        "colors": {
            "bg":           "#262624",
            "bg_card":      "#30302e",
            "accent":       "#d97757",
            "accent_hover": "#e38b6d",
            "text":         "#f5f4ee",
            "text_dim":     "#a3a199",
            "text_strong":  "#ffffff",
            "success":      "#8fb47a",
            "warn":         "#e0a458",
            "border":       "#43423d",
            "danger":       "#e5705e",
            "danger_hover": "#ee8676",
            "danger_bg":    "#3a2925",
            "on_accent":    "#ffffff",
            "accent_disabled":    "#5e4339",
            "on_accent_disabled": "#9a877e",
            "danger_disabled":    "#5e3a34",
            "switch_off":   "#55544e",
            "chart_other":  "#77756e",
            "chart_empty":  "#3d3c38",
            "chart_future": "#2d2d2b",
            "focus_study":  "#d97757",
            "focus_break":  "#8fa673",
        },
        "chart": (
            "#d97757", "#7eaedb", "#8fa673", "#ddb28f", "#ad8fca",
            "#5fa89e", "#cc6f91", "#c2a457", "#8090b5",
        ),
        "ttk": {
            "frame": "#3a3a37", "frame_hover": "#46453f", "fg": "#f5f4ee",
            "field": "#1f1e1d", "border": "#4d4c46", "light": "#3a3a37",
            "dark": "#2f2f2c", "trough": "#2b2b29", "select": "#6a4334",
            "on_select": "#ffffff", "disabled_fg": "#6f6d66",
            "list_bg": "#1f1e1d", "list_fg": "#f5f4ee",
            "list_select": "#6a4334", "list_on_select": "#ffffff",
        },
    },
}

THEME_NAMES = tuple(THEMES)
DEFAULT_THEME = "classic"
THEME = DEFAULT_THEME
REC_ASK = 1                      # 录像前询问 (合约的目光 / 刻痕, 设置 → 录像)
CONTRACT_THEME = "ops"           # 合约页的主题: "ops" 起源 / "ui" 跟随界面主题 (pages/contract_page.py)

COLORS = dict(THEMES[DEFAULT_THEME]["colors"])
CHART_PALETTE = list(THEMES[DEFAULT_THEME]["chart"])
TTK_COLORS = dict(THEMES[DEFAULT_THEME]["ttk"])


def apply_theme(name: str = None) -> str:
    """把 COLORS / CHART_PALETTE / TTK_COLORS 换成指定主题的颜色 (原地修改,
    已经 import 了 COLORS 的模块也能看到). 未知名字退回默认主题. 返回实际主题名."""
    global THEME
    if name not in THEMES:
        name = DEFAULT_THEME
    t = THEMES[name]
    COLORS.clear()
    COLORS.update(t["colors"])
    CHART_PALETTE[:] = t["chart"]
    TTK_COLORS.clear()
    TTK_COLORS.update(t["ttk"])
    THEME = name
    return name



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

