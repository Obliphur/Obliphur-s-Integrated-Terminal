package com.obliphur.oit.ui

import android.content.Intent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.obliphur.oit.OitApp
import com.obliphur.oit.ai.Ai
import com.obliphur.oit.ai.AiConfig
import com.obliphur.oit.ai.AiError
import com.obliphur.oit.core.Fmt
import com.obliphur.oit.core.RuleKeys
import com.obliphur.oit.core.RuleResult
import com.obliphur.oit.core.StageResult
import com.obliphur.oit.core.tr
import com.obliphur.oit.service.BlockLog
import com.obliphur.oit.service.appLabel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

private val PANEL_TITLES = mapOf(
    RuleKeys.BLACK_DOMAINS to "🌐  网站黑名单", RuleKeys.BLACK_APPS to "📱  应用黑名单",
    RuleKeys.WHITE_DOMAINS to "🌐  网站白名单", RuleKeys.WHITE_APPS to "📱  应用白名单",
)

fun ruleToast(r: RuleResult, item: String) {
    val core = OitApp.instance.core
    when (r) {
        RuleResult.APPLIED -> core.toast(tr("{item} 已生效.", "item" to item), "success")
        RuleResult.STAGED -> core.toast(tr("{item}: 改动将于 48 小时后生效.", "item" to item), "info")
        RuleResult.CANCELLED -> core.toast(tr("{item}: 已取消排队中的改动.", "item" to item), "info")
        RuleResult.EXISTS -> core.toast(tr("{item} 已在名单里.", "item" to item), "warn")
        RuleResult.MISSING -> core.toast(tr("{item} 不在名单里.", "item" to item), "warn")
        RuleResult.INVALID -> core.toast(tr("格式不对."), "warn")
    }
    if (r == RuleResult.APPLIED) OitApp.instance.applyNet(false)
    core.changed()
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun RulesScreen(go: (String) -> Unit) {
    LocalTick.current
    val o = LocalOit.current
    val core = OitApp.instance.core
    val mode = core.settings.str("FILTER_MODE")
    var view by rememberSaveable { mutableStateOf(mode) }
    var aiSettings by remember { mutableStateOf(false) }
    var aiSuggest by remember { mutableStateOf(false) }
    PageColumn {
        OitCard {
            val adapter = core.netMode() == "adapter"
            Text(tr("断网方式") + ": " + core.settings.display("NET_MODE", core.netMode()) + "\n" +
                tr("过滤名单模式") + ": " + core.settings.display("FILTER_MODE"),
                color = o.text, fontWeight = FontWeight.Bold)
            if (adapter) Dim(tr("全屏蔽: 离线时除 OIT 内部需要的连接 (本机, 局域网同步) 外, 所有网站一律拦截; 只启用应用名单."), color = o.warn)
            Dim(tr("本次运行: 放行 {a} · 拦截 {b}", "a" to BlockLog.allowed, "b" to BlockLog.blocked), size = 13)
            FlowRow(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                for ((m, label) in listOf(RuleKeys.BLACKLIST to "⛔  黑名单模式", RuleKeys.WHITELIST to "✅  白名单模式")) {
                    val btn: @Composable () -> Unit = {
                        if (m == mode) AccentButton(tr(label)) {} else PlainButton(tr(label)) {
                            val (st, msg) = core.stageSetting("FILTER_MODE", m)
                            core.toast(msg, if (st == StageResult.INVALID) "warn" else "info")
                        }
                    }
                    btn()
                }
                PlainButton(tr("🤖 AI 建议")) { aiSuggest = true }
                PlainButton(tr("⚙ 模型")) { aiSettings = true }
            }
            core.settings.pending["FILTER_MODE"]?.let {
                Dim(tr("切换到{mode}: {at}", "mode" to core.settings.display("FILTER_MODE", it.value),
                    "at" to core.settings.applyAtText("FILTER_MODE")), size = 13, color = o.warn)
            }
        }
        Flavor(tr("你们贴心的作者已经考虑到了戒断反应和设置参数的资料查询需要,所以默认是放行的.\n在完成初始设置后,各项设置的修改限制才会生效.\n作者比较喜欢用proxy block-all."))

        Segmented(listOf(RuleKeys.BLACKLIST to tr("黑名单"), RuleKeys.WHITELIST to tr("白名单")), view) { view = it }
        val (dk, ak) = RuleKeys.forMode(view)
        // 全屏蔽: 网站名单不起作用, 只显示应用名单 (MECHANICS 14.2)
        val blockAll = core.netMode() == "adapter"
        if (!blockAll) RulePanel(dk)
        RulePanel(ak)
        LockPermissions()

        Heading(tr("⏳  等待生效的改动"))
        val shownPending = core.rules.pending.toList().withIndex()
            .filter { !blockAll || it.value.list in listOf(RuleKeys.WHITE_APPS, RuleKeys.BLACK_APPS) }
        if (shownPending.isEmpty()) Dim(tr("没有"))
        shownPending.forEach { (i, p) ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("${if (p.op == "add") tr("加入") else tr("删除")} · ${tr(PANEL_TITLES[p.list]!!).trim()} · ${p.item}\n" +
                    p.applyAt.format(Fmt.YMDHM), fontSize = 13.sp, color = o.text, modifier = Modifier.weight(1f))
                TextButton({ core.rules.cancelPending(i); core.changed() }) { Text(tr("✕ 取消"), color = o.danger) }
            }
        }

    }
    if (aiSettings) AiSettingsDialog { aiSettings = false }
    if (aiSuggest) AiSuggestDialog(view) { aiSuggest = false }
}

