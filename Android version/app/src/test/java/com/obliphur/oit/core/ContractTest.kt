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
        assertEquals(10 + 1, Contract.levelOf(listOf("long3", "isolate", "last")))   // 最后防线: 危机等级每有 10 级 +1
        assertEquals(7, Contract.levelOf(listOf("long3", "isolate"))); assertEquals(4, Contract.levelOf(listOf("last", "long1")))
        assertEquals(1, Contract.lastBonus(19, listOf("last"))); assertEquals(2, Contract.lastBonus(20, listOf("last")))
        assertEquals(0, Contract.lastBonus(30, listOf("long3")))
        assertEquals(4, Contract.lifeOf(emptyList())); assertEquals(3, Contract.lifeOf(listOf("retreat1")))
        assertEquals(2, Contract.lifeOf(listOf("retreat2"))); assertEquals(1, Contract.lifeOf(listOf("last")))
        assertTrue(Contract.conflicts("retreat1", "last"))
        assertEquals(20, Contract.restMinutes(0)); assertEquals(22, Contract.restMinutes(1))
        assertEquals(27, Contract.restMinutes(4)); assertEquals(55, Contract.restMinutes(19))
        assertEquals(88, Contract.restMinutes(37)); assertEquals(90, Contract.restMinutes(38))
        assertEquals(90, Contract.restMinutes(50)); assertEquals(90 * 60, Contract.restReward(45))
        assertTrue((0 until 40).all { Contract.restMinutes(it) <= Contract.restMinutes(it + 1) })
        assertTrue(Contract.conflicts("long1", "long3") && Contract.conflicts("isolate", "info2"))
        assertFalse(Contract.conflicts("chan2", "isolate") || Contract.conflicts("isolate", "chan1"))
        assertTrue(Contract.conflicts("suppress", "isolate"))
        assertFalse(Contract.conflicts("jam", "music"))
        assertTrue(Contract.conflicts("short1", "short2")); assertFalse(Contract.conflicts("seal", "fine"))
        assertTrue(Contract.LAYOUT["short2"]!! < Contract.LAYOUT["suppress"]!!)
        assertEquals(Contract.SERIES.size + 9, Contract.BOARD_COLS)
        assertEquals(Contract.SERIES.size + 1, Contract.LAYOUT["fine"])     // 零散的挨着排, 不留空
        // 行为类似的放在一起: 禁止的软件一行挨着, 录像在它们下面, 互联网娱乐的败退在乐理之灾那一列的 4 级
        val m = Contract.LAYOUT["music"]!!
        assertEquals((m..m + 3).toList(), Contract.BANS.map { Contract.LAYOUT[it]!! })
        assertEquals(m, Contract.LAYOUT["gaze"]); assertEquals(m + 1, Contract.LAYOUT["scar"]); assertEquals(m, Contract.LAYOUT["ent"])
        assertEquals(Contract.LAYOUT["seal"], Contract.LAYOUT["fine"]); assertEquals(Contract.LAYOUT["suppress"], Contract.LAYOUT["jam"])
        assertTrue(Contract.conflicts("gaze", "scar") && Contract.conflicts("scar", "gaze"))
        assertTrue(Contract.BANS.all { Contract.conflicts("ent", it) && Contract.conflicts(it, "isolate") })
        assertTrue(Contract.conflicts("ent", "isolate")); assertFalse(Contract.conflicts("isolate", "tired2"))
        assertTrue(Contract.SOCIAL_APPS.intersect(Contract.VIDEO_APPS).isEmpty())
        assertEquals(listOf("long2", "isolate", "jam"), Contract.clean(listOf("long2", "long1", "x", "isolate", "suppress", "jam")))
        assertEquals(listOf("isolate", "chan2"), Contract.clean(listOf("isolate", "chan2")))
        val best = Contract.clean(listOf("long3", "move2", "info3", "suppress", "jam", "music", "fog", "tired3", "chan2", "last",
            "short2", "seal", "fine", "silent", "game", "video", "gaze"))
        assertEquals(31 + 3, Contract.levelOf(best))
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
        assertEquals("music", c.banCheck("music", true)); assertEquals("", c.banCheck("music", true)); c.banCheck("music", false)
        assertEquals("", c.downgrade("music")); assertFalse(c.has("music"))
        // 休息: 疲惫 ×0.6; 完成时按降级后的等级给固定的时间
        assertEquals(600.0, c.bookRest(1000.0), 1e-6)
        assertEquals(Contract.restMinutes(c.level) * 60.0, c.reward, 1e-6)
        // 空闲 30 分钟
        assertNull(c.due(t0.plusHours(1), true))
        assertEquals("idle" to t0.plusMinutes(90), c.due(t0.plusMinutes(91), true))
        // 设备离线 15 分钟失败: 从合约开始 / 本机打开程序之后才算
        assertNull(c.offlineDue(listOf("手机" to t0.minusHours(3)), t0.plusMinutes(14), t0))
        assertEquals("手机" to t0.plusMinutes(15), c.offlineDue(listOf("手机" to t0.minusHours(3)), t0.plusMinutes(16), t0))
        assertNull(c.offlineDue(listOf("手机" to t0), t0.plusMinutes(70), t0.plusHours(1)))
        // 互联网娱乐的败退: 四类都禁止, 每一类各自只在打开的那一下算; 违反时它降一级. 录像的词条违反一次就失效
        val e = Contract(tmp())
        e.start(listOf("ent", "gaze", "info3"), "me", t0)
        assertEquals("ent", e.banTerm("game")); assertEquals(listOf("camera"), e.records())
        assertEquals("ent", e.banCheck("video", true)); assertEquals("", e.banCheck("video", true))
        // 违反任意一项就整个失效, 四类都放开
        assertEquals(false to "", e.violate("ent")); assertFalse(e.has("ent"))
        assertEquals("", e.banTerm("silent")); assertEquals("", e.banCheck("silent", true))
        e.finish("completed", t0.plusHours(5))
        // 合约开始时积攒的休息时间 +20 分钟
        assertEquals(20 * 60, Contract.restGift(listOf("ent", "gaze"))); assertEquals(0, Contract.restGift(listOf("music")))
        e.start(listOf("ent", "gaze"), "me", t0)
        assertEquals(false to "", e.violate("gaze")); assertEquals(emptyList<String>(), e.records())
        // 信息隔绝: 休息时间 ×1.3 (和疲惫相乘), 降成信息阻断-III 后没有了
        e.start(listOf("isolate", "tired2"), "me", t0)
        assertEquals(0.6 * 1.3, e.restFactor(), 1e-9)
        e.downgrade("isolate"); assertEquals(0.6, e.restFactor(), 1e-9)
        // 没有疲惫: 基础 ×0.8 (信息隔绝照样相乘)
        assertEquals(0.8, Contract.restFactorOf(emptyList()), 1e-9)
        assertEquals(0.8 * 1.3, Contract.restFactorOf(listOf("isolate")), 1e-9)
        // 目标生命值: 默认 4, 退路封锁-I 只有 3; 整点记录超时未填写也算 (不降词条)
        assertEquals(4, c.violationLimit)
        c.start(listOf("suppress", "info3", "long1", "retreat1"), "me", t0)
        assertEquals(3, c.life); assertEquals("suppress", c.quickCheck())
        assertEquals(false to "", c.violate("suppress")); assertFalse(c.has("suppress"))
        assertEquals(false to "", c.violate("")); assertEquals(2, c.violations); assertEquals(1, c.life)
        assertEquals(true to "info2", c.violate("info3")); assertEquals(0, c.life)
        val rec = c.finish("failed", t0.plusHours(2))!!
        assertEquals(0, rec.optInt("reward")); assertEquals(4, rec.optInt("level")); assertEquals(6, rec.optInt("orig_level"))
        assertFalse(c.takeFirstBreak())
    }

    @Test
    fun completeAndSync() {
        val f = tmp()
        val c = Contract(f)
        val t0 = LocalDateTime.of(2026, 10, 18, 9, 30)
        c.start(listOf("last", "tired1"), "me", t0)
        assertEquals(1, c.violationLimit)
        assertEquals(420.0, c.bookRest(600.0), 1e-6)
        assertEquals(27 * 60.0, c.reward, 1e-6)
        assertEquals("completed" to t0.plusHours(4), c.due(t0.plusHours(4), false))
        val rec = c.finish("completed", t0.plusHours(4).plusMinutes(3))!!
        assertEquals(27 * 60, rec.optInt("reward"))
        assertEquals(0.0, c.reward, 1e-6)
        assertEquals(420.0, c.bookRestAfter(600.0, t0.plusHours(3)), 1e-6)
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
        // 细化规范的半小时格补填时限只有 30 min (10:00-10:30 那格 11:00 到期)
        assertEquals(emptyList<String>(), c.hourMisses(LocalDateTime.of(2026, 10, 18, 10, 59), fill, tx, "Sleeping"))
        assertEquals(listOf("2026-10-18/10/1"), c.hourMisses(LocalDateTime.of(2026, 10, 18, 11, 1), fill, tx, "Sleeping"))
        assertEquals(emptyList<String>(), c.hourMisses(LocalDateTime.of(2026, 10, 18, 11, 10), fill, tx, "Sleeping"))
        val a = LocalDateTime.of(2026, 10, 18, 10, 0)
        val b = a.plusMinutes(30)
        assertFalse(c.hourLocked(a, b, "", LocalDateTime.of(2026, 10, 18, 10, 59), fill, "Sleeping"))
        assertTrue(c.hourLocked(a, b, "Sleeping", LocalDateTime.of(2026, 10, 18, 11, 1), fill, "Sleeping"))
        assertFalse(c.hourLocked(a, b, "学习", LocalDateTime.of(2026, 10, 18, 11, 1), fill, "Sleeping"))
        c.finish("abandoned", LocalDateTime.of(2026, 10, 18, 12, 0))
        assertTrue(c.hourLocked(a, b, "Sleeping", LocalDateTime.of(2026, 10, 19, 0, 0), fill, "Sleeping"))
    }

    @Test
    fun newTerms() {
        val t0 = LocalDateTime.of(2026, 10, 18, 9, 30)
        // 翻页: 成系列的一页, 零散的一页; 侵蚀 / 静默协议和信息隔绝冲突
        assertEquals(listOf(0 until Contract.SERIES.size, Contract.SERIES.size until Contract.BOARD_COLS), Contract.PAGES)
        assertTrue(Contract.LAYOUT["deep1"]!! < Contract.SERIES.size && Contract.LAYOUT["hoard"]!! >= Contract.SERIES.size)
        assertTrue(Contract.conflicts("erode1", "isolate") && Contract.conflicts("isolate", "erode2") && Contract.conflicts("quiet", "isolate"))
        assertFalse(Contract.conflicts("erode1", "info3") || Contract.conflicts("quiet", "jam"))
        val c = Contract(tmp())
        // 禁止囤囤鼠!: 提前结束休息算违反 (软规则, 1 级, 违反一次失效), 休息时间 ×1.05; 侵蚀: 冷静期 ×2 / ×3
        c.start(listOf("hoard", "tired1", "erode2"), "me", t0)
        assertEquals("hoard", c.hoardCheck()); assertEquals(0.7 * 1.05, c.restFactor(), 1e-9); assertEquals(3.0, c.cooldownFactor(), 1e-9)
        assertEquals(false to "", c.violate("hoard")); assertEquals("", c.hoardCheck()); assertEquals(0.7, c.restFactor(), 1e-9)
        c.start(listOf("erode1"), "me", t0)
        assertEquals("", c.hoardCheck()); assertEquals(2.0, c.cooldownFactor(), 1e-9); assertEquals("", c.focusCheck(60.0))
        // 最后防线: 危机等级 10 → 多 1 级; 降级后不到 10 级就没有了
        c.start(listOf("last", "long3", "isolate"), "me", t0)
        assertEquals(11, c.level); assertEquals(1, c.lastExtra); assertEquals(11, c.origLevel)
        assertEquals(true to "info3", c.violate("isolate")); assertEquals(9, c.level); assertEquals(0, c.lastExtra)   // 目标生命值只有 1
        // 纵深推进: 专注不到 40 / 60 分钟算违反; -II 降成 -I
        c.start(listOf("deep2", "quiet"), "me", t0)
        assertEquals("deep2", c.focusCheck(59 * 60.0)); assertEquals("", c.focusCheck(60 * 60.0))
        assertEquals(false to "deep1", c.violate("deep2"))
        assertEquals("", c.focusCheck(59 * 60.0)); assertEquals("deep1", c.focusCheck(39 * 60.0))
        // 静默协议: 只在刚联网的那一下算; 1 级, 违反一次失效
        assertEquals("quiet", c.quietCheck(true)); assertEquals("", c.quietCheck(true)); assertEquals("", c.quietCheck(false))
        assertEquals(false to "", c.violate("quiet")); assertFalse(c.has("quiet")); assertEquals(1, c.level)
        // 未知变量: 刚开始和之后每个小时开始时各选一个 1-2 级的词条, 不算进危机等级
        c.start(listOf("unknown", "info2", "long1"), "me", t0, pre = 600.0)
        val pool = c.randomCandidates()
        assertTrue("gaze" !in pool && "scar" !in pool && "info1" !in pool && "long2" !in pool && "unknown" !in pool)
        assertTrue(pool.none { it.startsWith("long") })              // 不选持久战
        assertTrue("music" in pool && "jam" in pool)
        val rng = kotlin.random.Random(1)
        assertEquals(1, c.roll(t0, rng).size); assertEquals(6, c.level)
        assertEquals(0, c.roll(t0.plusMinutes(59), rng).size)
        assertEquals(2, c.roll(t0.plusHours(2).plusMinutes(1), rng).size)
        assertEquals(3, c.rand.size); assertEquals(6, c.level)
        assertTrue(c.rand.all { Contract.BY_ID[it]!!.level in 1..2 && it in c.terms })
        // 点按关闭: 算一次违反, 直接去掉
        val t = c.rand[0]
        assertEquals(false to "", c.violate(t, drop = true))
        assertFalse(t in c.terms || t in c.rand); assertEquals(1, c.violations)
        // 选中支援短缺: 补上能用多少
        c.start(listOf("unknown"), "me", t0, pre = 600.0)
        c.enable("short1")
        assertEquals(300.0, c.supplyLimit!!, 1e-9); assertEquals(listOf("short1"), c.rand); assertNull(c.randUntil("short1"))
        // 退路封锁: 目标生命值减少后不为 0 才选; 只在选中的那个小时里有效, 小时结束时移除 (不算违反)
        c.violate(""); c.violate("")
        assertTrue("retreat2" !in c.randomCandidates() && "retreat1" in c.randomCandidates())
        c.enable("retreat1", t0.plusHours(2))
        assertEquals(3, c.violationLimit); assertEquals(1, c.life); assertEquals(t0.plusHours(2), c.randUntil("retreat1"))
        assertEquals(emptyList<String>(), c.expire(t0.plusHours(1).plusMinutes(59))); assertTrue(c.has("retreat1"))
        assertEquals(listOf("retreat1"), c.expire(t0.plusHours(2)))
        assertFalse(c.has("retreat1")); assertEquals(2, c.life); assertEquals(2, c.violations); assertEquals(listOf("short1"), c.rand)
        assertEquals(listOf("short1"), c.finish("abandoned", t0.plusHours(1))!!.optJSONArray("rand")!!.strings())
    }
}
