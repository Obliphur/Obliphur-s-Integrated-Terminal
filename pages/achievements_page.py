import tkinter as tk
from tkinter import ttk

import config
from config import COLORS
from i18n import tr
from achievements import ACHIEVEMENTS, BY_ID, is_english_name
from .widgets import (flavor, cfg, wrap_label, show_pack, hide_pack,
                      ScrollArea, smooth)


def ach_name(aid: str) -> str:
    name = BY_ID[aid][1]
    return name if is_english_name(name) else tr(name)


def ach_text(aid: str) -> str:
    text = BY_ID[aid][2]
    return text if is_english_name(text) else tr(text)


class AchievementsPanel(tk.Frame):
    """成就卡片列表: 每个成就一张卡片. 没达成的只显示名字 (隐藏成就连名字也不显示),
    不显示进度和条件. 标题和已达成的个数由成就页画."""

    def __init__(self, master, store, bg=None):
        bg = bg or COLORS["bg"]
        super().__init__(master, bg=bg)
        self.store = store

        self.cards = {}
        for aid, *_ in ACHIEVEMENTS:
            self.cards[aid] = self._card(self, aid)

    def _card(self, parent, aid):
        card = COLORS["bg_card"]
        fr = tk.Frame(parent, bg=card, highlightbackground=COLORS["border"], highlightthickness=1)
        fr.pack(fill="x", pady=(0, 6))
        icon = tk.Label(fr, text="🔒", bg=card, font=config.font("h2"), width=2)
        icon.pack(side="left", padx=(12, 6), pady=8, anchor="n")
        col = tk.Frame(fr, bg=card)
        col.pack(side="left", fill="x", expand=True, padx=(0, 12), pady=8)
        top = tk.Frame(col, bg=card)
        top.pack(fill="x")
        name = tk.Label(top, text="", bg=card, font=config.font("body", bold=True), anchor="w")
        name.pack(side="left")
        when = tk.Label(top, text="", bg=card, fg=COLORS["text_dim"], font=config.font("small"))
        when.pack(side="right")
        text = tk.Label(col, text="", bg=card, fg=COLORS["text"], font=config.font("small"),
                        anchor="w", justify="left")
        wrap_label(text, 24)
        note_text = BY_ID[aid][4]
        note = flavor(col, tr(note_text), bg=card) if note_text else None
        return {"icon": icon, "name": name, "when": when, "text": text, "note": note}

    def refresh(self):
        s = self.store
        for aid, _name, _text, hidden, _note in ACHIEVEMENTS:
            c = self.cards[aid]
            got = s.at(aid)
            if got:
                cfg(c["icon"], text="🏆", fg=COLORS["accent"])
                cfg(c["name"], text=ach_name(aid), fg=COLORS["text_strong"])
                cfg(c["when"], text=f"{got:%Y-%m-%d}")
                cfg(c["text"], text=ach_text(aid))
                show_pack(c["text"], fill="x", pady=(2, 0))
                if c["note"] is not None:
                    show_pack(c["note"], fill="x", pady=(6, 0))
            else:
                cfg(c["icon"], text="🔒", fg=COLORS["text_dim"])
                cfg(c["name"], text="???" if hidden else ach_name(aid), fg=COLORS["text_dim"])
                cfg(c["when"], text="")
                hide_pack(c["text"])
                if c["note"] is not None:
                    hide_pack(c["note"])


class AchievementsPage(tk.Frame):
    """成就页 (MECHANICS 17): 单独一页, 由 设置 → 基本设置 的"🏆 成就"按钮打开.
    页面上方显示已达成的个数, 右上方"← 返回设置"回到设置 (和手机版一样)."""

    def __init__(self, master, store, on_back=None):
        super().__init__(master, bg=COLORS["bg"])
        self.store = store
        self.on_back = on_back
        bg = COLORS["bg"]

        hdr = tk.Frame(self, bg=bg)
        hdr.pack(fill="x", padx=28, pady=(18, 4))
        tk.Label(hdr, text=tr("🏆  成就"), bg=bg, fg=COLORS["text"],
                 font=config.font("h1", bold=True)).pack(side="left")
        self.btn_back = ttk.Button(hdr, text=tr("← 返回设置"), command=self._back)
        self.btn_back.pack(side="right")
        self.lbl_count = tk.Label(hdr, text="", bg=bg, fg=COLORS["accent"],
                                  font=config.font("body", bold=True))
        self.lbl_count.pack(side="right", padx=(0, 12))

        self.area = ScrollArea(self, height=420, bg=bg)
        self.area.pack(fill="both", expand=True, padx=26, pady=(6, 18))
        self.panel = AchievementsPanel(self.area.inner, store, bg=bg)
        self.panel.pack(fill="x")
        self.area.bind_children()

    def _back(self):
        if self.on_back is not None:
            self.on_back()

    @smooth
    def show(self):
        self.refresh()
        self.area.scroll_to_top()

    @smooth
    def refresh(self):
        self.panel.refresh()
        cfg(self.lbl_count, text=tr("已达成 {n}", n=len(self.store.unlocked)))


