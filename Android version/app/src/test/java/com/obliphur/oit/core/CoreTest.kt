package com.obliphur.oit.core

import com.obliphur.oit.service.Dns
import com.obliphur.oit.service.Packets
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.json.JSONObject
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.LocalDate
import java.time.LocalDateTime

class CoreTest {
    private fun tmp(): File = Files.createTempDirectory("oit").toFile()

    @Test
    fun breakFormula() {
        val table = mapOf(10.0 to 1.7, 15.0 to 2.5, 30.0 to 6.9, 45.0 to 11.6, 60.0 to 16.4, 90.0 to 26.8,
            120.0 to 38.0, 150.0 to 50.0, 180.0 to 55.0, 240.0 to 62.9)
        for ((t, f) in table) assertEquals("F($t)", f, Math.round(computeF(t) * 10) / 10.0, 1e-9)
        assertEquals("01:05", fmtMs(64.2))
        assertEquals("2h 05m", fmtHm(7500.0))
        assertEquals("0.4", numText(0.4))
        assertEquals("2", numText(2.0))
    }

    @Test
    fun settingsCooldown() {
        val f = File(tmp(), "s.json")
        val m = Settings(f)
        assertTrue(m.fresh)
        assertEquals(7200.0, m.num("DAILY_LIMIT"), 0.0)
        // 第一次改也要等
        assertEquals(StageResult.STAGED, m.stage("DAILY_LIMIT", 3600.0).first)
        assertEquals(7200.0, m.num("DAILY_LIMIT"), 0.0)
        assertTrue(m.pendingLeftSeconds("DAILY_LIMIT") in 47 * 3600..48 * 3600)
        // 改回当前值 = 取消
        assertEquals(StageResult.STAGED, m.stage("DAILY_LIMIT", 7200.0).first)
        assertTrue(m.pending.isEmpty())
        assertEquals(StageResult.NOCHANGE, m.stage("DAILY_LIMIT", 7200.0).first)
        // 即时项
        assertEquals(StageResult.INSTANT, m.stage("THEME", "claude").first)
        // 超出范围
        assertEquals(StageResult.INVALID, m.stage("COOLDOWN", 999999.0).first)
        assertEquals(StageResult.INVALID, m.stage("COOLDOWN", "abc").first)
        // 配置期: 立即生效, 排队中的也立即生效
        m.stage("COOLDOWN", 120.0)
        m.startSetup()
        assertTrue(m.inSetup())
        assertEquals(listOf("COOLDOWN"), m.promoteDue())
        assertEquals(120.0, m.num("COOLDOWN"), 0.0)
        val m2 = Settings(f)
        assertFalse(m2.fresh)
        assertTrue(m2.inSetup())
        m2.endSetup()
        assertEquals(StageResult.STAGED, m2.stage("FILTER_MODE", "whitelist").first)
        assertEquals("blacklist", m2.str("FILTER_MODE"))
        // 借贷过多后的一次免等待
        assertTrue(m2.grantFreeChange("DAILY_LIMIT"))
        assertFalse(m2.grantFreeChange("DAILY_LIMIT"))
        assertEquals(StageResult.INSTANT, m2.stage("DAILY_LIMIT", 3600.0).first)
        assertEquals(StageResult.STAGED, m2.stage("DAILY_LIMIT", 1800.0).first)
        // 选项 "1" / 1 / 1.0 是同一个值
        assertEquals("1", m2.coerce("HOUR_LOG_ENABLED", 1))
        assertEquals("1.0", m2.coerce("FONT_SCALE", 1))
        // 文件里被改坏的数值退回默认值
        m2.effective["TICK_CLAMP"] = 99999.0
        assertEquals(30.0, m2.num("TICK_CLAMP"), 0.0)
    }

    @Test
    fun ruleNormalize() {
        assertEquals("www.bilibili.com", normDomain("https://www.Bilibili.com:443/video?x=1"))
        assertEquals("example.com", normDomain("*.example.com"))
        assertEquals("github.com", normDomain("user:pw@github.com/a"))
        assertEquals("", normDomain("localnamenodot"))
        assertEquals("", normDomain("a b.com"))
        assertEquals("tv.danmaku.bili", normApp("https://play.google.com/store/apps/details?id=tv.danmaku.bili&hl=zh"))
        assertEquals("com.a.b", normApp("package:com.a.b"))
        assertEquals("", normApp("chrome"))
        assertTrue(domainMatch("a.b.com", "b.com"))
        assertFalse(domainMatch("ab.com", "b.com"))
    }

