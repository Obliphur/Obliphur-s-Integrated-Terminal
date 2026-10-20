package com.obliphur.oit.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.LocalDate
import java.time.LocalDateTime

/** 行为模式 / 外出 (设置 → 日常, MECHANICS 21). 对应桌面版 behavior.py 的 selftest. */
class BehaviorTest {

    private fun tmp(name: String = "behavior.json"): File =
        File(Files.createTempDirectory("oit").toFile(), name)

    private fun at(y: Int, m: Int, d: Int, h: Int, mi: Int): LocalDateTime =
        LocalDateTime.of(y, m, d, h, mi)

    private fun settings(mode: String = Cfg.BEHAVIOR_CLOSED): Settings {
        val s = Settings(tmp("settings.json"))
        s.effective["BEHAVIOR_MODE"] = mode
        return s
    }

    @Test
    fun outingRecording() {
        var clock = at(2026, 10, 19, 0, 0)
        val s = settings()
        val b = Behavior(tmp(), s) { clock }
        assertEquals(Cfg.BEHAVIOR_CLOSED, b.mode())
        assertTrue(b.enabled())
        assertNull(b.active(clock))

        // 4 小时 10:00 ~ 14:00: 2h 后到此刻已记 1.6h 专注 / 0.4h 休息; 4h 记录 → 24 min 休息
        assertEquals("", b.start(4, "dev", at(2026, 10, 19, 10, 0)))
        clock = at(2026, 10, 19, 12, 0)
        val o = b.active(clock)
        assertNotNull(o)
        assertEquals(Cfg.OUTING_QUOTA_FACTOR, b.quotaFactor(LocalDate.of(2026, 10, 19)), 1e-9)
        assertEquals(Cfg.OUTING_COOLDOWN_FACTOR, b.cooldownFactor(clock), 1e-9)
        val segs = b.focusSegments(LocalDate.of(2026, 10, 19), clock)
        val study = segs.filter { it.kind == "study" }.sumOf { secondsBetween(it.start, it.end) }
        val rest = segs.filter { it.kind == "break" }.sumOf { secondsBetween(it.start, it.end) }
        assertEquals(7200 * 0.8, study, 1e-6)
        assertEquals(7200 * 0.2, rest, 1e-6)
        assertEquals(1.6, b.studyHours(LocalDate.of(2026, 10, 19), clock), 1e-9)
        assertEquals(2.0, b.recordedHours(o!!, clock), 1e-9)
        // 整点记录: 外出的小时是"外出", 其他时间是"休息"; 模式切换之前的不改
        assertEquals("外出", b.hourAutoText(at(2026, 10, 19, 10, 0)))
        assertEquals("外出", b.hourAutoText(at(2026, 10, 19, 11, 0)))
        assertEquals("休息", b.hourAutoText(at(2026, 10, 19, 14, 0)))
        assertEquals("休息", b.hourAutoText(at(2026, 10, 19, 8, 0)))
        assertEquals("", b.hourAutoText(at(2026, 10, 18, 23, 0)))

        // 提前结束: 记到 12:00, 2 小时 → 12 min 休息; 只发一次
        clock = at(2026, 10, 19, 12, 0)
        val ev = b.stop({ true }, clock)
        assertEquals(2 * Cfg.OUTING_REST_PER_HOUR.toInt(), ev.reward)
        assertEquals(2.0, ev.hours, 1e-9)
        assertNull(b.active(clock))
        assertEquals(0, b.stop({ true }, clock).reward)
    }

