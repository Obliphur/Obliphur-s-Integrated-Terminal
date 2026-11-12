"""AI 配置帮助和语音设置窗口。"""

import copy
import uuid
import webbrowser
import tkinter as tk
from tkinter import ttk

import config
import i18n
import ai_voice
import toast
from config import COLORS
from i18n import tr
from .widgets import Modal, ScrollArea
from .model_fetch import auto_models
from .voice_design import open_voice_design


MODEL_HELP = "1. 先注册一个提供 AI 服务的网站，充值后创建 API Key（相当于调用 AI 的密码）。\n2. 点「新建」，名称随便写，方便自己认出。用中转站就选「自定义」，Base URL 照网站的 API 文档复制。\n3. 按网站说明选择模型类型和接口协议；不确定时先用网站的 OpenAI 兼容协议。\n4. 点「获取模型列表」选择网站支持的模型，或复制网站给的模型名称。\n5. 把 API Key 粘贴进来并保存。聊天页选择这份配置即可；「设为默认」还会让规则页使用它。"
VOICE_HELP = "1. 打开 MiniMax 平台，注册并开通语音服务，创建 API Key。\n2. 点「新建」，模板选 MiniMax，选择一个语音模型。HD 音质更好，Turbo 更快；费用以平台为准。\n3. 从平台的音色列表复制音色 ID；这是 voice_id，不是文件编号 file_id。尚无音色时，点「音色合成」，用提示词描述声音，或上传录音进行克隆。自定义音色需要当前 API 账号拥有使用权限。\n4. 粘贴 MiniMax 的 API Key。国内账号用国内 API 地址，国际账号用国际地址，不要填写平台网页地址。\n5. 保存后回到「设置 → 沟通」，为当前聊天对象选择这份语音配置。开启语音后，新消息会立即生成语音，点消息框左下角贴边的播放图标。\nClaudia 默认配置只给 Claudia 使用，音色已填好；仍需填写有权使用该音色的 API Key。"


def open_help(parent, voice=False):
    dlg = Modal(parent, tr("帮助"), width=580, height=min(610, int(parent.winfo_screenheight() * .8)))
    area = ScrollArea(dlg.body)
    area.pack(fill="both", expand=True)
    label = tk.Label(area.inner, text=tr(VOICE_HELP if voice else MODEL_HELP), wraplength=510,
                     justify="left", anchor="w", bg=COLORS["bg"], fg=COLORS["text"], font=config.font("body"))
    label.pack(fill="x", pady=(0, 14))
    url = ("https://platform.minimax.io" if i18n.is_en() else "https://platform.minimax.cn") if voice else "https://chuanapi.com/sign-up?aff=V4Gc"
    ttk.Button(area.inner, text=url, command=lambda: webbrowser.open(url)).pack(anchor="w")
    ttk.Button(dlg.buttons, text=tr("关闭"), command=dlg.cancel).pack(side="right")
    area.bind_children()
    dlg.wait()
    if parent.winfo_exists():
        parent.grab_set()