    @Test
    fun ruleStore() {
        var clock = LocalDateTime.of(2026, 1, 1, 12, 0)
        val path = File(tmp(), "r.json")
        val rs = RuleStore(path, now = { clock })
        var instant = true
        rs.instant = { instant }
        assertEquals(RuleResult.APPLIED, rs.add(RuleKeys.WHITE_DOMAINS, "school.edu.cn").first)
        assertEquals(RuleResult.EXISTS, rs.add(RuleKeys.WHITE_DOMAINS, "school.edu.cn").first)
        assertEquals(RuleResult.APPLIED, rs.add(RuleKeys.BLACK_DOMAINS, "bilibili.com").first)
        assertEquals(RuleResult.APPLIED, rs.add(RuleKeys.BLACK_APPS, "com.steam").first)
        assertEquals(RuleResult.APPLIED, rs.add(RuleKeys.WHITE_APPS, "com.code").first)
        assertEquals(RuleResult.INVALID, rs.add(RuleKeys.WHITE_DOMAINS, "???").first)
        instant = false
        val s = rs.snap
        val B = RuleKeys.BLACKLIST
        val W = RuleKeys.WHITELIST
        // 联网只看网站名单, 应用名单不参与 (MECHANICS 4.1 / 14.2)
        assertEquals(true to "whitelist-domain", s.decide("www.school.edu.cn", false, W))
        assertEquals(false to "offline", s.decide("zhihu.com", false, W))
        assertEquals(true to "online", s.decide("www.bilibili.com", true, W))
        assertEquals(true to "default-allow", s.decide("www.school.edu.cn", false, B))
        assertEquals(false to "blacklist-domain", s.decide("www.bilibili.com", false, B))
        assertEquals(true to "online", s.decide("www.bilibili.com", true, B))  // 联网中全部放行
        assertEquals(true to "local", s.decide("127.0.0.1", false, W))
        assertEquals(true to "default-allow", s.decide("notbilibili.com", false, B))
        // 应用名单只管锁定
        assertEquals(false to "blacklist-app", s.appAllowed("com.steam", false, B))
        assertEquals(false to "blacklist-app", s.appAllowed("com.steam", true, B))
        assertEquals(true to "default-allow", s.appAllowed("com.code", false, B))
        assertEquals(true to "whitelist-app", s.appAllowed("com.code", false, W))
        assertEquals(false to "not-whitelisted", s.appAllowed("com.steam", false, W))
        assertEquals(true to "online", s.appAllowed("com.steam", true, W))
        // 删除一律要等; 等待中再加回 = 取消
        assertEquals(RuleResult.STAGED, rs.remove(RuleKeys.BLACK_DOMAINS, "bilibili.com").first)
        assertEquals(RuleResult.STAGED, rs.remove(RuleKeys.BLACK_DOMAINS, "bilibili.com").first)
        assertEquals(1, rs.pending.size)
        assertEquals(RuleResult.CANCELLED, rs.add(RuleKeys.BLACK_DOMAINS, "bilibili.com").first)
        assertEquals(RuleResult.STAGED, rs.add(RuleKeys.BLACK_APPS, "com.qq").first)
        assertFalse("com.qq" in rs.items(RuleKeys.BLACK_APPS))
        assertEquals(RuleResult.CANCELLED, rs.remove(RuleKeys.BLACK_APPS, "com.qq").first)
        assertTrue(rs.pending.isEmpty())
        // 排队写进文件, 重新读取后照样按时生效
        assertEquals(RuleResult.STAGED, rs.add(RuleKeys.WHITE_DOMAINS, "wikipedia.org").first)
        val rs2 = RuleStore(path, now = { clock })
        assertEquals(1, rs2.pending.size)
        clock = clock.plusHours(47)
        assertTrue(rs2.promoteDue().isEmpty())
        clock = clock.plusHours(2)
        assertEquals(1, rs2.promoteDue().size)
        assertTrue("wikipedia.org" in rs2.items(RuleKeys.WHITE_DOMAINS))
    }

