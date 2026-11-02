package com.obliphur.oit.service

import android.app.AlarmManager
import android.app.AppOpsManager
import android.app.KeyguardManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.media.MediaPlayer
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.Process
import android.os.SystemClock
import com.obliphur.oit.OitApp
import com.obliphur.oit.R
import com.obliphur.oit.core.Cfg
import com.obliphur.oit.core.Notice
import com.obliphur.oit.core.TState
import com.obliphur.oit.core.f2
import com.obliphur.oit.core.fmtHm
import com.obliphur.oit.core.fmtMs
import com.obliphur.oit.core.tr
import com.obliphur.oit.ui.MainActivity
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.sin

// ════════════════ 通知 ════════════════

object Notifier {
    const val CH_STATUS = "status"
    const val CH_EVENTS = "events"
    const val CH_ALERTS = "alerts"
    const val ID_STATUS = 1
    private const val ID_EVENT = 2
    private const val ID_HOUR = 3
    private const val ID_VPN = 4
    const val ID_RECORD = 5
    private const val NOTICE_BASE = 1000

    private val posted = HashSet<Int>()

    fun channels(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH_STATUS, tr("运行状态"), NotificationManager.IMPORTANCE_LOW))
        nm.createNotificationChannel(NotificationChannel(CH_EVENTS, tr("提示"), NotificationManager.IMPORTANCE_DEFAULT))
        nm.createNotificationChannel(NotificationChannel(CH_ALERTS, tr("需要手动关闭的提醒"), NotificationManager.IMPORTANCE_HIGH))
    }

    private fun openIntent(ctx: Context, page: String?, code: Int): PendingIntent {
        val i = Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        if (page != null) i.putExtra("page", page)
        return PendingIntent.getActivity(ctx, code, i, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }

    private fun builder(ctx: Context, ch: String) =
        Notification.Builder(ctx, ch).setSmallIcon(R.drawable.ic_stat)

    fun status(ctx: Context): Notification {
        val core = OitApp.instance.core
        val t = core.timer
        val timer = when (t.state) {
            // 黑雾 (MECHANICS 18.3): 常驻通知里也看不到专注时长
            TState.RUNNING -> tr("专注中…") + "  " + (if (core.contract.fog()) "???" else f2(t.elapsed / 60)) + " min"
            TState.ON_BREAK -> tr("休息中 ☕") + "  " + fmtMs(t.countdown)
            TState.IDLE -> tr("准备就绪")
        }
        val n = core.net
        val net = when (n.mode) {
            "dev" -> "DEV"
            "online" -> tr("联网中") + "  " + fmtMs(n.grantLeft)
            "pending" -> tr("冷静期中 —— 仍可取消") + "  " + fmtMs((n.pending!!.at - System.currentTimeMillis()) / 1000.0)
            else -> tr("离线") + " · " + tr("今日剩余") + " " + fmtHm(n.remaining)
        }
        return builder(ctx, CH_STATUS).setContentTitle(timer).setContentText(net).setOngoing(true)
            .setOnlyAlertOnce(true).setShowWhen(false).setContentIntent(openIntent(ctx, null, 0)).build()
    }

    fun updateStatus(ctx: Context) {
        ctx.getSystemService(NotificationManager::class.java).notify(ID_STATUS, status(ctx))
    }

    /** 合约录像的前台服务通知 (MECHANICS 18.8): 系统要求录屏 / 摄像头时有一个常驻通知. */
    fun recording(ctx: Context, cam: Boolean, screen: Boolean): Notification {
        val what = listOfNotNull(if (cam) tr("摄像头") else null, if (screen) tr("屏幕") else null)
            .joinToString(" + ").ifEmpty { tr("录像") }
        return builder(ctx, CH_STATUS).setContentTitle(tr("合约录像中: {what}", "what" to what))
            .setContentText(tr("录像经过加密, 只能在 OIT 里查看 (设置 → 录像)."))
            .setOngoing(true).setOnlyAlertOnce(true).setShowWhen(false)
            .setContentIntent(openIntent(ctx, "settings", 5)).build()
    }

    fun event(ctx: Context, text: String) {
        val first = text.lineSequence().first()
        ctx.getSystemService(NotificationManager::class.java).notify(ID_EVENT,
            builder(ctx, CH_EVENTS).setContentTitle(first).setStyle(Notification.BigTextStyle().bigText(text))
                .setAutoCancel(true).setContentIntent(openIntent(ctx, null, 1)).build())
    }

    /** silent: 绑定了其他设备且本机不是最后有人操作的那台 —— 不出声 (MECHANICS 15.2). */
    fun hourPrompt(ctx: Context, label: String, silent: Boolean = false) {
        ctx.getSystemService(NotificationManager::class.java).notify(ID_HOUR,
            builder(ctx, if (silent) CH_STATUS else CH_EVENTS).setContentTitle(tr("📝  整点记录")).setContentText(label)
                .setAutoCancel(true).setContentIntent(openIntent(ctx, "log", 2)).build())
    }

    fun cancelHour(ctx: Context) = ctx.getSystemService(NotificationManager::class.java).cancel(ID_HOUR)

    fun vpnPermission(ctx: Context) {
        ctx.getSystemService(NotificationManager::class.java).notify(ID_VPN,
            builder(ctx, CH_ALERTS).setContentTitle(tr("断网功能已停止")).setContentText(tr("打开 OIT 重新授予 VPN 权限."))
                .setAutoCancel(true).setContentIntent(openIntent(ctx, null, 3)).build())
    }

    fun cancelVpn(ctx: Context) = ctx.getSystemService(NotificationManager::class.java).cancel(ID_VPN)

    /** 只能手动关闭的提醒: 每个对应一条通知, 按钮和划掉通知 (= ✕) 都会关掉它. */
    fun syncNotices(ctx: Context, list: List<Notice>) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        val ids = list.map { NOTICE_BASE + it.id }.toSet()
        for (old in posted.toList()) if (old !in ids) { nm.cancel(old); posted.remove(old) }
        for (n in list) {
            val nid = NOTICE_BASE + n.id
            if (nid in posted) continue
            val b = builder(ctx, CH_ALERTS).setContentTitle(n.title)
                .setContentText(n.message.lineSequence().first())
                .setStyle(Notification.BigTextStyle().bigText(n.message))
                .setContentIntent(openIntent(ctx, n.page, nid))
                .setDeleteIntent(noticeAction(ctx, n.id, null, nid * 8))
            n.buttons.forEachIndexed { i, btn ->
                b.addAction(Notification.Action.Builder(null, btn.label, noticeAction(ctx, n.id, btn.action, nid * 8 + 1 + i)).build())
            }
            nm.notify(nid, b.build())
            posted.add(nid)
        }
    }

    private fun noticeAction(ctx: Context, id: Int, action: String?, code: Int): PendingIntent {
        val i = Intent(ctx, NoticeReceiver::class.java).putExtra("id", id).putExtra("action", action)
        return PendingIntent.getBroadcast(ctx, code, i, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }
}

class NoticeReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        val id = intent.getIntExtra("id", -1)
        val action = intent.getStringExtra("action")
        OitApp.instance.core.closeNotice(id, action)
        if (action == "start" || action == "end" || action == "settings") {
            ctx.startActivity(Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                .putExtra("page", if (action == "settings") null else "timer"))
        }
    }
}

// ════════════════ 常驻服务: 每秒推进一次 Core ════════════════

class CoreService : Service() {
    private val handler = Handler(Looper.getMainLooper())
    private var n = 0
    private var lastRun = 0L
    private val loop = object : Runnable {
        override fun run() {
            val app = OitApp.instance
            // 唤醒锁拿着却隔了很久才轮到这里: 这段时间 OIT 被系统冻结了 (见 Background.noteGap)
            val now = SystemClock.elapsedRealtime()
            if (lastRun > 0) Background.noteGap(now - lastRun, wake != null)
            lastRun = now
            app.core.tick()
            runCatching { app.recordManager.tick() }      // 合约录像的开始 / 停止 (MECHANICS 18.8)
            if (++n % 5 == 0) {
                Notifier.updateStatus(this@CoreService)
                holdLocks(app.core.hasPeers())
                // 计划变了才动 VPN (例如开发者模式下刚绑定了设备: 要开一个全部放行的 VPN, 见 Core.vpnPlan)
                app.applyNet(false)
                app.maybeRelay()
                // 联动程序 (15.8): 保持它在运行 (不绑定设备也一样); 外出的计划结束时刻告诉它 (外出期间的设备不算离线)
                runCatching {
                    val dir = java.io.File(filesDir, "data")
                    com.obliphur.oit.sync.LinkFiles.writeHold(dir, app.core.linkHoldMs())
                    Waker.keepalive(this@CoreService, true)
                    if (n % 30 == 0) LinkService.start(this@CoreService)
                }
            }
            handler.postDelayed(this, 1000)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        foreground()
        handler.post(loop)
    }

    private fun foreground() {
        val note = Notifier.status(this)
        try {
            if (Build.VERSION.SDK_INT >= 34) startForeground(Notifier.ID_STATUS, note, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
            else startForeground(Notifier.ID_STATUS, note)
        } catch (_: Exception) {
            // 被系统重新拉起时不允许进入前台 (电池设置为"受限"): 照样运行, 下次打开界面时再进入前台
        }
    }

    /** 每次 startForegroundService 之后都要 startForeground (服务已经在运行时也一样), 否则系统会结束 OIT. */
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        foreground()
        return START_STICKY
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        OitApp.instance.core.saveAll()
        super.onTaskRemoved(rootIntent)
    }

    private var wake: PowerManager.WakeLock? = null
    private var wifi: android.net.wifi.WifiManager.WifiLock? = null

    /** 绑定了其他设备时: 熄屏后 CPU 和 Wi-Fi 也不睡下去, 不然手机上的 OIT 被暂停, 别的设备连进来只会超时 (MECHANICS 15.3). */
    @Suppress("DEPRECATION")
    private fun holdLocks(want: Boolean) {
        if (want == (wake != null)) return
        if (!want) {
            runCatching { wake?.release() }
            runCatching { wifi?.release() }
            wake = null; wifi = null
            return
        }
        wake = runCatching {
            getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "oit:sync")
                .apply { setReferenceCounted(false); acquire() }
        }.getOrNull()
        wifi = runCatching {
            applicationContext.getSystemService(android.net.wifi.WifiManager::class.java)
                .createWifiLock(android.net.wifi.WifiManager.WIFI_MODE_FULL_HIGH_PERF, "oit:sync")
                .apply { setReferenceCounted(false); acquire() }
        }.getOrNull()
    }

