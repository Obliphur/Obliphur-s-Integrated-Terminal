"""
合约的录像 (MECHANICS.md 18.8 目光 / 刻痕): 摄像头和整个屏幕各录成 mp4 (H.264 + AAC), 只用标准库.

编码用 Windows 自带的 Media Foundation (mfplat / mfreadwrite, 经 ctypes 调用 COM 接口):
- 屏幕: GDI 截取整个虚拟屏幕 (所有显示器, 带鼠标指针), 缩到最宽 SCREEN_WIDTH, 每秒 SCREEN_FPS 帧;
- 摄像头: 第一个视频采集设备, 挑最接近 640×480 的格式, 每秒最多 CAMERA_FPS 帧;
- 声音 (设置 → 录像, 默认都录): 麦克风 (外部音) 和系统音 (WASAPI loopback), 混成一路 48 kHz 立体声.
录像在后台线程里写. 每 SEGMENT 秒换一个文件 (程序被强行关掉时最多丢这一段).

加密 (防止被网盘 / 云相册自动上传后看到): 正在写的 mp4 放在系统的临时文件夹里 (work_dir, 不在数据目录),
写完 (Finalize) 后用 AES-256-CTR 加密成 .oitv 存进数据目录, 再删掉 mp4. 只能在 OIT 里播放 (play: 解密到临时文件夹再打开).
文件格式: MAGIC + 8 字节 nonce + 密文 (计数器 = nonce + 第几个 16 字节块, 同 Android 的 RecCrypto).

存放: 数据目录/recordings/camera 和 数据目录/recordings/screen, 分开存放.
"""

import array
import ctypes
import hashlib
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import uuid
from ctypes import POINTER, byref, c_int, c_long, c_uint, c_uint64, c_ulong, c_void_p, wintypes
from datetime import datetime

KINDS = ("camera", "screen")
PART = ".recording"
EXT = ".oitv"                     # 加密后的录像
MAGIC = b"OITV\x01"
REC_KEY = hashlib.sha256(b"obliphur-terminal/recording/v1").digest()   # 所有 OIT 共用 (简易加密: 只防自动上传)
AUDIO_RATE = 48000
AUDIO_CH = 2
AUDIO_BYTES = 16_000              # AAC 128 kbps
AUDIO_LAG = 0.25                  # 声音比画面晚这么多秒写 (等采集的数据到齐)
SEGMENT = 30 * 60                 # 每个文件最长 30 分钟
FOCUS_EVERY = 10                  # 刻痕: 每这么多秒判定一次焦点设备 (MECHANICS 18.8)
SCREEN_FPS = 1
SCREEN_WIDTH = 960               # 画面缩到最宽这么多 (压一下画质 / 体积; 合并画布也按它封顶)
SCREEN_BITRATE = 200_000
CAMERA_FPS = 1
CAMERA_BITRATE = 64_000           # 目光每秒只有 1 帧: 码率压低, 文件小 (传给别的设备合并 / 同步时不会太大)
CAMERA_AUDIO_BYTES = 12_000       # 目光的声音 AAC 96 kbps (Media Foundation 的 AAC 编码器支持的最低一档)
CAMERA_SIZE = (480, 360)
CAMERA_STALL = 12                 # 摄像头这么多秒没有新画面: 视为被关掉了


def folder(data_dir: str, kind: str) -> str:
    return os.path.join(data_dir, "recordings", kind)


def work_dir(data_dir: str, kind: str) -> str:
    """正在写的 (还没加密的) mp4 放在这里: 系统的临时文件夹, 不在数据目录 (数据目录可能在网盘里)."""
    tag = hashlib.sha1(os.path.abspath(data_dir).lower().encode("utf-8")).hexdigest()[:10]
    return os.path.join(tempfile.gettempdir(), "oit_rec", tag, kind)


def play_dir() -> str:
    return os.path.join(tempfile.gettempdir(), "oit_play")


def list_files(data_dir: str, kind: str) -> list:
    """录像文件, 新的在前: [{path, name, size, start, partial}]. partial: 没有正常写完 (多半放不出来);
    正在写的那个在临时文件夹里 (还没加密), 也列出来, 同样是 partial."""
    out = []
    for d in (folder(data_dir, kind), work_dir(data_dir, kind)):
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            low = n.lower()
            ext = next((e for e in (EXT, ".mp4") if low.endswith(e)), "")
            if not ext:
                continue
            p = os.path.join(d, n)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            stem = n[:-len(ext)]
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


# ---------------------------------------------------------------- 加密 (AES-256-CTR, Windows CNG)

class _Aes:
    """AES-256-ECB (bcrypt.dll), 用来算 CTR 的密钥流."""
    _lock = threading.Lock()
    _key = None

    @classmethod
    def handle(cls):
        with cls._lock:
            if cls._key is None:
                b = ctypes.windll.bcrypt
                alg, key = c_void_p(), c_void_p()
                if b.BCryptOpenAlgorithmProvider(byref(alg), ctypes.c_wchar_p("AES"), None, 0):
                    raise OSError("BCryptOpenAlgorithmProvider")
                mode = ctypes.create_unicode_buffer("ChainingModeECB")
                if b.BCryptSetProperty(alg, ctypes.c_wchar_p("ChainingMode"), mode, ctypes.sizeof(mode), 0):
                    raise OSError("BCryptSetProperty")
                kb = ctypes.create_string_buffer(REC_KEY, len(REC_KEY))
                if b.BCryptGenerateSymmetricKey(alg, byref(key), None, 0, kb, len(REC_KEY), 0):
                    raise OSError("BCryptGenerateSymmetricKey")
                cls._alg, cls._key = alg, key
            return cls._key

    @classmethod
    def ecb(cls, data: bytes) -> bytes:
        key = cls.handle()
        src = ctypes.create_string_buffer(data, len(data))
        dst = ctypes.create_string_buffer(len(data))
        n = c_ulong()
        if ctypes.windll.bcrypt.BCryptEncrypt(key, src, len(data), None, None, 0, dst, len(data), byref(n), 0):
            raise OSError("BCryptEncrypt")
        return dst.raw


