# -*- coding: utf-8 -*-
"""规则页里的两个对话框:模型设置,AI 名单建议.

AI 建议的每一步都由用户确认:
    ① 勾选要提供什么(应用 / 浏览器域名+次数)→ ② 预览将要发送的完整文本 →
    ③ 点「发送」才联网 → ④ 勾选要采纳的条目 → 才写入名单.
黑名单模式下不展示任何"理由"(解析器已丢弃,见 ai_assist.py).
"""

import threading
import tkinter as tk
from tkinter import ttk
from urllib.parse import urlsplit

import ai_assist
import config
import context_collect
import toast
from config import COLORS
from i18n import tr
from netproxy import WHITE_DOMAINS, WHITE_APPS, BLACK_DOMAINS, BLACK_APPS
from .widgets import Modal, wrap_label

_DAYS = context_collect.HISTORY_WINDOWS


def _label(parent, text, dim=False, small=False, **kw):
    lb = tk.Label(parent, text=text, bg=COLORS["bg"],
                  fg=COLORS["text_dim"] if dim else COLORS["text"],
                  font=config.font("small" if small else "body"),
                  anchor="w", justify="left", **kw)
    return lb


def _provider_label(key: str) -> str:
    return tr(ai_assist.PROVIDERS[key]["label"])


def _host(url: str) -> str:
    try:
        return urlsplit(url).netloc or url
    except ValueError:
        return url


# ══════════════ 模型设置 ══════════════
def open_model_settings(parent, cfg: "ai_assist.AIConfig") -> bool:
    """返回 True 表示保存过."""
    dlg = Modal(parent, tr("AI 模型设置"), width=540, height=520)
    b = dlg.body
    state = {"cur": cfg.provider, "family": ""}

    _label(b, tr("模型")).pack(fill="x")
    cb_provider = ttk.Combobox(b, state="readonly",
                               values=[_provider_label(k) for k in ai_assist.PROVIDER_ORDER])
    cb_provider.current(ai_assist.PROVIDER_ORDER.index(cfg.provider))
    cb_provider.pack(fill="x", pady=(2, 8))

    _label(b, tr("Base URL")).pack(fill="x")
    var_url = tk.StringVar()
    ttk.Entry(b, textvariable=var_url).pack(fill="x", pady=(2, 8))

    _label(b, tr("模型类型")).pack(fill="x")
    cb_family = ttk.Combobox(b, state="readonly",
                             values=[tr(ai_assist.FAMILY_LABELS[f]) for f in ai_assist.FAMILIES])
    cb_family.pack(fill="x", pady=(2, 8))

    _label(b, tr("型号")).pack(fill="x")
    mrow = tk.Frame(b, bg=COLORS["bg"])
    mrow.pack(fill="x", pady=(2, 8))
    cb_model = ttk.Combobox(mrow)
    cb_model.pack(side="left", fill="x", expand=True)
    btn_fetch = ttk.Button(mrow, text=tr("获取模型列表"))
    btn_fetch.pack(side="left", padx=(6, 0))

    _label(b, tr("接口协议")).pack(fill="x")
    cb_proto = ttk.Combobox(b, state="readonly", values=list(ai_assist.PROTOCOLS))
    cb_proto.pack(fill="x", pady=(2, 8))

    _label(b, tr("API Key")).pack(fill="x")
    var_key = tk.StringVar()
    ent_key = ttk.Entry(b, textvariable=var_key, show="•")
    ent_key.pack(fill="x", pady=(2, 2))
    var_show = tk.BooleanVar(value=False)
    ttk.Checkbutton(b, text=tr("显示 Key"), variable=var_show,
                    command=lambda: ent_key.config(show="" if var_show.get() else "•")
                    ).pack(anchor="w")
    note = _label(b, "", dim=True, small=True)
    note.pack(fill="x", pady=(6, 0))
    wrap_label(note, 20)

    def key_of():
        return ai_assist.PROVIDER_ORDER[cb_provider.current()]

    def family_of_ui() -> str:
        return state.get("family") or "other"

    def store_current():
        """把界面上的值记回 cfg(内存),切换供应商前调用."""
        cfg.set_profile(state["cur"], base_url=var_url.get(), model=cb_model.get(),
                        api_key=var_key.get(), family=family_of_ui())

    def load(k):
        prof = cfg.profile(k)
        fam = prof.get("family") or (k if k in ai_assist.FAMILY_PROTOCOL else "other")
        state["family"] = fam
        var_url.set(prof["base_url"])
        cb_model.config(values=ai_assist.models_for(dict(prof, provider=k)))
        cb_model.set(prof["model"])
        cb_family.set(tr(ai_assist.FAMILY_LABELS[fam]))
        cb_family.config(state="readonly" if k == "custom" else "disabled")
        cb_proto.set(ai_assist.FAMILY_PROTOCOL[fam])
        cb_proto.config(state="disabled")                 # 协议由模型类型决定, 不再单选
        var_key.set(prof["api_key"])
        note.config(text=(tr("请填写 Base URL 与型号,并选一个模型类型 (决定协议与缓存方式).")
                          + "\n" + tr("Gemini 类型要填官方地址, 不要用 OpenAI 兼容地址.")
                          if k == "custom"
                          else tr("已填入 {name} 的 Base URL.")
                          ).format(name=_provider_label(k)) + "\n" +
                 tr("API Key 加密保存在本机,只发往所选服务商."))
        state["cur"] = k

    def fetch():
        """向服务商取模型列表 (后台线程), 填进型号下拉框."""
        store_current()
        prof = cfg.profile(state["cur"])
        btn_fetch.config(state="disabled")

        def work():
            try:
                res = ("ok", ai_assist.list_models(prof))
            except ai_assist.AIError as e:
                res = ("err", e.message)
            except Exception as e:      # noqa: BLE001
                res = ("err", type(e).__name__)
            try:
                dlg.after(0, lambda: done(res))
            except Exception:
                pass

        def done(res):
            if not dlg.winfo_exists():
                return
            btn_fetch.config(state="normal")
            if res[0] == "ok":
                cb_model.config(values=res[1])
                toast.success(tr("取到 {n} 个模型.").format(n=len(res[1])))
            else:
                toast.warn(tr(res[1]))
        threading.Thread(target=work, daemon=True).start()
    btn_fetch.config(command=fetch)

    def on_provider(_e=None):
        store_current()
        load(key_of())

    def on_family(_e=None):
        idx = cb_family.current()
        if idx < 0:
            return
        state["family"] = ai_assist.FAMILIES[idx]
        cb_proto.set(ai_assist.FAMILY_PROTOCOL[state["family"]])

    cb_provider.bind("<<ComboboxSelected>>", on_provider)
    cb_family.bind("<<ComboboxSelected>>", on_family)
    load(state["cur"])

    saved = {"ok": False}

    def save():
        store_current()
        cfg.provider = state["cur"]
        if cfg.save():
            saved["ok"] = True
            toast.success(tr("已保存."))
            dlg.finish(True)
        else:
            toast.error(tr("保存失败,请检查应用数据目录的写入权限."))

    ttk.Button(dlg.buttons, text=tr("保存"), style="Accent.TButton", command=save).pack(
        side="right")
    ttk.Button(dlg.buttons, text=tr("取消"), command=dlg.cancel).pack(side="right", padx=(0, 8))
    dlg.wait()
    return saved["ok"]


