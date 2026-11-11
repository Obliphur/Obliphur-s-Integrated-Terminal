# -*- coding: utf-8 -*-
"""沟通页里"📎 数据"能发出去的内容: 今天的整点记录 / 专注计时, 日历 (选类别), 统计 (月数据) 的几页; 还有联合行动的邀请 (合约词条 + 开始 / 结束时间).
手机版: core/CommData.kt.

    python comm/comm_data.py     自检 (用假数据)
"""

from datetime import datetime, timedelta

import contract as C

STATS_KEYS = (("log", "整点记录"), ("focus", "专注计时"), ("net", "联网理由"), ("window", "窗口"), ("contracts", "合约记录"))
STATS_NAME = "统计(月数据)"
TODAY_KEYS = (("log", "整点记录(今天)"), ("timer", "专注计时(今天)"))      # 今天的整点记录 / 计时器 (对方查询的 [log] / [timer] 也是这两块)
CAL_KEYS = (("study", "专注"), ("break", "休息"), ("idle", "空闲"), ("sleep", "睡眠"))
WEEKDAY = "一二三四五六日"
TOP = 30
MAX_CHARS = 60000          # 一次最多发多少字 (词条库约 4000 字, 统计每类最多 30 项)


def label(sel: dict) -> str:
    """聊天里显示的那一行."""
    parts = []                                  # 顺序和 📎 窗口里一样: 今天 → 日历 → 统计(月数据)
    today = dict(TODAY_KEYS)
    parts += [today[k] for k in today if k in sel.get("today", [])]
    cal = dict(CAL_KEYS)
    cl = [cal[k] for k in sel.get("cal", []) if k in cal]
    if cl:
        parts.append("日历·" + "、".join(cl))
    names = dict(STATS_KEYS)
    st = [names[k] for k in sel.get("stats", []) if k in names]
    if st:
        parts.append(STATS_NAME + "·" + "、".join(st))
    return "📎 " + " / ".join(parts) if parts else ""


def _top(d: dict, unit: str = "h", n: int = TOP) -> str:
    rows = sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))
    total = sum(d.values())
    out = []
    for k, v in rows[:n]:
        out.append(f"  {k or '空白'}: {v / 3600:.1f} h" if unit == "h" else f"  {k or '空白'}: {v / 60:.0f} 分钟")
    if len(rows) > n:
        out.append(f"  其他 {len(rows) - n} 项: {sum(v for _k, v in rows[n:]) / 3600:.1f} h")
    out.append(f"  合计: {total / 3600:.1f} h")
    return "\n".join(out)


def term_text(tid: str) -> str:
    t = C.BY_ID.get(tid)
    return f"{t[3].split(':', 1)[-1]} (等级{t[2]}): {t[4]}" if t else tid


def _record(rec: dict) -> str:
    rows = C.record_rows(rec)
    head = f"危机等级 {C.level_label(int(rec.get('level') or 0), int(rec.get('orig_level') or rec.get('level') or 0))}"
    body = "; ".join(C.BY_ID[tid][3].split(":", 1)[-1] + ("(已失效)" if void else "") for _lv, tid, void, _o in rows)
    return f"  {head}: {body}"


def _req_name(req: str) -> str:
    """组合的一条要求: 词条 id 或词条系列 (写成系列里第一档的名字, 去掉罗马数字)."""
    if req in C.BY_ID:
        return C.BY_ID[req][3].split(":", 1)[-1]
    for t in C.TERMS:
        if t[1] == req:
            return t[3].split(":", 1)[-1].rsplit("-", 1)[0] + "(任意一档)"
    return req


def numbered_library() -> str:
    """对方发起联合行动时看到的词条库: 每个词条前面有编号 (= 词条在 contract.TERMS 里的位置 + 1), 对方回编号选词条."""
    lines = ["【合约词条库】"]
    for i, t in enumerate(C.TERMS, 1):
        kind, sep, name = t[3].partition(":")
        if not sep:
            kind, name = "特殊", kind
        lines.append(f"{i}. {name}({kind}, 等级{t[2]}): {t[4]}".replace("\n", " "))
    lines.append("\n【词条组合】(组合的要求都选着才生效)")
    for c in C.COMBOS:
        lines.append(f"- {c['name']}: 需要 " + " + ".join(_req_name(r) for r in c["reqs"]) + f"; 效果: {c['desc']}".replace("\n", ", "))
    return "\n".join(lines)


