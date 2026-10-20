"""提前启动的逻辑 + 弹窗自检 (不启动真程序, 不碰真窗口)."""

import os
import shutil
import tempfile
import tkinter as tk

import config
config.UI_SCALE = 1.0

import audiomute
import prestart
import window_guard
from pages.prestart_dialog import PrestartDialog

# ---------- 1. 弹窗: 点一行只是选中 (能连选两个) / ✕ 移除 / 搜索 ----------

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
assert len(dlg.shown) == 4

# 连点两行: 两个都能选中 (以前用 Listbox 普通点击只会选中一个)
dlg.toggle("alpha.exe")
dlg.toggle("beta.exe")
root.update()
assert dlg.picked == ["alpha.exe", "beta.exe"], dlg.picked
assert dlg.lbl_count.cget("text") == "已选 2/2"
assert str(dlg.btn_ok.cget("state")) == "normal"
assert [a["name"] for a in dlg._selected()] == ["Alpha", "Beta"]

# 每一行都是 名字 + 右边的 ✕; 选中的行是强调色
rows = dlg.rows_host.winfo_children()
assert len(rows) == 4, len(rows)
assert [w.cget("text") for w in rows[0].winfo_children() if isinstance(w, tk.Button)] == ["✕"]
assert rows[0].cget("bg") == config.COLORS["accent"] and rows[2].cget("bg") == config.COLORS["bg_card"]

# 滚轮: 整个条目栏 (列表 / 每一行 / 行里的字) 都能滚, 不只是右边那条滚动条
def wheel_bound(w):
    return bool(w.bind("<MouseWheel>"))
assert wheel_bound(dlg.area.canvas) and wheel_bound(dlg.rows_host)
assert all(wheel_bound(r) for r in rows), [r for r in rows if not wheel_bound(r)]
for r in rows:
    assert all(wheel_bound(c) for c in r.winfo_children()), r.winfo_children()

dlg.toggle("gamma.exe")               # 第三个: 放开最早选的那个
root.update()
assert dlg.picked == ["beta.exe", "gamma.exe"], dlg.picked
dlg.toggle("gamma.exe")               # 再点一次: 取消选中
root.update()
assert dlg.picked == ["beta.exe"]

dlg.var.set("gam")                    # 搜索
root.update()
assert len(dlg.shown) == 1 and dlg.shown[0]["name"] == "Gamma"
dlg.var.set("")
root.update()
assert len(dlg.shown) == 4

# ✕ 把程序从清单里去掉 (选中的也一起取消)
dlg.remove("beta.exe")
root.update()
assert "Beta" not in [a["name"] for a in dlg.apps] and dlg.picked == []
assert len(dlg.shown) == 3
rows = dlg.rows_host.winfo_children()
assert all(wheel_bound(r) for r in rows), "重画以后滚轮也要绑上"

dlg.toggle("alpha.exe")
dlg.toggle("delta.exe")
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

# ---------- 1b. 打开文件管理器自己挑程序 ----------

_tmp = tempfile.mkdtemp(prefix="oit_prestart_")
_exe = os.path.join(_tmp, "Demo Game.exe")
open(_exe, "w").close()
_setup = os.path.join(_tmp, "setup.exe")
open(_setup, "w").close()

item = prestart.program_from_path(_exe)
assert item and item["name"] == "Demo Game" and item["exe"] == "demo game.exe", item
assert item["running"] is False and item["launch"] == _exe
assert prestart.program_from_path(_setup) is None, "安装器不能加"
assert prestart.program_from_path(os.path.join(_tmp, "x.txt")) is None
assert prestart.program_from_path(os.path.join(_tmp, "missing.exe")) is None

