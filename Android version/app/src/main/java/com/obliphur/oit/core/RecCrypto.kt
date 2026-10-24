package com.obliphur.oit.core

import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.security.MessageDigest
import java.security.SecureRandom
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import javax.crypto.Cipher
import javax.crypto.CipherInputStream
import javax.crypto.CipherOutputStream
import javax.crypto.spec.IvParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * 合约录像的加密和文件管理 (MECHANICS 18.8), 和桌面版 recorder.py 是同一套格式, 两边能互通:
 * 目光 (camera) / 刻痕 (screen) 录成 mp4 后, 用 AES-256-CTR 加密成 .oitv 存进数据目录, 防止被
 * 网盘 / 相册自动上传后看到; 只能在 OIT 里播放 (解密到缓存再打开). 刻痕的片段还会发给合约主人合并.
 *
 * 文件格式: MAGIC (5 字节) + nonce (8 字节) + 密文; 计数器 = nonce ‖ 第几个 16 字节块 (8 字节大端),
 * 即 AES/CTR/NoPadding, IV = nonce ‖ 00000000_00000000. 密钥所有 OIT 共用 (简易加密: 只防自动上传).
 * 片段文件名 "YYYYMMDD-HHMMSS[-n]__<cid>.oitv" 里带着合约 id, 结束后各设备 / 各段按它合并.
 */
object RecCrypto {
    val KINDS = listOf("camera", "screen")
    const val EXT = ".oitv"
    const val PART = ".recording"
    private val MAGIC = byteArrayOf('O'.code.toByte(), 'I'.code.toByte(), 'T'.code.toByte(), 'V'.code.toByte(), 1)
    private val REC_KEY: ByteArray =
        MessageDigest.getInstance("SHA-256").digest("obliphur-terminal/recording/v1".toByteArray())
    private val rnd = SecureRandom()
    private const val BUF = 1 shl 16

    private fun cipher(nonce: ByteArray): Cipher = Cipher.getInstance("AES/CTR/NoPadding").apply {
        init(Cipher.ENCRYPT_MODE, SecretKeySpec(REC_KEY, "AES"), IvParameterSpec(nonce + ByteArray(8)))
    }

    /** 录像文件夹: 数据目录/recordings/<kind> (camera / screen / merged / incoming). */
    fun folder(dataDir: File, kind: String) = File(File(dataDir, "recordings"), kind)

    /** 正在写的 (还没加密的) mp4 放在缓存里 (cacheDir/oit_rec/<kind>), 不在数据目录. */
    fun workDir(cacheDir: File, kind: String) = File(File(cacheDir, "oit_rec"), kind)

    /** 别的设备发来的某一路 (camera / screen) 片段先放这里 (按合约 id), 合并时一起用. */
    fun incomingDir(dataDir: File, cid: String, kind: String) =
        File(File(File(File(dataDir, "recordings"), "incoming"), safeCid(cid)), kind)

    /** 合并 / 同步好的最终录像 (camera / screen) 存这里; 界面只展示这里的 (不展示片段). */
    fun mergedDir(dataDir: File, kind: String) = File(File(File(dataDir, "recordings"), "merged"), kind)

    fun safeCid(cid: String) = cid.filter { it.isLetterOrDigit() }.take(24).ifEmpty { "x" }

    /** 别的设备 (合约主人) 发来的已经合并好的最终录像: 直接存进 merged/<kind> (替换这个合约旧的), 不再合并. */
    fun storeMerged(dataDir: File, cid: String, kind: String, src: File): File {
        val d = mergedDir(dataDir, kind).apply { mkdirs() }
        d.listFiles()?.forEach { if (cidOf(it.name) == safeCid(cid)) it.delete() }
        val stem = java.time.LocalDateTime.now().format(STAMP)
        val dst = File(d, "${stem}__${safeCid(cid)}$EXT")
        if (!src.renameTo(dst)) { src.copyTo(dst, overwrite = true); src.delete() }
        return dst
    }

    /** 某一路合并好的最终录像, 新的在前. */
    fun listMerged(dataDir: File, kind: String): List<Rec> {
        val out = ArrayList<Rec>()
        for (f in mergedDir(dataDir, kind).listFiles() ?: emptyArray()) {
            if (!f.name.lowercase().endsWith(EXT)) continue
            val start = runCatching { LocalDateTime.parse(f.name.take(15), STAMP) }.getOrNull()
            out.add(Rec(f, f.name, f.length(), start, false))
        }
        out.sortWith(compareByDescending<Rec> { it.start ?: LocalDateTime.MIN }.thenByDescending { it.name })
        return out
    }

    /** 删掉某个合约某一路合并好的最终录像 (删除要在设备间同步). */
    fun deleteMerged(dataDir: File, cid: String, kind: String): Boolean {
        var removed = false
        mergedDir(dataDir, kind).listFiles()?.forEach { if (cidOf(it.name) == safeCid(cid)) { it.delete(); removed = true } }
        return removed
    }

    /** 文件名里的合约 id (录像按它合并): "…__<cid>.ext" → cid; 没有则 "". */
    fun cidOf(name: String): String {
        var stem = name.substringBeforeLast('.', name)
        if (stem.endsWith(PART)) stem = stem.dropLast(PART.length)
        val i = stem.indexOf("__")
        return if (i < 0) "" else stem.substring(i + 2)
    }

    // ---------------------------------------------------------------- 加密 / 解密

