# -*- coding: utf-8 -*-
"""
头像: 从资源管理器选一张图, 截取一个正方形区域 (沟通页 / 设置 → 沟通)
──────────────────────────────────────────────────────────────────
没有 Pillow, 也不想为这个多装依赖: 读图 / 缩放 / 方形裁剪 / 存 PNG 都交给 Windows 自带的 GDI+ (gdiplus.dll, ctypes 直接调),
能打开 png / jpg / bmp / gif / tiff. 界面在 pages/avatar_dialog.py (拖动 + 缩放, 方框里的就是头像).

    python avatar.py     自检 (只在 Windows 上, 用临时文件夹)
"""

import ctypes
import os
import sys
import tempfile
import time
import zlib
from ctypes import byref, c_float, c_int, c_size_t, c_uint, c_ulong, c_void_p, wintypes

_PNG_CLSID = (0x557CF406, 0x1A04, 0x11D3, bytes([0x9A, 0x73, 0x00, 0x00, 0xF8, 0x1E, 0xF3, 0x2E]))
_FORMAT_ARGB = 0x26200A            # PixelFormat32bppARGB
_state = {"gdi": None, "token": None}


class _Guid(ctypes.Structure):
    _fields_ = [("d1", c_uint), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]


class _StartupInput(ctypes.Structure):
    _fields_ = [("version", c_uint), ("callback", c_void_p), ("no_thread", wintypes.BOOL), ("no_codecs", wintypes.BOOL)]


def available() -> bool:
    return sys.platform == "win32"


def _open_clipboard(user) -> bool:
    """剪贴板短暂被截图工具或历史记录占用时稍等重试."""
    for attempt in range(3):
        if user.OpenClipboard(None):
            return True
        if attempt < 2:
            time.sleep(0.015)
    return False


def _save_png(g, image, out: str):
    clsid = _Guid(_PNG_CLSID[0], _PNG_CLSID[1], _PNG_CLSID[2], (ctypes.c_ubyte * 8)(*_PNG_CLSID[3]))
    _ok(g.GdipSaveImageToFile(image, ctypes.c_wchar_p(os.path.abspath(out)), byref(clsid), None), "clipboard save")


def clipboard_paths() -> list:
    """剪贴板复制的文件列表 (CF_HDROP)."""
    if not available():
        return []
    user = ctypes.WinDLL("user32", use_last_error=True)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    user.OpenClipboard.argtypes, user.OpenClipboard.restype = [wintypes.HWND], wintypes.BOOL
    user.GetClipboardData.argtypes, user.GetClipboardData.restype = [wintypes.UINT], wintypes.HANDLE
    user.CloseClipboard.argtypes, user.CloseClipboard.restype = [], wintypes.BOOL
    shell.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell.DragQueryFileW.restype = wintypes.UINT
    if not _open_clipboard(user):
        return []
    try:
        drop = user.GetClipboardData(15)
        if not drop:
            return []
        count = shell.DragQueryFileW(drop, 0xFFFFFFFF, None, 0)
        out = []
        for index in range(count):
            size = shell.DragQueryFileW(drop, index, None, 0) + 1
            path = ctypes.create_unicode_buffer(size)
            shell.DragQueryFileW(drop, index, path, size)
            out.append(path.value)
        return out
    finally:
        user.CloseClipboard()


