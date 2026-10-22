package com.obliphur.oit

import android.app.Application
import android.content.Intent
import android.net.VpnService
import android.os.SystemClock
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.obliphur.oit.core.Core
import com.obliphur.oit.core.Host
import com.obliphur.oit.core.I18n
import com.obliphur.oit.core.Notice
import com.obliphur.oit.core.Toast
import com.obliphur.oit.core.VpnPlan
import com.obliphur.oit.core.RecCrypto
import com.obliphur.oit.core.tr
import com.obliphur.oit.service.AppUsage
import com.obliphur.oit.service.BlockLog
import com.obliphur.oit.service.Chime
import com.obliphur.oit.service.CoreService
import com.obliphur.oit.service.Notifier
import com.obliphur.oit.service.OitVpnService
import com.obliphur.oit.service.Waker
import com.obliphur.oit.ui.MainActivity
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import java.io.File
import java.time.LocalDateTime

class OitApp : Application(), Host {
    companion object {
        lateinit var instance: OitApp
            private set
    }

    lateinit var core: Core
        private set
    /** 设备同步 (MECHANICS 15). */
    var sync: com.obliphur.oit.sync.SyncService? = null
        private set
    private var multicast: android.net.wifi.WifiManager.MulticastLock? = null

    /** 每次状态变化 +1; 界面观察它来刷新. */
    val version = MutableStateFlow(0L)
    val toasts = MutableSharedFlow<Toast>(extraBufferCapacity = 32)
    val notices = MutableStateFlow<List<Notice>>(emptyList())
    val navRequests = MutableSharedFlow<String>(extraBufferCapacity = 8)
    /** 界面在前台 (提示直接显示在界面上, 否则发通知). */
    var foreground = false
    /** 需要用户 (重新) 授予 VPN 权限. */
    var vpnNeedsPermission by mutableStateOf(false)

    private val oitDir get() = File(filesDir, "data")

    override fun onCreate() {
        super.onCreate()
        instance = this
        core = Core(oitDir, this) { SystemClock.elapsedRealtime() / 1000.0 }
        I18n.lang = core.settings.str("LANGUAGE")
        Notifier.channels(this)
        startSync()
        core.start()
        wipedNotice()
        CoreService.start(this)
        listenShutdown()
        // 上次没加密完的录像 (被强杀时留在缓存里的) 开机时补上加密 (MECHANICS 18.8)
        Thread { runCatching { RecCrypto.sealLeftovers(oitDir, cacheDir) } }.apply { isDaemon = true }.start()
    }

    /** 合约录像的调度 (MECHANICS 18.8): CoreService 每秒 tick 一次. */
    val recordManager by lazy { com.obliphur.oit.service.RecordManager(this) }

    /** 录屏的系统授权 (每次用一次; MainActivity 弹出授权后存进来, RecordManager 取用). */
    @Volatile var recProjection: Pair<Int, Intent>? = null

    /** 这次开机后 OIT 是被系统自动启动的 (开机自启动没被关掉, MECHANICS 14.8): BootReceiver 记下的开机次数等于现在的.
     *  厂商的"自启动"开关查不到, 只能这样事后确认; 还没确认过 (例如装好后还没重启过) 时算没开. */
    fun autostartOk(): Boolean {
        val now = bootCount()
        return now >= 0 && getSharedPreferences("oit_boot", MODE_PRIVATE).getInt("boot_seen", -2) == now
    }

    fun noteBootStart() {
        val n = bootCount()
        if (n >= 0) getSharedPreferences("oit_boot", MODE_PRIVATE).edit().putInt("boot_seen", n).apply()
    }

    private fun bootCount(): Int = runCatching {
        android.provider.Settings.Global.getInt(contentResolver, android.provider.Settings.Global.BOOT_COUNT, -1)
    }.getOrDefault(-1)

    /** 收到了系统的关机指令 (MECHANICS 15.4). */
    @Volatile var powerOff = false
        private set

    /** 本机电量 (%, 只在没充电时); 查不到 / 正在充电时 null. */
    fun battery(): Int? = runCatching {
        val bm = getSystemService(android.os.BatteryManager::class.java) ?: return null
        if (bm.isCharging) return null
        bm.getIntProperty(android.os.BatteryManager.BATTERY_PROPERTY_CAPACITY).takeIf { it in 0..100 }
    }.getOrNull()

