package com.obliphur.oit.core

import java.time.LocalDate
import java.time.temporal.ChronoUnit
import java.util.Locale
import java.util.zip.CRC32
import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * 拟真增强（声明式模板驱动）：角色慢变量生活线、延迟回调与跨周趋势、工程层防复读。
 * 与桌面端 comm/comm_realism.py 保持一致的算法和 CRC32 种子。
 */
object CommRealism {
    private val epoch2024: LocalDate = LocalDate.of(2024, 1, 1)
    private val callbackRe = Regex("""^\s*(?:-\s*)?\[(?:回访\s*[:：]|@)\s*(\d{4}-\d{2}-\d{2})\s*\]\s*(.+?)\s*$""")
    private val cjkRunRe = Regex("""[\u4e00-\u9fff]+""")
    private val stripEdgeRe = Regex("""^[\s，。！？、；：“”‘’"'()（）\[\]【】《》—…·.,!?;:~`]+|[\s，。！？、；：“”‘’"'()（）\[\]【】《》—…·.,!?;:~`]+$""")
    private val sentenceSplitRe = Regex("""[。！？!?；;\n]+""")

    private val stopPhrases = setOf(
        "你的", "我的", "你是", "我是", "不是", "没有", "可以", "什么", "这个", "那个", "自己",
        "现在", "如果", "还是", "已经", "不要", "应该", "只是", "一个", "这么", "那么", "这样", "那样",
        "因为", "所以", "但是", "不过", "而且", "或者", "虽然", "今天", "明天", "昨天", "时候", "事情",
        "一点", "一些", "一下", "这些", "那些", "他们", "我们", "你们", "这里", "那里", "如何", "怎么",
        "为什么", "没什么", "不需要", "不用", "不会", "不能", "不想", "觉得", "知道", "明白", "确实",
        "其实", "比较", "有点", "可能", "大概", "继续", "开始", "结束", "时间", "问题", "办法", "状态",
        "的话", "就好", "就行", "而已", "罢了", "之前", "之后", "刚才", "目前", "暂时", "自然"
    )

    private fun crc32(seed: String): Long {
        val c = CRC32()
        c.update(seed.toByteArray(Charsets.UTF_8))
        return c.value and 0xFFFFFFFFL
    }

    fun lifeArcsNote(text: String, ownerName: String, today: LocalDate): String {
        val arcs = CommRules.lifeArcs(text)
        if (arcs.isEmpty()) return ""
        val owner = ownerName.trim().lowercase(Locale.ROOT)
        val dayOrd = ChronoUnit.DAYS.between(epoch2024, today)
        val ids = arcs.keys.toList()
        val focusIdx = (crc32("$owner:$today:focus") % ids.size.toLong()).toInt()
        val lines = mutableListOf("[OIT_LIFE_ARC] 你近期的个人生活进展(自然融入闲聊或主动话题，勿透露机制或编号):")
        ids.forEachIndexed { idx, arcId ->
            val arc = arcs.getValue(arcId)
            val span = arc.minDays + (crc32("$owner:$arcId:span") % (arc.maxDays - arc.minDays + 1).toLong()).toInt()
            val offset = (crc32("$owner:$arcId:off") % span.toLong()).toInt()
            val pos = Math.floorMod(dayOrd + offset, span.toLong()).toInt()
            val stageIdx = minOf(arc.stages.size - 1, (pos * arc.stages.size) / span)
            val mark = if (idx == focusIdx) "★今日焦点 " else ""
            lines.add("- $mark$arcId(第${pos + 1}/${span}天): ${arc.stages[stageIdx]}")
        }
        return lines.joinToString("\n")
    }

