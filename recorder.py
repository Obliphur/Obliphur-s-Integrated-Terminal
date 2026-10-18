"""
合约的录像 (MECHANICS.md 18.3 目光 / 刻痕): 摄像头和整个屏幕各录成 mp4 (H.264), 只用标准库.

编码用 Windows 自带的 Media Foundation (mfplat / mfreadwrite, 经 ctypes 调用 COM 接口):
- 屏幕: GDI 截取整个虚拟屏幕 (所有显示器, 带鼠标指针), 缩到最宽 SCREEN_WIDTH, 每秒 SCREEN_FPS 帧;
- 摄像头: 第一个视频采集设备, 挑最接近 640×480 的格式, 每秒最多 CAMERA_FPS 帧.
录像在后台线程里写. 每 SEGMENT 秒换一个文件 (程序被强行关掉时最多丢这一段);
正在写的文件名是 "…" + PART + ".mp4", 写完 (Finalize) 后改成正式的名字.

存放: 数据目录/recordings/camera 和 数据目录/recordings/screen, 分开存放.
"""

import ctypes
import os
import threading
import time
import uuid
from ctypes import POINTER, byref, c_int, c_long, c_uint, c_uint64, c_ulong, c_void_p, wintypes
from datetime import datetime

KINDS = ("camera", "screen")
PART = ".recording"
SEGMENT = 30 * 60                 # 每个文件最长 30 分钟
SCREEN_FPS = 1
SCREEN_WIDTH = 1280
SCREEN_BITRATE = 320_000
CAMERA_FPS = 5
CAMERA_BITRATE = 240_000
CAMERA_SIZE = (640, 480)
CAMERA_STALL = 12                 # 摄像头这么多秒没有新画面: 视为被关掉了


def folder(data_dir: str, kind: str) -> str:
    return os.path.join(data_dir, "recordings", kind)


def list_files(data_dir: str, kind: str) -> list:
    """录像文件, 新的在前: [{path, name, size, start, partial}]. partial: 没有正常写完 (多半放不出来)."""
    d = folder(data_dir, kind)
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for n in names:
        if not n.lower().endswith(".mp4"):
            continue
        p = os.path.join(d, n)
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        stem = n[:-4]
        partial = stem.endswith(PART)
        if partial:
            stem = stem[:-len(PART)]
        try:
            start = datetime.strptime(stem[:15], "%Y%m%d-%H%M%S")
        except ValueError:
            start = None
        out.append({"path": p, "name": n, "size": size, "start": start, "partial": partial})
    out.sort(key=lambda f: (f["start"] or datetime.min, f["name"]), reverse=True)
    return out


def total_size(data_dir: str, kind: str) -> int:
    return sum(f["size"] for f in list_files(data_dir, kind))


# ---------------------------------------------------------------- COM / Media Foundation

HRESULT = c_long


def _guid(s: str):
    return GUID(s)


class GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]

    def __init__(self, s: str = None):
        super().__init__()
        if s:
            ctypes.memmove(self.data, uuid.UUID(s).bytes_le, 16)

    def __eq__(self, other):
        return isinstance(other, GUID) and bytes(self.data) == bytes(other.data)

    def __hash__(self):
        return hash(bytes(self.data))


