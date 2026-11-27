# -*- coding: utf-8 -*-
"""记忆系统核心纯工具：模板机读块剥离、带短 ID 的实时记忆编辑、本地冷归档 JSONL 与检索、睡眠期 Dreaming 校验。

无状态纯函数模块，不依赖 UI、网络或 Comm 实例，供桌面端与 MCP Bridge 共用。
"""

from datetime import datetime
import json
import os
import re

from comm.comm_persona import MEM_MAX

# 运行时已由 Python/Kotlin 程序解析并按需注入状态标签的纯机读模板块；
# 从发给模型的 System Prompt 中剥离可显著减少上下文噪声，且不改变任何规则解析行为。
MACHINE_TEMPLATE_BLOCKS = (
    "shift",
    "condition",
    "condition:strict",
    "valence:events",
    "valence:initial",
    "scenario",
    "valence:burst",
    "burst:consent",
    "tension:events",
    "tension:relationship",
    "life:arcs",
    "trend",
    "callback",
    "anti_repeat",
    "typing",
)

_MACHINE_BLOCK_RE = re.compile(
    r"(?ms)^\[(?:" + "|".join(re.escape(b) for b in MACHINE_TEMPLATE_BLOCKS) + r")\][^\n]*\n.*?^\[/(?:"
    + "|".join(re.escape(b) for b in MACHINE_TEMPLATE_BLOCKS) + r")\][^\n]*\n?"
)
_MULTI_BLANK_RE = re.compile(r"\n{3,}")

_LIVE_ID_RE = re.compile(r"^\[#([A-Za-z][A-Za-z0-9_-]{0,11})\]\s*(.+)$")
_TARGET_ID_RE = re.compile(r"^\[?#?([A-Za-z][A-Za-z0-9_-]{0,11})\]?$")
_ID_NUM_RE = re.compile(r"^E(\d+)$", re.I)

# 说教跟进类条目硬保护：Dreaming 离线清理不得自动移除未确认完成的说教条目
_LECTURE_PROTECT_RE = re.compile(r"(说教|改变目标|未完成|改造|习惯纠正)")

DREAM_PROMPT = (
    "你是 OIT 沟通模块的离线睡眠期记忆整理器（Dreaming）。\n"
    "给你「当前时间」「对方的人设（仅看记忆保留规则）」「现有长期记忆摘要（mem_auto）」「当前实时事件（mem_live，带 [#E*] 编号）」"
    "以及「近期完整对话记录」。请做一次冷静的向后反思与整合，只输出一个 JSON 对象，不要解释、不要 Markdown 代码块。\n"
    "JSON 字段：\n"
    '  {"mem_auto": "更新后的长期记忆摘要（纯文本，每条以 - 开头，不超过 40 字，最多 15 条）", '
    '"remove_live_ids": ["可安全清理的实时事件编号，如 E1；没有则空数组 []"]}\n'
    "整理原则：\n"
    "1. 核验与纠错：对照近期原始对话检查现有 mem_auto，修正写错、过期或被新对话推翻的事实；核实带 ? 的不确定条目。\n"
    "2. 跨层晋升：若 mem_live 中某条已沉淀为长期稳定的偏好、约定或身份事实，可晋升进 mem_auto，并把其编号放入 remove_live_ids。\n"
    "3. 清理过期实时事件：仅将已明确解决、已晋升、或已过期超过宽限期的回访编号放入 remove_live_ids；"
    "仍在跟进的说教改造目标（除非对话明确证实已完成改变）与未到期回访绝不要移除。\n"
    "4. 遵循人设【记忆保留】规则；绝不改写、删除或抄入任何【系统记录】。\n"
    "5. 严禁编造对话里没有出现过的事实；没有需要改动时保持原样。"
)


def strip_machine_template_blocks(text: str) -> str:
    """从发给模型的 System Prompt 中剥离纯机读模板块，保留 [tension]/[valence] 与日程正文。"""
    src = str(text or "")
    if not src:
        return ""
    cleaned = _MACHINE_BLOCK_RE.sub("", src)
    cleaned = _MULTI_BLANK_RE.sub("\n\n", cleaned)
    return cleaned.strip()


