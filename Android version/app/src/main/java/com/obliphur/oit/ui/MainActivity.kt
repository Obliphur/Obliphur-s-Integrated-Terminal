package com.obliphur.oit.ui

import android.Manifest
import android.content.Intent
import android.net.VpnService
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Snackbar
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.obliphur.oit.OitApp
import com.obliphur.oit.core.I18n
import com.obliphur.oit.core.tr
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private val app get() = OitApp.instance
    private val pageRequest = MutableStateFlow<String?>(null)

    private val vpnLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        asking = false
        app.applyNet(true)
        if (firstRun.isNotEmpty()) nextFirstRun() else askBattery()   // VPN 问完了再问电池优化
    }
    private val notifLauncher = registerForActivityResult(ActivityResultContracts.RequestPermission()) {
        asking = false
        // 通知权限问完了再问 VPN, 一次只弹一个系统窗口
        if (firstRun.isNotEmpty()) nextFirstRun() else ensureVpn()
    }
    /** 第一次打开时依次打开的系统设置页 (电池优化, 精确闹钟, 使用情况访问 ……): 从设置页回来再问下一项. */
    private val settingsLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        asking = false
        nextFirstRun()
    }
    // ---- 合约录像 (MECHANICS 18.8): 摄像头 / 麦克风权限, 录屏授权 ----
    private val recPermLauncher = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        asking = false
    }
    private val projLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        asking = false
        if (r.resultCode == RESULT_OK && r.data != null) app.recProjection = r.resultCode to r.data!!
        else recProjCooldown = System.currentTimeMillis() + 60_000   // 拒绝了: 一分钟内不再弹
    }
    private var recProjCooldown = 0L

    /** 界面在前台时补齐录像需要的权限 / 授权 (RecordManager 发现缺了什么就置位 needPerms / needProjection). */
    fun ensureRecordConsent() {
        if (asking) return
        val rm = app.recordManager
        if (rm.needPerms) {
            val need = buildList {
                if (rm.needCamera) add(Manifest.permission.CAMERA)
                if (rm.needAudio) add(Manifest.permission.RECORD_AUDIO)
            }.filter { checkSelfPermission(it) != android.content.pm.PackageManager.PERMISSION_GRANTED }
            if (need.isNotEmpty()) { asking = true; recPermLauncher.launch(need.toTypedArray()); return }
        }
        if (rm.needProjection && app.recProjection == null && System.currentTimeMillis() >= recProjCooldown) {
            val mpm = getSystemService(android.media.projection.MediaProjectionManager::class.java)
            asking = true; projLauncher.launch(mpm.createScreenCaptureIntent())
        }
    }

    /** 第一次打开时还没问的权限; 每一项返回 true = 打开了系统窗口 / 设置页, 等它回来. */
    private val firstRun = ArrayDeque<() -> Boolean>()
    /** 正在等系统的权限窗口 (同时弹两个系统窗口在部分手机上会卡住界面). */
    private var asking = false
    /** 这次打开界面时用的主题. */
    var uiTheme = "classic"
        private set

    /** 重启界面 (改了主题之后). */
    fun restartUi() {
        uiTheme = app.core.settings.str("THEME")
        recreate()
    }
    private var flowStarted = false

    fun requestVpn() {
        val i = VpnService.prepare(this)
        if (i != null) {
            asking = true
            vpnLauncher.launch(i)
        } else app.applyNet(true)
    }

    /**
     * 第一次打开: 欢迎窗口关掉之后再依次问通知权限和 VPN 权限 (由 Root 在欢迎窗口关掉后调用).
     * 之后每次从后台回到 OIT 也调用 ensureVpn().
     */
    fun startPermissionFlow() {
        if (flowStarted || asking) return
        flowStarted = true
        if (startFirstRun()) return
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            asking = true
            notifLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else ensureVpn()
    }

    /** 记下"第一次打开时的权限已经问过了"; 放在数据目录里, 重置 (删除全部数据) 后会再问一遍. */
    private val firstRunFlag get() = java.io.File(filesDir, "data/permissions_asked")

    /**
     * 第一次打开 (欢迎窗口关掉之后): 把需要的权限一项一项都问一遍, 一次只弹一个系统窗口 / 设置页 (MECHANICS 14.1).
     * 已经有的跳过; 用户在设置页里不给也照样问下一项. 返回 false = 不是第一次, 走平时的流程.
     */
    private fun startFirstRun(): Boolean {
        val flag = firstRunFlag
        if (flag.exists()) return false
        runCatching { flag.parentFile?.mkdirs(); flag.writeText(java.time.LocalDateTime.now().toString()) }
        val pkg = android.net.Uri.parse("package:$packageName")
        fun settings(i: Intent): Boolean = try {
            asking = true
            settingsLauncher.launch(i)
            true
        } catch (_: Exception) {
            asking = false
            false                       // 这台手机没有这个设置页: 跳过
        }
        firstRun.clear()
        // 通知
        firstRun.add {
            if (Build.VERSION.SDK_INT >= 33 &&
                checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                asking = true
                notifLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                true
            } else false
        }
        // VPN (断网)
        firstRun.add {
            if (app.core.vpnPlan() == com.obliphur.oit.core.VpnPlan.None) false
            else VpnService.prepare(this)?.let { i ->
                asking = true
                vpnLauncher.launch(i)
                true
            } ?: run {
                app.applyNet(true)
                false
            }
        }
        // 不受电池优化限制 (后台 / 熄屏时计时, 断网, 同步照常)
        firstRun.add {
            batteryAsked = true
            if (com.obliphur.oit.service.Background.batteryIgnored(this)) false
            else settings(Intent(android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, pkg))
        }
        // 精确闹钟 (整点提醒, 联网时间到, 休息结束 准时)
        firstRun.add {
            if (Build.VERSION.SDK_INT < 31 ||
                getSystemService(android.app.AlarmManager::class.java).canScheduleExactAlarms()) false
            else settings(Intent(android.provider.Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, pkg))
        }
        // 使用情况访问 (应用使用时间统计, 锁定应用)
        firstRun.add {
            if (com.obliphur.oit.service.AppUsage.permitted(this)) false
            else settings(Intent(android.provider.Settings.ACTION_USAGE_ACCESS_SETTINGS))
        }
        // 显示在其他应用上层 (锁定应用的盖层)
        firstRun.add {
            if (com.obliphur.oit.service.AppLock.canOverlay(this)) false
            else settings(Intent(android.provider.Settings.ACTION_MANAGE_OVERLAY_PERMISSION, pkg))
        }
        // 安装未知应用 (自动更新, 其他设备发来的安装包)
        firstRun.add {
            if (packageManager.canRequestPackageInstalls()) false
            else settings(Intent(android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, pkg))
        }
        // 手机厂商自己的 自启动 / 后台运行 (查不到有没有打开; 只在有这一页的手机上打开一次)
        firstRun.add {
            if (com.obliphur.oit.service.Background.hasOemPage(this)) {
                app.core.toast(tr("请允许 OIT 自启动 / 后台运行, 否则在后台或熄屏时可能被系统暂停."), "info")
                com.obliphur.oit.service.Background.openOem(this)
            }
            false
        }
        nextFirstRun()
        return true
    }

    private fun nextFirstRun() {
        while (!asking) {
            val step = firstRun.removeFirstOrNull() ?: return
            if (runCatching { step() }.getOrDefault(false)) return
        }
    }

    /** VPN 被别的 VPN 应用顶掉或停了: 重新申请 / 重新打开. */
    private fun ensureVpn() {
        if (asking) return
        if (app.core.vpnPlan() == com.obliphur.oit.core.VpnPlan.None) return
        if (VpnService.prepare(this) != null) requestVpn()
        else {
            if (com.obliphur.oit.service.OitVpnService.instance == null || com.obliphur.oit.service.OitVpnService.plan == null) app.applyNet(true)
            askBattery()
        }
    }

    /** 申请不受电池优化限制: 熄屏后系统也不会停掉 OIT (计时, 同步, 断网照常). 每次启动最多问一次. */
    private fun askBattery() {
        if (batteryAsked || asking) return
        batteryAsked = true
        val pm = getSystemService(android.os.PowerManager::class.java)
        if (pm.isIgnoringBatteryOptimizations(packageName)) return
        runCatching {
            startActivity(Intent(android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                android.net.Uri.parse("package:$packageName")))
        }
    }

    companion object {
        /** 这次启动已经问过电池优化了. */
        private var batteryAsked = false
    }

    override fun onRestart() {
        super.onRestart()
        // 从后台回到 OIT (再次打开): 断网用的 VPN 不在了就重新启动
        if (flowStarted) ensureVpn()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        com.obliphur.oit.service.CoreService.start(this)
        // 每次打开应用时检查更新 (MECHANICS 16)
        if (savedInstanceState == null) app.checkUpdateOnOpen()
        pageRequest.value = intent?.getStringExtra("page")
        // 界面主题在打开界面时定下来, 改了要重启才生效 (MECHANICS 14.8)
        uiTheme = app.core.settings.str("THEME")
        setContent { Root(this, pageRequest) }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        intent.getStringExtra("page")?.let { pageRequest.value = it }
    }

    /** 有人在操作本机 (设备同步: 整点提示音只在最后操作的设备上响). */
    override fun onUserInteraction() {
        super.onUserInteraction()
        app.core.lastOp = System.currentTimeMillis()
    }

    override fun onResume() {
        super.onResume()
        app.foreground = true
        app.core.uiForeground = true
        // 前台服务没在运行 (进程是在后台被拉起来的, 当时不允许启动): 趁界面在前台补上, 离开后 OIT 才不会被暂停
        com.obliphur.oit.service.CoreService.start(this)
        app.core.tick()
        app.recordManager.tick(); ensureRecordConsent()
        if (app.updateRetry && app.core.settings.bool("UPDATE_CHECK")) {
            app.updateRetry = false
            app.checkUpdate(auto = true)
        }
    }

    override fun onDestroy() {
        // 用户自己退出了 OIT (不是转屏之类): 指令 X 的输入框第三段要写今天的这些时刻
        if (isFinishing && !isChangingConfigurations) runCatching { app.core.prescript.noteClose() }
        super.onDestroy()
    }

    override fun onPause() {
        app.foreground = false
        app.core.uiForeground = false
        runCatching { app.core.prescript.pageChanged() }     // 指令 V: 退到后台就作废
        com.obliphur.oit.service.CoreService.start(this)
        app.core.saveAll()
        super.onPause()
    }
}

