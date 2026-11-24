"""桌面沟通页的单张拍照适配。

优先复用可选的 OpenCV；安装了 ffmpeg 时也支持 Windows DirectShow。所有调用都在后台线程执行。
"""

import os
import shutil
import subprocess
import ctypes
from ctypes import byref, c_int, c_void_p, c_ulong

import tempstore


def _media_foundation_photo() -> bytes:
    """通过 OIT 已有的 Media Foundation 封装抓取一帧，不依赖外部相机包。"""
    if os.name != "nt":
        return b""
    import avatar
    from recorder import recorder_media as mf
    lib, own = mf._startup()
    source = reader = attrs = current = None
    arr = None
    count = 0
    bitmap = c_void_p()
    try:
        arr, count = mf._camera_activates(lib)
        if not count:
            return b""
        source = c_void_p()
        mf._com(arr[0], 33, ctypes.byref(mf.IID_IMFMediaSource), ctypes.byref(source),
                argtypes=(ctypes.POINTER(mf.GUID), ctypes.POINTER(c_void_p)), what="ActivateObject")
        attrs = mf._attributes(lib, 1)
        mf._set_u32(attrs, mf.MF_SOURCE_READER_ENABLE_VIDEO_PROCESSING, 1)
        reader = c_void_p()
        mf._check(lib.rw.MFCreateSourceReaderFromMediaSource(source, attrs, ctypes.byref(reader)), "SourceReader")
        mf._release(attrs)
        attrs = None
        native = mf._pick_camera_type(reader)
        if native:
            try:
                mf._com(reader, 7, mf.MF_SOURCE_READER_FIRST_VIDEO_STREAM, None, native,
                        argtypes=(c_ulong, c_void_p, c_void_p), what="SetCurrentMediaType(native)")
            except mf.MFError:
                pass
            finally:
                mf._release(native)
        wanted = mf._media_type(lib)
        try:
            mf._set_guid(wanted, mf.MF_MT_MAJOR_TYPE, mf.MFMediaType_Video)
            mf._set_guid(wanted, mf.MF_MT_SUBTYPE, mf.MFVideoFormat_RGB32)
            mf._com(reader, 7, mf.MF_SOURCE_READER_FIRST_VIDEO_STREAM, None, wanted,
                    argtypes=(c_ulong, c_void_p, c_void_p), what="SetCurrentMediaType(RGB32)")
        finally:
            mf._release(wanted)
        current = c_void_p()
        mf._com(reader, 6, mf.MF_SOURCE_READER_FIRST_VIDEO_STREAM, ctypes.byref(current),
                argtypes=(c_ulong, ctypes.POINTER(c_void_p)), what="GetCurrentMediaType")
        size = mf._get_u64(current, mf.MF_MT_FRAME_SIZE)
        width, height = int(size >> 32), int(size & 0xFFFFFFFF)
        read = ctypes.WINFUNCTYPE(mf.HRESULT, c_void_p, c_ulong, c_ulong, ctypes.POINTER(c_ulong),
                                  ctypes.POINTER(c_ulong), ctypes.POINTER(ctypes.c_longlong),
                                  ctypes.POINTER(c_void_p))(ctypes.cast(reader, ctypes.POINTER(ctypes.POINTER(c_void_p)))[0][9])
        sample = c_void_p()
        for _ in range(12):
            stream, flags, timestamp = c_ulong(), c_ulong(), ctypes.c_longlong()
            mf._check(read(reader, mf.MF_SOURCE_READER_FIRST_VIDEO_STREAM, 0, byref(stream), byref(flags),
                           byref(timestamp), byref(sample)), "ReadSample")
            if sample:
                break
        if not sample:
            return b""
        data, size, buffer = mf._sample_bytes(sample)
        try:
            g = avatar._gdi()
            frame = bytearray(ctypes.string_at(data, min(size, width * height * 4)))
            if len(frame) != width * height * 4:
                return b""
            frame[3::4] = b"\xff" * (width * height)
            pixels = (ctypes.c_ubyte * len(frame)).from_buffer(frame)
            g.GdipCreateBitmapFromScan0.argtypes = [c_int, c_int, c_int, c_int, c_void_p, ctypes.POINTER(c_void_p)]
            g.GdipCreateBitmapFromScan0.restype = c_ulong
            if g.GdipCreateBitmapFromScan0(width, height, width * 4, avatar._FORMAT_ARGB,
                                           ctypes.cast(pixels, c_void_p), byref(bitmap)) != 0:
                return b""
            fd, path = tempstore.mkstemp("misc", prefix="camera-shot-", suffix=".png")
            os.close(fd)
            try:
                avatar._save_png(g, bitmap, path)
                with open(path, "rb") as stream_file:
                    return stream_file.read()
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
        finally:
            mf._com(buffer, 4, what="Unlock camera frame")
            mf._release(buffer)
            mf._release(sample)
    except Exception:
        return b""
    finally:
        if bitmap:
            try:
                avatar._gdi().GdipDisposeImage(bitmap)
            except Exception:
                pass
        mf._release(current)
        mf._release(reader)
        mf._release(attrs)
        if source:
            try:
                mf._com(source, 12, what="Shutdown camera")
            except Exception:
                pass
            mf._release(source)
        if arr:
            mf._free_activates(lib, arr, count)
        mf._shutdown(lib, own)


def _opencv():
    try:
        import cv2
        return cv2
    except Exception:
        return None


def capture(camera: str = "") -> bytes:
    """打开本机默认摄像头抓取一帧，返回 PNG 字节；不可用时返回空字节。"""
    native = _media_foundation_photo()
    if native:
        return native
    cv2 = _opencv()
    if cv2 is not None:
        try:
            cap = cv2.VideoCapture(0, getattr(cv2, "CAP_DSHOW", 0))
            try:
                if cap.isOpened():
                    # 摄像头刚打开时常是黑帧，给驱动几次机会完成曝光。
                    frame = None
                    for _ in range(8):
                        ok, candidate = cap.read()
                        if ok and candidate is not None and getattr(candidate, "size", 0):
                            frame = candidate
                    if frame is not None:
                        ok, encoded = cv2.imencode(".png", frame)
                        if ok:
                            return bytes(encoded)
            finally:
                cap.release()
        except Exception:
            pass

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return b""
    try:
        from recorder import camera_devices
        devices = camera_devices()
    except Exception:
        devices = []
    name = str(camera or (devices[0].get("name") if devices else "") or "")
    if not name:
        return b""
    fd, out = tempstore.mkstemp("misc", prefix="camera-shot-", suffix=".png")
    os.close(fd)
    try:
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "dshow", "-i", f"video={name}",
                        "-frames:v", "1", "-y", out], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=20, check=False)
        try:
            with open(out, "rb") as stream:
                return stream.read()
        except OSError:
            return b""
    finally:
        try:
            os.remove(out)
        except OSError:
            pass
