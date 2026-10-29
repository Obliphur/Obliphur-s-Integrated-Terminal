"""端到端自检:python -m netproxy.selftest

起一个本地「假网站」和一个本地「假上游代理」,让过滤代理真的转发 / 拦截一遍.
不碰系统代理,不联网.
"""

import socket
import threading

from .rules import RuleStore, WHITE_DOMAINS, BLACK_DOMAINS
from .server import FilterProxy, split_hostport


def _serve_once(handler):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)

    def loop():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=handler, args=(c,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    return srv, srv.getsockname()[1]


def _web(conn):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(4096)
        if not chunk:
            return
        data += chunk
    first = data.split(b"\r\n", 1)[0]
    body = b"hello " + first
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
                 % (len(body), body))
    conn.close()


def _echo(conn):
    while True:
        d = conn.recv(4096)
        if not d:
            break
        conn.sendall(d.upper())
    conn.close()


def _read_all(s):
    out = b""
    while True:
        d = s.recv(65536)
        if not d:
            return out
        out += d


class _Proxy(FilterProxy):
    """把所有「外网」主机重定向到本地假服务器."""

    def __init__(self, *a, targets=None, **kw):
        super().__init__(*a, **kw)
        self.targets = targets or {}

    def _dial(self, host, port):
        if host in self.targets:
            return super()._dial("127.0.0.1", self.targets[host])
        return super()._dial(host, port)


def _http_get(proxy_port, url):
    s = socket.create_connection(("127.0.0.1", proxy_port), timeout=5)
    host = url.split("//", 1)[1].split("/", 1)[0]
    s.sendall(f"GET {url} HTTP/1.1\r\nHost: {host}\r\nProxy-Connection: keep-alive\r\n\r\n".encode())
    return _read_all(s)


def _connect(proxy_port, hostport, payload=b"ping"):
    s = socket.create_connection(("127.0.0.1", proxy_port), timeout=5)
    s.sendall(f"CONNECT {hostport} HTTP/1.1\r\nHost: {hostport}\r\n\r\n".encode())
    head = b""
    while b"\r\n\r\n" not in head:
        d = s.recv(4096)
        if not d:
            break
        head += d
    status = head.split(b"\r\n", 1)[0]
    if b" 200" not in status:
        return status, head
    s.sendall(payload)
    s.shutdown(socket.SHUT_WR)
    return status, _read_all(s)


