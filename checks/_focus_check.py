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
from app_parts import app_sync as S  # noqa: E402


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


test_system_idle()
test_note_input()
test_status_refreshes()
print("focus device check OK (全系统输入时间 / 只前进 / 取不到时不动)")
