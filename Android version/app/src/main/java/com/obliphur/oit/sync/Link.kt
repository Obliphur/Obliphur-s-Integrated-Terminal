package com.obliphur.oit.sync

import org.json.JSONObject
import java.io.File
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.Executors

/**
 * 联动程序 (MECHANICS 15.8, 同 linkd.py). OIT 主体被关掉 / 被系统结束 / 崩溃时, 别的设备的"请对方重新启动 OIT"请求没有人收.
 * 联动程序 (LinkService) 在单独的进程 (:link) 里常驻, 主体不在了它还在:
 *  - 监听 TCP 17940: 别的设备的联动程序发来"启动 OIT"的请求 (绑定时的密钥加密 + 签名, 和同步一样) ——
 *    OIT 已经在运行 / 本机正在外出 (link_state.json 的 hold_until) / 刚启动过 就不理, 否则启动 OIT;
 *  - 每 15 秒看一眼绑定的设备的 OIT 还在不在 (同步端口答不答 hello). 连续 45 秒没答就去问它的联动程序:
 *    答了 = 那台设备还开着, 只是 OIT 没在运行 (不是那台设备离线) → 请它启动 OIT; 也不答 = 那台设备真的关机 / 断网了, 什么都不做.
 * 只读 OIT 的 sync_state.json (绑定的地址和密钥) 和 link_state.json, 不碰 OitApp / Core (这个进程里没有它们).
 */
object LinkFiles {
    /** (本机 id, {设备 id: 绑定信息 (addr, port, key, name)}); 读不到时 ("" , {}). */
    fun loadPeers(dir: File): Pair<String, Map<String, JSONObject>> = try {
        val st = JSONObject(File(dir, "sync_state.json").readText())
        val peers = st.optJSONObject("peers") ?: JSONObject()
        (st.optJSONObject("device")?.optString("id") ?: "") to peers.keys().asSequence().associateWith { peers.getJSONObject(it) }
    } catch (e: Exception) { "" to emptyMap() }

    /** OIT 主体写下的"这个时刻之前别替我启动" (毫秒): 外出的计划结束时刻. 没有时 0. */
    fun holdUntil(dir: File): Long = try {
        JSONObject(File(dir, "link_state.json").readText()).optLong("hold_until")
    } catch (e: Exception) { 0L }

    fun writeHold(dir: File, untilMs: Long) {
        val f = File(dir, "link_state.json")
        if (f.exists() && holdUntil(dir) == untilMs) return
        try {
            dir.mkdirs()
            val tmp = File(dir, "link_state.json.tmp")
            tmp.writeText(JSONObject().put("hold_until", untilMs).put("ts", System.currentTimeMillis()).toString())
            if (!tmp.renameTo(f)) { f.delete(); tmp.renameTo(f) }
        } catch (_: Exception) {
        }
    }
}

