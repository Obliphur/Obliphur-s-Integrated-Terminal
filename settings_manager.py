

from datetime import datetime, timedelta

import config
from i18n import tr
from config import DEFAULT_SETTINGS, load_json_file, save_json_file, settings_file


APPLY_DELAY = "delay"
APPLY_INSTANT = "instant"


APPLY_DELAY_HOURS = 48


DEFAULT_SINCE = "2024-01-01 00:00"

MINUTES = 60


def _fmt_min(sec: int) -> str:
    sec = int(sec)
    if sec % 3600 == 0:
        return f"{sec // 3600} h"
    return f"{sec / 60:g} min"


def _fmt_sec(sec: int) -> str:
    sec = int(sec)
    return f"{sec} s" if sec < 180 else _fmt_min(sec)


def chime_name(path) -> str:
    """自选提示音: 只显示文件名."""
    import os
    return "🎵 " + os.path.basename(str(path).strip().rstrip("\\/"))


def _num_text(x: float) -> str:
    """数字转成填写框里的文字: 去掉多余的 0, 最多 4 位小数."""
    return f"{round(float(x), 4):g}"


def _num(key: str, label: str, unit: str, scale: float, lo: float, hi: float,
         integer: bool, desc: str, page: str, short: str = None, group: str = None):
    """数值项: 显示在所属模块页的下半部分, 直接填写.

    unit/scale: 界面上按 unit 填写, 存储值 = 填写值 × scale (例如按分钟填, 按秒存).
    lo/hi: 允许范围, 按界面单位 (unit) 计.
    """
    def fmt(v, _scale=scale, _unit=unit):
        try:
            x = float(v) / _scale
        except (TypeError, ValueError):
            return str(v)
        return f"×{_num_text(x)}" if _unit == "×" else f"{_num_text(x)} {_unit}"
    return {
        "key": key, "label": label, "kind": "num", "apply": APPLY_DELAY,
        "page": page, "unit": unit, "scale": scale, "min": lo, "max": hi,
        "integer": integer, "fmt": fmt, "desc": desc, "short": short or label,
        "group": group,
    }


def _slot_text(value) -> str:
    """从 0:00 起的分钟数 → HH:MM."""
    try:
        m = int(round(float(value)))
    except (TypeError, ValueError):
        return str(value)
    m = max(0, min(24 * 60 - 1, m))
    return f"{m // 60:02d}:{m % 60:02d}"


def parse_slot(text):
    """填写框里的时间 → 分钟数. 认 "23:00" / "23：00" / "2300" / "23"; 认不出来时原样返回 (stage 会拒绝)."""
    t = str(text).strip().replace("：", ":").replace(" ", "")
    if not t:
        return t
    if ":" in t:
        head, _, tail = t.partition(":")
        if not (head.isdigit() and (tail == "" or tail.isdigit())):
            return str(text).strip()
        h, m = int(head), int(tail or 0)
    elif t.isdigit():
        h, m = (int(t), 0) if len(t) <= 2 else (int(t[:-2]), int(t[-2:]))
    else:
        return str(text).strip()
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return str(text).strip()
    return h * 60 + m


def _time(key: str, label: str, short: str, page: str = "daily"):
    """时间项: 按 HH:MM 填写, 存的是从 0:00 起的分钟数 (0 ~ 1439). 和数值项一样排在图格里."""
    return {
        "key": key, "label": label, "kind": "num", "apply": APPLY_DELAY,
        "page": page, "unit": "", "scale": 1, "min": 0, "max": 24 * 60 - 1,
        "integer": True, "fmt": lambda v: _slot_text(v), "desc": "",
        "short": short, "group": "sleep", "time": True,
    }


