package com.obliphur.oit.ui

import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.IconButton
import com.obliphur.oit.core.Cfg
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.ui.window.Dialog
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
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.withContext
import java.io.File
import java.util.UUID
import com.obliphur.oit.OitApp
import com.obliphur.oit.ai.Ai
import com.obliphur.oit.core.ChatMsg
import com.obliphur.oit.core.Comm
import com.obliphur.oit.core.CommData
import com.obliphur.oit.core.CommSel
import com.obliphur.oit.core.CommShift
import com.obliphur.oit.core.CommText
import com.obliphur.oit.core.parseIso
import com.obliphur.oit.core.tr

// ════════════════ 沟通 (MECHANICS 24) ════════════════

@Composable
fun CommScreen(go: (String) -> Unit) {
    val o = LocalOit.current
    val core = OitApp.instance.core
    val comm = core.comm
    val context = LocalContext.current
    var readingSession by remember { mutableStateOf(false) }
    var readingSessionId by remember { mutableStateOf("") }
    LaunchedEffect(comm.coreadUrl, comm.coreadAccessKey) {
        while (isActive) {
            val bridgeState = if (comm.coreadUrl.isNotEmpty() && comm.coreadAccessKey.isNotEmpty()) withContext(Dispatchers.IO) {
                runCatching {
                    val priorSession = readingSessionId
                    if (priorSession.isNotEmpty()) {
                        val heartbeat = java.net.URL("${comm.coreadUrl.trimEnd('/')}/v1/oit-chat/heartbeat").openConnection() as java.net.HttpURLConnection
                        heartbeat.connectTimeout = 1800; heartbeat.readTimeout = 1800
                        heartbeat.requestMethod = "POST"; heartbeat.doOutput = true
                        heartbeat.setRequestProperty("Content-Type", "application/json")
                        heartbeat.setRequestProperty("x-coread-key", comm.coreadAccessKey)
                        heartbeat.outputStream.use { it.write(org.json.JSONObject().put("session_id", priorSession).toString().toByteArray()) }
                        heartbeat.inputStream.close(); heartbeat.disconnect()
                    }
                    val connection = java.net.URL("${comm.coreadUrl.trimEnd('/')}/v1/oit-chat/state").openConnection() as java.net.HttpURLConnection
                    connection.connectTimeout = 1800; connection.readTimeout = 1800
                    connection.setRequestProperty("x-coread-key", comm.coreadAccessKey)
                    try { org.json.JSONObject(connection.inputStream.bufferedReader().use { it.readText() }) }
                    finally { connection.disconnect() }
                }.getOrNull()
            } else null
            readingSessionId = bridgeState?.optString("session_id").orEmpty()
            val active = (bridgeState?.optBoolean("active") ?: false) &&
                bridgeState?.optString("persona_pid") == comm.pid
            readingSession = active
            comm.readingMode = active
            comm.coreadSessionId = if (active) readingSessionId else ""
            delay(1600)
        }
    }
    DisposableEffect(comm) {
        comm.onCoreadEnd = {
            val sessionId = readingSessionId
            val url = comm.coreadUrl
            val key = comm.coreadAccessKey
            Thread {
                runCatching {
                    val connection = java.net.URL("${url.trimEnd('/')}/v1/oit-chat/end").openConnection() as java.net.HttpURLConnection
                    connection.requestMethod = "POST"; connection.doOutput = true
                    connection.setRequestProperty("Content-Type", "application/json")
                    connection.setRequestProperty("x-coread-key", key)
                    connection.outputStream.use { it.write(org.json.JSONObject().put("session_id", sessionId).toString().toByteArray()) }
                    connection.inputStream.close(); connection.disconnect()
                }
            }.apply { isDaemon = true }.start()
        }
        onDispose { comm.onCoreadEnd = null }
    }
    fun openCoread(together: Boolean = true) {
        if (together && comm.pid.isBlank()) {
            core.toast(tr("请先选择聊天对象，再开始共读."), "warn")
            return
        }
        if (comm.coreadUrl.isBlank() || comm.coreadAccessKey.isBlank()) {
            core.toast(tr("请先在 Windows 的沟通页点击「阅读」启动阅读服务."), "warn")
            return
        }
        val mode = if (together) "coread" else "solo"
        val personaName = if (together) "&persona_name=${java.net.URLEncoder.encode(comm.aiName(), "UTF-8")}" else ""
        val url = "${comm.coreadUrl.trimEnd('/')}/?reading_mode=$mode&owner_key=${comm.coreadAccessKey}&access_key=${comm.coreadAccessKey}&persona_pid=${java.net.URLEncoder.encode(comm.pid, "UTF-8")}$personaName"
        runCatching { context.startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(url))) }
            .onFailure { core.toast(tr("无法打开共读网页."), "warn") }
    }
    DisposableEffect(comm) {
        comm.onCoreadOpen = { openCoread(true) }
        onDispose { comm.onCoreadOpen = null }
    }
    DisposableEffect(Unit) { comm.setViewing(true); onDispose { comm.voice.stop(); comm.setViewing(false) } }
    var input by rememberSaveable { mutableStateOf("") }
    LaunchedEffect(OitApp.instance.commDraft) {
        if (OitApp.instance.commDraft.isNotEmpty()) {
            input = OitApp.instance.commDraft
            OitApp.instance.commDraft = ""
        }
    }
    var askClear by remember { mutableStateOf(false) }
    var help by remember { mutableStateOf(false) }
    var readingChoice by remember { mutableStateOf(false) }
    var musicReview by remember { mutableStateOf(false) }
    var coAlarm by remember { mutableStateOf(false) }                           // 共时闹钟的弹窗
    var shiftDlg by remember { mutableStateOf(false) }                          // 起点平移的弹窗
    var shiftAsk by remember { mutableStateOf(comm.shiftAsking()) }              // 请求了起点平移, 等对方回复 (⏳)
    var jointAsk by remember { mutableStateOf(core.jointAsk) }                  // 合约页选好词条回来了: 输入开始时间
    var jointLine by remember { mutableStateOf(comm.packageDraftLabel() to comm.jointStatusText()) }
    var invite by remember { mutableStateOf(comm.jointInvite() != null) }          // 对方发起的联合行动: 等用户点 接受 / 拒绝
    var shareOn by remember { mutableStateOf(comm.shareOn()) }                     // share data 开着吗
    var shareAsk by remember { mutableStateOf(comm.shareAsking()) }                 // 已发送 share data 请求, 等对方同意 (⏳)
    var shareInv by remember { mutableStateOf(comm.shareInvite()) }                // 对方发了 [share data]: 等用户点 接受 / 拒绝
    var scenarioOn by remember { mutableStateOf(comm.scenarioOn()) }
    var scenarioAsk by remember { mutableStateOf(comm.scenarioAsking()) }
    var scenarioInv by remember { mutableStateOf(comm.scenarioInvite()) }
    var loveInv by remember { mutableStateOf(comm.loveInvite()) }                  // 对方告白了: 等用户点 接受 / 拒绝
    var coreadInv by remember { mutableStateOf(comm.coreadInvite()) }
    var hasConsentRules by remember { mutableStateOf(comm.burstConsentRulesFor().isNotEmpty()) }
    var consentPending by remember { mutableStateOf(comm.burstConsentPending()) }
    var consentDlg by remember { mutableStateOf(false) }
    var consentPrompted by remember { mutableStateOf<Pair<String, Long>?>(null) }
    var ocrBusy by remember { mutableStateOf(false) }                          // 识别字体进行中
    var pendingImage by remember { mutableStateOf<ByteArray?>(null) }
    val pendingPreview = remember(pendingImage) { pendingImage?.let(::decodePreviewImage) }
    val ocrCtx = androidx.compose.ui.platform.LocalContext.current
    // 🖼 识别字体: 选一张图片, 让模型读出上面的文字, 放进聊天框 (图片本身不会发出去, 也不会保存)
    val pickOcr = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        ocrBusy = true
        core.toast(tr("正在识别图片上的文字…"))
        Thread {
            var text = ""; var err = ""
            try { text = comm.ocrImage(loadOcrImage(ocrCtx, uri)) }
            catch (e: com.obliphur.oit.ai.AiError) { err = e.message ?: "" }
            catch (e: Exception) { err = e.javaClass.simpleName }
            android.os.Handler(android.os.Looper.getMainLooper()).post {
                ocrBusy = false
                if (err.isNotEmpty()) core.toast(tr(err), "warn")
                else if (text.isBlank()) core.toast(tr("图片上没有识别到文字."), "warn")
                else { input = if (input.isBlank()) text else input + "\n" + text; core.toast(tr("文字已放进聊天框.")) }
            }
        }.apply { isDaemon = true }.start()
    }
    val pickChatImage = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        try {
            pendingImage = loadOcrImage(ocrCtx, uri)
        } catch (error: Exception) {
            core.toast(tr(error.message ?: "打不开这张图片."), "warn")
        }
    }
    var cameraFile by remember { mutableStateOf<File?>(null) }
    val takePicture = rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
        val file = cameraFile
        cameraFile = null
        try {
            if (ok && file != null && file.isFile) {
                pendingImage = file.readBytes()
            }
        } catch (error: Exception) {
            core.toast(tr(error.message ?: "拍照失败."), "warn")
        } finally { file?.delete() }
    }
    fun openCamera() {
        try {
            val dir = File(context.cacheDir, "comm_camera").apply { mkdirs() }
            val file = File(dir, "camera-${UUID.randomUUID()}.jpg")
            val uri = androidx.core.content.FileProvider.getUriForFile(context, "${context.packageName}.files", file)
            cameraFile = file
            takePicture.launch(uri)
        } catch (error: Exception) {
            cameraFile = null
            core.toast(tr(error.message ?: "无法打开相机."), "warn")
        }
    }
    val pickScore = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        val scoreOwner = comm.personaName()
        Thread {
            var imported = ""; var error = ""
            var staged: java.io.File? = null
            try {
                val name = ocrCtx.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
                    if (cursor.moveToFirst()) cursor.getString(0) else null
                } ?: "score.mscx"
                val size = ocrCtx.contentResolver.query(uri, arrayOf(OpenableColumns.SIZE), null, null, null)?.use { cursor ->
                    if (cursor.moveToFirst()) cursor.getLong(0) else -1L
                } ?: -1L
                require(size <= 50L * 1024 * 1024) { "乐谱文件不存在或超过50 MiB" }
                val ext = name.substringAfterLast('.', "mscx").lowercase()
                require(ext in setOf("mscx", "mscz", "mxl", "musicxml", "xml")) { "仅支持 MuseScore 和 MusicXML 乐谱" }
                staged = java.io.File(ocrCtx.cacheDir, "music-score-import.$ext")
                ocrCtx.contentResolver.openInputStream(uri)?.use { input -> staged!!.outputStream().use { output -> input.copyTo(output) } }
                    ?: error("无法读取乐谱文件")
                imported = comm.importScore(staged!!, scoreOwner).optString("title")
            } catch (e: Exception) { error = e.message ?: e.javaClass.simpleName }
            finally { staged?.delete() }
            android.os.Handler(android.os.Looper.getMainLooper()).post {
                if (error.isNotEmpty()) core.toast(tr(error), "warn") else core.toast(tr("已加入曲库: {title}", "title" to imported))
            }
        }.apply { isDaemon = true }.start()
    }
    // 不跟着每秒一次的 LocalTick 重画 (整页每秒重画一遍会有卡顿感): 只在聊天记录 / 状态变了才刷新
    var ver by remember { mutableIntStateOf(comm.version) }
    var otherStatus by remember { mutableStateOf(comm.otherStatusDisplay().first) }
    var tensionText by remember { mutableStateOf(comm.tensionDisplay(core.settings.showTension)) }
    var otherStatusKind by remember { mutableStateOf(comm.otherStatusDisplay().second) }
    var voiceSession by remember { mutableStateOf(0) }
    var voiceAutoRunning by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        while (true) {
            if (comm.voice.state == "failed" && comm.voice.error.isNotEmpty()) { core.toast(tr(comm.voice.error), "warn"); comm.voice.error = "" }
            if ((comm.blocked().isNotEmpty() || !comm.voice.playAllowed()) && comm.voice.state in listOf("playing", "loading")) comm.voice.stop()
            if (!comm.voice.playAllowed() && voiceAutoRunning) { voiceSession++; voiceAutoRunning = false }
            if (comm.version != ver) ver = comm.version
            if (coreadInv != comm.coreadInvite()) coreadInv = comm.coreadInvite()
            val (text, kind) = comm.otherStatusDisplay()
            if (text != otherStatus) otherStatus = text
            if (kind != otherStatusKind) otherStatusKind = kind
            comm.tensionDisplay(core.settings.showTension).let { if (it != tensionText) tensionText = it }
            if (core.jointAsk != jointAsk) jointAsk = core.jointAsk
            (comm.packageDraftLabel() to comm.jointStatusText()).let { if (it != jointLine) jointLine = it }
            (comm.jointInvite() != null).let { if (it != invite) invite = it }
            comm.shareOn().let { if (it != shareOn) shareOn = it }
            comm.shareAsking().let { if (it != shareAsk) shareAsk = it }
            comm.shiftAsking().let { if (it != shiftAsk) shiftAsk = it }
            comm.shareInvite().let { if (it != shareInv) shareInv = it }
            comm.scenarioOn().let { if (it != scenarioOn) scenarioOn = it }
            comm.scenarioAsking().let { if (it != scenarioAsk) scenarioAsk = it }
            comm.scenarioInvite().let { if (it != scenarioInv) scenarioInv = it }
            comm.loveInvite().let { if (it != loveInv) loveInv = it }
            comm.burstConsentRulesFor().isNotEmpty().let { if (it != hasConsentRules) hasConsentRules = it }
            comm.burstConsentPending().let { pending ->
                if (pending != consentPending) consentPending = pending
                if (pending > 0L) {
                    val key = comm.pid to pending
                    if (consentPrompted != key && !consentDlg) {
                        consentPrompted = key
                        consentDlg = true
                    }
                }
            }
            kotlinx.coroutines.delay(250)
        }
    }
    val msgs = remember(ver) { comm.visible() }
    val list = rememberLazyListState()
    LaunchedEffect(comm.pid) {
        comm.voice.stop()
        val session0 = voiceSession
        val name = comm.personaName()
        val clear0 = comm.clearTsForVoice()
        if (comm.voice.enabled && comm.voice.auto && comm.voice.playAllowed() && comm.blocked().isEmpty()) {
            voiceAutoRunning = true
            val queue = comm.voice.autoplay(name, comm.visible())
            try {
                for (m in queue) {
                    if (session0 != voiceSession || !comm.voice.enabled || !comm.voice.auto || !comm.voice.playAllowed() || comm.blocked().isNotEmpty() || clear0 != comm.clearTsForVoice()) break
                    if (comm.voice.played(name, m)) continue
                    val index = comm.visible().asReversed().indexOfFirst { it.uid == m.uid }
                    if (index < 0) continue
                    list.scrollToItem(index)
                    comm.voice.play(name, m)
                    while (comm.voice.state in listOf("loading", "playing")) {
                        if (session0 != voiceSession) break
                        if (!comm.voice.enabled || !comm.voice.auto || !comm.voice.playAllowed() || comm.blocked().isNotEmpty() || clear0 != comm.clearTsForVoice()) { comm.voice.stop(); break }
                        kotlinx.coroutines.delay(100)
                    }
                    kotlinx.coroutines.delay((comm.voice.gap * 1000).toLong())
                }
            } finally { voiceAutoRunning = false }
        }
    }
    // 倒排: 第 0 项是最新的一条, 在最下面. 新消息来了 (收到的 / 同步来的) 滑到最新; 正在往上翻很早的记录时不打断 (自己发的除外)
    LaunchedEffect(msgs.firstOrNull()?.id, msgs.size) {
        if (!voiceAutoRunning && msgs.isNotEmpty() && (list.firstVisibleItemIndex <= 4 || msgs.last().role == "me")) list.scrollToItem(0)
    }
    fun report(why: String) {
        core.toast(tr(why), "warn")
        if (comm.blocked().isEmpty() && (why == "还没有选人设" || "API" in why || "Base URL" in why || "模型" in why)) go(if (why == "还没有选人设") "settings:comm" else "settings:models")
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
            // AI 配置: 每个聊天对象各选一份 (在 设置 → 模型 里建 / 改, 最多 10 份)
            var cfgOpen by remember { mutableStateOf(false) }
            Box {
                TextButton({ cfgOpen = true }) { Text("▾  " + comm.currentConfigName().ifEmpty { "—" }, color = o.dim, fontSize = 13.sp) }
                androidx.compose.material3.DropdownMenu(cfgOpen, { cfgOpen = false }) {
                    for (c in comm.configOptions()) androidx.compose.material3.DropdownMenuItem(
                        text = { Text(c.name, color = if (c.id == comm.currentConfig()) o.accent else o.text) },
                        onClick = { cfgOpen = false; comm.setConfig(c.id) })
                }
            }
            TextButton({ if (comm.messages.isNotEmpty()) askClear = true }) { Text(tr("清空"), color = o.dim) }
        }
        // 对方现在在做什么 (按人设日程; 没有选人设时不显示): 小圆点的颜色跟着状态变
        if (otherStatus.isNotEmpty()) Row(Modifier.padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(8.dp).clip(CircleShape).background(
                Color(android.graphics.Color.parseColor(Comm.STATUS_COLORS[otherStatusKind] ?: "#9E9E9E"))))
            Text(otherStatus, color = o.dim, fontSize = 12.sp, modifier = Modifier.padding(start = 6.dp))
        }
        if (tensionText.isNotEmpty()) Text(tensionText, color = o.dim, fontSize = 12.sp, modifier = Modifier.padding(horizontal = 4.dp))
        // 倒排 + 上下各留一点空白: 第一条 / 最后一条也能再滑过去一些, 新消息总是贴着底部
        LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = list, reverseLayout = true,
            contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 64.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(msgs.asReversed(), key = { "${it.id}:${it.uid}" }) { m -> Bubble(m) { if (comm.voice.playAllowed()) { voiceSession++; comm.voice.play(comm.personaName(), m) } else core.toast(tr("仅可在空闲或休息期间播放语音."), "warn") } }
        }
        val blocked = comm.blocked()
        if (blocked.isNotEmpty() || comm.error.isNotEmpty()) Text(blocked.ifEmpty { tr(comm.error) }, color = o.danger, fontSize = 12.sp,
            modifier = Modifier.padding(top = 4.dp))
        val tight = androidx.compose.foundation.layout.PaddingValues(horizontal = 8.dp, vertical = 0.dp)
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), verticalAlignment = Alignment.CenterVertically) {
            // ❓ 帮助 (最左边): 解释每个按钮的作用
            TextButton({ help = true }, contentPadding = tight) { Text("❓", fontSize = 18.sp) }
            TextButton({ readingChoice = true }, contentPadding = tight) { Text("📚", fontSize = 18.sp) }
            if (hasConsentRules) {
                TextButton({ consentDlg = true }, contentPadding = tight) { Text("🛡", fontSize = 18.sp) }
            }
            // 当前聊天模式：开启情景模拟采用申请制，双方均可随时结束。
            TextButton({
                if (scenarioOn) comm.setScenario(false)
                else if (scenarioAsk) comm.scenarioCancel()
                else comm.toggleRequestDraft("scenario")
                scenarioOn = comm.scenarioOn(); scenarioAsk = comm.scenarioAsking()
            }, contentPadding = tight) {
                Text(if (scenarioOn) "🎭" else if (scenarioAsk) "⏳" else "📱", fontSize = 18.sp)
            }
            // 🔔 可以主动发起 / 🔕 禁止这个人主动发起 (主动发消息和追问都不发)
            TextButton({ comm.setMuted(!comm.isMuted()) }, contentPadding = tight) {
                Text(if (comm.isMuted()) "🔕" else "🔔", fontSize = 18.sp)
            }
            // 🤝 联合行动: 界面移到合约页选词条, 回来输入开始时间
            TextButton({ core.jointPickStart() }, contentPadding = tight) { Text("🤝", fontSize = 18.sp) }
            // 🖼 独立识字; 📎 将图片作为聊天消息发送
            TextButton({ if (!ocrBusy) pickOcr.launch("image/*") }, contentPadding = tight) { Text(if (ocrBusy) "…" else "🖼", fontSize = 18.sp) }
            TextButton({ if (!ocrBusy) pickChatImage.launch("image/*") }, contentPadding = tight) { Text("📎", fontSize = 18.sp) }
            TextButton({ openCamera() }, contentPadding = tight) { Text("📷", fontSize = 18.sp) }
            TextButton({ pickScore.launch(arrayOf("*/*")) }, contentPadding = tight) { Text("🎼", fontSize = 18.sp) }
            TextButton({ musicReview = true }, contentPadding = tight) { Text("🎹", fontSize = 18.sp) }
            // ⏰ 共时闹钟: 闹钟的时间跟随这个对象的实际起床时间, 手动选响铃的设备; ⏰ = 现在这个对象是共时闹钟 / ⏲ 不是
            TextButton({ coAlarm = true }, contentPadding = tight) {
                Text(if (comm.coAlarmPid().isNotEmpty() && comm.coAlarmPid() == comm.pid) "⏰" else "⏲", fontSize = 18.sp)
            }
            // 🕰 起点平移：范围由人设模板声明，明天起生效；⏳ = 等对方回复。
            TextButton({
                if (shiftAsk) { comm.shiftCancel(); shiftAsk = comm.shiftAsking() }
                else if (comm.pid.isEmpty()) core.toast(tr("先选一个聊天对象."), "warn")
                else if (comm.shiftStart() == null) core.toast(tr("这个人设没有日程文件, 不能平移起点"), "warn")
                else if (comm.shiftRange() == null) core.toast(tr("起点平移模板格式错误"), "warn")
                else shiftDlg = true
            }, contentPadding = tight) { Text(if (shiftAsk) "⏳" else "🕰", fontSize = 18.sp) }
            // share data: 打开后离开沟通页, 在别的页面里对方能看到那一页上你们的数据, 页面下方有聊天悬浮窗; 回到沟通自动关闭
            TextButton({
                val why = if (shareOn || shareAsk) comm.shareEnable(false) else comm.toggleRequestDraft("share")
                if (why.isNotEmpty()) core.toast(tr(why), "warn")
                shareOn = comm.shareOn(); shareAsk = comm.shareAsking()
            }, contentPadding = tight) { Text(if (shareOn) "👀" else if (shareAsk) "⏳" else "🙈", fontSize = 18.sp) }          // 👀 share data 开着 / ⏳ 等对方同意 / 🙈 关着
        }
        if (shareInv) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.padding(start = 8.dp)) {
            Text(tr("{name} 想看你们的数据", "name" to comm.aiName()), color = o.accent, fontSize = 12.sp)
            AccentButton(tr("接受")) { comm.shareRespond(true).let { if (it.isNotEmpty()) core.toast(tr(it), "warn") } }
            TextButton({ comm.shareRespond(false).let { if (it.isNotEmpty()) core.toast(tr(it), "warn") } }) { Text(tr("拒绝"), color = o.dim) }
        }
        if (scenarioInv) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.padding(start = 8.dp)) {
            Text(tr("🎭 {name} 想开启情景模拟", "name" to comm.aiName()), color = o.accent, fontSize = 12.sp)
            AccentButton(tr("接受")) { comm.scenarioRespond(true).let { if (it.isNotEmpty()) core.toast(tr(it), "warn") }; scenarioOn = comm.scenarioOn(); scenarioInv = comm.scenarioInvite() }
            TextButton({ comm.scenarioRespond(false).let { if (it.isNotEmpty()) core.toast(tr(it), "warn") }; scenarioInv = comm.scenarioInvite() }) { Text(tr("拒绝"), color = o.dim) }
        }
        if (coreadInv) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.padding(start = 8.dp)) {
            Text(tr("📚 ${comm.aiName()} 想和你共同阅读"), color = o.accent, fontSize = 12.sp)
            AccentButton(tr("接受")) {
                comm.coreadRespond(true).let { if (it.isNotEmpty()) core.toast(tr(it), "warn") }
                coreadInv = comm.coreadInvite()
                if (comm.coreadUrl.isNotBlank()) openCoread()
            }
            TextButton({ comm.coreadRespond(false).let { if (it.isNotEmpty()) core.toast(tr(it), "warn") }; coreadInv = comm.coreadInvite() }) {
                Text(tr("拒绝"), color = o.dim)
            }
        }
        if (loveInv) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.padding(start = 8.dp)) {
            Text(tr("{name} 向你告白", "name" to comm.aiName()), color = o.accent, fontSize = 12.sp)
            AccentButton(tr("接受")) { comm.loveRespond(true).let { if (it.isNotEmpty()) core.toast(tr(it), "warn") } }
            TextButton({ comm.loveRespond(false).let { if (it.isNotEmpty()) core.toast(tr(it), "warn") } }) { Text(tr("拒绝"), color = o.dim) }
        }
        if (consentPending > 0L) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.padding(start = 8.dp)) {
            Text(tr("🛡 {name} 等待同意声明确认", "name" to comm.aiName()), color = o.accent, fontSize = 12.sp)
            AccentButton(tr("确认")) { consentDlg = true }
        }
        // 选好、还没发出去的联合行动 / share data / coread 附件, 或发出去之后的进展
        if (jointLine.first.isNotEmpty()) Row(verticalAlignment = Alignment.CenterVertically) {
            Text(jointLine.first, color = o.accent, fontSize = 12.sp, modifier = Modifier.weight(1f, false).padding(start = 8.dp))
            TextButton({ comm.clearPackageDrafts() }, contentPadding = tight) { Text("✕", color = o.dim, fontSize = 14.sp) }
        } else if (jointLine.second.isNotEmpty())
            Text(jointLine.second, color = o.dim, fontSize = 12.sp, modifier = Modifier.padding(start = 8.dp))
        if (invite) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.padding(start = 8.dp)) {
            AccentButton(tr("接受")) { comm.jointRespond(true).let { if (it.isNotEmpty()) core.toast(tr(it), "warn") } }
            TextButton({ comm.jointRespond(false).let { if (it.isNotEmpty()) core.toast(tr(it), "warn") } }) { Text(tr("拒绝"), color = o.dim) }
        }
        if (pendingImage != null) Row(verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(start = 8.dp, bottom = 4.dp)) {
            if (pendingPreview != null) androidx.compose.foundation.Image(pendingPreview, tr("待发送图片预览"),
                Modifier.size(96.dp, 72.dp).clip(RoundedCornerShape(4.dp)).border(1.dp, o.border, RoundedCornerShape(4.dp)),
                contentScale = ContentScale.Fit)
            else Text("📎", color = o.dim)
            TextButton({ pendingImage = null }, contentPadding = tight) { Text("✕", color = o.dim, fontSize = 16.sp) }
        }
        Row(Modifier.fillMaxWidth().padding(bottom = 8.dp), verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(input, { input = it }, modifier = Modifier.weight(1f), maxLines = 4, enabled = blocked.isEmpty())
            AccentButton(tr("发送"), enabled = blocked.isEmpty()) {
                val why = pendingImage?.let { comm.sendImage(it, input) } ?: comm.send(input)
                if (why.isEmpty()) { input = ""; pendingImage = null } else if (why != "empty") report(why)
            }
        }
    }
    if (readingSession) Dialog(onDismissRequest = {}) {
        Column(Modifier.fillMaxWidth().background(o.card, RoundedCornerShape(18.dp)).padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Text(tr("正在共读，请在阅读网页里的聊天框继续沟通。"), color = o.text)
            TextButton({
                val url = comm.coreadUrl
                val key = comm.coreadAccessKey
                Thread {
                    runCatching {
                        val connection = java.net.URL("${url.trimEnd('/')}/v1/oit-chat/end").openConnection() as java.net.HttpURLConnection
                        connection.requestMethod = "POST"; connection.doOutput = true
                        connection.setRequestProperty("Content-Type", "application/json")
                        connection.setRequestProperty("x-coread-key", key)
                        connection.outputStream.use {
                            it.write(org.json.JSONObject().put("session_id", readingSessionId).toString().toByteArray())
                        }
                        connection.inputStream.close(); connection.disconnect()
                    }
                }.apply { isDaemon = true }.start()
                readingSession = false; comm.readingMode = false; comm.coreadSessionId = ""; readingSessionId = ""
            }) { Text(tr("结束阅读并恢复沟通"), color = o.accent) }
        }
    }
    if (help) HelpDialog { help = false }
    if (readingChoice) AlertDialog(onDismissRequest = { readingChoice = false }, containerColor = o.bg,
        title = { Text(tr("阅读")) },
        text = {
            Column {
                TextButton({ readingChoice = false; openCoread(false) }) { Text(tr("自己阅读"), color = o.accent) }
                TextButton({
                    readingChoice = false
                    comm.toggleRequestDraft("coread").let { if (it.isNotEmpty()) core.toast(tr(it), "warn") }
                }) { Text(tr("📚 发起 coread"), color = o.accent) }
            }
        },
        confirmButton = { TextButton({ readingChoice = false }) { Text(tr("取消"), color = o.dim) } })
    if (musicReview) MusicReviewDialog(comm) { musicReview = false }
    if (coAlarm) CoAlarmDialog { coAlarm = false }
    if (shiftDlg) ShiftDialog(comm.shiftStart() ?: CommShift.LO, comm.shiftRange() ?: (0 to 1439), { shiftDlg = false }) { minutes ->
        val why = comm.shiftRequest(minutes)
        if (why.isEmpty()) { shiftDlg = false; shiftAsk = comm.shiftAsking(); core.toast(tr("已向 {name} 提出请求, 等待回复", "name" to comm.aiName())) } else core.toast(tr(why), "warn")
    }
    jointAsk?.let { terms -> JointTimeDialog(terms, { core.jointAsk = null; jointAsk = null }) { start ->
        val why = comm.setJointDraft(terms, start)
        if (why.isEmpty()) { core.jointAsk = null; jointAsk = null } else core.toast(tr(why), "warn")
    } }
    if (askClear) Confirm(tr("清空"), tr("清空聊天记录?"), true, { askClear = false }) { comm.clear() }
    if (consentDlg) {
        val items = comm.burstConsentItemsFor()
        if (items.isNotEmpty()) {
            BurstConsentDialog(
                name = comm.aiName(),
                pending = consentPending > 0L,
                items = items,
                current = comm.burstConsentState(),
                onDismiss = { consentDlg = false },
                onConfirm = { choices ->
                    comm.submitBurstConsent(choices)
                    hasConsentRules = comm.burstConsentRulesFor().isNotEmpty()
                    consentPending = comm.burstConsentPending()
                    consentDlg = false
                },
            )
        } else {
            consentDlg = false
        }
    }
}

