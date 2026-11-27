# -*- coding: utf-8 -*-
"""情景房间系统自检：分人设专属房间、钥匙与内部反锁、按条数解锁部位、定向听觉隔离、AI 建房、NPC 自主移动/对话与 RoomPage 渲染。"""

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
    part_allowed,
    persona_room_id,
    pick_auto_speaker,
    render_room_map,
)
from pages.room_page import RoomPage


def main():
    with tempfile.TemporaryDirectory() as tmp:
        # 1. 默认房间、分人设专属房间与默认出生点（不在我的房间）
        store = RoomStore(tmp)
        assert {"my_home", "lounge"} <= set(store.rooms)
        assert "partner_home" not in store.rooms, "不应再存在模糊的通用 partner_home"
        assert store.current_room_id() == "my_home"

        store.sync_personas([("Claudia.txt", "Claudia"), ("Gemina.txt", "Gemina")])
        c_rid = persona_room_id("Claudia.txt")
        g_rid = persona_room_id("Gemina.txt")
        assert c_rid in store.rooms and g_rid in store.rooms
        assert store.occupants["Claudia.txt"] == c_rid, "人设默认应在自己房间，而非我的房间"
        assert store.occupants["Gemina.txt"] == g_rid
        assert store.occupants["user"] == "my_home"

        # 新建人设自动建房
        store.sync_personas([("Claudia.txt", "Claudia"), ("Gemina.txt", "Gemina"), ("NewOne.txt", "NewOne")])
        n_rid = persona_room_id("NewOne.txt")
        assert n_rid in store.rooms and store.occupants["NewOne.txt"] == n_rid

        # 2. 钥匙分发、公共空间与从内部反锁（有钥匙也打不开）
        assert store.has_key("user", "my_home") and not store.has_key("user", c_rid)
        assert store.has_key("Claudia.txt", c_rid)
        assert store.enter_room("user", c_rid, "我") != "", "无钥匙不能直接进 Claudia 的房间"
        assert store.enter_room("user", "lounge", "我") == "", "公共客厅无需钥匙谁都能进"

        # Claudia 把钥匙分给用户后，用户可进入；若 Claudia 从内部反锁，有钥匙也打不开
        assert store.grant_key(c_rid, "Claudia.txt", "user", "Claudia", "我") == ""
        assert store.has_key("user", c_rid)
        assert store.set_room_lock(c_rid, "Claudia.txt", True, "Claudia") == ""
        assert store.enter_room("user", c_rid, "我") != "", "门从内部反锁后，即使有钥匙也不能进入"
        assert store.set_room_lock(c_rid, "Claudia.txt", False, "Claudia") == ""
        assert store.enter_room("user", c_rid, "我") == "", "解除反锁后凭钥匙可以进入"

        # 角色自主移动到公共客厅
        assert store.enter_room("Gemina.txt", "lounge", "Gemina") == ""
        assert store.occupants["Gemina.txt"] == "lounge"

        # 用户创建私有场景默认由用户持有钥匙
        custom = store.add_room({
            "name": "秘密天文台",
            "theme": "观星",
            "ascii_map": ["┌────[望远镜]────┐", "│               │", "└──────[门]─────┘"],
        }, creator_id="user", is_public=False)
        assert store.has_key("user", custom["id"]) and not store.has_key("Claudia.txt", custom["id"])

        # 3. 热点解析
        text, hotspots = render_room_map(
            store.rooms[c_rid],
            [("user", "我"), ("Claudia.txt", "Claudia")],
        )
        assert "[钢琴]" in text and "[书架]" in text and "[@Claudia]" in text
        assert {"piano", "bookshelf", "desk", "door"} <= {h["action"] for h in hotspots if h["kind"] == "facility"}

        # 4. 按条数解锁部位 + 双手槽位与束缚规则
        assert part_allowed("肩膀", 0) == (True, 0)
        assert part_allowed("手掌 / 指尖", 49) == (False, 50)
        assert part_allowed("手掌 / 指尖", 50) == (True, 50)
        assert part_allowed("脸颊", 119) == (False, 120)
        assert part_allowed("脸颊", 120) == (True, 120)
        assert part_allowed("拥抱 / 拉近", 199) == (False, 200)
        assert part_allowed("拥抱 / 拉近", 200) == (True, 200)
        assert part_allowed("自定义部位", 199) == (False, 200)

        assert store.queue_hand_action("left", "Claudia.txt", "Claudia", "脸颊", "轻碰", msg_count=30) != ""
        assert store.queue_hand_action("left", "Claudia.txt", "Claudia", "肩膀", "轻轻拍了拍", msg_count=30) == ""
        assert store.queue_hand_action("right", "Claudia.txt", "Claudia", "衣袖 / 衣角", "拉一下", msg_count=30) == ""
        assert store.available_hands() == []

        store.set_hand_bound("left", True, note="丝带")
        store.reset_turn_hands(advance_turn=True)
        assert store.turn_no == 1
        assert store.hands["left"]["state"] == "bound" and store.hands["right"]["state"] == "free"
        store.set_hand_bound("both", False)

        # 5. 严格定向听觉隔离 & 房间互动条数累计
        store.enter_room("Gemina.txt", c_rid, "Gemina", force=True)
        before_cnt = store.room_msg_count("Claudia.txt")
        store.append_log(c_rid, "user", "我", ["Claudia.txt"], "say", "只告诉 Claudia 的秘密")
        assert store.room_msg_count("Claudia.txt") == before_cnt + 1
        assert store.room_msg_count("Gemina.txt") == 0
        c_seen = [e["text"] for e in store.visible_logs_for("Claudia.txt", c_rid)]
        g_seen = [e["text"] for e in store.visible_logs_for("Gemina.txt", c_rid)]
        assert "只告诉 Claudia 的秘密" in c_seen and "只告诉 Claudia 的秘密" not in g_seen

        store.append_log(c_rid, "Claudia.txt", "Claudia", ["Gemina.txt"], "say", "我们俩悄悄商量")
        user_view = store.user_display_logs(c_rid, {"user": "我", "Claudia.txt": "Claudia", "Gemina.txt": "Gemina"})
        assert all("我们俩悄悄商量" not in e["text"] for e in user_view)
        assert any(e.get("redacted") for e in user_view)

        # 6. AI 建房与自主行动回复解析
        gen = parse_generated_room('{"name":"旧书店","ascii_map":["┌──[书架]──┐","└──[门]──┘"]}', "书店")
        assert gen is not None and gen["name"] == "旧书店"
        parsed = parse_actor_reply(
            '{"to":["user"],"action":"起身","speech":"我去客厅等你","move_to":"lounge","lock_door":false,"give_key":{"room_id":"'
            + c_rid + '","to":"user"}}',
            {"user", "Claudia.txt"}, set(store.rooms.keys()),
        )
        assert parsed["move_to"] == "lounge" and parsed["lock_door"] is False and parsed["give_key"]["to"] == "user"

        # 7. NPC 自动对话与跨房自主行动挑选
        rng = random.Random(7)
        assert pick_auto_speaker(store, c_rid, set(), False, 100, rng=rng, include_other_rooms=True) is None
        picked = pick_auto_speaker(store, c_rid, set(), True, 100, rng=rng, include_other_rooms=True)
        assert picked is not None and picked[0] != "user"

        # 8. RoomPage tkinter UI 冒烟与设施联动
        personas_dir = os.path.join(tmp, "personas")
        os.makedirs(personas_dir, exist_ok=True)
        c = comm_mod.Comm(tmp, personas_dir)
        assert c.msg_total_for("Claudia.txt") >= 0

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
            assert called == ["music", "coread"]
        finally:
            root.destroy()

    print("room check OK")


if __name__ == "__main__":
    main()