/** 应用名单靠锁定生效 (MECHANICS 14.6): 缺权限时才显示, 带去授予的按钮. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun LockPermissions() {
    val o = LocalOit.current
    val ctx = LocalContext.current
    val usage = com.obliphur.oit.service.AppUsage.permitted(ctx)
    val overlay = com.obliphur.oit.service.AppLock.canOverlay(ctx)
    if (usage && overlay) return
    Dim(tr("锁定应用需要: ") + listOfNotNull(
        if (usage) null else tr("使用情况访问权限"), if (overlay) null else tr("显示在其他应用上层")).joinToString(" · "),
        size = 13, color = o.warn)
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        if (!usage) PlainButton(tr("使用情况访问权限")) {
            runCatching { ctx.startActivity(Intent(android.provider.Settings.ACTION_USAGE_ACCESS_SETTINGS)) }
        }
        if (!overlay) PlainButton(tr("显示在其他应用上层")) {
            runCatching {
                ctx.startActivity(Intent(android.provider.Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    android.net.Uri.parse("package:${ctx.packageName}")))
            }
        }
    }
}

// ════════════════ 设备同步 (MECHANICS 15) ════════════════

/** 同步 / 绑定出错的原因, 换成看得懂的话. */
fun syncErrorText(err: String?): String {
    val e = err ?: ""
    val low = e.lowercase()
    return when {
        e == "not-proxy" -> tr("本机不是 Proxy 断网方式")
        e == "peer-not-proxy" -> tr("对方不是 Proxy 断网方式")
        e == "unpaired" -> tr("对方已经解除了绑定 (或重置过), 在这里解除绑定后重新绑定")
        "timed out" in low || "timeout" in low -> tr("对方没有响应: 对方的 OIT 可能没在运行, 或者熄屏后被系统暂停了 (手机上打开 OIT 再试)")
        "refused" in low -> tr("对方没有在等连接: OIT 没在运行, 或者端口不对")
        else -> e
    }
}

private fun localIp(): String = try {
    java.net.NetworkInterface.getNetworkInterfaces().toList().filter { it.isUp && !it.isLoopback }
        .flatMap { it.inetAddresses.toList() }.firstOrNull { it is java.net.Inet4Address && it.isSiteLocalAddress }
        ?.hostAddress ?: ""
} catch (e: Exception) {
    ""
}

