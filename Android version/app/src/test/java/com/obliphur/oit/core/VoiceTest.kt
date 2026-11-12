package com.obliphur.oit.core

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.nio.file.Files
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** 语音生成去重、未播消息筛选与清空期间的并发结果丢弃。 */
class VoiceTest {
    @Test fun playbackRestrictionUsesStateEvenWhenChatIsAllowed() {
        val dir = Files.createTempDirectory("oit_voice_state").toFile()
        val comm = Comm(dir, java.io.File(dir, "personas"))
        try {
            comm.blocked = { "" }
            val msg = ChatMsg(1, "ai", "hello", "", uid = "restricted")
            for (kind in listOf("focus", "sleep", "out", "")) {
                comm.stateFn = { kind }
                assertFalse(comm.voice.playAllowed())
                comm.voice.play("Claudia.txt", msg)
                assertEquals("idle", comm.voice.state)
                assertFalse(comm.voice.played("Claudia.txt", msg))
            }
            for (kind in listOf("idle", "break")) {
                comm.stateFn = { kind }
                assertTrue(comm.voice.playAllowed())
            }
        } finally { comm.voice.close(); dir.deleteRecursively() }
    }
    @Test fun modelListContainsOnlyUniqueSpeechModels() {
        val data = JSONObject("""{"data":[{"id":"speech-new-turbo"},{"id":"text-model"},{"id":"speech-new-hd"},{"id":"speech-new-hd"}]}""")
        assertEquals(listOf("speech-new-hd", "speech-new-turbo"), Voice.parseModels(data))
        assertEquals(listOf("speech-one"), Voice.parseModels(JSONObject("""{"models":["speech-one","text-model"]}""")))
        assertEquals(listOf("speech-two"), Voice.parseModels(JSONObject("""{"data":{"models":[{"model":"speech-two"}]}}""")))
        assertEquals(emptyList<String>(), Voice.parseModels(JSONObject("""{"data":[{"id":"MiniMax-M3"}]}""")))
        val schema = """        model:
          type: string
          enum:
            - speech-2.8-hd
            - speech-2.8-turbo
        text:
          description: speech-unrelated
"""
        assertEquals(listOf("speech-2.8-hd", "speech-2.8-turbo"), Voice.parseModelDoc(schema))
        assertEquals(emptyList<String>(), Voice.parseModelDoc("speech-example"))
    }
    @Test fun cacheAndPersonaRestrictions() {
        val dir = Files.createTempDirectory("oit_voice").toFile()
        var calls = 0
        val voice = Voice(dir) { _, _ -> calls++; "ID3audio".toByteArray() }
        try {
            assertFalse(voice.enabled)
            assertEquals("claudia1999", voice.selected("Claudia.txt")!!.getString("voice_id"))
            assertNull(voice.selected("Assistant.txt"))
            voice.data.put("enabled", true)
            voice.data.getJSONObject("assign").put("Assistant.txt", Voice.CLAUDIA)
            assertNull(voice.selected("Assistant.txt"))
            val m = ChatMsg(1, "ai", "hello", "", uid = "first")
            assertNotNull(voice.request("Claudia.txt", m)!!.get(3, TimeUnit.SECONDS))
            assertNotNull(voice.request("Claudia.txt", m)!!.get(3, TimeUnit.SECONDS))
            assertEquals(1, calls)
            assertFalse(voice.played("Claudia.txt", m))
            val msgs = (0..29).map { ChatMsg(it, "ai", "$it", "", uid = "$it") }
            assertEquals((20..29).toList(), voice.autoplay("Claudia.txt", msgs).map { it.id })
            voice.clear("Claudia.txt")
            assertTrue(dir.walkTopDown().none { it.extension == "mp3" })
        } finally { voice.close(); dir.deleteRecursively() }
    }

    @Test fun designRejectsInvalidInputsBeforeNetwork() {
        for ((mode, prompt, id) in listOf(Triple("clone", "", "valid-name"), Triple("prompt", "", "valid-name"),
            Triple("prompt", "voice", "462298003509468"))) {
            try {
                VoiceDesign.create(JSONObject(), mode, prompt, "", id, "hello")
                fail("无效参数不应发请求")
            } catch (_: IllegalArgumentException) { }
        }
    }

    @Test fun clearingDropsPendingAudioAndFailuresCanRetry() {
        val dir = Files.createTempDirectory("oit_voice").toFile()
        val started = CountDownLatch(1)
        val release = CountDownLatch(1)
        var calls = 0
        val voice = Voice(dir) { _, text ->
            calls++
            if (text == "wait") { started.countDown(); check(release.await(3, TimeUnit.SECONDS)) }
            else if (calls == 2) throw IllegalStateException("failure")
            "ID3audio".toByteArray()
        }
        try {
            voice.data.put("enabled", true)
            val m = ChatMsg(1, "ai", "wait", "", uid = "first")
            val f = voice.request("Claudia.txt", m)!!
            assertSame(f, voice.request("Claudia.txt", m))
            assertTrue(started.await(3, TimeUnit.SECONDS))
            voice.clear("Claudia.txt"); release.countDown()
            assertNull(f.get(3, TimeUnit.SECONDS))
            assertTrue(dir.walkTopDown().none { it.extension == "mp3" })
            val retry = ChatMsg(2, "ai", "retry", "", uid = "retry")
            assertNull(voice.request("Claudia.txt", retry)!!.get(3, TimeUnit.SECONDS))
            assertNotNull(voice.request("Claudia.txt", retry)!!.get(3, TimeUnit.SECONDS))
        } finally { release.countDown(); voice.close(); dir.deleteRecursively() }
    }
}
