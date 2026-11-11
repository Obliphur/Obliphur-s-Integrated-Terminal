"""断网后端: 禁用网卡 (Adapter) 与过滤代理 (Proxy) 的切换、系统代理、模式同步。

从 net_guard.py 拆出来的 mixin; 依赖方向: net_guard.py 导入它们, 它们从不导入 net_guard.py.
"""

import sys
import queue
import subprocess


class BackendMixin:
    def _cmd(self, online: bool) -> str:
        verb = "Enable" if online else "Disable"
        return f"Get-NetAdapter -Physical | {verb}-NetAdapter -Confirm:$false"

    @staticmethod
    def _run(cmd):
        if callable(cmd):
            try:
                cmd()
            except Exception:
                pass
            return
        if sys.platform != "win32":
            return
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True,
                creationflags=0x08000000
            )
        except Exception:
            pass

    def _worker(self):

        while True:
            self._run(self.q.get())

    def _proxy_apply(self, online: bool):
        """proxy 模式的「联网 / 断网」:网卡保持开着,只切换过滤代理的放行策略.

        任何一步失败都不能留下「没有任何限制」的状态 —— 离线时退回禁用网卡."""
        try:
            self.proxy.start()
            self.proxy.set_online(online)
            if self._nic_off_by_fallback:
                self._nic_off_by_fallback = False
                self._run(self._cmd(True))
        except Exception as e:
            self._log(f"PROXY    failed ({e}); falling back to adapter cut-off")
            if not online:
                self._nic_off_by_fallback = True
                self._run(self._cmd(False))

    def _enter_proxy(self):
        """先让过滤代理就位,再放开网卡 —— 切换瞬间不留空窗."""
        self._proxy_apply(self.online)
        if not self._nic_off_by_fallback:
            self._run(self._cmd(True))

    def _backend_set(self, online: bool, sync: bool = False):
        job = (lambda: self._proxy_apply(online)) if self.net_mode == "proxy" \
            else self._cmd(online)
        if sync:
            self._run(job)
        else:
            self.q.put(job)

    def block(self, sync: bool = False):

        if self.dead or self.dev_mode:
            return
        self._backend_set(False, sync)

    def unblock(self):
        self._backend_set(True)

    def ensure_online(self):
        """彻底解除限制:网卡全开 + 过滤代理关闭并还原系统代理."""
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        self._nic_off_by_fallback = False
        self._run(self._cmd(True))
        self._run(lambda: self.proxy.stop(restore=True))
        if self.fence is not None:
            self._run(self.fence.clear_all)         # 浏览器围栏也撤掉 (重置 / 卸载 / 开发者模式)
            self._fence_clean = True
        if self.bypass is not None:
            self._run(self.bypass.clear_all)        # 不走系统代理的程序的封锁规则也撤掉
            self._bp_clean = True

    def sync_mode(self):
        """设置里的 NET_MODE 变了(立即生效或冷静期到期)就切换后端;没变则什么都不做."""
        if self.dead or self._applied_mode == self.net_mode:
            return False
        self._applied_mode = self.net_mode
        if self.dev_mode:
            return True
        if self.net_mode == "proxy":
            self.q.put(self._enter_proxy)
        else:
            try:
                self.before_adapter()       # 网卡还在: 先告诉其他设备本机要断网了
            except Exception:
                pass
            self.q.put(self._cmd(self.online))
            self.q.put(lambda: self.proxy.stop(restore=True))
        self._log(f"MODE     switched to {self.net_mode}")
        return True
