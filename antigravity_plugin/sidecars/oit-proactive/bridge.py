# -*- coding: utf-8 -*-
"""Antigravity Sidecar: subscribes to OIT Bridge SSE stream and wakes conversations via agentapi."""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
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


_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_TURN_CIDS = {}
_TURN_LOCK = threading.Lock()


def _run_agentapi(args: list) -> str:
    exe = _find_agentapi()
    if not exe:
        _log(f"agentapi not found in PATH={os.environ.get('PATH', '')[:500]}")
        return ""
    is_batch = os.name == "nt" and exe.lower().endswith((".cmd", ".bat"))
    clean = [re.sub(r"[\r\n]+", " ", str(a)).strip() if is_batch else str(a) for a in args[1:]]
    cmd = (["cmd.exe", "/c", exe] if is_batch else [exe]) + clean
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        out = (proc.stdout or "").strip()
        if proc.returncode != 0:
            _log(f"agentapi failed rc={proc.returncode} exe={exe} args={args[:3]} err={(proc.stderr or out).strip()[:300]}")
            return ""
        _log(f"agentapi ok exe={exe} args={args[:3]}")
        return out
    except Exception as exc:
        _log(f"agentapi exception exe={exe}: {exc}")
        return ""


def _conversation_rpc(method: str, payload: dict) -> bool:
    addr = str(os.environ.get("ANTIGRAVITY_LS_ADDRESS") or "").strip()
    csrf = str(os.environ.get("ANTIGRAVITY_CSRF_TOKEN") or "").strip()
    if not addr or not csrf:
        _log(f"skip {method}: ANTIGRAVITY_LS_ADDRESS/CSRF unavailable")
        return False
    url = f"http://{addr}/product.v1.ConversationService/{method}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "x-antigravity-csrf-token": csrf,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
        return True
    except Exception as exc:
        _log(f"{method} failed: {exc}")
        return False


def _archive_conversation(cid: str) -> bool:
    """通过 Antigravity ConversationService 将旧对话归档（静默降级，不阻塞正常消息）。"""
    cid = str(cid or "").strip()
    if not cid:
        return False
    ok = _conversation_rpc("UpdateConversation", {
        "conversationId": cid,
        "annotations": {"archived": True},
    })
    if ok:
        _log(f"archived conversation cid={cid}")
    return ok


def _cleanup_ephemeral_conversation(cid: str, delay_s: float = 6.0):
    """单轮回复完成后延迟删除临时对话；若删除失败则退回归档，保持会话列表干净。"""
    cid = str(cid or "").strip()
    if not cid:
        return

    def work():
        if delay_s > 0:
            time.sleep(delay_s)
        if _conversation_rpc("DeleteConversation", {"conversationId": cid}):
            _log(f"deleted ephemeral conversation cid={cid}")
        else:
            _archive_conversation(cid)

    threading.Thread(target=work, daemon=True).start()


def _archive_conversation_async(cid: str):
    if not cid:
        return
    threading.Thread(target=_archive_conversation, args=(cid,), daemon=True).start()


def _spawn_stateless_turn(turn_id: str, persona_name: str, kind: str, model: str = ""):
    who = persona_name or "OIT"
    if kind == "memory_compact":
        prompt = (
            f"[OIT单轮请求 turn_id={turn_id}] "
            f"请先调用 oit_get_turn_context(turn_id={turn_id!r}) 获取记忆整理任务与近期完整对话，"
            f"按指示调用 oit-memory-compact 子代理并通过 oit_memory_save_auto(text=..., turn_id={turn_id!r}) 保存；"
            "不要调用 oit_reply，完成后仅输出 ✓。"
        )
    else:
        prompt = (
            f"[OIT单轮请求 turn_id={turn_id}] "
            f"请先调用 oit_get_turn_context(turn_id={turn_id!r}) 获取本轮完整人设、记忆卡、近期完整对话记录与动态状态，"
            f"严格按人设与规则处理，最后调用 oit_reply(turn_id={turn_id!r}, ...) 提交回复，完成后仅输出 ✓ 结束。"
        )
    cmd = ["agentapi", "new-conversation", f"--title=OIT · {who}"]
    if model:
        cmd.append(f"--model={model}")
    cmd += ["--", prompt]
    out = _run_agentapi(cmd)
    m = _UUID_RE.search(out or "")
    if m:
        created_cid = m.group(0)
        with _TURN_LOCK:
            _TURN_CIDS[turn_id] = created_cid
        _log(f"stateless turn {turn_id} -> cid={created_cid}")


