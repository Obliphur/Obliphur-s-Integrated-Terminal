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
        assertTrue(Contract.conflicts("short1", "short2")); assertFalse(Contract.conflicts("seal", "fine"))
        assertTrue(Contract.GROUPS.indexOf("short") < Contract.GROUPS.indexOf("suppress"))
        assertEquals(listOf("long2", "isolate", "jam"), Contract.clean(listOf("long2", "long1", "x", "isolate", "suppress", "jam")))
        assertEquals(listOf("isolate", "chan2"), Contract.clean(listOf("isolate", "chan2")))
        val best = Contract.clean(listOf("long3", "move2", "info3", "suppress", "jam", "music", "fog", "tired3", "chan2", "last",
            "short2", "seal", "fine"))
        assertEquals(24, Contract.levelOf(best))
        assertEquals(4 * 3600 * 1.6, Contract.durationOf(listOf("long3")), 1e-6)
    }

    @Test
    fun softRules() {
        val c = Contract(tmp())
        val t0 = LocalDateTime.of(2026, 10, 18, 9, 30)
        c.start(listOf("tired2", "info2", "chan1", "long1", "music"), "me", t0)
        assertEquals(7, c.level)
        assertEquals(t0.plusSeconds((4 * 3600 * 1.2).toLong()), c.endAt)
        // 信息阻断-II: 开始时剩 100 分钟, 只能用 50 分钟; 用超了 → 降成 -I, 多给 25 分钟
        assertNull(c.netCheck(600.0, 0.0, 6000.0, "2026-10-18"))
        assertEquals(3000.0, c.netLeft(600.0)!!, 1e-6)
        assertEquals("info2" to "over", c.netCheck(3700.0, 0.0, 0.0, "2026-10-18"))
        assertEquals(false to "info1", c.violate("info2"))
        assertEquals(6, c.level); assertEquals(7, c.origLevel)
        assertEquals(1400.0, c.netLeft(3700.0)!!, 1e-6)
        assertEquals("info1" to "loan", c.netCheck(3800.0, 600.0, 0.0, "2026-10-18"))
        assertNull(c.netCheck(3800.0, 600.0, 0.0, "2026-10-18"))
        // 乐理之灾: 只在刚打开的那一下算; 只有 1 级, 降一级就失效
        assertEquals("music", c.musicCheck(true)); assertEquals("", c.musicCheck(true)); c.musicCheck(false)
        assertEquals("", c.downgrade("music")); assertFalse(c.has("music"))
        // 休息: 疲惫 ×0.75; 完成时按降级后的等级给固定的时间
        assertEquals(750.0, c.bookRest(1000.0), 1e-6)
        assertEquals(Contract.restMinutes(c.level) * 60.0, c.reward, 1e-6)
        // 空闲 30 分钟
        assertNull(c.due(t0.plusHours(1), true))
        assertEquals("idle" to t0.plusMinutes(90), c.due(t0.plusMinutes(91), true))
        // 3 次结束; 整点记录超时未填写也算 (不降词条)
        c.start(listOf("suppress", "info3", "long1"), "me", t0)
        assertEquals("suppress", c.quickCheck())
        assertEquals(false to "", c.violate("suppress")); assertFalse(c.has("suppress"))
        assertEquals(false to "", c.violate("")); assertEquals(2, c.violations)
        assertEquals(true to "info2", c.violate("info3"))
        val rec = c.finish("failed", t0.plusHours(2))!!
        assertEquals(0, rec.optInt("reward")); assertEquals(3, rec.optInt("level")); assertEquals(5, rec.optInt("orig_level"))
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
        assertEquals(0.85, Contract.firstBreakRate(0.7), 1e-9)
        val d = Contract(tmp())
        d.merge(c.doc()); d.merge(c.doc())
        assertEquals(1, d.history.size)
        assertEquals(1, Contract(f).history.size)
    }

    @Test
    fun supplyAndHours() {
        val c = Contract(tmp())
        val t0 = LocalDateTime.of(2026, 10, 18, 9, 30)
        // 合约开始前积攒 20 分钟 + 支援短缺-II: 一点都不能用
        c.start(listOf("short2"), "me", t0, pre = 1200.0)
        assertEquals(0.0, c.supplyLimit!!, 1e-9)
        val taken = c.preTake(1500.0)
        assertEquals(1200.0, taken, 1e-9); assertEquals(0.0, c.preLeft, 1e-9)
        assertEquals(600.0, c.supplyUntil(1800.0, taken)!!, 1e-9)
        assertEquals("", c.supplyCheck(1300.0, taken)); assertEquals("short2", c.supplyCheck(1100.0, taken))
        c.preSettle(taken, 1100.0)                 // 暂停: 合约开始前的部分原样还回去
        assertEquals(1100.0, c.preLeft, 1e-9); assertEquals(100.0, c.preUsed, 1e-9)
        assertEquals(false to "short1", c.violate("short2")); assertEquals(600.0, c.supplyLimit!!, 1e-9)
        assertEquals(1100.0, c.finish("abandoned", t0.plusHours(1))!!.optDouble("pre_left"), 1e-9)

        // 整点记录: 细化规范分成半小时, 超时未填写每格算一次违反; 记录封锁合约结束后照样锁着
        val fill = 3600L
        val texts = mapOf("2026-10-18/9/2" to "学习")
        c.start(listOf("seal", "fine"), "me", t0)
        assertTrue(c.hourForced()); assertTrue(c.fineHour(LocalDateTime.of(2026, 10, 18, 9, 0)))
        assertEquals(listOf("2026-10-18/9/2", "2026-10-18/10/1"),
            c.hourUnits(LocalDateTime.of(2026, 10, 18, 10, 40)).map { it.first })
        val tx: (String) -> String = { texts[it] ?: "" }
        assertEquals(emptyList<String>(), c.hourMisses(LocalDateTime.of(2026, 10, 18, 11, 0), fill, tx, "Sleeping"))
        assertEquals(listOf("2026-10-18/10/1"), c.hourMisses(LocalDateTime.of(2026, 10, 18, 11, 31), fill, tx, "Sleeping"))
        assertEquals(emptyList<String>(), c.hourMisses(LocalDateTime.of(2026, 10, 18, 11, 40), fill, tx, "Sleeping"))
        val a = LocalDateTime.of(2026, 10, 18, 10, 0)
        val b = a.plusMinutes(30)
        assertFalse(c.hourLocked(a, b, "", LocalDateTime.of(2026, 10, 18, 11, 29), fill, "Sleeping"))
        assertTrue(c.hourLocked(a, b, "Sleeping", LocalDateTime.of(2026, 10, 18, 11, 31), fill, "Sleeping"))
        assertFalse(c.hourLocked(a, b, "学习", LocalDateTime.of(2026, 10, 18, 11, 31), fill, "Sleeping"))
        c.finish("abandoned", LocalDateTime.of(2026, 10, 18, 12, 0))
        assertTrue(c.hourLocked(a, b, "Sleeping", LocalDateTime.of(2026, 10, 19, 0, 0), fill, "Sleeping"))
    }
}
