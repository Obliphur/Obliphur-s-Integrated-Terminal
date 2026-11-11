"""app.py 拆出来的无状态小工具 (常量 / 画颜色 / 放音频 / 语言文案).

不属于 BreakTimerApp, 也不碰任何实例状态; 需要它们的模块直接 import.
"""

import os
import sys

import config
from i18n import tr


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


ALARM_WAV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sounds", "target_alarm.wav")


TIRED_TEXT = ("主管{name}你好.我是Obliphur.\n"
              "你的休息时间不够吗?你可能暂时还无法做到长时间专注 导致休息效率低下.\n"
              "或者说,你处于低谷期,没有什么精力长时间专注.\n"
              "没关系.到设置调整休息时间倍率吧,请不要直接卸载,哪怕RESET也好...")


def tired_text(name: str) -> str:
    """一周内第 5 次领"我累了"时弹窗的文字 (MECHANICS 6.5)."""
    return tr(TIRED_TEXT, name=name or "")


URBAN_NOTICE_FLAG = "urban_notice"     # 切到都市之意时弹窗已经带过几次"可以改日程"的小字 (daily.json)
URBAN_NOTICE_HINTS = 2


def _blend(c1: str, c2: str, k: float) -> str:
    """c1 和 c2 按 k : (1 - k) 混合 (k 越小越接近 c2, 用来画"半透明"的字); 不是 #RRGGBB 时原样返回 c1."""
    try:
        a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
        return "#%02x%02x%02x" % tuple(round(x * k + y * (1 - k)) for x, y in zip(a, b))
    except (ValueError, TypeError, IndexError):
        return c1


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
