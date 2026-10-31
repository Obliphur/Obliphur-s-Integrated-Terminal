package com.obliphur.oit.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import com.obliphur.oit.R
import com.obliphur.oit.sync.LinkCore
import java.io.File

/**
 * 联动程序 (MECHANICS 15.8): 单独的进程 (:link), OIT 主体被关掉 / 被系统结束 / 崩溃后它还在, 替别的设备把本机的 OIT 重新启动.
 * 这个进程里没有 OitApp / Core (OitApp.onCreate 看到是 :link 进程就什么都不做); 只用 sync/Link.kt 的 LinkCore.
 * 绑定了其他设备时由 CoreService 保持它在运行 (没有就关掉), 开机 / 更新后由 BootReceiver 打开.
 */
class LinkService : Service() {
    private var link: LinkCore? = null
    @Volatile private var dead = false
    private var wake: PowerManager.WakeLock? = null
    private var wifi: android.net.wifi.WifiManager.WifiLock? = null
    private val dir get() = File(filesDir, "data")

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        foreground()
        startLink()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        foreground()
        startLink()
        return START_STICKY
    }

    private fun foreground() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH, "OIT link", NotificationManager.IMPORTANCE_MIN))
        val note = Notification.Builder(this, CH).setSmallIcon(R.drawable.ic_stat).setContentTitle("OIT link")
            .setContentText("Restarts OIT for other devices when it is closed").setOngoing(true).build()
        try {
            if (Build.VERSION.SDK_INT >= 34) startForeground(ID, note, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
            else startForeground(ID, note)
        } catch (_: Exception) {
            // 不允许进入前台 (电池设置为"受限"): 照样运行
        }
    }

    private fun log(text: String) {
        try {
            val f = File(dir, "link.log")
            dir.mkdirs()
            if (f.exists() && f.length() > 200_000) f.renameTo(File(dir, "link.log.old"))
            f.appendText(java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss ", java.util.Locale.US).format(java.util.Date()) + text + "\n")
        } catch (_: Exception) {
        }
    }

    /** 启动 OIT 主体: 起它的常驻服务 (主进程跟着起来, OitApp.onCreate 开始设备同步). 本进程有前台服务, 允许在后台启动前台服务. */
    private fun launchOit(): Boolean {
        val i = Intent(this, CoreService::class.java)
        return try {
            startForegroundService(i); true
        } catch (_: Exception) {
            try { startService(i); true } catch (_: Exception) { false }
        }
    }

    /** OIT 主进程 (不是 :link) 在不在运行: 在就不再去启动它. */
    private fun mainRunning(): Boolean = try {
        getSystemService(android.app.ActivityManager::class.java).runningAppProcesses?.any { it.processName == packageName } == true
    } catch (_: Exception) { false }

    @Synchronized private fun startLink() {
        if (link != null) return
        val l = LinkCore(dir, launch = { launchOit() }, otherAlive = { mainRunning() }, log = { log(it) })
        if (!l.serve()) { l.stop(); return }                 // 端口被占 = 已经有一个在运行
        link = l
        log("link started")
        Thread({
            while (!dead) {
                try {
                    l.probeOnce()
                    holdLocks(l.hasPeers())
                } catch (e: Exception) {
                    log("probe error ${e.javaClass.simpleName}: ${e.message}")
                }
                try { Thread.sleep(LinkCore.PROBE_EVERY_MS) } catch (_: InterruptedException) { }
            }
        }, "oit-link-probe").apply { isDaemon = true }.start()
    }

    /** 绑定了其他设备时: 熄屏后 CPU 和 Wi-Fi 也不睡下去, 别的设备的联动程序随时能连进来 (同 CoreService.holdLocks). */
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
            getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "oit:link")
                .apply { setReferenceCounted(false); acquire() }
        }.getOrNull()
        wifi = runCatching {
            applicationContext.getSystemService(android.net.wifi.WifiManager::class.java)
                .createWifiLock(android.net.wifi.WifiManager.WIFI_MODE_FULL_HIGH_PERF, "oit:link")
                .apply { setReferenceCounted(false); acquire() }
        }.getOrNull()
    }

    override fun onDestroy() {
        dead = true
        holdLocks(false)
        link?.stop()
        link = null
        super.onDestroy()
    }

    companion object {
        private const val CH = "oit_link"
        private const val ID = 4041

        /** 打开 (已经在运行时只是重新进入前台). */
        fun start(ctx: Context) {
            try {
                ctx.startForegroundService(Intent(ctx, LinkService::class.java))
            } catch (_: Exception) {
                // 后台不允许启动前台服务时, 等下次打开界面 / 开机再启动
            }
        }

        fun stop(ctx: Context) {
            runCatching { ctx.stopService(Intent(ctx, LinkService::class.java)) }
        }
    }
}
