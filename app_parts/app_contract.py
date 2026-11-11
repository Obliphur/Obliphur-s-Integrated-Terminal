"""合约 (MECHANICS 18): 检查 / 开始 / 结束.

从 app.py 的 BreakTimerApp 拆出来的 mixin: 状态都在 self 上, 这里只放方法.
录像的合并 / 发送 / 违规 / 禁网在 app_contract_media.py.
"""

import sys
import time
from datetime import datetime, timedelta

import config
import contract as contract_mod
import daily
import devsync
import recorder
import toast
import window_guard
from app_base import contract_page_results
from config import IDLE, ON_BREAK, READY_BREAK, RUNNING, fmt_ms
from i18n import tr


class ContractMixin:
    # ---- 合约 (MECHANICS 18) ----

    def _contract_peers(self) -> bool:
        """有没有绑定 / 连接的其他设备 (全频道阻塞干扰要不要用代理-全屏蔽)."""
        sync = getattr(self, "sync", None)
        try:
            return bool(sync.peers()) if sync is not None else True
        except Exception:
            return True

    def _apply_contract_overrides(self) -> bool:
        """合约中盖过设置的项 (全频道阻塞干扰: 断网方式) 套到 config 上; 变了才动."""
        c = self.contract
        over = c.overrides() if c.active else {}
        if over == getattr(config, "CONTRACT_OVERRIDES", {}):
            return False
        config.CONTRACT_OVERRIDES = over
        self.settings.apply_to_config()
        net = getattr(self, "net", None)
        if net is not None and net.sync_mode():
            self._log_notice(f"NET_MODE -> {net.net_mode} (contract)")
        pages = getattr(self, "pages", {})
        if "rules" in pages:
            pages["rules"].refresh(force=True)
        if net is not None and pages:
            self.refresh_net(force=True)
        return True

    def _fog_seed(self):
        """黑雾: 统计 / 日历显示假数据用的种子 (同一个合约里不变); 没有黑雾时 None."""
        return self.contract.live.get("id") if self.contract.fog() else None

    def _fog_words(self):
        """黑雾: 整点记录假内容用的编号 (开始合约时抽好); 没有黑雾时 None."""
        return self.contract.fog_words() if self.contract.fog() else None

    def _check_desktop(self):
        """每 2 秒: OIT 的窗口在不在当前的虚拟桌面上 (MECHANICS 15.7). 切到别的桌面期间本机算作离线 ——
        不同步, 也不接受同步, 其他设备照常把本机算作离线, 本机也把它们算作离线 (专注里这段记为空闲, 合约的离线判定 …)."""
        if sys.platform != "win32" or time.monotonic() - self._desk_at < 2:
            return
        self._desk_at = time.monotonic()
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        except Exception:
            return
        away = window_guard.on_current_desktop(hwnd) is False
        if away == self._desk_away:
            return
        self._desk_away = away
        self._log_notice("DESKTOP  " + ("left OIT's virtual desktop: offline" if away else "back on OIT's virtual desktop"))
        if not away and self.sync.peers():
            toast.info(tr("回到了 OIT 所在的桌面. 切到其他桌面期间本机算作离线."), timeout=8000)
            self.sync.sync_now()

    def adopt_net(self, live: dict):
        """别的设备上改动了联网状态 (申请 / 联网 / 结束): 照着切换 (MECHANICS 2.8)."""
        event = self.net.adopt(live)
        self.refresh_net(force=True)
        self._sync_break_lock()
        if event == "started":
            toast.success(tr("网络已开启."))
        elif event == "ended":
            toast.warn(tr("联网已在其他设备上结束,网络已断开."))

    def _owner_away(self, owner: str) -> bool:
        """这个状态的主人是本机, 或者主人不在线 (2 分钟没同步): 自动的操作由本机来做."""
        if not owner or owner == self.sync.my_id:
            return True
        now = time.time() * 1000
        return not any(st.get("id") == owner and now - float(st.get("ts", 0)) < devsync.FRESH_MS
                       for st in self.sync.peer_status())

    def _contract_changed(self):
        """合约开始 / 结束之后 (本机或同步来的): 盖过设置的项, "我累了"按钮, 黑雾, 各页面."""
        self._apply_contract_overrides()
        self._sync_hour_log_setting()
        self._sync_bonus_button()
        self.refresh_display()
        self.refresh_net(force=True)
        s = self.dm.prompt_slot
        if s is not None and self.dm.prompt_len == 30 and not self.contract.fine_hour(s):
            self.dm.prompt_slot = None           # 合约结束, 细化规范的半小时格合回一格: 不再等那半小时
            self.update_log_badge()
        if "log" in self.pages:
            self.pages["log"].refresh()
        self._refresh_if_visible("contract")
        self._paint_sidebar()
        self.ach_popups.sync()                   # 黑雾: 合约中达成的成就等结束后再弹出
        self._refresh_if_visible("stats", "cal")
        if self.contract.fog() and self.current_page in ("settings", "ach"):
            self.show_page("timer")

    def _check_contract(self):
        """每秒: 到时间就完成, 空闲太久就失败 (由合约的主人做), 刷新合约页."""
        c = self.contract
        # 整点记录的词条 (记录封锁 / 细化规范) 可能随同步 / 未知变量中途出现: 每秒看一次要不要强制开启整点记录 (18.4),
        # 不然整点记录关着的设备上既不响提示音, 也不能填写, 超时也不判定
        if (bool(self.settings.value("HOUR_LOG_ENABLED")) or c.hour_forced()) != self.dm.hour_enabled:
            self._sync_hour_log_setting()
        seen = (c.live.get("id"), "active") if c.active else None
        if seen != self._contract_seen:
            was = self._contract_seen
            self._contract_seen = seen
            self._contract_changed()
            try:                                     # 联合行动: 别的设备开始 / 结束的合约 (本机开始 / 结束的在 _begin_contract / end_contract 里处理)
                if seen is not None:
                    if self.comm.joint_started(list(c.terms), c.live.get("id"), c.end_at):
                        c.tag_joint(self.comm.joint_partner())
                elif was is not None and c.last:
                    self.comm.joint_ended(c.last.get("id"), c.last.get("result"), c.last.get("reward") or 0)
            except Exception as e:      # noqa: BLE001
                self._log_window(f"ERROR    joint sync {type(e).__name__}: {e}")
            if was is not None and seen is None and c.last:
                toast.info(tr("合约已结束: {r}", r=tr(contract_page_results().get(c.last.get("result"), ""))))
            elif seen is not None and was is not None:
                toast.info(tr("合约开始."))
        try:
            self._sync_recorders()
        except Exception as e:
            self._log_window(f"ERROR    record {type(e).__name__}: {e}")
        if not c.active:
            self._refresh_if_visible("contract")     # 休息 / 联网中开始按钮变灰
            return
        self._apply_contract_overrides()
        if c.mark_fine(datetime.now()):          # 细化规范生效过的小时一直分两格 (未知变量到时移除后也是, 18.4)
            self._refresh_if_visible("log")
        self._contract_hours()
        self._check_supply()
        if not c.active:
            return
        # 绑定的设备离线累计超过 15 分钟 (和空闲共用, 不要求连续): 合约失败 (看到它离线的设备都可以判定)
        # (外出期间不算, 已经从离线的时间段里去掉了: MECHANICS 8.4, 21.6)
        spans = None
        if self.sync.peers() and c.start_at:
            now_dt = datetime.now()
            floor = datetime.fromtimestamp(self.sync._started_ms / 1000)
            spans = [(datetime.fromtimestamp(x / 1000), datetime.fromtimestamp(y / 1000))
                     for x, y in self._offline_spans(c.start_at.timestamp() * 1000, now_dt.timestamp() * 1000, own=False)]
            devices = [(d["name"], datetime.fromtimestamp((d["seen"] or 0) / 1000))
                       for d in self.sync.device_states() if not d["online"]]
            hit = c.offline_due(devices, now_dt, floor, spans)
            if hit:
                self._offline_name = hit[0]
                self.end_contract("offline", hit[1])
                return
            spans = [(max(a, floor), b) for a, b in spans]
        if self._owner_away(c.owner):
            why, at = c.due(datetime.now(), self.state in (IDLE, READY_BREAK), spans)
            if why:
                self.end_contract(why, at)
                return
            # 信息阻断: 额度是各设备共用的, 只由一台设备判定
            tid, kind = c.net_check(self.net.used_all, self.net.bonus_all, self.net.remaining, self.net.date)
            if tid:
                self.contract_violation(tr("使用了紧急借贷") if kind == "loan" else tr("联网超过了合约的额度"), tid)
                if not c.active:
                    return
            # 未知变量: 合约的每个小时开始时 (刚开始时也算) 随机抽一个词条, 3 分钟后生效, 那个小时结束时移除
            now = datetime.now()
            gone = c.expire(now)
            for tid in gone:
                self._log_notice(f"CONTRACT unknown expired {tid}")
                toast.info(tr("未知变量: {name} 已移除", name=tr(contract_mod.BY_ID[tid][3])), clear=True)
            picked = c.roll(now, text_of=self.dm.unit_text, auto=self.dm.auto_text_for_key,
                            fill=config.FILL_WINDOW)
            woke = c.wake(now)
            for kind, val in picked:
                self._log_notice(f"CONTRACT unknown -> {kind} {val}")
                self._random_effect(kind, val)
            for tid in woke:
                self._log_notice(f"CONTRACT unknown on {tid}")
                toast.warn(tr("未知变量: 启用了 {name}", name=tr(contract_mod.BY_ID[tid][3])), timeout=10000, clear=True)
            if picked or woke or gone:
                self._contract_changed()
                if self.sync.peers():
                    self.sync.sync_now()
        # 静默协议: 专注中这台设备处于联网状态 (各设备看自己的)
        tid = c.quiet_check(self.state == RUNNING and self.net.mode == "online")
        if tid:
            self.contract_violation(tr("专注中处于联网状态"), tid)
            if not c.active:
                return
        self._refresh_if_visible("contract")
        if self.current_page == "contract":
            self._paint_sidebar()

    def contract_block(self) -> str:
        """休息 / 联网 / 外出中不能开始合约: 返回提示 (没有限制时 "")."""
        if self.state == ON_BREAK:
            return tr("请先结束休息再开始合约")
        if getattr(self, "behavior", None) is not None and self.behavior.active() is not None:
            return tr("请先结束外出再开始合约")
        if getattr(self, "net", None) is not None and self.net.mode == "online":
            return tr("请先结束联网再开始合约")
        sync = getattr(self, "sync", None)
        diff = sync.version_mismatch() if sync is not None else []
        if diff:
            name, v, _newer = diff[0]
            return tr("{name} 的版本 ({v}) 和本机 ({me}) 不一样, 不能开始合约", name=name, v=v, me=config.APP_VERSION)
        return ""

    def start_contract(self, terms):
        c = self.contract
        if c.active or self._rec_pending:
            return
        why = self.contract_block()
        if why:
            toast.warn(why)
            return
        terms = contract_mod.clean_terms(terms)
        lv = contract_mod.level_of(terms)
        dur = contract_mod.duration_of(terms)
        now = datetime.now()
        end = now + timedelta(seconds=dur)
        # 睡觉时间 (MECHANICS 19): 合约结束时间晚于"这次要睡的时间"时, 当天第一次先确认一次
        late = daily.contract_delay(self.dm, end, now)
        if late and not toast.ask(daily.late_text(late), title=tr("🌙  该睡觉了"),
                                  ok_text=tr("开始行动"), danger=True):
            return
        msg = tr("危机等级 {lv}", lv=lv)                        # 不显示时长和结束时刻
        msg += "\n" + tr("完成后休息 +{n} 分钟", n=contract_mod.rest_minutes_total(lv, terms, contract_mod.clean_theme(config.CONTRACT_THEME)))
        if self.state == RUNNING:                               # 1.0.407: 正在专注时开始合约 = 这段专注立即暂停并折算休息, 再开始新一轮
            msg += "\n" + tr("正在专注: 开始后这段专注立即暂停并折算休息, 然后开始新一轮专注.")
        if not toast.ask(msg, title=tr("▶  开始行动"), ok_text=tr("开始行动"), danger=True):
            return
        rec = self._record_lines(terms)
        if rec and self.settings.value("REC_ASK") and not toast.ask(
                "\n".join(rec), title=tr("⚔ 合约要求录像"), ok_text=tr("开始录像")):
            return
        if self.contract_block():
            return
        self._pending_cam = ""
        if "gaze" in terms:
            # 目光: 用设置 → 录像里选的那台 (本机的某个摄像头 / 在线的手机); 没选 (自动) 时
            # 本机能录就本机录, 否则找绑定的手机 (MECHANICS 18.8).
            how, which = self._camera_choice()
            if how == "phone":
                phone = self._camera_phone(which)
                if phone:
                    self._pending_cam = phone
                    self._begin_contract(terms)
                    return
                self._log_notice("RECORD camera choice: the phone camera is not online, falling back")
            local_ok = recorder.camera_consent() != "deny" and recorder.camera_count()
            if local_ok:
                self._pending_cam = self.sync.my_id
                self._rec_pending = True
                self._stop_recorder("camera")
                r = self._start_recorder("camera")
                toast.info(tr("正在打开摄像头…"))
                self._await_camera(terms, r, time.monotonic())
                return
            phone = self._camera_phone()
            if not phone:
                toast.warn(tr("没有可用的摄像头: 本机没有摄像头, 也没有绑定在线的手机.") + "\n"
                           + tr("「目光」需要本机或手机有摄像头."))
                return
            self._pending_cam = phone
        self._begin_contract(terms)

    def _camera_choice(self) -> tuple:
        """设置 → 录像 里选的摄像头: ("auto" / "pc" / "phone", 摄像头 id / 设备 id)."""
        raw = str(self.settings.value("REC_CAMERA") or "").strip()
        if raw.startswith("pc|"):
            return "pc", raw[3:]
        if raw.startswith("phone|"):
            return "phone", raw[6:]
        return "auto", ""

    def _local_camera_id(self) -> str:
        """本机录像用哪一台摄像头 (camera_devices 的 id); "" = 自动 (第一台)."""
        how, which = self._camera_choice()
        return which if how == "pc" else ""

    def _camera_phone(self, want: str = "") -> str:
        """绑定的、在线的手机 (目光可以用它的摄像头录): 返回设备 id, 没有则 "".
        want: 想用哪一台 (不在线 / 不是手机 / 没绑定 → ""). 不给时找任意一台在线的手机."""
        plat = self.sync.peers()
        states = self.sync.device_states()
        if want:
            p = plat.get(want) or {}
            if str(p.get("platform", "")).lower().startswith("android") \
                    and any(s["id"] == want and s.get("online") for s in states):
                return want
            return ""
        for s in states:
            if s.get("online") and str((plat.get(s["id"]) or {}).get("platform", "")).lower().startswith("android"):
                return s["id"]
        return ""

    def _await_camera(self, terms, r, t0):
        if r.state == "running":
            self._rec_pending = False
            self._begin_contract(terms)
            return
        if r.state in ("failed", "lost", "stopped") or time.monotonic() - t0 > 15:
            self._rec_pending = False
            self._stop_recorder("camera")
            toast.warn(tr("摄像头打不开, 合约没有开始.") + (f"\n{r.error}" if r.error else ""))
            return
        self.root.after(200, lambda: self._await_camera(terms, r, t0))

    def _begin_contract(self, terms):
        c = self.contract
        if c.active or self.contract_block():
            self._stop_recorder("camera")
            return
        lv = contract_mod.level_of(terms)
        paused = self._pause_focus_for_contract()          # 正在专注: 这段专注先暂停并折算休息, 合约开始后接着开始新一轮 (1.0.407)
        # 联合行动 (1.0.404): 合约开始前积攒的休息时间收起来 —— 合约中看不到, 不能用, 也不会触发支援短缺, 合约结束后原样还回去
        try:
            joint = self.comm.joint_pending(list(terms))
        except Exception as e:      # noqa: BLE001
            joint = False
            self._log_window(f"ERROR    joint_pending {type(e).__name__}: {e}")
        stash = float(self.break_carry) if joint else 0.0
        c.start(terms, owner=self.sync.my_id, pre=0 if joint else self.break_carry, cam=getattr(self, "_pending_cam", ""),
                theme=config.CONTRACT_THEME, stash=stash)
        if stash > 0:
            self._set_break_carry(0)
        # 目光: 摄像头在合约开始前就打开了 (要先确认能录), 那时的 recorder 还不知道合约 id;
        # 现在补上, 不然这一段 / 这一整场录像在结束后按合约 id 找不到 (MECHANICS 18.8).
        cid = str(c.live.get("id") or "")
        for r in self._recorders.values():
            if not r.tag:                      # 只有合约开始前打开的录像才没有 tag
                r.set_tag(cid)
        # 凑齐的词条组合: 积攒的休息时间 +N 分钟 (在 pre 之后加: 算合约中得到的, 合约结束后不留下)
        gift = contract_mod.rest_gift(terms)
        if gift > 0:
            self._set_break_carry(self.break_carry + gift)
            if self.state == IDLE:
                self.lbl_sub.config(text=self._idle_sub_text())
            self._sync_timer_buttons()
        self._rec_started_cid = c.live.get("id")
        self._log_notice(f"CONTRACT start level {lv} terms {','.join(terms) or '-'} until {c.end_at:%Y-%m-%d %H:%M}"
                         + (f" gift {gift}s" if gift else "") + (" (focus paused, new round)" if paused else ""))
        self._contract_seen = (c.live.get("id"), "active")
        try:
            if self.comm.joint_started(list(c.terms), c.live.get("id"), c.end_at):      # 联合行动: 按约开始了
                c.tag_joint(self.comm.joint_partner())
            elif c.stash > 0:                                                           # 没能算作联合行动 (差了一瞬间): 收起来的休息时间还回去
                self._set_break_carry(self.break_carry + int(c.unstash()))
        except Exception as e:      # noqa: BLE001
            self._log_window(f"ERROR    joint_started {type(e).__name__}: {e}")
        if paused:
            self._start_focus_for_contract()
        self._contract_changed()
        self._sync_recorders()
        toast.success(tr("合约开始.") + ("\n" + tr("积攒的休息时间 +{t}.", t=fmt_ms(gift)) if gift else "")
                      + ("\n" + tr("原来的专注已暂停并折算休息, 新一轮专注已开始.") if paused else ""))
        if self.sync.peers():
            self.sync.sync_now()

    def abandon_contract(self):
        c = self.contract
        if not c.active:
            return
        msg = tr("提前结束合约?")
        if c.reward >= 1:
            msg += "\n" + tr("现在结束只给 {m} 分钟休息时间 (完成给 {n} 分钟).",
                             m=round(c.reward_at(datetime.now()) / 60), n=round(c.reward / 60))
        if not toast.ask(msg, title=tr("■  提前结束"), danger=True):
            return
        self.end_contract("abandoned")

    def _pause_focus_for_contract(self) -> bool:
        """开始合约时正在专注 (1.0.407): 这段专注立即暂停 —— 学习时长照常记 (合约还没开始, 按普通规则), 它换来的休息时间
        存进积攒的休息时间并马上折算 (和专注自动结束后手动开始专注一样, MECHANICS 6.8), 回到空闲;
        合约开始后由 _start_focus_for_contract 接着开始新一轮专注. 返回 True = 暂停了一段专注."""
        if self.state != RUNNING or self.start_wall is None:
            return False
        start = self.start_wall
        elapsed = time.monotonic() - self.start_mono
        self._close_check_win()
        self.cancel_update()
        _minutes, brk = self._book_focus(start, elapsed)
        self._mine()
        self.reset_to_idle()
        self._set_break_carry(self.break_carry + brk)
        self._add_auto_rest(brk)
        self._settle_auto_rest(paused=True)
        self._refresh_if_visible("stats", "cal")
        return True

    def _start_focus_for_contract(self):
        """合约已经开始: 接着开始新一轮专注 (和手动开始专注一样: 阻滞从这一刻起算)."""
        self._enter_focus(datetime.now(), time.monotonic())
        if self.contract.slow_bind(self.start_wall):
            toast.warn(tr("阻滞: 合约内这段专注的时间按 {p}% 增长", p=int(round(contract_mod.RANDOM_SLOW * 100))), timeout=10000)

    def _stop_break_for_contract(self):
        """结束合约前先停止休息 (MECHANICS 18.1): 剩下的休息时间按"暂停休息"算.
        合约开始前积攒的那部分原样留下, 合约中得到的部分由 end_contract 作废; 不算禁止囤囤鼠! 的违反."""
        if self.state != ON_BREAK:
            return
        left = max(0, int(self.countdown_seconds))
        self._record_break_end()
        pre = min(left, int(self._break_pre))
        c = self.contract
        if c.active and c.live.get("id") == self._break_cid:
            c.pre_settle(self._break_pre, pre)
        rate = float(config.BREAK_CARRY_RATE)
        if self._contract_break_active:
            rate = contract_mod.Contract.first_break_rate(rate)
        add = left if self._bonus_break_active else pre + int(round((left - pre) * rate))
        self._break_pre, self._break_cid = 0.0, None
        self._bonus_break_active = False
        self._contract_break_active = False
        if add > 0:
            self._set_break_carry(self.break_carry + add)
        self.reset_to_idle()
        self._save_timer_state()          # 关掉程序再打开时不能又从休息中恢复
        self._arm_idle_nudge()

    def _stop_focus_for_contract(self):
        """合约非自然结束 (失败 / 手动终止, MECHANICS 18.1): 正在进行的专注立即结束. 学习时长照常记,
        这段专注换来的休息时间是合约中获得的, 随合约一起清空 —— 不开始休息, 回到空闲."""
        if self.state == READY_BREAK:
            self.reset_to_idle()
            self._save_timer_state()
            return
        if self.state != RUNNING or self.start_wall is None:
            return
        start = self.start_wall
        elapsed = time.monotonic() - self.start_mono
        self._close_check_win()
        self.cancel_update()
        self._book_focus(start, elapsed)         # 休息时间不要
        self._rest_tag = ""
        self._mine()
        self.reset_to_idle()
        self._save_timer_state()
        self._arm_idle_nudge()

    def end_contract(self, result: str, at: datetime = None):
        c = self.contract
        if not c.active:
            return
        # 合约完成时正在休息: 不立即结束这次休息, 继续休息 (仍算合约内的休息);
        # 结算奖励照常计入积攒的休息时间 (算正常休息时间); 手动暂停会清空合约内获得的时间, 自然走完变空闲 (MECHANICS 18.1)
        rest_over = (result == "completed" and self.state == ON_BREAK
                     and self._break_cid is not None and self._break_cid == c.live.get("id"))
        if rest_over:
            self._contract_rest_over = True
        else:
            self._stop_break_for_contract()      # 结束合约前先停止休息
        if result != "completed":
            self._stop_focus_for_contract()      # 失败 / 手动终止: 正在进行的专注也立即结束 (不给休息时间)
        self._joint_break_prev = self.state == ON_BREAK      # 合约结束而停掉的休息不算"用户结束了休息": 对方照常休息到合约给的时间
        pending = c.reward
        rec = c.finish(result, at, by=self.sync.my_id)
        self._contract_seen = None
        reward = int(rec.get("reward") or 0)
        keep = min(int(self.break_carry), int(float(rec.get("pre_left") or 0))) + int(float(rec.get("stash") or 0))    # 联合行动收起来的休息时间还回去
        if reward > 0 or keep != self.break_carry:
            self._set_break_carry(keep + reward)
            if self.state == IDLE:
                self.lbl_sub.config(text=self._idle_sub_text())
            self._sync_timer_buttons()
        self._log_notice(f"CONTRACT {result} level {rec.get('level')} violations {rec.get('violations')} "
                         f"reward {reward}s")
        try:
            self.comm.joint_ended(rec.get("id"), result, reward)                  # 联合行动: 对方同步得到同样的休息
        except Exception as e:      # noqa: BLE001
            self._log_window(f"ERROR    joint_ended {type(e).__name__}: {e}")
        self.check_achievements()
        self._contract_changed()
        lv = rec.get("level", 0)
        if result == "completed":
            msg = tr("危机等级 {lv}", lv=lv)
            if reward > 0:
                msg += "\n" + tr("积攒的休息时间 +{t}.", t=fmt_ms(reward))
            toast.notice(tr("⚔  合约完成"), msg)
        elif result == "abandoned":
            toast.info(tr("⚔ 行动终止") + ("\n" + tr("积攒的休息时间 +{t}.", t=fmt_ms(reward)) if reward > 0 else ""))
        else:
            msg = {"failed": tr("目标生命值归零 (违反 {n} 次), 合约终止.", n=rec.get("violations", 0)),
                   "offline": tr("{name} 离线超过 15 分钟, 合约终止.", name=self._offline_name or "?")
                   }.get(result, tr("空闲超过 15 分钟, 合约终止."))
            if pending >= 1:
                msg += "\n" + tr("按进行的比例给出 {m} 分钟休息时间 (完成给 {n} 分钟).",
                                 m=round(reward / 60), n=round(pending / 60))
            toast.notice(tr("⚔ 行动失败"), msg)
        if self.sync.peers():
            self.sync.sync_now()
        rec_terms = set(rec.get("orig") or []) | set(rec.get("terms") or [])
        kinds = [k for t, k in contract_mod.RECORD.items() if t in rec_terms]
        if kinds:
            self._after_contract(str(rec.get("id") or ""), str(rec.get("owner") or ""), kinds)
