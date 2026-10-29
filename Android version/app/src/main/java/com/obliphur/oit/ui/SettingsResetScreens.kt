package com.obliphur.oit.ui

import android.app.AlarmManager
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings as SysSettings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.foundation.clickable
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.obliphur.oit.OitApp
import com.obliphur.oit.core.Cfg
import com.obliphur.oit.core.Kind
import com.obliphur.oit.core.Schema
import com.obliphur.oit.core.Spec
import com.obliphur.oit.core.StageResult
import com.obliphur.oit.core.fmtHm
import com.obliphur.oit.core.tr
import com.obliphur.oit.service.AppUsage
import com.obliphur.oit.service.OitVpnService
import java.time.DayOfWeek
import java.time.LocalDate

// ════════════════ Settings 页 (MECHANICS 1) ════════════════

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SettingsScreen(tab: String, setTab: (String) -> Unit, act: MainActivity) {
    LocalTick.current
    val o = LocalOit.current
    val core = OitApp.instance.core
    val s = core.settings
    // 这一页还没保存的修改: key → 填写框里的文字 / 选中的值
    val edits = remember(tab) { mutableStateMapOf<String, String>() }
    PageColumn {
        Segmented(Schema.PAGES.map { (k, label) ->
            val n = s.pending.keys.count { Schema.BY_KEY[it]!!.page == k }
            k to (tr(label) + if (n > 0) " ⏳$n" else "")
        }, tab) { setTab(it) }

        if (tab == "basic") {
            if (s.inSetup()) OitCard(border = o.accent) {
                var ask by remember { mutableStateOf(false) }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(tr("⏳  配置期"), fontWeight = FontWeight.Bold, color = o.text, modifier = Modifier.weight(1f))
                    PlainButton(tr("结束配置期")) { ask = true }
                }
                val left = s.setupLeftSeconds()
                Dim(tr("还剩 {d} 天 {h} 小时 {m} 分: 所有修改立即生效.", "d" to left / 86400, "h" to (left % 86400) / 3600,
                    "m" to (left % 3600) / 60), size = 13)
                if (ask) Confirm(tr("结束配置期"), tr("结束后每次修改都要等 48 小时,不能撤销."), true, { ask = false }) {
                    core.endSetupPeriod()
                }
            }
            // 成就 (MECHANICS 17): 单独一页, 从这里打开
            OitCard(Modifier.clickable { OitApp.instance.navRequests.tryEmit("ach") }) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(tr("🏆  成就"), fontWeight = FontWeight.Bold, color = o.text, modifier = Modifier.weight(1f))
                    Text(tr("已达成 {n}", "n" to core.ach.unlocked.size), color = o.accent, fontWeight = FontWeight.Bold,
                        fontSize = 14.sp, modifier = Modifier.padding(end = 10.dp))
                    PlainButton(tr("打开")) { OitApp.instance.navRequests.tryEmit("ach") }
                }
            }
            SyncCard()
        }

        val (opts, nums) = Schema.pageSpecs(tab, s.str("THEME"))
        for (spec in opts) OptionRow(spec, edits)
        PageExtras(tab, act)
        if (nums.isNotEmpty()) {
            Heading(tr("数值"))
            for (spec in nums.filter { it.group == null }) NumRow(spec, edits)
            val week = nums.filter { it.group == "weekday" }
            if (week.isNotEmpty()) {
                Heading(tr("按星期调整联网额度"))
                val today = LocalDate.now().dayOfWeek
                for (spec in week) {
                    val dow = DayOfWeek.of(Cfg.WEEKDAY_KEYS.indexOf(spec.key) + 1)
                    val quota = s.num("DAILY_LIMIT") * s.num(spec.key)
                    NumRow(spec, edits, short = true,
                        note = "= ${fmtHm(quota)}" + if (dow == today) "  ◂ " + tr("今天") else "")
                }
            }
            // 日常: 周一到周日的睡觉时间 (MECHANICS 19)
            val sleep = nums.filter { it.group == "sleep" }
            if (sleep.isNotEmpty()) {
                Heading(tr("🌙  睡觉时间"))
                for (spec in sleep) NumRow(spec, edits, short = true)
            }
        }
        val delayedOnPage = Schema.ALL.any { it.page == tab && it.delayed }
        if (delayedOnPage) Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 6.dp)) {
            AccentButton(tr("💾 保存本页修改")) { savePage(tab, edits) }
            if (s.pending.isNotEmpty()) PlainButton(tr("✕ 取消全部待生效修改")) {
                val n = s.cancelAll()
                core.toast(tr("已取消 {n} 项待生效修改.", "n" to n)); core.changed()
            }
        }
        // 作者的话: 设置冷静期的理念 (MECHANICS 1.1)
        if (tab == "basic") Flavor(tr("在设计预期上 只要不是有绝对的正当理由 应该很少有改变能坚持过48h的等待期.\n这是一个理智与本能的抗争的常见结果.\n这个软件的一个理念就是放大理智优势,保留本能空间.") +
            "\n\n" + tr("'放松'方向的改变会被限制,是在希望未来的某一刻,理智只需要占据短暂的上风,就能阻止这个进程.\n为什么'收紧'也需要限制?作者希望用户不要竭泽而渔.你可能想到多的一个小时学习时间有多有价值 但是可能想不到少的一个小时休息时间会对心理健康有多大的压力.所以请在这一段时间好好考虑少的时间/禁用某些关键网站会对你的生活有什么影响.") +
            "\n\n" + tr("如果你很有动力,试试合约!"),
            Modifier.padding(top = 16.dp))
    }
}

