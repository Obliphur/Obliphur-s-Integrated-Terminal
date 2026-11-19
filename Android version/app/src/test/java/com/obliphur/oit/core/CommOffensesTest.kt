package com.obliphur.oit.core

import com.obliphur.oit.ai.ToolProtocol
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** 冒犯记录、倍率、清除权限和同步墓碑与桌面端保持一致。 */
class CommOffensesTest {
    private val now = 1805491140000L
    private val record = CommOffense("user:1", now, "无视拒绝，继续纠缠", now)

    @Test fun ratesAndPersistence() {
        for (count in 0..6) {
            val d = CommInteraction(offenses = (0 until count).map { record.copy(id = "user:$it") })
            assertEquals(maxOf(0, 40 - count * 8).toDouble(), d.addValence(listOf("joy" to 40), now, 1.0).valence, 1e-8)
            assertEquals(-40.0, d.addValence(listOf("hurt" to -40), now, 1.0).valence, 1e-8)
            assertEquals(d, CommInteraction.parse(d.json()))
            assertEquals(d.offenses, d.roll(now + 86400000L).offenses)
        }
        val d = CommInteraction(offenses = listOf(record), burstUntil = 1,
            valenceCooldowns = mapOf("joy" to now + 1), valenceChainUntil = now + 1)
            .addValence(listOf("joy" to 40), now, 0.5)
        assertEquals(4.0, d.valence, 0.0)
        val reply = ToolProtocol.pack("平静下来", "oit_reply", JSONObject().put("end_burst", true))
        assertEquals(20.0, d.endBurst(reply, 1, now, 20).valence, 0.0)
    }

    @Test fun claudiaSleepValenceRates() {
        assertEquals(20.0, CommInteraction().addValence(listOf("joy" to 40), now, 1.0, sleep = true).valence, 0.0)
        assertEquals(-8.0, CommInteraction().addValence(listOf("hurt" to -40), now, 1.0, sleep = true).valence, 0.0)
        val pending = CommInteraction(pending = mapOf("sleeping-message" to false),
            pendingSleep = setOf("sleeping-message", "stale-message"))
        assertEquals(setOf("sleeping-message"), CommInteraction.parse(pending.json()).pendingSleep)
        assertTrue(pending.note(0, false, mapOf("joy" to (null to "开心")), sleep = true).contains("0.5"))
    }

    @Test fun offensesShiftValenceEquilibrium() {
        val day = 86400000L
        val baseline = CommInteraction(valence = 5.0, valenceInitial = 15.0, valenceDecayAt = now,
            offenses = listOf(record, record.copy(id = "user:2")))
        assertTrue(baseline.note(0, false).contains("INITIAL_VALENCE=-5.0,"))
        assertEquals(5.0, baseline.roll(now).valence, 0.0)
        val settled = baseline.roll(now + day)
        assertEquals(-5.0, settled.valence, 0.0)
        assertEquals(15.0, settled.valenceInitial, 0.0)
        val forgiven = settled.copy(offenses = listOf(record.copy(forgiven = true), settled.offenses[1]))
        assertTrue(forgiven.note(0, false).contains("INITIAL_VALENCE=5.0,"))
        assertEquals(5.0, forgiven.roll(now + 2 * day).valence, 0.0)
        assertEquals(-100.0, forgiven.copy(valenceInitial = -95.0).effectiveValenceBaseline(), 0.0)
    }

    @Test fun additionsAndForgiveness() {
        fun reply(action: String, target: String = "", index: Int = 1): String = ToolProtocol.pack("尊重边界", "oit_reply",
            JSONObject().put("offenses", JSONArray().put(JSONObject().put("index", index).put("action", action)
                .put("target", target).put("reason", "根据对话判断"))))
        val add = reply("add")
        var records = CommOffenses.apply(emptyList(), add, 1, record.id, now, now)
        assertEquals(1, records.size)
        assertEquals(now, records.first().at)
        assertEquals(records, CommOffenses.apply(records, add, 1, record.id, now, now))
        assertEquals(records, CommOffenses.apply(records, reply("forgive", record.id, 2), 1, "user:2", now, now))
        assertEquals(records, CommOffenses.apply(records, reply("remove", record.id), 1, "user:2", now, now))
        records = CommOffenses.apply(records, reply("forgive", record.id), 1, "user:2", now + 1, now + 1)
        assertTrue(CommOffenses.active(records).isEmpty())
        assertEquals("", records.first().reason)
        assertEquals(records, CommOffenses.merge(records + listOf(record)))
        assertEquals(records, CommOffenses.parse(JSONArray(records.map { it.json() })))
        assertEquals("", CommOffenses.line(records))
        assertFalse(ToolProtocol.args(CommInteraction.cleanTension(add)).has("offenses"))
        assertTrue(CommOffenses.line(listOf(record)).contains(record.reason))
        assertTrue(CommInteraction(offenses = listOf(record)).note(0, false).contains("OFFENSE_COUNT=1"))
    }
}
