# -*- coding: utf-8 -*-
"""服务商配置 (ai_config.json): 加载 / 保存 / 加密 Key / 选择 / 校验. 依赖 ai_common, ai_key."""

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
from ai_assist import ai_key
from ai_assist.ai_key import decrypt_key, encrypt_key
from ai_assist.ai_common import *

_CONFIGS = {}


_ADAPTIVE_RE = re.compile(r"claude[-_ ]?(opus|sonnet|haiku)[-_ ]?(\d+)(?:[-_.](\d{1,2})(?!\d))?", re.I)


_ADAPTIVE_NAME_RE = re.compile(r"claude[-_ ]?(fable|mythos)", re.I)


def default_profile(provider: str = "custom") -> dict:
    p = PROVIDERS.get(provider, PROVIDERS["custom"])
    fam = provider if provider in FAMILY_PROTOCOL else "other"
    return {"base_url": p["base_url"], "model": p["model"],
            "protocol": p["protocol"], "api_key": "", "family": fam,
            "preset": provider if provider in PROVIDERS else "custom"}


class AIConfig:
    """最多 MAX_CONFIGS 份命名的 API 配置 (各有一份 Base URL / 型号 / Key), 互相独立.

    provider = 默认配置的 id (规则页的 AI 建议 / 其它不分对象的功能用它);
    assign = {人设文件名: 配置 id}, 沟通页给每个 AI 单独选的那一份.
    老版本只有每个服务商一份 (profiles 的键就是服务商), 读进来后照样是 5 份配置, 默认的那份是原来的 provider.
    """

    def __init__(self, path: str, template: str = None):
        self.path = path
        self.template = template
        self.provider = PROVIDER_ORDER[0]
        self.order = list(PROVIDER_ORDER)
        self.names = {k: PROVIDERS[k]["label"] for k in PROVIDER_ORDER}
        self.assign = {}
        self.profiles = {k: default_profile(k) for k in PROVIDER_ORDER}
        self.needs_migration = False
        self.load()
        if self.needs_migration and ai_key.can_encrypt():
            self.save()                           # 把旧版明文 Key 改存为加密

    def load(self):
        from_template = False
        try:
            with securefile.open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:                 # 本机还没有配置 → 默认模板(Key 永远不取)
            data, from_template = self._template_data(), True
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        saved = data.get("profiles")
        if isinstance(saved, dict):
            for k, prof in saved.items():
                if not isinstance(k, str) or not isinstance(prof, dict):
                    continue
                preset = prof.get("preset") if prof.get("preset") in PROVIDERS else (k if k in PROVIDERS else "custom")
                out = default_profile(preset)
                for field in ("base_url", "model", "protocol", "api_key", "family"):
                    if isinstance(prof.get(field), str):
                        out[field] = prof[field]
                enc = prof.get("api_key_enc")
                if isinstance(enc, str) and enc:
                    out["api_key"] = decrypt_key(enc)
                elif out["api_key"] and not from_template:
                    self.needs_migration = True   # 旧版明文 Key: 下次保存时加密
                if from_template:
                    out["api_key"] = ""
                fam = self._norm_family(preset, out)
                out["family"] = fam
                out["protocol"] = FAMILY_PROTOCOL[fam]
                self.profiles[k] = out
        # 列表顺序: 新格式存了 order; 老格式按预设的固定顺序, 然后是文件里多出来的
        order = data.get("order")
        if isinstance(order, list):
            self.order = [k for k in order if isinstance(k, str) and k in self.profiles]
        else:
            self.order = [k for k in PROVIDER_ORDER if k in self.profiles] + [k for k in self.profiles if k not in PROVIDER_ORDER]
        for k in self.profiles:
            if k not in self.order:
                self.order.append(k)
        names = data.get("names")
        if isinstance(names, dict):
            for k, v in names.items():
                if k in self.profiles and isinstance(v, str) and v.strip():
                    self.names[k] = v.strip()
        for k in self.order:
            if not self.names.get(k):
                self.names[k] = (PROVIDERS.get(k) or {}).get("label") or k
        active = data.get("provider") or data.get("active")
        if active in self.profiles:
            self.provider = active
        elif self.order:
            self.provider = self.order[0]
        assign = data.get("assign")
        if isinstance(assign, dict):
            for persona, cid in assign.items():
                if isinstance(persona, str) and cid in self.profiles:
                    self.assign[persona] = cid
        self._trim()

    def _trim(self):
        """最多 MAX_CONFIGS 份: 默认配置一定留着."""
        while len(self.order) > MAX_CONFIGS:
            victim = next((k for k in reversed(self.order) if k != self.provider), None)
            if victim is None:
                break
            self.order.remove(victim)
            self.profiles.pop(victim, None)
            self.names.pop(victim, None)
            self.assign = {p: c for p, c in self.assign.items() if c != victim}
        if not self.order:                        # 配置被删光了: 回到预设
            self.order = list(PROVIDER_ORDER)
            self.profiles = {k: default_profile(k) for k in PROVIDER_ORDER}
            self.names = {k: PROVIDERS[k]["label"] for k in PROVIDER_ORDER}
            self.assign = {}
            self.provider = self.order[0]
        if self.provider not in self.profiles:
            self.provider = self.order[0]

    def _norm_family(self, preset: str, prof: dict) -> str:
        if preset in FAMILY_PROTOCOL:
            return preset
        fam = str(prof.get("family") or "")
        if fam in FAMILY_PROTOCOL:
            return fam
        if str(prof.get("protocol") or "") == "anthropic":
            return "claude"                       # 老的自定义中转: 协议就是它的策略
        if str(prof.get("protocol") or "") == "gemini":
            return "gemini"
        if is_deepseek(prof):
            return "deepseek"
        return "other"

    def _unique_name(self, base: str) -> str:
        base = (base or "").strip() or "配置"
        used = set(self.names.values())
        if base not in used:
            return base
        n = 2
        while f"{base} {n}" in used:
            n += 1
        return f"{base} {n}"

    def _template_data(self):
        if not self.template:
            return None
        try:
            with securefile.open(self.template, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    # ---- 配置列表 ----
    def configs(self) -> list:
        """[(id, 名字, 型号)] 按界面上的顺序; 名字不会重复."""
        return [{"id": k, "name": self.names.get(k) or k, "model": self.profiles[k].get("model") or ""}
                for k in self.order]

    def config_name(self, cid: str = None) -> str:
        cid = cid or self.provider
        return self.names.get(cid) or (PROVIDERS.get(cid) or {}).get("label") or cid or ""

    def config_for(self, persona: str) -> str:
        """这个人设用哪一份配置: 沟通页单独选的, 没选过就用默认的."""
        cid = self.assign.get(persona or "")
        if cid in self.profiles:
            return cid
        if self.provider in self.profiles:
            return self.provider
        return self.order[0] if self.order else ""

    def assign_config(self, persona: str, cid: str):
        if not persona or cid not in self.profiles:
            return
        if cid == self.provider:
            self.assign.pop(persona, None)        # 用默认配置时不用单独记
        else:
            self.assign[persona] = cid

    def set_active(self, cid: str) -> bool:
        if cid not in self.profiles:
            return False
        self.provider = cid
        self.assign = {p: c for p, c in self.assign.items() if c != cid}
        return True

    def add_config(self, name: str = "") -> str:
        """新建一份空白配置 (自定义类型), 返回 id; 已经 10 份 = ""."""
        if len(self.order) >= MAX_CONFIGS:
            return ""
        cid = "cfg%06x" % random.getrandbits(24)
        while cid in self.profiles:
            cid = "cfg%06x" % random.getrandbits(24)
        self.profiles[cid] = default_profile("custom")
        self.order.append(cid)
        self.names[cid] = self._unique_name(name)
        return cid

    def rename_config(self, cid: str, name: str) -> bool:
        if cid not in self.profiles:
            return False
        name = (name or "").strip()
        if not name or any(k != cid and v == name for k, v in self.names.items()):
            return False
        self.names[cid] = name
        return True

    def remove_config(self, cid: str) -> bool:
        if cid not in self.profiles or len(self.order) <= 1:
            return False
        self.order.remove(cid)
        self.profiles.pop(cid, None)
        self.names.pop(cid, None)
        self.assign = {p: c for p, c in self.assign.items() if c != cid}
        if self.provider == cid:
            self.provider = self.order[0]
        return True

    def save(self) -> bool:
        tmp = self.path + ".tmp"
        try:
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            profiles = {}
            for k, prof in self.profiles.items():
                out = dict(prof)
                key = out.get("api_key") or ""
                if key and ai_key.can_encrypt():
                    try:
                        out["api_key_enc"] = encrypt_key(key)
                        out["api_key"] = ""          # 文件里不留明文
                    except OSError:
                        pass                          # 加密失败: 退回明文, 至少不丢 Key
                profiles[k] = out
            with securefile.open(tmp, "w", encoding="utf-8") as f:
                json.dump({"provider": self.provider, "order": list(self.order), "names": dict(self.names),
                           "profiles": profiles, "assign": dict(self.assign)},
                          f, ensure_ascii=False, indent=1)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)
            return True
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False

    # ---- 单份配置 ----
    def profile(self, cid: str = None) -> dict:
        """一份配置的接口参数 (provider 是它的预设类型, 用来决定 Key 只能发往哪 / 缓存方式; config_id 是配置 id)."""
        cid = cid or self.provider
        prof = self.profiles.get(cid) or default_profile()
        preset = prof.get("preset") if prof.get("preset") in PROVIDERS else "custom"
        return dict(prof, provider=preset, config_id=cid)

    def set_profile(self, cid: str, **fields):
        prof = self.profiles.get(cid)
        if prof is None:
            return
        for k, v in fields.items():
            if k in prof and isinstance(v, str):
                prof[k] = v.strip()
        if fields.get("preset") in PROVIDERS:
            prof["preset"] = fields["preset"]
        preset = prof.get("preset") or "custom"
        # 模型类型决定协议: 预设服务商固定是它自己, 自定义配置必须由用户选了才算. 不再接受单独的协议参数.
        if preset in FAMILY_PROTOCOL:
            fam = preset
        else:
            fam = prof.get("family") or "other"
            if fam not in FAMILY_PROTOCOL:
                fam = "other"
        prof["family"] = fam
        prof["protocol"] = FAMILY_PROTOCOL[fam]
        # 预设服务商的 Base URL 留空时,自动回到预设值;自定义允许留空
        if preset != "custom" and not prof["base_url"]:
            prof["base_url"] = PROVIDERS[preset]["base_url"]


