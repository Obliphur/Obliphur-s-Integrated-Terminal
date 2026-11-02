"""
不走系统代理的程序: 发现它直接连外面, 就封锁这条连接 (MECHANICS 3.2.3)
──────────────────────────────────────────────────────────────────────
Proxy / 代理-全屏蔽 断网方式只管得住遵守系统代理的程序. 遵守系统代理的程序只会连本机的过滤代理 (127.0.0.1),
所以 TCP 连接表里出现"某个程序直接连着外面的地址" = 这条连接不走系统代理 (游戏, 下载器, 聊天软件, 自己带 VPN / 代理的软件 ……).

发现之后 (每 2 秒看一次连接表, 后台线程), 封锁的是**这条连接**, 不是这个程序:
  1. 把这条直连的连接掐断 (SetTcpEntry → DELETE_TCB; 防火墙不会切断已经建立的连接);
  2. 加一条只管"这个程序 → 这个远端地址"的 Windows 防火墙出站阻止规则 `OIT bypass block` (program=<路径> remoteip=<地址>),
     它再连同一个地址的新连接被挡; 连别的地址不受影响 (发现了再一条一条封). 规则 10 分钟没再见到这条连接就删掉;
  3. 不弹提示, 只在流水里记 `BYPASS` (每个程序一次).
这个程序走过滤代理的部分 (连本机 127.0.0.1) 防火墙不管, 不受影响; 它连局域网也不受影响.
规则也在换成 Adapter 方式 / 开发者模式 / 重置 / 卸载时删掉, 程序重新打开时先删掉上次留下的.

不算直连的 (不会被封): 本机回环 (连过滤代理, 连本机的另一个代理软件), 局域网 / 链路本地 / 组播 / 运营商共享地址 (设备同步, 打印机, Tailscale 之类),
以及这些程序 ——
  · OIT 自己 (进程 + 解释器的可执行文件: 联网检查更新, 经过上游代理转发 ……);
  · Windows 自己 (%SystemRoot% 下的程序: DNS 客户端, 系统更新, 对时 ……) 和 Windows Defender;
  · 用户原来的代理软件 (Clash 之类, 监听 OIT 接管系统代理之前的代理地址的那个程序): 过滤代理的流量要靠它转出去;
  · config.BYPASS_ALLOW 里写的 exe 名.
只看 TCP (UDP 没有连接可看; 浏览器的 UDP / QUIC 已经被浏览器围栏挡住, 3.2.2). 只在 Proxy / 代理-全屏蔽 方式下生效 (和浏览器围栏一样).
局限: 看到的只是扫描那一刻的连接, 很短的连接可能错过; 程序换一个远端地址重连, 要等下一轮扫描才封 (这之间最多 2 秒);
IPv6 的已建立连接掐不断, 只靠规则挡新连接.

    python bypassguard.py      现在有哪些程序在直连 (只读, 不封锁)
"""

import ctypes
import ipaddress
import os
import socket
import struct
import sys
import time

import appban

RULE = "OIT bypass block"
SCAN_EVERY = 2.0              # 秒
RULE_TTL = 600.0              # 秒: 规则管的这条连接这么久没再见到就删掉
AF_INET, AF_INET6 = 2, 23
TCP_TABLE_OWNER_PID_ALL = 5
ST_LISTEN, ST_SYN_SENT, ST_ESTAB = 2, 3, 5
ST_DELETE_TCB = 12
ERROR_INSUFFICIENT_BUFFER = 122

# 路径以这些开头的程序不封 (展开环境变量后比较)
EXEMPT_PREFIXES = (
    "%SystemRoot%",
    "%ProgramData%\\Microsoft\\Windows Defender",
    "%ProgramFiles%\\Windows Defender",
    "%ProgramFiles%\\Windows Defender Advanced Threat Protection",
)


class Conn:
    """TCP 连接表的一行."""
    __slots__ = ("family", "pid", "state", "laddr", "lport", "raddr", "rport", "raw")

    def __init__(self, family, pid, state, laddr, lport, raddr, rport, raw=None):
        self.family, self.pid, self.state = family, pid, state
        self.laddr, self.lport, self.raddr, self.rport, self.raw = laddr, lport, raddr, rport, raw

    def __repr__(self):
        return f"<{self.pid} {self.laddr}:{self.lport}->{self.raddr}:{self.rport} st={self.state}>"


