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
import com.obliphur.oit.core.Notice
import com.obliphur.oit.core.TState
import com.obliphur.oit.core.f2
import com.obliphur.oit.core.fmtHm
import com.obliphur.oit.core.fmtMs
import com.obliphur.oit.core.tr
import com.obliphur.oit.ui.MainActivity
import kotlin.math.PI
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
            TState.RUNNING -> tr("专注中…") + "  " + f2(t.elapsed / 60) + " min"
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
    private val loop = object : Runnable {
        override fun run() {
            val app = OitApp.instance
            app.core.tick()
            if (++n % 5 == 0) {
                Notifier.updateStatus(this@CoreService)
                holdLocks(app.core.hasPeers())
                app.maybeRelay()
            }
            handler.postDelayed(this, 1000)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val note = Notifier.status(this)
        if (Build.VERSION.SDK_INT >= 34) startForeground(Notifier.ID_STATUS, note, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        else startForeground(Notifier.ID_STATUS, note)
        handler.post(loop)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

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
    }
}

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        CoreService.start(ctx)
        OitApp.instance.applyNet(true)
    }
}

object Waker {
    private var target = 0L

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

    private fun builtin(fin: () -> Unit) {
        try {
            val data = pcm()
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
