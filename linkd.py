"""
联动程序 (MECHANICS.md 15.8)
──────────────────────────────────────────────────────
OIT 主体被关掉 / 崩溃 / 被冻结时, 别的设备的"请对方重新启动 OIT"请求 (15.3) 没有人收.
联动程序是一个单独的后台进程, 登录时由任务计划程序启动 (autostart.py 的 LINK 任务), OIT 主体关闭后它照样在:

  · 监听 TCP 17940: 别的设备的联动程序发来"启动 OIT"的请求 (用绑定时的密钥加密 + 签名, 和同步一样)
    —— OIT 已经在运行 / 本机正在外出 (link_state.json 的 hold_until) / 刚启动过 就不理, 否则启动 OIT.
  · 每 15 秒看一眼绑定的设备的 OIT 还在不在 (它的同步端口答不答 hello). 连续 45 秒没答 ——
    就去问它的联动程序: 联动程序答了 = 那台设备还开着, 只是 OIT 没在运行 (不是那台设备离线) → 请它的联动程序启动 OIT;
    联动程序也不答 = 那台设备真的关机 / 断网了, 什么都不做.

只读 OIT 的 sync_state.json (绑定的地址和密钥) 和 link_state.json, 不依赖 tkinter.

自己设备上的 OIT 也由它看着: 绑定了其他设备时, 本机的 OIT 连续 35 秒不在运行 (没有互斥体, 同步端口也不答) 就替它重新启动 (外出期间除外).

更顽强 (MECHANICS 15.8):
  · 进程名不是 pythonw.exe 而是 oitlink.exe (解释器的副本): 结束 pythonw.exe 时它不会跟着没;
  · OIT 主体每 15 秒确认联动程序在不在, 不在就重新打开; 两边互相看着, 只结束其中一个会马上被拉起来;
  · 计划任务 `OIT link` 除了登录时, 还每分钟触发一次 (已经在运行时任务计划程序不会再开一个): 两个都被结束了一分钟内也会回来;
  · 每 5 分钟自己检查计划任务还在不在, 被删了 / 改坏了就重建 (OIT 的开机自启动被用户关掉时不建);
  · 服务线程死了 / 出了异常就在进程里重来, 不退出.
卸载时关掉 (uninstall.py).

    python linkd.py              运行 (已经有一个在运行就退出)
    python linkd.py --selftest   自检
"""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import devsync
import instance
import securefile

LINK_PORT = 17940
APP_NAME = "Obliphur's Integrated Terminal"      # 同 config.APP_NAME (自检里核对), 这里不能 import config (它要 tkinter)
PROBE_EVERY = 10              # 秒: 多久看一次绑定的设备的 OIT 还在不在
FAIL_FOR = 35                 # 秒: 连续这么久没答才去问它的联动程序: 比"拒绝连接算离线"的 25 秒 (devsync.REFUSED_OFFLINE_MS) 长, 先让别的设备看到它离线 (记空闲 / 提示), 再去启动
SELF_FAIL = FAIL_FOR          # 秒: 本机的 OIT 连续这么久不在才替它启动 (同上: 先让别的设备看到本机离线, 再启动)
SELF_GRACE = 40               # 秒: 联动程序自己刚启动的这段时间不替本机启动 OIT (登录时 OIT 的开机自启动正在启动它)
HEAL_EVERY = 300              # 秒: 多久检查一次自己的计划任务
LINK_EXE = "oitlink.exe"      # 联动程序的进程名 (pythonw.exe 的副本)
ASK_RETRY = 30               # 秒: 问了没成 (它的 OIT 还没起来 / 刚启动过) 就隔这么久再问
START_COOLDOWN = 120          # 秒: 启动过 OIT 之后这么久之内不再启动 (OIT 启动要一会儿)
BOOT_GRACE = 180              # 秒: 联动程序自己刚启动的这段时间不启动 OIT (登录时 OIT 的开机自启动正在启动它)
NO_WINDOW = 0x08000000
DETACHED = 0x00000008
TASK_NAME_RUN = "OIT autostart"


def data_dir() -> str:
    import closedtest
    if closedtest.active():
        return os.path.join(closedtest.low_dir(), APP_NAME)
    return os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "AppData", "LocalLow", APP_NAME)