    override fun onDestroy() {
        holdLocks(false)
        handler.removeCallbacks(loop)
        OitApp.instance.core.saveAll()
        super.onDestroy()
    }

    companion object {
        fun start(ctx: Context) {
            try {
                ctx.startForegroundService(Intent(ctx, CoreService::class.java))
            } catch (_: Exception) {
                // 后台不允许启动前台服务时 (例如被闹钟叫醒), 等下次打开界面再启动
            }
        }
    }
}

/** 设备睡眠时用闹钟按时叫醒: 联网时间到, 休息结束, 整点, 还在吗 …… */
class AlarmReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        OitApp.instance.core.tick()
        CoreService.start(ctx)
        if (OitApp.instance.core.hasPeers()) {          // 保活闹钟把被划掉的 OIT 叫起来了 (15.8)
            LinkService.start(ctx)
            Waker.keepalive(ctx, true)
        }
    }
}

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        // 开机自启动成功了 (MECHANICS 14.8): 记下这是第几次开机, 关机的前提 (15.4)
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) OitApp.instance.noteBootStart()
        CoreService.start(ctx)
        LinkService.start(ctx)
        OitApp.instance.applyNet(true)
    }
}

object Waker {
    private var target = 0L

    /** 联动 (MECHANICS 15.8): 每 45 秒一个"保活"闹钟 (比"拒绝连接算离线"的 25 秒长: 被划掉的这段先算离线, 再被叫起来) (want = false 取消). 手机把 OIT 从最近任务里划掉 (vivo 等会杀掉 OIT 的所有进程,
     *  联动程序也一起没了) 之后闹钟还在, 它把 OIT 和联动程序叫起来; OIT 在运行时 CoreService 每 5 秒把它往后推, 平时不会响. */
    fun keepalive(ctx: Context, want: Boolean) {
        val am = ctx.getSystemService(AlarmManager::class.java)
        val pi = PendingIntent.getBroadcast(ctx, 1, Intent(ctx, AlarmReceiver::class.java).setAction("oit.keepalive"),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        if (!want) { am.cancel(pi); return }
        val at = SystemClock.elapsedRealtime() + 45_000L
        val exact = Build.VERSION.SDK_INT < 31 || am.canScheduleExactAlarms()
        try {
            if (exact) am.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi)
            else am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi)
        } catch (_: SecurityException) {
            am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi)
        }
    }

    fun schedule(ctx: Context, seconds: Double) {
        val at = SystemClock.elapsedRealtime() + (seconds * 1000).toLong()
        if (kotlin.math.abs(at - target) < 2000) return
        target = at
        val am = ctx.getSystemService(AlarmManager::class.java)
        val pi = PendingIntent.getBroadcast(ctx, 0, Intent(ctx, AlarmReceiver::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val exact = Build.VERSION.SDK_INT < 31 || am.canScheduleExactAlarms()
        try {
            if (exact) am.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi)
            else am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi)
        } catch (_: SecurityException) {
            am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi)
        }
    }
}

// ════════════════ 整点提示音 ════════════════

object Chime {
    private val handler = Handler(Looper.getMainLooper())

    /** 内置三音提示音: 784 / 988 / 1319 Hz. */
    private fun pcm(): ShortArray {
        val rate = 44100
        val out = ArrayList<Short>()
        for ((freq, ms) in listOf(784 to 200, 988 to 200, 1319 to 450)) {
            val n = rate * ms / 1000
            for (i in 0 until n) {
                val fade = minOf(1.0, i / (rate * 0.015), (n - 1 - i) / (rate * 0.015)).coerceAtLeast(0.0)
                out.add((11000 * fade * sin(2 * PI * freq * i / rate)).toInt().toShort())
            }
            repeat((rate * 0.04).toInt()) { out.add(0) }
        }
        return out.toShortArray()
    }