got3 = []
dlg3 = PrestartDialog(root, [apps[0]], got3.append, limit=2)     # 清单里只有 Alpha
root.update()
assert len(dlg3.shown) == 1
dlg3.add_item(item)                                             # 文件管理器只负责加进清单
root.update()
assert len(dlg3.shown) == 2 and dlg3.shown[1]["name"] == "Demo Game"
assert dlg3.picked == [], "加进清单不等于选中"                    # 点行才选中
dlg3.toggle("demo game.exe")
root.update()
assert [a["name"] for a in dlg3._selected()] == ["Demo Game"]
assert dlg3.lbl_count.cget("text") == "已选 1/2"

dlg3.add_item(item)                                             # 已经在清单里: 不会多一条, 只选中
root.update()
assert len(dlg3.shown) == 2 and dlg3.picked == ["demo game.exe"]

# 再选一个 (现在能连选两个); 加第三个: 清单变长, 选中仍然只有 2 个
dlg3.toggle("alpha.exe")
root.update()
assert [a["name"] for a in dlg3._selected()] == ["Demo Game", "Alpha"], dlg3._selected()
other = os.path.join(_tmp, "Third App.exe")
open(other, "w").close()
dlg3.add_item(prestart.program_from_path(other))
root.update()
assert len(dlg3.shown) == 3 and len(dlg3._selected()) == 2, dlg3._selected()
dlg3.toggle("third app.exe")                                     # 选满后选新的: 放开最早的
root.update()
assert [a["name"] for a in dlg3._selected()] == ["Alpha", "Third App"], dlg3._selected()
dlg3._ok()
root.update()
assert len(got3) == 1 and {a["exe"] for a in got3[0]} == {"alpha.exe", "third app.exe"}, got3

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
assert ps.chosen and ps.active and ps.in_grace and ps.phase == "grace"
assert prestart.GRACE_SECONDS == 180.0
assert state["hidden"] == set(), "操作空窗期不藏窗口"
assert state["muted"] == [], "操作空窗期不静音"
assert ps.pids == {10, 11}, "空窗期里先认进程 (网络名单放行要用)"

# 3 分钟过了: 开始藏窗口 + 静音
ps.grace_until = 0.0
ps.step()
assert ps.phase == "active" and not ps.in_grace
assert state["hidden"] == {101, 103}, state["hidden"]      # 隐藏的 / 无关的 / 最小化的都不碰
assert state["muted"][-1] == ({10, 11}, True)

# 子进程后来又开了一个窗口: 下一秒要跟着藏
windows.append({"hwnd": 106, "pid": 11, "visible": True, "title": "late child"})
ps.step()
assert state["hidden"] == {101, 103, 106}, state["hidden"]

ps.stop()
assert set(state["shown"]) == {101, 103, 106}, state["shown"]
assert state["muted"][-1][1] is False, "结束专注要取消静音"
assert not ps.active and not ps.chosen and not ps.hidden and ps.phase == ""

# 提前终止: 空窗期里点"提前终止", 不用等 3 分钟, 马上藏 + 静音
state["hidden"].clear()
state["muted"].clear()
ps3 = prestart.Prestart()
ps3.start([{"name": "Demo", "exe": "demo.exe", "launch": "", "running": True}])
assert ps3.in_grace and state["hidden"] == set()
ps3.terminate()
assert ps3.phase == "active" and not ps3.in_grace
assert state["hidden"] == {101, 103, 106}, state["hidden"]
assert state["muted"] and state["muted"][-1] == ({10, 11}, True)
ps3.stop()

# 联网名单放行 (MECHANICS 20): 跟踪的进程 (含子进程) 的 exe 名
ps2 = prestart.Prestart()
assert ps2.exe_names() == set(), "没在提前启动时没有放行名单"
ps2.active = True
ps2.pids = {10, 11}
assert ps2.exe_names() == {"demo.exe", "helper.exe"}, ps2.exe_names()
ps2.pids = {99}
assert ps2.exe_names() == {"other.exe"}

root.destroy()
shutil.rmtree(_tmp, ignore_errors=True)
print("prestart check OK")