def shared_config(path: str, template: str = None) -> "AIConfig":
    """同一个文件只有一份 AIConfig: 规则页和沟通页都改模型设置, 不能各拿一份旧的互相覆盖."""
    key = os.path.abspath(path)
    cfg = _CONFIGS.get(key)
    if cfg is None:
        cfg = _CONFIGS[key] = AIConfig(path, template=template)
    return cfg


def family_of(profile: dict) -> str:
    """这个配置是哪一类模型. 存的 family 优先; 没有就按预设供应商 / 协议 / 官方地址认."""
    fam = str(profile.get("family") or "")
    if fam in FAMILY_PROTOCOL:
        return fam
    prov = str(profile.get("provider") or "")
    if prov in FAMILY_PROTOCOL:
        return prov
    if is_deepseek(profile):
        return "deepseek"
    proto = str(profile.get("protocol") or "")
    if proto == "anthropic":
        return "claude"
    if proto == "gemini":
        return "gemini"
    return "other"


def cache_plan(profile: dict) -> dict:
    """提示缓存的策略: 一家只走自己的一条路, 不把别家的机制混进来.
      claude   显式 cache_control (随第一次请求写好, 第二次调用不用等);
      chatgpt  OpenAI 自动前缀缓存, 带 prompt_cache_key 让同一前缀落到同一台机器, 第二次调用不用等;
      gemini   隐式缓存: systemInstruction 必须逐字节相同, 所以动态内容 (记忆卡等) 不能放进 system,
               要贴到最后一条用户消息里; 缓存由 Google 自己建, 第二次调用不用等;
      deepseek 自动前缀缓存, 磁盘缓存是异步落盘的, 第二次调用等几秒;
      other    不假设有缓存 (中转 / 本地模型), 第二次调用照旧等几秒."""
    fam = family_of(profile)
    return {"family": fam,
            "explicit": fam == "claude",          # 只有 Anthropic 协议要标 cache_control
            "key": fam == "chatgpt",              # 只有 OpenAI 认 prompt_cache_key
            "static_system": fam == "gemini",     # Gemini: system 不能掺任何动态内容
            "delay": {"claude": 0.0, "chatgpt": 0.0, "gemini": 0.0,
                      "deepseek": 3.0, "other": 3.0}[fam]}


