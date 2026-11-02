package com.obliphur.oit.ui

import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.obliphur.oit.OitApp
import com.obliphur.oit.ai.Ai
import com.obliphur.oit.core.ChatMsg
import com.obliphur.oit.core.CommText
import com.obliphur.oit.core.parseIso
import com.obliphur.oit.core.tr

// ════════════════ 沟通 (MECHANICS 24) ════════════════

@Composable
fun CommScreen(go: (String) -> Unit) {
    LocalTick.current
    val o = LocalOit.current
    val core = OitApp.instance.core
    val comm = core.comm
    DisposableEffect(Unit) { comm.setViewing(true); onDispose { comm.setViewing(false) } }
    var input by rememberSaveable { mutableStateOf("") }
    var askClear by remember { mutableStateOf(false) }
    val msgs = comm.visible()
    val list = rememberLazyListState()
    LaunchedEffect(comm.version) { if (msgs.isNotEmpty()) list.scrollToItem(msgs.size - 1) }

    Column(Modifier.fillMaxSize().imePadding().padding(horizontal = 12.dp)) {
        Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(comm.personaTitle(comm.personaName()), color = o.dim, fontSize = 14.sp, modifier = Modifier.weight(1f))
            TextButton({ if (comm.messages.isNotEmpty()) askClear = true }) { Text(tr("清空"), color = o.dim) }
        }
        LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = list, verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(msgs, key = { it.id }) { Bubble(it) }
        }
        val status = when {
            comm.busy -> tr("对方正在输入…")
            comm.error.isNotEmpty() -> tr(comm.error)
            else -> ""
        }
        if (status.isNotEmpty()) Text(status, color = if (comm.busy) o.dim else o.danger, fontSize = 12.sp,
            modifier = Modifier.padding(top = 4.dp))
        Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(input, { input = it }, modifier = Modifier.weight(1f), maxLines = 4)
            AccentButton(tr("发送")) {
                val why = comm.send(input)
                if (why.isEmpty()) input = ""
                else if (why != "empty") { core.toast(tr(why), "warn"); go("settings:comm") }
            }
        }
    }
    if (askClear) Confirm(tr("清空"), tr("清空聊天记录?"), true, { askClear = false }) { comm.clear() }
}

@Composable
private fun Bubble(m: ChatMsg) {
    val o = LocalOit.current
    val comm = OitApp.instance.core.comm
    val mine = m.role == "me"
    val when_ = parseIso(m.at)?.let { "%02d:%02d".format(it.hour, it.minute) } ?: ""
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (mine) Arrangement.End else Arrangement.Start,
        verticalAlignment = Alignment.Top) {
        if (comm.showAvatar && !mine) Avatar("ai", comm.personaTitle(comm.personaName()).take(1).ifEmpty { "?" }, 34)
        Column(Modifier.widthIn(max = 280.dp).padding(horizontal = 6.dp), horizontalAlignment = if (mine) Alignment.End else Alignment.Start) {
            Text(m.text, color = if (mine) o.onAccent else o.text, fontSize = 15.sp,
                modifier = Modifier
                    .background(if (mine) o.accent else o.card, RoundedCornerShape(12.dp))
                    .then(if (mine) Modifier else Modifier.border(1.dp, o.border, RoundedCornerShape(12.dp)))
                    .padding(horizontal = 12.dp, vertical = 8.dp))
            Text(when_, color = o.dim, fontSize = 11.sp)
        }
        if (comm.showAvatar && mine) Avatar("me", OitApp.instance.core.username.take(1).ifEmpty { "我" }, 34)
    }
}

/** 头像: 选过图片就显示图片 (圆形), 否则是名字的第一个字. */
@Composable
private fun Avatar(who: String, fallback: String, size: Int) {
    val o = LocalOit.current
    val comm = OitApp.instance.core.comm
    val mine = who == "me"
    val file = comm.avatarPath(who)
    val image = remember(file?.path, file?.lastModified()) {
        file?.let { runCatching { android.graphics.BitmapFactory.decodeFile(it.path)?.asImageBitmap() }.getOrNull() }
    }
    if (image != null) {
        androidx.compose.foundation.Image(image, null, Modifier.size(size.dp).clip(CircleShape))
        return
    }
    Box(Modifier.size(size.dp).background(if (mine) o.accent else o.card, CircleShape).border(1.dp, o.border, CircleShape),
        contentAlignment = Alignment.Center) {
        Text(fallback, color = if (mine) o.onAccent else o.text, fontWeight = FontWeight.Bold, fontSize = 14.sp)
    }
}

// ════════════════ 设置 → 沟通: API, 人设, 默认发送的信息, 头像 (即时保存, 没有等待期) ════════════════

