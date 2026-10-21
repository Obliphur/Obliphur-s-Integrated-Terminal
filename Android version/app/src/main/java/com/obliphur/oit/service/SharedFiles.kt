package com.obliphur.oit.service

import android.content.ContentValues
import android.content.Context
import android.provider.MediaStore
import java.io.File

/** 文件通道 (MECHANICS 15.6) 收到的文件存进公共目录: 图片 → 图片/OIT, 其他 → 下载/OIT (不需要存储权限). */
object SharedFiles {
    /** 把 src 存成 name, 返回显示用的位置 (例如 "Download/OIT/a.pdf"). */
    fun save(ctx: Context, src: File, name: String): String {
        val mime = java.net.URLConnection.guessContentTypeFromName(name)
            ?: android.webkit.MimeTypeMap.getSingleton().getMimeTypeFromExtension(name.substringAfterLast('.', "").lowercase())
        val image = mime?.startsWith("image/") == true
        val dir = if (image) "Pictures/OIT" else "Download/OIT"
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, name)
            if (mime != null) put(MediaStore.MediaColumns.MIME_TYPE, mime)
            put(MediaStore.MediaColumns.RELATIVE_PATH, dir)
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val coll = if (image) MediaStore.Images.Media.EXTERNAL_CONTENT_URI else MediaStore.Downloads.EXTERNAL_CONTENT_URI
        val r = ctx.contentResolver
        val uri = r.insert(coll, values) ?: throw IllegalStateException("MediaStore")
        try {
            r.openOutputStream(uri)!!.use { out -> src.inputStream().use { it.copyTo(out, 65536) } }
            r.update(uri, ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }, null, null)
        } catch (e: Exception) {
            runCatching { r.delete(uri, null, null) }
            throw e
        }
        return "$dir/$name"
    }
}
