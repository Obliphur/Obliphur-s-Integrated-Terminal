"""睡觉时间 (设置 → 日常) 和跟着它走的两个提示 (MECHANICS 19).

- 最近一次睡觉时间之后 3 小时 (SLEEP_WINDOW) 之内, 非合约期间第一次暂停休息: 弹一段话,
  那次休息的暂停结转比例 +5%.
- 合约的结束时间 (开始时刻 + 时长) 晚于"这次要睡的时间"时, 当天第一次按"开始行动"
  先弹一段确认, 取消就不开始. 这次要睡的时间: 还在睡觉后的窗口里 → 最近那次;
  已经过了窗口 → 下一次 (白天开始合约时, 比的就是今晚).

跨零点: 凌晨还没睡时, 最近一次睡觉时间是昨天那次 (设置里每天的时间可以不一样).
两个提示各自每个睡觉窗口 / 每天最多一次, 标记存在 DataManager.daily (daily.json).
"""

import math
from datetime import datetime, timedelta

import config
from i18n import tr


SLEEP_TEXT = ("你好,主管{name},我是Obliphur.到了应该睡觉的时间了.\n"
              "睡眠的时间会比较大幅度地影响明天的学习/工作.\n"
              "现在你的暂停休息倍率有+5%的修正.\n"
              "晚安.")

LATE_TEXT = "如果正常完成行动 会延迟你的睡眠至少 {n} min 你真的要开始吗?"

BREAK_FLAG = "sleep_break"         # 这个睡觉窗口里已经弹过提示 (存窗户那天的日期)
CONTRACT_FLAG = "contract_sleep"   # 这天按"开始行动"时已经弹过睡眠提醒 (存日期)


def sleep_text(name: str) -> str:
    """窗口内第一次暂停休息时弹的文字."""
    return tr(SLEEP_TEXT, name=name or "")


def late_text(minutes: int) -> str:
    """合约结束时间晚于睡觉时间时的确认文字."""
    return tr(LATE_TEXT, n=max(1, int(minutes)))


def window_day(now: datetime = None):
    """现在落在哪个睡觉窗口: 窗口那天的日期 (= 最近一次睡觉时间所在的那天)."""
    now = now or datetime.now()
    return config.last_sleep(now).date()


def in_window(now: datetime = None) -> bool:
    """现在是不是在最近一次睡觉时间之后的 SLEEP_WINDOW 秒之内."""
    now = now or datetime.now()
    return now < config.last_sleep(now) + timedelta(seconds=config.SLEEP_WINDOW)


def bedtime(now: datetime = None) -> datetime:
    """现在这次"要睡的时间": 还在睡觉后的窗口里 → 最近那次 (已经晚了);
    已经过了窗口 → 下一次 (白天 / 傍晚时比的就是今晚)."""
    now = now or datetime.now()
    last = config.last_sleep(now)
    if now < last + timedelta(seconds=config.SLEEP_WINDOW):
        return last
    return config.next_sleep(now)


def delay_minutes(end: datetime, now: datetime = None) -> int:
    """合约结束时间比"这次要睡的时间"晚多少分钟 (向上取整); 不比它晚时 0."""
    now = now or datetime.now()
    sec = (end - bedtime(now)).total_seconds()
    if sec <= 0:
        return 0
    return max(1, int(math.ceil(sec / 60.0)))


def break_rate(rate: float) -> float:
    """窗口内那次休息的暂停结转比例 (+5%)."""
    return float(rate) * (1.0 + float(config.SLEEP_BREAK_BONUS))


def take_break_notice(dm, now: datetime = None) -> bool:
    """这次暂停休息要不要弹睡觉提示: 在窗口里, 而且这个睡觉窗口还没弹过.
    要弹的时候顺手把标记写上 (调用方负责确认不在合约里)."""
    now = now or datetime.now()
    day = window_day(now).isoformat()
    if not in_window(now) or dm.daily_flag(BREAK_FLAG) == day:
        return False
    dm.set_daily_flag(BREAK_FLAG, day)
    return True


def contract_delay(dm, end: datetime, now: datetime = None) -> int:
    """开始合约前要不要弹睡眠提醒: 要弹时返回至少会延迟的分钟数 (并写上今天的标记), 否则 0."""
    now = now or datetime.now()
    n = delay_minutes(end, now)
    today = now.date().isoformat()
    if n <= 0 or dm.daily_flag(CONTRACT_FLAG) == today:
        return 0
    dm.set_daily_flag(CONTRACT_FLAG, today)
    return n


