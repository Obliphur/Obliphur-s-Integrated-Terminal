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


class ContractPage(tk.Frame):
    """合约页 (MECHANICS 18): 词条按等级 (行) 和类型 (列) 排成格子, 同一类型在同一列.
    下面是已选中的词条, 右边是总等级 (危机等级), 时长, 休息倍率和开始按钮. 合约进行中时只能看, 不能改."""

    TILE_W, TILE_H = 76, 62

    def __init__(self, master, contract, on_start=None, on_abandon=None, net_used=None):
        super().__init__(master, bg=COLORS["bg"])
        self.c = contract
        self.net_used = net_used or (lambda: 0.0)    # 今天所有设备已用的联网额度 (秒)
        self.on_start = on_start
        self.on_abandon = on_abandon
        self.tiles = {}
        self._hover = ""
        self._list_key = None
        bg = COLORS["bg"]

        hdr = tk.Frame(self, bg=bg)
        hdr.pack(fill="x", padx=28, pady=(22, 8))
        tk.Label(hdr, text=tr("⚔  合约"), bg=bg, fg=COLORS["text"],
                 font=config.font("h1", bold=True)).pack(side="left")
        self.lbl_state = tk.Label(hdr, text="", bg=bg, fg=COLORS["danger"],
                                  font=config.font("body", bold=True))
        self.lbl_state.pack(side="right")
        ttk.Separator(self, orient="horizontal", style="UI.TSeparator").pack(fill="x", padx=26)

        self.area = ScrollArea(self, bg=bg)
        self.area.pack(fill="both", expand=True, padx=(26, 12), pady=(10, 12))
        body = self.area.inner

        self._build_live(body)
        self._build_grid(body)
        self.lbl_hover = tk.Label(body, text="", bg=bg, fg=COLORS["text_dim"], font=config.font("small"),
                                  anchor="w", justify="left")
        self.lbl_hover.pack(fill="x", pady=(4, 8))
        self._build_bottom(body)
        self.lbl_last = tk.Label(body, text="", bg=bg, fg=COLORS["text_dim"], font=config.font("small"),
                                 anchor="w", justify="left")
        self.lbl_last.pack(fill="x", pady=(10, 0))
        self.area.bind_children()

    # ---- 进行中: 剩余时间, 违反次数, 空闲 ----

    def _build_live(self, body):
        card = COLORS["bg_card"]
        fr = tk.Frame(body, bg=card, highlightbackground=COLORS["danger"], highlightthickness=1)
        self.live_card = fr
        top = tk.Frame(fr, bg=card)
        top.pack(fill="x", padx=16, pady=(12, 0))
        self.lbl_left = tk.Label(top, text="", bg=card, fg=COLORS["text"],
                                 font=config.font("big", bold=True), anchor="w")
        self.lbl_left.pack(side="left")
        self.lbl_viol = tk.Label(top, text="", bg=card, fg=COLORS["danger"],
                                 font=config.font("h2", bold=True))
        self.lbl_viol.pack(side="right")
        self.lbl_live = tk.Label(fr, text="", bg=card, fg=COLORS["text_dim"], font=config.font("body"),
                                 anchor="w", justify="left")
        self.lbl_live.pack(fill="x", padx=16, pady=(2, 2))
        self.lbl_idle = tk.Label(fr, text="", bg=card, fg=COLORS["warn"], font=config.font("body", bold=True),
                                 anchor="w", justify="left")
        self.lbl_idle.pack(fill="x", padx=16, pady=(0, 12))

    # ---- 词条格子: 行 = 等级, 列 = 类型 ----

    def _build_grid(self, body):
        bg = COLORS["bg"]
        grid = tk.Frame(body, bg=bg)
        grid.pack(anchor="w", pady=(4, 0))
        w, h = config.scaled(self.TILE_W), config.scaled(self.TILE_H)
        pad = config.scaled(3)
        for lv in range(1, C.MAX_LEVEL + 1):
            lab = tk.Frame(grid, bg=bg, width=config.scaled(36), height=h)
            lab.grid(row=lv, column=0, padx=(0, pad), pady=pad)
            lab.pack_propagate(False)
            tk.Label(lab, text=str(lv), bg=bg, fg=COLORS["text"],
                     font=config.font("h1", bold=True)).pack(side="left")
            tk.Label(lab, text=marks(1), bg=bg, fg=COLORS["danger"],
                     font=config.font("small")).pack(side="left", anchor="s", pady=(0, 6))
        for col, group in enumerate(C.GROUPS, start=1):
            for tid, g, lv, _n, _d, _x in C.TERMS:
                if g != group:
                    continue
                fr = tk.Frame(grid, width=w, height=h, highlightthickness=1, cursor="hand2")
                fr.grid(row=lv, column=col, padx=pad, pady=pad)
                fr.pack_propagate(False)
                lbl = tk.Label(fr, text=marks(lv) + "\n" + term_short(tid), font=config.font("small", bold=True),
                               wraplength=w - config.scaled(8), justify="center")
                lbl.pack(fill="both", expand=True)
                for x in (fr, lbl):
                    x.bind("<Button-1>", lambda e, t=tid: self._click(t))
                    x.bind("<Enter>", lambda e, t=tid: self._enter(t))
                    x.bind("<Leave>", lambda e, t=tid: self._leave(t))
                self.tiles[tid] = (fr, lbl)

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

    def _enter(self, tid: str):
        self._hover = tid
        text = f"{marks(C.BY_ID[tid][2])}  {term_name(tid)} : {term_desc(tid)}"
        other = C.blocked_by(tid, self._selected())
        if other and tid not in self._selected():
            text += "   " + tr("[与 {name} 冲突]", name=term_name(other))
        cfg(self.lbl_hover, text=text)

    def _leave(self, tid: str):
        if self._hover == tid:
            self._hover = ""
            cfg(self.lbl_hover, text="")

    # ---- 下半部分: 已选中的词条 | 危机等级 ----

    def _build_bottom(self, body):
        bg, card = COLORS["bg"], COLORS["bg_card"]
        row = tk.Frame(body, bg=bg)
        row.pack(fill="x")
        left = tk.Frame(row, bg=card, highlightbackground=COLORS["border"], highlightthickness=1)
        left.pack(side="left", fill="both", expand=True)
        tk.Label(left, text=tr("已选中词条"), bg=card, fg=COLORS["text"],
                 font=config.font("body", bold=True), anchor="w").pack(fill="x", padx=12, pady=(8, 4))
        self.list_box = tk.Frame(left, bg=card)
        self.list_box.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        right = tk.Frame(row, bg=card, highlightbackground=COLORS["border"], highlightthickness=1)
        right.pack(side="left", fill="y", padx=(10, 0))
        inner = tk.Frame(right, bg=card)
        inner.pack(padx=16, pady=10)
        tk.Label(inner, text=tr("危机等级"), bg=card, fg=COLORS["text_dim"],
                 font=config.font("body", bold=True)).pack(anchor="w")
        self.lbl_level = tk.Label(inner, text="0", bg=card, fg=COLORS["danger"],
                                  font=config.font("big", bold=True))
        self.lbl_level.pack(anchor="w")
        self.lbl_info = tk.Label(inner, text="", bg=card, fg=COLORS["text"], font=config.font("body"),
                                 anchor="w", justify="left")
        self.lbl_info.pack(anchor="w", pady=(2, 8))
        self.lbl_warn = tk.Label(inner, text=tr("⚠  当前合约难度极大 请谨慎行动"), bg=card, fg=COLORS["warn"],
                                 font=config.font("small", bold=True), anchor="w", justify="left",
                                 wraplength=config.scaled(220))
        btns = tk.Frame(inner, bg=card)
        btns.pack(anchor="w")
        self.btn_row = btns
        self.btn_clear = ttk.Button(btns, text=tr("清空"), command=self._clear)
        self.btn_start = ttk.Button(btns, text=tr("▶  开始行动"), style="Danger.TButton",
                                    command=self._start)
        self.btn_end = ttk.Button(btns, text=tr("■  提前结束"), style="Danger.TButton",
                                  command=lambda: self.on_abandon and self.on_abandon())

    def _clear(self):
        if not self.c.active:
            self.c.set_draft([])
            self.refresh()

    def _start(self):
        if not self.c.active and self.on_start:
            self.on_start(list(self.c.draft))

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
        rows = self._rows(sel)
        key = tuple(rows)
        if key == self._list_key:
            return
        self._list_key = key
        card = COLORS["bg_card"]
        for w in self.list_box.winfo_children():
            w.destroy()
        if not rows:
            tk.Label(self.list_box, text="—", bg=card, fg=COLORS["text_dim"],
                     font=config.font("body")).pack(anchor="w")
        for lv, text, void in rows:
            r = tk.Frame(self.list_box, bg=card)
            r.pack(fill="x", pady=1)
            tk.Label(r, text=marks(lv), bg=card, fg=COLORS["text_dim"] if void else COLORS["danger"], width=5,
                     anchor="w", font=config.font("small", bold=True)).pack(side="left")
            tk.Label(r, text=text, bg=card, fg=COLORS["text_dim"] if void else COLORS["text"],
                     font=config.font("small"),
                     anchor="w", justify="left", wraplength=config.scaled(520)).pack(side="left", fill="x")
        self.area.bind_children()

    # ---- 刷新 ----

    def refresh(self):
        c, now = self.c, datetime.now()
        active = c.active
        sel = self._selected()
        for tid, (fr, lbl) in self.tiles.items():
            if tid in sel:
                colors = (COLORS["danger"], COLORS["on_accent"], COLORS["danger"])
            elif active or C.blocked_by(tid, sel):
                colors = (COLORS["bg"], COLORS["border"], COLORS["border"])
            else:
                colors = (COLORS["bg_card"], COLORS["text"], COLORS["border"])
            cfg(fr, bg=colors[0], highlightbackground=colors[2], cursor="arrow" if active else "hand2")
            cfg(lbl, bg=colors[0], fg=colors[1], cursor="arrow" if active else "hand2")
        self._fill_list(sel)

        level = c.level if active else C.level_of(sel)
        orig = c.orig_level if active else level
        cfg(self.lbl_level, text=str(level))
        info = [tr("时长 {t}", t=fmt_hm(C.duration_of(c.orig if active else sel))),
                tr("休息倍率 ×{m:g}", m=C.rest_mult(level))]
        if orig != level:
            info.insert(0, tr("开始时 {n}", n=orig))
        cfg(self.lbl_info, text="\n".join(info))
        if level >= HARD_LEVEL:
            show_pack(self.lbl_warn, anchor="w", pady=(0, 8), before=self.btn_row)
        else:
            hide_pack(self.lbl_warn)

        if active:
            end = c.end_at
            cfg(self.lbl_state, text=tr("合约进行中"))
            cfg(self.lbl_left, text=fmt_hms((end - now).total_seconds()))
            cfg(self.lbl_viol, text=tr("违反 {n} / {m}", n=c.violations, m=c.violation_limit))
            live = f"{c.start_at:%H:%M} — {end:%m-%d %H:%M}"
            if c.reward >= 1:
                live += "   ·   " + tr("完成后休息 +{t}", t=fmt_ms(c.reward))
            left = c.net_left(self.net_used())
            if left is not None:
                live += "   ·   " + tr("合约的联网额度还剩 {t}", t=fmt_hm(left))
            cfg(self.lbl_live, text=live)
            idle = c.idle_left(now)
            cfg(self.lbl_idle, text="" if idle is None else tr("空闲中: {t} 后合约终止", t=fmt_ms(idle)))
            if self.live_card.winfo_manager() != "pack":
                self.live_card.pack(fill="x", pady=(0, 10), before=self.area.inner.pack_slaves()[0])
            hide_pack(self.btn_clear)
            hide_pack(self.btn_start)
            show_pack(self.btn_end, side="left")
        else:
            cfg(self.lbl_state, text="")
            hide_pack(self.live_card)
            hide_pack(self.btn_end)
            show_pack(self.btn_clear, side="left")
            show_pack(self.btn_start, side="left", padx=(8, 0))

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
                text += "   ·   " + tr("休息 +{t}", t=fmt_ms(last["reward"]))
            cfg(self.lbl_last, text=text)
        else:
            cfg(self.lbl_last, text="")

    def show(self):
        self.refresh()
        self.area.scroll_to_top()
