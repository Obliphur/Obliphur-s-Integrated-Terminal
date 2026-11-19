package com.obliphur.oit.core

import android.content.Context
import android.media.AudioFormat
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import java.io.File
import java.nio.ByteOrder
import java.nio.FloatBuffer
import kotlin.math.*

/** Basic Pitch 0.4.0 的 22050 Hz 窗口及音符后处理；仅本机处理，不上传录音。 */
class MusicTranscribe(private val ctx: Context, private val model: File) {
    fun convert(source: File, progress: (Int) -> Unit): ByteArray {
        val audio = decode(source)
        val frameCount = (audio.size * 86L / 22050).toInt()
        val frames = Array(frameCount) { FloatArray(88) }; val onsets = Array(frameCount) { FloatArray(88) }
        val env = OrtEnvironment.getEnvironment()
        OrtSession.SessionOptions().use { options ->
            options.setIntraOpNumThreads(Cfg.MUSIC_CPU_THREADS)
            options.setInterOpNumThreads(1)
            env.createSession(model.absolutePath, options).use { session ->
                var start = 0; var output = 0
                while (start < audio.size + 3840) {
                    check(!Thread.currentThread().isInterrupted)
                    val window = FloatArray(43844) { j -> audio.getOrElse(start + j - 3840) { 0f } }
                    OnnxTensor.createTensor(env, FloatBuffer.wrap(window), longArrayOf(1, 43844, 1)).use { tensor ->
                        session.run(mapOf(session.inputNames.first() to tensor)).use { result ->
                            @Suppress("UNCHECKED_CAST")
                            val notes = (result.get("StatefulPartitionedCall:1").get().value as Array<Array<FloatArray>>)[0]
                            @Suppress("UNCHECKED_CAST")
                            val attacks = (result.get("StatefulPartitionedCall:2").get().value as Array<Array<FloatArray>>)[0]
                            for (j in 15 until notes.size - 15) if (output < frameCount) {
                                notes[j].copyInto(frames[output]); attacks[j].copyInto(onsets[output]); output++
                            }
                        }
                    }
                    start += 36164
                    progress((start * 85L / (audio.size + 3840)).toInt().coerceAtMost(85))
                }
            }
        }
        val events = extract(frames, onsets)
        require(events.isNotEmpty()) { "录音没有识别到可演奏音符" }
        progress(95)
        return MusicMidi.recording(events, audio.size / 22050.0)
    }

    private fun decode(source: File): FloatArray {
        val extractor = MediaExtractor()
        var codec: MediaCodec? = null
        try {
            extractor.setDataSource(source.absolutePath)
            val track = (0 until extractor.trackCount).firstOrNull { extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true }
                ?: error("文件中没有音频轨道")
            extractor.selectTrack(track)
            val format = extractor.getTrackFormat(track)
            if (format.containsKey(MediaFormat.KEY_DURATION)) require(format.getLong(MediaFormat.KEY_DURATION) <= Cfg.MUSIC_TRANSCRIBE_MAX_SECONDS * 1000000L) { "录音转录最长支持十分钟" }
            codec = MediaCodec.createDecoderByType(format.getString(MediaFormat.KEY_MIME)!!)
            codec.configure(format, null, null, 0); codec.start()
            var rate = format.getInteger(MediaFormat.KEY_SAMPLE_RATE); var channels = format.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
            var encoding = AudioFormat.ENCODING_PCM_16BIT
            val samples = ArrayList<FloatArray>(); var sampleCount = 0
            var inputEnded = false; var ended = false; var lastOutput = System.nanoTime()
            val info = MediaCodec.BufferInfo()
            while (!ended) {
                check(!Thread.currentThread().isInterrupted && System.nanoTime() - lastOutput < 30000000000L) { "音频解码超时" }
                if (!inputEnded) {
                    val index = codec.dequeueInputBuffer(10000)
                    if (index >= 0) {
                        val input = codec.getInputBuffer(index)!!
                        val size = extractor.readSampleData(input, 0)
                        if (size < 0) { codec.queueInputBuffer(index, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM); inputEnded = true }
                        else { codec.queueInputBuffer(index, 0, size, extractor.sampleTime, 0); extractor.advance() }
                    }
                }
                val index = codec.dequeueOutputBuffer(info, 10000)
                if (index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    val output = codec.outputFormat
                    rate = output.getInteger(MediaFormat.KEY_SAMPLE_RATE); channels = output.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
                    encoding = if (output.containsKey(MediaFormat.KEY_PCM_ENCODING)) output.getInteger(MediaFormat.KEY_PCM_ENCODING) else AudioFormat.ENCODING_PCM_16BIT
                } else if (index >= 0) {
                    val buffer = codec.getOutputBuffer(index)!!.order(ByteOrder.LITTLE_ENDIAN)
                    buffer.position(info.offset); buffer.limit(info.offset + info.size)
                    val bytes = if (encoding == AudioFormat.ENCODING_PCM_FLOAT) 4 else 2
                    require(encoding in setOf(AudioFormat.ENCODING_PCM_FLOAT, AudioFormat.ENCODING_PCM_16BIT)) { "音频编码不受支持" }
                    val mono = FloatArray(info.size / bytes / channels) {
                        var value = 0f
                        repeat(channels) { value += if (bytes == 4) buffer.float else buffer.short / 32768f }
                        (value / channels).coerceIn(-1f, 1f)
                    }
                    samples.add(mono); sampleCount += mono.size
                    require(sampleCount.toLong() <= rate.toLong() * Cfg.MUSIC_TRANSCRIBE_MAX_SECONDS) { "录音转录最长支持十分钟" }
                    ended = info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0
                    codec.releaseOutputBuffer(index, false); lastOutput = System.nanoTime()
                }
            }
            val raw = FloatArray(sampleCount); var offset = 0
            samples.forEach { it.copyInto(raw, offset); offset += it.size }; samples.clear()
            require(raw.isNotEmpty()) { "录音为空" }
            if (rate == 22050) return raw
            // 低通窗化 sinc 重采样，避免直接抽样把高频混叠进模型输入。
            val length = (sampleCount.toLong() * 22050 / rate).toInt()
            val cutoff = min(1.0, 22050.0 / rate) * .95
            return FloatArray(length) { i ->
                val position = i * rate / 22050.0; val center = floor(position).toInt()
                var value = 0.0; var weight = 0.0
                for (j in center - 16..center + 16) if (j in raw.indices) {
                    val x = position - j; val a = PI * x * cutoff
                    val w = cutoff * (if (abs(a) < 1e-8) 1.0 else sin(a) / a) * (.5 + .5 * cos(PI * x / 17))
                    value += raw[j] * w; weight += w
                }
                (if (weight != 0.0) value / weight else 0.0).toFloat()
            }
        } finally { codec?.let { runCatching { it.stop() }; it.release() }; extractor.release() }
    }

