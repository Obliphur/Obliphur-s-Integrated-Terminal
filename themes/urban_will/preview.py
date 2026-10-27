"""都市之意主题的试作预览 (开发用, 不随程序发布).

用隔离的 USERPROFILE 起一个只画界面、不联网 / 不碰防火墙 / 不改系统设置的 OIT,
把各页截成图片, 方便看主题效果. 在项目根目录运行:
    python themes/urban_will/preview.py
"""
import ctypes
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import zlib
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
HOME = os.path.join(tempfile.gettempdir(), "oit_urban_preview")
if os.path.isdir(HOME):
    shutil.rmtree(HOME, ignore_errors=True)
DATA = os.path.join(HOME, "AppData", "LocalLow", "Obliphur's Integrated Terminal")
OUT = os.environ.get("OIT_PREVIEW_OUT") or os.path.join(HERE, "screenshots")
THEME = os.environ.get("OIT_PREVIEW_THEME", "urban_will")
os.makedirs(DATA, exist_ok=True)
os.makedirs(OUT, exist_ok=True)
os.environ["USERPROFILE"] = HOME
os.environ["HOMEDRIVE"], os.environ["HOMEPATH"] = os.path.splitdrive(HOME)


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


_write(os.path.join(DATA, "profile.json"), {"username": "预览"})
_write(os.path.join(DATA, "app_settings.json"), {
    "effective": {
        "THEME": THEME,
        "LANGUAGE": "zh",
        "FONT_SCALE": 1.0,
        "UPDATE_CHECK": 0,
        "UPDATE_AUTO": 0,
    },
    "since": {}, "changed": {}, "free_next": [], "setup_until": None, "pending": {},
})

# 指令页预览: 一条进行中的 I 和一条已经给出的蓝色代码
_now = datetime.now().replace(microsecond=0)
_write(os.path.join(DATA, "prescript.json"), {
    "current": {
        "id": "preview", "type": "I", "body": "接下来的1h专注50min.",
        "accepted": _now.isoformat(timespec="seconds"),
        "expires": (_now + timedelta(minutes=50)).isoformat(timespec="seconds"),
        "pool": "focus", "n": None, "t": 1.0, "focus_sec": 720.0,
        "break_sec": 0.0, "stats_since": None,
    },
    "last_draw": (_now - timedelta(minutes=10)).isoformat(timespec="seconds"),
    "last_pool": "focus", "last_type": "I", "v_reset": False,
    "pending": [],
    "granted": [{"kind": "complete", "code": "1h*3min", "t": 1.0,
                 "seconds": 180, "at": _now.isoformat(timespec="seconds")}],
    "daily": {"date": _now.strftime("%Y-%m-%d"), "n_ii": 7, "n_iv": 6,
              "completed_t": 1.0, "bonus": False, "iv_done": False},
    "history": [],
})

import tkinter as tk

import config
config.UI_SCALE = 1.0
import app as app_mod

# ---- 预览模式: 把这些启动副作用换成空操作 ----
NOOP = ("_check_firewall", "_appban_sync", "_check_autostart", "_net_tick",
        "_schedule_apply_check", "_pump_sync", "_start_midnight_timer",
        "_schedule_hourly", "_schedule_window_guard", "_prestart_tick",
        "_window_focus_tick", "_schedule_app_rules")
for name in NOOP:
    setattr(app_mod.BreakTimerApp, name, lambda self, *a, **k: None)
app_mod.recorder.seal_leftovers = lambda *a, **k: None
app_mod.devsync.SyncService.start = lambda self, *a, **k: None

# 预览用 1280x860 的设计基准尺寸; 必须在建界面之前就定好, 否则页面按 788 高排版.
_orig_geometry = app_mod.BreakTimerApp._apply_geometry


def _preview_geometry(self):
    _orig_geometry(self)
    self.root.geometry("1280x860+0+0")


app_mod.BreakTimerApp._apply_geometry = _preview_geometry


# ---- 截图 (标准库: GDI 取屏幕, zlib 写 PNG) ----
class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


def _png_chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def capture(hwnd, path):
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    rect = RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return False
    src = user32.GetDC(hwnd)
    mem = gdi32.CreateCompatibleDC(src)
    bmp = gdi32.CreateCompatibleBitmap(src, w, h)
    old = gdi32.SelectObject(mem, bmp)
    gdi32.BitBlt(mem, 0, 0, w, h, src, 0, 0, 0x00CC0020)
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, src)
    raw = bytearray()
    stride = w * 4
    px = buf.raw
    for y in range(h):
        row = px[y * stride:(y + 1) * stride]
        rgb = bytearray(w * 3)
        rgb[0::3] = row[2::4]
        rgb[1::3] = row[1::4]
        rgb[2::3] = row[0::4]
        raw += b"\x00" + bytes(rgb)
    png = (b"\x89PNG\r\n\x1a\n"
           + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
           + _png_chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    return True


root = tk.Tk()
app = app_mod.BreakTimerApp(root)
root.geometry("1280x860+0+0")
root.attributes("-topmost", True)
root.update_idletasks()
root.update()
client = root.winfo_id()


def shot(name, setup=None):
    if setup:
        setup()
    try:
        if root.state() == "iconic":
            root.deiconify()
        root.lift()
        root.attributes("-topmost", True)
    except Exception:
        pass
    root.update_idletasks()
    root.update()
    time.sleep(0.08)
    if root.state() == "iconic":
        root.deiconify()
    root.update()
    ok = capture(client, os.path.join(OUT, f"urban_{name}.png"))
    print(name, "ok" if ok else "FAILED", flush=True)


def show(page):
    app.show_page(page)


def settings_theme():
    app.show_page("settings")
    page = app.pages["settings"]
    page.show_tab("basic")
    row = page._rows.get("THEME")
    if row:
        page.areas["basic"].scroll_to(row["frame"])
    root.update_idletasks()
    root.update()


def _walk(w):
    yield w
    for c in w.winfo_children():
        yield from _walk(c)


def focus_theme_combo():
    settings_theme()
    row = app.pages["settings"]._rows.get("THEME")
    if row:
        # 预览时窗口不一定拿得到前台焦点; 直接让灯圈画出来, 效果和真焦点一样.
        app.focus_glow.show(row["widget"])
        root.update()


def focus_log_entry():
    app.show_page("log")
    page = app.pages["log"]
    page._scroll_to(15)
    root.update_idletasks()
    root.update()
    ent = page.rows[15][1]
    app.focus_glow.show(ent)
    root.update()


shot("timer", lambda: show("timer"))
shot("prescript", lambda: show("prescript"))
shot("settings", settings_theme)
shot("stats", lambda: show("stats"))
shot("contract", lambda: show("contract"))
shot("log", lambda: show("log"))
shot("net", lambda: show("net"))
shot("rules", lambda: show("rules"))
shot("focus_combo", focus_theme_combo)
shot("focus_entry", focus_log_entry)

root.destroy()
print("done", flush=True)
os._exit(0)
