package com.obliphur.oit.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.util.ArrayDeque
import java.util.Random
import kotlin.math.*

/** SMF 0/1 读写、速度映射、人性化和差异统计；参考谱永远不做扰动。 */
object MusicMidi {
    data class Event(var tick: Long, var bytes: ByteArray)
    data class Score(val format: Int, val ppq: Int, val tracks: List<MutableList<Event>>)
    data class Note(val on: Double, val duration: Double, val pitch: Int, val velocity: Int, val track: Int)
    private fun u(b: Byte) = b.toInt() and 255

    fun read(data: ByteArray): Score {
        require(data.size in 14..Cfg.MUSIC_MAX_BYTES && String(data, 0, 4, Charsets.US_ASCII) == "MThd") { "MIDI 文件无效或格式不受支持" }
        var pos = 4
        fun number(n: Int): Int { require(pos + n <= data.size); var v = 0; repeat(n) { v = (v shl 8) or u(data[pos++]) }; return v }
        val header = number(4)
        val format = number(2); val count = number(2); val ppq = number(2)
        require(header >= 6 && header <= data.size - 8 && format in 0..1 && count > 0 && (format != 0 || count == 1) && ppq in 1..32767) { "MIDI 文件无效或格式不受支持" }
        pos = 8 + header
        var total = 0
        val tracks = (0 until count).map {
            require(pos + 8 <= data.size && String(data, pos, 4, Charsets.US_ASCII) == "MTrk")
            pos += 4
            val size = number(4); require(size >= 0 && size <= data.size - pos)
            val end = pos + size
            var tick = 0L; var running = 0
            fun vlq(): Int { var v = 0; repeat(4) { require(pos < end); val b = u(data[pos++]); v = (v shl 7) or (b and 127); if (b < 128) return v }; error("Invalid MIDI VLQ") }
            val events = ArrayList<Event>()
            while (pos < end) {
                require(++total <= Cfg.MUSIC_MAX_EVENTS)
                tick += vlq()
                require(pos < end)
                var status = u(data[pos])
                if (status >= 128) pos++ else { require(running != 0); status = running }
                val bytes: ByteArray
                if (status in 0x80..0xef) {
                    running = status
                    val length = if (status and 0xf0 in setOf(0xc0, 0xd0)) 1 else 2
                    require(pos + length <= end && (pos until pos + length).all { u(data[it]) < 128 })
                    bytes = byteArrayOf(status.toByte()) + data.copyOfRange(pos, pos + length); pos += length
                } else {
                    running = 0
                    require(status in setOf(0xff, 0xf0, 0xf7))
                    val start = pos
                    if (status == 0xff) { require(pos < end && u(data[pos]) < 128); pos++ }
                    val length = vlq(); require(length <= end - pos)
                    pos += length
                    bytes = byteArrayOf(status.toByte()) + data.copyOfRange(start, pos)
                    if (bytes.size > 1 && u(bytes[0]) == 255 && u(bytes[1]) == 81) require(bytes.size == 6 && bytes.drop(3).any { it != 0.toByte() })
                }
                events.add(Event(tick, bytes))
            }
            require(events.lastOrNull()?.bytes?.contentEquals(byteArrayOf(-1, 47, 0)) == true)
            events
        }
        require(pos == data.size)
        return Score(format, ppq, tracks)
    }

    private fun vlq(value: Long): ByteArray {
        require(value in 0..0x0fffffff)
        var v = value; val bytes = ArrayList<Byte>(); bytes.add((v and 127).toByte())
        while (v > 127) { v = v shr 7; bytes.add(((v and 127) or 128).toByte()) }
        return bytes.reversed().toByteArray()
    }
    fun write(score: Score): ByteArray {
        val out = ByteArrayOutputStream()
        fun number(v: Long, n: Int) { for (i in n - 1 downTo 0) out.write((v shr (i * 8)).toInt() and 255) }
        out.write("MThd".toByteArray()); number(6, 4); number(score.format.toLong(), 2); number(score.tracks.size.toLong(), 2); number(score.ppq.toLong(), 2)
        score.tracks.forEach { track ->
            val body = ByteArrayOutputStream(); var previous = 0L
            track.forEach { event -> body.write(vlq(event.tick - previous)); body.write(event.bytes); previous = event.tick }
            out.write("MTrk".toByteArray()); number(body.size().toLong(), 4); out.write(body.toByteArray())
        }
        return out.toByteArray()
    }

