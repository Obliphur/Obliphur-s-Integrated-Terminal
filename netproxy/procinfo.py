"""
连接 → 进程名(只在 Windows 上有效,其它平台返回 '')
─────────────────────────────────────────────────
代理收到一条本机连接时只知道对端端口.这里用 iphlpapi.GetExtendedTcpTable 查出
「本地端口 = 客户端端口,远端端口 = 代理端口」的那一行,拿到 PID,再换成 exe 文件名.
应用黑白名单靠这个才能成立.
"""

import ctypes
import os
import socket
import sys
import threading
import time

AF_INET = 2
TCP_TABLE_OWNER_PID_ALL = 5
ERROR_INSUFFICIENT_BUFFER = 122
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_pid_cache = {}
_pid_lock = threading.Lock()
_PID_TTL = 30.0


class _Row(ctypes.Structure):
    _fields_ = [
        ("state", ctypes.c_ulong),
        ("local_addr", ctypes.c_ulong),
        ("local_port", ctypes.c_ulong),
        ("remote_addr", ctypes.c_ulong),
        ("remote_port", ctypes.c_ulong),
        ("pid", ctypes.c_ulong),
    ]


def _port(raw: int) -> int:
    return socket.ntohs(raw & 0xFFFF)


def _pid_for_client(client_port: int, proxy_port: int) -> int:
    iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
    size = ctypes.c_ulong(0)
    buf = None
    for _ in range(4):
        rc = iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False,
                                          AF_INET, TCP_TABLE_OWNER_PID_ALL, 0)
        if rc == 0:
            break
        if rc != ERROR_INSUFFICIENT_BUFFER:
            return 0
        buf = ctypes.create_string_buffer(size.value + 4096)
        size = ctypes.c_ulong(size.value + 4096)
    else:
        return 0
    count = ctypes.c_ulong.from_buffer(buf).value
    base = ctypes.addressof(buf) + ctypes.sizeof(ctypes.c_ulong)
    rows = (_Row * count).from_address(base)
    for r in rows:
        if _port(r.local_port) == client_port and _port(r.remote_port) == proxy_port:
            return int(r.pid)
    return 0


def _exe_for_pid(pid: int) -> str:
    now = time.monotonic()
    with _pid_lock:
        hit = _pid_cache.get(pid)
        if hit and now - hit[1] < _PID_TTL:
            return hit[0]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    name = ""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if h:
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = ctypes.c_ulong(len(buf))
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                name = os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(h)
    with _pid_lock:
        if len(_pid_cache) > 512:
            _pid_cache.clear()
        _pid_cache[pid] = (name, now)
    return name


def client_info(client_port: int, proxy_port: int):
    """发起这条连接的 (pid, exe 文件名小写); 查不到返回 (0, '')."""
    if sys.platform != "win32":
        return 0, ""
    try:
        pid = _pid_for_client(int(client_port), int(proxy_port))
        return (pid, _exe_for_pid(pid)) if pid else (0, "")
    except Exception:
        return 0, ""


def exe_for_client(client_port: int, proxy_port: int) -> str:
    """发起这条连接的程序的 exe 文件名(小写);查不到返回 ''."""
    return client_info(client_port, proxy_port)[1]
