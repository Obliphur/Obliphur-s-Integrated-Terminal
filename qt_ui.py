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
from settings_manager import SETTINGS_PAGES, page_specs

try:
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QCloseEvent, QFontDatabase
    from PySide6.QtWidgets import (
        QApplication,
        QHBoxLayout,
        QGridLayout,
        QLabel,
        QComboBox,
        QFrame,
        QLineEdit,
        QListWidget,
        QScrollArea,
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

    class QtSettingsPage(QWidget):
        """Qt settings editor using SettingsManager's validation and staging."""

        def __init__(self, model, parent=None):
            super().__init__(parent)
            self.model = model
            self.sm = getattr(model, "settings", None)
            self._rows = {}
            self._loading = False
            root = QVBoxLayout(self)
            root.setContentsMargins(28, 22, 28, 18)
            title = QLabel(tr("⚙  设置"), self)
            title.setObjectName("pageTitle")
            root.addWidget(title)
            self.tabs = QTabWidget(self)
            root.addWidget(self.tabs, 1)
            for key, label in SETTINGS_PAGES:
                page = self._make_page(key)
                self.tabs.addTab(page, tr(label))
            self.notice = QLabel(self)
            self.notice.setWordWrap(True)
            self.notice.setObjectName("settingsNotice")
            root.addWidget(self.notice)
            self.refresh()

        def _make_page(self, page_key):
            scroll = QScrollArea(self)
            scroll.setWidgetResizable(True)
            body = QWidget(scroll)
            layout = QVBoxLayout(body)
            layout.setContentsMargins(8, 8, 8, 8)
            choices, nums = page_specs(page_key)
            for spec in choices:
                self._add_choice(layout, spec)
            for spec in nums:
                self._add_number(layout, spec)
            layout.addStretch(1)
            scroll.setWidget(body)
            return scroll

        def _row_layout(self, layout, label_text):
            row = QHBoxLayout()
            name = QLabel(tr(label_text), self)
            name.setMinimumWidth(190)
            name.setWordWrap(True)
            row.addWidget(name)
            layout.addLayout(row)
            return row

        def _add_choice(self, layout, spec):
            key = spec["key"]
            row = self._row_layout(layout, spec["label"])
            combo = QComboBox(self)
            values = list(self.sm.labeled_values(key, translate=True)) if self.sm else []
            for raw, label in values:
                combo.addItem(label, raw)
            if not values:
                combo.setEditable(True)
            row.addWidget(combo, 1)
            apply_button = QPushButton(tr("提交"), self)
            apply_button.clicked.connect(lambda _checked=False, k=key: self._stage_choice(k))
            row.addWidget(apply_button)
            pending = QLabel(self)
            pending.setObjectName("settingsPending")
            pending.setWordWrap(True)
            layout.addWidget(pending)
            self._rows[key] = {"spec": spec, "combo": combo, "pending": pending, "input": combo}

        def _add_number(self, layout, spec):
            key = spec["key"]
            row = self._row_layout(layout, spec.get("short") or spec["label"])
            entry = QLineEdit(self)
            entry.setMinimumWidth(110)
            row.addWidget(entry)
            unit = QLabel(str(spec.get("unit") or ""), self)
            row.addWidget(unit)
            apply_button = QPushButton(tr("提交"), self)
            apply_button.clicked.connect(lambda _checked=False, k=key: self._stage_number(k))
            row.addWidget(apply_button)
            current = QLabel(self)
            current.setObjectName("settingsCurrent")
            row.addWidget(current, 1)
            pending = QLabel(self)
            pending.setObjectName("settingsPending")
            pending.setWordWrap(True)
            layout.addWidget(pending)
            self._rows[key] = {"spec": spec, "entry": entry, "current": current, "pending": pending, "input": entry}

        def _choice_value(self, key):
            row = self._rows[key]
            combo = row["combo"]
            value = combo.currentData()
            return combo.currentText() if value is None else value

        def _stage_choice(self, key):
            if self._loading:
                return
            self._stage(key, self._choice_value(key))

        def _stage_number(self, key):
            if self._loading:
                return
            spec = self._rows[key]["spec"]
            text = self._rows[key]["entry"].text().strip()
            try:
                if spec.get("time"):
                    hh, mm = text.split(":", 1)
                    raw = int(hh) * 60 + int(mm)
                else:
                    value = float(text)
                    raw = value * float(spec.get("scale", 1))
                    if spec.get("integer"):
                        raw = int(round(raw))
            except (TypeError, ValueError):
                self.notice.setText(tr("{label}: 请填写有效数值.").format(label=tr(spec["label"])))
                return
            self._stage(key, raw)

        def _stage(self, key, value):
            status, message = self.sm.stage(key, value)
            if status == "invalid":
                self.notice.setText(str(message))
                return
            if hasattr(self.model, "on_settings_changed"):
                self.model.on_settings_changed(status, key)
            if status == "instant":
                hooks = {
                    "LANGUAGE": "on_language_changed",
                    "FONT_SCALE": "on_font_scale_changed",
                    "THEME": "on_theme_changed",
                    "CONTRACT_THEME": "rebuild_ui",
                }
                hook = getattr(self.model, hooks.get(key, ""), None)
                if callable(hook):
                    hook()
                # Theme and font changes update config in the model; repaint
                # the visible Qt shell without rebuilding the window object.
                host = self.window()
                if hasattr(host, "_apply_palette"):
                    host._apply_palette()
                self._apply_retranslated_text()
            self.notice.setText(str(message or tr("已保存.")))
            self.refresh()

        def _apply_retranslated_text(self):
            for i, (_key, label) in enumerate(SETTINGS_PAGES):
                self.tabs.setTabText(i, tr(label))
            for row in self._rows.values():
                spec = row["spec"]
                if "combo" in row:
                    combo = row["combo"]
                    for i in range(combo.count()):
                        raw = combo.itemData(i)
                        label = self.sm.label_for(spec["key"], raw, translate=True)
                        if label is not None:
                            combo.setItemText(i, label)

        def refresh(self):
            if self.sm is None:
                return
            self._loading = True
            try:
                for key, row in self._rows.items():
                    spec = row["spec"]
                    current = self.sm.value(key)
                    if "combo" in row:
                        combo = row["combo"]
                        idx = combo.findData(current)
                        if idx >= 0:
                            combo.setCurrentIndex(idx)
                        elif combo.isEditable():
                            combo.setEditText(str(current))
                    else:
                        row["entry"].setText(self.sm.ui_number(key))
                        row["current"].setText(tr("当前: {value}").format(value=self.sm.display(key)))
                    pending = self.sm.staged(key)
                    row["pending"].setText(
                        tr("待生效: {value} · {when}").format(
                            value=self.sm.display_value(key, pending["value"]),
                            when=self.sm.apply_at_text(key)) if pending else "")
                n = self.sm.staged_count()
                self.notice.setText(tr("待生效修改 {n} 项").format(n=n) if n else tr("当前没有待生效的修改."))
            finally:
                self._loading = False

    class QtOitWindow(QMainWindow):
        """Visible Qt shell backed by the existing OIT application model."""

        def __init__(self, model, tk_root: tk.Tk):
            super().__init__()
            self.model = model
            self.tk_root = tk_root
            self._closing = False
            self._qt_font_id = -1
            self._qt_font_family = ""
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
            root_layout = QVBoxLayout(central)
            root_layout.setContentsMargins(0, 0, 0, 0)
            root_layout.setSpacing(0)
            self.topbar = QWidget(central)
            self.topbar.setObjectName("topbar")
            self.topbar.setFixedHeight(config.scaled(4))
            root_layout.addWidget(self.topbar)
            shell = QWidget(central)
            outer = QHBoxLayout(shell)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)
            root_layout.addWidget(shell, 1)
            self.sidebar = QWidget(shell)
            self.sidebar.setObjectName("sidebar")
            self.sidebar.setFixedWidth(config.scaled(208))
            side_layout = QVBoxLayout(self.sidebar)
            side_layout.setContentsMargins(0, config.scaled(14), 0, 0)
            side_layout.setSpacing(config.scaled(2))
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
                button.setObjectName("navButton")
                button.setMinimumHeight(config.scaled(48))
                button.setCursor(Qt.PointingHandCursor)
                button.clicked.connect(lambda _checked=False, k=key: self.show_page(k))
                self._nav[key] = button
                side_layout.addWidget(button)
            side_layout.addStretch(1)
            self._add_timer_page()
            for key in order:
                if key != "timer":
                    page = (QtCalendarPage(self.model, self._stack) if key == "cal" else
                            QtStatsPage(self.model, self._stack) if key == "stats" else
                            QtSettingsPage(self.model, self._stack) if key == "settings" else QLabel(
                        tr("正在迁移此页面到 Qt6"), self._stack))
                    if isinstance(page, QLabel):
                        page.setAlignment(Qt.AlignCenter)
                    self._pages[key] = page
                    self._stack.addWidget(page)
            self.show_page("timer")

        def _add_timer_page(self):
            page = QWidget(self._stack)
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            header = QWidget(page)
            header_layout = QVBoxLayout(header)
            header_layout.setContentsMargins(config.scaled(28), config.scaled(22), config.scaled(28), config.scaled(8))
            title = QLabel(tr("⏱  Focus Timer"), page)
            title.setObjectName("pageTitle")
            header_layout.addWidget(title)
            layout.addWidget(header)
            separator = QFrame(page)
            separator.setFrameShape(QFrame.HLine)
            separator.setFixedHeight(1)
            sep_layout = QHBoxLayout()
            sep_layout.setContentsMargins(config.scaled(26), 0, config.scaled(26), 0)
            sep_layout.addWidget(separator)
            layout.addLayout(sep_layout)
            card = QWidget(page)
            card.setObjectName("timerCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(config.scaled(22), config.scaled(18), config.scaled(22), config.scaled(18))
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
            card_wrap = QHBoxLayout()
            card_wrap.setContentsMargins(config.scaled(26), config.scaled(20), config.scaled(26), 0)
            card_wrap.addWidget(card)
            layout.addLayout(card_wrap)
            actions = QVBoxLayout()
            actions.setContentsMargins(config.scaled(26), config.scaled(6), config.scaled(26), config.scaled(22))
            primary = QHBoxLayout()
            primary.setAlignment(Qt.AlignCenter)
            self.action = QPushButton(page)
            self.break_button = QPushButton(page)
            self.pause = QPushButton(page)
            self.action.setObjectName("accentButton")
            self.action.clicked.connect(self.model.on_action)
            self.break_button.clicked.connect(self.model.on_break_button)
            self.pause.clicked.connect(self.model.toggle_focus_pause)
            for widget in (self.action, self.break_button, self.pause):
                primary.addWidget(widget)
            actions.addLayout(primary)
            secondary = QHBoxLayout()
            secondary.setAlignment(Qt.AlignCenter)
            self.sleep = QPushButton(page)
            self.prestart = QPushButton(page)
            self.outing = QPushButton(page)
            self.sleep.clicked.connect(self.model._sleep_click)
            self.prestart.clicked.connect(self.model._prestart_click)
            self.outing.clicked.connect(self.model._outing_click)
            for widget in (self.sleep, self.prestart, self.outing):
                secondary.addWidget(widget)
            actions.addLayout(secondary)
            layout.addLayout(actions)
            flavor = QLabel(tr("在第二段区间内 学的时间越长得到的休息时间越长.请尽量长时间地专注."), page)
            flavor.setObjectName("flavor")
            flavor.setWordWrap(True)
            flavor_layout = QHBoxLayout()
            flavor_layout.setContentsMargins(config.scaled(26), 0, config.scaled(26), config.scaled(22))
            flavor_layout.addWidget(flavor)
            layout.addLayout(flavor_layout)
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
            family = self._font_family()

            def px(key):
                value = config.font(key)[1]
                # Tk uses point sizes for normal themes and pixel sizes (negative
                # values) for the urban pixel theme. Qt stylesheets use pixels.
                return abs(int(value)) if value < 0 else max(8, round(float(value) * 96.0 / 72.0))

            body_px, nav_px = px("body"), px("nav")
            button_px, small_px = px("btn"), px("small")
            h1_px, big_px = px("h1"), px("big")
            self.setStyleSheet(f"""
                QMainWindow, QWidget {{ background: {bg}; color: {text}; font-family: '{family}'; font-size: {body_px}px; }}
                QWidget#topbar {{ background: {COLORS.get('topbar', accent)}; }}
                QWidget#sidebar {{ background: {card}; border-right: 1px solid {border}; }}
                QPushButton#navButton {{ background: {card}; color: {text}; border: 0; border-radius: 0; text-align: left; font-size: {nav_px}px; padding-left: {config.scaled(14)}px; padding-right: {config.scaled(10)}px; padding-top: {config.scaled(12)}px; padding-bottom: {config.scaled(12)}px; }}
                QPushButton#navButton:hover {{ background: {border}; }}
                QPushButton#navButton[active="true"] {{ background: {COLORS.get('accent_fill', accent)}; color: {COLORS.get('on_accent_fill', COLORS.get('on_accent', '#ffffff'))}; }}
                QWidget#timerCard {{ background: {card}; border: 1px solid {border}; }}
                QLabel#pageTitle {{ font-size: {h1_px}px; font-weight: 700; }}
                QLabel#time {{ font-size: {big_px}px; font-weight: 700; color: {text}; }}
                QLabel#status, QLabel#today {{ color: {accent}; font-weight: 600; }}
                QLabel#flavor, QLabel#settingsPending {{ color: {dim}; font-size: {small_px}px; }}
                QPushButton {{ background: {card}; color: {text}; border: 1px solid {border}; font-size: {button_px}px; padding: {config.scaled(10)}px {config.scaled(18)}px; }}
                QPushButton:hover {{ border-color: {accent}; }}
                QPushButton:disabled {{ color: {dim}; }}
                QPushButton#accentButton {{ background: {COLORS.get('accent_fill', accent)}; color: {COLORS.get('on_accent_fill', COLORS.get('on_accent', '#ffffff'))}; border: 0; font-weight: 700; }}
                QLabel#calendarHeader {{ background: {card}; color: {dim}; padding: {config.scaled(6)}px; font-size: {small_px}px; }}
                QLabel#calendarCell {{ background: {card}; border: 1px solid {border}; padding: {config.scaled(4)}px; font-size: {small_px}px; }}
                QLabel#calendarCell[today="true"] {{ background: {accent}; color: {COLORS.get('on_accent', '#ffffff')}; }}
                QWidget#timerCard QPushButton {{ background: {accent}; color: {COLORS.get('on_accent', '#ffffff')}; }}
            """)

        def _font_family(self):
            """Load the urban theme font into Qt; fall back to the original UI font."""
            if not getattr(config, "THEME_PIXEL", False):
                return getattr(config, "FONT_FAMILY_DEFAULT", "Microsoft YaHei UI")
            spec = (getattr(config, "THEME_DATA", {}) or {}).get("font") or {}
            try:
                path = config.asset_path(*spec["file"])
                if self._qt_font_id < 0 or not self._qt_font_family:
                    self._qt_font_id = QFontDatabase.addApplicationFont(path)
                    if self._qt_font_id >= 0:
                        families = QFontDatabase.applicationFontFamilies(self._qt_font_id)
                        if families:
                            self._qt_font_family = families[0]
                if self._qt_font_family:
                    return self._qt_font_family
            except Exception:
                pass
            return getattr(config, "FONT_FAMILY_DEFAULT", "Microsoft YaHei UI")

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
            elif key == "settings":
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
            for target, source in (
                (self.action, "btn_action"), (self.break_button, "btn_break"),
                (self.pause, "btn_pause"), (self.sleep, "btn_sleep"),
                (self.prestart, "btn_prestart"), (self.outing, "btn_outing"),
            ):
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
