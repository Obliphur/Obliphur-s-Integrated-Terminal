"""
ProxyController:把规则库,过滤代理,系统代理拼成一个对象,NetGuard 只和它打交道.

    start()         启动过滤代理并把系统代理指过来(幂等,同时纠正被改回去的设置)
    set_online(b)   False = 离线(按名单过滤);True = 联网(全部放行)
    stop(restore)   关掉代理,并还原系统代理
"""

import os
import threading
import time
from collections import deque

from .browserurl import Watcher
from .rules import RuleStore, MODE_BLACKLIST, DEFAULT_TEMPLATE
from .server import FilterProxy, DEFAULT_PORT
from .sysproxy import SystemProxy
from .upgate import UpGate, UpResolver


class ProxyController:

    def __init__(self, data_dir: str, port: int = DEFAULT_PORT, rules: RuleStore = None,
                 sysproxy: SystemProxy = None, mode_getter=None, dev_getter=None):
        self.rules = rules or RuleStore(os.path.join(data_dir, "net_rules.json"),
                                         template=DEFAULT_TEMPLATE)
        self.sys = sysproxy or SystemProxy(os.path.join(data_dir, "sysproxy_backup.json"))
        if dev_getter:
            self.rules.dev_getter = dev_getter
        # 按 UP 主区分 (MECHANICS 4.8): 浏览器此刻开着哪个页面, 由地址栏告诉规则库; 没有 UP 主名单时一次都不会去扫
        self.up = UpResolver(names_path=os.path.join(data_dir, "up_names.json"))
        self.rules.up_probe = UpGate(Watcher(), self.up)
        self.preferred_port = port
        self._mode_getter = mode_getter or (lambda: MODE_BLACKLIST)
        self.online = False
        self.allowed = 0
        self.blocked = 0
        self.events = deque(maxlen=200)
        self.event_seq = 0
        self._server = None
        self._lock = threading.RLock()
        self._up_cache = (0.0, None)

    @property
    def running(self) -> bool:
        return bool(self._server and self._server.running)

    @property
    def port(self) -> int:
        return self._server.port if self._server else self.preferred_port


    @property
    def filter_mode(self) -> str:
        return self._mode_getter()

    def _decide(self, host, exe):
        return self.rules.decide(host, exe, self.online, self.filter_mode)

    def _upstream(self):
        now = time.monotonic()
        stamp, val = self._up_cache
        if now - stamp > 5.0:
            try:
                val = self.sys.original_upstream()
            except Exception:
                val = None
            if val and val[1] == self.port and val[0] in ("127.0.0.1", "localhost"):
                val = None
            self._up_cache = (now, val)
        return val

    def _on_event(self, host, exe, allow, why):
        if allow:
            self.allowed += 1
            return
        self.blocked += 1
        last = self.events[-1] if self.events else None
        if last and last["host"] == host and last["exe"] == exe and time.time() - last["ts"] < 2:
            last["count"] += 1
            return
        self.event_seq += 1
        self.events.append({"ts": time.time(), "host": host, "exe": exe,
                            "why": why, "count": 1})


    def start(self):
        with self._lock:
            if not self.running:
                srv = FilterProxy(self._decide, port=self.preferred_port,
                                  upstream=self._upstream, on_event=self._on_event)
                srv.start()
                self._server = srv
            self.sys.install("127.0.0.1", self._server.port)

    def set_online(self, online: bool):
        self.online = bool(online)

    def stop(self, restore: bool = True):
        with self._lock:
            srv, self._server = self._server, None
            if srv is not None:
                srv.stop()
            if restore:
                self.sys.restore()


def _selftest():
    import tempfile
    d = tempfile.mkdtemp()
    from .sysproxy import MemoryBackend
    be = MemoryBackend({"ProxyEnable": 0})
    c = ProxyController(d, port=0, sysproxy=SystemProxy(os.path.join(d, "bk.json"), be))
    c.start()
    assert c.running and be.state["ProxyServer"] == f"127.0.0.1:{c.port}"
    c.start()
    c.stop()
    assert not c.running and be.state.get("ProxyEnable") == 0
    return "netproxy.controller selftest OK"


if __name__ == "__main__":
    print(_selftest())
