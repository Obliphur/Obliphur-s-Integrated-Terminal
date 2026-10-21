package com.obliphur.oit.core

import java.time.DayOfWeek
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import kotlin.math.ceil
import kotlin.math.ln

/** 不在设置页里的固定数值 (对应桌面版 config.py). 可调的数值见 Settings.kt. */
object Cfg {
    const val APP_NAME = "Obliphur's Integrated Terminal"

    const val APPLY_DELAY_HOURS = 48L
    const val SETUP_HOURS = 72L

    // 紧急借贷借掉明天额度的这个比例以上时: 提醒增加休息时间, 并让下一次修改每日联网额度免等待
    const val LOAN_WARN_SHARE = 0.5

    // 休息结束后迟迟不开始下一轮的提醒
    const val IDLE_NUDGE_FIRST = 5 * 60L
    const val IDLE_NUDGE_EVERY = 60 * 60L

    // 专注中的"还在吗"
    const val FOCUS_CHECK_EVERY = 90 * 60.0
    const val FOCUS_CHECK_GRACE = 30 * 60.0
    const val FOCUS_CHECK_PENALTY = 0.3

    // 专注状态写盘与恢复
    const val FOCUS_SAVE_EVERY = 4 * 60.0
    const val FOCUS_RESUME_GAP = 5 * 60.0
    /** 绑定了其他设备时 (MECHANICS 8.2): 专注中这么久写一次; OIT 被关掉超过 CLOSED_GAP 再打开, 共享的专注终止,
     *  关掉的这段记为空闲; 先等 CLOSED_DECIDE_WAIT 秒内的第一次同步, 看其他设备是不是已经结束了这段专注. */
    const val FOCUS_ALIVE_EVERY = 30.0
    const val CLOSED_GAP = 2 * 60.0
    const val CLOSED_DECIDE_WAIT = 60.0

    // 连点领取休息时间
    const val BONUS_BREAK_CLICKS = 10
    const val BONUS_BREAK_WINDOW = 60.0
    const val BONUS_BREAK_SECONDS = 20 * 60
    const val BONUS_BREAK_WEEKLY = 2 * 3600
    /** 一周内第 5 次领"我累了": 弹窗劝调休息时间倍率, 它的下一次修改免等待 (MECHANICS 6.5). */
    const val BONUS_TIRED_TIMES = 5
    /** 用户名最多几个字. */
    const val USERNAME_MAX = 24
    const val VIOLATION_KEEP = 15        // 违规记录只留最近这么多条 (MECHANICS 8.1)

    // 应用使用时间统计: 每隔这么多秒看一次前台应用
    const val APP_FOCUS_EVERY = 10.0
    /** 专注时每隔多久数一次后台的应用 (超过上限就提醒并响一次违约音效, 之后每 OPEN_APPS_REPEAT 秒再响). */
    const val OPEN_APPS_EVERY = 10.0
    const val OPEN_APPS_REPEAT = 120.0

    const val WEEKDAY_KEYS_CSV = "QUOTA_MULT_MON,QUOTA_MULT_TUE,QUOTA_MULT_WED,QUOTA_MULT_THU,QUOTA_MULT_FRI,QUOTA_MULT_SAT,QUOTA_MULT_SUN"
    val WEEKDAY_KEYS = WEEKDAY_KEYS_CSV.split(",")

    fun weekdayKey(day: LocalDate): String = WEEKDAY_KEYS[day.dayOfWeek.value - 1]
    fun weekdayKey(d: DayOfWeek): String = WEEKDAY_KEYS[d.value - 1]

    // 睡觉时间 (设置 → 日常): 周一到周日各自可以改, 默认 23:00 (从 0:00 起的分钟数).
    // 睡觉时间之后 SLEEP_WINDOW 秒里第一次暂停休息: 弹提示, 那次休息的结转比例 +SLEEP_BREAK_BONUS;
    // 合约结束时间晚于"这次要睡的时间"时, 当天第一次按"开始行动"先弹一次确认 (MECHANICS 19).
    const val SLEEP_DEFAULT = 23 * 60
    const val SLEEP_WINDOW = 3 * 3600L
    const val SLEEP_BREAK_BONUS = 0.05
    const val SLEEP_KEYS_CSV = "SLEEP_MON,SLEEP_TUE,SLEEP_WED,SLEEP_THU,SLEEP_FRI,SLEEP_SAT,SLEEP_SUN"
    val SLEEP_KEYS = SLEEP_KEYS_CSV.split(",")