def library(contract=None) -> str:
    """整个词条库和组合. contract=None: 参考模板 (对方查询 [contract] 得到的: 只有词条和组合本身, 不标用户选了哪些, 具体选了什么由对方自己编);
    给了 contract: 标出哪些被用户选择 / 激活了 (合约进行中 = 激活的, 没开始 = 选好的(还没生效)), 📎 附件用."""
    active = bool(contract and contract.active)
    selected = [] if contract is None else (list(contract.terms) if active else list(contract.draft))
    tag = "已激活" if active else "已选择"
    lost = set(contract.lost) if active else set()
    if contract is None:
        lines = [f"【合约词条库】{NOTE}\n(下面是全部词条和组合; 用户具体选了哪些、合约进行得怎么样, 请根据人设自己编)"]
    else:
        head = (f"合约进行中, 危机等级 {getattr(contract, 'level_text', '')}" if active else
                ("合约没有开始, 选好的词条还没生效" if selected else "现在没有进行中的合约, 也没有选好的词条"))
        lines = [f"【合约词条库】{head}。词条后面的【{tag}】= 用户选的; 【已失效】= 被违反到失效。"]
    for t in C.TERMS:
        mark = f"【{tag}】" if t[0] in selected else ("【已失效】" if t[0] in lost else "")
        kind, _sep, name = t[3].partition(":")
        if not _sep:
            kind, name = "特殊", kind
        lines.append(f"- {name}({kind}, 等级{t[2]}){mark}: {t[4]}".replace("\n", " "))
    lines.append("\n【词条组合】(组合的要求都选着才生效)")
    for c in C.COMBOS:
        got = [r for r in c["reqs"] if C.combo_done({"reqs": (r,)}, selected)]
        done = C.combo_done(c, selected)
        mark = f"【{tag}·已凑齐】" if done else (f"(已选 {len(got)}/{len(c['reqs'])})" if got else "")
        lines.append(f"- {c['name']}{mark}: 需要 " + " + ".join(_req_name(r) + ("✓" if r in got else "") for r in c["reqs"])
                     + f"; 效果: {c['desc']}".replace("\n", ", "))
    return "\n".join(lines)


# ---- 对方可以查询的模块 (沟通 24.2.1): 回复 [refuse to reply][模块名] 得到该模块的数据内容和格式 ----
MODULE_TAGS = ("contract", "calendar", "stats", "timer", "log", "net", "rules", "settings", "reset", "prescript")
FOG_TEXT = "(黑雾中, 暂时无法提供)"
NOTE = "(格式模板: <尖括号> 里是占位符, 不是用户的数据; 请参照这个格式, 根据人设自己填写具体内容)"
TEMPLATES = {
    "timer": "【计时器】" + NOTE + r"""
  今天的每一段 (按时间顺序):
  <HH:MM>-<HH:MM> <专注 / 休息 / 睡眠> (<X 小时 Y 分钟>)
  <HH:MM>-<HH:MM> <专注 / 休息 / 睡眠> (<X 小时 Y 分钟>)
  (最后一行可能是还在进行的一段: <HH:MM>-现在 <专注 / 休息 / 睡眠> (<X 分钟>); 没有记录就写"今天还没有记录")
  总计:
  目前状态: <专注中 / 休息中 / 空闲 / 睡觉中 / 外出中>
  剩余休息时间: <X 小时 Y 分钟 / 无>
  今日总专注时长: <X 小时 Y 分钟>
  目前这轮专注时长: <X 分钟 / 0 分钟(未在专注)>""",
    "log": "【整点记录·今天】" + NOTE + r"""
  <HH>:00 <这个小时做了什么>
  <HH>:00 <这个小时做了什么>
  (一行一个小时, 没有记录的小时不写)""",
    "calendar": "【日历·<专注 / 休息 / 空闲 / 睡眠> <YYYY-MM>】" + NOTE + r"""
  <YYYY-MM-DD>: <X.X> h
  <YYYY-MM-DD>: <X.X> h
  (只列有记录的日子; 休息 / 空闲 / 睡眠只有最近 7 天)
  合计: <X.X> h""",
    "stats": "【统计·整点记录 <YYYY-MM>】" + NOTE + r"""
  <活动名称>: <X.X> h
  <活动名称>: <X.X> h
  合计: <X.X> h

【统计·联网理由 <YYYY-MM>】
  <理由>: <X> 分钟
  合计: <X.X> h

【统计·窗口 <YYYY-MM>】
  <程序名>: <X.X> h
  合计: <X.X> h

【统计·专注计时 最近 7 天】
  <MM-DD> 周<一~日>: 专注 <X> 分钟, 休息 <X> 分钟

【统计·合约记录 最近完成的】
  危机等级 <N>: <词条名>; <词条名>""",
    "net": "【联网】" + NOTE + r"""
  状态: <联网中 / 申请中 (冷静期) / 离线>
  今日额度: 已用 <X 小时 Y 分钟> / 共 <X 小时 Y 分钟>
  断网方式: <断网方式的名字>

【统计·联网理由 <YYYY-MM>】
  <理由>: <X> 分钟
  合计: <X.X> h""",
    "rules": "【规则】" + NOTE + r"""
  过滤名单模式: <黑名单模式 / 白名单模式>
  白名单 · 网站 (<数量>): <域名>、<域名> …
  白名单 · 应用 (<数量>): <程序名>、<程序名> …
  黑名单 · 网站 (<数量>): <域名>、<域名> …
  黑名单 · 应用 (<数量>): <程序名>、<程序名> …
  (空的名单写"空"; 每张最多列 40 条)""",
    "prescript": "【指令】" + NOTE + r"""
  <结束的时间> 指令 <类型>: <指令内容> → <结果>
  (都市之意主题独占的模块; 最近结束的几条, 没有就写"还没有结束过的指令")""",
}