    /** src (mp4) → dst (.oitv): 先写到 dst.tmp, 写完再改名. 不删 src. */
    fun encryptFile(src: File, dst: File) {
        val nonce = ByteArray(8).also { rnd.nextBytes(it) }
        val tmp = File(dst.parentFile, dst.name + ".tmp")
        src.inputStream().use { fi ->
            tmp.outputStream().use { raw ->
                raw.write(MAGIC); raw.write(nonce)
                CipherOutputStream(KeepOpen(raw), cipher(nonce)).use { fi.copyTo(it, BUF) }
            }
        }
        if (!tmp.renameTo(dst)) { tmp.copyTo(dst, overwrite = true); tmp.delete() }
    }

    /** src (.oitv) → dst (mp4). */
    fun decryptFile(src: File, dst: File) {
        src.inputStream().use { fi ->
            val head = ByteArray(MAGIC.size + 8)
            if (readFully(fi, head) != head.size || !head.copyOf(MAGIC.size).contentEquals(MAGIC))
                throw IllegalArgumentException("not an OIT recording")
            val nonce = head.copyOfRange(MAGIC.size, head.size)
            dst.outputStream().use { fo -> CipherInputStream(fi, cipher(nonce)).use { it.copyTo(fo, BUF) } }
        }
    }

    /** 写完的 mp4 (src) → 数据目录里的 .oitv, 删掉 src. 返回新的路径. */
    fun seal(dataDir: File, kind: String, src: File): File {
        val d = folder(dataDir, kind).apply { mkdirs() }
        var name = src.name
        if (name.endsWith(".mp4", true)) name = name.dropLast(4)
        if (name.endsWith(PART)) name = name.dropLast(PART.length)   // 被强杀时没改名的 (.recording.mp4): 封装后就是写完的片段
        var dst = File(d, name + EXT)
        var n = 1
        while (dst.exists()) { dst = File(d, "$name-$n$EXT"); n++ }
        encryptFile(src, dst)
        src.delete()
        return dst
    }

    /** 开 OIT 时: 上次没加密完的 mp4 (被强杀时留在缓存里的) 都加密起来. busy: 正在写的, 跳过. 返回加密了几个. */
    fun sealLeftovers(dataDir: File, cacheDir: File, busy: Set<String> = emptySet()): Int {
        var n = 0
        for (kind in KINDS) {
            val files = workDir(cacheDir, kind).listFiles() ?: continue
            for (f in files) {
                if (!f.name.endsWith(".mp4", true) || f.path in busy) continue
                runCatching { seal(dataDir, kind, f); n++ }
            }
        }
        return n
    }

    /** 播放: .oitv 解密到缓存 (cacheDir/oit_play), 返回解出来的 mp4 (用系统播放器打开). 先清掉上次的. */
    fun decryptToCache(cacheDir: File, path: File): File {
        val d = File(cacheDir, "oit_play").apply { mkdirs() }
        d.listFiles()?.forEach { it.delete() }
        if (!path.name.endsWith(EXT, true)) return path
        val out = File(d, path.name.dropLast(EXT.length) + ".mp4")
        decryptFile(path, out)
        return out
    }

    // ---------------------------------------------------------------- 列表

    data class Rec(val file: File, val name: String, val size: Long, val start: LocalDateTime?, val partial: Boolean)

    private val STAMP = DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss")

    /** 某一路的录像文件 (新的在前). work 给定时 (缓存目录) 也列出正在写的 mp4 (partial). */
    fun list(dataDir: File, kind: String, cacheDir: File? = null): List<Rec> {
        val out = ArrayList<Rec>()
        val dirs = listOfNotNull(folder(dataDir, kind), cacheDir?.let { workDir(it, kind) })
        for (dir in dirs) for (f in dir.listFiles() ?: emptyArray()) {
            val low = f.name.lowercase()
            val ext = listOf(EXT, ".mp4").firstOrNull { low.endsWith(it) } ?: continue
            var stem = f.name.dropLast(ext.length)
            // 只有缓存里的 mp4 是正在写的; 数据目录里的 .oitv 都已经写完 (以前被强杀后封装的 .recording.oitv 也算写完,
            // 否则这个合约的片段会因为"还在写"永远发不出去)
            val partial = ext == ".mp4"
            if (stem.endsWith(PART)) stem = stem.dropLast(PART.length)
            val start = runCatching { LocalDateTime.parse(stem.take(15), STAMP) }.getOrNull()
            out.add(Rec(f, f.name, f.length(), start, partial))
        }
        out.sortWith(compareByDescending<Rec> { it.start ?: LocalDateTime.MIN }.thenByDescending { it.name })
        return out
    }

    /** 某个合约这一路的录像片段 (新的在前). */
    fun filesOf(dataDir: File, kind: String, cid: String, cacheDir: File? = null): List<Rec> =
        if (cid.isEmpty()) emptyList() else list(dataDir, kind, cacheDir).filter { cidOf(it.name) == cid }

    fun totalSize(dataDir: File, kind: String): Long = list(dataDir, kind).sumOf { it.size }

    // ---------------------------------------------------------------- 工具

    private fun readFully(inp: InputStream, buf: ByteArray): Int {
        var n = 0
        while (n < buf.size) {
            val r = inp.read(buf, n, buf.size - n)
            if (r < 0) break
            n += r
        }
        return n
    }

    /** CipherOutputStream.close() 会关底层流; 包一层让我们自己控制外层流的生命周期. */
    private class KeepOpen(private val s: OutputStream) : OutputStream() {
        override fun write(b: Int) = s.write(b)
        override fun write(b: ByteArray, off: Int, len: Int) = s.write(b, off, len)
        override fun flush() = s.flush()
        override fun close() { s.flush() }
    }
}
