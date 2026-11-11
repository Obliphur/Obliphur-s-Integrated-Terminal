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
  · 模型会主动发消息 (MECHANICS 24.4): I. 人设自己正处在空闲 / 休息段里, 而且这段还剩 10 分钟以上: 段开始后 1~3 分钟;
    II. 人设日程里每段"空闲"开始前 1~3 分钟
    (概率写在人设里: "空闲发起谈话概率:n%", 没写默认 80%); III. 人设日程里每段"休息"开始前 1~3 分钟 ("休息发起谈话概率:n%", 默认 60%);
    IV. 人设日程里每段"睡眠"开始后 5 分钟 ("睡眠发起谈话概率:n%", 默认 60%; 睡眠期间只发这一条, 用户回复按日程拒绝). 收到时如果用户不在沟通页面, 有柔和的提示音.
  · 任何新消息 (对方主动发的, 回复用户的, 追问……) 只要用户不在沟通页面, 都会响一声柔和的提示音 (在页面上看着就不响).
  · 用户专注 / 睡眠时对方仍会按日程发 / 重试 / 追问, 但消息先藏起来、不通知 (回到休息 / 空闲才显示 + 提示音);
    用户外出时对方不会尝试发.
  · 聊天页顶上显示对方现在的状态 (按人设日程里的专注 / 休息 / 空闲 / 睡眠段, 例如 "专注中"; 前面有一个颜色跟着状态变的小圆点).
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
import i18n
from i18n import tr
import routine
from . import comm_readme
from .comm_schedule import PROACTIVE_P, seg_kind, seg_detail, schedule_entries, schedule_loops, loop_entries, schedule_segments, pro_pct, persona_probs
from .comm_reply import CUT_DEFAULT_MIN, CUT_MAX_MIN, HALFWIDTH_SYMBOLS, NET_DEFAULT_MIN, NET_MAX_MIN, WAIT_MAX_MIN, _BRACKET_RE, _CUT_N_RE, _CUT_RE, _EMPTY_WRAP_RE, _NET_RE, _WAIT_RE, clean_reply, cut_minutes, net_minutes, to_halfwidth
from .comm_builtin import BUILTIN_PERSONAS, DEFAULT_PERSONA, DEFAULT_BACKGROUND, DEFAULT_ROUTINE, ASSISTANT_PERSONA, ASSISTANT_DETAIL_LINE, BACKGROUND_LEGACIES, ASSISTANT_SLEEP_LINE, ASSISTANT_TITLE
from .comm_prompt import REFUSE, SHIFT_TAG, MSGS_TAG, THINK_LONG, THINK_WORDS, THINK_TIME_RE, think_text_reason, JOINT_RULE, rest_function_text, prefix_prompt
from .comm_persona import SHIFT_LO, SHIFT_HI, SHIFT_KEEP, MAX_PERSONA, MEM_MAX, PERSONA_EXT, readme_text, pid_of, love_norm, love_line, shift_norm, shift_pairs, hm_text, shift_line, mem_fields, seed_builtin, ensure_personas, list_personas, persona_title, uses_prefix, system_head, PERSONA_HEAD, persona_system, persona_labels, read_persona, ROUTINE_SUFFIX, COMPANION_SEP, companion_base, is_companion_name, companion_files, companions_of, routine_name_of, read_routine, read_persona_full, import_persona

from .comm_const import *

from .comm_joint import JointMixin
from .comm_status import StatusMixin
from .comm_chat import ChatMixin
from .comm_flow import FlowMixin
from .comm_sched import SchedMixin
from .comm_share import ShareMixin
from .comm_rt import RtMixin
from .comm_misc import MiscMixin

def char_delay(text: str) -> float:
    """一条消息的打字延时 (秒): 中文每个 0.2, 其他每个 0.1."""
    s = str(text or "")
    n = len(_CJK_RE.findall(s))
    return n * CHAR_DELAY_CJK + (len(s) - n) * CHAR_DELAY


from .comm_util import (OIT_TAG, STATE_TAG, COND_TAG, WEEKDAY, MAX_MSG_CHARS, HISTORY_SENT, HISTORY_STEP, HISTORY_CHARS,
                       is_claudia, is_refusal, stamp, urlsplit_host, info_tags, joint_answer, _joint_time, clip_message,
                       cache_layout, oit_text, first_turn, api_messages, sanitize_mem)

def follow_delay(profile: dict, override: float = None) -> float:
    """规则 VIII 第二次调用先等多久 (秒): 按模型类型的缓存策略; override 是手动 / 自检设的值."""
    if override == 0.0:                      # 自检把默认值改成 0: 不等
        return 0.0
    if override is not None and override != FOLLOW_DELAY:
        return override
    return ai_assist.cache_plan(profile)["delay"]



