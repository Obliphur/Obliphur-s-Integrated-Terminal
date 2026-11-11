# -*- coding: utf-8 -*-
"""发现与 HTTP 端点: UDP 广播 / 设置里的设备列表 / 启动停止 / hello.

从 devsync.py 的 SyncService 拆出来; 状态仍然全在实例的 self 上.
"""

import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import socket
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from devsync.devsync_crypto import (
    P, SealError, accept_proof, dh_keypair, dh_shared, file_key, file_xor, pair_code, seal, unseal
)
from devsync.devsync_util import *


class DiscoverMixin:
    def start(self):
        for port in range(self.http_port0, self.http_port0 + 10):
            try:
                self._httpd = ThreadingHTTPServer(("0.0.0.0", port), _make_handler(self))
                self._httpd.daemon_threads = True
                self.port = port
                break
            except OSError:
                continue
        if self._httpd:
            threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        try:
            u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            u.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            u.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            u.bind(("0.0.0.0", self.udp_port))
            u.settimeout(1.0)
            self._udp = u
            threading.Thread(target=self._udp_loop, daemon=True).start()
        except OSError:
            self._udp = None
        threading.Thread(target=self._sync_loop, daemon=True).start()
        return self.port


    def stop(self):
        self.dead = True
        self._wake.set()
        try:
            if self._httpd:
                self._httpd.shutdown()
                self._httpd.server_close()
        except Exception:
            pass
        try:
            if self._udp:
                self._udp.close()
        except Exception:
            pass


    def _udp_loop(self):
        while not self.dead:
            try:
                data, addr = self._udp.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                if self.dead:
                    return
                time.sleep(1)
                continue
            try:
                msg = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            if not isinstance(msg, dict) or msg.get("id") == self.my_id:
                continue
            if msg.get("oit") == "discover":
                reply = dict(self.device_info(), oit="here")
                try:
                    self._udp.sendto(json.dumps(reply).encode("utf-8"), addr)
                except OSError:
                    pass
            elif msg.get("oit") == "here":
                self._note_found(msg, addr[0])


    def _note_found(self, msg: dict, ip: str):
        info = {"id": str(msg.get("id")), "name": str(msg.get("name") or "?"),
                "platform": str(msg.get("platform") or "?"), "addr": ip,
                "port": int(msg.get("port") or HTTP_PORT),
                "net_mode": str(msg.get("net_mode") or ""), "ts": time.time()}
        with self.lock:
            self.found[info["id"]] = info
            for p in (self.state["peers"].get(info["id"]), (self.state.get("evict_out") or {}).get(info["id"])):
                if p and (p.get("addr") != ip or p.get("port") != info["port"]):
                    p["addr"], p["port"] = ip, info["port"]


    def _broadcast_targets(self):
        """255.255.255.255 加上每块网卡所在网段的广播地址.
        Windows 只把 255.255.255.255 从一块网卡发出去 (装了虚拟网卡 / VPN 时常常不是 Wi-Fi 那块),
        所以每个网段还要单独发一次. 以前一律按 /24 猜, 10.x / 172.x 这类更大的局域网里找不到对方."""
        targets = {"255.255.255.255"}
        for addr, mask in _ipv4_nets():
            if addr.startswith("127.") or addr.startswith("169.254.") or mask == "255.255.255.255":
                continue
            try:
                a = int.from_bytes(socket.inet_aton(addr), "big")
                m = int.from_bytes(socket.inet_aton(mask), "big")
            except OSError:
                continue
            targets.add(socket.inet_ntoa(((a & m) | (~m & 0xFFFFFFFF)).to_bytes(4, "big")))
        return targets


    def discover(self, wait: float = 2.0) -> list:
        """广播找一下同一局域网里的 OIT, 返回找到的设备 (不含本机)."""
        msg = json.dumps({"oit": "discover", "id": self.my_id, "v": PROTO}).encode("utf-8")
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.settimeout(0.3)
            for t in self._broadcast_targets():
                try:
                    s.sendto(msg, (t, self.udp_port))
                except OSError:
                    pass
            end = time.time() + wait
            while time.time() < end:
                try:
                    data, addr = s.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    m = json.loads(data.decode("utf-8"))
                except Exception:
                    continue
                if isinstance(m, dict) and m.get("oit") == "here" and m.get("id") != self.my_id:
                    self._note_found(m, addr[0])
        finally:
            s.close()
        with self.lock:
            return [dict(v) for v in self.found.values() if time.time() - v["ts"] < 60]


    def hello(self, addr: str, port: int = HTTP_PORT) -> dict:
        st, d = self._http(addr, port, "GET", "/oit/v1/hello", timeout=3.0)
        if st != 200:
            raise OSError(f"HTTP {st}")
        return d
