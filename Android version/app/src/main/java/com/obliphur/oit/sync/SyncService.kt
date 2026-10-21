package com.obliphur.oit.sync

import com.obliphur.oit.core.numText
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.File
import java.io.InputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.ServerSocket
import java.net.Socket
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/** 和本机数据打交道的部分 (Android 实现见 AndroidLocal.kt). 所有方法都在界面线程里调用. */
interface SyncLocal {
    fun netMode(): String
    fun autoText(): String
    /** 整点记录里这一格的自动填充内容 (行为模式会按小时变, MECHANICS 21). */
    fun autoTextFor(key: String): String = autoText()
    fun ledger(): JSONObject
    /** {文档: {项: 值}}, 值是 JSONObject / JSONArray / String / Number / null. */
    fun docs(): Map<String, Map<String, Any?>>
    fun apply(doc: String, changes: Map<String, Any?>)
    fun clearHistory()
    fun status(): JSONObject
    fun pairRequest(req: PairRequest)
    fun changed()
    fun appVersion(): String = ""
    /** 开发者模式: 子端跟着母端. */
    fun devMode(): Boolean = false
    fun setDevMode(on: Boolean) {}
    /** 其他设备直接发来的安装包 (MECHANICS 16): "" = 要, 否则是不要的原因. */
    fun updateWanted(platform: String, version: String): String = "platform"
    fun updateDest(version: String): File? = null
    fun updateReceived(file: File, version: String, from: String) {}
    fun updateFailed(error: String) {}
    /** 绑定的设备请这台设备给它下载新版本并发过去 (它自己从 GitHub 下不动): "" = 好, 否则是原因. */
    fun updateRequested(peerId: String): String = "unsupported"
    /** 别的设备 (名字 name) 重置 / 卸载了, 请本机也清空数据 (MECHANICS 9). */
    fun wipeRequested(wipeId: String, sender: String, name: String) {}
}

/** 在界面线程里执行, 等它做完 (同 devsync.MainRunner). post=null 时直接在当前线程执行 (测试用). */
class MainRunner(private val post: ((Runnable) -> Unit)? = null, private val isMain: () -> Boolean = { false }) {
    fun <T> call(fn: () -> T): T {
        if (post == null || isMain()) return fn()
        val latch = java.util.concurrent.CountDownLatch(1)
        var ok: T? = null
        var err: Throwable? = null
        post.invoke(Runnable {
            try { ok = fn() } catch (e: Throwable) { err = e } finally { latch.countDown() }
        })
        if (!latch.await(8, TimeUnit.SECONDS)) throw IllegalStateException("main thread busy")
        err?.let { throw it }
        @Suppress("UNCHECKED_CAST")
        return ok as T
    }
}

class PairRequest(
    val token: String, val id: String, val name: String, val platform: String,
    val addr: String, val port: Int, val key: ByteArray, val code: String, val mother: String,
) {
    @Volatile var state = "pending"
    val ts = System.currentTimeMillis()
}

class Outgoing(val addr: String, val port: Int) {
    @Volatile var state = "connecting"
    @Volatile var code = ""
    @Volatile var name = ""
    @Volatile var error = ""
}

class Found(val id: String, val name: String, val platform: String, val addr: String, val port: Int,
            val netMode: String, val ts: Long)

/**
 * 设备同步 (MECHANICS 15). 和桌面版 devsync.py 的协议完全一样, 可以互相绑定.
 */
/** 记住"哪台设备已经重置 / 卸载了"多久 (统计只保留本月和上月). */
private const val GONE_KEEP_MS = 62L * 86_400_000
/** 没有绑定、只是别的设备转交过来的设备, 这么久没有新消息就不再显示, 也不再算"离线" (它多半已经卸载了). 同 devsync.STALE_FWD_MS. */
private const val STALE_FWD_MS = 24L * 3_600_000

