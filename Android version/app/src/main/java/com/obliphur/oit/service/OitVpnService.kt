package com.obliphur.oit.service

import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.IpPrefix
import android.net.NetworkCapabilities
import android.net.VpnService
import android.os.Build
import android.os.ParcelFileDescriptor
import android.os.SystemClock
import android.system.Os
import android.system.OsConstants
import android.system.StructPollfd
import com.obliphur.oit.OitApp
import com.obliphur.oit.core.VpnPlan
import java.io.FileInputStream
import java.io.FileOutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.time.LocalDateTime
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors

/** 拦截记录: 本次运行的放行 / 拦截次数和最近的拦截 (MECHANICS 4.6). 只在内存里. */
object BlockLog {
    class Event(val host: String, val app: String, var time: LocalDateTime, var count: Int, var mono: Long)

    @Volatile var allowed = 0L
    @Volatile var blocked = 0L
    val events = ArrayDeque<Event>()
    /** 本次运行查询过的域名和次数, 给 AI 建议用 (不写文件). */
    val domains = ConcurrentHashMap<String, Int>()

    @Synchronized
    fun add(host: String, app: String) {
        blocked++
        val now = SystemClock.elapsedRealtime()
        val last = events.firstOrNull()
        // 同一个网站加同一个应用在 2 秒内重复被拦, 合并为一条
        if (last != null && last.host == host && last.app == app && now - last.mono <= 2000) {
            last.count++; last.mono = now; last.time = LocalDateTime.now(); return
        }
        events.addFirst(Event(host, app, LocalDateTime.now(), 1, now))
        while (events.size > 200) events.removeLast()
    }

    fun seen(host: String) {
        val h = host.removePrefix("www.")
        if (domains.size < 3000 || domains.containsKey(h)) domains.merge(h, 1) { a, b -> a + b }
    }

    @Synchronized
    fun recent(n: Int) = events.take(n).toList()

    @Synchronized
    fun clear() {
        allowed = 0; blocked = 0; events.clear(); domains.clear()
    }
}

/**
 * 断网的实际执行者 (MECHANICS 14.2). 一个 VpnService, 按 [VpnPlan] 建立接口:
 *  - BlockAll: 默认路由进 VPN, 读出来的包直接丢掉 = 断网;
 *  - Dns: 只把一个假的 DNS 地址路由进来, 按网站名单回 NXDOMAIN 或转发给真正的 DNS.
 *  - Dns 带 blockApps (合约的无言为真, MECHANICS 18.6): 这些应用的网络全部屏蔽.
 *    全部放行时只把这些应用放进 VPN, 包一律丢掉 ([Mode.APPS]); 要按名单过滤时全部流量进 VPN,
 *    这些应用的丢掉, 其余的由 [Forwarder] 代发 ([Mode.FORWARD]).
 * 应用名单不在这里处理 (只管锁定应用, 见 Core.checkAppLock).
 * 换计划时先建好新接口再关旧的, 切换过程中不留空档.
 */
