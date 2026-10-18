"""
成就 (MECHANICS.md 第 17 节): 条件判定和存档. 界面在 pages/achievements_page.py.

存档 achievements.json: 第一次启动的时刻, 达成了哪些 (什么时候), 还没被关掉的达成提示.
不显示进度; 触发条件只写在这里, 不显示在界面上.
"""

import os
import json
from datetime import datetime, timedelta

import config
from data_manager import split_items


# (id, 名字, 达成后显示的文字, 是否隐藏, 作者留下的文字)
# 名字是英文的不翻译 (中文界面和英文界面都一样).
ACHIEVEMENTS = [
    ("awake", "苏醒", "Hello World", False, ""),
    ("day1", "新晋主管", "你已经度过了与OIT相伴的第一天", False, ""),
    ("setup_done", "正式主管", "你完成了试用期", False, ""),
    ("day7", "第七天", "你已经在专注条件下完成了一周的工作.", False, ""),
    ("day30", "优秀主管", "你在OIT的陪伴下完成了一个月的工作", False, ""),
    ("day100", "百日的思索与决策", "一百天以来,您不断战斗着", False, ""),
    ("day365", "一年的鏖战与守望", "一年以来,您的战斗不曾终止,也将继续.", False, ""),
    ("study4", "专注", "你第一次在一天内学习了4h.", False, ""),
    ("study8", "高效专注", "你在一天内学习了8h,是一个相对比较长的时间.", False, ""),
    ("study12", "overload", "你在一天内学习了12h.这真的是人类可以做到的吗?", True, ""),
    ("sleep10", "冬眠", "睡了10h以上...", False, ""),
    ("summit", "登顶", "我们是冠军!", True, ""),
]
BY_ID = {a[0]: a for a in ACHIEVEMENTS}
SLEEP_WORD = "sleeping"
SUMMIT_LEVEL = 25          # "登顶": 完成危机等级 (结束时) 不低于这个的合约
LOG_FILES = ("settings_changes.log", "net_requests.log", "window_guard.log")


def is_english_name(name: str) -> bool:
    return all(ord(ch) < 128 for ch in name)


class Facts:
    """判定用的数据. study: {日期: 小时} (所有设备合计); hours: {日期: {小时: 内容}};
    contracts: 结束了的合约 (contract.py 的 history)."""

    def __init__(self, now: datetime, first: datetime, in_setup: bool, study: dict, hours: dict,
                 contracts=None):
        self.now, self.first, self.in_setup = now, first, in_setup
        self.study, self.hours = study, hours
        self.contracts = contracts or []


def sleep_hours(day: dict) -> float:
    """一天的整点记录里 Sleeping 一共占了几小时 (一小时多项活动时均分, 不分大小写)."""
    total = 0.0
    for text in (day or {}).values():
        items = split_items(text)
        if items:
            total += sum(1 for it in items if it.casefold() == SLEEP_WORD) / len(items)
    return total


def _since(days: int):
    """按日期算, 在 00:00 达成: 第一次启动那天之后的第 days 个 00:00."""
    return lambda f: (f.now.date() - f.first.date()).days >= days


def _study(hours: float):
    return lambda f: any(float(v or 0) >= hours - 1e-6 for v in f.study.values())


def _sleep(hours: float):
    return lambda f: any(sleep_hours(day) >= hours - 1e-6 for day in f.hours.values())


CHECKS = {
    "awake": lambda f: True,
    "day1": _since(1),
    "setup_done": lambda f: not f.in_setup,
    "day7": _since(7),
    "day30": _since(30),
    "day100": _since(100),
    "day365": _since(365),
    "study4": _study(4),
    "study8": _study(8),
    "study12": _study(12),
    "sleep10": _sleep(10),
    # 完成了危机等级 20 及以上的合约 (按结算时的等级, MECHANICS 18)
    "summit": lambda f: any(h.get("result") == "completed" and int(h.get("level") or 0) >= SUMMIT_LEVEL for h in f.contracts),
}


def _parse(text):
    try:
        return datetime.fromisoformat(str(text)) if text else None
    except ValueError:
        return None


