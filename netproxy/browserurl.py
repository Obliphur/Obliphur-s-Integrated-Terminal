"""
浏览器地址栏 (Windows UI Automation, 只用 ctypes)
────────────────────────────────────────────────
代理不解密 HTTPS, 只看得到域名. 要按 UP 主放行 / 拦截 (MECHANICS 4.8), 得知道浏览器此刻开着哪个页面:
这里读浏览器窗口地址栏里的网址.

· 只读两种浏览器窗口的地址栏: 标题里带"哔哩哔哩 / bilibili"的, 和此刻在最前面的 (刚在地址栏里输入网址按下回车时, 标题还是旧页面的);
  读到的不是哔哩哔哩的网址就丢掉, 不记, 不写文件;
· 每个窗口读它当前标签页的地址 (后台标签页读不到);
· 读不出来 (最小化, 地址栏没找到, 不是这里认识的浏览器) 记为 None, 调用方当作"认不出来".

COM 对象只在扫描线程里用 (线程里 CoInitializeEx 过), 扫描由 Watcher 在专门的线程里做.
"""

import ctypes
import re
import sys
import threading
import time
from ctypes import wintypes

# 认得地址栏的浏览器窗口: Chromium 系 (Chrome, Edge, Brave, Vivaldi …) 的地址栏类名是 OmniboxViewViews,
# Firefox 的是 AutomationId urlbar-input
BROWSER_EXES = {
    "chrome.exe", "msedge.exe", "brave.exe", "vivaldi.exe", "opera.exe", "chromium.exe", "firefox.exe",
    "librewolf.exe", "waterfox.exe", "floorp.exe", "arc.exe", "yandex.exe",
}
CHROMIUM_CLASS = "Chrome_WidgetWin_1"
FIREFOX_CLASS = "MozillaWindowClass"
TITLE_HINTS = ("哔哩哔哩", "bilibili")

_UIA_CLASSNAME = 30012
_UIA_AUTOMATIONID = 30011
_UIA_VALUE_PATTERN = 10002
_SCOPE_DESCENDANTS = 4
_VT_BSTR = 8
_NOT_FOUND_RETRY = 30.0           # 某个窗口里找不到地址栏时, 隔多久再找


def looks_bilibili(title) -> bool:
    t = str(title or "").lower()
    return any(h in t for h in TITLE_HINTS)


def is_bilibili_url(url) -> bool:
    """地址栏里的内容是不是哔哩哔哩的网址 (bilibili.com 和它的子域名; 地址栏常常省略 https://)."""
    t = re.sub(r'^[a-z][a-z0-9+.\-]*://', '', str(url or "").strip(), flags=re.I)
    host = re.split(r'[/?#]', t, maxsplit=1)[0].split('@')[-1].split(':')[0].strip('.').lower()
    return host == "bilibili.com" or host.endswith(".bilibili.com")


def _foreground_hwnd():
    if sys.platform != "win32":
        return 0
    try:
        user32 = ctypes.WinDLL("user32")
        user32.GetForegroundWindow.restype = wintypes.HWND
        return user32.GetForegroundWindow() or 0
    except Exception:
        return 0


class _VARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                ("r3", ctypes.c_ushort), ("val", ctypes.c_void_p), ("pad", ctypes.c_void_p)]