MFMediaType_Video = _guid("73646976-0000-0010-8000-00AA00389B71")
MFVideoFormat_H264 = _guid("34363248-0000-0010-8000-00AA00389B71")
MFVideoFormat_RGB32 = _guid("00000016-0000-0010-8000-00AA00389B71")
MF_MT_MAJOR_TYPE = _guid("48eba18e-f8c9-4687-bf11-0a74c9f96a8f")
MF_MT_SUBTYPE = _guid("f7e34c9a-42e8-4714-b74b-cb29d72c35e5")
MF_MT_AVG_BITRATE = _guid("20332624-fb0d-4d9e-bd0d-cbf6786c102e")
MF_MT_INTERLACE_MODE = _guid("e2724bb8-e676-4806-b4b2-a8d6efb44ccd")
MF_MT_FRAME_SIZE = _guid("1652c33d-d6b2-4012-b834-72030849a37d")
MF_MT_FRAME_RATE = _guid("c459a2e8-3d2c-4e44-b132-fee5156c7bb0")
MF_MT_PIXEL_ASPECT_RATIO = _guid("c6376a1e-8d0a-4027-be45-6d9a0ad39bb6")
MF_MT_DEFAULT_STRIDE = _guid("644b4e48-1e02-4516-b0eb-c01ca9d49ac6")
MF_SINK_WRITER_DISABLE_THROTTLING = _guid("08b845d8-2b74-4afe-9d53-be16d2d5ae4f")
MF_SOURCE_READER_ENABLE_VIDEO_PROCESSING = _guid("fb394f3d-ccf1-42ee-bbb3-f9b845d5681d")
MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE = _guid("c60ac5fe-252a-478f-a0ef-bc8fa5f7cad3")
MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID = _guid("8ac3587a-4ae7-42d8-99e0-0a6013eef90f")
MF_DEVSOURCE_ATTRIBUTE_FRIENDLY_NAME = _guid("60d0e559-52f8-4fa2-bbce-acdb34a8ec01")
IID_IMFMediaSource = _guid("279a808d-aec7-40c8-9c6b-a6b492c78a66")

MF_VERSION = 0x00020070
MF_SOURCE_READER_FIRST_VIDEO_STREAM = 0xFFFFFFFC
MF_SOURCE_READER_ALL_STREAMS = 0xFFFFFFFE
MF_SOURCE_READERF_ERROR = 0x1
MF_SOURCE_READERF_ENDOFSTREAM = 0x2
MF_E_NO_MORE_TYPES = -1072875847          # 0xC00D36B9
MFVideoInterlace_Progressive = 2


class MFError(OSError):
    pass


def _check(hr, what=""):
    if hr < 0:
        raise MFError(f"{what} 0x{hr & 0xFFFFFFFF:08X}")
    return hr


_PROTO_CACHE = {}


def _com(obj, idx, *args, argtypes=(), what=""):
    """调用 COM 对象的第 idx 个方法 (vtable), 失败时抛 MFError."""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
    key = tuple(argtypes)
    proto = _PROTO_CACHE.get(key)
    if proto is None:
        proto = _PROTO_CACHE[key] = ctypes.WINFUNCTYPE(HRESULT, c_void_p, *argtypes)
    return _check(proto(vtbl[idx])(obj, *args), what or f"vtbl[{idx}]")


def _release(obj):
    if obj:
        try:
            vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
            ctypes.WINFUNCTYPE(c_ulong, c_void_p)(vtbl[2])(obj)
        except Exception:
            pass


# IMFAttributes
_SET_UINT32, _SET_UINT64, _SET_GUID, _GET_UINT32, _GET_UINT64, _GET_GUID, _COPY_ALL = 21, 22, 24, 7, 8, 10, 32


def _set_u32(obj, key, v):
    _com(obj, _SET_UINT32, byref(key), c_uint(v), argtypes=(POINTER(GUID), c_uint), what="SetUINT32")


def _set_u64(obj, key, v):
    _com(obj, _SET_UINT64, byref(key), c_uint64(v), argtypes=(POINTER(GUID), c_uint64), what="SetUINT64")


def _set_guid(obj, key, g):
    _com(obj, _SET_GUID, byref(key), byref(g), argtypes=(POINTER(GUID), POINTER(GUID)), what="SetGUID")


def _get_u64(obj, key):
    v = c_uint64()
    _com(obj, _GET_UINT64, byref(key), byref(v), argtypes=(POINTER(GUID), POINTER(c_uint64)), what="GetUINT64")
    return v.value


def _get_guid(obj, key):
    g = GUID()
    _com(obj, _GET_GUID, byref(key), byref(g), argtypes=(POINTER(GUID), POINTER(GUID)), what="GetGUID")
    return g


