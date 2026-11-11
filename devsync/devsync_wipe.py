# -*- coding: utf-8 -*-
"""清除数据 (换设备时): wipe id 的去重 / 收到 wipe / 保留本机数据.

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


class WipeMixin:
    @staticmethod
    def new_wipe_id() -> str:
        return f"{_now_ms()}-{secrets.token_hex(3)}"


    def saw_wipe(self, wipe_id: str) -> bool:
        """这个清空请求以前处理过吗; 没有就记下 (转告来转告去只处理一次)."""
        with self.lock:
            seen = self.state.setdefault("wipes_seen", [])
            if wipe_id in seen:
                return True
            seen.append(wipe_id)
            del seen[:-WIPES_KEEP]
        self._save()
        return False


    def send_wipe(self, wipe_id: str, exclude=(), timeout: float = 3.0):
        """重置: 请每台绑定的设备 (exclude 除外) 清空 OIT 的数据, 绑定不动. 连不上 (或对方是不认识清空请求的旧版本)
        的记进 wipe_out, 下次连上时先送到; 送到之前不和它合并数据 (免得它的旧数据又回来). 最多等 timeout 秒."""
        self.saw_wipe(wipe_id)
        peers = {pid: p for pid, p in self.peers().items() if pid not in exclude}
        ok = {}

        def go(pid, p):
            ok[pid] = self._send_wipe_to(p, wipe_id, False, timeout) == 200
        threads = [threading.Thread(target=go, args=(pid, p), daemon=True) for pid, p in peers.items()]
        for t in threads:
            t.start()
        end = time.time() + timeout
        for t in threads:
            t.join(max(0.0, end - time.time()))
        with self.lock:
            for pid in peers:
                if not ok.get(pid):
                    self.state["wipe_out"][pid] = wipe_id
        self._save()


    def kept_state(self, wiped_by: str = "", leave: bool = False, unbound: str = "") -> dict:
        """重置后留下的同步状态: 本机的 id, 绑定 (密钥, 地址), 已删除的设备, 没送到的清空请求, 解除绑定的计划.
        账, 状态, 修改时间全部清掉 (所有设备的数据都清空了). wiped_by: 是哪台设备请本机清空的 (重开后提示).
        leave: 是别的设备解除了和本机的绑定 (15.1): 只重置本机, 和其他设备的绑定照样留着 (只用来重开后提示)."""
        with self.lock:
            peers = {k: {x: y for x, y in dict(v, last_err="", init=False).items() if x not in ("refused_since", "refused_last")}
                     for k, v in self.state["peers"].items()}
            d = {"device": dict(self.state["device"]), "peers": peers, "track": {}, "ledgers": {}, "status": {},
                 "gone": dict(self.state["gone"]), "wipes_seen": list(self.state.get("wipes_seen") or []),
                 "wipe_out": dict(self.state.get("wipe_out") or {}),
                 "unbinds": dict(self.state.get("unbinds") or {}),
                 "evict_out": dict(self.state.get("evict_out") or {})}
        if wiped_by:
            d["wiped_by"] = wiped_by
            if leave:
                d["wiped_leave"] = True
        if unbound:
            d["unbound_from"] = unbound          # 本机解除了和它们的绑定, 清空了本机的数据 (重开后提示)
        return d


    def pop_unbound_from(self) -> str:
        """重开后: 本机解除了和哪些设备的绑定 (名字, 逗号分隔; 只提示一次)."""
        with self.lock:
            who = str(self.state.pop("unbound_from", "") or "")
        if who:
            self._save()
        return who


    def pop_wiped_by(self):
        """重开后: (是哪台设备请本机清空了数据, 是不是它解除了和本机的绑定) (只提示一次)."""
        with self.lock:
            who = self.state.pop("wiped_by", "")
            leave = bool(self.state.pop("wiped_leave", False))
        if who:
            self._save()
        return str(who), leave


    def _deliver_wipe(self, peer_id: str, p: dict):
        """把没送到的清空请求送过去. True 送到了."""
        wid = (self.state.get("wipe_out") or {}).get(peer_id)
        if not wid:
            return True
        if self._send_wipe_to(p, wid, False, 4.0) != 200:
            return False
        with self.lock:
            self.state["wipe_out"].pop(peer_id, None)
        self._save()
        return True


    def _on_wipe(self, env: dict):
        """别的设备请本机清空数据 (它重置 / 卸载了). unpair: 同时解除和它的绑定 (卸载)."""
        sender = str(env.get("from") or "")
        p = self.peers().get(sender)
        if not p:
            return 401, {"error": "unpaired"}
        try:
            body = unseal(bytes.fromhex(p["key"]), env)
        except SealError as e:
            return 403, {"error": str(e)}
        wid = str(body.get("wipe") or "")
        if not wid:
            return 400, {"error": "bad-request"}
        name = str(p.get("name") or "?")
        leave = bool(body.get("leave"))
        if body.get("unpair"):
            self.runner.call(lambda: self.unpair(sender, notify=False))
        with self.lock:
            self.state.get("wipe_out", {}).pop(sender, None)    # 它自己也清空了, 本机欠它的清空请求不用再送
        if not self.saw_wipe(wid):
            self.runner.call(lambda: self.local.wipe_requested(wid, sender, name, leave))
        return 200, {}
