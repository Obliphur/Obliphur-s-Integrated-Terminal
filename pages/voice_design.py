"""音色合成子页面：提示词设计或上传录音克隆，试听后应用到父配置。"""

import copy
import queue
import shutil
import threading
import tkinter as tk
from tkinter import ttk, filedialog

import ai_voice
import ai_voice_design
import config
import tempstore
from config import COLORS
from i18n import tr
from .widgets import Modal, ScrollArea


def open_voice_design(parent, profile, apply):
    dlg = Modal(parent, tr("音色合成"), width=620, height=min(740, int(parent.winfo_screenheight() * .85)))
    area = ScrollArea(dlg.body)
    area.pack(fill="both", expand=True)
    b = area.inner
    events = queue.Queue()
    state = {"busy": False, "closed": False, "audio": b"", "profile": None, "file": ""}
    preview_dir = tempstore.mkdtemp("misc", prefix="voice_preview_")
    preview = ai_voice.Voice(preview_dir)
    mode = tk.StringVar(value="prompt")
    status = tk.StringVar()
    result = tk.StringVar()
    file_id = tk.StringVar()
    requested = tk.StringVar()
    filename = tk.StringVar()

    def label(text, parent=b):
        w = tk.Label(parent, text=tr(text), anchor="w", justify="left", wraplength=520,
                     bg=COLORS["bg"], fg=COLORS["text"], font=config.font("body"))
        w.pack(fill="x", pady=(8, 2))
        return w

    modes = tk.Frame(b, bg=COLORS["bg"])
    modes.pack(fill="x")
    for title, value in (("提示词模式", "prompt"), ("克隆模式", "clone")):
        ttk.Radiobutton(modes, text=tr(title), value=value, variable=mode).pack(side="left", padx=4)
    prompt_box = tk.Frame(b, bg=COLORS["bg"])
    prompt_box.pack(fill="x")
    clone_box = tk.Frame(b, bg=COLORS["bg"])
    label("声音描述", prompt_box)
    prompt = tk.Text(prompt_box, height=4, font=config.font("body"), wrap="word")
    prompt.pack(fill="x")
    label("录音文件（10 秒至 5 分钟，最大 20 MB）", clone_box)

    def choose():
        path = filedialog.askopenfilename(parent=dlg, filetypes=[(tr("录音文件"), "*.mp3 *.m4a *.wav")])
        if path:
            state["file"] = path
            filename.set(path)
            file_id.set("")

    ttk.Button(clone_box, text=tr("选择录音"), command=choose).pack(anchor="w")
    tk.Label(clone_box, textvariable=filename, wraplength=520, bg=COLORS["bg"], fg=COLORS["text"]).pack(fill="x")
    label("已有文件 ID（file_id，可代替上传）", clone_box)
    ttk.Entry(clone_box, textvariable=file_id).pack(fill="x")
    common_start = label("新音色 ID（可留空）")
    def switch(*_args):
        prompt_box.pack_forget()
        clone_box.pack_forget()
        (prompt_box if mode.get() == "prompt" else clone_box).pack(fill="x", before=common_start)
    mode.trace_add("write", switch)
    ttk.Entry(b, textvariable=requested).pack(fill="x")
    label("试听文本（最多 500 字）")
    text = tk.Text(b, height=3, font=config.font("body"), wrap="word")
    text.insert("1.0", tr("你好，这是我的声音。"))
    text.pack(fill="x")
    label("生成的音色 ID（voice_id）")
    ttk.Entry(b, textvariable=result, state="readonly").pack(fill="x")
    tk.Label(b, textvariable=status, wraplength=520, justify="left", bg=COLORS["bg"],
             fg=COLORS["text"], font=config.font("body")).pack(fill="x", pady=8)

    def create():
        if state["busy"]:
            return
        p = copy.deepcopy(profile)
        chosen_mode, desc, fid, vid, words = mode.get(), prompt.get("1.0", "end-1c"), file_id.get(), requested.get(), text.get("1.0", "end-1c")
        if not words.strip() or len(words) > 500:
            status.set(tr("请填写不超过 500 字的试听文本."))
            return
        path = state["file"]
        state["busy"] = True
        generate.config(state="disabled")
        play.config(state="disabled")
        use_button.config(state="disabled")
        status.set(tr("正在合成音色…"))
        def work():
            try:
                uploaded = ai_voice_design.upload(p, path) if chosen_mode == "clone" and not fid and path else fid
                created_id, audio = ai_voice_design.create(p, chosen_mode, desc, uploaded, vid, words)
                events.put((created_id, audio, p, words, uploaded, ""))
            except Exception as e:
                error = str(e).replace(p.get("api_key") or "\0", "***") if isinstance(e, ValueError) else tr("音色合成失败，请检查网络和配置.")
                events.put(("", b"", p, words, fid, error))
        threading.Thread(target=work, name="oit-voice-design", daemon=True).start()

    def listen():
        if not state["profile"]:
            return
        preview.stop()
        preview.data.update(enabled=True, profiles=[state["profile"]], assign={"preview": "preview"})
        audio = state["audio"]
        preview.generate = lambda p, words: audio or ai_voice.synthesize(p, words)
        preview.play("preview", {"role": "ai", "uid": result.get(), "text": state["words"]})

    def use():
        if result.get():
            apply(result.get())
            dlg.finish(True)

    generate = ttk.Button(dlg.buttons, text=tr("合成音色"), command=create)
    generate.pack(side="left")
    play = ttk.Button(dlg.buttons, text=tr("试听"), command=listen, state="disabled")
    play.pack(side="left", padx=6)
    use_button = ttk.Button(dlg.buttons, text=tr("使用此音色"), command=use, state="disabled")
    use_button.pack(side="right")
    ttk.Button(dlg.buttons, text=tr("关闭"), command=dlg.cancel).pack(side="right", padx=6)

    def poll():
        while not events.empty():
            vid, audio, p, words, fid, error = events.get_nowait()
            state["busy"] = False
            generate.config(state="normal")
            if error:
                status.set(error)
                if state["profile"]:
                    play.config(state="normal")
                    use_button.config(state="normal")
            else:
                preview.clear("preview")
                result.set(vid)
                file_id.set(fid)
                p.update(id="preview", voice_id=vid)
                state.update(audio=audio, profile=p, words=words)
                play.config(state="normal")
                use_button.config(state="normal")
                status.set(tr("音色已创建，可试听或使用."))
        if preview.play_state == "failed":
            status.set(tr(preview.error))
            preview.play_state = "idle"
        if not state["closed"]:
            state["poll"] = dlg.after(100, poll)

    def close(event):
        if event.widget is dlg:
            state["closed"] = True
            dlg.after_cancel(state["poll"])
            preview.close()
            shutil.rmtree(preview_dir, ignore_errors=True)

    dlg.bind("<Destroy>", close, add="+")
    state["poll"] = dlg.after(100, poll)
    area.bind_children()
    dlg.wait()
    if parent.winfo_exists():
        parent.grab_set()
