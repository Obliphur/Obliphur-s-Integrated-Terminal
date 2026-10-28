package com.obliphur.oit.service

import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.graphics.PixelFormat
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.telecom.TelecomManager
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import com.obliphur.oit.OitApp
import com.obliphur.oit.core.Toast
import com.obliphur.oit.core.tr

/**
 * 锁定应用 (MECHANICS 14.6, 对应桌面版 4.7 "关掉前台窗口").
 * Android 不允许普通应用关掉别的应用, 这里换成: 被锁定的应用一到前台, 就盖上一层"已锁定"挡住它, 并回到桌面.
 * 前台应用由使用情况访问权限认出 (AppUsage, Core 每秒查一次); 盖层和从后台回到桌面都需要"显示在其他应用上层".
 */
object AppLock {
    private val main = Handler(Looper.getMainLooper())
    private var view: View? = null
    private var title: TextView? = null
    /** 此刻被锁定的应用 (每次锁定只记一条拦截记录). */
    private var current: String? = null
    private val lastToast = HashMap<String, Long>()
    private var neverLock: Set<String>? = null

    fun canOverlay(ctx: Context) = Settings.canDrawOverlays(ctx)

    /** 永远不锁: 设置, 电话 / 紧急呼叫, 权限确认, 安装器. whitelist: 白名单模式下系统自带的应用也不锁
     *  (否则相机, 输入法设置之类全被锁掉); 黑名单里明确写了的系统应用照样锁. */
    fun exempt(ctx: Context, pkg: String, whitelist: Boolean): Boolean {
        if (pkg in never(ctx)) return true
        if (!whitelist) return false
        return try {
            val ai = ctx.packageManager.getApplicationInfo(pkg, 0)
            ai.flags and (ApplicationInfo.FLAG_SYSTEM or ApplicationInfo.FLAG_UPDATED_SYSTEM_APP) != 0
        } catch (e: Exception) {
            true                                    // 查不到的不锁 (同桌面版"查不到 exe 的窗口")
        }
    }

    private fun never(ctx: Context): Set<String> = neverLock ?: run {
        val pm = ctx.packageManager
        val s = mutableSetOf(ctx.packageName, "com.android.settings", "com.android.phone", "com.android.emergency",
            "com.android.permissioncontroller", "com.google.android.permissioncontroller",
            "com.android.packageinstaller", "com.google.android.packageinstaller", "com.android.systemui")
        runCatching {
            pm.queryIntentActivities(Intent(Settings.ACTION_SETTINGS), 0).forEach { s.add(it.activityInfo.packageName) }
        }
        runCatching { ctx.getSystemService(TelecomManager::class.java)?.defaultDialerPackage?.let { s.add(it) } }
        neverLock = s
        s
    }

    /** Core 每秒调用 (前台仍是被锁定的应用时会反复调用). */
    fun lock(ctx: Context, pkg: String, why: String) = main.post {
        val label = appLabel(ctx, pkg)
        if (current != pkg) {
            current = pkg
            BlockLog.add("🔒", pkg)
        }
        val text = if (why == "blacklist-app") tr("{app} 在黑名单里,已锁定.", "app" to label)
        else tr("{app} 不在白名单里,已锁定.", "app" to label)
        val now = SystemClock.elapsedRealtime()
        if (now - (lastToast[pkg] ?: -99_999L) >= 5_000) {          // 同一个应用 5 秒内只提示一次
            lastToast[pkg] = now
            val msg = if (canOverlay(ctx)) text else text + "\n" + tr("要真正挡住它, 需要允许 OIT \"显示在其他应用上层\".")
            OitApp.instance.toast(Toast(msg, "warn", !canOverlay(ctx)))
        }
        if (!canOverlay(ctx)) return@post
        show(ctx, text)
        goHome(ctx)
    }

    fun unlock() = main.post { current = null; hide() }

    private fun goHome(ctx: Context) {
        runCatching {
            ctx.startActivity(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
    }

    private fun show(ctx: Context, text: String) {
        view?.let { title?.text = text; return }
        val dp = { v: Float -> TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, ctx.resources.displayMetrics).toInt() }
        val t = TextView(ctx).apply {
            this.text = text
            setTextColor(0xFFFFFFFF.toInt()); textSize = 18f; gravity = Gravity.CENTER
        }
        val root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(0xF2111418.toInt())
            setPadding(dp(32f), dp(32f), dp(32f), dp(32f))
            addView(TextView(ctx).apply { this.text = com.obliphur.oit.core.I18n.ic("🔒"); textSize = 56f; gravity = Gravity.CENTER })
            addView(t)
            addView(Button(ctx).apply {
                this.text = tr("回到桌面")
                setOnClickListener { goHome(ctx); hide() }
            }, LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                .apply { topMargin = dp(24f) })
        }
        val lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            // 不抢按键 (返回 / 桌面键照常可用), 但挡住整个屏幕的触摸
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT)
        try {
            ctx.getSystemService(WindowManager::class.java).addView(root, lp)
            view = root
            title = t
        } catch (e: Exception) {
            view = null
        }
    }

    private fun hide() {
        val v = view ?: return
        view = null
        title = null
        runCatching { v.context.getSystemService(WindowManager::class.java).removeView(v) }
    }
}
