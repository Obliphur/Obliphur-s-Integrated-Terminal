# -*- coding: utf-8 -*-
"""沟通模块的常量 (MECHANICS 24): 标签, 概率, 超时, 默认值, 提示词片段.

从 comm.py 拆出来, 给 comm.py 和 comm/ 里各个 mixin 共用.
comm.py 用 `from comm.comm_const import *` 重新导出, 所以外面写的 comm.X 和自检里的裸名字不变.

自检会改 comm.py 里的 FOLLOW_DELAY / CHAR_DELAY / CHAR_DELAY_CJK / CLAUDIA_LEGACIES / ROUTINE_LEGACIES
(默认值在这里); 用到它们的 char_delay / follow_delay / _mk / 升级链 / __init__ 都还在 comm.py,
所以自检改的是那些函数真正读到的那一份.
"""

import os
import re

from comm.comm_persona import companion_base
from comm.comm_prompt import REFUSE
from comm.comm_builtin import CLAUDIA_PREVIOUS, CLAUDIA_BEFORE_ACTION


HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_AVATARS = {"Claudia.txt": "Claudia.png"}    # 自带人设的默认头像 (1.0.426): defaults/avatars/ 里的文件
PRO_LANGS = ("中文", "English", "日本語", "한국어", "Français", "Deutsch", "Español", "Русский")       # 设置里的主动谈话语言可选项 (1.0.407); 也可以自己写别的
MAX_PRO_LANG = 30
PRO_LANG_NOTE = ("\n(OIT 设置, 用户指定) 这条主动发起的话请使用「{lang}」来说; 只限这一条, 之后和用户的对话不受这个限制: "
                 "用户用什么语言, 你就按人设用什么语言回复.")
