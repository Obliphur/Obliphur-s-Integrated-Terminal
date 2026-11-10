"""配置期 / 第一次打开的快捷方式 / 重置页 (MECHANICS 9).

从 app.py 的 BreakTimerApp 拆出来的 mixin: 状态都在 self 上, 这里只放方法.
"""

import threading
import time

import tkinter as tk
from tkinter import ttk

import config
import continuity
import devsync
import securefile
import shortcut
import toast
from config import APP_NAME, COLORS
from data_manager import USERNAME_MAX
from i18n import tr
from pages.widgets import flavor


class MiscMixin:
    # ---- 配置期 ----

    def _ask_username(self):
        def done(name):
            self.dm.set_username(name)
            self._log_notice(f"USERNAME set: {name}")
            if self._first_run:
                self.root.after(200, self._show_welcome)
        toast.prompt_name(tr("👤  你的名字"), tr("初次见面.怎么称呼你?"), done, limit=USERNAME_MAX)

    def _show_welcome(self):
        hours = int(config.SETUP_HOURS)
        msg = (tr("联网要先申请,等过冷静期才连上,每天有额度.")
               + "\n\n" + tr("接下来{h}小时是配置期:所有修改立即生效.").format(h=hours)
               + "\n" + tr("之后每次修改都要等 48 小时.")
               + "\n\n" + tr("第一次可以在 设置 → 日常 选择行为模式 (这一次立即生效)."))
        # 快捷方式的三个选项就放在介绍窗口里, 点"开始使用"或"去设置"时照勾选的做
        toast.notice(tr("👋  欢迎使用 OIT"), msg, [
            (tr("去设置"), self._open_basic_settings, True), (tr("开始使用"), None, False)],
            options=[("desktop", tr("创建桌面快捷方式"), True),
                     ("start", tr("固定到开始菜单"), True),
                     ("taskbar", tr("固定到任务栏"), False)],
            on_options=self._make_shortcuts)

    # ---- 第一次打开: 快捷方式 (介绍窗口里勾选) ----

    def _make_shortcuts(self, opts: dict):
        if not any(opts.values()):
            return

        def run():
            res = shortcut.install(opts.get("desktop", False), opts.get("start", False),
                                   opts.get("taskbar", False), APP_NAME)
            self._log_notice("SHORTCUT " + ", ".join(f"{k}={'ok' if ok else 'no'}:{info}"
                                                     for k, (ok, info) in res.items()))
            self._on_main(lambda: self._report_shortcuts(res))
        threading.Thread(target=run, daemon=True).start()

    def _report_shortcuts(self, res: dict):
        names = {"desktop": tr("桌面快捷方式"), "start": tr("开始菜单"), "taskbar": tr("任务栏")}
        done = [names[k] for k, (ok, _i) in res.items() if ok]
        failed = [f"{names[k]}: {info}" for k, (ok, info) in res.items()
                  if not ok and info not in ("manual",)]
        lines = []
        if done:
            lines.append(tr("已完成: {items}", items=", ".join(done)))
        if "start" in res and res["start"][0]:
            lines.append(tr("OIT 在开始菜单的\"所有应用\"里; 没有出现在固定列表时, 在开始菜单里右键 OIT → 固定到\"开始\"."))
        if res.get("taskbar", (True, ""))[1] == "manual":
            lines.append(tr("Windows 不允许程序自己固定到任务栏. 已在资源管理器里选中 OIT 的快捷方式: 右键 → 显示更多选项 → 固定到任务栏."))
        if failed:
            lines.append(tr("失败: {items}", items="; ".join(failed)))
        if lines:
            toast.notice(tr("🖥  快捷方式"), "\n\n".join(lines))

    def _open_basic_settings(self):
        self.show_page("settings")
        try:
            self.pages["settings"].show_tab("basic")
        except Exception:
            pass

    def end_setup_period(self):
        self.settings.end_setup()
        self._setup_was_active = False
        self._log_notice("SETUP period ended by user")
        self._after_setup_change()
        toast.info(tr("配置期已结束."))
        self.check_achievements()

    def _check_setup_period(self):
        """配置期自然到期时提示一次, 并刷新界面上的标记."""
        active = self.settings.in_setup()
        if self._setup_was_active and not active:
            self._setup_was_active = False
            self._log_notice("SETUP period ended (time is up)")
            self._after_setup_change()
            toast.notice(tr("⏳  配置期结束了"),
                         tr("之后的修改都要等 48 小时."))


    def _build_reset_page(self, page):
        bg, card = COLORS["bg"], COLORS["bg_card"]
        danger = COLORS["danger"]

        hdr = tk.Frame(page, bg=bg)
        hdr.pack(fill="x", padx=28, pady=(22, 8))
        tk.Label(
            hdr, text=tr("🔄  重置"), bg=bg, fg=COLORS["text"],
            font=config.font("h1", bold=True)
        ).pack(anchor="w")

        ttk.Separator(page, orient="horizontal", style="UI.TSeparator").pack(fill="x", padx=26)

        warn_fr = tk.Frame(page, bg=COLORS["danger_bg"], highlightbackground=danger, highlightthickness=1)
        warn_fr.pack(padx=26, pady=(16, 10), fill="x")
        tk.Label(
            warn_fr, text=tr("⚠  警告:这会永久删除全部数据"),
            bg=COLORS["danger_bg"], fg=danger, font=config.font("body", bold=True),
            anchor="w"
        ).pack(fill="x", padx=16, pady=10)

        form = tk.Frame(page, bg=bg)
        form.pack(fill="x", padx=26, pady=(0, 16))
        row = tk.Frame(form, bg=bg)
        row.pack(anchor="w", pady=(0, 6))
        ttk.Label(row, text=tr("输入 RESET 以确认"), style="UI.TLabel").pack(side="left", padx=(0, 8))
        ttk.Entry(row, textvariable=self.var_confirm, width=16).pack(side="left")
        self.btn_reset = ttk.Button(
            form, text=tr("🔄  重置并清除数据"),
            style="Danger.TButton", command=self.on_reset
        )
        self.btn_reset.pack(anchor="w", ipadx=10)
        self.btn_reset.state(["disabled"])

        flavor(page, tr("就这样回到一切之初,修正错误的抉择."), bg=bg).pack(fill="x", padx=26, pady=(4, 16))

    def _check_offline_chime(self):
        """每 5 秒: 有绑定的设备刚离线时, 在焦点设备 (最后有人操作的那台) 上响一次提示音 (MECHANICS 15.2)."""
        try:
            peers = self.sync.peers()
            sts = {st["id"]: st for st in self.sync.device_states() if st["id"] in peers and not st["online"]}
            now_off = set(sts)
        except Exception:
            return
        was = getattr(self, "_offline_seen", None)
        self._offline_seen = now_off
        if was is None or not (now_off - was) or not self.sync.is_latest_active():
            return                                   # 刚打开时已经离线的不响
        if self.behavior.current() is not None:
            return                                   # 外出中: 其他设备离线是正常的
        names = ", ".join(str(peers[i].get("name") or "?") for i in sorted(now_off - was))
        # 记下为什么算离线 (最近一次同步失败的原因, 多久没有它的消息), 以后好查
        why = "; ".join(f"{peers[i].get('name') or '?'}: err={sts[i].get('err') or '-'} "
                        f"silent={int((time.time() * 1000 - float(sts[i].get('seen') or 0)) / 1000)}s"
                        f"{' outdated' if sts[i].get('outdated') else ''}" for i in sorted(now_off - was))
        self._log_notice(f"SYNC offline {names} ({why})")
        self._play_chime()
        toast.warn(tr("{name} 离线了.", name=names), clear=True)   # 离线消息黑雾也不遮 (18.3)

    def _check_unbind(self):
        """每 5 秒: 冷静期 (120 小时) 到了的解除绑定就解除, 通知那台设备 (它的数据不动), 并清空本机的数据 (MECHANICS 15.1).
        devsync.UNBIND_FREE 为真 (无代价解绑) 时: 马上解除, 并且不清空本机."""
        try:
            names = self.sync.check_unbind()
            for name in names:
                self._log_notice(f"SYNC unbind done {name}")
            if names and not devsync.UNBIND_FREE:
                self.do_reset(leave=True, unbound=", ".join(names))          # 只重置操作这一台: 不转告其他设备, 也不清空对方
            if names:
                return
            # 绑定的设备的 OIT 和联动程序连续 24 小时都拒绝连接 = 它被卸载了 (手机上的设备管理员拦不住卸载): 只解除和它的绑定,
            # 并清掉本机保留的它的数据 (账, 状态); 本机自己的数据不动 (MECHANICS 9.2)
            gone = self.sync.check_gone(hold=self.behavior.current() is not None)        # 外出期间不算 (可以关掉 OIT)
            for name in gone:
                self._log_notice(f"SYNC peer gone (refusing connections for 24 h, uninstalled?) {name}")
                toast.warn(tr("{name} 连续 24 小时拒绝连接, 判定为已卸载: 已解除和它的绑定, 它的数据已从本机清除.", name=name))
            if gone:
                self.on_sync_changed()
        except Exception as e:
            self._log_window(f"ERROR    unbind {type(e).__name__}: {e}")

    def _on_confirm_change(self, *_):
        ok = self.var_confirm.get().strip() == "RESET"
        self.btn_reset.state(["!disabled" if ok else "disabled"])

    def refresh_reset(self):
        self.var_confirm.set("")

    def on_reset(self):
        if self.var_confirm.get().strip() != "RESET":
            return
        if not toast.ask(
            tr("这将永久删除全部数据,不可撤销.") + ("\n" + tr("绑定的设备也会清空数据 (绑定保留).")
                                                    if self.sync.peers() else ""),
            title=tr("重置确认"), danger=True
        ):
            return
        self.do_reset()

    def do_reset(self, wipe_id: str = None, exclude=(), by: str = "", leave: bool = False, unbound: str = ""):
        """重置: 先请绑定的设备也清空 OIT 的数据 (绑定保留), 解除网络限制 (还原系统代理, 启用网卡),
        删除全部数据 (只留下设备同步的绑定), 再重新打开程序. 新进程找不到设置文件, 就像第一次安装:
        弹出介绍窗口, 开始配置期, 网络按默认设置接管.
        wipe_id / exclude / by: 是别的设备 (名字 by) 请本机清空的 —— 同一个请求转告给本机绑定的其他设备.
        leave: 是它解除了和本机的绑定 (15.1): 只重置本机, 不转告其他设备 (和它们的绑定照样留着)."""
        if self.dm.resetting:
            return
        self.dm.resetting = True
        self._cancel_jobs()
        self._close_check_win()
        kept = None
        try:
            if not leave:
                self.sync.send_wipe(wipe_id or self.sync.new_wipe_id(), exclude=exclude)   # MECHANICS 9
            kept = self.sync.kept_state(by, leave, unbound)
        except Exception:
            pass
        self.sync.stop()
        self._stop_all_recorders(wait=8.0)
        self.net.release()
        self.dm.erase_all_data()
        securefile.reset()                  # 数据文件夹没了: 内存里的密钥作废, 之后写的绑定是明文, 下次启动再加密 (MECHANICS 9.4)
        if continuity.current is not None:
            continuity.current.reset()      # 重置: 数据以外的锚点也清掉, 下一次启动算新数据 (MECHANICS 9.3)
        if kept:
            try:
                devsync.write_state(self.sync.file, kept)
            except OSError:
                pass
        self.restart_requested = True      # main.py 在主循环结束后重新打开程序
        self.root.destroy()