def parse_live_entries(mem_live: str) -> list[tuple[str, str]]:
    """解析实时记忆列表 -> [(id, body), ...]。兼容旧版无 ID 行（id 为 ""）。"""
    out: list[tuple[str, str]] = []
    for raw in str(mem_live or "").splitlines():
        s = raw.strip()
        if not s.startswith("- "):
            continue
        item = s[2:].strip()
        if not item:
            continue
        m = _LIVE_ID_RE.match(item)
        if m:
            out.append((m.group(1).upper(), m.group(2).strip()))
        else:
            out.append(("", item))
    return out


def format_live_entries(entries: list[tuple[str, str]]) -> str:
    lines = []
    for eid, body in entries:
        if eid:
            lines.append(f"- [#{eid}] {body}")
        else:
            lines.append(f"- {body}")
    return "\n".join(lines)


def _next_live_id(entries: list[tuple[str, str]]) -> str:
    used = {eid.upper() for eid, _ in entries if eid}
    max_n = 0
    for eid in used:
        m = _ID_NUM_RE.match(eid)
        if m:
            max_n = max(max_n, int(m.group(1)))
    cand = max_n + 1
    while f"E{cand}" in used:
        cand += 1
    return f"E{cand}"


def _find_live_index(entries: list[tuple[str, str]], target: str) -> int:
    """支持按短 ID（E1 / #E1 / [#E1]）、完整行（[#E1] 正文）或纯正文匹配。"""
    t = str(target or "").strip()
    if not t:
        return -1
    if t.startswith("- "):
        t = t[2:].strip()
    m_id = _TARGET_ID_RE.match(t)
    if m_id:
        want = m_id.group(1).upper()
        for idx, (eid, _body) in enumerate(entries):
            if eid.upper() == want:
                return idx
    m_full = _LIVE_ID_RE.match(t)
    if m_full:
        want_id = m_full.group(1).upper()
        want_body = m_full.group(2).strip()
        for idx, (eid, body) in enumerate(entries):
            if eid.upper() == want_id or body == want_body:
                return idx
    for idx, (eid, body) in enumerate(entries):
        full = f"[#{eid}] {body}" if eid else body
        if t == body or t == full:
            return idx
    return -1


def apply_live_edit(
    mem_live: str,
    action: str,
    target: str,
    text: str,
    *,
    max_items: int = 15,
    max_chars: int = MEM_MAX,
) -> tuple[bool, str, list[str], str]:
    """执行实时记忆 add/correct/remove；返回 (ok, new_mem_live, display_entries, error_msg)。"""
    entries = parse_live_entries(mem_live)
    act = str(action or "").strip().lower()
    tgt = str(target or "").strip()
    content = str(text or "").strip()
    # 若模型误在 text 开头重复写了 [#E1] 前缀，自动剥离，避免双重前缀
    m_dup = _LIVE_ID_RE.match(content)
    if m_dup:
        content = m_dup.group(2).strip()
    valid_text = isinstance(content, str) and "\n" not in content and "\r" not in content and "【系统记录】" not in content

    if act == "add":
        if tgt or not content or not valid_text or len(entries) >= max_items:
            return False, mem_live, [], "记忆事件不匹配或已达到数量上限"
        if any(body == content for _, body in entries):
            return False, mem_live, [], "记忆事件已存在"
        eid = _next_live_id(entries)
        entries.append((eid, content))
    elif act == "correct":
        idx = _find_live_index(entries, tgt)
        if idx < 0 or not content or not valid_text:
            return False, mem_live, [], "记忆事件不匹配或内容无效"
        eid = entries[idx][0] or _next_live_id(entries)
        entries[idx] = (eid, content)
    elif act == "remove":
        idx = _find_live_index(entries, tgt)
        if idx < 0:
            return False, mem_live, [], "未找到匹配的记忆事件"
        entries.pop(idx)
    else:
        return False, mem_live, [], "不支持的记忆操作"

    new_text = format_live_entries(entries)
    if len(new_text) > max_chars:
        return False, mem_live, [], "实时记忆总字数已达到上限"
    display = [f"[#{eid}] {body}" if eid else body for eid, body in entries]
    return True, new_text, display, ""


