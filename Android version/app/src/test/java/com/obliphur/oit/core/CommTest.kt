package com.obliphur.oit.core

import com.obliphur.oit.ai.AiConfig
import com.obliphur.oit.ai.AiError
import com.obliphur.oit.ai.Profile
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.LocalDateTime

/** 沟通 (MECHANICS 24). 对应桌面版 comm.py 的 _selftest. */
class CommTest {
    private var clock = LocalDateTime.of(2026, 11, 2, 9, 0, 0)
    private val calls = mutableListOf<Pair<String, List<Pair<String, String>>>>()
    private val script = ArrayDeque<String>()

    private fun comm(dir: File, personas: File): Comm {
        // 本机地址不要求 Key: 单元测试里没有 Android Keystore
        AiConfig(File(dir, "ai_config.json")).also {
            it.provider = "custom"
            it.profiles["custom"] = Profile("custom", "http://localhost:1/v1", "openai", "m", "")
            it.save()
        }
        return Comm(dir, personas, { clock }) { _, system, msgs ->
            calls.add(system to msgs.toList())
            val r = script.removeFirst()
            if (r.startsWith("ERR:")) throw AiError("network", r.substring(4))
            r
        }
    }

    private fun wait(c: Comm) {
        for (i in 0 until 300) {
            Thread.sleep(10)
            c.tick()
            if (!c.busy) return
        }
        throw AssertionError("超时")
    }

    @Test
    fun prefixAndPersonas() {
        val p = CommText.prefixPrompt()
        assertTrue(p.startsWith("你需要回复来自自律软件OIT用户的信息"))
        for (k in listOf("I.", "II.", "III.", "IV.根据你的专注时间段", "不超过5min", CommText.REFUSE, "默认你的每轮专注时间总是50min", "ln1.2"))
            assertTrue(k, k in p)
        assertTrue(p.trimEnd().endsWith("以下是用户设定的人设:"))
        assertTrue(CommText.readme().startsWith("如何编写人设prompt:") && p in CommText.readme())

        val td = Files.createTempDirectory("oit").toFile()
        val c = comm(File(td, "d").also { it.mkdirs() }, File(td, "personas"))
        assertTrue(File(td, "personas/README.md").exists() && c.listPersonas() == listOf("Claudia.txt"))
        assertEquals("小夏.txt", c.importPersona("小夏.txt", "你是小夏, 大学生.".byteInputStream()))
        assertEquals("小夏 (2).txt", c.importPersona("小夏.txt", "x".byteInputStream()))
        assertEquals("", c.importPersona("x.json", "x".byteInputStream()))
        assertEquals(listOf("Claudia.txt", "小夏 (2).txt", "小夏.txt"), c.listPersonas())
        assertTrue("大学生" in c.readPersona("小夏.txt") && c.readPersona("../x").isEmpty())
        c.set(persona = "")
        assertEquals("还没有选人设", c.send("hi"))
    }

