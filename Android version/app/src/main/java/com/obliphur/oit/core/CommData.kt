package com.obliphur.oit.core

import java.time.LocalDate
import java.time.LocalDateTime

/** share data 里用户的数据 (一页对应哪几块): 统计(月数据)的几页, 日历 (选类别), 今天的整点记录 / 专注计时 (today: log / timer). 对应桌面版 comm_data.py.
 *  (1.0.308 起没有"合约词条"了; 合约词条只在联合行动的邀请里发.) */
data class CommSel(val stats: List<String> = emptyList(), val cal: List<String> = emptyList(),
                   val today: List<String> = emptyList())

object CommData {
    val STATS_KEYS = listOf("log" to "整点记录", "focus" to "专注计时", "net" to "联网理由", "window" to "应用", "contracts" to "合约记录")
    const val STATS_NAME = "统计(月数据)"
    /** 今天的整点记录 / 计时器 (对方查询的 [log] / [timer] 也是这两块). */
    val TODAY_KEYS = listOf("log" to "整点记录(今天)", "timer" to "专注计时(今天)")
    val CAL_KEYS = listOf("study" to "专注", "break" to "休息", "idle" to "空闲", "sleep" to "睡眠")
    private const val WEEKDAY = "一二三四五六日"
    private const val TOP = 30
    const val MAX_CHARS = 60000          // 一次最多发多少字 (词条库约 4000 字, 统计每类最多 30 项)

    /** 对方可以查询的模块 (沟通 24.2.1): 回复 [refuse to reply][模块名] 得到该模块的数据内容和格式. */
    val MODULE_TAGS = listOf("contract", "calendar", "stats", "timer", "log", "net", "rules", "settings", "reset", "prescript")
    const val FOG_TEXT = "(黑雾中, 暂时无法提供)"
    private val STATE_TEXT = mapOf("focus" to "专注中", "break" to "休息中", "idle" to "空闲", "sleep" to "睡觉中", "out" to "外出中")
    val RESET_TEXT = "【重置】(OIT 的 重置 页)\n" +
        "  需要输入 RESET 再确认一次. 重置会: 解除网络限制 (重新启用网卡, 关闭过滤代理, 还原系统代理); 请每台绑定的设备也清空数据 (绑定不解除);\n" +
        "  删除整个数据目录: 学习记录, 整点记录, 统计, 联网额度与日志, 名单, 设置, AI 配置, 聊天记录全部删除; 进行中的专注不记录; 不留任何记录.\n" +
        "  程序随即自动重新打开, 像第一次安装: 设置都是默认值, 名单为空, 重新开始 72 小时配置期."