    @Test
    fun crossDayAndCap() {
        var clock = at(2026, 10, 19, 21, 0)
        val b = Behavior(tmp(), settings()) { clock }
        // 22:00 ~ 08:00: 第一天 2h (22:00~23:00 还有 1h 专注), 第二天 8h (7:00~8:00 1h 专注)
        assertEquals("", b.start(10, "", at(2026, 10, 19, 22, 0)))
        clock = at(2026, 10, 20, 8, 0)
        val ev = b.tick(clock) { true }
        assertEquals(10 * Cfg.OUTING_REST_PER_HOUR.toInt(), ev.reward)
        assertEquals(10.0, ev.hours, 1e-9)
        val d1 = b.focusSegments(LocalDate.of(2026, 10, 19), clock)
        assertEquals(3600 * 0.8, d1.filter { it.kind == "study" }.sumOf { secondsBetween(it.start, it.end) }, 1e-6)
        assertTrue(d1.any { it.kind == "break" })
        val d2 = b.focusSegments(LocalDate.of(2026, 10, 20), clock)
        assertEquals(3600 * 0.8, d2.filter { it.kind == "study" }.sumOf { secondsBetween(it.start, it.end) }, 1e-6)

        // 每天最多 10 小时: 7:00 ~ 20:00 (13h) 只记 10h → 60 min, 专注 8h
        var clock3 = at(2026, 10, 19, 6, 0)
        val b3 = Behavior(tmp(), settings()) { clock3 }
        assertEquals("", b3.start(13, "", at(2026, 10, 19, 7, 0)))
        clock3 = at(2026, 10, 19, 20, 0)
        val ev3 = b3.tick(clock3) { true }
        assertEquals(10 * Cfg.OUTING_REST_PER_HOUR.toInt(), ev3.reward)
        assertEquals(10.0, ev3.hours, 1e-9)
        assertEquals(10 * 3600 * 0.8,
            b3.focusSegments(LocalDate.of(2026, 10, 19), clock3).filter { it.kind == "study" }
                .sumOf { secondsBetween(it.start, it.end) }, 1e-6)

        // 超过 30 小时才碰到 3 小时的上限
        var clock4 = at(2026, 10, 19, 6, 0)
        val b4 = Behavior(tmp(), settings()) { clock4 }
        assertEquals("", b4.start(70, "", at(2026, 10, 19, 7, 0)))
        clock4 = at(2026, 10, 22, 5, 0)
        assertEquals(Cfg.OUTING_REST_MAX, b4.tick(clock4) { true }.reward)

        // 同一天两次外出: 向下取整, 先开始的先记, 一天合计最多 10h
        var clock5 = at(2026, 10, 19, 9, 0)
        val b5 = Behavior(tmp(), settings()) { clock5 }
        assertEquals("", b5.start(2, "", at(2026, 10, 19, 10, 0)))
        val ev5a = b5.stop({ true }, at(2026, 10, 19, 11, 30))
        assertEquals(1.0, ev5a.hours, 1e-9)
        assertEquals(Cfg.OUTING_REST_PER_HOUR.toInt(), ev5a.reward)
        assertEquals("", b5.start(9, "", at(2026, 10, 19, 12, 0)))
        val ev5 = b5.stop({ true }, at(2026, 10, 19, 21, 0))
        assertEquals(9.0, ev5.hours, 1e-9)
        assertEquals(9 * Cfg.OUTING_REST_PER_HOUR.toInt(), ev5.reward)

        // 不足 1 小时: 记 0 小时, 不发休息时间
        val b6 = Behavior(tmp(), settings()) { clock5 }
        assertEquals("", b6.start(1, "", at(2026, 10, 19, 10, 0)))
        val ev6 = b6.stop({ true }, at(2026, 10, 19, 10, 30))
        assertEquals(0.0, ev6.hours, 1e-9)
        assertEquals(0, ev6.reward)
        assertTrue(b6.focusSegments(LocalDate.of(2026, 10, 19), at(2026, 10, 19, 10, 30)).isEmpty())
    }

    @Test
    fun syncMergeAndSettingsDelay() {
        var clock = at(2026, 10, 19, 9, 0)
        val a = Behavior(tmp(), settings()) { clock }
        assertEquals("", a.start(3, "A", clock))
        val doc = a.doc()

        val b = Behavior(tmp(), settings()) { clock }
        b.merge(doc)
        assertNotNull(b.active(at(2026, 10, 19, 10, 0)))
        b.stop({ true }, at(2026, 10, 19, 10, 0))
        assertTrue(b.sorted().first().rewarded)
        b.merge(doc)                 // 旧记录不能复活已经结束 / 发过休息时间的外出
        assertTrue(b.sorted().first().rewarded)
        assertNotNull(b.sorted().first().stopped)

        // 设置: 配置期内行为模式也要等 168 小时; 第一次打开时给的一次免等待能立即生效
        val s = Settings(tmp("settings2.json"))
        s.startSetup(1)
        val (r, _) = s.stage("BEHAVIOR_MODE", "closed")
        assertEquals(StageResult.STAGED, r)
        assertTrue(s.pendingLeftSeconds("BEHAVIOR_MODE") > 167 * 3600)
        s.cancel("BEHAVIOR_MODE")
        assertTrue(s.grantFreeChange("BEHAVIOR_MODE"))
        assertEquals("free", s.waitTag("BEHAVIOR_MODE"))
        assertEquals(StageResult.INSTANT, s.stage("BEHAVIOR_MODE", "closed").first)
        assertEquals("closed", s.str("BEHAVIOR_MODE"))
        s.endSetup()
        assertEquals(StageResult.STAGED, s.stage("BEHAVIOR_MODE", "work").first)
        assertTrue(s.pendingLeftSeconds("BEHAVIOR_MODE") > 167 * 3600)
    }

    @Test
    fun yieldToContract() {
        // 合约和外出撞上 (同步来的): 外出在合约开始时结束; 比合约晚开始的外出整次作废
        var clock = at(2026, 10, 19, 0, 0)
        val s = settings()
        val b = Behavior(tmp(), s) { clock }
        assertEquals("", b.start(5, "dev", at(2026, 10, 19, 10, 0)))
        clock = at(2026, 10, 19, 12, 31)
        val o = b.yieldToContract(at(2026, 10, 19, 12, 30), clock)
        assertNotNull(o)
        assertEquals(at(2026, 10, 19, 12, 30), o!!.stopped)
        assertNull(b.active(clock))
        assertNull(b.yieldToContract(at(2026, 10, 19, 12, 30), clock))
        assertEquals(2.0, b.recordedHours(o, clock), 1e-9)

        clock = at(2026, 10, 19, 0, 0)
        val b2 = Behavior(tmp(), s) { clock }
        assertEquals("", b2.start(3, "dev", at(2026, 10, 19, 15, 20)))
        clock = at(2026, 10, 19, 15, 21)
        val o2 = b2.yieldToContract(at(2026, 10, 19, 15, 0), clock)!!
        assertEquals(o2.start, o2.stopped)
        assertEquals(0.0, b2.recordedHours(o2, clock), 1e-9)
        assertEquals(1.0, b2.quotaFactor(LocalDate.of(2026, 10, 19)), 1e-9)
        assertTrue(b2.days().isEmpty())
        assertEquals("休息", b2.hourAutoText(at(2026, 10, 19, 15, 0)))
        assertEquals(0, b2.tick(clock) { true }.reward)
    }
}