/** 绑定的每台设备此刻的状态, 一台一行; 2 分钟内没有消息的显示"离线". */
fun peerLines(): List<String> {
    val s = OitApp.instance.sync ?: return emptyList()
    val lines = s.deviceStates().map { d ->
        val name = d.name
        val st = d.status
        if (d.outdated) return@map "🔗 $name: " + tr("离线 (版本 {v} 较低, 请更新)", "v" to d.version)
        if (d.netOff) return@map "🔗 $name: " + tr("断网")      // 换成了 Adapter: 效果和在线一样, 只是不同步 (15.5)
        if (d.off) return@map "🔗 $name: " + tr("关机")          // 效果和在线一样, 只是不同步 (MECHANICS 15.4)
        if (!d.online) return@map "🔗 $name: " + tr("离线")
        var t = when (st.optString("timer")) {
            "running" -> tr("专注中…")
            "break" -> tr("休息中 ☕")
            else -> tr("准备就绪")
        }
        if (st.optString("timer") == "running" && !OitApp.instance.core.contract.fog())
            com.obliphur.oit.core.parseIso(st.optString("since"))?.let {
            t += " " + java.time.Duration.between(it, java.time.LocalDateTime.now()).toMinutes() + " min"
        }
        val net = when (st.optString("net")) { "online" -> tr("联网中"); "pending" -> tr("冷静期中"); else -> "" }
        "🔗 $name: $t" + if (net.isNotEmpty()) " · $net" else ""
    }
    // 本机版本低: 对方把本机算作离线
    return lines + s.versionMismatch().filter { it.third }.map { (name, v, _) ->
        tr("⚠ 本机版本 {me} 低于 {name} ({v}): 其他设备把本机算作离线, 请更新.",
            "me" to com.obliphur.oit.BuildConfig.VERSION_NAME, "name" to name, "v" to v)
    }
}

