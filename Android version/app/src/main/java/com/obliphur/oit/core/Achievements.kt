package com.obliphur.oit.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.LocalDateTime
import java.time.temporal.ChronoUnit

/** 一个成就. 名字是英文的不翻译 (中文界面和英文界面都一样). note: 作者留下的文字 (达成后显示在下方). */
data class Achievement(val id: String, val name: String, val text: String, val hidden: Boolean = false, val note: String = "")

/** 判定用的数据. study: {日期: 小时} (所有设备合计); hours: {日期: {小时: 内容}}. */
class AchFacts(
    val now: LocalDateTime, val first: LocalDateTime, val inSetup: Boolean,
    val study: Map<String, Double>, val hours: Map<String, Map<String, String>>,
)

/**
 * 成就 (MECHANICS 17, 同桌面版 achievements.py): 条件判定和存档 achievements.json.
 * 不显示进度; 触发条件只写在这里, 不显示在界面上.
 */
class AchievementStore(private val file: File, guessFirst: () -> LocalDateTime) {
    companion object {
        val ALL = listOf(
            Achievement("awake", "苏醒", "Hello World"),
            Achievement("day1", "新晋主管", "你已经度过了与OIT相伴的第一天"),
            Achievement("setup_done", "正式主管", "你完成了试用期"),
            Achievement("day7", "第七天", "你已经在专注条件下完成了一周的工作."),
            Achievement("day30", "优秀主管", "你在OIT的陪伴下完成了一个月的工作"),
            Achievement("day100", "百日的思索与决策", "一百天以来,您不断战斗着"),
            Achievement("day365", "一年的鏖战与守望", "一年以来,您的战斗不曾终止,也将继续."),
            Achievement("study4", "专注", "你第一次在一天内学习了4h."),
            Achievement("study8", "高效专注", "你在一天内学习了8h,是一个相对比较长的时间."),
            Achievement("study12", "overload", "你在一天内学习了12h.这真的是人类可以做到的吗?"),
            Achievement("sleep10", "冬眠", "睡了10h以上..."),
        )
        val BY_ID = ALL.associateBy { it.id }
        val LOG_FILES = listOf("settings_changes.log", "net_requests.log", "window_guard.log")

        fun isEnglish(s: String) = s.all { it.code < 128 }
        fun name(a: Achievement) = if (isEnglish(a.name)) a.name else tr(a.name)
        fun text(a: Achievement) = if (isEnglish(a.text)) a.text else tr(a.text)

        /** 一天的整点记录里 Sleeping 一共占了几小时 (一小时多项活动时均分, 不分大小写). */
        fun sleepHours(day: Map<String, String>): Double = day.values.sumOf { t ->
            val items = splitItems(t)
            if (items.isEmpty()) 0.0 else items.count { it.lowercase() == "sleeping" }.toDouble() / items.size
        }

        /** 按日期算, 在 00:00 达成: 第一次启动那天之后的第 days 个 00:00. */
        private fun since(days: Long): (AchFacts) -> Boolean =
            { f -> ChronoUnit.DAYS.between(f.first.toLocalDate(), f.now.toLocalDate()) >= days }

        private fun study(h: Double): (AchFacts) -> Boolean = { f -> f.study.values.any { it >= h - 1e-6 } }

        private fun sleep(h: Double): (AchFacts) -> Boolean = { f -> f.hours.values.any { sleepHours(it) >= h - 1e-6 } }

        val CHECKS: Map<String, (AchFacts) -> Boolean> = mapOf(
            "awake" to { _ -> true },
            "day1" to since(1),
            "setup_done" to { f -> !f.inSetup },
            "day7" to since(7),
            "day30" to since(30),
            "day100" to since(100),
            "day365" to since(365),
            "study4" to study(4.0),
            "study8" to study(8.0),
            "study12" to study(12.0),
            "sleep10" to sleep(10.0),
        )

        /**
         * 第一次有成就存档时 (新安装, 或者从没有成就的旧版本升级上来) 估计第一次启动的时刻:
         * 配置期的开始, 第一次打开界面的记录, 只追加的流水里最早的一行, 最早一天的记录, 取最早的.
         */
        fun guessFirstLaunch(dir: File, setupUntil: LocalDateTime?, dayKeys: Collection<String>,
                             now: LocalDateTime = LocalDateTime.now()): LocalDateTime {
            val out = mutableListOf(now)
            setupUntil?.let { out.add(it.minusHours(Cfg.SETUP_HOURS)) }
            runCatching { out.add(LocalDateTime.parse(File(dir, "permissions_asked").readText().trim())) }
            for (name in LOG_FILES) runCatching {
                val line = File(dir, name).bufferedReader(Charsets.UTF_8).use { it.readLine() } ?: return@runCatching
                out.add(LocalDateTime.parse(line.take(19), Fmt.TS))
            }
            for (ds in dayKeys) runCatching { out.add(java.time.LocalDate.parse(ds.take(10)).atStartOfDay()) }
            return out.reduce { a, b -> if (b.isBefore(a)) b else a }
        }
    }

    var first: LocalDateTime
        private set
    /** id -> 达成时刻. */
    val unlocked = LinkedHashMap<String, LocalDateTime>()
    /** 达成提示还没被关掉的 (按达成顺序). */
    val unseen = mutableListOf<String>()
    /** 重置中: 不再写文件. */
    var blocked: () -> Boolean = { false }

    init {
        val o = Store.readObj(file)
        var f: LocalDateTime? = null
        if (o != null) {
            f = parseIso(o.strOrNull("first_launch"))
            o.optJSONObject("unlocked")?.let { u ->
                for (k in u.keys()) if (k in BY_ID) parseIso(u.optString(k))?.let { unlocked[k] = it }
            }
            o.optJSONArray("unseen")?.strings()?.filter { it in unlocked && it !in unseen }?.let { unseen.addAll(it) }
        }
        first = f ?: guessFirst()
        if (o == null || f == null) save()
    }

    fun save() {
        if (blocked()) return
        val u = JSONObject()
        for ((k, v) in unlocked) u.put(k, v.iso())
        Store.write(file, JSONObject().put("first_launch", first.iso()).put("unlocked", u).put("unseen", JSONArray(unseen)))
    }

    /** 按现在的数据判定一遍, 返回这次新达成的 id. */
    fun check(f: AchFacts): List<String> {
        val new = mutableListOf<String>()
        for (a in ALL) {
            if (a.id in unlocked) continue
            if (runCatching { CHECKS[a.id]!!(f) }.getOrDefault(false)) {
                unlocked[a.id] = f.now.withNano(0)
                unseen.add(a.id)
                new.add(a.id)
            }
        }
        if (new.isNotEmpty()) save()
        return new
    }

    /** 设备同步: 别的设备达成的也算本机达成 (达成时刻取最早的). 同步来的"删除"不理会. 返回本机新达成的 id. */
    fun merge(changes: Map<String, Any?>): List<String> {
        val new = mutableListOf<String>()
        var dirty = false
        for ((id, v) in changes) {
            val t = parseIso(v?.toString()) ?: continue
            if (id !in BY_ID) continue
            val cur = unlocked[id]
            if (cur == null) {
                unlocked[id] = t
                unseen.add(id)
                new.add(id)
                dirty = true
            } else if (t.isBefore(cur)) {
                unlocked[id] = t
                dirty = true
            }
        }
        if (dirty) save()
        return new
    }

    fun markSeen(id: String) {
        if (unseen.remove(id)) save()
    }

    /** 同步用的文档: {id: 达成时刻}. */
    fun doc(): Map<String, Any?> = unlocked.mapValues { it.value.iso() }
}
