"""配置表单的自动取模型列表：输入停顿后请求，旧结果不覆盖新配置。"""

import queue
import threading


def auto_models(dlg, snapshot, fetch, apply, delay=700, status=None):
    results = queue.Queue()
    state = {"job": None, "poll": None, "pending": set(), "cache": {}}

    def request():
        state["job"] = None
        sig, profile = snapshot()
        if not profile:
            return
        if sig in state["cache"]:
            apply(state["cache"][sig])
            if status:
                status("done")
            return
        if sig in state["pending"]:
            return
        state["pending"].add(sig)
        if status:
            status("loading")
        def work():
            try:
                results.put((sig, fetch(profile)))
            except Exception:
                results.put((sig, None))  # 自动获取失败保留现有选项，不打断填写。
        threading.Thread(target=work, daemon=True).start()

    def schedule(*_args, refresh=False):
        if refresh:
            state["cache"].pop(snapshot()[0], None)
        if state["job"]:
            dlg.after_cancel(state["job"])
        state["job"] = dlg.after(delay, request)

    def poll():
        while not results.empty():
            sig, models = results.get_nowait()
            state["pending"].discard(sig)
            if models:
                state["cache"][sig] = models
            if sig == snapshot()[0]:
                if models:
                    apply(models)
                if status:
                    status("done" if models else "failed")
        state["poll"] = dlg.after(100, poll)

    def destroy(event):
        if event.widget is dlg:
            for key in ("job", "poll"):
                if state[key]:
                    dlg.after_cancel(state[key])
    dlg.bind("<Destroy>", destroy, add="+")
    state["poll"] = dlg.after(100, poll)
    return schedule
