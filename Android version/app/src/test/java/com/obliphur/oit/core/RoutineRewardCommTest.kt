package com.obliphur.oit.core

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.LocalDateTime

/** Claudia 独立休息结算的重启、同步与共同休息接续。 */
class RoutineRewardCommTest {
    @Test
    fun jointRestSurvivesRestartAndSync() {
        val root = Files.createTempDirectory("oit_reward").toFile()
        try {
            val personas = File(root, "personas").also { it.mkdirs() }
            File(personas, "Claudia.txt").writeText("你是 Claudia。")
            File(personas, "Claudia_templates.txt").writeText(
                "初始起点:7:00\n[循环 300min][100min]\n60min:空闲\n[循环 300min][100min]\n30min:特殊空闲")
            var clock = LocalDateTime.of(2026, 11, 20, 10, 0)
            fun make(name: String): Comm {
                val dir = File(root, name).also { it.mkdirs() }
                return Comm(dir, personas, { clock }) { _, _, _ -> "" }.also {
                    it.set(persona = "Claudia.txt", splitSend = false)
                    it.stateFn = { "break" }
                    it.breakEndFn = { clock.withHour(10).withMinute(30) }
                }
            }
            fun begin(c: Comm, id: String) {
                val ts = (c.syncDoc()["joint"] as? JSONObject)?.optLong("ts") ?: 0L
                c.applyDoc(mapOf("joint" to JSONObject().put("id", id).put("cid", id).put("pid", Comm.pidOf("Claudia.txt"))
                    .put("state", "run").put("terms", JSONArray(listOf("deep1"))).put("began", clock.minusHours(3).toString())
                    .put("start", clock.minusHours(3).toString()).put("until", clock.toString()).put("end", clock.toString())
                    .put("ts", ts + 1).apply {
                        (c.syncDoc()["joint"] as? JSONObject)?.optJSONArray("rest_history")?.let { put("rest_history", it) }
                    }))
            }
            val c = make("one")
            begin(c, "complete")
            assertTrue(c.jointEnded("complete", "completed", 1800.0))
            assertFalse(c.jointEnded("complete", "completed", 1800.0))
            val settled = c.syncDoc()["joint"] as JSONObject
            assertTrue(settled.getBoolean("scheduled_reward"))
            assertEquals(clock.withMinute(30), LocalDateTime.parse(settled.getString("rest_until")))
            clock = clock.withMinute(45)
            assertFalse(c.jointLive())
            assertEquals("rest", c.otherStatus()!!.first.kind)
            val clone = make("two")
            val doc = c.syncDoc()
            clone.applyDoc(doc)
            clone.applyDoc(doc)
            assertEquals(c.noteForTest(clock), clone.noteForTest(clock))
            c.flush()
            val reload = make("one")
            assertEquals(c.noteForTest(clock), reload.noteForTest(clock))
            clock = clock.withHour(11).withMinute(40)
            c.stateFn = { "idle" }
            begin(c, "failed")
            assertTrue(c.jointEnded("failed", "failed", 1500.0))
            assertFalse(c.jointLive())
            assertEquals("rest", c.otherStatus()!!.first.kind)
            assertTrue(c.rtStoreForTest()!!.recentContracts(clock, 30).none { it.optString("id") == "failed" })
            val history = (c.syncDoc()["joint"] as JSONObject).getJSONArray("rest_history")
            assertEquals(2, history.length())
            val next = JSONObject().put("id", "next").put("pid", Comm.pidOf("Claudia.txt")).put("state", "yes")
                .put("start", clock.plusHours(5).toString()).put("end", clock.plusHours(8).toString()).put("terms", JSONArray())
                .put("rest_history", history).put("ts", (c.syncDoc()["joint"] as JSONObject).getLong("ts") + 1)
            c.applyDoc(mapOf("joint" to next))
            clone.applyDoc(c.syncDoc())
            clock = clock.withHour(15).withMinute(20)
            assertEquals(c.noteForTest(clock), clone.noteForTest(clock))
            for (item in listOf(c, clone, reload)) item.flush()
        } finally {
            root.deleteRecursively()
        }
    }
}
