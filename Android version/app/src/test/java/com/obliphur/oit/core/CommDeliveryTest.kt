package com.obliphur.oit.core

import com.obliphur.oit.ai.AiConfig
import com.obliphur.oit.ai.AiError
import com.obliphur.oit.ai.Profile
import com.obliphur.oit.ai.ToolProtocol
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.LocalDateTime
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/** 分隔符完整气泡、字数延时、插话改写和本机待发存档。 */
class CommDeliveryTest {
    @Test fun visionAnalysisIsNotAReplySegment() {
        val root = Files.createTempDirectory("oit").toFile()
        val dir = File(root, "d").also { it.mkdirs() }
        val personas = File(root, "p").also { it.mkdirs() }
        File(personas, "小夏.txt").writeText("你是小夏")
        AiConfig(File(dir, "ai_config.json")).also {
            it.provider = "custom"
            it.profiles["custom"] = Profile("custom", "http://localhost:1/v1", "openai", "chat", "")
            it.profiles["claude"] = Profile("claude", "http://localhost:2/v1", "openai", "vision", "")
            it.assignTask("vision", "claude")
            it.save()
        }
        val oldDelay = Comm.charDelay
        val oldCjk = Comm.charDelayCjk
        var calls = 0
        var analysisIsolated = false
        val c = Comm(dir, personas, { LocalDateTime.of(2026, 11, 18, 12, 0) }) { profile, _, _ ->
            calls++
            if (profile.model == "vision") {
                analysisIsolated = Comm.deltaLocal.get() == null
                Comm.deltaLocal.get()?.invoke("分析草稿")
                "分析草稿"
            } else {
                if (calls == 1) throw AiError("http", "服务商返回 400")
                val text = "正文第一条\n<oit_message>\n正文第二条"
                Comm.deltaLocal.get()!!(text)
                ToolProtocol.pack(text = text)
            }
        }
        try {
            Comm.charDelay = 0.0; Comm.charDelayCjk = 0.0
            c.set(persona = "小夏.txt"); c.snapshot = { CommSnap("idle") }
            assertEquals("", c.sendImage("GIF89a..".toByteArray(), "看这张图"))
            val until = System.nanoTime() + TimeUnit.SECONDS.toNanos(5)
            do { c.tick(); Thread.sleep(5) } while (c.busy && System.nanoTime() < until)
            assertFalse(c.busy)
            assertTrue(c.error.isEmpty())
            assertEquals(3, calls)
            assertTrue(analysisIsolated)
            assertEquals(listOf("正文第一条", "正文第二条"), c.visible().filter { it.role == "ai" }.map { it.text })
        } finally {
            Comm.charDelay = oldDelay; Comm.charDelayCjk = oldCjk
            c.flush(); c.voice.close(); root.deleteRecursively()
        }
    }

    @Test fun messageBoundaries() {
        assertEquals(listOf("甲\n\n乙"), ReplyParts.split("甲\n\n乙"))
        assertEquals(listOf("甲", "乙"), ReplyParts.split("<oit_message>\n甲\r\n<oit_message>\r\n\n<oit_message>\n乙\n<oit_message>"))
        assertEquals(listOf("甲", "乙", "丙"), ReplyParts.split("甲<oit_message>乙 <OIT-MESSAGE> 丙"))
        assertEquals(listOf("甲", "乙", "丙"), ReplyParts.split("甲&lt; oit_message &gt;乙<oit_message   >丙"))
        assertEquals(listOf("```txt\n<oit_message>\n```", "乙"), ReplyParts.split("```txt\n<oit_message>\n```\n<oit_message>\n乙"))
        assertEquals(listOf("```txt\n正文 <oit_message> 仍在代码块\n```\n甲", "乙"), ReplyParts.split("```txt\n正文 <oit_message> 仍在代码块\n```\n甲<oit_message>乙"))
        val parts = ReplyParts()
        assertTrue(parts.feed("甲\n<oit_mes").isEmpty())
        assertEquals(listOf("甲"), parts.feed("sage>\n乙"))
        assertEquals(listOf("乙"), parts.feed("", final = true))
    }

