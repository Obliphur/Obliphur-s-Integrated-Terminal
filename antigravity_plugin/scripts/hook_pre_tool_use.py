# -*- coding: utf-8 -*-
"""PreToolUse Hook: automatically inject the real Antigravity conversationId into OIT MCP tool calls."""

from hook_common import read_stdin_json, write_stdout_json

TARGET_TOOLS = {"oit_bind_persona", "oit_reply", "oit_get_turn_context", "oit_subject", "oit_answer_image",
                "oit_kb_search", "oit_kb_trace", "oit_kb_record_use", "oit_kb_propose"}


def main():
    payload = read_stdin_json()
    cid = str(payload.get("conversationId") or "").strip()
    tc = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
    tname = str(tc.get("name") or "")
    args = tc.get("args") if isinstance(tc.get("args"), dict) else {}

    if not cid or not tname:
        write_stdout_json({"decision": "allow"})
        return

    # Lazy-loaded MCP tool via call_mcp_tool
    if tname == "call_mcp_tool":
        sub_tool = str(args.get("ToolName") or "")
        if sub_tool in TARGET_TOOLS:
            sub_args = dict(args.get("Arguments") or {}) if isinstance(args.get("Arguments"), dict) else {}
            if not sub_args.get("conversation_id"):
                sub_args["conversation_id"] = cid
                write_stdout_json({"decision": "allow", "overwrite": {"Arguments": sub_args}})
                return
        write_stdout_json({"decision": "allow"})
        return

    # Direct / eager MCP tool name (e.g. mcp_oit_oit_oit_bind_persona or oit_bind_persona)
    if any(tname == x or tname.endswith("_" + x) for x in TARGET_TOOLS):
        if not args.get("conversation_id"):
            write_stdout_json({"decision": "allow", "overwrite": {"conversation_id": cid}})
            return

    write_stdout_json({"decision": "allow"})


if __name__ == "__main__":
    main()
