package com.obliphur.oit.sync

import org.json.JSONObject
import java.math.BigInteger
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

class SealError(msg: String) : Exception(msg)

/**
 * 设备同步的密码学部分, 和桌面版 devsync.py 是同一套算法 (MECHANICS 15.3):
 * DH (RFC 3526 第 14 组) → K = SHA256(共享秘密, 256 字节大端);
 * 配对码 = SHA256(K ‖ "code") 前 4 字节 mod 10⁶; 加密密钥流 = HMAC(HMAC(K,"enc"), nonce ‖ 计数器);
 * 签名 = HMAC(HMAC(K,"mac"), nonce ‖ ts(8 字节) ‖ 密文).
 */
object SyncCrypto {
    val P = BigInteger(
        "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A0879" +
            "8E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B" +
            "0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA4836" +
            "1C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804" +
            "F1746C08CA18217C32905E462E36CE3BE39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6" +
            "955817183995497CEA956AE515D2261898FA051015728E5A8AACAA68FFFFFFFFFFFFFFFF", 16)
    private val G = BigInteger.valueOf(2)
    private val rnd = SecureRandom()
    const val TS_SKEW_MS = 300_000L

    fun hmac(key: ByteArray, msg: ByteArray): ByteArray =
        Mac.getInstance("HmacSHA256").apply { init(SecretKeySpec(key, "HmacSHA256")) }.doFinal(msg)

    fun sha256(b: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(b)

    fun hex(b: ByteArray) = b.joinToString("") { "%02x".format(it) }
    fun unhex(s: String) = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    fun keypair(): Pair<BigInteger, BigInteger> {
        val x = BigInteger(256, rnd).setBit(255)
        return x to G.modPow(x, P)
    }

    fun shared(x: BigInteger, peerPubHex: String): ByteArray {
        val y = BigInteger(peerPubHex, 16)
        if (y <= BigInteger.ONE || y >= P - BigInteger.ONE) throw SealError("bad public key")
        val s = y.modPow(x, P).toByteArray().dropWhile { it == 0.toByte() }.toByteArray()
        val fixed = ByteArray(256 - s.size) + s
        return sha256(fixed)
    }

    fun pairCode(k: ByteArray): String {
        val d = sha256(k + "code".toByteArray())
        val n = ((d[0].toLong() and 0xff) shl 24) or ((d[1].toLong() and 0xff) shl 16) or
            ((d[2].toLong() and 0xff) shl 8) or (d[3].toLong() and 0xff)
        return "%06d".format(n % 1_000_000)
    }

    fun acceptProof(k: ByteArray, token: String) = hex(hmac(k, "accepted:$token".toByteArray()))

    private fun xor(enc: ByteArray, nonce: ByteArray, data: ByteArray): ByteArray {
        val out = ByteArray(data.size)
        var i = 0
        while (i < data.size) {
            val ctr = byteArrayOf((i / 32 ushr 24).toByte(), (i / 32 ushr 16).toByte(), (i / 32 ushr 8).toByte(), (i / 32).toByte())
            val block = hmac(enc, nonce + ctr)
            for (j in 0 until minOf(32, data.size - i)) out[i + j] = (data[i + j].toInt() xor block[j].toInt()).toByte()
            i += 32
        }
        return out
    }

    private fun tsBytes(ts: Long) = ByteArray(8) { (ts ushr (56 - it * 8)).toByte() }

    /** 文件通道 (MECHANICS 15.6) 的加密密钥 (由绑定时的密钥算出). 同 devsync.file_key. */
    fun fileKey(k: ByteArray): ByteArray = hmac(k, "file".toByteArray())

    /** 文件通道: data[0, len) 和密钥流原地异或 (加密 / 解密一样), 这段在文件里从 offset 开始.
     *  密钥流第 i 个 32 字节 = SHA-256(密钥 + nonce + i). 同 devsync.file_xor. */
    fun fileXor(fk: ByteArray, nonce: ByteArray, offset: Long, data: ByteArray, len: Int = data.size) {
        val md = MessageDigest.getInstance("SHA-256")
        var pos = 0
        while (pos < len) {
            val abs = offset + pos
            val block = abs / 32
            val skip = (abs % 32).toInt()
            md.update(fk)
            md.update(nonce)
            md.update(tsBytes(block))
            val ks = md.digest()
            val n = minOf(32 - skip, len - pos)
            for (j in 0 until n) data[pos + j] = (data[pos + j].toInt() xor ks[skip + j].toInt()).toByte()
            pos += n
        }
    }

    /** 别的设备发来的文件名: 只留文件名本身, 去掉路径和不允许的字符. 同 devsync.safe_name. */
    fun safeName(name: String): String {
        val base = name.split('/', '\\').last().replace(Regex("[<>:\"|?*\\x00-\\x1f]"), "_").trim(' ', '.')
        return base.take(180).ifEmpty { "file" }
    }

    fun seal(k: ByteArray, sender: String, obj: JSONObject, nonce: ByteArray? = null, ts: Long? = null): JSONObject {
        val n = nonce ?: ByteArray(16).also { rnd.nextBytes(it) }
        val t = ts ?: System.currentTimeMillis()
        val ct = xor(hmac(k, "enc".toByteArray()), n, obj.toString().toByteArray(Charsets.UTF_8))
        val mac = hex(hmac(hmac(k, "mac".toByteArray()), n + tsBytes(t) + ct))
        val b64 = Base64.getEncoder()
        return JSONObject().put("from", sender).put("nonce", b64.encodeToString(n)).put("ts", t)
            .put("ct", b64.encodeToString(ct)).put("mac", mac)
    }

    private val seen = LinkedHashMap<String, Long>()

    fun unseal(k: ByteArray, env: JSONObject, replay: Boolean = true): JSONObject {
        val nonce: ByteArray
        val ct: ByteArray
        val ts: Long
        val mac: String
        try {
            nonce = Base64.getDecoder().decode(env.getString("nonce"))
            ct = Base64.getDecoder().decode(env.getString("ct"))
            ts = env.getLong("ts")
            mac = env.getString("mac")
        } catch (e: Exception) {
            throw SealError("malformed")
        }
        val want = hex(hmac(hmac(k, "mac".toByteArray()), nonce + tsBytes(ts) + ct))
        if (!MessageDigest.isEqual(want.toByteArray(), mac.toByteArray())) throw SealError("bad mac")
        if (kotlin.math.abs(System.currentTimeMillis() - ts) > TS_SKEW_MS) throw SealError("clock")
        if (replay) synchronized(seen) {
            val now = System.currentTimeMillis()
            seen.entries.removeAll { now - it.value > 2 * TS_SKEW_MS }
            if (seen.containsKey(mac)) throw SealError("replay")
            seen[mac] = now
        }
        return JSONObject(String(xor(hmac(k, "enc".toByteArray()), nonce, ct), Charsets.UTF_8))
    }
}
