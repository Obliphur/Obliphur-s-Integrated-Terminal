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
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
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
import com.obliphur.oit.core.tr
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private val app get() = OitApp.instance
    private val pageRequest = MutableStateFlow<String?>(null)

    private val vpnLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        asking = false
        app.applyNet(true)
        askBattery()                   // VPN 问完了再问电池优化
    }
    private val notifLauncher = registerForActivityResult(ActivityResultContracts.RequestPermission()) {
        asking = false
        ensureVpn()                    // 通知权限问完了再问 VPN, 一次只弹一个系统窗口
    }
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
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            asking = true
            notifLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else ensureVpn()
    }

    /** VPN 被别的 VPN 应用顶掉或停了: 重新申请 / 重新打开. */
    private fun ensureVpn() {
        if (asking || app.core.settings.devMode) return
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
        app.core.tick()
        if (app.updateRetry && app.core.settings.bool("UPDATE_CHECK")) {
            app.updateRetry = false
            app.checkUpdate(auto = true)
        }
    }

    override fun onPause() {
        app.foreground = false
        app.core.saveAll()
        super.onPause()
    }
}

val PAGES = listOf(
    "timer" to "⏱  计时器", "net" to "🌐  联网", "rules" to "🛡  规则", "log" to "📝  整点记录",
    "cal" to "📅  日历", "stats" to "📊  统计", "settings" to "⚙  设置", "reset" to "🔄  重置",
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun Root(act: MainActivity, pageRequest: MutableStateFlow<String?>) {
    val app = OitApp.instance
    val v by app.version.collectAsState()
    val notices by app.notices.collectAsState()
    val core = app.core
    val theme = act.uiTheme
    val fs = core.settings.str("FONT_SCALE").toFloatOrNull() ?: 1f
    var page by rememberSaveable { mutableStateOf("timer") }
    var settingsTab by rememberSaveable { mutableStateOf("basic") }
    val drawer = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    val snack = remember { SnackbarHostState() }
    var snackKind by remember { mutableStateOf("info") }
    val req by pageRequest.collectAsState()

    fun go(p: String) {
        val parts = p.split(":")
        page = parts[0]
        if (parts.size > 1) settingsTab = parts[1]
    }
    LaunchedEffect(req) { req?.let { go(it); pageRequest.value = null } }
    LaunchedEffect(Unit) { app.navRequests.collect { go(it) } }
    // 欢迎窗口关掉之后 (或者本来就没有) 再依次申请权限
    LaunchedEffect(notices.none { it.tag == "welcome" }) {
        if (notices.none { it.tag == "welcome" }) act.startPermissionFlow()
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
            fun navLabel(key: String, base: String): String = when (key) {
                "log" -> if (!core.dm.hourEnabled) "$base  (off)" else base + if (core.dm.awaitingSlot() != null) " ●" else ""
                "settings" -> base + (if (core.settings.pending.isNotEmpty()) "  ⏳${core.settings.pending.size}" else "")
                else -> base
            }
            ModalNavigationDrawer(drawerState = drawer, drawerContent = {
                ModalDrawerSheet(drawerContainerColor = o.card) {
                    Text("OIT", fontWeight = FontWeight.Bold, fontSize = 20.sp, color = o.text,
                        modifier = Modifier.padding(20.dp))
                    HorizontalDivider(color = o.border)
                    Spacer(Modifier.height(8.dp))
                    for ((key, label) in PAGES) {
                        val on = key == page
                        Text(navLabel(key, tr(label)), color = if (on) o.accent else o.text, fontSize = 16.sp,
                            fontWeight = if (on) FontWeight.Bold else FontWeight.Normal,
                            modifier = Modifier.fillMaxWidth().clickable {
                                page = key; scope.launch { drawer.close() }
                            }.padding(horizontal = 24.dp, vertical = 14.dp))
                    }
                }
            }) {
                Scaffold(
                    containerColor = o.bg,
                    topBar = {
                        TopAppBar(
                            title = { Text(navLabel(page, tr(PAGES.first { it.first == page }.second))) },
                            navigationIcon = {
                                TextButton({ scope.launch { drawer.open() } }) { Text("☰", fontSize = 22.sp, color = o.text) }
                            },
                            colors = TopAppBarDefaults.topAppBarColors(containerColor = o.bg, titleContentColor = o.text),
                        )
                    },
                    snackbarHost = {
                        SnackbarHost(snack) { d ->
                            val c = when (snackKind) { "success" -> o.success; "warn" -> o.warn; else -> o.accent }
                            Snackbar(d, containerColor = o.card, contentColor = o.text, actionColor = c,
                                shape = RoundedCornerShape(8.dp), modifier = Modifier.padding(8.dp))
                        }
                    },
                ) { pad ->
                    Column(Modifier.padding(pad).fillMaxSize()) {
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
                        val scroll = rememberScrollState()
                        val scrollable = page != "log" && page != "stats"
                        Column(if (scrollable) Modifier.verticalScroll(scroll) else Modifier.fillMaxSize()) {
                            when (page) {
                                "timer" -> TimerScreen()
                                "net" -> NetScreen()
                                "rules" -> RulesScreen { go(it) }
                                "log" -> HourLogScreen { go(it) }
                                "cal" -> CalendarScreen()
                                "stats" -> StatsScreen()
                                "settings" -> SettingsScreen(settingsTab, { settingsTab = it }, act)
                                "reset" -> ResetScreen()
                            }
                        }
                    }
                }
            }
            notices.firstOrNull()?.let { n ->
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
