package com.obliphur.oit.service

import android.net.VpnService
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.ServerSocket
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/** 无言为真的转发 (MECHANICS 18.6): 用本机的 TCP / UDP 回声服务器走一遍. */
class ForwarderTest {
    private val app = byteArrayOf(10, 111, 222.toByte(), 1)
    private val lo = byteArrayOf(127, 0, 0, 1)

    private fun u16(b: ByteArray, i: Int) = ((b[i].toInt() and 0xff) shl 8) or (b[i + 1].toInt() and 0xff)
    private fun u32(b: ByteArray, i: Int) = (u16(b, i) shl 16) or u16(b, i + 2)

    private fun sum(b: ByteArray, off: Int, len: Int, start: Long = 0): Long {
        var s = start
        var i = off
        while (i + 1 < off + len) { s += u16(b, i).toLong(); i += 2 }
        if (i < off + len) s += (b[i].toLong() and 0xff) shl 8
        return s
    }

    private fun fold(s0: Long): Int {
        var s = s0
        while (s ushr 16 != 0L) s = (s and 0xffff) + (s ushr 16)
        return (s.inv() and 0xffff).toInt()
    }

    /** 应用发出的 IPv4 TCP 包. */
    private fun tcp(sport: Int, dport: Int, seq: Int, ack: Int, flags: Int, data: ByteArray = ByteArray(0),
                    mss: Boolean = false): ByteArray {
        val opt = if (mss) 4 else 0
        val tl = 20 + opt + data.size
        val b = ByteArray(20 + tl)
        b[0] = 0x45; b[2] = (b.size ushr 8).toByte(); b[3] = b.size.toByte(); b[8] = 64; b[9] = 6
        System.arraycopy(app, 0, b, 12, 4); System.arraycopy(lo, 0, b, 16, 4)
        val c = fold(sum(b, 0, 20)); b[10] = (c ushr 8).toByte(); b[11] = c.toByte()
        b[20] = (sport ushr 8).toByte(); b[21] = sport.toByte(); b[22] = (dport ushr 8).toByte(); b[23] = dport.toByte()
        for (k in 0..3) { b[24 + k] = (seq ushr (24 - 8 * k)).toByte(); b[28 + k] = (ack ushr (24 - 8 * k)).toByte() }
        b[32] = (((20 + opt) / 4) shl 4).toByte(); b[33] = flags.toByte(); b[34] = 0xff.toByte(); b[35] = 0xff.toByte()
        if (mss) { b[40] = 2; b[41] = 4; b[42] = 0x05; b[43] = 0xb4.toByte() }
        System.arraycopy(data, 0, b, 40 + opt, data.size)
        val ps = sum(b, 12, 8) + 6 + tl
        val tc = fold(sum(b, 20, tl, ps)); b[36] = (tc ushr 8).toByte(); b[37] = tc.toByte()
        return b
    }

    /** 写回 VPN 的 TCP 包: 校验和都要对. */
    private class Seg(val seq: Int, val ack: Int, val flags: Int, val data: ByteArray)

    private fun seg(b: ByteArray): Seg {
        assertEquals(4, b[0].toInt() ushr 4)
        assertEquals(0, fold(sum(b, 0, 20)))                        // IP 首部校验和
        val tl = b.size - 20
        assertEquals(0, fold(sum(b, 20, tl, sum(b, 12, 8) + 6 + tl)))   // TCP 校验和
        assertTrue(b.copyOfRange(12, 16).contentEquals(lo))
        assertTrue(b.copyOfRange(16, 20).contentEquals(app))
        val doff = ((b[32].toInt() and 0xff) ushr 4) * 4
        return Seg(u32(b, 24), u32(b, 28), b[33].toInt() and 0xff, b.copyOfRange(20 + doff, b.size))
    }

    private fun forwarder(out: LinkedBlockingQueue<ByteArray>, block: String? = null) =
        Forwarder(object : VpnService() {}, { out.add(it) }) { _, _, _ -> block }