def rec_xor(nonce: bytes, offset: int, data: bytes) -> bytes:
    """录像的内容和密钥流异或 (加密 / 解密一样). 第 i 个 16 字节块的计数器 = nonce (8 字节) + i (8 字节, 大端);
    可以从任意位置 offset 开始. 同 Android 的 RecCrypto (AES/CTR/NoPadding, IV = nonce + 0)."""
    if not data:
        return b""
    first, skip = divmod(int(offset), 16)
    count = (skip + len(data) + 15) // 16
    ctr = b"".join(nonce + (first + i).to_bytes(8, "big") for i in range(count))
    ks = _Aes.ecb(ctr)[skip:skip + len(data)]
    return (int.from_bytes(data, "big") ^ int.from_bytes(ks, "big")).to_bytes(len(data), "big")


_CHUNK = 1 << 20


def encrypt_file(src: str, dst: str):
    """src (mp4) → dst (.oitv): 先写到 dst.tmp, 写完再改名. 不删 src."""
    nonce = secrets.token_bytes(8)
    tmp = dst + ".tmp"
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        fo.write(MAGIC + nonce)
        off = 0
        while True:
            b = fi.read(_CHUNK)
            if not b:
                break
            fo.write(rec_xor(nonce, off, b))
            off += len(b)
    os.replace(tmp, dst)


def decrypt_file(src: str, dst: str):
    """src (.oitv) → dst (mp4)."""
    with open(src, "rb") as fi:
        head = fi.read(len(MAGIC) + 8)
        if head[:len(MAGIC)] != MAGIC:
            raise ValueError("not an OIT recording")
        nonce = head[len(MAGIC):]
        with open(dst, "wb") as fo:
            off = 0
            while True:
                b = fi.read(_CHUNK)
                if not b:
                    break
                fo.write(rec_xor(nonce, off, b))
                off += len(b)


def seal_file(data_dir: str, kind: str, src: str) -> str:
    """写完的 mp4 (src, 在 work_dir 或旧版本留在数据目录里的) → 数据目录里的 .oitv, 删掉 src. 返回新的路径."""
    d = folder(data_dir, kind)
    os.makedirs(d, exist_ok=True)
    name = os.path.basename(src)
    if name.lower().endswith(".mp4"):
        name = name[:-4]
    dst = os.path.join(d, name + EXT)
    n = 1
    while os.path.exists(dst):
        dst = os.path.join(d, f"{name}-{n}{EXT}")
        n += 1
    encrypt_file(src, dst)
    _remove(src)
    return dst


def seal_leftovers(data_dir: str, busy=()) -> int:
    """打开程序时: 上次没加密的 (程序被强行关掉时留在临时文件夹里的, 旧版本存在数据目录里的 mp4) 都加密起来.
    busy: 正在写的文件 (跳过). 返回加密了几个."""
    n = 0
    for kind in KINDS:
        for d in (work_dir(data_dir, kind), folder(data_dir, kind)):
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for name in names:
                p = os.path.join(d, name)
                if not name.lower().endswith(".mp4") or p in busy:
                    continue
                try:
                    seal_file(data_dir, kind, p)
                    n += 1
                except OSError:
                    pass
    return n


def clean_play():
    """删掉播放时解密出来的临时文件 (正被播放器打开着的删不掉, 下次再删)."""
    d = play_dir()
    try:
        names = os.listdir(d)
    except OSError:
        return
    for name in names:
        _remove(os.path.join(d, name))


def play(path: str) -> str:
    """在 OIT 里打开一个录像: .oitv 解密到临时文件夹 (play_dir) 再用系统默认的播放器打开. 返回打开的文件."""
    clean_play()
    if not path.lower().endswith(EXT):
        os.startfile(path)
        return path
    d = play_dir()
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, os.path.basename(path)[:-len(EXT)] + ".mp4")
    if os.path.exists(out):
        out = os.path.join(d, f"{os.path.basename(path)[:-len(EXT)]}-{secrets.token_hex(3)}.mp4")
    decrypt_file(path, out)
    os.startfile(out)
    return out


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
MFMediaType_Audio = _guid("73647561-0000-0010-8000-00AA00389B71")
MFAudioFormat_AAC = _guid("00001610-0000-0010-8000-00AA00389B71")
MFAudioFormat_PCM = _guid("00000001-0000-0010-8000-00AA00389B71")
MF_MT_AUDIO_NUM_CHANNELS = _guid("37e48bf5-645e-4c5b-89de-ada9e29b696a")
MF_MT_AUDIO_SAMPLES_PER_SECOND = _guid("5faeeae7-0290-4c31-9e8a-c534f68d9dba")
MF_MT_AUDIO_BLOCK_ALIGNMENT = _guid("322de230-9eeb-43bd-ab7a-ff412251541d")
MF_MT_AUDIO_AVG_BYTES_PER_SECOND = _guid("1aab75c8-cfef-451c-ab95-ac034b8e1731")
MF_MT_AUDIO_BITS_PER_SAMPLE = _guid("f2deb57f-40fa-4764-aa33-ed4f2d1ff669")
MF_MT_ALL_SAMPLES_INDEPENDENT = _guid("c9173739-5e56-461c-b713-46fb995cb95f")
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


