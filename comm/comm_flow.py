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
from comm.comm_prompt import REFUSE, SHIFT_TAG, MSGS_TAG, THINK_LONG, THINK_WORDS, THINK_TIME_RE, think_text_reason, mental_text_reason, JOINT_RULE, rest_function_text, prefix_prompt, native_persona_text
from comm.comm_persona import SHIFT_LO, SHIFT_HI, SHIFT_KEEP, MAX_PERSONA, MEM_MAX, PERSONA_EXT, readme_text, pid_of, love_norm, love_line, shift_norm, shift_pairs, hm_text, shift_line, mem_fields, seed_builtin, ensure_personas, list_personas, persona_title, uses_prefix, system_head, PERSONA_HEAD, persona_system, persona_labels, read_persona, ROUTINE_SUFFIX, COMPANION_SEP, companion_base, is_companion_name, companion_files, companions_of, routine_name_of, read_routine, read_persona_full, import_persona
from comm.comm_util import (OIT_TAG, COND_TAG, WEEKDAY, MAX_MSG_CHARS, HISTORY_SENT, HISTORY_STEP, HISTORY_CHARS,
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

    def _net_apply(self, minutes: float, immediate: bool = False) -> bool:
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
        delay = 0 if immediate else NET_DELAY
        self._net_set({"ts": ms, "pid": self.pid, "n": float(minutes), "due": ms + delay * 1000, "st": "wait", "tries": 0})
        self.log(f"NET 申请 {minutes:g} 分钟: {'立即' if immediate else f'{NET_DELAY} 秒后'}搜索")
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

    def memory_block_of(self, st: dict, include_live=True, pid: str = "") -> str:
        """长期记忆: 模型整理的摘要 (可能不精确), 附在 system 里."""
        from comm.comm_memory import archive_summary_line
        st = st or {}
        auto = str(st.get("mem_auto") or "").strip()[:MEM_MAX]
        live = str(st.get("mem_live") or "").strip()[:MEM_MAX] if include_live else ""
        rel = love_line(st)                                  # 亲密关系由 OIT 记录, 总是写在最前面 (不管模型有没有整理过)
        from comm.comm_persona import sleep_delay_line
        sleep_delay = sleep_delay_line(st)
        shf = shift_line(st)                                 # 起点平移 (1.0.402) 同理
        from .comm_offenses import offenses_line
        offenses = offenses_line(st.get("interaction"))
        fixed = "\n".join(tr("- 【系统记录】双方互相发送的信息总数已达到 {count} 条, 此消息数量条件已满足.", count=n)
                          for n in mem_fields(st)["mem_fixed"])
        target_pid = pid or (self.pid if st is self._active_state() or not pid else "")
        arch = archive_summary_line(self.dir, target_pid) if (include_live and target_pid) else ""
        if not auto and not live and not rel and not shf and not sleep_delay and not fixed and not offenses and not arch:
            return ""
        return "\n\n以下是关于用户的长期记忆(自动整理摘要可能不精确；实时事件由你通过记忆工具维护，条目带 [#E*] 编号可直接按编号修改或移除；标着【系统记录】的是 OIT 记下的事实, 以它为准):\n" + "\n".join(x for x in (rel, shf, sleep_delay, fixed, offenses, auto, live, arch) if x)

    def memory_block(self) -> str:
        return self.memory_block_of(self._active_state(), pid=self.pid)

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
        if self._antigravity_active():
            bridge = getattr(self, "mcp_bridge", None)
            if bridge is not None and hasattr(bridge, "push_memory_compact") and bridge.push_memory_compact(self.pid):
                self._mem_next = self._ms() + MEM_GAP * 1000
                self.log("MEM dispatched via Antigravity stateless turn")
                return True
            return False
        batch = self._mem_delta()[:MEM_EVERY]
        memory = self.memory_block_of(self._active_state(), include_live=False).strip() or "(空)"
        lines = []
        for m in batch:
            who = "用户" if m.get("role") == "me" else "对方"
            when = stamp(datetime.fromtimestamp(int(m.get("ts") or 0) / 1000))
            lines.append(f"[{when}] {who}: {clip_message(str(m.get('api') or m.get('text') or ''), 1500)}")
        persona = clip_message(native_persona_text(read_persona(self.personas, self.persona_name())), 8000)
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
        if not self.leader():                                      # 外出设备回来前, 家中设备的旧请求不再写回记忆卡
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
        now_ms = self._ms()
        self.cfg["mem_auto_ts"] = max(now_ms, int(self.cfg.get("mem_auto_ts") or 0) + 1)
        self.cfg["mem_ts"] = max(now_ms, int(self.cfg.get("mem_ts") or 0) + 1, self.cfg["mem_auto_ts"])
        self._mem_next = now_ms + MEM_GAP * 1000
        self.save_chat()
        self._changed()
        self._push()

    def _dream_due(self) -> bool:
        """每日最多一次的睡眠期 / 静默期离线反思（Dreaming）：纠错、核验 ? 条目、跨层晋升与清理过期回访。"""
        if self._dream_busy or self._mem_busy or self.busy or self._bg_busy:
            return False
        if self.hard_block() or self.blocked() or self._ai_problem() or not self.leader():
            return False
        now = self.now()
        today = now.date().isoformat()
        if str(self.cfg.get("dream_day") or "") == today:
            return False
        now_ms = self._ms()
        if now_ms < int(getattr(self, "_dream_next", 0) or 0):
            return False
        # 仅在角色处于睡眠段，或距上次对话已静默 >= 30 分钟时触发，避免打扰活跃对话
        st_kind = (self.other_status() or {}).get("kind") if hasattr(self, "other_status") else ""
        last_chat_ts = max((int(m.get("ts") or 0) for m in self.messages if m.get("role") in ("me", "ai")), default=0)
        quiet_enough = st_kind == "sleep" or (last_chat_ts > 0 and now_ms - last_chat_ts >= 30 * 60 * 1000)
        if not quiet_enough:
            return False
        auto = str(self.cfg.get("mem_auto") or "").strip()
        live = str(self.cfg.get("mem_live") or "").strip()
        has_work = ("?" in auto) or bool(live) or len(self._mem_delta()) >= 6
        return has_work

    def _dream_kick(self) -> bool:
        """后台发起每日睡眠期 Dreaming 整理；支持原生 API 与 Antigravity 接管双路径。"""
        if not self._dream_due():
            return False
        today = self.now().date().isoformat()
        if self._antigravity_active():
            bridge = getattr(self, "mcp_bridge", None)
            if bridge is not None and hasattr(bridge, "push_memory_compact") and bridge.push_memory_compact(self.pid, mode="dream"):
                self._dream_next = self._ms() + 3 * 3600 * 1000
                self.log(f"DREAM dispatched via Antigravity day={today}")
                return True
            return False
        from comm.comm_memory import DREAM_PROMPT
        recent = [m for m in self.messages if m.get("role") in ("me", "ai") and not m.get("hold") and "pending_reply" not in m][-60:]
        if not recent and not str(self.cfg.get("mem_auto") or "").strip() and not str(self.cfg.get("mem_live") or "").strip():
            return False
        lines = []
        for m in recent:
            who = "用户" if m.get("role") == "me" else "对方"
            when = stamp(datetime.fromtimestamp(int(m.get("ts") or 0) / 1000))
            lines.append(f"[{when}] {who}: {clip_message(str(m.get('text') or ''), 800)}")
        persona = clip_message(native_persona_text(read_persona(self.personas, self.persona_name())), 6000)
        auto = str(self.cfg.get("mem_auto") or "").strip() or "(空)"
        live = str(self.cfg.get("mem_live") or "").strip() or "(空)"
        body = (
            f"当前时间: {stamp(self.now())}\n\n"
            f"对方的人设 (只看记忆保留规则，不要抄进摘要):\n{persona}\n\n"
            f"现有长期记忆摘要 (mem_auto):\n{auto}\n\n"
            f"当前实时事件 (mem_live):\n{live}\n\n"
            f"近期对话记录:\n" + ("\n".join(lines) or "(无)")
        )
        upto = max((int(m.get("ts") or 0) for m in recent), default=0)
        clear_snap = int(self.cfg.get("clear_ts") or 0)
        pid = self.pid
        profile = self.profile()
        self._dream_busy = True
        started = time.monotonic()
        self.log(f"DREAM start day={today} msgs={len(recent)}")

        def work():
            reply, err = "", ""
            try:
                reply = self.chat_fn(profile, DREAM_PROMPT, [{"role": "user", "content": body}],
                                     max_tokens=MEM_TOKENS, temperature=0.2, timeout=CHAT_TIMEOUT, cache=False, think=True,
                                     on_usage=lambda t, c: self.log(f"USAGE dream input={t} cached={c}"))
            except ai_assist.AIError as e:
                err = e.message
            except Exception as e:      # noqa: BLE001
                err = type(e).__name__
            self.log(f"DREAM {'FAIL ' + err if err else 'ok'} {time.monotonic() - started:.1f}s chars={len(reply)}")
            with self._lock:
                self._dream_results.append((reply, err, upto, clear_snap, today, pid))

        threading.Thread(target=work, daemon=True).start()
        return True

    def _dream_finish(self, reply: str, err: str, upto: int, clear_snap: int, today: str, pid: str):
        from comm.comm_memory import parse_dream_result, apply_dream_live_removals
        self._dream_busy = False
        if pid != self.pid or not self.leader():
            return
        if int(self.cfg.get("clear_ts") or 0) > int(clear_snap):
            self._dream_next = self._ms() + 3 * 3600 * 1000
            return
        parsed = parse_dream_result(reply) if (not err and reply) else None
        if not parsed:
            self._dream_next = self._ms() + 30 * 60 * 1000
            return
        now_ms = self._ms()
        changed = False
        if parsed["mem_auto"]:
            clean_auto = sanitize_mem(parsed["mem_auto"])
            if clean_auto and clean_auto != str(self.cfg.get("mem_auto") or ""):
                self.cfg["mem_auto"] = clean_auto
                self.cfg["mem_auto_ts"] = max(now_ms, int(self.cfg.get("mem_auto_ts") or 0) + 1)
                changed = True
        if parsed["remove_live_ids"]:
            new_live, removed = apply_dream_live_removals(str(self.cfg.get("mem_live") or ""), parsed["remove_live_ids"])
            if removed:
                self.cfg["mem_live"] = new_live
                self.cfg["mem_live_ts"] = max(now_ms, int(self.cfg.get("mem_live_ts") or 0) + 1)
                self.log(f"DREAM cleaned live ids={removed}")
                changed = True
        self.cfg["dream_day"] = today
        if upto > int(self.cfg.get("mem_upto") or 0):
            self.cfg["mem_upto"] = upto
        if changed:
            self.cfg["mem_ts"] = max(now_ms, int(self.cfg.get("mem_ts") or 0) + 1,
                                     int(self.cfg.get("mem_auto_ts") or 0), int(self.cfg.get("mem_live_ts") or 0))
        self._dream_next = now_ms + 3 * 3600 * 1000
        self.save_chat()
        self._changed()
        if changed:
            self._push()

    @staticmethod
    def _extra_api_only(api_txt: str, plain_txt: str) -> str:
        """仅当用户消息携带额外系统上下文（如邀请/共读页面等）时才附带 api 文本，避免普通聊天消息在接管上下文中重复。"""
        api_s = str(api_txt or "").strip()
        if not api_s or api_s == plain_txt:
            return ""
        if OIT_TAG in api_s or not api_s.endswith("\n" + plain_txt):
            return api_s
        return ""

    def _request(self, kind: str, extra=None, reading_context: str = ""):
        retry_since = int(getattr(self, "_retry_since", 0) or 0)
        self._retry_since = 0
        if self._antigravity_active():
            bridge = getattr(self, "mcp_bridge", None)
            if bridge is None:
                self.error = tr("已由 Antigravity 桌面版接管，但本地 MCP Bridge 未启动；请在设置中重新开启接管或点击自动部署。")
                self._changed()
                return
            if extra:
                if kind in ("retry", "user"):
                    self._req_ts = max([int(m.get("ts") or 0) for m in self.messages if m.get("role") == "me"] or [0])
                    if retry_since > 0:
                        pending_me = [
                            m for m in self.messages
                            if m.get("role") == "me" and not m.get("no_reply") and int(m.get("ts") or 0) >= retry_since
                        ]
                    else:
                        pending_me = []
                        for m in reversed(self.messages):
                            if m.get("role") == "ai" and not m.get("hold") and "pending_reply" not in m:
                                break
                            if m.get("role") == "me" and not m.get("no_reply"):
                                pending_me.append(m)
                        pending_me.reverse()
                    if pending_me:
                        user_lines, api_parts, held_images = [], [], []
                        for m in pending_me:
                            txt = str(m.get("text") or "").strip()
                            at_s = str(m.get("at") or "").replace("T", " ")[:16]
                            img_name = str(m.get("img") or "").strip()
                            img_file = self.chat_image_path(img_name) if img_name and hasattr(self, "chat_image_path") else ""
                            if not (img_file and os.path.isfile(img_file)):
                                img_file = ""
                            if img_file:
                                held_images.append(img_file)
                            line_txt = f"{txt} [图片文件: {img_file}]" if (img_file and txt) else (f"[图片文件: {img_file}]" if img_file else txt)
                            if line_txt:
                                user_lines.append(f"[{at_s}] {line_txt}" if at_s else line_txt)
                            extra_api = self._extra_api_only(str(m.get("api") or ""), txt)
                            if extra_api:
                                api_parts.append(extra_api)
                        held_block = "\n".join(user_lines)
                        retry_note = str(extra[0] or "").strip()
                        if retry_note:
                            api_parts.append(retry_note)
                        if held_images:
                            api_parts.append(oit_text(
                                "用户在上述消息中发送了图片文件：\n" + "\n".join(held_images)
                                + "\n请先使用 read_file 工具查看图片内容。若涉及学科题目并需要调用 oit-subject 子代理，必须将该图片完整路径一并写入子代理 Prompt，要求子代理亲自调用 read_file 查看原图，不要仅转述文字；回复用户时保留清晰的关键推导过程。"
                            ))
                        last_held_img = held_images[-1] if held_images else ""
                        if held_block or api_parts:
                            self._request_image = None
                            self._request_image_path = ""
                            push_txt = (
                                f"[OIT 忙碌期间留言 · 请先调用 oit_get_turn_context 再用 oit_reply 回复]\n{held_block}"
                                if held_block else (retry_note or "请处理用户消息")
                            )
                            if bridge.push_user_message(
                                self.pid,
                                push_txt,
                                api_context="\n\n".join(api_parts),
                                trigger_kind=kind,
                                held_messages=held_block,
                                image_path=last_held_img,
                            ):
                                self.error = ""
                            else:
                                self.error = tr("已由 Antigravity 桌面版接管：未检测到运行中的 OIT Proactive Bridge，请在 Antigravity 的 Customizations 中启用 OIT Companion 插件，或手动新建对话并绑定此人设。")
                            self._reply_rewrite = False
                            self._changed()
                            return
                self._req_ts = max([int(m.get("ts") or 0) for m in self.messages if m.get("role") == "me"] or [0])
                bridge.push_proactive(self.pid, str(extra[0] or ""), kind=kind)
                self.error = ""
                self._changed()
                return
            self._req_ts = max([int(m.get("ts") or 0) for m in self.messages if m.get("role") == "me"] or [0])
            img_path = str(getattr(self, "_request_image_path", "") or "")
            self._request_image = None
            self._request_image_path = ""
            last_msg = next((m for m in reversed(self.messages)
                             if m.get("role") == "me" and str(m.get("text") or "").strip()), None)
            if last_msg:
                last_me = str(last_msg.get("text") or "").strip()
                if (not img_path or not os.path.isfile(img_path)) and last_msg.get("img") and hasattr(self, "chat_image_path"):
                    cand = self.chat_image_path(last_msg["img"])
                    if cand and os.path.isfile(cand):
                        img_path = cand
                if img_path and not os.path.isfile(img_path):
                    img_path = ""
                api_parts = []
                extra_api = self._extra_api_only(str(last_msg.get("api") or ""), last_me)
                if extra_api:
                    api_parts.append(extra_api)
                if img_path:
                    api_parts.append(oit_text(
                        f"用户随本条消息发送了一张图片文件：{img_path}\n"
                        "请先使用 read_file 工具查看该图片内容。若涉及学科题目并需要调用 oit-subject 子代理，"
                        "必须将该图片完整路径一并写入子代理 Prompt，要求子代理亲自调用 read_file 查看原图，"
                        "不要仅转述文字；回复用户时保留清晰的关键推导过程。"
                    ))
                rc = str(reading_context or "").strip()[:18000]
                if rc:
                    api_parts.append(oit_text("当前 OIT 共读阅读上下文 (来自本机书库的当前可见页; 仅用于本轮回答, 不要伪称读过全书):\n" + rc))
                if bridge.push_user_message(self.pid, last_me, api_context="\n\n".join(api_parts), image_path=img_path):
                    self.error = ""
                else:
                    self.error = tr("已由 Antigravity 桌面版接管：未检测到运行中的 OIT Proactive Bridge，请在 Antigravity 的 Customizations 中启用 OIT Companion 插件。")
                self._reply_rewrite = False
                self._changed()
            return
        if self.busy:
            self._again = True
            return
        self._follow_at = 0                                # 有新请求了: 排着的第二次调用取消 (用户又说了 / 主动发 / 重试)
        self._follow_msg = ""
        self._req_ts = max([int(m.get("ts") or 0) for m in self.messages if m.get("role") == "me"] or [0])
        self._active_reply_after = max((int(m.get("reply_after") or 0) for m in self.messages
                                        if m.get("role") == "me" and int(m.get("ts") or 0) <= self._req_ts), default=0)
        batch = self._reply_begin()
        profile = self.profile()
        system = persona_system(self.personas, self.persona_name(), self.split_on())
        dynamic = self.memory_block() + self.joint_system_note()
        who = str(self.cfg.get("user_profile") or "").strip()[:MAX_PROFILE]
        if who and first_turn(self.messages):              # 身份背景: 只在这段对话的第一轮发一次
            dynamic += "\n\n以下是用户的身份背景(用户自己填写):\n" + who
        msgs = api_messages(self.messages, include_latest_api=True)
        reading_context = str(reading_context or "").strip()[:18000]
        if reading_context:
            context = oit_text("当前 OIT 共读阅读上下文 (来自本机书库的当前可见页; 仅用于本轮回答, 不要伪称读过全书):\n" + reading_context)
            if msgs and msgs[-1]["role"] == "user":
                msgs[-1]["content"] += "\n\n" + context
            else:
                msgs.append({"role": "user", "content": context})
        system, msgs = cache_layout(profile, system, dynamic, msgs)     # Gemini: 动态块贴到最后一条用户消息 (system 要逐字节不变)
        if extra:
            if msgs and msgs[-1]["role"] == "user":
                msgs[-1]["content"] += "\n\n" + extra[1]
            else:
                msgs.append({"role": "user", "content": extra[1]})
        if self._reply_rewrite:
            correction = oit_text("用户在你分条回复期间发来了新消息。尚未发出的旧内容已取消；历史中的 assistant 消息都是已经发出的。请根据用户最新消息改写尚未发送的后续内容，不要重复已发出的消息。")
            if msgs and msgs[-1]["role"] == "user":
                msgs[-1]["content"] += "\n\n" + correction
            else:
                msgs.append({"role": "user", "content": correction})
            self._reply_rewrite = False
        status = self.other_status()
        d = self._interaction_prepare()
        self._interaction_turn = (self._interaction_special(status),
                                  int(status["end"].timestamp() * 1000) if status.get("end") else 0, bool(d["shame_turns"]))
        note = oit_text(self.state_text() + "\n" + self.interaction_note(status) + "\n" + self._interaction_judge_note(kind))
        r_note = self.realism_note() if hasattr(self, "realism_note") else ""
        if r_note:
            note += "\n" + r_note
        rm_note = self.room_status_note() if hasattr(self, "room_status_note") else ""
        if rm_note:
            note += "\n" + rm_note
        if self._rt:
            note += "\n" + self._rt_note(self.now())
        if msgs and msgs[-1]["role"] == "user":
            msgs[-1]["content"] += "\n\n" + note
        else:
            msgs.append({"role": "user", "content": note})
        image = self._request_image
        self._request_image = None
        self._request_image_path = ""
        self._active_image = image
        self._answer_image_pending = None
        self._kb_citations_pending = []
        if image and msgs and msgs[-1]["role"] == "user":
            mime = ai_assist.image_mime(image)
            if mime:
                msgs[-1]["content"] = ai_assist.image_content(profile, image, mime, str(msgs[-1]["content"]))
        epoch = self._epoch
        self.busy = True
        self.busy_since = time.monotonic()
        started = time.monotonic()
        self.log(f"REQ {kind} start model={profile.get('model')} host={urlsplit_host(profile.get('base_url'))} "
                 f"msgs={len(msgs)} chars={sum(len(m['content']) for m in msgs) + len(system)}")
        self._changed()

        def used(tokens, cached):
            self.log(f"USAGE {kind} input={tokens} cached={cached}")

        def stream_delta(text):
            with self._lock:
                self._reply_events.append((batch, text))

        mental_reason = ""
        mental_task = ""
        if kind in ("user", "retry"):
            pending_user = []
            for message in reversed(self.messages):
                if message.get("role") == "ai":
                    break
                if message.get("role") == "me" and int(message.get("ts") or 0) <= self._req_ts:
                    pending_user.append(str(message.get("text") or ""))
            mental_text = "\n\n".join(reversed(pending_user)).strip()
            mental_reason = mental_text_reason(mental_text)
            if mental_reason:
                context = []
                for message in self.messages[-15:]:
                    role = "用户" if message.get("role") == "me" else "对方"
                    content = str(message.get("text") or "").strip()
                    if content:
                        context.append(f"{role}: {content[:2000]}")
                mental_task = "最近15条聊天消息:\n" + "\n\n".join(context) + \
                    "\n\n当前待回复的用户原话:\n" + mental_text[:8000]

        why = self.think_reason(kind)
        think_kw = {"think": True} if why else {}           # 只在开的时候带这个参数 (别的 chat_fn 不用认识它)
        if why:
            self.log(f"THINK {kind} on: {why}")

        if self.net_active():                               # 限时网络开着: 请求带上联网搜索 (只在开的时候带这个参数)
            think_kw["web"] = True
            self.log(f"NET {kind} 带联网搜索 (还剩 {max(0, int(self._net().get('until') or 0) - self._ms()) // 1000} 秒)")

        def ask(m):
            kwargs = dict(max_tokens=CHAT_TOKENS, temperature=CHAT_TEMPERATURE, timeout=CHAT_TIMEOUT,
                          cache=True, on_usage=used, tools=True, **think_kw)
            if self.chat_fn is ai_assist.chat_messages:
                kwargs["on_delta"] = stream_delta
            fn = self.remote_chat if profile.get("kind") == "device" and callable(self.remote_chat) else self.chat_fn
            return fn(profile, system, m, **kwargs)

        def work():
            try:
                convo = list(msgs)
                if mental_task:
                    try:
                        analysis = self.subagent_call(
                            "mental_health", f"{self.persona_name()}:{self.pid}", mental_task,
                            max_tokens=700, temperature=0.1, timeout=180)
                        if analysis:
                            convo.append({"role": "user", "content": oit_text(
                                "心理子代理内部分析（用户不可见；仅供你组织本轮回复，不能提及子代理或分析过程）:\n" + analysis)})
                            self.log(f"MENTAL subagent ok reason={mental_reason} chars={len(analysis)}")
                    except Exception as e:  # noqa: BLE001
                        self.log(f"MENTAL subagent FAIL {type(e).__name__}")
                reply = ask(convo)
                op = None
                from ai_assist.ai_tools import unpack
                if unpack(reply) is not None:
                    op, reply = self._tool_rounds(ask, convo, reply, kind, epoch)
                if unpack(reply) is None and _OP_RE.search(reply):          # 注入式自检保留旧协议
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
                    if unpack(reply) is not None and unpack(reply)["name"] not in ("", "oit_reply"):
                        retry_op, reply = self._tool_rounds(ask, convo, reply, kind, epoch)
                        op = op or retry_op
                res = (kind, extra, reply, "", epoch, op, batch)
                self.log(f"REQ {kind} ok {time.monotonic() - started:.1f}s reply_chars={len(reply)}")
            except ai_assist.AIError as e:
                res = (kind, extra, "", e.message, epoch, None, batch)
                self.log(f"REQ {kind} FAIL {time.monotonic() - started:.1f}s {e.kind}: {e.message}"
                         + (f" (系统代理 {ai_assist.proxy_hint()})" if e.kind == "network" else ""))
            except Exception as e:      # noqa: BLE001
                res = (kind, extra, "", f"{type(e).__name__}", epoch, None, batch)
                self.log(f"REQ {kind} FAIL {time.monotonic() - started:.1f}s {type(e).__name__}: {e}")
            self._active_image = None
            with self._lock:
                self._results.append(res)
        threading.Thread(target=work, daemon=True).start()