class LinkCore(
    private val dir: File,
    val port: Int = LINK_PORT,
    private val syncPorts: List<Int> = (17930..17939).toList(),
    private val peerLinkPort: (String) -> Int = { LINK_PORT },
    private val launch: () -> Boolean,
    private val graceMs: Long = BOOT_GRACE_MS,
    private val log: (String) -> Unit = {},
) {
    companion object {
        const val LINK_PORT = 17940
        const val PROBE_EVERY_MS = 10_000L
        /** 比"拒绝连接算离线"的 25 秒 (SyncService.REFUSED_OFFLINE_MS) 长: 先让别的设备看到它离线, 再去启动. */
        const val FAIL_FOR_MS = 35_000L
        const val ASK_RETRY_MS = 30_000L
        const val START_COOLDOWN_MS = 120_000L
        const val BOOT_GRACE_MS = 180_000L

        /** 一次简单的 HTTP 请求 (对方回复后关连接). 返回 (状态, 内容); 连不上抛异常. */
        fun call(addr: String, port: Int, method: String, path: String, body: JSONObject?, timeoutMs: Int = 4000): Pair<Int, JSONObject> {
            Socket().use { s ->
                s.connect(InetSocketAddress(addr, port), timeoutMs)
                s.soTimeout = timeoutMs
                val data = body?.toString()?.toByteArray(Charsets.UTF_8) ?: ByteArray(0)
                val head = "$method $path HTTP/1.1\r\nHost: $addr:$port\r\nContent-Type: application/json\r\n" +
                    "Content-Length: ${data.size}\r\nConnection: close\r\n\r\n"
                s.getOutputStream().apply { write(head.toByteArray()); write(data); flush() }
                val raw = s.getInputStream().readBytes()
                val text = String(raw, Charsets.UTF_8)
                val code = text.substringBefore("\r\n").split(" ").getOrNull(1)?.toIntOrNull() ?: 0
                val payload = text.substringAfter("\r\n\r\n", "")
                val obj = try { if (payload.isEmpty()) JSONObject() else JSONObject(payload) } catch (e: Exception) { JSONObject() }
                return code to obj
            }
        }

        /** 本机的联动程序在不在运行. */
        fun running(port: Int = LINK_PORT): Boolean = try { call("127.0.0.1", port, "GET", "/oit/v1/link", null, 1500).first == 200 } catch (e: Exception) { false }
    }

    private val born = System.currentTimeMillis()
    @Volatile private var lastStart = 0L
    private val failSince = HashMap<String, Long>()
    private val asked = HashMap<String, LongArray>()        // 设备 id → [上次问的时刻, 它已经启动了 OIT (1)]
    private var server: ServerSocket? = null
    @Volatile private var dead = false
    private val pool = Executors.newCachedThreadPool()
    private var myId = ""

    fun oitAlive(): Boolean {
        val id = LinkFiles.loadPeers(dir).first
        for (p in syncPorts) {
            try {
                val (st, d) = call("127.0.0.1", p, "GET", "/oit/v1/hello", null, 2000)
                if (st == 200 && (id.isEmpty() || d.optString("id") == id)) return true
            } catch (_: Exception) {
            }
        }
        return false
    }

    /** 请求来了: 启动本机的 OIT. 返回 alive / hold / grace / cooldown / started / failed. */
    @Synchronized fun startOit(): String {
        if (oitAlive()) return "alive"
        if (LinkFiles.holdUntil(dir) > System.currentTimeMillis()) return "hold"
        if (System.currentTimeMillis() - born < graceMs) return "grace"
        if (System.currentTimeMillis() - lastStart < START_COOLDOWN_MS) return "cooldown"
        lastStart = System.currentTimeMillis()
        val ok = try { launch() } catch (e: Exception) { log("launch error ${e.javaClass.simpleName}: ${e.message}"); false }
        log("start OIT: " + if (ok) "ok" else "failed")
        return if (ok) "started" else "failed"
    }

    fun onStartRequest(env: JSONObject): Pair<Int, JSONObject> {
        val sender = env.optString("from")
        val p = LinkFiles.loadPeers(dir).second[sender] ?: return 401 to JSONObject().put("error", "unpaired")
        val body = try { SyncCrypto.unseal(SyncCrypto.unhex(p.getString("key")), env) } catch (e: SealError) {
            return 403 to JSONObject().put("error", e.message)
        }
        if (body.optString("link") != "start") return 400 to JSONObject().put("error", "bad-request")
        val res = startOit()
        log("start request from ${p.optString("name").ifEmpty { sender }}: $res")
        return 200 to JSONObject().put("result", res)
    }

    private fun peerOitAlive(p: JSONObject): Boolean = try {
        call(p.getString("addr"), p.getInt("port"), "GET", "/oit/v1/hello", null, 3000).first == 200
    } catch (e: Exception) { false }

    /** 去问 p 的联动程序: 请它启动 OIT. 返回它的回复, 联动程序也不答时 "". */
    fun askPeer(id: String, p: JSONObject): String = try {
        val env = SyncCrypto.seal(SyncCrypto.unhex(p.getString("key")), myId, JSONObject().put("link", "start"))
        val (st, d) = call(p.getString("addr"), peerLinkPort(id), "POST", "/oit/v1/link-start", env, 4000)
        d.optString("result").ifEmpty { "http-$st" }
    } catch (e: Exception) { "" }

    fun probeOnce() {
        val (id, peers) = LinkFiles.loadPeers(dir)
        myId = id
        val now = System.currentTimeMillis()
        failSince.keys.retainAll(peers.keys); asked.keys.retainAll(peers.keys)
        for ((pid, p) in peers) {
            if (p.optString("addr").isEmpty() || p.optInt("port") == 0 || p.optString("key").isEmpty()) continue
            if (peerOitAlive(p)) { failSince.remove(pid); asked.remove(pid); continue }
            val since = failSince.getOrPut(pid) { now }
            val a = asked.getOrPut(pid) { longArrayOf(0L, 0L) }
            if (now - since < FAIL_FOR_MS || a[1] == 1L || now - a[0] < ASK_RETRY_MS) continue
            a[0] = now
            val res = askPeer(pid, p)
            val name = p.optString("name").ifEmpty { pid }
            if (res.isEmpty()) log("$name: OIT not answering, link not answering either (device is down / unreachable)")
            else {
                log("$name: OIT not answering, asked its link to start OIT -> $res")
                if (res == "started") a[1] = 1L
            }
        }
    }

    /** 有绑定的设备吗 (没有时联动程序用不着, 不必保持唤醒). */
    fun hasPeers() = LinkFiles.loadPeers(dir).second.isNotEmpty()

    fun serve(): Boolean {
        val s = try { ServerSocket().apply { reuseAddress = false; bind(InetSocketAddress(port)) } } catch (e: Exception) { return false }
        server = s
        Thread({
            while (!dead) {
                val c = try { s.accept() } catch (e: Exception) { if (dead) return@Thread else continue }
                try { pool.execute { handle(c) } } catch (e: Exception) { runCatching { c.close() } }
            }
        }, "oit-link-http").apply { isDaemon = true }.start()
        return true
    }

    private fun handle(c: Socket) {
        c.use {
            try {
                c.soTimeout = 8000
                val input = c.getInputStream()
                val buf = java.io.ByteArrayOutputStream()
                var end = -1
                while (end < 0 && buf.size() < 16_384) {
                    val b = input.read(); if (b < 0) break
                    buf.write(b)
                    val a = buf.toByteArray()
                    if (a.size >= 4 && a[a.size - 4] == '\r'.code.toByte() && a[a.size - 3] == '\n'.code.toByte() &&
                        a[a.size - 2] == '\r'.code.toByte() && a[a.size - 1] == '\n'.code.toByte()) end = a.size
                }
                val head = String(buf.toByteArray(), Charsets.UTF_8)
                val first = head.substringBefore("\r\n").split(" ")
                val len = Regex("(?i)content-length:\\s*(\\d+)").find(head)?.groupValues?.get(1)?.toIntOrNull() ?: 0
                val body = if (len in 1..100_000) String(input.readNBytes(len), Charsets.UTF_8) else ""
                val obj = try { if (body.isEmpty()) JSONObject() else JSONObject(body) } catch (e: Exception) { JSONObject() }
                val method = first.getOrNull(0) ?: ""
                val path = first.getOrNull(1) ?: ""
                val (code, out) = when {
                    method == "GET" && path == "/oit/v1/link" -> 200 to JSONObject().put("link", 1)
                    method == "POST" && path == "/oit/v1/link-start" -> onStartRequest(obj)
                    else -> 404 to JSONObject().put("error", "not found")
                }
                val data = out.toString().toByteArray(Charsets.UTF_8)
                val resp = "HTTP/1.1 $code OK\r\nContent-Type: application/json\r\nContent-Length: ${data.size}\r\nConnection: close\r\n\r\n"
                c.getOutputStream().apply { write(resp.toByteArray()); write(data); flush() }
            } catch (_: Exception) {
            }
        }
    }

    fun stop() {
        dead = true
        runCatching { server?.close() }
        pool.shutdownNow()
    }
}
