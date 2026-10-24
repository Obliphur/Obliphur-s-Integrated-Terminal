"""
设备同步 (MECHANICS.md 第 15 节): 本机信息, 寻找 / 按地址连接, 绑定, 已绑定的设备.
显示在 设置 → 基本设置 (第一页).
"""

import tkinter as tk
from datetime import datetime
from tkinter import ttk

import config
import toast
from config import COLORS
from i18n import tr
from .widgets import cfg, show_pack, hide_pack


def sync_error_text(err) -> str:
    """同步 / 绑定出错的原因, 换成看得懂的话."""
    e = str(err or "")
    low = e.lower()
    known = {"not-proxy": "本机不是 Proxy 断网方式", "peer-not-proxy": "对方不是 Proxy 断网方式",
             "unpaired": "对方已经解除了绑定 (或重置过), 在这里解除绑定后重新绑定"}
    if e in known:
        return tr(known[e])
    if "timed out" in low or "timeout" in low:
        return tr("对方没有响应: 对方的 OIT 可能没在运行, 或者熄屏后被系统暂停了 (手机上打开 OIT 再试)")
    if "refused" in low or "10061" in low:
        return tr("对方没有在等连接: OIT 没在运行, 或者端口不对")
    return e



class SyncPanel(tk.Frame):

    def __init__(self, master, sync, bg=None):
        super().__init__(master, bg=bg or COLORS["bg"])
        self.sync = sync
        self._found_ids = []
        self._peer_ids = []
        self._shown = {}
        self._build(self)

    def _build(self, body):
        bg, card = COLORS["bg"], COLORS["bg_card"]
        tk.Label(body, text=tr("🔗  设备同步"), bg=bg, fg=COLORS["text"],
                 font=config.font("body", bold=True)).pack(anchor="w", pady=(4, 2))
        fr = tk.Frame(body, bg=card, highlightbackground=COLORS["border"], highlightthickness=1)
        fr.pack(fill="x", pady=(0, 8))
        inner = tk.Frame(fr, bg=card)
        inner.pack(fill="x", padx=14, pady=10)
        self.lbl_sync_me = tk.Label(inner, text="", bg=card, fg=COLORS["text_dim"],
                                    font=config.font("small"), anchor="w", justify="left")
        self.lbl_sync_me.pack(fill="x")

        row = tk.Frame(inner, bg=card)
        row.pack(fill="x", pady=(8, 4))
        ttk.Button(row, text=tr("🔍 寻找设备"), command=self._sync_discover).pack(side="left")
        self.var_sync_addr = tk.StringVar()
        ent = ttk.Entry(row, textvariable=self.var_sync_addr, width=22)
        ent.pack(side="left", padx=(8, 0))
        ent.bind("<Return>", lambda _e: self._sync_connect())
        ttk.Button(row, text=tr("按地址连接"), command=self._sync_connect).pack(side="left", padx=(6, 0))

        self.lb_found = tk.Listbox(inner, height=3, activestyle="none", exportselection=False,
                                   font=config.font("small"), highlightbackground=COLORS["border"])
        self.lb_found.pack(fill="x", pady=(4, 4))
        row2 = tk.Frame(inner, bg=card)
        row2.pack(fill="x")
        ttk.Button(row2, text=tr("绑定 · 本机为母端"), style="Accent.TButton",
                   command=lambda: self._sync_pair(True)).pack(side="left")
        ttk.Button(row2, text=tr("绑定 · 对方为母端"),
                   command=lambda: self._sync_pair(False)).pack(side="left", padx=(6, 0))
        self.btn_pair_cancel = ttk.Button(row2, text=tr("取消"), command=self._sync_cancel)
        self.lbl_pair = tk.Label(inner, text="", bg=card, fg=COLORS["accent"],
                                 font=config.font("body", bold=True), anchor="w", justify="left")
        self.lbl_pair.pack(fill="x", pady=(6, 0))

        tk.Label(inner, text=tr("已绑定的设备"), bg=card, fg=COLORS["text"],
                 font=config.font("small", bold=True), anchor="w").pack(fill="x", pady=(10, 2))
        self.lb_peers = tk.Listbox(inner, height=3, activestyle="none", exportselection=False,
                                   font=config.font("small"), highlightbackground=COLORS["border"])
        self.lb_peers.pack(fill="x")
        row3 = tk.Frame(inner, bg=card)
        row3.pack(fill="x", pady=(4, 0))
        ttk.Button(row3, text=tr("立即同步"), command=self.sync.sync_now).pack(side="left")
        ttk.Button(row3, text=tr("解除绑定"), command=self._sync_unpair).pack(side="left", padx=(6, 0))

    def _sync_discover(self):
        import threading

        def go():
            try:
                self.sync.discover(2.5)
            except OSError:
                pass
        threading.Thread(target=go, daemon=True).start()
        toast.info(tr("正在寻找同一局域网里的 OIT…"))

    def _sync_connect(self):
        import threading
        text = self.var_sync_addr.get().strip()
        if not text:
            return
        host, _, port = text.partition(":")
        try:
            port = int(port) if port else 17930
        except ValueError:
            toast.warn(tr("地址格式: 192.168.1.5 或 192.168.1.5:17930"))
            return

        def go():
            try:
                d = self.sync.hello(host, port)
                self.sync._note_found(d, host)
            except Exception as e:
                self.after(0, lambda: toast.warn(tr("连接失败: {e}", e=sync_error_text(e))))
        threading.Thread(target=go, daemon=True).start()

    def _sync_pair(self, mother_is_me: bool):
        sel = self.lb_found.curselection()
        if not sel or sel[0] >= len(self._found_ids):
            toast.warn(tr("先在上面选一台设备."))
            return
        if getattr(config, "NET_MODE", "adapter") not in ("proxy", "proxy_all"):
            toast.warn(tr("两端都使用 Proxy 断网方式时才能同步."))
            return
        d = self.sync.found.get(self._found_ids[sel[0]])
        if not d:
            return
        if not mother_is_me and not toast.ask(
                tr("本机的设置, 名单和记录将被对方的数据替换."), title=tr("绑定"), danger=True):
            return
        self.sync.start_pairing(d["addr"], d["port"], mother_is_me)

    def _sync_cancel(self):
        self.sync.cancel_pairing()

    def _sync_unpair(self):
        """解除绑定 (MECHANICS 15.1): 冷静期 48 小时, 期间再按一次取消; 到时间后解除, 并清空那台设备上的全部数据."""
        sel = self.lb_peers.curselection()
        if not sel or sel[0] >= len(self._peer_ids):
            toast.warn(tr("先在上面选一台设备."))
            return
        pid = self._peer_ids[sel[0]]
        p = self.sync.peers().get(pid, {})
        name = p.get("name", "?")
        if self.sync.unbind_at(pid):
            if toast.ask(tr("取消解除和 {name} 的绑定?", name=name), title=tr("✕  取消解除绑定")):
                self.sync.cancel_unbind(pid)
                toast.info(tr("已取消解除绑定."))
            return
        if p.get("last_err") == "unpaired":
            msg = tr("解除和 {name} 的绑定?", name=name)          # 对方已经解除了 / 重置过: 直接解除
        else:
            msg = tr("48 小时后解除和 {name} 的绑定, 并清空 {name} 上的全部数据. 期间可以取消.", name=name)
        if toast.ask(msg, title=tr("解除绑定"), danger=True):
            self.sync.request_unbind(pid)

    def refresh(self):
        s = self.sync
        if s is None:
            return
        proxy_on = getattr(config, "NET_MODE", "adapter") in ("proxy", "proxy_all")
        me = tr("本机: {name} · 端口 {port}", name=s.my_name, port=s.port or "-")
        if not proxy_on:
            me += "\n" + tr("两端都使用 Proxy 断网方式时才能同步.")
        # 防火墙挡住入站连接时, 别的设备找不到本机 / 连不进来 (firewall.py)
        fw = getattr(s, "firewall", None) or {}
        fw_bad = bool(fw.get("blockers") or fw.get("error"))
        if fw.get("blockers"):
            me += "\n" + tr("Windows 防火墙里有阻止 Python 入站连接的规则, 其他设备连不进本机: {names}",
                            names=", ".join(fw["blockers"][:4]))
        elif fw.get("error"):
            me += "\n" + tr("没能在 Windows 防火墙里放行同步端口: {e}", e=fw["error"][:120])
        cfg(self.lbl_sync_me, text=me,
            fg=COLORS["text_dim"] if proxy_on and not fw_bad else COLORS["warn"])

        peers = s.peers()
        found = [d for d in s.found.values() if d["id"] not in peers]
        ids = [d["id"] for d in found]
        rows = [f"{d['name']}  ({d['platform']})  {d['addr']}:{d['port']}"
                + ("" if d.get("net_mode") == "proxy" else "  · " + tr("不是 Proxy 模式")) for d in found]
        if self._shown.get("_found") != rows:
            self._shown["_found"] = rows
            self._found_ids = ids
            self.lb_found.delete(0, "end")
            for r in rows:
                self.lb_found.insert("end", r)

        o = s.outgoing or {}
        st = o.get("state")
        text = {
            "connecting": tr("正在连接…"),
            "waiting": tr("配对码 {code} · 请在 {name} 上确认", code=o.get("code"), name=o.get("name")),
            "accepted": tr("已绑定 {name}.", name=o.get("name")),
            "rejected": tr("对方拒绝了绑定."),
            "timeout": tr("对方没有在 2 分钟内确认."),
            "cancelled": tr("已取消."),
            "error": tr("绑定失败: {e}", e=sync_error_text(o.get("error"))),
        }.get(st, "")
        cfg(self.lbl_pair, text=text)
        if st in ("connecting", "waiting"):
            show_pack(self.btn_pair_cancel, side="left", padx=(6, 0))
        else:
            hide_pack(self.btn_pair_cancel)

        now = datetime.now().timestamp() * 1000
        status = {st_["id"]: st_ for st_ in s.peer_status()}
        prow, pids = [], []
        for pid, p in peers.items():
            role = tr("母端") if p.get("mother") == pid else (
                tr("本机是母端") if p.get("mother") == s.my_id else "")
            if p.get("last_err"):
                when = tr("同步失败: {e}", e=sync_error_text(p["last_err"]))
            elif p.get("last_ok"):
                when = tr("上次同步 {t}", t=datetime.fromtimestamp(p["last_ok"] / 1000).strftime("%H:%M:%S"))
            else:
                when = tr("还没同步过")
            at = s.unbind_at(pid)
            if at:
                when = tr("将在 {t} 解除绑定", t=datetime.fromtimestamp(at / 1000).strftime("%m-%d %H:%M"))
            live = ""
            stt = status.get(pid)
            if stt and now - float(stt.get("ts", 0)) < 60_000:
                live = {"running": tr("专注中…"), "break": tr("休息中 ☕")}.get(stt.get("timer"), "")
            prow.append("  ·  ".join(x for x in (f"{p['name']} ({p['platform']})", role, when, live) if x))
            pids.append(pid)
        if self._shown.get("_peers") != prow:
            self._shown["_peers"] = prow
            self._peer_ids = pids
            self.lb_peers.delete(0, "end")
            for r in prow:
                self.lb_peers.insert("end", r)

