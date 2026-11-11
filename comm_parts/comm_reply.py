# -*- coding: utf-8 -*-
"""回复解析: 对方回复里的 [cut off] / [apply for network] / [waiting for reply] 等标记的清理与分钟数 (从 comm.py 拆出; 纯函数)."""

import re


CUT_DEFAULT_MIN = 10                  # [cut off] 后面没写分钟数时切断多久

CUT_MAX_MIN = 24 * 60                 # 一次最多切断多久

NET_DEFAULT_MIN = 5                   # [apply for network] 后面没写分钟数时开通多久

NET_MAX_MIN = 30                      # 一次最多开通多久

_WAIT_RE = re.compile(r"\[\s*waiting\s+for\s+reply\s*\](?:\s*(?:[:：]\s*)?\[\s*(\d+(?:\.\d+)?)\s*\]|\s*[:：]\s*\[?\s*(\d+(?:\.\d+)?)\s*\]?|\s*[:：])?"
                      r"(?:[ \t]*(?:minutes?|mins?|分钟))?", re.I)

_BRACKET_RE = re.compile(r"[\[［][^\[\]［］\n]*[\]］](?:[ \t]*[:：][ \t]*\d+(?:\.\d+)?)?")       # 空的 [] 也算; 紧跟的 ":1" 这样的数字一起去掉

WAIT_MAX_MIN = 1440

# 去掉 [] 之后留下的空壳: 对方常把标记写在行内代码 / 引号 / 括号里 (`[waiting for reply]:[1]`), 剩下 `` “” 「」 () 之类的就不显示了
_EMPTY_WRAP_RE = re.compile(r"`[ \t]*`|“[ \t]*”|「[ \t]*」|『[ \t]*』|\([ \t]*\)|（[ \t]*）|\"[ \t]*\"|'[ \t]*'")

_CUT_RE = re.compile(r"[\[［]\s*cut\s*off\s*[\]］]", re.I)

_CUT_N_RE = re.compile(r"\s*(?:[:：]\s*)?[\[［]?\s*(\d+(?:\.\d+)?)")

def cut_minutes(reply: str):
    """对方回复里的 [cut off][N] → 切断多少分钟 (没写 N = CUT_DEFAULT_MIN, 最多 CUT_MAX_MIN, 最少 1); 没有 [cut off] = None."""
    s = str(reply or "")
    m = _CUT_RE.search(s)
    if not m:
        return None
    n = _CUT_N_RE.match(s, m.end())
    try:
        minutes = float(n.group(1)) if n else float(CUT_DEFAULT_MIN)
    except ValueError:
        minutes = float(CUT_DEFAULT_MIN)
    return min(float(CUT_MAX_MIN), max(1.0, minutes))

_NET_RE = re.compile(r"[\[［]\s*apply\s+for\s+network\s*[\]］]", re.I)

def net_minutes(reply: str):
    """对方回复里的 [apply for network][N] → 申请开通多少分钟 (没写 N = NET_DEFAULT_MIN, 最少 1, 最多 NET_MAX_MIN); 没有这个标记 = None."""
    s = str(reply or "")
    m = _NET_RE.search(s)
    if not m:
        return None
    n = _CUT_N_RE.match(s, m.end())
    try:
        minutes = float(n.group(1)) if n else float(NET_DEFAULT_MIN)
    except ValueError:
        minutes = float(NET_DEFAULT_MIN)
    return min(float(NET_MAX_MIN), max(1.0, minutes))

def clean_reply(reply: str) -> tuple:
    """对方的回复 → (聊天里显示的文字, 等用户回复的分钟数 or None). 去掉 [waiting for reply]:[N] 和所有别的 [...]; N 不是 0 < N <= 1440 就当没写."""
    reply = str(reply or "")
    m = _WAIT_RE.search(reply)
    minutes = None
    if m and (m.group(1) or m.group(2)):
        n = float(m.group(1) or m.group(2))
        minutes = n if 0 < n <= WAIT_MAX_MIN else None
    text = _BRACKET_RE.sub("", _WAIT_RE.sub("", reply))
    prev = None
    while prev != text:                                   # 一层一层剥 (括号里的空代码 ...)
        prev, text = text, _EMPTY_WRAP_RE.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, minutes

# ---- 全角符号 → 半角符号 (1.0.408): 名字是 Claudia 的人设, 显示给用户前自动转 ----
HALFWIDTH_SYMBOLS = {
    "。": ".", "、": ",", "【": "[", "】": "]", "「": '"', "」": '"', "『": "'", "』": "'",
    "“": '"', "”": '"', "‘": "'", "’": "'", "《": "<", "》": ">", "〈": "<", "〉": ">",
    "〔": "[", "〕": "]", "〖": "[", "〗": "]", "〘": "[", "〙": "]", "〝": '"', "〞": '"', "〟": '"',
    "〜": "~", "–": "-", "—": "-", "―": "-", "‒": "-", "−": "-", "‐": "-", "‑": "-",
    "‥": "..", "…": "...", "　": " ", "｡": ".", "｢": '"', "｣": '"', "､": ",", "･": ".",
}

def to_halfwidth(text: str) -> str:
    """全角符号 → 半角符号: 全角 ASCII (U+FF01-FF5E) 按码位换, 全角空格换成半角空格, 中文标点按表换."""
    s = str(text or "")
    if not s:
        return s
    s = s.replace("……", "...").replace("——", "--")          # 成对的先换 (…… 是两个 …)
    out = []
    for ch in s:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            out.append(" ")
        else:
            out.append(HALFWIDTH_SYMBOLS.get(ch, ch))
    return "".join(out)