def cache_key(system: str, model: str = "") -> str:
    """prompt_cache_key: 同一段稳定的 system + 同一个模型 → 同一个键 (OpenAI 按它把请求路由到同一台机器)."""
    import hashlib
    return hashlib.sha256((str(model or "") + "\n" + str(system or "")).encode("utf-8")).hexdigest()[:40]


def check_profile(profile: dict):
    """配置不完整 / 不安全就抛 AIError(config).API Key 绝不通过明文 http 发出去(本机地址除外)."""
    url = (profile.get("base_url") or "").strip()
    if not url:
        raise AIError("config", "Base URL 为空")
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or "").lower()
    if u.scheme == "https" and host:
        pass
    elif u.scheme == "http" and host in ("localhost", "127.0.0.1", "::1"):
        pass
    else:
        raise AIError("config", "Base URL 必须是 https:// 开头(本机模型可用 http://localhost)")
    pinned = official_host(profile.get("provider") or "")
    if pinned and host != pinned:
        raise AIError("config", "为防止 Key 泄露,预设服务商的 Key 只会发往它的官方地址 {host}."
                                "要用其他地址(如中转),请选择「自定义供应商」.".replace("{host}", pinned))
    if not (profile.get("model") or "").strip():
        raise AIError("config", "模型名为空")
    if not (profile.get("api_key") or "").strip() and host not in ("localhost", "127.0.0.1", "::1"):
        raise AIError("config", "还没有填 API Key")


