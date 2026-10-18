import math
import tkinter as tk
from tkinter import ttk
from datetime import datetime

import config
import contract as C
from config import COLORS, fmt_hm, fmt_ms
from i18n import tr
from .widgets import ScrollArea, cfg, show_pack, hide_pack


def term_name(tid: str) -> str:
    return tr(C.BY_ID[tid][3])


def term_short(tid: str) -> str:
    """格子里的名字: 去掉"环境:" / "目标:", 名字和罗马数字分开 (英文用更短的名字)."""
    base, _sep, num = C.BY_ID[tid][3].split(":", 1)[-1].partition("-")
    return f"{tr(base)} {num}".strip()


def term_desc(tid: str) -> str:
    return tr(C.BY_ID[tid][4])


def fmt_hms(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 3600}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def marks(level: int) -> str:
    return "▲" * int(level)


HARD_LEVEL = 13                  # 总等级到这里就提示"难度极大"

RESULTS = {"completed": "完成", "failed": "失败: 违反词条", "idle": "失败: 空闲超过 30 分钟",
           "abandoned": "提前结束"}

# 合约页的主题 (设置 → 合约, CONTRACT_THEME). "ops" 危机合约: 深色页面, 浅灰的词条板, 六边形格子,
# 黑色的已选中词条, 红色的开始按钮; 侧边栏在合约页也换成这一套颜色.
OPS = {
    "bg": "#1f2023", "text": "#f2f2f2", "dim": "#9a9ca1", "line": "#c8202f",
    "board": "#d6d7da", "band": "#2c2d31", "band_text": "#ececec", "band_dim": "#9a9ca1",
    "tile": "#45474d", "tile_text": "#f4f4f4", "tile_rim": "#a3202c",
    "tile_on": "#1d6fc4", "tile_on_text": "#ffffff", "tile_on_rim": "#9cc8ff",
    "tile_off": "#bdbec1", "tile_off_text": "#8e9095", "tile_off_rim": "#a9aaad",
    "hint": "#2c2d31",
    "sel": "#141416", "sel_text": "#f2f2f2", "sel_dim": "#77797e",
    "badge_hi": "#c8202f", "badge_hi_text": "#ffffff", "badge_lo": "#f2f2f2", "badge_lo_text": "#141416",
    "bar": "#26272b", "level": "#ffffff", "warn": "#ffffff",
    "start": "#9e1b28", "start_hover": "#bf2433", "start_text": "#ffffff",
    "clear": "#3a3b40", "clear_hover": "#4a4b51", "clear_text": "#ececec",
    "tag": "#c8202f", "tag_text": "#ffffff",
    "live": "#18181b", "live_rim": "#c8202f", "danger": "#ff4d5a", "idle": "#f2b33d",
    "side": "#18191c", "side_text": "#cfd0d3", "side_on": "#c8202f", "side_on_text": "#ffffff",
    "side_hover": "#2c2d31", "side_rim": "#2c2d31",
}


def palette() -> dict:
    """当前合约页主题的颜色. "ui" = 跟随界面主题."""
    if getattr(config, "CONTRACT_THEME", "ops") != "ui":
        return OPS
    c = COLORS
    return {
        "bg": c["bg"], "text": c["text"], "dim": c["text_dim"], "line": c["border"],
        "board": c["bg"], "band": c["bg_card"], "band_text": c["text"], "band_dim": c["text_dim"],
        "tile": c["bg_card"], "tile_text": c["text"], "tile_rim": c["border"],
        "tile_on": c["danger"], "tile_on_text": c["on_accent"], "tile_on_rim": c["danger"],
        "tile_off": c["bg"], "tile_off_text": c["border"], "tile_off_rim": c["border"],
        "hint": c["text_dim"],
        "sel": c["bg_card"], "sel_text": c["text"], "sel_dim": c["text_dim"],
        "badge_hi": c["danger"], "badge_hi_text": c["on_accent"], "badge_lo": c["border"], "badge_lo_text": c["text"],
        "bar": c["bg_card"], "level": c["danger"], "warn": c["warn"],
        "start": c["danger"], "start_hover": c["danger_hover"], "start_text": c["on_accent"],
        "clear": c["border"], "clear_hover": c["bg"], "clear_text": c["text"],
        "tag": c["danger"], "tag_text": c["on_accent"],
        "live": c["bg_card"], "live_rim": c["danger"], "danger": c["danger"], "idle": c["warn"],
        "side": None,                      # 侧边栏不变
    }


