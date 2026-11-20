package com.obliphur.oit.core

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.LocalDateTime
import java.time.ZoneId

/** 真实完成奖励独立于模型请求，保留对象归属和既有冒犯倍率。 */
class JointValenceTest {
    private val declaration = "[valence:events]\njoint_completed:30\n[/valence:events]\n"

    @Test
    fun rewardDeclarationIsStrictAndSeparateFromModelConditions() {
        assertEquals(30, CommRules.valenceEvent(declaration, "joint_completed"))
        for (bad in listOf("", declaration.replace("30", "-1"), declaration.replace("30", "101"),
            declaration.replace("30", "X"), declaration + declaration,
            declaration.replace("joint_completed:30", "joint_completed:30\njoint_completed:30")))
            assertEquals(0, CommRules.valenceEvent(bad, "joint_completed"))
        val template = BuiltinPersonas.ALL.getValue(BuiltinPersonas.ROUTINE)
        assertEquals(30, CommRules.valenceEvent(template, "joint_completed"))
        assertFalse(CommRules.valence(template).containsKey("joint_completion"))
    }

    @Test
    fun realCompletionRewardsOnceAcrossMidnightRestartSyncAndPersonaSwitch() {
        val root = Files.createTempDirectory("oit_joint_valence").toFile()
        try {
            var clock = LocalDateTime.of(2026, 11, 20, 23, 59)
            var calls = 0
            fun make(key: String, template: String = declaration): Comm {
                val dir = File(root, key).also { it.mkdirs() }
                val personas = File(dir, "personas").also { it.mkdirs() }
                File(personas, "Claudia.txt").writeText("Claudia")
                File(personas, "Other.txt").writeText("Other")
                File(personas, "Claudia_templates.txt").writeText(template)
                return Comm(dir, personas, { clock }) { _, _, _ -> calls++; "" }.also {
                    it.set(persona = "Claudia.txt", splitSend = false)
                    it.stateFn = { "idle" }
                }
            }
            fun begin(c: Comm) {
                val ts = (c.syncDoc()["joint"] as? JSONObject)?.optLong("ts") ?: 0L
                c.applyDoc(mapOf("joint" to JSONObject().put("id", "joint").put("cid", "joint")
                    .put("pid", Comm.pidOf(c.personaName())).put("state", "run").put("terms", JSONArray())
                    .put("began", clock.minusHours(3).toString()).put("start", clock.minusHours(3).toString())
                    .put("end", clock.toString()).put("until", clock.toString()).put("ts", ts + 1)))
            }
            fun interaction(c: Comm, owner: String = Comm.pidOf(c.personaName())): CommInteraction =
                CommInteraction.parse(c.syncDoc()["interaction:$owner"] as? JSONObject)
            fun setInteraction(c: Comm, state: CommInteraction) {
                val owner = Comm.pidOf(c.personaName())
                c.applyDoc(mapOf("interaction:$owner" to state.copy(ts = interaction(c).ts + 1)
                    .json().put("clear", 0)))
            }
            val c = make("completed")
            assertFalse(c.jointEnded("solo", "completed"))
            begin(c)
            clock = clock.plusMinutes(2)
            assertFalse(c.jointEnded("other-contract", "completed"))
            assertTrue(c.jointEnded("joint", "completed"))
            assertEquals(30.0, interaction(c).valence, 1e-8)
            assertFalse(c.jointEnded("joint", "completed"))
            assertEquals(0, calls)
            val doc = c.syncDoc()
            c.flush()
            val peer = make("peer")
            peer.applyDoc(doc)
            peer.applyDoc(doc)
            assertEquals(30.0, interaction(peer).valence, 1e-8)
            assertFalse(peer.jointEnded("joint", "completed"))
            val reload = make("completed")
            assertEquals(30.0, interaction(reload).valence, 1e-8)
            assertFalse(reload.jointEnded("joint", "completed"))
            for (result in listOf("failed", "abandoned", "idle", "offline")) {
                val item = make(result)
                begin(item)
                assertTrue(item.jointEnded("joint", result))
                assertEquals(0.0, interaction(item).valence, 1e-8)
            }
            val foreign = make("foreign")
            val owner = Comm.pidOf(foreign.personaName())
            begin(foreign)
            foreign.set(persona = "Other.txt")
            assertTrue(foreign.jointEnded("joint", "completed"))
            assertEquals(30.0, interaction(foreign, owner).valence, 1e-8)
            assertEquals(0.0, interaction(foreign).valence, 1e-8)
            for ((key, template, expected) in listOf(Triple("custom", declaration.replace("30", "17"), 17.0),
                Triple("unset", "# no events", 0.0), Triple("disabled", declaration.replace("30", "0"), 0.0))) {
                val item = make(key, template)
                begin(item)
                assertTrue(item.jointEnded("joint", "completed"))
                assertEquals(expected, interaction(item).valence, 1e-8)
            }
            val at = clock.atZone(ZoneId.systemDefault()).toInstant().toEpochMilli()
            val limited = make("limited")
            setInteraction(limited, CommInteraction(valence = 90.0, valenceInitialized = true,
                valenceInitialSet = true, valenceDecayAt = at, valenceChainUntil = at + 10000, burstUntil = at))
            begin(limited)
            assertTrue(limited.jointEnded("joint", "completed"))
            assertEquals(100.0, interaction(limited).valence, 1e-8)
            val penalized = make("offense")
            setInteraction(penalized, CommInteraction(valenceInitialized = true, valenceInitialSet = true,
                valenceDecayAt = at, offenses = listOf(CommOffense("old", at, "test", at))))
            begin(penalized)
            assertTrue(penalized.jointEnded("joint", "completed"))
            assertEquals(24.0, interaction(penalized).valence, 1e-8)
        } finally {
            root.deleteRecursively()
        }
    }
}
