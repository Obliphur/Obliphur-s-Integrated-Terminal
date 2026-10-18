import os, tempfile, tkinter as tk
import config
config.UI_SCALE = 1.0
import contract as C
from pages.contract_page import ContractPage
root = tk.Tk(); root.withdraw()
c = C.Contract(tempfile.mkdtemp())
state = {"hold": False, "starts": 0}
page = ContractPage(root, c, on_start=lambda terms: state.__setitem__("starts", state["starts"] + 1), hold=lambda: state["hold"])
page.pack(fill="both", expand=True)
page.refresh(); state["hold"] = True; page.refresh()
root.update_idletasks()
print("hold y/h", page.lbl_hold.winfo_y(), page.lbl_hold.winfo_height(), page.lbl_hold.winfo_manager(), page.lbl_hold.winfo_ismapped())
print("btn  y/h", page.btn_start.winfo_y(), page.btn_start.winfo_height(), page.btn_start.winfo_manager(), page.btn_start.winfo_ismapped())
print("start_col h", page.start_col.winfo_height(), page.start_col.winfo_ismapped())
print("inner h", page.start_col.master.winfo_height())
print("pack info", page.start_col.pack_info() if page.start_col.winfo_manager()=="pack" else None)
print("hold info", page.lbl_hold.pack_info() if page.lbl_hold.winfo_manager()=="pack" else None)
root.destroy()
