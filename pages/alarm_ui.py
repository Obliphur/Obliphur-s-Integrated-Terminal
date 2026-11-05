"""闹钟的界面 (1.0.359): 设置 → 闹钟 页, 响铃弹窗, 沟通页 ⏰ 的"共时闹钟"弹窗. 逻辑在 alarm.py (本机闹钟) 和 comm.py (共时闹钟)."""
import os
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, ttk

import alarm as alarm_mod
import config
import toast
from config import COLORS
from i18n import tr
from .widgets import Modal, cfg, focus_name


def _check(parent, text, var, command, bg):
    return tk.Checkbutton(parent, text=text, variable=var, bg=bg, fg=COLORS["text"], selectcolor=bg,
                          activebackground=bg, activeforeground=COLORS["text"], font=config.font("body"),
                          anchor="w", command=command)


class AlarmSettings:
    """设置 → 闹钟 页的内容 (设置即时保存, 没有等待期): 开关 + 时间, 铃声 (+ 试听), 音量. page = SettingsPage."""

    def __init__(self, page, body):
        self.p = page
        self.a = page.alarm
        self.ringer = page.ringer
        self._vol_job = None
        if self.a is None:
            return
        card = COLORS["bg_card"]
        small = config.font("small")
        a = self.a

        fr, _head = page._card(body, "⏰  闹钟")
        self.var_on = tk.BooleanVar(value=bool(a.cfg["on"]))
        page._track(_check(fr, tr("开启 (每天重复)"), self.var_on, self._on_toggle, card), "开启 (每天重复)").pack(
            fill="x", padx=12, pady=(2, 2))
        row = tk.Frame(fr, bg=card)
        row.pack(fill="x", padx=12, pady=(2, 4))
        page._track(tk.Label(row, text=tr("时间"), bg=card, fg=COLORS["text"], font=config.font("body"), width=6,
                             anchor="w"), "时间").pack(side="left")
        h, m = a.hm()
        self.var_h, self.var_m = tk.StringVar(value=f"{h:02d}"), tk.StringVar(value=f"{m:02d}")
        self.sp_h = ttk.Spinbox(row, from_=0, to=23, width=4, wrap=True, format="%02.0f", textvariable=self.var_h,
                                command=self._on_time)
        self.sp_m = ttk.Spinbox(row, from_=0, to=59, width=4, wrap=True, format="%02.0f", textvariable=self.var_m,
                                command=self._on_time)
        self.sp_h.pack(side="left")
        tk.Label(row, text=" : ", bg=card, fg=COLORS["text"], font=config.font("body", bold=True)).pack(side="left")
        self.sp_m.pack(side="left")
        for sp in (self.sp_h, self.sp_m):
            sp.bind("<Return>", lambda _e: self._on_time())
            sp.bind("<FocusOut>", lambda _e: self._on_time())
        self.lbl_co = tk.Label(fr, text="", bg=card, fg=COLORS["accent"], font=small, anchor="w", justify="left", wraplength=560)
        self.lbl_next = tk.Label(fr, text="", bg=card, fg=COLORS["text_dim"], font=small, anchor="w", justify="left", wraplength=560)
        self.lbl_next.pack(fill="x", padx=12, pady=(0, 10))

        fr, _head = page._card(body, "🔔  铃声")
        row = tk.Frame(fr, bg=card)
        row.pack(fill="x", padx=12, pady=(2, 4))
        self._tones = [(k, tr(label)) for k, label in alarm_mod.TONES]
        self.var_tone = tk.StringVar()
        self.cb_tone = ttk.Combobox(row, textvariable=self.var_tone, state="readonly", width=16,
                                    values=[lab for _k, lab in self._tones])
        self.cb_tone.pack(side="left")
        self.cb_tone.bind("<<ComboboxSelected>>", lambda _e: self._on_tone())
        self.btn_preview = ttk.Button(row, text=tr("▶ 试听"), command=self._preview)
        self.btn_preview.pack(side="left", padx=(8, 0))
        self.row_custom = tk.Frame(fr, bg=card)
        self.btn_file = ttk.Button(self.row_custom, text=tr("选择文件…"), command=self._pick_file)
        self.btn_file.pack(side="left")
        self.lbl_file = tk.Label(self.row_custom, text="", bg=card, fg=COLORS["text_dim"], font=small, anchor="w")
        self.lbl_file.pack(side="left", padx=(8, 0))
        self.lbl_tone_hint = tk.Label(fr, text=tr("铃声会循环播放, 直到你在弹窗里关掉 (最多 5 分钟). 睡眠中也会响."), bg=card,
                                      fg=COLORS["text_dim"], font=small, anchor="w", justify="left", wraplength=560)
        self.lbl_tone_hint.pack(fill="x", padx=12, pady=(2, 10))

        fr, _head = page._card(body, "🔊  音量")
        row = tk.Frame(fr, bg=card)
        row.pack(fill="x", padx=12, pady=(2, 10))
        self.var_vol = tk.IntVar(value=int(a.cfg["volume"]))
        self.scale = tk.Scale(row, from_=0, to=100, orient="horizontal", variable=self.var_vol, showvalue=False,
                              bg=card, fg=COLORS["text"], troughcolor=COLORS["bg"], highlightthickness=0,
                              activebackground=COLORS["accent"], command=lambda _v: self._on_volume())
        self.scale.pack(side="left", fill="x", expand=True)
        self.lbl_vol = tk.Label(row, text="", bg=card, fg=COLORS["text"], font=config.font("body"), width=6, anchor="e")
        self.lbl_vol.pack(side="left", padx=(8, 0))
        self._sync_tone_ui()
        self.refresh()

    # ---- 即时保存 ----
    def _on_toggle(self):
        self.a.set(on=self.var_on.get())
        self.refresh()

    def _on_time(self):
        try:
            h, m = int(float(self.var_h.get())), int(float(self.var_m.get()))
        except ValueError:
            h, m = self.a.hm()
        h, m = max(0, min(23, h)), max(0, min(59, m))
        self.var_h.set(f"{h:02d}")
        self.var_m.set(f"{m:02d}")
        self.a.set(time=f"{h}:{m:02d}")
        self.refresh()

    def _on_tone(self):
        label = self.var_tone.get()
        key = next((k for k, lab in self._tones if lab == label), "classic")
        if key == "custom" and not self.a.cfg.get("custom"):
            self._pick_file()
            if not self.a.cfg.get("custom"):
                self._sync_tone_ui()
                return
        self.a.set(tone=key)
        self._sync_tone_ui()

    def _pick_file(self):
        path = filedialog.askopenfilename(
            title=tr("选择铃声"), parent=self.p.winfo_toplevel(),
            filetypes=[(tr("音频文件"), "*.wav *.mp3 *.m4a *.aac *.wma"), (tr("所有文件"), "*.*")])
        if path:
            self.a.set(custom=path, tone="custom")
            self._sync_tone_ui()

    def _on_volume(self):
        v = int(self.var_vol.get())
        cfg(self.lbl_vol, text=f"{v}%")
        if self._vol_job:
            self.p.after_cancel(self._vol_job)
        self._vol_job = self.p.after(250, lambda: self.a.set(volume=int(self.var_vol.get())))

    def _preview(self):
        if self.ringer.ringing:
            self.ringer.stop()
        elif self.p.ring_busy():
            toast.warn(tr("闹钟正在响."))
        else:
            self.a.set(volume=int(self.var_vol.get()))
            self.ringer.start(self.a.cfg, alarm_mod.PREVIEW_SECONDS)
        self.refresh()

    def _sync_tone_ui(self):
        a = self.a
        cfg_tone = a.cfg["tone"]
        self.var_tone.set(next((lab for k, lab in self._tones if k == cfg_tone), ""))
        if cfg_tone == "custom":
            if self.row_custom.winfo_manager() != "pack":
                self.row_custom.pack(fill="x", padx=12, pady=(0, 4), before=self.lbl_tone_hint)
            path = a.cfg.get("custom") or ""
            cfg(self.lbl_file, text=os.path.basename(path) if path else tr("还没有选文件"))
        elif self.row_custom.winfo_manager() == "pack":
            self.row_custom.pack_forget()

    # ---- 每秒 / 回到这一页时 ----
    def refresh(self):
        if self.a is None:
            return
        now = datetime.now()
        co_fn = self.p.co_alarm_fn()
        if co_fn is not None:
            name = self.p.comm.co_alarm_name()
            note = tr("共时闹钟已开启: 本机按「{name}」的起床时间响铃 (在 沟通 → ⏰ 里设置), 上面的开关和时间现在不生效; 铃声和音量照这里的设置.", name=name)
            if self.lbl_co.winfo_manager() != "pack":
                self.lbl_co.pack(fill="x", padx=12, pady=(0, 2), before=self.lbl_next)
            cfg(self.lbl_co, text=note)
            text = self._status(now, co_fn, name)
        else:
            if self.lbl_co.winfo_manager() == "pack":
                self.lbl_co.pack_forget()
            text = self._status(now, None, "")
        cfg(self.lbl_next, text=text)
        cfg(self.lbl_vol, text=f"{int(self.var_vol.get())}%")
        cfg(self.btn_preview, text=tr("■ 停止") if self.ringer.ringing else tr("▶ 试听"))
        a = self.a
        if bool(a.cfg["on"]) != self.var_on.get():
            self.var_on.set(bool(a.cfg["on"]))

    def _status(self, now, co_fn, name) -> str:
        if co_fn is not None:
            t = co_fn(now)
            if t is None:
                return tr("这个对象的日程里没有起床时间, 共时闹钟不会响.")
            return tr("下一次: {day} {t}", day=tr(alarm_mod.day_word(t, now)), t=f"{t:%H:%M}")
        t = self.a.next_ring(now)
        if t is None:
            return tr("闹钟已关闭")
        return tr("下一次: {day} {t}", day=tr(alarm_mod.day_word(t, now)), t=f"{t:%H:%M}")


