"""设置页的沟通模块: API / 人设 / 头像 / 主动发起 / 日程跟随 (即时保存)。

从 pages/settings_page.py 拆出来的 mixin; 依赖方向: settings_page.py 导入它们, 它们从不导入 settings_page.py.
"""

import os
import tkinter as tk
from tkinter import filedialog
from tkinter import ttk
import config
import toast
from config import COLORS
from i18n import tr

from ..widgets import (cfg, set_var, focus_name, show_pack, hide_pack)


class CommSettingsMixin:
    def _build_models_page(self, body):
        if self.comm is None:
            return
        fr, _head = self._card(body, "新增/修改 AI 配置")
        ttk.Button(fr, text=tr("设置"), command=self._comm_api).pack(anchor="w", padx=12, pady=(2, 10))

        fr, _head = self._card(body, "聊天模型（主模型）")
        self.lbl_main_model = tk.Label(fr, text="", bg=COLORS["bg_card"], fg=COLORS["text"],
                                       font=config.font("small"), anchor="w")
        self.lbl_main_model.pack(fill="x", padx=12, pady=(2, 4))
        self.btn_main_model = ttk.Button(
            fr, text=tr("选择"), command=lambda: self._pick_task_model("main", "聊天模型（主模型）"))
        self.btn_main_model.pack(anchor="w", padx=12, pady=(0, 10))

        for role, title in (("ocr", "识字模型"), ("review", "评判模型"),
                            ("subject", "数学/物理/化学模型")):
            fr, _head = self._card(body, title)
            label = tk.Label(fr, text="", bg=COLORS["bg_card"], fg=COLORS["text"],
                             font=config.font("small"), anchor="w")
            label.pack(fill="x", padx=12, pady=(2, 4))
            setattr(self, f"lbl_{role}_model", label)
            ttk.Button(fr, text=tr("选择"), command=lambda r=role, t=title: self._pick_task_model(r, t))\
                .pack(anchor="w", padx=12, pady=(0, 10))

    def _pick_task_model(self, role, title):
        from .. import ai_dialogs
        ai_dialogs.open_task_model_settings(self.winfo_toplevel(), self.comm.ai, role, title)
        self._refresh_models_page()

    def _refresh_models_page(self):
        if self.comm is None or not hasattr(self, "lbl_main_model"):
            return
        model_cfg = self.comm.ai
        main_id = model_cfg.config_for("")
        if host := model_cfg.takeover():
            main = tr("由 {host} 桌面版接管", host=host)
            self.btn_main_model.state(["disabled"])
        else:
            main = f"{model_cfg.config_name(main_id)} · {model_cfg.profile(main_id).get('model') or '—'}"
            self.btn_main_model.state(["!disabled"])
        cfg(self.lbl_main_model, text=main)
        for role in ("ocr", "review", "subject"):
            cid = model_cfg.task_assign.get(role, "")
            if cid and cid not in {item["id"] for item in model_cfg.configs()}:
                cid = ""
            text = tr("跟随聊天模型") if not cid else \
                f"{model_cfg.config_name(cid)} · {model_cfg.profile(cid).get('model') or '—'}"
            cfg(getattr(self, f"lbl_{role}_model"), text=text)

    def _build_comm_page(self, body):
        c = self.comm
        self.comm_vars = {}
        if c is None:
            return
        card = COLORS["bg_card"]
        small = config.font("small")

        fr, head = self._card(body, "🎭  人设")
        self._track(ttk.Button(head, text=tr("打开文件夹"), command=self._comm_open_folder),
                    "打开文件夹").pack(side="right")
        self._track(ttk.Button(head, text=tr("导入"), command=self._comm_import),
                    "导入").pack(side="right", padx=(0, 6))
        self.comm_persona = tk.StringVar()
        self.cb_persona = ttk.Combobox(fr, textvariable=self.comm_persona, state="readonly", width=40)
        self.cb_persona.pack(fill="x", padx=12, pady=(2, 10))
        self.cb_persona.bind("<<ComboboxSelected>>", lambda _e: self._comm_pick_persona())
        actions = tk.Frame(fr, bg=card)
        actions.pack(fill="x", padx=12, pady=(0, 10))
        self.btn_persona_profile = ttk.Button(actions, text=tr("打开人设简介"), command=self._comm_open_persona_profile)
        self.btn_persona_profile.pack(side="left", padx=(0, 6))
        ttk.Button(actions, text=tr("创建人设"), command=self._comm_create_persona).pack(side="left")
        # 选着 Claudia 而它的内容和默认人设不一样时, 显示"还原"
        self.row_restore = tk.Frame(fr, bg=card)
        tk.Label(self.row_restore, text=tr("Claudia 的内容和默认人设不一样"), bg=card, fg=COLORS["text_dim"], font=small,
                 anchor="w").pack(side="left", padx=(12, 8))
        ttk.Button(self.row_restore, text=tr("还原"), command=self._comm_restore_claudia).pack(side="left")

        fr, _head = self._card(body, "🧑  我的身份背景")
        self.txt_profile = tk.Text(fr, height=6, wrap="word", font=config.font("body"), bg=card, fg=COLORS["text"],
                                   insertbackground=COLORS["text"], relief="flat", highlightthickness=1,
                                   highlightbackground=COLORS["border"], highlightcolor=COLORS["accent"])
        self.txt_profile.pack(fill="x", padx=12, pady=(2, 4))
        self.txt_profile.insert("1.0", str(c.cfg.get("user_profile") or ""))
        self._profile_job = None
        self.txt_profile.bind("<KeyRelease>", lambda _e: self._comm_profile_later())
        self.txt_profile.bind("<FocusOut>", lambda _e: self._comm_profile_save())
        tk.Label(fr, text=tr("只在这段对话的第一轮发送, 改动在下一段对话生效."), bg=card, fg=COLORS["text_dim"],
                 font=small, anchor="w").pack(fill="x", padx=12, pady=(0, 8))

        fr, _head = self._card(body, "💬  主动发起")
        v = tk.BooleanVar(value=c.bg_on())
        self.comm_vars["bg_pro"] = v
        self._track(tk.Checkbutton(fr, text=tr("允许非正在聊天的人发起对话"), variable=v, bg=card, fg=COLORS["text"],
                                   selectcolor=card, activebackground=card, activeforeground=COLORS["text"],
                                   font=config.font("body"), anchor="w",
                                   command=lambda: self._comm_flag("bg_pro")), "允许非正在聊天的人发起对话").pack(
            fill="x", padx=12, pady=(0, 8))

        fr, _head = self._card(body, "🌐  主动谈话语言")
        tk.Label(fr, text=tr("对方主动发起谈话时使用的语言 (只管主动发起的那一句, 其他对话不限制)."), bg=card, fg=COLORS["text_dim"],
                 font=small, anchor="w", justify="left", wraplength=560).pack(fill="x", padx=12, pady=(0, 4))
        import comm as comm_mod
        self.var_pro_lang = tk.StringVar(value=c.pro_lang() or tr("不指定"))
        self.cb_pro_lang = ttk.Combobox(fr, textvariable=self.var_pro_lang, width=24,
                                        values=[tr("不指定")] + list(comm_mod.PRO_LANGS))      # 可以直接输入别的语言
        self.cb_pro_lang.pack(anchor="w", padx=12, pady=(0, 10))
        self.cb_pro_lang.bind("<<ComboboxSelected>>", lambda _e: self._comm_pro_lang_save())
        self.cb_pro_lang.bind("<Return>", lambda _e: self._comm_pro_lang_save())
        self.cb_pro_lang.bind("<FocusOut>", lambda _e: self._comm_pro_lang_save())

        fr, _head = self._card(body, "📅  日程跟随")
        tk.Label(fr, text=tr("人设的日程状态改变时 (包括睡眠), 询问是否跟随; 合约期间自动关闭, 结束后再开启."), bg=card, fg=COLORS["text_dim"],
                 font=small, anchor="w", justify="left", wraplength=560).pack(fill="x", padx=12, pady=(0, 2))
        v = tk.BooleanVar(value=c.sf_on())
        self.comm_vars["sched_follow"] = v
        self._track(tk.Checkbutton(fr, text=tr("开启"), variable=v, bg=card, fg=COLORS["text"],
                                   selectcolor=card, activebackground=card, activeforeground=COLORS["text"],
                                   font=config.font("body"), anchor="w",
                                   command=lambda: self._comm_flag("sched_follow")), "开启").pack(
            fill="x", padx=12, pady=(0, 8))

        fr, _head = self._card(body, "🖼  头像")
        v = tk.BooleanVar(value=bool(c.cfg.get("show_avatar")))
        self.comm_vars["show_avatar"] = v
        self._track(tk.Checkbutton(fr, text=tr("显示头像"), variable=v, bg=card, fg=COLORS["text"],
                                   selectcolor=card, activebackground=card, activeforeground=COLORS["text"],
                                   font=config.font("body"), anchor="w",
                                   command=lambda: self._comm_flag("show_avatar")), "显示头像").pack(
            fill="x", padx=12, pady=(0, 2))
        self.avatar_box = tk.Frame(fr, bg=card)
        self.avatar_prev = {}
        self._avatar_photos = []
        for who, text in (("me", "我"), ("ai", "对方")):
            row = tk.Frame(self.avatar_box, bg=card)
            row.pack(fill="x", padx=12, pady=3)
            self._track(tk.Label(row, text=tr(text), bg=card, fg=COLORS["text"], font=config.font("body"),
                                 width=6, anchor="w"), text).pack(side="left")
            prev = tk.Label(row, text="", bg=card, fg=COLORS["text_dim"], font=config.font("body"),
                            width=5, anchor="center", highlightthickness=1,
                            highlightbackground=COLORS["border"])
            prev.pack(side="left", padx=(0, 8))
            self.avatar_prev[who] = prev
            self._track(ttk.Button(row, text=tr("选择"), command=lambda w=who: self._comm_pick_avatar(w)),
                        "选择").pack(side="left")
            self._track(ttk.Button(row, text=tr("移除"), command=lambda w=who: self._comm_remove_avatar(w)),
                        "移除").pack(side="left", padx=(6, 0))
        self.avatar_foot = tk.Frame(fr, bg=card, height=8)
        self.avatar_foot.pack()
        self._comm_avatar_visible()
        self.areas["comm"].bind_children()

    def _comm_api(self):
        from .. import ai_dialogs
        ai_dialogs.open_model_settings(self.winfo_toplevel(), self.comm.ai)
        self._refresh_comm()
        self._refresh_models_page()

    def _comm_open_folder(self):
        folder = self.comm.personas
        os.makedirs(folder, exist_ok=True)
        self._open_uri(folder)

    def _comm_open_persona_profile(self):
        from comm.comm_persona import read_profile
        from .. import persona_dialogs
        persona_dialogs.open_profile(self.winfo_toplevel(), read_profile(self.comm.personas, self.comm.persona_name()))

    def _comm_create_persona(self):
        from .. import persona_dialogs
        name = persona_dialogs.create_persona(self.winfo_toplevel(), self.comm)
        if name:
            self.comm.set_cfg(persona=name)
            self._refresh_comm()

    def _comm_import(self):
        import comm as comm_mod
        path = filedialog.askopenfilename(
            title=tr("导入人设"), parent=self.winfo_toplevel(),
            filetypes=[(tr("人设文件"), "*.txt *.md"), (tr("所有文件"), "*.*")])
        if not path:
            return
        name = comm_mod.import_persona(self.comm.personas, path)
        if not name:
            toast.warn(tr("只能导入 .txt / .md 文件."))
            return
        self.comm.set_cfg(persona=name)
        self._refresh_comm()

    def _comm_profile_later(self):
        if self._profile_job:
            self.after_cancel(self._profile_job)
        self._profile_job = self.after(600, self._comm_profile_save)

    def _comm_profile_save(self):
        """身份背景: 随时保存 (即时生效, 没有等待期), 发给对方时附在人设后面."""
        self._profile_job = None
        import comm as comm_mod
        text = self.txt_profile.get("1.0", "end").strip()[:comm_mod.MAX_PROFILE]
        if text != str(self.comm.cfg.get("user_profile") or ""):
            self.comm.set_cfg(user_profile=text)

    def _comm_pro_lang_save(self):
        """主动谈话语言: 选了 / 输入了就保存 (「不指定」或空 = 不指定)."""
        import comm as comm_mod
        text = self.var_pro_lang.get().strip()
        if text == tr("不指定"):
            text = ""
        text = text[:comm_mod.MAX_PRO_LANG]
        if text != self.comm.pro_lang():
            self.comm.set_cfg(pro_lang=text)

    def _comm_pick_persona(self):
        label = self.comm_persona.get().strip()
        name = next((n for n, lab in getattr(self, "_persona_labels", []) if lab == label), "")
        if name:
            self.comm.set_cfg(persona=name)
            self._refresh_comm()

    def _comm_restore_claudia(self):
        self.comm.restore_claudia()
        self._refresh_comm()
        toast.info(tr("已还原默认人设"))

    def _comm_flag(self, key):
        self.comm.set_cfg(**{key: bool(self.comm_vars[key].get())})
        if key == "show_avatar":
            self._comm_avatar_visible()

    def _comm_pick_avatar(self, who):
        from ..avatar_dialog import pick_avatar
        if not self.comm.persona_name():                              # 我的头像和对方的头像都跟着人设走
            toast.warn(tr("还没有选人设"))
            return
        if pick_avatar(self.winfo_toplevel(), self.comm.avatar_dest(who)):
            self.comm.avatars_changed()
            self._refresh_avatars()

    def _comm_remove_avatar(self, who):
        self.comm.remove_avatar(who)
        self._refresh_avatars()

    def _refresh_avatars(self):
        import avatar
        self._avatar_photos = []
        for who, lb in self.avatar_prev.items():
            path = self.comm.avatar_path(who)
            photo = None
            if path:
                try:
                    photo = tk.PhotoImage(file=avatar.sized(path, config.scaled(40)))
                except (OSError, tk.TclError):
                    photo = None
            if photo is not None:
                self._avatar_photos.append(photo)
                lb.config(image=photo, text="", width=0)
            else:
                lb.config(image="", text="—", width=5)

    def _comm_avatar_visible(self):
        if self.comm.cfg.get("show_avatar"):
            show_pack(self.avatar_box, fill="x", before=self.avatar_foot)
        else:
            hide_pack(self.avatar_box)

    def _refresh_comm(self):
        c = self.comm
        if c is None or not hasattr(self, "cb_persona"):
            return
        import comm as comm_mod
        self._persona_labels = comm_mod.persona_labels(comm_mod.list_personas(c.personas))
        self.cb_persona.config(values=[lab for _n, lab in self._persona_labels])
        cur = c.persona_name()
        cfg(self.btn_persona_profile, state="normal" if cur else "disabled")
        if hasattr(self, "_voice_settings_refresh"):
            self._voice_settings_refresh()
        set_var(self.comm_persona, next((lab for n, lab in self._persona_labels if n == cur), ""))
        if c.claudia_differs():
            show_pack(self.row_restore, fill="x", pady=(0, 10))
        else:
            hide_pack(self.row_restore)
        if focus_name(self.txt_profile) != str(self.txt_profile) \
                and self.txt_profile.get("1.0", "end").strip() != str(c.cfg.get("user_profile") or ""):
            self.txt_profile.delete("1.0", "end")                       # 别的设备同步来了新的身份背景
            self.txt_profile.insert("1.0", str(c.cfg.get("user_profile") or ""))
        if self.comm_vars["bg_pro"].get() != c.bg_on():
            self.comm_vars["bg_pro"].set(c.bg_on())
        if self.comm_vars["sched_follow"].get() != c.sf_on():
            self.comm_vars["sched_follow"].set(c.sf_on())
        if focus_name(self.cb_pro_lang) not in (str(self.cb_pro_lang), "popdown"):          # 不在输入时: 别的设备同步来了新的语言
            want = c.pro_lang() or tr("不指定")
            if self.var_pro_lang.get() != want:
                self.var_pro_lang.set(want)
        if hasattr(self, "lbl_main_model"):
            self._refresh_models_page()
        self._refresh_avatars()

    def _comm_api_missing(self) -> bool:
        import ai_assist
        try:
            ai_assist.check_profile(self.comm.ai.profile())
        except ai_assist.AIError:
            return True
        return False

