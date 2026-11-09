"""休息 (MECHANICS 6): 奖励休息 / 开始与暂停休息 / 休息结束后的提醒 / 结转.

从 app.py 的 BreakTimerApp 拆出来的 mixin: 状态都在 self 上, 这里只放方法.
计时状态机、专注结算和每秒 tick 在 app_timer.py.
"""

import time
from datetime import datetime, timedelta

import config
import contract as contract_mod
import daily
import toast
from app_base import tired_text
from config import COLORS, IDLE, ON_BREAK, RUNNING, fmt_hm, fmt_ms, load_json_file
from i18n import tr


class BreakMixin:
    # ---- 连点 10 次换休息时间 ----

    def _bonus_state(self) -> dict:
        d = load_json_file(self.timer_file, {})
        return d if isinstance(d, dict) else {}

    def _bonus_left_this_week(self, now: datetime = None) -> int:
        """本周 (周一 0 点起) 还能领多少秒."""
        now = now or datetime.now()
        week = "%d-W%02d" % now.isocalendar()[:2]
        d = self._bonus_state()
        used = int(d.get("bonus_used", 0) or 0) if d.get("bonus_week") == week else 0
        return max(0, int(config.BONUS_BREAK_WEEKLY) - used)

    def _on_bonus_click(self):
        if self.contract.active:
            return                          # 合约中"我累了"按钮隐藏
        now = time.monotonic()
        window = float(config.BONUS_BREAK_WINDOW)
        if self._bonus_clicks == 0 or now - self._bonus_first > window:
            # 从第一次点击开始计时; 超过 1 分钟还没点满就从头再来
            self._bonus_clicks = 0
            self._bonus_first = now
            if self._bonus_job:
                try:
                    self.root.after_cancel(self._bonus_job)
                except Exception:
                    pass
            self._bonus_job = self.root.after(int(window * 1000) + 50, self._bonus_reset)
        self._bonus_clicks += 1
        need = int(config.BONUS_BREAK_CLICKS)
        if self._bonus_clicks < need:
            self._bonus_show(tr("☕  我累了 ({i})").format(i=self._bonus_clicks))
            return
        self._bonus_reset()
        self._grant_bonus_break()

    def _bonus_reset(self):
        self._bonus_clicks = 0
        if self._bonus_job:
            try:
                self.root.after_cancel(self._bonus_job)
            except Exception:
                pass
        self._bonus_job = None
        self._bonus_show(self._bonus_idle_text())

    def _bonus_idle_text(self) -> str:
        # 按钮上只写几个字, 不写具体规则 (规则见 MECHANICS.md 6.5)
        if self._bonus_left_this_week() <= 0:
            return tr("☕  本周已领完")
        return tr("☕  我累了")

    def _bonus_show(self, text: str):
        try:
            self.lbl_bonus.config(text=text)
        except Exception:
            pass

    def _grant_bonus_break(self):
        now = datetime.now()
        left = self._bonus_left_this_week(now)
        if left <= 0:
            toast.warn(tr("这一周的 {h} 已经领完了,下周一再来.").format(
                h=fmt_hm(config.BONUS_BREAK_WEEKLY)))
            return
        add = min(int(config.BONUS_BREAK_SECONDS), left)
        week = "%d-W%02d" % now.isocalendar()[:2]
        d = self._bonus_state()
        used = int(d.get("bonus_used", 0) or 0) if d.get("bonus_week") == week else 0
        count = (int(d.get("bonus_count", 0) or 0) if d.get("bonus_week") == week else 0) + 1
        self._timer_extra = {"bonus_week": week, "bonus_used": used + add, "bonus_count": count}
        self._mine()
        if self.state == ON_BREAK:
            self.countdown_seconds += add          # 正在休息: 直接加到这次休息里
            if self.break_end_wall is not None:
                self.break_end_wall += timedelta(seconds=add)
            self._bonus_break_active = True
            self._save_timer_state()
            where = tr("已加到这次休息里.")
            where += "\n" + tr("这次休息可以随时暂停,剩余时间不打折.")
        else:
            # 存进积攒的休息时间; 用到它的下一次休息享受"可暂停、不打折"
            self._timer_extra["bonus_pending"] = True
            self._set_break_carry(self.break_carry + add)
            where = tr("已存入积攒的休息时间.")
            where += "\n" + tr("下一次休息可以随时暂停,剩余时间不打折.")
        msg = tr("获得 {t} 休息时间.").format(t=fmt_ms(add)) + where
        msg += "\n" + tr("本周还能领 {left}.").format(left=fmt_hm(left - add))
        msg += "\n\n" + tr("这么想休息,也许是真的累了.休息不需要理由.")
        msg += "\n" + tr("不要勉强.")
        toast.success(msg, timeout=10000)
        if self.state == IDLE:
            self.lbl_sub.config(text=self._idle_sub_text())
        self._bonus_show(self._bonus_idle_text())
        self._sync_timer_buttons()
        if count == int(config.BONUS_TIRED_TIMES):
            self._tired_notice()

    def _tired_notice(self):
        """一周内第 5 次领"我累了" (MECHANICS 6.5): 劝去调休息时间倍率, 它的下一次修改免等待
        (已经有排队中的修改时, 那一项立即生效)."""
        if self.settings.make_due_now("BREAK_TIME_SCALE"):
            self._log_notice("BREAK_TIME_SCALE pending change applied now — 5 bonus breaks this week")
            self.check_pending_settings()
        elif self.settings.grant_free_change("BREAK_TIME_SCALE"):
            self._log_notice("BREAK_TIME_SCALE next change free — 5 bonus breaks this week")
        self.update_settings_badge()
        if "settings" in self.pages:
            self.pages["settings"].refresh()
        toast.notice(tr("☕  来自 Obliphur"), tired_text(self.dm.username), [
            (tr("去设置"), self._open_detail_settings, True), (tr("知道了"), None, False)])

    def _sleep_notice(self):
        """睡觉时间之后 3 小时内第一次暂停休息: 提醒去睡觉 (MECHANICS 19)."""
        toast.notice(tr("🌙  该睡觉了"), daily.sleep_text(self.dm.username),
                     [(tr("晚安"), None, True)])

    def _start_break(self, announce: bool = False):
        """开始休息倒计时 = 建议休息时长 + 之前暂停休息结转的时间."""
        # 结束专注时提前启动的程序全部放出来 (不等下一秒的 tick, 也不依赖 tick 还活着)
        if self.prestart.active:
            self._stop_prestart()
        total = self._break_seconds()
        if total <= 0:
            toast.info(tr("剩余时间为 0,已重置."))
            self.reset_to_idle()
            return
        carry = int(self.break_carry)
        tag, self._rest_tag = self._rest_tag, ""
        c = self.contract
        # 合约专注的下一次休息, 合约已经完成了: 仍按合约规则 —— 这段专注换来的是合约休息时间 (暂停时清空),
        # 积攒的休息时间 (合约开始前剩下的 + 完成奖励) 是正常休息时间, 放在最后用, 暂停时原样留下不打折
        after = (not c.active) and c.completed(tag)
        bonus = bool(self._bonus_state().get("bonus_pending"))
        if bonus:
            self._bonus_break_active = True
            self._timer_extra = {"bonus_pending": False}
        if not c.active and not after and c.take_first_break():
            self._contract_break_active = True
            toast.info(tr("合约完成后的第一次休息: 暂停时剩余时间 ×{r:g} 留给下次.",
                          r=contract_mod.Contract.first_break_rate(config.BREAK_CARRY_RATE)), timeout=8000)
        self._break_pre = c.pre_take(carry) if c.active else 0.0
        self._break_cid = c.live.get("id") if c.active else None
        self._contract_rest_over = False
        if after:
            self._break_pre, self._break_cid, self._contract_rest_over = float(carry), tag, True
        self._supply_warned = set()
        self.auto_rest = 0                       # 一起用进这次休息, 结束休息时照常折算 (6.8)
        if carry > 0 or bonus:
            self._set_break_carry(0)
        self.countdown_seconds = total
        self._break_warned = False
        self.state = ON_BREAK
        self.break_start_wall = datetime.now().replace(microsecond=0)
        self.break_end_wall = self.break_start_wall + timedelta(seconds=total)
        if bonus:
            toast.info(tr("这次休息可以随时暂停,剩余时间不打折."),
                       timeout=8000)
        self._show_on_break()
        self.schedule_update()
        if announce:
            msg = tr("本次专注 {m:.1f} 分钟,开始休息 {t}.").format(
                m=self.last_t_minutes, t=fmt_ms(total))
            if carry > 0:
                msg += "\n" + tr("(含之前暂停休息结转的 {c})").format(c=fmt_ms(carry))
            toast.success(msg)

    # ---- 休息: 建议时长 + 之前暂停休息结转的时间 ----

    def _break_seconds(self) -> int:
        """本次休息总秒数 = 建议休息时长 + 结余."""
        base = int(round(max(0.0, self.rest_base)))
        total = base + int(self.break_carry)
        return max(1, total) if total > 0 else 0

    def _show_ready_break(self):
        self._sync_timer_buttons()
        total = self._break_seconds()
        self.lbl_time.config(text=fmt_ms(total))
        sub = tr("已工作 {m:.2f} 分钟").format(m=self.last_t_minutes)
        if self.break_carry > 0:
            sub += "\n" + tr("(含之前暂停休息结转的 {c})").format(c=fmt_ms(self.break_carry))
        self.lbl_sub.config(text=sub)
        self._status(tr("本次专注结束"), COLORS["success"])

    def _show_on_break(self):
        self._sync_timer_buttons()
        self._status(tr("休息中 ☕"), COLORS["warn"])
        self.lbl_sub.config(text=tr("休息吧,倒计时中…"))
        self._sync_break_lock()

    def _pause_block(self) -> str:
        """不能暂停休息的原因 ("" = 可以): 网络正在使用 (领到休息时间后的第一次休息除外)."""
        if self.state != ON_BREAK:
            return ""
        if self.net.mode == "online" and not self._bonus_break_active:
            return tr("网络正在使用,不能暂停休息.")
        return ""

    def _break_locked(self) -> bool:
        return bool(self._pause_block())

    def _sync_break_lock(self):
        if self.state == ON_BREAK:
            self._sync_timer_buttons()

    def end_break_early(self, silent: bool = False):
        """暂停休息(关闭程序也算):剩余时间 ×BREAK_CARRY_RATE 计入下一次休息."""
        if self.state != ON_BREAK:
            return
        if not silent and self._break_locked():
            toast.warn(self._pause_block())
            return
        if silent and not self._timer_auto_ok():
            self.break_start_wall = None
        self._record_break_end()
        left = max(0, int(self.countdown_seconds))
        full = self._bonus_break_active
        rate = float(config.BREAK_CARRY_RATE)
        if self._contract_break_active:
            rate = contract_mod.Contract.first_break_rate(rate)
        # 睡觉时间 (MECHANICS 19): 窗口内、非合约期间的第一次暂停休息, 结转比例 +5%
        night = (not silent and not self.contract.active
                 and daily.take_break_notice(self.dm, datetime.now()))
        if night:
            rate = daily.break_rate(rate)
        self._bonus_break_active = False
        self._contract_break_active = False
        # 合约中: 剩余时间里合约开始前积攒的那部分 (放在最后用) 原样还回去, 不打折;
        # 合约中开始的休息, 合约已经结束了: 只还这部分 (合约中得到的休息时间不留到合约之后)
        pre = min(left, int(self._break_pre))
        c = self.contract
        same = c.active and c.live.get("id") == self._break_cid
        ended = self._break_cid is not None and not same
        if same:
            c.pre_settle(self._break_pre, pre)
        self._break_pre, self._break_cid = 0.0, None
        if ended:
            add = pre
        # 领到休息时间后的第一次休息: 暂停时剩余时间全部保留, 不按倍率打折 (盖过合约完成后的比例)
        elif full:
            add = left
        else:
            add = pre + int(round((left - pre) * rate))
        if add > 0:
            self._set_break_carry(self.break_carry + add)
            if not silent:
                if full or add == left:
                    toast.info(tr("休息已暂停,剩余 {left} 留给下次.").format(
                        left=fmt_ms(left)))
                else:
                    toast.info(tr("休息已暂停,下次休息 +{add}.").format(
                        left=fmt_ms(left), r=rate, add=fmt_ms(add)))
        if silent:
            self.state = IDLE
            self.countdown_seconds = 0
            self._contract_rest_over = False
        else:
            self.reset_to_idle()
            self._arm_idle_nudge()
            # 禁止囤囤鼠!: 提前结束休息照样能结束, 算一次违反 (软规则)
            tid = c.hoard_check()
            if tid:
                self.contract_violation(tr("提前结束了休息"), tid)
            if night:
                self._sleep_notice()

    def _record_break_end(self):
        """一段休息结束 (自然结束 / 暂停 / 关闭程序): 记入专注计时统计."""
        if self.break_start_wall is not None:
            self.dm.add_focus_session("break", self.break_start_wall, datetime.now())   # 有设备离线也照常记
            self.break_start_wall = None

    # ---- 休息结束后迟迟不开始: 提醒 ----

    def _arm_idle_nudge(self):
        """休息结束 (自然结束或暂停) 回到空闲时调用, 从此刻开始计时."""
        self.idle_since = datetime.now()
        self._nudges = 0

    def _check_idle_nudge(self):
        """每 5 秒检查一次. 第一次在空闲 IDLE_NUDGE_FIRST 秒后, 之后每 IDLE_NUDGE_EVERY 秒一次;
        按墙上时间算, 电脑睡眠醒来后错过的几次只补弹一次."""
        if self.idle_since is None:
            return
        if self.behavior.active() is not None:
            return                          # 外出中不提醒"该开始下一轮了"
        if self.sleep_since is not None:
            return                          # 睡眠中也不提醒
        if self.state != IDLE:
            self.idle_since = None          # 已经开始下一轮 (或别的状态), 不再提醒
            return
        if not self.sync.is_latest_active():
            return                          # 只在最后有人操作的设备上提醒
        now = datetime.now()
        first = timedelta(seconds=max(1, int(config.IDLE_NUDGE_FIRST)))
        every = timedelta(seconds=max(60, int(config.IDLE_NUDGE_EVERY)))
        due = self.idle_since + first + every * self._nudges
        if now < due:
            return
        while due <= now:
            self._nudges += 1
            due += every
        if self._nudge_win is not None and self._nudge_win.alive():
            return                          # 上一个提醒还开着, 不重复弹
        idle = fmt_hm((now - self.idle_since).total_seconds())
        self._nudge_win = toast.notice(
            tr("⏱  该开始下一轮了"),
            tr("休息结束已经 {t} 了,还没有开始下一轮专注.").format(t=idle),
            [(tr("▶  开始专注"), self._start_from_nudge, True), (tr("知道了"), None, False)])

    def _start_from_nudge(self):
        if self.state == IDLE and self.behavior.active() is None:
            self.show_page("timer")
            self.on_action()

    def _focus_live(self):
        """正在进行的一段, 供 Statistics 页画到此刻: (kind, 开始时刻) 或 None."""
        if self.state == RUNNING and self.start_wall is not None:
            return ("study", self.start_wall)
        if self.state == ON_BREAK and self.break_start_wall is not None:
            return ("break", self.break_start_wall)
        return None

    def _calendar_live(self):
        """日历用: 正在进行的一段, 比 _focus_live 多一种睡眠 (kind, 开始时刻)."""
        if self.sleep_since is not None:
            return ("sleep", self.sleep_since)
        return self._focus_live()

    def _idle_sub_text(self) -> str:
        if self.break_carry > 0:
            text = tr("积攒的休息时间: {c}").format(
                c=fmt_ms(self.break_carry))
            if self.auto_rest > 0:
                text += "\n" + tr("立即开始专注:先进行一次休息时间的折算")
            return text
        return ""

    def _load_break_carry(self) -> int:
        return self._load_timer_int("break_carry")

    def _load_timer_int(self, key: str) -> int:
        d = load_json_file(self.timer_file, {})
        try:
            return max(0, int(d.get(key, 0))) if isinstance(d, dict) else 0
        except (TypeError, ValueError):
            return 0

    def _set_break_carry(self, seconds: int):
        self.break_carry = max(0, int(seconds))
        self.auto_rest = min(int(getattr(self, "auto_rest", 0)), self.break_carry)
        self._save_timer_state()

    def _add_auto_rest(self, seconds: int):
        """非手动结束的专注: 休息时间存进积攒的休息时间, 记下这部分还没折算 (6.8)."""
        self.auto_rest = min(self.break_carry, self.auto_rest + max(0, int(seconds)))
        self._save_timer_state()

    def _settle_auto_rest(self, paused: bool = False):
        """手动开始专注 (6.8): 非手动结束的专注留下的那部分休息时间, 先按暂停休息的结转比例折算一次.
        paused: 合约开始时暂停了正在进行的专注 (_pause_focus_for_contract), 提示文字不同."""
        a = min(int(self.auto_rest), int(self.break_carry))
        self.auto_rest = 0
        if a <= 0:
            self._save_timer_state()
            return
        keep = int(round(a * float(config.BREAK_CARRY_RATE)))
        self._set_break_carry(self.break_carry - (a - keep))
        if self.contract.active:
            self.contract.pre_cap(self.break_carry)
        text = tr("休息时间折算: 这段专注换来的 {a} ×{r:g} = {k}.") if paused else tr("休息时间折算: 专注自动结束留下的 {a} ×{r:g} = {k}.")
        toast.info(text.format(a=fmt_ms(a), r=float(config.BREAK_CARRY_RATE), k=fmt_ms(keep)), timeout=8000)