class RingWindow(tk.Toplevel):
    """响铃弹窗: 置顶, 盖在所有页面 (包括睡眠的遮罩) 之上, 点"关闭闹钟"才停."""

    def __init__(self, root, at: datetime, co_name: str, on_close):
        super().__init__(root, bg=COLORS["bg"])
        self._on_close = on_close
        self._closed = False
        self.title("⏰ " + tr("闹钟"))
        self.resizable(False, False)
        self.attributes("-topmost", True)
        w, h = config.scaled(380), config.scaled(240)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        bg = COLORS["bg"]
        tk.Label(self, text="⏰", bg=bg, fg=COLORS["accent"], font=config.font("h1", bold=True)).pack(pady=(18, 0))
        tk.Label(self, text=f"{at:%H:%M}", bg=bg, fg=COLORS["text"], font=config.font("h1", bold=True)).pack()
        sub = tr("{name} 起床了 · 共时闹钟", name=co_name) if co_name else tr("闹钟")
        tk.Label(self, text=sub, bg=bg, fg=COLORS["text_dim"], font=config.font("body")).pack(pady=(2, 14))
        ttk.Button(self, text=tr("关闭闹钟"), style="Accent.TButton", command=self.close).pack(ipadx=18, ipady=4)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Return>", lambda _e: self.close())
        self.bind("<Escape>", lambda _e: self.close())
        self.after(1500, self._keep_top)
        # 别的窗口正占着输入 (弹窗的 grab, 例如睡眠的遮罩): 闹钟弹窗先抢过来, 关掉时还给它
        self._prev_grab = None
        try:
            self._prev_grab = root.grab_current()
        except tk.TclError:
            pass
        try:
            self.update_idletasks()
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    def _keep_top(self):
        if self._closed:
            return
        try:
            self.attributes("-topmost", True)
            self.lift()
            self.after(2000, self._keep_top)
        except tk.TclError:
            pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._on_close()
        finally:
            try:
                self.grab_release()
                self.destroy()
            except tk.TclError:
                pass
            prev = self._prev_grab
            try:
                if prev is not None and prev.winfo_exists():
                    prev.grab_set()
            except tk.TclError:
                pass


