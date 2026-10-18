"""
合约 (MECHANICS.md 第 18 节): 词条, 进行中的合约, 结束后的记录. 界面在 pages/contract_page.py.

存档 contract.json: 进行中的合约 (live), 结束了的合约 (history, 整点记录的锁定也看它), 没开始的草稿 (draft).
live 和 history 随设备同步 (doc / merge), draft 只留在本机.
"""

import os
from datetime import datetime, timedelta

import config
from config import load_json_file, save_json_file


BASE_SECONDS = 4 * 3600          # 合约的初始时长
IDLE_LIMIT = 30 * 60             # 合约中空闲 (不在专注也不在休息) 超过这么久就结束
OFFLINE_LIMIT = 15 * 60          # 合约中有绑定的设备离线超过这么久就失败
VIOLATION_LIMIT = 4              # 初始目标生命值: 每违反一次减一, 减到 0 合约失败
HISTORY_KEEP = 30
FRESH_MS = 2 * 60 * 1000         # 合约的主人这么久没同步过: 本机来做自动结束

# (id, 组, 等级, 名字, 描述, 冲突的词条)
# 同一组的词条互相冲突, 界面上排在同一列; 冲突是双向的 (这里只写一边).
# 成系列的 (有好几档的) 在前, 界面上放在左边; 零散的 (只有一档的) 在后, 放在右边.
TERMS = [
    ("long1", "long", 1, "环境:持久战-I", "时长延长为1.2倍", ()),
    ("long2", "long", 2, "环境:持久战-II", "时长延长为1.4倍", ()),
    ("long3", "long", 3, "环境:持久战-III", "时长延长为1.6倍", ()),
    ("move1", "move", 1, "目标:高机动-I", "现实中的移动速度加快25%", ()),
    ("move2", "move", 2, "目标:高机动-II", "现实中的移动速度加快50%", ()),
    ("info1", "info", 1, "环境:信息阻断-I", "只能使用剩余联网时间的75% 且不能使用借贷", ()),
    ("info2", "info", 2, "环境:信息阻断-II", "只能使用剩余联网时间的50% 且不能使用借贷", ()),
    ("info3", "info", 3, "环境:信息阻断-III", "只能使用剩余联网时间的25% 且不能使用借贷", ()),
    ("isolate", "info", 4, "环境:信息隔绝", "不能使用联网功能", ()),
    ("tired1", "tired", 1, "环境:疲惫-I", "期间获得的休息时间为0.9x", ()),
    ("tired2", "tired", 2, "环境:疲惫-II", "期间获得的休息时间为0.75x", ()),
    ("tired3", "tired", 3, "环境:疲惫-III", "期间获得的休息时间为0.6x", ()),
    ("short1", "short", 1, "环境:支援短缺-I", "只能使用合约开始前已保存休息时间的50%", ()),
    ("short2", "short", 2, "环境:支援短缺-II", "不能使用合约开始前已经保存的休息时间", ()),
    ("retreat1", "retreat", 1, "环境:退路封锁-I", "初始目标生命值为3", ()),
    ("retreat2", "retreat", 2, "环境:退路封锁-II", "初始目标生命值为2", ()),
    ("last", "retreat", 3, "最后防线", "初始目标生命值为1", ()),
    ("chan1", "chan", 1, "环境:通道阻塞-I", "期间的最大窗口数改为4", ()),
    ("chan2", "chan", 2, "环境:通道阻塞-II", "期间的最大窗口数改为3", ()),
    ("suppress", "suppress", 1, "环境:抑制", "不能使用快速访问功能", ("isolate",)),
    ("jam", "jam", 2, "环境:全频道阻塞干扰", "期间使用proxy block-all模式", ()),
    ("music", "music", 1, "环境:乐理之灾", "非休息期间不能打开音乐播放软件 不能使用外部软件播放mp3等音频文件", ()),
    ("fog", "fog", 1, "环境:黑雾", "合约期间 专注时长 成就 日历 统计数据不可见 且无法打开设置", ()),
    ("seal", "seal", 1, "环境:记录封锁", "超过填写时间未填写 则不能再修改该记录", ()),
    ("fine", "fine", 2, "目标:细化规范", "记录变为30min一次", ()),
]
BY_ID = {t[0]: t for t in TERMS}
GROUPS = list(dict.fromkeys(t[1] for t in TERMS))
# 词条板的布局: 成系列的每组一列 (左边); 零散的不单独占列, 按等级放进右边的几列, 每一行从左往右挨着排, 不留空.
SERIES = [g for g in GROUPS if sum(1 for t in TERMS if t[1] == g) > 1]
_LOOSE = {}
LAYOUT = {}                                             # 词条 → (列, 等级)
for _t in TERMS:
    if _t[1] in SERIES:
        LAYOUT[_t[0]] = (SERIES.index(_t[1]), _t[2])
    else:
        _i = _LOOSE.get(_t[2], 0)
        _LOOSE[_t[2]] = _i + 1
        LAYOUT[_t[0]] = (len(SERIES) + _i, _t[2])
BOARD_COLS = len(SERIES) + max(_LOOSE.values(), default=0)
MAX_LEVEL = max(t[2] for t in TERMS)

