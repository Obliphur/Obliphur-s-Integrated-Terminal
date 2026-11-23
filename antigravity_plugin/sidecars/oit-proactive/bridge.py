# -*- coding: utf-8 -*-
"""Antigravity Sidecar: subscribes to OIT Bridge SSE stream and wakes conversations via agentapi."""

import json
import os
import shutil
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


def _log(msg: str):
    sys.stderr.write(f"[oit-sidecar] {msg}\n")
    sys.stderr.flush()


def _find_agentapi() -> str:
    for name in ("agentapi", "agentapi.exe", "agentapi.cmd", "agentapi.bat"):
        found = shutil.which(name)
        if found:
            return found
    for env_key in ("AGENTAPI_PATH", "ANTIGRAVITY_AGENTAPI"):
        val = os.environ.get(env_key, "").strip()
        if val and os.path.isfile(val):
            return val
    return ""


def _run_agentapi(args: list):
    exe = _find_agentapi()
    if not exe:
        _log(f"agentapi not found in PATH={os.environ.get('PATH', '')[:500]}")
        return
    cmd = [exe] + list(args[1:])
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        cmd = ["cmd.exe", "/c"] + cmd
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        if proc.returncode != 0:
            _log(f"agentapi failed rc={proc.returncode} exe={exe} args={args[:3]} err={(proc.stderr or proc.stdout).strip()[:300]}")
        else:
            _log(f"agentapi ok exe={exe} args={args[:3]}")
    except Exception as exc:
        _log(f"agentapi exception exe={exe}: {exc}")


def dispatch_event(event: dict):
    cid = str(event.get("conversation_id") or "").strip()
    kind = str(event.get("kind") or event.get("event") or "proactive")
    persona_name = str(event.get("persona_name") or "").strip()
    _log(f"dispatch kind={kind} cid={cid or '(new)'} persona={persona_name or '?'}")
    if kind == "user_message":
        text = str(event.get("text") or "").strip()
        if not text:
            return
        if cid:
            _run_agentapi(["agentapi", "send-message", cid, text])
        else:
            who = persona_name or "当前 OIT 人设"
            prompt = (
                f"这是 OIT 人设「{who}」的新对话首条消息。请先调用 oit_bind_persona"
                f"（persona_name={who!r}, force=true）将本对话与该人设 1 对 1 绑定，"
                f"再调用 oit_get_turn_context 获取本轮状态，最后用 oit_reply 回复以下用户消息：\n{text}"
            )
            _run_agentapi(["agentapi", "new-conversation", "--", prompt])
        return
    display_time = str(event.get("display_time") or "...").strip()
    if kind == "memory_compact":
        _run_agentapi([
            "agentapi", "new-conversation", "--",
            "请使用 oit-memory-compact 子代理整理当前 OIT 记忆卡并通过 oit_memory_save_auto 写回。",
        ])
        return
    if cid:
        prompt = f"[OIT {display_time}] 收到 OIT 主动对话/追问触发（kind={kind}），请先调用 oit_get_turn_context 获取本轮触发详情，再用 oit_reply 回复。"
        _run_agentapi(["agentapi", "send-message", cid, prompt])
    else:
        who = persona_name or "当前 OIT 人设"
        prompt = (
            f"[OIT {display_time}] 收到 OIT 人设「{who}」的主动对话触发（kind={kind}）。"
            f"请先调用 oit_bind_persona（persona_name={who!r}, force=true）将本对话与该人设绑定，"
            f"再调用 oit_get_turn_context 获取本轮触发详情，并用 oit_reply 回复。"
        )
        _run_agentapi(["agentapi", "new-conversation", "--", prompt])


def consume_sse(cfg: dict, boot_mtime: float):
    url = f"http://127.0.0.1:{cfg['port']}/events/stream"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {cfg['token']}"})
    _log(f"connecting SSE port={cfg['port']}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        _log("SSE connected")
        event_name = ""
        data_lines = []
        for raw in resp:
            try:
                if os.path.getmtime(__file__) != boot_mtime:
                    _log("bridge.py updated on disk; exiting for clean auto-restart")
                    sys.exit(0)
            except OSError:
                pass
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                if data_lines:
                    try:
                        payload = json.loads("\n".join(data_lines))
                        if event_name in ("proactive", "user_message") or payload.get("event") in ("proactive", "user_message"):
                            dispatch_event(payload)
                    except Exception as exc:
                        _log(f"event error: {exc}")
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
    try:
        boot_mtime = os.path.getmtime(__file__)
    except OSError:
        boot_mtime = 0.0
    while True:
        try:
            if boot_mtime and os.path.getmtime(__file__) != boot_mtime:
                _log("bridge.py updated on disk; exiting for clean auto-restart")
                sys.exit(0)
        except OSError:
            pass
        cfg = load_bridge()
        if not cfg:
            time.sleep(3.0)
            continue
        try:
            consume_sse(cfg, boot_mtime)
        except Exception as exc:
            _log(f"reconnect after error: {exc}")
            time.sleep(3.0)


if __name__ == "__main__":
    main()