    private fun builtin(fin: () -> Unit) = playPcm(pcm(), fin)

    /** 沟通收到主动消息的提示音: 两个轻轻的钟声 (慢起音, 渐弱, 带一点泛音; 音量比整点提示音小得多). 和桌面版 config.ensure_soft_chime 是同一个音. */
    private fun softPcm(): ShortArray {
        val rate = 44100
        val total = (rate * 1.6).toInt()
        val buf = DoubleArray(total)
        for ((t0, freq, dur) in listOf(Triple(0.0, 659.25, 0.9), Triple(0.30, 880.0, 1.25))) {
            val start = (rate * t0).toInt()
            for (i in 0 until (rate * dur).toInt()) {
                val t = i.toDouble() / rate
                val env = minOf(1.0, t / 0.03) * exp(-t * 4.2)
                val v = sin(2 * PI * freq * t) + 0.18 * sin(4 * PI * freq * t) * exp(-t * 3.0)
                if (start + i < total) buf[start + i] += v * env
            }
        }
        val peak = buf.maxOf { abs(it) }.takeIf { it > 0.0 } ?: 1.0
        val gain = 6500.0 / peak
        val fade = (rate * 0.04).toInt()
        return ShortArray(total) { i -> (buf[i] * gain * minOf(1.0, (total - 1 - i).toDouble() / fade)).toInt().toShort() }
    }

    private val softData by lazy { softPcm() }

    fun playSoft() = playPcm(softData) {}

    private fun playPcm(data: ShortArray, fin: () -> Unit) {
        try {
            val track = AudioTrack.Builder()
                .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION_EVENT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
                .setAudioFormat(AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(44100).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
                .setBufferSizeInBytes(data.size * 2).setTransferMode(AudioTrack.MODE_STATIC).build()
            track.write(data, 0, data.size)
            track.play()
            val ms = data.size * 1000L / 44100
            handler.postDelayed({ runCatching { track.release() }; fin() }, ms + 100)
        } catch (e: Exception) {
            fin()
        }
    }

    /** uri 为空用内置提示音; 自选文件放不出来时退回内置. 最多等 10 秒就调用 done. */
    fun play(ctx: Context, uri: String, done: () -> Unit) {
        var finished = false
        val fin = { if (!finished) { finished = true; done() } }
        handler.postDelayed({ fin() }, 10_000)
        if (uri.isBlank()) { builtin(fin); return }
        try {
            val mp = MediaPlayer()
            mp.setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION_EVENT).build())
            mp.setDataSource(ctx, Uri.parse(uri))
            mp.setOnCompletionListener { it.release(); fin() }
            mp.setOnErrorListener { p, _, _ -> p.release(); builtin(fin); true }
            mp.setOnPreparedListener { it.start() }
            mp.prepareAsync()
        } catch (e: Exception) {
            builtin(fin)
        }
    }
}

// ════════════════ 前台应用 (应用使用时间统计) ════════════════

object AppUsage {
    private var lastQuery = 0L
    private var lastPkg: String? = null
    private var home: Set<String>? = null

    fun permitted(ctx: Context): Boolean {
        val ops = ctx.getSystemService(AppOpsManager::class.java)
        return ops.unsafeCheckOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), ctx.packageName) ==
            AppOpsManager.MODE_ALLOWED
    }

    private fun homes(ctx: Context): Set<String> = home ?: run {
        val i = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
        val s = ctx.packageManager.queryIntentActivities(i, 0).map { it.activityInfo.packageName }.toSet() +
            setOf("com.android.systemui", "android")
        home = s
        s
    }

    /** 包名; "" 是空白 (桌面 / 系统界面 / 锁屏); null 是不计入 (屏幕关闭, 没有权限). */
    fun current(ctx: Context): String? {
        if (!ctx.getSystemService(PowerManager::class.java).isInteractive) return null
        if (ctx.getSystemService(KeyguardManager::class.java).isKeyguardLocked) return ""
        if (!permitted(ctx)) return null
        val usm = ctx.getSystemService(UsageStatsManager::class.java)
        val now = System.currentTimeMillis()
        val from = if (lastQuery == 0L) now - 3600_000 else lastQuery - 2000
        val ev = usm.queryEvents(from, now)
        val e = UsageEvents.Event()
        while (ev.hasNextEvent()) {
            ev.getNextEvent(e)
            if (e.eventType == UsageEvents.Event.ACTIVITY_RESUMED) lastPkg = e.packageName
        }
        lastQuery = now
        val p = lastPkg ?: return ""
        if (p == ctx.packageName) return "OIT"
        if (p in homes(ctx)) return ""
        return p
    }
}