class OitVpnService : VpnService() {
    companion object {
        const val ACTION_APPLY = "com.obliphur.oit.VPN_APPLY"
        const val ACTION_STOP = "com.obliphur.oit.VPN_STOP"
        const val ACTION_REBUILD = "com.obliphur.oit.VPN_REBUILD"

        private const val V4_ADDR = "10.111.222.1"
        private const val V4_DNS = "10.111.222.2"
        private const val V6_ADDR = "fd00:111:222::1"
        private const val V6_DNS = "fd00:111:222::2"

        /** 0.0.0.0/0 去掉 10/8, 172.16/12, 192.168/16, 169.254/16, 127/8, 0/8 和 224/3 之后剩下的网段. */
        private val WAN_V4 = listOf(
            "1.0.0.0" to 8, "2.0.0.0" to 7, "4.0.0.0" to 6, "8.0.0.0" to 7, "11.0.0.0" to 8, "12.0.0.0" to 6,
            "16.0.0.0" to 4, "32.0.0.0" to 3, "64.0.0.0" to 3, "96.0.0.0" to 4, "112.0.0.0" to 5, "120.0.0.0" to 6,
            "124.0.0.0" to 7, "126.0.0.0" to 8, "128.0.0.0" to 3, "160.0.0.0" to 5, "168.0.0.0" to 8, "169.0.0.0" to 9,
            "169.128.0.0" to 10, "169.192.0.0" to 11, "169.224.0.0" to 12, "169.240.0.0" to 13, "169.248.0.0" to 14,
            "169.252.0.0" to 15, "169.255.0.0" to 16, "170.0.0.0" to 7, "172.0.0.0" to 12, "172.32.0.0" to 11,
            "172.64.0.0" to 10, "172.128.0.0" to 9, "173.0.0.0" to 8, "174.0.0.0" to 7, "176.0.0.0" to 4,
            "192.0.0.0" to 9, "192.128.0.0" to 11, "192.160.0.0" to 13, "192.169.0.0" to 16, "192.170.0.0" to 15,
            "192.172.0.0" to 14, "192.176.0.0" to 12, "192.192.0.0" to 10, "193.0.0.0" to 8, "194.0.0.0" to 7,
            "196.0.0.0" to 6, "200.0.0.0" to 5, "208.0.0.0" to 4,
        )

        @Volatile var instance: OitVpnService? = null
        @Volatile var plan: VpnPlan? = null
            private set
        /** 自检发现断网没有生效 (离线时 OIT 自己还能连上外网). */
        @Volatile var leak = false
            private set
        @Volatile var lastError: String? = null
            private set
        /** 正在屏蔽无言为真的应用的网络 (设置页显示). */
        @Volatile var appBlock = false
            private set

        fun apply(ctx: Context) {
            ctx.startService(Intent(ctx, OitVpnService::class.java).setAction(ACTION_APPLY))
        }

        /** 关掉现有的 VPN 再重新建立 (自检发现断网没有生效时). */
        fun rebuild(ctx: Context) {
            ctx.startService(Intent(ctx, OitVpnService::class.java).setAction(ACTION_REBUILD))
        }

        fun stop(ctx: Context) {
            if (instance != null) ctx.startService(Intent(ctx, OitVpnService::class.java).setAction(ACTION_STOP))
        }
    }

    /** 接口怎样建的: 只接管 DNS / 全部进 VPN 后丢掉 / 只有屏蔽的应用进 VPN 后丢掉 / 全部进 VPN, 屏蔽的应用丢掉, 其余转发. */
    private enum class Mode { DNS, DROP, APPS, FORWARD }