/** 设备同步 (MECHANICS 15), 显示在 设置 → 基本设置. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SyncCard() {
    val o = LocalOit.current
    val app = OitApp.instance
    val s = app.sync ?: return
    val core = app.core
    val scope = rememberCoroutineScope()
    var addr by rememberSaveable { mutableStateOf("") }
    var askMother by remember { mutableStateOf<com.obliphur.oit.sync.Found?>(null) }
    var askUnpair by remember { mutableStateOf<String?>(null) }
    val proxyOn = core.netMode() == "proxy"
    Heading(tr("🔗  设备同步"))
    OitCard {
        Dim(tr("本机: {name} · 端口 {port}", "name" to s.myName, "port" to s.port) + "  " + localIp(), size = 13)
        if (!proxyOn) Dim(tr("两端都使用 Proxy 断网方式时才能同步."), size = 13, color = o.warn)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 6.dp)) {
            PlainButton(tr("🔍 寻找设备")) {
                core.toast(tr("正在寻找同一局域网里的 OIT…"))
                scope.launch(Dispatchers.IO) { runCatching { s.discover() } }
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(addr, { addr = it }, singleLine = true, modifier = Modifier.weight(1f),
                placeholder = { Text("192.168.1.5:17930", color = o.dim) })
            TextButton({
                val host = addr.substringBefore(':').trim()
                val port = addr.substringAfter(':', "17930").trim().toIntOrNull() ?: 17930
                if (host.isNotEmpty()) scope.launch(Dispatchers.IO) {
                    try {
                        val d = s.hello(host, port)
                        s.noteFound(d, host)
                    } catch (e: Exception) {
                        withContext(Dispatchers.Main) { core.toast(tr("连接失败: {e}", "e" to syncErrorText(e.message ?: e.toString())), "warn") }
                    }
                }
            }) { Text(tr("按地址连接"), color = o.accent) }
        }
        val peers = s.peers()
        for (f in s.found.values.filter { it.id !in peers }.sortedBy { it.name }) {
            Column(Modifier.padding(vertical = 4.dp)) {
                Text("${f.name} (${f.platform})  ${f.addr}:${f.port}", color = o.text, fontSize = 14.sp)
                if (f.netMode != "proxy") Dim(tr("不是 Proxy 模式"), size = 12, color = o.warn)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    AccentButton(tr("绑定 · 本机为母端"), enabled = proxyOn) { s.startPairing(f.addr, f.port, true) }
                    PlainButton(tr("绑定 · 对方为母端"), enabled = proxyOn) { askMother = f }
                }
            }
        }
        s.outgoing?.let { og ->
            val text = when (og.state) {
                "connecting" -> tr("正在连接…")
                "waiting" -> tr("配对码 {code} · 请在 {name} 上确认", "code" to og.code, "name" to og.name)
                "accepted" -> tr("已绑定 {name}.", "name" to og.name)
                "rejected" -> tr("对方拒绝了绑定.")
                "timeout" -> tr("对方没有在 2 分钟内确认.")
                "cancelled" -> tr("已取消.")
                else -> tr("绑定失败: {e}", "e" to syncErrorText(og.error))
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(text, color = o.accent, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                if (og.state == "connecting" || og.state == "waiting") TextButton({ s.cancelPairing() }) { Text(tr("取消"), color = o.dim) }
            }
        }
        Text(tr("已绑定的设备"), color = o.text, fontWeight = FontWeight.Bold, fontSize = 14.sp, modifier = Modifier.padding(top = 10.dp))
        if (peers.isEmpty()) Dim(tr("没有"))
        val now = System.currentTimeMillis()
        val live = s.peerStatus().associate { it.first to it.third }
        for ((pid, p) in peers) {
            val role = when (p.optString("mother")) {
                pid -> tr("母端")
                s.myId -> tr("本机是母端")
                else -> ""
            }
            val err = p.optString("last_err")
            val okAt = p.optLong("last_ok")
            val whenText = when {
                err.isNotEmpty() -> tr("同步失败: {e}", "e" to syncErrorText(err))
                okAt > 0 -> tr("上次同步 {t}", "t" to java.time.Instant.ofEpochMilli(okAt).atZone(java.time.ZoneId.systemDefault())
                    .toLocalTime().withNano(0).toString())
                else -> tr("还没同步过")
            }
            val st = live[pid]?.takeIf { now - it.optLong("ts") < 60_000 }
            val busy = when (st?.optString("timer")) { "running" -> tr("专注中…"); "break" -> tr("休息中 ☕"); else -> "" }
            Column(Modifier.padding(vertical = 4.dp)) {
                Text("${p.optString("name")} (${p.optString("platform")})", color = o.text)
                Dim(listOf(role, whenText, busy).filter { it.isNotEmpty() }.joinToString("  ·  "), size = 12,
                    color = if (err.isNotEmpty()) o.warn else o.dim)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    PlainButton(tr("立即同步")) { s.syncNow() }
                    PlainButton(tr("解除绑定")) { askUnpair = pid }
                }
            }
        }
    }
    askMother?.let { f ->
        Confirm(tr("绑定"), tr("本机的设置, 名单和记录将被对方的数据替换."), true, { askMother = null }) {
            s.startPairing(f.addr, f.port, false)
        }
    }
    askUnpair?.let { pid ->
        Confirm(tr("解除绑定"), tr("解除和 {name} 的绑定?", "name" to (s.peers()[pid]?.optString("name") ?: "?")), true,
            { askUnpair = null }) { s.unpair(pid) }
    }
}

@Composable
private fun RulePanel(key: String) {
    val o = LocalOit.current
    val core = OitApp.instance.core
    val ctx = LocalContext.current
    val isApp = RuleKeys.isApp(key)
    var input by rememberSaveable(key) { mutableStateOf("") }
    var pick by remember { mutableStateOf(false) }
    OitCard {
        Text(tr(PANEL_TITLES[key]!!), color = o.text, fontWeight = FontWeight.Bold)
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 6.dp)) {
            OutlinedTextField(input, { input = it }, singleLine = true, modifier = Modifier.weight(1f),
                placeholder = { Text(if (isApp) "com.example.app" else "example.com", color = o.dim) })
            TextButton({
                val (r, item) = core.rules.add(key, input)
                ruleToast(r, item.ifEmpty { input })
                if (r != RuleResult.INVALID) input = ""
            }) { Text(tr("加入"), color = o.accent) }
        }
        if (isApp) PlainButton(tr("从已安装应用中选择")) { pick = true }
        val items = core.rules.items(key)
        val staged = core.rules.pending.filter { it.list == key && it.op == "add" }.map { it.item }
        if (items.isEmpty() && staged.isEmpty()) Dim(tr("名单为空"), Modifier.padding(top = 6.dp))
        for (item in items + staged) {
            val p = core.rules.pendingFor(key, item)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(if (isApp) appLabel(ctx, item) else item, color = if (item in staged) o.dim else o.text)
                    if (isApp) Dim(item, size = 11)
                    if (p != null) Dim((if (p.op == "add") tr("⏳ 待加入") else tr("⏳ 待删除")) + " · " +
                        p.applyAt.format(Fmt.YMDHM), size = 11, color = o.warn)
                }
                TextButton({ val (r, it2) = core.rules.remove(key, item); ruleToast(r, it2) }) { Text("✕", color = o.danger) }
            }
        }
    }
    if (pick) AppPicker({ pick = false }) { pkg ->
        val (r, item) = core.rules.add(key, pkg)
        ruleToast(r, item)
    }
}

/** 本机已安装 (有启动入口) 的应用. */
@Composable
fun AppPicker(onDismiss: () -> Unit, onPick: (String) -> Unit) {
    val o = LocalOit.current
    val ctx = LocalContext.current
    val apps = remember {
        val pm = ctx.packageManager
        pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0)
            .map { it.activityInfo.packageName to it.loadLabel(pm).toString() }
            .distinctBy { it.first }.sortedBy { it.second.lowercase() }
    }
    var q by remember { mutableStateOf("") }
    AlertDialog(onDismissRequest = onDismiss, containerColor = o.bg,
        title = { Text(tr("从已安装应用中选择")) },
        text = {
            Column {
                OutlinedTextField(q, { q = it }, singleLine = true, placeholder = { Text(tr("搜索")) }, modifier = Modifier.fillMaxWidth())
                LazyColumn(Modifier.heightIn(max = 400.dp)) {
                    items(apps.filter { q.isBlank() || it.second.contains(q, true) || it.first.contains(q, true) }) { (pkg, label) ->
                        Column(Modifier.fillMaxWidth().clickable { onPick(pkg); onDismiss() }.padding(vertical = 8.dp)) {
                            Text(label, color = o.text)
                            Text(pkg, color = o.dim, fontSize = 11.sp)
                        }
                    }
                }
            }
        },
        confirmButton = { TextButton(onDismiss) { Text(tr("取消"), color = o.dim) } })
}

