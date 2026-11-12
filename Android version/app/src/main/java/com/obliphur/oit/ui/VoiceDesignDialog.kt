package com.obliphur.oit.ui

import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.obliphur.oit.core.ChatMsg
import com.obliphur.oit.OitApp
import com.obliphur.oit.core.Voice
import com.obliphur.oit.core.VoiceDesign
import com.obliphur.oit.core.tr
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID

/** 合成结果先试听，确认后写入父配置草稿；关闭时释放试听缓存和播放器。 */
@Composable
fun VoiceDesignDialog(profile: JSONObject, onUse: (String) -> Unit, onDismiss: () -> Unit) {
    val o = LocalOit.current
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var mode by remember { mutableStateOf("prompt") }
    var prompt by remember { mutableStateOf("") }
    var fileId by remember { mutableStateOf("") }
    var requested by remember { mutableStateOf("") }
    var words by remember { mutableStateOf(tr("你好，这是我的声音。")) }
    var selected by remember { mutableStateOf<Uri?>(null) }
    var filename by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("") }
    var result by remember { mutableStateOf<VoiceDesign.Result?>(null) }
    var resultWords by remember { mutableStateOf("") }
    val folder = remember { File(ctx.cacheDir, "voice_preview/${UUID.randomUUID()}") }
    val player = remember { Voice(folder) }
    player.playAllowed = { OitApp.instance.core.comm.voice.playAllowed() }
    DisposableEffect(player) { onDispose { player.close(); folder.deleteRecursively() } }
    LaunchedEffect(player) {
        while (true) {
            if (player.state == "failed") status = tr(player.error)
            delay(100)
        }
    }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) {
            selected = uri; fileId = ""
            filename = ctx.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
                if (it.moveToFirst()) it.getString(0) else ""
            }.orEmpty()
        }
    }
    fun create() {
        if (words.isBlank() || words.length > 500) {
            status = tr("请填写不超过 500 字的试听文本.")
            return
        }
        busy = true; status = tr("正在合成音色…")
        val p = JSONObject(profile.toString())
        val chosenMode = mode; val desc = prompt; val fid = fileId; val vid = requested; val preview = words
        val source = selected; val name = filename
        scope.launch {
            try {
                val (made, uploaded) = withContext(Dispatchers.IO) {
                    val uploaded = if (chosenMode == "clone" && fid.isBlank() && source != null) {
                        val bytes = ctx.contentResolver.openInputStream(source)?.use { input ->
                            val out = java.io.ByteArrayOutputStream()
                            val chunk = ByteArray(8192)
                            while (true) {
                                val n = input.read(chunk)
                                if (n < 0) break
                                require(out.size() + n <= 20 * 1024 * 1024) { tr("请选择不超过 20 MB 的 mp3、m4a 或 wav 录音（10 秒至 5 分钟）.") }
                                out.write(chunk, 0, n)
                            }
                            out.toByteArray()
                        } ?: error("audio")
                        VoiceDesign.upload(p, name, bytes)
                    } else fid
                    VoiceDesign.create(p, chosenMode, desc, uploaded, vid, preview) to uploaded
                }
                player.clear("preview")
                result = made; resultWords = preview; fileId = uploaded
                status = tr("音色已创建，可试听或使用.")
            } catch (e: kotlinx.coroutines.CancellationException) { throw e }
            catch (e: Exception) {
                status = if (e is IllegalArgumentException) e.message.orEmpty().replace(p.optString("api_key").ifEmpty { "\u0000" }, "***")
                    else tr("音色合成失败，请检查网络和配置.")
            } finally { busy = false }
        }
    }
    fun listen() {
        if (!player.playAllowed()) { status = tr("仅可在空闲或休息期间播放语音."); return }
        val made = result ?: return
        status = tr("音色已创建，可试听或使用.")
        val p = JSONObject(profile.toString()).put("id", "preview").put("voice_id", made.voiceId)
        player.data.put("enabled", true).put("profiles", JSONArray().put(p)).put("assign", JSONObject().put("preview", "preview"))
        val msg = ChatMsg(1, "ai", resultWords, "", uid = made.voiceId)
        if (made.audio.isNotEmpty()) {
            // 设计接口已返回试听，不再付费合成；通过同一缓存和播放器播放。
            player.cacheAudio("preview", msg, made.audio)
        }
        player.play("preview", msg)
    }
    AlertDialog(onDismissRequest = onDismiss, containerColor = o.bg, title = { Text(tr("音色合成")) },
        text = { Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row {
                TextButton({ mode = "prompt" }, enabled = !busy) { Text(if (mode == "prompt") "● " + tr("提示词模式") else tr("提示词模式")) }
                TextButton({ mode = "clone" }, enabled = !busy) { Text(if (mode == "clone") "● " + tr("克隆模式") else tr("克隆模式")) }
            }
            if (mode == "prompt") OutlinedTextField(prompt, { prompt = it }, label = { Text(tr("声音描述")) }, minLines = 3, modifier = Modifier.fillMaxWidth())
            else {
                Text(tr("录音文件（10 秒至 5 分钟，最大 20 MB）"))
                TextButton({ picker.launch("audio/*") }, enabled = !busy) { Text(tr("选择录音")) }
                if (filename.isNotBlank()) Text(filename)
                OutlinedTextField(fileId, { fileId = it }, label = { Text(tr("已有文件 ID（file_id，可代替上传）")) }, modifier = Modifier.fillMaxWidth())
            }
            OutlinedTextField(requested, { requested = it }, label = { Text(tr("新音色 ID（可留空）")) }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(words, { words = it }, label = { Text(tr("试听文本（最多 500 字）")) }, minLines = 2, modifier = Modifier.fillMaxWidth())
            result?.let { OutlinedTextField(it.voiceId, {}, readOnly = true, label = { Text(tr("生成的音色 ID（voice_id）")) }, modifier = Modifier.fillMaxWidth()) }
            if (status.isNotEmpty()) Text(status)
            Row {
                TextButton({ create() }, enabled = !busy) { Text(tr("合成音色")) }
                TextButton({ listen() }, enabled = result != null && !busy) { Text(tr("试听")) }
            }
        } }, confirmButton = { TextButton({ result?.let { onUse(it.voiceId) } }, enabled = result != null && !busy) { Text(tr("使用此音色")) } },
        dismissButton = { TextButton(onDismiss) { Text(tr("关闭")) } })
}
