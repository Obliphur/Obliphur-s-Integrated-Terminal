package com.obliphur.oit.core

import android.content.res.AssetManager
import android.util.Xml
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.io.InputStream
import java.security.MessageDigest
import java.nio.charset.StandardCharsets
import java.util.zip.ZipInputStream

/** 本地乐谱列表；默认文件随应用资源安装，用户文件留在数据目录。 */
class MusicLibrary(private val dataDir: File, private val assets: AssetManager) {
    private val generationLock = Any()
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

    private fun personaKey(persona: String) = sha(persona.toByteArray(StandardCharsets.UTF_8)).take(20).lowercase()
    private fun isClaudia(persona: String?) = persona == null || persona.substringBeforeLast('.', persona).equals("Claudia", true)
    private fun available(item: JSONObject, persona: String?, related: Boolean, valence: Double): Boolean {
        if (!item.optBoolean("relationship_required")) return true
        return persona != null && isClaudia(persona) && related && valence.isFinite() && valence >= item.optDouble("valence_min", 70.0)
    }
    private fun uploadDirs(persona: String?) = if (persona == null) listOf(uploads) else buildList {
        add(File(uploads, personaKey(persona)))
        if (isClaudia(persona)) add(uploads)
    }
    private fun stateDir(persona: String?) = if (isClaudia(persona)) root else File(File(root, "personas"), personaKey(persona!!))

    fun catalog(persona: String? = null, related: Boolean = false, valence: Double = 0.0): JSONArray {
        val result = JSONArray()
        val claudia = isClaudia(persona)
        if (claudia) {
            for (i in 0 until manifest.length()) {
                val item = manifest.getJSONObject(i)
                if (available(item, persona, related, valence) && File(defaults, item.getString("file")).isFile) {
                    result.put(JSONObject().put("id", item.getString("id")).put("title", item.getString("title"))
                        .put("title_zh", item.getString("title_zh")).put("composer", item.getString("composer"))
                        .put("tags", item.getJSONArray("tags")).put("source", item.getString("source"))
                        .put("license", item.getString("license")).put("kind", "default"))
                }
            }
        }
        val seen = mutableSetOf<String>()
        val dirs = uploadDirs(persona)
        dirs.forEach { dir ->
            (dir.listFiles() ?: emptyArray()).filter { it.isFile && it.extension.lowercase() in allowed }
                .sortedBy { it.name.lowercase() }.forEach score@{ file ->
                    val id = "upload-${sha(file.readBytes()).take(16).lowercase()}"
                    if (!seen.add(id)) return@score
                    val score = metadata(file)
                    result.put(JSONObject().put("id", id)
                        .put("title", score.first).put("composer", score.second).put("tags", JSONArray().put("user-upload"))
                        .put("kind", "upload").put("format", file.extension.lowercase()))
                }
            }
        return result
    }

    fun add(source: File, persona: String? = null): JSONObject {
        require(source.isFile && source.length() in 1..(50L * 1024 * 1024)) { "乐谱文件不存在或超过50 MiB" }
        require(source.extension.lowercase() in allowed) { "仅支持 MuseScore 和 MusicXML 乐谱" }
        metadata(source)
        val bytes = source.readBytes()
        val digest = sha(bytes)
        val dir = if (persona == null) uploads else File(uploads, personaKey(persona)).also { it.mkdirs() }
        val target = File(dir, "$digest.${source.extension.lowercase()}")
        val duplicate = dir.listFiles()?.any { it.isFile && it.nameWithoutExtension == digest } == true
        if (!duplicate && !target.exists()) target.writeBytes(bytes)
        return catalog(persona).let { all -> (0 until all.length()).map { all.getJSONObject(it) }.first { it.optString("id") == "upload-${digest.take(16).lowercase()}" } }
    }

    fun get(id: String, persona: String? = null, related: Boolean = false, valence: Double = 0.0): Pair<JSONObject, File>? {
        for (i in 0 until (if (isClaudia(persona)) manifest.length() else 0)) {
            val item = manifest.getJSONObject(i)
            if (item.optString("id") == id && available(item, persona, related, valence)) return item to File(defaults, item.getString("file"))
        }
        if (id.startsWith("upload-")) {
            val file = uploadDirs(persona).asSequence().flatMap { (it.listFiles() ?: emptyArray()).asSequence() }
                .firstOrNull { it.isFile && it.nameWithoutExtension.startsWith(id.removePrefix("upload-"), true) && it.extension.lowercase() in allowed }
            if (file != null) {
                val (title, composer) = metadata(file)
                return JSONObject().put("id", id).put("title", title).put("composer", composer).put("kind", "upload") to file
            }
        }
        return null
    }

