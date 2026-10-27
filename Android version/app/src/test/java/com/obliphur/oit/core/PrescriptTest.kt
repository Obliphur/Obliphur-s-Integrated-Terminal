package com.obliphur.oit.core

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.LocalDateTime

/** 指令 (MECHANICS 22). 对应桌面版 prescript.py 的 _selftest. */
class PrescriptTest {

    private class Env {
        var mode = "focus"
        var page = "prescript"
        var minimized = false
        var dev = false
        var hours = 0.0
        val contracts = mutableListOf<JSONObject>()
        val rest = mutableListOf<Pair<Int, String>>()
        val dir: File = Files.createTempDirectory("oit").toFile()
        fun engine() = PrescriptEngine(
            File(dir, "prescript.json"),
            stateFn = { mode }, todayHoursFn = { hours }, contractsFn = { contracts },
            pageFn = { page }, minimizedFn = { minimized },
            grantRestFn = { s, c -> rest.add(s to c) }, devFn = { dev },
        )
    }

    private val t0 = LocalDateTime.of(2026, 10, 28, 10, 5, 0)

    @Test
    fun codes() {
        assertEquals("REST3", Prescript.codeFor(1.0))
        assertEquals("REST0.3", Prescript.codeFor(0.1))
        assertEquals("_%NONE%.", Prescript.padCode("NONE"))
        assertEquals("_REST3%.", Prescript.padCode("REST3"))
        assertEquals("_REST20.", Prescript.padCode("REST20"))
        assertEquals("REST0.3.", Prescript.padCode("REST0.3"))
        assertEquals("_REST3%.", Prescript.padCode("1h*3min"))
        assertEquals("_REST20.", Prescript.padCode("20min"))
        for (c in listOf("A", "REST120", "REST1200", "REST12345678")) assertEquals(8, Prescript.padCode(c).length)
    }

    @Test
    fun drawCooldown() {
        val env = Env()
        val eng = env.engine()
        assertTrue(eng.canDraw(t0))
        assertNotNull(eng.draw(t0, choose = "I"))
        assertFalse(eng.canDraw(t0.plusMinutes(10)))          // 同一个整点时段只能领一条
        assertNull(eng.draw(t0.plusMinutes(10), choose = "I"))
        assertTrue(eng.canDraw(t0.withMinute(0).plusHours(1))) // 下一个整点又可以
        // 领新的: 上一条作废
        assertNotNull(eng.draw(t0.withMinute(0).plusHours(1), choose = "III"))
        assertEquals(1, eng.actives().size)
        assertEquals("III", eng.current!!.getString("type"))
    }

    @Test
    fun focusCompletesAndSettles() {
        val env = Env()
        val eng = env.engine()
        env.page = "timer"
        eng.draw(t0, choose = "I")
        var now = t0
        repeat(50 * 60) { now = now.plusSeconds(1); eng.tick(now) }
        assertNull(eng.current)
        // 当时不在指令页: 休息时间等回到页面时才结算
        assertTrue(env.rest.isEmpty())
        env.page = "prescript"
        val given = eng.settleRewards(now.plusSeconds(5))
        assertEquals(1, given.size)
        assertEquals("REST3", given[0].getString("code"))
        assertEquals(listOf(180 to "REST3"), env.rest)
        assertTrue(eng.settleRewards(now.plusSeconds(6)).isEmpty())
        assertEquals(1, eng.grantedToday(now).size)
    }

    @Test
    fun voidLeavesPage() {
        val env = Env()
        val eng = env.engine()
        eng.draw(t0, choose = "V")
        env.minimized = true
        eng.pageChanged(t0.plusSeconds(10))
        assertNull(eng.current)
        assertTrue(eng.canDraw(t0.plusSeconds(11)))            // V 作废后可以立即再领
    }

    @Test
    fun statsPageStay() {
        val env = Env()
        val eng = env.engine()
        env.page = "stats"
        eng.draw(t0, choose = "VI")
        var now = t0
        repeat(182) { now = now.plusSeconds(1); eng.tick(now) }
        assertNull(eng.current)
        assertEquals("completed", eng.rawData().getJSONArray("history").getJSONObject(0).getString("result"))
    }

    @Test
    fun devModeKeepsThree() {
        val env = Env()
        env.dev = true
        env.mode = "idle"
        val eng = env.engine()
        assertNotNull(eng.draw(t0, choose = "III"))
        assertNotNull(eng.draw(t0, choose = "I"))
        assertNotNull(eng.draw(t0, choose = "VI"))
        assertEquals(Prescript.DEV_KEEP, eng.actives().size)
        assertFalse(eng.canDraw(t0))
        assertNull(eng.draw(t0, choose = "I"))
        env.mode = "break"
        env.page = "timer"
        var now = t0
        repeat(20 * 60 + 1) { now = now.plusSeconds(1); eng.tick(now) }
        assertEquals(listOf("I", "VI"), eng.actives().map { it.getString("type") })
        assertTrue(eng.canDraw(now))
    }

    @Test
    fun contractMatch() {
        val env = Env()
        val eng = env.engine()
        env.page = "timer"
        eng.draw(t0, choose = "II")
        val n = eng.current!!.getInt("n")
        val rec = JSONObject().put("result", "completed").put("level", n)
            .put("start", t0.plusMinutes(5).iso()).put("planned", t0.plusMinutes(65).iso())
            .put("end", t0.plusMinutes(66).iso())
        env.contracts.add(rec)
        eng.tick(t0.plusMinutes(67))
        assertNull(eng.current)
        assertEquals("REST3", eng.rawData().getJSONArray("pending").getJSONObject(0).getString("code"))
    }

    @Test
    fun saveAndReload() {
        val env = Env()
        val eng = env.engine()
        eng.draw(t0, choose = "III")
        eng.save()
        val again = env.engine()
        assertEquals("III", again.current!!.getString("type"))
    }
}
