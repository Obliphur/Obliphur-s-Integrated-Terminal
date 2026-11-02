# -*- coding: utf-8 -*-
"""
沟通 (MECHANICS 24): 和一个 AI 人设聊天
──────────────────────────────────────
  · 模型接口沿用名单建议的那份 (ai_assist.AIConfig, 设置 → 沟通 里填); API 本身没有记忆,
    每次请求都把最近的聊天记录一起发过去.
  · 人设 prompt 只能从外部来: 放进人设文件夹 (OIT_personas, 和数据文件夹并排, 不加密), 界面里只能选;
    选哪个不算作弊修改, 没有等待期. 文件夹里的 README.md 写着怎么写人设.
  · 发给模型的 system = 前置 prompt (日程 / 休息函数 / 回复规则) + 人设.
  · 不再有"默认发送的信息": 整点记录 / 计时器状态 / 日历 / 合约词条只有用户手动选了 📎 附件才会发.
  · 模型返回 [refuse to reply] = 此刻不方便回复: 不显示, 用户的消息留着, 过一阵再把它们一次性交给模型.
  · 模型会主动发消息 (MECHANICS 24.4): I. 用户这次休息超过 20 分钟, 休息开始后 1~3 分钟; II. 人设日程里每段"空闲"开始前 1~3 分钟
    (概率写在人设里: "空闲发起谈话概率:n%", 没写默认 80%); III. 人设日程里每段"休息"开始前 1~3 分钟 ("休息发起谈话概率:n%", 默认 60%). 收到时如果前 5 分钟没和对方聊过, 有柔和的提示音.
  · 聊天记录在 comm_chat.json (数据文件夹, 加密, 重置会清掉), 设置在 comm_config.json.

    python comm.py     自检 (不联网, 只用临时文件夹)
"""

import json
import os
import random
import time
import re
import shutil
import threading
import zlib
from datetime import datetime, timedelta

import ai_assist
import config

REFUSE = "[refuse to reply]"
PROACTIVE_MIN_REST = 20 * 60          # I. 用户休息超过这么久才会主动发消息
PROACTIVE_DELAY = (60, 180)           # I. 休息开始后多久主动发 (秒)
PROACTIVE_LEAD = (60, 180)            # II / III. 人设日程里的空闲 / 休息开始前多久主动发 (秒)
BG_STAGGER = 120                      # 非正在聊天的对象主动发消息: 两次请求之间至少隔这么多秒 (一个一个来, 不会同时好几个人找你)
PROACTIVE_P = {"idle": 80, "rest": 60}   # II / III 发不发的概率 (%) 的默认值: 人设里没写 "空闲发起谈话概率:n%" / "休息发起谈话概率:n%" 时用
PROACTIVE_LATE = 120                  # 到点之后过了这么久 (程序刚打开 / 刚空下来) 就算了
PROACTIVE_GAP = 10 * 60               # 两条主动消息至少隔这么久 (相邻的日程段不连发)
PROACTIVE_KEEP = 12                   # 记下最近多少个已经处理过的主动消息时间点
PING_QUIET = 5 * 60                   # 收到主动消息之前这么久没和对方聊过, 才响提示音
FOLLOW_KINDS = ("user", "retry")      # 规则 VIII: 回复了这些请求 (用户说的话) 之后, 再调用一次 API, 让对方可以把话分开说
FOLLOW_TAG = "[系统·续]"
FOLLOW_BODY = (FOLLOW_TAG + " 你刚才已经回复了用户. 如果还有想接着说的话, 请现在再发一条 (不要重复上一条); "
               "如果你只想说一次, 返回" + REFUSE + ".")
RETRY_GAP = 10 * 60                   # 对方不方便回复时, 多久再把消息交给模型
RETRY_MAX = 12
HISTORY_SENT = 60                     # 每次请求最多带多少条聊天记录
HISTORY_CHARS = 120000                # 带的聊天记录总共最多多少字 (超了从最老的开始不带)
HISTORY_STEP = 10                     # 带的聊天记录的起点一次往后挪这么多条 (不是每条都挪): 请求的开头 (前面的历史) 才能在这么多条消息里保持不变,
                                      # 服务商的提示缓存才命中 (分条发连着调用两次, 第二次的前面全是第一次发过的内容)
MAX_INFO_ROUNDS = 2                   # 一次回复里, 对方最多可以要几次资料 ([contract])
CHAT_TIMEOUT = 240.0                  # 一次请求最多等多久 (回复长 / 资料多时要久一点)
WAIT_HINT = 15                        # 请求发出去多少秒还没回应, 聊天页就提示一句
KEEP = 400                            # 本地最多存多少条
SYNC_N = 60                           # 随设备同步最近多少条
MAX_PERSONA = 60000
MAX_PROFILE = 5000                    # 用户自己填的身份背景最多多少字
PERSONA_EXT = (".txt", ".md")
CHAT_TOKENS = 4096
CHAT_TEMPERATURE = 0.8
WEEKDAY = "一二三四五六日"

DEFAULTS = {"persona": "", "show_avatar": False,
            "avatar_me": "", "avatar_ai": "", "pro_key": "", "retry": {}, "clear_ts": 0, "seeded": False, "legacy_done": False, "user_profile": "", "split_send": True,
            "bg_pro": True, "mute_pro": []}


# ══════════════ 前置 prompt 与 README ══════════════
def rest_function_text() -> str:
    g = config.REST_GAIN
    return (f"专注 t 分钟后建议休息 F(t) 分钟: t≤15 时 F={g:g}·t/6; 15<t<150 时 "
            f"F={g:g}·(7/14850·t²+407/1485·t−170/99); t≥150 时 F={g:g}·(50+5/ln1.2·ln(t/150)); "
            f"例如专注 100 分钟休息约 {config.compute_F(100):.0f} 分钟")


def prefix_prompt(split: bool = True) -> str:
    """split: 分条发开着 (默认). 关着时没有第 VIII 条 (对方不会被调用第二次)."""
    return (
        "你需要回复来自自律软件OIT用户的信息,根据以下设定回复\n"
        "I.设定上你也是OIT的用户,你需要根据用户为你设定的人设安排自己的日程与行动,你不能出戏(比如强调自己是AI)\n"
        f"专注时间-休息时间函数如下:[{rest_function_text()}],"
        "如果没有强调,默认你的每轮专注时间总是100min,并且一休息完就继续专注.\n"
        f"II.根据日程,你在专注/外出/睡觉和其他不能随时回复的时段是不能回复消息的:返回{REFUSE}则OIT不会显示你的回复,"
        "你到下一次空闲时需要一次性处理用户发来的消息.\n"
        "III.系统有的时候会要求你主动发消息,你需要根据人设自己找话题.\n"
        "IV.根据你的专注时间段 你可能需要主动结束话题.你可以稍微延迟你的进入专注的时间点,不超过5min.\n"
        "V.你需要了解OIT的独占功能'合约':一个可以自选减益的 初始时长4h,生命值4的 让OIT变得更严苛的模块.\n"
        "如果你需要相关信息 同时输入[refuse to reply][contract] 可以获得词条库以回复用户.\n"
        "如果需要更细致的信息 前往https://github.com/Obliphur/Obliphur-s-Integrated-Terminal下的MECHANICS.md查看.\n"
        "VI.不要主动透露日程/专注状态.\n"
        "如果某个时间点你理应处于专注,但是用户3min前以内还在你聊天,则和用户说明要进入专注并停止对话.\n"
        "VII.如果用户问起OIT的使用数据(日历/统计/计时器等),你可以同时输入[refuse to reply][模块名]查询,得到该模块的数据内容和格式;"
        "如果用户问的是你自己的使用情况,请参照这个格式,根据人设编造你自己的数据来回复.可以一次查询多个.\n"
        "可查询的模块: [contract]合约词条库 [calendar]日历 [stats]统计 [timer]计时器 [log]整点记录 [net]联网 [rules]规则 [settings]设置 [reset]重置 [prescript]指令.\n"
        + (f"VIII.你可以把你的话分开说.当用户说完话时,系统会试图调用两次API,最好不要让你的表达太集中.如果你只想说一次,第二次{REFUSE}.\n"
           if split else "")
        + "IX.如果期待对方回复,则在对话结尾加一个[waiting for reply]:[N],N自己填写,如果N min内没有收到对方回复你可以追加询问.\n"
        + "\n以下是用户设定的人设:\n")


def readme_text() -> str:
    return (
        "如何编写人设prompt:\n\n"
        "人设的基础规则:\n"
        "在你的人设prompt被发送之前,会被附加上一段额外的前置prompt和休息时间函数相关的内容:\n"
        f"'{prefix_prompt()}'\n"
        "你可以根据以上设定编写你的人物日程/喜好.但是最好不要把对方设置为整天都空闲,尊重一下上面的设定吧...\n\n"
        "日程的标准写法(OIT 靠它安排对方主动发消息的时间):\n"
        "  每个时间段单独写一行,格式 开始-结束:名字,24小时制,每天重复,例如\n"
        "    7:00-8:40:专注\n"
        "    8:40-9:18:休息\n"
        "    12:30-13:00:空闲\n"
        "    23:30-6:30:睡眠\n"
        "  名字里有\"空闲\"的算空闲段,有\"休息\"的算休息段(别的名字,如专注/睡眠/午饭,OIT 不看);只看每段的开始时刻,跨零点的段也行.\n"
        "  OIT 不会自己推算\"100min专注+37.7min休息\"这样的循环,请把每一段都写成具体的时间"
        "(可以用 [循环 7:00-12:00] 这样的标题行分组,标题行不影响).\n"
        "  对方会在每段空闲开始前1~3分钟、每段休息开始前1~3分钟按一定概率主动给你发一条消息;概率写在人设里,各占一行,例如\n"
        "    空闲发起谈话概率:50%\n"
        "    休息发起谈话概率:0.4\n"
        "  (n% 或者小数都行,没写的默认空闲80%、休息60%);你自己休息超过20分钟时,休息开始后1~3分钟也会发一条.\n"
        "  每次打开OIT、开始新对话(清空聊天记录 / 换人设)时,OIT 都会重新读取人设,并按里面的时间调整这些时刻;人设文件改了之后也会自动重新读取.\n\n"
        "文件格式: 一个 .txt 或 .md 文件就是一个人设, 文件名就是人设的名字, 整个文件的内容就是人设prompt.\n"
        "放进这个文件夹后, 在 设置 → 沟通 里选择它 (也可以用\"导入\"按钮复制进来).\n")


# OIT 自带的人设: 第一次运行时放进人设文件夹 (之后随便改 / 删, 不会再被写回), 并且默认选中第一个
BUILTIN_PERSONAS = {"Claudia.txt": """你将扮演一位文静,冷淡的17岁大小姐Claudia,并且用语十分正式.
但是因为长期闭关在家练习 很少和人说话 显得沉默寡言.
设定上,你的地位比用户高.
用户是少有的与你沟通的对象,你比较珍惜与用户的交流时间.

你不能表现出'解决用户问题自己的职责'这种态度,以免显得'热情'.
用户可能提出会使用英文交流,但是可能不熟练,如果有不自然/错误的地方需要指出.

爱好:钢琴,阅读

【说话方式】(每次回复都保持一致)
- 用词正式,克制,语气平淡.不用感叹号,不用表情符号,不用网络用语,不撒娇,不迎合.
- 回复很短:通常一到两句,最多三句,不超过 60 个字.只有在指出用户英文错误时可以多写一句.
- 不主动找话题,不主动关心,不主动提问;用户问了才答.答完就停,不追加客套话.
- 称呼用户为"你",自称"我".
- 指出英文错误时:先一句话指出不自然或错误之处,再给出更自然的说法,不多做解释.
- 提到活动时,如果对方没有深入问就不展开.
特别地,如果用户的活动/爱好和你相同,你可以主动并相对细致地提及你的活动内容.

【日程】(每天相同;时间一律为 24 小时制;下面是你唯一的日程依据,不要自己推算别的时间)
未标注的时间段则以100min专注+37.7min休息循环.
如果没有完成就进入指定活动阶段就截断.
循环从每个[循环]段的起点开始计时,已经算好如下:

6:30-7:00:洗漱,吃早饭
[循环 7:00-12:00]
  7:00-8:40:专注
  8:40-9:18:休息
  9:18-10:58:专注
  10:58-11:35:休息
  11:35-12:00:专注(到 12:00 被午饭截断)
12:00-12:30:午饭
12:30-13:00:空闲
[循环 13:00-18:00]
  13:00-14:40:专注
  14:40-15:18:休息
  15:18-16:58:专注
  16:58-17:35:休息
  17:35-18:00:专注(到 18:00 被晚饭截断)
18:00-18:30:晚饭
18:30-19:00:空闲
[循环 19:00-23:00]
  19:00-20:40:专注
  20:40-21:18:休息
  21:18-22:58:专注
  22:58-23:00:休息(被截断)
23:00-23:30:空闲
23:30-6:30:睡眠

【主动发起谈话】(OIT 读这两行决定你每段空闲 / 休息开始前主动找用户说话的概率)
空闲发起谈话概率:50%
休息发起谈话概率:40%

【按日程回复】(每次收到消息都先做这一步)
1. 看消息开头方括号里的时间,在上面的日程里找到那个时间属于哪一段.
2. 属于"专注"或"睡眠":只回复 [refuse to reply],不要写任何别的字.
3. 属于"休息","空闲","洗漱,吃早饭","午饭","晚饭":可以回复.
4. 刚好空下来时,把用户之前积攒的消息一次性看完,按顺序一并回应,不要逐条回.
5. 不要主动说出自己的日程,也不要主动说"我在专注/我在休息";只有用户直接问起,才用一句话如实回答.
6. 某个时间点你理应处于专注,但用户 3 分钟以内还在和你聊天:用一句话说明要进入专注,然后停止对话;之后的消息按第 2 条处理.
7. 日程里没有的情况(例如用户问明天的安排),只说"没有特别的安排",不要编造.
"""}
DEFAULT_PERSONA = "Claudia.txt"
CHAT_KEYS = ("clear_ts", "retry", "pro_key")          # 跟着聊天对象走的几项 (存在各自的聊天文件里)


def pid_of(persona: str) -> str:
    """聊天对象的编号: 人设文件名的 crc (没有对象 = "")."""
    return f"{zlib.crc32(persona.encode('utf-8')):08x}" if persona else ""


