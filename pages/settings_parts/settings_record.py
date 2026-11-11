"""设置页的录像模块: 摄像头权限 / 选择摄像头 / 录像文件列表与播放 / 删除。

从 pages/settings_page.py 拆出来的 mixin; 依赖方向: settings_page.py 导入它们, 它们从不导入 settings_page.py.
"""

import os
import threading
import tkinter as tk
from tkinter import ttk
import config
import recorder
import toast
from config import COLORS
from i18n import tr

from ..widgets import (cfg)


class RecordSettingsMixin:
    def _build_record_page(self, body):
        card = COLORS["bg_card"]
        small = config.font("small")
        fr, head = self._card(body, "📷  摄像头权限")
        self._track(ttk.Button(head, text=tr("打开系统设置"),
                               command=lambda: self._open_uri("ms-settings:privacy-webcam")),
                    "打开系统设置").pack(side="right")
        self._track(ttk.Button(head, text=tr("重新检查"), command=self._check_camera),
                    "重新检查").pack(side="right", padx=(0, 6))
        self.lbl_cam = tk.Label(fr, text="", bg=card, fg=COLORS["text"], font=small, anchor="w", justify="left")
        self.lbl_cam.pack(fill="x", padx=12, pady=(2, 2))

        fr, head = self._card(body, "📷  选择摄像头")
        self._track(ttk.Button(head, text=tr("刷新"), command=self._check_camera),
                    "刷新").pack(side="right")
        self.cam_var = tk.StringVar()
        self.cam_box = ttk.Combobox(fr, textvariable=self.cam_var, state="readonly", width=44)
        self.cam_box.pack(fill="x", padx=12, pady=(2, 4))
        self.cam_box.bind("<<ComboboxSelected>>", lambda _e: self._pick_camera())
        self.lbl_cam_sel = tk.Label(fr, text="", bg=card, fg=COLORS["text_dim"], font=small,
                                    anchor="w", justify="left")
        self.lbl_cam_sel.pack(fill="x", padx=12, pady=(0, 2))


    def _build_record_lists(self, body):
        card = COLORS["bg_card"]
        # 只展示最终 (合并 / 同步好) 的录像, 不展示片段 (MECHANICS 18.8)
        for kind, title in (("camera", "📷  摄像头 (目光)"), ("screen", "🖥  屏幕 (刻痕)")):
            fr, head = self._card(body, title)
            self._track(ttk.Button(head, text=tr("打开文件夹"), command=lambda k=kind: self._open_folder(k)),
                        "打开文件夹").pack(side="right")
            lbl = tk.Label(head, text="", bg=card, fg=COLORS["text_dim"], font=config.font("small"))
            lbl.pack(side="right", padx=(0, 10))
            tk.Label(fr, text=tr("录像超过 3 天会自动清除."), bg=card, fg=COLORS["text_dim"], font=config.font("small"),
                     anchor="w").pack(fill="x", padx=12)
            rows = tk.Frame(fr, bg=card)
            rows.pack(fill="x", padx=12, pady=(2, 10))
            self._rec_lists[kind] = (lbl, rows)
        self.areas["record"].bind_children()

    @staticmethod
    def _open_uri(uri):
        try:
            os.startfile(uri)
        except OSError:
            pass

    def _open_folder(self, kind):
        if not self.data_dir:
            return
        d = recorder.merged_dir(self.data_dir, kind)
        os.makedirs(d, exist_ok=True)
        self._open_uri(d)

    def _check_camera(self):
        """摄像头: Windows 隐私设置 + 本机有哪几台 (在后台线程里查, 要初始化 Media Foundation)."""
        if self._cam_state == "checking" or not hasattr(self, "lbl_cam"):
            return
        self._cam_state = "checking"
        cfg(self.lbl_cam, text=tr("检查中…"), fg=COLORS["text_dim"])

        def run():
            try:
                res = (recorder.camera_consent(), recorder.camera_devices())
            except Exception:      # noqa: BLE001
                res = ("", [])
            try:
                self.after(0, lambda: self._show_camera(*res))
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    def _show_camera(self, consent, devices):
        self._cam_state = None
        self._cam_devices = list(devices or [])
        n = len(self._cam_devices)
        if consent == "deny":
            text, color = tr("✗ Windows 不允许桌面应用使用摄像头"), COLORS["danger"]
        elif not n:
            text, color = tr("✗ 没有找到摄像头"), COLORS["warn"]
        else:
            text, color = tr("✓ 可以使用 · 摄像头 {n} 个", n=n), COLORS["success"]
        cfg(self.lbl_cam, text=text, fg=color)
        self._refresh_cam_choices(force=True)

    def _phone_cameras(self) -> list:
        """绑定的、此刻在线的手机 (目光可以用它的摄像头): [(设备 id, 名字)]."""
        out = []
        if self.sync is None:
            return out
        try:
            plat = self.sync.peers()
            for s in self.sync.device_states():
                if not s.get("online"):
                    continue
                p = plat.get(s["id"]) or {}
                if str(p.get("platform", "")).lower().startswith("android"):
                    out.append((str(s["id"]), str(s.get("name") or p.get("name") or "?")))
        except Exception:      # noqa: BLE001
            pass
        return out

    def _camera_label(self, value: str) -> str:
        value = str(value or "")
        if value.startswith("pc|"):
            for d in self._cam_devices:
                if d["id"] == value[3:]:
                    return d["name"]
            return tr("本机摄像头")
        if value.startswith("phone|"):
            pid = value[6:]
            try:
                for s in (self.sync.device_states() if self.sync is not None else []):
                    if s["id"] == pid:
                        return str(s.get("name") or "?")
            except Exception:      # noqa: BLE001
                pass
            return tr("手机")
        return tr("自动")

    def _build_cam_choices(self) -> tuple:
        """返回 (下拉框的 [(值, 文字)], 选的那台现在是不是不在, 在线手机 [(id, 名字)])."""
        phones = self._phone_cameras()
        choices = [("", tr("自动 (有本机摄像头就用本机)"))]
        for i, d in enumerate(self._cam_devices):
            choices.append((f"pc|{d['id']}", tr("本机 {n} · {name}", n=i + 1, name=d["name"])))
        for i, (did, name) in enumerate(phones):
            choices.append((f"phone|{did}", tr("手机 {n} · {name}", n=i + 1, name=name)))
        cur = str(self.sm.value("REC_CAMERA") or "")
        missing = bool(cur) and not any(v == cur for v, _l in choices)
        if missing:
            # 选过的那台现在不在 (拔掉了 / 手机不在线): 照样显示, 方便看出是哪一台
            choices.append((cur, tr("不可用: {name}", name=self._camera_label(cur))))
        return choices, missing, phones

    def _refresh_cam_choices(self, force: bool = False):
        if not hasattr(self, "cam_box") or not hasattr(self, "lbl_cam_sel"):
            return
        choices, missing, phones = self._build_cam_choices()
        cur = str(self.sm.value("REC_CAMERA") or "")
        sig = (tuple(choices), cur)
        if not force and sig == self._cam_sig:
            return
        self._cam_sig = sig
        self._cam_choices = choices
        labels = [lab for _v, lab in choices]
        if list(self.cam_box.cget("values")) != labels:
            self.cam_box.config(values=labels)
        label = next((lab for v, lab in choices if v == cur), labels[0])
        if self.cam_var.get() != label:
            self.cam_var.set(label)
        cfg(self.lbl_cam_sel,
            text=tr("本机 {n} 个摄像头 · 在线手机 {m} 台", n=len(self._cam_devices), m=len(phones)),
            fg=COLORS["danger"] if missing else COLORS["text_dim"])

    def _pick_camera(self):
        """选了另一台摄像头: 即时生效 (每台设备各自设置, 不同步)."""
        label = self.cam_var.get().strip()
        value = next((v for v, lab in self._cam_choices if lab == label), None)
        if value is None or value == str(self.sm.value("REC_CAMERA") or ""):
            return
        status, msg = self.sm.stage("REC_CAMERA", value)
        if status == "invalid":
            toast.warn(msg)
            self._refresh_cam_choices(force=True)
            return
        self.refresh()
        self._refresh_cam_choices(force=True)
        if self.on_changed:
            self.on_changed(status, "REC_CAMERA")
        if status in ("instant", "staged"):
            toast.info(tr(msg))

    def refresh_records(self):
        """录像文件的列表 (文件变了才重画)."""
        if self._tab != "record":
            return
        self._refresh_cam_choices()
        if not self.data_dir:
            return
        card = COLORS["bg_card"]
        small = config.font("small")
        for kind, (lbl, rows) in self._rec_lists.items():
            files = recorder.list_merged(self.data_dir, kind)
            live = self.recording(kind)
            sig = (live, tuple((f["name"], f["size"] // (1 << 20)) for f in files))
            if self._rec_sig.get(kind) == sig:
                continue
            self._rec_sig[kind] = sig
            total = sum(f["size"] for f in files)
            cfg(lbl, text=tr("{n} 个 · {size}", n=len(files), size=_fmt_size(total)))
            for w in rows.winfo_children():
                w.destroy()
            if not files:
                tk.Label(rows, text=tr("● 录制中, 结束后合并并同步.") if live else tr("还没有录像."),
                         bg=card, fg=COLORS["danger"] if live else COLORS["text_dim"], font=small,
                         anchor="w").pack(fill="x")
            for f in files[:60]:
                row = tk.Frame(rows, bg=card)
                row.pack(fill="x", pady=1)
                when = f["start"].strftime("%Y-%m-%d %H:%M:%S") if f["start"] else f["name"]
                tk.Label(row, text=f"{when}   {_fmt_size(f['size'])}", bg=card,
                         fg=COLORS["text"], font=small, anchor="w").pack(side="left")
                ttk.Button(row, text=tr("删除"),
                           command=lambda p=f["path"], k=kind: self._delete_record(p, k)).pack(side="right")
                ttk.Button(row, text=tr("▶ 播放"), command=lambda p=f["path"]: self._play_record(p)).pack(
                    side="right", padx=(0, 6))
            if len(files) > 60:
                tk.Label(rows, text=tr("还有 {n} 个, 在文件夹里查看.", n=len(files) - 60), bg=card,
                         fg=COLORS["text_dim"], font=small, anchor="w").pack(fill="x")
        self.areas["record"].bind_children()

    def _play_record(self, path):
        """录像是加密的 (.oitv): 解密到临时文件夹再用系统默认的播放器打开."""
        try:
            recorder.play(path)
        except Exception as e:      # noqa: BLE001
            toast.warn(tr("打不开这个录像: {e}", e=e))

    def _delete_record(self, path, kind):
        if not toast.ask(tr("删除这个录像?") + "\n" + tr("绑定的设备上也会删掉."), title=tr("删除录像"), danger=True):
            return
        self.on_delete_record(recorder.cid_of(os.path.basename(path)), kind)
        self._rec_sig = {}
        self.refresh_records()

    def refresh_sync(self):
        """设备同步那一块单独刷新 (每秒一次, 只在看着基本设置时)."""
        if self.sync_panel is not None and self._tab == "basic":
            self.sync_panel.refresh()




def _fmt_size(n: int) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.2f} GB"