    @Test
    fun netQuota() {
        val dir = tmp()
        val s = Settings(File(dir, "s.json"))
        var now = 1_000_000L
        val n = NetGuard(dir, s) { now }
        var calls = 0
        n.backend = { calls++ }
        val limit = s.dailyQuota(LocalDate.now())
        assertEquals(limit, n.remaining, 0.0)
        assertNotNull(n.request(0, "x"))
        assertNotNull(n.request(100000, "x"))
        assertNull(n.request(10, "查资料"))
        assertEquals("pending", n.mode)
        assertNotNull(n.request(10, "y"))
        now += 299_000
        assertNull(n.tick(1.0))
        now += 2_000
        assertEquals("started", n.tick(1.0))
        assertTrue(n.online)
        // 每一步最多计入 TICK_CLAMP 秒
        n.tick(1000.0)
        assertEquals(1.0 + 30.0, n.used, 1e-6)
        n.endEarly()
        assertFalse(n.online)
        // 快速访问 ×2
        assertNull(n.request(1, "q", quick = true))
        n.tick(0.0)
        assertTrue(n.grantQuick)
        assertNull(n.tick(30.0))
        assertEquals("ended", n.tick(30.0))
        assertEquals(31.0 + 120.0, n.used, 1e-6)
        // 借贷: 今天 +X, 明天 -2X; 最多借明天剩余的一半
        val tomorrow = s.dailyQuota(LocalDate.now().plusDays(1))
        assertEquals(tomorrow / 2, n.loanLeft, 1e-6)
        assertNull(n.borrow(10, "急用"))
        assertEquals(600.0, n.bonus, 0.0)
        assertEquals(1200.0, n.tomorrowUsed, 0.0)
        // 休息中冷静期打折
        assertNull(n.request(5, "r", cooldownRate = 0.4))
        assertEquals(now + 120_000, n.pending!!.at)
        n.cancel()
        // 欠款记到之后第一次打开的那一天
        val state = File(dir, "net_state.json")
        state.writeText(state.readText().replace(LocalDate.now().key(), LocalDate.now().minusDays(3).key()))
        val n2 = NetGuard(dir, s) { now }
        assertEquals(1200.0, n2.debt, 0.0)
        assertEquals(0.0, n2.used, 0.0)
        assertTrue(calls > 0)
    }

    @Test
    fun dataSplitAndTally() {
        assertEquals(listOf("学习", "吃饭"), splitItems("学习／吃饭/ "))
        val t = tallyItems(listOf("学习/吃饭" to 3600.0, "学习" to 3600.0, "Study" to 60.0, "study" to 60.0))
        assertEquals(5400.0, t["学习"]!!, 1e-9)
        assertEquals(120.0, t["Study"]!!, 1e-9)
        val dm = DataStore(tmp(), { 3600.0 }, { "Sleeping" })
        val st = LocalDateTime.of(2026, 3, 1, 23, 30)
        dm.addStudySession(st, st.plusHours(1), 60.0)
        assertEquals(0.5, dm.study["2026-03-01"]!!, 1e-9)
        assertEquals(0.5, dm.study["2026-03-02"]!!, 1e-9)
    }

    @Test
    fun dnsPackets() {
        // 一个 IPv4 UDP DNS 查询: example.com A
        val q = byteArrayOf(0x12, 0x34, 0x01, 0x00, 0, 1, 0, 0, 0, 0, 0, 0,
            7, 'e'.code.toByte(), 'x'.code.toByte(), 'a'.code.toByte(), 'm'.code.toByte(), 'p'.code.toByte(),
            'l'.code.toByte(), 'e'.code.toByte(), 3, 'c'.code.toByte(), 'o'.code.toByte(), 'm'.code.toByte(), 0, 0, 1, 0, 1)
        assertEquals("example.com", Dns.qname(q))
        val pkt = UdpQuery.ipv4(byteArrayOf(10, 111, 222.toByte(), 1), byteArrayOf(10, 111, 222.toByte(), 2), 40000, 53, q)
        val p = Packets.parseUdp(pkt, pkt.size)!!
        assertEquals(53, p.dstPort)
        assertEquals("example.com", Dns.qname(p.payload))
        val nx = Dns.nxdomain(p.payload)!!
        assertEquals(0x81.toByte(), nx[2])
        assertEquals(0x83.toByte(), nx[3])
        val reply = Packets.buildReply(p, nx)
        val back = Packets.parseUdp(reply, reply.size)!!
        assertEquals(40000, back.dstPort)
        assertEquals(53, back.srcPort)
        assertTrue(back.payload.contentEquals(nx))
        // IPv4 首部校验和: 对整个首部求和应为 0xffff
        var sum = 0L
        for (i in 0 until 20 step 2) sum += ((reply[i].toInt() and 0xff) shl 8 or (reply[i + 1].toInt() and 0xff)).toLong()
        while (sum ushr 16 != 0L) sum = (sum and 0xffff) + (sum ushr 16)
        assertEquals(0xffff, sum.toInt())
    }