def clipboard_image(out: str) -> str:
    """把 Windows 剪贴板 PNG / DIBV5 / DIB / HBITMAP 保存为 PNG."""
    if not available():
        return ""
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.OpenClipboard.argtypes, user.OpenClipboard.restype = [wintypes.HWND], wintypes.BOOL
    user.GetClipboardData.argtypes, user.GetClipboardData.restype = [wintypes.UINT], wintypes.HANDLE
    user.RegisterClipboardFormatW.argtypes, user.RegisterClipboardFormatW.restype = [wintypes.LPCWSTR], wintypes.UINT
    user.CloseClipboard.argtypes, user.CloseClipboard.restype = [], wintypes.BOOL
    kernel.GlobalLock.argtypes, kernel.GlobalLock.restype = [wintypes.HGLOBAL], c_void_p
    kernel.GlobalUnlock.argtypes, kernel.GlobalUnlock.restype = [wintypes.HGLOBAL], wintypes.BOOL
    kernel.GlobalSize.argtypes, kernel.GlobalSize.restype = [wintypes.HGLOBAL], c_size_t
    if not _open_clipboard(user):
        return ""
    try:
        png_fmt = user.RegisterClipboardFormatW("PNG")
        if png_fmt:
            handle = user.GetClipboardData(png_fmt)
            if handle:
                address = kernel.GlobalLock(handle)
                if address:
                    try:
                        size = int(kernel.GlobalSize(handle))
                        if size >= 8:
                            raw = ctypes.string_at(address, size)
                            if raw.startswith(b"\x89PNG\r\n\x1a\n"):
                                with open(os.path.abspath(out), "wb") as stream:
                                    stream.write(raw)
                                return out
                    finally:
                        kernel.GlobalUnlock(handle)
        g = _gdi()
        for fmt in (17, 8):
            handle = user.GetClipboardData(fmt)
            if not handle:
                continue
            address = kernel.GlobalLock(handle)
            if not address:
                continue
            image = c_void_p()
            try:
                size = int(kernel.GlobalSize(handle))
                header = int(ctypes.c_uint.from_address(address).value)
                if header < 40 or header > size:
                    continue
                depth = int(ctypes.c_ushort.from_address(address + 14).value)
                compression = int(ctypes.c_uint.from_address(address + 16).value)
                colors = int(ctypes.c_uint.from_address(address + 32).value) or (1 << depth if depth <= 8 else 0)
                offset = header + (12 if compression == 3 and header == 40 else 16 if compression == 6 and header == 40 else 0) + colors * 4
                if offset >= size:
                    continue
                status = g.GdipCreateBitmapFromGdiDib(c_void_p(address), c_void_p(address + offset), byref(image))
                if status != 0:
                    continue
                _save_png(g, image, out)
                return out
            finally:
                if image:
                    g.GdipDisposeImage(image)
                kernel.GlobalUnlock(handle)
        hbmp = user.GetClipboardData(2)
        if hbmp:
            image = c_void_p()
            try:
                if g.GdipCreateBitmapFromHBITMAP(hbmp, None, byref(image)) == 0 and image:
                    _save_png(g, image, out)
                    return out
            finally:
                if image:
                    g.GdipDisposeImage(image)
        return ""
    finally:
        user.CloseClipboard()


def _gdi():
    if _state["gdi"] is None:
        if not available():
            raise OSError("GDI+ only on Windows")
        g = ctypes.WinDLL("gdiplus")
        g.GdiplusStartup.argtypes = [ctypes.POINTER(c_size_t), ctypes.POINTER(_StartupInput), c_void_p]
        g.GdiplusStartup.restype = c_ulong
        g.GdipCreateBitmapFromGdiDib.argtypes = [c_void_p, c_void_p, ctypes.POINTER(c_void_p)]
        g.GdipCreateBitmapFromGdiDib.restype = c_ulong
        g.GdipCreateBitmapFromHBITMAP.argtypes = [wintypes.HBITMAP, wintypes.HANDLE, ctypes.POINTER(c_void_p)]
        g.GdipCreateBitmapFromHBITMAP.restype = c_ulong
        g.GdipSaveImageToFile.argtypes = [c_void_p, wintypes.LPCWSTR, ctypes.POINTER(_Guid), c_void_p]
        g.GdipSaveImageToFile.restype = c_ulong
        g.GdipDisposeImage.argtypes = [c_void_p]
        g.GdipDisposeImage.restype = c_ulong
        token = c_size_t()
        inp = _StartupInput(1, None, False, False)
        if g.GdiplusStartup(byref(token), byref(inp), None) != 0:
            raise OSError("GdiplusStartup failed")
        _state["gdi"], _state["token"] = g, token
    return _state["gdi"]


def _ok(status, what):
    if status != 0:
        raise OSError(f"GDI+ {what} failed ({status})")


