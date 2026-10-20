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
        assertEquals(12, Contract.levelOf(listOf("long3", "tired3", "info3", "last")))   // 最后防线没有额外等级了
        assertEquals(6, Contract.levelOf(listOf("long3", "tired3"))); assertEquals(4, Contract.levelOf(listOf("last", "long1")))
        assertEquals(4, Contract.lifeOf(emptyList())); assertEquals(3, Contract.lifeOf(listOf("retreat1")))
        assertEquals(2, Contract.lifeOf(listOf("retreat2"))); assertEquals(1, Contract.lifeOf(listOf("last")))
        assertTrue(Contract.conflicts("retreat1", "last"))
        assertEquals(20, Contract.restMinutes(0)); assertEquals(22, Contract.restMinutes(1))
        assertEquals(27, Contract.restMinutes(4)); assertEquals(55, Contract.restMinutes(19))
        assertEquals(88, Contract.restMinutes(37)); assertEquals(90, Contract.restMinutes(38))
        assertEquals(90, Contract.restMinutes(50)); assertEquals(90 * 60, Contract.restReward(45))
        assertTrue((0 until 40).all { Contract.restMinutes(it) <= Contract.restMinutes(it + 1) })
        // 删掉的两个 4 级词条: 互联网娱乐的败退 变成组合, 信息隔绝 (旧词条) 删了
        assertFalse("isolate" in Contract.BY_ID || "ent" in Contract.BY_ID)
        assertEquals(3, Contract.MAX_LEVEL)                       // 没有了 4 级的词条: 词条板不再留那一行
        assertEquals(2, Contract.TERMS.first { it.id == "silent" }.level)   // 无言为真调整为 2 级
        assertEquals(0.8, Contract.NET_SHARE["info1"]!!, 1e-9)
        assertEquals(0.65, Contract.NET_SHARE["info2"]!!, 1e-9)
        assertEquals(0.5, Contract.NET_SHARE["info3"]!!, 1e-9)
        assertTrue(Contract.conflicts("long1", "long3") && Contract.conflicts("short1", "short2"))
        assertFalse(Contract.conflicts("chan2", "info2") || Contract.conflicts("erode1", "info3"))
        assertFalse(Contract.conflicts("suppress", "quiet"))
        assertFalse(Contract.conflicts("jam", "music"))
        assertTrue(Contract.conflicts("short1", "short2")); assertFalse(Contract.conflicts("seal", "fine"))
        assertTrue(Contract.LAYOUT["short2"]!! < Contract.LAYOUT["suppress"]!!)
        assertEquals(Contract.SERIES.size + 10, Contract.BOARD_COLS)
        assertEquals(Contract.SERIES.size + 1, Contract.LAYOUT["fine"])     // 零散的挨着排, 不留空
        // 行为类似的放在一起: 禁止的软件 (乐理之灾 / 禁游令 / 低帧的诅咒) 在 1 级挨着;
        // 无言为真 (2 级) 在乐理之灾下面, 录像在禁游令 / 低帧的诅咒下面
        val m = Contract.LAYOUT["music"]!!
        assertEquals((m..m + 2).toList(), listOf("music", "game", "video").map { Contract.LAYOUT[it]!! })
        assertEquals(m + 1, Contract.LAYOUT["gaze"]); assertEquals(m + 2, Contract.LAYOUT["scar"])
        assertEquals(Contract.LAYOUT["music"], Contract.LAYOUT["silent"])   // 无言为真 2 级, 在乐理之灾下面
        assertEquals(Contract.LAYOUT["seal"], Contract.LAYOUT["fine"]); assertEquals(Contract.LAYOUT["suppress"], Contract.LAYOUT["jam"])
        // 高机动只留 1 级 (旧的高机动-I / II 删掉了), 和禁足挨着; 记录封锁的描述换掉了
        assertFalse("move1" in Contract.BY_ID || "move2" in Contract.BY_ID)
        assertEquals(1, Contract.BY_ID["move"]!!.level)
        assertEquals("现实中的移动速度+50%", Contract.BY_ID["move"]!!.desc)
        assertEquals(1, Contract.BY_ID["confine"]!!.level)
        assertEquals(Contract.LAYOUT["move"]!! + 1, Contract.LAYOUT["confine"])
        assertEquals("超过填写时间则不能再修改该记录", Contract.BY_ID["seal"]!!.desc)
        assertTrue(Contract.conflicts("gaze", "scar") && Contract.conflicts("scar", "gaze"))
        assertTrue(Contract.SOCIAL_APPS.intersect(Contract.VIDEO_APPS).isEmpty())
        assertEquals(listOf("long2", "suppress", "jam"), Contract.clean(listOf("long2", "long1", "x", "suppress", "jam")))
        // 词条组合: 系列任意一档都算; 凑齐了加危机等级 / 乘区 / 开始时或完成时的休息时间
        val mochi = Contract.COMBOS_BY_ID["mochi"]!!
        assertTrue(Contract.comboDone(mochi, listOf("long1", "tired2", "fine", "deep1")))
        assertFalse(Contract.comboDone(mochi, listOf("long1", "tired2", "fine")))
        assertEquals("void", Contract.comboState(mochi, listOf("long1", "tired2"), listOf("long1", "tired2", "fine", "deep1")))
        assertEquals(1, Contract.comboLevel(listOf("long1", "tired2", "fine", "deep1")))
        assertEquals(0.6 * 1.1, Contract.restFactorOf(listOf("long1", "tired2", "fine", "deep1")), 1e-9)
        val iso = listOf("info1", "erode1", "chan1", "suppress", "fog", "quiet", "jam")
        assertEquals(iso, Contract.clean(iso))                    // 信息隔绝组合的词条可以一起选
        assertEquals(1, Contract.comboLevel(iso)); assertEquals(10 * 60, Contract.comboEnd(iso))
        assertEquals(Contract.restMinutes(8) * 60 + 10 * 60, Contract.restReward(8, iso))
        assertEquals(20 * 60, Contract.comboGift(listOf("music", "silent", "game", "video")))
        assertEquals(0, Contract.comboGift(listOf("last", "unknown")))          // 还差 禁足 / 高机动
        assertEquals(20 * 60, Contract.comboGift(listOf("last", "unknown", "move", "confine")))
        assertEquals(mapOf("last" to Contract.COMBO_COLORS["gray"]!!, "unknown" to Contract.COMBO_COLORS["gray"]!!,
            "move" to Contract.COMBO_COLORS["gray"]!!, "confine" to Contract.COMBO_COLORS["gray"]!!),
            Contract.comboMarks(listOf("last", "unknown", "move", "confine")))
        assertEquals("纵深推进", Contract.reqName("deep")); assertEquals("最后防线", Contract.reqName("last"))
        val best = Contract.clean(listOf("long3", "erode2", "info3", "suppress", "jam", "music", "fog", "tired3", "chan2", "last",
            "short2", "seal", "fine", "silent", "game", "video", "gaze"))
        assertEquals(32, Contract.levelOf(best))
        assertEquals(20 * 60, Contract.restGift(best))             // 互联网娱乐的败退凑齐了
        assertEquals(4 * 3600 * 1.6, Contract.durationOf(listOf("long3")), 1e-6)
    }

    @Test
    fun softRules() {
        val c = Contract(tmp())
        val t0 = LocalDateTime.of(2026, 10, 18, 9, 30)
        c.start(listOf("tired2", "info2", "chan1", "long1", "music"), "me", t0)
        assertEquals(7, c.level)
        assertEquals(t0.plusSeconds((4 * 3600 * 1.2).toLong()), c.endAt)
        // 信息阻断-II: 开始时剩 100 分钟, 只能用 65 分钟; 用超了 → 降成 -I, 多给 15 分钟
        assertNull(c.netCheck(600.0, 0.0, 6000.0, "2026-10-18"))
        assertEquals(3900.0, c.netLeft(600.0)!!, 1e-6)
        assertEquals("info2" to "over", c.netCheck(4600.0, 0.0, 0.0, "2026-10-18"))
        assertEquals(false to "info1", c.violate("info2"))
        assertEquals(6, c.level); assertEquals(7, c.origLevel)
        assertEquals(800.0, c.netLeft(4600.0)!!, 1e-6)
        assertEquals("info1" to "loan", c.netCheck(4700.0, 600.0, 0.0, "2026-10-18"))
        assertNull(c.netCheck(4700.0, 600.0, 0.0, "2026-10-18"))
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
        // 互联网娱乐的败退 (词条组合): 四类都禁止, 每一类各自只在打开的那一下算; 开始时休息 +20 分钟
        val e = Contract(tmp())
        val nofun = Contract.COMBOS_BY_ID["nofun"]!!
        val isolation = Contract.COMBOS_BY_ID["isolation"]!!
        e.start(listOf("music", "silent", "game", "video"), "me", t0)
        assertEquals(1 + 2 + 1 + 1, e.level); assertEquals("game", e.banTerm("game"))
        assertEquals("video", e.banCheck("video", true)); assertEquals("", e.banCheck("video", true))
        assertEquals(20 * 60, Contract.restGift(e.terms))
        assertEquals("done", e.combos()[1].second)
        assertEquals(Contract.COMBO_COLORS["purple"], e.comboMarks()["music"])
        // 成员失效 (降级不算) 后组合的效果全部取消, 开始时给的休息时间不收回
        assertEquals(false to "", e.violate("game")); assertEquals("void", e.combos()[1].second)
        assertTrue(e.comboMarks().isEmpty())
        // 信息隔绝组合: 额外 1 危机等级, 完成时 +10 分钟; 成员失效后都没了
        val iso = listOf("info1", "erode1", "chan1", "suppress", "fog", "quiet", "jam")
        e.start(iso, "me", t0)
        assertEquals("done", e.combos()[3].second); assertEquals(9, e.level)
        assertEquals(Contract.restMinutes(9) * 60.0 + 10 * 60, e.reward, 1e-6)
        assertEquals(Contract.COMBO_COLORS["red"], e.comboMarks()["fog"])
        assertEquals(false to "", e.violate("suppress")); assertEquals("void", e.combos()[3].second)
        assertEquals(7, e.level); assertEquals(Contract.restMinutes(7) * 60.0, e.reward, 1e-6)
        // 打年糕组合: 额外 1 危机等级, 休息时间 ×1.1; 纵深推进降级不算失效
        e.start(listOf("long1", "tired2", "fine", "deep2"), "me", t0)
        assertEquals("done", e.combos()[0].second); assertEquals(8, e.level)
        assertEquals(0.6 * 1.1, e.restFactor(), 1e-9)
        assertEquals(false to "deep1", e.violate("deep2")); assertEquals("done", e.combos()[0].second)
        assertEquals(0.6 * 1.1, e.restFactor(), 1e-9)             // 降级不算失效
        assertEquals(false to "", e.violate("deep1")); assertEquals("void", e.combos()[0].second)
        assertEquals(0.6, e.restFactor(), 1e-9)                   // 成员失效了: 乘区取消
        // 没有疲惫: 基础 ×0.8 (打年糕照样相乘)
        assertEquals(0.8, Contract.restFactorOf(emptyList()), 1e-9)
        assertEquals(0.6 * 1.1, Contract.restFactorOf(listOf("long1", "tired2", "fine", "deep1")), 1e-9)
        // 录像的词条: 违反一次就失效, 不再录
        e.start(listOf("scar"), "me", t0)
        assertEquals(listOf("screen"), e.records()); assertEquals(2, e.level)
        assertEquals(false to "", e.violate("scar")); assertEquals(emptyList<String>(), e.records())
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
        // 翻页: 成系列的一页, 零散的一页, 第 3 页是词条组合
        assertEquals(listOf(0 until Contract.SERIES.size, Contract.SERIES.size until Contract.BOARD_COLS), Contract.PAGES)
        assertEquals(2, Contract.COMBO_PAGE)
        assertTrue(Contract.LAYOUT["deep1"]!! < Contract.SERIES.size && Contract.LAYOUT["hoard"]!! >= Contract.SERIES.size)
        assertFalse(Contract.conflicts("erode1", "info3") || Contract.conflicts("quiet", "jam"))
        assertFalse(Contract.conflicts("suppress", "fog"))
        val c = Contract(tmp())
        // 禁止囤囤鼠!: 提前结束休息算违反 (软规则, 1 级, 违反一次失效), 休息时间 ×1.05; 侵蚀: 冷静期 ×2 / ×3
        c.start(listOf("hoard", "tired1", "erode2"), "me", t0)
        assertEquals("hoard", c.hoardCheck()); assertEquals(0.7 * 1.05, c.restFactor(), 1e-9); assertEquals(3.0, c.cooldownFactor(), 1e-9)
        assertEquals(false to "", c.violate("hoard")); assertFalse(c.has("hoard")); assertEquals(0.7, c.restFactor(), 1e-9)
        assertEquals("hoard", c.hoardCheck()); assertTrue(c.echo("hoard"))          // 失效后再提前结束休息: 只响警报
        c.start(listOf("erode1"), "me", t0)
        assertEquals("", c.hoardCheck()); assertEquals(2.0, c.cooldownFactor(), 1e-9); assertEquals("", c.focusCheck(60.0))
        // 违反到失效的词条照样检查: 再次触发返回它 (echo, 只响警报), 不扣目标生命值
        c.start(listOf("chan1", "deep1", "quiet", "info1", "short1"), "me", t0, pre = 600.0)
        assertEquals("chan1", c.windowCheck(5)); assertEquals(false to "", c.violate("chan1"))
        assertEquals("chan1" to 4, c.windowWatch()); assertEquals("", c.windowCheck(5)); assertEquals("", c.windowCheck(3))
        assertEquals("chan1", c.windowCheck(5)); assertTrue(c.echo("chan1"))
        assertEquals(false to "", c.violate("deep1")); assertEquals("deep1", c.focusCheck(39 * 60.0)); assertEquals("", c.focusCheck(40 * 60.0))
        assertEquals("quiet", c.quietCheck(true)); assertEquals(false to "", c.violate("quiet")); assertEquals("", c.quietCheck(true))
        assertEquals("", c.quietCheck(false)); assertEquals("quiet", c.quietCheck(true))
        assertEquals(3, c.violations); assertEquals(1, c.life)
        c.live.put("violations", 0)                       // 目标生命值只有 4: 接着测剩下的
        assertNull(c.netCheck(0.0, 0.0, 4000.0, "d")); assertEquals("info1" to "over", c.netCheck(3300.0, 0.0, 0.0, "d"))
        assertEquals(false to "", c.violate("info1")); assertEquals("", c.netTerm())
        assertNull(c.netCheck(3300.0, 0.0, 0.0, "d"))                                   // 没有继续用
        assertEquals("info1" to "over", c.netCheck(3400.0, 0.0, 0.0, "d")); assertNull(c.netCheck(3400.0, 0.0, 0.0, "d"))
        assertEquals("info1" to "loan", c.netCheck(3400.0, 300.0, 0.0, "d")); assertTrue(c.echo("info1"))
        assertEquals("short1", c.supplyCheck(0.0, 400.0)); assertEquals(false to "", c.violate("short1")); assertNull(c.supplyLimit)
        assertEquals("short1", c.supplyWatch()); assertEquals("", c.supplyCheck(0.0, 400.0)); assertEquals("", c.supplyCheck(0.0, 400.3))
        assertEquals("short1", c.supplyCheck(0.0, 410.0)); assertTrue(c.echo("short1"))
        assertEquals(2, c.violations); assertEquals(listOf("chan1", "deep1", "quiet", "info1", "short1"), c.lost); assertEquals(0, c.level)
        // 降级 (还没失效) / 关闭未知变量选中的: 不算失效
        c.start(listOf("chan2", "unknown"), "me", t0)
        c.enable("music", t0.plusHours(1))
        assertEquals(false to "chan1", c.violate("chan2")); assertEquals(false to "", c.violate("music", drop = true))
        assertEquals(emptyList<String>(), c.lost); assertEquals("", c.banTerm("music")); assertFalse(c.echo("chan2"))
        // 未知变量可以再选中已经失效的词条: 选中期间违反照常扣目标生命值, 移除后又只响警报
        c.start(listOf("unknown", "music", "chan1"), "me", t0)
        assertEquals(false to "", c.violate("music")); assertEquals(false to "", c.violate("chan1"))
        assertEquals(listOf("music", "chan1"), c.lost)
        val again = c.randomCandidates()
        assertTrue("music" in again && "chan1" in again && "chan2" in again)
        c.enable("music", t0.plusHours(1))
        assertFalse(c.echo("music")); assertEquals("music", c.banCheck("music", true))
        assertEquals(false to "", c.violate("music")); assertEquals(3, c.violations); assertTrue(c.echo("music"))
        c.enable("chan2", t0.plusHours(1))
        assertEquals("chan2" to 3, c.windowWatch()); assertFalse(c.echo("chan2"))
        assertEquals(listOf("chan2"), c.expire(t0.plusHours(1))); assertEquals("chan1" to 4, c.windowWatch()); assertTrue(c.echo("chan1"))
        // 最后防线: 只有初始目标生命值 1, 没有额外的词条等级
        c.start(listOf("last", "long3", "tired3", "chan1"), "me", t0)
        assertEquals(10, c.level); assertEquals(1, c.violationLimit); assertEquals(10, c.origLevel)
        assertEquals(true to "", c.violate("chan1")); assertEquals(9, c.level)   // 目标生命值只有 1
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
        val r1 = kotlin.random.Random(1)
        val rng = Dice({ 0.0 }, { r1.nextInt(it) })              // 0 = 一定抽词条
        fun termsOf(ev: List<Pair<String, String>>) = ev.filter { it.first == "term" }.map { it.second }
        // 抽中后 3 分钟才生效, 只在抽中的那个小时里有效
        val first = termsOf(c.roll(t0, rng))
        assertEquals(1, first.size); assertEquals(emptyList<String>(), c.rand); assertEquals(first, c.waiting.keys.toList())
        assertEquals(t0.plusMinutes(3) to t0.plusHours(1), c.waiting[first[0]]); assertFalse(first[0] in c.randomCandidates())
        assertEquals(emptyList<String>(), c.wake(t0.plusMinutes(2).plusSeconds(59))); assertFalse(c.has(first[0]))
        assertEquals(first, c.wake(t0.plusMinutes(3))); assertEquals(first, c.rand); assertTrue(c.waiting.isEmpty())
        assertEquals(6, c.level); assertEquals(t0.plusHours(1), c.randUntil(first[0]))
        assertEquals(0, c.roll(t0.plusMinutes(59), rng).size)
        // 错过的小时补上; 同时只有一个, 到时移除不算违反
        val more = termsOf(c.roll(t0.plusHours(2).plusMinutes(1), rng))
        assertEquals(1, more.size); assertEquals(emptyList<String>(), c.rand)
        assertEquals(more, c.wake(t0.plusHours(2).plusMinutes(3))); assertEquals(more, c.rand); assertEquals(6, c.level)
        assertEquals(t0.plusHours(3), c.randUntil(more[0])); assertEquals(0, c.violations)
        assertTrue(c.rand.all { Contract.BY_ID[it]!!.level in 1..2 && it in c.terms })
        // 点按关闭: 算一次违反, 直接去掉
        val t = c.rand[0]
        assertEquals(false to "", c.violate(t, drop = true))
        assertFalse(t in c.terms || t in c.rand); assertEquals(1, c.violations)
        // 下一个小时再抽一个, 这个小时结束时移除
        val nxt = termsOf(c.roll(t0.plusHours(3), rng))
        assertEquals(nxt, c.wake(t0.plusHours(3).plusMinutes(5))); assertEquals(nxt, c.rand)
        assertEquals(nxt, c.expire(t0.plusHours(4))); assertEquals(emptyList<String>(), c.rand)
        assertEquals(1, c.violations); assertEquals(6, c.level)
        // 选中的词条降级: 还是它选中的, 移除的时刻不变
        c.start(listOf("unknown"), "me", t0)
        c.enable("chan2", t0.plusHours(1))
        assertEquals(false to "chan1", c.violate("chan2")); assertEquals(listOf("chan1"), c.rand)
        assertEquals(t0.plusHours(1), c.randUntil("chan1"))
        assertEquals(listOf("chan1"), c.expire(t0.plusHours(1))); assertEquals(listOf("unknown"), c.terms)
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

    @Test
    fun unknownEffects() {
        val t0 = LocalDateTime.of(2026, 10, 18, 9, 30)
        val c = Contract(tmp())
        fun seq(vararg rs: Double, pick: (Int) -> Int = { 0 }): Dice {
            val q = ArrayDeque(rs.toList())
            return Dice({ q.removeFirstOrNull() ?: 0.0 }, pick)
        }
        // 通过违反失效的词条可以再回到词条池, 但只会再被选中一次 (开始时选的, 和未知变量选中的都是)
        c.start(listOf("unknown", "music"), "me", t0)
        assertFalse("music" in c.randomCandidates()); assertEquals(false to "", c.violate("music"))
        assertTrue("music" in c.randomCandidates())                      // 失效了: 回到词条池
        val idx = c.randomCandidates().indexOf("music")
        assertEquals(listOf("term" to "music"), c.roll(t0, seq(pick = { idx })))
        assertEquals(listOf("music"), c.wake(t0.plusMinutes(3)))
        assertEquals(false to "", c.violate("music")); assertFalse("music" in c.randomCandidates())   // 只会再选中一次
        c.pick("game", t0)
        assertEquals(listOf("game"), c.wake(t0.plusMinutes(3))); assertFalse("game" in c.randomCandidates())
        assertEquals(false to "", c.violate("game", drop = true)); assertTrue("game" in c.randomCandidates())   // 关闭也算违反
        c.pick("game", t0)
        assertFalse("game" in c.randomCandidates())
        // 效果按 15/15/15/15/10/30
        assertEquals(listOf("rest_cut", "rest_cut", "lock", "rest_add", "double", "slow", "none", "none"),
            listOf(0.0, 0.149, 0.15, 0.3, 0.45, 0.6, 0.7, 0.999).map { Contract.effect(seq(it)) })
        // 有能选的词条: 75% 词条, 25% 效果
        c.start(listOf("unknown"), "me", t0)
        assertEquals(listOf("rest_cut" to ""), c.roll(t0, seq(0.75, 0.0))); assertTrue(c.waiting.isEmpty())
        assertEquals(1, c.roll(t0.plusHours(1), seq(0.7499)).size); assertEquals(1, c.waiting.size)
        // 再判定两次: 这次不生效, 改为判定两次 (这里: 一次词条, 一次 +10 分钟)
        val ev = c.roll(t0.plusHours(2), seq(0.9, 0.45, 0.0, 0.9, 0.3))
        assertEquals(listOf("double", "term", "rest_add"), ev.map { it.first }); assertEquals(1, c.waiting.size)
        // 没有能选的词条: 一定是效果
        c.live.put("rand_used", org.json.JSONArray(Contract.TERMS.map { it.id }))
        assertTrue(c.randomCandidates().isEmpty()); assertEquals(listOf("none" to ""), c.roll(t0.plusHours(3), seq(0.7)))
        // 阻滞: 只减慢合约内那部分专注 (合约结束后恢复正常速度); 合约结束后才开始的专注不生效
        c.live.put("rand_n", 3)
        assertEquals(listOf("slow" to ""), c.roll(t0.plusHours(3).plusMinutes(30), seq(0.6))); assertTrue(c.slowPending)
        assertFalse(c.slowBind(t0.plusHours(4).plusMinutes(10))); assertTrue(c.slowPending)    // 合约结束后才开始的专注
        val s1 = t0.plusHours(3).plusMinutes(30)
        assertTrue(c.slowBind(s1)); assertFalse(c.slowPending); assertFalse(c.slowBind(s1))
        assertEquals(0.9, c.focusRate(s1, s1), 1e-9)
        assertEquals(1.0, c.focusRate(s1, t0.plusHours(4).plusMinutes(10)), 1e-9)             // 合约结束后不再减慢
        assertEquals((30 * 60 * 0.9 + 30 * 60) / 60.0, c.focusMinutes(s1, 3600.0), 1e-6)
        // 封锁一个过了填写时限, 还没被封锁的记录格 (最近 24 小时里的), 合约结束后照样锁着; 填了的也锁
        val now = LocalDateTime.of(2026, 10, 18, 12, 10)
        val texts = mapOf("2026-10-18/10" to "学习")
        val textOf = { k: String -> texts[k] ?: "" }
        assertEquals("2026-10-18/10", c.lockCell(now, seq(pick = { it - 1 }), textOf, "Sleeping", 3600))
        val a = LocalDateTime.of(2026, 10, 18, 10, 0)
        val b = a.plusHours(1)
        assertTrue(c.hourLocked(a, b, "学习", now, 3600, "Sleeping"))
        assertFalse(c.hourLocked(b, b.plusHours(1), "", now, 3600, "Sleeping"))        // 11 点那格还没过填写时限
        assertEquals("2026-10-18/9", c.lockCell(now, seq(pick = { it - 1 }), textOf, "Sleeping", 3600))   // 已经封锁的不再选
        assertEquals("", c.lockCell(now, seq(), null, "Sleeping", 3600))
        c.finish("abandoned", now)
        // 合约提前结束了: 之后才开始的这段专注不减速 (阻滞只对合约内的专注生效)
        assertTrue(c.hourLocked(a, b, "学习", now.plusDays(3), 3600, "Sleeping"))
        assertEquals(1.0, c.focusRate(s1), 1e-9); assertEquals(60.0, c.focusMinutes(s1, 3600.0), 1e-6)
        // 未知变量启用了整点记录的词条: 这次合约中一直强制开启整点记录
        c.start(listOf("unknown"), "me", t0)
        assertFalse(c.hourForced())
        c.enable("seal", t0.plusHours(1))
        assertTrue(c.hourForced()); assertEquals(listOf("seal"), c.expire(t0.plusHours(1))); assertTrue(c.hourForced())
        c.finish("abandoned", t0.plusHours(2))
        assertFalse(c.hourForced())
        // 积攒的休息时间减少: 合约开始前的那部分最多剩这么多
        c.start(listOf("unknown"), "me", t0, pre = 600.0)
        c.preCap(900.0); assertEquals(600.0, c.preLeft, 1e-9)
        c.preCap(250.0); assertEquals(250.0, c.preLeft, 1e-9)
    }
}
