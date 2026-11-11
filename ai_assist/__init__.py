# -*- coding: utf-8 -*-
"""
AI 名单建议:接入模型 + 黑 / 白名单两套提示词
────────────────────────────────────────────
流程(界面在 pages/rules_page.py):
    用户同意采集 → context_collect 生成「应用清单 + 域名/次数」→ 预览给用户看 →
    用户点发送 → 本模块把它连同提示词发给所选模型 → 解析成条目 → 用户勾选 → 才写入名单.

隐私 / 行为约定(改这个文件前请先读):
  · 本模块只在被调用时联网,且只向用户选定的那一个服务商发一次请求;不写日志,不缓存上下文;
  · 只持久化「服务商配置」,不存采集内容. API Key 的保护 (见下方「API Key 保护」):
      - 存盘前用 Windows DPAPI 加密, 只有当前 Windows 账户能解开, 文件被拷走也读不出;
      - 预设服务商的 Key 只发往它自己的官方域名; 只走 https (本机模型除外);
      - 不跟随重定向 (重定向会把 Key 带到别的地址); 错误信息里出现 Key 时抹掉;
  · 黑名单模式:模型**不解释理由**.这一点在两层保证 —— 提示词要求不写,
    解析器也只保留条目本身,丢弃模型返回的所有说明文字;界面因此无从展示评价;
  · 模型的输出永远只是「候选」,从不自动写入名单;写入走 RuleStore,照样遵守名单的等待规则;
  · 请求走系统代理.若本程序的过滤代理正在运行,服务商域名不在白名单时会被拦下 ——
    这是预期行为(不会因为 AI 功能给自己开后门),错误提示里会说明.
"""

import base64
import json
import os
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from netproxy.rules import norm_app, norm_domain
import securefile

from .ai_common import *
from .ai_key import (
    _DPAPI_ENTROPY, _ENC_PREFIX, _dpapi, can_encrypt, decrypt_key, encrypt_key
)
from .ai_config import (
    AIConfig, _ADAPTIVE_NAME_RE, _ADAPTIVE_RE, _CONFIGS, adaptive_thinking, cache_key, cache_plan,
    check_profile, default_profile, family_of, is_deepseek, shared_config, think_on_field
)
from .ai_http import (
    MODEL_CACHE, _DIRECT, _Fresh, _NoRedirect, _OPENER, _THINK_BLOCK_RE, _THINK_CLOSE_RE, _THINK_OPEN_RE,
    _THINK_TAGS, _anthropic_final_text, _build_body, _chat_once, _error_text, _gemini_body, _gemini_parts,
    _gemini_text, _open_url, _responses_text, _send, _web_chat, chat, chat_messages, endpoint, image_content,
    image_mime, list_models, models_for, models_url, ocr_image, proxy_hint, redact, responses_url,
    strip_think, usage_of
)
from .ai_web import (
    SEARCH_HOSTS, SEARCH_RESULTS, SEARCH_UA, SEARCH_URL, _bing_url, _clean_html, _open_search, parse_search,
    web_local, web_mode, web_search
)

from . import ai_http as _ai_http, ai_key as _ai_key, ai_web as _ai_web

# ══════════════ 提示词 ══════════════
# 两段提示词共同的安全约定:清单是「数据」不是「指令」(应用名 / 域名里可能被塞进一句话).

def system_prompt(mode: str) -> str:
    return PROMPT_WHITELIST if mode == MODE_WHITELIST else PROMPT_BLACKLIST


def build_user_message(context_text: str, goal: str = "") -> str:
    parts = ["以下是用户提供的本机信息:\n\n" + (context_text or "(无)")]
    goal = (goal or "").strip()
    if goal:
        parts.append("用户的补充说明(原话):\n" + goal[:500])
    parts.append("请按要求只输出 JSON.")
    return "\n\n".join(parts)


# ══════════════ 用户可编辑的上下文 ══════════════

def extract_known(text: str):
    """从发送前的预览文本里重新提取 (域名列表, exe 列表).

    预览文本允许用户增删改(不想泄露的网站直接删掉),所以「清单」必须以**最终要发送的文本**为准,
    而不是采集时的原始结果 —— 否则用户删掉的条目仍会被当作「已知」,模型编造的同名项也能混进来.
    """
    exes = []
    for m in _EXE_RE.finditer(text or ""):
        a = norm_app(m.group(0))
        if a and a not in exes:
            exes.append(a)
    domains = []
    for line in (text or "").splitlines():
        line = _EXE_RE.sub(" ", line)              # 应用行里的 exe 名不当域名
        for m in _HOST_RE.finditer(line):
            d = norm_domain(m.group(0))
            if d.startswith("www."):
                d = d[4:]
            if d and d not in domains:
                domains.append(d)
    return domains, exes


# ══════════════ 解析 ══════════════


def _json_object(text: str):
    """从模型输出里取第一个完整的 JSON 对象(容忍 ```json 围栏和前后废话)."""
    text = re.sub(r"```(?:json)?", "", text or "")
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                    except ValueError:
                        break
                    if isinstance(obj, dict):
                        return obj
                    break
        start = text.find("{", start + 1)
    return None


