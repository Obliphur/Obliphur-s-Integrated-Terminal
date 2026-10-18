package com.obliphur.oit.service

import android.app.ActivityManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings

/**
 * 后台运行 (MECHANICS 15.3): OIT 在后台 / 熄屏时系统不暂停它, 绑定的设备才能继续同步.
 * Android 自带的三项限制可以查到; 手机厂商自己的 "自启动 / 后台运行" 开关查不到, 只能打开那一页让用户自己开.
 */
object Background {

    /** 不受电池优化限制: 熄屏 (Doze) 后照样能联网, 唤醒锁也有效, 被系统停掉后能从后台重新启动前台服务. */
    fun batteryIgnored(ctx: Context): Boolean =
        runCatching { ctx.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(ctx.packageName) }
            .getOrDefault(true)

    /** 电池设置被设成了"受限": 后台什么都做不了. */
    fun restricted(ctx: Context): Boolean = Build.VERSION.SDK_INT >= 28 &&
        runCatching { ctx.getSystemService(ActivityManager::class.java).isBackgroundRestricted }.getOrDefault(false)

    /** 流量节省程序 / 限制后台数据: 移动数据 (以及被标成计费的 Wi-Fi) 上后台不能联网. */
    fun dataRestricted(ctx: Context): Boolean = runCatching {
        ctx.getSystemService(ConnectivityManager::class.java).restrictBackgroundStatus ==
            ConnectivityManager.RESTRICT_BACKGROUND_STATUS_ENABLED
    }.getOrDefault(false)

    /** Android 自带的限制都解除了. */
    fun ok(ctx: Context) = batteryIgnored(ctx) && !restricted(ctx) && !dataRestricted(ctx)

