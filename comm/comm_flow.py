# -*- coding: utf-8 -*-
"""切断 / 限时网络 / 记忆卡 / 请求的发出 (线程). MECHANICS 24.7 / 24.8 / 24.11.

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


class FlowMixin:
    def cut_left(self) -> float:
        """对方还要切断多少秒 (0 = 没有切断)."""
        return max(0.0, (int(self.cfg.get("cut_until") or 0) - self._ms()) / 1000.0)

    def cut_block(self) -> str:
        """对方切断了谈话的原因 ("" = 没有): 用户不能发消息, 对方也不会主动发 / 重试 / 追问. 开发者模式下不拦."""
        left = self.cut_left()
        if left <= 0 or self.cut_exempt():
            return ""
        until = datetime.fromtimestamp(int(self.cfg.get("cut_until") or 0) / 1000)
        return tr("{name} 切断了与你的谈话, 还有 {n} 分钟 (到 {t})", name=self.share_ai_name(), n=max(1, round(left / 60)), t=f"{until:%H:%M}")

    def _cut_apply(self, minutes: float):
        """主线程: 对方回了 [cut off][N]: 记下切断到什么时候 (再次切断取更晚的), 并记一条给对方看的系统消息. 清空聊天不会解除切断."""
        until = self._ms() + int(float(minutes) * 60000)
        self.cfg["cut_until"] = max(int(self.cfg.get("cut_until") or 0), until)
        self._follow_at = 0
        self._follow_msg = ""
        self._again = False
        end = datetime.fromtimestamp(int(self.cfg["cut_until"]) / 1000)
        body = f"{CUT_TAG} 你在 {self.now():%H:%M} 切断了与用户的谈话 {minutes:g} 分钟, 到 {end:%H:%M} 才会恢复; 这段时间用户发不了消息, 你也不会被调用."
        self._add("sys", body, self._compose(self.now(), oit_text(body)))
        self.save_chat()
        self._changed()
        self._push()

    def _net(self) -> dict:
        d = self.cfg.get("net")
        return d if isinstance(d, dict) else {}

    def _net_set(self, d: dict):
        self.cfg["net"] = d
        self.save_cfg()
        self._changed()
        self._push()

    def net_phase(self) -> str:
        """限时网络现在的阶段: "wait" (申请了, 在等 NET_DELAY) / "on" (网络开着) / "cool" (关了, 冷却期) / "" (没有)."""
        n = self._net()
        st, now = n.get("st"), self._ms()
        if st == "wait":
            return "wait" if now < int(n.get("due") or 0) + 30 * 60000 else ""      # 程序关着错过了很久: 作废
        if st == "on" and now < int(n.get("until") or 0):
            return "on"
        if st in ("on", "cool"):
            return "cool" if now < int(n.get("cool") or 0) else ""
        return ""

    def net_active(self) -> bool:
        """网络开着吗 (开着时发给模型的请求带上联网搜索)."""
        return self.net_phase() == "on"

    def _net_apply(self, minutes: float) -> bool:
        """对方申请限时网络. 申请中 / 冷却期里的申请无效; 已经在开着的时候: 不用再等, 也不延长,
        但会马上再搜一次 (见 _check_net 的 "again")."""
        ph = self.net_phase()
        if ph == "on":
            n = dict(self._net())
            n["again"] = self._ms()
            self._net_set(n)
            self.log(f"NET 申请 {minutes:g} 分钟: 网络已经开着, 直接再搜一次 (不延长)")
            return False
        if ph:
            self.log(f"NET 申请 {minutes:g} 分钟: 无效 ({ph})")
            return False
        ms = self._ms()
        self._net_set({"ts": ms, "pid": self.pid, "n": float(minutes), "due": ms + NET_DELAY * 1000, "st": "wait", "tries": 0})
        self.log(f"NET 申请 {minutes:g} 分钟: {NET_DELAY} 秒后搜索")
        return True

    def _net_failed(self):
        """搜索那次请求失败了: 隔 NET_RETRY 秒再试, 最多 NET_TRIES 次; 还不行就放弃 (进入冷却)."""
        n = dict(self._net())
        if n.get("st") != "on":
            return
        ms = self._ms()
        tries = int(n.get("tries") or 0) + 1
        if tries <= NET_TRIES:
            n.update(st="wait", due=ms + NET_RETRY * 1000, tries=tries, ts=max(ms, int(n.get("ts") or 0) + 1))
            self.log(f"NET 搜索请求失败: {NET_RETRY} 秒后再试 ({tries}/{NET_TRIES})")
        else:
            n.update(st="cool", cool=ms + NET_COOLDOWN * 1000, ts=max(ms, int(n.get("ts") or 0) + 1))
            self.log("NET 搜索请求失败: 放弃, 进入冷却期")
        self._net_set(n)

    def _check_net(self) -> bool:
        """申请网络之后等够了: 开通网络, 再调用一次接口让对方搜索并回复.
        开着的时候又申请了 (again): 不重新开通, 但马上让它再搜一次."""
        n = self._net()
        if n.get("again"):
            n = dict(n)
            n.pop("again", None)
            n["ts"] = max(self._ms(), int(n.get("ts") or 0) + 1)
            self._net_set(n)
            if self.net_phase() != "on":
                return False                                          # 等的时候网络已经关了: 作废
            end = datetime.fromtimestamp(int(n.get("until") or 0) / 1000)
            self.log(f"NET 再次申请: 网络还开着 (到 {end:%H:%M}), 直接搜索, 不延长")
            self._trigger("net_again", f"{NET_TAG} 网络已经开着 (到 {end:%H:%M}), 不用再申请, 也不用等. 请现在搜索你要查的内容并回复用户 "
                                       f"(话题接着上面). 这条必须发出, 不要返回{REFUSE}. 网络只开到 {end:%H:%M}, 不会延长.")
            return True
        if n.get("st") != "wait":
            return False
        ms = self._ms()
        if n.get("pid") != self.pid or int(n.get("ts") or 0) <= int(self.cfg.get("clear_ts") or 0):
            self._net_set({"ts": max(ms, int(n.get("ts") or 0) + 1)})                # 换了聊天对象 / 清空了聊天: 作废
            return False
        if ms < int(n.get("due") or 0):
            return False
        if self.net_phase() != "wait" or self.hard_block():
            return False
        minutes = float(n.get("n") or NET_DEFAULT_MIN)
        until = ms + int(minutes * 60000)
        self._net_set(dict(n, st="on", until=until, cool=until + NET_COOLDOWN * 1000, ts=max(ms, int(n.get("ts") or 0) + 1)))
        end = datetime.fromtimestamp(until / 1000)
        self.log(f"NET 开通 {minutes:g} 分钟, 到 {end:%H:%M}")
        self._trigger("net", f"{NET_TAG} 网络已开通 {minutes:g} 分钟 (到 {end:%H:%M}). 你现在可以联网搜索: 请搜索你刚才想查的内容, 然后回复用户 "
                             f"(话题接着用户上面说的). 这条必须发出, 不要返回{REFUSE}. 网络开通期间用户再说话你仍然可以搜索.")
        return True

    def _trigger(self, kind: str, body: str):
        """系统要求对方主动发 / 重试: 这条系统消息先不进记录, 对方真的回复了才和回复一起记下 (被拒绝 / 出错就当没发过)."""
        api = self._compose(self.now(), oit_text(body))
        self._request(kind, (body, api))

    def memory_block_of(self, st: dict) -> str:
        """长期记忆: 模型整理的摘要 (可能不精确), 附在 system 里."""
        st = st or {}
        auto = str(st.get("mem_auto") or "").strip()[:MEM_MAX]
        rel = love_line(st)                                  # 亲密关系由 OIT 记录, 总是写在最前面 (不管模型有没有整理过)
        shf = shift_line(st)                                 # 起点平移 (1.0.402) 同理
        fixed = "\n".join(tr("- 【系统记录】双方互相发送的信息总数已达到 {count} 条, 此消息数量条件已满足.", count=n)
                          for n in mem_fields(st)["mem_fixed"])
        if not auto and not rel and not shf and not fixed:
            return ""
        return "\n\n以下是关于用户的长期记忆(你之前整理的摘要, 可能不精确; 标着【系统记录】的是 OIT 记下的事实, 以它为准):\n" + "\n".join(x for x in (rel, shf, fixed, auto) if x)

    def memory_block(self) -> str:
        return self.memory_block_of(self._active_state())

    def _mem_delta(self) -> list:
        """上次整理之后的新消息 (对方拒绝 / 系统消息不算)."""
        upto = int(self.cfg.get("mem_upto") or 0)
        return [m for m in self.messages
                if m.get("role") in ("me", "ai") and int(m.get("ts") or 0) > upto]

    def _mem_due(self) -> bool:
        if self._mem_busy or self.busy or self._bg_busy:
            return False
        if self.hard_block() or self.blocked() or self._ai_problem() or not self.leader():
            return False
        if self._ms() < int(self._mem_next or 0):
            return False
        return len(self._mem_delta()) >= MEM_EVERY

    def _mem_kick(self) -> bool:
        """到点了就在后台整理一次记忆卡 (不挡聊天); 只把上次整理之后的新消息 + 现有记忆卡交给模型."""
        if not self._mem_due():
            return False
        batch = self._mem_delta()[:MEM_EVERY]
        memory = self.memory_block().strip() or "(空)"
        lines = []
        for m in batch:
            who = "用户" if m.get("role") == "me" else "对方"
            when = stamp(datetime.fromtimestamp(int(m.get("ts") or 0) / 1000))
            lines.append(f"[{when}] {who}: {clip_message(str(m.get('api') or m.get('text') or ''), 1500)}")
        persona = clip_message(read_persona_full(self.personas, self.persona_name()), 8000)
        body = (f"当前时间: {stamp(self.now())}\n\n对方的人设 (只用来判断哪些内容该长期保留 / 不能清除, 不要抄进记忆卡):\n{persona}\n\n现有记忆卡:\n" + memory + "\n\n最近一段对话 (每条前面是发送时间):\n" + "\n".join(lines))
        upto = int(batch[-1].get("ts") or 0)
        pid = self.pid
        profile = self.profile()
        self._mem_busy = True
        started = time.monotonic()
        self.log(f"MEM start n={len(batch)}")

        def work():
            reply, err = "", ""
            try:
                reply = self.chat_fn(profile, MEM_PROMPT, [{"role": "user", "content": body}],
                                     max_tokens=MEM_TOKENS, temperature=0.2, timeout=CHAT_TIMEOUT, cache=False, think=True,
                                     on_usage=lambda t, c: self.log(f"USAGE mem input={t} cached={c}"))
            except ai_assist.AIError as e:
                err = e.message
            except Exception as e:      # noqa: BLE001
                err = type(e).__name__
            self.log(f"MEM {'FAIL ' + err if err else 'ok'} {time.monotonic() - started:.1f}s chars={len(reply)}")
            with self._lock:
                self._mem_results.append((reply, err, upto, pid))

        threading.Thread(target=work, daemon=True).start()
        return True

    def _mem_finish(self, reply: str, err: str, upto: int, pid: str):
        self._mem_busy = False
        if pid != self.pid:                                        # 整理期间换了聊天对象: 这份不知道是谁的, 丢掉
            return
        if err or not reply:
            self._mem_next = self._ms() + 30 * 60 * 1000           # 失败: 半小时后再试, 不轰炸
            return
        if int(self.cfg.get("clear_ts") or 0) > int(upto):         # 整理的时候清空过: 这份作废
            self._mem_next = self._ms() + MEM_GAP * 1000
            return
        text = sanitize_mem(reply)
        if not text:
            self._mem_next = self._ms() + 30 * 60 * 1000
            return
        self.cfg["mem_auto"] = text
        self.cfg["mem_upto"] = max(int(self.cfg.get("mem_upto") or 0), int(upto))
        self.cfg["mem_ts"] = self._ms()
        self._mem_next = self._ms() + MEM_GAP * 1000
        self.save_chat()
        self._changed()
        self._push()

    def _request(self, kind: str, extra=None):
        if self.busy:
            self._again = True
            return
        self._follow_at = 0                                # 有新请求了: 排着的第二次调用取消 (用户又说了 / 主动发 / 重试)
        self._follow_msg = ""
        self._req_ts = max([int(m.get("ts") or 0) for m in self.messages if m.get("role") == "me"] or [0])
        profile = self.profile()
        system = persona_system(self.personas, self.persona_name(), self.split_on())
        dynamic = self.memory_block() + self.joint_system_note()
        who = str(self.cfg.get("user_profile") or "").strip()[:MAX_PROFILE]
        if who and first_turn(self.messages):              # 身份背景: 只在这段对话的第一轮发一次
            dynamic += "\n\n以下是用户的身份背景(用户自己填写):\n" + who
        msgs = api_messages(self.messages)
        note = self._rt_note(self.now()) if self._rt else ""             # 有日程文件的人设: 此刻的状态由 OIT 算 (只贴在最后一条用户消息后面, 不写进记录)
        if note and msgs and msgs[-1]["role"] == "user":
            msgs[-1]["content"] += "\n\n" + note
        system, msgs = cache_layout(profile, system, dynamic, msgs)     # Gemini: 动态块贴到最后一条用户消息 (system 要逐字节不变)
        if extra:
            if msgs and msgs[-1]["role"] == "user":
                msgs[-1]["content"] += "\n\n" + extra[1]
            else:
                msgs.append({"role": "user", "content": extra[1]})
        epoch = self._epoch
        self.busy = True
        self.busy_since = time.monotonic()
        started = time.monotonic()
        self.log(f"REQ {kind} start model={profile.get('model')} host={urlsplit_host(profile.get('base_url'))} "
                 f"msgs={len(msgs)} chars={sum(len(m['content']) for m in msgs) + len(system)}")
        self._changed()

        def used(tokens, cached):
            self.log(f"USAGE {kind} input={tokens} cached={cached}")

        why = self.think_reason(kind)
        think_kw = {"think": True} if why else {}           # 只在开的时候带这个参数 (别的 chat_fn 不用认识它)
        if why:
            self.log(f"THINK {kind} on: {why}")

        if self.net_active():                               # 限时网络开着: 请求带上联网搜索 (只在开的时候带这个参数)
            think_kw["web"] = True
            self.log(f"NET {kind} 带联网搜索 (还剩 {max(0, int(self._net().get('until') or 0) - self._ms()) // 1000} 秒)")

        def ask(m):
            return self.chat_fn(profile, system, m, max_tokens=CHAT_TOKENS, temperature=CHAT_TEMPERATURE,
                                timeout=CHAT_TIMEOUT, cache=True, on_usage=used, **think_kw)

        def work():
            try:
                convo = list(msgs)
                reply = ask(convo)
                op = None
                if _OP_RE.search(reply):                                  # XII: 主动发起 (规则 I - IV) 或用户邀请 / 要求时才能发起联合行动
                    if kind in OP_KINDS:
                        op, reply = self._operation_rounds(ask, convo, reply, bool(extra))
                    else:                                                 # 追问 / 回应接受拒绝时写了: 告诉对方不行, 让它重新回复 (不然去掉 [] 后常常什么也不剩, 这条被丢掉, 对方还以为发出去了)
                        reply = self._operation_refused(ask, convo, reply, kind, bool(extra))
                # 对方回 [refuse to reply][contract] = 要词条库: 把资料给它, 再调用一次让它回复用户 (资料不写进记录)
                for _ in range(MAX_INFO_ROUNDS):
                    tags = info_tags(reply)
                    blocks, own, cond = [], [], []
                    for t in tags:
                        if t == COND_TAG:
                            try:
                                cond.append(self.condition_text())
                            except Exception as e:      # noqa: BLE001
                                self.log(f"COND FAIL {type(e).__name__}: {e}")
                            continue
                        if t == MSGS_TAG:
                            cond.append(self.messages_text())
                            continue
                        if t == STATE_TAG:
                            try:
                                cond.append(self.state_text())
                            except Exception as e:      # noqa: BLE001
                                self.log(f"STATE FAIL {type(e).__name__}: {e}")
                            continue
                        try:
                            ob = self.routine_info(t)                       # 有日程文件的人设: 对方自己的 OIT 数据 (OIT 按日程实时算出)
                            b = ob or str(self.info_fn(t) or "")
                        except Exception:       # noqa: BLE001
                            ob, b = "", ""
                        if b:
                            (own if ob else blocks).append(f"【{t}】\n{b}")
                    if not blocks and not own and not cond:
                        break
                    text = "\n\n".join(cond)
                    if own:
                        text += ("\n\n" if text else "") + ("(OIT 系统提供, 用户看不到) 以下是你自己的 OIT 数据 (OIT 按你的日程实时算出, 只有已经发生的部分, 之后的还没有):\n\n"
                                 + "\n\n".join(own) + "\n\n请据此回答, 这是你自己的数据, 不是用户的; 不要编造里面没有的内容.")
                    if blocks:
                        text += (("\n\n" if text else "(OIT 系统提供的参考模板, 用户看不到)\n\n") + ("另外这些只是参考模板:\n\n" if own else "")
                                 + "\n\n".join(blocks)
                                 + "\n\n以上模板只是**参考模板** (格式; <尖括号> 里是占位符), 不是用户的真实数据. 请参照这个格式和写法, 根据人设自己填写具体内容,"
                                   " 不要把它说成是用户的数据.")
                    text += " 回复用户上面的消息. 如果此刻仍然不方便回复, 返回" + REFUSE + "."
                    convo = convo + [{"role": "assistant", "content": reply}, {"role": "user", "content": oit_text(text)}]
                    reply = ask(convo)
                if kind in ("proactive", "net", "net_again") and is_refusal(reply) and not info_tags(reply):       # 主动发起的对话 / 搜索那次请求不能 [refuse to reply]: 再要求一次 (告白可以: 对方判断不合适就是不告白)
                    self.log(f"PRO {kind} 回了 [refuse to reply]: 不允许, 再要求一次")
                    convo = convo + [{"role": "assistant", "content": reply},
                                     {"role": "user", "content": oit_text(NET_NO_REFUSE if kind == "net" else PRO_NO_REFUSE)}]
                    reply = ask(convo)
                res = (kind, extra, reply, "", epoch, op)
                self.log(f"REQ {kind} ok {time.monotonic() - started:.1f}s reply_chars={len(reply)}")
            except ai_assist.AIError as e:
                res = (kind, extra, "", e.message, epoch)
                self.log(f"REQ {kind} FAIL {time.monotonic() - started:.1f}s {e.kind}: {e.message}"
                         + (f" (系统代理 {ai_assist.proxy_hint()})" if e.kind == "network" else ""))
            except Exception as e:      # noqa: BLE001
                res = (kind, extra, "", f"{type(e).__name__}", epoch)
                self.log(f"REQ {kind} FAIL {time.monotonic() - started:.1f}s {type(e).__name__}: {e}")
            with self._lock:
                self._results.append(res)
        threading.Thread(target=work, daemon=True).start()