    fun weeklyTrendNote(text: String, condDays: Map<String, Pair<Long, Long>>, today: LocalDate, shareEnabled: Boolean): String {
        if (!shareEnabled) return ""
        val cfg = CommRules.trend(text) ?: return ""
        val curKeys = (1..cfg.window).map { today.minusDays(it.toLong()).toString() }
        val prevKeys = ((cfg.window + 1)..(cfg.window * 2)).map { today.minusDays(it.toLong()).toString() }
        val curVals = curKeys.mapNotNull { condDays[it] }
        if (curVals.size < cfg.minDays) return ""
        val prevVals = prevKeys.mapNotNull { condDays[it] }

        val curStudyAvg = curVals.sumOf { it.first }.toDouble() / curVals.size / 3600.0
        val curIdleAvg = curVals.sumOf { it.second }.toDouble() / curVals.size / 3600.0
        val parts = mutableListOf(
            String.format(Locale.US, "近%d天(记录%d天)日均专注 %.1fh, 日均空闲 %.1fh", cfg.window, curVals.size, curStudyAvg, curIdleAvg)
        )

        if (prevVals.size >= cfg.minDays) {
            val prevStudyAvg = prevVals.sumOf { it.first }.toDouble() / prevVals.size / 3600.0
            if (prevStudyAvg >= 0.2) {
                val pct = (((curStudyAvg - prevStudyAvg) / prevStudyAvg) * 100.0).roundToInt()
                val label = when {
                    pct >= cfg.deltaPct -> "上升"
                    pct <= -cfg.deltaPct -> "下降"
                    else -> "持平"
                }
                parts.add(String.format(Locale.US, "较前%d天 %+d%%(%s)", cfg.window, pct, label))
            } else if (curStudyAvg >= 0.5) {
                parts.add("较前${cfg.window}天明显回升")
            }
        }

        val recentChron = curKeys.asReversed().mapNotNull { condDays[it]?.first }
        if (recentChron.size >= 3) {
            val a = recentChron[recentChron.size - 3]
            val b = recentChron[recentChron.size - 2]
            val c = recentChron[recentChron.size - 1]
            if (a < b && b < c && (c - a) >= 1800L) parts.add("近3日专注连续走高")
            else if (a > b && b > c && (a - c) >= 1800L) parts.add("近3日专注连续走低")
        }

        condDays[today.toString()]?.let {
            parts.add(String.format(Locale.US, "今日已专注 %.1fh", it.first / 3600.0))
        }
        return "[OIT_WEEKLY_TREND: ${parts.joinToString("；")}]"
    }

    fun callbackNote(text: String, memLive: String, today: LocalDate): String {
        val cfg = CommRules.callback(text) ?: return ""
        if (memLive.isBlank()) return ""
        data class Due(val diff: Int, val dateStr: String, val whenLabel: String, val body: String)
        val due = mutableListOf<Due>()
        val expired = mutableListOf<Pair<String, String>>()
        for (rawLine in memLive.lines()) {
            val m = callbackRe.matchEntire(rawLine) ?: continue
            val target = runCatching { LocalDate.parse(m.groupValues[1]) }.getOrNull() ?: continue
            val diff = ChronoUnit.DAYS.between(target, today).toInt()
            val body = m.groupValues[2].trim()
            if (diff in -cfg.lookaheadDays..cfg.expireDays) {
                val whenLabel = when {
                    diff == 0 -> "今天到期"
                    diff == 1 -> "昨天到期(可顺口追问结果)"
                    diff > 1 -> "${diff}天前到期(可顺口追问结果)"
                    else -> "${-diff}天后临近"
                }
                due.add(Due(diff, m.groupValues[1], whenLabel, body))
            } else if (diff > cfg.expireDays) {
                expired.add(m.groupValues[1] to body)
            }
        }
        if (due.isEmpty() && expired.isEmpty()) return ""
        due.sortWith(compareBy<Due> { abs(it.diff) }.thenBy { it.dateStr })
        val lines = mutableListOf("[OIT_CALLBACK_DUE] 待回访事项(自然时机顺口提及，问过之后用 oit_memory_edit 移除):")
        for (item in due.take(cfg.maxDue)) {
            lines.add("- [${item.dateStr}] ${item.whenLabel}: ${item.body}")
        }
        if (expired.isNotEmpty()) {
            val expItems = expired.take(3).joinToString("；") { "[${it.first}] ${it.second}" }
            lines.add("- 已过期可清理: $expItems")
        }
        return lines.joinToString("\n")
    }

