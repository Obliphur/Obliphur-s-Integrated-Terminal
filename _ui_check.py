"""合约页: 休息 / 联网中不能开始行动 (按钮变灰 + 上方一行提示), 结束后恢复. 窗口开在屏幕外."""
import tempstore
tempstore.session("ui_check")            # 临时文件放在 %TEMP%\OIT 里, 退出时整个删掉 (tempstore.py)

import tempfile
import tkinter as tk

import config
config.UI_SCALE = 1.0
import contract as C
from pages.contract_page import ContractPage

root = tk.Tk()
root.geometry("1280x860+5000+5000")
root.update()
c = C.Contract(tempfile.mkdtemp())
state = {"why": "", "starts": 0}
page = ContractPage(root, c, on_start=lambda terms: state.__setitem__("starts", state["starts"] + 1),
                    blocked=lambda: state["why"])
page.pack(fill="both", expand=True)
page.refresh()
root.update_idletasks()

# 空闲: 没有提示, 按钮亮着
assert page.lbl_block.winfo_manager() == "", "空闲时不应该有提示"
assert page.start_box.winfo_manager() == "pack"
assert str(page.btn_start.cget("bg")) == page.P["start"]
assert str(page.btn_start.cget("cursor")) == "hand2"

# 休息中: 按钮变灰, 提示在按钮上方, 点不动
state["why"] = "休息中, 不能开始行动"
page.refresh()
root.update_idletasks()
assert page.lbl_block.winfo_manager() == "pack", "休息时应该有一行提示"
assert str(page.btn_start.cget("bg")) == page.P["start_off"], "按钮应该变灰"
assert str(page.btn_start.cget("cursor")) == "arrow"
assert page.lbl_block.winfo_y() < page.btn_start.winfo_y(), "提示应该在按钮上方"
page.btn_start.event_generate("<Button-1>")
root.update()
assert state["starts"] == 0, "灰掉的按钮不能开始合约"

# 休息结束: 恢复
state["why"] = ""
page.refresh()
root.update_idletasks()
assert page.lbl_block.winfo_manager() == "", "休息结束后提示应该消失"
page.btn_start.event_generate("<Button-1>")
root.update()
assert state["starts"] == 1, "按钮恢复后可以开始合约"

# 合约进行中: 没有提示, 只有提前结束
c.start([], owner="me")
state["why"] = "休息中"
page.refresh()
root.update_idletasks()
assert page.btn_start.winfo_manager() == "", "合约中不显示开始行动"
assert page.btn_end.winfo_manager() == "pack"
assert page.lbl_block.winfo_manager() == ""

root.destroy()
print("contract page UI check OK")
