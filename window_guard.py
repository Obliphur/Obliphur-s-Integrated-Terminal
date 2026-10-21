"""
任务栏窗口数上限
────────────────
专注中每 WINDOW_CHECK_EVERY 秒数一次任务栏上的窗口 (同一个程序的多个窗口各算一个),
超过 WINDOW_LIMIT 个就提醒一次 (不再随机关窗口); 回到上限以内后再超才再提醒.
合约的"通道阻塞"也按这个数算违反 (contract.window_check), 同样只在专注中算.
休息 / 空闲时数都不数.

"在任务栏上" 的判定 (和 Windows 自己的规则一致):
  · 可见, 没有被 DWM 隐藏 (cloaked: 其他虚拟桌面上的窗口, 挂起的 UWP 窗口等);
  · 没有所有者窗口 (对话框之类不算), 或者带 WS_EX_APPWINDOW;
  · 不是工具窗口 (WS_EX_TOOLWINDOW) 或不可激活窗口 (WS_EX_NOACTIVATE), 除非带 WS_EX_APPWINDOW;
  · 有标题; 不是桌面 / 任务栏本身.
OIT 自己的窗口也计数.

前台程序 (窗口使用时间统计)
──────────────────────────
foreground_program() 返回当前前台窗口属于哪个程序 (exe 名, 不带 .exe);
桌面, 任务栏, 文件资源管理器, 命令行窗口, 锁屏, 以及没有前台窗口时返回 BLANK (空白).
OIT 自己的窗口返回 "OIT". 记时间的部分在 DataManager.add_window_time.

Rules 页的应用名单 (前台窗口)
────────────────────────────
check_focus_rules() 每秒看一次前台窗口: 它的程序在黑名单里 (黑名单模式), 或者不在白名单里
(白名单模式, 离线时) 就立即关掉它 (发给它的顶层窗口, 同样是 WM_CLOSE).
系统界面, 文件资源管理器, 命令行, 任务管理器, 设置, OIT 自己 (含卸载程序) 永远不关;
前台是对话框 (例如"是否保存") 时先不关, 等对话框关掉再说.

    python window_guard.py          自检 (用假的窗口列表, 不碰真实窗口)
"""

import os
import sys

WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000
GWL_EXSTYLE = -20
GW_OWNER = 4
DWMWA_CLOAKED = 14
WM_CLOSE = 0x0010
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4

# 桌面, 任务栏, 开始菜单之类: 满足上面的条件也不算
SHELL_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow",
}


def is_taskbar_window(w: dict) -> bool:
    """w: {visible, cloaked, owned, exstyle, title, cls}"""
    if not w.get("visible") or w.get("cloaked"):
        return False
    if not str(w.get("title") or "").strip():
        return False
    if w.get("cls") in SHELL_CLASSES:
        return False
    ex = int(w.get("exstyle") or 0)
    if ex & WS_EX_APPWINDOW:
        return True
    if w.get("owned"):
        return False
    if ex & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE):
        return False
    return True


# ───────────────────────── Windows API ─────────────────────────

_api = None


def _load_api():
    global _api
    if _api is not None:
        return _api
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [proc, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    # InternalGetWindowText 不给目标窗口发消息, 遇到卡死的程序也不会把 OIT 一起卡住
    user32.InternalGetWindowText.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                    wintypes.LPARAM]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.IsWindow.argtypes = [wintypes.HWND]
    dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                             ctypes.c_void_p, wintypes.DWORD]
    _api = (ctypes, wintypes, user32, dwmapi, proc)
    return _api


_vdm = None