def now_ms() -> int:
    return int(time.time() * 1000)


# ---- OIT 主体留下的两个文件 ----

def load_peers(dd: str):
    """(本机 id, {设备 id: {addr, port, key, name}}). 读不到时 ("", {})."""
    try:
        with securefile.open(os.path.join(dd, "sync_state.json"), "r", encoding="utf-8") as f:
            st = json.load(f)
        return str((st.get("device") or {}).get("id") or ""), dict(st.get("peers") or {})
    except (OSError, ValueError):
        return "", {}


def hold_until(dd: str) -> int:
    """OIT 主体写下的"这个时刻之前别替我启动" (毫秒): 外出的计划结束时刻 (外出期间的设备不算离线, MECHANICS 8.4). 没有时 0."""
    try:
        with securefile.open(os.path.join(dd, "link_state.json"), "r", encoding="utf-8") as f:
            return int(json.load(f).get("hold_until") or 0)
    except (OSError, ValueError, TypeError):
        return 0


def write_hold(dd: str, until_ms: int):
    """OIT 主体: 记下 hold_until (0 = 没有). 没变的时候不写."""
    path = os.path.join(dd, "link_state.json")
    if hold_until(dd) == int(until_ms) and os.path.exists(path):
        return
    try:
        os.makedirs(dd, exist_ok=True)
        tmp = path + ".tmp"
        with securefile.open(tmp, "w", encoding="utf-8") as f:
            json.dump({"hold_until": int(until_ms), "ts": now_ms()}, f)
        os.replace(tmp, path)
    except OSError:
        pass


# ---- 联动程序 ----