    class Tempo(score: Score) {
        private data class Point(val tick: Long, val sec: Double, val rate: Double)
        private val points: List<Point>
        init {
            val changes = sortedMapOf(0L to 500000)
            score.tracks.flatten().sortedBy { it.tick }.forEach { e -> if (e.bytes.size == 6 && u(e.bytes[0]) == 255 && u(e.bytes[1]) == 81)
                changes[e.tick] = (u(e.bytes[3]) shl 16) or (u(e.bytes[4]) shl 8) or u(e.bytes[5]) }
            var previous = 0L; var sec = 0.0; var rate = .5 / score.ppq
            points = changes.map { (tick, tempo) -> sec += (tick - previous) * rate; previous = tick; rate = tempo / 1000000.0 / score.ppq; Point(tick, sec, rate) }
        }
        fun seconds(tick: Long): Double { val p = points.last { it.tick <= tick }; return p.sec + (tick - p.tick) * p.rate }
        fun tick(seconds: Double): Long { val s = seconds.coerceAtLeast(0.0); val p = points.last { it.sec <= s }; return (p.tick + (s - p.sec) / p.rate).roundToLong().coerceAtLeast(0) }
    }
    private data class Pairing(val on: Event, val off: Event, val oldOn: Long, val oldOff: Long, val track: Int)
    private fun pairs(score: Score, drums: Boolean = true): List<Pairing> {
        val notes = ArrayList<Pairing>()
        score.tracks.forEachIndexed { index, track ->
            val active = HashMap<Int, ArrayDeque<Event>>()
            track.forEach { e ->
                val status = u(e.bytes[0]); val kind = status and 240
                if (kind in setOf(128, 144) && (drums || status and 15 != 9)) {
                    val key = (status and 15) * 128 + u(e.bytes[1]); val q = active.getOrPut(key) { ArrayDeque() }
                    if (kind == 144 && e.bytes[2] != 0.toByte()) q.add(e)
                    else if (q.isNotEmpty()) { val on = q.removeFirst(); notes.add(Pairing(on, e, on.tick, e.tick, index)) }
                }
            }
            require(active.values.all { it.isEmpty() }) { "MIDI 含有未结束的音符" }
        }
        return notes
    }
    fun notes(score: Score): List<Note> {
        val tempo = Tempo(score)
        return pairs(score).map { n -> Note(tempo.seconds(n.oldOn), tempo.seconds(n.oldOff) - tempo.seconds(n.oldOn), u(n.on.bytes[1]), u(n.on.bytes[2]), n.track) }.sortedBy { it.on }
    }
    fun duration(score: Score) = Tempo(score).seconds(score.tracks.maxOf { it.last().tick })

    fun humanize(bytes: ByteArray, seed: Long = System.nanoTime()): ByteArray {
        val score = read(bytes); val tempo = Tempo(score); val notes = pairs(score, drums = false)
        require(notes.isNotEmpty()) { "MIDI 没有可演奏的音符" }
        val rng = Random(seed); val knots = HashMap<Long, Double>()
        fun bounded(sigma: Double) = (rng.nextGaussian() * sigma).coerceIn(-Cfg.MUSIC_HUMANIZE_SIGMA_LIMIT * sigma, Cfg.MUSIC_HUMANIZE_SIGMA_LIMIT * sigma)
        val groups = notes.groupBy { it.oldOn }.toSortedMap(); val ticks = groups.keys.toList()
        ticks.forEachIndexed { index, tick ->
            val sec = tempo.seconds(tick)
            val gaps = listOf(index - 1, index + 1).filter { it in ticks.indices }.map { abs(tempo.seconds(ticks[it]) - sec) }
            val limit = (gaps.minOrNull() ?: Double.POSITIVE_INFINITY) * Cfg.MUSIC_HUMANIZE_IOI_FRACTION
            val position = tick / score.ppq.toDouble() / Cfg.MUSIC_HUMANIZE_DRIFT_BEATS
            val left = floor(position).toLong(); val f = position - left; val w = f * f * (3 - 2 * f)
            fun knot(i: Long) = knots.getOrPut(i) { (rng.nextDouble() * 2 - 1) * Cfg.MUSIC_HUMANIZE_DRIFT_MS / 1000 }
            val shared = knot(left) * (1 - w) + knot(left + 1) * w + bounded(Cfg.MUSIC_HUMANIZE_GROUP_MS) / 1000
            val velocity = bounded(Cfg.MUSIC_HUMANIZE_VELOCITY / 2)
            groups.getValue(tick).forEach { n ->
                val duration = tempo.seconds(n.oldOff) - sec
                val shift = (shared + bounded(Cfg.MUSIC_HUMANIZE_FINGER_MS) / 1000).coerceIn(-limit, limit)
                n.on.tick = tempo.tick(sec + shift)
                val release = (bounded(Cfg.MUSIC_HUMANIZE_RELEASE_MS) / 1000).coerceIn(-duration * Cfg.MUSIC_HUMANIZE_RELEASE_FRACTION, duration * Cfg.MUSIC_HUMANIZE_RELEASE_FRACTION)
                n.off.tick = max(n.on.tick + 1, tempo.tick(tempo.seconds(n.on.tick) + duration + release))
                n.on.bytes[2] = (u(n.on.bytes[2]) + velocity + bounded(Cfg.MUSIC_HUMANIZE_VELOCITY)).roundToInt().coerceIn(1, 127).toByte()
            }
        }
        notes.groupBy { (u(it.on.bytes[0]) and 15) * 128 + u(it.on.bytes[1]) }.values.forEach { seq ->
            val starts = seq.groupBy { it.oldOn }.toSortedMap()
            seq.forEach { n -> starts.entries.firstOrNull { it.key >= n.oldOff }?.let { next -> n.off.tick = min(n.off.tick, max(n.on.tick + 1, next.value.minOf { it.on.tick } - 1)) } }
        }
        score.tracks.forEach { track ->
            track.last().tick = track.maxOf { it.tick }
            fun priority(e: Event): Int {
                val status = u(e.bytes[0]); val kind = status and 240
                return when { e.bytes.contentEquals(byteArrayOf(-1, 47, 0)) -> 3; kind in setOf(128, 144) && status and 15 == 9 -> 1
                    kind == 128 || kind == 144 && e.bytes[2] == 0.toByte() -> 0; kind == 144 -> 2; else -> 1 }
            }
            track.sortWith(compareBy<Event> { it.tick }.thenBy { priority(it) })
        }
        return write(score)
    }