PRO_NO_REFUSE = ("(OIT 系统提供, 用户看不到) 这是你主动发起的对话, 不能返回" + REFUSE + ". 请按人设主动给用户发一条消息, 话题自己找 (不要透露日程).")
PROACTIVE_FREE_LEFT = 10 * 60         # I. 对方这段空闲 / 休息至少还要剩这么久 (秒), 才会主动发消息
PROACTIVE_DELAY = (60, 180)           # I. 对方进入空闲 / 休息段后多久主动发 (秒)
PROACTIVE_LEAD = (60, 180)            # II / III. 人设日程里的空闲 / 休息开始前多久主动发 (秒)
PROACTIVE_SLEEP_DELAY = 5 * 60        # IV. 人设睡眠开始后多久主动发 (秒)
IDLE_DAILY_SECONDS = 30 * 60          # 每天在"空闲"状态下最多能打开沟通界面这么久 (休息时不限); 超过后空闲时不能用沟通, 休息了才能用
IDLE_TICK_MAX = 5                     # 一次 tick 最多记这么多秒 (电脑睡眠 / 卡住时不会一下子记很多)
BG_STAGGER = 120                      # 非正在聊天的对象主动发消息: 两次请求之间至少隔这么多秒 (一个一个来, 不会同时好几个人找你)
PROACTIVE_LATE = 120                  # 到点之后过了这么久 (程序刚打开 / 刚空下来) 就算了
PROACTIVE_GAP = 10 * 60               # 两条主动消息至少隔这么久 (相邻的日程段不连发)
PROACTIVE_KEEP = 12                   # 记下最近多少个已经处理过的主动消息时间点
SHARE_TAG = "[系统·共享]"               # share data 的系统消息 (不是 "[系统]" 开头: 不算主动消息)
SHARE_COOLDOWN = 30                   # share data: 两次把页面数据发给对方至少隔这么久 (秒)
SHARE_DATA_PAGES = ("timer", "log", "cal", "stats")                  # 有数据可以分享的页面: 计时器 / 整点记录 / 日历 / 统计
SHARE_SKIP_PAGES = ("settings", "comm", "prescript", "reset")        # share data 打开时, 在这些页面里不分享也不显示聊天悬浮窗 (回到沟通 = 结束)
SHARE_PAGE_NAMES = {"timer": "计时器", "log": "整点记录", "cal": "日历", "stats": "统计", "net": "联网", "contract": "合约", "ach": "成就"}
SHIFT_ASK_TTL = 300                   # 用户请求平移起点: 等对方回 [Yes] / [No] 最多这么久 (秒)
SHARE_ASK_TTL = 300                   # 用户打开 share data 要先问对方: 等对方回 [Yes] / [No] 最多这么久 (秒)
SHARE_INVITE_TTL = 5 * 60             # 对方发 [share data] 之后, 邀请有效这么久 (秒, 1.0.428 起 5 分钟, 超时自动清理)
_SHARE_RE = re.compile(r"[\[［]\s*share\s+data\s*[\]］]", re.I)
CUT_TAG = "[系统·切断]"                # 对方切断谈话的系统消息 (不是 "[系统]" 开头: 不算主动消息)
HELD_KINDS = ("focus", "sleep")       # 对方按日程在这两种状态时, 用户发来的消息先由 OIT 存着 (带发送时间), 对方第一次空闲 / 休息时一次性发给它 (MECHANICS 24.3.8)
NET_TAG = "[系统·网络]"                # 限时网络 (规则 XIV): 等了 NET_DELAY 之后 OIT 再调用一次接口 (不是 "[系统]" 开头: 不算主动消息)
NET_DELAY = 2 * 60                    # 对方 [apply for network] 之后等这么久 (秒), OIT 才再次调用接口让它搜索并回复
NET_COOLDOWN = 10 * 60                # 网络关闭之后的冷却期 (秒): 期间的申请无效
NET_RETRY = 60                        # 搜索那次请求失败了, 隔多久再试 (秒)
NET_TRIES = 2                         # 搜索那次请求最多再试几次
NET_KINDS = ("user", "retry", "followup", "proactive", "chase")      # 这几种请求里写 [apply for network] 有效 (搜索那次请求本身里写的不算)
NET_NO_REFUSE = ("(OIT 系统提供, 用户看不到) 网络已经开通, 这一条不能返回" + REFUSE + ". 请现在联网搜索你刚才想查的内容, 并按人设回复用户.")
JOINT_TAG = "[系统·联合行动]"           # 联合行动进展的系统消息 (不是 "[系统]" 开头: 不算主动消息, 不触发提示音)
JOINT_EARLY = 10 * 60                 # 联合行动: 合约可以比约定的开始时间早开始这么久 (秒)
JOINT_LATE = 5 * 60                   # 联合行动: 约定的开始时间过了这么久还没开始合约就算用户放弃 (秒)
JOINT_INVITE_TTL = 5 * 60             # 联合行动: 对方发起的邀请这么久没人接受 / 拒绝就自动清理 (秒, 1.0.428)
JOINT_HP_MAX = 30                     # 联合行动: 一次最多记这么多条生命值损失 (下一次开始休息 / 合约结束时一起告诉对方)
JOINT_SPEAK_WAIT = 3 * 60             # 联合行动: 要让对方回应一句, 但对方正在回别的话: 最多等这么久 (秒)
JOINT_FOLLOW_STEP = 3 * 60            # 联合行动结束后: 用户还在专注 / 睡眠时, 同步阶段每次往后顺延这么久 (秒), 直到用户的休息结束
JOINT_FOLLOW_CAP = 3 * 3600           # 联合行动结束后: 同步阶段最长这么久 (秒)
FOLLOW_KINDS = ("user", "retry")      # 规则 VIII: 回复了这些请求 (用户说的话) 之后, 再调用一次 API, 让对方可以把话分开说
FOLLOW_TAG = "[系统·续]"
FOLLOW_BODY = (FOLLOW_TAG + " 你刚才已经回复了用户. 如果还有想接着说的话, 请现在再发一条 (不要重复上一条); "
               "如果你只想说一次, 返回" + REFUSE + ".")
CATCHUP_TAG = "[系统·补]"             # 回复显示之后才发现用户在等回复的时候又发了消息: 补问一次 (MECHANICS 24.3)
CATCHUP_BODY = (CATCHUP_TAG + " 你上一条回复发出的时候, 用户又发来了新消息 (上面用户那一轮里靠后的几条); 你的上一条回复没有针对它们. "
                "请现在回复这些新消息 (不要重复上一条回复的内容); 如果仍然不方便, 返回" + REFUSE + ".")
FOLLOW_DELAY = 3.0                    # 规则 VIII 的第二次调用先等这么久 (秒): 服务商的提示缓存是异步落盘的 (DeepSeek 说"几秒"),
                                      # 紧跟着调用常常赶不上, 白付一次全量历史; 隔几秒再发, 第二次的前缀才能命中
CHAR_DELAY = 0.1                      # 对方发来的消息: 英文 / 数字 / 符号每个字符晚这么多秒才显示 (像在打字; 排在前一条后面)
CHAR_DELAY_CJK = 0.2                  # 中文字符每个晚这么多秒 (Assistant 是助理, 不装打字, 见 _mk)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