    /** 关机 (包括电量耗尽时的自动关机): 存下专注状态 (记上"关机"), 和绑定的设备同步一轮, 让它们把本机算作"关机" ——
     *  效果和在线一样, 不会因为本机离线把专注记成空闲 / 让合约失败. 同步要用界面线程, 所以在后台线程里等. */
    private fun listenShutdown() {
        val f = android.content.IntentFilter(android.content.Intent.ACTION_SHUTDOWN).apply {
            addAction("android.intent.action.QUICKBOOT_POWEROFF")          // 部分厂商的快速关机
            addAction("com.htc.intent.action.QUICKBOOT_POWEROFF")
        }
        val r = object : android.content.BroadcastReceiver() {
            override fun onReceive(ctx: android.content.Context, intent: android.content.Intent) {
                powerOff = true
                runCatching { core.beatPower(force = true); core.timer.saveNow() }
                val s = sync ?: return
                if (s.peers().isEmpty()) return
                val pending = goAsync()
                Thread {
                    try { s.pushFinal(3000) } finally { pending.finish() }
                }.apply { isDaemon = true }.start()
            }
        }
        runCatching {
            if (android.os.Build.VERSION.SDK_INT >= 33) registerReceiver(r, f, android.content.Context.RECEIVER_EXPORTED)
            else registerReceiver(r, f)
        }
    }

    private fun deviceName(): String =
        android.provider.Settings.Global.getString(contentResolver, "device_name")?.takeIf { it.isNotBlank() }
            ?: "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}"

    /** 设备同步: 其他设备的账并进额度和统计; 监听局域网里的发现和同步请求. */
    private fun startSync() {
        val main = android.os.Handler(mainLooper)
        val runner = com.obliphur.oit.sync.MainRunner({ main.post(it) }, { android.os.Looper.myLooper() == mainLooper })
        val s = com.obliphur.oit.sync.SyncService(oitDir, com.obliphur.oit.sync.AndroidLocal(this), runner,
            defaultName = deviceName())
        sync = s
        core.dm.peers = { s.peerLedgers() }
        core.dm.peersById = { s.peerLedgersById() }
        core.dm.myId = { s.myId }
        core.dm.motherId = { s.motherId() }
        core.myId = { s.myId }
        core.peerFresh = { s.isFresh(it) }
        core.isLatestActive = { s.isLatestActive() }
        core.hasPeers = { s.peers().isNotEmpty() }
        core.syncedSince = { ms -> s.peers().values.any { it.optLong("last_ok") >= ms } }
        core.popAdapterBack = { s.popAdapterBack() }
        core.addSelfOff = { a, b -> s.addSelfOff(a, b) }
        core.wentOff = { autostartOk() && (powerOff || (battery()?.let { it < com.obliphur.oit.sync.SyncService.LOW_BATTERY } == true)) }
        core.followsMother = { s.motherId()?.let { it != s.myId } == true }
        core.motherFresh = {
            val m = s.motherId()
            m == null || System.currentTimeMillis() - (s.peers()[m]?.optLong("last_ok") ?: 0L) < com.obliphur.oit.sync.SyncService.FRESH_MS
        }
        core.devChangedHook = { if (s.peers().isNotEmpty()) s.syncNow() }
        core.offlineSpans = { a, b -> s.offlineSpans(a, b, own = false) }
        core.selfOffSpans = { a, b -> s.selfOffSpans(a, b) }
        core.offlineDevices = { s.deviceStates().filter { !it.online }.map { it.name to it.seen } }
        core.versionMismatch = { s.versionMismatch() }
        core.syncStartedMs = { s.startedMs }
        core.checkUnbind = { s.checkUnbind() }
        core.dm.offlineSpans = { a, b -> s.offlineSpans(a, b) }
        core.trackOffline = { s.trackOffline() }
        core.net.peerNet = { com.obliphur.oit.sync.SyncService.peerNet(s.peerLedgers(), core.net.date) }
        core.net.peerUsage = { s.peerLedgers().flatMap { l -> l.optJSONArray("usage")?.let { a -> (0 until a.length()).mapNotNull { a.optJSONObject(it) } } ?: emptyList() } }
        try {
            if (multicast == null) multicast = getSystemService(android.net.wifi.WifiManager::class.java)
                ?.createMulticastLock("oit-sync")?.apply { setReferenceCounted(false); acquire() }
        } catch (_: Exception) {
        }
        Thread { runCatching { s.start() } }.start()
    }

    // ---- Host ----

    override fun toast(t: Toast) {
        toasts.tryEmit(t)
        if (!foreground) Notifier.event(this, t.text)
    }

    override fun noticesChanged() {
        val list = core.notices.toList()
        notices.value = list
        Notifier.syncNotices(this, list)
    }

