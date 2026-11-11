"""开发者模式跟着母端 + 更新中继 (MECHANICS 16.6).

从 app.py 的 BreakTimerApp 拆出来的 mixin: 状态都在 self 上, 这里只放方法.
检查 / 安装更新在 updater.py, 转发靠 devsync 的文件通道.
"""

import os
import threading
import time

import config
import devsync
import toast
import updater
from i18n import tr


class UpdateMixin:
    # ---- 开发者模式跟着母端 (设备同步) ----

    def _is_child(self) -> bool:
        m = self.sync.mother_id()
        return bool(m) and m != self.sync.my_id

    def on_dev_toggled(self, enabled: bool):
        """在"每日联网额度"里填了开关的数: 绑定了母端的子端不能自己开关 (跟着母端), 改回去."""
        if self._is_child():
            config.DEV_MODE = not enabled
            return
        self.on_dev_mode_changed(enabled)
        if self.sync.peers():
            self.sync.sync_now()                # 马上告诉子端

    def _auto_dev_ready(self):
        """本地开发版本的自动开发者模式 (config.DEV_AUTO): 界面建好之后把网络放开, 并告诉子端. 子端以母端为准."""
        if self._is_child() or not config.dev_mode_enabled():
            return
        self.on_dev_mode_changed(True)
        self._log_notice("DEV      auto developer mode (local dev version)")
        if self.sync.peers():
            self.sync.sync_now()

    def follow_dev_mode(self, on: bool):
        """同步来的母端状态. 公开发布版没有开发者模式, 母端开了也不跟."""
        if on and not config.dev_allowed():
            return
        if on != config.dev_mode_enabled():
            config.DEV_MODE = on
            self.on_dev_mode_changed(on)

    def _check_dev_follow(self):
        """子端: 母端 2 分钟没同步过了 (关掉了 / 断开了), 开发者模式就关掉."""
        if not config.dev_mode_enabled() or not self._is_child():
            return
        p = self.sync.peers().get(self.sync.mother_id()) or {}
        if time.time() * 1000 - float(p.get("last_ok") or 0) > devsync.FRESH_MS:
            self.follow_dev_mode(False)

    # ---- 安装包直接发给其他设备 / 收其他设备发来的 (MECHANICS 16) ----

    def update_wanted(self, platform: str, version: str) -> str:
        """"" = 要; 否则是不要的原因."""
        if platform != "windows":
            return "platform"
        v = updater.parse_version(version)
        if not v or v <= updater.parse_version(config.APP_VERSION):
            return "not-newer"
        if updater.is_dev_checkout():
            return "dev"
        return ""

    def on_update_received(self, path: str, version: str, name: str):
        def install():
            self.updater.install_file(path, done=lambda u: self._on_main(
                lambda: self._after_relay_install(version, name)))
        if getattr(config, "UPDATE_AUTO", 1):
            install()
            return
        toast.notice(tr("⬆  发现新版本"), tr("{name} 发来了新版本 {v}.", name=name, v=version), [
            (tr("立即更新"), install, True), (tr("稍后"), None, False)])

    def _after_relay_install(self, version: str, name: str):
        u = self.updater
        self._refresh_if_visible("settings")
        if u.state == "installed":
            self._log_notice(f"UPDATE installed {version} (from {name})")
            toast.notice(tr("⬆  已更新"), tr("已从 {name} 收到并安装 {v}, 重启后生效", name=name, v=version), [
                (tr("立即重启"), self._restart_for_update, True), (tr("稍后"), None, False)])
        elif u.state == "dev":
            toast.warn(tr("这是 git 仓库里的开发版本, 不会自动覆盖, 请用 git pull 更新"))
        else:
            toast.warn(tr("检查 / 更新失败: {e}", e=u.error))

    def _maybe_relay(self):
        """自动更新开着: 绑定的设备版本比 GitHub 上的旧, 就给它下载并发过去 (每台设备每个版本一次)."""
        if not getattr(config, "UPDATE_AUTO", 1) or self.updater.latest is None or self._relaying:
            return
        for pid, p in self.sync.peers().items():
            pv = updater.parse_version(p.get("version"))
            if pv and pv < self.updater.latest and (pid, self.updater.latest) not in self._relayed:
                self.relay_updates()
                return

    def relay_updates(self, manual: bool = False, only: str = None) -> bool:
        """only: 只发给这一台 (它请本机发过去的). 返回 False: 正在发送 / 没有设备."""
        if self._relaying:
            if manual:
                toast.info(tr("正在发送…"))
            return False
        peers = {pid: p for pid, p in self.sync.peers().items()
                 if p.get("platform") in ("windows", "android") and (only is None or pid == only)}
        if not peers:
            if manual:
                toast.info(tr("没有绑定的设备."))
            return False
        self._relaying = True
        if manual:
            toast.info(tr("正在给其他设备下载新版本…"))
        cache = os.path.join(self.dm.data_dir, "relay")

        def run():
            out = []
            try:
                rel = self.updater.fetch_release()
                latest = updater.release_version(rel)
                for pid, p in peers.items():
                    name = str(p.get("name") or "?")
                    pv = updater.parse_version(p.get("version"))
                    if pv and pv >= latest:
                        out.append((name, "latest"))
                        continue
                    if pv is None and not manual:
                        continue                # 旧版本的 OIT 不报版本号, 也收不了: 只在手动时试
                    self._relayed.add((pid, latest))
                    try:
                        path = self.updater.fetch_package(rel, p["platform"], cache)
                        out.append((name, self.sync.offer_update(pid, path, p["platform"], updater.vtext(latest))))
                    except Exception as e:      # noqa: BLE001
                        out.append((name, str(e) or type(e).__name__))
            except Exception as e:              # noqa: BLE001
                out.append(("GitHub", str(e) or type(e).__name__))
            finally:
                self._relaying = False
            self._on_main(lambda: self._report_relay(out, manual))
        threading.Thread(target=run, daemon=True).start()
        return True

    def _report_relay(self, out, manual: bool):
        from pages.sync_panel import sync_error_text
        words = {"sent": tr("已发送, 对方正在接收"), "latest": tr("已是最新版本"),
                 "platform": tr("对方不收这个平台的安装包"), "not-newer": tr("对方的版本不比这个旧"),
                 "dev": tr("对方是 git 仓库里的开发版本"), "no-package": tr("这个发布里没有对方平台的安装包")}
        lines = [f"{n}: {words.get(st) or sync_error_text(st)}" for n, st in out]
        if not lines or (not manual and not any(st == "sent" for _n, st in out)):
            return
        (toast.success if any(st == "sent" for _n, st in out) else toast.info)(
            tr("📤 发给其他设备") + "\n" + "\n".join(lines))

    def on_dev_mode_changed(self, enabled: bool):
        """在"每日联网额度"里填 325799 开 / 关 (settings_manager.DEV_TOGGLE_CODE). 不是设置改动, 不记流水."""
        if enabled:
            self.net.release_restrictions()
        else:
            self.net.dev_closed()
            try:
                self.net.block(sync=True)
            except Exception:
                pass
        self.update_settings_badge()
        self.refresh_net(force=True)
        self.pages["settings"].refresh()
