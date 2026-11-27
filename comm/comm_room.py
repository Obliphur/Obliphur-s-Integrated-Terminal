# -*- coding: utf-8 -*-
"""古早文字游戏房间系统（PC 优先）：多房间字符画地图、设施联动、双手部位互动、严格定向听觉隔离与 NPC 自动对话。"""

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

FACILITY_ACTIONS = {
    "piano": "音乐演奏 / 曲库",
    "bookshelf": "打开共读",
    "desk": "专注计时 / 联合行动",
    "board": "合约 / 知识库",
    "door": "切换房间 / 离开",
    "custom": "设施互动",
}

BODY_PARTS = (
    ("head", "头顶 / 发丝"),
    ("cheek", "脸颊"),
    ("ear", "耳畔"),
    ("shoulder", "肩膀"),
    ("wrist", "手臂 / 手腕"),
    ("hand", "手掌 / 指尖"),
    ("sleeve", "衣袖 / 衣角"),
    ("waist", "后背 / 腰侧"),
    ("hug", "拥抱 / 拉近"),
    ("gaze", "目光注视"),
)

BUILTIN_ROOMS = {
    "my_home": {
        "id": "my_home",
        "name": "我的房间",
        "theme": "日常居室",
        "ambience": "台灯暖黄，窗外夜色安静，桌上的计时器微微亮着。",
        "builtin": True,
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
    "partner_home": {
        "id": "partner_home",
        "name": "对方的居室",
        "theme": "雅致琴房书室",
        "ambience": "木地板泛着柔和光泽，三角钢琴旁摆着刚沏好的红茶。",
        "builtin": True,
        "ascii_map": [
            "┌──────────────[窗台]──────────────┐",
            "│                                  │",
            "│  [三角钢琴]          [古典书架]  │",
            "│  ╔══════╗            │≡≡≡≡≡≡│    │",
            "│  ║▌▌▌▌▌▌║            │≡≡≡≡≡≡│    │",
            "│  ╚══════╝                        │",
            "│                                  │",
            "│  [茶几]                [书桌]    │",
            "│  ( ♨ )                 ┌────┐    │",
            "│                                  │",
            "└───────────────[门]───────────────┘",
        ],
        "facilities": [
            {"token": "[窗台]", "label": "窗台", "action": "custom", "desc": "靠在窗台边吹风、看风景。"},
            {"token": "[三角钢琴]", "label": "三角钢琴", "action": "piano", "desc": "坐到琴凳上演奏或聆听曲目。"},
            {"token": "[古典书架]", "label": "古典书架", "action": "bookshelf", "desc": "抽出一本书一起共读。"},
            {"token": "[茶几]", "label": "茶几", "action": "custom", "desc": "倒一杯热茶或坐下小憩。"},
            {"token": "[书桌]", "label": "书桌", "action": "desk", "desc": "进入专注或约定联合行动。"},
            {"token": "[门]", "label": "门", "action": "door", "desc": "离开这里，切换到其他房间。"},
        ],
    },
    "lounge": {
        "id": "lounge",
        "name": "公共客厅",
        "theme": "多人聚会客厅",
        "ambience": "宽敞的木质客厅，适合几个人围坐闲聊、合奏或各自看书。",
        "builtin": True,
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
    """校验并补全房间对象，自动从字符画中补齐遗漏的 [设施] 热点。"""
    rid = str(raw.get("id") or "").strip() or f"room_{uuid.uuid4().hex[:8]}"
    name = str(raw.get("name") or "未命名房间").strip()[:40]
    theme = str(raw.get("theme") or "自定义场景").strip()[:60]
    ambience = str(raw.get("ambience") or "").strip()[:240]
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
        "ascii_map": clean_lines,
        "facilities": list(fac_by_token.values()),
    }


def render_room_map(room: dict, occupants: list[tuple[str, str]]) -> tuple[str, list[dict]]:
    """渲染房间字符画，把在场人物放进地图底部空行，并返回精确的字符级热点列表。

    occupants: [(actor_id, display_name), ...]
    返回: (rendered_text, hotspots)
    hotspots 每项: {"kind": "facility"|"actor", "id": str, "label": str, "action": str,
                   "desc": str, "line": int (1-based), "col_start": int, "col_end": int}
    """
    base_lines = list(room.get("ascii_map") or [])
    if not base_lines:
        base_lines = ["┌────────────────────────┐", "│                        │", "└──────────[门]──────────┘"]

    lines = list(base_lines)
    # 在底边前插入在场人物行（每个角色形如 [@名字]，方便精确点击）
    if occupants:
        actor_tokens = [f"[@{name}]" for _aid, name in occupants]
        # 每行最多放 3 个角色，避免撑破地图宽度
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
    """管理房间地图、成员进出、用户双手槽位、定向听觉日志与持久化。"""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, ROOM_FILE)
        self.rooms: dict[str, dict] = {}
        self.occupants: dict[str, str] = {}   # actor_id -> room_id
        self.hands: dict[str, dict] = {
            "left": {"state": "free", "note": ""},
            "right": {"state": "free", "note": ""},
        }
        self.pending_actions: list[dict] = []
        self.logs: list[dict] = []
        self.load()

    def _ensure_builtins(self):
        for rid, raw in BUILTIN_ROOMS.items():
            if rid not in self.rooms:
                self.rooms[rid] = normalize_room(copy.deepcopy(raw))
        if "user" not in self.occupants or self.occupants["user"] not in self.rooms:
            self.occupants["user"] = "my_home"

    def load(self):
        self.rooms = {}
        self.occupants = {}
        self.logs = []
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for rid, raw in (data.get("rooms") or {}).items():
                        if isinstance(raw, dict):
                            raw = dict(raw)
                            raw.setdefault("id", rid)
                            norm = normalize_room(raw)
                            self.rooms[norm["id"]] = norm
                    occ = data.get("occupants") or {}
                    if isinstance(occ, dict):
                        for aid, rid in occ.items():
                            if isinstance(aid, str) and isinstance(rid, str) and rid in self.rooms:
                                self.occupants[aid] = rid
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
            except (OSError, json.JSONDecodeError, ValueError):
                pass
        self._ensure_builtins()
        # 重启后未提交的临时动作清零，非束缚手恢复空闲
        self.pending_actions = []
        self.reset_turn_hands(save=False)

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        payload = {
            "rooms": self.rooms,
            "occupants": self.occupants,
            "hands": self.hands,
            "logs": self.logs[-MAX_ROOM_LOGS:],
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ---- 房间与成员管理 ----

    def current_room_id(self) -> str:
        rid = self.occupants.get("user", "my_home")
        return rid if rid in self.rooms else "my_home"

    def current_room(self) -> dict:
        return self.rooms[self.current_room_id()]

    def room_list(self) -> list[dict]:
        return list(self.rooms.values())

    def add_room(self, raw: dict) -> dict:
        room = normalize_room(raw)
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
                if aid == "user":
                    self.occupants[aid] = "my_home"
                else:
                    del self.occupants[aid]
        self.save()
        return True

    def occupants_in(self, room_id: str) -> list[str]:
        return [aid for aid, rid in self.occupants.items() if rid == room_id]

    def join_room(self, actor_id: str, room_id: str, actor_name: str = "") -> bool:
        if room_id not in self.rooms:
            return False
        prev = self.occupants.get(actor_id)
        if prev == room_id:
            return False
        self.occupants[actor_id] = room_id
        who = actor_name or ("你" if actor_id == "user" else actor_id)
        if prev and prev in self.rooms:
            self.append_log(prev, "sys", "系统", ["all"], "move",
                            tr("{who} 推开门，离开了房间。", who=who))
        self.append_log(room_id, "sys", "系统", ["all"], "move",
                        tr("{who} 推开门，走进了「{room}」。", who=who, room=self.rooms[room_id]["name"]))
        self.save()
        return True

    def leave_room(self, actor_id: str, actor_name: str = "") -> bool:
        if actor_id == "user":
            return False
        prev = self.occupants.pop(actor_id, None)
        if not prev:
            return False
        who = actor_name or actor_id
        if prev in self.rooms:
            self.append_log(prev, "sys", "系统", ["all"], "move",
                            tr("{who} 离开了「{room}」。", who=who, room=self.rooms[prev]["name"]))
        self.save()
        return True

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

    def queue_hand_action(self, hand: str, target_id: str, target_name: str, part: str, text: str) -> str:
        """安排一次手部互动。返回空字符串表示成功，否则返回原因。"""
        if hand not in ("left", "right"):
            return "请指定左手或右手"
        st = self.hands[hand]["state"]
        if st == "bound":
            return "这只手正被束缚着，暂时无法行动"
        if st == "used":
            return "这只手本回合已经安排了互动"
        clean_text = str(text or "").strip()[:300]
        if not clean_text:
            return "请输入互动内容"
        self.hands[hand] = {"state": "used", "note": f"{target_name}·{part}"[:24]}
        self.pending_actions.append({
            "hand": hand,
            "target_id": target_id,
            "target_name": target_name,
            "part": str(part or "").strip()[:30],
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

    def reset_turn_hands(self, save: bool = True):
        self.pending_actions = []
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


def build_actor_turn_prompt(persona_sys: str, room: dict, actor_id: str, actor_name: str,
                            occupant_names: dict[str, str], visible_logs: list[dict],
                            hands: dict, trigger_reason: str = "user") -> tuple[str, list[dict]]:
    occ_desc = "、".join(f"{name}(id={aid})" for aid, name in occupant_names.items())
    hand_desc = f"用户左手={hands['left']['state']}, 用户右手={hands['right']['state']}"
    map_text = "\n".join(room.get("ascii_map") or [])
    room_guide = (
        "\n\n【当前场景：古早文字游戏房间】\n"
        f"房间：{room['name']}（主题：{room['theme']}）\n"
        f"氛围：{room.get('ambience', '')}\n"
        f"在场成员：{occ_desc}\n"
        f"你的身份：{actor_name}(id={actor_id})\n"
        f"用户双手状态：{hand_desc}\n"
        f"房间字符画地图：\n{map_text}\n\n"
        "【规则】\n"
        "1. 你只能看到发给你、对全员(all)说、或由你发出的消息；没指定你的话你听不到。\n"
        "2. 你可以自由决定本轮对谁说话（字段 to 填成员 id 列表，如 [\"user\"]、[\"all\"] 或房间里其他角色的 id）。\n"
        "3. 请严格只输出一个 JSON 对象，保持你原本的人设语气：\n"
        '{"to": ["user"], "action": "简短的动作描写(可为空)", "speech": "你的台词", "bind_hands": null}\n'
        '其中 bind_hands 可选 null / "left" / "right" / "both" / "release"（仅在剧情明确握住/束缚或放开用户的手时使用，平时填 null）。'
    )
    system = (persona_sys or f"你正在扮演 {actor_name}。") + room_guide

    history_lines = []
    for e in visible_logs:
        targets = e.get("targets") or ["all"]
        to_str = "全员" if "all" in targets else "、".join(occupant_names.get(t, t) for t in targets)
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
            "现在房间里停顿了一会儿。请根据当前上下文与你的人设，自然决定要不要主动向房间里的某个人（用户或其他在场角色）发起一句话或一个小动作。"
        )
    else:
        prompt = f"【房间近期记录】\n{hist_block}\n\n请按你的人设回应当前互动，并指定说话对象。"
    return system, [{"role": "user", "content": prompt}]


def parse_actor_reply(reply_text: str, valid_ids: set[str], default_target: str = "user") -> dict:
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
        return {
            "to": targets,
            "action": str(obj.get("action") or "").strip()[:240],
            "speech": str(obj.get("speech") or "").strip()[:1200],
            "bind_hands": bind,
        }
    return {
        "to": [default_target] if default_target in valid_ids else ["all"],
        "action": "",
        "speech": str(reply_text or "").strip()[:1200],
        "bind_hands": None,
    }


def pick_auto_speaker(store: RoomStore, room_id: str, busy_ids: set[str],
                      enabled: bool, prob_pct: int, rng=None) -> str | None:
    """按概率从当前房间挑选一位空闲的非玩家角色自动发起对话。"""
    if not enabled:
        return None
    r = rng or random
    p = max(0, min(100, int(prob_pct or 0))) / 100.0
    if p <= 0 or r.random() >= p:
        return None
    candidates = [aid for aid in store.occupants_in(room_id) if aid != "user" and aid not in busy_ids]
    if not candidates:
        return None
    return r.choice(candidates)