class _MF:
    """Media Foundation 的几个函数 (第一次用时加载)."""
    _lib = None

    @classmethod
    def get(cls):
        if cls._lib is None:
            ns = type("MFLib", (), {})()
            ns.ole = ctypes.windll.ole32
            ns.plat = ctypes.windll.mfplat
            ns.rw = ctypes.windll.mfreadwrite
            ns.mf = ctypes.windll.mf
            cls._lib = ns
        return cls._lib


def _startup():
    lib = _MF.get()
    lib.ole.CoInitializeEx(None, 0)                       # 多线程套间
    _check(lib.plat.MFStartup(MF_VERSION, 0), "MFStartup")
    return lib


def _shutdown(lib):
    try:
        lib.plat.MFShutdown()
        lib.ole.CoUninitialize()
    except Exception:
        pass


def _media_type(lib):
    t = c_void_p()
    _check(lib.plat.MFCreateMediaType(byref(t)), "MFCreateMediaType")
    return t


def _attributes(lib, n=2):
    a = c_void_p()
    _check(lib.plat.MFCreateAttributes(byref(a), c_uint(n)), "MFCreateAttributes")
    return a


class _Writer:
    """H.264 / mp4 的 Sink Writer: 输入是 RGB32 (input_type) 或者按 width/height 建的上下正向 RGB32."""

    def __init__(self, lib, path, width, height, fps, bitrate, input_type=None):
        self.lib = lib
        self.writer = c_void_p()
        self.stream = c_ulong()
        attrs = _attributes(lib)
        try:
            _set_u32(attrs, MF_SINK_WRITER_DISABLE_THROTTLING, 1)
            _check(lib.rw.MFCreateSinkWriterFromURL(ctypes.c_wchar_p(path), None, attrs, byref(self.writer)),
                   "MFCreateSinkWriterFromURL")
        finally:
            _release(attrs)
        out = _media_type(lib)
        try:
            _set_guid(out, MF_MT_MAJOR_TYPE, MFMediaType_Video)
            _set_guid(out, MF_MT_SUBTYPE, MFVideoFormat_H264)
            _set_u32(out, MF_MT_AVG_BITRATE, bitrate)
            _set_u32(out, MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive)
            _set_u64(out, MF_MT_FRAME_SIZE, (width << 32) | height)
            _set_u64(out, MF_MT_FRAME_RATE, (fps << 32) | 1)
            _set_u64(out, MF_MT_PIXEL_ASPECT_RATIO, (1 << 32) | 1)
            _com(self.writer, 3, out, byref(self.stream), argtypes=(c_void_p, POINTER(c_ulong)), what="AddStream")
        finally:
            _release(out)
        inp = _media_type(lib)
        try:
            if input_type:
                _com(input_type, _COPY_ALL, inp, argtypes=(c_void_p,), what="CopyAllItems")
            else:
                _set_guid(inp, MF_MT_MAJOR_TYPE, MFMediaType_Video)
                _set_guid(inp, MF_MT_SUBTYPE, MFVideoFormat_RGB32)
                _set_u32(inp, MF_MT_DEFAULT_STRIDE, width * 4)        # 正数: 从上往下
                _set_u64(inp, MF_MT_FRAME_SIZE, (width << 32) | height)
            _set_u32(inp, MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive)
            _set_u64(inp, MF_MT_FRAME_RATE, (fps << 32) | 1)
            _set_u64(inp, MF_MT_PIXEL_ASPECT_RATIO, (1 << 32) | 1)
            _com(self.writer, 4, self.stream, inp, None, argtypes=(c_ulong, c_void_p, c_void_p),
                 what="SetInputMediaType")
        finally:
            _release(inp)
        _com(self.writer, 5, what="BeginWriting")

    def write_bytes(self, data_ptr, size, t100ns, dur100ns):
        buf = c_void_p()
        _check(self.lib.plat.MFCreateMemoryBuffer(c_ulong(size), byref(buf)), "MFCreateMemoryBuffer")
        sample = c_void_p()
        try:
            p = c_void_p()
            _com(buf, 3, byref(p), None, None, argtypes=(POINTER(c_void_p), c_void_p, c_void_p), what="Lock")
            ctypes.memmove(p, data_ptr, size)
            _com(buf, 4, what="Unlock")
            _com(buf, 6, c_ulong(size), argtypes=(c_ulong,), what="SetCurrentLength")
            _check(self.lib.plat.MFCreateSample(byref(sample)), "MFCreateSample")
            _com(sample, 42, buf, argtypes=(c_void_p,), what="AddBuffer")
            self.write_sample(sample, t100ns, dur100ns)
        finally:
            _release(sample)
            _release(buf)

    def write_sample(self, sample, t100ns, dur100ns):
        _com(sample, 36, ctypes.c_longlong(t100ns), argtypes=(ctypes.c_longlong,), what="SetSampleTime")
        _com(sample, 38, ctypes.c_longlong(dur100ns), argtypes=(ctypes.c_longlong,), what="SetSampleDuration")
        _com(self.writer, 6, self.stream, sample, argtypes=(c_ulong, c_void_p), what="WriteSample")

    def close(self):
        if self.writer:
            try:
                _com(self.writer, 11, what="Finalize")
            except MFError:
                pass
            _release(self.writer)
            self.writer = c_void_p()


