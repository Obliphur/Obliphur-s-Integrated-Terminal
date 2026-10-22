package com.obliphur.oit.core

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.security.MessageDigest
import javax.crypto.Cipher
import javax.crypto.spec.SecretKeySpec

/**
 * 录像加密 (RecCrypto, MECHANICS 18.8) 和桌面版 recorder.py 的 .oitv 格式互通:
 * 这里用桌面版 rec_xor 的算法 (AES-ECB 算密钥流) 当参照, 两个方向都对一遍, 保证手机录的能在电脑上
 * 合并 / 播放, 电脑录的也能在手机上播放.
 */
class RecCryptoTest {
    private val recKey = MessageDigest.getInstance("SHA-256").digest("obliphur-terminal/recording/v1".toByteArray())
    private val magic = byteArrayOf('O'.code.toByte(), 'I'.code.toByte(), 'T'.code.toByte(), 'V'.code.toByte(), 1)

    /** 桌面版 recorder.rec_xor 的 Kotlin 参照: 第 i 个 16 字节块计数器 = nonce(8) ‖ i(8 大端). */
    private fun pcXor(nonce: ByteArray, offset: Int, data: ByteArray): ByteArray {
        val ecb = Cipher.getInstance("AES/ECB/NoPadding").apply { init(Cipher.ENCRYPT_MODE, SecretKeySpec(recKey, "AES")) }
        val first = offset / 16
        val skip = offset % 16
        val count = (skip + data.size + 15) / 16
        val ctr = ByteArray(count * 16)
        for (i in 0 until count) {
            System.arraycopy(nonce, 0, ctr, i * 16, 8)
            val blk = (first + i).toLong()
            for (j in 0 until 8) ctr[i * 16 + 8 + j] = (blk ushr (56 - j * 8)).toByte()
        }
        val ks = ecb.doFinal(ctr)
        return ByteArray(data.size) { (data[it].toInt() xor ks[skip + it].toInt()).toByte() }
    }

    private fun tmp(): File = Files.createTempDirectory("rec").toFile()

    @Test fun roundTrip() {
        val dir = tmp()
        val plain = ByteArray(5000) { (it * 31 + 7).toByte() }
        val src = File(dir, "a.mp4").apply { writeBytes(plain) }
        val enc = File(dir, "a.oitv")
        RecCrypto.encryptFile(src, enc)
        val bytes = enc.readBytes()
        assertArrayEquals(magic, bytes.copyOf(5))
        assertTrue(enc.length() == (5 + 8 + plain.size).toLong())
        val out = File(dir, "b.mp4")
        RecCrypto.decryptFile(enc, out)
        assertArrayEquals(plain, out.readBytes())
    }

    /** 电脑 → 手机: 用桌面算法造一个 .oitv, 手机解出来必须是原文. */
    @Test fun decryptsDesktopFormat() {
        val dir = tmp()
        val plain = "刻痕: focus device screen recording segment".repeat(40).toByteArray()
        val nonce = ByteArray(8) { (it + 1).toByte() }
        val ct = pcXor(nonce, 0, plain)
        val enc = File(dir, "pc.oitv").apply { writeBytes(magic + nonce + ct) }
        val out = File(dir, "pc.mp4")
        RecCrypto.decryptFile(enc, out)
        assertArrayEquals(plain, out.readBytes())
    }

    /** 手机 → 电脑: 手机加密的 .oitv, 用桌面算法 (读出它的 nonce) 解出来必须是原文. */
    @Test fun encryptsDesktopReadable() {
        val dir = tmp()
        val plain = ByteArray(8192) { (it xor 0x5a).toByte() }
        val src = File(dir, "p.mp4").apply { writeBytes(plain) }
        val enc = File(dir, "p.oitv")
        RecCrypto.encryptFile(src, enc)
        val bytes = enc.readBytes()
        val nonce = bytes.copyOfRange(5, 13)
        val ct = bytes.copyOfRange(13, bytes.size)
        assertArrayEquals(plain, pcXor(nonce, 0, ct))
    }

    @Test fun cidFromName() {
        assertEquals("c1a2b3", RecCrypto.cidOf("20261022-153000__c1a2b3.oitv"))
        assertEquals("c1a2b3", RecCrypto.cidOf("20261022-153000-2__c1a2b3.recording.mp4"))
        assertEquals("", RecCrypto.cidOf("20261022-153000.oitv"))
    }

    @Test fun listsNewestFirst() {
        val data = tmp()
        val d = RecCrypto.folder(data, "screen").apply { mkdirs() }
        File(d, "20261022-100000__x.oitv").writeBytes(magic + ByteArray(8) + ByteArray(10))
        File(d, "20261022-120000__x.oitv").writeBytes(magic + ByteArray(8) + ByteArray(10))
        val names = RecCrypto.list(data, "screen").map { it.name }
        assertEquals(listOf("20261022-120000__x.oitv", "20261022-100000__x.oitv"), names)
        assertEquals(2, RecCrypto.filesOf(data, "screen", "x").size)
    }
}
