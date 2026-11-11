"""
Windows 系统代理的安装 / 复原
────────────────────────────
· install():把「当前用户」的系统代理指向本地过滤代理,同时把原来的设置备份到
  sysproxy_backup.json(含 PAC 地址).
· restore():按备份还原.程序崩溃后下次启动仍能还原,因为备份在磁盘上.
· original_upstream():如果用户原来就在用 HTTP 代理(Clash / v2ray 之类),
  过滤代理放行的流量会继续转给它,不会因为装了本模式而翻不了墙.
· 非 Windows 平台使用空后端,什么都不做.
"""

import json
import os
import re
import sys

KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
FIELDS = ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL")
OUR_OVERRIDE = "localhost;127.*;[::1]"


class NullBackend:
    def read(self) -> dict:
        return {}

    def write(self, state: dict):
        pass

    def notify(self):
        pass


class MemoryBackend:
    """测试用:把「注册表」放在内存里."""

    def __init__(self, state=None):
        self.state = dict(state or {})
        self.notified = 0

    def read(self) -> dict:
        return {k: v for k, v in self.state.items() if v is not None}

    def write(self, state: dict):
        for k in FIELDS:
            v = state.get(k)
            if v is None:
                self.state.pop(k, None)
            else:
                self.state[k] = v

    def notify(self):
        self.notified += 1


class RegistryBackend:

    def read(self) -> dict:
        import winreg
        out = {}
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_READ) as k:
                for name in FIELDS:
                    try:
                        out[name] = winreg.QueryValueEx(k, name)[0]
                    except OSError:
                        pass
        except OSError:
            pass
        return out

    def write(self, state: dict):
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
            for name in FIELDS:
                v = state.get(name)
                if v is None:
                    try:
                        winreg.DeleteValue(k, name)
                    except OSError:
                        pass
                elif name == "ProxyEnable":
                    winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(v))
                else:
                    winreg.SetValueEx(k, name, 0, winreg.REG_SZ, str(v))

    def notify(self):
        import ctypes
        try:
            wininet = ctypes.WinDLL("wininet")
            wininet.InternetSetOptionW(None, 39, None, 0)    # SETTINGS_CHANGED
            wininet.InternetSetOptionW(None, 37, None, 0)    # REFRESH
        except Exception:
            pass


def parse_proxy_server(text, exclude=None):
    """'host:port' 或 'http=h:p;https=h:p;socks=…' → (host, port) 或 None.只认 HTTP 类代理."""
    text = str(text or "").strip()
    if not text:
        return None
    chosen = ""
    if "=" in text:
        table = {}
        for part in text.split(";"):
            if "=" in part:
                scheme, _, addr = part.partition("=")
                table[scheme.strip().lower()] = addr.strip()
        chosen = table.get("https") or table.get("http") or ""
    else:
        chosen = text
    chosen = re.sub(r'^[a-z][a-z0-9+.\-]*://', '', chosen)
    host, _, port = chosen.rpartition(":")
    if not host or not port.isdigit():
        return None
    result = (host.strip("[]"), int(port))
    if exclude and result == tuple(exclude):
        return None
    return result