def dispatch_event(event: dict):
    ev_type = str(event.get("event") or "").strip()
    cid = str(event.get("conversation_id") or "").strip()
    turn_id = str(event.get("turn_id") or "").strip()
    kind = str(event.get("kind") or ev_type or "proactive")
    persona_name = str(event.get("persona_name") or "").strip()
    model = str(event.get("model") or "").strip()
    archive_cid = str(event.get("archive_conversation_id") or "").strip()
    if archive_cid:
        _archive_conversation_async(archive_cid)
    if ev_type == "turn_done" or kind == "turn_done":
        tracked = ""
        if turn_id:
            with _TURN_LOCK:
                tracked = _TURN_CIDS.pop(turn_id, "")
        target_cid = cid or tracked
        if target_cid:
            _cleanup_ephemeral_conversation(target_cid)
        return
    _log(f"dispatch kind={kind} turn={turn_id or '-'} cid={cid or '(new)'} persona={persona_name or '?'}")
    if turn_id and event.get("stateless"):
        _spawn_stateless_turn(turn_id, persona_name, kind, model=model)
        return
    if kind == "rollover":
        who = persona_name or "当前 OIT 人设"
        if event.get("compact_memory"):
            prompt = (
                f"这是 OIT 人设「{who}」自动轮换后的新会话。请先调用 oit_bind_persona"
                f"（persona_name={who!r}, force=true）将本对话与该人设 1 对 1 绑定，"
                "再调用 oit_get_turn_context 载入人设、记忆卡与近期对话上下文，并按其中的记忆整理提示"
                "调用 oit-memory-compact 子代理整理保存长期记忆。"
                "完成后不要调用 oit_reply 给用户发消息，直接输出 ✓ 结束本轮即可。"
            )
        else:
            prompt = (
                f"这是 OIT 人设「{who}」自动轮换后的新会话。请调用 oit_bind_persona"
                f"（persona_name={who!r}, force=true）将本对话与该人设 1 对 1 绑定。"
                "完成绑定后不要调用 oit_reply 给用户发消息，直接输出 ✓ 结束本轮即可。"
            )
        _run_agentapi(["agentapi", "new-conversation", "--", prompt])
        return
    if kind == "user_message":
        text = str(event.get("text") or "").strip()
        image_path = str(event.get("image_path") or "").strip()
        if not text:
            return
        if image_path:
            text += f" [附件图片: {image_path}]（请先调用 read_file 查看该图片；若涉及学科题目并调用 oit-subject 子代理，必须把该图片路径原样传入子代理 Prompt 让其亲自 read_file 查看原图，最后用 oit_reply 返回含推导过程的回复）"
        if cid:
            _run_agentapi(["agentapi", "send-message", cid, text])
        else:
            who = persona_name or "当前 OIT 人设"
            prompt = (
                f"这是 OIT 人设「{who}」的新对话首条消息。请先调用 oit_bind_persona"
                f"（persona_name={who!r}, force=true）将本对话与该人设 1 对 1 绑定，"
                f"再调用 oit_get_turn_context 获取本轮状态，最后用 oit_reply 回复以下用户消息：{text}"
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


def _exit_if_updated(boot_mtime: float):
    if not boot_mtime:
        return
    try:
        if os.path.getmtime(__file__) != boot_mtime:
            _log("bridge.py updated on disk; exiting for clean auto-restart")
            sys.exit(0)
    except OSError:
        pass


def consume_sse(cfg: dict, boot_mtime: float):
    url = f"http://127.0.0.1:{cfg['port']}/events/stream"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {cfg['token']}"})
    _log(f"connecting SSE port={cfg['port']}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        _log("SSE connected")
        event_name = ""
        data_lines = []
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                if data_lines:
                    try:
                        payload = json.loads("\n".join(data_lines))
                        if event_name in ("proactive", "user_message", "rollover", "memory_compact", "turn_done") or payload.get("event") in ("proactive", "user_message", "rollover", "memory_compact", "turn_done"):
                            dispatch_event(payload)
                    except Exception as exc:
                        _log(f"event error: {exc}")
                event_name = ""
                data_lines = []
                _exit_if_updated(boot_mtime)
                continue
            if line.startswith(":"):
                if not data_lines and not event_name:
                    _exit_if_updated(boot_mtime)
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
        _exit_if_updated(boot_mtime)
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