    private inner class Session(val pfd: ParcelFileDescriptor, val plan: VpnPlan, val mode: Mode,
                                val apps: Set<String>) {
        @Volatile var alive = true
        private val out = FileOutputStream(pfd.fileDescriptor)
        val thread = Thread({ loop() }, "oit-vpn").apply { isDaemon = true }

        private fun loop() {
            val input = FileInputStream(pfd.fileDescriptor)
            val buf = ByteArray(32767)
            val pfds = arrayOf(StructPollfd().apply { fd = pfd.fileDescriptor; events = OsConstants.POLLIN.toShort() })
            while (alive) {
                try {
                    pfds[0].revents = 0
                    if (Os.poll(pfds, 1000) <= 0) continue
                    val n = input.read(buf)
                    if (n <= 0) continue
                    when (mode) {
                        Mode.DNS -> handle(buf, n, plan as VpnPlan.Dns)
                        Mode.FORWARD -> {
                            val dns = Packets.parseUdp(buf, n)?.takeIf { it.dstPort == 53 }
                            if (dns != null) handle(buf, n, plan as VpnPlan.Dns)
                            else forwarder?.offer(buf.copyOf(n))
                        }
                        // BlockAll / 只有屏蔽的应用在 VPN 里: 丢掉
                        Mode.DROP, Mode.APPS -> {}
                    }
                } catch (e: Exception) {
                    if (!alive) break
                    SystemClock.sleep(50)
                }
            }
        }

        private fun handle(buf: ByteArray, n: Int, plan: VpnPlan.Dns) {
            val p = Packets.parseUdp(buf, n) ?: return
            if (p.dstPort != 53) return
            val host = Dns.qname(p.payload) ?: return
            BlockLog.seen(host)
            if (apps.isNotEmpty()) {
                // 无言为真: 屏蔽的应用查什么都回 NXDOMAIN
                val pkg = owner(p).firstOrNull { it in apps }
                if (pkg != null) {
                    BlockLog.add(host, pkg)
                    Dns.nxdomain(p.payload)?.let { write(Packets.buildReply(p, it)) }
                    return
                }
            }
            if (plan.filter) {
                val core = OitApp.instance.core
                // 全屏蔽: 除本机地址以外一律拦截; 否则按网站名单
                val ok = if (plan.blockAll) host == "localhost" || host.endsWith(".localhost")
                else core.rules.snap.decide(host, core.net.online, core.settings.str("FILTER_MODE")).first
                if (!ok) {
                    // 发起查询的应用只用来写拦截记录
                    BlockLog.add(host, owner(p).firstOrNull() ?: "?")
                    Dns.nxdomain(p.payload)?.let { write(Packets.buildReply(p, it)) }
                    return
                }
            }
            BlockLog.allowed++
            pool.execute { forward(p) }
        }

        fun write(b: ByteArray) {
            if (!alive) return
            synchronized(out) { runCatching { out.write(b) } }
        }

        private fun forward(p: UdpPacket) {
            for (server in upstream()) {
                try {
                    DatagramSocket().use { s ->
                        protect(s)
                        s.soTimeout = 5000
                        s.send(DatagramPacket(p.payload, p.payload.size, InetSocketAddress(server, 53)))
                        val rb = ByteArray(4096)
                        val r = DatagramPacket(rb, rb.size)
                        s.receive(r)
                        write(Packets.buildReply(p, rb.copyOf(r.length)))
                        return
                    }
                } catch (_: Exception) {
                }
            }
        }

        fun close() {
            alive = false
            runCatching { pfd.close() }
        }
    }

    private val pool = Executors.newFixedThreadPool(8)
    private var session: Session? = null
    /** 全部流量进 VPN 时的转发 (Mode.FORWARD). 换接口时留着, 已经建立的连接不断. */
    private var forwarder: Forwarder? = null

    // 发起 DNS 查询的应用: uid → 包名, 缓存 30 秒
    private val uidCache = ConcurrentHashMap<Int, Pair<Long, List<String>>>()
    private fun owner(p: UdpPacket): List<String> =
        owner(OsConstants.IPPROTO_UDP, InetSocketAddress(p.srcAddr, p.srcPort), InetSocketAddress(p.dstAddr, p.dstPort))

    private fun owner(proto: Int, src: InetSocketAddress, dst: InetSocketAddress): List<String> {
        val cm = getSystemService(ConnectivityManager::class.java)
        val uid = try {
            cm.getConnectionOwnerUid(proto, src, dst)
        } catch (e: Exception) {
            -1
        }
        if (uid < 0) return emptyList()
        val now = SystemClock.elapsedRealtime()
        uidCache[uid]?.let { if (now - it.first < 30_000) return it.second }
        val pkgs = packageManager.getPackagesForUid(uid)?.toList() ?: emptyList()
        uidCache[uid] = now to pkgs
        return pkgs
    }