SETTINGS_SCHEMA = [
    _num("DAILY_LIMIT", "每日联网额度", "min", 60, 0, 1440, True,
         "每天最多能联网多久.", page="net"),
    _num("COOLDOWN", "联网冷静期", "min", 60, 0, 240, True,
         "提出申请后等多久才真的联网.想立刻联网请用快速访问.", page="net"),
    _num("ENFORCE_EVERY", "断网复查间隔", "s", 1, 5, 3600, True,
         "离线时每隔多久强制复查并重新断网.越小越难绕开,但更频繁调用系统命令.", page="net"),
    _num("TICK_CLAMP", "计时单步上限", "s", 1, 1, 300, True,
         "联网计时每一步最多计入多少秒;电脑睡眠或卡顿后醒来,不会一次计入一大段.", page="net"),
    _num("QUICK_MULTIPLIER", "快速访问消耗倍率", "×", 1, 1, 10, False,
         "快速访问免冷静期,每联网 1 分钟消耗多少分钟今日额度.", page="net"),
    _num("LOAN_RATE", "紧急借贷偿还倍率", "×", 1, 1, 10, False,
         "今天借 1 分钟额度,明天要扣多少分钟.", page="net"),
    _num("BREAK_COOLDOWN_RATE", "休息中冷静期倍率", "×", 1, 0, 1, False,
         "休息倒计时中提交的普通申请,若时长不超过剩余休息时间,冷静期乘以这个倍率.", page="net"),
    _num("BREAK_TIME_SCALE", "休息时间倍率", "×", 1, 0.1, 5, False,
         "计时器给出的建议休息时长乘以这个倍率;之前暂停休息结转的时间不受影响.", page="timer"),
    _num("BREAK_CARRY_RATE", "暂停休息结转比例", "×", 1, 0, 1, False,
         "暂停休息(或关闭程序)时,剩余休息时间按这个比例计入下一次休息.", page="timer"),
    _num("FILL_WINDOW", "整点记录补填时限", "min", 60, 1, 1440, True,
         "整点过后多久之内还能补填;超时自动记为「超时自动记录内容」.", page="log"),
    _num("WINDOW_LIMIT", "窗口数上限", "", 1, 1, 50, True,
         "任务栏上最多同时开着几个窗口;超过时一次随机关掉多出来的.", page="window"),
    # ---- 星期倍率 (联网页的第二块): 当天联网额度 = 每日联网额度 × 当天倍率 ----
    _num("QUOTA_MULT_MON", "周一联网倍率", "×", 1, 0, 10, False, "", page="net",
         group="weekday", short="周一"),
    _num("QUOTA_MULT_TUE", "周二联网倍率", "×", 1, 0, 10, False, "", page="net",
         group="weekday", short="周二"),
    _num("QUOTA_MULT_WED", "周三联网倍率", "×", 1, 0, 10, False, "", page="net",
         group="weekday", short="周三"),
    _num("QUOTA_MULT_THU", "周四联网倍率", "×", 1, 0, 10, False, "", page="net",
         group="weekday", short="周四"),
    _num("QUOTA_MULT_FRI", "周五联网倍率", "×", 1, 0, 10, False, "", page="net",
         group="weekday", short="周五"),
    _num("QUOTA_MULT_SAT", "周六联网倍率", "×", 1, 0, 10, False, "", page="net",
         group="weekday", short="周六"),
    _num("QUOTA_MULT_SUN", "周日联网倍率", "×", 1, 0, 10, False, "", page="net",
         group="weekday", short="周日"),
    # ---- 日常 (设置 → 日常): 睡觉时间, 周一到周日各自可以改 (MECHANICS 19) ----
    _time("SLEEP_MON", "周一睡觉时间", "周一"),
    _time("SLEEP_TUE", "周二睡觉时间", "周二"),
    _time("SLEEP_WED", "周三睡觉时间", "周三"),
    _time("SLEEP_THU", "周四睡觉时间", "周四"),
    _time("SLEEP_FRI", "周五睡觉时间", "周五"),
    _time("SLEEP_SAT", "周六睡觉时间", "周六"),
    _time("SLEEP_SUN", "周日睡觉时间", "周日"),
    {
        "key": "NET_MODE", "label": "断网方式",
        "kind": "choice", "apply": APPLY_DELAY,
        "choices": [
            ("adapter", "禁用网卡 (Adapter)"),
            ("proxy", "本地代理过滤 (Proxy)"),
            ("proxy_all", "代理-全屏蔽 (Proxy block-all)"),
        ],
        # 显示的就是选项的名字 (界面上再翻译)
        "fmt": lambda v: {"proxy": "本地代理过滤 (Proxy)", "proxy_all": "代理-全屏蔽 (Proxy block-all)"}.get(v, "禁用网卡 (Adapter)"),
        "desc": "Adapter:离线时直接禁用物理网卡.Proxy:网卡保持开启,由本地代理按 Rules 页的黑白名单放行 / 拦截.",
        "page": "net",
    },
    {
        "key": "FILTER_MODE", "label": "过滤名单模式",
        "kind": "choice", "apply": APPLY_DELAY,
        "choices": [
            ("blacklist", "黑名单模式"),
            ("whitelist", "白名单模式"),
        ],
        "fmt": lambda v: "Whitelist" if v == "whitelist" else "Blacklist",
        "desc": "仅 Proxy / 代理-全屏蔽 (Proxy block-all) 断网方式下有效 (全屏蔽时只看应用名单).黑名单模式:默认放行,只拦名单内.白名单模式:默认拦截,离线只放行名单内,联网会话中全放行.",
        "page": "rules",
    },
    {
        "key": "WINDOW_LIMIT_ENABLED", "label": "任务栏窗口数上限",
        "kind": "choice", "apply": APPLY_DELAY,
        "choices": [("1", "开启 (ON)"), ("0", "关闭 (OFF)")],
        "fmt": lambda v: "ON" if v else "OFF",
        "desc": "每 30 秒数一次任务栏上的窗口,超过「窗口数上限」就随机关掉多出来的.",
        "page": "window",
    },
    {
        "key": "WINDOW_FOCUS_ENABLED", "label": "窗口使用时间统计",
        "kind": "choice", "apply": APPLY_DELAY,
        "choices": [("1", "开启 (ON)"), ("0", "关闭 (OFF)")],
        "fmt": lambda v: "ON" if v else "OFF",
        "desc": "每 10 秒看一次当前在前台的程序,把这 10 秒记给它;桌面,文件资源管理器和命令行窗口记为空白.结果在 Statistics 页的「窗口」.",
        "page": "window",
    },
    {
        "key": "HOUR_LOG_ENABLED", "label": "整点记录开关",
        "kind": "choice", "apply": APPLY_DELAY,
        "choices": [("1", "开启 (ON)"), ("0", "关闭 (OFF)")],
        "fmt": lambda v: "ON" if v else "OFF",
        "desc": "程序启动时整点记录的初始状态;启动后仍可在 Hour Log 页直接切换.",
        "page": "log",
    },
    {
        "key": "AUTO_TEXT", "label": "超时自动记录内容",
        "kind": "instant", "apply": APPLY_INSTANT,
        "choices": [
            ("Sleeping", "Sleeping(睡觉)"),
            ("Idle", "Idle(发呆)"),
            ("Break", "Break(休息)"),
            ("Distracted", "Distracted(分心)"),
        ],
        "fmt": lambda v: str(v),
        "desc": "整点后一小时内没填写,就自动记成这段文字.这一项可以立即修改.",
        "page": "log",
    },
    {
        # 不管当前是哪种语言都显示 "Language": 看不懂当前语言的人也能找到这一项
        "key": "LANGUAGE", "label": "Language",
        "kind": "choice", "apply": APPLY_INSTANT,
        "choices": [("zh", "中文"), ("en", "English")],
        "fmt": lambda v: "中文" if str(v).startswith("zh") else "English",
        "desc": "即时生效.界面语言(中文 / English).",
    },
    {
        "key": "FONT_SCALE", "label": "界面字号",
        "kind": "choice", "apply": APPLY_INSTANT,
        "choices": [
            ("0.9", "偏小 (90%)"),
            ("1.0", "标准 (100%)"),
            ("1.15", "偏大 (115%)"),
            ("1.3", "很大 (130%)"),
            ("1.5", "极大 (150%)"),
        ],
        "fmt": lambda v: f"{float(v) * 100:.0f}%",
        "desc": "即时生效.整体缩放界面字号,适配不同分辨率.",
    },
    {
        "key": "THEME", "label": "界面主题",
        "kind": "choice", "apply": APPLY_INSTANT,
        "choices": [
            ("classic", "经典 (蓝白)"),
            ("claude", "Claude (浅色)"),
            ("claude_dark", "Claude (深色)"),
        ],
        "fmt": lambda v: {"claude": "Claude", "claude_dark": "Claude Dark"}.get(
            str(v), "Classic"),
        "desc": "即时生效.界面配色:经典蓝白,或参考 Claude 的米白 / 炭灰配色.",
    },
    {
        "key": "CONTRACT_THEME", "label": "合约页主题",
        "kind": "choice", "apply": APPLY_INSTANT,
        "choices": [("ops", "起源"), ("ui", "跟随界面主题")],
        "fmt": lambda v: "跟随界面主题" if str(v) == "ui" else "起源",
        "desc": "即时生效.合约页 (和合约页上的侧边栏) 的配色.",
        "page": "contract",
    },
    {
        "key": "REC_ASK", "label": "录像前询问",
        "kind": "choice", "apply": APPLY_INSTANT,
        "choices": [("1", "开启 (ON)"), ("0", "关闭 (OFF)")],
        "fmt": lambda v: "ON" if v else "OFF",
        "desc": "即时生效.开启时: 开始带录像词条 (目光 / 刻痕) 的合约前再确认一次; 别的设备开始的合约要本机录像时, 先问一次 (拒绝算一次违反).",
        "page": "record",
    },
    {
        "key": "UPDATE_CHECK", "label": "打开时检查更新",
        "kind": "choice", "apply": APPLY_INSTANT,
        "choices": [("1", "开启 (ON)"), ("0", "关闭 (OFF)")],
        "fmt": lambda v: "ON" if v else "OFF",
        "desc": "即时生效.每次打开程序时从 GitHub 检查有没有新版本.",
    },
    {
        "key": "UPDATE_AUTO", "label": "自动更新",
        "kind": "choice", "apply": APPLY_INSTANT,
        "choices": [("1", "开启 (ON)"), ("0", "关闭 (OFF)")],
        "fmt": lambda v: "ON" if v else "OFF",
        "desc": "即时生效.发现新版本时自动下载并安装, 重启后生效.",
    },
    {
        "key": "CHIME_WAV", "label": "整点提示音",
        "kind": "combo", "apply": APPLY_INSTANT,
        "choices": [
            ("__builtin__", "内置三音提示音 (推荐)"),
            ("", "Windows 蜂鸣声 (Beep)"),
        ],
        # 自选的音频只显示文件名, 不显示路径
        "fmt": lambda v: "内置三音提示音" if str(v).strip() in ("", "__builtin__") else (
            "Windows 蜂鸣声" if str(v) == "__beep__" else chime_name(v)),
        "desc": "即时生效.可粘贴本地 .wav / .mp3 的完整路径.",
        "page": "log",
    },
]