def _entries(value):
    """domains / apps 数组 → [(item, reason)];既收字符串也收 {item, reason}."""
    out = []
    if not isinstance(value, list):
        return out
    for e in value:
        if isinstance(e, str):
            out.append((e, ""))
        elif isinstance(e, dict) and isinstance(e.get("item"), str):
            r = e.get("reason")
            out.append((e["item"], r if isinstance(r, str) else ""))
    return out


def parse_suggestions(raw: str, mode: str, known_domains=(), known_apps=()) -> dict:
    """返回 {"domains": [{"item","reason","extra"}], "apps": [...]}.

    黑名单:条目必须出自采集到的清单,reason 一律丢弃(见模块说明).
    白名单:应用必须出自清单;网站允许少量清单外的推荐(extra=True).
    """
    obj = _json_object(raw)
    if obj is None:
        raise AIError("format", "模型没有按要求返回 JSON")
    black = mode != MODE_WHITELIST
    kd = {norm_domain(d) for d in known_domains} - {""}
    ka = {norm_app(a) for a in known_apps} - {""}

    def covered(host):                       # 子域名算作清单里的域名
        return any(host == d or host.endswith("." + d) for d in kd)

    out = {"domains": [], "apps": []}
    seen = set()
    extras = 0
    for item, reason in _entries(obj.get("domains")):
        d = norm_domain(item)
        if d.startswith("www."):                     # 子域名规则自动涵盖,去掉 www 让名单更干净
            d = d[4:]
        if not d or ("d", d) in seen:
            continue
        extra = not covered(d)
        if extra:
            if black or extras >= MAX_EXTRA_DOMAINS:
                continue
            extras += 1
        seen.add(("d", d))
        out["domains"].append({"item": d, "extra": extra,
                               "reason": "" if black else reason.strip()[:REASON_LEN]})
        if len(out["domains"]) >= MAX_ITEMS:
            break
    for item, reason in _entries(obj.get("apps")):
        a = norm_app(item)
        if not a or a not in ka or ("a", a) in seen:
            continue
        seen.add(("a", a))
        out["apps"].append({"item": a, "extra": False,
                            "reason": "" if black else reason.strip()[:REASON_LEN]})
        if len(out["apps"]) >= MAX_ITEMS:
            break
    return out


def suggest(profile: dict, mode: str, context_text: str, known_domains, known_apps,
            goal: str = "", opener=None) -> dict:
    """一次完整的建议请求(阻塞;界面应放在线程里调用)."""
    raw = chat(profile, system_prompt(mode), build_user_message(context_text, goal),
               opener=opener)
    return parse_suggestions(raw, mode, known_domains, known_apps)


