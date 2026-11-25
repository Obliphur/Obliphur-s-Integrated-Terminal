"""Qt6 desktop shell for the staged UI migration.

The application model still lives in :class:`BreakTimerApp`.  During the first
migration stage its Tk widgets are created on a hidden compatibility root while
the visible window and its high-frequency timer surface are rendered by Qt6.
The Qt event loop pumps Tk at a low cost so existing pages and background jobs
continue to work until each page is migrated.
"""

from __future__ import annotations

import tkinter as tk
import calendar as _calendar
from datetime import datetime

import config
from config import COLORS, IDLE, ON_BREAK, READY_BREAK, RUNNING
from i18n import is_en, tr

try:
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QCloseEvent, QFont
    from PySide6.QtWidgets import (
        QApplication,
        QHBoxLayout,
        QGridLayout,
        QLabel,
        QListWidget,
        QTabWidget,
        QMainWindow,
        QPushButton,
        QSizePolicy,
        QStackedWidget,
        QVBoxLayout,
        QWidget,
    )
except ImportError:  # The installer supplies PySide6; source checkouts may not.
    QApplication = None


def available() -> bool:
    return QApplication is not None


def _tk_text(widget, default: str = "") -> str:
    try:
        return str(widget.cget("text"))
    except Exception:
        return default