    fun compare(reference: ByteArray, performance: ByteArray): JSONObject {
        val rScore = read(reference); val pScore = read(performance)
        fun aligned(score: Score): List<Note> { val ns = notes(score); val start = ns.firstOrNull()?.on ?: 0.0; return ns.map { it.copy(on = it.on - start) } }
        val ref = aligned(rScore); val perf = aligned(pScore)
        require(ref.isNotEmpty() && perf.isNotEmpty()) { "MIDI 没有可演奏的音符" }
        val rd = ref.maxOf { it.on + it.duration }; val pd = perf.maxOf { it.on + it.duration }; val ratio = if (rd > 0 && pd > 0) pd / rd else 1.0
        val unused = perf.indices.toMutableSet(); val missing = ArrayList<Note>(); val matched = ArrayList<Pair<Note, Note>>()
        ref.forEach { r ->
            val i = unused.filter { perf[it].pitch == r.pitch }.minByOrNull { abs(perf[it].on / ratio - r.on) }
            if (i == null || abs(perf[i].on / ratio - r.on) > max(.75, rd * .01)) missing.add(r)
            else { unused.remove(i); matched.add(r to perf[i]) }
        }
        val onset = matched.map { (r, p) -> p.on / ratio - r.on }; val durations = matched.map { (r, p) -> p.duration / ratio - r.duration }
        fun mean(values: List<Double>, absolute: Boolean = false): Any = if (values.isEmpty()) JSONObject.NULL else (values.sumOf { if (absolute) abs(it) else it } / values.size * 1000).roundToLong()
        fun counts(ns: List<Note>) = JSONObject().apply { ns.groupingBy { it.pitch }.eachCount().forEach { (pitch, n) -> put(pitch.toString(), n) } }
        return JSONObject().put("schema", 1).put("summary", JSONObject().put("reference_notes", ref.size).put("performance_notes", perf.size)
            .put("matched", matched.size).put("missing", missing.size).put("extra", unused.size).put("performance_duration_ms", (duration(pScore) * 1000).roundToLong())
            .put("tempo_ratio_percent", (ratio * 100).roundToLong()).put("onset_error_ms_mean", mean(onset)).put("onset_error_ms_abs_mean", mean(onset, true))
            .put("duration_error_ms_abs_mean", mean(durations, true)))
            .put("largest_timing_errors_ms", JSONArray(onset.map { (it * 1000).roundToLong() }.sortedByDescending { abs(it) }.take(8)))
            .put("missing_pitch_counts", counts(missing)).put("extra_pitch_counts", counts(unused.sorted().map { perf[it] }))
    }

    /** 实时输入和转录共用秒→固定速度 MIDI，保留控制器和尾部时长。 */
    fun recording(events: List<Pair<Double, ByteArray>>, duration: Double): ByteArray {
        val track = events.map { (sec, bytes) -> Event((sec * 960).roundToLong().coerceAtLeast(0), bytes) }.toMutableList()
        track.add(Event((duration * 960).roundToLong().coerceAtLeast(track.maxOfOrNull { it.tick } ?: 0), byteArrayOf(-1, 47, 0)))
        track.sortBy { it.tick }
        return write(Score(0, 480, listOf(track)))
    }
}
