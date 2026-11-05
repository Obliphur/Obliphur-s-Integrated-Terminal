package com.obliphur.oit.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.LocalDateTime

/** 闹钟 (MECHANICS 25). 对应桌面版 alarm.py 的 _selftest. */
class AlarmTest {
    private fun at(d: Int, h: Int, m: Int, s: Int = 0, month: Int = 6) = LocalDateTime.of(2027, month, d, h, m, s)

    @Test
    fun parseAndHelpers() {
        assertEquals(7 to 5, AlarmStore.parseHm("7:05")); assertEquals(7 to 5, AlarmStore.parseHm(" 07 ： 05 "))
        assertNull(AlarmStore.parseHm("24:00")); assertNull(AlarmStore.parseHm("7:60")); assertNull(AlarmStore.parseHm("x"))
        assertEquals(at(1, 7, 0), AlarmStore.nextTime(7 to 0, at(1, 6, 59, 59)))
        assertEquals(at(2, 7, 0), AlarmStore.nextTime(7 to 0, at(1, 7, 0)))                 // 同一分钟已经过了: 明天
        assertEquals(1f, AlarmStore.gainOf(100), 0f); assertEquals(0f, AlarmStore.gainOf(0), 0f); assertEquals(0.25f, AlarmStore.gainOf(50), 1e-6f)
        assertEquals("明天", AlarmStore.dayWord(at(2, 6, 0), at(1, 23, 0))); assertEquals("6月9日", AlarmStore.dayWord(at(9, 6, 0), at(1, 23, 0)))
    }

    @Test
    fun ringLogic() {
        val td = Files.createTempDirectory("alarm").toFile()
        var clock = at(1, 6, 0)
        val f = File(td, "alarm.json")
        val a = AlarmStore(f) { clock }
        assertFalse(a.on); assertEquals("07:00", a.time); assertEquals("classic", a.tone); assertEquals(80, a.volume)
        assertNull(a.check(clock)); assertNull(a.nextRing(clock)); assertEquals("闹钟已关闭", a.statusText(clock))
        a.set(on = true, time = "6:30")
        assertEquals("06:30", a.time); assertEquals(at(1, 6, 30), a.nextRing(clock)); assertTrue("今天 06:30" in a.statusText(clock))
        a.set(time = "bad", volume = 250, tone = "nope")
        assertEquals("06:30", a.time); assertEquals(100, a.volume); assertEquals("classic", a.tone)       // 不合法的输入不改 / 音量夹在 0-100
        clock = at(1, 6, 29, 59); assertNull(a.check(clock))
        clock = at(1, 6, 30, 0)
        val hit = a.check(clock)!!
        assertEquals(at(1, 6, 30), hit.at); assertFalse(hit.co)
        assertNull(a.check(clock))                                                              // 响过的不再响
        assertEquals(at(2, 6, 30), a.nextRing(clock))                                           // 每天重复
        val b = AlarmStore(f) { clock }                                                         // 存盘, 重新打开不会再响一次
        assertEquals("06:30", b.time); assertNull(b.check(clock))
        // 错过: 超过 15 分钟才发现 = 这次不响
        clock = at(2, 6, 50)
        assertNull(a.check(clock)); assertEquals(at(3, 6, 30), a.nextRing(clock))
        clock = at(3, 6, 40)
        assertNotNull(a.check(clock)); assertNull(a.check(clock))
        // 改时间 / 开关: 之前过去的钟点不补响
        clock = at(3, 12, 0)
        a.set(time = "11:00")
        assertNull(a.check(clock)); assertEquals(at(4, 11, 0), a.nextRing(clock))
        a.set(on = false)
        clock = at(4, 11, 0); assertNull(a.check(clock))
        // 共时闹钟: 时间由 coFn 给, 开关不影响; 换对象 (sig) 重新计时
        val wakes = listOf(at(5, 6, 12), at(6, 6, 25))
        val co = { after: LocalDateTime -> wakes.firstOrNull { it.isAfter(after) } }
        clock = at(4, 23, 0)
        assertNull(a.check(clock, co, "A"))
        assertEquals(at(5, 6, 12), a.nextRing(clock, co)); assertTrue("明天 06:12" in a.statusText(clock, co, "Claudia") && "共时闹钟" in a.statusText(clock, co, "Claudia"))
        clock = at(5, 6, 12, 1)
        val h2 = a.check(clock, co, "A")!!
        assertTrue(h2.co); assertEquals(at(5, 6, 12), h2.at); assertNull(a.check(clock, co, "A"))
        clock = at(6, 6, 25, 30)
        assertNotNull(a.check(clock, co, "A")); assertNull(a.check(clock, co, "A"))
        assertNull(a.check(clock, { null }, "A"))                                               // 算不出下一次起床 = 不响
        a.set(on = true, time = "07:00")
        clock = at(6, 12, 0)
        assertNull(a.check(clock, null, ""))                                                    // 关掉共时闹钟: 回到自己的闹钟, 重新计时
        clock = at(7, 7, 0)
        assertNotNull(a.check(clock)); assertEquals(at(8, 7, 0), a.nextRing(clock))
        assertTrue(a.nextRingMs(clock) > 0 && AlarmStore(File(td, "none.json")) { clock }.nextRingMs(clock) == 0L)
    }
}
