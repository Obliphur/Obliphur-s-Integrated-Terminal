package com.obliphur.oit.core

/** 人设中的声明式条件，文件内容只作数据解析。 */
object CommRules {
    fun lines(text: String, section: String): List<String> {
        val lines = text.replace("\r\n", "\n").lines()
        val heads = lines.indices.filter { lines[it].trim().equals("[$section]", true) }
        val tails = lines.indices.filter { lines[it].trim().equals("[/$section]", true) }
        if (heads.size != 1 || tails.size != 1 || heads[0] >= tails[0]) return emptyList()
        return lines.subList(heads[0] + 1, tails[0]).map { it.trim() }
            .filter { it.isNotEmpty() && !it.startsWith("#") }
    }

    fun tension(text: String): Map<String, Pair<Int?, String>> {
        val out = linkedMapOf<String, Pair<Int?, String>>()
        val pattern = Regex("""([a-z_]{1,32})\s*[:：]\s*\[(\d{1,3}|X)\]\s*(\S.*)""")
        for (line in lines(text, "tension")) {
            val m = pattern.matchEntire(line) ?: return emptyMap()
            val id = m.groupValues[1]
            val gain = if (m.groupValues[2] == "X") null else m.groupValues[2].toInt()
            if (id in out || (gain != null && gain !in 1..Cfg.COMM_TENSION_GAIN_MAX)) return emptyMap()
            out[id] = gain to m.groupValues[3]
        }
        return out
    }

    fun valence(text: String): Map<String, Pair<Int?, String>> {
        val out = linkedMapOf<String, Pair<Int?, String>>()
        val pattern = Regex("""([a-z_]{1,32})\s*[:：]\s*\[([+-]?\d{1,3}|X)\]\s*(\S.*)""")
        for (line in lines(text, "valence")) {
            val m = pattern.matchEntire(line) ?: return emptyMap()
            val id = m.groupValues[1]
            val shift = m.groupValues[2].takeIf { it != "X" }?.toInt()
            if (id in out || (shift != null && (shift == 0 || kotlin.math.abs(shift) > 40))) return emptyMap()
            out[id] = shift to m.groupValues[3]
        }
        return out
    }

    fun valenceInitial(text: String): Double {
        val entries = lines(text, "valence:initial")
        if (entries.size != 1) return 0.0
        val m = Regex("""initial\s*[:：]\s*([+-]?\d+(?:\.\d+)?)""").matchEntire(entries.single()) ?: return 0.0
        return m.groupValues[1].toDoubleOrNull()?.takeIf { it.isFinite() && it in -100.0..100.0 } ?: 0.0
    }

    data class ValenceBurst(val rate: Double = 1.0, val endShift: Int = 0)

    /** 真实联合行动完成奖励；缺省或格式错误时不加值。 */
    fun valenceEvent(text: String, event: String): Int {
        val out = mutableMapOf<String, Int>()
        val pattern = Regex("""(joint_completed)\s*[:：]\s*([+]?\d{1,3})""")
        for (line in lines(text, "valence:events")) {
            val match = pattern.matchEntire(line) ?: return 0
            val name = match.groupValues[1]
            val gain = match.groupValues[2].toInt()
            if (name in out || gain !in 0..100) return 0
            out[name] = gain
        }
        return out[event] ?: 0
    }

    fun valenceBurst(text: String): ValenceBurst {
        val entries = lines(text, "valence:burst")
        if (entries.isEmpty()) return ValenceBurst()
        var rate = 1.0
        var endShift = 0
        val seen = mutableSetOf<String>()
        for (line in entries) {
            val m = Regex("""(burst_rate|burst_end_shift)\s*[:：]\s*([+-]?\d+(?:\.\d+)?)""").matchEntire(line) ?: return ValenceBurst()
            val key = m.groupValues[1]
            val value = m.groupValues[2].toDoubleOrNull()?.takeIf { it.isFinite() } ?: return ValenceBurst()
            if (!seen.add(key)) return ValenceBurst()
            when (key) {
                "burst_rate" -> { if (value !in 0.0..5.0) return ValenceBurst(); rate = value }
                "burst_end_shift" -> { if (value % 1.0 != 0.0 || kotlin.math.abs(value) > 100) return ValenceBurst(); endShift = value.toInt() }
            }
        }
        return ValenceBurst(rate, endShift)
    }

