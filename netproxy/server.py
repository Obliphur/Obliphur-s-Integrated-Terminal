"""
本地过滤代理(HTTP 代理 + HTTPS CONNECT 隧道,不解密 HTTPS)
──────────────────────────────────────────────────────────
· 域名取自 CONNECT 目标 / 绝对 URI / Host 头,不碰加密内容.
· 每条连接先查进程名(procinfo),再交给 decide(host, exe) 判定;拒绝就回 403.
· 若给了 upstream(用户原来的 HTTP 代理),放行的流量转给它而不是直连.
· 每个请求一条连接(强制 Connection: close),实现简单,没有连接复用的状态要维护.
"""

import select
import socket
import threading
import time
from urllib.parse import urlsplit

from . import procinfo

DEFAULT_PORT = 17890
HEAD_LIMIT = 64 * 1024
CONNECT_TIMEOUT = 10.0
IDLE_TIMEOUT = 300.0
RECHECK_EVERY = 1.0          # 已建立的连接每隔多久重新过一遍规则

_REASON_ZH = {
    "offline": "当前离线,只放行网站白名单",
    "blockall": "当前离线,全屏蔽",
    "ban": "合约的网络禁令: 这个软件 / 网站禁止联网",
    "blacklist-app": "该应用在黑名单中",
    "blacklist-domain": "该网站在黑名单中",
    "blacklist-up": "该 UP 主在黑名单中",
    "self": "禁止回环到代理自身",
}
_HOP_HEADERS = {"proxy-connection", "connection", "keep-alive", "proxy-authorization",
                "proxy-authenticate", "te", "trailer", "upgrade"}


def split_hostport(target: str, default_port: int):
    target = target.strip()
    if target.startswith("["):
        end = target.find("]")
        host = target[1:end] if end > 0 else target
        rest = target[end + 1:] if end > 0 else ""
        port = rest[1:] if rest.startswith(":") else ""
    else:
        host, _, port = target.rpartition(":")
        if not host:
            host, port = target, ""
    return host.strip().lower(), (int(port) if port.isdigit() else default_port)


