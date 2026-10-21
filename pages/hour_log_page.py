

import random
import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta

import config
import i18n
from config import COLORS
from i18n import tr
from .widgets import cfg


# 黑雾 (MECHANICS 18.3) 时盖住的格子里的假内容: 动词 + 量词 + 名词 随机拼起来 (每个词池 60 个, 中文每个词两个字),
# 词都取自 Minecraft Wiki 的"村民"条目 (中文 / 英文). 界面是英文时用英文的.
_ZH_VERBS = (
    "生成", "治愈", "掉落", "繁殖", "交易", "工作", "游荡", "聚集", "玩耍", "回家", "睡觉", "认领", "拾取", "分享", "耕作", "逃跑", "注视", "交谈",
    "收割", "躲避", "寻找", "起床", "攀爬", "打开", "放弃", "绑定", "破坏", "传送", "检测", "传播", "合并", "攻击", "装备", "催熟", "播种", "收获",
    "制作", "检查", "购买", "出售", "补货", "庆祝", "发射", "敲响", "观察", "追逐", "跳跃", "奔跑", "成长", "感染", "转换", "命名", "叫醒", "种植",
    "投掷", "摇头", "熬夜", "消耗", "储存", "升级",
)
_ZH_QUANTS = (
    "一个", "两个", "三个", "五个", "八个", "十个", "几个", "每个", "一块", "三块", "六块", "半块", "一组", "两组", "半组", "每组", "一格", "八格",
    "十格", "一张", "两张", "几张", "每张", "一次", "两次", "几次", "一枚", "十枚", "几枚", "一种", "五种", "七种", "几种", "一条", "两条", "几条",
    "一名", "三名", "五名", "一颗", "九颗", "几颗", "一件", "两件", "几件", "一片", "几片", "一层", "两层", "一级", "五级", "一只", "两只", "几只",
    "一些", "许多", "少量", "多个", "四块", "四组",
)
_ZH_NOUNS = (
    "村民", "村庄", "职业", "面包", "小麦", "骨粉", "农民", "渔夫", "牧师", "屠夫", "石匠", "皮匠", "傻子", "盔甲", "物品", "方块", "僵尸", "女巫",
    "粒子", "言论", "声望", "折扣", "礼物", "火箭", "房屋", "梯子", "屋顶", "教堂", "码头", "书架", "讲台", "木桶", "高炉", "砂轮", "作物", "耕地",
    "雪屋", "苹果", "南瓜", "徽章", "服装", "袍子", "帽子", "围裙", "眼镜", "手套", "头巾", "羽毛", "箭袋", "矿车", "蝙蝠", "骆驼", "熊猫", "狐狸",
    "兔子", "绵羊", "海龟", "山羊", "青蛙", "羊驼",
)
_EN_VERBS = (
    "trade", "breed", "spawn", "cure", "drop", "wander", "gather", "play", "sleep", "claim", "share", "farm",
    "flee", "stare", "harvest", "plant", "throw", "pick", "equip", "climb", "open", "close", "jump", "sprint",
    "craft", "heal", "gossip", "work", "explore", "buy", "sell", "restock", "celebrate", "launch", "ring",
    "forget", "migrate", "summon", "carry", "chase", "shear", "wear", "consume", "collect", "deposit",
    "attack", "avoid", "push", "grow", "rest", "unlock", "tend", "scan", "reach", "emit", "count", "store",
    "refresh", "despawn", "inhabit",
)
_EN_QUANTS = (
    "a stack of", "a block of", "a slot of", "a piece of", "a batch of", "a handful of", "two stacks of",
    "two blocks of", "two slots of", "two pieces of", "two batches of", "two handfuls of", "three stacks of",
    "three blocks of", "three slots of", "three pieces of", "three batches of", "three handfuls of",
    "four stacks of", "four blocks of", "four slots of", "four pieces of", "four batches of",
    "four handfuls of", "five stacks of", "five blocks of", "five slots of", "five pieces of",
    "five batches of", "five handfuls of", "six stacks of", "six blocks of", "six slots of", "six pieces of",
    "six batches of", "six handfuls of", "eight stacks of", "eight blocks of", "eight slots of",
    "eight pieces of", "eight batches of", "eight handfuls of", "ten stacks of", "ten blocks of",
    "ten slots of", "ten pieces of", "ten batches of", "ten handfuls of", "twelve stacks of",
    "twelve blocks of", "twelve slots of", "twelve pieces of", "twelve batches of", "twelve handfuls of",
    "several stacks of", "several blocks of", "several slots of", "several pieces of", "several batches of",
    "several handfuls of",
)
_EN_NOUNS = (
    "villagers", "emeralds", "beds", "bells", "carrots", "potatoes", "beetroots", "seeds", "pumpkins",
    "golems", "zombies", "pillagers", "witches", "farmers", "clerics", "librarians", "masons", "shepherds",
    "fletchers", "butchers", "armorers", "toolsmiths", "cartographers", "leatherworkers", "weaponsmiths",
    "fishermen", "nitwits", "babies", "particles", "doors", "ladders", "trapdoors", "chickens", "cats",
    "wolves", "composters", "barrels", "lecterns", "looms", "grindstones", "smokers", "stonecutters",
    "cauldrons", "apples", "saddles", "carpets", "books", "maps", "gifts", "rockets", "badges", "outfits",
    "hats", "aprons", "gloves", "crops", "igloos", "ravagers", "dispensers", "pistons",
)


