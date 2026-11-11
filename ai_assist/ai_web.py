# -*- coding: utf-8 -*-
"""联网搜索: OIT 自己抓 Bing (给没有搜索接口的模型用). 依赖 ai_common.

三家的联网方式不一样:
  Claude (anthropic 协议): 服务端工具 web_search_20250305, 搜索由 Anthropic 完成, 回复里直接带结果;
  ChatGPT (api.openai.com): Responses API (POST /responses) 的 web_search 工具, 同样由 OpenAI 完成;
  DeepSeek 和别的 OpenAI 兼容接口 (中转 / 本地模型): 官方没有搜索接口, 用函数调用 (tools: web_search),
  模型要搜时由 OIT 自己去搜 (Bing 搜索页, 不要 Key), 把结果交回去让它接着答.
"""

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

SEARCH_URL = "https://www.bing.com/search"          # 自己搜索用的页面 (测试里换成本机地址). 不能用 cn.bing.com/search: 它会 302 到首页, 搜不到任何结果 (1.0.349 的 bug)


SEARCH_HOSTS = ("cn.bing.com", "www.bing.com")      # 自己搜索会访问的域名: 过滤代理对它们放行 (只在网络开通期间, app._api_hosts)


SEARCH_RESULTS = 5


SEARCH_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0")


def web_mode(profile: dict) -> str:
    """这个服务商用哪种方式联网: "gemini" (原生 google_search) / "responses" (ChatGPT 官方) /
    "anthropic" (Claude 的服务端工具) / "function" (DeepSeek 等: OIT 自己搜)."""
    if profile.get("protocol") == "gemini":
        return "gemini"
    try:
        host = (urllib.parse.urlsplit(str(profile.get("base_url") or "")).hostname or "").lower()
    except ValueError:
        host = ""
    if host == "api.openai.com":
        return "responses"
    if profile.get("protocol") == "anthropic":
        return "anthropic"
    return "function"


def web_local(profile: dict) -> bool:
    """联网时是不是由 OIT 自己去搜 (这时要访问 SEARCH_HOSTS)."""
    return web_mode(profile) == "function"


def _clean_html(s: str) -> str:
    import html
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def _bing_url(href: str) -> str:
    """Bing 的结果链接有时是 .../ck/a?...&u=a1<base64>: 还原成真正的地址."""
    try:
        u = urllib.parse.urlsplit(href)
        if u.path.startswith("/ck/a"):
            v = (urllib.parse.parse_qs(u.query).get("u") or [""])[0]
            if v.startswith("a1"):
                raw = v[2:]
                return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "replace")
    except Exception:       # noqa: BLE001
        pass
    return href


def parse_search(page: str) -> list:
    """Bing 搜索页 → [(标题, 链接, 摘要)] (最多 SEARCH_RESULTS 条)."""
    out = []
    for item in re.findall(r'<li[^>]*class="[^"]*\bb_algo\b[^"]*"[^>]*>(.*?)</li>', page or "", re.S):
        a = re.search(r'<h2[^>]*>\s*<a\b[^>]*?href="([^"]+)"[^>]*>(.*?)</a>', item, re.S)
        if not a:
            continue
        p = re.search(r'<p\b[^>]*>(.*?)</p>', item, re.S)
        import html
        out.append((_clean_html(a.group(2))[:120], _bing_url(html.unescape(a.group(1)))[:300],
                    _clean_html(p.group(1) if p else "")[:240]))
        if len(out) >= SEARCH_RESULTS:
            break
    return out


def web_search(query: str, timeout: float = 15.0, opener=None) -> str:
    """OIT 自己搜索 (DeepSeek 等没有联网接口的模型用): 返回给模型看的结果文字. 失败抛 AIError."""
    q = re.sub(r"\s+", " ", str(query or "")).strip()[:200]
    if not q:
        raise AIError("format", "搜索关键词为空")
    url = SEARCH_URL + "?" + urllib.parse.urlencode({"q": q, "setlang": "zh-hans"})
    req = urllib.request.Request(url, headers={"User-Agent": SEARCH_UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6"})
    try:
        with _open_search(req, timeout, opener) as resp:
            page = resp.read(1_500_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 403 and e.headers and e.headers.get("X-OIT-Reason"):
            raise AIError("blocked", "搜索请求被本程序的过滤代理拦截")
        raise AIError("http", f"搜索服务返回 {e.code}")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise AIError("network", f"搜索连接失败:{getattr(e, 'reason', e)}")
    res = parse_search(page)
    if not res:
        return "(没有搜到结果)"
    return "\n".join(f"{i}. {t}\n   {u}\n   {s}" for i, (t, u, s) in enumerate(res, 1))


def _open_search(req, timeout, opener=None):
    """搜索页可以跟随跳转 (不带任何 Key); 其余同 _open_url."""
    if opener is not None:
        return opener(req, timeout=timeout)
    try:
        return urllib.request.build_opener().open(req, timeout=timeout)
    except urllib.error.URLError as e:
        if not isinstance(getattr(e, "reason", None), ConnectionRefusedError) or not urllib.request.getproxies():
            raise
        return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout)