def _get_u32(obj, key):
    v = c_uint()
    _com(obj, _GET_UINT32, byref(key), byref(v), argtypes=(POINTER(GUID), POINTER(c_uint)), what="GetUINT32")
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


class _Writer:
    """H.264 / mp4 的 Sink Writer: 输入是 RGB32 (input_type) 或者按 width/height 建的上下正向 RGB32.
    audio: 另外加一路 AAC (输入 16 位 PCM, AUDIO_RATE, AUDIO_CH)."""

    def __init__(self, lib, path, width, height, fps, bitrate, input_type=None, audio=False, audio_bytes=AUDIO_BYTES):
        self.lib = lib
        self.audio_bytes = audio_bytes
        self.writer = c_void_p()
        self.stream = c_ulong()
        self.astream = None
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
        if audio:
            try:
                self._add_audio()
            except MFError:
                self.astream = None             # 加不上声音: 只录画面
        _com(self.writer, 5, what="BeginWriting")

    def _add_audio(self):
        lib = self.lib
        out = _media_type(lib)
        stream = c_ulong()
        try:
            _set_guid(out, MF_MT_MAJOR_TYPE, MFMediaType_Audio)
            _set_guid(out, MF_MT_SUBTYPE, MFAudioFormat_AAC)
            _set_u32(out, MF_MT_AUDIO_BITS_PER_SAMPLE, 16)
            _set_u32(out, MF_MT_AUDIO_SAMPLES_PER_SECOND, AUDIO_RATE)
            _set_u32(out, MF_MT_AUDIO_NUM_CHANNELS, AUDIO_CH)
            _set_u32(out, MF_MT_AUDIO_AVG_BYTES_PER_SECOND, self.audio_bytes)
            _com(self.writer, 3, out, byref(stream), argtypes=(c_void_p, POINTER(c_ulong)), what="AddStream(audio)")
        finally:
            _release(out)
        inp = _media_type(lib)
        try:
            _set_guid(inp, MF_MT_MAJOR_TYPE, MFMediaType_Audio)
            _set_guid(inp, MF_MT_SUBTYPE, MFAudioFormat_PCM)
            _set_u32(inp, MF_MT_AUDIO_BITS_PER_SAMPLE, 16)
            _set_u32(inp, MF_MT_AUDIO_SAMPLES_PER_SECOND, AUDIO_RATE)
            _set_u32(inp, MF_MT_AUDIO_NUM_CHANNELS, AUDIO_CH)
            _set_u32(inp, MF_MT_AUDIO_BLOCK_ALIGNMENT, 2 * AUDIO_CH)
            _set_u32(inp, MF_MT_AUDIO_AVG_BYTES_PER_SECOND, AUDIO_RATE * 2 * AUDIO_CH)
            _set_u32(inp, MF_MT_ALL_SAMPLES_INDEPENDENT, 1)
            _com(self.writer, 4, stream, inp, None, argtypes=(c_ulong, c_void_p, c_void_p),
                 what="SetInputMediaType(audio)")
        finally:
            _release(inp)
        self.astream = stream

    def write_audio(self, pcm: bytes, t100ns, dur100ns):
        if self.astream is None or not pcm:
            return
        buf = ctypes.create_string_buffer(pcm, len(pcm))
        self.write_bytes(buf, len(pcm), t100ns, dur100ns, stream=self.astream)

    def write_bytes(self, data_ptr, size, t100ns, dur100ns, stream=None):
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
            self.write_sample(sample, t100ns, dur100ns, stream)
        finally:
            _release(sample)
            _release(buf)

    def write_sample(self, sample, t100ns, dur100ns, stream=None):
        _com(sample, 36, ctypes.c_longlong(t100ns), argtypes=(ctypes.c_longlong,), what="SetSampleTime")
        _com(sample, 38, ctypes.c_longlong(dur100ns), argtypes=(ctypes.c_longlong,), what="SetSampleDuration")
        _com(self.writer, 6, self.stream if stream is None else stream, sample, argtypes=(c_ulong, c_void_p),
             what="WriteSample")

    def close(self):
        if self.writer:
            try:
                _com(self.writer, 11, what="Finalize")
            except MFError:
                pass
            _release(self.writer)
            self.writer = c_void_p()


# ---------------------------------------------------------------- 声音 (WASAPI)

CLSID_MMDeviceEnumerator = _guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = _guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioClient = _guid("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2")
IID_IAudioCaptureClient = _guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317")
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD), ("nSamplesPerSec", wintypes.DWORD),
                ("nAvgBytesPerSec", wintypes.DWORD), ("nBlockAlign", wintypes.WORD),
                ("wBitsPerSample", wintypes.WORD), ("cbSize", wintypes.WORD)]


