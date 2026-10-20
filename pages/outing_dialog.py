"""外出: 设置持续多少小时 (封闭式管理 / 工作, MECHANICS 21).

只有一个填写框: 持续几小时 (1 ~ OUTING_MAX_HOURS), 可以跨天. 点"开始外出"后
由 app 开始记录; 关闭窗口 = 取消.
"""

import tkinter as tk
from tkinter import ttk

import config
from config import COLORS
from i18n import tr


class OutingDialog(tk.Toplevel):
    def __init__(self, master, on_done=None, max_hours: int = None):
        super().__init__(master, bg=COLORS["bg"], highlightbackground=COLORS["accent"],
                         highlightthickness=2)
        self.on_done = on_done
        self.max_hours = max(1, int(max_hours or config.OUTING_MAX_HOURS))
        self.title(tr("🚪  外出"))
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<Return>", lambda e: self._ok())
        self._build()
        self.update_idletasks()
        w, h = max(360, self.winfo_reqwidth()), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        try:
            self.entry.focus_force()
        except Exception:
            pass

    def _build(self):
        bg = COLORS["bg"]
        body = tk.Frame(self, bg=bg)
        body.pack(fill="both", expand=True, padx=22, pady=18)
        tk.Label(body, text=tr("🚪  外出"), bg=bg, fg=COLORS["text"],
                 font=config.font("h2", bold=True), anchor="w").pack(fill="x")
        row = tk.Frame(body, bg=bg)
        row.pack(fill="x", pady=(14, 4))
        tk.Label(row, text=tr("持续多少小时?"), bg=bg, fg=COLORS["text"],
                 font=config.font("body")).pack(side="left")
        self.var = tk.StringVar(value="2")
        self.entry = ttk.Entry(row, textvariable=self.var, width=8, justify="right")
        self.entry.pack(side="left", padx=(10, 4))
        tk.Label(row, text="h", bg=bg, fg=COLORS["text_dim"],
                 font=config.font("body")).pack(side="left")
        self.lbl_err = tk.Label(body, text="", bg=bg, fg=COLORS["danger"],
                                font=config.font("small"), anchor="w")
        self.lbl_err.pack(fill="x")
        btns = tk.Frame(body, bg=bg)
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text=tr("取消"), command=self.close).pack(side="right")
        ttk.Button(btns, text=tr("开始外出"), style="Accent.TButton",
                   command=self._ok).pack(side="right", padx=(0, 8))

    def _ok(self):
        text = self.var.get().strip()
        try:
            hours = int(text)
        except (TypeError, ValueError):
            self.lbl_err.config(text=tr("请填写整数小时."))
            return
        if hours < 1 or hours > self.max_hours:
            self.lbl_err.config(text=tr("持续时间要在 1 ~ {n} 小时之间.").format(n=self.max_hours))
            return
        self.close()
        if self.on_done:
            self.on_done(hours)

    def close(self):
        try:
            self.destroy()
        except Exception:
            pass