# 软规则: 界面上照样能做, 但会被算作违反, 这个词条降一级 (结算按降级后的等级);
# 其余是硬规则, 立即生效 (黑雾, 记录封锁, 细化规范, 全频道阻塞干扰, 持久战, 疲惫).
SOFT = {"info1", "info2", "info3", "isolate", "chan1", "chan2", "music", "suppress", "short1", "short2"}
LOWER = {"isolate": "info3", "info3": "info2", "info2": "info1", "chan2": "chan1",
         "short2": "short1"}                     # 降一级换成哪个词条
SINGLE = {"music", "suppress"}       # 只有一档的软规则: 降级时等级减一, 减到 0 就失效

DURATION = {"long1": 1.2, "long2": 1.4, "long3": 1.6}
NET_SHARE = {"info1": 0.75, "info2": 0.5, "info3": 0.25, "isolate": 0.0}
REST = {"tired1": 0.9, "tired2": 0.75, "tired3": 0.6}
WINDOWS = {"chan1": 4, "chan2": 3}
SUPPLY = {"short1": 0.5, "short2": 0.0}         # 合约开始前积攒的休息时间能用几成
HOUR_TERMS = {"seal", "fine"}
LIFE = {"retreat1": 3, "retreat2": 2, "last": 1}  # 初始目标生命值 (硬规则)                   # 整点记录的词条: 整点记录关着时, 这次合约中照样开启
SUPPLY_WARN = (180, 60)                         # 支援短缺: 还差这么多秒就用超时各提醒一次

# 总等级 → 完成合约时给的休息时间 (分钟, 固定; 按结束时的等级)
# 每 3 级一档. 15 级以内每档只多 2 分钟, 16 级起每档多 10 分钟; 25 级 (差不多选满) 起封顶 90 分钟
REST_TABLE = [(0, 40), (1, 42), (4, 44), (7, 46), (10, 48), (13, 50), (16, 60), (19, 70), (22, 80), (25, 90)]