# ---------------------------------------------------------------- 屏幕 (GDI)

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", c_long), ("biHeight", c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", c_long), ("biYPelsPerMeter", c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hCursor", wintypes.HANDLE),
                ("ptScreenPos", wintypes.POINT)]


class _Screen:
    """整个虚拟屏幕 → 缩放后的 32 位位图 (从上往下)."""

    def __init__(self, max_width=SCREEN_WIDTH):
        u, g = ctypes.windll.user32, ctypes.windll.gdi32
        self.u, self.g = u, g
        for fn in ("GetDC", "CreateCompatibleDC", "CreateDIBSection", "SelectObject"):
            getattr(u if fn == "GetDC" else g, fn).restype = c_void_p
        u.GetDC.argtypes = [c_void_p]
        u.ReleaseDC.argtypes = [c_void_p, c_void_p]
        g.CreateCompatibleDC.argtypes = [c_void_p]
        g.SelectObject.argtypes = [c_void_p, c_void_p]
        g.DeleteObject.argtypes = [c_void_p]
        g.DeleteDC.argtypes = [c_void_p]
        g.StretchBlt.argtypes = [c_void_p, c_int, c_int, c_int, c_int, c_void_p, c_int, c_int, c_int, c_int,
                                 wintypes.DWORD]
        g.SetStretchBltMode.argtypes = [c_void_p, c_int]
        g.CreateDIBSection.argtypes = [c_void_p, c_void_p, c_uint, POINTER(c_void_p), c_void_p, wintypes.DWORD]
        u.DrawIconEx.argtypes = [c_void_p, c_int, c_int, c_void_p, c_int, c_int, c_uint, c_void_p, c_uint]
        self.x, self.y = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
        self.sw, self.sh = max(1, u.GetSystemMetrics(78)), max(1, u.GetSystemMetrics(79))
        scale = min(1.0, max_width / self.sw)
        self.w = max(2, int(self.sw * scale) // 2 * 2)
        self.h = max(2, int(self.sh * scale) // 2 * 2)
        self.scale = scale
        self.screen = u.GetDC(None)
        self.dc = g.CreateCompatibleDC(self.screen)
        bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), self.w, -self.h, 1, 32, 0, 0, 0, 0, 0, 0)
        self.bits = c_void_p()
        self.bmp = g.CreateDIBSection(self.dc, byref(bmi), 0, byref(self.bits), None, 0)
        if not self.bmp or not self.bits:
            self.close()
            raise MFError("CreateDIBSection")
        self.old = g.SelectObject(self.dc, self.bmp)
        g.SetStretchBltMode(self.dc, 4)                  # HALFTONE
        self.size = self.w * self.h * 4

    def grab(self):
        SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
        self.g.StretchBlt(self.dc, 0, 0, self.w, self.h, self.screen, self.x, self.y, self.sw, self.sh,
                          SRCCOPY | CAPTUREBLT)
        ci = CURSORINFO()
        ci.cbSize = ctypes.sizeof(CURSORINFO)
        if self.u.GetCursorInfo(byref(ci)) and ci.flags & 1 and ci.hCursor:
            cx = int((ci.ptScreenPos.x - self.x) * self.scale)
            cy = int((ci.ptScreenPos.y - self.y) * self.scale)
            sz = max(12, int(32 * self.scale))
            self.u.DrawIconEx(self.dc, cx, cy, ci.hCursor, sz, sz, 0, None, 3)
        return self.bits

    def close(self):
        try:
            if getattr(self, "old", None):
                self.g.SelectObject(self.dc, self.old)
            if getattr(self, "bmp", None):
                self.g.DeleteObject(self.bmp)
            if getattr(self, "dc", None):
                self.g.DeleteDC(self.dc)
            if getattr(self, "screen", None):
                self.u.ReleaseDC(None, self.screen)
        except Exception:
            pass
        self.bmp = self.dc = self.screen = self.old = None


