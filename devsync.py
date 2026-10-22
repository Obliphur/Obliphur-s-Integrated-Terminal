"""
设备同步 (MECHANICS.md 第 15 节)
────────────────────────────────
同一个局域网里的 OIT (Windows / Android) 互相绑定, 同步设置, 名单, 数据和状态. 不经过任何服务器.

    发现   UDP 17931 广播 {"oit": "discover"}, 收到的回 {"oit": "here", id, name, port …}
    传输   HTTP (TCP 17930 起), 绑定之后的每个请求都加密并签名
    绑定   Diffie-Hellman (RFC 3526 第 14 组) 交换密钥, 两边显示同一个 6 位配对码, 被请求的一方确认
    加密   密钥 K 派生出 enc / mac 两把密钥; 密钥流 = HMAC-SHA256(enc, nonce ‖ 计数器), 再 HMAC 签名
          (只用标准库; Android 版 sync/SyncCrypto.kt 是同一套算法)

同步的内容分三类 (见 snapshot()):
    docs     共享文档: 设置, 整点记录, 计时器 (此刻的专注 / 休息, 积攒的休息时间), 成就, 合约, 行为模式的外出.
              每一项单独记修改时间, 新的覆盖旧的. 规则页的名单和过滤模式不同步
    ledgers  各设备自己的账: 今天的联网额度用量 / 借贷, 学习时长, 专注时间段, 联网理由, 窗口时间.
             互相转交, 每台设备显示的是所有设备的合计
    status   各设备此刻的状态 (专注 / 休息 / 联网) 和最后一次操作的时间 (整点提示音只在最后操作的设备上响)

同步由母端发起: 每 30 秒向每台子端发一次请求, 双方合并后回复; 子端只在第一次初始化和按"立即同步"时主动发.

第一次绑定时选一端作为母端: 另一端清掉自己的历史数据, 用母端的文档和账初始化 (今天已用的联网额度保留).

本模块不依赖 tkinter; 和本机数据打交道的部分由 local (见 sync_local.py) 提供.
所有改动本机数据的操作都通过 run_main 交给界面线程执行.

    python devsync.py       自检 (两个实例在本机互相绑定, 同步)
"""

import base64
import hashlib
import hmac
import http.client
import json
import os
import queue
import re
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROTO = 1
HTTP_PORT = 17930          # 被占用时依次尝试到 17939
UDP_PORT = 17931
SYNC_EVERY = 10            # 秒: 母端每隔这么久向每台子端发一次同步请求
# 设备离线 (Timer 页显示"离线", 这段专注记为空闲, MECHANICS 8.4): 和它同步失败, 或这么久没有它的消息.
# 比同步间隔多留 5 秒 (同步本身要花时间, 正好 10 秒会每一轮都闪一下"离线"); 只是转交过来的设备再多等一轮
OFFLINE_MS = (SYNC_EVERY + 5) * 1000
UNBIND_WAIT_MS = 24 * 3600 * 1000       # 解除全部绑定要等这么久 (期间可以取消)
WIPES_KEEP = 50                         # 记住最近这么多个处理过的清空请求
OFFLINE_FWD_MS = (2 * SYNC_EVERY + 5) * 1000
# 关机 (MECHANICS 15.4): 一台设备最后发来的状态写着"收到关机指令" (off), 或者电量低于这么多 (%, 没在充电) 时突然离线,
# 算作"关机": 效果和在线一样 (不算离线, 专注不记空闲, 合约不因它失败), 只是不同步. 重新同步上就恢复正常.
# 前提是它开着开机自启动 (状态里 auto, MECHANICS 3.4 / 14.8): 没开 / 开不了的照旧算"离线".
LOW_BATTERY = 2
UPDATE_MAX = 160 * 1024 * 1024    # 设备之间直接传的安装包最大多少
OFFER_TTL = 1800                  # 秒: 发出的安装包多久之内可以来取
FILE_MAX = 1 << 30                # 文件通道 (MECHANICS 15.6) 一次最多传多大
# 没有绑定、只是别的设备转交过来的设备, 这么久没有新消息就不再显示, 也不再算"离线" (它多半已经卸载了)
STALE_FWD_MS = 24 * 3600_000
FRESH_MS = 120_000         # 毫秒: 这么久之内同步过的设备才算"在线" (比较最后操作时间用)
PAIR_TIMEOUT = 120         # 秒: 等对方确认绑定
TS_SKEW = 300              # 秒: 请求里的时间戳允许和本机相差多少

# RFC 3526, 2048-bit MODP Group 14
P = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A0879"
    "8E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B"
    "0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA4836"
    "1C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804"
    "F1746C08CA18217C32905E462E36CE3BE39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6"
    "955817183995497CEA956AE515D2261898FA051015728E5A8AACAA68FFFFFFFFFFFFFFFF", 16)
G = 2

# 设置里每台设备各自决定的项, 不同步
LOCAL_SETTINGS = {"NET_MODE", "FILTER_MODE", "FONT_SCALE", "CHIME_WAV",
                  "WINDOW_LIMIT_ENABLED", "WINDOW_LIMIT", "WINDOW_FOCUS_ENABLED",
                  "UPDATE_CHECK", "UPDATE_AUTO", "REC_ASK", "REC_MIC", "REC_SYSTEM"}


# ═══════════════════════ 密码学 ═══════════════════════

