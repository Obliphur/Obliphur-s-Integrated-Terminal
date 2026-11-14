# -*- coding: utf-8 -*-
"""情景模式与张力：按对象保存，模型判断增长，特殊空闲结束后释放。"""

import re
import json
from datetime import date, datetime

import config
import routine
from .comm_const import KEEP


def interaction_norm(value):
    d = value if isinstance(value, dict) else {}
    def number(key, limit):
        try:
            return max(0, min(limit, int(d.get(key) or 0)))
        except (ValueError, TypeError, OverflowError):
            return 0
    pending = d.get("pending") if isinstance(d.get("pending"), dict) else {}
    days = d.get("burst_days")
    if not isinstance(days, list):
        # 旧存档的增长窗口只在爆发开始时创建，可还原最近一次触发日期。
        gain_until = number("gain_until", 2**63 - 1)
        try:
            days = [datetime.fromtimestamp(gain_until / 1000 - config.COMM_TENSION_BURST_GAIN_SECONDS).date().isoformat()] if gain_until else []
        except (ValueError, OverflowError, OSError):
            days = []
    return {"scenario_mode": d.get("scenario_mode") is True,
            "pending": {k: v for k, v in list(pending.items())[-KEEP:] if isinstance(k, str) and isinstance(v, bool)},
            "tension": number("tension", config.COMM_TENSION_MAX),
            "shame_turns": number("shame_turns", config.COMM_SHAME_TURNS),
            "burst_until": number("burst_until", 2**63 - 1),
            "gain_until": number("gain_until", 2**63 - 1),
            "burst_days": sorted({s for s in days if isinstance(s, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s)})[-2:],
            "morning_day": str(d.get("morning_day") or ""),
            "day": str(d.get("day") or ""), "ts": number("ts", 2**63 - 1)}


_TENSION_TAG = re.compile(r"[\[［]\s*tension\b[^\[\]［］\n]*[\]］]", re.I)
_TENSION_GAIN = re.compile(r"[\[［]\s*tension\s*[:：]\s*(\d{1,5})\s*[:：]\s*([+-]?\d{1,6})\s*[\]］]", re.I)


def tension_clean(reply):
    """内部张力标记不进入聊天、语音或后续历史。"""
    return _TENSION_TAG.sub("", reply).strip()


def tension_model_gain(reply, index, special):
    """同一条只取一个有效增量，并沿用日常减半与单条上限。"""
    gain = max((int(m[2]) for m in _TENSION_GAIN.finditer(reply) if int(m[1]) == index), default=0)
    gain = max(0, min(max(config.COMM_TENSION_GAINS.values()), gain))
    return int(gain if special else gain * config.COMM_TENSION_DAILY_RATE)


def interaction_gain(d, gain, now_ms):
    """爆发后按绝对时刻限制增长，与日常减半叠加。"""
    return int(gain * config.COMM_TENSION_BURST_GAIN_RATE) if now_ms < d.get("gain_until", 0) else gain


