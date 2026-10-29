"""
按 UP 主放行 / 拦截 (MECHANICS 4.8)
───────────────────────────────────
· parse_page(网址): 浏览器地址栏里的哔哩哔哩网址是 UP 主的主页 (space.bilibili.com/UID), 还是某个视频 (BV 号 / av 号);
· UpResolver: 视频是谁投的 —— 查哔哩哔哩公开接口 (x/web-interface/view), 合作投稿的每位 UP 主都算; 结果缓存. 本机直接连, 不走系统代理 (否则会被自己的过滤代理拦下);
  也负责查 UP 主的昵称 (名单里显示用);
· UpGate: RuleStore.up_probe. 问"这个浏览器里开着的哔哩哔哩页面是不是全属于 / 有没有属于某几位 UP 主".
  页面从 browserurl.Watcher 来 (需要时才扫地址栏). 每个页面是: 一组 UID (UP 主的主页 / 视频),
  空集合 (读到了地址, 但不属于任何 UP 主: 首页, 搜索, 直播间 …), 或者 None (认不出来: 没读到地址, 查不到视频的投稿人).

本模块不依赖 tkinter / config; 联网和读地址栏的部分都可以换成假的来测试.
"""

import json
import os
import re
import threading
import time
import urllib.request

from .rules import bili_uid, domain_match

SITE = "bilibili.com"
API_VIEW = "https://api.bilibili.com/x/web-interface/view?"
API_CARD = "https://api.bilibili.com/x/web-interface/card?mid="
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0.0.0 Safari/537.36")
_VIDEO_HOSTS = {"www.bilibili.com", "bilibili.com", "m.bilibili.com"}
_OK_TTL = 6 * 3600.0                 # 查到的结果缓存多久
_MISSING_TTL = 120.0                 # 接口说没有这个视频 (被删, 仅自己可见 …)
_ERROR_TTL = 5.0                     # 网络出错, 很快再试


def parse_page(url):
    """浏览器地址栏里的网址 → ("space", UID) / ("video", "BV…" 或 "av123") / ("other", None).
    地址栏常常省略 https://; 只认哔哩哔哩自己的域名 (别的网站路径里带 /video/ 不算)."""
    t = re.sub(r'^[a-z][a-z0-9+.\-]*://', '', str(url or "").strip(), flags=re.I)
    host, _, rest = t.partition('/')
    host = host.split('@')[-1].split(':')[0].strip('.').lower()
    path = re.split(r'[?#]', rest, maxsplit=1)[0]
    if not domain_match(host, SITE):
        return "other", None
    if host == "space.bilibili.com":
        uid = bili_uid("space.bilibili.com/" + path)
        return ("space", uid) if uid else ("other", None)
    if host in _VIDEO_HOSTS:
        if path.startswith("space/"):
            uid = bili_uid("bilibili.com/" + path)
            return ("space", uid) if uid else ("other", None)
        m = re.match(r'(?:s/)?video/(?:(bv)([0-9A-Za-z]{10})|av(\d{1,15}))(?![0-9A-Za-z])', path, re.I)
        if m:
            return "video", ("BV" + m.group(2)) if m.group(2) else ("av" + m.group(3))
    return "other", None


def _fetch_json(url: str, timeout: float = 6.0):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Referer": "https://www.bilibili.com/",
                                               "Accept": "application/json"})
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read(1 << 20).decode("utf-8", "replace"))


def owners_from_view(js) -> frozenset:
    """x/web-interface/view 的回复 → 投稿人 UID 集合 (含合作投稿的每位); 没有返回空集合."""
    try:
        d = js["data"]
        if js.get("code") != 0 or not isinstance(d, dict):
            return frozenset()
        out = set()
        for p in [d.get("owner")] + list(d.get("staff") or []):
            uid = bili_uid(p.get("mid")) if isinstance(p, dict) else ""
            if uid:
                out.add(uid)
        return frozenset(out)
    except (KeyError, TypeError, AttributeError):
        return frozenset()