def _selftest():
    from datetime import date

    class _DM:
        def __init__(self):
            self.flags = {}

        def daily_flag(self, key):
            return self.flags.get(key, "")

        def set_daily_flag(self, key, day):
            self.flags[key] = day

    def at(y, m, d, hh, mm):
        return datetime(y, m, d, hh, mm)

    # 周一 23:00, 周六 01:30 (周六凌晨才睡); 2026-10-19 是周一
    keep = (config.SLEEP_MON, config.SLEEP_SAT)
    config.SLEEP_MON, config.SLEEP_SAT = 23 * 60, 90
    try:
        assert at(2026, 10, 19, 0, 0).weekday() == 0
        # 最近一次 / 下一次睡觉时间
        assert config.last_sleep(at(2026, 10, 19, 23, 0)) == at(2026, 10, 19, 23, 0)
        assert config.last_sleep(at(2026, 10, 19, 22, 59)) == at(2026, 10, 18, 23, 0)
        assert config.next_sleep(at(2026, 10, 19, 20, 0)) == at(2026, 10, 19, 23, 0)
        assert config.next_sleep(at(2026, 10, 19, 23, 0)) == at(2026, 10, 20, 23, 0)
        # 3 小时窗口跨零点: 23:00 ~ 02:00
        assert in_window(at(2026, 10, 19, 23, 30))
        assert in_window(at(2026, 10, 20, 1, 59))
        assert not in_window(at(2026, 10, 20, 2, 0))
        assert not in_window(at(2026, 10, 19, 20, 0))
        # 凌晨还算前一天那个窗
        assert window_day(at(2026, 10, 20, 1, 30)) == date(2026, 10, 19)
        assert window_day(at(2026, 10, 20, 5, 0)) == date(2026, 10, 19)
        # 周六 01:30 睡: 窗口 01:30 ~ 04:30
        assert config.last_sleep(at(2026, 10, 24, 1, 0)) == at(2026, 10, 23, 23, 0)
        assert config.last_sleep(at(2026, 10, 24, 2, 0)) == at(2026, 10, 24, 1, 30)
        assert in_window(at(2026, 10, 24, 4, 0)) and not in_window(at(2026, 10, 24, 5, 0))

        # 这次要睡的时间: 白天 → 今晚; 窗口里 → 最近那次
        assert bedtime(at(2026, 10, 19, 20, 0)) == at(2026, 10, 19, 23, 0)
        assert bedtime(at(2026, 10, 19, 23, 30)) == at(2026, 10, 19, 23, 0)
        assert bedtime(at(2026, 10, 20, 1, 0)) == at(2026, 10, 19, 23, 0)
        assert bedtime(at(2026, 10, 20, 2, 30)) == at(2026, 10, 20, 23, 0)
        assert bedtime(at(2026, 10, 24, 3, 0)) == at(2026, 10, 24, 1, 30)

        # +5%
        assert abs(break_rate(0.7) - 0.735) < 1e-9

        # 合约结束时间晚了多少分钟
        assert delay_minutes(at(2026, 10, 20, 2, 0), at(2026, 10, 19, 23, 0)) == 180
        assert delay_minutes(at(2026, 10, 20, 0, 0), at(2026, 10, 19, 23, 0)) == 60
        assert delay_minutes(at(2026, 10, 19, 22, 0), at(2026, 10, 19, 20, 0)) == 0
        assert delay_minutes(at(2026, 10, 20, 2, 0), at(2026, 10, 19, 23, 30)) == 180
        assert delay_minutes(at(2026, 10, 20, 2, 0), at(2026, 10, 20, 1, 0)) == 180
        assert delay_minutes(at(2026, 10, 20, 22, 30), at(2026, 10, 20, 2, 30)) == 0
        assert delay_minutes(at(2026, 10, 20, 2, 0), at(2026, 10, 19, 21, 0)) == 180

        dm = _DM()
        now = at(2026, 10, 19, 23, 10)
        assert take_break_notice(dm, now) and dm.daily_flag(BREAK_FLAG) == "2026-10-19"
        assert not take_break_notice(dm, at(2026, 10, 20, 0, 30)), "同一个睡觉窗口只弹一次"
        assert take_break_notice(dm, at(2026, 10, 20, 23, 10)) and \
            dm.daily_flag(BREAK_FLAG) == "2026-10-20"
        assert not take_break_notice(dm, at(2026, 10, 20, 20, 0)), "窗口外不弹"

        dm2 = _DM()
        assert contract_delay(dm2, at(2026, 10, 20, 3, 0), at(2026, 10, 19, 23, 0)) == 240
        assert contract_delay(dm2, at(2026, 10, 20, 4, 0), at(2026, 10, 19, 23, 5)) == 0, "每天只一次"
        assert contract_delay(dm2, at(2026, 10, 21, 3, 0), at(2026, 10, 20, 23, 5)) == 240, "第二天还有"
        dm3 = _DM()
        assert contract_delay(dm3, at(2026, 10, 19, 22, 0), at(2026, 10, 19, 20, 0)) == 0
        assert not dm3.flags, "不弹的时候不写标记"
        assert contract_delay(dm3, at(2026, 10, 21, 0, 0), at(2026, 10, 20, 20, 0)) == 60, \
            "白天开始合约: 比的是今晚"
        assert sleep_text("A") and "{n}" not in late_text(90)
    finally:
        config.SLEEP_MON, config.SLEEP_SAT = keep
    return "daily selftest OK"


if __name__ == "__main__":
    print(_selftest())
