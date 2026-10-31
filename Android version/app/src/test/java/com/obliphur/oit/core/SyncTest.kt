package com.obliphur.oit.core

import com.obliphur.oit.sync.MainRunner
import com.obliphur.oit.sync.PairRequest
import com.obliphur.oit.sync.SealError
import com.obliphur.oit.sync.SyncCrypto
import com.obliphur.oit.sync.SyncLocal
import com.obliphur.oit.sync.SyncService
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.math.BigInteger
import java.nio.file.Files
import java.util.concurrent.Executors

class SyncTest {
    /** 关机 (MECHANICS 15.4): 最后的状态写着收到关机指令, 或者电量低于 2% (同 devsync.went_off). */
    @Test
    fun wentOffMatchesDesktop() {
        val w = { st: JSONObject? -> SyncService.wentOff(st) }
        fun a(o: JSONObject) = o.put("auto", true)
        assertTrue(w(a(JSONObject().put("off", true))))
        assertTrue(w(a(JSONObject().put("battery", 1))))
        assertTrue(w(a(JSONObject().put("battery", 0))))
        assertFalse(w(a(JSONObject().put("battery", 2))))
        assertFalse(w(a(JSONObject().put("timer", "idle"))))
        assertFalse(w(a(JSONObject().put("battery", JSONObject.NULL))))
        assertFalse(w(JSONObject().put("off", true)))                 // 没开开机自启动: 照旧算离线
        assertFalse(w(JSONObject().put("off", true).put("auto", false)))
        assertFalse(w(null))
    }

    @Test
    fun cryptoMatchesDesktop() {
        // 和 devsync.py 自检里的固定输入 / 输出一样: 两端算法逐字节一致
        val k = ByteArray(32) { it.toByte() }
        val env = SyncCrypto.seal(k, "dev", JSONObject().put("a", 1), ByteArray(16), 1700000000000)
        assertEquals("pHEJdtrTcg==", env.getString("ct"))
        assertEquals("a9ee146998cf5a913f1e5d4756f3b151789ed6e8e0b04b367bf1712d19ff8b24", env.getString("mac"))
        assertEquals("338005", SyncCrypto.pairCode(k))
        assertEquals("0d2c6ab660f97893d7b57f47872f0ac4b773568d34c7596a216691d2ed4dcc93",
            SyncCrypto.hex(SyncCrypto.shared(BigInteger.valueOf(5), "80")))
        val (xa, pa) = SyncCrypto.keypair()
        val (xb, pb) = SyncCrypto.keypair()
        val ka = SyncCrypto.shared(xa, pb.toString(16))
        assertTrue(ka.contentEquals(SyncCrypto.shared(xb, pa.toString(16))))
        val e2 = SyncCrypto.seal(ka, "a", JSONObject().put("x", "你好"))
        assertEquals("你好", SyncCrypto.unseal(ka, e2).getString("x"))
        try { SyncCrypto.unseal(ka, e2); fail("replay") } catch (_: SealError) {}
        try { SyncCrypto.unseal(ka, JSONObject(e2.toString()).put("mac", "00")); fail("mac") } catch (_: SealError) {}
    }

    private class Fake(val name: String) : SyncLocal {
        var mode = "proxy"
        val docs = linkedMapOf<String, MutableMap<String, Any?>>(
            "settings" to linkedMapOf("DAILY_LIMIT" to JSONObject().put("value", 7200.0).put("pending", JSONObject.NULL)),
            "hours" to linkedMapOf(), "rules" to linkedMapOf("black_domains" to JSONArray()), "timer" to linkedMapOf())
        var used = 0.0
        var cleared = 0
        val requests = mutableListOf<PairRequest>()
        override fun netMode() = mode
        override fun autoText() = "Sleeping"
        override fun ledger(): JSONObject = JSONObject().put("net", JSONObject().put("date", "2026-01-01").put("used", used))
        @Synchronized override fun docs(): Map<String, Map<String, Any?>> = docs.mapValues { LinkedHashMap(it.value) }
        @Synchronized override fun apply(doc: String, changes: Map<String, Any?>) {
            for ((k, v) in changes) if (v == null) docs[doc]!!.remove(k) else docs[doc]!![k] = v
        }
        override fun clearHistory() { cleared++ }
        override fun status(): JSONObject = JSONObject().put("timer", "idle").put("net", "offline")
        override fun pairRequest(req: PairRequest) { synchronized(requests) { requests.add(req) } }
        override fun changed() {}
        @Volatile var dev = false
        @Volatile var got: Pair<ByteArray, String>? = null
        @Volatile var restarted: Pair<String, String>? = null
        override fun restartRequested(sender: String, name: String) { restarted = sender to name }
        var version = "1.0.0"
        override fun appVersion() = version
        override fun devMode() = dev
        override fun setDevMode(on: Boolean) { dev = on }
        override fun updateWanted(platform: String, version: String) = if (platform == "fake") "" else "platform"
        override fun updateDest(version: String): java.io.File = Files.createTempFile("pkg", version).toFile()
        override fun updateReceived(file: java.io.File, version: String, from: String) { got = file.readBytes() to "$version/$from" }
    }

