# -*- coding: utf-8 -*-
"""标准化短期子代理调用；子代理只收到任务上下文，不接收聊天人设。"""

import json

from .ai_http import chat_messages

MAX_TURNS = 5
MAX_TEXT = 8000

SYSTEMS = {
    "mental_health": (
        "你是独立的心理与情绪分析子代理。只分析用户明确写出的心理困扰、情绪、需求和可观察的风险信号，"
        "不做临床诊断，不编造用户没有说过的经历，不输出给用户的成品回复。请用简洁的中文列出："
        "事实与情绪、用户可能需要的回应重点、是否出现需要优先关注的自伤或即时安全信号；证据不足就明确写未知。"
        "不要机械套用危机热线或免责声明模板，也不要输出隐藏思维过程。"
    ),
    "subject": (
        "你是独立的数学、物理、化学教育子代理。只处理给定题目和本子代理会话中的相关追问，"
        "不要假设聊天人设或其他聊天上下文。核对单位、符号和计算；返回简洁、可核验的答案，"
        "包含最终结论和必要的关键步骤。不要输出隐藏思维过程，不要调用工具。"
    ),
    "music_review": (
        "你是独立的音乐演奏分析子代理。根据结构化演奏数据和本子代理会话中的历史评判，"
        "用中文给出简短、具体、克制的练习建议；不臆测音色、力度、踏板或用户意图，证据不足就明确说明。"
    ),
}


def _turns(history):
    """只接受最近五轮干净的 user/assistant 文本，避免把聊天上下文带入子代理。"""
    out = []
    for item in history or ():
        if not isinstance(item, dict):
            continue
        user = str(item.get("user") or "").strip()[:MAX_TEXT]
        assistant = str(item.get("assistant") or "").strip()[:MAX_TEXT]
        if user and assistant:
            out.append({"role": "user", "content": user})
            out.append({"role": "assistant", "content": assistant})
    return out[-MAX_TURNS * 2:]


def invoke(profile, role, task, history=(), *, image=None, max_tokens=700, temperature=0.2,
           timeout=120, cache=False, on_usage=None, chat=None):
    """调用一个无聊天上下文的短期子代理，返回纯文本结果。"""
    if role not in SYSTEMS:
        raise ValueError("未知子代理类型")
    task = str(task or "").strip()[:MAX_TEXT]
    if not task:
        raise ValueError("子代理任务为空")
    content = task
    if image:
        from .ai_http import image_content, image_mime
        mime = image_mime(image)
        if mime:
            content = image_content(profile, image, mime, task)
    messages = _turns(history) + [{"role": "user", "content": content}]
    fn = chat or chat_messages
    result = fn(profile, SYSTEMS[role], messages, max_tokens=max_tokens,
                temperature=temperature, timeout=timeout, cache=cache,
                on_usage=on_usage)
    return str(result or "").strip()[:MAX_TEXT]


def record(history, task, result):
    """把一轮子代理问答压缩成可持久化的五轮上下文。"""
    items = list(history or ())
    items.append({"user": str(task or "").strip()[:MAX_TEXT],
                  "assistant": str(result or "").strip()[:MAX_TEXT]})
    return items[-MAX_TURNS:]


def encode(history):
    """用于聊天存档/设备同步的有限大小表示。"""
    return json.loads(json.dumps(list(history or ())[-MAX_TURNS:], ensure_ascii=False))
