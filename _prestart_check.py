"""提前启动的逻辑 + 弹窗自检 (不启动真程序, 不碰真窗口)."""

import tkinter as tk

import config
config.UI_SCALE = 1.0

import audiomute
import prestart
import window_guard
from pages.prestart_dialog import PrestartDialog

# ---------- 1. 弹窗: 搜索 / 最多选 2 个 ----------

root = tk.Tk()
root.withdraw()

apps = [
    {"name": "Alpha", "exe": "alpha.exe", "launch": r"C:\a\Alpha.lnk", "running": False},
    {"name": "Beta", "exe": "beta.exe", "launch": r"C:\a\Beta.lnk", "running": True},
    {"name": "Gamma", "exe": "gamma.exe", "launch": "", "running": True},
    {"name": "Delta", "exe": "delta.exe", "launch": r"C:\a\Delta.lnk", "running": False},
]
got = []
dlg = PrestartDialog(root, apps, got.append, limit=2)
root.update()
assert dlg.lbl_count.cget("text") == "已选 0/2"
assert str(dlg.btn_ok.cget("state")) == "disabled"
assert dlg.listbox.size() == 4
assert "正在运行" in dlg.listbox.get(1)

dlg.listbox.selection_set(0)
dlg.listbox.selection_set(1)
dlg._on_select()
assert dlg.lbl_count.cget("text") == "已选 2/2"
assert str(dlg.btn_ok.cget("state")) == "normal"

dlg.listbox.selection_set(2)          # 第三个会被去掉
dlg._on_select()
assert len(dlg._selected()) == 2, dlg._selected()

dlg.var.set("gam")                    # 搜索
root.update()
assert dlg.listbox.size() == 1 and "Gamma" in dlg.listbox.get(0)
dlg.var.set("")
root.update()
assert dlg.listbox.size() == 4

dlg.listbox.selection_clear(0, "end")
dlg.listbox.selection_set(0)
dlg.listbox.selection_set(3)
dlg._ok()
root.update()
assert len(got) == 1 and [a["name"] for a in got[0]] == ["Alpha", "Delta"], got

# 取消: 什么都不交
got2 = []
dlg2 = PrestartDialog(root, apps, got2.append, limit=2)
root.update()
dlg2.close()
root.update()
assert got2 == []

# ---------- 2. Prestart: 藏窗口 / 子进程 / 静音 / 恢复 ----------

state = {"hidden": set(), "shown": [], "muted": []}
windows = [
    {"hwnd": 101, "pid": 10, "visible": True, "title": "main"},
    {"hwnd": 102, "pid": 10, "visible": False, "title": "tray"},
    {"hwnd": 103, "pid": 11, "visible": True, "title": "child popup"},     # 子进程派生出来的
    {"hwnd": 104, "pid": 99, "visible": True, "title": "unrelated"},
    {"hwnd": 105, "pid": 10, "visible": True, "title": "minimized"},
]
procs = [(10, 0, "demo.exe"), (11, 10, "helper.exe"), (99, 0, "other.exe")]

window_guard.list_processes_full = lambda: list(procs)
window_guard.list_top_windows = lambda: [dict(w) for w in windows]
window_guard.is_iconic = lambda hwnd: hwnd == 105
window_guard.window_alive = lambda hwnd: True
window_guard.hide_window = lambda hwnd: state["hidden"].add(hwnd) or True
window_guard.show_window = lambda hwnd: state["shown"].append(hwnd) or True
audiomute.set_muted = lambda pids, muted: state["muted"].append((set(pids), bool(muted))) or len(pids)

ps = prestart.Prestart()
failed = ps.start([{"name": "Demo", "exe": "demo.exe", "launch": "", "running": True}])
assert failed == []
assert ps.chosen and ps.active
assert state["hidden"] == {101, 103}, state["hidden"]      # 隐藏的 / 无关的 / 最小化的都不碰
assert state["muted"] and state["muted"][-1] == ({10, 11}, True)

# 子进程后来又开了一个窗口: 下一秒要跟着藏
windows.append({"hwnd": 106, "pid": 11, "visible": True, "title": "late child"})
ps.step()
assert state["hidden"] == {101, 103, 106}, state["hidden"]

ps.stop()
assert set(state["shown"]) == {101, 103, 106}, state["shown"]
assert state["muted"][-1][1] is False, "结束专注要取消静音"
assert not ps.active and not ps.chosen and not ps.hidden

# 联网名单放行 (MECHANICS 20): 跟踪的进程 (含子进程) 的 exe 名
ps2 = prestart.Prestart()
assert ps2.exe_names() == set(), "没在提前启动时没有放行名单"
ps2.active = True
ps2.pids = {10, 11}
assert ps2.exe_names() == {"demo.exe", "helper.exe"}, ps2.exe_names()
ps2.pids = {99}
assert ps2.exe_names() == {"other.exe"}

root.destroy()
print("prestart check OK")