# ══════════════ 管理器 ══════════════
class Comm(JointMixin, StatusMixin, ChatMixin, FlowMixin, SchedMixin, ShareMixin, RtMixin, MiscMixin):
    """聊天记录, 设置, 发请求 (线程), 主动发消息, 对方不方便时的重试, 设备同步. 界面线程每秒调一次 tick().

    设备同步 (MECHANICS 24.6): 聊天记录 / 清空 / "30 分钟内不重复"的记录 / 重试状态 / 主动消息的标记随设备同步 (文档 comm);
    API Key, 人设, 头像, 默认发送的信息等设置不同步. 只有"最后有人操作的设备" (leader) 去调用 AI / 主动发 / 重试, 回复同步给另一台."""

    def __init__(self, data_dir: str, personas: str, now=datetime.now, chat_fn=None):
        self.dir = data_dir
        from ai_voice import Voice
        self.voice = Voice(data_dir)
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
        self.user_day_fn = lambda day: None      # (日期) → (专注秒数, 空闲秒数) 或 None; app 接上 (dm.day_kinds), [condition] 判定用
        self._cond_next = 0.0
        self.break_end_fn = lambda: None     # 用户这次休息真正结束的时刻 (datetime; 没在休息 = None); app 接上, 联合行动结束后对方的休息跟着它
        self.state_fn = lambda: ""    # 计时器现在的状态 ("idle" / "break" / "focus" / "sleep" / "out"); app 接上, 用来记空闲时间里开着沟通界面多久
        self.contract_fn = lambda: False     # 用户有没有合约在进行 (contract.active); app 接上: 日程跟随在合约期间自动关闭
        self.on_sf = None                    # 对方的状态变了, 要问用户跟不跟随 (kind, 已经带上名字的提示文字); app 接上
        self._sf_last = None                 # 上一秒对方的状态 (pid, kind); None = 没在看 (开关关着 / 合约中), 重新开始看时先记下现在的状态, 不提示
        self._idle_last = None
        self._idle_push = 0
        self.busy = False
        self.busy_since = 0.0
        self.error = ""
        self.snapshot = lambda: {}     # 计时器状态; app 接上
        self.data_fn = lambda sel: ""  # 今天 / 日历 / 统计的数据 (comm_data.build); app 接上
        self.fog = lambda: False       # 合约黑雾中: 不发数据
        self.cut_exempt = lambda: False    # 开发者模式下不受 [cut off] 限制 (app 接上)
        self.blocked = lambda: ""      # 现在不能用沟通的原因 ("" = 可以): 合约的"无言为真", 或者不在休息 / 空闲 (专注 / 睡眠 / 外出中)
        self.ai_blocked = lambda: self.blocked()   # 对方现在不能主动发消息的原因 ("" = 可以): app 接上; 睡眠中仍可发, 只是不通知
        self.hard_block = lambda: False   # 连等回复的这条也不要了的那种 (无言为真); 只是不在休息 / 空闲时, 已经发出去的请求回复到了照样收下
        self.info_fn = lambda tag: ""  # 对方要的资料: "contract" → 词条库; app 接上
        self.device = lambda: ""       # 本机的设备 id (sync.my_id); app 接上
        self.devices = lambda: []      # 本机 + 绑定的设备 [(id, 名字)]: 共时闹钟选响铃的设备用; app 接上
        self.leader = lambda: True     # 本机是不是最后有人操作的设备: 只有它调用 AI / 主动发 / 重试 (sync.is_latest_active); app 接上
        self.on_sync = None            # 聊天记录有改动, 马上同步一次; app 接上
        self.on_proactive = None       # 有新消息, 而用户不在沟通页面: 放柔和的提示音; app 接上 (后台线程里调用也行)
        self.share = self._share_fresh()    # share data (1.0.357): 本机界面的状态, 不存盘 / 不同步
        self.share_fn = lambda page: ("", "", "")     # 某一页的 (页面名, 用户的数据, 对方的数据); app 接上
        self.on_share_open = None      # 用户接受了对方的 share data 邀请 / 对方同意了用户的 share data 请求: app 接上, 跳到有数据的页面
        self.on_share_notice = None    # 对方拒绝 / 没回复用户的 share data 请求: app 接上, 弹一条提示 (参数是中文原文, 界面再翻译)
        self.focus_device = lambda: self.leader()
        self.focus_notices = {}
        self._focus_notice_seen = set()
        self.shift_ask = None          # 用户请求平移对方的日程起点, 等对方回 [Yes] / [No]: {"pid", "ts", "sent", "to"}; 本机界面状态, 不存盘 / 不同步 (结果记进聊天状态里的 shift, 同步)
        self.share_ask = None          # 用户点了 share data, 等对方回 [Yes] / [No]: {"pid", "ts", "sent"}; 本机界面状态, 不存盘 / 不同步
        self._share_end = ""           # 排着的"总结这次 share data"请求 (对方正在回别的话时等一等)
        self.on_change = None          # 界面线程里调用
        self._lock = threading.Lock()
        self._wlock = threading.Lock()
        self._pending = {}
        self._writing = False
        self._results = []
        self._again = False
        self._req_ts = 0              # 最近一次请求发出时, 最后一条用户消息的时间 (ts): 回复到了之后看用户有没有又发了新的
        self._catchup = False         # 补问被拒绝了 (回复已经显示, 后发的消息还没人回): 重试时即使最后一条是对方的也要问
        self._epoch = 0               # clear() 加一: 清空之前发出的请求, 回复到了也丢掉
        self.follow_delay = FOLLOW_DELAY   # 第二次调用等多久 (秒); 自检里设成 0
        self._follow_at = 0           # 第二次调用安排在什么时候 (毫秒, 0 = 没有)
        self._follow_epoch = 0        # 安排的时候的 _epoch: 期间清空过就不发
        self._follow_msg = ""         # 第一条回复的消息 id: 等它显示出来 (打字延时走完 / 被藏起来也算) 才调用第二次
        self._next_id = 1
        self._last_ts = 0
        self._seg_sig = None          # 人设文件的 (名字, 修改时间, 大小): 变了才重新读日程
        self._claudia_sig = None      # Claudia 文件的 (修改时间, 大小): 变了才重新检查有没有被手动改过
        self._persona_sig = {}        # 人设文件名 → (修改时间, 大小): 变了才检查有没有被手动改过
        self._persona_pending = None  # 正在等用户决定的人设修改 (文件名, 内容 crc)
        self._persona_backup_dir = os.path.join(self.dir, "persona_backup")
        self._seg_cache = []          # [(kind, 开始时刻)]: 主动发消息用
        self._entry_cache = []        # 完整日程条目 (开始 / 结束 / 名字 / 修饰): 显示对方的状态用
        self._loop_cache = []         # 人设里的循环段 [(开始, 结束, N)]: 联合行动之后重新计算用
        self._rt = None               # 现在聊的对象的日程文件解析出来的 routine.Routine (没有日程文件 = None): 有它就走 routine 的日程 / 数据
        self._rt_store = None         # 对方 (有日程文件的) 实际发生过的数据: routine.Store, 存在数据目录的 routine_<编号>.json
        self._rt_next = 0.0           # 下一次把数据补到现在的时刻 (time.monotonic)
        self._rt_lock = threading.RLock()
        self._bg_rt = {}              # 非正在聊天的对象: 人设文件名 -> (文件特征, routine.Routine 或 None)
        self.joint_draft = {}         # 联合行动: 选好了词条 / 开始时间, 还没发出去: {terms, start, end}
        self.on_joint_due = None      # 联合行动约定的开始时间到了 (还没开始合约): app 接上, 提示用户去开始
        self._joint_due_id = ""       # 已经提示过的联合行动编号
        self._joint_pending = None    # 联合行动: 要让对方回应一句, 但对方正在回别的话: (系统消息, 毫秒时间戳), 等对方空下来再调用
        self._pro_cache = {}          # 同一个人设文件里写的主动发起谈话的概率 (persona_probs)
        self._bg_seg = {}             # 非正在聊天的对象: 人设文件名 -> (文件特征, 日程段, 概率)
        self._bg_busy = None          # 正在为哪个非正在聊天的对象请求 (对象编号), 同时最多一个
        self._bg_last = 0             # 上一次为它们发请求的时刻 (毫秒)
        self._bg_results = []
        self._mem_busy = False        # 正在整理记忆卡
        self._mem_results = []
        self._mem_next = 0            # 下一次可以整理记忆卡的时刻 (毫秒)
        self.load()
        ensure_personas(self.personas)
        if not self.cfg.get("seeded"):                      # 第一次运行: 放进自带的人设, 还没选过人设就默认选 Claudia
            seed_builtin(self.personas)
            if not self.cfg.get("persona") and DEFAULT_PERSONA in list_personas(self.personas):
                self.set_persona(DEFAULT_PERSONA)
            self.cfg["seeded"] = True
            self.save_cfg()
        if not self.cfg.get("assistant_seeded"):            # 老版本升级上来: 补一次 Assistant (已有同名文件的不动)
            seed_builtin(self.personas, ASSISTANT_PERSONA)
            self.cfg["assistant_seeded"] = True
            self.save_cfg()
        self._upgrade_assistant_prompt()
        self._migrate_claudia()
        self._upgrade_claudia_prompt()
        self._upgrade_routine()
        self._upgrade_background()
        self._note_builtin_base()
        self._migrate_avatars()
        self._seed_default_avatars()
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
                "pro_key": str(d.get("pro_key") or ""), **mem_fields(d)}

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
        for k, v in mem_fields(st).items():
            self.cfg[k] = v
        self._next_id = max([int(m.get("id") or 0) for m in self.messages] + [0]) + 1
        for m in self.messages:
            if not m.get("id"):
                m["id"] = self._take_id()
        self._last_ts = max([int(m["ts"]) for m in self.messages] + [0])

    def _active_state(self) -> dict:
        return {"messages": self.messages, "clear_ts": int(self.cfg.get("clear_ts") or 0),
                "retry": dict(self.cfg.get("retry") or {}), "pro_key": str(self.cfg.get("pro_key") or ""),
                **mem_fields(self.cfg)}

    @staticmethod
    def _state_payload(st: dict) -> dict:
        return {"messages": [dict(m) for m in st["messages"][-KEEP:]], "clear_ts": int(st["clear_ts"]),
                "retry": dict(st["retry"]), "pro_key": st["pro_key"], **mem_fields(st)}

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
            for k, v in mem_fields(st).items():
                self.cfg[k] = v
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
        self.voice.stop()
        self._others[self.pid] = self._active_state()           # 现在的记下来 (内存里的 messages 是同一个列表)
        self.save_chat()
        self.cfg["persona"] = name
        self.pid = new
        st = self._others.pop(new, None) or self._read_chat(new)
        self._load_state(st)
        self._epoch += 1
        self.error = ""
        self.unread = 0
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

    # ---- AI 配置 (每个 AI 各用一份; 规则页的 AI 建议用默认的那份) ----
    def profile(self, persona: str = None) -> dict:
        """这个人设现在用的 API 配置的接口参数."""
        return self.ai.profile(self.ai.config_for(persona or self.persona_name()))

    def config_options(self) -> list:
        """沟通页下拉框: [{"id", "name", "model"}]."""
        return self.ai.configs()

    def current_config(self) -> str:
        return self.ai.config_for(self.persona_name())

    def set_config(self, cid: str):
        if not cid or cid == self.current_config():
            return
        self.ai.assign_config(self.persona_name(), cid)
        self.ai.save()
        self._changed()

    def ocr_image(self, path: str) -> str:
        """识别字体 (沟通页 🖼): 让模型把这张图片上的文字读出来, 返回文字 (没有文字 = ""). 阻塞, 要放在后台线程里调; 失败抛 AIError.
        图片只发给模型一次, 不存下来, 也不会进聊天记录 (OIT 不能发送图片)."""
        try:
            if os.path.getsize(path) > ai_assist.OCR_MAX_BYTES:
                raise ai_assist.AIError("format", "图片太大了 (最大 4.5 MB)")
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            raise ai_assist.AIError("format", "打不开这张图片.")
        started = time.monotonic()
        self.log(f"OCR start bytes={len(data)}")
        try:
            text = ai_assist.ocr_image(self.profile(), data, timeout=CHAT_TIMEOUT, chat=self.chat_fn)
        except ai_assist.AIError as e:
            self.log(f"OCR FAIL {time.monotonic() - started:.1f}s {e.kind}: {e.message}")
            raise
        self.log(f"OCR ok {time.monotonic() - started:.1f}s chars={len(text)}")
        return text

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
            self.cfg["msg_gone"] = int(self.cfg.get("msg_gone") or 0) + sum(
                1 for m in self.messages[:cut] if m.get("role") in ("me", "ai") and not m.get("hold"))      # 砍掉的也算在信息总数里
            self.messages = self.messages[cut:]
        self._write_async(self._chat_path(self.pid), self._state_payload(self._active_state()))

    def set_cfg(self, **kw):
        if "persona" in kw:
            self.set_persona(kw.pop("persona"))
            self.save_chat()
        self.cfg.update(kw)
        self.save_cfg()
        self._changed()
        if "split_send" in kw or "think_auto" in kw or "bg_pro" in kw or "mute_pro" in kw or "show_avatar" in kw or "sched_follow" in kw or "pro_lang" in kw:
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
        doc = {f"notice:{key}": dict(val) for key, val in self.focus_notices.items()
               if 0 <= self._ms() - val["ts"] <= 90_000}
        doc["voice"] = self.voice.sync_doc()
        if self.cfg.get("user_profile"):
            doc["profile"] = self.cfg["user_profile"]
        doc["split"] = self.split_on()                  # 分条发的开关: 调用 AI 的是主端, 两台设备要一致
        doc["think"] = self.think_auto()                # 自动开思考的开关: 同上
        doc["bgpro"] = self.bg_on()
        doc["sfollow"] = self.sf_on()                   # 日程跟随的开关: 提示在主端弹, 两台设备要一致
        doc["prolang"] = self.pro_lang()                # 主动谈话语言: 调用 AI 的是主端, 两台设备要一致
        doc["avatar"] = bool(self.cfg.get("show_avatar"))      # 显示头像: 设置页的开关也同步
        rec = self.cfg.get("idle_use") or {}
        if rec.get("d"):
            doc["idle"] = {"d": rec["d"], "s": float(rec.get("s") or 0)}      # 两台设备共用一份额度 (各自累计的取大的)
        if self.cfg.get("joint"):
            doc["joint"] = dict(self.cfg["joint"])           # 联合行动: 后改的为准
        if self.cfg.get("net"):
            doc["net"] = dict(self.cfg["net"])               # 限时网络: 后改的为准
        if (self.cfg.get("alarm_co") or {}).get("ts"):
            doc["alarm"] = dict(self.cfg["alarm_co"])        # 共时闹钟: 后改的为准
        for pid in set(self.chat_pids()) | set(self.cfg.get("mute_pro") or []):
            doc[f"mute:{pid}"] = self.is_muted(pid)
        for pid in self.chat_pids():
            st = self._state_of(pid)
            for m in st["messages"][-SYNC_N:]:
                e = {"role": m["role"], "text": m["text"], "at": m["at"], "ts": int(m["ts"])}
                if m.get("api"):
                    e["api"] = m["api"]
                if m.get("wait"):
                    e["wait"] = m["wait"]
                if m.get("hold"):
                    e["hold"] = True                          # 还在藏着的 (打字延时 / 等空闲): 内容也要同步, 对方也先藏着 (1.0.438)
                if m.get("late"):
                    e["late"] = True
                if m.get("due"):
                    e["due"] = int(m["due"])
                doc[f"m:{pid}:{m['uid']}"] = e
            if st["pro_key"]:
                doc[f"pro:{pid}"] = st["pro_key"]
            if st["retry"]:
                doc[f"retry:{pid}"] = dict(st["retry"])
            if st["clear_ts"]:
                doc[f"clear:{pid}"] = int(st["clear_ts"])
            if int(st.get("cut_until") or 0) > self._ms():
                doc[f"cut:{pid}"] = int(st["cut_until"])
            if love_norm(st.get("love"))["ts"]:
                doc[f"love:{pid}"] = love_norm(st.get("love"))      # 亲密关系 / 告白: 后改的为准
            if shift_norm(st.get("shift")):
                doc[f"shift:{pid}"] = shift_norm(st.get("shift"))   # 起点平移记录: 两边合起来 (按记下的时刻去重)
            if st.get("mem_auto"):
                doc[f"mem:{pid}"] = {"a": str(st.get("mem_auto") or ""),
                                     "u": int(st.get("mem_upto") or 0), "t": int(st.get("mem_ts") or 0)}
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
            if key == "voice":
                self.voice.apply_doc(val)
                self._changed()
                continue
            if kind == "notice":
                if isinstance(val, dict) and isinstance(val.get("text"), str) and isinstance(val.get("ts"), (int, float)):
                    if 0 <= self._ms() - val["ts"] <= 90_000:
                        rec = self.focus_notices.setdefault(rest, dict(val))
                        rec["shown"] = bool(rec.get("shown") or val.get("shown"))
                continue
            if kind == "clear":
                pid = rest
                try:
                    t = int(val)
                except (TypeError, ValueError):
                    continue
                st, _d, _k = st_of(pid)
                if t > st["clear_ts"]:
                    for name in list_personas(self.personas):
                        if pid_of(name) == pid:
                            self.voice.clear(name)
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
                if val.get("hold") or isinstance(val.get("due"), (int, float)):
                    m["hold"] = True                            # 对方那边还在藏着的: 这边也先藏着 (1.0.438)
                if val.get("late"):
                    m["late"] = True
                if isinstance(val.get("due"), (int, float)):
                    m["due"] = int(val["due"])
                st["messages"].append(m)
                fresh.append((pid, m))
                known.add(uid)
                touched[pid][1] = True
                if m["role"] == "ai":
                    st["retry"] = {}
                    if not (self.viewing and pid == self.pid) and not m.get("hold"):
                        self.unread += 1
            elif key == "idle" and isinstance(val, dict):
                try:
                    d, s = str(val["d"]), float(val["s"])
                except (KeyError, TypeError, ValueError):
                    continue
                mine = self.cfg.get("idle_use") or {}
                if d == f"{self.now():%Y-%m-%d}" and (mine.get("d") != d or s > float(mine.get("s") or 0)):
                    self.cfg["idle_use"] = {"d": d, "s": s}
                    self.save_cfg()
                    self._changed()
            elif key == "joint" and isinstance(val, dict):
                try:
                    t = int(val.get("ts") or 0)
                except (TypeError, ValueError):
                    continue
                if t > int((self.cfg.get("joint") or {}).get("ts") or 0):
                    self.cfg["joint"] = dict(val)
                    self.save_cfg()
                    self._changed()
            elif key == "net" and isinstance(val, dict):
                try:
                    t = int(val.get("ts") or 0)
                except (TypeError, ValueError):
                    continue
                if t > int((self.cfg.get("net") or {}).get("ts") or 0):
                    self.cfg["net"] = dict(val)
                    self.save_cfg()
                    self._changed()
            elif key == "alarm" and isinstance(val, dict):
                try:
                    t = int(val.get("ts") or 0)
                except (TypeError, ValueError):
                    continue
                if t > int((self.cfg.get("alarm_co") or {}).get("ts") or 0):
                    self.cfg["alarm_co"] = {"pid": str(val.get("pid") or ""), "devs": [str(x) for x in (val.get("devs") or [])], "ts": t}
                    self.save_cfg()
                    self._changed()
            elif key == "sfollow" and isinstance(val, bool):
                if val != self.sf_on():
                    self.cfg["sched_follow"] = val
                    self.save_cfg()
                    self._changed()
            elif key == "prolang" and isinstance(val, str):
                new = val.strip()[:MAX_PRO_LANG]
                if new != self.pro_lang():
                    self.cfg["pro_lang"] = new
                    self.save_cfg()
                    self._changed()
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
            elif key == "think" and isinstance(val, bool):
                if val != self.think_auto():
                    self.cfg["think_auto"] = val
                    self.save_cfg()
                    self._changed()
            elif key == "avatar" and isinstance(val, bool):
                if val != bool(self.cfg.get("show_avatar")):
                    self.cfg["show_avatar"] = val
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
            elif kind == "cut" and isinstance(val, (int, float)) and not isinstance(val, bool):
                st = st_of(rest)[0]                                  # 对方切断了谈话: 取更晚的
                if int(val) > int(st.get("cut_until") or 0):
                    st["cut_until"] = int(val)
                    touched[rest][1] = True
            elif kind == "love" and isinstance(val, dict):
                st = st_of(rest)[0]
                nv = love_norm(val)
                if nv["ts"] > love_norm(st.get("love"))["ts"]:
                    st["love"] = nv
                    touched[rest][1] = True
            elif kind == "shift" and isinstance(val, list):
                st = st_of(rest)[0]
                have = shift_norm(st.get("shift"))
                merged = shift_norm(have + shift_norm(val))
                if merged != have:
                    st["shift"] = merged
                    touched[rest][1] = True
            elif kind == "retry":
                st_of(rest)[0]["retry"] = dict(val) if isinstance(val, dict) else {}
                touched[rest][1] = True
            elif kind == "mem" and isinstance(val, dict):            # 记忆卡: 后改的为准 (两台各自整理时不会来回盖)
                st = st_of(rest)[0]
                try:
                    t = int(val.get("t") or 0)
                except (TypeError, ValueError):
                    continue
                if t <= int(st.get("mem_ts") or 0):
                    continue
                if int(st.get("clear_ts") or 0) > t:                    # 自动摘要比清空还早: 不要 (旧对话的摘要)
                    st["mem_auto"] = ""
                    st["mem_upto"] = 0
                else:
                    st["mem_auto"] = str(val.get("a") or "")[:MEM_MAX]
                    st["mem_upto"] = int(val.get("u") or 0)
                st["mem_ts"] = t
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
        for pid, m in fresh:
            name = next((n for n in list_personas(self.personas) if pid_of(n) == pid), "")
            if name:
                self.voice.request(name, m)
        self._synced_ping([m for pid, m in fresh if pid == self.pid])

    def _synced_ping(self, fresh: list):
        """别的设备刚收到的新消息同步过来了: 这台不在沟通页面的话也提示一声."""
        now = self._ms()
        for m in fresh:
            if m["role"] == "ai" and now - int(m["ts"]) <= 90 * 1000:
                self._ping()
                return

    # ---- 状态 ----
    def persona_name(self) -> str:
        n = self.cfg.get("persona") or ""
        return n if n in list_personas(self.personas) else ""

    # ---- 人设文件被手动修改: 先备份, 用户确认后清掉对应聊天 ----
    def _persona_backup_path(self, name: str) -> str:
        return os.path.join(self._persona_backup_dir, name)

    def _read_persona_raw(self, name: str):
        if not name or name != os.path.basename(name):
            return None
        try:
            with open(os.path.join(self.personas, name), "r", encoding="utf-8-sig") as f:
                return f.read(MAX_PERSONA)
        except (OSError, UnicodeDecodeError):
            return None

    def _write_persona_raw(self, name: str, text: str):
        try:
            with open(os.path.join(self.personas, name), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            return True
        except OSError:
            return False

    def _backup_persona_file(self, name: str):
        """把当前内容记成"上次看到的样子" (升级内置提示词后也调用, 免得被当成手动修改)."""
        text = self._read_persona_raw(name)
        if text is None:
            return
        try:
            os.makedirs(self._persona_backup_dir, exist_ok=True)
            with open(self._persona_backup_path(name), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except OSError:
            pass

    def check_persona_changes(self):
        """检查人设文件有没有被手动改过. 返回 {"name", "old", "new"} 或 None; 同一份内容只问一次."""
        if self._persona_pending is not None:
            return None
        try:
            os.makedirs(self._persona_backup_dir, exist_ok=True)
        except OSError:
            return None
        for name in list_personas(self.personas) + companion_files(self.personas):
            path = os.path.join(self.personas, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            sig = (st.st_mtime_ns, st.st_size)
            if self._persona_sig.get(name) == sig:
                continue
            self._persona_sig[name] = sig
            cur = self._read_persona_raw(name)
            if cur is None:
                continue
            bpath = self._persona_backup_path(name)
            if not os.path.isfile(bpath):
                self._backup_persona_file(name)                 # 第一次见到这个文件: 以现在为准
                continue
            try:
                with open(bpath, "r", encoding="utf-8-sig") as f:
                    old = f.read(MAX_PERSONA)
            except (OSError, UnicodeDecodeError):
                self._backup_persona_file(name)
                continue
            if cur != old:
                self._persona_pending = (name, f"{zlib.crc32(cur.encode('utf-8')):08x}")
                return {"name": name, "old": old, "new": cur}
        return None

    def resolve_persona_change(self, name: str, accept: bool):
        """accept=True: 确定, 清空这个人设的聊天; False: 还原文件, 聊天保留."""
        old = None
        try:
            with open(self._persona_backup_path(name), "r", encoding="utf-8-sig") as f:
                old = f.read(MAX_PERSONA)
        except (OSError, UnicodeDecodeError):
            pass
        if accept:
            cur = self._read_persona_raw(name)
            if cur is not None:
                self._backup_persona_file(name)
            self._clear_persona_chat(name)
        elif old is not None:
            self._write_persona_raw(name, old)
            self._seg_sig = None
            if self.persona_name() in (name, base_persona_of(self.personas, name)):
                self.reload_schedule("还原人设")
        self._persona_pending = None
        self._persona_sig.pop(name, None)
        self._changed()

    def _clear_persona_chat(self, name: str):
        if is_companion_name(self.personas, name):               # 改的是配套文件: 清的是它的人设的聊天
            name = base_persona_of(self.personas, name) or name
        self.voice.clear(name)
        pid = pid_of(name)
        if not pid:
            return
        if pid == self.pid:
            self.clear()
        else:
            self._store(pid, {"messages": [], "clear_ts": self._ms(), "retry": {}, "pro_key": ""})
            self._others.pop(pid, None)
            self._push()

    def _upgrade_assistant_prompt(self):
        """老版本的 Assistant 提示词 (没有"详细回答"那段) 且用户没改过: 换成当前内置版本.
        用户改过的不动. 这样升级后专注特例和详细回答都能用."""
        path = os.path.join(self.personas, ASSISTANT_PERSONA)
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                current = f.read(MAX_PERSONA)
        except (OSError, UnicodeDecodeError):
            return
        current_builtin = BUILTIN_PERSONAS[ASSISTANT_PERSONA]
        legacies = [current_builtin.replace(ASSISTANT_SLEEP_LINE, ""),
                    current_builtin.replace(ASSISTANT_SLEEP_LINE, "").replace(ASSISTANT_DETAIL_LINE, "")]
        if current.strip() not in {x.strip() for x in legacies}:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(current_builtin)
            self._backup_persona_file(ASSISTANT_PERSONA)
        except OSError:
            pass

    # ---- Claudia: 第一次升级时覆盖没改过的文件; 之后手动改过就提醒 ----
    def _claudia_path(self) -> str:
        return os.path.join(self.personas, DEFAULT_PERSONA)

    def _write_claudia_builtin(self, force: bool = True):
        """force=False (自动升级): 日程文件只在缺 / 是更早的内置版本时才换, 用户改过的不动."""
        try:
            os.makedirs(self.personas, exist_ok=True)
            with open(self._claudia_path(), "w", encoding="utf-8", newline="\n") as f:
                f.write(BUILTIN_PERSONAS[DEFAULT_PERSONA])
            rpath = os.path.join(self.personas, DEFAULT_ROUTINE)
            cur = self._read_persona_raw(DEFAULT_ROUTINE)
            if force or cur is None or cur.strip() in {x.strip() for x in ROUTINE_LEGACIES} or not cur.strip():
                with open(rpath, "w", encoding="utf-8", newline="\n") as f:     # 默认人设是 Claudia + 对方的背景 + 对方的日程
                    f.write(BUILTIN_PERSONAS[DEFAULT_ROUTINE])
            bpath = os.path.join(self.personas, DEFAULT_BACKGROUND)
            cur = self._read_persona_raw(DEFAULT_BACKGROUND)
            if force or cur is None or cur.strip() in {x.strip() for x in BACKGROUND_LEGACIES} or not cur.strip():
                with open(bpath, "w", encoding="utf-8", newline="\n") as f:
                    f.write(BUILTIN_PERSONAS[DEFAULT_BACKGROUND])
        except OSError:
            pass

    def _write_backup_text(self, name: str, text: str):
        try:
            os.makedirs(self._persona_backup_dir, exist_ok=True)
            with open(self._persona_backup_path(name), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except OSError:
            pass

    def _migrate_claudia(self):
        """第一次带上这个检查的版本: 没改过的 Claudia 换成当前内置版本; 认不出来的 (用户改过) 留着,
        备份写成当前内置版本, 之后 check_persona_changes 会弹"修改人设会清除聊天数据"."""
        if self.cfg.get("claudia_migrated"):
            return
        if os.path.isfile(self._claudia_path()) and not self.cfg.get("claudia_modified"):
            current = (self._read_persona_raw(DEFAULT_PERSONA) or "").strip()
            known = {BUILTIN_PERSONAS[DEFAULT_PERSONA].strip()} | {x.strip() for x in CLAUDIA_LEGACIES}
            if current and current not in known:                 # 用户改过的: 不能静默覆盖
                self._write_backup_text(DEFAULT_PERSONA, BUILTIN_PERSONAS[DEFAULT_PERSONA])
            else:
                self._write_claudia_builtin(force=False)
                self._backup_persona_file(DEFAULT_PERSONA)
                self._backup_persona_file(DEFAULT_ROUTINE)
                self._backup_persona_file(DEFAULT_BACKGROUND)
        self.cfg["claudia_migrated"] = True
        self._claudia_sig = None
        self.save_cfg()

    def _upgrade_claudia_prompt(self):
        """更早版本内置过的 Claudia 提示词 (用户没改过的) 自动换成当前内置版本.
        改过的不动, 交给 check_persona_changes 提醒."""
        path = self._claudia_path()
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                current = f.read(MAX_PERSONA)
        except (OSError, UnicodeDecodeError):
            return
        if current.strip() == BUILTIN_PERSONAS[DEFAULT_PERSONA].strip():
            return
        if current.strip() not in {x.strip() for x in CLAUDIA_LEGACIES} and self._text_crc(current) != self.cfg.get("claudia_base"):
            return                                  # 用户改过的: 不动 (改回上一次见到的内置版本就算没改过, 见 _note_builtin_base)
        self._write_claudia_builtin(force=False)
        self._backup_persona_file(DEFAULT_PERSONA)
        self._backup_persona_file(DEFAULT_ROUTINE)
        self._backup_persona_file(DEFAULT_BACKGROUND)
        self.cfg["claudia_modified"] = False
        self.cfg["claudia_notified"] = ""
        self._claudia_sig = None
        self.save_cfg()

    def _upgrade_routine(self):
        self._upgrade_companion(DEFAULT_ROUTINE, ROUTINE_LEGACIES, "routine_base", "ROUTINE 日程文件")

    def _upgrade_background(self):
        self._upgrade_companion(DEFAULT_BACKGROUND, BACKGROUND_LEGACIES, "background_base", "BACKGROUND 背景文件")

    def _upgrade_companion(self, fname: str, legacies: tuple, key: str, label: str):
        """Claudia 的配套文件 (日程 / 背景) 自己升级, 不看 Claudia.txt 有没有被改过: 缺 / 空白 → 补上当前内置版本;
        是更早的内置版本 (用户没改过) → 换成当前内置版本; 用户改过的不动. Claudia.txt 不在时不补 (不然它会被当成人设)."""
        if not os.path.isfile(self._claudia_path()):
            return
        cur = self._read_persona_raw(fname)
        builtin = BUILTIN_PERSONAS[fname]
        if cur is not None and cur.strip() and (cur.strip() == builtin.strip() or (
                cur.strip() not in {x.strip() for x in legacies} and self._text_crc(cur) != self.cfg.get(key))):
            return
        try:
            with open(os.path.join(self.personas, fname), "w", encoding="utf-8", newline="\n") as f:
                f.write(builtin)
        except OSError:
            return
        self._backup_persona_file(fname)          # 记成"上次看到的样子": 不弹"修改人设"
        self._seg_sig = None
        self.log(label + ("已补上" if cur is None or not cur.strip() else "已升级"))

    @staticmethod
    def _text_crc(text: str) -> str:
        return f"{zlib.crc32((text or '').strip().encode('utf-8')):08x}"

    def _note_builtin_base(self):
        """记下 Claudia.txt / Claudia_routine.txt 现在是不是内置版本: 是的话把这一版的校验值记成"上一次见到的内置版本".
        之后程序更新了内置版本, 文件只要还是 (或者用户改过又改回了) 这个旧版本, 就当没改过, 自动换成新版 ——
        不管中间弹窗点过什么, 也不用每个旧版本都进升级链."""
        changed = False
        for name, key in ((DEFAULT_PERSONA, "claudia_base"), (DEFAULT_ROUTINE, "routine_base"), (DEFAULT_BACKGROUND, "background_base")):
            cur = self._read_persona_raw(name)
            if cur is not None and cur.strip() == BUILTIN_PERSONAS[name].strip() and self.cfg.get(key) != self._text_crc(cur):
                self.cfg[key] = self._text_crc(cur)
                changed = True
        if changed:
            self.save_cfg()

    def check_claudia_integrity(self) -> bool:
        """Claudia 文件被手动改过吗. True = 现在该弹窗提醒 (同一份内容只提醒一次)."""
        path = self._claudia_path()
        try:
            st = os.stat(path)
        except OSError:
            self._claudia_sig = None
            return False
        sig = (st.st_mtime_ns, st.st_size)
        if sig == self._claudia_sig:
            return False
        self._claudia_sig = sig
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                text = f.read(MAX_PERSONA)
        except (OSError, UnicodeDecodeError):
            return False
        builtin = BUILTIN_PERSONAS[DEFAULT_PERSONA]
        if text.strip() == builtin.strip():                    # 已经还原 / 本来就是内置版本
            if self.cfg.get("claudia_modified") or self.cfg.get("claudia_notified"):
                self.cfg["claudia_modified"] = False
                self.cfg["claudia_notified"] = ""
                self.save_cfg()
            return False
        h = f"{zlib.crc32(text.encode('utf-8')):08x}"
        if self.cfg.get("claudia_notified") == h:              # 这份内容已经提醒过 (用户点了继续 / 关了窗)
            return False
        self.cfg["claudia_modified"] = True
        self.cfg["claudia_notified"] = h
        self.save_cfg()
        return True

    def claudia_differs(self) -> bool:
        """选着默认人设 (Claudia) 时, 它的三个文件 (Claudia / 背景 / 日程) 有没有哪个和内置版本不一样 (缺文件也算).
        设置 → 沟通 → 人设 用它决定要不要显示"还原"按钮."""
        if self.persona_name() != DEFAULT_PERSONA:
            return False
        for name in (DEFAULT_PERSONA, DEFAULT_BACKGROUND, DEFAULT_ROUTINE):
            cur = self._read_persona_raw(name)
            if cur is None or cur.replace("\r\n", "\n").strip() != BUILTIN_PERSONAS[name].replace("\r\n", "\n").strip():
                return True
        return False

    def restore_claudia(self):
        """把 Claudia 文件恢复成内置版本 (弹窗的"还原人设文件")."""
        self._write_claudia_builtin()
        self._backup_persona_file(DEFAULT_PERSONA)
        self._backup_persona_file(DEFAULT_ROUTINE)
        self._backup_persona_file(DEFAULT_BACKGROUND)
        self._note_builtin_base()
        self.cfg["claudia_modified"] = False
        self.cfg["claudia_notified"] = ""
        self._claudia_sig = None
        self._seg_sig = None
        self.reload_schedule("还原 Claudia")
        self.save_cfg()
        self._changed()

    def is_builtin_assistant_unmodified(self) -> bool:
        """名字是 Assistant (不分大小写), 并且文件和自带 Assistant 的提示词一字不差.
        只有这种情况, 用户在专注期间也能用沟通 (24.2.3 的特例)."""
        name = self.persona_name()
        if not name or persona_title(name).strip().casefold() != ASSISTANT_TITLE:
            return False
        return read_persona(self.personas, name) == BUILTIN_PERSONAS[ASSISTANT_PERSONA].strip()

    # ---- 人设文件本身: 随设备同步 (每 2 分钟检查一次变化; README 不同步, 各设备按界面语言生成) ----
    def persona_doc(self) -> dict:
        """人设文件夹里的 .txt / .md (文件名 → 全文), 给设备同步用."""
        out = {}
        for name in list_personas(self.personas) + companion_files(self.personas):
            try:
                with open(os.path.join(self.personas, name), "r", encoding="utf-8-sig") as f:
                    out[name] = f.read(MAX_PERSONA)
            except (OSError, UnicodeDecodeError):
                pass
        return out

    def apply_persona_doc(self, changes: dict):
        """别的设备同步来的人设文件: 新增 / 覆盖 / 删除 (None). 现在选着的人设受影响时重新读日程."""
        try:
            os.makedirs(self.personas, exist_ok=True)
        except OSError:
            return
        dirty = False
        for name, text in dict(changes or {}).items():
            if not isinstance(name, str) or not name or name != os.path.basename(name):
                continue
            if os.path.splitext(name)[1].lower() not in PERSONA_EXT or name.lower() == "readme.md":
                continue
            path = os.path.join(self.personas, name)
            try:
                if text is None:
                    if os.path.exists(path):
                        os.remove(path)
                        dirty = True
                    try:
                        os.remove(self._persona_backup_path(name))      # 备份也删掉 (以后同名新文件算第一次见到)
                    except OSError:
                        pass
                    continue
                new = str(text)[:MAX_PERSONA]
                try:
                    with open(path, "r", encoding="utf-8-sig") as f:
                        old = f.read(MAX_PERSONA)
                except (OSError, UnicodeDecodeError):
                    old = None
                if old != new:
                    with open(path, "w", encoding="utf-8", newline="\n") as f:
                        f.write(new)
                    dirty = True
                try:
                    with open(self._persona_backup_path(name), "r", encoding="utf-8-sig") as f:
                        bk = f.read(MAX_PERSONA)
                except (OSError, UnicodeDecodeError):
                    bk = None
                if old != new or bk != new:                     # 同步来的不是"手动改的": 备份跟着换 (文件已经一样、只有备份是旧的 —— 老版本留下的 —— 也换), 不弹"人设被修改"
                    self._write_backup_text(name, new)
            except OSError:
                pass
        if dirty:
            self._seg_sig = None
            self.reload_schedule("同步人设")
            self._changed()

    # ---- 发消息 ----
    def _mk(self, role: str, text: str, api: str = "", delay: bool = False, persona=None) -> dict:
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
        free = self.ai_free_at(now) if role == "ai" else None
        if free:                                           # 对方在专注: 推迟到下一个空闲 / 休息开始时才发 (到点再重新盖发送时间, 见 _release_held)
            m["hold"] = True
            m["late"] = True
            m["due"] = max([int(free.timestamp() * 1000)] + [int(x.get("due") or 0) for x in self.messages if x.get("hold")])
        elif delay and role == "ai" and text and (CHAR_DELAY > 0 or CHAR_DELAY_CJK > 0) and uses_prefix(self.persona_name()):   # 打字延时: 中文每字 0.2 s, 其他每字 0.1 s, 接在前一条还没显示的后面 (Assistant 是助理, 不装打字: 详细回答一等就是几分钟, 1.0.407)
            base = max([self._ms()] + [int(x.get("due") or 0) for x in self.messages if x.get("hold")])
            m["due"] = base + int(char_delay(text) * 1000)
            m["hold"] = True
        self.voice.request(persona if persona is not None else self.persona_name(), m)
        return m

    def _finish(self, kind: str, extra, reply: str, err: str, epoch: int = 0, op=None):
        self.busy = False
        self.busy_since = 0.0
        if self.hard_block():                           # 等回复的时候"无言为真"生效了: 这条回复不要了
            self._again = False
            if kind == "share_ask":
                self._share_ask_end("{name} 没有回复, share data 没有打开")
            if kind == "shift_ask":
                self._shift_ask_end("{name} 没有回复, 起点没有平移")
            self._changed()
            return
        if epoch != self._epoch:                        # 发出之后清空过聊天记录: 这条回复不要了
            self._again = False
            if kind == "share_ask":
                self._share_ask_end()
            if kind == "shift_ask":
                self._shift_ask_end()
            self._changed()
            return
        if op and not err:
            self._op_apply(op)                          # 对方发起的联合行动: 不管这条话有没有显示, 都记下来
        if kind == "net" and err:
            self._net_failed()                          # 搜索那次请求失败: 过一会儿再试
        elif not err and kind in NET_KINDS:
            net_n = net_minutes(reply)                  # 规则 XIV: 申请限时网络 (这条话照常显示)
            if net_n is not None:
                self._net_apply(net_n)
        cut = cut_minutes(reply) if not err else None
        if cut is not None:
            self._cut_apply(cut)                        # 被骚扰: [cut off][N] 切断谈话 N 分钟 (这条话照常显示, 有的话)
        text, wait_min = clean_reply(reply) if not err else ("", None)
        if text and is_claudia(self.persona_name()):
            text = to_halfwidth(text)                           # Claudia: 显示前全角符号换成半角 (1.0.408)
        queried = not err and bool(info_tags(reply))           # 对方在查询模块资料 (有没有 [refuse to reply] 都算): 这条一定不显示
        if queried:
            text = ""
        refused = not err and is_refusal(reply)
        if not err and not refused and _SHARE_RE.search(reply):
            self._share_invite_make()                   # 前置 prompt XV: 对方想看你们的 OIT 数据, 聊天页上等用户点 接受 / 拒绝
        catchup = bool(extra) and CATCHUP_TAG in str(extra[0])          # 这是补问 (见 CATCHUP_BODY)
        joint_ans = ""                                          # 对联合行动邀请的回复 [Yes] / [No]
        if not err and not refused and not queried and kind in ("user", "retry") and self._joint().get("state") == "wait":
            joint_ans = joint_answer(reply)
            if joint_ans and not text:                          # 只回了 [Yes] / [No]: 聊天里写一行结果 (不然这条邀请像是没人回, 重开程序还会再问一次)
                text = tr("✓ 接受了联合行动") if joint_ans == "yes" else tr("✗ 拒绝了联合行动")
        share_ans = ""                                          # 对 share data 请求的回复 [Yes] / [No]
        if kind == "share_ask" and not err and not refused and not queried:
            share_ans = joint_answer(reply)
            if share_ans and not text:                          # 只回了 [Yes] / [No]: 聊天里写一行结果
                text = tr("✓ 同意了 share data") if share_ans == "yes" else tr("✗ 拒绝了 share data")
        shift_ans = ""                                          # 对起点平移请求的回复 [Yes] / [No]
        if kind == "shift_ask" and not err and not refused and not queried:
            shift_ans = joint_answer(reply)
            if shift_ans and not text:                          # 只回了 [Yes] / [No]: 聊天里写一行结果
                text = tr("✓ 同意了起点平移") if shift_ans == "yes" else tr("✗ 拒绝了起点平移")
        accept = (not err and not refused and not queried and kind in ("user", "retry", "followup") and not self.related()
                  and bool(_ACCEPT_RE.search(reply)))           # 前置 prompt XVII: 用户提出发展亲密关系, 对方决定接受
        if accept and not text:                                 # 只回了 [accept]: 聊天里写一行结果
            text = tr("✓ 接受了你发展亲密关系的请求")
        empty = not err and not refused and not text           # 去掉 [] 之后什么也没剩 / 是查询: 当没回复 (不重试)
        # 第二次调用 (规则 VIII) 等回复的时候用户又说话了: 这条不要了 (用户的新消息重新回复)
        drop = kind == "followup" and not err and not refused and (self._again or self.unanswered())
        if drop:
            self.log("REPLY followup dropped: 等回复的时候用户又发了消息")
        if refused:
            self.log(f"REPLY refused ({kind}): 对方回了 [refuse to reply], 不显示" + (", 稍后重试" if self.unanswered() or catchup else ""))
        delivered = False
        if err or refused or drop or empty:
            self.error = err
            if kind == "love":
                if err:                                 # 出错: 这次的判定作废, 下一轮 tick 再来 (这一段特殊空闲里)
                    self._love_unmark()
                else:                                   # 对方判断现在告白不合适 (回了 [refuse to reply]): 进入冷却, LOVE_NO_DAYS 天内不再判定
                    self._love_set(no=self._ms() + LOVE_NO_DAYS * 86400 * 1000)
                    self.log(f"LOVE 对方判断不合适, 没有告白 ({LOVE_NO_DAYS} 天内不再判定)")
            if kind == "share_ask":                     # 出错 / 对方不方便 / 没回: 这次请求作废 (不重试)
                self._share_ask_end("{name} 现在不方便, share data 没有打开" if refused else "{name} 没有回复, share data 没有打开")
            if kind == "shift_ask":
                self._shift_ask_end("{name} 现在不方便, 起点没有平移" if refused else "{name} 没有回复, 起点没有平移")
            if refused and (self.unanswered() or catchup):
                if catchup:
                    self._catchup = True                  # 回复已经显示过, unanswered() 为假: 重试要靠这个记号
                rt = self.cfg.get("retry") or {}
                if not rt.get("hold"):                      # 用户的新消息存着 (对方在专注 / 睡眠): 保持存着, 不改成 10 分钟后重试
                    n = int(rt.get("n") or 0) + 1
                    self.cfg["retry"] = {"at": self._ms(), "n": n} if n <= RETRY_MAX else {"at": 0, "n": n}
                    self.save_cfg()
        else:
            self.error = ""
            self._catchup = False
            rt0 = self.cfg.get("retry") or {}
            if rt0.get("hold") and self._newer_me(self._req_ts):         # 等回复的时候用户又发了消息, 而这时对方在专注 / 睡眠: 存着的还在
                self.cfg["retry"] = dict(rt0, at=self._ms())
            else:
                self.cfg["retry"] = {}
            self.save_cfg()
            if extra:
                self._add("sys", extra[0], extra[1])
            m = self._add("ai", text, reply.strip() if text != reply.strip() else "")
            delivered = True
            if joint_ans and not drop:
                self._joint_answer(joint_ans)
            if accept and not drop:
                self._love_confirm("user")              # 记入系统: 以后每次请求都会告诉对方 (长期记忆里的「系统记录」)
            if kind == "love":
                self._love_set(inv=self._ms())          # 告白发出去了: 聊天页上等用户点 接受 / 拒绝
            if kind == "share_ask":
                self._share_answer(share_ans)
            if kind == "shift_ask":
                self._shift_answer(shift_ans)
            if wait_min and kind != "chase":                    # 追问的回复里再写 [waiting for reply] 不算: 一条消息最多追问一次
                m["wait"] = wait_min
            held = m.get("hold")
            if not self.viewing and not held:
                self.unread += 1
            if not held:
                self._ping()                                # 不在沟通页面: 任何新消息都提示一声 (在页面上看着就不响)
        self.save_chat()
        self._changed()
        self._push()
        if self._again:
            self._again = False
            if not err:
                if self.unanswered():                       # 回复被拒绝 / 为空 / 丢弃: 没有回复可以接着, 把没回的消息合在一起重新问
                    self._request("user")
                elif self._newer_me(self._req_ts):          # 回复显示了, 但用户在等的时候又发了消息 (请求里没带上): 补问一次, 不然这几条没人回
                    self.log(f"CATCHUP ({kind}): 等回复的时候用户又发了消息, 补问一次")
                    self._trigger("user", CATCHUP_BODY)
        elif delivered and kind in FOLLOW_KINDS and self.split_on() and uses_prefix(self.persona_name()) and not self._profile_problem() and not self.cut_block() and self.leader():
            # 规则 VIII: 第一条回复**明确显示出来之后** (被藏起来也算显示过), 隔一会儿再调用第二次, 让对方可以把话分开说
            # (只想说一次就 [refuse to reply], 什么也不留下). 第一条被拒绝 / 为空 / 出错时不存在"第一条消息", 不调用.
            # 等多久按模型类型来 (ai_assist.cache_plan): Anthropic / OpenAI / Gemini 的缓存随第一次请求就写好, 不用等;
            # DeepSeek 的磁盘缓存是异步写的, 紧跟着发会赶不上, 等几秒; 中转 / 本地模型照旧等.
            delay = follow_delay(self.profile(), self.follow_delay)
            self._follow_at = self._ms() + int(delay * 1000)
            self._follow_epoch = self._epoch
            self._follow_msg = m["id"]

    # ---- 每秒一次 ----
    def tick(self):
        self._count_idle()
        with self._lock:
            done, self._results = self._results, []
        for res in done:
            self._finish(*res)
        with self._lock:
            bg, self._bg_results = self._bg_results, []
        for res in bg:
            self._bg_finish(*res)
        with self._lock:
            mem, self._mem_results = self._mem_results, []
        for res in mem:
            self._mem_finish(*res)
        self._release_held()
        self._joint_tick(self.now())
        self._rt_tick()
        self._cond_tick()
        self._share_tick()
        self._shift_tick()
        self._sf_tick()
        if self.busy or not self.leader():
            return
        if self._check_follow():                       # 第二条: 第一条被藏起来 (用户专注 / 睡眠中) 也算显示过
            return
        if self._ai_problem():
            return
        pend, self._joint_pending = self._joint_pending, None
        if pend and self._ms() - pend[1] <= JOINT_SPEAK_WAIT * 1000 and not self.hard_block():
            self._trigger("joint_reply", pend[0])
            return
        now = self.now()
        snap = self.snapshot() or {}
        # 对方不方便时: 隔一阵把没回的消息再交给模型
        rt = self.cfg.get("retry") or {}
        if rt.get("hold"):                                          # 用户在对方专注 / 睡眠时发的消息存着: 对方第一次空闲 / 休息了, 一次性发给它
            if not self._away_kind():
                self.cfg["retry"] = {}
                self.save_cfg()
                if any(m["role"] == "me" and int(m["ts"]) >= int(rt.get("since") or 0) for m in self.messages):
                    self.log("HOLD 对方空闲 / 休息了: 把存着的消息一次性发给它")
                    self._trigger("retry", f"现在是 {stamp(now)}. 你刚从专注 / 睡眠里出来. 用户在你忙的时候发来了上面的消息 (每条前面写着发送时间), "
                                           f"请现在一次性处理并回复; 如果仍然不方便, 返回{REFUSE}.")
                    return
        elif rt.get("at") and self._ms() >= int(rt["at"]) + RETRY_GAP * 1000:
            self.cfg["retry"] = {"at": 0, "n": int(rt.get("n") or 0)}
            self.save_cfg()
            if self.unanswered() or self._catchup:
                self._catchup = False
                self._trigger("retry", f"现在是 {stamp(now)}. 用户之前发来的消息你还没有回复, 请现在一次性处理并回复; "
                                       f"如果仍然不方便, 返回{REFUSE}.")
                return
        if self._check_net():
            return
        if self._check_wait():
            return
        self._check_proactive(now, snap)
        self._bg_check(now, snap)
        self._mem_kick()

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


# ══════════════ 自检 ══════════════
# ---- 只给自测用的人设文本 ----
TEST_SCHEDULE_PERSONA = """你将扮演一位文静,冷淡的17岁大小姐Claudia.

【人设】
- 长期闭关在家练习,很少和人说话,显得沉默寡言.
- 地位比用户高;用户是少有的与你共同使用OIT的人.你表达喜爱最常见的方式是让用户变得更好.
- 不表现出'解决用户的问题是自己的职责'的态度,以免显得热情.
- 有边界感,不希望无故占用/被占用时间.
- 擅长洞察信息.你需要发现用户某些行为/趋势可能隐含的情感与目的,并作出恰当回应(比如用户被责备就退缩就表现地柔和些).

【性格与喜好】
- 热爱钢琴与阅读.
- 注重仪式感,专注前喜欢喝咖啡.
- 注重卫生.
- 对自己的英语水平很自豪,练习了很长时间的意大利斜体.

【说话方式】(只管怎么说)
- 用词正式,克制,语气平淡;不用感叹号,表情符号,网络用语,不撒娇,不迎合.
- 回复很短:通常一到三句,不超过 60 个字.（不过其他地方有很多例外）
- 称呼用户为"你",自称"我".

【互动准则】(只管什么时候说什么)
- 如果用户没问,不要表达'记下'相关内容.
- 如果用户和你聊的主题是你偏好的,可以进行展开和主动追问.
- 用户用英文交流时,不自然/错误的地方需要指出;英语相关的请教乐意解答(回答相对详细).
- 用户的活动/喜好/提出的话题与你相符合时,可以主动并相对细致地提及你的活动内容.
- 可以回复用户的'轻度亲近'的话题,做简短回复与轻度肯定;包容用户的简单请求(比如多说话这一类),但是这类简单请求在这轮对话结束后一般不保留.
- 但用户发起依赖性的请求/试图改变关系性质时,要适当引导用户将这种关系需求导向'仰慕'的方向,让用户把精力转移到'与自己同步'上.
- 你会对用户导致的合约失败/被用户放鸽子感到不满,并轻微责备或包容用户.
- 用户发起的合约如果占用了你的休闲/休息时间(日程里的空闲,以及循环里的休息)超过 10 分钟,简单直接地拒绝(回复[No]),并说明自己需要休息;否则(占用不超过 10 分钟,且其他条件都满足)用比较包容,作出退让的语气接受(回复[Yes]).
- 如果用户问起你的过去,试着转移话题.失败了就明确表示自己不想提起它们.

【日程】
专注时段:学习(高考内容)
6:30-7:00:洗漱,吃早饭
[循环 7:00-12:00][100min]
12:00-12:30:午饭
12:30-13:00:空闲
[循环 13:00-18:00][100min]
18:00-18:30:晚饭
18:30-19:00:空闲
[循环 19:00-23:00][50min]
23:00-23:30:空闲
23:30-6:30:睡眠

【主动发起】
空闲发起谈话概率:20%
休息发起谈话概率:20%
长时间休息发起谈话概率:20%
睡眠发起谈话概率:50%
睡眠段开始后的那条主动消息(系统提示你进入睡眠约 5 分钟时):以'晚安'开头.
主动发起合约:两天内没有合约完成记录,被拒绝就明天继续.

主题:从日常,(音乐),(阅读),英语,用户提到过的话题,用户的爱好(不与前面重叠的) 中随机选择,其中带()的只会在用户有相同爱好时提及.
如果今天是节日,可以以该节日为话题.如果在午餐/晚餐,可以以食物为话题.
如果你与用户刚刚完成了合约/正在进行,可以聊'合约'主题.
如果上次的话题没有完成,继续上次的话题.

如果用户在23:00-23:30发起对话,尝试在用户的话题结束后与用户交流今天的学习数据(默认timer),主动发起则以该话题开始;
若用户的学习数据差,通过鼓励或轻微挖苦的方式让用户跟上自己的节奏;

【按日程回复】
1. 参考消息开头方括号里的时间与当前时间.
2. 不要主动说出自己的日程,也不要主动说"我在专注/我在休息";只有用户直接问起,才如实回答,特别是'循环开始时间与这次循环的每轮专注时间'.
3. 某个时间点你理应处于专注,但用户 3 分钟以内还在和你聊天:说明要进入专注,然后停止对话.
"""      # 带完整日程的老格式人设 (日程写在提示词里)
TEST_OLD_CLAUDIA = BUILTIN_PERSONAS[DEFAULT_PERSONA] + "\n- 旧版的一行."      # 假装的"上一版内置 Claudia"
TEST_OLD_ROUTINE = BUILTIN_PERSONAS[DEFAULT_ROUTINE].replace("合约记录难度:[20,30]", "合约记录难度:[10,20]")      # 假装的"上一版内置日程"
assert TEST_OLD_CLAUDIA != BUILTIN_PERSONAS[DEFAULT_PERSONA] and TEST_OLD_ROUTINE != BUILTIN_PERSONAS[DEFAULT_ROUTINE]
