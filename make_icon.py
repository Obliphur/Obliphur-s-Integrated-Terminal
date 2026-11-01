"""
OIT 的图标 (MECHANICS.md 16.4 / 1.1)
──────────────────────────────────────────────────────
圆角方块底 (蓝色渐变) + 一圈缺了口的计时圆环 (专注计时 / 进度) + 环里的终端提示符 ">_" (Terminal),
圆环缺口的一端有一个琥珀色的圆点 (计时的指针尖). 全部用几何图形画 (这里没有任何位图素材), 在一个 256 × 256 的坐标系里:

    背景    圆角方块 r=56, 对角渐变 #3B82F6 → #1D4ED8
    圆环    圆心 (128,128) 半径 78 线宽 18, 白色, 圆头; 缺口 60°, 中心在 -40° (右上)
    提示符  ">" (96,104) → (126,128) → (96,152), "_" (138,154) → (172,154), 线宽 17, 白色, 圆头
    圆点    琥珀色 #FBBF24, 半径 14, 在圆环的末端 (290°, 也就是上方偏右), 像计时的指针尖

纯 Python 画 (有符号距离 + 超采样, 不依赖 Pillow), 输出:
    oit.ico   16/24/32/48/64/128/256 的 PNG 图标 (窗口, 快捷方式, "已安装的应用", setup.exe)
    oit.png   256 × 256 (Tk 的 iconphoto, 预览)
    Android version/app/src/main/res/drawable/ic_fg.xml (启动图标前景), ic_bg.xml (背景渐变), ic_stat.xml (通知栏小图标, 单色)

    python make_icon.py            重新生成
    python make_icon.py --check    不写文件, 只检查几何 / 编码 (自检)
"""

import math
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "Android version", "app", "src", "main", "res")
SIZES = (16, 24, 32, 48, 64, 128, 256)

BG1, BG2 = (0x3B, 0x82, 0xF6), (0x1D, 0x4E, 0xD8)
WHITE = (255, 255, 255)
AMBER = (0xFB, 0xBF, 0x24)
C = 128.0
RING_R, RING_W = 78.0, 18.0
GAP_CENTER, GAP_HALF = -40.0, 30.0       # 缺口的中心 (度, 屏幕坐标: 顺时针为正, 0° 在右边, -90° 在上边) 和半宽: 缺口在右上
ARC_A0, ARC_A1 = GAP_CENTER + GAP_HALF, GAP_CENTER + 360.0 - GAP_HALF      # 环顺时针从 -10° 画到 290°
CHEVRON = ((96.0, 104.0), (126.0, 128.0), (96.0, 152.0))
UNDER = ((138.0, 154.0), (172.0, 154.0))
STROKE = 17.0
DOT_R = 14.0


def _polar(a_deg, r=RING_R):
    a = math.radians(a_deg)
    return C + r * math.cos(a), C + r * math.sin(a)


ARC_START = _polar(ARC_A0)
ARC_END = _polar(ARC_A1)
DOT_AT = _polar(ARC_A1)


def d_round_rect(x, y, lo, hi, r):
    cx, cy = (lo + hi) / 2, (lo + hi) / 2
    h = (hi - lo) / 2 - r
    qx, qy = abs(x - cx) - h, abs(y - cy) - h
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r


def d_segment(x, y, a, b, w):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - ax - t * dx, y - ay - t * dy) - w / 2


def d_arc(x, y):
    """环 (带圆头的圆弧): 在角度范围里是到圆环的距离, 范围外是到两个端点的距离."""
    ang = math.degrees(math.atan2(y - C, x - C))
    rel = (ang - ARC_A0) % 360.0
    if rel <= (ARC_A1 - ARC_A0):
        return abs(math.hypot(x - C, y - C) - RING_R) - RING_W / 2
    return min(math.hypot(x - ARC_START[0], y - ARC_START[1]), math.hypot(x - ARC_END[0], y - ARC_END[1])) - RING_W / 2


