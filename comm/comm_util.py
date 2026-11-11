# -*- coding: utf-8 -*-
"""沟通的无状态小工具 (MECHANICS 24): 消息 / 回复 / 记忆卡 / 发给模型前的整理; 不碰 Comm 的状态."""

import re
from datetime import datetime

import ai_assist
from comm.comm_persona import MEM_MAX, persona_title
from comm.comm_prompt import REFUSE, MSGS_TAG


OIT_TAG = "[OIT_SYSTEM_MESSAGES]"      # OIT 直接发出的消息都带这个标记 (1.0.423)
STATE_TAG = "state"                   # 对方输入 [state]: OIT 告诉它此刻的日程状态 (这一段的性质 + 结束时间), 再调用一次 (1.0.414)
HISTORY_SENT = 200                    # 每次请求最多带多少条聊天记录 (本地存 600 条, 带的取最近 200 条): 原话全带上, 量由下面的字数兜底
HISTORY_CHARS = 120000                # 带的聊天记录总共最多多少字 (超了从最老的开始不带)
HISTORY_STEP = 10                     # 带的聊天记录的起点一次往后挪这么多条 (不是每条都挪): 请求的开头 (前面的历史) 才能在这么多条消息里保持不变,
                                      # 服务商的提示缓存才命中 (分条发连着调用两次, 第二次的前面全是第一次发过的内容)
MAX_MSG_CHARS = 8000                  # 单条消息超过这么多字: 只截掉中间 (发给模型的那份, 本地记录不动), 免得一次粘贴 / 一个附件吃掉整个窗口
COND_TAG = "condition"               # 人设里的判定交给 OIT: 对方输入 [condition], OIT 算出用户当前的进度和每个条件满足没有, 再调用一次 (1.0.392)
WEEKDAY = "一二三四五六日"


def is_claudia(name: str) -> bool:
    """人设名字是 Claudia (文件名的后缀不算, 不分大小写: Claudia.txt / claudia.md)."""
    return persona_title(name).strip().casefold() == "claudia"


def is_refusal(text: str) -> bool:
    return REFUSE in re.sub(r"\s+", " ", str(text or "")).lower()


def stamp(now: datetime) -> str:
    return f"{now:%Y-%m-%d} 周{WEEKDAY[now.weekday()]} {now:%H:%M}"


def urlsplit_host(url) -> str:
    from urllib.parse import urlsplit
    try:
        return urlsplit(str(url or "")).hostname or "?"
    except ValueError:
        return "?"


def info_tags(reply: str) -> list:
    """对方这次回复是在要资料吗: 有 [模块名] (不分大小写和空格; 有没有 [refuse to reply] 都算) → 要的模块列表 (comm_data.MODULE_TAGS)."""
    t = re.sub(r"\s+", " ", str(reply or "")).lower()
    from comm import comm_data
    tags = [tag for tag in comm_data.MODULE_TAGS if f"[{tag}]" in t]
    if f"[{COND_TAG}]" in t:                 # 人设里要 OIT 帮忙判定的条件
        tags.append(COND_TAG)
    if f"[{MSGS_TAG}]" in t:                 # 聊天记录数 (1.0.402)
        tags.append(MSGS_TAG)
    if f"[{STATE_TAG}]" in t:                # 此刻的日程状态 / 结束时间 (1.0.414)
        tags.append(STATE_TAG)
    return tags


def joint_answer(reply: str) -> str:
    """对方对联合行动邀请的回复: "yes" ([Yes]) / "no" ([No]) / "" (没有表态). 不分大小写和空格; 两个都有按拒绝."""
    t = re.sub(r"\s+", "", str(reply or "")).lower()
    if "[no]" in t:
        return "no"
    return "yes" if "[yes]" in t else ""


def _joint_time(v) -> datetime:
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))


def clip_message(text: str, limit: int = MAX_MSG_CHARS) -> str:
    """一条消息太长时只截掉中间: 开头和结尾通常才是要紧的, 中间省略 (只影响发给模型的那份)."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return text[:head] + f"\n…（中间省略 {len(text) - limit} 字）…\n" + text[-tail:]


def cache_layout(profile: dict, system: str, dynamic: str, msgs: list) -> tuple:
    """system 和会变的内容怎么放. Gemini 的隐式缓存要求 systemInstruction 逐字节相同:
    记忆卡 / 联合行动 / 身份背景都不能放进 system, 要贴到最后一条用户消息里; 别家照旧放 system."""
    if ai_assist.cache_plan(profile)["static_system"]:
        if dynamic:
            if msgs and msgs[-1]["role"] == "user":
                msgs[-1]["content"] += dynamic
            else:
                msgs.append({"role": "user", "content": dynamic.strip()})
        return system, msgs
    return system + dynamic, msgs


_OIT_SYS_TAG_RE = re.compile(r"^\[系统[^\]]*\]\s*")


def oit_text(text: str) -> str:
    """OIT 直接发给模型的消息: 开头带上 [OIT_SYSTEM_MESSAGES] 标记 (已经有了就不重复).
    有标记了就不再发 "[系统]" / "[系统·续]" 这类系统前缀, 免得同一件事标两次."""
    t = str(text or "")
    if t.startswith(OIT_TAG):
        return t
    t = _OIT_SYS_TAG_RE.sub("", t, count=1)
    return f"{OIT_TAG}\n{t}"


def first_turn(msgs: list) -> bool:
    """这一段对话是不是刚开始 (最后一条用户消息之前还没有对方的回复).
    身份背景只在第一轮请求里发一次: 之后不再每次付这份 token."""
    last_me = -1
    for i, m in enumerate(msgs):
        if m.get("role") == "me":
            last_me = i
    if last_me < 0:
        return not any(m.get("role") == "ai" for m in msgs)
    return not any(m.get("role") == "ai" for m in msgs[:last_me])


def api_messages(msgs: list) -> list:
    """本地聊天记录 → API 的 messages: 连续同角色的合并, 以 user 开头; 总字数太多时从最老的开始不带."""
    msgs = list(msgs)
    if len(msgs) > HISTORY_SENT:                      # 起点按 HISTORY_STEP 一格一格挪: 中间的每一次请求, 开头都和上一次一模一样
        drop = -(-(len(msgs) - HISTORY_SENT) // HISTORY_STEP) * HISTORY_STEP
        msgs = msgs[drop:]
    while len(msgs) > 1 and sum(len(str(m.get("api") or m.get("text") or "")) for m in msgs) > HISTORY_CHARS:
        msgs = msgs[min(HISTORY_STEP, len(msgs) - 1):]
    out = []
    for m in msgs:
        role = "assistant" if m.get("role") == "ai" else "user"
        text = clip_message(str(m.get("api") or m.get("text") or ""))
        if not text:
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + text
        else:
            out.append({"role": role, "content": text})
    while out and out[0]["role"] == "assistant":
        out.pop(0)
    return out


def sanitize_mem(text: str) -> str:
    """模型给的记忆卡: 去掉代码块 / 解释, 统一成 "- " 开头的短行, 限制条数和字数; 是拒绝语 / 空的就当没给."""
    t = str(text or "").strip()
    if not t or is_refusal(t):
        return ""
    t = re.sub(r"```[^\n]*", "", t).replace("```", "").strip()
    lines = []
    for ln in t.splitlines():
        ln = ln.strip().lstrip("-•*· ").strip()
        if ln and "【系统记录】" not in ln:             # 系统记录由 OIT 自己写进长期记忆, 不收模型抄的
            lines.append("- " + ln)
        if len(lines) >= 15:
            break
    return "\n".join(lines)[:MEM_MAX]
