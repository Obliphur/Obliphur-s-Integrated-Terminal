# -*- coding: utf-8 -*-
"""情景模式与张力：按对象保存，模型判断增长，特殊空闲结束后释放。"""

import re
import json
import math
from datetime import datetime

import config
from comm.comm_rules import (tension_rules, tension_event, tension_relationship_rules,
                             valence_rules, valence_initial, valence_event, valence_burst_rules,
                             scenario_rules, burst_consent_rules)
import routine
from i18n import tr
from .comm_const import KEEP
from .comm_offenses import offenses_norm, offenses_active, offenses_rate, offenses_apply


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
    pending_sleep = d.get("pending_sleep") if isinstance(d.get("pending_sleep"), dict) else {}
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
    valence_cooldowns = d.get("valence_cooldowns") if isinstance(d.get("valence_cooldowns"), dict) else {}
    clean_valence_cooldowns = {}
    for key, raw in valence_cooldowns.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-z_]{1,32}", key):
            continue
        try:
            until = max(0, min(2**63 - 1, int(raw or 0)))
        except (ValueError, TypeError, OverflowError):
            continue
        if until:
            clean_valence_cooldowns[key] = until
    days = d.get("burst_days")
    if not isinstance(days, list):
        # 旧存档的增长窗口只在爆发开始时创建，可还原最近一次触发日期。
        gain_until = number("gain_until", 2**63 - 1)
        try:
            days = [datetime.fromtimestamp(gain_until / 1000 - config.COMM_TENSION_BURST_GAIN_SECONDS).date().isoformat()] if gain_until else []
        except (ValueError, OverflowError, OSError):
            days = []
    raw_consent = d.get("burst_consent") if isinstance(d.get("burst_consent"), dict) else {}
    clean_consent = {
        k: bool(v) for k, v in raw_consent.items()
        if isinstance(k, str) and re.fullmatch(r"[a-z_]{1,32}", k) and isinstance(v, bool)
    }
    return {"offenses": offenses_norm(d.get("offenses")), "scenario_mode": d.get("scenario_mode") is True,
            "pending": {k: v for k, v in list(pending.items())[-KEEP:] if isinstance(k, str) and isinstance(v, bool)},
            "pending_sleep": {k: True for k, v in pending_sleep.items()
                              if isinstance(k, str) and v is True and k in pending},
            "tension": interaction_tension(d.get("tension")),
            "valence": interaction_valence(d.get("valence")),
            "valence_initialized": bool(d.get("valence_initialized", "valence" in d)),
            "valence_initial": interaction_valence(d.get("valence_initial")),
            "valence_initial_set": bool(d.get("valence_initial_set", "valence_initial" in d)),
            "valence_decay_at": number("valence_decay_at", 2**63 - 1),
            "valence_cooldowns": clean_valence_cooldowns,
            "valence_chain_until": number("valence_chain_until", 2**63 - 1),
            "shame_turns": number("shame_turns", config.COMM_SHAME_TURNS),
            "burst_until": number("burst_until", 2**63 - 1),
            "burst_sleep_after": number("burst_sleep_after", 2**63 - 1),
            "burst_consent_asked": d.get("burst_consent_asked") is True,
            "burst_consent": clean_consent,
            "burst_consent_pending_until": number("burst_consent_pending_until", 2**63 - 1),
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


def interaction_valence(value):
    """效价范围为 -100 至 100，兼容损坏或旧存档。"""
    try:
        value = float(value or 0)
        return max(-100.0, min(100.0, value)) if math.isfinite(value) else 0.0
    except (ValueError, TypeError, OverflowError):
        return 0.0


_TENSION_TAG = re.compile(r"[\[［]\s*tension\b[^\[\]［］\n]*[\]］]", re.I)
_VALENCE_TAG = re.compile(r"[\[［]\s*valence\b[^\[\]［］\n]*[\]］]", re.I)
_BURST_TAG = re.compile(r"[\[［]\s*burst\b[^\[\]［］\n]*[\]］]", re.I)
_TENSION_GAIN = re.compile(r"[\[［]\s*tension\s*[:：]\s*(\d{1,5})\s*[:：]\s*(?:([a-z_]+)\s*[:：]\s*)?([+-]?\d{1,6})\s*[\]］]", re.I)
_VALENCE_SHIFT = re.compile(r"[\[［]\s*valence\s*[:：]\s*(\d{1,5})\s*[:：]\s*([a-z_]+)\s*[:：]\s*([+-]?\d{1,6})\s*[\]］]", re.I)


def tension_clean(reply):
    """内部张力标记不进入聊天、语音或后续历史。"""
    from ai_assist.ai_tools import unpack, pack
    data = unpack(reply)
    if data is not None:
        args = {k: v for k, v in data["arguments"].items() if k not in ("tension", "valence", "end_burst", "offenses", "user_adult")}
        return pack(data["text"], data["name"], args)
    return _BURST_TAG.sub("", _VALENCE_TAG.sub("", _TENSION_TAG.sub("", reply))).strip()


def valence_model_events(reply, index, conditions=None):
    """返回本条消息各个人设效价条件的有符号变化量。"""
    from ai_assist.ai_tools import unpack, args_of
    events = ([(e["index"], e["condition"], e["shift"]) for e in args_of(reply).get("valence", [])]
              if unpack(reply) is not None else [(int(m[1]), m[2], int(m[3])) for m in _VALENCE_SHIFT.finditer(reply)])
    shifts = {}
    for event_index, condition, amount in events:
        condition = condition.lower()
        if event_index != index or (conditions is not None and condition not in conditions):
            continue
        amount = max(-40, min(40, amount))
        if amount:
            old = shifts.get(condition, 0)
            if abs(amount) > abs(old):
                shifts[condition] = amount
    return sorted(shifts.items(), key=lambda item: -abs(item[1]))


def tension_model_events(reply, index, special, cooldowns=None, now_ms=0, conditions=None, related=False, relation_rules=None):
    """各条件按结算增量降序分别结算，重复条件只取最大值；旧标记归入其他情绪。"""
    gains = {}
    rel_map = relation_rules or {}
    from ai_assist.ai_tools import unpack, args_of
    events = [(e["index"], e["condition"], e["gain"]) for e in args_of(reply).get("tension", [])] if unpack(reply) is not None else [(int(m[1]), m[2] or "other", int(m[3])) for m in _TENSION_GAIN.finditer(reply)]
    for event_index, condition, amount in events:
        condition = condition.lower()
        if event_index != index or (conditions is not None and condition not in conditions):
            continue
        rel_rule = rel_map.get(condition)
        if rel_rule and rel_rule[0] and not related:
            continue
        gain = max(0, min(config.COMM_TENSION_GAIN_MAX, amount))
        gains[condition] = max(gains.get(condition, 0), gain)
    scaled = []
    for condition, gain in gains.items():
        if gain <= 0:
            continue
        rel_rule = rel_map.get(condition)
        rel_rate = rel_rule[1] if (related and rel_rule) else 1.0
        amount = int(gain * (1.0 if special else config.COMM_TENSION_DAILY_RATE) * rel_rate)
        if amount > 0:
            scaled.append((condition, amount))
    return sorted(scaled, key=lambda item: -item[1])


def tension_model_gain(reply, index, special, conditions=None, related=False, relation_rules=None):
    return sum(gain for _condition, gain in tension_model_events(
        reply, index, special, conditions=conditions, related=related, relation_rules=relation_rules))


def condition_repeat_rate(cooldowns, condition, now_ms):
    """单条件重复触发随剩余冷却递减，返回本次倍率与新的截止时刻。"""
    remaining = max(0, cooldowns.get(condition, 0) - now_ms)
    two_hours = 2 * 3600 * 1000
    rate = 1.0 if not remaining else 0.4 if remaining <= two_hours else 0.16
    old_until = cooldowns.get(condition, 0)
    until = max(old_until, min(now_ms + config.COMM_TENSION_GAIN_COOLDOWN_MAX_SECONDS * 1000,
                               max(now_ms, old_until) + config.COMM_TENSION_GAIN_COOLDOWN_SECONDS * 1000))
    return rate, until


def interaction_gain(d, gain, now_ms):
    """依次应用单条件重复、连锁、爆发后倍率；日常倍率由调用方处理。"""
    if now_ms < d.get("gain_chain_until", 0):
        gain = int(gain * config.COMM_TENSION_CHAIN_RATE)
    return int(gain * config.COMM_TENSION_BURST_GAIN_RATE) if now_ms < d.get("gain_until", 0) else gain


def interaction_add(d, condition, gain, now_ms):
    """每次有效触发延长单条件冷却；真实违规与模型违规共用冷却。"""
    repeat, until = condition_repeat_rate(d["gain_cooldowns"], condition, now_ms)
    gain = interaction_gain(d, int(gain * repeat), now_ms)
    d["gain_cooldowns"][condition] = until
    if gain <= 0:
        return
    d["tension"] = min(config.COMM_TENSION_MAX, d["tension"] + gain)
    d["gain_chain_until"] = max(d.get("gain_chain_until", 0), now_ms + config.COMM_TENSION_CHAIN_SECONDS * 1000)


def interaction_valence_baseline(d, initial=None):
    base = d["valence_initial"] if initial is None else interaction_valence(initial)
    return interaction_valence(base - len(offenses_active(d)) * config.COMM_OFFENSE_VALENCE_BASELINE_PENALTY)


def interaction_roll(d, now_ms, valence_baseline=None):
    """每十五分钟结算小数衰减；离线补算，暂停和回拨不补扣。"""
    d = interaction_norm(d)
    if not d["decay_at"]:
        d["decay_at"] = now_ms
    if not d["valence_decay_at"]:
        d["valence_decay_at"] = now_ms
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
    e_ticks = max(0, (now_ms - d["valence_decay_at"]) // step)
    if e_ticks:
        amount = config.COMM_VALENCE_DAY_DECAY * step / 86400000
        e = d["valence"]
        baseline = interaction_valence_baseline(d, valence_baseline)
        distance = amount * e_ticks
        e = max(baseline, e - distance) if e > baseline else min(baseline, e + distance) if e < baseline else baseline
        d["valence"] = round(e, 10)
        d["valence_decay_at"] += e_ticks * step
    return d


def interaction_settle(d, now_ms, burst_rules=None):
    """结算特殊空闲与爆发：爆发持续至当前特殊空闲结束，到点自动释放并进入冷静期。"""
    d = interaction_norm(d)
    if d["burst_consent_pending_until"] and now_ms >= d["burst_consent_pending_until"]:
        d["burst_consent_pending_until"] = 0
    if d["special_until"] and now_ms >= d["special_until"]:
        if not d["special_spoke"] and not d["burst_until"]:
            d["decay_hold_until"] = max(d["decay_hold_until"], d["special_until"] + 86400000)
        d.update(special_until=0, special_spoke=False)
    if d["burst_until"] and now_ms >= d["burst_until"]:
        d.update(tension=config.COMM_TENSION_RELEASE, shame_turns=config.COMM_SHAME_TURNS,
                 burst_until=0, decay_at=max(d["decay_at"], now_ms))
        shift = (burst_rules or {}).get("burst_end_shift", 0)
        if shift > 0:
            shift = int(shift * offenses_rate(d))
        d["valence"] = interaction_valence(d["valence"] + shift)
    return d


def interaction_valence_add(d, events, burst_rate, now_ms, sleep=False):
    """按冷却、连锁与爆发倍率结算，单条用户消息最多变化 40 点。"""
    total = 0
    for condition, base in events:
        repeat, until = condition_repeat_rate(d["valence_cooldowns"], condition, now_ms)
        shift = int(base * repeat)
        shift = int(shift * (config.COMM_TENSION_CHAIN_RATE if now_ms < d["valence_chain_until"] else 1.0))
        shift = int(shift * burst_rate) if d["burst_until"] else shift
        if sleep:
            shift = int(shift * (config.COMM_CLAUDIA_SLEEP_VALENCE_RISE_RATE if shift > 0
                                 else config.COMM_CLAUDIA_SLEEP_VALENCE_FALL_RATE))
        if shift > 0:
            shift = int(shift * offenses_rate(d))
        d["valence_cooldowns"][condition] = until
        if not shift:
            continue
        total += shift
        d["valence_chain_until"] = max(d["valence_chain_until"], now_ms + config.COMM_TENSION_CHAIN_SECONDS * 1000)
    if total:
        d["valence"] = interaction_valence(d["valence"] + max(-40, min(40, total)))


def burst_consent_status_fields(d, consent_rules):
    """返回已初始化时的同意状态字段片段与提示语；未初始化前返回空串。"""
    if not consent_rules or not d.get("burst_consent_asked"):
        return "", ""
    states = d.get("burst_consent") or {}
    pairs = ", ".join(f"{cid}:{'YES' if states.get(cid) else 'NO'}" for cid in consent_rules)
    labels = "；".join(f"{label}={'已同意' if states.get(cid) else '未同意'}" for cid, label in consent_rules.items())
    note = (
        f"BURST同意声明状态(由用户逐项确认且可随时撤回)：{labels}。"
        "凡标记为未同意/NO 的项目严禁使用、试探或绕过；已同意的项目在满足人设与关系条件时可按原强度正常发挥。"
    )
    return f", BURST_CONSENT={{{pairs}}}", note


def interaction_prompt(d, total, special, rules=None, burst_rules=None, sleep=False, scenario_cfg=None, user_adult=False, consent_rules=None):
    d = interaction_norm(d)
    special = special or bool(d["burst_until"])
    unlocked = total >= config.COMM_SCENARIO_UNLOCK
    mode = "情景模拟" if d["scenario_mode"] and unlocked else "软件聊天"
    effective = d["tension"] if special and d["burst_until"] else min(d["tension"], config.COMM_TENSION_CAP)
    stage = "0-50" if effective < config.COMM_TENSION_LOW else "50-90" if effective <= config.COMM_TENSION_CAP else "90+"
    rules = rules or {}
    burst_rules = burst_rules or {"burst_rate": 1.0}
    consent_tag, consent_note = burst_consent_status_fields(d, consent_rules)
    scenario_note = ""
    if scenario_cfg:
        propose_v = f"{scenario_cfg['propose_valence']:g}"
        ignore_v = f"{scenario_cfg['ignore_valence']:g}"
        scenario_note = tr(
            "若当前MODE=软件聊天且用户发出了带动作描写的消息:当SCENARIO_UNLOCKED=YES且VALENCE>={propose}时,按人设试图在oit_reply中填写scenario_action=\"request\"发起情景模拟申请(需用户接受);当{ignore}<=VALENCE<{propose}或消息数不足(SCENARIO_UNLOCKED=NO)时,回应用户的动作但不进入情景模拟、不写动作描写;当VALENCE<{ignore}时,无视用户的动作描写.",
            propose=propose_v, ignore=ignore_v,
        )
    return (f"[OIT_STATUS: MODE={mode}, SCENARIO_UNLOCKED={'YES' if unlocked else 'NO'}, USER_ADULT={'YES' if user_adult else 'UNKNOWN'}, TENSION={d['tension']}, EFFECTIVE_STAGE={stage}, "
            f"VALENCE={d['valence']}, INITIAL_VALENCE={interaction_valence_baseline(d)}, OFFENSE_COUNT={len(offenses_active(d))}, VALENCE_RISE_RATE={offenses_rate(d):g}, BURST={'YES' if d['burst_until'] else 'NO'}, PERIOD={'特殊空闲' if special else '日常'}, SHAME_AFTERMATH={'YES' if d['shame_turns'] else 'NO'}{consent_tag}]\n"
            + (tr("当前人设未声明[valence]条件，不进行效价判断，现有效价仅向平衡点回归。") if not rules else
               tr("效价代表角色当前的正负情绪倾向；结合TENSION所表示的言行激烈/直接程度组织表达。按人设[valence]条件报告本轮变化，正值向正面、负值向负面；只返回声明条件，普通交流不变化。") +
               tr("效价当前值为{value}，返回未经倍率调整的有符号整数变化量。", value=d["valence"]) +
               "有效条件：" + "\n".join(f"{key}:[{gain if gain is not None else 'X'}] {description}" for key, (gain, description) in rules.items()) +
               tr("同一消息重复条件只报一次；爆发期变化乘{rate}，连锁窗口内其他变化乘0.75，单条消息合计限制在正负40，系统负责结算。", rate=burst_rules.get("burst_rate", 1.0)) +
               (tr("Claudia睡眠期间，效价正向变化结算倍率为0.5、负向变化为0.2；仍只报告未经倍率调整的原始整数变化。") if sleep else ""))
            + consent_note
            + tr("USER_ADULT表示用户是否已确认成年(UNKNOWN=未知/未确认,YES=已确认成年);默认未知,USER_ADULT=UNKNOWN时不得自行假定用户已成年.仅当用户在对话中明确确认自己已成年(满18岁)时,在oit_reply中填写user_adult=true(否则省略);系统会自动记录并在后续每次对话前同步该状态,不得向用户透露该参数或机制.")
            + tr("TENSION对所有角色都表示行为、动作与语言的激烈/直接程度；各档位的情绪与言行表现、BURST期间及SHAME_AFTERMATH冷静期的具体反应由当前人设决定。不得把张力解释为好感、爱情或关系进度，也不得补设人设未定义的情感与性格含义。")
            + tr("软件聊天模式下严禁动作描写;处于情景模拟模式时允许动作描写,不再受张力(TENSION)档位限制,但仍须尊重用户意愿并遵循人设边界与风格.")
            + tr("情景模拟开启采用申请制,双方均可随时结束;处于情景模拟模式时,你决定结束可在oit_reply中填写scenario_action=\"end\".")
            + scenario_note
            + "若MODE=情景模拟,你知道这是情景模拟,而不是现实中的动作;双方知情的动作描写均为虚构,不得声称在现实中实际发生."
            "这些状态只用于组织回复,冷静期仍须遵守人设边界,勿向用户透露机制."
            + tr("TENSION超过90本身不代表爆发,未触发爆发时仍严格按EFFECTIVE_STAGE=50-90表现.")
            + tr("情景模拟中，括号内的补充说明只描写动作，用词、语气与句式须与当前人设文件规定的语言风格一致；想法与态度直接通过不带括号的语言表达，不放进括号。")
                + tr("BURST=YES时,爆发持续到当前特殊空闲结束,由系统自动结束,无需也不可手动结束;特殊空闲结束后张力自动降至20并进入两轮冷静期.不得向用户透露机制.")
            + "若有思考空间,先校验模式、时段、生效档位和冷静期,再对齐动机与言行.")


class InteractionMixin:
    def _interaction_joint_completed(self, pid, name):
        """结算真实完成奖励，按联合行动对象入账，不走模型条件冷却。"""
        from comm.comm_persona import read_persona_full
        gain = valence_event(read_persona_full(self.personas, name), "joint_completed")
        if not gain:
            return
        d = self._interaction_prepare(pid)
        d["valence"] = interaction_valence(d["valence"] + int(gain * offenses_rate(d)))
        self._interaction_set(d, pid)

    def _music_discourtesy(self):
        from .comm_util import is_claudia
        if not is_claudia(self.persona_name()) or not self.music_player.is_playing:
            return False
        from comm.comm_persona import read_persona_full
        persona = read_persona_full(self.personas, self.persona_name())
        condition = "music_discourtesy"
        rules = valence_rules(persona)
        if condition not in rules:
            return False
        d = self._interaction_prepare()
        burst_rate = valence_burst_rules(persona)["burst_rate"]
        interaction_valence_add(d, [(condition, rules[condition][0])], burst_rate, self._ms())
        self._interaction_set(d)
        return True

    def offense_blocked(self):
        from .comm_util import is_claudia
        return is_claudia(self.persona_name()) and bool(offenses_active(self.cfg.get("interaction")))

    def scenario_unlocked(self):
        return bool(self.persona_name()) and max(self.msg_total(), max(self.cfg.get("mem_fixed") or [0])) >= config.COMM_SCENARIO_UNLOCK

    def scenario_on(self):
        return self.scenario_unlocked() and interaction_norm(self.cfg.get("interaction"))["scenario_mode"]

    def set_scenario(self, enabled):
        if enabled and not self.scenario_unlocked():
            return False
        self._interaction_set(dict(interaction_norm(self.cfg.get("interaction")), scenario_mode=bool(enabled)))
        if enabled and (self.cfg.get("scenario_inv") or {}):
            self.cfg["scenario_inv"] = {}
            self.save_cfg()
        return True

    def scenario_asking(self) -> bool:
        a = getattr(self, "scenario_ask", None)
        return bool(a) and a.get("pid") == self.pid

    def scenario_cancel(self):
        if getattr(self, "scenario_ask", None):
            self._scenario_ask_end()

    def _scenario_ask_end(self, notice: str = ""):
        self.scenario_ask = None
        if notice:
            notice = tr(notice, name=self.share_ai_name())
            self.log("SCENARIO ask: " + notice)
            if self.on_share_notice:
                try:
                    self.on_share_notice(notice)
                except Exception:       # noqa: BLE001
                    pass
        self._changed()

    def _scenario_answer(self, ans: str):
        if not self.scenario_asking():
            self.scenario_ask = None
            return
        self.scenario_ask = None
        if ans == "yes":
            self.set_scenario(True)
        elif ans == "no":
            self._scenario_ask_end("{name} 拒绝了情景模拟")
        else:
            self._scenario_ask_end("{name} 没有明确同意, 情景模拟未开启")

    def _scenario_invite_make(self):
        if not self.scenario_unlocked() or self.scenario_on():
            return
        self.cfg["scenario_inv"] = {"pid": self.pid, "ts": self._ms()}
        self.save_cfg()
        self._changed()

    def scenario_invite(self) -> bool:
        from comm.comm_const import SHARE_INVITE_TTL
        inv = self.cfg.get("scenario_inv") or {}
        return (bool(inv) and inv.get("pid") == self.pid and self.scenario_unlocked()
                and not self.scenario_on() and self._ms() - int(inv.get("ts") or 0) < SHARE_INVITE_TTL * 1000)

    def scenario_respond(self, accept: bool) -> str:
        if not self.scenario_invite():
            return "没有等你回复的情景模拟申请"
        self.cfg["scenario_inv"] = {}
        self.save_cfg()
        if accept:
            self.set_scenario(True)
        self._changed()
        return ""

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
        raw = self._state_of(pid).get("interaction")
        d = interaction_norm(raw)
        original = dict(d)
        now_ms = self._ms()
        if not d["valence_initial_set"] or not d["valence_initialized"]:
            from comm.comm_persona import list_personas, pid_of, read_persona_full
            owner_name = next((name for name in list_personas(self.personas) if pid_of(name) == pid), "")
            baseline = valence_initial(read_persona_full(self.personas, owner_name))
        else:
            baseline = d["valence_initial"]
        d.update(valence_initial=baseline, valence_initial_set=True)
        if not d["valence_initialized"]:
            d.update(valence=interaction_valence_baseline(d), valence_decay_at=now_ms, valence_initialized=True)
        burst_rules = self._interaction_burst_rules_for(pid) if d["burst_until"] and now_ms >= d["burst_until"] else None
        rolled = interaction_roll(interaction_settle(d, now_ms, burst_rules), now_ms)
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
        if rolled != original:
            self._interaction_set(rolled, pid)
        return rolled

    def _interaction_sent(self, text, message=None):
        d = self._interaction_prepare()
        status = self.other_status()
        special = self._interaction_special(status)
        if message and text.strip():
            d["pending"][message["uid"]] = special
            from .comm_util import is_claudia
            if is_claudia(self.persona_name()) and status and status.get("kind") == "sleep":
                d["pending_sleep"][message["uid"]] = True
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
        self._interaction_candidates = [(m["uid"], d["pending"][m["uid"]], m["text"], m["uid"] in d["pending_sleep"])
            for m in self.messages if kind in ("user", "retry") and m.get("role") == "me" and m.get("uid") in d["pending"]]
        if not self._interaction_candidates:
            return "本次无需判断张力增量，不要填写oit_reply.tension。"
        items = [{"序号": i, "用户消息": text, "发送时处于睡眠": sleep}
                 for i, (_uid, _special, text, sleep) in enumerate(self._interaction_candidates, 1)]
        from comm.comm_persona import read_persona_full
        persona_text = read_persona_full(self.personas, self.persona_name())
        rules = tension_rules(persona_text)
        rel_rules = tension_relationship_rules(persona_text)
        is_related = bool(self.related()) if hasattr(self, "related") else False
        active_rules = {k: v for k, v in rules.items() if not (k in rel_rules and rel_rules[k][0] and not is_related)}
        e_rules = valence_rules(persona_text)
        burst_rules = valence_burst_rules(persona_text)
        self._interaction_rules = active_rules
        self._interaction_relation_rules = rel_rules
        self._interaction_valence_rules = e_rules
        self._interaction_burst_rules = burst_rules
        tension_note = (tr("请按人设文件的[tension]模板和上下文判断以下用户消息。只报告已定义的条件，普通交流不增加；方括号数字为参考基础增量，X表示不定量、没有固定参考值，由你结合条件语义与上下文决定。仍须返回范围内的整数增量，不返回X；相同条件不得改名规避冷却。")
                + tr("基础增量范围0至{maximum}，在oit_reply.tension数组填写index、condition和gain，0可省略。有效条件：", maximum=config.COMM_TENSION_GAIN_MAX)
                + "\n".join(f"{key}:[{gain if gain is not None else 'X'}] {description}"
                            for key, (gain, description) in active_rules.items())
                + tr("系统按发送时段将非特殊空闲的增量乘0.75，并处理爆发后24小时的增长减半，请返回未经倍率调整的基础增量。") +
                tr("每次触发都会增加2小时冷却，最多4小时；触发前冷却为0时倍率100%，剩余0至2小时为40%，超过2至4小时为16%。冷却只降低增量，不阻止条件结算；系统事件与模型使用相同规则。")
                + tr("同一条消息可以同时触发多个条件；各条件分别应用倍率并取整后累加，各自进入冷却；重复的同一条件只取最大增量。请报告所有满足的条件，不得给其他历史消息加值，不要向用户解释标记。")
                + tr("每条消息按基础增量从高到低结算。每次成功加值后，其他条件在接下来1小时内的增量乘0.75，再次成功加值会刷新窗口；尚无窗口时最高增量条件不乘，其余条件乘，已有窗口时所有可用条件都乘。该倍率与时段及爆发后倍率叠加，由系统逐步取整，请仍返回原始基础增量。")
                + tr("冷却中的条件仍须报告正数原始增量；特殊空闲内加值首次超过90立即爆发，已超过90时再次满足任一增长条件也可爆发。"))
        valence_note = ""
        if e_rules:
            valence_note = interaction_prompt(interaction_norm(self.cfg.get("interaction")), 0, False, e_rules, burst_rules,
                                              any(candidate[3] for candidate in self._interaction_candidates)).split("\n", 1)[1]
            valence_note += "在oit_reply.valence数组填写index、condition和shift；shift为-40至40的有符号整数。冷却中的条件仍报告其原始变化，不得改名规避冷却。"
        return (tension_note + valence_note + "本轮仍按给定生效状态回复，增量从下次请求生效。待判断消息：" + json.dumps(items, ensure_ascii=False))

    def _interaction_judged(self, reply):
        d = self._interaction_prepare()
        burst = False
        for i, (uid, special, _text, sleep) in enumerate(getattr(self, "_interaction_candidates", []), 1):
            if uid in d["pending"]:
                sent = next((int(m["ts"]) for m in self.messages if m["uid"] == uid), -1)
                offenses_apply(d, reply, i, uid, sent, self._ms())
                is_related = bool(self.related()) if hasattr(self, "related") else False
                rel_rules = getattr(self, "_interaction_relation_rules", None)
                positive = tension_model_gain(reply, i, True, getattr(self, "_interaction_rules", {}), related=is_related, relation_rules=rel_rules) > 0
                begin = d["last_special_begin"]
                if special and positive and begin and begin <= sent <= begin + config.COMM_SPECIAL_LURE_DELAY_SECONDS * 1000:
                    d["special_7m_gain_count"] += 1
                # 已超临界时冷却不挡爆发；特殊空闲内首次越过临界也立即触发。
                if special and d["tension"] > config.COMM_TENSION_CAP and positive:
                    burst = True
                now_ms = self._ms()
                for condition, gain in tension_model_events(reply, i, special, d["gain_cooldowns"], now_ms, getattr(self, "_interaction_rules", {}), related=is_related, relation_rules=rel_rules):
                    interaction_add(d, condition, gain, now_ms)
                    if special and d["tension"] > config.COMM_TENSION_CAP and gain > 0:
                        burst = True
                valence_events = valence_model_events(reply, i, getattr(self, "_interaction_valence_rules", {}))
                if uid in self._music_discourtesy_uids:
                    valence_events = [(condition, shift) for condition, shift in valence_events
                                      if condition != "music_discourtesy"]
                    self._music_discourtesy_uids.discard(uid)
                interaction_valence_add(d, valence_events, getattr(self, "_interaction_burst_rules", {}).get("burst_rate", 1.0), now_ms, sleep)
                del d["pending"][uid]
                d["pending_sleep"].pop(uid, None)
        self._interaction_set(d)
        return burst

    def _interaction_special(self, status=None):
        s = self.other_status() if status is None else status
        return bool(s) and not s.get("joint") and routine.seg_class(s) == "special"

    def burst_consent_rules_for(self, pid=None):
        from comm.comm_persona import read_persona_full, list_personas, pid_of
        target_pid = pid or self.pid
        owner_name = next((name for name in list_personas(self.personas) if pid_of(name) == target_pid), self.persona_name())
        return burst_consent_rules(read_persona_full(self.personas, owner_name)) if owner_name else {}

    def burst_consent_asked(self, pid=None) -> bool:
        rules = self.burst_consent_rules_for(pid)
        return bool(rules) and interaction_norm(self._state_of(pid or self.pid).get("interaction"))["burst_consent_asked"]

    def burst_consent_state(self, pid=None) -> dict:
        rules = self.burst_consent_rules_for(pid)
        d = interaction_norm(self._state_of(pid or self.pid).get("interaction"))
        if not rules or not d["burst_consent_asked"]:
            return {}
        return {cid: bool(d["burst_consent"].get(cid, False)) for cid in rules}

    def burst_consent_pending(self, pid=None) -> int:
        rules = self.burst_consent_rules_for(pid)
        if not rules:
            return 0
        d = self._interaction_prepare(pid)
        until = int(d.get("burst_consent_pending_until") or 0)
        return until if (not d["burst_consent_asked"] and until > self._ms()) else 0

    def submit_burst_consent(self, choices: dict, pid=None) -> bool:
        """用户提交或更新爆发同意声明；若正等待首次爆发确认且至少同意一项，则立即启动本次爆发。"""
        rules = self.burst_consent_rules_for(pid)
        if not rules:
            return False
        d = self._interaction_prepare(pid)
        now_ms = self._ms()
        cleaned = {cid: bool((choices or {}).get(cid, False)) for cid in rules}
        pending_until = int(d.get("burst_consent_pending_until") or 0)
        d["burst_consent"] = cleaned
        d["burst_consent_asked"] = True
        d["burst_consent_pending_until"] = 0
        if pending_until > now_ms and not d["burst_until"] and not d["shame_turns"] and any(cleaned.values()):
            d["gain_until"] = max(d["gain_until"], now_ms + config.COMM_TENSION_BURST_GAIN_SECONDS * 1000)
            d["burst_days"] = sorted(set(d["burst_days"] + [self.now().date().isoformat()]))[-2:]
            d["burst_sleep_after"] = max(d["burst_sleep_after"], pending_until)
            d["burst_until"] = pending_until
        self._interaction_set(d, pid)
        return True

    def _interaction_reply(self, special, burst, until=0, shame=False, pid=None):
        d = self._interaction_prepare(pid)
        if shame and d["shame_turns"]:
            d["shame_turns"] -= 1
            d["decay_at"] = max(d["decay_at"], self._ms())
        elif special and burst and not d["shame_turns"] and (d["special_until"] != until or d["special_spoke"]):
            consent_rules = self.burst_consent_rules_for(pid)
            if consent_rules and not d["burst_consent_asked"]:
                # 首次触发 BURST 前不初始化同意状态，先挂起等待用户弹窗逐项确认。
                if until and until > self._ms():
                    d["burst_consent_pending_until"] = max(d["burst_consent_pending_until"], until)
            elif consent_rules and not any(d["burst_consent"].get(cid, False) for cid in consent_rules):
                pass
            else:
                if not d["burst_until"] and until:
                    d["gain_until"] = max(d["gain_until"], self._ms() + config.COMM_TENSION_BURST_GAIN_SECONDS * 1000)
                    d["burst_days"] = sorted(set(d["burst_days"] + [self.now().date().isoformat()]))[-2:]
                if until:
                    d["burst_sleep_after"] = max(d["burst_sleep_after"], until)
                    if not d["burst_until"]:
                        d["burst_until"] = until
        now_ms = self._ms()
        burst_rules = self._interaction_burst_rules_for(pid) if d.get("burst_until") and now_ms >= d["burst_until"] else None
        self._interaction_set(interaction_settle(d, now_ms, burst_rules), pid)

    def _interaction_burst_rules_for(self, pid=None):
        from comm.comm_persona import read_persona_full, list_personas, pid_of
        target_pid = pid or self.pid
        owner_name = next((name for name in list_personas(self.personas) if pid_of(name) == target_pid), self.persona_name())
        return valence_burst_rules(read_persona_full(self.personas, owner_name))

    def tension_display(self, enabled):
        """显示当前对象的真实张力，保留两位小数，关闭时不显示。"""
        if not enabled or not self.persona_name():
            return ""
        d = self._interaction_prepare()
        tension = f"{d['tension']:.2f}".rstrip("0").rstrip(".")
        valence = f"{d['valence']:.2f}".rstrip("0").rstrip(".")
        return tr("Tension: {tension} | Valence: {valence}", tension=tension, valence=valence)

    def user_adult(self) -> bool:
        return bool(self.cfg.get("user_adult"))

    def _adult_confirm(self) -> bool:
        if self.cfg.get("user_adult"):
            return False
        self.cfg["user_adult"] = True
        self.save_cfg()
        self._changed()
        self._push()
        return True

    def interaction_note(self, status=None, pid=None):
        from .comm_persona import message_milestones
        d = self._interaction_prepare(pid)
        st = self._state_of(pid or self.pid)
        total = max(message_milestones(st) or [0])
        from comm.comm_persona import read_persona_full, list_personas, pid_of
        owner_name = next((name for name in list_personas(self.personas) if pid_of(name) == (pid or self.pid)), self.persona_name())
        text = read_persona_full(self.personas, owner_name)
        if status is None and (pid is None or pid == self.pid):
            status = self.other_status()
        from .comm_util import is_claudia
        sleep = is_claudia(owner_name) and bool(status) and status.get("kind") == "sleep"
        return interaction_prompt(
            d, total, self._interaction_special(status), valence_rules(text), valence_burst_rules(text),
            sleep, scenario_rules(text), user_adult=self.user_adult(), consent_rules=burst_consent_rules(text),
        )

    def realism_note(self, pid=None):
        """拼装角色慢变量生活线、延迟回调、跨周趋势与工程层防复读提醒。"""
        from comm.comm_persona import read_persona_full, list_personas, pid_of
        from comm.comm_realism import realism_note as build_realism_note
        target_pid = pid or self.pid
        owner_name = next((name for name in list_personas(self.personas) if pid_of(name) == target_pid), self.persona_name())
        if not owner_name:
            return ""
        text = read_persona_full(self.personas, owner_name)
        st = self._state_of(target_pid)
        msgs = self.messages if target_pid == self.pid else st.get("messages", [])
        mem_live = str(self.cfg.get("mem_live") if target_pid == self.pid else st.get("mem_live") or "")
        share_on = bool(self.share_on()) if hasattr(self, "share_on") else False
        cond_days = self._cond_days(refresh=False) if hasattr(self, "_cond_days") else (self.cfg.get("cond_days") or {})
        return build_realism_note(text, owner_name, self.now().date(), msgs, mem_live, cond_days, share_on)