    override fun changed() {
        I18n.lang = core.settings.str("LANGUAGE")
        version.value = version.value + 1
    }

    override fun applyNet(force: Boolean) {
        val plan = core.vpnPlan()
        if (plan == VpnPlan.None) {
            OitVpnService.stop(this)
            return
        }
        if (VpnService.prepare(this) != null) {
            vpnFailed()
            return
        }
        if (vpnNeedsPermission) {
            vpnNeedsPermission = false
            Notifier.cancelVpn(this)
        }
        if (force || OitVpnService.instance == null || OitVpnService.plan != plan) {
            try {
                OitVpnService.apply(this)
            } catch (_: Exception) {
                // 后台启动服务被拒: 下一次复查时再试
            }
        }
    }

    fun vpnFailed() {
        val need = VpnService.prepare(this) != null
        if (need && !vpnNeedsPermission && !foreground) Notifier.vpnPermission(this)
        vpnNeedsPermission = need
        changed()
    }

    fun vpnChanged() = changed()

    override fun playChime(done: () -> Unit) = Chime.play(this, core.settings.str("CHIME_WAV"), done)

    override fun hourPrompt(slot: LocalDateTime, silent: Boolean) {
        Notifier.hourPrompt(this, tr("{a} ~ {b} 做了什么?",
            "a" to slot.format(com.obliphur.oit.core.Fmt.HM), "b" to slot.plusHours(1).format(com.obliphur.oit.core.Fmt.HM)), silent)
    }

    override fun foregroundApp(): String? = AppUsage.current(this)

    override fun lockApp(pkg: String, why: String) = com.obliphur.oit.service.AppLock.lock(this, pkg, why).let {}

    override fun unlockApp() = com.obliphur.oit.service.AppLock.unlock().let {}

    override fun lockExempt(pkg: String, whitelist: Boolean) = com.obliphur.oit.service.AppLock.exempt(this, pkg, whitelist)

    override fun openApps(): Set<String>? = AppUsage.openApps(this)

    /** 违反合约的警报 (MECHANICS 18). */
    override fun playAlarm() {
        runCatching {
            val mp = android.media.MediaPlayer.create(this, R.raw.target_alarm,
                android.media.AudioAttributes.Builder().setUsage(android.media.AudioAttributes.USAGE_ALARM)
                    .setContentType(android.media.AudioAttributes.CONTENT_TYPE_SONIFICATION).build(), 0) ?: return
            mp.setOnCompletionListener { it.release() }
            mp.start()
        }
    }

    override fun achievementsUnlocked(ids: List<String>) {
        // 界面上的提示由 Root 画出; 不在前台时另外发一条普通通知
        if (!foreground) for (id in ids) com.obliphur.oit.core.AchievementStore.BY_ID[id]?.let {
            Notifier.event(this, tr("🏆  达成成就") + ": " + com.obliphur.oit.core.AchievementStore.name(it))
        }
    }

