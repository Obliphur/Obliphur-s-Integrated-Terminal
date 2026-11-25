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
import contract as contract_rules
from config import COLORS, IDLE, ON_BREAK, READY_BREAK, RUNNING
from i18n import is_en, tr
from settings_manager import SETTINGS_PAGES, page_specs

try:
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QCloseEvent, QFontDatabase, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QHBoxLayout,
        QGridLayout,
        QLabel,
        QComboBox,
        QFrame,
        QLineEdit,
        QListWidget,
        QProgressBar,
        QPlainTextEdit,
        QScrollArea,
        QSpinBox,
        QTableWidget,
        QTableWidgetItem,
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
                if hasattr(host, "_pages") and "contract" in host._pages:
                    host._pages["contract"].refresh()
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

    class QtContractPage(QWidget):
        """Qt contract board backed by the existing ContractPage rule model."""

        def __init__(self, model, parent=None):
            super().__init__(parent)
            self.model = model
            self.legacy = getattr(model, "pages", {}).get("contract")
            self.page = 0
            root = QVBoxLayout(self)
            root.setContentsMargins(config.scaled(24), config.scaled(18), config.scaled(24), config.scaled(14))
            header = QHBoxLayout()
            self.title = QLabel(tr("⚔  合约"), self)
            self.title.setObjectName("pageTitle")
            self.state = QLabel(self)
            header.addWidget(self.title)
            header.addStretch(1)
            header.addWidget(self.state)
            root.addLayout(header)
            self.rules = QLabel(self)
            self.rules.setWordWrap(True)
            self.rules.setObjectName("contractRules")
            root.addWidget(self.rules)

            self.live = QLabel(self)
            self.live.setWordWrap(True)
            self.live.setObjectName("contractLive")
            root.addWidget(self.live)

            self.pages = QTabWidget(self)
            self.pages.currentChanged.connect(self._set_page)
            root.addWidget(self.pages, 1)
            for start, end in contract_rules.PAGES:
                body = QWidget(self.pages)
                grid = QGridLayout(body)
                grid.setContentsMargins(config.scaled(8), config.scaled(8), config.scaled(8), config.scaled(8))
                grid.setSpacing(config.scaled(6))
                for tid, group, level, _name, desc, _conflicts in contract_rules.TERMS:
                    col, _ = contract_rules.LAYOUT[tid]
                    if not start <= col < end:
                        continue
                    button = QPushButton(f"{level}  {tr(_name)}", body)
                    button.setMinimumHeight(config.scaled(46))
                    button.setToolTip(tr(desc))
                    button.clicked.connect(lambda _checked=False, term=tid: self._click(term))
                    grid.addWidget(button, level - 1, col - start)
                    setattr(button, "_term_id", tid)
                grid.setRowStretch(3, 1)
                self.pages.addTab(body, tr("词条 {n}").format(n=self.pages.count() + 1))

            combo_body = QWidget(self.pages)
            combo_layout = QVBoxLayout(combo_body)
            self.combos = QLabel(combo_body)
            self.combos.setWordWrap(True)
            combo_layout.addWidget(self.combos)
            combo_layout.addStretch(1)
            self.pages.addTab(combo_body, tr("词条组合"))
            self.info = QLabel(self)
            self.info.setWordWrap(True)
            root.addWidget(self.info)
            self.selected = QLabel(self)
            self.selected.setWordWrap(True)
            root.addWidget(self.selected)

            footer = QHBoxLayout()
            self.level = QLabel(self)
            self.clear = QPushButton(tr("清空"), self)
            self.clear.clicked.connect(self._clear)
            footer.addWidget(self.clear)
            footer.addWidget(self.level)
            footer.addStretch(1)
            self.reject = QPushButton(tr("拒绝"), self)
            self.accept = QPushButton(tr("接受"), self)
            self.start = QPushButton(tr("开始行动"), self)
            self.end = QPushButton(tr("提前结束"), self)
            self.reject.clicked.connect(lambda: self._respond(False))
            self.accept.clicked.connect(lambda: self._respond(True))
            self.start.clicked.connect(self._start)
            self.end.clicked.connect(self._end)
            for button in (self.reject, self.accept, self.start, self.end):
                footer.addWidget(button)
            root.addLayout(footer)
            self.refresh()

        def _set_page(self, page):
            self.page = page

        def _click(self, tid):
            if self.legacy is not None:
                self.legacy._click(tid)
            self.refresh()

        def _clear(self):
            if self.legacy is not None:
                self.legacy._clear()
            self.refresh()

        def _start(self):
            if self.legacy is not None:
                self.legacy._start()
            self.refresh()

        def _end(self):
            if self.legacy is not None and self.legacy.on_abandon:
                self.legacy.on_abandon()
            self.refresh()

        def _respond(self, accept):
            if self.legacy is not None:
                self.legacy._respond(accept)
            self.refresh()

        def refresh(self):
            if self.legacy is None:
                return
            self.legacy.refresh()
            c = self.legacy.c
            active = bool(c.active)
            chosen = set(c.terms if active else c.draft)
            blocked = self.legacy._terms_locked()
            self.state.setText(_tk_text(self.legacy.lbl_state))
            self.rules.setText("\n".join(self.legacy._rules_text()))
            for tab_index in range(2):
                body = self.pages.widget(tab_index)
                for i in range(body.layout().count()):
                    item = body.layout().itemAt(i)
                    button = item.widget()
                    if button is None or not hasattr(button, "_term_id"):
                        continue
                    tid = button._term_id
                    selected = tid in chosen
                    disabled = active or (blocked and not selected)
                    button.setEnabled(not disabled or (active and (tid in c.rand or c.can_lift(tid))))
                    if selected:
                        button.setStyleSheet("font-weight: 700; border-width: 2px;")
                    else:
                        button.setStyleSheet("")
            terms = c.terms if active else c.draft
            self.selected.setText(tr("已选词条: ") + (" · ".join(tr(contract_rules.BY_ID[t][3]) for t in terms) or tr("无")))
            self.level.setText(tr("危机等级: {n}").format(n=self.legacy.level()))
            self.info.setText("\n".join(_tk_text(w) for w in (self.legacy.lbl_info,) if _tk_text(w)))
            self.live.setVisible(active)
            if active:
                self.live.setText("\n".join(_tk_text(w) for w in (
                    self.legacy.lbl_left, self.legacy.lbl_viol, self.legacy.lbl_live,
                    self.legacy.lbl_idle, self.legacy.lbl_rec) if _tk_text(w)))
            inv = self.legacy._inv_on()
            self.clear.setVisible(not active and not blocked)
            self.start.setVisible(not active and not inv)
            self.reject.setVisible(inv)
            self.accept.setVisible(inv)
            self.end.setVisible(active)
            self.start.setText(tr("确定选择") if self.legacy._joint_pick else tr("开始行动"))
            self.start.setEnabled(not self.legacy.blocked() or self.legacy._joint_pick)
            self.combos.setText("\n\n".join(
                f"{tr(combo['name'])} · {tr(state)}\n{self.legacy._combo_desc(combo)}"
                for combo, state in self.legacy._combos()))

    class QtKnowledgePage(QWidget):
        """Qt knowledge browser that delegates storage and editing to KnowledgePage."""

        _LISTS = (("tactical", "技巧库"), ("conceptual", "概念库"),
                  ("reminder", "提醒库"), ("proposals", "待审提案"))

        def __init__(self, model, parent=None):
            super().__init__(parent)
            self.model = model
            self.legacy = getattr(model, "pages", {}).get("knowledge")
            self._syncing = False
            root = QVBoxLayout(self)
            root.setContentsMargins(config.scaled(18), config.scaled(12), config.scaled(18), config.scaled(12))
            title = QLabel(tr("📚  知识库"), self)
            title.setObjectName("pageTitle")
            root.addWidget(title)
            toolbar = QHBoxLayout()
            self.tools = []
            for label, method in (("在 Obsidian 中打开", "open_obsidian"), ("新建卡片", "new_card"),
                                  ("导入笔记", "import_notes"), ("刷新索引", "show"),
                                  ("知识库帮助", "help"), ("知识库设置", "settings")):
                button = QPushButton(tr(label), self)
                button.clicked.connect(lambda _checked=False, name=method: self._legacy_call(name))
                toolbar.addWidget(button)
                self.tools.append(button)
            root.addLayout(toolbar)

            filters = QHBoxLayout()
            self.subject = QComboBox(self)
            self.category = QComboBox(self)
            self.query = QLineEdit(self)
            self.query.setPlaceholderText(tr("搜索知识卡片"))
            self.sort = QComboBox(self)
            self.sort.addItems([tr(x) for x in ("综合权重", "频次", "最近使用", "层级视图")])
            self.search = QPushButton(tr("搜索"), self)
            self.clear_search = QPushButton(tr("清空"), self)
            for widget in (self.subject, self.category, self.query, self.sort, self.search, self.clear_search):
                filters.addWidget(widget, 1 if widget is self.query else 0)
            root.addLayout(filters)
            self.subject.currentTextChanged.connect(self._subject_changed)
            self.category.currentTextChanged.connect(lambda _text: self.refresh())
            self.query.returnPressed.connect(self.refresh)
            self.search.clicked.connect(self.refresh)
            self.clear_search.clicked.connect(lambda: (self.query.clear(), self.refresh()))
            self.sort.currentTextChanged.connect(self.refresh)

            self.tabs = QTabWidget(self)
            self.tables = {}
            for key, label in self._LISTS:
                table = QTableWidget(0, 2, self.tabs)
                table.setHorizontalHeaderLabels([tr("标题"), tr("学科 / 统计")])
                table.horizontalHeader().setStretchLastSection(True)
                table.setSelectionBehavior(QTableWidget.SelectRows)
                table.setSelectionMode(QTableWidget.ExtendedSelection)
                table.setEditTriggers(QTableWidget.NoEditTriggers)
                table.cellDoubleClicked.connect(lambda _row, _col, k=key: self._action("open_selected", k))
                self.tables[key] = table
                self.tabs.addTab(table, tr(label))
            root.addWidget(self.tabs, 1)

            actions = QHBoxLayout()
            for label, method in (("全选", "select_all"), ("全不选", "clear_selection"),
                                  ("批量批准", "approve_selected"), ("批量删除", "delete_selected"),
                                  ("查看所选卡片或提案", "open_selected"), ("管理所选卡片", "details")):
                button = QPushButton(tr(label), self)
                button.clicked.connect(lambda _checked=False, name=method: self._action(name))
                actions.addWidget(button)
            root.addLayout(actions)
            self.refresh()

        def _legacy_call(self, name):
            if self.legacy is None:
                return
            if name == "open_obsidian":
                from pages.knowledge_page import open_obsidian
                open_obsidian(self.legacy.kb.root, self.legacy.data_dir)
            else:
                getattr(self.legacy, name)()
            self.refresh()

        def _subject_changed(self, value):
            if self._syncing or self.legacy is None:
                return
            self.legacy.subject.set(value)
            self.legacy.category.set(tr("全部分类"))
            self.refresh()

        def _sync_selection(self, key):
            tree = self.legacy.lists[key]
            chosen = []
            table = self.tables[key]
            for index in table.selectionModel().selectedRows():
                item = table.item(index.row(), 0)
                if item is not None:
                    ident = item.data(Qt.UserRole)
                    if ident:
                        chosen.append(str(ident))
            tree.selection_set(chosen) if chosen else tree.selection_remove(tree.selection())

        def _action(self, name, key=None):
            if self.legacy is None:
                return
            key = key or self._LISTS[self.tabs.currentIndex()][0]
            self._sync_selection(key)
            if name in ("select_all", "clear_selection", "delete_selected"):
                self.legacy.tabs.select(self.tabs.currentIndex())
                if name == "delete_selected":
                    self.legacy.delete_selected(key)
                else:
                    getattr(self.legacy, name)(key)
            elif name in ("open_selected", "details"):
                getattr(self.legacy, name)(key)
            else:
                getattr(self.legacy, name)()
            self.refresh()

        def refresh(self):
            if self.legacy is None or self._syncing:
                return
            self._syncing = True
            try:
                self.legacy.subject.set(self.subject.currentText() or self.legacy.subject.get())
                self.legacy.category.set(self.category.currentText() or self.legacy.category.get())
                self.legacy.query.set(self.query.text())
                self.legacy.sort.set(self.sort.currentText() or self.legacy.sort.get())
                self.legacy.show()
                for widget, source in ((self.subject, self.legacy.subject_box),
                                       (self.category, self.legacy.category_box)):
                    values = [str(v) for v in source.cget("values")]
                    current = self.legacy.subject.get() if widget is self.subject else self.legacy.category.get()
                    widget.blockSignals(True)
                    widget.clear()
                    widget.addItems(values)
                    widget.setCurrentText(current)
                    widget.blockSignals(False)
                self.query.setText(self.legacy.query.get())
                self.sort.setCurrentText(self.legacy.sort.get())
                for key, _label in self._LISTS:
                    table = self.tables[key]
                    tree = self.legacy.lists[key]
                    table.setRowCount(0)
                    for iid in tree.get_children():
                        title = tree.item(iid, "text")
                        values = tree.item(iid, "values") or ("",)
                        row = table.rowCount()
                        table.insertRow(row)
                        title_item = QTableWidgetItem(str(title))
                        title_item.setData(Qt.UserRole, iid)
                        table.setItem(row, 0, title_item)
                        table.setItem(row, 1, QTableWidgetItem(str(values[0])))
            finally:
                self._syncing = False

    class QtResetPage(QWidget):
        """Qt confirmation surface for the existing reset and wipe workflow."""

        def __init__(self, model, parent=None):
            super().__init__(parent)
            self.model = model
            root = QVBoxLayout(self)
            root.setContentsMargins(config.scaled(28), config.scaled(22), config.scaled(28), config.scaled(22))
            title = QLabel(tr("🔄  重置"), self)
            title.setObjectName("pageTitle")
            root.addWidget(title)
            line = QFrame(self)
            line.setFrameShape(QFrame.HLine)
            root.addWidget(line)
            warning = QLabel(tr("⚠  警告:这会永久删除全部数据"), self)
            warning.setObjectName("resetWarning")
            warning.setWordWrap(True)
            root.addWidget(warning)
            row = QHBoxLayout()
            row.addWidget(QLabel(tr("输入 RESET 以确认"), self))
            self.confirm = QLineEdit(self)
            self.confirm.setMaxLength(16)
            row.addWidget(self.confirm)
            row.addStretch(1)
            root.addLayout(row)
            self.reset = QPushButton(tr("🔄  重置并清除数据"), self)
            self.reset.setObjectName("dangerButton")
            self.reset.setEnabled(False)
            self.confirm.textChanged.connect(lambda text: self.reset.setEnabled(text.strip() == "RESET"))
            self.reset.clicked.connect(self._reset)
            root.addWidget(self.reset, 0, Qt.AlignLeft)
            flavor = QLabel(tr("就这样回到一切之初,修正错误的抉择."), self)
            flavor.setWordWrap(True)
            flavor.setObjectName("flavor")
            root.addWidget(flavor)
            root.addStretch(1)

        def _reset(self):
            if self.confirm.text().strip() != "RESET":
                return
            self.model.var_confirm.set("RESET")
            self.model.on_reset()
            self.confirm.clear()

        def refresh(self):
            self.confirm.clear()

    class QtPrescriptPage(QWidget):
        """Qt version of the focused instruction screen for the urban theme."""

        def __init__(self, model, parent=None):
            super().__init__(parent)
            self.model = model
            self.legacy = getattr(model, "pages", {}).get("prescript")
            self.setObjectName("prescriptPage")
            root = QVBoxLayout(self)
            root.setContentsMargins(config.scaled(24), config.scaled(18), config.scaled(24), config.scaled(18))
            self.exit = QPushButton("×", self)
            self.exit.setObjectName("prescriptExit")
            self.exit.clicked.connect(self._exit)
            root.addWidget(self.exit, 0, Qt.AlignLeft)
            root.addStretch(1)
            center = QVBoxLayout()
            center.setAlignment(Qt.AlignCenter)
            self.mark = QLabel(self)
            self.mark.setAlignment(Qt.AlignCenter)
            path = config.asset_path("themes", "urban_will", "image", "Index.png")
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                self.mark.setPixmap(pixmap.scaled(config.scaled(128), config.scaled(128), Qt.KeepAspectRatio,
                                                  Qt.SmoothTransformation))
            center.addWidget(self.mark)
            self.code = QLabel(self)
            self.code.setObjectName("prescriptCode")
            self.code.setAlignment(Qt.AlignCenter)
            center.addWidget(self.code)
            self.body = QLabel(self)
            self.body.setObjectName("prescriptBody")
            self.body.setAlignment(Qt.AlignCenter)
            self.body.setWordWrap(True)
            self.body.setMaximumWidth(config.scaled(680))
            center.addWidget(self.body, 0, Qt.AlignHCenter)
            self.receive = QPushButton(tr("- 接收指令 -"), self)
            self.receive.setObjectName("prescriptReceive")
            self.receive.clicked.connect(self._receive)
            center.addWidget(self.receive, 0, Qt.AlignHCenter)
            self.editor = QPlainTextEdit(self)
            self.editor.setMaximumWidth(config.scaled(680))
            self.editor.setMaximumHeight(config.scaled(210))
            self.editor.hide()
            center.addWidget(self.editor, 0, Qt.AlignHCenter)
            self.editor_buttons = QWidget(self)
            edit_buttons = QHBoxLayout(self.editor_buttons)
            self.submit = QPushButton(tr("确认"), self.editor_buttons)
            self.abandon = QPushButton(tr("放弃"), self.editor_buttons)
            self.submit.clicked.connect(self._submit)
            self.abandon.clicked.connect(self._abandon)
            edit_buttons.addWidget(self.submit)
            edit_buttons.addWidget(self.abandon)
            self.editor_buttons.hide()
            center.addWidget(self.editor_buttons, 0, Qt.AlignHCenter)
            root.addLayout(center, 0)
            root.addStretch(1)

        def _engine(self):
            return getattr(self.legacy, "engine", None)

        def _exit(self):
            if self.legacy is not None:
                self.legacy._exit()

        def _receive(self):
            if self.legacy is not None:
                self.legacy._on_click()
            self.refresh()

        def _submit(self):
            engine = self._engine()
            if engine is not None:
                engine.submit_log(self.editor.toPlainText())
            self.refresh()

        def _abandon(self):
            engine = self._engine()
            if engine is not None:
                engine.abandon_log()
            self.refresh()

        def refresh(self):
            if self.legacy is None:
                return
            engine = self._engine()
            if engine is None:
                return
            current = engine.current
            body = self.legacy._body_text(current) if current else tr("谨遵指令之意")
            if current:
                from prescript.prescript_common import pad_code, type_code
                codes = [pad_code(type_code(current.get("type")))]
            else:
                from prescript.prescript_common import pad_code
                codes = [pad_code(str(item.get("code") or "")) for item in self.legacy._fresh_grants()]
                codes = codes or [pad_code("NONE")]
            self.body.setText(body)
            self.code.setText("\n".join(codes))
            self.receive.setEnabled(engine.can_draw() and not (current and current.get("type") == "X"))
            editing = bool(current and current.get("type") == "X")
            if editing and not self.editor.isVisible():
                self.editor.setPlainText(engine.log_prefill())
            self.editor.setVisible(editing)
            self.editor_buttons.setVisible(editing)

    class QtNetPage(QWidget):
        """Qt presentation for the network request workflow."""

        def __init__(self, model, parent=None):
            super().__init__(parent)
            self.model = model
            root = QVBoxLayout(self)
            root.setContentsMargins(0, 0, 0, 0)
            header = QHBoxLayout()
            header.setContentsMargins(config.scaled(28), config.scaled(22), config.scaled(28), config.scaled(8))
            title = QLabel(tr("🌐  联网申请"), self)
            title.setObjectName("pageTitle")
            header.addWidget(title)
            header.addStretch(1)
            file_button = QPushButton(tr("📁  文件通道"), self)
            file_button.clicked.connect(lambda: getattr(getattr(model, "file_channel", None), "open", lambda: None)())
            header.addWidget(file_button)
            root.addLayout(header)
            sep = QFrame(self)
            sep.setFrameShape(QFrame.HLine)
            sep.setFixedHeight(1)
            sep_layout = QHBoxLayout()
            sep_layout.setContentsMargins(config.scaled(26), 0, config.scaled(26), 0)
            sep_layout.addWidget(sep)
            root.addLayout(sep_layout)

            card = QWidget(self)
            card.setObjectName("netCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(config.scaled(22), config.scaled(18), config.scaled(22), config.scaled(18))
            self.status = QLabel(card)
            self.time = QLabel(card)
            self.time.setObjectName("time")
            self.sub = QLabel(card)
            self.sub.setWordWrap(True)
            self.progress = QProgressBar(card)
            self.progress.setTextVisible(False)
            self.quota = QLabel(card)
            self.quota.setObjectName("today")
            self.debt = QLabel(card)
            self.debt.setWordWrap(True)
            for widget in (self.status, self.time, self.sub, self.progress, self.quota, self.debt):
                card_layout.addWidget(widget)
            card_wrap = QHBoxLayout()
            card_wrap.setContentsMargins(config.scaled(26), config.scaled(20), config.scaled(26), 0)
            card_wrap.addWidget(card)
            root.addLayout(card_wrap)

            area = QWidget(self)
            area_layout = QVBoxLayout(area)
            area_layout.setContentsMargins(config.scaled(26), 0, config.scaled(26), config.scaled(22))
            form = QHBoxLayout()
            form.addWidget(QLabel(tr("分钟"), area))
            self.minutes = QSpinBox(area)
            self.minutes.setRange(1, 24 * 60)
            form.addWidget(self.minutes)
            form.addWidget(QLabel(tr("理由"), area))
            self.reason = QLineEdit(area)
            self.reason.setMinimumWidth(config.scaled(260))
            form.addWidget(self.reason, 1)
            area_layout.addLayout(form)
            buttons = QHBoxLayout()
            self.request = QPushButton(tr("📨  提交申请(需冷静期)"), area)
            self.quick = QPushButton(area)
            self.loan = QPushButton(tr("💳  紧急借贷"), area)
            self.request.clicked.connect(lambda: self._call("on_net_request"))
            self.quick.clicked.connect(lambda: self._call("on_net_quick"))
            self.loan.clicked.connect(lambda: self._call("on_net_loan"))
            buttons.addWidget(self.request)
            buttons.addWidget(self.quick)
            buttons.addWidget(self.loan)
            buttons.addStretch(1)
            area_layout.addLayout(buttons)
            self.form_area = area
            root.addWidget(area)

            self.controls = QWidget(self)
            control_layout = QHBoxLayout(self.controls)
            control_layout.setContentsMargins(config.scaled(26), 0, config.scaled(26), config.scaled(22))
            self.control = QPushButton(self.controls)
            self.upgrade = QPushButton(self.controls)
            self.control.clicked.connect(lambda: self._call("on_net_ctrl"))
            self.upgrade.clicked.connect(lambda: self._call("on_net_upgrade"))
            control_layout.addWidget(self.control)
            control_layout.addWidget(self.upgrade)
            control_layout.addStretch(1)
            root.addWidget(self.controls)
            root.addStretch(1)

        def _call(self, method):
            try:
                self.model.var_min.set(str(self.minutes.value()))
                self.model.var_reason.set(self.reason.text())
                getattr(self.model, method)()
            finally:
                self.refresh()

        def refresh(self):
            try:
                self.model.refresh_net(force=False)
            except Exception:
                pass
            self.status.setText(_tk_text(getattr(self.model, "net_lbl_status", None)))
            self.time.setText(_tk_text(getattr(self.model, "net_lbl_time", None), tr("离线")))
            self.sub.setText(_tk_text(getattr(self.model, "net_lbl_sub", None)))
            self.quota.setText(_tk_text(getattr(self.model, "net_lbl_quota", None)))
            self.debt.setText(_tk_text(getattr(self.model, "net_lbl_debt", None)))
            try:
                bar = self.model.net_bar
                self.progress.setMaximum(max(1, int(float(bar["maximum"]))))
                self.progress.setValue(int(float(bar["value"])))
            except Exception:
                pass
            mode = getattr(self.model, "_net_view", "offline")
            offline = mode == "offline"
            self.form_area.setVisible(offline)
            self.controls.setVisible(not offline)
            quick_mult = float(getattr(config, "QUICK_MULTIPLIER", 1.0))
            self.quick.setText(tr("⚡  快速访问(×{a:g})").format(a=quick_mult))
            if not offline:
                self.control.setText(tr("✕  取消申请") if mode == "pending" else tr("■  结束本次会话"))
                self.upgrade.setText(tr("⚡  改为快速访问(×{a:g})").format(a=quick_mult))
                self.upgrade.setVisible(mode == "pending")
            for target, source in ((self.request, "btn_net_req"), (self.quick, "btn_net_quick"), (self.loan, "btn_net_loan")):
                widget = getattr(self.model, source, None)
                try:
                    target.setEnabled(str(widget.cget("state")) != "disabled")
                except Exception:
                    target.setEnabled(True)

    class QtHourLogPage(QWidget):
        """Qt editor for the 24 hourly records, backed by HourLogPage rules."""

        def __init__(self, model, parent=None):
            super().__init__(parent)
            self.model = model
            self.legacy = getattr(model, "pages", {}).get("log")
            self._loading = False
            root = QVBoxLayout(self)
            root.setContentsMargins(0, 0, 0, 0)
            header = QHBoxLayout()
            header.setContentsMargins(config.scaled(28), config.scaled(18), config.scaled(28), config.scaled(4))
            title = QLabel(tr("📝  整点记录"), self)
            title.setObjectName("pageTitle")
            header.addWidget(title)
            header.addStretch(1)
            self.state = QLabel(self)
            self.date = QLabel(self)
            self.date.setObjectName("today")
            header.addWidget(self.date)
            header.addWidget(self.state)
            settings = QPushButton(tr("⚙  设置"), self)
            settings.clicked.connect(lambda: self.model.show_page("settings"))
            header.addWidget(settings)
            root.addLayout(header)
            self.date_line = QLabel(self)
            self.date_line.setObjectName("today")
            date_layout = QHBoxLayout()
            date_layout.setContentsMargins(config.scaled(28), config.scaled(8), config.scaled(28), config.scaled(6))
            date_layout.addWidget(self.date_line)
            root.addLayout(date_layout)
            scroll = QScrollArea(self)
            scroll.setWidgetResizable(True)
            body = QWidget(scroll)
            self.body_layout = QVBoxLayout(body)
            self.body_layout.setContentsMargins(config.scaled(10), config.scaled(6), config.scaled(10), config.scaled(12))
            self.edits = []
            for h in range(24):
                row = QHBoxLayout()
                label = QLabel(body)
                label.setMinimumWidth(config.scaled(150))
                first = QLineEdit(body)
                second = QLineEdit(body)
                first.editingFinished.connect(lambda hh=h: self._commit(hh, 0))
                second.editingFinished.connect(lambda hh=h: self._commit(hh, 1))
                row.addWidget(label)
                row.addWidget(first, 1)
                row.addWidget(second, 1)
                self.body_layout.addLayout(row)
                self.edits.append((label, first, second))
            self.body_layout.addStretch(1)
            scroll.setWidget(body)
            root.addWidget(scroll, 1)

        def _commit(self, row, half):
            if self._loading or self.legacy is None:
                return
            try:
                if half == 0:
                    self.legacy.rows[row][2].set(self.edits[row][1].text())
                else:
                    self.legacy.halves[row][1].set(self.edits[row][2].text())
                self.legacy._commit(row, half)
            except Exception:
                pass
            self.refresh()

        def refresh(self):
            if self.legacy is None:
                return
            self._loading = True
            try:
                self.legacy.refresh()
                self.state.setText(_tk_text(getattr(self.legacy, "lbl_state", None)))
                self.date_line.setText(_tk_text(getattr(self.legacy, "lbl_date", None)))
                for h, (label, first, second) in enumerate(self.edits):
                    legacy_row = self.legacy.rows[h]
                    label.setText(_tk_text(legacy_row[0]))
                    first.setText(str(legacy_row[2].get()))
                    second.setText(str(self.legacy.halves[h][1].get()))
                    split = bool(self.legacy._split.get(h))
                    second.setVisible(split)
                    first.setEnabled(str(legacy_row[1].cget("state")) != "disabled")
                    second.setEnabled(split and str(self.legacy.halves[h][0].cget("state")) != "disabled")
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
            self._current_key = "timer"
            self._qt_tick = 0
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
                            QtSettingsPage(self.model, self._stack) if key == "settings" else
                            QtNetPage(self.model, self._stack) if key == "net" else
                            QtHourLogPage(self.model, self._stack) if key == "log" else
                            QtContractPage(self.model, self._stack) if key == "contract" else
                            QtKnowledgePage(self.model, self._stack) if key == "knowledge" else
                            QtResetPage(self.model, self._stack) if key == "reset" else
                            QtPrescriptPage(self.model, self._stack) if key == "prescript" else QLabel(
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
                QPushButton#dangerButton {{ background: {COLORS.get('danger_fill', COLORS.get('danger', accent))}; color: {COLORS.get('on_danger_fill', text)}; border-color: {COLORS.get('danger', accent)}; font-weight: 700; }}
                QLabel#resetWarning {{ color: {COLORS.get('danger', accent)}; background: {COLORS.get('danger_bg', card)}; border: 1px solid {COLORS.get('danger', accent)}; padding: {config.scaled(12)}px; font-weight: 700; }}
                QWidget#prescriptPage {{ background: #000000; color: #ffffff; }}
                QLabel#prescriptCode {{ color: #96c4ec; font-family: Consolas, monospace; font-size: {px('big')}px; font-weight: 700; padding: {config.scaled(10)}px; }}
                QLabel#prescriptBody {{ color: #ffffff; font-size: {body_px}px; padding: {config.scaled(10)}px; }}
                QPushButton#prescriptReceive {{ background: #000000; color: #ffffff; border: 0; font-size: {button_px}px; }}
                QPushButton#prescriptExit {{ background: #000000; color: #2a3535; border: 0; padding: 2px 8px; }}
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
            self._current_key = key
            self.sidebar.setVisible(key != "prescript")
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
            elif key in ("net", "log"):
                self._pages[key].refresh()
            elif key == "contract":
                self._pages[key].refresh()
            elif key == "knowledge":
                self._pages[key].refresh()
            elif key == "reset":
                self._pages[key].refresh()
            elif key == "prescript":
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
            self._qt_tick += 1
            if self._qt_tick % 15 == 0 and self._current_key in ("net", "log", "contract", "prescript"):
                self._pages[self._current_key].refresh()

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
