"""提前启动 (仅 PC, MECHANICS 20).

专注中可以先挑一两个程序"提前启动": 程序现在就跑起来 (或者已经在跑), 但窗口藏起来、静音,
专注结束时再放出来, 程序照常运行. 每次专注只能选一次.

- 挑程序: 开始菜单快捷方式 + App Paths 注册表 + 正在运行的程序 (复用 context_collect 的
  快捷方式解析 / 过滤规则, 和 window_guard 的窗口、进程枚举).
- 启动: 快捷方式 (.lnk) 或 exe 全路径交给 os.startfile; 已经在运行的不再启动.
- 跟踪: 记住这些程序, 以及它们派生出来的子进程 (启动器 → 游戏这种); 每秒:
  · 把它们可见的窗口 (包括后来才出现的子窗口) 藏起来 (最小化的不碰);
  · 把它们自己的音频会话静音 (audiomute.py), 不动别的程序.
- 结束专注: 藏起来的窗口全部显示出来 (不抢焦点), 取消静音.

    python prestart.py            自检 (不启动、不碰真实窗口, 只测挑程序和进程树)
"""

import os
import sys

import audiomute
import window_guard


MAX_APPS = 2                     # 一次专注最多提前启动几个程序


def _clean(name: str) -> str:
    try:
        import context_collect
        return context_collect._clean_app_name(name)
    except Exception:
        return str(name or "").strip()


def _exe_of_link(path: str) -> str:
    try:
        import context_collect
        return str(context_collect._exe_from_lnk(path) or "").strip().lower()
    except Exception:
        return ""


def _normal(name: str, exe: str) -> bool:
    """只留正常程序: 安装器 / 卸载器 / 更新器 / 系统工具 / 驱动 / 配置项都去掉 (规则在 context_collect)."""
    try:
        import context_collect
        return context_collect.is_normal_app(name, exe)
    except Exception:
        return True


def _start_menu_links() -> list:
    """[{"name", "exe", "launch"}]: 开始菜单里的快捷方式 (当前用户 + 所有用户)."""
    import glob
    if sys.platform != "win32":
        return []
    roots = [
        os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
        os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                     r"Microsoft\Windows\Start Menu\Programs"),
    ]
    out = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for path in glob.glob(os.path.join(root, "**", "*.lnk"), recursive=True):
            exe = _exe_of_link(path)
            name = _clean(os.path.splitext(os.path.basename(path))[0])
            if not exe or not exe.endswith(".exe") or not _normal(name, exe):
                continue
            out.append({"name": name, "exe": exe, "launch": path})
    return out


def _app_paths() -> list:
    """[{"name", "exe", "launch"}]: App Paths 注册表里的 exe (默认值就是全路径)."""
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []
    sub = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    roots = [(winreg.HKEY_LOCAL_MACHINE, getattr(winreg, "KEY_WOW64_64KEY", 0)),
             (winreg.HKEY_LOCAL_MACHINE, getattr(winreg, "KEY_WOW64_32KEY", 0)),
             (winreg.HKEY_CURRENT_USER, 0)]
    out = []
    for hive, flags in roots:
        try:
            with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | flags) as key:
                n = winreg.QueryInfoKey(key)[0]
                names = []
                for i in range(n):
                    try:
                        names.append(winreg.EnumKey(key, i))
                    except OSError:
                        continue
        except OSError:
            continue
        for name in names:
            exe = name if name.lower().endswith(".exe") else name + ".exe"
            path = ""
            try:
                with winreg.OpenKey(hive, sub + "\\" + name, 0, winreg.KEY_READ | flags) as k:
                    path = str(winreg.QueryValueEx(k, "")[0] or "").strip()
            except OSError:
                path = ""
            if not path.lower().endswith(".exe") or not os.path.isfile(path):
                path = ""
            name = _clean(os.path.splitext(exe)[0])
            if _normal(name, exe):
                out.append({"name": name, "exe": exe.lower(), "launch": path})
    return out