    private object NoHost : Host {
        var fg: String? = null
        val locks = mutableListOf<String>()
        var unlocks = 0
        override fun lockApp(pkg: String, why: String) { locks.add("$pkg:$why") }
        override fun unlockApp() { unlocks++ }
        override fun lockExempt(pkg: String, whitelist: Boolean) = pkg == "com.android.settings"
        override fun toast(t: Toast) {}
        override fun noticesChanged() {}
        override fun changed() {}
        override fun applyNet(force: Boolean) {}
        override fun playChime(done: () -> Unit) = done()
        override fun hourPrompt(slot: LocalDateTime, silent: Boolean) {}
        override fun foregroundApp(): String? = fg
        override fun openPage(page: String) {}
        override fun scheduleWake(seconds: Double) {}
    }

    /** VPN 只看网站名单; 不管名单怎么写, OIT 自己连其他设备的局域网通道都在 (MECHANICS 14.2, 15). */
    @Test
    fun vpnPlanUsesOnlySiteLists() {
        val core = Core(tmp(), NoHost) { 0.0 }       // 第一次打开: 配置期内, 改动立即生效
        fun lanOpen(p: VpnPlan) = when (p) {
            is VpnPlan.Dns -> true                               // 只接管 DNS: 局域网直连
            is VpnPlan.BlockAll -> p.keepLan
            VpnPlan.None -> true
        }
        core.rules.add(RuleKeys.BLACK_APPS, "tv.danmaku.bili")
        assertEquals(VpnPlan.Dns(false, 0), core.vpnPlan())         // 应用黑名单不进 VPN, 只锁定
        core.rules.add(RuleKeys.BLACK_DOMAINS, "bilibili.com")
        assertTrue(core.vpnPlan() is VpnPlan.Dns && (core.vpnPlan() as VpnPlan.Dns).filter)
        core.stageSetting("FILTER_MODE", RuleKeys.WHITELIST)
        core.rules.add(RuleKeys.WHITE_APPS, "com.code")
        assertEquals(VpnPlan.BlockAll(keepLan = true), core.vpnPlan())   // 网站白名单为空: 应用白名单也不放行
        core.rules.add(RuleKeys.WHITE_DOMAINS, "school.edu.cn")
        assertTrue(core.vpnPlan() is VpnPlan.Dns && lanOpen(core.vpnPlan()))
        // 代理-全屏蔽 (Proxy block-all): 接管 DNS, 全部拦截, 局域网照样能连
        core.stageSetting("NET_MODE", "adapter")
        assertEquals(VpnPlan.Dns(true, 0, blockAll = true), core.vpnPlan())
        assertTrue(lanOpen(core.vpnPlan()))
    }

    /** 每日联网额度填 325799 = 开 / 关开发者模式: 不是真的修改, 不排队. */
    @Test
    fun devToggle() {
        val core = Core(tmp(), NoHost) { 0.0 }
        core.endSetupPeriod()
        val code = DEV_TOGGLE_CODE * 60.0
        assertEquals(StageResult.NOCHANGE to "", core.stageSetting("DAILY_LIMIT", code))
        assertTrue(core.settings.devMode)
        assertTrue(core.settings.pending.isEmpty())
        assertEquals(VpnPlan.None, core.vpnPlan())                // 开发者模式: 不断网
        core.hasPeers = { true }
        assertEquals(VpnPlan.Dns(false, 0), core.vpnPlan())       // 绑定了设备: 留一个全部放行的 VPN, 免得被冻结
        core.hasPeers = { false }
        assertEquals("", core.settings.waitTag("DAILY_LIMIT"))
        assertEquals(StageResult.NOCHANGE, core.stageSetting("DAILY_LIMIT", code).first)
        assertFalse(core.settings.devMode)
        assertEquals(7200.0, core.settings.num("DAILY_LIMIT"), 0.0)
        assertEquals(StageResult.INVALID, core.stageSetting("COOLDOWN", code).first)   // 只认每日联网额度
        // 子端跟着母端, 自己不能开关
        core.followsMother = { true }
        core.stageSetting("DAILY_LIMIT", code)
        assertFalse(core.settings.devMode)
        // 母端断开 2 分钟后, 子端的开发者模式自动关掉
        core.followsMother = { false }
        core.stageSetting("DAILY_LIMIT", code)
        assertTrue(core.settings.devMode)
        core.followsMother = { true }
        core.motherFresh = { false }
        core.tick()
        assertFalse(core.settings.devMode)
    }