def is_deepseek(profile: dict) -> bool:
    """是不是 DeepSeek 的官方地址. 只有它认下面关思考的两个字段 (中转 / 别家可能直接 400)."""
    try:
        host = (urllib.parse.urlsplit(str(profile.get("base_url") or "")).hostname or "").lower()
    except ValueError:
        return False
    return host == "deepseek.com" or host.endswith(".deepseek.com")


def adaptive_thinking(profile: dict) -> bool:
    """这个模型开思考要用 {"type": "adaptive"}: Claude 4.6 及以后的 opus / sonnet / haiku (含 5.x) 和 fable / mythos.
    它们不认 {"type": "enabled", "budget_tokens": N} (Haiku 5.5 / Sonnet 5.x / Opus 5.x / 4.7 / 4.8 / Fable 直接 400),
    4.6 上 budget_tokens 已弃用. 更老的 Claude (haiku 4.5, sonnet 4.5, claude-3 …) 和别家的模型仍然用 budget_tokens."""
    model = str(profile.get("model") or "")
    if _ADAPTIVE_NAME_RE.search(model):
        return True
    m = _ADAPTIVE_RE.search(model)
    return bool(m) and (int(m.group(2)), int(m.group(3) or 0)) >= (4, 6)


def think_on_field(profile: dict) -> dict:
    """开思考的 thinking 字段 (非 DeepSeek): 新模型 adaptive, 其他 enabled + budget_tokens."""
    return {"type": "adaptive"} if adaptive_thinking(profile) else {"type": "enabled", "budget_tokens": THINK_BUDGET}
