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

按 UP 主区分 (MECHANICS 4.8, 仅 bilibili.com):
    域名条目 bilibili.com 在黑名单里 → 它下面的"UP 主白名单"里的 UP 主, 主页和视频照常打开;
    域名条目 bilibili.com 在白名单里 → 它下面的"UP 主黑名单"里的 UP 主, 主页和视频拦截.
    代理看不到 HTTPS 的路径, 是哪个 UP 主的页面由 up_probe (浏览器地址栏, netproxy/upgate.py) 告诉 decide().

本模块不依赖 tkinter / config,可以单独测试.
"""

import json
import os
import re
import threading
from datetime import datetime, timedelta

WHITE_DOMAINS = "white_domains"
WHITE_APPS = "white_apps"
BLACK_DOMAINS = "black_domains"
BLACK_APPS = "black_apps"
# UP 主名单 (条目 "bilibili.com/UID"): 白名单 = 黑名单里的该站点放行这些 UP 主, 黑名单 = 白名单里的该站点拦截这些 UP 主
WHITE_UPS = "white_ups"
BLACK_UPS = "black_ups"
LIST_KEYS = (WHITE_DOMAINS, WHITE_APPS, BLACK_DOMAINS, BLACK_APPS, WHITE_UPS, BLACK_UPS)
UP_SITES = ("bilibili.com",)             # 支持按 UP 主区分的站点

MODE_BLACKLIST = "blacklist"
MODE_WHITELIST = "whitelist"
# 代理-全屏蔽 (Proxy block-all) (断网方式 proxy_all): 联网判定不看名单, 离线时除本机地址外全部拦截,
# 联网会话中全部放行. 应用名单照样按过滤名单模式 (黑 / 白) 关窗口, 只在专注 / 空闲时, 和联不联网无关 (app_allowed, MECHANICS 3.2.1 / 4.7)
MODE_BLOCKALL = "blockall"

CHANGE_DELAY_HOURS = 48
LOOSEN_DELAY_HOURS = CHANGE_DELAY_HOURS   # 旧名字, 保留兼容

_LOOPBACK = {"localhost", "::1", "0.0.0.0"}


def keys_for_mode(mode: str) -> tuple:
    return (WHITE_DOMAINS, WHITE_APPS) if mode == MODE_WHITELIST else (BLACK_DOMAINS, BLACK_APPS)


def up_key_for(domain_key: str) -> str:
    """网站名单 → 挂在它下面的 UP 主名单: 黑名单的站点放行白名单里的 UP 主, 白名单的站点拦截黑名单里的 UP 主."""
    return WHITE_UPS if domain_key == BLACK_DOMAINS else BLACK_UPS


def up_key_for_mode(mode: str) -> str:
    return BLACK_UPS if mode == MODE_WHITELIST else WHITE_UPS


def is_up(key: str) -> bool:
    return key in (WHITE_UPS, BLACK_UPS)


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
        # 提前启动的程序 (MECHANICS 20): 不看网站 / 应用名单, 直接放行 (代理-全屏蔽照样拦).
        # 由 app.py 在提前启动期间每秒钟更新成这些程序的 exe 名.
        self.exempt = set()
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
        # 浏览器此刻开着哪些页面 (netproxy/upgate.py UpGate): allowed(site, exe, uids) / denied(site, exe, uids). None = 不支持, 一律按整站名单
        self.up_probe = None
        self.load()
        self.promote_due()


    def load(self):
        raw = {}
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
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
        self._rebuild()

    def _template_raw(self) -> dict:
        """默认模板只提供名单本身;created / pending 不取,初始化窗口从第一次改动算起."""
        if not self.template:
            return {}
        try:
            with open(self.template, 'r', encoding='utf-8') as f:
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
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    def _rebuild(self):
        self._snap = {k: tuple(v) for k, v in self.lists.items()}
        ups = {}
        for k in (WHITE_UPS, BLACK_UPS):
            for item in self._snap[k]:
                site, _, uid = item.partition('/')
                ups.setdefault((k, site), set()).add(uid)
        self._ups = {k: frozenset(v) for k, v in ups.items()}
        self.version += 1


    def items(self, key: str) -> list:
        return list(self._snap[key])

    def up_uids(self, key: str, site: str) -> frozenset:
        """这个站点的 UP 主名单 (key = WHITE_UPS / BLACK_UPS) 里的 UID."""
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

    def _stage(self, op, key, item):
        self.pending.append({"op": op, "list": key, "item": item,
                             "apply_at": self._now() + self.delay})
        self._save()


    def add(self, key: str, raw):
        """返回 (status, item).
        status: applied 立即生效(仅开发者模式 / 配置期) / staged 进入等待 /
                cancelled 取消了排队中的删除 /
                exists 已在名单里 / invalid 格式不对"""
        item = normalize(key, raw)
        if not item:
            return "invalid", ""
        with self._lock:
            if self.created is None:
                self.created = self._now()
            if item in self.lists[key]:
                if self._drop_pending("remove", key, item):
                    self._save()
                    return "cancelled", item
                return "exists", item
            if self._find_pending("add", key, item) >= 0:
                return "staged", item
            if not self.dev_getter():
                self._stage("add", key, item)
                return "staged", item
            self.lists[key].append(item)
            self._save()
            self._rebuild()
            return "applied", item

    def remove(self, key: str, raw):
        """返回 (status, item).
        status: applied 立即生效(仅开发者模式 / 配置期) / staged 进入等待 /
                cancelled 取消了排队中的加入 / missing 名单里没有 / invalid"""
        item = normalize(key, raw)
        if not item:
            return "invalid", ""
        with self._lock:
            if self.created is None:
                self.created = self._now()
            if item not in self.lists[key]:
                if self._drop_pending("add", key, item):
                    self._save()
                    return "cancelled", item
                return "missing", item
            if self._find_pending("remove", key, item) >= 0:
                return "staged", item
            if not self.dev_getter():
                self._stage("remove", key, item)
                return "staged", item
            self.lists[key].remove(item)
            self._save()
            self._rebuild()
            return "applied", item

    def cancel_pending(self, index: int) -> bool:
        with self._lock:
            if 0 <= index < len(self.pending):
                del self.pending[index]
                self._save()
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
        """(放行?, 原因).原因: local / blockall / online / blacklist-app / blacklist-domain /
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
        if online:
            return True, "online"           # 联网会话中全部放行 (MECHANICS 4.1)
        if exe and exe in self.exempt:
            return True, "prestart"         # 提前启动的程序: 代理-全屏蔽和名单都不拦 (MECHANICS 20)
        if mode == MODE_BLOCKALL:
            return False, "blockall"
        if mode == MODE_WHITELIST:
            for r in s[WHITE_DOMAINS]:
                if domain_match(host, r):
                    if self._up_hit(r, exe, BLACK_UPS):
                        return False, "blacklist-up"        # 站点在白名单里, 但浏览器开着的是黑名单 UP 主的页面
                    return True, "whitelist-domain"
            return False, "offline"
        if exe and exe in s[BLACK_APPS]:
            return False, "blacklist-app"
        for r in s[BLACK_DOMAINS]:
            if domain_match(host, r):
                if self._up_hit(r, exe, WHITE_UPS):
                    return True, "whitelist-up"             # 站点在黑名单里, 但浏览器开着的全是白名单 UP 主的页面
                return False, "blacklist-domain"
        return True, "default-allow"

    def _up_hit(self, rule: str, exe: str, key: str) -> bool:
        """域名条目 rule 命中后, 再看浏览器开着的页面 (按 UP 主区分, MECHANICS 4.8).
        key = WHITE_UPS: 该浏览器里所有的该站点页面都是白名单 UP 主的; key = BLACK_UPS: 有一个页面是黑名单 UP 主的."""
        site = up_site(rule)
        probe = self.up_probe
        if not site or not exe or probe is None:
            return False
        uids = self._ups.get((key, site))
        if not uids:
            return False
        try:
            return bool((probe.allowed if key == WHITE_UPS else probe.denied)(site, exe, uids))
        except Exception:
            return False


    def app_allowed(self, exe: str, rest: bool, mode: str = MODE_BLACKLIST):
        """前台窗口的程序能不能用 (Rules 页的应用名单, 见 MECHANICS 4.7). 只管专注 / 空闲, 休息中 (rest) 全部放行; 和联不联网无关.
        (允许?, 原因). 原因: rest / whitelist-app / not-whitelisted / blacklist-app / default-allow"""
        exe = norm_app(exe) if exe else ""
        s = self._snap
        if exe and exe in self.exempt:
            return True, "prestart"         # 提前启动的程序: 不看名单 (MECHANICS 20)
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
    with open(old, "w", encoding="utf-8") as f:
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
    assert up_key_for(BLACK_DOMAINS) == WHITE_UPS and up_key_for(WHITE_DOMAINS) == BLACK_UPS

    class Probe:
        """假的浏览器: pages[exe] = 每个哔哩哔哩窗口里的 UP 主 UID 集合 (None = 认不出来)."""
        def __init__(self):
            self.pages = {}

        def allowed(self, site, exe, uids):
            ps = self.pages.get(exe) or []
            return bool(ps) and all(p is not None and p & uids for p in ps)

        def denied(self, site, exe, uids):
            return any(p and p & uids for p in self.pages.get(exe) or [])

    probe = Probe()
    clock2 = [datetime(2026, 1, 1, 12, 0)]
    ru = RuleStore(os.path.join(tempfile.mkdtemp(), "u.json"), now=lambda: clock2[0])
    ru.up_probe = probe
    ru.dev_getter = lambda: True
    assert ru.add(BLACK_DOMAINS, "bilibili.com")[0] == "applied"
    assert ru.add(WHITE_UPS, "https://space.bilibili.com/2742391?x=1") == ("applied", "bilibili.com/2742391")
    assert ru.add(WHITE_UPS, "2742391")[0] == "exists" and ru.add(WHITE_UPS, "nope")[0] == "invalid"
    assert ru.add(WHITE_UPS, "99")[0] == "applied" and ru.up_items(WHITE_UPS, "bilibili.com") == ["2742391", "99"]
    assert ru.up_uids(WHITE_UPS, "bilibili.com") == {"2742391", "99"} and not ru.up_uids(BLACK_UPS, "bilibili.com")
    B = MODE_BLACKLIST
    # 黑名单里的站点: 没有页面 / 认不出来 / 别的 UP 主 → 照旧拦截; 白名单 UP 主的页面 → 放行
    assert ru.decide("space.bilibili.com", "msedge.exe", False, B) == (False, "blacklist-domain")
    probe.pages = {"msedge.exe": [None]}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, B) == (False, "blacklist-domain")
    probe.pages = {"msedge.exe": [{"5"}]}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, B) == (False, "blacklist-domain")
    probe.pages = {"msedge.exe": [{"2742391"}]}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, B) == (True, "whitelist-up")
    assert ru.decide("api.bilibili.com", "msedge.exe", False, B) == (True, "whitelist-up"), "子域名一起放行"
    assert ru.decide("www.bilibili.com", "chrome.exe", False, B) == (False, "blacklist-domain"), "别的浏览器没开这个页面"
    assert ru.decide("www.bilibili.com", "", False, B) == (False, "blacklist-domain"), "查不到程序"
    assert ru.decide("notbilibili.com", "msedge.exe", False, B) == (True, "default-allow")
    probe.pages = {"msedge.exe": [{"2742391"}, {"99", "5"}]}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, B) == (True, "whitelist-up"), "合作投稿: 其中一位在白名单就行"
    probe.pages = {"msedge.exe": [{"2742391"}, {"5"}]}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, B) == (False, "blacklist-domain"), "还开着别的 UP 主的页面"
    probe.pages = {"msedge.exe": [{"2742391"}, None]}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, B) == (False, "blacklist-domain"), "有认不出来的窗口"
    probe.pages = {"msedge.exe": [{"2742391"}]}
    assert ru.decide("www.bilibili.com", "msedge.exe", True, B) == (True, "online")
    ru.up_probe = None
    assert ru.decide("www.bilibili.com", "msedge.exe", False, B) == (False, "blacklist-domain"), "没有探测器 = 整站拦截"
    ru.up_probe = probe
    # 合约的网络禁令比 UP 主白名单优先
    ru.banned_domains = {"bilibili.com"}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, B) == (False, "ban")
    ru.banned_domains = set()
    # 白名单里的站点: 黑名单 UP 主的页面拦截, 其余照旧放行 (认不出来 = 放行)
    W = MODE_WHITELIST
    assert ru.add(WHITE_DOMAINS, "bilibili.com")[0] == "applied"
    assert ru.add(BLACK_UPS, "space.bilibili.com/5")[0] == "applied"
    probe.pages = {"msedge.exe": [{"2742391"}]}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, W) == (True, "whitelist-domain")
    probe.pages = {"msedge.exe": [{"2742391"}, {"5"}]}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, W) == (False, "blacklist-up")
    assert ru.decide("www.bilibili.com", "chrome.exe", False, W) == (True, "whitelist-domain")
    probe.pages = {"msedge.exe": [None]}
    assert ru.decide("www.bilibili.com", "msedge.exe", False, W) == (True, "whitelist-domain")
    assert ru.decide("zhihu.com", "msedge.exe", False, W) == (False, "offline")
    # 探测器出错 = 当作没命中
    class Boom:
        def allowed(self, *a): raise RuntimeError("x")
        denied = allowed
    ru.up_probe = Boom()
    assert ru.decide("www.bilibili.com", "msedge.exe", False, W) == (True, "whitelist-domain")
    assert ru.decide("www.bilibili.com", "msedge.exe", False, B) == (False, "blacklist-domain")
    # UP 主名单的改动同样要等冷静期; 重新读取后还在
    ru.dev_getter = lambda: False
    assert ru.add(WHITE_UPS, "123")[0] == "staged" and ru.up_items(WHITE_UPS, "bilibili.com") == ["2742391", "99"]
    ru2 = RuleStore(ru.path, now=lambda: clock2[0])
    assert ru2.pending[0]["list"] == WHITE_UPS and ru2.pending[0]["item"] == "bilibili.com/123"
    clock2[0] += timedelta(hours=49)
    assert ru2.promote_due() and ru2.up_items(WHITE_UPS, "bilibili.com") == ["2742391", "99", "123"]
    assert ru2.remove(WHITE_UPS, "99")[0] == "staged" and "99" in ru2.up_uids(WHITE_UPS, "bilibili.com")
    # 旧的名单文件没有 UP 主名单: 读出来是空的
    assert RuleStore(old, now=lambda: clock[0]).items(WHITE_UPS) == []
    return "netproxy.rules selftest OK"


if __name__ == "__main__":
    print(_selftest())
