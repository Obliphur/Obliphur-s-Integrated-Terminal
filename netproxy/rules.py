"""
规则库:域名 / 应用 × 白名单 / 黑名单
────────────────────────────────────
语义:过滤方式二选一,只有当前方式对应的名单会生效.

    黑名单模式   默认放行,离线时只拦黑名单里的域名 / 应用;联网会话中全部放行
    白名单模式   默认拦截,离线时只放行网站白名单里的域名 (不管是哪个程序发起的);联网会话中全部放行.
                 应用白名单只决定哪些程序的窗口能开着 (app_allowed, MECHANICS 4.7), 不给联网开后门 ——
                 以前应用白名单里的程序可以连任何网站, 把浏览器放进去 (否则窗口会被关掉) 就等于不拦网站了
    (两种模式下联网申请都是为了解除限制, 见 MECHANICS 4.1)

名单改动的等待规则(黑 / 白名单相同):
    任何加入和删除都要等 CHANGE_DELAY_HOURS 小时才生效.
    dev_getter() 为真时(开发者模式 / 配置期)一切改动立即生效.

网站白名单里的"组" (MECHANICS 4.9): 加入一个网站时, OIT 会去连它 (netproxy/sitescan.py), 它引发的其他网站和它一起加入白名单,
    记成这个网站名下的一组 (groups: 主网站 → [引发的网站]). 引发的网站本身就是白名单里普通的条目 (判定、冷静期、删除都和别的条目一样),
    组只管显示 (Rules 页里归在主网站下面, 点开才看到) 和连带操作: 删除 / 取消主网站时, 只属于它的引发的网站一起删除 / 取消.

按 UP 主区分 (MECHANICS 4.8, 仅 bilibili.com): UP 主名单只有白名单, 两种模式下都是"只放行这些 UP 主" ——
    白名单里的 bilibili.com + UP 主白名单 → 只放行这些 UP 主的主页和视频, 其余的 B 站页面拦截 (名单是空的就整站放行);
    黑名单里的 bilibili.com + UP 主白名单 → 整站拦, 只有这些 UP 主的主页和视频例外放行 (名单是空的就整站拦).
    代理看不到 HTTPS 的路径, 是哪个 UP 主的页面由 up_probe (浏览器地址栏, netproxy/upgate.py) 告诉 decide().

本模块不依赖 tkinter / config,可以单独测试.
"""

import json
import os
import re
import threading
from datetime import datetime, timedelta
try:
    import securefile
except ImportError:                      # 直接运行本文件做自检时: 上一层才有
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    import securefile

WHITE_DOMAINS = "white_domains"
WHITE_APPS = "white_apps"
BLACK_DOMAINS = "black_domains"
BLACK_APPS = "black_apps"
# UP 主白名单 (条目 "bilibili.com/UID"), 两种模式共用: 该站点只放行这些 UP 主的主页和视频
# (旧版本有过 black_ups, 现在不读, 下次存盘时丢掉)
WHITE_UPS = "white_ups"
LIST_KEYS = (WHITE_DOMAINS, WHITE_APPS, BLACK_DOMAINS, BLACK_APPS, WHITE_UPS)
UP_SITES = ("bilibili.com",)             # 支持按 UP 主区分的站点
# 白名单里的这个域名连带放行的域名 (图片 / 视频流 / 接口): 只写 bilibili.com 的话视频放不出来
SITE_FAMILY = {
    "bilibili.com": ("bilibili.com", "bilivideo.com", "bilivideo.cn", "hdslb.com", "biliapi.net", "biliapi.com",
                     "acgvideo.com"),
}

MODE_BLACKLIST = "blacklist"
MODE_WHITELIST = "whitelist"
# 代理-全屏蔽 (Proxy block-all) (断网方式 proxy_all): 联网判定不看名单, 离线时除本机地址外全部拦截,
# 联网会话中全部放行. 应用名单照样按过滤名单模式 (黑 / 白) 关窗口, 只在专注 / 空闲时, 和联不联网无关 (app_allowed, MECHANICS 3.2.1 / 4.7)
MODE_BLOCKALL = "blockall"
# Claude Code 的连接 (API / 登录 / 更新): 任何模式都放行, 只有合约的网络禁令能拦 (MECHANICS 4.1)
CLAUDE_CODE_DOMAINS = ("anthropic.com", "claude.ai")

CHANGE_DELAY_HOURS = 48
LOOSEN_DELAY_HOURS = CHANGE_DELAY_HOURS   # 旧名字, 保留兼容

_LOOPBACK = {"localhost", "::1", "0.0.0.0"}


def keys_for_mode(mode: str) -> tuple:
    return (WHITE_DOMAINS, WHITE_APPS) if mode == MODE_WHITELIST else (BLACK_DOMAINS, BLACK_APPS)


def is_up(key: str) -> bool:
    return key == WHITE_UPS


def is_white(key: str) -> bool:
    return key in (WHITE_DOMAINS, WHITE_APPS)


def is_app(key: str) -> bool:
    return key in (WHITE_APPS, BLACK_APPS)


def norm_domain(text) -> str:
    """网址 / 域名 → 规范主机名;不合法返回 ''.`*.a.com`,`https://a.com/x` 都归一成 `a.com`."""
    t = str(text or "").strip().lower()
    if not t:
        return ""
    t = re.sub(r'^[a-z][a-z0-9+.\-]*://', '', t)
    t = re.split(r'[/?#]', t, maxsplit=1)[0]
    t = t.split('@')[-1]
    if t.startswith('['):
        end = t.find(']')
        host = t[1:end] if end > 0 else ""
    else:
        host = t.split(':')[0]
    host = host.strip('.')
    if host.startswith('*.'):
        host = host[2:]
    if not host or len(host) > 253:
        return ""
    if not re.fullmatch(r'[a-z0-9\-._]+|[0-9a-f:]+', host):
        return ""
    if '.' not in host and ':' not in host:
        return ""
    return host


def norm_app(text) -> str:
    """程序名 / 完整路径 → 小写 exe 文件名;`chrome` → `chrome.exe`."""
    t = str(text or "").strip().strip('"').replace('/', '\\')
    t = t.rsplit('\\', 1)[-1].strip().lower()
    if not t:
        return ""
    if not t.endswith('.exe'):
        t += '.exe'
    if len(t) > 100 or len(t) <= 4 or re.search(r'[<>:"|?*\x00-\x1f]', t):
        return ""
    return t


def up_site(rule: str) -> str:
    """名单里的这个域名条目属于哪个支持按 UP 主区分的站点 (bilibili.com, space.bilibili.com … → bilibili.com); 没有返回 ''."""
    rule = str(rule or "")
    for site in UP_SITES:
        if rule == site or rule.endswith('.' + site):
            return site
    return ""