class ContractPage(tk.Frame):
    """合约页 (MECHANICS 18): 词条板 (行 = 等级, 列 = 类型, 同一类型在同一列), 下面是已选中的词条 (两栏),
    最下面一条: 清空 | 危机等级 | 时长 / 完成后的休息时间 | 开始行动. 合约进行中时只能看, 不能改.
    颜色按 设置 → 合约 的"合约页主题"."""

    TILE_W, TILE_H, GAP, BAND_W = 76, 64, 7, 148

    def __init__(self, master, contract, on_start=None, on_abandon=None, net_used=None, on_change=None):
        self.P = P = palette()
        super().__init__(master, bg=P["bg"])
        self.c = contract
        self.net_used = net_used or (lambda: 0.0)    # 今天所有设备已用的联网额度 (秒)
        self.on_start = on_start
        self.on_abandon = on_abandon
        self.on_change = on_change or (lambda: None)  # 选的词条变了 (侧边栏上的危机等级跟着变)
        self._hover = ""
        self._list_key = None
        self._tile_state = {}

        hdr = tk.Frame(self, bg=P["bg"])
        hdr.pack(fill="x", padx=24, pady=(18, 6))
        tk.Label(hdr, text=tr("合约详情"), bg=P["tag"], fg=P["tag_text"], font=config.font("small", bold=True),
                 padx=8, pady=2).pack(side="left", anchor="s", pady=(0, 6))
        tk.Label(hdr, text=tr("⚔  合约"), bg=P["bg"], fg=P["text"],
                 font=config.font("h1", bold=True)).pack(side="left", padx=(12, 0))
        self.lbl_state = tk.Label(hdr, text="", bg=P["bg"], fg=P["danger"], font=config.font("body", bold=True))
        self.lbl_state.pack(side="right")
        tk.Frame(self, bg=P["line"], height=2).pack(fill="x", padx=22)

        self.area = ScrollArea(self, bg=P["bg"])
        self.area.pack(fill="both", expand=True, padx=(22, 8), pady=(10, 0))
        body = self.area.inner
        self._build_live(body)
        self._build_board(body)
        self._build_selected(body)
        self.lbl_last = tk.Label(body, text="", bg=P["bg"], fg=P["dim"], font=config.font("small"),
                                 anchor="w", justify="left")
        self.lbl_last.pack(fill="x", pady=(8, 4))
        self._build_bar()
        self.area.bind_children()

    # ---- 进行中: 剩余时间, 违反次数, 空闲 ----

    def _build_live(self, body):
        P = self.P
        fr = tk.Frame(body, bg=P["live"], highlightbackground=P["live_rim"], highlightthickness=2)
        self.live_card = fr
        top = tk.Frame(fr, bg=P["live"])
        top.pack(fill="x", padx=16, pady=(10, 0))
        self.lbl_left = tk.Label(top, text="", bg=P["live"], fg=P["text"],
                                 font=config.font("big", bold=True), anchor="w")
        self.lbl_left.pack(side="left")
        self.lbl_viol = tk.Label(top, text="", bg=P["live"], fg=P["danger"], font=config.font("h2", bold=True))
        self.lbl_viol.pack(side="right")
        self.lbl_live = tk.Label(fr, text="", bg=P["live"], fg=P["dim"], font=config.font("body"),
                                 anchor="w", justify="left")
        self.lbl_live.pack(fill="x", padx=16, pady=(0, 2))
        self.lbl_idle = tk.Label(fr, text="", bg=P["live"], fg=P["idle"], font=config.font("body", bold=True),
                                 anchor="w", justify="left")
        self.lbl_idle.pack(fill="x", padx=16, pady=(0, 10))

    # ---- 词条板: 左边一条竖写的 "CONTRACT LIST" 和各行的等级, 右边六边形的格子 ----

    def _build_board(self, body):
        P, s = self.P, config.scaled
        self.tw, self.th, self.gap, self.band = s(self.TILE_W), s(self.TILE_H), s(self.GAP), s(self.BAND_W)
        cols = len(C.GROUPS)
        self.board_w = self.band + self.gap + cols * (self.tw + self.gap)
        self.board_h = self.gap + C.MAX_LEVEL * (self.th + self.gap) + s(26)
        wrap = tk.Frame(body, bg=P["board"])
        wrap.pack(fill="x", pady=(0, 6))
        self.canvas = tk.Canvas(wrap, bg=P["board"], highlightthickness=0, height=self.board_h)
        self.canvas.pack(fill="x")
        # 窗口太窄放不下整块词条板时, 下面出一条横向滑动条
        self.hbar = ttk.Scrollbar(wrap, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=self.hbar.set, scrollregion=(0, 0, self.board_w, self.board_h))
        self.canvas.bind("<Configure>", self._board_resized)
        self.canvas.bind("<Shift-MouseWheel>", lambda e: self.canvas.xview_scroll(int(-e.delta / 120), "units"))
        self._draw_board()

    def _board_resized(self, event):
        if event.width < self.board_w - 2:
            show_pack(self.hbar, fill="x")
        else:
            hide_pack(self.hbar)
            self.canvas.xview_moveto(0)

    @staticmethod
    def _hexagon(x, y, w, h):
        k = w * 0.2
        return (x + k, y, x + w - k, y, x + w, y + h / 2, x + w - k, y + h, x + k, y + h, x, y + h / 2)

    def _draw_board(self):
        P, cv, s = self.P, self.canvas, config.scaled
        cv.create_rectangle(0, 0, self.band, self.board_h, fill=P["band"], outline="")
        # 竖写的标题 (同参考图的 "CONTRACT LIST: 可选合约")
        cv.create_text(s(22), self.board_h / 2, text="CONTRACT LIST", angle=90, fill=P["band_dim"],
                       font=config.font("h2", bold=True))
        cv.create_text(s(44), self.board_h / 2, text=tr("可选词条"), angle=90, fill=P["band_text"],
                       font=config.font("body", bold=True))
        for lv in range(1, C.MAX_LEVEL + 1):
            # 每一行的等级: 小字 "Contingency Level" 和一个 ▲ 标记在左, 大数字在右 (同参考图)
            y = self.gap + (lv - 1) * (self.th + self.gap)
            x0 = s(62)
            cv.create_text(x0, y + s(6), text="Contingency\nLevel", anchor="nw", fill=P["band_dim"],
                           font=(config.FONT_FAMILY, max(6, s(6))))
            by = y + self.th - s(22)
            cv.create_rectangle(x0, by, x0 + s(24), by + s(14), fill=P["badge_lo"], outline="")
            cv.create_text(x0 + s(12), by + s(7), text="▲", fill=P["badge_hi"],
                           font=(config.FONT_FAMILY, max(6, s(7)), "bold"))
            cv.create_text(self.band - s(10), y + self.th / 2, text=str(lv), anchor="e", fill=P["band_text"],
                           font=config.font("h1", bold=True))
        for col, group in enumerate(C.GROUPS):
            x = self.band + self.gap + col * (self.tw + self.gap)
            for tid, g, lv, _n, _d, _x in C.TERMS:
                if g != group:
                    continue
                y = self.gap + (lv - 1) * (self.th + self.gap)
                tag = f"t_{tid}"
                cv.create_polygon(self._hexagon(x, y, self.tw, self.th), fill=P["tile"], outline=P["tile_rim"],
                                  width=2, tags=(tag, tag + "_shape"))
                cv.create_text(x + self.tw / 2, y + self.th / 2, text=marks(lv) + "\n" + term_short(tid),
                               fill=P["tile_text"], justify="center", width=self.tw * 0.66,
                               font=(config.FONT_FAMILY, max(6, s(8)), "bold"), tags=(tag, tag + "_text"))
                cv.tag_bind(tag, "<Button-1>", lambda e, t=tid: self._click(t))
                cv.tag_bind(tag, "<Enter>", lambda e, t=tid: self._enter(t))
                cv.tag_bind(tag, "<Leave>", lambda e, t=tid: self._leave(t))
        # 鼠标停在格子上: 最下面一行写出这个词条的内容
        self.hover_id = cv.create_text(self.band + self.gap, self.board_h - s(13), text="", anchor="w",
                                       fill=P["hint"], font=config.font("small", bold=True))

    def _selected(self):
        return self.c.terms if self.c.active else self.c.draft

    def _click(self, tid: str):
        if self.c.active:
            return
        sel = list(self.c.draft)
        if tid in sel:
            sel.remove(tid)
        elif C.blocked_by(tid, sel):
            return
        else:
            sel.append(tid)
        self.c.set_draft(sel)
        self.refresh()
        self.on_change()
        self._enter(tid)

    def _enter(self, tid: str):
        self._hover = tid
        self.canvas.configure(cursor="arrow" if self.c.active else "hand2")
        text = f"{marks(C.BY_ID[tid][2])}  {term_name(tid)} : {term_desc(tid)}"
        other = C.blocked_by(tid, self._selected())
        if other and tid not in self._selected():
            text += "   " + tr("[与 {name} 冲突]", name=term_name(other))
        self.canvas.itemconfigure(self.hover_id, text=text)

    def _leave(self, tid: str):
        if self._hover == tid:
            self._hover = ""
            self.canvas.configure(cursor="")
            self.canvas.itemconfigure(self.hover_id, text="")

    # ---- 已选中的词条: 黑底, 两栏 ----

    def _build_selected(self, body):
        P, s = self.P, config.scaled
        fr = tk.Frame(body, bg=P["sel"])
        fr.pack(fill="x")
        band = tk.Canvas(fr, bg=P["sel"], highlightthickness=0, width=self.band - s(40), height=s(176))
        band.pack(side="left", fill="y")
        band.bind("<Configure>", lambda e: self._draw_sel_band(band, e.height))
        self.list_box = tk.Frame(fr, bg=P["sel"])
        self.list_box.pack(side="left", fill="both", expand=True, padx=(4, 12), pady=10)
        self.list_box.grid_columnconfigure(0, weight=1, uniform="sel")
        self.list_box.grid_columnconfigure(1, weight=1, uniform="sel")

    def _draw_sel_band(self, band, h):
        P, s = self.P, config.scaled
        band.delete("all")
        band.create_text(s(22), h / 2, text="CONTRACT SELECTED", angle=90, fill=P["sel_dim"],
                         font=config.font("small", bold=True))
        band.create_text(s(44), h / 2, text=tr("已选中词条"), angle=90, fill=P["sel_text"],
                         font=config.font("body", bold=True))

    def _rows(self, sel):
        """已选中的词条: (等级, 文字, 是否失效). 合约中按开始时选的列出, 降过级的写出降成了什么."""
        c = self.c
        if not c.active:
            return [(C.BY_ID[t][2], f"{term_name(t)} : {term_desc(t)}", False) for t in sel]
        out = []
        for t in c.orig:
            now = next((x for x in c.terms if x == t or C.BY_ID[x][1] == C.BY_ID[t][1]), "")
            if not now:
                out.append((C.BY_ID[t][2], f"{term_name(t)}   " + tr("[已失效]"), True))
            elif now != t:
                out.append((c.term_level(now), f"{term_name(now)} : {term_desc(now)}   "
                            + tr("[原 {name}]", name=term_short(t)), False))
            else:
                text = f"{term_name(t)} : {term_desc(t)}"
                if c.term_level(t) < C.BY_ID[t][2]:
                    text += "   " + tr("[降了 {n} 级]", n=C.BY_ID[t][2] - c.term_level(t))
                out.append((c.term_level(t), text, False))
        return out

    def _fill_list(self, sel):
        P = self.P
        rows = self._rows(sel)
        key = tuple(rows)
        if key == self._list_key:
            return
        self._list_key = key
        for w in self.list_box.winfo_children():
            w.destroy()
        if not rows:
            tk.Label(self.list_box, text="—", bg=P["sel"], fg=P["sel_dim"],
                     font=config.font("body")).grid(row=0, column=0, sticky="w")
        half = (len(rows) + 1) // 2           # 先排满左栏, 再排右栏
        for i, (lv, text, void) in enumerate(rows):
            r = tk.Frame(self.list_box, bg=P["sel"])
            r.grid(row=i % half, column=i // half, sticky="we", pady=2, padx=(0, 10))
            hi = lv >= 2 and not void
            tk.Label(r, text=marks(lv) or "—", bg=P["badge_hi"] if hi else P["badge_lo"],
                     fg=P["badge_hi_text"] if hi else P["badge_lo_text"], width=4,
                     font=config.font("small", bold=True)).pack(side="left", anchor="n", padx=(0, 8))
            lbl = tk.Label(r, text=text, bg=P["sel"], fg=P["sel_dim"] if void else P["sel_text"],
                           font=config.font("small"), anchor="w", justify="left")
            lbl.pack(side="left", fill="x", expand=True)
            lbl.bind("<Configure>", lambda e, l=lbl: l.configure(wraplength=max(80, e.width - 4)))
        self.area.bind_children()

    # ---- 最下面一条: 清空 | 危机等级 | 时长 / 完成后的休息时间 | 难度提示 | 开始行动 ----

    def _button(self, parent, text, bg, hover, fg, command, big=False):
        b = tk.Label(parent, text=text, bg=bg, fg=fg, cursor="hand2",
                     font=config.font("h2" if big else "body", bold=True),
                     padx=config.scaled(34 if big else 16), pady=config.scaled(12 if big else 8))
        b.bind("<Button-1>", lambda e: command())
        b.bind("<Enter>", lambda e: b.configure(bg=hover))
        b.bind("<Leave>", lambda e: b.configure(bg=bg))
        return b

    def _build_bar(self):
        P, s = self.P, config.scaled
        bar = tk.Frame(self, bg=P["bar"])
        bar.pack(fill="x", side="bottom")
        inner = tk.Frame(bar, bg=P["bar"])
        inner.pack(fill="x", padx=22, pady=10)
        self.btn_clear = self._button(inner, tr("🗑  清空"), P["clear"], P["clear_hover"], P["clear_text"], self._clear)
        self.btn_clear.pack(side="left")
        lv = tk.Frame(inner, bg=P["bar"])
        lv.pack(side="left", padx=(20, 0))
        tk.Label(lv, text=tr("危机等级"), bg=P["bar"], fg=P["dim"], font=config.font("small", bold=True)).pack(anchor="w")
        self.lbl_level = tk.Label(lv, text="0", bg=P["bar"], fg=P["level"], font=config.font("big", bold=True))
        self.lbl_level.pack(anchor="w")
        self.strip = tk.Canvas(inner, bg=P["bar"], highlightthickness=0, width=s(120), height=s(30))
        self.strip.pack(side="left", padx=(10, 0))
        self.lbl_info = tk.Label(inner, text="", bg=P["bar"], fg=P["text"], font=config.font("small"),
                                 anchor="w", justify="left")
        self.lbl_info.pack(side="left", padx=(14, 0))
        self.btn_start = self._button(inner, tr("开始行动"), P["start"], P["start_hover"], P["start_text"],
                                      self._start, big=True)
        self.btn_end = self._button(inner, tr("■  提前结束"), P["start"], P["start_hover"], P["start_text"],
                                    lambda: self.on_abandon and self.on_abandon(), big=True)
        self.lbl_warn = tk.Label(inner, text=tr("⚠  当前合约难度极大 请谨慎行动"), bg=P["bar"], fg=P["warn"],
                                 font=config.font("small", bold=True), justify="left",
                                 wraplength=s(170))

    def _draw_strip(self, level: int):
        """危机等级旁边的一排三角: 每级一个, 每 5 个一组."""
        P, s, cv = self.P, config.scaled, self.strip
        cv.delete("all")
        n = min(level, 25)
        w = s(8)
        for i in range(25):
            x = i * (w + 1) + (i // 5) * s(3)
            if x + w > int(cv["width"]):
                break
            cv.create_polygon(x, s(26), x + w / 2, s(26) - w * math.sqrt(3) / 2 * 1.4, x + w, s(26),
                              fill=P["level"] if i < n else P["clear"], outline="")

    def _clear(self):
        if not self.c.active:
            self.c.set_draft([])
            self.refresh()
            self.on_change()

    def _start(self):
        if not self.c.active and self.on_start:
            self.on_start(list(self.c.draft))

    # ---- 刷新 ----

    def level(self) -> int:
        return self.c.level if self.c.active else C.level_of(self.c.draft)

    def refresh(self):
        P, c, now = self.P, self.c, datetime.now()
        active = c.active
        sel = self._selected()
        for tid in C.BY_ID:
            if tid in sel:
                st = ("tile_on", "tile_on_text", "tile_on_rim")
            elif active or C.blocked_by(tid, sel):
                st = ("tile_off", "tile_off_text", "tile_off_rim")
            else:
                st = ("tile", "tile_text", "tile_rim")
            if self._tile_state.get(tid) != st:
                self._tile_state[tid] = st
                self.canvas.itemconfigure(f"t_{tid}_shape", fill=P[st[0]], outline=P[st[2]])
                self.canvas.itemconfigure(f"t_{tid}_text", fill=P[st[1]])
        self._fill_list(sel)

        level = self.level()
        orig = c.orig_level if active else level
        cfg(self.lbl_level, text=str(level))
        self._draw_strip(level)
        info = [tr("时长 {t}", t=fmt_hm(C.duration_of(c.orig if active else sel))),
                tr("完成后休息 +{n} 分钟", n=C.rest_minutes(level))]
        if orig != level:
            info.insert(0, tr("开始时 {n}", n=orig))
        cfg(self.lbl_info, text="\n".join(info))
        btn = self.btn_end if active else self.btn_start
        other = self.btn_start if active else self.btn_end
        hide_pack(other)
        show_pack(btn, side="right")
        if level >= HARD_LEVEL:
            show_pack(self.lbl_warn, side="right", padx=(0, 16))
        else:
            hide_pack(self.lbl_warn)
        if active:
            hide_pack(self.btn_clear)
        else:
            show_pack(self.btn_clear, side="left", before=self.lbl_level.master)

        if active:
            end = c.end_at
            cfg(self.lbl_state, text=tr("合约进行中"))
            cfg(self.lbl_left, text=fmt_hms((end - now).total_seconds()))
            cfg(self.lbl_viol, text=tr("违反 {n} / {m}", n=c.violations, m=c.violation_limit))
            live = f"{c.start_at:%H:%M} — {end:%m-%d %H:%M}"
            left = c.net_left(self.net_used())
            if left is not None:
                live += "   ·   " + tr("合约的联网额度还剩 {t}", t=fmt_hm(left))
            cfg(self.lbl_live, text=live)
            idle = c.idle_left(now)
            cfg(self.lbl_idle, text="" if idle is None else tr("空闲中: {t} 后合约终止", t=fmt_ms(idle)))
            if self.live_card.winfo_manager() != "pack":
                self.live_card.pack(fill="x", pady=(0, 8), before=self.area.inner.pack_slaves()[0])
        else:
            cfg(self.lbl_state, text="")
            hide_pack(self.live_card)

        last = c.last
        if last and not active:
            try:
                a = datetime.fromisoformat(last["start"])
                b = datetime.fromisoformat(last["end"])
                when = f"{a:%m-%d %H:%M} — {b:%H:%M}"
            except (KeyError, TypeError, ValueError):
                when = ""
            text = tr("上次合约: {r} · 危机等级 {lv} · {when}", r=tr(RESULTS.get(last.get("result"), "")),
                      lv=last.get("level", 0), when=when)
            if last.get("reward"):
                text += "   ·   " + tr("休息 +{n} 分钟", n=round(last["reward"] / 60))
            cfg(self.lbl_last, text=text)
        else:
            cfg(self.lbl_last, text="")

    def show(self):
        self.refresh()
        self.area.scroll_to_top()