    override fun openPage(page: String) {
        navRequests.tryEmit(page)
        if (!foreground) startActivity(Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK).putExtra("page", page))
    }

    override fun scheduleWake(seconds: Double) = Waker.schedule(this, seconds)

    // ---- 更新 (MECHANICS 16) ----

    val updater by lazy { com.obliphur.oit.service.Updater(this) }
    /** 打开时检查失败了 (例如没网): 下一次联网时再查. */
    var updateRetry = false

    private fun onMain(fn: () -> Unit) = android.os.Handler(mainLooper).post(fn)

    /** 在界面线程里执行 (后台线程做完后回来更新界面用). */
    fun main(fn: () -> Unit) {
        android.os.Handler(mainLooper).post(fn)
    }

    /** 每次打开界面时 (设置里开着"打开时检查更新"). */
    fun checkUpdateOnOpen() {
        if (core.settings.bool("UPDATE_CHECK")) checkUpdate(auto = true)
    }

    fun checkUpdate(auto: Boolean) {
        updater.check { onMain { afterCheck(auto) } }
        changed()
    }

    private fun afterCheck(auto: Boolean) {
        val u = updater
        val cur = tr("当前版本 {v}", "v" to com.obliphur.oit.BuildConfig.VERSION_NAME)
        when (u.state) {
            "available" -> if (auto && core.settings.bool("UPDATE_AUTO")) downloadUpdate()
            else core.notice(tr("⬆  发现新版本"), cur + "\n" + tr("发现新版本 {v}", "v" to u.latestText), listOf(
                com.obliphur.oit.core.NoticeButton(tr("立即更新"), "update", true),
                com.obliphur.oit.core.NoticeButton(tr("稍后"), "ok", false))) { if (it == "update") downloadUpdate() }
            "noapk" -> if (!auto) toast(Toast(tr("发现新版本 {v}", "v" to u.latestText) + "\n" + tr("这个发布里没有安卓安装包"), "warn", true))
            "latest" -> if (!auto) toast(Toast(tr("已是最新版本") + " (${com.obliphur.oit.BuildConfig.VERSION_NAME})", "info", false))
            "error" -> if (auto) updateRetry = true else toast(Toast(tr("检查 / 更新失败: {e}", "e" to u.error), "warn", true))
        }
        changed()
    }

    fun downloadUpdate() {
        toast(Toast(tr("正在下载更新…"), "info", false))
        // 20 秒后还很慢 (这台手机直连 GitHub 常常只有几十 KB/s): 同时请绑定的设备下载好发过来, 谁先到用谁
        android.os.Handler(mainLooper).postDelayed({
            val u = updater
            if (u.state == "downloading" && u.speed < 200 * 1024 && (u.total <= 0 || u.got < u.total / 2)) askPeersForUpdate(false)
        }, 20_000)
        updater.download {
            onMain {
                when (updater.state) {
                    "ready" -> core.notice(tr("⬆  新版本已下载"), tr("新版本 {v} 已下载, 点安装打开系统的安装界面.", "v" to updater.latestText),
                        listOf(com.obliphur.oit.core.NoticeButton(tr("安装"), "install", true),
                            com.obliphur.oit.core.NoticeButton(tr("稍后"), "ok", false))) { if (it == "install") updater.install() }
                    "error" -> {
                        toast(Toast(tr("检查 / 更新失败: {e}", "e" to updater.error), "warn", true))
                        // 从 GitHub 下不动: 请绑定的设备 (例如开着代理的电脑) 下载好发过来
                        askPeersForUpdate(false)
                    }
                }
                changed()
            }
        }
        changed()
    }

    // ---- 安装包直接发给其他设备 (MECHANICS 16) ----

    @Volatile private var relaying = false
    private val relayed = java.util.Collections.synchronizedSet(HashSet<String>())

    /** 自动更新开着: 绑定的设备版本比 GitHub 上的旧, 就给它下载并发过去 (每台设备每个版本一次). */
    fun maybeRelay() {
        if (!core.settings.bool("UPDATE_AUTO") || relaying) return
        val latest = updater.latest ?: return
        val s = sync ?: return
        if (s.peers().any { (id, p) ->
                val pv = com.obliphur.oit.service.Updater.parseVersion(p.optString("version"))
                pv != null && com.obliphur.oit.service.Updater.newer(latest, pv) &&
                    "$id@${com.obliphur.oit.service.Updater.text(latest)}" !in relayed
            }) relayUpdates(false)
    }

    /** only: 只发给这一台 (它请这台设备发过去的). 返回 false: 正在发送 / 没有设备. */
    fun relayUpdates(manual: Boolean, only: String? = null): Boolean {
        val s = sync ?: return false
        if (relaying) {
            if (manual) toast(Toast(tr("正在发送…"), "info", false))
            return false
        }
        val peers = s.peers().filter { it.value.optString("platform") in setOf("windows", "android") && (only == null || it.key == only) }
        if (peers.isEmpty()) {
            if (manual) toast(Toast(tr("没有绑定的设备."), "info", false))
            return false
        }
        relaying = true
        if (manual) toast(Toast(tr("正在给其他设备下载新版本…"), "info", false))
        Thread {
            val out = mutableListOf<Pair<String, String>>()
            try {
                val rel = updater.fetchRelease()
                val latest = com.obliphur.oit.service.Updater.releaseVersion(rel)!!
                val vt = com.obliphur.oit.service.Updater.text(latest)
                for ((id, p) in peers) {
                    val name = p.optString("name", "?")
                    val pv = com.obliphur.oit.service.Updater.parseVersion(p.optString("version"))
                    if (pv != null && !com.obliphur.oit.service.Updater.newer(latest, pv)) { out.add(name to "latest"); continue }
                    if (pv == null && !manual) continue      // 旧版本的 OIT 不报版本号, 也收不了: 只在手动时试
                    relayed.add("$id@$vt")
                    try {
                        val f = updater.fetchPackage(rel, p.optString("platform"))
                        out.add(name to s.offerUpdate(id, f, p.optString("platform"), vt))
                    } catch (e: Exception) {
                        out.add(name to (e.message ?: e.javaClass.simpleName))
                    }
                }
            } catch (e: Exception) {
                out.add("GitHub" to (e.message ?: e.javaClass.simpleName))
            } finally {
                relaying = false
            }
            onMain { reportRelay(out, manual) }
        }.start()
        return true
    }

    /** 请绑定的设备给这台手机下载新版本并从局域网发过来 (这台手机连 GitHub 下不动时; 对方例如电脑开着代理).
     *  下载失败时自动请一次 (manual = false 时只在有设备答应时提示). */
    fun askPeersForUpdate(manual: Boolean) {
        val s = sync ?: return
        val peers = s.peers().filter { it.value.optString("platform") in setOf("windows", "android") }
        if (peers.isEmpty()) {
            if (manual) toast(Toast(tr("没有绑定的设备."), "info", false))
            return
        }
        Thread {
            val out = peers.map { (id, p) ->
                p.optString("name", "?") to try { s.requestUpdate(id) } catch (e: Exception) { e.message ?: e.javaClass.simpleName }
            }
            onMain {
                val words = mapOf("asked" to tr("已请它下载, 下好后会发过来"), "old" to tr("对方的 OIT 版本太旧, 不支持"),
                    "busy" to tr("对方正在发送, 稍后再试"))
                val ok = out.any { it.second == "asked" }
                if (manual || ok) toast(Toast(tr("📥 让其他设备发来") + "\n" + out.joinToString("\n") { (n, st) ->
                    "$n: " + (words[st] ?: com.obliphur.oit.ui.syncErrorText(st)) }, if (ok) "success" else "info", true))
            }
        }.start()
    }

    private fun reportRelay(out: List<Pair<String, String>>, manual: Boolean) {
        val words = mapOf("sent" to tr("已发送, 对方正在接收"), "latest" to tr("已是最新版本"),
            "platform" to tr("对方不收这个平台的安装包"), "not-newer" to tr("对方的版本不比这个旧"),
            "dev" to tr("对方是 git 仓库里的开发版本"), "no-package" to tr("这个发布里没有对方平台的安装包"))
        val sent = out.any { it.second == "sent" }
        if (out.isEmpty() || (!manual && !sent)) return
        toast(Toast(tr("📤 发给其他设备") + "\n" + out.joinToString("\n") { (n, st) ->
            "$n: " + (words[st] ?: com.obliphur.oit.ui.syncErrorText(st)) }, if (sent) "success" else "info", true))
    }

    // ---- 重置 ----

    /** 删除全部数据并像第一次安装那样重新开始 (MECHANICS 9). 先请绑定的设备也清空数据:
     *  重置时绑定保留 (只留下设备同步的绑定), 卸载 (uninstall) 时同时解除绑定.
     *  wipeId / exclude / by: 是别的设备 (名字 by) 请本机清空的 —— 同一个请求转告给本机绑定的其他设备. */
    @Volatile private var resetting = false

    fun resetAll(wipeId: String? = null, exclude: String? = null, by: String = "", uninstall: Boolean = false) {
        if (resetting) return
        resetting = true
        var kept: org.json.JSONObject? = null
        // 不能在界面线程上联网: 放到后台线程, 最多等 3.5 秒
        sync?.let { s ->
            Thread {
                runCatching {
                    s.sendWipe(wipeId ?: com.obliphur.oit.sync.SyncService.newWipeId(), exclude, uninstall)
                    if (!uninstall) kept = s.keptState(by)
                }
            }.apply { start(); join(3500) }
        }
        sync?.stop()
        core.eraseEverything()
        kept?.let { com.obliphur.oit.sync.SyncService.writeState(oitDir, it) }
        BlockLog.clear()
        Notifier.syncNotices(this, emptyList())
        core = Core(oitDir, this) { SystemClock.elapsedRealtime() / 1000.0 }
        I18n.lang = core.settings.str("LANGUAGE")
        startSync()
        core.start()
        noticesChanged()
        resetting = false
        wipedNotice()
    }

    /** 别的设备重置 / 卸载时请本机清空了数据: 重开后提示一次. */
    fun wipedNotice() {
        val who = sync?.popWipedBy() ?: return
        if (who.isNotEmpty()) core.notice(tr("🔄  数据已清空"), tr("{name} 重置了 OIT, 本机的数据也一起清空了.", "name" to who),
            listOf(com.obliphur.oit.core.NoticeButton(tr("知道了"), "ok", true)))
    }
}
