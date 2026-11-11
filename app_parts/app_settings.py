"""设置生效 / 48 小时冷静期 / 关闭程序 / 日志与侧边栏徽章.

从 app.py 的 BreakTimerApp 拆出来的 mixin: 状态都在 self 上, 这里只放方法.
设置项的定义和取值在 settings_manager.py; 关闭程序时的计时器收尾走 app_timer.py.
"""

import os
from datetime import datetime

import config
import toast
from config import RUNNING
from i18n import tr
from pages.widgets import cfg


class SettingsMixin:
    def _after_setup_change(self):
        self.update_settings_badge()
        for key in ("settings", "rules"):
            if key in self.pages:
                try:
                    self.pages[key].refresh(force=True) if key == "rules" else \
                        self.pages[key].refresh()
                except Exception:
                    pass

    def check_pending_settings(self):
        if self.shutting_down:
            return []
        due = self.settings.promote_due()
        if not due:
            return due

        self._sync_hour_log_setting()
        if "NET_MODE" in due:
            self.net.sync_mode()
        if "BEHAVIOR_MODE" in due:
            self.behavior.sync_mode()
            self._behavior_tick()
        if "rules" in self.pages:
            self.pages["rules"].refresh(force=True)
        self.pages["settings"].refresh()
        self.update_settings_badge()
        self.refresh_net(force=True)
        labels = ",".join(tr(self.settings.display(k)) for k in due)
        toast.success(tr("以下配置已生效:") + "\n" + labels)
        self._log_notice(f"SETTINGS applied: {', '.join(due)}")
        return due

    def _check_rule_changes(self):
        """名单里到期的改动: 以前只在打开 Rules 页时才生效, 不看那一页时过滤代理一直用旧名单."""
        try:
            due = self.net.proxy.rules.promote_due()
        except Exception as e:
            self._log_window(f"ERROR    rules promote {type(e).__name__}: {e}")
            return
        if not due:
            return
        if "rules" in self.pages:
            self.pages["rules"].refresh(force=True)
        toast.success(tr("名单改动已生效:") + "\n" + "\n".join(
            f"{'+' if p['op'] == 'add' else '−'} {p['item']}" for p in due[:8]))
        self._log_notice("RULES applied: " + ", ".join(f"{p['op']} {p['list']} {p['item']}" for p in due))

    def _sync_hour_log_setting(self):
        # 整点记录关着, 合约选了整点记录的词条: 这次合约中照样开启, 结束后关闭 (MECHANICS 18.4)
        want = bool(self.settings.value("HOUR_LOG_ENABLED")) or self.contract.hour_forced()
        if want != self.dm.hour_enabled:
            self.dm.apply_hour_enabled(want)
        if "log" in self.pages:
            self.pages["log"].refresh()
        self._refresh_if_visible("stats")
        self.update_log_badge()

    def _log_window(self, text: str):
        """任务栏窗口数上限的流水: 关掉了哪些窗口, 以及检查出错的原因."""
        try:
            with open(os.path.join(self.dm.data_dir, "window_guard.log"),
                      'a', encoding='utf-8') as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")
        except IOError:
            pass

    def _log_notice(self, text: str):
        try:
            with open(os.path.join(self.dm.data_dir, "settings_changes.log"),
                      'a', encoding='utf-8') as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")
        except IOError:
            pass






    def on_close(self):
        if not getattr(self, "_update_restart", False):
            try:
                self.prescripts.note_close()        # 指令 X 的输入框要写今天被手动关闭的时刻
            except Exception:
                pass
        # 提前启动藏起来的窗口要放出来, 不然关掉 OIT 以后它们一直藏着 (连同触发隐藏的窗口)
        try:
            self._stop_prestart(final=True)
        except Exception:
            pass
        # 绑定了其他设备: 关闭程序就结束共享的专注, 并马上告诉其他设备 (MECHANICS 8.2)
        ended = False
        if self.state == RUNNING and self.start_mono is not None and self.sync.peers() \
                and not self.dm.resetting and not self._close_pending \
                and not getattr(self, "_update_restart", False):
            self._end_focus_for_close()
            ended = True
        # 专注 / 休息中关闭: 状态留在文件里, 下次打开时接着算 (见 _recover_focus / _recover_break)
        self._save_focus_now()
        self._cancel_jobs()
        if ended:
            try:
                self.sync.push_final()
            except Exception:
                pass
        self.sync.stop()
        # 关闭程序时开发者模式自动关掉, 否则 shutdown 不断网 (fail-closed 失效); 子端也跟着关
        # (本地开发版本的自动开发者模式例外: 关掉 / 重启测试用的实例不能把网断了, 下次打开时照样自动开)
        if config.dev_mode_enabled() and config.dev_auto():
            self.net.dev_closed()
        elif config.dev_mode_enabled():
            config.DEV_MODE = False
            self.net.dev_closed()
            if not self._is_child():
                try:
                    self.sync.push_final()
                except Exception:
                    pass
        self._stop_all_recorders(wait=8.0)     # 把正在录的文件写完 (Finalize)
        self.net.shutdown()
        self.root.destroy()

    def update_log_badge(self):
        b = self.nav_btns.get("log")
        if not b:
            return
        if not self.dm.hour_enabled:
            cfg(b, text=self.nav_labels["log"] + "  (off)")
        else:
            cfg(b, text=self.nav_labels["log"] + (" ●" if self.dm.awaiting_slot() else ""))

    def on_hour_log_toggle(self, enabled: bool):
        self.update_log_badge()
        if "log" in self.pages:
            self.pages["log"].refresh()

    def on_settings_changed(self, status: str = "", key: str = None):
        self.update_settings_badge()
        if "rules" in self.pages and key in ("FILTER_MODE", "NET_MODE", None):
            self.pages["rules"].refresh(force=True)
        if key in ("NET_MODE", None) and self.net.sync_mode():
            self.refresh_net(force=True)
            self._log_notice(f"NET_MODE -> {self.net.net_mode}")
        if status != "instant":
            return
        if key == "FULLSCREEN_DEFAULT":
            self._set_fullscreen(bool(config.FULLSCREEN_DEFAULT))       # 改了这一项: 当场切换
        if key == "BEHAVIOR_MODE" and getattr(self, "behavior", None) is not None:
            self.behavior.sync_mode()
            self._behavior_tick()
            if "log" in self.pages:
                self.pages["log"].refresh()
        if key in ("HOUR_LOG_ENABLED", "AUTO_TEXT", "FILL_WINDOW", None):

            if key in ("AUTO_TEXT", "FILL_WINDOW", None):
                if "log" in self.pages:
                    self.pages["log"].refresh()
            if key == "HOUR_LOG_ENABLED":
                self._sync_hour_log_setting()

    def update_settings_badge(self):
        b = self.nav_btns.get("settings")
        if not b:
            return
        mark = ""
        staged = self.settings.staged_count()
        if staged:
            mark += f"  ⏳{staged}"
        cfg(b, text=self.nav_labels["settings"] + mark)
