# -*- coding: utf-8 -*-
"""日程跟随 (实时) / 亲密关系的条件与告白 / 主动消息的触发. MECHANICS 24.15 / 24.16 / 24.18.

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


class RtMixin:
    def _rt_joint_t(self):
        """联合行动之后休息结束的时刻 (循环里的"立即开始专注"用); 没有 = None."""
        j = self._joint()
        if j.get("state") != "rest":
            return None
        try:
            return _joint_time(j["rest_until"])
        except (KeyError, TypeError, ValueError):
            return None

    def _rt_segs(self, a, b, rt=None, seed=None, joint=True) -> list:
        """[a, b) 里对方的日程段 (带当天波动; 现在聊的对象还会套上联合行动之后的重算)."""
        rt = rt or self._rt
        if not rt:
            return []
        seed = seed or self.pid
        t = self._rt_joint_t() if joint and rt is self._rt else None
        if t is None:
            return routine.plan_between(rt, a, b, seed)
        segs = routine.shift_for_joint(routine.plan_between(rt, a - timedelta(hours=12), b + timedelta(hours=12), seed), t, self.now())
        return [s for s in segs if s["end"] > a and s["start"] < b]

    def _rt_note(self, now, rt=None, pid=None) -> str:
        """给模型看的此刻状态 (OIT 按日程实时算出, 用户看不到): 状态 + 已经持续多久 + 下一段是专注 / 睡眠且 5 分钟内开始的话提前说一声 + 这一轮循环的开始时间与每轮专注时间."""
        rt = rt or self._rt
        if not rt:
            return ""
        seed = pid or self.pid
        active = pid is None or pid == self.pid
        segs = self._rt_segs(now - timedelta(hours=14), now + timedelta(hours=2), rt, seed, joint=active)
        cur = routine.status_in(segs, now)
        if active:
            j = self._joint_status(now)
            if j:
                cur = {"kind": j["kind"], "label": "联合行动", "start": j["begin"], "end": j["end"], "loop": None, "gap": False}
        if not cur:
            return ""
        out = (f"(OIT 系统提供, 用户看不到) 你此刻的状态 (OIT 按你的日程实时算出, 以此为准, 不要自己推算): "
               f"{tr(STATUS_TEXT.get(cur['kind'], '空闲'))}, 从 {cur['start']:%H:%M} 开始, 已经 {max(0, int((now - cur['start']).total_seconds() // 60))} 分钟.")
        if cur.get("loop") is not None:
            same = [x for x in segs if x.get("loop") == cur["loop"] and abs((x["start"] - cur["start"]).total_seconds()) < 12 * 3600]
            if same:
                nr = max((x.get("fm") or 0) for x in same) or (cur.get("n") or 100)       # 1.0.400: 循环里最后一轮的专注会提前结束, 每轮专注按实际排出来的算 (不是写的 100)
                out += f" 你所在的循环 {min(x['start'] for x in same):%H:%M} 开始, 每轮专注 {nr:g} 分钟."
                fm = cur.get("fm") or 0
                if fm and fm < nr - 0.5:
                    out += f" (这一轮的专注提前结束了, 只有 {fm:g} 分钟)"
        nxt = next((s for s in segs if s["start"] >= cur["end"] and s["kind"] in ("focus", "sleep")), None)
        if nxt and (nxt["start"] - now).total_seconds() <= 5 * 60:
            out += f" 你的{STATUS_TEXT[nxt['kind']].rstrip('中')}将在 {nxt['start']:%H:%M} 开始 (约 {max(1, round((nxt['start'] - now).total_seconds() / 60))} 分钟后)."
        return oit_text(out)

    def _rt_store_get(self):
        """现在聊的对象实际发生过的数据 (没有日程文件 = None)."""
        if not self._rt or not self.pid:
            return None
        st = self._rt_store
        if st is None or st.seed != self.pid:
            st = self._rt_store = routine.Store(os.path.join(self.dir, f"routine_{self.pid}.json"), self._rt, self.pid)
        st.r = self._rt
        return st

    def _rt_msgs(self, a, b) -> int:
        lo, hi = int(a.timestamp() * 1000), int(b.timestamp() * 1000)
        return sum(1 for m in self.messages if m.get("role") == "me" and lo <= int(m.get("ts") or 0) < hi)

    def _rt_fine(self, t) -> bool:
        """这个整点 t 开始的一小时里有没有带细化规范的联合行动 (整点记录改成每 30 分钟一格)."""
        j = self._joint()
        if j.get("state") not in ("run", "rest") or "fine" not in (j.get("terms") or []):
            return False
        try:
            a = _joint_time(j["began"])
            b = _joint_time(j.get("ended") or j["until"])
        except (KeyError, TypeError, ValueError):
            return False
        return a < t + timedelta(hours=1) and t < b

    def _rt_sync_safe(self):
        """联合行动的状态要变了 (开始 / 结束 / 休息结束): 先把对方的时间线补到现在, 之前的时间才按旧的状态记."""
        if not self._rt:
            return
        try:
            self._rt_sync()
        except Exception as e:      # noqa: BLE001
            self.log(f"ROUTINE sync FAIL {type(e).__name__}: {e}")

    def _rt_sync(self, now=None):
        """把对方的数据补到现在 (时间线 + 整点记录), 存盘. 好几天没调用时, 缺的部分按日程规则补齐."""
        st = self._rt_store_get()
        if not st:
            return None
        now = now or self.now()
        with self._rt_lock:
            self._rt_joint_record(st)
            j = self._joint_status(now)
            ov = (j["kind"], "空闲" if j["kind"] == "idle" else j["label"], j["begin"]) if j else None      # 联合行动里的空闲记成"空闲" (不是"联合行动"); begin: 只覆盖联合行动开始之后的时间
            st.fill_timeline(now, lambda a, b: self._rt_segs(a, b), ov)
            st.fill_cells(now, self._rt_msgs, self._rt_fine)
            if st.dirty:
                st.dirty = False
                self._write_async(st.path, st.payload())
        return st

    def _rt_joint_record(self, st):
        """和用户一起顺利完成的联合行动 (state=rest, result=completed): 把这次真实的合约 (词条 / 等级 / 结束时间) 记进对方的合约记录.
        联合行动的状态会随设备同步, 所以哪台设备都能记; 失败 / 提前终止的不算"顺利完成"."""
        j = self._joint()
        if j.get("state") != "rest" or j.get("result") != "completed":
            return
        import contract as C
        terms = C.clean_terms(j.get("terms") or [])
        ended = j.get("ended")
        if not terms or not ended:
            return
        lv = C.level_of(terms)
        st.add_joint({"id": str(j.get("cid") or j.get("id") or ended), "result": "completed", "end": str(ended)[:19], "level": lv,
                      "orig_level": lv, "orig": list(terms), "terms": list(terms), "rand": []})

    def _cond_days(self, refresh: bool = True) -> dict:
        """{日期: [专注秒, 空闲秒]}: 用户的计时记录只留最近 7 天, 所以最近 7 天每次都重算存下来, 更早的用以前存的."""
        now = self.now()
        days = {k: list(v) for k, v in (self.cfg.get("cond_days") or {}).items() if isinstance(v, (list, tuple)) and len(v) == 2}
        if refresh:
            for i in range(7):
                d = (now - timedelta(days=i)).date()
                try:
                    v = self.user_day_fn(d)
                except Exception:       # noqa: BLE001
                    v = None
                if v:
                    days[f"{d:%Y-%m-%d}"] = [int(v[0]), int(v[1])]
        keep = {f"{(now - timedelta(days=i)).date():%Y-%m-%d}" for i in range(COND_KEEP_DAYS)}
        days = {k: v for k, v in days.items() if k in keep}
        if refresh and days != (self.cfg.get("cond_days") or {}):
            self.cfg["cond_days"] = days
            self.save_cfg()
        return days

    def _cond_tick(self):
        if time.monotonic() < self._cond_next:
            return
        self._cond_next = time.monotonic() + 3600
        try:
            self._cond_days()
        except Exception as e:      # noqa: BLE001
            self.log(f"COND ledger FAIL {type(e).__name__}: {e}")

    def msg_total(self) -> int:
        """现在聊的这个对象互相发过多少条信息: 本地只存最近 KEEP 条, 更早被砍掉的记在 msg_gone 里 (清空聊天归零)."""
        return int(self.cfg.get("msg_gone") or 0) + sum(1 for m in self.messages if m.get("role") in ("me", "ai") and not m.get("hold"))

    def condition_state(self, strict: bool = False) -> list:
        """[(编号, 说明, 当前进度, 要求, 满足了吗)]: 五个条件, 全部用用户的真实数据算. strict = 对方主动告白用的那一组 (COND_STRICT), 否则是用户提出时 [condition] 用的 (COND_NORMAL)."""
        P = COND_STRICT if strict else COND_NORMAL
        now = self.now()
        today = now.date()
        # I. 共同完成的合约 (只有带日程文件的人设记得到)
        joint_n = 0
        st = None
        if self._rt:
            try:
                st = self._rt_sync(now)
            except Exception:       # noqa: BLE001
                st = None
        if st:
            floor = datetime.combine(today - timedelta(days=P["joint_days"] - 1), datetime.min.time())
            for x in st.joint:
                try:
                    end = datetime.fromisoformat(str(x["end"]))
                except (KeyError, ValueError):
                    continue
                if floor <= end <= now and int(x.get("level") or 0) >= P["joint_lv"]:
                    joint_n += 1
        # II / IV. 用户的专注 / 空闲
        days = self._cond_days()
        span = [f"{(now - timedelta(days=i)).date():%Y-%m-%d}" for i in range(P["study_days"])]
        got = [days[k] for k in span if k in days]
        study_h = sum(v[0] for v in got) / 3600.0
        idle_h = sum(v[1] for v in got) / 3600.0
        today_h = (days.get(f"{today:%Y-%m-%d}") or [0, 0])[0] / 3600.0
        # III. 信息条数 (总数, 不只是现在存着的)
        msgs = self.msg_total()
        # V. 特殊空闲时段
        special = False
        if self._rt:
            try:
                segs = self._rt_segs(now - timedelta(hours=14), now + timedelta(hours=2), self._rt, self.pid, joint=True)
                cur = routine.status_in(segs, now)
                special = bool(cur) and routine.seg_class(cur) == "special"
            except Exception:       # noqa: BLE001
                special = False
        return [
            ("I", f"包括今天在内的前 {P['joint_days']} 天, 你与用户共同完成难度>={P['joint_lv']}的合约", f"{joint_n} 场", f"{P['joint_n']} 场", joint_n >= P["joint_n"]),
            ("II", f"用户在包括今天在内的前 {P['study_days']} 天, 专注时间>={P['study_h']:g}h 并且空闲时间<={P['idle_h']:g}h",
             f"专注 {study_h:.1f}h, 空闲 {idle_h:.1f}h (OIT 只记录了其中 {len(got)} / {P['study_days']} 天)",
             f"专注>={P['study_h']:g}h, 空闲<={P['idle_h']:g}h", study_h >= P["study_h"] and idle_h <= P["idle_h"]),
            ("III", "你与用户互相发送的信息", f"{msgs} 条", f"{P['msgs']} 条", msgs >= P["msgs"]),
            ("IV", "用户今天的专注时长", f"{today_h:.1f}h", f"{P['today_h']:g}h", today_h >= P["today_h"]),
            ("V", "现在是特殊空闲时段", "是" if special else "不是", "是", special),
        ]

    def messages_text(self) -> str:
        """对方输入 [messages] 得到的: 它和用户的聊天记录数 (OIT 统计, 和 [condition] III 数的是同一个数)."""
        return (f"(OIT 系统提供, 用户看不到) 你和用户的聊天记录数: 共 {self.msg_total()} 条 (双方互相发送的信息总数, 由 OIT 统计, 以此为准, 不要自己数也不要编造).")

    def state_text(self) -> str:
        """对方输入 [state] 得到的: 它此刻的日程状态 (这一段的性质 + 结束时间), OIT 按日程实时算; 用户看不到."""
        now = self.now()
        s = self.other_status(now)
        if not s:
            return "(OIT 系统提供, 用户看不到) 你现在没有日程 (或不在任何一段里), 按空闲处理, 没有确定的结束时间."
        kind = s.get("kind") or "idle"
        label = str(s.get("label") or "").strip()
        detail = str(s.get("detail") or "").strip()
        name = f"{label}({detail})" if detail else label
        base = tr(STATUS_TEXT.get(kind, "空闲")).rstrip("中")
        if s.get("joint"):
            nature = f"联合行动 · {base}"               # 合约的同步阶段: 跟着用户的计时器走
        elif kind == "idle":
            # 特殊空闲: 只按"空闲"告诉模型, 免得它在聊天里把"特殊"两个字说出来
            nature = base if routine.seg_class(s) == "special" else (name or base)
        elif kind == "special":
            nature = base                              # 同上: 只当空闲
        else:
            nature = f"{base}({name})" if name and name != base else base
        begin, end = s.get("begin"), s.get("end")
        out = ("(OIT 系统提供, 用户看不到) 你此刻的日程状态 (OIT 按你的日程实时算出, 以此为准, 不要自己推算, 也不要编造): "
               + nature)
        if begin:
            out += f", 从 {begin:%H:%M} 开始"
        if end:
            out += f", 到 {end:%H:%M} 结束 (还剩 {max(0, int((end - now).total_seconds() // 60))} 分钟)"
        else:
            out += ", 没有确定的结束时间"
        return out + "."

    def condition_text(self) -> str:
        rows = self.condition_state()
        lines = [f"{k}.{desc}: 当前 {cur}, 要求 {need}, {'已满足' if ok else '未满足'}" for k, desc, cur, need, ok in rows]
        return ("(OIT 系统提供, 用户看不到) 条件判定结果 (OIT 按用户的真实数据算出, 以此为准, 不要自己推算, 也不要编造数字):\n"
                + "\n".join(lines) + f"\n结论: {'五个条件全部满足' if all(r[4] for r in rows) else '没有全部满足'}.")

    def love(self) -> dict:
        return love_norm(self.cfg.get("love"))

    def related(self) -> bool:
        """已经确立亲密关系了吗."""
        return bool(self.love()["rel"])

    def love_invite(self) -> bool:
        """对方告白了、等用户点 接受 / 拒绝 (聊天页上显示两个按钮)."""
        d = self.love()
        return bool(d["inv"]) and not d["rel"]

    def _love_set(self, **kw):
        d = self.love()
        d.update(kw)
        d["ts"] = max(self._ms(), d["ts"] + 1)
        self.cfg["love"] = love_norm(d)

    def _love_confirm(self, by: str):
        """亲密关系确立了 (by = user: 用户提出、对方 [accept]; ai: 对方告白、用户接受): 记入系统, 同步给别的设备."""
        if self.related():
            return
        self._love_set(rel=self._ms(), by=by, inv=0)
        self.log(f"LOVE 亲密关系确立 (by {by})")
        self.save_chat()
        self._changed()
        self._push()

    def _love_unmark(self):
        key = getattr(self, "_love_key", "")
        if key:
            self.cfg["pro_key"] = "|".join(k for k in str(self.cfg.get("pro_key") or "").split("|") if k and k != key)
            self._love_key = ""

    def _love_check(self, now, segs, done) -> bool:
        """每次进入特殊空闲段 (有日程文件的人设): 按 COND_STRICT 判定; 全部通过 → 让对方向用户告白, 用户在聊天页上选 接受 / 拒绝. 返回 True = 发起了."""
        d = self.love()
        if d["rel"] or d["inv"] or self._ms() < d["no"]:
            return False
        cur = routine.status_in(segs, now)
        if not cur or routine.seg_class(cur) != "special":
            return False
        key = f"L{cur['start']:%Y-%m-%dT%H:%M}"
        if key in done:
            return False
        self._pro_mark(key)                                          # 每个特殊空闲段只判定一次 (不管过没过)
        self.save_chat()
        if (now - cur["start"]).total_seconds() > PROACTIVE_LATE:   # 错过太久 (程序刚打开) 就算了
            self.log(f"LOVE {key} → 不判定 (错过了)")
            return False
        rows = self.condition_state(strict=True)
        bad = [r[0] for r in rows if not r[4]]
        if bad:
            self.log(f"LOVE {key} → 未通过: {','.join(bad)}")
            return False
        self.log(f"LOVE {key} → 全部通过, 让对方告白")
        self._love_key = key
        self._trigger("love", f"{LOVE_TAG} 主动触发的告白条件已经全部满足 (OIT 已按人设里「主动触发的表白」那一段判定, 现在是你的特殊空闲时间). "
                              "但要不要告白由你自己判断: 请根据你的长期记忆和聊天记录, 想想现在告白是否恰当; 如果不恰当 (例如用户是性取向常见的女性, "
                              f"或者用户完全没有表现出过发展亲密关系的意图), 不要告白, 只返回{REFUSE}. "
                              "如果恰当, 请现在按人设向用户表白: 结合你的长期记忆里关于用户的内容和用户的特点, 发言较长 (不受默认字数限制), 前半含蓄, 后半直接 "
                              "(不要问用户是否'谨慎','坚定'); 用户会在界面上选择接受或拒绝.")
        return True

    def love_respond(self, accept: bool) -> str:
        """用户点了 接受 / 拒绝 对方的告白. 返回 "" = 记下了, 否则是原因. 拒绝 → LOVE_NO_DAYS 天内不再判定主动告白."""
        if not self.love_invite():
            return "没有等你回复的告白"
        if accept:
            self._love_set(rel=self._ms(), by="ai", inv=0)
            body = (f"{LOVE_TAG} 用户在界面上点了「接受」你的告白. OIT 已记录: 你们现在是亲密关系. "
                    f"请按人设回应用户一句 (话题和语气自己定, 不要透露日程); 如果实在不方便, 返回{REFUSE}.")
        else:
            self._love_set(inv=0, no=self._ms() + LOVE_NO_DAYS * 86400 * 1000)
            body = (f"{LOVE_TAG} 用户在界面上点了「拒绝」你的告白 ({LOVE_NO_DAYS} 天内 OIT 不会再判定主动告白). "
                    f"请按人设回应用户一句 (话题和语气自己定, 不要透露日程); 如果实在不方便, 返回{REFUSE}.")
        self.log(f"LOVE 用户{'接受' if accept else '拒绝'}了告白")
        if not self.busy and not self._ai_problem() and self.leader():
            self.save_chat()
            self._trigger("love_reply", body)
        else:                                                       # 对方正忙 / 不能请求: 只记一条系统消息, 下次请求时它会看到
            self._add("sys", "告白", self._compose(self.now(), oit_text(body)))
        self.save_chat()
        self._changed()
        self._push()
        return ""

    def _rt_tick(self):
        if not self._rt or time.monotonic() < self._rt_next:
            return
        self._rt_next = time.monotonic() + 30
        try:
            self._rt_sync()
        except Exception as e:      # noqa: BLE001
            self.log(f"ROUTINE sync FAIL {type(e).__name__}: {e}")

    def routine_info(self, tag: str) -> str:
        """对方查询 [模块名] 时, 有日程文件的人设得到的是对方自己的数据 (OIT 按日程实时算出, 只有已经发生的部分); 不是这几个模块 / 没有日程文件 = ""."""
        if tag not in ("timer", "log", "calendar", "stats", "net") or not self._rt:
            return ""
        from comm import comm_data
        st = self._rt_sync()
        if not st:
            return ""
        now = self.now()
        day0 = datetime.combine(now.date(), datetime.min.time())
        prefix = f"{now:%Y-%m}"
        if tag == "log":
            return comm_data.hour_text(st.log_lines(now))
        if tag == "timer":
            ivs = [({"focus": "study", "rest": "break", "sleep": "sleep"}[k], a, now if b >= st.upto else b)
                   for k, a, b, _l in st.segments(day0, now) if k in ("focus", "rest", "sleep")]
            cur = self.other_status(now) or {}
            state = {"focus": "focus", "rest": "break", "sleep": "sleep"}.get(cur.get("kind"), "idle")
            snap = {"state": state, "focus_total_min": st.minutes(day0, now, ("focus",)),
                    "rest_left_min": max(0.0, (cur["end"] - now).total_seconds() / 60) if state == "break" else 0,
                    "focus_cur_min": max(0.0, (now - cur["begin"]).total_seconds() / 60) if state == "focus" else 0}
            return comm_data.timer_text(snap, ivs, now)
        if tag == "calendar":
            out = []
            for kind, name in (("focus", "专注"), ("rest", "休息"), ("idle", "空闲"), ("sleep", "睡眠")):
                days = {}
                for d in st.month_days(now):
                    if kind != "focus" and (now.date() - d).days > 6:
                        continue
                    h = st.day_minutes(d, (kind,)) / 60.0
                    if h > 0:
                        days[d] = h
                lines = [f"  {d:%Y-%m-%d}: {h:.1f} h" for d, h in sorted(days.items())]
                out.append(f"【日历·{name} {prefix}{'' if kind == 'focus' else ' (只有最近 7 天有记录)'}】\n" + ("\n".join(lines) if lines else "  没有记录")
                           + f"\n  合计: {sum(days.values()):.1f} h")
            return "\n\n".join(out)
        if tag == "net":
            return self._rt_net_text(st, now, prefix)
        # stats
        blocks = []
        acts = {}
        for key, (ln, text) in st.cells.items():
            if key.startswith(prefix) and text:
                acts[text] = acts.get(text, 0.0) + ln * 60.0
        blocks.append(f"【统计·整点记录 {prefix}】\n" + comm_data._top(acts))
        blocks.append(self._rt_reasons_text(st, now, prefix))
        for i, name in enumerate(self._rt.devices or ["设备"]):
            win = {}
            for d in st.month_days(now):
                for k, v in st.window_seconds(d, i).items():
                    win[k] = win.get(k, 0.0) + v
            if win:
                blocks.append(f"【统计·窗口 {prefix} · {name}】\n" + comm_data._top(win))
        lines = []
        for i in range(7):
            d = now.date() - timedelta(days=i)
            lines.append(f"  {d:%m-%d} 周{WEEKDAY[d.weekday()]}: 专注 {st.day_minutes(d, ('focus',)):.0f} 分钟, 休息 {st.day_minutes(d, ('rest',)):.0f} 分钟")
        blocks.append("【统计·专注计时 最近 7 天】\n" + "\n".join(lines))
        recs = st.recent_contracts(now)
        blocks.append("【统计·合约记录 最近完成的】\n" + ("\n".join(comm_data._record(r) for r in recs) if recs else "  还没有顺利完成的合约"))
        return "\n\n".join(blocks)

    def _rt_reasons_text(self, st, now, prefix: str) -> str:
        from comm import comm_data
        tot = {}
        for d in st.month_days(now):
            for k, v in st.net_by_reason(d).items():
                tot[k] = tot.get(k, 0.0) + v * 60.0
        return f"【统计·联网理由 {prefix}】\n" + comm_data._top(tot, unit="m")

    def _rt_net_text(self, st, now, prefix: str) -> str:
        from comm import comm_data
        used = sum(st.net_by_reason(now.date()).values()) * 60.0
        quota = routine.NET_QUOTA_S
        total = max(quota, -(-used * 1.2 // 600) * 600)
        return comm_data.net_text("offline", used, total, "代理 (Proxy)", self._rt_reasons_text(st, now, prefix))

    def _check_proactive(self, now, snap: dict, ctx=None):
        if ctx is None and (self.is_muted() or self.joint_live()):         # 联合行动的同步阶段: 对方陪着用户, 不主动找话题
            return
        done = self._pro_done(ctx)
        rt = ctx.get("rt") if ctx else self._rt
        if rt:
            self._check_proactive_rt(now, ctx, rt, done)
            return
        # I. 对方自己正处在空闲 / 休息段里, 而且这段还剩 10 分钟以上: 段开始后 1~3 分钟发一条
        cur = self._cur_free(now, ctx["entries"] if ctx else None)
        if cur:
            begin, left = cur
            key = f"F{begin:%Y-%m-%dT%H:%M}"
            if key not in done:
                due = begin + timedelta(seconds=random.Random(key).randint(*PROACTIVE_DELAY))
                if now >= due:
                    pl = (ctx["probs"].get("long", float(PROACTIVE_P["long"])) if ctx else self.pro_percent("long")) / 100.0
                    self._pro_fire(key, due, now, pl, f"[系统] 按你的日程, 你现在有空 (这段还剩约 {left / 60:.0f} 分钟). "
                                                       f"请根据人设主动给用户发一条消息, 话题自己找; 这条必须发出, 不要返回{REFUSE}.", ctx)
                    return
        # IV. 人设日程里的"睡眠"段: 睡眠开始后 5 分钟, 有概率发一条 (睡眠期间只发这一条, 用户回复按日程拒绝)
        for kind, mins in (ctx["segs"] if ctx else self._segments()):
            if kind != "sleep":
                continue
            begin = datetime.combine(now.date(), datetime.min.time()) + timedelta(minutes=mins)
            key = f"Z{begin:%Y-%m-%dT%H:%M}"
            if key in done:
                continue
            due = begin + timedelta(seconds=PROACTIVE_SLEEP_DELAY)
            if now < due:
                continue
            pct = ctx["probs"].get("sleep", float(PROACTIVE_P["sleep"])) if ctx else self.pro_percent("sleep")
            self._pro_fire(key, due, now, pct / 100.0,
                           f"[系统] 按你的日程, 你进入睡眠约 {PROACTIVE_SLEEP_DELAY // 60} 分钟了. "
                           f"这条是系统触发的: 即使你此刻处于睡眠时段, 也允许你主动给用户发一条消息, 话题自己找 (不要透露日程); "
                           f"发完继续睡, 用户回复时按日程拒绝; 这条必须发出, 不要返回{REFUSE}.", ctx)
            return
        # II / III. 人设日程里每段空闲 / 休息开始前 1~3 分钟, 有概率发一条
        for key, kind, begin in self._schedule_events(now, ctx["segs"] if ctx else None):
            if key in done:
                continue
            due = begin - timedelta(seconds=random.Random(key).randint(*PROACTIVE_LEAD))
            if now < due:
                continue
            mins = max(1, round((begin - now).total_seconds() / 60))
            pct = ctx["probs"].get(kind, float(PROACTIVE_P[kind])) if ctx else self.pro_percent(kind)
            self._pro_fire(key, due, now, pct / 100.0,
                           f"[系统] 按你的日程, 你的{'空闲' if kind == 'idle' else '休息'}时间将在 {begin:%H:%M} 开始 (约 {mins} 分钟后). "
                           f"这条是系统提前触发的: 即使你此刻还处于专注/不能回复的时段, 也允许你现在主动给用户发一条消息, 话题自己找 (不要透露日程); "
                           f"这条必须发出, 不要返回{REFUSE}.", ctx)
            return

    def _check_proactive_rt(self, now, ctx, rt, done):
        """有日程文件的人设: I~IV 同 _check_proactive, 只是时间用当天算出的日程 (带波动; 睡眠开始是实际波动过后的)."""
        seed = ctx["pid"] if ctx else self.pid
        segs = self._rt_segs(now - timedelta(hours=14), now + timedelta(minutes=4), rt, seed, joint=not ctx)
        if ctx is None and self._love_check(now, segs, done):             # 每次进入特殊空闲: 判定主动告白
            return

        def pct(kind):
            return pro_pct(ctx["probs"], kind) if ctx else self.pro_percent(kind)

        cur = routine.status_in(segs, now)
        if cur and cur["kind"] in ("idle", "rest") and routine.seg_class(cur) != "special":      # I. 自己正闲着, 这段还剩 10 分钟以上 (特殊空闲只在开始前发, 见 II / III)
            left = (cur["end"] - now).total_seconds()
            begin = cur["start"]
            key = f"F{begin:%Y-%m-%dT%H:%M}"
            if left >= PROACTIVE_FREE_LEFT and key not in done:
                due = begin + timedelta(seconds=random.Random(key).randint(*PROACTIVE_DELAY))
                if now >= due:
                    self._pro_fire(key, due, now, pct("long") / 100.0, f"[系统] 按你的日程, 你现在有空 (这段还剩约 {left / 60:.0f} 分钟). "
                                                                     f"请根据人设主动给用户发一条消息, 话题自己找; 这条必须发出, 不要返回{REFUSE}.", ctx)
                    return
        for s in segs:                                                    # IV. 睡眠开始 (波动过后的实际时刻) 5 分钟后
            if s["kind"] != "sleep" or s["start"] > now:
                continue
            key = f"Z{s['start']:%Y-%m-%dT%H:%M}"
            if key in done:
                continue
            due = s["start"] + timedelta(seconds=PROACTIVE_SLEEP_DELAY)
            if now < due:
                continue
            self._pro_fire(key, due, now, pct("sleep") / 100.0,
                           f"[系统] 按你的日程, 你进入睡眠约 {PROACTIVE_SLEEP_DELAY // 60} 分钟了. "
                           f"这条是系统触发的: 即使你此刻处于睡眠时段, 也允许你主动给用户发一条消息, 话题自己找 (不要透露日程); "
                           f"发完继续睡, 用户回复时按日程拒绝; 这条必须发出, 不要返回{REFUSE}.", ctx)
            return
        for s in segs:                                                    # II / III. 每段空闲 / 休息 (不含睡眠被延迟的空档) 开始前 1~3 分钟
            if s["kind"] not in ("idle", "rest") or s["gap"] or not (now < s["start"] <= now + timedelta(seconds=PROACTIVE_LEAD[1])):
                continue
            kind = routine.seg_class(s)                                   # idle / rest / key 重要活动 / special 特殊空闲
            key = f"S{s['start']:%Y-%m-%dT%H:%M}{kind[0]}"
            if key in done:
                continue
            due = s["start"] - timedelta(seconds=random.Random(key).randint(*PROACTIVE_LEAD))
            if now < due:
                continue
            mins = max(1, round((s["start"] - now).total_seconds() / 60))
            what = "休息" if kind == "rest" else "空闲" if kind == "idle" else f"空闲 (这一段是「{s['label']}」)"
            self._pro_fire(key, due, now, pct(kind) / 100.0,
                           f"[系统] 按你的日程, 你的{what}时间将在 {s['start']:%H:%M} 开始 (约 {mins} 分钟后). "
                           f"这条是系统提前触发的: 即使你此刻还处于专注/不能回复的时段, 也允许你现在主动给用户发一条消息, 话题自己找 (不要透露日程); "
                           f"这条必须发出, 不要返回{REFUSE}.", ctx)
            return

    def _pro_fire(self, key: str, due, now, p: float, body: str, ctx=None):
        """这个时间点处理一次: 记下来 (不管发没发), 没错过太久 / 摇中了概率 / 最近没发过, 才真的让对方发."""
        self._pro_mark(key, ctx)
        late = (now - due).total_seconds() > PROACTIVE_LATE          # 错过太久 (程序刚打开) 就算了
        hit = p >= 1.0 or random.Random(key + "#p").random() < p
        recent = self._pro_recent(now, ctx)
        who = f" [{ctx['name']}]" if ctx else ""
        if not late and hit and not recent:
            lang = self.pro_lang()
            if lang:
                body += PRO_LANG_NOTE.replace("{lang}", lang)        # 只加在这一条触发消息里: 之后的对话 (用户说的话) 不受影响
            self.log(f"PRO{who} {key} p={p:.2f} → 让对方发" + (f" (语言 {lang})" if lang else ""))
            if ctx:
                self._bg_request(ctx, "proactive", body)
            else:
                self._trigger("proactive", body)
        else:
            self.log(f"PRO{who} {key} p={p:.2f} → 不发 ({'错过了' if late else '概率没中' if not hit else '刚发过'})")
        if ctx:
            self._store(ctx["pid"], ctx["st"])
        else:
            self.save_chat()
            self.save_cfg()
        self._push()
