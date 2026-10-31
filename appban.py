"""
合约的网络禁令: Windows 防火墙出站规则 (MECHANICS 18.3)
──────────────────────────────────────────────────────
乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒 的软件合约期间禁止联网.
过滤代理按 exe 名拦 (netproxy/rules.py 的 banned), 但只管得住走系统代理的程序 (Steam / 游戏多半不走).
所以另外给正在运行的这些程序 (按完整路径) 加 Windows 防火墙的出站阻止规则:
    OIT contract ban     dir=out action=block program=<路径>      (每个程序一条, 名字相同)
OIT 本来就以管理员身份运行. 禁令解除 / 合约结束时删掉; 程序启动时先删掉上次留下的 (例如崩溃时没来得及删).
已经建立的连接防火墙不会切断, 新的连接才会被拦.

    python appban.py          列出现在有几条规则 (只读)
"""

import ctypes
import subprocess
import sys

NO_WINDOW = 0x08000000
RULE = "OIT contract ban"


def _run(args, timeout: float = 30):
    if sys.platform != "win32":
        return 0, ""
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        out = (r.stdout or b"") + (r.stderr or b"")
        try:
            return r.returncode, out.decode("utf-8").strip()
        except UnicodeDecodeError:
            return r.returncode, out.decode("mbcs", "replace").strip()
    except Exception as e:
        return -1, str(e)


def _netsh(*args):
    return ["netsh", "advfirewall", "firewall", *args]


def process_paths(names) -> set:
    """正在运行的、exe 名 (小写) 在 names 里的程序的完整路径."""
    if sys.platform != "win32" or not names:
        return set()
    import window_guard
    names = {n.lower() for n in names}
    out = set()
    k32 = ctypes.windll.kernel32
    for pid, _ppid, exe in window_guard.list_processes_full():
        if exe not in names:
            continue
        h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(len(buf))
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                out.add(buf.value)
        finally:
            k32.CloseHandle(h)
    return out


class AppBan:
    """记着已经加了规则的程序路径; sync() 把规则调整成正好是 paths (在后台线程里调用)."""

    def __init__(self, run=_run, rule: str = RULE):
        self.run = run
        self.rule = rule
        self.paths = set()

    def clear_all(self):
        """删掉所有这种规则 (程序启动时 / 合约结束时)."""
        self.run(_netsh("delete", "rule", f"name={self.rule}"))
        self.paths = set()

    def sync(self, paths) -> set:
        paths = {p for p in (paths or ()) if p}
        if not paths and self.paths:
            self.clear_all()
            return set()
        for p in sorted(paths - self.paths):
            rc, _ = self.run(_netsh("add", "rule", f"name={self.rule}", "dir=out", "action=block",
                                    f"program={p}", "enable=yes", "profile=any"))
            if rc == 0:
                self.paths.add(p)
        for p in sorted(self.paths - paths):
            self.run(_netsh("delete", "rule", f"name={self.rule}", f"program={p}"))
            self.paths.discard(p)
        return set(self.paths)


def _selftest():
    calls = []

    def fake(args, timeout=30):
        calls.append(args)
        return 0, "Ok."
    b = AppBan(fake)
    assert b.sync({r"C:\Steam\steam.exe", r"C:\WeChat\WeChat.exe"}) == {r"C:\Steam\steam.exe", r"C:\WeChat\WeChat.exe"}
    adds = [c for c in calls if c[3] == "add"]
    assert len(adds) == 2 and all("dir=out" in c and "action=block" in c and f"name={RULE}" in c for c in adds)
    calls.clear()
    assert b.sync({r"C:\Steam\steam.exe"}) == {r"C:\Steam\steam.exe"}
    assert calls == [_netsh("delete", "rule", f"name={RULE}", r"program=C:\WeChat\WeChat.exe")], calls
    calls.clear()
    assert b.sync({r"C:\Steam\steam.exe"}) and calls == [], "没有变化就不动"
    assert b.sync(set()) == set() and calls == [_netsh("delete", "rule", f"name={RULE}")]
    return "appban selftest OK"


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        print(_selftest())
    else:
        rc, text = _run(_netsh("show", "rule", f"name={RULE}"))
        print(f"{RULE}: " + ("none" if rc != 0 else f"{text.count('Program') or text.count('程序')} rule(s)"))
