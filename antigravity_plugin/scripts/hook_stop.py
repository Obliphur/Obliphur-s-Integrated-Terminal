# -*- coding: utf-8 -*-
"""Stop Hook: fallback sync if the model responded with plain text instead of calling oit_reply."""

import json
import os

from hook_common import bridge_post, read_stdin_json, write_stdout_json


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
                    if "oit_reply" in str(tc.get("name") or ""):
                        called_oit_reply = True
                if not last_text and step.get("content"):
                    last_text = str(step["content"]).strip()
    except Exception:
        return "", False
    return last_text, called_oit_reply


def main():
    payload = read_stdin_json()
    tpath = str(payload.get("transcriptPath") or "")
    text, called = _last_planner_text(tpath)
    if text and not called:
        try:
            bridge_post("/turn/fallback_sync", {
                "conversation_id": str(payload.get("conversationId") or ""),
                "text": text,
            })
        except Exception:
            pass
    write_stdout_json({})


if __name__ == "__main__":
    main()