def on_current_desktop(hwnd):
    """这个窗口在不在当前的虚拟桌面上 (Windows 的 IVirtualDesktopManager); 查不出来 (不是 Windows, 接口不可用) 时 None.
    只在界面线程里调用 (COM 按线程初始化)."""
    global _vdm
    if sys.platform != "win32" or not hwnd:
        return None
    import ctypes
    from ctypes import wintypes
    try:
        ole32 = ctypes.windll.ole32
        if _vdm is None:
            ole32.CoInitialize(None)
            clsid, iid = (ctypes.c_byte * 16)(), (ctypes.c_byte * 16)()
            ole32.CLSIDFromString("{AA509086-5CA9-4C25-8F95-589D3C07B48A}", ctypes.byref(clsid))
            ole32.CLSIDFromString("{A5CD92FF-29BE-454C-8D04-D82879FB3F1B}", ctypes.byref(iid))
            ptr = ctypes.c_void_p()
            if ole32.CoCreateInstance(ctypes.byref(clsid), None, 0x17, ctypes.byref(iid), ctypes.byref(ptr)) != 0                     or not ptr.value:
                _vdm = False
                return None
            _vdm = ptr
        if not _vdm:
            return None
        vtbl = ctypes.cast(_vdm, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND, ctypes.POINTER(wintypes.BOOL))(vtbl[3])
        res = wintypes.BOOL(1)
        if fn(_vdm, hwnd, ctypes.byref(res)) != 0:
            return None                     # 例如窗口藏起来了 / 还没显示: 当作查不出来
        return bool(res.value)
    except Exception:
        return None


def _describe(hwnd) -> dict:
    ctypes, wintypes, user32, dwmapi, _proc = _load_api()
    buf = ctypes.create_unicode_buffer(256)
    user32.InternalGetWindowText(hwnd, buf, 256)
    title = buf.value
    cbuf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cbuf, 256)
    cloaked = ctypes.c_int(0)
    try:
        if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked),
                                        ctypes.sizeof(cloaked)) != 0:
            cloaked.value = 0
    except OSError:
        cloaked.value = 0
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return {
        "hwnd": hwnd,
        "visible": bool(user32.IsWindowVisible(hwnd)),
        "cloaked": bool(cloaked.value),
        "owned": bool(user32.GetWindow(hwnd, GW_OWNER)),
        "exstyle": user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF,
        "title": title,
        "cls": cbuf.value,
        "pid": int(pid.value),
    }


def list_top_windows() -> list:
    """全部顶层窗口的信息 (还没筛选). 非 Windows 返回空表."""
    if sys.platform != "win32":
        return []
    ctypes, _wt, user32, _dwm, proc = _load_api()
    hwnds = []

    def cb(hwnd, _lp):
        hwnds.append(hwnd)
        return True

    user32.EnumWindows(proc(cb), 0)
    out = []
    for h in hwnds:
        try:
            out.append(_describe(h))
        except Exception:
            continue
    return out


def exe_name(pid: int) -> str:
    try:
        from netproxy.procinfo import _exe_for_pid
        return _exe_for_pid(pid) if pid else ""
    except Exception:
        return ""


def close_window(hwnd) -> bool:
    if sys.platform != "win32":
        return False
    _ct, _wt, user32, _dwm, _p = _load_api()
    return bool(user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))


def window_alive(hwnd) -> bool:
    if sys.platform != "win32" or not hwnd:
        return False
    _ct, _wt, user32, _dwm, _p = _load_api()
    return bool(user32.IsWindow(hwnd))


def is_iconic(hwnd) -> bool:
    """最小化的窗口 (提前启动不碰它: 本来就看不见)."""
    if sys.platform != "win32" or not hwnd:
        return False
    _ct, _wt, user32, _dwm, _p = _load_api()
    user32.IsIconic.argtypes = [_wt.HWND]
    return bool(user32.IsIconic(hwnd))


def hide_window(hwnd) -> bool:
    """藏起一个窗口 (不结束进程). 提前启动用它把程序窗口藏起来."""
    if sys.platform != "win32" or not hwnd:
        return False
    _ct, _wt, user32, _dwm, _p = _load_api()
    return bool(user32.ShowWindow(hwnd, SW_HIDE))


def show_window(hwnd) -> bool:
    """把藏起来的窗口显示出来 (不抢焦点)."""
    if sys.platform != "win32" or not hwnd:
        return False
    _ct, _wt, user32, _dwm, _p = _load_api()
    return bool(user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE))


# ───────────────────────── 前台程序 ─────────────────────────

BLANK = ""              # 空白: 桌面 / 命令行 / 锁屏 / 没有前台窗口
UNKNOWN = "?"           # 有前台窗口, 但查不到是哪个程序 (例如权限不够)
OWN_NAME = "OIT"

# 桌面, 任务栏, 开始菜单, 以及命令行窗口 (conhost 的经典控制台)
BLANK_CLASSES = SHELL_CLASSES | {"ConsoleWindowClass"}
# 命令提示符 / Windows Terminal 里的命令行, 锁屏, 文件资源管理器 (explorer.exe 的所有窗口)
BLANK_EXES = {"cmd.exe", "conhost.exe", "openconsole.exe", "windowsterminal.exe",
              "lockapp.exe", "explorer.exe"}


