"""
加入网站白名单时, 让电脑真的去连这个网站, 把它引发的其他网站一起找出来 (MECHANICS 4.9)
─────────────────────────────────────────────────────────────────────────────────────
只写 bilibili.com 的话页面是打不开全的: 图片在 hdslb.com, 接口在 api.bilibili.com ……白名单模式下这些都要一个一个加.
所以加入白名单时, OIT 用一个无界面的浏览器 (Edge, 没有就 Chrome) 真的打开这个网站, 看它连了哪些网站:

    1. 起一个只记录的本地代理 (netproxy.server.FilterProxy, 全部放行, 不碰系统代理);
    2. 无界面浏览器用一个临时的用户目录, 通过这个代理打开网站 (浏览器围栏只拦浏览器直连外网, 连本机代理不受影响, 3.2.2);
    3. 没有新的连接了 (网站加载完了) 就关掉浏览器, 清掉临时目录. 代理看到的所有域名 = 它引发的网站.

代理转发走 OIT 自己的进程: 先试能不能直连这个网站, 连不上的话经过用户原来的代理 (updater.upstream_proxy). 不经过 OIT 自己的过滤代理, 所以离线时也能探测.
找不到浏览器 / 浏览器没起来时退回"读网页": 下载首页, 从里面的 script / link / img / iframe 等标签和样式表里取域名 (看不到脚本运行之后才发出的请求).

本模块不依赖 tkinter / config.

    python -m netproxy.sitescan       自检 (不联网, 不启动浏览器)
"""

import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urljoin, urlsplit

from .rules import norm_domain, domain_match, family_match
from .server import FilterProxy

MAX_HOSTS = 80                  # 一个网站最多记下这么多个引发的网站
FIRST_WAIT = 14.0               # 这么久还没连上网站本身 = 连不上
QUIET = 4.0                     # 网站加载完 (没有新的连接) 再等多久才算完
MIN_RUN = 5.0
BUDGET = 30.0                   # 最多探测多久
_CREATE_NO_WINDOW = 0x08000000
_NEW_GROUP = 0x00000200

_FLAGS = ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
          "--disable-sync", "--disable-background-networking", "--disable-component-update", "--disable-default-apps",
          "--disable-domain-reliability", "--disable-breakpad", "--disable-client-side-phishing-detection",
          "--metrics-recording-only", "--mute-audio", "--no-service-autorun", "--window-size=1280,800"]

# 浏览器自己的后台连接 (检查更新, 钓鱼网站库, 新标签页, 账号 / 奖励 …), 不是网站引发的. 后缀 (含本身).
NOISE = (
    "edge.microsoft.com", "smartscreen.microsoft.com", "config.edge.skype.com", "msedge.api.cdp.microsoft.com",
    "edgeservices.bing.com", "browser.events.data.microsoft.com", "edge.activity.windows.com", "ntp.msn.com",
    "assets.msn.com", "api.msn.com", "c.msn.com", "substrate.office.com", "mss.office.com",
    "rewardsplatform.microsoft.com", "copilot.microsoft.com", "sapphire.microsoft.com", "edge-consumer-static.azureedge.net",
    "update.googleapis.com", "safebrowsing.googleapis.com", "optimizationguide-pa.googleapis.com",
    "content-autofill.googleapis.com", "clients1.google.com", "clients2.google.com", "clients4.google.com",
    "clientservices.googleapis.com", "redirector.gvt1.com", "edgedl.me.gvt1.com", "dl.google.com",
)
NOISE_EXACT = ("www.bing.com",)         # 只有这一个: 别的 bing.com 子域名可能是网站自己用的 (地图 …)
# 证书吊销检查的地址 (ocsp.xxx / crl.xxx): 不是网站的请求
_PKI = re.compile(r"^(ocsp|crl\d*|crls|pki|status|cdp\d*)\.")