def open_voice_settings(parent, comm):
    voice = comm.voice
    profiles = copy.deepcopy(voice.data["profiles"])
    dlg = Modal(parent, tr("AI 语音配置"), width=620, height=min(730, int(parent.winfo_screenheight() * .85)))
    top = tk.Frame(dlg.body, bg=COLORS["bg"])
    top.pack(fill="x")
    ttk.Button(top, text=tr("帮助"), command=lambda: open_help(dlg, True)).pack(side="right")
    area = ScrollArea(dlg.body)
    area.pack(fill="both", expand=True)
    b = area.inner
    pick = ttk.Combobox(b, state="readonly")
    pick.pack(fill="x", pady=8)
    vars_ = {}
    fields = (("name", "名称"), ("template", "模板"), ("model", "型号"),
              ("voice_id", "音色 ID"), ("url", "语音 API 地址"), ("api_key", "API Key"))
    widgets = {}
    for key, title in fields:
        tk.Label(b, text=tr(title), anchor="w", bg=COLORS["bg"], fg=COLORS["text"], font=config.font("body")).pack(fill="x")
        v = vars_[key] = tk.StringVar()
        if key in ("template", "model", "url"):
            w = ttk.Combobox(b, textvariable=v, state="readonly" if key == "template" else "normal",
                             values={"template": ["minimax"], "model": ai_voice.MODELS,
                                     "url": [ai_voice.CN_URL, ai_voice.GLOBAL_URL]}[key])
        else:
            w = ttk.Entry(b, textvariable=v, show="•" if key == "api_key" else "")
        w.pack(fill="x", pady=(2, 10))
        widgets[key] = w
        if key == "model":
            model_status = tk.StringVar()
            tk.Label(b, textvariable=model_status, anchor="w", wraplength=520,
                     bg=COLORS["bg"], fg=COLORS["text"], font=config.font("body")).pack(fill="x")
    current = [0]

    def snapshot():
        p = {k: v.get().strip() for k, v in vars_.items()}
        sig = (profiles[current[0]]["id"], p["url"], p["api_key"])
        return sig, p

    def got_models(models):
        widgets["model"].config(values=list(dict.fromkeys([vars_["model"].get()] + models)))

    def model_state(state):
        model_status.set(tr({"loading": "正在获取语音模型…", "done": "已获取 MiniMax 官方语音模型",
                             "failed": "未能获取语音模型列表，仍可选择预设型号或手动填写."}[state]))

    schedule_models = auto_models(dlg, snapshot, ai_voice.list_models, got_models, status=model_state)
    ttk.Button(b, text=tr("重新获取模型"), command=lambda: schedule_models(refresh=True)).pack(anchor="w", pady=4)
    for key in ("url", "api_key"):
        vars_[key].trace_add("write", schedule_models)

    def store():
        p = profiles[current[0]]
        p.update({k: v.get().strip() for k, v in vars_.items()})

    def load(index):
        current[0] = index
        p = profiles[index]
        widgets["model"].config(values=list(dict.fromkeys([p["model"]] + list(ai_voice.MODELS))))
        pick.config(values=[tr("Claudia 默认（专用）") if p["id"] == ai_voice.CLAUDIA else p["name"] for p in profiles])
        pick.current(index)
        for k, v in vars_.items():
            v.set(p.get(k, ""))
        delete.config(state="disabled" if p["id"] == ai_voice.CLAUDIA else "normal")
        schedule_models()

    def select(_e):
        idx = pick.current()
        store()
        load(idx)

    def add():
        store()
        profiles.append(ai_voice.profile(uuid.uuid4().hex, tr("新配置")))
        load(len(profiles) - 1)

    def remove():
        if profiles[current[0]]["id"] != ai_voice.CLAUDIA:
            profiles.pop(current[0])
            load(0)

    def save():
        store()
        if any(not p["name"] for p in profiles):
            toast.warn(tr("请填写配置名称."))
            return
        voice.save(profiles=profiles)
        comm._changed()
        comm._push()
        dlg.finish(True)

    ttk.Button(dlg.buttons, text=tr("新建"), command=add).pack(side="left")
    ttk.Button(b, text=tr("音色合成"), command=lambda: open_voice_design(
        dlg, {k: v.get().strip() for k, v in vars_.items()}, lambda vid: vars_["voice_id"].set(vid), voice.play_allowed)).pack(anchor="w", pady=4)
    delete = ttk.Button(dlg.buttons, text=tr("删除"), command=remove)
    delete.pack(side="left", padx=6)
    ttk.Button(dlg.buttons, text=tr("保存"), command=save).pack(side="right")
    ttk.Button(dlg.buttons, text=tr("取消"), command=dlg.cancel).pack(side="right", padx=6)
    pick.bind("<<ComboboxSelected>>", select)
    load(0)
    area.bind_children()
    return dlg.wait()


def build_voice_settings(page, body):
    comm = page.comm
    v = comm.voice
    fr, _ = page._card(body, "🔊  AI 语音")
    enabled = tk.BooleanVar(value=v.data["enabled"])
    details = tk.Frame(fr, bg=COLORS["bg_card"])
    auto = tk.BooleanVar(value=v.data["auto"])
    gap = tk.StringVar(value=str(v.data["gap"]))
    selected = tk.StringVar()
    options = []

    def refresh():
        nonlocal options
        enabled.set(v.data["enabled"])
        auto.set(v.data["auto"])
        options = [p for p in v.data["profiles"] if p["id"] != ai_voice.CLAUDIA or ai_voice.is_claudia(comm.persona_name())]
        picker.config(values=[tr("未选择")] + [tr("Claudia 默认（专用）") if p["id"] == ai_voice.CLAUDIA else p["name"] for p in options])
        cur = v.selected(comm.persona_name())
        picker.current(1 + next((i for i, p in enumerate(options) if cur and p["id"] == cur["id"]), -1))
        if enabled.get():
            details.pack(fill="x", padx=12, pady=(0, 10))
        else:
            details.pack_forget()

    def change():
        v.save(enabled=enabled.get(), auto=auto.get())
        comm._changed()
        comm._push()
        refresh()

    def select(_e):
        idx = picker.current() - 1
        assign = dict(v.data["assign"])
        assign[comm.persona_name()] = options[idx]["id"] if idx >= 0 else ""
        v.save(assign=assign)
        comm._changed()
        comm._push()

    def settings():
        open_voice_settings(page.winfo_toplevel(), comm)
        refresh()

    def save_gap(_e):
        try:
            n = float(gap.get())
            if not 0 <= n <= 60:
                raise ValueError()
        except ValueError:
            gap.set(str(v.data["gap"]))
            return
        v.save(gap=n)
        comm._push()

    def check(parent, label, variable):
        tk.Checkbutton(parent, text=tr(label), variable=variable, command=change,
                       bg=COLORS["bg_card"], fg=COLORS["text"], selectcolor=COLORS["bg_card"],
                       font=config.font("body")).pack(anchor="w", padx=12, pady=4)
    check(fr, "开启", enabled)
    ttk.Button(details, text=tr("AI 语音配置"), command=settings).pack(anchor="w", pady=4)
    picker = ttk.Combobox(details, textvariable=selected, state="readonly")
    picker.pack(fill="x", pady=4)
    picker.bind("<<ComboboxSelected>>", select)
    check(details, "自动播放", auto)
    tk.Label(details, text=tr("播放间隔（秒）"), bg=COLORS["bg_card"], fg=COLORS["text"], font=config.font("body")).pack(anchor="w")
    entry = ttk.Entry(details, textvariable=gap)
    entry.pack(fill="x", pady=4)
    entry.bind("<FocusOut>", save_gap)
    entry.bind("<Return>", save_gap)
    refresh()
    page._voice_settings_refresh = refresh
