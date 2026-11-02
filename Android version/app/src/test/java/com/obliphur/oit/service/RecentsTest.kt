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

    /** 统计 (没有 DUMP 权限时): 最近 N 分钟内用过、界面没销毁、没被强制停止、桌面上有图标的应用. */
    @Test fun recentlyUsedApps() {
        val resumed = 1; val paused = 2; val stopped = 23; val destroyed = 24
        val min = 60_000L
        val now = 100 * min
        fun ev(pkg: String, type: Int, atMin: Int, cls: String = "A") = UseEvent(pkg, cls, type, atMin * min)
        val events = listOf(
            ev("tv.danmaku.bili", resumed, 10), ev("tv.danmaku.bili", paused, 12), ev("tv.danmaku.bili", stopped, 12),   // 早就不用了
            ev("com.tencent.mm", resumed, 90), ev("com.tencent.mm", paused, 92), ev("com.tencent.mm", stopped, 92),      // 刚用过, 但被划掉 (强制停止)
            ev("com.obliphur.oit", resumed, 80), ev("com.obliphur.oit", paused, 85), ev("com.obliphur.oit", stopped, 85), // 刚用过
            ev("com.example.done", resumed, 90), ev("com.example.done", paused, 91), ev("com.example.done", destroyed, 91), // 销毁了
            ev("com.example.helper", resumed, 95), ev("com.example.helper", paused, 96),                               // 桌面上没有图标
            ev("com.android.settings", resumed, 20, "Settings"),                                                       // 一直在前台
            ev("com.android.settings", resumed, 95, "Sub"), ev("com.bbk.launcher2", resumed, 99),                      // 桌面
        )
        val launchable = setOf("tv.danmaku.bili", "com.tencent.mm", "com.android.settings", "com.obliphur.oit", "com.example.done", "com.bbk.launcher2")
        val gone = setOf("tv.danmaku.bili", "com.tencent.mm")
        assertEquals(setOf("com.android.settings", "com.obliphur.oit"),
            AppUsage.openFromEvents(events, now, 30 * min, setOf("com.bbk.launcher2"), launchable) { it in gone })
        // 没有被强制停止的 (别的手机): 刚用过的微信也算; 时间窗口放宽到 2 小时: 早就不用的 B 站也算
        assertEquals(setOf("com.tencent.mm", "com.android.settings", "com.obliphur.oit"),
            AppUsage.openFromEvents(events, now, 30 * min, setOf("com.bbk.launcher2"), launchable) { false })
        assertEquals(setOf("tv.danmaku.bili", "com.tencent.mm", "com.android.settings", "com.obliphur.oit"),
            AppUsage.openFromEvents(events, now, 120 * min, setOf("com.bbk.launcher2"), launchable) { false })
    }
}
