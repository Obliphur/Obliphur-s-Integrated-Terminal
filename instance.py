"""
OIT 单实例 (MECHANICS.md 3.5)
──────────────────────────────────────────────────────
电脑上同时只能运行一个 OIT: 启动时拿一个命名互斥体 (`Local\\OIT-main-instance`), 已经有别的 OIT 拿着就退出.
  · 更新 / 重置后的重新打开: 旧进程马上退出, 新进程最多等 WAIT 秒让它放手;
  · 联动程序 (linkd.py, 15.8) 用同一个互斥体看 OIT 在不在 —— 刚打开还没开始监听同步端口的也看得见;
  · **git 仓库里的开发版本不受限** (updater.is_dev_checkout): 照常拿互斥体 (让联动程序看得见), 但已经有别的 OIT 在运行时也不退出.

    python instance.py --selftest
"""

import subprocess
import sys
import time

NAME = "Local\\OIT-main-instance"
ERROR_ALREADY_EXISTS = 183
SYNCHRONIZE = 0x00100000
WAIT = 15.0
_handle = None


def _k32():
    import ctypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = ctypes.c_void_p
    k.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    k.OpenMutexW.restype = ctypes.c_void_p
    k.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    return k


def exists(name: str = NAME) -> bool:
    """有没有 OIT 实例拿着互斥体 (拿着的进程退出后互斥体就没了)."""
    if sys.platform != "win32":
        return False
    k = _k32()
    h = k.OpenMutexW(SYNCHRONIZE, 0, name)
    if h:
        k.CloseHandle(h)
        return True
    import ctypes
    return ctypes.get_last_error() == 5          # 5 = 拒绝访问: 对象在, 只是权限不够 (别的权限级别的进程创建的)


def acquire(wait: float = WAIT, enforce: bool = True, name: str = NAME) -> bool:
    """本进程拿互斥体. 返回 True = 可以继续运行; False = 已经有别的 OIT 在运行 (调用方该退出).
    enforce=False (开发版本): 拿不到也返回 True."""
    global _handle
    if sys.platform != "win32":
        return True
    import ctypes
    k = _k32()
    end = time.time() + max(0.0, wait)
    while True:
        h = k.CreateMutexW(None, 0, name)
        already = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        if h and not already:
            _handle = h
            return True
        if h:
            k.CloseHandle(h)               # 不放手的话互斥体在旧进程退出之后还会被我们这个句柄留着
        if not enforce:
            _handle = k.CreateMutexW(None, 0, name)
            return True
        if time.time() >= end:
            return False
        time.sleep(0.3)


def _selftest():
    if sys.platform != "win32":
        return "instance selftest OK (non-Windows)"
    name = f"Local\\OIT-selftest-{int(time.time() * 1000)}"
    assert not exists(name)
    assert acquire(0, True, name) and exists(name)
    code = ("import sys; sys.path.insert(0, %r); import instance; "
            "print(instance.acquire(0, %%s, %r))" % (__file__.rsplit("\\", 1)[0], name))
    out = subprocess.run([sys.executable, "-c", code % "True"], capture_output=True, text=True).stdout.strip()
    assert out == "False", "第二个实例应该被挡住: " + out
    out = subprocess.run([sys.executable, "-c", code % "False"], capture_output=True, text=True).stdout.strip()
    assert out == "True", "开发版本不受限: " + out
    return "instance selftest OK"


if __name__ == "__main__":
    print(_selftest())
