

import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta

import config
import i18n
from config import COLORS
from i18n import tr
from .widgets import cfg


# 黑雾 (MECHANICS 18.3) 时盖住的格子里的假内容: 动词 + 量词 + 名词 拼起来 (中文每个词两个字).
# 词取自 Minecraft Wiki 的"村民"条目 (每个词池 60 个), PRTS 的"水月"条目 (动词 40, 量词 20, 名词 40)
# 和脑叶公司 Wiki 的"T-01-31 沉默乐团"条目 (动词 50, 名词 50).
# 界面是英文时用英文的.
_ZH_VERBS = (
    "生成", "治愈", "掉落", "繁殖", "交易", "聚集", "认领", "拾取", "分享", "耕作", "注视", "收割", "躲避", "寻找", "攀爬", "打开", "放弃", "绑定",
    "破坏", "传送", "检测", "传播", "合并", "攻击", "装备", "催熟", "播种", "收获", "制作", "检查", "购买", "出售", "庆祝", "发射", "敲响", "观察",
    "追逐", "感染", "转换", "命名", "叫醒", "种植", "投掷", "消耗", "储存", "升级", "伏击", "隐匿", "部署", "阻挡", "回复", "触发", "充能", "束缚",
    "拖拽", "击杀", "歼灭", "进驻", "生产", "编入", "通关", "阻止", "劫掠", "坐视", "探索", "察觉", "索要", "审视", "见证", "怀疑", "延长", "割下",
    "询问", "摒弃", "预见", "滋润", "盘踞", "汲取", "改造", "孕育", "招募", "演奏", "指挥", "突破", "收容", "减少", "免疫", "镇压", "指派", "选定",
    "遮蔽", "覆盖", "遮挡", "锁定", "暂停", "拉开", "推进", "屏蔽", "警告", "清零", "登上", "杀死", "合上", "抵挡", "派遣", "调离", "施加", "呼叫",
    "丢失", "离开", "研发", "挥舞", "淹没", "献上", "构筑", "吸收", "穿戴", "评判", "领导", "协调", "传达", "丧失", "唤醒", "加快", "点燃",
)
_ZH_QUANTS = (
    "一个", "两个", "三个", "五个", "八个", "十个", "几个", "每个", "一块", "三块", "六块", "半块", "一组", "两组", "半组", "每组", "一格", "八格",
    "十格", "一张", "两张", "几张", "每张", "一次", "两次", "几次", "一枚", "十枚", "几枚", "一种", "五种", "七种", "几种", "一条", "两条", "几条",
    "一名", "三名", "五名", "一颗", "九颗", "几颗", "一件", "两件", "几件", "一片", "几片", "一层", "两层", "一级", "五级", "一只", "两只", "几只",
    "一些", "许多", "少量", "多个", "四块", "四组", "一棵", "两棵", "每棵", "一粒", "几粒", "一口", "半口", "三口", "一株", "每株", "两名", "每名",
    "一套", "整套", "一群", "那群", "一帮", "那帮", "三秒", "两阶",
)
_ZH_NOUNS = (
    "村民", "村庄", "职业", "面包", "小麦", "骨粉", "农民", "渔夫", "牧师", "屠夫", "石匠", "皮匠", "傻子", "盔甲", "物品", "方块", "僵尸", "女巫",
    "粒子", "言论", "声望", "折扣", "礼物", "火箭", "房屋", "梯子", "屋顶", "教堂", "码头", "书架", "讲台", "木桶", "高炉", "砂轮", "作物", "耕地",
    "雪屋", "苹果", "南瓜", "徽章", "服装", "袍子", "帽子", "围裙", "眼镜", "手套", "头巾", "羽毛", "箭袋", "矿车", "蝙蝠", "骆驼", "熊猫", "狐狸",
    "兔子", "绵羊", "海龟", "山羊", "青蛙", "羊驼", "干员", "技能", "天赋", "模组", "证章", "使者", "孩子", "舌尖", "恐鱼", "匪徒", "学者", "血肉",
    "纽带", "知识", "工具", "种子", "新芽", "野兽", "大树", "树苗", "族群", "洪流", "土壤", "幼子", "养分", "物种", "土地", "海洋", "星球", "环境",
    "食物", "锚点", "信物", "角色", "表情", "敌人", "弹道", "地块", "合同", "档案", "乐团", "模特", "支柱", "底盘", "乐队", "实体", "废墟", "帷幕",
    "乐章", "员工", "主管", "幕布", "镜头", "屏幕", "视野", "指令", "音符", "弱点", "灵魂", "职员", "噪音", "能量", "舞台", "掌声", "武器", "护甲",
    "镰刀", "总谱", "音乐", "末日", "交响", "饰品", "艺术", "公司", "乐谱", "神态", "举止", "风度", "听众", "内涵", "记忆", "心灵", "活物", "部门",
    "节奏", "萨满", "礼赞", "护盾", "子弹", "设施",
)
_EN_VERBS = (
    "trade", "breed", "spawn", "cure", "drop", "gather", "claim", "share", "harvest", "plant", "throw",
    "pick", "equip", "open", "close", "craft", "heal", "explore", "buy", "sell", "restock", "launch", "ring",
    "summon", "carry", "chase", "shear", "wear", "consume", "collect", "deposit", "attack", "avoid", "push",
    "unlock", "tend", "scan", "store", "refresh", "inhabit", "ambush", "conceal", "deploy", "block",
    "recover", "trigger", "charge", "bind", "stun", "drain", "drag", "slay", "annihilate", "station",
    "produce", "enlist", "clear", "prevent", "plunder", "ignore", "probe", "notice", "demand", "examine",
    "witness", "misplace", "doubt", "extend", "sever", "question", "discard", "foresee", "beget", "nourish",
    "occupy", "absorb", "reshape", "nurture", "recruit", "perform", "conduct", "breach", "contain", "reduce",
    "resist", "suppress", "assign", "select", "nullify", "shroud", "cover", "obscure", "lock", "unveil",
    "dampen", "mute", "warn", "wipe", "mount", "spread", "kill", "shut", "withstand", "dispatch", "withdraw",
    "apply", "call", "lose", "develop", "wield", "drown", "offer", "assemble", "soak", "don", "judge", "lead",
    "coordinate", "convey", "forfeit", "savor", "awaken", "hasten", "ignite", "applaud",
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
    "several handfuls of", "a tree of", "two trees of", "every tree of", "a grain of", "a few grains of",
    "a mouthful of", "half a mouthful of", "three mouthfuls of", "a sprout of", "every sprout of",
    "two squads of", "every squad of", "a set of", "a full set of", "a swarm of", "that swarm of", "a gang of",
    "that gang of", "three seconds of", "two tiers of",
)
_EN_NOUNS = (
    "villagers", "emeralds", "beds", "bells", "carrots", "potatoes", "beetroots", "seeds", "pumpkins",
    "golems", "zombies", "pillagers", "witches", "farmers", "clerics", "librarians", "masons", "shepherds",
    "fletchers", "butchers", "armorers", "toolsmiths", "cartographers", "leatherworkers", "weaponsmiths",
    "fishermen", "nitwits", "babies", "particles", "doors", "ladders", "trapdoors", "chickens", "cats",
    "wolves", "composters", "barrels", "lecterns", "looms", "grindstones", "smokers", "stonecutters",
    "cauldrons", "apples", "saddles", "carpets", "books", "maps", "gifts", "rockets", "badges", "outfits",
    "hats", "aprons", "gloves", "crops", "igloos", "ravagers", "dispensers", "pistons", "operators", "skills",
    "talents", "modules", "insignias", "envoys", "children", "tongues", "seaborn", "bandits", "scholars",
    "flesh", "bonds", "lore", "tools", "kernels", "sprouts", "beasts", "oaks", "saplings", "tribes",
    "torrents", "soils", "offspring", "nutrients", "species", "lands", "oceans", "planets", "habitats",
    "meals", "anchors", "tokens", "characters", "expressions", "enemies", "projectiles", "tiles", "contracts",
    "dossiers", "orchestras", "mannequins", "pillars", "chassis", "bands", "entities", "ruins", "veils",
    "movements", "employees", "managers", "curtains", "cameras", "screens", "sightlines", "commands", "notes",
    "weaknesses", "souls", "clerks", "noises", "energy", "stages", "applause", "weapons", "armor", "scythes",
    "scores", "music", "doomsdays", "symphonies", "accessories", "artworks", "companies", "librettos",
    "demeanors", "manners", "graces", "audiences", "meanings", "memories", "minds", "creatures", "departments",
    "rhythms", "shamans", "tributes", "shields", "bullets", "facilities",
)


