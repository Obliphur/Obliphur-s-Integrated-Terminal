package com.obliphur.oit.core

import org.junit.Assert.*
import org.junit.Test

class CommStreamTest {
    @Test fun codePointsAndNetworkGaps() {
        val display = CommStream({ if (it == "中") 0.2 else 0.1 })
        display.update("中a😀", 0)
        assertFalse(display.advance(199))
        assertEquals("", display.text)
        assertTrue(display.advance(200)); assertEquals("中", display.text)
        assertTrue(display.advance(300)); assertEquals("中a", display.text)
        assertFalse(display.advance(399))
        assertTrue(display.advance(400)); assertEquals("中a😀", display.text)
        display.update("中a😀b", 1000)
        display.advance(1000)
        assertEquals("中a😀", display.text)
        assertEquals(1100L, display.due)
    }

    @Test fun finalTextKeepsProgressAndHonorsFloor() {
        val display = CommStream({ 0.1 }, 1000)
        display.update("ab", 0)
        display.advance(1000); assertEquals("", display.text)
        display.advance(1100); assertEquals("a", display.text)
        display.update("ab", 1150)
        assertEquals(1200L, display.due)
        assertEquals("a", display.text)
        display.advance(1200); assertEquals("ab", display.text)
        val instant = CommStream({ 0.0 })
        instant.update("立即😀", 0); instant.advance(0)
        assertEquals("立即😀", instant.text)
    }
}