    fun select(id: String, persona: String? = null, related: Boolean = false, valence: Double = 0.0): JSONObject {
        val all = catalog(persona, related, valence)
        require((0 until all.length()).any { all.getJSONObject(it).optString("id") == id }) { "乐谱不属于当前人设的曲库" }
        val (item, file) = get(id, persona, related, valence) ?: error("所选乐谱已不存在")
        require(file.isFile) { "所选乐谱已不存在" }
        val state = JSONObject().put("id", id).put("title", item.optString("title"))
            .put("composer", item.optString("composer")).put("path", file.absolutePath).put("selected_at", System.currentTimeMillis())
        val stateDir = stateDir(persona).also { it.mkdirs() }
        val temp = File(stateDir, "selection.json.tmp")
        temp.writeText(state.toString(), Charsets.UTF_8)
        check(temp.renameTo(File(stateDir, "selection.json"))) { "无法保存当前选曲" }
        return state
    }

    fun selection(persona: String? = null, related: Boolean = false, valence: Double = 0.0): JSONObject? = runCatching {
        val stateDir = stateDir(persona)
        val state = JSONObject(File(stateDir, "selection.json").readText(Charsets.UTF_8))
        val (_, file) = get(state.getString("id"), persona, related, valence) ?: return null
        val allowed = catalog(persona, related, valence)
        if (!file.isFile || (0 until allowed.length()).none { allowed.getJSONObject(it).optString("id") == state.optString("id") }) null
        else state.put("path", file.absolutePath)
    }.getOrNull()

    /** 内置精确 MIDI 随包提供；上传 MusicXML 使用本机转换器。参数改变时重建演奏副本。 */
    fun generateMidi(id: String, persona: String, related: Boolean, valence: Double, tools: MusicTools): JSONObject = synchronized(generationLock) {
        val (item, source) = get(id, persona, related, valence) ?: error("乐谱不属于当前人设的曲库")
        val folder = File(stateDir(persona), "generated").also { it.mkdirs() }
        val target = File(folder, "$id.mid"); val receipt = File(folder, "$id.mid.json")
        val signature = MusicTools.sha(source.readBytes()) + ":kotlin:" + listOf(Cfg.MUSIC_HUMANIZE_VERSION,
            Cfg.MUSIC_HUMANIZE_DRIFT_MS, Cfg.MUSIC_HUMANIZE_DRIFT_BEATS, Cfg.MUSIC_HUMANIZE_GROUP_MS,
            Cfg.MUSIC_HUMANIZE_FINGER_MS, Cfg.MUSIC_HUMANIZE_RELEASE_MS, Cfg.MUSIC_HUMANIZE_VELOCITY,
            Cfg.MUSIC_HUMANIZE_SIGMA_LIMIT, Cfg.MUSIC_HUMANIZE_IOI_FRACTION, Cfg.MUSIC_HUMANIZE_RELEASE_FRACTION).joinToString(":")
        val current = runCatching { val info = JSONObject(receipt.readText()); info.optString("signature") == signature &&
            info.optString("sha256") == MusicTools.sha(target.readBytes()) && MusicMidi.notes(MusicMidi.read(target.readBytes())).isNotEmpty() }.getOrDefault(false)
        if (!current) {
            val raw = if (!id.startsWith("upload-")) tools.engineFile("music_library/midi/$id.mid").readBytes() else tools.midi(source)
            val bytes = MusicMidi.humanize(raw)
            MusicTools.atomic(target, bytes)
            MusicTools.atomic(receipt, JSONObject().put("signature", signature).put("sha256", MusicTools.sha(bytes)).toString().toByteArray())
        }
        JSONObject().put("id", id).put("title", item.optString("title")).put("path", target.absolutePath).put("humanized", true).put("format", "midi")
    }

    fun preparePerformance(id: String, persona: String, related: Boolean, valence: Double, tools: MusicTools): JSONObject = synchronized(generationLock) {
        val result = generateMidi(id, persona, related, valence, tools)
        val midi = File(result.getString("path"))
        val target = File(midi.parentFile, "$id.wav"); val receipt = File(midi.parentFile, "$id.wav.json")
        val signature = MusicTools.sha(midi.readBytes()) + ":fluidsynth-2.6.2:" + Cfg.MUSIC_RENDER_VERSION + ":" + MusicTools.sha(tools.asset("piano.sf2").readBytes())
        val current = runCatching { val info = JSONObject(receipt.readText()); info.optString("signature") == signature && target.length() > 44 &&
            info.optString("sha256") == MusicTools.sha(target.readBytes()) }.getOrDefault(false)
        if (!current) {
            tools.render(midi, target)
            MusicTools.atomic(receipt, JSONObject().put("signature", signature).put("sha256", MusicTools.sha(target.readBytes())).toString().toByteArray())
        }
        result.put("path", target.absolutePath).put("format", "wav")
    }

    fun referenceMidi(source: File, tools: MusicTools): ByteArray {
        if (source.extension.lowercase() in setOf("mscx", "mscz")) {
            val item = (0 until manifest.length()).map { manifest.getJSONObject(it) }
                .firstOrNull { source.name == it.optString("file") } ?: throw IllegalArgumentException(
                    "安卓评判请使用 MIDI 或 MusicXML；用户上传的 MuseScore 文件请先在电脑转换")
            return tools.engineFile("music_library/midi/${item.getString("id")}.mid").readBytes()
        }
        return tools.midi(source)
    }

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
