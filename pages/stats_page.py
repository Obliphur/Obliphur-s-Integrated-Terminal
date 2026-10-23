import math
import random
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk

import config
import contract as C
import window_guard
from config import COLORS, fmt_hm
from data_manager import month_prefixes, tally_items
from i18n import tr, is_en
from .widgets import ScrollArea, smooth, cfg, show_pack, hide_pack
from .contract_page import term_line, term_short, marks   # 合约记录按合约页的逻辑显示词条


# 图表颜色都跟随当前主题 (config.CHART_PALETTE / COLORS 会在切换主题时原地更新)
PALETTE = config.CHART_PALETTE

# 占比低于这个值的项归入「其他」; 鼠标停在「其他」上可以看到具体是哪些
OTHER_BELOW = 0.01
OTHER_TIP_LINES = 25
FOG_ITEMS = 10              # 黑雾: 饼图里填多少个没有意义的项
CONTRACT_ROWS = 5           # 统计页的合约记录: 看最近几个顺利完成的合约 (黑雾时补满这么多条)
VIOLATION_ROWS = 15         # 违规记录页: 最多留几条 (黑雾时补满, 内容全是 ???)


def _c(key: str) -> str:
    return COLORS[key]


WEEKDAYS_ZH = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
WEEKDAYS_EN = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _level_text(orig, level) -> str:
    """一条合约的等级, 和合约页一样: 最终等级[最初等级-扣的等级] (没扣过只写最终等级)."""
    return C.level_label(_int(level), _int(orig))


def _fog_contracts(seed) -> list:
    """黑雾: 5 条假记录 (初始等级 0 ~ 99, 失效等级 1 ~ 30; 词条随机填充); 真实条数不到 5 条时也补满.
    每条: ((初始等级, 失效等级), 和 record_rows 一样的词条行 [(等级, 词条 id, 是否失效, 原词条 id)], {} (没有组合))."""
    ids = [t[0] for t in C.TERMS]
    out = []
    for i in range(CONTRACT_ROWS):
        rnd = random.Random(f"{seed}/contracts/{i}")
        rows = [(C.BY_ID[t][2], t, rnd.random() < 0.35, "") for t in rnd.sample(ids, rnd.randint(2, 5))]
        out.append(((rnd.randint(0, 99), rnd.randint(1, 30)), rows, {}))
    return out


class ContractTermsDialog(tk.Toplevel):
    """统计页: 一条合约记录的具体词条 (失效的变灰)."""

    def __init__(self, master, a, b, rows: list, combos: dict = None):
        super().__init__(master, bg=COLORS["bg"], highlightbackground=COLORS["accent"],
                         highlightthickness=2)
        self.title(tr("⚔  合约词条"))
        self.resizable(False, False)
        self.attributes("-topmost", True)
        body = tk.Frame(self, bg=COLORS["bg"])
        body.pack(padx=22, pady=18)
        tk.Label(body, text=tr("⚔  合约词条"), bg=COLORS["bg"], fg=COLORS["text"],
                 font=config.font("h2", bold=True), anchor="w").pack(anchor="w")
        tk.Label(body, text=tr("等级 {lv}", lv=_level_text(a, b)), bg=COLORS["bg"],
                 fg=COLORS["accent"], font=config.font("body", bold=True),
                 anchor="w").pack(anchor="w", pady=(2, 8))
        # 和合约页一样: 等级三角 + 名字:描述 + [原 X] / [已失效], 失效的变灰;
        # 完成时凑齐的组合: 成员已经放在一起置顶 (record_rows), 用组合的颜色写 (黄色用深一点的, 浅色主题里也看得清)
        tint = {"yellow": "#d9a400", "purple": C.COMBO_COLORS["purple"],
                "gray": C.COMBO_COLORS["gray"], "red": C.COMBO_COLORS["red"]}
        combos = combos or {}
        for lv, tid, void, orig in rows or []:
            combo = C.COMBOS_BY_ID.get(combos.get(tid, ""))
            fg = tint[combo["color"]] if combo and not void else None
            text = term_line(tid)
            if orig:
                text += "   " + tr("[原 {name}]", name=term_short(orig))
            if void:
                text += "   " + tr("[已失效]")
            r = tk.Frame(body, bg=COLORS["bg"])
            r.pack(anchor="w", fill="x", pady=1)
            tk.Label(r, text=marks(lv) or "—", bg=COLORS["bg"],
                     fg=COLORS["text_dim"] if void else (fg or COLORS["accent"]), width=4, anchor="nw",
                     font=config.font("small", bold=True)).pack(side="left", anchor="n", padx=(0, 8))
            tk.Label(r, text=text, bg=COLORS["bg"], fg=COLORS["text_dim"] if void else (fg or COLORS["text"]),
                     font=config.font("body"), anchor="w", justify="left").pack(side="left", fill="x", expand=True)
        ttk.Button(body, text=tr("关闭"), style="Accent.TButton",
                   command=self.destroy).pack(anchor="e", pady=(12, 0))
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")