    @Test
    fun tcpEcho() {
        val server = ServerSocket(0, 5, InetAddress.getByName("127.0.0.1"))
        val t = Thread {
            server.accept().use { s ->
                val buf = ByteArray(5)
                var got = 0
                while (got < 5) got += s.getInputStream().read(buf, got, 5 - got)
                s.getOutputStream().write(String(buf).uppercase().toByteArray())
                s.getOutputStream().flush()
            }
        }.apply { start() }
        val out = LinkedBlockingQueue<ByteArray>()
        val f = forwarder(out)
        val port = server.localPort
        try {
            f.offer(tcp(40000, port, 1000, 0, 0x02, mss = true))
            val synAck = seg(out.poll(5, TimeUnit.SECONDS)!!)
            assertEquals(0x12, synAck.flags)
            assertEquals(1001, synAck.ack)
            val s0 = synAck.seq + 1
            f.offer(tcp(40000, port, 1001, s0, 0x10))
            f.offer(tcp(40000, port, 1001, s0, 0x18, "hello".toByteArray()))
            // 收到: 确认 hello, 回来的 HELLO, 服务器关掉后的 FIN (顺序不一定)
            val got = StringBuilder()
            var fin: Seg? = null
            var acked = false
            val until = System.currentTimeMillis() + 5000
            while ((fin == null || got.length < 5 || !acked) && System.currentTimeMillis() < until) {
                val s = seg(out.poll(5, TimeUnit.SECONDS) ?: break)
                if (s.ack == 1006) acked = true
                if (s.data.isNotEmpty()) { assertEquals(s0 + got.length, s.seq); got.append(String(s.data)) }
                if (s.flags and 0x01 != 0) fin = s
            }
            assertTrue(acked)
            assertEquals("HELLO", got.toString())
            assertNotNull(fin)
            assertEquals(s0 + 5, fin!!.seq)
            // 应用确认数据和 FIN, 自己也关: 转发确认这个 FIN
            f.offer(tcp(40000, port, 1006, s0 + 6, 0x11))
            val last = seg(out.poll(5, TimeUnit.SECONDS)!!)
            assertEquals(1007, last.ack)
        } finally {
            f.close(); t.join(2000); server.close()
        }
    }

    @Test
    fun blockedAppGetsNothing() {
        val server = ServerSocket(0, 5, InetAddress.getByName("127.0.0.1"))
        val out = LinkedBlockingQueue<ByteArray>()
        val f = forwarder(out, block = "com.tencent.mm")
        try {
            f.offer(tcp(40001, server.localPort, 1, 0, 0x02, mss = true))
            assertNull(out.poll(800, TimeUnit.MILLISECONDS))       // SYN 直接丢掉, 不回任何包
            server.soTimeout = 300
            assertTrue(runCatching { server.accept() }.isFailure)  // 也没有连到远端
        } finally {
            f.close(); server.close()
        }
    }

    @Test
    fun unknownConnectionGetsRst() {
        val out = LinkedBlockingQueue<ByteArray>()
        val f = forwarder(out)
        try {
            f.offer(tcp(40002, 9, 50, 777, 0x10))
            val r = seg(out.poll(5, TimeUnit.SECONDS)!!)
            assertEquals(0x04, r.flags)
            assertEquals(777, r.seq)
        } finally {
            f.close()
        }
    }

    @Test
    fun udpEcho() {
        val server = DatagramSocket(0, InetAddress.getByName("127.0.0.1"))
        val t = Thread {
            val p = DatagramPacket(ByteArray(100), 100)
            server.receive(p)
            server.send(DatagramPacket(String(p.data, 0, p.length).uppercase().toByteArray(), p.length, p.socketAddress))
        }.apply { start() }
        val out = LinkedBlockingQueue<ByteArray>()
        val f = forwarder(out)
        try {
            val payload = "ping".toByteArray()
            val b = ByteArray(28 + payload.size)
            b[0] = 0x45; b[3] = b.size.toByte(); b[8] = 64; b[9] = 17
            System.arraycopy(app, 0, b, 12, 4); System.arraycopy(lo, 0, b, 16, 4)
            b[20] = (41000 ushr 8).toByte(); b[21] = 41000.toByte()
            b[22] = (server.localPort ushr 8).toByte(); b[23] = server.localPort.toByte()
            b[25] = (8 + payload.size).toByte()
            System.arraycopy(payload, 0, b, 28, payload.size)
            f.offer(b)
            val pkt = out.poll(5, TimeUnit.SECONDS)!!
            val r = Packets.parseUdp(pkt, pkt.size)!!
            assertEquals("PING", String(r.payload))
            assertEquals(server.localPort, r.srcPort)
            assertEquals(41000, r.dstPort)
            assertTrue(r.src.contentEquals(lo) && r.dst.contentEquals(app))
        } finally {
            f.close(); t.join(2000); server.close()
        }
    }
}
