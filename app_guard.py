"""后台定时检查: 窗口事件与 5 秒心跳 / 窗口守护 / 自启 / 网卡重叠 / 应用规则 / 窗口使用时间.

从 app.py 的 BreakTimerApp 拆出来的 mixin: 状态都在 self 上, 这里只放方法.
5 秒心跳 _schedule_apply_check 把各子系统的定时检查串起来.
"""

import time
from datetime import datetime

import tkinter as tk

import autostart
import closedtest
import config
import contract as contract_mod
import firewall
import linkd
import registration
import toast
import updater
import window_guard
from config import ON_BREAK, RUNNING, fmt_hm
from i18n import tr


class GuardMixin:
    def _on_window_map(self, event):
        if hasattr(self, 'dm'):
            expected = datetime.now().strftime('%Y-%m-%d')
            if expected != self.dm.today_date:
                self._on_midnight()
            self.check_pending_settings()


    def _set_fullscreen(self, on: bool):
        try:
            self.root.attributes("-fullscreen", bool(on))
        except tk.TclError:
            pass

    def _toggle_fullscreen(self, _event=None):
        """F11: 全屏 / 退出全屏 (全屏时没有标题栏; 睡眠页、指令页等照常盖在上面)."""
        try:
            self.root.attributes("-fullscreen", not bool(self.root.attributes("-fullscreen")))
        except tk.TclError:
            pass
        return "break"

    def _on_window_unmap(self, event):
        """OIT 最小化 / 藏起来: V 要求不能最小化, 等窗口状态更新后马上判一次."""
        if event.widget is not self.root:
            return
        try:
            self.root.after(0, self.prescripts.page_changed)
        except Exception:
            pass


    def _schedule_apply_check(self):
        if self.shutting_down:
            return
        self.check_pending_settings()
        self._check_rule_changes()
        self._check_close_pending()
        self._check_focus()
        self._keep_awake()
        self._check_dev_follow()
        self._maybe_relay()
        self._check_idle_nudge()
        self._check_sleep()
        self._check_setup_period()
        self._check_unbind()
        self._check_offline_chime()
        self.check_achievements()
        self.apply_job = self.root.after(5000, self._schedule_apply_check)

    # ---- 任务栏窗口数上限 (window_guard.py) ----

    @staticmethod
    def _window_check_ms() -> int:
        return int(max(5.0, float(getattr(config, "WINDOW_CHECK_EVERY", 30))) * 1000)

    def _schedule_window_guard(self):
        if self.shutting_down:
            return
        ws = self._aged_windows()
        # 同一个程序的多个窗口算一个, 浏览器的每个窗口各算一个 (窗口数上限和通道阻塞一样, MECHANICS 12)
        self._check_windows(None if ws is None else window_guard.channel_count(ws, contract_mod.BROWSER_EXES))
        try:
            self._check_contract_windows(ws)
        except Exception as e:
            self._log_window(f"ERROR    contract windows {type(e).__name__}: {e}")
        self.window_job = self.root.after(self._window_check_ms(), self._schedule_window_guard)

    def _aged_windows(self):
        """任务栏上开着超过 WINDOW_MIN_AGE 秒的窗口 (MECHANICS 12); 数不出来时 None.
        不在专注时也记着各窗口开了多久, 开始专注时早就开着的窗口马上算."""
        if self.dm.resetting or not (getattr(config, "WINDOW_LIMIT_ENABLED", 0) or self.contract.window_watch()[1]):
            self._win_ages.first = {}
            return None
        try:
            ws = window_guard.taskbar_windows(include_own=True, exclude_pids=self.prestart.pids)
        except Exception as e:
            self._log_window(f"ERROR    {type(e).__name__}: {e}")
            return None
        now = time.monotonic()
        self._win_ages.update(ws, now)
        return self._win_ages.old(ws, now, float(getattr(config, "WINDOW_MIN_AGE", 120)))

    def _check_windows(self, n):
        """专注中窗口数超过上限: 提醒 + 响一次违约音效; 提醒后 2 分钟冷却, 还没解决再响一次.
        n: 开着超过 2 分钟的窗口数 (_aged_windows; 同一个程序的多个窗口算一个, 浏览器的各算一个)."""
        if self.dm.resetting or not getattr(config, "WINDOW_LIMIT_ENABLED", 0) \
                or self.state != RUNNING:
            self._win_over = False
            self._win_warn_at = 0.0
            return
        limit = int(getattr(config, "WINDOW_LIMIT", 6))
        if n is None:
            return
        over = n > limit
        if not over:
            self._win_over = False
            self._win_warn_at = 0.0
            return
        now = time.monotonic()
        cooldown = float(getattr(config, "WINDOW_WARN_COOLDOWN", 120))
        if window_guard.remind_due(True, self._win_warn_at, now, cooldown):
            self._win_warn_at = now
            self._log_window(f"OVER     {n} > {limit}")
            toast.warn(tr("窗口超过上限 {x} 个 (现在 {n} 个), 关掉一些吧.", n=n, x=limit))
            self._play_alarm()                       # 和违约同一个音效 (MECHANICS 12)
        self._win_over = True


    # ---- 设备同步 (MECHANICS 15) ----

    def _check_autostart(self, first: bool = False):
        if closedtest.active():
            return
        if first:
            try:
                # setup.exe 装的 (MECHANICS 16.4): 程序自己更新过的话, 把"已安装的应用"里的版本号也改成现在的
                registration.refresh(updater.program_dir(), config.APP_VERSION)
            except Exception as e:
                self._log_window(f"ERROR    registration {type(e).__name__}: {e}")
        try:
            self.autostart_state = autostart.ensure(self.dm.data_dir)     # 任务被禁用 / 删掉了就重建 (启动时和之后每 5 分钟)
        except Exception:
            self.autostart_state = "error"
        try:
            # 联动程序 (MECHANICS 15.8): 登录时启动的后台进程, OIT 关掉了它照样在, 替别的设备把本机的 OIT 重新启动;
            # 它的计划任务被禁用 / 删掉了也重建, 进程没在运行就打开 (不绑定设备时也一样)
            autostart.link_ensure()
            linkd.ensure_running(self.dm.data_dir)
        except Exception as e:
            self._log_window(f"ERROR    link {type(e).__name__}: {e}")

    def _check_link(self):
        if closedtest.active():
            return
        try:
            linkd.ensure_running(self.dm.data_dir)
        except Exception as e:
            self._log_window(f"ERROR    link {type(e).__name__}: {e}")

    @property
    def autostart_on(self) -> bool:
        return self.autostart_state == "on"

    def _check_firewall(self):
        try:
            self.sync.firewall = firewall.check()
        except Exception as e:
            self.sync.firewall = {"added": [], "blockers": [], "error": f"{type(e).__name__}: {e}"}


    # ---- Adapter 断网 (MECHANICS 15.5) ----

    def _before_adapter(self):
        """马上要换成 Adapter (网卡还在): 本机状态设为"断网"发给每台设备, 之后不再同步. 其他设备把本机算作在线."""
        if not self.sync.peers():
            return
        self._log_notice("SYNC     adapter: telling peers this device goes offline")
        try:
            self.sync.mark_adapter()
        except Exception as e:
            self._log_window(f"ERROR    adapter farewell {type(e).__name__}: {e}")

    def _check_adapter_overlap(self):
        """每秒: 断网期间和其他设备重叠的记录, 按 休息 > 专注 > 空闲 处理 (只改本机自己的记录, MECHANICS 15.5).
        本机断网回来: 第一次同步上之后和每台设备比一遍 (一样的由本机去掉); 别的设备断网回来: 和它比一遍."""
        if self.net.net_mode == "proxy":
            self.sync.note_proxy_back()
        todo = []
        win = self.sync.adapter_window()
        if win and self.sync.synced_since(win[1]):
            led = self.sync.peer_ledgers_by_id() or {}
            todo += [(did, win[0], win[1], True) for did in led]
            self.sync.clear_adapter()
        todo += [(did, a, b, False) for did, a, b in self.sync.pop_adapter_back()]
        total = {"study": 0.0, "break": 0.0, "idle": 0.0}
        for did, a, b, mine in todo:
            led = (self.sync.peer_ledgers_by_id() or {}).get(did) or {}
            res = self.dm.resolve_overlap(led.get("focus") or [], datetime.fromtimestamp(a / 1000),
                                          datetime.fromtimestamp(b / 1000), tie_mine=mine)
            for k in total:
                total[k] += res.get(k, 0.0)
            self._log_notice(f"SYNC     adapter overlap with {did} ({'mine' if mine else 'theirs'}): {res}")
        if any(v > 0 for v in total.values()):
            toast.info(tr("断网期间和其他设备重叠的记录已按 休息 > 专注 > 空闲 处理: 去掉了专注 {s}, 休息 {b}, 空闲 {i}.",
                          s=fmt_hm(total["study"]), b=fmt_hm(total["break"]), i=fmt_hm(total["idle"])), timeout=10000)
            self._refresh_if_visible("stats", "cal")
            self.refresh_display()
            if self.sync.peers():
                self.sync.sync_now()

    def _check_away_priority(self):
        """每秒: 刚从自由学习的外出回来的设备第一次同步上以后, 外出期间本机和它重叠的专注计时记录让位给它 (MECHANICS 21.6)."""
        total = {"study": 0.0, "break": 0.0, "idle": 0.0}
        for did, a, b in self.sync.pop_away_prio():
            led = (self.sync.peer_ledgers_by_id() or {}).get(did) or {}
            res = self.dm.resolve_overlap(led.get("focus") or [], datetime.fromtimestamp(a / 1000),
                                          datetime.fromtimestamp(b / 1000), tie_mine=False, priority=True)
            for k in total:
                total[k] += res.get(k, 0.0)
            self._log_notice(f"SYNC     away priority from {did}: {res}")
        if any(v > 0 for v in total.values()):
            toast.info(tr("刚从外出回来的设备的记录有优先权: 去掉了本机重叠的专注 {s}, 休息 {b}, 空闲 {i}.",
                          s=fmt_hm(total["study"]), b=fmt_hm(total["break"]), i=fmt_hm(total["idle"])), timeout=10000)
            self._refresh_if_visible("stats", "cal")
            self.refresh_display()
            if self.sync.peers():
                self.sync.sync_now()

    def _contract_line(self, b: dict) -> str:
        """冲突对话框里一个合约的一行: 起止, 危机等级, 结果 / 进行中, 词条."""
        from pages.contract_page import RESULTS, term_short
        a0, a1 = contract_mod._parse(b.get("start")), contract_mod._parse(b.get("end"))
        when = f"{a0:%m-%d %H:%M} — {a1:%H:%M}" if a0 and a1 else "?"
        if self.contract.fog():
            when = "??:?? — ??:??"                   # 黑雾: 不显示合约的开始 / 结束时刻
        state = tr("进行中") if not b.get("result") else tr(RESULTS.get(b["result"], b["result"]))
        terms = " · ".join(term_short(t) for t in b.get("terms") or [] if t in contract_mod.BY_ID) or "—"
        return tr("{when} · 危机等级 {lv} · {state}", when=when, lv=b.get("level", 0), state=state) + "\n    " + terms

    def _check_contract_conflicts(self):
        """时间重叠的两个合约 (MECHANICS 15.5): 问用户留哪个. 关掉窗口不选的话 1 分钟后再问."""
        c = self.contract
        win = getattr(self, "_conflict_win", None)
        if not c.conflicts:
            if win is not None:
                try:
                    win.destroy()                    # 在别的设备上已经选过了
                except Exception:
                    pass
                self._conflict_win = None
            return
        try:
            if win is not None and win.winfo_exists():
                return
        except Exception:
            pass
        if time.monotonic() < getattr(self, "_conflict_next", 0.0):
            return
        self._conflict_next = time.monotonic() + 60
        a, b = c.conflicts[0]
        msg = (tr("断网期间两台设备各自进行了合约, 时间有重叠. 只能保留一个, 另一个作废 (已经给出的休息时间不收回).")
               + "\n\nA  " + self._contract_line(a) + "\n\nB  " + self._contract_line(b))
        self._conflict_win = toast.notice(tr("⚔ 合约冲突"), msg, buttons=[
            (tr("保留 A"), lambda i=a["id"]: self._keep_contract(i), True),
            (tr("保留 B"), lambda i=b["id"]: self._keep_contract(i), True),
        ])

    def _keep_contract(self, keep_id: str):
        c = self.contract
        if not c.resolve_conflict(keep_id):
            return
        self._conflict_win = None
        self._conflict_next = 0.0
        self._log_notice(f"CONTRACT conflict: keep {keep_id}")
        self._check_contract()
        self._contract_changed()
        if self.sync.peers():
            self.sync.sync_now()

    def _peer_line(self):
        """Timer 页: 绑定的每台设备此刻的状态; 有设备离线时提醒离线的那段专注不会被记录. 返回 (文字, 有离线吗)."""
        lines = []
        offline = []
        out = getattr(self, "behavior", None) is not None and self.behavior.current() is not None
        for st in self.sync.device_states():
            if not st["online"] and out:
                lines.append(f"🔗 {st['name']}: {tr('外出中 🚪')}")   # 外出期间的设备不算离线 (8.4)
                continue
            if st.get("off"):
                lines.append(f"🔗 {st['name']}: {tr('关机')}")     # 效果和在线一样, 只是不同步 (15.4)
                continue
            if st.get("net_off"):
                lines.append(f"🔗 {st['name']}: {tr('断网')}")     # 换成了 Adapter: 效果和在线一样, 只是不同步 (15.5)
                continue
            if not st["online"]:
                offline.append(st["name"])
                if st.get("outdated"):
                    lines.append(f"🔗 {st['name']}: " + tr("离线 (版本 {v} 较低, 请更新)", v=st.get("version")))
                else:
                    lines.append(f"🔗 {st['name']}: {tr('离线')}")
                continue
            t = {"running": tr("专注中…"), "break": tr("休息中 ☕")}.get(st.get("timer"), tr("准备就绪"))
            if st.get("timer") == "running" and st.get("since") and not self.contract.fog():
                try:
                    mins = (datetime.now() - datetime.fromisoformat(st["since"])).total_seconds() / 60
                    t += f" {mins:.0f} min"
                except ValueError:
                    pass
            net = {"online": tr("联网中"), "pending": tr("冷静期中")}.get(st.get("net"), "")
            lines.append(f"🔗 {st.get('name', '?')}: {t}" + (f" · {net}" if net else ""))
        away = self.behavior.current() if getattr(self, "behavior", None) is not None else None
        if away is not None:
            lines.append(tr("🚪 外出中: 其他设备离线不影响专注和合约."))
        elif offline:
            lines.append(tr("⚠ 有设备离线: 离线的这段时间专注不会被记录."))
        for name, v, newer in self.sync.version_mismatch():
            if newer:          # 本机版本低: 对方把本机算作离线
                lines.append(tr("⚠ 本机版本 {me} 低于 {name} ({v}): 其他设备把本机算作离线, 请更新.",
                                me=config.APP_VERSION, name=name, v=v))
        return "\n".join(lines), bool(offline)

    # ---- Rules 页的应用名单: 前台窗口的程序被禁用时立即关掉 (MECHANICS 4.7) ----

    def _schedule_app_rules(self):
        if self.shutting_down:
            return
        try:
            self._check_app_rules()
        except Exception as e:
            self._log_window(f"ERROR    app rules {type(e).__name__}: {e}")
        try:
            self._apply_net_bans()
        except Exception as e:
            self._log_window(f"ERROR    bans {type(e).__name__}: {e}")
        self.app_rules_job = self.root.after(1000, self._schedule_app_rules)

    def _check_app_rules(self):
        """Proxy / 代理-全屏蔽 (Proxy block-all) 断网方式下生效 (全屏蔽时只剩应用名单起作用);
        开发者模式下不管 (和网络限制一样解除)."""
        if self.dm.resetting or self.net.dev_mode or self.net.net_mode != "proxy":
            return
        rules = self.net.proxy.rules
        mode = getattr(config, "FILTER_MODE", "blacklist")
        rest = self.state == ON_BREAK              # 只管专注 / 空闲, 休息中全部放行; 和联不联网无关 (4.1)
        hit = window_guard.check_focus_rules(lambda exe: rules.app_allowed(exe, rest, mode))
        if not hit:
            return
        exe, title, why = hit
        self._log_window(f"RULES    {why}  {exe}  {title}")
        try:
            self.net.proxy._on_event(f"🪟 {title[:40]}", exe, False, why)
        except Exception:
            pass
        now = time.monotonic()
        if now - self._app_rules_toast.get(exe, -99.0) >= 5:
            self._app_rules_toast[exe] = now
            msg = (tr("{exe} 在黑名单里,已关闭窗口.", exe=exe) if why == "blacklist-app"
                   else tr("{exe} 不在白名单里,已关闭窗口.", exe=exe))
            toast.warn(msg)
            # 全频道阻塞干扰 (软): 被禁止的程序 (同一个程序 5 秒内算一次) 这个钟点里各设备合计满 20 次算一次违反
            tid = self.contract.jam_block(datetime.now(), self.sync.my_id)
            if tid:
                self.contract_violation(tr("全频道阻塞干扰中频繁打开被禁止的程序"), tid)

    # ---- 窗口使用时间统计 ----

    @staticmethod
    def _window_focus_sec() -> float:
        return max(1.0, float(getattr(config, "WINDOW_FOCUS_EVERY", 10)))

    def _window_focus_ms(self) -> int:
        return int(self._window_focus_sec() * 1000)

    def _window_focus_tick(self):
        """看一次前台程序, 视为用户在过去这几秒一直在用它 (桌面 / 命令行记为空白).
        电脑睡眠时 after 不会触发, 所以睡眠的时间不会被算进去."""
        if self.shutting_down:
            return
        try:
            if getattr(config, "WINDOW_FOCUS_ENABLED", 0) and not self.dm.resetting:
                self.dm.add_window_time(window_guard.foreground_program(),
                                        self._window_focus_sec())
                self._window_focus_ticks += 1
                if self._window_focus_ticks % 6 == 0:      # 大约每分钟写一次文件
                    self.dm.save_window_usage()
                    self._refresh_if_visible("stats")
        except Exception as e:
            self._log_window(f"ERROR    focus {type(e).__name__}: {e}")
        self.window_focus_job = self.root.after(self._window_focus_ms(), self._window_focus_tick)
