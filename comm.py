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
  · 休息开始时, 如果这次休息超过 20 分钟, 会在 3~5 分钟后让模型主动发一条消息.
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
PROACTIVE_MIN_REST = 20 * 60          # 休息超过这么久才会主动发消息
PROACTIVE_DELAY = (3 * 60, 5 * 60)    # 休息开始后多久主动发
RETRY_GAP = 10 * 60                   # 对方不方便回复时, 多久再把消息交给模型
RETRY_MAX = 12
HISTORY_SENT = 60                     # 每次请求最多带多少条聊天记录
HISTORY_CHARS = 120000                # 带的聊天记录总共最多多少字 (超了从最老的开始不带)
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
            "avatar_me": "", "avatar_ai": "", "pro_key": "", "retry": {}, "clear_ts": 0, "seeded": False, "legacy_done": False, "user_profile": ""}


# ══════════════ 前置 prompt 与 README ══════════════
def rest_function_text() -> str:
    g = config.REST_GAIN
    return (f"专注 t 分钟后建议休息 F(t) 分钟: t≤15 时 F={g:g}·t/6; 15<t<150 时 "
            f"F={g:g}·(7/14850·t²+407/1485·t−170/99); t≥150 时 F={g:g}·(50+5/ln1.2·ln(t/150)); "
            f"例如专注 100 分钟休息约 {config.compute_F(100):.0f} 分钟")


def prefix_prompt() -> str:
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
        "\n以下是用户设定的人设:\n")


def readme_text() -> str:
    return (
        "如何编写人设prompt:\n\n"
        "人设的基础规则:\n"
        "在你的人设prompt被发送之前,会被附加上一段额外的前置prompt和休息时间函数相关的内容:\n"
        f"'{prefix_prompt()}'\n"
        "你可以根据以上设定编写你的人物日程/喜好.但是最好不要把对方设置为整天都空闲,尊重一下上面的设定吧...\n\n"
        "文件格式: 一个 .txt 或 .md 文件就是一个人设, 文件名就是人设的名字, 整个文件的内容就是人设prompt.\n"
        "放进这个文件夹后, 在 设置 → 沟通 里选择它 (也可以用\"导入\"按钮复制进来).\n")


