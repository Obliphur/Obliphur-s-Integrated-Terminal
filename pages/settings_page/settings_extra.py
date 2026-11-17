"""设置 → 额外功能：AI 语音、Process Lasso 与钢琴转 MIDI 模型。"""

import tkinter as tk
from tkinter import ttk

import config
import piano_model
from config import COLORS
from i18n import tr
from ..voice_dialogs import build_voice_settings
from ..widgets import cfg, wrap_label


class ExtraSettingsMixin:
    def _build_extra_page(self, body):
        if self.comm is not None:
            build_voice_settings(self, body)
        self._build_external_lasso_card(body)
        card = COLORS["bg_card"]
        fr = tk.Frame(body, bg=card, highlightbackground=COLORS["border"], highlightthickness=1)
        fr.pack(fill="x", pady=(2, 8))
        tk.Label(fr, text=tr("钢琴转 MIDI 模型"), bg=card, fg=COLORS["text"],
                 font=config.font("body", bold=True)).pack(anchor="w", padx=12, pady=(10, 4))
        self.piano_model = piano_model.manager(self.data_dir or config.app_data_dir(), self.external)
        self.piano_model_status = tk.Label(fr, text="", bg=card, fg=COLORS["text_dim"],
                                           font=config.font("body"), anchor="w", justify="left")
        self.piano_model_status.pack(fill="x", padx=12, pady=4)
        wrap_label(self.piano_model_status, 24)
        self.piano_model_button = ttk.Button(fr, command=self._download_piano_model)
        self.piano_model_button.pack(anchor="w", padx=12, pady=(2, 10))
        self._refresh_extra_page()
        self._poll_extra_page()
        self.areas["extra"].bind_children()
        self.bind("<Destroy>", self._stop_extra_poll, add="+")

    def _stop_extra_poll(self, event):
        if event.widget is self:
            self.after_cancel(self._extra_job)

    def _poll_extra_page(self):
        if not self.winfo_exists():
            return
        self._refresh_extra_page()
        self._extra_job = self.after(500, self._poll_extra_page)

    def _download_piano_model(self):
        try:
            self.piano_model.download()
        except ValueError as e:
            import toast
            toast.warn(str(e))
        self._refresh_extra_page()

    def _refresh_extra_page(self):
        if not hasattr(self, "piano_model"):
            return
        self.piano_model.refresh()
        if hasattr(self, "_voice_settings_refresh"):
            self._voice_settings_refresh()
        m = self.piano_model
        if m.busy:
            text = tr("正在下载模型… {n}%", n=m.progress)
        elif m.ready:
            text = tr("模型已下载: {path}", path=m.path)
        elif m.state == "error":
            text = tr("模型下载失败: {e}", e=m.error)
        else:
            text = tr("模型大小: 164 MiB")
        cfg(self.piano_model_status, text=text, fg=COLORS["warn"] if m.state == "error" else COLORS["text_dim"])
        cfg(self.piano_model_button, text=tr("重新下载模型") if m.ready else tr("下载模型"))
        self.piano_model_button.state(["disabled"] if m.busy or self.external.busy else ["!disabled"])
