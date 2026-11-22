# -*- coding: utf-8 -*-
"""发请求: 三种协议的报文 / 重试与错误 / 用量 / 思考 / 图片 / 模型列表. 依赖 ai_common, ai_config, ai_web."""

import base64
import json
import os
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.parse
import urllib.request

import securefile
from ai_assist.ai_common import *
from ai_assist.ai_config import cache_key, check_profile, family_of, is_deepseek, think_on_field
from ai_assist.ai_web import web_mode, web_search

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟随重定向: urllib 默认会把 Authorization / x-api-key 一起带到新地址."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code,
                                     "redirect blocked to protect the API key", headers, fp)


class _Fresh:
    """每次请求都新建 opener: build_opener 里的 ProxyHandler 在创建的那一刻读系统代理, 做成模块级常量的话 OIT 启动时是什么代理就一直用什么 ——
    之后系统代理变了 (OIT 的过滤代理换了端口 / 开发版放开限制把系统代理还原 / 换了代理软件) 就一直连旧端口, 报 WinError 10061 (积极拒绝)."""

    def open(self, req, timeout=None):
        return urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout)


_OPENER = _Fresh()


_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)       # 不走系统代理

STREAM_REJECT = set()          # (服务商, API 地址): 不支持流式响应时退回完整 JSON


_THINK_TAGS = r"(?:think|thinking|thought)"


_THINK_BLOCK_RE = re.compile(r"<(" + _THINK_TAGS + r")\b[^>]*>.*?</\1\s*>", re.S | re.I)


_THINK_CLOSE_RE = re.compile(r"</" + _THINK_TAGS + r"\s*>", re.I)


_THINK_OPEN_RE = re.compile(r"<" + _THINK_TAGS + r"\b[^>]*>", re.I)


MODEL_CACHE = {}                # (服务商, Base URL) -> 取到过的模型 id 列表 (只在内存里)

# 有的 Gemini 中转把无标签的分析混进 text, 用明确边界提取正文, 不猜测语言或段落。
_GEMINI_FINAL_RULE = ("\n输出格式:将本次唯一的正式回复(包括所需控制标记、JSON等)完整放在"
                      "<oit_final>和</oit_final>之间;思考、分析、计划和模拟的后续回复不得放在其中."
                      "OIT会移除这层标签后使用正文;后续API调用由OIT单独发起.")


def redact(text: str, key: str) -> str:
    """把错误信息里出现的 Key 抹掉 (有的服务商会在报错里回显 Key)."""
    key = (key or "").strip()
    if key and len(key) >= 6 and text:
        text = text.replace(key, "***")
    return text


def _open_url(req, timeout, opener=None):
    """通过当前系统代理发送请求；代理不可用时失败关闭，避免绕过 OIT 过滤规则."""
    if opener is not None:
        return opener(req, timeout=timeout)
    return _OPENER.open(req, timeout=timeout)


def proxy_hint() -> str:
    """现在请求会用的系统代理 (host:port, 不含账号); 没有 = "direct". 连接失败时记进日志, 方便排查."""
    try:
        from urllib.parse import urlsplit
        proxies = urllib.request.getproxies()
        p = proxies.get("https") or proxies.get("http") or ""
        return urlsplit(p if "//" in p else "//" + p).netloc.rpartition("@")[2] or "direct" if p else "direct"
    except Exception:       # noqa: BLE001
        return "?"


def _openai_root(profile: dict) -> str:
    """OpenAI 兼容接口只填域名时补 /v1; 显式路径与 DeepSeek 官方根地址保持原样."""
    base = profile["base_url"].strip().rstrip("/")
    if not urllib.parse.urlsplit(base).path and not is_deepseek(profile):
        return base + "/v1"
    return base


def endpoint(profile: dict) -> str:
    base = profile["base_url"].strip().rstrip("/")
    if profile.get("protocol") == "anthropic":
        return base + ("/messages" if base.endswith("/v1") else "/v1/messages")
    if profile.get("protocol") == "gemini":                 # 原生 Gemini: /v1beta/models/<model>:generateContent
        root = base if base.endswith(("/v1beta", "/v1")) else base + "/v1beta"
        model = str(profile.get("model") or "").strip()
        if model.startswith("models/"):
            model = model[len("models/"):]
        return f"{root}/models/{urllib.parse.quote(model, safe='')}:generateContent"
    return _openai_root(profile) + "/chat/completions"


def _error_text(body: bytes) -> str:
    try:
        data = json.loads(body.decode("utf-8", "replace"))
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            return str(err.get("message") or err)[:300]
        if err:
            return str(err)[:300]
    except ValueError:
        pass
    return body.decode("utf-8", "replace")[:300]


def chat(profile: dict, system: str, user: str, timeout: float = 90.0, opener=None) -> str:
    """向模型发一次请求,返回文本.失败抛 AIError."""
    return chat_messages(profile, system, [{"role": "user", "content": user}],
                         timeout=timeout, opener=opener)


