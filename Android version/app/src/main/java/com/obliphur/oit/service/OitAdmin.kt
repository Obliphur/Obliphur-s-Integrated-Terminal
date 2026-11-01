package com.obliphur.oit.service

import android.app.admin.DeviceAdminReceiver
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import com.obliphur.oit.OitApp
import com.obliphur.oit.core.tr

/**
 * 卸载保护 (MECHANICS 9.2): Android 不会在应用被卸载时通知应用自己, 但已经激活的设备管理员必须先取消激活才能卸载.
 * 绑定了其他设备的 OIT 请用户激活 (设置 → 设备同步); 取消激活 = 马上要卸载了 (系统弹出的确认框里写着后果),
 * 这时 OIT 还在, 来得及请每台绑定的设备清空数据并解除绑定 (和 Reset 页的"卸载 OIT"一样), 本机的数据也一起清空.
 */
class OitAdmin : DeviceAdminReceiver() {
    override fun onDisableRequested(context: Context, intent: Intent): CharSequence =
        tr("取消激活后就可以卸载 OIT. 现在取消: 本机和所有绑定设备上的 OIT 数据都会被清空, 绑定也会解除.")

    override fun onDisabled(context: Context, intent: Intent) {
        val app = OitApp.instance
        if (app.sync?.peers().isNullOrEmpty()) return
        app.resetAll(uninstall = true)          // 在主线程上 (和 Reset 页一样): 最多等 3.5 秒, 让请求送出去
    }

    companion object {
        fun component(ctx: Context) = ComponentName(ctx, OitAdmin::class.java)

        fun active(ctx: Context): Boolean =
            ctx.getSystemService(DevicePolicyManager::class.java).isAdminActive(component(ctx))

        /** 打开系统的"激活设备管理员"界面. */
        fun requestIntent(ctx: Context): Intent = Intent(DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN)
            .putExtra(DevicePolicyManager.EXTRA_DEVICE_ADMIN, component(ctx))
            .putExtra(DevicePolicyManager.EXTRA_ADD_EXPLANATION,
                tr("激活后, 卸载 OIT 前必须先取消激活, 取消时 OIT 会请其他设备清空数据."))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }
}
