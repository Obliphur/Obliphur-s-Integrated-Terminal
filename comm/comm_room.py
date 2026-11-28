# -*- coding: utf-8 -*-
"""古早文字游戏房间系统（PC 优先）：等宽对齐字符画地图、分人设专属房间、钥匙与内部反锁、牵手/跟随带人、位置互动、按条数解锁部位与 NPC 自主活动。"""

import copy
import json
import os
import random
import re
import time
import unicodedata
import uuid

from i18n import tr

ROOM_FILE = "comm_rooms.json"
MAX_ROOM_LOGS = 500
CUSTOM_PART_MIN_MSGS = 200
HOLD_HAND_MIN_MSGS = 100
HAND_USED_TTL_MS = 60_000          # 「刚刚用过」的手保持显示的时间，过后自动恢复自由
HAND_STATES = ("free", "holding", "held", "bound", "used")
# 手部动作碰到对方哪只手：空字符串表示与施动的手同侧（对方的左手/右手则明确指定）
HAND_TARGET_SIDES = {
    "对方的左手": "left",
    "对方的右手": "right",
    "手掌 / 指尖": "",
    "手臂 / 手腕": "",
}

# ---- 注意力（1.0.815）----
# 每个实体的注意力是 0~1 的连续量，按半衰期衰减：达到阈值才算「还在对话里」，由模型接管回合；
# 低于阈值走系统生活模拟（perform_idle_system_step），不调 API。用户叫名字 / 触碰永远能把人叫醒。
ATTENTION_WAKE = 0.35               # 阈值：达到即由模型接管
ATTENTION_HALF_MIN = 15             # 半衰期（分钟），由设置 room_npc_idle_min 覆盖
ATTENTION_SPEECH = 1.0              # 同房间听到用户说话 / 被轻声叫到
ATTENTION_SUMMON = 1.0              # 被叫到名字（可以隔着房间）
ATTENTION_TOUCH = 0.9               # 被触碰 / 被牵手 / 被束缚
ATTENTION_PHONE = 0.8               # 手机沟通里收到用户消息
ATTENTION_ENTER = 0.5               # 走进用户所在的房间
ATTENTION_LEAVE = 0.6               # 离开用户所在的房间时扣掉（用户走开对留下的人也成立）
ATTENTION_AUTO_PER_HOUR = 8         # 每实体每小时自主开口的上限；用户叫 / 触碰不受限
ATTENTION_HEARD_MAX = 5             # 当时没答上的呼唤最多记几条

# ---- 房间连接、门与音量（1.0.815）----
# 房间之间靠 doors 相连（双向），门只有所在房间的里侧能锁；只有相邻的房间能走过去。
VOLUMES = ("normal", "whisper", "shout")
VOLUME_LABELS = {"normal": "正常音量", "whisper": "轻语", "shout": "大声呼喊"}
QUIET_LABELS = ("弹琴", "看书", "休息")        # 需要安静的活动：被大声喊到会不高兴
MOOD_MINUTES = 8                            # 被打扰的不快持续多久

IDLE_ACTIVITIES = (
    ("弹琴", 20, 30, "{who} 坐到钢琴前，静静弹起了琴（预计 {m} 分钟）。"),
    ("看书", 20, 40, "{who} 从书架抽出一本书，安静地翻阅起来（预计 {m} 分钟）。"),
    ("休息", 15, 25, "{who} 靠在椅子上闭目养神，打算休息一会儿（预计 {m} 分钟）。"),
    ("泡茶", 10, 20, "{who} 沏了一壶热茶，慢慢啜饮着小憩（预计 {m} 分钟）。"),
    ("窗边发呆", 10, 20, "{who} 走到窗边望向外面，静静吹了会儿风（预计 {m} 分钟）。"),
)

FACILITY_ACTIONS = {
    "piano": "音乐演奏 / 曲库",
    "bookshelf": "打开共读",
    "desk": "专注计时 / 联合行动",
    "board": "合约 / 知识库",
    "door": "切换房间 / 离开",
    "position": "移动到此位置",
    "custom": "设施互动",
    "timer": "打开专注计时器",
    "contract": "打开合约页",
    "knowledge": "打开知识库",
    "music": "打开音乐演奏",
    "coread": "打开共读",
    "joint": "发起联合行动",
}

# (part_id, display_label, min_messages)
ACTION_CHANNEL_LABELS = {
    "left": "左手",
    "right": "右手",
    "gaze": "目光",
}

BODY_PARTS = (
    ("gaze", "目光注视", 0),
    ("sleeve", "衣袖 / 衣角", 0),
    ("shoulder", "肩膀", 0),
    ("hand", "手掌 / 指尖", 50),
    ("target_left_hand", "对方的左手", 50),
    ("target_right_hand", "对方的右手", 50),
    ("wrist", "手臂 / 手腕", 50),
    ("head", "头顶 / 发丝", 50),
    ("cheek", "脸颊", 120),
    ("ear", "耳畔", 120),
    ("waist", "后背 / 腰侧", 200),
    ("hug", "拥抱 / 拉近", 200),
)


# 小屋互动动作对应的基础 (Valence 效价, Tension 张力) 数值与冷却条件键
# 实际结算时会走 OIT 原生的冷却递减、连锁倍率、时段倍率与爆发倍率
ROOM_ACTION_VT: dict[str, tuple[str, int, int]] = {
    "目光注视": ("room_gaze", 2, 2),
    "衣袖 / 衣角": ("room_sleeve", 3, 3),
    "肩膀": ("room_shoulder", 3, 4),
    "手掌 / 指尖": ("room_hand", 5, 6),
    "对方的左手": ("room_target_left_hand", 5, 6),
    "对方的右手": ("room_target_right_hand", 5, 6),
    "牵手带走": ("room_hold", 5, 6),
    "手臂 / 手腕": ("room_wrist", 4, 6),
    "头顶 / 发丝": ("room_head", 6, 5),
    "脸颊": ("room_cheek", 7, 9),
    "耳畔": ("room_ear", 6, 12),
    "后背 / 腰侧": ("room_waist", 7, 11),
    "拥抱 / 拉近": ("room_hug", 9, 10),
    "靠在墙边拉近距离": ("room_lean_wall", 5, 10),
    "按在墙上 / 抵在墙边": ("room_pin_wall", 4, 15),
    "坐到身旁 / 腿上": ("room_sit_close", 8, 12),
    "按在椅子上": ("room_pin_chair", 4, 14),
}


def room_action_vt(part: str, secret: bool = False) -> tuple[str, int, int]:
    """返回小屋动作的 (条件键, 基础 V 增量, 基础 T 增量)；设施互动不产生 V/T。"""
    clean = str(part or "").strip()
    if not clean or clean == "设施":
        return "", 0, 0
    cond, v, t = ROOM_ACTION_VT.get(clean, ("room_custom", 6, 10))
    if secret:
        t += 3
    return cond, v, t


def display_width(text: str) -> int:
    """按 Windows 等宽字体 (NSimSun) 规则计算显示列宽：W/F/A（含汉字与框线）占 2 列，其余占 1 列。"""
    w = 0
    for ch in str(text or ""):
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 1
    return w


def _pad_right(text: str, target_w: int) -> str:
    diff = target_w - display_width(text)
    return text + (" " * max(0, diff))