private fun savePage(tab: String, edits: MutableMap<String, String>) {
    val core = OitApp.instance.core
    val s = core.settings
    var staged = 0
    var instant = 0
    val errors = mutableListOf<String>()
    for (spec in Schema.ALL.filter { it.page == tab && it.delayed }) {
        val text = edits[spec.key] ?: continue
        val numeric = spec.kind == Kind.NUM || spec.kind == Kind.TIME
        val value: Any = if (numeric) s.fromUiNumber(spec.key, text) else text
        // 和排队中的值相同就不再重新排队 (不然等待时间会被重置)
        val cur = s.pending[spec.key]?.value ?: s.raw(spec.key)
        if (numeric && value is Double && cur is Double && kotlin.math.abs(value - cur) < 1e-9) continue
        if (!numeric && value.toString() == cur.toString()) continue
        val (r, msg) = core.stageSetting(spec.key, value)
        when (r) {
            StageResult.INSTANT -> instant++
            StageResult.STAGED -> staged++
            StageResult.INVALID -> errors.add(msg)
            else -> {}
        }
    }
    edits.clear()
    when {
        errors.isNotEmpty() -> core.toast(errors.joinToString("\n"), "warn")
        staged + instant == 0 -> core.toast(tr("这一页没有修改."))
        else -> core.toast(tr("已保存: {i} 项立即生效, {s} 项 48 小时后生效.", "i" to instant, "s" to staged), "success")
    }
    core.changed()
}

@Composable
private fun WaitTag(key: String) {
    val o = LocalOit.current
    val tag = when (OitApp.instance.core.settings.waitTag(key)) {
        "instant" -> "⚡ " + tr("即时生效")
        "setup" -> tr("配置期内即时生效")
        "free" -> tr("这一次免等待")
        else -> ""
    }
    if (tag.isNotEmpty()) Text(tag, color = o.success, fontSize = 12.sp)
}

@Composable
private fun PendingLine(key: String) {
    val o = LocalOit.current
    val core = OitApp.instance.core
    val p = core.settings.pending[key] ?: return
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(tr("待生效: {v} · {at}", "v" to core.settings.display(key, p.value), "at" to core.settings.applyAtText(key)),
            color = o.warn, fontSize = 12.sp, modifier = Modifier.weight(1f))
        TextButton({ core.settings.cancel(key); core.changed() }) { Text(com.obliphur.oit.core.I18n.ic("✕"), color = o.danger) }
    }
}