class UpResolver:
    """视频 → UP 主, UP 主 → 昵称. 线程安全; 同一个视频同时来多次只查一次."""

    def __init__(self, fetch=None, names_path: str = None):
        self._fetch = fetch or _fetch_json
        self._lock = threading.Lock()
        self._owners = {}            # "BV…" / "av…" -> (frozenset 或 None, 过期时刻)
        self._flight = {}            # 正在查的 -> Event
        self._names = {}             # UID -> 昵称
        self._names_path = names_path
        self._name_flight = set()
        self._load_names()

    # ---- 视频 → UP 主 ----

    def owners(self, ref: str, wait: float = 2.0):
        """视频的投稿人 UID 集合; 还没查到 (没联网, 接口出错, 超过 wait 秒) 返回 None."""
        with self._lock:
            hit = self._owners.get(ref)
            if hit and hit[1] > time.monotonic():
                return hit[0]
            ev = self._flight.get(ref)
            start = ev is None
            if start:
                ev = self._flight[ref] = threading.Event()
        if start:
            threading.Thread(target=self._lookup, args=(ref, ev), daemon=True, name="oit-up-lookup").start()
        ev.wait(wait)
        with self._lock:
            hit = self._owners.get(ref)
            return hit[0] if hit and hit[1] > time.monotonic() else None

    def _lookup(self, ref: str, ev: threading.Event):
        owners, ttl = None, _ERROR_TTL
        try:
            q = ("bvid=" + ref) if ref.startswith("BV") else ("aid=" + ref[2:])
            got = owners_from_view(self._fetch(API_VIEW + q))
            owners, ttl = (got, _OK_TTL) if got else (None, _MISSING_TTL)
        except Exception:
            pass
        with self._lock:
            self._owners[ref] = (owners, time.monotonic() + ttl)
            if len(self._owners) > 2000:
                now = time.monotonic()
                self._owners = {k: v for k, v in self._owners.items() if v[1] > now}
            self._flight.pop(ref, None)
        ev.set()

    # ---- UP 主 → 昵称 (名单里显示) ----

    def name(self, uid: str) -> str:
        """缓存里的昵称; 还没查过返回 ''."""
        with self._lock:
            return self._names.get(uid, "")

    def fetch_name(self, uid: str) -> str:
        """去查昵称 (会联网, 不要在界面线程里调); 查不到返回 ''."""
        with self._lock:
            if uid in self._names:
                return self._names[uid]
            if uid in self._name_flight:
                return ""
            self._name_flight.add(uid)
        name = ""
        try:
            js = self._fetch(API_CARD + uid)
            if js.get("code") == 0:
                name = str(js["data"]["card"]["name"] or "").strip()[:60]
        except Exception:
            pass
        with self._lock:
            self._name_flight.discard(uid)
            if name:
                self._names[uid] = name
        if name:
            self._save_names()
        return name

    def _load_names(self):
        if not self._names_path:
            return
        try:
            with open(self._names_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            self._names = {str(k): str(v) for k, v in raw.items() if bili_uid(k) and v} \
                if isinstance(raw, dict) else {}
        except Exception:
            self._names = {}

    def _save_names(self):
        if not self._names_path:
            return
        with self._lock:
            data = dict(self._names)
        tmp = self._names_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self._names_path) or ".", exist_ok=True)
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._names_path)
        except OSError:
            pass


