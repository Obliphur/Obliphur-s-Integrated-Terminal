"""音效设置、草稿、播放回退与小窗口标签布局的自检，不播放真实音频。"""
import os
import sys
import wave
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempstore
tempstore.session("sound_check")

import tkinter as tk
import config
import sound_effects as effects
from settings_manager import SettingsManager, page_specs
from pages.settings_page import SettingsPage


folder = tempstore.mkdtemp("sound")
settings_path = os.path.join(folder, "settings.json")
sm = SettingsManager(settings_path)
assert {s["key"] for s in page_specs("sound")[0]} == set(effects.EVENTS)
assert "CHIME_WAV" not in {s["key"] for s in page_specs("log")[0]}
for key in effects.EVENTS:
    assert sm.stage(key, "__silent__")[0] == "instant"
    assert not sm.is_staged(key)
loaded = SettingsManager(settings_path)
assert all(loaded.value(key) == "__silent__" for key in effects.EVENTS)
assert sm.stage("DAMAGE_SOUND", "__wood__")[0] == "instant"
assert sm.value("COMM_SOUND") == "__silent__"

# 每种内置音色都有可播放的文件；新增 WAV 在双端保持一致。
for value, _label in effects.CHOICES:
    if value == "__silent__":
        assert not effects.resolve("CHIME_WAV", value, folder)
        continue
    assert os.path.isfile(effects.resolve("CHIME_WAV", value, folder)), value
for name in ("bell", "rise", "fall", "wood", "sparkle"):
    path = config.asset_path("sounds", f"effect_{name}.wav")
    with wave.open(path) as audio:
        assert audio.getnchannels() == 1 and audio.getsampwidth() == 2
        assert 0.1 < audio.getnframes() / audio.getframerate() < 3
    android = config.asset_path("Android version", "app", "src", "main", "res", "raw", f"effect_{name}.wav")
    with open(path, "rb") as a, open(android, "rb") as b:
        assert a.read() == b.read()

# 静音完全跳过播放；自选文件失效时回退到该事件默认音效。
with patch("app.app_base._play_audio_file", side_effect=[False, True]) as play:
    assert effects.play("CHIME_WAV", folder, "__silent__") is None
    play.assert_not_called()
    thread = effects.play("COMM_SOUND", folder, os.path.join(folder, "missing.mp3"))
    thread.join(5)
    assert not thread.is_alive() and play.call_count == 2
    assert play.call_args.args[0].endswith("comm_ping.wav")

root = tk.Tk()
root.geometry("850x650+-2500+-2500")
previews = []
page = SettingsPage(root, sm, data_dir=folder, on_sound=previews.append)
page.pack(fill="both", expand=True)
page.show_tab("sound")
page.refresh()
root.update()
assert "sound" in page._tab_btns
row = page._rows["COMM_SOUND"]
row["var"].set(effects.display("__sparkle__"))
row["widget"].event_generate("<<ComboboxSelected>>")
page.refresh()
assert page._widget_value("COMM_SOUND") == "__sparkle__", "刷新不能覆盖未提交的选择"
with patch("sound_effects.play") as play:
    page._preview_chime("COMM_SOUND")
    play.assert_called_once_with("COMM_SOUND", folder, "__sparkle__")
with patch("toast.info"), patch("toast.success"):
    page._stage_now("COMM_SOUND")
assert sm.value("COMM_SOUND") == "__sparkle__"
assert sm.value("DAMAGE_SOUND") == "__wood__"

# 自选文件只显示文件名，切到内置音色后路径不能覆盖选择。
with patch("pages.settings_page.settings_form.filedialog.askopenfilename", return_value=os.path.join(folder, "my_sound.wav")):
    page._pick_wav(row["widget"], row["custom"], "COMM_SOUND")
page.refresh()
assert page._widget_value("COMM_SOUND").endswith("my_sound.wav")
assert row["var"].get() == "🎵 my_sound.wav"
row["var"].set(effects.display("__silent__"))
assert page._widget_value("COMM_SOUND") == "__silent__"
with patch("toast.info"), patch("toast.success"):
    page._save_all()
assert sm.value("COMM_SOUND") == "__silent__" and not row["picked"]
root.geometry("650x650+-2500+-2500")
root.update()
assert max(button.winfo_y() for button in page._tab_btns.values()) > 0
for button in page._tab_btns.values():
    assert button.winfo_x() + button.winfo_width() <= button.master.winfo_width()
root.destroy()
print("sound settings check OK")