    data class ScenarioRules(val proposeValence: Double, val ignoreValence: Double)

    /** 读取情景模拟效价阈值；未声明或格式错误时返回 null。 */
    fun scenario(text: String): ScenarioRules? {
        val entries = lines(text, "scenario")
        if (entries.isEmpty()) return null
        var propose: Double? = null
        var ignore: Double? = null
        val seen = mutableSetOf<String>()
        val pattern = Regex("""(propose_valence|ignore_valence)\s*[:：]\s*([+-]?\d+(?:\.\d+)?)""")
        for (line in entries) {
            val m = pattern.matchEntire(line) ?: return null
            val key = m.groupValues[1]
            val value = m.groupValues[2].toDoubleOrNull()?.takeIf { it.isFinite() && it in -100.0..100.0 } ?: return null
            if (!seen.add(key)) return null
            if (key == "propose_valence") propose = value else ignore = value
        }
        val p = propose ?: return null
        val i = ignore ?: return null
        return if (i <= p) ScenarioRules(p, i) else null
    }

    data class TypingDelay(val char: Double? = null, val cjk: Double? = null)

    /** 读取可选打字延迟参数（秒/字符，限 0–5）；空缺或格式错误时回退默认。 */
    fun typingDelay(text: String): TypingDelay {
        val entries = lines(text, "typing")
        if (entries.isEmpty()) return TypingDelay()
        var perChar: Double? = null
        var perCjk: Double? = null
        val seen = mutableSetOf<String>()
        val pattern = Regex("""(char|cjk)\s*[:：]\s*(\d+(?:\.\d+)?)""")
        for (line in entries) {
            val m = pattern.matchEntire(line) ?: return TypingDelay()
            val key = m.groupValues[1]
            val value = m.groupValues[2].toDoubleOrNull()?.takeIf { it.isFinite() && it in 0.0..5.0 } ?: return TypingDelay()
            if (!seen.add(key)) return TypingDelay()
            when (key) {
                "char" -> perChar = value
                "cjk" -> perCjk = value
            }
        }
        return TypingDelay(perChar, perCjk)
    }

    fun event(text: String, event: String): Pair<String, Int>? {
        val rules = tension(text)
        val out = mutableMapOf<String, Pair<String, Int>>()
        val pattern = Regex("""([a-z_]+)\s*[:：]\s*\[([a-z_]{1,32})\s*[,，]\s*(\d{1,3})\]""")
        for (line in lines(text, "tension:events")) {
            val m = pattern.matchEntire(line) ?: return null
            val (name, id, number) = m.destructured
            val gain = number.toInt()
            if (name != "joint_hp_loss" || name in out || id !in rules || gain !in 1..Cfg.COMM_TENSION_GAIN_MAX) return null
            out[name] = id to gain
        }
        return out[event]
    }

    data class TensionRelationRule(val onlyRelated: Boolean, val rate: Double)

    /** 亲密关系下的张力条件门控与增幅；格式为 条件名:[only,倍率] 或 条件名:[倍率]。 */
    fun tensionRelationship(text: String): Map<String, TensionRelationRule> {
        val rules = tension(text)
        val out = linkedMapOf<String, TensionRelationRule>()
        val pattern = Regex("""([a-z_]{1,32})\s*[:：]\s*\[\s*(?:(only)\s*[,，]\s*)?(\d+(?:\.\d+)?)\s*\]""")
        for (line in lines(text, "tension:relationship")) {
            val m = pattern.matchEntire(line) ?: return emptyMap()
            val id = m.groupValues[1]
            val onlyRelated = m.groupValues[2].isNotEmpty()
            val rate = m.groupValues[3].toDoubleOrNull()?.takeIf { it.isFinite() && it in 0.0..5.0 } ?: return emptyMap()
            if (id !in rules || id in out) return emptyMap()
            out[id] = TensionRelationRule(onlyRelated, rate)
        }
        return out
    }

