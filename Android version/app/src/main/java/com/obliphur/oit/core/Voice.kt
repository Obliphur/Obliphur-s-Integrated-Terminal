package com.obliphur.oit.core

import android.media.MediaPlayer
import android.os.Handler
import android.os.Looper
import com.obliphur.oit.ai.Ai
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.Executors
import java.util.concurrent.Future

/** MiniMax 配置、后台生成和本机播放记录；音频不随聊天同步。 */
class Voice(private val dir: File, private val generate: ((JSONObject, String) -> ByteArray)? = null) {
    companion object {
        val MODELS = listOf("speech-02-hd", "speech-02-turbo", "speech-2.6-hd", "speech-2.6-turbo")
        const val CN_URL = "https://api.minimaxi.com/v1/t2a_v2"
        const val GLOBAL_URL = "https://api.minimax.io/v1/t2a_v2"
        const val CLAUDIA = "claudia"
        fun isClaudia(name: String) = name.equals("Claudia.txt", true)
        fun profile(id: String, name: String) = JSONObject().put("id", id).put("name", name).put("template", "minimax")
            .put("model", MODELS[0]).put("voice_id", if (id == CLAUDIA) Cfg.AI_VOICE_CLAUDIA_ID else "")
            .put("url", CN_URL).put("api_key", "")
        private fun hash(text: String) = MessageDigest.getInstance("SHA-256").digest(text.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it.toInt() and 255) }
    }
    private val cfg = File(dir, "ai_voice.json")
    private val pool = Executors.newFixedThreadPool(2) { r -> Thread(r, "oit-voice").apply { isDaemon = true } }
    private val jobs = HashMap<String, Future<File?>>()
    private val epochs = HashMap<String, Int>()
    private val main = Handler(Looper.getMainLooper())
    private var player: MediaPlayer? = null
    private var token = 0
    private var closed = false
    @Volatile var state = "idle"; private set
    @Volatile var error = ""
    var data = JSONObject().put("ts", 0L).put("enabled", false).put("auto", false).put("gap", Cfg.AI_VOICE_GAP)
        .put("profiles", JSONArray().put(profile(CLAUDIA, "Claudia"))).put("assign", JSONObject())
        private set
    init {
        Store.readObj(cfg)?.let { raw ->
            raw.optJSONArray("profiles")?.let { ps -> for (i in 0 until ps.length()) ps.getJSONObject(i).let {
                it.put("api_key", Ai.decryptKey(it.optString("api_key")))
            } }
            data = raw
        }
        normalize()
    }
    val enabled get() = data.optBoolean("enabled")
    val auto get() = data.optBoolean("auto")
    val gap get() = data.optDouble("gap", Cfg.AI_VOICE_GAP).coerceIn(0.0, 60.0)
    fun profiles(): List<JSONObject> = data.optJSONArray("profiles")?.let { a -> (0 until a.length()).map { a.getJSONObject(it) } } ?: emptyList()
    private fun normalize() {
        val ps = profiles().toMutableList()
        if (ps.none { it.optString("id") == CLAUDIA }) ps.add(0, profile(CLAUDIA, "Claudia"))
        ps.filter { it.optString("id") == CLAUDIA }.forEach { it.put("voice_id", Cfg.AI_VOICE_CLAUDIA_ID) }
        data.put("profiles", JSONArray(ps)).put("gap", gap)
        if (data.optJSONObject("assign") == null) data.put("assign", JSONObject())
    }
    private fun write() {
        val raw = JSONObject(data.toString())
        val ps = raw.getJSONArray("profiles")
        for (i in 0 until ps.length()) ps.getJSONObject(i).let { it.put("api_key", Ai.encryptKey(it.optString("api_key"))) }
        check(Store.write(cfg, raw))
    }
    @Synchronized fun save(vararg fields: Pair<String, Any>) {
        for ((key, value) in fields) data.put(key, value)
        normalize()
        data.put("ts", maxOf(System.currentTimeMillis(), data.optLong("ts") + 1))
        write()
        if (!enabled) stop()
    }
    @Synchronized fun syncDoc() = JSONObject(data.toString())
    @Synchronized fun applyDoc(raw: JSONObject) {
        if (raw.optLong("ts") <= data.optLong("ts")) return
        data = JSONObject(raw.toString()); normalize(); write()
        if (!enabled) stop()
    }
    fun selected(name: String): JSONObject? {
        val id = data.optJSONObject("assign")?.optString(name, if (isClaudia(name)) CLAUDIA else "") ?: ""
        return profiles().firstOrNull { it.optString("id") == id && (id != CLAUDIA || isClaudia(name)) }
    }
    private fun folder(name: String) = File(dir, "voice_cache/${hash(name)}")
    private fun audio(name: String, m: ChatMsg) = File(folder(name), hash(name + "\u0000" + m.uid.ifEmpty { m.id.toString() } + "\u0000" + m.text) + ".mp3")
    fun played(name: String, m: ChatMsg) = File(audio(name, m).path + ".played").exists()
    fun autoplay(name: String, msgs: List<ChatMsg>) = msgs.filter { it.role == "ai" && !played(name, it) }.takeLast(Cfg.AI_VOICE_AUTO_COUNT)

    @Synchronized fun request(name: String, m: ChatMsg): Future<File?>? {
        if (!enabled || closed || m.role != "ai") return null
        val f = audio(name, m)
        if (f.exists()) return java.util.concurrent.CompletableFuture.completedFuture(f)
        jobs[f.path]?.let { if (!it.isDone || runCatching { it.get() }.getOrNull() != null) return it }
        val p = selected(name)?.let { JSONObject(it.toString()) } ?: return null
        val epoch = epochs.getOrPut(name) { 0 }
        val future = pool.submit<File?> {
            try {
                val bytes = generate?.invoke(p, m.text) ?: synthesize(p, m.text)
                synchronized(this) {
                    if (closed || epoch != epochs[name]) return@submit null
                    f.parentFile?.mkdirs(); f.writeBytes(bytes)
                }
                f
            } catch (_: Exception) {
                error = "语音生成失败，请检查网络和语音配置."
                null
            }
        }
        jobs[f.path] = future
        return future
    }
    private fun synthesize(p: JSONObject, text: String): ByteArray {
        require(listOf("api_key", "model", "voice_id").all { p.optString(it).isNotBlank() })
        val url = p.optString("url", CN_URL).trim()
        require(url.startsWith("https://"))
        val body = JSONObject().put("model", p.getString("model")).put("text", text).put("stream", false).put("output_format", "hex")
            .put("voice_setting", JSONObject().put("voice_id", p.getString("voice_id")))
            .put("audio_setting", JSONObject().put("sample_rate", 32000).put("bitrate", 128000).put("format", "mp3").put("channel", 1))
        val conn = URL(url).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = "POST"; conn.doOutput = true; conn.connectTimeout = 15000; conn.readTimeout = 90000
            conn.instanceFollowRedirects = false
            conn.setRequestProperty("Authorization", "Bearer " + p.getString("api_key"))
            conn.setRequestProperty("Content-Type", "application/json")
            conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            require(conn.responseCode in 200..299)
            val response = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
            require((response.optJSONObject("base_resp")?.optInt("status_code", 0) ?: 0) == 0)
            val hex = response.getJSONObject("data").getString("audio")
            require(hex.isNotEmpty() && hex.length % 2 == 0)
            val bytes = ByteArray(hex.length / 2) { hex.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
            require(bytes[0].toInt() and 255 == 255 || bytes.take(3).toByteArray().contentEquals("ID3".toByteArray()))
            return bytes
        } finally { conn.disconnect() }
    }
    @Synchronized fun stop() {
        token++; state = "idle"
        val old = player; player = null
        main.post { runCatching { old?.release() } }
    }
    @Synchronized fun play(name: String, m: ChatMsg) {
        stop(); val seq = token; state = "loading"; error = ""
        val job = request(name, m)
        Thread {
            val file = runCatching { job?.get() }.getOrNull()
            main.post {
                synchronized(this) {
                    if (seq != token || closed || !enabled) return@post
                    if (file == null) { state = "failed"; if (error.isEmpty()) error = "请先填写语音配置的模型、音色 ID 和 API Key."; return@post }
                    runCatching {
                        val mp = MediaPlayer(); player = mp
                        mp.setDataSource(file.path)
                        mp.setOnPreparedListener {
                            synchronized(this) {
                                if (seq != token) return@setOnPreparedListener
                                mp.start(); File(file.path + ".played").writeText(""); state = "playing"
                            }
                        }
                        mp.setOnCompletionListener { if (seq == token) { state = "done"; player = null; it.release() } }
                        mp.setOnErrorListener { _, _, _ -> if (seq == token) { state = "failed"; error = "语音播放失败."; player = null; mp.release() }; true }
                        mp.prepareAsync()
                    }.onFailure { state = "failed"; error = "语音播放失败."; player?.release(); player = null }
                }
            }
        }.apply { isDaemon = true }.start()
    }
    @Synchronized fun clear(name: String) {
        stop(); epochs[name] = (epochs[name] ?: 0) + 1
        jobs.keys.removeAll { it.startsWith(folder(name).path + File.separator) }
        folder(name).deleteRecursively()
    }
    @Synchronized fun close() { stop(); closed = true; pool.shutdownNow() }
}
