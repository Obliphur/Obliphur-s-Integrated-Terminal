import math
import tkinter as tk
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


def term_line(tid: str) -> str:
    """名字 : 描述. 描述本身分了几行时, 从下一行开始写, 每一句一行."""
    desc = term_desc(tid)
    return f"{term_name(tid)} :" + ("\n" if "\n" in desc else " ") + desc


def fmt_hms(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 3600}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def marks(level: int) -> str:
    return "▲" * int(level)


HARD_LEVEL = 20                  # 总等级到这里就提示"难度极大"

# 手动提前结束是"行动终止"; 被动结束 (违反 / 空闲 / 设备离线) 是"行动失败"
RESULTS = {"completed": "完成", "failed": "行动失败: 目标生命值归零", "idle": "行动失败: 空闲超过 15 分钟",
           "offline": "行动失败: 设备离线超过 15 分钟", "abandoned": "行动终止"}

# 合约页的主题 (设置 → 合约, CONTRACT_THEME). "ops" 起源: 深色页面, 浅灰的词条板, 六边形格子,
# 黑色的已选中词条, 红色的开始按钮; 侧边栏在合约页也换成这一套颜色.
OPS = {
    "bg": "#1f2023", "text": "#f2f2f2", "dim": "#9a9ca1", "line": "#c8202f",
    "board": "#d6d7da", "band": "#2c2d31", "band_text": "#ececec", "band_dim": "#9a9ca1",
    "tile": "#45474d", "tile_text": "#f4f4f4", "tile_rim": "#6b6d73", "tile_rim4": "#ff2b3a",
    "tile_on": "#1d6fc4", "tile_on_text": "#ffffff", "tile_on_rim": "#9cc8ff",
    "tile_off": "#bdbec1", "tile_off_text": "#8e9095", "tile_off_rim": "#a9aaad",
    "tile_rand": "#f2b33d", "tile_rand_text": "#141416", "tile_rand_rim": "#ffd27a",
    # 合约中禁止联网的词条 (乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒): 紫色, 点击解除网络禁令 (MECHANICS 18.3)
    "tile_ban": "#7c3aed", "tile_ban_text": "#ffffff", "tile_ban_rim": "#c4a1ff",
    "hint": "#2c2d31", "nav": "#ececec", "nav_off": "#55575c",
    "sel": "#141416", "sel_text": "#f2f2f2", "sel_dim": "#77797e",
    "badge_hi": "#c8202f", "badge_hi_text": "#ffffff", "badge_lo": "#f2f2f2", "badge_lo_text": "#141416",
    "bar": "#26272b", "level": "#ffffff", "warn": "#ffffff",
    "start": "#9e1b28", "start_hover": "#bf2433", "start_text": "#ffffff",
    "start_off": "#4a4b51", "start_off_text": "#8e9095",
    "rules": "#18181b", "rules_rim": "#3a3b40",
    "clear": "#3a3b40", "clear_hover": "#4a4b51", "clear_text": "#ececec",
    "tag": "#c8202f", "tag_text": "#ffffff",
    "live": "#18181b", "live_rim": "#c8202f", "danger": "#ff4d5a", "idle": "#f2b33d",
    "side": "#18191c", "side_text": "#cfd0d3", "side_on": "#c8202f", "side_on_text": "#ffffff",
    "side_hover": "#2c2d31", "side_rim": "#2c2d31",
    # 词条组合的颜色 (打年糕 白色 / 互联网娱乐的败退 紫色 / 无懈可击 灰色 / 信息隔绝 红色)
    "combo_white": C.COMBO_COLORS["white"], "combo_purple": C.COMBO_COLORS["purple"],
    "combo_gray": C.COMBO_COLORS["gray"], "combo_red": C.COMBO_COLORS["red"],
}


