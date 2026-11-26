# -*- coding: utf-8 -*-
"""人设文件中的声明式条件；只解析数据，不执行文件中的代码。"""

import math
import re

import config


def rule_lines(text, section):
    """区块必须完整且唯一；空行与 # 注释不参与判定。"""
    start, end = f"[{section}]", f"[/{section}]"
    lines = text.replace("\r\n", "\n").splitlines()
    heads = [i for i, line in enumerate(lines) if line.strip().lower() == start]
    tails = [i for i, line in enumerate(lines) if line.strip().lower() == end]
    if len(heads) != 1 or len(tails) != 1 or heads[0] >= tails[0]:
        return []
    return [line.strip() for line in lines[heads[0] + 1:tails[0]]
            if line.strip() and not line.lstrip().startswith("#")]


def tension_rules(text):
    """条件名与参考基础增量由人设指定；X 不设参考值，由模型判断。"""
    out = {}
    for line in rule_lines(text, "tension"):
        m = re.fullmatch(r"([a-z_]{1,32})\s*[:：]\s*\[(\d{1,3}|X)\]\s*(\S.*)", line)
        if not m or m[1] in out:
            return {}
        gain = None if m[2] == "X" else int(m[2])
        if gain is not None and not 1 <= gain <= config.COMM_TENSION_GAIN_MAX:
            return {}
        out[m[1]] = (gain, m[3])
    return out


def valence_rules(text):
    """情绪效价条件由人设声明；参考值带符号，X 表示不定量。"""
    out = {}
    for line in rule_lines(text, "valence"):
        m = re.fullmatch(r"([a-z_]{1,32})\s*[:：]\s*\[([+-]?\d{1,3}|X)\]\s*(\S.*)", line)
        if not m or m[1] in out:
            return {}
        shift = None if m[2] == "X" else int(m[2])
        if shift is not None and (shift == 0 or abs(shift) > 40):
            return {}
        out[m[1]] = (shift, m[3])
    return out


def valence_initial(text):
    """读取人设的初始效价；缺省或格式错误时回到 0。"""
    lines = rule_lines(text, "valence:initial")
    if len(lines) != 1:
        return 0.0
    match = re.fullmatch(r"initial\s*[:：]\s*([+-]?\d+(?:\.\d+)?)", lines[0])
    if not match:
        return 0.0
    value = float(match[1])
    return value if math.isfinite(value) and -100.0 <= value <= 100.0 else 0.0


def valence_event(text, event):
    """真实联合行动完成奖励；缺省或格式错误时不加值。"""
    out = {}
    for line in rule_lines(text, "valence:events"):
        match = re.fullmatch(r"(joint_completed)\s*[:：]\s*([+]?\d{1,3})", line)
        if not match or match[1] in out or not 0 <= int(match[2]) <= 100:
            return 0
        out[match[1]] = int(match[2])
    return out.get(event, 0)


def valence_burst_rules(text):
    """读取可选爆发联动参数；格式错误时使用中性默认值。"""
    lines = rule_lines(text, "valence:burst")
    if not lines:
        return {"burst_rate": 1.0, "burst_end_shift": 0}
    out = {"burst_rate": 1.0, "burst_end_shift": 0}
    seen = set()
    for line in lines:
        pair = re.fullmatch(r"(burst_rate|burst_end_shift)\s*[:：]\s*([+-]?\d+(?:\.\d+)?)", line)
        if not pair or pair[1] in seen:
            return {"burst_rate": 1.0, "burst_end_shift": 0}
        seen.add(pair[1])
        number = float(pair[2])
        if not math.isfinite(number):
            return {"burst_rate": 1.0, "burst_end_shift": 0}
        if pair[1] == "burst_rate":
            if not 0.0 <= number <= 5.0:
                return {"burst_rate": 1.0, "burst_end_shift": 0}
            out[pair[1]] = number
        else:
            if not number.is_integer() or abs(number) > 100:
                return {"burst_rate": 1.0, "burst_end_shift": 0}
            out[pair[1]] = int(number)
    return out