# OIT 自带的人设: 第一次运行时放进人设文件夹 (之后随便改 / 删, 不会再被写回), 并且默认选中第一个
BUILTIN_PERSONAS = {"Claudia.txt": """你将扮演一位文静,冷淡的大小姐Claudia,并且用语十分正式.
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
- 提到爱好时,如果对方没有深入问就不展开.

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
def is_refusal(text: str) -> bool:
    return REFUSE in re.sub(r"\s+", " ", str(text or "")).lower()


def stamp(now: datetime) -> str:
    return f"{now:%Y-%m-%d} 周{WEEKDAY[now.weekday()]} {now:%H:%M}"


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
    msgs = list(msgs[-HISTORY_SENT:])
    total = 0
    keep = []
    for m in reversed(msgs):
        total += len(str(m.get("api") or m.get("text") or ""))
        if total > HISTORY_CHARS and keep:
            break
        keep.append(m)
    msgs = list(reversed(keep))
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
        self.attach = {"stats": [], "cal": [], "contract": False}     # 📎 确定过的附加数据: 随下一条消息发出
        self.attach_touched = False    # 用户确定过一次之后, 📎 窗口不再默认勾 整点记录 / 专注计时
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
        self.messages = self.messages[-KEEP:]
        self._write_async(self._chat_path(self.pid), self._state_payload(self._active_state()))

    def set_cfg(self, **kw):
        if "persona" in kw:
            self.set_persona(kw.pop("persona"))
            self.save_chat()
        self.cfg.update(kw)
        self.save_cfg()
        self._changed()

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
        for pid in self.chat_pids():
            st = self._state_of(pid)
            for m in st["messages"][-SYNC_N:]:
                e = {"role": m["role"], "text": m["text"], "at": m["at"], "ts": int(m["ts"])}
                if m.get("api"):
                    e["api"] = m["api"]
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
                st["messages"].append(m)
                known.add(uid)
                touched[pid][1] = True
                if m["role"] == "ai":
                    st["retry"] = {}
                    if not (self.viewing and pid == self.pid):
                        self.unread += 1
            elif key == "profile":
                new = str(val or "")[:MAX_PROFILE] if val is not None else ""
                if new != self.cfg.get("user_profile"):
                    self.cfg["user_profile"] = new
                    self.save_cfg()
                    self._changed()
            elif kind == "pro" and isinstance(val, str):
                st_of(rest)[0]["pro_key"] = val
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
        if touched:
            self._changed()

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
        return [m for m in self.messages if m["role"] in ("me", "ai")]

    def unanswered(self) -> bool:
        vis = self.visible()
        return bool(vis) and vis[-1]["role"] == "me"

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
        now = self.now()
        ts = max(self._ms(), self._last_ts + 1)          # 同一台设备上严格递增
        self._last_ts = ts
        m = {"id": self._take_id(), "uid": f"{ts}-{(self.device() or 'local')[:4]}", "role": role, "text": text,
             "at": now.isoformat(timespec="seconds"), "ts": ts}
        if api:
            m["api"] = api
        self.messages.append(m)
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
        self.attach = {"stats": list(sel.get("stats", [])), "cal": list(sel.get("cal", [])),
                       "contract": bool(sel.get("contract"))}
        self.attach_touched = True
        self._changed()

    def attach_form(self) -> dict:
        """📎 窗口打开时的勾选: 确定过的; 还没确定过 = 默认勾 整点记录 和 专注计时."""
        if self.attach_touched or self.attach_label():
            return self.attach
        import comm_data
        return {"stats": list(comm_data.DEFAULT_STATS), "cal": [], "contract": False}

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
        system = prefix_prompt() + read_persona(self.personas, self.persona_name())
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

        def ask(m):
            return self.chat_fn(profile, system, m, max_tokens=CHAT_TOKENS, temperature=CHAT_TEMPERATURE,
                                timeout=CHAT_TIMEOUT)

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
                self.log(f"REQ {kind} FAIL {time.monotonic() - started:.1f}s {e.kind}: {e.message}")
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
        refused = not err and is_refusal(reply)
        if refused:
            self.log(f"REPLY refused ({kind}): 对方回了 [refuse to reply], 不显示" + (", 稍后重试" if self.unanswered() else ""))
        if err or refused:
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
            if extra:
                self._add("sys", extra[0], extra[1])
            self._add("ai", reply.strip())
            if not self.viewing:
                self.unread += 1
        self.save_chat()
        self._changed()
        self._push()
        if self._again:
            self._again = False
            if self.unanswered() and not err:
                self._request("user")

    # ---- 每秒一次 ----
    def tick(self):
        with self._lock:
            done, self._results = self._results, []
        for res in done:
            self._finish(*res)
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
        # 休息开始 3~5 分钟后主动发一条
        if snap.get("state") == "break" and snap.get("break_start") and snap.get("break_total_s", 0) > PROACTIVE_MIN_REST:
            start = snap["break_start"]
            key = start.isoformat(timespec="seconds")
            if key != self.cfg.get("pro_key"):
                due = start + timedelta(seconds=random.Random(key).randint(*PROACTIVE_DELAY))
                if now >= due:
                    self.cfg["pro_key"] = key
                    if (now - due).total_seconds() <= 120:          # 错过太久 (程序刚打开) 就算了
                        mins = snap["break_total_s"] / 60
                        self._trigger("proactive", f"[系统] 用户刚开始休息, 这次计划休息约 {mins:.0f} 分钟. "
                                                   f"请根据人设主动给用户发一条消息, 话题自己找; 如果此刻不方便, 返回{REFUSE}.")
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

    def fake(profile, system, messages, **kw):
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

    # 主动发消息: 休息 > 20 分钟, 开始后 3~5 分钟
    os.makedirs(os.path.join(td, "d2"))
    c2 = Comm(os.path.join(td, "d2"), folder, now=lambda: clock[0], chat_fn=fake)
    c2.ai.set_profile("deepseek", api_key="k")
    c2.ai.provider = "deepseek"
    c2.set_cfg(persona="小夏.txt")
    start = clock[0]
    snap2 = {"state": "break", "break_start": start, "break_total_s": 25 * 60, "rest_left_min": 25,
             "focus_total_min": 60, "focus_cur_min": 0}
    c2.snapshot = lambda: snap2
    c2.tick()
    assert not c2.busy
    clock[0] = start + timedelta(minutes=2, seconds=59)
    c2.tick()
    assert not c2.busy
    script.append("休息啦, 在干嘛?")
    clock[0] = start + timedelta(minutes=5, seconds=1)
    c2.tick()
    assert c2.busy
    wait(c2)
    assert c2.visible()[-1]["role"] == "ai" and "主动给用户发一条消息" in calls[-1][1][-1]["content"]
    assert not c2.visible()[:-1] and c2.unread == 1
    c2.tick()
    assert not c2.busy, "同一次休息只主动发一次"
    # 休息不到 20 分钟不发
    start2 = clock[0] + timedelta(minutes=1)
    snap2.update(break_start=start2, break_total_s=15 * 60)
    clock[0] = start2 + timedelta(minutes=6)
    c2.tick()
    assert not c2.busy
    # 主动消息被拒绝: 什么也不留下
    snap2.update(break_start=clock[0], break_total_s=30 * 60)
    script.append(REFUSE)
    clock[0] += timedelta(minutes=5)
    c2.tick()
    wait(c2)
    assert len(c2.visible()) == 1 and sum(m["role"] == "sys" for m in c2.messages) == 1

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
    assert vis[0]["text"] == "📎 统计·联网理由\n帮我看看" and vis[1]["role"] == "ai"
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
    assert c2f.attach_form()["stats"] == ["log", "focus"] and c2f.attach_label() == ""
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
    assert c.visible()[-1]["text"] == "在专注前收到", "进入专注前发出的请求, 回复到了照样收下"
    n_calls = len(calls)
    c.cfg["retry"] = {"at": c._ms() - RETRY_GAP * 1000 - 5, "n": 1}
    c._add("me", "专注前最后一句")
    c.tick()
    assert not c.busy and len(calls) == n_calls, "专注中不会重试"
    c.blocked = lambda: ""
    script.append("空下来了")
    c.tick()
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
    assert read_persona(fresh_personas, "Claudia.txt").startswith("你将扮演一位文静,冷淡的大小姐Claudia")
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
    clock[0] = start + timedelta(minutes=5)
    script.append("休息啦")
    A.tick()
    assert A.busy
    wait(A)
    pull(A, B)
    B.leader = lambda: True
    B.tick()
    assert not B.busy and [m["text"] for m in B.visible()] == ["休息啦"]
    A.flush()
    B.flush()
    return "comm selftest OK"


if __name__ == "__main__":
    print(_selftest())
