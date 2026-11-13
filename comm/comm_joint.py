# -*- coding: utf-8 -*-
"""联合行动: 邀请 / 开始 / 结束 / 生命值 / 同步阶段 / 对方发起的开始时间. MECHANICS 24.9.

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


class JointMixin:
    def _joint(self) -> dict:
        """现在聊的这个对象的联合行动 ({} = 没有)."""
        j = self.cfg.get("joint")
        return j if isinstance(j, dict) and j.get("state") and j.get("pid") == self.pid else {}

    def _joint_set(self, changes: dict, replace: bool = False):
        j = {} if replace else dict(self.cfg.get("joint") or {})
        j.update(changes)
        j["ts"] = max(self._ms(), int(j.get("ts") or 0) + 1)       # 后改的为准 (设备同步)
        self.cfg["joint"] = j
        self.save_cfg()
        self._changed()
        self._push()

    def _joint_note(self, text: str):
        """联合行动的进展记一条系统消息 (聊天里不显示, 发给对方的历史里有): 对方才知道刚刚发生了什么."""
        body = f"{JOINT_TAG} {text}"
        self._add("sys", body, self._compose(self.now(), oit_text(body)))
        self.save_chat()
        self._changed()
        self._push()

    def _joint_span(self, j: dict) -> tuple:
        try:
            return _joint_time(j["start"]), _joint_time(j["end"])
        except (KeyError, TypeError, ValueError):
            return None, None

    def joint_busy(self) -> bool:
        """有联合行动还没结束吗 (等回复 / 对方已接受 / 进行中 / 合约结束后的同步休息中)."""
        j = self._joint()
        st = j.get("state")
        if st in ("queued", "wait", "invite", "yes", "run"):
            return True
        return st == "rest" and self._joint_rest_left(j) > 0

    def _joint_rest_left(self, j: dict) -> float:
        try:
            return (_joint_time(j["rest_until"]) - self.now()).total_seconds()
        except (KeyError, TypeError, ValueError):
            return 0.0

    def joint_live(self) -> bool:
        """现在是不是联合行动的"同步"阶段 (合约进行中, 或者合约结束后对方跟着休息): 对方的状态跟着用户走, 不主动发消息."""
        j = self._joint()
        return j.get("state") == "run" or (j.get("state") == "rest" and self._joint_rest_left(j) > 0)

    def joint_block(self) -> str:
        """现在不能发起联合行动的原因 ("" = 可以)."""
        if not self.pid:
            return "还没有选人设"
        if self.joint_busy():
            return "已经有一个联合行动在进行了"
        return ""

    def set_joint_draft(self, terms, start: datetime) -> str:
        """选好词条和开始时间: 记下来, 随下一条消息一起发 (结束时间 = 开始时间 + 这些词条的合约时长). 返回 "" = 记下了, 否则是原因."""
        import contract as C
        why = self.joint_block()
        if why:
            return why
        now = self.now()
        if start <= now:
            return "开始时间要晚于现在"
        if start > now + timedelta(days=1):
            return "开始时间要在 24 小时以内"
        terms = C.clean_terms(terms, prereq=True)
        end = start + timedelta(seconds=C.duration_of(terms))
        self.joint_draft = {"terms": terms, "start": start.isoformat(timespec="seconds"),
                            "end": end.isoformat(timespec="seconds")}
        self._changed()
        return ""

    def clear_joint_draft(self):
        if self.joint_draft:
            self.joint_draft = {}
            self._changed()

    def joint_label(self) -> str:
        """聊天框上方: 选好、还没发出去的联合行动 (开始 - 结束时间)."""
        d = self.joint_draft
        if not d:
            return ""
        from comm import comm_data
        return comm_data.joint_label(d["terms"], _joint_time(d["start"]), _joint_time(d["end"]), self.now().date())

    def joint_status_text(self) -> str:
        """聊天框上方: 发出去的联合行动现在怎么样了 ("" = 没有 / 已经过去很久)."""
        j = self._joint()
        st = j.get("state")
        a, b = self._joint_span(j)
        if st == "invite" and not self._joint_invite_ready(j):
            return ""
        if not st or a is None:
            return ""
        now = self.now()
        span = f"{a:%H:%M} - {b:%H:%M}" if a.date() == now.date() else f"{a:%m-%d %H:%M} - {b:%H:%M}"
        head = tr("🤝 联合行动") + f" {span}"
        who = self.share_ai_name()
        late = (now - datetime.fromtimestamp(int(j.get("ts") or 0) / 1000)).total_seconds() > 1800
        if st == "wait":
            return head + " · " + tr("等 {name} 回复", name=who)
        if st == "invite":
            return head + " · " + tr("{name} 邀请你一起行动", name=who)
        if st == "yes":
            return head + " · " + tr("{name} 已接受, 到点后在合约页开始行动", name=who)
        if st in ("no", "missed"):
            return "" if late else head + " · " + (tr("{name} 拒绝了", name=who) if st == "no" else tr("已错过"))
        if st == "run":
            return head + " · " + tr("进行中")
        if st == "rest" and self._joint_rest_left(j) > 0:
            return head + " · " + tr("合约结束, {name} 同步休息到 {t}", name=who, t=f"{_joint_time(j['rest_until']):%H:%M}")
        return ""

    def _joint_answer(self, ans: str):
        """对方回复了邀请: [Yes] → 已接受 (约定的开始时间已经过了就算错过), [No] → 拒绝."""
        j = self._joint()
        if j.get("state") != "wait":
            return
        a, _b = self._joint_span(j)
        if ans == "yes":
            self._joint_set({"state": "yes" if a is not None and self.now() < a else "missed"})
        else:
            self._joint_set({"state": "no"})

    def joint_partner(self) -> str:
        """联合行动的对象的名字 (人设名, 不带后缀): 记进合约记录, 统计 → 合约界面显示."""
        return persona_title(self.persona_name())

    def joint_pending(self, terms) -> bool:
        """现在开始这些词条的合约, 算不算按约开始联合行动 (不改任何状态; app 在合约开始前用它决定要不要收起合约前积攒的休息时间)."""
        j = self._joint()
        if j.get("state") != "yes" or set(terms or []) != set(j.get("terms") or []):
            return False
        a, _b = self._joint_span(j)
        return a is not None and a - timedelta(seconds=JOINT_EARLY) <= self.now() <= a + timedelta(seconds=JOINT_LATE)

    def joint_started(self, terms, cid, end=None) -> bool:
        """用户按约开始了合约 (app 在合约开始时调用): 词条和约定的一样, 时间在约定开始的前 10 分钟 ~ 后 5 分钟里, 联合行动开始."""
        if not self.joint_pending(terms):
            return False
        self._rt_sync_safe()                                  # 联合行动开始之前的时间按原日程记下来
        j = self._joint()
        a, b = self._joint_span(j)
        now = self.now()
        end = end or b
        self._joint_set({"state": "run", "cid": str(cid or ""), "began": now.isoformat(timespec="seconds"),
                         "until": end.isoformat(timespec="seconds"), "hp": []})
        self._joint_note(f"联合行动开始了 (合约 {now:%H:%M} - 预计 {end:%H:%M}). 从现在起你与用户的专注 / 休息同步.")
        return True

    def joint_ended(self, cid, result: str, reward_s: float = 0) -> bool:
        """联合行动里的合约结束了 (app 在合约结束时调用): result = completed / failed / idle / offline / abandoned; reward_s = 合约给的休息时间 (秒).
        对方同步得到同样的休息, 休息结束后重新计算日程 (_joint_shift). 合约期间损失生命值的记录在这时一起告诉对方 (有记录就再调用一次 API 让对方回应)."""
        j = self._joint()
        foreign = False
        if not j:                                             # 联合行动属于另一个对象 (合约期间用户切到了别的聊天): 状态照样转成"合约结束后的同步休息", 只是不往现在这个聊天里写记录
            raw = self.cfg.get("joint")
            if isinstance(raw, dict) and raw.get("state") == "run" and raw.get("pid") and raw.get("pid") != self.pid:
                j, foreign = raw, True
        if j.get("state") != "run" or (cid and str(cid) != str(j.get("cid") or cid)):
            return False
        now = self.now()
        rest_s = max(0, int(reward_s or 0))
        until = now + timedelta(seconds=rest_s)
        be = self._user_break_end(now)
        if be is not None:                                    # 合约结束时用户正在休息 (这次休息接着休息): 对方休息到用户这次休息结束
            until = be
        if foreign:
            self._joint_set({"state": "rest", "result": str(result), "ended": now.isoformat(timespec="seconds"),
                             "rest_s": rest_s, "rest_until": until.isoformat(timespec="seconds"), "hp": []})
            return True
        self._rt_sync_safe()                                  # 联合行动期间的状态按用户的专注 / 休息记下来
        lost = self._joint_hp_text(j)
        self._joint_set({"state": "rest", "result": str(result), "ended": now.isoformat(timespec="seconds"),
                         "rest_s": rest_s, "rest_until": until.isoformat(timespec="seconds"), "hp": []})
        if self._rt:                                          # 顺利完成的: 马上记进对方的合约记录 (别的设备在下一次整理数据时从同步来的联合行动状态里记)
            try:
                self._rt_sync()
            except Exception as e:      # noqa: BLE001
                self.log(f"ROUTINE joint record FAIL {type(e).__name__}: {e}")
        rest = f"你与用户一起得到了 {rest_s // 60} 分钟休息, 休息到 {until:%H:%M} 结束, 之后你需要重新计算日程." if rest_s > 0 else             "这次没有得到休息时间, 你立即重新计算日程."
        tail = (" " + lost) if lost else ""
        if result == "completed":
            self._joint_note(f"联合行动顺利完成了. {rest}{tail}")
        else:
            why = {"failed": "用户在合约中违反太多次 (目标生命值归零)", "idle": "用户在合约中空闲太久",
                   "offline": "用户的设备在合约中离线太久", "abandoned": "用户提前终止了合约"}.get(result, "合约没有完成")
            self._joint_note(f"联合行动失败了: {why}. {rest}{tail}")
        if lost:                                              # 有生命值损失的记录: 调用一次 API, 对方可以回应
            self._joint_speak("[系统] 联合行动结束了 (见上一条联合行动记录, 里面有用户在合约中损失生命值的情况). "
                              f"请按人设回应用户一句 (话题和语气自己定, 不要透露日程); 如果实在不方便, 返回{REFUSE}.")
        return True

    def joint_hp_lost(self, text: str) -> bool:
        """联合行动进行中用户损失了生命值 (app 在违反词条 / 解除网络禁令时调用): 记下时间和内容, 等下一次开始休息 / 合约结束时一起告诉对方."""
        j = self._joint()
        if j.get("state") != "run":
            return False
        hp = [x for x in (j.get("hp") or []) if isinstance(x, (list, tuple)) and len(x) == 2][-(JOINT_HP_MAX - 1):]
        hp.append([self.now().isoformat(timespec="seconds"), " ".join(str(text or "").split())[:200]])
        self._joint_set({"hp": hp})
        return True

    def _joint_hp_text(self, j: dict) -> str:
        """记下来的生命值损失 → 给对方看的文字 ("" = 没有)."""
        rows = []
        for x in (j.get("hp") or []):
            try:
                rows.append(f"{_joint_time(x[0]):%H:%M:%S} {x[1]}")
            except (IndexError, KeyError, TypeError, ValueError):
                continue
        return ("合约期间用户损失生命值的记录 (时间 内容): " + "; ".join(rows) + ".") if rows else ""

    def joint_break_started(self, total_s: float) -> bool:
        """联合行动进行中用户开始休息了 (app 在休息开始时调用): 告诉对方这次休息的时间, 连同之前记下的生命值损失; 调用一次 API, 对方可以回应."""
        j = self._joint()
        if j.get("state") != "run":
            return False
        now = self.now()
        secs = max(0, int(total_s or 0))
        until = now + timedelta(seconds=secs)
        span = f"{secs // 60} 分钟" if secs >= 60 else f"{secs} 秒"
        lost = self._joint_hp_text(j)
        if j.get("hp"):
            self._joint_set({"hp": []})
        self._joint_note(f"联合行动中用户 {now:%H:%M} 开始休息了: 这次休息 {span}, 到 {until:%H:%M} 结束, 你与用户同步休息." + ((" " + lost) if lost else ""))
        self._joint_speak("[系统] 联合行动中用户刚刚开始休息 (见上一条联合行动记录). "
                          f"请按人设回应用户一句 (话题和语气自己定, 不要透露日程); 如果实在不方便, 返回{REFUSE}.")
        return True

    def joint_break_ended(self) -> bool:
        """用户的休息结束了 (自然走完 / 提前结束; app 看到休息结束时调用).
        合约进行中: 对方的状态本来就实时跟着用户, 这里再记一条系统消息, 对方的聊天记录里才不会还停在"你在休息"上;
        合约结束后的同步休息中: 对方的休息也跟着结束, 立即重新计算日程."""
        j = self._joint()
        now = self.now()
        if j.get("state") == "run":
            mine = {"focus": "专注", "break": "休息", "sleep": "睡眠"}.get(self.state_fn(), "空闲")
            self._joint_note(f"联合行动中用户 {now:%H:%M} 结束了休息, 现在用户{mine}, 你的状态也跟着变成{mine} (不再休息).")
            return True
        if j.get("state") != "rest" or self._joint_rest_left(j) <= 0:
            return False
        was = _joint_time(j["rest_until"])
        self._rt_sync_safe()
        self._joint_set({"rest_until": now.isoformat(timespec="seconds")})
        self._joint_note(f"用户 {now:%H:%M} 结束了休息 (原定休息到 {was:%H:%M}), 你也跟着结束休息, 现在需要重新计算日程.")
        return True

    def _joint_speak(self, body: str):
        """联合行动: 调用一次 API 让对方回应一句. 对方不方便 / 不是主端就算了 (信息已经记在聊天记录里); 正在回别的话: 等一会儿再调用."""
        if self._ai_problem() or not self.leader() or self.hard_block():
            return
        if self.busy:
            self._joint_pending = (body, self._ms())
            return
        self._trigger("joint_reply", body)

    def _user_break_end(self, now=None):
        """用户正在休息 → 这次休息结束的时刻 (必须还没过); 否则 None."""
        if self.state_fn() != "break":
            return None
        try:
            t = self.break_end_fn()
        except Exception:      # noqa: BLE001
            return None
        return t if isinstance(t, datetime) and t > (now or self.now()) else None

    def _joint_follow(self, j: dict, now):
        """联合行动结束后的同步阶段 (state=rest): 对方继续跟着用户 —— 用户还在专注 / 睡眠: 一直顺延 (用户的休息结束才结束, 最长 JOINT_FOLLOW_CAP);
        用户正在休息: 休息到用户这次休息真正结束 (不再按合约给的那几分钟算). 用户空闲: 不顺延, 到时间就结束."""
        try:
            until, ended = _joint_time(j["rest_until"]), _joint_time(j["ended"])
        except (KeyError, TypeError, ValueError):
            return
        if until <= now:
            return
        cap = ended + timedelta(seconds=JOINT_FOLLOW_CAP)
        u = self.state_fn()
        want = None
        if u == "break":
            want = self._user_break_end(now)
            if want is not None and abs((want - until).total_seconds()) < 3:
                want = None
        elif u in ("focus", "sleep") and (until - now).total_seconds() < JOINT_FOLLOW_STEP / 2:
            want = now + timedelta(seconds=JOINT_FOLLOW_STEP)
        if want is None:
            return
        want = min(want, cap)
        if abs((want - until).total_seconds()) >= 3:
            self._joint_set({"rest_until": want.isoformat(timespec="seconds")})

    def _joint_tick(self, now):
        """联合行动的时间到了 / 错过了."""
        j = self._joint()
        st = j.get("state")
        if st in ("invite", "wait") and self._ms() - int(j.get("ts") or 0) >= JOINT_INVITE_TTL * 1000:
            self._joint_set({"state": ""})                     # 发出的邀请 5 分钟没人接受 / 回复: 自动清理 (1.0.428, 1.0.429 连用户发起的也算)
            return
        if st == "queued":
            if not self.leader() or not any(m.get("uid") == j.get("message") and not m.get("hold") for m in self.messages):
                return
            self._op_deliver()
            j = self._joint()
            st = j.get("state")
        a, _b = self._joint_span(j)
        if st == "rest" and self.leader():
            self._joint_follow(j, now)
        if a is None:
            return
        if st == "wait" and now > a:                          # 一直没有回复, 约定的开始时间过了
            if self.leader():
                self._joint_set({"state": "missed"})
        elif st == "invite" and now > a:                      # 对方发起的邀请, 用户一直没有接受 / 拒绝, 约定的开始时间过了
            if self.leader():
                self._joint_set({"state": "missed"})
                self._joint_note("用户没有回应你发起的联合行动 (已经错过).")
        elif st == "yes":
            if now >= a and self._joint_due_id != j.get("id"):
                self._joint_due_id = str(j.get("id"))
                if self.on_joint_due:
                    self.on_joint_due(dict(j))
            if now > a + timedelta(seconds=JOINT_LATE) and self.leader():
                self._joint_set({"state": "missed"})
                self._joint_note(f"用户答应了联合行动, 却在约定的开始时间 ({a:%H:%M}) 过了 {JOINT_LATE // 60} 分钟后还没有开始合约: 视为用户放弃了 (放了你的鸽子).")

    def _joint_status(self, now) -> dict:
        """联合行动同步阶段对方的状态: 合约进行中跟着用户的计时器 (专注 / 休息), 合约结束后一直休息到休息时间结束; 不在同步阶段 = {}."""
        j = self._joint()
        st = j.get("state")
        try:
            if st == "run":
                kind = {"focus": "focus", "break": "rest", "sleep": "sleep"}.get(self.state_fn(), "idle")
                return {"kind": kind, "label": "联合行动", "detail": "联合行动", "joint": True,
                        "begin": _joint_time(j["began"]), "end": _joint_time(j["until"])}
            if st == "rest" and self._joint_rest_left(j) > 0:
                kind = {"focus": "focus", "break": "rest", "sleep": "sleep"}.get(self.state_fn(), "rest")     # 合约结束后继续跟着用户 (用户空闲时对方休息)
                return {"kind": kind, "label": "联合行动", "detail": "联合行动", "joint": True,
                        "begin": _joint_time(j["ended"]), "end": _joint_time(j["rest_until"])}
        except (KeyError, TypeError, ValueError):
            pass
        return {}

    def _joint_shift_info(self):
        """联合行动之后休息结束的时刻 T: T 落在人设的某个循环段里, 那一段从 T 起"立即开始专注"重新计算, 直到那个循环结束 (之后又是原来的日程);
        T 落在循环外 (和原来的空闲 / 睡眠 / 吃饭重叠) 则不影响. 返回 {"entries": 重新计算后的完整日程, "loop": 重新生成的那几段, "until": 循环结束的时刻} 或 None."""
        j = self._joint()
        if j.get("state") != "rest":
            return None
        try:
            t = _joint_time(j["rest_until"])
        except (KeyError, TypeError, ValueError):
            return None
        self._load_schedule()
        now = self.now()
        if now < t:
            return None
        tmin = t.hour * 60 + t.minute
        for idx, (s, e, n) in enumerate(self._loop_cache):
            for back in (0, 1):                               # 0: 循环从 T 这一天开始; 1: 循环从前一天开始 (跨零点)
                tm = tmin + back * 1440
                if not s <= tm < e:
                    continue
                until = datetime.combine(t.date() - timedelta(days=back), datetime.min.time()) + timedelta(minutes=e)
                if now >= until:
                    return None
                new = loop_entries(s, e, n, tm)
                for x in new:
                    x["loop"] = idx
                keep = [x for x in self._entry_cache if x.get("loop") != idx]
                return {"entries": sorted(keep + new, key=lambda x: x["start"]), "loop": new, "until": until}
        return None

    def _joint_shift(self):
        info = self._joint_shift_info()
        return info["entries"] if info else None

    def joint_system_note(self) -> str:
        """发给对方的 system 末尾: 联合行动现在是什么情况 (对方按它回复, 不再按原日程判断). 没有联合行动 = ""."""
        j = self._joint()
        st = j.get("state")
        now = self.now()
        try:
            if st == "run":
                mine = {"focus": "专注", "break": "休息", "sleep": "睡眠"}.get(self.state_fn(), "空闲")
                return (f"\n\n[联合行动进行中] 你与用户的联合行动(合约) {_joint_time(j['began']):%H:%M} 开始, 预计 {_joint_time(j['until']):%H:%M} 结束. "
                        f"期间你们的专注 / 休息同步: 用户现在{mine}, 你也{mine}; 不要再按原日程判断此刻能不能回复.")
            if st == "rest" and self._joint_rest_left(j) > 0:
                until = _joint_time(j["rest_until"])
                ukind = self.state_fn()
                if ukind in ("focus", "sleep"):               # 合约结束了, 用户还在专注 / 睡眠: 对方继续同步, 用户的休息结束才结束
                    mine = {"focus": "专注", "sleep": "睡眠"}[ukind]
                    return (f"\n\n[联合行动已{'完成' if j.get('result') == 'completed' else '失败'}] 合约虽然结束了, 你与用户仍然同步: 用户现在{mine}, 你也{mine}; "
                            f"用户休息时你才休息, 用户的休息结束后 (预计不晚于 {until:%H:%M}) 按规则 X 重新计算日程.")
                return (f"\n\n[联合行动已{'完成' if j.get('result') == 'completed' else '失败'}] 你与用户同步得到了休息, "
                        f"你休息到 {until:%H:%M}; 休息结束后按规则 X 重新计算日程.")
            info = self._joint_shift_info()
            if info:
                lines = "\n".join(f"{x['start'] % 1440 // 60}:{x['start'] % 60:02d}-{x['end'] % 1440 // 60}:{x['end'] % 60:02d}:{x['label']}"
                                  for x in info["loop"])
                return (f"\n\n[联合行动之后重新计算的日程] 休息结束后你立即开始专注, 到 {info['until']:%H:%M} 为止循环部分的日程改成 "
                        f"(以此为准, 原来的循环部分作废):\n{lines}")
        except (KeyError, TypeError, ValueError):
            pass
        return ""

    def _op_important(self) -> list:
        """人设日程里的重要活动 (吃饭这类不叫"空闲"的空闲时段) 和睡眠: [(开始分钟, 结束分钟, 名字)]; 合约时间不能和它们重合.
        循环生成的休息和普通的"空闲"不算."""
        self._load_schedule()
        out = []
        if self._rt:
            return out
        for e in self._entry_cache:
            if e.get("loop") is not None:
                continue
            label = str(e.get("label") or "")
            head = re.split(r"[(（]", label, maxsplit=1)[0].strip().lower()
            plain = head in ("空闲", "idle", "free") and not e.get("detail")
            if e["kind"] == "sleep" or (e["kind"] == "idle" and not plain):
                out.append((int(e["start"]), int(e["end"]), label))
        return out

    def _op_slots(self, now) -> list:
        """接下来 24 小时内可以作为合约开始点的时刻: 重要活动 / 睡眠 / 休息 (循环里的) / 空闲 的**结尾** (可以跨天);
        [(开始时刻, 可用秒数, 挡住它的重要活动的名字 ("" = 没有))], 按时间排 (最近的在前); 太近 (OP_MIN_LEAD 秒内) 的不算."""
        self._load_schedule()
        if self._rt:
            return self._op_slots_rt(now)
        important = self._op_important()
        day0 = datetime.combine(now.date(), datetime.min.time())
        ends = sorted({int(e["end"]) for e in self._entry_cache if e["kind"] in ("sleep", "idle", "rest")})
        out, seen = [], set()
        for m in ends:
            for k in (-1, 0, 1):                              # 跨零点的段 (23:30-6:30) 结尾 > 1440
                t = day0 + timedelta(days=k, minutes=m)
                if t in seen or not now + timedelta(seconds=OP_MIN_LEAD) <= t <= now + timedelta(days=1):
                    continue
                seen.add(t)
                avail, who = 86400.0, ""
                for a, b, label in important:
                    for d in (-1, 0, 1, 2):
                        sa = day0 + timedelta(days=d, minutes=a)
                        sb = day0 + timedelta(days=d, minutes=b)
                        if sb <= t:
                            continue
                        left = max(0.0, (sa - t).total_seconds())
                        if left < avail:
                            avail, who = left, label
                out.append((t, avail, who))
        out.sort(key=lambda x: x[0])
        return out

    def _op_slots_rt(self, now) -> list:
        """_op_slots 的日程文件版本 (绝对时间): 睡眠 / 吃饭这类有名字的空闲是重要活动, 合约时间不能和它们重合."""
        segs = self._rt_segs(now - timedelta(hours=12), now + timedelta(days=2))
        important = [(s["start"], s["end"], s["label"]) for s in segs
                     if s["kind"] == "sleep" or (s["kind"] == "idle" and s["loop"] is None and not s["gap"] and not routine.Store.plain(s["label"]))]
        ends = sorted({s["end"] for s in segs if s["kind"] in ("sleep", "idle", "rest")})
        out = []
        for t in ends:
            if not now + timedelta(seconds=OP_MIN_LEAD) <= t <= now + timedelta(days=1):
                continue
            avail, who = 86400.0, ""
            for sa, sb, label in important:
                if sb <= t:
                    continue
                left = max(0.0, (sa - t).total_seconds())
                if left < avail:
                    avail, who = left, label
            out.append((t, avail, who))
        out.sort(key=lambda x: x[0])
        return out

    def _op_offer(self, slots: list, last: list) -> str:
        """第一次调用的回复: 带编号的词条库 + 可用的开始时间 + 上次的选择."""
        from comm import comm_data
        import contract as C
        lines = ["(OIT 系统提供, 用户看不到) 你要向用户发起联合行动. 下面是词条库, 每条前面是编号.", "", comm_data.numbered_library(), "",
                 f"【合约时长】{C.BASE_SECONDS // 3600:g} 小时 (主动发起的联合行动不选持久战, 时长固定).",
                 "【开始时间】由 OIT 自动定: 以你日程里最近的 重要活动 / 睡眠 / 休息 / 空闲 的结尾作为合约的开始点, 放不下就用下一个 (可以跨天); 下面列出最近的几个 (从那个时刻起合约开始, 此后你与用户的专注 / 休息同步). "
                 "合约不能和你日程里的重要活动 (吃饭等) 和睡眠重合, 所以词条的总时长要放得下:"]
        for t, avail, who in slots[:OP_OFFER_MAX]:
            span = "没有挡住的重要活动" if not who else f"到「{who}」之前还有 {avail / 3600:.1f} 小时"
            lines.append(f"- {t:%m-%d %H:%M} ({span})")
        if last:
            nums = [i for i, x in enumerate(C.TERMS, 1) if x[0] in last]
            lines.append(f"【上次选择的词条】[{','.join(str(n) for n in nums)}] (默认与上次相同; 想换就自己选)")
        else:
            lines.append("【上次选择的词条】没有 (请随机选择)")
        lines.append("请按顺序**只回复**选择的词条编号, 写成 [1,2,3,9,10] 这样 (不要回复别的字); 不要选无言为真, 也不要选持久战 (环境:持久战-I / II / III).")
        return "\n".join(lines)

    def _op_terms(self, pick: str, last: list) -> list:
        """对方回的编号 → 词条 id (去掉无言为真 / 冲突的 / 缺前置的). 一个也没有 = 上次的; 没有上次 = 随机选几个."""
        import contract as C
        ids = []
        m = re.search(r"[\[［]\s*(\d+(?:\s*[,，、]\s*\d+)*)\s*[\]］]", str(pick or ""))
        if m:
            for n in re.findall(r"\d+", m.group(1)):
                if 1 <= int(n) <= len(C.TERMS):
                    ids.append(C.TERMS[int(n) - 1][0])
        ban = {"silent"} | {t[0] for t in C.TERMS if t[1] == "long"}       # 主动发起不选无言为真, 也不选持久战 (时长固定)
        if self._rt:                                                        # 日程文件里写的"不选择的词条 / 违规词条"也不选
            ban |= routine.banned_ids(self._rt)
        terms = C.clean_terms([t for t in ids if t not in ban], prereq=True)
        if not terms:
            terms = C.clean_terms([t for t in (last or []) if t not in ban], prereq=True)
        if not terms:
            pool = [t[0] for t in C.TERMS if t[0] not in ban]
            terms = C.clean_terms(random.sample(pool, random.randint(2, 4)), prereq=True)
        return terms

    def _op_plan(self, terms: list, slots: list):
        """词条 + 最近的放得下的开始点 → {"terms", "start", "end"}; 哪个开始点都放不下 = None."""
        import contract as C
        dur = C.duration_of(terms)
        for t, avail, _who in slots:
            if avail >= dur:
                return {"terms": list(terms), "start": t.isoformat(timespec="seconds"),
                        "end": (t + timedelta(seconds=dur)).isoformat(timespec="seconds")}
        return None

    def _operation_rounds(self, ask, convo: list, reply: str, has_system: bool = True):
        """工作线程里: 对方回了 [operation] 之后的两次调用. convo 原地加上这几轮; 返回 (计划 or None, 最后那次 (对用户说话) 的回复)."""
        from comm import comm_data
        import contract as C
        why = self.joint_block() or ("现在是联合行动的同步阶段" if self.joint_live() else "")
        slots = [] if why else self._op_slots(self.now())
        if not why and not slots:
            why = "你的日程里没有可以作为开始点的时间段, 或者接下来 24 小时里没有合适的开始点"
        how = "按上面 [系统] 的要求" if has_system else "直接回复用户上面的消息, 即"
        convo.append({"role": "assistant", "content": reply})
        if why:
            convo.append({"role": "user", "content": oit_text(f"(OIT 系统提供, 用户看不到) 现在不能发起联合行动: {why}. 当作没有发起, 不要再输入 [operation]; "
                                                              f"请{how}对用户说话, 实在不方便返回{REFUSE}.")})
            return None, ask(convo)
        last = list(self.cfg.get("joint_last") or [])
        convo.append({"role": "user", "content": oit_text(self._op_offer(slots, last))})
        pick = ask(convo)
        terms = self._op_terms(pick, last)
        plan = self._op_plan(terms, slots)
        convo.append({"role": "assistant", "content": pick})
        names = "、".join(C.BY_ID[t][3].split(":", 1)[-1] for t in terms)
        if plan:
            a, b = _joint_time(plan["start"]), _joint_time(plan["end"])
            msg = (f"(OIT 系统提供, 用户看不到) 联合行动已发出: 词条 {names}; "
                   f"{comm_data.joint_label(terms, a, b, self.now().date())}. 用户到时按约开始合约即可. "
                   f"现在请对用户说话 (邀请对方一起, 话题和语气按人设; 可以提开始时间); 不要再输入 [operation] 或编号, 不要返回{REFUSE}.")
        else:
            msg = (f"(OIT 系统提供, 用户看不到) 联合行动没有发出: 你选的词条 ({names}) 的合约时长放不进任何一个开始时间 (会和你日程里的重要活动重合). "
                   f"当作没有发起, 现在请{how}对用户说话, 不要提联合行动; 实在不方便返回{REFUSE}.")
        convo.append({"role": "user", "content": oit_text(msg)})
        return plan, ask(convo)

    def _operation_refused(self, ask, convo: list, reply: str, kind: str, has_system: bool) -> str:
        """工作线程里: 回复用户 (不是主动发起) 时写了 [operation]. 不能发起 (MECHANICS 24.9.3): 告诉对方原因, 让它重新回复一次.
        重新回复里残留的 [operation] / 编号也去掉, 不留进聊天记录 (不然以后对方会以为自己发起过)."""
        self.log(f"OP rejected ({kind}): 回复用户时写了 [operation], 只有主动发起或用户邀请才能发起联合行动, 告诉对方后再调用一次")
        convo.append({"role": "assistant", "content": reply})
        how = "请按上面 [系统] 的要求对用户说话" if has_system else "请直接回复用户上面的消息"
        convo.append({"role": "user", "content": oit_text("(OIT 系统提供, 用户看不到) 现在不能发起联合行动: 只有系统要求你主动发消息 (第 III 条) 或用户邀请你发起时才能发起, 现在不是. "
                                                          f"当作没有发起, 不要再输入 [operation] 或编号, 也不要说自己发起了联合行动; {how}, 实在不方便返回{REFUSE}.")})
        return _OP_STRIP_RE.sub("", ask(convo)).strip()

    def _op_apply(self, plan: dict, message=None):
        """先保存邀请计划，等待对应消息实际显示再生效。"""
        if not plan or self.joint_block():
            return
        self._joint_set({"id": f"{self._ms()}", "pid": self.pid, "state": "queued", "terms": list(plan["terms"]),
                         "start": plan["start"], "end": plan["end"], "by": "ai", "message": (message or {}).get("uid", "")}, replace=True)
        if not message or not message.get("hold"):
            self._op_deliver()

    def _op_deliver(self):
        """消息已展示才发出邀请，五分钟有效期从这时起算。"""
        from comm import comm_data
        plan = self._joint()
        a, b = self._joint_span(plan)
        self._joint_set({"state": "invite"})
        self.cfg["joint_last"] = list(plan["terms"])
        self.save_cfg()
        self._joint_note(f"你向用户发起了联合行动: {comm_data.joint_label(plan['terms'], a, b, self.now().date())}. "
                         f"等用户在界面上点 接受 / 拒绝; 接受了, 到时按约开始合约, 联合行动就开始.")

    def joint_invite(self) -> dict:
        """对方发起的、等用户点 接受 / 拒绝 的联合行动 ({} = 没有); 聊天页顶上和合约页右下角都显示 拒绝 / 接受."""
        j = self._joint()
        return dict(j) if j.get("state") == "invite" and self._joint_invite_ready(j) else {}

    def _joint_invite_ready(self, invitation):
        """别的设备已展示的邀请，在本机也必须等关联消息展示。"""
        uid = invitation.get("message")
        return not uid or any(m.get("uid") == uid and not m.get("hold") for m in self.messages)

    def joint_state(self) -> str:
        """现在的联合行动的状态 (wait / invite / yes / no / missed / run / rest), 没有 = ""."""
        return str(self._joint().get("state") or "")

    def joint_respond(self, accept: bool) -> str:
        """用户点了 接受 / 拒绝. 返回 "" = 记下了, 否则是原因. 接受 → 和用户自己发起、对方答应了的一样 (到点在合约页按约开始); 拒绝 → 对方知道被拒绝了."""
        j = self._joint()
        if j.get("state") != "invite" or not self._joint_invite_ready(j):
            return "没有等你回复的联合行动邀请"
        a, _b = self._joint_span(j)
        if a is not None and self.now() > a:
            self._joint_set({"state": "missed"})
            return "约定的开始时间已经过了"
        self._joint_set({"state": "yes" if accept else "no"})
        self._joint_note("用户接受了你发起的联合行动. 到约定的开始时间, 用户会按约开始合约." if accept else "用户拒绝了你发起的联合行动.")
        if not self._ai_problem() and self.leader():          # 接受 / 拒绝都会再调用一次 API: 对方回应一句
            self._trigger("joint_reply", "[系统] 用户在界面上点了「" + ("接受" if accept else "拒绝") + "」你发起的联合行动. "
                                         "请按人设回应用户一句 (话题和语气自己定, 不要透露日程); " + f"如果实在不方便, 返回{REFUSE}.")
        return ""
