package com.obliphur.oit.core

import com.obliphur.oit.ai.Ai
import com.obliphur.oit.ai.AiConfig
import com.obliphur.oit.ai.AiError
import com.obliphur.oit.ai.Profile
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

/** 沟通 (MECHANICS 24). 对应桌面版 comm.py 的 _selftest. */
class CommTest {
    private var clock = LocalDateTime.of(2026, 11, 2, 9, 0, 0)
    private val calls = mutableListOf<Pair<String, List<Pair<String, String>>>>()
    private val script = ArrayDeque<String>()
    /** 第二次调用 (规则 VIII) 单独记: 不占 script / calls, 没有安排的回 [refuse to reply]. */
    private val follow = ArrayDeque<String>()
    private val followCalls = mutableListOf<Pair<String, List<Pair<String, String>>>>()

    private fun comm(dir: File, personas: File): Comm {
        // 本机地址不要求 Key: 单元测试里没有 Android Keystore
        AiConfig(File(dir, "ai_config.json")).also {
            it.provider = "custom"
            it.profiles["custom"] = Profile("custom", "http://localhost:1/v1", "openai", "m", "")
            it.save()
        }
        return Comm(dir, personas, { clock }) { _, system, msgs ->
            if (Comm.FOLLOW_TAG in msgs.last().second) {
                followCalls.add(system to msgs.toList())
                follow.removeFirstOrNull() ?: CommText.REFUSE
            } else {
                calls.add(system to msgs.toList())
                val r = script.removeFirst()
                if (r.startsWith("ERR:")) throw AiError("network", r.substring(4))
                r
            }
        }
    }

    /** 从 begin + lo 秒起, 每 2 秒看一次, 返回开始请求的那一秒 (没有 = null). */
    private fun fireAfter(c: Comm, begin: LocalDateTime, lo: Int = 60, hi: Int = 200): Int? {
        for (s in lo until hi step 2) { clock = begin.plusSeconds(s.toLong()); c.tick(); if (c.busy) return s }
        return null
    }

    /** 日程段开始前 200 秒起, 每 2 秒看一次, 返回开始请求时离开始还有几秒 (负数). */
    private fun fireBefore(c: Comm, begin: LocalDateTime): Int? {
        for (s in -200 until 0 step 2) { clock = begin.plusSeconds(s.toLong()); c.tick(); if (c.busy) return s }
        return null
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
        for (k in listOf("I.", "II.", "III.", "IV.根据你的专注时间段", "不超过5min", CommText.REFUSE, "默认你的每轮专注时间总是100min", "ln1.2"))
            assertTrue(k, k in p)
        assertTrue(p.trimEnd().endsWith("以下是用户设定的人设:"))
        assertTrue("VI.不要主动透露日程/专注状态." in p && "用户3min前以内还在你聊天,则和用户说明要进入专注并停止对话." in p && "50min" !in p)
        assertTrue(CommText.readme().startsWith("如何编写人设prompt:") && p in CommText.readme())

        val td = Files.createTempDirectory("oit").toFile()
        val c = comm(File(td, "d").also { it.mkdirs() }, File(td, "personas"))
        assertTrue(File(td, "personas/README.md").exists() && c.listPersonas() == listOf("Assistant.txt", "Claudia.txt"))
        assertEquals("小夏.txt", c.importPersona("小夏.txt", "你是小夏, 大学生.".byteInputStream()))
        assertEquals("小夏 (2).txt", c.importPersona("小夏.txt", "x".byteInputStream()))
        assertEquals("", c.importPersona("x.json", "x".byteInputStream()))
        assertEquals(listOf("Assistant.txt", "Claudia.txt", "小夏 (2).txt", "小夏.txt"), c.listPersonas())
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
        val snap = CommSnap("break", restLeftMin = 12.4, focusTotalMin = 200.0, breakStart = clock, breakTotalS = 1800.0)
        c.snapshot = { snap }

        script.add("早!")
        assertEquals("", c.send("早"))
        wait(c)
        val (system, msgs) = calls.last()
        assertTrue(system.startsWith(CommText.prefixPrompt()) && system.endsWith("上课."))
        val first = msgs[0].second
        assertTrue("2026-11-02 周一 09:00" in first)
        assertTrue("OIT 附带信息" !in first && "整点记录" !in first && "计时器" !in first && first.endsWith("早"))   // 默认只有时间, 没有任何附带的数据
        assertEquals(listOf("me", "ai"), c.visible().map { it.role })
        assertEquals(1, c.unread)

        clock = clock.plusMinutes(31)
        script.add("嗯"); c.send("在吗"); wait(c)
        assertFalse("计时器" in calls.last().second.last().second || "OIT 附带信息" in calls.last().second.last().second)

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

        // /clear: 之前的记录不再显示, 也不再发给模型; 清空前发出的请求回复到了也丢掉
        c.clear()
        script.add("晚安"); c.send("我先睡了"); wait(c)
        assertEquals(2, c.visible().size)
        assertEquals("", c.send(" /CLEAR "))
        assertTrue(c.visible().isEmpty() && c.messages.isEmpty())
        script.add("新的开始"); c.send("你好"); wait(c)
        val sent = calls.last().second
        assertTrue(sent.size == 1 && "我先睡了" !in sent[0].second)
        script.add("迟到的回复"); c.send("在吗"); c.clear(); wait(c)
        assertTrue(c.visible().isEmpty() && !c.busy)
        c.flush()
        assertTrue(Comm(dir, File(td, "personas"), { clock }) { _, _, _ -> "" }.visible().isEmpty())

        // 发数据: 聊天里一行说明, 数据附在发给模型的内容里
        c.clear()
        c.dataFn = { "【统计·联网理由】\n  学习资料: 15 分钟" }
        script.add("看到了")
        assertEquals("", c.sendData(CommSel(stats = listOf("net")), "帮我看看")); wait(c)
        assertEquals("📎 统计(月数据)·联网理由\n帮我看看", c.visible()[0].text)
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
        assertEquals("📎 日历·睡眠 / 合约词条 / 统计(月数据)·整点记录、应用",
            CommData.label(CommSel(listOf("log", "window"), listOf("sleep"), true)))
        // 今天的整点记录 / 专注计时: 排在最前面, 第一次打开默认勾上; 统计(月数据)默认不勾
        assertEquals("📎 整点记录(今天) / 专注计时(今天)", CommData.label(CommSel(today = listOf("timer", "log"))))
        val fresh = comm(File(td, "fresh").also { it.mkdirs() }, File(td, "freshp"))
        assertEquals(CommSel(today = listOf("log", "timer")), fresh.attachForm())
        assertTrue(fresh.attachForm().stats.isEmpty())
        fresh.setAttach(CommSel(stats = listOf("net"), today = listOf("log")))
        assertEquals("📎 整点记录(今天) / 统计(月数据)·联网理由", CommData.label(fresh.attach))
        assertEquals(listOf("log"), fresh.attachForm().today)
    }

