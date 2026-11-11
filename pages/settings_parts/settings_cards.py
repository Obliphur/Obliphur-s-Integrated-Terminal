"""设置页的卡片: 首次设置、更新、成就、规则、触发隐藏的窗口。

从 pages/settings_page.py 拆出来的 mixin; 依赖方向: settings_page.py 导入它们, 它们从不导入 settings_page.py.
"""

import tkinter as tk
from tkinter import ttk
import config
import toast
from config import COLORS
from i18n import tr

from ..widgets import (wrap_label, cfg, show_pack, hide_pack)


class CardsMixin:
    def _build_setup_card(self, body):
        """配置期: 剩余时间 + 提前结束按钮. 不在配置期时整块隐藏."""
        card = COLORS["bg_card"]
        fr = tk.Frame(body, bg=card, highlightbackground=COLORS["accent"],
                      highlightthickness=1)
        self.setup_card = fr
        row = tk.Frame(fr, bg=card)
        row.pack(fill="x", padx=12, pady=(10, 2))
        self._track(tk.Label(
            row, text=tr("⏳  配置期"), bg=card, fg=COLORS["text"],
            font=config.font("body", bold=True)), "⏳  配置期").pack(side="left")
        self._track(ttk.Button(row, text=tr("结束配置期"), command=self._end_setup),
                    "结束配置期").pack(side="right")
        self.lbl_setup = tk.Label(fr, text="", bg=card, fg=COLORS["text_dim"],
                                  font=config.font("small"), anchor="w", justify="left")
        self.lbl_setup.pack(fill="x", padx=12, pady=(0, 10))
        wrap_label(self.lbl_setup, 24)

    def _end_setup(self):
        if not toast.ask(
            tr("结束后每次修改都要等 48 小时,不能撤销."),
            title=tr("结束配置期"), danger=True
        ):
            return
        if self.on_end_setup:
            self.on_end_setup()
        self.refresh()

    def _build_update_card(self, body):
        """更新 (MECHANICS 16): 当前版本, GitHub 上的最新版本, 检查 / 立即更新."""
        if self.updater is None:
            return
        card = COLORS["bg_card"]
        fr = tk.Frame(body, bg=card, highlightbackground=COLORS["border"], highlightthickness=1)
        fr.pack(fill="x", pady=(2, 8))
        self.update_card = fr
        row = tk.Frame(fr, bg=card)
        row.pack(fill="x", padx=12, pady=(10, 2))
        self._track(tk.Label(row, text=tr("⬆  更新"), bg=card, fg=COLORS["text"],
                             font=config.font("body", bold=True)), "⬆  更新").pack(side="left")
        self.lbl_update = tk.Label(fr, text="", bg=card, fg=COLORS["text_dim"],
                                   font=config.font("small"), anchor="w", justify="left")
        self.lbl_update.pack(fill="x", padx=12)
        btns = tk.Frame(fr, bg=card)
        btns.pack(fill="x", padx=12, pady=(6, 10))
        self._track(ttk.Button(btns, text=tr("检查更新"),
                               command=lambda: self.on_update_check and self.on_update_check()),
                    "检查更新").pack(side="left")
        self.btn_update_now = self._track(ttk.Button(
            btns, text=tr("立即更新"), style="Accent.TButton",
            command=lambda: self.on_update_install and self.on_update_install()), "立即更新")

        # 给绑定的其他设备下载安装包, 直接从局域网发过去 (对方不用自己去 GitHub 下载)
        self._track(ttk.Button(btns, text=tr("📤 发给其他设备"),
                               command=lambda: self.on_update_relay and self.on_update_relay()),
                    "📤 发给其他设备").pack(side="left", padx=(8, 0))

        def open_page():
            import webbrowser
            import updater
            webbrowser.open(updater.RELEASES_PAGE)
        self._track(ttk.Button(btns, text=tr("发布页"), command=open_page), "发布页").pack(
            side="right")

    def _refresh_update_card(self):
        u = self.updater
        if u is None or self.update_card is None:
            return
        text = tr("当前版本 {v}", v=config.APP_VERSION)
        state = {
            "checking": tr("正在检查…"),
            "latest": tr("已是最新版本"),
            "available": tr("发现新版本 {v}", v=u.latest_text),
            "downloading": tr("正在下载更新…"),
            "installed": tr("已更新到 {v}, 重启后生效", v=u.latest_text),
            "dev": tr("这是 git 仓库里的开发版本, 不会自动覆盖, 请用 git pull 更新"),
            "error": tr("检查 / 更新失败: {e}", e=u.error),
        }.get(u.state, "")
        if state:
            text += "  ·  " + state
        cfg(self.lbl_update, text=text,
            fg=COLORS["accent"] if u.state in ("available", "installed") else
            (COLORS["warn"] if u.state in ("error", "dev") else COLORS["text_dim"]))
        if u.state == "available":
            show_pack(self.btn_update_now, side="left", padx=(8, 0))
        else:
            hide_pack(self.btn_update_now)


    def _build_achievements_card(self, body):
        """成就 (MECHANICS 17): 一栏 + "打开"按钮 (显示已达成的个数), 打开单独的成就页. 和手机版一样."""
        if self.ach_store is None:
            return
        card = COLORS["bg_card"]
        fr = tk.Frame(body, bg=card, highlightbackground=COLORS["border"],
                      highlightthickness=1, cursor="hand2")
        fr.pack(fill="x", pady=(2, 8))
        row = tk.Frame(fr, bg=card, cursor="hand2")
        row.pack(fill="x", padx=12, pady=10)
        title = tk.Label(row, text=tr("🏆  成就"), bg=card, fg=COLORS["text"],
                         font=config.font("body", bold=True), cursor="hand2")
        title.pack(side="left")
        self._track(title, "🏆  成就")
        self._track(ttk.Button(row, text=tr("打开"), command=self.open_achievements),
                    "打开").pack(side="right")
        self.lbl_ach_count = tk.Label(row, text="", bg=card, fg=COLORS["accent"],
                                      font=config.font("body", bold=True), cursor="hand2")
        self.lbl_ach_count.pack(side="right", padx=(0, 10))
        # 整块都能点 (不用非得点到按钮上)
        for w in (fr, row, title, self.lbl_ach_count):
            w.bind("<Button-1>", lambda _e: self.open_achievements())
        self.refresh_achievements()

    def open_achievements(self):
        """打开成就页 (单独的页, 由主窗口切换过去)."""
        if self.on_open_achievements is not None:
            self.on_open_achievements()

    def refresh_achievements(self):
        if self.lbl_ach_count is not None:
            cfg(self.lbl_ach_count,
                text=tr("已达成 {n}", n=len(self.ach_store.unlocked)))

    # ---- 联网页: 规则 (弹窗) ----

    def _build_rules_card(self, body):
        fr, head = self._card(body, "🛡  规则")
        self._track(ttk.Button(head, text=tr("打开"), command=lambda: self.on_open_rules and self.on_open_rules()),
                    "打开").pack(side="right")

    # ---- 窗口页: 触发隐藏的窗口 (MECHANICS 20) ----

    def _build_hidden_windows(self, body):
        """设置 → 窗口: 程序名匹配的窗口总被当成提前启动藏起来 (而不是被应用名单关掉), 但不获得联网权限.
        添加式列表: 填一个程序名点添加, 下面是已有的程序; 选中后删除, 都是即时保存."""
        if self.hidden_windows is None:
            return
        card = COLORS["bg_card"]
        fr, head = self._card(body, "🫥  触发隐藏的窗口")
        tk.Label(fr, text=tr("程序名填 exe 名, 例如 chrome.exe 或 chrome; 这些程序的窗口会藏起来, 但不获得联网权限."),
                 bg=card, fg=COLORS["text_dim"], font=config.font("small"),
                 anchor="w", justify="left", wraplength=config.scaled(560)).pack(
            fill="x", padx=12, pady=(2, 4))
        lf = tk.Frame(fr, bg=card)
        lf.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        sb = ttk.Scrollbar(lf, orient="vertical")
        self.hidden_list = tk.Listbox(
            lf, height=5, activestyle="none", exportselection=False,
            font=config.font("body"), yscrollcommand=sb.set,
            highlightbackground=COLORS["border"], selectmode="extended")
        sb.config(command=self.hidden_list.yview)
        sb.pack(side="right", fill="y")
        self.hidden_list.pack(side="left", fill="both", expand=True)
        er = tk.Frame(fr, bg=card)
        er.pack(fill="x", padx=12, pady=(0, 4))
        self.hidden_var = tk.StringVar()
        ent = ttk.Entry(er, textvariable=self.hidden_var)
        ent.pack(side="left", fill="x", expand=True)
        ent.bind("<Return>", lambda _e: self._add_hidden_window())
        self._track(ttk.Button(er, text=tr("添加"), style="Accent.TButton",
                               command=self._add_hidden_window), "添加").pack(side="left", padx=(6, 0))
        br = tk.Frame(fr, bg=card)
        br.pack(fill="x", padx=12, pady=(0, 10))
        self._track(ttk.Button(br, text=tr("删除选中"),
                               command=self._remove_hidden_window), "删除选中").pack(side="left")
        self._refresh_hidden_list()
        self.areas["window"].bind_children()

    def _refresh_hidden_list(self):
        self.hidden_list.delete(0, "end")
        for program in self.hidden_windows.programs:
            self.hidden_list.insert("end", program)

    def _add_hidden_window(self):
        if self.hidden_windows is None:
            return
        text = self.hidden_var.get().strip()
        if not text:
            return
        if not self.hidden_windows.set_programs(list(self.hidden_windows.programs) + [text]):
            toast.info(tr("已经在名单里了."))
            self.hidden_var.set("")
            return
        self.hidden_var.set("")
        self._refresh_hidden_list()
        self.on_hidden_windows()
        toast.success(tr("触发隐藏的窗口已保存."))

    def _remove_hidden_window(self):
        if self.hidden_windows is None:
            return
        picks = list(self.hidden_list.curselection())
        if not picks:
            toast.info(tr("先在列表里选中要删除的项."))
            return
        programs = list(self.hidden_windows.programs)
        for i in reversed(picks):
            if 0 <= i < len(programs):
                programs.pop(i)
        self.hidden_windows.set_programs(programs)
        self._refresh_hidden_list()
        self.on_hidden_windows()
        toast.success(tr("触发隐藏的窗口已保存."))