class AchievementPopups:
    """达成提示: 浮在 OIT 窗口右上角, 只能手动关闭 (✕); 点提示本身打开成就页 (同时关掉它).
    同时最多显示几个, 其余的排队; 程序关闭时还开着的, 下次打开再显示."""

    MAX_VISIBLE = 4

    def __init__(self, root, store, on_open):
        self.root, self.store, self.on_open = root, store, on_open
        self.wins = {}                # id -> Toplevel
        self._job = None
        self.hold = lambda: False     # 合约的黑雾 (MECHANICS 18.3): 达成提示等合约结束后再弹出
        root.bind("<Configure>", self._on_root_configure, add="+")
        root.bind("<Unmap>", self._on_root_unmap, add="+")
        root.bind("<Map>", self._on_root_map, add="+")

    def sync(self):
        """把还没被关掉的达成提示显示出来 (最多 MAX_VISIBLE 个)."""
        if self.hold():
            self.close_all_windows()
            return
        for aid in list(self.wins):
            if aid not in self.store.unseen:
                self._destroy(aid)
        for aid in self.store.unseen:
            if len(self.wins) >= self.MAX_VISIBLE:
                break
            if aid not in self.wins:
                self.wins[aid] = self._make(aid)
        self._place()

    def _make(self, aid):
        bg = COLORS["bg_card"]
        w = tk.Toplevel(self.root, bg=COLORS["accent"])
        w.withdraw()
        w.overrideredirect(True)
        w.transient(self.root)
        inner = tk.Frame(w, bg=bg, cursor="hand2")
        inner.pack(fill="both", expand=True, padx=config.scaled(2), pady=config.scaled(2))
        top = tk.Frame(inner, bg=bg)
        top.pack(fill="x", padx=(config.scaled(14), config.scaled(4)), pady=(config.scaled(6), 0))
        head = tk.Label(top, text=tr("🏆  达成成就"), bg=bg, fg=COLORS["accent"],
                        font=config.font("small", bold=True), anchor="w")
        head.pack(side="left")
        close = tk.Label(top, text="✕", bg=bg, fg=COLORS["text_dim"], font=config.font("body"),
                         cursor="hand2", padx=config.scaled(6))
        close.pack(side="right")
        name = tk.Label(inner, text=ach_name(aid), bg=bg, fg=COLORS["text_strong"],
                        font=config.font("h2", bold=True), anchor="w")
        name.pack(fill="x", padx=config.scaled(14))
        text = tk.Label(inner, text=ach_text(aid), bg=bg, fg=COLORS["text"], font=config.font("small"),
                        anchor="w", justify="left", wraplength=config.scaled(300))
        text.pack(fill="x", padx=config.scaled(14), pady=(config.scaled(2), config.scaled(12)))
        for x in (inner, top, head, name, text):
            x.bind("<Button-1>", lambda e, a=aid: self._open(a))
        close.bind("<Button-1>", lambda e, a=aid: self.close(a))
        return w

    def _place(self):
        try:
            if not self.wins or self.root.state() in ("iconic", "withdrawn"):
                return
            self.root.update_idletasks()
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            right = rx + self.root.winfo_width() - config.scaled(16)
            y = ry + config.scaled(16)
            for aid in self.store.unseen:
                w = self.wins.get(aid)
                if w is None:
                    continue
                w.update_idletasks()
                ww = max(config.scaled(340), w.winfo_reqwidth())
                wh = w.winfo_reqheight()
                w.geometry(f"{ww}x{wh}+{max(0, right - ww)}+{y}")
                w.deiconify()
                w.lift()
                y += wh + config.scaled(8)
        except tk.TclError:
            pass

    def _open(self, aid):
        self.close(aid)
        self.on_open()

    def close(self, aid):
        self._destroy(aid)
        self.store.mark_seen(aid)
        self.sync()

    def _destroy(self, aid):
        w = self.wins.pop(aid, None)
        if w is not None:
            try:
                w.destroy()
            except tk.TclError:
                pass

    def close_all_windows(self):
        """关闭程序 / 重置: 只拆掉窗口, 不算看过 (下次打开还显示)."""
        for aid in list(self.wins):
            self._destroy(aid)

    def _on_root_configure(self, event):
        if event.widget is self.root and self.wins:
            if self._job:
                self.root.after_cancel(self._job)
            self._job = self.root.after(30, self._after_move)

    def _after_move(self):
        self._job = None
        self._place()

    def _on_root_unmap(self, event):
        if event.widget is self.root:
            for w in self.wins.values():
                try:
                    w.withdraw()
                except tk.TclError:
                    pass

    def _on_root_map(self, event):
        if event.widget is self.root:
            self.root.after(50, self._place)
