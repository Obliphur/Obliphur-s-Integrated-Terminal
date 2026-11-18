package com.obliphur.oit.core

import kotlin.math.ceil

/** 流式正文的显示时钟；按 Unicode 码点计时，表情不拆成半个代理项。 */
internal class CommStream(private val delay: (String) -> Double, private val earliest: Long = 0L) {
    var source = ""; private set
    private var shown = 0
    private val times = ArrayList<Double>()
    private val ends = ArrayList<Int>()
    val text: String get() = source.substring(0, shown)
    val due: Long get() = times.lastOrNull()?.let { ceil(it).toLong() } ?: earliest

    fun update(value: String, now: Long) {
        var common = 0
        while (common < minOf(source.length, value.length) && source[common] == value[common]) common++
        if (common > 0 && source[common - 1].isHighSurrogate()) common--
        while (ends.isNotEmpty() && ends.last() > common) { ends.removeAt(ends.lastIndex); times.removeAt(times.lastIndex) }
        shown = minOf(shown, common)
        var at = maxOf(now.toDouble(), earliest.toDouble(), times.lastOrNull() ?: 0.0)
        var i = common
        while (i < value.length) {
            val end = i + Character.charCount(value.codePointAt(i))
            at += maxOf(0.0, delay(value.substring(i, end)) * 1000)
            ends.add(end); times.add(at)
            i = end
        }
        source = value
    }

    fun advance(now: Long): Boolean {
        var lo = 0
        var hi = times.size
        while (lo < hi) {
            val mid = (lo + hi) / 2
            if (times[mid] <= now) lo = mid + 1 else hi = mid
        }
        val next = if (lo == 0) 0 else ends[lo - 1]
        val changed = shown != next
        shown = next
        return changed
    }
}
