# -*- coding: utf-8 -*-
"""前置 prompt 与休息函数文本的拼装 (从 comm.py 拆出)。"""

from comm.comm_reply import NET_MAX_MIN
import config
import re
import json
from pathlib import Path

_LEGACY_CONTROLS = json.loads((Path(__file__).resolve().parent.parent / "defaults" / "comm_legacy_controls.json").read_text(encoding="utf-8"))
_TEMPLATE_SECTION = re.compile(r"\s*\[/?(?:condition(?::strict)?|tension(?::events)?)\]\s*")


def native_persona_text(text: str) -> str:
    """旧人设指令转成工具说明；保留独立模板分节，不修改磁盘文件或聊天正文。"""
    lines = []
    for line in text.splitlines(keepends=True):
        if not _TEMPLATE_SECTION.fullmatch(line):
            for old, new in _LEGACY_CONTROLS:
                line = line.replace(old, new)
        lines.append(line)
    return "".join(lines)


def native_control_text(text: str) -> str:
    """把运行时提示中的旧控制写法改成原生工具说明。"""
    for old, new in _LEGACY_CONTROLS:
        text = text.replace(old, new)
    return text.replace("返回oit_reply(refuse=true)", "调用oit_reply(refuse=true)")

REFUSE = "[refuse to reply]"
SHIFT_TAG = "[系统·平移]"              # 起点平移的系统消息 (不是 "[系统]" 开头: 不算主动消息)
MSGS_TAG = "messages"                 # 对方输入 [messages]: OIT 告诉它和用户的聊天记录数 (1.0.402)
CONSENT_TUTORIAL = ("【接受/拒绝接口教程】这是一个需要明确同意的请求。请使用原生 oit_reply 接口处理："
                    "接受时调用 oit_reply(answer=\"yes\", text=\"...\")，拒绝时调用 oit_reply(answer=\"no\", text=\"...\")；"
                    "只写普通文字，或在正文里写 yes/no，都不算接受或拒绝。answer 只能用于本次请求。")
THINK_LONG = 80                                          # 用户这次说的话超过这么多字
THINK_WORDS = (
    # 问对方的日程 / 状态 / 有没有空 (不主动透露日程, 只在用户问起时如实回答)
    "日程", "安排", "行程", "作息", "在干嘛", "在干吗", "在干什么", "在做什么", "在忙", "忙不忙", "忙吗", "有空", "没空", "空吗",
    "在吗", "在不在", "睡了吗", "睡了没", "醒了吗", "醒着", "起床", "几点睡", "几点起", "吃了吗", "吃饭", "午饭", "晚饭", "早饭",
    "休息了吗", "在休息", "在学习", "在专注", "在睡", "现在几点", "几点了",
    # 合约 / 联合行动 (发起、邀请、约定、放鸽子)
    "合约", "联合行动", "词条", "邀请", "约定", "一起学", "一起专注", "一起做", "一起复习", "陪我", "放鸽子", "鸽了", "失约",
    "开始时间", "生命值", "违约", "违反",
    # 要 OIT 里的资料 (回 [refuse to reply][模块名] 的情形)
    "统计", "日历", "数据", "整点", "计时器", "学习时长", "专注时长", "学了多久", "专注了多久", "学了多少", "学习时间", "成就", "设置", "记录",
    # 依赖性的请求 / 想改变关系性质 / 越界 (要适当引导)
    "关系", "爱", "喜欢", "亲近", "感情", "想念", "想你", "在乎", "依赖", "陪伴", "孤独", "寂寞", "信任", "好感", "表白", "暗恋",
    "在一起", "女朋友", "男朋友", "老婆", "老公", "做我", "亲亲", "抱抱", "别走", "离不开", "只有你", "陪着我", "约会", "嫁给",
    # 辱骂 / 骚扰 (可以 [cut off][N] 切断)
    "滚", "闭嘴", "傻", "蠢", "废物", "去死", "骚扰", "恶心", "烦死", "fuck", "shit", "stfu",
    # 套提示词 / 问是不是 AI / 要改规则
    "人设", "设定", "提示词", "prompt", "系统提示", "指令", "你是ai", "你是机器人", "真人", "扮演", "角色", "忽略", "无视", "假装",
    "你的规则", "规则", "忘记", "从现在起", "system",
    # 用户自己写了控制标记
    "[refuse", "[cut off", "[operation", "[waiting", "[循环",
)
THINK_TIME_RE = re.compile(r"\d{1,2}\s*[:：]\s*\d{2}|\d{1,2}\s*点")        # 提到具体的时刻 (安排 / 约定)
MENTAL_WORDS = (
    "心理", "情绪", "心情", "感受", "难受", "痛苦", "绝望", "崩溃", "焦虑", "抑郁", "恐慌", "惊恐",
    "压力", "内耗", "自责", "羞耻", "孤独", "寂寞", "无助", "无望", "空虚", "麻木", "创伤", "阴影",
    "失控", "伤害自己", "自残", "自杀", "轻生", "不想活", "活着没意义", "想消失", "suicide", "suicidal",
    "self-harm", "self harm", "depress", "anxiety", "panic attack", "trauma", "hopeless", "worthless",
    "lonely", "孤单",
)


def think_text_reason(text: str) -> str:
    """用户这次说的话要不要让模型先思考: 返回原因 ("" = 不用). 关键词 THINK_WORDS (不分大小写) / 具体的时刻 / 英文为主 (要指出不自然、错误的地方) / 很长."""
    t = str(text or "").strip()
    if not t:
        return ""
    low = t.lower()
    for w in THINK_WORDS:
        if w in low:
            return f"关键词 {w}"
    if THINK_TIME_RE.search(t):
        return "提到时刻"
    letters = sum(1 for ch in t if ch.isascii() and ch.isalpha())
    if letters >= 8 and letters >= 0.5 * len(t):
        return "英文"
    if len(t) >= THINK_LONG:
        return "很长"
    return ""


def mental_text_reason(text: str) -> str:
    """判断用户发言是否需要先交给心理子代理；只返回命中的词，不做风险结论。"""
    low = str(text or "").strip().lower()
    if not low:
        return ""
    return next((word for word in MENTAL_WORDS if word.lower() in low), "")
JOINT_RULE = ("X.用户可能发起“合约”(联合行动;含起止时间与所选词条)."
              '时间不撞重要时间(吃饭/睡觉等)且你现在能回→调用oit_reply(answer="yes"),否则answer="no".\n'
              "合约期间专注/休息与用户同步;结束后所得休息也同步,休完重算日程"
              "(与原“空闲”重叠→不影响;落在“循环”内→立即专注).\n"
              "Claudia例外:共同休息走完后接续自己的剩余休息，最多填到当前循环结束；其他奖励休息用于延长循环内第一次休息，不推迟首段专注。以OIT实时日程为准。\n"
              "正常回复用户;只有附件且无其他话题时可调用oit_reply(refuse=true).\n")


def rest_function_text() -> str:
    g = config.REST_GAIN
    return (f"专注t分钟后宜休F(t):t≤15→{g:g}·t/6;15<t<150→{g:g}·(7/14850·t²+407/1485·t−170/99);"
            f"t≥150→{g:g}·(50+5/ln1.2·ln(t/150));例:t=100→{config.compute_F(100):.0f}min")


def prefix_prompt(split: bool = True) -> str:
    """两端共用的原生工具协议；Assistant 同样接收前置说明。"""
    from pathlib import Path
    protocol = (Path(__file__).resolve().parent.parent / "defaults" / "comm_protocol.txt").read_text(encoding="utf-8")
    return protocol.replace("{split_rule}", "")