    @Test
    fun separateChatsPerPersona() {
        val td = Files.createTempDirectory("oit").toFile()
        val dir = File(td, "d").also { it.mkdirs() }
        val c = comm(dir, File(td, "pp"))
        c.importPersona("夜.txt", "你是夜".byteInputStream())
        c.importPersona("小夏.txt", "你是小夏".byteInputStream())
        c.set(persona = "小夏.txt")
        script.add("小夏: 在"); c.send("你好小夏"); wait(c)
        c.set(persona = "夜.txt")
        assertTrue(c.visible().isEmpty() && c.pid == Comm.pidOf("夜.txt"))          // 换了对象: 这个对象还没有聊过
        script.add("夜: 嗯"); c.send("你好夜"); wait(c)
        assertEquals(listOf("你好夜", "夜: 嗯"), c.visible().map { it.text })
        assertTrue(calls.last().second.none { "你好小夏" in it.second })             // 发给夜的不带小夏那边的记录
        assertTrue(calls.last().first.endsWith("你是夜"))
        c.set(persona = "小夏.txt")
        assertEquals(listOf("你好小夏", "小夏: 在"), c.visible().map { it.text })     // 换回来: 各聊各的
        c.clear()
        c.set(persona = "夜.txt")
        assertEquals(2, c.visible().size)                                            // 清空只清现在这个对象
        c.flush()
        val c2 = Comm(dir, File(td, "pp"), { clock }) { _, _, _ -> "" }
        assertTrue(c2.personaName() == "夜.txt" && c2.visible().size == 2)
        c2.set(persona = "小夏.txt")
        assertTrue(c2.visible().isEmpty())
        // 旧版本: 所有对象共用一份 comm_chat.json → 归给现在选着的对象
        val leg = File(td, "legacy").also { it.mkdirs() }
        File(leg, "comm_chat.json").writeText("""{"messages":[{"id":1,"role":"me","text":"以前的话","at":"2026-11-02T08:00:00"}]}""")
        File(leg, "comm_config.json").writeText("""{"persona":"小夏.txt","seeded":true}""")
        val lc = Comm(leg, File(td, "pp"), { clock }) { _, _, _ -> "" }
        assertEquals(listOf("以前的话"), lc.visible().map { it.text })
        lc.set(persona = "夜.txt")
        assertTrue(lc.visible().isEmpty())
        // 身份背景: 用户自己填, 随 system 一起发, 同步
        c.set(persona = "小夏.txt", userProfile = "我是大二的学生, 在准备考研.")
        script.add("好"); c.send("嗨"); wait(c)
        assertTrue(calls.last().first.endsWith("以下是用户的身份背景(用户自己填写):\n我是大二的学生, 在准备考研."))
        val other = comm(File(td, "o").also { it.mkdirs() }, File(td, "po"))
        other.applyDoc(c.syncDoc())
        assertEquals("我是大二的学生, 在准备考研.", other.userProfile)
    }