class _Uia:
    """IUIAutomation 的一小部分 (按虚表序号直接调用), 线程内使用."""

    def __init__(self):
        self.ole32 = ctypes.WinDLL("ole32")
        self.oleaut32 = ctypes.WinDLL("oleaut32")
        self.oleaut32.SysAllocString.restype = ctypes.c_void_p
        self.oleaut32.SysAllocString.argtypes = [ctypes.c_wchar_p]
        self.oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
        self.ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        self.ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                                ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        self.ole32.CoInitializeEx(None, 0)                       # COINIT_MULTITHREADED
        clsid, iid = (ctypes.c_byte * 16)(), (ctypes.c_byte * 16)()
        self.ole32.CLSIDFromString("{FF48DBA4-60EF-4201-AA87-54103EEF594E}", ctypes.byref(clsid))
        self.ole32.CLSIDFromString("{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}", ctypes.byref(iid))
        ptr = ctypes.c_void_p()
        if self.ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid),
                                       ctypes.byref(ptr)) != 0 or not ptr.value:
            raise OSError("IUIAutomation unavailable")
        self.ptr = ptr
        self._conds = {}

    @staticmethod
    def _fn(obj, index, *argtypes):
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[index])

    def release(self, obj):
        if obj and getattr(obj, "value", obj):
            try:
                self._fn(obj, 2)(obj)
            except Exception:
                pass

    def element_from_handle(self, hwnd):
        out = ctypes.c_void_p()
        if self._fn(self.ptr, 6, wintypes.HWND, ctypes.POINTER(ctypes.c_void_p))(
                self.ptr, hwnd, ctypes.byref(out)) != 0 or not out.value:
            return None
        return out

    def _condition(self, prop: int, text: str):
        key = (prop, text)
        if key not in self._conds:
            bstr = self.oleaut32.SysAllocString(text)
            var = _VARIANT(_VT_BSTR, 0, 0, 0, bstr, 0)
            out = ctypes.c_void_p()
            hr = self._fn(self.ptr, 23, ctypes.c_int, _VARIANT, ctypes.POINTER(ctypes.c_void_p))(
                self.ptr, prop, var, ctypes.byref(out))
            self.oleaut32.SysFreeString(bstr)
            if hr != 0 or not out.value:
                raise OSError("CreatePropertyCondition failed")
            self._conds[key] = out
        return self._conds[key]

    def find_first(self, root, prop: int, text: str):
        cond = self._condition(prop, text)
        out = ctypes.c_void_p()
        if self._fn(root, 5, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                root, _SCOPE_DESCENDANTS, cond, ctypes.byref(out)) != 0 or not out.value:
            return None
        return out

    def value(self, element):
        pat = ctypes.c_void_p()
        if self._fn(element, 16, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
                element, _UIA_VALUE_PATTERN, ctypes.byref(pat)) != 0 or not pat.value:
            return None
        try:
            bstr = ctypes.c_void_p()
            if self._fn(pat, 4, ctypes.POINTER(ctypes.c_void_p))(pat, ctypes.byref(bstr)) != 0:
                return None
            try:
                return ctypes.wstring_at(bstr.value) if bstr.value else ""
            finally:
                if bstr.value:
                    self.oleaut32.SysFreeString(bstr.value)
        finally:
            self.release(pat)

    def close(self):
        for c in self._conds.values():
            self.release(c)
        self._conds.clear()
        self.release(self.ptr)
        self.ptr = None


class AddressBars:
    """窗口 → 地址栏的 UIA 元素 (缓存着, 元素失效了再找); 扫描线程里用."""

    def __init__(self):
        self.uia = None
        self.failed = False
        self._els = {}                 # hwnd -> 元素指针
        self._missing = {}             # hwnd -> 上次没找到的时刻

    def read(self, hwnd: int, firefox: bool = False):
        if self.failed:
            return None
        try:
            if self.uia is None:
                self.uia = _Uia()
            el = self._els.get(hwnd)
            if el is not None:
                v = self.uia.value(el)
                if v is not None:
                    return v
                self.uia.release(self._els.pop(hwnd))
            now = time.monotonic()
            if now - self._missing.get(hwnd, -1e9) < _NOT_FOUND_RETRY:
                return None
            root = self.uia.element_from_handle(hwnd)
            if root is None:
                return None
            try:
                if firefox:
                    el = self.uia.find_first(root, _UIA_AUTOMATIONID, "urlbar-input")
                else:
                    el = self.uia.find_first(root, _UIA_CLASSNAME, "OmniboxViewViews")
            finally:
                self.uia.release(root)
            if el is None:
                self._missing[hwnd] = now
                return None
            self._missing.pop(hwnd, None)
            self._els[hwnd] = el
            return self.uia.value(el)
        except Exception:
            self.failed = True         # UIA 用不了 (没有接口 / 虚表对不上): 以后都当认不出来, 回到整站名单
            return None

    def forget_except(self, alive: set):
        if self.uia is None:
            return
        for h in [h for h in self._els if h not in alive]:
            self.uia.release(self._els.pop(h))
        for h in [h for h in self._missing if h not in alive]:
            self._missing.pop(h, None)

    def close(self):
        if self.uia is not None:
            for el in self._els.values():
                self.uia.release(el)
            self._els.clear()
            self.uia.close()
            self.uia = None


