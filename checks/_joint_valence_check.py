"""联合行动真实完成后的效价奖励、对象归属、跨天、重启与同步去重。"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempstore
tempstore.session("joint_valence_check")
from comm import Comm
from comm.comm_builtin import BUILTIN_PERSONAS
from comm.comm_interaction import interaction_norm
from comm.comm_rules import valence_event, valence_rules


root = tempstore.mkdtemp()
clock = [datetime(2026, 11, 20, 23, 59)]
calls = []
declaration = "[valence:events]\njoint_completed:30\n[/valence:events]\n"


def make(key, text=declaration):
    """使用隔离存档与可控时钟，禁止发真实请求。"""
    path = os.path.join(root, key)
    personas = os.path.join(path, "personas")
    os.makedirs(personas, exist_ok=True)
    for name, body in (("Claudia.txt", "Claudia"), ("Other.txt", "Other"),
                       ("Claudia_templates.txt", text)):
        with open(os.path.join(personas, name), "w", encoding="utf-8") as f:
            f.write(body)
    c = Comm(path, personas, now=lambda: clock[0], chat_fn=lambda *a, **k: calls.append(a) or "")
    c.set_cfg(persona="Claudia.txt", split_send=False)
    c.state_fn = lambda: "idle"
    return c


def begin(c, cid="joint"):
    """准备真实进行中的联合行动，不触发邀请请求。"""
    c._joint_set({"id": cid, "cid": cid, "pid": c.pid, "state": "run", "terms": [],
                  "began": (clock[0] - timedelta(hours=3)).isoformat(),
                  "start": (clock[0] - timedelta(hours=3)).isoformat(),
                  "end": clock[0].isoformat(), "until": clock[0].isoformat()}, replace=True)


def value(c, owner=None):
    return interaction_norm(c._state_of(owner or c.pid).get("interaction"))["valence"]


assert valence_event(declaration, "joint_completed") == 30
for invalid in ("", declaration.replace("30", "-1"), declaration.replace("30", "101"),
                declaration.replace("30", "X"), declaration + declaration,
                declaration.replace("joint_completed:30", "joint_completed:30\njoint_completed:30")):
    assert valence_event(invalid, "joint_completed") == 0
template = BUILTIN_PERSONAS["Claudia_templates.txt"]
assert valence_event(template, "joint_completed") == 30
assert "joint_completion" not in valence_rules(template), "真实奖励不能作为模型条件重复申报"

c = make("completed")
assert not c.joint_ended("solo", "completed") and value(c) == 0
begin(c)
clock[0] += timedelta(minutes=2)
assert not c.joint_ended("other-contract", "completed") and value(c) == 0
assert c.joint_ended("joint", "completed") and value(c) == 30
assert not c.joint_ended("joint", "completed") and value(c) == 30
assert not calls, "完成奖励不依赖模型请求"
doc = c.sync_doc()
c.flush()
peer = make("peer")
peer.apply_doc(doc)
peer.apply_doc(doc)
assert value(peer) == 30 and not peer.joint_ended("joint", "completed")
reload = make("completed")
assert value(reload) == 30 and not reload.joint_ended("joint", "completed")

for result in ("failed", "abandoned", "idle", "offline"):
    item = make(result)
    begin(item)
    assert item.joint_ended("joint", result) and value(item) == 0

foreign = make("foreign")
owner = foreign.pid
begin(foreign)
foreign.set_cfg(persona="Other.txt")
assert foreign.joint_ended("joint", "completed")
assert value(foreign, owner) == 30 and value(foreign) == 0

for key, text, expected in (("custom", declaration.replace("30", "17"), 17),
                            ("unset", "# no events", 0), ("disabled", declaration.replace("30", "0"), 0)):
    item = make(key, text)
    begin(item)
    assert item.joint_ended("joint", "completed") and value(item) == expected, (key, value(item))

limited = make("limited")
at = limited._ms()
limited._interaction_set(dict(interaction_norm({}), valence=90, valence_initialized=True,
                               valence_initial_set=True, valence_decay_at=at,
                               valence_chain_until=at + 10000, burst_until=at))
begin(limited)
assert limited.joint_ended("joint", "completed") and value(limited) == 100
penalized = make("offense")
penalized._interaction_set(dict(interaction_norm({}), valence_initialized=True,
    valence_initial_set=True, valence_decay_at=at,
    offenses=[{"id": "old", "at": at, "ts": at, "reason": "test", "forgiven": False}]))
begin(penalized)
assert penalized.joint_ended("joint", "completed") and value(penalized) == 24
print("joint_valence_check OK")
