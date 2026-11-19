"""演奏评判用的 MIDI 设备选择和录制。"""

import tkinter as tk
from tkinter import ttk

import config
from i18n import tr
import music_midi_input
from pages.widgets import Modal, COLORS


class MidiInputDialog(Modal):
    def __init__(self, parent):
        super().__init__(parent, tr("录制 MIDI"), width=config.scaled(420), height=config.scaled(225))
        self.recorder = None
        self.poll = None
        self.devices = []

        row = ttk.Frame(self.body)
        row.pack(fill="x", pady=(4, 12))
        self.device = ttk.Combobox(row, state="readonly", exportselection=False)
        self.device.pack(side="left", fill="x", expand=True)
        self.scan = ttk.Button(row, text=tr("刷新设备"), command=self._refresh)
        self.scan.pack(side="right", padx=(8, 0))
        self.status = tk.Label(self.body, bg=COLORS["bg"], fg=COLORS["text"], anchor="w",
                               font=config.font("body"), wraplength=config.scaled(360), justify="left")
        self.status.pack(fill="x")

        self.record = ttk.Button(self.buttons, text=tr("开始录制"), style="Accent.TButton", command=self._start)
        self.record.pack(side="right")
        ttk.Button(self.buttons, text=tr("取消"), command=self.cancel).pack(side="right", padx=(0, 8))
        self.bind("<Destroy>", self._destroyed, add="+")
        self._refresh()

    def _refresh(self):
        self.devices = []
        try:
            self.devices = music_midi_input.input_devices()
            message = "" if self.devices else tr("未找到 MIDI 输入设备.")
        except (OSError, music_midi_input.MidiInputError) as error:
            message = tr("无法读取 MIDI 输入设备: {error}").format(error=error)
        # 序号区分 Windows 返回同名的端口。
        self.device["values"] = [f"{i + 1}. {name}" for i, name in self.devices]
        if self.devices:
            self.device.current(0)
        else:
            self.device.set("")
        self.record.config(state="normal" if self.devices else "disabled")
        self.status.config(text=message)

    def _start(self):
        index = self.device.current()
        if index < 0:
            return
        try:
            self.recorder = music_midi_input.MidiInput(self.devices[index][0])
        except (OSError, music_midi_input.MidiInputError) as error:
            self.status.config(text=str(error))
            return
        self.scan.config(state="disabled")
        self.device.config(state="disabled")
        self.record.config(text=tr("停止录制"), command=self._stop)
        self._update_status()

    def _update_status(self):
        self.poll = None
        started, error = self.recorder.status()
        if error:
            self._stop()
            return
        self.status.config(text=tr("正在录制 MIDI…") if started else tr("等待第一个音符…"))
        self.poll = self.after(100, self._update_status)

    def _stop(self):
        if self.poll is not None:
            self.after_cancel(self.poll)
            self.poll = None
        try:
            data = self.recorder.finish()
        except music_midi_input.MidiInputError as error:
            self._close_recorder()
            self.status.config(text=str(error))
            self.scan.config(state="normal")
            self.device.config(state="readonly")
            self.record.config(text=tr("开始录制"), command=self._start)
        else:
            self.finish(data)

    def _close_recorder(self):
        if self.recorder is not None:
            self.recorder.close()
            self.recorder = None

    def _destroyed(self, event):
        if event.widget is self:
            if self.poll is not None:
                self.after_cancel(self.poll)
                self.poll = None
            self._close_recorder()


def record_midi(parent):
    """关闭录制窗后把键盘与鼠标控制交还给评判窗。"""
    try:
        return MidiInputDialog(parent).wait()
    finally:
        if parent.winfo_exists():
            parent.grab_set()