    const val NOTE = "(格式模板: <尖括号> 里是占位符, 不是用户的数据; 请参照这个格式, 根据人设自己填写具体内容)"
    private val TEMPLATES = mapOf(
        "timer" to "【计时器】$NOTE\n  今天的每一段 (按时间顺序):\n  <HH:MM>-<HH:MM> <专注 / 休息 / 睡眠> (<X 小时 Y 分钟>)\n" +
            "  <HH:MM>-<HH:MM> <专注 / 休息 / 睡眠> (<X 小时 Y 分钟>)\n" +
            "  (最后一行可能是还在进行的一段: <HH:MM>-现在 <专注 / 休息 / 睡眠> (<X 分钟>); 没有记录就写\"今天还没有记录\")\n  总计:\n" +
            "  目前状态: <专注中 / 休息中 / 空闲 / 睡觉中 / 外出中>\n  剩余休息时间: <X 小时 Y 分钟 / 无>\n" +
            "  今日总专注时长: <X 小时 Y 分钟>\n  目前这轮专注时长: <X 分钟 / 0 分钟(未在专注)>",
        "log" to "【整点记录·今天】$NOTE\n  <HH>:00 <这个小时做了什么>\n  <HH>:00 <这个小时做了什么>\n  (一行一个小时, 没有记录的小时不写)",
        "calendar" to "【日历·<专注 / 休息 / 空闲 / 睡眠> <YYYY-MM>】$NOTE\n  <YYYY-MM-DD>: <X.X> h\n  <YYYY-MM-DD>: <X.X> h\n" +
            "  (只列有记录的日子; 休息 / 空闲 / 睡眠只有最近 7 天)\n  合计: <X.X> h",
        "stats" to "【统计·整点记录 <YYYY-MM>】$NOTE\n  <活动名称>: <X.X> h\n  <活动名称>: <X.X> h\n  合计: <X.X> h\n\n" +
            "【统计·联网理由 <YYYY-MM>】\n  <理由>: <X> 分钟\n  合计: <X.X> h\n\n" +
            "【统计·窗口 <YYYY-MM>】\n  <程序名>: <X.X> h\n  合计: <X.X> h\n\n" +
            "【统计·专注计时 最近 7 天】\n  <MM-DD> 周<一~日>: 专注 <X> 分钟, 休息 <X> 分钟\n\n" +
            "【统计·合约记录 最近完成的】\n  危机等级 <N>: <词条名>; <词条名>",
        "net" to "【联网】$NOTE\n  状态: <联网中 / 申请中 (冷静期) / 离线>\n  今日额度: 已用 <X 小时 Y 分钟> / 共 <X 小时 Y 分钟>\n  断网方式: <断网方式的名字>\n\n" +
            "【统计·联网理由 <YYYY-MM>】\n  <理由>: <X> 分钟\n  合计: <X.X> h",
        "rules" to "【规则】$NOTE\n  过滤名单模式: <黑名单模式 / 白名单模式>\n  白名单 · 网站 (<数量>): <域名>、<域名> …\n" +
            "  白名单 · 应用 (<数量>): <程序名>、<程序名> …\n  黑名单 · 网站 (<数量>): <域名>、<域名> …\n  黑名单 · 应用 (<数量>): <程序名>、<程序名> …\n" +
            "  (空的名单写\"空\"; 每张最多列 40 条)",
        "prescript" to "【指令】$NOTE\n  <结束的时间> 指令 <类型>: <指令内容> → <结果>\n  (都市之意主题独占的模块; 最近结束的几条, 没有就写\"还没有结束过的指令\")",
    )

    /** 对方查询 [模块名] 得到的参考模板 (沟通 24.2.1): 只有格式和占位符, 没有用户的真实数据 (所以黑雾中也能给), 具体内容由对方按人设自己填写.
     *  settingsRows: (页, 名称) (设置页的结构, 值写成 <值>). 同 comm_data.template. */
    fun template(tag: String, settingsRows: List<Pair<String, String>> = emptyList()): String = when (tag) {
        "reset" -> RESET_TEXT
        "contract" -> library(null)
        "settings" -> {
            val out = arrayListOf("【设置】$NOTE")
            var page: String? = null
            for ((pg, name) in settingsRows) {
                if (pg != page) { out.add(" $pg:"); page = pg }
                out.add("  $name: <值>")
            }
            out.joinToString("\n")
        }
        else -> TEMPLATES[tag] ?: ""
    }

    private fun hm(minutes: Double): String {
        val m = Math.round(maxOf(0.0, minutes)).toInt()
        return if (m >= 60) "${m / 60} 小时 ${m % 60} 分钟" else "$m 分钟"
    }

    private val KIND_TEXT = mapOf("study" to "专注", "break" to "休息", "sleep" to "睡眠")

