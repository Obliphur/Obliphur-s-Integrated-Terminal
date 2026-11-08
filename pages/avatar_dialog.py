# -*- coding: utf-8 -*-
"""选头像: 资源管理器里选一张图 → 拖动 / 缩放, 圆框里的就是头像 (逻辑在 avatar.py)."""

import os
import tempfile
import tkinter as tk
from tkinter import filedialog, ttk

import avatar
import config
import toast
from config import COLORS
from i18n import tr
from .widgets import Modal

OUT_PX = 256
ZOOM_MAX = 0.1          # 缩到最小时, 取景框边长是图片短边的 10%


def pick_avatar(parent, dest: str) -> bool:
    """选图 + 截取, 圆形 PNG 存到 dest. 保存了返回 True."""
    path = filedialog.askopenfilename(
        parent=parent, title=tr("选择头像"),
        filetypes=[(tr("图片"), "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff"), (tr("所有文件"), "*.*")])
    if not path:
        return False
    try:
        w, h = avatar.image_size(path)
    except OSError:
        toast.warn(tr("打不开这张图片."))
        return False

    S = config.scaled(340)                  # 取景区的边长 (像素)
    R = S * 0.46                            # 圆框半径
    dlg = Modal(parent, tr("截取头像"), width=S + 50, height=S + config.scaled(150))
    st = {"cx": w / 2, "cy": h / 2, "z": 0.0, "job": None, "photo": None, "drag": None, "ok": False}
    base = float(min(w, h))
    tmp = os.path.join(tempfile.gettempdir(), f"oit_avatar_{os.getpid()}.png")

    def side() -> float:
        return base * (ZOOM_MAX ** st["z"])

    cv = tk.Canvas(dlg.body, width=S, height=S, bg=COLORS["bg_card"], highlightthickness=1,
                   highlightbackground=COLORS["border"])
    cv.pack()
    img_id = cv.create_image(0, 0, anchor="nw")
    # 圆框外变暗: 一个很粗的圆环盖住四个角
    ring = (S / 2) * 1.6
    cv.create_oval(S / 2 - R - ring / 2, S / 2 - R - ring / 2, S / 2 + R + ring / 2, S / 2 + R + ring / 2,
                   outline="#000000", width=ring, outlinestipple="gray50")
    cv.create_oval(S / 2 - R, S / 2 - R, S / 2 + R, S / 2 + R, outline="#ffffff", width=2)

    def redraw():
        st["job"] = None
        s = side()
        st["cx"] = min(max(st["cx"], s / 2), w - s / 2)
        st["cy"] = min(max(st["cy"], s / 2), h - s / 2)
        try:
            avatar.render(path, tmp, st["cx"], st["cy"], s, S, circle=False)
            st["photo"] = tk.PhotoImage(file=tmp)
            cv.itemconfig(img_id, image=st["photo"])
        except (OSError, tk.TclError):
            pass

    def later():
        if st["job"] is None:
            st["job"] = dlg.after(25, redraw)

    def press(e):
        st["drag"] = (e.x, e.y)

    def move(e):
        if st["drag"] is None:
            return
        dx, dy = e.x - st["drag"][0], e.y - st["drag"][1]
        st["drag"] = (e.x, e.y)
        k = side() / S
        st["cx"] -= dx * k
        st["cy"] -= dy * k
        later()

    def zoom_to(z):
        st["z"] = min(1.0, max(0.0, float(z)))
        var.set(st["z"] * 100)
        later()

    cv.bind("<ButtonPress-1>", press)
    cv.bind("<B1-Motion>", move)
    cv.bind("<MouseWheel>", lambda e: zoom_to(st["z"] + (0.05 if e.delta > 0 else -0.05)))
    var = tk.DoubleVar(value=0)
    ttk.Scale(dlg.body, from_=0, to=100, variable=var, orient="horizontal",
              command=lambda v: (st.__setitem__("z", float(v) / 100), later())).pack(fill="x", pady=(10, 0))

    def save():
        crop = side() * (2 * R) / S                       # 圆框直径对应的源图边长
        try:
            avatar.render(path, dest, st["cx"], st["cy"], crop, OUT_PX, circle=True)
            avatar.render(path, avatar.full_path(dest), st["cx"], st["cy"], crop, OUT_PX, circle=False)
        except OSError:
            toast.warn(tr("保存头像失败."))
            return
        st["ok"] = True
        dlg.finish(True)

    ttk.Button(dlg.buttons, text=tr("确定"), style="Accent.TButton", command=save).pack(side="right")
    ttk.Button(dlg.buttons, text=tr("取消"), command=dlg.cancel).pack(side="right", padx=(0, 8))
    redraw()
    dlg.wait()
    try:
        os.remove(tmp)
    except OSError:
        pass
    return st["ok"]