# ══════════════ 本地冷归档 JSONL 与按需检索 ══════════════

def archive_path(data_dir: str, pid: str) -> str:
    safe_pid = re.sub(r"[^A-Za-z0-9_-]", "", str(pid or ""))
    if not data_dir or not safe_pid:
        return ""
    return os.path.join(data_dir, "chat_archive", f"{safe_pid}.jsonl")


def archive_append(data_dir: str, pid: str, messages: list) -> int:
    """将超出 KEEP 窗口的旧消息追加写入本地冷归档 JSONL（仅保留可见 me/ai 对话）。"""
    path = archive_path(data_dir, pid)
    if not path or not messages:
        return 0
    rows = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("me", "ai"):
            continue
        if m.get("hold") or "pending_reply" in m:
            continue
        text = str(m.get("text") or "").strip()
        if not text:
            continue
        ts = int(m.get("ts") or 0)
        at = str(m.get("at") or "")
        if not at and ts > 0:
            try:
                at = datetime.fromtimestamp(ts / 1000).isoformat(timespec="seconds")
            except (OSError, OverflowError, ValueError):
                at = ""
        rows.append({
            "uid": str(m.get("uid") or ""),
            "ts": ts,
            "at": at,
            "role": role,
            "text": text[:2000],
        })
    if not rows:
        return 0
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        seen_uids = set()
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            obj = json.loads(line)
                            uid = str(obj.get("uid") or "")
                            if uid:
                                seen_uids.add(uid)
                        except json.JSONDecodeError:
                            continue
            except OSError:
                pass
        written = 0
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            for r in rows:
                if r["uid"] and r["uid"] in seen_uids:
                    continue
                if r["uid"]:
                    seen_uids.add(r["uid"])
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                written += 1
        return written
    except OSError:
        return 0


def archive_clear(data_dir: str, pid: str):
    path = archive_path(data_dir, pid)
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _load_archive_rows(data_dir: str, pid: str) -> list[dict]:
    path = archive_path(data_dir, pid)
    if not path or not os.path.isfile(path):
        return []
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                    if isinstance(obj, dict) and obj.get("role") in ("me", "ai") and obj.get("text"):
                        rows.append(obj)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows


def archive_summary_line(data_dir: str, pid: str) -> str:
    """L4 顶层极简目录指针：仅当存在本地冷归档时返回一行提示。"""
    rows = _load_archive_rows(data_dir, pid)
    if not rows:
        return ""
    first_at = ""
    for r in rows:
        at = str(r.get("at") or "")
        if len(at) >= 10:
            first_at = at[:10]
            break
        ts = int(r.get("ts") or 0)
        if ts > 0:
            try:
                first_at = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
                break
            except (OSError, OverflowError, ValueError):
                pass
    since = f"自 {first_at} 起" if first_at else ""
    return f"- 【可检索档案】本地归档了{since}共 {len(rows)} 条更早聊天记录；回忆往事时可用 oit_memory_search 按关键词或日期检索。"


def _row_day(row: dict) -> str:
    at = str(row.get("at") or "")
    if len(at) >= 10 and at[4:5] == "-" and at[7:8] == "-":
        return at[:10]
    ts = int(row.get("ts") or 0)
    if ts > 0:
        try:
            return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        except (OSError, OverflowError, ValueError):
            pass
    return ""