def classify_foreground(w, own_pid: int = None) -> str:
    """w: {hwnd, cls, pid, exe} 或 None. 返回程序名 / BLANK / UNKNOWN / OWN_NAME."""
    own_pid = os.getpid() if own_pid is None else own_pid
    if not w or not w.get("hwnd"):
        return BLANK
    if w.get("cls") in BLANK_CLASSES:
        return BLANK
    if w.get("pid") and w.get("pid") == own_pid:
        return OWN_NAME
    exe = str(w.get("exe") or "").strip().lower()
    if not exe:
        return UNKNOWN
    if exe in BLANK_EXES:
        return BLANK
    return exe[:-4] if exe.endswith(".exe") else exe


def foreground_window():
    """当前前台窗口的 {hwnd, cls, pid, exe}; 没有前台窗口或非 Windows 返回 None."""
    if sys.platform != "win32":
        return None
    ctypes, wintypes, user32, _dwm, _p = _load_api()
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    cbuf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cbuf, 256)
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return {"hwnd": hwnd, "cls": cbuf.value, "pid": int(pid.value),
            "exe": exe_name(int(pid.value))}


def foreground_program() -> str:
    return classify_foreground(foreground_window())


# ───────────────────────── 应用名单: 关掉前台窗口 ─────────────────────────

# 不管名单怎么写都不关的程序: 系统界面, 资源管理器, 命令行, 任务管理器, 设置, 登录 / 权限确认
PROTECTED_EXES = BLANK_EXES | {
    "taskmgr.exe", "systemsettings.exe", "searchhost.exe", "searchapp.exe",
    "startmenuexperiencehost.exe", "shellexperiencehost.exe", "applicationframehost.exe",
    "textinputhost.exe", "dwm.exe", "csrss.exe", "winlogon.exe", "logonui.exe",
    "consent.exe", "credentialuibroker.exe", "lockapp.exe",
}


def focus_violation(w, allowed, own_pid: int = None, app_name: str = "Obliphur's Integrated Terminal"):
    """w: {hwnd, root, cls, pid, exe, title, owned}; allowed(exe) -> (允许?, 原因).
    该关时返回原因, 否则 None."""
    own_pid = os.getpid() if own_pid is None else own_pid
    if not w or not w.get("hwnd"):
        return None
    if w.get("cls") in BLANK_CLASSES or (w.get("pid") and w.get("pid") == own_pid):
        return None
    exe = str(w.get("exe") or "").strip().lower()
    if not exe or exe in PROTECTED_EXES:
        return None
    # 单独运行的卸载程序也是 OIT 的窗口
    if exe in ("python.exe", "pythonw.exe") and app_name in str(w.get("title") or ""):
        return None
    if w.get("owned"):
        return None
    ok, why = allowed(exe)
    return None if ok else why


def _root_window(hwnd):
    _ct, _wt, user32, _dwm, _p = _load_api()
    user32.GetAncestor.argtypes = [_wt.HWND, _wt.UINT]
    user32.GetAncestor.restype = _wt.HWND
    return user32.GetAncestor(hwnd, 3) or hwnd          # GA_ROOTOWNER


def check_focus_rules(allowed, close=close_window):
    """前台窗口违反应用名单就关掉它. 返回 (exe, 标题, 原因) 或 None."""
    w = foreground_window()
    if not w:
        return None
    try:
        d = _describe(w["hwnd"])
        w.update(title=d["title"], owned=d["owned"])
    except Exception:
        pass
    why = focus_violation(w, allowed)
    if why is None:
        return None
    root = _root_window(w["hwnd"])
    if not close(root):
        return None
    return w.get("exe") or "", str(w.get("title") or ""), why


# ───────────────────────── 检查一次 ─────────────────────────

