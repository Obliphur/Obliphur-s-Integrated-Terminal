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


def refresh(program_dir: str, version: str, root=None, path: str = UNINSTALL_PATH) -> bool:
    """程序启动时: 是 setup.exe 装的, 注册表里的版本号和现在的不一样 (程序自己更新过) 就改过来;
    注册信息丢了 (被清理工具删了) 就补上. 返回有没有改动."""
    m = read_marker(program_dir)
    if not m or sys.platform != "win32":
        return False
    cur = registered_version(root, path)
    if cur == version and os.path.exists(m.get("python") or ""):
        return False
    python = m.get("python") or sys.executable
    ok = register(program_dir, version, python, int(m.get("size_kb") or 0), root, path)
    if ok and m.get("version") != version:
        m["version"] = version
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
