package com.obliphur.oit.core

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files

class SidebarOrderTest {
    private fun tmpDir(): File = Files.createTempDirectory("oit_sidebar_test").toFile()

    @Test
    fun normalizeFillsMissingAndKeepsSingleDivider() {
        val def = SidebarOrder.normalize(null)
        assertEquals(SidebarOrder.DEFAULT_ORDER, def)

        // Missing divider & missing keys: divider appended, missing keys inserted before divider
        val partial = SidebarOrder.normalize(listOf("timer", "timer", "", "comm"))
        assertEquals(1, partial.count { it == SidebarOrder.DIVIDER })
        assertTrue(partial.indexOf("timer") < partial.indexOf(SidebarOrder.DIVIDER))
        assertTrue(partial.indexOf("comm") < partial.indexOf(SidebarOrder.DIVIDER))
        assertTrue(partial.indexOf("settings") < partial.indexOf(SidebarOrder.DIVIDER))
        assertTrue(partial.indexOf("reset") < partial.indexOf(SidebarOrder.DIVIDER))
        assertEquals(SidebarOrder.DEFAULT_ORDER.toSet(), partial.toSet())
    }

    @Test
    fun dimmingRuleFollowsDividerPosition() {
        val order = SidebarOrder.DEFAULT_ORDER
        assertFalse(SidebarOrder.isDimmed(order, "timer"))
        assertFalse(SidebarOrder.isDimmed(order, "settings"))
        assertFalse(SidebarOrder.isDimmed(order, SidebarOrder.DIVIDER))
        assertTrue(SidebarOrder.isDimmed(order, "comm"))
        assertTrue(SidebarOrder.isDimmed(order, "reset"))
    }

    @Test
    fun moveItemsAndDividerAcrossBoundary() {
        val start = SidebarOrder.DEFAULT_ORDER
        // Move "stats" below divider (to the end)
        val m1 = SidebarOrder.move(start, "stats", null)
        assertEquals("stats", m1.last())
        assertTrue(SidebarOrder.isDimmed(m1, "stats"))

        // Move "comm" above "timer"
        val m2 = SidebarOrder.move(m1, "comm", "timer")
        assertTrue(m2.indexOf("comm") < m2.indexOf("timer"))
        assertFalse(SidebarOrder.isDimmed(m2, "comm"))

        // Move divider itself above "contract"
        val m3 = SidebarOrder.move(m2, SidebarOrder.DIVIDER, "contract")
        assertTrue(SidebarOrder.isDimmed(m3, "contract"))
        assertTrue(SidebarOrder.isDimmed(m3, "settings"))
        assertFalse(SidebarOrder.isDimmed(m3, "timer"))
    }

    @Test
    fun loadAndSavePersistAndPreserveOtherPrefs() {
        val dir = tmpDir()
        try {
            val f = SidebarOrder.prefsFile(dir)
            Store.write(f, JSONObject().put("other_key", 42))

            val moved = SidebarOrder.move(SidebarOrder.DEFAULT_ORDER, "log", null)
            assertTrue(SidebarOrder.save(dir, moved))

            val reloaded = SidebarOrder.load(dir)
            assertEquals(moved, reloaded)
            assertTrue(SidebarOrder.isDimmed(reloaded, "log"))

            val raw = Store.readObj(f)!!
            assertEquals(42, raw.optInt("other_key"))
        } finally {
            dir.deleteRecursively()
        }
    }
}
