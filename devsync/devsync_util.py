# -*- coding: utf-8 -*-
"""设备同步的共享常量与小工具 (MECHANICS 15).

纯数据 / 纯函数: 不发请求 (http_call 除外), 不碰 SyncService 的状态.
devsync.py 用 `from ... import *` 重新导出, 所以 devsync.OFFLINE_MS 之类的写法不变.
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
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler

import securefile
from devsync.devsync_crypto import file_xor


PROTO = 1


HTTP_PORT = 17930          # 被占用时依次尝试到 17939


UDP_PORT = 17931


SYNC_EVERY = 20            # 秒: 母端每隔这么久向每台子端发一次同步请求


SYNC_CONNECT = 5           # 秒: 连不上 (对方不在这个地址) 就去重新找地址, 不必久等


SYNC_READ = 30             # 秒: 连上之后等回复. 手机被系统短暂冻结 / Wi-Fi 休眠时会 8 ~ 20 秒才回, 等得太短会白白算成失败


# 设备离线 (Timer 页显示"离线", 这段专注记为空闲, MECHANICS 8.4): 这么久没有它的消息 (同步失败也要等这么久,
# 只有 HARD_ERRORS 里这几种明确的失败立刻算). 留够三轮同步: 偶尔一次超时 / 对方卡几秒, 下一轮就恢复了, 不该闪一下"离线";
# 离线的那段仍从最后一次有消息的时刻算起, 所以真的离线时专注记录不会少记; 只是转交过来的设备再多等一轮
OFFLINE_MS = 3 * SYNC_EVERY * 1000
# 同步失败的原因里, 下面这几种说明对方明确不接受 / 不在线 (不是偶然的网络问题): 不等 OFFLINE_MS, 马上算离线
HARD_ERRORS = ("unpaired", "wipe-pending", "away", "peer-not-proxy", "not-proxy")
UNBIND_WAIT_MS = 120 * 3600 * 1000      # 解除绑定要等这么久 (冷静期 120 小时, 期间可以取消)
UNBIND_FREE = True                      # 1.0.467: 解绑设备无代价 (立即解除, 不清空本机); 改回 False 恢复冷静期 120 小时
RESTART_RETRY = 30                    # 秒: 设备离线时请它重新启动 OIT, 连不上就每隔这么久再试一次, 直到有回应或它回来 (MECHANICS 15.3)
RESTART_FRESH_MS = 90_000             # 本机刚打开这么久之内不理重启请求 (对方看到的离线是打开之前的事)
RESTART_HEALTHY_MS = 30_000           # 刚和发请求的设备同步成功过 (任何一个方向) 这么久之内不理重启请求: 本机明明在线
RESTART_GAP_MS = 10 * 60_000          # 本机因为别的设备的请求重启过之后, 这么久之内不再理会重启请求 (免得两台设备互相拉着重启)
EVICT_RETRY = 30                        # 秒: 解除绑定后还没送到的清空请求, 每隔这么久再送一次
WIPES_KEEP = 50                         # 记住最近这么多个处理过的清空请求
OFFLINE_FWD_MS = OFFLINE_MS + (SYNC_EVERY + 5) * 1000
# 同步失败的原因是"对方拒绝连接" (它的主机开着, 只是 OIT 没在运行): 不是偶然的网络问题, 不必等满 OFFLINE_MS, 这么久没有消息就算离线 (MECHANICS 8.4)
REFUSED_OFFLINE_MS = 25_000
# 对方被卸载了 (MECHANICS 9.2): 手机上的设备管理员拦不住卸载 (部分手机 / 系统的卸载界面直接删), 所以由其他设备发现 ——
# 对方的主机一直在线 (同步端口和联动程序的端口都"拒绝连接", 不是超时) 连续这么久 = 对方的 OIT 和联动程序都没了 (卸载 / 强行停止), 本机只解除和它的绑定, 并清掉本机保留的它的数据 (账, 状态); 本机自己的数据不动
GONE_REFUSED_MS = 24 * 3600_000
GONE_GAP_MS = 30 * 60_000             # 两次"拒绝连接"之间隔了这么久 (本机睡了 / 关了 / 中间连上过) 就重新算
GONE_LINK_PORT = 17940                # 联动程序的端口 (linkd.LINK_PORT)


def refused(err: str) -> bool:
    """同步失败的原因是不是"对方拒绝连接" (Windows 10061 / 系统语言的"积极拒绝" / Linux 的 refused)."""
    e = str(err or "").lower()
    return "10061" in e or "refused" in e or "积极拒绝" in str(err or "")
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

# 设置里每台设备各自决定的项, 不同步
LOCAL_SETTINGS = {"NET_MODE", "FILTER_MODE", "FONT_SCALE", "CHIME_WAV",
                  "WINDOW_LIMIT_ENABLED", "WINDOW_LIMIT", "WINDOW_FOCUS_ENABLED",
                  "UPDATE_CHECK", "UPDATE_AUTO", "REC_ASK", "REC_CAMERA",
                  "CONTRACT_THEME", "FULLSCREEN_DEFAULT"}
OFFLINE_MS = 3 * SYNC_EVERY * 1000


HARD_ERRORS = ("unpaired", "wipe-pending", "away", "peer-not-proxy", "not-proxy")


UNBIND_WAIT_MS = 120 * 3600 * 1000      # 解除绑定要等这么久 (冷静期 120 小时, 期间可以取消)


UNBIND_FREE = True                      # 1.0.467: 解绑设备无代价 (立即解除, 不清空本机); 改回 False 恢复冷静期 120 小时


RESTART_RETRY = 30                    # 秒: 设备离线时请它重新启动 OIT, 连不上就每隔这么久再试一次, 直到有回应或它回来 (MECHANICS 15.3)


RESTART_FRESH_MS = 90_000             # 本机刚打开这么久之内不理重启请求 (对方看到的离线是打开之前的事)


RESTART_HEALTHY_MS = 30_000           # 刚和发请求的设备同步成功过 (任何一个方向) 这么久之内不理重启请求: 本机明明在线


RESTART_GAP_MS = 10 * 60_000          # 本机因为别的设备的请求重启过之后, 这么久之内不再理会重启请求 (免得两台设备互相拉着重启)


EVICT_RETRY = 30                        # 秒: 解除绑定后还没送到的清空请求, 每隔这么久再送一次


WIPES_KEEP = 50                         # 记住最近这么多个处理过的清空请求


OFFLINE_FWD_MS = OFFLINE_MS + (SYNC_EVERY + 5) * 1000


REFUSED_OFFLINE_MS = 25_000


GONE_REFUSED_MS = 24 * 3600_000


GONE_GAP_MS = 30 * 60_000             # 两次"拒绝连接"之间隔了这么久 (本机睡了 / 关了 / 中间连上过) 就重新算


GONE_LINK_PORT = 17940                # 联动程序的端口 (linkd.LINK_PORT)


def refused(err: str) -> bool:
    """同步失败的原因是不是"对方拒绝连接" (Windows 10061 / 系统语言的"积极拒绝" / Linux 的 refused)."""
    e = str(err or "").lower()
    return "10061" in e or "refused" in e or "积极拒绝" in str(err or "")


LOW_BATTERY = 2


UPDATE_MAX = 160 * 1024 * 1024    # 设备之间直接传的安装包最大多少


OFFER_TTL = 1800                  # 秒: 发出的安装包多久之内可以来取


FILE_MAX = 1 << 30                # 文件通道 (MECHANICS 15.6) 一次最多传多大


STALE_FWD_MS = 24 * 3600_000


FRESH_MS = 120_000         # 毫秒: 这么久之内同步过的设备才算"在线" (比较最后操作时间用)


PAIR_TIMEOUT = 120         # 秒: 等对方确认绑定


LOCAL_SETTINGS = {"NET_MODE", "FILTER_MODE", "FONT_SCALE", "CHIME_WAV",
                  "WINDOW_LIMIT_ENABLED", "WINDOW_LIMIT", "WINDOW_FOCUS_ENABLED",
                  "UPDATE_CHECK", "UPDATE_AUTO", "REC_ASK", "REC_CAMERA",
                  "CONTRACT_THEME", "FULLSCREEN_DEFAULT"}


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


H_NONE = _hash(None)


def _vtuple(text):
    """'1.0.55' → (1, 0, 55); 没有数字时 None (同 updater.parse_version)."""
    nums = [int(n) for n in re.findall(r"\d+", str(text or ""))[:3]]
    return tuple(nums + [0] * (3 - len(nums))) if nums else None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _away_win(v):
    """状态里的 away: [开始毫秒, 结束毫秒] → (开始, 结束); 不是这个格式时 None."""
    try:
        if isinstance(v, (list, tuple)) and len(v) == 2 and float(v[1]) > float(v[0]) > 0:
            return int(v[0]), int(v[1])
    except (TypeError, ValueError):
        pass
    return None


def _in_away(doc, key, win) -> bool:
    """这一项算不算外出期间会改到的内容: 计时器此刻的状态, 和与外出重叠的那些小时的整点记录."""
    if doc == "timer":
        return key == "live"
    if doc != "hours":
        return False
    try:
        day, hour = str(key).split("/")[:2]
        h0 = datetime.strptime(day, "%Y-%m-%d").replace(hour=int(hour)).timestamp() * 1000
    except (ValueError, IndexError):
        return False
    return h0 < win[1] and h0 + 3600_000 > win[0]


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


def write_state(path: str, state: dict):
    """重置后把留下的同步状态写回去 (数据目录刚被删掉)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with securefile.open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


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
    """其他设备今天的联网额度用量之和 (只算日期是今天的).
    还停在昨天的设备 (还没跨日 / 跨日后还没同步过来): 按它跨日后的样子算 —— 昨天借的 (明天扣的) 变成今天的欠款,
    其余清零. 这样本机一跨日额度就是对的, 不用等那台设备同步."""
    out = {"used": 0.0, "bonus": 0.0, "tomorrow_used": 0.0, "debt": 0.0, "gift": 0.0}
    try:
        yesterday = (datetime.strptime(str(date), "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    except ValueError:
        yesterday = None
    for led in ledgers:
        n = led.get("net") or {}
        if yesterday and n.get("date") == yesterday:
            try:
                out["debt"] += max(0.0, float(n.get("tomorrow_used", 0)))
            except (TypeError, ValueError):
                pass
            continue
        if n.get("date") != date:
            continue
        for k in out:
            try:
                out[k] += max(0.0, float(n.get(k, 0)))
            except (TypeError, ValueError):
                pass
    return out


def _make_handler(svc):
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
            if self.path == "/oit/v1/restart":
                return self._send(*svc._on_restart(body))
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
            if self.path == "/oit/v1/recstop":
                return self._send(*svc._on_recstop(body))
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
def http_call(addr: str, port: int, method: str, path: str, body=None, timeout: float = 6.0,
              read_timeout: float = None):
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


__all__ = [
    "PROTO",
    "HTTP_PORT",
    "UDP_PORT",
    "SYNC_EVERY",
    "SYNC_CONNECT",
    "SYNC_READ",
    "OFFLINE_MS",
    "HARD_ERRORS",
    "UNBIND_WAIT_MS",
    "UNBIND_FREE",
    "RESTART_RETRY",
    "RESTART_FRESH_MS",
    "RESTART_HEALTHY_MS",
    "RESTART_GAP_MS",
    "EVICT_RETRY",
    "WIPES_KEEP",
    "OFFLINE_FWD_MS",
    "REFUSED_OFFLINE_MS",
    "GONE_REFUSED_MS",
    "GONE_GAP_MS",
    "GONE_LINK_PORT",
    "LOW_BATTERY",
    "UPDATE_MAX",
    "OFFER_TTL",
    "FILE_MAX",
    "STALE_FWD_MS",
    "FRESH_MS",
    "PAIR_TIMEOUT",
    "LOCAL_SETTINGS",
    "H_NONE",
    "GONE_KEEP_MS",
    "refused",
    "_hash",
    "_vtuple",
    "_now_ms",
    "_away_win",
    "_in_away",
    "went_off",
    "battery_percent",
    "_ipv4_nets",
    "write_state",
    "safe_name",
    "_call",
    "peer_net",
    "_make_handler",
    "http_call",
]