def scenario_rules(text):
    """读取情景模拟效价阈值；未声明或格式错误时返回 None。"""
    entries = rule_lines(text, "scenario")
    if not entries:
        return None
    out = {}
    for line in entries:
        pair = re.fullmatch(r"(propose_valence|ignore_valence)\s*[:：]\s*([+-]?\d+(?:\.\d+)?)", line)
        if not pair or pair[1] in out:
            return None
        value = float(pair[2])
        if not math.isfinite(value) or not -100.0 <= value <= 100.0:
            return None
        out[pair[1]] = value
    if len(out) != 2 or out["ignore_valence"] > out["propose_valence"]:
        return None
    return out


def typing_delay_rules(text):
    """读取可选打字延迟参数；空缺或格式错误时返回 (None, None)。"""
    lines = rule_lines(text, "typing")
    if not lines:
        return None, None
    out = {}
    for line in lines:
        pair = re.fullmatch(r"(char|cjk)\s*[:：]\s*(\d+(?:\.\d+)?)", line)
        if not pair or pair[1] in out:
            return None, None
        value = float(pair[2])
        if not math.isfinite(value) or not 0.0 <= value <= 5.0:
            return None, None
        out[pair[1]] = value
    return out.get("char"), out.get("cjk")


def shift_range(text):
    """可选平移区块：缺省允许全天，格式错误返回 None；起点大于终点表示跨午夜。"""
    if not re.search(r"^\s*\[/?shift\]\s*$", text, re.I | re.M):
        return 0, 1439
    lines = rule_lines(text, "shift")
    if len(lines) != 1:
        return None
    m = re.fullmatch(r"range\s*[:：]\s*\[\s*(\d{1,2})[:：](\d{2})\s*[,，]\s*(\d{1,2})[:：](\d{2})\s*\]", lines[0])
    if not m:
        return None
    h1, m1, h2, m2 = map(int, m.groups())
    return (h1 * 60 + m1, h2 * 60 + m2) if h1 < 24 and h2 < 24 and m1 < 60 and m2 < 60 else None


def shift_allows(bounds, minutes):
    """记录按合法时刻保存，新请求才应用当前人设的范围。"""
    if bounds is None or not 0 <= minutes < 1440:
        return False
    lo, hi = bounds
    return lo <= minutes <= hi if lo <= hi else minutes >= lo or minutes <= hi


def tension_event(text, event):
    """真实系统事件绑定到人设条件；未声明时不增长。"""
    rules = tension_rules(text)
    out = {}
    for line in rule_lines(text, "tension:events"):
        m = re.fullmatch(r"([a-z_]+)\s*[:：]\s*\[([a-z_]{1,32})\s*[,，]\s*(\d{1,3})\]", line)
        if not m or m[1] != "joint_hp_loss" or m[1] in out or m[2] not in rules or not 1 <= int(m[3]) <= config.COMM_TENSION_GAIN_MAX:
            return None
        out[m[1]] = (m[2], int(m[3]))
    return out.get(event)


def tension_relationship_rules(text):
    """亲密关系下的张力条件门控与增幅；格式为 条件名:[only,倍率] 或 条件名:[倍率]。"""
    rules = tension_rules(text)
    out = {}
    for line in rule_lines(text, "tension:relationship"):
        m = re.fullmatch(r"([a-z_]{1,32})\s*[:：]\s*\[\s*(?:(only)\s*[,，]\s*)?(\d+(?:\.\d+)?)\s*\]", line)
        if not m or m[1] not in rules or m[1] in out:
            return {}
        rate = float(m[3])
        if not math.isfinite(rate) or not 0.0 <= rate <= 5.0:
            return {}
        out[m[1]] = (bool(m[2]), rate)
    return out