for _s in SETTINGS_SCHEMA:
    _s.setdefault("page", "basic")

# 设置页按模块分页, 顺序和左侧导航一致; 基本设置 (整个程序的外观) 永远是第一页.
# 每页上半部分是选项 / 开关, 下半部分是数值. 数值按 group 各排一格 (没有 group 的排第一格).
SETTINGS_PAGES = [
    ("basic", "基本设置"),
    ("timer", "计时器"),
    ("net", "联网"),
    ("rules", "规则"),
    ("log", "整点记录"),
    ("contract", "合约"),
    ("record", "录像"),
    ("window", "窗口"),
    ("daily", "日常"),
]

SCHEMA_BY_KEY = {s["key"]: s for s in SETTINGS_SCHEMA}
assert {s["page"] for s in SETTINGS_SCHEMA} <= {k for k, _ in SETTINGS_PAGES}


def page_specs(page: str):
    """(选项类, 数值类) 两组设置项, 各自按 SETTINGS_SCHEMA 里的顺序."""
    specs = [s for s in SETTINGS_SCHEMA if s["page"] == page]
    return ([s for s in specs if s["kind"] != "num"],
            [s for s in specs if s["kind"] == "num"])


DELAYED_KEYS = [s["key"] for s in SETTINGS_SCHEMA if s["apply"] == APPLY_DELAY]
INSTANT_KEYS = [s["key"] for s in SETTINGS_SCHEMA if s["apply"] == APPLY_INSTANT]


