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

    /** 各家手机的 自启动 / 后台运行 设置页 (按顺序试, 第一个存在的就用). */
    private val OEM = listOf(
        // 小米 / 红米 (MIUI, HyperOS)
        "com.miui.securitycenter" to "com.miui.permcenter.autostart.AutoStartManagementActivity",
        "com.miui.powerkeeper" to "com.miui.powerkeeper.ui.HiddenAppsConfigActivity",
        // 华为 / 荣耀
        "com.huawei.systemmanager" to "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity",
        "com.huawei.systemmanager" to "com.huawei.systemmanager.optimize.process.ProtectActivity",
        "com.hihonor.systemmanager" to "com.hihonor.systemmanager.startupmgr.ui.StartupNormalAppListActivity",
        // OPPO / 一加 / realme (ColorOS)
        "com.coloros.safecenter" to "com.coloros.safecenter.permission.startup.StartupAppListActivity",
        "com.coloros.safecenter" to "com.coloros.safecenter.startupapp.StartupAppListActivity",
        "com.oplus.safecenter" to "com.oplus.safecenter.permission.startup.StartupAppListActivity",
        "com.oppo.safe" to "com.oppo.safe.permission.startup.StartupAppListActivity",
        // vivo / iQOO
        "com.vivo.permissionmanager" to "com.vivo.permissionmanager.activity.BgStartUpManagerActivity",
        "com.iqoo.secure" to "com.iqoo.secure.ui.phoneoptimize.BgStartUpManager",
        // 魅族
        "com.meizu.safe" to "com.meizu.safe.permission.SmartBGActivity",
        // 三星
        "com.samsung.android.lool" to "com.samsung.android.sm.ui.battery.BatteryActivity",
        // 华硕
        "com.asus.mobilemanager" to "com.asus.mobilemanager.autostart.AutoStartActivity",
    )

    /** 这台手机有厂商自己的后台设置页. */
    fun hasOemPage(ctx: Context) = oemIntent(ctx) != null

    private fun oemIntent(ctx: Context): Intent? = OEM.asSequence().map { (pkg, cls) ->
        Intent().setComponent(ComponentName(pkg, cls)).putExtra("package_name", ctx.packageName)
            .putExtra("package_label", "OIT")
    }.firstOrNull { i ->
        runCatching { i.resolveActivityInfo(ctx.packageManager, 0)?.exported == true }.getOrDefault(false)
    }

    /** 打开厂商的 自启动 / 后台运行 设置页; 没有就打开 OIT 的应用信息页 (电池 → 无限制 / 允许后台活动). */
    fun openOem(ctx: Context): Boolean = oemIntent(ctx)?.let { open(ctx, it) } == true || appDetails(ctx)

    private fun open(ctx: Context, i: Intent): Boolean =
        runCatching { ctx.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }.isSuccess
}
