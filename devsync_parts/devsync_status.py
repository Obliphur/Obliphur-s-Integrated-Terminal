# -*- coding: utf-8 -*-
"""设备状态 / 账本合计 / 适配器与离线时间段 (MECHANICS 15.7).

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

from devsync_parts.devsync_crypto import (
    P, SealError, accept_proof, dh_keypair, dh_shared, file_key, file_xor, pair_code, seal, unseal
)
from devsync_parts.devsync_util import *


class StatusMixin:
    def peer_ledgers_by_id(self) -> dict:
        with self.lock:
            return {k: v for k, v in self.state["ledgers"].items() if k != self.my_id}


    def mother_id(self):
        """母端的设备 id: 本机是任何一次绑定的母端就是本机, 否则是绑定时选的那台."""
        peers = self.peers()
        if any(p.get("mother") == self.my_id for p in peers.values()):
            return self.my_id
        for p in peers.values():
            if p.get("mother"):
                return p["mother"]
        return None


    def peer_ledgers(self) -> list:
        with self.lock:
            return [v for k, v in self.state["ledgers"].items() if k != self.my_id]


    def is_latest_active(self) -> bool:
        """本机是不是最后有人操作的设备 (只比较最近 2 分钟内同步过的设备)."""
        try:
            mine = float(self.local.status().get("last_op", 0))
        except Exception:
            return True
        now = _now_ms()
        with self.lock:
            for k, st in self.state["status"].items():
                if k == self.my_id or now - float(st.get("ts", 0)) > FRESH_MS:
                    continue
                if float(st.get("last_op", 0)) > mine:
                    return False
        return True


    def device_states(self) -> list:
        """其他设备此刻的状态 (绑定的, 和转交过来的): [{id, name, online, off, timer, net, since, seen, version, outdated}].
        online: 绑定的设备 —— OFFLINE_MS 之内有它的消息 (同步偶然失败一两次不算, 除非是 HARD_ERRORS 里明确的失败),
                版本也不比本机低 (outdated: 版本比本机低, 算离线);
                转交过来的设备 —— OFFLINE_FWD_MS 之内有它的消息.
        off: 关机 —— 连不上了, 但它最后的状态是收到关机指令 / 电量低于 LOW_BATTERY%; 这时 online 也是 True (效果和在线一样).
        net_off: 断网 —— 它换成了 Adapter 断网方式, 走之前告诉了大家 (MECHANICS 15.5); 不同步, 效果也和在线一样.
        本机自己在 Adapter 断网期间, 其他设备都算在线 (连不上是本机的原因, 不把专注记成空闲)."""
        now = _now_ms()
        mine = _vtuple(_call(self.local, "app_version", ""))
        if self._prune_forwarded(now):
            self._save()
        with self.lock:
            peers, status = self.state["peers"], self.state["status"]
            ids = (set(peers) | set(status)) - (set(self.state["gone"]) - set(peers))
            ids.discard(self.my_id)
            out = []
            for i in ids:
                st = status.get(i) or {}
                p = peers.get(i)
                name = (p or {}).get("name") or (self.state["ledgers"].get(i) or {}).get("name") or "?"
                seen = max(float(st.get("ts", 0)), float((p or {}).get("last_ok") or 0))
                ver = str((p or {}).get("version") or "")
                old = bool(mine and _vtuple(ver) and _vtuple(ver) < mine)
                err = str((p or {}).get("last_err") or "")
                if p is not None:
                    online = err not in HARD_ERRORS and now - seen < (REFUSED_OFFLINE_MS if refused(err) else OFFLINE_MS) and not old
                else:
                    online = now - seen < OFFLINE_FWD_MS
                net_off = not online and not old and bool(st.get("adapter"))
                off = not online and not old and not net_off and went_off(st)
                if not online and not old and self.adapter_since():
                    online = True                       # 本机在 Adapter 断网: 别人都算在线
                out.append({"id": i, "name": str(name), "online": online or off or net_off, "off": off,
                            "net_off": net_off,
                            "timer": st.get("timer") or "idle", "net": st.get("net") or "",
                            "since": st.get("since"), "seen": seen, "version": ver, "outdated": old,
                            "err": err})
        return sorted(out, key=lambda d: d["name"])


    def adapter_since(self) -> int:
        """本机从什么时候 (毫秒) 开始用 Adapter 断网, 不在断网时 0."""
        return int(self.state.get("adapter_since") or 0) if not self.state.get("adapter_end") else 0


    def mark_adapter(self):
        """本机马上要换成 Adapter (之后不再同步): 记下开始的时刻, 并把本机状态 ("断网") 发给每台设备."""
        with self.lock:
            if not self.state.get("adapter_since") or self.state.get("adapter_end"):
                self.state["adapter_since"] = _now_ms()
                self.state.pop("adapter_end", None)
        self._save()
        self.push_final(timeout=2.0, force=True)


    def adapter_window(self):
        """Adapter 断网结束了 (换回了 Proxy): (开始, 结束) 毫秒; 还在断网 / 没有断网过时 None."""
        a, b = int(self.state.get("adapter_since") or 0), int(self.state.get("adapter_end") or 0)
        return (a, b) if a and b else None


    def note_proxy_back(self):
        """本机换回了 Proxy: 断网结束的时刻 (之后第一次同步上时按 休息 > 专注 > 空闲 处理重叠)."""
        if self.state.get("adapter_since") and not self.state.get("adapter_end"):
            with self.lock:
                self.state["adapter_end"] = _now_ms()
            self._save()


    def clear_adapter(self):
        with self.lock:
            self.state.pop("adapter_since", None)
            self.state.pop("adapter_end", None)
        self._save()


    def pop_away_prio(self) -> list:
        """刚从自由学习的外出回来的设备: [(设备 id, 开始毫秒, 结束毫秒)], 取出后清空 (本机据此处理自己记录里的重叠, MECHANICS 21.6)."""
        with self.lock:
            out = [(did, int(v[0]), int(v[1])) for did, v in (self.state.get("away_prio") or {}).items()]
            self.state["away_prio"] = {}
        if out:
            self._save()
        return out


    def pop_adapter_back(self) -> list:
        """其他设备从 Adapter 断网回来了: [(设备 id, 开始毫秒, 结束毫秒)], 取出后清空 (本机据此处理自己记录里的重叠)."""
        with self.lock:
            out = [(did, int(v[0]), int(v[1])) for did, v in (self.state.get("adapter_back") or {}).items()]
            self.state["adapter_back"] = {}
        if out:
            self._save()
        return out


    def version_mismatch(self) -> list:
        """绑定的设备里版本和本机不一样的 (知道版本的): [(名字, 版本, 是否比本机新)]."""
        mine = _vtuple(_call(self.local, "app_version", ""))
        out = []
        with self.lock:
            for p in self.state["peers"].values():
                v = _vtuple(p.get("version"))
                if mine and v and v != mine:
                    out.append((str(p.get("name") or "?"), str(p.get("version")), v > mine))
        return sorted(out)


    def _prune_forwarded(self, now: float) -> bool:
        """删掉没有绑定、也不可能再有人转交过来的设备的状态和账 (记进 gone, 免得又被送回来):
        本机一台绑定的设备都没有时全部删掉; 否则删掉 STALE_FWD_MS 之内没有新消息的. 返回删过没有."""
        with self.lock:
            peers, gone = self.state["peers"], self.state["gone"]
            changed = False
            for did in set(self.state["status"]) | set(self.state["ledgers"]):
                if did == self.my_id or did in peers:
                    continue
                st = self.state["status"].get(did) or {}
                if peers and now - float(st.get("ts", 0) or 0) < STALE_FWD_MS:
                    continue
                self.state["status"].pop(did, None)
                self.state["ledgers"].pop(did, None)
                gone[did] = max(int(gone.get(did, 0)), int(now))
                changed = True
            return changed


    def track_offline(self):
        """每秒调用一次: 记下每台设备离线的时间段 (从最后一次有它的消息算起, 到重新有消息为止)."""
        now = _now_ms()
        for d in self.device_states():
            iv = self._offline.setdefault(d["id"], [])
            if not d["online"]:
                if not iv or iv[-1][1] is not None:
                    iv.append([min(d["seen"] or self._started_ms, now), None])
            elif iv and iv[-1][1] is None:
                iv[-1][1] = max(iv[-1][0], d["seen"])
        floor = now - 26 * 3600_000
        for k in list(self._offline):
            self._offline[k] = [v for v in self._offline[k] if v[1] is None or v[1] > floor]


    def add_self_off(self, a_ms: int, b_ms: int):
        """本机关机 (收到关机指令 / 没电) 的这段 [a, b] (MECHANICS 15.4): 本机之后记下的专注里这段记为空闲, 不计入统计."""
        if b_ms <= a_ms:
            return
        floor = _now_ms() - 26 * 3600_000
        with self.lock:
            keep = [v for v in (self.state.get("self_off") or []) if isinstance(v, list) and len(v) == 2 and v[1] > floor]
            keep.append([int(a_ms), int(b_ms)])
            self.state["self_off"] = keep
        self._save()


    def self_off_spans(self, start_ms: float, end_ms: float) -> list:
        """[start, end] 里本机关机的那几段 (15.4) [(开始毫秒, 结束毫秒)]."""
        out = []
        for v in self.state.get("self_off") or []:
            if isinstance(v, list) and len(v) == 2:
                a, b = max(v[0], start_ms), min(v[1], end_ms)
                if b > a:
                    out.append((a, b))
        return sorted(out)


    def offline_spans(self, start_ms: float, end_ms: float, own: bool = True) -> list:
        """[start, end] 里有任意一台设备离线的时间段 (合并过的) [(开始毫秒, 结束毫秒)];
        own: 也包括本机关机的那几段 (15.4)."""
        now = _now_ms()
        spans = []
        for iv in list(self._offline.values()) + ([self.state.get("self_off") or []] if own else []):
            for a, b in iv:
                a, b = max(a, start_ms), min(b if b is not None else now, end_ms)
                if b > a:
                    spans.append([a, b])
        spans.sort()
        out = []
        for a, b in spans:
            if out and a <= out[-1][1]:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        return [(a, b) for a, b in out]


    def peer_status(self) -> list:
        with self.lock:
            return [dict(v, id=k, name=(self.state["ledgers"].get(k) or {}).get("name", "?"))
                    for k, v in self.state["status"].items() if k != self.my_id]