    fun sleepKey(day: LocalDate): String = SLEEP_KEYS[day.dayOfWeek.value - 1]
    fun sleepKey(d: DayOfWeek): String = SLEEP_KEYS[d.value - 1]

    // 行为模式 (设置 → 日常, MECHANICS 21): 第一次打开时可以选, 之后修改要等 24 × 7 小时.
    const val BEHAVIOR_CLOSED = "closed"
    const val BEHAVIOR_WORK = "work"
    const val BEHAVIOR_FREE = "free"
    val BEHAVIOR_MODES = listOf(BEHAVIOR_CLOSED, BEHAVIOR_WORK, BEHAVIOR_FREE)
    const val BEHAVIOR_DELAY_HOURS = 24 * 7L
    const val OUTING_TEXT = "外出"
    const val REST_TEXT = "休息"
    // 每天最多记 10 小时; 7:00 ~ 睡觉时间 (SLEEP_*) 之内的部分按 80% 专注 / 20% 休息填充.
    const val OUTING_WINDOW_START = 7 * 60
    const val OUTING_DAILY_CAP = 10 * 3600.0
    const val OUTING_FOCUS_SHARE = 0.8
    // 外出结束: 被记录的小时数 × 6 分钟, 最多 3 小时.
    const val OUTING_REST_PER_HOUR = 6 * 60.0
    const val OUTING_REST_MAX = 3 * 3600
    // 外出期间: 当天的联网额度 ×0.2, 联网冷静期 ×0.3.
    const val OUTING_QUOTA_FACTOR = 0.2
    const val OUTING_COOLDOWN_FACTOR = 0.3
    const val OUTING_MAX_HOURS = 24 * 7
}

/** 建议休息时长 F(t), t 与结果都以分钟计 (MECHANICS 6.2). */
fun computeF(t: Double): Double = when {
    t <= 15.0 -> t / 6.0
    t < 150.0 -> (7.0 / 14850.0) * t * t + (407.0 / 1485.0) * t - (170.0 / 99.0)
    else -> 50.0 + (5.0 / ln(1.2)) * ln(t / 150.0)
}

fun fmtHm(sec: Double): String {
    val s = maxOf(0L, sec.toLong())
    return "${s / 3600}h %02dm".format((s % 3600) / 60)
}

fun fmtMs(sec: Double): String {
    val s = maxOf(0L, ceil(sec).toLong())
    return "%02d:%02d".format(s / 60, s % 60)
}

/** 从 0:00 起的分钟数 → "HH:MM" (设置 → 日常 的睡觉时间). */
fun slotText(minutes: Any?): String {
    val raw = (minutes as? Number)?.toDouble() ?: return minutes?.toString() ?: ""
    val m = Math.round(raw).coerceIn(0L, (24 * 60 - 1).toLong()).toInt()
    return "%02d:%02d".format(m / 60, m % 60)
}

/** 填写框里的时间 → 分钟数. 认 "23:00" / "23：00" / "2300" / "23"; 认不出来时 null (stage 会拒绝). */
fun parseSlot(text: String): Int? {
    val t = text.trim().replace("：", ":").replace(" ", "")
    if (t.isEmpty()) return null
    val h: Int
    val m: Int
    if (t.contains(":")) {
        val head = t.substringBefore(":")
        val tail = t.substringAfter(":")
        if (head.isEmpty() || !head.all { it.isDigit() }) return null
        if (tail.isNotEmpty() && !tail.all { it.isDigit() }) return null
        h = head.toInt()
        m = if (tail.isEmpty()) 0 else tail.toInt()
    } else {
        if (!t.all { it.isDigit() }) return null
        if (t.length <= 2) {
            h = t.toInt(); m = 0
        } else {
            h = t.dropLast(2).toInt(); m = t.takeLast(2).toInt()
        }
    }
    if (h !in 0..23 || m !in 0..59) return null
    return h * 60 + m
}

