package com.obliphur.oit.ui

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.obliphur.oit.OitApp
import com.obliphur.oit.core.I18n
import com.obliphur.oit.core.tr
import com.obliphur.oit.core.Voice
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

/** 配置帮助入口与平台链接，桌面和手机使用相同说明。 */
@Composable
fun ConfigHelpDialog(voice: Boolean, onDismiss: () -> Unit) {
    val o = LocalOit.current
    val ctx = LocalContext.current
    val url = if (!voice) "https://chuanapi.com/sign-up?aff=V4Gc" else
        if (I18n.isEn) "https://platform.minimax.io" else "https://platform.minimax.cn"
    AlertDialog(onDismissRequest = onDismiss, containerColor = o.bg, title = { Text(tr("帮助")) },
        text = { Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(tr(if (voice) VOICE_HELP else MODEL_HELP), color = o.text)
            TextButton({ ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }) { Text(url) }
        } }, confirmButton = { TextButton(onDismiss) { Text(tr("关闭")) } })
}

@Composable
fun VoiceSettingsCard() {
    val core = OitApp.instance.core
    val comm = core.comm
    val voice = comm.voice
    val o = LocalOit.current
    var enabled by remember(voice.data.optLong("ts")) { mutableStateOf(voice.enabled) }
    var auto by remember(voice.data.optLong("ts")) { mutableStateOf(voice.auto) }
    var gap by remember(voice.data.optLong("ts")) { mutableStateOf(voice.gap.toString()) }
    var dialog by remember { mutableStateOf(false) }
    fun save(vararg fields: Pair<String, Any>) {
        runCatching { voice.save(*fields); comm.onSync?.invoke() }.onFailure { core.toast(tr("保存失败."), "warn") }
    }
    OitCard {
        Text(tr("🔊  AI 语音"), color = o.text)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(tr("开启"), modifier = Modifier.weight(1f), color = o.text)
            androidx.compose.material3.Switch(enabled, { enabled = it; save("enabled" to it) })
        }
        if (enabled) {
            PlainButton(tr("AI 语音配置")) { dialog = true }
            val ps = voice.profiles().filter { it.optString("id") != Voice.CLAUDIA || Voice.isClaudia(comm.personaName()) }
            val choices = listOf("" to tr("未选择")) + ps.map { it.optString("id") to
                if (it.optString("id") == Voice.CLAUDIA) tr("Claudia 默认（专用）") else it.optString("name") }
            VoicePicker(tr("语音配置"), choices, voice.selected(comm.personaName())?.optString("id") ?: "") { id ->
                val assign = JSONObject(voice.data.getJSONObject("assign").toString()).put(comm.personaName(), id)
                save("assign" to assign)
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(tr("自动播放"), modifier = Modifier.weight(1f), color = o.text)
                androidx.compose.material3.Switch(auto, { auto = it; save("auto" to it) })
            }
            OutlinedTextField(gap, { text -> gap = text; text.toDoubleOrNull()?.takeIf { it.isFinite() && it in 0.0..60.0 }?.let { save("gap" to it) } },
                label = { Text(tr("播放间隔（秒）")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
        }
    }
    if (dialog) VoiceConfigDialog { dialog = false }
}

@Composable
private fun VoicePicker(label: String, choices: List<Pair<String, String>>, value: String, onPick: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Column {
        Text(label, color = LocalOit.current.dim)
        TextButton({ open = true }) { Text("▾  " + (choices.firstOrNull { it.first == value }?.second ?: value)) }
        DropdownMenu(open, { open = false }) {
            choices.forEach { (key, title) -> DropdownMenuItem(text = { Text(title) }, onClick = { open = false; onPick(key) }) }
        }
    }
}

@Composable
fun VoiceConfigDialog(onDismiss: () -> Unit) {
    val core = OitApp.instance.core
    val voice = core.comm.voice
    val o = LocalOit.current
    val ps = remember { mutableStateListOf<JSONObject>().apply { addAll(voice.profiles().map { JSONObject(it.toString()) }) } }
    var sel by remember { mutableStateOf(0) }
    var p by remember { mutableStateOf(JSONObject(ps[0].toString())) }
    var help by remember { mutableStateOf(false) }
    var design by remember { mutableStateOf(false) }
    var modelStatus by remember { mutableStateOf("") }
    var refreshModels by remember { mutableStateOf(0) }
    var models by remember(p.optString("id"), p.optString("url"), p.optString("api_key")) { mutableStateOf(Voice.MODELS) }
    val modelCache = remember { HashMap<List<String>, List<String>>() }
    LaunchedEffect(p.optString("id"), p.optString("url"), p.optString("api_key"), refreshModels) {
        val snapshot = JSONObject(p.toString())
        val key = listOf(snapshot.optString("id"), snapshot.optString("url"), snapshot.optString("api_key"))
        run {
            delay(700)
            modelStatus = tr("正在获取语音模型…")
            try {
                val found = modelCache[key] ?: withContext(Dispatchers.IO) { Voice.listModels(snapshot) }
                modelCache[key] = found
                models = found
                modelStatus = tr("已获取 MiniMax 官方语音模型")
            } catch (e: kotlinx.coroutines.CancellationException) { throw e }
            catch (_: Exception) { modelStatus = tr("未能获取语音模型列表，仍可选择预设型号或手动填写.") }
        }
    }
    fun load(index: Int) { ps[sel] = p; sel = index; p = JSONObject(ps[sel].toString()) }
    fun field(key: String, value: String) { p = JSONObject(p.toString()).put(key, value) }
    AlertDialog(onDismissRequest = onDismiss, containerColor = o.bg,
        title = { Row(verticalAlignment = Alignment.CenterVertically) {
            Text(tr("AI 语音配置"), modifier = Modifier.weight(1f))
            TextButton({ help = true }) { Text(tr("帮助")) }
        } },
        text = { Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            VoicePicker(tr("语音配置"), ps.mapIndexed { i, item -> i.toString() to
                if (item.optString("id") == Voice.CLAUDIA) tr("Claudia 默认（专用）") else item.optString("name") }, sel.toString()) { load(it.toInt()) }
            Row {
                TextButton({ ps[sel] = p; ps.add(Voice.profile(UUID.randomUUID().toString(), tr("新配置"))); load(ps.lastIndex) }) { Text(tr("新建")) }
                TextButton({ ps.removeAt(sel); sel = 0; p = JSONObject(ps[0].toString()) }, enabled = p.optString("id") != Voice.CLAUDIA) { Text(tr("删除")) }
            }
            OutlinedTextField(p.optString("name"), { field("name", it) }, label = { Text(tr("名称")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
            VoicePicker(tr("模板"), listOf("minimax" to "MiniMax"), "minimax") {}
            VoicePicker(tr("型号"), (listOf(p.optString("model")) + models).filter { it.isNotBlank() }.distinct().map { it to it }, p.optString("model")) { field("model", it) }
            if (modelStatus.isNotBlank()) Text(modelStatus)
            TextButton({ modelCache.clear(); refreshModels++ }) { Text(tr("重新获取模型")) }
            // 模型名称可手填，兼容平台之后发布的新型号。
            OutlinedTextField(p.optString("model"), { field("model", it) }, label = { Text(tr("型号")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(p.optString("voice_id"), { field("voice_id", it) }, label = { Text(tr("音色 ID")) },
                singleLine = true, modifier = Modifier.fillMaxWidth())
            TextButton({ design = true }) { Text(tr("音色合成")) }
            VoicePicker(tr("语音 API 地址"), listOf(Voice.CN_URL to Voice.CN_URL, Voice.GLOBAL_URL to Voice.GLOBAL_URL), p.optString("url")) { field("url", it) }
            OutlinedTextField(p.optString("url"), { field("url", it) }, label = { Text(tr("语音 API 地址")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(p.optString("api_key"), { field("api_key", it) }, label = { Text(tr("API Key")) }, singleLine = true,
                visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
        } },
        confirmButton = { TextButton({
            ps[sel] = p
            if (ps.any { it.optString("name").isBlank() }) core.toast(tr("请填写配置名称."), "warn")
            else runCatching { voice.save("profiles" to JSONArray(ps.toList())); core.comm.onSync?.invoke(); onDismiss() }
                .onFailure { core.toast(tr("保存失败."), "warn") }
        }) { Text(tr("保存")) } }, dismissButton = { TextButton(onDismiss) { Text(tr("取消")) } })
    if (help) ConfigHelpDialog(true) { help = false }
    if (design) VoiceDesignDialog(p, { id -> field("voice_id", id); design = false }, { design = false })
}

private const val MODEL_HELP = "1. 先注册一个提供 AI 服务的网站，充值后创建 API Key（相当于调用 AI 的密码）。\n2. 点「新建」，名称随便写，方便自己认出。用中转站就选「自定义」，Base URL 照网站的 API 文档复制。\n3. 按网站说明选择模型类型和接口协议；不确定时先用网站的 OpenAI 兼容协议。\n4. 点「获取模型列表」选择网站支持的模型，或复制网站给的模型名称。\n5. 把 API Key 粘贴进来并保存。聊天页选择这份配置即可；「设为默认」还会让规则页使用它。"
private const val VOICE_HELP = "1. 打开 MiniMax 平台，注册并开通语音服务，创建 API Key。\n2. 点「新建」，模板选 MiniMax，选择一个语音模型。HD 音质更好，Turbo 更快；费用以平台为准。\n3. 从平台的音色列表复制音色 ID；这是 voice_id，不是文件编号 file_id。尚无音色时，点「音色合成」，用提示词描述声音，或上传录音进行克隆。自定义音色需要当前 API 账号拥有使用权限。\n4. 粘贴 MiniMax 的 API Key。国内账号用国内 API 地址，国际账号用国际地址，不要填写平台网页地址。\n5. 保存后回到「设置 → 沟通」，为当前聊天对象选择这份语音配置。开启语音后，新消息会立即生成语音，点消息框左下角贴边的播放图标。\nClaudia 默认配置只给 Claudia 使用，音色已填好；仍需填写有权使用该音色的 API Key。"
