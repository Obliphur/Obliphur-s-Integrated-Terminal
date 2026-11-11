"""页面侧的小块: 日程跟随 / 闹钟 / 规则弹窗 / 侧边栏 / 计时页 UI.

从 app.py 的 BreakTimerApp 拆出来的 mixin: 状态都在 self 上, 这里只放方法.
闹钟的判定在 alarm.py (MECHANICS 25), 计时页的状态机在 app/app_timer.py.
"""

import os
import sys
from datetime import datetime

import tkinter as tk
from tkinter import ttk

import comm as comm_mod
import config
import toast
from app.app_base import _blend
from config import COLORS, IDLE, ON_BREAK, RUNNING
from i18n import tr
from pages import RulesPage
from pages.widgets import cfg, flavor


class PagesMixin:
    # ---- 日程跟随 (1.0.406, MECHANICS 24.17): 对方的状态变了 → 弹窗问用户要不要跟着变 ----
    def _sf_close(self):
        win, self._sf_win = self._sf_win, None
        try:
            if win is not None and win.winfo_exists():
                win.close()
        except Exception:       # noqa: BLE001
            pass

    def _sf_ask(self, kind: str, text: str):
        """对方的状态变了 (comm._sf_tick 在焦点设备调用): 问用户跟不跟随; 上一个还没回答的提示换成这个."""
        self._sf_close()
        if self.shutting_down:
            return
        self._sf_win = toast.notice(tr("📅  日程跟随"), text, [
            (tr("不用"), None, False),
            (tr("跟随"), lambda: self._sf_follow(kind), True)])

    def _sf_follow(self, kind: str):
        """用户点了"跟随": 把计时器带到和对方一样的状态 (等于按对应的按钮; 合约 / 外出中不能跟随)."""
        self._sf_win = None
        try:
            if self.contract.active:
                toast.warn(tr("合约中不能跟随."))
                return
            if self.behavior.active() is not None:
                toast.warn(tr("外出中不能跟随."))
                return
            if self.sleep_since is not None and kind != "sleep":
                self.wake()
            if kind == "focus":
                if self.state != RUNNING:
                    self.on_action()                 # 空闲 → 开始专注; 休息中 → 暂停休息并马上开始专注
            elif kind == "rest":
                if self.state == RUNNING:
                    self.on_action()                 # 结束专注, 直接进入休息
                elif self.state == IDLE:
                    self.on_break_button()           # 用积攒的休息时间开始休息 (没有积攒的会提示)
            elif kind == "sleep":
                if self.sleep_since is None:
                    if self.state == RUNNING:
                        self.on_action()
                    if self.state == ON_BREAK:
                        self.end_break_early()
                    if self.state == IDLE:
                        self.start_sleep()
                    else:
                        toast.warn(tr("请先结束专注 / 休息再睡眠."))
            else:                                    # 空闲: 结束专注 / 休息
                if self.state == RUNNING:
                    self.on_action()
                if self.state == ON_BREAK:
                    self.end_break_early()
        except Exception as e:      # noqa: BLE001
            self._log_window(f"ERROR    follow {type(e).__name__}: {e}")

    # ---- 闹钟 (1.0.359, MECHANICS 25): 每秒看一次该不该响; 睡眠 / 合约 / 任何页面都照响 ----
    def _comm_devices(self):
        """共时闹钟选响铃设备用: 本机 + 绑定的设备 [(id, 名字)]."""
        out = [(self.sync.my_id, self.sync.my_name)]
        try:
            out += [(s["id"], s["name"]) for s in self.sync.device_states()]
        except Exception:       # noqa: BLE001
            pass
        return out

    def _co_alarm_fn(self):
        """本机要按共时闹钟响铃时 = f(after) → 对象的下一次起床时刻; 否则 None."""
        try:
            return self.comm.co_alarm_fn()
        except Exception:       # noqa: BLE001
            return None

    def _alarm_tick(self):
        if self.shutting_down or self.dm.resetting:
            return
        try:
            if self._alarm_win is None:
                co_fn = self._co_alarm_fn()
                hit = self.alarm.check(datetime.now(), co_fn, self.comm.co_alarm_pid() if co_fn else "")
                if hit:
                    self._alarm_ring(hit)
            if self.current_page == "settings" and "settings" in self.pages:
                self.pages["settings"].refresh_alarm()
        except Exception as e:      # noqa: BLE001
            self._log_window(f"ERROR    alarm {type(e).__name__}: {e}")

    def _alarm_ring(self, hit: dict):
        from pages.alarm_ui import RingWindow
        name = self.comm.co_alarm_name() if hit["co"] else ""
        self._log_notice(f"ALARM ring {hit['at']:%Y-%m-%d %H:%M}" + (f" co={name}" if hit["co"] else ""))
        self.ringer.start(self.alarm.cfg)
        self._alarm_win = RingWindow(self.root, hit["at"], name, self._alarm_closed)

    def _alarm_closed(self):
        self.ringer.stop()
        self._alarm_win = None

    def _joint_break_watch(self):
        """联合行动: 用户开始休息时告诉对方这次休息的时间 (和之前损失的生命值一起, 对方可以回应); 休息结束 (走完 / 提前结束)
        时对方在合约结束后同步的休息也跟着结束. 每秒看一次状态, 不用在每个开始 / 结束休息的地方都接线."""
        on = self.state == ON_BREAK
        prev, self._joint_break_prev = self._joint_break_prev, on
        if prev is None or prev == on:
            return
        if on:
            if self.break_start_wall is not None and self.break_end_wall is not None:
                total = (self.break_end_wall - self.break_start_wall).total_seconds()
            else:
                total = self.countdown_seconds
            # 合约开始前积攒的休息时间 (放在这次休息的最后用) 不算: 告诉对方的只有合约里得到的休息时间
            self.comm.joint_break_started(max(0.0, float(total) - float(self._break_pre or 0)))
        else:
            self.comm.joint_break_ended()

    def _joint_hp_lost(self, text: str):
        """联合行动: 记下这次损失的生命值 (时间 + 内容), 下一次开始休息 / 合约结束时告诉对方."""
        try:
            self.comm.joint_hp_lost(text)
        except Exception as e:      # noqa: BLE001
            self._log_window(f"ERROR    joint_hp {type(e).__name__}: {e}")

    def _show_persona_change(self, change: dict):
        """人设文件被手动改过: 确认是否清空对应的聊天数据, 或者还原文件."""
        try:
            name = change["name"]
            msg = tr("{name},修改人设会清除聊天数据,你确定吗?", name=self.dm.username or "")
            if comm_mod.persona_title(name).casefold().split("_")[0] == "claudia":
                msg = tr("主管{name},我希望你尊重对方的人格.", name=self.dm.username or "") + "\n" + msg
            toast.notice(tr("人设文件被修改"), msg, [
                (tr("还原文件"), lambda: self.comm.resolve_persona_change(name, False), True),
                (tr("确定"), lambda: self.comm.resolve_persona_change(name, True), False)])
        except Exception:
            pass

    def _comm_ping(self):
        """有新的聊天消息, 而用户不在沟通页面: 柔和的提示音 (后台线程里放).
        绑定了其他设备时只在焦点设备 (最后有人操作的那台) 上响, 其他设备只标未读."""
        if self.sync.is_latest_active():
            self._play_soft_chime()

    def _play_soft_chime(self):
        if sys.platform != "win32" or self.shutting_down:
            return
        import sound_effects
        sound_effects.play("COMM_SOUND", self.dm.data_dir)

    def _play_break_soon_chime(self):
        """休息快结束的提示音: 比沟通提示音更低更轻 (后台线程里放). 只在焦点设备上响."""
        if sys.platform != "win32" or self.shutting_down:
            return
        if not self.sync.is_latest_active():
            return
        import sound_effects
        sound_effects.play("BREAK_SOUND", self.dm.data_dir)

    def _break_one_min_warn(self):
        """休息只剩 1 分钟: 轻提示音 + 右下角消息 (不挡住倒计时)."""
        if not self.sync.is_latest_active():
            return
        self._play_break_soon_chime()
        toast.info(tr("休息只剩 1 分钟了"))

    def update_comm_badge(self):
        b = getattr(self, "nav_btns", {}).get("comm")
        if b:
            cfg(b, text=self.nav_labels["comm"] + (" ●" if self.comm.unread else ""))


    # ---- 规则: 设置 → 联网 里的弹窗 ----

    def open_rules_popup(self):
        win = self._rules_win
        try:
            if win is not None and win.winfo_exists():
                win.deiconify()
                win.lift()
                win.focus_force()
                return
        except tk.TclError:
            pass
        top = tk.Toplevel(self.root, bg=COLORS["bg"])
        top.title(tr("🛡  规则"))
        w, h = config.scaled(1000), config.scaled(780)
        try:
            sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
            w, h = min(w, sw - 80), min(h, sh - 120)
            x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - w) // 2)
            y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - h) // 4)
            x, y = max(0, min(x, sw - w)), max(0, min(y, sh - h - 60))
            top.geometry(f"{w}x{h}+{x}+{y}")
        except tk.TclError:
            top.geometry(f"{w}x{h}")
        top.transient(self.root)
        self._rules_win = top
        page = RulesPage(top, self.net.proxy, settings=self.settings, sync=self.sync,
                         on_changed=self.on_settings_changed,
                         on_open_settings=self._close_rules_popup)
        page.pack(fill="both", expand=True)
        self.pages["rules"] = page
        top.protocol("WM_DELETE_WINDOW", self._close_rules_popup)
        top.bind("<Escape>", lambda _e: self._close_rules_popup())
        page.refresh(force=True)

    def _close_rules_popup(self):
        self.pages.pop("rules", None)
        win, self._rules_win = self._rules_win, None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass


    # ---- 合约页上的侧边栏: 换成合约页主题的颜色 (MECHANICS 18) ----

    def _paint_sidebar(self):
        """合约页 (主题不是"跟随界面主题"时): 侧边栏换成合约页主题的颜色; 其他页照常."""
        if not hasattr(self, "sidebar"):
            return
        from pages.contract_page import palette
        P = palette()
        on = self.current_page == "contract" and P.get("side")
        side_bg = P["side"] if on else COLORS["bg_card"]
        cfg(self.sidebar, bg=side_bg, highlightbackground=P["side_rim"] if on else COLORS["border"])
        for w in self.sidebar.winfo_children():
            if isinstance(w, tk.Frame):
                cfg(w, bg=side_bg)
        cfg(self.topbar, bg=P.get("topbar", P["line"]) if on else COLORS.get("topbar", COLORS["accent"]))
        for k, b in self.nav_btns.items():
            if not on:
                break
            if k == self.current_page:
                cfg(b, bg=P["side_on"], fg=P["side_on_text"], activebackground=P["side_on"],
                    activeforeground=P["side_on_text"])
            else:
                cfg(b, bg=P["side"], fg=P["side_text"], activebackground=P["side_hover"],
                    activeforeground=P["side_on_text"])

    def _build_timer_page(self, body):
        bg, card = COLORS["bg"], COLORS["bg_card"]

        hdr = tk.Frame(body, bg=bg)
        hdr.pack(fill="x", padx=28, pady=(22, 8))
        tk.Label(
            hdr, text=tr("⏱  Focus Timer"), bg=bg, fg=COLORS["text"],
            font=config.font("h1", bold=True)
        ).pack(anchor="w")

        ttk.Separator(body, orient="horizontal", style="UI.TSeparator").pack(fill="x", padx=26)

        card_fr = tk.Frame(
            body, bg=card, highlightbackground=COLORS["border"],
            highlightthickness=1, relief="solid", bd=0
        )
        card_fr.pack(padx=26, pady=20, fill="x")
        inner = tk.Frame(card_fr, bg=card)
        inner.pack(padx=22, pady=18, fill="both")

        status_row = tk.Frame(inner, bg=card)
        status_row.pack(anchor="w", pady=(0, 12))
        self.dot_canvas = tk.Canvas(status_row, width=12, height=12, bg=card, highlightthickness=0)
        self.dot_canvas.pack(side="left", padx=(0, 8))
        self.dot_id = self.dot_canvas.create_oval(1, 1, 11, 11, fill=COLORS["text_dim"], outline="")
        self.lbl_status = tk.Label(
            status_row, text=tr("准备就绪"), bg=card, fg=COLORS["text_dim"],
            font=config.font("body")
        )
        self.lbl_status.pack(side="left")

        self.lbl_time = tk.Label(
            inner, text="0.00 min", bg=card, fg=COLORS["text"],
            font=config.font("big", bold=True), anchor="w"
        )
        self.lbl_time.pack(anchor="w", pady=(2, 6))

        self.lbl_sub = tk.Label(
            inner, text=self._idle_sub_text(),
            bg=card, fg=COLORS["text_dim"], font=config.font("body"), anchor="w",
            justify="left"
        )
        self.lbl_sub.pack(anchor="w")

        stats_frame = tk.Frame(inner, bg=card)
        stats_frame.pack(anchor="w", pady=(14, 0), fill="x")
        self.lbl_today = tk.Label(
            stats_frame, text=tr("今日: {h:.1f} h").format(h=0.0), bg=card, fg=COLORS["accent"],
            font=config.font("body", bold=True)
        )
        self.lbl_today.pack(side="left", padx=(0, 30))
        # 绑定的其他设备此刻的状态 (MECHANICS 15)
        self.lbl_peers = tk.Label(inner, text="", bg=card, fg=COLORS["text_dim"],
                                  font=config.font("small"), anchor="w", justify="left")
        self.lbl_peers.pack(anchor="w", pady=(6, 0))

        btn_row = tk.Frame(body, bg=bg)
        btn_row.pack(fill="x", padx=26, pady=(6, 22))
        # 第一行只有 开始专注 / 开始休息; 其他按钮放在第二行; "我累了" 在页面右上角
        btns = tk.Frame(btn_row, bg=bg)
        btns.pack()
        btns2 = tk.Frame(btn_row, bg=bg)
        btns2.pack(pady=(10, 0))
        self.btn_action = ttk.Button(
            btns, text=tr("▶  开始专注"), style="Accent.TButton",
            command=self.on_action
        )
        self.btn_action.pack(side="left", ipadx=10)
        self.btn_break = ttk.Button(
            btns, text=tr("☕  开始休息"), command=self.on_break_button
        )
        self.btn_break.pack(side="left", ipadx=10, padx=(10, 0))
        # 暂停专注 (MECHANICS 6.9): 专注中可用, 暂停时专注时间按倍率倒退
        self.btn_pause = ttk.Button(btns, text=tr("⏸  暂停专注"), command=self.toggle_focus_pause)
        self.btn_pause.pack(side="left", ipadx=10, padx=(10, 0))
        # 睡眠 (MECHANICS 23): 盖住整个窗口, 只剩一个"起床"
        self.btn_sleep = ttk.Button(btns2, text=tr("😴  睡眠"), command=self._sleep_click)
        self.btn_sleep.pack(side="left", ipadx=10)

        # 提前启动 (仅 PC, MECHANICS 20): 专注中把选中的程序先跑起来, 窗口藏起来 + 静音 (出现时排在外出前面)
        self.btn_prestart = ttk.Button(btns2, text=tr("⏩  提前启动"), command=self._prestart_click)

        # 外出 (MECHANICS 21): 三种行为模式都有; 封闭式管理叫"上学", 工作叫"工作", 自由学习叫"外出"
        self.btn_outing = ttk.Button(btns2, text=tr("🚪  外出"), command=self._outing_click)
        self.btn_outing.pack(side="left", ipadx=8, padx=(10, 0))

        # 连点按钮: 1 分钟内连点 10 次, 换 20 分钟休息时间 (每周最多 2 小时); 放在页面右上角, 半透明
        dim = _blend(COLORS["text_dim"], bg, 0.45)
        self.lbl_bonus = tk.Button(body, text="", command=self._on_bonus_click, relief="flat", bd=0,
                                   highlightthickness=0, bg=bg, fg=dim, activebackground=bg,
                                   activeforeground=COLORS["text_dim"], font=config.font("small"),
                                   cursor="hand2", padx=6, pady=2)
        self.lbl_bonus.bind("<Enter>", lambda e, b=self.lbl_bonus: b.config(fg=COLORS["text_dim"]))
        self.lbl_bonus.bind("<Leave>", lambda e, b=self.lbl_bonus, c=dim: b.config(fg=c))
        self._bonus_show(self._bonus_idle_text())

        # 作者的话 (MECHANICS.md 6.2): 一直显示在页面下方的空白处
        flavor(body, tr("在第二段区间内 学的时间越长得到的休息时间越长.请尽量长时间地专注."), bg=bg).pack(
            side="bottom", fill="x", padx=26, pady=(0, 22))
        self._sync_timer_buttons()
        self._sync_bonus_button()

        self._set_dot(COLORS["text_dim"])


    def _outing_key(self, mode: str) -> str:
        """外出按钮上的名字: 封闭式管理 = 上学, 工作 = 工作, 自由学习 (和别的) = 外出."""
        return {config.BEHAVIOR_CLOSED: "上学", config.BEHAVIOR_WORK: "工作"}.get(mode, "外出")

    def _sync_outing_button(self):
        """外出按钮: 三种行为模式都有, 文字按模式 (上学 / 工作 / 外出); 外出中变成"结束…"."""
        btn = getattr(self, "btn_outing", None)
        if btn is None:
            return
        try:
            cur = self.behavior.current()
            mode = (cur.get("mode") if cur is not None else "") or self.behavior.mode()
            name = self._outing_key(mode)
            text = tr(f"🚪  结束{name}") if cur is not None else tr(f"🚪  {name}")
            if btn.cget("text") != text:
                btn.config(text=text)
        except Exception:
            pass