def fog_noise(words, at: datetime) -> str:
    """黑雾时一个格子的假内容: words 是开始合约时抽好的编号 (contract.fog_words), 按这一格是一天里的第几个半小时取."""
    v, q, n = words[(at.hour * 2 + at.minute // 30) % len(words)]
    if i18n.is_en():
        return f"{_EN_VERBS[v % len(_EN_VERBS)].capitalize()} {_EN_QUANTS[q % len(_EN_QUANTS)]} {_EN_NOUNS[n % len(_EN_NOUNS)]}"
    return _ZH_VERBS[v % len(_ZH_VERBS)] + _ZH_QUANTS[q % len(_ZH_QUANTS)] + _ZH_NOUNS[n % len(_ZH_NOUNS)]


class HourLogPage(tk.Frame):

    def __init__(self, master, data_manager, on_text_changed=None, on_toggle=None,
                 on_open_settings=None, locked=None, split=None, fog=None, sealed=None, rest=None,
                 split_all=None, on_filled=None):
        super().__init__(master, bg=COLORS["bg"])
        self.dm = data_manager
        # 合约 (MECHANICS 18.4): 记录封锁 —— 已经填写了的格子 (手动 / 自动) 不能再改 (开始, 结束, 内容, 现在)
        self.locked = locked or (lambda start, end, text, now: False)
        # 细化规范: 这一小时分成两个半小时, 各填一格
        self.split = split or (lambda start: False)
        # 黑雾: 返回这个合约的种子 (没有黑雾时 None). 除了"现在"的格子和待填写的格子, 其他格子的时间写 ???, 内容是随机字符
        self.fog = fog or (lambda: None)
        self.rest = rest or (lambda a, b, text: False)   # 休息中没填写的格子: 黑雾不盖住, 自己填写之前一直能填 (18.4)
        self.split_all = split_all or (lambda: False)
        self.on_filled = on_filled or (lambda: None)  # 空着的格子填上了 (记录封锁另外封锁两个格子)
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

        # 第 i 行 = 以 i:00 结束的那一小时 (左边写起点); 最上面一行是昨天 23:00–24:00.
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
            was = self._get(h, i if split else None)
            s = self._slot_start(h)
            if split:
                self.dm.set_half_text(s.strftime('%Y-%m-%d'), s.hour, i, text)
            else:
                self.dm.set_hour_text(s.strftime('%Y-%m-%d'), s.hour, text)
            if self.on_text_changed:
                self.on_text_changed()
            if text and not str(was or "").strip():
                self.on_filled()
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
            seed = self.fog()                   # 黑雾时是开始合约时抽好的假内容编号
        except Exception:
            seed = None
        for h, (lbl, ent, var) in self.rows.items():
            start = self._slot_start(h)
            end = start + timedelta(hours=1)
            ent2, var2 = self.halves[h]
            split = self.split(start)
            if not split and seed is not None and self.split_all()                     and not (start <= now < end or (awaiting is not None and start <= awaiting < end)):
                split = True                        # 细化规范 + 黑雾: 盖住的行也都分成两格 (只是显示)
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
                hidden = seed is not None and not (a <= now < b or wait or self.rest(a, b, text))   # 黑雾: 只露出"现在"、待填写的和休息中没填写的格子
                shown = shown or not hidden
                if hidden:
                    noise = fog_noise(seed, a)
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
                lock = self.locked(a, b, text, now)     # 记录封锁另外封锁的格子: 还没结束的也锁着
                locked = locked or lock
                cfg(e, state="normal" if (enabled and done and not lock) else "disabled")
                cfg(e, highlightbackground=COLORS["accent"] if wait else COLORS["border"])
            ended = end <= now or (split and start + timedelta(minutes=30) <= now)

            is_wait = awaiting is not None and start <= awaiting < end
            is_now = enabled and start <= now < end
            label = f"{start.hour:02d}:00" if seed is None or shown else "???"   # 左边写这一小时的起点
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