# ---------------------------------------------------------------- 摄像头

def _camera_activates(lib):
    """视频采集设备 (IMFActivate 数组, 数量). 用完要 _free_activates."""
    attrs = _attributes(lib, 1)
    arr = POINTER(c_void_p)()
    n = c_uint()
    try:
        _set_guid(attrs, MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE, MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID)
        _check(lib.mf.MFEnumDeviceSources(attrs, byref(arr), byref(n)), "MFEnumDeviceSources")
    finally:
        _release(attrs)
    return arr, n.value


def _free_activates(lib, arr, n):
    for i in range(n):
        _release(arr[i])
    if arr:
        lib.ole.CoTaskMemFree(arr)


def camera_count() -> int:
    """有几个摄像头 (出错时 0). 会初始化一次 Media Foundation, 不要在界面线程里频繁调用."""
    try:
        lib = _startup()
    except Exception:
        return 0
    try:
        arr, n = _camera_activates(lib)
        _free_activates(lib, arr, n)
        return n
    except Exception:
        return 0
    finally:
        _shutdown(lib)


def camera_consent() -> str:
    """Windows 隐私设置里桌面应用能不能用摄像头: "allow" / "deny" / "" (查不到)."""
    try:
        import winreg
        base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"
        out = ""
        for hive, sub in ((winreg.HKEY_LOCAL_MACHINE, base), (winreg.HKEY_CURRENT_USER, base),
                          (winreg.HKEY_CURRENT_USER, base + r"\NonPackaged")):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    v = str(winreg.QueryValueEx(k, "Value")[0]).lower()
            except OSError:
                continue
            if v == "deny":
                return "deny"
            out = v or out
        return "allow" if out == "allow" else out
    except Exception:
        return ""


def _pick_camera_type(reader):
    """最接近 CAMERA_SIZE 的原生格式 (不超过它的里最大的; 都超过时最小的)."""
    best, best_key = None, None
    i = 0
    while i < 200:
        t = c_void_p()
        vtbl = ctypes.cast(reader, POINTER(POINTER(c_void_p)))[0]
        hr = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_ulong, c_ulong, POINTER(c_void_p))(vtbl[5])(
            reader, MF_SOURCE_READER_FIRST_VIDEO_STREAM, i, byref(t))
        if hr < 0:
            break
        try:
            fs = _get_u64(t, MF_MT_FRAME_SIZE)
            w, h = fs >> 32, fs & 0xFFFFFFFF
        except MFError:
            _release(t)
            i += 1
            continue
        fits = w <= CAMERA_SIZE[0] and h <= CAMERA_SIZE[1]
        key = (1, w * h) if fits else (0, -w * h)
        if best_key is None or key > best_key:
            _release(best)
            best, best_key = t, key
        else:
            _release(t)
        i += 1
    return best


# ---------------------------------------------------------------- 录像线程

