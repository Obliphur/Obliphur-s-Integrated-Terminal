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
    ("wrist", "手臂 / 手腕", 50),
    ("head", "头顶 / 发丝", 50),
    ("cheek", "脸颊", 120),
    ("ear", "耳畔", 120),
    ("waist", "后背 / 腰侧", 200),
    ("hug", "拥抱 / 拉近", 200),
)


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
    if any(k in low for k in ("墙", "椅", "沙发", "窗", "茶几", "床", "wall", "chair", "sofa")):
        return "position"
    return "custom"


def normalize_room(raw: dict) -> dict:
    """校验并补全房间对象（含等宽对齐、房主、公共属性、钥匙列表与内部反锁状态）。"""
    rid = str(raw.get("id") or "").strip() or f"room_{uuid.uuid4().hex[:8]}"
    name = str(raw.get("name") or "未命名房间").strip()[:40]
    theme = str(raw.get("theme") or "自定义场景").strip()[:60]
    ambience = str(raw.get("ambience") or "").strip()[:240]
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
        "builtin": bool(raw.get("builtin", False)),
        "owner": owner,
        "is_public": is_public,
        "keys": keys,
        "locked_inside": bool(raw.get("locked_inside", False)),
        "locked_by": str(raw.get("locked_by") or "").strip(),
        "locked_reason": str(raw.get("locked_reason") or "").strip(),
        "ascii_map": clean_lines,
        "facilities": list(fac_by_token.values()),
    }


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


