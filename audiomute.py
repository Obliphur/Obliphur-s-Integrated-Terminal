"""按进程静音 / 取消静音 (提前启动: 藏起来的程序不许出声, MECHANICS 20).

Windows 的"音量合成器"里每个在放声音的程序是一条音频会话; 这里直接用 ctypes 调 WASAPI 的
会话接口 (IAudioSessionManager2 → IAudioSessionControl2 → ISimpleAudioVolume), 不依赖 pycaw 之类的包.

只影响这些进程自己的会话, 不动系统音量, 也不动别的程序.

注意: 没在放声音的程序没有会话, 这里静音不了 —— 它本来也不出声; 一秒后它一出声,
新出现的会话就会被下一次调用静音 (提前启动那边每秒调一次).

    python audiomute.py           自检 (枚举一遍会话, 有的话静音再取消)
"""

import sys


COINIT_APARTMENTTHREADED = 0x2
CLSCTX_ALL = 0x17

CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
IID_IAudioSessionManager2 = "{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}"
IID_IAudioSessionControl2 = "{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}"
IID_ISimpleAudioVolume = "{87CE5498-68D6-44E5-9215-6DA47EF883D8}"

E_RENDER, E_CONSOLE = 0, 0          # 播放设备, 默认输出


def _guid(text: str):
    """'{XXXXXXXX-XXXX-...}' → GUID 结构 (每次现造, 免得依赖 ctypes 的 GUID 定义)."""
    import ctypes
    from ctypes import wintypes
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    u = uuid.UUID(text.strip("{}"))
    g = GUID()
    g.Data1 = u.time_low
    g.Data2 = u.time_mid
    g.Data3 = u.time_hi_version
    for i, b in enumerate(u.bytes[8:]):
        g.Data4[i] = b
    return g


