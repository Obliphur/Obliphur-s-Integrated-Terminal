"""设置 → 额外功能：AI 语音、Process Lasso 与音乐工具。"""

import tkinter as tk
from tkinter import filedialog, ttk

import config
import music_tools
import music_library
import toast
from config import COLORS
from i18n import tr
from ..voice_dialogs import build_voice_settings
from ..widgets import cfg, wrap_label


class ExtraSettingsMixin:
    def _build_extra_page(self, body):
        if self.comm is not None:
            build_voice_settings(self, body)
        self._build_external_lasso_card(body)
        self.music = music_tools.manager(self.data_dir or config.app_data_dir(), self.external)
        self.music_library = music_library.manager(self.data_dir or config.app_data_dir())
        card = COLORS["bg_card"]
        fr = tk.Frame(body, bg=card, highlightbackground=COLORS["border"], highlightthickness=1)
        fr.pack(fill="x", pady=(2, 8))
        tk.Label(fr, text=tr("音乐工具"), bg=card, fg=COLORS["text"],
                 font=config.font("body", bold=True)).pack(anchor="w", padx=12, pady=(10, 4))
        self.music_install = ttk.Button(fr, text=tr("一键安装音乐工具"), command=self._install_music)
        self.music_install.pack(anchor="w", padx=12, pady=4)
        self.music_labels, self.music_buttons = {}, {}
        for key, spec in music_tools.TOOLS.items():
            label = tk.Label(fr, bg=card, fg=COLORS["text_dim"], font=config.font("body"),
                             anchor="w", justify="left")
            label.pack(fill="x", padx=12, pady=(8, 2))
            wrap_label(label, 24)
            button = ttk.Button(fr, text=tr("安装 {tool}", tool=tr(spec.name)),
                                command=lambda k=key: self._install_music(k))
            button.pack(anchor="w", padx=12, pady=2)
            self.music_labels[key], self.music_buttons[key] = label, button
        self.music_convert = ttk.Button(fr, text=tr("转换文件…"), command=self._convert_music)
        self.music_convert.pack(anchor="w", padx=12, pady=(10, 4))
        self.music_import = ttk.Button(fr, text=tr("导入乐谱到曲库…"), command=self._import_music_score)
        self.music_import.pack(anchor="w", padx=12, pady=4)
        self.music_export_midi = ttk.Button(fr, text=tr("导出所选曲目为 MIDI…"), command=self._export_selected_midi)
        self.music_export_midi.pack(anchor="w", padx=12, pady=4)
        self.music_status = tk.Label(fr, bg=card, fg=COLORS["text_dim"], font=config.font("body"),
                                     anchor="w", justify="left")
        self.music_status.pack(fill="x", padx=12, pady=(4, 10))
        wrap_label(self.music_status, 24)
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

    def _install_music(self, tool=None):
        if not self.external.root:
            self._choose_external_root()
        if not self.external.root:
            return
        try:
            self.music.start_install(tool)
        except (OSError, ValueError, music_tools.MusicError) as error:
            toast.warn(str(error))
        self._refresh_extra_page()

    def _convert_music(self):
        source = filedialog.askopenfilename(parent=self, title=tr("选择乐谱或钢琴录音"),
                    filetypes=[(tr("乐谱或钢琴录音"), " ".join("*" + ext for spec in music_tools.TOOLS.values() for ext in spec.inputs))])
        if not source:
            return
        from pathlib import Path
        types = [("MIDI", "*.mid")]
        if Path(source).suffix.lower() not in music_tools.TOOLS["piano"].inputs:
            types.append(("MuseScore", "*.mscz"))
            if Path(source).suffix.lower() in music_tools.TOOLS["audiveris"].inputs:
                types.append(("MusicXML", "*.mxl"))
        destination = filedialog.asksaveasfilename(parent=self, title=tr("保存转换结果"),
                            initialfile=Path(source).stem + ".mid", defaultextension=".mid", filetypes=types)
        if not destination:
            return
        try:
            self.music.start_convert(source, destination)
        except (OSError, ValueError, music_tools.MusicError) as error:
            toast.warn(str(error))
        self._refresh_extra_page()

    def _import_music_score(self):
        source = filedialog.askopenfilename(parent=self, title=tr("选择要导入的乐谱"),
                    filetypes=[(tr("MuseScore 和 MusicXML"), "*.mscz *.mscx *.mxl *.musicxml *.xml")])
        if not source:
            return
        try:
            item = self.music_library.add(source)
            toast.info(tr("已加入曲库: {title}", title=item["title"]))
        except (OSError, ValueError, music_library.MusicLibraryError) as error:
            toast.warn(str(error))

    def _export_selected_midi(self):
        from pathlib import Path
        selected = self.music_library.selection()
        if not selected:
            toast.warn(tr("请先选择一首曲目"))
            return
        destination = filedialog.asksaveasfilename(parent=self, title=tr("保存 MIDI 文件"),
                    initialfile=Path(selected["title"]).stem + ".mid", defaultextension=".mid",
                    filetypes=[("MIDI", "*.mid")])
        if not destination:
            return
        try:
            self.music.start_performance(selected["path"], destination)
        except (OSError, ValueError, music_tools.MusicError) as error:
            toast.warn(str(error))
        self._refresh_extra_page()

    def _refresh_extra_page(self):
        if not hasattr(self, "music"):
            return
        if hasattr(self, "_voice_settings_refresh"):
            self._voice_settings_refresh()
        statuses = self.music.status()
        blocked = self.external.location_busy
        selected = self.music_library.selection()
        for key, info in statuses.items():
            state = tr("已就绪") if info["ready"] else tr("未安装")
            if key == "piano" and info["weights_ready"] and not info["ready"]:
                state = tr("权重已下载，运行环境未安装")
            cfg(self.music_labels[key], text=tr("{tool}: {state}", tool=tr(info["name"]), state=state))
            self.music_buttons[key].state(["disabled"] if blocked or info["ready"] else ["!disabled"])
        self.music_install.state(["disabled"] if blocked or all(s["ready"] for s in statuses.values()) else ["!disabled"])
        can_convert = any(statuses[key]["ready"] for key in music_tools.CONVERTERS)
        self.music_convert.state(["disabled"] if blocked or not can_convert else ["!disabled"])
        self.music_export_midi.state(["disabled"] if blocked or not selected or not statuses["musescore"]["ready"] else ["!disabled"])
        m = self.music
        if m.busy:
            name = tr(music_tools.TOOLS[m.current].name) if m.current in music_tools.TOOLS else ""
            progress = m.model.progress if m.current == "piano" and m.model.busy else m.progress
            if m.state == "installing":
                text = tr("正在安装 {tool}… {n}%", tool=name, n=progress) if 0 < progress < 100 else tr("正在安装 {tool}…", tool=name)
            else:
                text = tr("正在转换… {tool}", tool=name)
        elif m.state == "error":
            text = tr("音乐任务失败: {error}", error=m.error)
        elif isinstance(m.result, music_tools.MusicResult):
            text = tr("已保存: {path}", path=m.result.files[0])
        elif m.state == "ready":
            text = tr("安装完成")
        else:
            text = ""
        cfg(self.music_status, text=text, fg=COLORS["warn"] if m.state == "error" else COLORS["text_dim"])