    fun antiRepeatNote(text: String, aiTextsAll: List<String>): String {
        val cfg = CommRules.antiRepeat(text) ?: return ""
        val aiTexts = aiTextsAll.map { it.trim() }.filter { it.isNotEmpty() }.takeLast(cfg.window)
        if (aiTexts.isEmpty()) return ""

        val openCounts = linkedMapOf<String, Int>()
        val endCounts = linkedMapOf<String, Int>()
        val phraseCounts = linkedMapOf<String, Int>()
        val forbidHits = linkedMapOf<String, Int>()

        for (body in aiTexts) {
            for (word in cfg.forbid) {
                if (word in body) forbidHits[word] = (forbidHits[word] ?: 0) + 1
            }
            val msgOpens = linkedSetOf<String>()
            val msgEnds = linkedSetOf<String>()
            val msgPhrases = linkedSetOf<String>()

            val firstClean = stripEdgeRe.replace(body.trim(), "")
            cjkRunRe.matchAt(firstClean, 0)?.let { m ->
                val run = m.value
                if (run.length >= 2) {
                    val head = run.substring(0, 2)
                    if (head !in stopPhrases) msgOpens.add(head)
                }
            }

            for (sent in sentenceSplitRe.split(body)) {
                val sc = stripEdgeRe.replace(sent.trim(), "")
                if (sc.length < 4) continue
                val runs = cjkRunRe.findAll(sc).map { it.value }.toList()
                if (runs.isNotEmpty() && runs.last().length >= 2) {
                    val lastRun = runs.last()
                    val tail = lastRun.substring(lastRun.length - minOf(3, lastRun.length))
                    if (tail !in stopPhrases) msgEnds.add(tail)
                }
                for (run in runs) {
                    val rlen = run.length
                    for (n in 3..4) {
                        if (rlen < n) continue
                        for (i in 0..(rlen - n)) {
                            val gram = run.substring(i, i + n)
                            if (gram !in stopPhrases) msgPhrases.add(gram)
                        }
                    }
                }
            }

            for (k in msgOpens) openCounts[k] = (openCounts[k] ?: 0) + 1
            for (k in msgEnds) endCounts[k] = (endCounts[k] ?: 0) + 1
            for (k in msgPhrases) phraseCounts[k] = (phraseCounts[k] ?: 0) + 1
        }

        val rawPhrases = phraseCounts.entries
            .filter { it.value >= cfg.minHits }
            .sortedWith(compareByDescending<Map.Entry<String, Int>> { it.key.length }.thenByDescending { it.value }.thenBy { it.key })
        val keptPhrases = mutableListOf<Pair<String, Int>>()
        for ((p, c) in rawPhrases) {
            if (keptPhrases.any { (kp, kc) -> p in kp && kc >= c }) continue
            keptPhrases.add(p to c)
        }
        keptPhrases.sortWith(compareByDescending<Pair<String, Int>> { it.second }.thenByDescending { it.first.length }.thenBy { it.first })

        val edgeMin = cfg.minHits + 1
        val topOpens = openCounts.entries.filter { it.value >= edgeMin }.sortedByDescending { it.value }
        val topEnds = endCounts.entries.filter { it.value >= edgeMin }.sortedByDescending { it.value }

        val hints = mutableListOf<String>()
        val openKeys = topOpens.take(2).map { it.key }
        for (e in topOpens.take(2)) hints.add("开头「${e.key}」×${e.value}")
        for ((k, c) in keptPhrases) {
            if (hints.size >= cfg.maxItems) break
            if (k in openKeys) continue
            hints.add("短语「$k」×$c")
        }
        for (e in topEnds.take(2)) {
            if (hints.size >= cfg.maxItems) break
            if (hints.any { e.key in it }) continue
            hints.add("结尾「${e.key}」×${e.value}")
        }

        val forbidPart = if (forbidHits.isNotEmpty()) {
            val hitList = forbidHits.entries.sortedByDescending { it.value }.joinToString("、") { "${it.key}×${it.value}" }
            "；近期误用禁用词($hitList，本轮务必避开)"
        } else ""

        if (hints.isEmpty() && forbidPart.isEmpty()) return ""
        val bodyText = if (hints.isNotEmpty()) hints.joinToString("、") else "无高频短语"
        return "[OIT_ANTI_REPEAT: 近期高频表达(请换用其他说法，避免复读): $bodyText$forbidPart]"
    }

    fun realismNote(
        personaText: String,
        ownerName: String,
        today: LocalDate,
        aiTexts: List<String>,
        memLive: String,
        condDays: Map<String, Pair<Long, Long>>,
        shareEnabled: Boolean
    ): String = listOf(
        lifeArcsNote(personaText, ownerName, today),
        weeklyTrendNote(personaText, condDays, today, shareEnabled),
        callbackNote(personaText, memLive, today),
        antiRepeatNote(personaText, aiTexts)
    ).filter { it.isNotEmpty() }.joinToString("\n")
}
