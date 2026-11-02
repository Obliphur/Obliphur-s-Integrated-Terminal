package com.obliphur.oit.service

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** 读最近任务列表 (MECHANICS 14.6): 只看 "Visible recent tasks" 一段, 每个任务取根界面的包名. */
class RecentsTest {
    private val dump = """
ACTIVITY MANAGER RECENT TASKS (dumpsys activity recents)
  Recent tasks:
  * Recent #2: ActivityStack{a6b7e2d #1487 visible=true type=standard A=10127:com.vivo.globalsearch U=0 sz=0}
    realActivity={com.vivo.globalsearch/.SearchActivity}

  Visible recent tasks (most recent first):
  * RecentTaskInfo #0: 
    id=1489 stackId=1489 userId=0 hasTask=true lastActiveTime=150836645
    baseIntent=Intent { act=android.intent.action.MAIN cat=[android.intent.category.LAUNCHER] flg=0x30200000 cmp=com.obliphur.oit/.ui.MainActivity }
    baseActivity={com.obliphur.oit/com.obliphur.oit.ui.MainActivity}
    realActivity={com.obliphur.oit/com.obliphur.oit.ui.MainActivity}
  * RecentTaskInfo #1: 
    baseIntent=Intent { act=android.intent.action.MAIN cmp=com.github.metacubex.clash.meta/com.github.kr328.clash.MainActivityAlias }
    baseActivity={com.github.metacubex.clash.meta/com.github.kr328.clash.MainActivityAlias}
    topActivity={com.android.permissioncontroller/.GrantActivity}
  * RecentTaskInfo #2: 
    baseIntent=Intent { act=android.intent.action.MAIN cmp=com.android.settings/.Settings }
""".trimIndent()

    @Test fun visibleTasksOnly() {
        assertEquals(setOf("com.obliphur.oit", "com.github.metacubex.clash.meta", "com.android.settings"),
            AppUsage.parseRecents(dump))
    }

    @Test fun noSection() {
        assertNull(AppUsage.parseRecents("Permission Denial: can't dump"))
    }

    /** 统计 (没有 DUMP 权限时): 最近 N 分钟内在前台待过至少 M 秒、界面没销毁、没被强制停止、桌面上有图标的应用. */
    @Test fun recentlyUsedApps() {
        val resumed = 1; val paused = 2; val stopped = 23; val destroyed = 24
        val now = 6000_000L                                        // 100 分钟
        fun ev(pkg: String, type: Int, atSec: Int, cls: String = "A") = UseEvent(pkg, cls, type, atSec * 1000L)
        val events = listOf(
            ev("tv.danmaku.bili", resumed, 600), ev("tv.danmaku.bili", paused, 720), ev("tv.danmaku.bili", stopped, 720),    // 早就不用了
            ev("com.tencent.mm", resumed, 5400), ev("com.tencent.mm", paused, 5520), ev("com.tencent.mm", stopped, 5520),     // 刚用过, 但被划掉 (强制停止)
            ev("com.obliphur.oit", resumed, 5300), ev("com.obliphur.oit", paused, 5500), ev("com.obliphur.oit", stopped, 5500), // 刚用过
            ev("com.example.done", resumed, 5400), ev("com.example.done", paused, 5460), ev("com.example.done", destroyed, 5460), // 销毁了
            ev("com.example.helper", resumed, 5500), ev("com.example.helper", paused, 5560),                                 // 桌面上没有图标
            ev("com.android.documentsui", resumed, 5700), ev("com.android.documentsui", paused, 5706),                      // 文件选择器: 只待了 6 秒
            ev("com.android.documentsui", stopped, 5707),
            ev("com.android.settings", resumed, 1200, "Settings"),                                                           // 一直在前台
            ev("com.android.settings", resumed, 5700, "Sub"), ev("com.bbk.launcher2", resumed, 5900),                        // 桌面
        )
        val launchable = setOf("tv.danmaku.bili", "com.tencent.mm", "com.android.settings", "com.obliphur.oit", "com.example.done",
            "com.android.documentsui", "com.bbk.launcher2")
        val home = setOf("com.bbk.launcher2")
        val gone = setOf("tv.danmaku.bili", "com.tencent.mm")
        // 最近 10 分钟, 至少 20 秒: 只剩开着的设置和刚用过的 OIT
        assertEquals(setOf("com.android.settings", "com.obliphur.oit"),
            AppUsage.openFromEvents(events, now, 600_000L, 20_000L, home, launchable) { it in gone })
        // 没有被强制停止的 (别的手机): 刚用过的微信也算
        assertEquals(setOf("com.tencent.mm", "com.android.settings", "com.obliphur.oit"),
            AppUsage.openFromEvents(events, now, 600_000L, 20_000L, home, launchable) { false })
        // 时间窗口放宽到 2 小时: 早就不用的 B 站也算; 前台时间不限: 文件选择器也算
        assertEquals(setOf("tv.danmaku.bili", "com.tencent.mm", "com.android.settings", "com.obliphur.oit"),
            AppUsage.openFromEvents(events, now, 7200_000L, 20_000L, home, launchable) { false })
        assertEquals(setOf("com.tencent.mm", "com.android.settings", "com.obliphur.oit", "com.android.documentsui"),
            AppUsage.openFromEvents(events, now, 600_000L, 0L, home, launchable) { false })
        // 前台时间只算窗口里的那段: 窗口 600 秒, OIT 的 200 秒前台时间只有最后 100 秒在窗口里
        assertEquals(setOf("com.android.settings", "com.obliphur.oit"),
            AppUsage.openFromEvents(events, now, 600_000L, 90_000L, home, launchable) { it in gone })
        assertEquals(setOf("com.android.settings"),
            AppUsage.openFromEvents(events, now, 600_000L, 110_000L, home, launchable) { it in gone })
    }
}