def usage_of(data: dict) -> tuple:
    """响应里的用量 → (输入 token 总数, 其中命中缓存的). 各家字段不同: Anthropic cache_read_input_tokens (输入 = input + 读缓存 + 写缓存),
    Gemini usageMetadata.promptTokenCount / cachedContentTokenCount, DeepSeek prompt_cache_hit_tokens,
    OpenAI prompt_tokens_details.cached_tokens; 没有就是 0."""

    def num(v):
        return int(v) if isinstance(v, (int, float)) else 0
    if not isinstance(data, dict):
        return 0, 0
    meta = data.get("usageMetadata")                        # 原生 Gemini: 没有 usage 字段
    if isinstance(meta, dict):
        return num(meta.get("promptTokenCount")), num(meta.get("cachedContentTokenCount"))
    u = data.get("usage")
    if not isinstance(u, dict):
        return 0, 0
    if "input_tokens" in u:
        read = num(u.get("cache_read_input_tokens"))
        return num(u.get("input_tokens")) + read + num(u.get("cache_creation_input_tokens")), read
    det = u.get("prompt_tokens_details")
    cached = num(u.get("prompt_cache_hit_tokens")) or num(det.get("cached_tokens") if isinstance(det, dict) else 0)
    return num(u.get("prompt_tokens")), cached


def _gemini_parts(content) -> list:
    """一条消息的内容 → Gemini 的 parts. 文字包成 {"text"}; 图片 (OCR) 已经是 parts 形状, 原样带过去."""
    if isinstance(content, list):
        return content
    return [{"text": str(content)}]


def _gemini_thinking_level(profile: dict, think_on: bool):
    """Gemini 4 的思考档位沿用沟通模块的旧规则；旧版 Gemini 不发送该字段。"""
    model = str(profile.get("model") or "").lower()
    if not re.search(r"gemini[-_ /]*4(?:\D|$)", model):
        return None
    if not think_on:
        return "low"
    if "carbon" in model:
        return "medium"
    if "neon" in model or "hydrogen" in model:
        return "high"
    return "medium"


def _gemini_body(profile: dict, system: str, messages: list, max_tokens: int, temperature: float,
                 think_on: bool = False) -> dict:
    """原生 Gemini (generateContent). 隐式缓存由 Google 按完全相同的 systemInstruction 建,
    请求里不用标缓存位置; 所以 system 里不能放会变的东西 (动态内容由 comm 贴到最后一条用户消息)."""
    contents = [{"role": "model" if m.get("role") == "assistant" else "user",
                 "parts": _gemini_parts(m.get("content"))} for m in messages]
    generation = {"maxOutputTokens": max_tokens, "temperature": temperature}
    thinking_level = _gemini_thinking_level(profile, think_on)
    if thinking_level:
        generation["thinkingConfig"] = {"thinkingLevel": thinking_level}
    body = {"contents": contents, "generationConfig": generation}
    body["systemInstruction"] = {"parts": [{"text": (system or "") + _GEMINI_FINAL_RULE}]}
    return body


def _build_body(profile: dict, system: str, messages: list, max_tokens: int, temperature: float,
                cache: bool, think_off: bool = True, think_on: bool = False) -> dict:
    """请求体 + 这个模型自己的策略. 每家的思考 / 缓存字段都只出现在自己那一支里:
    Anthropic 只认 cache_control 和 thinking; OpenAI 不认 thinking 字段 (带了可能 400), 缓存用 prompt_cache_key;
    Gemini 用原生 generateContent, 隐式缓存不用在请求里标; DeepSeek / 中转才要 thinking 开关."""
    fam = family_of(profile)
    if profile["protocol"] == "gemini":
        return _gemini_body(profile, system, messages, max_tokens, temperature, think_on)
    if profile["protocol"] == "anthropic":
        sys_field, msgs = system, list(messages)
        if cache:
            mark = {"type": "ephemeral"}
            sys_field = [{"type": "text", "text": system, "cache_control": mark}]
            if msgs:
                content = msgs[-1].get("content")
                if isinstance(content, list):
                    content = [dict(part, cache_control=mark) if part.get("type") == "text" else part
                               for part in content]
                else:
                    content = [{"type": "text", "text": str(content), "cache_control": mark}]
                msgs[-1] = {"role": msgs[-1]["role"], "content": content}
        body = {"model": profile["model"], "max_tokens": max_tokens, "system": sys_field, "messages": msgs}
        if think_on:                                        # 开思考 (OIT 的规则判断需要时才开, comm.think_reason)
            if is_deepseek(profile):
                body["reasoning"] = {"effort": "medium"}
            else:
                body["thinking"] = think_on_field(profile)   # enabled 要求 max_tokens 大于 budget_tokens; 都不能带 temperature
        elif is_deepseek(profile):
            body["reasoning"] = {"effort": "none"}          # Anthropic 格式: none = 关思考
        # 关思考: 不写 thinking. 新版 Claude 不认 {"type": "disabled"}, 带上会 400 (1.0.427)
        return body
    body = {"model": profile["model"], "max_tokens": max_tokens, "temperature": temperature,
            "messages": [{"role": "system", "content": system}] + list(messages)}
    if fam in ("chatgpt", "gemini"):                        # Gemini 的 OpenAI 中转也不发送其他模型的 thinking 字段
        if cache and fam == "chatgpt":
            body["prompt_cache_key"] = cache_key(system, profile.get("model"))
        return body
    if fam == "deepseek":                                   # 官方地址: 用自己的思考开关
        if think_on:
            body.pop("temperature", None)                    # 思考模式下 temperature 不起作用
        body["thinking"] = {"type": "enabled"} if think_on else {"type": "disabled"}
        return body
    # other: 中转 / 本地模型: 默认关思考, 不认就 400 后去掉重试 (chat_messages)
    if think_on:
        body.pop("temperature", None)
        body["thinking"] = think_on_field(profile)
    elif think_off:
        body["thinking"] = {"type": "disabled"}
    return body