@Composable
private fun OptionRow(spec: Spec, edits: MutableMap<String, String>) {
    val o = LocalOit.current
    val core = OitApp.instance.core
    val s = core.settings
    val ctx = LocalContext.current
    var askRestart by remember { mutableStateOf(false) }
    if (askRestart) Confirm(tr("界面主题"), tr("界面主题将在重启 OIT 后生效. 现在重启吗?"), false,
        { askRestart = false }) { (ctx as? MainActivity)?.restartUi() }
    OitCard {
        Text(tr(spec.label), fontWeight = FontWeight.Bold, color = o.text)
        WaitTag(spec.key)
        Dim(tr("当前: {v}", "v" to s.display(spec.key)), size = 13)
        PendingLine(spec.key)
        when (spec.kind) {
            Kind.CHOICE -> {
                val sel = edits[spec.key] ?: s.pending[spec.key]?.value?.toString() ?: s.str(spec.key)
                Segmented(spec.choices.map { it.first to tr(it.second) }, sel) { v ->
                    if (spec.key == "THEME") {
                        // 主题重启后才生效: 存下来, 弹窗问要不要现在重启
                        if (v != core.settings.str("THEME")) {
                            core.stageSetting(spec.key, v)
                            askRestart = true
                        }
                    } else if (!spec.delayed) {
                        val (_, msg) = core.stageSetting(spec.key, v)
                        core.toast(msg)
                    } else {
                        edits[spec.key] = v
                    }
                }
            }
            Kind.INSTANT_TEXT -> {
                val cur = s.str(spec.key)
                Segmented(spec.choices.map { it.first to tr(it.second) }, cur) { core.stageSetting(spec.key, it) }
                var custom by remember(cur) { mutableStateOf(cur) }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(custom, { custom = it }, singleLine = true, modifier = Modifier.weight(1f))
                    TextButton({ if (custom.isNotBlank()) core.stageSetting(spec.key, custom.trim()) }) { Text(tr("应用"), color = o.accent) }
                }
            }
            Kind.TEXT -> {
                val cur = s.str(spec.key)
                var custom by remember(cur) { mutableStateOf(cur) }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(custom, { custom = it }, singleLine = true, modifier = Modifier.weight(1f))
                    TextButton({
                        if (custom.isNotBlank() || spec.windows) {          // 提升时段可以清空
                            val (status, msg) = core.stageSetting(spec.key, custom.trim())
                            if (status != StageResult.INVALID) core.toast(msg)
                        }
                    }) { Text(tr("应用修改"), color = o.accent) }
                }
            }
            Kind.CHIME -> ChimePicker()
            Kind.NUM, Kind.TIME -> {}
        }
    }
}

@Composable
private fun ChimePicker() {
    val o = LocalOit.current
    val core = OitApp.instance.core
    val ctx = LocalContext.current
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            runCatching { ctx.contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION) }
            core.stageSetting("CHIME_WAV", uri.toString())
        }
    }
    val custom = core.settings.str("CHIME_WAV").isNotBlank()
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Segmented(listOf("builtin" to tr("内置三音提示音 (推荐)"), "file" to tr("选择音频文件…")),
            if (custom) "file" else "builtin") {
            if (it == "builtin") core.stageSetting("CHIME_WAV", "") else launcher.launch(arrayOf("audio/*"))
        }
    }
    PlainButton(tr("▶ 试听")) { core.previewChime() }
    // 只显示文件名, 不显示路径
    if (custom) Dim("🎵 " + audioName(ctx, core.settings.str("CHIME_WAV")), size = 12, color = o.dim)
}

