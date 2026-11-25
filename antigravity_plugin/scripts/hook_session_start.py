# -*- coding: utf-8 -*-
"""SessionStart Hook: only inject OIT persona when this conversationId is already bound to an OIT persona.

Unbound conversations remain completely isolated from OIT so users can use Antigravity for other tasks freely.
"""

from hook_common import bridge_get, read_stdin_json, write_stdout_json


def main():
    payload = read_stdin_json()
    cid = str(payload.get("conversationId") or "").strip()
    if not cid:
        write_stdout_json({})
        return
    try:
        chk = bridge_get("/session/check", {"conversation_id": cid})
        if not chk.get("bound"):
            write_stdout_json({})
            return
        persona = bridge_get("/persona/active", {"conversation_id": cid})
        parts = []
        if persona.get("system_prompt"):
            parts.append(str(persona["system_prompt"]))
        if persona.get("memory_block"):
            parts.append("记忆卡:\n" + str(persona["memory_block"]))
        if persona.get("recent_history"):
            parts.append(
                "近期对话上下文（用于新会话无缝续接，请自然承接话题，不要向用户提及换了会话）:\n"
                + str(persona["recent_history"])
            )
        if parts:
            write_stdout_json({
                "injectSteps": [
                    {"systemMessage": {"systemMessage": "\n\n".join(parts)}}
                ]
            })
            return
    except Exception:
        pass
    write_stdout_json({})


if __name__ == "__main__":
    main()
