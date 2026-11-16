# -*- coding: utf-8 -*-
"""情景模式与张力：按对象保存，模型判断增长，特殊空闲结束后释放。"""

import re
import json
import math
from datetime import datetime

import config
from comm.comm_rules import tension_rules, tension_event
import routine
from i18n import tr
from .comm_const import KEEP


def interaction_tension(value):
    """兼容旧整数与小数字段，排除损坏存档中的非有限数。"""
    try:
        value = float(value or 0)
        return max(0, min(config.COMM_TENSION_MAX, value)) if math.isfinite(value) else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def interaction_norm(value):
    d = value if isinstance(value, dict) else {}
    def number(key, limit):
        try:
            return max(0, min(limit, int(d.get(key) or 0)))
        except (ValueError, TypeError, OverflowError):
            return 0
    pending = d.get("pending") if isinstance(d.get("pending"), dict) else {}
    cooldowns = d.get("gain_cooldowns") if isinstance(d.get("gain_cooldowns"), dict) else {}
    clean_cooldowns = {}
    for key in cooldowns:
        if not isinstance(key, str) or not re.fullmatch(r"[a-z_]{1,32}", key):
            continue
        try:
            until = max(0, min(2**63 - 1, int(cooldowns.get(key) or 0)))
        except (ValueError, TypeError, OverflowError):
            continue
        if until:
            clean_cooldowns[key] = until
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
            "tension": interaction_tension(d.get("tension")),
            "shame_turns": number("shame_turns", config.COMM_SHAME_TURNS),
            "burst_until": number("burst_until", 2**63 - 1),
            "burst_sleep_after": number("burst_sleep_after", 2**63 - 1),
            "gain_until": number("gain_until", 2**63 - 1),
            "gain_cooldowns": clean_cooldowns,
            "gain_chain_until": number("gain_chain_until", 2**63 - 1),
            "burst_days": sorted({s for s in days if isinstance(s, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s)})[-2:],
            "morning_day": str(d.get("morning_day") or ""),
            "special_until": number("special_until", 2**63 - 1),
            "special_spoke": d.get("special_spoke") is True,
            "last_special_begin": number("last_special_begin", 2**63 - 1),
            "special_entry_tension": interaction_tension(d.get("special_entry_tension")),
            "special_7m_gain_count": number("special_7m_gain_count", 2**31 - 1),
            "special_7m_lured_seg": number("special_7m_lured_seg", 2**63 - 1),
            "skip_decay": d.get("skip_decay") is True,
            "decay_at": number("decay_at", 2**63 - 1),
            "decay_hold_until": number("decay_hold_until", 2**63 - 1),
            "day": str(d.get("day") or ""), "ts": number("ts", 2**63 - 1)}


_TENSION_TAG = re.compile(r"[\[［]\s*tension\b[^\[\]［］\n]*[\]］]", re.I)
_BURST_TAG = re.compile(r"[\[［]\s*burst\b[^\[\]［］\n]*[\]］]", re.I)
_BURST_END = re.compile(r"[\[［]\s*burst\s*[:：]\s*end\s*[\]］]", re.I)
_TENSION_GAIN = re.compile(r"[\[［]\s*tension\s*[:：]\s*(\d{1,5})\s*[:：]\s*(?:([a-z_]+)\s*[:：]\s*)?([+-]?\d{1,6})\s*[\]］]", re.I)


def tension_clean(reply):
    """内部张力标记不进入聊天、语音或后续历史。"""
    return _BURST_TAG.sub("", _TENSION_TAG.sub("", reply)).strip()


def tension_model_events(reply, index, special, cooldowns=None, now_ms=0, conditions=None):
    """各条件按基础增量降序分别结算，重复条件只取最大值；旧标记归入其他情绪。"""
    gains = {}
    for m in _TENSION_GAIN.finditer(reply):
        condition = (m[2] or "other").lower()
        if int(m[1]) != index or (conditions is not None and condition not in conditions):
            continue
        if now_ms < (cooldowns or {}).get(condition, 0):
            continue
        gain = max(0, min(config.COMM_TENSION_GAIN_MAX, int(m[3])))
        gains[condition] = max(gains.get(condition, 0), gain)
    return [(condition, int(gain if special else gain * config.COMM_TENSION_DAILY_RATE))
            for condition, gain in sorted(gains.items(), key=lambda item: -item[1]) if gain > 0]


def tension_model_gain(reply, index, special, conditions=None):
    return sum(gain for _condition, gain in tension_model_events(reply, index, special, conditions=conditions))