def chat_messages(profile: dict, system: str, messages: list, timeout: float = 90.0, opener=None,
                  max_tokens: int = MAX_TOKENS, temperature: float = 0.2, cache: bool = False, on_usage=None,
                  think: bool = False, web: bool = False, tools: bool = False, on_delta=None) -> str:
    """默认关思考: OpenAI 兼容的中转会带 thinking: {"type": "disabled"}; 服务商不认这个字段回 400 时, 去掉它重试一次,
    并记住这个地址 (THINK_REJECT), 以后不再带. DeepSeek 官方地址一直带 (它认).
    Anthropic 协议关思考时不写 thinking (新版 Claude 不认 disabled). 中转不认 cache_control 回 400 时, 去掉缓存标记
    重试一次, 并记住这个地址 (CACHE_REJECT).
    think=True: 这一次开思考 (沟通里规则判断出需要时, comm.think_reason); 开思考的字段回 400 时改回不开思考重试, 并记住这个地址 (THINK_ON_REJECT).
    web=True: 这一次开着网络 (沟通 规则 XIV 的限时网络, 方式见 web_mode); 服务商的接口不认搜索 (400 / 404 / 405 / 422) 时, 不联网再答一次, 并在系统提示里说明没能联网."""
    if tools:
        from .ai_tools import tool_chat
        return tool_chat(profile, system, messages, timeout, opener, max_tokens, temperature, cache, on_usage, think, web, on_delta)
    if web and profile.get("base_url"):
        try:
            return _web_chat(profile, system, messages, timeout, opener, max_tokens, temperature, cache, on_usage)
        except AIError as e:
            if not (e.kind == "http" and e.message.startswith(WEB_UNSUPPORTED)):
                raise
            system = system + WEB_FAIL_NOTE
    if think and max_tokens > THINK_BUDGET and profile.get("base_url") and endpoint(profile) not in THINK_ON_REJECT:
        try:
            return _chat_once(profile, system, messages, timeout, opener, max_tokens, temperature, cache, on_usage, False, True, on_delta)
        except AIError as e:
            if not (e.kind == "http" and e.message.startswith("服务商返回 400")):
                raise
            THINK_ON_REJECT.add(endpoint(profile))
    tkey = endpoint(profile) if profile.get("base_url") else ""
    if tkey and tkey not in THINK_REJECT:
        try:
            return _chat_once(profile, system, messages, timeout, opener, max_tokens, temperature, cache, on_usage, True, on_delta=on_delta)
        except AIError as e:
            if not (e.kind == "http" and e.message.startswith("服务商返回 400") and not is_deepseek(profile)):
                raise
            THINK_REJECT.add(tkey)
    return _chat_once(profile, system, messages, timeout, opener, max_tokens, temperature, cache, on_usage, False, on_delta=on_delta)


def strip_think(text: str) -> str:
    """去掉混进正文的思考内容: 有的中转 / 本地模型把思考写在正文里 (<think>…</think>), 不能让它出现在聊天 / 记忆卡里.
    成对的整段删掉; 只有结尾标签 (开头标签被服务商吃掉了) 就删掉它和它前面的所有字; 只有开头标签 (思考到一半被截断) 就从它开始全删."""
    if "<" not in text:
        return text
    t = _THINK_BLOCK_RE.sub("", text)
    m = _THINK_CLOSE_RE.search(t)
    if m:
        t = t[m.end():]
    m = _THINK_OPEN_RE.search(t)
    if m:
        t = t[:m.start()]
    return t.strip() if t != text else text