// ════════════════ AI 名单建议 (MECHANICS 5) ════════════════

private fun aiConfig() = AiConfig(File(OitApp.instance.core.dir, "ai_config.json"))

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun AiSettingsDialog(onDismiss: () -> Unit) {
    val o = LocalOit.current
    val cfg = remember { aiConfig() }
    var provider by remember { mutableStateOf(cfg.provider) }
    val edits = remember { HashMap(cfg.profiles) }
    var p by remember { mutableStateOf(cfg.profile()) }
    fun switch(k: String) {
        edits[provider] = p; provider = k; p = edits[k]!!
    }
    AlertDialog(onDismissRequest = onDismiss, containerColor = o.bg,
        title = { Text(tr("⚙ 模型")) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Segmented(Ai.PROVIDERS.map { it.key to tr(it.value.label) }, provider) { switch(it) }
                val custom = provider == "custom"
                OutlinedTextField(p.baseUrl, { if (custom) p = p.copy(baseUrl = it) }, enabled = custom,
                    label = { Text("Base URL") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                if (custom) Segmented(listOf("openai" to "OpenAI", "anthropic" to "Anthropic"), p.protocol) { p = p.copy(protocol = it) }
                OutlinedTextField(p.model, { p = p.copy(model = it) }, label = { Text(tr("模型")) }, singleLine = true,
                    modifier = Modifier.fillMaxWidth())
                val models = Ai.PROVIDERS[provider]!!.models
                if (models.isNotEmpty()) Segmented(models.map { it to it }, p.model) { p = p.copy(model = it) }
                OutlinedTextField(p.apiKey, { p = p.copy(apiKey = it.trim()) }, label = { Text("API Key") }, singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password), modifier = Modifier.fillMaxWidth())
            }
        },
        confirmButton = {
            TextButton({
                edits[provider] = p
                cfg.provider = provider
                cfg.profiles.putAll(edits)
                if (cfg.save()) OitApp.instance.core.toast(tr("已保存."), "success")
                onDismiss()
            }) { Text(tr("保存"), color = o.accent) }
        },
        dismissButton = { TextButton(onDismiss) { Text(tr("取消"), color = o.dim) } })
}

