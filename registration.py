"""
把 OIT 注册为系统里的"软件" (MECHANICS.md 16.4)
──────────────────────────────────────────────────────
setup.exe (setup.py) 装好 OIT 之后在注册表里写一项卸载信息, 设置 → 应用 → 已安装的应用 (和"程序和功能") 里就有 OIT:
    HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\<KEY>
        DisplayName / DisplayVersion / Publisher / InstallLocation / DisplayIcon / EstimatedSize / URLInfoAbout
        UninstallString   → 卸载程序 uninstall.py (用装 OIT 时记下的 Python)
程序文件夹里的 install.json 记着这次安装: 版本, 用的 Python, 建了哪些快捷方式 (卸载时按它清理).
程序自己更新 (updater.py 覆盖文件) 后版本号会变: OIT 每次启动时 refresh() 把 DisplayVersion 改成现在的.
没有用 setup.exe 装的 (git 仓库 / 解压的压缩包): 没有 install.json, 这里的函数什么也不做.

    python registration.py       自检 (只写一个临时的 HKCU 项, 用完删掉)
"""

import json
import os
import re
import subprocess
import sys
import time

KEY = "ObliphurIntegratedTerminal"
UNINSTALL_PATH = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
MARKER = "install.json"
PUBLISHER = "Obliphur"
HOMEPAGE = "https://github.com/Obliphur/Obliphur-s-Integrated-Terminal"


def marker_path(program_dir: str) -> str:
    return os.path.join(program_dir, MARKER)


