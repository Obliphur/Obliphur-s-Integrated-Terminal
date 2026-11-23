# -*- coding: utf-8 -*-
"""头像 / 未读 / 空闲用量 / 消息组装与发送 / 离开时的回复. MECHANICS 24.2 / 24.3.

从 comm.py 拆出来 (MECHANICS 24); 状态仍然全在 Comm 实例的 self 上, 这里只有方法.
comm.py 的 `class Comm` 继承这个 mixin, 外面看到的还是同一个类.
"""

import json
import hashlib
import os
import random
import re
import shutil
import threading
import time
import zlib
from datetime import datetime, timedelta

import ai_assist
import config
import i18n
from i18n import tr
import routine
from comm import comm_readme
from comm.comm_const import *
from comm.comm_schedule import PROACTIVE_P, seg_kind, seg_detail, schedule_entries, schedule_loops, loop_entries, schedule_segments, pro_pct, persona_probs
from comm.comm_reply import CUT_DEFAULT_MIN, CUT_MAX_MIN, HALFWIDTH_SYMBOLS, NET_DEFAULT_MIN, NET_MAX_MIN, WAIT_MAX_MIN, _BRACKET_RE, _CUT_N_RE, _CUT_RE, _EMPTY_WRAP_RE, _NET_RE, _WAIT_RE, clean_reply, cut_minutes, net_minutes, to_halfwidth
from comm.comm_builtin import BUILTIN_PERSONAS, DEFAULT_PERSONA, DEFAULT_BACKGROUND, DEFAULT_ROUTINE, ASSISTANT_PERSONA, ASSISTANT_DETAIL_LINE, BACKGROUND_LEGACIES, ASSISTANT_SLEEP_LINE, ASSISTANT_TITLE
from comm.comm_prompt import (REFUSE, SHIFT_TAG, MSGS_TAG, CONSENT_TUTORIAL, THINK_LONG, THINK_WORDS,
                              THINK_TIME_RE, think_text_reason, JOINT_RULE, rest_function_text, prefix_prompt)
from comm.comm_persona import SHIFT_LO, SHIFT_HI, SHIFT_KEEP, MAX_PERSONA, MEM_MAX, PERSONA_EXT, readme_text, pid_of, love_norm, love_line, shift_norm, shift_pairs, hm_text, shift_line, mem_fields, seed_builtin, ensure_personas, list_personas, persona_title, uses_prefix, system_head, PERSONA_HEAD, persona_system, persona_labels, read_persona, ROUTINE_SUFFIX, COMPANION_SEP, companion_base, is_companion_name, companion_files, companions_of, routine_name_of, read_routine, read_persona_full, import_persona
from comm.comm_util import (OIT_TAG, COND_TAG, WEEKDAY, MAX_MSG_CHARS, HISTORY_SENT, HISTORY_STEP, HISTORY_CHARS,
                       is_claudia, is_refusal, stamp, urlsplit_host, info_tags, joint_answer, _joint_time, clip_message,
                       cache_layout, oit_text, first_turn, api_messages, sanitize_mem)

_LEGACY_CLAUDIA_AVATARS = {
    "2708f7409a1c79a562af8cc9635edb30a9e8c415034f102faaf7cfaa2e788c21",
    "243cbbdeed7d22a6a2d671eca2e7e41c3cebebc83e83141004128c46e4c1712f",
}
_LEGACY_CLAUDIA_FULLS = {
    "00eeccf953e568441cf8cb9e8e8ebe36d0f6010862fb543123f61a713e454341",
    "243cbbdeed7d22a6a2d671eca2e7e41c3cebebc83e83141004128c46e4c1712f",
}


def _avatar_hash(path):
    with open(path, "rb") as source:
        return hashlib.sha256(source.read()).hexdigest()


