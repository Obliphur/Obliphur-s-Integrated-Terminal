"""Rules 页 · 站点条目下面的 UP 主名单 (MECHANICS 4.8).
黑名单里的 bilibili.com → "UP 主白名单" (这些 UP 主的主页和视频照常打开);
白名单里的 bilibili.com → "UP 主黑名单" (这些 UP 主的主页和视频拦截).
名单的改动和网站名单一样要等冷静期 (RuleStore 管); 昵称是后台去查的, 查到了自动刷新."""

import threading
import tkinter as tk
from tkinter import ttk

import config
import toast
from config import COLORS
from i18n import tr
from netproxy.rules import up_site, up_key_for, WHITE_UPS
from .widgets import Modal


def open_ups(parent, rules, resolver, domain_key: str, rule: str, toast_status, on_changed=None):
    """rule: 网站名单里被点开的条目 (例如 bilibili.com); domain_key: 它在哪张网站名单里."""
    site = up_site(rule)
    if not site:
        return
    key = up_key_for(domain_key)
    heading = tr("✅  UP 主白名单") if key == WHITE_UPS else tr("⛔  UP 主黑名单")
    card = COLORS["bg_card"]

    dlg = Modal(parent, f"{rule} · {heading}", width=500, height=470)
    tk.Label(dlg.body, text=f"{rule}  ·  {heading}", bg=COLORS["bg"], fg=COLORS["text"],
             font=config.font("body", bold=True), anchor="w").pack(fill="x")

    lf = tk.Frame(dlg.body, bg=card, highlightbackground=COLORS["border"], highlightthickness=1)
    lf.pack(fill="both", expand=True, pady=(8, 6))
    sb = ttk.Scrollbar(lf, orient="vertical")
    lb = tk.Listbox(lf, height=9, activestyle="none", exportselection=False, selectmode="extended",
                    font=config.font("small"), yscrollcommand=sb.set,
                    highlightbackground=COLORS["border"])
    sb.config(command=lb.yview)
    sb.pack(side="right", fill="y")
    lb.pack(side="left", fill="both", expand=True)

    tk.Label(dlg.body, text=tr("粘贴 UP 主的主页链接或 UID"), bg=COLORS["bg"], fg=COLORS["text_dim"],
             font=config.font("small"), anchor="w").pack(fill="x")
    er = tk.Frame(dlg.body, bg=COLORS["bg"])
    er.pack(fill="x", pady=(2, 6))
    var = tk.StringVar()
    ent = ttk.Entry(er, textvariable=var)
    ent.pack(side="left", fill="x", expand=True)

    rows = []                      # [(kind, uid)]: live 在名单里 / pending-add 排队等着加入
    shown = {"sig": None}
    fetching = set()

    def label(uid):
        name = resolver.name(uid)
        return f"{name}  ({uid})" if name else uid

    def build():
        uids = rules.up_items(key, site)
        out, lines = [], []
        pend = {(p["op"], p["item"]): p for p in rules.pending if p["list"] == key}
        for uid in uids:
            p = pend.get(("remove", f"{site}/{uid}"))
            out.append(("live", uid))
            lines.append(label(uid) + (("    ⏳ " + tr("将于 {at} 删除").format(at=f"{p['apply_at']:%m-%d %H:%M}"))
                                       if p else ""))
        for (op, item), p in pend.items():
            if op == "add" and item.startswith(site + "/"):
                uid = item[len(site) + 1:]
                out.append(("pending-add", uid))
                lines.append("⏳ " + label(uid) + "    " + tr("将于 {at} 加入").format(at=f"{p['apply_at']:%m-%d %H:%M}"))
        return out, lines

    def fetch_missing():
        todo = [uid for _k, uid in rows if not resolver.name(uid) and uid not in fetching]
        if not todo:
            return
        fetching.update(todo)

        def work():
            for uid in todo:
                resolver.fetch_name(uid)
        threading.Thread(target=work, daemon=True, name="oit-up-names").start()

    def render(force=False):
        new_rows, lines = build()
        sig = tuple(lines)
        if not force and sig == shown["sig"]:
            return
        shown["sig"] = sig
        sel = {rows[i] for i in lb.curselection() if i < len(rows)}
        rows[:] = new_rows
        lb.delete(0, "end")
        for line in lines:
            lb.insert("end", line)
        for i, r in enumerate(rows):
            if r in sel:
                lb.selection_set(i)
        fetch_missing()

    def poll():
        try:
            if not dlg.winfo_exists():
                return
            render()
            dlg.after(700, poll)
        except tk.TclError:
            pass

    def add(_e=None):
        text = var.get().strip()
        if not text:
            return
        status, item = rules.add(key, text)
        if status in ("applied", "staged", "cancelled"):
            var.set("")
        toast_status(status, key, item)
        render(force=True)
        if on_changed:
            on_changed()

    def remove():
        picks = [rows[i][1] for i in lb.curselection() if i < len(rows)]
        if not picks:
            toast.info(tr("先在列表里选中要删除的项."))
            return
        statuses = [rules.remove(key, f"{site}/{uid}")[0] for uid in picks]
        for st in ("staged", "applied", "cancelled"):
            if st in statuses:
                toast_status(st, key, f"{site}/{picks[-1]}" if len(picks) == 1 else None)
                break
        else:
            toast_status(statuses[-1])
        render(force=True)
        if on_changed:
            on_changed()

    ent.bind("<Return>", add)
    ttk.Button(er, text=tr("添加"), style="Accent.TButton", command=add).pack(side="left", padx=(6, 0))
    ttk.Button(dlg.buttons, text=tr("删除选中"), command=remove).pack(side="left")
    ttk.Button(dlg.buttons, text=tr("关闭"), command=dlg.cancel).pack(side="right")

    render(force=True)
    poll()
    ent.focus_set()
    dlg.wait()