def find_browser():
    """无界面浏览器的路径: 优先 Edge (Windows 自带), 其次 Chrome; 没有返回 None."""
    if sys.platform != "win32":
        return None
    cands = []
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(env)
        if root:
            cands.append(os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"))
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        root = os.environ.get(env)
        if root:
            cands.append(os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"))
    return next((p for p in cands if os.path.isfile(p)), None)


def is_ip(host: str) -> bool:
    return bool(re.fullmatch(r"[0-9.]+|[0-9a-f:]+", host)) and (":" in host or host.replace(".", "").isdigit())


def is_noise(host: str) -> bool:
    return (host in NOISE_EXACT or any(domain_match(host, d) for d in NOISE) or bool(_PKI.match(host)))


def related(parent: str, hosts, covered=lambda h: False, limit: int = MAX_HOSTS) -> list:
    """从探测到的域名里挑出"网站引发的其他网站": 规范化, 去重, 去掉 IP / 本机 / 浏览器自己的后台连接 /
    已经被 parent (含子域名, 连带的域名) 或名单里已有的条目 (covered) 放行的; 保持先后顺序, 最多 limit 个."""
    parent = norm_domain(parent)
    out = []
    for raw in hosts:
        h = norm_domain(raw)
        if not h or h in out or is_ip(h) or h.startswith("127.") or h in ("localhost", "0.0.0.0"):
            continue
        if is_noise(h) or family_match(h, parent) or covered(h):
            continue
        out.append(h)
        if len(out) >= limit:
            break
    return out


class Result:
    """hosts: 探测到的所有域名 (没过滤, 按出现的先后); via: browser / static; reached: 连上了网站本身; error: 失败原因 (没有 = 空)."""

    def __init__(self, hosts=(), via="", reached=False, error=""):
        self.hosts, self.via, self.reached, self.error = list(hosts), via, reached, error


class _ScanProxy(FilterProxy):
    """记录哪些域名真的连上了 (HTTPS 隧道建立成功), 不只是浏览器想连."""

    def __init__(self, *a, on_open=None, **k):
        super().__init__(*a, **k)
        self._on_open = on_open or (lambda host: None)

    def _open_tunnel(self, host, port):
        s = super()._open_tunnel(host, port)
        self._on_open(host)
        return s


def pick_route(domain: str, upstream=None, timeout: float = 6.0):
    """转发走哪条路: None = 直连 (直连连得上这个网站, 或者本来就没有别的代理), 否则 upstream = 用户原来的代理 (host, port)."""
    if not upstream:
        return None
    for host in (domain, "www." + domain):
        try:
            socket.create_connection((host, 443), timeout=timeout).close()
            return None
        except OSError:
            continue
    return upstream


def _kill_tree(pid: int):
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=_CREATE_NO_WINDOW, timeout=15)
    except Exception:
        pass


def _purge_old_profiles(older_than: float = 3600.0):
    """上次没清干净的临时用户目录 (浏览器还占着文件时删不掉) 这次一起清."""
    tmp = tempfile.gettempdir()
    try:
        for name in os.listdir(tmp):
            p = os.path.join(tmp, name)
            if name.startswith("oit-scan-") and os.path.isdir(p) and time.time() - os.path.getmtime(p) > older_than:
                shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


def _rm_profile(path: str):
    for _ in range(20):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return
        time.sleep(0.25)


def browser_scan(domain: str, up=None, browser: str = None, launch=None, dial=None, cancelled=lambda: False,
                 first_wait: float = FIRST_WAIT, quiet: float = QUIET, min_run: float = MIN_RUN,
                 budget: float = BUDGET) -> Result:
    """用无界面浏览器打开 https://domain/, 返回它连过的所有域名 (up: 转发经过的代理, None = 直连).
    launch(proxy_port, url, profile_dir) -> 进程 (有 .pid / .poll()) 和 dial(host, port) -> socket 是测试用的替身."""
    domain = norm_domain(domain)
    if not domain:
        return Result(error="invalid")
    seen = []                                   # (出现的时间, 域名): 浏览器想连的
    opened = set()                              # 真的连上了的
    lock = threading.Lock()
    t0 = time.monotonic()

    def on_event(host, exe, allow, why):
        with lock:
            seen.append((time.monotonic() - t0, host))

    def on_open(host):
        with lock:
            opened.add(host)

    proxy = _ScanProxy(lambda h, e: (True, "scan"), port=0, upstream=lambda: up, on_event=on_event, on_open=on_open)
    if dial is not None:
        proxy._dial = dial
    port = proxy.start()
    prof = tempfile.mkdtemp(prefix="oit-scan-")
    proc = None
    try:
        url = f"https://{domain}/"
        if launch is not None:
            proc = launch(port, url, prof)
        else:
            args = [browser, *_FLAGS, f"--user-data-dir={prof}", f"--proxy-server=http://127.0.0.1:{port}", url]
            proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=_CREATE_NO_WINDOW | _NEW_GROUP)
        while True:
            time.sleep(0.25)
            now = time.monotonic() - t0
            with lock:
                busy = [t for t, h in seen if not is_noise(h)]        # 浏览器自己的后台连接不算"还在加载"
                last = busy[-1] if busy else None
                reached = any(domain_match(h, domain) or domain_match(domain, h) for h in opened)
            if cancelled():
                return Result([h for _t, h in seen], "browser", reached, "cancelled")
            if now >= budget or (last is not None and reached and now - last >= quiet and now >= min_run):
                break
            if not reached and now >= first_wait:
                break
            if proc is not None and hasattr(proc, "poll") and proc.poll() is not None and now >= 3:
                break                           # 浏览器自己退出了 (没起来 / 崩了)
        with lock:
            hosts = [h for _t, h in seen]
        return Result(hosts, "browser", reached, "" if reached else "unreachable")
    finally:
        if proc is not None:
            _kill_tree(proc.pid)
        proxy.stop()
        time.sleep(0.3)
        _rm_profile(prof)


