"""
Windows 防火墙: 设备同步的入站端口 (MECHANICS.md 15.3)
──────────────────────────────────────────────────────
设备同步要别的设备连进来: TCP 17930 ~ 17939 (同步), UDP 17931 (广播找设备).
Windows 默认拦截入站连接, 只在程序第一次监听时弹一次"是否允许 Python 访问网络";
那个窗口被关掉 / 当时连的是"公用网络" / 程序没弹窗 (以管理员身份运行时常见) 时,
本机能连别人, 别人却连不上本机 —— 找不到这台电脑, 本机是子端时母端的同步请求也进不来.

OIT 本来就以管理员身份运行, 启动时在后台补上两条只放行局域网 (remoteip=localsubnet) 的入站规则:
    OIT device sync (TCP)   TCP 17930-17939
    OIT device sync (UDP)   UDP 17931
已经有就不动. 卸载时删掉 (uninstall.py).

"阻止"规则优先于"允许"规则: 用户点过"取消"时 Windows 会给 python.exe / pythonw.exe 建阻止规则.
那些规则也管着别的 Python 程序, 这里不删, 只把名字找出来显示在 Rules 页 (blockers()).

    python firewall.py      列出两条规则在不在, 以及挡住本机 Python 的阻止规则 (只读)
"""

import os
import subprocess
import sys

NO_WINDOW = 0x08000000
RULES = (
    ("OIT device sync (TCP)", "TCP", "17930-17939"),
    ("OIT device sync (UDP)", "UDP", "17931"),
    ("OIT link (TCP)", "TCP", "17940"),         # 联动程序 (linkd.py, MECHANICS 15.8)
)


def _run(args, timeout: float = 30):
    """运行一条命令, 返回 (退出码, 输出). 非 Windows 什么都不做."""
    if sys.platform != "win32":
        return 0, ""
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        out = (r.stdout or b"") + (r.stderr or b"")
        return r.returncode, out.decode("utf-8", "replace").strip()
    except Exception as e:
        return -1, str(e)


def _netsh(*args):
    return ["netsh", "advfirewall", "firewall", *args]


def has_rule(name: str, run=_run) -> bool:
    # 规则不存在时 netsh 返回 1 (输出是本地化的文字, 不看它)
    return run(_netsh("show", "rule", f"name={name}"))[0] == 0


def ensure(run=_run) -> dict:
    """补上缺的规则. 返回 {"added": [名字], "error": 文字}. 需要管理员权限."""
    out = {"added": [], "error": ""}
    if sys.platform != "win32":
        return out
    for name, proto, ports in RULES:
        if has_rule(name, run):
            continue
        rc, text = run(_netsh("add", "rule", f"name={name}", "dir=in", "action=allow",
                              f"protocol={proto}", f"localport={ports}",
                              "remoteip=localsubnet", "profile=any", "enable=yes"))
        if rc == 0:
            out["added"].append(name)
        else:
            out["error"] = text or f"netsh exit {rc}"
    return out


def remove(run=_run) -> list:
    """卸载: 删掉 OIT 加的规则, 返回删不掉的规则名."""
    left = []
    if sys.platform != "win32":
        return left
    for name, _proto, _ports in RULES:
        if has_rule(name, run) and run(_netsh("delete", "rule", f"name={name}"))[0] != 0:
            left.append(name)
    return left


def interpreters() -> list:
    """运行 OIT 的 Python (python.exe 和同目录的 pythonw.exe), 小写完整路径."""
    exe = os.path.abspath(sys.executable)
    d = os.path.dirname(exe)
    out = {exe.lower()}
    for n in ("python.exe", "pythonw.exe"):
        p = os.path.join(d, n)
        if os.path.exists(p):
            out.add(p.lower())
    return sorted(out)


def blockers(run=_run) -> list:
    """启用中的、挡住本机 Python 入站连接的阻止规则的名字."""
    if sys.platform != "win32":
        return []
    exes = ",".join("'" + p.replace("'", "''") + "'" for p in interpreters())
    cmd = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
           f"$exes = @({exes}); "
           "Get-NetFirewallApplicationFilter | Where-Object { "
           "$exes -contains ([Environment]::ExpandEnvironmentVariables([string]$_.Program)).ToLower() } | "
           "Get-NetFirewallRule | Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' "
           "-and $_.Action -eq 'Block' } | ForEach-Object { $_.DisplayName }")
    rc, text = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], timeout=60)
    if rc != 0:
        return []
    return sorted({ln.strip() for ln in text.splitlines() if ln.strip()})


def check(run=_run) -> dict:
    """启动时在后台调用: 补规则, 再找阻止规则. {"added", "error", "blockers"}."""
    out = ensure(run)
    out["blockers"] = blockers(run)
    return out


def _selftest():
    calls = []
    present = set()

    def fake(args, timeout=30):
        calls.append(args)
        if args[:1] == ["powershell"]:
            return 0, "Python\r\npythonw\r\n\r\n"
        verb = args[3]
        name = next(a[5:] for a in args if a.startswith("name="))
        if verb == "show":
            return (0, "") if name in present else (1, "")
        if verb == "add":
            present.add(name)
            return 0, ""
        if verb == "delete":
            present.discard(name)
            return 0, ""
        return 1, ""

    if sys.platform == "win32":
        r = check(fake)
        assert r["added"] == [n for n, _p, _q in RULES] and not r["error"], r
        assert r["blockers"] == ["Python", "pythonw"], r
        add = [a for a in calls if a[3] == "add"]
        assert all("remoteip=localsubnet" in a and "dir=in" in a for a in add)
        assert any("localport=17930-17939" in a and "protocol=TCP" in a for a in add)
        calls.clear()
        assert ensure(fake)["added"] == [] and not any(a[3] == "add" for a in calls), "已有规则不重复添加"
        assert remove(fake) == [] and not present
    else:
        assert check(fake) == {"added": [], "error": "", "blockers": []}
    return "firewall selftest OK"


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        print(_selftest())
    else:
        for n, _p, _q in RULES:
            print(f"{n}: {'present' if has_rule(n) else 'missing'}")
        print("blocking rules:", blockers() or "none")
