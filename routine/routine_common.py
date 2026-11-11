"""日程引擎的公共部分: 确定性的 sha256 随机、分钟 / 概率解析、时间格式, 以及常量。

从 routine.py 拆出来的部分; 依赖方向: routine.py 和后面的 parts 导入它, 它从不导入 routine.py.
"""

import re
import hashlib
from datetime import datetime


KEEP_JOINT = 120                  # 存下来的真实联合行动合约记录最多留多少条
KEEP_DAYS = 35                    # 存下来的时间线 / 整点记录最多留多少天
SLEEP_CELL = "睡眠"                # 睡着的整点记录格子的内容 (同 OIT 自己的整点记录)
BACKFILL_DAYS = 7                # 第一次使用 (没有存档) 时往前补多少天
SYNC_MAX_DAYS = 14                # 一次最多补多少天 (很久没开程序也只补最近这些)
CHAT_FULL = 50.0                  # 上一个区间用户发了 >= CHAT_FULL_MSGS 条 → "聊天" 的概率 (%)
CHAT_FULL_MSGS = 20               # (一个小时的区间; 半小时的格子按比例是 10 条)
BASE_FLUCT = 0.02                 # (基准数据) 每一项概率的波动比例 ±2%
NET_SHARE = 0.25                  # 联网总时长 = 清醒但不在专注的时间 × 这个比例
NET_QUOTA_S = 2 * 3600.0          # 对方的每日联网额度 (秒; 和用户自己的设置无关, 电脑和手机一样)
CONTRACT_DAY_P = 0.3              # 每天完成一个合约的概率 (对方的合约记录)
NAMES_NO_PROB = ("设备列表",)

_NUM = r"(\d+(?:\.\d+)?)"
_UNIT = r"(min|mins|分钟|h|hr|hrs|小时)"
_DUR_RE = re.compile(r"^[ \t]*(?:[-*•][ \t]*)?" + _NUM + r"[ \t]*" + _UNIT + r"[ \t]*[:：][ \t]*(\S[^\n]*)$", re.I | re.M)
_LOOP_RE = re.compile(r"^[ \t]*(?:[-*•][ \t]*)?[\[［][ \t]*(?:循环|loop|cycle|repeat)[ \t]*" + _NUM + r"[ \t]*" + _UNIT + r"?[ \t]*[\]］]"
                      r"[ \t]*(?:[\[［][ \t]*" + _NUM + r"[ \t]*" + _UNIT + r"?[ \t]*[\]］])?", re.I | re.M)
_START_RE = re.compile(r"初始起点[ \t]*[:：][ \t]*(\d{1,2})[:：](\d{2})")
_FOCUS_RE = re.compile(r"^[ \t]*专注时段[ \t]*[:：][ \t]*(\S[^\n]*)$", re.M)
_RANGE = r"[\[［][ \t]*([+-]?\d+(?:\.\d+)?)[ \t]*[,，~～][ \t]*([+-]?\d+(?:\.\d+)?)[ \t]*[\]］]"
_FLUCT_KEYS = (("focus_start", "下一个专注开始节点波动"), ("focus_end", "下一个专注结束节点波动"),
               ("sleep_start", "睡眠开始时间波动"), ("sleep_end", "睡眠结束时间波动"),
               ("rest_end", "循环末尾休息结束时间波动"))          # 1.0.400: 每个循环最后那次休息比循环的结束晚多少分钟 (没写 = 不晚, 正好对上)
_LIST_RE = re.compile(r"^[ \t]*(?:[-*•][ \t]*)?([^\n{}]+?)[ \t]*[:：][ \t]*[{｛]([^{}｛｝\n]*)[}｝][ \t]*(\([ \t]*基准数据[ \t]*\)|（[ \t]*基准数据[ \t]*）)?", re.M)
_DEV_WIN_RE = re.compile(r"^设备[-－](\d+)[ \t]*[:：][ \t]*窗口使用时间$")
_ITEM_RE = re.compile(r"^[ \t]*(X|x|\d+(?:\.\d+)?)[ \t]*[%％][ \t]*[:：][ \t]*(\S.*)$")
_DIFF_RE = re.compile(r"合约记录难度[ \t]*[:：]" + r"[ \t]*" + _RANGE)


# ══════════════ 确定性随机 (sha256: Python 和 Kotlin 算出一样的数) ══════════════
def u01(key: str) -> float:
    h = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") / float(1 << 64)


def rint(key: str, lo, hi) -> int:
    """[lo, hi] 里的整数 (两端都可能取到)."""
    lo, hi = int(round(min(lo, hi))), int(round(max(lo, hi)))
    return lo + min(hi - lo, int(u01(key) * (hi - lo + 1)))


# ══════════════ 解析 ══════════════
def _minutes(v: float, unit: str) -> float:
    u = (unit or "min").lower()
    return float(v) * (60.0 if u.startswith("h") or u == "小时" else 1.0)


def parse_list(body: str) -> list:
    """"{30%:英语,数学,语文}" 里的内容 → [{"name", "pct" (没写 = None), "x" (写 X% = 随聊天变)}]; 没写概率的项按剩下的概率平分 (resolve_list)."""
    out = []
    for raw in re.split(r"[,，]", body or ""):
        raw = raw.strip()
        if not raw:
            continue
        m = _ITEM_RE.match(raw)
        if m:
            x = m.group(1) in ("X", "x")
            out.append({"name": m.group(2).strip(), "pct": None if x else float(m.group(1)), "x": x})
        else:
            out.append({"name": raw, "pct": None, "x": False})
    return out


def resolve_list(items: list, x_value: float = None) -> list:
    """没写概率的项按其他项剩下的概率平分 (规则 IV). x_value: 写成 X% 的项此刻的概率 (None = 当 0). 返回 [(名字, 概率%)], 总和 <= 100."""
    fixed = sum((x_value or 0.0) if it["x"] else (it["pct"] or 0.0) for it in items if it["x"] or it["pct"] is not None)
    free = [it for it in items if not it["x"] and it["pct"] is None]
    share = max(0.0, 100.0 - fixed) / len(free) if free else 0.0
    out = []
    for it in items:
        p = (x_value or 0.0) if it["x"] else (it["pct"] if it["pct"] is not None else share)
        out.append((it["name"], max(0.0, p)))
    return out


def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat(timespec="seconds")


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def plain_label(label: str) -> bool:
    """这一段的标签是不是普通的"空闲 / 休息" (不含"特殊空闲")."""
    head = re.split(r"[(（]", str(label or ""), maxsplit=1)[0].strip().lower()
    return head in ("空闲", "idle", "free", "休息", "rest", "")


