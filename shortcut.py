"""
快捷方式 (MECHANICS.md 1.1: 第一次打开时的介绍窗口里勾选)
──────────────────────────────────────────────────────
    桌面          在当前用户的桌面上建 "OIT.lnk"
    开始菜单      在开始菜单的程序文件夹里建 "OIT.lnk" (出现在"所有应用"里), 再试着固定到"开始"
    任务栏        Windows 10 1809 之后不允许程序自己固定到任务栏: 能用"固定到任务栏"命令就用,
                 不能就在资源管理器里选中开始菜单里的 OIT.lnk, 由用户右键固定
快捷方式指向 pythonw.exe + 本文件夹的 main.py (打包成 exe 时直接指向 exe).
用 Windows 自带的 WScript.Shell / Shell.Application, 不需要额外安装.

    python shortcut.py       自检 (不真的创建)
"""

import os
import subprocess
import sys

SHORTCUT_NAME = "OIT.lnk"


def _ps_quote(text: str) -> str:
    """PowerShell 单引号字符串: 里面的单引号写两遍."""
    return "'" + str(text).replace("'", "''") + "'"


def launch_target():
    """(要运行的程序, 参数, 工作目录)."""
    here = os.path.dirname(os.path.abspath(__file__))
    if getattr(sys, "frozen", False):
        return sys.executable, "", os.path.dirname(sys.executable)
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return exe, f'"{os.path.join(here, "main.py")}"', here


def desktop_dir() -> str:
    """当前用户的桌面 (被 OneDrive 等重定向过也能找到)."""
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                capture_output=True, text=True, timeout=15, creationflags=0x08000000)
            path = (r.stdout or "").strip()
            if path and os.path.isdir(path):
                return path
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def start_menu_dir() -> str:
    return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                        "Microsoft", "Windows", "Start Menu", "Programs")


def shortcut_path() -> str:
    return os.path.join(desktop_dir(), SHORTCUT_NAME)


def exists() -> bool:
    return os.path.exists(shortcut_path())


def _ps(script: str, timeout: float = 30):
    return subprocess.run(["powershell", "-NoProfile", "-STA", "-Command",
                           "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script],
                          capture_output=True, timeout=timeout, creationflags=0x08000000)


def verb_script(lnk: str, patterns) -> str:
    """对快捷方式执行名字符合 patterns 的右键命令 (去掉 & 后用 -like 比较); 执行了就输出 did."""
    folder, name = os.path.split(lnk)
    cond = " -or ".join("$n -like " + _ps_quote(p) for p in patterns)
    return ("$o = (New-Object -ComObject Shell.Application).Namespace(" + _ps_quote(folder) + ").ParseName("
            + _ps_quote(name) + "); foreach ($v in $o.Verbs()) { $n = $v.Name.Replace('&', ''); "
            "if ((" + cond + ") -and $n -notlike '*取消*' -and $n -notlike 'Unpin*') { $v.DoIt(); 'did'; break } }")


PIN_START = ("*固定到*开始*", "Pin to Start*")
PIN_TASKBAR = ("*固定到任务栏*", "Pin to taskbar*")


def pin(lnk: str, patterns) -> bool:
    """试着执行"固定到…"命令; 系统不提供这个命令时返回 False. (执行了也不一定生效, Windows 11 会忽略.)"""
    try:
        r = _ps(verb_script(lnk, patterns))
        return "did" in (r.stdout or b"").decode("utf-8", "replace")
    except Exception:
        return False


def reveal(path: str):
    """在资源管理器里选中这个文件."""
    try:
        subprocess.Popen(["explorer", "/select," + path])
    except Exception:
        pass


def install(desktop: bool, start_menu: bool, taskbar: bool, description: str = "Obliphur's Integrated Terminal"):
    """第一次打开时勾选的几项. 返回 {项: (成功?, 说明)}; 任务栏的说明是 "pinned" 或 "manual"."""
    out = {}
    if desktop:
        out["desktop"] = create(description, desktop_dir())
    start_lnk = None
    if start_menu or taskbar:
        ok, info = create(description, start_menu_dir())
        start_lnk = info if ok else None
        if start_menu:
            out["start"] = (ok, info)
            if ok:
                pin(info, PIN_START)
    if taskbar:
        if start_lnk and pin(start_lnk, PIN_TASKBAR):
            out["taskbar"] = (True, "pinned")
        elif start_lnk:
            reveal(start_lnk)
            out["taskbar"] = (False, "manual")
        else:
            out["taskbar"] = (False, "no shortcut")
    return out


def icon_file() -> str:
    """OIT 的图标 (oit.ico, make_icon.py 生成) 在不在程序文件夹里: 在就返回路径, 不在 ""."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oit.ico")
    return p if os.path.exists(p) else ""


def build_script(lnk: str, target: str, args: str, workdir: str, description: str, icon: str = "") -> str:
    return ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + _ps_quote(lnk) + "); "
            "$s.TargetPath = " + _ps_quote(target) + "; "
            "$s.Arguments = " + _ps_quote(args) + "; "
            "$s.WorkingDirectory = " + _ps_quote(workdir) + "; "
            "$s.Description = " + _ps_quote(description) + "; "
            + (("$s.IconLocation = " + _ps_quote(icon) + "; ") if icon else "") +
            "$s.Save()")


def create(description: str = "Obliphur's Integrated Terminal", folder: str = None):
    """在 folder (默认桌面) 建快捷方式. 返回 (成功?, 快捷方式路径或出错原因)."""
    if sys.platform != "win32":
        return False, "Windows only"
    folder = folder or desktop_dir()
    os.makedirs(folder, exist_ok=True)
    lnk = os.path.join(folder, SHORTCUT_NAME)
    target, args, workdir = launch_target()
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            build_script(lnk, target, args, workdir, description, icon_file())],
                           capture_output=True, text=True, timeout=30, creationflags=0x08000000)
    except Exception as e:      # noqa: BLE001
        return False, str(e) or type(e).__name__
    if os.path.exists(lnk):
        return True, lnk
    return False, (r.stderr or r.stdout or "unknown error").strip()[:300]


def _selftest():
    s = build_script(r"C:\Users\a\Desktop\OIT.lnk", r"C:\Py\pythonw.exe", '"D:\\Obliphur\'s\\main.py"',
                     r"D:\Obliphur's", "Obliphur's Integrated Terminal")
    assert "'D:\\Obliphur''s'" in s and "Obliphur''s Integrated Terminal" in s
    assert s.count("$s.") == 5 and s.endswith("$s.Save()")
    si = build_script(r"C:.lnk", "t", "", "w", "d", r"C:\Obliphur's\oit.ico")
    assert si.count("$s.") == 6 and r"$s.IconLocation = 'C:\Obliphur''s\oit.ico'; $s.Save()" in si
    target, args, workdir = launch_target()
    assert args.endswith('main.py"') and os.path.isdir(workdir)
    v = verb_script(r"C:\Users\a\Start Menu\OIT.lnk", PIN_TASKBAR)
    assert "Namespace('C:\\Users\\a\\Start Menu')" in v and "ParseName('OIT.lnk')" in v
    assert "-like '*固定到任务栏*'" in v and "Unpin*" in v
    assert start_menu_dir().endswith(os.path.join("Start Menu", "Programs"))
    return "shortcut selftest OK"


if __name__ == "__main__":
    print(_selftest())