class ChatMixin:
    @staticmethod
    def _avatar_name(who: str, persona: str) -> str:
        return f"{who}_{zlib.crc32(persona_title(persona or '').encode('utf-8')):08x}.png"

    def avatar_dest(self, who: str) -> str:
        """who: "me" / "ai". 我的头像和对方的头像都跟着人设走 (每个聊天对象各自设置, 换人设就换头像)."""
        return os.path.join(self.dir, "avatars", self._avatar_name(who, self.persona_name()))

    def _migrate_avatars(self):
        """以前"我"的头像只有一个 (me.png): 复制给现有的每个人设各一份, 然后删掉它 (只做一次, 之后各自设置)."""
        old = os.path.join(self.dir, "avatars", "me.png")
        if not os.path.isfile(old):
            return
        try:
            for name in dict.fromkeys(list_personas(self.personas) + [self.persona_name()]):       # 现在选着的 (可能是没选人设) 也算
                dst = os.path.join(self.dir, "avatars", self._avatar_name("me", name))
                if not os.path.exists(dst):
                    shutil.copyfile(old, dst)
            os.remove(old)
        except OSError:
            pass

    def _seed_default_avatars(self):
        """首次初始化默认头像；升级只替换指纹一致的旧内置图，不覆盖自选或恢复已移除的头像。"""
        version = int(self.cfg.get("default_avatars") or 0)
        if version >= 5:
            return
        names = set(list_personas(self.personas))
        complete = True
        for persona, file in DEFAULT_AVATARS.items():
            if persona not in names:
                continue
            source_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            src = os.path.join(source_root, "defaults", "avatars", file)
            dst = os.path.join(self.dir, "avatars", self._avatar_name("ai", persona))
            if not os.path.isfile(src):
                complete = False
                continue
            try:
                initial = version == 0 and not os.path.exists(dst)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                for source, target, old_hashes in (
                        (src, dst, _LEGACY_CLAUDIA_AVATARS),
                        (src[:-4] + ".full.png", dst[:-4] + ".full.png", _LEGACY_CLAUDIA_FULLS)):
                    legacy = (persona == "Claudia.txt" and os.path.isfile(target)
                              and _avatar_hash(target) in old_hashes)
                    if not (initial and not os.path.exists(target) or legacy):
                        continue
                    if not os.path.isfile(source):
                        complete = False
                        continue
                    shutil.copyfile(source, target)
            except OSError:
                complete = False
        if complete:
            self.cfg["default_avatars"] = 5
            self.save_cfg()

    def avatar_path(self, who: str) -> str:
        p = self.avatar_dest(who)
        return p if os.path.isfile(p) else ""

    def remove_avatar(self, who: str):
        p = self.avatar_dest(who)
        for f in (p, p[:-4] + ".full.png"):         # 完整版也一起删 (1.0.430)
            try:
                os.remove(f)
            except OSError:
                pass
        self._changed()

    def avatars_changed(self):
        self._changed()

    def visible(self) -> list:
        """聊天里显示的消息. 不能用沟通的时候 (专注 / 睡眠 / 外出 / 无言为真) 收到的对方的消息先藏起来 (hold), 回到休息 / 空闲再显示 (24.4)."""
        return [m for m in self.messages if m["role"] in ("me", "ai") and not m.get("hold")]

    def unanswered(self) -> bool:
        vis = [m for m in self.messages if m["role"] in ("me", "ai")]          # 藏起来的回复也算回复过了
        return bool(vis) and vis[-1]["role"] == "me" and not vis[-1].get("no_reply")

    def _newer_me(self, ts: int) -> bool:
        """最近一次请求发出之后, 用户又发了消息 (那次请求里没带上)."""
        return any(m.get("role") == "me" and int(m.get("ts") or 0) > int(ts) for m in self.messages)

    def _release_held(self):
        """回到休息 / 空闲: 藏着的消息现在显示 (未读 +N; 不在沟通页面的话响一声). 带 due 的 (CHAR_DELAY 的打字延时) 到点才显示, 按顺序一条一条出来."""
        if not any(m.get("hold") for m in self.messages) or self.blocked():
            return
        now = self._ms()
        away = self._away_kind()                           # 对方此刻 (按日程, 合约进行中 / 合约后的同步休息按合约) 还在专注 / 睡眠吗

        def due_of(m) -> int:
            floor = int(m.get("reply_after") or 0)
            d = max(int(m.get("due") or 0), floor)
            if m.get("late") and not away:                 # 对方专注时发的, 现在已经不在专注了 (日程到了 / 合约让对方休息了): 不用再等原来排的时刻
                return max(min(d, now), floor)
            return d
        held = []
        for index, m in enumerate(self.messages):
            if not m.get("hold"):
                continue
            if due_of(m) > now or ("pending_reply" in m and not self.leader()):
                break
            held.append(m)
            due = due_of(m)
            if "pending_reply" in m and due > 0 and now > due:
                # 心跳迟到、阻塞或离线恢复后仍保留气泡之间的等待，不一口气倾倒整个队列。
                for following in self.messages[index + 1:]:
                    if "pending_reply" in following and int(following.get("due") or 0) >= due:
                        following["due"] += now - due
        if not held:
            return
        for m in held:
            del m["hold"]
            due = due_of(m)
            m.pop("due", None)
            m["shown"] = now                               # 等用户回复的时间从这时候算起 (规则 IX)
            pending = m.pop("pending_reply", None) is not None
            m.pop("reply_ready", None)
            late = m.pop("late", False)
            if late or pending:                            # 本机待发消息发布时重盖时间；日程延迟保留约定的空闲起点。
                m["at"] = datetime.fromtimestamp((due if late else now) / 1000).isoformat(timespec="seconds")
                self._last_ts = m["ts"] = max(self._ms(), self._last_ts + 1)
                self.messages.remove(m)
                self.messages.append(m)
            self._deliver_invites(m)
            self._music_deliver(m)
        if not self.viewing:
            self.unread += len(held)
            unpinged = [m for m in held if not m.get("pinged")]
            if unpinged and self._ping():
                for m in unpinged:
                    m["pinged"] = True
        self.save_chat()
        self._changed()
        self._push()

    def _idle_used(self) -> float:
        rec = self.cfg.get("idle_use") or {}
        return float(rec.get("s") or 0) if rec.get("d") == f"{self.now():%Y-%m-%d}" else 0.0

    def idle_left(self) -> float:
        """今天空闲时间里还能用沟通多少秒."""
        return max(0.0, IDLE_DAILY_SECONDS - self._idle_used())

    def idle_over(self) -> bool:
        return self.idle_left() <= 0

    def _count_idle(self):
        now = self.now()
        last, self._idle_last = self._idle_last, now
        if last is None or not self.viewing or self.state_fn() != "idle":
            return
        dt = min((now - last).total_seconds(), IDLE_TICK_MAX)
        if dt <= 0:
            return
        before = self._idle_used()
        self.cfg["idle_use"] = {"d": f"{now:%Y-%m-%d}", "s": before + dt}
        if int(before) // 30 != int(before + dt) // 30:          # 每 30 秒存一次, 每分钟同步一次
            self.save_cfg()
            if int(before) // 60 != int(before + dt) // 60:
                self._push()
        if before < IDLE_DAILY_SECONDS <= before + dt:           # 刚用完: 马上存 / 同步, 界面立刻显示不能用
            self.save_cfg()
            self._push()
            self._changed()

    def set_viewing(self, on: bool):
        if not on:
            if self.viewing:
                self._music_discourtesy()
            self.voice.stop()
            self.music_player.stop()
            self._music_actions.clear()
        if self.viewing and not on:
            self.save_cfg()
        self.viewing = bool(on)
        if on and self.unread:
            self.unread = 0
            self._changed()

    def _compose(self, now: datetime, body: str) -> str:
        return f"[{stamp(now)}]\n" + body

    def _add(self, role: str, text: str, api: str = "") -> dict:
        m = self._mk(role, text, api, True)
        self.messages.append(m)
        return m

    def _sent(self):
        """用户发出一条之后的收尾."""
        self._reply_interrupt()
        self.error = ""
        self._catchup = False
        self.cfg["retry"] = {}
        self.save_cfg()
        self.save_chat()
        self._changed()
        self._push()

    def send(self, text: str, image: bytes = None, reply_delay_seconds: float = 0,
             reading_context: str = "", from_reader: bool = False) -> str:
        """用户发一条. 返回 "" = 已发出, 否则是不能发的原因."""
        if getattr(self, "reading_mode", False) and not from_reader:
            return "reading_mode"
        text = str(text or "").strip()
        request_draft = dict(getattr(self, "request_draft", {}))
        has_requests = any(request_draft.values())
        if not text and not image and not self.joint_draft and not has_requests:
            return "empty"
        if text.lower() == "/clear":
            self.clear()
            return ""
        if (self.joint_draft or has_requests) and reply_delay_seconds <= 0 and not image:
            why = self._send_package(self.joint_draft, text, request_draft)
            if not why:
                self.joint_draft = {}
                self.clear_request_draft()
            return why
        why = self.problem()
        if why:
            return why
        now = self.now()
        shown = text or "[图片]"
        message = self._add("me", shown, self._compose(now, shown))
        if text and self._music_discourtesy():
            self._music_discourtesy_uids.add(message["uid"])
        if image:
            self._request_image = image
        if reply_delay_seconds > 0:
            message["reply_after"] = self._ms() + int(reply_delay_seconds * 1000)
        self._interaction_sent(text, message)
        self._sent()
        if reading_context:
            self._ask_user(reading_context=reading_context)
        else:
            self._ask_user()
        return ""

    def _send_package(self, joint, text: str = "", requests=None) -> str:
        """一条带联合行动 / share data / 共读 / 演奏评判附件的消息, 附件内容只发给模型."""
        from comm import comm_data
        requests = requests or {}
        if not joint and not any(requests.values()):
            return "empty"
        why = self.problem()
        if why:
            return why
        parts, shown = [], []
        if joint:
            why = self.joint_block()
            if why:
                return why
            start, end = _joint_time(joint["start"]), _joint_time(joint["end"])
            shown.append(comm_data.joint_label(joint["terms"], start, end, self.now().date()))
            parts.append(oit_text(CONSENT_TUTORIAL + "\n\n" + comm_data.joint_text(joint["terms"], start, end)))
        if requests.get("share"):
            if self.share["on"] or self.share_asking():
                return tr("share data 已开启或正在等待回复")
            status = self.other_status()
            if status and status.get("kind") in HELD_KINDS and not status.get("joint"):
                return tr("{name} {st}, 现在没法征求同意", name=self.share_ai_name(), st=tr(STATUS_TEXT[status["kind"]]))
            shown.append(tr("🙈 share data"))
            parts.append(oit_text(CONSENT_TUTORIAL + "\n\n这是用户发来的 share data 请求。请按人设与实时状态判断是否同意：只有明确同意后，OIT才会开始分享；开始前不得声称已读取数据。仅对此请求填写answer。"))
        if requests.get("coread"):
            if self.coread_ask and self.coread_ask.get("pid") == self.pid:
                return tr("⏳ 等待共读同意")
            shown.append(tr("📚 共读"))
            parts.append(oit_text(CONSENT_TUTORIAL + "\n\n这是用户发来的共同阅读请求。请按人设与实时状态决定是否接受：只有明确同意后才会打开共读室；用户还须在OIT接受你的主动申请才会打开。进入共读后围绕当前书籍、页码和批注交流，循环专注、重要活动和睡眠的累计重叠最多10分钟；Claudia可说明结束原因后调用oit_reading_end，结束说明显示后才结束会话。仅对此请求填写answer。"))
        music = requests.get("music")
        if music:
            shown.append(tr("🎼 演奏评判"))
            parts.append(oit_text(str(music.get("text", "")).strip()))
        now = self.now()
        text = str(text or "").strip()
        if text:
            shown.append(text)
            parts.append(text)
        message = self._add("me", "\n".join(shown), self._compose(now, "\n\n".join(parts)))
        if music and music.get("delay", 0) > 0:
            message["reply_after"] = self._ms() + int(float(music["delay"]) * 1000)
        if text and self._music_discourtesy():
            self._music_discourtesy_uids.add(message["uid"])
        self._interaction_sent(text, message)
        if joint:                                           # 邀请发出去了: 等对方回 [Yes] / [No]
            self.cfg["joint_last"] = list(joint["terms"])
            self._joint_set({"id": f"{self._ms()}", "pid": self.pid, "state": "wait", "terms": list(joint["terms"]),
                             "start": joint["start"], "end": joint["end"]}, replace=True)
        if requests.get("share"):
            self.share_ask = {"pid": self.pid, "ts": self._ms(), "sent": True}
        if requests.get("coread"):
            self.coread_ask = {"pid": self.pid, "ts": message["ts"]}
        self._sent()
        self._ask_user()
        return ""

    def _away_kind(self) -> str:
        """对方此刻按日程在专注 / 睡眠 (HELD_KINDS) 吗: 是 = 返回 kind, 否 (空闲 / 休息 / 没有日程 / 没选人设) = ""."""
        if not self.persona_name():
            return ""
        k = (self.other_status() or {}).get("kind")
        return k if k in HELD_KINDS else ""

    def _ask_user(self, reading_context: str = ""):
        """用户发了消息之后: 对方在专注 / 睡眠就不调用接口, 消息 (连同发送时间, 已经在每条的开头) 先存着, 记下 hold,
        对方第一次空闲 / 休息时 tick() 一次性发给它; 否则马上请求."""
        kind = self._away_kind()
        if not kind:
            self._request("user", reading_context=reading_context)
            return
        if reading_context:
            for message in reversed(self.messages):
                if message.get("role") == "me":
                    message["api"] = str(message.get("api") or "") + "\n\n" + oit_text(
                        "当前 OIT 共读阅读上下文:\n" + str(reading_context)[:18000])
                    self.save_chat()
                    break
        since = self._ms()                                           # 存着的起点: 对方最后一条回复之后, 用户的第一条消息 (发消息时 _sent 已经清过 retry, 所以不能看旧的 hold)
        for m in reversed(self.messages):
            if m["role"] == "ai":
                break
            if m["role"] == "me":
                since = int(m["ts"])
        self.cfg["retry"] = {"hold": True, "at": self._ms(), "n": 0, "since": since}
        self.save_cfg()
        self.log(f"HOLD 对方在{STATUS_TEXT[kind]}: 用户的消息先存着, 等对方空闲 / 休息时一次性发")
        self._changed()
        self._push()
