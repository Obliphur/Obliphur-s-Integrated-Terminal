"""指令 (Prescript) 页: 都市之意主题独占, 放在左侧导航第一位.

整页盖在窗口上 (遮住侧边栏), 样子参照 https://fuming-397.github.io/command-receiver/ :
黑底, 居中一张图标、蓝色代码、白色正文和 "- 接收指令 -". 不显示任何时间 / 限时.
左上角一个几乎看不见的退出按钮, 或者按 ESC.

判定 / 计时 / 存档都在 prescript.py; 这一页只负责画.
"""

import ctypes
import os
import random
import sys
import time
from datetime import datetime
import tkinter as tk
import tkinter.font as tkfont

import config
from config import COLORS
from i18n import tr
from prescript import IDLE_ENTRIES, pad_code, type_code

IDLE_TEXT = "谨遵指令之意"
SOUND_ALIAS = "oit_prescript"
SCRAMBLE_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()_+-=[]{}|;:,.?"
GLOW = "#2C6670"          # 蓝色代码的光晕
BTN_HOVER = "#FF0000"
BTN_DISABLED = "#444444"
EXIT_DIM = "#2A3535"


def _mix(c1: str, c2: str, k: float) -> str:
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * k):02X}" for x, y in zip(a, b))


class GlowText(tk.Canvas):
    """一行带光晕的字 (Tk 没有 text-shadow, 用几份暗色的字错开一像素叠出来)."""

    PAD = 4
    OFFSETS = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1))

    def __init__(self, master, font, fg, glow, bg):
        super().__init__(master, bg=bg, highlightthickness=0, bd=0)
        self._font = tkfont.Font(font=font)
        self._fg, self._glow = fg, glow
        self.set_text("", "")

    def set_text(self, text: str, reserve: str = None):
        reserve = text if reserve is None else reserve
        width = max(self._font.measure(text), self._font.measure(reserve)) + 2 * self.PAD
        height = self._font.metrics("linespace") + 2 * self.PAD
        self.configure(width=width, height=height)
        self.delete("all")
        cx, cy = width / 2, height / 2
        for dx, dy in self.OFFSETS:
            self.create_text(cx + dx, cy + dy, text=text, font=self._font, fill=self._glow)
        self.create_text(cx, cy, text=text, font=self._font, fill=self._fg)


class LogEditor(tk.Frame):
    """X 词条的输入框: 整页盖在指令页上, 样子像一个普通的 txt (黑底白字, 蓝色光标); 保存 / 关闭由页面处理."""

    def __init__(self, master, text, on_save, on_close):
        super().__init__(master, bg="#000000")
        self._on_save, self._on_close = on_save, on_close
        box = tk.Frame(self, bg="#000000")
        box.place(relx=0.5, rely=0.5, relwidth=0.7, relheight=0.78, anchor="center")
        bar = tk.Frame(box, bg="#000000")
        bar.pack(side="bottom", fill="x", pady=(config.scaled(8), 0))
        body = tk.Frame(box, bg=EXIT_DIM, padx=1, pady=1)
        body.pack(side="top", fill="both", expand=True)
        self.text = tk.Text(
            body, bg="#050505", fg=COLORS.get("text_strong", "#FFFFFF"),
            insertbackground=COLORS.get("accent", "#A5F3FF"), selectbackground=GLOW,
            relief="flat", bd=0, highlightthickness=0, wrap="word", undo=True,
            font=config.font("body"), padx=config.scaled(12), pady=config.scaled(10))
        self.text.pack(side="left", fill="both", expand=True)
        self.text.insert("1.0", text)
        self.btn_save = tk.Label(bar, text=tr("- 保存 -"), bg="#000000", fg="#FFFFFF",
                                 font=config.font("btn"), cursor="hand2")
        self.btn_save.pack(side="right")
        self.btn_save.bind("<Button-1>", lambda e: self.save())
        self.btn_save.bind("<Enter>", lambda e: self.btn_save.configure(fg=BTN_HOVER))
        self.btn_save.bind("<Leave>", lambda e: self.btn_save.configure(fg="#FFFFFF"))
        self.btn_close = tk.Label(self, text="×", bg="#000000", fg=EXIT_DIM, font=config.font("body"),
                                  cursor="hand2", padx=8, pady=2)
        self.btn_close.place(x=4, y=2)
        self.btn_close.bind("<Button-1>", lambda e: self._on_close())
        self.btn_close.bind("<Enter>", lambda e: self.btn_close.configure(fg=COLORS["text_dim"]))
        self.btn_close.bind("<Leave>", lambda e: self.btn_close.configure(fg=EXIT_DIM))
        self.text.bind("<Control-s>", lambda e: (self.save(), "break")[1])
        self.text.bind("<Escape>", lambda e: (self._on_close(), "break")[1])

    def content(self) -> str:
        return self.text.get("1.0", "end-1c")

    def save(self):
        self._on_save(self.content())

    def focus_text(self):
        try:
            self.text.focus_set()
            self.text.mark_set("insert", "1.0")
        except tk.TclError:
            pass


