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
    private val callbackRe = Regex("""^\s*(?:-\s*)?(?:\[#[A-Za-z][A-Za-z0-9_-]{0,11}\]\s*)?\[(?:回访\s*[:：]|@)\s*(\d{4}-\d{2}-\d{2})\s*\]\s*(.+?)\s*$""")
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

    private fun safeBirthdayInYear(birth: LocalDate, year: Int): LocalDate =
        runCatching { LocalDate.of(year, birth.monthValue, birth.dayOfMonth) }.getOrElse { LocalDate.of(year, 2, 28) }

    private data class MilestoneState(
        val active: CommRules.PersonaMilestone?,
        val reached: Set<String>,
        val upcoming: CommRules.PersonaMilestone?
    )

    private fun milestoneState(milestones: List<CommRules.PersonaMilestone>, today: LocalDate): MilestoneState {
        val reached = mutableSetOf<String>()
        var active: CommRules.PersonaMilestone? = null
        var upcoming: CommRules.PersonaMilestone? = null
        val todayIso = today.toString()
        for (item in milestones) {
            if (item.date <= todayIso) {
                reached.add(item.id)
                active = item
            } else if (upcoming == null) {
                upcoming = item
            }
        }
        return MilestoneState(active, reached, upcoming)
    }

    private fun arcAllowed(spec: CommRules.LifeArcSpec, activeId: String, reached: Set<String>): Boolean = when (spec.whenMode) {
        "" -> true
        "before" -> spec.whenStages.firstOrNull()?.let { it !in reached } ?: true
        "since" -> spec.whenStages.firstOrNull()?.let { it in reached } ?: true
        "stage" -> activeId.isNotEmpty() && activeId in spec.whenStages
        else -> true
    }

    private fun blockShuffle(owner: String, arcId: String, block: Long, count: Int): IntArray {
        val order = IntArray(count) { it }
        for (i in count - 1 downTo 1) {
            val j = (crc32("$owner:$arcId:shuf:$block:$i") % (i + 1).toLong()).toInt()
            val tmp = order[i]
            order[i] = order[j]
            order[j] = tmp
        }
        return order
    }

    private fun pickTopicIndex(owner: String, arcId: String, cycle: Long, count: Int): Int {
        if (count <= 1) return 0
        if (count == 2) {
            val base = (crc32("$owner:$arcId:t0") % 2L)
            return Math.floorMod(cycle + base, 2L).toInt()
        }
        val block = Math.floorDiv(cycle, count.toLong())
        val slot = Math.floorMod(cycle, count.toLong()).toInt()
        val order = blockShuffle(owner, arcId, block, count)
        if (block > 0L) {
            val prevLast = blockShuffle(owner, arcId, block - 1L, count).last()
            if (order[0] == prevLast) {
                val tmp = order[0]
                order[0] = order[1]
                order[1] = tmp
            }
        }
        return order[slot]
    }

    fun personaEvolutionNote(text: String, today: LocalDate): String {
        val clock = CommRules.personaClock(text)
        val milestones = CommRules.personaMilestones(text)
        if (clock == null && milestones.isEmpty()) return ""

        val lines = mutableListOf(
            "[OIT_PERSONA_EVOLUTION] 角色当前时间线状态" +
                "(初始人设正文仅代表初始设定，凡年龄、学历/身份或近况与此处冲突时，一律以本区块为准，自然融入言行勿透露机制):"
        )
        var hasItem = false

        if (clock != null) {
            val birth = LocalDate.parse(clock.birth)
            val bdayThis = safeBirthdayInYear(birth, today.year)
            val age = today.year - birth.year - (if (today.isBefore(bdayThis)) 1 else 0)
            if (age >= 0) {
                val bdayHint = if (today == bdayThis) {
                    "，今天正好是你${age}岁生日"
                } else {
                    val nextBday = if (today.isBefore(bdayThis)) bdayThis else safeBirthdayInYear(birth, today.year + 1)
                    val daysLeft = ChronoUnit.DAYS.between(today, nextBday).toInt()
                    if (daysLeft in 1..7) "，距离你${age + 1}岁生日还有${daysLeft}天" else ""
                }
                lines.add(String.format(Locale.US, "- 当前年龄: %d岁（生日 %02d-%02d%s）", age, birth.monthValue, birth.dayOfMonth, bdayHint))
                hasItem = true
            }
        }

        if (milestones.isNotEmpty()) {
            val ms = milestoneState(milestones, today)
            ms.active?.let {
                lines.add("- 当前人生阶段: ${it.label}（自 ${it.date} 起）— ${it.summary}")
                hasItem = true
            }
            ms.upcoming?.let {
                val daysTo = ChronoUnit.DAYS.between(today, LocalDate.parse(it.date)).toInt()
                if (daysTo in 1..14) {
                    lines.add("- 临近节点: 距离「${it.label}」（${it.date}）还有${daysTo}天")
                    hasItem = true
                }
            }
        }

        return if (hasItem) lines.joinToString("\n") else ""
    }

    fun lifeArcsNote(text: String, ownerName: String, today: LocalDate): String {
        val specs = CommRules.lifeArcSpecs(text)
        if (specs.isEmpty()) return ""
        val ms = milestoneState(CommRules.personaMilestones(text), today)
        val activeId = ms.active?.id ?: ""
        val ids = specs.entries.filter { arcAllowed(it.value, activeId, ms.reached) }.map { it.key }
        if (ids.isEmpty()) return ""

        val owner = ownerName.trim().lowercase(Locale.ROOT)
        val dayOrd = ChronoUnit.DAYS.between(epoch2024, today)
        val focusIdx = (crc32("$owner:$today:focus") % ids.size.toLong()).toInt()
        val lines = mutableListOf("[OIT_LIFE_ARC] 你近期的个人生活进展(自然融入闲聊或主动话题，活动结束后自动切换新项，勿透露机制或编号):")
        ids.forEachIndexed { idx, arcId ->
            val spec = specs.getValue(arcId)
            val span = spec.minDays + (crc32("$owner:$arcId:span") % (spec.maxDays - spec.minDays + 1).toLong()).toInt()
            val offset = (crc32("$owner:$arcId:off") % span.toLong()).toInt()
            val total = dayOrd + offset.toLong()
            val cycle = Math.floorDiv(total, span.toLong())
            val pos = Math.floorMod(total, span.toLong()).toInt()
            val stageIdx = minOf(spec.stages.size - 1, (pos * spec.stages.size) / span)
            var stageText = spec.stages[stageIdx]
            var transition = ""
            if (spec.topics.isNotEmpty()) {
                val curTopic = spec.topics[pickTopicIndex(owner, arcId, cycle, spec.topics.size)]
                stageText = stageText.replace("{topic}", curTopic)
                if (pos == 0) {
                    val prevTopic = spec.topics[pickTopicIndex(owner, arcId, cycle - 1L, spec.topics.size)]
                    transition = " [刚开启新项，上一项「$prevTopic」已收尾]"
                } else if (pos == span - 1) {
                    transition = " [今日收尾，完成后将开启下一项]"
                }
            } else {
                if (pos == 0) {
                    transition = " [新轮次开启，换新内容勿重复上轮]"
                } else if (pos == span - 1) {
                    transition = " [今日收尾，完成后将开启下一项]"
                }
            }
            val mark = if (idx == focusIdx) "★今日焦点 " else ""
            lines.add("- $mark$arcId(第${pos + 1}/${span}天): $stageText$transition")
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
        personaEvolutionNote(personaText, today),
        lifeArcsNote(personaText, ownerName, today),
        weeklyTrendNote(personaText, condDays, today, shareEnabled),
        callbackNote(personaText, memLive, today),
        antiRepeatNote(personaText, aiTexts)
    ).filter { it.isNotEmpty() }.joinToString("\n")
}