    private fun waitFor(ms: Long = 10_000, cond: () -> Boolean) {
        val end = System.currentTimeMillis() + ms
        while (System.currentTimeMillis() < end) { if (cond()) return; Thread.sleep(50) }
        fail("timeout")
    }

    /** 和桌面版 devsync.py 互通 (需要先在本机跑起 interop_peer.py, 并设置 OIT_INTEROP=1). */
    @Test
    fun interopWithDesktop() {
        if (System.getenv("OIT_INTEROP") != "1") return
        val main = Executors.newSingleThreadExecutor()
        var mainThread: Thread? = null
        main.submit { mainThread = Thread.currentThread() }.get()
        val runner = MainRunner({ main.execute(it) }, { Thread.currentThread() === mainThread })
        val l = Fake("Android")
        l.docs["hours"]!!["2026-01-01/7"] = "旧数据"
        val s = SyncService(Files.createTempDirectory("i").toFile(), l, runner, defaultName = "Android", httpPort0 = 18080, udpPort = 18081)
        try {
            s.start()
            assertEquals("windows", s.hello("127.0.0.1", 18070).getString("platform"))
            s.startPairing("127.0.0.1", 18070, motherIsMe = false)       // PC 是母端
            waitFor(20_000) { s.outgoing?.state == "accepted" && l.cleared == 1 }
            // 用 PC 的内容初始化
            assertEquals(5400.0, (l.docs["settings"]!!["DAILY_LIMIT"] as JSONObject).getDouble("value"), 0.0)
            assertEquals(mapOf("2026-01-01/8" to "来自PC"), l.docs["hours"]!!.toMap())
            assertEquals(900.0, SyncService.peerNet(s.peerLedgers(), "2026-01-01")[0], 0.0)
            // 再从 Android 改一项, 同步给 PC
            Thread.sleep(5)
            runner.call { l.apply("hours", mapOf("2026-01-01/9" to "来自安卓")) }
            l.used = 300.0
            assertTrue(s.syncWith(s.peers().keys.first()))
        } finally {
            s.stop(); main.shutdownNow()
        }
    }