class _Capture:
    """一路声音: 默认的麦克风 (loopback=False) 或系统播放的声音 (loopback=True), 转成 16 位 PCM 立体声 48 kHz.
    采到的数据攒在 buf 里."""

    def __init__(self, lib, loopback: bool):
        self.loopback = loopback
        self.client = self.capture = None
        self.buf = bytearray()
        en, dev, client, cap = c_void_p(), c_void_p(), c_void_p(), c_void_p()
        try:
            _check(lib.ole.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, 0x17,
                                            byref(IID_IMMDeviceEnumerator), byref(en)), "CoCreateInstance")
            _com(en, 4, 0 if loopback else 1, 0, byref(dev), argtypes=(c_int, c_int, POINTER(c_void_p)),
                 what="GetDefaultAudioEndpoint")
            _com(dev, 3, byref(IID_IAudioClient), 0x17, None, byref(client),
                 argtypes=(POINTER(GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p)), what="Activate")
            fmt = WAVEFORMATEX(1, AUDIO_CH, AUDIO_RATE, AUDIO_RATE * 2 * AUDIO_CH, 2 * AUDIO_CH, 16, 0)
            flags = AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY
            if loopback:
                flags |= AUDCLNT_STREAMFLAGS_LOOPBACK
            _com(client, 3, 0, flags, ctypes.c_longlong(30_000_000), ctypes.c_longlong(0), byref(fmt), None,
                 argtypes=(c_int, wintypes.DWORD, ctypes.c_longlong, ctypes.c_longlong, c_void_p, c_void_p),
                 what="IAudioClient.Initialize")
            _com(client, 14, byref(IID_IAudioCaptureClient), byref(cap),
                 argtypes=(POINTER(GUID), POINTER(c_void_p)), what="GetService")
            _com(client, 10, what="IAudioClient.Start")
            self.client, self.capture = client, cap
            client = cap = None
        finally:
            _release(cap)
            _release(client)
            _release(dev)
            _release(en)
        self._get = ctypes.WINFUNCTYPE(HRESULT, c_void_p, POINTER(POINTER(ctypes.c_ubyte)), POINTER(c_uint),
                                       POINTER(wintypes.DWORD), c_void_p, c_void_p)(
            ctypes.cast(self.capture, POINTER(POINTER(c_void_p)))[0][3])

    def pump(self):
        """把已经采到的都取出来放进 buf. 设备没了 (拔掉 / 换了默认设备) 时抛 MFError."""
        while True:
            n = c_uint()
            _com(self.capture, 5, byref(n), argtypes=(POINTER(c_uint),), what="GetNextPacketSize")
            if not n.value:
                return
            data, frames, flags = POINTER(ctypes.c_ubyte)(), c_uint(), wintypes.DWORD()
            _check(self._get(self.capture, byref(data), byref(frames), byref(flags), None, None), "GetBuffer")
            size = frames.value * 2 * AUDIO_CH
            if flags.value & AUDCLNT_BUFFERFLAGS_SILENT or not data:
                self.buf += bytes(size)
            else:
                self.buf += ctypes.string_at(data, size)
            _com(self.capture, 4, frames.value, argtypes=(c_uint,), what="ReleaseBuffer")

    def close(self):
        if self.client:
            try:
                _com(self.client, 11, what="IAudioClient.Stop")
            except MFError:
                pass
        _release(self.capture)
        _release(self.client)
        self.capture = self.client = None


class _Audio:
    """录像的声音: 麦克风 + 系统音 (各自可以关), 按时间取出混好的 16 位 PCM. 打不开的那一路不录 (note 里写原因)."""

    def __init__(self, lib, mic: bool, system: bool):
        self.srcs = []
        self.note = ""
        for on, loop in ((mic, False), (system, True)):
            if not on:
                continue
            try:
                self.srcs.append(_Capture(lib, loop))
            except Exception as e:
                self.note += f"{'system' if loop else 'mic'}: {e}; "

    def take(self, frames: int) -> bytes:
        """取 frames 帧 (不够的补静音), 多路相加 (超出范围的截断). 攒得太多 (比 AUDIO_LAG 多 1 秒) 时丢掉最旧的."""
        size = frames * 2 * AUDIO_CH
        parts = []
        for s in list(self.srcs):
            try:
                s.pump()
            except Exception as e:
                self.note += f"{'system' if s.loopback else 'mic'} lost: {e}; "
                s.close()
                self.srcs.remove(s)
                continue
            keep = int((AUDIO_LAG + 1.0) * AUDIO_RATE) * 2 * AUDIO_CH
            if len(s.buf) > size + keep:
                del s.buf[:len(s.buf) - size - keep]
            chunk = bytes(s.buf[:size])
            del s.buf[:size]
            if chunk.strip(b"\x00"):
                parts.append(chunk + bytes(size - len(chunk)))
        if not parts:
            return bytes(size)
        if len(parts) == 1:
            return parts[0]
        a, b = array.array("h", parts[0]), array.array("h", parts[1])
        mixed = array.array("h", [x if x > -32768 else -32768 for x in
                                  (y if y < 32767 else 32767 for y in (p + q for p, q in zip(a, b)))])
        return mixed.tobytes()

    def close(self):
        for s in self.srcs:
            s.close()
        self.srcs = []


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


# ---------------------------------------------------------------- 录像线程

