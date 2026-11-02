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

    /** 估计 (没有 DUMP 权限时): vivo 不记"销毁"事件, 划掉的应用 = 被强制停止, 不能再算. */
    @Test fun estimateSkipsStopped() {
        val resumed = 1; val paused = 2; val stopped = 23; val destroyed = 24
        val last = mapOf(
            "tv.danmaku.bili/tv.danmaku.bili.MainActivity" to stopped,          // 划掉了 (FLAG_STOPPED)
            "com.tencent.mm/com.tencent.mm.ui.LauncherUI" to paused,           // 划掉了
            "com.android.settings/com.android.settings.Settings" to resumed,   // 开着
            "com.android.settings/com.android.settings.SubSettings" to stopped,
            "com.obliphur.oit/com.obliphur.oit.ui.MainActivity" to stopped,    // 开着
            "com.example.done/com.example.done.A" to destroyed,                // 销毁了
            "com.example.helper/com.example.helper.B" to stopped,              // 桌面上没有图标的附属程序
            "com.bbk.launcher2/com.bbk.launcher2.Launcher" to resumed,         // 桌面
        )
        val launchable = setOf("tv.danmaku.bili", "com.tencent.mm", "com.android.settings", "com.obliphur.oit", "com.example.done", "com.bbk.launcher2")
        val gone = setOf("tv.danmaku.bili", "com.tencent.mm")
        assertEquals(setOf("com.android.settings", "com.obliphur.oit"),
            AppUsage.openFromEvents(last, setOf("com.bbk.launcher2"), launchable) { it in gone })
        // 没有被强制停止的 (别的手机): 全都算
        assertEquals(setOf("tv.danmaku.bili", "com.tencent.mm", "com.android.settings", "com.obliphur.oit"),
            AppUsage.openFromEvents(last, setOf("com.bbk.launcher2"), launchable) { false })
    }
}