def _running_exes() -> set:
    """此刻在运行、而且有任务栏窗口的程序 (exe 小写)."""
    out = set()
    try:
        for w in window_guard.taskbar_windows():
            exe = window_guard.exe_name(w.get("pid") or 0).strip().lower()
            if exe:
                out.add(exe)
    except Exception:
        pass
    return out


def scan_programs() -> list:
    """可以提前启动的程序: [{"name", "exe", "launch", "running"}], 按名字排.

    开始菜单的快捷方式优先 (能直接启动); App Paths 里能补上全路径;
    正在运行但两边都没有的程序也列出来 (launch 是空的, 只藏窗口/静音, 不启动).
    """
    running = _running_exes()
    found = {}
    for item in _start_menu_links():
        cur = found.get(item["exe"])
        if cur is None or (not cur.get("launch") and item.get("launch")):
            found[item["exe"]] = item
    for item in _app_paths():
        cur = found.get(item["exe"])
        if cur is None:
            found[item["exe"]] = item
        elif not cur.get("launch") and item.get("launch"):
            cur["launch"] = item["launch"]
    own = {"python.exe", "pythonw.exe"} | set(getattr(window_guard, "PROTECTED_EXES", set()))
    for exe in running:
        if exe in own:
            continue
        cur = found.get(exe)
        if cur is None:
            found[exe] = {"name": _clean(os.path.splitext(exe)[0]), "exe": exe, "launch": ""}
    out = []
    for exe, item in found.items():
        if exe in own:
            continue
        name = str(item.get("name") or "").strip()
        if not name or not _normal(name, exe):
            continue
        launch = item.get("launch") or ""
        if not launch and exe not in running:
            continue                 # 不能启动、又没在跑: 选了也没用
        out.append({"name": name, "exe": exe, "launch": launch,
                    "running": exe in running})
    out.sort(key=lambda a: (not a["running"], a["name"].lower()))
    return out


def descendants(pids, procs=None) -> set:
    """pids 以及它们派生出来的子进程 (启动器 → 游戏这种)."""
    procs = window_guard.list_processes_full() if procs is None else procs
    out = {int(p) for p in pids}
    changed = True
    while changed:
        changed = False
        for pid, ppid, _exe in procs:
            if ppid in out and pid not in out:
                out.add(int(pid))
                changed = True
    return out


class Prestart:
    """一次专注的"提前启动": 启动、藏窗口、静音; 专注结束时全部放出来."""

    def __init__(self):
        self.items = []          # 选中的程序 {"name", "exe", "launch", "running"}
        self.active = False
        self.pids = set()        # 跟踪的进程 (含子进程)
        self.hidden = set()      # 藏起来的窗口
        self._exes = set()

    @property
    def chosen(self) -> bool:
        """这次专注已经选过了 (按钮要藏起来, 不可更改)."""
        return bool(self.items)

    def start(self, items) -> list:
        """开始: 没在运行的程序启动它, 然后马上藏窗口 + 静音. 返回启动失败的名字."""
        self.items = [dict(it) for it in (items or [])][:MAX_APPS]
        self.active = True
        self.pids = set()
        self.hidden = set()
        self._exes = {str(it.get("exe") or "").lower() for it in self.items if it.get("exe")}
        failed = []
        for it in self.items:
            if it.get("running") or not it.get("launch"):
                continue
            try:
                os.startfile(it["launch"])
            except OSError:
                failed.append(str(it.get("name") or it.get("exe") or "?"))
        self.step()
        return failed

    def exe_names(self) -> set:
        """跟踪的进程 (含子进程) 的 exe 名: 网络名单放行它们用 (MECHANICS 20).
        不在提前启动状态时返回空集合."""
        if not self.active or not self.pids:
            return set()
        try:
            procs = window_guard.list_processes_full()
        except Exception:
            return set()
        return {exe for pid, _ppid, exe in procs if pid in self.pids}

    def step(self):
        """每秒调一次: 找进程 (含新出现的子进程), 藏窗口, 静音."""
        if not self.active:
            return
        try:
            procs = window_guard.list_processes_full()
        except Exception:
            return
        pids = {pid for pid, _ppid, exe in procs if exe in self._exes}
        pids = descendants(pids, procs)
        pids.discard(os.getpid())
        self.pids = pids
        try:
            for w in window_guard.list_top_windows():
                hwnd = w.get("hwnd")
                if w.get("pid") not in pids or not w.get("visible") or hwnd in self.hidden:
                    continue
                if window_guard.is_iconic(hwnd):        # 最小化的不碰 (本来就看不见)
                    continue
                if window_guard.hide_window(hwnd):
                    self.hidden.add(hwnd)
        except Exception:
            pass
        try:
            if pids:
                audiomute.set_muted(pids, True)         # 新出现的会话也静音
        except Exception:
            pass

    def stop(self):
        """结束专注: 显示藏起来的窗口 (不抢焦点), 取消静音."""
        self.active = False
        for hwnd in list(self.hidden):
            try:
                if window_guard.window_alive(hwnd):
                    window_guard.show_window(hwnd)
            except Exception:
                pass
        self.hidden.clear()
        try:
            if self.pids:
                audiomute.set_muted(self.pids, False)
        except Exception:
            pass
        self.pids = set()
        self.items = []


