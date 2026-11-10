"""计时器 (MECHANICS 6): 状态机入口 / 专注状态存档与恢复 / 专注结算 / 每秒 tick / 跨天 / 整点.

从 app.py 的 BreakTimerApp 拆出来的 mixin: 状态都在 self 上, 这里只放方法.
休息在 app_break.py; 睡觉在 app.py (MECHANICS 23).
"""

import os
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, time as dt_time, timedelta

import config
import contract as contract_mod
import devsync
import toast
from app_base import _play_audio_file
from config import (COLORS, IDLE, ON_BREAK, READY_BREAK, RUNNING,
                    compute_F, ensure_builtin_chime, fmt_hm, fmt_ms,
                    load_json_file, save_json_file)
from i18n import tr


class TimerMixin:
    def _set_dot(self, color_hex: str):
        self.dot_canvas.itemconfig(self.dot_id, fill=color_hex)

    def _status(self, text: str, dot_color: str):
        self.lbl_status.config(text=text)
        self._set_dot(dot_color)

    def on_action(self):
        """主按钮: 空闲 → 开始专注; 专注中 → 结束专注 (直接进入休息);
        休息中 → 暂停休息并马上开始专注."""
        self._mine()
        if self.behavior.active() is not None and self.state == IDLE:
            toast.info(tr("外出中, 先结束外出再开始专注."))
            return
        if self.state == IDLE:
            self._settle_auto_rest()             # 非手动结束的专注留下的休息时间: 先折算一次 (6.8)
            self._enter_focus(datetime.now(), time.monotonic())
            if self.contract.slow_bind(self.start_wall):
                toast.warn(tr("阻滞: 合约内这段专注的时间按 {p}% 增长", p=int(round(contract_mod.RANDOM_SLOW * 100))),
                           timeout=10000)
                if self.sync.peers():
                    self.sync.sync_now()

        elif self.state == RUNNING:
            end_wall = datetime.now()
            # 阻滞: 只减慢合约内那部分专注的时间 (MECHANICS 18.3)
            elapsed_minutes = self.contract.focus_minutes(
                self.start_wall, time.monotonic() - self.start_mono)
            self.last_t_minutes = elapsed_minutes      # 建议休息时长照常按整段专注算
            self.rest_base = self._focus_rest(elapsed_minutes, self.start_wall, end_wall)
            off = self._cut_offline(self.start_wall, end_wall)
            self.dm.add_study_session(self.start_wall, end_wall, max(0.0, elapsed_minutes - off / 60.0))
            self._add_study_pieces(self.start_wall, end_wall)
            self._close_check_win()
            self.state = READY_BREAK          # 专注已记下: 先清掉文件里的专注状态
            self._save_timer_state()
            self.refresh_display()

            self.cancel_update()
            # 合约专注 (开始于合约里) 的下一次休息按合约规则 (合约在专注中完成了也一样, MECHANICS 18.2)
            self._rest_tag = self.contract.focus_tag(self.start_wall)
            # 结束专注后直接进入休息, 不再停在"去休息"这一步
            self._start_break(announce=True)
            # 纵深推进: 这段专注不到要求的时长, 算一次违反
            tid = self.contract.focus_check(elapsed_minutes * 60)
            if tid:
                self.contract_violation(tr("专注只有 {m} 分钟 (至少 {n} 分钟)", m=int(elapsed_minutes),
                                           n=contract_mod.DEEP[tid] // 60), tid)

        elif self.state == READY_BREAK:
            # 旧流程留下的状态 (正常不会再进入): 按一下照样开始休息
            self._start_break()

        elif self.state == ON_BREAK:
            if self._break_locked():
                toast.warn(self._pause_block())
                return
            # 休息中直接开始专注: 上一段专注的提前启动要接着 (见 _enter_focus; MECHANICS 20)
            self.end_break_early()
            if self.state == IDLE:
                self._focus_from_break = True
                self.on_action()

    def on_break_button(self):
        """副按钮: 空闲 → 用积攒的休息时间直接开始休息; 休息中 → 暂停休息."""
        self._mine()
        if self.behavior.active() is not None and self.state == IDLE:
            toast.info(tr("外出中, 先结束外出再休息."))
            return
        if self.state == IDLE:
            if self.break_carry <= 0:
                toast.info(tr("还没有积攒的休息时间."))
                return
            self.last_t_minutes = 0.0
            self.rest_base = 0.0
            self._rest_tag = ""                  # 不是接着专注的休息: 按普通规则
            total = int(self.break_carry)
            self._start_break()
            if self.state == ON_BREAK:
                toast.success(tr("开始休息 {t}.").format(t=fmt_ms(total)))
        elif self.state == ON_BREAK:
            self.end_break_early()

    def _sync_timer_buttons(self):
        """按当前状态设置两个按钮的文字与可用状态; 提前启动按钮只在专注中、还没选过时出现."""
        if not hasattr(self, "btn_break"):
            return
        locked = self._break_locked()
        outing = self.behavior.active()
        try:
            if outing is not None:
                self.btn_action.config(text=tr("🚪  外出中"), state="disabled")
                self.btn_break.config(text=tr("☕  开始休息"), state="disabled")
            elif self.state == RUNNING:
                self.btn_action.config(text=tr("■  结束专注"), state="!disabled")
                self.btn_break.config(text=tr("☕  开始休息"), state="disabled")
            elif self.state == ON_BREAK:
                st = "disabled" if locked else "!disabled"
                self.btn_action.config(text=tr("▶  开始专注"), state=st)
                self.btn_break.config(text=tr("⏸  暂停休息"), state=st)
            elif self.state == READY_BREAK:
                self.btn_action.config(text=tr("☕  去休息"), state="!disabled")
                self.btn_break.config(text=tr("☕  开始休息"), state="disabled")
            else:
                self.btn_action.config(text=tr("▶  开始专注"), state="!disabled")
                self.btn_break.config(text=tr("☕  开始休息"),
                                      state="!disabled" if self.break_carry > 0 else "disabled")
        except Exception:
            pass
        self._sync_prestart_button()
        self._sync_outing_button()
        self._sync_sleep_button()
        self._sync_prescript_marks()

    def _sync_sleep_button(self):
        """睡眠按钮: 只有空闲 (不在专注 / 休息 / 外出 / 合约) 时能按."""
        btn = getattr(self, "btn_sleep", None)
        if btn is None:
            return
        try:
            ok = (self.state == IDLE and self.behavior.active() is None and not self.contract.active
                  and self.sleep_since is None)
            btn.config(state="!disabled" if ok else "disabled")
        except Exception:
            pass

    def _save_timer_state(self):
        """timer_state.json: 积攒的休息时间 + 本周连点领取的记录 + 进行中的专注."""
        if self.dm.resetting:
            return
        d = self._bonus_state()
        d.update(getattr(self, "_timer_extra", None) or {})
        self._timer_extra = None
        d["break_carry"] = self.break_carry
        d["auto_rest"] = int(getattr(self, "auto_rest", 0))
        d["focus"] = self._focus_record()
        # 睡眠中: 关掉程序再打开时接着睡 (MECHANICS 23)
        d["sleep"] = ({"since": self.sleep_since.isoformat(timespec="seconds"), "owner": self.timer_owner}
                      if self.sleep_since is not None else None)
        # 进行中的休息: 关掉程序再打开时接着休息 (6.3)
        d["break"] = self.timer_live() if self.state == ON_BREAK and self.break_end_wall is not None else None
        self._focus_saved = time.monotonic()
        save_json_file(self.timer_file, d)

    # ---- 专注状态: 随时写进文件, 程序异常退出也不丢 ----

    def _focus_record(self):
        """进行中的专注: 开始时刻, 最后一次保存的时刻, 已关掉的提醒数, 最后一次确认时已专注的秒数."""
        if self.state != RUNNING or self.start_wall is None:
            return None
        return {"start": self.start_wall.isoformat(timespec="seconds"),
                "alive": datetime.now().isoformat(timespec="seconds"),
                "checks": int(self._focus_checks),
                "ok": int(self._focus_ok),
                # 重开时原样恢复, 同步的计时器状态才不会因为"主人变了"被当成新的改动 (盖掉其他设备更新的状态)
                "owner": self.timer_owner,
                # 关机 / 电量很低: 重新打开时不把关着的这段记成空闲, 跟着其他设备 (MECHANICS 15.4)
                "off": bool(self._went_off())}

    def _on_power_off(self):
        """Windows 要关机 / 注销了 (MECHANICS 15.4): 存下专注状态 (记上"关机"), 告诉其他设备本机关机了 ——
        它们把本机算作"关机" (效果和在线一样), 不会因为本机离线把专注记成空闲 / 让合约失败.
        关机被取消 (程序 1 分钟后还在运行) 时恢复正常."""
        self.power_off = True
        self._beat_power(force=True)
        self._save_focus_now()
        try:
            if self.sync.peers():
                self.sync.push_final(timeout=2.0)
        except Exception:
            pass
        try:
            self.root.after(60_000, self._power_off_cancelled)
        except Exception:
            pass

    # 关机的时间段 (MECHANICS 15.4): 每 30 秒写一次 power.json (此刻, 算不算关机); 重新打开时据此知道关了多久
    def _power_file(self) -> str:
        return os.path.join(self.dm.data_dir, "power.json")

    def _beat_power(self, force: bool = False):
        if not force and time.monotonic() - getattr(self, "_power_at", 0.0) < 30:
            return
        self._power_at = time.monotonic()
        save_json_file(self._power_file(), {"alive": int(time.time() * 1000), "off": bool(self._went_off())})

    def _note_power_back(self):
        """启动时: 上次是关机 / 没电停下的, 关着的这段 [最后一次心跳, 现在] 记下来 —— 本机之后记下的专注里这段记为空闲, 不计入统计."""
        d = load_json_file(self._power_file(), {})
        try:
            alive, off = int(d.get("alive") or 0), bool(d.get("off"))
        except (TypeError, ValueError, AttributeError):
            return
        now = int(time.time() * 1000)
        if off and alive and now - alive > float(config.CLOSED_GAP) * 1000:
            self.sync.add_self_off(alive, now)
            self._log_notice(f"POWER    was off {datetime.fromtimestamp(alive / 1000):%m-%d %H:%M} ~ now")
        self._beat_power(force=True)

    def _power_off_cancelled(self):
        self.power_off = False
        if self.state == RUNNING:
            self._save_timer_state()

    def _went_off(self) -> bool:
        """本机这次停下来算不算关机: 开着开机自启动, 并且收到了关机指令 / 电量很低 (见 devsync.went_off)."""
        return self.autostart_on and (self.power_off or (self.battery is not None and self.battery < devsync.LOW_BATTERY))

    def _save_focus_now(self):
        self.dm.save_window_usage()
        if self.state in (RUNNING, ON_BREAK) and not self.dm.resetting:
            try:
                self._save_timer_state()
            except Exception:
                pass

    def _enter_focus(self, start_wall: datetime, start_mono: float, checks: int = 0,
                     ok: float = 0.0):
        self.start_wall = start_wall
        self.start_mono = start_mono
        self._focus_checks = checks
        self._focus_ok = ok
        self._close_check_win()
        # 从休息接着专注 (点"开始专注" / 别的设备开始 / 暂停休息后马上开始): 上一段专注的提前启动原样接着 —
        # 不重新给 3 分钟操作空窗期, 否则程序在空窗期外会被应用名单关掉 (MECHANICS 20).
        # 从空闲开始的真正新专注: 上一段的不再沿用, 重新选.
        was_break = self.state == ON_BREAK or bool(getattr(self, "_focus_from_break", False))
        if self.prestart.active:
            self._stop_prestart(park=was_break)
        if was_break:
            self.prestart.resume_last()
        else:
            self.prestart.drop_last()
        self._focus_from_break = False
        self.state = RUNNING
        self._sync_prestart_exempt()
        self._status(tr("专注中…"), COLORS["accent"])
        self.lbl_sub.config(text="")
        self._sync_timer_buttons()
        self._save_timer_state()
        self.schedule_update()

    def _resume_break(self, live: dict, end: datetime, left: int):
        """照着计时器状态 (同步来的 / 关闭前存下的) 进入休息, 还剩 left 秒."""
        # 从专注进休息: 提前启动的程序马上放出来, 选过的留给休息中直接开始专注时接着用 (MECHANICS 20)
        if self.prestart.active:
            self._stop_prestart(park=True)
        self.state = ON_BREAK
        self.start_wall = None
        self.break_start_wall = datetime.fromisoformat(live["break_start"])
        self.break_end_wall = end
        self.countdown_seconds = left
        self._bonus_break_active = bool(live.get("bonus"))
        self._contract_break_active = bool(live.get("cbreak"))
        self._break_pre = float(live.get("cpre") or 0)
        self._break_cid = live.get("ccid")
        self._contract_rest_over = bool(live.get("crestover"))
        self._show_on_break()
        self.schedule_update()

    def _recover_break(self):
        """启动时: 上次关闭时在休息 → 还没到结束时刻就接着休息; 已经过了就记下这段休息, 回到空闲 (6.3)."""
        rec = self._bonus_state().get("break")
        if not isinstance(rec, dict) or rec.get("state") != "break":
            return
        now = datetime.now()
        try:
            start = datetime.fromisoformat(rec["break_start"])
            end = datetime.fromisoformat(rec["break_end"])
        except (KeyError, TypeError, ValueError):
            self._save_timer_state()          # 记录坏了: 丢掉
            return
        self.timer_owner = str(rec.get("owner") or "")
        left = int((end - now).total_seconds())
        if left > 0 and start <= now:
            self._resume_break(rec, end, left)
            self._save_timer_state()
            toast.info(tr("上次休息时程序退出了.已接着休息(到 {at}).", at=f"{end:%H:%M}"), timeout=8000)
            return
        if start < end <= now:
            self.dm.add_focus_session("break", start, end)
            c = self.contract
            if c.active and c.live.get("id") == rec.get("ccid"):
                c.pre_settle(float(rec.get("cpre") or 0), 0)
            self.idle_since, self._nudges = end, 0      # 休息结束后迟迟不开始的提醒照常从结束时刻算 (6.4)
        self._save_timer_state()

    def _recover_focus(self):
        """启动时: 上次退出时还在专注 → 不久前退出就接着计时, 否则记到最后一次保存为止."""
        rec = self._bonus_state().get("focus")
        if not rec:
            return
        try:
            start = datetime.fromisoformat(rec["start"])
            alive = datetime.fromisoformat(rec["alive"])
            checks = max(0, int(rec.get("checks", 0)))
            ok = max(0.0, float(rec.get("ok", 0)))
        except Exception:
            self._save_timer_state()          # 记录坏了: 丢掉
            return
        now = datetime.now()
        if start > now:                        # 系统时间被往回调过: 无法判断, 丢掉
            self._save_timer_state()
            return
        alive = min(max(alive, start), now)
        gap = (now - alive).total_seconds()
        self.timer_owner = str(rec.get("owner") or "")
        if rec.get("off") and gap > float(config.CLOSED_GAP):
            # 本机是关机 / 没电停下的 (MECHANICS 15.4): 效果和在线一样, 专注照常接着计时 ("还在吗"照常按开始时刻算);
            # 关着的这段本机的数据不计入 (见 _cut_offline). 绑定了其他设备时, 同步上以后跟着它们
            self._enter_focus(start, time.monotonic() - (now - start).total_seconds(), checks, ok)
            toast.info(tr("关机期间专注照常进行, 已接着计时."), timeout=8000)
            return
        if self.sync.peers() and gap > float(config.CLOSED_GAP):
            # 绑定了其他设备, 共享的专注中 OIT 被关掉了: 关掉的这段记为空闲 (总统计里优先级最高),
            # 专注照常恢复, 等第一次同步看其他设备是不是已经结束了它, 没有就由本机终止 (_check_close_pending).
            # 本机是关机 / 没电了 (MECHANICS 15.4): 其他设备把本机算作在线, 这段不记空闲; 同步上了就跟着它们
            off = bool(rec.get("off"))
            if not off:
                self.dm.add_focus_session("idle", alive, now, focus=start)
            self._enter_focus(start, time.monotonic() - (now - start).total_seconds(), checks, ok)
            self._close_pending = {"start": start, "alive": alive, "t": time.monotonic(),
                                   "ms": int(time.time() * 1000), "off": off, "now": now}
            return
        if gap <= float(config.FOCUS_RESUME_GAP):
            self._enter_focus(start, time.monotonic() - (now - start).total_seconds(), checks, ok)
            toast.info(tr("上次专注时程序退出了.已接着计时(从 {at} 开始).").format(
                at=f"{start:%H:%M}"), timeout=8000)
            return
        # 退出很久了: 专注记到最后一次保存为止, 对应的休息时间存进积攒的休息时间
        sec = (alive - start).total_seconds()
        limit = self._check_mark(checks + 1, start) + float(config.FOCUS_CHECK_GRACE)
        penalized = sec >= limit
        if penalized:
            sec = limit
        minutes, brk = self._book_focus(start, sec, ok if penalized else None)
        self._set_break_carry(self.break_carry + brk)   # 同时清掉文件里的专注状态
        self._add_auto_rest(brk)
        if not rec.get("off"):
            self._deep_check_closed(start, sec)
        self.refresh_display()
        if minutes <= 0:
            return
        if penalized:
            ok = min(ok, sec)
            msg = tr("上次专注时程序退出了,专注已按{at}自动结束.").format(
                at=f"{start + timedelta(seconds=sec):%m-%d %H:%M}")
            msg += "\n" + tr("没有确认的部分按×{p:g}计入,共计入{m:.1f}分钟.").format(
                since=f"{start + timedelta(seconds=ok):%H:%M}", seg=fmt_hm(sec - ok),
                p=float(config.FOCUS_CHECK_PENALTY), m=minutes)
        else:
            msg = tr("上次专注时程序退出了,专注记到 {at} 为止: 计入 {m:.1f} 分钟.").format(
                at=f"{alive:%m-%d %H:%M}", m=minutes)
        if brk > 0:
            msg += "\n" + tr("积攒的休息时间 +{t}.").format(t=fmt_ms(brk))
            msg += "\n" + tr("立即开始专注:先进行一次休息时间的折算")
        toast.notice(tr("⏱  上次的专注已记录"), msg)

    def _check_close_pending(self):
        """上次在共享的专注中 OIT 被关掉了 (_recover_focus): 第一次同步之后 (最多等 CLOSED_DECIDE_WAIT 秒),
        这段专注还在进行就终止它 —— 专注记到被关掉的时刻为止, 之后的那段已经记为空闲."""
        p = self._close_pending
        if not p:
            return
        if not self.sync.synced_since(p["ms"]) and \
                time.monotonic() - p["t"] < float(config.CLOSED_DECIDE_WAIT):
            return
        self._close_pending = None
        if self.state != RUNNING or self.start_wall != p["start"]:
            return                             # 其他设备已经结束了这段专注, 本机照着切换过了
        if p.get("off"):
            if self.sync.synced_since(p["ms"]):
                # 关机期间其他设备把本机算作在线, 共享的专注照常进行: 接着计时
                toast.info(tr("关机期间共享的专注照常进行, 已接着计时."), timeout=8000)
                return
            # 没能和任何设备同步上: 和普通的被关掉一样, 关着的那段记为空闲, 专注终止
            self.dm.add_focus_session("idle", p["alive"], p["now"], focus=p["start"])
        start, alive = p["start"], p["alive"]
        sec = (alive - start).total_seconds()
        limit = self._check_mark(self._focus_checks + 1, start) + float(config.FOCUS_CHECK_GRACE)
        penalized = sec >= limit
        if penalized:
            sec = limit
        self._close_check_win()
        self.cancel_update()
        minutes, brk = self._book_focus(start, sec, self._focus_ok if penalized else None)
        self._mine()                           # 终止由本机做, 其他设备照着结束
        self.reset_to_idle()
        self._set_break_carry(self.break_carry + brk)
        self._add_auto_rest(brk)
        if not p.get("off"):
            self._deep_check_closed(start, sec)
        self.lbl_sub.config(text=self._idle_sub_text())
        self.refresh_display()
        self._refresh_if_visible("stats", "cal")
        msg = tr("这台设备上的 OIT 在 {at} 被关掉了, 共享的专注已终止.").format(at=f"{alive:%m-%d %H:%M}")
        msg += "\n" + tr("专注记到被关掉时为止: 计入 {m:.1f} 分钟; 关掉的这段记为空闲.").format(m=minutes)
        if brk > 0:
            msg += "\n" + tr("积攒的休息时间 +{t}.").format(t=fmt_ms(brk))
            msg += "\n" + tr("立即开始专注:先进行一次休息时间的折算")
        toast.notice(tr("⏹  专注已终止"), msg)

    def _end_focus_for_close(self):
        """绑定了其他设备时关闭程序: 共享的专注就此结束 (记到此刻), 不开始休息, 休息时间存进积攒的休息时间."""
        start = self.start_wall
        elapsed = time.monotonic() - self.start_mono
        self._close_check_win()
        self.cancel_update()
        _minutes, brk = self._book_focus(start, elapsed)
        self._mine()
        self.reset_to_idle()
        self.break_carry += brk
        self._add_auto_rest(brk)                 # 同时写进文件
        if not self._went_off():
            self._deep_check_closed(start, elapsed)

    def _keep_awake(self):
        """专注 / 休息中不让系统自动睡眠 (屏幕照常可以关掉), 计时和同步不会停."""
        want = self.state in (RUNNING, ON_BREAK)
        if want == self._awake or sys.platform != "win32":
            return
        self._awake = want
        try:
            import ctypes
            # ES_CONTINUOUS | ES_SYSTEM_REQUIRED; 只写 ES_CONTINUOUS = 恢复正常
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if want else 0))
        except Exception:
            pass

    def _book_focus(self, start: datetime, seconds: float, ok=None):
        """记下一段专注, 返回 (计入的分钟数, 对应的休息秒数).
        ok: 没回应"还在吗"被自动结束时, 最后一次确认时已专注的秒数 —— 它之前的照常计,
            之后没有确认的那一段只把前 FOCUS_CHECK_PENALTY 记作专注. 计入的部分在时间上是连续的."""
        seconds = max(0.0, float(seconds))
        if ok is not None:
            ok = min(max(0.0, float(ok)), seconds)
            seconds = ok + (seconds - ok) * float(config.FOCUS_CHECK_PENALTY)
        end = start + timedelta(seconds=seconds)
        minutes = self.contract.focus_minutes(start, seconds)   # 阻滞: 只减慢合约内的那部分时间
        # 有设备离线的那段记为空闲, 不计入学习时长; 建议休息时长照常按计入前的时长算
        counted = max(0.0, minutes - self._cut_offline(start, end) / 60.0)
        self.dm.add_study_session(start, end, counted)
        self._add_study_pieces(start, end)
        brk = int(round(self._focus_rest(minutes, start, end)))
        return counted, brk

    def _deep_check_closed(self, start: datetime, seconds: float):
        """专注停止 (手动结束 / 提醒超时自动结束 / OIT 被关掉等, 非关机) 时按纵深推进检查这段够不够长 (18.3).
        关机 / 断电导致的停止由调用方用 off 判断豁免 (MECHANICS 15.4), 不走到这里."""
        minutes = self.contract.focus_minutes(start, seconds)
        tid = self.contract.focus_check(minutes * 60)
        if tid:
            self.contract_violation(tr("专注只有 {m} 分钟 (至少 {n} 分钟)", m=int(minutes),
                                       n=contract_mod.DEEP[tid] // 60), tid)

    def _focus_rest(self, minutes: float, start: datetime, end: datetime) -> float:
        """一段专注换来的休息时间 (秒): F(t) × 休息时间倍率, 再按合约 (MECHANICS 18) 调整."""
        sec = max(0.0, compute_F(minutes)) * float(config.BREAK_TIME_SCALE) * 60.0
        if self.contract.active:
            return self.contract.book_rest(sec, end)
        return self.contract.book_rest_after(sec, start) if start else sec

    def _offline_spans(self, a_ms: float, b_ms: float, own: bool = True) -> list:
        """[a, b] 里有设备离线的时间段 (毫秒): 外出期间 (MECHANICS 8.4, 21.6) 设备离线不算 ——
        外出的设备本来就不在家里的网络上. own: 也包括本机关机的那几段 (它们照常算)."""
        spans = [list(s) for s in self.sync.offline_spans(a_ms, b_ms, own=False)]
        try:
            away = self.behavior.away_spans(datetime.fromtimestamp(a_ms / 1000), datetime.fromtimestamp(b_ms / 1000), free_only=False)
        except Exception:
            away = []
        for x, y in away:
            xm, ym = x.timestamp() * 1000, y.timestamp() * 1000
            cut = []
            for a, b in spans:
                if b <= xm or a >= ym:
                    cut.append([a, b])
                    continue
                if a < xm:
                    cut.append([a, xm])
                if b > ym:
                    cut.append([ym, b])
            spans = cut
        if own:
            spans += [list(s) for s in self.sync.self_off_spans(a_ms, b_ms)]
        spans.sort()
        out = []
        for a, b in spans:
            if out and a <= out[-1][1]:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        return [(a, b) for a, b in out if b - a > 1]          # (浮点换算会留下不到 1 毫秒的碎片)

    def _cut_offline(self, start: datetime, end: datetime) -> float:
        """[start, end] 这段专注里不计入学习时长的部分, 返回一共多少秒:
        - 有绑定的设备离线的时间段: 记为空闲 (统计里压过同一段专注);
        - 本机关机的时间段 (MECHANICS 15.4): 本机的数据不计入 (见 _add_study_pieces);
          这段只有关机的设备 (没有别的设备记下专注 / 休息) 时记为空闲."""
        a_ms, b_ms = start.timestamp() * 1000, end.timestamp() * 1000
        try:
            spans = self._offline_spans(a_ms, b_ms, own=False)
            mine = self.sync.self_off_spans(a_ms, b_ms)
        except Exception:
            return 0.0
        idle = list(spans)
        for a, b in mine:
            if not self.dm.peer_active(datetime.fromtimestamp(a / 1000), datetime.fromtimestamp(b / 1000)):
                idle.append((a, b))
        for a, b in idle:
            self.dm.add_focus_session("idle", datetime.fromtimestamp(a / 1000), datetime.fromtimestamp(b / 1000),
                                      focus=start)
        total, last = 0.0, None
        for a, b in sorted(list(spans) + list(mine)):          # 合并后的长度 (两种重叠时不重复算)
            if last is not None and a < last:
                a = last
            if b > a:
                total += (b - a) / 1000.0
            last = b if last is None else max(last, b)
        return total

    def _add_study_pieces(self, start: datetime, end: datetime):
        """记下本机这段专注; 本机关机的那几段 (MECHANICS 15.4) 不记, 不参与专注计时的计算."""
        try:
            mine = self.sync.self_off_spans(start.timestamp() * 1000, end.timestamp() * 1000)
        except Exception:
            mine = []
        a = start
        for x, y in mine:
            x, y = datetime.fromtimestamp(x / 1000), datetime.fromtimestamp(y / 1000)
            if x > a:
                self.dm.add_focus_session("study", a, x)
            a = max(a, y)
        if end > a:
            self.dm.add_focus_session("study", a, end)

    # ---- 专注中每 1.5 小时: 还在吗 ----

    def _close_check_win(self):
        w, self._check_win = self._check_win, None
        if w is not None:
            try:
                w.close()
            except Exception:
                pass

    def _check_focus(self):
        """每 5 秒检查一次: 弹出 / 判定"还在吗"提醒, 并定时保存专注状态."""
        if self.state != RUNNING or self.start_mono is None:
            self._close_check_win()
            return
        if self._close_pending:
            return                             # 先等 _check_close_pending 决定这段专注还要不要
        # 绑定的设备非关机离线累计超过 5 分钟: 自动结束专注 (MECHANICS 15.4; 关机算在线, 不计)
        if self.sync.peers() and self._timer_auto_ok() \
                and self._offline_secs(self.start_wall) >= float(config.OFFLINE_FOCUS_END):
            self._offline_end_focus(time.monotonic() - self.start_mono)
            return
        if self._check_win is not None and not self._check_win.alive():
            self._check_win = None             # 被手动关掉了 = 人还在
            self._mine()
            self._focus_checks += 1
            self._focus_ok = time.monotonic() - self.start_mono
            self._save_timer_state()
        grace = float(config.FOCUS_CHECK_GRACE)
        elapsed = time.monotonic() - self.start_mono
        mark = self._check_mark(self._focus_checks + 1)
        if elapsed >= mark + grace:
            if self._timer_auto_ok():          # 别的设备是主人时等它结束, 结果同步过来
                self._auto_end_focus(mark + grace)
            return
        if elapsed >= mark and self._check_win is None:
            due = datetime.now() + timedelta(seconds=mark + grace - elapsed)
            # 合约专注: 写准确的分钟数 (间隔是随机的); 黑雾时不写专注了多久 (18.3)
            if not self.contract.focus_tag(self.start_wall):
                spent = tr("已经专注 {t} 了.").format(t=fmt_hm(mark)) + "\n"
            elif self.contract.fog():
                spent = ""
            else:
                spent = tr("已经专注 {n} 分钟了.").format(n=int(round(mark / 60))) + "\n"
            self._check_win = toast.notice(
                tr("⏱  还在专注吗?"),
                spent + tr("请在{at}之前关掉这个窗口,否则专注会自动结束.").format(
                    at=f"{due:%H:%M}", p=float(config.FOCUS_CHECK_PENALTY)),
                [(tr("还在,继续专注"), None, True), (tr("■  结束专注"), self._end_from_check, False)])
        # 绑定了其他设备时写得勤一些: 被关掉之后才知道是什么时候关的
        every = float(config.FOCUS_ALIVE_EVERY if self.sync.peers() else config.FOCUS_SAVE_EVERY)
        if time.monotonic() - self._focus_saved >= every:
            self._save_timer_state()

    def _check_mark(self, n: int, start: datetime = None) -> float:
        """第 n 次"还在吗"提醒在开始专注后多少秒: 合约专注每次随机 40 ~ 60 分钟 (contract.check_mark)."""
        start = start or self.start_wall
        return contract_mod.check_mark(n, start, bool(self.contract.focus_tag(start)),
                                       float(config.FOCUS_CHECK_EVERY))

    def _end_from_check(self):
        if self.state == RUNNING:
            self.show_page("timer")
            self.on_action()

    def _auto_end_focus(self, seconds: float):
        """提醒窗没人关: 自动结束专注. 不开始休息倒计时 (人不在), 休息时间存进积攒的休息时间."""
        start = self.start_wall
        ok = min(self._focus_ok, seconds)
        self._close_check_win()
        self.cancel_update()
        minutes, brk = self._book_focus(start, seconds, ok)
        self._mine()                             # 自动结束由本机做, 其他设备照着结束
        self.reset_to_idle()
        self._set_break_carry(self.break_carry + brk)   # 同时清掉文件里的专注状态
        self._add_auto_rest(brk)
        self._deep_check_closed(start, seconds)  # 纵深推进: 自动结束的这段也查够不够长 (任何原因停止都查)
        self.lbl_sub.config(text=self._idle_sub_text())
        self._sync_timer_buttons()
        self.refresh_display()
        self._refresh_if_visible("stats", "cal")
        msg = tr("提醒窗{n}分钟内没有被关掉,专注已在{at}自动结束.").format(
            n=int(round(float(config.FOCUS_CHECK_GRACE) / 60)),
            at=f"{start + timedelta(seconds=seconds):%H:%M}")
        msg += "\n" + tr("没有确认的部分按×{p:g}计入,共计入{m:.1f}分钟.").format(
            since=f"{start + timedelta(seconds=ok):%H:%M}", seg=fmt_hm(seconds - ok),
            p=float(config.FOCUS_CHECK_PENALTY), m=minutes)
        if brk > 0:
            msg += "\n" + tr("积攒的休息时间 +{t}.").format(t=fmt_ms(brk))
            msg += "\n" + tr("立即开始专注:先进行一次休息时间的折算")
        toast.notice(tr("⏹  专注已自动结束"), msg)

    def _offline_secs(self, start: datetime) -> float:
        """这段专注里绑定设备非关机离线累计了多少秒 (关机算在线, 不计)."""
        try:
            spans = self._offline_spans(start.timestamp() * 1000, time.time() * 1000, own=False)
        except Exception:
            return 0.0
        return sum((b - a) for a, b in spans) / 1000.0

    def _offline_end_focus(self, seconds: float):
        """绑定设备非关机离线累计超过 5 分钟: 自动结束专注 (MECHANICS 15.4). 休息时间存进积攒的休息时间."""
        start = self.start_wall
        self._close_check_win()
        self.cancel_update()
        minutes, brk = self._book_focus(start, seconds)
        self._mine()                             # 离线判定由本机做, 其他设备照着结束
        self.reset_to_idle()
        self._set_break_carry(self.break_carry + brk)
        self._add_auto_rest(brk)
        self._deep_check_closed(start, seconds)  # 任何原因停止都查纵深推进
        self.lbl_sub.config(text=self._idle_sub_text())
        self._sync_timer_buttons()
        self.refresh_display()
        self._refresh_if_visible("stats", "cal")
        msg = tr("绑定的设备离线累计超过 {n} 分钟, 专注已自动结束.").format(n=int(float(config.OFFLINE_FOCUS_END) // 60))
        if brk > 0:
            msg += "\n" + tr("积攒的休息时间 +{t}.").format(t=fmt_ms(brk))
            msg += "\n" + tr("立即开始专注:先进行一次休息时间的折算")
        toast.notice(tr("⏹  专注已自动结束"), msg)

    def _restore_timer_view(self):
        """界面重建 (换语言 / 字号) 后按当前状态恢复计时器页."""
        self._sync_timer_buttons()
        active = self.behavior.active()
        if active is not None and self.state == IDLE:
            self._show_outing(active)
        elif self.state == RUNNING:
            self._status(tr("专注中…"), COLORS["accent"])
            self.schedule_update()
        elif self.state == READY_BREAK:
            self._show_ready_break()
        elif self.state == ON_BREAK:
            self._show_on_break()
            self.schedule_update()

    def schedule_update(self):
        self.cancel_update()
        self.update()

    def cancel_update(self):
        if self.update_job:
            self.root.after_cancel(self.update_job)
            self.update_job = None

    def update(self):
        if self.state == RUNNING:
            elapsed = time.monotonic() - self.start_mono
            rate = self.contract.focus_rate(self.start_wall)   # 现在还是不是阻滞中 (合约结束后就不再减慢)
            cur_m = self.contract.focus_minutes(self.start_wall, elapsed)
            if self.contract.fog():
                # 黑雾: 专注时看不到专注时长 (预计休息时长也能推出时长, 一起不显示)
                self.lbl_time.config(text="???")
                self.lbl_sub.config(text="")
                self.update_job = self.root.after(200, self.update)
                return
            self.lbl_time.config(text=f"{cur_m:.2f} min")
            rem = compute_F(cur_m) * float(config.BREAK_TIME_SCALE) * self.contract.rest_factor()
            if rem > 0:
                sub = tr("预计停止时的休息时长:  {m:.2f} 分钟").format(m=rem)
            else:
                sub = tr("预计休息时长: 0.00 分钟")
            if rate < 1:
                sub += "\n" + tr("阻滞: 这段专注的时间按 {p}% 增长", p=int(round(rate * 100)))
            self.lbl_sub.config(text=sub)
            self.update_job = self.root.after(200, self.update)

        elif self.state == ON_BREAK:
            if self.countdown_seconds > 0:
                if config.BREAK_WARN_ENABLED and not self._break_warned and self.countdown_seconds <= 60:
                    self._break_warned = True
                    self._break_one_min_warn()
                m = self.countdown_seconds // 60
                s = self.countdown_seconds % 60
                self.lbl_time.config(text=f"{m:02d}:{s:02d}")
                sub = tr("休息剩余 {m} 分 {s:02d} 秒").format(m=m, s=s)
                if self._contract_rest_over:
                    sub += "\n" + tr("暂停休息: 立即消耗所有的合约休息时间, 但是不对正常休息打折")
                elif self.contract.active and self._break_pre > 0:
                    sub += "\n" + tr("合约前保存的休息时间: {t}").format(t=fmt_ms(int(self._break_pre)))
                if self._break_locked():
                    sub += "\n" + self._pause_block()
                self.lbl_sub.config(text=sub)
                self.countdown_seconds -= 1
                self.update_job = self.root.after(1000, self.update)
            else:
                if not self._timer_auto_ok():
                    self.break_start_wall = None      # 这一段由开始休息的那台设备记
                self._record_break_end()
                self._bonus_break_active = False
                self._contract_break_active = False
                if self.contract.active and self.contract.live.get("id") == self._break_cid:
                    self.contract.pre_settle(self._break_pre, 0)
                self._break_pre, self._break_cid = 0.0, None
                toast.success(tr("休息结束,可以开始下一轮了!"))
                self.reset_to_idle()
                self._arm_idle_nudge()
        else:
            self.cancel_update()

    def reset_to_idle(self):
        self.cancel_update()
        # 回到空闲 = 这段专注 (或休息) 结束了: 提前启动的程序全部放出来.
        # 选过的程序先记下; 紧接着如果是从休息直接开始专注, _enter_focus 会接着用, 否则丢掉 (MECHANICS 20)
        if getattr(self, "prestart", None) is not None and self.prestart.active:
            self._stop_prestart(park=True)
        self.state = IDLE
        self.start_mono = None
        self.start_wall = None
        self.last_t_minutes = 0.0
        self.rest_base = 0.0
        self.countdown_seconds = 0
        self._contract_rest_over = False
        self._sync_timer_buttons()
        active = self.behavior.active() if getattr(self, "behavior", None) is not None else None
        if active is not None:
            self._show_outing(active)
        else:
            self.lbl_time.config(text="0.00 min")
            self.lbl_sub.config(text=self._idle_sub_text())
            self._status(tr("准备就绪"), COLORS["text_dim"])
        if hasattr(self, "timer_file"):
            self._save_timer_state()            # 清掉文件里的进行中的休息, 重开时不会再记一遍

    def refresh_display(self):
        if self.contract.fog():
            self.lbl_today.config(text=tr("今日: ???"))
            return
        self.lbl_today.config(text=tr("今日: {h:.1f} h").format(h=self.dm.get_today_hours()))


    def _start_midnight_timer(self):
        self._schedule_midnight()

    def _schedule_midnight(self):
        now = datetime.now()
        next_midnight = datetime.combine(now.date() + timedelta(days=1), dt_time.min)
        remaining = (next_midnight - now).total_seconds()
        if remaining <= 0:
            remaining = 86400.0
        delay_ms = int(remaining * 1000) + 100
        self.midnight_job = self.root.after(delay_ms, self._on_midnight)

    def _on_midnight(self):
        old_date = self.dm.today_date
        self.dm.today_date = datetime.now().strftime('%Y-%m-%d')
        if old_date[:7] != self.dm.today_date[:7]:
            self.dm.prune_study_data()
        # 不清 prompt_slot:昨天 23:00 那格现在显示在今天最上方,仍可填写.
        self.dm.finalize_hours()
        self.dm.save_hour_log()
        self.update_log_badge()
        self.pages["log"].refresh()
        self.refresh_display()
        self._refresh_if_visible("cal", "stats")
        self._schedule_midnight()

    def _refresh_if_visible(self, *keys):
        for key in keys:
            if key == "rules":                     # 规则是弹窗, 开着就刷新
                if key in self.pages:
                    self.pages[key].refresh()
            elif self.current_page == key and key in self.pages:
                self.pages[key].refresh()

    def _schedule_hourly(self):
        # 每个整点和半点各醒一次; 半点只在合约的细化规范 (MECHANICS 18.4) 时响铃
        now = datetime.now()
        base = now.replace(minute=0, second=0, microsecond=0)
        self.next_hour = base + timedelta(minutes=30) if now.minute < 30 else base + timedelta(hours=1)
        remaining = (self.next_hour - now).total_seconds()
        if remaining <= 0:
            remaining = 1800.0
        delay_ms = int(remaining * 1000) + 100
        self.hourly_job = self.root.after(delay_ms, self._on_hour)

    def _on_hour(self):
        at = self.next_hour
        self._schedule_hourly()
        if "log" in self.pages:
            self.pages["log"].refresh()          # 到了填写时限的格子 (记录封锁) 锁上
        if at.minute == 30:
            hour = at - timedelta(minutes=30)
            if not (self.dm.hour_enabled and self.contract.fine_hour(hour)):
                return
            slot, length = hour, 30                         # 前半小时
        else:
            hour = at - timedelta(hours=1)
            self.dm.finalize_hours()
            if not self.dm.hour_enabled:
                self.update_log_badge()
                return
            if self.contract.fine_hour(hour):
                slot, length = at - timedelta(minutes=30), 30   # 后半小时
            else:
                slot, length = hour, 60
        if self._slot_locked(slot, length):
            self.update_log_badge()                         # 记录封锁另外封锁了这一格: 填不了, 不提示
            return
        # 绑定了其他设备时, 只有最后有人操作的那台响 (其他设备只标出待填写); 睡眠中不响, 免得吵醒
        chime = self._play_chime() if self.sync.is_latest_active() and self.sleep_since is None else None
        self._wait_chime(chime, slot, length=length)

    def _play_chime(self, choice=None):
        """choice: 试听时下拉框里选中的 (还没提交); None = 用设置里的."""
        if sys.platform != "win32":
            try:
                self.root.bell()
            except Exception:
                pass
            return None

        path = (self.settings.value("CHIME_WAV") if choice is None else choice) or ""
        if path and not os.path.exists(path):
            path = ""
        builtin = os.path.join(self.dm.data_dir, "chime.wav")
        if not path or path == "__builtin__":
            path = ensure_builtin_chime(builtin) or ""

        def run():
            # 自选文件 (.wav / .mp3) 放不出来时, 退回内置提示音, 再不行就蜂鸣
            if _play_audio_file(path):
                return
            if path != builtin and _play_audio_file(ensure_builtin_chime(builtin) or ""):
                return
            try:
                import winsound
                for freq, ms in ((784, 200), (988, 200), (1319, 450)):
                    winsound.Beep(freq, ms)
            except Exception:
                pass
        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t

    def play_chime(self, choice=None):
        self._play_chime(choice)

    def _wait_chime(self, t, slot, waited: int = 0, length: int = 60):
        # 等提示音放完再提示填写; 自选的音频可能很长, 最多等 10 秒
        if t is not None and t.is_alive() and waited < 10000:
            self.root.after(200, lambda: self._wait_chime(t, slot, waited + 200, length))
            return
        if not self.dm.hour_enabled:
            return
        self.dm.prompt_slot = slot
        self.dm.prompt_len = length
        self.update_log_badge()
        if self.current_page == "log":
            self.pages["log"].show()
        else:
            self.pages["log"].refresh()
