# -*- coding: utf-8 -*-
"""文件通道: 安装包 / 录像 / 照片的发送与接收, 删除与停止录像的同步.

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


class TransferMixin:
    def offer_update(self, peer_id: str, path: str, platform: str, version: str) -> str:
        """把安装包发给一台设备: 先发签名的说明 (平台, 版本, 大小, SHA-256, 取件码), 对方再来取文件,
        取到后核对大小和 SHA-256. 返回 'sent', 或对方不要的原因 (platform / not-newer / dev …)."""
        p = self.peers().get(peer_id)
        if not p:
            return "unpaired"
        size = os.path.getsize(path)
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        token = secrets.token_hex(16)
        now = time.time()
        with self.lock:
            self.offers = {t: v for t, v in self.offers.items() if v[1] > now}
            self.offers[token] = (path, now + OFFER_TTL)
        env = seal(bytes.fromhex(p["key"]), self.my_id, {"offer": {
            "platform": platform, "version": version, "sha256": h.hexdigest(), "size": size,
            "token": token, "name": os.path.basename(path)}})
        st, reply = self._http(p["addr"], p["port"], "POST", "/oit/v1/offer", env, timeout=10.0)
        if st == 401:
            raise RuntimeError("unpaired")
        if st != 200:
            raise RuntimeError(reply.get("error") or f"HTTP {st}")
        return "sent" if reply.get("ok") else str(reply.get("why") or "declined")


    def offer_file(self, token: str):
        return self.offer_get(token)[0]


    def offer_get(self, token: str):
        """(文件, 加密用的 (密钥, nonce) 或 None); 没有 / 过期了是 (None, None)."""
        with self.lock:
            v = self.offers.get(token)
        if v and v[1] > time.time() and os.path.isfile(v[0]):
            return v[0], (v[2] if len(v) > 2 else None)
        return None, None


    def send_file(self, peer_id: str, path: str, cid: str = "", kind: str = "", final: bool = False) -> str:
        """后台线程: 先发签名的说明 (文件名, 大小, SHA-256, 取件码, nonce), 对方同意后来取 (内容加密传).
        cid: 不为空时这是合约录像 (MECHANICS 18.8), 对方自动收下 (不问用户存到哪里): kind = camera / screen;
        final=True 时是合约主人合并好的最终录像 (对方直接存进 merged 展示), 否则是片段 (发给主人合并).
        返回 'sent', 或对方不要的原因 ('old' = 对方的 OIT 太旧)."""
        p = self.peers().get(peer_id)
        if not p:
            return "unpaired"
        size = os.path.getsize(path)
        if not 0 < size <= FILE_MAX:
            return "size"
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        token, nonce = secrets.token_hex(16), secrets.token_bytes(16)
        k = bytes.fromhex(p["key"])
        now = time.time()
        with self.lock:
            self.offers = {t: v for t, v in self.offers.items() if v[1] > now}
            self.offers[token] = (path, now + OFFER_TTL, (file_key(k), nonce))
        offer = {"name": os.path.basename(path), "size": size, "sha256": h.hexdigest(),
                 "token": token, "nonce": nonce.hex()}
        if cid:
            offer["cid"] = str(cid)
            offer["kind"] = "camera" if kind == "camera" else "screen"
            if final:
                offer["final"] = True
        env = seal(k, self.my_id, {"send": offer})
        st, reply = self._http(p["addr"], p["port"], "POST", "/oit/v1/send", env, timeout=10.0)
        if st == 404:
            return "old"
        if st == 401:
            raise RuntimeError("unpaired")
        if st != 200:
            raise RuntimeError(reply.get("error") or f"HTTP {st}")
        return "sent" if reply.get("ok") else str(reply.get("why") or "declined")


    def del_recording(self, cid: str, kind: str):
        """删掉某个合约某一路合并好的录像时, 通知所有绑定的设备也删 (MECHANICS 18.8). 后台线程里调用."""
        cid = re.sub(r"[^0-9A-Za-z]", "", str(cid))[:24]
        kind = "camera" if kind == "camera" else "screen"
        for pid, p in list(self.peers().items()):
            env = seal(bytes.fromhex(p["key"]), self.my_id, {"recdel": {"cid": cid, "kind": kind}})
            try:
                self._http(p["addr"], p["port"], "POST", "/oit/v1/recdel", env, timeout=5.0)
            except Exception:       # noqa: BLE001
                pass


    def stop_recording(self, cid: str):
        """终止整个录制过程 (MECHANICS 18.8): 通知所有绑定设备停止录像 (违反随合约同步). 后台线程里调用."""
        cid = re.sub(r"[^0-9A-Za-z]", "", str(cid))[:24]
        for pid, p in list(self.peers().items()):
            env = seal(bytes.fromhex(p["key"]), self.my_id, {"recstop": {"cid": cid}})
            try:
                self._http(p["addr"], p["port"], "POST", "/oit/v1/recstop", env, timeout=5.0)
            except Exception:       # noqa: BLE001
                pass


    def _on_recstop(self, env: dict):
        """别的设备终止了录制: 本机也停."""
        pid = str(env.get("from"))
        p = self.peers().get(pid)
        if not p:
            return 401, {"error": "unpaired"}
        try:
            raw = unseal(bytes.fromhex(p["key"]), env)["recstop"]
            cid = re.sub(r"[^0-9A-Za-z]", "", str(raw["cid"]))[:24]
        except SealError as e:
            return 403, {"error": str(e)}
        except Exception:
            return 400, {"error": "bad-request"}
        fn = getattr(self.local, "rec_stop", None)
        if fn is not None:
            try:
                self.runner.call(lambda: fn(cid))
            except Exception:       # noqa: BLE001
                pass
        return 200, {"ok": True}


    def _on_recdel(self, env: dict):
        """别的设备删了某个录像: 本机也删掉对应的 (按 合约 id + 路)."""
        pid = str(env.get("from"))
        p = self.peers().get(pid)
        if not p:
            return 401, {"error": "unpaired"}
        try:
            raw = unseal(bytes.fromhex(p["key"]), env)["recdel"]
            cid = re.sub(r"[^0-9A-Za-z]", "", str(raw["cid"]))[:24]
            kind = "camera" if str(raw.get("kind")) == "camera" else "screen"
        except SealError as e:
            return 403, {"error": str(e)}
        except Exception:
            return 400, {"error": "bad-request"}
        fn = getattr(self.local, "rec_deleted", None)
        if fn is not None:
            try:
                self.runner.call(lambda: fn(cid, kind))
            except Exception:       # noqa: BLE001
                pass
        return 200, {"ok": True}


    def _on_send(self, env: dict):
        """别的设备要发文件过来: 核对说明, 交给本机 (问用户存到哪里 / 直接存), 本机决定后再来取 (fetch_file)."""
        pid = str(env.get("from"))
        p = self.peers().get(pid)
        if not p:
            return 401, {"error": "unpaired"}
        try:
            raw = unseal(bytes.fromhex(p["key"]), env)["send"]
            o = {"name": str(raw["name"]), "size": int(raw["size"]), "sha256": str(raw["sha256"]),
                 "token": str(raw["token"]), "nonce": str(raw["nonce"])}
            if raw.get("cid"):
                o["cid"] = re.sub(r"[^0-9A-Za-z]", "", str(raw["cid"]))[:24]
                o["kind"] = "camera" if str(raw.get("kind")) == "camera" else "screen"
                o["final"] = bool(raw.get("final"))
        except SealError as e:
            return 403, {"error": str(e)}
        except Exception:
            return 400, {"error": "bad-request"}
        if not (0 < o["size"] <= FILE_MAX) or not re.fullmatch(r"[0-9a-f]{64}", o["sha256"]) \
                or not re.fullmatch(r"[0-9a-f]{32}", o["token"]) or not re.fullmatch(r"[0-9a-f]{32}", o["nonce"]):
            return 400, {"error": "bad-request"}
        o["name"] = safe_name(o["name"])
        # 刻痕的录屏片段 (18.8): 合约主人自动收下合并, 不问用户存到哪里
        if o.get("cid"):
            fn = getattr(self.local, "rec_offered", None)
            if fn is None:
                return 200, {"ok": False, "why": "unsupported"}
            try:
                why = self.runner.call(lambda: fn(pid, str(p.get("name") or "?"), o))
            except Exception as e:      # noqa: BLE001
                return 503, {"error": str(e)}
            return 200, ({"ok": True} if not why else {"ok": False, "why": why})
        fn = getattr(self.local, "file_offered", None)
        if fn is None:
            return 200, {"ok": False, "why": "unsupported"}
        try:
            why = self.runner.call(lambda: fn(pid, str(p.get("name") or "?"), o))
        except Exception as e:      # noqa: BLE001
            return 503, {"error": str(e)}
        return 200, ({"ok": True} if not why else {"ok": False, "why": why})


    def fetch_file(self, peer_id: str, o: dict, dest: str):
        """后台线程: 到发来说明的那台设备上取文件, 解密, 核对大小和 SHA-256, 写到 dest; 告诉对方收到了.
        出错时删掉 dest, 抛出异常."""
        p = self.peers().get(peer_id)
        if not p:
            raise RuntimeError("unpaired")
        k = bytes.fromhex(p["key"])
        fk, nonce = file_key(k), bytes.fromhex(o["nonce"])
        h, n, size = hashlib.sha256(), 0, int(o["size"])
        tmp = dest + ".part"
        try:
            conn = http.client.HTTPConnection(p["addr"], int(p["port"]), timeout=30)
            try:
                conn.request("GET", f"/oit/v1/file/{o['token']}", headers={"Content-Length": "0", "Connection": "close"})
                r = conn.getresponse()
                if r.status != 200:
                    raise RuntimeError(f"HTTP {r.status}")
                with open(tmp, "wb") as f:
                    while True:
                        chunk = r.read(1 << 16)
                        if not chunk:
                            break
                        if n + len(chunk) > size:
                            raise ValueError("size mismatch")
                        plain = file_xor(fk, nonce, n, chunk)
                        n += len(chunk)
                        h.update(plain)
                        f.write(plain)
            finally:
                conn.close()
            if n != size or h.hexdigest() != o["sha256"]:
                raise ValueError("checksum mismatch")
            os.replace(tmp, dest)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        try:                         # 告诉对方收到了 (旧版本不认, 不要紧)
            env = seal(k, self.my_id, {"got": {"token": o["token"]}})
            self._http(p["addr"], p["port"], "POST", "/oit/v1/got", env, timeout=5.0)
        except Exception:
            pass


    def _on_got(self, env: dict):
        pid = str(env.get("from"))
        p = self.peers().get(pid)
        if not p:
            return 401, {"error": "unpaired"}
        try:
            token = str(unseal(bytes.fromhex(p["key"]), env)["got"]["token"])
        except SealError as e:
            return 403, {"error": str(e)}
        except Exception:
            return 400, {"error": "bad-request"}
        with self.lock:
            v = self.offers.pop(token, None)
        if v and len(v) > 2:
            fn = getattr(self.local, "file_delivered", None)
            if fn is not None:
                try:
                    self.runner.call(lambda: fn(str(p.get("name") or "?"), os.path.basename(v[0])))
                except Exception:
                    pass
        return 200, {"ok": True}


    def request_update(self, peer_id: str) -> str:
        """请一台绑定的设备给本机下载新版本并发过来 (本机连 GitHub 下不动时). 对方下载好后用 offer 发过来.
        返回 'asked', 或对方不肯的原因 ('old' = 对方的 OIT 太旧, 不认这个请求)."""
        p = self.peers().get(peer_id)
        if not p:
            return "unpaired"
        env = seal(bytes.fromhex(p["key"]), self.my_id, {"want": {
            "platform": "windows", "version": self.runner.call(self.local.app_version)}})
        st, reply = self._http(p["addr"], p["port"], "POST", "/oit/v1/want", env, timeout=10.0)
        if st == 404:
            return "old"
        if st == 401:
            raise RuntimeError("unpaired")
        if st != 200:
            raise RuntimeError(reply.get("error") or f"HTTP {st}")
        return "asked" if reply.get("ok") else str(reply.get("why") or "declined")


    def _on_want(self, env: dict):
        """绑定的设备请本机给它下载新版本并发过去 (例如手机连 GitHub 下不动, 电脑开着代理)."""
        pid = str(env.get("from"))
        p = self.peers().get(pid)
        if not p:
            return 401, {"error": "unpaired"}
        try:
            body = unseal(bytes.fromhex(p["key"]), env)
            if not isinstance(body.get("want"), dict):
                raise ValueError
        except SealError as e:
            return 403, {"error": str(e)}
        except Exception:
            return 400, {"error": "bad-request"}
        fn = getattr(self.local, "update_requested", None)
        if fn is None:
            return 200, {"ok": False, "why": "unsupported"}
        try:
            why = self.runner.call(lambda: fn(pid))
        except Exception as e:      # noqa: BLE001
            return 503, {"error": str(e)}
        return 200, ({"ok": True} if not why else {"ok": False, "why": why})


    def _on_offer(self, env: dict):
        p = self.peers().get(str(env.get("from")))
        if not p:
            return 401, {"error": "unpaired"}
        try:
            body = unseal(bytes.fromhex(p["key"]), env)
            o = body["offer"]
            size, sha, token = int(o["size"]), str(o["sha256"]), str(o["token"])
            platform, version = str(o["platform"]), str(o["version"])
        except SealError as e:
            return 403, {"error": str(e)}
        except Exception:
            return 400, {"error": "bad-request"}
        if not (0 < size <= UPDATE_MAX) or not re.fullmatch(r"[0-9a-f]{64}", sha) \
                or not re.fullmatch(r"[0-9a-f]{32}", token):
            return 400, {"error": "bad-request"}
        try:
            why = self.runner.call(lambda: self.local.update_wanted(platform, version))
        except Exception as e:      # noqa: BLE001
            return 503, {"error": str(e)}
        if why:
            return 200, {"ok": False, "why": why}
        threading.Thread(target=self._fetch_update, args=(dict(p), dict(o)), daemon=True).start()
        return 200, {"ok": True}


    def _fetch_update(self, p: dict, o: dict):
        """后台线程: 到发来说明的那台设备上取安装包, 核对后交给本机安装."""
        dest = None
        try:
            dest = self.runner.call(lambda: self.local.update_dest(str(o["version"])))
            h, n, size = hashlib.sha256(), 0, int(o["size"])
            conn = http.client.HTTPConnection(p["addr"], int(p["port"]), timeout=30)
            try:
                conn.request("GET", f"/oit/v1/file/{o['token']}",
                             headers={"Content-Length": "0", "Connection": "close"})
                r = conn.getresponse()
                if r.status != 200:
                    raise RuntimeError(f"HTTP {r.status}")
                with open(dest, "wb") as f:
                    while True:
                        chunk = r.read(1 << 16)
                        if not chunk:
                            break
                        n += len(chunk)
                        if n > size:
                            raise ValueError("size mismatch")
                        h.update(chunk)
                        f.write(chunk)
            finally:
                conn.close()
            if n != size or h.hexdigest() != o["sha256"]:
                raise ValueError("checksum mismatch")
        except Exception as e:      # noqa: BLE001 —— 告诉用户
            if dest:
                try:
                    os.remove(dest)
                except OSError:
                    pass
            err = str(e) or type(e).__name__
            try:
                self.runner.call(lambda: self.local.update_failed(err))
            except Exception:
                pass
            return
        self.runner.call(lambda: self.local.update_received(dest, str(o["version"]), str(p.get("name") or "?")))
