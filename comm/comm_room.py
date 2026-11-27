# -*- coding: utf-8 -*-
"""古早文字游戏房间系统（PC 优先）：分人设专属房间、钥匙与内部反锁、按条数解锁部位、定向听觉隔离与 NPC 自主移动/对话。"""

import copy
import json
import os
import random
import re
import time
import uuid

from i18n import tr

ROOM_FILE = "comm_rooms.json"
MAX_ROOM_LOGS = 500
CUSTOM_PART_MIN_MSGS = 200

FACILITY_ACTIONS = {
    "piano": "音乐演奏 / 曲库",
    "bookshelf": "打开共读",
    "desk": "专注计时 / 联合行动",
    "board": "合约 / 知识库",
    "door": "切换房间 / 离开",
    "custom": "设施互动",
}

# (part_id, display_label, min_messages)
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


def part_required_msgs(part_key_or_label: str) -> int:
    s = str(part_key_or_label or "").strip()
    for pid, label, req in BODY_PARTS:
        if s == pid or s == label:
            return req
    return CUSTOM_PART_MIN_MSGS


def part_allowed(part_key_or_label: str, msg_count: int) -> tuple[bool, int]:
    req = part_required_msgs(part_key_or_label)
    return int(msg_count or 0) >= req, req


def persona_room_id(persona_name: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff-]+", "_", str(persona_name or "").strip()).strip("_")
    return f"home_{safe or 'persona'}"


def make_persona_room(persona_name: str, display_name: str) -> dict:
    """为指定人设生成默认专属房间，默认归该人设所有且持有钥匙。"""
    title = str(display_name or persona_name).strip() or persona_name
    return {
        "id": persona_room_id(persona_name),
        "name": f"{title} 的房间",
        "theme": f"{title} 的专属居室",
        "ambience": f"这里是 {title} 的私人空间，窗边透进柔和的光，书架与乐器井然有序。",
        "builtin": True,
        "owner": persona_name,
        "is_public": False,
        "keys": [persona_name],
        "locked_inside": False,
        "locked_by": "",
        "ascii_map": [
            "┌──────────────[窗台]──────────────┐",
            "│                                  │",
            "│  [钢琴]                [书架]    │",
            "│  ║▌▌▌║                 │≡≡≡≡│    │",
            "│                                  │",
            "│  [茶几]                [书桌]    │",
            "│  ( ♨ )                 ┌────┐    │",
            "│                                  │",
            "└───────────────[门]───────────────┘",
        ],
        "facilities": [
            {"token": "[窗台]", "label": "窗台", "action": "custom", "desc": "靠在窗台边吹风、看风景。"},
            {"token": "[钢琴]", "label": "钢琴", "action": "piano", "desc": "打开音乐演奏评判或曲库。"},
            {"token": "[书架]", "label": "书架", "action": "bookshelf", "desc": "抽出一本书开启共读。"},
            {"token": "[茶几]", "label": "茶几", "action": "custom", "desc": "倒一杯热茶或坐下小憩。"},
            {"token": "[书桌]", "label": "书桌", "action": "desk", "desc": "进入专注计时或发起联合行动。"},
            {"token": "[门]", "label": "门", "action": "door", "desc": "推门走出房间，前往其他房间。"},
        ],
    }