def read_marker(program_dir: str) -> dict:
    """这个程序文件夹是 setup.exe 装的吗: 是就返回 install.json 的内容, 不是 {}."""
    try:
        with open(marker_path(program_dir), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_marker(program_dir: str, data: dict):
    with open(marker_path(program_dir), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def launchable_python(path: str) -> str:
    r"""Microsoft Store 版 Python 的真实路径 (C:\Program Files\WindowsApps\<包>\python.exe) 不允许别的程序直接运行 (WinError 5);
    能运行的是每个用户的别名 %LOCALAPPDATA%\Microsoft\WindowsApps\<包名>_<发布者>\python.exe. 是前者且别名在就换成别名, 其他路径原样."""
    parts = (path or "").split("\\")
    i = next((k for k, x in enumerate(parts) if x.lower() == "windowsapps"), -1)
    if i < 1 or parts[i - 1].lower() != "program files" or i + 1 >= len(parts) or "__" not in parts[i + 1]:
        return path
    pkg, pub = parts[i + 1].split("_", 1)[0], parts[i + 1].rsplit("__", 1)[-1]
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps", f"{pkg}_{pub}")
    for name in (os.path.basename(path), "python.exe"):
        alias = os.path.join(base, name)
        if os.path.exists(alias):
            return alias
    return path


def pythonw_of(python: str) -> str:
    """python.exe → 同目录的 pythonw.exe (没有就原样)."""
    pyw = os.path.join(os.path.dirname(python), "pythonw.exe")
    return pyw if os.path.exists(pyw) else python


def uninstall_command(program_dir: str, python: str) -> str:
    return f'"{pythonw_of(python)}" "{os.path.join(program_dir, "uninstall.py")}"'


def values(program_dir: str, version: str, python: str, size_kb: int = 0) -> dict:
    """卸载信息里的各项 (名字 → (类型, 值))."""
    import winreg
    sz, dw = winreg.REG_SZ, winreg.REG_DWORD
    v = {
        "DisplayName": (sz, "Obliphur's Integrated Terminal"),
        "DisplayVersion": (sz, version),
        "Publisher": (sz, PUBLISHER),
        "InstallLocation": (sz, program_dir),
        "DisplayIcon": (sz, pythonw_of(python)),
        "UninstallString": (sz, uninstall_command(program_dir, python)),
        "URLInfoAbout": (sz, HOMEPAGE),
        "InstallDate": (sz, time.strftime("%Y%m%d")),
        "NoModify": (dw, 1),
        "NoRepair": (dw, 1),
    }
    if size_kb:
        v["EstimatedSize"] = (dw, int(size_kb))
    return v


def register(program_dir: str, version: str, python: str, size_kb: int = 0, root=None, path: str = UNINSTALL_PATH) -> bool:
    """写卸载信息. root 默认 HKLM (需要管理员); 自检里用 HKCU 下的临时项."""
    if sys.platform != "win32":
        return False
    import winreg
    root = winreg.HKEY_LOCAL_MACHINE if root is None else root
    try:
        with winreg.CreateKeyEx(root, path + "\\" + KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
            for name, (typ, val) in values(program_dir, version, python, size_kb).items():
                winreg.SetValueEx(k, name, 0, typ, val)
        return True
    except OSError:
        return False


def registered_version(root=None, path: str = UNINSTALL_PATH) -> str:
    """注册表里现在的 DisplayVersion (没有注册时 "")."""
    if sys.platform != "win32":
        return ""
    import winreg
    root = winreg.HKEY_LOCAL_MACHINE if root is None else root
    try:
        with winreg.OpenKey(root, path + "\\" + KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            return str(winreg.QueryValueEx(k, "DisplayVersion")[0])
    except OSError:
        return ""


def unregister(root=None, path: str = UNINSTALL_PATH) -> bool:
    """删卸载信息 (本来就没有也算成功)."""
    if sys.platform != "win32":
        return True
    import winreg
    root = winreg.HKEY_LOCAL_MACHINE if root is None else root
    try:
        winreg.DeleteKeyEx(root, path + "\\" + KEY, winreg.KEY_WOW64_64KEY, 0)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def registered_value(name: str, root=None, path: str = UNINSTALL_PATH) -> str:
    if sys.platform != "win32":
        return ""
    import winreg
    root = winreg.HKEY_LOCAL_MACHINE if root is None else root
    try:
        with winreg.OpenKey(root, path + "\\" + KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            return str(winreg.QueryValueEx(k, name)[0])
    except OSError:
        return ""


def fix_shortcuts(program_dir: str, python: str, links) -> None:
    """快捷方式改成指向能运行的 pythonw.exe."""
    import shortcut
    for lnk in links or []:
        if os.path.exists(lnk):
            script = shortcut.build_script(lnk, pythonw_of(python), f'"{os.path.join(program_dir, "main.py")}"', program_dir,
                                           "Obliphur's Integrated Terminal")
            try:
                subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=60, creationflags=0x08000000)
            except Exception:
                pass


def refresh(program_dir: str, version: str, root=None, path: str = UNINSTALL_PATH) -> bool:
    """程序启动时: 是 setup.exe 装的, 注册表里的版本号 / 卸载命令和现在的不一样 (程序自己更新过, 或者记的 Python 路径不能直接运行) 就改过来;
    注册信息丢了 (被清理工具删了) 就补上. 返回有没有改动."""
    m = read_marker(program_dir)
    if not m or sys.platform != "win32":
        return False
    python = launchable_python(m.get("python") or sys.executable)
    if registered_version(root, path) == version and registered_value("UninstallString", root, path) == uninstall_command(program_dir, python)             and os.path.exists(python):
        return False
    ok = register(program_dir, version, python, int(m.get("size_kb") or 0), root, path)
    if ok:
        if python != m.get("python"):
            fix_shortcuts(program_dir, python, m.get("shortcuts"))
        if m.get("version") != version or python != m.get("python"):
            m["version"], m["python"] = version, python
            try:
                write_marker(program_dir, m)
            except OSError:
                pass
    return ok


def _selftest():
    import tempfile
    import shutil
    if sys.platform != "win32":
        return "registration selftest OK (non-Windows)"
    import winreg
    d = tempfile.mkdtemp()
    test_path = r"Software\OIT-registration-selftest"
    try:
        assert read_marker(d) == {} and not refresh(d, "1.0.0", winreg.HKEY_CURRENT_USER, test_path)
        py = os.path.join(d, "python.exe")
        open(py, "w").close()
        open(os.path.join(d, "pythonw.exe"), "w").close()
        assert uninstall_command(d, py).startswith('"' + os.path.join(d, "pythonw.exe") + '" "' + os.path.join(d, "uninstall.py") + '"')
        assert register(d, "1.2.3", py, 1234, winreg.HKEY_CURRENT_USER, test_path)
        assert registered_version(winreg.HKEY_CURRENT_USER, test_path) == "1.2.3"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, test_path + "\\" + KEY) as k:
            assert winreg.QueryValueEx(k, "InstallLocation")[0] == d
            assert winreg.QueryValueEx(k, "EstimatedSize")[0] == 1234
            assert "uninstall.py" in winreg.QueryValueEx(k, "UninstallString")[0]
        # 程序自己更新后: 有 install.json 才改版本号
        assert not refresh(d, "1.2.4", winreg.HKEY_CURRENT_USER, test_path)
        write_marker(d, {"version": "1.2.3", "python": py, "size_kb": 1234})
        assert refresh(d, "1.2.4", winreg.HKEY_CURRENT_USER, test_path)
        assert registered_version(winreg.HKEY_CURRENT_USER, test_path) == "1.2.4" and read_marker(d)["version"] == "1.2.4"
        assert not refresh(d, "1.2.4", winreg.HKEY_CURRENT_USER, test_path), "没变: 不用写"
        # Microsoft Store 版 Python: 真实路径 → 别名 (别名存在时)
        pkg = r"C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\python3.13.exe"
        assert launchable_python(r"C:\Python313\python.exe") == r"C:\Python313\python.exe"
        old_la = os.environ.get("LOCALAPPDATA")
        al = os.path.join(d, "Microsoft", "WindowsApps", "PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0")
        os.makedirs(al)
        open(os.path.join(al, "python.exe"), "w").close()
        os.environ["LOCALAPPDATA"] = d
        try:
            assert launchable_python(pkg) == os.path.join(al, "python.exe")
            os.remove(os.path.join(al, "python.exe"))
            assert launchable_python(pkg) == pkg, "别名不在: 原样"
        finally:
            os.environ["LOCALAPPDATA"] = old_la or ""
        assert unregister(winreg.HKEY_CURRENT_USER, test_path) and registered_version(winreg.HKEY_CURRENT_USER, test_path) == ""
        assert unregister(winreg.HKEY_CURRENT_USER, test_path)
    finally:
        shutil.rmtree(d, ignore_errors=True)
        try:
            winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, test_path, 0, 0)
        except OSError:
            pass
    return "registration selftest OK"


if __name__ == "__main__":
    print(_selftest())
