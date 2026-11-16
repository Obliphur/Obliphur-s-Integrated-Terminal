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
    """条件名与参考基础增量由人设指定；描述交给模型判断。"""
    out = {}
    for line in rule_lines(text, "tension"):
        m = re.fullmatch(r"([a-z_]{1,32})\s*[:：]\s*\[(\d{1,3})\]\s*(\S.*)", line)
        if not m or m[1] in out or not 1 <= int(m[2]) <= config.COMM_TENSION_GAIN_MAX:
            return {}
        out[m[1]] = (int(m[2]), m[3])
    return out


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