class Link:
    def __init__(self, dd: str, port: int = LINK_PORT, sync_ports=range(17930, 17940), peer_link_port=None,
                 launch=None, grace: float = BOOT_GRACE):
        self.dd = dd
        self.port = port
        self.sync_ports = list(sync_ports)
        self.peer_link_port = peer_link_port or (lambda pid: LINK_PORT)
        self.launch = launch or launch_oit
        self.born = time.time()
        self.grace = grace
        self.last_start = 0.0
        self.absent_since = None      # 本机的 OIT 从什么时候开始不在 (秒)
        self.server_thread = None
        self.fail_since = {}          # 设备 id → 它的 OIT 从什么时候开始没答 (秒)
        self.asked = {}               # 设备 id → [上次问的时刻, 它已经启动了 OIT (True)]
        self.server = None
        self.stop_flag = threading.Event()
        self.lock = threading.Lock()

    # -- 日志 --

    def log(self, text: str):
        path = os.path.join(self.dd, "link.log")
        try:
            os.makedirs(self.dd, exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) > 200_000:
                os.replace(path, path + ".old")
            with open(path, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
        except OSError:
            pass

    # -- 本机的 OIT --

    def oit_alive(self) -> bool:
        """本机的 OIT 主体在不在: 有 OIT 的实例锁 (instance.py, 刚打开还没开始监听的也算), 或者同步端口 (17930-17939) 上有它的 hello."""
        if instance.exists():
            return True
        my_id, _ = load_peers(self.dd)
        for p in self.sync_ports:
            try:
                st, d = devsync.SyncService._http("127.0.0.1", p, "GET", "/oit/v1/hello", timeout=2.0)
            except Exception:
                continue
            if st == 200 and (not my_id or d.get("id") == my_id):
                return True
        return False

    def start_oit(self, grace: float = None) -> str:
        """请求来了 / 自检发现 OIT 不在: 启动本机的 OIT. 返回结果 alive / hold / grace / cooldown / started / failed."""
        grace = self.grace if grace is None else grace
        with self.lock:
            if self.oit_alive():
                return "alive"
            if hold_until(self.dd) > now_ms():
                return "hold"
            if time.time() - self.born < grace:
                return "grace"
            if time.time() - self.last_start < START_COOLDOWN:
                return "cooldown"
            self.last_start = time.time()
            ok = False
            try:
                ok = bool(self.launch())
            except Exception as e:
                self.log(f"launch error {type(e).__name__}: {e}")
            self.log("start OIT: " + ("ok" if ok else "failed"))
            return "started" if ok else "failed"

    def self_check(self):
        """自己设备上的 OIT 在不在: 绑定了其他设备时, 连续 SELF_FAIL 秒不在就启动它 (外出期间 / 刚启动过 不启动). 返回 start_oit 的结果, 没动作时 ""."""
        if not load_peers(self.dd)[1] or self.oit_alive():
            self.absent_since = None
            return ""
        now = time.time()
        if self.absent_since is None:
            self.absent_since = now
        if now - self.absent_since < SELF_FAIL or now - self.born < SELF_GRACE:
            return ""
        res = self.start_oit(grace=0)
        if res in ("started", "failed"):
            self.log(f"self check: OIT not running for {int(now - self.absent_since)}s -> {res}")
        return res

    def heal(self):
        """计划任务 `OIT link` 被删了 / 改坏了就重建 (autostart.link_ensure: OIT 的开机自启动被用户关掉时不建)."""
        if sys.platform != "win32" or not load_peers(self.dd)[1]:
            return
        import autostart
        before = autostart.link_state()
        after = autostart.link_ensure()
        if before != after:
            self.log(f"link task: {before} -> {after}")

    def on_start_request(self, env: dict):
        sender = str(env.get("from") or "")
        _, peers = load_peers(self.dd)
        p = peers.get(sender)
        if not p:
            return 401, {"error": "unpaired"}
        try:
            body = devsync.unseal(bytes.fromhex(p["key"]), env)
        except devsync.SealError as e:
            return 403, {"error": str(e)}
        if body.get("link") != "start":
            return 400, {"error": "bad-request"}
        res = self.start_oit()
        self.log(f"start request from {p.get('name') or sender}: {res}")
        return 200, {"result": res}

    # -- 看绑定的设备 --

    def peer_oit_alive(self, p: dict) -> bool:
        try:
            st, _ = devsync.SyncService._http(p["addr"], int(p["port"]), "GET", "/oit/v1/hello", timeout=3.0)
            return st == 200
        except Exception:
            return False

    def ask_peer(self, pid: str, p: dict) -> str:
        """去问 p 的联动程序: 请它启动 OIT. 返回它的回复 (result), 联动程序也不答时 ""."""
        try:
            env = devsync.seal(bytes.fromhex(p["key"]), self.my_id, {"link": "start"})
            st, d = devsync.SyncService._http(p["addr"], self.peer_link_port(pid), "POST", "/oit/v1/link-start", env, timeout=4.0)
        except Exception:
            return ""
        return str(d.get("result") or f"http-{st}") if st else ""

    def probe_once(self):
        my_id, peers = load_peers(self.dd)
        self.my_id = my_id
        now = time.time()
        for pid in list(self.fail_since):
            if pid not in peers:
                self.fail_since.pop(pid, None)
                self.asked.pop(pid, None)
        for pid, p in peers.items():
            if not p.get("addr") or not p.get("port") or not p.get("key"):
                continue
            if self.peer_oit_alive(p):
                self.fail_since.pop(pid, None)
                self.asked.pop(pid, None)
                continue
            since = self.fail_since.setdefault(pid, now)
            a = self.asked.get(pid) or [0.0, False]
            if now - since < FAIL_FOR or a[1] or now - a[0] < ASK_RETRY:
                continue
            a[0] = now
            self.asked[pid] = a
            res = self.ask_peer(pid, p)
            name = p.get("name") or pid
            if not res:
                self.log(f"{name}: OIT not answering, link not answering either (device is down / unreachable)")
            else:
                self.log(f"{name}: OIT not answering, asked its link to start OIT -> {res}")
                if res == "started":
                    a[1] = True

    # -- 运行 --

    def serve(self):
        link = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/oit/v1/link":
                    return self._send(200, {"link": 1})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n).decode("utf-8")) if 0 < n < 100_000 else {}
                except ValueError:
                    body = {}
                if self.path == "/oit/v1/link-start":
                    return self._send(*link.on_start_request(body))
                if self.path == "/oit/v1/link-quit" and self.client_address[0] == "127.0.0.1":
                    self._send(200, {})
                    link.stop_flag.set()
                    return
                self._send(404, {"error": "not found"})

        class S(HTTPServer):
            allow_reuse_address = False         # 端口被占 = 已经有一个联动程序在运行 (Windows 上 reuse 会让两个都绑得上)
            daemon_threads = True

        self.server = S(("0.0.0.0", self.port), H)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def close_server(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass

    def run(self) -> str:
        """运行到被要求退出. 返回 busy (已经有一个在运行) / quit (被要求关掉) / changed (程序文件更新了, 已用新代码重新打开) / dead (服务线程死了, 调用方重来)."""
        self.my_id = ""
        securefile.configure(self.dd)            # OIT 的数据是加密的 (MECHANICS 9.4): 读 sync_state.json / link_state.json 要用同一个密钥
        try:
            self.serve()
        except OSError:
            return "busy"
        self.log("link started")
        mtimes = _mtimes()
        last_watch = last_heal = time.time()
        result = "quit"
        while not self.stop_flag.is_set():
            try:
                self.self_check()
            except Exception as e:
                self.log(f"self check error {type(e).__name__}: {e}")
            try:
                self.probe_once()
            except Exception as e:
                self.log(f"probe error {type(e).__name__}: {e}")
            if self.stop_flag.wait(PROBE_EVERY):
                break
            if not self.server_thread.is_alive():
                self.log("server thread died, restarting")
                result = "dead"
                break
            if time.time() - last_heal >= HEAL_EVERY:
                last_heal = time.time()
                try:
                    self.heal()
                except Exception as e:
                    self.log(f"heal error {type(e).__name__}: {e}")
            if time.time() - last_watch >= 60:
                last_watch = time.time()
                if _mtimes() != mtimes:         # 程序文件更新了: 用新的代码重新打开自己
                    self.log("program files changed, restarting")
                    self.close_server()
                    spawn_self(self.dd)
                    return "changed"
        self.close_server()
        return result


def _mtimes():
    here = os.path.dirname(os.path.abspath(__file__))
    out = []
    for n in ("linkd.py", "devsync.py"):
        try:
            out.append(os.path.getmtime(os.path.join(here, n)))
        except OSError:
            out.append(0)
    return out


# ---- 启动 OIT / 启动联动程序 ----

def launch_oit() -> bool:
    """启动 OIT: 先跑开机自启动的计划任务 (以最高权限启动, 已经在运行就不再开); 任务没有 / 被禁用了就直接启动."""
    if sys.platform != "win32":
        return False
    try:
        r = subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME_RUN], capture_output=True, timeout=30, creationflags=NO_WINDOW)
        if r.returncode == 0:
            return True
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    exe = _pyw()
    try:
        subprocess.Popen([exe, os.path.join(here, "main.py")], cwd=here, close_fds=True, creationflags=NO_WINDOW)
        return True
    except OSError:
        return False