    internal fun extract(frames: Array<FloatArray>, onsets: Array<FloatArray>): List<Pair<Double, ByteArray>> {
        if (frames.size < 3) return emptyList()
        val n = frames.size; val inferred = Array(n) { FloatArray(88) }
        var maxDifference = 0f; var maxOnset = 0f
        for (t in frames.indices) for (p in 0..87) {
            maxOnset = max(maxOnset, onsets[t][p])
            if (t >= 2) {
                val d = max(0f, min(frames[t][p] - frames[t - 1][p], frames[t][p] - frames[t - 2][p]))
                inferred[t][p] = d; maxDifference = max(maxDifference, d)
            }
        }
        for (t in frames.indices) for (p in 0..87) inferred[t][p] = max(onsets[t][p], if (maxDifference > 0) inferred[t][p] * maxOnset / maxDifference else 0f)
        val remaining = Array(n) { frames[it].copyOf() }; val events = ArrayList<Pair<Double, ByteArray>>()
        fun erase(t: Int, p: Int) { for (q in max(0, p - 1)..min(87, p + 1)) remaining[t][q] = 0f }
        fun time(t: Int): Double {
            // 与 Basic Pitch model_frames_to_time 的窗口漂移修正一致。
            val window = 43844.0 / 22050
            val frame = window / 172
            return max(0.0, t * 256.0 / 22050 - (floor(t / 172.0) * (frame - 256.0 / 22050) * 172))
        }
        fun emit(start: Int, end: Int, pitch: Int) {
            if (end - start <= 11) return
            val velocity = ((start until end).sumOf { frames[it][pitch].toDouble() } / (end - start) * 127).roundToInt().coerceIn(1, 127)
            events.add(time(start) to byteArrayOf(0x90.toByte(), (pitch + 21).toByte(), velocity.toByte()))
            events.add(time(end) to byteArrayOf(0x80.toByte(), (pitch + 21).toByte(), 0))
        }
        for (t in n - 2 downTo 1) for (p in 87 downTo 0) if (inferred[t][p] >= .5f && inferred[t][p] > inferred[t - 1][p] && inferred[t][p] > inferred[t + 1][p]) {
            var end = t + 1; var quiet = 0
            while (end < n - 1 && quiet < 11) { quiet = if (remaining[end][p] < .3f) quiet + 1 else 0; end++ }
            end -= quiet
            if (end - t > 11) { for (i in t until end) erase(i, p); emit(t, end, p) }
        }
        // 以能量优先级处理残留段；相邻音高清除后跳过陈旧候选，避免全矩阵反复扫描。
        data class Peak(val t: Int, val p: Int, val value: Float)
        val peaks = java.util.PriorityQueue<Peak>(compareByDescending<Peak> { it.value }.thenBy { it.t }.thenBy { it.p })
        for (t in 1 until n - 1) for (p in 0..87) if (remaining[t][p] > .3f && remaining[t][p] >= remaining[t - 1][p] && remaining[t][p] >= remaining[t + 1][p]) peaks.add(Peak(t, p, remaining[t][p]))
        while (peaks.isNotEmpty()) {
            val peak = peaks.remove(); val p = peak.p; val mid = peak.t
            if (remaining[mid][p] <= .3f) continue
            remaining[mid][p] = 0f
            var end = mid + 1; var quiet = 0
            while (end < n - 1 && quiet < 11) { quiet = if (remaining[end][p] < .3f) quiet + 1 else 0; erase(end, p); end++ }
            end = end - 1 - quiet
            var start = mid - 1; quiet = 0
            while (start > 0 && quiet < 11) { quiet = if (remaining[start][p] < .3f) quiet + 1 else 0; erase(start, p); start-- }
            start = start + 1 + quiet
            emit(start, end, p)
        }
        return events.sortedWith(compareBy<Pair<Double, ByteArray>> { it.first }.thenBy { it.second[0].toInt() and 255 })
    }
}