@Composable
fun AiSuggestDialog(mode: String, onDismiss: () -> Unit) {
    val o = LocalOit.current
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val core = OitApp.instance.core
    var step by remember { mutableStateOf("consent") }      // consent / busy / preview / result / error
    var days by remember { mutableStateOf("90") }
    var context by remember { mutableStateOf("") }
    var goal by remember { mutableStateOf("") }
    var error by remember { mutableStateOf("") }
    val results = remember { mutableStateListOf<Triple<String, Ai.Suggestion, Boolean>>() }   // (list, 建议, 勾选)
    val (dk, ak) = RuleKeys.forMode(mode)

    AlertDialog(onDismissRequest = { if (step != "busy") onDismiss() }, containerColor = o.bg,
        title = { Text(tr("🤖 AI 建议") + " · " + tr(if (mode == RuleKeys.WHITELIST) "白名单" else "黑名单")) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                when (step) {
                    "consent" -> {
                        Text(tr("将采集: 已安装的应用 (名称与包名), 所选天数内各应用的使用时长, 以及本次运行通过 OIT 查询过的域名和次数. 采集结果只在内存里, 发送前可以预览和修改."), color = o.text)
                        Text(tr("回看天数"), color = o.dim)
                        Segmented(listOf("30" to "30", "90" to "90", "365" to "365"), days) { days = it }
                    }
                    "busy" -> Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(color = o.accent)
                        Text(tr("请稍候…"), color = o.text, modifier = Modifier.padding(start = 12.dp))
                    }
                    "preview" -> {
                        OutlinedTextField(context, { context = it }, label = { Text(tr("将要发送的内容")) },
                            modifier = Modifier.fillMaxWidth().heightIn(min = 160.dp, max = 320.dp))
                        OutlinedTextField(goal, { goal = it }, label = { Text(tr("补充说明 (可选)")) }, modifier = Modifier.fillMaxWidth())
                    }
                    "result" -> {
                        if (results.isEmpty()) Dim(tr("模型没有给出建议."))
                        results.forEachIndexed { i, (list, s, on) ->
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Checkbox(on, { results[i] = Triple(list, s, it) })
                                Column {
                                    Text((if (RuleKeys.isApp(list)) appLabel(ctx, s.item) + "  " else "") + s.item +
                                        if (s.extra) "  ✦" else "", color = o.text, fontSize = 14.sp)
                                    if (s.reason.isNotEmpty()) Dim(s.reason, size = 12)
                                }
                            }
                        }
                    }
                    "error" -> Text(error, color = o.danger)
                }
            }
        },
        confirmButton = {
            when (step) {
                "consent" -> TextButton({
                    step = "busy"
                    scope.launch {
                        context = withContext(Dispatchers.IO) { runCatching { Ai.collect(ctx, days.toInt()) }.getOrDefault("") }
                        step = "preview"
                    }
                }) { Text(tr("同意并采集"), color = o.accent) }
                "preview" -> TextButton({
                    step = "busy"
                    val text = context
                    scope.launch {
                        try {
                            val (kd, ka) = Ai.extractKnown(text)
                            val raw = withContext(Dispatchers.IO) {
                                Ai.chat(aiConfig().profile(), Ai.systemPrompt(mode), Ai.userMessage(text, goal))
                            }
                            val (ds, ap) = Ai.parse(raw, mode, kd, ka)
                            results.clear()
                            ds.forEach { results.add(Triple(dk, it, true)) }
                            ap.forEach { results.add(Triple(ak, it, true)) }
                            step = "result"
                        } catch (e: AiError) {
                            error = tr(e.message ?: ""); step = "error"
                        } catch (e: Exception) {
                            error = e.toString(); step = "error"
                        }
                    }
                }) { Text(tr("确认发送"), color = o.accent) }
                "result" -> TextButton({
                    var n = 0
                    for ((list, s, on) in results) if (on) {
                        val (r, _) = core.rules.add(list, s.item)
                        if (r == RuleResult.APPLIED || r == RuleResult.STAGED) n++
                    }
                    core.toast(tr("已写入 {n} 项.", "n" to n), "success")
                    OitApp.instance.applyNet(false)
                    core.changed()
                    onDismiss()
                }) { Text(tr("写入名单"), color = o.accent) }
                else -> {}
            }
        },
        dismissButton = { if (step != "busy") TextButton(onDismiss) { Text(tr("关闭"), color = o.dim) } })
}