def palette() -> dict:
    """当前合约页主题的颜色. "ui" = 跟随界面主题."""
    if getattr(config, "CONTRACT_THEME", "ops") != "ui":
        return OPS
    c = COLORS
    return {
        "bg": c["bg"], "text": c["text"], "dim": c["text_dim"], "line": c["border"],
        "board": c["bg"], "band": c["bg_card"], "band_text": c["text"], "band_dim": c["text_dim"],
        "tile": c["bg_card"], "tile_text": c["text"], "tile_rim": c["border"], "tile_rim4": c["danger"],
        "tile_on": c["danger"], "tile_on_text": c["on_accent"], "tile_on_rim": c["danger"],
        "tile_off": c["bg"], "tile_off_text": c["border"], "tile_off_rim": c["border"],
        "tile_rand": c["warn"], "tile_rand_text": "#141416", "tile_rand_rim": c["warn"],
        "tile_ban": "#8b5cf6", "tile_ban_text": "#ffffff", "tile_ban_rim": "#8b5cf6",
        "hint": c["text_dim"], "nav": c["text"], "nav_off": c["border"],
        "sel": c["bg_card"], "sel_text": c["text"], "sel_dim": c["text_dim"],
        "badge_hi": c["danger"], "badge_hi_text": c["on_accent"], "badge_lo": c["border"], "badge_lo_text": c["text"],
        "bar": c["bg_card"], "level": c["danger"], "warn": c["warn"],
        "start": c["danger"], "start_hover": c["danger_hover"], "start_text": c["on_accent"],
        "start_off": c["border"], "start_off_text": c["text_dim"],
        "rules": c["bg_card"], "rules_rim": c["border"],
        "clear": c["border"], "clear_hover": c["bg"], "clear_text": c["text"],
        "tag": c["danger"], "tag_text": c["on_accent"],
        "live": c["bg_card"], "live_rim": c["danger"], "danger": c["danger"], "idle": c["warn"],
        "side": None,                      # 侧边栏不变
        # 组合的颜色: 白 / 紫 / 灰 / 红; 白和灰在浅色主题里用文字色, 免得看不见
        "combo_white": c["text_strong"], "combo_purple": "#8b5cf6",
        "combo_gray": c["text_dim"], "combo_red": c["danger"],
    }


