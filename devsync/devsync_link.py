# -*- coding: utf-8 -*-
"""连接卫生: 离线检测 / 请对方重启 / 解绑与拒绝 / 强制下线.

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


class LinkMixin:
    def check_restart(self, hold: bool = False):
        """每秒: 绑定的设备离线了 (15.3 的判定; 关机 / 断网 / 版本较低 / 对方明确不接受的不算), 马上向它发"重新启动 OIT"的请求;
        连不上 (0) 就每 RESTART_RETRY 秒再试, 有回应 (不管是什么) 就不再发, 直到它回来后再次离线.
        hold: 本机正在外出 (外出期间的设备不算离线). 本机自己的原因 (在别的虚拟桌面 / Adapter 断网) 连不上不是对方的问题, 不发.
        刚打开 OIT 时还没同步过的设备要等一轮同步再说 (免得把其实在线的设备重启了)."""
        if hold or _call(self, "away", False) or self.adapter_since():
            return
        tries = self.__dict__.setdefault("_restart_try", {})
        was_on = self.__dict__.setdefault("_restart_on", set())
        warm = _now_ms() - self._started_ms >= OFFLINE_MS + SYNC_EVERY * 1000
        peers = self.peers()
        for d in self.device_states():
            pid = d["id"]
            p = peers.get(pid)
            if p is None:
                continue
            if d["online"]:
                if d["seen"] >= self._started_ms:
                    was_on.add(pid)            # 本机这次打开之后真的收到过它的消息 (不是上次关机前留下的旧时间)
                tries.pop(pid, None)
                continue
            if d["outdated"] or d["err"] in ("unpaired", "wipe-pending", "away") or not (warm or pid in was_on):
                continue
            t = tries.setdefault(pid, {"at": 0.0, "done": False})
            if t["done"] or time.time() - t["at"] < RESTART_RETRY:
                continue
            t["at"] = time.time()

            def go(pid=pid, p=p, t=t):
                st = self._send_restart_to(p, 4.0)
                if st:
                    t["done"] = True
                    self.runner.call(lambda: _call(self.local, "restart_sent", None, str(p.get("name") or "?"), st))
            threading.Thread(target=go, daemon=True).start()


    def _on_restart(self, env: dict):
        """别的设备发现本机离线了, 请本机重新启动 OIT. 本机在别的虚拟桌面 (away) 时不理; 刚因为这个重启过的也不理."""
        sender = str(env.get("from") or "")
        p = self.peers().get(sender)
        if not p:
            return 401, {"error": "unpaired"}
        try:
            body = unseal(bytes.fromhex(p["key"]), env)
        except SealError as e:
            return 403, {"error": str(e)}
        if not body.get("restart"):
            return 400, {"error": "bad-request"}
        if _call(self, "away", False):
            return 503, {"error": "away"}
        now = _now_ms()
        if now - int(self.state.get("restart_at") or 0) < RESTART_GAP_MS:
            return 200, {"ignored": "recent"}
        if now - self._started_ms < RESTART_FRESH_MS:
            return 200, {"ignored": "fresh"}         # 本机刚打开: 对方看到的"离线"是打开之前的事
        if not p.get("last_err") and now - int(p.get("last_ok") or 0) < RESTART_HEALTHY_MS:
            return 200, {"ignored": "healthy"}       # 刚和它同步成功过 (它发来的同步也算): 本机不是离线的
        with self.lock:
            self.state["restart_at"] = now
        self._save()
        self.runner.call(lambda: self.local.restart_requested(sender, str(p.get("name") or "?")))
        return 200, {}


    def unbind_at(self, peer_id: str):
        """计划解除和这台设备的绑定的时刻 (毫秒), 没有计划时 None."""
        v = (self.state.get("unbinds") or {}).get(peer_id)
        return int(v) if v else None


    def request_unbind(self, peer_id: str) -> bool:
        """开始解除绑定的冷静期. 对方已经解除了和本机的绑定 (或重置过, 同步失败: unpaired) 时直接解除, 不用等.
        返回是不是直接解除了."""
        p = self.peers().get(peer_id)
        if not p:
            return False
        if p.get("last_err") == "unpaired":
            self.unpair(peer_id, notify=False)
            return True
        with self.lock:
            # 无代价解绑: 计划就是"现在", 下一次 check_unbind (每 5 秒) 马上解除, 界面上也不会写 120 小时后的时刻
            self.state.setdefault("unbinds", {})[peer_id] = _now_ms() + (0 if UNBIND_FREE else UNBIND_WAIT_MS)
        self._save()
        return False


    @staticmethod
    def _note_refused(q: dict, err: str, now: int = None):
        """同步失败时 (持有 self.lock): 记下"对方拒绝连接"的这一段 (开始, 最后一次); 别的错误 / 隔太久就重新算."""
        now = _now_ms() if now is None else now
        if not refused(err):
            q.pop("refused_since", None)
            q.pop("refused_last", None)
            return
        if not q.get("refused_since") or now - int(q.get("refused_last") or 0) > GONE_GAP_MS:
            q["refused_since"] = now
        q["refused_last"] = now


    @staticmethod
    def _link_port_state(addr: str, port: int = GONE_LINK_PORT, timeout: float = 3.0) -> str:
        """对方联动程序的端口: \"open\" 有人听 / \"refused\" 主机在线但没人听 / \"unknown\" 超时 / 连不上别的原因."""
        try:
            with socket.create_connection((addr, int(port)), timeout):
                return "open"
        except ConnectionRefusedError:
            return "refused"
        except OSError as e:
            return "refused" if refused(str(e)) else "unknown"


    def _reset_refused(self, pid: str = None):
        with self.lock:
            for k, q in self.state["peers"].items():
                if pid is None or k == pid:
                    q.pop("refused_since", None)
                    q.pop("refused_last", None)


    def check_gone(self, link_state=None, hold: bool = False) -> list:
        """每 5 秒: 绑定的设备连续 GONE_REFUSED_MS 都\"拒绝连接\" (同步端口和联动程序的端口都是), 就当它被卸载了:
        解除和它的绑定 (unpair 同时删掉本机保留的它的账和状态), 返回它们的名字. 本机自己的数据不动 (调用方只提示, 不重置本机).
        联动程序的端口有人听 = 对方的联动程序还在, 不算; 探测不了 (超时) 的这一轮不算也不重置.
        外出 (hold: 本机看到的外出, 外出期间可以关掉 OIT, 联动程序也不启动它, 8.4 / 21) / 本机在别的虚拟桌面或 Adapter 断网 (连不上是本机的原因) /
        对方关机 (off: 收到关机指令 / 没电) 或断网 (net_off: 换成了 Adapter, 15.5) 的时候不算, 并且把\"拒绝连接\"的记录清掉, 回来之后重新算 24 小时."""
        link_state = link_state or self._link_port_state
        if hold or _call(self, "away", False) or self.adapter_since():
            self._reset_refused()
            return []
        now = _now_ms()
        done = []
        states = {d["id"]: d for d in self.device_states()}
        for pid, p in list(self.peers().items()):
            d = states.get(pid)
            if d and (d["off"] or d["net_off"]):
                self._reset_refused(pid)
                continue
            since, last = int(p.get("refused_since") or 0), int(p.get("refused_last") or 0)
            if not since or now - last > GONE_GAP_MS or last - since < GONE_REFUSED_MS:
                continue
            if time.time() - self.__dict__.setdefault("_gone_try", {}).get(pid, 0) < 60:
                continue
            self._gone_try[pid] = time.time()
            st = link_state(p.get("addr"))
            if st == "open":
                with self.lock:
                    q = self.state["peers"].get(pid)
                    if q:
                        q.pop("refused_since", None)
                        q.pop("refused_last", None)
                continue
            if st != "refused":
                continue
            self.unpair(pid, notify=False)
            done.append(str(p.get("name") or "?"))
            self._save()
        return done


    def cancel_unbind(self, peer_id: str):
        with self.lock:
            (self.state.get("unbinds") or {}).pop(peer_id, None)
        self._save()


    def check_unbind(self) -> list:
        """每 5 秒: 冷静期到了的解除绑定 —— 解除, 并通知那台设备 (它只是少了一条绑定, 数据不动); 调用方要重置本机 (清空本机的数据).
        对方离线时通知留在 evict_out, 每 EVICT_RETRY 秒再送, 直到送到 (或 GONE_KEEP_MS 之后放弃).
        返回这次解除了的设备的名字."""
        now = _now_ms()
        done = []
        with self.lock:
            plans = dict(self.state.get("unbinds") or {})
        for pid, at in plans.items():
            if now < int(at or 0) and not UNBIND_FREE:
                continue
            p = self.peers().get(pid)
            with self.lock:
                self.state["unbinds"].pop(pid, None)
                if p:
                    self.state.setdefault("evict_out", {})[pid] = {
                        "key": p["key"], "addr": p["addr"], "port": p["port"], "name": p.get("name", "?"),
                        "since": now}
            if p:
                self.unpair(pid, notify=False)
                done.append(str(p.get("name") or "?"))
            self._save()
        self._deliver_evicts(now)
        return done


    def _deliver_evicts(self, now: int):
        """把解除绑定后还没送到的通知 (只解除绑定, 不清空对方的数据) 送过去 (后台线程)."""
        tries = self.__dict__.setdefault("_evict_try", {})
        with self.lock:
            out = dict(self.state.get("evict_out") or {})
        for pid, e in out.items():
            if now - int(e.get("since") or now) > GONE_KEEP_MS:
                with self.lock:
                    self.state["evict_out"].pop(pid, None)
                self._save()
                continue
            if time.time() - tries.get(pid, 0) < EVICT_RETRY:
                continue
            tries[pid] = time.time()

            def go(pid=pid, e=e):
                st = self._send_unpair_to(e, 4.0)
                if st in (200, 401):           # 送到了 / 它已经不认识本机 (重置过, 或者自己解除了)
                    with self.lock:
                        (self.state.get("evict_out") or {}).pop(pid, None)
                    self._save()
            threading.Thread(target=go, daemon=True).start()