/** 某一天的睡觉时间: 从 0:00 起的分钟数 (设置 → 日常, MECHANICS 19). */
fun sleepMinutes(settings: Settings, day: LocalDate): Int =
    settings.num(Cfg.sleepKey(day)).toInt().coerceIn(0, 24 * 60 - 1)

fun sleepTimeOn(settings: Settings, day: LocalDate): LocalDateTime =
    day.atStartOfDay().plusMinutes(sleepMinutes(settings, day).toLong())

/** 现在之前最近的一次睡觉时刻; 凌晨还没睡时算的是昨天那次 (跨零点). */
fun lastSleep(settings: Settings, now: LocalDateTime): LocalDateTime {
    val t = sleepTimeOn(settings, now.toLocalDate())
    return if (!t.isAfter(now)) t else sleepTimeOn(settings, now.toLocalDate().minusDays(1))
}

/** 现在之后最近的一次睡觉时刻. */
fun nextSleep(settings: Settings, now: LocalDateTime): LocalDateTime {
    val t = sleepTimeOn(settings, now.toLocalDate())
    return if (t.isAfter(now)) t else sleepTimeOn(settings, now.toLocalDate().plusDays(1))
}

/** 去掉多余的 0, 最多 4 位小数 (同 Python 的 f"{round(x, 4):g}"). */
fun numText(x: Double): String {
    val r = Math.round(x * 10000.0) / 10000.0
    if (r == Math.floor(r) && kotlin.math.abs(r) < 1e15) return r.toLong().toString()
    return r.toBigDecimal().stripTrailingZeros().toPlainString()
}

fun f1(x: Double) = "%.1f".format(x)
fun f2(x: Double) = "%.2f".format(x)

object Fmt {
    val DAY: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd")
    val HM: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm")
    val MDHM: DateTimeFormatter = DateTimeFormatter.ofPattern("MM-dd HH:mm")
    val YMDHM: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")
    val TS: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")
    val ISO: DateTimeFormatter = DateTimeFormatter.ISO_LOCAL_DATE_TIME
}

fun LocalDate.key(): String = format(Fmt.DAY)
fun LocalDateTime.iso(): String = withNano(0).format(Fmt.ISO)
fun parseIso(text: String?): LocalDateTime? =
    try { if (text.isNullOrBlank()) null else LocalDateTime.parse(text) } catch (e: Exception) { null }
fun LocalDateTime.floorHour(): LocalDateTime = withMinute(0).withSecond(0).withNano(0)
fun LocalDateTime.floorDay(): LocalDateTime = toLocalDate().atStartOfDay()
fun secondsBetween(a: LocalDateTime, b: LocalDateTime): Double =
    java.time.Duration.between(a, b).toMillis() / 1000.0
fun LocalDateTime.plusSec(s: Double): LocalDateTime = plusNanos((s * 1e9).toLong())

/** (本月, 上月) 的 'YYYY-MM' 前缀; 数据只保留这两个月. */
fun monthPrefixes(now: LocalDate = LocalDate.now()): Pair<String, String> {
    val last = now.minusMonths(1)
    return "%d-%02d".format(now.year, now.monthValue) to "%d-%02d".format(last.year, last.monthValue)
}

/** 一个小时 / 一次申请的多项活动用 / 连接, 如 '学习/吃饭'. */
fun splitItems(text: String?): List<String> =
    (text ?: "").replace("／", "/").split("/").map { it.trim() }.filter { it.isNotEmpty() }

/** [(文本, 秒)] → {活动: 秒}; 按 / 拆分均分, 同名 (忽略大小写) 合并. */
fun tallyItems(pairs: Iterable<Pair<String, Double>>): Map<String, Double> {
    val totals = LinkedHashMap<String, Double>()
    val names = HashMap<String, String>()
    for ((text, secs) in pairs) {
        val items = splitItems(text)
        if (items.isEmpty() || secs <= 0) continue
        val share = secs / items.size
        for (item in items) {
            val k = item.lowercase()
            names.putIfAbsent(k, item)
            totals[k] = (totals[k] ?: 0.0) + share
        }
    }
    return totals.entries.associate { names[it.key]!! to it.value }
}
