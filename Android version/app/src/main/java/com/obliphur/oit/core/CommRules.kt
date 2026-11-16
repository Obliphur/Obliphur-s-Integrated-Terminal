package com.obliphur.oit.core

/** 人设中的声明式条件，文件内容只作数据解析。 */
object CommRules {
    val README_ZH = """
条件模板（与 routine 一样使用字段:数值/列表；放在人设正文或任意配套文件中，每个区块仅一份）：
[tension]
delight:[20] 用户分享让当前人设欣喜的真实进展
[/tension]
[tension:events]
joint_hp_loss:[delight,15]
[/tension:events]
张力条件名用 1–32 个小写英文字母/下划线，数值为 1–40 的参考基础增量，后面写触发语义；模型只可报告已定义的条件。没有有效 [tension] 时聊天不增加张力。可选的 tension:events 把真实合约扣血事件绑定到已定义条件，未声明不增长；此处是事件的实际基础增量，与聊天共用该条件冷却。
[condition]
joint:[30,15,24]
study:[60,420,210]
messages:500
today:8
period:special
[/condition]
[condition:strict]
messages:1000
period:special
[/condition:strict]
condition 用于 [condition] 查询；condition:strict 用于进入特殊空闲时的主动告白。joint:[天数,最低难度,完成场数]；study:[天数,最低专注小时,最高空闲小时]；messages:最低双方消息总数；today:今天最低专注小时；period:special 要求当前为特殊空闲。只判定写出的项目，全部满足才通过；天数 1–70（含今天），未知字段、重复项、负数、缺失或空区块均视为配置无效，不触发主动告白。可用 # 开头写整行注释。只查询到的真实记录参与判定；不执行任何文件代码。旧自定义人设请补充模板，系统不猜测自然语言条件。未改过的内置人设随正常升级更新。
"""
    val README_EN = """
Rule templates use field:value/list syntax like routine files. Put them in the persona or any companion file, with one complete block of each kind:
[tension]
delight:[20] The user shares real progress that delights this persona
[/tension]
[tension:events]
joint_hp_loss:[delight,15]
[/tension:events]
Tension IDs contain 1–32 lowercase ASCII letters/underscores. The value is a reference base gain from 1 to 40; the text describes the trigger. The model may report only declared IDs. Without a valid tension block, chat adds no tension. The optional events block binds actual joint-contract HP loss to a declared ID and base gain; an omitted binding adds nothing and configured events share that ID's cooldown with chat.
[condition]
joint:[30,15,24]
study:[60,420,210]
messages:500
today:8
period:special
[/condition]
[condition:strict]
messages:1000
period:special
[/condition:strict]
condition is used by the [condition] query; condition:strict is checked for proactive confession on entering special free time. joint:[days,minimum level,completed count]; study:[days,minimum focus hours,maximum idle hours]; messages:minimum total messages; today:minimum focus hours today; period:special requires special free time. Only declared fields are checked and all must pass. Days range from 1 to 70 including today. Unknown/duplicate fields, negative values, missing or empty blocks are invalid and never trigger proactive confession. Whole-line # comments are allowed. Only actual records are evaluated; file content never executes as code. Add templates to older custom personas; natural-language conditions are not guessed. Unmodified built-in personas update through the existing upgrade process.
"""
    fun lines(text: String, section: String): List<String> {
        val lines = text.replace("\r\n", "\n").lines()
        val heads = lines.indices.filter { lines[it].trim().equals("[$section]", true) }
        val tails = lines.indices.filter { lines[it].trim().equals("[/$section]", true) }
        if (heads.size != 1 || tails.size != 1 || heads[0] >= tails[0]) return emptyList()
        return lines.subList(heads[0] + 1, tails[0]).map { it.trim() }
            .filter { it.isNotEmpty() && !it.startsWith("#") }
    }

    fun tension(text: String): Map<String, Pair<Int, String>> {
        val out = linkedMapOf<String, Pair<Int, String>>()
        val pattern = Regex("""([a-z_]{1,32})\s*[:：]\s*\[(\d{1,3})\]\s*(\S.*)""")
        for (line in lines(text, "tension")) {
            val m = pattern.matchEntire(line) ?: return emptyMap()
            val id = m.groupValues[1]
            val gain = m.groupValues[2].toInt()
            if (id in out || gain !in 1..Cfg.COMM_TENSION_GAIN_MAX) return emptyMap()
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