def template(tag: str, settings_rows: list = None) -> str:
    """对方查询 [模块名] 得到的参考模板 (沟通 24.2.1): 只有格式和占位符, 没有用户的真实数据 (所以黑雾中也能给), 具体内容由对方按人设自己填写.
    settings_rows: [(页, 名称)] (设置页的结构, 值写成 <值>)."""
    if tag == "reset":
        return RESET_TEXT
    if tag == "contract":
        return library(None)
    if tag == "settings":
        out = ["【设置】" + NOTE]
        page = None
        for pg, name in settings_rows or []:
            if pg != page:
                out.append(f" {pg}:")
                page = pg
            out.append(f"  {name}: <值>")
        return "\n".join(out)
    return TEMPLATES.get(tag, "")
STATE_TEXT = {"focus": "专注中", "break": "休息中", "idle": "空闲", "sleep": "睡觉中", "out": "外出中"}
RESET_TEXT = (
    "【重置】(OIT 的 重置 页)\n"
    "  需要输入 RESET 再确认一次. 重置会: 解除网络限制 (重新启用网卡, 关闭过滤代理, 还原系统代理); 请每台绑定的设备也清空数据 (绑定不解除);\n"
    "  删除整个数据目录: 学习记录, 整点记录, 统计, 联网额度与日志, 名单, 设置, AI 配置, 聊天记录全部删除; 进行中的专注不记录; 不留任何记录.\n"
    "  程序随即自动重新打开, 像第一次安装: 设置都是默认值, 名单为空, 重新开始 72 小时配置期.")


def _hm(minutes: float) -> str:
    m = int(round(max(0.0, minutes)))
    return f"{m // 60} 小时 {m % 60} 分钟" if m >= 60 else f"{m} 分钟"


KIND_TEXT = {"study": "专注", "break": "休息", "sleep": "睡眠"}


def interval_lines(intervals, now: datetime = None, live_word: str = "现在") -> list:
    """今天每一段专注 / 休息 / 睡眠的持续区间, 按开始时间排好: [(kind, 开始, 结束)] → ["HH:MM-HH:MM 专注 (X 小时 Y 分钟)"].
    结束不早于现在的 (还在进行的一段) 写成 "HH:MM-现在"; live_word 为空时写成此刻的时间 (要存下来的记录用, 日志的专注时间段)."""
    now = now or datetime.now()
    out = []
    for kind, a, b in sorted(intervals or [], key=lambda x: (x[1], x[2])):
        name = KIND_TEXT.get(kind)
        if not name or b <= a:
            continue
        live = b >= now
        end_text = (live_word or f"{now:%H:%M}") if live else f"{b:%H:%M}"
        out.append(f"{a:%H:%M}-{end_text} {name} ({_hm(((now if live else b) - a).total_seconds() / 60)})")
    return out