@Composable
private fun BurstConsentDialog(
    name: String,
    pending: Boolean,
    items: LinkedHashMap<String, com.obliphur.oit.core.CommRules.ConsentRule>,
    current: Map<String, Boolean>,
    onDismiss: () -> Unit,
    onConfirm: (Map<String, Boolean>) -> Unit,
) {
    val o = LocalOit.current
    val l3Bg = if (o.dark) Color(0xFF1D2836) else Color(0xFFEAF2FC)
    val l4Bg = if (o.dark) Color(0xFF381E25) else Color(0xFFFCECEF)
    val l45Bg = if (o.dark) Color(0xFF301C3D) else Color(0xFFF5EBFC)
    val l3Badge = if (o.dark) Color(0xFF93C5FD) else Color(0xFF1D4ED8)
    val l4Badge = if (o.dark) Color(0xFFFDA4AF) else Color(0xFFBE123C)
    val l45Badge = if (o.dark) Color(0xFFD8B4FE) else Color(0xFF7E22CE)
    val states = remember(items, current) {
        mutableStateOf(items.keys.associateWith { current[it] == true })
    }
    val intro = if (pending) {
        tr("{name} 即将首次进入高张力爆发（BURST）状态。请逐项勾选你同意允许的内容（未勾选的项目将被严格禁止；可随时点击工具栏「🛡」修改或撤回）。", "name" to name)
    } else {
        tr("管理 {name} 的内容同意声明。勾选即表示同意允许，取消勾选会立即撤回该项同意，后续回复将立即停止使用对应行为。", "name" to name)
    }
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = o.bg,
        title = { Text(tr("同意声明")) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(intro, color = o.text, fontSize = 13.sp)
                Text(
                    tr("提示：明确勾选同意声明有助于模型理解边界，可以增大内容被正确且保持原强度生成的概率。"),
                    color = o.dim, fontSize = 12.sp,
                )
                items.forEach { (cid, rule) ->
                    val checked = states.value[cid] == true
                    val rowBg = when {
                        rule.level >= 4.5 -> l45Bg
                        rule.level >= 4.0 -> l4Bg
                        else -> l3Bg
                    }
                    val badgeColor = when {
                        rule.level >= 4.5 -> l45Badge
                        rule.level >= 4.0 -> l4Badge
                        else -> l3Badge
                    }
                    val badgeText = when {
                        rule.level >= 4.5 -> "4.5级"
                        rule.level >= 4.0 -> "4级"
                        else -> "3级"
                    }
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(8.dp))
                            .background(rowBg)
                            .clickable { states.value = states.value + (cid to !checked) }
                            .padding(end = 10.dp, top = 2.dp, bottom = 2.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Checkbox(checked = checked, onCheckedChange = { states.value = states.value + (cid to it) })
                        Text(rule.label, color = o.text, fontSize = 13.sp, modifier = Modifier.weight(1f))
                        Text(
                            tr(badgeText),
                            color = badgeColor,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Bold,
                            modifier = Modifier.padding(start = 6.dp),
                        )
                    }
                }
            }
        },
        dismissButton = {
            Row {
                TextButton({ states.value = items.keys.associateWith { true } }) { Text(tr("全选"), color = o.accent) }
                TextButton(onDismiss) { Text(tr("取消"), color = o.dim) }
            }
        },
        confirmButton = {
            TextButton({ onConfirm(states.value) }) { Text(tr("确定"), color = o.accent) }
        },
    )
}