    /** 缺省允许全天；起点大于终点表示跨午夜，错误区块不接受新请求。 */
    fun shiftRange(text: String): Pair<Int, Int>? {
        if (!Regex("""^\s*\[/?shift\]\s*$""", setOf(RegexOption.IGNORE_CASE, RegexOption.MULTILINE)).containsMatchIn(text)) return 0 to 1439
        val entries = lines(text, "shift")
        if (entries.size != 1) return null
        val m = Regex("""range\s*[:：]\s*\[\s*(\d{1,2})[:：](\d{2})\s*[,，]\s*(\d{1,2})[:：](\d{2})\s*\]""").matchEntire(entries[0]) ?: return null
        val (h1, m1, h2, m2) = m.destructured.toList().map { it.toInt() }
        if (h1 >= 24 || h2 >= 24 || m1 >= 60 || m2 >= 60) return null
        return (h1 * 60 + m1) to (h2 * 60 + m2)
    }

    fun shiftAllows(bounds: Pair<Int, Int>?, minutes: Int): Boolean {
        if (bounds == null || minutes !in 0 until 1440) return false
        val (lo, hi) = bounds
        return if (lo <= hi) minutes in lo..hi else minutes >= lo || minutes <= hi
    }

    data class Conditions(val values: Map<String, Double>, val enabled: Set<String>) {
        val jointDays get() = (values["joint_days"] ?: 1.0).toInt()
        val jointLv get() = (values["joint_lv"] ?: 0.0).toInt()
        val jointN get() = (values["joint_n"] ?: 0.0).toInt()
        val studyDays get() = (values["study_days"] ?: 1.0).toInt()
        val studyH get() = values["study_h"] ?: 0.0
        val idleH get() = values["idle_h"] ?: 0.0
        val msgs get() = (values["msgs"] ?: 0.0).toInt()
        val todayH get() = values["today_h"] ?: 0.0
    }

    fun conditions(text: String, strict: Boolean = false): Conditions? {
        val fields = mapOf("joint" to listOf("joint_days", "joint_lv", "joint_n"),
            "study" to listOf("study_days", "study_h", "idle_h"),
            "messages" to listOf("msgs"), "today" to listOf("today_h"))
        val out = mutableMapOf<String, Double>()
        val enabled = linkedSetOf<String>()
        for (line in lines(text, if (strict) "condition:strict" else "condition")) {
            val pair = line.split(Regex("[:：]"), limit = 2).map { it.trim() }
            if (pair.size != 2 || !enabled.add(pair[0])) return null
            val (key, value) = pair
            if (key == "period") {
                if (value != "special") return null
                continue
            }
            val names = fields[key] ?: return null
            if (names.size > 1 && !(value.startsWith("[") && value.endsWith("]"))) return null
            val values = value.trim('[', ']').replace('，', ',').split(',').map {
                it.trim().toDoubleOrNull()?.takeIf { n -> n.isFinite() && n >= 0 } ?: return null
            }
            if (values.size != names.size) return null
            for ((name, n) in names.zip(values)) {
                if (name in listOf("joint_days", "study_days") && (n !in 1.0..Cfg.COMM_CONDITION_KEEP_DAYS.toDouble() || n != Math.floor(n))) return null
                if (name in listOf("joint_lv", "joint_n", "msgs") && (n > Int.MAX_VALUE || n != Math.floor(n))) return null
                out[name] = n
            }
        }
        return if (enabled.isEmpty()) null else Conditions(out, enabled)
    }

    data class PersonaClock(val birth: String)
    data class PersonaMilestone(val id: String, val date: String, val label: String, val summary: String)
    data class LifeArc(val minDays: Int, val maxDays: Int, val stages: List<String>)
    data class LifeArcSpec(
        val id: String,
        val minDays: Int,
        val maxDays: Int,
        val stages: List<String>,
        val topics: List<String> = emptyList(),
        val whenMode: String = "",
        val whenStages: List<String> = emptyList()
    )
    data class TrendConfig(val window: Int = 7, val minDays: Int = 3, val deltaPct: Int = 15)
    data class CallbackConfig(val lookaheadDays: Int = 0, val expireDays: Int = 2, val maxDue: Int = 2)
    data class AntiRepeatConfig(val window: Int = 16, val minHits: Int = 3, val maxItems: Int = 6, val forbid: List<String> = emptyList())