def timer_text(snap: dict, intervals=None, now: datetime = None) -> str:
    """【计时器】: 先按时间顺序列出今天的每一段休息 / 专注 / 睡眠的持续区间 (intervals), 最后才是总的数据:
    目前状态, 剩余休息时间, 今日总专注时长, 目前这轮专注时长. intervals = None (调用方不提供) 时只有总的数据."""
    if not snap:
        return "【计时器】\n  " + FOG_TEXT
    st = snap.get("state") or "idle"
    left = _hm(snap.get("rest_left_min", 0)) if st == "break" else "无"
    cur = _hm(snap.get("focus_cur_min", 0)) if st == "focus" else "0 分钟(未在专注)"
    head = "【计时器】\n"
    if intervals is not None:
        lines = interval_lines(intervals, now)
        head += ("  今天的每一段 (按时间顺序):\n" + ("\n".join("  " + x for x in lines) if lines else "  今天还没有记录")
                 + "\n  总计:\n")
    return (head + "  目前状态: " + STATE_TEXT.get(st, st) + "\n  剩余休息时间: " + left +
            "\n  今日总专注时长: " + _hm(snap.get("focus_total_min", 0)) + "\n  目前这轮专注时长: " + cur)


# ---- 联合行动 (MECHANICS 24.9): 邀请对方一起完成一次合约 ----
CONTRACT_RULES = (
    "【合约的基础规则】合约是 OIT 的独占功能: 用户自选一些'词条'(减益), 让 OIT 变得更严苛, 然后开始行动.\n"
    "- 初始时长 3 小时 (个别词条会延长); 目标生命值初始 4, 每违反一次词条规定减 1, 归零合约失败;\n"
    "- 选的词条等级加起来是危机等级, 等级越高完成后得到的休息时间越多 (完成给 20~45 分钟);\n"
    "- 合约中空闲 (不专注也不休息) / 设备离线累计超过 15 分钟也会失败; 失败或提前终止只按进行的比例给休息时间;\n"
    "- 合约期间用户只能专注 / 休息, 合约中得到的休息时间在合约结束后作废, 只留完成奖励.")


def joint_label(terms, start: datetime, end: datetime, today=None) -> str:
    """聊天里 / 聊天框上方显示的一行: 联合行动的开始和结束时间 (不在今天 (today, 默认真实的今天) 的带上日期)."""
    day = "" if start.date() == (today or datetime.now().date()) else f"{start:%m-%d} "
    return f"🤝 联合行动 {day}{start:%H:%M} - {end:%H:%M}" + (f" · {len(terms)} 个词条" if terms else "")


def joint_text(terms, start: datetime, end: datetime) -> str:
    """发给对方的联合行动邀请 (用的是原来'附件'的形式): 前面是合约的基础规则, 然后是选的词条 / 开始 / 结束时间; 对方要回 [Yes] 或 [No]."""
    level = C.level_of(terms)
    span = _hm((end - start).total_seconds() / 60)
    lines = ["(用户发来的联合行动邀请: 想和你一起完成一次合约)", CONTRACT_RULES, "",
             f"【联合行动】开始时间 {start:%Y-%m-%d %H:%M}, 预计结束时间 {end:%Y-%m-%d %H:%M} (按选的词条算出的合约时长 {span}).",
             f"危机等级 {level}, 完成后休息约 {C.rest_minutes_total(level, terms)} 分钟; 目标生命值 {C.life_of(terms)}.",
             "【选择的词条】" + ("" if terms else "(没有选词条)")]
    for tid in terms:
        t = C.BY_ID.get(tid)
        if not t:
            continue
        kind, sep, name = t[3].partition(":")
        if not sep:
            kind, name = "特殊", kind
        lines.append(f"- {name}({kind}, 等级{t[2]}): {t[4]}".replace("\n", " "))
    lines += ["", "请按规则 X 回复: 接受就发送 [Yes], 不接受发送 [No] (后面可以再加一句话)."]
    return "\n".join(lines)


def hour_text(lines: list, enabled: bool = True) -> str:
    """【整点记录·今天】: 一行一小时."""
    if not enabled:
        return "【整点记录】\n  整点记录没有开启"
    return "【整点记录·今天】\n" + ("\n".join("  " + l for l in lines) if lines else "  今天还没有记录")