    @Test
    fun flow() {
        val td = Files.createTempDirectory("oit").toFile()
        val dir = File(td, "d").also { it.mkdirs() }
        val c = comm(dir, File(td, "personas"))
        c.importPersona("小夏.txt", "你是小夏.每天 8 点到 12 点上课.".byteInputStream())
        c.set(persona = "小夏.txt")
        c.hourLines = { listOf("08:00 起床", "09:00 写代码") }
        val snap = CommSnap("break", restLeftMin = 12.4, focusTotalMin = 200.0, breakStart = clock, breakTotalS = 1800.0)
        c.snapshot = { snap }

        script.add("早!")
        assertEquals("", c.send("早"))
        wait(c)
        val (system, msgs) = calls.last()
        assertTrue(system.startsWith(CommText.prefixPrompt()) && system.endsWith("上课."))
        val first = msgs[0].second
        assertTrue("2026-11-02 周一 09:00" in first && "整点记录: 08:00 起床; 09:00 写代码" in first)
        assertTrue("剩余休息时间: 12 分钟" in first && "今日总专注时长: 3 小时 20 分钟" in first && first.endsWith("早"))
        assertEquals(listOf("me", "ai"), c.visible().map { it.role })
        assertEquals(1, c.unread)

        // 30 分钟内同类信息不重复, 满 30 分钟又发
        clock = clock.plusMinutes(29)
        script.add("嗯"); c.send("在吗"); wait(c)
        assertFalse("整点记录" in calls.last().second.last().second || "OIT 附带信息" in calls.last().second.last().second)
        clock = clock.plusMinutes(2)
        script.add("嗯"); c.send("还在"); wait(c)
        assertTrue("整点记录" in calls.last().second.last().second && "计时器" in calls.last().second.last().second)

        // 不方便: 不显示, 消息留着, 10 分钟后一次性交给模型
        script.add(CommText.REFUSE)
        c.send("上课吗"); wait(c)
        assertTrue(c.visible().last().role == "me" && c.messages.none { CommText.REFUSE in it.text })
        script.add("我下课了! 刚才说什么?")
        c.tick(); assertFalse(c.busy)
        clock = clock.plusMinutes(11)
        c.tick(); assertTrue(c.busy)
        wait(c)
        assertEquals("ai", c.visible().last().role)
        assertTrue("还没有回复" in calls.last().second.last().second)

        // 出错: 消息保留
        script.add("ERR:连接失败")
        c.send("喂"); wait(c)
        assertEquals("连接失败", c.error)
        assertEquals("me", c.visible().last().role)

        // 重开: 记录和设置都在
        c.flush()
        val c2 = Comm(dir, File(td, "personas"), { clock }) { _, _, _ -> "" }
        assertEquals("小夏.txt", c2.persona)
        assertEquals(c.visible().size, c2.visible().size)

        // /clear: 之前的记录不再显示, 也不再发给模型; 信息重新附带; 清空前发出的请求回复到了也丢掉
        c.hourLines = { listOf("09:00 写代码") }
        c.clear()
        script.add("晚安"); c.send("我先睡了"); wait(c)
        assertEquals(2, c.visible().size)
        assertEquals("", c.send(" /CLEAR "))
        assertTrue(c.visible().isEmpty() && c.messages.isEmpty())
        script.add("新的开始"); c.send("你好"); wait(c)
        val sent = calls.last().second
        assertTrue(sent.size == 1 && "我先睡了" !in sent[0].second && "整点记录" in sent[0].second)
        script.add("迟到的回复"); c.send("在吗"); c.clear(); wait(c)
        assertTrue(c.visible().isEmpty() && !c.busy)
        c.flush()
        assertTrue(Comm(dir, File(td, "personas"), { clock }) { _, _, _ -> "" }.visible().isEmpty())

        // 发数据: 聊天里一行说明, 数据附在发给模型的内容里
        c.clear()
        c.dataFn = { "【统计·联网理由】\n  学习资料: 15 分钟" }
        script.add("看到了")
        assertEquals("", c.sendData(CommSel(stats = listOf("net")), "帮我看看")); wait(c)
        assertEquals("📎 统计·联网理由\n帮我看看", c.visible()[0].text)
        assertEquals("ai", c.visible()[1].role)
        val api = calls.last().second.last().second
        assertTrue("(用户发来的数据)" in api && "学习资料: 15 分钟" in api && api.endsWith("帮我看看"))
        assertEquals("empty", c.sendData(CommSel()))
        c.dataFn = { "" }
        assertEquals("没有可发送的数据", c.sendData(CommSel(stats = listOf("net"))))
        c.fog = { true }
        assertEquals("黑雾中无法发送", c.sendData(CommSel(stats = listOf("net"))))
        // 📎 确定过的附加数据: 随下一条消息发出, 只写附加数据不写字也能发
        c.fog = { false }
        c.dataFn = { "【日历·专注】\n  11-01: 3.0 h" }
        c.setAttach(CommSel(cal = listOf("study")))
        script.add("嗯嗯")
        assertEquals("", c.send("")); wait(c)
        assertEquals("", CommData.label(c.attach))
        assertEquals("📎 日历·专注", c.visible()[c.visible().size - 2].text)
        assertEquals("empty", c.send(""))
        // 无言为真: 不能发, 不会主动发, 也不会重试
        c.dataFn = { "x" }
        c.blocked = { "无言为真生效中, 无法使用沟通" }
        val n = c.messages.size
        assertEquals("无言为真生效中, 无法使用沟通", c.send("hi"))
        assertEquals("无言为真生效中, 无法使用沟通", c.sendData(CommSel(stats = listOf("net"))))
        c.tick()
        assertTrue(c.messages.size == n && !c.busy)
        c.blocked = { "" }
        assertEquals("📎 统计·整点记录、应用 / 日历·睡眠 / 合约词条",
            CommData.label(CommSel(listOf("log", "window"), listOf("sleep"), true)))
    }

    @Test
    fun infoRequests() {
        assertEquals(listOf("contract"), CommText.infoTags(" [Refuse to reply] [CONTRACT] "))
        assertTrue(CommText.infoTags("[contract]").isEmpty() && CommText.infoTags("好的").isEmpty())
        assertTrue(CommText.infoTags("[refuse to reply][mechanics.md]").isEmpty())
        val p = CommText.prefixPrompt()
        assertTrue("V.你需要了解OIT的独占功能'合约'" in p && "[refuse to reply][contract]" in p && "mechanics" !in p)
        val td = Files.createTempDirectory("oit").toFile()
        val c = comm(File(td, "d").also { it.mkdirs() }, File(td, "p"))
        c.set(persona = "Claudia.txt")
        c.infoFn = { tag -> mapOf("contract" to "词条库: 持久战")[tag] ?: "" }
        val n = calls.size
        script.addAll(listOf("[refuse to reply][contract]", "合约里有持久战."))
        assertEquals("", c.send("合约是什么")); wait(c)
        assertEquals(n + 2, calls.size)                       // 要了一次资料, 共调用两次
        assertTrue("词条库: 持久战" in calls.last().second.last().second)
        assertEquals(listOf("合约是什么", "合约里有持久战."), c.visible().map { it.text })
        assertTrue(c.messages.none { "词条库" in it.api })        // 资料不写进聊天记录
        // 要不到资料: 当作不方便
        c.infoFn = { "" }
        script.add("[refuse to reply][contract]")
        c.send("再问一次"); wait(c)
        assertEquals("再问一次", c.visible().last().text)
    }