# ---- 读网页 (没有浏览器时的退路) ----

_ATTR = re.compile(r"""(?:src|data-src|poster|action|data)\s*=\s*["']([^"'\s>]+)["']""", re.I)
_SRCSET = re.compile(r"""srcset\s*=\s*["']([^"']+)["']""", re.I)
_LINK = re.compile(r"<link\b[^>]*>", re.I)
_HREF = re.compile(r"""href\s*=\s*["']([^"'\s>]+)["']""", re.I)
_REL = re.compile(r"""rel\s*=\s*["']?([a-z\- ]+)""", re.I)
_CSS_URL = re.compile(r"""url\(\s*["']?([^"')\s]+)""", re.I)
_IMPORT = re.compile(r"""@import\s+["']([^"']+)""", re.I)
_LOAD_RELS = ("stylesheet", "preload", "prefetch", "preconnect", "dns-prefetch", "icon", "manifest", "modulepreload")


def page_hosts(html: str, base_url: str) -> list:
    """网页里会发出请求的地方用到的域名 (script / img / iframe / video 的 src, srcset, link 的 stylesheet / preload / preconnect …,
    样式里的 url() 和 @import); <a href> 不算 (点了才会连)."""
    urls = []
    for m in _ATTR.finditer(html):
        urls.append(m.group(1))
    for m in _SRCSET.finditer(html):
        urls += [part.strip().split(" ")[0] for part in m.group(1).split(",") if part.strip()]
    for tag in _LINK.findall(html):
        rel, href = _REL.search(tag), _HREF.search(tag)
        if href and rel and any(r in rel.group(1).lower().split() for r in _LOAD_RELS):
            urls.append(href.group(1))
    urls += [m.group(1) for m in _CSS_URL.finditer(html)] + [m.group(1) for m in _IMPORT.finditer(html)]
    out = []
    for u in urls:
        if u.startswith(("data:", "javascript:", "#", "about:", "blob:")):
            continue
        try:
            host = (urlsplit(urljoin(base_url, u)).hostname or "").lower()
        except ValueError:
            continue
        if host and host not in out:
            out.append(host)
    return out


