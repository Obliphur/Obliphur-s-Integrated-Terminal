"""焦点设备 (最后有人操作的设备) 的"操作时刻" (MECHANICS 15.2 / 18.8).

检查:
- Windows 上取的是全系统的键盘 / 鼠标输入时间 (别的程序里在用电脑也算), 不是只认 OIT 窗口;
- 取不到系统输入时间 (非 Windows / 调用失败) 时不更新 —— 免得把一台没人用的设备误判成一直在用;
- 操作时刻只往前走, 不会因为更早的输入倒退.
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


test_system_idle()
test_note_input()
test_status_refreshes()
test_comm_notices_follow_focus()
test_break_notice_follows_focus()
test_schedule_follows_focus_instead_of_chat_leader()
print("focus device check OK (全系统输入时间 / 只前进 / 取不到时不动)")