def open_co_alarm(parent, comm):
    """沟通页 ⏰: 把现在聊的对象设为共时闹钟 (一次只有一个), 手动选哪些设备响铃. 改了就生效, 随设备同步."""
    pid = comm.pid
    if not pid:
        toast.warn(tr("先选一个聊天对象."))
        return
    width = config.scaled(480)
    dlg = Modal(parent, tr("共时闹钟"), width=width, height=config.scaled(430))
    bg = COLORS["bg"]
    small = config.font("small")
    import comm as comm_mod
    name = comm_mod.persona_title(comm.persona_name())
    wrap = width - config.scaled(60)
    tk.Label(dlg.body, text=tr("开启后, 闹钟的时间跟随这个对象的日程里的实际起床时间 (睡眠中也会响). 响铃用「设置 → 闹钟」里的铃声和音量; 哪些设备响, 在下面手动选."),
             bg=bg, fg=COLORS["text_dim"], font=small, justify="left", anchor="w", wraplength=wrap).pack(fill="x")
    var_on = tk.BooleanVar(value=comm.co_alarm_pid() == pid)
    lbl_other = tk.Label(dlg.body, text="", bg=bg, fg=COLORS["warn"] if "warn" in COLORS else COLORS["accent"], font=small,
                         justify="left", anchor="w", wraplength=wrap)
    dev_box = tk.Frame(dlg.body, bg=bg)
    lbl_next = tk.Label(dlg.body, text="", bg=bg, fg=COLORS["accent"], font=config.font("body"), justify="left", anchor="w", wraplength=wrap)
    dev_vars = {}

    def sync_ui():
        on = comm.co_alarm_pid() == pid
        other = comm.co_alarm_pid() and not on
        cfg(lbl_other, text=tr("现在的共时闹钟是「{name}」, 打开会换成这个对象.", name=comm.co_alarm_name()) if other else "")
        if on:
            if dev_box.winfo_manager() != "pack":
                dev_box.pack(fill="x", pady=(8, 0), before=lbl_next)
        elif dev_box.winfo_manager() == "pack":
            dev_box.pack_forget()
        if on:
            t = comm.wake_after(pid, datetime.now())
            if t is None:
                text = tr("这个对象的日程里没有起床时间 (没有日程文件, 也没有睡眠段), 闹钟不会响.")
            elif not comm.co_alarm_devs():
                text = tr("没有选响铃的设备: 不会响.")
            else:
                import alarm as am
                text = tr("下一次起床: {day} {t}", day=tr(am.day_word(t, datetime.now())), t=f"{t:%H:%M}")
        else:
            text = ""
        cfg(lbl_next, text=text)

    def toggle():
        comm.set_co_alarm(pid if var_on.get() else "")
        chosen_now = set(comm.co_alarm_devs())              # 刚打开时沿用原来选的设备 (没有就是本机)
        for d, v in dev_vars.items():
            v.set(d in chosen_now)
        sync_ui()

    def devs_changed():
        comm.set_co_alarm_devs([d for d, v in dev_vars.items() if v.get()])
        sync_ui()

    tk.Checkbutton(dlg.body, text=tr("把「{name}」设为共时闹钟", name=name), variable=var_on, command=toggle, bg=bg,
                   fg=COLORS["text"], selectcolor=bg, activebackground=bg, activeforeground=COLORS["text"],
                   font=config.font("body", bold=True), anchor="w").pack(fill="x", pady=(10, 0))
    lbl_other.pack(fill="x")
    tk.Label(dev_box, text=tr("响铃的设备"), bg=bg, fg=COLORS["text"], font=config.font("body", bold=True), anchor="w").pack(fill="x")
    mine = comm.device()
    chosen = set(comm.co_alarm_devs())
    devices = list(comm.devices() or [])
    if not devices and mine:
        devices = [(mine, tr("本机"))]
    for did, dname in devices:
        v = tk.BooleanVar(value=did in chosen)
        dev_vars[did] = v
        label = dname + (" (" + tr("本机") + ")" if did == mine else "")
        tk.Checkbutton(dev_box, text=label, variable=v, command=devs_changed, bg=bg, fg=COLORS["text"], selectcolor=bg,
                       activebackground=bg, activeforeground=COLORS["text"], font=config.font("body"), anchor="w").pack(fill="x")
    lbl_next.pack(fill="x", pady=(10, 0))
    sync_ui()
    ttk.Button(dlg.buttons, text=tr("关闭"), style="Accent.TButton", command=lambda: dlg.finish(True)).pack(side="right")
    dlg.update_idletasks()
    dlg._center(parent, width, dlg.winfo_reqheight())
    dlg.wait()