class _Ole:
    """一次 CoInitialize / CoUninitialize 的范围."""

    def __init__(self):
        self.ole32 = None
        self.inited = False
        self.created = []           # 拿到的接口, 退出时逐个 Release

    def __enter__(self):
        import ctypes
        self.ctypes = ctypes
        if sys.platform != "win32":
            return self
        self.ole32 = ctypes.WinDLL("ole32")
        self.ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.ole32.CoInitializeEx.restype = ctypes.c_long
        self.ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
                                                ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        self.ole32.CoCreateInstance.restype = ctypes.c_long
        hr = self.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        self.inited = hr == 0            # S_FALSE: 这个线程已经初始化过 (不归我们关)
        return self

    def __exit__(self, *exc):
        for ptr in reversed(self.created):
            try:
                self._call(ptr, 2, ctypes.c_ulong, ctypes.c_void_p)(ptr)
            except Exception:
                pass
        self.created.clear()
        if self.ole32 is not None and self.inited:
            try:
                self.ole32.CoUninitialize()
            except Exception:
                pass
        return False

    def _vtable(self, ptr, index: int):
        ctypes = self.ctypes
        vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        return vtbl[index]

    def _call(self, ptr, index: int, restype, *argtypes):
        proto = self.ctypes.WINFUNCTYPE(restype, *argtypes)
        return proto(self._vtable(ptr, index))

    # ---- 具体接口 (vtable 下标见注释) ----

    def co_create(self, clsid: str, iid: str):
        ctypes = self.ctypes
        ptr = ctypes.c_void_p()
        hr = self.ole32.CoCreateInstance(ctypes.byref(_guid(clsid)), None, CLSCTX_ALL,
                                         ctypes.byref(_guid(iid)), ctypes.byref(ptr))
        if hr < 0 or not ptr:
            raise OSError(f"CoCreateInstance {clsid} 0x{hr & 0xFFFFFFFF:08X}")
        self.created.append(ptr)
        return ptr

    def query(self, ptr, iid: str):
        ctypes = self.ctypes
        out = ctypes.c_void_p()
        fn = self._call(ptr, 0, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.POINTER(ctypes.c_void_p))
        hr = fn(ptr, ctypes.byref(_guid(iid)), ctypes.byref(out))
        if hr < 0 or not out:
            return None
        self.created.append(out)
        return out

    def get_default_endpoint(self, enumerator):
        ctypes = self.ctypes
        dev = ctypes.c_void_p()
        fn = self._call(enumerator, 4, ctypes.c_long, ctypes.c_void_p, ctypes.c_int,
                        ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
        hr = fn(enumerator, E_RENDER, E_CONSOLE, ctypes.byref(dev))
        if hr < 0 or not dev:
            raise OSError(f"GetDefaultAudioEndpoint 0x{hr & 0xFFFFFFFF:08X}")
        self.created.append(dev)
        return dev

    def activate(self, device, iid: str):
        ctypes = self.ctypes
        out = ctypes.c_void_p()
        fn = self._call(device, 3, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.c_ulong, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        hr = fn(device, ctypes.byref(_guid(iid)), CLSCTX_ALL, None, ctypes.byref(out))
        if hr < 0 or not out:
            raise OSError(f"IAudioSessionManager2 activate 0x{hr & 0xFFFFFFFF:08X}")
        self.created.append(out)
        return out

    def sessions(self):
        """[(进程 id, 会话控制指针), …] (指针随 _Ole 退出一起释放)."""
        ctypes = self.ctypes
        enumerator = self.co_create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
        device = self.get_default_endpoint(enumerator)
        manager = self.activate(device, IID_IAudioSessionManager2)
        enum2 = ctypes.c_void_p()
        fn = self._call(manager, 5, ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        hr = fn(manager, ctypes.byref(enum2))
        if hr < 0 or not enum2:
            raise OSError(f"GetSessionEnumerator 0x{hr & 0xFFFFFFFF:08X}")
        self.created.append(enum2)
        count = ctypes.c_int(0)
        fn = self._call(enum2, 3, ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))
        if fn(enum2, ctypes.byref(count)) < 0:
            return []
        out = []
        for i in range(max(0, count.value)):
            ctrl = ctypes.c_void_p()
            fn = self._call(enum2, 4, ctypes.c_long, ctypes.c_void_p, ctypes.c_int,
                            ctypes.POINTER(ctypes.c_void_p))
            if fn(enum2, i, ctypes.byref(ctrl)) < 0 or not ctrl:
                continue
            self.created.append(ctrl)
            ctrl2 = self.query(ctrl, IID_IAudioSessionControl2)
            if ctrl2 is None:
                continue
            pid = ctypes.c_ulong(0)
            fn = self._call(ctrl2, 14, ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
            if fn(ctrl2, ctypes.byref(pid)) < 0:
                continue
            out.append((int(pid.value), ctrl))
        return out


def set_muted(pids, muted: bool) -> int:
    """把这些进程的音频会话静音 (muted=True) / 取消静音. 返回改了几个会话 (非 Windows 返回 0)."""
    pids = {int(p) for p in (pids or ()) if int(p) > 0}
    if not pids or sys.platform != "win32":
        return 0
    n = 0
    try:
        with _Ole() as ole:
            ctypes = ole.ctypes
            for pid, ctrl in ole.sessions():
                if pid not in pids:
                    continue
                vol = ole.query(ctrl, IID_ISimpleAudioVolume)
                if vol is None:
                    continue
                fn = ole._call(vol, 5, ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p)
                if fn(vol, 1 if muted else 0, None) >= 0:
                    n += 1
    except Exception:
        return n
    return n


def muted_state(pid: int):
    """这个进程的会话现在是静音的吗 (没有会话时 None)."""
    if sys.platform != "win32":
        return None
    try:
        with _Ole() as ole:
            ctypes = ole.ctypes
            for p, ctrl in ole.sessions():
                if p != int(pid):
                    continue
                vol = ole.query(ctrl, IID_ISimpleAudioVolume)
                if vol is None:
                    continue
                state = ctypes.c_int(0)
                fn = ole._call(vol, 6, ctypes.c_long, ctypes.c_void_p,
                               ctypes.POINTER(ctypes.c_int))
                if fn(vol, ctypes.byref(state)) >= 0:
                    return bool(state.value)
    except Exception:
        return None
    return None


def _selftest():
    if sys.platform != "win32":
        return "audiomute selftest OK (非 Windows, 跳过)"
    with _Ole() as ole:
        items = ole.sessions()
    pids = sorted({p for p, _c in items})
    print(f"音频会话 {len(items)} 条, 进程 {pids}")
    # 静音 → 查 → 恢复成原来的样子 (不留下副作用): 自己的进程有会话就用自己, 否则借第一个
    import os
    me = os.getpid()
    pid = me if me in pids else (pids[0] if pids else 0)
    if pid:
        was = muted_state(pid)
        n1 = set_muted([pid], True)
        now = muted_state(pid)
        n2 = set_muted([pid], bool(was))
        print(f"进程 {pid}: 原本静音={was}, 静音后={now} (改了 {n1}/{n2} 个), 已恢复成 {bool(was)}")
        assert n1 >= 1 and n2 >= 1 and now is True
        assert muted_state(pid) == bool(was), "测完要恢复原来的静音状态"
    assert set_muted([], True) == 0 and set_muted([-1], True) == 0
    return "audiomute selftest OK"


if __name__ == "__main__":
    print(_selftest())