class RoomStore:
    """管理分人设房间、钥匙分发、内部反锁、牵手/跟随带人、室内位置、双手槽位与定向听觉日志。"""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, ROOM_FILE)
        self.rooms: dict[str, dict] = {}
        self.occupants: dict[str, str] = {}
        self.positions: dict[str, str] = {}         # actor_id -> 当前室内位置（如 "墙边" / "椅子" / "房间中央"）
        self.following: dict[str, str] = {}         # actor_id -> leader_id（言语同意跟随）
        self.holding_hands: dict[str, str] = {"left": "", "right": ""}  # side -> actor_id（直接牵手）
        self.room_counts: dict[str, int] = {}
        self.last_user_interact: dict[str, int] = {}
        self.activities: dict[str, dict] = {}
        self.hands: dict[str, dict] = {
            "left": {"state": "free", "note": ""},
            "right": {"state": "free", "note": ""},
        }
        self.pending_actions: list[dict] = []
        self.logs: list[dict] = []
        self.load()

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

    def sync_personas(self, personas: list[tuple[str, str]]) -> bool:
        """为每个人设（含新建人设）确保专属房间存在，并默认让未就位的人设待在自己的房间里。"""
        changed = False
        for pid, title in personas:
            if not pid:
                continue
            rid = persona_room_id(pid)
            if rid not in self.rooms:
                self.rooms[rid] = normalize_room(make_persona_room(pid, title))
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
        self.holding_hands = {"left": "", "right": ""}
        self.room_counts = {}
        self.last_user_interact = {}
        self.activities = {}
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
                    hh = data.get("holding_hands") or {}
                    if isinstance(hh, dict):
                        for s in ("left", "right"):
                            self.holding_hands[s] = str(hh.get(s) or "")
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
                    hands = data.get("hands") or {}
                    if isinstance(hands, dict):
                        for side in ("left", "right"):
                            h = hands.get(side)
                            if isinstance(h, dict):
                                st = h.get("state")
                                if st not in ("free", "used", "bound", "holding"):
                                    st = "free"
                                self.hands[side] = {"state": st, "note": str(h.get("note") or "")[:60]}
                    logs = data.get("logs") or []
                    if isinstance(logs, list):
                        self.logs = [x for x in logs[-MAX_ROOM_LOGS:] if isinstance(x, dict)]
            except (OSError, json.JSONDecodeError, ValueError):
                pass
        self._ensure_builtins()
        self.pending_actions = []
        self.reset_turn_hands(save=False)

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        payload = {
            "rooms": self.rooms,
            "occupants": self.occupants,
            "positions": self.positions,
            "following": self.following,
            "holding_hands": self.holding_hands,
            "room_counts": self.room_counts,
            "last_user_interact": self.last_user_interact,
            "activities": self.activities,
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
        """按音量与室内位置计算听众：normal=全屋可听(["all"])；whisper=仅同一位置的在场成员可听。"""
        if str(volume or "normal").lower() != "whisper":
            return ["all"]
        pos = self.position_of(sender_id)
        same_pos = [aid for aid in self.occupants_in(room_id) if self.position_of(aid) == pos]
        return same_pos or [sender_id]

    # ---- 牵手与跟随（带角色出去 / 被带出去） ----

    def hold_hand(self, side: str, actor_id: str, actor_name: str = "", msg_count: int | None = None) -> str:
        if side not in ("left", "right"):
            return "请指定左手或右手"
        if msg_count is not None and int(msg_count) < HOLD_HAND_MIN_MSGS:
            cur = int(msg_count)
            return tr("牵手带走需要与对方累计 {req} 条消息（当前 {cur} 条，还差 {need} 条）。",
                      req=HOLD_HAND_MIN_MSGS, cur=cur, need=HOLD_HAND_MIN_MSGS - cur)
        if self.hands[side]["state"] == "bound":
            return "这只手正被束缚着，无法牵手"
        if self.occupants.get("user") != self.occupants.get(actor_id):
            return "对方不在你当前所在的房间"
        self.holding_hands[side] = actor_id
        self.hands[side] = {"state": "holding", "note": f"握住 {actor_name or actor_id}"[:24]}
        rid = self.current_room_id()
        self.append_log(rid, "sys", "系统", ["all"], "sys",
                        tr("你用{hand}握住了 {who} 的手。",
                           hand="左手" if side == "left" else "右手", who=actor_name or actor_id))
        self.save()
        return ""

    def release_hand(self, side: str, name_map: dict[str, str] = None):
        if side not in ("left", "right"):
            return
        aid = self.holding_hands.get(side) or ""
        self.holding_hands[side] = ""
        if self.hands[side]["state"] == "holding":
            self.hands[side] = {"state": "free", "note": ""}
        if aid:
            who = (name_map or {}).get(aid, aid)
            rid = self.current_room_id()
            self.append_log(rid, "sys", "系统", ["all"], "sys",
                            tr("你松开了牵着 {who} 的手。", who=who))
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
        if leader_id == "user":
            for s in ("left", "right"):
                aid = self.holding_hands.get(s)
                if aid and aid in occ and aid not in out:
                    out.append(aid)
        else:
            # 如果用户牵着 leader 的手，leader 移动时也能把用户带走
            if leader_id in self.holding_hands.values() and "user" in occ:
                out.append("user")
        for aid, lid in self.following.items():
            if lid == leader_id and aid in occ and aid != leader_id and aid not in out:
                out.append(aid)
        return out

    # ---- 房间、钥匙与内部反锁 ----

    def current_room_id(self) -> str:
        rid = self.occupants.get("user", "my_home")
        return rid if rid in self.rooms else "my_home"

    def current_room(self) -> dict:
        return self.rooms[self.current_room_id()]

    def room_list(self) -> list[dict]:
        return list(self.rooms.values())

    def add_room(self, raw: dict, creator_id: str = "user", is_public: bool = False) -> dict:
        data = dict(raw)
        data["owner"] = "" if is_public else creator_id
        data["is_public"] = bool(is_public)
        data["keys"] = [] if is_public else ([creator_id] if creator_id else [])
        data["locked_inside"] = False
        data["locked_by"] = ""
        data["locked_reason"] = ""
        room = normalize_room(data)
        self.rooms[room["id"]] = room
        self.save()
        return room

    def delete_room(self, room_id: str) -> bool:
        room = self.rooms.get(room_id)
        if not room or room.get("builtin"):
            return False
        del self.rooms[room_id]
        for aid, rid in list(self.occupants.items()):
            if rid == room_id:
                self.occupants[aid] = "my_home" if aid == "user" else persona_room_id(aid)
                if self.occupants[aid] not in self.rooms:
                    self.occupants[aid] = "lounge"
        self.save()
        return True

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
        if self.occupants.get(actor_id) == room_id:
            return True, ""
        if room.get("locked_inside"):
            return False, tr("「{room}」的门从里面反锁了，即使有钥匙也打不开。", room=room["name"])
        if room.get("is_public"):
            return True, ""
        if self.has_key(actor_id, room_id):
            return True, ""
        return False, tr("你没有「{room}」的钥匙，无法直接进入。", room=room["name"])

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

    def set_room_lock(self, room_id: str, actor_id: str, locked: bool, actor_name: str = "",
                      reason: str = "manual") -> str:
        room = self.rooms.get(room_id)
        if not room:
            return "房间不存在"
        if self.occupants.get(actor_id) != room_id:
            return "只有在房间内部才能反锁或打开门锁"
        if bool(room.get("locked_inside")) == bool(locked):
            if locked and reason:
                room["locked_reason"] = reason
            return ""
        room["locked_inside"] = bool(locked)
        room["locked_by"] = actor_id if locked else ""
        room["locked_reason"] = reason if locked else ""
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

        nm = name_map or {}
        who = actor_name or nm.get(actor_id) or ("你" if actor_id == "user" else actor_id)
        comp_names = "、".join(nm.get(c, "你" if c == "user" else c) for c in companions)

        if prev and prev in self.rooms:
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
                for s in ("left", "right"):
                    if self.holding_hands.get(s) == aid:
                        self.holding_hands[s] = ""
                        if self.hands[s]["state"] == "holding":
                            self.hands[s] = {"state": "free", "note": ""}
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

    def is_recently_interacted(self, actor_id: str, idle_minutes: int, now_ms: int = None) -> bool:
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        last = int(self.last_user_interact.get(actor_id) or 0)
        if not last:
            return False
        return (now - last) < max(1, int(idle_minutes or 15)) * 60 * 1000

    def activity_label(self, actor_id: str, now_ms: int = None, schedule_kind: str = "") -> str:
        if schedule_kind == "focus":
            return "专注中"
        if schedule_kind == "sleep":
            return "睡眠中"
        if self.following.get(actor_id) == "user":
            return "跟随中"
        if actor_id in self.holding_hands.values():
            return "牵手中"
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        act = self.activities.get(actor_id)
        if act and int(act.get("until") or 0) > now and act.get("label"):
            return f"{act['label']}中"
        return ""

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

        enterable = [
            rid for rid in self.rooms
            if rid != cur_rid and self.can_enter(actor_id, rid)[0]
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
        now = int(time.time() * 1000)
        occ = self.occupants_in(room_id)
        if sender == "user":
            heard = [aid for aid in occ if aid != "user" and ("all" in targets or aid in targets)]
            for aid in heard:
                self.room_counts[aid] = self.room_counts.get(aid, 0) + 1
                self.last_user_interact[aid] = now
                self.activities.pop(aid, None)
        elif "all" in targets or "user" in targets:
            self.room_counts[sender] = self.room_counts.get(sender, 0) + 1
            self.last_user_interact[sender] = now

    # ---- 双手槽位、独立目光与部位互动 ----

    def available_hands(self) -> list[str]:
        """自由或握住状态的手都可脱离当前动作作出新动作，仅被束缚时不可用。"""
        return [side for side in ("left", "right") if self.hands[side]["state"] != "bound"]

    def hand_label(self, side: str) -> str:
        name = "左手" if side == "left" else "右手"
        st = self.hands[side]["state"]
        note = self.hands[side].get("note") or ""
        if st == "bound":
            return f"{name}: 被束缚" + (f"({note})" if note else "")
        if st == "holding":
            return f"{name}: {note or '握住'}"
        return f"{name}: 自由"

    def queue_hand_action(self, hand: str, target_id: str, target_name: str, part: str, text: str,
                          msg_count: int | None = None, secret: bool = False) -> str:
        clean_part = str(part or "").strip()[:30]
        is_gaze = (clean_part in ("gaze", "目光注视") or hand == "gaze")
        channel = "gaze" if is_gaze else hand
        if channel not in ("left", "right", "gaze"):
            return "请指定左手或右手"
        if channel in ("left", "right"):
            if self.hands[channel]["state"] == "bound":
                return "这只手正被束缚着，暂时无法行动"
        if msg_count is not None and clean_part != "设施":
            ok, req = part_allowed(clean_part, msg_count)
            if not ok:
                return tr("「{part}」需要与对方累计 {req} 条消息才允许接触（当前 {cur} 条，还差 {need} 条）。",
                          part=clean_part, req=req, cur=msg_count, need=req - msg_count)
        clean_text = str(text or "").strip()[:300]
        if not clean_text:
            return "请输入互动内容"

        # 自由或握住状态的手安排新动作时自动脱离原握持；左手、右手、目光各自独立且可同时共存
        if channel == "gaze":
            self.pending_actions = [a for a in self.pending_actions if a.get("hand") != "gaze"]
        else:
            if self.hands[channel]["state"] == "holding":
                self.holding_hands[channel] = ""
                self.hands[channel] = {"state": "free", "note": ""}
            self.pending_actions = [a for a in self.pending_actions if a.get("hand") != channel]

        self.pending_actions.append({
            "hand": channel,
            "target_id": target_id,
            "target_name": target_name,
            "part": clean_part,
            "text": clean_text,
            "secret": bool(secret),
        })
        return ""

    def remove_pending_action(self, index: int):
        if 0 <= index < len(self.pending_actions):
            self.pending_actions.pop(index)

    def set_hand_bound(self, side: str, bound: bool, note: str = ""):
        sides = ("left", "right") if side == "both" else ((side,) if side in ("left", "right") else ())
        for s in sides:
            if bound:
                self.holding_hands[s] = ""
                self.hands[s] = {"state": "bound", "note": str(note or "")[:40]}
                self.pending_actions = [a for a in self.pending_actions if a.get("hand") not in (s, "both")]
            elif self.hands[s]["state"] == "bound":
                self.hands[s] = {"state": "free", "note": ""}
        self.save()

    def reset_turn_hands(self, save: bool = True, advance_turn: bool = False):
        self.pending_actions = []
        for side in ("left", "right"):
            if self.hands[side]["state"] not in ("free", "holding", "bound"):
                self.hands[side] = {"state": "free", "note": ""}
        if save:
            self.save()

    # ---- 定向听觉隔离日志 ----

    def append_log(self, room_id: str, sender: str, sender_name: str, targets: list[str],
                   kind: str, text: str, actions: list[dict] = None,
                   volume: str = "normal", pos: str = "") -> dict:
        clean_targets = [str(t) for t in (targets or ["all"]) if str(t)]
        if not clean_targets:
            clean_targets = ["all"]
        vol = "whisper" if str(volume or "").lower() == "whisper" else "normal"
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
        self.logs.append(entry)
        self._bump_room_counts(room_id, sender, clean_targets, kind)
        if len(self.logs) > MAX_ROOM_LOGS:
            self.logs = self.logs[-MAX_ROOM_LOGS:]
        self.save()
        return entry

    def can_hear(self, entry: dict, actor_id: str) -> bool:
        if entry.get("kind") in ("sys", "move"):
            return True
        if entry.get("sender") == actor_id:
            return True
        targets = entry.get("targets") or []
        return "all" in targets or actor_id in targets

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
        """返回房间内该角色能听见或看见的记录（非隐秘动作全房可见，隐秘接触仅双方可见，轻语仅同位置可听）。"""
        out = []
        for e in self.logs:
            if e.get("room_id") != room_id:
                continue
            if e.get("kind") in ("sys", "move"):
                out.append(e)
                continue
            can_hear_speech = self.can_hear(e, actor_id)
            vis_acts = self.visible_actions_for(e, actor_id)
            if not can_hear_speech and not vis_acts:
                continue
            item = dict(e)
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
        for e in [x for x in self.logs if x.get("room_id") == room_id][-limit:]:
            if self.can_hear(e, "user"):
                item = dict(e)
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


def build_map_generation_prompt(theme: str, desc: str) -> tuple[str, list[dict]]:
    system = (
        "你是古早文字冒险游戏的场景地图设计师。请根据用户协商的主题与场景描述，生成一个字符画房间地图。\n"
        "严格只输出一个 JSON 对象，不要附加任何解释或多余文字，字段如下：\n"
        "{\n"
        '  "name": "房间名称(不超过12字)",\n'
        '  "theme": "场景主题",\n'
        '  "ambience": "一句话氛围描写(不超过60字)",\n'
        '  "ascii_map": ["第一行", "第二行", ...],\n'
        '  "facilities": [{"token": "[设施名]", "label": "设施名", "action": "piano|bookshelf|desk|board|door|position|custom", "desc": "简短说明"}]\n'
        "}\n"
        "要求：\n"
        "1. ascii_map 使用 Unicode 框线字符（┌ ─ ┐ │ └ ┘）绘制一个 8~12 行整齐的房间，内部尽量包含 [椅子] 和 [墙边] 等位置节点。\n"
        "2. 所有可点击设施与位置必须用半角方括号包裹，如 [钢琴]、[书架]、[书桌]、[椅子]、[墙边]、[门]。\n"
        "3. 地图底部必须包含 [门]（action 为 door），方便玩家进出房间。\n"
        "4. action 映射：钢琴/乐器用 piano，书架用 bookshelf，书桌用 desk，布告板用 board，门用 door，椅子/墙边/沙发/窗台等位置用 position，其他用 custom。"
    )
    user_msg = f"主题：{theme.strip()}\n场景设定与细节：{desc.strip() or '按主题自由发挥，富有复古文字游戏质感。'}"
    return system, [{"role": "user", "content": user_msg}]


def parse_generated_room(reply_text: str, fallback_theme: str = "自定义场景") -> dict | None:
    obj = _extract_json(reply_text)
    if not obj or not obj.get("ascii_map"):
        return None
    obj = dict(obj)
    obj["builtin"] = False
    obj.setdefault("theme", fallback_theme)
    obj["id"] = f"custom_{uuid.uuid4().hex[:8]}"
    return normalize_room(obj)


def build_actor_turn_prompt(persona_sys: str, store: RoomStore, room: dict, actor_id: str, actor_name: str,
                            all_names: dict[str, str], visible_logs: list[dict],
                            trigger_reason: str = "user",
                            memory_block: str = "", phone_history: str = "") -> tuple[str, list[dict]]:
    occ_ids = store.occupants_in(room["id"])
    occ_desc = "、".join(
        f"{all_names.get(aid, aid)}(id={aid}, 位置={store.position_of(aid)})"
        for aid in occ_ids
    )
    hand_desc = f"用户左手={store.hand_label('left')}, 用户右手={store.hand_label('right')}"
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
        f"用户双手状态（同一房间内公开可见，包括牵手与小动作）：{hand_desc}\n"
        f"房间字符画地图：\n{map_text}\n\n"
        "【世界房间列表】\n" + "\n".join(world_lines) + "\n\n"
        "【规则】\n"
        "1. 原来的沟通页面代表手机聊天（可以随时私发给任何人）；而这里是面对面的现实房间空间，无法随意屏蔽某人，只能通过音量控制：\n"
        "   - volume=\"normal\"（正常音量）：房间里所有人都能听见。\n"
        "   - volume=\"whisper\"（轻语）：只有和你处于【同一室内位置】的人能听见，其他位置的人听不清。\n"
        "2. 同一房间内的公开动作（牵手、移动位置、小动作等）大家都看得到；标注了[隐秘]的身体接触只有双方知道，旁人看不见。\n"
        "3. 为避免显得话多，如果你只是在旁边看着、或者此刻不需要插话，请把 silent 设为 true（此时 speech 留空，不发消息打扰）。\n"
        "4. 你可以结合当前位置（如墙边、椅子、沙发、钢琴旁）做位置相关互动，并用 position 字段更新你的室内位置。\n"
        "5. 如果用户言语请求你跟随同行，同意可将 follow 设为 \"user\"（取消跟随设为 \"none\"，不变为 null）。\n"
        "6. 你可以自行移动去公共房间或有钥匙且未反锁的房间（move_to 填房间 id）；若想把用户一起带去那个房间，可将 bring_user 设为 true。\n"
        "7. 若不想被打扰可将 lock_door 设为 true 从内部反锁（false 解锁，null 不变）；分钥匙可填 give_key: {\"room_id\": \"...\", \"to\": \"user\"}。\n"
        "8. 请严格只输出一个 JSON 对象，保持你原本的人设语气：\n"
        '{"silent": false, "volume": "normal", "action": "简短动作描写(可为空)", "speech": "你的台词(可为空)", '
        '"position": null, "bind_hands": null, "follow": null, "move_to": null, "bring_user": false, "lock_door": null, "give_key": null}'
    )
    system = (persona_sys or f"你正在扮演 {actor_name}。") + (memory_block or "") + room_guide

    history_lines = []
    for e in visible_logs:
        if e.get("kind") in ("sys", "move"):
            history_lines.append(f"* {e['text']}")
        else:
            vol_tag = f"[在「{e.get('pos') or '原处'}」轻语]" if e.get("volume") == "whisper" else "[正常音量]"
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
    if trigger_reason == "auto":
        prompt = (
            f"{phone_block}"
            f"【房间近期记录】\n{hist_block}\n\n"
            "现在过了一小会儿。请根据当前上下文、位置与你的人设，自然决定要不要开口、换位置或移动；如果觉得没必要打扰可以设 silent=true。"
        )
    elif trigger_reason == "observe":
        prompt = (
            f"{phone_block}"
            f"【房间近期记录】\n{hist_block}\n\n"
            "你刚刚看到了房间里发生的新动作或状态变化。如果符合你的人设想给出反应就回应；如果没必要出声、只是默默看在眼里，请设 silent=true 避免话多。"
        )
    else:
        prompt = (
            f"{phone_block}"
            f"【房间近期记录】\n{hist_block}\n\n"
            "请按你的人设回应当前互动，并决定说话音量（normal 或 whisper）；若此刻选择沉默可设 silent=true。"
        )
    return system, [{"role": "user", "content": prompt}]


def parse_actor_reply(reply_text: str, valid_ids: set[str], valid_rooms: set[str] = None,
                      default_target: str = "user") -> dict:
    obj = _extract_json(reply_text)
    if obj:
        vol = str(obj.get("volume") or "normal").strip().lower()
        if vol not in ("normal", "whisper"):
            vol = "normal"
        silent = bool(obj.get("silent", False))
        bind = obj.get("bind_hands")
        if bind not in ("left", "right", "both", "release"):
            bind = None
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
            "position": position,
            "bind_hands": bind,
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
        "position": None,
        "bind_hands": None,
        "follow": None,
        "move_to": None,
        "bring_user": False,
        "lock_door": None,
        "give_key": None,
    }