def search_memory_archive(
    data_dir: str,
    pid: str,
    mem_msgs: list,
    *,
    query: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 8,
) -> dict:
    """按关键词和/或日期范围检索本地冷归档与当前对话记录，返回命中及前后上下文。"""
    q = str(query or "").strip()
    d_from = str(date_from or "").strip()[:10]
    d_to = str(date_to or "").strip()[:10]
    lim = max(1, min(15, int(limit or 8)))
    if not q and not d_from and not d_to:
        return {"ok": False, "error": "query、date_from、date_to 至少提供一项"}

    archive_rows = _load_archive_rows(data_dir, pid)
    combined: list[dict] = []
    seen_uids = set()
    for r in archive_rows:
        uid = str(r.get("uid") or "")
        if uid:
            seen_uids.add(uid)
        combined.append({**r, "_src": "archive"})
    for m in (mem_msgs or []):
        if not isinstance(m, dict) or m.get("role") not in ("me", "ai"):
            continue
        if m.get("hold") or "pending_reply" in m:
            continue
        text = str(m.get("text") or "").strip()
        if not text:
            continue
        uid = str(m.get("uid") or "")
        if uid and uid in seen_uids:
            continue
        if uid:
            seen_uids.add(uid)
        combined.append({
            "uid": uid,
            "ts": int(m.get("ts") or 0),
            "at": str(m.get("at") or ""),
            "role": m["role"],
            "text": text[:2000],
            "_src": "recent",
        })
    combined.sort(key=lambda r: (int(r.get("ts") or 0), str(r.get("uid") or "")))

    terms = [t.lower() for t in re.split(r"[\s,，、;；]+", q) if t.strip()]
    scored = []
    for idx, row in enumerate(combined):
        day = _row_day(row)
        if d_from and (not day or day < d_from):
            continue
        if d_to and (not day or day > d_to):
            continue
        text_low = str(row.get("text") or "").lower()
        if terms:
            hits = sum(1 for t in terms if t in text_low)
            if hits == 0:
                continue
            score = hits * 10 + (1 if q.lower() in text_low else 0)
        else:
            score = 1
        scored.append((score, int(row.get("ts") or 0), idx))

    scored.sort(key=lambda x: (-x[0], -x[1]))
    results = []
    for _score, _ts, idx in scored[:lim]:
        row = combined[idx]
        item = {
            "at": str(row.get("at") or _row_day(row))[:16].replace("T", " "),
            "who": "用户" if row.get("role") == "me" else "你",
            "text": str(row.get("text") or "")[:360],
            "source": row.get("_src", "recent"),
        }
        if idx > 0:
            prev = combined[idx - 1]
            item["before"] = ("用户: " if prev.get("role") == "me" else "你: ") + str(prev.get("text") or "")[:180]
        if idx + 1 < len(combined):
            nxt = combined[idx + 1]
            item["after"] = ("用户: " if nxt.get("role") == "me" else "你: ") + str(nxt.get("text") or "")[:180]
        results.append(item)

    return {
        "ok": True,
        "total_searched": len(combined),
        "archive_count": len(archive_rows),
        "matched": len(scored),
        "results": results,
    }


# ══════════════ 睡眠期 Dreaming 解析与安全过滤 ══════════════

def parse_dream_result(raw: str) -> dict | None:
    """解析 Dreaming 返回的 JSON 结果，容错去掉代码块包裹。"""
    s = str(raw or "").strip()
    if not s:
        return None
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(s[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    mem_auto = str(obj.get("mem_auto") or "").strip()
    raw_ids = obj.get("remove_live_ids")
    remove_ids = []
    if isinstance(raw_ids, list):
        for item in raw_ids:
            m = _TARGET_ID_RE.match(str(item or "").strip())
            if m:
                remove_ids.append(m.group(1).upper())
    return {"mem_auto": mem_auto, "remove_live_ids": remove_ids}


def apply_dream_live_removals(mem_live: str, remove_ids: list[str]) -> tuple[str, list[str]]:
    """安全应用 Dreaming 建议清理的实时事件编号：硬保护说教未完成类条目。"""
    if not remove_ids or not mem_live:
        return mem_live, []
    want = {str(x).upper() for x in remove_ids if x}
    entries = parse_live_entries(mem_live)
    kept = []
    removed = []
    for eid, body in entries:
        if eid and eid.upper() in want and not _LECTURE_PROTECT_RE.search(body):
            removed.append(eid.upper())
            continue
        kept.append((eid, body))
    if not removed:
        return mem_live, []
    return format_live_entries(kept), removed