BUILTIN_ROOMS = {
    "my_home": {
        "id": "my_home",
        "name": "我的房间",
        "theme": "日常居室",
        "ambience": "台灯暖黄，窗外夜色安静，桌上的计时器微微亮着。",
        "builtin": True,
        "owner": "user",
        "is_public": False,
        "keys": ["user"],
        "locked_inside": False,
        "locked_by": "",
        "ascii_map": [
            "┌──────────────[窗户]──────────────┐",
            "│                                  │",
            "│  [书桌]                [布告板]  │",
            "│  ┌────┐                ┌──────┐  │",
            "│  │专注│                │ 合约 │  │",
            "│  └────┘                └──────┘  │",
            "│                                  │",
            "│  [钢琴]                [书架]    │",
            "│  ║▌▌▌║                 │≡≡≡≡│    │",
            "│                                  │",
            "└───────────────[门]───────────────┘",
        ],
        "facilities": [
            {"token": "[窗户]", "label": "窗户", "action": "custom", "desc": "推开窗透透气，看看外面的天色。"},
            {"token": "[书桌]", "label": "书桌", "action": "desk", "desc": "打开专注计时器或发起联合行动。"},
            {"token": "[布告板]", "label": "布告板", "action": "board", "desc": "查看合约词条或打开知识库。"},
            {"token": "[钢琴]", "label": "钢琴", "action": "piano", "desc": "打开音乐演奏评判或曲库。"},
            {"token": "[书架]", "label": "书架", "action": "bookshelf", "desc": "挑选书籍开启共读。"},
            {"token": "[门]", "label": "门", "action": "door", "desc": "推门走出房间，前往其他房间。"},
        ],
    },
    "lounge": {
        "id": "lounge",
        "name": "公共客厅",
        "theme": "公共聚会客厅",
        "ambience": "宽敞的木质公共客厅，不需要钥匙，谁都可以随时进来闲聊、合奏或看书。",
        "builtin": True,
        "owner": "",
        "is_public": True,
        "keys": [],
        "locked_inside": False,
        "locked_by": "",
        "ascii_map": [
            "┌─────────────[落地窗]─────────────┐",
            "│                                  │",
            "│  [长沙发]            [共读书柜]  │",
            "│  [════════]          │≡≡≡≡≡≡│    │",
            "│                                  │",
            "│  [钢琴]    [布告板]    [书桌]    │",
            "│  ║▌▌▌║     ┌──────┐    ┌────┐    │",
            "│                                  │",
            "└───────────────[门]───────────────┘",
        ],
        "facilities": [
            {"token": "[落地窗]", "label": "落地窗", "action": "custom", "desc": "望向窗外，感受此刻的天色与氛围。"},
            {"token": "[长沙发]", "label": "长沙发", "action": "custom", "desc": "靠在柔软的沙发上休息或并肩闲聊。"},
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
    return "custom"


def normalize_room(raw: dict) -> dict:
    """校验并补全房间对象（含房主、公共属性、钥匙列表与内部反锁状态）。"""
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
        "ascii_map": clean_lines,
        "facilities": list(fac_by_token.values()),
    }


def render_room_map(room: dict, occupants: list[tuple[str, str]]) -> tuple[str, list[dict]]:
    """渲染房间字符画，把在场人物放进地图底部空行，并返回精确的字符级热点列表。"""
    base_lines = list(room.get("ascii_map") or [])
    if not base_lines:
        base_lines = ["┌────────────────────────┐", "│                        │", "└──────────[门]──────────┘"]

    lines = list(base_lines)
    if occupants:
        actor_tokens = [f"[@{name}]" for _aid, name in occupants]
        actor_rows = []
        for i in range(0, len(actor_tokens), 3):
            chunk = "  ".join(actor_tokens[i:i + 3])
            actor_rows.append(f"│  在场: {chunk}")
        insert_at = len(lines) - 1 if len(lines) >= 2 else len(lines)
        for offset, row in enumerate(actor_rows):
            lines.insert(insert_at + offset, row)

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
                    "desc": tr("点击与 {name} 互动（展开部位列表）", name=name) if aid != "user" else tr("这是你自己"),
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
    """管理分人设房间、钥匙分发、内部反锁、双手槽位、定向听觉日志与房间互动计数。"""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, ROOM_FILE)
        self.rooms: dict[str, dict] = {}
        self.occupants: dict[str, str] = {}     # actor_id -> room_id（所有角色默认在线，各自在自己房间）
        self.room_counts: dict[str, int] = {}   # actor_id -> 在房间里与用户的累计互动条数
        self.hands: dict[str, dict] = {
            "left": {"state": "free", "note": ""},
            "right": {"state": "free", "note": ""},
        }
        self.pending_actions: list[dict] = []
        self.logs: list[dict] = []
        self.turn_no: int = 0
        self.load()

    def _ensure_builtins(self):
        # 迁移清理旧的通用 partner_home
        self.rooms.pop("partner_home", None)
        for rid, raw in BUILTIN_ROOMS.items():
            if rid not in self.rooms:
                self.rooms[rid] = normalize_room(copy.deepcopy(raw))
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
                # 确保房主始终保有自己房间的钥匙
                r = self.rooms[rid]
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
        self.room_counts = {}
        self.logs = []
        self.turn_no = 0
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
                    rc = data.get("room_counts") or {}
                    if isinstance(rc, dict):
                        for aid, val in rc.items():
                            if isinstance(aid, str):
                                try:
                                    self.room_counts[aid] = max(0, int(val or 0))
                                except (ValueError, TypeError):
                                    pass
                    hands = data.get("hands") or {}
                    if isinstance(hands, dict):
                        for side in ("left", "right"):
                            h = hands.get(side)
                            if isinstance(h, dict):
                                st = h.get("state")
                                if st not in ("free", "used", "bound"):
                                    st = "free"
                                self.hands[side] = {"state": st, "note": str(h.get("note") or "")[:60]}
                    logs = data.get("logs") or []
                    if isinstance(logs, list):
                        self.logs = [x for x in logs[-MAX_ROOM_LOGS:] if isinstance(x, dict)]
                    self.turn_no = max(0, int(data.get("turn_no") or 0))
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
            "room_counts": self.room_counts,
            "hands": self.hands,
            "logs": self.logs[-MAX_ROOM_LOGS:],
            "turn_no": self.turn_no,
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

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

    def set_room_lock(self, room_id: str, actor_id: str, locked: bool, actor_name: str = "") -> str:
        room = self.rooms.get(room_id)
        if not room:
            return "房间不存在"
        if self.occupants.get(actor_id) != room_id:
            return "只有在房间内部才能反锁或打开门锁"
        if bool(room.get("locked_inside")) == bool(locked):
            return ""
        room["locked_inside"] = bool(locked)
        room["locked_by"] = actor_id if locked else ""
        who = actor_name or ("你" if actor_id == "user" else actor_id)
        msg = (tr("{who} 从里面反锁了房门（外面即使有钥匙也打不开）。", who=who)
               if locked else tr("{who} 打开了房门的反锁。", who=who))
        self.append_log(room_id, "sys", "系统", ["all"], "sys", msg)
        self.save()
        return ""

    def enter_room(self, actor_id: str, room_id: str, actor_name: str = "", force: bool = False) -> str:
        """移动角色到目标房间。返回空字符串表示成功，否则返回被拒原因（无钥匙 / 内部反锁）。"""
        if room_id not in self.rooms:
            return "房间不存在"
        prev = self.occupants.get(actor_id)
        if prev == room_id:
            return ""
        if not force:
            ok, reason = self.can_enter(actor_id, room_id)
            if not ok:
                return reason
        self.occupants[actor_id] = room_id
        who = actor_name or ("你" if actor_id == "user" else actor_id)
        if prev and prev in self.rooms:
            # 房间空了之后自动解除反锁，防止空房永久锁死
            if not self.occupants_in(prev) and self.rooms[prev].get("locked_inside"):
                self.rooms[prev]["locked_inside"] = False
                self.rooms[prev]["locked_by"] = ""
            self.append_log(prev, "sys", "系统", ["all"], "move",
                            tr("{who} 推开门，离开了房间。", who=who))
        self.append_log(room_id, "sys", "系统", ["all"], "move",
                        tr("{who} 推开门，走进了「{room}」。", who=who, room=self.rooms[room_id]["name"]))
        self.save()
        return ""

    # 兼容旧方法名
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
            self.append_log(prev, "sys", "系统", ["all"], "move",
                            tr("{who} 离开了「{room}」。", who=who, room=self.rooms[prev]["name"]))
        self.save()
        return True

    # ---- 房间互动计数（用于部位解锁） ----

    def room_msg_count(self, actor_id: str) -> int:
        return int(self.room_counts.get(actor_id) or 0)

    def _bump_room_counts(self, room_id: str, sender: str, targets: list[str], kind: str):
        if kind not in ("say", "action"):
            return
        occ = self.occupants_in(room_id)
        if sender == "user":
            heard = [aid for aid in occ if aid != "user" and ("all" in targets or aid in targets)]
            for aid in heard:
                self.room_counts[aid] = self.room_counts.get(aid, 0) + 1
        elif "all" in targets or "user" in targets:
            self.room_counts[sender] = self.room_counts.get(sender, 0) + 1

    # ---- 双手槽位与部位互动 ----

    def available_hands(self) -> list[str]:
        return [side for side in ("left", "right") if self.hands[side]["state"] == "free"]

    def hand_label(self, side: str) -> str:
        name = "左手" if side == "left" else "右手"
        st = self.hands[side]["state"]
        note = self.hands[side].get("note") or ""
        if st == "bound":
            return f"{name}: 被束缚" + (f"({note})" if note else "")
        if st == "used":
            return f"{name}: 已安排" + (f"({note})" if note else "")
        return f"{name}: 空闲"

    def queue_hand_action(self, hand: str, target_id: str, target_name: str, part: str, text: str,
                          msg_count: int | None = None) -> str:
        """安排一次手部互动；对角色互动且提供 msg_count 时校验该部位的条数门槛。"""
        if hand not in ("left", "right"):
            return "请指定左手或右手"
        st = self.hands[hand]["state"]
        if st == "bound":
            return "这只手正被束缚着，暂时无法行动"
        if st == "used":
            return "这只手本回合已经安排了互动"
        clean_part = str(part or "").strip()[:30]
        if msg_count is not None and clean_part != "设施":
            ok, req = part_allowed(clean_part, msg_count)
            if not ok:
                return tr("「{part}」需要与对方累计 {req} 条消息才允许接触（当前 {cur} 条，还差 {need} 条）。",
                          part=clean_part, req=req, cur=msg_count, need=req - msg_count)
        clean_text = str(text or "").strip()[:300]
        if not clean_text:
            return "请输入互动内容"
        self.hands[hand] = {"state": "used", "note": f"{target_name}·{clean_part}"[:24]}
        self.pending_actions.append({
            "hand": hand,
            "target_id": target_id,
            "target_name": target_name,
            "part": clean_part,
            "text": clean_text,
        })
        return ""

    def remove_pending_action(self, index: int):
        if 0 <= index < len(self.pending_actions):
            item = self.pending_actions.pop(index)
            side = item.get("hand")
            if side in ("left", "right") and self.hands[side]["state"] == "used":
                self.hands[side] = {"state": "free", "note": ""}

    def set_hand_bound(self, side: str, bound: bool, note: str = ""):
        sides = ("left", "right") if side == "both" else ((side,) if side in ("left", "right") else ())
        for s in sides:
            if bound:
                self.hands[s] = {"state": "bound", "note": str(note or "")[:40]}
                self.pending_actions = [a for a in self.pending_actions if a.get("hand") != s]
            elif self.hands[s]["state"] == "bound":
                self.hands[s] = {"state": "free", "note": ""}
        self.save()

    def reset_turn_hands(self, save: bool = True, advance_turn: bool = False):
        self.pending_actions = []
        if advance_turn:
            self.turn_no += 1
        for side in ("left", "right"):
            if self.hands[side]["state"] == "used":
                self.hands[side] = {"state": "free", "note": ""}
        if save:
            self.save()

    # ---- 定向听觉隔离日志 ----

    def append_log(self, room_id: str, sender: str, sender_name: str, targets: list[str],
                   kind: str, text: str, actions: list[dict] = None) -> dict:
        clean_targets = [str(t) for t in (targets or ["all"]) if str(t)]
        if not clean_targets:
            clean_targets = ["all"]
        entry = {
            "id": uuid.uuid4().hex[:10],
            "ts": int(time.time() * 1000),
            "room_id": room_id,
            "sender": sender,
            "sender_name": sender_name,
            "targets": clean_targets,
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

    def visible_logs_for(self, actor_id: str, room_id: str, limit: int = 35) -> list[dict]:
        """严格按听觉权限过滤：只有发话者本人或 targets 包含该角色/all 时可见。"""
        out = [e for e in self.logs if e.get("room_id") == room_id and self.can_hear(e, actor_id)]
        return out[-limit:]

    def user_display_logs(self, room_id: str, name_map: dict[str, str], limit: int = 120) -> list[dict]:
        """给用户界面展示的日志：对未指定用户听到的 NPC 间私聊做脱敏提示，不泄露原文。"""
        out = []
        for e in [x for x in self.logs if x.get("room_id") == room_id][-limit:]:
            if self.can_hear(e, "user"):
                item = dict(e)
                item["redacted"] = False
                out.append(item)
            else:
                target_names = "、".join(name_map.get(t, t) for t in (e.get("targets") or []))
                out.append({
                    "id": e["id"],
                    "ts": e["ts"],
                    "room_id": room_id,
                    "sender": e["sender"],
                    "sender_name": e["sender_name"],
                    "targets": e.get("targets") or [],
                    "kind": "private_hint",
                    "text": tr("{who} 对 {to} 低声说了些什么（你没有听清）。",
                               who=e["sender_name"], to=target_names or tr("某人")),
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
        '  "facilities": [{"token": "[设施名]", "label": "设施名", "action": "piano|bookshelf|desk|board|door|custom", "desc": "简短说明"}]\n'
        "}\n"
        "要求：\n"
        "1. ascii_map 使用 Unicode 框线字符（┌ ─ ┐ │ └ ┘）绘制一个 8~13 行、宽度约 34~42 字符的整齐房间。\n"
        "2. 所有可点击设施必须用半角方括号包裹，如 [钢琴]、[书架]、[书桌]、[布告板]、[门] 以及符合主题的特色设施（如 [天文望远镜]、[咖啡机]）。\n"
        "3. 地图底部必须包含 [门]（action 为 door），方便玩家进出房间。\n"
        "4. action 映射：钢琴/乐器用 piano，书架/藏书用 bookshelf，书桌/工作台用 desk，布告板/笔记用 board，门用 door，其他特色设施用 custom。"
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
                            trigger_reason: str = "user") -> tuple[str, list[dict]]:
    occ_ids = store.occupants_in(room["id"])
    occ_desc = "、".join(f"{all_names.get(aid, aid)}(id={aid})" for aid in occ_ids)
    hand_desc = f"用户左手={store.hands['left']['state']}, 用户右手={store.hands['right']['state']}"
    map_text = "\n".join(room.get("ascii_map") or [])

    # 告诉角色世界里有哪些房间、哪些能去（公共或有钥匙且未反锁）、哪些被反锁
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
        f"本房间在场成员：{occ_desc}\n"
        f"你的身份：{actor_name}(id={actor_id})\n"
        f"用户双手状态：{hand_desc}\n"
        f"房间字符画地图：\n{map_text}\n\n"
        "【世界房间列表】\n" + "\n".join(world_lines) + "\n\n"
        "【规则】\n"
        "1. 你只能看到发给你、对全员(all)说、或由你发出的消息；没指定你的话你听不到。\n"
        "2. 你可以自由决定本轮对谁说话（字段 to 填本房间成员 id 列表，如 [\"user\"]、[\"all\"] 或其他在场角色 id）。\n"
        "3. 你可以自行移动去公共房间或你持有钥匙且未反锁的房间拜访他人（move_to 填房间 id，不移动填 null）。\n"
        "4. 若你此刻不想被打扰，可将 lock_door 设为 true 从内部反锁房门（false 为解锁，null 为不变）。\n"
        "5. 若你想把自己持有钥匙的房间钥匙分给某人，可填 give_key: {\"room_id\": \"...\", \"to\": \"user\"或角色id}，否则填 null。\n"
        "6. 请严格只输出一个 JSON 对象，保持你原本的人设语气：\n"
        '{"to": ["user"], "action": "简短动作描写(可为空)", "speech": "你的台词(可为空)", '
        '"bind_hands": null, "move_to": null, "lock_door": null, "give_key": null}'
    )
    system = (persona_sys or f"你正在扮演 {actor_name}。") + room_guide

    history_lines = []
    for e in visible_logs:
        targets = e.get("targets") or ["all"]
        to_str = "全员" if "all" in targets else "、".join(all_names.get(t, t) for t in targets)
        if e.get("kind") in ("sys", "move"):
            history_lines.append(f"* {e['text']}")
        else:
            acts = e.get("actions") or []
            act_txt = "；".join(
                f"[{'左手' if a.get('hand') == 'left' else '右手'}→{a.get('target_name')}·{a.get('part')}] {a.get('text')}"
                for a in acts
            )
            body = f"{act_txt} {e.get('text', '')}".strip()
            history_lines.append(f"{e['sender_name']} → {to_str}: {body}")

    hist_block = "\n".join(history_lines) if history_lines else "（房间里刚刚安静下来，还没有人说话。）"
    if trigger_reason == "auto":
        prompt = (
            f"【房间近期记录】\n{hist_block}\n\n"
            "现在过了一小会儿。请根据当前上下文、在场成员与你的人设，自然决定要不要向在场某人发起一句对话，"
            "或者自行移动去公共客厅 / 拜访你有钥匙的房间（不想被打扰也可以反锁房门）。"
        )
    else:
        prompt = f"【房间近期记录】\n{hist_block}\n\n请按你的人设回应当前互动，并指定说话对象（也可按剧情移动、锁门或分钥匙）。"
    return system, [{"role": "user", "content": prompt}]


def parse_actor_reply(reply_text: str, valid_ids: set[str], valid_rooms: set[str] = None,
                      default_target: str = "user") -> dict:
    obj = _extract_json(reply_text)
    if obj:
        raw_to = obj.get("to")
        if isinstance(raw_to, str):
            raw_to = [raw_to]
        targets = []
        if isinstance(raw_to, list):
            for t in raw_to:
                ts = str(t or "").strip()
                if ts == "all" or ts in valid_ids:
                    targets.append(ts)
        if not targets:
            targets = [default_target] if default_target in valid_ids else ["all"]
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
        return {
            "to": targets,
            "action": str(obj.get("action") or "").strip()[:240],
            "speech": str(obj.get("speech") or "").strip()[:1200],
            "bind_hands": bind,
            "move_to": move_to,
            "lock_door": lock_door,
            "give_key": give_key,
        }
    return {
        "to": [default_target] if default_target in valid_ids else ["all"],
        "action": "",
        "speech": str(reply_text or "").strip()[:1200],
        "bind_hands": None,
        "move_to": None,
        "lock_door": None,
        "give_key": None,
    }


def pick_auto_speaker(store: RoomStore, room_id: str, busy_ids: set[str],
                      enabled: bool, prob_pct: int, rng=None,
                      include_other_rooms: bool = False) -> tuple[str, str] | None:
    """按概率挑选一位空闲的非玩家角色自主行动（对话或移动拜访）。返回 (actor_id, actor_room_id) 或 None。"""
    if not enabled:
        return None
    r = rng or random
    p = max(0, min(100, int(prob_pct or 0))) / 100.0
    if p <= 0 or r.random() >= p:
        return None
    if include_other_rooms:
        candidates = [(aid, rid) for aid, rid in store.occupants.items() if aid != "user" and aid not in busy_ids]
    else:
        candidates = [(aid, room_id) for aid in store.occupants_in(room_id) if aid != "user" and aid not in busy_ids]
    if not candidates:
        return None
    return r.choice(candidates)
