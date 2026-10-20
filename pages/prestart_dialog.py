"""提前启动: 挑至多两个程序 (仅 PC, 见 MECHANICS 20).

一个 Toplevel: 上面搜索框 + "打开文件管理器" (它只负责把程序加进清单), 中间是程序清单 ——
点一行只是**选中 / 取消选中** (最多两个), 每行右边的 ✕ 把程序从清单里去掉;
下面 "已选 n/2" 和 取消 / 确定. 点确定才开始 (app 那边启动它们).
"""

import os
import tkinter as tk
from tkinter import ttk

import config
import prestart
from config import COLORS
from i18n import tr
from .widgets import ScrollArea


class PrestartDialog(tk.Toplevel):
    """程序清单: 最多选 limit 个. on_done(apps) 在点"确定"时调用."""

    def __init__(self, master, apps, on_done=None, limit: int = 2):
        super().__init__(master, bg=COLORS["bg"], highlightbackground=COLORS["accent"],
                         highlightthickness=2)
        self.title(tr("⏩  提前启动"))
        self.resizable(False, True)
        self.attributes("-topmost", True)
        self.apps = [dict(a) for a in (apps or [])]
        self.on_done = on_done
        self.limit = max(1, int(limit))
        self.shown = []
        self.picked = []                 # 选中的 exe (按选中的先后, 最多 limit 个)
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
        card = COLORS["bg_card"]
        body = tk.Frame(self, bg=bg)
        body.pack(fill="both", expand=True, padx=20, pady=16)

        tk.Label(body, text=tr("⏩  提前启动"), bg=bg, fg=COLORS["text"],
                 font=config.font("h2", bold=True), anchor="w").pack(fill="x")
        tk.Label(body, text=tr("挑至多 {n} 个程序: 点确定后有 3 分钟可以先操作, "
                               "之后窗口藏起来、静音; 结束专注时再放出来.",
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
        # 清单里没有的程序: 打开文件管理器自己挑 (.exe / .lnk) —— 只负责加进清单
        ttk.Button(row, text=tr("📂  打开文件管理器"),
                   command=self._browse).pack(side="left", padx=(8, 0))

        frame = tk.Frame(body, bg=card, highlightbackground=COLORS["border"], highlightthickness=1)
        frame.pack(fill="both", expand=True, pady=(8, 8))
        self.area = ScrollArea(frame, height=config.scaled(330), bg=card)
        self.area.pack(fill="both", expand=True, padx=2, pady=2)
        self.rows_host = self.area.inner

        foot = tk.Frame(body, bg=bg)
        foot.pack(fill="x")
        self.lbl_count = tk.Label(foot, text="", bg=bg, fg=COLORS["text_dim"],
                                  font=config.font("small"))
        self.lbl_count.pack(side="left")
        ttk.Button(foot, text=tr("取消"), command=self.close).pack(side="right")
        self.btn_ok = ttk.Button(foot, text=tr("确定"), style="Accent.TButton", command=self._ok)
        self.btn_ok.pack(side="right", padx=(0, 8))

    # ---- 清单 / 选中 / 移除 ----

    def _filter(self, text: str):
        text = str(text or "").strip().lower()
        self.shown = [a for a in self.apps
                      if not text or text in a["name"].lower() or text in str(a["exe"]).lower()]
        self.picked = [e for e in self.picked if e in {a["exe"] for a in self.apps}]
        self._render()

    def _render(self):
        """按当前清单重画每一行 (选中 / 未选中两种样子) —— 点一行只切换选中, 不启动."""
        card, accent = COLORS["bg_card"], COLORS["accent"]
        on_accent = COLORS["on_accent"]
        for w in self.rows_host.winfo_children():
            w.destroy()
        if not self.shown:
            tk.Label(self.rows_host, text=tr("没有匹配的程序."), bg=card, fg=COLORS["text_dim"],
                     font=config.font("body"), anchor="w").pack(anchor="w", padx=10, pady=10)
            self.area.bind_children()            # 整条栏都能滚 (不只是右边那一条滚动条)
            self._update_foot()
            return
        for app in self.shown:
            exe = app["exe"]
            on = exe in self.picked
            fg = on_accent if on else COLORS["text"]
            row_bg = accent if on else card
            row = tk.Frame(self.rows_host, bg=row_bg,
                           highlightbackground=COLORS["border"], highlightthickness=1)
            row.pack(fill="x", padx=4, pady=2)
            text = ("✓  " if on else "     ") + app["name"] + f"  ({exe})"
            if app.get("running"):
                text += "  ·  " + tr("正在运行")
            lbl = tk.Label(row, text=text, bg=row_bg, fg=fg, anchor="w",
                           font=config.font("body"))
            lbl.pack(side="left", fill="x", expand=True, padx=(8, 0), pady=6)
            rm = tk.Button(row, text="✕", relief="flat", bd=0, padx=8, pady=2,
                           bg=row_bg, fg=on_accent if on else COLORS["text_dim"],
                           activebackground=COLORS["danger"] if on else COLORS["border"],
                           activeforeground=on_accent if on else COLORS["text"],
                           font=config.font("body", bold=True), cursor="hand2",
                           command=lambda e=exe: self.remove(e))
            rm.pack(side="right", padx=(4, 6))
            for w in (row, lbl):
                w.bind("<Button-1>", lambda e, x=exe: self.toggle(x))
        self.area.bind_children()                # 整条栏都能滚 (不只是右边那一条滚动条)
        self._update_foot()

    def _update_foot(self):
        n = len(self._selected())
        self.lbl_count.config(text=tr("已选 {n}/{m}", n=n, m=self.limit))
        self.btn_ok.config(state="normal" if n else "disabled")

    def _selected(self) -> list:
        by = {a["exe"]: a for a in self.apps}
        return [by[e] for e in self.picked if e in by]

    def toggle(self, exe: str):
        """点一行: 选中 / 取消选中. 已经选了两个时, 先放开最早选的那个."""
        if exe in self.picked:
            self.picked.remove(exe)
        else:
            self.picked.append(exe)
            if len(self.picked) > self.limit:
                self.picked.pop(0)
        self._render()

    def remove(self, exe: str):
        """点行右边的 ✕: 把这个程序从清单里去掉了."""
        self.apps = [a for a in self.apps if a["exe"] != exe]
        if exe in self.picked:
            self.picked.remove(exe)
        self._filter(self.var.get())

    # ---- 打开文件管理器自己挑程序 (只负责加进清单) ----

    def _browse(self):
        from tkinter import filedialog, messagebox
        path = filedialog.askopenfilename(
            parent=self, title=tr("打开文件管理器查找程序"),
            filetypes=[(tr("程序"), "*.exe"), (tr("快捷方式"), "*.lnk"),
                       (tr("所有文件"), "*.*")])
        if not path:
            return
        item = prestart.program_from_path(path)
        if item is None:
            messagebox.showwarning(
                tr("不是正常的程序"),
                tr("这个文件不能提前启动: {p}", p=os.path.basename(path)), parent=self)
            return
        self.add_item(item)

    def add_item(self, item: dict):
        """加进清单 (不自动选中); 已经在清单里就只把它选中."""
        exe = str(item.get("exe") or "")
        if not exe:
            return
        if any(a["exe"] == exe for a in self.apps):
            if exe not in self.picked:
                self.toggle(exe)
            return
        self.apps.append(dict(item))
        self.var.set("")                     # 清掉搜索, 保证新加的那条看得见
        self._filter("")
        self.area.canvas.yview_moveto(1.0)

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
