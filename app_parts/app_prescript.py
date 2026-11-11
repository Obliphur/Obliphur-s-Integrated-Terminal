"""指令 (MECHANICS 22, 都市之意主题独占): 抽取 / 计时 / 刻痕 / 奖励休息.

从 app.py 的 BreakTimerApp 拆出来的 mixin: 状态都在 self 上, 这里只放方法.
界面在 pages/prescript_page.py, 规则在 prescript.py.
"""

import random
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk

import comm_data
import config
from app_base import _play_audio_file
from config import COLORS, IDLE, ON_BREAK, RUNNING


class PrescriptMixin:
    # ---- 指令 (Prescript, 都市之意主题独占) ----

    def _prescript_enabled(self) -> bool:
        return getattr(config, "THEME", "") == "urban_will"

    def _today_hours_live(self) -> float:
        """今天的专注小时数, 包括正在进行、还没记进 study_log 的这一段."""
        hours = float(self.dm.get_today_hours())
        if self.state == RUNNING and self.start_wall is not None:
            now = datetime.now()
            start = max(self.start_wall,
                        now.replace(hour=0, minute=0, second=0, microsecond=0))
            if now > start:
                hours += (now - start).total_seconds() / 3600.0
        return hours

    def _prescript_tick(self):
        self.prescript_job = None
        try:
            if not self.dm.resetting:
                self.prescripts.tick()
            self._sync_prescript_marks()
        except Exception as e:
            self._log_window(f"ERROR    prescript tick {type(e).__name__}: {e}")
        if not self.shutting_down:
            self.prescript_job = self.root.after(1000, self._prescript_tick)

    def _prescript_page_event(self, on: bool):
        """别的设备进入 / 退出了指令页 (同步来的事件): 本机跟着进入 / 退出 (只在都市之意主题下, MECHANICS 22.8)."""
        if self.shutting_down or not self._prescript_enabled() or "prescript" not in self.pages:
            return
        cur = getattr(self, "current_page", "")
        self._page_from_sync = True
        try:
            if on and cur != "prescript" and self.sleep_since is None:
                self.show_page("prescript")
            elif not on and cur == "prescript":
                self._exit_prescript()
        finally:
            self._page_from_sync = False

    def _exit_prescript(self):
        back = getattr(self, "_prescript_back", "timer")
        self.show_page(back if back in self.pages and back != "prescript" else "timer")

    def _prescript_remaining_rest(self) -> float:
        """用户剩余的休息时间 (秒): 正在休息是这次休息的倒计时, 否则是积攒的休息时间 (指令 III 按它的 50% 算)."""
        if self.state == ON_BREAK:
            return max(0, int(self.countdown_seconds))
        return max(0, int(self.break_carry))

    def _prescript_hour_log(self) -> str:
        """今天的整点记录, 一行一小时 ("09:00  内容"); 没有启用整点记录就是空白 (X 词条输入框的第一段)."""
        if not self.dm.hour_enabled:
            return ""
        today = datetime.now().strftime("%Y-%m-%d")
        lines = []
        for hour in range(24):
            text = self.dm.get_hour_text(today, hour)
            if text:
                lines.append(f"{hour:02d}:00  {text}")
        return "\n".join(lines)

    def _prescript_focus_log(self) -> str:
        """今天每一段专注的时间段, 一行一段 ("09:05-09:55 专注 (50 分钟)"; 还在进行的一段写到此刻); 没有就是空白 (X 词条输入框的第二段)."""
        now = datetime.now()
        intervals = [x for x in self.dm.day_intervals(now, self._calendar_live(), now) if x[0] == "study"]
        return "\n".join(comm_data.interval_lines(intervals, now, live_word=""))

    def _prescript_changed(self):
        self._sync_prescript_marks()
        page = getattr(self, "pages", {}).get("prescript")
        if page is None:
            return
        try:
            page.refresh()
        except Exception:
            pass

    def _prescript_completed(self):
        """完成了一条指令: 随机放一个指令音效 (和领取时的"滴答"是同一批)."""
        if not self._prescript_enabled() or sys.platform != "win32":
            return
        now = time.monotonic()
        if now - self._last_complete_sound < 0.5:        # 同一秒完成好几条只响一次
            return
        self._last_complete_sound = now
        path = config.asset_path("themes", "urban_will", "audio",
                                 f"ticktock_type{random.randint(1, 3)}.mp3")
        threading.Thread(target=_play_audio_file, args=(path,), daemon=True).start()

    def _prescript_mark_image(self):
        """指令标记 (几乎看不见的蓝花, 文件里已经是半透明), 缩到和按钮差不多高; 文件不在就是 None."""
        if self._mark_img is None:
            path = config.asset_path("themes", "urban_will", "image", "prescript_mark.png")
            try:
                img = tk.PhotoImage(file=path)
                target = config.scaled(20)
                self._mark_img = img.subsample(max(1, round(img.width() / target)))
            except Exception:
                self._mark_img = False
        return self._mark_img or None

    def _prescript_mark_wanted(self) -> frozenset:
        """现在要在哪些按钮上画标记: 专注 / 休息 的开始按钮, 侧边栏的合约 / 统计入口."""
        if not self._prescript_enabled():
            return frozenset()
        marks = self.prescripts.marks()
        want = set()
        # 开始专注: 空闲或休息中才是"开始"按钮 (专注中是"结束专注", 外出中停用)
        if "focus" in marks and self.state in (IDLE, ON_BREAK) and self.behavior.active() is None:
            want.add("btn_action")
        if "break" in marks and self.state != ON_BREAK:         # 休息中它是"暂停休息"
            want.add("btn_break")
        if "contract" in marks:
            want.add("nav:contract")
        if "stats" in marks:
            want.add("nav:stats")
        return frozenset(want)

    def _mark_target(self, name):
        if name.startswith("nav:"):
            return self.nav_btns.get(name[4:])
        return getattr(self, name, None)

    def _mark_bg(self, b, hover: bool) -> str:
        """按钮此刻的底色 (标记的底色要和它一样, 半透明的图才不会露出一块方形)."""
        try:
            if isinstance(b, ttk.Button):
                st = ttk.Style()
                style = str(b.cget("style") or "TButton")
                if b.instate(["disabled"]):
                    state = ["disabled"]
                else:
                    state = ["active"] if hover else []
                return st.lookup(style, "background", state) or COLORS["bg"]
            return str(b.cget("activebackground" if hover else "bg"))
        except tk.TclError:
            return COLORS["bg"]

    def _mark_recolor(self, name):
        lab = self._mark_labels.get(name)
        b = self._mark_target(name)
        if lab is None or b is None:
            return
        try:
            under = b.winfo_containing(b.winfo_pointerx(), b.winfo_pointery())
            lab.configure(bg=self._mark_bg(b, under in (b, lab)))
        except (tk.TclError, KeyError):
            pass

    def _make_mark(self, name, b, img):
        """标记: 按钮一端的一个小图 (不压在文字上). 点它等于点按钮, 鼠标移上去按钮照样高亮."""
        lab = tk.Label(b, image=img, bd=0, highlightthickness=0, cursor="hand2",
                       bg=self._mark_bg(b, False))
        if name.startswith("nav:"):          # 侧边栏的文字靠左: 标记放右端; 专注 / 休息按钮的文字居中: 放左端
            lab.place(relx=1.0, x=-6, rely=0.5, anchor="e")
        else:
            lab.place(relx=0.0, x=6, rely=0.5, anchor="w")

        def click(_e=None):
            try:
                b.invoke()
            except tk.TclError:
                pass

        def enter(_e=None):
            try:
                b.event_generate("<Enter>")          # 指针进了标记, 按钮会收到 Leave: 补一个 Enter 让它保持高亮
            except tk.TclError:
                pass
            self._mark_recolor(name)

        lab.bind("<Button-1>", click)
        lab.bind("<Enter>", enter)
        lab.bind("<Leave>", lambda e: self.root.after_idle(lambda: self._mark_recolor(name)))
        b.bind("<Enter>", lambda e: self.root.after_idle(lambda: self._mark_recolor(name)), add="+")
        b.bind("<Leave>", lambda e: self.root.after_idle(lambda: self._mark_recolor(name)), add="+")
        self._mark_labels[name] = lab

    def _sync_prescript_marks(self):
        if not hasattr(self, "nav_btns"):
            return
        try:
            want = self._prescript_mark_wanted()
        except Exception:
            return
        img = self._prescript_mark_image() if want else None
        if want and img is None:
            want = frozenset()
        if want != self._marks_shown:
            shown = self._marks_shown or frozenset()
            self._marks_shown = want
            for name in shown | want:
                try:
                    lab = self._mark_labels.pop(name, None)
                    if lab is not None:
                        lab.destroy()
                    b = self._mark_target(name)
                    if name in want and b is not None:
                        self._make_mark(name, b, img)
                except tk.TclError:
                    pass
        # 按钮的底色会变 (选中的页 / 可用与否): 每次都跟一下
        for name in self._mark_labels:
            self._mark_recolor(name)

    def _grant_prescript_rest(self, seconds: int, code: str = ""):
        """指令给的休息时间: 正在休息就加进这次休息, 否则存进积攒的休息时间."""
        seconds = max(0, int(seconds))
        if seconds <= 0 or self.dm.resetting:
            return
        self._log_notice(f"PRESCRIPT +{seconds}s {code}".rstrip())
        if self.state == ON_BREAK:
            self.countdown_seconds += seconds
            if self.break_end_wall is not None:
                self.break_end_wall += timedelta(seconds=seconds)
            self._save_timer_state()
        else:
            self._set_break_carry(self.break_carry + seconds)
            if self.state == IDLE and hasattr(self, "lbl_sub"):
                try:
                    self.lbl_sub.config(text=self._idle_sub_text())
                except Exception:
                    pass
