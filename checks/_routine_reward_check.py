"""Claudia 合约奖励的日程、共同休息接续、重启与跨设备去重；只使用临时数据。"""
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempstore
tempstore.session("routine_reward_check")
import routine
from comm import Comm
from comm.comm_persona import pid_of

clock = [datetime(2026, 11, 20, 10)]
root = tempstore.mkdtemp()
personas = os.path.join(root, "personas")
os.makedirs(personas)
with open(os.path.join(personas, "Claudia.txt"), "w", encoding="utf-8") as f:
    f.write("你是 Claudia。")
with open(os.path.join(personas, "Claudia_templates.txt"), "w", encoding="utf-8") as f:
    f.write("初始起点:7:00\n[循环 300min][100min]\n60min:空闲\n[循环 300min][100min]\n30min:特殊空闲")


def make(name):
    """阻止网络请求，使用可控时钟及相互独立的设备目录。"""
    path = os.path.join(root, name)
    os.makedirs(path, exist_ok=True)
    c = Comm(path, personas, now=lambda: clock[0], chat_fn=lambda *_a, **_k: "")
    c.set_cfg(persona="Claudia.txt", split_send=False)
    c._load_schedule()
    c.state_fn = lambda: "break"
    c.break_end_fn = lambda: clock[0].replace(hour=10, minute=30)
    return c


def begin(c, cid):
    """直接准备已经开始的联合行动，不涉及邀请或真实合约。"""
    t = clock[0]
    c._joint_set({"id": cid, "cid": cid, "pid": c.pid, "state": "run", "terms": ["deep1"],
                  "began": (t - timedelta(hours=3)).isoformat(), "start": (t - timedelta(hours=3)).isoformat(),
                  "until": t.isoformat(), "end": t.isoformat()}, replace=True)


def current(c, t):
    return routine.status_in(c._rt_segs(t - timedelta(hours=1), t + timedelta(hours=1)), t)


with patch("routine.routine_store.generated_rest", return_value=None):
    c = make("one")
    begin(c, "complete")
    assert c.joint_ended("complete", "completed", 1800)
    assert c._joint()["rest_until"] == clock[0].replace(minute=30).isoformat()
    assert not c.joint_ended("complete", "completed", 1800), "同一合约只能到账一次"
    clock[0] = clock[0].replace(minute=45)
    assert current(c, clock[0])["kind"] == "rest"
    clock[0] = clock[0].replace(hour=11, minute=0)
    assert current(c, clock[0])["kind"] == "focus" and current(c, clock[0])["start"] == clock[0]
    doc = c.sync_doc()
    c.flush()
    clone = make("two")
    clone.apply_doc(doc)
    clone.apply_doc(doc)
    assert current(clone, clock[0]) == current(c, clock[0]), "重同步不重复发奖"
    reload = make("one")
    assert current(reload, clock[0]) == current(c, clock[0]), "重启不丢接续日程"

    # 失败时没有共同休息：20 分钟填到循环结束，5 分钟保留。
    clock[0] = clock[0].replace(hour=11, minute=40)
    c.state_fn = lambda: "idle"
    begin(c, "failed")
    assert c.joint_ended("failed", "failed", 1500)
    assert not c.joint_live()
    assert current(c, clock[0])["kind"] == "rest"
    at_noon = clock[0].replace(hour=12, minute=0)
    assert current(c, at_noon)["kind"] == "idle"
    after = clock[0].replace(hour=15, minute=20)
    assert current(c, after)["kind"] == "rest"
    resumed = after.replace(minute=23)
    assert current(c, resumed)["kind"] == "focus"
    assert all(x.get("id") != "failed" for x in c._rt_store_get().recent_contracts(resumed, 20)), "失败不冒充完成记录"

    # 新邀请会替换 joint；结转仍需通过历史事件随设备同步。
    c._joint_set({"id": "next", "pid": c.pid, "state": "yes", "terms": [], "start": resumed.isoformat(),
                  "end": (resumed + timedelta(hours=3)).isoformat()}, replace=True)
    clock[0] = after
    clone.apply_doc(c.sync_doc())
    assert current(clone, after) == current(c, after)
    assert any(x["id"] == "failed" for x in clone.cfg["joint"]["rest_history"])

    # 提前结束共同休息后，自己的奖励立即接续，不跟随用户下一次开始的休息。
    clock[0] = clock[0].replace(hour=10, minute=0)
    short = make("short")
    begin(short, "short")
    assert short.joint_ended("short", "completed", 1800)
    clock[0] = clock[0].replace(minute=10)
    short.state_fn = lambda: "idle"
    assert short.joint_break_ended()
    assert current(short, clock[0].replace(minute=35))["kind"] == "rest"
    assert current(short, clock[0].replace(minute=40))["kind"] == "focus"
    assert not short.joint_live()
    for x in (c, clone, reload, short):
        x.flush()

print("routine_reward_check OK")
