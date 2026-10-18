package com.obliphur.oit.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.LocalDateTime

/** 合约 (MECHANICS 18), 同桌面版 contract.py 的自检. */
class ContractTest {
    private fun tmp(): File = File(Files.createTempDirectory("oit").toFile(), "contract.json")

    @Test
    fun terms() {
        assertEquals(8, Contract.levelOf(listOf("long3", "isolate", "last")))
        assertEquals(20, Contract.restMinutes(0)); assertEquals(24, Contract.restMinutes(3))
        assertEquals(28, Contract.restMinutes(4)); assertEquals(44, Contract.restMinutes(18))
        assertEquals(50 * 60, Contract.restReward(19))
        assertTrue(Contract.conflicts("long1", "long3") && Contract.conflicts("isolate", "info2"))
        assertFalse(Contract.conflicts("chan2", "isolate") || Contract.conflicts("isolate", "chan1"))
        assertTrue(Contract.conflicts("suppress", "isolate"))
        assertFalse(Contract.conflicts("jam", "music"))
        assertEquals(listOf("long2", "isolate", "jam"), Contract.clean(listOf("long2", "long1", "x", "isolate", "suppress", "jam")))
        assertEquals(listOf("isolate", "chan2"), Contract.clean(listOf("isolate", "chan2")))
        val best = Contract.clean(listOf("long3", "move2", "info3", "suppress", "jam", "music", "fog", "tired3", "chan2", "last"))
        assertEquals(21, Contract.levelOf(best))
    }

    @Test
    fun softRules() {
        val c = Contract(tmp())
        val t0 = LocalDateTime.of(2026, 10, 18, 9, 30)
        c.start(listOf("tired2", "info2", "chan1", "long1", "music"), "me", t0)
        assertEquals(8, c.level)
        assertEquals(t0.plusSeconds((4 * 3600 * 1.1).toLong()), c.endAt)
        // 信息阻断-II: 开始时剩 100 分钟, 只能用 50 分钟; 用超了 → 降成 -I, 多给 25 分钟
        assertNull(c.netCheck(600.0, 0.0, 6000.0, "2026-10-18"))
        assertEquals(3000.0, c.netLeft(600.0)!!, 1e-6)
        assertEquals("info2" to "over", c.netCheck(3700.0, 0.0, 0.0, "2026-10-18"))
        assertEquals(false to "info1", c.violate("info2"))
        assertEquals(7, c.level); assertEquals(8, c.origLevel)
        assertEquals(1400.0, c.netLeft(3700.0)!!, 1e-6)
        assertEquals("info1" to "loan", c.netCheck(3800.0, 600.0, 0.0, "2026-10-18"))
        assertNull(c.netCheck(3800.0, 600.0, 0.0, "2026-10-18"))
        // 乐理之灾: 只在刚打开的那一下算, 降一级, 再降就失效
        assertEquals("music", c.musicCheck(true)); assertEquals("", c.musicCheck(true)); c.musicCheck(false)
        assertEquals("music", c.downgrade("music")); assertEquals(1, c.termLevel("music"))
        assertEquals("", c.downgrade("music")); assertFalse(c.has("music"))
        // 休息: 疲惫 ×0.75; 完成时按降级后的等级给固定的时间
        assertEquals(750.0, c.bookRest(1000.0), 1e-6)
        assertEquals(Contract.restMinutes(c.level) * 60.0, c.reward, 1e-6)
        // 空闲 30 分钟
        assertNull(c.due(t0.plusHours(1), true))
        assertEquals("idle" to t0.plusMinutes(90), c.due(t0.plusMinutes(91), true))
        // 整点记录的锁定
        assertFalse(c.hourLocked(LocalDateTime.of(2026, 10, 18, 9, 0), LocalDateTime.of(2026, 10, 18, 10, 59)))
        assertTrue(c.hourLocked(LocalDateTime.of(2026, 10, 18, 9, 0), LocalDateTime.of(2026, 10, 18, 11, 0)))
        assertFalse(c.hourLocked(LocalDateTime.of(2026, 10, 18, 8, 0), LocalDateTime.of(2026, 10, 18, 12, 0)))
        // 3 次结束
        c.start(listOf("suppress", "info3", "long1"), "me", t0)
        assertEquals("suppress", c.quickCheck())
        assertEquals(false to "suppress", c.violate("suppress"))
        assertEquals(false to "", c.violate("suppress"))
        assertEquals(true to "info2", c.violate("info3"))
        val rec = c.finish("failed", t0.plusHours(2))!!
        assertEquals(0, rec.optInt("reward")); assertEquals(3, rec.optInt("level")); assertEquals(6, rec.optInt("orig_level"))
        assertFalse(c.takeFirstBreak())
    }

    @Test
    fun completeAndSync() {
        val f = tmp()
        val c = Contract(f)
        val t0 = LocalDateTime.of(2026, 10, 18, 9, 30)
        c.start(listOf("last", "tired1"), "me", t0)
        assertEquals(1, c.violationLimit)
        assertEquals(540.0, c.bookRest(600.0), 1e-6)
        assertEquals(24 * 60.0, c.reward, 1e-6)
        assertEquals("completed" to t0.plusHours(4), c.due(t0.plusHours(4), false))
        val rec = c.finish("completed", t0.plusHours(4).plusMinutes(3))!!
        assertEquals(24 * 60, rec.optInt("reward"))
        assertEquals(0.0, c.reward, 1e-6)
        assertEquals(540.0, c.bookRestAfter(600.0, t0.plusHours(3)), 1e-6)
        assertEquals(600.0, c.bookRestAfter(600.0, t0.plusHours(5)), 1e-6)
        assertTrue(c.takeFirstBreak()); assertFalse(c.takeFirstBreak())
        assertEquals(0.9, Contract.firstBreakRate(0.8), 1e-9)
        val d = Contract(tmp())
        d.merge(c.doc()); d.merge(c.doc())
        assertEquals(1, d.history.size)
        assertEquals(1, Contract(f).history.size)
    }
}
