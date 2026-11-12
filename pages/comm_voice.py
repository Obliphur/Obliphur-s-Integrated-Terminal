"""沟通页的语音队列：只在页面可见且沟通可用时播放。"""

import time
from i18n import tr
import toast


class CommVoiceMixin:
    def _voice_start(self):
        self._voice_cancel()
        c = self.comm
        self._voice_context = (c.pid, c.cfg.get("clear_ts", 0))
        self._voice_queue = c.voice.autoplay(c.persona_name(), c.visible()) if (
            c.voice.data["enabled"] and c.voice.data["auto"] and c.voice.play_allowed() and not c.blocked()) else []
        self._voice_next = time.monotonic()
        self._voice_active = False

    def _voice_poll(self):
        self._voice_tick()
        if self.comm.viewing:
            self._voice_job = self.after(100, self._voice_poll)

    def _voice_cancel(self):
        job = getattr(self, "_voice_job", None)
        if job:
            self.after_cancel(job)
            self._voice_job = None
        self.comm.voice.stop()
        self._voice_queue = []
        self._voice_active = False

    def _voice_scroll(self, m):
        self.update_idletasks()
        if m["id"] not in self._rendered:
            msgs = self.comm.visible()
            ids = [x["id"] for x in msgs]
            if not self._rendered or m["id"] not in ids:
                return
            start, end = ids.index(m["id"]), ids.index(self._rendered[0])
            for older in reversed(msgs[start:end]):
                self._rows.insert(0, self._add_row(older, before=self._rows[0]))
                self._rendered.insert(0, older["id"])
            self._cap = max(self._cap, len(self._rows) + 40)
            self.update_idletasks()
        if m["id"] in self._rendered:
            row = self._rows[self._rendered.index(m["id"])]
            self.area.canvas.yview_moveto(row.winfo_y() / max(1, self.area.inner.winfo_height()))

    def _voice_manual(self, m):
        if not self.comm.voice.play_allowed():
            toast.warn(tr("仅可在空闲或休息期间播放语音."))
            return
        if self.comm.blocked() or not self.comm.viewing:
            return
        self._voice_cancel()
        self.comm.voice.play(self.comm.persona_name(), m)
        self._voice_job = self.after(100, self._voice_poll)

    def _voice_tick(self):
        c = self.comm
        context = (c.pid, c.cfg.get("clear_ts", 0))
        if not c.viewing or c.blocked() or not c.voice.data["enabled"] or not c.voice.play_allowed():
            self._voice_cancel()
            return
        if context != getattr(self, "_voice_context", None):
            self._voice_start()
        voice = c.voice
        if voice.play_state == "failed" and voice.error:
            toast.warn(tr(voice.error))
            voice.error = ""
        if not voice.data["auto"]:
            self._voice_queue = []
            if getattr(self, "_voice_active", False):
                voice.stop()
                self._voice_active = False
        if getattr(self, "_voice_active", False):
            if voice.play_state in ("done", "failed", "idle"):
                self._voice_active = False
                self._voice_next = time.monotonic() + voice.data["gap"]
            return
        queue = getattr(self, "_voice_queue", [])
        if queue and time.monotonic() >= self._voice_next:
            m = queue.pop(0)
            if voice.played(c.persona_name(), m):
                return
            self._voice_scroll(m)
            voice.play(c.persona_name(), m)
            self._voice_active = True