def fog_noise(seed, key: str) -> str:
    """黑雾时一个格子的假内容: 同一个合约里同一个格子不变 (不会每秒闪)."""
    rnd = random.Random(f"{seed}|{key}")
    if i18n.is_en():
        return f"{rnd.choice(_EN_VERBS).capitalize()} {rnd.choice(_EN_QUANTS)} {rnd.choice(_EN_NOUNS)}"
    return rnd.choice(_ZH_VERBS) + rnd.choice(_ZH_QUANTS) + rnd.choice(_ZH_NOUNS)


class HourLogPage(tk.Frame):

    def __init__(self, master, data_manager, on_text_changed=None, on_toggle=None,
                 on_open_settings=None, locked=None, split=None, fog=None, sealed=None):
        super().__init__(master, bg=COLORS["bg"])
        self.dm = data_manager
        # 合约 (MECHANICS 18.4): 记录封锁 —— 已经填写了的格子 (手动 / 自动) 不能再改 (开始, 结束, 内容, 现在)
        self.locked = locked or (lambda start, end, text, now: False)
        # 细化规范: 这一小时分成两个半小时, 各填一格
        self.split = split or (lambda start: False)
        # 黑雾: 返回这个合约的种子 (没有黑雾时 None). 除了"现在"的格子和待填写的格子, 其他格子的时间写 ???, 内容是随机字符
        self.fog = fog or (lambda: None)
        # 记录封锁: 这个格子填写后就不能再改 (开始, 结束). 这种格子只在按 Enter 并确认后才保存,
        # 离开输入框时不保存, 写了的内容留着 (草稿), 等按 Enter
        self.sealed = sealed or (lambda start, end: False)
        self._drafts = {}          # (行, 第几格) → 没保存的内容
        self.on_text_changed = on_text_changed
        self.on_toggle = on_toggle
        self.on_open_settings = on_open_settings
        self.view_date = data_manager.today_date
        self.rows = {}
        self.halves = {}           # 第 i 行的后半小时: (输入框, 内容)
        self._split = {}           # 第 i 行此刻是不是分成了两格
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
            ent.bind("<KP_Enter>", lambda e, h=h: self._on_return(h))
            ent.bind("<FocusOut>", lambda e, h=h: self._commit(h))
            # 细化规范时的后半小时 (平时不显示)
            var2 = tk.StringVar()
            ent2 = tk.Entry(
                row, textvariable=var2, font=config.font("body"), relief="flat",
                bg=card, fg=COLORS["text"], insertbackground=COLORS["text"],
                disabledbackground=bg, disabledforeground=COLORS["text_dim"],
                highlightthickness=1, highlightbackground=COLORS["border"],
                highlightcolor=COLORS["accent"]
            )
            ent2.bind("<Return>", lambda e, h=h: self._on_return(h, 1))
            ent2.bind("<KP_Enter>", lambda e, h=h: self._on_return(h, 1))
            ent2.bind("<FocusOut>", lambda e, h=h: self._commit(h, 1))
            self.rows[h] = (lbl, ent, var)
            self.halves[h] = (ent2, var2)

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

    def _get(self, row: int, half=None) -> str:
        s = self._slot_start(row)
        if half is not None:
            return self.dm.get_half_text(s.strftime('%Y-%m-%d'), s.hour, half)
        return self.dm.get_hour_text(s.strftime('%Y-%m-%d'), s.hour)

    def _scroll_to(self, row: int):
        self.update_idletasks()
        total = max(1, len(self.rows))
        row = min(max(row, 0), total - 1)
        self.canvas.yview_moveto(max(0, row - 2) / float(total))

    def _on_return(self, h: int, i: int = 0):
        if self._commit(h, i, enter=True) is False:
            return "break"                         # 没确认: 留在输入框里
        self.canvas.focus_set()
        return "break"

    def _cell(self, h: int, i: int):
        s = self._slot_start(h)
        if self._split.get(h):
            a = s + timedelta(minutes=30 * i)
            return a, a + timedelta(minutes=30)
        return s, s + timedelta(hours=1)

    def _commit(self, h: int, i: int = 0, enter: bool = False):
        ent, var = (self.rows[h][1], self.rows[h][2]) if i == 0 else self.halves[h]
        if str(ent.cget("state")) == "disabled":
            return None
        text = var.get().strip()
        split = self._split.get(h)
        if text != self._get(h, i if split else None):
            a, b = self._cell(h, i)
            if text and self.sealed(a, b):
                # 记录封锁 (MECHANICS 18.4): 填写后就不能再改 —— 只有按 Enter 并确认才保存
                if not enter:
                    self._drafts[(h, i)] = var.get()
                    return None
                import toast
                if not toast.ask(tr("确认修改这个格子吗?") + "\n" + tr("记录封锁: 填写后就不能再修改了.") + "\n\n" + text,
                                 title=tr("确认修改"), ok_text=tr("确认修改")):
                    self._drafts[(h, i)] = var.get()
                    return False
            self._drafts.pop((h, i), None)
            s = self._slot_start(h)
            if split:
                self.dm.set_half_text(s.strftime('%Y-%m-%d'), s.hour, i, text)
            else:
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
        try:
            seed = self.fog()
        except Exception:
            seed = None
        for h, (lbl, ent, var) in self.rows.items():
            start = self._slot_start(h)
            end = start + timedelta(hours=1)
            ent2, var2 = self.halves[h]
            split = self.split(start)
            if split != self._split.get(h):
                self._split[h] = split
                if split:
                    ent2.pack(side="left", fill="x", expand=True, ipady=4, padx=(6, 0))
                else:
                    ent2.pack_forget()
            if split:
                mid = start + timedelta(minutes=30)
                cells = [(ent, var, start, mid, self._get(h, 0)), (ent2, var2, mid, end, self._get(h, 1))]
            else:
                cells = [(ent, var, start, end, self._get(h))]
            locked = False
            shown = False                           # 黑雾时这一行有没有露出来的格子
            for i, (e, v, a, b, text) in enumerate(cells):
                wait = awaiting is not None and a <= awaiting < b
                hidden = seed is not None and not (a <= now < b or wait)     # 黑雾: 只露出"现在"和待填写的格子
                shown = shown or not hidden
                if hidden:
                    noise = fog_noise(seed, f"{a:%Y-%m-%d %H:%M}")
                    if v.get() != noise:
                        v.set(noise)
                    cfg(e, state="disabled", highlightbackground=COLORS["border"])
                    continue
                draft = self._drafts.get((h, i))
                if draft is not None and (text or not draft.strip()):
                    self._drafts.pop((h, i), None)            # 已经有内容了 (例如同步来的) / 草稿清空了: 不再留草稿
                    draft = None
                shown_text = draft if draft is not None else text
                if focused is not e and v.get() != shown_text:
                    v.set(shown_text)
                done = b <= now
                lock = done and self.locked(a, b, text, now)
                locked = locked or lock
                cfg(e, state="normal" if (enabled and done and not lock) else "disabled")
                cfg(e, highlightbackground=COLORS["accent"] if wait else COLORS["border"])
            ended = end <= now or (split and start + timedelta(minutes=30) <= now)

            is_wait = awaiting is not None and start <= awaiting < end
            is_now = enabled and start <= now < end
            label = f"{h:02d}:00" if seed is None or shown else "???"
            if seed is not None and not shown:
                pass
            elif is_now:
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

    def show(self):
        aw = self.dm.awaiting_slot()
        self.refresh()
        now = datetime.now().replace(minute=0, second=0, microsecond=0)
        row = self._row_of(aw if aw else now)
        self._scroll_to(row)
        if aw and row in self.rows:
            (self.halves[row][0] if self._split.get(row) and aw.minute >= 30 else self.rows[row][1]).focus_set()