def align_room_map(lines: list[str]) -> list[str]:
    """将字符画房间的上下边框与每一行内部内容对齐到完全一致的显示列宽。"""
    raw = [str(l).rstrip() for l in (lines or []) if str(l).strip()]
    if not raw:
        return ["┌──────────────────────────┐", "│                          │", "└───────────[门]───────────┘"]

    # 剥离外框，算出统一内部宽度（偶数，方便用宽度为 2 的 '─' 精确铺满）
    parsed = []
    inner_w = 34
    for idx, line in enumerate(raw):
        if idx == 0 and line.startswith("┌") and line.endswith("┐"):
            mid = line[1:-1].strip("─ ")
            parsed.append(("top", mid))
            inner_w = max(inner_w, display_width(mid) + 8)
        elif idx == len(raw) - 1 and line.startswith("└") and line.endswith("┘"):
            mid = line[1:-1].strip("─ ")
            parsed.append(("bottom", mid))
            inner_w = max(inner_w, display_width(mid) + 8)
        elif line.startswith("│"):
            content = line[1:-1] if line.endswith("│") and len(line) >= 2 else line[1:]
            content = content.rstrip()
            parsed.append(("body", content))
            inner_w = max(inner_w, display_width(content) + 2)
        else:
            parsed.append(("body", line))
            inner_w = max(inner_w, display_width(line) + 2)

    if inner_w % 2 == 1:
        inner_w += 1

    def _border_line(left_ch: str, right_ch: str, token: str) -> str:
        tok = token.strip()
        if not tok:
            return left_ch + ("─" * (inner_w // 2)) + right_ch
        tw = display_width(tok)
        rem = max(0, inner_w - tw)
        # 如果剩余列数为奇数，给左右补一个空格对齐到偶数，确保总列宽严格等于 inner_w
        left_pad = ""
        if rem % 2 == 1:
            left_pad = " "
            rem -= 1
        left_dashes = (rem // 2) // 2
        right_dashes = (rem // 2) - left_dashes
        out = left_ch + ("─" * left_dashes) + left_pad + tok + ("─" * right_dashes) + right_ch
        return out

    aligned = []
    for kind, content in parsed:
        if kind == "top":
            aligned.append(_border_line("┌", "┐", content))
        elif kind == "bottom":
            aligned.append(_border_line("└", "┘", content))
        else:
            aligned.append("│" + _pad_right(content, inner_w) + "│")
    return aligned


def part_required_msgs(part_key_or_label: str) -> int:
    s = str(part_key_or_label or "").strip()
    for pid, label, req in BODY_PARTS:
        if s == pid or s == label:
            return req
    for _pid, label, req in ALL_POSITION_INTERACTIONS:
        if s == label:
            return req
    return CUSTOM_PART_MIN_MSGS


def part_allowed(part_key_or_label: str, msg_count: int) -> tuple[bool, int]:
    req = part_required_msgs(part_key_or_label)
    return int(msg_count or 0) >= req, req


# 基于位置的特色互动：(id, label, min_msgs)
WALL_INTERACTIONS = (
    ("lean_wall", "靠在墙边拉近距离", 50),
    ("pin_wall", "按在墙上 / 抵在墙边", 120),
)
CHAIR_INTERACTIONS = (
    ("lean_chair", "扶着椅背俯身靠近", 50),
    ("pin_chair", "按在椅子上", 120),
    ("sit_lap", "坐到身旁 / 腿上", 200),
)
NEAR_INTERACTIONS = (
    ("lean_beside", "并肩靠在旁边", 0),
    ("press_beside", "撑在身侧圈住对方", 120),
)
ALL_POSITION_INTERACTIONS = WALL_INTERACTIONS + CHAIR_INTERACTIONS + NEAR_INTERACTIONS


def position_interactions_for(user_pos: str, target_pos: str) -> list[tuple[str, str, int]]:
    """根据用户或目标角色当前所在位置，返回额外解锁的位置互动列表。"""
    combined = f"{user_pos or ''} {target_pos or ''}"
    out = []
    if "墙" in combined:
        out.extend(WALL_INTERACTIONS)
    if any(k in combined for k in ("椅", "沙发", "琴凳")):
        out.extend(CHAIR_INTERACTIONS)
    if any(k in combined for k in ("窗", "桌", "琴", "书架", "茶几")):
        out.extend(NEAR_INTERACTIONS)
    # 去重保序
    seen = set()
    uniq = []
    for item in out:
        if item[0] not in seen:
            seen.add(item[0])
            uniq.append(item)
    return uniq


def persona_room_id(persona_name: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff-]+", "_", str(persona_name or "").strip()).strip("_")
    return f"home_{safe or 'persona'}"


def make_persona_room(persona_name: str, display_name: str) -> dict:
    """为指定人设生成默认专属房间（含椅子与墙边位置），默认归该人设所有且持有钥匙。"""
    title = str(display_name or persona_name).strip() or persona_name
    return {
        "id": persona_room_id(persona_name),
        "name": f"{title} 的房间",
        "theme": f"{title} 的专属居室",
        "ambience": f"这里是 {title} 的私人空间，窗边透进柔和的光，书架、钢琴与木椅井然有序。",
        "builtin": True,
        "owner": persona_name,
        "is_public": False,
        "keys": [persona_name],
        "locked_inside": False,
        "locked_by": "",
        "locked_reason": "",
        "doors": {"lounge": {"label": "卧室门"}},
        "ascii_map": [
            "┌──────────────[窗台]──────────────┐",
            "│                                  │",
            "│  [钢琴]                [书架]    │",
            "│  [椅子]                [墙边]    │",
            "│                                  │",
            "│  [茶几]                [书桌]    │",
            "│                                  │",
            "└───────────────[门]───────────────┘",
        ],
        "facilities": [
            {"token": "[窗台]", "label": "窗台", "action": "position", "desc": "走到窗台边吹风、看风景。"},
            {"token": "[钢琴]", "label": "钢琴", "action": "piano", "desc": "打开音乐演奏评判或曲库。"},
            {"token": "[书架]", "label": "书架", "action": "bookshelf", "desc": "抽出一本书开启共读。"},
            {"token": "[椅子]", "label": "椅子", "action": "position", "desc": "坐到椅子上，或靠近椅子触发位置互动。"},
            {"token": "[墙边]", "label": "墙边", "action": "position", "desc": "靠近墙边站立，可触发靠墙/按在墙上等位置互动。"},
            {"token": "[茶几]", "label": "茶几", "action": "position", "desc": "走到茶几旁倒茶小憩。"},
            {"token": "[书桌]", "label": "书桌", "action": "desk", "desc": "进入专注计时或发起联合行动。"},
            {"token": "[门]", "label": "门", "action": "door", "desc": "推门走出房间，前往其他房间。"},
        ],
    }


BUILTIN_ROOMS = {
    "my_home": {
        "id": "my_home",
        "name": "我的房间",
        "theme": "日常居室",
        "ambience": "台灯暖黄，窗外夜色安静，桌旁摆着木椅，计时器微微亮着。",
        "builtin": True,
        "owner": "user",
        "is_public": False,
        "keys": ["user"],
        "locked_inside": False,
        "locked_by": "",
        "locked_reason": "",
        "doors": {"lounge": {"label": "房门"}},
        "ascii_map": [
            "┌──────────────[窗户]──────────────┐",
            "│                                  │",
            "│  [书桌]                [布告板]  │",
            "│  [椅子]                [墙边]    │",
            "│                                  │",
            "│  [钢琴]                [书架]    │",
            "│                                  │",
            "└───────────────[门]───────────────┘",
        ],
        "facilities": [
            {"token": "[窗户]", "label": "窗户", "action": "position", "desc": "走到窗边透透气，看看外面的天色。"},
            {"token": "[书桌]", "label": "书桌", "action": "desk", "desc": "打开专注计时器或发起联合行动。"},
            {"token": "[布告板]", "label": "布告板", "action": "board", "desc": "查看合约词条或打开知识库。"},
            {"token": "[椅子]", "label": "椅子", "action": "position", "desc": "坐到椅子上，或靠近椅子触发位置互动。"},
            {"token": "[墙边]", "label": "墙边", "action": "position", "desc": "靠近墙边站立，可触发靠墙/按在墙上等位置互动。"},
            {"token": "[钢琴]", "label": "钢琴", "action": "piano", "desc": "打开音乐演奏评判或曲库。"},
            {"token": "[书架]", "label": "书架", "action": "bookshelf", "desc": "挑选书籍开启共读。"},
            {"token": "[门]", "label": "门", "action": "door", "desc": "推门走出房间，前往其他房间。"},
        ],
    },
    "lounge": {
        "id": "lounge",
        "name": "公共客厅",
        "theme": "公共聚会客厅",
        "ambience": "宽敞的木质公共客厅，摆着沙发与木椅，谁都可以随时进来闲聊、合奏或看书。",
        "builtin": True,
        "owner": "",
        "is_public": True,
        "keys": [],
        "locked_inside": False,
        "locked_by": "",
        "locked_reason": "",
        "doors": {"my_home": {"label": "房门"}},
        "ascii_map": [
            "┌─────────────[落地窗]─────────────┐",
            "│                                  │",
            "│  [长沙发]            [共读书柜]  │",
            "│  [椅子]              [墙边]      │",
            "│                                  │",
            "│  [钢琴]   [布告板]   [书桌]      │",
            "│                                  │",
            "└───────────────[门]───────────────┘",
        ],
        "facilities": [
            {"token": "[落地窗]", "label": "落地窗", "action": "position", "desc": "走到落地窗前望向窗外。"},
            {"token": "[长沙发]", "label": "长沙发", "action": "position", "desc": "坐到沙发上休息或并肩闲聊。"},
            {"token": "[椅子]", "label": "椅子", "action": "position", "desc": "坐到木椅上，靠近椅子可触发位置互动。"},
            {"token": "[墙边]", "label": "墙边", "action": "position", "desc": "靠近墙边站立，可触发靠墙/按在墙上等位置互动。"},
            {"token": "[共读书柜]", "label": "共读书柜", "action": "bookshelf", "desc": "打开共读，一起翻阅书页。"},
            {"token": "[钢琴]", "label": "钢琴", "action": "piano", "desc": "打开音乐演奏或曲库。"},
            {"token": "[布告板]", "label": "布告板", "action": "board", "desc": "查看合约或知识库卡片。"},
            {"token": "[书桌]", "label": "书桌", "action": "desk", "desc": "专注计时或发起联合行动。"},
            {"token": "[门]", "label": "门", "action": "door", "desc": "前往其他房间。"},
        ],
    },
}

_TOKEN_RE = re.compile(r"\[[^\[\]\n]{1,20}\]")


def _infer_action(label: str) -> str:
    low = str(label or "").lower()
    if any(k in low for k in ("钢琴", "琴", "乐器", "留声机", "音乐", "piano")):
        return "piano"
    if any(k in low for k in ("书架", "书柜", "藏书", "bookshelf", "book")):
        return "bookshelf"
    if any(k in low for k in ("书桌", "工作台", "计时", "写字台", "desk")):
        return "desk"
    if any(k in low for k in ("布告", "黑板", "白板", "合约", "笔记", "board")):
        return "board"
    if any(k in low for k in ("门", "出口", "走廊", "door")):
        return "door"
    if any(k in low for k in ("计时", "专注", "timer")):
        return "timer"
    if any(k in low for k in ("合约", "contract")):
        return "contract"
    if any(k in low for k in ("知识", "knowledge")):
        return "knowledge"
    if any(k in low for k in ("音乐", "演奏", "music")):
        return "music"
    if any(k in low for k in ("共读", "书库", "coread")):
        return "coread"
    if any(k in low for k in ("联合", "joint")):
        return "joint"
    if any(k in low for k in ("墙", "椅", "沙发", "窗", "茶几", "床", "wall", "chair", "sofa")):
        return "position"
    return "custom"


def normalize_room(raw: dict) -> dict:
    """校验并补全房间对象（含等宽对齐、房主、公共属性、钥匙列表与内部反锁状态）。"""
    rid = str(raw.get("id") or "").strip() or f"room_{uuid.uuid4().hex[:8]}"
    name = str(raw.get("name") or "未命名房间").strip()[:40]
    theme = str(raw.get("theme") or "自定义场景").strip()[:60]
    ambience = str(raw.get("ambience") or "").strip()[:240]
    map_note = str(raw.get("map_note") or "").strip()[:500]
    owner = str(raw.get("owner") or "").strip()
    is_public = bool(raw.get("is_public", rid == "lounge" or not owner))
    raw_keys = raw.get("keys")
    keys = list(dict.fromkeys(str(k).strip() for k in (raw_keys if isinstance(raw_keys, list) else []) if str(k).strip()))
    if owner and not is_public and owner not in keys:
        keys.insert(0, owner)

    lines = raw.get("ascii_map")
    if isinstance(lines, str):
        lines = lines.splitlines()
    if not isinstance(lines, list) or not lines:
        lines = list(BUILTIN_ROOMS["lounge"]["ascii_map"])
    clean_lines = [str(line).rstrip() for line in lines[:24]]
    joined = "\n".join(clean_lines)
    if "[门]" not in joined and not any(_infer_action(m.group(0)[1:-1]) == "door" for m in _TOKEN_RE.finditer(joined)):
        clean_lines.append("└───────────────[门]───────────────┘")
    clean_lines = align_room_map(clean_lines)
    joined = "\n".join(clean_lines)

    fac_by_token = {}
    for item in raw.get("facilities") or []:
        if not isinstance(item, dict):
            continue
        tok = str(item.get("token") or "").strip()
        if not tok:
            lbl = str(item.get("label") or "").strip()
            tok = f"[{lbl}]" if lbl else ""
        if not tok.startswith("["):
            tok = f"[{tok}]"
        label = str(item.get("label") or tok.strip("[]")).strip()[:24]
        action = str(item.get("action") or "").strip()
        if action not in FACILITY_ACTIONS:
            action = _infer_action(label)
        fac_by_token[tok] = {
            "token": tok,
            "label": label,
            "action": action,
            "desc": str(item.get("desc") or FACILITY_ACTIONS.get(action, "互动设施")).strip()[:120],
        }

    for m in _TOKEN_RE.finditer(joined):
        tok = m.group(0)
        inner = tok[1:-1].strip()
        if not inner or inner.startswith("@") or set(inner) <= set("═─-_= "):
            continue
        if tok not in fac_by_token:
            action = _infer_action(inner)
            fac_by_token[tok] = {
                "token": tok,
                "label": inner[:24],
                "action": action,
                "desc": FACILITY_ACTIONS.get(action, "点击互动"),
            }

    return {
        "id": rid,
        "name": name,
        "theme": theme,
        "ambience": ambience,
        "map_note": map_note,
        "builtin": bool(raw.get("builtin", False)),
        "owner": owner,
        "is_public": is_public,
        "keys": keys,
        "locked_inside": bool(raw.get("locked_inside", False)),
        "locked_by": str(raw.get("locked_by") or "").strip(),
        "locked_reason": str(raw.get("locked_reason") or "").strip(),
        "doors": _normalize_doors(raw.get("doors")),
        "ascii_map": clean_lines,
        "facilities": list(fac_by_token.values()),
    }


def _normalize_doors(raw) -> dict[str, dict]:
    """房间通向别的房间的门：{邻居 id: {label, locked, by, reason}}；两边各存一份，锁定会同步镜像。"""
    out: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return out
    for rid, val in raw.items():
        clean = str(rid or "").strip()
        if not clean:
            continue
        if isinstance(val, bool):
            val = {"locked": val}
        if not isinstance(val, dict):
            continue
        out[clean] = {
            "label": str(val.get("label") or "房门").strip()[:20],
            "locked": bool(val.get("locked", False)),
            "by": str(val.get("by") or "").strip(),
            "reason": str(val.get("reason") or "").strip(),
        }
    return out


def render_room_map(room: dict, occupants: list[tuple[str, str]]) -> tuple[str, list[dict]]:
    """渲染严格对齐的房间字符画，把在场人物放进地图底部空行，并返回精确的字符级热点列表。"""
    base_lines = list(room.get("ascii_map") or [])
    if not base_lines:
        base_lines = ["┌──────────────────────────┐", "│                          │", "└───────────[门]───────────┘"]

    lines = list(base_lines)
    if occupants:
        actor_tokens = [f"[@{name}]" for _aid, name in occupants]
        actor_rows = []
        for i in range(0, len(actor_tokens), 3):
            chunk = "  ".join(actor_tokens[i:i + 3])
            actor_rows.append(f"│  在场: {chunk}  │")
        insert_at = len(lines) - 1 if len(lines) >= 2 else len(lines)
        for offset, row in enumerate(actor_rows):
            lines.insert(insert_at + offset, row)

    lines = align_room_map(lines)
    fac_map = {f["token"]: f for f in room.get("facilities") or []}
    actor_by_token = {f"[@{name}]": (aid, name) for aid, name in occupants}
    hotspots = []

    for line_idx, line in enumerate(lines, start=1):
        for m in _TOKEN_RE.finditer(line):
            tok = m.group(0)
            if tok in actor_by_token:
                aid, name = actor_by_token[tok]
                hotspots.append({
                    "kind": "actor",
                    "id": aid,
                    "label": name,
                    "action": "interact_actor",
                    "desc": tr("点击与 {name} 互动（展开部位与位置互动）", name=name) if aid != "user" else tr("这是你自己"),
                    "line": line_idx,
                    "col_start": m.start(),
                    "col_end": m.end(),
                })
            elif tok in fac_map:
                f = fac_map[tok]
                hotspots.append({
                    "kind": "facility",
                    "id": f["token"],
                    "label": f["label"],
                    "action": f["action"],
                    "desc": f["desc"],
                    "line": line_idx,
                    "col_start": m.start(),
                    "col_end": m.end(),
                })

    return "\n".join(lines), hotspots


def _ms_now(now_ms: int = None) -> int:
    return int(time.time() * 1000) if now_ms is None else int(now_ms)


class RoomStore:
    """管理分人设房间、钥匙分发、内部反锁、牵手/跟随带人、室内位置、双手槽位与定向听觉日志。"""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, ROOM_FILE)
        self.rooms: dict[str, dict] = {}
        self.occupants: dict[str, str] = {}
        self.positions: dict[str, str] = {}         # actor_id -> 当前室内位置（如 "墙边" / "椅子" / "房间中央"）
        self.following: dict[str, str] = {}         # actor_id -> leader_id（言语同意跟随）
        self.room_counts: dict[str, int] = {}
        self.last_user_interact: dict[str, int] = {}
        self.activities: dict[str, dict] = {}
        # 注意力：值 + 最后一次写入的时刻（读取时按半衰期衰减）；heard 记当时没答上的呼唤
        self.attn_half_min: int = ATTENTION_HALF_MIN
        self.attention: dict[str, float] = {}
        self.attention_ts: dict[str, int] = {}
        self.heard: dict[str, list[dict]] = {}
        self.auto_turns: dict[str, list[int]] = {}
        # 双手、牵手与待发动作都属于「实体」：用户与所有人设共用同一套结构
        self.hands: dict[str, dict] = {}            # actor_id -> {"left"/"right": {state, note, link, ts}}
        self.holding: dict[str, dict] = {}          # actor_id -> {"left"/"right": target_id}（这只手握住了谁）
        self.pending: dict[str, list[dict]] = {}    # actor_id -> 待发动作（下一句话一起发出的手部/目光动作）
        self.moods: dict[str, dict] = {}            # actor_id -> {label, until, reason}（被打扰等短暂情绪）
        self.name_map: dict[str, str] = {}          # 运行时由界面写入，给系统日志用
        self.logs: list[dict] = []
        self.on_map_changed = None
        self.load()

    def map_description(self) -> str:
        """Human-readable current map topology for prompts and persona memory."""
        lines = []
        for room in self.rooms.values():
            neighbors = [self.rooms.get(n, {}).get("name", n) for n in self.neighbors(room["id"])]
            note = str(room.get("map_note") or "").strip()
            if neighbors:
                line = f"「{room.get('name', room['id'])}」通过房门连接到「{'、'.join(neighbors)}」。"
            else:
                line = f"「{room.get('name', room['id'])}」目前没有可通行的房门连接。"
            lines.append(f"{line} {note}".strip())
        return " ".join(lines)

    def map_snapshot(self) -> str:
        """Compact machine-readable snapshot used when generating or renovating maps."""
        chunks = []
        for room in self.rooms.values():
            chunks.append(
                f"房间「{room.get('name', room['id'])}」主题：{room.get('theme', '')}\n"
                + "\n".join(room.get("ascii_map") or [])
                + f"\n门连接：{', '.join((self.rooms.get(n) or {}).get('name', n) for n in self.neighbors(room['id'])) or '无'}"
            )
        return "\n\n".join(chunks)

    def _map_changed(self):
        callback = self.on_map_changed
        if callable(callback):
            try:
                callback(self.map_description())
            except Exception:
                pass

    def _ensure_builtins(self):
        self.rooms.pop("partner_home", None)
        for rid, raw in BUILTIN_ROOMS.items():
            if rid not in self.rooms:
                self.rooms[rid] = normalize_room(copy.deepcopy(raw))
            else:
                # 升级旧存档中的内置房间：补齐 [椅子] / [墙边] 与对齐地图
                r = self.rooms[rid]
                joined = "\n".join(r.get("ascii_map") or [])
                if "[椅子]" not in joined or "[墙边]" not in joined:
                    updated = normalize_room(copy.deepcopy(raw))
                    updated["keys"] = r.get("keys") or updated["keys"]
                    updated["locked_inside"] = r.get("locked_inside", False)
                    updated["locked_by"] = r.get("locked_by", "")
                    updated["locked_reason"] = r.get("locked_reason", "")
                    self.rooms[rid] = updated
        if "user" not in self.occupants or self.occupants["user"] not in self.rooms:
            self.occupants["user"] = "my_home"
        for aid, rid in list(self.occupants.items()):
            if rid not in self.rooms:
                self.occupants.pop(aid, None)

    def _bootstrap_links(self):
        """旧存档没有门的概念：先给每间房接一扇通向公共客厅的门，再补齐双向并砍掉走不到客厅的房间。"""
        for rid, room in self.rooms.items():
            if rid == "lounge" or room.get("doors"):
                continue
            room["doors"] = {"lounge": {"label": "房门",
                                        "locked": bool(room.get("locked_inside")),
                                        "by": room.get("locked_by") or "",
                                        "reason": room.get("locked_reason") or ""}}
        if "my_home" in self.rooms:
            self.link_rooms("my_home", "lounge", save=False)
        for rid, room in list(self.rooms.items()):
            doors = dict(room.get("doors") or {})
            for nid in list(doors):
                other = self.rooms.get(nid)
                if other is None:
                    doors.pop(nid, None)
                    continue
                if rid not in (other.get("doors") or {}):
                    other.setdefault("doors", {})[rid] = {"label": doors[nid].get("label") or "房门"}
            room["doors"] = doors
            self._sync_lock_summary(room)
        self.prune_orphans()

    def _sync_lock_summary(self, room: dict, save: bool = False):
        """locked_inside 是「这间房至少有一扇门从里侧锁上了」的汇总，供旧的判定与界面继续用。"""
        doors = room.get("doors") or {}
        locked = [d for d in doors.values() if d.get("locked")]
        if locked:
            room["locked_inside"] = True
            first = locked[0]
            room["locked_by"] = str(first.get("by") or "")
            room["locked_reason"] = str(first.get("reason") or "")
        else:
            room["locked_inside"] = False
            room["locked_by"] = ""
            room["locked_reason"] = ""
        if save:
            self.save()

    def sync_personas(self, personas: list[tuple[str, str]]) -> bool:
        """为每个人设（含新建人设）确保专属房间存在，并默认让未就位的人设待在自己的房间里。"""
        changed = False
        active_ids = {str(pid) for pid, _title in personas if pid}
        for aid in list(self.occupants):
            if aid == "user" or aid in active_ids:
                continue
            self.occupants.pop(aid, None)
            self.positions.pop(aid, None)
            self.following.pop(aid, None)
            self.hands.pop(aid, None)
            self.holding.pop(aid, None)
            self.pending.pop(aid, None)
            self.attention.pop(aid, None)
            self.attention_ts.pop(aid, None)
            self.heard.pop(aid, None)
            self.auto_turns.pop(aid, None)
            self.moods.pop(aid, None)
            changed = True
        for room in self.rooms.values():
            keys = list(room.get("keys") or [])
            clean_keys = [key for key in keys if key == "user" or key in active_ids]
            if clean_keys != keys:
                room["keys"] = clean_keys
                changed = True
        # Assistant、被禁止主动发言的人设以及已删除的人设不属于小屋；旧版本留下的专属房间和占位一并回收。
        for rid, room in list(self.rooms.items()):
            owner = str(room.get("owner") or "")
            if (room.get("builtin") and owner and rid == persona_room_id(owner)
                    and owner not in active_ids):
                self.rooms.pop(rid, None)
                for other in self.rooms.values():
                    (other.get("doors") or {}).pop(rid, None)
                    self._sync_lock_summary(other)
                for aid, cur in list(self.occupants.items()):
                    if cur == rid:
                        self.occupants.pop(aid, None)
                        self.positions.pop(aid, None)
                        self.following.pop(aid, None)
                        self.hands.pop(aid, None)
                        self.holding.pop(aid, None)
                        self.pending.pop(aid, None)
                        self.attention.pop(aid, None)
                        self.attention_ts.pop(aid, None)
                        self.heard.pop(aid, None)
                        self.auto_turns.pop(aid, None)
                        self.moods.pop(aid, None)
                self.logs = [entry for entry in self.logs if entry.get("room_id") != rid]
                changed = True
        for pid, title in personas:
            if not pid:
                continue
            rid = persona_room_id(pid)
            if rid not in self.rooms:
                self.rooms[rid] = normalize_room(make_persona_room(pid, title))
                self.link_rooms(rid, "lounge", save=False)         # 卧室始终连着公共客厅
                changed = True
            else:
                r = self.rooms[rid]
                joined = "\n".join(r.get("ascii_map") or [])
                if r.get("builtin") and ("[椅子]" not in joined or "[墙边]" not in joined):
                    updated = normalize_room(make_persona_room(pid, title))
                    updated["keys"] = r.get("keys") or updated["keys"]
                    updated["locked_inside"] = r.get("locked_inside", False)
                    updated["locked_by"] = r.get("locked_by", "")
                    updated["locked_reason"] = r.get("locked_reason", "")
                    self.rooms[rid] = updated
                    r = updated
                    changed = True
                if r.get("owner") != pid:
                    r["owner"] = pid
                    changed = True
                if not r.get("is_public") and pid not in r["keys"]:
                    r["keys"].insert(0, pid)
                    changed = True
                if self.link_rooms(rid, "lounge", save=False):     # 卧室始终连着公共客厅（两边都要有门）
                    changed = True
            if pid not in self.occupants or self.occupants[pid] not in self.rooms:
                self.occupants[pid] = rid
                changed = True
        if changed:
            self.save()
        return changed

    def load(self):
        self.rooms = {}
        self.occupants = {}
        self.positions = {}
        self.following = {}
        self.hands = {}
        self.holding = {}
        self.pending = {}
        self.room_counts = {}
        self.last_user_interact = {}
        self.activities = {}
        self.attention = {}
        self.attention_ts = {}
        self.heard = {}
        self.auto_turns = {}
        self.moods = {}
        self.logs = []
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for rid, raw in (data.get("rooms") or {}).items():
                        if rid == "partner_home" or not isinstance(raw, dict):
                            continue
                        raw = dict(raw)
                        raw.setdefault("id", rid)
                        norm = normalize_room(raw)
                        self.rooms[norm["id"]] = norm
                    occ = data.get("occupants") or {}
                    if isinstance(occ, dict):
                        for aid, rid in occ.items():
                            if isinstance(aid, str) and isinstance(rid, str) and rid in self.rooms:
                                self.occupants[aid] = rid
                    pos = data.get("positions") or {}
                    if isinstance(pos, dict):
                        self.positions = {str(k): str(v)[:30] for k, v in pos.items() if isinstance(k, str) and v}
                    fol = data.get("following") or {}
                    if isinstance(fol, dict):
                        self.following = {str(k): str(v) for k, v in fol.items() if isinstance(k, str) and v}
                    hh = data.get("holding") if "holding" in data else data.get("holding_hands") or {}
                    if isinstance(hh, dict):
                        if any(k in ("left", "right") for k in hh):
                            hh = {"user": hh}                     # 旧存档：只有用户的手
                        self.holding = {
                            str(aid): {s: str(v) for s, v in (sides or {}).items()
                                       if s in ("left", "right") and v}
                            for aid, sides in hh.items()
                            if isinstance(aid, str) and isinstance(sides, dict)
                        }
                    rc = data.get("room_counts") or {}
                    if isinstance(rc, dict):
                        for aid, val in rc.items():
                            if isinstance(aid, str):
                                try:
                                    self.room_counts[aid] = max(0, int(val or 0))
                                except (ValueError, TypeError):
                                    pass
                    lui = data.get("last_user_interact") or {}
                    if isinstance(lui, dict):
                        for aid, val in lui.items():
                            if isinstance(aid, str):
                                try:
                                    self.last_user_interact[aid] = max(0, int(val or 0))
                                except (ValueError, TypeError):
                                    pass
                    acts = data.get("activities") or {}
                    if isinstance(acts, dict):
                        for aid, val in acts.items():
                            if isinstance(aid, str) and isinstance(val, dict):
                                self.activities[aid] = {
                                    "label": str(val.get("label") or "")[:30],
                                    "until": max(0, int(val.get("until") or 0)),
                                    "room_id": str(val.get("room_id") or ""),
                                }
                    att = data.get("attention") or {}
                    if isinstance(att, dict):
                        for aid, val in att.items():
                            if not isinstance(aid, str):
                                continue
                            try:
                                v = float(val or 0.0)
                            except (TypeError, ValueError):
                                continue
                            if v > 0:
                                self.attention[aid] = max(0.0, min(1.0, v))
                    ats = data.get("attention_ts") or {}
                    if isinstance(ats, dict):
                        for aid, val in ats.items():
                            if isinstance(aid, str):
                                try:
                                    self.attention_ts[aid] = max(0, int(val or 0))
                                except (TypeError, ValueError):
                                    pass
                    heard = data.get("heard") or {}
                    if isinstance(heard, dict):
                        for aid, items in heard.items():
                            if not (isinstance(aid, str) and isinstance(items, list)):
                                continue
                            clean = []
                            for it in items:
                                if not isinstance(it, dict):
                                    continue
                                txt = str(it.get("text") or "").strip()[:200]
                                if not txt:
                                    continue
                                clean.append({
                                    "text": txt,
                                    "room": str(it.get("room") or "")[:30],
                                    "who": str(it.get("who") or "")[:30],
                                    "ts": max(0, int(it.get("ts") or 0)),
                                })
                            if clean:
                                self.heard[aid] = clean[-ATTENTION_HEARD_MAX:]
                    turns = data.get("auto_turns") or {}
                    if isinstance(turns, dict):
                        for aid, items in turns.items():
                            if not (isinstance(aid, str) and isinstance(items, list)):
                                continue
                            kept = []
                            for it in items:
                                try:
                                    kept.append(int(it or 0))
                                except (TypeError, ValueError):
                                    continue
                            if kept:
                                self.auto_turns[aid] = kept[-32:]
                    moods = data.get("moods") or {}
                    if isinstance(moods, dict):
                        for aid, val in moods.items():
                            if not (isinstance(aid, str) and isinstance(val, dict)):
                                continue
                            until = 0
                            try:
                                until = max(0, int(val.get("until") or 0))
                            except (TypeError, ValueError):
                                continue
                            if until:
                                self.moods[aid] = {"label": str(val.get("label") or "")[:20],
                                                   "until": until,
                                                   "reason": str(val.get("reason") or "")[:30]}
                    hands = data.get("hands") or {}
                    if isinstance(hands, dict):
                        if any(k in ("left", "right") for k in hands):
                            hands = {"user": hands}                   # 旧存档：只有用户的手
                        for aid, sides in hands.items():
                            if not (isinstance(aid, str) and isinstance(sides, dict)):
                                continue
                            clean = {}
                            for side in ("left", "right"):
                                h = sides.get(side)
                                if not isinstance(h, dict):
                                    continue
                                st = h.get("state")
                                if st not in HAND_STATES:
                                    st = "free"
                                ts = h.get("ts")
                                clean[side] = {
                                    "state": st,
                                    "note": str(h.get("note") or "")[:60],
                                    "link": str(h.get("link") or ""),
                                    "ts": int(ts) if isinstance(ts, (int, float)) else 0,
                                }
                            if clean:
                                self.hands[aid] = clean
                    logs = data.get("logs") or []
                    if isinstance(logs, list):
                        self.logs = [x for x in logs[-MAX_ROOM_LOGS:] if isinstance(x, dict)]
            except (OSError, json.JSONDecodeError, ValueError):
                pass
        self._ensure_builtins()
        self._bootstrap_links()
        self.pending = {}
        for aid in ("user", *self.hands):
            self.reset_turn_hands(aid, save=False)

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        payload = {
            "rooms": self.rooms,
            "occupants": self.occupants,
            "positions": self.positions,
            "following": self.following,
            "holding": self.holding,
            "room_counts": self.room_counts,
            "last_user_interact": self.last_user_interact,
            "activities": self.activities,
            "attention": self.attention,
            "attention_ts": self.attention_ts,
            "heard": self.heard,
            "auto_turns": self.auto_turns,
            "moods": self.moods,
            "hands": self.hands,
            "logs": self.logs[-MAX_ROOM_LOGS:],
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ---- 室内位置 ----

    def position_of(self, actor_id: str) -> str:
        return self.positions.get(actor_id) or "房间中央"

    def move_position(self, actor_id: str, pos_label: str, actor_name: str = "") -> bool:
        clean = str(pos_label or "").strip()[:30]
        if not clean or self.position_of(actor_id) == clean:
            return False
        self.positions[actor_id] = clean
        rid = self.occupants.get(actor_id)
        if rid and rid in self.rooms:
            who = actor_name or ("你" if actor_id == "user" else actor_id)
            self.append_log(rid, "sys", "系统", ["all"], "sys",
                            tr("{who} 移动到了「{pos}」。", who=who, pos=clean))
        self.save()
        return True

    def listeners_for_volume(self, room_id: str, sender_id: str, volume: str = "normal") -> list[str]:
        """按音量与室内位置计算听众：normal=全屋可听(["all"])；whisper=仅同一位置；
        shout=全屋加隔壁房间（隔壁靠 append_log 的 spill 传出，这里仍是 ["all"]）。"""
        vol = str(volume or "normal").lower()
        if vol not in VOLUMES:
            vol = "normal"
        if vol != "whisper":
            return ["all"]
        pos = self.position_of(sender_id)
        same_pos = [aid for aid in self.occupants_in(room_id) if self.position_of(aid) == pos]
        return same_pos or [sender_id]

    # ---- 牵手与跟随（任何实体都能牵手、带人 / 被带出去） ----

    def hold_hand(self, actor_id: str, side: str, target_id: str = "user",
                  actor_name: str = "", target_name: str = "", msg_count: int | None = None) -> str:
        """actor 用自己的某只手牵住 target 的同侧手；两只手都会记下配对状态。"""
        if side not in ("left", "right"):
            return "请指定左手或右手"
        if not target_id or target_id == actor_id:
            return "需要指定牵手对象"
        if msg_count is not None and int(msg_count) < HOLD_HAND_MIN_MSGS:
            return tr("还不够熟络，暂时无法牵手带走。")
        mine = self.hands_of(actor_id)[side]
        if mine["state"] == "bound":
            return "这只手正被束缚着，无法牵手"
        if mine["state"] == "held":
            return tr("这只手正被拉在别人手里，先抽回来才能去牵别人。")
        if self.occupants.get(actor_id) != self.occupants.get(target_id):
            return "对方不在你当前所在的房间"
        if self.position_of(actor_id) != self.position_of(target_id):
            return "你和对方不在同一个位置，需先走到对方身边才能牵手"
        theirs = self.hands_of(target_id)[side]
        if theirs["state"] == "bound":
            return tr("对方这只手正被束缚着，无法牵手。")
        if theirs["state"] in ("holding", "held"):
            return tr("对方这只手已经牵着别人了。")
        self.holding.setdefault(actor_id, {})[side] = target_id
        self._set_hand(actor_id, side, "holding", f"握住 {target_name or target_id}", link=target_id)
        self.holding.setdefault(target_id, {})[side] = ""
        self._set_hand(target_id, side, "held", f"被 {actor_name or actor_id} 握住", link=actor_id)
        self.raise_attention(target_id, ATTENTION_TOUCH)
        self.raise_attention(actor_id, ATTENTION_TOUCH)
        rid = self.occupants.get(actor_id) or self.current_room_id()
        self.append_log(rid, "sys", "系统", ["all"], "sys",
                        tr("{who} 用{hand}握住了 {target} 的手。",
                           who=actor_name or ("你" if actor_id == "user" else actor_id),
                           hand="左手" if side == "left" else "右手",
                           target=target_name or ("你" if target_id == "user" else target_id)))
        self.save()
        return ""

    def release_hand(self, actor_id: str, side: str, name_map: dict[str, str] = None,
                     actor_name: str = "", save: bool = True):
        """松开牵手：牵着的人松手，或被牵着的人把手抽回来，两边的手状态一起恢复。"""
        if side not in ("left", "right"):
            return
        h = self._hand(actor_id, side)
        st = h["state"]
        if st not in ("holding", "held"):
            return
        other = h.get("link") or ""
        self._detach_hand(actor_id, side)
        if other:
            names = name_map or {}
            other_name = names.get(other, "你" if other == "user" else other)
            who = actor_name or names.get(actor_id, "你" if actor_id == "user" else actor_id)
            rid = self.occupants.get(actor_id) or self.current_room_id()
            text = (tr("{who} 松开了牵着 {other} 的手。", who=who, other=other_name) if st == "holding"
                    else tr("{who} 把手从 {other} 手里抽了回来。", who=who, other=other_name))
            if rid in self.rooms:
                self.append_log(rid, "sys", "系统", ["all"], "sys", text)
        if save:
            self.save()

    def set_following(self, actor_id: str, leader_id: str | None, actor_name: str = "", leader_name: str = ""):
        if leader_id:
            if self.following.get(actor_id) != leader_id:
                self.following[actor_id] = leader_id
                rid = self.occupants.get(actor_id)
                if rid in self.rooms:
                    self.append_log(rid, "sys", "系统", ["all"], "sys",
                                    tr("{who} 开始跟在 {leader} 身旁同行。",
                                       who=actor_name or actor_id,
                                       leader=leader_name or ("你" if leader_id == "user" else leader_id)))
                self.save()
        else:
            if actor_id in self.following:
                del self.following[actor_id]
                self.save()

    def companions_of(self, leader_id: str, room_id: str) -> list[str]:
        """返回与 leader 同房且会一起被带走的同伴（牵手或跟随）。"""
        occ = set(self.occupants_in(room_id))
        out = []
        for side in ("left", "right"):
            aid = (self.holding.get(leader_id) or {}).get(side) or ""
            if aid and aid in occ and aid not in out:
                out.append(aid)
        for aid, lid in self.following.items():
            if lid == leader_id and aid in occ and aid != leader_id and aid not in out:
                out.append(aid)
        return out

    # ---- 房间、门、连接与内部反锁 ----

    def neighbors(self, room_id: str) -> list[str]:
        """与该房间互相连通的房间（门在两边都存在才算数）。"""
        room = self.rooms.get(room_id)
        if not room:
            return []
        out = []
        for nid in (room.get("doors") or {}):
            other = self.rooms.get(nid)
            if other and room_id in (other.get("doors") or {}):
                out.append(nid)
        return out

    def door_between(self, room_id: str, other_id: str) -> dict:
        return dict(((self.rooms.get(room_id) or {}).get("doors") or {}).get(other_id) or {})

    def link_rooms(self, a: str, b: str, label: str = "房门", save: bool = True) -> bool:
        """把两个房间连起来：两边的 doors 各存一份，锁定状态按两边一致。"""
        if a == b or a not in self.rooms or b not in self.rooms:
            return False
        changed = False
        for src, dst in ((a, b), (b, a)):
            doors = self.rooms[src].setdefault("doors", {})
            if dst not in doors:
                doors[dst] = {"label": label, "locked": False, "by": "", "reason": ""}
                changed = True
        if changed and save:
            self.save()
        if changed:
            self._map_changed()
        return changed

    def unlink_rooms(self, a: str, b: str, save: bool = True) -> bool:
        changed = False
        for src, dst in ((a, b), (b, a)):
            doors = (self.rooms.get(src) or {}).get("doors") or {}
            if dst in doors:
                doors.pop(dst, None)
                self._sync_lock_summary(self.rooms[src])
                changed = True
        if changed and save:
            self.save()
        if changed:
            self._map_changed()
        return changed

    def path_between(self, a: str, b: str) -> list[str]:
        """两房间之间最短的通行路线（含两端）；走不到就返回空列表。"""
        if a == b:
            return [a]
        if a not in self.rooms or b not in self.rooms:
            return []
        prev = {a: ""}
        queue = [a]
        while queue:
            cur = queue.pop(0)
            for nid in self.neighbors(cur):
                if nid in prev:
                    continue
                prev[nid] = cur
                if nid == b:
                    path = [b]
                    while prev[path[-1]]:
                        path.append(prev[path[-1]])
                    return list(reversed(path))
                queue.append(nid)
        return []

    def prune_orphans(self, root: str = "lounge") -> list[str]:
        """删掉走不到公共客厅的房间：断开的子房间、生成失败留下的空房；里面的人先回到客厅。"""
        if root not in self.rooms:
            return []
        seen = {root}
        queue = [root]
        while queue:
            cur = queue.pop(0)
            for nid in self.neighbors(cur):
                if nid not in seen:
                    seen.add(nid)
                    queue.append(nid)
        dropped = []
        for rid in list(self.rooms):
            if rid in seen:
                continue
            room = self.rooms.get(rid) or {}
            if room.get("builtin") and rid in ("my_home", "lounge"):
                self.link_rooms(rid, root, save=False)
                continue
            dropped.append(rid)
        for rid in dropped:
            self.rooms.pop(rid, None)
            for other in self.rooms.values():
                (other.get("doors") or {}).pop(rid, None)
                self._sync_lock_summary(other)
            for aid, cur in list(self.occupants.items()):
                if cur == rid:
                    self.occupants[aid] = root
                    self.positions[aid] = "门边"
        if dropped:
            for other in self.rooms.values():
                self._sync_lock_summary(other)
            self.save()
            self._map_changed()
        return dropped

    def current_room_id(self) -> str:
        rid = self.occupants.get("user", "my_home")
        return rid if rid in self.rooms else "my_home"

    def current_room(self) -> dict:
        return self.rooms[self.current_room_id()]

    def room_list(self) -> list[dict]:
        return list(self.rooms.values())

    def add_room(self, raw: dict, creator_id: str = "user", is_public: bool = False,
                 attach_to: str = "") -> dict:
        data = dict(raw)
        data["owner"] = "" if is_public else creator_id
        data["is_public"] = bool(is_public)
        data["keys"] = [] if is_public else ([creator_id] if creator_id else [])
        data["locked_inside"] = False
        data["locked_by"] = ""
        data["locked_reason"] = ""
        data["doors"] = {}
        room = normalize_room(data)
        self.rooms[room["id"]] = room
        # 新房间从创建者所在的房间接一扇门；没指定就接公共客厅，保证走得到
        anchor = attach_to or self.occupants.get(creator_id) or "lounge"
        if anchor not in self.rooms or anchor == room["id"]:
            anchor = "lounge" if "lounge" in self.rooms else ""
        if anchor and anchor != room["id"]:
            self.link_rooms(anchor, room["id"], save=False)
        self.save()
        self._map_changed()
        return room

    def delete_room(self, room_id: str, actor_id: str = "user") -> bool:
        """删除房间：断掉所有门、把里面的人送回公共客厅；内置房间与公共客厅不能删。"""
        room = self.rooms.get(room_id)
        if not room or room.get("builtin") or room_id == "lounge":
            return False
        if actor_id and actor_id != "user" and room.get("owner") not in ("", actor_id):
            return False
        self.rooms.pop(room_id, None)
        for other in self.rooms.values():
            (other.get("doors") or {}).pop(room_id, None)
            self._sync_lock_summary(other)
        fallback = "lounge" if "lounge" in self.rooms else (next(iter(self.rooms), ""))
        for aid, rid in list(self.occupants.items()):
            if rid == room_id:
                self.occupants[aid] = fallback or persona_room_id(aid)
                if self.occupants[aid] not in self.rooms:
                    self.occupants[aid] = fallback
                self.positions[aid] = "门边"
        self.logs = [e for e in self.logs if e.get("room_id") != room_id]
        self.save()
        self._map_changed()
        return True

    def renovate_room(self, room_id: str, raw: dict) -> dict | None:
        """Apply an AI renovation while retaining identity, ownership and doors."""
        old = self.rooms.get(room_id)
        if not old:
            return None
        data = dict(raw or {})
        data.update({
            "id": room_id,
            "builtin": old.get("builtin", False),
            "owner": old.get("owner", ""),
            "is_public": old.get("is_public", False),
            "keys": list(old.get("keys") or []),
            "doors": copy.deepcopy(old.get("doors") or {}),
            "locked_inside": old.get("locked_inside", False),
            "locked_by": old.get("locked_by", ""),
            "locked_reason": old.get("locked_reason", ""),
        })
        room = normalize_room(data)
        self.rooms[room_id] = room
        self.save()
        self._map_changed()
        return room

    def rooms_brief(self, room_id: str) -> str:
        """给模型看的一句话：这间房通向哪里、门锁着没有。"""
        room = self.rooms.get(room_id) or {}
        parts = []
        for nid in self.neighbors(room_id):
            other = self.rooms.get(nid) or {}
            door = self.door_between(room_id, nid)
            lock = "（里侧反锁）" if door.get("locked") else ""
            parts.append(f"{other.get('name', nid)}{lock}")
        return "、".join(parts) or "没有门"

    def occupants_in(self, room_id: str) -> list[str]:
        return [aid for aid, rid in self.occupants.items() if rid == room_id]

    def has_key(self, actor_id: str, room_id: str) -> bool:
        room = self.rooms.get(room_id)
        if not room:
            return False
        if room.get("is_public"):
            return True
        return actor_id == room.get("owner") or actor_id in (room.get("keys") or [])

    def can_enter(self, actor_id: str, room_id: str) -> tuple[bool, str]:
        room = self.rooms.get(room_id)
        if not room:
            return False, "房间不存在"
        prev = self.occupants.get(actor_id)
        if prev == room_id:
            return True, ""
        if prev and prev in self.rooms:
            if room_id not in self.neighbors(prev):
                return False, tr("「{a}」和「{b}」之间没有门，只能先经过相连的房间。",
                                 a=self.rooms[prev]["name"], b=room["name"])
            if self.door_between(prev, room_id).get("locked"):
                return False, tr("「{room}」的门从里面反锁了，即使有钥匙也打不开。", room=room["name"])
        elif room.get("locked_inside"):
            return False, tr("「{room}」的门从里面反锁了，即使有钥匙也打不开。", room=room["name"])
        if room.get("is_public"):
            return True, ""
        if self.has_key(actor_id, room_id):
            return True, ""
        return False, tr("你没有「{room}」的钥匙，无法直接进入。", room=room["name"])

    def walk_to(self, actor_id: str, room_id: str, actor_name: str = "",
                bring_ids: list[str] = None, name_map: dict[str, str] = None) -> str:
        """沿相连的房间走过去（不会穿门而过）：中途被锁住就停在原地并返回原因。"""
        prev = self.occupants.get(actor_id)
        if not prev or prev == room_id or prev not in self.rooms:
            return self.enter_room(actor_id, room_id, actor_name=actor_name,
                                   bring_ids=bring_ids, name_map=name_map)
        path = self.path_between(prev, room_id)
        if not path:
            return tr("走不到「{room}」：中间没有相连的门。", room=(self.rooms.get(room_id) or {}).get("name", room_id))
        for hop in path[1:]:
            err = self.enter_room(actor_id, hop, actor_name=actor_name, name_map=name_map)
            if err:
                return err
        return ""

    def grant_key(self, room_id: str, grantor_id: str, target_id: str,
                  grantor_name: str = "", target_name: str = "") -> str:
        room = self.rooms.get(room_id)
        if not room:
            return "房间不存在"
        if room.get("is_public"):
            return "公共空间无需钥匙，所有人都能进入"
        if not self.has_key(grantor_id, room_id):
            return "只有持有该房间钥匙的人才能分发钥匙"
        if target_id not in room["keys"]:
            room["keys"].append(target_id)
            who = grantor_name or ("你" if grantor_id == "user" else grantor_id)
            to_who = target_name or ("你" if target_id == "user" else target_id)
            cur_rid = self.occupants.get(grantor_id) or room_id
            if cur_rid in self.rooms:
                self.append_log(cur_rid, "sys", "系统", ["all"], "sys",
                                tr("{who} 把「{room}」的钥匙分给了 {to}。", who=who, room=room["name"], to=to_who))
            self.save()
        return ""

    def revoke_key(self, room_id: str, owner_id: str, target_id: str) -> str:
        room = self.rooms.get(room_id)
        if not room or room.get("is_public"):
            return "无法收回公共空间钥匙"
        if room.get("owner") != owner_id:
            return "只有房主可以收回钥匙"
        if target_id == owner_id:
            return "不能收回房主自己的钥匙"
        if target_id in room["keys"]:
            room["keys"].remove(target_id)
            self.save()
        return ""

    def _set_door_lock(self, room_id: str, other_id: str, locked: bool, by: str = "", reason: str = ""):
        """一扇门两边的里侧/外侧同时上锁或解锁：门是共用的，状态必须一致。"""
        for src, dst in ((room_id, other_id), (other_id, room_id)):
            room = self.rooms.get(src)
            if not room:
                continue
            door = (room.get("doors") or {}).get(dst)
            if door is None:
                continue
            door["locked"] = bool(locked)
            door["by"] = str(by or "") if locked else ""
            door["reason"] = str(reason or "") if locked else ""
            self._sync_lock_summary(room)

    def set_room_lock(self, room_id: str, actor_id: str, locked: bool, actor_name: str = "",
                      reason: str = "manual", door_to: str = "") -> str:
        room = self.rooms.get(room_id)
        if not room:
            return "房间不存在"
        if self.occupants.get(actor_id) != room_id:
            return tr("只有站在房间的里侧才能反锁或打开门锁。")
        doors = list(room.get("doors") or {})
        if door_to:
            if door_to not in doors:
                return "这间房没有通向那里的门"
            doors = [door_to]
        if not doors:
            return "这间房没有门"
        already = all(bool(((room.get("doors") or {}).get(nid) or {}).get("locked")) for nid in doors)
        if already == bool(locked):
            if locked and reason:
                for nid in doors:
                    ((room.get("doors") or {}).get(nid) or {})["reason"] = reason
                self._sync_lock_summary(room)
            return ""
        for nid in doors:
            self._set_door_lock(room_id, nid, locked, actor_id, reason if locked else "")
        who = actor_name or ("你" if actor_id == "user" else actor_id)
        if locked:
            if reason == "focus":
                msg = tr("{who} 进入专注时段，回到房间并从里面反锁了房门。", who=who)
            elif reason == "sleep":
                msg = tr("{who} 进入睡眠时段，回到房间并从里面反锁了房门。", who=who)
            else:
                msg = tr("{who} 从里面反锁了房门（外面即使有钥匙也打不开）。", who=who)
        else:
            msg = tr("{who} 打开了房门的反锁。", who=who)
        self.append_log(room_id, "sys", "系统", ["all"], "sys", msg)
        self.save()
        return ""

    def enter_room(self, actor_id: str, room_id: str, actor_name: str = "", force: bool = False,
                   bring_ids: list[str] = None, name_map: dict[str, str] = None) -> str:
        """移动角色到目标房间（自动带走牵手/跟随的同伴，或显式指定的 bring_ids）。"""
        if room_id not in self.rooms:
            return "房间不存在"
        prev = self.occupants.get(actor_id)
        if prev == room_id:
            return ""
        if not force:
            door = self.door_between(prev, room_id) if prev else {}
            if door.get("locked") and str(door.get("by") or "") == actor_id:
                self._set_door_lock(prev, room_id, False)      # 自己的锁，自己从里侧打开
            ok, reason = self.can_enter(actor_id, room_id)
            if not ok:
                return reason

        # 收集需要一起带走的同伴（必须和出发者在同一个旧房间）
        companions = []
        if prev:
            for cid in self.companions_of(actor_id, prev):
                if cid not in companions:
                    companions.append(cid)
            for cid in (bring_ids or []):
                if cid != actor_id and self.occupants.get(cid) == prev and cid not in companions:
                    companions.append(cid)

        self.occupants[actor_id] = room_id
        self.positions[actor_id] = "门边"
        for cid in companions:
            self.occupants[cid] = room_id
            self.positions[cid] = "门边"

        # 注意力：走进用户所在的房间会注意到人，离开用户所在的房间注意力就散了
        now_att = _ms_now()
        user_rid = self.current_room_id()
        for mid in [actor_id, *companions]:
            if mid == "user":
                continue
            if user_rid == room_id:
                self.raise_attention(mid, ATTENTION_ENTER, now_att)
            elif prev and prev == user_rid:
                self.drop_attention(mid, ATTENTION_LEAVE, now_att)
        if actor_id == "user":
            for other in self.occupants_in(prev or ""):
                if other != "user":
                    self.drop_attention(other, ATTENTION_LEAVE, now_att)
            for other in self.occupants_in(room_id):
                if other != "user" and other not in companions:
                    self.raise_attention(other, ATTENTION_ENTER, now_att)

        nm = name_map or {}
        who = actor_name or nm.get(actor_id) or ("你" if actor_id == "user" else actor_id)
        comp_names = "、".join(nm.get(c, "你" if c == "user" else c) for c in companions)

        if prev and prev in self.rooms:
            if not self.occupants_in(prev) and (self.rooms[prev].get("doors") or {}).get(room_id, {}).get("locked"):
                self._set_door_lock(prev, room_id, False)
            if not self.occupants_in(prev) and self.rooms[prev].get("locked_inside"):
                self.rooms[prev]["locked_inside"] = False
                self.rooms[prev]["locked_by"] = ""
                self.rooms[prev]["locked_reason"] = ""
            leave_msg = (tr("{who} 带着 {comp} 推开门，离开了房间。", who=who, comp=comp_names)
                         if comp_names else tr("{who} 推开门，离开了房间。", who=who))
            self.append_log(prev, "sys", "系统", ["all"], "move", leave_msg)

        enter_msg = (tr("{who} 带着 {comp} 一起走进了「{room}」。", who=who, comp=comp_names, room=self.rooms[room_id]["name"])
                     if comp_names else tr("{who} 推开门，走进了「{room}」。", who=who, room=self.rooms[room_id]["name"]))
        self.append_log(room_id, "sys", "系统", ["all"], "move", enter_msg)
        self.save()
        return ""

    def join_room(self, actor_id: str, room_id: str, actor_name: str = "", force: bool = False) -> bool:
        return self.enter_room(actor_id, room_id, actor_name=actor_name, force=force) == ""

    def leave_room(self, actor_id: str, actor_name: str = "") -> bool:
        if actor_id == "user":
            return False
        prev = self.occupants.pop(actor_id, None)
        if not prev:
            return False
        self.drop_attention(actor_id, ATTENTION_LEAVE)
        who = actor_name or actor_id
        if prev in self.rooms:
            if not self.occupants_in(prev) and self.rooms[prev].get("locked_inside"):
                self.rooms[prev]["locked_inside"] = False
                self.rooms[prev]["locked_by"] = ""
                self.rooms[prev]["locked_reason"] = ""
            self.append_log(prev, "sys", "系统", ["all"], "move",
                            tr("{who} 离开了「{room}」。", who=who, room=self.rooms[prev]["name"]))
        self.save()
        return True

    # ---- 专注/睡眠自动反锁、互动时间与空闲系统随机活动 ----

    def sync_schedule_locks(self, status_map: dict[str, str], name_map: dict[str, str]) -> bool:
        """专注/睡眠期间，对方会自动回到自己的房间并从内部反锁；结束后若仍为自动反锁则解锁。"""
        changed = False
        for aid, kind in status_map.items():
            if aid == "user":
                continue
            own_rid = persona_room_id(aid)
            if own_rid not in self.rooms:
                continue
            name = name_map.get(aid, aid)
            room = self.rooms[own_rid]
            if kind in ("focus", "sleep"):
                # 专注/睡眠时解除跟随与牵手，独自回房反锁
                self.following.pop(aid, None)
                self._release_all_hands(aid)
                if self.occupants.get(aid) != own_rid:
                    self.enter_room(aid, own_rid, actor_name=name, force=True, name_map=name_map)
                    changed = True
                if not room.get("locked_inside") or room.get("locked_reason") != kind:
                    self.set_room_lock(own_rid, aid, True, actor_name=name, reason=kind)
                    changed = True
            else:
                if room.get("locked_inside") and room.get("locked_by") == aid and room.get("locked_reason") in ("focus", "sleep"):
                    self.set_room_lock(own_rid, aid, False, actor_name=name)
                    changed = True
        return changed

    # ---- 注意力：对话与生活模拟之间的连续过渡 ----

    def attention_of(self, actor_id: str, now_ms: int = None) -> float:
        """当前注意力：存档值按 attn_half_min 的半衰期衰减到此刻。"""
        if not actor_id:
            return 0.0
        val = float(self.attention.get(actor_id) or 0.0)
        if val <= 0:
            return 0.0
        ts = int(self.attention_ts.get(actor_id) or 0)
        now = _ms_now(now_ms)
        if ts and now > ts:
            half = max(1, int(self.attn_half_min or ATTENTION_HALF_MIN)) * 60_000
            val *= 0.5 ** ((now - ts) / half)
        return max(0.0, min(1.0, val))

    def _set_attention(self, actor_id: str, val: float, now: int):
        self.attention[actor_id] = round(max(0.0, min(1.0, float(val))), 4)
        self.attention_ts[actor_id] = int(now)

    def raise_attention(self, actor_id: str, amount: float = ATTENTION_SPEECH, now_ms: int = None) -> float:
        """抬起注意力（1.0 表示立刻满）；已经更高时保持原值。"""
        if not actor_id or actor_id == "user":
            return 0.0
        now = _ms_now(now_ms)
        val = min(1.0, max(self.attention_of(actor_id, now), float(amount)))
        self._set_attention(actor_id, val, now)
        return val

    def drop_attention(self, actor_id: str, amount: float = ATTENTION_LEAVE, now_ms: int = None) -> float:
        """扣注意力：离开房间、被留在原地这类事情让人把注意力收回到自己的事上。"""
        if not actor_id or actor_id == "user":
            return 0.0
        now = _ms_now(now_ms)
        val = max(0.0, self.attention_of(actor_id, now) - float(amount))
        self._set_attention(actor_id, val, now)
        self.activities.pop(actor_id, None)          # 注意力散了，正在做的事也就放下了
        return val

    def awake(self, actor_id: str, now_ms: int = None) -> bool:
        """注意力还在阈值之上：这一轮由模型接管。"""
        return self.attention_of(actor_id, now_ms) >= ATTENTION_WAKE

    def auto_budget_left(self, actor_id: str, now_ms: int = None, per_hour: int = None) -> int:
        """这一小时还能自主开口几次（只用来自主发言；回应用户不受限）。"""
        now = _ms_now(now_ms)
        cap = ATTENTION_AUTO_PER_HOUR if per_hour is None else max(0, int(per_hour))
        kept = [t for t in (self.auto_turns.get(actor_id) or []) if -60_000 < now - int(t) < 3_600_000]
        self.auto_turns[actor_id] = kept
        return max(0, cap - len(kept))

    def spend_auto_budget(self, actor_id: str, now_ms: int = None):
        now = _ms_now(now_ms)
        kept = [t for t in (self.auto_turns.get(actor_id) or []) if -60_000 < now - int(t) < 3_600_000]
        kept.append(now)
        self.auto_turns[actor_id] = kept[-32:]

    def add_heard(self, actor_id: str, text: str, room_name: str = "", who: str = "你",
                  now_ms: int = None) -> bool:
        """当时答不上的呼唤先记下来（在专注 / 睡眠、或不在场），下次轮到他的回合一并补上。"""
        clean = str(text or "").strip()[:200]
        if not actor_id or actor_id == "user" or not clean:
            return False
        items = list(self.heard.get(actor_id) or [])
        items.append({"text": clean, "room": str(room_name or "")[:30],
                      "who": str(who or "你")[:30], "ts": _ms_now(now_ms)})
        self.heard[actor_id] = items[-ATTENTION_HEARD_MAX:]
        self.raise_attention(actor_id, ATTENTION_SUMMON, now_ms)
        return True

    def take_heard(self, actor_id: str) -> list[dict]:
        """取走记下的呼唤（下一次模型回合用掉）。"""
        return self.heard.pop(actor_id, []) or []

    def note_phone_contact(self, actor_id: str, now_ms: int = None) -> float:
        """手机沟通里收到用户的消息：也算互动，不再只有房间里说话才算。"""
        if not actor_id or actor_id == "user":
            return 0.0
        now = _ms_now(now_ms)
        self.last_user_interact[actor_id] = now
        return self.raise_attention(actor_id, ATTENTION_PHONE, now)

    def activity_label(self, actor_id: str, now_ms: int = None, schedule_kind: str = "") -> str:
        if schedule_kind == "focus":
            return "专注中"
        if schedule_kind == "sleep":
            return "睡眠中"
        if self.following.get(actor_id) == "user":
            return "跟随中"
        for s in ("left", "right"):
            if self._hand(actor_id, s)["state"] in ("holding", "held"):
                return "牵手中"
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        act = self.activities.get(actor_id)
        mood = self.mood_of(actor_id, now)
        base = f"{act['label']}中" if (act and int(act.get("until") or 0) > now and act.get("label")) else ""
        if mood:
            return f"{base}·{mood.get('label') or '不快'}" if base else str(mood.get("label") or "不快")
        return base

    def awareness_brief(self, actor_id: str, limit: int = 4) -> str:
        """给模型的听觉与空间说明：谁在你这间房、门通向哪里、隔壁是谁、各音量意味着什么。"""
        rid = self.occupants.get(actor_id)
        if not rid or rid not in self.rooms:
            return ""
        room = self.rooms.get(rid) or {}
        here = [f"{self.name_of(a)}(位置={self.position_of(a)})"
                for a in self.occupants_in(rid) if a != actor_id]
        nb_lines = []
        for nid in self.neighbors(rid):
            other = self.rooms.get(nid) or {}
            door = self.door_between(rid, nid)
            who = [f"{self.name_of(a)}({self.activity_label(a) or '在场'})"
                   for a in self.occupants_in(nid) if a != actor_id]
            tag = "门里侧反锁·过不去" if door.get("locked") else "门没锁·可以过去"
            nb_lines.append(f"- {other.get('name', nid)}（{tag}）：{'、'.join(who) or '没有人'}")
        recent = []
        for e in self.logs[-12:]:
            if e.get("kind") not in ("say", "action") or not e.get("text"):
                continue
            if e.get("room_id") != rid and not self.spills_to(e, rid):
                continue
            vol = VOLUME_LABELS.get(e.get("volume") or "normal", "正常音量")
            where = "隔壁传来" if e.get("room_id") != rid else ""
            recent.append(f"- {e.get('sender_name')}（{vol}{'·' + where if where else ''}）：{str(e.get('text'))[:60]}")
        lines = [
            "【听觉与空间】",
            f"- 你所在的房间：{room.get('name', rid)}；门通向：{self.rooms_brief(rid)}",
            f"- 同房间的人：{'、'.join(here[:limit]) or '只有你自己'}",
            "- 隔壁房间：" + ("\n  ".join(nb_lines) if nb_lines else "没有相邻的房间"),
            "- 音量的含义：正常音量(normal)=同房间的人都听得清；轻语(whisper)=只有与你同一位置的人才听得见；"
            "大声呼喊(shout)=隔壁房间也能听见，但会吵到正在看书 / 弹琴 / 休息的人。",
        ]
        if recent:
            lines.append("- 最近的发言（含音量）：\n  " + "\n  ".join(recent[-4:]))
        return "\n".join(lines)

    def perform_idle_system_step(self, actor_id: str, actor_name: str,
                                 now_ms: int = None, rng=None) -> str:
        """超过 N 分钟未与用户互动：由系统随机移动或开展持续活动（弹琴/看书/休息等），不调用模型也不发言。"""
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        r = rng or random
        cur_act = self.activities.get(actor_id)
        if cur_act and int(cur_act.get("until") or 0) > now:
            return ""

        cur_rid = self.occupants.get(actor_id)
        if not cur_rid or cur_rid not in self.rooms:
            return ""
        if self.pending_of(actor_id) or any(self.holding_of(actor_id, s) for s in ("left", "right")):
            return ""                       # 手上有事（待发动作 / 正牵着人）就不随意走开

        enterable = [
            rid for rid in self.neighbors(cur_rid)
            if self.can_enter(actor_id, rid)[0]
        ]
        if enterable and r.random() < 0.35:
            target_rid = r.choice(enterable)
            if self.enter_room(actor_id, target_rid, actor_name=actor_name) == "":
                self.activities.pop(actor_id, None)
                self.save()
                return f"move:{target_rid}"

        label, lo_m, hi_m, tpl = r.choice(IDLE_ACTIVITIES)
        mins = r.randint(lo_m, hi_m)
        self.activities[actor_id] = {
            "label": label,
            "until": now + mins * 60 * 1000,
            "room_id": cur_rid,
        }
        msg = tr(tpl, who=actor_name, m=mins)
        self.append_log(cur_rid, "sys", "系统", ["all"], "sys", msg)
        self.save()
        return f"activity:{label}"

    # ---- 房间互动计数（用于部位解锁） ----

    def room_msg_count(self, actor_id: str) -> int:
        return int(self.room_counts.get(actor_id) or 0)

    def _bump_room_counts(self, room_id: str, sender: str, targets: list[str], kind: str):
        if kind not in ("say", "action"):
            return
        now = _ms_now()
        occ = self.occupants_in(room_id)
        if sender == "user":
            heard = [aid for aid in occ if aid != "user" and ("all" in targets or aid in targets)]
            for aid in heard:
                self.room_counts[aid] = self.room_counts.get(aid, 0) + 1
                self.last_user_interact[aid] = now
                self.activities.pop(aid, None)
                self.raise_attention(aid, ATTENTION_SPEECH, now)
        elif "all" in targets or "user" in targets:
            self.room_counts[sender] = self.room_counts.get(sender, 0) + 1
            self.last_user_interact[sender] = now
            self.raise_attention(sender, ATTENTION_SPEECH, now)

    def pair_msg_count(self, a: str, b: str) -> int:
        """两个实体之间的互动条数：与用户之间取房间累计，其余按同房可听见的发言/动作累计。"""
        if not a or not b or a == b:
            return 0
        if "user" in (a, b):
            return self.room_msg_count(b if a == "user" else a)
        n = 0
        for e in self.logs:
            if e.get("kind") not in ("say", "action"):
                continue
            sender, targets = e.get("sender"), e.get("targets") or []
            if sender == a and ("all" in targets or b in targets):
                n += 1
            elif sender == b and ("all" in targets or a in targets):
                n += 1
        return n

    # ---- 双手槽位、独立目光与部位互动（用户与所有人设都是平等的实体） ----

    def _hand(self, actor_id: str, side: str) -> dict:
        """取某实体某只手的槽位（惰性创建，顺手清掉失效的临时状态）。"""
        sides = self.hands.setdefault(actor_id, {})
        h = sides.get(side)
        if not isinstance(h, dict):
            h = {"state": "free", "note": "", "link": "", "ts": 0}
            sides[side] = h
        return self._normalize_hand(actor_id, side, h)

    def _set_hand(self, actor_id: str, side: str, state: str, note: str = "",
                  link: str = "", ts: int | None = None):
        self.hands.setdefault(actor_id, {})[side] = {
            "state": state if state in HAND_STATES else "free",
            "note": str(note or "")[:60],
            "link": str(link or ""),
            "ts": int(time.time() * 1000) if ts is None else int(ts),
        }

    def _normalize_hand(self, actor_id: str, side: str, h: dict) -> dict:
        """临时状态过期、或牵手配对的另一头已经断开时，手自动恢复自由。"""
        st = h.get("state")
        if st == "used" and int(time.time() * 1000) - int(h.get("ts") or 0) > HAND_USED_TTL_MS:
            h.update({"state": "free", "note": "", "link": "", "ts": 0})
        elif st == "held":
            holder = h.get("link") or ""
            other = self.hands.get(holder, {}).get(side) if holder else None
            if not holder or not isinstance(other, dict) or other.get("link") != actor_id:
                h.update({"state": "free", "note": "", "link": "", "ts": 0})
        elif st == "holding":
            target = (self.holding.get(actor_id) or {}).get(side) or ""
            other = self.hands.get(target, {}).get(side) if target else None
            if not target or not isinstance(other, dict) or other.get("link") != actor_id:
                self.holding.get(actor_id, {}).pop(side, None)
                h.update({"state": "free", "note": "", "link": "", "ts": 0})
        return h

    def _detach_hand(self, actor_id: str, side: str):
        """把这只手从牵手配对里拆出来，另一侧的手同步恢复自由。"""
        if side not in ("left", "right"):
            return
        h = self._hand(actor_id, side)
        other = h.get("link") or (self.holding.get(actor_id) or {}).get(side) or ""
        self.holding.get(actor_id, {}).pop(side, None)
        h.update({"state": "free", "note": "", "link": "", "ts": 0})
        if not other or other == actor_id:
            return
        other_h = self._hand(other, side)
        if other_h.get("link") == actor_id:
            self.holding.get(other, {}).pop(side, None)
            other_h.update({"state": "free", "note": "", "link": "", "ts": 0})

    def _release_all_hands(self, actor_id: str):
        for side in ("left", "right"):
            self._detach_hand(actor_id, side)

    def hands_of(self, actor_id: str) -> dict[str, dict]:
        return {side: self._hand(actor_id, side) for side in ("left", "right")}

    def hand_state(self, actor_id: str, side: str) -> str:
        return self._hand(actor_id, side)["state"]

    def hand_label(self, actor_id: str, side: str) -> str:
        name = "左手" if side == "left" else "右手"
        h = self._hand(actor_id, side)
        st, note = h["state"], h.get("note") or ""
        if st == "bound":
            return f"{name}: 被束缚" + (f"({note})" if note else "")
        if st == "held":
            return f"{name}: {note or '被握住'}"
        if st == "holding":
            return f"{name}: {note or '握住'}"
        if st == "used":
            return f"{name}: {note or '刚刚用过'}"
        return f"{name}: 自由"

    def hands_brief(self, actor_id: str) -> str:
        return "、".join(self.hand_label(actor_id, s).replace(": ", "")
                         for s in ("left", "right"))

    def available_hands(self, actor_id: str) -> list[str]:
        """自由 / 握住 / 刚用过的手都能脱离原动作再作新动作；被束缚或被别人牵着的不行。"""
        return [side for side in ("left", "right")
                if self._hand(actor_id, side)["state"] not in ("bound", "held")]

    def holding_of(self, actor_id: str, side: str) -> str:
        if self._hand(actor_id, side)["state"] != "holding":
            return ""
        return (self.holding.get(actor_id) or {}).get(side) or ""

    def pending_of(self, actor_id: str) -> list[dict]:
        return list(self.pending.get(actor_id) or [])

    def queue_hand_action(self, actor_id: str, hand: str, target_id: str, target_name: str, part: str,
                          text: str, msg_count: int | None = None, secret: bool = False) -> str:
        clean_part = str(part or "").strip()[:30]
        is_gaze = (clean_part in ("gaze", "目光注视") or hand == "gaze")
        channel = "gaze" if is_gaze else hand
        if channel not in ("left", "right", "gaze"):
            return "请指定左手或右手"
        if target_id and target_id == actor_id:
            return tr("不能对自己使用动作。")
        if channel in ("left", "right"):
            st = self.hand_state(actor_id, channel)
            if st == "bound":
                return "这只手正被束缚着，暂时无法行动"
            if st == "held":
                return tr("这只手正被拉着，先抽回来才能做别的动作。")
            if clean_part != "设施" and target_id in self.occupants:
                if self.occupants.get(actor_id) != self.occupants.get(target_id):
                    return "对方不在你当前所在的房间"
                if self.position_of(actor_id) != self.position_of(target_id):
                    return "你和对方不在同一个位置，需先走到对方身边才能使用动作"
        if msg_count is not None and clean_part != "设施":
            ok, _req = part_allowed(clean_part, msg_count)
            if not ok:
                return tr("「{part}」暂未解锁，再多聊一些吧。", part=clean_part)
        clean_text = str(text or "").strip()[:300]

        # 自由或握住状态的手安排新动作时自动脱离原握持；左手、右手、目光各自独立且可同时共存
        if channel in ("left", "right"):
            self._detach_hand(actor_id, channel)
        acts = [a for a in (self.pending.get(actor_id) or []) if a.get("hand") != channel]
        acts.append({
            "hand": channel,
            "target_id": target_id,
            "target_name": target_name,
            "part": clean_part,
            "text": clean_text,
            "secret": bool(secret),
        })
        self.pending[actor_id] = acts
        return ""

    def remove_pending_action(self, actor_id: str, index: int):
        acts = self.pending.get(actor_id) or []
        if 0 <= index < len(acts):
            acts.pop(index)
        self.pending[actor_id] = acts

    def take_pending(self, actor_id: str, actor_name: str = "") -> list[dict]:
        """取走该实体这一轮的待发动作，并把它用到的手标记成「刚刚用过」（对方相应部位同步）。"""
        acts = self.pending.pop(actor_id, []) or []
        now = int(time.time() * 1000)
        who = actor_name or ("你" if actor_id == "user" else actor_id)
        for a in acts:
            channel = a.get("hand")
            part = str(a.get("part") or "")
            if channel not in ("left", "right"):
                continue
            note = "设施互动" if part == "设施" else f"→{a.get('target_name') or ''}·{part}"
            self._set_hand(actor_id, channel, "used", note, ts=now)
            target = str(a.get("target_id") or "")
            side = HAND_TARGET_SIDES.get(part)
            if not target or target == actor_id or side is None or part == "牵手带走":
                continue
            target_side = side or channel
            if self._hand(target, target_side)["state"] in ("free", "used"):
                self._set_hand(target, target_side, "used", f"{who}·{part}", ts=now)
                self.raise_attention(target, ATTENTION_TOUCH, now)
        return acts

    def set_hand_bound(self, actor_id: str, side: str, bound: bool, note: str = ""):
        sides = ("left", "right") if side == "both" else ((side,) if side in ("left", "right") else ())
        if sides:
            self.raise_attention(actor_id, ATTENTION_TOUCH)
        for s in sides:
            if bound:
                self._detach_hand(actor_id, s)
                self.pending[actor_id] = [a for a in (self.pending.get(actor_id) or [])
                                          if a.get("hand") not in (s, "both")]
                self._set_hand(actor_id, s, "bound", note)
            elif self._hand(actor_id, s)["state"] == "bound":
                self._set_hand(actor_id, s, "free", "")
        self.save()

    def reset_turn_hands(self, actor_id: str, save: bool = True):
        self.pending[actor_id] = []
        for side in ("left", "right"):
            if self._hand(actor_id, side)["state"] == "used":
                self._set_hand(actor_id, side, "free", "")
        if save:
            self.save()

    # ---- 定向听觉隔离日志 ----

    def append_log(self, room_id: str, sender: str, sender_name: str, targets: list[str],
                   kind: str, text: str, actions: list[dict] = None,
                   volume: str = "normal", pos: str = "") -> dict:
        clean_targets = [str(t) for t in (targets or ["all"]) if str(t)]
        if not clean_targets:
            clean_targets = ["all"]
        vol = str(volume or "").lower()
        if vol not in VOLUMES:
            vol = "normal"
        entry = {
            "id": uuid.uuid4().hex[:10],
            "ts": int(time.time() * 1000),
            "room_id": room_id,
            "sender": sender,
            "sender_name": sender_name,
            "targets": clean_targets,
            "volume": vol,
            "pos": str(pos or (self.position_of(sender) if sender != "sys" else "")),
            "kind": kind,
            "text": str(text or "").strip(),
            "actions": list(actions or []),
        }
        if vol == "shout" and kind in ("say", "action"):
            entry["spill"] = self.neighbors(room_id)      # 隔壁房间也听得见
            self._annoy_quiet_ones(room_id, sender, sender_name)
        self.logs.append(entry)
        self._bump_room_counts(room_id, sender, clean_targets, kind)
        if len(self.logs) > MAX_ROOM_LOGS:
            self.logs = self.logs[-MAX_ROOM_LOGS:]
        self.save()
        return entry

    def clear_logs(self) -> int:
        """清空小屋聊天历史与由这些互动记录累计的部位解锁计数。"""
        removed = len(self.logs)
        self.logs = []
        self.room_counts = {}
        self.last_user_interact = {}
        self.save()
        return removed

    def _annoy_quiet_ones(self, room_id: str, sender: str, sender_name: str):
        """大声喊到正在看书 / 弹琴 / 休息的人：记一段不快，并留一句话。"""
        now = _ms_now()
        for aid in self.occupants_in(room_id):
            if aid == sender or aid == "user":
                continue
            act = self.activities.get(aid) or {}
            if int(act.get("until") or 0) <= now or act.get("label") not in QUIET_LABELS:
                continue
            self.moods[aid] = {"label": "被吵到", "until": now + MOOD_MINUTES * 60_000, "reason": sender_name}
            self.append_log(room_id, "sys", "系统", ["all"], "sys",
                            tr("{who} 被这一声吼得皱起眉，手上的{act}停了一下。",
                               who=self.name_of(aid), act=act.get("label") or tr("事")))

    def name_of(self, actor_id: str) -> str:
        if actor_id == "user":
            return self.name_map.get("user") or "你"
        return self.name_map.get(actor_id) or actor_id

    def mood_of(self, actor_id: str, now_ms: int = None) -> dict:
        mood = self.moods.get(actor_id)
        if not mood:
            return {}
        if int(mood.get("until") or 0) <= _ms_now(now_ms):
            self.moods.pop(actor_id, None)
            return {}
        return dict(mood)

    def can_hear(self, entry: dict, actor_id: str) -> bool:
        if entry.get("kind") in ("sys", "move"):
            return True
        if entry.get("sender") == actor_id:
            return True
        if self.spills_to(entry, self.occupants.get(actor_id) or ""):
            return True
        targets = entry.get("targets") or []
        return "all" in targets or actor_id in targets

    def spills_to(self, entry: dict, room_id: str) -> bool:
        """大声呼喊从隔壁房间传过来的记录：只有被传到的房间看得见，且能看出是从哪传来的。"""
        if not room_id or entry.get("volume") != "shout":
            return False
        return room_id in (entry.get("spill") or [])

    def entry_for_room(self, entry: dict, room_id: str) -> dict:
        """同一个记录在不同房间里看：在声源房间之外时标上 from_room，前端与提示词据此说明是隔墙传来的。"""
        item = dict(entry)
        item.pop("spill", None)
        if entry.get("room_id") != room_id:
            item["from_room"] = (self.rooms.get(entry.get("room_id")) or {}).get("name", "")
        return item

    def visible_actions_for(self, entry: dict, actor_id: str) -> list[dict]:
        """同一房间内公开动作人人可见；标记为 secret 的隐秘接触仅发起者与被接触者可见。"""
        sender = entry.get("sender")
        out = []
        for a in entry.get("actions") or []:
            if not a.get("secret"):
                out.append(a)
            elif actor_id == sender or actor_id == a.get("target_id"):
                out.append(a)
        return out

    def visible_logs_for(self, actor_id: str, room_id: str, limit: int = 35) -> list[dict]:
        """返回房间内该角色能听见或看见的记录（非隐秘动作全房可见，隐秘接触仅双方可见，轻语仅同位置可听；大声呼喊隔壁也能听到）。"""
        out = []
        for e in self.logs:
            in_room = e.get("room_id") == room_id
            if not in_room and not self.spills_to(e, room_id):
                continue
            if e.get("kind") in ("sys", "move"):
                out.append(e if in_room else self.entry_for_room(e, room_id))
                continue
            can_hear_speech = self.can_hear(e, actor_id) if in_room else self.spills_to(e, room_id)
            vis_acts = self.visible_actions_for(e, actor_id) if in_room else []
            if not can_hear_speech and not vis_acts:
                continue
            item = self.entry_for_room(e, room_id) if not in_room else dict(e)
            item["actions"] = vis_acts
            if not can_hear_speech:
                item["text"] = ""
            out.append(item)
        return out[-limit:]

    def recent_logs_for_actor(self, actor_id: str, limit: int = 12) -> list[dict]:
        """跨房间获取某角色亲历或听见的近期小屋记录（严格遵守听觉与隐秘动作隔离）。"""
        out = []
        my_rid = self.occupants.get(actor_id)
        for e in self.logs:
            kind = e.get("kind")
            if kind in ("sys", "move"):
                if e.get("room_id") == my_rid or e.get("sender") == actor_id:
                    out.append(e)
                continue
            can_hear_speech = self.can_hear(e, actor_id)
            vis_acts = self.visible_actions_for(e, actor_id)
            if not can_hear_speech and not vis_acts:
                continue
            # 若不在同房间且既非发言人也非定向听众/动作目标，跳过
            if e.get("room_id") != my_rid and e.get("sender") != actor_id and actor_id not in (e.get("targets") or []):
                if not any(a.get("target_id") == actor_id for a in vis_acts):
                    continue
            item = dict(e)
            item["actions"] = vis_acts
            if not can_hear_speech:
                item["text"] = ""
            out.append(item)
        return out[-limit:]

    def user_display_logs(self, room_id: str, name_map: dict[str, str], limit: int = 120) -> list[dict]:
        out = []
        picked = [x for x in self.logs if x.get("room_id") == room_id or self.spills_to(x, room_id)]
        for e in picked[-limit:]:
            spilled = e.get("room_id") != room_id
            if self.can_hear(e, "user") or spilled:
                item = self.entry_for_room(e, room_id) if spilled else dict(e)
                item["redacted"] = False
                out.append(item)
            else:
                where = e.get("pos") or self.position_of(e["sender"])
                out.append({
                    "id": e["id"],
                    "ts": e["ts"],
                    "room_id": room_id,
                    "sender": e["sender"],
                    "sender_name": e["sender_name"],
                    "targets": e.get("targets") or [],
                    "volume": e.get("volume", "whisper"),
                    "pos": where,
                    "kind": "private_hint",
                    "text": tr("{who} 在「{pos}」轻声说了些什么（你不在同一位置，没有听清）。",
                               who=e["sender_name"], pos=where),
                    "actions": [],
                    "redacted": True,
                })
        return out


# ══════════════ AI 协议与辅助 ══════════════

def _extract_json(text: str) -> dict | None:
    s = str(text or "").strip()
    if not s:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.S)
    if fenced:
        s = fenced.group(1)
    else:
        start = s.find("{")
        end = s.rfind("}")
        if start >= 0 and end > start:
            s = s[start:end + 1]
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def build_map_generation_prompt(theme: str, desc: str, old_map: str = "", topology: str = "") -> tuple[str, list[dict]]:
    system = (
        "你是古早文字冒险游戏的场景地图设计师。请根据用户协商的主题与场景描述，生成或装修一个字符画房间地图。\n"
        "严格只输出一个 JSON 对象，不要附加任何解释或多余文字，字段如下：\n"
        "{\n"
        '  "name": "房间名称(不超过12字)",\n'
        '  "theme": "场景主题",\n'
        '  "ambience": "一句话氛围描写(不超过60字)",\n'
        '  "ascii_map": ["第一行", "第二行", ...],\n'
        '  "facilities": [{"token": "[设施名]", "label": "设施名", "action": "piano|bookshelf|desk|board|door|position|timer|contract|knowledge|music|coread|joint|custom", "desc": "简短说明"}],\n'
        '  "map_note": "用自然语言说明本地图中各个空间、门和相邻房间的连接关系"\n'
        "}\n"
        "要求：\n"
        "1. ascii_map 使用 Unicode 框线字符（┌ ─ ┐ │ └ ┘）绘制一个 8~12 行整齐的房间，内部尽量包含 [椅子] 和 [墙边] 等位置节点。\n"
        "2. 所有可点击设施与位置必须用半角方括号包裹，如 [钢琴]、[书架]、[书桌]、[椅子]、[墙边]、[门]。\n"
        "3. 地图底部必须包含 [门]（action 为 door），方便玩家进出房间。\n"
        "4. action 映射：钢琴/乐器用 piano，书架用 bookshelf，书桌用 desk，布告板用 board，门用 door；计时/合约/知识库/音乐/共读/联合行动分别用 timer/contract/knowledge/music/coread/joint；椅子/墙边/沙发/窗台等位置用 position，其他用 custom。\n"
        "5. 如果提供了旧地图，必须在其基础上修改而不是忽略；保留合理的既有空间与设施，并落实用户的新要求。"
    )
    user_msg = f"主题：{theme.strip()}\n场景设定与细节：{desc.strip() or '按主题自由发挥，富有复古文字游戏质感。'}"
    if old_map:
        user_msg += f"\n\n旧地图（请参考并修改）：\n{old_map}"
    if topology:
        user_msg += f"\n\n当前世界房间连接（请用 map_note 自然语言复述相关连接）：\n{topology}"
    return system, [{"role": "user", "content": user_msg}]


def parse_generated_room(reply_text: str, fallback_theme: str = "自定义场景", room_id: str = "") -> dict | None:
    obj = _extract_json(reply_text)
    if not obj or not obj.get("ascii_map"):
        return None
    obj = dict(obj)
    obj["builtin"] = False
    obj.setdefault("theme", fallback_theme)
    obj["id"] = room_id or f"custom_{uuid.uuid4().hex[:8]}"
    return normalize_room(obj)


def build_actor_turn_prompt(persona_sys: str, store: RoomStore, room: dict, actor_id: str, actor_name: str,
                            all_names: dict[str, str], visible_logs: list[dict],
                            trigger_reason: str = "user",
                            memory_block: str = "", phone_history: str = "",
                            extra_note: str = "", awareness: str = "") -> tuple[str, list[dict]]:
    occ_ids = store.occupants_in(room["id"])
    occ_desc = "、".join(
        f"{all_names.get(aid, aid)}(id={aid}, 位置={store.position_of(aid)})"
        for aid in occ_ids
    )
    hand_desc = "；".join(
        f"{all_names.get(aid, aid)}：{store.hands_brief(aid)}" for aid in occ_ids
    )
    part_desc = "、".join(f"{lbl}({req}条)" if req else lbl for _pid, lbl, req in BODY_PARTS)
    map_text = "\n".join(room.get("ascii_map") or [])

    world_lines = []
    for r in store.room_list():
        r_occ = [all_names.get(a, a) for a in store.occupants_in(r["id"])]
        ok, _ = store.can_enter(actor_id, r["id"])
        perm = "公共" if r.get("is_public") else ("你有钥匙" if store.has_key(actor_id, r["id"]) else "无钥匙")
        lock_s = "·已内部反锁" if r.get("locked_inside") else ""
        world_lines.append(f"- {r['name']}(id={r['id']}, {perm}{lock_s}, {'可进入' if ok else '不可进入'}): 在场 {', '.join(r_occ) or '空'}")

    room_guide = (
        "\n\n【当前场景：古早文字游戏房间世界】\n"
        f"当前房间：{room['name']}(id={room['id']}，主题：{room['theme']}，门锁：{'已从内部反锁' if room.get('locked_inside') else '未反锁'})\n"
        f"氛围：{room.get('ambience', '')}\n"
        f"本房间在场成员与位置：{occ_desc}\n"
        f"你的身份：{actor_name}(id={actor_id})\n"
        f"所有在场成员（包括你与用户）的双手状态（同一房间内公开可见）：{hand_desc}\n"
        f"房间字符画地图：\n{map_text}\n\n"
        "【世界房间列表】\n" + "\n".join(world_lines) + "\n\n"
        "【规则】\n"
        "1. 原来的沟通页面代表手机聊天（可以随时私发给任何人）；而这里是面对面的现实房间空间，无法随意屏蔽某人，只能通过音量控制：\n"
        "   - volume=\"normal\"（正常音量）：房间里所有人都能听见。\n"
        "   - volume=\"whisper\"（轻语）：只有和你处于【同一室内位置】的人能听见，其他位置的人听不清。\n"
        "   - volume=\"shout\"（大声呼喊）：整个房间都听得见，隔壁的房间也听得见；正在看书 / 弹琴 / 休息的人会被吵到，不会高兴。\n"
        "2. 同一房间内的公开动作（牵手、移动位置、小动作等）大家都看得到；标注了[隐秘]的身体接触只有双方知道，旁人看不见。\n"
        "3. 为避免显得话多，如果你只是在旁边看着、或者此刻不需要插话，请把 silent 设为 true（此时 speech 留空，不发消息打扰）。\n"
        "4. 你可以结合当前位置（如墙边、椅子、沙发、钢琴旁）做位置相关互动，并用 position 字段更新你的室内位置。\n"
        "5. 如果用户言语请求你跟随同行，同意可将 follow 设为 \"user\"（取消跟随设为 \"none\"，不变为 null）。\n"
        "6. 房间之间靠门相连，只有相连的房间才能走过去（move_to 填房间 id；隔了几间房的会依次穿过中间的房间）。"
        "门只有站在房间的里侧才能反锁；若想把用户一起带去那个房间，可将 bring_user 设为 true。\n"
        f"7. 你和房间里每个人都是平等的实体，可以用 actions 数组对任何一个同房间同位置的成员（包括用户）做手部或目光动作，与用户能做的动作完全一样。每项格式：{{\"hand\": \"left|right|gaze\", \"target\": \"成员 id\", \"part\": \"部位\", \"text\": \"动作描写\", \"secret\": false}}。可用部位：{part_desc}；part 写“设施”表示触碰设施而不针对人。left/right 的手必须能自由活动（被束缚或被别人牵着的手不行）；部位门槛按你与该成员累计互动条数计算，未达门槛的动作会被系统拒绝。最多三项，每个手/目光各占一个槽位。\n"
        "8. 当用户明确提出牵手、拉着一起走/带走，或你按当前关系与情境自然决定牵住某人时，用 hold_hand 填 left 或 right（默认牵用户，可加 hold_target 指定同房间同位置的其他成员），按用户要求松开时填 release；没有牵手意图时必须为 null。只能选择双方当前可用且未被束缚的手，且需同室同位置、互动累计达到牵手门槛。这个字段就是牵手带走的唯一触发方式，不要要求用户点击按钮。\n"
        "9. 若不想被打扰可将 lock_door 设为 true 从内部反锁（false 解锁，null 不变）；分钥匙可填 give_key: {\"room_id\": \"...\", \"to\": \"user\"}；把某人的双手束缚/放开可填 bind_hands（left / right / both / release，默认对象是用户，可加 bind_target 指定）。\n"
        "10. 请严格只输出一个 JSON 对象，保持你原本的人设语气：\n"
        '{"silent": false, "volume": "normal", "action": "简短动作描写(可为空)", "speech": "你的台词(可为空)", '
        '"actions": [], "position": null, "bind_hands": null, "bind_target": null, "hold_hand": null, "hold_target": null, "follow": null, "move_to": null, "bring_user": false, "lock_door": null, "give_key": null}'
    )
    system = (persona_sys or f"你正在扮演 {actor_name}。") + (memory_block or "") + room_guide
    if str(awareness or "").strip():
        system += "\n\n" + awareness.strip()

    history_lines = []
    for e in visible_logs:
        if e.get("kind") in ("sys", "move"):
            history_lines.append(f"* {e['text']}")
        else:
            if e.get("volume") == "whisper":
                vol_tag = f"[在「{e.get('pos') or '原处'}」轻语]"
            elif e.get("volume") == "shout":
                vol_tag = "[大声呼喊]"
            else:
                vol_tag = "[正常音量]"
            if e.get("from_room"):
                vol_tag = f"[从「{e.get('from_room')}」隔墙传来{vol_tag}]"
            acts = e.get("actions") or []
            act_txt = "；".join(
                f"[{ACTION_CHANNEL_LABELS.get(a.get('hand'), '手部')}{'·隐秘' if a.get('secret') else ''}→{a.get('target_name')}·{a.get('part')}] {a.get('text')}"
                for a in acts
            )
            body = f"{act_txt} {e.get('text', '')}".strip()
            history_lines.append(f"{e['sender_name']} {vol_tag}: {body}")

    hist_block = "\n".join(history_lines) if history_lines else "（房间里刚刚安静下来，还没有人说话。）"
    phone_block = (
        f"【最近手机聊天记录（沟通页上下文）】\n{phone_history.strip()}\n\n"
        if str(phone_history or "").strip() else ""
    )
    note_block = f"{extra_note.strip()}\n\n" if str(extra_note or "").strip() else ""
    if trigger_reason == "summon":
        prompt = (
            f"{note_block}"
            f"{phone_block}"
            f"【房间近期记录】\n{hist_block}\n\n"
            "有人刚才叫了你的名字。你现在不在对方所在的房间，也不是你随口说话的时候。"
            "请按人设与手上的事决定：愿意过去就用 move_to 填对方所在的房间 id 走过去（走过去后正常说话即可）；"
            "若门锁着、或你此刻不打算过去，就设 silent=true；也可以只在原地换个位置。不要假装听见了房间里的对话。"
        )
    elif trigger_reason == "auto":
        prompt = (
            f"{note_block}"
            f"{phone_block}"
            f"【房间近期记录】\n{hist_block}\n\n"
            "现在过了一小会儿。请根据当前上下文、位置与你的人设，自然决定要不要开口、换位置或移动；如果觉得没必要打扰可以设 silent=true。"
        )
    elif trigger_reason == "observe":
        prompt = (
            f"{note_block}"
            f"{phone_block}"
            f"【房间近期记录】\n{hist_block}\n\n"
            "你刚刚看到了房间里发生的新动作或状态变化。如果符合你的人设想给出反应就回应；如果没必要出声、只是默默看在眼里，请设 silent=true 避免话多。"
        )
    else:
        prompt = (
            f"{note_block}"
            f"{phone_block}"
            f"【房间近期记录】\n{hist_block}\n\n"
            "请按你的人设回应当前互动，并决定说话音量（normal / whisper / shout，大声呼喊会吵到隔壁）；若此刻选择沉默可设 silent=true。"
        )
    return system, [{"role": "user", "content": prompt}]


def _parse_actor_actions(raw, valid_ids: set[str], default_target: str = "user") -> list[dict]:
    """解析角色回合里的 actions（手部 / 目光动作），格式与用户在界面里排的动作完全一致。"""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[:3]:
        if not isinstance(item, dict):
            continue
        hand = str(item.get("hand") or "").strip().lower()
        part = str(item.get("part") or "").strip()[:30]
        if hand == "gaze" or part in ("gaze", "目光注视"):
            hand, part = "gaze", "目光注视"
        elif hand not in ("left", "right") or not part:
            continue
        target = str(item.get("target") or item.get("target_id") or default_target).strip()
        if valid_ids and target not in valid_ids:
            continue
        out.append({
            "hand": hand,
            "target_id": target,
            "part": part,
            "text": str(item.get("text") or "").strip()[:200],
            "secret": bool(item.get("secret", False)),
        })
    return out


def parse_actor_reply(reply_text: str, valid_ids: set[str], valid_rooms: set[str] = None,
                      default_target: str = "user") -> dict:
    obj = _extract_json(reply_text)
    if obj:
        vol = str(obj.get("volume") or "normal").strip().lower()
        if vol not in VOLUMES:
            vol = "normal"
        silent = bool(obj.get("silent", False))
        bind = obj.get("bind_hands")
        if bind not in ("left", "right", "both", "release"):
            bind = None
        hold_hand = obj.get("hold_hand")
        if hold_hand not in ("left", "right", "release"):
            hold_hand = None
        hold_target = str(obj.get("hold_target") or "").strip()
        if hold_target and valid_ids and hold_target not in valid_ids:
            hold_target = ""
        bind_target = str(obj.get("bind_target") or "").strip()
        if bind_target and valid_ids and bind_target not in valid_ids:
            bind_target = ""
        move_to = str(obj.get("move_to") or "").strip() or None
        if move_to and valid_rooms is not None and move_to not in valid_rooms:
            move_to = None
        lock_door = obj.get("lock_door")
        if not isinstance(lock_door, bool):
            lock_door = None
        give_key = obj.get("give_key")
        if not (isinstance(give_key, dict) and give_key.get("room_id") and give_key.get("to")):
            give_key = None
        position = str(obj.get("position") or "").strip()[:30] or None
        follow = obj.get("follow")
        if follow is not None:
            follow = str(follow).strip()
            if follow not in ("none", "user") and follow not in valid_ids:
                follow = None
        return {
            "silent": silent,
            "volume": vol,
            "action": str(obj.get("action") or "").strip()[:240],
            "speech": "" if silent else str(obj.get("speech") or "").strip()[:1200],
            "actions": _parse_actor_actions(obj.get("actions"), valid_ids, default_target),
            "position": position,
            "bind_hands": bind,
            "bind_target": bind_target or None,
            "hold_hand": hold_hand,
            "hold_target": hold_target or None,
            "follow": follow,
            "move_to": move_to,
            "bring_user": bool(obj.get("bring_user", False)),
            "lock_door": lock_door,
            "give_key": give_key,
        }
    return {
        "silent": False,
        "volume": "normal",
        "action": "",
        "speech": str(reply_text or "").strip()[:1200],
        "actions": [],
        "position": None,
        "bind_hands": None,
        "bind_target": None,
        "hold_hand": None,
        "hold_target": None,
        "follow": None,
        "move_to": None,
        "bring_user": False,
        "lock_door": None,
        "give_key": None,
    }


def auto_step_plan(store: RoomStore, room_id: str, busy_ids: set[str],
                   enabled: bool, prob_pct: int, rng=None,
                   include_other_rooms: bool = False,
                   blocked_ids: set[str] = None,
                   now_ms: int = None,
                   per_hour: int = None,
                   always_model: bool = False) -> tuple[str, str, str] | None:
    """一次自动触发该走哪条路：("model", 实体, 房间) 让模型接管，或 ("system", 实体, 房间) 系统打理生活。

    注意力在阈值之上且这一小时还有自主额度的人才让模型说话；否则由系统随机活动，不调 API。
    """
    if not enabled:
        return None
    r = rng or random
    p = max(0, min(100, int(prob_pct or 0))) / 100.0
    if p <= 0 or r.random() >= p:
        return None
    skip = set(busy_ids or ()) | set(blocked_ids or ())
    if include_other_rooms:
        candidates = [(aid, rid) for aid, rid in store.occupants.items() if aid != "user" and aid not in skip]
    else:
        candidates = [(aid, room_id) for aid in store.occupants_in(room_id) if aid != "user" and aid not in skip]
    if not candidates:
        return None
    if always_model:
        aid, rid = r.choice(candidates)
        return "model", aid, rid
    awake = [c for c in candidates
             if store.awake(c[0], now_ms) and store.auto_budget_left(c[0], now_ms, per_hour) > 0]
    if awake:
        aid, rid = r.choice(awake)
        return "model", aid, rid
    aid, rid = r.choice(candidates)
    return "system", aid, rid


def summon_targets(store: RoomStore, text: str, name_map: dict[str, str]) -> list[str]:
    """在用户的话里找出被叫到名字的实体（按出现先后排序）：标题、标题里的别名、文件名主干都算。"""
    raw = str(text or "")
    if not raw:
        return []
    low = raw.casefold()
    found = []
    for aid in store.occupants:
        if aid == "user":
            continue
        cands = []
        title = str(name_map.get(aid) or "").strip()
        if title:
            cands.extend([title, title.split()[0]])
            cands.extend(part.strip(" \t（）()【】[]") for part in re.split(r"[（(·/／]| - ", title))
        stem = os.path.splitext(aid)[0]
        if stem:
            cands.append(stem)
        pos = 0
        for cand in cands:
            clean = str(cand or "").strip()
            if len(clean) < 2:
                continue
            idx = low.find(clean.casefold())
            if idx >= 0 and (not pos or idx + 1 < pos):
                pos = idx + 1
        if pos:
            found.append((pos, aid))
    found.sort()
    return [aid for _pos, aid in found]


def phone_room_status_note(store: RoomStore, actor_id: str, actor_name: str,
                           all_names: dict[str, str], recent_limit: int = 10) -> str:
    """生成注入原手机沟通页的小屋状态与近期互动记录，实现小屋与手机沟通双向上下文互通。"""
    if not actor_id or actor_id not in store.occupants:
        return ""
    my_rid = store.occupants.get(actor_id, "")
    my_room = store.rooms.get(my_rid)
    u_rid = store.current_room_id()
    u_room = store.rooms.get(u_rid)
    if not my_room or not u_room:
        return ""
    rooms_brief = []
    for r in store.room_list():
        ok, _ = store.can_enter(actor_id, r["id"])
        tag = "公共" if r.get("is_public") else ("有钥匙" if store.has_key(actor_id, r["id"]) else "无钥匙")
        if r.get("locked_inside"):
            tag += "·已反锁"
        if r["id"] not in store.neighbors(my_rid) and r["id"] != my_rid:
            tag += "·不相连"
        rooms_brief.append(f"{r['name']}(id={r['id']},{tag},{'可进' if ok else '不可进'})")

    recent_lines = []
    for e in store.recent_logs_for_actor(actor_id, limit=recent_limit):
        rname = (store.rooms.get(e.get("room_id")) or {}).get("name") or e.get("room_id") or "小屋"
        ts_ms = int(e.get("ts") or 0)
        t_tag = time.strftime("%m-%d %H:%M", time.localtime(ts_ms / 1000.0)) if ts_ms else ""
        prefix = f"[{t_tag} {rname}]" if t_tag else f"[{rname}]"
        if e.get("kind") in ("sys", "move"):
            txt = str(e.get("text") or "").strip()[:140]
            if txt:
                recent_lines.append(f"- {prefix} * {txt}")
        else:
            acts = e.get("actions") or []
            act_txt = "；".join(
                f"[{ACTION_CHANNEL_LABELS.get(a.get('hand'), '手部')}{'·隐秘' if a.get('secret') else ''}→{a.get('target_name')}·{a.get('part')}] {a.get('text')}"
                for a in acts
            )
            body = f"{act_txt} {e.get('text', '')}".strip()[:220]
            if body:
                if e.get("volume") == "whisper":
                    vol_s = "（轻语）"
                elif e.get("volume") == "shout":
                    vol_s = "（大声呼喊）"
                else:
                    vol_s = ""
                recent_lines.append(f"- {prefix} {e.get('sender_name') or e.get('sender')}{vol_s}: {body}")

    recent_block = (
        "\n近期小屋互动记录（你听见或亲历的部分，与当前手机聊天属于同一时间线）：\n" + "\n".join(recent_lines)
        if recent_lines else ""
    )
    # 手部状态只在有人被牵着/束缚（即不是两只手都自由）时才写，避免提示词凭空变长
    hands_bits = []
    for aid in (actor_id, "user"):
        if all(store.hand_state(aid, s) in ("free", "used") for s in ("left", "right")):
            continue
        who = "你" if aid == actor_id else "用户"
        hands_bits.append(f"{who}双手：{store.hands_brief(aid)}")
    hands_block = ("　".join(hands_bits) + "\n") if hands_bits else ""
    return (
        "[OIT 小屋状态（手机聊天联动）]\n"
        f"你当前在小屋的「{my_room['name']}」(id={my_rid}，位置：{store.position_of(actor_id)}，"
        f"门锁：{'已从内部反锁' if my_room.get('locked_inside') else '未反锁'})；"
        f"用户当前在「{u_room['name']}」(id={u_rid}，位置：{store.position_of('user')})。\n"
        f"{hands_block}"
        f"小屋房间：{'；'.join(rooms_brief)}。"
        f"{recent_block}\n"
        "你此刻正通过手机与用户聊天；如果需要同时在小屋中移动房间、换室内位置、反锁/解锁房门、把你持有钥匙的房间钥匙分给用户、跟随用户、"
        "牵手或放开（hold_hand）、束缚用户的双手（bind_hands），或对同房间同位置的成员做和用户一样的手部 / 目光动作（actions），可调用 oit_room_action 工具。"
    )


def execute_room_tool(store: RoomStore, actor_id: str, actor_name: str,
                      args: dict, all_names: dict[str, str],
                      msg_count_of=None) -> dict:
    """在手机沟通页执行 oit_room_action 工具，安全校验门锁、钥匙、牵手与手部动作。"""
    if not actor_id:
        return {"ok": False, "error": "当前未选择人设"}
    args = args if isinstance(args, dict) else {}
    results = []
    cur_rid = store.occupants.get(actor_id) or persona_room_id(actor_id)
    count_of = msg_count_of if callable(msg_count_of) else store.pair_msg_count

    move_to = str(args.get("move_to") or "").strip()
    if move_to and move_to != cur_rid:
        err = store.walk_to(actor_id, move_to, actor_name, name_map=all_names)
        if err:
            results.append(f"移动失败：{err}")
        else:
            cur_rid = store.occupants.get(actor_id, cur_rid)
            results.append(f"已进入「{store.rooms[cur_rid]['name']}」")

    pos = str(args.get("position") or "").strip()[:30]
    if pos:
        if store.move_position(actor_id, pos, actor_name):
            results.append(f"已移动到室内位置「{pos}」")

    fol = str(args.get("follow") or "").strip()
    if fol == "none":
        store.set_following(actor_id, None, actor_name)
        results.append("已取消跟随")
    elif fol == "user":
        store.set_following(actor_id, "user", actor_name, all_names.get("user", "你"))
        results.append("已开始跟随用户")

    hold = str(args.get("hold_hand") or "").strip()
    if hold == "release":
        released = []
        for side in ("left", "right"):
            if store.hand_state(actor_id, side) in ("holding", "held"):
                store.release_hand(actor_id, side, all_names, actor_name)
                released.append("左手" if side == "left" else "右手")
        results.append("已松开" + "、".join(released) if released else "当前没有牵手")
    elif hold in ("left", "right"):
        target = str(args.get("hold_target") or "user").strip() or "user"
        err = store.hold_hand(actor_id, hold, target, actor_name,
                              all_names.get(target, target), msg_count=count_of(actor_id, target))
        if err:
            results.append(f"牵手失败：{err}")
        else:
            results.append(f"已用{'左手' if hold == 'left' else '右手'}牵住 {all_names.get(target, target)} 的手")

    bind = str(args.get("bind_hands") or "").strip()
    if bind in ("left", "right", "both", "release"):
        target = str(args.get("bind_target") or "user").strip() or "user"
        if bind == "release":
            store.set_hand_bound(target, "both", False)
            results.append(f"已放开 {all_names.get(target, target)} 的双手")
        else:
            store.set_hand_bound(target, bind, True, actor_name)
            results.append(f"已束缚 {all_names.get(target, target)} 的双手")

    acts = _parse_actor_actions(args.get("actions"), set(all_names.keys()))
    if acts:
        for a in acts:
            a["target_name"] = all_names.get(a["target_id"], a["target_id"])
            err = store.queue_hand_action(actor_id, a["hand"], a["target_id"], a["target_name"],
                                          a["part"], a["text"], msg_count=count_of(actor_id, a["target_id"]),
                                          secret=a["secret"])
            if err:
                results.append(f"动作未执行：{err}")
        done = store.take_pending(actor_id, actor_name)
        if done:
            room_id = store.occupants.get(actor_id) or cur_rid
            store.append_log(room_id, actor_id, actor_name, ["all"], "action", "", actions=done,
                             pos=store.position_of(actor_id))
            results.append(f"已做出 {len(done)} 个手部 / 目光动作")

    gk_room = str(args.get("give_key_room") or "").strip()
    if gk_room:
        to_id = str(args.get("give_key_to") or "user").strip() or "user"
        err = store.grant_key(gk_room, actor_id, to_id, actor_name, all_names.get(to_id, to_id))
        if err:
            results.append(f"分发钥匙失败：{err}")
        else:
            results.append(f"已把房间 {gk_room} 的钥匙分给 {all_names.get(to_id, to_id)}")

    lock_val = args.get("lock_door")
    if isinstance(lock_val, bool):
        err = store.set_room_lock(cur_rid, actor_id, lock_val, actor_name)
        if err:
            results.append(f"门锁操作失败：{err}")
        else:
            results.append("已从内部反锁房门" if lock_val else "已解除房门反锁")

    room = store.rooms.get(cur_rid) or {}
    return {
        "ok": True,
        "results": results or ["未执行变更"],
        "current_room": room.get("name", cur_rid),
        "current_room_id": cur_rid,
        "position": store.position_of(actor_id),
        "hands": store.hands_brief(actor_id),
        "user_hands": store.hands_brief("user"),
        "locked_inside": bool(room.get("locked_inside", False)),
    }
