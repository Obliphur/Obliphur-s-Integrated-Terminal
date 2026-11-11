"""睡觉 (MECHANICS 23) / 外出与行为模式 (MECHANICS 21) / 提前启动 (MECHANICS 20).

从 app.py 的 BreakTimerApp 拆出来的 mixin: 状态都在 self 上, 这里只放方法.
计时器本体在 app/app_timer.py; 睡觉时的整点记录直接改 DataManager, 见 MECHANICS 23.1.
"""

import os
import tkinter as tk
from datetime import datetime
from tkinter import ttk

import config
import prestart
import toast
from app.app_base import _hours_text
from config import COLORS, IDLE, RUNNING, fmt_hm, fmt_ms
from i18n import tr
from pages.outing_dialog import OutingDialog
from pages.prestart_dialog import PrestartDialog


class DailyMixin:
    # ---- 睡眠 (MECHANICS 23): 独立的一种时间, 不同于专注 / 休息 / 空闲 ----

    def _sleep_click(self):
        if self.sleep_since is not None:
            return
        if self.contract.active:
            toast.warn(tr("合约中不能睡眠."))
            return
        if self.behavior.active() is not None:
            toast.warn(tr("外出中不能睡眠."))
            return
        if self.state != IDLE:
            toast.warn(tr("请先结束专注 / 休息再睡眠."))
            return
        self.start_sleep()

    def start_sleep(self):
        self._mine()
        self.idle_since = None
        self.sleep_since = datetime.now().replace(microsecond=0)
        self._save_timer_state()
        self._fill_sleep_hours()                    # 睡着了: 这一小时的整点记录马上填上"睡眠"并锁定
        self._show_sleep_cover()
        self._sync_timer_buttons()
        self._log_notice(f"SLEEP start {self.sleep_since:%H:%M:%S}")
        if self.sync.peers():
            self.sync.sync_now()

    def wake(self):
        """起床: 这段睡眠记下来 (睡眠是单独的一种时间), 回到空闲."""
        since = self.sleep_since
        if since is None:
            return
        self._mine()
        now = datetime.now()
        self.sleep_since = None
        self.dm.add_focus_session("sleep", since, now)
        self.dm.relabel_sleep(since, now)       # 睡眠期间已经自动记成"分心"的小时改成"睡眠"
        self.dm.unfill_short_sleep(since, now)  # 睡着时马上记成"睡眠"、结果睡不满半小时的小时退回去
        self._refresh_if_visible("log")
        self._save_timer_state()
        self._hide_sleep_cover()
        self._arm_idle_nudge()
        self._sync_timer_buttons()
        self._refresh_if_visible("cal", "stats")
        self._log_notice(f"SLEEP end {now:%H:%M:%S} ({int((now - since).total_seconds())}s)")
        if self.sync.peers():
            self.sync.sync_now()

    def _fill_sleep_hours(self):
        try:
            if self.dm.fill_sleep_now():
                self._refresh_if_visible("log")
                self.update_log_badge()
        except Exception as e:
            self._log_window(f"ERROR    sleep fill {type(e).__name__}: {e}")

    def _check_sleep(self):
        """每 5 秒: 睡眠中却进入了专注 / 休息 / 外出 / 合约 (例如别的设备同步过来的) 就起床; 页面被拆掉了就重新盖上.
        睡眠中每个新的一小时, 整点记录马上填上"睡眠"."""
        if self.sleep_since is None:
            return
        self._fill_sleep_hours()
        if self.state != IDLE or self.contract.active or self.behavior.active() is not None:
            self.wake()
            return
        cover = self._sleep_cover
        try:
            alive = cover is not None and cover.winfo_exists()
        except tk.TclError:
            alive = False
        if not alive:
            self._show_sleep_cover()

    def _recover_sleep(self):
        """启动时: 上次关掉程序时还在睡眠 → 接着睡 (程序关着的这段也算睡眠)."""
        rec = self._bonus_state().get("sleep")
        if not isinstance(rec, dict):
            return
        try:
            self.sleep_since = datetime.fromisoformat(rec["since"])
        except (KeyError, TypeError, ValueError):
            return
        self.timer_owner = str(rec.get("owner") or "")
        self._show_sleep_cover()
        self._sync_timer_buttons()

    def _show_sleep_cover(self):
        """盖住整个窗口 (侧边栏也盖住): 空白背景, 正中一个"起床". 程序照常运行, 只是用户碰不到别的功能."""
        self._hide_sleep_cover()
        bg = COLORS["bg"]
        cover = tk.Frame(self.root, bg=bg)
        cover.place(x=0, y=0, relwidth=1, relheight=1)
        icon = self._sleep_icon() if getattr(config, "THEME", "") == "urban_will" else None
        if icon is not None:
            # 都市之意: 正中是指令页的那个图标, "醒来" 在它下面一点
            self._sleep_img = icon
            tk.Label(cover, image=icon, bg=bg, bd=0).place(relx=0.5, rely=0.5, anchor="center")
            btn = ttk.Button(cover, text=tr("醒来"), style="Accent.TButton", command=self.wake)
            btn.place(relx=0.5, rely=0.5, y=icon.height() // 2 + config.scaled(36), anchor="n")
        else:
            btn = ttk.Button(cover, text=tr("☀  起床"), style="Accent.TButton", command=self.wake)
            btn.place(relx=0.5, rely=0.5, anchor="center")
        cover.lift()
        self._sleep_cover, self._sleep_btn = cover, btn
        try:
            btn.focus_set()
        except tk.TclError:
            pass
        if not getattr(self, "_sleep_focus_bound", False):
            self._sleep_focus_bound = True
            self.root.bind_all("<FocusIn>", self._sleep_focus_guard, add="+")

    @staticmethod
    def _sleep_icon():
        """指令页中间的那个图标 (都市之意主题的睡眠页也用它); 找不到返回 None."""
        path = config.asset_path("themes", "urban_will", "image", "Index.png")
        if not os.path.isfile(path):
            return None
        try:
            img = tk.PhotoImage(file=path)
            target = config.scaled(128)
            if img.width() > target:
                img = img.subsample(max(1, round(img.width() / target)))
            elif img.width() < target:
                img = img.zoom(max(1, round(target / img.width())))
            return img
        except Exception:
            return None

    def _hide_sleep_cover(self):
        cover = self._sleep_cover
        self._sleep_cover = None
        if cover is not None:
            try:
                cover.destroy()
            except tk.TclError:
                pass

    def _sleep_focus_guard(self, event):
        """睡眠中: 键盘焦点跑到被盖住的控件上 (Tab) 就拉回"起床"按钮, 免得用空格 / 回车按到下面的按钮."""
        cover = self._sleep_cover
        if cover is None:
            return
        try:
            w = event.widget
            if isinstance(w, str) or str(w.winfo_toplevel()) != str(self.root):
                return
            if not str(w).startswith(str(cover)):
                self._sleep_btn.focus_set()
        except Exception:
            pass

    # ---- 行为模式: 外出 (MECHANICS 21) ----

    def _can_reward_outing(self, outing=None) -> bool:
        """休息时间只由开始这次外出的设备发 (和计时器的记录只记一次一样)."""
        try:
            mine = self.sync.my_id
            if not mine or not self.sync.peers():
                return True
        except Exception:
            return True
        owner = str((outing or {}).get("owner") or "")
        return not owner or owner == mine

    def _outing_click(self):
        active = self.behavior.active()
        if active is not None:
            h = self.behavior.recorded_hours(active)
            if toast.ask(
                tr("结束外出?"),
                tr("已经记录了 {h} 小时. 现在结束外出吗?").format(h=_hours_text(h)),
                ok_text=tr("结束外出"), danger=True
            ):
                self._end_outing()
            return
        if self.behavior.away() is not None:
            # 自由学习的外出: 什么都没限制, 直接问一下结不结束 (MECHANICS 21.6)
            if toast.ask(tr("结束外出?"), tr("现在结束外出吗?"), ok_text=tr("结束外出"), danger=True):
                self._end_outing()
            return
        if self.behavior.enabled():
            if self.contract.active:
                toast.warn(tr("合约中不能外出."))
                return
            if self.state != IDLE:
                toast.warn(tr("请先结束专注 / 休息再外出."))
                return
        win = getattr(self, "_outing_win", None)
        try:
            if win is not None and win.winfo_exists():
                win.lift()
                return
        except Exception:
            pass
        self._outing_win = None
        self._outing_win = OutingDialog(self.root, self._start_outing,
                                        max_hours=int(config.OUTING_MAX_HOURS),
                                        name=self._outing_key(self.behavior.mode()))

    def _start_outing(self, hours: int):
        # 填时长的窗口开着时, 可能已经开始了合约 / 专注 (MECHANICS 21.1); 自由学习的外出不受限制 (21.6)
        if self.behavior.enabled():
            if self.contract.active:
                toast.warn(tr("合约中不能外出."))
                return
            if self.state != IDLE:
                toast.warn(tr("请先结束专注 / 休息再外出."))
                return
        ok, msg, o = self.behavior.start(hours, owner=(self.sync.my_id or ""))
        if not ok:
            toast.warn(tr(msg))
            return
        self._outing_was_active = not self.behavior.is_free(o)
        self._log_notice(f"OUTING start {hours}h until {o['end']:%Y-%m-%d %H:%M}")
        toast.success(tr("外出 {h} 小时, 到 {at} 结束.").format(
            h=hours, at=f"{o['end']:%m-%d %H:%M}"))
        self._sync_timer_buttons()
        self._behavior_tick()
        if self.sync.peers():
            self.sync.sync_now()

    def _end_outing(self):
        was_active = self._outing_was_active
        res = self.behavior.stop(can_reward=self._can_reward_outing)
        self._outing_was_active = False
        self._apply_outing_result(res)
        self._sync_timer_buttons()
        if self.state == IDLE and was_active:
            self.reset_to_idle()
        if self.sync.peers():
            self.sync.sync_now()

    def _apply_outing_result(self, res: dict) -> bool:
        reward = int((res or {}).get("reward") or 0)
        if reward <= 0:
            return False
        hours = float((res or {}).get("hours") or 0)
        self._set_break_carry(self.break_carry + reward)
        toast.success(tr("外出结束: 记录了 {h} 小时, 获得 {t} 休息时间.").format(
            h=_hours_text(hours), t=fmt_ms(reward)), timeout=10000)
        self._sync_bonus_button()
        self._refresh_if_visible("stats", "cal")
        return True

    def _show_outing(self, active: dict):
        now = datetime.now()
        left = max(0, int((active["end"] - now).total_seconds()))
        h = self.behavior.recorded_hours(active, now)
        self.lbl_time.config(text=fmt_hm(left))
        self.lbl_sub.config(text=tr("外出中 · 已经记录 {h} 小时").format(h=_hours_text(h)))
        self._status(tr("外出中 🚪"), COLORS["warn"])

    def _behavior_tick(self, initial: bool = False):
        """每秒: 到时间的外出自动结束, 发休息时间; 外出中更新 Timer 页."""
        if self.shutting_down or not hasattr(self, "behavior"):
            return
        now = datetime.now()
        was = self._outing_was_active
        self.behavior.sync_mode(now)
        if not self.behavior.enabled() and self.behavior.active(now) is not None:
            self._apply_outing_result(self.behavior.stop(now, can_reward=self._can_reward_outing))
            was = True
        c = getattr(self, "contract", None)
        if c is not None and c.active and self.behavior.active(now) is not None:
            # 别的设备同步来的合约 / 外出撞上了: 外出在合约开始时结束 (MECHANICS 21.1)
            o = self.behavior.yield_to_contract(c.start_at, now)
            if o is not None:
                self._log_notice(f"OUTING yield to contract, stopped {o['stopped']:%Y-%m-%d %H:%M}")
                toast.warn(tr("合约进行中, 外出已结束."))
                was = True
                if self.sync.peers():
                    self.sync.sync_now()
        ev = self.behavior.tick(now, can_reward=self._can_reward_outing)
        if ev.get("reward"):
            self._apply_outing_result(ev)
            was = True
        elif ev.get("ended"):
            toast.info(tr("外出结束."))
            was = was or any(not self.behavior.is_free(o) for o in ev["ended"])      # 自由学习的外出结束不用重置 Timer 页
            if self.sync.peers():
                self.sync.sync_now()               # 外出的设备回来了: 马上同步 (它的记录有优先权, MECHANICS 21.6)
        active = self.behavior.active(now)
        if active is not None:
            self._outing_was_active = True
            if self.state == IDLE:
                self._show_outing(active)
        elif (was or self._outing_was_active) and self.state == IDLE:
            self._outing_was_active = False
            self.reset_to_idle()
        self._sync_outing_button()
        self._sync_bonus_button()

    def _sync_prestart_button(self):
        """提前启动按钮: 专注中显示; 选过以后在操作空窗期里变成"提前终止"."""
        btn = getattr(self, "btn_prestart", None)
        if btn is None:
            return
        try:
            show = (self.state == RUNNING and not self.behavior.active()
                    and (not self.prestart.chosen or self.prestart.in_grace))
            text = tr("⏹  提前终止") if self.prestart.in_grace else tr("⏩  提前启动")
            if btn.cget("text") != text:
                btn.config(text=text)
            if show and not btn.winfo_ismapped():
                try:
                    btn.pack(side="left", ipadx=6, padx=(10, 0), before=self.btn_outing)
                except Exception:
                    btn.pack(side="left", ipadx=6, padx=(10, 0))
            elif not show and btn.winfo_ismapped():
                btn.pack_forget()
        except Exception:
            pass

    # ---- 提前启动 (仅 PC, MECHANICS 20) ----

    def _prestart_click(self):
        """专注中点"提前启动": 扫一遍程序, 弹窗选至多两个; 空窗期里点它就是"提前终止"."""
        if self.state != RUNNING:
            return
        if self.prestart.in_grace:                   # 操作空窗期: 提前终止, 马上藏 + 静音
            self.prestart.terminate()
            toast.info(tr("提前终止: 现在就藏起来并静音."))
            self._sync_prestart_button()
            return
        if self.prestart.chosen:
            return
        win = getattr(self, "_prestart_win", None)
        try:
            if win is not None and win.winfo_exists():
                win.lift()
                return
        except Exception:
            pass
        self._prestart_win = None
        try:
            apps = prestart.scan_programs()
        except Exception as e:
            toast.warn(tr("扫描程序失败: {e}", e=f"{type(e).__name__}: {e}"))
            return
        apps = self.prestart_list.apply(apps)        # 套上之前的添加 / 删除; 清单空了也能开, 好用文件管理器再加
        self._prestart_win = PrestartDialog(self.root, apps, self._prestart_chosen,
                                           limit=prestart.MAX_APPS, store=self.prestart_list)

    def _prestart_chosen(self, items):
        """选好了: 启动 (没在跑的), 进入 3 分钟的操作空窗期; 空窗期过了才藏窗口 / 静音."""
        if not items or self.state != RUNNING:
            return
        failed = self.prestart.start(items)
        self._sync_prestart_exempt()
        names = "、".join(str(i.get("name") or i.get("exe")) for i in items)
        msg = tr("提前启动: {names}. 3 分钟内可以先操作, 之后窗口藏起来、静音.", names=names)
        if failed:
            msg += "\n" + tr("打不开: {names}", names="、".join(failed))
        toast.info(msg, timeout=8000)
        self._log_notice("PRESTART " + ", ".join(str(i.get("exe")) for i in items))
        self._sync_timer_buttons()

    def _prestart_tick(self):
        """每秒: 跟着这些进程 (含子进程); 空窗期过了 (或提前终止) 就藏窗口 / 静音; 专注结束就放出来.
        另外管"触发隐藏的窗口": 程序名命中的窗口一直藏着, 但不给联网豁免."""
        if self.shutting_down:
            return
        try:
            try:
                self._behavior_tick()
            except Exception as e:
                # 这一步出错也不能让整个每秒循环停掉: 停了的话提前启动藏着的窗口就再也放不出来
                self._log_window(f"ERROR    behavior tick {type(e).__name__}: {e}")
            if self.prestart.active and self.state != RUNNING:
                try:
                    self._stop_prestart(park=True)
                except Exception as e:
                    self._log_window(f"ERROR    prestart stop {type(e).__name__}: {e}")
            if self.prestart.active:
                try:
                    self.prestart.step()
                except Exception as e:
                    self._log_window(f"ERROR    prestart {type(e).__name__}: {e}")
            try:
                self.prestart.auto_step()
            except Exception as e:
                self._log_window(f"ERROR    hidden windows {type(e).__name__}: {e}")
            self._sync_prestart_exempt()
            self._sync_prestart_button()
        finally:
            if not self.shutting_down:
                self.prestart_job = self.root.after(1000, self._prestart_tick)

    def _stop_prestart(self, park: bool = False, final: bool = False):
        """放出来: 显示藏起来的窗口 (不抢焦点), 取消静音, 清掉放行名单; 下次专注可以重新选.

        park=True: 记下这次选过的程序 —— 休息中直接开始专注时接着用 (MECHANICS 20).
        final=True: 关闭 OIT, 连"触发隐藏的窗口"也一起放出来."""
        if park:
            self.prestart.park()
        self.prestart.stop(final=final)
        self._sync_prestart_exempt()
        self._sync_timer_buttons()

    def _hidden_windows_changed(self):
        """设置 → 窗口 的"触发隐藏的窗口"保存后: 先把以前自动藏起来的放出来, 再按新列表重新藏."""
        self.prestart.release_auto()
        self._sync_prestart_exempt()

    def _sync_prestart_exempt(self):
        """提前启动的程序联网不受网站 / 应用名单和代理-全屏蔽限制 (MECHANICS 20); AI 接口的域名也例外放行.
        只有专注期间才有这个豁免; 休息中 / 空闲时照常按名单判定.
        "触发隐藏的窗口"只进 hidden_exempt (应用名单不关), 不进联网豁免."""
        try:
            rules = self.net.proxy.rules
            running = self.state == RUNNING
            rules.exempt = self.prestart.exe_names() if running else set()
            rules.hide_exempt = self.prestart.hidden_exes()
            # 操作空窗期: 新启动的程序 (开始时没在运行的) 不受应用 / 网站名单限制; 原来就开着的照旧
            rules.open_new_except = self.prestart.baseline_exes if running and self.prestart.in_grace else None
            rules.api_hosts = self._api_hosts()
        except Exception:
            pass

    def _api_hosts(self) -> set:
        """当前 AI 服务商 Base URL 的主机 (聊天 / 名单建议要用): 过滤代理不拦它."""
        try:
            import ai_assist
            from urllib.parse import urlsplit
            profs = [self.comm.ai.profile(c["id"]) for c in self.comm.ai.configs()]
            bases = [str(p.get("base_url") or "").strip() for p in profs]
            hosts = {(urlsplit(b).hostname or "").lower() for b in bases} - {""}
            if self.comm.net_active() and any(ai_assist.web_local(p) for p in profs):
                hosts |= set(ai_assist.SEARCH_HOSTS)        # 限时网络开着, 并且由 OIT 自己搜索 (DeepSeek 等): 搜索页的域名也放行
            return hosts
        except Exception:
            return set()
