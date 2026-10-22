package com.obliphur.oit.service

import android.net.VpnService
import android.os.SystemClock
import android.system.OsConstants
import java.net.InetAddress
import java.net.InetSocketAddress
import java.nio.ByteBuffer
import java.nio.channels.DatagramChannel
import java.nio.channels.SelectionKey
import java.nio.channels.Selector
import java.nio.channels.SocketChannel
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.atomic.AtomicInteger
import kotlin.random.Random

/**
 * 合约的无言为真 (MECHANICS 18.6): 全部流量进 VPN 时的用户态转发.
 * 社交 / AI 聊天应用 ([blocked] 认出来的) 发出的连接直接丢掉; 其余应用的 TCP / UDP 由这里用受保护的 socket
 * 代发 (不经过 VPN), 收到的数据再写回 VPN. DNS 不经过这里 (OitVpnService 自己按网站名单处理).
 *
 * TCP 只做最基本的一套: 不用窗口缩放 (窗口最大 64 KB), 按对方的窗口发, 1 秒没有确认就从没确认的地方重发.
 * 所有连接的状态只在 [thread] 里改: VPN 读到的包经 [inbox] 交过来.
 */
class Forwarder(
    private val vpn: VpnService,
    private val write: (ByteArray) -> Unit,
    /** 这个连接是不是名单里的应用发出的: 返回包名 (用来写拦截记录), 不是时 null. */
    private val blocked: (proto: Int, src: InetSocketAddress, dst: InetSocketAddress) -> String?,
) {
    private companion object {
        const val TCP = 6
        const val UDP = 17
        const val FIN = 0x01
        const val SYN = 0x02
        const val RST = 0x04
        const val PSH = 0x08
        const val ACK = 0x10

        const val CONNECTING = 0
        const val SYN_RCVD = 1
        const val ESTABLISHED = 2

        const val TO_APP_LIMIT = 256 * 1024       // 远端发来还没交给应用的数据, 超过就先不读
        const val TO_REMOTE_LIMIT = 256 * 1024    // 应用发来还没写给远端的数据, 超过就不收 (不确认, 应用会重发)
        const val RTO_MS = 1000L
        const val CONNECT_TIMEOUT_MS = 15_000L
        const val TCP_IDLE_MS = 10 * 60_000L
        const val HALF_CLOSED_MS = 2 * 60_000L
        const val UDP_IDLE_MS = 2 * 60_000L
        const val BLOCK_CACHE_MS = 60_000L
        const val MAX_TCP = 1024
        const val MAX_UDP = 512
        const val INBOX_LIMIT = 4096
    }

    /** 常见公共 DoH / DoT 解析器 (Firefox / Chrome / 社交应用写死用来绕过系统 DNS 的): 443 端口连它们一律丢掉. */
    private val dohResolvers: Set<InetAddress> = listOf(
        "1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9", "149.112.112.112",
        "208.67.222.222", "208.67.220.220", "94.140.14.14", "94.140.15.15",
        "2606:4700:4700::1111", "2606:4700:4700::1001", "2001:4860:4860::8888", "2001:4860:4860::8844",
        "2620:fe::fe", "2620:fe::9",
    ).mapNotNull { runCatching { InetAddress.getByName(it) }.getOrNull() }.toSet()

    /** 从 VPN 读到的一个 IP 包 (只看 TCP / UDP). */
    private class Ip(val b: ByteArray, val v6: Boolean, val src: ByteArray, val dst: ByteArray,
                     val proto: Int, val off: Int, val end: Int)

    /** 发来还没交给应用的数据: 第 0 个字节的序号是 sndUna. */
    private class Buf {
        var data = ByteArray(16 * 1024)
        var start = 0
        var end = 0
        val size get() = end - start

        fun append(src: ByteArray, n: Int) {
            if (end + n > data.size) {
                val keep = size
                val nd = if (keep + n > data.size) ByteArray(maxOf(data.size * 2, keep + n)) else data
                System.arraycopy(data, start, nd, 0, keep)      // 同一个数组里挪也没问题
                data = nd; start = 0; end = keep
            }
            System.arraycopy(src, 0, data, end, n)
            end += n
        }

        fun consume(n: Int) {
            start += minOf(n, size)
            if (start == end) { start = 0; end = 0 }
        }
    }

    private inner class Tcp(
        val key: String, val v6: Boolean,
        val appIp: ByteArray, val appPort: Int, val remIp: ByteArray, val remPort: Int,
    ) {
        var ch: SocketChannel? = null
        var sk: SelectionKey? = null
        var state = CONNECTING
        val iss = Random.nextInt()
        var sndUna = iss          // 最早没确认的序号
        var sndNxt = iss          // 下一个要发的序号
        var rcvNxt = 0            // 下一个想收到的应用的序号
        var appWnd = 0
        var mss = 536
        val toApp = Buf()
        val toRemote = ArrayDeque<ByteBuffer>()
        var toRemoteBytes = 0
        var lastWin = 65535
        var appFin = false        // 应用已经发了 FIN (已确认)
        var outShut = false
        var remoteEof = false
        var finSent = false
        var finSeq = 0
        var finAcked = false
        val born = SystemClock.elapsedRealtime()
        var lastActive = born
        var lastProgress = born
        var appFinAt = 0L
    }

    private inner class Udp(
        val key: String, val v6: Boolean,
        val appIp: ByteArray, val appPort: Int, val remIp: ByteArray, val remPort: Int,
        val ch: DatagramChannel,
    ) {
        var sk: SelectionKey? = null
        var lastActive = SystemClock.elapsedRealtime()
    }

    @Volatile private var alive = true
    private val selector: Selector = Selector.open()
    private val inbox = ConcurrentLinkedQueue<ByteArray>()
    private val inboxSize = AtomicInteger()
    private val tcp = HashMap<String, Tcp>()
    private val udp = HashMap<String, Udp>()
    private val dropped = HashMap<String, Long>()          // 名单里的应用的连接: 一段时间里不再查是谁发的
    private val readBuf = ByteBuffer.allocate(64 * 1024)
    private val bytes = ByteArray(64 * 1024)
    private var lastSweep = 0L

    val thread = Thread({ loop() }, "oit-forward").apply { isDaemon = true; start() }

    /** VPN 读到的非 DNS 包 (从 VPN 的线程调用). */
    fun offer(packet: ByteArray) {
        if (!alive) return
        if (inboxSize.get() >= INBOX_LIMIT) return
        inbox.add(packet)
        inboxSize.incrementAndGet()
        selector.wakeup()
    }

    fun close() {
        alive = false
        runCatching { selector.wakeup() }
    }

    // ---- 主循环 ----

    private fun loop() {
        try {
            while (alive) {
                selector.select(250)
                if (!alive) break
                while (true) {
                    val p = inbox.poll() ?: break
                    inboxSize.decrementAndGet()
                    runCatching { onPacket(p) }
                }
                val it = selector.selectedKeys().iterator()
                while (it.hasNext()) {
                    val k = it.next()
                    it.remove()
                    runCatching { onKey(k) }
                }
                val now = SystemClock.elapsedRealtime()
                if (now - lastSweep >= 250) {
                    lastSweep = now
                    runCatching { sweep(now) }
                }
            }
        } catch (_: Exception) {
        } finally {
            for (c in tcp.values.toList()) runCatching { closeTcp(c, rst = true) }
            for (u in udp.values.toList()) runCatching { closeUdp(u) }
            runCatching { selector.close() }
        }
    }

    private fun onKey(k: SelectionKey) {
        if (!k.isValid) return
        when (val a = k.attachment()) {
            is Tcp -> {
                if (k.isConnectable) onConnect(a)
                if (k.isValid && k.isReadable) onRemoteRead(a)
                if (k.isValid && k.isWritable) flushRemote(a)
            }
            is Udp -> if (k.isReadable) onUdpRead(a)
        }
    }

    private fun sweep(now: Long) {
        dropped.entries.removeIf { now - it.value > BLOCK_CACHE_MS }
        for (c in tcp.values.toList()) {
            when {
                c.state == CONNECTING && now - c.born > CONNECT_TIMEOUT_MS -> closeTcp(c, rst = true)
                c.state == SYN_RCVD && now - c.born > CONNECT_TIMEOUT_MS -> closeTcp(c, rst = true)
                now - c.lastActive > TCP_IDLE_MS -> closeTcp(c, rst = true)
                c.appFin && c.appFinAt > 0 && now - c.appFinAt > HALF_CLOSED_MS -> closeTcp(c, rst = true)
                c.state == SYN_RCVD && now - c.lastProgress > RTO_MS -> { c.lastProgress = now; sendSynAck(c) }
                c.state == ESTABLISHED && now - c.lastProgress > RTO_MS -> retransmit(c, now)
            }
        }
        for (u in udp.values.toList()) if (now - u.lastActive > UDP_IDLE_MS) closeUdp(u)
    }

    // ---- 解析 ----

    private fun u16(b: ByteArray, i: Int) = ((b[i].toInt() and 0xff) shl 8) or (b[i + 1].toInt() and 0xff)
    private fun u32(b: ByteArray, i: Int) = (u16(b, i) shl 16) or u16(b, i + 2)

    private fun parse(b: ByteArray): Ip? {
        if (b.size < 20) return null
        return when (b[0].toInt() ushr 4) {
            4 -> {
                val ihl = (b[0].toInt() and 0x0f) * 4
                if (ihl < 20 || b.size < ihl) return null
                if (u16(b, 6) and 0x3fff != 0) return null          // 分片的包不处理
                val end = minOf(b.size, u16(b, 2))
                Ip(b, false, b.copyOfRange(12, 16), b.copyOfRange(16, 20), b[9].toInt() and 0xff, ihl, end)
            }
            6 -> {
                if (b.size < 40) return null
                val end = minOf(b.size, 40 + u16(b, 4))
                Ip(b, true, b.copyOfRange(8, 24), b.copyOfRange(24, 40), b[6].toInt() and 0xff, 40, end)
            }
            else -> null
        }
    }

    private fun key(proto: Int, src: ByteArray, sport: Int, dst: ByteArray, dport: Int): String {
        val sb = StringBuilder(64).append(proto).append('|')
        for (x in src) sb.append(x.toInt() and 0xff).append('.')
        sb.append(sport).append('|')
        for (x in dst) sb.append(x.toInt() and 0xff).append('.')
        return sb.append(dport).toString()
    }

    private fun sockAddr(ip: ByteArray, port: Int) = InetSocketAddress(InetAddress.getByAddress(ip), port)

    /** 会绕过明文 DNS 过滤的加密 DNS / QUIC: DoT (853)、QUIC / DoH3 (UDP 443)、写死到公共 DoH 解析器 (443).
     *  全部丢掉, 逼应用回到明文 DNS (由 VPN 的 DNS 过滤处理), 否则社交平台用自带的加密 DNS 绕过, 屏蔽不干净. */
    private fun bypassesDns(proto: Int, dport: Int, dstIp: ByteArray): Boolean {
        if (dport == 853) return true                              // DoT
        if (proto == UDP && dport == 443) return true              // QUIC / HTTP3 (含 DoH3)
        if (dport == 443 && runCatching { InetAddress.getByAddress(dstIp) in dohResolvers }.getOrDefault(false)) return true
        return false
    }

    /** 新连接: 名单里的应用发的 / 加密 DNS 绕过的就丢掉 (结果缓存一段时间). */
    private fun isDropped(key: String, proto: Int, ip: Ip, sport: Int, dport: Int): Boolean {
        val now = SystemClock.elapsedRealtime()
        if (dropped.containsKey(key)) { dropped[key] = now; return true }
        if (bypassesDns(proto, dport, ip.dst)) { dropped[key] = now; return true }
        val pkg = runCatching {
            blocked(if (proto == TCP) OsConstants.IPPROTO_TCP else OsConstants.IPPROTO_UDP,
                sockAddr(ip.src, sport), sockAddr(ip.dst, dport))
        }.getOrNull() ?: return false
        dropped[key] = now
        BlockLog.add(InetAddress.getByAddress(ip.dst).hostAddress ?: "?", pkg)
        return true
    }

    private fun onPacket(b: ByteArray) {
        val ip = parse(b) ?: return
        when (ip.proto) {
            TCP -> onTcp(ip)
            UDP -> onUdp(ip)
            // 其余 (ICMP …) 丢掉
        }
    }

    // ---- TCP ----

    private fun onTcp(ip: Ip) {
        val b = ip.b
        val o = ip.off
        if (ip.end < o + 20) return
        val sport = u16(b, o)
        val dport = u16(b, o + 2)
        val seq = u32(b, o + 4)
        val ack = u32(b, o + 8)
        val doff = (b[o + 12].toInt() and 0xff ushr 4) * 4
        val flags = b[o + 13].toInt() and 0xff
        val wnd = u16(b, o + 14)
        if (doff < 20 || o + doff > ip.end) return
        val dataOff = o + doff
        val len = ip.end - dataOff
        val key = key(TCP, ip.src, sport, ip.dst, dport)
        var c = tcp[key]
        if (c == null) {
            if (flags and RST != 0) return
            if (flags and SYN == 0 || flags and ACK != 0) {
                // 不认识的连接 (例如 VPN 重建之前的): 回 RST, 应用不用干等
                rstFor(ip, sport, dport, seq, ack, flags, len)
                return
            }
            if (isDropped(key, TCP, ip, sport, dport)) return
            if (tcp.size >= MAX_TCP) { rstFor(ip, sport, dport, seq, ack, flags, len); return }
            c = Tcp(key, ip.v6, ip.src, sport, ip.dst, dport)
            c.rcvNxt = seq + 1
            c.appWnd = wnd
            c.mss = minOf(synMss(b, o + 20, dataOff) ?: 536, if (ip.v6) 1440 else 1460)
            tcp[key] = c
            openTcp(c)
            return
        }
        c.lastActive = SystemClock.elapsedRealtime()
        if (flags and RST != 0) { closeTcp(c, rst = false); return }
        if (flags and SYN != 0) {
            if (c.state == SYN_RCVD) sendSynAck(c)          // 应用重发了 SYN: 再回一次
            return
        }
        if (c.state == CONNECTING) return
        if (flags and ACK != 0) onAck(c, ack, wnd)
        if (len > 0 || flags and FIN != 0) {
            val ahead = seq - c.rcvNxt
            if (ahead > 0) {                                // 乱序: 重复确认, 等应用重发
                sendAck(c)
                return
            }
            val skip = -ahead                               // 已经收过的部分
            if (skip < len) {
                val n = len - skip
                if (!c.appFin && c.toRemoteBytes + n <= TO_REMOTE_LIMIT) {
                    c.toRemote.addLast(ByteBuffer.wrap(b.copyOfRange(dataOff + skip, dataOff + len)))
                    c.toRemoteBytes += n
                    c.rcvNxt += n
                }
            }
            if (flags and FIN != 0 && !c.appFin && seq + len == c.rcvNxt) {
                c.appFin = true
                c.appFinAt = SystemClock.elapsedRealtime()
                c.rcvNxt += 1
            }
            sendAck(c)
            flushRemote(c)
        }
        if (tcp[key] === c) {
            flushApp(c)
            maybeDone(c)
        }
    }

    private fun synMss(b: ByteArray, from: Int, to: Int): Int? {
        var i = from
        while (i < to) {
            val kind = b[i].toInt() and 0xff
            if (kind == 0) break
            if (kind == 1) { i++; continue }
            if (i + 1 >= to) break
            val l = b[i + 1].toInt() and 0xff
            if (l < 2) break
            if (kind == 2 && l == 4 && i + 3 < to) return u16(b, i + 2).takeIf { it >= 64 }
            i += l
        }
        return null
    }

    private fun openTcp(c: Tcp) {
        try {
            val ch = SocketChannel.open()
            c.ch = ch
            ch.configureBlocking(false)
            vpn.protect(ch.socket())
            if (ch.connect(sockAddr(c.remIp, c.remPort))) {
                c.sk = ch.register(selector, 0, c)
                connected(c)
            } else {
                c.sk = ch.register(selector, SelectionKey.OP_CONNECT, c)
            }
        } catch (_: Exception) {
            refuse(c)
        }
    }

    private fun onConnect(c: Tcp) {
        try {
            if (c.ch?.finishConnect() == true) connected(c)
        } catch (_: Exception) {
            refuse(c)
        }
    }

    private fun connected(c: Tcp) {
        c.state = SYN_RCVD
        c.sndUna = c.iss
        c.sndNxt = c.iss + 1
        c.lastProgress = SystemClock.elapsedRealtime()
        sendSynAck(c)
        interest(c)
    }

    /** 连不上远端: 回 RST, 应用马上知道. */
    private fun refuse(c: Tcp) {
        sendTcp(c, 0, c.rcvNxt, RST or ACK, 0)
        dropTcp(c)
    }

    private fun onAck(c: Tcp, ack: Int, wnd: Int) {
        c.appWnd = wnd
        val acked = ack - c.sndUna
        val flight = c.sndNxt - c.sndUna
        if (acked <= 0 || acked > flight) return
        var data = acked
        if (c.state == SYN_RCVD) { c.state = ESTABLISHED; data -= 1 }
        if (c.finSent && ack == c.finSeq + 1) { c.finAcked = true; data -= 1 }
        if (data > 0) c.toApp.consume(data)
        c.sndUna = ack
        c.lastProgress = SystemClock.elapsedRealtime()
    }

    /** 1 秒没有确认: 从最早没确认的地方重发 (窗口是 0 时只发一个确认, 让应用回窗口). */
    private fun retransmit(c: Tcp, now: Long) {
        c.lastProgress = now
        if (c.sndNxt == c.sndUna) {
            if (c.toApp.size > 0 && c.appWnd == 0) sendAck(c)
            return
        }
        c.sndNxt = c.sndUna
        if (c.finSent && !c.finAcked) c.finSent = false
        flushApp(c)
    }

    private fun onRemoteRead(c: Tcp) {
        val ch = c.ch ?: return
        val space = TO_APP_LIMIT - c.toApp.size
        if (space <= 0) { interest(c); return }
        readBuf.clear()
        readBuf.limit(minOf(space, readBuf.capacity()))
        val n = try {
            ch.read(readBuf)
        } catch (_: Exception) {
            closeTcp(c, rst = true); return
        }
        if (n < 0) c.remoteEof = true
        else if (n > 0) {
            readBuf.flip()
            readBuf.get(bytes, 0, n)
            c.toApp.append(bytes, n)
            c.lastActive = SystemClock.elapsedRealtime()
        }
        flushApp(c)
        maybeDone(c)
    }

    /** 把远端的数据按应用的窗口发给应用; 远端关了并且都发完时发 FIN. */
    private fun flushApp(c: Tcp) {
        if (c.state != ESTABLISHED || tcp[c.key] !== c) return
        if (c.sndNxt == c.sndUna) c.lastProgress = SystemClock.elapsedRealtime()   // 重发计时从这次发出算起
        while (!c.finSent) {
            val sent = c.sndNxt - c.sndUna
            val avail = c.toApp.size - sent
            val room = c.appWnd - sent
            if (avail <= 0 || room <= 0) break
            val n = minOf(avail, room, c.mss)
            sendTcp(c, c.sndNxt, c.rcvNxt, ACK or PSH, n, c.toApp.data, c.toApp.start + sent)
            c.sndNxt += n
        }
        if (c.remoteEof && !c.finSent && c.toApp.size == c.sndNxt - c.sndUna) {
            c.finSeq = c.sndNxt
            sendTcp(c, c.sndNxt, c.rcvNxt, FIN or ACK, 0)
            c.sndNxt += 1
            c.finSent = true
        }
        interest(c)
    }

    /** 把应用的数据写给远端; 应用关了并且都写完时关掉远端的写方向. */
    private fun flushRemote(c: Tcp) {
        val ch = c.ch ?: return
        if (c.state == CONNECTING) return
        try {
            while (c.toRemote.isNotEmpty()) {
                val bb = c.toRemote.first()
                val n = ch.write(bb)
                c.toRemoteBytes -= n
                if (bb.hasRemaining()) break
                c.toRemote.removeFirst()
            }
            if (c.toRemote.isEmpty() && c.appFin && !c.outShut) {
                c.outShut = true
                ch.shutdownOutput()
            }
        } catch (_: Exception) {
            closeTcp(c, rst = true); return
        }
        // 窗口刚才很小, 现在腾出来了: 告诉应用
        if (c.lastWin < 2 * c.mss && window(c) >= 2 * c.mss) sendAck(c)
        interest(c)
    }

    private fun maybeDone(c: Tcp) {
        if (tcp[c.key] !== c) return
        if (c.appFin && c.finSent && c.finAcked && c.toRemote.isEmpty()) closeTcp(c, rst = false)
    }

    private fun interest(c: Tcp) {
        val k = c.sk ?: return
        if (!k.isValid) return
        var ops = 0
        if (c.state != CONNECTING) {
            if (!c.remoteEof && c.toApp.size < TO_APP_LIMIT) ops = ops or SelectionKey.OP_READ
            if (c.toRemote.isNotEmpty()) ops = ops or SelectionKey.OP_WRITE
        } else ops = SelectionKey.OP_CONNECT
        if (k.interestOps() != ops) k.interestOps(ops)
    }

    private fun window(c: Tcp) = (TO_REMOTE_LIMIT - c.toRemoteBytes).coerceIn(0, 65535)

    private fun sendSynAck(c: Tcp) {
        sendTcp(c, c.iss, c.rcvNxt, SYN or ACK, 0, mss = if (c.v6) 1440 else 1460)
    }

    private fun sendAck(c: Tcp) = sendTcp(c, c.sndNxt, c.rcvNxt, ACK, 0)

    private fun sendTcp(c: Tcp, seq: Int, ack: Int, flags: Int, len: Int,
                        data: ByteArray? = null, off: Int = 0, mss: Int = 0) {
        val w = window(c)
        c.lastWin = w
        write(tcpPacket(c.v6, c.remIp, c.appIp, c.remPort, c.appPort, seq, ack, flags, w, data, off, len, mss))
    }

    /** 不认识的 TCP 包: 按 RFC 793 回 RST. */
    private fun rstFor(ip: Ip, sport: Int, dport: Int, seq: Int, ack: Int, flags: Int, len: Int) {
        val pkt = if (flags and ACK != 0)
            tcpPacket(ip.v6, ip.dst, ip.src, dport, sport, ack, 0, RST, 0, null, 0, 0, 0)
        else {
            val segLen = len + (if (flags and SYN != 0) 1 else 0) + (if (flags and FIN != 0) 1 else 0)
            tcpPacket(ip.v6, ip.dst, ip.src, dport, sport, 0, seq + segLen, RST or ACK, 0, null, 0, 0, 0)
        }
        write(pkt)
    }

    private fun closeTcp(c: Tcp, rst: Boolean) {
        if (rst && c.state != CONNECTING) sendTcp(c, c.sndNxt, c.rcvNxt, RST or ACK, 0)
        else if (rst) sendTcp(c, 0, c.rcvNxt, RST or ACK, 0)
        dropTcp(c)
    }

    private fun dropTcp(c: Tcp) {
        runCatching { c.sk?.cancel() }
        runCatching { c.ch?.close() }
        if (tcp[c.key] === c) tcp.remove(c.key)
    }

    // ---- UDP ----

    private fun onUdp(ip: Ip) {
        val b = ip.b
        val o = ip.off
        if (ip.end < o + 8) return
        val sport = u16(b, o)
        val dport = u16(b, o + 2)
        val end = minOf(ip.end, o + u16(b, o + 4)).let { if (it < o + 8) ip.end else it }
        val key = key(UDP, ip.src, sport, ip.dst, dport)
        var u = udp[key]
        if (u == null) {
            if (isDropped(key, UDP, ip, sport, dport)) return
            if (udp.size >= MAX_UDP) udp.values.minByOrNull { it.lastActive }?.let { closeUdp(it) }
            val ch = try {
                DatagramChannel.open().apply {
                    configureBlocking(false)
                    vpn.protect(socket())
                    connect(sockAddr(ip.dst, dport))
                }
            } catch (_: Exception) {
                return
            }
            u = Udp(key, ip.v6, ip.src, sport, ip.dst, dport, ch)
            u.sk = runCatching { ch.register(selector, SelectionKey.OP_READ, u) }.getOrNull() ?: run {
                runCatching { ch.close() }; return
            }
            udp[key] = u
        }
        u.lastActive = SystemClock.elapsedRealtime()
        runCatching { u.ch.write(ByteBuffer.wrap(b, o + 8, end - o - 8)) }
    }

    private fun onUdpRead(u: Udp) {
        val q = UdpPacket(u.v6, u.appIp, u.remIp, u.appPort, u.remPort, ByteArray(0))
        while (true) {
            readBuf.clear()
            val n = try {
                u.ch.read(readBuf)
            } catch (_: Exception) {
                closeUdp(u); return
            }
            if (n <= 0) break
            readBuf.flip()
            val data = ByteArray(n)
            readBuf.get(data)
            write(Packets.buildReply(q, data))
            u.lastActive = SystemClock.elapsedRealtime()
        }
    }

    private fun closeUdp(u: Udp) {
        runCatching { u.sk?.cancel() }
        runCatching { u.ch.close() }
        if (udp[u.key] === u) udp.remove(u.key)
    }

    // ---- 拼包 ----

    private fun put16(b: ByteArray, i: Int, v: Int) {
        b[i] = (v ushr 8).toByte(); b[i + 1] = v.toByte()
    }

    private fun put32(b: ByteArray, i: Int, v: Int) {
        put16(b, i, v ushr 16); put16(b, i + 2, v)
    }

    private fun sum(data: ByteArray, off: Int, len: Int, start: Long = 0): Long {
        var s = start
        var i = off
        while (i + 1 < off + len) {
            s += u16(data, i).toLong(); i += 2
        }
        if (i < off + len) s += (data[i].toLong() and 0xff) shl 8
        return s
    }

    private fun fold(s0: Long): Int {
        var s = s0
        while (s ushr 16 != 0L) s = (s and 0xffff) + (s ushr 16)
        return (s.inv() and 0xffff).toInt()
    }

    private fun tcpPacket(v6: Boolean, src: ByteArray, dst: ByteArray, sport: Int, dport: Int,
                          seq: Int, ack: Int, flags: Int, win: Int,
                          data: ByteArray?, off: Int, len: Int, mss: Int): ByteArray {
        val opt = if (mss > 0) 4 else 0
        val tcpLen = 20 + opt + len
        val hdr = if (v6) 40 else 20
        val out = ByteArray(hdr + tcpLen)
        if (v6) {
            out[0] = 0x60
            put16(out, 4, tcpLen)
            out[6] = TCP.toByte()
            out[7] = 64
            System.arraycopy(src, 0, out, 8, 16)
            System.arraycopy(dst, 0, out, 24, 16)
        } else {
            out[0] = 0x45
            put16(out, 2, out.size)
            put16(out, 6, 0x4000)
            out[8] = 64
            out[9] = TCP.toByte()
            System.arraycopy(src, 0, out, 12, 4)
            System.arraycopy(dst, 0, out, 16, 4)
            put16(out, 10, fold(sum(out, 0, 20)))
        }
        put16(out, hdr, sport)
        put16(out, hdr + 2, dport)
        put32(out, hdr + 4, seq)
        put32(out, hdr + 8, if (flags and ACK != 0) ack else 0)
        out[hdr + 12] = (((20 + opt) / 4) shl 4).toByte()
        out[hdr + 13] = flags.toByte()
        put16(out, hdr + 14, win)
        if (opt > 0) {
            out[hdr + 20] = 2; out[hdr + 21] = 4; put16(out, hdr + 22, mss)
        }
        if (len > 0 && data != null) System.arraycopy(data, off, out, hdr + 20 + opt, len)
        val ps = sum(src, 0, src.size) + sum(dst, 0, dst.size) + TCP + tcpLen
        put16(out, hdr + 16, fold(sum(out, hdr, tcpLen, ps)))
        return out
    }
}