# 在"每日联网额度"里填这个数 (分钟) 并保存 = 开 / 关开发者模式. 两端相同 (Android: Settings.kt)
DEV_TOGGLE_CODE = 325799


def is_dev_toggle(key: str, value) -> bool:
    if key != "DAILY_LIMIT":
        return False
    try:
        return abs(float(value) - DEV_TOGGLE_CODE * SCHEMA_BY_KEY[key]["scale"]) < 1e-6
    except (TypeError, ValueError, KeyError):
        return False


class SettingsManager:

    def __init__(self, path: str = None, delay_hours: int = APPLY_DELAY_HOURS):
        self.path = path or settings_file()
        self.delay = timedelta(hours=delay_hours)
        self.effective = {}
        self.pending = {}
        self.since = {}
        self.changed = {}
        self.free_next = set()   # 下一次修改免等待的项 (紧急借贷借得太多时给每日联网额度)
        self.setup_until = None  # 配置期截止时刻 (第一次打开软件时开始)
        import os as _os
        self.fresh = not _os.path.exists(self.path)   # 第一次打开软件 (还没有设置文件)
        self.load()
        self.promote_due()
        self.apply_to_config()


    def load(self):
        raw = load_json_file(self.path, {})
        if not isinstance(raw, dict):
            raw = {}
        eff = raw.get("effective") if isinstance(raw.get("effective"), dict) else {}
        pen = raw.get("pending") if isinstance(raw.get("pending"), dict) else {}
        since = raw.get("since") if isinstance(raw.get("since"), dict) else {}
        changed = raw.get("changed") if isinstance(raw.get("changed"), dict) else {}

        for s in SETTINGS_SCHEMA:
            k = s["key"]
            if k in eff:
                self.effective[k] = self._coerce(k, eff[k])
            else:
                self.effective[k] = self._coerce(k, DEFAULT_SETTINGS.get(k))
            self.since[k] = str(since.get(k) or DEFAULT_SINCE)
            self.changed[k] = bool(changed.get(k))

        self.setup_until = self._parse_dt(raw.get("setup_until"))
        free = raw.get("free_next") if isinstance(raw.get("free_next"), list) else []
        self.free_next = {k for k in free if k in SCHEMA_BY_KEY}

        for k, item in pen.items():
            if k not in SCHEMA_BY_KEY or not isinstance(item, dict):
                continue
            at = self._parse_dt(item.get("apply_at"))
            if at is None:
                continue
            self.pending[k] = {
                "value": self._coerce(k, item.get("value")),
                "requested_at": str(item.get("requested_at") or ""),
                "apply_at": at,
            }

    def save(self):
        payload = {
            "effective": self.effective,
            "since": self.since,
            "changed": dict(self.changed),
            "free_next": sorted(self.free_next),
            "setup_until": (self.setup_until.isoformat(timespec="seconds")
                            if self.setup_until else None),
            "pending": {
                k: {
                    "value": v["value"],
                    "requested_at": v["requested_at"],
                    "apply_at": v["apply_at"].isoformat(timespec="seconds"),
                }
                for k, v in self.pending.items()
            },
        }
        return save_json_file(self.path, payload)


    @staticmethod
    def _coerce(key: str, value):
        spec = SCHEMA_BY_KEY.get(key)
        if spec is None:
            return value
        if spec["kind"] in ("text", "combo", "instant"):
            return "" if value is None else str(value).strip()
        if spec["kind"] == "num":
            if isinstance(value, bool):
                value = int(value)
            try:
                x = float(str(value).strip())
            except (TypeError, ValueError):
                return value
            if x != x or x in (float("inf"), float("-inf")):
                return value
            return int(round(x)) if spec.get("integer") else x
        if spec["kind"] == "choice":

            if isinstance(value, bool):
                return int(value)
            text = str(value).strip()
            try:
                return int(text)
            except (TypeError, ValueError):
                pass
            try:
                return float(text)
            except (TypeError, ValueError):
                return value
        return value

    @staticmethod
    def _parse_dt(text):
        if not text:
            return None
        try:
            return datetime.fromisoformat(str(text))
        except ValueError:
            return None

    @staticmethod
    def _now() -> datetime:
        return datetime.now()


    def value(self, key: str):
        return self.effective.get(key, DEFAULT_SETTINGS.get(key))

    def staged(self, key: str):
        return self.pending.get(key)

    def is_staged(self, key: str) -> bool:
        return key in self.pending

    def ever_changed(self, key: str) -> bool:
        return bool(self.changed.get(key))


    def staged_count(self) -> int:
        return len(self.pending)

    def apply_at(self, key: str):
        item = self.pending.get(key)
        return item["apply_at"] if item else None

    def apply_at_text(self, key: str) -> str:
        at = self.apply_at(key)
        if at is None:
            return ""
        left = at - self._now()
        secs = int(left.total_seconds())
        if secs <= 0:
            return tr("立即生效中…")
        d, rem = divmod(secs, 86400)
        h, rem = divmod(rem, 3600)
        m = rem // 60
        if d:
            return tr("{at} 生效 (还有 {d} 天 {h} 小时)").format(
                at=f"{at:%Y-%m-%d %H:%M}", d=d, h=h)
        if h:
            return tr("{at} 生效 (还有 {h} 小时 {m} 分)").format(
                at=f"{at:%H:%M}", h=h, m=m)
        return tr("{at} 生效 (还有 {m} 分)").format(at=f"{at:%H:%M}", m=m)

    def pending_left_seconds(self, key: str) -> int:
        at = self.apply_at(key)
        if at is None:
            return 0
        return max(0, int((at - self._now()).total_seconds()))


    def stage(self, key: str, value):
        spec = SCHEMA_BY_KEY.get(key)
        if spec is None:
            return "unknown", tr("未知配置项 {key}", key=key)
        if is_dev_toggle(key, value):
            # 不是真的修改: 不排队, 不写文件, 不记流水. 调用方负责让网络跟着切换
            config.toggle_dev_mode()
            return "dev", ""
        value = self._coerce(key, value)
        ok, msg = self.validate(key, value)
        if not ok:
            return "invalid", msg
        if spec["apply"] == APPLY_INSTANT or config.changes_instant():
            self.effective[key] = value
            self.since[key] = f"{self._now():%Y-%m-%d %H:%M}"
            self.changed[key] = True
            self.pending.pop(key, None)
            self.save()
            self.apply_to_config()
            return "instant", tr("{label} 已立即生效.", label=tr(spec['label']))

        if value == self.value(key):

            had = self.pending.pop(key, None) is not None
            if had:
                self.save()
                return "staged", tr("{label} 的待生效修改已取消.",
                                    label=tr(spec['label']))
            return "nochange", tr("{label} 与当前生效值相同.", label=tr(spec['label']))

        now = self._now()
        if key in self.free_next:
            # 一次性的免等待 (紧急借贷借掉明天额度一半以上时给的), 用掉就没了
            self.free_next.discard(key)
            self.effective[key] = value
            self.since[key] = f"{now:%Y-%m-%d %H:%M}"
            self.changed[key] = True
            self.pending.pop(key, None)
            self.save()
            self.apply_to_config()
            return "instant", tr("{label} 已立即生效.",
                                 label=tr(spec['label']))

        self.pending[key] = {
            "value": value,
            "requested_at": f"{now:%Y-%m-%d %H:%M}",
            "apply_at": now + self.delay,
        }
        self.save()
        return "staged", tr("{label} 已提交,将于 {at}.",
                            label=tr(spec['label']), at=self.staged_apply_text(key))

    def validate(self, key: str, value):
        """数值项: 必须是数字且在允许范围内. 时间项: 必须是 0 ~ 1439 的分钟数. 其他项总是通过."""
        spec = SCHEMA_BY_KEY.get(key) or {}
        if spec.get("kind") != "num":
            return True, ""
        label = tr(spec["label"])
        if spec.get("time"):
            if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value <= 24 * 60 - 1):
                return False, tr("{label}: 请按 HH:MM 填写 (00:00 ~ 23:59).", label=label)
            return True, ""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, tr("{label}: 请填写数字.", label=label)
        x = value / spec["scale"]
        lo, hi = spec["min"], spec["max"]
        if x < lo - 1e-9 or x > hi + 1e-9:
            unit = spec["unit"]
            rng = (f"{_num_text(lo)} ~ {_num_text(hi)}" if unit in ("×", "")
                   else f"{_num_text(lo)} ~ {_num_text(hi)} {unit}")
            return False, tr("{label} 允许的范围是 {rng}.", label=label, rng=rng)
        return True, ""

    # ---- 界面单位 <-> 存储值 (数值项) ----

    def ui_number(self, key: str, value=None) -> str:
        spec = SCHEMA_BY_KEY[key]
        v = self.value(key) if value is None else value
        if spec.get("time"):
            return _slot_text(v)
        try:
            return _num_text(float(v) / spec["scale"])
        except (TypeError, ValueError):
            return str(v)

    def from_ui_number(self, key: str, text: str):
        """把填写框里的文字换成存储值; 不是数字时原样返回 (stage 会拒绝)."""
        spec = SCHEMA_BY_KEY[key]
        if spec.get("time"):
            return parse_slot(text)
        try:
            x = float(str(text).strip())
        except (TypeError, ValueError):
            return str(text).strip()
        return self._coerce(key, x * spec["scale"])

    def grant_free_change(self, key: str) -> bool:
        """给某一项一次"下一次修改免等待". 已经有了就不重复给, 返回是否新给了."""
        if key not in SCHEMA_BY_KEY or key in self.free_next:
            return False
        self.free_next.add(key)
        self.save()
        return True

    def make_due_now(self, key: str) -> bool:
        """这一项排队中的修改现在就到期 (下一次 promote_due 生效). 没有排队中的修改时返回 False."""
        item = self.pending.get(key)
        if not item:
            return False
        item["apply_at"] = self._now()
        self.save()
        return True

    def wait_tag(self, key: str) -> str:
        """修改这一项是否需要等待: 'instant' 即时生效 / 'setup' 配置期内即时 /
        'free' 这一次免等待 / '' 需要等待 (或开发者模式, 不显示)."""
        spec = SCHEMA_BY_KEY.get(key) or {}
        if spec.get("apply") == APPLY_INSTANT:
            return "instant"
        if config.dev_mode_enabled():
            return ""                   # 开发者模式在界面上不显示
        if config.setup_active():
            return "setup"
        if key in self.free_next:
            return "free"
        return ""

    def staged_apply_text(self, key: str) -> str:
        at = self.apply_at(key)
        return f"{at:%Y-%m-%d %H:%M}" if at else ""

    def cancel(self, key: str) -> bool:
        if self.pending.pop(key, None) is None:
            return False
        self.save()
        return True

    def cancel_all(self) -> int:
        n = len(self.pending)
        if n:
            self.pending.clear()
            self.save()
        return n


    def promote_due(self) -> list:
        now = self._now()
        dev = config.changes_instant()
        due = [k for k, v in self.pending.items() if dev or v["apply_at"] <= now]
        for k in due:
            self.effective[k] = self.pending[k]["value"]
            self.since[k] = f"{now:%Y-%m-%d %H:%M}"
            del self.pending[k]
        if due:
            self.save()
            self.apply_to_config()
        return due

    def seconds_until_next(self):
        if not self.pending:
            return None
        return min(self.pending_left_seconds(k) for k in self.pending)


    def config_value(self, key: str):
        value = self.value(key)
        spec = SCHEMA_BY_KEY.get(key) or {}
        if spec.get("kind") == "num":
            ok, _ = self.validate(key, value)
            if not ok:
                # 文件被手动改坏时退回默认值, 不让程序带着离谱的数值运行.
                value = self._coerce(key, DEFAULT_SETTINGS.get(key))
            if key == "FILL_WINDOW":
                return timedelta(seconds=int(value))
            return value
        if spec.get("kind") == "choice":
            for raw, _label in spec.get("choices", []):
                if str(raw) == str(value):
                    if key == "FONT_SCALE":
                        return float(raw)
                    try:
                        return int(raw)
                    except (TypeError, ValueError):
                        return raw
        if key == "HOUR_LOG_ENABLED":
            return int(bool(value))
        if key == "FONT_SCALE":
            try:
                return max(0.75, min(2.0, float(value)))
            except (TypeError, ValueError):
                return 1.0
        if key == "THEME":
            return str(value) if str(value) in config.THEMES else config.DEFAULT_THEME
        if key in ("LANGUAGE", "AUTO_TEXT"):
            return str(value)
        if key == "CHIME_WAV":
            return "" if str(value).strip() in ("", "__builtin__") else str(value)
        return value

    def apply_to_config(self):
        over = getattr(config, "CONTRACT_OVERRIDES", None) or {}
        for key in self.effective:
            setattr(config, key, over[key] if key in over else self.config_value(key))
        config.SETUP_UNTIL = self.setup_until

    # ---- 配置期 ----

    def start_setup(self, hours: float = None):
        hours = config.SETUP_HOURS if hours is None else hours
        self.setup_until = self._now() + timedelta(hours=hours)
        self.save()
        self.apply_to_config()

    def end_setup(self):
        self.setup_until = None
        self.save()
        self.apply_to_config()

    def in_setup(self) -> bool:
        return self.setup_until is not None and self._now() < self.setup_until

    def setup_left_seconds(self) -> int:
        if not self.in_setup():
            return 0
        return int((self.setup_until - self._now()).total_seconds())


    def raw_ui_value(self, key: str) -> str:
        spec = SCHEMA_BY_KEY.get(key) or {}
        if spec.get("kind") in ("choice", "combo"):
            label = self.label_for(key, self.value(key))
            return label if label is not None else self.display(key)
        return str(self.value(key))

    def display(self, key: str) -> str:
        spec = SCHEMA_BY_KEY.get(key)
        if spec is None:
            return str(self.value(key))
        try:
            return spec["fmt"](self.value(key))
        except Exception:
            return str(self.value(key))

    def display_value(self, key: str, value) -> str:
        spec = SCHEMA_BY_KEY.get(key)
        if spec is None:
            return str(value)
        try:
            return spec["fmt"](value)
        except Exception:
            return str(value)

    def choices(self, key: str):
        spec = SCHEMA_BY_KEY.get(key) or {}
        return list(spec.get("choices") or [])

    def labeled_values(self, key: str, translate: bool = False):
        out = []
        for raw, label in self.choices(key):
            out.append((self._coerce(key, raw), tr(label) if translate else label))
        return out

    def label_for(self, key: str, value, translate: bool = False):
        for raw, label in self.choices(key):
            if self._coerce(key, raw) == self._coerce(key, value):
                return tr(label) if translate else label
        return None



