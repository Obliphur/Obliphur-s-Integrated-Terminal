"""防绕过与每秒 tick: 系统代理被改回、浏览器围栏、直连程序封锁 (MECHANICS 3.2.2 / 3.2.3)。

从 net_guard.py 拆出来的 mixin; 依赖方向: net_guard.py 导入它们, 它们从不导入 net_guard.py.
"""

import time
import threading
from datetime import datetime
import browserfence
import bypassguard
import config


class TickMixin:
    # ---- 防绕过: Proxy 断网方式只管得住遵守系统代理的程序 (MECHANICS 3.2.2) ----

    def _guard_wanted(self) -> bool:
        return self.fence is not None and not self.dead and not self.dev_mode and self.net_mode == "proxy"

    def _guard_tick(self, now: float):
        if self._guard_wanted() and now - self._watch_at >= 1.0:
            self._watch_at = now
            self._watch_system_proxy()
        self._fence_tick(now)
        self._bypass_tick(now)

    def _watch_system_proxy(self):
        """每秒看一眼系统代理: 被关掉 / 改成别的代理 / 加了 PAC 就马上改回来 (不等"断网复查间隔")."""
        try:
            if not self.proxy.running or not self.q.empty():
                return
            if self.proxy.sys.is_installed("127.0.0.1", self.proxy.port):
                return
        except Exception:
            return
        self.tamper = {"count": self.tamper["count"] + 1, "at": time.time()}
        self._log("TAMPER   system proxy was changed; restoring it")
        self.q.put(lambda: self._proxy_apply(self.online))
        try:
            if self.on_tamper:
                self.on_tamper("proxy")
        except Exception:
            pass

    def _fence_tick(self, now: float):
        """每 10 秒把浏览器围栏调整成正好是现在的浏览器 (后台线程; 不在 Proxy 方式 / 开发者模式时删掉)."""
        if self.fence is None or self._fence_busy or now - self._fence_at < 10.0:
            return
        want = self._guard_wanted()
        if not want and self._fence_clean:
            return
        self._fence_at = now
        self._fence_busy = True
        threading.Thread(target=self._fence_run, args=(want,), daemon=True).start()

    def _fence_run(self, want: bool):
        try:
            if want:
                self.fence.sync(browserfence.browser_paths())
                self._fence_clean = False
            else:
                self.fence.clear_all()
                self._fence_clean = True
        except Exception as e:
            self._log(f"FENCE    failed ({type(e).__name__}: {e})")
        finally:
            self._fence_busy = False

    def _bypass_tick(self, now: float):
        """每 2 秒看一次连接表 (后台线程): 有不走系统代理的程序直连外面就封锁这条连接; 不在 Proxy 方式 / 开发者模式时删掉规则 (只删一次)."""
        if self.bypass is None or self._bp_busy or now - self._bp_at < bypassguard.SCAN_EVERY:
            return
        want = self._guard_wanted() and bool(getattr(config, "BYPASS_GUARD", True))
        if not want and self._bp_clean:
            return
        self._bp_at = now
        self._bp_busy = True
        threading.Thread(target=self._bypass_run, args=(want,), daemon=True).start()

    def _bypass_run(self, want: bool):
        try:
            if want:
                if not self._bp_init:
                    self._bp_init = True
                    self.bypass.clear_all()
                self._bp_clean = False
                for path, killed, new in self.bypass.scan():
                    if new:           # 不弹提示, 只在流水里记一条 (每个程序一次)
                        self._log(f"BYPASS   blocked a direct connection of {path} (not via the system proxy; {killed} connection(s) cut)")
            else:
                self.bypass.clear_all()
                self._bp_clean = True
        except Exception as e:
            self._log(f"BYPASS   failed ({type(e).__name__}: {e})")
        finally:
            self._bp_busy = False

    def tick(self, dt: float):
        today = datetime.now().strftime('%Y-%m-%d')
        if today != self.date:
            self._rollover(today)                 # 开发者模式下也照常跨日, 关掉开发者模式时额度已经是今天的
        self._guard_tick(time.monotonic())

        if self.dev_mode:
            if self.pending:
                self.pending = None
                self._log("DEV      pending request dropped (developer mode)")
            # 开着的时间照常消耗联网额度 (1 倍), 记成一次联网; 只消耗, 不限制: 额度用完了也不会关, 随时能开
            dt = min(max(dt, 0.0), self.tick_clamp)
            self.used += dt
            if self._session is None:
                self._open_session("开发者模式", False, 0)
            self._count_online(dt)
            if time.monotonic() - self._last_save >= 5:
                self._save()
            return None

        event = None
        if self.pending and time.time() >= self.pending["at"]:
            p, self.pending = self.pending, None
            self._reset_grant()
            quick = bool(p.get("quick"))
            rate = max(1.0, float(config.QUICK_MULTIPLIER)) if quick else 1.0
            secs = min(p["minutes"] * 60.0, self.remaining / rate)
            # 编号接着申请的, 各设备算出来的一样 (结束时刻差一两秒的, 同步时对齐)
            self.live_id = (self.live_id or str(int(p["at"] * 1000))) + "g"
            if secs > 0 and self._grant(round(time.time() + secs), rate, quick, p.get("reason", ""), p["minutes"],
                                        left=secs):
                self._save()
                self._log(f"START    {int(secs // 60)} min granted"
                          + (f" (quick, {rate:g}x)" if quick else ""))
                event = "started"

        now = time.monotonic()
        if self.online:
            dt = min(max(dt, 0.0), self.tick_clamp, self.grant_left)
            self.grant_left -= dt
            if self.grant_end and self.grant_end - time.time() < self.grant_left - 1:
                self.grant_left = max(0.0, self.grant_end - time.time())   # 按墙上时间到点 (电脑睡眠过等)
            # 同一次联网在各设备上一起进行: 只由发起的设备扣额度 (它不在线时由本机扣)
            if self.charge_ok(self.live_owner):
                self._count_online(dt)
                self.used += dt * self.grant_rate

            if self.grant_left <= 1e-6:
                self.live_id += "e"
                self._reset_grant()
                self.block()
                self._save()
                self._log("END      time is up")
                event = "ended"
            elif now - self._last_save >= 5:
                self._save()
            if self.net_mode == "proxy" and now - self._last_enforce >= self.enforce_every:
                self._last_enforce = now
                if self.q.empty():
                    self.q.put(lambda: self._proxy_apply(self.online))
        elif now - self._last_enforce >= self.enforce_every:
            self._last_enforce = now
            if self.q.empty():
                self.block()
        return event