class SyncService(
    dir: File,
    private val local: SyncLocal,
    private val runner: MainRunner,
    private val platform: String = "android",
    defaultName: String = "Android",
    private val httpPort0: Int = HTTP_PORT,
    private val udpPort: Int = UDP_PORT,
) {
    companion object {
        /** 解除全部绑定要等这么久 (期间可以取消). */
        const val UNBIND_WAIT_MS = 24 * 3600 * 1000L
        /** 记住最近这么多个处理过的清空请求. */
        const val WIPES_KEEP = 50

        fun newWipeId(): String = "${System.currentTimeMillis()}-${SyncCrypto.hex(ByteArray(3).also { SecureRandom().nextBytes(it) })}"

        /** 重置后把留下的同步状态写回去 (数据目录刚被清空). */
        fun writeState(dir: File, state: JSONObject) {
            runCatching { dir.mkdirs(); File(dir, "sync_state.json").writeText(state.toString()) }
        }

        const val PROTO = 1
        const val HTTP_PORT = 17930
        const val UDP_PORT = 17931
        /** 母端每隔这么久向每台子端发一次同步请求. */
        const val SYNC_EVERY_MS = 10_000L
        /** 设备之间直接传的安装包最大多少; 发出的安装包多久之内可以来取. */
        const val UPDATE_MAX = 160L * 1024 * 1024
        const val OFFER_TTL_MS = 1_800_000L
        /** 这么久之内同步过的设备才算"在线" (比较最后操作时间用). */
        const val FRESH_MS = 120_000L
        /** 设备离线 (Timer 页显示"离线", 这段时间记为空闲, MECHANICS 8.4): 绑定的设备最后一次同步失败, 或者这么久没有它的消息.
         *  比同步间隔多留 5 秒 (同步本身要花时间, 正好 10 秒会每一轮都闪一下"离线"); 只是转交过来的设备再多等一轮. 同 devsync.OFFLINE_MS. */
        const val OFFLINE_MS = SYNC_EVERY_MS + 5_000L
        const val OFFLINE_FWD_MS = 2 * SYNC_EVERY_MS + 5_000L
        /** 关机 (MECHANICS 15.4): 一台设备最后发来的状态写着"收到关机指令" (off), 或者电量低于这么多 (%, 没在充电) 时突然离线,
         *  算作"关机": 效果和在线一样 (不算离线, 专注不记空闲, 合约不因它失败), 只是不同步. 同 devsync.LOW_BATTERY.
         *  前提是它开着开机自启动 (状态里 auto): 没开 / 开不了的照旧算"离线". */
        const val LOW_BATTERY = 2

        /** 这台设备最后发来的状态: 开着开机自启动 (auto), 并且收到了关机指令 / 电量低于 LOW_BATTERY%. 同 devsync.went_off. */
        fun wentOff(st: JSONObject?): Boolean {
            if (st == null || !st.optBoolean("auto")) return false
            if (st.optBoolean("off")) return true
            if (!st.has("battery") || st.isNull("battery")) return false
            val b = st.optDouble("battery", Double.NaN)
            return !b.isNaN() && b < LOW_BATTERY
        }
        const val PAIR_TIMEOUT_MS = 120_000L
        /** 设置里每台设备各自决定的项, 不同步. */
        val LOCAL_SETTINGS = setOf("NET_MODE", "FILTER_MODE", "FONT_SCALE", "CHIME_WAV",
            "WINDOW_LIMIT_ENABLED", "WINDOW_LIMIT", "WINDOW_FOCUS_ENABLED", "UPDATE_CHECK", "UPDATE_AUTO")

        /** 值的规范文字: 数字一律按 numText, 对象按键排序 (用来判断改没改过). */
        fun canon(v: Any?): String = when (v) {
            null, JSONObject.NULL -> "null"
            is Boolean -> v.toString()
            is Number -> numText(v.toDouble())
            is String -> JSONObject.quote(v)
            is JSONArray -> (0 until v.length()).joinToString(",", "[", "]") { canon(v.opt(it)) }
            is JSONObject -> v.keys().asSequence().sorted().joinToString(",", "{", "}") { "${JSONObject.quote(it)}:${canon(v.opt(it))}" }
            is Map<*, *> -> v.keys.map { it.toString() }.sorted().joinToString(",", "{", "}") { "${JSONObject.quote(it)}:${canon(v[it])}" }
            is Collection<*> -> v.joinToString(",", "[", "]") { canon(it) }
            else -> JSONObject.quote(v.toString())
        }

        fun hash(v: Any?): String =
            SyncCrypto.hex(MessageDigest.getInstance("SHA-256").digest(canon(v).toByteArray())).take(16)

        val H_NONE = hash(null)

        /** 其他设备今天的联网额度用量之和 (只算日期是今天的). */
        fun peerNet(ledgers: List<JSONObject>, date: String): DoubleArray {
            val out = DoubleArray(5)   // used, bonus, tomorrow_used, debt, gift
            for (l in ledgers) {
                val n = l.optJSONObject("net") ?: continue
                if (n.optString("date") != date) continue
                listOf("used", "bonus", "tomorrow_used", "debt", "gift").forEachIndexed { i, k -> out[i] += maxOf(0.0, n.optDouble(k, 0.0)) }
            }
            return out
        }
    }

    private val file = File(dir, "sync_state.json")
    private val lock = ReentrantLock()
    @Volatile var dead = false
    var port: Int = 0
        private set
    private val incoming = HashMap<String, PairRequest>()
    @Volatile var outgoing: Outgoing? = null
        private set
    val found = java.util.concurrent.ConcurrentHashMap<String, Found>()
    private var server: ServerSocket? = null
    private var udp: DatagramSocket? = null
    private val pool = Executors.newCachedThreadPool()
    private val wake = Object()
    @Volatile private var woken = false
    /** 正在和哪些设备同步 (同一台设备不同时跑两轮). */
    private val busy = HashSet<String>()
    /** 每台设备离线的时间段 {id: [[开始毫秒, 结束毫秒 (-1 = 还在离线)], …]}, 只在内存里 (MECHANICS 8.4). */
    private val offline = HashMap<String, MutableList<LongArray>>()
    /** 这次打开程序 (开始同步) 的时刻. */
    val startedMs = System.currentTimeMillis()
    private val state: JSONObject = load()

    init {
        if (state.optJSONObject("device") == null) {
            state.put("device", JSONObject().put("id", SyncCrypto.hex(ByteArray(6).also { SecureRandom().nextBytes(it) }))
                .put("name", defaultName))
            save()
        }
    }

    // ---- 存档 ----

    private fun load(): JSONObject {
        val d = try { JSONObject(file.readText()) } catch (e: Exception) { JSONObject() }
        for (k in listOf("peers", "track", "ledgers", "status", "gone", "wipe_out")) if (d.optJSONObject(k) == null) d.put(k, JSONObject())
        if (d.optJSONArray("wipes_seen") == null) d.put("wipes_seen", JSONArray())
        return d
    }

    private fun save() {
        if (dead) return
        lock.withLock {
            try {
                val tmp = File(file.path + ".tmp")
                tmp.writeText(state.toString())
                if (!tmp.renameTo(file)) { file.delete(); tmp.renameTo(file) }
            } catch (_: Exception) {
            }
        }
    }

    val myId: String get() = state.getJSONObject("device").getString("id")
    val myName: String get() = state.getJSONObject("device").optString("name", "Android")

    fun peers(): Map<String, JSONObject> = lock.withLock {
        val p = state.getJSONObject("peers")
        p.keys().asSequence().associateWith { JSONObject(p.getJSONObject(it).toString()) }
    }

    private fun proxyOk() = try { local.netMode() == "proxy" } catch (e: Exception) { false }

    fun deviceInfo(): JSONObject = JSONObject().put("id", myId).put("name", myName).put("platform", platform)
        .put("version", local.appVersion())
        .put("port", port).put("net_mode", local.netMode()).put("v", PROTO)

    private fun info(): JSONObject = runner.call { deviceInfo() }

    // ---- 启动 / 停止 ----

    fun start() {
        for (p in httpPort0 until httpPort0 + 10) {
            try {
                server = ServerSocket().apply { reuseAddress = true; bind(InetSocketAddress(p)) }
                port = p
                break
            } catch (_: Exception) {
            }
        }
        server?.let { s -> Thread({ acceptLoop(s) }, "oit-sync-http").apply { isDaemon = true }.start() }
        try {
            udp = DatagramSocket(null).apply {
                reuseAddress = true; broadcast = true; soTimeout = 1000; bind(InetSocketAddress(udpPort))
            }
            Thread({ udpLoop() }, "oit-sync-udp").apply { isDaemon = true }.start()
        } catch (_: Exception) {
            udp = null
        }
        Thread({ syncLoop() }, "oit-sync").apply { isDaemon = true }.start()
    }

    fun stop() {
        dead = true
        syncNow()
        runCatching { server?.close() }
        runCatching { udp?.close() }
        pool.shutdownNow()
    }

    // ---- 发现 ----

    private fun udpLoop() {
        val buf = ByteArray(4096)
        while (!dead) {
            val pk = DatagramPacket(buf, buf.size)
            try {
                udp!!.receive(pk)
            } catch (e: java.net.SocketTimeoutException) {
                continue
            } catch (e: Exception) {
                if (dead) return
                Thread.sleep(1000); continue
            }
            val msg = try { JSONObject(String(pk.data, 0, pk.length, Charsets.UTF_8)) } catch (e: Exception) { continue }
            if (msg.optString("id") == myId) continue
            when (msg.optString("oit")) {
                "discover" -> try {
                    val reply = info().put("oit", "here").toString().toByteArray()
                    udp!!.send(DatagramPacket(reply, reply.size, pk.address, pk.port))
                } catch (_: Exception) {
                }
                "here" -> noteFound(msg, pk.address.hostAddress ?: "")
            }
        }
    }

    fun noteFound(msg: JSONObject, ip: String) {
        val f = Found(msg.optString("id"), msg.optString("name", "?"), msg.optString("platform", "?"), ip,
            msg.optInt("port", HTTP_PORT), msg.optString("net_mode"), System.currentTimeMillis())
        if (f.id.isEmpty()) return
        found[f.id] = f
        lock.withLock {
            state.getJSONObject("peers").optJSONObject(f.id)?.let {
                if (it.optString("addr") != ip || it.optInt("port") != f.port) { it.put("addr", ip).put("port", f.port) }
            }
        }
    }

    private fun broadcastTargets(): Set<InetAddress> {
        val out = mutableSetOf(InetAddress.getByName("255.255.255.255"))
        try {
            for (ni in NetworkInterface.getNetworkInterfaces()) {
                if (!ni.isUp || ni.isLoopback) continue
                for (ia in ni.interfaceAddresses) ia.broadcast?.let { out.add(it) }
            }
        } catch (_: Exception) {
        }
        return out
    }

    /** 广播找一下同一局域网里的 OIT (阻塞 waitMs). */
    fun discover(waitMs: Long = 2500): List<Found> {
        val msg = JSONObject().put("oit", "discover").put("id", myId).put("v", PROTO).toString().toByteArray()
        DatagramSocket().use { s ->
            s.broadcast = true
            s.soTimeout = 300
            for (t in broadcastTargets()) runCatching { s.send(DatagramPacket(msg, msg.size, t, udpPort)) }
            val end = System.currentTimeMillis() + waitMs
            val buf = ByteArray(4096)
            while (System.currentTimeMillis() < end) {
                val pk = DatagramPacket(buf, buf.size)
                try { s.receive(pk) } catch (e: java.net.SocketTimeoutException) { continue } catch (e: Exception) { break }
                val m = try { JSONObject(String(pk.data, 0, pk.length, Charsets.UTF_8)) } catch (e: Exception) { continue }
                if (m.optString("oit") == "here" && m.optString("id") != myId) noteFound(m, pk.address.hostAddress ?: "")
            }
        }
        val now = System.currentTimeMillis()
        return found.values.filter { now - it.ts < 60_000 }
    }

    // ---- HTTP (只实现用得到的那一点) ----

    private fun readLine(i: InputStream): String {
        val sb = StringBuilder()
        while (true) {
            val c = i.read()
            if (c < 0 || c == '\n'.code) break
            if (c != '\r'.code) sb.append(c.toChar())
        }
        return sb.toString()
    }

    /** request=true: 读的是请求. 请求没有 Content-Length 时请求体长度是 0 —— 以前一律读到连接关闭,
     *  电脑发来的 GET (不带 Content-Length) 就卡到超时, 绑定时一直显示 timed out.
     *  响应没有 Content-Length 时才读到连接关闭. */
    private fun readMessage(i: InputStream, request: Boolean = false): Triple<String, Map<String, String>, ByteArray> {
        val first = readLine(i)
        val headers = HashMap<String, String>()
        while (true) {
            val l = readLine(i)
            if (l.isEmpty()) break
            val c = l.indexOf(':')
            if (c > 0) headers[l.substring(0, c).trim().lowercase()] = l.substring(c + 1).trim()
        }
        val n = headers["content-length"]?.toIntOrNull() ?: if (request) 0 else -1
        val body = if (n >= 0) {
            if (n > 8_000_000) throw IllegalStateException("too large")
            val b = ByteArray(n)
            var off = 0
            while (off < n) {
                val r = i.read(b, off, n - off)
                if (r < 0) break
                off += r
            }
            b
        } else i.readBytes()
        return Triple(first, headers, body)
    }

    private fun http(addr: String, port: Int, method: String, path: String, body: JSONObject?, timeoutMs: Int = 6000): Pair<Int, JSONObject> {
        Socket().use { s ->
            s.connect(InetSocketAddress(addr, port), timeoutMs)
            s.soTimeout = timeoutMs
            val data = body?.toString()?.toByteArray(Charsets.UTF_8) ?: ByteArray(0)
            val head = "$method $path HTTP/1.1\r\nHost: $addr:$port\r\nContent-Type: application/json\r\n" +
                "Content-Length: ${data.size}\r\nConnection: close\r\n\r\n"
            s.getOutputStream().apply { write(head.toByteArray()); write(data); flush() }
            val (status, _, raw) = readMessage(BufferedInputStream(s.getInputStream()))
            val code = status.split(" ").getOrNull(1)?.toIntOrNull() ?: 0
            val obj = try { if (raw.isEmpty()) JSONObject() else JSONObject(String(raw, Charsets.UTF_8)) } catch (e: Exception) { JSONObject() }
            return code to obj
        }
    }

    private fun acceptLoop(s: ServerSocket) {
        while (!dead) {
            val c = try { s.accept() } catch (e: Exception) { if (dead) return else continue }
            try {
                pool.execute { serve(c) }
            } catch (e: Exception) {
                runCatching { c.close() }
            }
        }
    }

    private fun serve(c: Socket) {
        c.use {
            try {
                c.soTimeout = 10_000
                val (first, _, raw) = readMessage(BufferedInputStream(c.getInputStream()), request = true)
                val parts = first.split(" ")
                val method = parts.getOrNull(0) ?: ""
                val path = parts.getOrNull(1) ?: ""
                val body = try { if (raw.isEmpty()) JSONObject() else JSONObject(String(raw, Charsets.UTF_8)) } catch (e: Exception) { JSONObject() }
                val ip = c.inetAddress.hostAddress ?: ""
                if (method == "GET" && path.startsWith("/oit/v1/file/")) {
                    serveFile(c, offerFile(path.substringAfterLast('/')))
                    return@use
                }
                val (code, out) = when {
                    method == "GET" && path == "/oit/v1/hello" -> 200 to info()
                    method == "GET" && path.startsWith("/oit/v1/pair/") -> pairStatus(path.substringAfterLast('/'))
                    method == "POST" && path == "/oit/v1/pair" -> onPair(body, ip)
                    method == "POST" && path == "/oit/v1/sync" -> onSync(body)
                    method == "POST" && path == "/oit/v1/unpair" -> onUnpair(body)
                    method == "POST" && path == "/oit/v1/wipe" -> onWipe(body)
                    method == "POST" && path == "/oit/v1/offer" -> onOffer(body)
                    method == "POST" && path == "/oit/v1/want" -> onWant(body)
                    else -> 404 to JSONObject().put("error", "not found")
                }
                val data = out.toString().toByteArray(Charsets.UTF_8)
                val reason = if (code == 200) "OK" else "Error"
                c.getOutputStream().apply {
                    write(("HTTP/1.1 $code $reason\r\nContent-Type: application/json\r\nContent-Length: ${data.size}\r\n" +
                        "Connection: close\r\n\r\n").toByteArray())
                    write(data)
                    flush()
                }
            } catch (_: Exception) {
            }
        }
    }

    // ---- 安装包直接发给其他设备 (MECHANICS 16) ----

    private val offers = java.util.concurrent.ConcurrentHashMap<String, Pair<File, Long>>()

    private fun offerFile(token: String): File? =
        offers[token]?.takeIf { it.second > System.currentTimeMillis() && it.first.isFile }?.first

    private fun serveFile(c: Socket, f: File?) {
        val out = c.getOutputStream()
        if (f == null) {
            val d = "{\"error\":\"not found\"}".toByteArray()
            out.write(("HTTP/1.1 404 Error\r\nContent-Type: application/json\r\nContent-Length: ${d.size}\r\n" +
                "Connection: close\r\n\r\n").toByteArray())
            out.write(d)
            out.flush()
            return
        }
        c.soTimeout = 60_000
        out.write(("HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nContent-Length: ${f.length()}\r\n" +
            "Connection: close\r\n\r\n").toByteArray())
        f.inputStream().use { it.copyTo(out, 65536) }
        out.flush()
    }

    /** 把安装包发给一台设备: 先发签名的说明 (平台, 版本, 大小, SHA-256, 取件码), 对方再来取文件, 取到后核对.
     *  返回 "sent", 或对方不要的原因. 在后台线程里调用. */
    fun offerUpdate(peerId: String, file: File, platform: String, version: String): String {
        val p = peers()[peerId] ?: return "unpaired"
        val md = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { inp ->
            val buf = ByteArray(65536)
            while (true) {
                val n = inp.read(buf)
                if (n < 0) break
                md.update(buf, 0, n)
            }
        }
        val token = SyncCrypto.hex(ByteArray(16).also { SecureRandom().nextBytes(it) })
        val now = System.currentTimeMillis()
        offers.entries.removeAll { it.value.second < now }
        offers[token] = file to (now + OFFER_TTL_MS)
        val env = SyncCrypto.seal(SyncCrypto.unhex(p.getString("key")), myId, JSONObject().put("offer", JSONObject()
            .put("platform", platform).put("version", version).put("sha256", SyncCrypto.hex(md.digest()))
            .put("size", file.length()).put("token", token).put("name", file.name)))
        val (st, reply) = http(p.getString("addr"), p.getInt("port"), "POST", "/oit/v1/offer", env, 10_000)
        if (st == 401) throw IllegalStateException("unpaired")
        if (st != 200) throw IllegalStateException(reply.optString("error", "HTTP $st"))
        return if (reply.optBoolean("ok")) "sent" else reply.optString("why", "declined")
    }

    /** 请一台绑定的设备给这台下载新版本并发过来 (它那里也许能连上 GitHub, 例如电脑开着代理).
     *  对方下载好后用 offer 发过来. 返回 "asked", 或对方不肯的原因 ("old" = 对方的 OIT 太旧, 不认这个请求). 在后台线程里调用. */
    fun requestUpdate(peerId: String): String {
        val p = peers()[peerId] ?: return "unpaired"
        val env = SyncCrypto.seal(SyncCrypto.unhex(p.getString("key")), myId, JSONObject().put("want", JSONObject()
            .put("platform", "android").put("version", local.appVersion())))
        val (st, reply) = http(p.getString("addr"), p.getInt("port"), "POST", "/oit/v1/want", env, 10_000)
        if (st == 404) return "old"
        if (st == 401) throw IllegalStateException("unpaired")
        if (st != 200) throw IllegalStateException(reply.optString("error", "HTTP $st"))
        return if (reply.optBoolean("ok")) "asked" else reply.optString("why", "declined")
    }

    private fun onWant(env: JSONObject): Pair<Int, JSONObject> {
        val id = env.optString("from")
        val p = peers()[id] ?: return 401 to JSONObject().put("error", "unpaired")
        val body = try { SyncCrypto.unseal(SyncCrypto.unhex(p.getString("key")), env) } catch (e: SealError) {
            return 403 to JSONObject().put("error", e.message)
        }
        body.optJSONObject("want") ?: return 400 to JSONObject().put("error", "bad-request")
        val why = try { runner.call { local.updateRequested(id) } } catch (e: Exception) {
            return 503 to JSONObject().put("error", e.message)
        }
        return 200 to if (why.isEmpty()) JSONObject().put("ok", true) else JSONObject().put("ok", false).put("why", why)
    }

    private fun onOffer(env: JSONObject): Pair<Int, JSONObject> {
        val bad = 400 to JSONObject().put("error", "bad-request")
        val p = peers()[env.optString("from")] ?: return 401 to JSONObject().put("error", "unpaired")
        val body = try { SyncCrypto.unseal(SyncCrypto.unhex(p.getString("key")), env) } catch (e: SealError) {
            return 403 to JSONObject().put("error", e.message)
        }
        val o = body.optJSONObject("offer") ?: return bad
        val size = o.optLong("size")
        if (size <= 0 || size > UPDATE_MAX || !Regex("[0-9a-f]{64}").matches(o.optString("sha256")) ||
            !Regex("[0-9a-f]{32}").matches(o.optString("token"))) return bad
        val why = try { runner.call { local.updateWanted(o.optString("platform"), o.optString("version")) } } catch (e: Exception) {
            return 503 to JSONObject().put("error", e.message)
        }
        if (why.isNotEmpty()) return 200 to JSONObject().put("ok", false).put("why", why)
        pool.execute { fetchUpdate(p, o) }
        return 200 to JSONObject().put("ok", true)
    }

    /** 后台线程: 到发来说明的那台设备上取安装包, 核对大小和 SHA-256 后交给本机安装. */
    private fun fetchUpdate(p: JSONObject, o: JSONObject) {
        var dest: File? = null
        try {
            val d = runner.call { local.updateDest(o.optString("version")) } ?: throw IllegalStateException("no place to save")
            dest = d
            val size = o.optLong("size")
            val md = MessageDigest.getInstance("SHA-256")
            var n = 0L
            Socket().use { s ->
                s.connect(InetSocketAddress(p.getString("addr"), p.getInt("port")), 6000)
                s.soTimeout = 30_000
                s.getOutputStream().apply {
                    write("GET /oit/v1/file/${o.optString("token")} HTTP/1.1\r\nHost: oit\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".toByteArray())
                    flush()
                }
                val inp = BufferedInputStream(s.getInputStream())
                val status = readLine(inp)
                while (readLine(inp).isNotEmpty()) { /* 跳过响应头 */ }
                if (status.split(" ").getOrNull(1) != "200") throw IllegalStateException(status)
                d.outputStream().use { out ->
                    val buf = ByteArray(65536)
                    while (true) {
                        val r = inp.read(buf)
                        if (r < 0) break
                        n += r
                        if (n > size) throw IllegalStateException("size mismatch")
                        md.update(buf, 0, r)
                        out.write(buf, 0, r)
                    }
                }
            }
            if (n != size || SyncCrypto.hex(md.digest()) != o.optString("sha256")) throw IllegalStateException("checksum mismatch")
        } catch (e: Exception) {
            dest?.delete()
            val err = e.message ?: e.javaClass.simpleName
            runCatching { runner.call { local.updateFailed(err) } }
            return
        }
        val got = dest ?: return
        runCatching { runner.call { local.updateReceived(got, o.optString("version"), p.optString("name", "?")) } }
    }

    fun hello(addr: String, port: Int = HTTP_PORT): JSONObject {
        val (st, d) = http(addr, port, "GET", "/oit/v1/hello", null, 3000)
        if (st != 200) throw IllegalStateException("HTTP $st")
        return d
    }

    // ---- 绑定: 发起方 ----

    fun startPairing(addr: String, port: Int, motherIsMe: Boolean) {
        val o = Outgoing(addr, port)
        outgoing = o
        pool.execute { pairWorker(o, motherIsMe) }
    }

    fun cancelPairing() {
        outgoing?.state = "cancelled"
    }

    private fun pairWorker(o: Outgoing, motherIsMe: Boolean) {
        try {
            if (!runner.call { proxyOk() }) throw IllegalStateException("not-proxy")
            val (x, pub) = SyncCrypto.keypair()
            val req = info().put("pub", pub.toString(16)).put("mother", if (motherIsMe) "me" else "you")
            val (st, d) = http(o.addr, o.port, "POST", "/oit/v1/pair", req)
            if (st != 200) throw IllegalStateException(d.optString("error", "HTTP $st"))
            val k = SyncCrypto.shared(x, d.getString("pub"))
            val token = d.getString("token")
            o.code = SyncCrypto.pairCode(k)
            o.name = d.optString("name", "?")
            o.state = "waiting"
            val deadline = System.currentTimeMillis() + PAIR_TIMEOUT_MS
            var misses = 0
            while (System.currentTimeMillis() < deadline && o.state == "waiting" && !dead) {
                Thread.sleep(1500)
                // Wi-Fi 偶尔丢一两个包: 等对方确认的这两分钟里, 连续几次连不上才算失败
                val r = try {
                    http(o.addr, o.port, "GET", "/oit/v1/pair/$token", null, 4000).second.also { misses = 0 }
                } catch (e: java.io.IOException) {
                    if (++misses >= 5) throw e
                    continue
                }
                when (r.optString("state")) {
                    "rejected" -> { o.state = "rejected"; return }
                    "accepted" -> {
                        if (r.optString("proof") != SyncCrypto.acceptProof(k, token)) throw IllegalStateException("bad proof")
                        val peerId = d.getString("id")
                        val mother = if (motherIsMe) myId else peerId
                        runner.call { addPeer(peerId, d.optString("name", "?"), d.optString("platform", "?"), o.addr, o.port, k, mother) }
                        o.state = "accepted"
                        if (mother != myId) initAfterAccept(peerId)
                        return
                    }
                }
            }
            if (o.state == "waiting") o.state = "timeout"
        } catch (e: Exception) {
            o.error = e.message ?: e.javaClass.simpleName
            o.state = "error"
        }
    }

    private fun addPeer(id: String, name: String, platform: String, addr: String, port: Int, k: ByteArray, mother: String) {
        lock.withLock {
            state.getJSONObject("gone").remove(id)          // 解除过又重新绑定
            state.getJSONObject("peers").put(id, JSONObject().put("name", name).put("platform", platform)
                .put("addr", addr).put("port", port).put("key", SyncCrypto.hex(k)).put("mother", mother)
                .put("paired_at", System.currentTimeMillis()).put("last_ok", 0).put("last_err", "")
                // 本机是子端: 用母端的内容初始化之前不参加普通同步
                .put("init", mother != myId))
        }
        save()
        local.changed()
    }

    // ---- 绑定: 被请求方 ----

    private fun onPair(body: JSONObject, ip: String): Pair<Int, JSONObject> {
        if (!runner.call { proxyOk() }) return 409 to JSONObject().put("error", "not-proxy")
        if (body.optString("net_mode") != "proxy") return 409 to JSONObject().put("error", "peer-not-proxy")
        val peerId = body.optString("id")
        val (x, pub) = SyncCrypto.keypair()
        val k = try { SyncCrypto.shared(x, body.getString("pub")) } catch (e: Exception) {
            return 400 to JSONObject().put("error", "bad request")
        }
        if (peerId.isEmpty()) return 400 to JSONObject().put("error", "bad request")
        val token = SyncCrypto.hex(ByteArray(8).also { SecureRandom().nextBytes(it) })
        val mother = if (body.optString("mother") == "me") peerId else myId
        val req = PairRequest(token, peerId, body.optString("name", "?"), body.optString("platform", "?"), ip,
            body.optInt("port", HTTP_PORT), k, SyncCrypto.pairCode(k), mother)
        synchronized(incoming) {
            incoming.entries.removeAll { System.currentTimeMillis() - it.value.ts > PAIR_TIMEOUT_MS + 30_000 }
            incoming[token] = req
        }
        runCatching { runner.call { local.pairRequest(req) } }
        return 200 to JSONObject().put("id", myId).put("name", myName).put("platform", platform)
            .put("pub", pub.toString(16)).put("token", token)
    }

    private fun pairStatus(token: String): Pair<Int, JSONObject> {
        val r = synchronized(incoming) { incoming[token] } ?: return 404 to JSONObject().put("state", "unknown")
        val out = JSONObject().put("state", r.state)
        if (r.state == "accepted") out.put("proof", SyncCrypto.acceptProof(r.key, token))
        return 200 to out
    }

    /** 界面线程: 用户在提示窗里接受 / 拒绝了绑定请求. */
    fun answerPairing(token: String, accept: Boolean): Boolean {
        val r = synchronized(incoming) { incoming[token] } ?: return false
        if (r.state != "pending") return false
        if (!accept) { r.state = "rejected"; return true }
        addPeer(r.id, r.name, r.platform, r.addr, r.port, r.key, r.mother)
        r.state = "accepted"
        // 本机是子端: 主动向母端要初始化的内容. 本机是母端: 等子端来要 —— 这时发起方 (子端) 可能还没
        // 查到"已接受", 马上同步过去只会得到 401 (以前会显示"同步失败: unpaired")
        if (r.mother != myId) pool.execute { initAfterAccept(r.id) }
        return true
    }

    /** 本机是子端, 刚接受了绑定: 向母端要初始化的内容.
     *  发起方要等下一次查询 (每 1.5 秒) 才知道已经接受, 在那之前它还不认识本机, 会回 401 ——
     *  以前只试一次, 失败后要等 30 秒的下一轮, 这期间一直显示"同步失败: unpaired". */
    private fun initAfterAccept(peerId: String) {
        repeat(12) {
            if (dead || peerId !in peers()) return
            if (trySync(peerId, init = true, quiet = true) == true) return
            Thread.sleep(1500)
        }
        trySync(peerId, init = true)             // 还不行就照常记下原因, 交给定时同步
    }

    // ---- 解除绑定 ----

    /** 解除绑定, 并删掉本机保留的对方的数据 (账, 状态). 一律记进 gone 并转告其他设备 (同 devsync.unpair):
     *  以前只有 forget (对方重置 / 卸载了) 时才记, 手动解除的设备 —— 例如没有先解除绑定就卸载了 OIT 的那台 ——
     *  它的状态会被其他设备 (或解除前已经发出的那一轮同步) 送回来, 之后一直显示"离线", 却不在绑定列表里, 再也解除不掉.
     *  forget 保留给旧的调用方, 现在和不带它一样. */
    @Suppress("UNUSED_PARAMETER")
    fun unpair(peerId: String, notify: Boolean = true, forget: Boolean = false) {
        val p = lock.withLock {
            state.getJSONObject("ledgers").remove(peerId)
            state.getJSONObject("status").remove(peerId)
            state.getJSONObject("gone").put(peerId, System.currentTimeMillis())
            state.getJSONObject("peers").remove(peerId) as? JSONObject
        }
        save()
        local.changed()
        if (p != null && notify) pool.execute {
            runCatching {
                val env = SyncCrypto.seal(SyncCrypto.unhex(p.getString("key")), myId, JSONObject().put("unpair", true))
                http(p.getString("addr"), p.getInt("port"), "POST", "/oit/v1/unpair", env, 3000)
            }
        }
    }

    private fun onUnpair(env: JSONObject): Pair<Int, JSONObject> {
        val from = env.optString("from")
        val p = peers()[from] ?: return 200 to JSONObject()
        val body = try { SyncCrypto.unseal(SyncCrypto.unhex(p.getString("key")), env) } catch (e: SealError) {
            return 403 to JSONObject().put("error", e.message)
        }
        runner.call { unpair(from, notify = false, forget = body.optBoolean("forget")) }
        return 200 to JSONObject()
    }

    // ---- 清空数据 (重置 / 卸载, MECHANICS 9, 15.1), 同 devsync ----

    /** 这个清空请求以前处理过吗; 没有就记下 (转告来转告去只处理一次). */
    fun sawWipe(wipeId: String): Boolean {
        lock.withLock {
            val seen = state.getJSONArray("wipes_seen")
            if ((0 until seen.length()).any { seen.optString(it) == wipeId }) return true
            seen.put(wipeId)
            while (seen.length() > WIPES_KEEP) seen.remove(0)
        }
        save()
        return false
    }

    /** 请一台设备清空数据 (unpair: 同时解除和本机的绑定). 返回 HTTP 状态 (连不上 0; 404 = 不认识清空请求的旧版本). */
    private fun wipeTo(p: JSONObject, wipeId: String, unpair: Boolean, timeoutMs: Int): Int = try {
        val env = SyncCrypto.seal(SyncCrypto.unhex(p.getString("key")), myId,
            JSONObject().put("wipe", wipeId).put("unpair", unpair))
        http(p.getString("addr"), p.getInt("port"), "POST", "/oit/v1/wipe", env, timeoutMs).first
    } catch (e: Exception) { 0 }

    /** 重置 (unpair = false): 请每台绑定的设备 (exclude 除外) 清空数据, 绑定不动; 连不上 / 旧版本的记进 wipe_out,
     *  下次连上时先送到, 送到之前不合并它的数据. 卸载 (unpair = true): 同时解除和本机的绑定; 旧版本退回旧的做法
     *  (只解除绑定, 删掉本机的数据). 在后台线程里调用, 最多等 timeoutMs. */
    fun sendWipe(wipeId: String, exclude: String? = null, unpair: Boolean = false, timeoutMs: Int = 3000) {
        sawWipe(wipeId)
        val all = peers().filterKeys { it != exclude }
        val ok = java.util.concurrent.ConcurrentHashMap<String, Boolean>()
        val threads = all.map { (id, p) ->
            Thread {
                var st = wipeTo(p, wipeId, unpair, timeoutMs)
                if (st == 404 && unpair) st = runCatching {
                    val env = SyncCrypto.seal(SyncCrypto.unhex(p.getString("key")), myId,
                        JSONObject().put("unpair", true).put("forget", true))
                    http(p.getString("addr"), p.getInt("port"), "POST", "/oit/v1/unpair", env, timeoutMs).first
                }.getOrDefault(0)
                ok[id] = st == 200
            }.apply { isDaemon = true; start() }
        }
        val end = System.currentTimeMillis() + timeoutMs
        for (t in threads) t.join(maxOf(1L, end - System.currentTimeMillis()))
        if (!unpair) {
            lock.withLock { for (id in all.keys) if (ok[id] != true) state.getJSONObject("wipe_out").put(id, wipeId) }
            save()
        }
    }

    /** 重置后留下的同步状态: 本机的 id, 绑定 (密钥, 地址), 已删除的设备, 没送到的清空请求, 解除绑定的计划.
     *  账, 状态, 修改时间全部清掉. wipedBy: 是哪台设备请本机清空的 (重开后提示). */
    fun keptState(wipedBy: String = ""): JSONObject = lock.withLock {
        val peers = JSONObject()
        val src = state.getJSONObject("peers")
        for (k in src.keys()) peers.put(k, JSONObject(src.getJSONObject(k).toString()).put("last_err", "").put("init", false))
        val d = JSONObject().put("device", JSONObject(state.getJSONObject("device").toString())).put("peers", peers)
            .put("track", JSONObject()).put("ledgers", JSONObject()).put("status", JSONObject())
            .put("gone", JSONObject(state.getJSONObject("gone").toString()))
            .put("wipe_out", JSONObject(state.getJSONObject("wipe_out").toString()))
            .put("wipes_seen", JSONArray(state.getJSONArray("wipes_seen").toString()))
        if (state.optLong("unbind_at") > 0) d.put("unbind_at", state.optLong("unbind_at"))
        if (wipedBy.isNotEmpty()) d.put("wiped_by", wipedBy)
        d
    }

    /** 重开后: 是哪台设备请本机清空了数据 (只提示一次). */
    fun popWipedBy(): String {
        val who = lock.withLock { (state.remove("wiped_by") as? String) ?: "" }
        if (who.isNotEmpty()) save()
        return who
    }

    /** 把没送到的清空请求送过去. true 送到了. */
    private fun deliverWipe(peerId: String, p: JSONObject): Boolean {
        val wid = lock.withLock { state.getJSONObject("wipe_out").optString(peerId) }
        if (wid.isEmpty()) return true
        if (wipeTo(p, wid, false, 4000) != 200) return false
        lock.withLock { state.getJSONObject("wipe_out").remove(peerId) }
        save()
        return true
    }

    private fun onWipe(env: JSONObject): Pair<Int, JSONObject> {
        val from = env.optString("from")
        val p = peers()[from] ?: return 401 to JSONObject().put("error", "unpaired")
        val body = try { SyncCrypto.unseal(SyncCrypto.unhex(p.getString("key")), env) } catch (e: SealError) {
            return 403 to JSONObject().put("error", e.message)
        }
        val wid = body.optString("wipe")
        if (wid.isEmpty()) return 400 to JSONObject().put("error", "bad-request")
        val name = p.optString("name").ifEmpty { "?" }
        if (body.optBoolean("unpair")) runner.call { unpair(from, notify = false) }
        lock.withLock { state.getJSONObject("wipe_out").remove(from) }   // 它自己也清空了
        if (!sawWipe(wid)) runner.call { local.wipeRequested(wid, from, name) }
        return 200 to JSONObject()
    }

    // ---- 解除全部绑定 (要等 24 小时, 期间可以取消) ----

    /** 计划解除全部绑定的时刻 (毫秒), 没有计划时 null. */
    val unbindAt: Long? get() = lock.withLock { state.optLong("unbind_at").takeIf { it > 0 } }

    fun requestUnbind() {
        lock.withLock { state.put("unbind_at", System.currentTimeMillis() + UNBIND_WAIT_MS) }
        save()
    }

    fun cancelUnbind() {
        lock.withLock { state.remove("unbind_at") }
        save()
    }

    /** 到时间了就解除和所有设备的绑定 (通知对方, 各自保留数据). 返回这次解除了没有. */
    fun checkUnbind(): Boolean {
        val at = unbindAt ?: return false
        if (System.currentTimeMillis() < at) return false
        lock.withLock { state.remove("unbind_at") }
        for (id in peers().keys) unpair(id, notify = true)
        save()
        return true
    }

    /** 收到关机指令 (MECHANICS 15.4): 和每台绑定的设备同步一轮 (子端平时不主动发), 让它们知道本机关机了.
     *  在后台线程里调用 (同步要用界面线程拼内容), 最多等 timeoutMs. */
    fun pushFinal(timeoutMs: Long = 3000) {
        val ids = peers().filterValues { !it.optBoolean("init") }.keys.toList()
        val threads = ids.map { id -> Thread { runCatching { syncWith(id) } }.apply { isDaemon = true; start() } }
        val end = System.currentTimeMillis() + timeoutMs
        for (t in threads) t.join(maxOf(1L, end - System.currentTimeMillis()))
    }

    /** 重置 / 卸载前: 让每台绑定的设备解除绑定, 并删掉它们保留的本机的数据 (账, 状态);
     *  它们再转告各自绑定的设备 (见 gone). 在后台线程里调用, 最多等 timeoutMs. */
    fun forgetMe(timeoutMs: Int = 3000) {
        val all = peers().values.toList()
        val threads = all.map { p ->
            Thread {
                runCatching {
                    val env = SyncCrypto.seal(SyncCrypto.unhex(p.getString("key")), myId,
                        JSONObject().put("unpair", true).put("forget", true))
                    http(p.getString("addr"), p.getInt("port"), "POST", "/oit/v1/unpair", env, timeoutMs)
                }
            }.apply { isDaemon = true; start() }
        }
        val end = System.currentTimeMillis() + timeoutMs
        for (t in threads) t.join(maxOf(1L, end - System.currentTimeMillis()))
    }

    // ---- 文档 ----

    private fun track(doc: String): JSONObject = state.getJSONObject("track").optJSONObject(doc)
        ?: JSONObject().also { state.getJSONObject("track").put(doc, it) }

    /** 本机文档 + 每一项的修改时间. 和上一次相比变了的项, 修改时间记为现在. */
    private fun stamp(localDocs: Map<String, Map<String, Any?>>): Map<String, Map<String, Pair<Long, Any?>>> {
        val now = System.currentTimeMillis()
        val out = HashMap<String, Map<String, Pair<Long, Any?>>>()
        for ((doc, kv) in localDocs) {
            val t = track(doc)
            for ((key, v) in kv) {
                val h = hash(v)
                val cur = t.optJSONArray(key)
                if (cur == null || cur.optString(0) != h) t.put(key, JSONArray().put(h).put(now))
            }
            for (key in t.keys().asSequence().toList()) {
                if (key !in kv && t.getJSONArray(key).optString(0) != H_NONE) t.put(key, JSONArray().put(H_NONE).put(now))
            }
            out[doc] = t.keys().asSequence().associateWith { t.getJSONArray(it).optLong(1) to kv[it] }
        }
        return out
    }

    private fun wins(doc: String, key: String, mt: Long, v: Any?, cur: Pair<Long, Any?>): Boolean {
        if (doc == "hours") {
            // 超时自动记录的内容永远让位给自己填写的内容 (行为模式按小时有不同的自动内容)
            val auto = local.autoTextFor(key)
            val rAuto = v == auto
            val lAuto = cur.second == auto
            val lHas = cur.second != null && cur.second.toString().isNotEmpty()
            val rHas = v != null && v.toString().isNotEmpty()
            if (rAuto && lHas && !lAuto) return false
            if (lAuto && rHas && !rAuto) return true
        }
        return mt > cur.first
    }

    private fun wire(v: Any?): Any = when (v) {
        null -> JSONObject.NULL
        is Map<*, *> -> JSONObject(v)
        is Collection<*> -> JSONArray(v)
        else -> v
    }

    /** 界面线程: 要发给别人的全部内容. */
    fun snapshot(): JSONObject {
        val own = local.ledger().put("rev", System.currentTimeMillis()).put("name", myName).put("platform", platform)
        val st = local.status().put("ts", System.currentTimeMillis())
        val docs = JSONObject()
        lock.withLock {
            for ((doc, kv) in stamp(local.docs())) {
                val o = JSONObject()
                for ((k, p) in kv) o.put(k, JSONArray().put(p.first).put(wire(p.second)))
                docs.put(doc, o)
            }
        }
        save()
        return lock.withLock {
            val ledgers = JSONObject(state.getJSONObject("ledgers").toString()).put(myId, own)
            val status = JSONObject(state.getJSONObject("status").toString()).put(myId, st)
            val gone = state.getJSONObject("gone")
            val cutoff = System.currentTimeMillis() - GONE_KEEP_MS
            for (k in gone.keys().asSequence().toList()) if (gone.optLong(k) < cutoff) gone.remove(k)
            JSONObject().put("v", PROTO).put("device", deviceInfo()).put("ledgers", ledgers).put("docs", docs).put("status", status)
                .put("gone", JSONObject(gone.toString())).put("dev", local.devMode())
        }
    }

    private fun unwire(v: Any?): Any? = if (v == null || v == JSONObject.NULL) null else v

    /** 界面线程: 并入别人发来的内容. init=true: 本机是子端, 第一次用母端的内容整体初始化. */
    fun merge(snap: JSONObject?, init: Boolean = false) {
        if (snap == null) return
        if (init) local.clearHistory()
        // 开发者模式: 子端跟着母端
        val sender = snap.optJSONObject("device")?.optString("id") ?: ""
        if (snap.has("dev") && sender.isNotEmpty() && sender != myId && sender == motherId()) local.setDevMode(snap.optBoolean("dev"))
        lock.withLock {
            val led = state.getJSONObject("ledgers")
            val gone = state.getJSONObject("gone")
            val paired = state.getJSONObject("peers")
            // 已经重置 / 卸载的设备: 删掉它的账和状态 (重新绑定过的不算)
            snap.optJSONObject("gone")?.let { g ->
                for (did in g.keys()) {
                    if (did == myId || paired.has(did)) continue
                    val ts = g.optLong(did)
                    if (ts > gone.optLong(did, 0)) gone.put(did, ts)
                    led.remove(did)
                    state.getJSONObject("status").remove(did)
                }
            }
            snap.optJSONObject("ledgers")?.let { r ->
                for (did in r.keys()) {
                    if (did == myId) continue
                    val l = r.optJSONObject(did) ?: continue
                    // 重置 / 卸载之前的旧账不再收
                    if (gone.has(did) && !paired.has(did) && l.optLong("rev") <= gone.optLong(did)) continue
                    if (l.optLong("rev") > (led.optJSONObject(did)?.optLong("rev", -1) ?: -1)) led.put(did, l)
                }
            }
            val sts = state.getJSONObject("status")
            val goneNow = state.getJSONObject("gone")
            val peersNow = state.getJSONObject("peers")
            snap.optJSONObject("status")?.let { r ->
                for (did in r.keys()) {
                    if (did == myId) continue
                    val s = r.optJSONObject(did) ?: continue
                    // 已经解除 / 重置 / 卸载的设备留下的旧状态
                    if (goneNow.has(did) && !peersNow.has(did) && s.optLong("ts") <= goneNow.optLong(did)) continue
                    if (s.optLong("ts") > (sts.optJSONObject(did)?.optLong("ts", -1) ?: -1)) sts.put(did, s)
                }
            }
        }
        val localDocs = local.docs()
        val stamped = lock.withLock { stamp(localDocs) }
        val now = System.currentTimeMillis()
        val remote = snap.optJSONObject("docs") ?: JSONObject()
        for (doc in remote.keys()) {
            if (doc !in localDocs) continue
            val entries = remote.optJSONObject(doc) ?: continue
            val mine = stamped[doc] ?: emptyMap()
            val changes = LinkedHashMap<String, Any?>()
            lock.withLock {
                val t = track(doc)
                for (key in entries.keys()) {
                    val e = entries.optJSONArray(key) ?: continue
                    val mt = e.optLong(0)
                    val v = unwire(e.opt(1))
                    val cur = mine[key]
                    if (init || cur == null || wins(doc, key, mt, v, cur)) {
                        if (cur == null || canon(cur.second) != canon(v)) changes[key] = v
                        t.put(key, JSONArray().put(hash(v)).put(mt))
                    }
                }
                if (init) for ((key, cur) in mine) {
                    if (!entries.has(key) && cur.second != null) {
                        changes[key] = null
                        t.put(key, JSONArray().put(H_NONE).put(now))
                    }
                }
            }
            if (changes.isNotEmpty()) runCatching { local.apply(doc, changes) }
        }
        save()
        local.changed()
    }

    // ---- 同步 ----

    fun syncWith(peerId: String, init: Boolean = false): Boolean = trySync(peerId, init) != false

    /** true 成功 / false 失败 / null 这次不用算 (正在同步, 或对方子端还在初始化).
     *  quiet: 失败了也不记原因 (绑定刚完成时的重试). */
    private fun trySync(peerId: String, init: Boolean = false, quiet: Boolean = false): Boolean? {
        val p = peers()[peerId] ?: return false
        val first = init || p.optBoolean("init")
        synchronized(busy) { if (!busy.add(peerId)) return null }
        try {
            return syncOnce(peerId, p, first, quiet)
        } finally {
            synchronized(busy) { busy.remove(peerId) }
        }
    }

    private fun syncOnce(peerId: String, p: JSONObject, init: Boolean, quiet: Boolean): Boolean? {
        val k = SyncCrypto.unhex(p.getString("key"))
        if (lock.withLock { state.getJSONObject("wipe_out").has(peerId) }) {
            // 本机重置过, 对方还没清空: 先送清空请求, 送到之前不合并它的数据
            if (deliverWipe(peerId, p)) return null
            if (!quiet) lock.withLock { state.getJSONObject("peers").optJSONObject(peerId)?.put("last_err", "wipe-pending") }
            return false
        }
        return try {
            val snap = runner.call {
                if (init) snapshot().put("docs", JSONObject()).put("ledgers", JSONObject()) else snapshot()
            }
            val env = SyncCrypto.seal(k, myId, JSONObject().put("snap", snap).put("init", init))
            val (st, reply) = http(p.getString("addr"), p.getInt("port"), "POST", "/oit/v1/sync", env, 10_000)
            if (st == 401) throw IllegalStateException("unpaired")
            // 对方是子端, 正在向本机要初始化的内容: 不是出错
            if (st == 409 && reply.optString("error") in setOf("initializing", "wipe-pending")) return null
            if (st != 200) throw IllegalStateException(reply.optString("error", "HTTP $st"))
            val body = SyncCrypto.unseal(k, reply)
            // 等回复的时候解除了绑定: 回复里的东西不要了 (否则对方的状态又被并回来, 成了解除不掉的"离线"设备)
            if (!peers().containsKey(peerId)) return false
            runner.call { merge(body.optJSONObject("snap"), init) }
            lock.withLock {
                state.getJSONObject("peers").optJSONObject(peerId)?.let { q ->
                    q.put("last_ok", System.currentTimeMillis()).put("last_err", "")
                    if (init) q.put("init", false)
                    body.optJSONObject("snap")?.optJSONObject("device")?.optString("name")?.takeIf { it.isNotEmpty() }
                        ?.let { q.put("name", it) }
                    // 对方的版本 (给它发更新用)
                    body.optJSONObject("snap")?.optJSONObject("device")?.optString("version")?.takeIf { it.isNotEmpty() }
                        ?.let { q.put("version", it) }
                }
            }
            save()
            true
        } catch (e: Exception) {
            if (!quiet) lock.withLock {
                state.getJSONObject("peers").optJSONObject(peerId)?.put("last_err", e.message ?: e.javaClass.simpleName)
            }
            false
        }
    }

    private fun onSync(env: JSONObject): Pair<Int, JSONObject> {
        if (!runner.call { proxyOk() }) return 409 to JSONObject().put("error", "not-proxy")
        val from = env.optString("from")
        // 请求体读不出来 (例如超过大小上限): 以前当成"没绑定"回 401, 对方会显示 unpaired
        if (from.isEmpty()) return 400 to JSONObject().put("error", "bad-request")
        val p = peers()[from] ?: return 401 to JSONObject().put("error", "unpaired")
        if (p.optBoolean("init")) return 409 to JSONObject().put("error", "initializing")
        // 先等本机把清空请求送过去, 不收它的旧数据
        if (lock.withLock { state.getJSONObject("wipe_out").has(from) }) return 409 to JSONObject().put("error", "wipe-pending")
        val k = SyncCrypto.unhex(p.getString("key"))
        val body = try { SyncCrypto.unseal(k, env) } catch (e: SealError) {
            return 403 to JSONObject().put("error", e.message)
        }
        val snap = body.optJSONObject("snap") ?: JSONObject()
        if (snap.optJSONObject("device")?.optString("net_mode") != "proxy") return 409 to JSONObject().put("error", "peer-not-proxy")
        val mine = try {
            runner.call { merge(snap); snapshot() }
        } catch (e: Exception) {
            return 503 to JSONObject().put("error", e.message)
        }
        lock.withLock {
            state.getJSONObject("peers").optJSONObject(from)?.put("last_ok", System.currentTimeMillis())?.put("last_err", "")
                ?.let { q -> snap.optJSONObject("device")?.optString("version")?.takeIf { it.isNotEmpty() }?.let { q.put("version", it) } }
        }
        return 200 to SyncCrypto.seal(k, myId, JSONObject().put("snap", mine))
    }

    @Volatile private var manual = false

    /** 立即同步一轮; 子端也会主动向母端发一次. */
    fun syncNow() {
        manual = true
        synchronized(wake) { woken = true; wake.notifyAll() }
    }

    private fun syncLoop() {
        var lastDiscover = 0L
        while (!dead) {
            synchronized(wake) {
                if (!woken) wake.wait(SYNC_EVERY_MS)
                woken = false
            }
            if (dead) return
            val wasManual = manual
            manual = false
            // 出了任何错都只跳过这一轮: 以前界面线程 8 秒没空 (OIT 在后台 / 熄屏时被系统放慢) 时
            // runner.call 抛出的 "main thread busy" 没人接, 同步线程就此退出, 直到重新打开 OIT 都不再同步
            try {
                if (!runner.call { proxyOk() }) continue
                // 母端向子端发请求; 子端只在还没初始化或按了"立即同步"时向母端发
                val wipeOut = lock.withLock { state.getJSONObject("wipe_out").keys().asSequence().toSet() }
                val targets = peers().filter { (id, p) -> p.optString("mother") == myId || p.optBoolean("init") || wasManual || id in wipeOut }.keys
                val failed = targets.filter { trySync(it) == false }
                // 有设备连不上: 地址可能变了, 广播找一下再试一次
                if (failed.isNotEmpty() && System.currentTimeMillis() - lastDiscover > 60_000) {
                    lastDiscover = System.currentTimeMillis()
                    runCatching { discover(1500) }
                    failed.forEach { trySync(it) }
                }
            } catch (e: InterruptedException) {
                if (dead) return
            } catch (_: Exception) {
            }
        }
    }

    // ---- 给本机其他部分用 ----

    fun peerLedgersById(): Map<String, JSONObject> = lock.withLock {
        val l = state.getJSONObject("ledgers")
        l.keys().asSequence().filter { it != myId }.mapNotNull { k -> l.optJSONObject(k)?.let { k to it } }.toMap()
    }

    /** 母端的设备 id: 本机是任何一次绑定的母端就是本机, 否则是绑定时选的那台. */
    fun motherId(): String? {
        val p = peers()
        if (p.values.any { it.optString("mother") == myId }) return myId
        return p.values.map { it.optString("mother") }.firstOrNull { it.isNotEmpty() }
    }

    fun peerLedgers(): List<JSONObject> = lock.withLock {
        val l = state.getJSONObject("ledgers")
        l.keys().asSequence().filter { it != myId }.mapNotNull { l.optJSONObject(it) }.toList()
    }

    /** 本机是不是最后有人操作的设备 (只比较最近 2 分钟内同步过的设备). 界面线程调用. */
    fun isLatestActive(): Boolean {
        val mine = try { local.status().optLong("last_op") } catch (e: Exception) { return true }
        val now = System.currentTimeMillis()
        return peerStatus().none { now - it.third.optLong("ts") < FRESH_MS && it.third.optLong("last_op") > mine }
    }

    /** 这台设备最近 2 分钟内同步过吗. */
    fun isFresh(id: String): Boolean {
        val now = System.currentTimeMillis()
        return peerStatus().any { it.first == id && now - it.third.optLong("ts") < FRESH_MS }
    }

    /** 其他设备此刻的状态: (设备 id, 名字, 状态). outdated: 版本比本机低, 算离线. */
    /** off: 关机 (连不上了, 但它最后的状态是收到关机指令 / 电量很低); 这时 online 也是 true (效果和在线一样). */
    class DeviceState(val id: String, val name: String, val online: Boolean, val status: JSONObject, val seen: Long,
                      val version: String = "", val outdated: Boolean = false, val off: Boolean = false)

    /** 绑定的设备里版本和本机不一样的 (知道版本的): (名字, 版本, 是否比本机新). */
    fun versionMismatch(): List<Triple<String, String, Boolean>> = lock.withLock {
        val mine = vparse(local.appVersion()) ?: return emptyList()
        val peers = state.getJSONObject("peers")
        peers.keys().asSequence().mapNotNull { k ->
            val p = peers.getJSONObject(k)
            val v = vparse(p.optString("version")) ?: return@mapNotNull null
            if (v == mine) null else Triple(p.optString("name", "?"), p.optString("version"), vnewer(v, mine))
        }.sortedBy { it.first }.toList()
    }

    /** "1.0.55" → [1, 0, 55]; 没有数字时 null (同 Updater.parseVersion). */
    private fun vparse(text: String?): List<Int>? {
        val nums = Regex("\\d+").findAll(text ?: "").map { it.value.toInt() }.take(3).toList()
        return if (nums.isEmpty()) null else nums + List(3 - nums.size) { 0 }
    }
    private fun vnewer(a: List<Int>, b: List<Int>): Boolean {
        for (i in 0 until 3) if (a[i] != b[i]) return a[i] > b[i]
        return false
    }

    /** 其他设备此刻的状态 (绑定的, 和转交过来的). online: 绑定的设备最后一次同步没有失败, 而且 OFFLINE_MS 之内有它的消息,
     *  版本也不比本机低;
     *  转交过来的设备 OFFLINE_FWD_MS 之内有消息. 不管它此刻在专注 / 休息 / 空闲, 同步失败就算离线. */
    fun deviceStates(): List<DeviceState> = lock.withLock {
        val now = System.currentTimeMillis()
        if (pruneForwarded(now)) save()
        val peers = state.getJSONObject("peers")
        val status = state.getJSONObject("status")
        val gone = state.getJSONObject("gone")
        val ledgers = state.getJSONObject("ledgers")
        val ids = (peers.keys().asSequence() + status.keys().asSequence()).toSet()
            .filter { it != myId && (peers.has(it) || !gone.has(it)) }
        val mine = vparse(local.appVersion())
        ids.map { id ->
            val st = status.optJSONObject(id) ?: JSONObject()
            val p = peers.optJSONObject(id)
            val name = p?.optString("name")?.takeIf { it.isNotEmpty() }
                ?: ledgers.optJSONObject(id)?.optString("name")?.takeIf { it.isNotEmpty() } ?: "?"
            if (p != null) {
                val seen = maxOf(p.optLong("last_ok"), st.optLong("ts"))
                val ver = p.optString("version")
                val old = mine != null && vparse(ver)?.let { vnewer(mine, it) } == true    // 版本比本机低: 算离线
                val online = p.optString("last_err").isEmpty() && now - seen < OFFLINE_MS && !old
                val off = !online && !old && wentOff(st)
                DeviceState(id, name, online || off, st, seen, ver, old, off)
            } else {
                val seen = st.optLong("ts")
                val online = now - seen < OFFLINE_FWD_MS
                val off = !online && wentOff(st)
                DeviceState(id, name, online || off, st, seen, off = off)
            }
        }.sortedBy { it.name }
    }

    /** 删掉没有绑定、也不可能再有人转交过来的设备的状态和账 (记进 gone, 免得又被送回来):
     *  本机一台绑定的设备都没有时全部删掉; 否则删掉 STALE_FWD_MS 之内没有新消息的. 返回删过没有. 同 devsync._prune_forwarded. */
    private fun pruneForwarded(now: Long): Boolean = lock.withLock {
        val peers = state.getJSONObject("peers")
        val status = state.getJSONObject("status")
        val ledgers = state.getJSONObject("ledgers")
        val gone = state.getJSONObject("gone")
        val drop = (status.keys().asSequence() + ledgers.keys().asSequence()).toSet().filter { did ->
            did != myId && !peers.has(did) &&
                (peers.length() == 0 || now - (status.optJSONObject(did)?.optLong("ts") ?: 0L) >= STALE_FWD_MS)
        }
        for (did in drop) {
            status.remove(did)
            ledgers.remove(did)
            gone.put(did, maxOf(gone.optLong(did), now))
        }
        drop.isNotEmpty()
    }

    /** 每隔几秒调用一次: 记下每台设备离线的时间段 (从最后一次有它的消息算起, 到重新有消息为止). */
    fun trackOffline() {
        val now = System.currentTimeMillis()
        val states = deviceStates()
        synchronized(offline) {
            for (d in states) {
                val iv = offline.getOrPut(d.id) { mutableListOf() }
                if (!d.online) {
                    if (iv.isEmpty() || iv.last()[1] >= 0) iv.add(longArrayOf(minOf(if (d.seen > 0) d.seen else startedMs, now), -1))
                } else if (iv.isNotEmpty() && iv.last()[1] < 0) {
                    iv.last()[1] = maxOf(iv.last()[0], d.seen)
                }
            }
            val floor = now - 26 * 3600_000L
            for (iv in offline.values) iv.removeAll { it[1] in 0..floor }
        }
    }

    /** [start, end] 里有任意一台设备离线的时间段 (合并过的). */
    fun offlineSpans(startMs: Long, endMs: Long): List<Pair<Long, Long>> {
        val now = System.currentTimeMillis()
        val spans = synchronized(offline) {
            offline.values.flatten().mapNotNull {
                val a = maxOf(it[0], startMs)
                val b = minOf(if (it[1] < 0) now else it[1], endMs)
                if (b > a) a to b else null
            }
        }.sortedBy { it.first }
        val out = mutableListOf<Pair<Long, Long>>()
        for ((a, b) in spans) {
            if (out.isNotEmpty() && a <= out.last().second) out[out.size - 1] = out.last().first to maxOf(out.last().second, b)
            else out.add(a to b)
        }
        return out
    }

    fun peerStatus(): List<Triple<String, String, JSONObject>> = lock.withLock {
        val s = state.getJSONObject("status")
        val l = state.getJSONObject("ledgers")
        s.keys().asSequence().filter { it != myId }.mapNotNull { id ->
            s.optJSONObject(id)?.let { Triple(id, l.optJSONObject(id)?.optString("name") ?: "?", it) }
        }.toList()
    }

    @Suppress("unused")
    private fun awaitPool() = pool.awaitTermination(1, TimeUnit.SECONDS)
}