def _pyw() -> str:
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return pyw if os.path.exists(pyw) else exe


_exe_failed = False


def link_exe() -> str:
    """联动程序的可执行文件: pythonw.exe 的副本 oitlink.exe (放在解释器旁边, 能找到它的库). 这样任务管理器里进程叫 oitlink.exe,
    结束 pythonw.exe (OIT 主体) 时它不会跟着没. 复制不了 / 副本跑不起来时 (例如 Microsoft Store 版的 Python: 库只许包自己的进程加载) 用 pythonw.exe 本身."""
    global _exe_failed
    pyw = _pyw()
    if sys.platform != "win32" or os.path.basename(pyw).lower() == LINK_EXE:
        return pyw
    dst = os.path.join(os.path.dirname(pyw), LINK_EXE)
    tmp = os.path.join(os.path.dirname(pyw), "oitlink-new.exe")
    try:
        a = os.stat(pyw)
        try:
            b = os.stat(dst)
            if b.st_size == a.st_size and int(b.st_mtime) == int(a.st_mtime):
                return dst
        except OSError:
            pass
        import shutil
        if _exe_failed:
            raise OSError("copy failed before")
        shutil.copy2(pyw, tmp)
        r = subprocess.run([tmp, "-c", "import sys, socket, ctypes, http.server, json"], capture_output=True, timeout=30, creationflags=NO_WINDOW)
        if r.returncode != 0:
            _exe_failed = True                  # 这个进程里不再试
            raise OSError("copy does not run")
        os.replace(tmp, dst)                    # 副本正在运行 (替换不了) 时抛 PermissionError
        return dst
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return dst if os.path.exists(dst) else pyw