    fun requestBattery(ctx: Context) = open(ctx,
        Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${ctx.packageName}")))

    fun appDetails(ctx: Context) = open(ctx,
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${ctx.packageName}")))

    fun dataSaver(ctx: Context) = open(ctx,
        Intent(Settings.ACTION_IGNORE_BACKGROUND_DATA_RESTRICTIONS_SETTINGS, Uri.parse("package:${ctx.packageName}")))
        || appDetails(ctx)

    /** 各家手机的 自启动 设置页 (按顺序试, 第一个存在的就用). */
    private val AUTOSTART = listOf(
        // 小米 / 红米 (MIUI, HyperOS)
        "com.miui.securitycenter" to "com.miui.permcenter.autostart.AutoStartManagementActivity",
        // 华为 / 荣耀
        "com.huawei.systemmanager" to "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity",
        "com.hihonor.systemmanager" to "com.hihonor.systemmanager.startupmgr.ui.StartupNormalAppListActivity",
        // OPPO / 一加 / realme (ColorOS)
        "com.coloros.safecenter" to "com.coloros.safecenter.permission.startup.StartupAppListActivity",
        "com.coloros.safecenter" to "com.coloros.safecenter.startupapp.StartupAppListActivity",
        "com.oplus.safecenter" to "com.oplus.safecenter.permission.startup.StartupAppListActivity",
        "com.oppo.safe" to "com.oppo.safe.permission.startup.StartupAppListActivity",
        // vivo / iQOO
        "com.vivo.permissionmanager" to "com.vivo.permissionmanager.activity.BgStartUpManagerActivity",
        "com.iqoo.secure" to "com.iqoo.secure.ui.phoneoptimize.BgStartUpManager",
        "com.iqoo.secure" to "com.iqoo.secure.ui.phoneoptimize.AddWhiteListActivity",
        // 魅族
        "com.meizu.safe" to "com.meizu.safe.permission.SmartBGActivity",
        // 华硕
        "com.asus.mobilemanager" to "com.asus.mobilemanager.autostart.AutoStartActivity",
    )

    /** 各家手机自己的 后台耗电 设置页: 这里不允许时, 前台服务也挡不住系统在后台冻结 OIT
     *  (vivo 的"允许后台高耗电", 小米的"省电策略 → 无限制", 华为的"应用启动管理", OPPO 的"允许后台运行"). */
    private val POWER = listOf(
        // vivo / iQOO (OriginOS, Funtouch)
        "com.vivo.abe" to "com.vivo.applicationbehaviorengine.ui.ExcessivePowerManagerActivity",
        "com.vivo.abeui" to "com.vivo.abeui.highpower.ExcessivePowerManagerActivity",
        "com.iqoo.powersaving" to "com.iqoo.powersaving.PowerSavingManagerActivity",
        // 小米 / 红米
        "com.miui.powerkeeper" to "com.miui.powerkeeper.ui.HiddenAppsConfigActivity",
        // 华为
        "com.huawei.systemmanager" to "com.huawei.systemmanager.optimize.process.ProtectActivity",
        // OPPO / 一加 / realme
        "com.coloros.oppoguardelf" to "com.coloros.powermanager.fuelgaue.PowerUsageModelActivity",
        "com.oplus.battery" to "com.oplus.powermanager.fuelgaue.PowerUsageModelActivity",
        // 三星
        "com.samsung.android.lool" to "com.samsung.android.sm.ui.battery.BatteryActivity",
    )

    private fun find(ctx: Context, list: List<Pair<String, String>>): Intent? = list.asSequence().map { (pkg, cls) ->
        Intent().setComponent(ComponentName(pkg, cls)).putExtra("package_name", ctx.packageName)
            .putExtra("package_label", "OIT")
    }.firstOrNull { i ->
        runCatching { i.resolveActivityInfo(ctx.packageManager, 0)?.exported == true }.getOrDefault(false)
    }

    /** 这台手机有厂商自己的 自启动 / 后台耗电 设置页. */
    fun hasOemPage(ctx: Context) = hasAutostart(ctx) || hasPowerPage(ctx)
    fun hasAutostart(ctx: Context) = find(ctx, AUTOSTART) != null
    fun hasPowerPage(ctx: Context) = find(ctx, POWER) != null

    /** 打开厂商的 后台耗电 设置页 (最要紧), 没有就打开 自启动; 都没有 (或打不开) 就打开 OIT 的应用信息页 (电池 → 无限制). */
    fun openOem(ctx: Context): Boolean = find(ctx, POWER)?.let { open(ctx, it) } == true ||
        find(ctx, AUTOSTART)?.let { open(ctx, it) } == true || appDetails(ctx)
    fun openPower(ctx: Context): Boolean = find(ctx, POWER)?.let { open(ctx, it) } == true || appDetails(ctx)
    fun openAutostart(ctx: Context): Boolean = find(ctx, AUTOSTART)?.let { open(ctx, it) } == true || appDetails(ctx)

    // ---- 被系统暂停过吗 ----

    /** 本次运行里 OIT 被系统暂停 (冻结 / 深度睡眠) 的次数, 最近一次在什么时候, 停了多久. 只在内存里. */
    @Volatile var pauses = 0
        private set
    @Volatile var lastPauseAt: java.time.LocalDateTime? = null
        private set
    @Volatile var lastPauseSec = 0L
        private set

    /** 常驻服务每秒推进一次; 绑定了其他设备 (唤醒锁拿着) 时两次之间隔了 PAUSE_SEC 秒以上 = 这段时间 OIT 被系统停住了,
     *  别的设备连过来能连上却等不到回复. */
    fun noteGap(gapMs: Long, holdingLocks: Boolean) {
        if (!holdingLocks || gapMs < PAUSE_SEC * 1000) return
        pauses++
        lastPauseSec = gapMs / 1000
        lastPauseAt = java.time.LocalDateTime.now().minusSeconds(lastPauseSec)
    }

    /** 最近 6 小时里被暂停过. */
    fun pausedRecently(): Boolean =
        lastPauseAt?.isAfter(java.time.LocalDateTime.now().minusHours(6)) == true

    const val PAUSE_SEC = 30L

    private fun open(ctx: Context, i: Intent): Boolean =
        runCatching { ctx.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }.isSuccess
}
