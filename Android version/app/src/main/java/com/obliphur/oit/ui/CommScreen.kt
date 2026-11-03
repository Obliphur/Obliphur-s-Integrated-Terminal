package com.obliphur.oit.ui

import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.obliphur.oit.OitApp
import com.obliphur.oit.ai.Ai
import com.obliphur.oit.core.ChatMsg
import com.obliphur.oit.core.Comm
import com.obliphur.oit.core.CommData
import com.obliphur.oit.core.CommSel
import com.obliphur.oit.core.CommText
import com.obliphur.oit.core.parseIso
import com.obliphur.oit.core.tr

// ════════════════ 沟通 (MECHANICS 24) ════════════════

@Composable
fun CommScreen(go: (String) -> Unit) {
    val o = LocalOit.current
    val core = OitApp.instance.core
    val comm = core.comm
    DisposableEffect(Unit) { comm.setViewing(true); onDispose { comm.setViewing(false) } }
    var input by rememberSaveable { mutableStateOf("") }
    var askClear by remember { mutableStateOf(false) }
    var attach by remember { mutableStateOf(false) }
    // 不跟着每秒一次的 LocalTick 重画 (整页每秒重画一遍会有卡顿感): 只在聊天记录 / 状态变了才刷新
    var ver by remember { mutableIntStateOf(comm.version) }
    var waitHint by remember { mutableStateOf("") }
    var otherStatus by remember { mutableStateOf(comm.otherStatusDisplay().first) }
    var otherStatusKind by remember { mutableStateOf(comm.otherStatusDisplay().second) }
    LaunchedEffect(Unit) {
        while (true) {
            if (comm.version != ver) ver = comm.version
            comm.waitingText().let { if (it != waitHint) waitHint = it }
            val (text, kind) = comm.otherStatusDisplay()
            if (text != otherStatus) otherStatus = text
            if (kind != otherStatusKind) otherStatusKind = kind
            kotlinx.coroutines.delay(250)
        }
    }
    val msgs = remember(ver) { comm.visible() }
    val list = rememberLazyListState()
    // 倒排: 第 0 项是最新的一条, 在最下面. 新消息来了 (收到的 / 同步来的) 滑到最新; 正在往上翻很早的记录时不打断 (自己发的除外)
    LaunchedEffect(msgs.firstOrNull()?.id, msgs.size) {
        if (msgs.isNotEmpty() && (list.firstVisibleItemIndex <= 4 || msgs.last().role == "me")) list.scrollToItem(0)
    }
    fun report(why: String) {
        core.toast(tr(why), "warn")
        if (comm.blocked().isEmpty() && (why == "还没有选人设" || "API" in why || "Base URL" in why || "模型" in why)) go("settings:comm")
    }

    Column(Modifier.fillMaxSize().imePadding().padding(horizontal = 12.dp)) {
        Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            // 聊天对象: 换了就换成那个对象自己的聊天记录 (各聊各的)
            var whoOpen by remember { mutableStateOf(false) }
            Box(Modifier.weight(1f)) {
                TextButton({ whoOpen = true }) { Text("▾  " + comm.personaTitle(comm.personaName()).ifEmpty { "—" }, color = o.dim, fontSize = 14.sp) }
                androidx.compose.material3.DropdownMenu(whoOpen, { whoOpen = false }) {
                    for ((name, label) in comm.personaLabels()) androidx.compose.material3.DropdownMenuItem(
                        text = { Text(label, color = if (name == comm.personaName()) o.accent else o.text) },
                        onClick = { whoOpen = false; comm.set(persona = name) })
                }
            }
            // 模型: 和 AI 名单建议用同一份模型设置, 选了两边都生效
            ModelPicker({ comm.modelProfile() }) { comm.setModel(it) }
            TextButton({ if (comm.messages.isNotEmpty()) askClear = true }) { Text(tr("清空"), color = o.dim) }
        }
        // 对方现在在做什么 (按人设日程; 没有选人设时不显示): 小圆点的颜色跟着状态变
        if (otherStatus.isNotEmpty()) Row(Modifier.padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(8.dp).clip(CircleShape).background(
                Color(android.graphics.Color.parseColor(Comm.STATUS_COLORS[otherStatusKind] ?: "#9E9E9E"))))
            Text(otherStatus, color = o.dim, fontSize = 12.sp, modifier = Modifier.padding(start = 6.dp))
        }
        // 倒排 + 上下各留一点空白: 第一条 / 最后一条也能再滑过去一些, 新消息总是贴着底部
        LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = list, reverseLayout = true,
            contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 64.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(msgs.asReversed(), key = { it.id }) { Bubble(it) }
        }
        val blocked = comm.blocked()
        if (blocked.isNotEmpty() || comm.error.isNotEmpty()) Text(blocked.ifEmpty { tr(comm.error) }, color = o.danger, fontSize = 12.sp,
            modifier = Modifier.padding(top = 4.dp))
        else if (waitHint.isNotEmpty()) Text(tr(waitHint), color = o.dim, fontSize = 12.sp, modifier = Modifier.padding(top = 4.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            TextButton({ attach = true }, contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 8.dp, vertical = 0.dp)) {
                Text("📎", fontSize = 18.sp)
            }
            // 🔔 可以主动发起 / 🔕 禁止这个人主动发起 (主动发消息和追问都不发)
            TextButton({ comm.setMuted(!comm.isMuted()) }, contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 8.dp, vertical = 0.dp)) {
                Text(if (comm.isMuted()) "🔕" else "🔔", fontSize = 18.sp)
            }
            val attached = CommData.label(comm.attach)
            if (attached.isNotEmpty()) {
                Text(attached, color = o.accent, fontSize = 12.sp, modifier = Modifier.weight(1f, false).clickable { attach = true })
                TextButton({ comm.setAttach(CommSel()) }, contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 8.dp, vertical = 0.dp)) {
                    Text("✕", color = o.dim, fontSize = 14.sp)
                }
            }
        }
        Row(Modifier.fillMaxWidth().padding(bottom = 8.dp), verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(input, { input = it }, modifier = Modifier.weight(1f), maxLines = 4, enabled = blocked.isEmpty())
            AccentButton(tr("发送"), enabled = blocked.isEmpty()) {
                val why = comm.send(input)
                if (why.isEmpty()) input = "" else if (why != "empty") report(why)
            }
        }
    }
    if (attach) AttachDialog(comm.attachForm(), { attach = false }) { sel -> comm.setAttach(sel); attach = false }
    if (askClear) Confirm(tr("清空"), tr("清空聊天记录?"), true, { askClear = false }) { comm.clear() }
}

