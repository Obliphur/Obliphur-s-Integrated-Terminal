# -*- coding: utf-8 -*-
"""绑定与解绑: Diffie-Hellman 配对 / 配对码确认 / 忘记本机.

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


class PairMixin:
    def start_pairing(self, addr: str, port: int, mother_is_me: bool):
        """在后台线程里发起绑定; 进度看 self.outgoing."""
        self.outgoing = {"state": "connecting", "addr": addr, "port": int(port), "code": "",
                         "name": "", "error": ""}
        threading.Thread(target=self._pair_worker, args=(addr, int(port), mother_is_me),
                         daemon=True).start()


    def cancel_pairing(self):
        o = self.outgoing
        if o:
            o["state"] = "cancelled"


    def _pair_worker(self, addr, port, mother_is_me):
        o = self.outgoing
        try:
            if not self.proxy_ok():
                raise RuntimeError("not-proxy")
            x, pub = dh_keypair()
            st, d = self._http(addr, port, "POST", "/oit/v1/pair", dict(
                self.device_info(), pub=format(pub, "x"), mother="me" if mother_is_me else "you"))
            if st != 200:
                raise RuntimeError(d.get("error") or f"HTTP {st}")
            k = dh_shared(x, d["pub"])
            token = str(d["token"])
            o.update(state="waiting", code=pair_code(k), name=str(d.get("name") or "?"))
            deadline = time.time() + PAIR_TIMEOUT
            misses = 0
            while time.time() < deadline and o["state"] == "waiting" and not self.dead:
                time.sleep(1.5)
                try:
                    st, r = self._http(addr, port, "GET", f"/oit/v1/pair/{token}", timeout=4.0)
                    misses = 0
                except OSError:
                    # Wi-Fi 偶尔丢一两个包: 等对方确认的这两分钟里, 连续几次连不上才算失败
                    misses += 1
                    if misses >= 5:
                        raise
                    continue
                state = r.get("state")
                if state == "rejected":
                    o["state"] = "rejected"
                    return
                if state == "accepted":
                    if not hmac.compare_digest(str(r.get("proof")), accept_proof(k, token)):
                        raise RuntimeError("bad proof")
                    peer_id = str(d["id"])
                    mother = self.my_id if mother_is_me else peer_id
                    self.runner.call(lambda: self._add_peer(peer_id, d, addr, port, k, mother))
                    o["state"] = "accepted"
                    if mother != self.my_id:
                        self._init_after_accept(peer_id)
                    return
            if o["state"] == "waiting":
                o["state"] = "timeout"
        except Exception as e:      # noqa: BLE001 —— 显示给用户
            o.update(state="error", error=str(e) or type(e).__name__)


    def _add_peer(self, peer_id, info, addr, port, k, mother):
        with self.lock:
            self.state["gone"].pop(peer_id, None)       # 解除过又重新绑定
            self.state["peers"][peer_id] = {
                "name": str(info.get("name") or "?"), "platform": str(info.get("platform") or "?"),
                "addr": addr, "port": int(port), "key": k.hex(), "mother": mother,
                "paired_at": _now_ms(), "last_ok": 0, "last_err": "",
                # 本机是子端: 用母端的内容初始化之前不参加普通同步, 免得自己的旧数据传给母端
                "init": mother != self.my_id}
        self._save()
        self.local.changed()


    def _on_pair(self, body: dict, client_ip: str):
        if not self.proxy_ok():
            return 409, {"error": "not-proxy"}
        if body.get("net_mode") != "proxy":
            return 409, {"error": "peer-not-proxy"}
        try:
            peer_id = str(body["id"])
            x, pub = dh_keypair()
            k = dh_shared(x, str(body["pub"]))
        except Exception:
            return 400, {"error": "bad request"}
        token = secrets.token_hex(8)
        mother = peer_id if body.get("mother") == "me" else self.my_id
        req = {"token": token, "id": peer_id, "name": str(body.get("name") or "?"),
               "platform": str(body.get("platform") or "?"), "addr": client_ip,
               "port": int(body.get("port") or HTTP_PORT), "key": k, "code": pair_code(k),
               "mother": mother, "state": "pending", "ts": time.time()}
        with self.lock:
            for t, r in list(self.incoming.items()):
                if time.time() - r["ts"] > PAIR_TIMEOUT + 30:
                    del self.incoming[t]
            self.incoming[token] = req
        try:
            self.runner.call(lambda: self.local.pair_request(dict(req, key=None)), timeout=3.0)
        except Exception:
            pass
        return 200, {"id": self.my_id, "name": self.my_name, "platform": self.platform,
                     "pub": format(pub, "x"), "token": token}


    def _pair_status(self, token: str):
        r = self.incoming.get(token)
        if not r:
            return 404, {"state": "unknown"}
        out = {"state": r["state"]}
        if r["state"] == "accepted":
            out["proof"] = accept_proof(r["key"], token)
        return 200, out


    def answer_pairing(self, token: str, accept: bool):
        """界面线程: 用户在提示窗里接受 / 拒绝了绑定请求."""
        r = self.incoming.get(token)
        if not r or r["state"] != "pending":
            return False
        if not accept:
            r["state"] = "rejected"
            return True
        self._add_peer(r["id"], r, r["addr"], r["port"], r["key"], r["mother"])
        r["state"] = "accepted"
        # 本机是子端: 主动向母端要初始化的内容. 本机是母端: 等子端来要 —— 这时发起方 (子端) 可能还没
        # 查到"已接受", 马上同步过去只会得到 401 (以前会显示"同步失败: unpaired")
        if r["mother"] != self.my_id:
            threading.Thread(target=self._init_after_accept, args=(r["id"],), daemon=True).start()
        return True


    def _init_after_accept(self, peer_id: str):
        """本机是子端, 刚接受了绑定: 向母端要初始化的内容.
        发起方要等下一次查询 (每 1.5 秒) 才知道已经接受, 在那之前它还不认识本机, 会回 401 ——
        以前只试一次, 失败后要等 20 秒的下一轮, 这期间一直显示"同步失败: unpaired"."""
        for _ in range(12):
            if self.dead or peer_id not in self.peers():
                return
            if self._sync_with(peer_id, init=True, quiet=True):
                return
            time.sleep(1.5)
        self._sync_with(peer_id, init=True)          # 还不行就照常记下原因, 交给定时同步


    def unpair(self, peer_id: str, notify: bool = True, forget: bool = False):
        """解除绑定, 并删掉本机保留的对方的数据 (账, 状态).
        一律记进 gone 并转告其他设备: 以前只有 forget (对方重置 / 卸载了) 时才记, 手动解除的设备 ——
        例如没有先解除绑定就卸载了 OIT 的那台 —— 它的状态会被其他设备 (或解除前已经发出的那一轮同步) 送回来,
        之后一直显示"离线", 却不在绑定列表里, 再也解除不掉. forget 保留给旧的调用方, 现在和不带它一样."""
        with self.lock:
            p = self.state["peers"].pop(peer_id, None)
            self.state["ledgers"].pop(peer_id, None)
            self.state["status"].pop(peer_id, None)
            self.state["gone"][peer_id] = _now_ms()
        self._save()
        self.local.changed()
        if p and notify:
            def go():
                try:
                    env = seal(bytes.fromhex(p["key"]), self.my_id, {"unpair": True})
                    self._http(p["addr"], p["port"], "POST", "/oit/v1/unpair", env, timeout=3.0)
                except Exception:
                    pass
            threading.Thread(target=go, daemon=True).start()


    def _on_unpair(self, env: dict):
        p = self.peers().get(str(env.get("from")))
        if not p:
            return 200, {}
        try:
            body = unseal(bytes.fromhex(p["key"]), env)
        except SealError as e:
            return 403, {"error": str(e)}
        sender = str(env.get("from"))
        self.runner.call(lambda: self.unpair(sender, notify=False, forget=bool(body.get("forget"))))
        return 200, {}


    def forget_me(self, timeout: float = 3.0):
        """重置 / 卸载前: 让每台绑定的设备解除绑定, 并删掉它们保留的本机的数据 (账, 状态);
        它们再转告各自绑定的设备 (见 gone). 不等结果, 最多等 timeout 秒."""
        peers = self.peers()
        threads = [threading.Thread(target=self._send_forget, args=(p, timeout), daemon=True)
                   for p in peers.values()]
        for t in threads:
            t.start()
        end = time.time() + timeout
        for t in threads:
            t.join(max(0.0, end - time.time()))
