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
    DisposableEffect(Unit) { comm.setViewing(true); onDispose { comm.setViewing(false) } }
    var input by rememberSaveable { mutableStateOf("") }
    var askClear by remember { mutableStateOf(false) }
    var help by remember { mutableStateOf(false) }
    var coAlarm by remember { mutableStateOf(false) }                           // 共时闹钟的弹窗
    var shiftDlg by remember { mutableStateOf(false) }                          // 起点平移的弹窗
    var shiftAsk by remember { mutableStateOf(comm.shiftAsking()) }              // 请求了起点平移, 等对方回复 (⏳)
    var jointAsk by remember { mutableStateOf(core.jointAsk) }                  // 合约页选好词条回来了: 输入开始时间
    var jointLine by remember { mutableStateOf(comm.jointLabel() to comm.jointStatusText()) }
    var invite by remember { mutableStateOf(comm.jointInvite() != null) }          // 对方发起的联合行动: 等用户点 接受 / 拒绝
    var shareOn by remember { mutableStateOf(comm.shareOn()) }                     // share data 开着吗
    var shareAsk by remember { mutableStateOf(comm.shareAsking()) }                 // 点了 share data, 等对方同意 (⏳)
    var shareInv by remember { mutableStateOf(comm.shareInvite()) }                // 对方发了 [share data]: 等用户点 接受 / 拒绝
    var loveInv by remember { mutableStateOf(comm.loveInvite()) }                  // 对方告白了: 等用户点 接受 / 拒绝
    var ocrBusy by remember { mutableStateOf(false) }                          // 识别字体进行中
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
    // 不跟着每秒一次的 LocalTick 重画 (整页每秒重画一遍会有卡顿感): 只在聊天记录 / 状态变了才刷新
    var ver by remember { mutableIntStateOf(comm.version) }
    var otherStatus by remember { mutableStateOf(comm.otherStatusDisplay().first) }
    var otherStatusKind by remember { mutableStateOf(comm.otherStatusDisplay().second) }
    LaunchedEffect(Unit) {
        while (true) {
            if (comm.version != ver) ver = comm.version
            val (text, kind) = comm.otherStatusDisplay()
            if (text != otherStatus) otherStatus = text
            if (kind != otherStatusKind) otherStatusKind = kind
            if (core.jointAsk != jointAsk) jointAsk = core.jointAsk
            (comm.jointLabel() to comm.jointStatusText()).let { if (it != jointLine) jointLine = it }
            (comm.jointInvite() != null).let { if (it != invite) invite = it }
            comm.shareOn().let { if (it != shareOn) shareOn = it }
            comm.shareAsking().let { if (it != shareAsk) shareAsk = it }
            comm.shiftAsking().let { if (it != shiftAsk) shiftAsk = it }
            comm.shareInvite().let { if (it != shareInv) shareInv = it }
            comm.loveInvite().let { if (it != loveInv) loveInv = it }
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
        val tight = androidx.compose.foundation.layout.PaddingValues(horizontal = 8.dp, vertical = 0.dp)
        Row(verticalAlignment = Alignment.CenterVertically) {
            // ❓ 帮助 (最左边): 解释每个按钮的作用
            TextButton({ help = true }, contentPadding = tight) { Text("❓", fontSize = 18.sp) }
            // 🔔 可以主动发起 / 🔕 禁止这个人主动发起 (主动发消息和追问都不发)
            TextButton({ comm.setMuted(!comm.isMuted()) }, contentPadding = tight) {
                Text(if (comm.isMuted()) "🔕" else "🔔", fontSize = 18.sp)
            }
            // 🤝 联合行动: 界面移到合约页选词条, 回来输入开始时间
            TextButton({ core.jointPickStart() }, contentPadding = tight) { Text("🤝", fontSize = 18.sp) }
            // 🖼 识别字体: 选一张图片, 让模型读出上面的文字, 放进聊天框 (不能发送图片)
            TextButton({ if (!ocrBusy) pickOcr.launch("image/*") }, contentPadding = tight) { Text(if (ocrBusy) "…" else "🖼", fontSize = 18.sp) }
            // ⏰ 共时闹钟: 闹钟的时间跟随这个对象的实际起床时间, 手动选响铃的设备; ⏰ = 现在这个对象是共时闹钟 / ⏲ 不是
            TextButton({ coAlarm = true }, contentPadding = tight) {
                Text(if (comm.coAlarmPid().isNotEmpty() && comm.coAlarmPid() == comm.pid) "⏰" else "⏲", fontSize = 18.sp)
            }
            // 🕰 起点平移: 请求对方把日程起点换到 4:00-9:00 之间的某个时刻, 明天起生效; ⏳ = 等对方回复 (再点 = 取消)
            TextButton({
                if (shiftAsk) { comm.shiftCancel(); shiftAsk = comm.shiftAsking() }
                else if (comm.pid.isEmpty()) core.toast(tr("先选一个聊天对象."), "warn")
                else if (comm.shiftStart() == null) core.toast(tr("这个人设没有日程文件, 不能平移起点"), "warn")
                else shiftDlg = true
            }, contentPadding = tight) { Text(if (shiftAsk) "⏳" else "🕰", fontSize = 18.sp) }
            // share data: 打开后离开沟通页, 在别的页面里对方能看到那一页上你们的数据, 页面下方有聊天悬浮窗; 回到沟通自动关闭
            TextButton({
                comm.shareEnable(!(shareOn || shareAsk)).let { if (it.isNotEmpty()) core.toast(tr(it), "warn") }      // 等对方同意的时候再点 = 取消
                shareOn = comm.shareOn(); shareAsk = comm.shareAsking()
            }, contentPadding = tight) { Text(if (shareOn) "👀" else if (shareAsk) "⏳" else "🙈", fontSize = 18.sp) }          // 👀 share data 开着 / ⏳ 等对方同意 / 🙈 关着
        }
        if (shareInv) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.padding(start = 8.dp)) {
            Text(tr("{name} 想看你们的数据", "name" to comm.aiName()), color = o.accent, fontSize = 12.sp)
            AccentButton(tr("接受")) { comm.shareRespond(true).let { if (it.isNotEmpty()) core.toast(tr(it), "warn") } }
            TextButton({ comm.shareRespond(false).let { if (it.isNotEmpty()) core.toast(tr(it), "warn") } }) { Text(tr("拒绝"), color = o.dim) }
        }
        if (loveInv) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.padding(start = 8.dp)) {
            Text(tr("{name} 向你告白", "name" to comm.aiName()), color = o.accent, fontSize = 12.sp)
            AccentButton(tr("接受")) { comm.loveRespond(true).let { if (it.isNotEmpty()) core.toast(tr(it), "warn") } }
            TextButton({ comm.loveRespond(false).let { if (it.isNotEmpty()) core.toast(tr(it), "warn") } }) { Text(tr("拒绝"), color = o.dim) }
        }
        // 选好、还没发出去的联合行动 (开始 - 结束时间, 结束时间自动算好) 或发出去之后的进展; 📎 勾选的数据
        if (jointLine.first.isNotEmpty()) Row(verticalAlignment = Alignment.CenterVertically) {
            Text(jointLine.first, color = o.accent, fontSize = 12.sp, modifier = Modifier.weight(1f, false).padding(start = 8.dp))
            TextButton({ comm.clearJointDraft() }, contentPadding = tight) { Text("✕", color = o.dim, fontSize = 14.sp) }
        } else if (jointLine.second.isNotEmpty())
            Text(jointLine.second, color = o.dim, fontSize = 12.sp, modifier = Modifier.padding(start = 8.dp))
        if (invite) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.padding(start = 8.dp)) {
            AccentButton(tr("接受")) { comm.jointRespond(true).let { if (it.isNotEmpty()) core.toast(tr(it), "warn") } }
            TextButton({ comm.jointRespond(false).let { if (it.isNotEmpty()) core.toast(tr(it), "warn") } }) { Text(tr("拒绝"), color = o.dim) }
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
    if (help) HelpDialog { help = false }
    if (coAlarm) CoAlarmDialog { coAlarm = false }
    if (shiftDlg) ShiftDialog(comm.shiftStart() ?: CommShift.LO, { shiftDlg = false }) { minutes ->
        val why = comm.shiftRequest(minutes)
        if (why.isEmpty()) { shiftDlg = false; shiftAsk = comm.shiftAsking(); core.toast(tr("已向 {name} 提出请求, 等待回复", "name" to comm.aiName())) } else core.toast(tr(why), "warn")
    }
    jointAsk?.let { terms -> JointTimeDialog(terms, { core.jointAsk = null; jointAsk = null }) { start ->
        val why = comm.setJointDraft(terms, start)
        if (why.isEmpty()) { core.jointAsk = null; jointAsk = null } else core.toast(tr(why), "warn")
    } }
    if (askClear) Confirm(tr("清空"), tr("清空聊天记录?"), true, { askClear = false }) { comm.clear() }
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

@Composable
private fun HelpDialog(onDismiss: () -> Unit) {
    val o = LocalOit.current
    val rows = listOf(
        "❓" to tr("帮助") + ": " + tr("打开这个说明."),
        "👀 / 🙈" to tr("分享数据") + ": " + tr("点一下切换 (👀 = 打开). 打开后去别的页面 (设置 / 沟通 / 指令 / 重置 以外): 对方会收到那一页上你和它自己的数据 (30 秒冷却), " +
            "页面下方有聊天悬浮窗, 计时器 / 整点记录 / 日历 / 统计页还能切换看自己 / 对方的数据; 回到沟通自动关闭, 对方会总结一次. " +
            "对方也可以发 [share data] 邀请你, 点「接受」才会打开."),
        "🔔 / 🔕" to tr("主动发起") + ": " + tr("点一下切换: 🔔 = 允许这个对象主动给你发消息 / 追问, 🔕 = 禁止."),
        "⏰ / ⏲" to tr("共时闹钟") + ": " + tr("点开设置: 把这个对象设为共时闹钟后 (⏰), 闹钟的时间跟随它的日程里的实际起床时间 (睡眠中也会响); " +
            "哪些设备响铃由你手动选, 铃声和音量用「设置 → 闹钟」里的. 一次只有一个对象是共时闹钟."),
        "🕰" to tr("起点平移") + ": " + tr("请求对方把日程的起点平移到新的时刻 (4:00 - 9:00). 对方同意后明天起生效, 今天的日程不变; " +
            "只记在 OIT 里, 不会修改人设文件. 对方可以拒绝."),
        "🖼" to tr("识别字体") + ": " + tr("选一张图片, OIT 让模型把图片上的文字读出来, 放进聊天框 (可以再改). 不能发送图片, 只能提取文字; 图片不会被保存, 对方也看不到."),
        "🤝" to tr("联合行动") + ": " + tr("邀请对方一起完成一次合约: 界面会移到合约页, 选好词条后点「确定选择」(原来「开始行动」的位置), " +
            "再输入开始时间; 词条和合约的基础规则会打包发给对方, 结束时间自动算好写在聊天框上方. " +
            "对方回 [Yes] 就按约开始合约; 合约期间你们的专注 / 休息同步, 合约后得到的休息也同步."),
    )
    AlertDialog(onDismissRequest = onDismiss, containerColor = o.bg, title = { Text(tr("帮助")) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                for ((icon, text) in rows) Row(Modifier.padding(bottom = 8.dp)) {
                    Text(icon, color = o.text, fontWeight = FontWeight.Bold, fontSize = 15.sp, modifier = Modifier.width(56.dp))
                    Text(text, color = o.text, fontSize = 13.sp, modifier = Modifier.weight(1f))
                }
                Text("⚠  " + tr("特别提醒: 合约词条「无言为真」生效时会禁止「沟通」的使用 (不能发消息, 对方的消息也收不到), 合约结束后才能恢复."),
                    color = o.danger, fontWeight = FontWeight.Bold, fontSize = 13.sp, modifier = Modifier.padding(top = 6.dp))
            }
        },
        confirmButton = { TextButton(onDismiss) { Text(tr("知道了"), color = o.accent) } })
}

