# -*- coding: utf-8 -*-
"""情景房间系统自检：默认地图、热点解析、成员进出、双手槽位与束缚、定向听觉隔离、AI 建房、NPC 自动对话与 RoomPage 渲染。"""

import os
import random
import sys
import tempfile
import tkinter as tk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import comm as comm_mod
from comm.comm_room import (
    RoomStore,
    parse_actor_reply,
    parse_generated_room,
    pick_auto_speaker,
    render_room_map,
)
from pages.room_page import RoomPage


def main():
    with tempfile.TemporaryDirectory() as tmp:
        # 1. 默认房间与热点解析
        store = RoomStore(tmp)
        assert {"my_home", "partner_home", "lounge"} <= set(store.rooms), "缺少默认内置房间"
        assert store.current_room_id() == "my_home"

        store.join_room("alice", "my_home", "Alice")
        store.join_room("bob", "my_home", "Bob")
        store.join_room("carol", "lounge", "Carol")
        assert set(store.occupants_in("my_home")) == {"user", "alice", "bob"}
        assert set(store.occupants_in("lounge")) == {"carol"}

        text, hotspots = render_room_map(
            store.rooms["my_home"],
            [("user", "我"), ("alice", "Alice"), ("bob", "Bob")],
        )
        assert "[钢琴]" in text and "[书架]" in text and "[@Alice]" in text
        fac_actions = {h["action"] for h in hotspots if h["kind"] == "facility"}
        actor_ids = {h["id"] for h in hotspots if h["kind"] == "actor"}
        assert {"piano", "bookshelf", "desk", "board", "door"} <= fac_actions
        assert {"user", "alice", "bob"} == actor_ids

        # 2. 双手槽位与束缚规则
        assert store.available_hands() == ["left", "right"]
        assert store.queue_hand_action("left", "alice", "Alice", "肩膀", "轻轻拍了拍") == ""
        assert store.available_hands() == ["right"]
        assert store.queue_hand_action("left", "alice", "Alice", "脸颊", "重复使用左手") != ""
        assert store.queue_hand_action("right", "bob", "Bob", "手掌", "递过去一杯茶") == ""
        assert store.available_hands() == []
        assert len(store.pending_actions) == 2

        store.set_hand_bound("left", True, note="丝带")
        assert store.hands["left"]["state"] == "bound"
        store.reset_turn_hands()
        assert store.hands["left"]["state"] == "bound", "回合重置不能解除束缚"
        assert store.hands["right"]["state"] == "free"
        assert store.available_hands() == ["right"]
        assert store.queue_hand_action("left", "alice", "Alice", "头顶", "被束缚时不能用") != ""

        store.set_hand_bound("both", True, note="绳结")
        assert store.available_hands() == []
        store.set_hand_bound("both", False)
        assert store.available_hands() == ["left", "right"]

        # 3. 严格定向听觉隔离
        store.append_log("my_home", "user", "我", ["alice"], "say", "只告诉 Alice 的秘密")
        alice_seen = [e["text"] for e in store.visible_logs_for("alice", "my_home")]
        bob_seen = [e["text"] for e in store.visible_logs_for("bob", "my_home")]
        assert "只告诉 Alice 的秘密" in alice_seen
        assert "只告诉 Alice 的秘密" not in bob_seen, "未指定的角色不应听到定向消息"

        store.append_log("my_home", "alice", "Alice", ["bob"], "say", "我们俩偷偷商量一下")
        user_view = store.user_display_logs("my_home", {"user": "我", "alice": "Alice", "bob": "Bob"})
        assert all("我们俩偷偷商量一下" not in e["text"] for e in user_view), "未指定用户时不应泄露 NPC 私聊正文"
        assert any(e.get("redacted") for e in user_view), "应显示未听清的脱敏提示"

        # 4. 持久化校验
        store2 = RoomStore(tmp)
        assert set(store2.occupants_in("my_home")) == {"user", "alice", "bob"}
        assert store2.leave_room("bob", "Bob") is True
        assert "bob" not in store2.occupants_in("my_home")

        # 5. AI 字符画地图生成解析与回复解析
        raw_reply = '''```json
        {
          "name": "深夜天文台",
          "theme": "星空观测",
          "ambience": "穹顶缓缓打开，银河清晰可见。",
          "ascii_map": [
            "┌──────────[穹顶]──────────┐",
            "│  [天文望远镜]   [星图桌] │",
            "│                          │",
            "└───────────[门]───────────┘"
          ]
        }
        ```'''
        gen = parse_generated_room(raw_reply, "星空观测")
        assert gen is not None and gen["name"] == "深夜天文台"
        toks = {f["token"] for f in gen["facilities"]}
        assert {"[穹顶]", "[天文望远镜]", "[星图桌]", "[门]"} <= toks

        parsed = parse_actor_reply('{"to": ["alice"], "action": "轻笑", "speech": "看那边", "bind_hands": "left"}',
                                   {"user", "alice", "bob"})
        assert parsed["to"] == ["alice"] and parsed["bind_hands"] == "left"

        # 6. NPC 自动对话概率挑选
        rng = random.Random(42)
        assert pick_auto_speaker(store2, "my_home", set(), False, 100, rng=rng) is None
        assert pick_auto_speaker(store2, "my_home", set(), True, 0, rng=rng) is None
        assert pick_auto_speaker(store2, "my_home", set(), True, 100, rng=rng) == "alice"
        assert pick_auto_speaker(store2, "my_home", {"alice"}, True, 100, rng=rng) is None

        # 7. RoomPage tkinter UI 冒烟与设施联动
        personas_dir = os.path.join(tmp, "personas")
        os.makedirs(personas_dir, exist_ok=True)
        c = comm_mod.Comm(tmp, personas_dir)
        assert c.cfg.get("room_npc_chat") is False
        assert int(c.cfg.get("room_npc_interval")) == 30
        assert int(c.cfg.get("room_npc_prob")) == 30

        root = tk.Tk()
        root.withdraw()
        try:
            called = []
            page = RoomPage(
                root, c, tmp,
                on_music=lambda: called.append("music"),
                on_coread=lambda: called.append("coread"),
            )
            page.pack(fill="both", expand=True)
            root.update_idletasks()
            piano_hs = next(h for h in page._hotspots if h["action"] == "piano")
            book_hs = next(h for h in page._hotspots if h["action"] == "bookshelf")
            page._on_hotspot_click(piano_hs)
            page._on_hotspot_click(book_hs)
            assert called == ["music", "coread"], f"设施联动回调异常: {called}"
        finally:
            root.destroy()

    print("room check OK")


if __name__ == "__main__":
    main()