def net_text(mode: str, used_s: float, total_s: float, net_mode: str, month_reasons: str = "") -> str:
    """【联网】: 现在的状态, 今日额度, 断网方式; month_reasons: 本月联网理由 (build 出来的那一块)."""
    names = {"online": "联网中", "pending": "申请中 (冷静期)", "offline": "离线", "dev": "离线"}
    out = ("【联网】\n  状态: " + names.get(mode, mode) + "\n  今日额度: 已用 " + _hm(used_s / 60) + " / 共 " + _hm(total_s / 60) +
           "\n  断网方式: " + net_mode)
    return out + ("\n\n" + month_reasons if month_reasons else "")


def rules_text(mode_name: str, lists: list) -> str:
    """【规则】: 过滤名单模式 + 四张名单 [(标题, [条目])] (每张最多 40 条)."""
    out = ["【规则】", "  过滤名单模式: " + mode_name]
    for title, items in lists:
        out.append(f"  {title} ({len(items)}): " + ("、".join(items[:40]) + (" …" if len(items) > 40 else "") if items else "空"))
    return "\n".join(out)


def settings_text(rows: list) -> str:
    """【设置】: [(页, 名称, 值)] 一页一组."""
    out = ["【设置】(当前生效的值)"]
    page = None
    for pg, name, value in rows:
        if pg != page:
            out.append(f" {pg}:")
            page = pg
        out.append(f"  {name}: {value}")
    return "\n".join(out)


def prescript_text(urban: bool, history: list) -> str:
    """【指令】: 都市之意主题独占的模块; 最近结束的几条."""
    if not urban:
        return "【指令】\n  指令是都市之意主题独占的模块, 现在没有启用"
    out = ["【指令】(最近结束的)"]
    for ended, kind, result, body in history:
        out.append(f"  {ended} 指令 {kind}: {body} → {result}")
    return "\n".join(out) if history else "【指令】\n  还没有结束过的指令"


def build(sel: dict, dm, net, contract, now: datetime = None, focus_live=None, cal_live=None,
          month_prefix: str = None, window_names: dict = None, today_log=None, today_timer=None) -> str:
    """today_log / today_timer: 今天的整点记录 / 计时器的文字 (app 给: 和对方查询的 [log] / [timer] 一样).
    块的顺序和 📎 窗口里一样: 今天 → 日历 → 统计(月数据)."""
    now = now or datetime.now()
    prefix = month_prefix or now.strftime("%Y-%m")
    blocks, cb, sb = [], [], []                  # 今天, 日历, 统计(月数据)
    today = sel.get("today", [])
    if "log" in today and today_log:
        blocks.append(today_log())
    if "timer" in today and today_timer:
        blocks.append(today_timer())
    stats = list(sel.get("stats", []))
    if "log" in stats:
        sb.append(f"【统计·整点记录 {prefix}】\n" + _top(dm.hour_activity_seconds(prefix)))
    if "net" in stats:
        from data_manager import tally_items
        sb.append(f"【统计·联网理由 {prefix}】\n" + _top(tally_items(net.usage_by_reason(prefix)), unit="m"))
    if "window" in stats:
        names = {"": "空白", "?": "未知程序", **(window_names or {})}
        out = {}
        for k, v in dm.window_seconds(prefix, "all").items():
            out[names.get(k, k)] = out.get(names.get(k, k), 0.0) + v
        sb.append(f"【统计·窗口 {prefix}】\n" + _top(out))
    if "focus" in stats:
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        lines = []
        for i in range(7):
            day = today - timedelta(days=i)
            hours = dm.focus_hours(day, focus_live)
            st = sum(h[0] for h in hours) / 60
            br = sum(h[1] for h in hours) / 60
            lines.append(f"  {day:%m-%d} 周{WEEKDAY[day.weekday()]}: 专注 {st:.0f} 分钟, 休息 {br:.0f} 分钟")
        sb.append("【统计·专注计时 最近 7 天】\n" + "\n".join(lines))
    if "contracts" in stats:
        recs = C.recent_completed(contract.history, 5)
        sb.append("【统计·合约记录 最近完成的】\n" + ("\n".join(_record(r) for r in recs) if recs else "  还没有顺利完成的合约"))
    for kind in sel.get("cal", []):
        name = dict(CAL_KEYS).get(kind)
        if not name:
            continue
        lines = []
        if kind == "study":
            days = {d: h for d, h in dm.study_data.items() if d.startswith(prefix)}
        else:
            floor = dm._focus_floor(now).date()
            days = {}
            for n in range(1, 32):
                try:
                    d = datetime.strptime(f"{prefix}-{n:02d}", "%Y-%m-%d")
                except ValueError:
                    break
                if floor <= d.date() <= now.date():
                    days[d.strftime("%Y-%m-%d")] = dm.day_kinds(d, cal_live if d.date() == now.date() else None, now)[kind] / 3600.0
        for d in sorted(days):
            if days[d] > 0:
                lines.append(f"  {d}: {days[d]:.1f} h")
        tail = "" if kind == "study" else " (只有最近 7 天有记录)"
        cb.append(f"【日历·{name} {prefix}{tail}】\n" + ("\n".join(lines) if lines else "  没有记录")
                      + f"\n  合计: {sum(days.values()):.1f} h")
    text = "\n\n".join(blocks + cb + sb)
    return text[:MAX_CHARS]


