

import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta

import config
from config import COLORS
from i18n import tr
from .widgets import cfg


class HourLogPage(tk.Frame):

    def __init__(self, master, data_manager, on_text_changed=None, on_toggle=None,
                 on_open_settings=None, locked=None):
        super().__init__(master, bg=COLORS["bg"])
        self.dm = data_manager
        # 合约 (MECHANICS 18): 和合约重叠的小时, 进入下一个时间段之后就不能再改
        self.locked = locked or (lambda start, now: False)
        self.on_text_changed = on_text_changed
        self.on_toggle = on_toggle
        self.on_open_settings = on_open_settings
        self.view_date = data_manager.today_date
        self.rows = {}
        self._build()


    def _build(self):
        bg, card = COLORS["bg"], COLORS["bg_card"]

        hdr = tk.Frame(self, bg=bg)
        hdr.pack(fill="x", padx=28, pady=(18, 4))

        left = tk.Frame(hdr, bg=bg)
        left.pack(side="left", fill="x", expand=True, anchor="nw")
        tk.Label(
            left, text=tr("📝  整点记录"), bg=bg, fg=COLORS["text"],
            font=config.font("h1", bold=True)
        ).pack(anchor="w")

        right = tk.Frame(hdr, bg=bg)
        right.pack(side="right", anchor="ne", pady=(2, 0))
        self.lbl_state = tk.Label(
            right, text="", bg=bg, font=config.font("body", bold=True)
        )
        self.lbl_state.pack(side="right", padx=(8, 0))
        self.btn_settings = ttk.Button(
            right, text=tr("⚙  设置"), command=self._open_settings
        )
        self.btn_settings.pack(side="right")

        self.lbl_date = tk.Label(
            self, text="", bg=bg, fg=COLORS["accent"],
            font=config.font("body", bold=True), anchor="w"
        )
        self.lbl_date.pack(fill="x", padx=28, pady=(8, 6))

        body = tk.Frame(self, bg=bg, highlightbackground=COLORS["border"], highlightthickness=1)
        body.pack(fill="both", expand=True, padx=26, pady=(0, 18))
        self.canvas = tk.Canvas(body, bg=bg, highlightthickness=0, height=270, yscrollincrement=30)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfig(self.win, width=e.width)
        )
        self.canvas.bind("<MouseWheel>", self._on_wheel)

        # 第 i 行 = 以 i:00 结束的那一小时;00:00 行是昨天 23:00–24:00.
        for h in range(24):
            row = tk.Frame(self.inner, bg=bg)
            row.pack(fill="x", padx=10, pady=2)
            lbl = tk.Label(
                row, text="", width=18, anchor="w", bg=bg, fg=COLORS["text_dim"],
                font=config.font("body", bold=True)
            )
            lbl.pack(side="left")
            var = tk.StringVar()
            ent = tk.Entry(
                row, textvariable=var, font=config.font("body"), relief="flat",
                bg=card, fg=COLORS["text"], insertbackground=COLORS["text"],
                disabledbackground=bg, disabledforeground=COLORS["text_dim"],
                highlightthickness=1, highlightbackground=COLORS["border"],
                highlightcolor=COLORS["accent"]
            )
            ent.pack(side="left", fill="x", expand=True, ipady=4)
            ent.bind("<Return>", lambda e, h=h: self._on_return(h))
            ent.bind("<FocusOut>", lambda e, h=h: self._commit(h))
            self.rows[h] = (lbl, ent, var)

    def _open_settings(self):
        if self.on_open_settings:
            self.on_open_settings()

    def _on_wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120), "units")

    def _slot_start(self, row: int) -> datetime:
        """第 row 行对应那一小时的开始时刻(数据按开始时刻存)."""
        d0 = datetime.strptime(self.view_date, '%Y-%m-%d')
        return d0 + timedelta(hours=row - 1)

    def _row_of(self, slot: datetime) -> int:
        d0 = datetime.strptime(self.view_date, '%Y-%m-%d')
        return int((slot - d0).total_seconds() // 3600) + 1

    def _get(self, row: int) -> str:
        s = self._slot_start(row)
        return self.dm.get_hour_text(s.strftime('%Y-%m-%d'), s.hour)

    def _scroll_to(self, row: int):
        self.update_idletasks()
        total = max(1, len(self.rows))
        row = min(max(row, 0), total - 1)
        self.canvas.yview_moveto(max(0, row - 2) / float(total))

    def _on_return(self, h: int):
        self._commit(h)
        self.canvas.focus_set()

    def _commit(self, h: int):
        _, ent, var = self.rows[h]
        if str(ent.cget("state")) == "disabled":
            return
        text = var.get().strip()
        if text != self._get(h):
            s = self._slot_start(h)
            self.dm.set_hour_text(s.strftime('%Y-%m-%d'), s.hour, text)
            if self.on_text_changed:
                self.on_text_changed()
            self.refresh()


    def refresh(self):
        now = datetime.now()
        self.view_date = self.dm.today_date
        enabled = bool(self.dm.hour_enabled)

        cfg(self.lbl_state,
            text=tr("当前: {state}").format(state=tr("开启") if enabled else tr("关闭_状态")),
            fg=COLORS["success"] if enabled else COLORS["text_dim"]
        )
        cfg(self.lbl_date,
            text=tr("{date}(今天)").format(date=self.view_date)
                 + ("" if enabled else tr("   ·  记录已关闭"))
        )

        awaiting = self.dm.awaiting_slot()
        focused = self.focus_get()
        for h, (lbl, ent, var) in self.rows.items():
            start = self._slot_start(h)
            end = start + timedelta(hours=1)
            ended = end <= now
            locked = ended and self.locked(start, now)
            text = self._get(h)
            if focused is not ent and var.get() != text:
                var.set(text)
            cfg(ent, state="normal" if (enabled and ended and not locked) else "disabled")

            is_wait = awaiting is not None and awaiting == start
            is_now = enabled and start <= now < end
            label = f"{h:02d}:00"
            if is_now:
                label += tr("   ◂ 现在")
            elif is_wait:
                label += tr("   ◂ 待填写")
            elif locked:
                label += "   🔒"
            cfg(lbl,
                text=label,
                fg=COLORS["accent"] if (is_wait or is_now) else
                   (COLORS["text"] if (enabled and ended and not locked) else COLORS["text_dim"])
            )
            cfg(ent,
                highlightbackground=COLORS["accent"] if is_wait else COLORS["border"]
            )

    def show(self):
        aw = self.dm.awaiting_slot()
        self.refresh()
        now = datetime.now().replace(minute=0, second=0, microsecond=0)
        row = self._row_of(aw if aw else now)
        self._scroll_to(row)
        if aw and row in self.rows:
            self.rows[row][1].focus_set()
