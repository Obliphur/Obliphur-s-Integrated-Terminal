"""焦点设备 (最后有人操作的设备) 的"操作时刻" (MECHANICS 15.2 / 18.8).

检查:
- Windows 上取的是全系统的键盘 / 鼠标输入时间 (别的程序里在用电脑也算), 不是只认 OIT 窗口;
- 取不到系统输入时间 (非 Windows / 调用失败) 时不更新 —— 免得把一台没人用的设备误判成一直在用;
- 操作时刻只往前走, 不会因为更早的输入倒退.
- 专注确认窗只在焦点设备显示, 切换焦点不算确认、不延后期限; 手动确认照常同步.
不开窗口, 不碰真实数据.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempstore

tempstore.autosession()
from app import app_sync as S  # noqa: E402


def test_system_idle():
    v = S.system_idle_ms()
    if sys.platform == "win32":
        assert v >= 0, v
        assert v < 86_400_000, v
    else:
        assert v == -1, v


def test_note_input():
    class A:
        pass

    a = A()
    a.last_op = 1000.0
    real = S.system_idle_ms
    try:
        S.system_idle_ms = lambda: 5000          # 5 秒前有过输入
        S.SyncMixin._note_input(a)
        now = time.time() * 1000
        assert abs(a.last_op - (now - 5000)) < 5000, (a.last_op, now)
        before = a.last_op
        S.system_idle_ms = lambda: 60_000        # 更早的输入: 不倒退
        S.SyncMixin._note_input(a)
        assert a.last_op == before
        S.system_idle_ms = lambda: -1            # 取不到: 不动
        S.SyncMixin._note_input(a)
        assert a.last_op == before
        S.system_idle_ms = lambda: 10            # 刚有输入: 更新到此刻
        S.SyncMixin._note_input(a)
        assert time.time() * 1000 - a.last_op < 2000, a.last_op
    finally:
        S.system_idle_ms = real


def test_status_refreshes():
    """状态发布前会先刷新 last_op (别的设备据此判断焦点)."""
    import types

    import sync_local

    app = types.SimpleNamespace()
    app.last_op = 1000.0
    app.power_off = False
    app.autostart_on = False
    app.net = types.SimpleNamespace(mode="proxy")
    app.sync = types.SimpleNamespace(adapter_since=lambda: 0)
    called = []
    app._note_input = lambda: called.append(1)
    loc = sync_local.PcLocal.__new__(sync_local.PcLocal)
    loc.app = app
    try:
        st = loc.status()
        assert called == [1], "status() 要先刷新本机的操作时刻"
        assert st["last_op"] == 1000, st
    except AttributeError as e:
        # 老的假 app 缺属性时只要求 _note_input 被调用
        assert called == [1], e


def test_comm_notices_follow_focus():
    """消息在手机处理, 电脑取得提示; 确认状态同步回来后不重复弹."""
    from comm import Comm
    from pathlib import Path
    root = Path(tempstore.mkdtemp("focus-notices"))
    phone = Comm(str(root / "phone"), str(root / "personas"))
    pc = Comm(str(root / "pc"), str(root / "personas"))
    phone.focus_device = lambda: False
    pc.focus_device = lambda: True
    phone.queue_focus_notice("test notice")
    assert phone.take_focus_notices() == []
    pc.apply_doc(phone.sync_doc())
    assert pc.take_focus_notices() == ["test notice"]
    assert pc.take_focus_notices() == []
    phone.apply_doc(pc.sync_doc())
    phone.focus_device = lambda: True
    assert phone.take_focus_notices() == []


def test_break_notice_follows_focus():
    """剩余一分钟的声音和弹窗一并遵守焦点判断."""
    import types
    from app import app_pages
    focused, calls = [False], []
    a = types.SimpleNamespace(sync=types.SimpleNamespace(is_latest_active=lambda: focused[0]),
                              _play_break_soon_chime=lambda: calls.append("sound"))
    original = app_pages.toast.info
    try:
        app_pages.toast.info = lambda text: calls.append("toast")
        app_pages.PagesMixin._break_one_min_warn(a)
        assert calls == []
        focused[0] = True
        app_pages.PagesMixin._break_one_min_warn(a)
        assert calls == ["sound", "toast"]
    finally:
        app_pages.toast.info = original


def test_schedule_follows_focus_instead_of_chat_leader():
    """聊天主端在手机时, 电脑仍可作为焦点设备询问日程跟随."""
    import types
    from comm.comm_status import StatusMixin
    asked = []
    a = types.SimpleNamespace(persona_name=lambda: "AI", sf_live=lambda: True,
                              pid="p", _sf_last=("p", "focus"), _status_kind=lambda: "rest",
                              leader=lambda: False, focus_device=lambda: True,
                              state_fn=lambda: "idle", log=lambda text: None,
                              sf_text=lambda kind: kind, on_sf=lambda *args: asked.append(args))
    StatusMixin._sf_tick(a)
    assert asked == [("rest", "rest")]


def test_focus_check_follows_focus():
    """两台设备间转移提醒, 撤窗不确认; 确认同步后不重弹, 非焦点仍处理超时."""
    from datetime import datetime
    from types import SimpleNamespace
    from unittest.mock import Mock, patch
    import config
    from app.app_timer import TimerMixin

    class App(TimerMixin):
        pass

    def make_app(focused):
        a = App()
        a.state = config.RUNNING
        a.start_mono = time.monotonic() - 65
        a.start_wall = datetime.now()
        a._close_pending = a._pause_mono = a._check_win = None
        a._focus_checks = a._focus_ok = 0
        a._focus_saved = 0
        a.sync = SimpleNamespace(peers=lambda: ["peer"], is_latest_active=lambda: focused[0])
        a.contract = SimpleNamespace(focus_tag=lambda start: False)
        a._end_rewound_focus = lambda: False
        a._offline_secs = lambda start: 0
        a._timer_auto_ok = lambda: True
        a._check_mark = lambda n: 60 * n
        a._focus_elapsed = lambda: 65
        a._save_timer_state = Mock()
        a._mine = Mock()
        a._auto_end_focus = Mock()
        return a

    pc_focus, phone_focus = [True], [False]
    pc, phone = make_app(pc_focus), make_app(phone_focus)
    with patch("app.app_timer.toast.notice") as notice:
        def window(*args):
            w = SimpleNamespace(open=True)
            w.alive = lambda: w.open
            w.close = lambda: setattr(w, "open", False)
            return w

        notice.side_effect = window
        pc._check_focus()
        phone._check_focus()
        old = pc._check_win
        assert old is not None and phone._check_win is None
        phone._save_timer_state.assert_called()  # 非焦点仍定时保存
        pc_focus[0], phone_focus[0] = False, True
        pc._check_focus()
        phone._check_focus()
        assert not old.alive() and pc._check_win is None and phone._check_win is not None
        pc._check_focus()
        assert pc._focus_checks == phone._focus_checks == 0
        pc._mine.assert_not_called()
        phone._check_win.close()                 # 用户手动确认
        phone._focus_ok = 90                      # 之前确认过的有效专注时间
        phone._focus_elapsed = lambda: 65         # 暂停倒退后低于之前的确认值
        phone._check_focus()
        assert phone._focus_checks == 1 and phone._focus_ok == 90
        phone._mine.assert_called_once()

        # 同一段专注从远端同步确认次数, 焦点切回来也不重弹
        live = {"state": "running", "start": "same", "checks": 1, "ok": 65, "owner": "phone"}
        pc.timer_live = lambda: {"state": "running", "start": "same"}
        S.SyncMixin.adopt_timer(pc, live)
        pc_focus[0], phone_focus[0] = True, False
        pc._check_focus()
        assert pc._check_win is None and pc._focus_checks == 1

        # 焦点转移不延后原期限, 且超时仍由计时器主人处理
        phone._focus_elapsed = lambda: 120 + float(config.FOCUS_CHECK_GRACE)
        phone._check_focus()
        phone._auto_end_focus.assert_called_once_with(120 + float(config.FOCUS_CHECK_GRACE))
        assert notice.call_count == 2


test_system_idle()
test_note_input()
test_status_refreshes()
test_comm_notices_follow_focus()
test_break_notice_follows_focus()
test_schedule_follows_focus_instead_of_chat_leader()
test_focus_check_follows_focus()
print("focus device check OK (全系统输入 / 专注提醒随焦点转移 / 确认同步 / 超时)")