RETRY_GAP = 10 * 60                   # 对方不方便回复时, 多久再把消息交给模型
RETRY_MAX = 12
OP_MIN_LEAD = 5 * 60                  # 对方发起联合行动: 开始时间要离现在至少这么久 (秒), 不然用户来不及准备
OP_OFFER_MAX = 12                     # 给对方看的开始时间最多列几个 (最近的; 定开始时间时 OIT 仍然从全部里找)
COND_JOINT_DAYS, COND_JOINT_N, COND_JOINT_LV = 30, 24, 15      # I.  前 30 天 (含今天) 和对方共同完成 24 场难度 >= 15 的合约
COND_STUDY_DAYS, COND_STUDY_H, COND_IDLE_H = 60, 420.0, 210.0  # II. 前 60 天 (含今天) 专注 >= 420 h, 空闲 <= 210 h
COND_MSGS = 500                       # III. 双方一共发了至少 500 条信息 (现在聊天记录里有的; 本地存的条数 KEEP 要比它大)
COND_TODAY_H = 8.0                    # IV. 今天专注 >= 8 h
COND_KEEP_DAYS = 70                   # 每天的 (专注, 空闲) 秒数留多久 (用户的计时记录只留 7 天, 所以 OIT 自己每天存一份)
COND_NORMAL = {"joint_days": COND_JOINT_DAYS, "joint_n": COND_JOINT_N, "joint_lv": COND_JOINT_LV, "study_days": COND_STUDY_DAYS,
               "study_h": COND_STUDY_H, "idle_h": COND_IDLE_H, "msgs": COND_MSGS, "today_h": COND_TODAY_H}      # 用户提出发展亲密关系: 人设里 [condition] 的五个条件
COND_STRICT = {"joint_days": 30, "joint_n": 27, "joint_lv": 20, "study_days": 60,
               "study_h": 480.0, "idle_h": 180.0, "msgs": 1000, "today_h": 9.0}     # 对方主动告白 (1.0.397): 每次进入特殊空闲时由 OIT 判定, 条件比上面的严格 (人设 Claudia.txt 里「主动触发的表白」)
LOVE_NO_DAYS = 15                     # 用户拒绝了对方的告白 / 对方自己否决了 (回 [refuse to reply]) 之后, 这么多天内不再判定主动告白
LOVE_TAG = "[系统·告白]"               # 告白相关的系统消息 (不是 "[系统]" 开头: 不算主动消息)
_ACCEPT_RE = re.compile(r"[\[［]\s*accept\s*[\]］]", re.I)       # 前置 prompt XVII: 用户提出发展亲密关系, 对方决定接受
MAX_INFO_ROUNDS = 2                   # 一次回复里, 对方最多可以要几次资料 ([contract])
CHAT_TIMEOUT = 240.0                  # 一次请求最多等多久 (回复长 / 资料多时要久一点)
KEEP = 600                            # 本地最多存多少条 (每次请求带多少条看 HISTORY_SENT, 和这个无关; [condition] 的信息条数也数这里)
SYNC_N = 60                           # 随设备同步最近多少条
MAX_PROFILE = 5000                    # 用户自己填的身份背景最多多少字
MEM_EVERY = 40                        # 每这么多条新消息, 让模型整理一次"记忆卡"
MEM_GAP = 3 * 3600                    # 两次整理至少隔这么久 (秒): 一口气发几十条时不会连着调
MEM_TOKENS = 3000                     # 整理记忆卡一次请求的 max_tokens: 开了思考, 要比思考预算 (ai_assist.THINK_BUDGET) 大, 还得留出记忆卡本身的长度
MEM_PROMPT = ("你是 OIT 沟通模块的记忆整理器. 给你「现有记忆卡」和「最近一段对话」, 输出更新后的记忆卡.\n"
              "只保留以后还有用的事实: 用户的身份, 偏好, 计划, 约定, 禁区, 以及没聊完的话题和只处理了一部分的请求 (写明大概是什么时候的事).\n"
              "每次整理都要先逐条检查现有记忆卡, 对照「当前时间」和最近的对话, 删掉已经失效 / 过期的内容: 已经聊完 / 办完的话题和请求, 已经过去的计划和约定, 被新对话推翻的事实.\n"
              "重点记:\n用户的性格,反应模式,情感类型;用户希望AI记下的内容;用户的爱好;\n不要记日常琐事.\n"
              "用户的'简单要求'(例如让你多说点话)一般只在本轮对话有效, 不记; 但如果现有记忆卡里有「【系统记录】已确立亲密关系」那一行(由 OIT 记录, 不要把它抄进你输出的记忆卡), 这之后用户的简单要求要长期保留, 检查失效 / 过期内容时也不能清除. 其余哪些内容该长期保留 / 不能清除, 以「对方的人设」里写的为准.\n"
              "删掉寒暄, 重复, 一次性的内容. 每条一行, 以 \"- \" 开头, 不超过 40 字, 最多 15 条.\n"
              "和新对话冲突时以新对话为准; 不确定的条目末尾加 \"?\". 不要编造对话里没有的事.\n"
              "只输出记忆卡本身, 不要解释, 不要标题, 不要代码块.")