    @Test fun delayedBubblesAndInterruption() {
        val root = Files.createTempDirectory("oit").toFile()
        val dir = File(root, "d").also { it.mkdirs() }
        val personas = File(root, "p").also { it.mkdirs() }
        File(personas, "小夏.txt").writeText("你是小夏")
        AiConfig(File(dir, "ai_config.json")).also {
            it.provider = "custom"
            it.profiles["custom"] = Profile("custom", "http://localhost:1/v1", "openai", "m", "")
            it.save()
        }
        var clock = LocalDateTime.of(2026, 11, 18, 23, 59, 59)
        data class Task(val text: String, val chunks: List<String> = emptyList(), val fail: Boolean = false,
                        val ready: CountDownLatch = CountDownLatch(1), val done: CountDownLatch = CountDownLatch(1))
        val tasks = LinkedBlockingQueue<Task>()
        val all = ArrayList<Task>()
        val calls = ArrayList<List<Pair<String, String>>>()
        val oldDelay = Comm.charDelay
        val oldCjk = Comm.charDelayCjk
        val c = Comm(dir, personas, { clock }) { _, _, history ->
            val task = tasks.poll(3, TimeUnit.SECONDS) ?: error("未安排响应")
            calls.add(history.toList())
            val delta = Comm.deltaLocal.get()!!
            task.chunks.forEach(delta)
            task.ready.countDown()
            check(task.done.await(5, TimeUnit.SECONDS))
            delta(task.text.drop(task.chunks.sumOf { it.length }))
            if (task.fail) throw AiError("network", "流式响应中断，回复尚未完成")
            ToolProtocol.pack(text = task.text)
        }
        fun task(text: String, chunks: List<String> = emptyList(), fail: Boolean = false): Task =
            Task(text, chunks, fail).also { tasks.add(it); all.add(it) }
        fun pump(until: () -> Boolean) {
            val end = System.nanoTime() + TimeUnit.SECONDS.toNanos(5)
            while (!until() && System.nanoTime() < end) { c.tick(); Thread.sleep(5) }
            assertTrue("后台请求未按预期结束", until())
        }
        fun start(task: Task, message: String = "在吗", delay: Long = 0L) {
            assertEquals("", c.send(message, delay))
            assertTrue(task.ready.await(3, TimeUnit.SECONDS))
            c.tick()
        }
        fun end(task: Task) { task.done.countDown(); pump { !c.busy } }
        var restarted: Comm? = null
        try {
            Comm.charDelay = 0.1; Comm.charDelayCjk = 0.2
            c.set(persona = "小夏.txt"); c.snapshot = { CommSnap("idle") }
            val first = task("甲\n<oit_message>\n乙\n<oit_message>\n丙", listOf("甲\n<oit_m", "essage>\n"))
            start(first)
            assertEquals(listOf("在吗"), c.visible().map { it.text })
            val pending = c.messages.last()
            assertEquals("甲", pending.text)
            assertFalse(c.syncDoc().containsKey("m:${c.pid}:${pending.uid}"))
            assertTrue(c.apiMessages().none { it.first == "assistant" })
            clock = clock.plusNanos(200_000_000); c.tick()
            assertTrue(c.busy); assertEquals("甲", c.visible().last().text)
            assertTrue(c.syncDoc().containsKey("m:${c.pid}:${pending.uid}"))
            end(first)
            val queued = c.messages.filter { it.pendingReply != null }
            assertEquals(listOf("乙", "丙"), queued.map { it.text })
            assertEquals(200L, queued[1].due - queued[0].due)
            clock = clock.plusNanos(200_000_000); c.tick()
            assertEquals("乙", c.visible().last().text)
            clock = clock.plusNanos(200_000_000); c.tick()
            assertEquals(listOf("在吗", "甲", "乙", "丙"), c.visible().map { it.text })
            assertEquals(1, calls.size)

            val second = task("已发\n<oit_message>\n旧后续很长")
            second.done.countDown(); start(second); pump { !c.busy }
            clock = clock.plusNanos(400_000_000); c.tick()
            assertEquals("已发", c.visible().last().text)
            val revision = task("按新消息修改")
            start(revision, "改一下")
            assertFalse(c.messages.any { it.text == "旧后续很长" })
            val history = calls.last().joinToString("\n") { it.second }
            assertTrue("已发" in history && "改一下" in history && "改写" in history)
            assertFalse("旧后续很长" in history)
            end(revision)
            clock = clock.plusSeconds(5); c.tick()
            assertEquals("按新消息修改", c.visible().last().text)

            val old = task("前段\n<oit_message>\n废弃尾段", listOf("前段\n<oit_message>\n"), fail = true)
            start(old)
            clock = clock.plusNanos(400_000_000); c.tick()
            assertEquals("前段", c.visible().last().text)
            val replacement = task("改写结果")
            assertEquals("", c.send("新的要求")); old.done.countDown()
            pump { replacement.ready.count == 0L }
            assertFalse(c.messages.any { "废弃尾段" in it.text })
            assertFalse("废弃尾段" in calls.last().toString())
            end(replacement)
            clock = clock.plusSeconds(5); c.tick()
            assertEquals("改写结果", c.visible().last().text)

            val remoteOld = task("远端插话前的待发内容")
            remoteOld.done.countDown(); start(remoteOld); pump { !c.busy }
            val count = calls.size
            val ts = (c.messages.maxOfOrNull { it.ts } ?: 0L) + 1
            c.leader = { false }
            c.applyDoc(mapOf("m:${c.pid}:remote-$ts" to JSONObject().put("role", "me").put("text", "远端新要求").put("ts", ts).put("at", clock.toString())))
            c.tick()
            assertFalse(c.messages.any { it.text == "远端插话前的待发内容" })
            assertEquals(count, calls.size); assertFalse(c.busy)
            val remoteNew = task("远端改写结果")
            c.leader = { true }; c.tick()
            assertTrue(remoteNew.ready.await(3, TimeUnit.SECONDS))
            assertTrue("远端新要求" in calls.last().toString())
            assertFalse("远端插话前的待发内容" in calls.last().toString())
            end(remoteNew)
            clock = clock.plusSeconds(5); c.tick()

            val broken = task("保留\n<oit_message>\n不完整尾段", listOf("保留\n<oit_message>\n不完"), fail = true)
            start(broken)
            clock = clock.plusNanos(400_000_000); c.tick()
            assertEquals("保留", c.visible().last().text)
            end(broken)
            assertEquals("保留", c.visible().last().text)
            assertTrue(c.error.isNotEmpty())
            assertFalse(c.messages.any { "不完整尾段" in it.text })

            val saved = task("持久化正文")
            saved.done.countDown(); start(saved); pump { !c.busy }; c.flush()
            restarted = Comm(dir, personas, { clock }) { _, _, _ -> error("恢复队列不应重发 API") }
            clock = clock.plusSeconds(10); restarted.tick()
            assertEquals("持久化正文", restarted.visible().last().text)
            c.clear(); clock = clock.withHour(12); c.set(persona = "Assistant.txt")
            val instant = task("第一条\n<oit_message>\n第二条", listOf("第一条\n<oit_message>\n第"))
            start(instant)
            assertTrue(c.busy); assertEquals("第一条", c.visible().last().text)
            assertTrue(c.visible().last().at.startsWith("2026-"))
            end(instant)
            assertEquals(listOf("第一条", "第二条"), c.visible().filter { it.role == "ai" }.map { it.text })
            val stale = task("过期\n<oit_message>\n尾段", listOf("过期\n<oit_message>\n"))
            start(stale); c.clear(); end(stale)
            assertTrue(c.visible().isEmpty())
            val beforeClear = task("清空前旧内容")
            start(beforeClear); c.clear()
            val fresh = task("新会话回复")
            assertEquals("", c.send("新会话问题")); beforeClear.done.countDown()
            pump { fresh.ready.count == 0L }; end(fresh)
            assertEquals(listOf("新会话问题", "新会话回复"), c.visible().map { it.text })
        } finally {
            all.forEach { it.done.countDown() }
            Comm.charDelay = oldDelay; Comm.charDelayCjk = oldCjk
            c.flush(); c.voice.close(); restarted?.flush(); restarted?.voice?.close()
            root.deleteRecursively()
        }
    }
}