class Recorder(threading.Thread):
    """一路录像 (kind = camera / screen). state: starting → running → stopped;
    出错时 failed (没能开始) / lost (录着录着摄像头没了). error 是说明."""

    def __init__(self, data_dir: str, kind: str, on_file=None):
        super().__init__(daemon=True, name=f"rec-{kind}")
        self.data_dir = data_dir
        self.kind = kind
        self.on_file = on_file                 # 写完一个文件时调用 (路径)
        self.state = "starting"
        self.error = ""
        self.started_at = None                 # 第一帧写进去的时刻 (time.time())
        self.frames = 0
        self._stop = threading.Event()

    def stop(self, wait: float = 5.0):
        self._stop.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(wait)

    @property
    def alive(self) -> bool:
        return self.state in ("starting", "running")

    def _new_path(self):
        d = folder(self.data_dir, self.kind)
        os.makedirs(d, exist_ok=True)
        stem = datetime.now().strftime("%Y%m%d-%H%M%S")
        n = 0
        while True:
            name = stem + (f"-{n}" if n else "")
            final = os.path.join(d, name + ".mp4")
            part = os.path.join(d, name + PART + ".mp4")
            if not os.path.exists(final) and not os.path.exists(part):
                return part, final
            n += 1

    def _done(self, part, final):
        try:
            os.replace(part, final)
        except OSError:
            final = part
        if self.on_file:
            try:
                self.on_file(final)
            except Exception:
                pass

    def run(self):
        try:
            lib = _startup()
        except Exception as e:
            self.state, self.error = "failed", f"Media Foundation: {e}"
            return
        try:
            (self._run_camera if self.kind == "camera" else self._run_screen)(lib)
        except Exception as e:
            if self.state == "starting":
                self.state = "failed"
            elif self.state == "running" and not self._stop.is_set():
                self.state = "lost"
            self.error = self.error or f"{type(e).__name__}: {e}"
        finally:
            if self.state in ("starting", "running"):
                self.state = "stopped"
            _shutdown(lib)

    # ---- 屏幕 ----

    def _run_screen(self, lib):
        scr = _Screen()
        try:
            period = 1.0 / SCREEN_FPS
            dur = int(10_000_000 / SCREEN_FPS)
            while not self._stop.is_set():
                part, final = self._new_path()
                w = _Writer(lib, part, scr.w, scr.h, SCREEN_FPS, SCREEN_BITRATE)
                seg0 = time.monotonic()
                k = 0
                try:
                    while not self._stop.is_set() and time.monotonic() - seg0 < SEGMENT:
                        bits = scr.grab()
                        w.write_bytes(bits, scr.size, k * dur, dur)
                        k += 1
                        self.frames += 1
                        if self.state == "starting":
                            self.state, self.started_at = "running", time.time()
                        self._stop.wait(max(0.0, seg0 + k * period - time.monotonic()))
                finally:
                    w.close()
                    self._done(part, final) if k else _remove(part)
        finally:
            scr.close()

    # ---- 摄像头 ----

    def _run_camera(self, lib):
        arr, n = _camera_activates(lib)
        if not n:
            _free_activates(lib, arr, n)
            self.error = "no camera"
            raise MFError("没有找到摄像头")
        source = c_void_p()
        try:
            _com(arr[0], 33, byref(IID_IMFMediaSource), byref(source),
                 argtypes=(POINTER(GUID), POINTER(c_void_p)), what="ActivateObject")
        finally:
            _free_activates(lib, arr, n)
        reader = c_void_p()
        attrs = _attributes(lib, 1)
        try:
            _set_u32(attrs, MF_SOURCE_READER_ENABLE_VIDEO_PROCESSING, 1)
            _check(lib.rw.MFCreateSourceReaderFromMediaSource(source, attrs, byref(reader)),
                   "MFCreateSourceReaderFromMediaSource")
        finally:
            _release(attrs)
        cur = c_void_p()
        try:
            native = _pick_camera_type(reader)
            if native:
                try:
                    _com(reader, 7, MF_SOURCE_READER_FIRST_VIDEO_STREAM, None, native,
                         argtypes=(c_ulong, c_void_p, c_void_p), what="SetCurrentMediaType(native)")
                except MFError:
                    pass
                finally:
                    _release(native)
            want = _media_type(lib)
            try:
                _set_guid(want, MF_MT_MAJOR_TYPE, MFMediaType_Video)
                _set_guid(want, MF_MT_SUBTYPE, MFVideoFormat_RGB32)
                _com(reader, 7, MF_SOURCE_READER_FIRST_VIDEO_STREAM, None, want,
                     argtypes=(c_ulong, c_void_p, c_void_p), what="SetCurrentMediaType(RGB32)")
            finally:
                _release(want)
            _com(reader, 6, MF_SOURCE_READER_FIRST_VIDEO_STREAM, byref(cur),
                 argtypes=(c_ulong, POINTER(c_void_p)), what="GetCurrentMediaType")
            fs = _get_u64(cur, MF_MT_FRAME_SIZE)
            width, height = fs >> 32, fs & 0xFFFFFFFF
            self._camera_loop(lib, reader, cur, width, height)
        finally:
            _release(cur)
            _release(reader)
            try:
                _com(source, 12, what="Shutdown")
            except Exception:
                pass
            _release(source)

    def _camera_loop(self, lib, reader, cur, width, height):
        period = 1.0 / CAMERA_FPS
        dur = int(10_000_000 / CAMERA_FPS)
        read = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_ulong, c_ulong, POINTER(c_ulong), POINTER(c_ulong),
                                  POINTER(ctypes.c_longlong), POINTER(c_void_p))(
            ctypes.cast(reader, POINTER(POINTER(c_void_p)))[0][9])
        self._last_frame = time.monotonic()
        while not self._stop.is_set():
            part, final = self._new_path()
            w = _Writer(lib, part, width, height, CAMERA_FPS, CAMERA_BITRATE, input_type=cur)
            seg0 = time.monotonic()
            last = -1e9
            k = 0
            try:
                while not self._stop.is_set() and time.monotonic() - seg0 < SEGMENT:
                    idx, flags, ts = c_ulong(), c_ulong(), ctypes.c_longlong()
                    sample = c_void_p()
                    hr = read(reader, MF_SOURCE_READER_FIRST_VIDEO_STREAM, 0, byref(idx), byref(flags),
                              byref(ts), byref(sample))
                    try:
                        if hr < 0:
                            self.error = f"ReadSample 0x{hr & 0xFFFFFFFF:08X}"
                            raise MFError(self.error)
                        if flags.value & (MF_SOURCE_READERF_ERROR | MF_SOURCE_READERF_ENDOFSTREAM):
                            self.error = f"camera stream ended (flags 0x{flags.value:X})"
                            raise MFError(self.error)
                        now = time.monotonic()
                        if not sample:
                            if now - self._last_frame > CAMERA_STALL:
                                self.error = "camera stalled"
                                raise MFError(self.error)
                            continue
                        self._last_frame = now
                        if now - last < period * 0.9:
                            continue
                        last = now
                        w.write_sample(sample, int((now - seg0) * 10_000_000), dur)
                        k += 1
                        self.frames += 1
                        if self.state == "starting":
                            self.state, self.started_at = "running", time.time()
                    finally:
                        _release(sample)
            finally:
                w.close()
                self._done(part, final) if k else _remove(part)

    def stalled(self) -> bool:
        """摄像头太久没有新画面 (ReadSample 卡住时线程自己发现不了, 由外面看)."""
        if self.kind != "camera" or self.state != "running":
            return False
        return time.monotonic() - getattr(self, "_last_frame", time.monotonic()) > CAMERA_STALL


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _selftest(data_dir=None, seconds=3):
    """录几秒屏幕, 看文件写出来了没有 (要有桌面)."""
    import tempfile
    d = data_dir or tempfile.mkdtemp()
    r = Recorder(d, "screen")
    r.start()
    t0 = time.time()
    while r.state == "starting" and time.time() - t0 < 10:
        time.sleep(0.1)
    assert r.state == "running", (r.state, r.error)
    time.sleep(seconds)
    r.stop()
    assert r.state == "stopped", (r.state, r.error)
    files = list_files(d, "screen")
    assert len(files) == 1 and not files[0]["partial"] and files[0]["size"] > 1000, files
    return f"recorder selftest OK ({r.frames} frames, {files[0]['size']} bytes, cameras: {camera_count()})", d


if __name__ == "__main__":
    print(_selftest()[0])