MEM_PROMPT += "\n所有【系统记录】均由 OIT 固定保存, 不要改写、删除或抄入输出; 消息数量门槛的达成记录永久有效, 不等于其他条件也已满足."
STATUS_TEXT = {"focus": "专注中", "sleep": "睡眠中", "rest": "休息中", "idle": "空闲"}
STATUS_COLORS = {"rest": "#9CCC65", "sleep": "#283593", "focus": "#FDD835", "idle": "#9E9E9E"}   # 聊天页状态前面的小圆点
# 日程跟随 (1.0.406, MECHANICS 24.17): 对方的状态 (按日程) 变了, 问用户要不要跟着变; 文字里指代对方一律用 {name} (人设名)
SF_TEXT = {"focus": "{name} 开始专注了, 要跟着专注吗?", "rest": "{name} 开始休息了, 要跟着休息吗?",
           "sleep": "{name} 去睡觉了, 要跟着睡眠吗?", "idle": "{name} 现在空闲了, 要跟着空闲吗?"}
SF_STATE = {"focus": "focus", "rest": "break", "sleep": "sleep", "idle": "idle"}      # 对方的状态 → 计时器的同一种状态 (state_fn 的值)
CHAT_TOKENS = 4096
CHAT_TEMPERATURE = 0.8

# ---- 什么时候开思考 (第一层: 规则, 不额外调用 API) ----
# 沟通默认关思考 (省 token, 也让人设的语气稳); 下面这些情况容易漏掉人设里的规则, 这一次请求开思考 (设置 → 沟通 → 思考 可以关掉):
THINK_KINDS = ("proactive", "joint_reply", "chase", "love", "love_reply")      # 系统触发的: 主动发起 (要选话题 / 可能发起联合行动), 回应联合行动的接受 / 拒绝, 追问
THINK_NEW_AFTER = 180                                    # 对方此刻按日程该在专注 / 睡眠, 而用户这时开始一段新的谈话 (上一条对方的消息已经是 3 分钟以前): 要判断回不回


DEFAULTS = {"persona": "", "show_avatar": True,
            "avatar_me": "", "avatar_ai": "", "default_avatars": 0, "pro_key": "", "retry": {}, "clear_ts": 0, "seeded": False, "assistant_seeded": False,
            "claudia_migrated": False, "claudia_modified": False, "claudia_notified": "", "claudia_base": "", "routine_base": "", "background_base": "",
            "legacy_done": False, "joint_last": [], "user_profile": "", "split_send": True,
            "bg_pro": True, "mute_pro": [], "idle_use": {}, "joint": {}, "sched_follow": False, "pro_lang": "",
            "mem_auto": "", "mem_fixed": [], "mem_upto": 0, "mem_ts": 0,  # 模型摘要与系统固定记录
            "shift": [],                                     # 起点平移记录 (跟着聊天对象走, 同步; 不改人设文件)
            "love": {}, "msg_gone": 0,                       # 亲密关系状态 (跟着聊天对象走, 同步) / 被 KEEP 砍掉的信息条数 (本机)
            "alarm_co": {},                                  # 共时闹钟 (1.0.359): {pid, devs: [响铃的设备 id], ts}; pid 为空 = 关闭
            "net": {}}                                       # 限时网络 (规则 XIV): {ts, pid, n, due, until, cool, st: wait | on | cool, tries}


# ══════════════ 前置 prompt 与 README ══════════════




# 更早版本内置过的文本 (升级时判断文件是不是没被用户改过, 一字不差才换成当前版本).
# (claudia_base / routine_base / background_base) 判断, 以后改默认人设再按需往里放.
CLAUDIA_LEGACIES = (CLAUDIA_PREVIOUS, CLAUDIA_BEFORE_ACTION)
ROUTINE_LEGACIES = ()
CHAT_KEYS = ("clear_ts", "retry", "pro_key", "mem_auto", "mem_fixed", "mem_upto", "mem_ts", "cut_until", "love", "msg_gone", "shift")   # 跟着聊天对象走的几项 (存在各自的聊天文件里)


























