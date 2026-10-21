package com.obliphur.oit.core

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.LocalDateTime

/** 一台设备用 Adapter 断网期间各自记下的东西 (MECHANICS 15.5). 对应桌面版 contract.py / data_manager.py 的测试. */
class AdapterSyncTest {
    private fun tmp(name: String = "contract.json"): File = File(Files.createTempDirectory("oit").toFile(), name)
    private fun at(h: Int, m: Int = 0): LocalDateTime = LocalDateTime.of(2026, 10, 21, h, m)

    /** 进行中的合约被同步来的另一个合约盖掉 → 冲突; 选了留下哪个, 另一个作废 (随同步). */
    @Test
    fun contractConflictKeepOne() {
        val t0 = LocalDateTime.now().minusHours(1).withNano(0)
        val a = Contract(tmp())
        val b = Contract(tmp())
        a.start(listOf("long1"), "A", t0)
        Thread.sleep(5)
        b.start(listOf("music", "game"), "B", t0.plusMinutes(30))
        val aId = a.live.opt("id").toString()
        val bId = b.live.opt("id").toString()
        a.merge(mapOf("live" to b.doc()["live"]))
        assertEquals(bId, a.live.opt("id").toString())
        assertEquals(1, a.conflicts.size)
        assertTrue(a.resolveConflict(aId))
        assertEquals(aId, a.live.opt("id").toString())
        assertTrue(a.conflicts.isEmpty() && bId in a.voided)
        assertEquals(1, a.doc()["void:$bId"])
        b.merge(mapOf("void:$bId" to 1))
        assertFalse("作废的进行中合约在另一台设备上也结束", b.active)
    }

    /** 别的设备结束了这个合约又开始了新的 (送来的记录里有本机的这个) → 不算冲突. */
    @Test
    fun normalHandoverIsNoConflict() {
        val t0 = LocalDateTime.now().minusHours(1).withNano(0)
        val f = Contract(tmp())
        val g = Contract(tmp())
        f.start(listOf("long1"), "F", t0)
        g.merge(f.doc())
        g.finish("abandoned", t0.plusMinutes(10))
        Thread.sleep(5)
        g.start(listOf("fog"), "G", t0.plusMinutes(11))
        f.merge(g.doc())
        assertTrue(f.conflicts.isEmpty())
        assertEquals(g.live.opt("id").toString(), f.live.opt("id").toString())
    }

    /** 重叠的记录按 休息 > 专注 > 空闲: 只删本机的; 一样的只由断网的那台删 (手机不会是断网的那台). */
    @Test
    fun overlapBreakFocusIdle() {
        val dm = DataStore(Files.createTempDirectory("oit").toFile(), { 3600.0 }, { "Sleeping" })
        dm.addFocusSession("study", at(10), at(11))
        dm.addFocusSession("break", at(11), at(11, 20))
        dm.addFocusSession("idle", at(12), at(13), at(9))
        dm.study["2026-10-21"] = 3.0
        fun rec(k: String, a: LocalDateTime, b: LocalDateTime) =
            JSONObject().put("k", k).put("s", a.format(Fmt.TS)).put("e", b.format(Fmt.TS))
        val peer = listOf(rec("study", at(10, 30), at(11, 10)), rec("break", at(12, 30), at(12, 40)),
            rec("study", at(12, 40), at(14)), rec("break", at(10, 0), at(10, 15)))
        val r = dm.resolveOverlap(peer, at(9), at(15), tieMine = false)
        assertEquals(900.0, r["study"]!!, 1e-6)                 // 被别人的休息盖住的专注 (10:00 ~ 10:15)
        assertEquals(0.0, r["break"]!!, 1e-6)                   // 自己的休息不删
        assertEquals(1800.0, r["idle"]!!, 1e-6)                 // 被别人的休息 / 专注盖住的空闲
        assertEquals(3.0 - 0.25, dm.study["2026-10-21"]!!, 1e-9)
        assertTrue("专注撞专注: 手机不删", dm.focus.any { it.kind == "study" && it.start == at(10, 15) && it.end == at(11) })
        assertTrue(dm.focus.any { it.kind == "idle" && it.end == at(12, 30) && it.focus.isNotEmpty() })
    }
}
