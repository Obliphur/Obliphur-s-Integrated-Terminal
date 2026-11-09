"""AI 模型设置弹窗: 字段多 / 字号大时内容要能滚到底, 最下面的字段能点到.

以前窗口固定 600x660, 125% 缩放下 "接口协议 / API Key / 保存" 下面的内容被切掉、点不到.
窗口开在屏幕外, 只读 AI 配置的临时副本, 不碰真实数据.
"""
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
                inner=area.inner.winfo_reqheight())
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
        assert seen["content"] >= seen["inner"] - 2, (scale, seen)   # 滚动区包住全部内容
        assert seen["viewport"] > 0, (scale, seen)
        if scale > 1.0:
            # 放大字号后内容一定放不下: 这时才真的在测滚动
            assert seen["content"] > seen["viewport"], (scale, seen)
finally:
    Modal.wait, ai_dialogs.ScrollArea = orig_wait, orig_area
    config.UI_SCALE = 1.0

root.destroy()
print("AI 模型设置弹窗 check OK (各缩放都能滚到底)")
