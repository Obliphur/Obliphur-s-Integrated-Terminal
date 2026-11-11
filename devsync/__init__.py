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
    docs     共享文档: 设置, 整点记录, 计时器 (此刻的专注 / 休息, 积攒的休息时间), 成就, 合约, 行为模式的外出, 指令 (领到的指令和奖励).
              每一项单独记修改时间, 新的覆盖旧的. 规则页的名单和过滤模式不同步
    ledgers  各设备自己的账: 今天的联网额度用量 / 借贷, 学习时长, 专注时间段, 联网理由, 窗口时间.
             互相转交, 每台设备显示的是所有设备的合计
    status   各设备此刻的状态 (专注 / 休息 / 联网) 和最后一次操作的时间 (整点提示音只在最后操作的设备上响)

同步由母端发起: 每 20 秒向每台子端发一次请求, 双方合并后回复; 子端只在第一次初始化和按"立即同步"时主动发.

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
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import securefile
from .devsync_crypto import (
    P, dh_keypair, dh_shared, pair_code, accept_proof, file_key, file_xor, seal,
    SealError, unseal
)

from .devsync_util import *
from .devsync_discover import DiscoverMixin
from .devsync_pair import PairMixin
from .devsync_wipe import WipeMixin
from .devsync_link import LinkMixin
from .devsync_docs import DocSyncMixin
from .devsync_transfer import TransferMixin
from .devsync_status import StatusMixin




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