    /** 今天每一段专注 / 休息 / 睡眠的持续区间, 按开始时间排好 → "HH:MM-HH:MM 专注 (X 小时 Y 分钟)". 结束不早于现在的 (还在进行的一段) 写成 "HH:MM-现在"; liveWord 为空时写成此刻的时间 (要存下来的记录用, 日志的专注时间段). */
    fun intervalLines(intervals: List<Triple<String, LocalDateTime, LocalDateTime>>, now: LocalDateTime = LocalDateTime.now(),
                      liveWord: String = "现在"): List<String> {
        val out = ArrayList<String>()
        for ((kind, a, b) in intervals.sortedWith(compareBy({ it.second }, { it.third }))) {
            val name = KIND_TEXT[kind] ?: continue
            if (!b.isAfter(a)) continue
            val live = !b.isBefore(now)
            val end = if (live) now else b
            out.add("%02d:%02d-%s %s (%s)".format(a.hour, a.minute, if (live) liveWord.ifEmpty { "%02d:%02d".format(now.hour, now.minute) } else "%02d:%02d".format(b.hour, b.minute), name,
                hm(secondsBetween(a, end) / 60)))
        }
        return out
    }

    /** 【计时器】: 先按时间顺序列出今天的每一段休息 / 专注 / 睡眠的持续区间 (intervals), 最后才是总的数据:
     *  目前状态, 剩余休息时间, 今日总专注时长, 目前这轮专注时长. intervals = null (调用方不提供) 时只有总的数据. */
    fun timerText(s: CommSnap?, intervals: List<Triple<String, LocalDateTime, LocalDateTime>>? = null,
                  now: LocalDateTime = LocalDateTime.now()): String {
        if (s == null) return "【计时器】\n  $FOG_TEXT"
        val left = if (s.state == "break") hm(s.restLeftMin) else "无"
        val cur = if (s.state == "focus") hm(s.focusCurMin) else "0 分钟(未在专注)"
        var head = "【计时器】\n"
        if (intervals != null) {
            val lines = intervalLines(intervals, now)
            head += "  今天的每一段 (按时间顺序):\n" + (if (lines.isEmpty()) "  今天还没有记录" else lines.joinToString("\n") { "  $it" }) + "\n  总计:\n"
        }
        return head + "  目前状态: ${STATE_TEXT[s.state] ?: s.state}\n  剩余休息时间: $left\n  今日总专注时长: ${hm(s.focusTotalMin)}\n  目前这轮专注时长: $cur"
    }

    // ---- 联合行动 (MECHANICS 24.9): 邀请对方一起完成一次合约 ----
    val CONTRACT_RULES = "【合约的基础规则】合约是 OIT 的独占功能: 用户自选一些'词条'(减益), 让 OIT 变得更严苛, 然后开始行动.\n" +
        "- 初始时长 3 小时 (个别词条会延长); 目标生命值初始 4, 每违反一次词条规定减 1, 归零合约失败;\n" +
        "- 选的词条等级加起来是危机等级, 等级越高完成后得到的休息时间越多 (完成给 20~45 分钟);\n" +
        "- 合约中空闲 (不专注也不休息) / 设备离线累计超过 15 分钟也会失败; 失败或提前终止只按进行的比例给休息时间;\n" +
        "- 合约期间用户只能专注 / 休息, 合约中得到的休息时间在合约结束后作废, 只留完成奖励."

    /** 聊天里 / 聊天框上方显示的一行: 联合行动的开始和结束时间 (不在今天 (today) 的带上日期). */
    fun jointLabel(terms: List<String>, start: LocalDateTime, end: LocalDateTime, today: LocalDate = LocalDate.now()): String {
        val day = if (start.toLocalDate() == today) "" else "%02d-%02d ".format(start.monthValue, start.dayOfMonth)
        return "🤝 联合行动 $day%02d:%02d - %02d:%02d".format(start.hour, start.minute, end.hour, end.minute) +
            if (terms.isNotEmpty()) " · ${terms.size} 个词条" else ""
    }

