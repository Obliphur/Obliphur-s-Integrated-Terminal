package com.obliphur.oit.core

import java.time.LocalDate
import java.time.LocalDateTime

/** 沟通页 📎 勾选的内容: 统计的几页, 日历 (选类别), 合约的词条. 对应桌面版 comm_data.py. */
data class CommSel(val stats: List<String> = emptyList(), val cal: List<String> = emptyList(), val contract: Boolean = false)

object CommData {
    val STATS_KEYS = listOf("log" to "整点记录", "focus" to "专注计时", "net" to "联网理由", "window" to "应用", "contracts" to "合约记录")
    /** 📎 窗口第一次打开时默认勾上的. */
    val DEFAULT_STATS = listOf("log", "focus")
    val CAL_KEYS = listOf("study" to "专注", "break" to "休息", "idle" to "空闲", "sleep" to "睡眠")
    private const val WEEKDAY = "一二三四五六日"
    private const val TOP = 30
    const val MAX_CHARS = 60000          // 一次最多发多少字 (词条库约 4000 字, 统计每类最多 30 项)

    /** 聊天里显示的那一行. */
    fun label(sel: CommSel): String {
        val parts = ArrayList<String>()
        val st = sel.stats.mapNotNull { k -> STATS_KEYS.firstOrNull { it.first == k }?.second }
        if (st.isNotEmpty()) parts.add("统计·" + st.joinToString("、"))
        val cl = sel.cal.mapNotNull { k -> CAL_KEYS.firstOrNull { it.first == k }?.second }
        if (cl.isNotEmpty()) parts.add("日历·" + cl.joinToString("、"))
        if (sel.contract) parts.add("合约词条")
        return if (parts.isEmpty()) "" else "📎 " + parts.joinToString(" / ")
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

    private fun record(rec: org.json.JSONObject): String {
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

    /** 整个词条库和组合, 标出哪些被用户选择 / 激活了: 合约进行中 = 激活的, 没开始 = 选好的(还没生效). */
    fun library(c: Contract): String {
        val active = c.active
        val selected = if (active) c.terms else c.draft
        val tag = if (active) "已激活" else "已选择"
        val lost = if (active) c.lost.toSet() else emptySet()
        val head = if (active) "合约进行中, 危机等级 ${Contract.levelLabel(c.level, c.origLevel)}"
        else if (selected.isNotEmpty()) "合约没有开始, 选好的词条还没生效" else "现在没有进行中的合约, 也没有选好的词条"
        val lines = ArrayList<String>()
        lines.add("【合约词条库】$head。词条后面的【$tag】= 用户选的; 【已失效】= 被违反到失效。")
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

    /** 数据都从 Core 里取; 黑雾中不给 (调用方先判断). */
    fun build(sel: CommSel, core: Core, windowName: (String) -> String): String {
        val now = LocalDateTime.now()
        val prefix = "%04d-%02d".format(now.year, now.monthValue)
        val blocks = ArrayList<String>()
        if ("log" in sel.stats) blocks.add("【统计·整点记录 $prefix】\n" + top(core.dm.hourActivitySeconds(prefix)))
        if ("net" in sel.stats) blocks.add("【统计·联网理由 $prefix】\n" + top(tallyItems(core.net.usageByReason(prefix)), minutes = true))
        if ("window" in sel.stats) {
            val out = HashMap<String, Double>()
            for ((k, v) in core.dm.windowSeconds(prefix, "all")) {
                val n = when (k) { "" -> "空白"; "?" -> "未知程序"; else -> windowName(k) }
                out[n] = (out[n] ?: 0.0) + v
            }
            blocks.add("【统计·应用 $prefix】\n" + top(out))
        }
        if ("focus" in sel.stats) {
            val today = LocalDate.now()
            val live = core.timer.live()
            val lines = (0..6).map { i ->
                val day = today.minusDays(i.toLong())
                val hours = core.dm.focusHours(day, live)
                "  %02d-%02d 周%s: 专注 %.0f 分钟, 休息 %.0f 分钟".format(day.monthValue, day.dayOfMonth, WEEKDAY[day.dayOfWeek.value - 1],
                    hours.sumOf { it.first } / 60, hours.sumOf { it.second } / 60)
            }
            blocks.add("【统计·专注计时 最近 7 天】\n" + lines.joinToString("\n"))
        }
        if ("contracts" in sel.stats) {
            val recs = Contract.recentCompleted(core.contract.history, 5)
            blocks.add("【统计·合约记录 最近完成的】\n" + if (recs.isEmpty()) "  还没有顺利完成的合约" else recs.joinToString("\n") { record(it) })
        }
        for (kind in sel.cal) {
            val name = CAL_KEYS.firstOrNull { it.first == kind }?.second ?: continue
            val days: Map<String, Double> = if (kind == "study") core.dm.monthStudy(prefix) else {
                val today = LocalDate.now()
                val live = core.timer.calendarLive()
                (0..6).map { today.minusDays(it.toLong()) }.filter { it.toString().startsWith(prefix) }
                    .associate { d -> d.toString() to (core.dm.dayKinds(d, if (d == today) live else null, now)[kind] ?: 0.0) / 3600.0 }
            }
            val lines = days.keys.sorted().filter { (days[it] ?: 0.0) > 0 }.map { "  $it: %.1f h".format(days[it]) }
            val tail = if (kind == "study") "" else " (只有最近 7 天有记录)"
            blocks.add("【日历·$name $prefix$tail】\n" + (if (lines.isEmpty()) "  没有记录" else lines.joinToString("\n")) +
                "\n  合计: %.1f h".format(days.values.sum()))
        }
        if (sel.contract) blocks.add(library(core.contract))
        return blocks.joinToString("\n\n").take(MAX_CHARS)
    }
}
