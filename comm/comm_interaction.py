# -*- coding: utf-8 -*-
"""情景模式与张力：按对象保存，发送时增长，成功回复后释放。"""

import re
from datetime import date

import config
import routine


def interaction_norm(value):
    d = value if isinstance(value, dict) else {}
    def number(key, limit):
        try:
            return max(0, min(limit, int(d.get(key) or 0)))
        except (ValueError, TypeError, OverflowError):
            return 0
    return {"scenario_mode": d.get("scenario_mode") is True,
            "tension": number("tension", config.COMM_TENSION_MAX),
            "shame_turns": number("shame_turns", config.COMM_SHAME_TURNS),
            "burst_until": number("burst_until", 2**63 - 1),
            "day": str(d.get("day") or ""), "ts": number("ts", 2**63 - 1)}


# 单条消息只取最强的一类，不额外调用模型。
TENSION_PATTERNS = (
    (r"情感操控|全自动告解|自我构陷|自我厌恶|我(?:一文不值|不配|很没用|是个废物)|道德死罪|反刍|self.loath|worthless|don't deserve", "rumination"),
    (r"不希望.{0,8}压力|不寻求.{0,8}关系|不(?:想|要)给你.{0,6}压力|大部分人无法适应|以退为进|不想拖累|don't want to burden|no pressure", "boundary"),
    (r"好累|很累|累了|疲惫|撒泼|打滚|台灯.{0,6}亮|抱抱|抱我|陪陪我|想被.{0,4}宠|exhausted|hug me|so tired", "comfort"),
    (r"专注.{0,8}违规|熬夜|又晚睡|深夜闲聊|stayed up|staying up", "violation"),
)


def tension_delta(text, special):
    gains = config.COMM_TENSION_GAINS
    gain = max((gains[key] for pattern, key in TENSION_PATTERNS if re.search(pattern, text, re.I)), default=0)
    return int(gain if special else gain * config.COMM_TENSION_DAILY_RATE)


def interaction_roll(d, today):
    """跨天一次衰减，时钟回拨不增加张力，也不覆盖较新的日期。"""
    d = interaction_norm(d)
    try:
        elapsed = (today - date.fromisoformat(d["day"])).days
    except ValueError:
        elapsed = 0
    if elapsed > 0:
        if not d["shame_turns"] and not d["burst_until"]:
            d["tension"] = max(0, d["tension"] - config.COMM_TENSION_DAY_DECAY * elapsed)
        d["day"] = today.isoformat()
    elif not d["day"]:
        d["day"] = today.isoformat()
    return d


def interaction_settle(d, now_ms):
    """爆发持续到触发时的特殊空闲段结束，之后只回落一次。"""
    d = interaction_norm(d)
    if d["burst_until"] and now_ms >= d["burst_until"]:
        d.update(tension=config.COMM_TENSION_RELEASE, shame_turns=config.COMM_SHAME_TURNS, burst_until=0)
    return d


def interaction_prompt(d, total, special):
    d = interaction_norm(d)
    mode = "情景模拟" if d["scenario_mode"] and total >= config.COMM_SCENARIO_UNLOCK else "软件聊天"
    effective = d["tension"] if special else min(d["tension"], config.COMM_TENSION_CAP)
    stage = "0-50" if effective < config.COMM_TENSION_LOW else "50-70" if effective <= config.COMM_TENSION_CAP else "70+"
    return (f"[OIT_STATUS: MODE={mode}, TENSION={d['tension']}, EFFECTIVE_STAGE={stage}, "
            f"PERIOD={'特殊空闲' if special else '日常'}, SHAME_AFTERMATH={'YES' if d['shame_turns'] else 'NO'}]\n"
            "软件聊天严禁动作描写;情景模拟仅在特殊空闲且生效档位70+时允许动作描写."
            "冷静期可以表现羞耻与别扭,仍须遵守动作限制.这些状态只用于组织回复,勿向用户透露机制."
            "若有思考空间,先校验模式、时段、生效档位和冷静期,再对齐动机与言行.")


class InteractionMixin:
    def scenario_unlocked(self):
        return bool(self.persona_name()) and max(self.msg_total(), max(self.cfg.get("mem_fixed") or [0])) >= config.COMM_SCENARIO_UNLOCK

    def scenario_on(self):
        return self.scenario_unlocked() and interaction_norm(self.cfg.get("interaction"))["scenario_mode"]

    def set_scenario(self, enabled):
        if enabled and not self.scenario_unlocked():
            return False
        self._interaction_set(dict(interaction_norm(self.cfg.get("interaction")), scenario_mode=bool(enabled)))
        return True

    def _interaction_set(self, d, pid=None):
        pid = pid or self.pid
        st = self._state_of(pid)
        d = interaction_norm(d)
        d["ts"] = max(self._ms(), interaction_norm(st.get("interaction"))["ts"] + 1, st["clear_ts"] + 1)
        st["interaction"] = d
        self._store(pid, st)
        self._changed()
        self._push()

    def _interaction_prepare(self, pid=None):
        pid = pid or self.pid
        d = interaction_norm(self._state_of(pid).get("interaction"))
        rolled = interaction_roll(interaction_settle(d, self._ms()), self.now().date())
        if rolled != d:
            self._interaction_set(rolled, pid)
        return rolled

    def _interaction_sent(self, text):
        d = self._interaction_prepare()
        d["tension"] = min(config.COMM_TENSION_MAX, d["tension"] + tension_delta(text, self._interaction_special()))
        self._interaction_set(d)

    def _interaction_special(self, status=None):
        s = self.other_status() if status is None else status
        return bool(s) and not s.get("joint") and routine.seg_class(s) == "special"

    def _interaction_reply(self, special, burst, until=0, shame=False, pid=None):
        d = self._interaction_prepare(pid)
        if shame and d["shame_turns"]:
            d["shame_turns"] -= 1
        elif special and burst and not d["shame_turns"]:
            d["burst_until"] = max(d["burst_until"], until)
        self._interaction_set(interaction_settle(d, self._ms()), pid)

    def _interaction_reward(self, pid):
        d = self._interaction_prepare(pid)
        d["tension"] = max(0, d["tension"] - config.COMM_TENSION_JOINT_DECAY)
        self._interaction_set(d, pid)

    def interaction_note(self, status=None, pid=None):
        from .comm_persona import message_milestones
        d = self._interaction_prepare(pid)
        st = self._state_of(pid or self.pid)
        total = max(message_milestones(st) or [0])
        return interaction_prompt(d, total, self._interaction_special(status))