def bili_uid(text) -> str:
    """UP 主主页链接 / UID → UID 数字串; 认不出来返回 ''.
    `https://space.bilibili.com/2742391?spm_id_from=…`, `space.bilibili.com/2742391/video`, `2742391`, `UID:2742391` 都行."""
    t = str(text or "").strip()
    m = (re.fullmatch(r'(?:uid|mid)?\s*[:=：]?\s*(\d{1,20})', t, re.I)
         or re.match(r'^(?:[a-z][a-z0-9+.\-]*://)?(?:space\.bilibili\.com/|(?:www\.|m\.)?bilibili\.com/space/)(\d{1,20})(?!\d)',
                     t, re.I))
    if not m:
        return ""
    uid = str(int(m.group(1)))
    return "" if uid == "0" else uid


def norm_up(text) -> str:
    """UP 主主页链接 / UID → "bilibili.com/UID" (名单里存的样子); 认不出来返回 ''."""
    t = str(text or "").strip()
    m = re.fullmatch(r'bilibili\.com/(\d{1,20})', t, re.I)
    uid = bili_uid(m.group(1) if m else t)
    return f"bilibili.com/{uid}" if uid else ""


def normalize(key: str, text) -> str:
    if is_up(key):
        return norm_up(text)
    return norm_app(text) if is_app(key) else norm_domain(text)


def domain_match(host: str, rule: str) -> bool:
    return host == rule or host.endswith('.' + rule)


def family_match(host: str, rule: str) -> bool:
    """网站白名单条目 rule 放行不放行 host: 域名本身和子域名, 加上 SITE_FAMILY 里连带的域名 (只对 bilibili.com 这样的整站条目)."""
    return domain_match(host, rule) or any(domain_match(host, d) for d in SITE_FAMILY.get(rule, ()))


DEFAULT_TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "defaults", "net_rules.default.json")