@Composable
fun CommSettings() {
    LocalTick.current
    val o = LocalOit.current
    val ctx = LocalContext.current
    val core = OitApp.instance.core
    val comm = core.comm
    var apiDialog by remember { mutableStateOf(false) }
    var readme by remember { mutableStateOf(false) }
    var avatarWho by remember { mutableStateOf("me") }
    var cropUri by remember { mutableStateOf<android.net.Uri?>(null) }
    val pickImage = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri -> if (uri != null) cropUri = uri }

    val pick = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        val name = runCatching {
            ctx.contentResolver.query(uri, null, null, null, null)?.use { c ->
                if (c.moveToFirst()) c.getString(c.getColumnIndexOrThrow(OpenableColumns.DISPLAY_NAME)) else null
            }
        }.getOrNull() ?: "persona.txt"
        val saved = runCatching { ctx.contentResolver.openInputStream(uri)?.use { comm.importPersona(name, it) } }.getOrNull() ?: ""
        if (saved.isEmpty()) core.toast(tr("只能导入 .txt / .md 文件."), "warn") else comm.set(persona = saved)
    }

    OitCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("🔑  API", fontWeight = FontWeight.Bold, color = o.text, modifier = Modifier.weight(1f))
            PlainButton(tr("设置")) { apiDialog = true }
        }
        val cfg = comm.aiConfig()
        val ok = runCatching { Ai.checkProfile(cfg.profile()) }.isSuccess
        Text("${tr(Ai.PROVIDERS[cfg.provider]?.label ?: "")} · ${cfg.profile().model.ifEmpty { "—" }}  ${if (ok) "✓" else "✗"}",
            color = o.text, fontSize = 13.sp, modifier = Modifier.padding(top = 6.dp))
    }

    OitCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(tr("🎭  人设"), fontWeight = FontWeight.Bold, color = o.text, modifier = Modifier.weight(1f))
            PlainButton(tr("写法")) { readme = true }
            Box(Modifier.size(6.dp))
            PlainButton(tr("导入")) { pick.launch(arrayOf("*/*")) }
        }
        val names = comm.listPersonas()
        if (names.isNotEmpty()) Box(Modifier.padding(top = 8.dp)) {
            Segmented(names.map { it to it }, comm.personaName()) { comm.set(persona = it) }
        }
    }

    OitCard {
        Text(tr("📨  默认发送的信息"), fontWeight = FontWeight.Bold, color = o.text)
        SwitchRow(tr("整点记录"), comm.sendHour) { comm.set(sendHour = it) }
        SwitchRow(tr("计时器状态"), comm.sendTimer) { comm.set(sendTimer = it) }
    }

    OitCard {
        Text(tr("🖼  头像"), fontWeight = FontWeight.Bold, color = o.text)
        SwitchRow(tr("显示头像"), comm.showAvatar) { comm.set(showAvatar = it) }
        if (comm.showAvatar) for ((who, label) in listOf("me" to tr("我"), "ai" to tr("对方"))) {
            Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(label, color = o.text, modifier = Modifier.width(48.dp))
                Avatar(who, "—", 40)
                Box(Modifier.weight(1f))
                PlainButton(tr("选择")) {
                    if (who == "ai" && comm.personaName().isEmpty()) core.toast(tr("还没有选人设"), "warn")
                    else { avatarWho = who; pickImage.launch("image/*") }
                }
                if (comm.avatarPath(who) != null) {
                    Box(Modifier.size(6.dp))
                    PlainButton(tr("移除")) { comm.removeAvatar(who) }
                }
            }
        }
    }
    cropUri?.let { u ->
        AvatarCropDialog(u) { bmp ->
            cropUri = null
            if (bmp != null && !comm.saveAvatar(avatarWho, bmp)) core.toast(tr("保存头像失败."), "warn")
        }
    }

    if (apiDialog) AiSettingsDialog { apiDialog = false }
    if (readme) AlertDialog(onDismissRequest = { readme = false }, containerColor = o.bg,
        title = { Text(tr("🎭  人设")) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Text(CommText.readme(), color = o.text, fontSize = 13.sp)
                Text(comm.personas.path, color = o.dim, fontSize = 11.sp, modifier = Modifier.padding(top = 8.dp))
            }
        },
        confirmButton = { TextButton({ readme = false }) { Text(tr("关闭"), color = o.accent) } })
}

@Composable
private fun SwitchRow(label: String, value: Boolean, onChange: (Boolean) -> Unit) {
    val o = LocalOit.current
    Row(Modifier.fillMaxWidth().padding(top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = o.text, modifier = Modifier.weight(1f))
        Switch(value, onChange, colors = SwitchDefaults.colors(checkedTrackColor = o.accent, checkedThumbColor = o.onAccent))
    }
}
