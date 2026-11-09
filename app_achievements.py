"""成就 (MECHANICS 17): 条件判定 / 同步 / 弹窗 / 成就页入口.

从 app.py 的 BreakTimerApp 拆出来的 mixin: 状态都在 self 上, 这里只放方法.
成就本身的规则在 achievements.py.
"""

from datetime import datetime

import tkinter as tk

import achievements


class AchievementsMixin:
    # ---- 成就 (MECHANICS 17) ----

    def check_achievements(self):
        """每 5 秒 (以及启动时) 判定一遍; 新达成的弹出提示, 浮在窗口上直到手动关掉."""
        if self.shutting_down or self.dm.resetting:
            return
        study = {d: self.dm._study_all(d) for d in set(self.dm.study_data).union(
            *((led.get("study") or {}) for led in (self.dm.peer_ledgers() or [])))}
        facts = achievements.Facts(datetime.now(), self.ach.first, self.settings.in_setup(),
                                   study, self.dm.hour_days, self.contract.history,
                                   carry=self.break_carry)
        self._after_achievements(self.ach.check(facts))

    def on_achievements_synced(self, changes: dict):
        """别的设备达成的成就 (设备同步): 本机也算达成, 同样弹出提示."""
        self._after_achievements(self.ach.merge(changes))

    def _after_achievements(self, new):
        if new and "settings" in self.pages:
            self.pages["settings"].refresh_achievements()
        if new and self.current_page == "ach" and "ach" in self.pages:
            self.pages["ach"].refresh()
        self.ach_popups.sync()

    def _open_achievements(self):
        try:
            if self.root.state() in ("iconic", "withdrawn"):
                self.root.deiconify()
            self.root.lift()
        except tk.TclError:
            pass
        self.show_page("ach")