class RuleStore:

    def __init__(self, path: str, delay_hours: float = CHANGE_DELAY_HOURS,
                 now=None, template: str = None):
        self.path = path
        self.template = template
        self.delay = timedelta(hours=delay_hours)
        self._now = now or datetime.now
        self.dev_getter = lambda: False          # 开发者模式 / 配置期:改动不用等
        # 过滤名单模式的切换排队中时 (MECHANICS 4.4): 新模式的名单现在还不起作用, 可以提前设置 —— 这些名单的改动立即保存, 不用再等 48 小时
        # (它们要等模式切换生效才起作用, 切换本身已经在等了). app.py 接上: 返回这些名单的 key.
        self.preset_keys = lambda: frozenset()
        # 提前启动的程序 (MECHANICS 20): 不看网站 / 应用名单, 直接放行 (代理-全屏蔽照样拦).
        # 由 app.py 在提前启动期间每秒钟更新成这些程序的 exe 名.
        self.exempt = set()
        self.open_new_except = None      # 提前启动的操作空窗期: 不在这个集合里的程序 (开始时没在运行的) 名单 / 代理-全屏蔽都放行; None = 不开 (合约网络禁令除外, MECHANICS 20)
        # AI 接口的域名 (当前服务商 Base URL 的主机): 名单 / 代理-全屏蔽都放行 (合约网络禁令仍然优先).
        # 由 app.py 每秒按 ai_config.json 更新.
        self.api_hosts = set()
        # 合约的网络禁令 (MECHANICS 18.3): 这些程序禁止联网 (联网会话 / 提前启动都不放行). app.py 每秒更新.
        self.banned = set()
        self.banned_domains = set()              # 同上: 这些网站 (含子域名) 也封锁
        self._lock = threading.RLock()
        self.lists = {k: [] for k in LIST_KEYS}
        self.pending = []
        self.created = None
        self.version = 0
        self._snap = {k: () for k in LIST_KEYS}
        self._ups = {}                           # (名单, 站点) -> UID 集合
        self.groups = {}                         # 主网站 -> [它引发的网站]: 只有网站白名单 (WHITE_DOMAINS) 有组
        # 浏览器此刻开着哪些页面 (netproxy/upgate.py UpGate): allowed(site, exe, uids). None = 不支持, 一律按整站名单
        self.up_probe = None
        self.load()
        self.promote_due()


    def load(self):
        raw = {}
        try:
            with securefile.open(self.path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
        except FileNotFoundError:
            raw = self._template_raw()           # 本机还没有名单文件 → 用默认模板
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        lists = raw.get("lists") if isinstance(raw.get("lists"), dict) else {}
        for k in LIST_KEYS:
            seen, out = set(), []
            for item in lists.get(k) or []:
                n = normalize(k, item)
                if n and n not in seen:
                    seen.add(n)
                    out.append(n)
            self.lists[k] = out
        self.pending = []
        for p in raw.get("pending") or []:
            try:
                op, key = p["op"], p["list"]
                item = normalize(key, p["item"])
                at = datetime.fromisoformat(p["apply_at"])
            except Exception:
                continue
            if op in ("add", "remove") and key in LIST_KEYS and item:
                self.pending.append({"op": op, "list": key, "item": item, "apply_at": at})
        try:
            self.created = datetime.fromisoformat(raw["created"])
        except Exception:
            self.created = None
        self.groups = {}
        if isinstance(raw.get("groups"), dict):
            for parent, kids in raw["groups"].items():
                p = norm_domain(parent)
                ks = [k for k in (norm_domain(x) for x in kids if isinstance(x, str)) if k and k != p] \
                    if isinstance(kids, list) else []
                if p and ks:
                    self.groups[p] = list(dict.fromkeys(ks))
        self._rebuild()

    def _template_raw(self) -> dict:
        """默认模板只提供名单本身;created / pending 不取,初始化窗口从第一次改动算起."""
        if not self.template:
            return {}
        try:
            with securefile.open(self.template, 'r', encoding='utf-8') as f:
                raw = json.load(f)
        except Exception:
            return {}
        lists = raw.get("lists") if isinstance(raw, dict) else None
        return {"lists": lists} if isinstance(lists, dict) else {}

    def _save(self):
        payload = {
            "created": (self.created.isoformat(timespec="seconds")
                        if self.created else None),
            "lists": self.lists,
            "groups": self.groups,
            "pending": [
                {"op": p["op"], "list": p["list"], "item": p["item"],
                 "apply_at": p["apply_at"].isoformat(timespec="seconds")}
                for p in self.pending
            ],
        }
        tmp = self.path + ".tmp"
        try:
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            with securefile.open(tmp, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    def _rebuild(self):
        self._snap = {k: tuple(v) for k, v in self.lists.items()}
        ups = {}
        for item in self._snap[WHITE_UPS]:
            site, _, uid = item.partition('/')
            ups.setdefault((WHITE_UPS, site), set()).add(uid)
        self._ups = {k: frozenset(v) for k, v in ups.items()}
        self._prune_groups()
        self.version += 1

    def _live(self, item: str) -> bool:
        """网站白名单里有这个条目, 或者它的加入在排队中."""
        return item in self.lists[WHITE_DOMAINS] or self._find_pending("add", WHITE_DOMAINS, item) >= 0

    def _prune_groups(self):
        """组里已经不在名单里 (也不在排队加入) 的网站去掉; 主网站没了 / 组空了, 组也去掉."""
        out = {}
        for parent, kids in self.groups.items():
            if not self._live(parent):
                continue
            ks = [k for k in kids if self._live(k)]
            if ks:
                out[parent] = ks
        self.groups = out

    def children(self, parent: str, active_only: bool = True) -> list:
        """主网站引发的网站 (组): 默认只给已经生效的 (在名单里的); active_only=False 连排队中的也给."""
        kids = self.groups.get(parent, [])
        return [k for k in kids if (k in self.lists[WHITE_DOMAINS] if active_only else self._live(k))]

    def tree(self) -> list:
        """网站白名单按组排好: [(条目, [它名下已生效的引发的网站]), …]. 在别的主网站名下的条目不单独列出."""
        active = self.items(WHITE_DOMAINS)
        nested = {k for p, ks in self.groups.items() if p in active for k in ks}
        return [(item, self.children(item)) for item in active if item not in nested]

    def exclusive_children(self, parent: str) -> list:
        """只属于这个主网站的引发的网站 (别的还在的主网站名下没有它)."""
        return [k for k in self.groups.get(parent, [])
                if not any(k in ks for p, ks in self.groups.items() if p != parent and self._live(p))]


    def items(self, key: str) -> list:
        return list(self._snap[key])

    def up_uids(self, key: str, site: str) -> frozenset:
        """这个站点的 UP 主名单 (key = WHITE_UPS) 里的 UID."""
        return self._ups.get((key, site), frozenset())

    def up_items(self, key: str, site: str) -> list:
        """站点的 UP 主名单, 按加入的先后: [UID, …]."""
        prefix = site + '/'
        return [i[len(prefix):] for i in self._snap[key] if i.startswith(prefix)]

    def _find_pending(self, op, key, item):
        for i, p in enumerate(self.pending):
            if p["op"] == op and p["list"] == key and p["item"] == item:
                return i
        return -1

    def _drop_pending(self, op, key, item):
        i = self._find_pending(op, key, item)
        if i >= 0:
            del self.pending[i]
            return True
        return False

    def pending_for(self, key: str, item: str):
        for p in self.pending:
            if p["list"] == key and p["item"] == item:
                return p
        return None

    def _stage(self, op, key, item, at=None):
        self.pending.append({"op": op, "list": key, "item": item,
                             "apply_at": at or self._now() + self.delay})
        self._save()


    def add(self, key: str, raw, at=None):
        """返回 (status, item).
        status: applied 立即生效(仅开发者模式 / 配置期) / staged 进入等待 /
                cancelled 取消了排队中的删除 /
                exists 已在名单里 / invalid 格式不对
        at: 排队时的生效时间 (默认现在起 CHANGE_DELAY_HOURS 小时后); 组里的网站和主网站一起生效用."""
        item = normalize(key, raw)
        if not item:
            return "invalid", ""
        with self._lock:
            if self.created is None:
                self.created = self._now()
            if item in self.lists[key]:
                if self._drop_pending("remove", key, item):
                    self._save()
                    self._rebuild()
                    return "cancelled", item
                if key == WHITE_DOMAINS and self._ungroup(item):    # 自己加的: 不再是别人名下的, 单独一条
                    self._save()
                    self._rebuild()
                return "exists", item
            if self._find_pending("add", key, item) >= 0:
                return "staged", item
            if not self.dev_getter() and key not in self.preset_keys():
                self._stage("add", key, item, at)
                return "staged", item
            self.lists[key].append(item)
            self._save()
            self._rebuild()
            return "applied", item

    def _ungroup(self, item: str) -> bool:
        changed = False
        for parent in list(self.groups):
            if item in self.groups[parent]:
                self.groups[parent] = [k for k in self.groups[parent] if k != item]
                changed = True
        return changed

    def remove(self, key: str, raw, at=None):
        """返回 (status, item).
        status: applied 立即生效(仅开发者模式 / 配置期) / staged 进入等待 /
                cancelled 取消了排队中的加入 / missing 名单里没有 / invalid
        网站白名单里删主网站 (或取消它排队中的加入) 时, 只属于它的组里的网站一起处理 (一样的状态, 一样的生效时间)."""
        with self._lock:
            kids = self.exclusive_children(normalize(key, raw)) if key == WHITE_DOMAINS else []
            status, item = self._remove_one(key, raw, at)
            if status in ("applied", "staged", "cancelled") and key == WHITE_DOMAINS:
                p = self.pending_for(key, item)
                when = p["apply_at"] if status == "staged" and p and p["op"] == "remove" else None
                for k in kids:
                    self._remove_one(key, k, when)
                if status != "staged":
                    self.groups.pop(item, None)
                self._save()
                self._rebuild()
            return status, item

    def _remove_one(self, key: str, raw, at=None):
        item = normalize(key, raw)
        if not item:
            return "invalid", ""
        with self._lock:
            if self.created is None:
                self.created = self._now()
            if item not in self.lists[key]:
                if self._drop_pending("add", key, item):
                    self._save()
                    self._rebuild()
                    return "cancelled", item
                return "missing", item
            if self._find_pending("remove", key, item) >= 0:
                return "staged", item
            if not self.dev_getter() and key not in self.preset_keys():
                self._stage("remove", key, item, at)
                return "staged", item
            self.lists[key].remove(item)
            self._save()
            self._rebuild()
            return "applied", item

    def add_group(self, parent: str, hosts) -> dict:
        """网站白名单里的 parent (已在名单里, 或加入在排队中) 引发的这些网站一起加入名单, 记在它名下 (MECHANICS 4.9).
        已经被名单里的条目放行的 (含子域名, 连带的域名) 不重复加. 排队中的和主网站同一个生效时间.
        返回 {"added": [新加入 / 排队的网站], "kept": [本来就在名单里的, 现在归到它名下]}."""
        parent = norm_domain(parent)
        out = {"added": [], "kept": []}
        with self._lock:
            if not parent or not self._live(parent):
                return out
            pend = self.pending_for(WHITE_DOMAINS, parent)
            at = pend["apply_at"] if pend and pend["op"] == "add" else None
            kids = list(self.groups.get(parent, []))
            for raw in hosts:
                h = norm_domain(raw)
                if not h or h == parent or h in kids or family_match(h, parent):
                    continue
                cov = [r for r in self.lists[WHITE_DOMAINS] if family_match(h, r)]
                if cov:                       # 已经放行了: 如果放行它的是别的网站名下的, 也算在这个网站名下 (删掉那个网站时不能连带删掉)
                    for r in cov:
                        if r != parent and r not in kids and any(r in ks for ks in self.groups.values()):
                            kids.append(r)
                            out["kept"].append(r)
                    continue
                st, item = self.add(WHITE_DOMAINS, h, at)
                if st in ("applied", "staged"):
                    kids.append(h)
                    out["added"].append(h)
            if kids:
                self.groups[parent] = kids
                self._save()
                self._rebuild()
        return out

    def cancel_pending(self, index: int) -> bool:
        """取消排队中的改动. 取消网站白名单主网站的加入 / 删除时, 只属于它的组里的网站排队中的同样改动一起取消."""
        with self._lock:
            if 0 <= index < len(self.pending):
                p = self.pending.pop(index)
                if p["list"] == WHITE_DOMAINS:
                    for k in self.exclusive_children(p["item"]):
                        self._drop_pending(p["op"], WHITE_DOMAINS, k)
                self._save()
                self._rebuild()
                return True
            return False

    def promote_due(self) -> list:
        with self._lock:
            now = self._now()
            dev = self.dev_getter()
            due = [p for p in self.pending if dev or p["apply_at"] <= now]
            if not due:
                return []
            for p in due:
                self.pending.remove(p)
                lst = self.lists[p["list"]]
                if p["op"] == "add" and p["item"] not in lst:
                    lst.append(p["item"])
                elif p["op"] == "remove" and p["item"] in lst:
                    lst.remove(p["item"])
            self._save()
            self._rebuild()
            return due


    def decide(self, host: str, exe: str, online: bool, mode: str = MODE_BLACKLIST):
        """(放行?, 原因).原因: local / blockall / online / api / blacklist-app / blacklist-domain /
        default-allow / whitelist-domain / offline"""
        host = (host or "").lower().strip('.')
        exe = (exe or "").lower()
        s = self._snap
        if host in _LOOPBACK or host.startswith("127."):
            return True, "local"
        if exe and exe in self.banned:
            return False, "ban"              # 合约的网络禁令: 什么时候都不放行 (MECHANICS 18.3)
        if self.banned_domains and any(domain_match(host, d) for d in self.banned_domains):
            return False, "ban"              # 网络禁令的网站: 联网会话 / 白名单 / 提前启动都放行不了
        if self.api_hosts and any(domain_match(host, h) for h in self.api_hosts):
            return True, "api"              # AI 接口: 名单 / 代理-全屏蔽都放行 (聊天 / 名单建议要用)
        if any(domain_match(host, d) for d in CLAUDE_CODE_DOMAINS):
            return True, "claude-code"      # Claude Code: 名单 / 代理-全屏蔽都放行
        if online:
            return True, "online"           # 联网会话中全部放行 (MECHANICS 4.1)
        if exe and (exe in self.exempt or self._grace_new(exe)):
            return True, "prestart"         # 提前启动的程序 / 空窗期新启动的程序: 代理-全屏蔽和名单都不拦 (MECHANICS 20)
        if mode == MODE_BLOCKALL:
            return False, "blockall"
        if mode == MODE_WHITELIST:
            for r in s[WHITE_DOMAINS]:
                if family_match(host, r):
                    uids = self._ups.get((WHITE_UPS, up_site(r)))
                    if not uids:
                        return True, "whitelist-domain"
                    # 这个站点有 UP 主白名单: 浏览器开着的 B 站页面全是白名单 UP 主的才放行
                    if self._probe(exe, r, uids):
                        return True, "whitelist-up"
                    return False, "not-whitelisted-up"
            return False, "offline"
        if exe and exe in s[BLACK_APPS]:
            return False, "blacklist-app"
        for r in s[BLACK_DOMAINS]:
            if domain_match(host, r):
                # 这个站点有 UP 主白名单: 整站拦, 只有浏览器开着的 B 站页面全是白名单 UP 主的才例外放行
                uids = self._ups.get((WHITE_UPS, up_site(r)))
                if uids and self._probe(exe, r, uids):
                    return True, "whitelist-up"
                return False, "blacklist-domain"
        return True, "default-allow"

    def _probe(self, exe: str, rule: str, uids) -> bool:
        """浏览器开着的 B 站页面是不是全是 uids 里 UP 主的; 没有探测器, 认不出程序, 探测出错 → False (不放行)."""
        probe = self.up_probe
        site = up_site(rule)
        if probe is None or not exe or not site:
            return False
        try:
            return bool(probe.allowed(site, exe, uids))
        except Exception:
            return False


    def _grace_new(self, exe: str) -> bool:
        """空窗期里: 开始时没在运行的程序 (新启动的) 先放行, 不等下一秒的标记."""
        base = self.open_new_except
        return base is not None and exe not in base

    def in_white_apps(self, exe: str) -> bool:
        """这个程序在 Rules 页的应用白名单里吗 (提前启动不去标记 / 藏它们, MECHANICS 20)."""
        exe = norm_app(exe) if exe else ""
        return bool(exe) and exe in self._snap[WHITE_APPS]

    def app_allowed(self, exe: str, rest: bool, mode: str = MODE_BLACKLIST):
        """前台窗口的程序能不能用 (Rules 页的应用名单, 见 MECHANICS 4.7). 只管专注 / 空闲, 休息中 (rest) 全部放行; 和联不联网无关.
        (允许?, 原因). 原因: rest / whitelist-app / not-whitelisted / blacklist-app / default-allow"""
        exe = norm_app(exe) if exe else ""
        s = self._snap
        if exe and (exe in self.exempt or self._grace_new(exe)):
            return True, "prestart"         # 提前启动的程序 / 空窗期新启动的程序: 不看名单 (MECHANICS 20)
        if rest:
            return True, "rest"
        if mode == MODE_WHITELIST:
            if exe and exe in s[WHITE_APPS]:
                return True, "whitelist-app"
            return False, "not-whitelisted"
        if exe and exe in s[BLACK_APPS]:
            return False, "blacklist-app"
        return True, "default-allow"


def _selftest():
    import tempfile
    assert norm_domain("https://www.Bilibili.com:443/video?x=1") == "www.bilibili.com"
    assert norm_domain("*.example.com") == "example.com"
    assert norm_domain("user:pw@github.com/a") == "github.com"
    assert norm_domain("[::1]:80") == "::1"
    assert norm_domain("localnamenodot") == "" and norm_domain("a b.com") == ""
    assert norm_app("chrome") == "chrome.exe"
    assert norm_app(r'"C:\Program Files\Steam\Steam.EXE"') == "steam.exe"
    assert norm_app("a/b\\x.exe") == "x.exe" and norm_app("") == "" and norm_app("<>") == ""
    assert domain_match("a.b.com", "b.com") and not domain_match("ab.com", "b.com")

    clock = [datetime(2026, 1, 1, 12, 0)]
    path = os.path.join(tempfile.mkdtemp(), "r.json")
    rs = RuleStore(path, now=lambda: clock[0])
    instant = [True]
    rs.dev_getter = lambda: instant[0]

    # 配置期内: 加入立即生效
    assert rs.add(WHITE_DOMAINS, "school.edu.cn")[0] == "applied"
    assert rs.add(WHITE_DOMAINS, "school.edu.cn")[0] == "exists"
    assert rs.add(BLACK_DOMAINS, "bilibili.com")[0] == "applied"
    assert rs.add(BLACK_APPS, "steam")[0] == "applied"
    assert rs.add(WHITE_APPS, "code.exe")[0] == "applied"
    assert rs.add(WHITE_DOMAINS, "???")[0] == "invalid"
    assert not rs.pending
    instant[0] = False
    # 过滤名单模式的切换在排队: 新模式的名单提前设置, 改动立即保存 (其他名单照常等 48 小时)
    rs.preset_keys = lambda: frozenset({WHITE_DOMAINS})
    assert rs.add(WHITE_DOMAINS, "preset.example.com")[0] == "applied" and "preset.example.com" in rs.items(WHITE_DOMAINS)
    assert rs.add(BLACK_DOMAINS, "later.example.com")[0] == "staged" and rs.pending
    assert rs.remove(WHITE_DOMAINS, "preset.example.com")[0] == "applied" and "preset.example.com" not in rs.items(WHITE_DOMAINS)
    rs.cancel_pending(0)
    rs.preset_keys = lambda: frozenset()
    assert rs.add(WHITE_DOMAINS, "preset.example.com")[0] == "staged", "没有排队的切换: 照常等"
    rs.cancel_pending(0)
    assert not rs.pending

    def d(host, exe, online):                       # 黑名单模式
        return rs.decide(host, exe, online, MODE_BLACKLIST)

    def w(host, exe, online):                       # 白名单模式
        return rs.decide(host, exe, online, MODE_WHITELIST)

    assert w("www.school.edu.cn", "chrome.exe", False) == (True, "whitelist-domain")
    assert w("zhihu.com", "chrome.exe", False) == (False, "offline")
    assert w("zhihu.com", "code.exe", False) == (False, "offline"), "应用白名单不放行网站"
    assert w("www.school.edu.cn", "code.exe", False) == (True, "whitelist-domain")
    assert w("www.bilibili.com", "chrome.exe", True) == (True, "online"), "白名单模式联网时不看黑名单"
    assert d("www.school.edu.cn", "chrome.exe", False) == (True, "default-allow"), "黑名单模式默认放行"
    assert d("zhihu.com", "code.exe", False) == (True, "default-allow")
    assert d("www.bilibili.com", "chrome.exe", False) == (False, "blacklist-domain"), "离线也拦黑名单"
    assert d("zhihu.com", "steam.exe", False) == (False, "blacklist-app")
    assert d("zhihu.com", "chrome.exe", True) == (True, "online")
    assert d("www.bilibili.com", "chrome.exe", True) == (True, "online"), "黑名单模式联网时全部放行"
    assert d("zhihu.com", "steam.exe", True) == (True, "online")
    for fn in (d, w):
        assert fn("localhost", "", False) == (True, "local")
        assert fn("127.0.0.1", "", False) == (True, "local")
    assert d("notbilibili.com", "", False) == (True, "default-allow")
    empty = RuleStore(os.path.join(tempfile.mkdtemp(), "e.json"))
    assert empty.decide("anything.com", "x.exe", False, MODE_BLACKLIST) == (True, "default-allow"), \
        "黑名单为空时必须什么都不拦"
    assert empty.decide("anything.com", "x.exe", False, MODE_WHITELIST) == (False, "offline"), \
        "白名单为空时离线全拦"
    assert keys_for_mode(MODE_WHITELIST) == (WHITE_DOMAINS, WHITE_APPS)
    # 全屏蔽: 网站名单 / 应用名单都不放行网站
    assert rs.decide("www.school.edu.cn", "code.exe", False, MODE_BLOCKALL) == (False, "blockall")
    assert rs.decide("zhihu.com", "steam.exe", False, MODE_BLOCKALL) == (False, "blockall")
    assert rs.decide("zhihu.com", "chrome.exe", True, MODE_BLOCKALL) == (True, "online")
    assert rs.decide("localhost", "", False, MODE_BLOCKALL) == (True, "local")
    # AI 接口的域名: 名单 / 全屏蔽都放行; 合约的网络禁令仍然优先
    rs.api_hosts = {"api.deepseek.com"}
    assert rs.decide("api.deepseek.com", "chrome.exe", False, MODE_BLOCKALL) == (True, "api")
    assert rs.decide("sub.api.deepseek.com", "code.exe", False, MODE_WHITELIST) == (True, "api")
    assert rs.decide("other.com", "chrome.exe", False, MODE_BLOCKALL) == (False, "blockall")
    rs.api_hosts = set()
    # Claude Code: 代理-全屏蔽 / 白名单 / 黑名单都放行; 合约的网络禁令仍然优先
    assert rs.decide("api.anthropic.com", "claude.exe", False, MODE_BLOCKALL) == (True, "claude-code")
    assert rs.decide("claude.ai", "node.exe", False, MODE_WHITELIST) == (True, "claude-code")
    assert rs.decide("notanthropic.com", "claude.exe", False, MODE_BLOCKALL) == (False, "blockall")
    rs.banned_domains = {"anthropic.com"}
    assert rs.decide("api.anthropic.com", "claude.exe", False, MODE_BLOCKALL) == (False, "ban")
    rs.banned_domains = set()
    assert keys_for_mode("whatever") == (BLACK_DOMAINS, BLACK_APPS)

    assert rs.app_allowed("Steam.exe", False, MODE_BLACKLIST) == (False, "blacklist-app")
    assert rs.app_allowed("chrome.exe", True, MODE_BLACKLIST)[0]
    assert rs.app_allowed("code.exe", False, MODE_WHITELIST) == (True, "whitelist-app")
    assert rs.app_allowed("chrome.exe", False, MODE_WHITELIST) == (False, "not-whitelisted")
    assert rs.app_allowed("chrome.exe", True, MODE_WHITELIST) == (True, "rest")
    assert rs.app_allowed("steam.exe", True, MODE_BLACKLIST) == (True, "rest"), "休息中全部放行"

    # 提前启动 (MECHANICS 20): 名单和代理-全屏蔽都不拦
    rs.exempt = {"steam.exe"}
    assert rs.decide("zhihu.com", "steam.exe", False, MODE_BLACKLIST) == (True, "prestart")
    assert rs.decide("www.school.edu.cn", "steam.exe", False, MODE_WHITELIST) == (True, "prestart")
    assert rs.decide("bilibili.com", "chrome.exe", False, MODE_BLACKLIST) == (False, "blacklist-domain")
    assert rs.decide("zhihu.com", "steam.exe", False, MODE_BLOCKALL) == (True, "prestart")
    assert rs.decide("zhihu.com", "chrome.exe", False, MODE_BLOCKALL) == (False, "blockall"), "别的程序照样拦"
    assert rs.app_allowed("steam.exe", False, MODE_BLACKLIST) == (True, "prestart")
    assert rs.app_allowed("steam.exe", False, MODE_WHITELIST) == (True, "prestart")
    assert rs.app_allowed("chrome.exe", False, MODE_WHITELIST) == (False, "not-whitelisted")
    # 合约的网络禁令: 联网中 / 提前启动也拦
    rs.banned = {"steam.exe"}
    assert rs.decide("store.steampowered.com", "steam.exe", True, MODE_BLACKLIST) == (False, "ban")
    assert rs.decide("zhihu.com", "steam.exe", False, MODE_BLOCKALL) == (False, "ban")
    assert rs.decide("localhost", "steam.exe", False, MODE_BLACKLIST) == (True, "local")
    assert rs.decide("zhihu.com", "chrome.exe", True, MODE_BLACKLIST) == (True, "online")
    rs.banned = set()
    rs.banned_domains = {"bilibili.com"}
    assert rs.decide("www.bilibili.com", "chrome.exe", True, MODE_BLACKLIST) == (False, "ban"), "联网中也封锁"
    assert rs.decide("bilibili.com", "chrome.exe", False, MODE_WHITELIST) == (False, "ban")
    assert rs.decide("notbilibili.com", "chrome.exe", True, MODE_BLACKLIST) == (True, "online")
    rs.banned_domains = set()
    rs.exempt = set()
    rs.open_new_except = {"chrome.exe"}  # 空窗期: 开始时没在跑的程序放行, 原来就开着的照旧受名单管
    assert rs.decide("zhihu.com", "javaw.exe", False, MODE_WHITELIST) == (True, "prestart")
    assert rs.app_allowed("javaw.exe", False, MODE_WHITELIST) == (True, "prestart")
    assert rs.decide("zhihu.com", "chrome.exe", False, MODE_WHITELIST) == (False, "offline")
    assert rs.app_allowed("chrome.exe", False, MODE_WHITELIST) == (False, "not-whitelisted")
    rs.open_new_except = None

    # 删除一律要等
    assert rs.remove(BLACK_DOMAINS, "bilibili.com")[0] == "staged"
    assert rs.remove(WHITE_APPS, "code.exe")[0] == "staged"
    assert rs.remove(WHITE_APPS, "code.exe")[0] == "staged", "重复删除不重复排队"
    assert len(rs.pending) == 2
    assert d("bilibili.com", "", False)[0] is False, "删除要等冷静期"
    assert rs.app_allowed("code.exe", False, MODE_WHITELIST)[0] is True, "删除要等冷静期"
    # 等待中再加回 = 取消排队中的删除
    assert rs.add(BLACK_DOMAINS, "bilibili.com")[0] == "cancelled"
    assert len(rs.pending) == 1
    # 加入也一律要等, 包括从没加入过的新条目
    assert rs.add(BLACK_APPS, "qq.exe")[0] == "staged", "新条目加入也要等"
    assert "qq.exe" not in rs.items(BLACK_APPS)
    assert rs.add(BLACK_APPS, "qq.exe")[0] == "staged", "重复加入不重复排队"
    assert len(rs.pending) == 2
    assert rs.remove(BLACK_APPS, "qq.exe")[0] == "cancelled", "删掉排队中的加入 = 取消"
    assert len(rs.pending) == 1
    clock[0] += timedelta(hours=49)
    assert len(rs.promote_due()) == 1 and "code.exe" not in rs.items(WHITE_APPS)
    assert rs.add(WHITE_APPS, "code.exe")[0] == "staged"
    assert rs.remove(WHITE_APPS, "code.exe")[0] == "cancelled"
    assert not rs.pending
    assert rs.remove(WHITE_DOMAINS, "nothere.com")[0] == "missing"

    # 排队写进文件, 重新读取后照样按时生效
    assert rs.add(WHITE_DOMAINS, "wikipedia.org")[0] == "staged"
    rs2 = RuleStore(path, now=lambda: clock[0])
    assert len(rs2.pending) == 1 and "wikipedia.org" not in rs2.items(WHITE_DOMAINS)
    clock[0] += timedelta(hours=47)
    assert rs2.promote_due() == []
    clock[0] += timedelta(hours=2)
    assert len(rs2.promote_due()) == 1 and "wikipedia.org" in rs2.items(WHITE_DOMAINS)
    rs3 = RuleStore(path, now=lambda: clock[0])
    assert rs3.remove(WHITE_DOMAINS, "wikipedia.org")[0] == "staged"
    assert rs3.cancel_pending(0) and not rs3.pending

    # 旧格式文件里的 history 直接忽略, 不影响读取
    old = os.path.join(tempfile.mkdtemp(), "old.json")
    with securefile.open(old, "w", encoding="utf-8") as f:
        json.dump({"lists": {BLACK_DOMAINS: ["a.com"]}, "pending": [],
                   "history": {BLACK_DOMAINS: ["a.com", "b.com"]}}, f)
    rs4 = RuleStore(old, now=lambda: clock[0])
    assert rs4.items(BLACK_DOMAINS) == ["a.com"]
    assert rs4.add(BLACK_DOMAINS, "c.com")[0] == "staged"

    # 开发者模式 / 配置期: 一切改动立即生效, 排队中的也立即生效
    dev = [False]
    rs5 = RuleStore(os.path.join(tempfile.mkdtemp(), "d.json"), now=lambda: clock[0])
    rs5.dev_getter = lambda: dev[0]
    assert rs5.add(BLACK_DOMAINS, "x.com")[0] == "staged"
    dev[0] = True
    assert rs5.promote_due() and not rs5.pending and rs5.items(BLACK_DOMAINS) == ["x.com"]
    assert rs5.remove(BLACK_DOMAINS, "x.com")[0] == "applied"
    assert rs5.add(BLACK_DOMAINS, "y.com")[0] == "applied"

    # UP 主区分 (MECHANICS 4.8)
    assert norm_up("https://space.bilibili.com/2742391?spm_id_from=333.788.upinfo.head.click") == "bilibili.com/2742391"
    assert norm_up("space.bilibili.com/2742391/video") == "bilibili.com/2742391"
    assert norm_up("m.bilibili.com/space/2742391") == "bilibili.com/2742391"
    assert norm_up("2742391") == norm_up("UID: 2742391") == norm_up("mid=2742391") == "bilibili.com/2742391"
    assert norm_up("bilibili.com/2742391") == "bilibili.com/2742391" and norm_up("007") == "bilibili.com/7"
    for bad in ("", "0", "abc", "bilibili.com", "https://www.bilibili.com/video/BV1GJ411x7h7",
                "https://space.bilibili.com/", "https://evil.com/space.bilibili.com/5", "-5"):
        assert norm_up(bad) == "", bad
    assert up_site("bilibili.com") == up_site("space.bilibili.com") == "bilibili.com"
    assert up_site("notbilibili.com") == "" and up_site("zhihu.com") == ""
    class Probe:
        """假的浏览器: pages[exe] = 每个哔哩哔哩窗口里的 UP 主 UID 集合 (空集合 = 不属于任何 UP 主的页面, None = 认不出来)."""
        def __init__(self):
            self.pages = {}

        def allowed(self, site, exe, uids):
            ps = self.pages.get(exe) or []
            return bool(ps) and all(p is not None and p & uids for p in ps)

    probe = Probe()
    clock2 = [datetime(2026, 1, 1, 12, 0)]
    ru = RuleStore(os.path.join(tempfile.mkdtemp(), "u.json"), now=lambda: clock2[0])
    ru.up_probe = probe
    ru.dev_getter = lambda: True
    B, W = MODE_BLACKLIST, MODE_WHITELIST
    # 黑名单里的站点 + UP 主白名单: 整站拦, 只有白名单里的 UP 主例外 (其余页面, 认不出来的窗口都拦); 没有 UP 主白名单时整站拦
    assert ru.add(BLACK_DOMAINS, "bilibili.com")[0] == "applied"
    assert ru.decide("www.bilibili.com", "msedge.exe", False, B) == (False, "blacklist-domain"), "没有 UP 主名单: 整站拦"
    assert ru.add(WHITE_UPS, "https://space.bilibili.com/2742391?x=1") == ("applied", "bilibili.com/2742391")
    assert ru.add(WHITE_UPS, "2742391")[0] == "exists" and ru.add(WHITE_UPS, "nope")[0] == "invalid"
    assert ru.add(WHITE_UPS, "99")[0] == "applied" and ru.up_items(WHITE_UPS, "bilibili.com") == ["2742391", "99"]
    assert ru.up_uids(WHITE_UPS, "bilibili.com") == {"2742391", "99"}
    probe.pages = {"msedge.exe": [{"2742391"}]}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, B) == (True, "whitelist-up")
    assert ru.decide("api.bilibili.com", "msedge.exe", False, B) == (True, "whitelist-up"), "子域名一起放行"
    probe.pages = {"msedge.exe": [{"99"}, {"2742391", "7"}]}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, B) == (True, "whitelist-up"), "几个窗口都是白名单 UP 主"
    probe.pages = {"msedge.exe": [frozenset()]}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, B) == (False, "blacklist-domain"), "首页 / 搜索等: 拦"
    probe.pages = {"msedge.exe": [{"5"}]}
    assert ru.decide("api.bilibili.com", "msedge.exe", False, B) == (False, "blacklist-domain"), "别的 UP 主: 拦"
    probe.pages = {"msedge.exe": [{"2742391"}, {"5"}]}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, B) == (False, "blacklist-domain"), "还开着别的 UP 主的页面"
    probe.pages = {"msedge.exe": [{"2742391"}, None]}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, B) == (False, "blacklist-domain"), "有认不出来的窗口: 拦"
    probe.pages = {"chrome.exe": [{"2742391"}]}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, B) == (False, "blacklist-domain"), "别的浏览器开的页面"
    probe.pages = {"msedge.exe": [{"2742391"}]}
    assert ru.decide("www.bilibili.com", "", False, B) == (False, "blacklist-domain"), "查不到程序: 拦"
    assert ru.decide("notbilibili.com", "msedge.exe", False, B) == (True, "default-allow")
    assert ru.decide("www.bilibili.com", "msedge.exe", True, B) == (True, "online")
    ru.up_probe = None
    assert ru.decide("www.bilibili.com", "msedge.exe", False, B) == (False, "blacklist-domain"), "没有探测器 = 整站拦"
    ru.up_probe = probe
    ru.banned_domains = {"bilibili.com"}          # 合约的网络禁令比 UP 主名单优先
    assert ru.decide("www.bilibili.com", "msedge.exe", False, B) == (False, "ban")
    ru.banned_domains = set()
    ru.remove(WHITE_UPS, "2742391"); ru.remove(WHITE_UPS, "99")
    # 白名单里的站点 + UP 主白名单: 只放行白名单里的 UP 主 (其余页面, 认不出来的窗口都拦); 没有 UP 主白名单时整站放行
    assert ru.add(WHITE_DOMAINS, "bilibili.com")[0] == "applied"
    assert ru.decide("www.bilibili.com", "msedge.exe", False, W) == (True, "whitelist-domain"), "没有 UP 主名单: 整站放行"
    assert ru.add(WHITE_UPS, "space.bilibili.com/5")[0] == "applied"
    probe.pages = {"msedge.exe": [{"5"}]}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, W) == (True, "whitelist-up")
    assert ru.decide("api.bilibili.com", "msedge.exe", False, W) == (True, "whitelist-up")
    probe.pages = {"msedge.exe": [{"5"}, {"2742391"}]}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, W) == (False, "not-whitelisted-up"), "还开着别的 UP 主的页面"
    probe.pages = {"msedge.exe": [{"5"}, None]}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, W) == (False, "not-whitelisted-up"), "有认不出来的窗口"
    probe.pages = {"msedge.exe": [frozenset()]}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, W) == (False, "not-whitelisted-up"), "首页不属于任何 UP 主"
    probe.pages = {}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, W) == (False, "not-whitelisted-up"), "没有页面: 不放行"
    assert ru.decide("www.bilibili.com", "", False, W) == (False, "not-whitelisted-up")
    assert ru.decide("zhihu.com", "msedge.exe", False, W) == (False, "offline")
    # 白名单里的 bilibili.com 连带它的图片 / 视频流域名 (没有这些视频放不出来), UP 主白名单照样管着它们
    for cdn in ("i0.hdslb.com", "upos-sz-mirror08c.bilivideo.com", "xy1x2x3x4xy.mcdn.bilivideo.cn", "x.biliapi.net"):
        probe.pages = {"msedge.exe": [{"5"}]}
        assert ru.decide(cdn, "msedge.exe", False, W) == (True, "whitelist-up"), cdn
        probe.pages = {"msedge.exe": [{"9"}]}
        assert ru.decide(cdn, "msedge.exe", False, W) == (False, "not-whitelisted-up"), cdn
    assert ru.decide("nothdslb.com", "msedge.exe", False, W) == (False, "offline")
    assert ru.decide("i0.hdslb.com", "msedge.exe", False, B) == (True, "default-allow"), "黑名单模式不连带这些域名"
    rn = RuleStore(os.path.join(tempfile.mkdtemp(), "n.json"), now=lambda: clock2[0])
    rn.dev_getter = lambda: True
    rn.add(WHITE_DOMAINS, "bilibili.com")
    assert rn.decide("i0.hdslb.com", "chrome.exe", False, W) == (True, "whitelist-domain"), "没有 UP 主名单: 连带放行"
    rm = RuleStore(os.path.join(tempfile.mkdtemp(), "m.json"), now=lambda: clock2[0])
    rm.dev_getter = lambda: True
    rm.add(WHITE_DOMAINS, "space.bilibili.com")
    assert rm.decide("i0.hdslb.com", "chrome.exe", False, W) == (False, "offline"), "只写子域名不连带"
    # 探测器出错 = 不放行 / 拦 (回到整站名单的严格一侧)
    class Boom:
        def allowed(self, *a): raise RuntimeError("x")
    ru.up_probe = Boom()
    assert ru.decide("www.bilibili.com", "msedge.exe", False, W) == (False, "not-whitelisted-up")
    assert ru.decide("www.bilibili.com", "msedge.exe", False, B) == (False, "blacklist-domain")
    # UP 主名单的改动同样要等冷静期; 重新读取后还在
    ru.dev_getter = lambda: False
    assert ru.add(WHITE_UPS, "123")[0] == "staged" and ru.up_items(WHITE_UPS, "bilibili.com") == ["5"]
    ru2 = RuleStore(ru.path, now=lambda: clock2[0])
    assert ru2.pending[0]["list"] == WHITE_UPS and ru2.pending[0]["item"] == "bilibili.com/123"
    clock2[0] += timedelta(hours=49)
    assert ru2.promote_due() and ru2.up_items(WHITE_UPS, "bilibili.com") == ["5", "123"]
    assert ru2.remove(WHITE_UPS, "5")[0] == "staged" and "5" in ru2.up_uids(WHITE_UPS, "bilibili.com")
    # 旧的名单文件没有 UP 主名单: 读出来是空的
    assert RuleStore(old, now=lambda: clock[0]).items(WHITE_UPS) == []

    # 网站白名单的组 (MECHANICS 4.9): 主网站引发的网站和它一起加入, 同一个生效时间, 归在它名下
    ck = [datetime(2026, 3, 1, 8, 0)]
    gp = os.path.join(tempfile.mkdtemp(), "g.json")
    g = RuleStore(gp, now=lambda: ck[0])
    g.dev_getter = lambda: False
    assert g.add(WHITE_DOMAINS, "zhihu.com")[0] == "staged"
    ck[0] += timedelta(seconds=20)                                   # 探测花了一会儿
    got = g.add_group("zhihu.com", ["zhimg.com", "www.zhihu.com", "hm.baidu.com", "pic.zhimg.com", "zhimg.com", "bad host"])
    assert got["added"] == ["zhimg.com", "hm.baidu.com", "pic.zhimg.com"], got       # 子域名 (www.zhihu.com) 不加, 重复 / 不合法的不加
    at = g.pending_for(WHITE_DOMAINS, "zhihu.com")["apply_at"]
    assert all(g.pending_for(WHITE_DOMAINS, k)["apply_at"] == at for k in ("zhimg.com", "hm.baidu.com", "pic.zhimg.com")), "和主网站一起生效"
    assert g.children("zhihu.com") == [] and len(g.children("zhihu.com", active_only=False)) == 3
    assert g.add_group("never.com", ["x.com"]) == {"added": [], "kept": []}, "主网站不在名单里 (也没排队): 不加"
    g2 = RuleStore(gp, now=lambda: ck[0])                            # 重新读取: 组还在
    assert g2.groups["zhihu.com"] == ["zhimg.com", "hm.baidu.com", "pic.zhimg.com"]
    ck[0] = at + timedelta(seconds=1)
    assert len(g2.promote_due()) == 4
    assert g2.tree() == [("zhihu.com", ["zhimg.com", "hm.baidu.com", "pic.zhimg.com"])], g2.tree()
    assert g2.decide("pic.zhimg.com", "chrome.exe", False, MODE_WHITELIST) == (True, "whitelist-domain")
    assert g2.decide("other.net", "chrome.exe", False, MODE_WHITELIST) == (False, "offline")
    # 第二个主网站引发了同一个网站: 已经放行的不重复加; 单独删一个引发的网站不影响别的
    assert g2.add(WHITE_DOMAINS, "zhida.com")[0] == "staged"
    ck[0] += timedelta(seconds=5)
    got = g2.add_group("zhida.com", ["hm.baidu.com", "static.zhida.net"])
    assert got == {"added": ["static.zhida.net"], "kept": ["hm.baidu.com"]}, got
    # 删除主网站: 只属于它的引发的网站一起排队删除, 同一个生效时间; 取消排队也一起取消
    assert g2.remove(WHITE_DOMAINS, "zhihu.com")[0] == "staged"
    rm_at = {k: g2.pending_for(WHITE_DOMAINS, k) for k in ("zhihu.com", "zhimg.com", "pic.zhimg.com")}
    assert all(p and p["op"] == "remove" and p["apply_at"] == rm_at["zhihu.com"]["apply_at"] for p in rm_at.values())
    assert not g2.pending_for(WHITE_DOMAINS, "hm.baidu.com"), "zhida.com 名下也有它: 不连带删"
    pend_idx = next(i for i, p in enumerate(g2.pending) if p["item"] == "zhihu.com" and p["op"] == "remove")
    assert g2.cancel_pending(pend_idx)
    assert not any(p["op"] == "remove" for p in g2.pending), g2.pending
    assert g2.tree()[0][0] == "zhihu.com" and g2.children("zhihu.com") == ["zhimg.com", "hm.baidu.com", "pic.zhimg.com"]
    assert g2.remove(WHITE_DOMAINS, "zhihu.com")[0] == "staged"
    ck[0] += timedelta(hours=49)
    g2.promote_due()
    assert "zhihu.com" not in g2.items(WHITE_DOMAINS) and "zhimg.com" not in g2.items(WHITE_DOMAINS)
    assert "hm.baidu.com" in g2.items(WHITE_DOMAINS), "zhida.com 名下的也有它: 不能连带删掉"
    assert "zhihu.com" not in g2.groups and g2.children("zhida.com") == ["hm.baidu.com", "static.zhida.net"], g2.groups
    # 加入还在排队时删除主网站 = 取消, 引发的网站排队中的加入也一起取消
    g3 = RuleStore(os.path.join(tempfile.mkdtemp(), "g3.json"), now=lambda: ck[0])
    g3.dev_getter = lambda: False
    g3.add(WHITE_DOMAINS, "a.com")
    g3.add_group("a.com", ["cdn.a-static.com", "api.a-api.com"])
    assert len(g3.pending) == 3
    assert g3.remove(WHITE_DOMAINS, "a.com")[0] == "cancelled" and not g3.pending and not g3.groups
    # 配置期 (立即生效): 主网站和引发的网站立即生效; 自己再加一次引发的网站 = 不再属于主网站
    g3.dev_getter = lambda: True
    g3.add(WHITE_DOMAINS, "b.com")
    assert g3.add_group("b.com", ["cdn.b-static.com", "img.b-img.com"])["added"] == ["cdn.b-static.com", "img.b-img.com"]
    assert g3.tree() == [("b.com", ["cdn.b-static.com", "img.b-img.com"])]
    assert g3.add(WHITE_DOMAINS, "img.b-img.com")[0] == "exists"
    assert g3.tree() == [("b.com", ["cdn.b-static.com"]), ("img.b-img.com", [])], g3.tree()
    assert g3.remove(WHITE_DOMAINS, "b.com")[0] == "applied"
    assert g3.items(WHITE_DOMAINS) == ["img.b-img.com"] and not g3.groups
    # bilibili.com 连带放行的域名不再作为组里的网站重复加
    g3.add(WHITE_DOMAINS, "bilibili.com")
    assert g3.add_group("bilibili.com", ["i0.hdslb.com", "api.bilibili.com", "cm.other.com"])["added"] == ["cm.other.com"]
    return "netproxy.rules selftest OK"


if __name__ == "__main__":
    print(_selftest())