/** 🕰 起点平移: 输入新的起点 (时:分, 4:00 - 9:00); 确定后向对方提出请求, 对方回 [Yes] / [No]. */
@Composable
private fun ShiftDialog(cur: Int, onCancel: () -> Unit, onOk: (Int) -> Unit) {
    val o = LocalOit.current
    var text by remember { mutableStateOf(CommShift.hm(cur).let { if (it.length < 5) "0$it" else it }) }
    fun parse(): Int? {
        val m = Regex("\\s*(\\d{1,2})\\s*[:：]?\\s*(\\d{2})\\s*").matchEntire(text) ?: return null
        val h = m.groupValues[1].toInt()
        val mi = m.groupValues[2].toInt()
        return if (mi > 59) null else h * 60 + mi
    }
    val v = parse()
    AlertDialog(onDismissRequest = onCancel, containerColor = o.bg, title = { Text(tr("起点平移")) },
        text = {
            Column {
                Text(tr("请求对方把日程的起点平移到新的时刻 (4:00 - 9:00). 对方同意后明天起生效, 今天的日程不变; " +
                    "只记在 OIT 里, 不会修改人设文件. 对方可以拒绝."), color = o.dim, fontSize = 12.sp)
                Text(tr("明天的起点: {t}", "t" to CommShift.hm(cur)), color = o.text, modifier = Modifier.padding(top = 10.dp))
                OutlinedTextField(text, { text = it.take(8) }, singleLine = true, modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
                if (v == null) Text(tr("请输入 时:分, 例如 14:30"), color = o.danger, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
            }
        },
        dismissButton = { TextButton(onCancel) { Text(tr("取消"), color = o.dim) } },
        confirmButton = { TextButton({ v?.let(onOk) }) { Text(tr("请求"), color = o.accent) } })
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
        if (comm.claudiaDiffers()) Row(Modifier.padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {   // 选着 Claudia 而内容和默认人设不一样
            Text(tr("Claudia 的内容和默认人设不一样"), color = o.dim, fontSize = 12.sp, modifier = Modifier.weight(1f))
            PlainButton(tr("还原")) { comm.restoreClaudia(); core.toast(tr("已还原默认人设")) }
        }
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
        Text(tr("✂  分条发送"), fontWeight = FontWeight.Bold, color = o.text)
        Text(tr("经过优化,不怎么会增加token消耗"), color = o.dim, fontSize = 12.sp,
            modifier = Modifier.padding(top = 2.dp))
        SwitchRow(tr("开启"), comm.splitSend) { comm.set(splitSend = it) }
    }

    OitCard {
        Text(tr("🧠  思考"), fontWeight = FontWeight.Bold, color = o.text)
        Text(tr("遇到问日程 / 合约 / 越界 / 英文这类容易漏掉规则的情况(以及主动发起)时,让模型先思考再回复,更稳地遵守人设规则.会增加这几次请求的 token 消耗."),
            color = o.dim, fontSize = 12.sp, modifier = Modifier.padding(top = 2.dp))
        SwitchRow(tr("需要时自动开启"), comm.thinkAuto) { comm.set(thinkAuto = it) }
    }

    OitCard {
        Text(tr("💬  主动发起"), fontWeight = FontWeight.Bold, color = o.text)
        SwitchRow(tr("允许非正在聊天的人发起对话"), comm.bgPro) { comm.set(bgPro = it) }
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
