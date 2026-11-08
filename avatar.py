# -*- coding: utf-8 -*-
"""
头像: 从资源管理器选一张图, 截取一个圆形区域 (沟通页 / 设置 → 沟通)
──────────────────────────────────────────────────────────────────
没有 Pillow, 也不想为这个多装依赖: 读图 / 缩放 / 圆形裁剪 / 存 PNG 都交给 Windows 自带的 GDI+ (gdiplus.dll, ctypes 直接调),
能打开 png / jpg / bmp / gif / tiff. 界面在 pages/avatar_dialog.py (拖动 + 缩放, 圆框里的就是头像).

    python avatar.py     自检 (只在 Windows 上, 用临时文件夹)
"""

import ctypes
import os
import sys
import tempfile
import zlib
from ctypes import byref, c_float, c_int, c_uint, c_ulong, c_void_p, wintypes

_PNG_CLSID = (0x557CF406, 0x1A04, 0x11D3, bytes([0x9A, 0x73, 0x00, 0x00, 0xF8, 0x1E, 0xF3, 0x2E]))
_FORMAT_ARGB = 0x26200A            # PixelFormat32bppARGB
_state = {"gdi": None, "token": None}


class _Guid(ctypes.Structure):
    _fields_ = [("d1", c_uint), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]


class _StartupInput(ctypes.Structure):
    _fields_ = [("version", c_uint), ("callback", c_void_p), ("no_thread", wintypes.BOOL), ("no_codecs", wintypes.BOOL)]


def available() -> bool:
    return sys.platform == "win32"


def _gdi():
    if _state["gdi"] is None:
        if not available():
            raise OSError("GDI+ only on Windows")
        g = ctypes.WinDLL("gdiplus")
        token = c_ulong()
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
    """完整 (没有裁成圆形) 的那份头像: 和圆形的那份放在一起, 叫 xxx.full.png."""
    return (master[:-4] if master.lower().endswith(".png") else master) + ".full.png"


def sized(master: str, px: int) -> str:
    """头像 (圆形 PNG) 缩到 px × px 的缓存文件, 主文件更新了就重画; 返回路径."""
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
    print(_selftest())