val PAGES = listOf(
    "timer" to "⏱  计时器", "net" to "🌐  联网", "rules" to "🛡  规则", "log" to "📝  整点记录",
    "cal" to "📅  日历", "stats" to "📊  统计", "contract" to "⚔  合约", "settings" to "⚙  设置", "reset" to "🔄  重置",
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun Root(act: MainActivity, pageRequest: MutableStateFlow<String?>) {
    val app = OitApp.instance
    val v by app.version.collectAsState()
    val notices by app.notices.collectAsState()
    val core = app.core
    val theme = act.uiTheme
    I18n.noIcons = theme == "urban_will"          // 都市之意不画图标, 也不显示风味文本
    I18n.noFlavor = theme == "urban_will"
    val fs = core.settings.str("FONT_SCALE").toFloatOrNull() ?: 1f
    var page by rememberSaveable { mutableStateOf("timer") }
    // 指令是都市之意主题独占的页面, 放在所有页面的第一位 (MECHANICS 22)
    val pages = if (theme == "urban_will") listOf("prescript" to "📜  指令") + PAGES else PAGES
    var prescriptBack by rememberSaveable { mutableStateOf("timer") }
    var settingsTab by rememberSaveable { mutableStateOf("basic") }
    val drawer = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    val snack = remember { SnackbarHostState() }
    var snackKind by remember { mutableStateOf("info") }
    val req by pageRequest.collectAsState()
    // 还没有用户名: 先填写 (MECHANICS 1.1), 填好之前不显示其他提示窗
    var needName by remember { mutableStateOf(core.username.isEmpty()) }

    fun go(p: String) {
        val parts = p.split(":")
        // 合约 · 黑雾 (软规则): 要切到计时器以外的页面记一次, 一个钟点里满 50 次算一次违反 (MECHANICS 18.3)
        if (parts[0] != page && parts[0] != "timer") core.fogSwitch()
        // 黑雾: 无法打开设置 (成就页在设置里)
        if (parts[0] in setOf("settings", "ach") && core.contract.fog()) {
            core.toast(tr("黑雾笼罩: 合约期间无法打开设置."), "warn"); return
        }
        if (parts[0] == "prescript" && page != "prescript") prescriptBack = page
        page = parts[0]
        if (parts.size > 1) settingsTab = parts[1]
    }
    LaunchedEffect(req) { req?.let { go(it); pageRequest.value = null } }
    // 都市之意的像素字体: 第一次用时下载 (不放进安装包), 下不下来隔一分钟重试
    val contractUrban = core.settings.str("CONTRACT_THEME") == "urban"      // 合约页主题选了都市之意也要这个字体
    LaunchedEffect(theme, contractUrban) {
        if (theme != "urban_will" && !contractUrban) return@LaunchedEffect
        if (!PixelFont.present(act)) core.toast(tr("正在下载像素字体 (5 MB)…"), "info")
        var failed = false
        while (!PixelFont.ensure(act)) {
            if (!failed) core.toast(tr("像素字体没有下载下来, 稍后自动重试."), "warn")
            failed = true
            kotlinx.coroutines.delay(60_000)
        }
        if (failed) core.toast(tr("像素字体已下载."), "success")
    }
    // 合约页总是横屏显示, 离开后恢复 (MECHANICS 18.6)
    // 合约页也藏起状态栏 (从顶部下滑时临时出现), 让整页在一屏里显示得大一些
    LaunchedEffect(page) {
        core.currentPage = page
        core.notePrescriptPage(page == "prescript")           // 进入 / 退出指令页: 马上同步, 其他设备跟着 (MECHANICS 22.8)
        runCatching { core.prescript.pageChanged() }          // 指令 V 要一直停在指令页, VI 要连续停在统计页
        act.requestedOrientation = if (page == "contract") android.content.pm.ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
        else android.content.pm.ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        val bars = androidx.core.view.WindowCompat.getInsetsController(act.window, act.window.decorView)
        if (page == "contract" || page == "prescript") {
            bars.systemBarsBehavior = androidx.core.view.WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            bars.hide(androidx.core.view.WindowInsetsCompat.Type.statusBars())
        } else bars.show(androidx.core.view.WindowInsetsCompat.Type.statusBars())
    }
    LaunchedEffect(Unit) { app.navRequests.collect { go(it) } }
    // 手机上退出应用 / 收入后台 (熄屏不算) 视为退出指令页, 回到计时器页 (MECHANICS 22.8)
    androidx.compose.runtime.DisposableEffect(act) {
        val obs = androidx.lifecycle.LifecycleEventObserver { _, e ->
            if (e == androidx.lifecycle.Lifecycle.Event.ON_STOP && core.currentPage == "prescript") {
                val interactive = act.getSystemService(android.os.PowerManager::class.java)?.isInteractive != false
                if (interactive) {
                    core.notePrescriptPage(false)
                    core.currentPage = "timer"
                    page = "timer"
                }
            }
        }
        act.lifecycle.addObserver(obs)
        onDispose { act.lifecycle.removeObserver(obs) }
    }
    // 合约录像 (MECHANICS 18.8): 界面在前台时, 若合约要录像却缺权限 / 录屏授权, 隔一会儿看一次, 需要就弹窗
    LaunchedEffect(Unit) {
        while (true) {
            if (app.recordManager.needPerms || app.recordManager.needProjection) act.ensureRecordConsent()
            kotlinx.coroutines.delay(1500)
        }
    }
    // 填好用户名, 欢迎窗口也关掉之后 (或者本来就没有) 再依次申请权限
    val welcomeDone = !needName && notices.none { it.tag == "welcome" }
    LaunchedEffect(welcomeDone) {
        if (welcomeDone) act.startPermissionFlow()
    }
    LaunchedEffect(Unit) {
        app.toasts.collect {
            snackKind = it.kind
            launch { snack.showSnackbar(it.text, duration = if (it.long) SnackbarDuration.Long else SnackbarDuration.Short) }
        }
    }

    // 换主题 / 字号 / 语言时和电脑版一样盖一层加载页, 界面在它后面换好 (MECHANICS 11)
    val look = "$fs|${core.settings.str("LANGUAGE")}"
    var shownLook by remember { mutableStateOf(look) }
    var reloading by remember { mutableStateOf(false) }
    LaunchedEffect(look) {
        if (look != shownLook) {
            reloading = true
            kotlinx.coroutines.delay(700)
            shownLook = look
            reloading = false
        }
    }

    OitTheme(theme, fs) {
        CompositionLocalProvider(LocalTick provides v) {
            val o = LocalOit.current
            // 合约页 (合约页主题不是"跟随界面主题"时): 顶栏和侧边栏换成合约页主题的颜色 (MECHANICS 18)
            val cp = if (page == "contract") contractColors().takeIf { it.side != null } else null
            val sideBg = cp?.side ?: o.card
            fun navLabel(key: String, base: String): String = when (key) {
                "log" -> if (!core.dm.hourEnabled) "$base  (off)" else base + if (core.dm.awaitingSlot() != null) " ●" else ""
                "settings" -> base + (if (core.settings.pending.isNotEmpty()) "  ⏳${core.settings.pending.size}" else "")
                else -> base
            }.let { I18n.ic(it) }
            ModalNavigationDrawer(drawerState = drawer, drawerContent = {
                ModalDrawerSheet(drawerContainerColor = sideBg) {
                    Text("OIT", fontWeight = FontWeight.Bold, fontSize = 20.sp, color = cp?.text ?: o.text,
                        modifier = Modifier.padding(20.dp))
                    HorizontalDivider(color = cp?.line ?: o.border)
                    Spacer(Modifier.height(8.dp))
                    for ((key, label) in pages) {
                        val on = key == page || (key == "settings" && page == "ach")
                        // 指令标记: 持有合约 / 统计任务时, 在对应入口上盖一朵几乎看不见的花
                        Box(Modifier.fillMaxWidth().background(if (cp != null && on) cp.sideOn else sideBg).clickable {
                            go(key); scope.launch { drawer.close() }
                        }, contentAlignment = Alignment.CenterStart) {
                            Text(navLabel(key, tr(label)), color = if (cp != null) (if (on) cp.startText else cp.sideText)
                                else if (on) o.accent else o.text, fontSize = 16.sp,
                                fontWeight = if (on) FontWeight.Bold else FontWeight.Normal,
                                modifier = Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 14.dp))
                            if ((key == "contract" && prescriptMark("contract")) || (key == "stats" && prescriptMark("stats")))
                                PrescriptMark(Modifier.align(Alignment.CenterEnd).padding(end = 24.dp))
                        }
                    }
                }
            }) {
                Scaffold(
                    containerColor = cp?.bg ?: o.bg,
                    topBar = {
                        TopAppBar(
                            title = { Text(if (page == "ach") tr("🏆  成就") else navLabel(page, tr(pages.first { it.first == page }.second))) },
                            navigationIcon = {
                                TextButton({ scope.launch { drawer.open() } }) { Text(I18n.ic("☰"), fontSize = 22.sp, color = cp?.text ?: o.text) }
                            },
                            colors = TopAppBarDefaults.topAppBarColors(containerColor = cp?.side ?: o.bg,
                                titleContentColor = cp?.text ?: o.text),
                            // 合约页 (横屏) 的顶栏矮一些
                            expandedHeight = if (page == "contract") 44.dp else TopAppBarDefaults.TopAppBarExpandedHeight,
                        )
                    },
                    snackbarHost = {
                        SnackbarHost(snack) { d ->
                            val c = when (snackKind) { "success" -> o.success; "warn" -> o.warn; "error" -> o.danger; else -> o.accent }
                            Snackbar(d, containerColor = o.card, contentColor = o.text, actionColor = c,
                                shape = RoundedCornerShape(8.dp), modifier = Modifier.padding(8.dp))
                        }
                    },
                ) { pad ->
                  Box(Modifier.padding(pad).fillMaxSize()) {
                    Column(Modifier.fillMaxSize()) {
                        if (app.vpnNeedsPermission && !core.settings.devMode) {
                            Row(Modifier.fillMaxWidth().background(o.dangerBg).padding(horizontal = 16.dp, vertical = 8.dp),
                                verticalAlignment = Alignment.CenterVertically) {
                                Text(tr("断网功能已停止"), color = o.danger, modifier = Modifier.weight(1f))
                                DangerButton(tr("授予 VPN 权限")) { act.requestVpn() }
                            }
                        } else if (com.obliphur.oit.service.OitVpnService.leak && !core.settings.devMode) {
                            Row(Modifier.fillMaxWidth().background(o.dangerBg).padding(horizontal = 16.dp, vertical = 8.dp),
                                verticalAlignment = Alignment.CenterVertically) {
                                Text(tr("断网未生效"), color = o.danger, modifier = Modifier.weight(1f))
                                DangerButton(tr("重新建立 VPN")) { com.obliphur.oit.service.OitVpnService.rebuild(act) }
                            }
                        }
                        // 绑定了其他设备, 系统却会在后台 / 熄屏时限制 OIT: 同步会停 (MECHANICS 15.3)
                        // 也包括: 查到 OIT 最近确实被系统冻结过 (手机厂商自己的后台限制, 例如 vivo 没有"允许后台高耗电")
                        val bgPaused = com.obliphur.oit.service.Background.pausedRecently()
                        if (core.hasPeers() && (!com.obliphur.oit.service.Background.ok(act) || bgPaused)) {
                            Row(Modifier.fillMaxWidth().background(o.dangerBg).padding(horizontal = 16.dp, vertical = 8.dp),
                                verticalAlignment = Alignment.CenterVertically) {
                                Text(if (bgPaused) tr("OIT 在后台被系统暂停过, 这段时间没有同步") else tr("在后台 / 熄屏时同步会被系统暂停"),
                                    color = o.danger, modifier = Modifier.weight(1f))
                                DangerButton(tr("去解除")) {
                                    val bg = com.obliphur.oit.service.Background
                                    when {
                                        !bg.batteryIgnored(act) -> bg.requestBattery(act)
                                        bgPaused && bg.hasOemPage(act) -> bg.openOem(act)
                                        else -> go("settings:basic")
                                    }
                                }
                            }
                        }
                        val scroll = rememberScrollState()
                        val scrollable = page != "log" && page != "stats" && page != "contract"
                        Column(if (scrollable) Modifier.verticalScroll(scroll) else Modifier.fillMaxSize()) {
                            when (page) {
                                "timer" -> TimerScreen()
                                "net" -> NetScreen()
                                "rules" -> RulesScreen { go(it) }
                                "log" -> HourLogScreen { go(it) }
                                "cal" -> CalendarScreen()
                                "stats" -> StatsScreen()
                                "contract" -> {
                                    // 合约页主题是都市之意: 合约页换成像素字体 (没下载好之前用系统字体)
                                    val px = if (core.settings.str("CONTRACT_THEME") == "urban") PixelFont.family.value else null
                                    if (px != null) androidx.compose.material3.ProvideTextStyle(
                                        androidx.compose.ui.text.TextStyle(fontFamily = px)) { ContractScreen() }
                                    else ContractScreen()
                                }
                                "ach" -> {
                                    androidx.activity.compose.BackHandler { go("settings:basic") }
                                    AchievementScreen { go("settings:basic") }
                                }
                                "settings" -> SettingsScreen(settingsTab, { settingsTab = it }, act)
                                "reset" -> ResetScreen()
                            }
                        }
                    }
                    // "我累了" 固定在 Timer 页的右下角
                    if (page == "timer") BonusCorner(Modifier.align(Alignment.BottomEnd))
                  }
                }
            }
            // 指令页整页盖在界面上 (遮住顶栏), 左上角 × 或返回键退出
            if (page == "prescript") PrescriptScreen { go(prescriptBack.takeIf { it != "prescript" } ?: "timer") }
            // 换走了都市之意主题: 指令页不存在了
            if (page == "prescript" && theme != "urban_will") LaunchedEffect(Unit) { page = "timer" }
            // 达成成就的提示浮在界面上, 手动关闭才消失 (MECHANICS 17)
            AchievementPopups { go("ach") }
            // 黑雾开始时正看着设置 / 成就: 离开
            if (page in setOf("settings", "ach") && core.contract.fog()) LaunchedEffect(Unit) { page = "timer" }
            if (needName) NameDialog { core.setUsername(it); needName = false }
            else notices.firstOrNull()?.let { n ->
                AlertDialog(
                    onDismissRequest = { core.closeNotice(n.id, null) },
                    title = { Text(n.title) },
                    text = { Text(n.message) },
                    confirmButton = {
                        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                            for (b in n.buttons.sortedBy { it.primary }) {
                                TextButton({
                                    core.closeNotice(n.id, b.action)
                                    n.page?.let { p -> if (b.action != "ok") go(p) }
                                }) { Text(b.label, color = if (b.primary) o.accent else o.dim) }
                            }
                        }
                    },
                    containerColor = o.bg,
                )
            }
            // 睡眠 (MECHANICS 23): 盖住整个界面, 空白背景, 正中一个"起床"; 程序 (VPN 防修改, 同步, 自启动) 照常运行
            if (core.timer.sleepSince != null) {
                androidx.activity.compose.BackHandler { }
                androidx.compose.foundation.layout.Box(
                    Modifier.fillMaxSize().background(o.bg).clickable(enabled = true, onClick = {}),
                    contentAlignment = Alignment.Center,
                ) {
                    if (theme == "urban_will") {
                        // 都市之意: 正中是指令页的那个图标, "醒来" 在它下面一点
                        androidx.compose.foundation.Image(androidx.compose.ui.res.painterResource(com.obliphur.oit.R.drawable.prescript_index),
                            contentDescription = null, modifier = Modifier.size(128.dp))
                        Box(Modifier.align(Alignment.Center).offset(y = 120.dp)) {
                            AccentButton(tr("醒来")) { core.timer.wake() }
                        }
                    } else AccentButton(tr("☀  起床")) { core.timer.wake() }
                }
            }
            if (reloading) {
                androidx.compose.foundation.layout.Box(
                    Modifier.fillMaxSize().background(o.bg)
                        .clickable(enabled = true, onClick = {}),        // 加载中不响应点击
                    contentAlignment = Alignment.Center,
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(tr("正在重新加载界面…"), color = o.text, fontSize = 18.sp, fontWeight = FontWeight.Bold)
                        Text(tr("请稍候"), color = o.dim, fontSize = 13.sp, modifier = Modifier.padding(top = 6.dp))
                        androidx.compose.material3.LinearProgressIndicator(color = o.accent, trackColor = o.border,
                            modifier = Modifier.padding(top = 18.dp).fillMaxWidth(0.6f))
                    }
                }
            }
        }
    }
}

/** 填写用户名 (MECHANICS 1.1): 不填就关不掉. */
@Composable
private fun NameDialog(onDone: (String) -> Unit) {
    val o = com.obliphur.oit.ui.LocalOit.current
    var name by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = {},
        title = { Text(tr("👤  你的名字")) },
        text = {
            androidx.compose.foundation.layout.Column {
                Text(tr("初次见面.怎么称呼你?"))
                OutlinedTextField(name, { name = it.take(com.obliphur.oit.core.Cfg.USERNAME_MAX) }, singleLine = true,
                    modifier = Modifier.padding(top = 12.dp))
            }
        },
        confirmButton = {
            TextButton({ onDone(name.trim()) }, enabled = name.isNotBlank()) {
                Text(tr("确定"), color = if (name.isNotBlank()) o.accent else o.dim)
            }
        },
        containerColor = o.bg,
        properties = androidx.compose.ui.window.DialogProperties(dismissOnBackPress = false, dismissOnClickOutside = false),
    )
}