class UpGate:
    """RuleStore.up_probe: 浏览器里开着的哔哩哔哩页面属于哪些 UP 主.
    allowed(site, exe, uids): 这个浏览器 (exe) 开着的哔哩哔哩页面至少有一个, 而且每一个都是 uids 里某位 UP 主的主页或视频;
    denied(site, exe, uids):  其中有一个是 uids 里某位 UP 主的主页或视频, 或者有一个认不出来 (宁可错拦: UP 主黑名单拦的是整站里的一部分).
    扫描结果太旧 → 一律当作认不出来 (allowed 假, denied 真)."""

    def __init__(self, watcher, resolver: UpResolver):
        self.watcher = watcher
        self.resolver = resolver

    def _page_uids(self, url):
        """一个窗口里的页面属于哪些 UP 主 (集合; 空集合 = 不属于任何 UP 主); 认不出来 None."""
        if not url:
            return None
        kind, ref = parse_page(url)
        if kind == "space":
            return frozenset((ref,))
        if kind == "video":
            return self.resolver.owners(ref)
        return frozenset()

    def _urls(self, site: str, exe: str):
        """这个浏览器的窗口地址 [url 或 None, …]; 扫描结果太旧 / 不是这个站点 → None."""
        if site != SITE:
            return None
        snap, fresh = self.watcher.snapshot(wait=3.0)
        if not fresh:
            return None
        return snap.get(str(exe or "").lower()) or []

    def allowed(self, site: str, exe: str, uids) -> bool:
        urls = self._urls(site, exe)
        if not urls:
            return False
        for url in urls:
            got = self._page_uids(url)
            if not got or not (got & uids):
                return False
        return True

    def denied(self, site: str, exe: str, uids) -> bool:
        urls = self._urls(site, exe)
        if urls is None:
            return True
        for url in urls:
            got = self._page_uids(url)
            if got is None or got & uids:
                return True
        return False