class _Row4(ctypes.Structure):
    _fields_ = [("state", ctypes.c_ulong), ("local_addr", ctypes.c_ulong), ("local_port", ctypes.c_ulong),
                ("remote_addr", ctypes.c_ulong), ("remote_port", ctypes.c_ulong), ("pid", ctypes.c_ulong)]


class _Row6(ctypes.Structure):
    _fields_ = [("local_addr", ctypes.c_ubyte * 16), ("local_scope", ctypes.c_ulong), ("local_port", ctypes.c_ulong),
                ("remote_addr", ctypes.c_ubyte * 16), ("remote_scope", ctypes.c_ulong), ("remote_port", ctypes.c_ulong),
                ("state", ctypes.c_ulong), ("pid", ctypes.c_ulong)]


def _port(raw: int) -> int:
    return socket.ntohs(raw & 0xFFFF)


def _table(family: int, row_type):
    iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
    size = ctypes.c_ulong(0)
    buf = None
    for _ in range(4):
        rc = iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, family, TCP_TABLE_OWNER_PID_ALL, 0)
        if rc == 0:
            break
        if rc != ERROR_INSUFFICIENT_BUFFER:
            return []
        buf = ctypes.create_string_buffer(size.value + 4096)
        size = ctypes.c_ulong(size.value + 4096)
    else:
        return []
    count = ctypes.c_ulong.from_buffer(buf).value
    rows = (row_type * count).from_buffer(buf, ctypes.sizeof(ctypes.c_ulong))
    # 每一行复制一份: 行本来是指着 buf 里面的 (from_address 还不会让 buf 活着), buf 一出这个函数就释放了, 之后读行 = 读已经释放的内存,
    # 内存被收回时程序直接崩溃 (_ctypes.pyd 访问冲突 0xc0000005, 每隔几分钟一次)
    return [row_type.from_buffer_copy(r) for r in rows]


def tcp_conns() -> list:
    """本机现在的 TCP 连接 (IPv4 + IPv6), 带着拥有它的进程 id. 非 Windows / 出错返回 []."""
    if sys.platform != "win32":
        return []
    out = []
    try:
        for r in _table(AF_INET, _Row4):
            out.append(Conn(4, int(r.pid), int(r.state), socket.inet_ntoa(struct.pack("<L", r.local_addr)), _port(r.local_port),
                            socket.inet_ntoa(struct.pack("<L", r.remote_addr)), _port(r.remote_port),
                            raw=(r.local_addr, r.local_port, r.remote_addr, r.remote_port)))
        for r in _table(AF_INET6, _Row6):
            out.append(Conn(6, int(r.pid), int(r.state), str(ipaddress.IPv6Address(bytes(r.local_addr))), _port(r.local_port),
                            str(ipaddress.IPv6Address(bytes(r.remote_addr))), _port(r.remote_port)))
    except Exception:
        return out
    return out


def kill_conn(c: Conn) -> bool:
    """掐断一条已经建立的连接 (只有 IPv4 能掐; 需要管理员权限)."""
    if sys.platform != "win32" or c.family != 4 or not c.raw:
        return False
    try:
        row = _Row4(ST_DELETE_TCB, c.raw[0], c.raw[1], c.raw[2], c.raw[3], c.pid)
        return ctypes.WinDLL("iphlpapi").SetTcpEntry(ctypes.byref(row)) == 0
    except Exception:
        return False


def pid_path(pid: int) -> str:
    """进程的 exe 完整路径; 查不到 ""."""
    if sys.platform != "win32" or pid in (0, 4):
        return ""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    k32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.c_ulong(len(buf))
        return buf.value if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ""
    finally:
        k32.CloseHandle(h)


def is_external(addr: str) -> bool:
    """会出本地网络的地址 (不是回环 / 私有 / 链路本地 / 组播 / 运营商共享 / 基准测试 ……)."""
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def own_exes() -> set:
    """OIT 自己用的可执行文件 (规范化的完整路径): 当前解释器和它旁边的 python / pythonw / 联动程序的副本."""
    base = os.path.dirname(os.path.abspath(sys.executable))
    names = {os.path.abspath(sys.executable), os.path.join(base, "python.exe"), os.path.join(base, "pythonw.exe"),
             os.path.join(base, "oitlink.exe")}
    return {os.path.normcase(p) for p in names}