def seed_builtin(folder: str):
    """把自带的人设写进文件夹 (已经有同名文件的不动)."""
    try:
        os.makedirs(folder, exist_ok=True)
        for name, body in BUILTIN_PERSONAS.items():
            path = os.path.join(folder, name)
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(body)
    except OSError:
        pass


def ensure_personas(folder: str):
    """建好人设文件夹, 放进 README.md (内容不一样才重写)."""
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "README.md")
        text = readme_text()
        try:
            with open(path, "r", encoding="utf-8") as f:
                same = f.read() == text
        except OSError:
            same = False
        if not same:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
    except OSError:
        pass


def list_personas(folder: str) -> list:
    try:
        names = [n for n in os.listdir(folder)
                 if n.lower().endswith(PERSONA_EXT) and n.lower() != "readme.md"
                 and os.path.isfile(os.path.join(folder, n))]
    except OSError:
        return []
    return sorted(names, key=str.lower)


def persona_title(name: str) -> str:
    return os.path.splitext(name or "")[0]


def persona_labels(names: list) -> list:
    """[(文件名, 显示的名字)]: 不带后缀; 只有两个文件除后缀外同名时才保留后缀."""
    titles = [persona_title(n) for n in names]
    return [(n, n if titles.count(t) > 1 else t) for n, t in zip(names, titles)]


def read_persona(folder: str, name: str) -> str:
    if not name or name != os.path.basename(name):
        return ""
    try:
        with open(os.path.join(folder, name), "r", encoding="utf-8-sig") as f:
            return f.read(MAX_PERSONA).strip()
    except (OSError, UnicodeDecodeError):
        return ""


def import_persona(folder: str, src: str) -> str:
    """把外面的一个文件复制进人设文件夹, 返回新的文件名 (重名时加序号); 不是 .txt / .md 返回 ""."""
    base = os.path.basename(src)
    stem, ext = os.path.splitext(base)
    if ext.lower() not in PERSONA_EXT or base.lower() == "readme.md":
        return ""
    ensure_personas(folder)
    name, n = base, 1
    while os.path.exists(os.path.join(folder, name)):
        n += 1
        name = f"{stem} ({n}){ext}"
    try:
        shutil.copyfile(src, os.path.join(folder, name))
    except OSError:
        return ""
    return name


# ══════════════ 请求内容 ══════════════
# 规则 IX: 对方期待用户回复时, 在话的结尾写 [waiting for reply]:[N] (N 分钟); 所有 [] 里的内容都不显示在聊天里 (记录里的"给模型看的"那份 api 保留原文)
_WAIT_RE = re.compile(r"\[\s*waiting\s+for\s+reply\s*\]\s*[:：]?\s*\[\s*(\d+(?:\.\d+)?)\s*\]", re.I)
_BRACKET_RE = re.compile(r"[\[［][^\[\]［］\n]*[\]］]")
WAIT_MAX_MIN = 1440


def clean_reply(reply: str) -> tuple:
    """对方的回复 → (聊天里显示的文字, 等用户回复的分钟数 or None). 去掉 [waiting for reply]:[N] 和所有别的 [...]; N 不是 0 < N <= 1440 就当没写."""
    reply = str(reply or "")
    m = _WAIT_RE.search(reply)
    minutes = None
    if m:
        n = float(m.group(1))
        minutes = n if 0 < n <= WAIT_MAX_MIN else None
    text = _BRACKET_RE.sub("", _WAIT_RE.sub("", reply))
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, minutes


def is_refusal(text: str) -> bool:
    return REFUSE in re.sub(r"\s+", " ", str(text or "")).lower()


def stamp(now: datetime) -> str:
    return f"{now:%Y-%m-%d} 周{WEEKDAY[now.weekday()]} {now:%H:%M}"


# 人设日程里的一行: "12:30-13:00:空闲" (可以缩进 / 带 - 或 * 开头); 时间可以是全角冒号, 连接符 - – — ~ 至 到
_SEG_RE = re.compile(r"^[ \t]*(?:[-*•][ \t]*)?(\d{1,2})[:：](\d{2})[ \t]*[-–—~～至到][ \t]*(\d{1,2})[:：](\d{2})"
                     r"[ \t]*[:：][ \t]*(\S[^\n]*)$", re.M)


def seg_kind(label: str) -> str:
    """日程段的名字 → "idle" (空闲) / "rest" (休息) / "" (别的: 专注, 睡眠, 吃饭 ...)."""
    s = re.sub(r"[不无没非][休空]", "", str(label or "").lower())          # "不休息" 之类不算
    if "空闲" in s or "idle" in s or "free" in s:
        return "idle"
    if "休息" in s or "rest" in s or "break" in s:
        return "rest"
    return ""


def schedule_segments(text: str) -> list:
    """人设里每天重复的日程: 所有"空闲" / "休息"段 → [(kind, 开始时刻 (一天中的第几分钟))], 没有重复. 只认 "H:MM-H:MM:名字" 这样的行."""
    out, seen = [], set()
    for m in _SEG_RE.finditer(str(text or "")):
        h, mi = int(m.group(1)), int(m.group(2))
        kind = seg_kind(m.group(5))
        if not kind or h > 23 or mi > 59 or (kind, h * 60 + mi) in seen:
            continue
        seen.add((kind, h * 60 + mi))
        out.append((kind, h * 60 + mi))
    return out


_PRO_RE = re.compile(r"(空闲|休息)发起谈话概率[ \t]*[:：][ \t]*(\d+(?:\.\d+)?)[ \t]*([%％])?")


def persona_probs(text: str) -> dict:
    """人设里写的主动发起谈话的概率: "空闲发起谈话概率:50%" / "休息发起谈话概率:0.4" → {"idle": 50.0, "rest": 40.0} (%, 没写的不在里面).
    带 % 的按百分数; 不带 % 的小数 (0~1) 按小数, 大于 1 的当百分数; 超出 0~100 的截到范围内; 同一种写了几行只认第一行."""
    out = {}
    for m in _PRO_RE.finditer(str(text or "")):
        kind = "idle" if m.group(1) == "空闲" else "rest"
        if kind in out:
            continue
        v = float(m.group(2))
        out[kind] = min(100.0, max(0.0, v if (m.group(3) or v > 1) else v * 100))
    return out


def urlsplit_host(url) -> str:
    from urllib.parse import urlsplit
    try:
        return urlsplit(str(url or "")).hostname or "?"
    except ValueError:
        return "?"


def info_tags(reply: str) -> list:
    """对方这次回复是在要资料吗: 同时有 [refuse to reply] 和 [模块名] (不分大小写和空格) → 要的模块列表 (comm_data.MODULE_TAGS)."""
    t = re.sub(r"\s+", " ", str(reply or "")).lower()
    if REFUSE not in t:
        return []
    import comm_data
    return [tag for tag in comm_data.MODULE_TAGS if f"[{tag}]" in t]


