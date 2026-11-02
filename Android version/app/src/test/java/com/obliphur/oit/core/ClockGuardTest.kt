package com.obliphur.oit.core

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** 系统时间被改 (MECHANICS 9.6). 对应桌面版 clockguard.py 的 _selftest. */
class ClockGuardTest {

    /** 假的时钟: 系统时间 = offset + u (u 是开机以来的秒); shift 改系统时间 (不动 u). */
    private class Clock(wall: Double = 1_800_000_000.0, var u: Double = 1000.0) {
        var offset = wall - u
        fun wall() = offset + u
        fun run(secs: Double) { u += secs }
        fun shift(secs: Double) { offset += secs }
    }

    private var store: JSONObject? = null

    private fun mk(c: Clock): ClockGuard {
        val g = ClockGuard({ c.u }, { c.wall() }, load = { store?.let { JSONObject(it.toString()) } }, save = { store = JSONObject(it.toString()) })
        g.start()
        return g
    }

    private fun run(g: ClockGuard, c: Clock, secs: Int): List<String> {
        val out = mutableListOf<String>()
        repeat(secs) {
            c.run(1.0)
            g.tick()?.let { out.add(it) }
        }
        return out
    }

    @Test
    fun warnRestoreAndWipe() {
        store = null
        var c = Clock()
        var g = mk(c)
        assertTrue(run(g, c, 120).isEmpty())
        c.shift(200.0)                                  // 3 分钟的小调整: 不理
        assertTrue(run(g, c, 5).isEmpty())
        c.run(8 * 3600.0)                               // 睡了 8 小时 (u 和系统时间一起走)
        assertNull(g.tick())
        assertTrue(run(g, c, 3).isEmpty())
        // 往前改 3 天: 警告; 10 分钟内改回来 → 解除
        c.shift(3 * 86400.0)
        assertEquals("warn", g.tick())
        assertEquals("jump", g.warn!!.kind)
        assertTrue(run(g, c, 120).isEmpty())
        c.shift(-3 * 86400.0)
        assertEquals("restored", g.tick())
        assertNull(g.warn)
        assertTrue(run(g, c, 60).isEmpty())
        // 24 小时内再被改 (哪怕改回来了): 直接清数据
        c.shift(-2 * 3600.0)
        assertEquals("wipe", g.tick())
        assertNull(g.tick())                            // 只发一次

        // 警告之后 10 分钟没改回来 → 清数据
        store = null
        c = Clock()
        g = mk(c)
        run(g, c, 10)
        c.shift(-86400.0)
        assertEquals("warn", g.tick())
        assertEquals(listOf("wipe"), run(g, c, (ClockGuard.GRACE + 5).toInt()))

        // 倒计时里又改了一次 → 清数据
        store = null
        c = Clock()
        g = mk(c)
        run(g, c, 10)
        c.shift(86400.0)
        assertEquals("warn", g.tick())
        assertTrue(run(g, c, 100).isEmpty())
        c.shift(7200.0)
        assertEquals("wipe", g.tick())
    }

    @Test
    fun restartAndReboot() {
        store = null
        var c = Clock()
        var g = mk(c)
        run(g, c, 10)
        c.shift(86400.0)
        assertEquals("warn", g.tick())
        run(g, c, 100)
        // 重启 (同一次开机): 还没改回来 → 接着倒数; 改回来了 → 解除
        val g2 = mk(c)
        assertNotNull(g2.warn)
        assertTrue(g2.warn!!.left > 0 && g2.warn!!.left < ClockGuard.GRACE)
        assertNull(g2.tick())
        c.shift(-86400.0)
        val g3 = mk(c)
        assertEquals("restored", g3.tick())
        assertNull(g3.warn)

        // 重新开机之后: 按离改之前最后的时间有多远估计
        store = null
        c = Clock()
        g = mk(c)
        run(g, c, 10)
        c.shift(86400.0)
        assertEquals("warn", g.tick())
        val saved = JSONObject(store.toString())
        store = JSONObject(saved.toString())
        val stillBad = mk(Clock(wall = c.wall() + 300, u = 50.0))
        assertNotNull(stillBad.warn)
        assertNull(stillBad.tick())
        store = JSONObject(saved.toString())
        val fixed = mk(Clock(wall = c.wall() - 86400 + 300, u = 50.0))
        assertNull(fixed.warn)
        assertEquals("restored", fixed.tick())

        // 程序没开着的时候往回改: 警告, 回到记着的时间之后解除
        store = null
        c = Clock()
        g = mk(c)
        run(g, c, 60)
        val last = c.wall()
        val c2 = Clock(wall = last - 5 * 86400, u = c.u + 30)
        val back = mk(c2)
        assertEquals("warn", back.tick())
        assertEquals("back", back.warn!!.kind)
        c2.shift(5 * 86400.0 + 60)
        assertEquals("restored", back.tick())
    }

    @Test
    fun noUptimeNoGuard() {
        val g = ClockGuard({ null })
        g.start()
        assertNull(g.tick())
        assertFalse(g.on)
    }
}
