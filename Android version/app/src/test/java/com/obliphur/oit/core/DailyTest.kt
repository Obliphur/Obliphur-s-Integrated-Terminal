package com.obliphur.oit.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.LocalDate
import java.time.LocalDateTime

/** 睡觉时间 (设置 → 日常, MECHANICS 19). 对应桌面版 daily.py 的 selftest. */
class DailyTest {

    private fun settings(): Settings =
        Settings(File(Files.createTempDirectory("oit").toFile(), "settings.json"))

    private fun file(): File = File(Files.createTempDirectory("oit").toFile(), "daily.json")

    private fun at(y: Int, m: Int, d: Int, h: Int, mi: Int): LocalDateTime =
        LocalDateTime.of(y, m, d, h, mi)

    @Test
    fun sleepMath() {
        val s = settings()
        // 默认每天都是 23:00; 2026-10-19 是周一
        assertEquals(at(2026, 10, 19, 23, 0), lastSleep(s, at(2026, 10, 19, 23, 0)))
        assertEquals(at(2026, 10, 18, 23, 0), lastSleep(s, at(2026, 10, 19, 22, 59)))
        assertEquals(at(2026, 10, 19, 23, 0), nextSleep(s, at(2026, 10, 19, 20, 0)))
        assertEquals(at(2026, 10, 20, 23, 0), nextSleep(s, at(2026, 10, 19, 23, 0)))
        // 周六 01:30 才睡
        s.effective["SLEEP_SAT"] = 90.0
        assertEquals(at(2026, 10, 23, 23, 0), lastSleep(s, at(2026, 10, 24, 1, 0)))
        assertEquals(at(2026, 10, 24, 1, 30), lastSleep(s, at(2026, 10, 24, 2, 0)))
    }

    @Test
    fun windowCrossesMidnight() {
        val s = settings()
        val d = Daily(file(), s)
        assertTrue(d.inWindow(at(2026, 10, 19, 23, 30)))
        assertTrue(d.inWindow(at(2026, 10, 20, 1, 59)))
        assertFalse(d.inWindow(at(2026, 10, 20, 2, 0)))
        assertFalse(d.inWindow(at(2026, 10, 19, 20, 0)))
        assertEquals(LocalDate.of(2026, 10, 19), d.windowDay(at(2026, 10, 20, 1, 30)))
        assertEquals(LocalDate.of(2026, 10, 19), d.windowDay(at(2026, 10, 20, 5, 0)))
    }

    @Test
    fun bedtimeAndDelay() {
        val s = settings()
        val d = Daily(file(), s)
        assertEquals(at(2026, 10, 19, 23, 0), d.bedtime(at(2026, 10, 19, 20, 0)))
        assertEquals(at(2026, 10, 19, 23, 0), d.bedtime(at(2026, 10, 19, 23, 30)))
        assertEquals(at(2026, 10, 19, 23, 0), d.bedtime(at(2026, 10, 20, 1, 0)))
        assertEquals(at(2026, 10, 20, 23, 0), d.bedtime(at(2026, 10, 20, 2, 30)))
        assertEquals(180, d.delayMinutes(at(2026, 10, 20, 2, 0), at(2026, 10, 19, 23, 0)))
        assertEquals(60, d.delayMinutes(at(2026, 10, 20, 0, 0), at(2026, 10, 19, 23, 0)))
        assertEquals(0, d.delayMinutes(at(2026, 10, 19, 22, 0), at(2026, 10, 19, 20, 0)))
    }

    @Test
    fun oncePerSleepWindow() {
        val d = Daily(file(), settings())
        assertTrue(d.takeBreakNotice(at(2026, 10, 19, 23, 10)))
        assertFalse("同一个睡觉窗口只弹一次", d.takeBreakNotice(at(2026, 10, 20, 0, 30)))
        assertFalse("窗口外不弹", d.takeBreakNotice(at(2026, 10, 20, 20, 0)))
        assertTrue(d.takeBreakNotice(at(2026, 10, 20, 23, 10)))
    }

    @Test
    fun contractDelayOncePerDay() {
        val d = Daily(file(), settings())
        assertEquals(240, d.contractDelay(at(2026, 10, 20, 3, 0), at(2026, 10, 19, 23, 0)))
        assertEquals("每天只一次", 0, d.contractDelay(at(2026, 10, 20, 4, 0), at(2026, 10, 19, 23, 5)))
        assertEquals("白天开始合约: 比的是今晚", 60,
            d.contractDelay(at(2026, 10, 21, 0, 0), at(2026, 10, 20, 20, 0)))
        val d2 = Daily(file(), settings())
        assertEquals(0, d2.contractDelay(at(2026, 10, 19, 22, 0), at(2026, 10, 19, 20, 0)))
        assertEquals("不弹的时候不写标记", "", d2.flag("contract_sleep"))
    }

    @Test
    fun breakRateBonus() {
        assertEquals(0.735, Daily(file(), settings()).breakRate(0.7), 1e-9)
    }

    @Test
    fun timeSettingRoundTrip() {
        val s = settings()
        assertEquals("23:00", s.uiNumber("SLEEP_MON"))
        assertEquals("23:00", s.display("SLEEP_MON"))
        assertEquals(1350, s.fromUiNumber("SLEEP_MON", "22:30"))
        assertEquals(1425, s.fromUiNumber("SLEEP_MON", "23:45"))
        assertEquals(0, s.fromUiNumber("SLEEP_MON", "0"))
        assertNull(s.validate("SLEEP_TUE", 1439.0))
        assertNotNull(s.validate("SLEEP_TUE", 1440.0))
        assertNotNull(s.validate("SLEEP_TUE", 1350.toInt()))
        val (r, _) = s.stage("SLEEP_WED", s.fromUiNumber("SLEEP_WED", "22:30"))
        assertEquals(StageResult.STAGED, r)
        assertEquals(1350.0, s.pending["SLEEP_WED"]!!.value as Double, 1e-9)
        // 日常页: 行为模式 / 两个自动记录文本是选项, 周一到周日 7 项睡觉时间是数值
        val (opts, nums) = Schema.pageSpecs("daily")
        assertEquals(listOf("BEHAVIOR_MODE", "OUTING_TEXT", "REST_TEXT"), opts.map { it.key })
        assertEquals(Cfg.SLEEP_KEYS, nums.map { it.key })
        assertTrue(nums.all { it.kind == Kind.TIME })
        // 都市之意主题下多出六项指令时段 (文字, 即时生效, 可以留空); 其他主题看不到
        val (urbanOpts, _) = Schema.pageSpecs("daily", "urban_will")
        assertEquals(opts.map { it.key } + Cfg.PRESCRIPT_WIN_KINDS.map { "PRESCRIPT_WIN_$it" }, urbanOpts.map { it.key })
        assertTrue(urbanOpts.drop(3).all { it.windows && !it.delayed })
        assertNotNull(s.validate("PRESCRIPT_WIN_VII", "坏的"))
        assertNull(s.validate("PRESCRIPT_WIN_VII", ""))
        assertNull(s.validate("PRESCRIPT_WIN_VII", "04:00-06:00,15:00-16:00"))
        assertEquals(StageResult.INSTANT, s.stage("PRESCRIPT_WIN_VII", "04:00-06:00").first)
        assertEquals("04:00-06:00", s.str("PRESCRIPT_WIN_VII"))
    }
}