/** 📎 勾选要发送的数据: 今天的整点记录 / 专注计时, 日历 (选类别), 合约词条, 统计 (月数据) 的几页. */
@Composable
private fun AttachDialog(current: CommSel, onDismiss: () -> Unit, onOk: (CommSel) -> Unit) {
    val o = LocalOit.current
    val today = remember { mutableStateListOf<String>().apply { addAll(current.today) } }
    val stats = remember { mutableStateListOf<String>().apply { addAll(current.stats) } }
    val cal = remember { mutableStateListOf<String>().apply { addAll(current.cal) } }
    var contract by remember { mutableStateOf(current.contract) }
    @Composable
    fun box(label: String, on: Boolean, set: (Boolean) -> Unit) {
        Row(Modifier.fillMaxWidth().clickable { set(!on) }, verticalAlignment = Alignment.CenterVertically) {
            androidx.compose.material3.Checkbox(on, { set(it) },
                colors = androidx.compose.material3.CheckboxDefaults.colors(checkedColor = o.accent, checkmarkColor = o.onAccent))
            Text(label, color = o.text)
        }
    }
    fun toggle(list: MutableList<String>, key: String, on: Boolean) { if (on) list.add(key) else list.remove(key) }
    // 关掉窗口 (点外面 / 返回键) 也保存, 和"确定"一样
    AlertDialog(onDismissRequest = { onOk(CommSel(stats.toList(), cal.toList(), contract, today.toList())) }, containerColor = o.bg,
        title = { Text(tr("发送数据")) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Text(tr("🕒  今天"), fontWeight = FontWeight.Bold, color = o.text, modifier = Modifier.padding(top = 4.dp))
                for ((k, label) in CommData.TODAY_KEYS) box(tr(label), k in today) { toggle(today, k, it) }
                Text(tr("📅  日历"), fontWeight = FontWeight.Bold, color = o.text, modifier = Modifier.padding(top = 10.dp))
                for ((k, label) in CommData.CAL_KEYS) box(tr(label), k in cal) { toggle(cal, k, it) }
                Text(tr("⚔  合约"), fontWeight = FontWeight.Bold, color = o.text, modifier = Modifier.padding(top = 10.dp))
                box(tr("合约词条"), contract) { contract = it }
                // 统计 (月数据) 和它的子项放在最下面
                Text(tr("📊  " + CommData.STATS_NAME), fontWeight = FontWeight.Bold, color = o.text, modifier = Modifier.padding(top = 10.dp))
                for ((k, label) in CommData.STATS_KEYS) box(tr(label), k in stats) { toggle(stats, k, it) }
            }
        },
        confirmButton = { TextButton({ onOk(CommSel(stats.toList(), cal.toList(), contract, today.toList())) }) { Text(tr("确定"), color = o.accent) } })
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
        val names = comm.personaLabels()
        if (names.isNotEmpty()) Box(Modifier.padding(top = 8.dp)) {
            Segmented(names, comm.personaName()) { comm.set(persona = it) }
        }
    }

    OitCard {
        Text(tr("🧑  我的身份背景"), fontWeight = FontWeight.Bold, color = o.text)
        // 随时保存 (即时生效, 没有等待期), 发给对方时附在人设后面
        var profile by remember { mutableStateOf(comm.userProfile) }
        OutlinedTextField(profile, { profile = it.take(Comm.MAX_PROFILE); comm.set(userProfile = profile) },
            modifier = Modifier.fillMaxWidth().padding(top = 6.dp), minLines = 3, maxLines = 8)
    }

    OitCard {
        Text(tr("✂  分条发送"), fontWeight = FontWeight.Bold, color = o.text)
        SwitchRow(tr("开启"), comm.splitSend) { comm.set(splitSend = it) }
    }

    OitCard {
        Text(tr("💬  主动发起"), fontWeight = FontWeight.Bold, color = o.text)
        SwitchRow(tr("允许非正在聊天的人发起对话"), comm.bgPro) { comm.set(bgPro = it) }
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
                    if (comm.personaName().isEmpty()) core.toast(tr("还没有选人设"), "warn")
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