# ══════════════ 自检(不联网) ══════════════
def _selftest():
    import http.server
    import tempfile
    import threading

    # 默认模板:本机没有配置文件时采用,且 API Key 永远不从模板读取
    td = tempfile.mkdtemp()
    tpl = os.path.join(td, "t.json")
    with securefile.open(tpl, "w", encoding="utf-8") as f:
        json.dump({"provider": "deepseek", "profiles": {"deepseek": {
            "base_url": "https://x.example/v1", "model": "m1", "protocol": "openai",
            "api_key": "LEAK"}}}, f)
    t = AIConfig(os.path.join(td, "none.json"), template=tpl)
    assert t.provider == "deepseek" and t.profile("deepseek")["model"] == "m1"
    assert t.profile("deepseek")["api_key"] == "" and t.profile("claude")["base_url"]
    assert not os.path.exists(os.path.join(td, "none.json"))      # 只读模板,不落盘
    assert AIConfig(os.path.join(td, "none.json"), template=os.path.join(td, "nope")).provider == "claude"
    real = AIConfig(os.path.join(td, "none.json"), template=DEFAULT_TEMPLATE)
    assert all(p["api_key"] == "" for p in real.profiles.values()), "仓库模板里不能带 Key"

    # 预设自动填入
    c = AIConfig(os.path.join(tempfile.mkdtemp(), "ai.json"))
    assert c.profile("claude")["base_url"] == "https://api.anthropic.com"
    assert c.profile("chatgpt")["base_url"] == "https://api.openai.com/v1"
    assert c.profile("gemini")["base_url"] == "https://generativelanguage.googleapis.com"
    assert c.profile("deepseek")["base_url"] == "https://api.deepseek.com"
    assert c.profile("custom")["base_url"] == "" and c.profile("custom")["model"] == ""
    assert c.profile("gemini")["family"] == "gemini" and c.profile("gemini")["protocol"] == "gemini"
    c.set_profile("deepseek", base_url="", api_key=" sk-x ")
    assert c.profile("deepseek")["base_url"] == "https://api.deepseek.com"
    assert c.profile("deepseek")["api_key"] == "sk-x"
    # 自定义供应商: 模型类型决定协议, 不是随便填的协议字段
    c.set_profile("custom", base_url="https://relay.example/v1", model="gemini-3.5-flash", family="gemini")
    assert c.profile("custom")["protocol"] == "gemini" and c.profile("custom")["family"] == "gemini"
    c.set_profile("custom", family="claude")
    assert c.profile("custom")["protocol"] == "anthropic"
    c.set_profile("custom", family="other")
    assert c.profile("custom")["protocol"] == "openai"
    c.provider = "deepseek"
    assert c.save()
    assert AIConfig(c.path).profile()["api_key"] == "sk-x"
    assert AIConfig(c.path).profile("custom")["family"] == "other", "模型类型要存下来"

    # 命名配置: 最多 10 份, 互相独立, 每个 AI 各选一份
    c2 = AIConfig(os.path.join(tempfile.mkdtemp(), "ai2.json"))
    cid = c2.add_config("备用")
    assert cid and c2.config_name(cid) == "备用" and c2.profile(cid)["provider"] == "custom"
    assert not c2.rename_config(cid, "Claude") and c2.rename_config(cid, "备用2")
    c2.set_profile(cid, preset="claude", base_url="https://api.anthropic.com", model="m-c")
    assert c2.profile(cid)["protocol"] == "anthropic" and c2.profile(cid)["model"] == "m-c"
    c2.set_profile(cid, preset="custom", family="gemini", base_url="https://relay.example/v1")
    assert c2.profile(cid)["protocol"] == "gemini"
    # 同一 Gemini 模型经 OpenAI 兼容中转: 协议独立保存并在重开后保持。
    c2.set_profile(cid, family="gemini", protocol="openai")
    assert c2.profile(cid)["protocol"] == "openai"
    assert c2.save() and AIConfig(c2.path).profile(cid)["protocol"] == "openai"
    relay = c2.profile(cid)
    assert endpoint(relay) == "https://relay.example/v1/chat/completions"
    assert models_url(relay) == "https://relay.example/v1/models"
    native = dict(relay, protocol="gemini")
    assert endpoint(native).startswith("https://relay.example/v1/models/")
    assert models_url(native) == "https://relay.example/v1/models"
    assert c2.set_active(cid) and c2.config_for("甲.txt") == cid
    c2.assign_config("乙.txt", "deepseek")
    assert c2.config_for("乙.txt") == "deepseek" and c2.config_for("甲.txt") == cid
    assert c2.save() and AIConfig(c2.path).config_for("乙.txt") == "deepseek"
    assert c2.remove_config(cid)
    while c2.add_config("x"):
        pass
    assert len(c2.order) == MAX_CONFIGS

    assert endpoint({"base_url": "https://api.openai.com/v1", "protocol": "openai"}) \
        == "https://api.openai.com/v1/chat/completions"
    assert endpoint({"base_url": "https://api.deepseek.com/", "protocol": "openai"}) \
        == "https://api.deepseek.com/chat/completions"
    assert endpoint({"base_url": "https://api.anthropic.com", "protocol": "anthropic"}) \
        == "https://api.anthropic.com/v1/messages"
    assert endpoint({"base_url": "https://generativelanguage.googleapis.com", "protocol": "gemini",
                     "model": "gemini-3.5-flash"}) \
        == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent"
    assert endpoint({"base_url": "https://relay.example/v1beta", "protocol": "gemini",
                     "model": "models/gemini-3.1-pro"}) \
        == "https://relay.example/v1beta/models/gemini-3.1-pro:generateContent"
    for bad in ({"base_url": "", "model": "m", "api_key": "k"},
                {"base_url": "http://evil.example/v1", "model": "m", "api_key": "k"},
                {"base_url": "https://a.example", "model": "", "api_key": "k"},
                {"base_url": "https://a.example", "model": "m", "api_key": ""}):
        try:
            check_profile(bad)
            raise AssertionError(bad)
        except AIError as e:
            assert e.kind == "config"
    check_profile({"base_url": "http://localhost:11434/v1", "model": "m", "api_key": ""})

    # 解析:黑名单丢弃理由,丢弃清单外条目
    apps = ["steam.exe", "code.exe"]
    doms = ["bilibili.com", "github.com"]
    raw = ('好的:\n```json\n{"domains":[{"item":"www.bilibili.com","reason":"浪费时间"},'
           '"made-up.com","https://github.com/x"],"apps":[{"item":"Steam","reason":"沉迷"},'
           '"ghost.exe"]}\n```')
    r = parse_suggestions(raw, MODE_BLACKLIST, doms, apps)
    assert [d["item"] for d in r["domains"]] == ["bilibili.com", "github.com"], r
    assert [a["item"] for a in r["apps"]] == ["steam.exe"], r
    assert all(x["reason"] == "" for x in r["domains"] + r["apps"]), "黑名单不得带理由"
    # 白名单:保留理由,允许少量清单外网站
    r = parse_suggestions(
        '{"domains":[{"item":"github.com","reason":"代码托管"},{"item":"docs.python.org",'
        '"reason":"文档"}],"apps":[{"item":"code.exe","reason":"编辑器"},"ghost.exe"]}',
        MODE_WHITELIST, doms, apps)
    assert r["domains"][0]["reason"] == "代码托管" and not r["domains"][0]["extra"]
    assert r["domains"][1]["extra"] and r["apps"][0]["item"] == "code.exe"
    assert len(r["apps"]) == 1
    try:
        parse_suggestions("没有 json", MODE_BLACKLIST, doms, apps)
        raise AssertionError
    except AIError as e:
        assert e.kind == "format"
    kd2, ka2 = extract_known("【应用】\nSteam (steam.exe)\nVisual Studio Code (Code.exe)\n"
                             "【网站】\nbilibili.com —— 312 次\nwww.github.com\nhttps://x.org/p")
    assert ka2 == ["steam.exe", "code.exe"] and kd2 == ["bilibili.com", "github.com", "x.org"], (kd2, ka2)
    assert "steam.exe" not in kd2
    assert "不要写任何理由" in PROMPT_BLACKLIST and "reason" not in PROMPT_BLACKLIST

    # 端到端:本地假服务,分别模拟 OpenAI 兼容 与 Anthropic 协议
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n))
            seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
            answer = '{"domains":["bilibili.com"],"apps":["steam.exe"]}'
            if ":generateContent" in self.path:                    # 原生 Gemini
                out = {"candidates": [{"content": {"parts": [{"text": "你好"}]}}],
                       "usageMetadata": {"promptTokenCount": 10, "cachedContentTokenCount": 4}}
            elif self.path.startswith("/cache400") and "cache_control" in json.dumps(body):
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"Malformed request: unknown field cache_control"}}')
                return
            elif self.path.endswith("/messages"):
                out = {"content": [{"type": "text", "text": answer}]}
            elif self.path.startswith("/redir"):
                self.send_response(302)
                self.send_header("Location", "http://evil.example/steal")
                self.end_headers()
                return
            elif self.path.startswith("/echo"):
                self.send_response(401)
                self.end_headers()
                self.wfile.write(json.dumps({"error": {"message": "Incorrect key: " +
                                 self.headers.get("Authorization", "")}}).encode())
                return
            elif self.path.startswith("/deny"):
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"bad key"}}')
                return
            else:
                out = {"choices": [{"message": {"content": answer}}]}
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        p = {"base_url": base + "/v1", "model": "m", "protocol": "openai", "api_key": "k1"}
        r = suggest(p, MODE_BLACKLIST, "ctx", doms, apps, goal="少看视频")
        assert r["domains"][0]["item"] == "bilibili.com" and r["apps"][0]["item"] == "steam.exe"
        path, hdr, body = seen[-1]
        assert path == "/v1/chat/completions" and hdr["authorization"] == "Bearer k1"
        assert body["messages"][0]["content"] == PROMPT_BLACKLIST
        assert "少看视频" in body["messages"][1]["content"]
        r = suggest(dict(p, protocol="anthropic", base_url=base), MODE_WHITELIST,
                    "ctx", doms, apps)
        path, hdr, body = seen[-1]
        assert path == "/v1/messages" and hdr["x-api-key"] == "k1" \
            and hdr["anthropic-version"] == ANTHROPIC_VERSION
        assert body["system"] == PROMPT_WHITELIST and body["messages"][0]["role"] == "user"
        # 提示缓存: Anthropic 协议把系统提示和最后一条消息标成缓存位置; OpenAI 协议不改请求
        chat_messages(dict(p, protocol="anthropic", base_url=base), "SYS", [{"role": "user", "content": "hi"}], cache=True)
        body = seen[-1][2]
        assert body["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
        assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]}]
        chat_messages(p, "SYS", [{"role": "user", "content": "hi"}], cache=True)
        assert seen[-1][2]["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]
        # 中转不认 cache_control: 400 后去掉缓存标记重试一次, 并记住这个地址
        pc = dict(p, protocol="anthropic", base_url=base + "/cache400")
        assert chat_messages(pc, "SYS", [{"role": "user", "content": "hi"}], cache=True) == \
            '{"domains":["bilibili.com"],"apps":["steam.exe"]}'
        assert "cache_control" not in json.dumps(seen[-1][2])
        assert endpoint(pc) in CACHE_REJECT
        # 模型类型 → 缓存策略: 一家只走自己的一条路, 不把别家的字段带进来
        mm = [{"role": "user", "content": "hi"}]
        assert family_of({"provider": "custom", "model": "gemini-3.5-flash",
                          "base_url": "https://relay.example/v1"}) == "other", "自定义不按模型名猜, 必须用户选"
        assert family_of({"provider": "custom", "protocol": "anthropic"}) == "claude"
        assert family_of({"provider": "custom", "base_url": "https://api.deepseek.com"}) == "deepseek"
        assert cache_plan({"provider": "claude"}) == {"family": "claude", "explicit": True, "key": False, "static_system": False, "delay": 0.0}
        assert cache_plan({"provider": "chatgpt"})["key"] and not cache_plan({"provider": "chatgpt"})["explicit"]
        assert cache_plan({"provider": "gemini"})["static_system"] and cache_plan({"provider": "gemini"})["delay"] == 0.0
        assert cache_plan({"provider": "deepseek"})["delay"] == 3.0
        # ChatGPT: 不带 thinking (OpenAI 不认), 带 prompt_cache_key
        pg = {"base_url": base, "model": "gpt-4o", "protocol": "openai", "api_key": "k", "provider": "chatgpt"}
        b = _build_body(pg, "SYS", mm, 4096, 0.8, True)
        assert "thinking" not in b and b["prompt_cache_key"] == cache_key("SYS", "gpt-4o"), b
        assert "prompt_cache_key" not in _build_body(pg, "SYS", mm, 4096, 0.8, False)
        assert "thinking" not in _build_body(pg, "SYS", mm, 4096, 0.8, True, False, True), "开思考也不带 OpenAI 不认的字段"
        # Gemini: 原生请求体; 缓存靠相同的 systemInstruction, 请求里不标缓存位置
        gm = {"base_url": "https://generativelanguage.googleapis.com", "model": "gemini-3.5-flash",
              "protocol": "gemini", "api_key": "k", "provider": "gemini"}
        b = _build_body(gm, "SYS", mm, 4096, 0.9, True)
        assert b["systemInstruction"] == {"parts": [{"text": "SYS"}]} and b["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
        assert b["generationConfig"] == {"maxOutputTokens": 4096, "temperature": 0.9}
        assert "thinking" not in b and "cache_control" not in json.dumps(b)
        assert _gemini_text({"candidates": [{"content": {"parts": [{"text": "a"}, {"text": "想", "thought": True}, {"text": "b"}]}}]}) == "ab"
        try:
            _gemini_text({"promptFeedback": {"blockReason": "SAFETY"}})
            raise AssertionError
        except AIError as e:
            assert "拦截" in e.message
        assert usage_of({"usageMetadata": {"promptTokenCount": 120, "cachedContentTokenCount": 80}}) == (120, 80)
        assert usage_of({"usageMetadata": {"promptTokenCount": 10}}) == (10, 0)
        # 原生 Gemini 端到端: 路径 / Key 请求头 / 请求体 / 缓存用量
        gp = {"base_url": base, "model": "gemini-3.5-flash", "protocol": "gemini", "api_key": "gk", "provider": "custom"}
        us = []
        assert chat_messages(gp, "SYS", mm, cache=True, on_usage=lambda *a: us.append(a)) == "你好"
        path, hdr, body = seen[-1]
        assert path == "/v1beta/models/gemini-3.5-flash:generateContent" and hdr["x-goog-api-key"] == "gk"
        assert body["systemInstruction"] == {"parts": [{"text": "SYS"}]} and "cache_control" not in json.dumps(body)
        assert us[-1] == (10, 4), "Gemini 的 cachedContentTokenCount 要算进命中"
        # DeepSeek 官方地址: 显式关掉思考 (它默认开, 思考 token 按输出计费, 而且会忽略 temperature); 别家不加
        ds = dict(p, base_url="https://api.deepseek.com", model="deepseek-flash")
        b = _build_body(ds, "SYS", [{"role": "user", "content": "hi"}], 4096, 0.8, True)
        assert b["thinking"] == {"type": "disabled"} and b["temperature"] == 0.8
        # 别家 / 中转: 默认也关 (thinking disabled); 不认回 400 就去掉重试, 并记住
        assert _build_body(p, "SYS", [{"role": "user", "content": "hi"}], 4096, 0.8, True)["thinking"] == {"type": "disabled"}
        assert "thinking" not in _build_body(p, "SYS", [{"role": "user", "content": "hi"}], 4096, 0.8, True, False)
        assert "thinking" not in _build_body(dict(p, protocol="anthropic"), "SYS", [{"role": "user", "content": "hi"}], 4096, 0.8, True), \
            "Anthropic 关思考不写 thinking (新 Claude 不认 disabled)"
        b = _build_body(dict(ds, protocol="anthropic", base_url="https://api.deepseek.com/anthropic"),
                        "SYS", [{"role": "user", "content": "hi"}], 4096, 0.8, True)
        assert b["reasoning"] == {"effort": "none"} and "thinking" not in b
        # 开思考 (think=True, comm.think_reason 判断需要时): Anthropic 协议带 budget_tokens 且没有 temperature; OpenAI 协议不带 temperature; DeepSeek 用自己的字段
        m1 = [{"role": "user", "content": "hi"}]
        b = _build_body(dict(p, protocol="anthropic"), "SYS", m1, 4096, 0.8, True, False, True)
        assert b["thinking"] == {"type": "enabled", "budget_tokens": THINK_BUDGET} and "temperature" not in b and b["max_tokens"] > THINK_BUDGET
        b = _build_body(p, "SYS", m1, 4096, 0.8, True, False, True)
        assert b["thinking"] == {"type": "enabled", "budget_tokens": THINK_BUDGET} and "temperature" not in b
        assert _build_body(ds, "SYS", m1, 4096, 0.8, True, False, True)["thinking"] == {"type": "enabled"}
        b = _build_body(dict(ds, protocol="anthropic", base_url="https://api.deepseek.com/anthropic"), "SYS", m1, 4096, 0.8, True, False, True)
        assert b["reasoning"] == {"effort": "medium"} and "thinking" not in b
        # 新的 Claude (4.6 及以后) 不认 budget_tokens (Haiku 5.5 等直接 400): 开思考用 adaptive; 老的 / 别家不变
        for mid in ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-sonnet-5", "claude-opus-5-5", "claude-opus-4-7", "claude-opus-4-6",
                    "claude-fable-5-1", "anthropic/claude-haiku-5-5", "Claude-Sonnet-4.6"):
            assert adaptive_thinking({"model": mid}), mid
            for proto in ("anthropic", "openai"):
                b = _build_body(dict(p, protocol=proto, model=mid), "SYS", m1, 4096, 0.8, True, False, True)
                assert b["thinking"] == {"type": "adaptive"} and "temperature" not in b, (mid, proto)
        for mid in ("claude-haiku-4-5", "claude-haiku-4-5-20251001", "claude-sonnet-4-5", "claude-sonnet-4-20250514", "claude-opus-4-1-20250805",
                    "claude-3-5-sonnet-20241022", "gpt-5", "deepseek-flash", "m", ""):
            assert not adaptive_thinking({"model": mid}), mid
            assert think_on_field({"model": mid}) == {"type": "enabled", "budget_tokens": THINK_BUDGET}, mid
        b = _build_body(dict(ds, protocol="anthropic", base_url="https://api.deepseek.com/anthropic", model="claude-haiku-5-5"), "SYS", m1, 4096, 0.8, True, False, True)
        assert b["reasoning"] == {"effort": "medium"}, "DeepSeek 官方地址永远用自己的字段"
        chat_messages(p, "SYS", m1, max_tokens=4096, think=True)
        assert seen[-1][2]["thinking"]["type"] == "enabled", "think=True 真的发出了开思考"
        chat_messages(p, "SYS", m1, max_tokens=4096)
        assert seen[-1][2]["thinking"] == {"type": "disabled"}, "默认仍然关"
        # 思考内容混进正文: 去掉 (成对 / 只有结尾标签 / 只有开头标签), 没有标签的原样不动
        assert strip_think("你好") == "你好" and strip_think("a < b 且 c > d") == "a < b 且 c > d"
        assert strip_think("<think>想一想\n再想想</think>\n\n你好") == "你好"
        assert strip_think("<THINKING>x</THINKING>好") == "好" and strip_think("<think>a</think>[1]<think>b</think>好") == "[1]好"
        assert strip_think("思考过程\n</think>\n- 喜欢钢琴") == "- 喜欢钢琴"
        assert strip_think("- 喜欢钢琴<think>没写完") == "- 喜欢钢琴"
        assert strip_think("<think>只有思考") == ""
        try:
            chat(dict(p, base_url=base + "/deny"), "s", "u")
            raise AssertionError
        except AIError as e:
            assert e.kind == "auth" and "bad key" in e.message
        # 重定向: 拒绝, 不把 Key 带到新地址
        n_before = len(seen)
        try:
            chat(dict(p, base_url=base + "/redir"), "s", "u")
            raise AssertionError
        except AIError as e:
            assert e.kind == "http" and "跳转" in e.message, e.message
        assert len(seen) == n_before + 1, "只应有原请求, 不能跟随重定向"
        # 报错里回显的 Key 要抹掉
        try:
            chat(dict(p, base_url=base + "/echo", api_key="sk-secret-123"), "s", "u")
            raise AssertionError
        except AIError as e:
            assert "sk-secret-123" not in e.message and "***" in e.message, e.message
    finally:
        srv.shutdown()

    # 取模型列表: OpenAI 兼容 / Anthropic 两种协议都是 GET .../models
    class H2(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
            if "/v1beta/models" in self.path:                 # 原生 Gemini
                obj = {"models": [{"name": "models/gemini-3.5-flash", "supportedGenerationMethods": ["generateContent"]},
                                  {"name": "models/text-embed", "supportedGenerationMethods": ["embedContent"]}]}
            else:
                obj = {"data": [{"id": "m-b"}, {"id": "m-a"}, {"id": "m-b"}]}
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv2 = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H2)
    threading.Thread(target=srv2.serve_forever, daemon=True).start()
    base2 = f"http://127.0.0.1:{srv2.server_address[1]}"
    try:
        seen = []
        assert list_models({"base_url": base2 + "/v1", "protocol": "openai", "api_key": "k9", "provider": "custom"}) == ["m-a", "m-b"]
        assert seen[-1][0] == "/v1/models" and seen[-1][1]["authorization"] == "Bearer k9"
        list_models({"base_url": base2, "protocol": "anthropic", "api_key": "k9", "provider": "custom"})
        assert seen[-1][0] == "/v1/models" and seen[-1][1]["x-api-key"] == "k9" and seen[-1][1]["anthropic-version"] == ANTHROPIC_VERSION
        gem = {"base_url": base2, "protocol": "gemini", "api_key": "k9", "provider": "custom"}
        assert models_for({"provider": "custom", "base_url": base2, "model": "mine"}) == ["mine", "m-a", "m-b"]
        assert list_models(gem) == ["gemini-3.5-flash"], "只留能 generateContent 的"
        assert seen[-1][0] == "/v1beta/models" and seen[-1][1]["x-goog-api-key"] == "k9"
        assert models_for({"provider": "deepseek", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"})[0] == "deepseek-chat"
    finally:
        srv2.shutdown()

    # ---- 限时网络 (web=True): 三种方式 ----
    BING = ('<html><ol><li class="b_algo"><h2><a href="https://example.com/a?x=1&amp;y=2">标题 <strong>甲</strong></a></h2>'
            '<div><p class="b_lineclamp2">摘要&nbsp;甲 &amp; 乙</p></div></li>'
            '<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?!&amp;&amp;u=a1aHR0cHM6Ly9leGFtcGxlLm9yZy9i&amp;ntb=1">标题乙</a></h2>'
            '<p>摘要乙</p></li></ol></html>')
    r = parse_search(BING)
    assert r[0] == ("标题 甲", "https://example.com/a?x=1&y=2", "摘要 甲 & 乙"), r
    assert r[1][1] == "https://example.org/b" and r[1][2] == "摘要乙", r
    assert parse_search("<html>没有结果</html>") == []
    assert web_mode({"base_url": "https://api.openai.com/v1", "protocol": "openai"}) == "responses"
    assert web_mode({"base_url": "https://api.anthropic.com", "protocol": "anthropic"}) == "anthropic"
    assert web_mode({"base_url": "https://api.deepseek.com", "protocol": "openai"}) == "function"
    assert web_local({"base_url": "https://api.deepseek.com", "protocol": "openai"}) and not web_local({"base_url": "https://api.openai.com/v1", "protocol": "openai"})
    web_seen = []

    class H3(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _out(self, code, obj, raw=None):
            data = raw if raw is not None else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            web_seen.append(("GET", self.path, None))
            self._out(200, None, BING.encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            web_seen.append(("POST", self.path, body))
            if self.path.startswith("/nosearch"):
                return self._out(400, {"error": {"message": "unknown tool"}})
            if self.path.endswith("/messages"):                       # Anthropic: 服务端搜索, 内容里夹着 server_tool_use / web_search_tool_result
                n = sum(1 for m in body["messages"] if m["role"] == "assistant")
                if "tools" in body and n == 0 and body.get("pause"):
                    pass
                content = [{"type": "text", "text": "我先查一下. "},
                           {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "x"}},
                           {"type": "web_search_tool_result", "tool_use_id": "s1", "content": []},
                           {"type": "text", "text": "查到了: "}, {"type": "text", "text": "答案 A"}]
                return self._out(200, {"content": content, "stop_reason": "end_turn", "usage": {"input_tokens": 7}})
            if self.path.endswith("/responses"):
                return self._out(200, {"output": [{"type": "web_search_call", "id": "w"},
                                                  {"type": "message", "content": [{"type": "output_text", "text": "答案 R"}]}],
                                       "usage": {"input_tokens": 3}})
            tool_done = any(m.get("role") == "tool" for m in body["messages"])         # chat/completions: 第一轮要搜, 拿到结果再答
            if "tools" in body and not tool_done:
                return self._out(200, {"choices": [{"message": {"content": None, "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": json.dumps({"query": "今天 新闻"})}}]}}]})
            return self._out(200, {"choices": [{"message": {"content": "答案 F"}}]})

    srv3 = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H3)
    threading.Thread(target=srv3.serve_forever, daemon=True).start()
    base3 = f"http://127.0.0.1:{srv3.server_address[1]}"
    saved_url = _ai_web.SEARCH_URL
    try:
        _ai_web.SEARCH_URL = base3 + "/search"
        usage = []
        m1 = [{"role": "user", "content": "今天有什么新闻"}]
        # DeepSeek / 别的 OpenAI 兼容: 函数调用, OIT 自己搜, 结果交回去再答
        pf = {"base_url": base3 + "/v1", "model": "m", "protocol": "openai", "api_key": "k", "provider": "custom"}
        assert chat_messages(pf, "SYS", m1, web=True, on_usage=lambda *a: usage.append(a)) == "答案 F"
        posts = [w for w in web_seen if w[0] == "POST"]
        assert posts[0][2]["tools"] == [WEB_FUNCTION] and "tool_choice" not in posts[0][2]
        gets = [w for w in web_seen if w[0] == "GET"]
        assert len(gets) == 1 and "q=%E4%BB%8A%E5%A4%A9+%E6%96%B0%E9%97%BB" in gets[0][1], gets
        second = posts[1][2]["messages"]
        assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"][0]["id"] == "c1"
        assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "c1" and "https://example.com/a?x=1&y=2" in second[-1]["content"]
        # Claude: 服务端 web_search 工具, 只取最后一次搜索结果之后的文字
        web_seen.clear()
        pa = {"base_url": base3, "model": "m", "protocol": "anthropic", "api_key": "k", "provider": "custom"}
        assert chat_messages(pa, "SYS", m1, web=True, cache=True) == "查到了: 答案 A"
        b = web_seen[-1][2]
        assert web_seen[-1][1] == "/v1/messages" and b["tools"] == [WEB_TOOL_ANTHROPIC] and "thinking" not in b
        # ChatGPT 官方地址: Responses API (地址只能是 api.openai.com, 这里用假的 opener 接住)

        class _Resp:
            def __init__(self, obj):
                self.raw = json.dumps(obj).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n=-1):
                return self.raw
        calls = []

        def fake_open(req, timeout=None):
            calls.append((req.full_url, json.loads(req.data)))
            return _Resp({"output": [{"type": "message", "content": [{"type": "output_text", "text": "答案 R"}]}]})
        po = {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "protocol": "openai", "api_key": "k", "provider": "chatgpt"}
        assert chat_messages(po, "SYS", m1, web=True, opener=fake_open) == "答案 R"
        assert calls[0][0] == "https://api.openai.com/v1/responses"
        assert calls[0][1]["tools"] == [{"type": "web_search"}] and calls[0][1]["instructions"] == "SYS"
        assert calls[0][1]["input"] == m1 and "messages" not in calls[0][1]
        # 接口不认搜索 (400): 不联网再答一次, 系统提示里说明
        web_seen.clear()
        pn = dict(pf, base_url=base3 + "/nosearch")
        try:
            chat_messages(pn, "SYS", m1, web=True)
        except AIError:
            pass                                                   # 这个假服务器连普通聊天也回 400: 只看有没有用不带搜索的请求再试
        plain = [w[2] for w in web_seen if w[0] == "POST"]
        assert "tools" in plain[0] and "tools" not in plain[1] and plain[1]["messages"][0]["content"].endswith(WEB_FAIL_NOTE)
        # web=False: 请求里没有 tools
        web_seen.clear()
        assert chat_messages(pf, "SYS", m1) == "答案 F"
        assert "tools" not in web_seen[-1][2]
    finally:
        _ai_web.SEARCH_URL = saved_url
        srv3.shutdown()

    # 预设服务商的 Key 只发往官方域名
    for prov, url in (("deepseek", "https://evil.example/v1"),
                      ("claude", "https://api.anthropic.com.evil.example")):
        try:
            check_profile({"provider": prov, "base_url": url, "model": "m", "api_key": "k"})
            raise AssertionError
        except AIError as e:
            assert e.kind == "config"
    check_profile({"provider": "deepseek", "base_url": "https://api.deepseek.com/v1",
                   "model": "m", "api_key": "k"})
    check_profile({"provider": "custom", "base_url": "https://relay.example/v1",
                   "model": "m", "api_key": "k"})

    # 存盘加密: 用可逆的假 DPAPI 验证文件里不留明文, 读回来能还原, 旧明文会被迁移
    real_dpapi, real_can = _ai_key._dpapi, _ai_key.can_encrypt
    _ai_key._dpapi = lambda data, protect: bytes(b ^ 0x5A for b in data)
    _ai_key.can_encrypt = lambda: True
    try:
        path = os.path.join(tempfile.mkdtemp(), "ai.json")
        with securefile.open(path, "w", encoding="utf-8") as f:
            json.dump({"provider": "deepseek", "profiles": {"deepseek": {
                "base_url": "https://api.deepseek.com", "model": "m", "protocol": "openai",
                "api_key": "sk-plain-777"}}}, f)
        m = AIConfig(path)
        assert m.profile("deepseek")["api_key"] == "sk-plain-777"
        txt = open(path, encoding="utf-8").read()
        assert "sk-plain-777" not in txt and "dpapi:" in txt, "旧明文 Key 应被加密改存"
        assert AIConfig(path).profile("deepseek")["api_key"] == "sk-plain-777"
        assert decrypt_key("dpapi:!!notbase64") == ""
    finally:
        _ai_key._dpapi, _ai_key.can_encrypt = real_dpapi, real_can

    # 每次请求重新读系统代理 (不是 import 时的那一份)
    real_bo, built = urllib.request.build_opener, []
    urllib.request.build_opener = lambda *a, **k: built.append(1) or real_bo(*a, **k)
    try:
        for _ in range(2):
            try:
                _ai_http._OPENER.open(urllib.request.Request("http://127.0.0.1:9/"), timeout=1)
            except Exception:       # noqa: BLE001
                pass
    finally:
        urllib.request.build_opener = real_bo
    assert len(built) == 2, "每次请求都新建 opener"
    # 系统代理指向的端口拒绝连接 (WinError 10061): 再直连试一次; 直连也不行报最初的错误; 没有系统代理时不重试
    import io
    real = (_ai_http._OPENER, _ai_http._DIRECT, urllib.request.getproxies)

    class _Refuse:
        def open(self, req, timeout=None):
            raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))

    class _Ok:
        n = 0

        def open(self, req, timeout=None):
            self.n += 1
            return io.BytesIO(json.dumps({"data": [{"id": "m-z"}]}).encode())
    prof = {"base_url": "https://x.example/v1", "protocol": "openai", "api_key": "k9", "provider": "custom"}
    try:
        urllib.request.getproxies = lambda: {"https": "http://user:pw@127.0.0.1:7897"}
        assert proxy_hint() == "127.0.0.1:7897", proxy_hint()
        ok = _Ok()
        _ai_http._OPENER, _ai_http._DIRECT = _Refuse(), ok
        assert list_models(prof) == ["m-z"] and ok.n == 1
        _ai_http._DIRECT = _Refuse()
        try:
            list_models(prof)
            raise AssertionError
        except AIError as e:
            assert e.kind == "network" and "连接失败" in e.message
        urllib.request.getproxies = lambda: {}
        assert proxy_hint() == "direct"
        ok = _Ok()
        _ai_http._OPENER, _ai_http._DIRECT = _Refuse(), ok
        try:
            list_models(prof)
            raise AssertionError
        except AIError as e:
            assert e.kind == "network" and ok.n == 0, "没有系统代理: 本来就是直连, 不重试"
    finally:
        _ai_http._OPENER, _ai_http._DIRECT, urllib.request.getproxies = real
    return "ai_assist selftest OK"
