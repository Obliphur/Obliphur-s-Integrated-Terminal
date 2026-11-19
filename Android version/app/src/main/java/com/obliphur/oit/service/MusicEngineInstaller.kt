package com.obliphur.oit.service

import android.content.Context
import android.os.Build
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.zip.ZipFile

/** 按设备 ABI 安装可选音乐运行时；下载和包内文件都要校验摘要。 */
class MusicEngineInstaller(private val ctx: Context, private val updater: Updater) {
    companion object {
        private const val MAX_PACKAGE = 100L * 1024 * 1024
        private val ABIS = setOf("arm64-v8a", "armeabi-v7a", "x86", "x86_64")
        private fun sha(file: File): String = MessageDigest.getInstance("SHA-256").digest(file.readBytes())
            .joinToString("") { "%02x".format(it) }
    }

    val abi: String? = Build.SUPPORTED_ABIS.firstOrNull { it in ABIS }
    @Volatile var state = if (installed) "installed" else "missing"
        private set
    @Volatile var error = ""
        private set

    private val root get() = File(ctx.filesDir, "music-engine/${abi ?: "unsupported"}")
    val installed get() = abi != null && File(root, "install.json").isFile
    val assetName get() = abi?.let { "OIT-music-engine-$it.zip" }.orEmpty()

    fun file(relative: String): File {
        check(installed) { "请先在设置 → 额外功能下载音乐运行时" }
        require(relative.startsWith("assets/music_engine/") || relative.startsWith("assets/music_library/midi/") ||
            relative.startsWith("jniLibs/$abi/")) { "音乐资源路径无效" }
        return File(root, relative).also { require(it.isFile) { "音乐资源缺失，请在额外功能重新安装" } }
    }

    @Synchronized fun install() {
        if (state == "downloading" || installed) return
        val selectedAbi = abi ?: run { state = "error"; error = "此设备 ABI 暂不支持音乐运行时"; return }
        state = "downloading"
        error = ""
        Thread {
            val archive = File(ctx.cacheDir, "music-engine-$selectedAbi.zip")
            val staging = File(root.parentFile, "$selectedAbi.installing")
            try {
                val asset = updater.fetchReleaseAsset("OIT-music-engine-$selectedAbi.zip")
                    ?: error("当前发布没有音乐运行时附件")
                updater.downloadReleaseAsset(asset, archive, MAX_PACKAGE)
                installArchive(archive, staging, selectedAbi)
                state = "installed"
            } catch (e: Exception) {
                error = e.message ?: e.javaClass.simpleName
                state = "error"
            } finally {
                archive.delete()
                staging.deleteRecursively()
            }
        }.apply { isDaemon = true; start() }
    }

    private fun installArchive(archive: File, staging: File, selectedAbi: String) {
        staging.deleteRecursively()
        check(staging.mkdirs()) { "无法创建音乐资源目录" }
        ZipFile(archive).use { zip ->
            val manifestEntry = zip.getEntry("manifest.json") ?: error("音乐资源包缺少清单")
            val manifest = zip.getInputStream(manifestEntry).use { JSONObject(it.readBytes().toString(Charsets.UTF_8)) }
            val expected = manifest.keys().asSequence().associateWith { manifest.getString(it) }
            val allowed = expected.keys.filter {
                it.startsWith("assets/music_engine/") || it.startsWith("assets/music_library/midi/") ||
                    it.startsWith("jniLibs/$selectedAbi/")
            }.toSet()
            check(allowed.isNotEmpty() && allowed == expected.keys) { "音乐资源清单含有不匹配的 ABI 或路径" }
            val entries = zip.entries().asSequence().filterNot { it.isDirectory || it.name == "manifest.json" }.toList()
            check(entries.map { it.name }.toSet() == allowed) { "音乐资源包文件与清单不匹配" }
            var total = 0L
            for (entry in entries) {
                val name = entry.name
                check(!name.startsWith('/') && '\\' !in name && name.split('/').none { it == ".." || it.isEmpty() }) {
                    "音乐资源包包含无效路径"
                }
                check(entry.size in 0..MAX_PACKAGE && total + entry.size <= MAX_PACKAGE) { "音乐资源包过大" }
                val target = File(staging, name)
                target.parentFile?.mkdirs()
                zip.getInputStream(entry).use { input -> target.outputStream().use { output -> input.copyTo(output) } }
                total += target.length()
                check(sha(target) == expected.getValue(name)) { "音乐资源校验失败: $name" }
            }
            check(File(staging, "assets/music_engine/piano.sf2").isFile &&
                File(staging, "assets/music_engine/verovio-toolkit.js").isFile &&
                File(staging, "assets/music_engine/basic-pitch.onnx").isFile &&
                File(staging, "jniLibs/$selectedAbi/libfluidsynth.so").isFile) { "音乐资源包不完整" }
            File(staging, "install.json").writeText(JSONObject().put("abi", selectedAbi).toString(), Charsets.UTF_8)
        }
        val target = root
        val backup = File(root.parentFile, "$selectedAbi.previous")
        backup.deleteRecursively()
        if (target.exists()) check(target.renameTo(backup)) { "无法替换现有音乐资源" }
        if (!staging.renameTo(target)) {
            if (backup.exists()) backup.renameTo(target)
            error("无法启用音乐资源")
        }
        backup.deleteRecursively()
    }
}
