"""Claude Code 本地 CLI 适配器.

CLI 会话只保存会话 id；OIT 的可见聊天仍由 comm 自己存档。每次调用只把本轮
最后一条用户消息交给 Claude Code，历史由 CLI 会话负责保留。
"""

import json
import os
import shutil
import subprocess
import time
import threading

from .ai_common import AIError


_HELP = {}
_HELP_LOCK = threading.Lock()


def command_path(profile: dict) -> str:
    """返回可执行文件路径；配置里的 command 只允许是命令名或绝对路径。"""
    command = str(profile.get("command") or "claude").strip()
    if not command or any(ch in command for ch in "\r\n\"'"):
        return ""
    return shutil.which(command) or (command if os.path.isabs(command) and os.path.isfile(command) else "")


def installed(profile: dict = None) -> bool:
    return bool(command_path(profile or {"command": "claude"}))


def capabilities(profile: dict = None) -> dict:
    """读取本机 CLI 的公开帮助，只判断参数是否存在，不探测账户额度或隐藏模型。"""
    path = command_path(profile or {"command": "claude"})
    if not path:
        return {}
    with _HELP_LOCK:
        if path in _HELP:
            return dict(_HELP[path])
        try:
            p = subprocess.run([path, "--help"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=8,
                               stdin=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            text = (p.stdout or "") + "\n" + (p.stderr or "")
        except (OSError, subprocess.TimeoutExpired):
            text = ""
        flags = {"effort": "--effort" in text, "model": "--model" in text}
        _HELP[path] = flags
        return dict(flags)


def _error(text: str) -> AIError:
    msg = (text or "").strip().replace("\x00", "")
    low = msg.lower()
    if any(x in low for x in ("login", "authenticate", "auth", "subscription")):
        return AIError("auth", "Claude Code 尚未登录，请先在终端运行 claude 完成登录.")
    return AIError("network", "Claude Code 调用失败" + (": " + msg[-300:] if msg else "."))


def chat_messages(profile: dict, system: str, messages: list, timeout: float = 240.0,
                  max_tokens: int = 4096, session_store=None, session_key: str = "",
                  **_kwargs) -> str:
    """以 print/json 模式调用 Claude Code，并在 session_store 中续接会话。"""
    path = command_path(profile)
    if not path:
        raise AIError("config", "没有找到 Claude Code，请先安装并登录.")
    if not messages:
        raise AIError("format", "没有要发送的消息")
    store = session_store if isinstance(session_store, dict) else {}
    key = str(session_key or profile.get("config_id") or "default")
    state = store.get(key) if isinstance(store.get(key), dict) else {}
    sid = str(state.get("id") or "")
    # 首次建立会话时用 OIT 的干净历史重建；之后历史留在 Claude Code 会话里。
    if sid:
        prompt = str(messages[-1].get("content") or "").strip()
    else:
        lines = []
        for message in messages:
            role = "用户" if message.get("role") == "user" else "对方"
            content = str(message.get("content") or "").strip()
            if content:
                lines.append(f"{role}: {content}")
        prompt = "\n\n".join(lines)
    if not prompt:
        raise AIError("format", "没有要发送的消息")
    args = [path, "-p", prompt, "--output-format", "json"]
    caps = capabilities(profile)
    requested_effort = str(profile.get("effort") or "auto").lower()
    if requested_effort not in ("low", "medium", "high", "max"):
        requested_effort = "medium" if _kwargs.get("think") else "low"
    if caps.get("effort"):
        args[1:1] = ["--effort", requested_effort]
    model = str(profile.get("model") or "").strip()
    if caps.get("model") and model and model not in ("订阅模型", "subscription", "auto"):
        args[1:1] = ["--model", model]
    if sid:
        args[1:1] = ["--resume", sid]
    if system and not sid:
        args[1:1] = ["--system-prompt", system]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=max(10.0, float(timeout)),
                              stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        raise AIError("config", "没有找到 Claude Code，请先安装并登录.")
    except subprocess.TimeoutExpired:
        raise AIError("network", "Claude Code 响应超时.")
    if proc.returncode:
        # CLI 失败后丢弃会话 id；下一轮由 OIT 回退或新建会话。
        store.pop(key, None)
        raise _error(proc.stderr or proc.stdout)
    raw = (proc.stdout or "").strip()
    data = None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        # 某些版本即使要求 json 仍可能只输出正文。
        if raw:
            return raw
    if not isinstance(data, dict):
        raise AIError("format", "Claude Code 返回了无法识别的内容.")
    if data.get("is_error") or data.get("error"):
        raise _error(str(data.get("error") or data.get("result") or ""))
    result = str(data.get("result") or data.get("text") or "").strip()
    if not result:
        raise AIError("format", "Claude Code 没有返回内容.")
    new_id = str(data.get("session_id") or data.get("sessionId") or sid or "")
    if new_id:
        store[key] = {"id": new_id, "updated": int(time.time())}
    return result

