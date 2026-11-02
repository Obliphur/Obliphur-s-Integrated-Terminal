# -*- coding: utf-8 -*-
"""
沟通 (MECHANICS 24): 和一个 AI 人设聊天
──────────────────────────────────────
  · 模型接口沿用名单建议的那份 (ai_assist.AIConfig, 设置 → 沟通 里填); API 本身没有记忆,
    每次请求都把最近的聊天记录一起发过去.
  · 人设 prompt 只能从外部来: 放进人设文件夹 (OIT_personas, 和数据文件夹并排, 不加密), 界面里只能选;
    选哪个不算作弊修改, 没有等待期. 文件夹里的 README.md 写着怎么写人设.
  · 发给模型的 system = 前置 prompt (日程 / 休息函数 / 回复规则) + 人设.
  · 默认附带的信息: 整点记录, 计时器状态. 同一类信息 30 分钟内发过一遍就不重复发.
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
RESEND_GAP = 30 * 60                  # 同一类信息多久之内不重复发
PROACTIVE_MIN_REST = 20 * 60          # 休息超过这么久才会主动发消息
PROACTIVE_DELAY = (3 * 60, 5 * 60)    # 休息开始后多久主动发
RETRY_GAP = 10 * 60                   # 对方不方便回复时, 多久再把消息交给模型
RETRY_MAX = 12
HISTORY_SENT = 60                     # 每次请求最多带多少条聊天记录
KEEP = 400                            # 本地最多存多少条
MAX_PERSONA = 20000
PERSONA_EXT = (".txt", ".md")
CHAT_TOKENS = 1000
CHAT_TEMPERATURE = 0.8
WEEKDAY = "一二三四五六日"

DEFAULTS = {"persona": "", "send_hour": True, "send_timer": True, "show_avatar": False,
            "avatar_me": "", "avatar_ai": "", "last_sent": {}, "pro_key": ""}


# ══════════════ 前置 prompt 与 README ══════════════
def rest_function_text() -> str:
    g = config.REST_GAIN
    return (f"专注 t 分钟后建议休息 F(t) 分钟: t≤15 时 F={g:g}·t/6; 15<t<150 时 "
            f"F={g:g}·(7/14850·t²+407/1485·t−170/99); t≥150 时 F={g:g}·(50+5/ln1.2·ln(t/150)); "
            f"例如专注 50 分钟休息约 {config.compute_F(50):.0f} 分钟")


def prefix_prompt() -> str:
    return (
        "你需要回复来自自律软件OIT用户的信息,根据以下设定回复\n"
        "I.设定上你也是OIT的用户,你需要根据用户为你设定的人设安排自己的日程与行动,你不能出戏(比如强调自己是AI)\n"
        f"专注时间-休息时间函数如下:[{rest_function_text()}],"
        "如果没有强调,默认你的每轮专注时间总是50min,并且一休息完就继续专注.\n"
        f"II.根据日程,你在专注/外出/睡觉和其他不能随时回复的时段是不能回复消息的:返回{REFUSE}则OIT不会显示你的回复,"
        "你到下一次空闲时需要一次性处理用户发来的消息.\n"
        "III.系统有的时候会要求你主动发消息,你需要根据人设自己找话题.\n"
        "IV.根据你的专注时间段 你可能需要主动结束话题.你可以稍微延迟你的进入专注的时间点,不超过5min.\n"
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


def _hm(minutes: float) -> str:
    m = int(round(max(0.0, minutes)))
    return f"{m // 60} 小时 {m % 60} 分钟" if m >= 60 else f"{m} 分钟"


STATE_TEXT = {"focus": "专注中", "break": "休息中", "idle": "空闲", "sleep": "睡觉中", "out": "外出中"}


def timer_line(snap: dict) -> str:
    st = snap.get("state") or "idle"
    left = f"{_hm(snap.get('rest_left_min', 0))}" if st == "break" else "无"
    cur = _hm(snap.get("focus_cur_min", 0)) if st == "focus" else "0 分钟(未在专注)"
    return (f"目前状态: {STATE_TEXT.get(st, st)}; 剩余休息时间: {left}; "
            f"今日总专注时长: {_hm(snap.get('focus_total_min', 0))}; 目前这轮专注时长: {cur}")


def api_messages(msgs: list) -> list:
    """本地聊天记录 → API 的 messages: 连续同角色的合并, 以 user 开头."""
    out = []
    for m in msgs[-HISTORY_SENT:]:
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
    """聊天记录, 设置, 发请求 (线程), 主动发消息, 对方不方便时的重试. 界面线程每秒调一次 tick()."""

    def __init__(self, data_dir: str, personas: str, now=datetime.now, chat_fn=None):
        self.dir = data_dir
        self.personas = personas
        self.now = now
        self.chat_fn = chat_fn or ai_assist.chat_messages
        self.cfg_path = os.path.join(data_dir, "comm_config.json")
        self.chat_path = os.path.join(data_dir, "comm_chat.json")
        self.ai = ai_assist.shared_config(os.path.join(data_dir, "ai_config.json"),
                                          template=ai_assist.DEFAULT_TEMPLATE)
        self.cfg = dict(DEFAULTS)
        self.messages = []
        self.version = 0              # 聊天记录 / 状态变了就加一, 页面据此重画
        self.unread = 0
        self.viewing = False
        self.busy = False
        self.error = ""
        self.hour_lines = lambda: []   # 今天填过的整点记录 ["09:00 写代码", …]; app 接上
        self.snapshot = lambda: {}     # 计时器状态; app 接上
        self.data_fn = lambda sel: ""  # 统计 / 日历 / 合约词条的数据 (comm_data.build); app 接上
        self.fog = lambda: False       # 合约黑雾中: 不发数据
        self.on_change = None          # 界面线程里调用
        self._lock = threading.Lock()
        self._wlock = threading.Lock()
        self._pending = {}
        self._writing = False
        self._results = []
        self._again = False
        self._retry_at = None
        self._retries = 0
        self._epoch = 0               # clear() 加一: 清空之前发出的请求, 回复到了也丢掉
        self._next_id = 1
        self.load()
        ensure_personas(self.personas)
        last = self.messages[-1] if self.messages else None
        if last and last["role"] == "me" and self._age(last) < 6 * 3600:
            self._retry_at = self.now() + timedelta(seconds=60)    # 上次关程序时还没收到回复

    # ---- 存取 ----
    def load(self):
        d = config.load_json_file(self.cfg_path, {})
        if isinstance(d, dict):
            for k, default in DEFAULTS.items():
                v = d.get(k)
                if isinstance(v, type(default)):
                    self.cfg[k] = v
        d = config.load_json_file(self.chat_path, {})
        msgs = d.get("messages") if isinstance(d, dict) else None
        if isinstance(msgs, list):
            self.messages = [m for m in msgs if isinstance(m, dict) and m.get("role") in ("me", "ai", "sys")]
        self._next_id = max([int(m.get("id") or 0) for m in self.messages] + [0]) + 1

    # 写盘 (加密 + fsync) 放在后台线程: 发送 / 收到消息时界面线程不等磁盘, 不卡. 连续几次保存合并成一次.
    def _write_async(self, kind: str, payload):
        with self._wlock:
            self._pending[kind] = payload
            if self._writing:
                return
            self._writing = True
        threading.Thread(target=self._write_loop, daemon=True).start()

    def _write_loop(self):
        while True:
            with self._wlock:
                pending, self._pending = self._pending, {}
                if not pending:
                    self._writing = False
                    return
            if "cfg" in pending:
                config.save_json_file(self.cfg_path, pending["cfg"])
            if "chat" in pending:
                config.save_json_file(self.chat_path, pending["chat"])

    def flush(self, timeout: float = 5.0):
        """等后台的写盘做完 (关程序前, 以及自检)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._wlock:
                if not self._writing and not self._pending:
                    return
            time.sleep(0.01)

    def save_cfg(self):
        self._write_async("cfg", json.loads(json.dumps(self.cfg)))

    def save_chat(self):
        self.messages = self.messages[-KEEP:]
        self._write_async("chat", {"messages": [dict(m) for m in self.messages]})

    def set_cfg(self, **kw):
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

    def _age(self, m) -> float:
        try:
            return (self.now() - datetime.fromisoformat(m["at"])).total_seconds()
        except (KeyError, ValueError, TypeError):
            return 1e9

    # ---- 状态 ----
    def persona_name(self) -> str:
        n = self.cfg.get("persona") or ""
        return n if n in list_personas(self.personas) else ""

    def problem(self) -> str:
        """现在不能聊天的原因 ("" = 可以)."""
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

    # ---- 信息块 ----
    def _info(self, snap: dict, now: datetime) -> str:
        """还没到重复间隔的信息不发; 返回要附带的文字 ("" = 没有)."""
        sent = self.cfg.get("last_sent") or {}
        parts = []

        def due(key):
            try:
                return (now - datetime.fromisoformat(sent[key])).total_seconds() >= RESEND_GAP
            except (KeyError, ValueError, TypeError):
                return True

        if self.cfg.get("send_hour") and due("hour"):
            lines = list(self.hour_lines() or [])
            if lines:
                parts.append("整点记录: " + "; ".join(lines))
                sent["hour"] = now.isoformat(timespec="seconds")
        if self.cfg.get("send_timer") and due("timer") and snap:
            parts.append("计时器: " + timer_line(snap))
            sent["timer"] = now.isoformat(timespec="seconds")
        self.cfg["last_sent"] = sent
        return "\n".join(parts)

    def _compose(self, now: datetime, body: str, snap: dict) -> str:
        info = self._info(snap, now)
        return f"[{stamp(now)}]\n" + (f"(OIT 附带信息)\n{info}\n" if info else "") + body

    # ---- 发消息 ----
    def _add(self, role: str, text: str, api: str = "") -> dict:
        now = self.now()
        m = {"id": self._next_id, "role": role, "text": text, "at": now.isoformat(timespec="seconds")}
        if api:
            m["api"] = api
        self._next_id += 1
        self.messages.append(m)
        return m

    def send(self, text: str) -> str:
        """用户发一条. 返回 "" = 已发出, 否则是不能发的原因."""
        text = str(text or "").strip()
        if not text:
            return "empty"
        if text.lower() == "/clear":
            self.clear()
            return ""
        why = self.problem()
        if why:
            return why
        now = self.now()
        snap = self.snapshot() or {}
        self._add("me", text, self._compose(now, text, snap))
        self.error = ""
        self._retries = 0
        self._retry_at = None
        self.save_cfg()
        self.save_chat()
        self._changed()
        self._request("user")
        return ""

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
        self._add("me", shown, self._compose(now, f"(用户发来的数据)\n{data}" + (f"\n\n{text}" if text else ""), self.snapshot() or {}))
        self.error = ""
        self._retries = 0
        self._retry_at = None
        self.save_cfg()
        self.save_chat()
        self._changed()
        self._request("user")
        return ""

    def _trigger(self, kind: str, body: str):
        now = self.now()
        self._add("sys", body, self._compose(now, body, self.snapshot() or {}))
        self.save_cfg()
        self._request(kind)

    def _request(self, kind: str):
        if self.busy:
            self._again = True
            return
        profile = self.ai.profile()
        system = prefix_prompt() + read_persona(self.personas, self.persona_name())
        msgs = api_messages(self.messages)
        trig = self.messages[-1]["id"] if kind != "user" else 0
        epoch = self._epoch
        self.busy = True
        self._changed()

        def work():
            try:
                reply = self.chat_fn(profile, system, msgs, max_tokens=CHAT_TOKENS,
                                     temperature=CHAT_TEMPERATURE)
                res = (kind, trig, reply, "", epoch)
            except ai_assist.AIError as e:
                res = (kind, trig, "", e.message, epoch)
            except Exception as e:      # noqa: BLE001
                res = (kind, trig, "", f"{type(e).__name__}", epoch)
            with self._lock:
                self._results.append(res)
        threading.Thread(target=work, daemon=True).start()

    def _finish(self, kind: str, trig: int, reply: str, err: str, epoch: int = 0):
        self.busy = False
        if epoch != self._epoch:                        # 发出之后清空过聊天记录: 这条回复不要了
            self._again = False
            self._changed()
            return
        now = self.now()
        if err:
            self.error = err
            if trig:                                    # 失败的主动 / 重试请求不留下
                self.messages = [m for m in self.messages if m["id"] != trig]
        elif is_refusal(reply):
            self.error = ""
            if trig:
                self.messages = [m for m in self.messages if m["id"] != trig]
            if self.unanswered() and self._retries < RETRY_MAX:
                self._retries += 1
                self._retry_at = now + timedelta(seconds=RETRY_GAP)
        else:
            self.error = ""
            self._retries = 0
            self._retry_at = None
            self._add("ai", reply.strip())
            if not self.viewing:
                self.unread += 1
        self.save_chat()
        self._changed()
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
        if self.busy or self.problem():
            return
        now = self.now()
        snap = self.snapshot() or {}
        # 对方不方便时: 隔一阵把没回的消息再交给模型
        if self._retry_at and now >= self._retry_at:
            self._retry_at = None
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
                    else:
                        self.save_cfg()

    def clear(self):
        """清空聊天记录 (界面上的按钮, 或者在聊天框里发 /clear): 以后不再显示, 也不再发给模型.
        之前附带过的信息也一起没了, 所以"30 分钟内不重复"的记录清零."""
        self.messages = []
        self.unread = 0
        self._retry_at = None
        self._retries = 0
        self._epoch += 1
        self.error = ""
        self.cfg["last_sent"] = {}
        self.save_cfg()
        self.save_chat()
        self._changed()


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
    assert REFUSE in p and "默认你的每轮专注时间总是50min" in p and p.rstrip().endswith("以下是用户设定的人设:")
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
    c.hour_lines = lambda: ["08:00 起床", "09:00 写代码"]
    snap = {"state": "break", "rest_left_min": 12.4, "focus_total_min": 200, "focus_cur_min": 0,
            "break_start": clock[0], "break_total_s": 1800}
    c.snapshot = lambda: snap
    assert c.send("hi") == "还没有选人设"
    c.set_cfg(persona="小夏.txt")
    script.append("早!")
    assert c.send("早") == "" and c.busy
    wait(c)
    system, msgs = calls[-1]
    assert system.startswith(prefix_prompt()) and system.endswith("每天 8 点到 12 点上课.")
    assert msgs[0]["role"] == "user" and "2026-11-02 周一 09:00" in msgs[0]["content"]
    assert "整点记录: 08:00 起床; 09:00 写代码" in msgs[0]["content"] and "剩余休息时间: 12 分钟" in msgs[0]["content"]
    assert "今日总专注时长: 3 小时 20 分钟" in msgs[0]["content"] and msgs[0]["content"].endswith("早")
    assert [m["role"] for m in c.visible()] == ["me", "ai"] and c.unread == 1
    # 30 分钟内同类信息不重复
    clock[0] += timedelta(minutes=29)
    script.append("嗯")
    c.send("在吗")
    wait(c)
    assert "OIT 附带信息" not in calls[-1][1][-1]["content"] and "整点记录" not in calls[-1][1][-1]["content"]
    # 满 30 分钟又发
    clock[0] += timedelta(minutes=2)
    script.append("嗯")
    c.send("还在")
    wait(c)
    assert "整点记录" in calls[-1][1][-1]["content"] and "计时器" in calls[-1][1][-1]["content"]
    # 关掉某一类就不发
    c.set_cfg(send_hour=False, last_sent={})
    clock[0] += timedelta(hours=1)
    script.append("嗯")
    c.send("再来")
    wait(c)
    last = calls[-1][1][-1]["content"]
    assert "整点记录" not in last and "计时器" in last

    # 不方便: 不显示, 消息留着, 过 10 分钟一次性交给模型
    c.set_cfg(send_hour=True, send_timer=True, last_sent={})
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

    # /clear: 之前的记录不再显示, 也不再发给模型; 信息重新附带; 清空前发出的请求回复到了也丢掉
    c.snapshot = lambda: snap
    c.hour_lines = lambda: ["09:00 写代码"]
    c.set_cfg(send_hour=True, send_timer=True)
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
    assert len(msgs) == 1 and "我先睡了" not in msgs[0]["content"] and "整点记录" in msgs[0]["content"]
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
    c.flush()
    return "comm selftest OK"


if __name__ == "__main__":
    print(_selftest())