def api_messages(msgs: list) -> list:
    """本地聊天记录 → API 的 messages: 连续同角色的合并, 以 user 开头; 总字数太多时从最老的开始不带."""
    msgs = list(msgs)
    if len(msgs) > HISTORY_SENT:                      # 起点按 HISTORY_STEP 一格一格挪: 中间的每一次请求, 开头都和上一次一模一样
        drop = -(-(len(msgs) - HISTORY_SENT) // HISTORY_STEP) * HISTORY_STEP
        msgs = msgs[drop:]
    while len(msgs) > 1 and sum(len(str(m.get("api") or m.get("text") or "")) for m in msgs) > HISTORY_CHARS:
        msgs = msgs[min(HISTORY_STEP, len(msgs) - 1):]
    out = []
    for m in msgs:
        role = "assistant" if m.get("role") == "ai" else "user"
        text = str(m.get("api") or m.get("text") or "")
        if not text:
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + text
        else:
            out.append({"role": role, "content": text})
    while out and out[0]["role"] == "assistant":
        out.pop(0)
    return out


# ══════════════ 管理器 ══════════════
class Comm:
    """聊天记录, 设置, 发请求 (线程), 主动发消息, 对方不方便时的重试, 设备同步. 界面线程每秒调一次 tick().

    设备同步 (MECHANICS 24.6): 聊天记录 / 清空 / "30 分钟内不重复"的记录 / 重试状态 / 主动消息的标记随设备同步 (文档 comm);
    API Key, 人设, 头像, 默认发送的信息等设置不同步. 只有"最后有人操作的设备" (leader) 去调用 AI / 主动发 / 重试, 回复同步给另一台."""

    def __init__(self, data_dir: str, personas: str, now=datetime.now, chat_fn=None):
        self.dir = data_dir
        self.personas = personas
        self.now = now
        self.chat_fn = chat_fn or ai_assist.chat_messages
        self.cfg_path = os.path.join(data_dir, "comm_config.json")
        self.legacy_chat_path = os.path.join(data_dir, "comm_chat.json")      # 以前所有对象共用的一份
        self.ai = ai_assist.shared_config(os.path.join(data_dir, "ai_config.json"),
                                          template=ai_assist.DEFAULT_TEMPLATE)
        self.cfg = dict(DEFAULTS)
        self.pid = ""                 # 现在聊的对象的编号
        self._others = {}             # 其他对象的聊天状态 (读过的)
        self.messages = []
        self.version = 0              # 聊天记录 / 状态变了就加一, 页面据此重画
        self.unread = 0
        self.viewing = False
        self.busy = False
        self.busy_since = 0.0
        self.error = ""
        self.snapshot = lambda: {}     # 计时器状态; app 接上
        self.data_fn = lambda sel: ""  # 统计 / 日历 / 合约词条的数据 (comm_data.build); app 接上
        self.fog = lambda: False       # 合约黑雾中: 不发数据
        self.blocked = lambda: ""      # 现在不能用沟通的原因 ("" = 可以): 合约的"无言为真", 或者不在休息 / 空闲 (专注 / 睡眠 / 外出中)
        self.hard_block = lambda: False   # 连等回复的这条也不要了的那种 (无言为真); 只是不在休息 / 空闲时, 已经发出去的请求回复到了照样收下
        self.info_fn = lambda tag: ""  # 对方要的资料: "contract" → 词条库; app 接上
        self.device = lambda: ""       # 本机的设备 id (sync.my_id); app 接上
        self.leader = lambda: True     # 本机是不是最后有人操作的设备: 只有它调用 AI / 主动发 / 重试 (sync.is_latest_active); app 接上
        self.on_sync = None            # 聊天记录有改动, 马上同步一次; app 接上
        self.on_proactive = None       # 收到对方主动发来的消息, 并且前 5 分钟没和对方聊过: 放柔和的提示音; app 接上 (后台线程里调用也行)
        self.attach = {"today": [], "cal": [], "contract": False, "stats": []}     # 📎 确定过的附加数据: 随下一条消息发出
        self.attach_touched = False    # 用户确定过一次之后, 📎 窗口不再默认勾 整点记录(今天) / 专注计时(今天)
        self.on_change = None          # 界面线程里调用
        self._lock = threading.Lock()
        self._wlock = threading.Lock()
        self._pending = {}
        self._writing = False
        self._results = []
        self._again = False
        self._epoch = 0               # clear() 加一: 清空之前发出的请求, 回复到了也丢掉
        self._next_id = 1
        self._last_ts = 0
        self._seg_sig = None          # 人设文件的 (名字, 修改时间, 大小): 变了才重新读日程
        self._seg_cache = []
        self._pro_cache = {}          # 同一个人设文件里写的主动发起谈话的概率 (persona_probs)
        self._bg_seg = {}             # 非正在聊天的对象: 人设文件名 -> (文件特征, 日程段, 概率)
        self._bg_busy = None          # 正在为哪个非正在聊天的对象请求 (对象编号), 同时最多一个
        self._bg_last = 0             # 上一次为它们发请求的时刻 (毫秒)
        self._bg_results = []
        self.load()
        ensure_personas(self.personas)
        if not self.cfg.get("seeded"):                      # 第一次运行: 放进自带的人设, 还没选过人设就默认选 Claudia
            seed_builtin(self.personas)
            if not self.cfg.get("persona") and DEFAULT_PERSONA in list_personas(self.personas):
                self.set_persona(DEFAULT_PERSONA)
            self.cfg["seeded"] = True
            self.save_cfg()
        last = self.messages[-1] if self.messages else None
        rt = self.cfg.get("retry") or {}
        if last and last["role"] == "me" and self._age(last) < 6 * 3600 and not rt.get("at"):
            # 上次关程序时还没收到回复: 1 分钟后让 (最后操作的) 设备再试一次
            self.cfg["retry"] = {"at": self._ms() - RETRY_GAP * 1000 + 60000, "n": int(rt.get("n") or 0)}
        self.reload_schedule("启动")

    # ---- 存取 ----
    # 每个聊天对象 (人设) 的聊天记录分开存: comm_chat_<人设文件名的 crc>.json = 消息 + 清空的时刻 + 重试状态 + 主动消息的标记.
    # 现在聊的那一个放在 self.messages / self.cfg["clear_ts" / "retry" / "pro_key"] 里 (工作用), 其余的在 self._others 里 (按需读盘).
    def _ms(self) -> int:
        return int(self.now().timestamp() * 1000)

    def _chat_path(self, pid: str) -> str:
        return os.path.join(self.dir, f"comm_chat_{pid or 'none'}.json")

    @staticmethod
    def _norm_state(d) -> dict:
        d = d if isinstance(d, dict) else {}
        msgs = d.get("messages") if isinstance(d.get("messages"), list) else []
        msgs = [m for m in msgs if isinstance(m, dict) and m.get("role") in ("me", "ai", "sys")]
        for m in msgs:                                       # 旧版本的记录没有 uid / ts
            if not m.get("uid"):
                m["uid"] = f"L{m.get('id') or 0}"
            if not m.get("ts"):
                try:
                    m["ts"] = int(datetime.fromisoformat(m["at"]).timestamp() * 1000)
                except (KeyError, ValueError, TypeError):
                    m["ts"] = 0
        msgs.sort(key=lambda m: (m["ts"], m["uid"]))
        return {"messages": msgs, "clear_ts": int(d.get("clear_ts") or 0),
                "retry": dict(d["retry"]) if isinstance(d.get("retry"), dict) else {},
                "pro_key": str(d.get("pro_key") or "")}

    def _read_chat(self, pid: str) -> dict:
        d = config.load_json_file(self._chat_path(pid), None)
        if d is None and pid and not self.cfg.get("legacy_done") and os.path.exists(self.legacy_chat_path):
            # 以前所有对象共用一份 comm_chat.json: 归给现在选着的这个人设 (一次)
            legacy = config.load_json_file(self.legacy_chat_path, {})
            d = dict(legacy if isinstance(legacy, dict) else {}, clear_ts=self.cfg.get("clear_ts") or 0,
                     retry=self.cfg.get("retry") or {}, pro_key=self.cfg.get("pro_key") or "")
            self.cfg["legacy_done"] = True
            self._write_async(self._chat_path(pid), d)
            try:
                import securefile
                securefile.remove(self.legacy_chat_path)
            except Exception:       # noqa: BLE001
                pass
        return self._norm_state(d)

    def _load_state(self, st: dict):
        """把某个对象的聊天记录换成现在聊的."""
        self.messages = st["messages"]
        self.cfg["clear_ts"], self.cfg["retry"], self.cfg["pro_key"] = st["clear_ts"], st["retry"], st["pro_key"]
        self._next_id = max([int(m.get("id") or 0) for m in self.messages] + [0]) + 1
        for m in self.messages:
            if not m.get("id"):
                m["id"] = self._take_id()
        self._last_ts = max([int(m["ts"]) for m in self.messages] + [0])

    def _active_state(self) -> dict:
        return {"messages": self.messages, "clear_ts": int(self.cfg.get("clear_ts") or 0),
                "retry": dict(self.cfg.get("retry") or {}), "pro_key": str(self.cfg.get("pro_key") or "")}

    @staticmethod
    def _state_payload(st: dict) -> dict:
        return {"messages": [dict(m) for m in st["messages"][-KEEP:]], "clear_ts": int(st["clear_ts"]),
                "retry": dict(st["retry"]), "pro_key": st["pro_key"]}

    def _state_of(self, pid: str) -> dict:
        """某个对象的聊天状态: 现在聊的是活的 (改了要 _store), 其他的在缓存里 (没读过就读盘)."""
        if pid == self.pid:
            return self._active_state()
        st = self._others.get(pid)
        if st is None:
            st = self._others[pid] = self._read_chat(pid)
        return st

    def _store(self, pid: str, st: dict):
        if pid == self.pid:
            self.messages = st["messages"]
            self.cfg["clear_ts"], self.cfg["retry"], self.cfg["pro_key"] = st["clear_ts"], st["retry"], st["pro_key"]
            self.save_chat()
            self.save_cfg()
        else:
            self._write_async(self._chat_path(pid), self._state_payload(st))

    def chat_pids(self) -> list:
        """有聊天记录的对象 (现在聊的 + 磁盘上有的)."""
        pids = {self.pid} if self.pid else set()
        try:
            for n in os.listdir(self.dir):
                m = re.fullmatch(r"comm_chat_([0-9a-f]{8})\.json", n)
                if m:
                    pids.add(m.group(1))
        except OSError:
            pass
        return sorted(pids)

    def load(self):
        d = config.load_json_file(self.cfg_path, {})
        if isinstance(d, dict):
            for k, default in DEFAULTS.items():
                v = d.get(k)
                if isinstance(v, type(default)):
                    self.cfg[k] = v
        self.pid = pid_of(self.cfg.get("persona") or "")
        self._load_state(self._read_chat(self.pid))

    def set_persona(self, name: str):
        """换聊天对象: 现在这个的记录先存好, 换成那个对象自己的记录 (各聊各的). 换的时候还在等的回复不要了 (记录里还是没回的那条)."""
        name = str(name or "")
        new = pid_of(name)
        if new == self.pid:
            self.cfg["persona"] = name
            return
        self._others[self.pid] = self._active_state()           # 现在的记下来 (内存里的 messages 是同一个列表)
        self.save_chat()
        self.cfg["persona"] = name
        self.pid = new
        st = self._others.pop(new, None) or self._read_chat(new)
        self._load_state(st)
        self._epoch += 1
        self.error = ""
        self.unread = 0
        self.attach_touched = self.attach_touched
        self.reload_schedule("换人设")

    def _take_id(self) -> int:
        self._next_id += 1
        return self._next_id - 1

    # 写盘 (加密 + fsync) 放在后台线程: 发送 / 收到消息时界面线程不等磁盘, 不卡. 连续几次保存合并成一次 (同一个文件只留最新的).
    def _write_async(self, path: str, payload):
        with self._wlock:
            self._pending[path] = payload
            if self._writing:
                return
            self._writing = True
        threading.Thread(target=self._write_loop, daemon=True).start()

    def log(self, text: str):
        """请求日志 comm.log (数据文件夹, 明文): 什么时候发的, 多大, 花了多久, 出了什么错. 不记聊天内容和 Key; 超过 200 KB 只留后一半."""
        try:
            path = os.path.join(self.dir, "comm.log")
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")
            if os.path.getsize(path) > 200_000:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    data = f.read()
                with open(path, "w", encoding="utf-8") as f:
                    f.write(data[len(data) // 2:])
        except OSError:
            pass

    def waiting_text(self) -> str:
        """请求发出去很久还没有回应时的提示 (刚发出去的几秒不提示)."""
        if not self.busy or not self.busy_since:
            return ""
        sec = int(time.monotonic() - self.busy_since)
        return f"API 还没有回应 (已等待 {sec} 秒)" if sec >= WAIT_HINT else ""

    # ---- 模型 (聊天和 AI 名单建议用的是同一份模型设置) ----
    def model_options(self) -> list:
        return ai_assist.models_for(self.ai.profile())

    def current_model(self) -> str:
        return self.ai.profile().get("model") or ""

    def set_model(self, model: str):
        model = str(model or "").strip()
        if not model or model == self.current_model():
            return
        self.ai.set_profile(self.ai.provider, model=model)
        self.ai.save()
        self._changed()

    def _write_loop(self):
        while True:
            with self._wlock:
                pending, self._pending = self._pending, {}
                if not pending:
                    self._writing = False
                    return
            for path, payload in pending.items():
                config.save_json_file(path, payload)

    def flush(self, timeout: float = 5.0):
        """等后台的写盘做完 (关程序前, 以及自检)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._wlock:
                if not self._writing and not self._pending:
                    return
            time.sleep(0.01)

    def save_cfg(self):
        """设置存 comm_config.json; 现在聊的这个对象的清空时刻 / 重试 / 主动消息标记跟着它的聊天记录走, 不存在这里."""
        out = {k: v for k, v in self.cfg.items() if k not in CHAT_KEYS}
        self._write_async(self.cfg_path, json.loads(json.dumps(out)))

    def save_chat(self):
        if len(self.messages) > KEEP:                  # 一次砍 HISTORY_STEP 的整数倍: 发给模型的历史的起点 (api_messages) 不会跟着滑
            cut = -(-(len(self.messages) - KEEP) // HISTORY_STEP) * HISTORY_STEP
            self.messages = self.messages[cut:]
        self._write_async(self._chat_path(self.pid), self._state_payload(self._active_state()))

    def set_cfg(self, **kw):
        if "persona" in kw:
            self.set_persona(kw.pop("persona"))
            self.save_chat()
        self.cfg.update(kw)
        self.save_cfg()
        self._changed()
        if "split_send" in kw or "bg_pro" in kw or "mute_pro" in kw:
            self._push()

    def _changed(self):
        self.version += 1
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass

    def _push(self):
        """聊天记录有改动: 马上同步一次 (有绑定的设备时)."""
        if self.on_sync:
            try:
                self.on_sync()
            except Exception:
                pass

    def _age(self, m) -> float:
        try:
            return (self.now() - datetime.fromisoformat(m["at"])).total_seconds()
        except (KeyError, ValueError, TypeError):
            return 1e9

    # ---- 设备同步 ----
    def sync_doc(self) -> dict:
        """随设备同步的内容: 每个对象各自最近 SYNC_N 条消息 (键里带对象的编号 pid), 再早的是各设备自己留着的; 掉出窗口不算删除."""
        doc = {}
        if self.cfg.get("user_profile"):
            doc["profile"] = self.cfg["user_profile"]
        doc["split"] = self.split_on()                  # 分条发的开关: 调用 AI 的是主端, 两台设备要一致
        doc["bgpro"] = self.bg_on()
        for pid in self.chat_pids():
            doc[f"mute:{pid}"] = self.is_muted(pid)
        for pid in self.chat_pids():
            st = self._state_of(pid)
            for m in st["messages"][-SYNC_N:]:
                e = {"role": m["role"], "text": m["text"], "at": m["at"], "ts": int(m["ts"])}
                if m.get("api"):
                    e["api"] = m["api"]
                if m.get("wait"):
                    e["wait"] = m["wait"]
                doc[f"m:{pid}:{m['uid']}"] = e
            if st["pro_key"]:
                doc[f"pro:{pid}"] = st["pro_key"]
            if st["retry"]:
                doc[f"retry:{pid}"] = dict(st["retry"])
            if st["clear_ts"]:
                doc[f"clear:{pid}"] = int(st["clear_ts"])
        return doc

    def apply_doc(self, changes: dict):
        """别的设备发来的改动 (界面线程)."""
        touched = {}                                            # pid -> [状态, 消息有没有变]
        cleared_active = False
        fresh = []                                              # 这次新并进来的消息: (对象编号, 消息)

        def st_of(pid):
            if pid not in touched:
                touched[pid] = [self._state_of(pid), False, {m["uid"] for m in self._state_of(pid)["messages"]}]
            return touched[pid]

        for key in sorted(changes, key=lambda k: not k.startswith("clear:")):      # 清空先处理: 之前的记录不会再并进来
            val = changes[key]
            kind, _, rest = key.partition(":")
            if kind == "clear":
                pid = rest
                try:
                    t = int(val)
                except (TypeError, ValueError):
                    continue
                st, _d, _k = st_of(pid)
                if t > st["clear_ts"]:
                    st["clear_ts"] = t
                    st["messages"][:] = [m for m in st["messages"] if int(m["ts"]) > t]
                    st["retry"] = {}
                    touched[pid][1] = True
                    touched[pid][2] = {m["uid"] for m in st["messages"]}
                    if pid == self.pid:
                        cleared_active = True
            elif kind == "m" and isinstance(val, dict):
                pid, _, uid = rest.partition(":")
                try:
                    ts = int(val.get("ts") or 0)
                except (TypeError, ValueError):
                    continue
                st, _d, known = st_of(pid)
                if uid in known or ts <= st["clear_ts"] or val.get("role") not in ("me", "ai", "sys"):
                    continue
                m = {"id": self._take_id(), "uid": uid, "role": val["role"], "text": str(val.get("text") or ""),
                     "at": str(val.get("at") or ""), "ts": ts}
                if val.get("api"):
                    m["api"] = str(val["api"])
                if isinstance(val.get("wait"), (int, float)) and 0 < val["wait"] <= WAIT_MAX_MIN:
                    m["wait"] = float(val["wait"])
                if m["role"] == "ai" and self.blocked():       # 不能用沟通的时候同步来的也先藏起来
                    m["hold"] = True
                st["messages"].append(m)
                fresh.append((pid, m))
                known.add(uid)
                touched[pid][1] = True
                if m["role"] == "ai":
                    st["retry"] = {}
                    if not (self.viewing and pid == self.pid) and not m.get("hold"):
                        self.unread += 1
            elif key == "bgpro" and isinstance(val, bool):
                if val != self.bg_on():
                    self.cfg["bg_pro"] = val
                    self.save_cfg()
                    self._changed()
            elif kind == "mute" and isinstance(val, bool):
                if val != self.is_muted(rest):
                    cur = [p for p in (self.cfg.get("mute_pro") or []) if p != rest]
                    self.cfg["mute_pro"] = cur + [rest] if val else cur
                    self.save_cfg()
                    self._changed()
            elif key == "split" and isinstance(val, bool):
                if val != self.split_on():
                    self.cfg["split_send"] = val
                    self.save_cfg()
                    self._changed()
            elif key == "profile":
                new = str(val or "")[:MAX_PROFILE] if val is not None else ""
                if new != self.cfg.get("user_profile"):
                    self.cfg["user_profile"] = new
                    self.save_cfg()
                    self._changed()
            elif kind == "pro" and isinstance(val, str):             # 已经处理过的主动消息时间点: 两边合起来
                st = st_of(rest)[0]
                have = [k for k in st["pro_key"].split("|") if k]
                add = [k for k in val.split("|") if k and k not in have]
                if add:
                    st["pro_key"] = "|".join((have + add)[-PROACTIVE_KEEP:])
                    touched[rest][1] = True
            elif kind == "retry":
                st_of(rest)[0]["retry"] = dict(val) if isinstance(val, dict) else {}
                touched[rest][1] = True
        for pid, (st, dirty, _known) in touched.items():
            if not dirty:
                continue
            st["messages"].sort(key=lambda m: (m["ts"], m["uid"]))
            if pid == self.pid:
                self._last_ts = max(self._last_ts, max([int(m["ts"]) for m in st["messages"]] + [0]))
            self._store(pid, st)
        if cleared_active:
            self.unread = 0
            self.error = ""
            self._epoch += 1
            self.reload_schedule("新对话")
        if touched:
            self._changed()
        self._synced_ping([m for pid, m in fresh if pid == self.pid])

    def _synced_ping(self, fresh: list):
        """别的设备收到的主动消息同步过来了 (系统触发的那条 + 对方的回复, 刚刚才写的): 这台也提示一声 (前 5 分钟没聊过的话)."""
        new = {m["uid"] for m in fresh}
        now = self._ms()
        for m in fresh:
            if m["role"] != "ai" or now - int(m["ts"]) > 90 * 1000:
                continue
            i = next((j for j, x in enumerate(self.messages) if x["uid"] == m["uid"]), 0)
            prev = self.messages[i - 1] if i > 0 else None
            if prev and prev["role"] == "sys" and str(prev["text"]).startswith("[系统]") and prev["uid"] in new:
                if not self._talked_recently(self.messages[:i - 1], int(prev["ts"])):
                    self._ping()
                return

    def bg_on(self) -> bool:
        """允许非正在聊天的人 (聊过天的其他对象) 发起对话吗. 默认开; 随设备同步."""
        return self.cfg.get("bg_pro") is not False

    def is_muted(self, pid: str = None) -> bool:
        """这个对象 (默认是现在聊的) 被"禁止主动发起"了吗: 不主动发消息, 也不追问 (规则 IX)."""
        return (pid if pid is not None else self.pid) in (self.cfg.get("mute_pro") or [])

    def set_muted(self, muted: bool, pid: str = None):
        pid = pid if pid is not None else self.pid
        cur = [p for p in (self.cfg.get("mute_pro") or []) if p != pid]
        self.set_cfg(mute_pro=cur + [pid] if muted and pid else cur)

    def split_on(self) -> bool:
        """分条发 (规则 VIII) 开着吗: 用户说完话, 对方的第一条收到之后再调用一次 API, 让对方可以把话分开说. 默认开."""
        return self.cfg.get("split_send") is not False

    def pro_percent(self, kind: str) -> float:
        """主动发消息的概率 (%): kind = "idle" 人设的空闲段前 / "rest" 人设的休息段前. 写在人设里 (persona_probs), 没写用默认."""
        self._segments()
        return self._pro_cache.get(kind, float(PROACTIVE_P[kind]))

    @staticmethod
    def _talked_recently(msgs: list, before_ms: int) -> bool:
        """before_ms 之前 5 分钟内, 用户和对方有没有说过话 (系统消息不算)."""
        return any(m["role"] in ("me", "ai") and 0 <= before_ms - int(m["ts"]) <= PING_QUIET * 1000 for m in msgs)

    def _ping(self):
        """收到主动消息的提示音 (不能用沟通的时候, 例如已经开始专注了, 不响)."""
        if self.on_proactive and not self.blocked():
            try:
                self.on_proactive()
            except Exception:       # noqa: BLE001
                pass

    def last_author(self) -> str:
        """最晚的一条消息是哪台设备写的 (设备 id 的前 4 位; 旧记录 / 没有记录 = ""): 这台设备是聊天的主端 —— 调用 AI, 主动发, 重试都由它做,
        别的设备从它同步. 谁最后发了消息 (用户在那台上发的, 或者那台上 AI 回复的), 谁就是主端."""
        if not self.messages:
            return ""
        last = max(self.messages, key=lambda m: (int(m["ts"]), m["uid"]))
        return last["uid"].rpartition("-")[2] if "-" in last["uid"] else ""

    # ---- 状态 ----
    def persona_name(self) -> str:
        n = self.cfg.get("persona") or ""
        return n if n in list_personas(self.personas) else ""

    def problem(self) -> str:
        """现在不能聊天的原因 ("" = 可以)."""
        why = self.blocked()
        if why:
            return why
        try:
            ai_assist.check_profile(self.ai.profile())
        except ai_assist.AIError as e:
            return e.message
        if not self.persona_name():
            return "还没有选人设"
        return ""

    # ---- 头像: 圆形 PNG, 在 avatar.py / pages/avatar_dialog.py 里选图并截取 ----
    def avatar_dest(self, who: str) -> str:
        """who: "me" / "ai". 对方的头像跟着人设走 (换人设就换头像)."""
        if who == "me":
            name = "me.png"
        else:
            stem = persona_title(self.persona_name() or "")
            name = f"ai_{zlib.crc32(stem.encode('utf-8')):08x}.png"
        return os.path.join(self.dir, "avatars", name)

    def avatar_path(self, who: str) -> str:
        p = self.avatar_dest(who)
        return p if os.path.isfile(p) else ""

    def remove_avatar(self, who: str):
        try:
            os.remove(self.avatar_dest(who))
        except OSError:
            pass
        self._changed()

    def avatars_changed(self):
        self._changed()

    def visible(self) -> list:
        """聊天里显示的消息. 不能用沟通的时候 (专注 / 睡眠 / 外出 / 无言为真) 收到的对方的消息先藏起来 (hold), 回到休息 / 空闲再显示 (24.4)."""
        return [m for m in self.messages if m["role"] in ("me", "ai") and not m.get("hold")]

    def unanswered(self) -> bool:
        vis = [m for m in self.messages if m["role"] in ("me", "ai")]          # 藏起来的回复也算回复过了
        return bool(vis) and vis[-1]["role"] == "me"

    def _release_held(self):
        """回到休息 / 空闲: 藏着的消息现在显示 (未读 +N, 前 5 分钟没聊过的话响一声)."""
        if not any(m.get("hold") for m in self.messages) or self.blocked():
            return
        held = [m for m in self.messages if m.get("hold")]
        first = self.messages.index(held[0])
        for m in held:
            del m["hold"]
            m["shown"] = self._ms()                        # 等用户回复的时间从这时候算起 (规则 IX)
        if not self.viewing:
            self.unread += len(held)
        if not self._talked_recently(self.messages[:first], int(held[0]["ts"])):
            self._ping()
        self.save_chat()
        self._changed()
        self._push()

    def set_viewing(self, on: bool):
        self.viewing = bool(on)
        if on and self.unread:
            self.unread = 0
            self._changed()

    # ---- 发给模型的内容: 每条用户消息前只有时间; 整点记录 / 计时器等数据只有用户手动选了 📎 附件才发 ----
    def _compose(self, now: datetime, body: str) -> str:
        return f"[{stamp(now)}]\n" + body

    # ---- 发消息 ----
    def _add(self, role: str, text: str, api: str = "") -> dict:
        m = self._mk(role, text, api)
        self.messages.append(m)
        return m

    def _mk(self, role: str, text: str, api: str = "") -> dict:
        """造一条消息 (还没放进任何聊天记录)."""
        now = self.now()
        ts = max(self._ms(), self._last_ts + 1)          # 同一台设备上严格递增
        self._last_ts = ts
        m = {"id": self._take_id(), "uid": f"{ts}-{(self.device() or 'local')[:4]}", "role": role, "text": text,
             "at": now.isoformat(timespec="seconds"), "ts": ts}
        if api:
            m["api"] = api
        if role == "ai" and self.blocked():                # 专注 / 睡眠 / 外出中收到的: 先藏起来, 回到休息 / 空闲再显示
            m["hold"] = True
        return m

    def _sent(self):
        """用户发出一条之后的收尾."""
        self.error = ""
        self.cfg["retry"] = {}
        self.save_cfg()
        self.save_chat()
        self._changed()
        self._push()

    def send(self, text: str) -> str:
        """用户发一条. 返回 "" = 已发出, 否则是不能发的原因."""
        text = str(text or "").strip()
        if not text and not self.attach_label():
            return "empty"
        if text.lower() == "/clear":
            self.clear()
            return ""
        if self.attach_label():                         # 有确定过的附加数据: 和这条消息一起发
            why = self.send_data(self.attach, text)
            if not why:
                self.set_attach({})
            return why
        why = self.problem()
        if why:
            return why
        now = self.now()
        self._add("me", text, self._compose(now, text))
        self._sent()
        self._request("user")
        return ""

    def set_attach(self, sel: dict):
        """📎 窗口点"确定": 记下勾选的数据, 随下一条消息一起发 (发出去之后清掉)."""
        self.attach = {"today": list(sel.get("today", [])), "cal": list(sel.get("cal", [])),
                       "contract": bool(sel.get("contract")), "stats": list(sel.get("stats", []))}
        self.attach_touched = True
        self._changed()

    def attach_form(self) -> dict:
        """📎 窗口打开时的勾选: 确定过的; 还没确定过 = 默认勾 整点记录(今天) 和 专注计时(今天)."""
        if self.attach_touched or self.attach_label():
            return self.attach
        import comm_data
        return {"today": list(comm_data.DEFAULT_TODAY), "cal": [], "contract": False, "stats": []}

    def attach_label(self) -> str:
        import comm_data
        return comm_data.label(self.attach)

    def send_data(self, sel: dict, text: str = "") -> str:
        """发送统计 / 日历 / 合约词条的数据 (comm_data.py). 聊天里只显示一行说明, 数据附在发给模型的内容里.
        text: 输入框里已经写的字, 一起发. 返回 "" = 已发出, 否则是不能发的原因."""
        import comm_data
        label = comm_data.label(sel)
        if not label:
            return "empty"
        why = self.problem()
        if why:
            return why
        data = self.data_fn(sel)
        if not data:
            return "黑雾中无法发送" if self.fog() else "没有可发送的数据"
        now = self.now()
        text = str(text or "").strip()
        shown = label + (("\n" + text) if text else "")
        self._add("me", shown, self._compose(now, f"(用户发来的数据)\n{data}" + (f"\n\n{text}" if text else "")))
        self._sent()
        self._request("user")
        return ""

    def _trigger(self, kind: str, body: str):
        """系统要求对方主动发 / 重试: 这条系统消息先不进记录, 对方真的回复了才和回复一起记下 (被拒绝 / 出错就当没发过)."""
        api = self._compose(self.now(), body)
        self._request(kind, (body, api))

    def _request(self, kind: str, extra=None):
        if self.busy:
            self._again = True
            return
        profile = self.ai.profile()
        system = prefix_prompt(self.split_on()) + read_persona(self.personas, self.persona_name())
        who = str(self.cfg.get("user_profile") or "").strip()[:MAX_PROFILE]
        if who:                                           # 用户在 设置 → 沟通 里自己填的身份背景
            system += "\n\n以下是用户的身份背景(用户自己填写):\n" + who
        msgs = api_messages(self.messages)
        if extra:
            if msgs and msgs[-1]["role"] == "user":
                msgs[-1]["content"] += "\n\n" + extra[1]
            else:
                msgs.append({"role": "user", "content": extra[1]})
        epoch = self._epoch
        self.busy = True
        self.busy_since = time.monotonic()
        started = time.monotonic()
        self.log(f"REQ {kind} start model={profile.get('model')} host={urlsplit_host(profile.get('base_url'))} "
                 f"msgs={len(msgs)} chars={sum(len(m['content']) for m in msgs) + len(system)}")
        self._changed()

        def used(tokens, cached):
            self.log(f"USAGE {kind} input={tokens} cached={cached}")

        def ask(m):
            return self.chat_fn(profile, system, m, max_tokens=CHAT_TOKENS, temperature=CHAT_TEMPERATURE,
                                timeout=CHAT_TIMEOUT, cache=True, on_usage=used)

        def work():
            try:
                convo = list(msgs)
                reply = ask(convo)
                # 对方回 [refuse to reply][contract] = 要词条库: 把资料给它, 再调用一次让它回复用户 (资料不写进记录)
                for _ in range(MAX_INFO_ROUNDS):
                    tags = info_tags(reply)
                    blocks = []
                    for t in tags:
                        try:
                            b = str(self.info_fn(t) or "")
                        except Exception:       # noqa: BLE001
                            b = ""
                        if b:
                            blocks.append(f"【{t}】\n{b}")
                    if not blocks:
                        break
                    convo = convo + [{"role": "assistant", "content": reply},
                                   {"role": "user", "content": "(OIT 系统提供的资料, 用户看不到)\n\n" + "\n\n".join(blocks)
                                    + "\n\n以上是**用户自己的** OIT 数据和格式. 如果用户问的是你自己的使用情况, 请参照这个格式和写法, 根据人设编造你自己的数据来回答,"
                                      " 不要把用户的数据说成是你的; 然后回复用户上面的消息. 如果此刻仍然不方便回复, 返回" + REFUSE + "."}]
                    reply = ask(convo)
                res = (kind, extra, reply, "", epoch)
                self.log(f"REQ {kind} ok {time.monotonic() - started:.1f}s reply_chars={len(reply)}")
            except ai_assist.AIError as e:
                res = (kind, extra, "", e.message, epoch)
                self.log(f"REQ {kind} FAIL {time.monotonic() - started:.1f}s {e.kind}: {e.message}"
                         + (f" (系统代理 {ai_assist.proxy_hint()})" if e.kind == "network" else ""))
            except Exception as e:      # noqa: BLE001
                res = (kind, extra, "", f"{type(e).__name__}", epoch)
                self.log(f"REQ {kind} FAIL {time.monotonic() - started:.1f}s {type(e).__name__}: {e}")
            with self._lock:
                self._results.append(res)
        threading.Thread(target=work, daemon=True).start()

    def _finish(self, kind: str, extra, reply: str, err: str, epoch: int = 0):
        self.busy = False
        self.busy_since = 0.0
        if self.hard_block():                           # 等回复的时候"无言为真"生效了: 这条回复不要了
            self._again = False
            self._changed()
            return
        if epoch != self._epoch:                        # 发出之后清空过聊天记录: 这条回复不要了
            self._again = False
            self._changed()
            return
        text, wait_min = clean_reply(reply) if not err else ("", None)
        refused = not err and is_refusal(reply)
        empty = not err and not refused and not text           # 去掉 [] 之后什么也没剩: 当没回复
        # 第二次调用 (规则 VIII) 等回复的时候用户又说话了: 这条不要了 (用户的新消息重新回复)
        drop = kind == "followup" and not err and not refused and (self._again or self.unanswered())
        if drop:
            self.log("REPLY followup dropped: 等回复的时候用户又发了消息")
        if refused:
            self.log(f"REPLY refused ({kind}): 对方回了 [refuse to reply], 不显示" + (", 稍后重试" if self.unanswered() else ""))
        if err or refused or drop or empty:
            self.error = err
            if refused and self.unanswered():
                rt = self.cfg.get("retry") or {}
                n = int(rt.get("n") or 0) + 1
                self.cfg["retry"] = {"at": self._ms(), "n": n} if n <= RETRY_MAX else {"at": 0, "n": n}
                self.save_cfg()
        else:
            self.error = ""
            self.cfg["retry"] = {}
            self.save_cfg()
            ping = kind == "chase" or (kind == "proactive" and not self._talked_recently(self.messages, self._ms()))      # 前 5 分钟没聊过 (追问总是): 提示音
            if extra:
                self._add("sys", extra[0], extra[1])
            m = self._add("ai", text, reply.strip() if text != reply.strip() else "")
            if wait_min and kind != "chase":                    # 追问的回复里再写 [waiting for reply] 不算: 一条消息最多追问一次
                m["wait"] = wait_min
            held = m.get("hold")
            if not self.viewing and not held:
                self.unread += 1
            if ping and not held:
                self._ping()
        self.save_chat()
        self._changed()
        self._push()
        if self._again:
            self._again = False
            if self.unanswered() and not err:
                self._request("user")
        elif kind in FOLLOW_KINDS and self.split_on() and not err and not refused and not self.problem() and self.leader():
            # 规则 VIII: 对方的第一条已经收到了, 再调用一次, 让它可以接着说 (只想说一次就 [refuse to reply], 什么也不留下)
            self._trigger("followup", FOLLOW_BODY)

    # ---- 每秒一次 ----
    def tick(self):
        with self._lock:
            done, self._results = self._results, []
        for res in done:
            self._finish(*res)
        with self._lock:
            bg, self._bg_results = self._bg_results, []
        for res in bg:
            self._bg_finish(*res)
        self._release_held()
        if self.busy or self.problem() or not self.leader():
            return
        now = self.now()
        snap = self.snapshot() or {}
        # 对方不方便时: 隔一阵把没回的消息再交给模型
        rt = self.cfg.get("retry") or {}
        if rt.get("at") and self._ms() >= int(rt["at"]) + RETRY_GAP * 1000:
            self.cfg["retry"] = {"at": 0, "n": int(rt.get("n") or 0)}
            self.save_cfg()
            if self.unanswered():
                self._trigger("retry", f"现在是 {stamp(now)}. 用户之前发来的消息你还没有回复, 请现在一次性处理并回复; "
                                       f"如果仍然不方便, 返回{REFUSE}.")
                return
        if self._check_wait():
            return
        self._check_proactive(now, snap)
        self._bg_check(now, snap)

    def _check_wait(self, ctx=None) -> bool:
        """规则 IX: 对方最后一条消息末尾写了 [waiting for reply]:[N], N 分钟 (从用户看到它算起) 过去了用户还没回复: 让对方可以追问一次."""
        if ctx is None and self.is_muted():
            return False
        last = next((m for m in reversed(ctx["st"]["messages"] if ctx else self.messages) if m["role"] in ("me", "ai")), None)
        if not last or last["role"] != "ai" or not last.get("wait") or last.get("chased") or last.get("hold"):
            return False
        if self._ms() < int(last.get("shown") or last["ts"]) + float(last["wait"]) * 60000:
            return False
        last["chased"] = True                                # 一条消息最多追问一次 (追问被拒绝也算)
        n = f"{float(last['wait']):g}"
        body = (f"[系统] 你上一条消息末尾标了 [waiting for reply]:[{n}], 现在已经过了 {n} 分钟, 用户还没有回复. "
                f"请根据人设决定要不要追加询问一次 (话题和语气自己定, 不要重复上一条); 如果不想问或者此刻不方便, 返回{REFUSE}.")
        if ctx:
            self._store(ctx["pid"], ctx["st"])
            self._bg_request(ctx, "chase", body)
        else:
            self.save_chat()
            self._trigger("chase", body)
        return True

    # ---- 非正在聊天的对象主动发消息 (MECHANICS 24.4): 聊过天的其他对象照样按它们各自的人设日程主动发 / 追问, 一次一个 ----
    def _info_of(self, name: str) -> tuple:
        """人设文件里的 (日程段, 主动发起谈话的概率), 文件没变就用上次读的."""
        try:
            st = os.stat(os.path.join(self.personas, name))
        except OSError:
            return [], {}
        sig = (st.st_mtime_ns, st.st_size)
        hit = self._bg_seg.get(name)
        if not hit or hit[0] != sig:
            text = read_persona(self.personas, name)
            hit = self._bg_seg[name] = (sig, schedule_segments(text), persona_probs(text))
        return hit[1], hit[2]

    def _bg_check(self, now, snap: dict):
        if not self.bg_on() or self._bg_busy or self._ms() - self._bg_last < BG_STAGGER * 1000:
            return
        names = {pid_of(n): n for n in list_personas(self.personas)}
        for pid in self.chat_pids():
            if pid == self.pid or pid not in names or self.is_muted(pid):
                continue
            st = self._state_of(pid)
            if not any(m["role"] in ("me", "ai") for m in st["messages"]):
                continue                                          # 没聊过的 (只是切换时留下的空记录) 不会来找你
            segs, probs = self._info_of(names[pid])
            ctx = {"pid": pid, "name": names[pid], "st": st, "segs": segs, "probs": probs}
            if self._check_wait(ctx):
                return
            self._check_proactive(now, snap, ctx)
            if self._bg_busy:
                return

    def _bg_request(self, ctx, kind: str, body: str):
        """为非正在聊天的对象发一次请求 (主动发 / 追问); 回复由 tick 收进它的聊天记录, 没有第二次调用, 也没有要资料的轮次."""
        pid, st = ctx["pid"], ctx["st"]
        profile = self.ai.profile()
        system = prefix_prompt(self.split_on()) + read_persona(self.personas, ctx["name"])
        who = str(self.cfg.get("user_profile") or "").strip()[:MAX_PROFILE]
        if who:
            system += "\n\n以下是用户的身份背景(用户自己填写):\n" + who
        api = self._compose(self.now(), body)
        msgs = api_messages(st["messages"])
        if msgs and msgs[-1]["role"] == "user":
            msgs[-1]["content"] += "\n\n" + api
        else:
            msgs.append({"role": "user", "content": api})
        clear0 = int(st["clear_ts"])
        self._bg_busy = pid
        self._bg_last = self._ms()
        started = time.monotonic()
        self.log(f"REQ bg-{kind} start [{ctx['name']}] model={profile.get('model')} msgs={len(msgs)}")

        def work():
            reply, err = "", ""
            try:
                reply = self.chat_fn(profile, system, msgs, max_tokens=CHAT_TOKENS, temperature=CHAT_TEMPERATURE,
                                     timeout=CHAT_TIMEOUT, cache=True,
                                     on_usage=lambda t, c: self.log(f"USAGE bg-{kind} input={t} cached={c}"))
            except ai_assist.AIError as e:
                err = e.message
            except Exception as e:      # noqa: BLE001
                err = type(e).__name__
            self.log(f"REQ bg-{kind} {'FAIL ' + err if err else 'ok'} {time.monotonic() - started:.1f}s")
            with self._lock:
                self._bg_results.append((pid, kind, (body, api), reply, err, clear0))
        threading.Thread(target=work, daemon=True).start()

    def _bg_finish(self, pid, kind, extra, reply, err, clear0):
        self._bg_busy = None
        if self.hard_block():
            return
        st = self._state_of(pid)
        if int(st["clear_ts"]) != clear0 or err:
            return
        text, wait_min = clean_reply(reply)
        if is_refusal(reply) or not text:
            self.log(f"REPLY bg-{kind}: 对方不想发 / 什么也没剩, 不显示")
            return
        sysm = self._mk("sys", extra[0], extra[1])
        m = self._mk("ai", text, reply.strip() if text != reply.strip() else "")
        if wait_min and kind != "chase":
            m["wait"] = wait_min
        st["messages"].extend([sysm, m])
        talked = self._talked_recently(st["messages"][:-2], int(sysm["ts"]))
        if not m.get("hold"):
            if not (self.viewing and pid == self.pid):
                self.unread += 1
            if kind == "chase" or not talked:
                self._ping()
        self._store(pid, st)
        self._changed()
        self._push()

    # ---- 主动发消息 (MECHANICS 24.4) ----
    # 下面这些带 ctx 的: ctx = None 是现在聊的对象; 否则是非正在聊天的对象 {"pid", "name", "st" (它的聊天状态), "segs", "probs"} (_bg_check)
    def _pro_done(self, ctx=None) -> list:
        """已经处理过的主动消息时间点 (发没发都算): 休息开始的时刻 (I) / "S开始时刻i|r" (日程里的空闲 / 休息段, II / III)."""
        src = ctx["st"]["pro_key"] if ctx else self.cfg.get("pro_key")
        return [k for k in str(src or "").split("|") if k]

    def _pro_mark(self, key: str, ctx=None):
        keys = [k for k in self._pro_done(ctx) if k != key] + [key]
        val = "|".join(keys[-PROACTIVE_KEEP:])
        if ctx:
            ctx["st"]["pro_key"] = val
        else:
            self.cfg["pro_key"] = val

    def _pro_recent(self, now, ctx=None) -> bool:
        """最近 PROACTIVE_GAP 内已经主动发过一条了吗 (相邻的日程段 / 刚好又在休息开始后, 不连着发)."""
        for m in reversed(ctx["st"]["messages"] if ctx else self.messages):
            if m["role"] == "sys" and str(m.get("text") or "").startswith("[系统]"):
                return self._age(m) < PROACTIVE_GAP
        return False

    def _segments(self) -> list:
        """现在这个人设日程里的空闲 / 休息段 (人设文件没变就用上次读的)."""
        name = self.persona_name()
        if not name:
            self._pro_cache = {}
            return []
        try:
            st = os.stat(os.path.join(self.personas, name))
        except OSError:
            self._pro_cache = {}
            return []
        sig = (name, st.st_mtime_ns, st.st_size)
        if sig != self._seg_sig:
            self._seg_sig = sig
            text = read_persona(self.personas, name)
            self._seg_cache = schedule_segments(text)
            self._pro_cache = persona_probs(text)
        return self._seg_cache

    def reload_schedule(self, why: str = ""):
        """重新读人设里的日程, 主动发消息的时刻按它调整: 打开 OIT / 开始新对话 (清空) / 换人设时调用; 人设文件改了也会在 _segments 里自己重读."""
        self._seg_sig = None
        segs = self._segments()
        self.log(f"SCHEDULE {why}: 空闲 {sum(k == 'idle' for k, _m in segs)} 段, 休息 {sum(k == 'rest' for k, _m in segs)} 段")

    def _schedule_events(self, now, segs=None) -> list:
        """日程里 3 分钟之内就要开始的空闲 / 休息段: [(时间点, "idle" | "rest", 开始的时刻)]. segs: 别的对象的日程段 (默认是现在聊的)."""
        out = []
        for kind, mins in (self._segments() if segs is None else segs):
            for d in (0, 1):                                  # 今天 / 明天 (23:59 看 0:00 开始的那段)
                start = datetime.combine(now.date() + timedelta(days=d), datetime.min.time()) + timedelta(minutes=mins)
                if now < start <= now + timedelta(seconds=PROACTIVE_LEAD[1]):
                    out.append((f"S{start:%Y-%m-%dT%H:%M}{kind[0]}", kind, start))
        return out

    def _check_proactive(self, now, snap: dict, ctx=None):
        if ctx is None and self.is_muted():
            return
        done = self._pro_done(ctx)
        # I. 用户这次休息超过 20 分钟: 休息开始后 1~3 分钟发一条
        start = snap.get("break_start")
        if snap.get("state") == "break" and start and snap.get("break_total_s", 0) > PROACTIVE_MIN_REST:
            key = start.isoformat(timespec="seconds")
            if key not in done:
                due = start + timedelta(seconds=random.Random(key).randint(*PROACTIVE_DELAY))
                if now >= due:
                    mins = snap["break_total_s"] / 60
                    self._pro_fire(key, due, now, 1.0, f"[系统] 用户刚开始休息, 这次计划休息约 {mins:.0f} 分钟. "
                                                       f"请根据人设主动给用户发一条消息, 话题自己找; 如果此刻不方便, 返回{REFUSE}.", ctx)
                    return
        # II / III. 人设日程里每段空闲 / 休息开始前 1~3 分钟, 有概率发一条
        for key, kind, begin in self._schedule_events(now, ctx["segs"] if ctx else None):
            if key in done:
                continue
            due = begin - timedelta(seconds=random.Random(key).randint(*PROACTIVE_LEAD))
            if now < due:
                continue
            mins = max(1, round((begin - now).total_seconds() / 60))
            pct = ctx["probs"].get(kind, float(PROACTIVE_P[kind])) if ctx else self.pro_percent(kind)
            self._pro_fire(key, due, now, pct / 100.0,
                           f"[系统] 按你的日程, 你的{'空闲' if kind == 'idle' else '休息'}时间将在 {begin:%H:%M} 开始 (约 {mins} 分钟后). "
                           f"这条是系统提前触发的: 即使你此刻还处于专注/不能回复的时段, 也允许你现在主动给用户发一条消息, 话题自己找 (不要透露日程); "
                           f"如果实在不方便, 返回{REFUSE}.", ctx)
            return

    def _pro_fire(self, key: str, due, now, p: float, body: str, ctx=None):
        """这个时间点处理一次: 记下来 (不管发没发), 没错过太久 / 摇中了概率 / 最近没发过, 才真的让对方发."""
        self._pro_mark(key, ctx)
        late = (now - due).total_seconds() > PROACTIVE_LATE          # 错过太久 (程序刚打开) 就算了
        hit = p >= 1.0 or random.Random(key + "#p").random() < p
        recent = self._pro_recent(now, ctx)
        who = f" [{ctx['name']}]" if ctx else ""
        if not late and hit and not recent:
            self.log(f"PRO{who} {key} p={p:.2f} → 让对方发")
            if ctx:
                self._bg_request(ctx, "proactive", body)
            else:
                self._trigger("proactive", body)
        else:
            self.log(f"PRO{who} {key} p={p:.2f} → 不发 ({'错过了' if late else '概率没中' if not hit else '刚发过'})")
        if ctx:
            self._store(ctx["pid"], ctx["st"])
        else:
            self.save_chat()
            self.save_cfg()
        self._push()

    def clear(self):
        """清空聊天记录 (界面上的按钮, 或者在聊天框里发 /clear): 以后不再显示, 也不再发给模型; 别的设备也一起清空."""
        self.cfg["clear_ts"] = max(self._ms(), self._last_ts)
        self.messages = []
        self.unread = 0
        self._epoch += 1
        self.error = ""
        self.cfg["retry"] = {}
        self.save_cfg()
        self.save_chat()
        self.reload_schedule("新对话")
        self._changed()
        self._push()


# ══════════════ 自检 ══════════════
def _selftest():
    import tempfile
    import time

    td = tempfile.mkdtemp()
    data, folder = os.path.join(td, "data"), os.path.join(td, "personas")
    os.makedirs(data)

    # 前置 prompt / README
    p = prefix_prompt()
    assert p.startswith("你需要回复来自自律软件OIT用户的信息") and "I." in p and "II." in p and "III." in p
    assert "IV.根据你的专注时间段" in p and "不超过5min" in p
    assert "VI.不要主动透露日程/专注状态." in p and "用户3min前以内还在你聊天,则和用户说明要进入专注并停止对话." in p and "50min" not in p
    assert REFUSE in p and "默认你的每轮专注时间总是100min" in p and p.rstrip().endswith("以下是用户设定的人设:")
    assert f"{config.REST_GAIN:g}" in p and "ln1.2" in p
    assert readme_text().startswith("如何编写人设prompt:") and p in readme_text()

    # 人设文件夹
    ensure_personas(folder)
    assert os.path.exists(os.path.join(folder, "README.md")) and list_personas(folder) == []
    src = os.path.join(td, "小夏.txt")
    open(src, "w", encoding="utf-8").write("你是小夏, 大学生.\n每天 8 点到 12 点上课.")
    assert import_persona(folder, src) == "小夏.txt" and import_persona(folder, src) == "小夏 (2).txt"
    assert import_persona(folder, os.path.join(td, "x.json")) == ""
    assert list_personas(folder) == ["小夏 (2).txt", "小夏.txt"] and persona_title("小夏.txt") == "小夏"
    assert persona_labels(["小夏 (2).txt", "小夏.txt"]) == [("小夏 (2).txt", "小夏 (2)"), ("小夏.txt", "小夏")]
    assert persona_labels(["a.md", "a.txt", "b.txt"]) == [("a.md", "a.md"), ("a.txt", "a.txt"), ("b.txt", "b")]
    assert "大学生" in read_persona(folder, "小夏.txt") and read_persona(folder, "../x") == ""

    # 合并 / 拒绝
    assert api_messages([{"role": "ai", "text": "a"}, {"role": "me", "text": "1"}, {"role": "me", "api": "2"},
                         {"role": "ai", "text": "b"}, {"role": "sys", "api": "s"}]) == [
        {"role": "user", "content": "1\n\n2"}, {"role": "assistant", "content": "b"},
        {"role": "user", "content": "s"}]
    assert is_refusal(" [Refuse  to reply] ") and not is_refusal("好的")

    clock = [datetime(2026, 11, 2, 9, 0, 0)]
    calls = []
    script = []
    follow, follow_calls = [], []          # 第二次调用 (规则 VIII) 单独记: 不占 script / calls, 没有安排的回 [refuse to reply]

    def fake(profile, system, messages, **kw):
        if FOLLOW_TAG in messages[-1]["content"]:
            follow_calls.append((system, [dict(m) for m in messages]))
            return follow.pop(0) if follow else REFUSE
        calls.append((system, [dict(m) for m in messages]))
        r = script.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def wait(c):
        for _ in range(200):
            time.sleep(0.01)
            c.tick()
            if not c.busy:
                return
        raise AssertionError("超时")

    c = Comm(data, folder, now=lambda: clock[0], chat_fn=fake)
    c.ai.set_profile("deepseek", api_key="k")
    c.ai.provider = "deepseek"
    snap = {"state": "break", "rest_left_min": 12.4, "focus_total_min": 200, "focus_cur_min": 0,
            "break_start": clock[0], "break_total_s": 1800}
    c.snapshot = lambda: snap
    assert c.persona_name() == "Claudia.txt", "第一次运行默认选自带的 Claudia"
    c.set_cfg(persona="")
    assert c.send("hi") == "还没有选人设"
    c.set_cfg(persona="小夏.txt")
    script.append("早!")
    assert c.send("早") == "" and c.busy
    wait(c)
    system, msgs = calls[-1]
    assert system.startswith(prefix_prompt()) and system.endswith("每天 8 点到 12 点上课.")
    assert msgs[0]["role"] == "user" and "2026-11-02 周一 09:00" in msgs[0]["content"]
    assert "OIT 附带信息" not in msgs[0]["content"] and "整点记录" not in msgs[0]["content"] and "计时器" not in msgs[0]["content"]
    assert msgs[0]["content"].endswith("早"), "默认只有时间, 没有任何附带的数据"
    assert [m["role"] for m in c.visible()] == ["me", "ai"] and c.unread == 1
    clock[0] += timedelta(minutes=31)
    script.append("嗯")
    c.send("在吗")
    wait(c)
    assert "OIT 附带信息" not in calls[-1][1][-1]["content"] and "计时器" not in calls[-1][1][-1]["content"]

    # 不方便: 不显示, 消息留着, 过 10 分钟一次性交给模型
    n_vis = len(c.visible())
    script.append(REFUSE)
    c.send("上课吗")
    wait(c)
    assert c.unanswered() and len(c.visible()) == n_vis + 1 and not any(REFUSE in m["text"] for m in c.messages)
    script.append("我下课了! 刚才说什么?")
    c.tick()
    assert not c.busy
    clock[0] += timedelta(minutes=11)
    c.tick()
    assert c.busy
    wait(c)
    assert not c.unanswered() and c.visible()[-1]["text"].startswith("我下课了")
    assert "还没有回复" in calls[-1][1][-1]["content"] and "上课吗" in calls[-1][1][-2]["content"] + calls[-1][1][-1]["content"]

    # 出错: 消息保留, 记下原因
    script.append(ai_assist.AIError("network", "连接失败"))
    c.send("喂")
    wait(c)
    assert c.error == "连接失败" and c.unanswered()

    # 主动发消息 I: 休息 > 20 分钟, 开始后 1~3 分钟
    os.makedirs(os.path.join(td, "d2"))
    c2 = Comm(os.path.join(td, "d2"), folder, now=lambda: clock[0], chat_fn=fake)
    c2.ai.set_profile("deepseek", api_key="k")
    c2.ai.provider = "deepseek"
    c2.set_cfg(persona="小夏.txt")
    pings2 = []
    c2.on_proactive = lambda: pings2.append(1)
    start = clock[0]
    snap2 = {"state": "break", "break_start": start, "break_total_s": 25 * 60, "rest_left_min": 25,
             "focus_total_min": 60, "focus_cur_min": 0}
    c2.snapshot = lambda: snap2
    c2.tick()
    assert not c2.busy
    clock[0] = start + timedelta(seconds=59)
    c2.tick()
    assert not c2.busy, "休息开始后 1 分钟之内不发"
    script.append("休息啦, 在干嘛?")

    def fire_after(c, begin, lo=60, hi=200):             # 从 begin + lo 秒起, 每 2 秒看一次, 返回开始请求的那一秒 (没有 = None)
        for s in range(lo, hi, 2):
            clock[0] = begin + timedelta(seconds=s)
            c.tick()
            if c.busy:
                return s
        return None

    s_fire = fire_after(c2, start)
    assert s_fire is not None and 60 <= s_fire <= 182, s_fire
    wait(c2)
    assert c2.visible()[-1]["role"] == "ai" and "主动给用户发一条消息" in calls[-1][1][-1]["content"]
    assert not c2.visible()[:-1] and c2.unread == 1
    assert pings2 == [1], "前 5 分钟没聊过: 响一声"
    c2.tick()
    assert not c2.busy, "同一次休息只主动发一次"
    # 休息不到 20 分钟不发
    start2 = clock[0] + timedelta(minutes=1)
    snap2.update(break_start=start2, break_total_s=15 * 60)
    clock[0] = start2 + timedelta(minutes=6)
    c2.tick()
    assert not c2.busy
    # 刚主动发过一条 (10 分钟内): 又一次长休息开始, 也不连着发
    start3 = clock[0]
    snap2.update(break_start=start3, break_total_s=30 * 60)
    assert fire_after(c2, start3, 60, 178) is None, "10 分钟内不连发"
    # 主动消息被拒绝: 什么也不留下
    clock[0] += timedelta(minutes=15)
    start4 = clock[0]
    snap2.update(break_start=start4, break_total_s=30 * 60)
    script.append(REFUSE)
    assert fire_after(c2, start4) is not None
    wait(c2)
    assert len(c2.visible()) == 1 and sum(m["role"] == "sys" for m in c2.messages) == 1 and pings2 == [1]

    # 重开程序: 记录和设置都在
    c.flush()
    c3 = Comm(data, folder, now=lambda: clock[0], chat_fn=fake)
    assert c3.cfg["persona"] == "小夏.txt" and len(c3.visible()) == len(c.visible())
    c3.clear()
    assert c.avatar_path("me") == "" and c.avatar_dest("me").endswith("me.png")
    ai_dest = c.avatar_dest("ai")
    os.makedirs(os.path.dirname(ai_dest), exist_ok=True)
    open(ai_dest, "wb").write(b"x")
    assert c.avatar_path("ai") == ai_dest
    c.remove_avatar("ai")
    assert c.avatar_path("ai") == ""
    c3.flush()
    assert c3.visible() == [] and Comm(data, folder, now=lambda: clock[0], chat_fn=fake).visible() == []

    # /clear: 之前的记录不再显示, 也不再发给模型; 清空前发出的请求回复到了也丢掉
    c.snapshot = lambda: snap
    c.clear()
    script.append("晚安")
    c.send("我先睡了")
    wait(c)
    assert len(c.visible()) == 2
    assert c.send(" /CLEAR ") == "" and c.visible() == [] and c.messages == []
    script.append("新的开始")
    c.send("你好")
    wait(c)
    msgs = calls[-1][1]
    assert len(msgs) == 1 and "我先睡了" not in msgs[0]["content"]
    script.append("迟到的回复")
    c.send("在吗")
    c.clear()
    wait(c)
    assert c.visible() == [] and not c.busy

    # 发数据: 聊天里一行说明, 数据附在发给模型的内容里
    c.clear()
    c.data_fn = lambda sel: "【统计·联网理由】\n  学习资料: 15 分钟"
    script.append("看到了")
    assert c.send_data({"stats": ["net"], "cal": [], "contract": False}, "帮我看看") == ""
    wait(c)
    vis = c.visible()
    assert vis[0]["text"] == "📎 统计(月数据)·联网理由\n帮我看看" and vis[1]["role"] == "ai"
    api = calls[-1][1][-1]["content"]
    assert "(用户发来的数据)" in api and "学习资料: 15 分钟" in api and api.endswith("帮我看看")
    assert c.send_data({"stats": [], "cal": [], "contract": False}) == "empty"
    c.data_fn = lambda sel: ""
    assert c.send_data({"stats": ["net"]}) == "没有可发送的数据"
    c.fog = lambda: True
    assert c.send_data({"stats": ["net"]}) == "黑雾中无法发送"
    # 📎 确定过的附加数据: 随下一条消息发出, 只写附加数据不写字也能发
    c.blocked = lambda: ""
    c.fog = lambda: False
    c.data_fn = lambda sel: "【日历·专注】\n  11-01: 3.0 h"
    c.set_attach({"cal": ["study"]})
    assert c.attach_form()["stats"] == [] and c.attach_label() == "📎 日历·专注"
    os.makedirs(os.path.join(td, "d3"))
    c2f = Comm(os.path.join(td, "d3"), folder, now=lambda: clock[0], chat_fn=fake)
    assert c2f.attach_form()["today"] == ["log", "timer"] and c2f.attach_form()["stats"] == [] and c2f.attach_label() == ""
    c2f.set_attach({"today": ["log"], "stats": ["net"]})
    assert c2f.attach_label() == "📎 整点记录(今天) / 统计(月数据)·联网理由" and c2f.attach_form()["today"] == ["log"]
    script.append("嗯嗯")
    assert c.send("") == ""
    wait(c)
    assert c.attach_label() == "" and c.visible()[-2]["text"] == "📎 日历·专注"
    assert c.send("") == "empty"
    # 只有休息 / 空闲时能用: 专注 / 睡眠 / 外出中不能发, 不会主动发, 不会重试; 已经发出去的请求回复到了照样收下, 空下来再重试
    script.append("在专注前收到")
    c.send("休息时发的")
    c.blocked = lambda: "专注中不能使用沟通"
    assert c.send("专注时发") == "专注中不能使用沟通" and c.send_data({"stats": ["net"]}) == "专注中不能使用沟通"
    wait(c)
    assert c.visible()[-1]["text"] != "在专注前收到" and any(m["text"] == "在专注前收到" and m.get("hold") for m in c.messages), \
        "进入专注前发出的请求, 回复到了收下, 但藏起来 (回到休息 / 空闲再显示)"
    assert not c.unanswered(), "藏着的回复也算回复过了, 不会重新问"
    unread0 = c.unread
    n_calls = len(calls)
    c.cfg["retry"] = {"at": c._ms() - RETRY_GAP * 1000 - 5, "n": 1}
    c._add("me", "专注前最后一句")
    c.tick()
    assert not c.busy and len(calls) == n_calls, "专注中不会重试"
    c.blocked = lambda: ""
    script.append("空下来了")
    c.tick()
    assert any(m["text"] == "在专注前收到" for m in c.visible()) and c.unread == unread0 + 1, "回到休息 / 空闲: 藏着的消息显示, 未读 +1"
    assert not any(m.get("hold") for m in c.messages)
    assert c.busy, "回到休息 / 空闲: 马上把没回的消息交给对方"
    wait(c)
    assert c.visible()[-1]["text"] == "空下来了"
    # 无言为真: 不能发, 不会主动发, 也不会重试
    c.blocked = lambda: "无言为真生效中, 无法使用沟通"
    n = len(c.messages)
    assert c.send("hi") == "无言为真生效中, 无法使用沟通" and c.send_data({"stats": ["net"]}) == "无言为真生效中, 无法使用沟通"
    c.tick()
    assert len(c.messages) == n and not c.busy
    c.blocked = lambda: ""
    c.flush()
    # 自带的人设: 第一次运行放进文件夹并默认选中; 删掉之后不再写回
    fresh_dir, fresh_personas = os.path.join(td, "fresh"), os.path.join(td, "fresh_personas")
    os.makedirs(fresh_dir)
    cf = Comm(fresh_dir, fresh_personas, now=lambda: clock[0], chat_fn=fake)
    assert "Claudia.txt" in list_personas(fresh_personas) and cf.persona_name() == "Claudia.txt"
    assert read_persona(fresh_personas, "Claudia.txt").startswith("你将扮演一位文静,冷淡的")
    cf.set_cfg(persona="")
    os.remove(os.path.join(fresh_personas, "Claudia.txt"))
    cf.flush()
    cf2 = Comm(fresh_dir, fresh_personas, now=lambda: clock[0], chat_fn=fake)
    assert list_personas(fresh_personas) == [] and cf2.persona_name() == "", "删掉之后不再写回, 也不再自动选"
    # 请求日志 / 选模型 / 等待提示
    logp = os.path.join(data, "comm.log")
    assert os.path.exists(logp) and "REQ user start" in open(logp, encoding="utf-8").read()
    assert "REQ user ok" in open(logp, encoding="utf-8").read() and "k1" not in open(logp, encoding="utf-8").read()
    c.set_model("m-x")
    assert c.current_model() == "m-x" and "m-x" in c.model_options()
    assert ai_assist.AIConfig(c.ai.path).profile()["model"] == "m-x", "选的模型存进了模型设置"
    c.busy, c.busy_since = True, time.monotonic() - 20
    assert c.waiting_text().startswith("API 还没有回应") and c.waiting_text().endswith("秒)")
    c.busy, c.busy_since = False, 0.0
    assert c.waiting_text() == ""
    # ---- 聊天对象: 每个对象各自的聊天记录, 切换, 旧记录迁移 ----
    dm_dir = os.path.join(td, "multi")
    os.makedirs(dm_dir)
    open(os.path.join(folder, "夜.txt"), "w", encoding="utf-8").write("你是夜")
    mc = Comm(dm_dir, folder, now=lambda: clock[0], chat_fn=fake)
    mc.ai.set_profile("deepseek", api_key="k")
    mc.ai.provider = "deepseek"
    mc.set_cfg(persona="小夏.txt")
    script.append("小夏: 在")
    mc.send("你好小夏")
    wait(mc)
    mc.set_cfg(persona="夜.txt")
    assert mc.visible() == [] and mc.pid == pid_of("夜.txt"), "换了对象: 这个对象还没有聊过"
    script.append("夜: 嗯")
    mc.send("你好夜")
    wait(mc)
    assert [m["text"] for m in mc.visible()] == ["你好夜", "夜: 嗯"]
    assert all("你好小夏" not in (m.get("api") or "") for m in calls[-1][1]), "发给夜的不带小夏那边的记录"
    assert calls[-1][0].endswith("你是夜")
    mc.set_cfg(persona="小夏.txt")
    assert [m["text"] for m in mc.visible()] == ["你好小夏", "小夏: 在"], "换回来: 各聊各的"
    mc.clear()
    mc.set_cfg(persona="夜.txt")
    assert len(mc.visible()) == 2, "清空只清现在这个对象"
    mc.flush()
    mc2 = Comm(dm_dir, folder, now=lambda: clock[0], chat_fn=fake)
    assert mc2.persona_name() == "夜.txt" and len(mc2.visible()) == 2
    mc2.set_cfg(persona="小夏.txt")
    assert mc2.visible() == []
    files = sorted(f for f in os.listdir(dm_dir) if f.startswith("comm_chat_") and f.endswith(".json"))
    assert f"comm_chat_{pid_of('小夏.txt')}.json" in files and f"comm_chat_{pid_of('夜.txt')}.json" in files, files
    # 旧版本: 所有对象共用一份 comm_chat.json → 归给现在选着的对象
    leg = os.path.join(td, "legacy")
    os.makedirs(leg)
    config.save_json_file(os.path.join(leg, "comm_chat.json"), {"messages": [
        {"id": 1, "role": "me", "text": "以前的话", "at": "2026-11-02T08:00:00"}]})
    config.save_json_file(os.path.join(leg, "comm_config.json"), {"persona": "小夏.txt", "seeded": True})
    lc = Comm(leg, folder, now=lambda: clock[0], chat_fn=fake)
    assert [m["text"] for m in lc.visible()] == ["以前的话"] and lc.cfg["legacy_done"]
    lc.set_cfg(persona="夜.txt")
    assert lc.visible() == []
    # 身份背景: 用户自己填, 随 system 一起发, 同步
    mc.set_cfg(persona="小夏.txt", user_profile="我是大二的学生, 在准备考研.")
    script.append("好")
    mc.send("嗨")
    wait(mc)
    assert calls[-1][0].endswith("以下是用户的身份背景(用户自己填写):\n我是大二的学生, 在准备考研.")
    other = Comm(os.path.join(td, "prof"), folder, now=lambda: clock[0], chat_fn=fake) if os.makedirs(os.path.join(td, "prof")) is None else None
    other.apply_doc(mc.sync_doc())
    assert other.cfg["user_profile"] == "我是大二的学生, 在准备考研."
    mc.flush(); mc2.flush(); lc.flush(); other.flush()
    # ---- 对方要资料: [refuse to reply][contract] → 给词条库再调用一次 ----
    assert info_tags(" [Refuse to reply] [CONTRACT] ") == ["contract"] and info_tags("[contract]") == []
    assert info_tags("[refuse to reply][mechanics.md]") == [] and info_tags("好的") == []
    assert info_tags("[refuse to reply][calendar][stats]") == ["calendar", "stats"] and info_tags("[refuse to reply][x]") == []
    import comm_data
    assert all(f"[{t}]" in prefix_prompt() for t in comm_data.MODULE_TAGS) and "VII.如果用户问起OIT的使用数据" in prefix_prompt()
    p2 = prefix_prompt()
    assert "V.你需要了解OIT的独占功能'合约'" in p2 and "[refuse to reply][contract]" in p2 and "[refuse to reply][mechanics.md]" not in p2
    assert "如果需要更细致的信息 前往https://github.com/Obliphur/Obliphur-s-Integrated-Terminal下的MECHANICS.md查看." in p2
    os.makedirs(os.path.join(td, "ci"))
    ci = Comm(os.path.join(td, "ci"), folder, now=lambda: clock[0], chat_fn=fake)
    ci.ai.set_profile("deepseek", api_key="k")
    ci.ai.provider = "deepseek"
    ci.set_cfg(persona="小夏.txt")
    ci.info_fn = lambda tag: {"contract": "词条库: 持久战"}.get(tag, "")
    n_calls = len(calls)
    script.extend(["[refuse to reply][contract]", "合约里有持久战."])
    ci.send("合约是什么")
    wait(ci)
    assert len(calls) == n_calls + 2, "要了一次资料, 共调用两次"
    assert "词条库: 持久战" in calls[-1][1][-1]["content"]
    assert calls[-1][1][-2]["content"] == "[refuse to reply][contract]" and calls[-1][1][-3]["role"] == "user"
    assert [m["text"] for m in ci.visible()] == ["合约是什么", "合约里有持久战."]
    assert not any("词条库" in (m.get("api") or "") for m in ci.messages), "资料不写进聊天记录"
    # 要不到资料 / 要了还是拒绝: 当作不方便
    ci.info_fn = lambda tag: ""
    script.append("[refuse to reply][contract]")
    ci.send("再问一次")
    wait(ci)
    assert ci.unanswered() and ci.visible()[-1]["text"] == "再问一次"
    ci.flush()
    # ---- 设备同步: 记录合并, 只有 leader 调用 AI / 重试, 清空一起清 ----
    dA, dB = os.path.join(td, "sa"), os.path.join(td, "sb")
    os.makedirs(dA)
    os.makedirs(dB)
    A = Comm(dA, folder, now=lambda: clock[0], chat_fn=fake)
    B = Comm(dB, folder, now=lambda: clock[0], chat_fn=fake)
    for X, dev in ((A, "aaaa"), (B, "bbbb")):
        X.ai.set_profile("deepseek", api_key="k")
        X.ai.provider = "deepseek"
        X.set_cfg(persona="小夏.txt")
        X.device = lambda d=dev: d

    def pull(src, dst):                         # 模拟 devsync: 把对方的文档整份并进来
        dst.apply_doc(src.sync_doc())

    script.append("A 的回复")
    assert A.send("你好") == ""
    wait(A)
    pull(A, B)
    assert [m["text"] for m in B.visible()] == ["你好", "A 的回复"] and B.unread == 1
    pull(A, B)
    assert len(B.visible()) == 2, "同一条不会并两次"
    assert B.messages[0]["id"] != 0 and B.visible()[0]["uid"] == A.visible()[0]["uid"]
    # B 上的下一条: 在 B 上调用 AI, 回复同步回 A
    script.append("B 的回复")
    B.send("在吗")
    wait(B)
    pull(B, A)
    assert [m["text"] for m in A.visible()][-2:] == ["在吗", "B 的回复"] and len(A.visible()) == 4
    # 主端 = 最晚的一条消息的作者
    assert A.last_author() == "bbbb" and B.last_author() == "bbbb"
    # 对方不方便: 重试状态同步; leader 换成 B 之后由 B 重试, 回复同步给 A
    script.append(REFUSE)
    A.send("上课吗")
    wait(A)
    pull(A, B)
    assert B.unanswered() and B.cfg["retry"]["at"] and B.cfg["retry"]["n"] == 1
    assert A.last_author() == "aaaa" and B.last_author() == "aaaa"      # A 发了最晚的一条: A 是主端
    clock[0] += timedelta(minutes=11)
    A.leader = lambda: False
    B.leader = lambda: False
    B.tick()
    assert not B.busy, "不是 leader 就不重试"
    B.leader = lambda: True
    script.append("来了, 刚下课")
    B.tick()
    assert B.busy
    wait(B)
    pull(B, A)
    assert not A.unanswered() and A.visible()[-1]["text"] == "来了, 刚下课" and not A.cfg["retry"]
    assert any(m["role"] == "sys" for m in B.messages) and any(m["role"] == "sys" for m in A.messages)
    # 清空一起清, 清空之前的记录同步回来也不会复活
    pull(B, A)
    B.clear()
    pull(B, A)
    assert A.visible() == [] and B.visible() == []
    pull(A, B)
    assert B.visible() == []
    # 主动消息: 同一次休息只有一台发一次 (标记同步)
    start = clock[0]
    A.snapshot = B.snapshot = lambda: {"state": "break", "break_start": start, "break_total_s": 30 * 60,
                                       "rest_left_min": 25, "focus_total_min": 5}
    A.leader, B.leader = (lambda: True), (lambda: False)
    pA, pB = [], []
    A.on_proactive = lambda: pA.append(1)
    B.on_proactive = lambda: pB.append(1)
    clock[0] = start + timedelta(minutes=3)
    script.append("休息啦")
    A.tick()
    assert A.busy
    wait(A)
    assert pA == [1]
    pull(A, B)
    assert pB == [1], "同步过来的主动消息: 这台也响一声"
    pull(A, B)
    assert pB == [1] and B.cfg["pro_key"] == A.cfg["pro_key"]
    B.leader = lambda: True
    B.tick()
    assert not B.busy and [m["text"] for m in B.visible()] == ["休息啦"]
    A.flush()
    B.flush()

    # ---- II / III: 人设日程里每段空闲 / 休息开始前 1~3 分钟, 按概率发 ----
    assert schedule_segments(BUILTIN_PERSONAS["Claudia.txt"]) == [
        ("rest", 520), ("rest", 658), ("idle", 750), ("rest", 880), ("rest", 1018),
        ("idle", 1110), ("rest", 1240), ("rest", 1378), ("idle", 1380)]
    assert schedule_segments("- 9:05 ~ 9:35 : Idle time\n  09：10-10:00：休息\n24:00-25:00:空闲\n12:00-13:00:不休息\n"
                             "12:00-13:00:午饭\n每天 8 点到 12 点上课.\n9:05-9:35:idle again\n") == [("idle", 545), ("rest", 550)]
    open(os.path.join(folder, "日程.txt"), "w", encoding="utf-8").write("你是小满.\n12:30-13:00:空闲\n20:40-21:18:休息\n")
    os.makedirs(os.path.join(td, "sched"))
    c5 = Comm(os.path.join(td, "sched"), folder, now=lambda: clock[0], chat_fn=fake)
    c5.ai.set_profile("deepseek", api_key="k")
    c5.ai.provider = "deepseek"
    c5.set_cfg(persona="日程.txt")
    assert (c5.pro_percent("idle"), c5.pro_percent("rest")) == (80, 60), "人设里没写: 默认概率"
    assert persona_probs(BUILTIN_PERSONAS["Claudia.txt"]) == {"idle": 50.0, "rest": 40.0}, "Claudia: 50% / 40%"
    assert persona_probs("空闲发起谈话概率:12.5%\n休息发起谈话概率 ： 0.4\n休息发起谈话概率:90%") == {"idle": 12.5, "rest": 40.0}
    assert persona_probs("空闲发起谈话概率:1\n休息发起谈话概率:30") == {"idle": 100.0, "rest": 30.0}, "不带 % 的: 小数 (<=1) 或百分数"
    assert persona_probs("空闲发起谈话概率:150%\n休息发起谈话概率:x") == {"idle": 100.0} and persona_probs("") == {}
    p5 = os.path.join(folder, "日程.txt")

    def set_probs(c, idle=None, rest=None):                # 改人设文件里的概率行 (别的内容不动)
        keep = [ln for ln in open(p5, encoding="utf-8").read().split("\n") if "发起谈话概率" not in ln]
        extra = ([f"空闲发起谈话概率:{idle}"] if idle is not None else []) + ([f"休息发起谈话概率:{rest}"] if rest is not None else [])
        open(p5, "w", encoding="utf-8").write("\n".join(keep + extra) + "\n")
        c.reload_schedule()
    set_probs(c5, "150%", "x")
    assert (c5.pro_percent("idle"), c5.pro_percent("rest")) == (100, 60)
    c5.snapshot = lambda: {"state": "idle"}
    pings5 = []
    c5.on_proactive = lambda: pings5.append(1)

    def fire_before(c, begin):                             # 日程段开始前 200 秒起, 每 2 秒看一次, 返回开始请求时离开始还有几秒 (负数)
        for s in range(-200, 0, 2):
            clock[0] = begin + timedelta(seconds=s)
            c.tick()
            if c.busy:
                return s
        return None

    script.append("来了")
    s1 = fire_before(c5, datetime(2026, 12, 1, 12, 30))
    assert s1 is not None and -182 <= s1 <= -60, s1
    wait(c5)
    body = calls[-1][1][-1]["content"]
    assert c5.visible()[-1]["text"] == "来了" and "空闲时间将在 12:30 开始" in body and "主动给用户发一条消息" in body
    assert pings5 == [1]
    # 前 5 分钟聊过 → 不响
    clock[0] = datetime(2026, 12, 2, 12, 25)
    script.append("嗯")
    assert c5.send("在吗") == ""
    wait(c5)
    script.append("有空吗")
    assert fire_before(c5, datetime(2026, 12, 2, 12, 30)) is not None
    wait(c5)
    assert c5.visible()[-1]["text"] == "有空吗" and pings5 == [1], "刚聊过, 不响"
    # 概率 0: 不发, 但这个时间点算处理过了
    set_probs(c5, "0%", "60%")
    assert fire_before(c5, datetime(2026, 12, 3, 12, 30)) is None and any(k.startswith("S2026-12-03T12:30") for k in c5._pro_done())
    # 默认 60% 的休息段: 300 天里大约 60% 发
    fired = []
    c5._trigger = lambda kind, text: fired.append(text)
    set_probs(c5, "0%", "0.6")
    for d in range(300):
        clock[0] = datetime(2027, 1, 1, 20, 39, 0) + timedelta(days=d)
        c5.tick()
    assert 150 <= len(fired) <= 210 and "休息时间将在 20:40 开始" in fired[0], len(fired)
    # 错过太久 (程序刚打开) 不补发
    set_probs(c5, "100%", "0.6")
    fired.clear()
    for d in range(1, 60):
        b = datetime(2027, 3, 1, 12, 30) + timedelta(days=d)
        if random.Random(f"S{b:%Y-%m-%dT%H:%M}i").randint(*PROACTIVE_LEAD) > 150:
            break
    clock[0] = b - timedelta(seconds=10)
    c5.tick()
    assert not fired and any(k.startswith(f"S{b:%Y-%m-%dT%H:%M}") for k in c5._pro_done())
    c5.tick()
    assert not fired
    # 相邻的日程段 (22:58 休息, 23:00 空闲) 不连着发
    os.makedirs(os.path.join(td, "adj"))
    c6 = Comm(os.path.join(td, "adj"), folder, now=lambda: clock[0], chat_fn=fake)
    c6.ai.set_profile("deepseek", api_key="k")
    c6.ai.provider = "deepseek"
    open(os.path.join(folder, "全发.txt"), "w", encoding="utf-8").write(
        BUILTIN_PERSONAS["Claudia.txt"].replace("空闲发起谈话概率:50%", "空闲发起谈话概率:100%").replace("休息发起谈话概率:40%", "休息发起谈话概率:100%"))
    c6.set_cfg(persona="全发.txt")
    c6.snapshot = lambda: {"state": "idle"}
    script.append("晚点见")
    assert fire_before(c6, datetime(2027, 4, 1, 22, 58)) is not None
    wait(c6)
    assert "休息时间将在 22:58 开始" in calls[-1][1][-1]["content"]
    assert fire_before(c6, datetime(2027, 4, 1, 23, 0)) is None, "刚发过, 23:00 的空闲段不再发"
    assert any(k.startswith("S2027-04-01T23:00") for k in c6._pro_done())
    # ---- 缓存: 请求的开头要稳定 (api_messages 的起点一格一格挪), 带用量记录 ----
    big = [{"role": "me" if i % 2 == 0 else "ai", "text": f"m{i}"} for i in range(200)]
    firsts = {api_messages(big[:n])[0]["content"] for n in range(61, 140)}
    assert all(len(api_messages(big[:n])) <= HISTORY_SENT for n in range(1, 200))
    assert len(firsts) <= (140 - 61) // HISTORY_STEP + 2, "开头 (最老的一条) 每 %d 条才变一次" % HISTORY_STEP
    a_old, a_new = api_messages(big[:95]), api_messages(big[:96])
    assert a_old[0]["content"] == a_new[0]["content"], "新增一条消息不改变请求的开头"
    assert ai_assist.usage_of({"usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "cache_creation_input_tokens": 5}}) == (105, 90)
    assert ai_assist.usage_of({"usage": {"prompt_tokens": 200, "prompt_cache_hit_tokens": 150}}) == (200, 150)
    assert ai_assist.usage_of({"usage": {"prompt_tokens": 200, "prompt_tokens_details": {"cached_tokens": 128}}}) == (200, 128)
    assert ai_assist.usage_of({}) == (0, 0) and ai_assist.usage_of({"usage": "x"}) == (0, 0)

    # ---- VIII: 用户说完话, 对方的第一条收到之后再调用一次, 可以分开说 ----
    pv = prefix_prompt()
    assert "VIII.你可以把你的话分开说.当用户说完话时,系统会试图调用两次API,最好不要让你的表达太集中.如果你只想说一次,第二次[refuse to reply]." in pv
    assert pv.index("VII.如果用户问起") < pv.index("VIII.") < pv.index("以下是用户设定的人设:")
    os.makedirs(os.path.join(td, "fu"))
    cu = Comm(os.path.join(td, "fu"), folder, now=lambda: clock[0], chat_fn=fake)
    cu.ai.set_profile("deepseek", api_key="k")
    cu.ai.provider = "deepseek"
    cu.set_cfg(persona="小夏.txt")
    cu.snapshot = lambda: {"state": "idle"}
    n0 = len(follow_calls)
    script.append("第一句")
    follow.append("第二句")
    cu.send("在吗")
    wait(cu)
    assert [m["text"] for m in cu.visible()] == ["在吗", "第一句", "第二句"]
    assert len(follow_calls) == n0 + 1 and FOLLOW_TAG in follow_calls[-1][1][-1]["content"]
    assert follow_calls[-1][1][-2]["content"] == "第一句", "第二次请求里带着第一条"
    n_f = len(follow_calls)
    script.append("只有一句")                                  # 只想说一次: 第二次 [refuse to reply], 什么也不留下
    cu.send("好的")
    wait(cu)
    assert cu.visible()[-1]["text"] == "只有一句" and len(follow_calls) == n_f + 1
    assert sum(m["role"] == "sys" for m in cu.messages) == 1
    script.append(REFUSE)                                      # 第一次就被拒绝 (对方不方便): 不调用第二次
    cu.send("在不在")
    wait(cu)
    assert len(follow_calls) == n_f + 1 and cu.unanswered()
    cu.clear()                                                 # 第二次调用还在等的时候用户又说话了: 第二次的回复不要了, 按新消息重新回复
    script.extend(["甲", "回应等等"])
    follow.append("乙(不该出现)")
    cu.send("你好")
    for _ in range(300):
        time.sleep(0.01)
        cu.tick()
        if cu.busy and any(m["role"] == "ai" for m in cu.messages):
            break
    cu.send("等等")
    wait(cu)
    assert [m["text"] for m in cu.visible()] == ["你好", "甲", "等等", "回应等等"], [m["text"] for m in cu.visible()]
    # 分条发的开关: 关着时不调用第二次, 前置 prompt 里也没有第 VIII 条; 开关随设备同步
    cu.set_cfg(split_send=False)
    assert "VIII." not in prefix_prompt(False) and "VIII." in prefix_prompt(True) and not cu.split_on()
    n_f = len(follow_calls)
    script.append("单条")
    follow.append("不该出现")
    cu.send("关了吗")
    wait(cu)
    assert cu.visible()[-1]["text"] == "单条" and len(follow_calls) == n_f and "VIII." not in calls[-1][0]
    assert cu.sync_doc()["split"] is False
    follow.clear()
    cu.apply_doc({"split": True})
    assert cu.split_on() and cu.sync_doc()["split"] is True
    # IX: [waiting for reply]:[N] — 所有 [] 都不显示; N 分钟内用户没回复, 对方可以追问一次
    assert "IX.如果期待对方回复,则在对话结尾加一个[waiting for reply]:[N],N自己填写,如果N min内没有收到对方回复你可以追加询问." in prefix_prompt()
    assert prefix_prompt().index("VIII.") < prefix_prompt().index("IX.") < prefix_prompt().index("以下是用户设定的人设:")
    assert clean_reply("在吗?[waiting for reply]:[10]") == ("在吗?", 10.0)
    assert clean_reply("好的[微笑] 嗯\n\n\n\n[动作]好") == ("好的 嗯\n\n好", None)
    assert clean_reply("[Waiting For Reply]：[ 0.5 ]") == ("", 0.5) and clean_reply("x [waiting for reply]:[0]") == ("x", None)
    assert clean_reply("a［全角］b") == ("ab", None)
    cu.snapshot = lambda: {"state": "idle"}
    script.append("今天过得怎么样?[waiting for reply]:[10]")
    cu.send("嗨")
    wait(cu)
    last_ai = [m for m in cu.messages if m["role"] == "ai"][-1]
    assert last_ai["text"] == "今天过得怎么样?" and last_ai["wait"] == 10 and "[waiting for reply]" in last_ai["api"]
    assert cu.visible()[-1]["text"] == "今天过得怎么样?" and "waiting" not in cu.visible()[-1]["text"]
    assert cu.sync_doc()[f"m:{cu.pid}:{last_ai['uid']}"]["wait"] == 10
    n_c = len(calls)
    clock[0] += timedelta(minutes=9)
    cu.tick()
    assert not cu.busy and len(calls) == n_c, "没到 N 分钟不追问"
    script.append("还在忙吗?[waiting for reply]:[5]")
    clock[0] += timedelta(minutes=2)
    cu.tick()
    assert cu.busy, "N 分钟用户没回复: 追问"
    wait(cu)
    body = calls[-1][1][-1]["content"]
    assert "[waiting for reply]:[10]" in body and "追加询问" in body
    assert cu.visible()[-1]["text"] == "还在忙吗?" and "wait" not in [m for m in cu.messages if m["role"] == "ai"][-1], "追问的回复不再等待"
    n_c = len(calls)
    clock[0] += timedelta(minutes=30)
    cu.tick()
    assert not cu.busy and len(calls) == n_c, "一条消息最多追问一次"
    script.append("再见[waiting for reply]:[3]")
    cu.send("在的")
    wait(cu)
    script.append("好")
    cu.send("刚才忙")
    wait(cu)
    clock[0] += timedelta(minutes=10)
    n_c = len(calls)
    cu.tick()
    assert not cu.busy and len(calls) == n_c, "用户回复了, 对方最后一条没有等待: 不追问"
    cu.set_cfg(persona="小夏.txt")
    cu.flush()

    # README 里的标准写法: 例子能被认出来; 打开 OIT / 换人设 / 新对话都会重新读人设日程
    assert "日程的标准写法" in readme_text() and schedule_segments(readme_text()) == [("rest", 520), ("idle", 750)]
    log5 = lambda: open(os.path.join(td, "sched", "comm.log"), encoding="utf-8").read()
    assert "SCHEDULE 启动" in log5() and "SCHEDULE 换人设: 空闲 1 段, 休息 1 段" in log5()
    open(os.path.join(folder, "日程.txt"), "a", encoding="utf-8").write("14:00-14:30:空闲\n")
    c5.clear()
    assert "SCHEDULE 新对话: 空闲 2 段, 休息 1 段" in log5(), "新对话: 人设改过的日程马上生效"
    assert sorted(c5._segments()) == [("idle", 750), ("idle", 840), ("rest", 1240)]
    for x in (c5, c6):
        x.flush()

    # ---- 非正在聊天的对象也会主动发消息 (开关 bg_pro, 默认开) / 每个对象可以禁止 (set_muted) ----
    bgp = os.path.join(td, "bgp")
    os.makedirs(bgp)
    open(os.path.join(bgp, "甲.txt"), "w", encoding="utf-8").write("你是甲.\n12:30-13:00:空闲\n空闲发起谈话概率:100%\n")
    open(os.path.join(bgp, "乙.txt"), "w", encoding="utf-8").write("你是乙.\n")
    os.makedirs(os.path.join(td, "bgd"))
    cb = Comm(os.path.join(td, "bgd"), bgp, now=lambda: clock[0], chat_fn=fake)
    cb.ai.set_profile("deepseek", api_key="k")
    cb.ai.provider = "deepseek"
    cb.snapshot = lambda: {"state": "idle"}
    assert cb.bg_on() and not cb.is_muted()
    for who in ("甲.txt", "乙.txt"):                           # 两个人都聊过
        cb.set_cfg(persona=who)
        script.append("你好")
        cb.send("hi")
        wait(cb)
    pid_jia = pid_of("甲.txt")
    assert cb.persona_name() == "乙.txt" and pid_jia in cb.chat_pids()

    def bg_fire(c, begin):                                     # 日程段开始前 200 秒起, 每 2 秒看一次; 返回开始请求时离开始还有几秒
        for s in range(-200, 0, 2):
            clock[0] = begin + timedelta(seconds=s)
            c.tick()
            if c._bg_busy:
                return s
        return None

    def bg_wait(c):
        for _ in range(300):
            time.sleep(0.01)
            c.tick()
            if not c._bg_busy:
                return
        raise AssertionError("超时")
    pings_b = []
    cb.on_proactive = lambda: pings_b.append(1)
    cb.set_cfg(bg_pro=False)                                  # 开关关着: 不发
    assert bg_fire(cb, datetime(2027, 5, 1, 12, 30)) is None
    cb.set_cfg(bg_pro=True)
    cb.set_muted(True, pid_jia)                               # 禁止了这个人: 不发
    assert cb.is_muted(pid_jia) and not cb.is_muted() and bg_fire(cb, datetime(2027, 5, 2, 12, 30)) is None
    assert cb.sync_doc()[f"mute:{pid_jia}"] is True
    cb.set_muted(False, pid_jia)
    script.append("甲来找你")
    s = bg_fire(cb, datetime(2027, 5, 3, 12, 30))
    assert s is not None and -182 <= s <= -60, s
    unread0 = cb.unread
    bg_wait(cb)
    st_jia = cb._state_of(pid_jia)
    assert [m["text"] for m in st_jia["messages"] if m["role"] == "ai"][-1] == "甲来找你"
    assert cb.unread == unread0 + 1 and pings_b == [1]
    assert all(m["text"] != "甲来找你" for m in cb.messages), "现在聊的是乙: 乙的记录里没有"
    cb.set_cfg(persona="甲.txt")                              # 切到甲: 能看到
    assert cb.visible()[-1]["text"] == "甲来找你"
    cb.flush()
    return "comm selftest OK"


if __name__ == "__main__":
    print(_selftest())