def guess_first_launch(data_dir: str, setup_until, day_keys, now: datetime = None) -> datetime:
    """第一次有成就存档时 (新安装, 或者从没有成就的旧版本升级上来) 估计第一次启动的时刻:
    配置期的开始, 只追加的流水里最早的一行, 最早一天的记录, 取最早的."""
    now = now or datetime.now()
    out = [now]
    if setup_until:
        out.append(setup_until - timedelta(hours=float(getattr(config, "SETUP_HOURS", 72))))
    for name in LOG_FILES:
        try:
            with open(os.path.join(data_dir, name), "r", encoding="utf-8") as f:
                line = f.readline()
            out.append(datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S"))
        except (OSError, ValueError):
            pass
    for ds in day_keys or ():
        try:
            out.append(datetime.strptime(str(ds)[:10], "%Y-%m-%d"))
        except ValueError:
            pass
    return min(out)


class AchievementStore:
    def __init__(self, data_dir: str, guess_first=None):
        self.path = os.path.join(data_dir, "achievements.json")
        self.first: datetime = None
        self.unlocked = {}            # id -> 达成时刻 (iso)
        self.unseen = []              # 达成提示还没被关掉的 (按达成顺序)
        self.blocked = lambda: False  # 重置中: 不再写文件
        fresh = not os.path.exists(self.path)
        self._load()
        if fresh or self.first is None:
            self.first = guess_first() if guess_first else datetime.now()
            self.save()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(d, dict):
            return
        self.first = _parse(d.get("first_launch"))
        self.unlocked = {k: str(v) for k, v in (d.get("unlocked") or {}).items()
                         if k in BY_ID and _parse(v)}
        self.unseen = [k for k in (d.get("unseen") or []) if k in self.unlocked]

    def save(self):
        if self.blocked():
            return
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"first_launch": self.first.isoformat(timespec="seconds"),
                           "unlocked": self.unlocked, "unseen": self.unseen},
                          f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def at(self, aid: str):
        return _parse(self.unlocked.get(aid))

    def check(self, facts: Facts) -> list:
        """按现在的数据判定一遍, 返回这次新达成的 id."""
        new = []
        for aid, *_ in ACHIEVEMENTS:
            if aid in self.unlocked:
                continue
            try:
                ok = CHECKS[aid](facts)
            except Exception:
                ok = False
            if ok:
                self.unlocked[aid] = facts.now.isoformat(timespec="seconds")
                self.unseen.append(aid)
                new.append(aid)
        if new:
            self.save()
        return new

    def merge(self, changes: dict) -> list:
        """设备同步: 别的设备达成的也算本机达成 (达成时刻取最早的). 返回本机新达成的 id.
        同步来的"删除"不理会: 达成了就不会再取消."""
        new = []
        changed = False
        for aid, val in (changes or {}).items():
            t = _parse(val)
            if aid not in BY_ID or t is None:
                continue
            cur = self.at(aid)
            if cur is None:
                self.unlocked[aid] = t.isoformat(timespec="seconds")
                self.unseen.append(aid)
                new.append(aid)
                changed = True
            elif t < cur:
                self.unlocked[aid] = t.isoformat(timespec="seconds")
                changed = True
        if changed:
            self.save()
        return new

    def mark_seen(self, aid: str):
        if aid in self.unseen:
            self.unseen.remove(aid)
            self.save()

    def doc(self) -> dict:
        """同步用的文档: {id: 达成时刻}."""
        return dict(self.unlocked)


def _selftest():
    import tempfile
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "settings_changes.log"), "w", encoding="utf-8") as f:
        f.write("2026-01-02 03:04:05  SETUP period ended by user\n")
    now = datetime(2026, 1, 20, 12, 0, 0)
    first = guess_first_launch(d, None, ["2026-01-05"], now)
    assert first == datetime(2026, 1, 2, 3, 4, 5), first
    s = AchievementStore(d, lambda: first)
    hours = {"2026-01-10": {str(h): "Sleeping" for h in range(9)},
             "2026-01-11": {**{str(h): "sleeping" for h in range(9)}, "9": "Sleeping/学习", "10": "SLEEPING/吃饭"}}
    got = s.check(Facts(now, first, False, {"2026-01-03": 4.2}, hours))
    assert set(got) == {"awake", "day1", "setup_done", "day7", "study4", "sleep10"}, got
    assert s.check(Facts(now, first, False, {}, {})) == []
    assert sleep_hours(hours["2026-01-10"]) == 9
    s2 = AchievementStore(d)
    assert s2.first == first and set(s2.unlocked) == set(got) and s2.unseen == got
    assert s2.merge({"study8": "2026-01-05T10:00:00", "awake": "2025-12-31T00:00:00",
                     "nope": "2026-01-01T00:00:00", "day30": None}) == ["study8"]
    assert s2.at("awake") == datetime(2025, 12, 31)
    late = datetime(2026, 3, 1, 23, 59)
    f = Facts(datetime(2026, 3, 2, 0, 0, 1), late, True, {}, {})
    assert CHECKS["day1"](f) and not CHECKS["day7"](f)
    assert not CHECKS["day1"](Facts(datetime(2026, 3, 1, 23, 59, 59), datetime(2026, 3, 1, 0, 0), True, {}, {}))
    s2.mark_seen("awake")
    assert "awake" not in AchievementStore(d).unseen
    assert is_english_name("overload") and not is_english_name("苏醒")
    # 登顶: 完成危机等级 25 及以上的合约
    f24 = Facts(datetime(2026, 1, 1), datetime(2026, 1, 1), False, {}, {},
                [{"result": "completed", "level": 24}, {"result": "failed", "level": 26}])
    assert not CHECKS["summit"](f24)
    f25 = Facts(datetime(2026, 1, 1), datetime(2026, 1, 1), False, {}, {}, [{"result": "completed", "level": 25}])
    assert CHECKS["summit"](f25)
    print("achievements selftest ok")


if __name__ == "__main__":
    _selftest()
