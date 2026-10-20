"""提前启动: 挑至多两个程序 (仅 PC, 见 MECHANICS 20).

一个 Toplevel: 上面搜索框, 中间程序清单 (开始菜单 / App Paths / 正在运行),
下面 "已选 n/2" 和 取消 / 确定. 确定后把选中的程序交给 on_done (app 那边启动它们).
"""

import tkinter as tk
from tkinter import ttk

import config
from config import COLORS
from i18n import tr


class PrestartDialog(tk.Toplevel):
    """程序清单: 最多选 limit 个. on_done(apps) 在点"确定"时调用."""

    def __init__(self, master, apps, on_done=None, limit: int = 2):
        super().__init__(master, bg=COLORS["bg"], highlightbackground=COLORS["accent"],
                         highlightthickness=2)
        self.title(tr("⏩  提前启动"))
        self.resizable(False, True)
        self.attributes("-topmost", True)
        self.apps = list(apps or [])
        self.on_done = on_done
        self.limit = max(1, int(limit))
        self.shown = []
        self._build()
        self._filter("")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        self.update_idletasks()
        w, h = max(560, self.winfo_reqwidth()), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")

    def _build(self):
        bg = COLORS["bg"]
        body = tk.Frame(self, bg=bg)
        body.pack(fill="both", expand=True, padx=20, pady=16)

        tk.Label(body, text=tr("⏩  提前启动"), bg=bg, fg=COLORS["text"],
                 font=config.font("h2", bold=True), anchor="w").pack(fill="x")
        tk.Label(body, text=tr("挑至多 {n} 个程序: 现在就启动 (或接管已在运行的), "
                               "窗口藏起来、静音; 结束专注时再放出来.",
                               n=self.limit),
                 bg=bg, fg=COLORS["text_dim"], font=config.font("small"), anchor="w",
                 justify="left", wraplength=config.scaled(560)).pack(fill="x", pady=(2, 10))

        row = tk.Frame(body, bg=bg)
        row.pack(fill="x")
        tk.Label(row, text=tr("搜索"), bg=bg, fg=COLORS["text_dim"],
                 font=config.font("small")).pack(side="left")
        self.var = tk.StringVar()
        ent = ttk.Entry(row, textvariable=self.var, width=28)
        ent.pack(side="left", padx=(6, 0), fill="x", expand=True)
        self.var.trace_add("write", lambda *_: self._filter(self.var.get()))

        frame = tk.Frame(body, bg=COLORS["bg_card"], highlightbackground=COLORS["border"],
                         highlightthickness=1)
        frame.pack(fill="both", expand=True, pady=(8, 8))
        sb = ttk.Scrollbar(frame, orient="vertical")
        sb.pack(side="right", fill="y")
        self.listbox = tk.Listbox(
            frame, height=14, width=54, selectmode="extended", activestyle="none",
            bg=COLORS["bg_card"], fg=COLORS["text"], selectbackground=COLORS["accent"],
            selectforeground=COLORS["on_accent"], highlightthickness=0, bd=0,
            font=config.font("body"), yscrollcommand=sb.set, exportselection=False)
        self.listbox.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=6)
        sb.config(command=self.listbox.yview)
        self.listbox.bind("<<ListboxSelect>>", lambda e: self._on_select())
        self.listbox.bind("<Double-Button-1>", lambda e: self._ok())

        foot = tk.Frame(body, bg=bg)
        foot.pack(fill="x")
        self.lbl_count = tk.Label(foot, text="", bg=bg, fg=COLORS["text_dim"],
                                  font=config.font("small"))
        self.lbl_count.pack(side="left")
        ttk.Button(foot, text=tr("取消"), command=self.close).pack(side="right")
        self.btn_ok = ttk.Button(foot, text=tr("确定"), style="Accent.TButton", command=self._ok)
        self.btn_ok.pack(side="right", padx=(0, 8))

    # ---- 清单 / 选择 ----

    def _filter(self, text: str):
        text = str(text or "").strip().lower()
        picked = {self.shown[i]["exe"] for i in self.listbox.curselection() if i < len(self.shown)}
        self.shown = [a for a in self.apps
                      if not text or text in a["name"].lower() or text in str(a["exe"]).lower()]
        self.listbox.delete(0, "end")
        for i, a in enumerate(self.shown):
            tag = "  ·  " + tr("正在运行") if a.get("running") else ""
            self.listbox.insert("end", f"{a['name']}  ({a['exe']}){tag}")
            if a["exe"] in picked and len(self.listbox.curselection()) < self.limit:
                self.listbox.selection_set(i)
        self._on_select()

    def _selected(self) -> list:
        return [self.shown[i] for i in self.listbox.curselection() if i < len(self.shown)]

    def _on_select(self):
        sel = list(self.listbox.curselection())
        if len(sel) > self.limit:                 # 多选了: 只留前 limit 个
            for i in sel[self.limit:]:
                self.listbox.selection_clear(i)
        n = len(self._selected())
        self.lbl_count.config(text=tr("已选 {n}/{m}", n=n, m=self.limit))
        self.btn_ok.config(state="normal" if n else "disabled")

    def _ok(self):
        sel = self._selected()
        if not sel:
            return
        self.close()
        if self.on_done:
            self.on_done(sel)

    def close(self):
        try:
            self.destroy()
        except Exception:
            pass