class PrescriptPage(tk.Frame):
    """黑底居中: 图标 / 蓝色代码 / 正文 / "- 接收指令 -"; 左上角一个不显眼的退出."""

    def __init__(self, master, engine, on_exit=None):
        super().__init__(master, bg="#000000")
        self.engine = engine
        self.on_exit = on_exit
        self._active = False          # 页面正在显示: 文字变化才播动画
        self._visit_since = ""        # 这一次打开页面的时刻: 只显示这之后给出的代码
        self._tick_job = None
        self._body = None             # 正文当前的目标文字
        self._codes = None            # 蓝色代码当前的目标文字 (list)
        self._body_gen = 0
        self._prev_type = ""          # 上一次刷新时进行中指令的类型 (V 结束要立刻复位)
        self._scramble_ms = 0         # 这一次蓝色代码变动要多久 (0 = 默认); 领取时等于音效的长度
        self._sound_job = None
        self._sound_len = 0
        self._code_gen = 0
        self._body_busy = False
        self._code_busy = False
        self._body_job = None
        self._code_job = None
        self._code_lines = []
        self._btn_color = "#FFFFFF"
        self._btn_job = None
        self._btn_hover = False
        self._image = None
        self._editor = None           # X 词条的输入框 (打开时盖在页面上)
        self._editor_for = ""         # 已经为哪一条指令打开过输入框 (每条只自动打开一次)
        self._build()
        top = self.winfo_toplevel()
        self._esc_id = top.bind("<Escape>", self._on_escape, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")

    # ---- 界面 ----

    def _build(self):
        bg = "#000000"
        self.main = tk.Frame(self, bg=bg)
        self.main.place(relx=0.5, rely=0.5, anchor="center")

        self._load_image()
        if self._image is not None:
            tk.Label(self.main, image=self._image, bg=bg, bd=0).pack(pady=(0, config.scaled(10)))

        self.code_box = tk.Frame(self.main, bg=bg)
        self.code_box.pack()

        self.lbl_body = tk.Label(
            self.main, text="", bg=bg, fg=COLORS.get("text_strong", "#FFFFFF"),
            font=config.font("body"), justify="center", anchor="center",
            wraplength=config.scaled(420)
        )
        self.lbl_body.pack(pady=(config.scaled(4), config.scaled(18)))

        self.btn = tk.Label(
            self.main, text=tr("- 接收指令 -"), bg=bg, fg=self._btn_color,
            font=config.font("btn"), cursor="hand2"
        )
        self.btn.pack()
        self.btn.bind("<Button-1>", self._on_click)
        self.btn.bind("<Enter>", lambda e: self._hover(True))
        self.btn.bind("<Leave>", lambda e: self._hover(False))

        self.btn_exit = tk.Label(
            self, text="×", bg=bg, fg=EXIT_DIM, font=config.font("body"),
            cursor="hand2", padx=8, pady=2
        )
        self.btn_exit.place(x=4, y=2)
        self.btn_exit.bind("<Button-1>", lambda e: self._exit())
        self.btn_exit.bind("<Enter>", lambda e: self.btn_exit.configure(fg=COLORS["text_dim"]))
        self.btn_exit.bind("<Leave>", lambda e: self.btn_exit.configure(fg=EXIT_DIM))

        # 正文的换行宽度跟着窗口走 (网页是窗口宽度的一半, 至少 320).
        self.bind("<Configure>", self._on_resize, add="+")

    def _load_image(self):
        path = config.asset_path("themes", "urban_will", "image", "Index.png")
        if not os.path.isfile(path):
            return
        try:
            img = tk.PhotoImage(file=path)
            target = config.scaled(128)
            if img.width() > target:
                img = img.subsample(max(1, round(img.width() / target)))
            elif img.width() < target:
                img = img.zoom(max(1, round(target / img.width())))
            self._image = img
        except Exception:
            self._image = None

    def _on_resize(self, event):
        if event.widget is not self:
            return
        width = max(config.scaled(320), int(event.width * 0.5))
        if int(self.lbl_body.cget("wraplength")) != width:
            self.lbl_body.configure(wraplength=width)

    # ---- 显示 / 退出 ----

    def show(self):
        """盖到窗口上之后调用."""
        self._active = False
        self._visit_since = datetime.now().replace(microsecond=0).isoformat(timespec="seconds")
        self.refresh()                      # 先按当前状态直接画出来, 不播动画
        self._active = True
        self.engine.settle_rewards()        # 新给出的蓝色代码会通过 on_change 播动画
        self.refresh()
        self._start_tick()
        try:
            self.focus_set()
        except tk.TclError:
            pass

    def hide(self):
        self._active = False
        self._stop_tick()
        self._drop_editor()

    def _exit(self):
        if self.on_exit is not None:
            self.on_exit()

    def _on_escape(self, event=None):
        try:
            if self._editor is not None:
                self._close_editor()
            elif self.winfo_ismapped():
                self._exit()
        except tk.TclError:
            pass

    # ---- 刷新 ----

    def refresh(self, force=False):
        # force: 刚领取时用 -- 抽到和上一条一样的指令 (文字没变) 也要重新播动画.
        # 永远只显示最新的一条指令 (开发者模式下同时有几条, 旧的在后台照常判定, 不显示).
        cur = self.engine.current
        body = self._body_text(cur) if cur else tr(IDLE_TEXT)
        # 有进行中的指令: 蓝色代码是这条指令的词; 没有就看这次打开之后结算出的奖励, 再没有就是 _%NONE%.
        if cur:
            codes = [pad_code(type_code(cur.get("type")))]
        else:
            codes = [pad_code(str(g.get("code") or "")) for g in self._fresh_grants()]
            codes = codes or [pad_code("NONE")]
        # V 结束 (完成 / 作废) 之后立刻回到最初等待领取的样子, 不播动画
        instant = self._prev_type in IDLE_ENTRIES and not cur
        self._prev_type = str(cur.get("type") or "") if cur else ""
        if force or body != self._body:
            self._body = body
            self._set_body(body, instant)
        if force or codes != self._codes:
            self._codes = codes
            self._set_codes(codes, instant)
        self._sync_button()
        self._sync_editor(cur)

    # ---- X: 记录输入框 ----

    def _sync_editor(self, cur):
        """X 词条领到之后 (正文打完了) 自动打开一次输入框; 这条指令没了输入框也跟着收起来."""
        if cur and cur.get("type") == "X":
            if self._editor is None and self._editor_for != cur.get("id") and not self._body_busy and self._active:
                self._editor_for = str(cur.get("id") or "")
                self._open_editor()
        elif self._editor is not None:
            self._drop_editor()

    def _open_editor(self):
        text = self.engine.log_prefill()
        self._editor = LogEditor(self, text, self._save_editor, self._close_editor)
        self._editor.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._editor.lift()
        self._editor.focus_text()

    def _drop_editor(self):
        editor, self._editor = self._editor, None
        if editor is not None:
            try:
                editor.destroy()
            except tk.TclError:
                pass

    def _save_editor(self, text):
        """保存: 一点没改 = 指令作废, 改过 = 完成."""
        self.engine.submit_log(text)
        self._drop_editor()
        self.refresh()
        self.focus_set()

    def _close_editor(self):
        """没保存就关掉: 等于没改, 指令作废."""
        self.engine.abandon_log()
        self._drop_editor()
        self.refresh()
        self.focus_set()

    @staticmethod
    def _body_text(cur) -> str:
        """指令正文; 带数字的 (II / III / IV) 按模板翻译, 英文界面也能显示."""
        n = cur.get("n")
        templates = {"II": "完成一次至少{n}级的合约.", "III": "休息{n}min.", "IV": "今天专注至少{n} h"}
        tpl = templates.get(cur.get("type"))
        if tpl and n is not None:
            return tr(tpl, n=int(n))
        return tr(cur.get("body", ""))

    def _fresh_grants(self):
        """只显示这一次打开页面之后给出的代码, 而且不到 3 分钟的不写出来 (再次打开显示 _%NONE%.)."""
        out = []
        for g in self.engine.granted_today():
            try:
                seconds = int(g.get("seconds") or 0)
            except (TypeError, ValueError):
                seconds = 0
            if str(g.get("at") or "") >= self._visit_since and seconds >= 180:
                out.append(g)
        return out

    def _set_body(self, text, instant=False):
        self._body_gen += 1
        gen = self._body_gen
        self._cancel("_body_job")
        if not self._active or instant or not text:
            self._body_busy = False
            self.lbl_body.configure(text=text)
            return
        self._body_busy = True
        self.lbl_body.configure(text="")
        self._type_body(text, 1, gen)

    def _type_body(self, text, n, gen):
        """逐字打出来, 每字 50 ms."""
        if gen != self._body_gen:
            return
        self.lbl_body.configure(text=text[:n])
        if n >= len(text):
            self._body_busy = False
            self._body_job = None
            return
        self._body_job = self.after(50, lambda: self._type_body(text, n + 1, gen))

    def _ensure_lines(self, count):
        color = COLORS.get("accent", "#A5F3FF")
        while len(self._code_lines) < count:
            line = GlowText(self.code_box, config.font("h2", bold=True), color, GLOW, "#000000")
            line.pack()
            self._code_lines.append(line)
        while len(self._code_lines) > count:
            self._code_lines.pop().destroy()

    def _set_codes(self, codes, instant=False):
        self._code_gen += 1
        gen = self._code_gen
        self._cancel("_code_job")
        self._ensure_lines(len(codes))
        if not self._active or instant:
            self._code_busy = False
            for line, text in zip(self._code_lines, codes):
                line.set_text(text)
            return
        self._code_busy = True
        longest = max(len(c) for c in codes)
        total = (self._scramble_ms or 100 * longest) / 1000.0
        self._scramble(codes, time.monotonic(), total, gen)

    def _scramble(self, codes, start, total, gen):
        """乱码每 25 ms 刷新一次, 在 total 秒里从左到右一个个定下来 (领取时 total 就是音效的长度)."""
        if gen != self._code_gen:
            return
        longest = max(len(c) for c in codes)
        elapsed = time.monotonic() - start
        if elapsed >= total:
            for line, text in zip(self._code_lines, codes):
                line.set_text(text)
            self._code_busy = False
            self._code_job = None
            return
        done = int(elapsed / total * longest) - 1
        for line, text in zip(self._code_lines, codes):
            shown = "".join(
                ch if i <= done else random.choice(SCRAMBLE_CHARS)
                for i, ch in enumerate(text)
            )
            line.set_text(shown, text)
        self._code_job = self.after(25, lambda: self._scramble(codes, start, total, gen))

    # ---- 按钮 ----

    def _sync_button(self):
        can = self.engine.can_draw()
        self.btn.configure(cursor="hand2" if can else "arrow")
        if not can:
            self._set_btn_color(BTN_DISABLED, instant=True)
        else:
            self._set_btn_color(BTN_HOVER if self._btn_hover else "#FFFFFF")

    def _hover(self, on):
        self._btn_hover = on
        if self.engine.can_draw():
            self._set_btn_color(BTN_HOVER if on else "#FFFFFF")

    def _set_btn_color(self, target, instant=False):
        """颜色在 0.5 s 里渐变 (网页上的 transition)."""
        if self._btn_job is not None:
            self.after_cancel(self._btn_job)
            self._btn_job = None
        if instant:
            self._btn_color = target
            self.btn.configure(fg=target)
            return
        start = self._btn_color
        if start == target:
            return

        def step(i):
            self._btn_job = None
            k = i / 10
            self._btn_color = _mix(start, target, k)
            self.btn.configure(fg=self._btn_color)
            if i < 10:
                self._btn_job = self.after(50, lambda: step(i + 1))

        step(1)

    def _on_click(self, event=None):
        if self._body_busy or self._code_busy or not self.engine.can_draw():
            return
        # 先把音效打开、量出长度: 蓝色代码变动的时长和音效一样长.
        length_ms = self._open_sound()
        self._scramble_ms = length_ms
        try:
            drawn = self.engine.draw() is not None
            if drawn:
                self._play_sound()
            else:
                self._close_sound()
            self.refresh(force=drawn)
        finally:
            self._scramble_ms = 0

    def _mci(self, command, want_text=False):
        buf = ctypes.create_unicode_buffer(64)
        code = ctypes.windll.winmm.mciSendStringW(command, buf, 63, None)
        return (code, buf.value) if want_text else code

    def _open_sound(self) -> int:
        """接收指令的 "滴答" 音效 (三个里随机一个): 打开并返回长度 (毫秒); 文件不在或打不开返回 0."""
        self._sound_open = False
        if sys.platform != "win32":
            return 0
        name = f"ticktock_type{random.randint(1, 3)}.mp3"
        path = config.asset_path("themes", "urban_will", "audio", name)
        if not os.path.isfile(path):
            return 0
        try:
            self._mci(f"close {SOUND_ALIAS}")
            if self._mci(f'open "{path}" type mpegvideo alias {SOUND_ALIAS}') != 0:
                return 0
            self._mci(f"set {SOUND_ALIAS} time format milliseconds")
            code, text = self._mci(f"status {SOUND_ALIAS} length", want_text=True)
            self._sound_open = True
            self._sound_len = int(text) if code == 0 and text.isdigit() else 0
            return self._sound_len
        except Exception:
            return 0

    def _play_sound(self):
        if getattr(self, "_sound_open", False):
            try:
                self._mci(f"play {SOUND_ALIAS} from 0")
            except Exception:
                pass
            # 放完就关掉: 音效文件一直开着的话, 更新程序覆盖它会失败 (WinError 32)
            self._cancel("_sound_job")
            self._sound_job = self.after(getattr(self, "_sound_len", 0) + 500, self._close_sound)

    def _close_sound(self):
        self._sound_job = None
        if getattr(self, "_sound_open", False):
            try:
                self._mci(f"close {SOUND_ALIAS}")
            except Exception:
                pass
            self._sound_open = False

    # ---- 每秒检查 (整点一过按钮就恢复) ----

    def _start_tick(self):
        if self._tick_job is None:
            self._tick_job = self.after(1000, self._tick)

    def _stop_tick(self):
        if self._tick_job is not None:
            try:
                self.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None

    def _tick(self):
        self._tick_job = None
        if not self.winfo_exists() or not self._active:
            return
        try:
            self.refresh()
        except Exception:
            pass
        self._tick_job = self.after(1000, self._tick)

    # ---- 清理 ----

    def _cancel(self, name):
        job = getattr(self, name)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
            setattr(self, name, None)

    def _on_destroy(self, event):
        if event.widget is not self:
            return
        self._active = False
        self._body_gen += 1
        self._code_gen += 1
        for name in ("_tick_job", "_body_job", "_code_job", "_btn_job", "_sound_job"):
            self._cancel(name)
        self._close_sound()
        try:
            self.winfo_toplevel().unbind("<Escape>", self._esc_id)
        except Exception:
            pass