def remove_exe() -> bool:
    """卸载: 删掉 oitlink.exe (进程已经关掉了). 返回删干净了没有."""
    if sys.platform != "win32":
        return True
    d = os.path.dirname(_pyw())
    ok = True
    for n in (LINK_EXE, "oitlink-new.exe"):
        try:
            os.remove(os.path.join(d, n))
        except FileNotFoundError:
            pass
        except OSError:
            ok = False
    return ok


def link_command():
    """(要运行的程序, 脚本, 工作目录): 联动程序. 工作目录是数据目录 (不占着程序文件夹, 卸载时删得掉)."""
    here = os.path.dirname(os.path.abspath(__file__))
    return link_exe(), os.path.join(here, "linkd.py"), data_dir()


def running(port: int = LINK_PORT) -> bool:
    try:
        st, _ = devsync.SyncService._http("127.0.0.1", port, "GET", "/oit/v1/link", timeout=1.5)
        return st == 200
    except Exception:
        return False


def spawn_self(dd: str):
    exe, script, _ = link_command()
    os.makedirs(dd, exist_ok=True)
    flags = (DETACHED | NO_WINDOW) if sys.platform == "win32" else 0
    subprocess.Popen([exe, script], cwd=dd, close_fds=True, creationflags=flags)


def ensure_running(dd: str = None) -> bool:
    """OIT 主体: 联动程序没在运行就打开它. 返回打开了没有."""
    if sys.platform != "win32" or running():
        return False
    try:
        spawn_self(dd or data_dir())
        return True
    except OSError:
        return False


def stop(port: int = LINK_PORT) -> bool:
    """卸载: 关掉本机的联动程序. 返回关掉了 (或本来就没有)."""
    if not running(port):
        return True
    try:
        devsync.SyncService._http("127.0.0.1", port, "POST", "/oit/v1/link-quit", {}, timeout=3.0)
    except Exception:
        pass
    for _ in range(20):
        if not running(port):
            return True
        time.sleep(0.25)
    return False


# ---- 自检 ----