    // 真正的 DNS: 底层网络 (Wi-Fi / 移动数据) 的 DNS, 缓存 10 秒; 拿不到就用公共 DNS
    private var upCache: Pair<Long, List<InetAddress>>? = null
    private fun upstream(): List<InetAddress> {
        val now = SystemClock.elapsedRealtime()
        upCache?.let { if (now - it.first < 10_000) return it.second }
        val cm = getSystemService(ConnectivityManager::class.java)
        val found = mutableListOf<InetAddress>()
        @Suppress("DEPRECATION")
        for (n in cm.allNetworks) {
            val caps = cm.getNetworkCapabilities(n) ?: continue
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) continue
            if (!caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)) continue
            cm.getLinkProperties(n)?.dnsServers?.let { found.addAll(it) }
        }
        found.addAll(listOf("223.5.5.5", "1.1.1.1", "8.8.8.8").map { InetAddress.getByName(it) })
        val list = found.distinct()
        upCache = now to list
        return list
    }

    /** 当前 Wi-Fi / 有线网络的网段不在 10/8, 172.16/12, 192.168/16 里时 (校园网的公网地址, 运营商级 NAT 100.64/10 …),
     *  WAN_V4 会把它也放进 VPN 丢掉, 同一网络里的设备同步就连不上. Android 13 起可以把这些网段单独排除. */
    private fun excludeLocalNets(b: Builder) {
        if (Build.VERSION.SDK_INT < 33) return
        val cm = getSystemService(ConnectivityManager::class.java) ?: return
        @Suppress("DEPRECATION")
        for (n in cm.allNetworks) {
            val caps = cm.getNetworkCapabilities(n) ?: continue
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) continue
            if (!caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) &&
                !caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) continue
            for (la in cm.getLinkProperties(n)?.linkAddresses ?: emptyList()) {
                val a = la.address as? Inet4Address ?: continue
                if (a.isSiteLocalAddress || a.isLinkLocalAddress || a.isLoopbackAddress) continue
                // 太大的网段 (/16 以上) 多半不是局域网, 不排除, 免得白名单模式离线时放走外网
                if (la.prefixLength < 16) continue
                runCatching { b.excludeRoute(IpPrefix(a, la.prefixLength)) }
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // stopSelf(startId): 之后又收到 APPLY (例如重置后马上重新接管) 时不会把新的 VPN 关掉
        if (intent?.action == ACTION_STOP) {
            teardown()
            stopSelf(startId)
            return START_NOT_STICKY
        }
        // ACTION_APPLY, 或系统 (始终开启的 VPN) 拉起的
        val app = OitApp.instance
        val want = app.core.vpnPlan()
        if (want == VpnPlan.None) {
            teardown(); stopSelf(startId); return START_NOT_STICKY
        }
        when {
            intent?.action == ACTION_REBUILD -> {
                teardown(); establish(want)
            }
            want != plan || session == null -> establish(want)
            else -> if (want !is VpnPlan.Dns) verifyBlocked(retry = !leak)
        }
        return START_STICKY
    }

    private fun blocking(p: VpnPlan?) = p is VpnPlan.BlockAll

    /** 要屏蔽的应用里装了的 (一个都没装时不另外处理: 空的允许列表等于所有应用都进 VPN). */
    private fun installed(apps: Set<String>): Set<String> = apps.filter {
        runCatching { packageManager.getApplicationInfo(it, 0) }.isSuccess
    }.toSet()

    private fun modeOf(p: VpnPlan, apps: Set<String>) = when {
        p is VpnPlan.BlockAll -> Mode.DROP
        apps.isEmpty() -> Mode.DNS
        p is VpnPlan.Dns && !p.filter -> Mode.APPS
        else -> Mode.FORWARD
    }

    private fun establish(want: VpnPlan, retry: Boolean = true) {
        val p = want
        val apps = if (p is VpnPlan.Dns) installed(p.blockApps) else emptySet()
        val mode = modeOf(p, apps)
        // 路由的样子变了 (例如"只接管 DNS"和"全部路由进 VPN"之间) 时, 先关掉旧接口再建新的.
        // 原地更新路由在部分系统上不生效, 会出现"断网方式是 Adapter 但离线时仍能上网".
        if (session != null && session?.mode != mode) {
            session?.close()
            session = null
            plan = null
        }
        val b = Builder().setSession("OIT").setMtu(1500).setBlocking(false)
        b.setMetered(false)
        b.addAddress(V4_ADDR, 32)
        b.addAddress(V6_ADDR, 128)
        when (p) {
            is VpnPlan.BlockAll -> {
                if (p.keepLan) {
                    // 除了局域网 / 链路本地 / 组播以外的全部地址 (Proxy 模式下设备同步还要用局域网)
                    for ((a, n) in WAN_V4) b.addRoute(a, n)
                    b.addRoute("2000::", 3)
                    excludeLocalNets(b)
                } else {
                    b.addRoute("0.0.0.0", 0); b.addRoute("::", 0)
                }
            }
            is VpnPlan.Dns -> when (mode) {
                Mode.APPS -> {
                    // 全部放行, 只有屏蔽的应用进 VPN: 它们的包全部丢掉 (局域网也不留)
                    for (a in apps) runCatching { b.addAllowedApplication(a) }
                    b.addRoute("0.0.0.0", 0); b.addRoute("::", 0)
                }
                Mode.FORWARD -> {
                    // 全部流量进 VPN (局域网除外, 设备同步要用): DNS 照常过滤, 屏蔽的应用丢掉, 其余转发. OIT 自己不进来
                    runCatching { b.addDisallowedApplication(packageName) }
                    b.addDnsServer(V4_DNS); b.addRoute(V4_DNS, 32)
                    b.addDnsServer(V6_DNS); b.addRoute(V6_DNS, 128)
                    for ((a, n) in WAN_V4) b.addRoute(a, n)
                    b.addRoute("2000::", 3)
                    excludeLocalNets(b)
                }
                else -> {
                    b.addDnsServer(V4_DNS); b.addRoute(V4_DNS, 32)
                    b.addDnsServer(V6_DNS); b.addRoute(V6_DNS, 128)
                }
            }
            VpnPlan.None -> return
        }
        val pfd = try {
            b.establish()
        } catch (e: Exception) {
            lastError = e.toString()
            null
        }
        if (pfd == null) {
            if (lastError == null) lastError = "establish() returned null"
            // 没有 VPN 权限 (被别的 VPN 应用占用或被撤销): 交给界面提示
            OitApp.instance.vpnFailed()
            return
        }
        lastError = null
        val old = session
        if (mode == Mode.FORWARD && forwarder == null) {
            forwarder = Forwarder(this, { session?.write(it) }) { proto, src, dst ->
                val ban = session?.apps ?: emptySet()
                owner(proto, src, dst).firstOrNull { it in ban }
            }
        } else if (mode != Mode.FORWARD) stopForwarder()
        session = Session(pfd, p, mode, apps).also { it.thread.start() }
        plan = p
        appBlock = mode == Mode.APPS || mode == Mode.FORWARD
        old?.close()
        leak = false
        OitApp.instance.vpnChanged()
        if (blocking(p)) verifyBlocked(retry)
    }

    /**
     * 自检: 全部断网时 OIT 自己也在 VPN 里, 直接连一个公共 DNS 的 53 端口应该连不上.
     * 连上了说明系统没有按 VPN 的路由走 —— 关掉重建一次, 还不行就在界面上标出来.
     */
    private fun verifyBlocked(retry: Boolean = false) {
        val p = plan as? VpnPlan.BlockAll ?: return
        Thread({
            SystemClock.sleep(1500)
            val leaked = listOf("223.5.5.5", "1.1.1.1").any { ip ->
                runCatching {
                    java.net.Socket().use { it.connect(InetSocketAddress(ip, 53), 2500) }
                    true
                }.getOrDefault(false)
            }
            android.os.Handler(mainLooper).post {
                if (plan != p) return@post
                if (leaked && retry) {
                    teardown(); establish(p, retry = false)
                } else if (leak != leaked) {
                    leak = leaked
                    if (leaked) lastError = "traffic is not going through the VPN"
                    OitApp.instance.vpnChanged()
                }
            }
        }, "oit-vpn-check").apply { isDaemon = true }.start()
    }

    private fun stopForwarder() {
        forwarder?.close()
        forwarder = null
    }

    private fun teardown() {
        leak = false
        appBlock = false
        stopForwarder()
        session?.close()
        session = null
        plan = null
        uidCache.clear()
        OitApp.instance.vpnChanged()
    }

    override fun onRevoke() {
        teardown()
        OitApp.instance.vpnFailed()
        stopSelf()
    }

    override fun onDestroy() {
        teardown()
        pool.shutdownNow()
        if (instance === this) instance = null
        super.onDestroy()
    }
}

/** 规则页显示用: 一个包名的应用名; 没装就原样返回. */
fun appLabel(ctx: Context, pkg: String): String = try {
    val pm = ctx.packageManager
    pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
} catch (e: Exception) {
    pkg
}