if QApplication is not None:
    class QtCalendarPage(QWidget):
        """Qt calendar view backed by the existing DataManager calendar rules."""

        _KINDS = (("study", "专注"), ("break", "休息"), ("idle", "空闲"), ("sleep", "睡眠"))
        _DAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

        def __init__(self, model, parent=None):
            super().__init__(parent)
            self.model = model
            self.offset = 0
            self.kind = "study"
            root = QVBoxLayout(self)
            root.setContentsMargins(28, 22, 28, 22)
            root.setSpacing(12)
            header = QHBoxLayout()
            self.title = QLabel(self)
            self.title.setObjectName("pageTitle")
            self.total = QLabel(self)
            self.total.setObjectName("today")
            header.addWidget(self.title)
            header.addWidget(self.total)
            header.addStretch(1)
            self.kind_buttons = {}
            for key, label in self._KINDS:
                button = QPushButton(tr(label), self)
                button.clicked.connect(lambda _checked=False, k=key: self.set_kind(k))
                self.kind_buttons[key] = button
                header.addWidget(button)
            self.prev = QPushButton(tr("‹ 上月"), self)
            self.next = QPushButton(tr("本月 ›"), self)
            self.prev.clicked.connect(lambda: self.go(-1))
            self.next.clicked.connect(lambda: self.go(0))
            header.addWidget(self.prev)
            header.addWidget(self.next)
            root.addLayout(header)

            self.grid = QGridLayout()
            self.grid.setSpacing(2)
            self.cells = []
            for col, day in enumerate(self._DAYS):
                label = QLabel(tr(day), self)
                label.setAlignment(Qt.AlignCenter)
                label.setObjectName("calendarHeader")
                self.grid.addWidget(label, 0, col)
            for row in range(6):
                cells = []
                for col in range(7):
                    label = QLabel("", self)
                    label.setAlignment(Qt.AlignCenter)
                    label.setMinimumHeight(52)
                    label.setObjectName("calendarCell")
                    self.grid.addWidget(label, row + 1, col)
                    cells.append(label)
                self.cells.append(cells)
            root.addLayout(self.grid, 1)

        def _calendar_page(self):
            return getattr(self.model, "pages", {}).get("cal")

        def go(self, offset: int):
            self.offset = -1 if offset < 0 else 0
            self.refresh()

        def set_kind(self, kind: str):
            self.kind = kind
            self.refresh()

        def refresh(self):
            now = datetime.now()
            year, month = now.year, now.month
            if self.offset < 0:
                if month == 1:
                    year, month = year - 1, 12
                else:
                    month -= 1
            page = self._calendar_page()
            dm = getattr(page, "dm", None)
            prefix = f"{year}-{month:02d}"
            today_str = now.strftime("%Y-%m-%d")
            months = ("January", "February", "March", "April", "May", "June",
                      "July", "August", "September", "October", "November", "December") if is_en() else (
                          "1 月", "2 月", "3 月", "4 月", "5 月", "6 月", "7 月", "8 月", "9 月", "10 月", "11 月", "12 月")
            fog_seed = None
            try:
                fog_seed = page.fog() if page is not None else None
            except Exception:
                pass
            data = {}
            if self.kind == "study":
                for date_str, hours in (getattr(dm, "study_data", {}) or {}).items():
                    if date_str.startswith(prefix):
                        data[date_str] = float(hours)
            elif page is not None and dm is not None:
                try:
                    data = page._kind_hours(self.kind, prefix, now)
                except Exception:
                    data = {}
            if fog_seed is not None:
                import random
                rnd = random.Random(f"{fog_seed}/{prefix}")
                data = {f"{prefix}-{day:02d}": round(rnd.uniform(16, 80), 1)
                        for day in range(1, 32) if f"{prefix}-{day:02d}" <= today_str}
                self.title.setText("???")
                self.total.setText(tr("合计: ???"))
            else:
                self.title.setText(tr("{year} 年 {month}").format(year=year, month=months[month - 1]))
                if self.kind == "study":
                    self.total.setText(tr("合计: {h:.1f} h").format(h=sum(data.values())))
                else:
                    total_templates = {
                        "break": "休息合计: {h:.1f} h",
                        "idle": "空闲合计: {h:.1f} h",
                        "sleep": "睡眠合计: {h:.1f} h",
                    }
                    self.total.setText(tr(total_templates[self.kind]).format(h=sum(data.values())))
            self.prev.setEnabled(self.offset == 0)
            self.next.setEnabled(self.offset < 0)
            for key, button in self.kind_buttons.items():
                button.setEnabled(key != self.kind)
            weeks = _calendar.monthcalendar(year, month)
            for row in range(6):
                week = weeks[row] if row < len(weeks) else [0] * 7
                for col, day in enumerate(week):
                    label = self.cells[row][col]
                    if not day:
                        label.setText("")
                        label.setProperty("today", False)
                        continue
                    date_str = f"{year}-{month:02d}-{day:02d}"
                    hours = float(data.get(date_str, 0.0) or 0.0)
                    label.setText(f"{day}\n{hours:.1f}h" if hours > 0 else str(day))
                    label.setProperty("today", date_str == today_str)
                    label.style().unpolish(label)
                    label.style().polish(label)

    class QtStatsPage(QWidget):
        """Compact Qt statistics view; the existing model remains the source of truth."""

        def __init__(self, model, parent=None):
            super().__init__(parent)
            self.model = model
            root = QVBoxLayout(self)
            root.setContentsMargins(28, 22, 28, 22)
            title = QLabel(tr("📊  统计"), self)
            title.setObjectName("pageTitle")
            root.addWidget(title)
            self.tabs = QTabWidget(self)
            root.addWidget(self.tabs, 1)
            self.summary = QWidget(self.tabs)
            summary_layout = QVBoxLayout(self.summary)
            summary_layout.setContentsMargins(18, 18, 18, 18)
            self.summary_labels = {}
            for key in ("today", "this_month", "last_month", "focus", "contract"):
                label = QLabel(self.summary)
                label.setWordWrap(True)
                label.setMinimumHeight(34)
                self.summary_labels[key] = label
                summary_layout.addWidget(label)
            summary_layout.addStretch(1)
            self.tabs.addTab(self.summary, tr("概览"))

            self.contracts = QListWidget(self.tabs)
            self.violations = QListWidget(self.tabs)
            self.tabs.addTab(self.contracts, tr("合约记录"))
            self.tabs.addTab(self.violations, tr("违规记录"))

        def _month_prefixes(self):
            now = datetime.now()
            this = f"{now.year}-{now.month:02d}"
            if now.month == 1:
                last = f"{now.year - 1}-12"
            else:
                last = f"{now.year}-{now.month - 1:02d}"
            return this, last

        def refresh(self):
            dm = getattr(self.model, "dm", None)
            if dm is None:
                return
            this, last = self._month_prefixes()
            try:
                today = float(dm.get_today_hours())
            except Exception:
                today = 0.0
            try:
                this_hours = sum(float(v) for v in dm.month_study(this).values())
                last_hours = sum(float(v) for v in dm.month_study(last).values())
            except Exception:
                this_hours = sum(float(v) for k, v in dm.study_data.items() if str(k).startswith(this))
                last_hours = sum(float(v) for k, v in dm.study_data.items() if str(k).startswith(last))
            focus_count = len(getattr(dm, "focus_sessions", []) or [])
            self.summary_labels["today"].setText(tr("今日专注: {h:.1f} 小时").format(h=today))
            self.summary_labels["this_month"].setText(tr("本月专注: {h:.1f} 小时").format(h=this_hours))
            self.summary_labels["last_month"].setText(tr("上月专注: {h:.1f} 小时").format(h=last_hours))
            self.summary_labels["focus"].setText(tr("专注 / 休息记录: {n} 条").format(n=focus_count))
            contract = getattr(self.model, "contract", None)
            if contract is not None and getattr(contract, "active", False):
                self.summary_labels["contract"].setText(tr("当前合约: 等级 {lv}, 目标生命值 {life} / {limit}").format(
                    lv=getattr(contract, "level", 0), life=getattr(contract, "life", 0),
                    limit=getattr(contract, "violation_limit", 0)))
            else:
                self.summary_labels["contract"].setText(tr("当前没有进行中的合约"))

            self.contracts.clear()
            history = list(getattr(contract, "history", []) or []) if contract is not None else []
            for rec in reversed(history[-15:]):
                result = tr("完成") if rec.get("result") == "completed" else tr("失败")
                ended = str(rec.get("end") or rec.get("start") or "")[:16].replace("T", " ")
                self.contracts.addItem(f"{ended} · {result} · {tr('等级')} {rec.get('level', 0)}")
            if not history:
                self.contracts.addItem(tr("暂无合约记录"))
            self.violations.clear()
            for rec in dm.recent_violations(15):
                self.violations.addItem(f"{rec.get('at', '')} · {rec.get('text', '')}")
            if not getattr(dm, "violations", None):
                self.violations.addItem(tr("暂无违规记录"))

    class QtOitWindow(QMainWindow):
        """Visible Qt shell backed by the existing OIT application model."""

        def __init__(self, model, tk_root: tk.Tk):
            super().__init__()
            self.model = model
            self.tk_root = tk_root
            self._closing = False
            self._nav = {}
            self._pages = {}
            self.setWindowTitle(self._title())
            self.setMinimumSize(config.scaled(config.MIN_WINDOW_W), config.scaled(config.MIN_WINDOW_H))
            self.resize(config.scaled(config.WINDOW_W), config.scaled(config.WINDOW_H))
            self._build()
            self._apply_palette()
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._pump)
            self._timer.start(33)

        def _title(self) -> str:
            return tr("指令终端") if getattr(config, "THEME", "") == "urban_will" else config.APP_NAME

        def _build(self):
            central = QWidget(self)
            outer = QHBoxLayout(central)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)
            self.sidebar = QWidget(central)
            side_layout = QVBoxLayout(self.sidebar)
            side_layout.setContentsMargins(12, 18, 12, 12)
            side_layout.setSpacing(4)
            self._side_layout = side_layout
            self._stack = QStackedWidget(central)
            self._stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            outer.addWidget(self.sidebar)
            outer.addWidget(self._stack, 1)
            self.setCentralWidget(central)

            labels = getattr(self.model, "nav_labels", {})
            order = list(labels) or ["timer", "log", "cal", "stats", "contract", "comm", "settings"]
            for key in order:
                button = QPushButton(labels.get(key, key.title()), self.sidebar)
                button.setCursor(Qt.PointingHandCursor)
                button.clicked.connect(lambda _checked=False, k=key: self.show_page(k))
                self._nav[key] = button
                side_layout.addWidget(button)
            side_layout.addStretch(1)
            self._add_timer_page()
            for key in order:
                if key != "timer":
                    page = (QtCalendarPage(self.model, self._stack) if key == "cal" else
                            QtStatsPage(self.model, self._stack) if key == "stats" else QLabel(
                        tr("正在迁移此页面到 Qt6"), self._stack))
                    if isinstance(page, QLabel):
                        page.setAlignment(Qt.AlignCenter)
                    self._pages[key] = page
                    self._stack.addWidget(page)
            self.show_page("timer")

        def _add_timer_page(self):
            page = QWidget(self._stack)
            layout = QVBoxLayout(page)
            layout.setContentsMargins(36, 28, 36, 28)
            layout.setSpacing(16)
            title = QLabel(tr("⏱  Focus Timer"), page)
            title.setObjectName("pageTitle")
            layout.addWidget(title)
            card = QWidget(page)
            card.setObjectName("timerCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(24, 22, 24, 22)
            self.status = QLabel(page)
            self.status.setObjectName("status")
            self.time_label = QLabel("0.00 min", page)
            self.time_label.setObjectName("time")
            self.sub_label = QLabel(page)
            self.sub_label.setWordWrap(True)
            self.today_label = QLabel(page)
            self.today_label.setObjectName("today")
            for widget in (self.status, self.time_label, self.sub_label, self.today_label):
                card_layout.addWidget(widget)
            layout.addWidget(card)
            actions = QHBoxLayout()
            self.action = QPushButton(page)
            self.break_button = QPushButton(page)
            self.pause = QPushButton(page)
            self.sleep = QPushButton(tr("😴  睡眠"), page)
            self.action.clicked.connect(self.model.on_action)
            self.break_button.clicked.connect(self.model.on_break_button)
            self.pause.clicked.connect(self.model.toggle_focus_pause)
            self.sleep.clicked.connect(self.model._sleep_click)
            for widget in (self.action, self.break_button, self.pause, self.sleep):
                actions.addWidget(widget)
            layout.addLayout(actions)
            layout.addStretch(1)
            self._pages["timer"] = page
            self._stack.addWidget(page)

        def _apply_palette(self):
            bg = COLORS.get("bg", "#202020")
            card = COLORS.get("bg_card", bg)
            text = COLORS.get("text", "#f0f0f0")
            dim = COLORS.get("text_dim", "#a0a0a0")
            accent = COLORS.get("accent", "#4da3ff")
            border = COLORS.get("border", "#404040")
            self.setStyleSheet(f"""
                QMainWindow, QWidget {{ background: {bg}; color: {text}; }}
                QWidget#timerCard {{ background: {card}; border: 1px solid {border}; }}
                QLabel#pageTitle {{ font-size: 22px; font-weight: 700; }}
                QLabel#time {{ font-size: 42px; font-weight: 700; color: {text}; }}
                QLabel#status, QLabel#today {{ color: {accent}; font-weight: 600; }}
                QPushButton {{ background: {card}; color: {text}; border: 1px solid {border}; padding: 9px 14px; }}
                QPushButton:hover {{ border-color: {accent}; }}
                QPushButton:disabled {{ color: {dim}; }}
                QPushButton[active="true"] {{ background: {accent}; color: {COLORS.get('on_accent', '#ffffff')}; }}
                QLabel#calendarHeader {{ background: {card}; color: {dim}; padding: 6px; }}
                QLabel#calendarCell {{ background: {card}; border: 1px solid {border}; padding: 4px; }}
                QLabel#calendarCell[today="true"] {{ background: {accent}; color: {COLORS.get('on_accent', '#ffffff')}; }}
                QWidget#timerCard QPushButton {{ background: {accent}; color: {COLORS.get('on_accent', '#ffffff')}; }}
            """)

        def show_page(self, key: str):
            if key not in self._pages:
                key = "timer"
            try:
                self.model.show_page(key)
            except Exception:
                pass
            self._stack.setCurrentWidget(self._pages[key])
            if key == "cal":
                self._pages[key].refresh()
            elif key == "stats":
                self._pages[key].refresh()
            for name, button in self._nav.items():
                button.setProperty("active", name == key)
                button.style().unpolish(button)
                button.style().polish(button)

        def _pump(self):
            if self._closing:
                return
            try:
                self.tk_root.update()
            except tk.TclError:
                return
            self._refresh()

        def _refresh(self):
            self.setWindowTitle(self._title())
            self.status.setText(_tk_text(getattr(self.model, "lbl_status", None), tr("准备就绪")))
            self.time_label.setText(_tk_text(getattr(self.model, "lbl_time", None), "0.00 min"))
            self.sub_label.setText(_tk_text(getattr(self.model, "lbl_sub", None)))
            self.today_label.setText(_tk_text(getattr(self.model, "lbl_today", None)))
            for target, source in ((self.action, "btn_action"), (self.break_button, "btn_break"), (self.pause, "btn_pause")):
                widget = getattr(self.model, source, None)
                target.setText(_tk_text(widget))
                try:
                    target.setEnabled(str(widget.cget("state")) != "disabled")
                except Exception:
                    target.setEnabled(True)

        def closeEvent(self, event: QCloseEvent):
            self._closing = True
            self._timer.stop()
            try:
                self.model.on_close()
            finally:
                event.accept()


def run(model, tk_root: tk.Tk) -> int:
    """Run the Qt shell and return QApplication's exit code."""
    if not available():
        raise RuntimeError("PySide6 is not installed")
    qt_app = QApplication.instance() or QApplication([])
    window = QtOitWindow(model, tk_root)
    window.show()
    return qt_app.exec()