def scan(bars: AddressBars, windows=None, exe_of=None, foreground=None) -> dict:
    """浏览器里开着的哔哩哔哩页面: {exe 名: [地址栏网址 或 None, …]} (每个窗口一项; None = 标题像哔哩哔哩但读不到地址).
    只看可见的浏览器窗口: 标题带 哔哩哔哩 / bilibili 的, 以及此刻在最前面的; 地址不是哔哩哔哩的丢掉.
    windows / exe_of / foreground 留给自检用假数据."""
    if windows is None or exe_of is None:
        import window_guard
        windows = window_guard.list_top_windows() if windows is None else windows
        exe_of = exe_of or window_guard.exe_name
    wins = windows
    fg = _foreground_hwnd() if foreground is None else foreground
    out, alive = {}, set()
    for w in wins:
        if not w.get("visible") or w.get("cloaked"):
            continue
        titled = looks_bilibili(w.get("title"))
        if not titled and not (fg and w.get("hwnd") == fg):
            continue
        cls = w.get("cls")
        if cls not in (CHROMIUM_CLASS, FIREFOX_CLASS):
            continue
        exe = str(exe_of(w.get("pid") or 0) or "").lower()
        if not exe:
            continue
        hwnd = w.get("hwnd")
        url = None
        if exe in BROWSER_EXES:
            alive.add(hwnd)
            url = bars.read(hwnd, firefox=(cls == FIREFOX_CLASS))
            url = url.strip() if isinstance(url, str) and url.strip() else None
        if url:
            if not is_bilibili_url(url):
                continue                   # 别的网站 (标题里碰巧有 bilibili, 或者只是最前面的窗口)
        elif not titled:
            continue                       # 最前面的窗口, 读不到地址, 标题也不像: 没有证据, 不算
        out.setdefault(exe, []).append(url)
    bars.forget_except(alive)
    return out


class Watcher:
    """需要时才扫描: snapshot() 的结果超过 max_age 秒就叫扫描线程扫一遍, 等它扫完再返回 (单飞, 并发的调用共用一次扫描).
    没有人要的时候不扫, 也不碰任何窗口. 扫描卡住 (浏览器无响应) 时 snapshot() 最多等 wait 秒, 超过 stale 秒的结果不算数."""

    def __init__(self, scan_fn=None, max_age: float = 0.6, stale: float = 4.0):
        self._scan_fn = scan_fn
        self.max_age = max_age
        self.stale = stale
        self._cv = threading.Condition()
        self._snap = {}
        self._ts = -1e9
        self._gen = 0
        self._want = False
        self._thread = None

    def _run(self):
        bars = AddressBars() if self._scan_fn is None else None
        fn = self._scan_fn or (lambda: scan(bars))
        try:
            while True:
                with self._cv:
                    if not self._cv.wait_for(lambda: self._want, timeout=60.0):
                        self._thread = None          # 一分钟没人要: 线程退出, 放掉地址栏元素
                        return
                try:
                    snap = fn()
                except Exception:
                    snap = {}
                with self._cv:
                    self._snap = snap
                    self._ts = time.monotonic()
                    self._gen += 1
                    self._want = False
                    self._cv.notify_all()
        finally:
            if bars is not None:
                bars.close()

    def snapshot(self, wait: float = 1.5):
        """(扫描结果, 够新吗). 够新 = 不超过 stale 秒."""
        with self._cv:
            if time.monotonic() - self._ts > self.max_age:
                gen = self._gen
                self._want = True
                if self._thread is None or not self._thread.is_alive():
                    self._thread = threading.Thread(target=self._run, daemon=True, name="oit-browser-urls")
                    self._thread.start()
                self._cv.notify_all()
                self._cv.wait_for(lambda: self._gen != gen, timeout=wait)
            return self._snap, (time.monotonic() - self._ts) <= self.stale


