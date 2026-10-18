

import tkinter as tk
import calendar
import random
from datetime import datetime
from tkinter import ttk

import config
from config import COLORS
from i18n import tr, is_en
from .widgets import smooth, cfg

DAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
MONTHS_ZH = ("1 月", "2 月", "3 月", "4 月", "5 月", "6 月",
             "7 月", "8 月", "9 月", "10 月", "11 月", "12 月")
MONTHS_EN = ("January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December")


class CalendarPage(tk.Frame):
    def __init__(self, master, data_manager):
        super().__init__(master, bg=COLORS["bg"])
        self.dm = data_manager
        # 合约的黑雾 (MECHANICS 18.3): 不为 None 时显示随机的学习时长, 月份和合计写成 [CENSORED]
        self.fog = lambda: None
        self.display_year = datetime.now().year
        self.display_month = datetime.now().month
        self.offset = 0            # 0 = 本月, -1 = 上月(只保留这两个月)
        self.day_labels = []
        self._build_ui()

    def _build_ui(self):
        nav_frame = tk.Frame(self, bg=COLORS["bg"])
        nav_frame.pack(fill="x", padx=18, pady=(20, 5))

        self.lbl_month_title = tk.Label(
            nav_frame, text="", font=config.font("h2", bold=True),
            bg=COLORS["bg"], fg=COLORS["text"], anchor="w"
        )
        self.lbl_month_title.pack(side="left", expand=True, fill="x")

        self.btn_next = ttk.Button(nav_frame, text=tr("本月 ›"), command=lambda: self._go(0))
        self.btn_next.pack(side="right", padx=(4, 0))
        self.btn_prev = ttk.Button(nav_frame, text=tr("‹ 上月"), command=lambda: self._go(-1))
        self.btn_prev.pack(side="right", padx=(10, 0))

        self.lbl_month_total = tk.Label(
            nav_frame, text=tr("合计: {h:.1f} h").format(h=0.0),
            font=config.font("body", bold=True),
            bg=COLORS["bg"], fg=COLORS["accent"]
        )
        self.lbl_month_total.pack(side="right", padx=(10, 5))

        header_frame = tk.Frame(self, bg=COLORS["bg"])
        header_frame.pack(fill="x", padx=18, pady=(5, 0))
        for i, _day in enumerate(DAYS):
            lbl = tk.Label(
                header_frame, text="", font=config.font("small", bold=True),
                bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                width=8, height=1, anchor="center"
            )
            lbl.grid(row=0, column=i, padx=1, pady=1, sticky="nsew")
            header_frame.columnconfigure(i, weight=1)
            self.day_labels.append(lbl)

        self.grid_frame = tk.Frame(self, bg=COLORS["bg"])
        self.grid_frame.pack(fill="both", expand=True, padx=18, pady=(0, 18))

        # 6 行 × 7 列的格子只建一次, 之后每次刷新只改文字和颜色
        # (以前每次都全部销毁重建 42 个控件, 切到这页时会明显卡一下).
        self.cells = []
        for r in range(6):
            row = []
            for c in range(7):
                lbl = tk.Label(
                    self.grid_frame, text="", bg=COLORS["bg"], fg=COLORS["text"],
                    font=config.font("small"), width=8, height=2,
                    relief="flat", bd=0, anchor="center"
                )
                lbl.grid(row=r, column=c, padx=1, pady=1, sticky="nsew")
                row.append(lbl)
            self.cells.append(row)
        for i in range(7):
            self.grid_frame.columnconfigure(i, weight=1)

    def _apply_day_headers(self):
        for lbl, day in zip(self.day_labels, DAYS):
            cfg(lbl, text=tr(day))

    @smooth
    def _go(self, offset: int):
        self.offset = offset
        self.refresh()

    def show(self):
        self.offset = 0
        self.refresh()

    def refresh(self):
        now = datetime.now()
        self.display_year, self.display_month = now.year, now.month
        if self.offset < 0:
            if self.display_month == 1:
                self.display_year, self.display_month = self.display_year - 1, 12
            else:
                self.display_month -= 1
        for btn, off in ((self.btn_prev, self.offset < 0), (self.btn_next, self.offset == 0)):
            if btn.instate(["disabled"]) != off:
                btn.state(["disabled" if off else "!disabled"])

        months = MONTHS_EN if is_en() else MONTHS_ZH
        title = tr("{year} 年 {month}").format(
            year=self.display_year, month=months[self.display_month - 1]
        )
        seed = self.fog()
        cfg(self.lbl_month_title, text="[CENSORED]" if seed is not None else title)
        self._apply_day_headers()

        cal = calendar.monthcalendar(self.display_year, self.display_month)
        today_str = datetime.now().strftime('%Y-%m-%d')

        prefix = f"{self.display_year}-{self.display_month:02d}"
        month_data = {}
        month_total = 0.0
        for date_str, hours in self.dm.study_data.items():
            if date_str.startswith(prefix):
                month_data[date_str] = hours
                month_total += hours
        if seed is not None:
            rnd = random.Random(f"{seed}/{prefix}")
            month_data = {f"{prefix}-{d:02d}": round(rnd.uniform(0, 9), 1)
                          for d in range(1, 32) if f"{prefix}-{d:02d}" <= today_str}
            cfg(self.lbl_month_total, text=tr("合计: [CENSORED]"))
        else:
            cfg(self.lbl_month_total, text=tr("合计: {h:.1f} h").format(h=month_total))

        for row_idx in range(6):
            week = cal[row_idx] if row_idx < len(cal) else [0] * 7
            for col_idx, day in enumerate(week):
                lbl = self.cells[row_idx][col_idx]
                if day == 0:
                    cfg(lbl, text="", bg=COLORS["bg"])
                    continue
                date_str = f"{self.display_year}-{self.display_month:02d}-{day:02d}"
                hours = month_data.get(date_str, 0.0)
                is_today = date_str == today_str
                cfg(lbl,
                    text=f"{day}\n{hours:.1f}h" if hours > 0 else str(day),
                    bg=COLORS["accent"] if is_today else COLORS["bg_card"],
                    fg=COLORS["on_accent"] if is_today else COLORS["text"])