# ══════════════ AI 建议 ══════════════
def open_suggest(parent, cfg: "ai_assist.AIConfig", rules, mode: str, on_done=None):
    """mode: ai_assist.MODE_BLACKLIST / MODE_WHITELIST."""
    black = mode != ai_assist.MODE_WHITELIST
    prof = cfg.profile()
    try:
        ai_assist.check_profile(prof)
    except ai_assist.AIError as e:
        toast.warn(tr(e.message) + " —— " + tr("请先到「模型设置」里配置."))
        return
    provider = _provider_label(cfg.provider)
    target = f"{provider} · {prof['model']} · {_host(prof['base_url'])}"
    title = tr("AI 建议 · 黑名单") if black else tr("AI 建议 · 白名单")
    dlg = Modal(parent, title, width=640, height=600)
    body = dlg.body
    ctx = {"apps": [], "domains": [], "text": "", "items": [], "edited": False}
    box = {"frame": None}

    def show(builder):
        if box["frame"] is not None:
            box["frame"].destroy()
        fr = tk.Frame(body, bg=COLORS["bg"])
        fr.pack(fill="both", expand=True)
        box["frame"] = fr
        for w in dlg.buttons.winfo_children():
            w.destroy()
        builder(fr)

    def btn(text, cmd, accent=False, left=False):
        ttk.Button(dlg.buttons, text=text, command=cmd,
                   style="Accent.TButton" if accent else "TButton").pack(
            side="left" if left else "right", padx=(0, 8) if not left else (0, 0))

    def poll(job, done):
        """线程里干活,界面线程轮询结果(tk 不允许在别的线程里碰控件)."""
        if not dlg.winfo_exists():
            return
        if job["done"]:
            done(job)
        else:
            dlg.after(120, lambda: poll(job, done))

    def run_in_thread(fn, done):
        job = {"done": False, "value": None, "error": None}

        def work():
            try:
                job["value"] = fn()
            except Exception as e:                      # noqa: BLE001
                job["error"] = e
            job["done"] = True
        threading.Thread(target=work, daemon=True).start()
        poll(job, done)

    # ── ① 同意 ──
    var_apps = tk.BooleanVar(value=True)
    var_hist = tk.BooleanVar(value=True)
    var_days = tk.StringVar(value=str(_DAYS[1]))
    var_goal = tk.StringVar()
    var_model = tk.StringVar()

    def pick_model():
        """选了另一个模型: 记进模型设置 (聊天也用同一份), 这次请求就用它."""
        m = var_model.get().strip()
        if not m or m == prof["model"]:
            return
        prof["model"] = m
        cfg.set_profile(cfg.provider, model=m)
        cfg.save()

    def fetch_models(cb):
        def work():
            try:
                res = ("ok", ai_assist.list_models(prof))
            except ai_assist.AIError as e:
                res = ("err", e.message)
            except Exception as e:      # noqa: BLE001
                res = ("err", type(e).__name__)
            try:
                dlg.after(0, lambda: finish(res))
            except Exception:
                pass

        def finish(res):
            if not dlg.winfo_exists():
                return
            if res[0] == "ok":
                cb.config(values=res[1])
                toast.success(tr("取到 {n} 个模型.").format(n=len(res[1])))
            else:
                toast.warn(tr(res[1]))
        threading.Thread(target=work, daemon=True).start()

    def step_consent(fr):
        _label(fr, tr("先说清楚会发生什么"), ).pack(fill="x")
        info = _label(
            fr,
            tr("· 只读取你勾选的内容,点「发送」后才联网.") + "\n" +
            tr("· 浏览器记录只取域名和访问次数.") + "\n" +
            tr("· 发送前可以在预览里删改.") + "\n" +
            tr("· 只发给下面这家服务商,本程序不保存.") + "\n" +
            (tr("· 模型只给候选条目,不给评价.") if black else
             tr("· 候选条目由你勾选后才加入名单.")),
            dim=True, small=True)
        info.pack(fill="x", pady=(4, 10))
        wrap_label(info, 20)
        _label(fr, tr("发送给: {t}").format(t=target)).pack(fill="x", pady=(0, 4))
        mr = tk.Frame(fr, bg=COLORS["bg"])
        mr.pack(fill="x", pady=(0, 10))
        _label(mr, tr("模型")).pack(side="left", padx=(0, 6))
        var_model.set(prof["model"])
        cbm = ttk.Combobox(mr, textvariable=var_model, values=ai_assist.models_for(prof), width=34)
        cbm.pack(side="left")
        cbm.bind("<<ComboboxSelected>>", lambda _e: pick_model())
        cbm.bind("<Return>", lambda _e: pick_model())
        cbm.bind("<FocusOut>", lambda _e: pick_model())
        ttk.Button(mr, text=tr("获取"), width=6, command=lambda: fetch_models(cbm)).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(fr, text=tr("已安装的应用(名称 + exe 名)"), variable=var_apps).pack(
            anchor="w")
        hr = tk.Frame(fr, bg=COLORS["bg"])
        hr.pack(fill="x", pady=(6, 0))
        ttk.Checkbutton(hr, text=tr("浏览器常访问的网站(仅域名与次数),回看"),
                        variable=var_hist).pack(side="left")
        ttk.Combobox(hr, width=5, state="readonly", textvariable=var_days,
                     values=[str(d) for d in _DAYS]).pack(side="left", padx=6)
        _label(hr, tr("天")).pack(side="left")
        _label(fr, tr("补充说明(可选,会随请求一起发送)")).pack(fill="x", pady=(14, 2))
        ttk.Entry(fr, textvariable=var_goal).pack(fill="x")
        btn(tr("采集并预览"), start_collect, accent=True)
        btn(tr("取消"), dlg.cancel)

    def start_collect():
        if not var_apps.get() and not var_hist.get():
            toast.info(tr("至少勾选一项."))
            return
        days = int(var_days.get() or 90)
        want_a, want_h = var_apps.get(), var_hist.get()

        def waiting(fr):
            _label(fr, tr("正在读取本机信息…")).pack(pady=40)
        show(waiting)
        run_in_thread(lambda: context_collect.collect_context(
            want_apps=want_a, want_history=want_h, days=days), collected)

    def collected(job):
        if job["error"] is not None:
            toast.error(tr("读取失败."))
            show(step_consent)
            return
        res = job["value"]
        ctx.update(apps=res["apps"], domains=res["domains"], text=res["text"], edited=False)
        if not res["text"].strip():
            toast.info(tr("没有读到可用的内容."))
            show(step_consent)
            return
        show(step_preview)

    # ── ② 预览 ──
    def step_preview(fr):
        _label(fr, tr("将要发送的全部内容,可以直接编辑.")
               ).pack(fill="x")
        tf = tk.Frame(fr, bg=COLORS["bg"])
        tf.pack(fill="both", expand=True, pady=(6, 4))
        sb = ttk.Scrollbar(tf, orient="vertical")
        txt = tk.Text(tf, wrap="word", undo=True, font=config.font("small"),
                      yscrollcommand=sb.set,
                      highlightbackground=COLORS["border"], highlightthickness=1)
        sb.config(command=txt.yview)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        if not ctx["edited"]:                 # 第一次进来才装入采集结果;从「返回」回来保留用户的修改
            goal = var_goal.get().strip()
            ctx["text"] = ctx["text"] + (("\n\n" + tr("【补充说明】") + "\n" + goal) if goal else "")
            ctx["edited"] = True
        txt.insert("1.0", ctx["text"])
        box["txt"] = txt
        _label(fr, tr("发送给: {t}").format(t=target), dim=True, small=True).pack(fill="x")
        btn(tr("发送"), start_send, accent=True)
        btn(tr("返回"), back_to_consent)
        btn(tr("取消"), dlg.cancel)

    def grab_text():
        txt = box.get("txt")
        if txt is not None:
            ctx["text"] = txt.get("1.0", "end").strip()
        return ctx["text"]

    def back_to_consent():
        grab_text()
        show(step_consent)

    def start_send():
        text = grab_text()
        if not text:
            toast.info(tr("内容是空的,没有可发送的信息."))
            return
        if len(text) > ai_assist.MAX_CONTEXT_CHARS:
            toast.warn(tr("内容太长了,请删减到 {n} 字以内.").format(n=ai_assist.MAX_CONTEXT_CHARS))
            return

        def waiting(fr):
            _label(fr, tr("正在请求 {p}…").format(p=provider)).pack(pady=40)
        show(waiting)
        # 清单以「最终要发送的文本」为准(用户可能删改过),而不是采集时的原始结果
        known_d, known_a = ai_assist.extract_known(text)
        run_in_thread(lambda: ai_assist.suggest(prof, mode, text, known_d, known_a),
                      received)

    def received(job):
        err = job["error"]
        if err is not None:
            msg = tr(err.message) if isinstance(err, ai_assist.AIError) else tr("请求失败.")
            toast.error(msg)
            show(step_preview)
            return
        res = job["value"]
        dkey, akey = (BLACK_DOMAINS, BLACK_APPS) if black else (WHITE_DOMAINS, WHITE_APPS)
        items = []
        for e in res["domains"]:
            items.append((dkey, e))
        for e in res["apps"]:
            items.append((akey, e))
        ctx["items"] = items
        show(step_result)

    # ── ③ 结果 ──
    def step_result(fr):
        items = ctx["items"]
        if not items:
            _label(fr, tr("模型没有给出可采纳的条目.")).pack(pady=30)
            btn(tr("关闭"), dlg.cancel)
            return
        _label(fr, tr("勾选要加入{name}的条目(默认全选):").format(
            name=tr("黑名单") if black else tr("白名单"))).pack(fill="x")
        lf = tk.Frame(fr, bg=COLORS["bg"])
        lf.pack(fill="both", expand=True, pady=(6, 4))
        sb = ttk.Scrollbar(lf, orient="vertical")
        lb = tk.Listbox(lf, selectmode="extended", activestyle="none", exportselection=False,
                        font=config.font("small"), yscrollcommand=sb.set)
        sb.config(command=lb.yview)
        sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)
        for key, e in items:
            icon = "🖥" if key in (BLACK_APPS, WHITE_APPS) else "🌐"
            line = f"{icon}  {e['item']}"
            if not black:                      # 黑名单:只有条目本身,不显示任何说明
                if e["reason"]:
                    line += f"   —  {e['reason']}"
                if e["extra"]:
                    line += "   " + tr("(推荐,不在本机记录中)")
            lb.insert("end", line)
        lb.select_set(0, "end")
        hint = (tr("配置期内:立即生效.") if config.changes_instant()
                else tr("48 小时后生效."))
        _label(fr, hint, dim=True, small=True).pack(fill="x")

        def adopt():
            picked = list(lb.curselection())
            if not picked:
                toast.info(tr("没有选中任何条目."))
                return
            statuses = [rules.add(items[i][0], items[i][1]["item"])[0] for i in picked]
            if "staged" in statuses:
                toast.info(tr("已提交,48 小时后生效."))
            elif "applied" in statuses:
                toast.success(tr("已加入名单."))
            else:
                toast.info(tr("没有新增项(可能已在名单里)."))
            if on_done:
                on_done()
            dlg.finish(True)
        btn(tr("加入名单"), adopt, accent=True)
        btn(tr("全选"), lambda: lb.select_set(0, "end"), left=False)
        btn(tr("取消"), dlg.cancel)

    show(step_consent)
    dlg.wait()