def _load(path: str):
    g = _gdi()
    img = c_void_p()
    _ok(g.GdipCreateBitmapFromFile(ctypes.c_wchar_p(os.path.abspath(path)), byref(img)), "open")
    return img


def image_size(path: str):
    g = _gdi()
    img = _load(path)
    try:
        w, h = c_uint(), c_uint()
        g.GdipGetImageWidth(img, byref(w))
        g.GdipGetImageHeight(img, byref(h))
        return int(w.value), int(h.value)
    finally:
        g.GdipDisposeImage(img)


def render(src: str, out: str, cx: float, cy: float, side: float, px: int, circle: bool = True):
    """把 src 里以 (cx, cy) 为中心、边长 side (源图像素) 的正方形缩成 px × px 的 PNG 存到 out;
    circle=True 时圆外透明. 超出图片的部分会被挪回图片里."""
    g = _gdi()
    img = _load(src)
    dst = c_void_p()
    gr = c_void_p()
    path = c_void_p()
    try:
        w, h = c_uint(), c_uint()
        g.GdipGetImageWidth(img, byref(w))
        g.GdipGetImageHeight(img, byref(h))
        side = max(1.0, min(float(side), float(w.value), float(h.value)))
        x0 = min(max(cx - side / 2, 0.0), w.value - side)
        y0 = min(max(cy - side / 2, 0.0), h.value - side)
        _ok(g.GdipCreateBitmapFromScan0(px, px, 0, _FORMAT_ARGB, None, byref(dst)), "create")
        _ok(g.GdipGetImageGraphicsContext(dst, byref(gr)), "context")
        g.GdipSetInterpolationMode(gr, 7)          # HighQualityBicubic
        g.GdipSetPixelOffsetMode(gr, 2)            # HighQuality
        g.GdipSetSmoothingMode(gr, 4)              # AntiAlias
        if circle:
            _ok(g.GdipCreatePath(0, byref(path)), "path")
            _ok(g.GdipAddPathEllipseI(path, 0, 0, px, px), "ellipse")
            _ok(g.GdipSetClipPath(gr, path, 0), "clip")
        # 源图边缘不要渗色: ImageAttributes 的换行模式用 TileFlipXY 太绕, 这里源矩形一定在图内, 直接画
        _ok(g.GdipDrawImageRectRect(gr, img, c_float(0), c_float(0), c_float(px), c_float(px),
                                    c_float(x0), c_float(y0), c_float(side), c_float(side), 2, None, None, None), "draw")
        clsid = _Guid(_PNG_CLSID[0], _PNG_CLSID[1], _PNG_CLSID[2], (ctypes.c_ubyte * 8)(*_PNG_CLSID[3]))
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        _ok(g.GdipSaveImageToFile(dst, ctypes.c_wchar_p(os.path.abspath(out)), byref(clsid), None), "save")
    finally:
        if path:
            g.GdipDeletePath(path)
        if gr:
            g.GdipDeleteGraphics(gr)
        if dst:
            g.GdipDisposeImage(dst)
        g.GdipDisposeImage(img)


def pixel(path: str, x: int, y: int) -> int:
    """某个像素的 ARGB (自检用)."""
    g = _gdi()
    img = _load(path)
    try:
        c = c_uint()
        _ok(g.GdipBitmapGetPixel(img, x, y, byref(c)), "pixel")
        return int(c.value)
    finally:
        g.GdipDisposeImage(img)


def full_path(master: str) -> str:
    """完整 (没有裁剪) 的那份头像: 和方形头像放在一起, 叫 xxx.full.png."""
    return (master[:-4] if master.lower().endswith(".png") else master) + ".full.png"