    /** 绑定了其他设备, 共享的专注中 OIT 被关掉: 重开时关掉的这段记为空闲, 专注终止, 总统计里空闲压过专注 (MECHANICS 8.2). */
    @Test
    fun closedDuringSharedFocus() {
        val dir = tmp()
        var clock = 1000.0
        val a = Core(dir, NoHost) { clock }
        a.myId = { "A" }
        a.timer.onAction()                                       // 开始专注
        val start = a.timer.liveState().getString("start")
        // 模拟: 20 分钟前最后一次写进文件, 之后 OIT 被关掉了
        val f = File(dir, "timer_state.json")
        val st = JSONObject(f.readText())
        val s0 = LocalDateTime.now().minusMinutes(30).withNano(0)
        val alive = s0.plusMinutes(10)
        st.getJSONObject("focus").put("start", s0.toString()).put("alive", alive.toString())
        f.writeText(st.toString())
        clock = 5000.0
        val b = Core(dir, NoHost) { clock }
        b.myId = { "A" }
        b.hasPeers = { true }
        b.syncedSince = { false }
        b.start()
        assertEquals(TState.RUNNING, b.timer.state)              // 先照常恢复, 等第一次同步
        assertTrue(b.dm.focus.any { it.kind == "idle" && it.focus == s0.format(Fmt.TS) })
        b.timer.check()
        assertEquals(TState.RUNNING, b.timer.state)              // 60 秒内还在等同步
        clock += 61.0
        b.timer.check()
        assertEquals(TState.IDLE, b.timer.state)                 // 没有设备结束它: 本机终止
        val study = b.dm.focus.filter { it.kind == "study" }
        assertEquals(1, study.size)
        assertEquals(alive, study[0].end)                        // 专注记到被关掉的时刻为止
        assertTrue(start.isNotEmpty())
        // 别的设备记了整段专注: 总统计里被关掉的那段算空闲
        b.dm.addFocusSession("study", s0, LocalDateTime.now())
        val total = listOf(LocalDate.now().minusDays(1), LocalDate.now())
            .sumOf { d -> b.dm.focusHours(d, null).sumOf { it.first } }
        // 本机那段 10 分钟 + 别的设备那段去掉空闲后剩下的 10 分钟 (不去掉的话是 40 分钟)
        assertEquals(20 * 60.0, total, 5.0)
    }

    /** 有设备离线时: 专注照常, 休息照常算, 但离线的那段记为空闲, 不计入学习时长 (MECHANICS 8.4). */
    @Test
    fun offlineStretchIsIdle() {
        var clock = 1000.0
        val core = Core(tmp(), NoHost) { clock }
        core.endSetupPeriod()
        core.offlineSpans = { a, _ -> listOf(a to a + 20 * 60_000L) }   // 测试里墙上时间不走: 直接给 20 分钟离线
        core.timer.onAction()
        clock += 3600.0
        core.timer.onAction()                                    // 专注 60 分钟, 结束后进入休息
        assertEquals(TState.ON_BREAK, core.timer.state)
        assertEquals(40.0 / 60, core.dm.todayHours() + core.dm.yesterdayHours(), 0.02)   // 只计 40 分钟
        assertTrue(core.timer.countdown > 15 * 60)               // 休息照常按 60 分钟算 (约 16 分钟)
        assertTrue(core.dm.focus.any { it.kind == "idle" })
    }

    /** 下载安装包走 GitHub API 的附件地址 (不经过 github.com), 带着大小和 SHA-256 核对 (MECHANICS 16). */
    @Test
    fun updateSourceUsesApiAssetUrl() {
        val U = com.obliphur.oit.service.Updater
        val rel = JSONObject().put("zipball_url", "https://api.github.com/repos/x/y/zipball/1.0.9").put("assets", org.json.JSONArray()
            .put(JSONObject().put("name", "OIT.apk").put("url", "https://api.github.com/repos/x/y/releases/assets/7")
                .put("browser_download_url", "https://github.com/x/y/releases/download/1.0.9/OIT.apk")
                .put("size", 5).put("digest", "sha256:ABCD")))
        assertEquals(com.obliphur.oit.service.Updater.Companion.Source("https://api.github.com/repos/x/y/releases/assets/7", U.ACCEPT_FILE, 5, "abcd"), U.pickApk(rel))
        assertEquals(com.obliphur.oit.service.Updater.Companion.Source("https://api.github.com/repos/x/y/zipball/1.0.9", U.ACCEPT_JSON, -1, null), U.pickWindows(rel))
        assertNull(U.pickApk(JSONObject()))
    }

