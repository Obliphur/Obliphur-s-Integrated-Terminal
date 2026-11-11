"""
Windows Media Foundation / GDI / 摄像头 的 ctypes 封装 (MECHANICS 18.8).

mp4 的 Sink Writer (H.264), 整个虚拟屏幕的截取, 摄像头枚举与读取, 以及读 mp4 的
Source Reader 小工具. 都是无状态的 COM 调用; 合并逻辑在 recorder_merge.py,
录像线程在 recorder.py. 由 recorder.py 重新导出.
"""

import ctypes
import uuid
from ctypes import POINTER, byref, c_int, c_long, c_uint, c_uint64, c_ulong, c_void_p, wintypes

from recorder_parts.recorder_common import CAMERA_SIZE, SCREEN_WIDTH


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
MF_MT_VIDEO_ROTATION = _guid("c380465d-2271-428c-9b83-ecea3b4a85c1")   # mp4 的旋转标记 (手机按陀螺仪写的), 顺时针度数
MFMediaType_Audio = _guid("73647561-0000-0010-8000-00AA00389B71")
MF_MT_ALL_SAMPLES_INDEPENDENT = _guid("c9173739-5e56-461c-b713-46fb995cb95f")
MF_SINK_WRITER_DISABLE_THROTTLING = _guid("08b845d8-2b74-4afe-9d53-be16d2d5ae4f")
MF_SOURCE_READER_ENABLE_VIDEO_PROCESSING = _guid("fb394f3d-ccf1-42ee-bbb3-f9b845d5681d")
MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE = _guid("c60ac5fe-252a-478f-a0ef-bc8fa5f7cad3")
MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID = _guid("8ac3587a-4ae7-42d8-99e0-0a6013eef90f")
MF_DEVSOURCE_ATTRIBUTE_FRIENDLY_NAME = _guid("60d0e559-52f8-4fa2-bbce-acdb34a8ec01")
MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_SYMBOLIC_LINK = _guid("58f0aad8-22bf-4f8a-bb3d-d2c4978c6e2f")
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


def _get_u32(obj, key):
    v = c_uint()
    _com(obj, _GET_UINT32, byref(key), byref(v), argtypes=(POINTER(GUID), POINTER(c_uint)), what="GetUINT32")
    return v.value


def _get_guid(obj, key):
    g = GUID()
    _com(obj, _GET_GUID, byref(key), byref(g), argtypes=(POINTER(GUID), POINTER(GUID)), what="GetGUID")
    return g


def _get_string(obj, key) -> str:
    """IMFAttributes 里的字符串 (设备名字 / symbolic link), 读不出来时 ""."""
    buf = ctypes.create_unicode_buffer(1024)
    n = c_uint()
    _com(obj, 12, byref(key), ctypes.cast(buf, c_void_p), c_uint(len(buf)), byref(n),
         argtypes=(POINTER(GUID), c_void_p, c_uint, POINTER(c_uint)), what="GetString")
    return buf.value


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
    """初始化 COM + Media Foundation. 返回 (lib, 是否由本次调用初始化了 COM).
    界面线程 (Tk 把主线程设成了 STA 套间) 里调用时, CoInitializeEx(MTA) 会返回 RPC_E_CHANGED_MODE 失败 —— 这时
    绝不能再 CoUninitialize, 否则会把 Tk 的套间多释放一次, 之后界面一用到 COM (文件对话框 / 打开文件 / 剪贴板) 就卡死."""
    lib = _MF.get()
    hr = c_long(lib.ole.CoInitializeEx(None, 0)).value   # 多线程套间 (worker 线程)
    own = hr == 0                                        # S_OK: 本次负责 CoUninitialize; S_FALSE / 失败: 不碰 (同 audiomute)
    _check(lib.plat.MFStartup(MF_VERSION, 0), "MFStartup")
    return lib, own


def _shutdown(lib, own=True):
    try:
        lib.plat.MFShutdown()
        if own:
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


# ---------------------------------------------------------------- 写 mp4

class _Writer:
    """H.264 / mp4 的 Sink Writer (只有画面): 输入是 RGB32 (input_type) 或者按 width/height 建的上下正向 RGB32."""

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
        _com(self.writer, 6, self.stream, sample, argtypes=(c_ulong, c_void_p),
             what="WriteSample")

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


def _camera_name(dev) -> str:
    try:
        return _get_string(dev, MF_DEVSOURCE_ATTRIBUTE_FRIENDLY_NAME).strip()
    except MFError:
        return ""


def _camera_link(dev) -> str:
    try:
        return _get_string(dev, MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_SYMBOLIC_LINK).strip()
    except MFError:
        return ""


def camera_devices() -> list:
    """本机的摄像头: [{"id", "name"}], 设置 → 录像 里选 (MECHANICS 18.8).
    id 优先用设备的 symbolic link (稳定, 换 USB 口也认得出); 没有时 "name|<名字>".
    和 camera_count 一样会初始化一次 Media Foundation, 不要在界面线程里频繁调用. 出错时 []."""
    try:
        lib, own = _startup()
    except Exception:
        return []
    try:
        arr, n = _camera_activates(lib)
        try:
            out = []
            for i in range(n):
                name = _camera_name(arr[i]) or f"Camera {i + 1}"
                out.append({"id": _camera_link(arr[i]) or f"name|{name}", "name": name})
            return out
        finally:
            _free_activates(lib, arr, n)
    except Exception:
        return []
    finally:
        _shutdown(lib, own)