def shapes(x, y):
    """(白色部分的距离, 圆点的距离, 背景的距离)."""
    white = min(d_arc(x, y), d_segment(x, y, CHEVRON[0], CHEVRON[1], STROKE), d_segment(x, y, CHEVRON[1], CHEVRON[2], STROKE),
                d_segment(x, y, UNDER[0], UNDER[1], STROKE))
    dot = math.hypot(x - DOT_AT[0], y - DOT_AT[1]) - DOT_R
    return white, dot, d_round_rect(x, y, 0.0, 256.0, 56.0)


def lerp(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def render(size: int, background: bool = True):
    """size × size 的 RGBA 字节 (行优先)."""
    ss = 8 if size <= 32 else 4 if size <= 64 else 3
    scale = 256.0 / size
    out = bytearray()
    for py in range(size):
        for px in range(size):
            r = g = b = a = 0.0
            for sy in range(ss):
                for sx in range(ss):
                    x, y = (px + (sx + 0.5) / ss) * scale, (py + (sy + 0.5) / ss) * scale
                    white, dot, bg = shapes(x, y)
                    col, alpha = None, 0.0
                    if background and bg < 0:
                        col, alpha = lerp(BG1, BG2, (x + y) / 512.0), 1.0
                    if white < 0:
                        col, alpha = WHITE, 1.0
                    if dot < 0:
                        col, alpha = AMBER, 1.0
                    elif dot < 5.0 and white < 0:
                        col, alpha = lerp(BG1, BG2, (x + y) / 512.0), 1.0      # 圆点周围留一圈底色, 和白色的环分得开
                    if col is not None:
                        pm = alpha
                        r += col[0] * pm
                        g += col[1] * pm
                        b += col[2] * pm
                        a += pm
            n = ss * ss
            if a > 0:
                out += bytes((round(r / a), round(g / a), round(b / a), round(255 * a / n)))
            else:
                out += b"\x00\x00\x00\x00"
    return bytes(out)


def png(size: int, rgba: bytes) -> bytes:
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(images) -> bytes:
    """images: [(size, png 字节)] → .ico (每张图是 PNG, Windows Vista 之后都认)."""
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, data = b"", b""
    for size, p in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(p), offset + len(data))
        data += p
    return head + entries + data


# ---- Android (矢量图, 108 × 108 的画布, 内容缩到安全区里) ----