def _selftest():
    if sys.platform != "win32":
        return "prestart selftest OK (非 Windows, 跳过)"
    apps = scan_programs()
    assert apps and all({"name", "exe", "launch", "running"} <= set(a) for a in apps)
    assert all(a["exe"].endswith(".exe") for a in apps)
    exes = [a["exe"] for a in apps]
    assert len(exes) == len(set(exes)), "同一个 exe 只列一次"
    print(f"扫到 {len(apps)} 个程序, 其中 {sum(1 for a in apps if a['running'])} 个正在运行")
    # 只留正常程序: 安装器 / 卸载器 / 系统工具 / 驱动 / 配置项 / 运行时 都不在清单里
    for name, exe in [("Setup", "setup.exe"), ("Uninstall A", "unins000.exe"),
                      ("安装向导", "setup.exe"), ("驱动人生", "drivergenius.exe"),
                      ("控制面板", "control.exe"), ("注册表编辑器", "regedit.exe"),
                      ("pythonw3.13", "pythonw3.13.exe"), ("Node.js", "node.exe")]:
        assert not _normal(name, exe), (name, exe)
    for name, exe in [("Steam", "steam.exe"), ("微信", "weixin.exe"),
                      ("MuseScore Studio 4", "musescore4.exe")]:
        assert _normal(name, exe), (name, exe)
    for a in apps:
        low = (a["name"] + " " + a["exe"]).lower()
        assert not any(k in low for k in ("setup", "uninstall", "unins", "installer")), a
        assert a["launch"] or a["running"], "不能启动又没在跑的不要"

    # 进程树: 1 → 2 → 3, 4 无关
    procs = [(1, 0, "a.exe"), (2, 1, "b.exe"), (3, 2, "c.exe"), (4, 0, "d.exe")]
    assert descendants({1}, procs) == {1, 2, 3}
    assert descendants({4}, procs) == {4}
    assert descendants(set(), procs) == set()

    ps = Prestart()
    assert not ps.chosen
    # 用一个不存在的程序测流程: 不启动、不藏任何东西
    ps.start([{"name": "x", "exe": "oit-selftest-nonexistent.exe", "launch": "", "running": True}])
    assert ps.chosen and ps.active and ps.items
    ps.step()
    assert not ps.hidden
    ps.stop()
    assert not ps.chosen and not ps.active and not ps.hidden
    return "prestart selftest OK"


if __name__ == "__main__":
    print(_selftest())
