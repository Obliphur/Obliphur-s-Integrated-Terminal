# -*- coding: utf-8 -*-
"""清空聊天记录 / 重建状态.

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


class MiscMixin:
    def queue_focus_notice(self, text):
        """处理请求的设备发布提示, 由焦点设备显示, 不改变聊天主端."""
        import uuid
        self.focus_notices[uuid.uuid4().hex] = {"ts": self._ms(), "text": text}

    def take_focus_notices(self):
        """仅取一次最近的提示; 过期事件及去重标记一起清理."""
        now = self._ms()
        self.focus_notices = {k: v for k, v in self.focus_notices.items() if 0 <= now - v["ts"] <= 90_000}
        self._focus_notice_seen.intersection_update(self.focus_notices)
        if not self.focus_device():
            return []
        out = [v["text"] for k, v in self.focus_notices.items()
               if k not in self._focus_notice_seen and not v.get("shown")]
        for v in self.focus_notices.values():
            v["shown"] = True
        self._focus_notice_seen.update(self.focus_notices)
        return out

    def clear(self):
        """清空聊天记录 (界面上的按钮, 或者在聊天框里发 /clear): 以后不再显示, 也不再发给模型; 别的设备也一起清空."""
        self.cfg["clear_ts"] = max(self._ms(), self._last_ts)
        self.messages = []
        self.unread = 0
        self._epoch += 1
        self._follow_at = 0                # 清空前排着的第二次调用不要了
        self._follow_msg = ""
        self.error = ""
        self._catchup = False
        self.cfg["retry"] = {}
        self.cfg["mem_auto"] = ""          # 记忆卡跟着聊天一起清掉
        self.cfg["mem_upto"] = 0
        self.cfg["mem_ts"] = self._ms()
        self.cfg["love"] = love_norm({"ts": self._ms()})   # 新对话: 亲密关系 / 告白 / 信息条数也跟着记忆卡一起清掉 (别的设备一起)
        self.cfg["msg_gone"] = 0
        jj = self.cfg.get("joint")
        if jj and (not jj.get("pid") or jj.get("pid") == self.pid):     # 新对话: 联合行动也作废 (别的设备一起); 清空的是别的对象的聊天 (联合行动属于另一个对象) 不动它 (1.0.404)
            self.cfg["joint"] = {"ts": max(self._ms(), int(jj.get("ts") or 0) + 1)}
        self.joint_draft = {}
        if self.cfg.get("net"):                            # 新对话: 限时网络也作废 (别的设备一起)
            self.cfg["net"] = {"ts": max(self._ms(), int(self.cfg["net"].get("ts") or 0) + 1)}
        self.save_cfg()
        self.save_chat()
        self.reload_schedule("新对话")
        self._changed()
        self._push()
