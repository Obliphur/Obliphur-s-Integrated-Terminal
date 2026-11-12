"""HTTPS 拦截提示: 将前台浏览器中被拒绝的页面转到本机提示页, 不解密 TLS."""

import sys
import threading
import time
from urllib.parse import urlsplit

from .browserurl import AddressBars, BROWSER_EXES, CHROMIUM_CLASS, FIREFOX_CLASS

NOTICE_TTL = 30.0


def page_target(value):
    """地址栏常省略协议; 只接收普通 HTTP(S) 网址, 不接收搜索词或内部页面."""
    text = str(value or "").strip()
    if not text or any(c.isspace() for c in text):
        return None
    if "://" not in text:
        text = "https://" + text
    try:
        url = urlsplit(text)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            return None
        host = url.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        if not host or url.port == 0:
            return None
        return host, text
    except (ValueError, UnicodeError):
        return None


class BlockPageWatcher:
    """拦截后短暂查看前台地址栏; 子资源 / 后台请求不抢走当前页面."""

    def __init__(self, decide, make_url):
        self.decide = decide
        self.make_url = make_url
        self._pending = {}
        self._cv = threading.Condition()
        self._thread = None
        self._stopped = False

    def denied(self, host, exe, why):
        if sys.platform != "win32" or exe not in BROWSER_EXES or why == "self":
            return
        with self._cv:
            if self._stopped:
                return
            now = time.monotonic()
            self._pending = {k: v for k, v in self._pending.items() if now - v < NOTICE_TTL}
            if len(self._pending) >= 256:
                self._pending.pop(next(iter(self._pending)))
            self._pending.setdefault((host, exe), now)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True, name="oit-block-page")
                self._thread.start()
            self._cv.notify_all()

    def stop(self):
        with self._cv:
            self._stopped = True
            self._pending.clear()
            self._cv.notify_all()

    def _visit(self, bars, window, exe):
        """窗口 / 地址 / 最新规则三者都匹配才跳转, 保留注入点供自检使用."""
        if not window or not window.get("visible", True) or window.get("cloaked"):
            return False
        cls = window.get("cls")
        if cls not in (CHROMIUM_CLASS, FIREFOX_CLASS) or exe not in BROWSER_EXES:
            return False
        with self._cv:
            if self._stopped or not any(key[1] == exe for key in self._pending):
                return False
        hwnd = window["hwnd"]
        value = bars.read(hwnd, firefox=(cls == FIREFOX_CLASS))
        target = page_target(value)
        if target is None:
            return False
        host, original = target
        key = (host, exe)
        with self._cv:
            stamp = self._pending.get(key)
            alias_stamp = self._pending.get(("www." + host, exe))
        # Chromium 未聚焦的地址栏还会省略 www.; 只有两种主机都被禁用时才推断,
        # 避免一个允许的页面因为它请求了另一个被禁用的主机而被跳走.
        if "://" not in value and not host.startswith("www."):
            if stamp is None and alias_stamp is None:
                return False
            if self.decide(host, exe)[0] or self.decide("www." + host, exe)[0]:
                return False
            if stamp is None:
                host = "www." + host
                key = (host, exe)
                stamp = alias_stamp
                parsed = urlsplit(original)
                original = parsed._replace(netloc="www." + parsed.netloc).geturl()
        with self._cv:
            if self._stopped or stamp is None or not 0.4 <= time.monotonic() - stamp < NOTICE_TTL:
                return False
        allow, why = self.decide(host, exe)
        if allow:
            with self._cv:
                self._pending.pop(key, None)
            return False
        if self._stopped:
            return False
        url = self.make_url(host, why, original)
        if not bars.navigate(hwnd, value, url):
            return False
        with self._cv:
            self._pending.pop(key, None)
        return True

    def _run(self):
        import window_guard
        bars = AddressBars(missing_retry=0.5)
        try:
            while True:
                with self._cv:
                    if self._stopped:
                        return
                    now = time.monotonic()
                    self._pending = {k: v for k, v in self._pending.items() if now - v < NOTICE_TTL}
                    if not self._pending:
                        self._thread = None
                        return
                try:
                    w = window_guard.foreground_window()
                    bars.forget_except({w["hwnd"]} if w else set())
                    if w:
                        self._visit(bars, w, w.get("exe", ""))
                except Exception:
                    pass                       # 浏览器 / UIA 不可用时只保留网络拦截
                with self._cv:
                    if not self._stopped:
                        self._cv.wait_for(lambda: self._stopped, timeout=0.25)
        finally:
            bars.close()