    @Test
    fun pairAndSync() {
        // 两个实例共用一个"界面线程"
        val main = Executors.newSingleThreadExecutor()
        var mainThread: Thread? = null
        main.submit { mainThread = Thread.currentThread() }.get()
        val runner = MainRunner({ main.execute(it) }, { Thread.currentThread() === mainThread })
        val la = Fake("A")
        val lb = Fake("B")
        la.docs["settings"]!!["DAILY_LIMIT"] = JSONObject().put("value", 3600.0).put("pending", JSONObject.NULL)
        la.docs["hours"]!!["2026-01-01/9"] = "学习"
        lb.docs["hours"]!!["2026-01-01/10"] = "游戏"
        val sa = SyncService(Files.createTempDirectory("a").toFile(), la, runner, defaultName = "A", httpPort0 = 18050, udpPort = 18051)
        val sb = SyncService(Files.createTempDirectory("b").toFile(), lb, runner, defaultName = "B", httpPort0 = 18060, udpPort = 18061)
        try {
            sa.start(); sb.start()
            assertEquals(sb.myId, sa.hello("127.0.0.1", sb.port).getString("id"))
            // 电脑发来的 GET 不带 Content-Length: 也要马上回复, 不能读到连接关闭 (以前绑定时一直 timed out)
            java.net.Socket("127.0.0.1", sb.port).use { s ->
                s.soTimeout = 3000
                s.getOutputStream().write("GET /oit/v1/hello HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".toByteArray())
                val reply = String(s.getInputStream().readBytes())
                assertTrue(reply, reply.startsWith("HTTP/1.1 200") && reply.contains(sb.myId))
            }
            // B 是子端 (A 为母端)
            sa.startPairing("127.0.0.1", sb.port, true)
            waitFor { synchronized(lb.requests) { lb.requests.isNotEmpty() } && sa.outgoing?.state == "waiting" }
            val req = lb.requests.last()
            assertEquals(sa.outgoing!!.code, req.code)
            runner.call { sb.answerPairing(req.token, true) }
            waitFor { sa.outgoing?.state == "accepted" && lb.cleared == 1 && sb.peers()[sa.myId]?.optBoolean("init") == false }
            assertEquals(0, la.cleared)
            // 绑定刚完成时的先后顺序不能变成"同步失败" (以前子端先同步过去会得到 401 unpaired)
            assertEquals("", sb.peers()[sa.myId]!!.optString("last_err"))
            assertEquals("", sa.peers()[sb.myId]!!.optString("last_err"))
            assertEquals(3600.0, (lb.docs["settings"]!!["DAILY_LIMIT"] as JSONObject).getDouble("value"), 0.0)
            assertEquals(mapOf("2026-01-01/9" to "学习"), lb.docs["hours"]!!.toMap())
            assertFalse("子端的旧数据不能传给母端", la.docs["hours"]!!.containsKey("2026-01-01/10"))
            // 之后: 新的修改覆盖旧的, 账互相转交; 自动记录让位给自己填写的
            Thread.sleep(5)
            runner.call { lb.apply("hours", mapOf("2026-01-01/11" to "吃饭")) }
            lb.used = 600.0
            runner.call { la.apply("rules", mapOf("black_domains" to JSONArray().put("bilibili.com"))) }
            assertTrue(sb.syncWith(sa.myId))
            assertEquals("吃饭", la.docs["hours"]!!["2026-01-01/11"])
            assertEquals("bilibili.com", (lb.docs["rules"]!!["black_domains"] as JSONArray).getString(0))
            assertEquals(600.0, SyncService.peerNet(sa.peerLedgers(), "2026-01-01")[0], 0.0)
            runner.call { la.apply("hours", mapOf("2026-01-01/12" to "写作业")); lb.apply("hours", mapOf("2026-01-01/12" to "Sleeping")) }
            assertTrue(sa.syncWith(sb.myId))
            assertEquals("写作业", lb.docs["hours"]!!["2026-01-01/12"])
            runner.call { la.apply("hours", mapOf("2026-01-01/9" to null)) }
            assertTrue(sa.syncWith(sb.myId))
            assertFalse(lb.docs["hours"]!!.containsKey("2026-01-01/9"))
            // 开发者模式跟着母端 (A); 子端 (B) 自己的状态不会传给母端
            la.dev = true
            assertTrue(sa.syncWith(sb.myId)); assertTrue(lb.dev)
            lb.dev = false
            assertTrue(sb.syncWith(sa.myId)); assertTrue(la.dev); assertTrue(lb.dev)
            la.dev = false
            assertTrue(sa.syncWith(sb.myId)); assertFalse(lb.dev)
            assertEquals("1.0.0", sa.peers()[sb.myId]!!.optString("version"))
            // 版本不一样: 版本低的算离线, 两边都知道不一样 (不能开始合约)
            assertTrue(sa.versionMismatch().isEmpty()); assertTrue(sa.deviceStates().single().online)
            la.version = "1.0.1"
            assertTrue(sa.syncWith(sb.myId))
            val low = sa.deviceStates().single()
            assertFalse(low.online); assertTrue(low.outdated); assertEquals("1.0.0", low.version)
            assertEquals(listOf(false), sa.versionMismatch().map { it.third })
            assertEquals(listOf("1.0.1" to true), sb.versionMismatch().map { it.second to it.third })
            assertTrue(sb.deviceStates().single().online); assertFalse(sb.deviceStates().single().outdated)
            la.version = "1.0.0"
            assertTrue(sa.syncWith(sb.myId)); assertTrue(sa.versionMismatch().isEmpty() && sb.versionMismatch().isEmpty())
            // 设备离线时请它重新启动 OIT (MECHANICS 15.3): 请求送到就重启, 刚重启过的不再理会, 密钥不对的拒绝
            val pa = sb.peers()[sa.myId]!!
            assertEquals(200, sb.restartTo(pa, 4000)); assertEquals(sb.myId to "B", la.restarted)
            la.restarted = null
            assertEquals(200, sb.restartTo(pa, 4000)); assertNull(la.restarted)
            assertEquals(403, sb.restartTo(JSONObject(pa.toString()).put("key", "11".repeat(32)), 4000)); assertNull(la.restarted)
            // 安装包直接发给 B, B 核对 SHA-256 后交给本机安装; 平台不对的不要
            val pkg = Files.createTempFile("oit", ".bin").toFile()
            val blob = ByteArray(300_000) { (it * 31 % 251).toByte() }
            pkg.writeBytes(blob)
            assertEquals("sent", sa.offerUpdate(sb.myId, pkg, "fake", "9.9.9"))
            waitFor { lb.got != null }
            assertTrue(lb.got!!.first.contentEquals(blob))
            assertEquals("9.9.9/A", lb.got!!.second)
            assertEquals("platform", sa.offerUpdate(sb.myId, pkg, "android", "9.9.9"))
            // B 重置: A 解除绑定并删掉保留的 B 的数据, 之后也不再收 B 重置前的旧账
            assertTrue(sa.peerLedgersById().containsKey(sb.myId))
            sb.forgetMe()
            waitFor { sa.peers().isEmpty() }
            assertFalse(sa.peerLedgersById().containsKey(sb.myId))
            val gone = runner.call { sa.snapshot() }.getJSONObject("gone")
            assertTrue(gone.has(sb.myId))
            runner.call { sa.merge(JSONObject().put("ledgers", JSONObject().put(sb.myId, JSONObject().put("rev", 5)))) }
            assertFalse(sa.peerLedgersById().containsKey(sb.myId))
            runner.call { sb.unpair(sa.myId, notify = false) }
            // 手动解除绑定后: 再送来的对方的旧状态不收, 不会成为解除不掉的"离线"设备
            val old = System.currentTimeMillis() - 1000
            runner.call { sb.merge(JSONObject().put("status", JSONObject().put(sa.myId, JSONObject().put("ts", old)))) }
            assertTrue(sb.deviceStates().none { it.id == sa.myId })
            // 旧版本留下的残留 (没有绑定、也没人转交的设备): 一台绑定的设备都没有时全部清掉
            runner.call { sb.merge(JSONObject().put("status", JSONObject().put("leftover", JSONObject().put("ts", System.currentTimeMillis())))
                .put("ledgers", JSONObject().put("leftover", JSONObject().put("rev", 1).put("name", "Old")))) }
            assertTrue(sb.deviceStates().isEmpty())
            assertTrue(runner.call { sb.snapshot() }.getJSONObject("gone").has("leftover"))
            assertTrue(sb.peerLedgers().isEmpty())
        } finally {
            sa.stop(); sb.stop(); main.shutdownNow()
        }
    }

