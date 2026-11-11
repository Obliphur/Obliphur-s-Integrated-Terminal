"""事件音效的选项、文件解析与播放；所有事件共用同一套内置音色。"""

import os
import random
import threading

import config
from i18n import tr


EVENTS = {
    "CHIME_WAV": ("整点提示音", "chime"),
    "DAMAGE_SOUND": ("损失生命值", "alarm"),
    "COMM_SOUND": ("收到信息", "soft"),
    "BREAK_SOUND": ("休息即将结束", "break"),
    "OFFLINE_SOUND": ("设备离线", "chime"),
    "WINDOW_SOUND": ("窗口超限", "alarm"),
    "PRESCRIPT_RECEIVE_SOUND": ("领取指令", "tick"),
    "PRESCRIPT_COMPLETE_SOUND": ("完成指令", "tick"),
}
CHOICES = [
    ("__builtin__", "默认音效"), ("__silent__", "静音"),
    ("__chime__", "三音提示"), ("__soft__", "柔和钟声"),
    ("__break__", "低音钟声"), ("__alarm__", "警报"),
    ("__tick__", "随机滴答"), ("__bell__", "清脆铃声"),
    ("__rise__", "上行音阶"), ("__fall__", "下行音阶"),
    ("__wood__", "木琴轻敲"), ("__sparkle__", "星光"),
]


def display(value):
    value = str(value or "__builtin__")
    return next((tr(label) for v, label in CHOICES if v == value),
                "🎵 " + os.path.basename(value.rstrip("\\/")))


def resolve(key, value, data_dir):
    """返回可播放的文件；空字符串兼容旧版默认值，静音返回空路径。"""
    value = str(value or "__builtin__").strip()
    if value == "__silent__":
        return ""
    tone = EVENTS[key][1] if value == "__builtin__" else value.strip("_")
    if tone == "chime":
        return config.ensure_builtin_chime(os.path.join(data_dir, "chime.wav"))
    if tone == "soft":
        return config.ensure_soft_chime(os.path.join(data_dir, "comm_ping.wav"))
    if tone == "break":
        return config.ensure_break_chime(os.path.join(data_dir, "break_soon.wav"))
    if tone == "alarm":
        return config.asset_path("sounds", "target_alarm.wav")
    if tone == "tick":
        return config.asset_path("themes", "urban_will", "audio",
                                 f"ticktock_type{random.randint(1, 3)}.mp3")
    if value in dict(CHOICES):
        return config.asset_path("sounds", f"effect_{tone}.wav")
    return value


def play(key, data_dir, choice=None):
    """后台播放，失效的自选文件退回该事件默认音效。"""
    from app.app_base import _play_audio_file
    value = getattr(config, key) if choice is None else choice
    if value == "__silent__":
        return None

    def run():
        path = resolve(key, value, data_dir)
        if _play_audio_file(path):
            return
        fallback = resolve(key, "__builtin__", data_dir)
        if path != fallback and _play_audio_file(fallback):
            return
        # 文件播放不可用时仍保留原有警报 / 整点蜂鸣兜底。
        try:
            import winsound
            notes = ((1400, 140),) * 3 if EVENTS[key][1] == "alarm" else (
                (784, 200), (988, 200), (1319, 450))
            for frequency, duration in notes:
                winsound.Beep(frequency, duration)
        except Exception:
            pass

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread
