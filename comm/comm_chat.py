# -*- coding: utf-8 -*-
"""头像 / 未读 / 空闲用量 / 消息组装与发送 / 离开时的回复. MECHANICS 24.2 / 24.3.

从 comm.py 拆出来 (MECHANICS 24); 状态仍然全在 Comm 实例的 self 上, 这里只有方法.
comm.py 的 `class Comm` 继承这个 mixin, 外面看到的还是同一个类.
"""

import json
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
from comm.comm_prompt import REFUSE, SHIFT_TAG, MSGS_TAG, THINK_LONG, THINK_WORDS, THINK_TIME_RE, think_text_reason, JOINT_RULE, rest_function_text, prefix_prompt
from comm.comm_persona import SHIFT_LO, SHIFT_HI, SHIFT_KEEP, MAX_PERSONA, MEM_MAX, PERSONA_EXT, readme_text, pid_of, love_norm, love_line, shift_norm, shift_pairs, hm_text, shift_line, mem_fields, seed_builtin, ensure_personas, list_personas, persona_title, uses_prefix, system_head, PERSONA_HEAD, persona_system, persona_labels, read_persona, ROUTINE_SUFFIX, COMPANION_SEP, companion_base, is_companion_name, companion_files, companions_of, routine_name_of, read_routine, read_persona_full, import_persona
from comm.comm_util import (OIT_TAG, STATE_TAG, COND_TAG, WEEKDAY, MAX_MSG_CHARS, HISTORY_SENT, HISTORY_STEP, HISTORY_CHARS,
                       is_claudia, is_refusal, stamp, urlsplit_host, info_tags, joint_answer, _joint_time, clip_message,
                       cache_layout, oit_text, first_turn, api_messages, sanitize_mem)


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
        """自带人设的默认头像 (1.0.426): 第一次运行 (或重置后) 放一份到 avatars, 之后不再自动更新 (1.0.434).
        用户自己换过 / 删过的不动."""
        if self.cfg.get("default_avatars"):
            return
        names = set(list_personas(self.personas))
        for persona, file in DEFAULT_AVATARS.items():
            if persona not in names:
                continue
            src = os.path.join(HERE, "defaults", "avatars", file)
            dst = os.path.join(self.dir, "avatars", self._avatar_name("ai", persona))
            if os.path.exists(dst) or not os.path.isfile(src):
                continue
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
                full_src = src[:-4] + ".full.png"
                if os.path.isfile(full_src):
                    shutil.copyfile(full_src, dst[:-4] + ".full.png")
            except OSError:
                pass
        self.cfg["default_avatars"] = True
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
        return bool(vis) and vis[-1]["role"] == "me"

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
            d = int(m.get("due") or 0)
            if m.get("late") and not away:                 # 对方专注时发的, 现在已经不在专注了 (日程到了 / 合约让对方休息了): 不用再等原来排的时刻
                return min(d, now)
            return d
        held = []
        for m in self.messages:
            if not m.get("hold"):
                continue
            if due_of(m) > now:
                break
            held.append(m)
        if not held:
            return
        for m in held:
            del m["hold"]
            due = due_of(m)
            m.pop("due", None)
            m["shown"] = now                               # 等用户回复的时间从这时候算起 (规则 IX)
            if m.pop("late", False):                       # 对方专注时发的: 发送时间改成下一个空闲 / 休息开始的时刻, 排到最后 (还没同步给别的设备, 改 ts 没关系)
                m["at"] = datetime.fromtimestamp(due / 1000).isoformat(timespec="seconds")
                self._last_ts = m["ts"] = max(self._ms(), self._last_ts + 1)
                self.messages.remove(m)
                self.messages.append(m)
        if not self.viewing:
            self.unread += len(held)
            self._ping()
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
        self.error = ""
        self._catchup = False
        self.cfg["retry"] = {}
        self.save_cfg()
        self.save_chat()
        self._changed()
        self._push()

    def send(self, text: str) -> str:
        """用户发一条. 返回 "" = 已发出, 否则是不能发的原因."""
        text = str(text or "").strip()
        if not text and not self.joint_draft:
            return "empty"
        if text.lower() == "/clear":
            self.clear()
            return ""
        if self.joint_draft:                            # 选好的联合行动: 和这条消息一起发
            why = self._send_package(self.joint_draft, text)
            if not why:
                self.joint_draft = {}
            return why
        why = self.problem()
        if why:
            return why
        now = self.now()
        self._add("me", text, self._compose(now, text))
        self._sent()
        self._ask_user()
        return ""

    def _send_package(self, joint, text: str = "") -> str:
        """一条带联合行动邀请的消息 (joint = {terms, start, end}). 聊天里只显示说明, 邀请的内容附在发给模型的内容里."""
        from comm import comm_data
        if not joint:
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
            parts.append(comm_data.joint_text(joint["terms"], start, end))
        now = self.now()
        text = str(text or "").strip()
        if text:
            shown.append(text)
            parts.append(text)
        self._add("me", "\n".join(shown), self._compose(now, "\n\n".join(parts)))
        if joint:                                           # 邀请发出去了: 等对方回 [Yes] / [No]
            self.cfg["joint_last"] = list(joint["terms"])
            self._joint_set({"id": f"{self._ms()}", "pid": self.pid, "state": "wait", "terms": list(joint["terms"]),
                             "start": joint["start"], "end": joint["end"]}, replace=True)
        self._sent()
        self._ask_user()
        return ""

    def _away_kind(self) -> str:
        """对方此刻按日程在专注 / 睡眠 (HELD_KINDS) 吗: 是 = 返回 kind, 否 (空闲 / 休息 / 没有日程 / 没选人设) = ""."""
        if not self.persona_name():
            return ""
        k = (self.other_status() or {}).get("kind")
        return k if k in HELD_KINDS else ""

    def _ask_user(self):
        """用户发了消息之后: 对方在专注 / 睡眠就不调用接口, 消息 (连同发送时间, 已经在每条的开头) 先存着, 记下 hold,
        对方第一次空闲 / 休息时 tick() 一次性发给它; 否则马上请求."""
        kind = self._away_kind()
        if not kind:
            self._request("user")
            return
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
