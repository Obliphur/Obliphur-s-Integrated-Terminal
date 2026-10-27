package com.obliphur.oit.ui

import android.content.Context
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * 都市之意主题的像素字体: GNU Unifont 16.0.04 (OpenType 构建, GPLv2+ 带字体嵌入例外).
 * 5 MB 不放进安装包: 第一次用这个主题时从 unifoundry.com 下载到 files/fonts/, 之后一直用本地的文件.
 * 断网期间 (VPN 全部丢弃) 下不下来, 先用系统字体, 每分钟重试一次, 联网之后就下好了.
 */
object PixelFont {
    const val URL = "https://unifoundry.com/pub/unifont/unifont-16.0.04/font-builds/unifont-16.0.04.otf"
    const val SIZE = 5_320_880L
    private const val NAME = "unifont-16.0.04.otf"

    /** 字体下载好、读进来之后才有值; 界面读它来换字体. */
    val family = mutableStateOf<FontFamily?>(null)

    private fun file(ctx: Context) = File(File(ctx.filesDir, "fonts"), NAME)

    fun present(ctx: Context): Boolean = file(ctx).let { it.isFile && it.length() == SIZE }

    private fun load(ctx: Context): Boolean {
        if (family.value != null) return true
        val f = file(ctx)
        if (!present(ctx)) return false
        return try {
            family.value = FontFamily(Font(f))
            true
        } catch (e: Exception) {
            f.delete()
            false
        }
    }

    /** 字体已经在本地就读进来, 没有就下载; 成功返回 true. 在后台线程里做. */
    suspend fun ensure(ctx: Context): Boolean = withContext(Dispatchers.IO) {
        if (load(ctx)) return@withContext true
        if (!download(ctx)) return@withContext false
        load(ctx)
    }

    private fun download(ctx: Context): Boolean {
        val dest = file(ctx)
        val tmp = File(dest.path + ".part")
        return try {
            dest.parentFile?.mkdirs()
            val c = URL(URL).openConnection() as HttpURLConnection
            c.connectTimeout = 15_000
            c.readTimeout = 30_000
            c.instanceFollowRedirects = true
            if (c.responseCode != 200) return false
            c.inputStream.use { input -> tmp.outputStream().use { input.copyTo(it) } }
            // 大小和文件头 ("OTTO" = OpenType CFF) 都对才算
            val ok = tmp.length() == SIZE && tmp.inputStream().use { s ->
                ByteArray(4).also { s.read(it) }.toString(Charsets.ISO_8859_1) == "OTTO"
            }
            if (ok) {
                dest.delete()
                tmp.renameTo(dest)
            } else tmp.delete()
            ok
        } catch (e: Exception) {
            tmp.delete()
            false
        }
    }
}