def _selftest():
    P = parse_page
    assert P("https://space.bilibili.com/2742391?spm_id_from=333.788.upinfo.head.click") == ("space", "2742391")
    assert P("space.bilibili.com/2742391/video") == ("space", "2742391")
    assert P("https://www.bilibili.com/video/BV1GJ411x7h7/?spm_id_from=333") == ("video", "BV1GJ411x7h7")
    assert P("www.bilibili.com/video/bv1GJ411x7h7") == ("video", "BV1GJ411x7h7")
    assert P("bilibili.com/video/av80433022?p=2") == ("video", "av80433022")
    assert P("https://m.bilibili.com/video/BV1GJ411x7h7") == ("video", "BV1GJ411x7h7")
    assert P("https://m.bilibili.com/space/2742391") == ("space", "2742391")
    assert P("https://www.bilibili.com/space/2742391") == ("space", "2742391")
    for other in ("https://www.bilibili.com/", "https://search.bilibili.com/all?keyword=x",
                  "https://space.bilibili.com/", "https://space.bilibili.com/abc",
                  "https://www.bilibili.com/video/", "https://www.bilibili.com/video/BV1GJ411",
                  "https://www.bilibili.com/video/BV1GJ411x7h7extra", "https://live.bilibili.com/123",
                  "https://evil.com/video/BV1GJ411x7h7", "https://notbilibili.com/video/BV1GJ411x7h7",
                  "https://www.bilibili.com.evil.com/video/BV1GJ411x7h7", "", None, "bilibili", "some text"):
        assert P(other) == ("other", None), other
    assert P("HTTPS://User@Space.Bilibili.com:443/42") == ("space", "42")

    js = {"code": 0, "data": {"owner": {"mid": 1}, "staff": [{"mid": 2}, {"mid": "3"}, {"x": 1}, "bad"]}}
    assert owners_from_view(js) == {"1", "2", "3"}
    assert owners_from_view({"code": -404, "data": None}) == frozenset()
    assert owners_from_view({"code": 0, "data": {"owner": {}}}) == frozenset()
    assert owners_from_view(None) == frozenset() and owners_from_view({"code": 0}) == frozenset()

    # 解析器: 缓存, 合并并发, 出错不卡住
    calls = []
    book = {"BV1GJ411x7h7": {"code": 0, "data": {"owner": {"mid": 7}}},
            "av5": {"code": 0, "data": {"owner": {"mid": 8}, "staff": [{"mid": 9}]}},
            "BV1gone0000x": {"code": -404, "message": "gone"}}

    def fetch(url):
        calls.append(url)
        if "/card?" in url:
            mid = url.rsplit("=", 1)[1]
            if mid == "7":
                return {"code": 0, "data": {"card": {"mid": "7", "name": "某 UP"}}}
            return {"code": -400}
        time.sleep(0.05)
        key = url.rsplit("=", 1)[1]
        if "aid=" in url:
            key = "av" + key
        if key == "boom":
            raise OSError("down")
        return book.get(key, {"code": -404})
    d = os.path.join(__import__("tempfile").mkdtemp(), "names.json")
    r = UpResolver(fetch, d)
    res = []
    ts = [threading.Thread(target=lambda: res.append(r.owners("BV1GJ411x7h7"))) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert res == [frozenset({"7"})] * 5 and len(calls) == 1, (res, calls)
    assert r.owners("BV1GJ411x7h7") == {"7"} and len(calls) == 1, "命中缓存不再查"
    assert r.owners("av5") == {"8", "9"} and "aid=5" in calls[-1]
    assert r.owners("BV1gone0000x") is None
    n = len(calls)
    assert r.owners("BV1gone0000x") is None and len(calls) == n, "查不到的也缓存一会儿"
    t0 = time.monotonic()
    assert r.owners("BV1slow00000", wait=0.01) is None and time.monotonic() - t0 < 0.04, "来不及就先说不知道"
    time.sleep(0.15)
    assert r.name("7") == "" and r.fetch_name("7") == "某 UP" and r.name("7") == "某 UP"
    assert r.fetch_name("8") == "" and r.name("8") == ""
    assert UpResolver(fetch, d).name("7") == "某 UP", "昵称存了盘"

    # UpGate
    class W:
        def __init__(self):
            self.snap, self.fresh = {}, True

        def snapshot(self, wait=1.5):
            return self.snap, self.fresh
    w = W()
    g = UpGate(w, UpResolver(fetch))
    U = frozenset({"7", "42"})
    video = "https://www.bilibili.com/video/BV1GJ411x7h7"
    space = "https://space.bilibili.com/42"
    w.snap = {"msedge.exe": [video]}
    assert g.allowed(SITE, "msedge.exe", U) and g.allowed(SITE, "MSEdge.exe", U) and g.denied(SITE, "msedge.exe", U)
    w.snap = {"msedge.exe": [video, space]}
    assert g.allowed(SITE, "msedge.exe", U), "几个窗口都是名单里的 UP 主"
    assert not g.allowed(SITE, "msedge.exe", frozenset({"42"})), "视频是 7 的"
    w.snap = {"msedge.exe": [video, "https://www.bilibili.com/"]}
    assert not g.allowed(SITE, "msedge.exe", U) and g.denied(SITE, "msedge.exe", U)
    w.snap = {"msedge.exe": ["https://www.bilibili.com/"]}
    assert not g.allowed(SITE, "msedge.exe", U) and not g.denied(SITE, "msedge.exe", U), "首页不属于任何 UP 主"
    w.snap = {"msedge.exe": [video, None]}
    assert not g.allowed(SITE, "msedge.exe", U) and g.denied(SITE, "msedge.exe", U)
    w.snap = {"msedge.exe": [None]}
    assert not g.allowed(SITE, "msedge.exe", U) and g.denied(SITE, "msedge.exe", frozenset({"1"})), "认不出来: 不放行, 也不敢说没事"
    w.snap = {"msedge.exe": ["https://www.bilibili.com/video/BV1gone0000x"]}
    assert not g.allowed(SITE, "msedge.exe", U) and g.denied(SITE, "msedge.exe", U), "查不到 UP 主"
    w.snap = {"chrome.exe": [video]}
    assert not g.allowed(SITE, "msedge.exe", U) and not g.denied(SITE, "msedge.exe", U), "这个浏览器没开 B 站页面"
    assert g.denied(SITE, "chrome.exe", U)
    w.snap, w.fresh = {"msedge.exe": [video]}, False
    assert not g.allowed(SITE, "msedge.exe", U) and g.denied(SITE, "msedge.exe", U), "扫描结果太旧"
    w.fresh = True
    assert not g.allowed("zhihu.com", "msedge.exe", U) and not g.allowed(SITE, "", U)
    return "netproxy.upgate selftest OK"


if __name__ == "__main__":
    print(_selftest())