    @Test
    fun builtinPersona() {
        val td = Files.createTempDirectory("oit").toFile()
        val dir = File(td, "d").also { it.mkdirs() }
        val c = comm(dir, File(td, "pp"))
        assertEquals(listOf("Claudia.txt"), c.listPersonas())
        assertEquals("Claudia.txt", c.personaName())            // 第一次运行默认选中
        assertTrue(c.readPersona("Claudia.txt").startsWith("你将扮演一位文静,冷淡的大小姐Claudia"))
        c.set(persona = "")
        File(td, "pp/Claudia.txt").delete()
        c.flush()
        val c2 = Comm(dir, File(td, "pp"), { clock }) { _, _, _ -> "" }
        assertTrue(c2.listPersonas().isEmpty() && c2.personaName().isEmpty())     // 删掉之后不再写回, 也不再自动选
    }

    @Test
    fun deviceSync() {
        val td = Files.createTempDirectory("oit").toFile()
        val a = comm(File(td, "a").also { it.mkdirs() }, File(td, "pa"))
        val b = comm(File(td, "b").also { it.mkdirs() }, File(td, "pb"))
        a.device = { "aaaa" }; b.device = { "bbbb" }
        for (c in listOf(a, b)) {
            c.importPersona("小夏.txt", "你是小夏".byteInputStream())
            c.set(persona = "小夏.txt", sendHour = false, sendTimer = false)
        }
        fun pull(src: Comm, dst: Comm) = dst.applyDoc(src.syncDoc())

        script.add("A 的回复")
        assertEquals("", a.send("你好")); wait(a)
        pull(a, b)
        assertEquals(listOf("你好", "A 的回复"), b.visible().map { it.text })
        assertEquals(1, b.unread)
        pull(a, b)
        assertEquals(2, b.visible().size)               // 同一条不会并两次
        assertEquals("aaaa", b.lastAuthor())

        // 在 B 上发: B 调用 AI, 成为主端, 回复同步回 A
        script.add("B 的回复")
        b.send("在吗"); wait(b)
        pull(b, a)
        assertEquals(listOf("在吗", "B 的回复"), a.visible().map { it.text }.takeLast(2))
        assertEquals("bbbb", a.lastAuthor())

        // 对方不方便: 重试状态同步; 最晚消息的作者 (主端) 才重试
        script.add(CommText.REFUSE)
        a.send("上课吗"); wait(a)
        pull(a, b)
        assertTrue(b.visible().last().role == "me")
        clock = clock.plusMinutes(11)
        a.leader = { false }; b.leader = { false }
        b.tick(); assertFalse(b.busy)                   // 不是主端就不重试
        b.leader = { true }
        script.add("来了, 刚下课")
        b.tick(); assertTrue(b.busy)
        wait(b)
        pull(b, a)
        assertEquals("来了, 刚下课", a.visible().last().text)

        // 清空一起清, 清空之前的记录同步回来也不会复活
        b.clear()
        pull(b, a)
        assertTrue(a.visible().isEmpty())
        pull(a, b)
        assertTrue(b.visible().isEmpty())
    }

    @Test
    fun proactive() {
        val td = Files.createTempDirectory("oit").toFile()
        val c = comm(File(td, "d").also { it.mkdirs() }, File(td, "personas"))
        c.importPersona("小夏.txt", "你是小夏".byteInputStream())
        c.set(persona = "小夏.txt")
        val start = clock
        var snap = CommSnap("break", restLeftMin = 25.0, focusTotalMin = 60.0, breakStart = start, breakTotalS = 25 * 60.0)
        c.snapshot = { snap }
        c.tick(); assertFalse(c.busy)
        clock = start.plusMinutes(2).plusSeconds(59)
        c.tick(); assertFalse(c.busy)
        script.add("休息啦, 在干嘛?")
        clock = start.plusMinutes(5).plusSeconds(1)
        c.tick(); assertTrue(c.busy)
        wait(c)
        assertEquals(listOf("ai"), c.visible().map { it.role })
        assertTrue("主动给用户发一条消息" in calls.last().second.last().second)
        c.tick(); assertFalse("同一次休息只主动发一次", c.busy)
        // 休息不到 20 分钟不发
        val s2 = clock.plusMinutes(1)
        snap = CommSnap("break", breakStart = s2, breakTotalS = 15 * 60.0)
        clock = s2.plusMinutes(6)
        c.tick(); assertFalse(c.busy)
        // 主动消息被拒绝: 什么也不留下
        snap = CommSnap("break", breakStart = clock, breakTotalS = 30 * 60.0)
        script.add(CommText.REFUSE)
        clock = clock.plusMinutes(5)
        c.tick(); wait(c)
        assertEquals(1, c.visible().size)
        assertEquals(1, c.messages.count { it.role == "sys" })
    }
}
