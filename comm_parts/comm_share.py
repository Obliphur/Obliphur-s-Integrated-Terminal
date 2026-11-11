# -*- coding: utf-8 -*-
"""share data / 起点平移: 请求, 回答, 超时, 同步. MECHANICS 24.13 / 24.14.

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
from comm_parts import comm_readme
from comm_parts.comm_const import *
from comm_parts.comm_schedule import PROACTIVE_P, seg_kind, seg_detail, schedule_entries, schedule_loops, loop_entries, schedule_segments, pro_pct, persona_probs
from comm_parts.comm_reply import CUT_DEFAULT_MIN, CUT_MAX_MIN, HALFWIDTH_SYMBOLS, NET_DEFAULT_MIN, NET_MAX_MIN, WAIT_MAX_MIN, _BRACKET_RE, _CUT_N_RE, _CUT_RE, _EMPTY_WRAP_RE, _NET_RE, _WAIT_RE, clean_reply, cut_minutes, net_minutes, to_halfwidth
from comm_parts.comm_builtin import BUILTIN_PERSONAS, DEFAULT_PERSONA, DEFAULT_BACKGROUND, DEFAULT_ROUTINE, ASSISTANT_PERSONA, ASSISTANT_DETAIL_LINE, BACKGROUND_LEGACIES, ASSISTANT_SLEEP_LINE, ASSISTANT_TITLE
from comm_parts.comm_prompt import REFUSE, SHIFT_TAG, MSGS_TAG, THINK_LONG, THINK_WORDS, THINK_TIME_RE, think_text_reason, JOINT_RULE, rest_function_text, prefix_prompt
from comm_parts.comm_persona import SHIFT_LO, SHIFT_HI, SHIFT_KEEP, MAX_PERSONA, MEM_MAX, PERSONA_EXT, readme_text, pid_of, love_norm, love_line, shift_norm, shift_pairs, hm_text, shift_line, mem_fields, seed_builtin, ensure_personas, list_personas, persona_title, uses_prefix, system_head, PERSONA_HEAD, persona_system, persona_labels, read_persona, ROUTINE_SUFFIX, COMPANION_SEP, companion_base, is_companion_name, companion_files, companions_of, routine_name_of, read_routine, read_persona_full, import_persona
from comm_parts.comm_util import (OIT_TAG, STATE_TAG, COND_TAG, WEEKDAY, MAX_MSG_CHARS, HISTORY_SENT, HISTORY_STEP, HISTORY_CHARS,
                       is_claudia, is_refusal, stamp, urlsplit_host, info_tags, joint_answer, _joint_time, clip_message,
                       cache_layout, oit_text, first_turn, api_messages, sanitize_mem)


class ShareMixin:
    @staticmethod
    def _share_fresh() -> dict:
        return {"on": False, "started": False, "view": "me", "page": "", "pending": "", "last": 0, "pages": []}

    def share_on(self) -> bool:
        return bool(self.share["on"])

    def share_page(self) -> str:
        """现在分享着的页面 (在 SHARE_SKIP_PAGES 里 / 没打开 = "")."""
        return self.share["page"] if self.share["on"] else ""

    def share_view(self) -> str:
        """界面上看谁的数据: "me" 自己 / "ai" 对方."""
        return self.share["view"]

    def share_ai_name(self) -> str:
        """share data 的界面上对方的名字: 人设名 (不带后缀), 没选人设时是 "AI"; 切换按钮 / 标题写成"<名字> 的数据"."""
        return persona_title(self.persona_name()) or "AI"

    def set_share_view(self, view: str):
        self.share["view"] = "ai" if view == "ai" else "me"
        self._changed()

    def share_enable(self, on: bool) -> str:
        """沟通页的 share data 开关. 打开前要能聊天 (选了人设, API 配好); 返回 "" = 好了, 否则是原因. 关掉 = 结束 (看过数据的话调用一次 API 总结)."""
        if on:
            if self.share["on"] or self.share_asking():
                return ""
            why = self.problem()
            if why:
                return why
            st = self.other_status()
            if st and st.get("kind") in HELD_KINDS and not st.get("joint"):
                return tr("{name} {st}, 现在没法征求同意", name=self.share_ai_name(), st=tr(STATUS_TEXT[st["kind"]]))
            self.share_ask = {"pid": self.pid, "ts": self._ms(), "sent": False}      # 用户发起的 share data 要先争取对方同意 (MECHANICS 24.12): 对方回 [Yes] 才真的打开
            self._changed()
            return ""
        if self.share_ask:
            self._share_ask_end()                                                    # 等对方回复的时候又点了一下: 取消
        self.share_close(True)
        return ""

    def shifts(self) -> list:
        return shift_norm(self.cfg.get("shift"))

    def shift_start(self, d=None):
        """d (默认明天) 这一天对方的初始起点 (分钟); 没有日程文件 = None."""
        self._load_schedule()
        if not self._rt:
            return None
        return self._rt.start_for(d or (self.now().date() + timedelta(days=1)))

    def shift_asking(self) -> bool:
        return bool(self.shift_ask) and self.shift_ask.get("pid") == self.pid

    def shift_request(self, minutes: int) -> str:
        """用户请求把对方的日程起点平移到 minutes (从 0:00 起的分钟). 返回 "" = 已发出请求 (等对方回复), 否则是原因."""
        why = self.problem()
        if why:
            return why
        if self.shift_asking():
            return tr("正在等 {name} 回复上一次的起点平移请求", name=self.share_ai_name())
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            return "时间不对"
        if not (SHIFT_LO <= minutes <= SHIFT_HI):
            return tr("新起点要在 {lo} - {hi} 之间", lo=hm_text(SHIFT_LO), hi=hm_text(SHIFT_HI))
        cur = self.shift_start()
        if cur is None:
            return "这个人设没有日程文件, 不能平移起点"
        if cur == minutes:
            return tr("明天的起点已经是 {t} 了", t=hm_text(minutes))
        st = self.other_status()
        if st and st.get("kind") in HELD_KINDS and not st.get("joint"):
            return tr("{name} {st}, 现在没法征求同意", name=self.share_ai_name(), st=tr(STATUS_TEXT[st["kind"]]))
        self.shift_ask = {"pid": self.pid, "ts": self._ms(), "sent": False, "to": minutes}
        self._changed()
        return ""

    def shift_cancel(self):
        if self.shift_ask:
            self._shift_ask_end()

    def _shift_ask_end(self, notice: str = ""):
        self.shift_ask = None
        if notice:
            notice = tr(notice, name=self.share_ai_name())
            self.log("SHIFT ask: " + notice)
            if self.on_share_notice:
                try:
                    self.on_share_notice(notice)
                except Exception:       # noqa: BLE001
                    pass
        self._changed()

    def _shift_commit(self, minutes: int) -> bool:
        """对方同意了: 记下 (明天起生效), 同步给别的设备. 请求发出之后日程文件被换掉 / 范围不对就不记."""
        cur = self.shift_start()
        if cur is None or not (SHIFT_LO <= int(minutes) <= SHIFT_HI):
            return False
        day = self.now().date() + timedelta(days=1)
        rec = {"ts": max(self._ms(), max([e["ts"] for e in self.shifts()] + [0]) + 1), "d": f"{day:%Y-%m-%d}", "s": int(minutes), "f": int(cur)}
        self.cfg["shift"] = shift_norm(self.shifts() + [rec])
        self.log(f"SHIFT 起点 {hm_text(cur)} -> {hm_text(minutes)} ({rec['d']} 起生效)")
        self._load_schedule()
        self.save_chat()
        self._changed()
        self._push()
        return True

    def _shift_answer(self, ans: str):
        """对方回了 [Yes] / [No] (ans = "yes" / "no" / "" 没表态)."""
        a = self.shift_ask
        if not self.shift_asking():
            self.shift_ask = None
            return
        self.shift_ask = None
        if ans == "yes":
            if not self._shift_commit(a["to"]):
                self._shift_ask_end("起点没有平移 (日程文件变了)")
                return
            self._changed()
            if self.on_share_notice:
                try:
                    self.on_share_notice(tr("{name} 同意了, 日程起点明天起平移到 {t}", name=self.share_ai_name(), t=hm_text(a["to"])))
                except Exception:       # noqa: BLE001
                    pass
        elif ans == "no":
            self._shift_ask_end("{name} 拒绝了起点平移")
        else:
            self._shift_ask_end("{name} 没有明确同意, 起点没有平移")

    def _shift_tick(self):
        """每秒: 排着的起点平移请求 (主端发, 对方空下来才发); 太久没回作废."""
        a = self.shift_ask
        if not a:
            return
        if a.get("pid") != self.pid:
            self.shift_ask = None
        elif self._ms() - int(a["ts"]) > SHIFT_ASK_TTL * 1000:
            self._shift_ask_end("{name} 没有回复, 起点没有平移")
        elif not a.get("sent") and not self.busy and not self._ai_problem() and self.leader():
            cur = self.shift_start()
            a["sent"] = True
            self._trigger("shift_ask", (f"{SHIFT_TAG} 用户请求把你的日程起点 (初始起点) 从 {hm_text(cur if cur is not None else 0)} 平移到 {hm_text(a['to'])}: "
                                        f"新起点从明天起生效 (今天的日程不变), 只改日程, 不改你的人设; 起点允许的范围是 {hm_text(SHIFT_LO)} - {hm_text(SHIFT_HI)}, "
                                        "之后的各项活动照常从新起点往后排. 请按人设判断是否同意: 同意就回复 [Yes], 不同意就回复 [No] "
                                        f"(两个标签只写一个; 后面可以按人设加一句话); 如果实在不方便回答, 返回{REFUSE}."))

    def _share_open(self) -> str:
        """真的打开 share data (对方同意了 / 用户接受了对方的邀请). 返回 "" = 好了, 否则是原因."""
        why = self._profile_problem()
        if why:
            return why
        self.share = self._share_fresh()
        self.share["on"] = True
        self._changed()
        return ""

    def share_asking(self) -> bool:
        """用户点了 share data, 正在等对方回 [Yes] / [No]."""
        return bool(self.share_ask) and self.share_ask.get("pid") == self.pid

    def _share_ask_end(self, notice: str = ""):
        self.share_ask = None
        if notice:
            notice = tr(notice, name=self.share_ai_name())              # 指代对方一律用 {name} (人设名)
            self.log("SHARE ask: " + notice)
            if self.on_share_notice:
                try:
                    self.on_share_notice(notice)
                except Exception:       # noqa: BLE001
                    pass
        self._changed()

    def _share_answer(self, ans: str):
        """对方回了 [Yes] / [No] (ans = "yes" / "no" / "" 没表态)."""
        if not self.share_asking():
            self.share_ask = None
            return
        if ans == "yes":
            self.share_ask = None
            self.share = self._share_fresh()
            self.share["on"] = True
            self._changed()
            if self.on_share_open:
                try:
                    self.on_share_open()
                except Exception:       # noqa: BLE001
                    pass
        elif ans == "no":
            self._share_ask_end("{name} 拒绝了 share data")
        else:
            self._share_ask_end("{name} 没有明确同意, share data 没有打开")

    def share_close(self, summarize: bool = True):
        s = self.share
        if not s["on"]:
            return
        pages = [SHARE_PAGE_NAMES.get(p, p) for p in s["pages"]]
        started = bool(s["started"])
        self.share = self._share_fresh()
        self._changed()
        if summarize and started:
            self._share_end = (f"{SHARE_TAG} 用户结束了 share data, 回到了沟通页 (这次看过: {'、'.join(pages) or '没有看数据'}). "
                               "请根据刚才共享的数据 (见上面的共享记录) 总结这次 share data: 按人设简短评价, 对用户说一句话 "
                               f"(话题和语气自己定, 不要透露日程); 如果实在不方便, 返回{REFUSE}.")

    def share_page_changed(self, page: str):
        """app 切页时调用. 回到沟通 = 结束; 设置 / 指令 / 重置 = 暂停 (不分享, 不显示悬浮窗); 别的页 = 开始分享这一页."""
        s = self.share
        if not s["on"]:
            return
        if page == "comm":
            if s["started"]:
                self.share_close(True)
            return
        if page in SHARE_SKIP_PAGES:
            s["page"] = ""
            s["pending"] = ""
            self._changed()
            return
        s["started"] = True
        s["page"] = page
        s["view"] = "me"
        if page not in s["pages"]:
            s["pages"].append(page)
        s["pending"] = page if page in SHARE_DATA_PAGES else ""
        self._changed()

    def _share_tick(self):
        """每秒: 排着的征求同意请求 / 总结请求 (对方空下来再发) 和排着的页面数据 (冷却过了再发)."""
        inv = self.cfg.get("share_inv") or {}
        if inv and self._ms() - int(inv.get("ts") or 0) >= SHARE_INVITE_TTL * 1000:
            self.cfg["share_inv"] = {}                         # 发出的 share data 邀请 5 分钟没人接受: 自动清理 (1.0.428)
            self.save_cfg()
            self._changed()
        a = self.share_ask
        if a:
            if a.get("pid") != self.pid:
                self.share_ask = None
            elif self._ms() - int(a["ts"]) > SHARE_ASK_TTL * 1000:
                self._share_ask_end("{name} 没有回复, share data 没有打开")
            elif not a.get("sent") and not self.busy and not self._ai_problem() and self.leader():
                a["sent"] = True
                self._trigger("share_ask", (f"{SHARE_TAG} 用户想打开 share data: 打开之后, 用户在 OIT 各个页面时, 会把那一页上用户和你自己的数据发给你, "
                                            "页面下方还有一个聊天悬浮窗. 这需要你的同意: 同意就回复 [Yes], 不同意就回复 [No] (两个标签只写一个; "
                                            f"后面可以按人设加一句话); 如果实在不方便回答, 返回{REFUSE}."))
                return
        if self._share_end and not self.busy and not self._ai_problem() and self.leader():
            body, self._share_end = self._share_end, ""
            self._trigger("share_end", body)
            return
        s = self.share
        if s["on"] and s["pending"] and s["pending"] == s["page"] and self._ms() - int(s["last"]) >= SHARE_COOLDOWN * 1000:
            page, s["pending"] = s["pending"], ""
            try:
                self._share_send(page)
            except Exception as e:      # noqa: BLE001
                self.log(f"SHARE send FAIL {type(e).__name__}: {e}")

    def _share_send(self, page: str):
        """把这一页的数据发给对方 (记成一条系统消息, 聊天里不显示; 不调用 API, 用户接着说话时对方就有这些数据)."""
        name, mine, ai = self.share_fn(page)
        if not (mine or ai):
            return
        name = name or SHARE_PAGE_NAMES.get(page, page)
        body = (f"{SHARE_TAG} 用户正在和你一起看 OIT 的「{name}」页 (share data). 下面是这一页的数据: 先是用户的, 再是你自己的 "
                "(OIT 按你的日程实时算出, 只有已经发生的部分). 用户随后会在页面下方的悬浮聊天窗里和你说话, 请结合这些数据回复; 这条本身不需要回复.\n\n"
                f"【用户的数据】\n{mine or '(没有)'}\n\n【你的数据】\n{ai or '(你没有自己的数据)'}")
        self._add("sys", f"共享数据·{name}", self._compose(self.now(), oit_text(body)))
        self.share["last"] = self._ms()
        self.save_chat()
        self._changed()
        self._push()
        self.log(f"SHARE {page} → 对方 (用户 {len(mine)} 字, 对方 {len(ai)} 字)")

    def _share_invite_make(self):
        self.cfg["share_inv"] = {"pid": self.pid, "ts": self._ms()}
        self.save_cfg()
        self._changed()

    def share_invite(self) -> bool:
        """对方发了 [share data], 等用户点 接受 / 拒绝 (聊天页上显示两个按钮)."""
        inv = self.cfg.get("share_inv") or {}
        return bool(inv) and inv.get("pid") == self.pid and not self.share["on"] and self._ms() - int(inv.get("ts") or 0) < SHARE_INVITE_TTL * 1000

    def share_respond(self, accept: bool) -> str:
        if not self.share_invite():
            return "没有等你回复的 share data 邀请"
        self.cfg["share_inv"] = {}
        self.save_cfg()
        if accept:
            why = self._share_open()
            if why:
                return why
            note = f"{SHARE_TAG} 用户接受了你的 share data 请求: 之后用户在 OIT 各个页面时, 你会收到那一页上用户和你自己的数据."
        else:
            note = f"{SHARE_TAG} 用户拒绝了你的 share data 请求."
        self._add("sys", "share data", self._compose(self.now(), oit_text(note)))
        self.save_chat()
        self._changed()
        self._push()
        if accept and self.on_share_open:
            try:
                self.on_share_open()
            except Exception:       # noqa: BLE001
                pass
        return ""

    def share_ai_view(self, page: str) -> dict:
        """界面上"看对方的数据"时这一页显示的内容 ({} = 没有): {"title", "blocks": [...]}.
        blocks: {"t": "card", "dot", "lines": [...]} / {"t": "rows", "title", "rows": [[左, 右, 占比 or None]]} / {"t": "cal", "year", "month", "today", "days": {kind: {日期: 小时}}}.
        只有已经发生的部分 (整点记录只给结束了的格子)."""
        st = self._rt_sync() if self._rt else None
        if not st or page not in SHARE_DATA_PAGES:
            return {}
        now = self.now()
        day0 = datetime.combine(now.date(), datetime.min.time())
        prefix = f"{now:%Y-%m}"
        title = SHARE_PAGE_NAMES[page]

        def hm(m):
            m = int(round(max(0.0, m)))
            return f"{m // 60} 小时 {m % 60} 分钟" if m >= 60 else f"{m} 分钟"

        def top(d, unit="h"):
            rows = sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:30]
            mx = max([v for _k, v in rows] or [1.0]) or 1.0
            return [[k or "空白", (f"{v / 3600:.1f} h" if unit == "h" else f"{v / 60:.0f} 分钟"), v / mx] for k, v in rows]

        if page == "timer":
            cur = self.other_status(now) or {}
            kind = cur.get("kind") or "idle"
            if kind == "focus":
                big = f"{max(0.0, (now - cur['begin']).total_seconds() / 60):.2f} min"
            elif kind == "rest":
                big = tr("剩余休息 ") + f"{max(0.0, (cur['end'] - now).total_seconds() / 60):.1f} min"
            else:
                big = "—"
            focus_h = st.minutes(day0, now, ("focus",)) / 60.0
            ivs = []
            for k, a, b, _l in st.segments(day0, now):
                if k in ("focus", "rest", "sleep"):
                    live = b >= st.upto
                    ivs.append([f"{a:%H:%M}-{'现在' if live else f'{b:%H:%M}'} {STATUS_TEXT[k][:2]}", hm(((now if live else b) - a).total_seconds() / 60), None])
            return {"title": title, "blocks": [
                {"t": "card", "dot": STATUS_COLORS.get(kind, "#9E9E9E"), "lines": [STATUS_TEXT.get(kind, "空闲"), big, f"今日: {focus_h:.1f} h"]},
                {"t": "rows", "title": "今天的每一段", "rows": ivs or [["今天还没有记录", "", None]]}]}
        if page == "log":
            rows = [[ln.split(" ", 1)[0], ln.split(" ", 1)[1] if " " in ln else "", None] for ln in st.log_lines(now)]
            return {"title": title, "blocks": [{"t": "rows", "title": "今天的整点记录", "rows": rows or [["今天还没有记录", "", None]]}]}
        if page == "cal":
            days = {}
            for kind in ("focus", "rest", "idle", "sleep"):
                d = {}
                for day in st.month_days(now):
                    if kind != "focus" and (now.date() - day).days > 6:
                        continue
                    h = st.day_minutes(day, (kind,)) / 60.0
                    if h > 0:
                        d[f"{day:%Y-%m-%d}"] = round(h, 2)
                days[kind] = d
            return {"title": title, "blocks": [{"t": "cal", "year": now.year, "month": now.month, "today": f"{now:%Y-%m-%d}", "days": days}]}
        acts = {}
        for key, (ln, text) in st.cells.items():
            if key.startswith(prefix) and text:
                acts[text] = acts.get(text, 0.0) + ln * 60.0
        reasons = {}
        for day in st.month_days(now):
            for k, v in st.net_by_reason(day).items():
                reasons[k] = reasons.get(k, 0.0) + v * 60.0
        blocks = [{"t": "rows", "title": f"整点记录 {prefix}", "rows": top(acts) or [["没有记录", "", None]]},
                  {"t": "rows", "title": f"联网理由 {prefix}", "rows": top(reasons, "m") or [["没有记录", "", None]]}]
        for i, name in enumerate(self._rt.devices or ["设备"]):
            win = {}
            for day in st.month_days(now):
                for k, v in st.window_seconds(day, i).items():
                    win[k] = win.get(k, 0.0) + v
            if win:
                blocks.append({"t": "rows", "title": f"窗口 {prefix} · {name}", "rows": top(win)})
        lines = []
        for i in range(7):
            d = now.date() - timedelta(days=i)
            lines.append([f"{d:%m-%d} 周{WEEKDAY[d.weekday()]}", f"专注 {st.day_minutes(d, ('focus',)):.0f} 分钟, 休息 {st.day_minutes(d, ('rest',)):.0f} 分钟", None])
        blocks.append({"t": "rows", "title": "专注计时 最近 7 天", "rows": lines})
        from comm_parts import comm_data
        recs = st.recent_contracts(now)
        blocks.append({"t": "rows", "title": "合约记录 最近完成的",
                       "rows": [[r["end"][5:16].replace("T", " "), comm_data._record(r).strip(), None] for r in recs] or [["还没有顺利完成的合约", "", None]]})
        return {"title": title, "blocks": blocks}
