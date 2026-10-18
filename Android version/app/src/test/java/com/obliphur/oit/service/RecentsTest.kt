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
}