def static_scan(domain: str, upstream=None, fetch=None, cancelled=lambda: False) -> Result:
    """下载首页和它的样式表, 取里面的域名. fetch(url) -> (最终网址, 文本) 抛 OSError = 连不上 (测试用替身, 默认走 urllib)."""
    domain = norm_domain(domain)
    if not domain:
        return Result(error="invalid")
    fetch = fetch or (lambda u: _fetch(u, upstream))
    try:
        final, html = fetch(f"https://{domain}/")
    except (OSError, ValueError) as e:
        return Result(via="static", error=str(e) or type(e).__name__)
    hosts = [h for h in (urlsplit(final).hostname,) if h] + page_hosts(html, final)
    sheets = [urljoin(final, m.group(1)) for tag in _LINK.findall(html) for m in [_HREF.search(tag)]
              if m and "stylesheet" in (_REL.search(tag).group(1).lower() if _REL.search(tag) else "")]
    for css in sheets[:8]:
        if cancelled():
            break
        try:
            cfinal, text = fetch(css)
        except (OSError, ValueError):
            continue
        hosts += [h for h in page_hosts(text, cfinal) if h not in hosts]
    return Result(hosts, "static", True)


def _fetch(url: str, upstream=None, limit: int = 2_000_000):
    import urllib.request
    last = None
    for up in ([None, upstream] if upstream else [None]):
        proxies = {"http": f"http://{up[0]}:{up[1]}", "https": f"http://{up[0]}:{up[1]}"} if up else {}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36 Edg/120.0",
            "Accept": "text/html,text/css,*/*;q=0.8", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
        try:
            with opener.open(req, timeout=12) as r:
                raw = r.read(limit)
                charset = r.headers.get_content_charset() or "utf-8"
                return r.geturl(), raw.decode(charset, errors="replace")
        except OSError as e:
            last = e
    raise last


def scan_site(domain: str, upstream=None, cancelled=lambda: False) -> Result:
    """探测 domain 引发的网站: 先用无界面浏览器, 没有浏览器 / 没起来 / 连不上时再读网页. 不抛异常 (失败写在 Result.error)."""
    _purge_old_profiles()
    exe = find_browser()
    res = None
    if exe:
        try:
            res = browser_scan(domain, pick_route(norm_domain(domain), upstream), browser=exe, cancelled=cancelled)
        except Exception as e:      # noqa: BLE001
            res = Result(via="browser", error=str(e) or type(e).__name__)
        if res.reached or res.error == "cancelled":
            return res
    try:
        st = static_scan(domain, upstream, cancelled=cancelled)
    except Exception as e:          # noqa: BLE001
        st = Result(via="static", error=str(e) or type(e).__name__)
    if st.reached or not res:
        return st
    st.hosts = res.hosts + [h for h in st.hosts if h not in res.hosts]
    st.error = st.error or res.error
    return st


