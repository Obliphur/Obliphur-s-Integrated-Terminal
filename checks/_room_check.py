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
    ATTENTION_ENTER,
    ATTENTION_HALF_MIN,
    ATTENTION_LEAVE,
    ATTENTION_WAKE,
    RoomStore,
    auto_step_plan,
    display_width,
    parse_actor_reply,
    parse_generated_room,
    part_allowed,
    persona_room_id,
    position_interactions_for,
    render_room_map,
    summon_targets,
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

        # 3. 热点解析与字符画严格等宽对齐 + 椅子/墙边位置互动
        text, hotspots = render_room_map(
            store.rooms[c_rid],
            [("user", "Madam Chen"), ("Claudia.txt", "Claudia")],
        )
        widths = {display_width(line) for line in text.splitlines()}
        assert len(widths) == 1, f"字符画各行显示宽度必须完全一致，实际: {widths}"
        assert "[钢琴]" in text and "[书架]" in text and "[椅子]" in text and "[墙边]" in text and "[@Claudia]" in text
        assert {"piano", "bookshelf", "desk", "door", "position"} <= {h["action"] for h in hotspots if h["kind"] == "facility"}

        assert store.move_position("user", "墙边", "Madam Chen") is True
        wall_labels = [lbl for _id, lbl, _req in position_interactions_for(store.position_of("user"), "房间中央")]
        assert "按在墙上 / 抵在墙边" in wall_labels
        assert store.move_position("user", "椅子", "Madam Chen") is True
        chair_labels = [lbl for _id, lbl, _req in position_interactions_for(store.position_of("user"), "房间中央")]
        assert "按在椅子上" in chair_labels and "坐到身旁 / 腿上" in chair_labels

        # 牵手带人出去（需同位置 + 100 条消息）& 言语跟随 & 被对方带出去
        assert store.hold_hand("user", "left", "Claudia.txt", "Madam Chen", "Claudia", msg_count=100) != "", "不在同一位置时不应允许牵手"
        assert store.queue_hand_action("user", "left", "Claudia.txt", "Claudia", "肩膀", "隔空拍肩", msg_count=100) != "", "不在同一位置时不应允许手部动作"
        assert store.move_position("user", store.position_of("Claudia.txt"), "Madam Chen") is True
        assert store.hold_hand("user", "left", "Claudia.txt", "Madam Chen", "Claudia", msg_count=99) != "", "不足 100 条消息不应允许牵手带走"
        assert store.hold_hand("user", "left", "Claudia.txt", "Madam Chen", "Claudia", msg_count=100) == ""
        assert store.hand_state("user", "left") == "holding" and "握住" in store.hand_label("user", "left")
        assert store.hand_state("Claudia.txt", "left") == "held", "被牵的那只手也要更新成被握住"
        assert "被" in store.hand_label("Claudia.txt", "left")
        assert store.enter_room("user", "lounge", "Madam Chen") == ""
        assert store.occupants["Claudia.txt"] == "lounge", "牵手切换房间应把 Claudia 一起带出来"
        assert store.hold_hand("Gemina.txt", "left", "user", "Gemina", "Madam Chen", msg_count=100) != "", "同一只手不能被两个人同时牵"
        assert store.queue_hand_action("Claudia.txt", "left", "Gemina.txt", "Gemina", "肩膀", "被牵着还想抬手", msg_count=100) != "", "被牵着的手不能做新动作"
        store.release_hand("user", "left", {"user": "Madam Chen", "Claudia.txt": "Claudia"})
        assert store.hand_state("user", "left") == "free" and "自由" in store.hand_label("user", "left")
        assert store.hand_state("Claudia.txt", "left") == "free", "松手要两边一起恢复"
        store.set_following("Claudia.txt", "user", "Claudia", "Madam Chen")
        assert store.enter_room("user", "my_home", "Madam Chen") == ""
        assert store.occupants["Claudia.txt"] == "my_home", "跟随状态下切换房间应一起跟着走"
        store.set_following("Claudia.txt", None, "Claudia")
        assert store.enter_room("Claudia.txt", "lounge", "Claudia", bring_ids=["user"]) == ""
        assert store.occupants["user"] == "lounge", "对方 bring_user 移动时应把用户一起带出去"
        assert store.enter_room("user", c_rid, "Madam Chen") == ""
        assert store.enter_room("Claudia.txt", c_rid, "Claudia") == ""

        # 4. 按条数解锁部位 + 双手槽位与束缚规则（用户与角色是平等实体）
        assert part_allowed("肩膀", 0) == (True, 0)
        assert part_allowed("手掌 / 指尖", 49) == (False, 50)
        assert part_allowed("手掌 / 指尖", 50) == (True, 50)
        assert part_allowed("对方的左手", 49) == (False, 50)
        assert part_allowed("对方的右手", 50) == (True, 50)
        assert part_allowed("脸颊", 119) == (False, 120)
        assert part_allowed("脸颊", 120) == (True, 120)
        assert part_allowed("拥抱 / 拉近", 199) == (False, 200)
        assert part_allowed("拥抱 / 拉近", 200) == (True, 200)
        assert part_allowed("自定义部位", 199) == (False, 200)

        assert store.queue_hand_action("user", "left", "Claudia.txt", "Claudia", "脸颊", "轻碰", msg_count=30) != ""
        assert store.queue_hand_action("user", "left", "Claudia.txt", "Claudia", "肩膀", "轻轻拍了拍", msg_count=30) == ""
        assert store.queue_hand_action("user", "right", "Claudia.txt", "Claudia", "衣袖 / 衣角", "拉一下", msg_count=30) == ""
        assert store.queue_hand_action("user", "left", "Claudia.txt", "Claudia", "目光注视", "", msg_count=0) == "", "动作互动内容允许留空"
        assert store.queue_hand_action("user", "left", "Claudia.txt", "Claudia", "目光注视", "安静地看着她", msg_count=0) == ""
        assert [a["hand"] for a in store.pending_of("user")] == ["left", "right", "gaze"], "左手、右手、目光注视应各自独立并可同时共存"
        assert store.queue_hand_action("user", "both", "Claudia.txt", "Claudia", "肩膀", "双手", msg_count=30) != "", "已移除双手选项"

        # 握住状态的手仍可脱离当前动作作出新动作
        assert store.hold_hand("user", "left", "Claudia.txt", "Madam Chen", "Claudia", msg_count=100) == ""
        assert "left" in store.available_hands("user")
        assert store.queue_hand_action("user", "left", "Claudia.txt", "Claudia", "肩膀", "松开手拍了拍肩", msg_count=100) == ""
        assert store.hand_state("user", "left") == "free" and store.hand_state("Claudia.txt", "left") == "free"

        # 双手都被束缚时，独立目光仍可用
        store.set_hand_bound("user", "both", True, note="丝带")
        assert store.available_hands("user") == []
        assert store.queue_hand_action("user", "left", "Claudia.txt", "Claudia", "肩膀", "尝试抬手", msg_count=100) != ""
        assert store.queue_hand_action("user", "gaze", "Claudia.txt", "Claudia", "目光注视", "直视对方", msg_count=0) == ""
        store.set_hand_bound("user", "right", False)
        store.reset_turn_hands("user")
        assert store.hand_state("user", "left") == "bound" and store.hand_state("user", "right") == "free"
        store.set_hand_bound("user", "both", False)

        # 4.1 角色也能对用户做同样的动作，并且会改动用户的手部状态
        assert store.queue_hand_action("Claudia.txt", "left", "user", "Madam Chen", "对方的左手", "牵起你的手", msg_count=100) == ""
        acts_room = store.take_pending("Claudia.txt", "Claudia")
        assert [a["hand"] for a in acts_room] == ["left"]
        assert store.hand_state("Claudia.txt", "left") == "used"
        assert store.hand_state("user", "left") == "used", "角色碰到用户的手时，用户那只手也要更新"

        # 4.2 角色与角色之间同样可以动手（不需要经过用户）
        assert store.enter_room("Gemina.txt", c_rid, "Gemina", force=True) == ""
        store.move_position("Gemina.txt", store.position_of("Claudia.txt"), "Gemina")
        assert store.queue_hand_action("Claudia.txt", "right", "Gemina.txt", "Gemina", "肩膀", "拍了拍她的肩", msg_count=100) == ""
        assert [a["target_id"] for a in store.take_pending("Claudia.txt", "Claudia")] == ["Gemina.txt"]
        assert store.hand_state("Claudia.txt", "right") == "used"
        assert store.hold_hand("Claudia.txt", "left", "Gemina.txt", "Claudia", "Gemina", msg_count=100) == "", "角色之间也能牵手"
        assert store.hand_state("Gemina.txt", "left") == "held" and store.holding_of("Claudia.txt", "left") == "Gemina.txt"
        store.release_hand("Gemina.txt", "left", {"Gemina.txt": "Gemina", "Claudia.txt": "Claudia"})
        assert store.hand_state("Gemina.txt", "left") == "free" and store.holding_of("Claudia.txt", "left") == "", "被牵的一方抽手也要断开配对"

        # 5. 按音量与位置决定谁能听到（轻语仅同位置可听，正常音量全屋可听）
        store.enter_room("Gemina.txt", c_rid, "Gemina", force=True)
        store.move_position("user", "墙边", "Madam Chen")
        store.move_position("Claudia.txt", "墙边", "Claudia")
        store.move_position("Gemina.txt", "椅子", "Gemina")

        before_cnt = store.room_msg_count("Claudia.txt")
        whisper_targets = store.listeners_for_volume(c_rid, "user", "whisper")
        assert set(whisper_targets) == {"user", "Claudia.txt"}, f"轻语应仅同位置的人能听见: {whisper_targets}"
        store.append_log(c_rid, "user", "我", whisper_targets, "say", "只在墙边轻语的秘密", volume="whisper", pos="墙边")
        assert store.room_msg_count("Claudia.txt") == before_cnt + 1
        assert store.room_msg_count("Gemina.txt") == 0
        c_seen = [e["text"] for e in store.visible_logs_for("Claudia.txt", c_rid)]
        g_seen = [e["text"] for e in store.visible_logs_for("Gemina.txt", c_rid)]
        assert "只在墙边轻语的秘密" in c_seen and "只在墙边轻语的秘密" not in g_seen

        # 正常音量全屋都能听到
        normal_targets = store.listeners_for_volume(c_rid, "user", "normal")
        assert normal_targets == ["all"]
        store.append_log(c_rid, "user", "我", normal_targets, "say", "大家都能听见", volume="normal", pos="墙边")
        assert "大家都能听见" in [e["text"] for e in store.visible_logs_for("Gemina.txt", c_rid)]

        # 小屋 ↔ 手机沟通双向上下文互通（遵守听觉隔离，不泄密）
        from comm.comm_room import build_actor_turn_prompt, phone_room_status_note
        c_phone_note = phone_room_status_note(store, "Claudia.txt", "Claudia", {"user": "我", "Claudia.txt": "Claudia", "Gemina.txt": "Gemina"})
        g_phone_note = phone_room_status_note(store, "Gemina.txt", "Gemina", {"user": "我", "Claudia.txt": "Claudia", "Gemina.txt": "Gemina"})
        assert "只在墙边轻语的秘密" in c_phone_note and "大家都能听见" in c_phone_note
        assert "只在墙边轻语的秘密" not in g_phone_note and "大家都能听见" in g_phone_note
        sys_p, turn_msgs = build_actor_turn_prompt(
            "你是 Claudia。", store, store.rooms[c_rid], "Claudia.txt", "Claudia",
            {"user": "我", "Claudia.txt": "Claudia"}, store.visible_logs_for("Claudia.txt", c_rid),
            memory_block="\n\n长期记忆：用户喜欢冰镇香蕉牛奶",
            phone_history="- [22:50] 用户: 刚刚在手机上聊过天",
        )
        assert "冰镇香蕉牛奶" in sys_p and "刚刚在手机上聊过天" in turn_msgs[0]["content"]

        # 两个 NPC 在另一个位置轻语，用户听不清
        store.move_position("Claudia.txt", "椅子", "Claudia")
        npc_whisper = store.listeners_for_volume(c_rid, "Claudia.txt", "whisper")
        assert set(npc_whisper) == {"Claudia.txt", "Gemina.txt"}
        store.append_log(c_rid, "Claudia.txt", "Claudia", npc_whisper, "say", "椅子旁的小声密谋", volume="whisper", pos="椅子")
        user_view = store.user_display_logs(c_rid, {"user": "我", "Claudia.txt": "Claudia", "Gemina.txt": "Gemina"})
        assert all("椅子旁的小声密谋" not in e["text"] for e in user_view)
        assert any(e.get("redacted") for e in user_view)

        # 公开小动作同房全员可见；隐秘身体接触仅双方可见
        store.move_position("user", "椅子", "Madam Chen")
        assert store.queue_hand_action("user", "left", "Claudia.txt", "Claudia", "肩膀", "公开拍拍肩", msg_count=200, secret=False) == ""
        assert store.queue_hand_action("user", "right", "Claudia.txt", "Claudia", "手掌 / 指尖", "桌下偷偷勾手指", msg_count=200, secret=True) == ""
        acts_mix = store.take_pending("user", "我")
        store.append_log(c_rid, "user", "我", ["user", "Claudia.txt"], "action", "", actions=acts_mix, volume="whisper", pos="墙边")
        store.reset_turn_hands("user")
        c_acts = [a["text"] for e in store.visible_logs_for("Claudia.txt", c_rid) for a in e.get("actions") or []]
        g_acts = [a["text"] for e in store.visible_logs_for("Gemina.txt", c_rid) for a in e.get("actions") or []]
        assert "公开拍拍肩" in c_acts and "桌下偷偷勾手指" in c_acts
        assert "公开拍拍肩" in g_acts and "桌下偷偷勾手指" not in g_acts, "公开动作应全房可见，隐秘接触旁人不可见"

        # 动作产生 V / T 数值（设施不产生，隐秘接触张力更高）
        from comm.comm_room import room_action_vt
        assert room_action_vt("设施") == ("", 0, 0)
        c_gaze, v_gaze, t_gaze = room_action_vt("目光注视")
        c_ear, v_ear, t_ear = room_action_vt("耳畔", secret=False)
        _, _, t_ear_sec = room_action_vt("耳畔", secret=True)
        assert c_gaze and v_gaze > 0 and t_gaze > 0
        assert t_ear > t_gaze and t_ear_sec > t_ear

        # 6. AI 建房与自主行动 / silent 回复解析
        gen = parse_generated_room('{"name":"旧书店","ascii_map":["┌──[书架]──┐","└──[门]──┘"]}', "书店")
        assert gen is not None and gen["name"] == "旧书店"
        parsed = parse_actor_reply(
            '{"silent":false,"volume":"whisper","action":"起身","speech":"我去客厅等你","move_to":"lounge","lock_door":false,"give_key":{"room_id":"'
            + c_rid + '","to":"user"}}',
            {"user", "Claudia.txt"}, set(store.rooms.keys()),
        )
        assert parsed["silent"] is False and parsed["volume"] == "whisper" and parsed["move_to"] == "lounge"
        hold_parsed = parse_actor_reply('{"hold_hand":"right"}', {"user", "Claudia.txt"})
        assert hold_parsed["hold_hand"] == "right", "模型牵手意图应保留左右手选择"
        assert parse_actor_reply('{"hold_hand":"invalid"}', {"user"})["hold_hand"] is None
        silent_parsed = parse_actor_reply('{"silent":true,"speech":"不应说出口"}', {"user", "Claudia.txt"})
        assert silent_parsed["silent"] is True and silent_parsed["speech"] == ""
        act_parsed = parse_actor_reply(
            '{"speech":"","actions":[{"hand":"left","target":"user","part":"手掌 / 指尖","text":"牵住你的手"},'
            '{"hand":"gaze","part":"目光注视","text":"看着你"},'
            '{"hand":"left","target":"不存在的人","part":"肩膀"},'
            '{"hand":"both","part":"肩膀"}]}',
            {"user", "Claudia.txt"},
        )
        assert [a["hand"] for a in act_parsed["actions"]] == ["left", "gaze"], "无效手部与越界目标应被丢掉"
        assert act_parsed["actions"][1]["part"] == "目光注视"
        assert parse_actor_reply('{"actions":[]}', {"user"})["actions"] == []

        # 7. 专注/睡眠自动反锁自己的房间、空闲系统随机活动（不发言）与注意力调度
        assert store.enter_room("user", "lounge", "我") == ""
        store.sync_schedule_locks({"Claudia.txt": "focus", "Gemina.txt": "idle"},
                                  {"Claudia.txt": "Claudia", "Gemina.txt": "Gemina"})
        assert store.occupants["Claudia.txt"] == c_rid
        assert store.rooms[c_rid]["locked_inside"] is True and store.rooms[c_rid]["locked_reason"] == "focus"
        assert store.enter_room("user", c_rid, "我") != "", "专注反锁期间即使有钥匙也进不去"
        store.sync_schedule_locks({"Claudia.txt": "idle", "Gemina.txt": "idle"},
                                  {"Claudia.txt": "Claudia", "Gemina.txt": "Gemina"})
        assert store.rooms[c_rid]["locked_inside"] is False, "结束专注后自动解除反锁"

        # 注意力在阈值以下 -> 系统随机移动或活动，不发言
        import time as _time
        now_ms = int(_time.time() * 1000) + 20 * 60 * 1000
        assert not store.awake("Gemina.txt", now_ms=now_ms)
        logs_before = len(store.logs)
        res = store.perform_idle_system_step("Gemina.txt", "Gemina", now_ms=now_ms, rng=random.Random(3))
        assert res.startswith(("activity:", "move:")), f"应执行本地随机活动或移动: {res}"
        new_logs = store.logs[logs_before:]
        assert new_logs and all(e["kind"] in ("sys", "move") for e in new_logs), "空闲随机活动不应发言"
        if res.startswith("activity:"):
            assert store.activity_label("Gemina.txt", now_ms=now_ms) != ""
            assert store.perform_idle_system_step("Gemina.txt", "Gemina", now_ms=now_ms + 60_000, rng=random.Random(3)) == ""

        # 注意力的半衰期、阈值与抬升
        base = int(_time.time() * 1000)
        store.raise_attention("Gemina.txt", 1.0, now_ms=base)
        assert store.awake("Gemina.txt", now_ms=base)
        half = max(1, int(store.attn_half_min or ATTENTION_HALF_MIN)) * 60_000
        assert abs(store.attention_of("Gemina.txt", now_ms=base + half) - 0.5) < 0.01, "一个半衰期应衰减到一半"
        assert store.attention_of("Gemina.txt", now_ms=base) >= ATTENTION_WAKE
        assert not store.awake("Gemina.txt", now_ms=base + 3 * half), "三个半衰期后应低于阈值"

        # 离开房间扣注意力；走进用户所在的房间加回来（用户走开对留下的人也成立）
        assert store.enter_room("Gemina.txt", store.current_room_id(), "Gemina", force=True) == ""
        store.raise_attention("Gemina.txt", 1.0)
        assert store.attention_of("Gemina.txt") >= 1.0
        assert store.enter_room("Gemina.txt", g_rid, "Gemina", force=True) == ""
        assert abs(store.attention_of("Gemina.txt") - (1.0 - ATTENTION_LEAVE)) < 0.02, "离开房间应扣注意力"
        assert store.enter_room("Gemina.txt", store.current_room_id(), "Gemina", force=True) == ""
        assert abs(store.attention_of("Gemina.txt") - ATTENTION_ENTER) < 0.02, \
            "走进用户所在的房间应把人叫回神来（至少到注意阈值之上）"
        store.raise_attention("Gemina.txt", 1.0)
        assert store.enter_room("user", "my_home", "我") == ""
        assert store.attention_of("Gemina.txt") < 1.0, "用户走开后，留在房间里的人注意力也要掉"

        # 被叫到名字：不依赖「对话窗口」，跨房间也认得出；答不上的先记下来
        nm = {"user": "我", "Claudia.txt": "Claudia", "Gemina.txt": "Gemina"}
        assert summon_targets(store, "Claudia，你在吗？", nm) == ["Claudia.txt"]
        assert summon_targets(store, "有人吗", nm) == []
        assert summon_targets(store, "叫一下 Claudia 和 Gemina", nm) == ["Claudia.txt", "Gemina.txt"], \
            "多人点名应按出现顺序"
        assert summon_targets(store, "小克，过来一下", {"Claudia.txt": "Claudia (小克)"}) == ["Claudia.txt"], \
            "标题里的别名也要认"
        assert store.add_heard("Claudia.txt", "Claudia，你在吗", "lounge", "我")
        assert store.awake("Claudia.txt"), "叫名字应把人叫醒"
        heard = store.take_heard("Claudia.txt")
        assert len(heard) == 1 and heard[0]["text"].startswith("Claudia") and heard[0]["room"] == "lounge"
        assert store.take_heard("Claudia.txt") == []

        # 自动触发：注意力 + 每小时额度决定走模型还是系统；睡觉 / 专注的人跳过
        rng = random.Random(7)
        assert auto_step_plan(store, c_rid, set(), False, 100, rng=rng, include_other_rooms=True) is None
        blocked_ids = {"Claudia.txt"}
        store.auto_turns.pop("Gemina.txt", None)
        store.raise_attention("Gemina.txt", 1.0)
        plan = auto_step_plan(store, c_rid, set(), True, 100, rng=random.Random(1),
                              include_other_rooms=True, blocked_ids=blocked_ids)
        assert plan is not None and plan[0] == "model" and plan[1] == "Gemina.txt", plan
        store.auto_turns["Gemina.txt"] = [int(_time.time() * 1000)] * 8
        assert store.auto_budget_left("Gemina.txt") == 0, "自主开口额度用完后应转系统"
        plan = auto_step_plan(store, c_rid, set(), True, 100, rng=random.Random(1),
                              include_other_rooms=True, blocked_ids=blocked_ids)
        assert plan is not None and plan[0] == "system", plan
        store.auto_turns.pop("Gemina.txt", None)
        store.drop_attention("Gemina.txt", 1.0)
        plan = auto_step_plan(store, c_rid, set(), True, 100, rng=random.Random(1),
                              include_other_rooms=True, blocked_ids=blocked_ids)
        assert plan is not None and plan[0] == "system", "注意力低于阈值时由系统接管"

        # 7.5 房间连接、门的内外侧、音量传播与断开房间的自动清理
        s2 = RoomStore(os.path.join(tmp, "rooms2"))
        s2.sync_personas([("Claudia.txt", "Claudia"), ("Gemina.txt", "Gemina")])
        assert "lounge" in s2.neighbors(c_rid) and c_rid in s2.neighbors("lounge"), "每间卧室都要连着公共客厅"
        assert s2.neighbors(c_rid) == ["lounge"], "卧室默认只通向客厅"
        assert s2.enter_room("user", c_rid, "我") != "", "没有门连着的地方走不过去"
        assert s2.path_between("my_home", c_rid) == ["my_home", "lounge", c_rid], "隔着客厅的房间要经过客厅"
        s2.link_rooms("my_home", c_rid)
        assert c_rid in s2.neighbors("my_home") and "my_home" in s2.neighbors(c_rid), "连接一定是双向的"
        s2.unlink_rooms("my_home", c_rid)
        assert c_rid not in s2.neighbors("my_home")

        # 门只有站在里侧能锁；锁上之后外面的人过不去，里侧的人自己开门才能出去
        assert s2.set_room_lock(c_rid, "user", True, "我") != "", "不在房间里面不能锁门"
        assert s2.grant_key(c_rid, "Claudia.txt", "user", "Claudia", "我") == ""
        assert s2.enter_room("user", "lounge", "我") == ""
        assert s2.enter_room("user", c_rid, "我") == "", "先走到客厅，再进 Claudia 的房间"
        assert s2.set_room_lock(c_rid, "user", True, "我") == ""
        assert s2.door_between(c_rid, "lounge")["locked"] and s2.door_between("lounge", c_rid)["locked"], \
            "一扇门两边都要记成锁上"
        assert s2.enter_room("user", "lounge", "我") == "", "里侧的人自己开门能出去"
        assert not s2.door_between(c_rid, "lounge")["locked"], "自己出门时把锁打开了"
        s2.enter_room("user", c_rid, "我")
        s2.set_room_lock(c_rid, "user", True, "我")
        s2.enter_room("Gemina.txt", "lounge", "Gemina", force=True)
        assert s2.enter_room("Gemina.txt", c_rid, "Gemina") != "", "反锁的门挡得住外面的人"
        s2.set_room_lock(c_rid, "user", False, "我")

        # 走不到客厅的房间自动删除，里面的人回到客厅
        orphan = s2.add_room({"name": "断开的储藏室", "ascii_map": ["┌──[门]──┐", "└────────┘"]},
                             creator_id="user", attach_to="lounge")
        assert orphan["id"] in s2.neighbors("lounge")
        s2.enter_room("Gemina.txt", orphan["id"], "Gemina", force=True)
        s2.unlink_rooms("lounge", orphan["id"])
        dropped = s2.prune_orphans()
        assert orphan["id"] in dropped and orphan["id"] not in s2.rooms
        assert s2.occupants["Gemina.txt"] == "lounge", "被删房间里的人回到公共客厅"

        # 手动删除房间：门一起断掉；内置房间不能删
        spare = s2.add_room({"name": "临时标本室", "ascii_map": ["┌──[门]──┐", "└────────┘"]},
                            creator_id="user", attach_to="lounge")
        assert spare["id"] in s2.neighbors("lounge")
        assert s2.delete_room(spare["id"]) and spare["id"] not in s2.rooms
        assert spare["id"] not in s2.neighbors("lounge") and not s2.delete_room("lounge")

        # 大声呼喊：隔壁听得见并标明来处，再远就听不见；正看书的人会被吵到
        user_room = s2.current_room_id()
        s2.enter_room("Claudia.txt", user_room, "Claudia", force=True)
        s2.activities["Claudia.txt"] = {"label": "看书", "until": int(_time.time() * 1000) + 600_000,
                                        "room_id": user_room}
        shout = s2.append_log(user_room, "user", "我", ["all"], "say", "喂——有人吗", volume="shout")
        assert set(shout.get("spill") or []) == set(s2.neighbors(user_room))
        assert s2.mood_of("Claudia.txt").get("label") == "被吵到", "大声喊会吵到正在看书的人"
        assert any(e.get("from_room") for e in s2.visible_logs_for("Gemina.txt", "lounge")), \
            "隔壁房间要听得见，并标明是从哪传来的"
        assert all("喂——有人吗" not in e.get("text", "") for e in s2.visible_logs_for("Gemina.txt", "my_home")), \
            "隔了两间房就听不见了"
        assert s2.moods and s2.activity_label("Claudia.txt").endswith("被吵到")
        brief = s2.awareness_brief("Gemina.txt")
        assert "【听觉与空间】" in brief and "隔壁房间" in brief and "大声呼喊" in brief
        assert "轻语" in brief and "正常音量" in brief

        personas_dir = os.path.join(tmp, "personas")
        os.makedirs(personas_dir, exist_ok=True)
        c = comm_mod.Comm(tmp, personas_dir)
        assert c.msg_total_for("Claudia.txt") >= 0
        assert int(c.cfg.get("room_npc_idle_min")) == 15
        note = c.room_status_note()
        assert "[OIT 小屋状态（手机聊天联动）]" in note and "oit_room_action" in note
        res_act = c.execute_phone_room_action({
            "move_to": "lounge",
            "position": "椅子",
            "give_key_room": persona_room_id(c.persona_name()),
        })
        assert res_act["ok"] and res_act["current_room_id"] == "lounge" and res_act["position"] == "椅子"
        assert c.room_store.has_key("user", persona_room_id(c.persona_name())), "手机聊天中分钥匙应同步到共享 RoomStore"
        assert "hands" in res_act and "user_hands" in res_act, "手机工具结果应带回双方手部状态"

        # 手机聊天里也能对用户做与界面相同的手部动作 / 牵手（同房间同位置 + 熟络度达标）
        cur = c.persona_name()
        c.persona_status_kind = lambda _pid: "idle"      # 避开当前时段专注自动回房，专心测动作链路
        assert c.room_store.enter_room("user", "lounge", "我") == ""
        c.room_store.move_position("user", "椅子", "我")
        c.room_store.room_counts[cur] = 200
        res_hand = c.execute_phone_room_action({
            "move_to": "lounge",
            "position": "椅子",
            "hold_hand": "left",
            "actions": [{"hand": "right", "target": "user", "part": "肩膀", "text": "拍拍你的肩"}],
        })
        assert res_hand["ok"], res_hand
        assert c.room_store.hand_state(cur, "left") == "holding", res_hand
        assert c.room_store.hand_state("user", "left") == "held"
        assert c.room_store.hand_state(cur, "right") == "used"
        rel = c.execute_phone_room_action({"hold_hand": "release"})
        assert rel["ok"] and "松开" in "".join(rel["results"]), rel
        assert c.room_store.hand_state("user", "left") == "free"

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
            assert not hasattr(page, "btn_lock"), "反锁房门已移入 [门] 菜单，不应占顶栏按钮"
            st_web = page._build_web_state()
            assert set(st_web["hands"]) == {"left", "right"} and "label" in st_web["hands"]["left"]
            assert isinstance(st_web["pending_actions"], list)
            assert all("hands" in o for o in st_web["occupants"]), "网页状态里每个在场实体都要带双手状态"
            piano_hs = next(h for h in page._hotspots if h["action"] == "piano")
            book_hs = next(h for h in page._hotspots if h["action"] == "bookshelf")
            page._on_hotspot_click(piano_hs)
            page._on_hotspot_click(book_hs)
            assert called == ["music", "coread"]

            # 9. WebView2 本地服务状态接口与动作闭环验证（含 token 鉴权）
            if page._use_webview and page._web_server is not None:
                import json
                import urllib.error
                import urllib.request

                url = page._web_server.start()
                base = url.split("?")[0]
                tok = page._web_server.token
                # 无 token 应 403
                try:
                    urllib.request.urlopen(base + "api/state", timeout=3)
                    raise AssertionError("缺少 token 应拒绝访问")
                except urllib.error.HTTPError as e:
                    assert e.code == 403

                # 后台线程请求 /api/state 和 /api/action，同时主线程泵 Tk 事件循环
                import threading
                res_box = {}

                def client_work():
                    try:
                        with urllib.request.urlopen(f"{base}api/state?token={tok}", timeout=3) as r:
                            res_box["state"] = json.loads(r.read().decode("utf-8"))
                        req = urllib.request.Request(
                            f"{base}api/action?token={tok}",
                            data=json.dumps({"type": "set_volume", "volume": "whisper"}).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urllib.request.urlopen(req, timeout=3) as r:
                            res_box["action"] = json.loads(r.read().decode("utf-8"))
                    except Exception as exc:
                        res_box["err"] = exc

                t = threading.Thread(target=client_work, daemon=True)
                t.start()
                for _ in range(100):
                    if not t.is_alive():
                        break
                    root.update()
                    _time.sleep(0.02)
                t.join(timeout=2)
                assert "err" not in res_box, f"WebView2 服务请求失败: {res_box.get('err')}"
                assert res_box["state"]["ok"] and res_box["state"]["state"]["map_text"]
                assert res_box["action"]["ok"] and res_box["action"]["state"]["volume"] == "whisper"
        finally:
            root.destroy()

    print("room check OK")


if __name__ == "__main__":
    main()