def _exempt_prefixes() -> list:
    out = []
    for p in EXEMPT_PREFIXES:
        e = os.path.normcase(os.path.expandvars(p))
        if "%" not in e:
            out.append(e.rstrip("\\") + "\\")
    return out


class BypassGuard(appban.AppBan):
    """封锁不走系统代理的直连连接. self.rules: {(程序路径, 远端地址): 最后一次见到的时刻}; self.paths: 有规则的程序."""

    def __init__(self, run=appban._run, tcp=tcp_conns, path_of=pid_path, kill=kill_conn, upstream=None, own_pid=None,
                 allow=None, exes=None, prefixes=None, clock=time.monotonic):
        super().__init__(run, rule=RULE)
        self.tcp, self.path_of, self.kill = tcp, path_of, kill
        self._upstream = upstream or _upstream_port
        self.own_pid = own_pid or os.getpid()
        self._allow = allow
        self._exes = exes
        self._prefixes = prefixes
        self.clock = clock
        self.rules = {}
        self.seen = set()                       # 已经提示过的程序
        self.last = []                          # 上一轮看到的直连程序 [(路径, 连接数)] (只读用)

    def clear_all(self):
        super().clear_all()
        self.rules = {}

    def allow(self) -> set:
        if self._allow is not None:
            return {a.lower() for a in self._allow}
        try:
            import config
            return {a.lower() for a in getattr(config, "BYPASS_ALLOW", ())}
        except Exception:
            return set()

    def exempt(self, path: str, upstream_paths: set) -> bool:
        p = os.path.normcase(path)
        if p in (self._exes if self._exes is not None else own_exes()) or p in upstream_paths:
            return True
        if os.path.basename(p) in self.allow():
            return True
        return any(p.startswith(pre) for pre in (self._prefixes if self._prefixes is not None else _exempt_prefixes()))

    def find(self, conns=None):
        """→ ({路径: [直连的连接]}, 用户原来的代理软件的路径集合). 不动任何东西."""
        conns = self.tcp() if conns is None else conns
        up = self._upstream()
        up_paths = set()
        if up:
            for c in conns:
                if c.state == ST_LISTEN and c.lport == up:
                    p = self.path_of(c.pid)
                    if p:
                        up_paths.add(os.path.normcase(p))
        groups = {}
        for c in conns:
            if c.state not in (ST_ESTAB, ST_SYN_SENT) or c.pid in (0, 4, self.own_pid) or not is_external(c.raddr):
                continue
            path = self.path_of(c.pid)
            if not path or self.exempt(path, up_paths):
                continue
            groups.setdefault(path, []).append(c)
        return groups, up_paths

    def _delete(self, path: str, ip: str):
        self.run(appban._netsh("delete", "rule", f"name={self.rule}", f"program={path}", f"remoteip={ip}"))
        self.rules.pop((path, ip), None)
        if not any(p == path for p, _ in self.rules):
            self.paths.discard(path)

    def scan(self) -> list:
        """找出直连的连接, 封锁 (掐断 + "这个程序 → 这个地址"的防火墙规则). 返回 [(路径, 掐断的连接数, 这个程序是不是第一次被提示)]."""
        now = self.clock()
        groups, _ = self.find()
        self.last = [(p, len(cs)) for p, cs in groups.items()]
        out = []
        for path, cs in sorted(groups.items()):
            for ip in sorted({c.raddr for c in cs}):
                key = (path, ip)
                if key not in self.rules:
                    rc, _ = self.run(appban._netsh("add", "rule", f"name={self.rule}", "dir=out", "action=block",
                                                   f"program={path}", f"remoteip={ip}", "enable=yes", "profile=any"))
                    if rc != 0:
                        continue
                    self.paths.add(path)
                self.rules[key] = now
            killed = sum(1 for c in cs if self.kill(c))
            first = path not in self.seen
            self.seen.add(path)
            out.append((path, killed, first))
        for (path, ip), seen_at in list(self.rules.items()):       # 很久没再见到的连接: 规则删掉
            if now - seen_at > RULE_TTL:
                self._delete(path, ip)
        return out


