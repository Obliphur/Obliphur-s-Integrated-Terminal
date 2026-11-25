# -*- coding: utf-8 -*-
"""PreInvocation Hook: on invocationNum == 1, inject compact OIT dynamic state and expand pending_trigger."""

from hook_common import bridge_get, read_stdin_json, write_stdout_json


def main():
    payload = read_stdin_json()
    if int(payload.get("invocationNum") or 0) != 1:
        write_stdout_json({})
        return
    cid = str(payload.get("conversationId") or "").strip()
    last_input = str(payload.get("lastUserInput") or "")
    try:
        res = bridge_get("/turn/context", {
            "conversation_id": cid,
            "host": "antigravity",
            "only_bound": "1",
            "consume_trigger": "1",
            "delta": "1",
            "last_user_input": last_input[:1000],
        })
        if res.get("bound") is False:
            write_stdout_json({})
            return
        sys_msg = str(res.get("system_message") or "").strip()
        if sys_msg:
            write_stdout_json({
                "injectSteps": [
                    {"systemMessage": {"systemMessage": sys_msg}}
                ]
            })
            return
    except Exception:
        pass
    write_stdout_json({})


if __name__ == "__main__":
    main()
