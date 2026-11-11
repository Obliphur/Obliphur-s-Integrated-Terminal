"""设备同步 (MECHANICS 15) 与更新 (MECHANICS 16).

从 app.py 的 BreakTimerApp 拆出来的 mixin: 状态都在 self 上, 这里只放方法.
"""

import time
from datetime import datetime

import config
import continuity
import i18n
import tkinter as tk
import toast
from config import IDLE, ON_BREAK, RUNNING
from i18n import tr


class SyncMixin:
    def _pump_sync(self):
        """后台线程交过来的改动在这里 (界面线程) 执行."""
        if self.shutting_down:
            return
        self.sync_runner.pump()
        self.sync_job = self.root.after(100, self._pump_sync)

    # ---- 设备同步: 计时器此刻的状态 (MECHANICS 15.2) ----

    def _touch(self, _e=None):
        self.last_op = time.time() * 1000

    def _mine(self):
        """本机改动了计时器状态: 本机成为这个状态的主人 (自动结束 / 记录由主人做)."""
        self.timer_owner = self.sync.my_id
        self.last_op = time.time() * 1000

    def _timer_auto_ok(self) -> bool:
        """自动结束专注, 记下休息这类没人按按钮的操作, 由状态的主人做;
        主人不在线 (2 分钟没同步) 时本机来做."""
        return self._owner_away(self.timer_owner)

    def timer_live(self) -> dict:
        owner = self.timer_owner or self.sync.my_id
        if self.sleep_since is not None and self.state == IDLE:
            return {"state": "sleep", "owner": owner,
                    "since": self.sleep_since.isoformat(timespec="seconds")}
        if self.state == RUNNING and self.start_wall is not None:
            return {"state": "running", "owner": owner,
                    "start": self.start_wall.replace(microsecond=0).isoformat(timespec="seconds"),
                    "checks": int(self._focus_checks), "ok": int(self._focus_ok)}
        if self.state == ON_BREAK and self.break_end_wall is not None and self.break_start_wall is not None:
            return {"state": "break", "owner": owner,
                    "break_start": self.break_start_wall.isoformat(timespec="seconds"),
                    "break_end": self.break_end_wall.isoformat(timespec="seconds"),
                    "bonus": bool(self._bonus_break_active),
                    "cbreak": bool(self._contract_break_active),
                    "cpre": round(float(self._break_pre), 1), "ccid": self._break_cid,
                    "crestover": bool(self._contract_rest_over)}
        return {"state": "idle", "owner": owner}

    def adopt_timer(self, live: dict):
        """别的设备上更新的计时器状态: 照着切换, 不记录本机原来那一段 (那台设备会记)."""
        if not isinstance(live, dict):
            return
        cur = self.timer_live()
        st, owner = live.get("state"), str(live.get("owner") or "")
        if st == cur["state"] and live.get("start") == cur.get("start") \
                and live.get("break_end") == cur.get("break_end") and live.get("since") == cur.get("since"):
            if st == "running" and int(live.get("checks", 0)) > self._focus_checks:
                self._close_check_win()         # 在别的设备上确认过"还在吗"了
                self._focus_checks = int(live.get("checks", 0))
                self._focus_ok = float(live.get("ok", 0))
            self.timer_owner = owner
            self._save_timer_state()
            return
        self._close_check_win()
        self.cancel_update()
        self.idle_since = None
        now = datetime.now()
        if self.sleep_since is not None and st != "sleep":
            self.sleep_since = None                 # 别的设备起床了 / 开始了别的事: 那台设备记下这段睡眠
            self._hide_sleep_cover()
        try:
            if st == "sleep":
                since = datetime.fromisoformat(live["since"])
                self.break_start_wall = None
                self.reset_to_idle()
                self.sleep_since = since
                self._show_sleep_cover()
            elif st == "running":
                start = datetime.fromisoformat(live["start"])
                self.break_start_wall = None
                self._enter_focus(start, time.monotonic() - (now - start).total_seconds(),
                                  int(live.get("checks", 0)), float(live.get("ok", 0)))
            elif st == "break":
                end = datetime.fromisoformat(live["break_end"])
                left = int((end - now).total_seconds())
                if left <= 0:
                    self.reset_to_idle()
                else:
                    self._resume_break(live, end, left)
            else:
                self.break_start_wall = None
                self.reset_to_idle()
        except (KeyError, TypeError, ValueError):
            return
        self.timer_owner = owner
        self._save_timer_state()
        self.refresh_display()

    # ---- 更新 (MECHANICS 16) ----

    def _on_main(self, fn):
        """后台线程完成后回到界面线程."""
        try:
            self.sync_runner.call(fn)
        except Exception:
            pass

    def check_update(self, auto: bool):
        if self.shutting_down:
            return
        self.updater.check(done=lambda u: self._on_main(lambda: self._after_check(auto)))
        self._refresh_if_visible("settings")

    def _after_check(self, auto: bool):
        u = self.updater
        self._refresh_if_visible("settings")
        if u.state == "available":
            if auto and getattr(config, "UPDATE_AUTO", 1):
                self.install_update()
                return
            toast.notice(tr("⬆  发现新版本"),
                         tr("当前版本 {v}", v=config.APP_VERSION) + "\n" + tr("发现新版本 {v}", v=u.latest_text), [
                             (tr("立即更新"), self.install_update, True), (tr("稍后"), None, False)])
        elif u.state == "latest" and not auto:
            toast.info(tr("已是最新版本") + f" ({config.APP_VERSION})")
        elif u.state == "error":
            # 更新失败只弹提示的话事后查不到原因: 记进 settings_changes.log (路线见 updater._route)
            import updater as updater_mod
            via = updater_mod._route.get("last") or updater_mod._route.get("via") or "?"
            self._log_notice(f"UPDATE failed via {via}: {u.error}")
            if auto:
                self._update_retry = True
            else:
                toast.warn(tr("检查 / 更新失败: {e}", e=u.error))

    def install_update(self):
        if self.updater.state != "available":
            return
        toast.info(tr("正在下载更新…"))
        self.updater.install(done=lambda u: self._on_main(self._after_install))
        self._refresh_if_visible("settings")

    def _after_install(self):
        u = self.updater
        self._refresh_if_visible("settings")
        if u.state == "installed":
            self._log_notice(f"UPDATE installed {u.latest_text}")
            toast.notice(tr("⬆  已更新"), tr("已更新到 {v}, 重启后生效", v=u.latest_text), [
                (tr("立即重启"), self._restart_for_update, True), (tr("稍后"), None, False)])
        elif u.state == "dev":
            toast.warn(tr("这是 git 仓库里的开发版本, 不会自动覆盖, 请用 git pull 更新"))
        elif u.state == "error":
            toast.warn(tr("检查 / 更新失败: {e}", e=u.error))

    def on_peer_saw_me(self, st: dict):
        """别的设备记着的本机上次的状态比本机的数据新得多 = 本机的数据被倒回去了 (备份还原, 镜像还原 ……): 在锚点里标记,
        马上重启, 下一次启动时清掉数据 (MECHANICS 9.3)."""
        c = continuity.current
        if c is None or self._cont_broken or self.shutting_down or self.dm.resetting or not c.peer_saw(st):
            return
        self._cont_broken = True
        self._log_notice(f"CONTINUITY peers saw seq {st.get('cseq')} > ours {c.seq}")
        c.mark_pending(f"peers saw seq {st.get('cseq')} > ours {c.seq}")
        self._restart_for_update()

    def restart_by_peer(self, name: str):
        """别的设备发现本机离线了, 请本机重新启动 OIT (MECHANICS 15.3): 和更新后重启一样, 共享的专注不结束."""
        if self.shutting_down or self.dm.resetting:
            return
        self._log_notice(f"SYNC restart requested by {name}")
        self._restart_for_update()

    def _restart_for_update(self):
        self.restart_requested = True       # main.py 在主循环结束后重新打开程序
        self._update_restart = True         # 更新后马上重开: 不算"关闭程序", 共享的专注不结束
        try:
            self.root.withdraw()            # 旧窗口马上消失: 下面的收尾 (保存, 同步, 断网) 可能要几秒
        except tk.TclError:
            pass
        try:
            self.on_close()
        finally:
            # 收尾中途出错 (例如内存里的旧代码碰上刚覆盖的新文件) 时, 以前停在 root.destroy() 之前:
            # 旧窗口一直开着, main.py 也等不到主循环结束, 不会重新打开程序
            try:
                if self.root.winfo_exists():
                    self.root.destroy()
            except tk.TclError:
                pass

    def on_sync_changed(self):
        self.refresh_display()
        self.update_log_badge()
        self.update_settings_badge()
        self._refresh_if_visible("rules", "stats", "cal", "log")

    def after_sync_settings(self, keys):
        """同步来的设置生效之后: 和本机修改生效时一样刷新."""
        if "LANGUAGE" in keys or "THEME" in keys:
            i18n.init_from_config()
            self.root.after(50, self.rebuild_ui)
            return
        if "HOUR_LOG_ENABLED" in keys:
            self._sync_hour_log_setting()
        if "BEHAVIOR_MODE" in keys and getattr(self, "behavior", None) is not None:
            self.behavior.sync_mode()
            self._behavior_tick()
        if "FILTER_MODE" in keys and "rules" in self.pages:
            self.pages["rules"].refresh(force=True)
        if "settings" in self.pages:
            self.pages["settings"].refresh()
        self.update_settings_badge()
        self.refresh_net(force=True)

    def after_behavior_synced(self):
        """同步来的外出记录: 刷新按钮 / 倒计时; 到时间还没发的休息时间由本机发 (如果是主人)."""
        if getattr(self, "behavior", None) is None:
            return
        self.behavior.sync_mode()
        self._behavior_tick()
        self._sync_timer_buttons()
        self._refresh_if_visible("stats", "cal")