    /** 发给对方的联合行动邀请 (用的是原来'附件'的形式): 前面是合约的基础规则, 然后是选的词条 / 开始 / 结束时间; 对方要回 [Yes] 或 [No]. */
    fun jointText(terms: List<String>, start: LocalDateTime, end: LocalDateTime): String {
        val level = Contract.levelOf(terms)
        fun f(t: LocalDateTime) = "%04d-%02d-%02d %02d:%02d".format(t.year, t.monthValue, t.dayOfMonth, t.hour, t.minute)
        val lines = arrayListOf("(用户发来的联合行动邀请: 想和你一起完成一次合约)", CONTRACT_RULES, "",
            "【联合行动】开始时间 ${f(start)}, 预计结束时间 ${f(end)} (按选的词条算出的合约时长 ${hm(secondsBetween(start, end) / 60)}).",
            "危机等级 $level, 完成后休息约 ${Contract.restMinutesTotal(level, terms)} 分钟; 目标生命值 ${Contract.lifeOf(terms)}.",
            "【选择的词条】" + if (terms.isEmpty()) "(没有选词条)" else "")
        for (id in terms) {
            val t = Contract.BY_ID[id] ?: continue
            val kind = if (':' in t.name) t.name.substringBefore(':') else "特殊"
            lines.add("- ${t.name.substringAfter(':')}($kind, 等级${t.level}): ${t.desc}".replace("\n", " "))
        }
        lines.add("")
        lines.add("请按规则 X 回复: 接受就发送 [Yes], 不接受发送 [No] (后面可以再加一句话).")
        return lines.joinToString("\n")
    }

    /** 【整点记录·今天】: 一行一小时. */
    fun hourText(lines: List<String>, enabled: Boolean = true): String =
        if (!enabled) "【整点记录】\n  整点记录没有开启"
        else "【整点记录·今天】\n" + if (lines.isEmpty()) "  今天还没有记录" else lines.joinToString("\n") { "  $it" }

    /** 【联网】: 现在的状态, 今日额度, 断网方式; monthReasons: 本月联网理由 (build 出来的那一块). */
    fun netText(mode: String, usedS: Double, totalS: Double, netMode: String, monthReasons: String = ""): String {
        val names = mapOf("online" to "联网中", "pending" to "申请中 (冷静期)", "offline" to "离线", "dev" to "离线")
        val out = "【联网】\n  状态: ${names[mode] ?: mode}\n  今日额度: 已用 ${hm(usedS / 60)} / 共 ${hm(totalS / 60)}\n  断网方式: $netMode"
        return out + if (monthReasons.isNotEmpty()) "\n\n$monthReasons" else ""
    }

    /** 【规则】: 过滤名单模式 + 四张名单 (标题, 条目) (每张最多 40 条). */
    fun rulesText(modeName: String, lists: List<Pair<String, List<String>>>): String =
        (listOf("【规则】", "  过滤名单模式: $modeName") + lists.map { (title, items) ->
            "  $title (${items.size}): " + if (items.isEmpty()) "空" else items.take(40).joinToString("、") + if (items.size > 40) " …" else ""
        }).joinToString("\n")

    /** 【设置】: (页, 名称, 值) 一页一组. */
    fun settingsText(rows: List<Triple<String, String, String>>): String {
        val out = arrayListOf("【设置】(当前生效的值)")
        var page: String? = null
        for ((pg, name, value) in rows) {
            if (pg != page) { out.add(" $pg:"); page = pg }
            out.add("  $name: $value")
        }
        return out.joinToString("\n")
    }

    /** 【指令】: 都市之意主题独占的模块; 最近结束的几条. */
    fun prescriptText(urban: Boolean, history: List<List<String>>): String {
        if (!urban) return "【指令】\n  指令是都市之意主题独占的模块, 现在没有启用"
        if (history.isEmpty()) return "【指令】\n  还没有结束过的指令"
        return "【指令】(最近结束的)\n" + history.joinToString("\n") { "  ${it[0]} 指令 ${it[1]}: ${it[3]} → ${it[2]}" }
    }