def _h(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


def dh_keypair():
    x = secrets.randbits(256) | (1 << 255)
    return x, pow(G, x, P)


def dh_shared(x: int, peer_pub_hex: str) -> bytes:
    y = int(peer_pub_hex, 16)
    if not 1 < y < P - 1:
        raise ValueError("bad public key")
    s = pow(y, x, P)
    return hashlib.sha256(s.to_bytes(256, "big")).digest()


def pair_code(k: bytes) -> str:
    n = int.from_bytes(hashlib.sha256(k + b"code").digest()[:4], "big") % 1000000
    return f"{n:06d}"


def accept_proof(k: bytes, token: str) -> str:
    return _h(k, b"accepted:" + token.encode()).hex()


def _keystream_xor(enc: bytes, nonce: bytes, data: bytes) -> bytes:
    out = bytearray(len(data))
    for i in range(0, len(data), 32):
        block = _h(enc, nonce + (i // 32).to_bytes(4, "big"))
        for j in range(min(32, len(data) - i)):
            out[i + j] = data[i + j] ^ block[j]
    return bytes(out)


def file_key(k: bytes) -> bytes:
    """文件通道的加密密钥 (由绑定时的密钥算出)."""
    return _h(k, b"file")


def file_xor(fk: bytes, nonce: bytes, offset: int, data: bytes) -> bytes:
    """文件通道: 文件内容和密钥流异或 (加密 / 解密一样). 密钥流第 i 个 32 字节 = SHA-256(密钥 + nonce + i),
    可以从任意位置 offset 开始接着算. 同 Android 的 SyncCrypto.fileXor."""
    if not data:
        return b""
    first, skip = divmod(int(offset), 32)
    count = (skip + len(data) + 31) // 32
    pre = fk + nonce
    stream = b"".join(hashlib.sha256(pre + (first + i).to_bytes(8, "big")).digest() for i in range(count))
    ks = stream[skip:skip + len(data)]
    return (int.from_bytes(data, "big") ^ int.from_bytes(ks, "big")).to_bytes(len(data), "big")


def seal(k: bytes, sender: str, obj, nonce: bytes = None, ts: int = None) -> dict:
    nonce = nonce or secrets.token_bytes(16)
    ts = int(time.time() * 1000) if ts is None else ts
    pt = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ct = _keystream_xor(_h(k, b"enc"), nonce, pt)
    mac = _h(_h(k, b"mac"), nonce + ts.to_bytes(8, "big") + ct).hex()
    return {"from": sender, "nonce": base64.b64encode(nonce).decode(), "ts": ts,
            "ct": base64.b64encode(ct).decode(), "mac": mac}


class SealError(Exception):
    pass


_seen_nonces = {}
_seen_lock = threading.Lock()


def unseal(k: bytes, env: dict, replay: bool = True):
    try:
        nonce = base64.b64decode(env["nonce"])
        ct = base64.b64decode(env["ct"])
        ts = int(env["ts"])
        mac = str(env["mac"])
    except Exception:
        raise SealError("malformed")
    want = _h(_h(k, b"mac"), nonce + ts.to_bytes(8, "big") + ct).hex()
    if not hmac.compare_digest(want, mac):
        raise SealError("bad mac")
    if abs(time.time() * 1000 - ts) > TS_SKEW * 1000:
        raise SealError("clock")
    if replay:
        with _seen_lock:
            now = time.time()
            for n, t in list(_seen_nonces.items()):
                if now - t > 2 * TS_SKEW:
                    del _seen_nonces[n]
            if mac in _seen_nonces:
                raise SealError("replay")
            _seen_nonces[mac] = now
    return json.loads(_keystream_xor(_h(k, b"enc"), nonce, ct).decode("utf-8"))


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


H_NONE = _hash(None)


# ═══════════════════════ 交给界面线程执行 ═══════════════════════

class MainRunner:
    """其他线程把要改本机数据的操作排进来, 界面线程定期 pump() 执行 (tk 不是线程安全的)."""

    def __init__(self):
        self.q = queue.Queue()

    def call(self, fn, timeout: float = 8.0):
        box, ev = {}, threading.Event()
        self.q.put((fn, box, ev))
        if not ev.wait(timeout):
            raise TimeoutError("main thread busy")
        if "err" in box:
            raise box["err"]
        return box.get("ok")

    def pump(self, limit: int = 20):
        for _ in range(limit):
            try:
                fn, box, ev = self.q.get_nowait()
            except queue.Empty:
                return
            try:
                box["ok"] = fn()
            except Exception as e:      # noqa: BLE001 —— 原样交回调用的线程
                box["err"] = e
            ev.set()


# ═══════════════════════ 同步服务 ═══════════════════════

def _vtuple(text):
    """'1.0.55' → (1, 0, 55); 没有数字时 None (同 updater.parse_version)."""
    nums = [int(n) for n in re.findall(r"\d+", str(text or ""))[:3]]
    return tuple(nums + [0] * (3 - len(nums))) if nums else None


def _now_ms() -> int:
    return int(time.time() * 1000)


class SyncService:

    def __init__(self, data_dir: str, local, runner: MainRunner, platform: str = "windows",
                 name: str = None, http_port: int = HTTP_PORT, udp_port: int = UDP_PORT):
        self.file = os.path.join(data_dir, "sync_state.json")
        self.local = local
        self.runner = runner
        self.platform = platform
        self.http_port0 = http_port
        self.udp_port = udp_port
        self.port = None
        self.lock = threading.RLock()
        self.dead = False
        self.incoming = {}        # token -> 别人发来的绑定请求
        self.outgoing = None      # 本机发起的绑定 {state, code, name, …}
        self.found = {}           # id -> 广播找到的设备
        self.offers = {}          # token -> (文件, 过期时刻[, (密钥, nonce)]): 发给其他设备的安装包 / 文件, 等对方来取
        # 本机算作离线吗 (电脑: 用户切到了 OIT 不在的虚拟桌面, MECHANICS 15.7): 这时不同步, 也不接受同步
        self.away = lambda: False
        self._httpd = None
        self._udp = None
        self._wake = threading.Event()
        self._busy = set()        # 正在和哪些设备同步 (同一台设备不同时跑两轮)
        # 每台设备离线的时间段 {id: [[开始毫秒, 结束毫秒或 None], …]}, 只在内存里 (MECHANICS 8.4)
        self._offline = {}
        self._started_ms = _now_ms()
        self.firewall = None      # 本机防火墙检查的结果 (firewall.check, 由 app 在后台填)
        self.state = self._load()
        if not self.state.get("device"):
            self.state["device"] = {"id": secrets.token_hex(6),
                                    "name": name or socket.gethostname() or "OIT"}
            self._save()
        elif name and not self.state["device"].get("name"):
            self.state["device"]["name"] = name

    # ---- 存档 ----

    def _load(self) -> dict:
        try:
            with open(self.file, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                for k in ("peers", "track", "ledgers", "status", "gone", "wipe_out"):
                    if not isinstance(d.get(k), dict):
                        d[k] = {}
                if not isinstance(d.get("wipes_seen"), list):
                    d["wipes_seen"] = []
                return d
        except Exception:
            pass
        return {"device": None, "peers": {}, "track": {}, "ledgers": {}, "status": {}, "gone": {},
                "wipe_out": {}, "wipes_seen": []}

    def _save(self):
        if self.dead:
            return
        with self.lock:
            tmp = self.file + ".tmp"
            try:
                os.makedirs(os.path.dirname(self.file) or ".", exist_ok=True)
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.state, f, ensure_ascii=False)
                os.replace(tmp, self.file)
            except OSError:
                pass

    @property
    def my_id(self) -> str:
        return self.state["device"]["id"]

    @property
    def my_name(self) -> str:
        return self.state["device"]["name"]

    def peers(self) -> dict:
        with self.lock:
            return {k: dict(v) for k, v in self.state["peers"].items()}

    def proxy_ok(self) -> bool:
        try:
            return self.local.net_mode() == "proxy"
        except Exception:
            return False

    def device_info(self) -> dict:
        return {"id": self.my_id, "name": self.my_name, "platform": self.platform,
                "port": self.port, "net_mode": self.local.net_mode(), "v": PROTO,
                "version": _call(self.local, "app_version", "")}

    # ---- 启动 / 停止 ----

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

    # ---- 发现 ----

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
            p = self.state["peers"].get(info["id"])
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

    # ---- HTTP 客户端 ----

    @staticmethod
    def _http(addr: str, port: int, method: str, path: str, body=None, timeout: float = 6.0):
        conn = http.client.HTTPConnection(addr, int(port), timeout=timeout)
        try:
            data = json.dumps(body).encode("utf-8") if body is not None else b""
            # GET 也写明 Content-Length: 0 —— 旧版手机端没有它时会一直读到连接关闭, 卡到超时 (timed out)
            conn.request(method, path, body=data,
                         headers={"Content-Type": "application/json", "Connection": "close",
                                  "Content-Length": str(len(data))})
            r = conn.getresponse()
            raw = r.read(8_000_000)
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                payload = {}
            return r.status, payload
        finally:
            conn.close()

    def hello(self, addr: str, port: int = HTTP_PORT) -> dict:
        st, d = self._http(addr, port, "GET", "/oit/v1/hello", timeout=3.0)
        if st != 200:
            raise OSError(f"HTTP {st}")
        return d

    # ---- 绑定: 发起方 ----

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

    # ---- 绑定: 被请求方 ----

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
        以前只试一次, 失败后要等 30 秒的下一轮, 这期间一直显示"同步失败: unpaired"."""
        for _ in range(12):
            if self.dead or peer_id not in self.peers():
                return
            if self._sync_with(peer_id, init=True, quiet=True):
                return
            time.sleep(1.5)
        self._sync_with(peer_id, init=True)          # 还不行就照常记下原因, 交给定时同步

    # ---- 解除绑定 ----

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

    # ---- 清空数据 (重置 / 卸载, MECHANICS 9, 15.1) ----

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
            ok[pid] = send_wipe_to(self.my_id, p, wipe_id, False, timeout) == 200
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

    def kept_state(self, wiped_by: str = "") -> dict:
        """重置后留下的同步状态: 本机的 id, 绑定 (密钥, 地址), 已删除的设备, 没送到的清空请求, 解除绑定的计划.
        账, 状态, 修改时间全部清掉 (所有设备的数据都清空了). wiped_by: 是哪台设备请本机清空的 (重开后提示)."""
        with self.lock:
            peers = {k: dict(v, last_err="", init=False) for k, v in self.state["peers"].items()}
            d = {"device": dict(self.state["device"]), "peers": peers, "track": {}, "ledgers": {}, "status": {},
                 "gone": dict(self.state["gone"]), "wipe_out": dict(self.state.get("wipe_out") or {}),
                 "wipes_seen": list(self.state.get("wipes_seen") or [])}
            if self.state.get("unbind_at"):
                d["unbind_at"] = self.state["unbind_at"]
        if wiped_by:
            d["wiped_by"] = wiped_by
        return d

    def pop_wiped_by(self) -> str:
        """重开后: 是哪台设备请本机清空了数据 (只提示一次)."""
        with self.lock:
            who = self.state.pop("wiped_by", "")
        if who:
            self._save()
        return str(who)

    def _deliver_wipe(self, peer_id: str, p: dict):
        """把没送到的清空请求送过去. True 送到了."""
        wid = (self.state.get("wipe_out") or {}).get(peer_id)
        if not wid:
            return True
        if send_wipe_to(self.my_id, p, wid, False, 4.0) != 200:
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
        if body.get("unpair"):
            self.runner.call(lambda: self.unpair(sender, notify=False))
        with self.lock:
            self.state.get("wipe_out", {}).pop(sender, None)    # 它自己也清空了, 本机欠它的清空请求不用再送
        if not self.saw_wipe(wid):
            self.runner.call(lambda: self.local.wipe_requested(wid, sender, name))
        return 200, {}

    # ---- 解除全部绑定 (要等 24 小时, 期间可以取消) ----

    @property
    def unbind_at(self):
        """计划解除全部绑定的时刻 (毫秒), 没有计划时 None."""
        v = self.state.get("unbind_at")
        return int(v) if v else None

    def request_unbind(self):
        with self.lock:
            self.state["unbind_at"] = _now_ms() + UNBIND_WAIT_MS
        self._save()

    def cancel_unbind(self):
        with self.lock:
            self.state.pop("unbind_at", None)
        self._save()

    def check_unbind(self) -> bool:
        """到时间了就解除和所有设备的绑定 (通知对方, 各自保留数据). 返回这次解除了没有."""
        at = self.unbind_at
        if not at or _now_ms() < at:
            return False
        with self.lock:
            self.state.pop("unbind_at", None)
        for pid in list(self.peers()):
            self.unpair(pid, notify=True)
        self._save()
        return True

    # ---- 文档 (每一项单独记修改时间) ----

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

    def _wins(self, doc, key, mt, val, cur) -> bool:
        cur_mt, cur_val = cur
        if doc == "hours":
            # 超时自动记录的内容永远让位给自己填写的内容 (行为模式按小时有不同的自动内容)
            auto = _call(self.local, "auto_text_for", None, key) or self.local.auto_text()
            r_auto, l_auto = val == auto, cur_val == auto
            if r_auto and cur_val and not l_auto:
                return False
            if l_auto and val and not r_auto:
                return True
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
        local = self.local.docs()
        stamped = self._stamp(local)
        track = self.state["track"]
        now = _now_ms()
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
                if init or cur is None or self._wins(doc, key, mt, val, cur):
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

    # ---- 同步 ----

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
            st, reply = self._http(p["addr"], p["port"], "POST", "/oit/v1/sync", env, timeout=10.0)
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
                if (snap.get("device") or {}).get("version"):
                    q["version"] = str(snap["device"]["version"])
        return 200, seal(k, self.my_id, {"snap": mine})

    # ---- 安装包直接发给其他设备 (MECHANICS 16) ----

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

    # ---- 文件通道 (MECHANICS 15.6): 把文件 / 图片发给一台绑定的设备 ----

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
        threads = [threading.Thread(target=send_forget, args=(self.my_id, p, timeout), daemon=True)
                   for p in peers.values()]
        for t in threads:
            t.start()
        end = time.time() + timeout
        for t in threads:
            t.join(max(0.0, end - time.time()))

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

    def synced_since(self, ms: int) -> bool:
        """绑定的设备里有没有哪台在 ms (毫秒时间戳) 之后同步成功过."""
        return any(int(p.get("last_ok") or 0) >= ms for p in self.peers().values())

    def sync_now(self):
        """立即同步一轮; 子端也会主动向母端发一次."""
        self._manual = True
        self._wake.set()

    def _sync_loop(self):
        last_discover = 0.0
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
            peers = {pid: p for pid, p in self.peers().items()
                     if p.get("mother") == self.my_id or p.get("init") or manual or pid in wipe_out}
            failed = [pid for pid in peers if self._sync_with(pid) is False]
            # 有设备连不上: 地址可能变了 (DHCP), 广播找一下再试一次
            if failed and time.time() - last_discover > 60:
                last_discover = time.time()
                try:
                    self.discover(1.5)
                except OSError:
                    pass
                for pid in failed:
                    self._sync_with(pid)

    # ---- 给本机其他部分用: 其他设备的账 ----

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
        online: 绑定的设备 —— 最近一次同步没有失败, 而且 OFFLINE_MS 之内有它的消息, 版本也不比本机低
                (outdated: 版本比本机低, 算离线);
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
                if p is not None:
                    online = not p.get("last_err") and now - seen < OFFLINE_MS and not old
                else:
                    online = now - seen < OFFLINE_FWD_MS
                net_off = not online and not old and bool(st.get("adapter"))
                off = not online and not old and not net_off and went_off(st)
                if not online and not old and self.adapter_since():
                    online = True                       # 本机在 Adapter 断网: 别人都算在线
                out.append({"id": i, "name": str(name), "online": online or off or net_off, "off": off,
                            "net_off": net_off,
                            "timer": st.get("timer") or "idle", "net": st.get("net") or "",
                            "since": st.get("since"), "seen": seen, "version": ver, "outdated": old})
        return sorted(out, key=lambda d: d["name"])

    # ---- Adapter 断网 (MECHANICS 15.5) ----

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


def went_off(st: dict) -> bool:
    """这台设备最后发来的状态: 开着开机自启动, 并且收到了关机指令 / 电量低于 LOW_BATTERY% (没在充电)."""
    if not isinstance(st, dict) or not st.get("auto"):
        return False
    if st.get("off"):
        return True
    try:
        b = st.get("battery")
        return b is not None and float(b) < LOW_BATTERY
    except (TypeError, ValueError):
        return False


def battery_percent():
    """本机电量 (%, 只在用电池供电时); 台式机 / 插着电 / 查不到时 None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class _SPS(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
        s = _SPS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
            return None
        if s.ACLineStatus != 0 or s.BatteryFlag & 128 or s.BatteryLifePercent > 100:
            return None                   # 插着电 / 没有电池 / 不知道
        return int(s.BatteryLifePercent)
    except Exception:
        return None


def _ipv4_nets() -> list:
    """[(地址, 掩码)] 本机各网卡的 IPv4. Windows 用 GetIpAddrTable 取真实掩码; 其他平台按 /24 猜."""
    out = []
    if os.name == "nt":
        try:
            import ctypes
            import struct

            class Row(ctypes.Structure):
                _fields_ = [("addr", ctypes.c_uint32), ("index", ctypes.c_uint32),
                            ("mask", ctypes.c_uint32), ("bcast", ctypes.c_uint32),
                            ("reasm", ctypes.c_uint32), ("unused", ctypes.c_ushort),
                            ("type", ctypes.c_ushort)]
            iphlpapi = ctypes.WinDLL("iphlpapi")
            size = ctypes.c_ulong(0)
            iphlpapi.GetIpAddrTable(None, ctypes.byref(size), False)
            buf = ctypes.create_string_buffer(size.value + 64)
            if iphlpapi.GetIpAddrTable(buf, ctypes.byref(size), False) == 0:
                n = ctypes.c_ulong.from_buffer(buf).value
                rows = (Row * n).from_buffer(buf, ctypes.sizeof(ctypes.c_ulong))
                for r in rows:
                    if r.type & (0x0008 | 0x0040):         # 已断开 / 已删除的地址
                        continue
                    out.append((socket.inet_ntoa(struct.pack("<I", r.addr)),
                                socket.inet_ntoa(struct.pack("<I", r.mask))))
            if out:
                return out
        except Exception:
            out = []
    try:
        for _f, _t, _p, _c, sa in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            out.append((sa[0], "255.255.255.0"))
    except OSError:
        pass
    return out


GONE_KEEP_MS = 62 * 86400_000      # 记住"哪台设备已经重置 / 卸载了"多久 (统计只保留本月和上月)


def send_wipe_to(my_id: str, peer: dict, wipe_id: str, unpair: bool, timeout: float = 3.0) -> int:
    """请一台绑定的设备清空 OIT 的数据 (unpair: 同时解除和本机的绑定). 返回 HTTP 状态 (连不上时 0;
    404 = 对方是不认识清空请求的旧版本). uninstall.py 也用它."""
    try:
        env = seal(bytes.fromhex(peer["key"]), my_id, {"wipe": wipe_id, "unpair": bool(unpair)})
        st, _ = SyncService._http(peer["addr"], peer["port"], "POST", "/oit/v1/wipe", env, timeout=timeout)
        return st
    except Exception:
        return 0


def write_state(path: str, state: dict):
    """重置后把留下的同步状态写回去 (数据目录刚被删掉)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def send_forget(my_id: str, peer: dict, timeout: float = 3.0) -> bool:
    """告诉一台绑定的设备: 本机要重置 / 卸载了, 解除绑定并删掉本机的数据. uninstall.py 也用它."""
    try:
        env = seal(bytes.fromhex(peer["key"]), my_id, {"unpair": True, "forget": True})
        st, _ = SyncService._http(peer["addr"], peer["port"], "POST", "/oit/v1/unpair", env, timeout=timeout)
        return st == 200
    except Exception:
        return False


def safe_name(name: str) -> str:
    """别的设备发来的文件名: 只留文件名本身, 去掉路径和 Windows 不允许的字符."""
    name = re.split(r"[\\/]", str(name or ""))[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:180] or "file"


def _call(obj, name: str, default, *args):
    """本机接口里可选的方法 (测试用的 _FakeLocal 不一定有)."""
    fn = getattr(obj, name, None)
    if fn is None:
        return default
    try:
        return fn(*args)
    except Exception:
        return default


def peer_net(ledgers, date: str) -> dict:
    """其他设备今天的联网额度用量之和 (只算日期是今天的)."""
    out = {"used": 0.0, "bonus": 0.0, "tomorrow_used": 0.0, "debt": 0.0, "gift": 0.0}
    for led in ledgers:
        n = led.get("net") or {}
        if n.get("date") != date:
            continue
        for k in out:
            try:
                out[k] += max(0.0, float(n.get(k, 0)))
            except (TypeError, ValueError):
                pass
    return out


def _make_handler(svc: SyncService):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code: int, obj):
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 8_000_000:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception:
                return {}

        def do_GET(self):
            if self.path.startswith("/oit/v1/file/"):
                return self._send_file(*svc.offer_get(self.path.rsplit("/", 1)[-1]))
            if self.path == "/oit/v1/hello":
                return self._send(200, svc.device_info())
            if self.path.startswith("/oit/v1/pair/"):
                return self._send(*svc._pair_status(self.path.rsplit("/", 1)[-1]))
            self._send(404, {"error": "not found"})

        def do_POST(self):
            body = self._body()
            if self.path == "/oit/v1/pair":
                return self._send(*svc._on_pair(body, self.client_address[0]))
            if self.path == "/oit/v1/sync":
                return self._send(*svc._on_sync(body))
            if self.path == "/oit/v1/unpair":
                return self._send(*svc._on_unpair(body))
            if self.path == "/oit/v1/wipe":
                return self._send(*svc._on_wipe(body))
            if self.path == "/oit/v1/offer":
                return self._send(*svc._on_offer(body))
            if self.path == "/oit/v1/want":
                return self._send(*svc._on_want(body))
            if self.path == "/oit/v1/send":
                return self._send(*svc._on_send(body))
            if self.path == "/oit/v1/got":
                return self._send(*svc._on_got(body))
            if self.path == "/oit/v1/recdel":
                return self._send(*svc._on_recdel(body))
            self._send(404, {"error": "not found"})

        def _send_file(self, path, enc=None):
            """enc: 文件通道的 (密钥, nonce), 内容加密后发; 安装包 (None) 原样发."""
            if not path:
                return self._send(404, {"error": "not found"})
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(os.path.getsize(path)))
            self.send_header("Connection", "close")
            self.end_headers()
            off = 0
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 16), b""):
                    self.wfile.write(file_xor(enc[0], enc[1], off, chunk) if enc else chunk)
                    off += len(chunk)
    return H


# ═══════════════════════ 自检 ═══════════════════════

class _FakeLocal:
    """测试用的本机数据: 一份设置, 一份整点记录, 一本账."""

    def __init__(self, name):
        self.name = name
        self.mode = "proxy"
        self.docs_ = {"settings": {"DAILY_LIMIT": {"value": 7200.0, "pending": None}},
                      "hours": {}, "rules": {"black_domains": []}, "timer": {"break_carry": 0}}
        self.used = 0.0
        self.study = {}
        self.requests = []
        self.cleared = 0

    def net_mode(self):
        return self.mode

    def ledger(self):
        return {"net": {"date": "2026-01-01", "used": self.used, "bonus": 0, "tomorrow_used": 0,
                        "debt": 0}, "study": dict(self.study), "focus": [], "usage": [], "window": {}}

    def docs(self):
        return json.loads(json.dumps(self.docs_))

    def apply(self, doc, changes):
        for k, v in changes.items():
            if v is None:
                self.docs_[doc].pop(k, None)
            else:
                self.docs_[doc][k] = v

    def clear_history(self):
        self.cleared += 1
        self.study = {}

    def auto_text(self):
        return "Sleeping"

    def status(self):
        return {"timer": "idle", "net": "offline"}

    def pair_request(self, req):
        self.requests.append(req)

    def changed(self):
        pass

    # 开发者模式 / 安装包 (可选的接口)
    dev = False
    got = None

    def dev_mode(self):
        return self.dev

    def set_dev_mode(self, on):
        self.dev = on

    def app_version(self):
        return "1.0.0"

    def update_wanted(self, platform, version):
        return "" if platform == "fake" else "platform"

    def update_dest(self, version):
        import tempfile
        return os.path.join(tempfile.mkdtemp(), f"pkg-{version}")

    def update_received(self, path, version, name):
        with open(path, "rb") as f:
            self.got = (f.read(), version, name)

    def update_failed(self, err):
        self.got = ("failed", err)

    wiped = None

    def wipe_requested(self, wipe_id, sender, name):
        self.wiped = (wipe_id, sender, name)


def _selftest():
    # 密码学: 双方算出同一把密钥和配对码; 篡改 / 重放 / 时钟差都拒绝
    xa, pa = dh_keypair()
    xb, pb = dh_keypair()
    ka, kb = dh_shared(xa, format(pb, "x")), dh_shared(xb, format(pa, "x"))
    assert ka == kb and len(pair_code(ka)) == 6
    env = seal(ka, "a", {"x": "你好", "n": [1, 2]})
    assert unseal(kb, env) == {"x": "你好", "n": [1, 2]}
    for bad in (dict(env, mac="0" * 64), dict(env, ct=base64.b64encode(b"zz").decode())):
        try:
            unseal(kb, bad)
            raise AssertionError("tampered message accepted")
        except SealError:
            pass
    try:
        unseal(kb, env)
        raise AssertionError("replay accepted")
    except SealError:
        pass
    try:
        unseal(kb, seal(ka, "a", {}, ts=_now_ms() - 3600_000))
        raise AssertionError("old message accepted")
    except SealError:
        pass
    # 固定输入的输出, Android 版的单元测试用同样的数字核对
    fixed = seal(bytes(range(32)), "dev", {"a": 1}, nonce=bytes(16), ts=1700000000000)
    assert fixed["ct"] == "pHEJdtrTcg==", fixed["ct"]
    assert fixed["mac"] == "a9ee146998cf5a913f1e5d4756f3b151789ed6e8e0b04b367bf1712d19ff8b24"
    assert pair_code(bytes(range(32))) == "338005"
    assert dh_shared(5, format(pow(2, 7, P), "x")).hex() ==         "0d2c6ab660f97893d7b57f47872f0ac4b773568d34c7596a216691d2ed4dcc93"
    assert pow(2, P - 1, P) == 1
    nets = _ipv4_nets()
    assert all(socket.inet_aton(a) and socket.inet_aton(m) for a, m in nets), nets

    # 两个实例在本机互相绑定 (A 是母端), 然后同步
    import tempfile
    runner = MainRunner()
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            runner.pump()
            time.sleep(0.02)
    threading.Thread(target=pump, daemon=True).start()
    la, lb = _FakeLocal("A"), _FakeLocal("B")
    la.docs_["settings"]["DAILY_LIMIT"]["value"] = 3600.0
    la.docs_["hours"]["2026-01-01/9"] = "学习"
    lb.docs_["hours"]["2026-01-01/10"] = "游戏"
    lb.study = {"2025-12-31": 3.0}
    sa = SyncService(tempfile.mkdtemp(), la, runner, name="A", http_port=17950, udp_port=17951)
    sb = SyncService(tempfile.mkdtemp(), lb, runner, name="B", http_port=17960, udp_port=17961)
    try:
        sa.start()
        sb.start()
        assert sa.hello("127.0.0.1", sb.port)["id"] == sb.my_id

        # GET 也必须带 Content-Length (旧版手机端没有它就读到连接关闭, 一直卡到超时)
        raw = socket.socket()
        raw.bind(("127.0.0.1", 0))
        raw.listen(1)
        head = {}

        def one():
            c, _ = raw.accept()
            buf = b""
            while b"\r\n\r\n" not in buf:
                buf += c.recv(4096)
            head["h"] = buf.lower()
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
            c.close()
        threading.Thread(target=one, daemon=True).start()
        SyncService._http("127.0.0.1", raw.getsockname()[1], "GET", "/oit/v1/hello", timeout=3)
        raw.close()
        assert b"content-length: 0" in head["h"], head

        # B 不是 Proxy 模式: 拒绝
        lb.mode = "adapter"
        sa.start_pairing("127.0.0.1", sb.port, mother_is_me=True)
        for _ in range(100):
            if sa.outgoing["state"] in ("error", "waiting"):
                break
            time.sleep(0.05)
        assert sa.outgoing["state"] == "error" and "not-proxy" in sa.outgoing["error"]
        lb.mode = "proxy"

        sa.start_pairing("127.0.0.1", sb.port, mother_is_me=True)
        for _ in range(100):
            if lb.requests and sa.outgoing["state"] == "waiting":
                break
            time.sleep(0.05)
        req = lb.requests[-1]
        assert req["code"] == sa.outgoing["code"], "两边显示同一个配对码"
        assert req["mother"] == sa.my_id
        runner.call(lambda: sb.answer_pairing(req["token"], True))
        for _ in range(200):
            if sa.outgoing["state"] == "accepted" and lb.cleared:
                break
            time.sleep(0.05)
        assert sa.outgoing["state"] == "accepted", sa.outgoing
        for _ in range(200):
            if lb.cleared and not sb.peers()[sa.my_id].get("init"):
                break
            time.sleep(0.05)
        # B 是子端: 用 A 的内容初始化 —— 自己的整点记录和历史被换掉
        assert lb.cleared == 1 and la.cleared == 0, (lb.cleared, la.cleared)
        assert "2026-01-01/10" not in la.docs_["hours"], "子端的旧数据不能传给母端"
        assert lb.docs_["settings"]["DAILY_LIMIT"]["value"] == 3600.0
        assert lb.docs_["hours"] == {"2026-01-01/9": "学习"}, lb.docs_["hours"]
        assert sa.my_id in sb.peers() and sb.my_id in sa.peers()
        # 绑定刚完成时的先后顺序不能变成"同步失败" (以前子端先同步过去会得到 401 unpaired)
        assert not sb.peers()[sa.my_id]["last_err"], sb.peers()[sa.my_id]
        assert not sa.peers()[sb.my_id]["last_err"], sa.peers()[sb.my_id]
        # 母端向还在初始化的子端同步: 不算出错
        assert sa._sync_with(sb.my_id) in (True, None)

        # 之后: 新的修改覆盖旧的, 账互相转交
        time.sleep(0.01)
        lb.docs_["hours"]["2026-01-01/11"] = "吃饭"
        lb.used = 600.0
        la.docs_["rules"]["black_domains"] = ["bilibili.com"]
        assert sb._sync_with(sa.my_id)
        assert la.docs_["hours"]["2026-01-01/11"] == "吃饭"
        assert lb.docs_["rules"]["black_domains"] == ["bilibili.com"]
        assert peer_net(sa.peer_ledgers(), "2026-01-01")["used"] == 600.0
        # 自动记录让位给自己填写的
        la.docs_["hours"]["2026-01-01/12"] = "写作业"
        lb.docs_["hours"]["2026-01-01/12"] = "Sleeping"
        assert sa._sync_with(sb.my_id)
        assert lb.docs_["hours"]["2026-01-01/12"] == "写作业"
        assert la.docs_["hours"]["2026-01-01/12"] == "写作业"
        # 删除也同步
        del la.docs_["hours"]["2026-01-01/9"]
        assert sa._sync_with(sb.my_id)
        assert "2026-01-01/9" not in lb.docs_["hours"]

        # 开发者模式跟着母端 (A); 子端 (B) 自己的状态不会传给母端
        la.dev = True
        assert sa._sync_with(sb.my_id) and lb.dev is True
        lb.dev = False
        assert sb._sync_with(sa.my_id) and la.dev is True and lb.dev is True
        la.dev = False
        assert sa._sync_with(sb.my_id) and lb.dev is False
        assert sa.peers()[sb.my_id].get("version") == "1.0.0", "同步时记下对方的版本"

        # 安装包直接发给 B: B 核对 SHA-256 后交给本机安装; 平台不对的不要
        pkg = os.path.join(tempfile.mkdtemp(), "OIT-9.9.9.bin")
        blob = os.urandom(300_000)
        with open(pkg, "wb") as f:
            f.write(blob)
        assert sa.offer_update(sb.my_id, pkg, "fake", "9.9.9") == "sent"
        for _ in range(200):
            if lb.got:
                break
            time.sleep(0.05)
        assert lb.got and lb.got[0] == blob and lb.got[1] == "9.9.9" and lb.got[2] == "A", lb.got
        assert sa.offer_update(sb.my_id, pkg, "android", "9.9.9") == "platform"
        lb.got = None
        with sa.lock:                                   # 只有发出去的取件码能取到文件
            tok = next(iter(sa.offers))
        assert sa.offer_file(tok) == pkg and sa.offer_file("0" * 32) is None
        # 请对方下载新版本发过来 (want): 对方的 local 决定肯不肯
        la.wanted = []
        la.update_requested = lambda pid: la.wanted.append(pid) or ""
        assert sb.request_update(sa.my_id) == "asked" and la.wanted == [sb.my_id]
        la.update_requested = lambda pid: "busy"
        assert sb.request_update(sa.my_id) == "busy"
        del la.update_requested
        assert sb.request_update(sa.my_id) == "unsupported"

        # 文件通道 (15.6): B 同意后来取, 内容加密传, 核对 SHA-256; 取完告诉 A
        offered, delivered = [], []
        lb.file_offered = lambda pid, who, o: offered.append((pid, who, o)) or ""
        la.file_delivered = lambda who, name: delivered.append((who, name))
        src = os.path.join(tempfile.mkdtemp(), "照片 1.png")
        blob2 = os.urandom(150_000)
        with open(src, "wb") as f:
            f.write(blob2)
        assert sa.send_file(sb.my_id, src) == "sent"
        pid, who, o = offered[0]
        assert pid == sa.my_id and who == "A" and o["name"] == "照片 1.png" and o["size"] == len(blob2)
        pa = sb.peers()[sa.my_id]
        c = http.client.HTTPConnection(pa["addr"], int(pa["port"]), timeout=5)
        c.request("GET", f"/oit/v1/file/{o['token']}")
        raw = c.getresponse().read()
        c.close()
        assert len(raw) == len(blob2) and raw != blob2, "传的内容是加密的"
        dest = os.path.join(tempfile.mkdtemp(), "x.png")
        sb.fetch_file(pid, o, dest)
        with open(dest, "rb") as f:
            assert f.read() == blob2
        assert delivered == [("B", "照片 1.png")] and sa.offer_get(o["token"]) == (None, None), "取完就不能再取"
        bad = dict(offered[0][2], sha256="0" * 64)
        try:
            sb.fetch_file(pid, bad, dest + "2")
            raise AssertionError("取不到 / 核对不上要报错")
        except (RuntimeError, ValueError):
            assert not os.path.exists(dest + "2") and not os.path.exists(dest + "2.part")
        del lb.file_offered
        assert sa.send_file(sb.my_id, src) == "unsupported"

        # 刻痕的录屏片段 (18.8): 带 cid 发送, 走 rec_offered (主人自动收下, 不走 file_offered)
        recs = []
        lb.rec_offered = lambda pid, who, o: recs.append((pid, who, o)) or ""
        assert sa.send_file(sb.my_id, src, cid="c123") == "sent"
        pid2, who2, o2 = recs[0]
        assert pid2 == sa.my_id and o2.get("cid") == "c123" and o2["name"] == "照片 1.png"
        rd = os.path.join(tempfile.mkdtemp(), "seg.oitv")
        sb.fetch_file(pid2, o2, rd)
        with open(rd, "rb") as f:
            assert f.read() == blob2
        del lb.rec_offered
        assert sa.send_file(sb.my_id, src, cid="c123") == "unsupported"

        # 本机算作离线 (15.7, 电脑切到了 OIT 不在的虚拟桌面): 不接受同步, 对方同步失败
        sa.away = lambda: True
        assert sb._sync_with(sa.my_id) is False, "对方同步失败, 把本机算作离线"
        sa.away = lambda: False
        assert sb._sync_with(sa.my_id) is True
        assert safe_name("..\\x\\a/b:c?.png") == "b_c_.png" and safe_name("C:\\x\\y.txt") == "y.txt"
        assert safe_name(" . ") == "file"

        # 关闭前推送 (push_final): 不等回复里的内容, 对方照样并入
        la.docs_["timer"]["live"] = {"state": "idle", "owner": sa.my_id}
        assert sb.synced_since(0) and not sb.synced_since(_now_ms() + 60_000)
        sa.push_final(timeout=3.0)
        for _ in range(100):
            if lb.docs_["timer"].get("live") == {"state": "idle", "owner": sa.my_id}:
                break
            time.sleep(0.05)
        assert lb.docs_["timer"].get("live") == {"state": "idle", "owner": sa.my_id}, lb.docs_["timer"]

        # 解除绑定: 对方也解除
        runner.call(lambda: sa.unpair(sb.my_id))
        for _ in range(100):
            if not sb.peers():
                break
            time.sleep(0.05)
        assert not sa.peers() and not sb.peers()

        # 反过来: B 发起, 选 A 为母端 —— 发起方是子端, 拿到"已接受"后马上初始化
        lb.cleared = 0
        sb.start_pairing("127.0.0.1", sa.port, mother_is_me=False)
        for _ in range(100):
            if la.requests and sb.outgoing["state"] == "waiting":
                break
            time.sleep(0.05)
        req = la.requests[-1]
        assert req["mother"] == sa.my_id and req["code"] == sb.outgoing["code"]
        runner.call(lambda: sa.answer_pairing(req["token"], True))
        for _ in range(200):
            if sb.outgoing["state"] == "accepted" and not sb.peers().get(sa.my_id, {}).get("init", True):
                break
            time.sleep(0.05)
        assert sb.outgoing["state"] == "accepted" and lb.cleared == 1, (sb.outgoing, lb.cleared)
        assert not sb.peers()[sa.my_id]["last_err"] and not sa.peers()[sb.my_id]["last_err"]
        assert sa.peers()[sb.my_id]["last_ok"], "子端初始化时母端也记下了这次同步"
        # 手动解除绑定: 解除后再送来的对方的旧状态 / 旧账不收, 也不会成为解除不掉的"离线"设备
        sx = SyncService(tempfile.mkdtemp(), _FakeLocal("X"), runner, name="X", http_port=17980, udp_port=17981)
        sx.state["peers"]["zz"] = {"name": "Z", "platform": "android", "addr": "1.2.3.4", "port": 1,
                                   "key": "00", "mother": sx.my_id}
        sx.state["status"]["zz"] = {"ts": 10, "timer": "idle"}
        sx.state["peers"]["yy"] = dict(sx.state["peers"]["zz"], name="Y")
        sx.state["unbind_at"] = _now_ms() - 1
        assert sx.check_unbind() and "yy" not in sx.peers() and sx.unbind_at is None, "到时间解除全部绑定"
        sx.unpair("zz", notify=False)
        sx.merge({"status": {"zz": {"ts": 20, "timer": "idle"}}, "ledgers": {"zz": {"rev": 20, "name": "Z"}}})
        assert "zz" not in sx.state["status"] and "zz" not in sx.state["ledgers"], sx.state
        assert not [d for d in sx.device_states() if d["id"] == "zz"]
        # 旧版本留下的残留: 没有绑定设备时全部清掉
        sx.state["status"]["old"] = {"ts": _now_ms(), "timer": "idle"}
        sx.state["ledgers"]["old"] = {"rev": 1, "name": "Old"}
        assert sx.device_states() == [] and "old" not in sx.state["status"] and "old" in sx.state["gone"]

        # 清空请求 (新的重置): A 清空数据, 绑定不动; 同一个请求只处理一次
        assert sa._sync_with(sb.my_id)
        wid = SyncService.new_wipe_id()
        sb.send_wipe(wid, timeout=3.0)
        assert la.wiped == (wid, sb.my_id, "B") and sb.my_id in sa.peers() and not sb.state["wipe_out"]
        la.wiped = None
        assert send_wipe_to(sb.my_id, sb.peers()[sa.my_id], wid, False) == 200 and la.wiped is None, "只处理一次"
        kept = sb.kept_state("A")
        assert kept["peers"] and not kept["ledgers"] and not kept["track"] and kept["wiped_by"] == "A"
        # 连不上: 记下来; 送到之前对方的同步不收 (wipe-pending), 下一轮先送清空请求
        real = sb.state["peers"][sa.my_id]["port"]
        sb.state["peers"][sa.my_id]["port"] = 1
        wid2 = SyncService.new_wipe_id()
        sb.send_wipe(wid2, timeout=1.0)
        assert sb.state["wipe_out"] == {sa.my_id: wid2} and la.wiped is None
        sb.state["peers"][sa.my_id]["port"] = real
        assert sa._sync_with(sb.my_id) is None, "对方要先送清空请求: 不算同步失败"
        assert sb._sync_with(sa.my_id) is None and la.wiped == (wid2, sb.my_id, "B") and not sb.state["wipe_out"]
        # 卸载: 清空并解除和它的绑定
        la.wiped = None
        assert send_wipe_to(sb.my_id, sb.peers()[sa.my_id], SyncService.new_wipe_id(), True) == 200
        assert la.wiped and sb.my_id not in sa.peers() and sb.my_id in sa.state["gone"]
        sa.state["gone"].pop(sb.my_id)
        sa.state["peers"][sb.my_id] = dict(sb.peers()[sa.my_id], name="B", addr="127.0.0.1", port=sb.port,
                                           mother=sa.my_id)
        # 解除全部绑定: 24 小时后才解除, 期间可以取消
        sb.request_unbind()
        assert sb.unbind_at and not sb.check_unbind() and sb.peers()
        sb.cancel_unbind()
        assert sb.unbind_at is None

        # B 重置: 让 A 解除绑定并删掉 A 保留的 B 的数据, A 再转告别的设备 (gone)
        assert sa._sync_with(sb.my_id)
        assert sb.my_id in sa.state["ledgers"]
        sb.forget_me(timeout=3.0)
        for _ in range(100):
            if sb.my_id not in sa.peers():
                break
            time.sleep(0.05)
        assert sb.my_id not in sa.peers() and sb.my_id not in sa.state["ledgers"]
        assert sb.my_id not in sa.state["status"] and sb.my_id in sa.state["gone"]
        assert sa.snapshot()["gone"].get(sb.my_id)
        # 第三台设备 C 收到 A 转告的 gone: 删掉 B 的旧账, 之后也不再收旧账
        lc = _FakeLocal("C")
        sc = SyncService(tempfile.mkdtemp(), lc, runner, name="C", http_port=17970, udp_port=17971)
        sc.merge({"ledgers": {sb.my_id: {"rev": 5, "name": "B"}}})
        assert sb.my_id in sc.state["ledgers"]
        sc.merge({"gone": {sb.my_id: _now_ms()}})
        assert sb.my_id not in sc.state["ledgers"]
        sc.merge({"ledgers": {sb.my_id: {"rev": 5, "name": "B"}}})
        assert sb.my_id not in sc.state["ledgers"], "重置前的旧账不能再传回来"
        sc.merge({"ledgers": {sb.my_id: {"rev": _now_ms() + 10, "name": "B"}}})
        assert sb.my_id in sc.state["ledgers"], "重置之后的新账照常收"

        # 离线的时间段: 从最后一次有消息算起, 到重新有消息为止
        t0 = _now_ms()
        # dX 是绑定的设备 pY 转交过来的 (本机一台绑定的设备都没有时, 转交过来的不可能还在, 会被清掉)
        sc.state["peers"]["pY"] = {"name": "Y", "platform": "windows", "addr": "127.0.0.1", "port": 1,
                                   "key": "00", "mother": "pY", "last_ok": t0, "last_err": ""}
        sc.state["status"]["dX"] = {"ts": t0 - 10 * 60_000, "timer": "running"}
        sc.state["ledgers"]["dX"] = {"rev": 1, "name": "X"}
        states = {d["id"]: d for d in sc.device_states()}
        assert not states["dX"]["online"] and states["dX"]["name"] == "X"
        # 版本不一样: 版本低的算离线 (本机 1.0.0); 两边都知道版本不一样
        assert states["pY"]["online"] and sc.version_mismatch() == []
        sc.state["peers"]["pY"]["version"] = "0.9.9"
        st = {d["id"]: d for d in sc.device_states()}["pY"]
        assert not st["online"] and st["outdated"] and sc.version_mismatch() == [("Y", "0.9.9", False)]
        sc.state["peers"]["pY"]["version"] = "1.0.1"
        st = {d["id"]: d for d in sc.device_states()}["pY"]
        assert st["online"] and not st["outdated"] and sc.version_mismatch() == [("Y", "1.0.1", True)]
        sc.state["peers"]["pY"].pop("version")
        sc.track_offline()
        assert sc.offline_spans(t0 - 20 * 60_000, t0 - 5 * 60_000) == [(t0 - 10 * 60_000, t0 - 5 * 60_000)]
        sc.state["status"]["dX"]["ts"] = t0 - 1000          # 又有消息了
        sc.track_offline()
        assert {d["id"]: d for d in sc.device_states()}["dX"]["online"]
        spans = sc.offline_spans(t0 - 20 * 60_000, t0 + 60_000)
        assert spans == [(t0 - 10 * 60_000, t0 - 1000)], spans
        assert sc.offline_spans(t0, t0 + 60_000) == [], "重新上线之后不算离线"
        # 关机 (MECHANICS 15.4): 最后的状态是收到关机指令 / 电量低于 2% 时突然离线 → 关机, 效果和在线一样
        sc.state["peers"]["pY"]["last_err"] = "timeout"
        sc.state["status"]["pY"] = {"ts": t0 - 60_000, "timer": "running", "battery": 1, "auto": False}
        st = {d["id"]: d for d in sc.device_states()}["pY"]
        assert not st["online"] and not st["off"], "没开开机自启动: 照旧算离线"
        sc.state["status"]["pY"]["auto"] = True
        st = {d["id"]: d for d in sc.device_states()}["pY"]
        assert st["online"] and st["off"], st
        sc.state["status"]["pY"]["battery"] = 2
        st = {d["id"]: d for d in sc.device_states()}["pY"]
        assert not st["online"] and not st["off"], "电量 2% 不算"
        sc.state["status"]["pY"]["off"] = True
        assert {d["id"]: d for d in sc.device_states()}["pY"]["off"]
        sc.state["peers"]["pY"]["version"] = "0.9.9"
        st = {d["id"]: d for d in sc.device_states()}["pY"]
        assert not st["online"] and not st["off"], "版本低的照样算离线"
        sc.state["peers"]["pY"].pop("version")
        sc.state["peers"]["pY"]["last_err"] = ""
        sc.state["status"]["pY"] = {"ts": t0, "timer": "idle"}
        # 本机关机的那段 (15.4) 也算进离线的时间段: 本机之后记下的专注里这段记为空闲
        sc.add_self_off(t0 - 50 * 60_000, t0 - 40 * 60_000)
        assert (t0 - 50 * 60_000, t0 - 40 * 60_000) in sc.offline_spans(t0 - 60 * 60_000, t0 - 30 * 60_000)
        sc.state["self_off"] = []
        # 断网 (15.5): 最后的状态写着 adapter → 断网, 效果和在线一样; 回来时记下断网的那一段
        sc.state["peers"]["pY"]["last_err"] = "timeout"
        sc.state["status"]["pY"] = {"ts": t0 - 60_000, "timer": "running", "adapter": t0 - 3_600_000}
        st = {d["id"]: d for d in sc.device_states()}["pY"]
        assert st["online"] and st["net_off"] and not st["off"], st
        sc.merge({"status": {"pY": {"ts": t0 + 1, "timer": "idle"}}})
        back = sc.pop_adapter_back()
        assert [(d, a) for d, a, _b in back] == [("pY", t0 - 3_600_000)] and sc.pop_adapter_back() == []
        sc.state["peers"]["pY"]["last_err"] = ""
        sc.state["status"]["pY"] = {"ts": t0, "timer": "idle"}
        # 本机断网期间: 其他设备都算在线; 换回 Proxy 后记下结束的时刻
        sc.state["adapter_since"] = t0 - 1000
        sc.state["peers"]["pY"]["last_err"] = "unreachable"
        assert {d["id"]: d for d in sc.device_states()}["pY"]["online"] and sc.adapter_since() == t0 - 1000
        assert sc.adapter_window() is None
        sc.note_proxy_back()
        w = sc.adapter_window()
        assert w and w[0] == t0 - 1000 and sc.adapter_since() == 0
        sc.clear_adapter()
        assert sc.adapter_window() is None
        sc.state["peers"]["pY"]["last_err"] = ""
        assert went_off({"off": True, "auto": True}) and went_off({"battery": 0, "auto": True})
        assert not went_off({"battery": None, "auto": True}) and not went_off({"off": True})
        assert not went_off({}) and not went_off(None)
        # 转交过来的设备一天没有消息: 不再显示
        sc.state["status"]["dX"]["ts"] = t0 - STALE_FWD_MS - 1000
        assert "dX" not in {d["id"] for d in sc.device_states()} and "dX" in sc.state["gone"]
        sc.state["peers"].pop("pY")
        runner.call(lambda: sb.unpair(sa.my_id, notify=False))
        # 请求体读不出来 (例如太大): 回 400, 不再冒充"没绑定"
        assert sa._on_sync({})[0] == 400
    finally:
        stop.set()
        sa.stop()
        sb.stop()
    return "devsync selftest OK"


if __name__ == "__main__":
    print(_selftest())