    private fun parseIsoDate(raw: String): String? {
        val m = Regex("""(\d{4})-(\d{2})-(\d{2})""").matchEntire(raw.trim()) ?: return null
        val y = m.groupValues[1].toInt()
        val mo = m.groupValues[2].toInt()
        val d = m.groupValues[3].toInt()
        if (y !in 1900..2100) return null
        return runCatching { java.time.LocalDate.of(y, mo, d); String.format(java.util.Locale.US, "%04d-%02d-%02d", y, mo, d) }.getOrNull()
    }

    /** 动态年龄与生日配置 [persona:clock]：birth:YYYY-MM-DD；缺省或非法返回 null。 */
    fun personaClock(text: String): PersonaClock? {
        val entries = lines(text, "persona:clock")
        if (entries.isEmpty()) return null
        var birth: String? = null
        for (line in entries) {
            val pair = line.split(Regex("[:：]"), limit = 2).map { it.trim() }
            if (pair.size != 2 || !pair[0].equals("birth", ignoreCase = true) || birth != null) return null
            birth = parseIsoDate(pair[1]) ?: return null
        }
        return birth?.let { PersonaClock(it) }
    }

    /** 单向人生里程碑 [persona:milestones]：stage_id:[YYYY-MM-DD] 标签 | 状态说明；非法项使整组无效。 */
    fun personaMilestones(text: String): List<PersonaMilestone> {
        val out = mutableListOf<PersonaMilestone>()
        val seen = mutableSetOf<String>()
        val pattern = Regex("""([a-z][a-z0-9_]{0,31})\s*[:：]\s*\[\s*(\d{4}-\d{2}-\d{2})\s*\]\s*(\S.*)""")
        for (line in lines(text, "persona:milestones")) {
            val m = pattern.matchEntire(line) ?: return emptyList()
            val id = m.groupValues[1]
            if (!seen.add(id)) return emptyList()
            val iso = parseIsoDate(m.groupValues[2]) ?: return emptyList()
            val parts = m.groupValues[3].split('|', limit = 2).map { it.trim() }
            if (parts.size != 2 || parts[0].isEmpty() || parts[1].isEmpty()) return emptyList()
            out.add(PersonaMilestone(id, iso, parts[0].take(40), parts[1].take(240)))
        }
        out.sortWith(compareBy<PersonaMilestone> { it.date }.thenBy { it.id })
        return out
    }

    /** 角色慢变量生活线完整解析：支持可选阶段门控 (@before/@since/@stage) 与主题池自动轮换 (主题1;主题2 >> 阶段1 | 阶段2)。 */
    fun lifeArcSpecs(text: String): Map<String, LifeArcSpec> {
        val out = linkedMapOf<String, LifeArcSpec>()
        val pattern = Regex(
            """([a-z_]{1,32})(?:\(\s*@(before|since|stage)\s*[:：]\s*([a-z0-9_,\s]+)\s*\))?\s*[:：]\s*\[\s*(\d{1,2})\s*[,，]\s*(\d{1,2})\s*\]\s*(\S.*)"""
        )
        for (line in lines(text, "life:arcs")) {
            val m = pattern.matchEntire(line) ?: return emptyMap()
            val id = m.groupValues[1]
            if (id in out) return emptyMap()
            val whenMode = m.groupValues[2]
            val whenStages = if (whenMode.isNotEmpty()) {
                m.groupValues[3].split(',').map { it.trim() }.filter { it.isNotEmpty() }
            } else emptyList()
            if (whenMode.isNotEmpty() && (whenStages.isEmpty() || ((whenMode == "before" || whenMode == "since") && whenStages.size != 1))) {
                return emptyMap()
            }
            val lo = m.groupValues[4].toInt()
            val hi = m.groupValues[5].toInt()
            var body = m.groupValues[6].trim()
            var topics = emptyList<String>()
            val sepIdx = body.indexOf(">>")
            if (sepIdx >= 0) {
                val tRaw = body.substring(0, sepIdx)
                topics = tRaw.split(Regex("[;；]")).map { it.trim() }.filter { it.isNotEmpty() }
                if (topics.size !in 2..24) return emptyMap()
                body = body.substring(sepIdx + 2).trim()
            }
            val stages = body.split('|').map { it.trim() }.filter { it.isNotEmpty() }
            if (lo !in 1..60 || hi !in lo..60 || stages.size !in 2..12) return emptyMap()
            out[id] = LifeArcSpec(id, lo, hi, stages, topics, whenMode, whenStages)
        }
        return out
    }