def _selftest():
    import shutil
    import tempfile
    import config
    assert APP_NAME == config.APP_NAME and data_dir().endswith(config.APP_NAME)
    key = os.urandom(32).hex()
    real_exists = instance.exists
    instance.exists = lambda *a: False          # 本机真有 OIT 在运行时 (互斥体在) 也不影响自检

    def make(my, other, other_port):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "sync_state.json"), "w", encoding="utf-8") as f:
            json.dump({"device": {"id": my}, "peers": {other: {"name": other, "addr": "127.0.0.1", "port": other_port, "key": key}}}, f)
        return d

    da, db = make("A", "B", 1), make("B", "A", 1)       # 双方的 OIT 同步端口都是 1: 没人在听 = OIT 都没在运行
    launched = []
    la = Link(da, port=18941, sync_ports=[1], peer_link_port=lambda pid: 18942, launch=lambda: launched.append("A") or True, grace=0)
    lb = Link(db, port=18942, sync_ports=[1], peer_link_port=lambda pid: 18941, launch=lambda: launched.append("B") or True, grace=0)
    la.serve(); lb.serve()
    try:
        assert running(18941) and running(18942)
        try:
            Link(da, port=18941).serve()
            raise AssertionError("第二个联动程序应该绑不上端口")
        except OSError:
            pass
        la.my_id, lb.my_id = "A", "B"
        # 外出期间 (hold_until) 不启动
        write_hold(db, now_ms() + 3600_000)
        assert hold_until(db) > now_ms() and la.ask_peer("B", load_peers(da)[1]["B"]) == "hold" and launched == []
        write_hold(db, 0)
        assert hold_until(db) == 0
        # 密钥不对 / 没绑定: 拒绝
        bad = dict(load_peers(da)[1]["B"], key=os.urandom(32).hex())
        assert la.ask_peer("B", bad) == "http-403"
        assert la.ask_peer("B", load_peers(da)[1]["B"]) == "started" and launched == ["B"]
        # 刚启动过: 冷却
        assert la.ask_peer("B", load_peers(da)[1]["B"]) == "cooldown" and launched == ["B"]
        # 探测: 对方 OIT 不答满 FAIL_FOR 才去问; 问到启动了就不再问
        lb.last_start = 0
        la.fail_since.clear(); la.asked.clear()
        la.probe_once()
        assert launched == ["B"] and "B" in la.fail_since, "刚开始没答: 先等"
        la.fail_since["B"] -= FAIL_FOR + 1
        la.probe_once()
        assert launched == ["B", "B"] and la.asked["B"][1] is True
        la.fail_since["B"] -= 1
        la.asked["B"][0] -= ASK_RETRY + 1
        la.probe_once()
        assert launched == ["B", "B"], "已经启动了就不再问"
        # 对方整台设备不在 (联动程序也不答): 什么都不做
        lb.server.shutdown(); lb.server.server_close()
        la.asked.clear()
        la.fail_since["B"] -= 1
        la.probe_once()
        assert launched == ["B", "B"] and la.asked["B"][1] is False
        # 对方的 OIT 答了: 清掉记录
        assert not la.peer_oit_alive(load_peers(da)[1]["B"])
        # 自己设备上的 OIT 不在 (自检): 不满 SELF_FAIL / 联动程序刚启动 / 外出期间 都不启动, 否则启动一次, 之后冷却
        mine = []
        lc = Link(da, port=18943, sync_ports=[1], launch=lambda: mine.append(1) or True, grace=0)
        alive = [False]
        lc.oit_alive = lambda: alive[0]
        assert lc.self_check() == "" and lc.absent_since is not None, "刚发现不在: 先等"
        lc.absent_since -= SELF_FAIL + 1
        lc.born -= SELF_GRACE + 1
        write_hold(da, now_ms() + 3600_000)
        assert lc.self_check() == "hold" and mine == []
        write_hold(da, 0)
        young = Link(da, port=18944, sync_ports=[1], launch=lambda: mine.append(2) or True, grace=0)
        young.oit_alive = lambda: False
        young.absent_since = time.time() - SELF_FAIL - 1
        assert young.self_check() == "" and mine == [], "联动程序刚启动: 不启动"
        assert lc.self_check() == "started" and mine == [1]
        assert lc.self_check() == "cooldown" and mine == [1]
        alive[0] = True
        assert lc.self_check() == "" and lc.absent_since is None, "OIT 回来了: 清掉记录"
        alive[0] = False
        lc.absent_since = None
        assert lc.self_check() == "" and lc.absent_since is not None
        # 没有绑定的设备: 不管 (划掉就是关掉)
        dn = tempfile.mkdtemp()
        try:
            ln = Link(dn, port=18945, sync_ports=[1], launch=lambda: mine.append(3) or True, grace=0)
            ln.oit_alive = lambda: False
            ln.absent_since = time.time() - 999
            assert ln.self_check() == "" and mine == [1]
        finally:
            shutil.rmtree(dn, ignore_errors=True)
        # 联动程序的可执行文件: 能跑的 pythonw.exe 副本 oitlink.exe (复制不了就是 pythonw.exe 本身)
        exe = link_exe()
        assert os.path.exists(exe) and exe == link_command()[0]
        if sys.platform == "win32" and os.path.basename(exe).lower() == LINK_EXE:
            out = subprocess.run([exe.replace("pythonw", "python") if os.path.exists(exe.replace("oitlink", "python")) else exe,
                                  "-c", "print(1)"], capture_output=True, text=True).returncode
            assert out == 0
    finally:
        instance.exists = real_exists
        la.server.shutdown(); la.server.server_close()
        for d in (da, db):
            shutil.rmtree(d, ignore_errors=True)
    assert stop(18941) is True
    return "linkd selftest OK"


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        print(_selftest())
    else:
        while True:                                  # 出了异常 / 服务线程死了: 在进程里重来, 不退出
            try:
                if Link(data_dir()).run() != "dead":
                    break
            except Exception as e:
                try:
                    Link(data_dir()).log(f"crashed {type(e).__name__}: {e}")
                except Exception:
                    pass
            time.sleep(5)
