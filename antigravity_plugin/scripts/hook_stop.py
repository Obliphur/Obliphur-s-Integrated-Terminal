# -*- coding: utf-8 -*-
"""Stop Hook: fallback sync if the model responded with plain text instead of calling oit_reply."""

import json
import os

from hook_common import bridge_get, bridge_post, read_stdin_json, write_stdout_json


def _last_planner_text(transcript_path: str) -> tuple[str, bool]:
    if not transcript_path or not os.path.isfile(transcript_path):
        return "", False
    last_text = ""
    called_oit_reply = False
    try:
        with open(transcript_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()[-40:]
        for line in reversed(lines):
            try:
                step = json.loads(line)
            except Exception:
                continue
            if step.get("type") == "USER_INPUT":
                break
            if step.get("type") == "PLANNER_RESPONSE":
                for tc in step.get("tool_calls") or []:
                    if "oit_reply" in json.dumps(tc, ensure_ascii=False):
                        called_oit_reply = True
                if not last_text and step.get("content"):
                    last_text = str(step["content"]).strip()
            elif "oit_reply" in line:
                called_oit_reply = True
    except Exception:
        return "", False
    return last_text, called_oit_reply


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
    except Exception:
        write_stdout_json({})
        return
    tpath = str(payload.get("transcriptPath") or "")
    text, called = _last_planner_text(tpath)
    if text and not called:
        try:
            bridge_post("/turn/fallback_sync", {
                "conversation_id": cid,
                "text": text,
            })
        except Exception:
            pass
    write_stdout_json({})


if __name__ == "__main__":
    main()