/** 选好的音频文件的名字 (不含路径). */
private fun audioName(ctx: android.content.Context, uri: String): String {
    val u = Uri.parse(uri)
    runCatching {
        ctx.contentResolver.query(u, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) c.getString(0)?.takeIf { it.isNotBlank() }?.let { return it }
        }
    }
    return (u.lastPathSegment ?: "").substringAfterLast('/').substringAfterLast(':')
}

@Composable
private fun NumRow(spec: Spec, edits: MutableMap<String, String>, short: Boolean = false, note: String = "") {
    val o = LocalOit.current
    val s = OitApp.instance.core.settings
    val shown = edits[spec.key] ?: s.uiNumber(spec.key, s.pending[spec.key]?.value)
    Column {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(tr(if (short) spec.short!! else spec.label), color = o.text, fontSize = 14.sp,
                modifier = Modifier.width(if (short) 56.dp else 150.dp))
            OutlinedTextField(shown, { edits[spec.key] = it }, singleLine = true,
                keyboardOptions = KeyboardOptions(
                    keyboardType = if (spec.kind == Kind.TIME) KeyboardType.Text else KeyboardType.Decimal),
                suffix = { if (spec.unit.isNotEmpty()) Text(spec.unit, color = o.dim) },
                modifier = Modifier.weight(1f))
            if (note.isNotEmpty()) Text(note, color = o.dim, fontSize = 12.sp, modifier = Modifier.padding(start = 8.dp))
        }
        WaitTag(spec.key)
        if (s.pending.containsKey(spec.key)) {
            Dim(tr("当前: {v}", "v" to s.display(spec.key)), size = 12)
            PendingLine(spec.key)
        }
    }
}

/** 下载进度: "3.2 / 18.2 MB · 120 KB/s", 重连过时加上次数. */
private fun updateProgress(u: com.obliphur.oit.service.Updater): String {
    fun mb(b: Long) = "%.1f".format(b / 1048576.0)
    var t = mb(u.got) + (if (u.total > 0) " / " + mb(u.total) else "") + " MB"
    if (u.speed > 0) t += " · ${u.speed / 1024} KB/s"
    if (u.retry > 0) t += " · " + tr("重连 {n} 次", "n" to u.retry)
    return t
}

