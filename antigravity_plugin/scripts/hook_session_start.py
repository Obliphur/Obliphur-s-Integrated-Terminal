# -*- coding: utf-8 -*-
"""SessionStart Hook: bind Antigravity conversationId to OIT and inject static persona once."""

from hook_common import bridge_get, bridge_post, read_stdin_json, write_stdout_json


def main():
    payload = read_stdin_json()
    cid = str(payload.get("conversationId") or "").strip()
    if not cid:
        write_stdout_json({})
        return
    try:
        bridge_post("/session/bind", {"conversation_id": cid, "host": "antigravity"})
        persona = bridge_get("/persona/active")
        parts = []
        if persona.get("system_prompt"):
            parts.append(str(persona["system_prompt"]))
        if persona.get("memory_block"):
            parts.append("记忆卡:\n" + str(persona["memory_block"]))
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
