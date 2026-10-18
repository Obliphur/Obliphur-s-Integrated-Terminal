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

    // 应用使用时间统计: 每隔这么多秒看一次前台应用
    const val APP_FOCUS_EVERY = 10.0

    const val WEEKDAY_KEYS_CSV = "QUOTA_MULT_MON,QUOTA_MULT_TUE,QUOTA_MULT_WED,QUOTA_MULT_THU,QUOTA_MULT_FRI,QUOTA_MULT_SAT,QUOTA_MULT_SUN"
    val WEEKDAY_KEYS = WEEKDAY_KEYS_CSV.split(",")

    fun weekdayKey(day: LocalDate): String = WEEKDAY_KEYS[day.dayOfWeek.value - 1]
    fun weekdayKey(d: DayOfWeek): String = WEEKDAY_KEYS[d.value - 1]
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