    /** 角色慢变量生活线兼容接口。 */
    fun lifeArcs(text: String): Map<String, LifeArc> =
        lifeArcSpecs(text).mapValues { (_, s) -> LifeArc(s.minDays, s.maxDays, s.stages) }

    private fun intSection(text: String, section: String, bounds: Map<String, IntRange>, defaults: Map<String, Int>): Map<String, Int>? {
        val entries = lines(text, section)
        if (entries.isEmpty()) return null
        val out = defaults.toMutableMap()
        val seen = mutableSetOf<String>()
        for (line in entries) {
            val pair = line.split(Regex("[:：]"), limit = 2).map { it.trim() }
            if (pair.size != 2) return null
            val (key, raw) = pair
            val range = bounds[key] ?: return null
            if (!seen.add(key) || !Regex("""\d{1,3}""").matches(raw)) return null
            val value = raw.toInt()
            if (value !in range) return null
            out[key] = value
        }
        return out
    }

    fun trend(text: String): TrendConfig? {
        val m = intSection(
            text, "trend",
            mapOf("window" to 3..14, "min_days" to 2..14, "delta_pct" to 5..50),
            mapOf("window" to 7, "min_days" to 3, "delta_pct" to 15)
        ) ?: return null
        return TrendConfig(m.getValue("window"), m.getValue("min_days"), m.getValue("delta_pct"))
    }

    fun callback(text: String): CallbackConfig? {
        val m = intSection(
            text, "callback",
            mapOf("lookahead_days" to 0..3, "expire_days" to 1..7, "max_due" to 1..5),
            mapOf("lookahead_days" to 0, "expire_days" to 2, "max_due" to 2)
        ) ?: return null
        return CallbackConfig(m.getValue("lookahead_days"), m.getValue("expire_days"), m.getValue("max_due"))
    }

    fun antiRepeat(text: String): AntiRepeatConfig? {
        val entries = lines(text, "anti_repeat")
        if (entries.isEmpty()) return null
        val bounds = mapOf("window" to 6..30, "min_hits" to 2..6, "max_items" to 2..10)
        var window = 16
        var minHits = 3
        var maxItems = 6
        var forbid = emptyList<String>()
        val seen = mutableSetOf<String>()
        for (line in entries) {
            val pair = line.split(Regex("[:：]"), limit = 2).map { it.trim() }
            if (pair.size != 2) return null
            val (key, raw) = pair
            if (!seen.add(key)) return null
            if (key == "forbid") {
                val words = raw.split('|').map { it.trim() }.filter { it.isNotEmpty() }
                if (words.size > 20 || words.any { it.length > 16 }) return null
                forbid = words
            } else {
                val range = bounds[key] ?: return null
                if (!Regex("""\d{1,3}""").matches(raw)) return null
                val value = raw.toInt()
                if (value !in range) return null
                when (key) {
                    "window" -> window = value
                    "min_hits" -> minHits = value
                    "max_items" -> maxItems = value
                }
            }
        }
        return AntiRepeatConfig(window, minHits, maxItems, forbid)
    }

    data class ConsentRule(val level: Double, val label: String)

    /** 标准化爆发同意声明区块 [burst:consent]：每行 id:[3|4|4.5] 说明文字（档位可省略，默认 3），最多 12 项；非法项使整组无效。 */
    fun burstConsentItems(text: String): LinkedHashMap<String, ConsentRule> {
        val out = linkedMapOf<String, ConsentRule>()
        val pattern = Regex("""([a-z_]{1,32})\s*[:：]\s*(?:\[(3|4(?:\.5)?)\]\s*)?(\S.*)""")
        for (line in lines(text, "burst:consent")) {
            val m = pattern.matchEntire(line) ?: return linkedMapOf()
            val id = m.groupValues[1]
            val level = m.groupValues[2].toDoubleOrNull() ?: 3.0
            val label = m.groupValues[3].trim()
            if (id in out || label.isEmpty() || label.length > 80 || out.size >= 12) return linkedMapOf()
            out[id] = ConsentRule(level, label)
        }
        return out
    }

    fun burstConsent(text: String): LinkedHashMap<String, String> =
        LinkedHashMap(burstConsentItems(text).mapValues { it.value.label })
}