class StatsPage(tk.Frame):
    """📊 Statistics:整点记录活动, 联网理由与各程序的前台时间, 饼状图 + 图例.

    右上角切换看本月还是上月 (专注计时页固定看最近 7 天, 不显示这个切换).
    第一页(整点记录)只在整点记录开启时出现;每一页可单独切换「占比 / 总时间」."""

    def __init__(self, master, data_manager, net_guard, focus_live=None):
        super().__init__(master, bg=COLORS["bg"])
        self.dm = data_manager
        self.net = net_guard
        self.focus_live = focus_live
        self.page = "log"
        self.mode = {"log": "pct", "net": "pct", "window": "pct"}
        self.month = "this"          # "this" 本月 / "last" 上月
        self.device = "all"          # 窗口页看哪台设备: "all" 全部 / "self" 本机 / 设备 id
        self._device_keys = None
        self._tabs_log_on = None
        self._focus_days = []
        self._focus_geom = None
        self._tip = None            # 「其他」的悬停提示 (一个无边框的小窗)
        # 合约的黑雾 (MECHANICS 18.3): 不为 None 时只显示随机的假数据 (同一个合约里不变)
        self.fog = lambda: None
        # 合约记录页的数据来源: 合约的 history (顺利完成的那几条)
        self.contract_log = lambda: []
        self._contract_sig = None
        # 违规记录页的数据来源: 最近 15 条违规的提示文字 (新的在前)
        self.violation_log = lambda: []
        self._violation_sig = None
        self.contract_mode = "done"        # 合约页: "done" 通过记录 / "violations" 违规记录
        self._build()
        self.after(60000, self._focus_tick)

    def _build(self):
        bg = COLORS["bg"]

        hdr = tk.Frame(self, bg=bg)
        hdr.pack(fill="x", padx=28, pady=(18, 4))
        tk.Label(
            hdr, text=tr("📊  统计"), bg=bg, fg=COLORS["text"],
            font=config.font("h1", bold=True)
        ).pack(side="left")

        # 右上角: 看本月还是上月
        self.month_bar = tk.Frame(hdr, bg=bg)
        self.month_bar.pack(side="right")
        self.month_btns = {}
        for key, text in (("this", tr("本月")), ("last", tr("上月"))):
            b = tk.Button(
                self.month_bar, text=text, relief="flat", bd=0, padx=14, pady=6,
                font=config.font("body", bold=True), cursor="hand2",
                command=lambda k=key: self._set_month(k)
            )
            b.pack(side="right", padx=(4, 0))
            self.month_btns[key] = b

        bar = tk.Frame(self, bg=bg)
        bar.pack(fill="x", padx=26, pady=(8, 8))
        self.tab_btns = {}
        for key, text in (("log", tr("📝  整点记录")), ("net", tr("🌐  联网理由")),
                          ("focus", tr("⏱  专注计时")), ("window", tr("🪟  窗口")),
                          ("contracts", tr("⚔  合约"))):
            b = tk.Button(
                bar, text=text, relief="flat", bd=0, padx=14, pady=6,
                font=config.font("body", bold=True), cursor="hand2",
                command=lambda k=key: self._set_page(k)
            )
            self.tab_btns[key] = b
        self.mode_bar = tk.Frame(bar, bg=bg)
        self.mode_bar.pack(side="right")
        self.mode_btns = {}
        for key, text in (("total", tr("总时间")), ("pct", tr("占比"))):
            b = tk.Button(
                self.mode_bar, text=text, relief="flat", bd=0, padx=12, pady=6,
                font=config.font("small"), cursor="hand2",
                command=lambda k=key: self._set_mode(k)
            )
            b.pack(side="right", padx=(4, 0))
            self.mode_btns[key] = b
        tk.Label(self.mode_bar, text=tr("显示:"), bg=bg, fg=COLORS["text_dim"],
                 font=config.font("small")).pack(side="right", padx=(0, 2))

        # 合约页: 通过记录 / 违规记录 (通过记录在左; 和上面的显示方式同一个样式)
        self.contract_mode_bar = tk.Frame(bar, bg=bg)
        self.contract_btns = {}
        for key, text in (("done", tr("通过记录")), ("violations", tr("违规记录"))):
            b = tk.Button(
                self.contract_mode_bar, text=text, relief="flat", bd=0, padx=12, pady=6,
                font=config.font("small"), cursor="hand2",
                command=lambda k=key: self._set_contract_mode(k)
            )
            b.pack(side="left", padx=(0, 4))
            self.contract_btns[key] = b

        # 专注计时页的图例
        self.focus_legend = tk.Frame(bar, bg=bg)
        for color, text in ((_c("focus_study"), tr("专注")), (_c("focus_break"), tr("休息")),
                            (_c("chart_empty"), tr("空白"))):
            tk.Frame(self.focus_legend, bg=color, width=config.scaled(12),
                     height=config.scaled(12)).pack(side="left", padx=(10, 4))
            tk.Label(self.focus_legend, text=text, bg=bg, fg=COLORS["text_dim"],
                     font=config.font("small")).pack(side="left")

        # 窗口页: 绑定了其他设备时按设备切换 (MECHANICS 15.2)
        self.device_bar = tk.Frame(self, bg=bg)
        self.device_btns = {}

        self.sep = ttk.Separator(self, orient="horizontal", style="UI.TSeparator")
        self.sep.pack(fill="x", padx=26)

        body = tk.Frame(self, bg=bg)
        self.pie_body = body
        body.pack(fill="both", expand=True, padx=20, pady=(10, 16))

        # 专注计时页: 最近 7 天, 每天 24 根条, 每根按比例分成专注 / 休息 / 空白
        self.focus_body = tk.Frame(self, bg=COLORS["bg_card"],
                                   highlightbackground=COLORS["border"], highlightthickness=1)
        self.focus_cv = tk.Canvas(self.focus_body, bg=COLORS["bg_card"], highlightthickness=0)
        self.focus_cv.pack(fill="both", expand=True, padx=6, pady=6)
        self.focus_cv.bind("<Configure>", lambda e: self._draw_focus())
        self.focus_cv.bind("<Motion>", self._focus_hover)
        self.focus_cv.bind("<Leave>", lambda e: self.focus_cv.delete("tip"))

        # 合约记录 (通过记录): 最近 5 条顺利完成的合约 (黑雾时是 5 条假数据)
        card = COLORS["bg_card"]
        self.contracts_body = tk.Frame(self, bg=card,
                                       highlightbackground=COLORS["border"], highlightthickness=1)
        head = tk.Frame(self.contracts_body, bg=card)
        head.pack(fill="x", padx=16, pady=(12, 8))
        tk.Label(head, text=tr("⚔  合约记录"), bg=card, fg=COLORS["text"],
                 font=config.font("h2", bold=True)).pack(side="left")
        self.contract_rows = tk.Frame(self.contracts_body, bg=card)
        self.contract_rows.pack(fill="both", expand=True, padx=10, pady=(0, 12))

        # 违规记录: 最近 15 条合约违规的提示文字 (黑雾时全部是 ???, 不足 15 条补满)
        self.violations_body = tk.Frame(self, bg=card,
                                        highlightbackground=COLORS["border"], highlightthickness=1)
        head = tk.Frame(self.violations_body, bg=card)
        head.pack(fill="x", padx=16, pady=(12, 8))
        tk.Label(head, text=tr("⚠  违规记录"), bg=card, fg=COLORS["text"],
                 font=config.font("h2", bold=True)).pack(side="left")
        self.violation_area = ScrollArea(self.violations_body, height=config.scaled(430), bg=card)
        self.violation_area.pack(fill="both", expand=True, padx=10, pady=(0, 12))
        self.violation_rows = self.violation_area.inner

        # 一张卡片: 上面是月份和合计, 下面左边饼图, 右边图例
        card = COLORS["bg_card"]
        col = tk.Frame(body, bg=card, highlightbackground=COLORS["border"], highlightthickness=1)
        col.pack(fill="both", expand=True, padx=6)
        title = tk.Label(col, text="", bg=card, fg=COLORS["text"],
                         font=config.font("h2", bold=True), anchor="w")
        title.pack(fill="x", padx=14, pady=(10, 0))
        total = tk.Label(col, text="", bg=card, fg=COLORS["accent"],
                         font=config.font("body", bold=True), anchor="w")
        total.pack(fill="x", padx=14)
        row = tk.Frame(col, bg=card)
        row.pack(fill="both", expand=True, padx=8, pady=(6, 10))
        size = config.scaled(260)
        canvas = tk.Canvas(row, width=size, height=size, bg=card, highlightthickness=0)
        canvas.pack(side="left", anchor="n", padx=(10, 20), pady=(4, 0))
        canvas.tag_bind("other", "<Enter>", lambda e: self._pie_hover(0, e))
        canvas.tag_bind("other", "<Motion>", lambda e: self._pie_hover(0, e))
        canvas.tag_bind("other", "<Leave>", lambda e: self._hide_tip())
        legend = ScrollArea(row, height=size, bg=card)
        legend.pack(side="left", fill="both", expand=True)
        self.cols = [{"title": title, "total": total, "canvas": canvas,
                      "size": size, "legend": legend,
                      "rows": [], "empty": None, "sig": None, "keys": None,
                      "other": [], "other_row": None, "grand": 0.0}]


    @smooth
    def _set_page(self, key: str):
        if key == "contracts":
            self.contract_mode = "done"      # 每次打开合约页都从"通过记录"开始
        self.page = key
        self.refresh()

    @smooth
    def _set_device(self, key: str):
        self.device = key
        self.refresh()

    def _sync_device_bar(self):
        """窗口页且绑定了其他设备时显示设备切换: 全部 / 本机 / 每台设备."""
        devices = [("all", tr("全部设备"))] + [
            (k, tr("本机") if k == "self" else n) for k, n in self.dm.window_devices()]
        show = self.page == "window" and len(devices) > 2
        keys = tuple(devices)
        if keys != self._device_keys:
            self._device_keys = keys
            for b in self.device_btns.values():
                b.destroy()
            self.device_btns = {}
            for k, text in devices:
                b = tk.Button(self.device_bar, text=text, relief="flat", bd=0, padx=12, pady=5,
                              font=config.font("small", bold=True), cursor="hand2",
                              command=lambda kk=k: self._set_device(kk))
                b.pack(side="left", padx=(0, 4))
                self.device_btns[k] = b
        if self.device not in dict(devices):
            self.device = "all"
        for k, b in self.device_btns.items():
            on = k == self.device
            cfg(b, bg=COLORS["text"] if on else COLORS["bg_card"],
                fg=COLORS["bg"] if on else COLORS["text"],
                activebackground=COLORS["border"], activeforeground=COLORS["text"])
        if show:
            show_pack(self.device_bar, fill="x", padx=26, pady=(0, 8), before=self.sep)
        else:
            hide_pack(self.device_bar)

    @smooth
    def _set_month(self, key: str):
        self.month = key
        self.refresh()

    @smooth
    def _set_mode(self, key: str):
        self.mode[self.page] = key
        self.refresh()

    @smooth
    def _set_contract_mode(self, key: str):
        """合约页: 通过记录 / 违规记录."""
        self.contract_mode = key
        self.refresh()

    def _style_buttons(self):
        for key, b in self.tab_btns.items():
            on = key == self.page
            cfg(b, bg=COLORS["accent"] if on else COLORS["bg_card"],
                     fg=COLORS["on_accent"] if on else COLORS["text"],
                     activebackground=COLORS["accent_hover"] if on else COLORS["border"],
                     activeforeground=COLORS["on_accent"] if on else COLORS["text"])
        for key, b in self.month_btns.items():
            on = key == self.month
            cfg(b, bg=COLORS["accent"] if on else COLORS["bg_card"],
                     fg=COLORS["on_accent"] if on else COLORS["text"],
                     activebackground=COLORS["accent_hover"] if on else COLORS["border"],
                     activeforeground=COLORS["on_accent"] if on else COLORS["text"])
        cur = self.mode.get(self.page, "pct")
        for key, b in self.mode_btns.items():
            on = key == cur
            cfg(b, bg=COLORS["text"] if on else COLORS["bg_card"],
                     fg=COLORS["bg"] if on else COLORS["text_dim"],
                     activebackground=COLORS["border"], activeforeground=COLORS["text"])
        for key, b in self.contract_btns.items():
            on = key == self.contract_mode
            cfg(b, bg=COLORS["text"] if on else COLORS["bg_card"],
                     fg=COLORS["bg"] if on else COLORS["text_dim"],
                     activebackground=COLORS["border"], activeforeground=COLORS["text"])


    def _data(self, prefix: str):
        seed = self.fog()
        if seed is not None:
            # 黑雾: 不显示真正的数据, 10 个没有意义的项, 比例随机, 名字都是 ???
            rnd = random.Random(f"{seed}/{self.page}/{prefix}")
            return {"???" + "\u200b" * i: rnd.uniform(1, 9) * 3600 for i in range(FOG_ITEMS)}
        if self.page == "log":
            return self.dm.hour_activity_seconds(prefix)
        if self.page == "window":
            names = {window_guard.BLANK: tr("空白"), window_guard.UNKNOWN: tr("未知程序")}
            # 手机发来的是包名, 有应用名就显示应用名
            for led in (self.dm.peer_ledgers_by_id() or {}).values():
                labels = led.get("labels") if isinstance(led.get("labels"), dict) else {}
                for k, v in labels.items():
                    names.setdefault(k, str(v))
            out = {}
            for k, v in self.dm.window_seconds(prefix, self.device).items():
                n = names.get(k, k)
                out[n] = out.get(n, 0.0) + v
            return out
        return tally_items(self.net.usage_by_reason(prefix))

    def _color(self, i: int, name: str) -> str:
        """第 i 项的颜色. 窗口页的「空白」和专注计时页一样用空白色, 不占调色板."""
        if self.page == "window" and name == tr("空白"):
            return _c("chart_empty")
        return PALETTE[i] if i < len(PALETTE) else _c("chart_other")

    def refresh(self):
        self._hide_tip()
        log_on = bool(self.dm.hour_enabled)
        if not log_on and self.page == "log":
            self.page = "net"
        # 标签按钮只在 "整点记录" 开关变化时才重新排列 (重复 pack 会触发整页重新布局)
        if self._tabs_log_on != log_on:
            self._tabs_log_on = log_on
            for b in self.tab_btns.values():
                b.pack_forget()
            if log_on:
                self.tab_btns["log"].pack(side="left", padx=(0, 4))
            self.tab_btns["net"].pack(side="left", padx=(0, 4))
            self.tab_btns["focus"].pack(side="left", padx=(0, 4))
            self.tab_btns["window"].pack(side="left", padx=(0, 4))
            self.tab_btns["contracts"].pack(side="left", padx=(0, 4))
        self._style_buttons()
        self._sync_device_bar()

        if self.page == "contracts":
            hide_pack(self.month_bar)
            hide_pack(self.mode_bar)
            hide_pack(self.focus_legend)
            hide_pack(self.focus_body)
            hide_pack(self.pie_body)
            show_pack(self.contract_mode_bar, side="right")
            if self.contract_mode == "violations":
                hide_pack(self.contracts_body)
                show_pack(self.violations_body, fill="both", expand=True, padx=26, pady=(10, 16))
                self._draw_violations()
            else:
                hide_pack(self.violations_body)
                show_pack(self.contracts_body, fill="both", expand=True, padx=26, pady=(10, 16))
                self._draw_contracts()
            return
        hide_pack(self.contract_mode_bar)
        hide_pack(self.contracts_body)
        hide_pack(self.violations_body)
        if self.page == "focus":
            hide_pack(self.month_bar)
            hide_pack(self.mode_bar)
            show_pack(self.focus_legend, side="right")
            hide_pack(self.pie_body)
            show_pack(self.focus_body, fill="both", expand=True, padx=26, pady=(10, 16))
            self._load_focus()
            self._draw_focus()
            return
        hide_pack(self.focus_legend)
        hide_pack(self.focus_body)
        show_pack(self.month_bar, side="right")
        show_pack(self.mode_bar, side="right")
        show_pack(self.pie_body, fill="both", expand=True, padx=20, pady=(10, 16))

        this, last = month_prefixes()
        name, prefix = (tr("上月"), last) if self.month == "last" else (tr("本月"), this)
        self._draw_column(self.cols[0], f"{name} · {prefix}", self._data(prefix))

    # ---- 合约记录 (过去 5 条顺利完成的合约, MECHANICS 8.1) ----

    def _contract_rows_data(self) -> list:
        """[((初始等级, 失效等级), [(等级, 词条 id, 失效?, 原词条 id)], {组合成员: 组合 id})]; 黑雾时是 5 条假数据.
        词条行和合约页一样 (record_rows); 完成时凑齐的组合的成员置顶 (record_marks)."""
        seed = self.fog()
        if seed is not None:
            return _fog_contracts(seed)
        recs = C.recent_completed(self.contract_log(), CONTRACT_ROWS)
        return [((int(r.get("orig_level") or r.get("level") or 0), int(r.get("level") or 0)),
                 C.record_rows(r), C.record_marks(r)) for r in recs]

    def _draw_contracts(self):
        data = self._contract_rows_data()
        sig = tuple((a, b, tuple(terms), tuple(sorted(cm.items()))) for (a, b), terms, cm in data)
        if sig == self._contract_sig:
            return
        self._contract_sig = sig
        for w in self.contract_rows.winfo_children():
            w.destroy()
        if not data:
            tk.Label(self.contract_rows, text=tr("还没有顺利完成的合约."),
                     bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                     font=config.font("body"), anchor="w").pack(anchor="w", padx=8, pady=8)
            return
        for (a, b), terms, cm in data:
            self._contract_row(a, b, terms, cm).pack(fill="x", padx=6, pady=(0, 6))

    def _contract_row(self, a: int, b: int, terms: list, cm: dict) -> tk.Frame:
        card = COLORS["bg_card"]
        row = tk.Frame(self.contract_rows, bg=card, cursor="hand2",
                       highlightbackground=COLORS["border"], highlightthickness=1)
        tk.Label(row, text=tr("等级"), bg=card, fg=COLORS["text_dim"],
                 font=config.font("small")).pack(side="left", padx=(14, 8), pady=10)
        tk.Label(row, text=_level_text(a, b), bg=card, fg=COLORS["accent"],
                 font=config.font("h2", bold=True)).pack(side="left")
        tk.Label(row, text=tr("点击查看词条"), bg=card, fg=COLORS["text_dim"],
                 font=config.font("small")).pack(side="right", padx=14)
        for w in [row] + list(row.winfo_children()):
            w.bind("<Button-1>", lambda e, aa=a, bb=b, tt=terms, mm=cm: self._show_contract(aa, bb, tt, mm))
        return row

    def _show_contract(self, a: int, b: int, terms: list, cm: dict = None):
        """点一条合约记录: 弹出具体词条 (失效的变灰, 凑齐的组合的成员置顶并用组合的颜色写)."""
        try:
            ContractTermsDialog(self.winfo_toplevel(), a, b, terms, cm)
        except Exception:
            pass

    # ---- 违规记录 (最近 15 条违规的提示文字, MECHANICS 8.1) ----

    def _violation_rows_data(self) -> list:
        """[(时间, 文字)]; 黑雾时是 15 条 ??? (不足 15 条补满)."""
        if self.fog() is not None:
            return [("???", "???")] * VIOLATION_ROWS
        data = []
        for rec in list(self.violation_log() or [])[:VIOLATION_ROWS]:
            if not isinstance(rec, dict):
                continue
            text = str(rec.get("text") or "").strip()
            if text:
                data.append((str(rec.get("at") or "???"), text))
        return data

    def _draw_violations(self):
        data = self._violation_rows_data()
        sig = tuple(data)
        if sig == self._violation_sig:
            return
        self._violation_sig = sig
        card = COLORS["bg_card"]
        for w in self.violation_rows.winfo_children():
            w.destroy()
        if not data:
            tk.Label(self.violation_rows, text=tr("还没有违规记录."), bg=card,
                     fg=COLORS["text_dim"], font=config.font("body"), anchor="w").pack(
                anchor="w", padx=8, pady=8)
            self.violation_area.bind_children()      # 整条栏都能滚
            return
        for at, text in data:
            row = tk.Frame(self.violation_rows, bg=card,
                           highlightbackground=COLORS["border"], highlightthickness=1)
            row.pack(fill="x", padx=6, pady=(0, 6))
            tk.Label(row, text=at, bg=card, fg=COLORS["text_dim"],
                     font=config.font("small"), anchor="w").pack(fill="x", padx=14, pady=(8, 0))
            lbl = tk.Label(row, text=text, bg=card, fg=COLORS["text"], font=config.font("body"),
                           anchor="w", justify="left", wraplength=config.scaled(640))
            lbl.pack(fill="x", padx=14, pady=(2, 8))
            row.bind("<Configure>",
                     lambda e, l=lbl: l.config(wraplength=max(config.scaled(200), e.width - config.scaled(40))))
        self.violation_area.bind_children()          # 整条栏都能滚 (不只是右边那一条滚动条)

    def _draw_column(self, col, title: str, data: dict):
        pct_mode = self.mode[self.page] == "pct"
        items = sorted(data.items(), key=lambda kv: (-kv[1], kv[0]))
        # 数据和显示方式都没变 → 什么都不用画 (切回这页时几乎零开销)
        sig = (title, pct_mode, tuple(items))
        if col["sig"] == sig:
            return
        col["sig"] = sig

        total = sum(v for _, v in items)
        cfg(col["title"], text=title)
        cfg(col["total"], text=tr("合计 {t} · {n} 项").format(t=fmt_hm(total), n=len(items)))

        # 占比 ≥ 1% 的项单独上色 (最多用完调色板), 其余归入「其他」
        main = [(k, v) for k, v in items if total > 0 and v / total >= OTHER_BELOW]
        main = main[:len(PALETTE)]
        shown = {k for k, _ in main}
        col["other"] = [(k, v) for k, v in items if k not in shown]
        slices = [(k, v, self._color(i, k)) for i, (k, v) in enumerate(main)]
        if col["other"]:
            slices.append(("???" if self.fog() is not None else tr("其他"),
                           sum(v for _, v in col["other"]), _c("chart_other")))
        col["other_row"] = len(main) if col["other"] else None
        col["grand"] = total
        self._draw_pie(col["canvas"], col["size"], slices, total, pct_mode,
                       other=bool(col["other"]))
        self._fill_legend(col, slices, total, pct_mode)

    def _fill_legend(self, col, items, total, pct_mode):
        """items: [(名称, 秒, 颜色)]. 图例的每一行控件循环使用: 只在行数不够时新建, 多出来的收起来."""
        inner = col["legend"].inner
        card = COLORS["bg_card"]
        if col["empty"] is None:
            col["empty"] = tk.Label(inner, text=tr("暂无数据"), bg=card,
                                    fg=COLORS["text_dim"], font=config.font("small"))
        if items:
            hide_pack(col["empty"])
        else:
            show_pack(col["empty"], anchor="w", padx=6, pady=4)

        rows = col["rows"]
        created = False
        while len(rows) < len(items):
            row = tk.Frame(inner, bg=card)
            sw = tk.Frame(row, bg=card, width=config.scaled(12), height=config.scaled(12))
            sw.pack(side="left", padx=(2, 8))
            val = tk.Label(row, text="", bg=card, fg=COLORS["text"], width=9, anchor="e",
                           font=config.font("small", bold=True))
            val.pack(side="right")
            name = tk.Label(row, text="", bg=card, fg=COLORS["text"], anchor="w",
                            font=config.font("small"))
            name.pack(side="left", fill="x", expand=True)
            idx = len(rows)
            for w in (row, sw, val, name):
                w.bind("<Enter>", lambda e, c=col, i=idx: self._legend_hover(c, i, e))
                w.bind("<Motion>", lambda e, c=col, i=idx: self._legend_hover(c, i, e))
                w.bind("<Leave>", lambda e: self._hide_tip())
            rows.append((row, sw, val, name))
            created = True

        for i, (row, sw, val, name) in enumerate(rows):
            if i >= len(items):
                hide_pack(row)
                continue
            k, v, color = items[i]
            cfg(sw, bg=color)
            cfg(val, text=f"{v / total * 100:.1f}%" if pct_mode else fmt_hm(v))
            cfg(name, text=k)
            show_pack(row, fill="x", padx=4, pady=1)
        if created:
            col["legend"].bind_children()
        # 只有项目或顺序变了才滚回顶部; 窗口页每分钟刷新一次, 不要打断正在看的图例
        keys = tuple(k for k, _v, _c2 in items)
        if col["keys"] != keys:
            col["keys"] = keys
            col["legend"].scroll_to_top()

    # ---- 「其他」的悬停提示 ----

    def _other_text(self, col) -> str:
        items = col["other"]
        total = sum(v for _, v in items) or 1.0
        grand = col["grand"] or total
        pct_mode = self.mode.get(self.page, "pct") == "pct"
        lines = [f"{k}    {v / grand * 100:.2f}%" if pct_mode else f"{k}    {fmt_hm(v)}"
                 for k, v in items[:OTHER_TIP_LINES]]
        if len(items) > OTHER_TIP_LINES:
            lines.append(tr("…以及另外 {n} 项").format(n=len(items) - OTHER_TIP_LINES))
        return "\n".join(lines)

    def _show_tip(self, col, x_root: int, y_root: int):
        if not col["other"]:
            return
        if self._tip is None or not self._tip.winfo_exists():
            self._tip = tk.Toplevel(self)
            self._tip.overrideredirect(True)
            self._tip.attributes("-topmost", True)
            self._tip.label = tk.Label(
                self._tip, bg=COLORS["text"], fg=COLORS["bg"], justify="left",
                font=config.font("small"), padx=config.scaled(8), pady=config.scaled(6))
            self._tip.label.pack()
        lbl = self._tip.label
        text = self._other_text(col)
        if lbl.cget("text") != text:
            lbl.config(text=text)
        self._tip.update_idletasks()
        w, h = self._tip.winfo_reqwidth(), self._tip.winfo_reqheight()
        x, y = x_root + config.scaled(14), y_root + config.scaled(14)
        if x + w > self.winfo_screenwidth():
            x = x_root - w - config.scaled(8)
        if y + h > self.winfo_screenheight():
            y = y_root - h - config.scaled(8)
        self._tip.geometry(f"+{int(x)}+{int(y)}")
        self._tip.deiconify()

    def _hide_tip(self):
        if self._tip is not None:
            try:
                self._tip.withdraw()
            except tk.TclError:
                self._tip = None

    def _pie_hover(self, i: int, event):
        self._show_tip(self.cols[i], event.x_root, event.y_root)

    def _legend_hover(self, col, idx: int, event):
        if col["other_row"] == idx:
            self._show_tip(col, event.x_root, event.y_root)
        else:
            self._hide_tip()

    def _draw_pie(self, cv: tk.Canvas, size: int, slices, total: float, pct_mode: bool,
                  other: bool = False):
        """other: 最后一块是「其他」, 给它打上 other 标签, 鼠标停在上面时显示具体项."""
        cv.delete("all")
        pad = 6
        x0, y0, x1, y1 = pad, pad, size - pad, size - pad
        cx, cy, r = size / 2.0, size / 2.0, size / 2.0 - pad
        if total <= 0:
            cv.create_oval(x0, y0, x1, y1, fill=_c("chart_empty"), outline="")
            cv.create_text(cx, cy, text=tr("暂无数据"), fill=COLORS["text_dim"],
                           font=config.font("small"))
            return
        if len(slices) == 1:
            cv.create_oval(x0, y0, x1, y1, fill=slices[0][2], outline=COLORS["bg_card"],
                           tags=("other",) if other else ())
        start = 90.0
        labels = []
        for i, (name, v, color) in enumerate(slices):
            extent = -360.0 * v / total
            if len(slices) > 1:
                cv.create_arc(x0, y0, x1, y1, start=start, extent=extent,
                              fill=color, outline=COLORS["bg_card"], width=2, style="pieslice",
                              tags=("other",) if other and i == len(slices) - 1 else ())
            if v / total >= 0.06:
                mid = math.radians(start + extent / 2.0)
                rr = 0.62 * r if len(slices) > 1 else 0.0
                text = f"{v / total * 100:.0f}%" if pct_mode else fmt_hm(v)
                # 空白色很浅, 上面的字用暗色
                fg = COLORS["text_dim"] if color == _c("chart_empty") else COLORS["on_accent"]
                labels.append((cx + rr * math.cos(mid), cy - rr * math.sin(mid), text, fg))
            start += extent
        for x, y, text, fg in labels:
            cv.create_text(x, y, text=text, fill=fg, font=config.font("tiny", bold=True))

    # ---- 专注计时 ----

    def _load_focus(self):
        live = self.focus_live() if self.focus_live else None
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        seed = self.fog()
        if seed is not None:
            # 黑雾: 每一小时随机填上专注 / 休息 / 空白
            now = datetime.now()
            self._focus_days = []
            for i in range(self.dm.FOCUS_DAYS):
                day = today - timedelta(days=i)
                rnd = random.Random(f"{seed}/focus/{day:%Y-%m-%d}")
                hours = []
                for h in range(24):
                    st = rnd.choice((0, 0, rnd.uniform(0, 3600), 3600))
                    br = rnd.uniform(0, 3600 - st)
                    hours.append((st, br) if day + timedelta(hours=h) <= now else (0.0, 0.0))
                self._focus_days.append((day, hours))
            return
        self._focus_days = [
            (today - timedelta(days=i), self.dm.focus_hours(today - timedelta(days=i), live))
            for i in range(self.dm.FOCUS_DAYS)
        ]

    def _focus_tick(self):
        try:
            if not self.winfo_exists():
                return              # 界面重建后旧页面已销毁, 不再续约
        except Exception:
            return
        try:
            if self.page == "focus" and self.winfo_ismapped():
                self._load_focus()
                self._draw_focus()
        finally:
            self.after(60000, self._focus_tick)

    @staticmethod
    def _day_label(day: datetime, today: bool, fog: bool = False) -> str:
        if fog:
            return "???"
        wd = (WEEKDAYS_EN if is_en() else WEEKDAYS_ZH)[day.weekday()]
        return tr("今天") + f" {wd}" if today else f"{day:%m-%d} {wd}"

    def _draw_focus(self):
        cv = self.focus_cv
        cv.delete("all")
        self._focus_geom = None
        if not self._focus_days:
            return
        w = cv.winfo_width()
        if w < 50:
            return
        card = COLORS["bg_card"]
        left = config.scaled(118)
        right = config.scaled(128)
        top = config.scaled(24)
        gap = config.scaled(16)
        n = len(self._focus_days)
        # 行高随窗口高度伸缩
        avail = cv.winfo_height() - top - config.scaled(8) - gap * (n - 1)
        row_h = max(config.scaled(28), min(config.scaled(64), avail / n))
        x0, x1 = left, max(left + 240, w - right)
        slot = (x1 - x0) / 24.0
        bar_w = max(2.0, slot * 0.78)
        now = datetime.now()
        small = config.font("tiny")

        for h in range(0, 24, 3):
            cv.create_text(x0 + h * slot, top - config.scaled(8), text=f"{h:02d}",
                           anchor="w", fill=COLORS["text_dim"], font=small)
        cv.create_text(x1, top - config.scaled(8), text="24", anchor="e",
                       fill=COLORS["text_dim"], font=small)

        for i, (day, hours) in enumerate(self._focus_days):
            y0 = top + i * (row_h + gap)
            y1 = y0 + row_h
            cv.create_text(config.scaled(12), (y0 + y1) / 2, anchor="w",
                           text=self._day_label(day, i == 0, self.fog() is not None),
                           fill=COLORS["text"] if i == 0 else COLORS["text_dim"],
                           font=config.font("small", bold=(i == 0)))
            for h, (st, br) in enumerate(hours):
                bx0 = x0 + h * slot + (slot - bar_w) / 2
                bx1 = bx0 + bar_w
                future = day + timedelta(hours=h) > now
                cv.create_rectangle(bx0, y0, bx1, y1, outline="",
                                    fill=_c("chart_future") if future else _c("chart_empty"))
                hs = row_h * st / 3600.0
                hb = row_h * br / 3600.0
                if hs > 0:
                    cv.create_rectangle(bx0, y1 - hs, bx1, y1, outline="", fill=_c("focus_study"))
                if hb > 0:
                    cv.create_rectangle(bx0, y1 - hs - hb, bx1, y1 - hs, outline="",
                                        fill=_c("focus_break"))
            tot_s = sum(st for st, _ in hours)
            tot_b = sum(br for _, br in hours)
            tx = x1 + config.scaled(12)
            cv.create_text(tx, y0 + row_h * 0.3, anchor="w", fill=_c("focus_study"),
                           font=config.font("small", bold=True),
                           text=f"{tr('专注')} {fmt_hm(tot_s)}")
            cv.create_text(tx, y0 + row_h * 0.75, anchor="w", fill=_c("focus_break"),
                           font=config.font("small"),
                           text=f"{tr('休息')} {fmt_hm(tot_b)}")
        self._focus_geom = (x0, slot, top, row_h, gap)

    @staticmethod
    def _fmt_min(sec: float) -> str:
        if 0 < sec < 30:
            return "<1"
        return str(int(round(sec / 60.0)))

    def _focus_hover(self, event):
        cv = self.focus_cv
        cv.delete("tip")
        g = self._focus_geom
        if not g:
            return
        x0, slot, top, row_h, gap = g
        h = int((event.x - x0) // slot)
        i = int((event.y - top) // (row_h + gap))
        if not (0 <= h < 24 and 0 <= i < len(self._focus_days)):
            return
        if event.y - top - i * (row_h + gap) > row_h:
            return
        day, hours = self._focus_days[i]
        st, br = hours[h]
        blank = max(0.0, 3600.0 - st - br)
        start = day + timedelta(hours=h)
        if start > datetime.now():
            body = tr("尚未到来")
        else:
            body = tr("专注 {s} 分 · 休息 {b} 分 · 空白 {e} 分").format(
                s=self._fmt_min(st), b=self._fmt_min(br), e=self._fmt_min(blank))
        text = f"{day:%m-%d}  {h:02d}:00–{(h + 1) % 24:02d}:00\n{body}"

        pad = config.scaled(6)
        tid = cv.create_text(0, 0, text=text, anchor="nw", fill=COLORS["bg"],
                             font=config.font("small"), tags="tip", justify="left")
        bx = cv.bbox(tid)
        tw, th = bx[2] - bx[0], bx[3] - bx[1]
        x = event.x + config.scaled(14)
        y = event.y + config.scaled(14)
        if x + tw + pad * 2 > cv.winfo_width():
            x = event.x - tw - pad * 2 - config.scaled(8)
        if y + th + pad * 2 > cv.winfo_height():
            y = event.y - th - pad * 2 - config.scaled(8)
        cv.coords(tid, x + pad, y + pad)
        rect = cv.create_rectangle(x, y, x + tw + pad * 2, y + th + pad * 2,
                                   fill=COLORS["text"], outline="", tags="tip")
        cv.tag_raise(tid, rect)

    def show(self):
        self.refresh()