class FilterProxy:

    def __init__(self, decide, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                 upstream=None, on_event=None, max_conns: int = 256):
        self.decide = decide
        self.host = host
        self.preferred_port = port
        self.port = port
        self.upstream = upstream or (lambda: None)
        self.on_event = on_event or (lambda *a, **k: None)
        self._sem = threading.BoundedSemaphore(max_conns)
        self._sock = None
        self._thread = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())


    def start(self) -> int:
        if self.running:
            return self.port
        self._stop.clear()
        last = None
        for offset in range(0, 21):
            port = self.preferred_port + offset if self.preferred_port else 0
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.bind((self.host, port))
                s.listen(128)
            except OSError as e:
                s.close()
                last = e
                if not self.preferred_port:
                    break
                continue
            self._sock = s
            self.port = s.getsockname()[1]
            self._thread = threading.Thread(target=self._accept_loop, daemon=True,
                                            name="oit-filter-proxy")
            self._thread.start()
            return self.port
        raise OSError(f"cannot bind filter proxy: {last}")

    def stop(self):
        self._stop.set()
        s, self._sock = self._sock, None
        if s is not None:
            try:
                s.close()
            except OSError:
                pass

    def _accept_loop(self):
        s = self._sock
        while not self._stop.is_set():
            try:
                cli, addr = s.accept()
            except OSError:
                break
            if not self._sem.acquire(blocking=False):
                cli.close()
                continue
            threading.Thread(target=self._serve, args=(cli, addr), daemon=True).start()


    def _serve(self, cli, addr):
        try:
            self._handle(cli, addr)
        except Exception:
            pass
        finally:
            try:
                cli.close()
            except OSError:
                pass
            self._sem.release()

    @staticmethod
    def _read_head(sock):
        buf = b""
        while b"\r\n\r\n" not in buf:
            if len(buf) > HEAD_LIMIT:
                return None, b""
            try:
                chunk = sock.recv(8192)
            except OSError:
                return None, b""
            if not chunk:
                return None, b""
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        return head, rest

    def _reply(self, cli, code: int, reason: str, body_text: str = "", why: str = ""):
        body = (f"<html><head><meta charset='utf-8'><title>{code}</title></head><body "
                f"style='font-family:sans-serif;margin:3em'><h2>{code} {reason}</h2>"
                f"<p>{body_text}</p><p style='color:#888'>Obliphur's Integrated Terminal</p>"
                f"</body></html>").encode("utf-8")
        head = (f"HTTP/1.1 {code} {reason}\r\nContent-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n"
                f"X-OIT-Reason: {why}\r\n\r\n").encode("latin-1")
        try:
            cli.sendall(head + body)
        except OSError:
            pass

    def _deny(self, cli, host, why):
        msg = _REASON_ZH.get(why, why)
        self._reply(cli, 403, "Forbidden", f"已拦截: <b>{host}</b><br>{msg}", why)

    def _dial(self, host: str, port: int):
        return socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)

    def _open_tunnel(self, host: str, port: int):
        """到目标的 TCP 通道:有上游代理就用 CONNECT 穿过去,否则直连."""
        up = self.upstream()
        if not up:
            s = self._dial(host, port)
            s.settimeout(None)      # 10 秒只管建立连接; 之后和客户端一侧一样阻塞, 由 _relay 管空闲
            return s
        s = self._dial(up[0], up[1])
        try:
            hp = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
            s.sendall(f"CONNECT {hp} HTTP/1.1\r\nHost: {hp}\r\n\r\n".encode("latin-1"))
            head, rest = self._read_head(s)
            if head is None or b" 200" not in head.split(b"\r\n", 1)[0]:
                raise OSError("upstream refused CONNECT")
            if rest:
                raise OSError("unexpected data after upstream CONNECT")
            s.settimeout(None)
            return s
        except Exception:
            s.close()
            raise

    def _is_self(self, host: str, port: int) -> bool:
        return port == self.port and (host in ("localhost", "::1") or host.startswith("127."))

    def _handle(self, cli, addr):
        cli.settimeout(15)
        head, rest = self._read_head(cli)
        if head is None:
            return
        lines = head.split(b"\r\n")
        try:
            method, target, version = lines[0].decode("latin-1").split(" ", 2)
        except ValueError:
            self._reply(cli, 400, "Bad Request")
            return
        exe = procinfo.exe_for_client(addr[1], self.port)

        if method.upper() == "CONNECT":
            host, port = split_hostport(target, 443)
            allow, why = self.decide(host, exe)
            if allow and self._is_self(host, port):
                allow, why = False, "self"
            self.on_event(host, exe, allow, why)
            if not allow:
                self._deny(cli, host, why)
                return
            try:
                up = self._open_tunnel(host, port)
            except OSError:
                self._reply(cli, 502, "Bad Gateway", f"无法连接 {host}:{port}")
                return
            try:
                cli.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if rest:
                    up.sendall(rest)
                cli.settimeout(None)
                self._relay(cli, up, check=self._rechecker(host, exe))
            finally:
                up.close()
            return

        headers = []
        host_header = ""
        for raw in lines[1:]:
            line = raw.decode("latin-1")
            name, _, value = line.partition(":")
            lname = name.strip().lower()
            if lname == "host":
                host_header = value.strip()
            if lname in _HOP_HEADERS:
                continue
            headers.append(line)

        if target.lower().startswith("http://"):
            u = urlsplit(target)
            host, port = (u.hostname or "").lower(), (u.port or 80)
            path = (u.path or "/") + (("?" + u.query) if u.query else "")
        elif target.startswith("/") and host_header:
            host, port = split_hostport(host_header, 80)
            path = target
        else:
            self._reply(cli, 400, "Bad Request", "不支持的请求目标")
            return
        if not host:
            self._reply(cli, 400, "Bad Request")
            return

        allow, why = self.decide(host, exe)
        if allow and self._is_self(host, port):
            allow, why = False, "self"
        self.on_event(host, exe, allow, why)
        if not allow:
            self._deny(cli, host, why)
            return

        up_addr = self.upstream()
        try:
            if up_addr:
                up = self._dial(up_addr[0], up_addr[1])
                first = f"{method} {target} {version}"
            else:
                up = self._dial(host, port)
                first = f"{method} {path} {version}"
        except OSError:
            self._reply(cli, 502, "Bad Gateway", f"无法连接 {host}:{port}")
            return
        try:
            out = [first] + headers + ["Connection: close", "", ""]
            up.sendall("\r\n".join(out).encode("latin-1") + rest)
            up.settimeout(None)
            cli.settimeout(None)
            self._relay(cli, up, check=self._rechecker(host, exe))
        finally:
            up.close()

    def _rechecker(self, host: str, exe: str):
        """已建立的隧道也要服从新规则:名单改了,联网会话结束了,下一秒内就掐断.
        (浏览器会把连接长期复用,只在建立时检查一次的话,新加的黑名单对已打开的站点无效.)"""
        def check() -> bool:
            allow, why = self.decide(host, exe)
            if not allow:
                self.on_event(host, exe, False, why)
            return allow
        return check

    @staticmethod
    def _relay(a, b, idle: float = IDLE_TIMEOUT, check=None):
        peers = {a: b, b: a}
        last_active = last_check = time.monotonic()
        while peers:
            try:
                ready, _, _ = select.select(list(peers), [], [],
                                            RECHECK_EVERY if check else idle)
            except (OSError, ValueError):
                return
            now = time.monotonic()
            if check and now - last_check >= RECHECK_EVERY:
                last_check = now
                if not check():
                    return
            if not ready:
                if now - last_active >= idle:
                    return
                continue
            last_active = now
            for s in ready:
                if s not in peers:
                    continue
                try:
                    data = s.recv(65536)
                except OSError:
                    data = b""
                if not data:
                    other = peers.pop(s)
                    try:
                        other.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    continue
                try:
                    peers[s].sendall(data)
                except (OSError, KeyError):
                    return