class SystemProxy:

    def __init__(self, backup_path: str, backend=None):
        self.backup_path = backup_path
        if backend is None:
            backend = RegistryBackend() if sys.platform == "win32" else NullBackend()
        self.backend = backend
        self.ours = None

    @staticmethod
    def target(host: str, port: int) -> dict:
        return {"ProxyEnable": 1, "ProxyServer": f"{host}:{port}",
                "ProxyOverride": OUR_OVERRIDE, "AutoConfigURL": None}

    def _load_backup(self):
        try:
            with open(self.backup_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else None
        except Exception:
            return None

    def _save_backup(self, ours: str, orig: dict):
        tmp = self.backup_path + ".tmp"
        d = os.path.dirname(self.backup_path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ours": ours, "orig": orig}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.backup_path)

    @staticmethod
    def _is_ours(cur: dict, ours_server: str) -> bool:
        return cur.get("ProxyEnable") == 1 and cur.get("ProxyServer") == ours_server

    def is_installed(self, host: str, port: int) -> bool:
        cur = self.backend.read()
        want = self.target(host, port)
        return all(cur.get(k) == want[k] for k in FIELDS) or (
            self._is_ours(cur, want["ProxyServer"]) and not cur.get("AutoConfigURL")
            and cur.get("ProxyOverride") == OUR_OVERRIDE)

    def install(self, host: str, port: int):
        """幂等:已经是我们的设置就什么都不做;被改过就改回来(同时更新备份)."""
        want = self.target(host, port)
        self.ours = want["ProxyServer"]
        cur = self.backend.read()
        backup = self._load_backup()
        known_ours = {want["ProxyServer"]}
        if backup:
            known_ours.add(backup.get("ours"))
        if cur.get("ProxyServer") not in known_ours:
            self._save_backup(want["ProxyServer"], {k: cur.get(k) for k in FIELDS})
        elif backup is None:
            self._save_backup(want["ProxyServer"], {k: None for k in FIELDS})
        elif backup.get("ours") != want["ProxyServer"]:
            self._save_backup(want["ProxyServer"], backup.get("orig") or {})
        if not self.is_installed(host, port):
            self.backend.write(want)
            self.backend.notify()

    def restore(self):
        backup = self._load_backup()
        cur = self.backend.read()
        if backup is not None:
            orig = backup.get("orig") or {}
            if orig.get("ProxyServer") == backup.get("ours"):
                orig = {k: None for k in FIELDS}
            self.backend.write({k: orig.get(k) for k in FIELDS})
            self.backend.notify()
            try:
                os.remove(self.backup_path)
            except OSError:
                pass
        elif self.ours and self._is_ours(cur, self.ours):
            self.backend.write({"ProxyEnable": 0, "ProxyServer": None,
                                "ProxyOverride": None, "AutoConfigURL": None})
            self.backend.notify()

    def original_upstream(self):
        backup = self._load_backup()
        if not backup:
            return None
        orig = backup.get("orig") or {}
        if orig.get("ProxyEnable") != 1:
            return None
        return parse_proxy_server(orig.get("ProxyServer"), exclude=None)


def _selftest():
    import tempfile
    assert parse_proxy_server("127.0.0.1:7890") == ("127.0.0.1", 7890)
    assert parse_proxy_server("http=a:1;https=b:2;socks=c:3") == ("b", 2)
    assert parse_proxy_server("socks=c:3") is None
    assert parse_proxy_server("http://p.example:8080") == ("p.example", 8080)
    assert parse_proxy_server("") is None and parse_proxy_server("nonsense") is None

    tmp = tempfile.mkdtemp()
    bk = os.path.join(tmp, "b.json")

    # ── 原来没有代理:安装 → 还原回「没有代理」
    be = MemoryBackend({"ProxyEnable": 0})
    sp = SystemProxy(bk, be)
    sp.install("127.0.0.1", 17890)
    assert be.state["ProxyServer"] == "127.0.0.1:17890" and be.state["ProxyEnable"] == 1
    n = be.notified
    sp.install("127.0.0.1", 17890)
    assert be.notified == n, "重复安装应当什么都不做"
    sp.restore()
    assert be.state.get("ProxyEnable") == 0 and "ProxyServer" not in be.state
    assert not os.path.exists(bk)

    # ── 原来有 Clash + PAC:安装后可链到 Clash,还原后原样回来
    be = MemoryBackend({"ProxyEnable": 1, "ProxyServer": "127.0.0.1:7890",
                        "ProxyOverride": "<local>", "AutoConfigURL": "http://x/pac"})
    sp = SystemProxy(bk, be)
    sp.install("127.0.0.1", 17890)
    assert "AutoConfigURL" not in be.state
    assert sp.original_upstream() == ("127.0.0.1", 7890)
    be.state["ProxyEnable"] = 0                       # 被人关掉 → 重新装回
    sp.install("127.0.0.1", 17890)
    assert be.state["ProxyEnable"] == 1 and sp.original_upstream() == ("127.0.0.1", 7890)

    # ── 崩溃后重启:新实例,备份还在,不能把「我们自己的设置」当成原设置
    sp2 = SystemProxy(bk, be)
    sp2.install("127.0.0.1", 17890)
    assert sp2.original_upstream() == ("127.0.0.1", 7890)
    sp2.restore()
    assert be.state == {"ProxyEnable": 1, "ProxyServer": "127.0.0.1:7890",
                        "ProxyOverride": "<local>", "AutoConfigURL": "http://x/pac"}, be.state

    # ── 端口变了(端口被占用回退)也只更新 ours,不丢原设置
    sp3 = SystemProxy(bk, be)
    sp3.install("127.0.0.1", 17890)
    sp3.install("127.0.0.1", 17891)
    assert be.state["ProxyServer"] == "127.0.0.1:17891"
    assert sp3.original_upstream() == ("127.0.0.1", 7890)
    sp3.restore()
    assert be.state["ProxyServer"] == "127.0.0.1:7890"

    # ── 备份丢了但设置还是我们的:还原成「关闭」
    be = MemoryBackend({"ProxyEnable": 0})
    sp4 = SystemProxy(bk, be)
    sp4.install("127.0.0.1", 17890)
    os.remove(bk)
    sp4.restore()
    assert be.state.get("ProxyEnable") == 0
    return "netproxy.sysproxy selftest OK"


if __name__ == "__main__":
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    import tempstore
    tempstore.autosession()      # 自检的临时文件进 %TEMP%\OIT 的会话目录, 退出即删 (tempstore.py)
    print(_selftest())
