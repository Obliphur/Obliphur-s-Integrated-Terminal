# -*- coding: utf-8 -*-
"""共同阅读申请状态、同步与用户确认."""

from datetime import datetime, timedelta

import config
import routine
from comm.comm_util import is_claudia, oit_text
from i18n import tr


COREAD_OFFER_TTL = config.COMM_COREAD_OFFER_SECONDS * 1000
COREAD_ACTIVITY_BUDGET = config.COMM_COREAD_ACTIVITY_SECONDS


class CoReadMixin:
    def _coread_asking(self):
        ask = self.coread_ask
        return bool(ask and ask.get("pid") == self.pid and int(ask["ts"]) <= self._req_ts)

    def _coread_ask_end(self, notice: str = ""):
        self.coread_ask = None
        if notice:
            notice = tr(notice, name=self.share_ai_name())
            self.log("COREAD ask: " + notice)
            if self.on_share_notice:
                try:
                    self.on_share_notice(notice)
                except Exception:
                    pass
        self._changed()

    def _coread_answer(self, answer: str):
        if not self._coread_asking():
            return
        if self._ms() - int(self.coread_ask["ts"]) >= COREAD_OFFER_TTL:
            self._coread_ask_end("{name} 没有回复, 共读室没有打开")
            return
        if answer == "yes":
            self._coread_ask_end()
            if self.on_coread_open:
                try:
                    self.on_coread_open()
                except Exception as error:
                    self.log(f"COREAD open FAIL {type(error).__name__}: {error}")
                    self._coread_ask_end("启动共读室失败.")
        elif answer == "no":
            self._coread_ask_end("{name} 拒绝了共同阅读")
        else:
            self._coread_ask_end("{name} 没有明确同意, 共读室没有打开")

    def _coread_end_apply(self, op, message):
        self._coread_pending_end = {"session": str(op.get("session") or ""),
                                    "message": str(message.get("uid") or ""),
                                    "ready": not bool(message.get("hold"))}
        self._coread_end_deliver(message)

    def _coread_end_deliver(self, message):
        pending = getattr(self, "_coread_pending_end", None)
        if pending and message.get("uid") == pending.get("message") and not message.get("hold"):
            pending["ready"] = True

    def coread_offer(self):
        offer = self.cfg.get("coread_offer") or {}
        if (not isinstance(offer, dict) or offer.get("pid") != self.pid
                or offer.get("state") != "invite"
                or self._ms() - int(offer.get("ts") or 0) >= COREAD_OFFER_TTL):
            return {}
        uid = str(offer.get("message") or "")
        if uid and not any(m.get("uid") == uid and not m.get("hold") for m in self.messages):
            return {}
        return dict(offer)

    def _coread_offer_apply(self, _op, message=None):
        op = _op or {}
        quotes = (op.get("free_statement"), op.get("request_statement"))
        if (not is_claudia(self.persona_name()) or not self.pid or self.reading_mode
                or (self.cfg.get("coread_offer") or {}).get("state") in ("queued", "invite")
                or not any(self._coread_quote_is_recent(quote) for quote in quotes)):
            return False
        offer = {"id": str(self._ms()), "pid": self.pid, "state": "queued",
                 "ts": self._ms(), "message": str((message or {}).get("uid") or "")}
        self.cfg["coread_offer"] = offer
        self.save_cfg()
        if not message or not message.get("hold"):
            self._coread_offer_deliver(message)
        self._changed()
        self._push()
        return True

    def _coread_quote_is_recent(self, quote: str) -> bool:
        """只校验引文来源和时间；是否表示有空或邀请共读由模型结合上下文判断."""
        quote = str(quote or "").strip()
        if len(quote) < 2:
            return False
        now = self._ms()
        for message in reversed(self.messages):
            age = now - int(message.get("ts") or 0)
            if (message.get("role") == "me" and 0 <= age <= config.COMM_COREAD_QUOTE_SECONDS * 1000
                    and quote in str(message.get("text") or "")):
                return True
        return False

    def coread_restricted_seconds(self, start: datetime, end: datetime) -> float:
        """两时刻间循环专注、重要活动和睡眠的合并时长。"""
        if end <= start:
            return 0.0
        self._load_schedule()
        if self._rt:
            segments = self._rt_segs(start, end, self._rt, self.pid, joint=True)
        else:
            entries = self._entries()
            segments = []
            day = start.date() - timedelta(days=1)
            while day <= end.date() + timedelta(days=1):
                midnight = datetime.combine(day, datetime.min.time())
                segments.extend(dict(entry,
                                     start=midnight + timedelta(minutes=entry["start"]),
                                     end=midnight + timedelta(minutes=entry["end"]))
                                for entry in entries)
                day += timedelta(days=1)
        intervals = []
        for segment in segments:
            kind = routine.seg_class(segment)
            blocked = (kind == "key" or kind == "focus" and segment.get("loop") is not None
                       or kind == "sleep")
            if not blocked:
                continue
            a, b = max(start, segment["start"]), min(end, segment["end"])
            if b > a:
                intervals.append((a, b))
        return self._coread_merge_seconds(intervals)

    @staticmethod
    def _coread_merge_seconds(intervals) -> float:
        """合并重叠日程区间，避免同一时刻被多种受限活动重复计算."""
        total, current = 0.0, None
        for a, b in sorted(intervals, key=lambda item: item[0]):
            if current is None:
                current = [a, b]
            elif a <= current[1]:
                current[1] = max(current[1], b)
            else:
                total += (current[1] - current[0]).total_seconds()
                current = [a, b]
        if current is not None:
            total += (current[1] - current[0]).total_seconds()
        return total

    def _coread_offer_deliver(self, message=None):
        offer = self.cfg.get("coread_offer") or {}
        if offer.get("state") != "queued" or offer.get("pid") != self.pid:
            return
        uid = str(offer.get("message") or "")
        if uid and (not message or message.get("uid") != uid or message.get("hold")):
            return
        offer["state"] = "invite"
        offer["ts"] = self._ms()
        self.cfg["coread_offer"] = offer
        self.save_cfg()
        self._changed()
        self._push()

    def _coread_offer_tick(self):
        ask = self.coread_ask
        if ask and (ask.get("pid") != self.pid or self._ms() - int(ask.get("ts") or 0) >= config.COMM_COREAD_OFFER_SECONDS * 1000):
            self._coread_ask_end("{name} 没有回复, 共读室没有打开")
        offer = self.cfg.get("coread_offer") or {}
        if offer.get("state") == "queued":
            uid = str(offer.get("message") or "")
            message = next((m for m in self.messages if uid and m.get("uid") == uid), None)
            if message is None and self._ms() - int(offer.get("ts") or 0) >= COREAD_OFFER_TTL:
                self.cfg["coread_offer"] = {"id": str(offer.get("id") or ""), "pid": self.pid,
                                            "state": "", "ts": self._ms(), "message": ""}
                self.save_cfg()
                self._changed()
                self._push()
            elif message and not message.get("hold"):
                self._coread_offer_deliver(message)
        elif offer.get("state") == "invite" and self._ms() - int(offer.get("ts") or 0) >= COREAD_OFFER_TTL:
            self.cfg["coread_offer"] = {"id": str(offer.get("id") or ""), "pid": str(offer.get("pid") or ""),
                                        "state": "", "ts": self._ms(), "message": ""}
            self.save_cfg()
            self._changed()
            self._push()

    def coread_offer_respond(self, accept: bool) -> str:
        offer = self.coread_offer()
        if not offer:
            return tr("没有等你回复的共读邀请")
        self.cfg["coread_offer"] = {"id": str(offer.get("id") or ""), "pid": self.pid,
                                    "state": "", "ts": self._ms(), "message": ""}
        self.save_cfg()
        action = tr("接受了") if accept else tr("拒绝了")
        note = f"[系统·共读] 用户{action}你共同阅读的申请。"
        self._add("sys", "共同阅读", self._compose(self.now(), oit_text(note)))
        self.save_chat()
        self._changed()
        self._push()
        if accept and getattr(self, "on_coread_open", None):
            try:
                self.on_coread_open()
            except Exception as error:
                self.log(f"COREAD open FAIL {type(error).__name__}: {error}")
                return tr("启动共读室失败: {error}", error=str(error))
        return ""
