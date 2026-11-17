"""基本设置中的外部软件目录和 Process Lasso 部署入口。"""

import tkinter as tk
from tkinter import ttk, filedialog

import config
import external_software
import toast
from config import COLORS
from i18n import tr
from ..widgets import cfg, wrap_label


class ExternalSettingsMixin:
    def _build_external_card(self, body):
        self.external = external_software.manager(self.data_dir or config.app_data_dir())
        card = COLORS["bg_card"]
        fr = tk.Frame(body, bg=card, highlightbackground=COLORS["border"], highlightthickness=1)
        fr.pack(fill="x", pady=(2, 8))
        self._track(tk.Label(fr, text=tr("外部软件"), bg=card, fg=COLORS["text"],
                            font=config.font("body", bold=True)), "外部软件").pack(anchor="w", padx=12, pady=(10, 4))
        self.external_root = tk.Label(fr, bg=card, fg=COLORS["text"], anchor="w", justify="left",
                                      font=config.font("body"))
        self.external_root.pack(fill="x", padx=12)
        wrap_label(self.external_root, 24)
        self.external_choose = self._track(ttk.Button(fr, text=tr("选择外部软件安装位置…"),
                                                     command=self._choose_external_root), "选择外部软件安装位置…")
        self.external_choose.pack(anchor="w", padx=12, pady=(6, 8))
        tk.Label(fr, text="Process Lasso", bg=card, fg=COLORS["text"],
                 font=config.font("body", bold=True)).pack(anchor="w", padx=12)
        self.external_status = tk.Label(fr, bg=card, fg=COLORS["text_dim"], anchor="w", justify="left",
                                        font=config.font("body"))
        self.external_status.pack(fill="x", padx=12, pady=4)
        wrap_label(self.external_status, 24)
        self.external_deploy = ttk.Button(fr, command=self._deploy_external)
        self.external_deploy.pack(anchor="w", padx=12, pady=4)
        self.external_open = self._track(ttk.Button(fr, text=tr("打开 Process Lasso"),
                                                   command=self._open_external), "打开 Process Lasso")
        self.external_open.pack(anchor="w", padx=12, pady=(0, 10))
        self._poll_external()
        self.areas["basic"].bind_children()

    def _poll_external(self):
        self._refresh_external_card()
        self._external_job = self.after(500, self._poll_external)
        # 页面重建时取消轮询，部署由本机 manager 继续执行。
        if not getattr(self, "_external_destroy_bound", False):
            self._external_destroy_bound = True
            self.bind("<Destroy>", self._stop_external_poll, add="+")

    def _stop_external_poll(self, event):
        if event.widget is self:
            self.after_cancel(self._external_job)

    def _choose_external_root(self):
        path = filedialog.askdirectory(parent=self, title=tr("选择外部软件安装位置…"),
                                       initialdir=self.external.root or None, mustexist=True)
        if path:
            try:
                self.external.select_root(path)
            except (OSError, ValueError) as e:
                toast.warn(str(e))
            self._refresh_external_card()

    def _deploy_external(self):
        if not self.external.root:
            self._choose_external_root()
        if not self.external.root:
            return
        try:
            self.external.deploy()
        except (OSError, ValueError) as e:
            toast.warn(str(e))
        self._refresh_external_card()

    def _open_external(self):
        try:
            self.external.open_lasso()
        except OSError as e:
            toast.warn(str(e))

    def _refresh_external_card(self):
        u = self.external
        cfg(self.external_root, text=tr("外部软件安装位置: {path}", path=u.root) if u.root else tr("尚未选择外部软件安装位置"))
        state = {"downloading": tr("正在下载 Process Lasso… {n}%", n=u.progress),
                 "verifying": tr("正在校验 Bitsum 数字签名…"),
                 "configuring": tr("正在配置模型保护规则…"),
                 "installing": tr("正在安装 Process Lasso…"),
                 "ready": tr("已部署；Python / PyTorch 与 OIT 已排除抑制和内存整理."),
                 "error": tr("部署失败: {e}", e=u.error)}.get(u.state)
        if state is None:
            state = tr("已安装，点击重新部署可恢复模型保护规则.") if u.installed else tr("尚未安装 Process Lasso")
        if u.installed:
            state += "\n" + tr("安装位置: {path}", path=u.installed_dir)
        cfg(self.external_status, text=state, fg=COLORS["warn"] if u.state == "error" else COLORS["text_dim"])
        cfg(self.external_deploy, text=tr("重新部署并配置") if u.installed else tr("下载、安装并配置"))
        self.external_choose.state(["disabled"] if u.location_busy else ["!disabled"])
        self.external_deploy.state(["disabled"] if u.location_busy else ["!disabled"])
        self.external_open.state(["!disabled"] if u.installed and not u.location_busy else ["disabled"])
