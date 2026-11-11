# -*- coding: utf-8 -*-
"""日程加载 / 段与空闲 / 规则 VIII 的续话 / 后台对象 / 主动消息判定. MECHANICS 24.4 / 24.5.

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


class SchedMixin:
    def _follow_ready(self) -> bool:
        """第二条调用要等第一条真的到了用户眼前: 还在打字延时里 (hold + due 没到) 就先等;
        被藏起来 (专注 / 睡眠中, hold 但没有 due 或已经到期) 也算显示过."""
        m = next((x for x in self.messages if x.get("id") == self._follow_msg), None)
        if m is None or not m.get("hold"):
            return True
        if m.get("late") or self.blocked():          # 对方在专注 / 用户不能用沟通: 先藏起来也算显示过
            return True
        return int(m.get("due") or 0) <= self._ms()

    def _check_follow(self) -> bool:
        """规则 VIII 的第二次调用到点了 (延时见 FOLLOW_DELAY)."""
        if not self._follow_at or self._ms() < self._follow_at:
            return False
        if not self._follow_ready():                      # 第一条还没显示出来 (打字动画): 等它出来再调用
            self._follow_at = self._ms() + 500
            return False
        self._follow_at = 0
        self._follow_msg = ""
        if self._follow_epoch != self._epoch or self._again or self.busy:
            return False
        if not self.split_on() or not uses_prefix(self.persona_name()) or self._profile_problem() or self.cut_block() or not self.leader() or self.hard_block():
            return False
        if self.unanswered():                             # 等的时候用户又说了: 由那条新消息自己触发回复
            return False
        self._trigger("followup", FOLLOW_BODY)
        return True

    def _info_of(self, name: str) -> tuple:
        """人设文件里的 (日程条目, 主动发起谈话的概率), 文件没变就用上次读的."""
        try:
            st = os.stat(os.path.join(self.personas, name))
        except OSError:
            return [], {}, None
        sig = (st.st_mtime_ns, st.st_size, self._companion_sig(name))
        hit = self._bg_seg.get(name)
        if not hit or hit[0] != sig:
            text = read_persona_full(self.personas, name)
            rt = routine.parse(read_routine(self.personas, name))
            hit = self._bg_seg[name] = (sig, [] if rt else schedule_entries(text), persona_probs(text), rt)
        if hit[3]:
            hit[3].set_shifts(shift_pairs(self._state_of(pid_of(name)).get("shift")))       # 起点平移 (1.0.402)
        return hit[1], hit[2], hit[3]

    def _bg_check(self, now, snap: dict):
        if not self.bg_on() or self._bg_busy or self._ms() - self._bg_last < BG_STAGGER * 1000:
            return
        names = {pid_of(n): n for n in list_personas(self.personas)}
        for pid in self.chat_pids():
            if pid == self.pid or pid not in names or self.is_muted(pid):
                continue
            st = self._state_of(pid)
            if int(st.get("cut_until") or 0) > self._ms():
                continue                                          # 切断了谈话的: 不会来找你
            if not any(m["role"] in ("me", "ai") for m in st["messages"]):
                continue                                          # 没聊过的 (只是切换时留下的空记录) 不会来找你
            entries, probs, rt = self._info_of(names[pid])
            ctx = {"pid": pid, "name": names[pid], "st": st, "entries": entries, "rt": rt,
                   "segs": [(e["kind"], e["start"]) for e in entries if e["kind"]], "probs": probs}
            if self._check_wait(ctx):
                return
            self._check_proactive(now, snap, ctx)
            if self._bg_busy:
                return

    def _bg_request(self, ctx, kind: str, body: str):
        """为非正在聊天的对象发一次请求 (主动发 / 追问); 回复由 tick 收进它的聊天记录, 没有第二次调用, 也没有要资料的轮次."""
        pid, st = ctx["pid"], ctx["st"]
        profile = self.profile(ctx["name"])
        system = persona_system(self.personas, ctx["name"], self.split_on())
        system += self.memory_block_of(ctx["st"])
        who = str(self.cfg.get("user_profile") or "").strip()[:MAX_PROFILE]
        if who and first_turn(st["messages"]):             # 身份背景: 只在这段对话的第一轮发一次
            system += "\n\n以下是用户的身份背景(用户自己填写):\n" + who
        api = self._compose(self.now(), oit_text(body))
        msgs = api_messages(st["messages"])
        note = self._rt_note(self.now(), ctx["rt"], pid) if ctx.get("rt") else ""
        if note and msgs and msgs[-1]["role"] == "user":
            msgs[-1]["content"] += "\n\n" + note
        if msgs and msgs[-1]["role"] == "user":
            msgs[-1]["content"] += "\n\n" + api
        else:
            msgs.append({"role": "user", "content": api})
        clear0 = int(st["clear_ts"])
        self._bg_busy = pid
        self._bg_last = self._ms()
        started = time.monotonic()
        self.log(f"REQ bg-{kind} start [{ctx['name']}] model={profile.get('model')} msgs={len(msgs)}")

        think_kw = {"think": True} if self.think_auto() and kind in THINK_KINDS else {}

        def work():
            reply, err = "", ""
            try:
                reply = self.chat_fn(profile, system, msgs, max_tokens=CHAT_TOKENS, temperature=CHAT_TEMPERATURE,
                                     timeout=CHAT_TIMEOUT, cache=True,
                                     on_usage=lambda t, c: self.log(f"USAGE bg-{kind} input={t} cached={c}"), **think_kw)
                if kind == "proactive" and is_refusal(reply) and not info_tags(reply):          # 主动发起的对话不能 [refuse to reply]: 再要求一次
                    self.log(f"PRO bg [{ctx['name']}] 回了 [refuse to reply]: 不允许, 再要求一次")
                    reply = self.chat_fn(profile, system, msgs + [{"role": "assistant", "content": reply}, {"role": "user", "content": PRO_NO_REFUSE}],
                                         max_tokens=CHAT_TOKENS, temperature=CHAT_TEMPERATURE, timeout=CHAT_TIMEOUT, cache=True,
                                         on_usage=lambda t, c: self.log(f"USAGE bg-{kind} input={t} cached={c}"), **think_kw)
            except ai_assist.AIError as e:
                err = e.message
            except Exception as e:      # noqa: BLE001
                err = type(e).__name__
            self.log(f"REQ bg-{kind} {'FAIL ' + err if err else 'ok'} {time.monotonic() - started:.1f}s")
            with self._lock:
                self._bg_results.append((pid, kind, (body, api), reply, err, clear0))
        threading.Thread(target=work, daemon=True).start()

    def _bg_finish(self, pid, kind, extra, reply, err, clear0):
        self._bg_busy = None
        if self.hard_block():
            return
        st = self._state_of(pid)
        if int(st["clear_ts"]) != clear0 or err:
            return
        text, wait_min = clean_reply(reply)
        name = next((n for n in list_personas(self.personas) if pid_of(n) == pid), "")
        if text and is_claudia(name):
            text = to_halfwidth(text)                           # Claudia: 显示前全角符号换成半角 (1.0.408)
        if is_refusal(reply) or not text or info_tags(reply):
            self.log(f"REPLY bg-{kind}: 对方不想发 / 什么也没剩, 不显示")
            return
        sysm = self._mk("sys", extra[0], extra[1])
        m = self._mk("ai", text, reply.strip() if text != reply.strip() else "", persona=name)
        if wait_min and kind != "chase":
            m["wait"] = wait_min
        st["messages"].extend([sysm, m])
        if not m.get("hold"):
            if not (self.viewing and pid == self.pid):
                self.unread += 1
            self._ping()                                    # 不在沟通页面: 新消息提示一声 (在页面上看着就不响)
        self._store(pid, st)
        self._changed()
        self._push()

    def _pro_done(self, ctx=None) -> list:
        """已经处理过的主动消息时间点 (发没发都算): "F开始时刻" (对方自己的空闲 / 休息段, I) / "S开始时刻i|r" (日程里的空闲 / 休息段, II / III) / "Z开始时刻" (睡眠段, IV)."""
        src = ctx["st"]["pro_key"] if ctx else self.cfg.get("pro_key")
        return [k for k in str(src or "").split("|") if k]

    def _pro_mark(self, key: str, ctx=None):
        keys = [k for k in self._pro_done(ctx) if k != key] + [key]
        val = "|".join(keys[-PROACTIVE_KEEP:])
        if ctx:
            ctx["st"]["pro_key"] = val
        else:
            self.cfg["pro_key"] = val

    def _pro_recent(self, now, ctx=None) -> bool:
        """最近 PROACTIVE_GAP 内已经主动发过一条了吗 (相邻的日程段 / 刚好又在休息开始后, 不连着发)."""
        for m in reversed(ctx["st"]["messages"] if ctx else self.messages):
            if m["role"] == "sys" and str(m.get("text") or "").startswith("[系统]"):
                return self._age(m) < PROACTIVE_GAP
        return False

    def _load_schedule(self) -> list:
        """读人设文件里的日程 (人设文件没变就用上次读的): 原来的 (没有联合行动调整过的) 空闲 / 休息 / 专注 / 睡眠段."""
        name = self.persona_name()
        if not name:
            self._pro_cache, self._seg_cache, self._entry_cache, self._loop_cache, self._rt = {}, [], [], [], None
            return []
        try:
            st = os.stat(os.path.join(self.personas, name))
        except OSError:
            self._pro_cache, self._seg_cache, self._entry_cache, self._loop_cache, self._rt = {}, [], [], [], None
            return []
        sig = (name, st.st_mtime_ns, st.st_size, self._companion_sig(name))
        if sig != self._seg_sig:
            self._seg_sig = sig
            text = read_persona_full(self.personas, name)
            self._rt = routine.parse(read_routine(self.personas, name))
            if self._rt:                                       # 有日程文件: 日程走 routine (按持续时间算), 旧的"每天重复的钟点"不再读
                self._entry_cache, self._loop_cache, self._seg_cache = [], [], []
            else:
                self._entry_cache = schedule_entries(text)
                self._loop_cache = schedule_loops(text)
                self._seg_cache = [(e["kind"], e["start"]) for e in self._entry_cache if e["kind"]]
            self._pro_cache = persona_probs(text)
        if self._rt:
            self._rt.set_shifts(shift_pairs(self.cfg.get("shift")))       # 起点平移 (1.0.402): 换了记录, 之前算好的日程作废
        return self._seg_cache

    def _companion_sig(self, name: str) -> tuple:
        """人设所有配套文件 (背景 / 日程 ...) 的 (文件名, 修改时间, 大小): 任何一个变了都要重新读."""
        out = []
        for cn in companions_of(self.personas, name):
            try:
                st = os.stat(os.path.join(self.personas, cn))
            except OSError:
                continue
            out.append((cn, st.st_mtime_ns, st.st_size))
        return tuple(out)

    def _segments(self) -> list:
        """现在这个人设日程里的空闲 / 休息 / 专注 / 睡眠段 (联合行动之后循环被重新计算时, 是重新计算后的)."""
        base = self._load_schedule()
        shifted = self._joint_shift()
        return [(e["kind"], e["start"]) for e in shifted if e["kind"]] if shifted is not None else base

    def _entries(self) -> list:
        """现在这个人设的完整日程 (带结束时刻 / 修饰): 显示对方的状态用 (联合行动之后循环被重新计算时, 是重新计算后的)."""
        self._load_schedule()
        shifted = self._joint_shift()
        return shifted if shifted is not None else self._entry_cache

    def reload_schedule(self, why: str = ""):
        """重新读人设里的日程, 主动发消息的时刻按它调整: 打开 OIT / 开始新对话 (清空) / 换人设时调用; 人设文件改了也会在 _segments 里自己重读."""
        self._seg_sig = None
        segs = self._segments()
        self.log(f"SCHEDULE {why}: 空闲 {sum(k == 'idle' for k, _m in segs)} 段, 休息 {sum(k == 'rest' for k, _m in segs)} 段"
                 f", 专注 {sum(k == 'focus' for k, _m in segs)} 段, 睡眠 {sum(k == 'sleep' for k, _m in segs)} 段")

    def _schedule_events(self, now, segs=None) -> list:
        """日程里 3 分钟之内就要开始的空闲 / 休息段: [(时间点, "idle" | "rest", 开始的时刻)]. segs: 别的对象的日程段 (默认是现在聊的)."""
        out = []
        for kind, mins in (self._segments() if segs is None else segs):
            if kind not in ("idle", "rest"):                  # 专注 / 睡眠段只用来显示状态, 不主动发消息
                continue
            for d in (0, 1):                                  # 今天 / 明天 (23:59 看 0:00 开始的那段)
                start = datetime.combine(now.date() + timedelta(days=d), datetime.min.time()) + timedelta(minutes=mins)
                if now < start <= now + timedelta(seconds=PROACTIVE_LEAD[1]):
                    out.append((f"S{start:%Y-%m-%dT%H:%M}{kind[0]}", kind, start))
        return out

    def _cur_free(self, now, entries=None):
        """对方现在正处在自己日程的哪一段空闲 / 休息里, 而且这段至少还剩 PROACTIVE_FREE_LEFT 秒: (开始时刻, 剩余秒数); 否则 None.

        entries: 别的对象的日程条目 (默认是现在聊的); 午饭 / 晚饭这种别的名字也算空闲段 (_seg_kind 里归成 idle).
        """
        t = now.hour * 60 + now.minute
        best = None                                                # (开始, 结束): 重叠时取最晚开始的那段
        for e in (entries if entries is not None else self._entries()):
            if e["kind"] not in ("idle", "rest"):
                continue
            for shift in (0, -24 * 60):                            # 今天 / 昨天开始的跨零点段
                start, end = e["start"] + shift, e["end"] + shift
                if start <= t < end and (best is None or start > best[0]):
                    best = (start, end)
        if not best:
            return None
        left = (best[1] - t) * 60 - now.second - now.microsecond / 1e6
        if left < PROACTIVE_FREE_LEFT:
            return None
        base = datetime.combine(now.date(), datetime.min.time())
        return base + timedelta(minutes=best[0]), left
