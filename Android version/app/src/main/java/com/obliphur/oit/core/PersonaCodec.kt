package com.obliphur.oit.core

import java.io.File
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import java.util.Base64
import java.util.UUID
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/** 双端通用的人设信封；固定内容产生固定密文，避免同步反复发现格式变化。 */
object PersonaCodec {
    const val MAX_PERSONA = 60000
    const val MAX_STORED = MAX_PERSONA * 6 + 128
    const val MAGIC = "OIT-PERSONA-ENC:1\n"
    private const val PREFIX = "OIT-PERSONA-ENC:"
    private val enc = sha("OIT/persona/v1/encryption")
    private val auth = sha("OIT/persona/v1/authentication")
    private fun sha(s: String) = MessageDigest.getInstance("SHA-256").digest(s.toByteArray(Charsets.UTF_8))
    private fun hmac(key: ByteArray) = Mac.getInstance("HmacSHA256").apply { init(SecretKeySpec(key, "HmacSHA256")) }
    private fun mac(key: ByteArray, data: ByteArray) = hmac(key).doFinal(data)

    fun protectedName(name: String): Boolean {
        val base = name.lowercase().removeSuffix(".bak")
        val stem = base.substringBeforeLast('.')
        return base.substringAfterLast('.') in listOf("txt", "md") && (stem == "claudia" || stem.startsWith("claudia_"))
    }

    private fun xor(data: ByteArray, nonce: ByteArray): ByteArray {
        val out = ByteArray(data.size)
        val stream = hmac(enc)
        val counter = ByteBuffer.allocate(4)
        for (start in data.indices step 32) {
            stream.update(nonce)
            val key = stream.doFinal(counter.putInt(0, start / 32).array())
            for (i in 0 until minOf(32, data.size - start)) out[start + i] = (data[start + i].toInt() xor key[i].toInt()).toByte()
        }
        return out
    }

    /** 旧明文仍可读取；损坏、超限及未知版本的密文不能进入提示词。 */
    fun decode(text: String): String {
        val value = text.removePrefix("\uFEFF").replace("\r\n", "\n")
        if (!value.startsWith(PREFIX)) return if (value.codePointCount(0, value.length) <= MAX_PERSONA) value
            else value.substring(0, value.offsetByCodePoints(0, MAX_PERSONA))
        require(value.startsWith(MAGIC) && value.length <= MAX_STORED)
        val raw = Base64.getDecoder().decode(value.substring(MAGIC.length).filterNot { it.isWhitespace() })
        require(raw.size in 48..(MAX_PERSONA * 4 + 48))
        val body = raw.copyOfRange(0, raw.size - 32)
        require(MessageDigest.isEqual(mac(auth, MAGIC.toByteArray(Charsets.UTF_8) + body), raw.copyOfRange(body.size, raw.size)))
        val bytes = xor(body.copyOfRange(16, body.size), body.copyOfRange(0, 16))
        val plain = Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString()
        require(plain.codePointCount(0, plain.length) <= MAX_PERSONA)
        return plain
    }

    fun encode(name: String, text: String): String {
        val encrypted = text.removePrefix("\uFEFF").startsWith(PREFIX)
        val plain = decode(text)
        if (!protectedName(name) && !encrypted) return plain
        val data = plain.toByteArray(Charsets.UTF_8)
        val nonce = mac(auth, "nonce\u0000".toByteArray(Charsets.UTF_8) + data).copyOf(16)
        val body = nonce + xor(data, nonce)
        val payload = Base64.getEncoder().encodeToString(body + mac(auth, MAGIC.toByteArray(Charsets.UTF_8) + body))
        return MAGIC + payload.chunked(96).joinToString("\n") + "\n"
    }

    fun read(file: File): String = file.bufferedReader(Charsets.UTF_8).use { reader ->
        val out = StringBuilder()
        val buf = CharArray(4096)
        while (out.length <= MAX_STORED) {
            val n = reader.read(buf, 0, minOf(buf.size, MAX_STORED + 1 - out.length))
            if (n < 0) break
            out.append(buf, 0, n)
        }
        decode(out.toString())
    }

    fun write(file: File, text: String) {
        val stored = encode(file.name, text)
        if (!stored.startsWith(MAGIC)) {
            file.writeText(stored, Charsets.UTF_8)
            return
        }
        val tmp = File(file.parentFile, "${file.name}.${UUID.randomUUID()}.tmp")
        try {
            tmp.writeText(stored, Charsets.UTF_8)
            try {
                Files.move(tmp.toPath(), file.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
            } catch (_: UnsupportedOperationException) {
                Files.move(tmp.toPath(), file.toPath(), StandardCopyOption.REPLACE_EXISTING)
            }
        } finally {
            tmp.delete()
        }
    }

    /** 分别转换当前文件与旧备份，不改变正文差异和聊天数据。 */
    fun migrate(folder: File) {
        for (file in folder.listFiles().orEmpty()) migrateFile(file)
    }

    fun migrateFile(file: File) {
        if (!file.isFile || !protectedName(file.name) || file.length() > MAX_PERSONA * 4L + 3) return
        runCatching {
            val stored = file.readText(Charsets.UTF_8).removePrefix("\uFEFF")
            val plain = stored.replace("\r\n", "\n")
            if (!stored.startsWith(PREFIX) && plain.codePointCount(0, plain.length) <= MAX_PERSONA) write(file, plain)
        }
    }
}
