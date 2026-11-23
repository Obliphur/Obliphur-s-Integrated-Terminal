# -*- coding: utf-8 -*-
"""Antigravity Sidecar: subscribes to OIT Bridge SSE stream and wakes conversations via agentapi."""

import json
import os
import subprocess
import sys
import time
import urllib.request


def load_bridge() -> dict:
    if os.environ.get("OIT_BRIDGE_PORT") and os.environ.get("OIT_BRIDGE_TOKEN"):
        return {
            "port": int(os.environ["OIT_BRIDGE_PORT"]),
            "token": os.environ["OIT_BRIDGE_TOKEN"],
        }
    candidates = []
    if os.environ.get("OIT_BRIDGE_FILE"):
        candidates.append(os.environ["OIT_BRIDGE_FILE"])
    if os.environ.get("OIT_DATA_DIR"):
        candidates.append(os.path.join(os.environ["OIT_DATA_DIR"], "mcp_bridge.json"))
    marker = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".oit_data_dir"))
    if os.path.isfile(marker):
        try:
            with open(marker, "r", encoding="utf-8") as fh:
                d = fh.read().strip()
            if d:
                candidates.append(os.path.join(d, "mcp_bridge.json"))
        except Exception:
            pass
    candidates.append(os.path.join(os.path.expanduser("~"), "AppData", "LocalLow", "Obliphur's Integrated Terminal", "mcp_bridge.json"))
    appdata = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    candidates.append(os.path.join(appdata, "OIT", "mcp_bridge.json"))
    candidates.append(os.path.join(os.path.expanduser("~"), ".oit", "mcp_bridge.json"))
    for p in candidates:
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    d = json.load(fh)
                if d.get("port") and d.get("token"):
                    return d
            except Exception:
                pass
    return {}


def dispatch_event(event: dict):
    cid = str(event.get("conversation_id") or "").strip()
    display_time = str(event.get("display_time") or "...").strip()
    kind = str(event.get("kind") or "proactive")
    if kind == "memory_compact":
        subprocess.run(
            ["agentapi", "new-conversation", "--", "请使用 oit-memory-compact 子代理整理当前 OIT 记忆卡并通过 oit_memory_save_auto 写回。"],
            check=False,
        )
        return
    if cid:
        subprocess.run(["agentapi", "send-message", cid, display_time], check=False)
    else:
        subprocess.run(["agentapi", "new-conversation", "--", display_time], check=False)


def consume_sse(cfg: dict):
    url = f"http://127.0.0.1:{cfg['port']}/events/stream"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {cfg['token']}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        event_name = ""
        data_lines = []
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                if data_lines:
                    try:
                        payload = json.loads("\n".join(data_lines))
                        if event_name == "proactive" or payload.get("event") == "proactive":
                            dispatch_event(payload)
                    except Exception as exc:
                        sys.stderr.write(f"[oit-sidecar] event error: {exc}\n")
                event_name = ""
                data_lines = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())


def main():
    while True:
        cfg = load_bridge()
        if not cfg:
            time.sleep(3.0)
            continue
        try:
            consume_sse(cfg)
        except Exception as exc:
            sys.stderr.write(f"[oit-sidecar] reconnect after error: {exc}\n")
            time.sleep(3.0)


if __name__ == "__main__":
    main()