def _selftest():
    import os
    import tempfile
    tmp = os.path.join(tempfile.mkdtemp(), "settings.json")
    m = SettingsManager(tmp, delay_hours=48)
    assert m.fresh, "还没有设置文件 = 第一次打开"
    assert m.value("DAILY_LIMIT") == DEFAULT_SETTINGS["DAILY_LIMIT"]
    assert not m.is_staged("DAILY_LIMIT") and not m.in_setup()

    # 没有"首次修改免等待"了: 第一次改也要等
    st, _ = m.stage("DAILY_LIMIT", 3600)
    assert st == "staged" and m.value("DAILY_LIMIT") == DEFAULT_SETTINGS["DAILY_LIMIT"]
    assert 47 * 3600 < m.pending_left_seconds("DAILY_LIMIT") <= 48 * 3600
    assert m.wait_tag("DAILY_LIMIT") == ""
    # 填 325799 = 开 / 关开发者模式: 不排队, 不改生效值, 排队中的修改原样保留
    before = (dict(m.pending), dict(m.effective))
    assert m.stage("DAILY_LIMIT", m.from_ui_number("DAILY_LIMIT", "325799")) == ("dev", "")
    assert config.dev_mode_enabled() and (dict(m.pending), dict(m.effective)) == before
    assert m.stage("DAILY_LIMIT", DEV_TOGGLE_CODE * 60)[0] == "dev" and not config.dev_mode_enabled()
    assert m.stage("COOLDOWN", DEV_TOGGLE_CODE * 60)[0] != "dev", "只认每日联网额度"

    st, _ = m.stage("CHIME_WAV", "__builtin__")
    assert st == "instant" and not m.is_staged("CHIME_WAV")
    assert config.CHIME_WAV == ""

    # 配置期: 改动立即生效, 排队中的也立即生效
    m.start_setup(72)
    assert m.in_setup() and config.setup_active() and m.wait_tag("COOLDOWN") == "setup"
    assert 71 * 3600 < m.setup_left_seconds() <= 72 * 3600
    assert m.promote_due() == ["DAILY_LIMIT"] and config.DAILY_LIMIT == 3600
    st, _ = m.stage("COOLDOWN", 120)
    assert st == "instant" and config.COOLDOWN == 120
    m2 = SettingsManager(tmp, delay_hours=48)
    assert not m2.fresh and m2.in_setup(), "配置期写进文件, 重启后仍在"
    m2.end_setup()
    assert not m2.in_setup() and not config.setup_active()
    st, _ = m2.stage("COOLDOWN", 600)
    assert st == "staged" and m2.value("COOLDOWN") == 120
    m2.pending["COOLDOWN"]["apply_at"] = m2._now() - timedelta(seconds=1)
    assert m2.promote_due() == ["COOLDOWN"]
    assert m2.value("COOLDOWN") == 600 and config.COOLDOWN == 600

    m2.stage("FILTER_MODE", "whitelist")
    m3 = SettingsManager(tmp, delay_hours=48)
    assert m3.is_staged("FILTER_MODE") and m3.value("FILTER_MODE") == DEFAULT_SETTINGS["FILTER_MODE"]
    assert m3.cancel_all() == 1

    # 配置期过期
    m3.setup_until = m3._now() - timedelta(seconds=1)
    m3.apply_to_config()
    assert not m3.in_setup() and not config.setup_active()

    # 睡觉时间 (设置 → 日常): 按 HH:MM 填, 存的是分钟数, 默认 23:00 (MECHANICS 19)
    m4 = SettingsManager(os.path.join(tempfile.mkdtemp(), "settings.json"), delay_hours=48)
    assert m4.ui_number("SLEEP_MON") == "23:00" and m4.value("SLEEP_SUN") == 23 * 60
    assert m4.from_ui_number("SLEEP_MON", "22:30") == 22 * 60 + 30
    assert m4.from_ui_number("SLEEP_MON", "7") == 7 * 60
    assert m4.from_ui_number("SLEEP_MON", "2300") == 23 * 60
    assert m4.from_ui_number("SLEEP_TUE", "晚上") == "晚上"
    assert m4.stage("SLEEP_TUE", "晚上")[0] == "invalid"
    assert m4.stage("SLEEP_TUE", m4.from_ui_number("SLEEP_TUE", "24:00"))[0] == "invalid"
    assert m4.stage("SLEEP_TUE", m4.from_ui_number("SLEEP_TUE", "23:00"))[0] == "nochange"
    assert page_specs("daily")[1][0]["key"] == "SLEEP_MON" and len(page_specs("daily")[1]) == 7
    m4.start_setup(1)                       # 配置期内立即生效
    assert m4.stage("SLEEP_MON", m4.from_ui_number("SLEEP_MON", "22:00"))[0] == "instant"
    assert config.SLEEP_MON == 22 * 60 and config.sleep_minutes(datetime(2026, 10, 19)) == 22 * 60
    m4.end_setup()
    return "settings_manager selftest OK"


if __name__ == "__main__":
    print(_selftest())