def _gemini_text(data: dict) -> str:
    """跳过 thought 部分, 从剩余 text 中提取最后一个完整的正式回复块."""
    cands = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(cands, list) or not cands or not isinstance(cands[0], dict):
        fb = data.get("promptFeedback") if isinstance(data, dict) else None
        reason = fb.get("blockReason") if isinstance(fb, dict) else ""
        raise AIError("format", "Gemini 没有返回内容" + (f" (被拦截: {reason})" if reason else ""))
    content = cands[0].get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        return ""
    text = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict) and not p.get("thought"))
    # 最后一个完整正文块才是答案; 缺失或截断时拒绝展示, 防止退回未隔离的分析。
    blocks = re.findall(r"<oit_final>(.*?)</oit_final>", text, re.S)
    if not blocks:
        raise AIError("format", "模型没有返回内容")
    return blocks[-1].strip()


def _chat_once(profile: dict, system: str, messages: list, timeout: float, opener, max_tokens: int,
               temperature: float, cache: bool, on_usage, think_off: bool, think_on: bool = False,
               on_delta=None) -> str:
    """带历史的一次请求: messages = [{"role": "user" | "assistant", "content": 文字}], 以 user 开头, 以 user 结尾.
    API 本身没有记忆, 想让模型记得前面说过的话, 就得每次把历史一起发过去 (沟通页, comm.py).
    cache: 提示缓存 (后续对话及工具往返可复用前面的历史). 标缓存位置 / 带缓存键的只有
    Anthropic (cache_control) 和 ChatGPT (prompt_cache_key); Gemini 靠相同的 systemInstruction 自动命中, 别家不假设有缓存.
    on_usage(输入 token 数, 命中缓存的 token 数): 响应里有用量时调用."""
    if cache and endpoint(profile) in CACHE_REJECT:          # 这个地址以前就不认 cache_control: 这次直接不标
        cache = False
    body = _build_body(profile, system, messages, max_tokens, temperature, cache, think_off, think_on)
    stream = bool(on_delta and endpoint(profile) not in STREAM_REJECT)
    while True:
        try:
            data = _send_stream(profile, body, timeout, opener, on_delta) if stream else _send(profile, body, timeout, opener)
            break
        except AIError as e:
            if e.kind != "http" or not e.message.startswith("服务商返回 400"):
                raise
            if stream:
                stream = False
                STREAM_REJECT.add(endpoint(profile))
            elif cache:
                cache = False
                CACHE_REJECT.add(endpoint(profile))
                body = _build_body(profile, system, messages, max_tokens, temperature, False, think_off, think_on)
            else:
                raise
    if profile["protocol"] == "anthropic":
        try:
            text = "".join(b.get("text", "") for b in data.get("content", [])
                           if isinstance(b, dict) and b.get("type") == "text")
        except (KeyError, IndexError, TypeError, AttributeError):
            raise AIError("format", "服务商返回了无法识别的内容")
    elif profile["protocol"] == "gemini":
        text = _gemini_text(data)
    else:
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError, AttributeError):
            raise AIError("format", "服务商返回了无法识别的内容")
    if on_usage:
        try:
            on_usage(*usage_of(data))
        except Exception:       # noqa: BLE001 —— 用量只是记录, 出错不影响回复
            pass
    text = strip_think(text)
    if not text.strip():
        raise AIError("format", "模型没有返回内容")
    return text


def _http_error(e, key: str):
    try:
        payload = e.read(4000) if e.fp is not None else b""
    except Exception:
        payload = b""
    if e.code == 403 and e.headers and e.headers.get("X-OIT-Reason"):
        raise AIError("blocked", "请求被本程序的过滤代理拦截(该服务商的域名不在白名单中)")
    if 300 <= e.code < 400:
        raise AIError("http", f"服务商要求跳转到别的地址({e.code}),为保护 API Key 已拒绝.请检查 Base URL 是否填对.")
    if e.code in (401, 403):
        raise AIError("auth", "API Key 无效或没有权限:" + redact(_error_text(payload), key))
    raise AIError("http", f"服务商返回 {e.code}:" + redact(_error_text(payload), key))


def _send(profile: dict, body: dict, timeout: float, opener, url: str = None) -> dict:
    """把请求体 POST 给服务商 (url 默认是聊天接口), 返回解析好的 JSON. 失败抛 AIError."""
    check_profile(profile)
    key = (profile.get("api_key") or "").strip()
    headers = {"Content-Type": "application/json", "User-Agent": "OIT/1.0"}
    if profile["protocol"] == "anthropic":
        headers["x-api-key"] = key
        headers["anthropic-version"] = ANTHROPIC_VERSION
    elif profile["protocol"] == "gemini":
        headers["x-goog-api-key"] = key                  # 原生 Gemini: Key 走请求头, 不放 URL
    elif key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url or endpoint(profile),
                                 data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with _open_url(req, timeout, opener) as resp:
            raw = resp.read(2_000_000)
    except urllib.error.HTTPError as e:
        _http_error(e, key)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise AIError("network", redact(f"连接失败:{getattr(e, 'reason', e)}", key))

    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise AIError("format", "服务商返回了无法识别的内容")
    if not isinstance(data, dict):
        raise AIError("format", "服务商返回了无法识别的内容")
    return data