# 乐理之灾: 常见的音乐播放软件 (exe 名, 小写) 和音频文件扩展名 (窗口标题里出现就算在放).
# 视频播放器 (PotPlayer, VLC, MPC, KMPlayer, Windows 媒体播放器 …) 不在名单里: 只有用它们打开音频文件 (看标题) 才算.
MUSIC_EXES = {
    "cloudmusic.exe", "qqmusic.exe", "kugou.exe", "kwmusic.exe", "kuwomusic.exe", "spotify.exe",
    "foobar2000.exe", "aimp.exe", "itunes.exe", "applemusic.exe", "music.ui.exe",
    "musicbee.exe", "winamp.exe", "mediamonkey.exe",
    "dopamine.exe", "lx-music-desktop.exe", "yesplaymusic.exe", "listen1.exe", "tidal.exe",
    "deezer.exe", "amazon music.exe", "qianqian.exe", "baidumusic.exe", "migu music.exe",
    "musicplayer2.exe", "groove.exe",
}
AUDIO_EXTS = (".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".wma", ".ape", ".opus", ".ncm", ".kgm")


def level_of(terms) -> int:
    return sum(BY_ID[t][2] for t in terms if t in BY_ID)


def life_of(terms) -> int:
    """选了这些词条时的初始目标生命值."""
    return min((LIFE[t] for t in terms or [] if t in LIFE), default=VIOLATION_LIMIT)


def rest_minutes(level: int) -> int:
    out = REST_TABLE[0][1]
    for lo, minutes in REST_TABLE:
        if level >= lo:
            out = minutes
    return out


def rest_reward(level: int) -> int:
    """完成合约时存进积攒的休息时间的秒数."""
    return rest_minutes(level) * 60


def duration_of(terms) -> int:
    f = 1.0
    for t in terms:
        f = max(f, DURATION.get(t, 1.0))
    return int(round(BASE_SECONDS * f))


def conflicts(a: str, b: str) -> bool:
    if a == b or a not in BY_ID or b not in BY_ID:
        return False
    if BY_ID[a][1] == BY_ID[b][1]:
        return True
    return b in BY_ID[a][5] or a in BY_ID[b][5]


def blocked_by(tid: str, selected) -> str:
    """选了 selected 之后, tid 和其中哪一个冲突 (没有冲突返回 "")."""
    for s in selected:
        if s != tid and conflicts(tid, s):
            return s
    return ""


def clean_terms(terms) -> list:
    """去掉不认识的和互相冲突的 (后面的让位给前面的), 按 TERMS 的顺序."""
    out = []
    for t in terms or []:
        if t in BY_ID and t not in out and not blocked_by(t, out):
            out.append(t)
    order = {t[0]: i for i, t in enumerate(TERMS)}
    return sorted(out, key=order.get)


def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat(timespec="seconds")


def _parse(text):
    try:
        return datetime.fromisoformat(str(text)) if text else None
    except ValueError:
        return None


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


class Contract:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "contract.json")
        self.live = {"state": "none"}
        self.history = []
        self.draft = []
        self._watch = {}                      # 软规则: 此刻是不是已经超了 (只记在本机)
        self.blocked = lambda: False          # 重置中: 不写文件
        self.load()

    # ---- 存档 ----

    def load(self):
        d = load_json_file(self.path, {})
        d = d if isinstance(d, dict) else {}
        live = d.get("live")
        self.live = live if isinstance(live, dict) and live.get("state") in ("active", "none") \
            else {"state": "none"}
        if self.active:
            self.live["terms"] = clean_terms(self.live.get("terms"))
        self.history = [h for h in (d.get("history") or []) if isinstance(h, dict) and h.get("start")]
        self.draft = clean_terms(d.get("draft"))

    def save(self):
        if self.blocked():
            return
        self.history = sorted(self.history, key=lambda h: str(h.get("end")))[-HISTORY_KEEP:]
        save_json_file(self.path, {"live": self.live, "history": self.history, "draft": self.draft})

    # ---- 进行中的合约 ----

    @property
    def active(self) -> bool:
        return self.live.get("state") == "active"

    @property
    def terms(self) -> list:
        return list(self.live.get("terms") or []) if self.active else []

    def has(self, tid: str) -> bool:
        return tid in self.terms

    @property
    def start_at(self):
        return _parse(self.live.get("start")) if self.active else None

    @property
    def end_at(self):
        return _parse(self.live.get("end")) if self.active else None

    @property
    def cut(self) -> dict:
        """只有一档的词条被降了几级."""
        c = self.live.get("cut") if self.active else None
        return c if isinstance(c, dict) else {}

    def term_level(self, tid: str) -> int:
        return BY_ID[tid][2] - int(self.cut.get(tid) or 0)

    @property
    def level(self) -> int:
        return sum(self.term_level(t) for t in self.terms if t in BY_ID)

    @property
    def orig(self) -> list:
        """开始时选的词条 (降级前)."""
        return clean_terms(self.live.get("orig") or self.terms) if self.active else []

    @property
    def orig_level(self) -> int:
        return level_of(self.orig)

    @property
    def violations(self) -> int:
        return int(self.live.get("violations") or 0) if self.active else 0

    @property
    def violation_limit(self) -> int:
        """初始目标生命值."""
        return life_of(self.terms)

    @property
    def life(self) -> int:
        """目标生命值还剩多少 (每违反一次减一, 减到 0 合约失败)."""
        return max(0, self.violation_limit - self.violations)

    @property
    def rest(self) -> float:
        """合约中已经给出的休息时间 (秒, 已乘疲惫)."""
        return float(self.live.get("rest") or 0) if self.active else 0.0

    @property
    def reward(self) -> float:
        """完成时给的休息时间 (秒): 按现在的等级查表 (固定); 结算按降级后的等级."""
        return float(rest_reward(self.level)) if self.active else 0.0

    @property
    def owner(self) -> str:
        return str(self.live.get("owner") or "")

    def set_draft(self, terms):
        self.draft = clean_terms(terms)
        self.save()

    def start(self, terms, owner: str = "", now: datetime = None, pre: float = 0) -> dict:
        """pre: 开始时积攒的休息时间 (秒). 合约中暂停休息时这部分不打折; 支援短缺限制它能用几成."""
        now = (now or datetime.now()).replace(microsecond=0)
        terms = clean_terms(terms)
        pre = max(0.0, float(pre))
        share = self._supply_share(terms)
        self.live = {"state": "active", "id": _now_ms(), "start": _iso(now),
                     "end": _iso(now + timedelta(seconds=duration_of(terms))),
                     "terms": terms, "orig": list(terms), "cut": {}, "violations": 0, "rest": 0,
                     "owner": owner, "idle_since": None, "net": None, "first_break": False,
                     "pre": round(pre, 1), "pre_left": round(pre, 1), "pre_used": 0,
                     "supply": None if share is None else round(pre * share, 1), "hl": []}
        self._watch = {}
        self.save()
        return self.live

    def finish(self, result: str, now: datetime = None, by: str = "") -> dict:
        """结束合约: result = completed / failed / idle / offline / abandoned. 返回这一条记录 (含给出的休息时间)."""
        if not self.active:
            return {}
        now = (now or datetime.now()).replace(microsecond=0)
        end = min(now, self.end_at or now)
        reward = int(round(self.reward)) if result == "completed" else 0
        rec = {"id": self.live.get("id"), "start": self.live.get("start"), "end": _iso(end),
               "planned": self.live.get("end"), "terms": self.terms, "orig": self.orig,
               "level": self.level, "orig_level": self.orig_level, "cut": dict(self.cut),
               "result": result, "violations": self.violations, "reward": reward,
               "pre_left": self.pre_left}
        self.history.append(rec)
        self.live = {"state": "none", "owner": by, "first_break": result == "completed"}
        self.save()
        return rec

    @property
    def last(self) -> dict:
        """最近结束的一个合约."""
        return max(self.history, key=lambda h: str(h.get("end")), default={})

    # ---- 到期 / 空闲 ----

    def due(self, now: datetime, timer_idle: bool):
        """该结束了吗: ('completed' / 'idle', 结束的时刻) 或 ('', None).
        同时记下空闲从什么时候开始 (只由做自动结束的设备调用)."""
        if not self.active:
            return "", None
        end = self.end_at
        since = _parse(self.live.get("idle_since"))
        if timer_idle:
            if since is None:
                since = now
                self.live["idle_since"] = _iso(now)
                self.save()
        elif since is not None:
            self.live["idle_since"] = None
            self.save()
            since = None
        if since is not None and since + timedelta(seconds=IDLE_LIMIT) <= min(now, end):
            return "idle", since + timedelta(seconds=IDLE_LIMIT)
        if now >= end:
            return "completed", end
        return "", None

    def offline_due(self, devices, now: datetime, floor: datetime):
        """设备离线超过 OFFLINE_LIMIT: (设备名, 失败的时刻) 或 None. devices: [(名字, 离线开始的时刻)];
        离线从合约开始, 以及 floor (本机这次打开程序的时刻: 本机关着时的事由其他设备判定) 之后才算."""
        if not self.active or not self.start_at:
            return None
        for name, since in devices:
            since = max(since, self.start_at, floor)
            if since + timedelta(seconds=OFFLINE_LIMIT) <= now:
                return name, since + timedelta(seconds=OFFLINE_LIMIT)
        return None

    def idle_left(self, now: datetime):
        """空闲还能持续多久 (秒); 不在空闲时 None."""
        since = _parse(self.live.get("idle_since")) if self.active else None
        if since is None:
            return None
        return max(0.0, IDLE_LIMIT - (now - since).total_seconds())

    def violate(self, tid: str):
        """违反了一次词条 tid: 记一次, 这个词条降一级. 返回 (是否达到了上限, 降级后的词条 / "" = 失效).
        tid = "": 不是哪个词条的违反 (整点记录超时未填写), 只记一次."""
        if not self.active:
            return False, ""
        new = self.downgrade(tid) if tid else ""
        self.live["violations"] = self.violations + 1
        self.save()
        return self.violations >= self.violation_limit, new

    def downgrade(self, tid: str) -> str:
        """词条降一级: 系列词条换成低一档的, 只有一档的等级减一; 降到底就失效. 返回降级后的词条 ("" = 失效)."""
        terms = self.terms
        if tid not in terms:
            return ""
        old_share = self.net_share()
        old_supply = self._supply_share(terms)
        cut = dict(self.cut)
        i = terms.index(tid)
        if tid in LOWER:
            terms[i] = new = LOWER[tid]
        elif tid in SINGLE and self.term_level(tid) > 1:
            cut[tid] = int(cut.get(tid) or 0) + 1
            new = tid
        else:
            terms.pop(i)
            cut.pop(tid, None)
            new = ""
        self.live["terms"] = clean_terms(terms)
        self.live["cut"] = cut
        # 信息阻断: 降一级 = 多给低一档多出来的那部分额度 (从现在用到的地方算起)
        n = self.live.get("net")
        new_share = self.net_share()
        if isinstance(n, dict) and old_share is not None and new_share is not None:
            n["limit"] = float(n.get("limit") or 0) + float(n.get("base") or 0) * (new_share - old_share)
        # 支援短缺: 同样多给低一档多出来的那部分; 失效了就不再限制
        new_supply = self._supply_share(self.live["terms"])
        if new_supply is None:
            self.live["supply"] = None
        elif old_supply is not None and self.live.get("supply") is not None:
            self.live["supply"] = round(float(self.live["supply"]) + self.pre * (new_supply - old_supply), 1)
        self.save()
        return new

    # ---- 软规则: 有没有违反 (由调用方算作违反: violate) ----

    def net_term(self) -> str:
        return next((t for t in self.terms if t in NET_SHARE), "")

    def net_check(self, used: float, bonus: float, raw: float, date: str):
        """信息阻断 / 信息隔绝: 合约开始时 (以及合约中换日时) 记下当时的剩余额度, 只能用它的几成, 不能借贷.
        返回 (要降级的词条, "loan" / "over"), 没有违反时 ("", "") (只由做自动结束的设备调用, 额度是各设备共用的)."""
        tid = self.net_term()
        if not tid:
            return "", ""
        n = self.live.get("net")
        if not isinstance(n, dict) or n.get("date") != date:
            raw = max(0.0, float(raw))
            self.live["net"] = {"date": date, "base": round(raw, 1), "bonus": round(float(bonus), 1),
                                "limit": round(float(used) + raw * NET_SHARE[tid], 1)}
            self.save()
            return "", ""
        if float(bonus) > float(n.get("bonus") or 0) + 0.5:
            n["bonus"] = round(float(bonus), 1)
            self.save()
            return tid, "loan"
        if float(used) > float(n.get("limit") or 0) + 0.5:
            return tid, "over"
        return "", ""

    def net_left(self, used: float):
        """合约还允许用多少联网额度 (秒); 没有信息阻断时 None."""
        n = self.live.get("net") if self.net_term() else None
        if not isinstance(n, dict):
            return None
        return max(0.0, float(n.get("limit") or 0) - float(used))

    def _edge(self, key: str, over: bool) -> bool:
        """只在刚越过的那一下算一次违反; 回到限额以内后再越过才再算 (只记在本机)."""
        w = self.__dict__.setdefault("_watch", {})
        was = w.get(key, False)
        w[key] = over
        return over and not was

    def window_check(self, n: int) -> str:
        """通道阻塞: 任务栏上的窗口数超过词条的上限."""
        lim = self.window_limit()
        tid = next((t for t in self.terms if t in WINDOWS), "")
        if not lim or not tid:
            return ""
        return tid if self._edge("win", n > lim) else ""

    def music_check(self, playing: bool) -> str:
        """乐理之灾: 开着音乐播放软件 / 用别的软件放音频文件."""
        if not self.has("music"):
            return ""
        return "music" if self._edge("music", playing) else ""

    def quick_check(self) -> str:
        """抑制: 用了快速访问."""
        return "suppress" if self.has("suppress") else ""

    # ---- 合约开始前积攒的休息时间 (暂停不打折; 支援短缺) ----
    # 一次休息 = 建议休息时长 + 积攒的休息时间; 先用合约中得到的, 合约开始前积攒的放在最后用.

    @property
    def pre(self) -> float:
        """开始时积攒的休息时间 (秒)."""
        return float(self.live.get("pre") or 0) if self.active else 0.0

    @property
    def pre_left(self) -> float:
        """还在积攒的休息时间里的那部分 (没用掉, 也没有拿进正在进行的休息)."""
        return float(self.live.get("pre_left") or 0) if self.active else 0.0

    @property
    def pre_used(self) -> float:
        return float(self.live.get("pre_used") or 0) if self.active else 0.0

    def pre_take(self, carry: float) -> float:
        """开始休息, 积攒的 carry 秒全部拿进这次休息: 其中合约开始前的有多少 (从 pre_left 里拿走)."""
        if not self.active:
            return 0.0
        x = min(max(0.0, float(carry)), self.pre_left)
        if x > 0:
            self.live["pre_left"] = round(self.pre_left - x, 1)
            self.save()
        return x

    def pre_settle(self, taken: float, back: float):
        """一次休息结束 (back = 暂停时还给积攒的那部分合约开始前的时间, 自然结束时 0)."""
        if not self.active:
            return
        taken, back = max(0.0, float(taken)), max(0.0, min(float(taken), float(back)))
        self.live["pre_left"] = round(self.pre_left + back, 1)
        self.live["pre_used"] = round(self.pre_used + taken - back, 1)
        self.save()

    @staticmethod
    def _supply_share(terms):
        shares = [SUPPLY[t] for t in terms or [] if t in SUPPLY]
        return min(shares) if shares else None

    def supply_term(self) -> str:
        return next((t for t in self.terms if t in SUPPLY), "")

    @property
    def supply_limit(self):
        """支援短缺: 合约开始前积攒的休息时间最多能用多少秒 (None = 不限)."""
        v = self.live.get("supply") if self.supply_term() else None
        return None if v is None else float(v)

    def supply_until(self, left: float, taken: float):
        """正在休息 (还剩 left 秒, 这次休息里有 taken 秒是合约开始前的): 再过多少秒就用超了 (None = 这次用不超)."""
        lim = self.supply_limit
        if lim is None or taken <= 0:
            return None
        cross = float(taken) - (lim - self.pre_used)       # 剩余时间降到这里时用超
        if cross <= 0:
            return None
        return max(0.0, float(left) - cross)

    def supply_check(self, left: float, taken: float) -> str:
        """支援短缺: 合约开始前积攒的休息时间用超了 (正在休息时按已经用掉的算)."""
        lim = self.supply_limit
        tid = self.supply_term()
        if lim is None or not tid:
            return ""
        used = self.pre_used + max(0.0, float(taken) - max(0.0, float(left)))
        return tid if used > lim + 0.5 else ""

    # ---- 词条的效果 ----

    def overrides(self) -> dict:
        """合约中盖过设置的项 (config 里的值, 硬规则): 全频道阻塞干扰 → 代理-全屏蔽."""
        return {"NET_MODE": "proxy_all"} if self.has("jam") else {}

    def window_limit(self) -> int:
        return min((WINDOWS[t] for t in self.terms if t in WINDOWS), default=0)

    def net_share(self):
        """只能用剩余联网时间的几成 (None = 不限)."""
        shares = [NET_SHARE[t] for t in self.terms if t in NET_SHARE]
        return min(shares) if shares else None

    def fog(self) -> bool:
        return self.has("fog")

    def hour_forced(self) -> bool:
        """选了整点记录的词条: 整点记录关着时, 这次合约中照样开启."""
        return any(t in HOUR_TERMS for t in self.terms)

    def fine_hour(self, slot_start: datetime) -> bool:
        """细化规范: 这一小时 (按开始时刻) 和进行中的合约重叠, 记录改为每 30 分钟一次."""
        if not self.has("fine"):
            return False
        a, b = self.start_at, self.end_at
        return bool(a and b and a < slot_start + timedelta(hours=1) and slot_start < b)

    def rest_factor(self) -> float:
        return min((REST[t] for t in self.terms if t in REST), default=1.0)

    def book_rest(self, seconds: float, end: datetime = None) -> float:
        """一段专注换来的休息时间 (秒, 已乘"休息时间倍率"). 返回给出的休息时间.
        合约中: 乘疲惫的倍率 (记进合约里给出的休息时间). 完成合约的奖励另外按等级给 (reward)."""
        seconds = max(0.0, float(seconds))
        end = end or datetime.now()
        if self.active and self.start_at and self.start_at <= end:
            now = seconds * self.rest_factor()
            self.live["rest"] = round(self.rest + now, 1)
            self.save()
            return now
        return seconds

    def book_rest_after(self, seconds: float, start: datetime) -> float:
        """完成之后才结束的专注, 开始于刚完成的合约里: 按那个合约的疲惫给."""
        h = self.last
        hs, he = _parse(h.get("start")), _parse(h.get("end"))
        if h.get("result") == "completed" and hs and he and hs <= start < he:
            terms = h.get("terms") or []
            f = min((REST[t] for t in terms if t in REST), default=1.0)
            return seconds * f
        return seconds

    def take_first_break(self) -> bool:
        """完成合约后的第一次休息: 用掉这个标记."""
        if self.active or not self.live.get("first_break"):
            return False
        self.live["first_break"] = False
        self.save()
        return True

    @staticmethod
    def first_break_rate(x: float) -> float:
        """完成合约后的第一次休息: 暂停时的结转比例 1 - (1 - x) / 2."""
        x = min(1.0, max(0.0, float(x)))
        return 1.0 - (1.0 - x) / 2.0

    # ---- 整点记录: 超时未填写算一次违反; 记录封锁 ----

    def periods(self, now: datetime = None, term: str = ""):
        """各个合约 (结束了的和进行中的) 的 (开始, 结束); term: 只要选了这个词条的."""
        out = []
        for h in self.history:
            a, b = _parse(h.get("start")), _parse(h.get("end"))
            if a and b and (not term or term in (h.get("orig") or h.get("terms") or [])):
                out.append((a, b))
        if self.active and self.start_at and (not term or term in self.orig):
            out.append((self.start_at, now or datetime.now()))
        return out

    def hour_units(self, now: datetime):
        """进行中的合约里已经结束的记录格: [(键, 开始, 结束)].
        键是 "日期/小时", 细化规范的两个半小时是 "日期/小时/1" "日期/小时/2"."""
        if not self.active or not self.start_at:
            return []
        a, b = self.start_at, min(now, self.end_at or now)
        out = []
        h = a.replace(minute=0, second=0, microsecond=0)
        while h < b:
            key = f"{h:%Y-%m-%d}/{h.hour}"
            if self.fine_hour(h):
                parts = [(key + "/1", h, h + timedelta(minutes=30)),
                         (key + "/2", h + timedelta(minutes=30), h + timedelta(hours=1))]
            else:
                parts = [(key, h, h + timedelta(hours=1))]
            out += [p for p in parts if p[1] < b and a < p[2] and p[2] <= now]
            h += timedelta(hours=1)
        return out

    def hour_misses(self, now: datetime, fill: timedelta, text_of, auto: str) -> list:
        """合约中过了填写时限还没填写的记录格 (空着, 或者是超时自动记录的内容): 每格只算一次违反.
        返回新算的键 (只由做自动结束的设备调用)."""
        if not self.active:
            return []
        old = list(self.live.get("hl") or [])
        seen = set(old)
        new = []
        for key, _a, b in self.hour_units(now):
            if key in seen or b + fill > now:
                continue
            seen.add(key)
            if (text_of(key) or "") in ("", auto):
                new.append(key)
        if len(seen) != len(old):
            self.live["hl"] = sorted(seen)
            self.save()
        return new

    def hour_locked(self, start: datetime, end: datetime, text: str, now: datetime, fill: timedelta,
                    auto: str) -> bool:
        """记录封锁: 和选了这个词条的合约重叠的记录格, 过了填写时限还没填写 (空着 / 超时自动记录),
        就不能再改 —— 合约结束了也一样."""
        if now < end + fill or (text or "") not in ("", auto):
            return False
        return any(a < end and start < b for a, b in self.periods(now, "seal"))

    # ---- 设备同步 ----

    def doc(self) -> dict:
        return {"live": dict(self.live), "history": [dict(h) for h in self.history]}

    def merge(self, changes: dict) -> bool:
        """同步来的改动. 进行中的合约: 后改的覆盖先改的; 记录: 合并 (按 id). 返回进行中的合约是否变了."""
        changed = False
        if isinstance(changes.get("live"), dict) and changes["live"].get("state") in ("active", "none"):
            if changes["live"] != self.live:
                self.live = dict(changes["live"])
                if self.active:
                    self.live["terms"] = clean_terms(self.live.get("terms"))
                changed = True
        if isinstance(changes.get("history"), list):
            have = {str(h.get("id")) for h in self.history}
            for h in changes["history"]:
                if isinstance(h, dict) and h.get("start") and str(h.get("id")) not in have:
                    self.history.append(dict(h))
                    have.add(str(h.get("id")))
        self.save()
        return changed


