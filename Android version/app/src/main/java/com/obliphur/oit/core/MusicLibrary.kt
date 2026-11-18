package com.obliphur.oit.core

import android.content.res.AssetManager
import android.util.Xml
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.io.InputStream
import java.security.MessageDigest
import java.util.zip.ZipInputStream

/** 本地乐谱列表；默认文件随应用资源安装，用户文件留在数据目录。 */
class MusicLibrary(private val dataDir: File, private val assets: AssetManager) {
    private val root = File(dataDir, "music_library")
    private val defaults = File(root, "defaults")
    private val uploads = File(root, "uploads")
    private val manifest = JSONArray(assets.open("music_library/catalog.json").bufferedReader(Charsets.UTF_8).use { it.readText() })
    private val allowed = setOf("mscx", "mscz", "mxl", "musicxml", "xml")

    init { ensureDefaults() }

    private fun sha(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02X".format(it) }

    private fun ensureDefaults() {
        defaults.mkdirs(); uploads.mkdirs()
        for (i in 0 until manifest.length()) {
            val item = manifest.getJSONObject(i)
            val asset = "music_library/scores/${item.getString("file")}"
            val bytes = assets.open(asset).use { it.readBytes() }
            require(sha(bytes) == item.getString("sha256")) { "默认乐谱资源校验失败" }
            val target = File(defaults, item.getString("file"))
            if (!target.isFile || sha(target.readBytes()) != item.getString("sha256")) target.writeBytes(bytes)
        }
    }

    fun catalog(): JSONArray {
        val result = JSONArray()
        for (i in 0 until manifest.length()) {
            val item = manifest.getJSONObject(i)
            if (File(defaults, item.getString("file")).isFile) {
                result.put(JSONObject().put("id", item.getString("id")).put("title", item.getString("title"))
                    .put("title_zh", item.getString("title_zh")).put("composer", item.getString("composer"))
                    .put("tags", item.getJSONArray("tags")).put("source", item.getString("source"))
                    .put("license", item.getString("license")).put("kind", "default"))
            }
        }
        val seen = mutableSetOf<String>()
        (uploads.listFiles() ?: emptyArray()).filter { it.isFile && it.extension.lowercase() in allowed }
            .sortedBy { it.name.lowercase() }.forEach { file ->
                val id = "upload-${sha(file.readBytes()).take(16).lowercase()}"
                if (!seen.add(id)) return@forEach
                val score = metadata(file)
                result.put(JSONObject().put("id", id)
                    .put("title", score.first).put("composer", score.second).put("tags", JSONArray().put("user-upload"))
                    .put("kind", "upload").put("format", file.extension.lowercase()))
            }
        return result
    }

    fun add(source: File): JSONObject {
        require(source.isFile && source.length() in 1..(50L * 1024 * 1024)) { "乐谱文件不存在或超过50 MiB" }
        require(source.extension.lowercase() in allowed) { "仅支持 MuseScore 和 MusicXML 乐谱" }
        metadata(source)
        val bytes = source.readBytes()
        val digest = sha(bytes)
        val target = File(uploads, "$digest.${source.extension.lowercase()}")
        val duplicate = uploads.listFiles()?.any { it.isFile && it.nameWithoutExtension == digest } == true
        if (!duplicate && !target.exists()) target.writeBytes(bytes)
        return catalog().let { all -> (0 until all.length()).map { all.getJSONObject(it) }.first { it.optString("id") == "upload-${digest.take(16).lowercase()}" } }
    }

    fun get(id: String): Pair<JSONObject, File>? {
        for (i in 0 until manifest.length()) {
            val item = manifest.getJSONObject(i)
            if (item.optString("id") == id) return item to File(defaults, item.getString("file"))
        }
        if (id.startsWith("upload-")) {
            val file = uploads.listFiles()?.firstOrNull { it.isFile && it.nameWithoutExtension.startsWith(id.removePrefix("upload-"), true) && it.extension.lowercase() in allowed }
            if (file != null) {
                val (title, composer) = metadata(file)
                return JSONObject().put("id", id).put("title", title).put("composer", composer).put("kind", "upload") to file
            }
        }
        return null
    }

    fun select(id: String): JSONObject {
        val (item, file) = get(id) ?: error("所选乐谱已不存在")
        require(file.isFile) { "所选乐谱已不存在" }
        val state = JSONObject().put("id", id).put("title", item.optString("title"))
            .put("composer", item.optString("composer")).put("path", file.absolutePath).put("selected_at", System.currentTimeMillis())
        val temp = File(root, "selection.json.tmp")
        temp.writeText(state.toString(), Charsets.UTF_8)
        check(temp.renameTo(File(root, "selection.json"))) { "无法保存当前选曲" }
        return state
    }

    fun selection(): JSONObject? = runCatching {
        val state = JSONObject(File(root, "selection.json").readText(Charsets.UTF_8))
        val (_, file) = get(state.getString("id")) ?: return null
        if (!file.isFile) null else state.put("path", file.absolutePath)
    }.getOrNull()

    private fun metadata(file: File): Pair<String, String> {
        var title = ""
        var composer = ""
        val stream = scoreXml(file)
        stream.use {
            val parser = Xml.newPullParser().apply { setInput(it, "UTF-8") }
            var event = parser.eventType
            var checkedRoot = false
            while (event != org.xmlpull.v1.XmlPullParser.END_DOCUMENT) {
                if (!checkedRoot && event == org.xmlpull.v1.XmlPullParser.START_TAG) {
                    checkedRoot = true
                    require(parser.name.lowercase() in setOf("musescore", "score-partwise", "score-timewise")) {
                        "文件不是可识别的 MuseScore 或 MusicXML 乐谱"
                    }
                }
                if (event == org.xmlpull.v1.XmlPullParser.START_TAG && parser.name == "metaTag") {
                    val key = parser.getAttributeValue(null, "name")?.lowercase() ?: ""
                    val value = parser.nextText().trim()
                    if (key in setOf("worktitle", "movementtitle", "title") && title.isEmpty()) title = value
                    if (key in setOf("composer", "arranger") && composer.isEmpty()) composer = value
                }
                event = parser.next()
            }
        }
        if (title.isEmpty()) title = file.nameWithoutExtension.replace('_', ' ')
        return title.take(160) to composer.take(100)
    }

    private fun scoreXml(file: File): InputStream {
        if (file.extension.lowercase() !in setOf("mscz", "mxl")) return FileInputStream(file)
        val zip = ZipInputStream(FileInputStream(file))
        while (true) {
            val entry = zip.nextEntry ?: break
            val name = entry.name.lowercase()
            if (!entry.isDirectory && name.endsWith(".xml", ignoreCase = true) && !name.startsWith("meta-inf/")) return zip
            if (!entry.isDirectory && name.endsWith(".mscx")) return zip
            zip.closeEntry()
        }
        zip.close()
        error("压缩包中没有可读取的乐谱")
    }
}
