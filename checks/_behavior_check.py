"""行为模式 / 外出和 DataManager 的叠加 (MECHANICS 21).

在临时目录里跑, 不碰本机的数据.
"""

import tempstore
tempstore.session("behavior_check")      # 临时文件放在 %TEMP%\OIT 里, 退出时整个删掉 (tempstore.py)

import os
import tempfile
from datetime import datetime, timedelta

import config

keep = (config.BEHAVIOR_MODE, os.environ.get("USERPROFILE"))
root = tempfile.mkdtemp()
os.environ["USERPROFILE"] = root
try:
    import behavior
    from data_manager import DataManager

    config.BEHAVIOR_MODE = config.BEHAVIOR_CLOSED
    at = datetime(2026, 10, 19, 0, 0)
    b = behavior.Behavior(os.path.join(root, "behavior"), now=at)
    dm = DataManager(hour_auto_text=b.hour_auto_text)
    dm.extra_study = b.study_hours
    dm.extra_days = b.days
    dm.extra_focus = b.focus_segments

    ok, msg, o = b.start(4, datetime(2026, 10, 19, 10, 0), owner="pc")
    assert ok, msg

    # 学习时长叠加: 10:00 ~ 14:00 → 3.2 h (8:2)
    assert abs(dm._study_all("2026-10-19") - 3.2) < 1e-9, dm._study_all("2026-10-19")
    assert dm.get_today_hours() >= 0
    assert "2026-10-19" in dm.month_study("2026-10")
    assert abs(dm.month_study("2026-10")["2026-10-19"] - 3.2) < 1e-9

    # 专注计时条: 10 点那一格 48 分钟专注 + 12 分钟休息
    hours = dm.focus_hours(datetime(2026, 10, 19))
    study, rest = hours[10]
    assert abs(study - 2880) < 1e-6, (study, rest)
    assert abs(rest - 720) < 1e-6, (study, rest)
    assert abs(sum(s for s, _r in hours) - 3.2 * 3600) < 1e-6

    # 整点记录的自动填充内容
    assert dm.auto_text_for_key("2026-10-19/10") == "上学", "封闭式管理的外出填「上学」"
    assert dm.auto_text_for_key("2026-10-19/8") == "睡眠"
    assert dm.is_auto_text("上学", "2026-10-19/10")
    assert not dm.is_auto_text("学习", "2026-10-19/10")
    assert dm.is_fixed_auto("上学", "2026-10-19/10") and dm.is_fixed_auto("睡眠", "2026-10-19/8"), "固定的自动内容不能改"
    assert not dm.is_fixed_auto("学习", "2026-10-19/10") and not dm.is_fixed_auto("", "2026-10-19/10")

    # 结束外出: 4 小时 → 24 分钟休息; 再结束一次不重复发
    ev = b.stop(datetime(2026, 10, 19, 14, 0), can_reward=True)
    assert ev["reward"] == 4 * 6 * 60 and abs(ev["hours"] - 4) < 1e-9, ev
    assert b.stop(datetime(2026, 10, 19, 14, 0), can_reward=True)["reward"] == 0
    assert abs(dm._study_all("2026-10-19") - 3.2) < 1e-9

    # 联网额度: 有外出的日子只能用总额度 (每日额度 + 借到的 + 合约奖励) 的 20% (NetGuard 每次用时现读)
    from net_guard import NetGuard
    keep_limit = config.DAILY_LIMIT
    config.DAILY_LIMIT = 3600
    try:
        ng = NetGuard.__new__(NetGuard)
        ng.dead = True
        ng.date = "2026-10-19"
        ng.quota_factor = b.quota_factor
        ng.cooldown_factor = b.cooldown_factor
        ng.bonus, ng.gift, ng.used, ng.debt = 1800.0, 600.0, 300.0, 0.0
        assert abs(ng.limit - 3600) < 1e-6, ng.limit
        assert abs(ng.outing_factor - 0.2) < 1e-9
        assert abs(ng.full_total - 6000) < 1e-6 and abs(ng.today_total - 1200) < 1e-6, ng.today_total
        assert abs(ng.remaining - 900) < 1e-6, ng.remaining
        ng.used = 1500.0
        assert ng.remaining == 0.0, "外出前已经用掉的照常算"
        ng.quota_factor = lambda day: 1.0
        assert abs(ng.today_total - 6000) < 1e-6
    finally:
        config.DAILY_LIMIT = keep_limit

    # 自由学习: 不再有记录规则, 没有外出的小时不填「休息」; 切换之前的小时不改
    config.BEHAVIOR_MODE = config.BEHAVIOR_FREE
    b.sync_mode(datetime(2026, 10, 20, 0, 0))
    assert abs(dm._study_all("2026-10-19") - 3.2) < 1e-9, "已经发生的外出仍然算 (历史记录)"
    assert dm.auto_text_for_key("2026-10-19/10") == dm.auto_text
    assert dm.auto_text_for_key("2026-10-20/10") == dm.auto_text
    assert not dm.is_fixed_auto(dm.auto_text, "2026-10-20/10"), "Distracted 自动填的格子可以改"

    # 自由学习的外出: 标记而已, 不记录学习时长, 整点记录填「外出」; 工作的外出填「工作」
    bf = behavior.Behavior(os.path.join(root, "behavior_free"), now=at)
    dmf = DataManager(hour_auto_text=bf.hour_auto_text)
    dmf.extra_study = bf.study_hours
    dmf.extra_days = bf.days
    dmf.extra_focus = bf.focus_segments
    assert bf.start(4, datetime(2026, 10, 19, 10, 0), owner="pc")[0], "自由学习也可以外出"
    assert bf.active(datetime(2026, 10, 19, 11, 0)) is None and bf.away(datetime(2026, 10, 19, 11, 0)) is not None
    assert dmf._study_all("2026-10-19") == 0 and not bf.days(), "自由学习的外出不叠加学习时长"
    assert dmf.auto_text_for_key("2026-10-19/10") == "外出" and dmf.auto_text_for_key("2026-10-19/9") == dmf.auto_text
    config.BEHAVIOR_MODE = config.BEHAVIOR_WORK
    bw = behavior.Behavior(os.path.join(root, "behavior_work"), now=at)
    dmw = DataManager(hour_auto_text=bw.hour_auto_text)
    bw.start(2, datetime(2026, 10, 19, 10, 0))
    assert dmw.auto_text_for_key("2026-10-19/10") == "工作" and dmw.auto_text_for_key("2026-10-19/12") == "睡眠"

    # Timer 页的每秒检查: 外出中 → 显示倒计时; 结束后 → 回到准备状态
    from app import BreakTimerApp
    now = datetime.now().replace(microsecond=0)
    config.BEHAVIOR_MODE = config.BEHAVIOR_CLOSED
    b2 = behavior.Behavior(os.path.join(root, "behavior2"), now=now)
    assert b2.start(2, "", now)[0]

    class _Fake:
        shutting_down = False
        state = config.IDLE
        _outing_was_active = True
        behavior = b2
        sync = type("_S", (), {"peers": lambda self: [], "sync_now": lambda self: None})()
        shown = None
        reset = False

        def _can_reward_outing(self, outing=None):
            return True

        def _apply_outing_result(self, res):
            self.applied = res

        def _show_outing(self, active):
            self.shown = active

        def reset_to_idle(self):
            self.reset = True
            self.state = config.IDLE

        def _sync_outing_button(self):
            pass

        def _sync_bonus_button(self):
            pass

    f = _Fake()
    BreakTimerApp._behavior_tick(f)
    assert f.shown is not None and not f.reset
    b2.stop(now + timedelta(minutes=1), can_reward=True)
    BreakTimerApp._behavior_tick(f)
    assert f.reset, "外出结束后 Timer 页回到准备状态"
finally:
    config.BEHAVIOR_MODE, old = keep
    if old is None:
        os.environ.pop("USERPROFILE", None)
    else:
        os.environ["USERPROFILE"] = old

print("behavior integration check OK")
