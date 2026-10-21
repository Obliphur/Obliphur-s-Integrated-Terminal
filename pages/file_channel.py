"""文件通道 (MECHANICS 15.6): 把文件 / 图片发给一台绑定的设备; 收到时问存到哪里."""

import os
import threading
import tkinter as tk
from tkinter import filedialog, ttk

import config
import toast
from config import COLORS
from i18n import tr


def fmt_size(n: int) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.2f} GB"


_WHY = {
    "old": "对方的 OIT 版本太旧, 收不了文件.",
    "size": "文件太大 (最多 1 GB).",
    "unpaired": "已经不是绑定的设备了.",
    "unsupported": "对方收不了文件.",
    "busy": "对方正忙, 稍后再试.",
}


class FileChannel:
    """发送对话框 + 收到文件时的提示. app 需要有 root, sync (devsync.SyncService), _on_main."""

    def __init__(self, app):
        self.app = app
        self.win = None

    # ---- 发送 ----

    def open(self):
        sync = self.app.sync
        peers = sync.peers()
        if not peers:
            toast.info(tr("还没有绑定的设备 (设置 → 基本设置 → 设备同步)."))
            return
        if self.win is not None and self.win.winfo_exists():
            self.win.lift()
            return
        bg = COLORS["bg"]
        w = tk.Toplevel(self.app.root, bg=bg)
        self.win = w
        w.title(tr("📁  文件通道"))
        w.transient(self.app.root)
        w.resizable(False, False)
        box = tk.Frame(w, bg=bg)
        box.pack(padx=22, pady=18, fill="both")
        tk.Label(box, text=tr("📁  文件通道"), bg=bg, fg=COLORS["text"],
                 font=config.font("h2", bold=True)).pack(anchor="w")

        online = {d["id"]: d for d in sync.device_states()}
        ids = list(peers)
        names = []
        for pid in ids:
            st = online.get(pid) or {}
            name = str(peers[pid].get("name") or st.get("name") or "?")
            names.append(name + ("" if st.get("online") else "  · " + tr("离线")))
        tk.Label(box, text=tr("发给"), bg=bg, fg=COLORS["text_dim"],
                 font=config.font("small")).pack(anchor="w", pady=(12, 2))
        who = ttk.Combobox(box, values=names, state="readonly", width=36)
        who.current(0)
        who.pack(anchor="w", fill="x")

        tk.Label(box, text=tr("文件 / 图片"), bg=bg, fg=COLORS["text_dim"],
                 font=config.font("small")).pack(anchor="w", pady=(12, 2))
        row = tk.Frame(box, bg=bg)
        row.pack(anchor="w", fill="x")
        path = tk.StringVar(value="")
        lbl = tk.Label(row, text=tr("还没有选择"), bg=bg, fg=COLORS["text"], font=config.font("body"),
                       anchor="w", width=34)
        lbl.pack(side="left", fill="x", expand=True)

        def pick():
            p = filedialog.askopenfilename(parent=w, title=tr("选择要发送的文件"))
            if p:
                path.set(p)
                lbl.config(text=f"{os.path.basename(p)}  ({fmt_size(os.path.getsize(p))})")

        ttk.Button(row, text=tr("选择文件…"), command=pick).pack(side="left", padx=(8, 0))

        btns = tk.Frame(box, bg=bg)
        btns.pack(anchor="e", pady=(16, 0))
        send_btn = ttk.Button(btns, text=tr("发送"), style="Accent.TButton")
        send_btn.pack(side="right")
        ttk.Button(btns, text=tr("关闭"), command=w.destroy).pack(side="right", padx=(0, 8))

        def send():
            p = path.get()
            if not p or not os.path.isfile(p):
                toast.warn(tr("先选择一个文件."))
                return
            i = who.current()
            pid, name = ids[i], str(peers[ids[i]].get("name") or "?")
            send_btn.state(["disabled"])
            threading.Thread(target=self._send, args=(pid, name, p, send_btn), daemon=True).start()

        send_btn.config(command=send)

    def _send(self, pid: str, name: str, path: str, btn):
        try:
            why = self.app.sync.send_file(pid, path)
            err = ""
        except Exception as e:      # noqa: BLE001 —— 告诉用户
            why, err = "error", str(e) or type(e).__name__

        def done():
            try:
                btn.state(["!disabled"])
            except tk.TclError:
                pass
            if why == "sent":
                toast.success(tr("已发给 {who}: {name}. 等对方接收.", who=name, name=os.path.basename(path)))
            elif why == "error":
                toast.warn(tr("发送失败: {e}", e=err))
            else:
                toast.warn(tr(_WHY.get(why, "对方没有接收.")))
        self.app._on_main(done)

    # ---- 接收 ----

    def offered(self, pid: str, who: str, o: dict) -> str:
        """别的设备要发文件过来 (界面线程): 弹出提示, 用户选好存到哪里再去取. 返回 "" (先答应, 取不取由用户定)."""
        msg = tr("{who} 发来文件: {name} ({size})", who=who, name=o["name"], size=fmt_size(o["size"]))
        toast.notice(tr("📁  收到文件"), msg, [
            (tr("保存…"), lambda: self._save(pid, who, o), True), (tr("拒绝"), None, False)])
        return ""

    def _save(self, pid: str, who: str, o: dict):
        ext = os.path.splitext(o["name"])[1]
        dest = filedialog.asksaveasfilename(
            parent=self.app.root, title=tr("保存 {who} 发来的文件", who=who), initialfile=o["name"],
            defaultextension=ext, filetypes=[(ext or "*", "*" + ext), ("*", "*")] if ext else [("*", "*")])
        if not dest:
            return
        toast.info(tr("正在接收 {name}…", name=o["name"]))

        def run():
            try:
                self.app.sync.fetch_file(pid, o, dest)
                err = ""
            except Exception as e:      # noqa: BLE001
                err = str(e) or type(e).__name__
            self.app._on_main(lambda: toast.warn(tr("接收失败: {e}", e=err)) if err
                              else toast.success(tr("已保存: {path}", path=dest)))
        threading.Thread(target=run, daemon=True).start()

    def delivered(self, who: str, name: str):
        toast.success(tr("{who} 已收到 {name}.", who=who, name=name))
