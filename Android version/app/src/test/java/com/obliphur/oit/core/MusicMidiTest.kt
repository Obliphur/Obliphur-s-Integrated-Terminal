package com.obliphur.oit.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MusicMidiTest {
    private fun sample() = MusicMidi.recording(listOf(
        0.0 to byteArrayOf(0x90.toByte(), 60, 100),
        0.0 to byteArrayOf(0xb0.toByte(), 64, 127),
        0.5 to byteArrayOf(0x80.toByte(), 60, 0),
        0.75 to byteArrayOf(0xb0.toByte(), 64, 0)
    ), 1.0)

    @Test fun recordingRoundTripsAndComparesAgainstItself() {
        val midi = sample()
        val score = MusicMidi.read(midi)
        assertEquals(1, MusicMidi.notes(score).size)
        assertEquals(60, MusicMidi.notes(score).single().pitch)
        val summary = MusicMidi.compare(midi, midi).getJSONObject("summary")
        assertEquals(1, summary.getInt("matched"))
        assertEquals(0, summary.getInt("missing"))
        assertEquals(0, summary.getInt("extra"))
    }

    @Test fun humanizationIsSeededAndPreservesPedalAndNotePairs() {
        val original = sample()
        val first = MusicMidi.humanize(original, 42)
        assertTrue(first.contentEquals(MusicMidi.humanize(original, 42)))
        val events = MusicMidi.read(first).tracks.flatten().map { it.bytes }
        assertTrue(events.any { it.contentEquals(byteArrayOf(0xb0.toByte(), 64, 127)) })
        assertEquals(1, MusicMidi.notes(MusicMidi.read(first)).size)
    }
}
