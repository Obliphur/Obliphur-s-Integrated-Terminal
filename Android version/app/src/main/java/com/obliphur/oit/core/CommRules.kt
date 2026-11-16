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
}