def run():
    import os
    import tempfile
    assert split_hostport("a.com:8443", 443) == ("a.com", 8443)
    assert split_hostport("[::1]:80", 443) == ("::1", 80)
    assert split_hostport("a.com", 443) == ("a.com", 443)

    web, web_port = _serve_once(_web)
    echo, echo_port = _serve_once(_echo)

    rules = RuleStore(os.path.join(tempfile.mkdtemp(), "r.json"))
    rules.dev_getter = lambda: True                    # 测试里名单改动立即生效
    rules.add(WHITE_DOMAINS, "school.example")
    rules.add(BLACK_DOMAINS, "bad.example")
    state = {"online": False, "mode": "whitelist"}
    events = []

    def decide(host, exe):
        return rules.decide(host, exe, state["online"], state["mode"])

    p = _Proxy(decide, port=0, targets={"school.example": web_port, "bad.example": web_port,
                                         "other.example": web_port, "tls.school.example": echo_port},
               on_event=lambda h, e, a, w: events.append((h, a, w)))
    port = p.start()
    try:
        # 离线:白名单放行,其它拦截
        r = _http_get(port, "http://school.example/x?y=1")
        assert r.startswith(b"HTTP/1.1 200") and b"hello GET /x?y=1 HTTP/1.1" in r, r
        r = _http_get(port, "http://other.example/")
        assert r.startswith(b"HTTP/1.1 403") and b"offline" in r, r
        st, body = _connect(port, "tls.school.example:443")
        assert b" 200" in st and body == b"PING", (st, body)
        st, head = _connect(port, "other.example:443")
        assert b" 403" in st, st

        # 黑名单模式:默认放行,离线也只拦黑名单
        state["mode"] = "blacklist"
        r = _http_get(port, "http://other.example/")
        assert r.startswith(b"HTTP/1.1 200"), r
        r = _http_get(port, "http://bad.example/")
        assert r.startswith(b"HTTP/1.1 403") and b"blacklist-domain" in r, r

        # 联网会话中: 黑名单模式也全部放行 (MECHANICS 4.1)
        state["online"] = True
        r = _http_get(port, "http://other.example/")
        assert r.startswith(b"HTTP/1.1 200"), r
        r = _http_get(port, "http://bad.example/")
        assert r.startswith(b"HTTP/1.1 200"), r
        p.targets["bad-tls.example"] = echo_port
        rules.add(BLACK_DOMAINS, "bad-tls.example")
        st, body = _connect(port, "bad-tls.example:443")
        assert b" 200" in st and body == b"PING", (st, body)

        # 代理-全屏蔽 (Proxy block-all): 离线全部拦截 (403 页面写明原因), 联网全部放行
        state["mode"], state["online"] = "blockall", False
        r = _http_get(port, "http://school.example/")
        assert r.startswith(b"HTTP/1.1 403") and b"blockall" in r, r
        assert "全屏蔽".encode("utf-8") in r, r
        state["online"] = True
        r = _http_get(port, "http://school.example/")
        assert r.startswith(b"HTTP/1.1 200"), r
        state["mode"] = "blacklist"
        st, _ = _connect(port, f"127.0.0.1:{port}")
        assert b" 403" in st, "不允许经代理回环到代理自身"

        # 解析失败 → 502 而不是挂死
        st, _ = _connect(port, "nonexistent.invalid:443")
        assert b" 502" in st or b" 403" in st, st

        # 大块数据完整转发
        big = os.urandom(200_000).replace(b"\r", b"x")
        st, body = _connect(port, "tls.school.example:443", big)
        assert body == big.upper(), "大数据转发被截断"

        assert any(e[0] == "bad.example" and not e[1] for e in events)

        # 已建立的隧道:规则一变就必须被掐断(浏览器会复用连接,不能只在建立时检查)
        import time
        state["online"], state["mode"] = False, "blacklist"
        rules.remove(BLACK_DOMAINS, "later.example")
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(b"CONNECT later.example:443 HTTP/1.1\r\nHost: later.example:443\r\n\r\n")
        p.targets["later.example"] = echo_port
        head = b""
        while b"\r\n\r\n" not in head:
            head += s.recv(4096)
        assert b" 200" in head.split(b"\r\n", 1)[0]
        s.sendall(b"hi")
        assert s.recv(16) == b"HI"
        rules.add(BLACK_DOMAINS, "later.example")          # 现在加入黑名单
        t0 = time.monotonic()
        s.settimeout(5)
        cut = False
        try:
            while True:
                d = s.recv(16)
                if not d:
                    cut = True
                    break
        except OSError:
            cut = True
        assert cut and time.monotonic() - t0 < 4, "新加入黑名单后,已打开的连接没有被掐断"
        s.close()

        # 联网中打开的黑名单网站: 联网会话结束后约 1 秒内被掐断
        state["online"] = True
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(b"CONNECT later.example:443 HTTP/1.1\r\nHost: later.example:443\r\n\r\n")
        head = b""
        while b"\r\n\r\n" not in head:
            head += s.recv(4096)
        assert b" 200" in head.split(b"\r\n", 1)[0], head
        s.sendall(b"hi")
        assert s.recv(16) == b"HI"
        state["online"] = False
        t0 = time.monotonic()
        cut = False
        try:
            while True:
                if not s.recv(16):
                    cut = True
                    break
        except OSError:
            cut = True
        assert cut and time.monotonic() - t0 < 4, "联网结束后, 黑名单网站的连接没有被掐断"
        s.close()

        # 按 UP 主区分 (MECHANICS 4.8): 浏览器开着哪个 UP 主的页面, 由 up_probe 告诉规则库 (这里用假的)
        from .rules import WHITE_UPS, BLACK_UPS

        class Page:
            uids = frozenset()           # 开着的页面属于哪些 UP 主 (空 = 首页之类, None = 认不出来)

            def allowed(self, site, exe, uids):
                return self.uids is not None and bool(self.uids & uids)

            def denied(self, site, exe, uids):
                return self.uids is None or bool(self.uids & uids)
        page = Page()
        rules.up_probe = page
        p.targets["www.bilibili.com"] = web_port             # 网页请求
        p.targets["space.bilibili.com"] = echo_port           # 隧道
        # 黑名单里的站点 + UP 主黑名单: 只拦黑名单里的 UP 主
        rules.add(BLACK_DOMAINS, "bilibili.com")
        state["online"], state["mode"] = False, "blacklist"
        r = _http_get(port, "http://www.bilibili.com/")
        assert r.startswith(b"HTTP/1.1 403") and b"blacklist-domain" in r, "没有 UP 主黑名单: 整站拦"
        rules.add(BLACK_UPS, "https://space.bilibili.com/42?spm_id_from=1")
        assert _http_get(port, "http://www.bilibili.com/").startswith(b"HTTP/1.1 200"), "首页放行"
        page.uids = frozenset({"7"})
        st, body = _connect(port, "space.bilibili.com:443")
        assert b" 200" in st and body == b"PING", "别的 UP 主放行"
        page.uids = None
        assert _http_get(port, "http://www.bilibili.com/").startswith(b"HTTP/1.1 403"), "认不出来的窗口: 拦"
        page.uids = frozenset({"42"})
        r = _http_get(port, "http://www.bilibili.com/")
        assert r.startswith(b"HTTP/1.1 403") and b"blacklist-up" in r, r
        assert "UP 主".encode("utf-8") in r, r
        page.uids = frozenset({"7"})
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(b"CONNECT space.bilibili.com:443 HTTP/1.1\r\nHost: space.bilibili.com:443\r\n\r\n")
        head = b""
        while b"\r\n\r\n" not in head:
            head += s.recv(4096)
        assert b" 200" in head.split(b"\r\n", 1)[0], head
        s.sendall(b"hi")
        assert s.recv(16) == b"HI"
        page.uids = frozenset({"42"})                        # 浏览器换到黑名单 UP 主的页面
        t0 = time.monotonic()
        s.settimeout(5)
        cut = False
        try:
            while True:
                if not s.recv(16):
                    cut = True
                    break
        except OSError:
            cut = True
        assert cut and time.monotonic() - t0 < 4, "浏览器进入黑名单 UP 主的页面后, 已打开的连接没有被掐断"
        s.close()
        # 白名单里的站点 + UP 主白名单: 只放行白名单里的 UP 主
        rules.add(WHITE_DOMAINS, "bilibili.com")
        state["mode"] = "whitelist"
        assert _http_get(port, "http://www.bilibili.com/").startswith(b"HTTP/1.1 200"), "没有 UP 主白名单: 整站放行"
        rules.add(WHITE_UPS, "7")
        page.uids = frozenset({"7"})
        assert _http_get(port, "http://www.bilibili.com/").startswith(b"HTTP/1.1 200")
        page.uids = frozenset({"42"})
        r = _http_get(port, "http://www.bilibili.com/")
        assert r.startswith(b"HTTP/1.1 403") and b"not-whitelisted-up" in r, r
        assert "UP 主白名单".encode("utf-8") in r, r
        page.uids = frozenset()
        assert _http_get(port, "http://www.bilibili.com/").startswith(b"HTTP/1.1 403"), "首页不属于任何 UP 主"
        rules.up_probe = None
    finally:
        p.stop()

    # ── 上游代理链:放行的流量必须穿过「用户原来的代理」
    seen = []

    def fake_up(conn):
        head = b""
        while b"\r\n\r\n" not in head:
            d = conn.recv(4096)
            if not d:
                return
            head += d
        first = head.split(b"\r\n", 1)[0]
        seen.append(first)
        if first.startswith(b"CONNECT"):
            conn.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
            _echo(conn)
        else:
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nUP")
            conn.close()

    up, up_port = _serve_once(fake_up)
    state["online"] = True
    p2 = _Proxy(decide, port=0, upstream=lambda: ("127.0.0.1", up_port),
                targets={"other.example": up_port})
    port2 = p2.start()
    try:
        r = _http_get(port2, "http://other.example/a")
        assert r.endswith(b"UP") and seen[-1].startswith(b"GET http://other.example/a"), (r, seen)
        st, body = _connect(port2, "other.example:443")
        assert b" 200" in st and body == b"PING" and seen[-1].startswith(b"CONNECT other.example:443")
    finally:
        p2.stop()

    # ── 端口被占用 → 顺延
    hold = socket.socket()
    hold.bind(("127.0.0.1", 0))
    hold.listen(1)
    taken = hold.getsockname()[1]
    p3 = FilterProxy(decide, port=taken)
    got = p3.start()
    assert got != taken
    p3.stop()
    hold.close()
    web.close()
    echo.close()
    up.close()
    return "netproxy end-to-end selftest OK"


if __name__ == "__main__":
    print(run())
