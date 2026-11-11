"""指令的常量与无状态小工具: 池子 / 时限 / 蓝色代码 / 时间格式 (MECHANICS 第 22 节)。

从 prescript.py 拆出来的公共部分; 依赖方向: prescript.py 和 prescript 下的 mixin 都导入它,
它从不导入 prescript.py.
"""

import re
from datetime import datetime, timedelta


TYPES = ("I", "II", "III", "IV", "V", "VI")      # 池子里的六列; V 是空闲词条 (见 IDLE_ENTRIES)

# 每个池子里六条指令的概率: I, II, III, IV, V, VI
POOLS = {
    "focus": (0.8, 0.03, 0.03, 0.10, 0.00, 0.04),
    "break": (0.0, 0.10, 0.70, 0.10, 0.05, 0.05),
    "idle":  (0.05, 0.01, 0.01, 0.02, 0.90, 0.01),
}

# 领取后多久作废 (秒); IV 在这一天结束时作废, 由日期换日处理. 空闲词条 (IDLE_ENTRIES) 没有时限.
EXPIRY_SECONDS = {
    "I": 60 * 60,
    "II": 8 * 60 * 60,
    "III": 60 * 60,
    "VI": 60 * 60,
}

# 空闲池里 V 那 0.9 由这几个词条分; 每个词条: 正文 / 自动完成要几分钟 (0 = 没有计时) / 蓝色代码的词.
# 设备时间的哪几段里它的概率被抬高在设置 → 日常 里改 (config.PRESCRIPT_WIN_*, 默认值在 config.PRESCRIPT_WIN_DEFAULTS).
# t = 分钟数 × 0.02 (5 分钟 0.1); X 没有计时, t 固定 0.1.
IDLE_ENTRIES = {
    "V": {"body": "立即停下任何活动,接下来的5min什么也不做.", "minutes": 5, "word": "STOP"},
    "VII": {"body": "到开阔场地进行运动,持续20min.", "minutes": 20, "word": "EXERCISE"},
    "VIII": {"body": "进行一次淋浴.", "minutes": 10, "word": "SHOWER"},
    "IX": {"body": "凝视天空.", "minutes": 5, "word": "GAZE"},
    "X": {"body": "记录,核对并反思这一天的活动.", "minutes": 0, "word": "RECORD"},
    "XI": {"body": "打扫并整理房间.", "minutes": 15, "word": "CLEAN"},
}
IDLE_KINDS = tuple(IDLE_ENTRIES)
ALL_TYPES = TYPES + tuple(k for k in IDLE_KINDS if k not in TYPES)
IDLE_BOOST = 0.8                # 时间段对得上的词条 (几个就均分) 拿走这一格概率的 80%, 其余词条平分剩下的
FOCUS_CHANCE_START = 0.25       # 每次空闲池抽取之后, 下一次改抽专注池的概率; 没触发 +25%, 触发后回到 25%
FOCUS_CHANCE_STEP = 0.25
LOG_KEEP_DAYS = 30
PAGE_EVENT_FRESH = 180          # 其他设备进入 / 退出指令页的事件, 多少秒之内收到才跟着做 (太旧的是以前留下的, 不理)

FIXED_T = {"I": 1.0, "III": 1.0, "IV": 1.0, "VI": 1.0}
FIXED_T.update({k: (0.1 if not e["minutes"] else round(e["minutes"] * 0.02, 3))
                for k, e in IDLE_ENTRIES.items()})
FOCUS_TARGET = 50 * 60          # I: 50 分钟专注
BREAK_TARGET = 20 * 60          # III (旧存档里没有分钟数的): 20 分钟休息
REST_SHARE = 0.5                # III: 要休息的分钟数 = 用户剩余休息时间的 50%
REST_MAX_MIN = 30               # III: 最多 30 分钟
REFLECT_TARGET = 3 * 60         # VI: 统计页连续停留 3 分钟
DAILY_T_TARGET = 10.0           # 今天完成的指令 t 合计到 10
DAILY_BONUS_SECONDS = 20 * 60   # 额外 20 分钟休息
DAWN_HOUR = 4                   # 每天凌晨 4:00 起, 自动得到 DAWN_SECONDS 的休息时间 (一天一次, 不用打开指令页)
DAWN_SECONDS = 20 * 60

# 每条指令的蓝色代码用的词 (补位后正好 8 个字符以内)
TYPE_WORDS = {"I": "FOCUS", "II": "CONTRACT", "III": "REST", "IV": "DAILY", "VI": "REFLECT"}
TYPE_WORDS.update({k: e["word"] for k, e in IDLE_ENTRIES.items()})
CODE_WIDTH = 8                  # 蓝色代码的宽度 (和网页上的指令代码一样, 8 个字符)
CODE_LEFT = "_%"                # 左边补位, 从最外面一格开始依次用
CODE_RIGHT = "%."               # 右边补位, 从最里面一格往外依次用 (最后一格是 .)

