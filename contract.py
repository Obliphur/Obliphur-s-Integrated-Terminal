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
VIOLATION_LIMIT = 3              # 违反词条累计这么多次就结束
HISTORY_KEEP = 30
FRESH_MS = 2 * 60 * 1000         # 合约的主人这么久没同步过: 本机来做自动结束

# (id, 组, 等级, 名字, 描述, 冲突的词条)
# 同一组的词条互相冲突, 界面上排在同一列; 冲突是双向的 (这里只写一边).
TERMS = [
    ("long1", "long", 1, "环境:持久战-I", "时长延长为1.1倍", ()),
    ("long2", "long", 2, "环境:持久战-II", "时长延长为1.2倍", ()),
    ("long3", "long", 3, "环境:持久战-III", "时长延长为1.3倍", ()),
    ("move1", "move", 1, "目标:高机动-I", "现实中的移动速度加快25%", ()),
    ("move2", "move", 2, "目标:高机动-II", "现实中的移动速度加快50%", ()),
    ("info1", "info", 1, "环境:信息阻断-I", "只能使用剩余联网时间的75% 且不能使用借贷", ()),
    ("info2", "info", 2, "环境:信息阻断-II", "只能使用剩余联网时间的50% 且不能使用借贷", ()),
    ("info3", "info", 3, "环境:信息阻断-III", "只能使用剩余联网时间的25% 且不能使用借贷", ()),
    ("isolate", "info", 4, "环境:信息隔绝", "不能使用联网功能", ()),
    ("suppress", "suppress", 2, "环境:抑制", "不能使用快速访问功能", ("isolate",)),
    ("jam", "jam", 2, "环境:全频道阻塞干扰", "期间使用proxy block-all模式", ()),
    ("music", "music", 2, "环境:乐理之灾", "期间不能打开音乐播放软件 不能使用外部软件播放mp3等音频文件", ()),
    ("fog", "fog", 1, "环境:黑雾", "期间不能查看统计数据 且在专注时无法看到专注时长", ()),
    ("tired1", "tired", 1, "环境:疲惫-I", "期间获得的休息时间为0.9x", ()),
    ("tired2", "tired", 2, "环境:疲惫-II", "期间获得的休息时间为0.75x", ()),
    ("tired3", "tired", 3, "环境:疲惫-III", "期间获得的休息时间为0.6x", ()),
    ("chan1", "chan", 1, "环境:通道阻塞-I", "期间的最大窗口数改为4", ()),
    ("chan2", "chan", 2, "环境:通道阻塞-II", "期间的最大窗口数改为3", ()),
    ("last", "last", 1, "最后防线", "违反一次词条即结束合约", ()),
]
BY_ID = {t[0]: t for t in TERMS}
GROUPS = list(dict.fromkeys(t[1] for t in TERMS))      # 界面上从左到右的列
MAX_LEVEL = max(t[2] for t in TERMS)

# 用"不能" / "禁止"描述的词条是软规则: 界面上照样能做, 但会被算作违反, 这个词条降一级 (结算按降级后的等级);
# 用"无法"描述的 (黑雾) 是硬规则, 立即生效; 全频道阻塞干扰 / 持久战 / 疲惫直接生效.
SOFT = {"info1", "info2", "info3", "isolate", "chan1", "chan2", "music", "suppress"}
LOWER = {"isolate": "info3", "info3": "info2", "info2": "info1", "chan2": "chan1"}   # 降一级换成哪个词条
SINGLE = {"music", "suppress"}       # 只有一档的词条: 降级时等级减一, 减到 0 就失效

DURATION = {"long1": 1.1, "long2": 1.2, "long3": 1.3}
NET_SHARE = {"info1": 0.75, "info2": 0.5, "info3": 0.25, "isolate": 0.0}
REST = {"tired1": 0.9, "tired2": 0.75, "tired3": 0.6}
WINDOWS = {"chan1": 4, "chan2": 3}

# 总等级 → 完成后的休息时间 (分钟, 20 分钟 = ×1); 倍率 = 分钟 / 20, 和"休息时间倍率"相乘
REST_TABLE = [(0, 20), (1, 24), (4, 28), (7, 32), (10, 36), (13, 40), (16, 44), (19, 50)]