def taskbar_windows(windows=None, own_pid: int = None, include_own: bool = False,
                    exclude_pids=None) -> list:
    """任务栏上的窗口; include_own=False 时不含 OIT 自己的 (窗口数要算上 OIT 时传 True).
    exclude_pids: 这些进程的窗口不算 (提前启动的程序, MECHANICS 20)."""
    own_pid = os.getpid() if own_pid is None else own_pid
    skip = set(exclude_pids or ())
    windows = list_top_windows() if windows is None else windows
    return [w for w in windows
            if is_taskbar_window(w) and (include_own or w.get("pid") != own_pid)
            and w.get("pid") not in skip]


def count_windows(windows=None, own_pid: int = None, exclude_pids=None) -> int:
    """任务栏上的窗口数 (含 OIT 自己的窗口); 专注中超过上限就提醒 (由调用方比较).
    提前启动的程序 (exclude_pids) 显示的窗口也不算."""
    return len(taskbar_windows(windows, own_pid, include_own=True, exclude_pids=exclude_pids))


class WindowAges:
    """每个窗口第一次被看到的时刻 (MECHANICS 12): 开着超过 min_age 秒的窗口才计入窗口数.
    按 (hwnd, 进程) 认窗口; 窗口关掉后忘掉, 再打开重新算. OIT 启动前就开着的窗口从第一次看到时算起."""

    def __init__(self):
        self.first = {}

    @staticmethod
    def _key(w: dict):
        return w.get("hwnd"), w.get("pid")

    def update(self, windows, now: float):
        keys = {self._key(w) for w in windows}
        self.first = {k: self.first.get(k, now) for k in keys}

    def old(self, windows, now: float, min_age: float) -> list:
        return [w for w in windows if now - self.first.get(self._key(w), now) >= float(min_age)]


def remind_due(over: bool, last_at: float, now: float, cooldown: float = 120.0) -> bool:
    """窗口数超限的提醒到时间了吗: 刚超过时立刻提醒; 之后每次隔 cooldown 秒 (还没解决就再响一次).
    回到上限以内以后调用方把 last_at 清零."""
    if not over:
        return False
    return last_at <= 0 or (now - last_at) >= max(1.0, float(cooldown))


# ───────────────────────── 合约: 乐理之灾 / 通道阻塞 (MECHANICS 18) ─────────────────────────

def list_processes() -> list:
    """[(pid, exe 小写)]; 非 Windows 返回空表."""
    return [(pid, exe) for pid, _ppid, exe in list_processes_full()]