    /** 文件通道 (MECHANICS 15.6): 加密和桌面版 devsync.file_xor 一样, 可以从任意位置接着算; 文件名去掉路径. */
    @Test
    fun fileChannelCipher() {
        val fk = SyncCrypto.fileKey(ByteArray(32) { 1 })
        val nonce = ByteArray(16) { it.toByte() }
        fun data() = ByteArray(768) { (it % 256).toByte() }
        val a = data().also { SyncCrypto.fileXor(fk, nonce, 0, it) }
        assertEquals("b14cf71db18ec6e7a7af4da53b79cd35f72d52faa4811d7d396d34d848fa6a06", SyncCrypto.hex(SyncCrypto.sha256(a)))
        val b = data().also { SyncCrypto.fileXor(fk, nonce, 37, it) }
        assertEquals("d2950937ce7f5bc87e54fd9df488894e85c80cb9e3e0cfd3caeee0742930b9c2", SyncCrypto.hex(SyncCrypto.sha256(b)))
        // 分几段解密 (段长不是 32 的倍数) 照样还原
        var off = 0
        for (step in listOf(7, 100, 33, 628)) {
            val part = a.copyOfRange(off, off + step)
            SyncCrypto.fileXor(fk, nonce, off.toLong(), part)
            part.copyInto(a, off)
            off += step
        }
        assertTrue(a.contentEquals(data()))
        assertEquals("b_c_.png", SyncCrypto.safeName("..\\x\\a/b:c?.png"))
        assertEquals("y.txt", SyncCrypto.safeName("C:\\x\\y.txt"))
        assertEquals("file", SyncCrypto.safeName(" . "))
    }

    @Test
    fun peerNetYesterday() {
        // 还停在昨天的设备: 昨天借的变成今天的欠款, 其余不算
        val led = listOf(org.json.JSONObject().put("net", org.json.JSONObject().put("date", "2026-01-01")
            .put("used", 600.0).put("bonus", 1200.0).put("tomorrow_used", 2400.0)))
        assertEquals(listOf(0.0, 0.0, 0.0, 2400.0, 0.0), SyncService.peerNet(led, "2026-01-02").toList())
        assertEquals(0.0, SyncService.peerNet(led, "2026-01-03")[3], 0.0)
        assertEquals(0.0, SyncService.peerNet(led, "2026-01-01")[3], 0.0)
    }
}
