package com.obliphur.oit.core

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.webkit.WebViewClient
import com.sun.jna.Library
import com.sun.jna.Native
import com.sun.jna.Pointer
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.zip.ZipFile

/** 本机音乐任务串行；转换失败不替换已有输出，媒体和缓存不参加设备同步。 */
class MusicTools(private val ctx: Context, private val dir: File) {
    companion object {
        private val lock = Any()
        fun sha(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
        fun atomic(target: File, bytes: ByteArray) {
            target.parentFile!!.mkdirs()
            val temp = File.createTempFile("music-", ".tmp", target.parentFile)
            try { temp.writeBytes(bytes); check(temp.renameTo(target)) { "无法保存音乐文件" } } finally { temp.delete() }
        }
    }
    @Volatile var busy = false; private set
    @Volatile var progress = 0; private set
    @Volatile var error = ""; private set
    private fun <T> task(action: () -> T): T = synchronized(lock) {
        check(Looper.myLooper() != Looper.getMainLooper()) { "音乐转换必须在后台执行" }
        busy = true; progress = 0; error = ""
        try { action().also { progress = 100 } } catch (e: Exception) { error = e.message.orEmpty(); throw e } finally { busy = false }
    }
    fun asset(name: String): File {
        val target = File(dir, "music_library/engine/$name")
        val bytes = ctx.assets.open("music_engine/$name").use { it.readBytes() }
        if (!target.isFile || sha(target.readBytes()) != sha(bytes)) atomic(target, bytes)
        return target
    }
    fun midi(source: File, humanize: Boolean = false): ByteArray = task {
        require(source.isFile && source.length() in 1..Cfg.MUSIC_MAX_BYTES.toLong()) { "音乐文件不存在或超过50 MiB" }
        val raw = when (source.extension.lowercase()) {
            "mid", "midi" -> source.readBytes()
            "musicxml", "mxl", "xml" -> convertXml(source)
            "wav", "mp3", "flac", "ogg", "m4a", "aac" -> MusicTranscribe(ctx, asset("basic-pitch.onnx")).convert(source) { progress = it }
            "mscx", "mscz" -> throw IllegalArgumentException("安卓上传乐谱请使用 MusicXML 或 MXL；MuseScore 文件请先在电脑导出为 MusicXML")
            else -> throw IllegalArgumentException("不支持此音乐文件格式")
        }
        MusicMidi.read(raw)
        if (humanize) MusicMidi.humanize(raw) else raw
    }

    @Suppress("SetJavaScriptEnabled")
    private fun convertXml(source: File): ByteArray {
        val xml = if (source.extension.equals("mxl", true)) ZipFile(source).use { z ->
            val entries = z.entries().asSequence().toList()
            val container = z.getEntry("META-INF/container.xml")?.let { z.getInputStream(it).use { input -> input.readBytes().toString(Charsets.UTF_8) } }
            val root = container?.let { Regex("full-path\\s*=\\s*[\"']([^\"']+)[\"']").find(it)?.groupValues?.get(1) }
            val entry = root?.let(z::getEntry) ?: entries.firstOrNull { !it.isDirectory && !it.name.startsWith("META-INF/") && it.name.endsWith(".xml", true) }
                ?: error("压缩包中没有可读取的乐谱")
            require(entry.size in 1..Cfg.MUSIC_MAX_BYTES.toLong()) { "乐谱解压后过大" }
            z.getInputStream(entry).use { it.readBytes() }
        } else source.readBytes()
        require(xml.size <= Cfg.MUSIC_MAX_BYTES)
        val latch = CountDownLatch(1)
        var result = ""; var failure = ""
        val main = Handler(Looper.getMainLooper()); var web: WebView? = null
        val closed = java.util.concurrent.atomic.AtomicBoolean(false)
        val bridge = object {
            @JavascriptInterface fun source() = Base64.encodeToString(xml, Base64.NO_WRAP)
            @JavascriptInterface fun finish(data: String, error: String) { result = data; failure = error; latch.countDown() }
        }
        main.post {
            if (!closed.get()) try {
                web = WebView(ctx).apply {
                    settings.javaScriptEnabled = true
                    settings.blockNetworkLoads = true
                    settings.allowContentAccess = false
                    addJavascriptInterface(bridge, "OitScore")
                    webViewClient = object : WebViewClient() {
                        override fun shouldOverrideUrlLoading(view: WebView?, request: android.webkit.WebResourceRequest?) = true
                    }
                    loadUrl("file:///android_asset/music_engine/convert.html")
                }
            } catch (e: Exception) { failure = e.message.orEmpty(); latch.countDown() }
        }
        try {
            check(latch.await(120, TimeUnit.SECONDS)) { "MusicXML 转换超时" }
            check(failure.isEmpty() && result.isNotEmpty()) { "MusicXML 转换失败: $failure" }
            return Base64.decode(result, Base64.DEFAULT).also { MusicMidi.read(it) }
        } finally { closed.set(true); main.post { web?.apply { removeJavascriptInterface("OitScore"); destroy() }; web = null } }
    }

    /** JNA 调官方 Android 动态库，使用 sample 时钟离线渲染，事件不经谱面量化。 */
    interface Fluid : Library {
        fun new_fluid_settings(): Pointer?
        fun delete_fluid_settings(settings: Pointer)
        fun fluid_settings_setnum(settings: Pointer, name: String, value: Double): Int
        fun fluid_settings_setint(settings: Pointer, name: String, value: Int): Int
        fun fluid_settings_setstr(settings: Pointer, name: String, value: String): Int
        fun new_fluid_synth(settings: Pointer): Pointer?
        fun delete_fluid_synth(synth: Pointer)
        fun fluid_synth_sfload(synth: Pointer, path: String, reset: Int): Int
        fun new_fluid_player(synth: Pointer): Pointer?
        fun fluid_player_add(player: Pointer, path: String): Int
        fun fluid_player_play(player: Pointer): Int
        fun fluid_player_get_status(player: Pointer): Int
        fun delete_fluid_player(player: Pointer)
        fun new_fluid_file_renderer(synth: Pointer): Pointer?
        fun fluid_file_renderer_process_block(renderer: Pointer): Int
        fun delete_fluid_file_renderer(renderer: Pointer)
    }
    fun render(midi: File, target: File): File = task {
        val duration = MusicMidi.duration(MusicMidi.read(midi.readBytes()))
        require(duration in 0.0..3600.0) { "曲目时长超过一小时" }
        val fluid = Native.load("fluidsynth", Fluid::class.java)
        val settings = checkNotNull(fluid.new_fluid_settings())
        var synth: Pointer? = null; var player: Pointer? = null; var renderer: Pointer? = null
        target.parentFile!!.mkdirs()
        val temp = File.createTempFile("render-", ".wav", target.parentFile)
        try {
            check(fluid.fluid_settings_setnum(settings, "synth.sample-rate", 44100.0) == 0)
            fluid.fluid_settings_setint(settings, "synth.cpu-cores", Cfg.MUSIC_CPU_THREADS)
            fluid.fluid_settings_setstr(settings, "player.timing-source", "sample")
            fluid.fluid_settings_setstr(settings, "audio.file.name", temp.absolutePath)
            fluid.fluid_settings_setstr(settings, "audio.file.type", "wav")
            fluid.fluid_settings_setstr(settings, "audio.file.format", "s16")
            synth = checkNotNull(fluid.new_fluid_synth(settings))
            check(fluid.fluid_synth_sfload(synth, asset("piano.sf2").absolutePath, 1) >= 0) { "钢琴音色加载失败" }
            player = checkNotNull(fluid.new_fluid_player(synth))
            check(fluid.fluid_player_add(player, midi.absolutePath) == 0)
            renderer = checkNotNull(fluid.new_fluid_file_renderer(synth))
            check(fluid.fluid_player_play(player) == 0)
            var blocks = 0
            val maxBlocks = ((duration + 30) * 44100 / 64).toInt()
            while (fluid.fluid_player_get_status(player) == 1) {
                check(++blocks <= maxBlocks && !Thread.currentThread().isInterrupted) { "音乐渲染超时" }
                check(fluid.fluid_file_renderer_process_block(renderer) == 0) { "音乐渲染失败" }
                progress = (blocks * 64 / 44100.0 / (duration + 2) * 100).toInt().coerceIn(0, 99)
            }
            fluid.delete_fluid_file_renderer(renderer); renderer = null
            require(temp.length() > 44 && temp.inputStream().use { String(it.readNBytes(4), Charsets.US_ASCII) } == "RIFF") { "音乐渲染未生成有效音频" }
            check(temp.renameTo(target)) { "无法保存音乐文件" }
            target
        } finally {
            renderer?.let(fluid::delete_fluid_file_renderer); player?.let(fluid::delete_fluid_player)
            synth?.let(fluid::delete_fluid_synth); fluid.delete_fluid_settings(settings); temp.delete()
        }
    }
}