    fun top(d: Map<String, Double>, minutes: Boolean = false): String {
        val rows = d.entries.sortedWith(compareBy({ -it.value }, { it.key }))
        val out = ArrayList<String>()
        fun v(x: Double) = if (minutes) "%.0f 分钟".format(x / 60) else "%.1f h".format(x / 3600)
        for (e in rows.take(TOP)) out.add("  ${e.key.ifEmpty { "空白" }}: ${v(e.value)}")
        if (rows.size > TOP) out.add("  其他 ${rows.size - TOP} 项: %.1f h".format(rows.drop(TOP).sumOf { it.value } / 3600))
        out.add("  合计: %.1f h".format(d.values.sum() / 3600))
        return out.joinToString("\n")
    }

    fun termText(id: String): String = Contract.BY_ID[id]?.let { "${it.name.substringAfter(':')} (等级${it.level}): ${it.desc}" } ?: id

    internal fun record(rec: org.json.JSONObject): String {
        val rows = Contract.recordRows(rec)
        val level = rec.optInt("level")
        val head = "危机等级 " + Contract.levelLabel(level, rec.optInt("orig_level", level))
        val body = rows.joinToString("; ") { r -> (Contract.BY_ID[r.id]?.name?.substringAfter(':') ?: r.id) + if (r.void) "(已失效)" else "" }
        return "  $head: $body"
    }

    private fun reqName(req: String): String {
        Contract.BY_ID[req]?.let { return it.name.substringAfter(':') }
        Contract.TERMS.firstOrNull { it.group == req }?.let { return it.name.substringAfter(':').substringBeforeLast('-') + "(任意一档)" }
        return req
    }

    /** 对方发起联合行动时看到的词条库: 每个词条前面有编号 (= 词条在 Contract.TERMS 里的位置 + 1), 对方回编号选词条. */
    fun numberedLibrary(): String {
        val lines = arrayListOf("【合约词条库】")
        for ((i, t) in Contract.TERMS.withIndex()) {
            val kind = if (':' in t.name) t.name.substringBefore(':') else "特殊"
            lines.add("${i + 1}. ${t.name.substringAfter(':')}($kind, 等级${t.level}): ${t.desc}".replace("\n", " "))
        }
        lines.add("\n【词条组合】(组合的要求都选着才生效)")
        for (cb in Contract.COMBOS)
            lines.add("- ${cb.name}: 需要 " + cb.reqs.joinToString(" + ") { reqName(it) } + "; 效果: ${cb.desc}".replace("\n", ", "))
        return lines.joinToString("\n")
    }

    /** 整个词条库和组合, 标出哪些被用户选择 / 激活了: 合约进行中 = 激活的, 没开始 = 选好的(还没生效). */
    fun library(c: Contract?): String {
        val active = c?.active == true
        val selected = if (c == null) emptyList() else if (active) c.terms else c.draft
        val tag = if (active) "已激活" else "已选择"
        val lost = if (c != null && active) c.lost.toSet() else emptySet()
        val lines = ArrayList<String>()
        if (c == null) lines.add("【合约词条库】$NOTE\n(下面是全部词条和组合; 用户具体选了哪些、合约进行得怎么样, 请根据人设自己编)")
        else {
            val head = if (active) "合约进行中, 危机等级 ${Contract.levelLabel(c.level, c.origLevel)}"
            else if (selected.isNotEmpty()) "合约没有开始, 选好的词条还没生效" else "现在没有进行中的合约, 也没有选好的词条"
            lines.add("【合约词条库】$head。词条后面的【$tag】= 用户选的; 【已失效】= 被违反到失效。")
        }
        for (t in Contract.TERMS) {
            val mark = if (t.id in selected) "【$tag】" else if (t.id in lost) "【已失效】" else ""
            val kind = if (':' in t.name) t.name.substringBefore(':') else "特殊"
            lines.add("- ${t.name.substringAfter(':')}($kind, 等级${t.level})$mark: ${t.desc}".replace("\n", " "))
        }
        lines.add("\n【词条组合】(组合的要求都选着才生效)")
        for (cb in Contract.COMBOS) {
            val got = cb.reqs.filter { r -> selected.any { id -> if (r in Contract.GROUPS) Contract.BY_ID[id]?.group == r else id == r } }
            val done = Contract.comboDone(cb, selected)
            val mark = if (done) "【$tag·已凑齐】" else if (got.isNotEmpty()) "(已选 ${got.size}/${cb.reqs.size})" else ""
            lines.add("- ${cb.name}$mark: 需要 " + cb.reqs.joinToString(" + ") { reqName(it) + if (it in got) "✓" else "" } +
                "; 效果: ${cb.desc}".replace("\n", ", "))
        }
        return lines.joinToString("\n")
    }