/** 各页上 Android 特有的权限 / 系统设置入口. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun PageExtras(tab: String, act: MainActivity) {
    val o = LocalOit.current
    val ctx = LocalContext.current
    val app = OitApp.instance
    fun open(i: Intent) = runCatching { ctx.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
    if (tab == "basic") OitCard {
        // 更新 (MECHANICS 16)
        LocalTick.current
        val u = app.updater
        Text(tr("⬆  更新"), fontWeight = FontWeight.Bold, color = o.text)
        val state = when (u.state) {
            "checking" -> tr("正在检查…")
            "latest" -> tr("已是最新版本")
            "available" -> tr("发现新版本 {v}", "v" to u.latestText)
            "noapk" -> tr("发现新版本 {v}", "v" to u.latestText) + " · " + tr("这个发布里没有安卓安装包")
            "downloading" -> tr("正在下载更新…") + " " + updateProgress(u)
            "ready" -> tr("新版本 {v} 已下载", "v" to u.latestText)
            "error" -> tr("检查 / 更新失败: {e}", "e" to u.error)
            else -> ""
        }
        Dim(tr("当前版本 {v}", "v" to com.obliphur.oit.BuildConfig.VERSION_NAME) + if (state.isNotEmpty()) "  ·  $state" else "",
            size = 13, color = when (u.state) { "available", "ready" -> o.accent; "error", "noapk" -> o.warn; else -> o.dim })
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 6.dp)) {
            PlainButton(tr("检查更新"), enabled = u.state != "checking" && u.state != "downloading") { app.checkUpdate(auto = false) }
            if (u.canDownload) AccentButton(if (u.state == "error") tr("重新下载") else tr("立即更新")) { app.downloadUpdate() }
            if (u.state == "downloading") PlainButton(tr("取消下载")) { u.cancel() }
            if (u.state == "ready") AccentButton(tr("安装")) { u.install() }
            val peers = app.sync?.peers()?.isNotEmpty() == true
            // 这台手机连 GitHub 下载慢 / 下不动时: 请绑定的设备 (例如电脑) 下载好再从局域网发过来
            if (peers && (u.canDownload || u.state == "downloading")) PlainButton(tr("📥 让其他设备发来")) { app.askPeersForUpdate(true) }
            // 给绑定的其他设备下载安装包, 直接从局域网发过去 (对方不用自己去 GitHub 下载)
            if (peers) PlainButton(tr("📤 发给其他设备")) { app.relayUpdates(true) }
            PlainButton(tr("发布页")) { runCatching { u.openReleasePage() } }
        }
    }
    when (tab) {
        "basic" -> OitCard {
            // 后台 / 熄屏时系统不暂停 OIT: 计时, 断网, 设备同步照常 (MECHANICS 15.3)
            val bg = com.obliphur.oit.service.Background
            val ignoring = bg.batteryIgnored(ctx)
            val restricted = bg.restricted(ctx)
            val data = bg.dataRestricted(ctx)
            Text(tr("后台运行"), fontWeight = FontWeight.Bold, color = o.text)
            Dim(if (ignoring) tr("不受电池优化限制") else tr("受电池优化限制"), size = 13,
                color = if (ignoring) o.success else o.warn)
            if (restricted) Dim(tr("电池设置为\"受限\": 在后台时 OIT 会被停下"), size = 13, color = o.warn)
            if (data) Dim(tr("限制了后台数据: 移动数据上在后台时不能同步"), size = 13, color = o.warn)
            bg.lastPauseAt?.let {
                Dim(tr("本次运行中被系统暂停过 {n} 次, 最近一次 {at} 起停了 {min} 分钟: 这段时间不能同步",
                    "n" to bg.pauses, "at" to it.format(com.obliphur.oit.core.Fmt.HM), "min" to (bg.lastPauseSec + 59) / 60),
                    size = 13, color = o.warn)
            }
            Dim(tr("在后台或熄屏时继续同步需要以上限制都解除. 部分手机 (vivo, 小米, 华为, OPPO 等) 不管这些, 照样在后台冻结 OIT: 还要在\"后台耗电\"里允许 OIT 后台高耗电 (或把电池设为\"无限制\"), 并允许自启动."),
                size = 12, color = o.dim)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (!ignoring) PlainButton(tr("解除电池优化")) { bg.requestBattery(ctx) }
                if (data) PlainButton(tr("允许后台数据")) { bg.dataSaver(ctx) }
                if (bg.hasPowerPage(ctx)) PlainButton(tr("后台耗电 (允许后台高耗电)")) { bg.openPower(ctx) }
                if (bg.hasAutostart(ctx)) PlainButton(tr("自启动")) { bg.openAutostart(ctx) }
                PlainButton(tr("应用信息 (电池)")) { bg.appDetails(ctx) }
            }
        }
        "net" -> OitCard {
            Text("VPN", fontWeight = FontWeight.Bold, color = o.text)
            val plan = OitVpnService.plan
            val running = OitVpnService.instance != null && plan != null
            val kind = when (plan) {
                is com.obliphur.oit.core.VpnPlan.BlockAll -> tr("全部断网")
                is com.obliphur.oit.core.VpnPlan.Dns -> if (plan.blockAll) tr("全屏蔽") else if (plan.filter) tr("按名单过滤 DNS") else tr("全部放行")
                else -> ""
            }
            Dim(when {
                app.vpnNeedsPermission -> tr("没有 VPN 权限")
                OitVpnService.leak -> tr("断网未生效")
                running -> tr("运行中") + " · " + kind + (if (OitVpnService.appBlock) " · " + tr("屏蔽合约禁止联网的应用") else "")
                else -> tr("未运行")
            }, size = 13, color = if (running && !OitVpnService.leak) o.success else o.warn)
            OitVpnService.lastError?.let { Dim(it, size = 11, color = o.dim) }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (app.vpnNeedsPermission) DangerButton(tr("授予 VPN 权限")) { act.requestVpn() }
                PlainButton(tr("系统 VPN 设置")) { open(Intent(SysSettings.ACTION_VPN_SETTINGS)) }
                if (running) PlainButton(tr("重新建立 VPN")) { OitVpnService.rebuild(ctx) }
            }
        }
        "log" -> if (Build.VERSION.SDK_INT >= 31) OitCard {
            val ok = ctx.getSystemService(AlarmManager::class.java).canScheduleExactAlarms()
            Text(tr("准点提醒"), fontWeight = FontWeight.Bold, color = o.text)
            Dim(if (ok) tr("已允许精确闹钟") else tr("未允许精确闹钟"), size = 13, color = if (ok) o.success else o.warn)
            if (!ok) PlainButton(tr("允许精确闹钟")) {
                open(Intent(SysSettings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, Uri.parse("package:${ctx.packageName}")))
            }
        }
        "window" -> OitCard {
            val ok = AppUsage.permitted(ctx)
            Text(tr("使用情况访问权限"), fontWeight = FontWeight.Bold, color = o.text)
            Dim(if (ok) tr("已授予") else tr("未授予"), size = 13, color = if (ok) o.success else o.warn)
            if (!ok) PlainButton(tr("去授予")) { open(Intent(SysSettings.ACTION_USAGE_ACCESS_SETTINGS)) }
        }
        "record" -> RecordingsCard(act)
    }
    // 后台应用数怎么数的, 现在算了哪些 (MECHANICS 14.6)
    if (tab == "window") OitCard {
        LocalTick.current
        val exact = AppUsage.usingRecents(ctx)
        Text(tr("后台应用数"), fontWeight = FontWeight.Bold, color = o.text)
        Dim(if (exact) tr("精确: 按多任务界面里的应用数") else tr("估计: 最近 6 小时用过、没有被关掉的应用 (从多任务里划掉的可能还算着)"),
            size = 13, color = if (exact) o.success else o.warn)
        if (AppUsage.precise(ctx) && !exact && AppUsage.recentsError.isNotEmpty())
            Dim(tr("读不出最近任务: {e}", "e" to AppUsage.recentsError), size = 12, color = o.warn)
        // 现在算了哪些应用: 只在开发者模式里显示 (调试用), 正式界面不显示
        if (OitApp.instance.core.settings.devMode) AppUsage.lastOpen?.let { apps ->
            val pm = ctx.packageManager
            val names = apps.map { p -> runCatching { pm.getApplicationLabel(pm.getApplicationInfo(p, 0)).toString() }.getOrDefault(p) }
            Dim(tr("现在算了 {n} 个", "n" to apps.size) + if (names.isEmpty()) "" else ": " + names.sorted().joinToString(", "), size = 13)
        }
        if (!AppUsage.precise(ctx)) {
            Dim(tr("要精确计数, 需要在电脑上用 adb 给 OIT 授予一次权限 (手机打开 USB 调试 / 无线调试后执行下面两条, 再重新打开 OIT; 卸载重装 OIT 前一直有效):"),
                size = 12)
            for (perm in listOf("DUMP", "PACKAGE_USAGE_STATS"))
                Text("adb shell pm grant ${ctx.packageName} android.permission.$perm", color = o.text, fontSize = 12.sp,
                    fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace)
        }
    }
}

// ════════════════ 录像 (目光 / 刻痕, MECHANICS 18.8) ════════════════

@Composable
private fun RecordingsCard(act: MainActivity) {
    LocalTick.current
    val o = LocalOit.current
    val ctx = LocalContext.current
    val app = OitApp.instance
    val dataDir = java.io.File(ctx.filesDir, "data")

    fun play(f: java.io.File) {
        runCatching {
            val mp4 = com.obliphur.oit.core.RecCrypto.decryptToCache(ctx.cacheDir, f)
            val uri = androidx.core.content.FileProvider.getUriForFile(ctx, "${ctx.packageName}.files", mp4)
            ctx.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, "video/mp4")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK))
        }.onFailure { app.core.toast(tr("打不开这个录像: {e}", "e" to (it.message ?: "?")), "warn") }
    }

    OitCard {
        Text(tr("🎬  录像"), fontWeight = FontWeight.Bold, color = o.text)
    }

    // 只展示最终 (合并 / 同步好) 的录像, 不展示片段 (MECHANICS 18.8)
    for ((kind, label) in listOf("camera" to tr("📷 摄像头 (目光)"), "screen" to tr("🖥 屏幕 (刻痕)"))) {
        val files = com.obliphur.oit.core.RecCrypto.listMerged(dataDir, kind)
        val recording = app.recordManager.isRecording(kind)
        OitCard {
            val total = files.sumOf { it.size }
            Text(label + "   " + tr("{n} 个 · {size}", "n" to files.size, "size" to fmtSize(total)),
                fontWeight = FontWeight.Bold, color = o.text)
            if (files.isEmpty())
                Dim(if (recording) tr("● 录制中, 结束后合并并同步.") else tr("还没有录像."),
                    size = 13, color = if (recording) o.danger else o.dim)
            for (rec in files.take(40)) {
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 4.dp)) {
                    val when0 = rec.start?.format(com.obliphur.oit.core.Fmt.YMDHM) ?: rec.name
                    Column(modifier = Modifier.weight(1f)) {
                        Text(when0, color = o.text, fontSize = 13.sp)
                        Dim(fmtSize(rec.size), size = 11, color = o.dim)
                    }
                    PlainButton(tr("播放")) { play(rec.file) }
                    PlainButton(tr("删除")) {
                        app.recordManager.deleteRecording(com.obliphur.oit.core.RecCrypto.cidOf(rec.name), kind)
                    }
                }
            }
        }
    }
}

// ════════════════ Reset 页 (MECHANICS 9) ════════════════

@Composable
fun ResetScreen() {
    LocalTick.current
    val o = LocalOit.current
    val ctx = LocalContext.current
    var confirm by remember { mutableStateOf("") }
    var ask by remember { mutableStateOf("") }
    PageColumn {
        OitCard(border = o.danger, bg = o.dangerBg) {
            Text(tr("⚠  警告:这会永久删除全部数据"), color = o.danger, fontWeight = FontWeight.Bold)
        }
        OutlinedTextField(confirm, { confirm = it }, singleLine = true, label = { Text(tr("输入 RESET 以确认")) },
            modifier = Modifier.fillMaxWidth())
        DangerButton(tr("🔄  重置并清除数据"), enabled = confirm.trim() == "RESET") { ask = "reset" }
        Flavor(tr("就这样回到一切之初,修正错误的抉择."))
        Heading(tr("🗑  卸载"))
        DangerButton(tr("🗑  卸载 OIT"), enabled = confirm.trim() == "RESET") { ask = "uninstall" }
    }
    when (ask) {
        "reset" -> Confirm(tr("重置确认"), tr("这将永久删除全部数据,不可撤销.") +
            if (OitApp.instance.sync?.peers()?.isNotEmpty() == true) "\n" + tr("绑定的设备也会清空数据 (绑定保留).") else "",
            true, { ask = "" }) {
            confirm = ""
            OitApp.instance.resetAll()
        }
        "uninstall" -> Confirm(tr("卸载"), tr("这将恢复网络并永久删除全部数据,然后打开系统的卸载确认.") +
            if (OitApp.instance.sync?.peers()?.isNotEmpty() == true) "\n" + tr("绑定的设备也会清空数据, 并解除和本机的绑定.") else "",
            true, { ask = "" }) {
            confirm = ""
            OitApp.instance.resetAll(uninstall = true)
            ctx.startActivity(Intent(Intent.ACTION_DELETE, Uri.parse("package:${ctx.packageName}")))
        }
    }
}
