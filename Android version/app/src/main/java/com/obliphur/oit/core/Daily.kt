package com.obliphur.oit.core

import org.json.JSONObject
import java.io.File
import java.time.LocalDate
import java.time.LocalDateTime
import kotlin.math.ceil

/** 睡觉时间之后 3 小时内第一次暂停休息时弹的文字 (MECHANICS 19). */
const val SLEEP_TEXT = "你好,主管{name},我是Obliphur.到了应该睡觉的时间了.\n" +
    "睡眠的时间会比较大幅度地影响明天的学习/工作.\n" +
    "现在你的暂停休息倍率有+5%的修正.\n" +
    "晚安."

/** 合约结束时间晚于"这次要睡的时间"时的确认文字. */
const val LATE_TEXT = "如果正常完成行动 会延迟你的睡眠至少 {n} min 你真的要开始吗?"

private const val BREAK_FLAG = "sleep_break"        // 这个睡觉窗口里已经弹过提示 (存窗口那天的日期)
private const val CONTRACT_FLAG = "contract_sleep"  // 这天按"开始行动"时已经弹过睡眠提醒 (存日期)

/**
 * 睡觉时间 (设置 → 日常) 和跟着它走的两个提示 (MECHANICS 19). 对应桌面版 daily.py.
 *
 * - 最近一次睡觉时间之后 3 小时内, 非合约期间第一次暂停休息: 弹提示, 那次休息的结转比例 +5%.
 * - 合约结束时间晚于"这次要睡的时间"时, 当天第一次按"开始行动"先弹一次确认.
 *
 * 两个标记各自每个睡觉窗口 / 每天最多一次, 存在数据目录的 daily.json (只记日期, 不同步).
 */
class Daily(private val file: File, private val settings: Settings) {

    fun flag(key: String): String = Store.readObj(file)?.optString(key)?.trim() ?: ""

    fun setFlag(key: String, day: String) {
        val o = Store.readObj(file) ?: JSONObject()
        o.put(key, day)
        Store.write(file, o)
    }

    /** 现在落在哪个睡觉窗口: 窗口那天的日期 (= 最近一次睡觉时间所在的那天). */
    fun windowDay(now: LocalDateTime = LocalDateTime.now()): LocalDate =
        lastSleep(settings, now).toLocalDate()

    /** 现在是不是在最近一次睡觉时间之后的 SLEEP_WINDOW 秒之内. */
    fun inWindow(now: LocalDateTime = LocalDateTime.now()): Boolean =
        now.isBefore(lastSleep(settings, now).plusSeconds(Cfg.SLEEP_WINDOW))

    /** 这次"要睡的时间": 还在睡觉后的窗口里 → 最近那次 (已经晚了); 已经过了窗口 → 下一次. */
    fun bedtime(now: LocalDateTime = LocalDateTime.now()): LocalDateTime {
        val last = lastSleep(settings, now)
        return if (now.isBefore(last.plusSeconds(Cfg.SLEEP_WINDOW))) last else nextSleep(settings, now)
    }

    /** 合约结束时间比"这次要睡的时间"晚多少分钟 (向上取整); 不比它晚时 0. */
    fun delayMinutes(end: LocalDateTime, now: LocalDateTime = LocalDateTime.now()): Int {
        val sec = secondsBetween(bedtime(now), end)
        if (sec <= 0) return 0
        return maxOf(1, ceil(sec / 60.0).toInt())
    }

    /** 窗口内那次休息的暂停结转比例 (+5%). */
    fun breakRate(rate: Double): Double = rate * (1.0 + Cfg.SLEEP_BREAK_BONUS)

    /** 这次暂停休息要不要弹提示 (调用方先确认不在合约里); 要弹的时候顺手写上标记. */
    fun takeBreakNotice(now: LocalDateTime = LocalDateTime.now()): Boolean {
        val day = windowDay(now).key()
        if (!inWindow(now) || flag(BREAK_FLAG) == day) return false
        setFlag(BREAK_FLAG, day)
        return true
    }

    /** 开始合约前要不要弹睡眠提醒: 要弹时返回至少会延迟的分钟数 (并写上今天的标记), 否则 0. */
    fun contractDelay(end: LocalDateTime, now: LocalDateTime = LocalDateTime.now()): Int {
        val n = delayMinutes(end, now)
        val today = now.toLocalDate().key()
        if (n <= 0 || flag(CONTRACT_FLAG) == today) return 0
        setFlag(CONTRACT_FLAG, today)
        return n
    }

    fun sleepText(name: String): String = tr(SLEEP_TEXT, "name" to name)

    fun lateText(minutes: Int): String = tr(LATE_TEXT, "n" to maxOf(1, minutes))
}
