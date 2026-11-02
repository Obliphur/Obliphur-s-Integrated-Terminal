package com.obliphur.oit.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.LocalDateTime

/** 数据文件读写: 写入先写 .tmp 再改名, 中途断电也不会留下半个文件. */
object Store {
    fun readObj(f: File): JSONObject? = try {
        if (f.exists()) JSONObject(f.readText(Charsets.UTF_8)) else null
    } catch (e: Exception) {
        null
    }

    fun write(f: File, o: JSONObject): Boolean = try {
        f.parentFile?.mkdirs()
        val tmp = File(f.path + ".tmp")
        java.io.FileOutputStream(tmp).use { out ->        // 写完 fsync 再改名: 断电不会留下半个文件
            out.write(o.toString(1).toByteArray(Charsets.UTF_8))
            out.flush()
            runCatching { out.fd.sync() }
        }
        if (!tmp.renameTo(f)) {
            f.delete()
            tmp.renameTo(f)
        } else true
    } catch (e: Exception) {
        false
    }

    /** 只追加的流水 (net_requests.log, settings_changes.log …). */
    fun append(f: File, text: String) {
        try {
            f.parentFile?.mkdirs()
            f.appendText("${LocalDateTime.now().format(Fmt.TS)}  $text\n", Charsets.UTF_8)
        } catch (_: Exception) {
        }
    }
}

fun JSONObject.dbl(key: String, def: Double = 0.0): Double {
    val v = opt(key) ?: return def
    return when (v) {
        is Number -> v.toDouble()
        is String -> v.toDoubleOrNull() ?: def
        else -> def
    }
}

fun JSONObject.strOrNull(key: String): String? =
    if (has(key) && !isNull(key)) optString(key) else null

fun JSONArray.objects(): List<JSONObject> =
    (0 until length()).mapNotNull { optJSONObject(it) }

fun JSONArray.strings(): List<String> =
    (0 until length()).mapNotNull { if (isNull(it)) null else opt(it)?.toString() }