def render_full(src: str, out: str, max_side: int = 0, max_w: int = 0):
    """整张图 (不裁) 存成 PNG: 最长边超过 max_side 或宽超过 max_w 时等比缩小, 0 = 保持原分辨率."""
    g = _gdi()
    img = _load(src)
    dst = c_void_p()
    gr = c_void_p()
    try:
        w, h = c_uint(), c_uint()
        g.GdipGetImageWidth(img, byref(w))
        g.GdipGetImageHeight(img, byref(h))
        w0, h0 = int(w.value), int(h.value)
        if max_w:
            scale = min(1.0, max_w / max(1, w0))
        elif max_side:
            scale = min(1.0, max_side / max(1, max(w0, h0)))
        else:
            scale = 1.0
        tw, th = max(1, int(round(w0 * scale))), max(1, int(round(h0 * scale)))
        _ok(g.GdipCreateBitmapFromScan0(tw, th, 0, _FORMAT_ARGB, None, byref(dst)), "create")
        _ok(g.GdipGetImageGraphicsContext(dst, byref(gr)), "context")
        g.GdipSetInterpolationMode(gr, 7)
        g.GdipSetPixelOffsetMode(gr, 2)
        g.GdipSetSmoothingMode(gr, 4)
        _ok(g.GdipDrawImageRectRect(gr, img, c_float(0), c_float(0), c_float(tw), c_float(th),
                                    c_float(0), c_float(0), c_float(w0), c_float(h0), 2, None, None, None), "draw")
        clsid = _Guid(_PNG_CLSID[0], _PNG_CLSID[1], _PNG_CLSID[2], (ctypes.c_ubyte * 8)(*_PNG_CLSID[3]))
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        _ok(g.GdipSaveImageToFile(dst, ctypes.c_wchar_p(os.path.abspath(out)), byref(clsid), None), "save")
    finally:
        if gr:
            g.GdipDeleteGraphics(gr)
        if dst:
            g.GdipDisposeImage(dst)
        g.GdipDisposeImage(img)


def fit(master: str, max_side: int) -> str:
    """点开头像时用的完整图: 等比缩到最长边 max_side 以内 (不裁), 缓存成 PNG. 原图更新了重画."""
    out = f"{master}.fit{max_side}.png"
    try:
        if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(master):
            return out
    except OSError:
        pass
    render_full(master, out, max_side)
    return out


def fit_width(master: str, max_w: int) -> str:
    """长图阅读用: 只按最大宽度 max_w 等比缩放 (不压扁高度), 缓存成 PNG. 原图更新了重画."""
    out = f"{master}.fitw{max_w}.png"
    try:
        if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(master):
            return out
    except OSError:
        pass
    render_full(master, out, max_w=max_w)
    return out


def sized(master: str, px: int) -> str:
    """头像 (方形 PNG) 缩到 px × px 的缓存文件, 主文件更新了就重画; 返回路径."""
    out = f"{master}.{px}.png"
    try:
        if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(master):
            return out
    except OSError:
        pass
    w, h = image_size(master)
    render(master, out, w / 2, h / 2, min(w, h), px, circle=False)
    return out


def _selftest():
    if not available():
        return "avatar selftest skipped (not Windows)"

    def png(w, h, rgb):
        raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

        def chunk(t, d):
            c = t + d
            return len(d).to_bytes(4, "big") + c + zlib.crc32(c).to_bytes(4, "big")
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes([8, 2, 0, 0, 0]))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

    td = tempfile.mkdtemp()
    src = os.path.join(td, "s.png")
    with open(src, "wb") as f:
        f.write(png(100, 60, (255, 0, 0)))
    assert image_size(src) == (100, 60)
    out = os.path.join(td, "a", "c.png")
    render(src, out, 10, 10, 200, 64, circle=True)         # 中心和边长都越界: 会被挪回图内
    assert image_size(out) == (64, 64)
    assert pixel(out, 1, 1) >> 24 == 0, "圆外应当透明"
    assert pixel(out, 32, 32) == 0xFFFF0000, hex(pixel(out, 32, 32))
    sq = os.path.join(td, "sq.png")
    render(src, sq, 50, 30, 30, 32, circle=False)
    assert pixel(sq, 0, 0) >> 24 == 255
    small = sized(out, 20)
    assert image_size(small) == (20, 20) and sized(out, 20) == small
    try:
        image_size(os.path.join(td, "nope.png"))
        raise AssertionError
    except OSError:
        pass
    return "avatar selftest OK"


if __name__ == "__main__":
    import tempstore
    tempstore.autosession()      # 自检的临时文件进 %TEMP%\OIT 的会话目录, 退出即删 (tempstore.py)
    print(_selftest())