def _camera_index(arr, n, wanted: str) -> int:
    """要用第几个摄像头 (设置 → 录像 选的那台). wanted: camera_devices 的 id (或名字);
    没有选 / 找不到时用第一个 (和以前一样)."""
    wanted = str(wanted or "").strip()
    if not wanted or n <= 1:
        return 0
    by_name = -1
    for i in range(n):
        name = _camera_name(arr[i])
        dev_id = _camera_link(arr[i]) or f"name|{name}"
        if dev_id == wanted:
            return i
        if wanted.startswith("name|") and name == wanted[5:]:
            return i
        if by_name < 0 and wanted == name:
            by_name = i
    return by_name if by_name >= 0 else 0


def camera_count() -> int:
    """有几个摄像头 (出错时 0). 会初始化一次 Media Foundation, 不要在界面线程里频繁调用."""
    try:
        lib, own = _startup()
    except Exception:
        return 0
    try:
        arr, n = _camera_activates(lib)
        _free_activates(lib, arr, n)
        return n
    except Exception:
        return 0
    finally:
        _shutdown(lib, own)


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


# ---------------------------------------------------------------- 读 mp4 (合并 / 自检)

def _reader_type(lib, reader, stream, major, subtype, setup=None):
    """给 Source Reader 的某一路设定要解出来的格式 (video → RGB32, audio → PCM); 返回实际的媒体类型 (要 _release)."""
    t = _media_type(lib)
    try:
        _set_guid(t, MF_MT_MAJOR_TYPE, major)
        _set_guid(t, MF_MT_SUBTYPE, subtype)
        if setup:
            setup(t)
        _com(reader, 7, stream, None, t, argtypes=(c_ulong, c_void_p, c_void_p), what="SetCurrentMediaType")
    finally:
        _release(t)
    cur = c_void_p()
    _com(reader, 6, stream, byref(cur), argtypes=(c_ulong, POINTER(c_void_p)), what="GetCurrentMediaType")
    return cur


def _video_rotation(reader) -> int:
    """这段视频播放时要顺时针转多少度 (0 / 90 / 180 / 270): 手机录目光时按陀螺仪写进 mp4 的旋转标记 (MECHANICS 18.8).
    Source Reader 解出来的画面是没转过的, 合并时要自己转."""
    t = c_void_p()
    try:
        _com(reader, 5, MF_SOURCE_READER_FIRST_VIDEO_STREAM, 0, byref(t),
             argtypes=(c_ulong, c_ulong, POINTER(c_void_p)), what="GetNativeMediaType")
        return int(_get_u32(t, MF_MT_VIDEO_ROTATION)) % 360
    except MFError:
        return 0
    finally:
        _release(t)


def _find_streams(reader):
    """Source Reader 里画面 / 声音分别是第几路 (mp4 里顺序不一定): 返回 (画面流号, 声音流号), 没有的为 -1."""
    vi = ai = -1
    for i in range(8):
        t = c_void_p()
        vtbl = ctypes.cast(reader, POINTER(POINTER(c_void_p)))[0]
        hr = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_ulong, c_ulong, POINTER(c_void_p))(vtbl[5])(
            reader, i, 0, byref(t))
        if hr < 0:
            break
        try:
            major = _get_guid(t, MF_MT_MAJOR_TYPE)
        except MFError:
            major = None
        finally:
            _release(t)
        if major == MFMediaType_Video and vi < 0:
            vi = i
        elif major == MFMediaType_Audio and ai < 0:
            ai = i
    return vi, ai


def _sample_bytes(sample):
    """IMFSample → (指针, 字节数, 连续缓冲区); 用完要 Unlock + _release(buf)."""
    buf = c_void_p()
    _com(sample, 41, byref(buf), argtypes=(POINTER(c_void_p),), what="ConvertToContiguousBuffer")
    p, n = c_void_p(), c_ulong()
    _com(buf, 3, byref(p), None, byref(n), argtypes=(POINTER(c_void_p), c_void_p, POINTER(c_ulong)), what="Lock")
    return p, n.value, buf


def _dims(path: str):
    """mp4 画面 (按旋转标记转正后) 的宽高."""
    lib, own = _startup()
    reader = c_void_p()
    try:
        _check(lib.rw.MFCreateSourceReaderFromURL(ctypes.c_wchar_p(path), None, byref(reader)), "open")
        t = c_void_p()
        _com(reader, 6, MF_SOURCE_READER_FIRST_VIDEO_STREAM, byref(t),
             argtypes=(c_ulong, POINTER(c_void_p)), what="GetCurrentMediaType")
        try:
            fs = _get_u64(t, MF_MT_FRAME_SIZE)
            w, h = fs >> 32, fs & 0xFFFFFFFF
            return (h, w) if _video_rotation(reader) in (90, 270) else (w, h)
        finally:
            _release(t)
    finally:
        _release(reader)
        _shutdown(lib, own)


def _streams(path: str) -> int:
    """mp4 里有几路 (画面 + 声音), 用 Source Reader 数."""
    lib, own = _startup()
    reader = c_void_p()
    try:
        _check(lib.rw.MFCreateSourceReaderFromURL(ctypes.c_wchar_p(path), None, byref(reader)), "open")
        n = 0
        while n < 8:
            t = c_void_p()
            vtbl = ctypes.cast(reader, POINTER(POINTER(c_void_p)))[0]
            hr = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_ulong, c_ulong, POINTER(c_void_p))(vtbl[5])(
                reader, n, 0, byref(t))
            if hr < 0:
                break
            _release(t)
            n += 1
        return n
    finally:
        _release(reader)
        _shutdown(lib, own)
