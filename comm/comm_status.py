# -*- coding: utf-8 -*-
"""对方状态 / 忙碌与静音 / 共时闹钟 / 思考 / 主动谈话语言 / 日程跟随. MECHANICS 24.1 / 24.17.

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


class StatusMixin:
    def bg_on(self) -> bool:
        """允许非正在聊天的人 (聊过天的其他对象) 发起对话吗. 默认开; 随设备同步."""
        return self.cfg.get("bg_pro") is not False

    def is_muted(self, pid: str = None) -> bool:
        """这个对象 (默认是现在聊的) 被"禁止主动发起"了吗: 不主动发消息, 也不追问 (规则 IX)."""
        return (pid if pid is not None else self.pid) in (self.cfg.get("mute_pro") or [])

    def set_muted(self, muted: bool, pid: str = None):
        pid = pid if pid is not None else self.pid
        cur = [p for p in (self.cfg.get("mute_pro") or []) if p != pid]
        self.set_cfg(mute_pro=cur + [pid] if muted and pid else cur)

    def co_alarm(self) -> dict:
        """{pid, devs, ts}; 没有开 = {}."""
        c = self.cfg.get("alarm_co")
        return dict(c) if isinstance(c, dict) and c.get("pid") else {}

    def co_alarm_pid(self) -> str:
        return str(self.co_alarm().get("pid") or "")

    def co_alarm_devs(self) -> list:
        return [str(x) for x in (self.co_alarm().get("devs") or [])]

    def _co_set(self, pid: str, devs: list):
        old = int((self.cfg.get("alarm_co") or {}).get("ts") or 0)
        self.cfg["alarm_co"] = {"pid": pid, "devs": list(devs), "ts": max(self._ms(), old + 1)}
        self.save_cfg()
        self._changed()
        self._push()

    def set_co_alarm(self, pid: str, devs: list = None):
        """把 pid 设为共时闹钟 (一次只有一个对象; pid 为空 = 关闭). 响铃的设备没指定时沿用原来的, 都没有就是本机."""
        kept = [str(x) for x in ((self.cfg.get("alarm_co") or {}).get("devs") or [])]        # 关掉之后也留着, 再打开时沿用
        if not pid:
            self._co_set("", kept)
            return
        if devs is None:
            devs = kept or ([self.device()] if self.device() else [])
        self._co_set(pid, devs)

    def set_co_alarm_devs(self, devs: list):
        """手动设置哪些设备响铃 (只在共时闹钟开着时有意义)."""
        if self.co_alarm_pid():
            self._co_set(self.co_alarm_pid(), devs)

    def co_alarm_name(self) -> str:
        """共时闹钟对象的名字 (人设文件已经删了 = "")."""
        pid = self.co_alarm_pid()
        for n in list_personas(self.personas):
            if pid_of(n) == pid:
                return persona_title(n)
        return ""

    def co_alarm_fn(self):
        """本机要按共时闹钟响铃时, 返回 f(after) → after 之后对方的下一次起床时刻 (算不出来 = None); 否则 None."""
        pid = self.co_alarm_pid()
        me = self.device()
        if not pid or not me or me not in self.co_alarm_devs():
            return None
        return lambda after: self.wake_after(pid, after)

    def wake_after(self, pid: str, after):
        """对象 pid 在 after 之后的下一次实际起床时刻: 有日程文件的是每天算出来的起床时刻 (睡眠段的结束); 旧式固定钟点日程的是睡眠段的结束时刻."""
        name = next((n for n in list_personas(self.personas) if pid_of(n) == pid), "")
        if not name:
            return None
        entries, _probs, rt = self._info_of(name)
        if rt:
            return next((w for w in routine.wake_times(rt, after, after + timedelta(days=3), pid) if w > after), None)
        best = None
        for e in entries:
            if e.get("kind") != "sleep":
                continue
            for k in range(3):
                t = datetime.combine(after.date(), datetime.min.time()) + timedelta(days=k, minutes=int(e["end"]) % 1440)
                if t > after:
                    best = t if best is None or t < best else best
                    break
        return best

    def think_auto(self) -> bool:
        """自动开思考 (设置 → 沟通 → 思考) 开着吗: 默认开. 开着时, 规则判断 (think_reason) 需要的那几次请求开思考; 关掉就一直不开."""
        return self.cfg.get("think_auto") is not False

    def think_reason(self, kind: str) -> str:
        """这一次请求要不要开思考 (第一层: 只看规则, 不额外调用 API): 返回原因, "" = 不开.
        - 系统触发的 (THINK_KINDS): 开;
        - 用户说的话 (user / retry): 看还没回复的那几条: 关键词 / 时刻 / 英文 / 很长 (think_text_reason);
          或者对方此刻按日程该在专注 / 睡眠, 用户这时开始一段新的谈话 (要判断回不回, THINK_NEW_AFTER)."""
        if not self.think_auto():
            return ""
        if kind in THINK_KINDS:
            return f"系统触发 {kind}"
        if kind not in ("user", "retry"):
            return ""
        run, last_ai = [], 0
        for m in reversed(self.messages):
            if m.get("role") == "ai":
                last_ai = int(m.get("ts") or 0)
                break
            if m.get("role") == "me":
                run.append(str(m.get("text") or ""))
        why = think_text_reason("\n".join(reversed(run)))
        if why:
            return why
        st = self.other_status() or {}
        if st.get("kind") in ("focus", "sleep") and (last_ai == 0 or self._ms() - last_ai > THINK_NEW_AFTER * 1000):
            return "日程里" + STATUS_TEXT[st["kind"]] + ", 新的谈话"
        return ""

    def split_on(self) -> bool:
        """分条发 (规则 VIII) 开着吗: 用户说完话, 对方的第一条收到之后再调用一次 API, 让对方可以把话分开说. 默认开."""
        return self.cfg.get("split_send") is not False

    def pro_lang(self) -> str:
        """设置里的主动谈话语言 ("" = 不指定): 只管对方主动发起的那一句 (规则 I - IV 触发的), 其他对话不限制 (1.0.407)."""
        return str(self.cfg.get("pro_lang") or "").strip()[:MAX_PRO_LANG]

    def pro_percent(self, kind: str) -> float:
        """主动发消息的概率 (%): kind = "idle" 人设的空闲段前 / "rest" 人设的休息段前 / "sleep" 人设的睡眠段开始后. 写在人设里 (persona_probs), 没写用默认."""
        self._segments()
        return pro_pct(self._pro_cache, kind)

    def _ping(self):
        """有新消息, 而用户不在沟通页面: 柔和的提示音 (在页面上看着就不用响; 不能用沟通的时候也不响)."""
        if self.viewing:
            return
        if self.on_proactive and not self.blocked():
            try:
                self.on_proactive()
            except Exception:       # noqa: BLE001
                pass

    def last_author(self) -> str:
        """最晚的一条消息是哪台设备写的 (设备 id 的前 4 位; 旧记录 / 没有记录 = ""): 这台设备是聊天的主端 —— 调用 AI, 主动发, 重试都由它做,
        别的设备从它同步. 谁最后发了消息 (用户在那台上发的, 或者那台上 AI 回复的), 谁就是主端."""
        if not self.messages:
            return ""
        last = max(self.messages, key=lambda m: (int(m["ts"]), m["uid"]))
        return last["uid"].rpartition("-")[2] if "-" in last["uid"] else ""

    def problem(self) -> str:
        """现在不能聊天的原因 ("" = 可以)."""
        why = self.blocked()
        if why:
            return why
        why = self.cut_block()
        if why:
            return why
        return self._profile_problem()

    def _profile_problem(self) -> str:
        """API / 人设没配置好的原因 ("" = 可以)."""
        try:
            ai_assist.check_profile(self.profile())
        except ai_assist.AIError as e:
            return e.message
        if not self.persona_name():
            return "还没有选人设"
        return ""

    def _ai_problem(self) -> str:
        """对方现在能不能主动发消息 / 重试 ("" = 可以). 和 problem 的区别: 用户睡眠时对方仍可以发, 只是消息先藏起来、不通知."""
        why = self.ai_blocked()
        if why:
            return why
        why = self.cut_block()
        if why:
            return why
        return self._profile_problem()

    def other_status(self, now=None) -> dict:
        """对方现在在日程里的哪一段: {"kind", "label", "detail", "begin", "end"}; 没有日程 / 不在任何一段 = {}."""
        now = now or self.now()
        joint = self._joint_status(now)                       # 联合行动的同步阶段: 跟着用户走, 不看日程
        if joint:
            return joint
        if self._rt:                                          # 有日程文件: 按当天算出的日程 (带波动)
            self._load_schedule()
            s = routine.status_in(self._rt_segs(now - timedelta(hours=1), now + timedelta(hours=1)), now)
            return {"kind": s["kind"], "label": s["label"], "detail": "", "begin": s["start"], "end": s["end"]} if s else {}
        entries = self._entries()
        if not entries:
            return {}
        t = now.hour * 60 + now.minute
        best = None                                           # (开始时刻, 结束时刻, 条目): 重叠时取最晚开始的那段
        for e in entries:
            for shift in (0, -24 * 60):                       # 今天 / 昨天开始的跨零点段
                start, end = e["start"] + shift, e["end"] + shift
                if start <= t < end and (best is None or start > best[0]):
                    best = (start, end, e)
        if best is None:
            return {}
        base = datetime.combine(now.date(), datetime.min.time())
        return {"kind": best[2]["kind"], "label": best[2]["label"], "detail": best[2]["detail"],
                "begin": base + timedelta(minutes=best[0]), "end": base + timedelta(minutes=best[1])}

    def other_status_display(self, now=None) -> tuple:
        """聊天页顶上那一行: (状态文字, kind). 只显示 专注中 / 睡眠中 / 休息中 / 空闲 四种状态;
        别的名字 / 不在任何一段里 / 没有日程, 一律按空闲显示; 没有选人设 = ("", "")."""
        if not self.persona_name():
            return "", ""
        s = self.other_status(now)
        kind = s["kind"] if s and s["kind"] in STATUS_TEXT else "idle"
        return tr(STATUS_TEXT[kind]) + ((" · " + tr("联合行动")) if s and s.get("joint") else ""), kind

    def ai_free_at(self, now=None):
        """对方此刻在专注 (按人设日程) 时: 下一个空闲 / 休息开始的时刻 (中间接着的睡眠也跳过); 不在专注 / 联合行动中 / 找不到 = None.
        对方在专注期间发出的消息不马上显示, 推迟到这个时刻再发 (MECHANICS 24.3.9)."""
        now = now or self.now()
        if not self.persona_name():
            return None
        s = self.other_status(now)
        if not s or s.get("kind") != "focus" or s.get("joint"):
            return None
        t = now
        for _ in range(24):
            s = self.other_status(t)
            if not s or s.get("kind") not in ("focus", "sleep"):
                return t if t > now and t - now <= timedelta(hours=24) else None
            end = s.get("end")
            if not end or end <= t:
                return None
            t = end
        return None

    def other_status_text(self, now=None) -> str:
        """聊天页顶上那一行 (没有前缀 / 修饰 / 结束时间, 前面加什么颜色的小圆点看 kind)."""
        return self.other_status_display(now)[0]

    def sf_on(self) -> bool:
        """设置里的日程跟随开关 (默认关, 随设备同步)."""
        return bool(self.cfg.get("sched_follow"))

    def sf_live(self) -> bool:
        """日程跟随现在真的在看着对方吗: 开关开着, 并且没有合约 (用户自己的合约, 或联合行动里对方跟着用户的同步阶段). 合约期间自动关闭, 结束后再开启."""
        return self.sf_on() and not self.contract_fn() and not self.joint_live()

    def sf_text(self, kind: str) -> str:
        """提示文字 (指代对方用 {name} = 人设名)."""
        return tr(SF_TEXT.get(kind, SF_TEXT["idle"]), name=self.share_ai_name())

    def _status_kind(self) -> str:
        s = self.other_status()
        return s["kind"] if s and s["kind"] in STATUS_TEXT else "idle"

    def _sf_tick(self):
        """每秒: 日程跟随开着 (并且没有合约) 时看对方的状态; 变了就在焦点设备询问 (用户的计时器已经是同一种状态 / 外出中不问).
        开关关着 / 合约中 / 没选人设 / 换了聊天对象 = 重新开始看: 先记下对方现在的状态, 不提示 (合约里对方被改的状态也不算)."""
        if not self.persona_name() or not self.sf_live():
            self._sf_last = None
            return
        key = (self.pid, self._status_kind())
        prev, self._sf_last = self._sf_last, key
        if prev is None or prev == key or prev[0] != key[0]:
            return
        if not self.on_sf or not self.focus_device() or self.state_fn() in (SF_STATE[key[1]], "out"):
            return
        self.log(f"FOLLOW 对方 {STATUS_TEXT[prev[1]]} → {STATUS_TEXT[key[1]]}: 问用户跟不跟随")
        try:
            self.on_sf(key[1], self.sf_text(key[1]))
        except Exception:       # noqa: BLE001
            pass