def _partial_json_text(raw: str, sent: str) -> str:
    """从尚未结束的工具参数 JSON 中安全提取 oit_reply.text 的新增部分."""
    m = re.search(r'"text"\s*:\s*"((?:\\.|[^"\\])*)', raw, re.S)
    if not m:
        return ""
    src, out, i = m.group(1), [], 0
    escapes = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
    while i < len(src):
        ch = src[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(src):
            break
        esc = src[i + 1]
        if esc == "u":
            digits = src[i + 2:i + 6]
            if len(digits) != 4 or not re.fullmatch(r"[0-9a-fA-F]{4}", digits):
                break
            out.append(chr(int(digits, 16)))
            i += 6
        elif esc in escapes:
            out.append(escapes[esc])
            i += 2
        else:
            break
    if out and 0xD800 <= ord(out[-1]) <= 0xDBFF:
        out.pop()  # 等待下一片 JSON 的低代理项，避免把半个 emoji 交给界面
    text = "".join(out).encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
    return text[len(sent):] if text.startswith(sent) else ""


def _send_stream(profile: dict, body: dict, timeout: float, opener, on_delta, url: str = None) -> dict:
    """接收 SSE 并重建为原协议完整响应；只把正式回复正文增量交给预览回调."""
    check_profile(profile)
    protocol = profile["protocol"]
    request_body = dict(body)
    if protocol == "anthropic":
        request_body["stream"] = True
    elif protocol == "gemini":
        request_body.pop("stream", None)
        target = url or endpoint(profile)
        target = target.replace(":generateContent", ":streamGenerateContent")
        url = target + ("&" if "?" in target else "?") + "alt=sse"
    else:
        request_body["stream"] = True
    key = (profile.get("api_key") or "").strip()
    headers = {"Content-Type": "application/json", "User-Agent": "OIT/1.0", "Accept": "text/event-stream"}
    if protocol == "anthropic":
        headers["x-api-key"] = key
        headers["anthropic-version"] = ANTHROPIC_VERSION
    elif protocol == "gemini":
        headers["x-goog-api-key"] = key
    elif key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url or endpoint(profile), data=json.dumps(request_body).encode("utf-8"),
                                 headers=headers, method="POST")
    def emit(text):
        if text:
            try:
                on_delta(text)
            except Exception:
                pass
    plain_raw, plain_sent = "", ""
    def emit_plain(text):
        """普通文本先过滤思考及未收全的标签；Gemini 只预览正式回复块。"""
        nonlocal plain_raw, plain_sent
        plain_raw += text
        visible = plain_raw
        if protocol == "gemini":
            if "<oit_final>" not in visible:
                return
            visible = visible.split("<oit_final>", 1)[1].split("</oit_final>", 1)[0]
        tail = visible.rfind("<")
        if tail >= 0 and ">" not in visible[tail:]:
            visible = visible[:tail]
        visible = strip_think(visible)
        if visible.startswith(plain_sent):
            emit(visible[len(plain_sent):])
            plain_sent = visible
    try:
        with _open_url(req, timeout, opener) as resp:
            content_type = str(getattr(resp, "headers", {}).get("Content-Type", "")).split(";", 1)[0].strip().lower()
            if content_type == "application/json" or content_type.endswith("+json"):
                STREAM_REJECT.add(endpoint(profile))
                try:
                    data = json.loads(resp.read(2_000_000).decode("utf-8"))
                    if not isinstance(data, dict):
                        raise ValueError("响应必须是对象")
                    return data
                except (ValueError, UnicodeDecodeError) as e:
                    raise AIError("format", "服务商返回了无效的 JSON 响应") from e
            if protocol == "openai":
                message = {"role": "assistant", "content": "", "tool_calls": []}
                usage, finish = None, None
                complete = False
                tools = {}
                for line in resp:
                    line = line.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        complete = True
                        break
                    try:
                        chunk = json.loads(raw)
                    except ValueError:
                        raise AIError("format", "服务商返回了无效的流式数据")
                    if chunk.get("error"):
                        raise AIError("format", "服务商在流式响应中返回错误")
                    if isinstance(chunk.get("usage"), dict):
                        usage = chunk["usage"]
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if isinstance(content, str):
                        message["content"] += content
                        if not request_body.get("tools"):
                            emit_plain(content)
                    for part in delta.get("tool_calls") or []:
                        i = part.get("index", 0)
                        tool = tools.setdefault(i, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}, "_sent": ""})
                        if part.get("id"):
                            tool["id"] = part["id"]
                        fn = part.get("function") or {}
                        tool["function"]["name"] += fn.get("name") or ""
                        tool["function"]["arguments"] += fn.get("arguments") or ""
                        if tool["function"]["name"] == "oit_reply":
                            piece = _partial_json_text(tool["function"]["arguments"], tool["_sent"])
                            if piece:
                                tool["_sent"] += piece
                                emit(piece)
                    finish = choice.get("finish_reason") or finish
                if not complete and not finish:
                    raise AIError("network", "流式响应中断，回复尚未完成")
                if finish in ("length", "content_filter"):
                    raise AIError("format", "模型回复被截断或拦截")
                message["tool_calls"] = [{k: v for k, v in item.items() if not k.startswith("_")} for _, item in sorted(tools.items())]
                if not message["tool_calls"]:
                    message.pop("tool_calls")
                choice = {"message": message, "finish_reason": finish}
                data = {"choices": [choice]}
                if usage is not None:
                    data["usage"] = usage
                return data
            if protocol == "anthropic":
                blocks, usage, stop_reason = {}, {}, None
                complete = False
                event_name, data_lines = "", []
                def anth_event():
                    nonlocal usage, stop_reason, complete
                    if not data_lines:
                        return
                    try:
                        obj = json.loads("\n".join(data_lines))
                    except ValueError:
                        raise AIError("format", "服务商返回了无效的流式数据")
                    kind = obj.get("type") or event_name
                    if kind == "error":
                        raise AIError("format", "服务商在流式响应中返回错误")
                    if kind == "message_stop":
                        complete = True
                    if kind == "message_start":
                        usage = (obj.get("message") or {}).get("usage") or {}
                    elif kind == "content_block_start":
                        i, block = obj.get("index", 0), obj.get("content_block") or {}
                        blocks[i] = dict(block)
                        blocks[i]["_raw"] = ""
                        if block.get("type") == "text" and not request_body.get("tools"):
                            emit_plain(block.get("text") or "")
                    elif kind == "content_block_delta":
                        i, delta = obj.get("index", 0), obj.get("delta") or {}
                        block = blocks.setdefault(i, {"type": "text", "text": ""})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text") or ""
                            block["text"] = block.get("text", "") + text
                            if not request_body.get("tools"):
                                emit_plain(text)
                        elif delta.get("type") == "input_json_delta":
                            raw = block.get("_raw", "") + (delta.get("partial_json") or "")
                            block["_raw"] = raw
                            if block.get("name") == "oit_reply":
                                piece = _partial_json_text(raw, block.get("_sent", ""))
                                if piece:
                                    block["_sent"] = block.get("_sent", "") + piece
                                    emit(piece)
                    elif kind == "message_delta":
                        delta = obj.get("delta") or {}
                        stop_reason = delta.get("stop_reason") or stop_reason
                        usage.update(obj.get("usage") or {})
                for line in resp:
                    line = line.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif not line:
                        anth_event()
                        event_name, data_lines = "", []
                anth_event()
                if not complete and not stop_reason:
                    raise AIError("network", "流式响应中断，回复尚未完成")
                if stop_reason == "max_tokens":
                    raise AIError("format", "模型回复被截断或拦截")
                content = []
                for _, source in sorted(blocks.items()):
                    block = {k: v for k, v in source.items() if k not in ("_raw", "_sent")}
                    if block.get("type") == "tool_use":
                        try:
                            block["input"] = json.loads(source["_raw"]) if source.get("_raw") else block.get("input", {})
                        except ValueError:
                            raise AIError("format", "工具参数格式错误")
                    content.append(block)
                return {"content": content, "usage": usage, "stop_reason": stop_reason}
            candidates, usage = {}, None
            for line in resp:
                line = line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                if line[5:].strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(line[5:].strip())
                except ValueError:
                    raise AIError("format", "服务商返回了无效的流式数据")
                if chunk.get("error"):
                    raise AIError("format", "服务商在流式响应中返回错误")
                for cand in chunk.get("candidates") or []:
                    index = cand.get("index", 0)
                    saved = candidates.setdefault(index, {"content": {"role": "model", "parts": []}})
                    parts = saved["content"]["parts"]
                    for incoming in (cand.get("content") or {}).get("parts") or []:
                        if "text" in incoming:
                            if parts and "text" in parts[-1] and parts[-1].get("thought") == incoming.get("thought"):
                                parts[-1]["text"] += incoming["text"]
                            else:
                                parts.append(dict(incoming))
                            if not request_body.get("tools") and not incoming.get("thought"):
                                emit_plain(incoming["text"])
                        elif "functionCall" in incoming:
                            parts.append(dict(incoming))
                            call = incoming["functionCall"]
                            if call.get("name") == "oit_reply":
                                text = (call.get("args") or {}).get("text")
                                if isinstance(text, str):
                                    prev = saved.get("_sent", "")
                                    if text.startswith(prev):
                                        emit(text[len(prev):])
                                        saved["_sent"] = text
                    if cand.get("finishReason"):
                        saved["finishReason"] = cand["finishReason"]
                if isinstance(chunk.get("usageMetadata"), dict):
                    usage = chunk["usageMetadata"]
            data = {"candidates": [{k: v for k, v in c.items() if k != "_sent"} for _, c in sorted(candidates.items())]}
            if usage is not None:
                data["usageMetadata"] = usage
            if not data["candidates"]:
                raise AIError("format", "服务商没有返回流式内容")
            if any(not c.get("finishReason") for c in data["candidates"]):
                raise AIError("network", "流式响应中断，回复尚未完成")
            if any(c["finishReason"] != "STOP" for c in data["candidates"]):
                raise AIError("format", "模型回复被截断或拦截")
            return data
    except urllib.error.HTTPError as e:
        _http_error(e, key)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise AIError("network", redact(f"连接失败:{getattr(e, 'reason', e)}", key))