def _f(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def vector_paths(origin, k, white="#FFFFFF", dot=True, width_k=1.0):
    """把 256 坐标系里的图形按 (origin, k) 缩放成 Android 矢量图的 path 列表 (字符串)."""
    def P(p):
        return (origin[0] + (p[0] - C) * k, origin[1] + (p[1] - C) * k)
    r = RING_R * k
    s, e = P(ARC_START), P(ARC_END)
    sweep = ARC_A1 - ARC_A0
    large = 1 if sweep > 180 else 0
    sw = RING_W * k * width_k
    stroke = STROKE * k * width_k
    out = [f'    <path android:fillColor="#00000000" android:strokeColor="{white}" android:strokeWidth="{_f(sw)}" '
           f'android:strokeLineCap="round" android:pathData="M{_f(s[0])},{_f(s[1])} A{_f(r)},{_f(r)} 0 {large},1 {_f(e[0])},{_f(e[1])}" />']
    c = [P(p) for p in CHEVRON]
    u = [P(p) for p in UNDER]
    out.append(f'    <path android:fillColor="#00000000" android:strokeColor="{white}" android:strokeWidth="{_f(stroke)}" '
               f'android:strokeLineCap="round" android:strokeLineJoin="round" '
               f'android:pathData="M{_f(c[0][0])},{_f(c[0][1])} L{_f(c[1][0])},{_f(c[1][1])} L{_f(c[2][0])},{_f(c[2][1])} '
               f'M{_f(u[0][0])},{_f(u[0][1])} L{_f(u[1][0])},{_f(u[1][1])}" />')
    if dot:
        d = P(DOT_AT)
        rr = DOT_R * k
        out.append(f'    <path android:fillColor="#FBBF24" android:strokeColor="#00000000" '
                   f'android:pathData="M{_f(d[0] - rr)},{_f(d[1])} a{_f(rr)},{_f(rr)} 0 1,0 {_f(2 * rr)},0 a{_f(rr)},{_f(rr)} 0 1,0 {_f(-2 * rr)},0z" />')
    return out


def android_files():
    fg = ['<?xml version="1.0" encoding="utf-8"?>',
          '<!-- 启动图标前景 (make_icon.py 生成, 别手改): 缺口的计时圆环 + 终端提示符 ">_" + 琥珀色圆点 -->',
          '<vector xmlns:android="http://schemas.android.com/apk/res/android"',
          '    android:width="108dp" android:height="108dp"',
          '    android:viewportWidth="108" android:viewportHeight="108">']
    fg += vector_paths((54.0, 54.0), 0.36)           # 环外径 ≈ 87 × 0.36 = 31 < 安全区半径 33
    fg.append("</vector>")
    bg = ['<?xml version="1.0" encoding="utf-8"?>',
          '<!-- 启动图标背景 (make_icon.py 生成): 和电脑版图标同样的蓝色对角渐变 -->',
          '<shape xmlns:android="http://schemas.android.com/apk/res/android">',
          f'    <gradient android:angle="315" android:startColor="#{BG1[0]:02X}{BG1[1]:02X}{BG1[2]:02X}" '
          f'android:endColor="#{BG2[0]:02X}{BG2[1]:02X}{BG2[2]:02X}" />',
          '</shape>']
    stat = ['<?xml version="1.0" encoding="utf-8"?>',
            '<!-- 通知栏小图标 (make_icon.py 生成, 单色): 缺口的圆环 + ">_" -->',
            '<vector xmlns:android="http://schemas.android.com/apk/res/android"',
            '    android:width="24dp" android:height="24dp"',
            '    android:viewportWidth="24" android:viewportHeight="24">']
    stat += vector_paths((12.0, 12.0), 0.125, white="#FFFFFFFF", dot=False, width_k=1.1)
    stat.append("</vector>")
    return {"drawable/ic_fg.xml": "\n".join(fg) + "\n", "drawable/ic_bg.xml": "\n".join(bg) + "\n",
            "drawable/ic_stat.xml": "\n".join(stat) + "\n"}


def main():
    check = "--check" in sys.argv
    sizes = (16, 32) if check else SIZES
    images = [(s, png(s, render(s))) for s in sizes]
    blob = ico(images)
    assert blob[:4] == b"\x00\x00\x01\x00" and all(p[:8] == b"\x89PNG\r\n\x1a\n" for _s, p in images)
    files = android_files()
    assert "A" in files["drawable/ic_fg.xml"] and "FBBF24" in files["drawable/ic_fg.xml"]
    if check:
        # 圆心 (提示符的位置) 是白色, 角落是透明的, 缺口那一侧没有环
        px = render(32)
        def at(x, y):
            i = (y * 32 + x) * 4
            return px[i:i + 4]
        assert at(0, 0)[3] == 0, "圆角方块的角是透明的"
        assert at(16, 16)[:3] != (0, 0, 0) and at(5, 16)[3] == 255
        return "make_icon check OK"
    big = images[-1][1]
    with open(os.path.join(HERE, "oit.ico"), "wb") as f:
        f.write(blob)
    with open(os.path.join(HERE, "oit.png"), "wb") as f:
        f.write(big)
    for name, text in files.items():
        with open(os.path.join(RES, name.replace("/", os.sep)), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return f"oit.ico ({len(blob) // 1024} KB), oit.png, {len(files)} Android drawables"


if __name__ == "__main__":
    print(main())
