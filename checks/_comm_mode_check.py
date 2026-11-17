"""沟通模式入口：未解锁也可见可点，窄窗口 / 大字号不挤掉，帮助说明可滚动。"""
import os
import sys
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempstore
tempstore.session("comm_mode_check")

import config
import i18n
from comm import Comm
from pages.comm_page import CommPage
from pages.settings_page import SettingsPage
from settings_manager import SettingsManager
from pages.widgets import Modal, ScrollArea


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


root = tk.Tk()
root.geometry("980x680+5000+5000")
folder = tempstore.mkdtemp("comm_mode")
c = Comm(os.path.join(folder, "data"), os.path.join(folder, "personas"))
original_scale = config.UI_SCALE
original_language = i18n.get_language()
original_show_tension = config.COMM_SHOW_TENSION
sm = SettingsManager(os.path.join(folder, "settings.json"))
settings = SettingsPage(root, sm, data_dir=folder)
settings.show_tab("net")
# 真正通过设置页提交：口令不修改联网冷静期、不影响开发者模式，并恢复填写框。
row = settings._num_rows["COOLDOWN"]
before = (dict(sm.effective), dict(sm.pending), config.dev_mode_enabled())
for expected in (True, False):
    row["var"].set("325799")
    assert settings._stage_num("COOLDOWN") == ("tension", "")
    assert config.COMM_SHOW_TENSION is expected
    assert row["var"].get() == sm.ui_number("COOLDOWN")
    assert (dict(sm.effective), dict(sm.pending), config.dev_mode_enabled()) == before
settings.destroy()
split_settings = SettingsPage(root, sm, data_dir=folder, comm=c)
split_settings.show_tab("comm")
root.update()
assert split_settings.var_split_interval.get() == "5"
assert split_settings.split_interval_row.winfo_manager() == "pack"
split_settings.var_split_interval.set("13")
assert c.split_interval() == 13
split_settings.var_split_interval.set("")
assert c.split_interval() == 13
split_settings.var_split_interval.set("-1")
assert c.split_interval() == 13
split_settings.comm_vars["split_send"].set(False)
split_settings._comm_flag("split_send")
assert not split_settings.split_interval_row.winfo_manager()
assert c.split_interval() == 13
c.apply_doc({"split": True, "split_interval": 8})
split_settings._refresh_comm()
assert split_settings.split_interval_row.winfo_manager() == "pack"
assert split_settings.var_split_interval.get() == "8"
split_settings.destroy()
try:
    for language in ("zh", "en"):
        i18n.set_language(language)
        for scale in (1.0, 1.5):
            config.UI_SCALE = scale
            page = CommPage(root, c)
            page.pack(fill="both", expand=True)
            c.cfg["msg_gone"] = 199
            c.cfg["mem_fixed"] = []
            c._interaction_set({"scenario_mode": False})
            # 仅开关开启时显示真实张力；不受情景模式、消息门槛和90档位封顶影响。
            page._sync_status()
            assert not page.lbl_tension.winfo_manager()
            assert sm.stage("COOLDOWN", sm.from_ui_number("COOLDOWN", "325799"))[0] == "tension"
            c._interaction_set({"tension": 95.126})
            with patch.object(c, "other_status_display", return_value=("", "idle")):
                page._sync_status()
                root.update()
            assert page.lbl_tension.winfo_ismapped() and page.lbl_tension.cget("text") == "Tension: 95.13"
            c._interaction_set({"tension": 42.5})
            page._sync_status()
            assert page.lbl_tension.cget("text") == "Tension: 42.5"
            assert sm.stage("COOLDOWN", sm.from_ui_number("COOLDOWN", "325799"))[0] == "tension"
            page._sync_status()
            assert not page.lbl_tension.winfo_manager() and page.lbl_tension.cget("text") == ""
            c._interaction_set({"scenario_mode": False})
            # 很长的对方状态不能挤掉模式入口。
            with patch.object(c, "other_status_display", return_value=("long status " * 80, "idle")):
                page.refresh(force=True)
                root.update()
            for button in (page.btn_scenario,):
                assert button.winfo_ismapped()
                assert not button.instate(["disabled"])
                assert button.winfo_rootx() >= page.winfo_rootx()
                assert button.winfo_rootx() + button.winfo_width() <= page.winfo_rootx() + page.winfo_width()
                assert button.master == page.btn_mute.master
                assert button.winfo_rooty() >= page.area.winfo_rooty() + page.area.winfo_height()
                assert button.winfo_rooty() + button.winfo_height() <= page.entry.winfo_rooty()
            assert page.btn_scenario.cget("text") == "📱"
            with patch("pages.comm_page.toast.info") as hint:
                page.btn_scenario.invoke()
                assert not c.scenario_on()
                hint.assert_not_called()

            c.cfg["msg_gone"] = 200
            page.refresh(force=True)
            page.btn_scenario.invoke()
            assert c.scenario_on()
            assert page.btn_scenario.cget("text") == "🎭"
            page.btn_scenario.invoke()
            assert not c.scenario_on()
            assert page.btn_scenario.cget("text") == "📱"

            def inspect_help(dlg):
                dlg.update_idletasks()
                text = "\n".join(str(w.cget("text")) for w in descendants(dlg) if "text" in w.keys())
                assert i18n.tr("聊天模式") in text and "📱 / 🎭" in text
                assert "200" not in text
                area = next(w for w in descendants(dlg) if isinstance(w, ScrollArea))
                assert area.winfo_height() > 0
                assert dlg.winfo_height() <= dlg.winfo_screenheight() * .85 + 1
                area.canvas.yview_moveto(1)
                dlg.update_idletasks()
                assert area.canvas.yview()[1] >= .99
                dlg.destroy()

            with patch.object(Modal, "wait", inspect_help):
                page._help()
            if page._resize_job:
                page.after_cancel(page._resize_job)
            page.destroy()
finally:
    config.COMM_SHOW_TENSION = original_show_tension
    config.UI_SCALE = original_scale
    i18n.set_language(original_language)
    c.flush()
    c.voice.close()
    root.destroy()

print("Comm mode check OK")