    /** 应用名单: 前台应用不能用就锁定, 离开后撤掉 (MECHANICS 14.6). */
    @Test
    fun appLock() {
        NoHost.locks.clear(); NoHost.unlocks = 0; NoHost.fg = null
        val core = Core(tmp(), NoHost) { 0.0 }
        core.rules.add(RuleKeys.BLACK_APPS, "tv.danmaku.bili")
        core.stageSetting("NET_MODE", "proxy")
        NoHost.fg = "tv.danmaku.bili"
        core.tick()
        assertEquals(listOf("tv.danmaku.bili:blacklist-app"), NoHost.locks)
        assertEquals("tv.danmaku.bili", core.lockedApp)
        NoHost.fg = ""                                           // 回到桌面
        core.tick()
        assertEquals(null, core.lockedApp)
        assertEquals(1, NoHost.unlocks)
        // 白名单模式: 不在应用白名单里的锁定, 永远不锁的除外; 联网中不锁
        core.stageSetting("FILTER_MODE", RuleKeys.WHITELIST)
        core.rules.add(RuleKeys.WHITE_APPS, "com.code")
        NoHost.locks.clear()
        for (a in listOf("com.code", "com.android.settings", "OIT")) { NoHost.fg = a; core.tick() }
        assertTrue(NoHost.locks.isEmpty())
        NoHost.fg = "com.game"
        core.tick()
        assertEquals(listOf("com.game:not-whitelisted"), NoHost.locks)
        // 全屏蔽时应用名单照样起作用 (只剩应用名单)
        core.stageSetting("NET_MODE", "adapter")
        core.tick()
        assertEquals("com.game", core.lockedApp)
        NoHost.fg = "com.code"
        core.tick()
        assertEquals(null, core.lockedApp)
    }

    /** 计时器此刻的状态在设备之间切换: 照着切换, 只有操作的那台记录 (MECHANICS 15.2). */
    @Test
    fun sharedTimerState() {
        var clock = 1000.0
        val a = Core(tmp(), NoHost) { clock }
        val b = Core(tmp(), NoHost) { clock }
        a.myId = { "A" }; b.myId = { "B" }
        a.timer.onAction()                                       // A 开始专注
        val la = a.timer.liveState()
        assertEquals("running", la.getString("state"))
        assertEquals("A", la.getString("owner"))
        b.timer.adopt(la)
        assertEquals(TState.RUNNING, b.timer.state)
        assertEquals(canon(la), canon(b.timer.liveState()))         // 照着切换后两边一模一样, 不会来回覆盖
        clock += 1800.0
        b.timer.onAction()                                       // 在 B 上结束专注: B 记学习时长, 进入休息
        assertTrue(b.dm.todayHours() > 0.49)
        val lb = b.timer.liveState()
        assertEquals("break", lb.getString("state"))
        assertEquals("B", lb.getString("owner"))
        a.timer.adopt(lb)
        assertEquals(TState.ON_BREAK, a.timer.state)
        assertEquals(0.0, a.dm.todayHours(), 0.0)               // A 不重复记
        assertEquals(canon(lb), canon(a.timer.liveState()))
        assertEquals(b.timer.countdown, a.timer.countdown, 2.0)
        a.timer.onBreakButton()                                  // 在 A 上暂停休息
        b.timer.adopt(a.timer.liveState())
        assertEquals(TState.IDLE, b.timer.state)
        // "还在吗": 别的设备是主人且在线时, 本机不自动结束
        b.timer.onAction()
        a.timer.adopt(b.timer.liveState())
        a.peerFresh = { it == "B" }
        clock += Cfg.FOCUS_CHECK_EVERY + Cfg.FOCUS_CHECK_GRACE + 10
        a.timer.check()
        assertEquals(TState.RUNNING, a.timer.state)
        b.timer.check()
        assertEquals(TState.IDLE, b.timer.state)                // 主人自己自动结束
    }