def _selftest():
    import tempfile
    assert level_of(["long3", "isolate", "last"]) == 10
    assert life_of([]) == 4 and life_of(["retreat1"]) == 3 and life_of(["retreat2"]) == 2 and life_of(["last"]) == 1
    assert conflicts("retreat1", "last") and conflicts("retreat2", "last"), "退路封锁和最后防线是同一组"
    assert not {"vlc.exe", "potplayermini64.exe", "mpc-hc64.exe", "wmplayer.exe"} & MUSIC_EXES, "视频播放器不算"
    assert rest_minutes(0) == 40 and rest_minutes(3) == 42 and rest_minutes(4) == 44 and rest_minutes(15) == 50
    assert rest_minutes(16) == 60 and rest_minutes(24) == 80 and rest_minutes(25) == 90 and rest_reward(26) == 90 * 60
    assert conflicts("long1", "long3") and conflicts("isolate", "info2"), "同一组互相冲突"
    assert not conflicts("isolate", "chan1") and not conflicts("chan2", "isolate") and conflicts("suppress", "isolate")
    assert not conflicts("jam", "music") and conflicts("short1", "short2") and not conflicts("seal", "fine")
    assert clean_terms(["long2", "long1", "x", "isolate", "suppress", "jam"]) == ["long2", "isolate", "jam"]
    assert clean_terms(["isolate", "chan2"]) == ["isolate", "chan2"], "信息隔绝和通道阻塞可以一起选"
    assert duration_of([]) == 4 * 3600 and duration_of(["long3"]) == int(4 * 3600 * 1.6)
    assert LAYOUT["short2"][0] < LAYOUT["suppress"][0], "成系列的在左边, 零散的在右边"
    assert BOARD_COLS == len(SERIES) + 4 and LAYOUT["jam"] == (len(SERIES), 2) and LAYOUT["fine"] == (len(SERIES) + 1, 2),         "零散的词条挨着排, 不留空"
    assert len(set(LAYOUT.values())) == len(TERMS)
    best = clean_terms(["long3", "move2", "info3", "suppress", "jam", "music", "fog", "tired3", "chan2", "last",
                        "short2", "seal", "fine"])
    assert level_of(best) == 26, level_of(best)

    c = Contract(tempfile.mkdtemp())
    t0 = datetime(2026, 10, 18, 9, 30)
    c.start(["tired2", "info2", "chan1", "long1", "music"], owner="me", now=t0)
    assert c.active and c.level == 2 + 2 + 1 + 1 + 1 and c.end_at == t0 + timedelta(seconds=int(4 * 3600 * 1.2))
    assert c.overrides() == {} and not c.fog() and not c.hour_forced()
    # 信息阻断-II: 开始时剩 100 分钟, 只能用 50 分钟; 用超了 → 降成 -I, 多给 25 分钟
    assert c.net_check(600, 0, 6000, "2026-10-18") == ("", "")
    assert c.net_left(600) == 3000 and c.net_check(3599, 0, 0, "2026-10-18") == ("", "")
    assert c.net_check(3700, 0, 0, "2026-10-18") == ("info2", "over")
    over, new = c.violate("info2")
    assert not over and new == "info1" and c.terms == ["long1", "info1", "tired2", "chan1", "music"], c.terms
    assert c.net_left(3700) == 600 + 3000 + 1500 - 3700 and c.level == 6 and c.orig_level == 7
    assert c.net_check(3800, 0, 0, "2026-10-18") == ("", "")
    assert c.net_check(3800, 600, 0, "2026-10-18") == ("info1", "loan"), "借贷也算违反"
    assert c.net_check(3800, 600, 0, "2026-10-18") == ("", ""), "同一次借贷只算一次"
    assert c.downgrade("info1") == "" and not c.net_term() and c.net_check(9999, 9999, 0, "x") == ("", "")
    # 通道阻塞 / 乐理之灾: 只在刚越过的那一下算
    assert c.window_check(5) == "chan1" and c.window_check(6) == "" and c.window_check(3) == ""
    assert c.window_check(5) == "chan1"
    assert c.music_check(True) == "music" and c.music_check(True) == "" and c.music_check(False) == ""
    assert c.downgrade("music") == "" and not c.has("music"), "乐理之灾只有 1 级, 降一级就失效"
    assert c.quick_check() == ""
    # 休息: 疲惫 ×0.75; 完成时按降级后的等级给固定的时间 (等级 4 → 44 分钟)
    assert abs(c.book_rest(1000, t0 + timedelta(hours=1)) - 750) < 1e-6
    assert c.level == 4 and c.reward == 44 * 60
    # 空闲 30 分钟结束
    assert c.due(t0 + timedelta(hours=1), True) == ("", None)
    assert c.idle_left(t0 + timedelta(hours=1, minutes=10)) == 20 * 60
    assert c.due(t0 + timedelta(hours=1, minutes=31), True) == ("idle", t0 + timedelta(hours=1, minutes=30))
    assert c.due(t0 + timedelta(hours=1, minutes=31), False)[0] == "", "开始专注就不算空闲"
    # 设备离线 15 分钟失败: 从合约开始 / 本机打开程序之后才算
    t1 = t0 + timedelta(hours=1)
    assert c.offline_due([("手机", t0 - timedelta(hours=3))], t0 + timedelta(minutes=14), t0) is None
    assert c.offline_due([("手机", t0 - timedelta(hours=3))], t0 + timedelta(minutes=16), t0) == \
        ("手机", t0 + timedelta(minutes=15))
    assert c.offline_due([("手机", t0)], t1 + timedelta(minutes=10), t1) is None, "本机刚打开"
    # 目标生命值: 默认 4; 退路封锁-I 只有 3, 违反 3 次结束; 整点记录超时未填写也算 (不降词条)
    c.start(["long1"], owner="me", now=t0)
    assert c.violation_limit == 4 and c.life == 4
    c.start(["suppress", "info3", "long1", "retreat1"], owner="me", now=t0)
    assert c.violation_limit == 3 and c.life == 3 and c.quick_check() == "suppress"
    assert c.violate("suppress") == (False, "") and not c.has("suppress") and c.life == 2
    assert c.violate("") == (False, "") and c.violations == 2 and c.level == 3 + 1 + 1 and c.life == 1
    assert c.violate("info3") == (True, "info2") and c.life == 0
    rec = c.finish("failed", t0 + timedelta(hours=2))
    assert rec["reward"] == 0 and rec["orig_level"] == 6 and rec["level"] == 4 and not c.active
    assert not c.take_first_break()

    # 完成: 按等级给固定的休息时间 (等级 4 → 44 分钟), 第一次休息的结转比例
    c.start(["last", "tired1"], owner="me", now=t0)
    assert c.violation_limit == 1 and abs(c.book_rest(600, t0) - 540) < 1e-6 and c.reward == 44 * 60
    assert c.due(t0 + timedelta(hours=4), False) == ("completed", t0 + timedelta(hours=4))
    rec = c.finish("completed", t0 + timedelta(hours=4, minutes=3))
    assert rec["reward"] == 44 * 60 and rec["end"] == (t0 + timedelta(hours=4)).isoformat()
    assert abs(c.book_rest_after(600, t0 + timedelta(hours=3)) - 540) < 1e-6 and c.reward == 0, "完成后才结束的专注只乘疲惫"
    assert c.book_rest_after(600, t0 + timedelta(hours=5)) == 600
    assert c.take_first_break() and not c.take_first_break()
    assert abs(Contract.first_break_rate(0.7) - 0.85) < 1e-9

    # 最近结束的那个, 不是最近开始的那个
    c.start(["long1"], now=t0 - timedelta(days=1))
    c.finish("abandoned", t0 - timedelta(days=1) + timedelta(hours=1))
    assert c.last["result"] == "completed" and abs(c.book_rest_after(600, t0 + timedelta(hours=3)) - 540) < 1e-6

    # 合约开始前积攒的休息时间 (20 分钟) + 支援短缺-II: 一点都不能用
    c.start(["short2"], owner="me", now=t0, pre=1200)
    assert c.pre == 1200 and c.pre_left == 1200 and c.supply_limit == 0
    taken = c.pre_take(1500)                 # 积攒的 25 分钟里有 20 分钟是合约开始前的
    assert taken == 1200 and c.pre_left == 0
    # 这次休息 = 建议 10 分钟 + 积攒 25 分钟; 剩余 > 20 分钟时还没用到合约开始前的
    assert c.supply_until(1800, taken) == 600 and c.supply_check(1300, taken) == ""
    assert c.supply_check(1100, taken) == "short2"
    c.pre_settle(taken, 1100)                # 暂停: 合约开始前的 1100 秒原样还回去
    assert c.pre_left == 1100 and abs(c.pre_used - 100) < 1e-6
    assert c.violate("short2") == (False, "short1") and c.supply_limit == 600, "降级: 多给 50%"
    assert c.supply_check(0, 0) == "" and c.supply_until(1100, 1100) == 1100 - (1100 - 500)
    rec = c.finish("abandoned", t0 + timedelta(hours=1))
    assert rec["pre_left"] == 1100

    # 整点记录: 超时未填写算违反 (每格一次); 细化规范分成半小时; 记录封锁合约结束后照样锁着
    fill = timedelta(hours=1)
    texts = {"2026-10-18/9/2": "学习"}
    c.start(["seal", "fine"], owner="me", now=t0)
    assert c.hour_forced() and c.fine_hour(datetime(2026, 10, 18, 9)) and not c.fine_hour(datetime(2026, 10, 18, 8))
    units = c.hour_units(datetime(2026, 10, 18, 10, 40))
    assert [u[0] for u in units] == ["2026-10-18/9/2", "2026-10-18/10/1"], units
    assert c.hour_misses(datetime(2026, 10, 18, 11, 0), fill, texts.get, "Sleeping") == []
    assert c.hour_misses(datetime(2026, 10, 18, 11, 31), fill, texts.get, "Sleeping") == ["2026-10-18/10/1"]
    assert c.hour_misses(datetime(2026, 10, 18, 11, 40), fill, texts.get, "Sleeping") == [], "每格只算一次"
    a, b = datetime(2026, 10, 18, 10), datetime(2026, 10, 18, 10, 30)
    assert not c.hour_locked(a, b, "", datetime(2026, 10, 18, 11, 29), fill, "Sleeping")
    assert c.hour_locked(a, b, "Sleeping", datetime(2026, 10, 18, 11, 31), fill, "Sleeping")
    assert not c.hour_locked(a, b, "学习", datetime(2026, 10, 18, 11, 31), fill, "Sleeping"), "按时填了的不锁"
    c.finish("abandoned", datetime(2026, 10, 18, 12))
    assert c.hour_locked(a, b, "Sleeping", datetime(2026, 10, 19), fill, "Sleeping"), "合约结束后照样锁着"
    assert not c.hour_locked(datetime(2026, 10, 18, 13), datetime(2026, 10, 18, 14), "", datetime(2026, 10, 19),
                             fill, "Sleeping")

    # 同步: 记录按 id 合并
    d = Contract(tempfile.mkdtemp())
    d.merge(c.doc())
    assert len(d.history) == 5 and not d.active
    d.merge({"history": c.doc()["history"]})
    assert len(d.history) == 5
    c2 = Contract(os.path.dirname(c.path))
    assert len(c2.history) == 5, "写进文件, 重开后仍在"
    return "contract selftest OK"


if __name__ == "__main__":
    print(_selftest())
