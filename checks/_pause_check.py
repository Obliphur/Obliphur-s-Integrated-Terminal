"""专注暂停 (MECHANICS 6.9): 暂停让计时器的专注时长按 PAUSE_REWIND 倍率倒退,
暂停的时段在统计 / 日历里记为空闲, 但沟通 / 指令 / 合约一律当作专注.
不开窗口; 数据放在临时目录, 不碰本机的数据.
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempstore

tempstore.session("pause_check")

root = tempstore.mkdtemp("pause-check")
os.environ["USERPROFILE"] = root

import config  # noqa: E402
from app.app_timer import TimerMixin  # noqa: E402
from app.app_sync import SyncMixin  # noqa: E402
from data_manager import DataManager  # noqa: E402


def fake_app():
    """只带暂停 / 计时用到的字段."""
    class A(TimerMixin, SyncMixin):
        pass

    a = A()
    a.state = config.RUNNING
    a.start_mono = time.monotonic() - 600        # 墙上 10 分钟
    a.start_wall = datetime.now() - timedelta(seconds=600)
    a._pause_total = 0.0
    a._pause_mono = None
    a._pause_wall = None
    a.dm = DataManager()
    a.dm.focus_pause_live = lambda: (a.start_wall, a._pause_wall)
    a._mine = lambda: None
    a._close_check_win = lambda: None
    a._status = lambda *a1, **k: None
    a._sync_timer_buttons = lambda: None
    a._save_timer_state = lambda: None
    a.refresh_display = lambda: None
    a._refresh_if_visible = lambda *a1, **k: None
    a.lbl_sub = type("L", (), {"config": lambda self, **k: None})()
    a.prestart = type("P", (), {"active": False, "drop_last": lambda self: None,
                                "resume_last": lambda self: None})()
    a._sync_prestart_exempt = lambda: None
    a.cancel_update = lambda: None
    a.timer_owner = "test"
    a.sleep_since = None
    a._focus_checks = 0
    a._focus_ok = 0
    a._close_pending = None
    a.schedule_update = lambda: None
    return a


def test_elapsed_rewinds():
    a = fake_app()
    base = TimerMixin._focus_elapsed(a)
    assert 595 <= base <= 605, base
    a._pause_total = 120.0                       # 暂停过 2 分钟
    old = float(config.PAUSE_REWIND)
    try:
        config.PAUSE_REWIND = 2.0
        now = TimerMixin._focus_elapsed(a)
        assert abs(now - (base - 360)) < 1.5, (base, now)     # 2 倍倒退: 扣除墙上正常增长的 120 秒, 再倒退 240 秒
        config.PAUSE_REWIND = 1.0
        now = TimerMixin._focus_elapsed(a)
        assert abs(now - (base - 240)) < 1.5, (base, now)     # 下限 1: 扣除增长 120 秒再倒退 120 秒
        config.PAUSE_REWIND = 0.2                     # 低于下限按 1 算
        now = TimerMixin._focus_elapsed(a)
        assert abs(now - (base - 240)) < 1.5, (base, now)
        config.PAUSE_REWIND = 2.0
        a._pause_total = 10 ** 6                      # 倒退到负数: 夹到 0
        assert TimerMixin._focus_elapsed(a) == 0.0
    finally:
        config.PAUSE_REWIND = old


def test_pause_writes_idle():
    """暂停 → 继续: 暂停的那段在 focus_log 里记成 p=True 的空闲."""
    a = fake_app()
    TimerMixin._pause_focus(a)
    assert a._pause_mono is not None and a._pause_wall is not None
    a._pause_mono = time.monotonic() - 90        # 当作已经暂停了 90 秒
    a._pause_wall = datetime.now() - timedelta(seconds=90)
    TimerMixin._close_focus_pause(a)
    assert a._pause_mono is None and a._pause_wall is None
    assert abs(a._pause_total - 90) < 2, a._pause_total
    idle = [r for r in a.dm.focus_sessions if r.get("k") == "idle" and r.get("p")]
    assert len(idle) == 1, a.dm.focus_sessions
    assert idle[0]["f"] == a.start_wall.strftime(a.dm._TS), idle

    day = datetime.now()
    # 统计 / 日历: 这段算空闲 → 专注之后画成空白
    with_hole = a.dm.focus_hours(day, ("study", a.start_wall), True)
    as_focus = a.dm.focus_hours(day, ("study", a.start_wall), False)
    total_hole = sum(x[0] for x in with_hole)
    total_focus = sum(x[0] for x in as_focus)
    assert total_focus - total_hole >= 85, (total_focus, total_hole)

    # 沟通 / 指令: day_intervals 不把暂停当成空闲 → 仍然是一整段专注
    segs = a.dm.day_intervals(day, ("study", a.start_wall))
    study = [(s, e) for k, s, e in segs if k == "study"]
    assert study, segs
    assert (study[-1][1] - study[-1][0]).total_seconds() >= 595, study

    # 日历的另一条路: day_kinds 统计的也是把暂停当空闲
    kinds_idle = a.dm.day_kinds(day, ("study", a.start_wall), now=datetime.now(), pause_as_idle=True)
    kinds_focus = a.dm.day_kinds(day, ("study", a.start_wall), now=datetime.now(), pause_as_idle=False)
    assert kinds_focus["study"] - kinds_idle["study"] >= 85, (kinds_focus, kinds_idle)


def test_sync_roundtrip():
    """暂停状态经 timer_live / adopt_timer 传到别的设备, 恢复后照样倒退."""
    a = fake_app()
    TimerMixin._pause_focus(a)
    a._pause_total = 30.0
    a._pause_mono = time.monotonic() - 20
    a._pause_wall = datetime.now() - timedelta(seconds=20)
    live = {
        "state": "running",
        "owner": "x",
        "start": a.start_wall.replace(microsecond=0).isoformat(timespec="seconds"),
        "checks": 0, "ok": 0,
        "paused": round(a._pause_total, 1),
        "pause": a._pause_wall.isoformat(timespec="seconds"),
    }
    b = fake_app()
    TimerMixin._enter_focus(b, datetime.fromisoformat(live["start"]),
                            time.monotonic() - 600, 0, 0.0,
                            pause_total=float(live["paused"]),
                            pause_wall=datetime.fromisoformat(live["pause"]))
    assert abs(b._pause_total - 30.0) < 0.1, b._pause_total
    assert b._pause_mono is not None, "恢复后还在暂停"
    # 程序关着的那 20 秒也算进这次暂停: 距 _pause_mono 应该已经 20 秒左右
    assert abs((time.monotonic() - b._pause_mono) - 20) < 2, time.monotonic() - b._pause_mono
    old = float(config.PAUSE_REWIND)
    try:
        config.PAUSE_REWIND = 2.0
        el = TimerMixin._focus_elapsed(b)
        assert abs(el - (600 - 3 * (30 + 20))) < 3, el
    finally:
        config.PAUSE_REWIND = old


def test_setting_bounds():
    """设置项: 倍率下限 1, 上限 10 (小数)."""
    import settings_manager as sm
    spec = sm.SCHEMA_BY_KEY.get("PAUSE_REWIND")
    assert spec is not None, "设置页里没有 PAUSE_REWIND"
    assert spec["kind"] == "num", spec
    assert float(spec["min"]) == 1.0, spec
    assert float(spec["max"]) == 10.0, spec
    assert spec["page"] == "timer", spec
    assert "PAUSE_REWIND" in config.DEFAULT_SETTINGS
    m = sm.SettingsManager(os.path.join(root, "pause_settings.json"))
    assert m.validate("PAUSE_REWIND", 1)[0], "1 应该通过"
    assert not m.validate("PAUSE_REWIND", 0.5)[0], "低于下限应该被拒绝"
    assert not m.validate("PAUSE_REWIND", 11)[0], "高于上限应该被拒绝"


def test_live_pause_and_settlement():
    """暂停中立即显示为空闲; 结算保留完整数据时间线, 跨天也不缩短."""
    a = fake_app()
    a._pause_mono = time.monotonic() - 90
    a._pause_wall = datetime.now() - timedelta(seconds=90)
    hours = a.dm.focus_hours(datetime.now(), ("study", a.start_wall))
    assert abs(sum(s for s, _ in hours) - 510) < 3, hours
    a._close_focus_pause()
    a.contract = type("C", (), {"focus_minutes": lambda self, start, sec: sec / 60})()
    a._cut_offline = lambda *args: 0
    a._add_study_pieces = lambda start, end: a.dm.add_focus_session("study", start, end)
    a._focus_rest = lambda *args: 0
    total, _ = a._book_focus(a.start_wall, a._focus_elapsed(), data_seconds=a._focus_wall())
    assert abs(total - 10) < 0.1, total
    assert abs(sum(s for s, _ in a.dm.focus_hours(datetime.now())) - 510) < 3
    a.dm.load_focus_log()
    assert any(r.get("p") for r in a.dm.focus_sessions)
    # 已完成的跨零点专注, 暂停仅切掉实际重叠区间。
    b = fake_app()
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start, end = midnight - timedelta(minutes=10), midnight + timedelta(minutes=10)
    b.dm.focus_sessions = []
    b.dm.add_focus_session("study", start, end)
    b.dm.add_focus_session("idle", midnight - timedelta(minutes=2), midnight + timedelta(minutes=2), start, True)
    for day in (start, end):
        assert abs(sum(s for s, _ in b.dm.focus_hours(day)) - 480) < 1
        assert abs(sum(s for s, _ in b.dm.focus_hours(day, pause_as_idle=False)) - 600) < 1


def test_real_sync_pause_resume():
    """同一段专注也必须应用暂停/继续变化, 不能被同步的快速返回忽略."""
    a, b = fake_app(), fake_app()
    b._enter_focus(a.start_wall, a.start_mono)
    a._pause_focus()
    b.adopt_timer(a.timer_live())
    assert b._pause_mono is not None
    a.toggle_focus_pause()
    b.adopt_timer(a.timer_live())
    assert b._pause_mono is None


def test_zero_ends_focus():
    """倒退到零后自动结束且只弹一次; 继续/结束按钮也不能绕过, 真实统计仍保留."""
    from unittest.mock import Mock, patch
    for trigger in ("_check_focus", "update", "toggle_focus_pause", "on_action"):
        a = fake_app()
        a.start_mono = 100.0
        a.start_wall = datetime.now() - timedelta(seconds=600)
        a._pause_mono = 400.0
        a._pause_wall = datetime.now() - timedelta(seconds=300)
        a._timer_auto_ok = lambda: True
        a.lbl_time = a.lbl_sub
        a._idle_sub_text = lambda: ""
        a.timer_file = "unused"
        a._save_timer_state = Mock()
        a.break_carry = 123
        a._auto_rest = 45
        a.contract = type("C", (), {"focus_minutes": lambda self, start, sec: sec / 60,
                                    "focus_rate": lambda self, start: 1.0,
                                    "fog": lambda self: True})()
        a.root = Mock()
        a._cut_offline = lambda *args: 0
        a._add_study_pieces = lambda start, end: a.dm.add_focus_session("study", start, end)
        a._focus_rest = lambda *args: 0
        a._deep_check_closed = Mock()
        a.dm.focus_sessions = []
        with patch("app.app_timer.time.monotonic", return_value=700.0), \
                patch("app.app_timer.toast.notice") as notice:
            assert a._focus_elapsed() == 0
            getattr(a, trigger)()
            if trigger == "update":
                assert a.state == config.RUNNING   # 等同步 / 恢复流程先装好状态
                a.root.after_idle.call_args.args[0]()
            assert a.state == config.IDLE, trigger
            assert a.start_wall is None and a._pause_mono is None
            assert a.break_carry == 123 and a._auto_rest == 45
            a._save_timer_state.assert_called()
            a._deep_check_closed.assert_called_once()
            notice.assert_called_once()
            assert "0" in notice.call_args.args[1]
            a._check_focus()
            notice.assert_called_once()
        assert abs(sum(s for s, _ in a.dm.focus_hours(datetime.now())) - 300) < 3
        assert abs(sum(s for s, _ in a.dm.focus_hours(datetime.now(), pause_as_idle=False)) - 600) < 3


def test_zero_end_guards():
    """普通专注从零开始, 暂停未到零、等主人或等重开裁定时均不能提前结束."""
    from unittest.mock import patch
    a = fake_app()
    a._timer_auto_ok = lambda: True
    with patch("app.app_timer.time.monotonic", return_value=700.0):
        a.start_mono = 700.0
        assert not a._end_rewound_focus()
        a.start_mono = 100.0
        a._pause_mono = 699.0
        assert not a._end_rewound_focus()
        a._pause_mono = 400.0
        a._timer_auto_ok = lambda: False
        assert not a._end_rewound_focus()
        a._timer_auto_ok = lambda: True
        a._close_pending = object()
        assert not a._end_rewound_focus()
        assert a.state == config.RUNNING


test_elapsed_rewinds()
test_pause_writes_idle()
test_sync_roundtrip()
test_setting_bounds()
test_live_pause_and_settlement()
test_real_sync_pause_resume()
test_zero_ends_focus()
test_zero_end_guards()
print("pause check OK (倒退 / 零点结束与弹窗 / 记空闲 / 同步 / 下限)")