/** ❓ 帮助: 沟通页上每个小按钮是干什么的, 还有"无言为真"会禁止沟通的提醒. */
/** 识别字体用的图片: 够小又是 PNG / JPG / WEBP / GIF 的原样用; 太大或者格式不认 (BMP / HEIC …) 的解码后缩小, 重新压成 JPEG. */
private fun loadOcrImage(ctx: android.content.Context, uri: android.net.Uri): ByteArray {
    val raw = ctx.contentResolver.openInputStream(uri)?.use { it.readBytes() } ?: throw com.obliphur.oit.ai.AiError("format", "打不开这张图片.")
    if (raw.size <= Ai.OCR_MAX_BYTES && Ai.imageMime(raw).isNotEmpty()) return raw
    val bounds = android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds = true }
    android.graphics.BitmapFactory.decodeByteArray(raw, 0, raw.size, bounds)
    if (bounds.outWidth <= 0 || bounds.outHeight <= 0) throw com.obliphur.oit.ai.AiError("format", "只支持 PNG / JPG / WEBP / GIF 图片")
    var sample = 1
    while (maxOf(bounds.outWidth, bounds.outHeight) / sample > 2400) sample *= 2
    val bmp = android.graphics.BitmapFactory.decodeByteArray(raw, 0, raw.size, android.graphics.BitmapFactory.Options().apply { inSampleSize = sample })
        ?: throw com.obliphur.oit.ai.AiError("format", "打不开这张图片.")
    try {
        var q = 88
        while (true) {
            val out = java.io.ByteArrayOutputStream()
            bmp.compress(android.graphics.Bitmap.CompressFormat.JPEG, q, out)
            if (out.size() <= Ai.OCR_MAX_BYTES || q <= 40) return out.toByteArray()
            q -= 12
        }
    } finally { bmp.recycle() }
}