KEEP = 3                        # 每个用户同时最多持有几条指令, 满了就不能再领 (没有领取冷却)
DEV_KEEP = KEEP                 # 旧名字

# 每种指令对应界面上哪个入口 (在那个按钮上标记): 专注 / 休息 开始按钮, 合约 / 统计 侧边栏入口; IV / V 没有标记
MARK_OF = {"I": "focus", "III": "break", "II": "contract", "VI": "stats"}
HISTORY_KEEP = 100
SYNC_DAYS = 3                   # 同步给其他设备的指令记录: 最近这么多天的
SYNC_VOLATILE = ("focus_sec", "break_sec", "stats_since", "log_base")   # 各设备自己累计的进度, 不同步
GRANTED_KEEP = 40
SAVE_EVERY = 10                 # 秒; 累计的专注 / 休息秒数最多丢这么多


def _iso(when: datetime) -> str:
    return when.replace(microsecond=0).isoformat(timespec="seconds")


def _parse(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def fmt_t(t) -> str:
    """t 的小时数写法: 1 → "1", 0.1 → "0.1", 2.5 → "2.5"."""
    try:
        t = float(t)
    except (TypeError, ValueError):
        return "0"
    if abs(t - round(t)) < 1e-9:
        return str(int(round(t)))
    return f"{t:.3f}".rstrip("0").rstrip(".")


def rest_code(minutes) -> str:
    """给出 N 分钟休息的代码正文: 3 → "REST3", 0.3 → "REST0.3"."""
    try:
        minutes = round(float(minutes), 1)
    except (TypeError, ValueError):
        minutes = 0.0
    text = str(int(minutes)) if abs(minutes - round(minutes)) < 1e-9 else f"{minutes:.1f}"
    return "REST" + text


def code_for(t) -> str:
    """完成一条指令时显示的蓝色代码的正文: 休息 t×3 分钟."""
    try:
        return rest_code(float(t) * 3)
    except (TypeError, ValueError):
        return rest_code(0)


_LEGACY_H = re.compile(r"^(\d+(?:\.\d+)?)h\*3min$")       # 旧存档里的 "1h*3min"
_LEGACY_MIN = re.compile(r"^(\d+(?:\.\d+)?)min$")          # 旧存档里的 "20min"


def pad_code(code: str) -> str:
    """蓝色代码补到 8 个字符: 正文居中, 两边用 _ % . 补位 ("NONE" → "_%NONE%.")."""
    code = str(code or "")
    m = _LEGACY_H.match(code)
    if m:
        code = rest_code(float(m.group(1)) * 3)
    else:
        m = _LEGACY_MIN.match(code)
        if m:
            code = rest_code(float(m.group(1)))
    code = code[:CODE_WIDTH]
    gap = CODE_WIDTH - len(code)
    left = gap // 2
    right = gap - left
    return ((CODE_LEFT * CODE_WIDTH)[:left] + code
            + (CODE_RIGHT * CODE_WIDTH)[CODE_WIDTH * 2 - right:])


def rest_minutes(remaining_seconds) -> int:
    """III 要休息几分钟: 剩余休息时间的 50%, 最多 30 分钟, 至少 1 分钟."""
    try:
        minutes = int(float(remaining_seconds) * REST_SHARE // 60)
    except (TypeError, ValueError):
        minutes = 0
    return max(1, min(REST_MAX_MIN, minutes))


def type_code(kind) -> str:
    """一条指令的蓝色代码正文 (还没补位): I → "FOCUS"."""
    return TYPE_WORDS.get(str(kind), "NONE")


def fmt_clock(seconds) -> str:
    """倒计时: 不足一小时写 mm:ss, 否则 h:mm:ss."""
    seconds = max(0, int(round(float(seconds or 0))))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _midnight_after(now: datetime) -> datetime:
    return datetime.combine(now.date() + timedelta(days=1), datetime.min.time())


def contract_duration_hours(rec: dict) -> float:
    """合约记录的总时长 (小时): 按计划结束时刻算, 没有时用实际结束时刻."""
    rec = rec if isinstance(rec, dict) else {}
    start = _parse(rec.get("start"))
    stop = _parse(rec.get("planned")) or _parse(rec.get("end"))
    if start is None or stop is None or stop <= start:
        return 0.0
    return (stop - start).total_seconds() / 3600.0

