# -*- coding: utf-8 -*-
"""完整气泡的流式分条、待发队列和插话撤销；不逐字展示。"""

import re

from .comm_interaction import tension_clean
from .comm_reply import to_halfwidth
from .comm_util import is_claudia

MESSAGE_BREAK = "<oit_message>"


class ReplyParts:
    """仅将代码围栏外独占一行的分隔符当作消息边界，兼容拆包。"""

    def __init__(self):
        self.tail = ""
        self.lines = []
        self.fence = ""

    def feed(self, delta, final=False):
        self.tail += delta
        lines = self.tail.split("\n")
        self.tail = "" if final else lines.pop()
        out = []
        for line in lines:
            stripped = line.strip()
            fence = re.match(r"^(`{3,}|~{3,})", stripped)
            if not self.fence and stripped == MESSAGE_BREAK:
                text = "\n".join(self.lines).strip()
                if text:
                    out.append(text)
                self.lines = []
                continue
            if fence:
                marker = fence[1]
                if not self.fence:
                    self.fence = marker
                elif marker[0] == self.fence[0] and len(marker) >= len(self.fence) and not stripped[len(marker):].strip():
                    self.fence = ""
            self.lines.append(line.rstrip("\r"))
        if final:
            text = "\n".join(self.lines).strip()
            if text:
                out.append(text)
            self.lines = []
        return out


def reply_parts(text):
    return ReplyParts().feed(text, final=True)


class ReplyBatch:
    def __init__(self, epoch, source, after):
        self.epoch, self.source, self.after = epoch, source, after
        self.parser = ReplyParts()
        self.messages = []
        self.cancelled = False
        self.finished = False


class DeliveryMixin:
    def _reply_begin(self):
        batch = ReplyBatch(self._epoch, self._req_ts, self._active_reply_after)
        self._reply_batch = batch
        return batch

    def _reply_queue(self, text, batch, ready=False):
        m = self._add("ai", text)
        # 未发出的正文仅本机持有；发送时才进入跨设备历史。
        m["pending_reply"] = batch.source
        m["reply_ready"] = ready
        m["hold"] = True
        m["reply_after"] = batch.after
        m["due"] = max(int(m.get("due") or 0), batch.after)
        batch.messages.append(m)
        return m

    def _reply_drain(self):
        with self._lock:
            events, self._reply_events = self._reply_events, []
        dirty = False
        for batch, text in events:
            if batch is not self._reply_batch or batch.cancelled or batch.finished or batch.epoch != self._epoch or self.hard_block():
                continue
            for part in batch.parser.feed(text):
                part = tension_clean(part).strip()
                if is_claudia(self.persona_name()):
                    part = to_halfwidth(part)
                if part:
                    self._reply_queue(part, batch)
                    dirty = True
        if dirty:
            self.save_chat()

    def _reply_discard(self, batch):
        if batch:
            doomed = {id(m) for m in batch.messages if "pending_reply" in m}
            self.messages[:] = [m for m in self.messages if id(m) not in doomed]
            self._reply_cancel_actions(doomed, batch.messages)

    def _reply_cancel_actions(self, doomed, candidates):
        for m in candidates:
            if id(m) in doomed:
                self._music_actions.pop(m["id"], None)
        joint = self._joint()
        if joint.get("state") == "queued" and any(id(m) in doomed and m["uid"] == joint.get("message") for m in candidates):
            self._joint_set({"state": ""})

    def _reply_interrupt(self):
        """用户插话只撤销未发出的气泡，已显示的保留；旧回调随后失效。"""
        latest = max((int(m.get("ts") or 0) for m in self.messages if m["role"] == "me"), default=0)
        doomed = {id(m) for m in self.messages if "pending_reply" in m and latest > m["pending_reply"]}
        self._reply_cancel_actions(doomed, self.messages)
        self.messages[:] = [m for m in self.messages if id(m) not in doomed]
        batch = self._reply_batch
        interrupted = bool(doomed) or bool(self.busy and batch and batch.epoch == self._epoch and latest > batch.source)
        if self.busy and batch and batch.epoch == self._epoch and latest > batch.source:
            batch.cancelled = True
            self._again = True
        if interrupted:
            self._reply_rewrite = True
        return interrupted

    def _reply_complete(self, text, batch=None):
        """校验完整响应后补入尾段，已入队或已发出的前段不重复添加。"""
        batch = batch or ReplyBatch(self._epoch, self._req_ts, self._active_reply_after)
        parts = reply_parts(text)
        existing = [m["text"] for m in batch.messages]
        if parts[:len(existing)] != existing:
            # 服务商改写了已预发的正文时，不把冲突内容追加为第二份回复。
            sent = [m for m in batch.messages if "pending_reply" not in m]
            self._reply_discard(batch)
            batch.messages = sent
            if sent:
                return sent[-1]
        for part in parts[len(batch.messages):]:
            self._reply_queue(part, batch, ready=True)
        for m in batch.messages:
            if "pending_reply" in m:
                m["reply_ready"] = True
        return batch.messages[-1] if batch.messages else None