class ContractPage(tk.Frame):
    """合约页 (MECHANICS 18): 词条板 (行 = 等级, 列 = 类型, 同一类型在同一列; 翻页), 下面是已选中的词条 (两栏),
    最下面一条: 清空 | 危机等级 | 时长 / 完成后的休息时间 | 开始行动. 合约进行中时只能看, 不能改.
    颜色按 设置 → 合约 的"合约页主题"."""

    TILE_W, GAP, BAND_W = 68, 7, 148              # 格子是正六边形: 高 = 宽 × √3 / 2

    def __init__(self, master, contract, on_start=None, on_abandon=None, net_used=None, on_change=None,
                 blocked=None, rec_text=None, on_stop_screen=None, on_close_rand=None, on_lift_ban=None):
        self.P = P = palette()
        super().__init__(master, bg=P["bg"])
        self.c = contract
        self.net_used = net_used or (lambda: 0.0)    # 今天所有设备已用的联网额度 (秒)
        self.on_start = on_start
        self.on_abandon = on_abandon
        self.on_change = on_change or (lambda: None)  # 选的词条变了 (侧边栏上的危机等级跟着变)
        self.blocked = blocked or (lambda: "")        # 休息 / 联网中不能开始: 按钮上方的提示
        self.rec_text = rec_text or (lambda: "")      # 目光 / 刻痕: 录像的状态 ("" = 没有在录)
        self.on_stop_screen = on_stop_screen          # 刻痕: 终止录屏 (算一次违反)
        self.on_close_rand = on_close_rand            # 未知变量选中的词条: 点击关闭 (算一次违反)
        self.on_lift_ban = on_lift_ban                # 禁止联网的词条 (紫色): 点击解除网络禁令, 扣目标生命值
        self._hover = ""
        self._list_key = None
        self._tile_state = {}

        hdr = tk.Frame(self, bg=P["bg"])
        hdr.pack(fill="x", padx=24, pady=(18, 6))
        tk.Label(hdr, text=tr("合约详情"), bg=P["tag"], fg=P["tag_text"], font=config.font("small", bold=True),
                 padx=8, pady=2).pack(side="left", anchor="s", pady=(0, 6))
        tk.Label(hdr, text=tr("⚔  合约"), bg=P["bg"], fg=P["text"],
                 font=config.font("h1", bold=True)).pack(side="left", padx=(12, 0))
        # 右上角: 合约期间的特别规则 (两栏)
        rules = tk.Frame(hdr, bg=P["rules"], highlightbackground=P["rules_rim"], highlightthickness=1)
        rules.pack(side="right", anchor="se")
        tk.Label(rules, text=tr("合约期间"), bg=P["rules"], fg=P["danger"], font=config.font("tiny", bold=True),
                 anchor="nw").pack(side="left", fill="y", padx=(8, 4), pady=4)
        self.lbl_rules = [tk.Label(rules, text="", bg=P["rules"], fg=P["dim"], font=config.font("tiny"),
                                   anchor="nw", justify="left") for _ in range(2)]
        for lbl in self.lbl_rules:
            lbl.pack(side="left", fill="y", padx=(4, 8), pady=4)
        self.lbl_state = tk.Label(hdr, text="", bg=P["bg"], fg=P["danger"], font=config.font("body", bold=True))
        self.lbl_state.pack(side="right", padx=(0, 14))
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
        # 目光 / 刻痕: 录像的状态; 刻痕可以终止录屏 (立即算一次违反)
        self.rec_row = tk.Frame(fr, bg=P["live"])
        self.lbl_rec = tk.Label(self.rec_row, text="", bg=P["live"], fg=P["danger"], font=config.font("small", bold=True),
                                anchor="w", justify="left")
        self.lbl_rec.pack(side="left")
        self.btn_stop_rec = self._button(self.rec_row, tr("■ 终止录屏"), P["clear"], P["clear_hover"], P["clear_text"],
                                         lambda: self.on_stop_screen and self.on_stop_screen())

    # ---- 词条板: 左边一条竖写的 "CONTRACT LIST" 和各行的等级, 右边六边形的格子; 翻页 (成系列的一页, 零散的一页,
    #      第 3 页是词条组合的效果) ----

    def _build_board(self, body):
        P, s = self.P, config.scaled
        self.tw, self.gap, self.band = s(self.TILE_W), s(self.GAP), s(self.BAND_W)
        self.th = round(self.tw * math.sqrt(3) / 2)
        self.hover_h = s(66)                     # 最下面写词条内容的地方: 最多三行 (长的描述自己换行)
        self.board_h = self.gap + C.MAX_LEVEL * (self.th + self.gap) + self.hover_h
        self.page = 0
        self.pages = list(C.PAGES)               # 前几页是词条的列; 最后一页是组合 (界面直接画)
        self.canvas = tk.Canvas(body, bg=P["board"], highlightthickness=0, height=self.board_h)
        self.canvas.pack(fill="x", pady=(0, 6))
        self.canvas.bind("<Configure>", self._board_resized)
        self._draw_board()
        self._draw_tiles()

    def _split_pages(self, width: int) -> list:
        """C.PAGES 里放不下的一页 (窗口太窄) 再分成几页."""
        per = max(1, (width - self.band - self.gap) // (self.tw + self.gap))
        out = []
        for a, b in C.PAGES:
            size = -(-(b - a) // -(-(b - a) // per))        # 分成几页时每页一样多
            out += [(i, min(b, i + size)) for i in range(a, b, size)]
        return out

    def _page_count(self) -> int:
        """总页数: 词条的那几页 + 组合效果的一页."""
        return len(self.pages) + 1

    def _on_combo_page(self) -> bool:
        return self.page >= len(self.pages)

    def _board_resized(self, _event):
        # 布局过程中宽度会来回变几次: 等它停下来再按最后的宽度分页 (只重画格子, 不动别的控件的大小)
        if getattr(self, "_resize_job", None):
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(80, self._apply_width)

    def _apply_width(self):
        self._resize_job = None
        pages = self._split_pages(self.canvas.winfo_width())
        if pages != self.pages:
            combo = self._on_combo_page()
            first = None if combo else self.pages[self.page][0]
            self.pages = pages
            self.page = len(pages) if combo else \
                next((i for i, (a, b) in enumerate(pages) if a <= first < b), 0)
            self._draw_tiles()
            if not self._on_combo_page():
                self._paint_tiles()

    def _flip(self, step: int):
        page = min(self._page_count() - 1, max(0, self.page + step))
        if page != self.page:
            self.page = page
            self._draw_tiles()
            if not self._on_combo_page():
                self._paint_tiles()

    @staticmethod
    def _hexagon(x, y, w, h):
        """放进 w × h 的格子里, 居中的正六边形 (平顶)."""
        r = min(w / 2, h / math.sqrt(3))
        cx, cy, hh = x + w / 2, y + h / 2, r * math.sqrt(3) / 2
        return (cx - r / 2, cy - hh, cx + r / 2, cy - hh, cx + r, cy, cx + r / 2, cy + hh, cx - r / 2, cy + hh, cx - r, cy)

    def _draw_board(self):
        P, cv, s = self.P, self.canvas, config.scaled
        cv.create_rectangle(0, 0, self.band, self.board_h, fill=P["band"], outline="")
        # 竖写的标题 (同参考图的 "CONTRACT LIST: 可选合约"); 组合效果那一页换成 "CONTRACT EFFECT"
        mid = (self.board_h - self.hover_h) / 2
        self.band_title = cv.create_text(s(22), mid, text="CONTRACT LIST", angle=90, fill=P["band_dim"],
                                         font=config.font("h2", bold=True))
        self.band_sub = cv.create_text(s(44), mid, text=tr("可选词条"), angle=90, fill=P["band_text"],
                                       font=config.font("body", bold=True))
        for lv in range(1, C.MAX_LEVEL + 1):
            # 每一行的等级: 小字 "Contingency Level" 和一个 ▲ 标记在左, 大数字在右 (同参考图)
            y = self.gap + (lv - 1) * (self.th + self.gap)
            x0 = s(62)
            cv.create_text(x0, y + s(6), text="Contingency\nLevel", anchor="nw", fill=P["band_dim"],
                           font=(config.FONT_FAMILY, max(6, s(6))), tags=("band_lv",))
            by = y + self.th - s(22)
            cv.create_rectangle(x0, by, x0 + s(24), by + s(14), fill=P["badge_lo"], outline="", tags=("band_lv",))
            cv.create_text(x0 + s(12), by + s(7), text="▲", fill=P["badge_hi"],
                           font=(config.FONT_FAMILY, max(6, s(7)), "bold"), tags=("band_lv",))
            cv.create_text(self.band - s(10), y + self.th / 2, text=str(lv), anchor="e", fill=P["band_text"],
                           font=config.font("h1", bold=True), tags=("band_lv",))
        # 翻页: 左边一条最下面 "◀  1 / 2  ▶"
        ny = self.board_h - self.hover_h / 2
        self.nav_prev = cv.create_text(s(26), ny, text="◀", font=config.font("h2", bold=True))
        self.nav_text = cv.create_text(self.band / 2, ny, text="", fill=P["band_text"], font=config.font("body", bold=True))
        self.nav_next = cv.create_text(self.band - s(26), ny, text="▶", font=config.font("h2", bold=True))
        for item, step in ((self.nav_prev, -1), (self.nav_next, 1)):
            cv.tag_bind(item, "<Button-1>", lambda e, d=step: self._flip(d))
            cv.tag_bind(item, "<Enter>", lambda e: self.canvas.configure(cursor="hand2"))
            cv.tag_bind(item, "<Leave>", lambda e: self.canvas.configure(cursor=""))
        # 鼠标停在格子上: 最下面一行写出这个词条的内容
        self.hover_id = cv.create_text(self.band + self.gap, self.board_h - self.hover_h + s(4), text="", anchor="nw",
                                       fill=P["hint"], font=config.font("small", bold=True), tags=("hover",))

    def _nav(self):
        """翻页的 "◀ 1 / 3 ▶" 和左右两条的颜色."""
        P, cv = self.P, self.canvas
        n = self._page_count()
        cv.itemconfigure(self.nav_text, text=f"{self.page + 1} / {n}")
        cv.itemconfigure(self.nav_prev, fill=P["nav"] if self.page > 0 else P["nav_off"])
        cv.itemconfigure(self.nav_next, fill=P["nav"] if self.page < n - 1 else P["nav_off"])
        combo = self._on_combo_page()
        cv.itemconfigure(self.band_title, text="CONTRACT EFFECT" if combo else "CONTRACT LIST")
        cv.itemconfigure(self.band_sub, text=tr("词条组合") if combo else tr("可选词条"))
        cv.itemconfigure("band_lv", state="hidden" if combo else "normal")

    def _draw_tiles(self):
        """这一页的格子 (翻页 / 窗口宽度变了时重画)."""
        P, cv, s = self.P, self.canvas, config.scaled
        cv.delete("tile")
        cv.delete("combo")
        self._tile_state = {}
        self._leave_all()
        if self._on_combo_page():
            self._combo_key = tuple((x["id"], st) for x, st in self._combos())
            self._draw_combos()
            self.canvas.itemconfigure(self.hover_id, text=tr("凑齐列出的词条 / 词条系列时生效"))
            self._nav()
            return
        a, b = self.pages[self.page]
        for tid, g, lv, _n, _d, _x in C.TERMS:
            col = C.LAYOUT[tid][0]                 # 成系列的每组一列, 零散的在右边挨着排
            if not a <= col < b:
                continue
            x = self.band + self.gap + (col - a) * (self.tw + self.gap)
            y = self.gap + (lv - 1) * (self.th + self.gap)
            tag = f"t_{tid}"
            cv.create_polygon(self._hexagon(x, y, self.tw, self.th), fill=P["tile"],
                              outline=P["tile_rim"], width=2, tags=("tile", tag, tag + "_shape"))
            cv.create_text(x + self.tw / 2, y + self.th / 2, text=marks(lv) + "\n" + term_short(tid),
                           fill=P["tile_text"], justify="center", width=self.tw * 0.66,
                           font=(config.FONT_FAMILY, max(6, s(8)), "bold"), tags=("tile", tag, tag + "_text"))
            cv.tag_bind(tag, "<Button-1>", lambda e, t=tid: self._click(t))
            cv.tag_bind(tag, "<Enter>", lambda e, t=tid: self._enter(t))
            cv.tag_bind(tag, "<Leave>", lambda e, t=tid: self._leave(t))
        self._nav()

    # ---- 第 3 页: 词条组合的效果 ----

    def _combos(self) -> list:
        """(组合, 状态): 合约中按现在的看, 没开始时按草稿看."""
        c = self.c
        if c.active:
            return c.combos()
        return [(x, C.combo_state(x, c.draft)) for x in C.COMBOS]

    def _combo_color(self, combo, key="color") -> str:
        return self.P.get(f"combo_{combo[key]}", self.P["text"])

    def _draw_combos(self):
        """4 个组合: 颜色条 + 名字 + 凑齐要哪些词条 + 效果 + 现在凑齐了没有 (2 × 2)."""
        P, cv, s = self.P, self.canvas, config.scaled
        cv.delete("combo")
        x0, y0 = self.band + self.gap, self.gap
        w = self.canvas.winfo_width() - x0 - self.gap
        w = max(s(360), w)
        h = self.board_h - self.hover_h - self.gap
        cw, ch = (w - self.gap) / 2, (h - self.gap) / 2
        status = {"done": tr("已凑齐"), "void": tr("已失效"), "open": tr("未凑齐")}
        for i, (combo, state) in enumerate(self._combos()):
            cx = x0 + (i % 2) * (cw + self.gap)
            cy = y0 + (i // 2) * (ch + self.gap)
            color = self._combo_color(combo)
            on = state == "done"
            rim = color if on else (P["tile_off_rim"] if state == "open" else P["tile_rim"])
            tag = f"c_{combo['id']}"
            cv.create_rectangle(cx, cy, cx + cw, cy + ch, fill=P["sel"], outline=rim,
                                width=2 if on else 1, tags=("combo", tag))
            cv.create_rectangle(cx, cy, cx + s(5), cy + ch, fill=color if on else P["clear"], outline="",
                                tags=("combo", tag))
            # 名字 (组合的颜色) 和状态
            cv.create_text(cx + s(12), cy + s(8), text=tr(combo["name"]), anchor="nw",
                           fill=color, font=config.font("body", bold=True), tags=("combo", tag))
            cv.create_text(cx + cw - s(10), cy + s(9), text=status.get(state, ""), anchor="ne",
                           fill=color if on else (P["danger"] if state == "void" else P["sel_dim"]),
                           font=config.font("tiny", bold=True), tags=("combo", tag))
            # 凑齐要哪些词条 (成系列的一组只写一个名字)
            need = " · ".join(tr(C.req_name(r)) for r in combo["reqs"])
            cv.create_text(cx + s(12), cy + s(30), text=tr("需要") + "  " + need, anchor="nw",
                           fill=P["sel_text"] if on else P["sel_dim"], width=cw - s(20),
                           font=config.font("tiny"), tags=("combo", tag))
            # 效果: 写成一行 (太窄时自己换行), 贴在卡片最下面, 免得和上面的"需要"叠在一起
            desc = tr(combo["desc"]).replace("\n", "  ·  ")
            cv.create_text(cx + s(12), cy + ch - s(6), text=desc, anchor="sw",
                           fill=color if on else P["sel_dim"], width=cw - s(20),
                           font=config.font("tiny"), tags=("combo", tag))
            cv.tag_bind(tag, "<Enter>", lambda e, x=combo: self._combo_hover(x))
            cv.tag_bind(tag, "<Leave>",
                        lambda e: self.canvas.itemconfigure(self.hover_id, text=tr("凑齐列出的词条 / 词条系列时生效")))
        if self._hover:
            self._leave(self._hover)

    def _combo_hover(self, combo):
        name = tr(combo["name"])
        need = " · ".join(tr(C.req_name(r)) for r in combo["reqs"])
        self.canvas.itemconfigure(self.hover_id, text=f"{name}   {tr('需要')}: {need}   ·   {tr(combo['desc'])}")
        self.canvas.configure(cursor="")

    def _leave_all(self):
        if self._hover:
            self._hover = ""
        self.canvas.itemconfigure(self.hover_id, text="")
        self.canvas.configure(cursor="")


    def _selected(self):
        return self.c.terms if self.c.active else self.c.draft

    def _click(self, tid: str):
        if self.c.active:
            if tid in self.c.rand and self.on_close_rand:
                self.on_close_rand(tid)
            elif self.c.can_lift(tid) and self.on_lift_ban:
                self.on_lift_ban(tid)
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
        rand = self.c.active and tid in self.c.rand
        ban = self.c.can_lift(tid)
        self.canvas.configure(cursor="hand2" if rand or ban or not self.c.active else "arrow")
        text = f"{marks(C.BY_ID[tid][2])}  {term_name(tid)} : {term_desc(tid)}"
        if rand:
            text += "   " + tr("[未知变量选中 · 点击关闭, 算一次违反]")
        elif ban:
            text += "   " + tr("[禁止联网中 · 点击解除, 目标生命值 -{n}]", n=self.c.lift_cost(tid))
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

    def _combo_marks(self) -> dict:
        """凑齐了的组合的成员词条 → 颜色 (界面上框住它们), 颜色按当前主题的."""
        c = self.c
        raw = c.combo_marks() if c.active else C.combo_marks(c.draft)
        if not raw:
            return {}
        by_hex = {C.COMBO_COLORS[x["color"]]: x["color"] for x in C.COMBOS}
        return {t: self.P.get(f"combo_{by_hex.get(v, 'gray')}", v) for t, v in raw.items()}

    def _rows(self, sel):
        """已选中的词条: (等级, 文字, 是否失效, 未知变量选中的词条 / "", 现在的词条). 合约中按开始时选的列出,
        降过级的写出降成了什么; 最后是未知变量选中的."""
        c = self.c
        if not c.active:
            return [(C.BY_ID[t][2], term_line(t), False, "", t) for t in sel]
        out = []
        rand = c.rand
        mine = [x for x in c.terms if x not in rand]
        for t in c.orig:
            now = next((x for x in mine if x == t or C.BY_ID[x][1] == C.BY_ID[t][1]), "")
            if not now:
                out.append((C.BY_ID[t][2], f"{term_name(t)}   " + tr("[已失效]"), True, "", ""))
            elif now != t:
                out.append((c.term_level(now), f"{term_line(now)}   "
                            + tr("[原 {name}]", name=term_short(t)), False, "", now))
            else:
                text = term_line(t)
                if c.term_level(t) < C.BY_ID[t][2]:
                    text += "   " + tr("[降了 {n} 级]", n=C.BY_ID[t][2] - c.term_level(t))
                out.append((c.term_level(t), text, False, "", t))
        for t in rand:
            text = f"{term_line(t)}   " + tr("[未知变量选中 · 点击关闭]")
            until = c.rand_until(t)
            if until:
                text += "   " + tr("[{t} 移除]", t=f"{until:%H:%M}")
            out.append((c.term_level(t), text, False, t, t))
        for t, (at, _b) in c.waiting.items():          # 抽中了还没生效的
            out.append((C.BY_ID[t][2], f"{term_line(t)}   " + tr("[未知变量抽中 · {t} 生效]", t=f"{at:%H:%M}"),
                        True, "", ""))
        return out

    def _fill_list(self, sel):
        P = self.P
        rows = self._rows(sel)
        colors = self._combo_marks()
        key = (tuple(rows), tuple(sorted(colors.items())))
        if key == self._list_key:
            return
        self._list_key = key
        for w in self.list_box.winfo_children():
            w.destroy()
        if not rows:
            tk.Label(self.list_box, text="—", bg=P["sel"], fg=P["sel_dim"],
                     font=config.font("body")).grid(row=0, column=0, sticky="w")
        half = (len(rows) + 1) // 2           # 先排满左栏, 再排右栏
        for i, (lv, text, void, rand, tid) in enumerate(rows):
            color = colors.get(tid, "") if tid else ""
            ban = bool(tid) and self.c.can_lift(tid)       # 禁止联网中 (紫色): 点击解除网络禁令
            r = tk.Frame(self.list_box, bg=P["sel"],
                         highlightbackground=color or P["sel"], highlightthickness=2 if color else 0)
            r.grid(row=i % half, column=i // half, sticky="we", pady=2, padx=(0, 10))
            hi = lv >= 2 and not void
            if rand:
                badge = (P["tile_rand"], P["tile_rand_text"])
            elif ban:
                badge = (P["tile_ban"], P["tile_ban_text"])
            else:
                badge = (P["badge_hi"], P["badge_hi_text"]) if hi else (P["badge_lo"], P["badge_lo_text"])
            tk.Label(r, text=marks(lv) or "—", bg=badge[0], fg=badge[1], width=4,
                     font=config.font("small", bold=True)).pack(side="left", anchor="n", padx=(0, 8))
            fg = P["tile_rand"] if rand else P["tile_ban_rim"] if ban else P["sel_dim"] if void else P["sel_text"]
            lbl = tk.Label(r, text=text + ("   " + tr("[禁止联网 · 点击解除]") if ban else ""), bg=P["sel"], fg=fg,
                           font=config.font("small"), anchor="w", justify="left",
                           cursor="hand2" if rand or ban else "")
            lbl.pack(side="left", fill="x", expand=True)
            lbl.bind("<Configure>", lambda e, l=lbl: l.configure(wraplength=max(80, e.width - 4)))
            if rand:
                lbl.bind("<Button-1>", lambda e, t=rand: self.on_close_rand and self.on_close_rand(t))
            elif ban:
                lbl.bind("<Button-1>", lambda e, t=tid: self.on_lift_ban and self.on_lift_ban(t))
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
        self.start_box = tk.Frame(inner, bg=P["bar"])
        self.lbl_block = tk.Label(self.start_box, text="", bg=P["bar"], fg=P["dim"], font=config.font("tiny"))
        self.btn_start = self._button(self.start_box, tr("开始行动"), P["start"], P["start_hover"], P["start_text"],
                                      self._start, big=True)
        self.btn_end = self._button(self.start_box, tr("■  提前结束"), P["start"], P["start_hover"],
                                    P["start_text"], lambda: self.on_abandon and self.on_abandon(), big=True)
        self.start_box.pack(side="right")
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
        if not self.c.active and self.on_start and not self.blocked():
            self.on_start(list(self.c.draft))

    def _rules_text(self):
        """右上角: 合约期间的特别规则 (左栏, 右栏)."""
        n = self.c.violation_limit if self.c.active else C.life_of(self.c.draft)
        return ("\n".join([tr("· 目标生命值 {n}: 违反一次 -1, 归零即失败", n=n),
                           tr("· 空闲 / 设备离线共 15 分钟: 失败"),
                           tr("· 整点记录超时未填写: 违反 1 次")]),
                "\n".join([tr("· \"我累了\"不可用"),
                           tr("· 暂停休息: 合约前积攒的时间不打折"),
                           tr("· 合约中得到的休息时间结束后作废 (奖励照常)")]))

    # ---- 刷新 ----

    def level(self) -> int:
        return self.c.level if self.c.active else C.level_of(self.c.draft)

    def _paint_tiles(self):
        """这一页格子的颜色: 选中 / 不能选 / 未知变量选中的 (黄色); 合约中违反到失效的词条是"未选择"的样子.
        凑齐了词条组合的词条用组合的颜色框住."""
        P, c = self.P, self.c
        if self._on_combo_page():
            return
        active = c.active
        sel = self._selected()
        rand = c.rand
        lost = c.lost
        marks = self._combo_marks()
        a, b = self.pages[self.page]
        for tid in C.BY_ID:
            if not a <= C.LAYOUT[tid][0] < b:
                continue
            if tid in rand:
                st = ("tile_rand", "tile_rand_text", "tile_rand_rim")
            elif active and c.can_lift(tid):
                st = ("tile_ban", "tile_ban_text", "tile_ban_rim")      # 禁止联网中: 点击解除
            elif tid in sel:
                st = ("tile_on", "tile_on_text", "tile_on_rim")
            elif active and tid in lost:
                st = ("tile", "tile_text", "tile_rim")
            elif active or C.blocked_by(tid, sel):
                st = ("tile_off", "tile_off_text", "tile_off_rim")
            else:
                st = ("tile", "tile_text", "tile_rim")
            if self._tile_state.get(tid) != (st, marks.get(tid, "")):
                self._tile_state[tid] = (st, marks.get(tid, ""))
                rim = marks.get(tid) or P[st[2]]        # 凑齐了的组合: 用组合的颜色框住
                self.canvas.itemconfigure(f"t_{tid}_shape", fill=P[st[0]], outline=rim,
                                          width=3 if tid in marks else 2)
                self.canvas.itemconfigure(f"t_{tid}_text", fill=P[st[1]])

    def refresh(self):
        P, c, now = self.P, self.c, datetime.now()
        active = c.active
        sel = self._selected()
        self._paint_tiles()
        self._fill_list(sel)

        level = self.level()
        orig = c.orig_level if active else level
        terms = c.mine if active else sel
        cfg(self.lbl_level, text=C.level_label(level, orig))       # 扣过等级: 最终等级[最初等级-扣的等级]
        self._draw_strip(level)
        info = [tr("时长 {t}", t=fmt_hm(C.duration_of(c.orig if active else sel))),
                tr("完成后休息 +{n} 分钟", n=C.rest_minutes_total(level, terms))]
        # 合约中: 开始时已经给出的 (按开始时选的词条算, 之后成员失效 / 未知变量选中都不变, MECHANICS 18.3.1)
        gift = C.rest_gift(c.orig if active else sel)
        if gift:
            info.append(tr("开始时休息 +{n} 分钟", n=gift // 60))
        cfg(self.lbl_info, text="\n".join(info))
        if self._on_combo_page():
            key = tuple((x["id"], st) for x, st in self._combos())
            if key != getattr(self, "_combo_key", None):
                self._combo_key = key
                self._draw_combos()
        for lbl, text in zip(self.lbl_rules, self._rules_text()):
            cfg(lbl, text=text)
        btn = self.btn_end if active else self.btn_start
        other = self.btn_start if active else self.btn_end
        hide_pack(other)
        show_pack(btn, side="top", anchor="e")
        why = "" if active else self.blocked()
        if why:
            cfg(self.lbl_block, text=why)
            show_pack(self.lbl_block, side="top", anchor="e", pady=(0, 2), before=btn)
        else:
            hide_pack(self.lbl_block)
        off = bool(why)
        if getattr(self, "_start_off", None) != off:
            self._start_off = off
            bg, fg = (P["start_off"], P["start_off_text"]) if off else (P["start"], P["start_text"])
            hover = P["start_off"] if off else P["start_hover"]
            self.btn_start.configure(bg=bg, fg=fg, cursor="arrow" if off else "hand2")
            self.btn_start.bind("<Enter>", lambda e: self.btn_start.configure(bg=hover))
            self.btn_start.bind("<Leave>", lambda e: self.btn_start.configure(bg=bg))
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
            # 黑雾: 看不到合约的剩余时间 (结束的时刻也一起藏起来)
            fog = c.fog()
            cfg(self.lbl_left, text="???" if fog else fmt_hms((end - now).total_seconds()))
            # 目标生命值: 每违反一次减一, 减到 0 合约失败
            cfg(self.lbl_viol, text=tr("目标生命值") + "  " + "♥" * c.life + "♡" * (c.violation_limit - c.life)
                + f"  {c.life} / {c.violation_limit}")
            # 黑雾: 开始和结束的时刻都不显示
            live = ("??:??" if fog else f"{c.start_at:%H:%M}") + " — " + ("??:??" if fog else f"{end:%m-%d %H:%M}")
            left = c.net_left(self.net_used())
            if left is not None:
                live += "   ·   " + tr("合约的联网额度还剩 {t}", t=fmt_hm(left))
            cfg(self.lbl_live, text=live)
            idle = c.idle_left(now)
            cfg(self.lbl_idle, text="" if idle is None else tr("空闲中: {t} 后合约终止", t=fmt_ms(idle)))
            rec = self.rec_text()
            if rec:
                cfg(self.lbl_rec, text=rec)
                show_pack(self.rec_row, fill="x", padx=16, pady=(0, 10))
                if c.has("scar") and self.on_stop_screen:
                    show_pack(self.btn_stop_rec, side="right")
                else:
                    hide_pack(self.btn_stop_rec)
            else:
                hide_pack(self.rec_row)
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
