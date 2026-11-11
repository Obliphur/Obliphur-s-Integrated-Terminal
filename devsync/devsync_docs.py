# -*- coding: utf-8 -*-
"""文档同步: 时间戳 / 合并 / 每 20 秒的同步循环 / 关闭前推送.

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


class DocSyncMixin:
    def _stamp(self, local_docs: dict) -> dict:
        """本机文档 + 每一项的修改时间. 和上一次相比变了的项, 修改时间记为现在."""
        now = _now_ms()
        out = {}
        track = self.state["track"]
        for doc, kv in local_docs.items():
            t = track.setdefault(doc, {})
            for key, val in kv.items():
                h = _hash(val)
                if key not in t or t[key][0] != h:
                    t[key] = [h, now]
            for key in list(t):
                if key not in kv and t[key][0] != H_NONE:
                    t[key] = [H_NONE, now]
            out[doc] = {key: [t[key][1], kv.get(key)] for key in t}
        return out


    def _wins(self, doc, key, mt, val, cur, win_r=None, win_l=None) -> bool:
        """win_r: 发来的这台设备刚从自由学习的外出回来 (MECHANICS 21.6), win_l: 本机刚回来 —— [开始, 结束] 毫秒;
        外出期间 (结束之前) 改过的计时器状态 / 整点记录, 回来的设备有优先权."""
        cur_mt, cur_val = cur
        if doc == "prescript":
            # 指令: 已经结束的记录 / 结算过的奖励永远盖过进行中的, 其他按修改时间
            def done(v):
                return isinstance(v, dict) and (bool(v.get("result")) or v.get("settled") is True)
            if done(val) != done(cur_val):
                return done(val)
        if doc == "hours":
            # 超时自动记录的内容永远让位给自己填写的内容 (行为模式按小时有不同的自动内容)
            auto = _call(self.local, "auto_text_for", None, key) or self.local.auto_text()
            norm = getattr(self.local, "auto_norm", None) or (lambda t: t)       # Sleeping / 睡眠 算同一项
            r_auto, l_auto = norm(val) == auto, norm(cur_val) == auto
            if r_auto and cur_val and not l_auto:
                return False
            if l_auto and val and not r_auto:
                return True
        if win_r and not win_l and _in_away(doc, key, win_r) and int(cur_mt) <= win_r[1]:
            return True                              # 它刚回来: 外出期间本机改的让位给它
        if win_l and not win_r and _in_away(doc, key, win_l) and int(mt) <= win_l[1]:
            return False                             # 本机刚回来: 别的设备在外出期间改的让位给本机
        return int(mt) > int(cur_mt)


    def snapshot(self) -> dict:
        """界面线程: 要发给别人的全部内容."""
        own = self.local.ledger()
        own["rev"] = _now_ms()
        own["name"], own["platform"] = self.my_name, self.platform
        st = self.local.status()
        st["ts"] = _now_ms()
        docs = self._stamp(self.local.docs())
        self._save()
        with self.lock:
            ledgers = dict(self.state["ledgers"])
            status = dict(self.state["status"])
        ledgers[self.my_id] = own
        status[self.my_id] = st
        with self.lock:
            cutoff = _now_ms() - GONE_KEEP_MS
            self.state["gone"] = {k: v for k, v in self.state["gone"].items() if v >= cutoff}
            gone = dict(self.state["gone"])
        return {"v": PROTO, "device": self.device_info(), "ledgers": ledgers, "gone": gone,
                "dev": bool(_call(self.local, "dev_mode", False)),
                "docs": docs, "status": status}


    def merge(self, snap: dict, init: bool = False):
        """界面线程: 并入别人发来的内容. init=True: 本机是子端, 第一次用母端的内容整体初始化."""
        if not isinstance(snap, dict):
            return
        my = self.my_id
        if init:
            self.local.clear_history()
        sender = str((snap.get("device") or {}).get("id") or "")
        if "dev" in snap and sender and sender != my and sender == self.mother_id():
            _call(self.local, "set_dev_mode", None, bool(snap.get("dev")))    # 子端跟着母端
        with self.lock:
            for did, ts in (snap.get("gone") or {}).items():
                if did == my or did in self.state["peers"]:
                    continue                    # 重新绑定过的设备不算
                try:
                    ts = int(ts)
                except (TypeError, ValueError):
                    continue
                if ts > int(self.state["gone"].get(did, 0)):
                    self.state["gone"][did] = ts
                self.state["ledgers"].pop(did, None)
                self.state["status"].pop(did, None)
            for did, led in (snap.get("ledgers") or {}).items():
                if did == my or not isinstance(led, dict):
                    continue
                if did in self.state["gone"] and did not in self.state["peers"] \
                        and int(led.get("rev", 0)) <= int(self.state["gone"][did]):
                    continue                    # 已经重置 / 卸载的设备留下的旧账
                if int(led.get("rev", 0)) > int((self.state["ledgers"].get(did) or {}).get("rev", -1)):
                    self.state["ledgers"][did] = led
            for did, st in (snap.get("status") or {}).items():
                if did == my or not isinstance(st, dict):
                    continue
                if did in self.state["gone"] and did not in self.state["peers"] \
                        and int(st.get("ts", 0)) <= int(self.state["gone"][did]):
                    continue                    # 已经解除 / 重置 / 卸载的设备留下的旧状态
                old_st = self.state["status"].get(did) or {}
                if int(st.get("ts", 0)) > int(old_st.get("ts", -1)):
                    if old_st.get("adapter") and not st.get("adapter"):
                        # 它从 Adapter 断网回来了: 断网期间的记录要处理重叠 (MECHANICS 15.5)
                        self.state.setdefault("adapter_back", {})[did] = [int(old_st["adapter"]), _now_ms()]
                    self.state["status"][did] = st
        mine_seen = (snap.get("status") or {}).get(my)
        if isinstance(mine_seen, dict) and sender and sender != my:
            _call(self.local, "peer_saw_me", None, mine_seen)       # 它们记着的本机上次的状态 (数据的连续性, MECHANICS 9.3)
        local = self.local.docs()
        stamped = self._stamp(local)
        track = self.state["track"]
        now = _now_ms()
        # 自由学习的外出 (MECHANICS 21.6): 外出的设备回来以后第一次同步, 它的记录有优先权
        win_r = _away_win(((snap.get("status") or {}).get(sender) or {}).get("away")) if sender and sender != my else None
        win_l = _away_win(_call(self.local, "away_window", None))
        if win_r:
            with self.lock:
                self.state.setdefault("away_prio", {})[sender] = list(win_r)
        for doc, entries in (snap.get("docs") or {}).items():
            if doc not in local or not isinstance(entries, dict):
                continue
            mine = stamped.get(doc, {})
            changes = {}
            for key, ent in entries.items():
                try:
                    mt, val = int(ent[0]), ent[1]
                except Exception:
                    continue
                cur = mine.get(key)
                if init or cur is None or self._wins(doc, key, mt, val, cur, win_r, win_l):
                    if cur is None or cur[1] != val:
                        changes[key] = val
                    track.setdefault(doc, {})[key] = [_hash(val), mt]
            if init:
                for key, cur in mine.items():
                    if key not in entries and cur[1] is not None:
                        changes[key] = None
                        track[doc][key] = [H_NONE, now]
            if changes:
                try:
                    self.local.apply(doc, changes)
                except Exception:
                    pass
        self._save()
        self.local.changed()


    def _sync_with(self, peer_id: str, init: bool = False, quiet: bool = False):
        """True 成功 / False 失败 / None 这次不用算 (正在同步, 或对方子端还在初始化).
        quiet: 失败了也不记原因 (绑定刚完成时的重试)."""
        p = self.peers().get(peer_id)
        if not p:
            return False
        if p.get("init") and not init:
            init = True
        with self.lock:
            if peer_id in self._busy:
                return None
            self._busy.add(peer_id)
        try:
            return self._sync_once(peer_id, p, init, quiet)
        finally:
            with self.lock:
                self._busy.discard(peer_id)


    def _sync_once(self, peer_id: str, p: dict, init: bool, quiet: bool):
        k = bytes.fromhex(p["key"])
        if peer_id in (self.state.get("wipe_out") or {}):
            # 本机重置过, 对方还没清空: 先送清空请求, 送到之前不合并它的数据
            if self._deliver_wipe(peer_id, p):
                return None
            if not quiet:
                with self.lock:
                    q = self.state["peers"].get(peer_id)
                    if q:
                        q["last_err"] = "wipe-pending"
            return False
        try:
            if init:
                # 子端第一次: 只要母端的内容, 自己的文档不发过去
                snap = self.runner.call(lambda: dict(self.snapshot(), docs={}, ledgers={}))
            else:
                snap = self.runner.call(self.snapshot)
            env = seal(k, self.my_id, {"snap": snap, "init": init})
            st, reply = self._http(p["addr"], p["port"], "POST", "/oit/v1/sync", env,
                                   timeout=SYNC_CONNECT, read_timeout=SYNC_READ)
            if st == 401:
                raise RuntimeError("unpaired")
            if st == 409 and reply.get("error") in ("initializing", "wipe-pending"):
                return None             # 对方是子端在初始化, 或对方要先把清空请求送过来: 不是出错
            if st != 200:
                raise RuntimeError(reply.get("error") or f"HTTP {st}")
            body = unseal(k, reply)
            # 等回复的时候解除了绑定: 回复里的东西不要了 (否则对方的状态又被并回来, 成了解除不掉的"离线"设备)
            if peer_id not in self.peers():
                return False
            self.runner.call(lambda: self.merge(body.get("snap"), init=init))
            with self.lock:
                q = self.state["peers"].get(peer_id)
                if q:
                    q["last_ok"], q["last_err"] = _now_ms(), ""
                    q.pop("refused_since", None)
                    q.pop("refused_last", None)
                    if init:
                        q["init"] = False
                    dev = (body.get("snap") or {}).get("device") or {}
                    if dev.get("name"):
                        q["name"] = str(dev["name"])
                    if dev.get("version"):
                        q["version"] = str(dev["version"])
            self._save()
            return True
        except Exception as e:      # noqa: BLE001 —— 记下来显示
            if not quiet:
                with self.lock:
                    q = self.state["peers"].get(peer_id)
                    if q:
                        q["last_err"] = str(e) or type(e).__name__
                        self._note_refused(q, q["last_err"])
            return False


    def _on_sync(self, env: dict):
        if not self.proxy_ok():
            return 409, {"error": "not-proxy"}
        if _call(self, "away", False):
            return 503, {"error": "away"}             # 本机算作离线 (15.7): 对方同步失败, 把本机算作离线
        if not env.get("from"):
            # 请求体读不出来 (例如超过大小上限): 以前当成"没绑定"回 401, 对方会显示 unpaired
            return 400, {"error": "bad-request"}
        p = self.peers().get(str(env.get("from")))
        if not p:
            return 401, {"error": "unpaired"}
        if p.get("init"):
            return 409, {"error": "initializing"}
        if str(env.get("from")) in (self.state.get("wipe_out") or {}):
            return 409, {"error": "wipe-pending"}      # 先等本机把清空请求送过去, 不收它的旧数据
        k = bytes.fromhex(p["key"])
        try:
            body = unseal(k, env)
        except SealError as e:
            return 403, {"error": str(e)}
        snap = body.get("snap") or {}
        dev = snap.get("device") or {}
        farewell = bool(((snap.get("status") or {}).get(str(dev.get("id") or "")) or {}).get("adapter"))
        if dev.get("net_mode") != "proxy" and not farewell:      # 换成 Adapter 之前的最后一次 (15.5) 照收
            return 409, {"error": "peer-not-proxy"}
        try:
            self.runner.call(lambda: self.merge(snap))
            mine = self.runner.call(self.snapshot)
        except Exception as e:      # noqa: BLE001
            return 503, {"error": str(e)}
        with self.lock:
            q = self.state["peers"].get(str(env.get("from")))
            if q:
                q["last_ok"], q["last_err"] = _now_ms(), ""
                q.pop("refused_since", None)
                q.pop("refused_last", None)
                if (snap.get("device") or {}).get("version"):
                    q["version"] = str(snap["device"]["version"])
        return 200, seal(k, self.my_id, {"snap": mine})


    def sync_now(self):
        """立即同步一轮; 子端也会主动向母端发一次."""
        self._manual = True
        self._wake.set()


    def _sync_loop(self):
        while not self.dead:
            self._wake.wait(SYNC_EVERY)
            self._wake.clear()
            manual, self._manual = getattr(self, "_manual", False), False
            if self.dead:
                return
            if not self.proxy_ok() or _call(self, "away", False):
                continue
            # 母端向子端发请求; 子端只在还没初始化或按了"立即同步"时向母端发
            wipe_out = self.state.get("wipe_out") or {}
            peers = [pid for pid, p in self.peers().items()
                     if p.get("mother") == self.my_id or p.get("init") or manual or pid in wipe_out]
            # 每台设备各用一个线程: 一台卡住 (最多等 SYNC_READ 秒) 不拖慢别的设备, 每轮的间隔也不会被拉长.
            # 上一轮还没完的设备这轮跳过 (_sync_with 返回 None)
            for pid in peers:
                threading.Thread(target=self._sync_peer, args=(pid,), daemon=True).start()


    def _sync_peer(self, pid: str):
        """一台设备的一轮同步. 失败了马上原地再试一次 (偶尔一次连接被重置 / 刚好卡住, 不该让下一整轮才恢复);
        还不行, 地址可能变了 (DHCP): 广播找一下 (每分钟最多一次) 再试."""
        try:
            if self._sync_with(pid) is not False:
                return
            time.sleep(1.0)
            if self._sync_with(pid) is not False:
                return
            with self.lock:
                ok = time.time() - self._last_discover > 60
                if ok:
                    self._last_discover = time.time()
            if ok:
                try:
                    self.discover(1.5)
                except OSError:
                    return
                self._sync_with(pid)
        except Exception:       # noqa: BLE001 —— 一台设备出错不影响别的设备, 也不让这个线程留下报错
            pass


    def synced_since(self, ms: int) -> bool:
        """绑定的设备里有没有哪台在 ms (毫秒时间戳) 之后同步成功过."""
        return any(int(p.get("last_ok") or 0) >= ms for p in self.peers().values())


    def push_final(self, timeout: float = 3.0, force: bool = False):
        """界面线程, 程序关闭前: 把本机此刻的内容发给每台设备 (不等回复里的内容, 最多等 timeout 秒).
        例如共享的专注因为本机关闭而结束了, 其他设备要马上照着结束 (MECHANICS 8.2).
        force: 不看断网方式 (换成 Adapter 之前的最后一次)."""
        if not force and (not self.proxy_ok() or _call(self, "away", False)):
            return
        peers = {pid: p for pid, p in self.peers().items() if not p.get("init")}
        if not peers:
            return
        snap = self.snapshot()

        def send(p):
            try:
                env = seal(bytes.fromhex(p["key"]), self.my_id, {"snap": snap, "init": False})
                self._http(p["addr"], p["port"], "POST", "/oit/v1/sync", env, timeout=timeout)
            except Exception:
                pass
        threads = [threading.Thread(target=send, args=(p,), daemon=True) for p in peers.values()]
        for t in threads:
            t.start()
        end = time.time() + timeout
        for t in threads:
            t.join(max(0.0, end - time.time()))