    /** 数据都从 Core 里取; 黑雾中不给 (调用方先判断). 块的顺序和 📎 窗口里一样: 今天 → 日历 → 统计(月数据). */
    fun build(sel: CommSel, core: Core, windowName: (String) -> String): String {
        val now = LocalDateTime.now()
        val prefix = "%04d-%02d".format(now.year, now.monthValue)
        val blocks = ArrayList<String>()                // 今天
        val cb = ArrayList<String>()                    // 日历
        val sb = ArrayList<String>()                    // 统计(月数据)
        if ("log" in sel.today) blocks.add(core.commLogText())
        if ("timer" in sel.today) blocks.add(core.commTimerText())
        if ("log" in sel.stats) sb.add("【统计·整点记录 $prefix】\n" + top(core.dm.hourActivitySeconds(prefix)))
        if ("net" in sel.stats) sb.add("【统计·联网理由 $prefix】\n" + top(tallyItems(core.net.usageByReason(prefix)), minutes = true))
        if ("window" in sel.stats) {
            val out = HashMap<String, Double>()
            for ((k, v) in core.dm.windowSeconds(prefix, "all")) {
                val n = when (k) { "" -> "空白"; "?" -> "未知程序"; else -> windowName(k) }
                out[n] = (out[n] ?: 0.0) + v
            }
            sb.add("【统计·应用 $prefix】\n" + top(out))
        }
        if ("focus" in sel.stats) {
            val today = LocalDate.now()
            val live = core.timer.live()
            val lines = (0..6).map { i ->
                val day = today.minusDays(i.toLong())
                val hours = core.dm.focusHours(day, live, false)
                "  %02d-%02d 周%s: 专注 %.0f 分钟, 休息 %.0f 分钟".format(day.monthValue, day.dayOfMonth, WEEKDAY[day.dayOfWeek.value - 1],
                    hours.sumOf { it.first } / 60, hours.sumOf { it.second } / 60)
            }
            sb.add("【统计·专注计时 最近 7 天】\n" + lines.joinToString("\n"))
        }
        if ("contracts" in sel.stats) {
            val recs = Contract.recentCompleted(core.contract.history, 5)
            sb.add("【统计·合约记录 最近完成的】\n" + if (recs.isEmpty()) "  还没有顺利完成的合约" else recs.joinToString("\n") { record(it) })
        }
        for (kind in sel.cal) {
            val name = CAL_KEYS.firstOrNull { it.first == kind }?.second ?: continue
            val days: Map<String, Double> = if (kind == "study") core.dm.monthStudy(prefix) else {
                val today = LocalDate.now()
                val live = core.timer.calendarLive()
                (0..6).map { today.minusDays(it.toLong()) }.filter { it.toString().startsWith(prefix) }
                    .associate { d -> d.toString() to (core.dm.dayKinds(d, if (d == today) live else null, now, false)[kind] ?: 0.0) / 3600.0 }
            }
            val lines = days.keys.sorted().filter { (days[it] ?: 0.0) > 0 }.map { "  $it: %.1f h".format(days[it]) }
            val tail = if (kind == "study") "" else " (只有最近 7 天有记录)"
            cb.add("【日历·$name $prefix$tail】\n" + (if (lines.isEmpty()) "  没有记录" else lines.joinToString("\n")) +
                "\n  合计: %.1f h".format(days.values.sum()))
        }
        return (blocks + cb + sb).joinToString("\n\n").take(MAX_CHARS)
    }
}
