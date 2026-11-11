"""AI 模型设置弹窗: 内容放得下时窗口收紧, 放不下时能滚到底, 字段都点得到.

以前窗口固定 600x660, 125% 缩放下后面的字段被切掉、点不到.
窗口开在屏幕外, 只读 AI 配置的临时副本, 不碰真实数据.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempstore
tempstore.session("ai_dialog_check")     # 临时文件放在 %TEMP%\OIT 里, 退出时整个删掉 (tempstore.py)

import os
import tempfile
import tkinter as tk

import config

import ai_assist
from pages import ai_dialogs
from pages.widgets import Modal

root = tk.Tk()
root.geometry("1280x860+5000+5000")
root.update()

cfg = ai_assist.AIConfig(os.path.join(tempfile.mkdtemp(), "ai_config.json"))
seen = {}


def fake_wait(self):
    """不真的进事件循环: 量一下滚动区就关掉弹窗."""
    self.update_idletasks()
    area = seen["area"]
    x0, y0, x1, y1 = area.canvas.bbox("all")
    seen.update(viewport=area.canvas.winfo_height(), content=y1,
                inner=area.inner.winfo_reqheight(), height=self.winfo_height(),
                screen=self.winfo_screenheight())
    self.destroy()


class Spy(ai_dialogs.ScrollArea):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        seen["area"] = self


orig_wait, orig_area = Modal.wait, ai_dialogs.ScrollArea
Modal.wait = fake_wait
ai_dialogs.ScrollArea = Spy
try:
    for scale in (1.0, 1.25, 1.5):
        config.UI_SCALE = scale
        seen.clear()
        ai_dialogs.open_model_settings(root, cfg)
        assert seen["content"] >= seen["inner"] - 2, (scale, seen)     # 滚动区包住全部内容
        assert seen["viewport"] > 0, (scale, seen)
        assert seen["height"] <= seen["screen"] * 0.9, (scale, seen)   # 不能高过屏幕
        if scale >= 1.5:
            # 字号很大时一定放不下: 这时才真的在测滚动
            assert seen["content"] > seen["viewport"], (scale, seen)
finally:
    Modal.wait, ai_dialogs.ScrollArea = orig_wait, orig_area
    config.UI_SCALE = 1.0

root.destroy()
print("AI 模型设置弹窗 check OK (放得下就收紧, 放不下能滚到底)")