def interaction_gain(d, gain, now_ms):
    """先应用条件连锁倍率，再应用爆发后减半；日常倍率由调用方处理。"""
    if now_ms < d.get("gain_chain_until", 0):
        gain = int(gain * config.COMM_TENSION_CHAIN_RATE)
    return int(gain * config.COMM_TENSION_BURST_GAIN_RATE) if now_ms < d.get("gain_until", 0) else gain


def interaction_add(d, condition, gain, now_ms):
    """成功加值才占用该条件的三小时窗口；真实违规与模型违规共用冷却。"""
    gain = interaction_gain(d, gain, now_ms)
    if gain <= 0 or now_ms < d["gain_cooldowns"].get(condition, 0):
        return
    d["tension"] = min(config.COMM_TENSION_MAX, d["tension"] + gain)
    d["gain_cooldowns"][condition] = now_ms + config.COMM_TENSION_GAIN_COOLDOWN_SECONDS * 1000
    d["gain_chain_until"] = max(d.get("gain_chain_until", 0), now_ms + config.COMM_TENSION_CHAIN_SECONDS * 1000)


def interaction_roll(d, now_ms):
    """每十五分钟结算小数衰减；离线补算，暂停和回拨不补扣。"""
    d = interaction_norm(d)
    if not d["decay_at"]:
        d["decay_at"] = now_ms
    if d["skip_decay"]:
        d["decay_hold_until"] = max(d["decay_hold_until"], now_ms + 86400000)
        d["skip_decay"] = False
    step = config.COMM_TENSION_DECAY_SECONDS * 1000
    if d["shame_turns"] or d["burst_until"] or (d["special_until"] and not d["special_spoke"]):
        d["decay_at"] += max(0, (now_ms - d["decay_at"]) // step) * step
    start = max(d["decay_at"], d["decay_hold_until"])
    ticks = max(0, (now_ms - start) // step)
    for _ in range(ticks):
        if d["tension"] <= 0:
            break
        rate = config.COMM_TENSION_DAY_DECAY
        if d["tension"] > config.COMM_TENSION_CAP:
            rate = config.COMM_TENSION_HIGH_DAY_DECAY
        elif d["tension"] >= config.COMM_TENSION_LOW:
            rate *= 2
        d["tension"] = max(0, round(d["tension"] - rate * step / 86400000, 10))
    if ticks:
        d["decay_at"] = start + ticks * step
    return d


def interaction_settle(d, now_ms):
    """仅结算特殊空闲；burst_until 非零表示爆发，旧截止时刻不再自动结束。"""
    d = interaction_norm(d)
    if d["special_until"] and now_ms >= d["special_until"]:
        if not d["special_spoke"] and not d["burst_until"]:
            d["decay_hold_until"] = max(d["decay_hold_until"], d["special_until"] + 86400000)
        d.update(special_until=0, special_spoke=False)
    return d


def interaction_end(d, reply, burst, now_ms):
    """仅正常回复可结束请求时仍生效的同次爆发，新冷静期从下一轮开始。"""
    d = interaction_norm(d)
    if burst and d["burst_until"] == burst and _BURST_END.search(reply):
        d.update(tension=config.COMM_TENSION_RELEASE, shame_turns=config.COMM_SHAME_TURNS,
                 burst_until=0, decay_at=max(d["decay_at"], now_ms))
    return d


def interaction_prompt(d, total, special):
    d = interaction_norm(d)
    special = special or bool(d["burst_until"])
    mode = "情景模拟" if d["scenario_mode"] and total >= config.COMM_SCENARIO_UNLOCK else "软件聊天"
    effective = d["tension"] if special and d["burst_until"] else min(d["tension"], config.COMM_TENSION_CAP)
    stage = "0-50" if effective < config.COMM_TENSION_LOW else "50-90" if effective <= config.COMM_TENSION_CAP else "90+"
    return (f"[OIT_STATUS: MODE={mode}, TENSION={d['tension']}, EFFECTIVE_STAGE={stage}, "
            f"BURST={'YES' if d['burst_until'] else 'NO'}, PERIOD={'特殊空闲' if special else '日常'}, SHAME_AFTERMATH={'YES' if d['shame_turns'] else 'NO'}]\n"
            "软件聊天、日常时段或生效档位0-50严禁动作描写;情景模拟且处于特殊空闲时,50-90允许柔和、克制、有边界的动作,尊重用户意愿,不强迫接触;90+才允许更强烈的动作描写."
            "若MODE=情景模拟,你知道这是情景模拟,而不是现实中的动作;双方知情的动作描写均为虚构,不得声称在现实中实际发生."
            "冷静期可以表现羞耻与别扭,仍须遵守动作限制.这些状态只用于组织回复,勿向用户透露机制."
            + tr("TENSION超过90本身不代表爆发,未触发爆发时仍严格按EFFECTIVE_STAGE=50-90表现.")
            + tr("情景模拟中，括号内的补充说明只描写动作，用词、语气与句式须与当前人设文件规定的语言风格一致；想法与态度直接通过不带括号的语言表达，不放进括号。")
            + tr("BURST=YES时,由你结合上下文和人设决定爆发是否结束,不设固定时限;决定结束时在正常回复末尾附加[burst:end],否则不输出该标记.结束标记生效后张力降至20并进入两轮冷静期;本条回复仍按当前状态组织,后续回复遵守新状态.不得向用户透露标记或机制.")
            + "若有思考空间,先校验模式、时段、生效档位和冷静期,再对齐动机与言行.")


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

    def _interaction_prepare(self, pid=None, status=None):
        pid = pid or self.pid
        d = interaction_norm(self._state_of(pid).get("interaction"))
        rolled = interaction_roll(interaction_settle(d, self._ms()), self._ms())
        if status is None and pid == self.pid:
            status = self.other_status()
        if status is not None:
            if self._interaction_special(status):
                begin = int(status["begin"].timestamp() * 1000)
                if rolled["last_special_begin"] != begin:
                    rolled.update(last_special_begin=begin, special_entry_tension=rolled["tension"], special_7m_gain_count=0)
            elif rolled["last_special_begin"]:
                rolled.update(last_special_begin=0, special_entry_tension=0, special_7m_gain_count=0)
        if status and self._interaction_special(status) and rolled["tension"] > config.COMM_TENSION_CAP:
            until = int(status["end"].timestamp() * 1000)
            if rolled["special_until"] != until:
                begin = int(status["begin"].timestamp() * 1000)
                spoke = any(m["role"] == "me" and str(m.get("text") or "").strip() and begin <= int(m["ts"]) < until
                            for m in self._state_of(pid)["messages"])
                rolled.update(special_until=until, special_spoke=spoke)
        if rolled != d:
            self._interaction_set(rolled, pid)
        return rolled

    def _interaction_sent(self, text, message=None):
        d = self._interaction_prepare()
        special = self._interaction_special()
        if message and text.strip():
            d["pending"][message["uid"]] = special
            if special and d["special_until"]:
                if not d["special_spoke"]:
                    d["decay_at"] = max(d["decay_at"], self._ms())
                d["special_spoke"] = True
        self._interaction_set(d)

    def _interaction_violation(self):
        """真实联合行动违规直接结算，不从聊天文本推断。"""
        from comm.comm_persona import read_persona_full
        event = tension_event(read_persona_full(self.personas, self.persona_name()), "joint_hp_loss")
        if event is None:
            return
        condition, base = event
        d = self._interaction_prepare()
        status = self.other_status()
        special = self._interaction_special(status)
        burst = d["tension"] > config.COMM_TENSION_CAP
        gain = base
        if not special:
            gain = int(gain * config.COMM_TENSION_DAILY_RATE)
        interaction_add(d, condition, gain, self._ms())
        self._interaction_set(d)
        if special and (burst or d["tension"] > config.COMM_TENSION_CAP):
            self._interaction_reply(True, True, int(status["end"].timestamp() * 1000))

    def _interaction_judge_note(self, kind):
        d = self._interaction_prepare()
        self._interaction_candidates = [(m["uid"], d["pending"][m["uid"]], m["text"])
            for m in self.messages if kind in ("user", "retry") and m.get("role") == "me" and m.get("uid") in d["pending"]]
        if not self._interaction_candidates:
            return "本次无需判断张力增量，不要输出 [tension:序号:条件:增量]。"
        items = [{"序号": i, "用户消息": text} for i, (_uid, _special, text) in enumerate(self._interaction_candidates, 1)]
        from comm.comm_persona import read_persona_full
        rules = tension_rules(read_persona_full(self.personas, self.persona_name()))
        self._interaction_rules = rules
        return (tr("请按人设文件的[tension]模板和上下文判断以下用户消息。只报告已定义的条件，普通交流不增加；方括号数字为参考基础增量，X表示不定量、没有固定参考值，由你结合条件语义与上下文决定。仍须返回范围内的整数增量，不返回X；相同条件不得改名规避冷却。")
                + tr("基础增量范围0至{maximum}，在正常回复末尾附加[tension:序号:条件:增量]，0可省略。有效条件：", maximum=config.COMM_TENSION_GAIN_MAX)
                + "\n".join(f"{key}:[{gain if gain is not None else 'X'}] {description}"
                            for key, (gain, description) in rules.items())
                + tr("系统按发送时段将非特殊空闲的增量乘0.75，并处理爆发后24小时的增长减半，请返回未经倍率调整的基础增量。") +
                tr("每个条件{hours}小时内仅加一次，系统事件与模型同名条件共用冷却。", hours=config.COMM_TENSION_GAIN_COOLDOWN_SECONDS // 3600)
                + tr("同一条消息可以同时触发多个条件；各条件分别应用倍率并取整后累加，各自进入冷却；重复的同一条件只取最大增量。请报告所有满足的条件，不得给其他历史消息加值，不要向用户解释标记。")
                + tr("每条消息按基础增量从高到低结算。每次成功加值后，其他条件在接下来1小时内的增量乘0.75，再次成功加值会刷新窗口；尚无窗口时最高增量条件不乘，其余条件乘，已有窗口时所有可用条件都乘。该倍率与时段及爆发后倍率叠加，由系统逐步取整，请仍返回原始基础增量。")
                + tr("冷却中的条件仍须报告正数原始增量；特殊空闲内加值首次超过90立即爆发，已超过90时再次满足任一增长条件也可爆发，冷却只限制加值。") +
                "本轮仍按给定生效档位回复，增量从下次请求生效。待判断消息：" + json.dumps(items, ensure_ascii=False))

    def _interaction_judged(self, reply):
        d = self._interaction_prepare()
        burst = False
        for i, (uid, special, _text) in enumerate(getattr(self, "_interaction_candidates", []), 1):
            if uid in d["pending"]:
                positive = tension_model_gain(reply, i, True, getattr(self, "_interaction_rules", {})) > 0
                begin = d["last_special_begin"]
                sent = next((int(m["ts"]) for m in self.messages if m["uid"] == uid), -1)
                if special and positive and begin and begin <= sent <= begin + config.COMM_SPECIAL_LURE_DELAY_SECONDS * 1000:
                    d["special_7m_gain_count"] += 1
                # 已超临界时冷却不挡爆发；特殊空闲内首次越过临界也立即触发。
                if special and d["tension"] > config.COMM_TENSION_CAP and positive:
                    burst = True
                now_ms = self._ms()
                for condition, gain in tension_model_events(reply, i, special, d["gain_cooldowns"], now_ms, getattr(self, "_interaction_rules", {})):
                    interaction_add(d, condition, gain, now_ms)
                    if special and d["tension"] > config.COMM_TENSION_CAP and gain > 0:
                        burst = True
                del d["pending"][uid]
        self._interaction_set(d)
        return burst

    def _interaction_special(self, status=None):
        s = self.other_status() if status is None else status
        return bool(s) and not s.get("joint") and routine.seg_class(s) == "special"

    def _interaction_reply(self, special, burst, until=0, shame=False, pid=None):
        d = self._interaction_prepare(pid)
        if shame and d["shame_turns"]:
            d["shame_turns"] -= 1
            d["decay_at"] = max(d["decay_at"], self._ms())
        elif special and burst and not d["shame_turns"] and (d["special_until"] != until or d["special_spoke"]):
            if not d["burst_until"] and until:
                d["gain_until"] = max(d["gain_until"], self._ms() + config.COMM_TENSION_BURST_GAIN_SECONDS * 1000)
                d["burst_days"] = sorted(set(d["burst_days"] + [self.now().date().isoformat()]))[-2:]
            if until:
                d["burst_sleep_after"] = max(d["burst_sleep_after"], until)
                if not d["burst_until"]:
                    d["burst_until"] = until
        self._interaction_set(interaction_settle(d, self._ms()), pid)

    def _interaction_end(self, reply, burst, pid=None):
        d = self._interaction_prepare(pid)
        ended = interaction_end(d, reply, burst, self._ms())
        if ended != d:
            self._interaction_set(ended, pid)

    def tension_display(self, enabled):
        """显示当前对象的真实张力，保留两位小数，关闭时不显示。"""
        if not enabled or not self.persona_name():
            return ""
        value = f"{self._interaction_prepare()['tension']:.2f}".rstrip("0").rstrip(".")
        return tr("Tension: {value}", value=value)

    def interaction_note(self, status=None, pid=None):
        from .comm_persona import message_milestones
        d = self._interaction_prepare(pid)
        st = self._state_of(pid or self.pid)
        total = max(message_milestones(st) or [0])
        return interaction_prompt(d, total, self._interaction_special(status))
