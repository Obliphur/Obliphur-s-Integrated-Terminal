"""Qt6 desktop shell for the staged UI migration.

The application model still lives in :class:`BreakTimerApp`.  During the first
migration stage its Tk widgets are created on a hidden compatibility root while
the visible window and its high-frequency timer surface are rendered by Qt6.
The Qt event loop pumps Tk at a low cost so existing pages and background jobs
continue to work until each page is migrated.
"""

from __future__ import annotations

import tkinter as tk

import config
from config import COLORS, IDLE, ON_BREAK, READY_BREAK, RUNNING
from i18n import tr

try:
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QCloseEvent, QFont
    from PySide6.QtWidgets import (
        QApplication,
        QHBoxLayout,
        QLabel,
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
                    page = QLabel(tr("正在迁移此页面到 Qt6"), self._stack)
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