# 乐理之灾: 常见的音乐播放软件 (exe 名, 小写) 和音频文件扩展名 (窗口标题里出现就算在放)
MUSIC_EXES = {
    "cloudmusic.exe", "qqmusic.exe", "kugou.exe", "kwmusic.exe", "kuwomusic.exe", "spotify.exe",
    "foobar2000.exe", "aimp.exe", "itunes.exe", "applemusic.exe", "music.ui.exe",
    "microsoft.media.player.exe", "wmplayer.exe", "musicbee.exe", "winamp.exe", "mediamonkey.exe",
    "dopamine.exe", "lx-music-desktop.exe", "yesplaymusic.exe", "listen1.exe", "tidal.exe",
    "deezer.exe", "amazon music.exe", "qianqian.exe", "baidumusic.exe", "migu music.exe",
    "musicplayer2.exe", "potplayermini64.exe", "potplayermini.exe", "potplayer.exe", "vlc.exe",
    "mpc-hc64.exe", "mpc-hc.exe", "mpc-be64.exe", "mpc-be.exe", "kmplayer.exe", "groove.exe",
}
AUDIO_EXTS = (".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".wma", ".ape", ".opus", ".ncm", ".kgm")


def level_of(terms) -> int:
    return sum(BY_ID[t][2] for t in terms if t in BY_ID)


def rest_minutes(level: int) -> int:
    out = REST_TABLE[0][1]
    for lo, minutes in REST_TABLE:
        if level >= lo:
            out = minutes
    return out


def rest_mult(level: int) -> float:
    return rest_minutes(level) / 20.0


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
        return 1 if self.has("last") else VIOLATION_LIMIT

    @property
    def rest(self) -> float:
        """合约中已经给出的休息时间 (秒, 已乘疲惫)."""
        return float(self.live.get("rest") or 0) if self.active else 0.0

    @property
    def reward(self) -> float:
        """完成时多给的休息时间 (秒) = 合约中给出的休息时间 × (按现在的等级的倍率 - 1); 结算按降级后的等级."""
        return self.rest * (rest_mult(self.level) - 1.0)

    @property
    def owner(self) -> str:
        return str(self.live.get("owner") or "")

    def set_draft(self, terms):
        self.draft = clean_terms(terms)
        self.save()

    def start(self, terms, owner: str = "", now: datetime = None) -> dict:
        now = (now or datetime.now()).replace(microsecond=0)
        terms = clean_terms(terms)
        self.live = {"state": "active", "id": _now_ms(), "start": _iso(now),
                     "end": _iso(now + timedelta(seconds=duration_of(terms))),
                     "terms": terms, "orig": list(terms), "cut": {}, "violations": 0, "rest": 0,
                     "owner": owner, "idle_since": None, "net": None, "first_break": False}
        self._watch = {}
        self.save()
        return self.live

    def finish(self, result: str, now: datetime = None, by: str = "") -> dict:
        """结束合约: result = completed / failed / idle / abandoned. 返回这一条记录 (含给出的休息时间)."""
        if not self.active:
            return {}
        now = (now or datetime.now()).replace(microsecond=0)
        end = min(now, self.end_at or now)
        reward = int(round(self.reward)) if result == "completed" else 0
        rec = {"id": self.live.get("id"), "start": self.live.get("start"), "end": _iso(end),
               "planned": self.live.get("end"), "terms": self.terms, "orig": self.orig,
               "level": self.level, "orig_level": self.orig_level, "cut": dict(self.cut),
               "result": result, "violations": self.violations, "reward": reward}
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

    def idle_left(self, now: datetime):
        """空闲还能持续多久 (秒); 不在空闲时 None."""
        since = _parse(self.live.get("idle_since")) if self.active else None
        if since is None:
            return None
        return max(0.0, IDLE_LIMIT - (now - since).total_seconds())

    def violate(self, tid: str):
        """违反了一次词条 tid: 记一次, 这个词条降一级. 返回 (是否达到了上限, 降级后的词条 / "" = 失效)."""
        if not self.active:
            return False, ""
        new = self.downgrade(tid)
        self.live["violations"] = self.violations + 1
        self.save()
        return self.violations >= self.violation_limit, new

    def downgrade(self, tid: str) -> str:
        """词条降一级: 系列词条换成低一档的, 只有一档的等级减一; 降到底就失效. 返回降级后的词条 ("" = 失效)."""
        terms = self.terms
        if tid not in terms:
            return ""
        old_share = self.net_share()
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

    def rest_factor(self) -> float:
        return min((REST[t] for t in self.terms if t in REST), default=1.0)

    def book_rest(self, seconds: float, end: datetime = None) -> float:
        """一段专注换来的休息时间 (秒, 已乘"休息时间倍率"). 返回现在就给的部分.
        合约中: 先乘疲惫的倍率; 按总等级多给的部分 (×倍率 - 1) 存进合约, 完成时才给.
        刚完成的合约里开始的专注 (完成后才结束): 直接按倍率给."""
        seconds = max(0.0, float(seconds))
        end = end or datetime.now()
        if self.active and self.start_at and self.start_at <= end:
            now = seconds * self.rest_factor()
            self.live["rest"] = round(self.rest + now, 1)
            self.save()
            return now
        return seconds

    def book_rest_after(self, seconds: float, start: datetime) -> float:
        """完成之后才结束的专注, 开始于刚完成的合约里: 按那个合约的疲惫和倍率给."""
        h = self.last
        hs, he = _parse(h.get("start")), _parse(h.get("end"))
        if h.get("result") == "completed" and hs and he and hs <= start < he:
            terms = h.get("terms") or []
            f = min((REST[t] for t in terms if t in REST), default=1.0)
            return seconds * f * rest_mult(int(h.get("level", level_of(terms))))
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

    # ---- 整点记录的锁定 ----

    def periods(self, now: datetime = None):
        out = []
        for h in self.history:
            a, b = _parse(h.get("start")), _parse(h.get("end"))
            if a and b:
                out.append((a, b))
        if self.active and self.start_at:
            out.append((self.start_at, now or datetime.now()))
        return out

    def hour_locked(self, slot_start: datetime, now: datetime = None) -> bool:
        """以 slot_start 开始的那一小时: 和合约有重叠, 并且已经进入了它之后的下一个时间段结束之后
        (也就是那一小时结束后再过一小时), 就不能再改 —— 合约结束了也一样."""
        now = now or datetime.now()
        slot_end = slot_start + timedelta(hours=1)
        if now < slot_end + timedelta(hours=1):
            return False
        return any(a < slot_end and slot_start < b for a, b in self.periods(now))

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
    assert level_of(["long3", "isolate", "last"]) == 8
    assert rest_minutes(0) == 20 and rest_minutes(3) == 24 and rest_minutes(4) == 28
    assert rest_minutes(18) == 44 and rest_minutes(19) == 50 and rest_mult(19) == 2.5
    assert conflicts("long1", "long3") and conflicts("isolate", "info2"), "同一组互相冲突"
    assert not conflicts("isolate", "chan1") and not conflicts("chan2", "isolate") and conflicts("suppress", "isolate")
    assert not conflicts("jam", "music")
    assert clean_terms(["long2", "long1", "x", "isolate", "suppress", "jam"]) == ["long2", "isolate", "jam"]
    assert clean_terms(["isolate", "chan2"]) == ["isolate", "chan2"], "信息隔绝和通道阻塞可以一起选"
    assert duration_of([]) == 4 * 3600 and duration_of(["long3"]) == int(4 * 3600 * 1.3)
    best = clean_terms(["long3", "move2", "info3", "suppress", "jam", "music", "fog", "tired3", "chan2", "last"])
    assert level_of(best) == 21, level_of(best)

    c = Contract(tempfile.mkdtemp())
    t0 = datetime(2026, 10, 18, 9, 30)
    c.start(["tired2", "info2", "chan1", "long1", "music"], owner="me", now=t0)
    assert c.active and c.level == 2 + 2 + 1 + 1 + 2 and c.end_at == t0 + timedelta(seconds=int(4 * 3600 * 1.1))
    assert c.overrides() == {} and not c.fog()
    # 信息阻断-II: 开始时剩 100 分钟, 只能用 50 分钟; 用超了 → 降成 -I, 多给 25 分钟
    assert c.net_check(600, 0, 6000, "2026-10-18") == ("", "")
    assert c.net_left(600) == 3000 and c.net_check(3599, 0, 0, "2026-10-18") == ("", "")
    assert c.net_check(3700, 0, 0, "2026-10-18") == ("info2", "over")
    over, new = c.violate("info2")
    assert not over and new == "info1" and c.terms == ["long1", "info1", "music", "tired2", "chan1"]
    assert c.net_left(3700) == 600 + 3000 + 1500 - 3700 and c.level == 7 and c.orig_level == 8
    assert c.net_check(3800, 0, 0, "2026-10-18") == ("", "")
    assert c.net_check(3800, 600, 0, "2026-10-18") == ("info1", "loan"), "借贷也算违反"
    assert c.net_check(3800, 600, 0, "2026-10-18") == ("", ""), "同一次借贷只算一次"
    assert c.downgrade("info1") == "" and not c.net_term() and c.net_check(9999, 9999, 0, "x") == ("", "")
    # 通道阻塞 / 乐理之灾: 只在刚越过的那一下算
    assert c.window_check(5) == "chan1" and c.window_check(6) == "" and c.window_check(3) == ""
    assert c.window_check(5) == "chan1"
    assert c.music_check(True) == "music" and c.music_check(True) == "" and c.music_check(False) == ""
    assert c.downgrade("music") == "music" and c.term_level("music") == 1 and c.level == 1 + 1 + 1 + 2
    assert c.downgrade("music") == "" and not c.has("music")
    assert c.quick_check() == ""
    # 休息: 疲惫 ×0.75; 完成时按降级后的等级给 (等级 4 → ×1.4)
    assert abs(c.book_rest(1000, t0 + timedelta(hours=1)) - 750) < 1e-6
    assert abs(c.reward - 750 * 0.4) < 1e-6
    # 空闲 30 分钟结束
    assert c.due(t0 + timedelta(hours=1), True) == ("", None)
    assert c.idle_left(t0 + timedelta(hours=1, minutes=10)) == 20 * 60
    assert c.due(t0 + timedelta(hours=1, minutes=31), True) == ("idle", t0 + timedelta(hours=1, minutes=30))
    assert c.due(t0 + timedelta(hours=1, minutes=31), False)[0] == "", "开始专注就不算空闲"
    # 整点记录: 9:00 那一小时和合约重叠, 11:00 起锁定; 8:00 那一小时不重叠
    assert not c.hour_locked(datetime(2026, 10, 18, 9), datetime(2026, 10, 18, 10, 59))
    assert c.hour_locked(datetime(2026, 10, 18, 9), datetime(2026, 10, 18, 11))
    assert not c.hour_locked(datetime(2026, 10, 18, 8), datetime(2026, 10, 18, 12))
    # 违反 3 次结束
    c.start(["suppress", "info3", "long1"], owner="me", now=t0)
    assert c.quick_check() == "suppress"
    assert c.violate("suppress") == (False, "suppress") and c.violate("suppress") == (False, "")
    assert c.violate("info3") == (True, "info2")
    rec = c.finish("failed", t0 + timedelta(hours=2))
    assert rec["reward"] == 0 and rec["orig_level"] == 6 and rec["level"] == 3 and not c.active
    assert not c.take_first_break()
    assert c.hour_locked(datetime(2026, 10, 18, 10), datetime(2026, 10, 18, 13)), "结束后照样锁定"
    assert not c.hour_locked(datetime(2026, 10, 18, 12), datetime(2026, 10, 18, 15))

    # 完成: 给出积攒的休息时间, 第一次休息的结转比例
    c.start(["last"], owner="me", now=t0)
    assert c.violation_limit == 1 and c.book_rest(600, t0) == 600 and abs(c.reward - 120) < 1e-6
    assert c.due(t0 + timedelta(hours=4), False) == ("completed", t0 + timedelta(hours=4))
    rec = c.finish("completed", t0 + timedelta(hours=4, minutes=3))
    assert rec["reward"] == 120 and rec["end"] == (t0 + timedelta(hours=4)).isoformat()
    assert c.book_rest_after(600, t0 + timedelta(hours=3)) == 720
    assert c.book_rest_after(600, t0 + timedelta(hours=5)) == 600
    assert c.take_first_break() and not c.take_first_break()
    assert abs(Contract.first_break_rate(0.8) - 0.9) < 1e-9

    # 最近结束的那个, 不是最近开始的那个
    c.start(["long1"], now=t0 - timedelta(days=1))
    c.finish("abandoned", t0 - timedelta(days=1) + timedelta(hours=1))
    assert c.last["result"] == "completed" and c.book_rest_after(600, t0 + timedelta(hours=3)) == 720

    # 同步: 记录按 id 合并
    d = Contract(tempfile.mkdtemp())
    d.merge(c.doc())
    assert len(d.history) == 3 and not d.active
    d.merge({"history": c.doc()["history"]})
    assert len(d.history) == 3
    c2 = Contract(os.path.dirname(c.path))
    assert len(c2.history) == 3, "写进文件, 重开后仍在"
    return "contract selftest OK"


if __name__ == "__main__":
    print(_selftest())
