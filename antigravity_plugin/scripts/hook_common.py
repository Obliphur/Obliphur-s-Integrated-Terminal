# -*- coding: utf-8 -*-
"""Shared helper for OIT Antigravity lifecycle hooks."""

import json
import os
import sys
import urllib.parse
import urllib.request


def read_stdin_json() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def write_stdout_json(obj: dict):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()


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


def bridge_get(path: str, query: dict = None, timeout: float = 5.0) -> dict:
    cfg = load_bridge()
    if not cfg:
        return {}
    url = f"http://127.0.0.1:{cfg['port']}{path}"
    if query:
        qs = urllib.parse.urlencode({k: v for k, v in query.items() if v is not None and v != ""})
        if qs:
            url += "?" + qs
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {cfg['token']}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def bridge_post(path: str, body: dict, timeout: float = 5.0) -> dict:
    cfg = load_bridge()
    if not cfg:
        return {}
    url = f"http://127.0.0.1:{cfg['port']}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {cfg['token']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))