def _selftest():
    assert looks_bilibili("某视频_哔哩哔哩_bilibili - Google Chrome") and looks_bilibili("space.BILIBILI.com/1")
    assert not looks_bilibili("GitHub - Google Chrome") and not looks_bilibili(None)

    class Bars:
        def __init__(self):
            self.urls = {1: "https://space.bilibili.com/2742391", 2: "  ", 3: None}
            self.kept = None

        def read(self, hwnd, firefox=False):
            return self.urls.get(hwnd)

        def forget_except(self, alive):
            self.kept = set(alive)

    def win(hwnd, title="x_哔哩哔哩_bilibili", pid=10, cls=CHROMIUM_CLASS, visible=True, cloaked=False):
        return {"hwnd": hwnd, "title": title, "pid": pid, "cls": cls, "visible": visible, "cloaked": cloaked}

    exes = {10: "MSEdge.exe", 11: "code.exe", 12: "firefox.exe"}
    bars = Bars()
    wins = [win(1), win(2), win(3), win(4, title="GitHub"), win(5, visible=False),
            win(6, cloaked=True), win(7, pid=11), win(8, pid=12, cls="Other"),
            win(1, pid=12, cls=FIREFOX_CLASS)]
    got = scan(bars, wins, exes.get, foreground=0)
    assert got == {"msedge.exe": ["https://space.bilibili.com/2742391", None, None],
                   "code.exe": [None], "firefox.exe": ["https://space.bilibili.com/2742391"]}, got
    assert bars.kept == {1, 2, 3}
    # 最前面的窗口标题还是旧页面, 但地址栏里已经是哔哩哔哩的网址 (刚输入网址按下回车)
    bars.urls = {4: "https://www.bilibili.com/video/BV1GJ411x7h7", 9: "https://github.com/x", 3: "https://www.google.com/search?q=bilibili"}
    wins = [win(3), win(4, title="新标签页"), win(9, title="GitHub")]
    assert scan(bars, wins, exes.get, foreground=4) == {"msedge.exe": ["https://www.bilibili.com/video/BV1GJ411x7h7"]}
    assert scan(bars, wins, exes.get, foreground=9) == {}, "最前面的是别的网站: 丢掉; 标题像哔哩哔哩但地址是谷歌搜索: 也丢掉"
    bars.urls = {4: None}
    assert scan(bars, [win(4, title="新标签页")], exes.get, foreground=4) == {}, "最前面的窗口读不到地址, 标题也不像: 不算"
    for yes in ("space.bilibili.com/1", "HTTPS://www.Bilibili.com:443/x", "bilibili.com"):
        assert is_bilibili_url(yes), yes
    for no in ("notbilibili.com/x", "bilibili 官网", "https://google.com/?q=bilibili.com", "bilibili.com.evil.com", None):
        assert not is_bilibili_url(no), no

    calls = []
    w = Watcher(lambda: calls.append(1) or {"chrome.exe": ["u"]}, max_age=0.05, stale=0.5)
    snap, fresh = w.snapshot()
    assert snap == {"chrome.exe": ["u"]} and fresh and len(calls) == 1
    snap, fresh = w.snapshot()
    assert len(calls) == 1, "够新的结果直接用"
    time.sleep(0.08)
    w.snapshot()
    assert len(calls) == 2
    res = []
    ts = [threading.Thread(target=lambda: res.append(w.snapshot())) for _ in range(6)]
    time.sleep(0.08)
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(calls) == 3 and all(r[0] == {"chrome.exe": ["u"]} for r in res), "并发的调用共用一次扫描"
    slow = Watcher(lambda: (time.sleep(0.3), {})[1], max_age=0.01, stale=0.05)
    t0 = time.monotonic()
    snap, fresh = slow.snapshot(wait=0.05)
    assert time.monotonic() - t0 < 0.25 and not fresh, "扫描卡住时最多等 wait 秒, 旧结果不算数"
    return "netproxy.browserurl selftest OK"


if __name__ == "__main__":
    print(_selftest())
    if sys.platform == "win32" and "--live" in sys.argv:
        b = AddressBars()
        t0 = time.monotonic()
        print(scan(b), f"{(time.monotonic() - t0) * 1000:.0f} ms")
        t0 = time.monotonic()
        print(scan(b), f"{(time.monotonic() - t0) * 1000:.0f} ms (cached)")
        b.close()