private fun decodePreviewImage(data: ByteArray): androidx.compose.ui.graphics.ImageBitmap? {
    val bounds = android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds = true }
    android.graphics.BitmapFactory.decodeByteArray(data, 0, data.size, bounds)
    if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
    var sample = 1
    while (maxOf(bounds.outWidth, bounds.outHeight) / sample > 480) sample *= 2
    return android.graphics.BitmapFactory.decodeByteArray(data, 0, data.size,
        android.graphics.BitmapFactory.Options().apply { inSampleSize = sample })?.asImageBitmap()
}

@Composable
private fun HelpDialog(onDismiss: () -> Unit) {
    val o = LocalOit.current
    val rows = listOf(
        Triple("❓", tr("帮助"), tr("打开这个说明.")),
        Triple("📚", tr("阅读"), tr("选择「自己阅读」可独自打开书库；选择「发起 coread」会作为附件加入待发送内容，随下一条消息发送，待对方明确同意后才打开共读室。共读期间请在阅读网页聊天，结束后恢复沟通。手机需连接 Windows 的阅读服务。")),
        Triple("🛡", tr("同意声明"), tr("当且仅当当前人设有同意声明时在工具栏显示。可随时点击逐项勾选或撤回同意（未勾选的项目将被严格禁止）；若首次触发 BURST 时仍未设置，系统也会自动弹窗提示确认。")),
        Triple("💬", tr("说教"), tr("你可以请求对方针对现实中想改变的状态、行为、习惯或语言进行说教，分条分析问题与解决方案；主题会简短记入记忆卡，实际完成改变后才移除。")),
        Triple("📱 / 🎭", tr("聊天模式"), tr("输入框上方的 📱 / 🎭 按钮用于情景模拟。开启情景模拟采用申请制（📱 会把申请作为附件随下一条消息发出，或点「接受」同意对方申请），开启后显示 🎭，双方均可随时结束。模式按聊天对象保存。")),
        Triple("👀 / 🙈", tr("分享数据"), tr("🙈 会把请求作为附件加入待发送内容，随下一条消息询问对方；同意后才打开. 👀 表示已打开，去别的页面时对方会收到那一页上你们的数据；回到沟通自动关闭. 对方也可以发邀请，点「接受」才会打开.")),
        Triple("🔔 / 🔕", tr("主动发起"), tr("🔔 = 允许这个对象主动找你, 🔕 = 禁止.")),
        Triple("⏰ / ⏲", tr("共时闹钟"), tr("⏰ = 闹钟跟随这个对象的起床时间; 响铃的设备手动选, 一次只能有一个对象.")),
        Triple("🕰", tr("起点平移"), tr("请求对方平移日程起点, 允许范围由人设模板决定, 同意后明天起生效.")),
        Triple("🖼", tr("识别字体"), tr("选一张图片, 把上面的文字读出来放进聊天框. OCR 不发送图片; 也可以用 📎 把图片发给聊天 AI.")),
        Triple("📷", tr("拍照"), tr("打开手机系统相机拍照，预览后点发送作为图片消息发出.")),
        Triple("🧾", tr("解答图片"), tr("开启设置中的解答图片模式后, 数学、物理、化学子代理的解答会由聊天 AI 风格化, 再生成一张可滑动长图.")),
        Triple("🤝", tr("联合行动"), tr("邀请对方一起做一次合约: 在合约页选词条, 输入开始时间; 对方回 [Yes] 就按约开始. Claudia 的剩余休息按她的日程接续和结转。")),
    )
    val half = (rows.size + 1) / 2                                    // 左栏先排满, 剩下的放第二栏
    @Composable
    fun Cell(r: Triple<String, String, String>) {
        Column(Modifier.padding(bottom = 10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(r.first, color = o.text, fontWeight = FontWeight.Bold, fontSize = 14.sp)
                Text(r.second, color = o.text, fontWeight = FontWeight.Bold, fontSize = 13.sp, modifier = Modifier.padding(start = 6.dp))
            }
            Text(r.third, color = o.dim, fontSize = 12.sp)
        }
    }
    AlertDialog(onDismissRequest = onDismiss, containerColor = o.bg, title = { Text(tr("帮助")) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Row(Modifier.fillMaxWidth()) {
                    Column(Modifier.weight(1f).padding(end = 8.dp)) { for (r in rows.take(half)) Cell(r) }
                    Column(Modifier.weight(1f)) { for (r in rows.drop(half)) Cell(r) }
                }
                Text("⚠  " + tr("特别提醒: 「无言为真」生效时无法使用「沟通」."),
                    color = o.danger, fontWeight = FontWeight.Bold, fontSize = 13.sp, modifier = Modifier.padding(top = 4.dp))
            }
        },
        confirmButton = { TextButton(onDismiss) { Text(tr("知道了"), color = o.accent) } })
}