    private fun canon(o: org.json.JSONObject) = com.obliphur.oit.sync.SyncService.canon(o)

    /** 应用时间线与多台设备合并 (和桌面版 data_manager 的例子一样). */
    @Test
    fun windowTimeline() {
        val dm = DataStore(tmp(), { 3600.0 }, { "Sleeping" })
        val t = LocalDateTime.of(2026, 10, 17, 9, 0, 5)
        for (i in 0 until 12) dm.noteRun("chrome", t.withMinute(i * 10 / 60).withSecond(5 + (i * 10) % 60))
        dm.noteRun("code", LocalDateTime.of(2026, 10, 17, 9, 1, 58))
        dm.noteRun("code", LocalDateTime.of(2026, 10, 17, 9, 2, 10))
        assertEquals(listOf(Triple(540, 541, "chrome"), Triple(541, 543, "code")),
            dm.windowRuns["2026-10-17"]!!.map { Triple(it.s, it.e, it.app) })
        val mother = WindowSource(emptyMap(), mapOf("2026-10-17" to listOf(WinRun(540, 600, "chrome"), WinRun(600, 610, ""))))
        val phone = WindowSource(mapOf("2026-09-30" to mapOf("x" to 5.0)), mapOf("2026-10-17" to listOf(WinRun(590, 620, "tv.danmaku.bili"))))
        assertEquals(mapOf("chrome" to 3600.0, "tv.danmaku.bili" to 1200.0), combineWindow(listOf(mother, phone), "2026-10"))
        assertEquals(mapOf("x" to 5.0), combineWindow(listOf(mother, phone), "2026-09"))
        assertEquals(1800.0, combineWindow(listOf(phone, mother), "2026-10")["tv.danmaku.bili"]!!, 0.0)
    }

    @Test
    fun updateVersionRules() {
        // 和桌面版 updater.py 的自检同样的例子
        val U = com.obliphur.oit.service.Updater
        assertEquals(listOf(1, 0, 0), U.parseVersion("1"))
        assertEquals(listOf(1, 2, 0), U.parseVersion("v1.2"))
        assertEquals(null, U.parseVersion("Python"))
        assertEquals(listOf(1, 0, 0), U.releaseVersion(org.json.JSONObject().put("tag_name", "1").put("name", "1.0.0")))
        assertEquals(listOf(2, 1, 0), U.releaseVersion(org.json.JSONObject().put("tag_name", "v2.1").put("name", "Spring update")))
        assertTrue(U.newer(listOf(1, 1, 0), listOf(1, 0, 9)))
        assertFalse(U.newer(listOf(1, 1, 0), listOf(1, 1, 0)))
        val rel = org.json.JSONObject().put("assets", org.json.JSONArray()
            .put(org.json.JSONObject().put("name", "OIT-windows.zip").put("browser_download_url", "w"))
            .put(org.json.JSONObject().put("name", "OIT-android-release.apk").put("browser_download_url", "a")))
        assertEquals("a", U.pickApk(rel)?.url)
        assertEquals(null, U.pickApk(org.json.JSONObject().put("assets", org.json.JSONArray())))
    }

    @Test
    fun translationsHavePlaceholders() {
        // 英文译文里的占位符必须和中文原文一致
        val re = Regex("\\{(\\w+)\\}")
        for ((zh, en) in EnStrings.MAP) {
            assertEquals(zh, re.findAll(zh).map { it.value }.toSet(), re.findAll(en).map { it.value }.toSet())
        }
    }
}

/** 测试用: 拼一个 IPv4 UDP 包. */
object UdpQuery {
    fun ipv4(src: ByteArray, dst: ByteArray, sport: Int, dport: Int, payload: ByteArray): ByteArray {
        val out = ByteArray(28 + payload.size)
        out[0] = 0x45
        out[2] = (out.size ushr 8).toByte(); out[3] = out.size.toByte()
        out[8] = 64; out[9] = 17
        System.arraycopy(src, 0, out, 12, 4)
        System.arraycopy(dst, 0, out, 16, 4)
        out[20] = (sport ushr 8).toByte(); out[21] = sport.toByte()
        out[22] = (dport ushr 8).toByte(); out[23] = dport.toByte()
        val ul = 8 + payload.size
        out[24] = (ul ushr 8).toByte(); out[25] = ul.toByte()
        System.arraycopy(payload, 0, out, 28, payload.size)
        return out
    }
}