class SyncService(DiscoverMixin, PairMixin, WipeMixin, LinkMixin, DocSyncMixin, TransferMixin, StatusMixin):

    # 模块级发送函数的薄包装: 自检会改 devsync.send_restart_to, 所以每次调用都重新取 (不能 from ... import 绑死)
    def _send_wipe_to(self, peer, wipe_id, unpair=False, timeout=3.0):
        return send_wipe_to(self.my_id, peer, wipe_id, unpair=unpair, timeout=timeout)

    def _send_unpair_to(self, peer, timeout=3.0):
        return send_unpair_to(self.my_id, peer, timeout=timeout)

    def _send_restart_to(self, peer, timeout=3.0):
        return send_restart_to(self.my_id, peer, timeout=timeout)

    def _send_forget(self, peer, timeout=3.0):
        return send_forget(self.my_id, peer, timeout=timeout)


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
        self._last_discover = 0.0
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
            with securefile.open(self.file, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                for k in ("peers", "track", "ledgers", "status", "gone", "wipe_out", "unbinds", "evict_out"):
                    if not isinstance(d.get(k), dict):
                        d[k] = {}
                if not isinstance(d.get("wipes_seen"), list):
                    d["wipes_seen"] = []
                d.pop("unbind_at", None)              # 旧版本 Reset 页的"解除所有绑定"计划: 已经去掉了
                return d
        except Exception:
            pass
        return {"device": None, "peers": {}, "track": {}, "ledgers": {}, "status": {}, "gone": {},
                "wipe_out": {}, "wipes_seen": [], "unbinds": {}, "evict_out": {}}

    def _save(self):
        if self.dead:
            return
        with self.lock:
            tmp = self.file + ".tmp"
            try:
                os.makedirs(os.path.dirname(self.file) or ".", exist_ok=True)
                with securefile.open(tmp, "w", encoding="utf-8") as f:
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

    def peer_flag(self, flag: str, fresh_ms: int = None) -> bool:
        """其他设备里有没有哪台最近一次的状态里 flag 是真的 (例如 "prescript": 停在指令页上); 太久没消息的不算."""
        fresh = FRESH_MS if fresh_ms is None else fresh_ms
        now = _now_ms()
        with self.lock:
            for did, st in self.state["status"].items():
                if did == self.my_id or did in self.state["gone"] or not isinstance(st, dict):
                    continue
                if st.get(flag) and now - int(st.get("ts", 0)) <= fresh:
                    return True
        return False

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

    # ---- 发现 ----

    # ---- HTTP 客户端 ----

    @staticmethod
    def _http(addr: str, port: int, method: str, path: str, body=None, timeout: float = 6.0, read_timeout: float = None):
        """timeout: 连接和读取各等多久; read_timeout: 连上之后等回复另算 (不给就和 timeout 一样)."""
        conn = http.client.HTTPConnection(addr, int(port), timeout=timeout)
        try:
            if read_timeout is not None:
                conn.connect()
                conn.sock.settimeout(read_timeout)
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

    # ---- 绑定: 发起方 ----

    # ---- 绑定: 被请求方 ----

    # ---- 解除绑定 ----

    # ---- 清空数据 (重置 / 卸载, MECHANICS 9, 15.1) ----

    # ---- 设备离线时请它重新启动 OIT (MECHANICS 15.3) ----

    # ---- 解除绑定 (MECHANICS 15.1): UNBIND_FREE 时立即解除, 本机数据不动; 否则冷静期 120 小时 (期间可以取消), 到时间后解除并清空这一台 (本机) 上的全部数据, 不影响对方 ----

    # ---- 文档 (每一项单独记修改时间) ----

    # ---- 同步 ----

    # ---- 安装包直接发给其他设备 (MECHANICS 16) ----

    # ---- 文件通道 (MECHANICS 15.6): 把文件 / 图片发给一台绑定的设备 ----

    # ---- 给本机其他部分用: 其他设备的账 ----

    # ---- Adapter 断网 (MECHANICS 15.5) ----








def send_wipe_to(my_id: str, peer: dict, wipe_id: str, unpair: bool, timeout: float = 3.0, leave: bool = False) -> int:
    """请一台绑定的设备清空 OIT 的数据 (unpair: 同时解除和本机的绑定). 返回 HTTP 状态 (连不上时 0;
    404 = 对方是不认识清空请求的旧版本). uninstall.py 也用它.
    leave: 本机解除了和它的绑定 (15.1): 只重置它自己 (清空数据), 不再转告其他设备."""
    try:
        env = seal(bytes.fromhex(peer["key"]), my_id, {"wipe": wipe_id, "unpair": bool(unpair), "leave": bool(leave)})
        st, _ = SyncService._http(peer["addr"], peer["port"], "POST", "/oit/v1/wipe", env, timeout=timeout)
        return st
    except Exception:
        return 0


def send_unpair_to(my_id: str, peer: dict, timeout: float = 3.0) -> int:
    """告诉一台设备: 本机解除了和它的绑定 (它只删掉这条绑定和本机留在它那里的状态, 数据不动). 返回 HTTP 状态 (连不上时 0)."""
    try:
        env = seal(bytes.fromhex(peer["key"]), my_id, {"unpair": True})
        st, _ = SyncService._http(peer["addr"], peer["port"], "POST", "/oit/v1/unpair", env, timeout=timeout)
        return st
    except Exception:
        return 0


def send_restart_to(my_id: str, peer: dict, timeout: float = 3.0) -> int:
    """请一台绑定的设备重新启动 OIT. 返回 HTTP 状态 (连不上时 0; 404 = 对方是不认识这个请求的旧版本)."""
    try:
        env = seal(bytes.fromhex(peer["key"]), my_id, {"restart": True})
        st, _ = SyncService._http(peer["addr"], peer["port"], "POST", "/oit/v1/restart", env, timeout=timeout)
        return st
    except Exception:
        return 0



def send_forget(my_id: str, peer: dict, timeout: float = 3.0) -> bool:
    """告诉一台绑定的设备: 本机要重置 / 卸载了, 解除绑定并删掉本机的数据. uninstall.py 也用它."""
    try:
        env = seal(bytes.fromhex(peer["key"]), my_id, {"unpair": True, "forget": True})
        st, _ = SyncService._http(peer["addr"], peer["port"], "POST", "/oit/v1/unpair", env, timeout=timeout)
        return st == 200
    except Exception:
        return False






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
    restarted = None

    def wipe_requested(self, wipe_id, sender, name, leave=False):
        self.wiped = (wipe_id, sender, name) + (("leave",) if leave else ())

    def restart_requested(self, sender, name):
        self.restarted = (sender, name)


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
        # 还停在昨天的设备: 昨天借的变成今天的欠款, 其余不算
        led = [{"net": {"date": "2026-01-01", "used": 600.0, "bonus": 1200.0, "tomorrow_used": 2400.0}}]
        assert peer_net(led, "2026-01-02") == {"used": 0.0, "bonus": 0.0, "tomorrow_used": 0.0, "debt": 2400.0, "gift": 0.0}
        assert peer_net(led, "2026-01-03")["debt"] == 0.0 and peer_net(led, "2026-01-01")["debt"] == 0.0
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

        # 设备离线时请它重新启动 OIT (MECHANICS 15.3): 请求本身 (本机在别的虚拟桌面不理, 刚重启过不再理)
        pa = sb.peers()[sa.my_id]
        sa.away = lambda: True
        assert send_restart_to(sb.my_id, pa) == 503 and la.restarted is None
        sa.away = lambda: False
        sa._started_ms -= 3 * RESTART_FRESH_MS                  # 本机刚打开的不理 / 刚同步成功过的不理
        sa.state["peers"][sb.my_id]["last_ok"] = _now_ms()
        assert send_restart_to(sb.my_id, pa) == 200 and la.restarted is None, "刚同步成功过: 本机明明在线"
        sa.state["peers"][sb.my_id]["last_ok"] = _now_ms() - 10 * RESTART_HEALTHY_MS
        sa._started_ms += 3 * RESTART_FRESH_MS
        assert send_restart_to(sb.my_id, pa) == 200 and la.restarted is None, "刚打开的不理"
        sa._started_ms -= 3 * RESTART_FRESH_MS
        assert send_restart_to(sb.my_id, pa) == 200 and la.restarted == (sb.my_id, sb.state["device"]["name"]), la.restarted
        la.restarted = None
        assert send_restart_to(sb.my_id, pa) == 200 and la.restarted is None, "刚重启过: 不再理会"
        assert send_restart_to(sb.my_id, dict(pa, key="11" * 32)) == 403 and la.restarted is None
        # 发出请求: 离线马上发, 连不上每 RESTART_RETRY 秒再试, 有回应就不再发, 回来之后再离线再发; 外出 / 刚打开还没同步过的不发
        sent = []
        real_send = globals()["send_restart_to"]
        res = [0]
        globals()["send_restart_to"] = lambda me, p, timeout=3.0: (sent.append(p["port"]), res[0])[1]

        def settle():
            for th in threading.enumerate():
                if th is not threading.current_thread() and th.daemon and th.name.startswith("Thread-"):
                    th.join(0.5)
        try:
            sb._started_ms -= 10 * OFFLINE_MS
            sb.state["peers"][sa.my_id]["last_ok"] = 0
            sb.state["status"].pop(sa.my_id, None)
            assert not {d["id"]: d for d in sb.device_states()}[sa.my_id]["online"]
            sb.check_restart(hold=True)
            settle()
            assert sent == [], "外出期间的设备不算离线"
            sb.check_restart()
            settle()
            assert sent == [pa["port"]], "离线马上发"
            sb.check_restart()
            settle()
            assert len(sent) == 1, "RESTART_RETRY 之内不重发"
            sb.__dict__["_restart_try"][sa.my_id]["at"] -= RESTART_RETRY + 1
            sb.check_restart()
            settle()
            assert len(sent) == 2, "连不上: 隔一会儿再试"
            res[0] = 200
            sb.__dict__["_restart_try"][sa.my_id]["at"] -= RESTART_RETRY + 1
            sb.check_restart()
            settle()
            sb.__dict__["_restart_try"][sa.my_id]["at"] -= RESTART_RETRY + 1
            sb.check_restart()
            settle()
            assert len(sent) == 3, "有回应就不再发"
            sb.state["peers"][sa.my_id]["last_ok"] = _now_ms()
            sb.check_restart()
            assert sa.my_id not in sb.__dict__["_restart_try"], "回来了: 清掉"
            sb.state["peers"][sa.my_id]["last_ok"] = 0
            sb.state["status"].pop(sa.my_id, None)
            sb.check_restart()
            settle()
            assert len(sent) == 4, "再次离线再发"
            sb.state["peers"][sa.my_id]["last_ok"] = _now_ms()
            sb.check_restart()
        finally:
            globals()["send_restart_to"] = real_send
            sb.state["peers"][sa.my_id]["last_ok"] = _now_ms()
        sb._restart_try = {}
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
        sx.state["unbinds"]["yy"] = _now_ms() - 1
        assert sx.check_unbind() == ["Y"] and "yy" not in sx.peers() and sx.unbind_at("yy") is None, "冷静期到了解除"
        assert "yy" in sx.state["evict_out"] and "zz" in sx.peers(), "连不上: 清空请求留着, 之后再送"
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
        # 解除绑定: 1.0.435 起 UNBIND_FREE=True, 马上解除, 通知对方 (它的数据不动), 本机由调用方重置 (实际不重置)
        keep_a = dict(sb.peers()[sa.my_id])
        assert not sb.request_unbind(sa.my_id)
        if UNBIND_FREE:
            assert sb.check_unbind() == ["A"] and sa.my_id not in sb.peers()
            la.wiped = None
            for _ in range(100):
                if sb.my_id not in sa.peers() and not sb.state["evict_out"]:
                    break
                time.sleep(0.05)
            assert la.wiped is None, "对方的数据不清空"
            assert sb.my_id not in sa.peers() and sb.my_id in sa.state["gone"], "对方只是少了这条绑定"
            assert not sb.state["evict_out"], "送到了就不再送"
        else:
            assert sb.unbind_at(sa.my_id) and sb.check_unbind() == [] and sa.my_id in sb.peers()
            sb.cancel_unbind(sa.my_id)
            assert sb.unbind_at(sa.my_id) is None
            sb.request_unbind(sa.my_id)
            sb.state["unbinds"][sa.my_id] = _now_ms() - 1
            la.wiped = None
            assert sb.check_unbind() == ["A"] and sa.my_id not in sb.peers()
            for _ in range(100):
                if sb.my_id not in sa.peers() and not sb.state["evict_out"]:
                    break
                time.sleep(0.05)
            assert la.wiped is None, "对方的数据不清空"
            assert sb.my_id not in sa.peers() and sb.my_id in sa.state["gone"], "对方只是少了这条绑定"
            assert not sb.state["evict_out"], "送到了就不再送"
        assert UNBIND_WAIT_MS == 120 * 3600 * 1000
        kept = sb.kept_state("", unbound="A")
        assert kept["unbound_from"] == "A" and not kept["ledgers"] and "wiped_by" not in kept
        sb.state["unbound_from"] = "A"
        assert sb.pop_unbound_from() == "A" and sb.pop_unbound_from() == ""
        kept = sa.kept_state("B", leave=True)
        assert kept["wiped_leave"] and not kept["ledgers"]
        sa.state["gone"].pop(sb.my_id)
        sa.state["peers"][sb.my_id] = dict(keep_a, name="B", addr="127.0.0.1",
                                           port=sb.port, mother=sa.my_id)
        sb.state["gone"].pop(sa.my_id)
        sb.state["peers"][sa.my_id] = keep_a

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

        # 对方被卸载了 (MECHANICS 9.2): 同步端口一直拒绝连接满 24 小时, 联动程序的端口也没人听 → 解除绑定, 返回名字 (调用方清空本机)
        err_refused = "[WinError 10061] 由于目标计算机积极拒绝，无法连接。"
        sc.state["peers"]["pG"] = {"name": "G", "platform": "android", "addr": "127.0.0.1", "port": 1, "key": "00",
                                   "last_ok": 0, "last_err": ""}
        q = sc.state["peers"]["pG"]
        t = _now_ms()
        SyncService._note_refused(q, err_refused, t - GONE_REFUSED_MS - 60_000)
        for k in range(0, GONE_REFUSED_MS // 60_000 + 2, 10):                      # 每 10 分钟拒绝一次, 连续 24 小时多一点
            SyncService._note_refused(q, err_refused, t - GONE_REFUSED_MS - 60_000 + k * 60_000)
        assert q["refused_last"] - q["refused_since"] >= GONE_REFUSED_MS
        sc._gone_try = {}
        assert sc.check_gone(lambda a: "open") == [] and "refused_since" not in q, "联动程序还在: 不算, 重新算"
        SyncService._note_refused(q, err_refused, t - GONE_REFUSED_MS - 60_000)
        SyncService._note_refused(q, err_refused, t)
        assert q["refused_since"] == t and q["refused_last"] == t, "隔了 24 小时才又拒绝一次 (中间没有记录): 重新算"
        q["refused_since"], q["refused_last"] = t - GONE_REFUSED_MS - 60_000, t
        sc._gone_try = {}
        assert sc.check_gone(lambda a: "unknown") == [] and "pG" in sc.peers(), "探测不了 (超时): 这一轮不算"
        sc._gone_try = {}
        assert sc.check_gone(lambda a: "refused") == ["G"] and "pG" not in sc.peers(), "都拒绝连接满 24 小时: 解除绑定"
        # 外出 / 关机 / 断网期间不算, 记录清掉重新算
        sc.state["peers"]["pG"] = {"name": "G", "platform": "android", "addr": "127.0.0.1", "port": 1, "key": "00", "last_ok": 0,
                                   "last_err": err_refused, "refused_since": t - GONE_REFUSED_MS - 60_000, "refused_last": t}
        sc._gone_try = {}
        assert sc.check_gone(lambda a: "refused", hold=True) == [] and "pG" in sc.peers() and "refused_since" not in sc.state["peers"]["pG"], "本机看到外出: 不算, 重新算"
        sc.state["peers"]["pG"].update(refused_since=t - GONE_REFUSED_MS - 60_000, refused_last=t)
        sc.state["status"]["pG"] = {"ts": t - 10 * 60_000, "off": True, "auto": True}            # 最后的状态是收到关机指令
        sc._gone_try = {}
        assert sc.check_gone(lambda a: "refused") == [] and "pG" in sc.peers() and "refused_since" not in sc.state["peers"]["pG"], "对方关机: 不算, 重新算"
        sc.state["status"]["pG"] = {"ts": t - 10 * 60_000, "adapter": True}                        # 对方换成了 Adapter 断网
        sc.state["peers"]["pG"].update(refused_since=t - GONE_REFUSED_MS - 60_000, refused_last=t)
        sc._gone_try = {}
        assert sc.check_gone(lambda a: "refused") == [] and "pG" in sc.peers(), "对方断网: 不算"
        sc.state["status"].pop("pG")
        sc.state["peers"].pop("pG")
        # 不满 24 小时 / 中间有别的错误 / 成功过: 不算
        sc.state["peers"]["pG"] = {"name": "G", "platform": "android", "addr": "127.0.0.1", "port": 1, "key": "00", "last_ok": 0, "last_err": ""}
        q = sc.state["peers"]["pG"]
        q["refused_since"], q["refused_last"] = t - 3600_000, t
        sc._gone_try = {}
        assert sc.check_gone(lambda a: "refused") == [], "才 1 小时"
        SyncService._note_refused(q, "timed out", t)
        assert "refused_since" not in q, "超时不是拒绝: 重新算"
        sc.state["peers"].pop("pG")
        # 重置后留下的绑定里没有这些记录
        sc.state["peers"]["pH"] = {"name": "H", "platform": "android", "addr": "127.0.0.1", "port": 1, "key": "00", "last_ok": 0,
                                   "last_err": err_refused, "refused_since": t - 5, "refused_last": t}
        assert "refused_since" not in sc.kept_state()["peers"]["pH"]
        sc.state["peers"].pop("pH")

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
        # 同步偶然失败 (超时 / 对方卡了几秒): 还在 OFFLINE_MS 之内有它的消息就不算离线; 明确的失败 (away …) 马上算
        sc.state["peers"]["pY"]["last_err"] = "timed out"
        assert {d["id"]: d for d in sc.device_states()}["pY"]["online"], "偶然一次超时不算离线"
        sc.state["peers"]["pY"]["last_ok"] = t0 - OFFLINE_MS - 1000
        st = {d["id"]: d for d in sc.device_states()}["pY"]
        assert not st["online"] and st["err"] == "timed out", "一直没有消息: 算离线"
        # 对方拒绝连接 (OIT 没在运行): 25 秒没有消息就算离线, 不必等满 60 秒; 超时的照旧要等
        sc.state["peers"]["pY"]["last_ok"] = _now_ms() - 30_000
        sc.state["peers"]["pY"]["last_err"] = "[WinError 10061] 由于目标计算机积极拒绝，无法连接。"
        assert not {d["id"]: d for d in sc.device_states()}["pY"]["online"], "拒绝连接: 25 秒就算离线"
        sc.state["peers"]["pY"]["last_ok"] = _now_ms() - 10_000
        assert {d["id"]: d for d in sc.device_states()}["pY"]["online"], "拒绝连接但才 10 秒: 还在线"
        sc.state["peers"]["pY"]["last_err"] = "timed out"
        sc.state["peers"]["pY"]["last_ok"] = _now_ms() - 30_000
        assert {d["id"]: d for d in sc.device_states()}["pY"]["online"], "超时的要等满 60 秒"
        sc.state["peers"]["pY"]["last_ok"] = t0
        sc.state["peers"]["pY"]["last_err"] = "away"
        assert not {d["id"]: d for d in sc.device_states()}["pY"]["online"], "对方明确不接受: 马上算离线"
        sc.state["peers"]["pY"]["last_ok"] = t0 - 2 * OFFLINE_MS
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
        sc.state["peers"]["pY"]["last_ok"] = t0
        # 本机关机的那段 (15.4) 也算进离线的时间段: 本机之后记下的专注里这段记为空闲
        sc.add_self_off(t0 - 50 * 60_000, t0 - 40 * 60_000)
        assert (t0 - 50 * 60_000, t0 - 40 * 60_000) in sc.offline_spans(t0 - 60 * 60_000, t0 - 30 * 60_000)
        sc.state["self_off"] = []
        # 断网 (15.5): 最后的状态写着 adapter → 断网, 效果和在线一样; 回来时记下断网的那一段
        sc.state["peers"]["pY"]["last_err"] = "timeout"
        sc.state["peers"]["pY"]["last_ok"] = t0 - 2 * OFFLINE_MS
        sc.state["status"]["pY"] = {"ts": t0 - 60_000 - OFFLINE_MS, "timer": "running", "adapter": t0 - 3_600_000}
        st = {d["id"]: d for d in sc.device_states()}["pY"]
        assert st["online"] and st["net_off"] and not st["off"], st
        sc.merge({"status": {"pY": {"ts": t0 + 1, "timer": "idle"}}})
        back = sc.pop_adapter_back()
        assert [(d, a) for d, a, _b in back] == [("pY", t0 - 3_600_000)] and sc.pop_adapter_back() == []
        sc.state["peers"]["pY"]["last_err"] = ""
        sc.state["peers"]["pY"]["last_ok"] = t0
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
        # 自由学习的外出 (MECHANICS 21.6): 外出的设备回来以后, 外出期间 (结束之前) 改过的计时器状态 / 整点记录它有优先权
        win = (t0 - 3_600_000, t0 - 600_000)
        old_live, new_live = {"state": "idle"}, {"state": "running"}
        assert not sc._wins("timer", "live", t0 - 2_000_000, new_live, (t0 - 700_000, old_live)), "没有外出: 谁新听谁的"
        assert sc._wins("timer", "live", t0 - 2_000_000, new_live, (t0 - 700_000, old_live), win, None), "它刚回来: 外出期间本机改的让位"
        assert not sc._wins("timer", "live", t0 - 2_000_000, new_live, (t0 - 100_000, old_live), win, None), \
            "本机在它回来之后才改的不让"
        assert not sc._wins("timer", "live", t0 - 700_000, old_live, (t0 - 2_000_000, new_live), None, win), "本机刚回来: 别的设备在外出期间改的让位"
        assert sc._wins("timer", "live", t0 - 100_000, old_live, (t0 - 2_000_000, new_live), None, win), "回来之后别人才改的听别人的"
        assert not sc._wins("timer", "break_carry", t0 - 2_000_000, 5, (t0 - 700_000, 9), win, None), "别的项照旧按时间"
        day = datetime.fromtimestamp((t0 - 1_800_000) / 1000)
        key_in = f"{day:%Y-%m-%d}/{day.hour}"
        key_out = f"{day:%Y-%m-%d}/{(day.hour + 5) % 24}"
        assert sc._wins("hours", key_in, t0 - 2_000_000, "study", (t0 - 700_000, "play"), win, None), "外出期间的那一小时"
        assert not sc._wins("hours", key_out, t0 - 2_000_000, "study", (t0 - 700_000, "play"), win, None), "不在外出期间的小时"
        assert _away_win([t0 - 3_600_000, t0]) == (t0 - 3_600_000, t0) and _away_win([5, 3]) is None and _away_win(None) is None
        sc.merge({"status": {"pY": {"ts": t0 + 2, "timer": "idle", "away": [t0 - 3_600_000, t0 - 600_000]}}, "device": {"id": "pY"}})
        assert sc.pop_away_prio() == [("pY", t0 - 3_600_000, t0 - 600_000)] and sc.pop_away_prio() == []
        sc.state["status"]["pY"] = {"ts": t0, "timer": "idle"}
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