/** 🕰 起点平移：范围由人设模板声明，确定后向对方提出请求。 */
@Composable
private fun ShiftDialog(cur: Int, bounds: Pair<Int, Int>, onCancel: () -> Unit, onOk: (Int) -> Unit) {
    val o = LocalOit.current
    var text by remember { mutableStateOf(CommShift.hm(cur).let { if (it.length < 5) "0$it" else it }) }
    fun parse(): Int? {
        val m = Regex("\\s*(\\d{1,2})\\s*[:：]?\\s*(\\d{2})\\s*").matchEntire(text) ?: return null
        val h = m.groupValues[1].toInt()
        val mi = m.groupValues[2].toInt()
        return if (h > 23 || mi > 59) null else h * 60 + mi
    }
    val v = parse()
    AlertDialog(onDismissRequest = onCancel, containerColor = o.bg, title = { Text(tr("起点平移")) },
        text = {
            Column {
                Text(tr("请求对方把日程的起点平移到新的时刻 ({lo} - {hi}). 对方同意后明天起生效, 今天的日程不变; " +
                    "只记在 OIT 里, 不会修改人设文件. 对方可以拒绝.", "lo" to CommShift.hm(bounds.first), "hi" to CommShift.hm(bounds.second)), color = o.dim, fontSize = 12.sp)
                Text(tr("明天的起点: {t}", "t" to CommShift.hm(cur)), color = o.text, modifier = Modifier.padding(top = 10.dp))
                OutlinedTextField(text, { text = it.take(8) }, singleLine = true, modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
                if (v == null) Text(tr("请输入 时:分, 例如 14:30"), color = o.danger, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
                else if ((v - cur) % Cfg.COMM_SHIFT_STEP_MINUTES != 0)
                    Text(tr("起点平移必须以 {minutes} 分钟为间隔", "minutes" to Cfg.COMM_SHIFT_STEP_MINUTES), color = o.danger, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
            }
        },
        dismissButton = { TextButton(onCancel) { Text(tr("取消"), color = o.dim) } },
        confirmButton = { TextButton({ v?.let(onOk) }, enabled = v != null && (v - cur) % Cfg.COMM_SHIFT_STEP_MINUTES == 0) { Text(tr("请求"), color = o.accent) } })
}

/** 合约页点了"确定选择"之后: 输入开始时间 (时:分, 已经过了的算明天), 下面实时算出结束时间; 确定后打包成联合行动, 随下一条消息发出. */
@Composable
private fun JointTimeDialog(terms: List<String>, onCancel: () -> Unit, onOk: (java.time.LocalDateTime) -> Unit) {
    val o = LocalOit.current
    val dur = com.obliphur.oit.core.Contract.durationOf(com.obliphur.oit.core.Contract.clean(terms, prereq = true)).toLong()
    var text by remember { mutableStateOf(java.time.LocalDateTime.now().plusMinutes(30).let { "%02d:%02d".format(it.hour, it.minute) }) }
    fun parse(): java.time.LocalDateTime? {
        val m = Regex("\\s*(\\d{1,2})\\s*[:：]?\\s*(\\d{2})\\s*").matchEntire(text) ?: return null
        val h = m.groupValues[1].toInt()
        val mi = m.groupValues[2].toInt()
        if (h > 23 || mi > 59) return null
        val n = java.time.LocalDateTime.now()
        val start = n.withHour(h).withMinute(mi).withSecond(0).withNano(0)
        return if (start.isAfter(n)) start else start.plusDays(1)
    }
    val start = parse()
    AlertDialog(onDismissRequest = onCancel, containerColor = o.bg, title = { Text(tr("联合行动")) },
        text = {
            Column {
                Text(tr("合约开始时间 (时:分)"), color = o.text, fontWeight = FontWeight.Bold)
                OutlinedTextField(text, { text = it.take(8) }, singleLine = true, modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
                if (start == null) Text(tr("请输入 时:分, 例如 14:30"), color = o.danger, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
                else {
                    val end = start.plusSeconds(dur)
                    val day = if (start.toLocalDate() == java.time.LocalDate.now()) "" else tr("(明天)") + " "
                    Text(tr("预计 {a} - {b} (合约时长 {d})", "a" to day + "%02d:%02d".format(start.hour, start.minute),
                        "b" to "%02d:%02d".format(end.hour, end.minute), "d" to com.obliphur.oit.core.fmtHm(dur.toDouble())),
                        color = o.dim, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
                }
            }
        },
        dismissButton = { TextButton(onCancel) { Text(tr("取消"), color = o.dim) } },
        confirmButton = { TextButton({ start?.let(onOk) }) { Text(tr("确定"), color = o.accent) } })
}

@Composable
private fun Bubble(m: ChatMsg, play: (() -> Unit)? = null) {
    val o = LocalOit.current
    val comm = OitApp.instance.core.comm
    val mine = m.role == "me"
    val when_ = parseIso(m.at)?.let { "%02d:%02d".format(it.hour, it.minute) } ?: ""
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (mine) Arrangement.End else Arrangement.Start,
        verticalAlignment = Alignment.Top) {
        if (comm.showAvatar && !mine) Avatar("ai", comm.personaTitle(comm.personaName()).take(1).ifEmpty { "?" }, 34)
        Column(Modifier.widthIn(max = 280.dp).padding(horizontal = 6.dp), horizontalAlignment = if (mine) Alignment.End else Alignment.Start) {
            Column(
                modifier = Modifier
                    .background(if (mine) o.accent else o.card, RoundedCornerShape(12.dp))
                    .then(if (mine) Modifier else Modifier.border(1.dp, o.border, RoundedCornerShape(12.dp)))) {
                Text(m.text, color = if (mine) o.onAccent else o.text, fontSize = 15.sp,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp))
                if (!mine && comm.voice.enabled) Text("▶", color = o.dim, fontSize = 14.sp,
                    modifier = Modifier.align(Alignment.Start)
                        .clickable(onClickLabel = tr("播放语音"), role = androidx.compose.ui.semantics.Role.Button) {
                            if (comm.blocked().isEmpty()) play?.invoke()
                        }.padding(start = 4.dp, end = 6.dp, bottom = 2.dp))
            }
            Text(when_, color = o.dim, fontSize = 11.sp)
        }
        if (comm.showAvatar && mine) Avatar("me", OitApp.instance.core.username.take(1).ifEmpty { "我" }, 34)
    }
}

/** 头像: 选过图片就显示图片 (聊天里是方形), 否则是名字的第一个字. 点对方的头像可以看完整的大图. */
@Composable
private fun Avatar(who: String, fallback: String, size: Int) {
    val o = LocalOit.current
    val comm = OitApp.instance.core.comm
    val mine = who == "me"
    val file = comm.avatarPath(who)
    val image = remember(file?.path, file?.lastModified()) {
        file?.let { runCatching { android.graphics.BitmapFactory.decodeFile(it.path)?.asImageBitmap() }.getOrNull() }
    }
    var full by remember(file?.path) { mutableStateOf(false) }
    val fullFile = if (full) comm.avatarFullPath(who) else null
    val fullImage = remember(fullFile?.path, fullFile?.lastModified()) {
        fullFile?.let { runCatching { android.graphics.BitmapFactory.decodeFile(it.path)?.asImageBitmap() }.getOrNull() }
    }
    if (full) {
        androidx.compose.ui.window.Dialog(onDismissRequest = { full = false }) {
            Box(Modifier.background(o.bg, RoundedCornerShape(10.dp)).padding(10.dp)) {
                (fullImage ?: image)?.let {
                    androidx.compose.foundation.Image(it, null,
                        Modifier.fillMaxWidth(0.92f).aspectRatio(it.width.toFloat() / it.height))      // 整张原图: 不裁, 等比放大
                }
            }
        }
    }
    if (image != null) {
        val click = if (mine) Modifier else Modifier.clickable { full = true }
        androidx.compose.foundation.Image(image, null,
            Modifier.size(size.dp).border(1.dp, o.border).then(click))
        return
    }
    Box(Modifier.size(size.dp).background(if (mine) o.accent else o.card).border(1.dp, o.border),
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
    var readme by remember { mutableStateOf(false) }
    var personaProfile by remember { mutableStateOf<String?>(null) }
    var author by remember { mutableStateOf(false) }
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
            Text(tr("🎭  人设"), fontWeight = FontWeight.Bold, color = o.text, modifier = Modifier.weight(1f))
            PlainButton(tr("写法")) { readme = true }
            Box(Modifier.size(6.dp))
            PlainButton(tr("导入")) { pick.launch(arrayOf("*/*")) }
        }
        val names = comm.personaLabels()
        if (names.isNotEmpty()) Box(Modifier.padding(top = 8.dp)) {
            Segmented(names, comm.personaName()) { comm.set(persona = it) }
        }
        val recModel = when (comm.personaTitle(comm.personaName()).trim().lowercase()) {
            "claudia" -> tr("推荐使用Claude")
            "gemina" -> tr("推荐使用Gemini")
            "charlotte" -> tr("推荐使用ChatGPT")
            else -> ""
        }
        if (recModel.isNotEmpty()) {
            Text(recModel, color = o.accent, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
        }
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            val selected = comm.personaName()
            if (selected.isNotEmpty()) PlainButton(tr("打开人设简介")) { personaProfile = comm.readPersonaProfile(selected) }
            PlainButton(tr("创建人设")) { author = true }
        }
        if (comm.claudiaDiffers()) Row(Modifier.padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {   // 选着 Claudia 而内容和默认人设不一样
            Text(tr("Claudia 的内容和默认人设不一样"), color = o.dim, fontSize = 12.sp, modifier = Modifier.weight(1f))
            PlainButton(tr("还原")) { comm.restoreClaudia(); core.toast(tr("已还原默认人设")) }
        }
    }

    personaProfile?.let { PersonaProfileDialog(it) { personaProfile = null } }
    if (author) PersonaAuthorDialog(comm, { author = false }) {
        comm.set(persona = it)
        author = false
    }

    OitCard {
        Text(tr("🧑  我的身份背景"), fontWeight = FontWeight.Bold, color = o.text)
        // 只在这段对话的第一轮发一次, 之后不再重复 (模型之后靠聊天记录)
        var profile by remember { mutableStateOf(comm.userProfile) }
        OutlinedTextField(profile, { profile = it.take(Comm.MAX_PROFILE); comm.set(userProfile = profile) },
            modifier = Modifier.fillMaxWidth().padding(top = 6.dp), minLines = 3, maxLines = 8)
        Text(tr("只在这段对话的第一轮发送, 改动在下一段对话生效."), color = o.dim, fontSize = 12.sp,
            modifier = Modifier.padding(top = 4.dp))
    }

    OitCard {
        Text(tr("🧾  解答图片"), fontWeight = FontWeight.Bold, color = o.text)
        SwitchRow(tr("使用解答图片模式"), comm.answerImages) { comm.set(answerImages = it) }
    }

    OitCard {
        Text(tr("💬  主动发起"), fontWeight = FontWeight.Bold, color = o.text)
        SwitchRow(tr("允许非正在聊天的人发起对话"), comm.bgPro) { comm.set(bgPro = it) }
    }

    OitCard {
        Text(tr("🌐  主动谈话语言"), fontWeight = FontWeight.Bold, color = o.text)
        Text(tr("对方主动发起谈话时使用的语言 (只管主动发起的那一句, 其他对话不限制)."), color = o.dim, fontSize = 12.sp, modifier = Modifier.padding(top = 2.dp))
        var langOpen by remember { mutableStateOf(false) }
        Box {
            TextButton({ langOpen = true }) { Text("▾  " + comm.proLang.ifEmpty { tr("不指定") }, color = o.text) }
            androidx.compose.material3.DropdownMenu(langOpen, { langOpen = false }) {
                for (lang in listOf("") + Comm.PRO_LANGS) androidx.compose.material3.DropdownMenuItem(
                    text = { Text(lang.ifEmpty { tr("不指定") }, color = if (lang == comm.proLang) o.accent else o.text) },
                    onClick = { langOpen = false; comm.set(proLang = lang) })
            }
        }
    }

    OitCard {
        Text(tr("📅  日程跟随"), fontWeight = FontWeight.Bold, color = o.text)
        Text(tr("人设的日程状态改变时 (包括睡眠), 询问是否跟随; 合约期间自动关闭, 结束后再开启."), color = o.dim, fontSize = 12.sp,
            modifier = Modifier.padding(top = 2.dp))
        SwitchRow(tr("开启"), comm.schedFollow) { comm.set(schedFollow = it) }
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
        AvatarCropDialog(u) { bmp, full ->
            cropUri = null
            if (bmp != null && !comm.saveAvatar(avatarWho, bmp, full)) core.toast(tr("保存头像失败."), "warn")
        }
    }

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

@Composable
private fun MusicReviewDialog(comm: Comm, onDismiss: () -> Unit) {
    val o = LocalOit.current
    val ctx = LocalContext.current
    val input = remember { comm.musicInput() }
    var reference by remember { mutableStateOf<java.io.File?>(null) }
    var performance by remember { mutableStateOf<java.io.File?>(null) }
    var recorded by remember { mutableStateOf<ByteArray?>(null) }
    var ports by remember { mutableStateOf(input.ports()) }
    var recording by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("") }
    val pickReference = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) Thread {
            val target = java.io.File(ctx.cacheDir, "music-review-reference.${ctx.contentResolver.getType(uri)?.substringAfter('/') ?: "mid"}")
            try { ctx.contentResolver.openInputStream(uri)?.use { it.copyTo(target.outputStream()) }; reference = target; status = "" }
            catch (e: Exception) { status = e.message.orEmpty() }
        }.apply { isDaemon = true; start() }
    }
    val pickPerformance = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) Thread {
            val ext = ctx.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                if (c.moveToFirst()) c.getString(0)?.substringAfterLast('.', "mid") else "mid"
            } ?: "mid"
            val target = java.io.File(ctx.cacheDir, "music-review-performance.$ext")
            try { ctx.contentResolver.openInputStream(uri)?.use { it.copyTo(target.outputStream()) }; performance = target; recorded = null; status = "" }
            catch (e: Exception) { status = e.message.orEmpty() }
        }.apply { isDaemon = true; start() }
    }
    val requestBluetooth = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        status = if (input.scanBluetooth()) tr("正在搜索蓝牙 MIDI 设备") else tr("无法搜索蓝牙 MIDI 设备")
    }
    LaunchedEffect(Unit) {
        while (true) {
            ports = input.ports()
            if (input.error.isNotEmpty()) status = input.error
            kotlinx.coroutines.delay(500)
        }
    }
    DisposableEffect(Unit) { onDispose { input.close() } }
    AlertDialog(onDismissRequest = { if (!busy) onDismiss() }, containerColor = o.bg,
        title = { Text(tr("演奏评判"), color = o.text) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                OutlinedButton({ pickReference.launch(arrayOf("audio/midi", "audio/x-midi", "audio/*", "application/xml", "application/vnd.recordare.musicxml+xml", "application/vnd.recordare.musicxml")) }, enabled = !busy) {
                    Text(reference?.name ?: comm.selectedScore()?.optString("title")?.takeIf { it.isNotEmpty() }?.let { tr("当前选曲: {title}", "title" to it) } ?: tr("选择参考 MIDI 或 MusicXML"))
                }
                OutlinedButton({ pickPerformance.launch(arrayOf("audio/*")) }, enabled = !busy) {
                    Text(performance?.name ?: tr("选择演奏 MIDI 或录音"))
                }
                if (!recording) {
                    androidx.compose.foundation.layout.Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton({
                            val permissions = if (android.os.Build.VERSION.SDK_INT >= 31)
                                listOf(android.Manifest.permission.BLUETOOTH_SCAN, android.Manifest.permission.BLUETOOTH_CONNECT)
                            else listOf(android.Manifest.permission.ACCESS_FINE_LOCATION)
                            if (android.os.Build.VERSION.SDK_INT >= 23) {
                                val missing = permissions
                                    .filter { androidx.core.content.ContextCompat.checkSelfPermission(ctx, it) != android.content.pm.PackageManager.PERMISSION_GRANTED }
                                if (missing.isEmpty()) { input.scanBluetooth(); status = tr("正在搜索蓝牙 MIDI 设备") }
                                else requestBluetooth.launch(missing.toTypedArray())
                            } else { input.scanBluetooth(); status = tr("正在搜索蓝牙 MIDI 设备") }
                        }, enabled = !busy) { Text(tr("扫描蓝牙 MIDI")) }
                    }
                    ports.forEach { port ->
                        TextButton({
                            input.start(port); recording = input.recording; status = if (input.recording) tr("等待 MIDI 首音") else input.error
                        }, enabled = !busy) { Text(tr("录制: {name}", "name" to port.name), color = o.accent) }
                    }
                } else {
                    Text(tr("MIDI 首音数: {count}", "count" to input.noteCount), color = o.text)
                    OutlinedButton({
                        try {
                            recorded = input.stop()
                            performance = java.io.File(ctx.cacheDir, "music-review-recording.mid").also { it.writeBytes(recorded!!) }
                            recording = false; status = tr("MIDI 录制完成")
                        } catch (e: Exception) { recording = false; status = e.message.orEmpty() }
                    }) { Text(tr("停止录制")) }
                }
                if (status.isNotEmpty()) Text(status, color = o.dim, fontSize = 12.sp)
            }
        },
        confirmButton = {
            TextButton(onClick = {
                if (busy) return@TextButton
                val score = reference ?: comm.selectedScore()?.optString("path")?.takeIf { it.isNotEmpty() }?.let { java.io.File(it) }
                val played = performance
                if (score == null || played == null) { status = tr("请选择参考曲目和演奏文件，或录制 MIDI"); return@TextButton }
                busy = true; status = tr("正在分析演奏")
                Thread {
                    var reply = ""; var wait = 0L; var failure = ""
                    try { val result = comm.evaluateMusic(score, played, recorded); reply = result.first; wait = result.second }
                    catch (e: Exception) { failure = e.message.orEmpty() }
                    android.os.Handler(android.os.Looper.getMainLooper()).post {
                        busy = false
                        if (failure.isNotEmpty()) status = failure
                        else {
                            comm.setMusicReviewDraft(reply, wait)
                            onDismiss()
                        }
                    }
                }.apply { isDaemon = true; start() }
            }, enabled = !busy && !recording) { Text(if (busy) tr("正在分析…") else tr("开始评判"), color = o.accent) }
        },
        dismissButton = { TextButton(onClick = onDismiss, enabled = !busy) { Text(tr("取消"), color = o.dim) } })
}
