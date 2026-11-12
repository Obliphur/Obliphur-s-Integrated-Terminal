package com.obliphur.oit.core

import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.util.UUID

/** 提示词设计和录音克隆只访问所选语音配置的同源接口。 */
object VoiceDesign {
    data class Result(val voiceId: String, val audio: ByteArray = byteArrayOf())
    private fun endpoint(p: JSONObject, operation: String): URL {
        val base = URI(p.optString("url").trim())
        require(base.scheme == "https" && !base.host.isNullOrBlank() && base.userInfo == null) { tr("语音 API 地址必须以 https:// 开头.") }
        val path = base.path.trimEnd('/').removeSuffix("/t2a_v2") + "/" + operation
        return URI(base.scheme, base.authority, path, base.query, null).toURL()
    }
    private fun call(p: JSONObject, operation: String, body: ByteArray, contentType: String = "application/json"): JSONObject {
        val key = p.optString("api_key").trim()
        require(key.isNotBlank()) { tr("请先填写 API Key.") }
        val conn = endpoint(p, operation).openConnection() as HttpURLConnection
        try {
            conn.instanceFollowRedirects = false; conn.requestMethod = "POST"; conn.doOutput = true
            conn.connectTimeout = 15000; conn.readTimeout = 90000
            conn.setRequestProperty("Authorization", "Bearer $key")
            conn.setRequestProperty("Content-Type", contentType)
            conn.outputStream.use { it.write(body) }
            require(conn.responseCode in 200..299) { tr("音色合成失败，请检查网络和配置.") }
            val data = JSONObject(conn.inputStream.bufferedReader(Charsets.UTF_8).use { it.readText() })
            val resp = data.optJSONObject("base_resp")
            if (resp != null && resp.optInt("status_code", 0) != 0) {
                val message = resp.optString("status_msg").take(300).replace(key, "***")
                throw IllegalArgumentException(tr("语音服务返回错误 {code}：{message}", "code" to resp.optInt("status_code").toString(), "message" to message))
            }
            return data
        } finally { conn.disconnect() }
    }
    fun upload(p: JSONObject, filename: String, audio: ByteArray): String {
        val ext = filename.substringAfterLast('.', "").lowercase()
        require(ext in listOf("mp3", "m4a", "wav") && audio.isNotEmpty() && audio.size <= 20 * 1024 * 1024) {
            tr("请选择不超过 20 MB 的 mp3、m4a 或 wav 录音（10 秒至 5 分钟）.")
        }
        val boundary = "oit" + UUID.randomUUID().toString().replace("-", "")
        val body = ByteArrayOutputStream().apply {
            write(("--$boundary\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\nvoice_clone\r\n" +
                "--$boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"voice.$ext\"\r\n" +
                "Content-Type: application/octet-stream\r\n\r\n").toByteArray())
            write(audio); write("\r\n--$boundary--\r\n".toByteArray())
        }.toByteArray()
        val id = call(p, "files/upload", body, "multipart/form-data; boundary=$boundary").optJSONObject("file")?.optString("file_id").orEmpty()
        require(id.matches(Regex("[0-9]+"))) { tr("语音服务没有返回有效的 file_id.") }
        return id
    }
    fun create(p: JSONObject, mode: String, prompt: String, fileId: String, requested: String, preview: String): Result {
        val id = requested.trim().ifBlank { "oit_voice_" + UUID.randomUUID().toString().replace("-", "") }
        require(id.matches(Regex("[A-Za-z][A-Za-z0-9_-]{6,254}[A-Za-z0-9]"))) {
            tr("新音色 ID 须为 8–256 位，以英文字母开头，仅含字母、数字、横线或下划线，末尾为字母或数字.")
        }
        if (mode == "prompt") {
            require(prompt.isNotBlank() && preview.isNotBlank() && preview.length <= 500) { tr("请填写声音描述和不超过 500 字的试听文本.") }
            val body = JSONObject().put("prompt", prompt.trim()).put("preview_text", preview.trim()).put("voice_id", id)
            val data = call(p, "voice_design", body.toString().toByteArray(Charsets.UTF_8))
            val actual = data.optString("voice_id")
            require(actual.isNotBlank()) { tr("语音服务没有返回有效的 voice_id.") }
            val hex = data.optString("trial_audio")
            require(hex.length % 2 == 0)
            return Result(actual, ByteArray(hex.length / 2) { hex.substring(it * 2, it * 2 + 2).toInt(16).toByte() })
        }
        require(mode == "clone" && fileId.trim().toLongOrNull()?.let { it > 0 } == true) { tr("请上传录音或填写有效的 file_id.") }
        val body = JSONObject().put("file_id", fileId.trim().toLong()).put("voice_id", id)
        call(p, "voice_clone", body.toString().toByteArray(Charsets.UTF_8))
        return Result(id)
    }
}
