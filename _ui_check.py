import os
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
state = {"hold": False, "starts": 0}
page = ContractPage(root, c, on_start=lambda terms: state.__setitem__("starts", state["starts"] + 1),
                    hold=lambda: state["hold"])
page.pack(fill="both", expand=True)
page.refresh()
root.update_idletasks()

assert page.lbl_hold.winfo_manager() == "", "空闲时不应该有小字"
assert page.start_col.winfo_manager() == "pack"
assert page.btn_start._enabled is True
assert str(page.btn_start.cget("bg")) == page.P["start"]

# 休息中: 按钮变灰, 小字在按钮上方, 点不动
state["hold"] = True
page.refresh()
root.update_idletasks()
assert page.lbl_hold.winfo_manager() == "pack", "休息时应该有小字"
assert page.btn_start._enabled is False
assert str(page.btn_start.cget("bg")) == page.P["clear"], "按钮应该变灰"
assert str(page.btn_start.cget("cursor")) == "arrow"
assert page.lbl_hold.winfo_y() < page.btn_start.winfo_y(), "小字应该在按钮上方"
page.btn_start.event_generate("<Button-1>")
root.update()
assert state["starts"] == 0, "灰掉的按钮不能开始合约"

# 休息结束: 恢复
state["hold"] = False
page.refresh()
root.update_idletasks()
assert page.lbl_hold.winfo_manager() == "", "休息结束后小字应该消失"
assert page.btn_start._enabled is True
page.btn_start.event_generate("<Button-1>")
root.update()
assert state["starts"] == 1, "按钮恢复后可以开始合约"

# 合约进行中: 没有小字, 只有提前结束
c.start([], owner="me")
state["hold"] = True
page.refresh()
root.update_idletasks()
assert page.start_col.winfo_manager() == "", "合约中不显示开始行动"
assert page.btn_end.winfo_manager() == "pack"
assert page.lbl_hold.winfo_manager() == ""

root.destroy()
print("contract page UI check OK")