class Recorder(threading.Thread):
    """一路录像 (kind = camera / screen). state: starting → running → stopped;
    出错时 failed (没能开始) / lost (录着录着摄像头没了). error 是说明."""

    def __init__(self, data_dir: str, kind: str, on_file=None, audio=None, tag=""):
        super().__init__(daemon=True, name=f"rec-{kind}")
        self.data_dir = data_dir
        self.kind = kind
        self.tag = re.sub(r"[^0-9A-Za-z]", "", str(tag))[:24]   # 合约 id: 文件名里带上, 结束后按它合并 (18.8)
        self.on_file = on_file                 # 写完一个文件 (加密好) 时调用 (路径)
        self.audio = audio or (lambda: (False, False))   # () → (录麦克风, 录系统音); 每段开始时 / 改了时重新打开
        self._aud = None
        self._aud_opts = None
        self._afr = 0                          # 这一段已经写了多少帧声音
        self.writing = ""                      # 正在写的文件 (临时文件夹里)
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
        d = work_dir(self.data_dir, self.kind)
        os.makedirs(d, exist_ok=True)
        out = folder(self.data_dir, self.kind)
        stem = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = f"__{self.tag}" if self.tag else ""
        n = 0
        while True:
            name = stem + (f"-{n}" if n else "") + suffix
            final = os.path.join(d, name + ".mp4")
            part = os.path.join(d, name + PART + ".mp4")
            if not any(os.path.exists(x) for x in (final, part, os.path.join(out, name + EXT))):
                self.writing = part
                return part, final
            n += 1

    def _done(self, part, final):
        """一段写完了: 改成正式的名字, 在另一个线程里加密进数据目录 (不耽误下一段)."""
        self.writing = ""
        try:
            os.replace(part, final)
        except OSError:
            final = part

        def seal():
            try:
                path = seal_file(self.data_dir, self.kind, final)
            except OSError as e:
                self.error = self.error or f"encrypt: {e}"
                return
            if self.on_file:
                try:
                    self.on_file(path)
                except Exception:
                    pass
        t = threading.Thread(target=seal, daemon=True, name=f"rec-seal-{self.kind}")
        t.start()
        self._sealing = t

    def wait_sealed(self, wait: float = 10.0):
        """等最后一段加密完 (关闭程序时)."""
        t = getattr(self, "_sealing", None)
        if t is not None and t.is_alive():
            t.join(wait)

    # ---- 声音 ----

    def _audio_open(self, lib):
        """按设置打开 (或重新打开) 声音; 返回这一段要不要加声音轨."""
        try:
            opts = tuple(bool(x) for x in self.audio())
        except Exception:
            opts = (False, False)
        if opts != self._aud_opts:
            if self._aud is not None:
                self._aud.close()
            self._aud = _Audio(lib, *opts) if any(opts) else None
            self._aud_opts = opts
            if self._aud is not None and self._aud.note:
                self.error = self.error or self._aud.note
        self._afr = 0
        return any(opts)

    def _audio_feed(self, w, seg0) -> bool:
        """把到现在为止 (晚 AUDIO_LAG 秒) 的声音写进这一段. 设置改了要加声音轨时返回 True (换一个文件)."""
        try:
            opts = tuple(bool(x) for x in self.audio())
        except Exception:
            opts = self._aud_opts
        if opts != self._aud_opts:
            if any(opts) and w.astream is None:
                return True
            self._audio_open(_MF.get())
            self._afr = int((time.monotonic() - seg0 - AUDIO_LAG) * AUDIO_RATE)
            return False
        if w.astream is None:
            return False
        due = int((time.monotonic() - seg0 - AUDIO_LAG) * AUDIO_RATE) - self._afr
        if due < AUDIO_RATE // 20:
            return False
        pcm = self._aud.take(due) if self._aud is not None else bytes(due * 2 * AUDIO_CH)
        w.write_audio(pcm, self._afr * 10_000_000 // AUDIO_RATE, due * 10_000_000 // AUDIO_RATE)
        self._afr += due
        return False

    def _audio_close(self):
        if self._aud is not None:
            self._aud.close()
            self._aud = None
        self._aud_opts = None

    def run(self):
        try:
            lib, own = _startup()
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
            self._audio_close()
            _shutdown(lib, own)

    # ---- 屏幕 ----

    def _run_screen(self, lib):
        scr = _Screen()
        try:
            period = 1.0 / SCREEN_FPS
            dur = int(10_000_000 / SCREEN_FPS)
            while not self._stop.is_set():
                part, final = self._new_path()
                audio = self._audio_open(lib)
                w = _Writer(lib, part, scr.w, scr.h, SCREEN_FPS, SCREEN_BITRATE, audio=audio)
                seg0 = time.monotonic()
                k = 0
                split = False
                try:
                    while not self._stop.is_set() and not split and time.monotonic() - seg0 < SEGMENT:
                        bits = scr.grab()
                        w.write_bytes(bits, scr.size, k * dur, dur)
                        k += 1
                        self.frames += 1
                        if self.state == "starting":
                            self.state, self.started_at = "running", time.time()
                        while not self._stop.is_set() and not split:
                            left = seg0 + k * period - time.monotonic()
                            if left <= 0:
                                break
                            split = self._audio_feed(w, seg0)
                            self._stop.wait(min(left, 0.2))
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
            audio = self._audio_open(lib)
            w = _Writer(lib, part, width, height, CAMERA_FPS, CAMERA_BITRATE, input_type=cur, audio=audio,
                        audio_bytes=CAMERA_AUDIO_BYTES)
            seg0 = time.monotonic()
            last = -1e9
            k = 0
            try:
                while not self._stop.is_set() and time.monotonic() - seg0 < SEGMENT:
                    if self._audio_feed(w, seg0):
                        break
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


def cid_of(name: str) -> str:
    """文件名里的合约 id (录像按它合并): "YYYYmmdd-HHMMSS[-n]__<cid>.ext" → cid; 没有则 ""."""
    stem = os.path.splitext(os.path.basename(name))[0]
    if stem.endswith(PART):
        stem = stem[:-len(PART)]
    _sep, _m, cid = stem.partition("__")
    return cid


def files_of(data_dir: str, kind: str, cid: str) -> list:
    """某个合约这一路的录像片段 (新的在前)."""
    return [f for f in list_files(data_dir, kind) if cid and cid_of(f["name"]) == cid]


# ---------------------------------------------------------------- 合并 (刻痕: 把各设备 / 各段录屏接成一个)

MF_SOURCE_READER_FIRST_AUDIO_STREAM = 0xFFFFFFFD


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


def _rotate90(src: bytes, sw, sh, stride) -> tuple:
    """RGB32 源 (sw×sh, 行距 stride 可正负) 顺时针转 90°: 返回 (新 bytes, 新宽=sh, 新高=sw, 新行距=sh*4, 从上往下).
    只在"参与的全是竖屏设备、却冒出一帧横屏画面"时用 (MECHANICS 18.8), 把横屏画面转正."""
    astride = abs(stride)
    nw, nh = sh, sw
    nrow = nw * 4
    out = bytearray(nrow * nh)
    for ny in range(nh):                 # ny: 0..sw-1
        sx = ny
        for nx in range(nw):             # nx: 0..sh-1
            sy = sh - 1 - nx
            soff = (sy if stride > 0 else (sh - 1 - sy)) * astride + sx * 4
            doff = ny * nrow + nx * 4
            out[doff: doff + 4] = src[soff: soff + 4]
    return bytes(out), nw, nh, nrow


def _fit(src: bytes, sw, sh, stride, dw, dh) -> bytes:
    """一帧 RGB32 (源 sw×sh, 行距 stride 字节, 可正可负) 等比缩放后放进 dw×dh 的画布 (从上往下):
    保持宽高比, 居中, 周围补黑 (横屏画布放竖屏内容时左右留黑, 反之上下留黑). 用最近邻缩放 (录屏 1 fps, 够用)."""
    astride = abs(stride)
    row = dw * 4
    out = bytearray(row * dh)                       # 全黑画布
    if sw <= 0 or sh <= 0:
        return bytes(out)
    scale = min(dw / sw, dh / sh)                   # 等比缩放到刚好放进画布 (contain)
    iw = max(2, min(dw, int(sw * scale)) & ~1)      # 内容区大小 (取偶)
    ih = max(2, min(dh, int(sh * scale)) & ~1)
    ox, oy = (dw - iw) // 2, (dh - ih) // 2         # 居中
    same_w = (iw == sw)
    xmap = None if same_w else [min(sw - 1, x * sw // iw) * 4 for x in range(iw)]
    for dyi in range(ih):
        sy = min(sh - 1, dyi * sh // ih)
        soff = (sy if stride > 0 else (sh - 1 - sy)) * astride
        scaled = src[soff: soff + iw * 4] if same_w else b"".join(src[soff + xb: soff + xb + 4] for xb in xmap)
        dy = oy + dyi
        out[dy * row + ox * 4: dy * row + ox * 4 + len(scaled)] = scaled
    return bytes(out)


def _canvas(info):
    """合并画布的大小和朝向 (MECHANICS 18.8). info: [(宽, 高, 是不是本机录的)].
    - 有横屏设备参与就按横屏: 本机 (电脑, 横屏设备) 录过, 或片段里横屏占多数; 否则 (全是竖屏手机) 按竖屏.
    - 画布取同朝向里面积最大的那段 (保持分辨率不变), 最长边再压到不超过 SCREEN_WIDTH (压一下体积).
    - 不同朝向的内容: 横屏画布里的竖屏内容左右留黑; 竖屏画布里的横屏画面直接转正 (见 _reencode_concat / _rotate90)."""
    info = [(w, h, loc) for w, h, loc in info if w > 0 and h > 0]
    if not info:
        return None
    n_land = sum(1 for w, h, _ in info if w > h)
    n_port = len(info) - n_land
    has_local = any(loc for _, _, loc in info)
    land = has_local or (n_land > 0 and n_land >= n_port)
    same = [(w, h) for w, h, _ in info if (w > h) == land] or [(w, h) for w, h, _ in info]
    w, h = max(same, key=lambda d: d[0] * d[1])
    m = max(w, h)
    if m > SCREEN_WIDTH:
        w, h = round(w * SCREEN_WIDTH / m), round(h * SCREEN_WIDTH / m)
    return max(2, w // 2 * 2), max(2, h // 2 * 2)


def _reencode_concat(out_path, inputs, fps=SCREEN_FPS, size=None, bitrate=SCREEN_BITRATE):
    """把几个 mp4 (已解密) 按顺序解码 → 重新编码接成一个 (画面统一到 size 给的画布, 没给时用第一段的大小;
    每一段等比缩放居中留黑, 不同朝向也兼容; 只接画面). 每一段的时间戳接在上一段后面. 返回输出的 (宽, 高)."""
    lib, own = _startup()
    writer = None
    dw, dh = (size or (0, 0))
    try:
        base = 0                                    # 累计时间 (100ns): 这一段的时间戳要加上它
        for path in inputs:
            reader = c_void_p()
            attrs = _attributes(lib, 1)
            try:
                _set_u32(attrs, MF_SOURCE_READER_ENABLE_VIDEO_PROCESSING, 1)
                _check(lib.rw.MFCreateSourceReaderFromURL(ctypes.c_wchar_p(path), attrs, byref(reader)), "open")
            finally:
                _release(attrs)
            vcur = acur = None
            try:
                vidx, _aidx = _find_streams(reader)
                if vidx < 0:
                    continue
                vcur = _reader_type(lib, reader, MF_SOURCE_READER_FIRST_VIDEO_STREAM, MFMediaType_Video,
                                    MFVideoFormat_RGB32)
                fs = _get_u64(vcur, MF_MT_FRAME_SIZE)
                sw, sh = fs >> 32, fs & 0xFFFFFFFF
                try:
                    stride = ctypes.c_int(_get_u32(vcur, MF_MT_DEFAULT_STRIDE)).value
                except MFError:
                    stride = sw * 4
                if not stride:
                    stride = sw * 4
                has_audio = False       # 合并后的总览只接画面 (各段自己的文件里留着声音)
                if writer is None:
                    if not (dw and dh):                     # 没给画布: 用第一段的大小 (同以前)
                        dw, dh = min(sw, SCREEN_WIDTH) // 2 * 2, sh // 2 * 2
                    writer = _Writer(lib, out_path, dw, dh, fps, bitrate, audio=has_audio)
                # 竖屏画布里冒出横屏画面 (全是竖屏设备, 中途有横屏) → 把这一段的每帧转正 (MECHANICS 18.8)
                rotate = dw < dh and sw > sh
                rd = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_ulong, c_ulong, POINTER(c_ulong), POINTER(c_ulong),
                                        POINTER(ctypes.c_longlong), POINTER(c_void_p))(
                    ctypes.cast(reader, POINTER(POINTER(c_void_p)))[0][9])
                seg_end = base
                while True:
                    idx, flags, ts = c_ulong(), c_ulong(), ctypes.c_longlong()
                    sample = c_void_p()
                    hr = rd(reader, MF_SOURCE_READER_ALL_STREAMS, 0, byref(idx), byref(flags), byref(ts),
                            byref(sample))
                    if hr < 0:
                        break
                    try:
                        if flags.value & MF_SOURCE_READERF_ENDOFSTREAM and not sample:
                            break
                        if not sample:
                            continue
                        dur = ctypes.c_longlong()
                        try:
                            _com(sample, 37, byref(dur), argtypes=(POINTER(ctypes.c_longlong),), what="GetSampleDuration")
                        except MFError:
                            dur.value = 0
                        if idx.value != vidx:        # 只接画面 (声音在各段自己的文件里)
                            continue
                        stream = writer.stream
                        ptr, size, buf = _sample_bytes(sample)
                        try:
                            src = ctypes.string_at(ptr, size)
                            fw, fh, fstride = sw, sh, stride
                            if rotate:
                                src, fw, fh, fstride = _rotate90(src, sw, sh, stride)
                            frame = _fit(src, fw, fh, fstride, dw, dh)
                            data = ctypes.create_string_buffer(frame, len(frame))
                            writer.write_bytes(data, len(frame), base + ts.value, dur.value, stream=stream)
                        finally:
                            _com(buf, 4, what="Unlock")
                            _release(buf)
                        seg_end = max(seg_end, base + ts.value + dur.value)
                    finally:
                        _release(sample)
                base = seg_end
            finally:
                _release(vcur)
                _release(acur)
                _release(reader)
        return dw, dh
    finally:
        if writer is not None:
            writer.close()
        _shutdown(lib, own)


def _safe_cid(cid: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", str(cid))[:24] or "x"


def incoming_dir(data_dir: str, cid: str, kind: str = "screen") -> str:
    """从别的设备收到的某一路 (camera / screen) 录像片段先放这里 (按合约 id), 合并时一起用."""
    return os.path.join(data_dir, "recordings", "incoming", _safe_cid(cid), kind)


def merged_dir(data_dir: str, kind: str) -> str:
    """合并 / 同步好的最终录像 (camera / screen) 存这里; 界面只展示这里的 (不展示片段)."""
    return os.path.join(data_dir, "recordings", "merged", kind)


def merged_file(data_dir: str, cid: str, kind: str, src: str):
    """别的设备 (合约主人) 发来的已经合并好的最终录像: 直接存进 merged/<kind> (替换这个合约旧的), 不再合并."""
    d = merged_dir(data_dir, kind)
    os.makedirs(d, exist_ok=True)
    for n in os.listdir(d):                       # 同一个合约的旧结果先删掉 (重合并后会再发来)
        if cid_of(n) == _safe_cid(cid):
            _remove(os.path.join(d, n))
    stem = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(d, f"{stem}__{_safe_cid(cid)}{EXT}")
    try:
        os.replace(src, dst)
    except OSError:
        shutil.move(src, dst)
    return dst


def list_merged(data_dir: str, kind: str) -> list:
    """合并 / 同步好的最终录像 (camera / screen), 新的在前: [{path, name, size, start, partial}]. 界面只展示这些."""
    out = []
    try:
        names = os.listdir(merged_dir(data_dir, kind))
    except OSError:
        return out
    for n in names:
        if not n.lower().endswith(EXT):
            continue
        p = os.path.join(merged_dir(data_dir, kind), n)
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        try:
            start = datetime.strptime(n[:15], "%Y%m%d-%H%M%S")
        except ValueError:
            start = None
        out.append({"path": p, "name": n, "size": size, "start": start, "partial": False})
    out.sort(key=lambda f: (f["start"] or datetime.min, f["name"]), reverse=True)
    return out


def delete_merged(data_dir: str, cid: str, kind: str) -> bool:
    """删掉某个合约某一路合并好的最终录像 (删除要在设备间同步, MECHANICS 18.8)."""
    removed = False
    try:
        for n in os.listdir(merged_dir(data_dir, kind)):
            if cid_of(n) == _safe_cid(cid):
                _remove(os.path.join(merged_dir(data_dir, kind), n))
                removed = True
    except OSError:
        pass
    return removed


def merge(data_dir: str, cid: str, kind: str = "screen", extra=()) -> str:
    """MECHANICS 18.8: 把这个合约某一路 (camera / screen) 的录像片段 (本机的 + 从别的设备收到的 + extra)
    按时间接成一个视频, 加密存进 merged/<kind>. 返回合并后的路径 (没有片段时 "")."""
    inc = incoming_dir(data_dir, cid, kind)
    got = []
    try:
        for n in os.listdir(inc):
            if n.lower().endswith(EXT):
                got.append(os.path.join(inc, n))
    except OSError:
        pass
    segs = [dict(s, local=True) for s in files_of(data_dir, kind, cid)] + [
        {"path": p, "name": os.path.basename(p), "start": None, "local": False} for p in list(extra) + got]
    segs = [s for s in segs if not s.get("partial")]
    if not segs:
        return ""

    def when(s):
        return s["start"] or datetime.strptime(os.path.basename(s["name"])[:15], "%Y%m%d-%H%M%S")
    try:
        segs.sort(key=when)
    except ValueError:
        segs.sort(key=lambda s: s["name"])
    work = os.path.join(work_dir(data_dir, "merged"), cid)
    os.makedirs(work, exist_ok=True)
    plain = []          # [(解密后的 mp4 路径, 是不是本机录的)]
    try:
        for i, s in enumerate(segs):
            p = s["path"]
            if p.lower().endswith(EXT):
                out = os.path.join(work, f"{i:04d}.mp4")
                decrypt_file(p, out)
                plain.append((out, s.get("local", False)))
            else:
                plain.append((p, s.get("local", False)))
        raw = os.path.join(work, "merged.mp4")
        # 画布的朝向 / 大小 (MECHANICS 18.8): 有横屏设备 (本机电脑录过, 或横屏片段占多数) 就按横屏, 否则按竖屏;
        # 横屏画布里的竖屏内容左右留黑, 竖屏画布里冒出的横屏画面转正
        info = []
        for p, loc in plain:
            try:
                w, h = _dims(p)
                info.append((w, h, loc))
            except Exception:       # noqa: BLE001
                pass
        # 目光 (摄像头) 的合并结果按目光的码率压 (要同步给所有设备, 不能太大)
        _reencode_concat(raw, [p for p, _ in plain], size=_canvas(info),
                         bitrate=CAMERA_BITRATE if kind == "camera" else SCREEN_BITRATE)
        d = merged_dir(data_dir, kind)
        os.makedirs(d, exist_ok=True)
        for n in os.listdir(d):                   # 这个合约这一路的旧结果先删 (本次重新合并盖掉)
            if cid_of(n) == _safe_cid(cid):
                _remove(os.path.join(d, n))
        stem = when(segs[0]).strftime("%Y%m%d-%H%M%S")
        dst = os.path.join(d, f"{stem}__{_safe_cid(cid)}{EXT}")
        encrypt_file(raw, dst)
        return dst
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _dims(path: str):
    """mp4 画面的宽高."""
    lib, own = _startup()
    reader = c_void_p()
    try:
        _check(lib.rw.MFCreateSourceReaderFromURL(ctypes.c_wchar_p(path), None, byref(reader)), "open")
        t = c_void_p()
        _com(reader, 6, MF_SOURCE_READER_FIRST_VIDEO_STREAM, byref(t),
             argtypes=(c_ulong, POINTER(c_void_p)), what="GetCurrentMediaType")
        try:
            fs = _get_u64(t, MF_MT_FRAME_SIZE)
            return fs >> 32, fs & 0xFFFFFFFF
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


def _selftest(data_dir=None, seconds=3, audio=(True, True)):
    """录几秒屏幕 (带声音), 看加密的文件写出来了没有, 解密后能不能读出画面和声音两路 (要有桌面)."""
    d = data_dir or tempfile.mkdtemp()
    # 加密: 任意位置开始解密都对得上
    data = os.urandom(5000)
    nonce = b"12345678"
    enc = rec_xor(nonce, 0, data)
    assert enc != data and rec_xor(nonce, 0, enc) == data and rec_xor(nonce, 37, enc[37:999]) == data[37:999]
    # 已知答案 (NIST SP 800-38A F.5.5 的密钥换成 REC_KEY 时没有现成的答案, 这里只核对 CTR 的计数器拼法)
    ks = rec_xor(nonce, 0, bytes(32))
    assert ks[16:] == _Aes.ecb(nonce + (1).to_bytes(8, "big"))
    r = Recorder(d, "screen", audio=lambda: audio)
    r.start()
    t0 = time.time()
    while r.state == "starting" and time.time() - t0 < 10:
        time.sleep(0.1)
    assert r.state == "running", (r.state, r.error)
    time.sleep(seconds)
    note = r.error
    r.stop()
    r.wait_sealed()
    assert r.state == "stopped", (r.state, r.error)
    files = list_files(d, "screen")
    assert len(files) == 1 and not files[0]["partial"] and files[0]["name"].endswith(EXT) \
        and files[0]["size"] > 1000, files
    assert not os.listdir(work_dir(d, "screen")), "临时文件夹里的 mp4 加密后删掉了"
    with open(files[0]["path"], "rb") as f:
        head = f.read(64)
    assert head.startswith(MAGIC) and b"ftyp" not in head, "存下来的不是明文 mp4"
    out = os.path.join(d, "check.mp4")
    decrypt_file(files[0]["path"], out)
    with open(out, "rb") as f:
        assert f.read(12)[4:8] == b"ftyp", "解密后是 mp4"
    n = _streams(out)
    assert n == (2 if any(audio) else 1), (n, note)
    # 合并 (刻痕): 给这一段打上合约 id 再录一段, 接成一个 (总览只有画面)
    merged = ""
    r2 = Recorder(d, "screen", audio=lambda: audio, tag="x1")
    r2.start()
    t0 = time.time()
    while r2.state == "starting" and time.time() - t0 < 10:
        time.sleep(0.1)
    time.sleep(2)
    r2.stop()
    r2.wait_sealed()
    assert len(files_of(d, "screen", "x1")) == 1 and cid_of(files_of(d, "screen", "x1")[0]["name"]) == "x1"
    mp = merge(d, "x1")
    mo = os.path.join(d, "merged.mp4")
    decrypt_file(mp, mo)
    assert _streams(mo) == 1 and os.path.getsize(mp) > 1000, (mp, _streams(mo))
    merged = f", merged {_dims(mo)}"
    return (f"recorder selftest OK ({r.frames} frames, {files[0]['size']} bytes, streams {n}, "
            f"cameras: {camera_count()}{merged}{', audio: ' + note if note else ''})"), d


if __name__ == "__main__":
    print(_selftest()[0])
