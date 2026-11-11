"""联网页 (MECHANICS 2) 与断网的辅助检查: 额度 / 申请 / 快速访问 / 借贷 / 篡改检测.

从 app.py 的 BreakTimerApp 拆出来的 mixin: 状态都在 self 上, 这里只放方法.
真正的断网判定在 netproxy/rules.py 的 decide(), 本文件只负责界面与每秒的 _net_tick.
"""

import os
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk

import config
import continuity
import devsync
import linkd
import securefile
import srcguard
import toast
from config import COLORS, ON_BREAK, fmt_hm, fmt_ms
from i18n import tr
from pages.widgets import cfg, hide_pack, show_pack


class NetMixin:
    def _build_net_page(self, page):
        bg, card = COLORS["bg"], COLORS["bg_card"]

        hdr = tk.Frame(page, bg=bg)
        hdr.pack(fill="x", padx=28, pady=(22, 8))
        tk.Label(
            hdr, text=tr("🌐  联网申请"), bg=bg, fg=COLORS["text"],
            font=config.font("h1", bold=True)
        ).pack(side="left", anchor="w")
        # 文件通道 (MECHANICS 15.6): 把文件 / 图片发给绑定的设备
        ttk.Button(hdr, text=tr("📁  文件通道"), command=self.file_channel.open).pack(side="right")

        ttk.Separator(page, orient="horizontal", style="UI.TSeparator").pack(fill="x", padx=26)

        card_fr = tk.Frame(
            page, bg=card, highlightbackground=COLORS["border"],
            highlightthickness=1, relief="solid", bd=0
        )
        card_fr.pack(padx=26, pady=20, fill="x")
        inner = tk.Frame(card_fr, bg=card)
        inner.pack(padx=22, pady=18, fill="both")

        status_row = tk.Frame(inner, bg=card)
        status_row.pack(anchor="w", pady=(0, 12))
        self.net_dot_canvas = tk.Canvas(status_row, width=12, height=12, bg=card, highlightthickness=0)
        self.net_dot_canvas.pack(side="left", padx=(0, 8))
        self.net_dot_id = self.net_dot_canvas.create_oval(1, 1, 11, 11, fill=COLORS["text_dim"], outline="")
        self.net_lbl_status = tk.Label(
            status_row, text="Offline", bg=card, fg=COLORS["text_dim"],
            font=config.font("body")
        )
        self.net_lbl_status.pack(side="left")

        self.net_lbl_time = tk.Label(
            inner, text="Offline", bg=card, fg=COLORS["text"],
            font=config.font("big", bold=True), anchor="w"
        )
        self.net_lbl_time.pack(anchor="w", pady=(2, 6))

        self.net_lbl_sub = tk.Label(
            inner, text="", bg=card, fg=COLORS["text_dim"],
            font=config.font("body"), anchor="w", justify="left"
        )
        self.net_lbl_sub.pack(anchor="w")

        self.net_bar = ttk.Progressbar(
            inner, maximum=float(self.settings.value("DAILY_LIMIT")), value=0,
            style="Quota.Horizontal.TProgressbar"
        )
        self.net_bar.pack(fill="x", pady=(16, 6))
        self.net_lbl_quota = tk.Label(
            inner, text="", bg=card, fg=COLORS["accent"],
            font=config.font("body", bold=True), anchor="w"
        )
        self.net_lbl_quota.pack(anchor="w")
        self.net_lbl_debt = tk.Label(
            inner, text="", bg=card, fg=COLORS["warn"],
            font=config.font("small"), anchor="w", justify="left"
        )
        self.net_lbl_debt.pack(anchor="w")

        area = tk.Frame(page, bg=bg)
        area.pack(fill="x", padx=26, pady=(0, 22))

        self.net_form = tk.Frame(area, bg=bg)
        row1 = tk.Frame(self.net_form, bg=bg)
        row1.pack(anchor="w", pady=(0, 8))
        ttk.Label(row1, text=tr("分钟"), style="UI.TLabel", width=8).pack(side="left")
        ttk.Spinbox(
            row1, from_=1, to=24 * 60, width=6,
            textvariable=self.var_min
        ).pack(side="left")
        row2 = tk.Frame(self.net_form, bg=bg)
        row2.pack(anchor="w", pady=(0, 12))
        ttk.Label(row2, text=tr("理由"), style="UI.TLabel", width=8).pack(side="left")
        ttk.Entry(row2, textvariable=self.var_reason, width=30).pack(side="left")

        btns = tk.Frame(self.net_form, bg=bg)
        btns.pack(anchor="w")
        self.btn_net_req = ttk.Button(
            btns, text=tr("📨  提交申请(需冷静期)"), style="Accent.TButton",
            command=self.on_net_request
        )
        self.btn_net_req.pack(side="left")
        self.btn_net_quick = ttk.Button(
            btns, text=tr("⚡  快速访问(×{a:g})").format(a=config.QUICK_MULTIPLIER),
            style="Danger.TButton", command=self.on_net_quick
        )
        self.btn_net_quick.pack(side="left", padx=(8, 0))
        self.btn_net_loan = ttk.Button(
            btns, text=tr("💳  紧急借贷"), style="Danger.TButton",
            command=self.on_net_loan
        )
        self.btn_net_loan.pack(side="left", padx=(8, 0))
        self.net_form.pack_configure(anchor="w", fill="x")

        self.net_ctrl = tk.Frame(area, bg=bg)
        self.btn_net_ctrl = ttk.Button(
            self.net_ctrl, text="", style="Accent.TButton",
            command=self.on_net_ctrl
        )
        self.btn_net_ctrl.pack(side="left", ipadx=10)
        self.btn_net_upgrade = ttk.Button(
            self.net_ctrl, text=tr("⚡  改为快速访问(×{a:g})").format(a=config.QUICK_MULTIPLIER),
            style="Danger.TButton",
            command=self.on_net_upgrade
        )

    def on_net_request(self):
        ok, minutes, reason = self._read_net_form()
        if not ok:
            return
        rate = 1.0
        if self.state == ON_BREAK and minutes * 60 <= self.countdown_seconds:
            # 休息中, 且申请时长不超过剩余休息时间 → 冷静期 ×BREAK_COOLDOWN_RATE
            rate = config.BREAK_COOLDOWN_RATE
        # 合约的侵蚀: 冷静期 ×2 / ×3
        ok, msg = self.net.request(minutes, reason, cooldown_rate=rate, cooldown_mult=self.contract.cooldown_factor())
        if not ok:
            toast.warn(tr(msg))
            return
        self.var_reason.set("")
        self.refresh_net(force=True)

    def on_net_quick(self):
        ok, minutes, reason = self._read_net_form()
        if not ok:
            return
        ok2, msg2 = self.net.can_quick(minutes)
        if not ok2:
            toast.warn(tr(msg2))
            return
        if not toast.ask(
            tr("快速访问跳过冷静期立刻联网,额度按 ×{a:g} 消耗.").format(
                a=config.QUICK_MULTIPLIER, m=minutes,
                cost=fmt_hm(minutes * 60 * config.QUICK_MULTIPLIER)),
            title=tr("快速访问"), danger=True
        ):
            return
        ok, msg = self.net.request(minutes, reason, quick=True)
        if not ok:
            toast.warn(tr(msg))
            return
        self.var_reason.set("")
        self.refresh_net(force=True)
        self._contract_quick()

    def on_net_loan(self):
        ok, minutes, reason = self._read_net_form()
        if not ok:
            return
        ok2, msg2 = self.net.can_borrow(minutes)
        if not ok2:
            toast.warn(tr(msg2))
            return
        if not toast.ask(
            tr("紧急借贷不会联网,只会给今天加额度;明天扣 {b:g} 倍.").format(
                b=config.LOAN_RATE, add=fmt_hm(minutes * 60),
                cost=fmt_hm(minutes * 60 * config.LOAN_RATE)),
            title=tr("紧急借贷"), danger=True
        ):
            return
        before = self.net.tomorrow_used_all
        ok, msg = self.net.borrow(minutes, reason)
        if not ok:
            toast.warn(tr(msg))
            return
        self.var_reason.set("")
        toast.success(tr("今天额度 +{add}.").format(add=fmt_hm(minutes * 60)))
        self.refresh_net(force=True)
        self._check_heavy_loan(before)

    def _check_heavy_loan(self, before: float):
        """借贷借掉明天额度的 LOAN_WARN_SHARE (默认一半) 以上: 提醒增加休息时间,
        并让下一次修改「每日联网额度」立即生效. 每天只在刚越过这条线时触发一次."""
        limit = self.net.tomorrow_limit
        if limit <= 0:
            return
        line = limit * float(config.LOAN_WARN_SHARE)
        after = self.net.tomorrow_used_all
        if not (before < line <= after):
            return
        granted = self.settings.grant_free_change("DAILY_LIMIT")
        if granted:
            self._log_notice("DAILY_LIMIT next change free — loan took "
                             f"{after / limit:.0%} of tomorrow's quota")
            self.update_settings_badge()
            if "settings" in self.pages:
                self.pages["settings"].refresh()
        msg = tr("明天的联网额度已经借掉了 {pct} ({used} / {limit}).").format(
            pct=f"{after / limit:.0%}", used=fmt_hm(after), limit=fmt_hm(limit))
        msg += "\n\n" + tr("也许是休息不够,可以调高「休息时间倍率」.")
        msg += "\n\n" + tr("下一次修改「每日联网额度」免等待.")
        toast.notice(tr("💳  借得有点多了"), msg, [
            (tr("去设置"), self._open_detail_settings, True), (tr("知道了"), None, False)])

    def _open_detail_settings(self):
        self.show_page("settings")
        try:
            self.pages["settings"].show_tab("timer")
        except Exception:
            pass

    def _read_net_form(self):
        try:
            minutes = int(self.var_min.get())
        except ValueError:
            toast.warn(tr("分钟数必须是整数."))
            return False, 0, ""
        reason = self.var_reason.get().strip()
        if not reason:
            toast.warn(tr("请写一个申请理由."))
            return False, 0, ""
        return True, minutes, reason

    def on_net_ctrl(self):
        if self.net.mode == "pending":
            self.net.cancel()
        elif self.net.mode == "online":
            self.net.end_early()
        self.refresh_net(force=True)

    def on_net_upgrade(self):
        p = self.net.pending or {}
        minutes = p.get("minutes", 0)
        ok2, msg2 = self.net.can_quick(minutes)
        if not ok2:
            toast.warn(tr(msg2))
            return
        if not toast.ask(
            tr("立刻结束冷静期并联网?额度按 ×{a:g} 消耗.").format(
                m=minutes, cost=fmt_hm(minutes * 60 * config.QUICK_MULTIPLIER), a=config.QUICK_MULTIPLIER),
            title=tr("快速访问"), danger=True
        ):
            return
        ok, msg = self.net.convert_to_quick()
        if not ok:
            toast.warn(tr(msg))
            return
        self.refresh_net(force=True)
        self._contract_quick()

    def _net_set_status(self, text: str, color: str):
        # 状态后面写上当前的断网方式, 一眼能看出是不是"代理-全屏蔽 (Proxy block-all)"
        text = f"{text}  ·  {tr(self.settings.display_value('NET_MODE', config.NET_MODE))}"
        cfg(self.net_lbl_status, text=text)
        if self.net_dot_canvas.itemcget(self.net_dot_id, "fill") != color:
            self.net_dot_canvas.itemconfig(self.net_dot_id, fill=color)

    def _net_bar_set(self, n):
        mx, val = max(1.0, n.today_total), float(n.quota_used)
        try:
            if float(self.net_bar["maximum"]) != mx:
                self.net_bar["maximum"] = mx
            if abs(float(self.net_bar["value"]) - val) >= 0.5:
                self.net_bar["value"] = val
        except Exception:
            self.net_bar["maximum"] = mx
            self.net_bar["value"] = val

    def refresh_net(self, force: bool = False):
        n = self.net
        # 开发者模式在界面上不显示: 照常显示成离线
        mode = "offline" if n.mode == "dev" else n.mode

        if mode != self._net_view or force:
            self._net_view = mode
            # 只在需要时才收起 / 显示 (重复 pack 会触发整页重新布局)
            if mode == "offline":
                hide_pack(self.net_ctrl)
                show_pack(self.net_form, anchor="w", fill="x")
            else:
                hide_pack(self.net_form)
                show_pack(self.net_ctrl, anchor="w")
                cfg(self.btn_net_ctrl,
                    text=tr("✕  取消申请") if mode == "pending" else tr("■  结束本次会话")
                )
                if mode == "pending":
                    cfg(self.btn_net_upgrade,
                        text=tr("⚡  改为快速访问(×{a:g})").format(a=config.QUICK_MULTIPLIER))
                    show_pack(self.btn_net_upgrade, side="left", padx=(8, 0))
                else:
                    hide_pack(self.btn_net_upgrade)

        if mode == "offline":
            self._net_set_status(tr("离线"), COLORS["text_dim"])
            cfg(self.net_lbl_time, text=tr("离线"))
            if n.remaining < 60:
                cfg(self.net_lbl_sub, text=tr("今天的额度已用完."))
                cfg(self.btn_net_req, state="disabled")
            else:
                cfg(self.net_lbl_sub, text="")
                cfg(self.btn_net_req, state="!disabled")
            cfg(self.btn_net_quick,
                text=tr("⚡  快速访问(×{a:g})").format(a=config.QUICK_MULTIPLIER),
                state="!disabled" if n.quick_left >= 60 else "disabled")
            cfg(self.btn_net_loan,
                state="!disabled" if n.loan_left >= 60 else "disabled")
        elif mode == "pending":
            p = n.pending
            self._net_set_status(tr("冷静期中 —— 仍可取消"), COLORS["warn"])
            cfg(self.net_lbl_time, text=fmt_ms(p["at"] - time.time()))
            start_at = datetime.fromtimestamp(p["at"]).strftime("%H:%M")
            cfg(self.net_lbl_sub,
                text=tr("已申请 {m} 分钟 · {at} 联网").format(m=p['minutes'], at=start_at)
            )
        else:
            self._net_set_status(tr("联网中"), COLORS["success"])
            cfg(self.net_lbl_time, text=fmt_ms(n.grant_left))
            if n.grant_quick:
                cfg(self.net_lbl_sub,
                    text=tr("⚡ 快速访问 ×{a:g}").format(
                        a=n.grant_rate)
                )
            else:
                cfg(self.net_lbl_sub, text="")

        self._net_bar_set(n)
        cfg(self.net_lbl_quota,
            text=tr("今日已用: {used} / {total}     今日剩余: {left}     可快速访问: {quick}").format(
                used=fmt_hm(n.quota_used), total=fmt_hm(n.today_total),
                left=fmt_hm(n.remaining), quick=fmt_hm(n.quick_left))
        )
        bits = []
        mult = n.quota_multiplier
        if abs(mult - 1.0) > 1e-9:
            bits.append(tr("今天的联网倍率 ×{m}: 每日额度 {base} → 今天 {quota}").format(
                m=f"{mult:g}", base=fmt_hm(float(config.DAILY_LIMIT)), quota=fmt_hm(n.limit)))
        if abs(n.outing_factor - 1.0) > 1e-9:
            bits.append(tr("外出: 今天只能用总额度的 {p}% (总额度 {full} → 能用 {total})").format(
                p=f"{n.outing_factor * 100:g}", full=fmt_hm(n.full_total), total=fmt_hm(n.today_total)))
        if n.outing_cooldown < 1.0:
            bits.append(tr("外出中: 联网冷静期 ×{f}").format(f=f"{n.outing_cooldown:g}"))
        if n.bonus_all > 0:
            bits.append(tr("今天已借 {bonus},明天将扣 {cost}(明天剩余 {left})").format(
                bonus=fmt_hm(n.bonus_all), cost=fmt_hm(n.tomorrow_used_all),
                left=fmt_hm(n.tomorrow_remaining)))
        if n.debt_all > 0:
            bits.append(tr("今天要先还昨天借的 {debt}").format(debt=fmt_hm(n.debt_all)))
        if n.gift_all > 0:
            bits.append(tr("今天合约奖励的联网额度 {t}", t=fmt_hm(n.gift_all)))
        cfg(self.net_lbl_debt, text="\n".join(bits))

    def _safe_minutes(self):
        try:
            return int(self.var_min.get())
        except (TypeError, ValueError):
            return 0

    def _preset_rule_keys(self):
        """过滤名单模式的切换在排队时: 新模式里(当前模式没有的)那些名单的 key —— 可以提前设置, 改动立即保存 (MECHANICS 4.4)."""
        try:
            from netproxy.rules import keys_for_mode, WHITE_UPS, MODE_WHITELIST
            st = self.settings.staged("FILTER_MODE")
            if not st:
                return frozenset()
            new = st["value"]
            keys = set(keys_for_mode(new)) - set(keys_for_mode(getattr(config, "FILTER_MODE", "blacklist")))
            if new == MODE_WHITELIST and keys:
                keys.add(WHITE_UPS)
            return frozenset(keys)
        except Exception:
            return frozenset()

    def _on_net_tamper(self, kind: str):
        """系统代理被人改了 (关掉 / 换成别的代理), NetGuard 已经改回去了: 提示一次 (30 秒内不重复)."""
        now = time.monotonic()
        if now - getattr(self, "_tamper_toast_at", -999.0) < 30:
            return
        self._tamper_toast_at = now
        toast.warn(tr("系统代理被改动了, 已经改回去."))

    def _check_source(self):
        """后台线程: 程序文件和清单对不上 (不是更新) → 回到界面线程处理."""
        if self.shutting_down or self._cont_broken or not srcguard.enabled():
            return
        try:
            bad = srcguard.running_check()
        except Exception as e:
            self._log_window(f"ERROR    srcguard {type(e).__name__}: {e}")
            return
        if bad:
            try:
                self.root.after(0, lambda: self._source_modified(bad))
            except Exception:
                pass

    def _source_modified(self, bad):
        """源码被非更新地改动: 和数据被改过一样, 在锚点里标记 pending, 马上重启, 下一次启动清数据 (启动时 srcguard.verify 也会发现)."""
        if self._cont_broken or self.shutting_down or self.dm.resetting or continuity.current is None:
            return
        self._cont_broken = True
        why = "source modified: " + ", ".join(bad[:8])
        self._log_notice("SOURCE " + why)
        continuity.current.mark_pending(why)
        self._restart_for_update()

    def _check_clock(self):
        """系统时间被改 (MECHANICS 9.6): 第一次发现弹窗警告, 10 分钟内改回来就解除; 没改回来 / 又改了一次就清数据"""
        if self.clock is None or self._cont_broken or self.shutting_down or self.dm.resetting:
            return
        try:
            ev = self.clock.tick()
        except Exception as e:
            self._log_window(f"ERROR    clockguard {type(e).__name__}: {e}")
            return
        if ev == "warn":
            self._log_notice("CLOCK modified: warned")
            who = continuity.current.username() if continuity.current is not None else ""
            toast.notice(tr("⚠  系统时间"), tr("{name},这没有意义.你可以在10min内修改回来,否则你的数据会被清除.", name=who),
                         [(tr("知道了"), None, True)])
        elif ev == "restored":
            self._log_notice("CLOCK restored")
            toast.success(tr("系统时间已改回来."))
        elif ev == "wipe" and continuity.current is not None:
            # 和数据被改过 / 倒回去一样: 在锚点里标记, 马上重启, 下一次启动时清掉数据并弹出 "<用户名>,覆水难收."
            self._cont_broken = True
            self._log_notice("CLOCK modified: wiping")
            continuity.current.mark_pending("system clock modified")
            self._restart_for_update()

    def _net_tick(self):
        if self.shutting_down:
            return
        now = time.monotonic()
        dt = now - self._net_last
        self._net_last = now
        event = self.net.tick(dt)
        self.refresh_net()
        self._sync_break_lock()
        if "rules" in self.pages:
            self.pages["rules"].refresh()
        self._comm_tick()
        self._alarm_tick()
        if self.current_page == "settings" and "settings" in self.pages:
            self.pages["settings"].refresh_sync()
        if event == "started" and self._update_retry and getattr(config, "UPDATE_CHECK", 1):
            self._update_retry = False
            self.check_update(auto=True)
        self._beat_power()
        self._check_desktop()
        if time.monotonic() - self._battery_at >= 20:   # 本机电量: 发给其他设备 (MECHANICS 15.4)
            self._battery_at = time.monotonic()
            self.battery = devsync.battery_percent()
        if securefile.tampered and not self._cont_broken and continuity.current is not None and not self.shutting_down and not self.dm.resetting:
            # 数据文件被改过 / 删了 / 倒回去了 (securefile.py, MECHANICS 9.4): 在锚点里标记, 马上重启, 下一次启动时清掉数据
            self._cont_broken = True
            self._log_notice("SECURE tampered " + ", ".join(f"{n}:{w}" for n, w in securefile.tampered[:8]))
            continuity.current.mark_pending("tampered: " + ", ".join(f"{n}:{w}" for n, w in securefile.tampered[:8]))
            self._restart_for_update()
        self._check_clock()
        if time.monotonic() - self._src_at >= 300:
            self._src_at = time.monotonic()
            threading.Thread(target=self._check_source, daemon=True).start()
        if time.monotonic() - self._purge_at >= 3600:
            self._purge_at = time.monotonic()
            threading.Thread(target=self._purge_recordings, daemon=True).start()
        if time.monotonic() - self._cont_at >= continuity.TICK:
            self._cont_at = time.monotonic()             # 数据的连续性: 序号推进一次 (MECHANICS 9.3)
            if continuity.current is not None:
                threading.Thread(target=continuity.current.tick, daemon=True).start()
        if time.monotonic() - self._link_at >= 15:
            self._link_at = time.monotonic()             # 联动程序 (15.8) 在不在: 15 秒看一次 (它也看着本机的 OIT, 互相拉起来)
            threading.Thread(target=self._check_link, daemon=True).start()
        if time.monotonic() - self._autostart_at >= 300 and self.autostart_state != "unknown":
            self._autostart_at = time.monotonic()        # 开机自启动的任务有没有被禁用 / 删掉 (有就重建): 5 分钟看一次
            threading.Thread(target=self._check_autostart, daemon=True).start()
        try:
            self._check_adapter_overlap()
            self._check_away_priority()
            self._check_contract_conflicts()
        except Exception as e:
            self._log_window(f"ERROR    adapter overlap {type(e).__name__}: {e}")
        self.sync.track_offline()               # 每秒: 同步失败的设备马上算离线 (MECHANICS 8.4)
        try:
            # 设备离线了: 马上请它重新启动 OIT (15.3); 外出期间的设备不算离线
            outing = self.behavior.current()
            self.sync.check_restart(hold=outing is not None)
            # 联动程序 (15.8) 在 OIT 关掉后替本机收启动请求: 外出的计划结束之前别启动 (外出期间的设备不算离线)
            linkd.write_hold(self.dm.data_dir, int(outing["end"].timestamp() * 1000) if outing is not None else 0)
        except Exception as e:
            self._log_window(f"ERROR    restart request {type(e).__name__}: {e}")
        if time.monotonic() - self._outbox_at >= 30:   # 没发出去的录像片段: 每 30 秒再试一次 (18.8)
            self._outbox_at = time.monotonic()
            if os.path.exists(self._outbox_path()):
                self._flush_outbox()
        try:
            self._check_contract()
        except Exception as e:
            self._log_window(f"ERROR    contract {type(e).__name__}: {e}")
        self._sync_bonus_button()               # "我累了": 本周领完了就藏起来, 新的一周 (或同步来的领取记录) 随之变化
        if self.current_page == "timer" and hasattr(self, "lbl_peers"):
            text, off = self._peer_line()
            cfg(self.lbl_peers, text=text, fg=COLORS["warn"] if off else COLORS["text_dim"])
        self.net_job = self.root.after(1000, self._net_tick)

        if event:
            self._refresh_if_visible("stats")
        if event == "started":
            toast.success(tr("网络已开启."))
        elif event == "ended":
            toast.warn(tr("时间到,网络已断开."))
