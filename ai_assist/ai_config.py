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
    fam = p.get("family") or (provider if provider in FAMILY_PROTOCOL else "other")
    return {"base_url": p.get("base_url", ""), "model": p["model"],
            "protocol": p["protocol"], "api_key": "", "family": fam,
            "kind": p.get("kind", "api"), "command": p.get("command", ""), "effort": p.get("effort", "auto"),
            "preset": provider if provider in PROVIDERS else "custom"}


def device_profile(device_id: str, device_name: str) -> dict:
    """构造一份不含密钥的设备代理配置。"""
    name = str(device_name or device_id or "设备").strip()[:80] or "设备"
    return {"base_url": "", "model": f"由{name}处理", "protocol": "device",
            "api_key": "", "family": "other", "kind": "device", "command": "",
            "effort": "auto", "preset": "device", "target_device": str(device_id or ""),
            "target_name": name}


class AIConfig:
    """最多 MAX_CONFIGS 份命名的 API 配置 (各有一份 Base URL / 型号 / Key), 互相独立.

    provider = 默认配置的 id (规则页的 AI 建议 / 其它不分对象的功能用它);
    assign = {人设文件名: 配置 id}, 沟通页给每个 AI 单独选的那一份.
    老版本只有每个服务商一份 (profiles 的键就是服务商), 读进来后照样是 5 份配置, 默认的那份是原来的 provider.
    """

    def __init__(self, path: str, template: str = None):
        self.path = path
        self.template = template
        self.provider = "claude" if "claude" in PROVIDER_ORDER else PROVIDER_ORDER[0]
        self.order = list(PROVIDER_ORDER)
        self.names = {k: PROVIDERS[k]["label"] for k in self.order}
        self.assign = {}
        self.task_assign = {}
        self.profiles = {k: default_profile(k) for k in self.order}
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
            # 带 order 的命名配置是完整列表, 不能补回已删除的预设; 旧格式仍按预设补全。
            if isinstance(data.get("order"), list):
                self.profiles = {}
                self.names = {}
            for k, prof in saved.items():
                if not isinstance(k, str) or not isinstance(prof, dict):
                    continue
                preset = self._profile_preset(k, prof)
                out = default_profile(preset)
                for field in ("base_url", "model", "protocol", "api_key", "family", "kind", "command", "effort", "preset", "target_device", "target_name"):
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
                out["preset"] = preset
                if preset in CLI_PROVIDERS:
                    out["kind"], out["protocol"] = "cli", "cli"
                    out["base_url"], out["api_key"] = "", ""
                    out["family"] = PROVIDERS[preset].get("family") or "other"
                    out["command"] = out.get("command") or PROVIDERS[preset].get("command", "")
                elif preset == "device":
                    out["kind"], out["protocol"] = "device", "device"
                    out["api_key"], out["base_url"] = "", ""
                    out["target_device"] = str(out.get("target_device") or "")
                    out["target_name"] = str(out.get("target_name") or out.get("model") or "设备")
                    out["model"] = f"由{out['target_name']}处理"
                else:
                    out["kind"] = "api"
                    out["protocol"] = (out.get("protocol") if preset == "custom" and out.get("protocol") in PROTOCOLS and out.get("protocol") != "cli"
                                       else FAMILY_PROTOCOL[fam])
                self.profiles[k] = out
            if isinstance(data.get("order"), list) and not data.get("order") and not self.profiles:
                self.order = self._available_order()
                self.profiles = {k: default_profile(k) for k in self.order}
                self.names = {k: PROVIDERS[k]["label"] for k in self.order}
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
        tasks = data.get("tasks")
        if isinstance(tasks, dict):
            self.task_assign = {role: cid for role, cid in tasks.items()
                                if role in ("ocr", "review", "subject") and cid in self.profiles}
        self._ensure_managed_profiles()
        self._remove_unavailable_cli()
        self._trim()

    @staticmethod
    def _profile_preset(key: str, prof: dict) -> str:
        """从存档恢复预设；旧版自动生成的 CLI 配置可能没有 preset 字段。"""
        saved = prof.get("preset")
        if saved == "device" or prof.get("kind") == "device" or prof.get("protocol") == "device":
            return "device"
        if saved in CLI_PROVIDERS:
            return saved
        if key in CLI_PROVIDERS:
            return key
        command = str(prof.get("command") or "").strip().lower()
        for preset in CLI_PROVIDERS:
            if command == str(PROVIDERS[preset].get("command") or "").lower():
                return preset
        if saved in PROVIDERS:
            return saved
        return key if key in PROVIDERS else "custom"

    def _ensure_managed_profiles(self):
        """已安装的本地客户端始终出现在模型配置里，卸载后才允许删除。"""
        from .ai_cli import installed
        for preset in CLI_PROVIDERS:
            if preset in self.profiles or not installed({"command": PROVIDERS[preset].get("command", "")}):
                continue
            self.profiles[preset] = default_profile(preset)
            self.names[preset] = PROVIDERS[preset]["label"]
            self.order.insert(0, preset)

    @staticmethod
    def _available_order():
        from .ai_cli import installed
        return [k for k in PROVIDER_ORDER
                if k not in CLI_PROVIDERS or installed({"command": PROVIDERS[k].get("command", "")})]

    def _remove_unavailable_cli(self):
        """CLI 被卸载后不显示其托管配置；重新安装时由上面的补齐逻辑恢复。"""
        from .ai_cli import installed
        gone = []
        for cid, prof in list(self.profiles.items()):
            preset = self._profile_preset(cid, prof)
            if preset in CLI_PROVIDERS and not installed({"command": PROVIDERS[preset].get("command", "")}):
                gone.append(cid)
        if not gone:
            return
        for cid in gone:
            self.profiles.pop(cid, None)
            self.names.pop(cid, None)
            self.assign = {p: c for p, c in self.assign.items() if c != cid}
            self.task_assign = {r: c for r, c in self.task_assign.items() if c != cid}
        self.order = [cid for cid in self.order if cid not in gone]

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
            self.task_assign = {role: cid for role, cid in self.task_assign.items() if cid != victim}
        if not self.order:                        # 配置被删光了: 回到预设
            self.order = self._available_order()
            self.profiles = {k: default_profile(k) for k in self.order}
            self.names = {k: PROVIDERS[k]["label"] for k in self.order}
            self.assign = {}
            self.task_assign = {}
            self.provider = self.order[0]
        if self.provider not in self.profiles:
            self.provider = self.order[0]

    def refresh_devices(self, devices) -> bool:
        """按当前绑定设备刷新代理配置；设备解绑或消失后立即移除。"""
        wanted = {}
        for item in devices or ():
            if isinstance(item, dict):
                did, name = item.get("id"), item.get("name")
            elif isinstance(item, (tuple, list)) and item:
                did, name = item[0], item[1] if len(item) > 1 else item[0]
            else:
                continue
            did = str(did or "").strip()
            if did:
                wanted[f"device_{did}"] = (did, str(name or did).strip()[:80] or did)
        changed = False
        for cid in list(self.profiles):
            prof = self.profiles.get(cid) or {}
            if self._profile_preset(cid, prof) == "device" and cid not in wanted:
                self.profiles.pop(cid, None)
                self.names.pop(cid, None)
                self.order = [key for key in self.order if key != cid]
                self.assign = {p: c for p, c in self.assign.items() if c != cid}
                self.task_assign = {r: c for r, c in self.task_assign.items() if c != cid}
                changed = True
        for cid, (did, name) in wanted.items():
            prof = self.profiles.get(cid)
            fresh = device_profile(did, name)
            if not isinstance(prof, dict):
                self.profiles[cid] = fresh
                self.names[cid] = f"由{name}处理"
                self.order.append(cid)
                changed = True
                continue
            if prof.get("target_name") != name or prof.get("model") != fresh["model"]:
                prof.update(fresh)
                self.names[cid] = f"由{name}处理"
                changed = True
        self._trim()
        if changed:
            self.save()
        return changed

    def _norm_family(self, preset: str, prof: dict) -> str:
        if preset in CLI_PROVIDERS:
            return PROVIDERS[preset].get("family") or "other"
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
        """新建一份空白配置 (自定义类型), 返回 id; 已经达到上限 = ""."""
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
        prof = self.profiles.get(cid) or {}
        if prof.get("kind") in ("cli", "device") or prof.get("preset") in CLI_PROVIDERS or prof.get("preset") == "device":
            from .ai_cli import installed
            if prof.get("kind") == "device" or installed(prof):
                return False
        self.order.remove(cid)
        self.profiles.pop(cid, None)
        self.names.pop(cid, None)
        self.assign = {p: c for p, c in self.assign.items() if c != cid}
        self.task_assign = {role: value for role, value in self.task_assign.items() if value != cid}
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
                           "profiles": profiles, "assign": dict(self.assign), "tasks": dict(self.task_assign)},
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
        preset = self._profile_preset(cid, prof)
        return dict(prof, preset=preset, provider=preset, config_id=cid)

    def task_profile(self, role: str, fallback: str = None) -> dict:
        """返回视觉/OCR/评判专用配置；未指定时沿用聊天当前配置。"""
        return self.profile(self.task_assign.get(role) or fallback or self.provider)

    def assign_task(self, role: str, cid: str = "") -> bool:
        if role not in ("ocr", "review", "subject") or (cid and cid not in self.profiles):
            return False
        if cid:
            self.task_assign[role] = cid
        else:
            self.task_assign.pop(role, None)
        return True

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
        # 预设协议固定; 自定义中转可独立选协议, 切换模型类型时默认匹配该类型。
        if preset in CLI_PROVIDERS:
            prof["kind"] = "cli"
            prof["family"] = PROVIDERS[preset].get("family") or "other"
            prof["protocol"] = "cli"
            prof["base_url"] = ""
            prof["api_key"] = ""
            prof["command"] = prof.get("command") or PROVIDERS[preset].get("command", "")
            return
        if preset == "device":
            prof.update(device_profile(prof.get("target_device", ""), prof.get("target_name", "设备")))
            return
        prof["kind"] = "api"
        if preset in FAMILY_PROTOCOL:
            fam = preset
        else:
            fam = prof.get("family") or "other"
            if fam not in FAMILY_PROTOCOL:
                fam = "other"
        prof["family"] = fam
        protocol = fields.get("protocol")
        if preset == "custom" and protocol in PROTOCOLS and protocol != "cli":
            prof["protocol"] = protocol
        elif preset != "custom" or "family" in fields:
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
    if str(profile.get("kind") or "") == "cli" or str(profile.get("protocol") or "") == "cli":
        from .ai_cli import installed
        if not installed(profile):
            raise AIError("config", "没有找到 Claude Code，请先安装并登录.")
        return
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
