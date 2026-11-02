package com.obliphur.oit.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.time.LocalDateTime

/** 睡眠时整点记录马上填上"睡眠"并锁定 (MECHANICS 8.2 / 23). 对应桌面版 data_manager 的 fill_sleep_now. */
class SleepHourLogTest {
    private fun store() = DataStore(Files.createTempDirectory("oit").toFile(), { 600.0 }, { "Distracted" })

    @Test
    fun fillLockAndUnfill() {
        val dm = store()
        dm.hourEnabled = true
        val now = LocalDateTime.now()
        val start = now.minusHours(2).minusMinutes(10)
        dm.sleepLive = { start }
        assertTrue(dm.fillSleepNow(now))
        // 睡眠占着的每一小时 (从睡着那一小时到现在这一小时) 都填上了, 而且是固定的自动内容: 不能改
        var h = start.floorHour()
        var n = 0
        while (!h.isAfter(now)) {
            val ds = h.toLocalDate().key()
            val text = dm.hourText(ds, h.hour)
            assertEquals(dm.sleepText, text)
            assertTrue(dm.isFixedAuto(text, "$ds/${h.hour}"))
            n++
            h = h.plusHours(1)
        }
        assertTrue(n >= 3)
        assertFalse(dm.fillSleepNow(now))               // 填过的不再重复

        // 自己填过的不覆盖
        val dm2 = store()
        dm2.hourEnabled = true
        val ds = now.toLocalDate().key()
        dm2.hourDays[ds] = hashMapOf(now.hour.toString() to "学习")
        dm2.sleepLive = { now.minusMinutes(1) }
        dm2.fillSleepNow(now)
        assertEquals("学习", dm2.hourText(ds, now.hour))

        // 整点记录没开着不填
        val dm3 = store()
        dm3.hourEnabled = false
        dm3.sleepLive = { now.minusMinutes(1) }
        assertFalse(dm3.fillSleepNow(now))

        // 起床: 睡不满半小时的那一小时退回空白 (睡着时马上填的)
        val dm4 = store()
        dm4.hourEnabled = true
        val s = now.minusMinutes(1)
        dm4.sleepLive = { s }
        dm4.fillSleepNow(now)
        assertEquals(dm4.sleepText, dm4.hourText(ds, now.hour))
        dm4.sleepLive = { null }
        dm4.addFocusSession("sleep", s, s.plusMinutes(10))
        assertTrue(dm4.unfillShortSleep(s, s.plusMinutes(10)))
        assertEquals("", dm4.hourText(ds, now.hour))
    }
}