def list_processes_full() -> list:
    """[(pid, 父进程 pid, exe 小写)]; 非 Windows 返回空表 (提前启动要顺着父子进程找)."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PE)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PE)]
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)          # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    out = []
    try:
        e = PE()
        e.dwSize = ctypes.sizeof(PE)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out.append((int(e.th32ProcessID), int(e.th32ParentProcessID), str(e.szExeFile).lower()))
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def audio_title(title: str, exts) -> bool:
    """窗口标题里有音频文件名 (例如 "song.mp3 - VLC"), 就当作正在用它放音频."""
    t = str(title or "").lower()
    for ext in exts:
        i = t.find(ext)
        while i > 0:
            after = t[i + len(ext):i + len(ext) + 1]
            if not (after.isalnum() or after == "_"):
                return True
            i = t.find(ext, i + 1)
    return False


def find_music(music_exes, audio_exts, procs=None, windows=None, own_pid: int = None, exe_of=exe_name):
    """正在用的音乐播放软件 (进程在就算, 缩到托盘里也照样在放), 或者标题里有音频文件的窗口.
    返回 (exe, 标题) 或 None. 只查看, 不关."""
    own_pid = os.getpid() if own_pid is None else own_pid
    for pid, exe in (list_processes() if procs is None else procs):
        if exe in music_exes and pid != own_pid:
            return exe, ""
    for w in taskbar_windows(windows, own_pid):
        title = str(w.get("title") or "")
        if not audio_title(title, audio_exts):
            continue
        exe = (exe_of(w.get("pid") or 0) or "").lower()
        if exe not in PROTECTED_EXES:
            return exe or "?", title
    return None


def find_banned(exes, titles, browsers, site_title, procs=None, windows=None, own_pid: int = None,
                exe_of=exe_name):
    """合约禁止打开的一类软件 (无言为真 / 禁游令 / 低帧的诅咒): 进程在就算 (缩到托盘里也算);
    titles 不空时, 浏览器窗口的标题是这些网站之一也算 (site_title(标题, titles)). 返回 (exe, 标题) 或 None. 只查看, 不关."""
    own_pid = os.getpid() if own_pid is None else own_pid
    for pid, exe in (list_processes() if procs is None else procs):
        if exe in exes and pid != own_pid:
            return exe, ""
    if not titles:
        return None
    for w in taskbar_windows(windows, own_pid):
        title = str(w.get("title") or "")
        if not title:
            continue
        exe = (exe_of(w.get("pid") or 0) or "").lower()
        if exe in browsers and site_title(title, titles):
            return exe, title
    return None


def _selftest():
    def win(hwnd, title="w", pid=100, visible=True, cloaked=False, owned=False, ex=0,
            cls="AppWindow"):
        return {"hwnd": hwnd, "title": title, "pid": pid, "visible": visible,
                "cloaked": cloaked, "owned": owned, "exstyle": ex, "cls": cls}

    # 判定规则
    # 开着超过 2 分钟的窗口才计入 (WindowAges)
    ages = WindowAges()
    ws = [win(1), win(2)]
    ages.update(ws, 1000.0)
    assert ages.old(ws, 1000.0, 120) == []
    ages.update(ws + [win(3)], 1060.0)
    assert [w["hwnd"] for w in ages.old(ws + [win(3)], 1120.0, 120)] == [1, 2], "第 3 个才开 1 分钟"
    ages.update([win(2), win(3)], 1200.0)
    assert [w["hwnd"] for w in ages.old([win(1), win(2), win(3)], 1200.0, 120)] == [2, 3], "关掉的忘掉, 再出现重新算"
    assert ages.old([win(3, pid=7)], 1200.0, 120) == [], "同一个句柄换了进程算新窗口"
    assert is_taskbar_window(win(1))
    assert not is_taskbar_window(win(1, visible=False))
    assert not is_taskbar_window(win(1, cloaked=True))
    assert not is_taskbar_window(win(1, title="  "))
    assert not is_taskbar_window(win(1, owned=True)), "对话框不算"
    assert is_taskbar_window(win(1, owned=True, ex=WS_EX_APPWINDOW))
    assert not is_taskbar_window(win(1, ex=WS_EX_TOOLWINDOW))
    assert not is_taskbar_window(win(1, ex=WS_EX_NOACTIVATE))
    assert is_taskbar_window(win(1, ex=WS_EX_TOOLWINDOW | WS_EX_APPWINDOW))
    assert not is_taskbar_window(win(1, title="Program Manager", cls="Progman"))
    assert not is_taskbar_window(win(1, cls="Shell_TrayWnd"))

    # 同一个程序的多个窗口各算一个; OIT 自己的窗口也算 (只是数, 不再关)
    ws = [win(1, pid=5), win(2, pid=5), win(3, pid=6), win(4, pid=7),
          win(5, pid=99), win(6, pid=8, owned=True), win(7, pid=8, cloaked=True)]
    assert len(taskbar_windows(ws, own_pid=99)) == 4
    assert len(taskbar_windows(ws, own_pid=99, include_own=True)) == 5
    assert count_windows(ws, own_pid=99) == 5
    ws.append(win(8, pid=9))
    assert count_windows(ws, own_pid=99) == 6, "多一个任务栏窗口就多一个"
    assert count_windows([win(i, pid=i) for i in range(1, 20)], own_pid=0) == 19
    # 提前启动 (MECHANICS 20) 的程序: 窗口显示出来也不算进窗口数
    assert count_windows(ws, own_pid=99, exclude_pids={5}) == 4
    assert count_windows(ws, own_pid=99, exclude_pids={5, 6, 99}) == 2
    # 提醒冷却: 刚超过立刻提醒, 之后每 2 分钟一次 (还没解决就再响)
    assert remind_due(True, 0.0, 1000.0) and not remind_due(True, 1000.0, 1060.0)
    assert remind_due(True, 1000.0, 1120.0) and not remind_due(False, 0.0, 1000.0)
    assert remind_due(True, 990.0, 1000.0, 30) is False and remind_due(True, 970.0, 1000.0, 30)
    assert count_windows(ws, own_pid=99, exclude_pids=set()) == 6

    # 前台程序
    def fg(cls="Chrome_WidgetWin_1", pid=100, exe="chrome.exe", hwnd=1):
        return {"hwnd": hwnd, "cls": cls, "pid": pid, "exe": exe}
    assert classify_foreground(fg(), own_pid=99) == "chrome"
    assert classify_foreground(fg(exe="Code.EXE"), own_pid=99) == "code"
    assert classify_foreground(None, own_pid=99) == BLANK
    assert classify_foreground(fg(hwnd=0), own_pid=99) == BLANK
    assert classify_foreground(fg(cls="Progman", exe="explorer.exe"), own_pid=99) == BLANK
    assert classify_foreground(fg(cls="WorkerW", exe="explorer.exe"), own_pid=99) == BLANK
    assert classify_foreground(fg(cls="CabinetWClass", exe="explorer.exe"),
                               own_pid=99) == BLANK, "文件资源管理器也记为空白"
    assert classify_foreground(fg(cls="CabinetWClass", exe="Explorer.EXE"), own_pid=99) == BLANK
    assert classify_foreground(fg(cls="ConsoleWindowClass", exe="cmd.exe"), own_pid=99) == BLANK
    assert classify_foreground(fg(cls="CASCADIA_HOSTING_WINDOW_CLASS",
                                  exe="WindowsTerminal.exe"), own_pid=99) == BLANK
    assert classify_foreground(fg(pid=99, exe="python.exe"), own_pid=99) == OWN_NAME
    assert classify_foreground(fg(exe=""), own_pid=99) == UNKNOWN

    # 应用名单: 前台窗口
    black = lambda exe: (exe != "steam.exe", "blacklist-app")
    white = lambda exe: (exe in ("code.exe",), "not-whitelisted")
    assert focus_violation(fg(exe="steam.exe"), black, own_pid=99) == "blacklist-app"
    assert focus_violation(fg(exe="Steam.EXE"), black, own_pid=99) == "blacklist-app"
    assert focus_violation(fg(exe="chrome.exe"), black, own_pid=99) is None
    assert focus_violation(fg(exe="chrome.exe"), white, own_pid=99) == "not-whitelisted"
    assert focus_violation(fg(exe="code.exe"), white, own_pid=99) is None
    for safe in ("explorer.exe", "taskmgr.exe", "cmd.exe", "SystemSettings.exe"):
        assert focus_violation(fg(exe=safe), white, own_pid=99) is None, safe
    assert focus_violation(fg(pid=99, exe="python.exe"), white, own_pid=99) is None, "OIT 自己"
    assert focus_violation(dict(fg(exe="python.exe"), title="Uninstall Obliphur's Integrated Terminal"),
                           white, own_pid=99) is None, "卸载程序"
    assert focus_violation(dict(fg(exe="steam.exe"), owned=True), black, own_pid=99) is None, "对话框先不关"
    assert focus_violation(fg(exe=""), white, own_pid=99) is None, "查不到程序的不关"
    assert focus_violation(None, white, own_pid=99) is None

    # 合约 · 乐理之灾: 音乐软件在运行, 或者标题里有音频文件的窗口
    exts = (".mp3", ".flac")
    assert audio_title("Song.MP3 - VLC media player", exts) and audio_title("a.flac", exts)
    assert not audio_title("player.mp3x - notes", exts) and not audio_title("mp3 converter", exts)
    wins = [win(2, title="readme", pid=101), win(3, title="b.mp3", pid=102), win(1, title="a.mp3 - Chrome", pid=100)]
    exes = {100: "chrome.exe", 101: "code.exe", 102: "explorer.exe"}
    assert find_music({"cloudmusic.exe"}, exts, procs=[(6, "code.exe"), (5, "cloudmusic.exe")], windows=wins,
                      own_pid=99, exe_of=exes.get) == ("cloudmusic.exe", "")
    assert find_music({"cloudmusic.exe"}, exts, procs=[(99, "cloudmusic.exe")], windows=wins,
                      own_pid=99, exe_of=exes.get) == ("chrome.exe", "a.mp3 - Chrome"), "资源管理器不算"
    assert find_music({"cloudmusic.exe"}, exts, procs=[], windows=wins[:2], own_pid=99, exe_of=exes.get) is None
    return "window_guard selftest OK"


if __name__ == "__main__":
    print(_selftest())
    if sys.platform == "win32" and "--list" in sys.argv:
        for w in taskbar_windows():
            print(w["hwnd"], exe_name(w["pid"]), w["title"])
        print("foreground:", repr(foreground_program()))