def _selftest_templates():
    """对方查询得到的是参考模板: 每个模块都有, 带占位符, 没有用户的真实数据."""
    for tag in MODULE_TAGS:
        t = template(tag, [("基本设置", "语言")])
        assert t, tag
        if tag != "reset":
            assert NOTE in t and "<" in t, tag
    assert "<值>" in template("settings", [("基本设置", "语言")]) and "基本设置:" in template("settings", [("基本设置", "语言")])
    lib = template("contract")
    assert "【已选择】" not in lib and "【已激活】" not in lib and "【已失效】" not in lib and "持久战" in lib and "【词条组合】" in lib
    assert template("nope") == ""
    return True


def _selftest():
    assert _selftest_templates()
    class DM:
        study_data = {"2026-11-01": 3.5, "2026-11-02": 1.0, "2026-10-30": 9.0}

        def hour_activity_seconds(self, p):
            return {"写代码": 7200.0, "睡眠": 3600.0}

        def window_seconds(self, p, dev):
            return {"": 600.0, "code.exe": 1800.0}

        def focus_hours(self, day, live):
            return [(1800.0, 600.0)] + [(0.0, 0.0)] * 23

        def _focus_floor(self, now):
            return now - timedelta(days=6)

        def day_kinds(self, d, live, now):
            return {"break": 3600.0, "idle": 0.0, "sleep": 7200.0, "study": 0.0}

    class Net:
        def usage_by_reason(self, p):
            return [("学习资料", 600.0), ("学习资料", 300.0)]

    class Con:
        active = False
        draft = ["long1"]
        history = []
        terms = []
        lost = []

    assert "目前状态: 休息中" in timer_text({"state": "break", "rest_left_min": 12, "focus_total_min": 90}) and FOG_TEXT in timer_text({})
    assert "09:00 写代码" in hour_text(["09:00 写代码"]) and "没有开启" in hour_text([], False)
    assert "已用 30 分钟 / 共 2 小时 0 分钟" in net_text("offline", 1800, 7200, "Proxy") and "联网中" in net_text("online", 0, 60, "x")
    assert "白名单 · 网站 (2): a.com、b.com" in rules_text("黑名单模式", [("白名单 · 网站", ["a.com", "b.com"]), ("黑名单 · 应用", [])])
    assert "黑名单 · 应用 (0): 空" in rules_text("黑名单模式", [("黑名单 · 应用", [])])
    assert " 计时器:\n  休息时间倍率: ×1" in settings_text([("计时器", "休息时间倍率", "×1")])
    assert "没有启用" in prescript_text(False, []) and "→ completed" in prescript_text(True, [("2026-11-02T10:00:00", "V", "completed", "停下")])

    class Act(Con):
        active = True
        terms = ["long1"]
        level_text = "1"

    now = datetime(2026, 11, 2, 12, 0)
    assert "合约进行中, 危机等级 1" in library(Act())
    assert build({"contract": True}, DM(), Net(), Act(), now=now) == "", "📎 附件里没有合约词条了"
    sel = {"stats": ["log", "net", "focus", "window", "contracts"], "cal": ["study", "sleep"]}
    text = build(sel, DM(), Net(), Con(), now=now)
    text += "\n\n" + library(Con())
    for k in ("整点记录 2026-11", "写代码: 2.0 h", "学习资料: 15 分钟", "专注 30 分钟, 休息 10 分钟", "空白: 0.2 h", "code.exe",
              "还没有顺利完成的合约", "日历·专注", "2026-11-01: 3.5 h", "合计: 4.5 h", "日历·睡眠", "只有最近 7 天", "合约没有开始", "持久战-I(环境, 等级1)【已选择】", "无言为真(环境, 等级2)", "打年糕", "信息隔绝"):
        assert k in text, k
    assert "2026-10-30" not in text
    lib = library(Act())
    assert "持久战-I(环境, 等级1)【已激活】" in lib and "无言为真(环境, 等级2):" in lib and "【已激活】" not in lib.split("无言为真")[1].split("\n")[0]
    assert "打年糕(已选 1/4): 需要" in lib and "未知变量(特殊, 等级3)" in lib and "持久战(任意一档)✓" in lib
    assert len(lib) < 9000, len(lib)
    assert label(sel) == "📎 日历·专注、睡眠 / 统计(月数据)·整点记录、联网理由、专注计时、窗口、合约记录"
    assert label({"contract": True}) == "", "合约词条已经不是附件选项"
    assert label({"today": ["timer", "log"]}) == "📎 整点记录(今天) / 专注计时(今天)"
    # 今天的整点记录 / 专注计时, 顺序: 今天 → 日历 → 统计(月数据)
    tt = build({"today": ["timer", "log"], "cal": ["study"], "stats": ["net"]}, DM(), Net(), Con(), now=now,
               today_log=lambda: "【整点记录·今天】\n  09:00 写代码", today_timer=lambda: "【计时器】\n  目前状态: 专注中")
    order = [tt.index(k) for k in ("【整点记录·今天】", "【计时器】", "【日历·专注", "【统计·联网理由")]
    assert order == sorted(order), order
    assert build({"today": ["log", "timer"]}, DM(), Net(), Con(), now=now) == ""
    assert dict(TODAY_KEYS)["log"] == "整点记录(今天)"
    assert label({"stats": [], "cal": []}) == ""
    # 专注计时(今天): 先按时间顺序列出每一段休息 / 专注 / 睡眠的持续区间, 最后才是总时间
    d0 = datetime(2026, 11, 4)
    ivs = [("break", d0.replace(hour=8, minute=40), d0.replace(hour=9, minute=18)),
           ("sleep", d0, d0.replace(hour=6, minute=30)),
           ("study", d0.replace(hour=7), d0.replace(hour=8, minute=40)),
           ("study", d0.replace(hour=9, minute=18), d0.replace(hour=10, minute=0))]
    nowt = d0.replace(hour=10, minute=0)
    tt = timer_text({"state": "focus", "focus_total_min": 142, "focus_cur_min": 42}, ivs, nowt)
    pos = [tt.index(k) for k in ("00:00-06:30 睡眠 (6 小时 30 分钟)", "07:00-08:40 专注 (1 小时 40 分钟)", "08:40-09:18 休息 (38 分钟)",
                                 "09:18-现在 专注 (42 分钟)", "总计:", "目前状态: 专注中", "今日总专注时长: 2 小时 22 分钟")]
    assert pos == sorted(pos), tt
    assert "今天还没有记录" in timer_text({"state": "idle"}, [], nowt) and "今天的每一段" not in timer_text({"state": "idle"})
    assert FOG_TEXT in timer_text({}, ivs, nowt) and "09:18" not in timer_text({}, ivs, nowt)
    # 联合行动的邀请: 前面是合约的基础规则, 然后是开始 / 结束时间和词条
    a, b = d0.replace(hour=14), d0.replace(hour=18)
    jt = joint_text(["long1"], a, b)
    for k in ("【合约的基础规则】", "初始时长 3 小时", "目标生命值", "开始时间 2026-11-04 14:00", "预计结束时间 2026-11-04 18:00",
              "持久战-I(环境, 等级1)", "[Yes]", "[No]", "危机等级 1"):
        assert k in jt, k
    assert jt.index("【合约的基础规则】") < jt.index("【联合行动】") < jt.index("【选择的词条】")
    assert joint_label(["long1"], a, b).endswith("14:00 - 18:00 · 1 个词条") and "(没有选词条)" in joint_text([], a, b)
    return "comm_data selftest OK"


if __name__ == "__main__":
    print(_selftest())