def pick_auto_speaker(store: RoomStore, room_id: str, busy_ids: set[str],
                      enabled: bool, prob_pct: int, rng=None,
                      include_other_rooms: bool = False,
                      blocked_ids: set[str] = None) -> tuple[str, str] | None:
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
    return r.choice(candidates)


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
                vol_s = "（轻语）" if e.get("volume") == "whisper" else ""
                recent_lines.append(f"- {prefix} {e.get('sender_name') or e.get('sender')}{vol_s}: {body}")

    recent_block = (
        "\n近期小屋互动记录（你听见或亲历的部分，与当前手机聊天属于同一时间线）：\n" + "\n".join(recent_lines)
        if recent_lines else ""
    )
    return (
        "[OIT 小屋状态（手机聊天联动）]\n"
        f"你当前在小屋的「{my_room['name']}」(id={my_rid}，位置：{store.position_of(actor_id)}，"
        f"门锁：{'已从内部反锁' if my_room.get('locked_inside') else '未反锁'})；"
        f"用户当前在「{u_room['name']}」(id={u_rid}，位置：{store.position_of('user')})。\n"
        f"小屋房间：{'；'.join(rooms_brief)}。"
        f"{recent_block}\n"
        "你此刻正通过手机与用户聊天；如果需要同时在小屋中移动房间、换室内位置、反锁/解锁房门、把你持有钥匙的房间钥匙分给用户或跟随用户，可调用 oit_room_action 工具。"
    )


def execute_room_tool(store: RoomStore, actor_id: str, actor_name: str,
                      args: dict, all_names: dict[str, str]) -> dict:
    """在手机沟通页执行 oit_room_action 工具，安全校验门锁与钥匙规则。"""
    if not actor_id:
        return {"ok": False, "error": "当前未选择人设"}
    args = args if isinstance(args, dict) else {}
    results = []
    cur_rid = store.occupants.get(actor_id) or persona_room_id(actor_id)

    move_to = str(args.get("move_to") or "").strip()
    if move_to and move_to != cur_rid:
        err = store.enter_room(actor_id, move_to, actor_name, name_map=all_names)
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
        "locked_inside": bool(room.get("locked_inside", False)),
    }