def _upstream_port():
    """用户原来的代理软件监听的端口 (OIT 接管系统代理之前的系统代理); 没有 None."""
    try:
        import updater
        up = updater.upstream_proxy()
        return int(up[1]) if up and up[0] in ("127.0.0.1", "localhost", "::1") else None
    except Exception:
        return None


def _selftest():
    me = 100
    GAME, V6 = r"C:\Games\game.exe", r"C:\Apps\ipv6app.exe"
    rows = [
        Conn(4, 10, ST_ESTAB, "192.168.1.5", 50000, "93.184.216.34", 443, raw=(1, 2, 3, 4)),          # 游戏直连外面
        Conn(4, 10, ST_ESTAB, "192.168.1.5", 50001, "93.184.216.34", 80, raw=(1, 2, 5, 6)),           # 同一个地址的另一条
        Conn(4, 11, ST_ESTAB, "127.0.0.1", 50002, "127.0.0.1", 17890, raw=None),                          # 浏览器连过滤代理
        Conn(4, 12, ST_ESTAB, "192.168.1.5", 50003, "192.168.1.9", 17930, raw=None),                      # 设备同步 (局域网)
        Conn(4, 13, ST_ESTAB, "192.168.1.5", 50004, "100.64.1.2", 443, raw=None),                         # 运营商共享地址
        Conn(4, me, ST_ESTAB, "192.168.1.5", 50005, "140.82.112.5", 443, raw=None),                       # OIT 自己 (检查更新)
        Conn(4, 14, ST_ESTAB, "192.168.1.5", 50006, "1.1.1.1", 443, raw=None),                            # svchost
        Conn(4, 15, ST_LISTEN, "127.0.0.1", 7890, "0.0.0.0", 0, raw=None),                                # Clash 监听
        Conn(4, 15, ST_ESTAB, "192.168.1.5", 50007, "8.8.4.4", 443, raw=None),                            # Clash 连出去
        Conn(6, 16, ST_ESTAB, "2001:db8::1", 50008, "2606:4700::1111", 443),                              # IPv6 直连
        Conn(4, 17, ST_ESTAB, "192.168.1.5", 50009, "9.9.9.9", 443, raw=None),                            # 在 BYPASS_ALLOW 里
        Conn(4, 18, ST_ESTAB, "192.168.1.5", 50010, "9.9.9.10", 443, raw=None),                           # 路径查不到
        Conn(4, 19, 1, "192.168.1.5", 50011, "9.9.9.11", 443, raw=None),                                  # 已关闭的连接
        Conn(4, 4, ST_ESTAB, "192.168.1.5", 50012, "9.9.9.12", 443, raw=None),                            # System
    ]
    paths = {10: GAME, 11: r"C:\Program Files\Chrome\chrome.exe", 12: r"C:\Program Files\OIT\x.exe",
             13: r"C:\Tools\tailscale.exe", me: r"C:\Python\pythonw.exe", 14: r"C:\Windows\System32\svchost.exe",
             15: r"C:\Clash\clash.exe", 16: V6, 17: r"C:\Tools\Allowed.exe", 18: "", 19: r"C:\Apps\old.exe"}
    calls, killed = [], []
    t = [1000.0]

    def run(args, timeout=30):
        calls.append(args)
        return 0, "Ok."

    def mk(r=run, **kw):
        base = dict(tcp=lambda: rows, path_of=lambda pid: paths.get(pid, ""), kill=lambda c: killed.append(c.pid) or True,
                    upstream=lambda: 7890, own_pid=me, allow=["allowed.exe"], exes={os.path.normcase(r"C:\Python\pythonw.exe")},
                    prefixes=[os.path.normcase("C:\\Windows\\")], clock=lambda: t[0])
        base.update(kw)
        return BypassGuard(r, **base)

    g = mk()
    groups, up = g.find()
    assert sorted(groups) == [V6, GAME], sorted(groups)
    assert up == {os.path.normcase(r"C:\Clash\clash.exe")}
    res = g.scan()
    assert sorted(res) == [(V6, 1, True), (GAME, 2, True)], res            # 假的 kill 都成功; 游戏的两条连接都掐了
    adds = [c for c in calls if c[3] == "add"]
    # 规则只管"这个程序 → 这个远端地址": 游戏的两条连接是同一个地址, 一条规则
    assert len(adds) == 2 and all("dir=out" in c and "action=block" in c and f"name={RULE}" in c for c in adds)
    assert {(c[8], c[9]) for c in adds} == {(f"program={GAME}", "remoteip=93.184.216.34"), (f"program={V6}", "remoteip=2606:4700::1111")}
    assert set(g.rules) == {(GAME, "93.184.216.34"), (V6, "2606:4700::1111")} and g.paths == {GAME, V6}
    # 下一轮: 规则已经有了, 不再加, 也不再提示, 但还有直连的连接就继续掐
    calls.clear()
    killed.clear()
    res2 = g.scan()
    assert not [c for c in calls if c[3] == "add"] and all(not first for _p, _k, first in res2) and killed.count(10) == 2
    # 同一个程序换了一个远端地址: 只多一条规则 (整个程序没有被封)
    rows.append(Conn(4, 10, ST_ESTAB, "192.168.1.5", 50013, "151.101.1.1", 443, raw=(1, 2, 7, 8)))
    calls.clear()
    g.scan()
    adds = [c for c in calls if c[3] == "add"]
    assert len(adds) == 1 and "remoteip=151.101.1.1" in adds[0] and f"program={GAME}" in adds[0]
    rows.pop()
    # 游戏不再连那个地址了: 规则 10 分钟后删掉 (只删这一条), 还在连的留着
    t[0] += 5
    g.scan()
    saved = list(rows)
    rows[:] = [r for r in rows if r.pid != 10]
    t[0] += RULE_TTL + 1
    calls.clear()
    g.scan()
    dels = [c for c in calls if c[3] == "delete"]
    assert len(dels) == 2 and all(f"program={GAME}" in c and any(x.startswith("remoteip=") for x in c) for c in dels), dels
    assert not any(p == GAME for p, _ in g.rules) and GAME not in g.paths
    assert (V6, "2606:4700::1111") in g.rules
    rows[:] = saved
    # 防火墙规则加不上: 不记, 下一轮再试
    g2 = mk(lambda a, timeout=30: (1, "denied"), upstream=lambda: None, allow=[], exes=set(), prefixes=[])
    g2.scan()
    assert g2.rules == {} and g2.paths == set()
    # 用户没有原来的代理: Clash 的直连也算 (没有人给它豁免)
    assert r"C:\Clash\clash.exe" in g2.find()[0]
    # 清掉所有规则
    g.clear_all()
    assert calls[-1] == appban._netsh("delete", "rule", f"name={RULE}") and g.paths == set() and g.rules == {}
    # 地址分类
    assert is_external("93.184.216.34") and is_external("2606:4700::1111")
    for a in ("127.0.0.1", "::1", "10.1.2.3", "192.168.0.8", "172.16.5.5", "169.254.1.1", "224.0.0.251", "100.64.1.2",
              "198.18.0.5", "fe80::1", "fd00::1", "0.0.0.0", "not-an-ip"):
        assert not is_external(a), a
    assert is_external("::ffff:8.8.8.8") and not is_external("::ffff:192.168.1.1")
    if sys.platform == "win32":
        assert isinstance(tcp_conns(), list)               # 真的连接表读得出来
        assert pid_path(os.getpid()).lower().endswith(".exe")
    if sys.platform == "win32":
        # 连接表的行不能指着已经释放的缓冲区 (以前每隔几分钟崩溃一次): 反复取表, 搅动堆之后再读每一行
        import gc
        for _ in range(300):
            rows = _table(AF_INET, _Row4)
            junk = [bytearray(400_000) for _ in range(6)]
            del junk
            gc.collect()
            assert all(isinstance(r.pid, int) and r.local_port >= 0 for r in rows)
    return "bypassguard selftest OK"


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        print(_selftest())
    else:
        bg = BypassGuard()
        groups, up = bg.find()
        print("upstream proxy programs:", sorted(up) or "none")
        for p, cs in sorted(groups.items()):
            print(f"{p}: {len(cs)} direct connection(s), e.g. {cs[0].raddr}:{cs[0].rport}")
        if not groups:
            print("no direct connections from programs that bypass the system proxy")