def responses_url(profile: dict) -> str:
    return _openai_root(profile) + "/responses"


def _responses_text(data: dict) -> str:
    """Responses API 的回复 → 文字: output 里所有 message 的 output_text 拼起来 (web_search_call 之类的条目跳过)."""
    out = []
    for item in data.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "message":
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "output_text":
                    out.append(str(c.get("text") or ""))
    return "".join(out)


def _anthropic_final_text(content: list) -> str:
    """服务端搜索的回复: 内容里夹着 server_tool_use / web_search_tool_result, 搜索前的"我来查一下"不要, 只取最后一次搜索结果之后的文字."""
    blocks = [b for b in content if isinstance(b, dict)]
    last = max([i for i, b in enumerate(blocks) if b.get("type") == "web_search_tool_result"] + [-1])
    return "".join(str(b.get("text") or "") for b in blocks[last + 1:] if b.get("type") == "text")


def _web_chat(profile: dict, system: str, messages: list, timeout: float, opener, max_tokens: int,
              temperature: float, cache: bool, on_usage) -> str:
    """开着网络的一次请求 (三种方式见上). 失败抛 AIError."""
    check_profile(profile)
    mode = web_mode(profile)

    def used(data):
        if on_usage:
            try:
                on_usage(*usage_of(data))
            except Exception:       # noqa: BLE001
                pass

    if mode == "responses":
        body = {"model": profile["model"], "instructions": system, "max_output_tokens": max_tokens,
                "input": [{"role": m["role"], "content": m["content"]} for m in messages],
                "tools": [{"type": "web_search"}]}
        data = _send(profile, body, timeout, opener, url=responses_url(profile))
        used(data)
        text = _responses_text(data)
    elif mode == "gemini":                                # 原生 Gemini: 服务端 google_search 工具
        body = _gemini_body(profile, system, messages, max_tokens, temperature, False)
        body["tools"] = [{"google_search": {}}]
        data = _send(profile, body, timeout, opener)
        used(data)
        text = _gemini_text(data)
    elif mode == "anthropic":
        msgs = list(messages)
        text = ""
        for _ in range(WEB_PAUSE_ROUNDS + 1):
            body = _build_body(profile, system, msgs, max_tokens, temperature, cache, False, False)
            body["tools"] = [dict(WEB_TOOL_ANTHROPIC)]
            data = _send(profile, body, timeout, opener)
            used(data)
            content = data.get("content") if isinstance(data.get("content"), list) else []
            text = _anthropic_final_text(content)
            if data.get("stop_reason") == "pause_turn":       # 服务端搜得太久被暂停: 把到现在为止的内容原样带回去接着跑
                msgs = msgs + [{"role": "assistant", "content": content}]
                continue
            break
    else:
        msgs = list(messages)
        text = ""
        for rnd in range(WEB_ROUNDS + 1):
            body = _build_body(profile, system, msgs, max_tokens, temperature, cache, False, False)
            body["tools"] = [WEB_FUNCTION]
            if rnd >= WEB_ROUNDS:
                body["tool_choice"] = "none"                   # 搜够了: 这一次必须直接答
            data = _send(profile, body, timeout, opener)
            used(data)
            try:
                msg = data["choices"][0]["message"]
            except (KeyError, IndexError, TypeError):
                raise AIError("format", "服务商返回了无法识别的内容")
            calls = [c for c in (msg.get("tool_calls") or []) if isinstance(c, dict)] if isinstance(msg, dict) else []
            if not calls or rnd >= WEB_ROUNDS:
                text = (msg.get("content") or "") if isinstance(msg, dict) else ""
                break
            msgs = msgs + [{"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls}]
            for c in calls:
                try:
                    args = json.loads((c.get("function") or {}).get("arguments") or "{}")
                    result = web_search(str(args.get("query") or ""), opener=opener)
                except AIError as e:
                    result = f"(搜索失败: {e.message})"
                except (ValueError, AttributeError):
                    result = "(搜索失败: 参数不对)"
                msgs.append({"role": "tool", "tool_call_id": str(c.get("id") or ""), "content": result})
    text = strip_think(text)
    if not text.strip():
        raise AIError("format", "模型没有返回内容")
    return text


def image_mime(data: bytes) -> str:
    """图片文件头 → MIME (模型接口只认 PNG / JPEG / WEBP / GIF); 认不出返回 ""."""
    d = bytes(data[:16])
    if d.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if d.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if d[:4] == b"RIFF" and d[8:12] == b"WEBP":
        return "image/webp"
    if d[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return ""


def image_content(profile: dict, data: bytes, mime: str, text: str) -> list:
    """一条带图片的用户消息的 content (各协议的写法不同)."""
    b64 = base64.b64encode(data).decode("ascii")
    if profile["protocol"] == "anthropic":
        return [{"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                {"type": "text", "text": text}]
    if profile["protocol"] == "gemini":
        return [{"inlineData": {"mimeType": mime, "data": b64}}, {"text": text}]
    return [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            {"type": "text", "text": text}]


def ocr_image(profile: dict, data: bytes, timeout: float = 120.0, opener=None, chat=None) -> str:
    """识别字体: 让模型把图片上的文字返回. 图片不会存下来, 也不会发进聊天. 没有文字返回 "". 失败抛 AIError.
    chat: 发请求的函数 (默认 chat_messages; 沟通模块会传自己的, 测试里换成假的)."""
    if len(data) > OCR_MAX_BYTES:
        raise AIError("format", "图片太大了 (最大 4.5 MB)")
    mime = image_mime(data)
    if not mime:
        raise AIError("format", "只支持 PNG / JPG / WEBP / GIF 图片")
    msgs = [{"role": "user", "content": image_content(profile, data, mime, OCR_USER)}]
    if chat is None:
        reply = chat_messages(profile, OCR_SYSTEM, msgs, timeout=timeout, opener=opener, max_tokens=OCR_TOKENS, temperature=0.0)
    else:
        reply = chat(profile, OCR_SYSTEM, msgs, max_tokens=OCR_TOKENS, temperature=0.0, timeout=timeout, cache=False)
    t = re.sub(r"```[^\n]*", "", str(reply or "")).replace("```", "").strip()
    return "" if t.lower().replace(" ", "") == "[notext]" else t


def models_url(profile: dict) -> str:
    base = profile["base_url"].strip().rstrip("/")
    if profile.get("protocol") == "anthropic":
        return base + ("/models" if base.endswith("/v1") else "/v1/models")
    if profile.get("protocol") == "gemini":
        return (base if base.endswith(("/v1beta", "/v1")) else base + "/v1beta") + "/models"
    return _openai_root(profile) + "/models"


def list_models(profile: dict, timeout: float = 20.0, opener=None) -> list:
    """向服务商取可用的模型 id (GET /models, 各协议都有). 失败抛 AIError. 取到的记进 MODEL_CACHE."""
    check_profile(dict(profile, model=profile.get("model") or "-"))
    key = (profile.get("api_key") or "").strip()
    headers = {"User-Agent": "OIT/1.0"}
    if profile["protocol"] == "anthropic":
        headers["x-api-key"] = key
        headers["anthropic-version"] = ANTHROPIC_VERSION
    elif profile["protocol"] == "gemini":
        headers["x-goog-api-key"] = key
    elif key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(models_url(profile), headers=headers, method="GET")
    try:
        with _open_url(req, timeout, opener) as resp:
            raw = resp.read(2_000_000)
    except urllib.error.HTTPError as e:
        try:
            payload = e.read(4000) if e.fp is not None else b""
        except Exception:
            payload = b""
        if e.code == 403 and e.headers and e.headers.get("X-OIT-Reason"):
            raise AIError("blocked", "请求被本程序的过滤代理拦截(该服务商的域名不在白名单中)")
        if e.code in (401, 403):
            raise AIError("auth", "API Key 无效或没有权限:" + redact(_error_text(payload), key))
        raise AIError("http", f"服务商返回 {e.code}:" + redact(_error_text(payload), key))
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise AIError("network", redact(f"连接失败:{getattr(e, 'reason', e)}", key))
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
        if profile["protocol"] == "gemini":                  # 原生 Gemini: {"models":[{"name":"models/...","supportedGenerationMethods":[...]}]}
            ids = [str(m["name"]).split("/")[-1] for m in data["models"]
                   if isinstance(m, dict) and m.get("name")
                   and (not m.get("supportedGenerationMethods") or "generateContent" in m["supportedGenerationMethods"])]
        else:
            ids = [str(m["id"]) for m in data["data"] if isinstance(m, dict) and m.get("id")]
    except (ValueError, KeyError, TypeError):
        raise AIError("format", "服务商返回了无法识别的内容")
    ids = sorted(dict.fromkeys(ids))
    if not ids:
        raise AIError("format", "服务商没有给出模型列表")
    MODEL_CACHE[(profile.get("provider") or "", profile["base_url"].strip())] = ids
    return ids


def models_for(profile: dict) -> list:
    """下拉框里的模型: 取到过的 > 预设的; 当前用着的一定在里面."""
    prov = profile.get("provider") or ""
    preset = PROVIDERS.get(prov, {}).get("models") or PROVIDERS.get(family_of(profile), {}).get("models") or []
    ids = list(MODEL_CACHE.get((prov, (profile.get("base_url") or "").strip())) or preset or [])
    cur = (profile.get("model") or "").strip()
    if cur and cur not in ids:
        ids.insert(0, cur)
    return ids
