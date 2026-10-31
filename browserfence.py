"""
浏览器围栏: 防止绕过 Proxy 断网方式 (MECHANICS 3.2.2)
─────────────────────────────────────────────────────
Proxy / 代理-全屏蔽 断网方式只管得住遵守系统代理的程序. 最常见的绕法是让浏览器自己不走系统代理:
浏览器里设了别的代理 / "不使用代理" (设置, 命令行参数, 插件), 或者别的 VPN / 代理软件把流量接走.

浏览器正常使用时, 所有连接都只连本机的过滤代理 (127.0.0.1). Windows 防火墙不过滤本机回环连接,
所以给浏览器程序加一条"出站阻止"规则, 它连本机的过滤代理照常, 想直接连外面 (不管是设置里改了代理, 插件, 还是 VPN 接走) 一律被挡.

    OIT browser fence     dir=out action=block program=<路径>      (每个浏览器一条, 名字相同)

只在 Proxy / 代理-全屏蔽 方式下生效 (开发者模式, Adapter 方式, 重置 / 卸载时删掉); 程序启动时先删掉上次留下的.
和 appban.py (合约的网络禁令) 用同一套做法, 规则名不同.

    python browserfence.py     列出现在有几条规则 (只读)
"""

import os
import sys

import appban

RULE = "OIT browser fence"

# 常见浏览器的 exe 名 (小写). 不含 msedgewebview2 (别的程序内嵌的网页控件).
BROWSER_EXES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe",
    "iexplore.exe", "chromium.exe", "waterfox.exe", "librewolf.exe", "thorium.exe",
    "360se.exe", "360chrome.exe", "qqbrowser.exe", "sogouexplorer.exe", "2345explorer.exe",
    "ucbrowser.exe", "maxthon.exe",
}

# 没有在运行的浏览器也先拦上 (之后再打开就已经拦着了): 常见的安装位置
_KNOWN = (
    ("%ProgramFiles%", r"Google\Chrome\Application\chrome.exe"),
    ("%ProgramFiles(x86)%", r"Google\Chrome\Application\chrome.exe"),
    ("%LocalAppData%", r"Google\Chrome\Application\chrome.exe"),
    ("%ProgramFiles%", r"Microsoft\Edge\Application\msedge.exe"),
    ("%ProgramFiles(x86)%", r"Microsoft\Edge\Application\msedge.exe"),
    ("%ProgramFiles%", r"Mozilla Firefox\firefox.exe"),
    ("%ProgramFiles(x86)%", r"Mozilla Firefox\firefox.exe"),
    ("%ProgramFiles%", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
    ("%LocalAppData%", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
    ("%LocalAppData%", r"Programs\Opera\opera.exe"),
    ("%LocalAppData%", r"Vivaldi\Application\vivaldi.exe"),
)


def known_paths() -> set:
    out = set()
    for base, rel in _KNOWN:
        root = os.path.expandvars(base)
        if root and not root.startswith("%"):
            path = os.path.join(root, rel)
            if os.path.isfile(path):
                out.add(path)
    return out


def browser_paths() -> set:
    """要围住的浏览器: 正在运行的 (按完整路径) + 常见位置里装着的."""
    if sys.platform != "win32":
        return set()
    return set(appban.process_paths(BROWSER_EXES)) | known_paths()


class BrowserFence(appban.AppBan):
    def __init__(self, run=appban._run):
        super().__init__(run, rule=RULE)


def _selftest():
    calls = []

    def fake(args, timeout=30):
        calls.append(args)
        return 0, "Ok."
    f = BrowserFence(fake)
    assert f.rule == RULE and f.sync({r"C:\a\chrome.exe"}) == {r"C:\a\chrome.exe"}
    assert all(f"name={RULE}" in c for c in calls if c[3] == "add")
    assert f.sync(set()) == set() and calls[-1] == appban._netsh("delete", "rule", f"name={RULE}")
    assert "chrome.exe" in BROWSER_EXES and "msedgewebview2.exe" not in BROWSER_EXES
    return "browserfence selftest OK"


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        print(_selftest())
    else:
        rc, text = appban._run(appban._netsh("show", "rule", f"name={RULE}"))
        print(f"{RULE}: " + ("none" if rc != 0 else f"{text.count('Program') or text.count('程序')} rule(s)"))