def condition_rules(text, strict=False):
    """按日程的字段:数值/列表格式读取；支持删减条件，非法项使整组无效。"""
    out, enabled = {}, []
    fields = {"joint": ("joint_days", "joint_lv", "joint_n"),
              "study": ("study_days", "study_h", "idle_h"),
              "messages": ("msgs",), "today": ("today_h",)}
    for line in rule_lines(text, "condition:strict" if strict else "condition"):
        pair = re.split(r"[:：]", line, maxsplit=1)
        if len(pair) != 2:
            return None
        key, value = (s.strip() for s in pair)
        if key in enabled:
            return None
        enabled.append(key)
        if key == "period":
            if value != "special":
                return None
            continue
        if key not in fields:
            return None
        # 多值字段必须使用与 routine 一致的方括号列表。
        if len(fields[key]) > 1 and not (value.startswith("[") and value.endswith("]")):
            return None
        try:
            values = [float(v.strip()) for v in value.strip("[]").replace("，", ",").split(",")]
        except ValueError:
            return None
        if len(values) != len(fields[key]) or any(not math.isfinite(v) or v < 0 for v in values):
            return None
        for name, v in zip(fields[key], values):
            if name in ("joint_days", "study_days") and not (1 <= v <= config.COMM_CONDITION_KEEP_DAYS and v.is_integer()):
                return None
            if name in ("joint_lv", "joint_n", "msgs") and (v > 2147483647 or not v.is_integer()):
                return None
            out[name] = int(v) if name not in ("study_h", "idle_h", "today_h") else v
    return (out, enabled) if enabled else None


def life_arcs_rules(text):
    """角色慢变量生活线：id:[min_days,max_days] 阶段1 | 阶段2 | ...；非法项使整组无效。"""
    out = {}
    for line in rule_lines(text, "life:arcs"):
        m = re.fullmatch(r"([a-z_]{1,32})\s*[:：]\s*\[\s*(\d{1,2})\s*[,，]\s*(\d{1,2})\s*\]\s*(\S.*)", line)
        if not m or m[1] in out:
            return {}
        lo, hi = int(m[2]), int(m[3])
        stages = tuple(part.strip() for part in m[4].split("|") if part.strip())
        if not (1 <= lo <= hi <= 60) or not (2 <= len(stages) <= 12):
            return {}
        out[m[1]] = (lo, hi, stages)
    return out


def _int_section(text, section, bounds, defaults):
    """解析键值对整数区块；缺省返回 None，格式错误或重复键返回 None。"""
    lines = rule_lines(text, section)
    if not lines:
        return None
    out = dict(defaults)
    seen = set()
    for line in lines:
        pair = re.split(r"[:：]", line, maxsplit=1)
        if len(pair) != 2:
            return None
        key, raw = pair[0].strip(), pair[1].strip()
        if key not in bounds or key in seen:
            return None
        seen.add(key)
        if not re.fullmatch(r"\d{1,3}", raw):
            return None
        lo, hi = bounds[key]
        val = int(raw)
        if not (lo <= val <= hi):
            return None
        out[key] = val
    return out


def trend_rules(text):
    """跨周趋势洞察模板配置；缺省或非法返回 None。"""
    return _int_section(
        text, "trend",
        {"window": (3, 14), "min_days": (2, 14), "delta_pct": (5, 50)},
        {"window": 7, "min_days": 3, "delta_pct": 15},
    )


def callback_rules(text):
    """延迟回调模板配置；缺省或非法返回 None。"""
    return _int_section(
        text, "callback",
        {"lookahead_days": (0, 3), "expire_days": (1, 7), "max_due": (1, 5)},
        {"lookahead_days": 0, "expire_days": 2, "max_due": 2},
    )


def anti_repeat_rules(text):
    """防复读护栏模板配置；缺省或非法返回 None。"""
    lines = rule_lines(text, "anti_repeat")
    if not lines:
        return None
    bounds = {"window": (6, 30), "min_hits": (2, 6), "max_items": (2, 10)}
    out = {"window": 16, "min_hits": 3, "max_items": 6, "forbid": ()}
    seen = set()
    for line in lines:
        pair = re.split(r"[:：]", line, maxsplit=1)
        if len(pair) != 2:
            return None
        key, raw = pair[0].strip(), pair[1].strip()
        if key in seen:
            return None
        seen.add(key)
        if key == "forbid":
            words = tuple(w.strip() for w in raw.split("|") if w.strip())
            if len(words) > 20 or any(len(w) > 16 for w in words):
                return None
            out["forbid"] = words
        elif key in bounds:
            if not re.fullmatch(r"\d{1,3}", raw):
                return None
            lo, hi = bounds[key]
            val = int(raw)
            if not (lo <= val <= hi):
                return None
            out[key] = val
        else:
            return None
    return out