    @Test
    fun infoRequests() {
        assertEquals(listOf("contract"), CommText.infoTags(" [Refuse to reply] [CONTRACT] "))
        assertEquals(listOf("contract"), CommText.infoTags("[contract]"))          // 没有 [refuse to reply] 也算查询
        assertTrue(CommText.infoTags("好的").isEmpty())
        assertTrue(CommText.infoTags("[refuse to reply][mechanics.md]").isEmpty())
        assertEquals(listOf("calendar", "stats"), CommText.infoTags("[refuse to reply][calendar][stats]"))
        assertTrue(CommText.infoTags("[refuse to reply][x]").isEmpty())
        assertTrue(CommData.MODULE_TAGS.all { "[$it]" in CommText.prefixPrompt() } && "VII.如果用户问起OIT的使用数据" in CommText.prefixPrompt())
        assertTrue("目前状态: 休息中" in CommData.timerText(CommSnap("break", restLeftMin = 12.0, focusTotalMin = 90.0)) && CommData.FOG_TEXT in CommData.timerText(null))
        assertTrue("09:00 写代码" in CommData.hourText(listOf("09:00 写代码")) && "没有开启" in CommData.hourText(emptyList(), false))
        assertTrue("已用 30 分钟 / 共 2 小时 0 分钟" in CommData.netText("offline", 1800.0, 7200.0, "Proxy"))
        assertTrue("白名单 · 网站 (2): a.com、b.com" in CommData.rulesText("黑名单模式", listOf("白名单 · 网站" to listOf("a.com", "b.com"), "黑名单 · 应用" to emptyList())))
        assertTrue(" 计时器:\n  休息时间倍率: ×1" in CommData.settingsText(listOf(Triple("计时器", "休息时间倍率", "×1"))))
        assertTrue("没有启用" in CommData.prescriptText(false, emptyList()) && "→ completed" in CommData.prescriptText(true, listOf(listOf("t", "V", "completed", "停下"))))
        val p = CommText.prefixPrompt()
        assertTrue("V.你需要了解OIT的独占功能'合约'" in p && "[refuse to reply][contract]" in p && "[refuse to reply][mechanics.md]" !in p)
        assertTrue("如果需要更细致的信息 前往https://github.com/Obliphur/Obliphur-s-Integrated-Terminal下的MECHANICS.md查看." in p)
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

    /** 每天空闲时间里能用沟通 45 分钟 (只算空闲状态下开着界面的时间); 休息不限 (沟通 24.1). */
    @Test
    fun idleDailyBudget() {
        val td = Files.createTempDirectory("oit").toFile()
        val c = comm(File(td, "d").also { it.mkdirs() }, File(td, "p"))
        var state = "idle"
        c.stateFn = { state }
        clock = LocalDateTime.of(2027, 6, 1, 20, 0)
        fun run(n: Int) { repeat(n) { clock = clock.plusSeconds(1); c.tick() } }
        c.tick()
        run(100)                                                   // 没开着界面: 不记
        assertEquals(Comm.IDLE_DAILY_SECONDS.toDouble(), c.idleLeft(), 0.0)
        c.setViewing(true)
        run(60)
        assertEquals(60.0, Comm.IDLE_DAILY_SECONDS - c.idleLeft(), 2.0)
        state = "break"; run(600)                                  // 休息: 不限, 不记
        assertEquals(60.0, Comm.IDLE_DAILY_SECONDS - c.idleLeft(), 2.0)
        state = "idle"; run(1500)
        assertFalse(c.idleOver())
        run(400)
        assertTrue("空闲时间里 30 分钟用完了", c.idleOver())
        clock = clock.plusSeconds(600); c.tick()                   // 卡住很久: 一次 tick 最多记几秒
        assertTrue(c.idleOver())
        clock = LocalDateTime.of(2027, 6, 2, 8, 0)
        assertFalse("第二天重新算", c.idleOver())
        c.applyDoc(mapOf("idle" to org.json.JSONObject().put("d", "2027-06-02").put("s", 1000.0)))     // 另一台设备也用了: 取大的
        assertEquals(1000.0, Comm.IDLE_DAILY_SECONDS - c.idleLeft(), 1.0)
        c.applyDoc(mapOf("idle" to org.json.JSONObject().put("d", "2027-06-02").put("s", 500.0)))
        assertEquals(1000.0, Comm.IDLE_DAILY_SECONDS - c.idleLeft(), 1.0)
        c.applyDoc(mapOf("idle" to org.json.JSONObject().put("d", "2027-06-01").put("s", 99999.0)))    // 昨天的: 不算
        assertEquals(1000.0, Comm.IDLE_DAILY_SECONDS - c.idleLeft(), 1.0)
    }

    /** 对方查询得到的是参考模板: 每个模块都有, 带占位符, 没有用户的真实数据 (沟通 24.2.1). */
    @Test
    fun queryTemplates() {
        for (tag in CommData.MODULE_TAGS) {
            val t = CommData.template(tag, listOf("基本设置" to "语言"))
            assertTrue(tag, t.isNotEmpty())
            if (tag != "reset") assertTrue(tag, CommData.NOTE in t && "<" in t)
        }
        val s = CommData.template("settings", listOf("基本设置" to "语言"))
        assertTrue("<值>" in s && "基本设置:" in s)
        val lib = CommData.template("contract")
        assertTrue("【已选择】" !in lib && "【已激活】" !in lib && "【已失效】" !in lib && "持久战" in lib && "【词条组合】" in lib)
        assertEquals("", CommData.template("nope"))
    }

    @Test
    fun onlyRestOrIdle() {
        val td = Files.createTempDirectory("oit").toFile()
        val c = comm(File(td, "d").also { it.mkdirs() }, File(td, "p"))
        c.set(persona = "Claudia.txt")
        script.add("在专注前收到")
        assertEquals("", c.send("休息时发的"))
        c.blocked = { "专注中不能使用沟通" }
        assertEquals("专注中不能使用沟通", c.send("专注时发"))
        assertEquals("专注中不能使用沟通", c.sendData(CommSel(stats = listOf("net"))))
        wait(c)
        assertEquals("休息时发的", c.visible().last().text)                   // 进入专注前发出的请求, 回复到了收下, 但藏起来
        assertTrue(c.messages.any { it.text == "在专注前收到" && it.hold })
        val unread0 = c.unread
        val n = calls.size
        c.blocked = { "" }
        c.tick()                                                              // 回到休息 / 空闲: 藏着的显示, 未读 +1
        assertTrue(c.visible().any { it.text == "在专注前收到" })
        assertEquals(unread0 + 1, c.unread)
        script.add("空下来了")
        c.send("再说一句"); wait(c)
        assertEquals(n + 1, calls.size)
        c.blocked = { "专注中不能使用沟通" }
        c.tick()
        assertFalse(c.busy)                                                    // 专注中不会主动发 / 重试
    }

    /** 用户睡眠: 对方仍按日程主动发, 但消息先藏起来、不通知; 醒来才显示 (+ 提示音). 用户专注: 完全不尝试. */
    @Test
    fun sleepAllowsOutgoingButHolds() {
        val td = Files.createTempDirectory("oit").toFile()
        val c = comm(File(td, "d").also { it.mkdirs() }, File(td, "p"))
        c.importPersona("夜猫.txt", "你是夜猫.\n12:30-13:00:空闲\n空闲发起谈话概率:100%\n".byteInputStream())
        c.set(persona = "夜猫.txt")
        c.stateFn = { "sleep" }
        c.blocked = { "睡眠中不能使用沟通" }
        c.aiBlocked = { "" }
        c.snapshot = { CommSnap("sleep") }
        val pings = mutableListOf<Int>()
        c.onProactive = { pings.add(1) }
        script.add("睡着了也要说")
        val begin = LocalDateTime.of(2028, 2, 1, 12, 30)
        assertNotNull("睡眠时对方仍会发", fireBefore(c, begin))
        wait(c)
        assertTrue(c.visible().isEmpty() && c.messages.any { it.hold })          // 睡眠时收到: 先藏起来
        assertEquals(0, c.unread); assertEquals(0, pings.size)                  // 睡眠时不通知
        c.stateFn = { "idle" }; c.blocked = { "" }
        c.tick()
        assertEquals(listOf("睡着了也要说"), c.visible().map { it.text })         // 醒来才显示
        assertEquals(1, c.unread); assertEquals(listOf(1), pings)
        c.stateFn = { "focus" }
        c.blocked = { "专注中不能使用沟通" }
        c.aiBlocked = { "专注中不能使用沟通" }
        val n = calls.size
        assertNull("专注时不会尝试发消息", fireBefore(c, LocalDateTime.of(2028, 2, 2, 12, 30)))
        assertEquals(n, calls.size)
    }

    /** 聊天页顶上的对方状态: 按人设日程, 跨零点也认; 人设 README 跟随界面语言. */
    @Test
    fun otherStatusAndReadmeLanguage() {
        val td = Files.createTempDirectory("oit").toFile()
        val c = comm(File(td, "d").also { it.mkdirs() }, File(td, "p"))
        c.set(persona = "Claudia.txt")
        clock = LocalDateTime.of(2026, 11, 2, 8, 0)
        assertEquals("专注中", c.otherStatusText())
        clock = LocalDateTime.of(2026, 11, 2, 9, 0)
        assertEquals("休息中", c.otherStatusText())
        clock = LocalDateTime.of(2026, 11, 2, 12, 40)
        assertEquals("空闲", c.otherStatusText())
        clock = LocalDateTime.of(2026, 11, 2, 12, 15)
        assertEquals("空闲", c.otherStatusText())                      // 午饭等非四类: 算空闲
        assertEquals("idle", c.otherStatusDisplay().second)
        clock = LocalDateTime.of(2026, 11, 2, 6, 45)
        assertEquals("空闲", c.otherStatusText())                      // 不在四类段里: 也算空闲
        clock = LocalDateTime.of(2026, 11, 2, 23, 59)
        assertEquals("睡眠中", c.otherStatusText())                     // 不显示结束时间
        assertEquals("sleep", c.otherStatusDisplay().second)
        val old = I18n.lang
        try {
            I18n.lang = "en"
            assertTrue(CommText.readme().startsWith("How to write a persona prompt:"))
            assertTrue("8:00-9:00:Focus" in CommText.readme())
            assertTrue("The format and the standard wording must match to be recognized and matched." in CommText.readme())
        } finally {
            I18n.lang = old
        }
        assertTrue("格式和名词与标准用语相同才能识别与匹配." in CommText.readme())
        assertTrue("8:00-9:00:专注(学习)" in CommText.readme())
        c.importPersona("带修饰.txt", "8:00-9:00:专注(学习)\n9:00-10:00:午饭(便当)\n".byteInputStream())
        c.set(persona = "带修饰.txt")
        clock = LocalDateTime.of(2026, 11, 3, 8, 30)
        assertEquals("专注中", c.otherStatusText())                    // 状态里不显示修饰
        clock = LocalDateTime.of(2026, 11, 3, 9, 30)
        assertEquals("空闲", c.otherStatusText())                      // 午饭算空闲
        c.importPersona("无日程.txt", "你是小满.\n".byteInputStream())
        c.set(persona = "无日程.txt")
        clock = LocalDateTime.of(2026, 11, 3, 15, 0)
        assertEquals("空闲", c.otherStatusText())                      // 没有日程也算空闲
    }

    /** 人设文件本身随设备同步: 新增 / 覆盖 / 删除 (README 不同步). */
    @Test
    fun personaFilesSync() {
        val td = Files.createTempDirectory("oit").toFile()
        val c = comm(File(td, "d").also { it.mkdirs() }, File(td, "p"))
        c.importPersona("甲.txt", "你是甲".byteInputStream())
        c.set(persona = "甲.txt")
        assertEquals("你是甲", c.personaDoc()["甲.txt"])
        c.applyPersonaDoc(mapOf<String, Any?>("乙.txt" to "你是乙", "甲.txt" to null))
        assertTrue("乙.txt" in c.listPersonas() && "甲.txt" !in c.listPersonas())
        assertEquals("你是乙", c.readPersona("乙.txt"))
        assertTrue("README.md" !in c.personaDoc())
    }

    /** 特例: 名字是 Assistant 且提示词没改过 → 专注时也能用. */
    @Test
    fun assistantFocusExemption() {
        val td = Files.createTempDirectory("oit").toFile()
        val c = comm(File(td, "d").also { it.mkdirs() }, File(td, "p"))
        c.set(persona = "Assistant.txt")
        assertTrue(c.isBuiltinAssistantUnmodified())
        File(td, "p/Assistant.md").writeText(BuiltinPersonas.ALL.getValue(BuiltinPersonas.ASSISTANT))
        c.set(persona = "Assistant.md")
        assertTrue(c.isBuiltinAssistantUnmodified())
        File(td, "p/Assisstant.md").writeText(BuiltinPersonas.ALL.getValue(BuiltinPersonas.ASSISTANT))
        c.set(persona = "Assisstant.md")
        assertFalse(c.isBuiltinAssistantUnmodified())          // 拼错的名字不算
        c.set(persona = "Assistant.md")
        File(td, "p/Assistant.md").appendText("\n改过的内容")
        assertFalse(c.isBuiltinAssistantUnmodified())
    }

    @Test
    fun builtinPersona() {
        val td = Files.createTempDirectory("oit").toFile()
        val dir = File(td, "d").also { it.mkdirs() }
        val c = comm(dir, File(td, "pp"))
        assertEquals(listOf("Assistant.txt", "Claudia.txt"), c.listPersonas())
        assertEquals("Claudia.txt", c.personaName())            // 第一次运行默认选中
        assertTrue(c.readPersona("Claudia.txt").startsWith("你将扮演一位文静,冷淡的"))
        assertTrue(c.readPersona("Assistant.txt").startsWith("你是名为 Assistant 的助理"))
        c.set(persona = "")
        File(td, "pp/Claudia.txt").delete()
        File(td, "pp/Assistant.txt").delete()
        c.flush()
        val c2 = Comm(dir, File(td, "pp"), { clock }) { _, _, _ -> "" }
        assertTrue(c2.listPersonas().isEmpty() && c2.personaName().isEmpty())     // 删掉之后不再写回, 也不再自动选
        // 老版本升级上来: 已经 seeded 过, 也会补一次 Assistant (没有同名文件时)
        val od = File(td, "od").also { it.mkdirs() }
        val op = File(td, "op").also { it.mkdirs() }
        File(op, "小夏.txt").writeText("你是小夏.")
        File(od, "comm_config.json").writeText("""{"seeded":true,"persona":"小夏.txt"}""")
        val co = Comm(od, op, { clock }) { _, _, _ -> "" }
        assertTrue("Assistant.txt" in co.listPersonas() && "Claudia.txt" !in co.listPersonas())
    }

    @Test
    fun deviceSync() {
        val td = Files.createTempDirectory("oit").toFile()
        val a = comm(File(td, "a").also { it.mkdirs() }, File(td, "pa"))
        val b = comm(File(td, "b").also { it.mkdirs() }, File(td, "pb"))
        a.device = { "aaaa" }; b.device = { "bbbb" }
        for (c in listOf(a, b)) {
            c.importPersona("小夏.txt", "你是小夏\n长时间休息发起谈话概率:100%\n".byteInputStream())
            c.set(persona = "小夏.txt")
        }
        fun pull(src: Comm, dst: Comm) = dst.applyDoc(src.syncDoc())

        script.add("A 的回复")
        assertEquals("", a.send("你好")); wait(a)
        pull(a, b)
        assertEquals(listOf("你好", "A 的回复"), b.visible().map { it.text })
        // 请求日志 / 选模型 (聊天和 AI 名单建议用同一份模型设置)
        val logText = File(td, "a/comm.log").readText()
        assertTrue("REQ user start" in logText && "REQ user ok" in logText)
        a.setModel("m-x")
        assertEquals("m-x", a.modelProfile().model)
        assertTrue("m-x" in com.obliphur.oit.ai.Ai.modelsFor(a.modelProfile()))
        assertEquals(1, b.unread)
        pull(a, b)
        assertEquals(2, b.visible().size)               // 同一条不会并两次
        assertEquals("aaaa", b.lastAuthor())
        a.set(showAvatar = true)                         // 设置页的开关也同步
        pull(a, b)
        assertTrue(b.showAvatar)
        a.set(showAvatar = false)
        pull(a, b)
        assertFalse(b.showAvatar)

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

        // 主动消息: 同一次休息只有一台发一次 (标记同步), 同步过来的这台也提示一声
        val start = clock
        val bsnap = CommSnap("break", restLeftMin = 25.0, focusTotalMin = 5.0, breakStart = start, breakTotalS = 30 * 60.0)
        a.snapshot = { bsnap }; b.snapshot = { bsnap }
        a.leader = { true }; b.leader = { false }
        val pa = mutableListOf<Int>(); val pb = mutableListOf<Int>()
        a.onProactive = { pa.add(1) }; b.onProactive = { pb.add(1) }
        script.add("休息啦")
        clock = start.plusMinutes(3)
        a.tick(); assertTrue(a.busy); wait(a)
        assertEquals(listOf(1), pa)
        pull(a, b)
        assertEquals(listOf(1), pb)                      // 同步过来的主动消息: 这台也响一声
        pull(a, b)
        assertEquals(listOf(1), pb)
        b.leader = { true }
        b.tick(); assertFalse(b.busy)
        assertEquals(listOf("休息啦"), b.visible().map { it.text })
    }

    @Test
    fun proactive() {
        val td = Files.createTempDirectory("oit").toFile()
        val c = comm(File(td, "d").also { it.mkdirs() }, File(td, "personas"))
        c.importPersona("长休.txt", "你是小夏\n长时间休息发起谈话概率:100%\n".byteInputStream())
        c.set(persona = "长休.txt")
        val pings = mutableListOf<Int>()
        c.onProactive = { pings.add(1) }
        val start = clock
        var snap = CommSnap("break", restLeftMin = 25.0, focusTotalMin = 60.0, breakStart = start, breakTotalS = 25 * 60.0)
        c.snapshot = { snap }
        c.tick(); assertFalse(c.busy)
        clock = start.plusSeconds(59)
        c.tick(); assertFalse("休息开始后 1 分钟之内不发", c.busy)
        script.add("休息啦, 在干嘛?")
        val sFire = fireAfter(c, start)
        assertTrue("开始后 1~3 分钟之内: $sFire", sFire != null && sFire in 60..182)
        wait(c)
        assertEquals(listOf("ai"), c.visible().map { it.role })
        assertTrue("主动给用户发一条消息" in calls.last().second.last().second)
        assertEquals("前 5 分钟没聊过: 响一声", listOf(1), pings)
        c.tick(); assertFalse("同一次休息只主动发一次", c.busy)
        // 休息不到 20 分钟不发
        val s2 = clock.plusMinutes(1)
        snap = CommSnap("break", breakStart = s2, breakTotalS = 15 * 60.0)
        clock = s2.plusMinutes(6)
        c.tick(); assertFalse(c.busy)
        // 刚主动发过一条 (10 分钟内): 又一次长休息开始, 也不连着发
        val s3 = clock
        snap = CommSnap("break", breakStart = s3, breakTotalS = 30 * 60.0)
        assertNull("10 分钟内不连发", fireAfter(c, s3, 60, 178))
        // 主动消息被拒绝: 什么也不留下
        clock = clock.plusMinutes(15)
        val s4 = clock
        snap = CommSnap("break", breakStart = s4, breakTotalS = 30 * 60.0)
        script.add(CommText.REFUSE)
        assertNotNull(fireAfter(c, s4))
        wait(c)
        assertEquals(1, c.visible().size)
        assertEquals(1, c.messages.count { it.role == "sys" })
        assertEquals(listOf(1), pings)
        // 长时间休息发起谈话概率: 人设里写的; 0% 不发 (这个时间点算处理过了); 没写默认 70%
        assertEquals(mapOf("long" to 70.0, "rest" to 30.0, "idle" to 20.0), CommText.personaProbs("长时间休息发起谈话概率:0.7\n休息发起谈话概率:30\n空闲发起谈话概率:20%"))
        assertEquals(100.0, c.proPercent("long"), 0.0)
        val pf = File(td, "personas/长休.txt")
        pf.writeText("你是小夏\n长时间休息发起谈话概率:0%\n"); c.reloadSchedule()
        clock = clock.plusMinutes(15)
        val s5 = clock
        snap = CommSnap("break", breakStart = s5, breakTotalS = 30 * 60.0)
        assertEquals(0.0, c.proPercent("long"), 0.0)
        assertNull(fireAfter(c, s5))
        pf.writeText("你是小夏\n"); c.reloadSchedule()
        assertEquals(70.0, c.proPercent("long"), 0.0)
    }

    /** 规则 VIII: 用户说完话, 对方的第一条收到之后再调用一次 API, 可以把话分开说. */
    @Test
    fun splitReply() {
        val p = CommText.prefixPrompt()
        assertTrue("VIII.你可以把你的话分开说.当用户说完话时,系统会试图调用两次API,最好不要让你的表达太集中.如果你只想说一次,第二次[refuse to reply]." in p)
        assertTrue(p.indexOf("VII.如果用户问起") < p.indexOf("VIII.") && p.indexOf("VIII.") < p.indexOf("以下是用户设定的人设:"))
        val td = Files.createTempDirectory("oit").toFile()
        val c = comm(File(td, "d").also { it.mkdirs() }, File(td, "p"))
        c.importPersona("小夏.txt", "你是小夏".byteInputStream())
        c.set(persona = "小夏.txt")
        c.snapshot = { CommSnap("idle") }
        script.add("第一句"); follow.add("第二句")
        assertEquals("", c.send("在吗")); wait(c)
        assertEquals(listOf("在吗", "第一句", "第二句"), c.visible().map { it.text })
        assertEquals(1, followCalls.size)
        assertTrue(Comm.FOLLOW_TAG in followCalls.last().second.last().second)
        assertEquals("第一句", followCalls.last().second[followCalls.last().second.size - 2].second)   // 第二次请求里带着第一条
        val n = followCalls.size
        // 只想说一次: 第二次 [refuse to reply], 什么也不留下
        script.add("只有一句")
        c.send("好的"); wait(c)
        assertEquals("只有一句", c.visible().last().text)
        assertEquals(n + 1, followCalls.size)
        assertEquals(1, c.messages.count { it.role == "sys" })
        // 第一次就被拒绝 (对方不方便): 不调用第二次
        script.add(CommText.REFUSE)
        c.send("在不在"); wait(c)
        assertEquals(n + 1, followCalls.size)
        assertEquals("me", c.visible().last().role)
        // 第二次调用还在等的时候用户又说话了: 第二次的回复不要了, 按新消息重新回复
        c.clear()
        script.add("甲"); script.add("回应等等"); follow.add("乙(不该出现)")
        c.send("你好")
        for (i in 0 until 300) {
            Thread.sleep(10); c.tick()
            if (c.busy && c.messages.any { it.role == "ai" }) break
        }
        c.send("等等"); wait(c)
        assertEquals(listOf("你好", "甲", "等等", "回应等等"), c.visible().map { it.text })
        // 分条发的开关: 关着时不调用第二次, 前置 prompt 里也没有第 VIII 条; 开关随设备同步
        c.set(splitSend = false)
        assertFalse("VIII." in CommText.prefixPrompt(false))
        assertTrue("VIII." in CommText.prefixPrompt(true))
        val nf = followCalls.size
        script.add("单条"); follow.add("不该出现")
        c.send("关了吗"); wait(c)
        assertEquals("单条", c.visible().last().text)
        assertEquals(nf, followCalls.size)
        assertEquals(false, c.syncDoc()["split"])
        follow.clear()
        c.set(splitSend = true)
        assertEquals(true, c.syncDoc()["split"])
        // IX: [waiting for reply]:[N] — 所有 [] 都不显示; N 分钟内用户没回复, 对方可以追问一次
        val pr = CommText.prefixPrompt()
        assertTrue("IX.如果期待对方回复,则在对话结尾加一个[waiting for reply]:[N],N自己填写,如果N min内没有收到对方回复你可以追加询问." in pr)
        assertTrue(pr.indexOf("VIII.") < pr.indexOf("IX.") && pr.indexOf("IX.") < pr.indexOf("以下是用户设定的人设:"))
        assertEquals("在吗?" to 10.0, CommText.cleanReply("在吗?[waiting for reply]:[10]"))
        assertEquals("好的 嗯\n\n好" to 0.0, CommText.cleanReply("好的[微笑] 嗯\n\n\n\n[动作]好"))
        assertEquals("" to 0.5, CommText.cleanReply("[Waiting For Reply]：[ 0.5 ]"))
        assertEquals("x" to 0.0, CommText.cleanReply("x [waiting for reply]:[0]"))
        assertEquals("ab" to 0.0, CommText.cleanReply("a［全角］b"))
        for (tail in listOf("[waiting for reply]:1", "[waiting for reply]：[1", "[waiting for reply]:[1]", "[waiting for reply][1]", "[waiting for reply]: 1 min",
            "[waiting for reply]:1分钟", "[Waiting for reply] : [ 1 ]"))
            assertEquals(tail, "你在吗?" to 1.0, CommText.cleanReply("你在吗?\n\n" + tail))             // N 不带 [] / 括号没写全 / 带单位: 照样认, 不留 : 和数字
        assertEquals("好" to 0.0, CommText.cleanReply("好[]:1"))
        assertEquals("好 呢" to 0.0, CommText.cleanReply("好[]：2.5 呢"))
        assertEquals("好" to 0.0, CommText.cleanReply("[动作] : 3 好"))
        assertEquals("好" to 0.0, CommText.cleanReply("好[waiting for reply]:"))
        assertEquals("1 3 个" to 0.0, CommText.cleanReply("1[waiting for reply] 3 个"))
        assertEquals("要在结尾带上 ,才会触发" to 1.0, CommText.cleanReply("要在结尾带上 `[waiting for reply]:[1]`,才会触发"))
        assertEquals("好" to 0.0, CommText.cleanReply("好(`[x]`)“[y]”「[z]」"))
        script.add("今天过得怎么样?[waiting for reply]:[10]")
        c.send("嗨"); wait(c)
        val lastAi = c.messages.last { it.role == "ai" }
        assertEquals("今天过得怎么样?", lastAi.text)
        assertEquals(10.0, lastAi.wait, 0.0)
        assertTrue("[waiting for reply]" in lastAi.api)
        assertEquals(10.0, (c.syncDoc()["m:${c.pid}:${lastAi.uid}"] as org.json.JSONObject).getDouble("wait"), 0.0)
        val nc = calls.size
        clock = clock.plusMinutes(9)
        c.tick(); assertFalse(c.busy); assertEquals(nc, calls.size)          // 没到 N 分钟不追问
        script.add("还在忙吗?[waiting for reply]:[5]")
        clock = clock.plusMinutes(2)
        c.tick(); assertTrue(c.busy)                                           // N 分钟用户没回复: 追问
        wait(c)
        val body = calls.last().second.last().second
        assertTrue("[waiting for reply]:[10]" in body && "追加询问" in body)
        assertEquals("还在忙吗?", c.visible().last().text)
        assertEquals(0.0, c.messages.last { it.role == "ai" }.wait, 0.0)       // 追问的回复不再等待
        val nc2 = calls.size
        clock = clock.plusMinutes(30)
        c.tick(); assertFalse(c.busy); assertEquals(nc2, calls.size)          // 一条消息最多追问一次
    }

    /** 提示缓存: 发给模型的历史的起点一格一格挪 (中间每次请求开头一模一样), 各家的用量字段都认得. */
    @Test
    fun cacheFriendlyHistory() {
        val td = Files.createTempDirectory("oit").toFile()
        val c = comm(File(td, "d").also { it.mkdirs() }, File(td, "p"))
        c.set(persona = "Claudia.txt")
        val firsts = HashSet<String>()
        for (i in 0 until 140) {
            script.add("a$i")
            c.send("m$i"); wait(c)
            if (i > 40 && i % 2 == 0) firsts.add(c.apiMessages().first().second)
        }
        assertTrue(c.apiMessages().size <= Comm.HISTORY_SENT)
        assertTrue("开头变得太勤: ${firsts.size}", firsts.size <= (140 * 2 - 80) / Comm.HISTORY_STEP + 2)
        assertEquals(105L to 90L, Ai.usageOf(JSONObject("""{"usage":{"input_tokens":10,"cache_read_input_tokens":90,"cache_creation_input_tokens":5}}""")))
        assertEquals(200L to 150L, Ai.usageOf(JSONObject("""{"usage":{"prompt_tokens":200,"prompt_cache_hit_tokens":150}}""")))
        assertEquals(200L to 128L, Ai.usageOf(JSONObject("""{"usage":{"prompt_tokens":200,"prompt_tokens_details":{"cached_tokens":128}}}""")))
        assertEquals(0L to 0L, Ai.usageOf(JSONObject("{}")))
    }

    /** 非正在聊天的对象也会主动发消息 (开关 bgPro, 默认开); 每个对象可以禁止 (setMuted). */
    @Test
    fun backgroundPersonas() {
        val td = Files.createTempDirectory("oit").toFile()
        val pdir = File(td, "p").also { it.mkdirs() }
        val c = comm(File(td, "d").also { it.mkdirs() }, pdir)
        c.importPersona("甲.txt", "你是甲.\n12:30-13:00:空闲\n空闲发起谈话概率:100%\n".byteInputStream())
        c.importPersona("乙.txt", "你是乙.\n".byteInputStream())
        c.snapshot = { CommSnap("idle") }
        assertTrue(c.bgPro)
        for (who in listOf("甲.txt", "乙.txt")) {                    // 两个人都聊过
            c.set(persona = who)
            script.add("你好")
            c.send("hi"); wait(c)
        }
        assertEquals("乙.txt", c.personaName())
        fun bgFire(begin: LocalDateTime): Int? {
            for (s in -200 until 0 step 2) { clock = begin.plusSeconds(s.toLong()); c.tick(); if (!c.bgIdle()) return s }
            return null
        }
        fun bgWait() { for (i in 0 until 300) { Thread.sleep(10); c.tick(); if (c.bgIdle()) return }; throw AssertionError("超时") }
        var pings = 0
        c.onProactive = { pings++ }
        c.set(bgPro = false)                                          // 开关关着: 不发
        assertNull(bgFire(LocalDateTime.of(2027, 5, 1, 12, 30)))
        c.set(bgPro = true)
        val pidJia = Comm.pidOf("甲.txt")
        c.setMuted(true, pidJia)                                      // 禁止了这个人: 不发
        assertTrue(c.isMuted(pidJia)); assertFalse(c.isMuted())
        assertNull(bgFire(LocalDateTime.of(2027, 5, 2, 12, 30)))
        assertEquals(true, c.syncDoc()["mute:$pidJia"])
        c.setMuted(false, pidJia)
        script.add("甲来找你")
        val s = bgFire(LocalDateTime.of(2027, 5, 3, 12, 30))
        assertTrue("开始前 1~3 分钟: $s", s != null && s in -182..-60)
        val unread0 = c.unread
        bgWait()
        assertEquals(unread0 + 1, c.unread)
        assertEquals(1, pings)
        assertTrue("现在聊的是乙: 乙的记录里没有", c.visible().none { it.text == "甲来找你" })
        c.set(persona = "甲.txt")                                     // 切到甲: 能看到
        assertEquals("甲来找你", c.visible().last().text)
    }

    @Test
    fun scheduleProactive() {
        // 人设日程里的空闲 / 休息 / 专注 / 睡眠段
        assertEquals(listOf("focus" to 420, "rest" to 520, "focus" to 558, "rest" to 658, "focus" to 696, "idle" to 750,
            "focus" to 780, "rest" to 880, "focus" to 918, "rest" to 1018, "focus" to 1056, "idle" to 1110,
            "focus" to 1140, "rest" to 1240, "focus" to 1278, "rest" to 1378, "idle" to 1380, "sleep" to 1410),
            CommText.scheduleSegments(BuiltinPersonas.ALL.getValue("Claudia.txt")))
        assertEquals(listOf("idle" to 545, "rest" to 550), CommText.scheduleSegments(
            "- 9:05 ~ 9:35 : Idle time\n  09：10-10:00：休息\n24:00-25:00:空闲\n12:00-13:00:不休息\n" +
                "12:00-13:00:午饭\n每天 8 点到 12 点上课.\n9:05-9:35:idle again\n"))
        // 专注 / 睡眠的识别, 英文写法 (不分大小写), 以及标准用语后面的修饰
        assertEquals("focus", CommText.segKind("专注(学习)"))
        assertEquals("focus", CommText.segKind("FOCUS"))
        assertEquals("focus", CommText.segKind("Focus(study)"))
        assertEquals("focus", CommText.segKind("专注(休息)"))            // 先看标准用语, 不看修饰里的字
        assertEquals("rest", CommText.segKind("休息(专注)"))
        assertEquals("sleep", CommText.segKind("sleeping"))
        assertEquals("", CommText.segKind("午饭"))
        assertEquals("", CommText.segKind("不专注"))
        assertEquals(6 * 60 + 30 + 24 * 60, CommText.scheduleEntries("23:30-6:30:睡眠").first().end)
        assertEquals("学习", CommText.scheduleEntries("8:00-9:00:专注(学习)").first().detail)
        assertEquals(listOf("focus" to 480, "focus" to 540, "focus" to 600, "sleep" to 1410), CommText.scheduleSegments(
            "8:00-9:00:Focus\n9:00-10:00:focus(study)\n10:00-11:00:FOCUS\n23:30-6:30:SLEEP\n23:30-6:30:Sleep"))
        // 循环段: [循环 9:00-12:00][Nmin] 自动生成 专注 N + 休息 F(N); 不写 N 默认 100min
        fun cyc(s: String) = CommText.scheduleEntries(s).map { Triple(it.kind, it.start, it.end) }
        assertEquals(listOf(Triple("focus", 540, 640), Triple("rest", 640, 678), Triple("focus", 678, 720)),
            cyc("[循环 9:00-12:00]\n"))
        assertEquals(cyc("[循环 9:00-12:00][100min]\n"), cyc("[Loop 9:00-12:00][100min]\n"))
        assertEquals(cyc("[循环 9:00-12:00][100min]\n"), cyc("[CYCLE 9:00-12:00]\n"))
        assertEquals(listOf(Triple("focus", 540, 590), Triple("rest", 590, 606), Triple("focus", 606, 656),
            Triple("rest", 656, 672), Triple("focus", 672, 720)), cyc("[循环 9:00-12:00][50min]\n"))
        assertEquals(listOf(Triple("focus", 540, 600)), cyc("[循环 9:00-12:00]\n9:00-10:00:专注\n"))   // 显式优先
        assertEquals(Triple("focus", 1380, 1480), cyc("[循环 23:00-1:00]\n").first())                 // 跨零点
        assertEquals(listOf("focus" to 540, "rest" to 640, "focus" to 678), CommText.scheduleSegments("[循环 9:00-12:00]\n"))

        val td = Files.createTempDirectory("oit").toFile()
        val c = comm(File(td, "d").also { it.mkdirs() }, File(td, "p"))
        c.importPersona("日程.txt", "你是小满.\n12:30-13:00:空闲\n20:40-21:18:休息\n".byteInputStream())
        c.set(persona = "日程.txt")
        assertEquals(80.0 to 60.0, c.proPercent("idle") to c.proPercent("rest"))          // 人设里没写: 默认概率
        assertEquals(mapOf("idle" to 20.0, "rest" to 20.0, "long" to 20.0), CommText.personaProbs(BuiltinPersonas.ALL.getValue("Claudia.txt")))   // Claudia: 20% / 20% / 20%
        assertEquals(mapOf("idle" to 12.5, "rest" to 40.0), CommText.personaProbs("空闲发起谈话概率:12.5%\n休息发起谈话概率 ： 0.4\n休息发起谈话概率:90%"))
        assertEquals(mapOf("idle" to 100.0, "rest" to 30.0), CommText.personaProbs("空闲发起谈话概率:1\n休息发起谈话概率:30"))
        assertEquals(mapOf("idle" to 100.0), CommText.personaProbs("空闲发起谈话概率:150%\n休息发起谈话概率:x"))
        val p5 = File(td, "p/日程.txt")
        fun setProbs(idle: String, rest: String) {                // 改人设文件里的概率行 (别的内容不动)
            p5.writeText(p5.readLines().filter { "发起谈话概率" !in it }.joinToString("\n") + "\n空闲发起谈话概率:$idle\n休息发起谈话概率:$rest\n")
            c.reloadSchedule()
        }
        setProbs("150%", "x")
        assertEquals(100.0 to 60.0, c.proPercent("idle") to c.proPercent("rest"))
        c.snapshot = { CommSnap("idle") }
        val pings = mutableListOf<Int>()
        c.onProactive = { pings.add(1) }
        script.add("来了")
        val s1 = fireBefore(c, LocalDateTime.of(2026, 12, 1, 12, 30))
        assertTrue("开始前 1~3 分钟: $s1", s1 != null && s1 in -182..-60)
        wait(c)
        val body = calls.last().second.last().second
        assertEquals("来了", c.visible().last().text)
        assertTrue("空闲时间将在 12:30 开始" in body && "主动给用户发一条消息" in body)
        assertEquals(1, pings.size)
        // 前 5 分钟聊过 → 不响
        clock = LocalDateTime.of(2026, 12, 2, 12, 25)
        script.add("嗯")
        assertEquals("", c.send("在吗")); wait(c)
        script.add("有空吗")
        assertNotNull(fireBefore(c, LocalDateTime.of(2026, 12, 2, 12, 30)))
        wait(c)
        assertEquals("有空吗", c.visible().last().text)
        assertEquals("刚聊过, 不响", 1, pings.size)
        // 概率 0: 不发, 但这个时间点算处理过了
        setProbs("0%", "60%")
        assertNull(fireBefore(c, LocalDateTime.of(2026, 12, 3, 12, 30)))
        // 休息段默认 60%: 300 天里大约 60% 发
        setProbs("0%", "0.6")
        repeat(300) { script.add("嗯") }
        val before = calls.size
        for (d in 0 until 300) {
            clock = LocalDateTime.of(2027, 1, 1, 20, 39, 0).plusDays(d.toLong())
            c.tick()
            if (c.busy) wait(c)
        }
        val hits = calls.size - before
        assertTrue("命中 $hits", hits in 150..210)
        assertTrue("休息时间将在 20:40 开始" in calls.last().second.last().second)
        // 错过太久 (程序刚打开) 不补发
        setProbs("100%", "0.6")
        var b = LocalDateTime.of(2027, 3, 2, 12, 30)
        while (60 + java.util.Random("S${b.toLocalDate()}T12:30i".hashCode().toLong()).nextInt(121) <= 150) b = b.plusDays(1)
        clock = b.minusSeconds(10)
        val n0 = calls.size
        c.tick(); assertFalse(c.busy); c.tick(); assertFalse(c.busy)
        assertEquals(n0, calls.size)
        // 相邻的日程段 (22:58 休息, 23:00 空闲) 不连着发
        val c2 = comm(File(td, "d2").also { it.mkdirs() }, File(td, "p2"))
        assertEquals("Claudia.txt", c2.personaName())
        File(td, "p2/全发.txt").writeText(BuiltinPersonas.ALL.getValue("Claudia.txt")
            .replace("空闲发起谈话概率:20%", "空闲发起谈话概率:100%").replace("休息发起谈话概率:20%", "休息发起谈话概率:100%"))
        c2.set(persona = "全发.txt")
        c2.snapshot = { CommSnap("idle") }
        script.add("晚点见")
        assertNotNull(fireBefore(c2, LocalDateTime.of(2027, 4, 1, 22, 58)))
        wait(c2)
        assertTrue("休息时间将在 22:58 开始" in calls.last().second.last().second)
        assertNull("刚发过, 23:00 的空闲段不再发", fireBefore(c2, LocalDateTime.of(2027, 4, 1, 23, 0)))

        // README 里的标准写法: 例子能被认出来; 打开 OIT / 换人设 / 新对话都会重新读人设日程
        assertTrue("日程的标准写法" in CommText.readme())
        assertEquals(listOf("focus" to 420, "rest" to 520, "idle" to 750, "sleep" to 1410), CommText.scheduleSegments(CommText.readme()))
        val log = { File(td, "d/comm.log").readText() }
        assertTrue("SCHEDULE 启动" in log() && "SCHEDULE 换人设: 空闲 1 段, 休息 1 段, 专注 0 段, 睡眠 0 段" in log())
        File(td, "p/日程.txt").appendText("14:00-14:30:空闲\n")
        c.clear()
        assertTrue("新对话: 人设改过的日程马上生效", "SCHEDULE 新对话: 空闲 2 段, 休息 1 段, 专注 0 段, 睡眠 0 段" in log())
    }
}