def interaction_roll(d, today):
    """跨天一次衰减，时钟回拨不增加张力，也不覆盖较新的日期。"""
    d = interaction_norm(d)
    try:
        elapsed = (today - date.fromisoformat(d["day"])).days
    except ValueError:
        elapsed = 0
    if elapsed > 0:
        if not d["shame_turns"] and not d["burst_until"]:
            for _ in range(elapsed):
                if not d["tension"]:
                    break
                decay = config.COMM_TENSION_DAY_DECAY
                if config.COMM_TENSION_LOW <= d["tension"] <= config.COMM_TENSION_CAP:
                    decay *= 2
                d["tension"] = max(0, d["tension"] - decay)
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
            "软件聊天、日常时段或生效档位0-50严禁动作描写;情景模拟且处于特殊空闲时,50-70允许柔和、克制、有边界的动作,尊重用户意愿,不强迫接触;70+才允许更强烈的动作描写."
            "若MODE=情景模拟,你知道这是情景模拟,而不是现实中的动作;双方知情的动作描写均为虚构,不得声称在现实中实际发生."
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

    def _interaction_sent(self, text, message=None):
        d = self._interaction_prepare()
        special = self._interaction_special()
        if message and text.strip():
            d["pending"][message["uid"]] = special
        self._interaction_set(d)

    def _interaction_violation(self):
        """真实联合行动违规直接结算，不从聊天文本推断。"""
        d = self._interaction_prepare()
        gain = config.COMM_TENSION_GAINS["violation"]
        if not self._interaction_special():
            gain = int(gain * config.COMM_TENSION_DAILY_RATE)
        d["tension"] = min(config.COMM_TENSION_MAX, d["tension"] + interaction_gain(d, gain, self._ms()))
        self._interaction_set(d)

    def _interaction_judge_note(self, kind):
        d = self._interaction_prepare()
        self._interaction_candidates = [(m["uid"], d["pending"][m["uid"]], m["text"])
            for m in self.messages if kind in ("user", "retry") and m.get("role") == "me" and m.get("uid") in d["pending"]]
        if not self._interaction_candidates:
            return "本次无需判断张力增量，不要输出 [tension:序号:增量]。"
        items = [{"序号": i, "用户消息": text} for i, (_uid, _special, text) in enumerate(self._interaction_candidates, 1)]
        return ("请结合人设、上下文和以下用户消息的语义判断是否应增加张力，普通交流无需增加。"
                f"为每条选择0至{max(config.COMM_TENSION_GAINS.values())}的整数基础增量，在正常回复末尾附加 [tension:序号:增量]；"
                f"0表示不增加，可省略。自我构陷参考{config.COMM_TENSION_GAINS['rumination']}、边界试探{config.COMM_TENSION_GAINS['boundary']}、"
                f"疲惫撒娇{config.COMM_TENSION_GAINS['comfort']}、违规{config.COMM_TENSION_GAINS['violation']}，也可自行判断其他情绪。"
                "系统会按发送时段处理日常减半，并处理爆发后24小时的增长减半，无需你减半；不得给其他历史消息加值，不要向用户解释标记。"
                "本轮仍按给定生效档位回复，增量从下次请求生效。待判断消息：" + json.dumps(items, ensure_ascii=False))

    def _interaction_judged(self, reply):
        d = self._interaction_prepare()
        for i, (uid, special, _text) in enumerate(getattr(self, "_interaction_candidates", []), 1):
            if uid in d["pending"]:
                gain = interaction_gain(d, tension_model_gain(reply, i, special), self._ms())
                d["tension"] = min(config.COMM_TENSION_MAX, d["tension"] + gain)
                del d["pending"][uid]
        self._interaction_set(d)

    def _interaction_special(self, status=None):
        s = self.other_status() if status is None else status
        return bool(s) and not s.get("joint") and routine.seg_class(s) == "special"

    def _interaction_reply(self, special, burst, until=0, shame=False, pid=None):
        d = self._interaction_prepare(pid)
        if shame and d["shame_turns"]:
            d["shame_turns"] -= 1
        elif special and burst and not d["shame_turns"]:
            if not d["burst_until"] and until:
                d["gain_until"] = max(d["gain_until"], self._ms() + config.COMM_TENSION_BURST_GAIN_SECONDS * 1000)
                d["burst_days"] = sorted(set(d["burst_days"] + [self.now().date().isoformat()]))[-2:]
            d["burst_until"] = max(d["burst_until"], until)
        self._interaction_set(interaction_settle(d, self._ms()), pid)

    def interaction_note(self, status=None, pid=None):
        from .comm_persona import message_milestones
        d = self._interaction_prepare(pid)
        st = self._state_of(pid or self.pid)
        total = max(message_milestones(st) or [0])
        return interaction_prompt(d, total, self._interaction_special(status))