base_persona_of = companion_base





# ══════════════ 请求内容 ══════════════
# 规则 IX: 对方期待用户回复时, 在话的结尾写 [waiting for reply]:[N] (N 分钟); 所有 [] 里的内容都不显示在聊天里 (记录里的"给模型看的"那份 api 保留原文)
_OP_RE = re.compile(r"\[\s*operation\s*\]", re.I)         # 对方回 [operation] = 想发起联合行动 (前置 prompt XII)
OP_KINDS = ("proactive", "user", "retry", "followup")      # 这几种请求里写 [operation] 有效: 主动发起 (规则 I - IV), 或回复用户 (用户邀请 / 要求发起); 追问 / 回应接受拒绝等系统触发的不行
_OP_STRIP_RE = re.compile(r"\[\s*operation\s*\](?:\s*\[[\d,\s，、]*\])?", re.I)     # [operation] 和紧跟的编号 [1,2,3]


















__all__ = [
    "HERE",
    "DEFAULT_AVATARS",
    "PRO_LANGS",
    "MAX_PRO_LANG",
    "PRO_LANG_NOTE",
    "PRO_NO_REFUSE",
    "PROACTIVE_FREE_LEFT",
    "PROACTIVE_DELAY",
    "PROACTIVE_LEAD",
    "PROACTIVE_SLEEP_DELAY",
    "IDLE_DAILY_SECONDS",
    "IDLE_TICK_MAX",
    "BG_STAGGER",
    "PROACTIVE_LATE",
    "PROACTIVE_GAP",
    "PROACTIVE_KEEP",
    "SHARE_TAG",
    "SHARE_COOLDOWN",
    "SHARE_DATA_PAGES",
    "SHARE_SKIP_PAGES",
    "SHARE_PAGE_NAMES",
    "SHIFT_ASK_TTL",
    "SHARE_ASK_TTL",
    "SHARE_INVITE_TTL",
    "_SHARE_RE",
    "CUT_TAG",
    "HELD_KINDS",
    "NET_TAG",
    "NET_DELAY",
    "NET_COOLDOWN",
    "NET_RETRY",
    "NET_TRIES",
    "NET_KINDS",
    "NET_NO_REFUSE",
    "JOINT_TAG",
    "JOINT_EARLY",
    "JOINT_LATE",
    "JOINT_INVITE_TTL",
    "JOINT_HP_MAX",
    "JOINT_SPEAK_WAIT",
    "JOINT_FOLLOW_STEP",
    "JOINT_FOLLOW_CAP",
    "FOLLOW_KINDS",
    "FOLLOW_TAG",
    "FOLLOW_BODY",
    "CATCHUP_TAG",
    "CATCHUP_BODY",
    "FOLLOW_DELAY",
    "CHAR_DELAY",
    "CHAR_DELAY_CJK",
    "_CJK_RE",
    "RETRY_GAP",
    "RETRY_MAX",
    "OP_MIN_LEAD",
    "OP_OFFER_MAX",
    "COND_JOINT_DAYS",
    "COND_JOINT_N",
    "COND_JOINT_LV",
    "COND_STUDY_DAYS",
    "COND_STUDY_H",
    "COND_IDLE_H",
    "COND_MSGS",
    "COND_TODAY_H",
    "COND_KEEP_DAYS",
    "COND_NORMAL",
    "COND_STRICT",
    "LOVE_NO_DAYS",
    "LOVE_TAG",
    "_ACCEPT_RE",
    "MAX_INFO_ROUNDS",
    "CHAT_TIMEOUT",
    "KEEP",
    "SYNC_N",
    "MAX_PROFILE",
    "MEM_EVERY",
    "MEM_GAP",
    "MEM_TOKENS",
    "MEM_PROMPT",
    "STATUS_TEXT",
    "STATUS_COLORS",
    "SF_TEXT",
    "SF_STATE",
    "CHAT_TOKENS",
    "CHAT_TEMPERATURE",
    "THINK_KINDS",
    "THINK_NEW_AFTER",
    "DEFAULTS",
    "CLAUDIA_LEGACIES",
    "ROUTINE_LEGACIES",
    "CHAT_KEYS",
    "base_persona_of",
    "_OP_RE",
    "OP_KINDS",
    "_OP_STRIP_RE",
]