def _selftest():
    assert is_ip("1.2.3.4") and is_ip("::1") and not is_ip("a1.com") and not is_ip("1.com")
    assert is_noise("nav-edge.smartscreen.microsoft.com") and is_noise("www.bing.com") and not is_noise("map.bing.com")
    assert is_noise("ocsp.digicert.com") and is_noise("crl3.digicert.com") and not is_noise("status-page.example.com")
    hosts = ["Bilibili.com", "www.bilibili.com", "s1.hdslb.com", "i0.hdslb.com", "s1.hdslb.com", "edge.microsoft.com",
             "1.2.3.4", "localhost", "api.bilibili.com", "https://cdn.example.net/x", "bad host", "ocsp.digicert.com"]
    assert related("bilibili.com", hosts) == ["cdn.example.net"], related("bilibili.com", hosts)    # hdslb.com 等是 bilibili.com 连带放行的
    assert related("site.org", hosts) == ["bilibili.com", "www.bilibili.com", "s1.hdslb.com", "i0.hdslb.com",
                                          "api.bilibili.com", "cdn.example.net"], related("site.org", hosts)
    assert related("site.org", hosts, covered=lambda h: h.endswith("bilibili.com")) == [
        "s1.hdslb.com", "i0.hdslb.com", "cdn.example.net"]
    assert related("example.com", [f"h{i}.cdn{i}.net" for i in range(200)], limit=5) == [f"h{i}.cdn{i}.net" for i in range(5)]
    # 读网页
    html = ('<html><head><link rel="stylesheet" href="//static.cdn.com/a.css"><link rel="canonical" href="https://x.org/">'
            '<link rel="preconnect" href="https://fonts.gstatic.com"><script src="https://js.stat.io/a.js"></script>'
            '<style>@import "https://fonts.googleapis.com/css"; body{background:url(https://img.cdn2.com/b.png)}</style>'
            '</head><body><img src="/local.png"><img srcset="https://i1.pic.com/a.png 1x, https://i2.pic.com/a.png 2x">'
            '<iframe src="https://embed.video.com/v"></iframe><a href="https://linkonly.com/">x</a>'
            '<img src="data:image/png;base64,AAAA"></body></html>')
    ph = page_hosts(html, "https://www.site.com/index.html")
    for h in ("static.cdn.com", "fonts.gstatic.com", "js.stat.io", "fonts.googleapis.com", "img.cdn2.com", "www.site.com",
              "i1.pic.com", "i2.pic.com", "embed.video.com"):
        assert h in ph, (h, ph)
    assert "x.org" not in ph and "linkonly.com" not in ph, ph
    pages = {"https://site.com/": ("https://www.site.com/", html), "https://static.cdn.com/a.css": (
        "https://static.cdn.com/a.css", "@font-face{src:url(https://fonts.cdn3.com/f.woff2)}")}

    def fetch(u):
        if u not in pages:
            raise OSError("404")
        return pages[u]
    r = static_scan("site.com", fetch=fetch)
    assert r.reached and r.via == "static" and "www.site.com" in r.hosts and "fonts.cdn3.com" in r.hosts, r.hosts
    r = static_scan("nowhere.com", fetch=fetch)
    assert not r.reached and r.error, r.error
    # 浏览器探测: 替身 "浏览器" 经过本地代理发 CONNECT, 记下的域名就是探测结果; 不联网 (代理连不上这些域名, 只记录)
    import socket

    class Fake:
        pid = 0
        returncode = None

        def poll(self):
            return None

    def launch(port, url, prof):
        def go():
            for h in ("site.com", "edge.microsoft.com", "static.cdn.com", "api.site.com", "static.cdn.com"):
                try:
                    s = socket.create_connection(("127.0.0.1", port), timeout=3)
                    s.sendall(f"CONNECT {h}:443 HTTP/1.1\r\nHost: {h}:443\r\n\r\n".encode())
                    s.settimeout(1.5)
                    try:
                        s.recv(100)
                    except OSError:
                        pass
                    s.close()
                except OSError:
                    pass
        threading.Thread(target=go, daemon=True).start()
        return Fake()
    killed, keep = [], []
    real_kill = globals()["_kill_tree"]
    globals()["_kill_tree"] = lambda pid: killed.append(pid)

    def dial_ok(host, port):                    # 假的"连上了": 一对本地 socket
        a, b = socket.socketpair()
        keep.append(b)
        return a

    def dial_fail(host, port):
        raise OSError("no route")
    try:
        t = time.monotonic()
        r = browser_scan("site.com", launch=launch, dial=dial_ok, first_wait=5, quiet=1.0, min_run=1.0, budget=20)
        assert r.reached and r.via == "browser" and not r.error, (r.hosts, r.error)
        assert related("site.com", r.hosts) == ["static.cdn.com"], r.hosts
        assert time.monotonic() - t < 15 and killed == [0]
        # 浏览器想连但连不上 (没网 / 被墙): 不算连上, 报 unreachable
        r = browser_scan("site.com", launch=launch, dial=dial_fail, first_wait=2.0, quiet=1.0, min_run=1.0, budget=8)
        assert not r.reached and r.error == "unreachable", (r.reached, r.error)
        assert pick_route("a.example", None) is None            # 没有别的代理: 直连, 不预先探测
    finally:
        globals()["_kill_tree"] = real_kill
        for s in keep:
            s.close()
    assert not [n for n in os.listdir(tempfile.gettempdir()) if n.startswith("oit-scan-")
                and time.time() - os.path.getmtime(os.path.join(tempfile.gettempdir(), n)) < 120], "临时目录没清掉"
    return "netproxy.sitescan selftest OK"


if __name__ == "__main__":
    print(_selftest())
