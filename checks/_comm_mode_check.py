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
    config.UI_SCALE = original_scale
    i18n.set_language(original_language)
    c.flush()
    c.voice.close()
    root.destroy()

print("Comm mode check OK")
