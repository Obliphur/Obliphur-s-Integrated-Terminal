package com.obliphur.oit.core

import com.obliphur.oit.sync.LinkCore
import com.obliphur.oit.sync.LinkFiles
import com.obliphur.oit.sync.SyncCrypto
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files

/** 联动程序 (MECHANICS 15.8): 对方的 OIT 不答 → 问它的联动程序 → 启动; 外出期间 / 刚启动过 / 密钥不对 都不启动. */
class LinkTest {
    private fun make(me: String, other: String, key: String): File {
        val d = Files.createTempDirectory("link").toFile()
        File(d, "sync_state.json").writeText(JSONObject().put("device", JSONObject().put("id", me)).put("peers", JSONObject()
            .put(other, JSONObject().put("name", other).put("addr", "127.0.0.1").put("port", 1).put("key", key))).toString())
        return d
    }

    @Test
    fun askAndStart() {
        val key = SyncCrypto.hex(ByteArray(32) { (it * 7).toByte() })
        val da = make("A", "B", key); val db = make("B", "A", key)
        val launched = mutableListOf<String>()
        val a = LinkCore(da, port = 18951, syncPorts = listOf(1), peerLinkPort = { 18952 }, launch = { launched.add("A"); true }, graceMs = 0)
        val b = LinkCore(db, port = 18952, syncPorts = listOf(1), peerLinkPort = { 18951 }, launch = { launched.add("B"); true }, graceMs = 0)
        assertTrue(a.serve()); assertTrue(b.serve())
        try {
            assertTrue(LinkCore.running(18951) && LinkCore.running(18952))
            assertFalse("第二个联动程序应该绑不上端口", LinkCore(da, port = 18951, launch = { true }).serve())
            a.probeOnce()                                       // 只为了记下本机 id
            val pb = LinkFiles.loadPeers(da).second["B"]!!
            // 外出期间不启动
            LinkFiles.writeHold(db, System.currentTimeMillis() + 3_600_000)
            assertEquals("hold", a.askPeer("B", pb)); assertTrue(launched.isEmpty())
            LinkFiles.writeHold(db, 0)
            assertEquals(0L, LinkFiles.holdUntil(db))
            // 密钥不对: 拒绝
            assertEquals("http-403", a.askPeer("B", JSONObject(pb.toString()).put("key", SyncCrypto.hex(ByteArray(32) { 9 }))))
            // 正常启动一次, 之后冷却
            assertEquals("started", a.askPeer("B", pb)); assertEquals(listOf("B"), launched)
            assertEquals("cooldown", a.askPeer("B", pb)); assertEquals(listOf("B"), launched)
            // 对方整台设备不在 (联动程序也不答): 什么都不做
            b.stop()
            assertEquals("", a.askPeer("B", pb))
            assertFalse(LinkCore.running(18952))
        } finally {
            a.stop(); b.stop()
        }
    }
}
