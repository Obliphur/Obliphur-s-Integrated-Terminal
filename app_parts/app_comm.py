"""沟通 (MECHANICS 24): 沟通页状态 / 分享 / 联合行动.

从 app.py 的 BreakTimerApp 拆出来的 mixin: 状态都在 self 上, 这里只放方法.
夹在中间的闹钟 (MECHANICS 25) 和"规则"弹窗还留在 app.py.
"""

import time
from datetime import datetime, timedelta

import comm as comm_mod
from comm_parts import comm_data
import config
import toast
from config import ON_BREAK, RUNNING
from i18n import tr


class CommMixin:
    # ---- 沟通 (MECHANICS 24) ----

    def _comm_state(self) -> str:
        """计时器现在的状态: sleep / focus / break / out / idle. 沟通只在 break / idle 时能用:
        外出期间只要计时器在休息就是 break (能用), 外出而没在休息才是 out (不能用)."""
        if self.sleep_since is not None:
            return "sleep"
        if self.state == RUNNING:
            return "focus"
        if self.state == ON_BREAK:
            return "break"
        if self.behavior.active() is not None:
            return "out"
        return "idle"

    def _comm_break_end(self):
        """用户这次休息结束的时刻 (没在休息 = None): 联合行动结束后对方的休息跟着它."""
        if self.state != ON_BREAK:
            return None
        if self.break_end_wall is not None:
            return self.break_end_wall
        return datetime.now() + timedelta(seconds=max(0, int(self.countdown_seconds)))

    def _comm_blocked(self) -> str:
        """现在不能用沟通的原因 ("" = 可以). 开发者模式下沟通总是开放."""
        if config.dev_mode_enabled():
            return ""
        if self.contract.has("silent"):
            return tr("无言为真生效中, 无法使用沟通")
        st = self._comm_state()
        if st == "focus" and self.comm.is_builtin_assistant_unmodified():
            return ""                              # 特例: 没改过提示词的内置 Assistant, 专注时也能用
        if st in ("focus", "sleep", "out"):
            return tr({"focus": "专注中不能使用沟通", "sleep": "睡眠中不能使用沟通", "out": "外出中不能使用沟通"}[st])
        if st == "idle" and self.comm.idle_over():
            return tr("今天空闲时间里能用沟通的 30 分钟已经用完了 (休息时不限)")
        return ""

    def _comm_ai_blocked(self) -> str:
        """对方现在不能主动发消息的原因 ("" = 可以). 用户专注 / 睡眠时对方仍可以发 (消息先藏起来, 回到休息 / 空闲再通知);
        外出 / 无言为真 / 空闲额度用完时不会尝试发消息. 开发者模式下总是开放."""
        if config.dev_mode_enabled():
            return ""
        if self.contract.has("silent"):
            return tr("无言为真生效中, 无法使用沟通")
        st = self._comm_state()
        if st == "out":
            return tr("外出中不能使用沟通")
        if st == "idle" and self.comm.idle_over():
            return tr("今天空闲时间里能用沟通的 30 分钟已经用完了 (休息时不限)")
        return ""

    def _comm_snapshot(self) -> dict:
        """发给对方的计时器状态; 黑雾中不给 (免得对方把看不到的数字说出来)."""
        if self.contract.fog():
            return {}
        now = datetime.now()
        st = self._comm_state()
        snap = {"state": st, "focus_total_min": self._today_hours_live() * 60.0, "focus_cur_min": 0.0,
                "rest_left_min": 0.0, "break_start": None, "break_total_s": 0}
        if self.state == RUNNING and self.start_wall is not None:
            snap["focus_cur_min"] = self.contract.focus_minutes(self.start_wall, time.monotonic() - self.start_mono)
        if self.state == ON_BREAK and self.break_end_wall is not None and self.break_start_wall is not None:
            snap["rest_left_min"] = max(0.0, (self.break_end_wall - now).total_seconds()) / 60.0
            snap["break_start"] = self.break_start_wall
            snap["break_total_s"] = (self.break_end_wall - self.break_start_wall).total_seconds()
        return snap

    SHARE_SEL = {"timer": {"today": ["timer"]}, "log": {"today": ["log"]},
                 "cal": {"cal": ["study", "break", "idle", "sleep"]},
                 "stats": {"stats": ["log", "net", "window", "focus", "contracts"]}}
    SHARE_TAGS = {"timer": "timer", "log": "log", "cal": "calendar", "stats": "stats"}

    def _comm_share(self, page: str) -> tuple:
        """share data: 这一页上 (页面名, 用户的数据, 对方的数据). 黑雾中用户的数据不给; 对方没有日程文件就没有自己的数据."""
        name = comm_mod.SHARE_PAGE_NAMES.get(page, page)
        sel = self.SHARE_SEL.get(page)
        if not sel:
            return name, "", ""
        mine = self._comm_data(sel) or (comm_data.FOG_TEXT if self.contract.fog() else "")
        ai = self.comm.routine_info(self.SHARE_TAGS[page])
        return name, mine, ai

    def _share_sync_ui(self, force: bool = False):
        """share data 的界面: 聊天悬浮窗 (打开着、在设置 / 指令 / 重置 / 沟通以外的页面时显示) 和"AI 的数据"."""
        if not hasattr(self, "share_dock"):          # 界面还没建好
            return
        c = self.comm
        page = c.share_page()
        show = c.share_on() and bool(page)
        has_ai = show and page in comm_mod.SHARE_DATA_PAGES and bool(self.comm._rt)
        if show:
            if self.share_dock.winfo_manager() != "grid":
                self.share_dock.grid(row=1, column=0, sticky="ew")
            self.share_dock.refresh(has_ai)
        elif self.share_dock.winfo_manager() == "grid":
            self.share_dock.grid_remove()
        want_ai = has_ai and c.share_view() == "ai"
        if want_ai:
            now = time.monotonic()
            if force or now - self._share_ai_at >= 5:
                self._share_ai_at = now
                try:
                    self.ai_data_view.render(c.share_ai_view(page), c.share_ai_name())
                except Exception as e:      # noqa: BLE001
                    self._log_window(f"ERROR    share view {type(e).__name__}: {e}")
            if self.ai_data_view.winfo_manager() != "grid":
                self.ai_data_view.grid(row=0, column=0, sticky="nsew")
            self.ai_data_view.lift()
        elif self.ai_data_view.winfo_manager() == "grid":
            self.ai_data_view.grid_remove()

    def _comm_data(self, sel: dict) -> str:
        """share data 里用户的数据: 今天的整点记录 / 计时器, 日历, 统计 (黑雾中什么也不给)."""
        if self.contract.fog():
            return ""
        return comm_data.build(sel, self.dm, self.net, self.contract, focus_live=self._focus_live(),
                               cal_live=self._calendar_live(),
                               today_log=lambda: self._comm_real("log"), today_timer=lambda: self._comm_real("timer"))

    def _comm_user_day(self, day) -> tuple:
        """某一天 (date) 用户的 (专注秒, 空闲秒); 只有最近 7 天有计时记录 (dm.day_kinds), 更早的由沟通模块自己存的账补."""
        now = datetime.now()
        k = self.dm.day_kinds(datetime.combine(day, datetime.min.time()), self._calendar_live() if day == now.date() else None, now)
        return k["study"], k["idle"]

    def _comm_info(self, tag: str) -> str:
        """对方查询的模块 ([模块名]): 只给一个参考模板 (格式和占位符, 24.2.1), 具体内容由对方按人设自己填写; 没有用户的真实数据, 黑雾中也能给."""
        rows = None
        if tag == "settings":
            from settings_manager import SETTINGS_PAGES, page_specs
            rows = []
            for page, title in SETTINGS_PAGES:
                if page == "comm":
                    continue
                choices, nums = page_specs(page)
                rows += [(tr(title), tr(spec.get("short") or spec["label"])) for spec in choices + nums]
        return comm_data.template(tag, rows)

    def _comm_real(self, tag: str) -> str:
        """今天的整点记录 / 计时器的真实文字 (share data 用): 黑雾中不给."""
        if self.contract.fog():
            return comm_data.FOG_TEXT
        if tag == "timer":
            now = datetime.now()
            try:
                intervals = self.dm.day_intervals(now, self._calendar_live(), now)
            except Exception:       # noqa: BLE001
                intervals = []
            return comm_data.timer_text(self._comm_snapshot(), intervals, now)
        if tag == "log":
            today = datetime.now().strftime("%Y-%m-%d")
            lines = [f"{h:02d}:00 {self.dm.get_hour_text(today, h)}" for h in range(24) if self.dm.get_hour_text(today, h)]
            return comm_data.hour_text(lines, self.dm.hour_enabled)
        return ""

    def _comm_leader(self) -> bool:
        """本机是不是聊天的主端: 最晚的一条消息是本机写的 (MECHANICS 24.6). 主端不在线 / 还没有消息时, 看谁最后有人操作."""
        if not self.sync.peers():
            return True
        mid = self.comm.last_author()
        if not mid:
            return self.sync.is_latest_active()
        if mid == (self.sync.my_id or "")[:4]:
            return True
        for s in self.sync.device_states():
            if str(s["id"]).startswith(mid) and s.get("online"):
                return False
        return self.sync.is_latest_active()

    def _comm_tick(self):
        if self.dm.resetting:
            return
        try:
            self._joint_break_watch()
            self.comm.tick()
            self._joint_invite_watch()
            if self._sf_win is not None and not self.comm.sf_live():       # 合约开始了 / 开关关了: 还没回答的提示作废
                self._sf_close()
            change = self.comm.check_persona_changes()
            if change:
                self._show_persona_change(change)
            if self.current_page == "comm" and "comm" in self.pages:
                self.pages["comm"].refresh()
            self._share_sync_ui()
        except Exception as e:
            self._log_window(f"ERROR    comm {type(e).__name__}: {e}")

    # ---- 联合行动 (MECHANICS 24.9): 沟通页 🤝 → 合约页选词条 → 点"确定选择" → 回到沟通页输入开始时间 → 随下一条消息发给对方 ----
    def _joint_pick_start(self):
        """沟通页的 🤝: 界面移到合约页, 让用户选词条 (开始行动的按钮换成"确定选择")."""
        why = self.comm.joint_block() or self.comm.problem()
        if not why and self.contract.active:
            why = "合约进行中, 不能发起联合行动"
        if not why and self.contract.fog():
            why = "黑雾中无法发送"
        if why:
            toast.warn(tr(why))
            return
        self._joint_pick = True
        self.pages["contract"].set_joint_pick(True)
        self.show_page("contract")
        toast.info(tr("联合行动: 选好词条后, 点右下角的「确定选择」."))

    def _joint_pick_cancel(self):
        self._joint_pick = False
        if "contract" in self.pages:
            self.pages["contract"].set_joint_pick(False)

    def _joint_pick_done(self, terms):
        """合约页点了"确定选择": 回到沟通页, 让用户输入开始时间."""
        self._joint_pick_cancel()
        self.show_page("comm")
        self.pages["comm"].ask_joint_time(list(terms))

    # ---- 对方 (AI) 发起的联合行动 (MECHANICS 24.9.3): 邀请到了 → 界面跳到合约页, 词条换成对方选的, 右下角是 拒绝 / 接受 ----
    def _joint_invite_info(self) -> dict:
        """合约页用: 有没有等用户回复的邀请 ({} = 没有), 以及右下角上方的说明."""
        j = self.comm.joint_invite()
        if not j:
            return {}
        try:
            a, b = datetime.fromisoformat(j["start"]), datetime.fromisoformat(j["end"])
            span = f"{a:%H:%M} - {b:%H:%M}" if a.date() == datetime.now().date() else f"{a:%m-%d %H:%M} - {b:%H:%M}"
        except (KeyError, TypeError, ValueError):
            span = ""
        return {"id": str(j.get("id") or ""),
                "hint": tr("🤝  {name} 邀请你一起行动 {span}", name=self.comm.joint_partner(), span=span).strip()}

    def _joint_invite_watch(self):
        """每秒看一次: 对方发起了联合行动 → 跳到合约页, 词条换成对方选的 (用户原来选的先记着). 拒绝 / 错过 / 聊天被清空以后换回原来的,
        接受了就留着 (到约定的开始时间在这里点开始行动). 邀请到的时候在合约中 / 指令页 / 沟通不能用 (专注 / 睡眠 / 外出): 先不跳, 能跳了再跳."""
        inv = self.comm.joint_invite()
        iid = str(inv.get("id") or "") if inv else ""
        if iid and iid != self._inv_id:
            if self.shutting_down or self.contract.active or getattr(self, "current_page", "") == "prescript" or self._comm_blocked():
                return
            terms = list(inv.get("terms") or [])
            if not self._inv_id:
                self._inv_prev = [] if list(self.contract.draft) == terms else list(self.contract.draft)
            self._inv_id = iid
            self._joint_pick_cancel()                      # 正在为自己发起的联合行动选词条: 取消
            self.contract.set_draft(terms)
            self._paint_sidebar()
            self.show_page("contract")
            toast.notice(tr("🤝  联合行动"), tr("{name} 邀请你一起行动: 词条已换成对方所选的, 在右下角选择「拒绝」或「接受」.",
                                                name=self.comm.joint_partner()))
        elif not iid and self._inv_id:
            self._inv_id = ""
            prev, self._inv_prev = self._inv_prev, []
            if self.comm.joint_state() != "yes" and not self.contract.active:      # 拒绝 / 错过 / 作废: 换回原来选的词条
                self.contract.set_draft(prev)
            self._paint_sidebar()
            self._refresh_if_visible("contract")

    def _joint_invite_respond(self, accept: bool):
        """合约页右下角点了 拒绝 / 接受."""
        why = self.comm.joint_respond(accept)
        if why:
            toast.warn(tr(why))
        else:
            toast.info(tr("已接受联合行动: 到约定的开始时间, 在这里点「开始行动」.") if accept else tr("已拒绝联合行动."))
        self._joint_invite_watch()

    def _joint_due(self, joint: dict):
        """联合行动约定的开始时间到了 (对方已经接受): 把约定的词条放进合约页, 提示用户开始行动."""
        try:
            if not self.contract.active:
                self.contract.set_draft(joint.get("terms") or [])
                self._paint_sidebar()
                if "contract" in self.pages:
                    self.pages["contract"].refresh()
            toast.notice(tr("🤝  联合行动"), tr("约定的开始时间到了: 在合约页点「开始行动」(词条已经选好), {name} 会和你同步专注 / 休息.", name=self.comm.joint_partner() or "AI"))
        except Exception as e:      # noqa: BLE001
            self._log_window(f"ERROR    joint_due {type(e).__name__}: {e}")

    def _open_comm_settings(self):
        self.show_page("settings")
        if "settings" in self.pages:
            self.pages["settings"].show_tab("comm")
