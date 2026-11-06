package com.obliphur.oit.core

import org.json.JSONObject
import java.io.File
import java.time.LocalDateTime
import java.time.ZoneId

/** 到点了: at = 预定的响铃时刻, co = 是不是按共时闹钟响的. */
class AlarmHit(val at: LocalDateTime, val co: Boolean)

/**
 * 闹钟 (1.0.359, MECHANICS 25): 设置 → 闹钟 (时间 / 铃声 / 音量) + 沟通的"共时闹钟" (时间跟随对象的实际起床时间). 对应桌面版 alarm.py.
 *
 * · 配置只存本机 (alarm.json), 不同步: 哪台设备响由各自的开关决定. 共时闹钟的设置 (哪个对象 / 哪些设备响铃) 在 Comm 里, 随设备同步.
 * · 每天重复. 到点之后最多 MISS_SECONDS 内发现的都会响; 更晚才发现的这次不响, 排到下一次.
 *   从开启 / 改时间 / 改共时闹钟的那一刻起算, 之前已经过去的钟点不补响.
 * · 音量 = 铃声的幅度 (gain = (音量/100)²).
 */
class AlarmStore(private val file: File, private val now: () -> LocalDateTime = { LocalDateTime.now() }) {
    var on = false; private set
    var time = "07:00"; private set
    var tone = "classic"; private set
    /** 系统铃声的 Uri (空 = 系统默认闹钟铃声). */
    var custom = ""; private set
    /** 自选的音频文件: 选的时候复制进应用私有目录 (file = 复制后的路径, fileName = 原来的文件名), 之后不依赖原文件. */
    var audio = ""; private set
    var audioName = ""; private set
    var volume = 80; private set
    private var armed = ""
    private var fired = ""
    private var coSig = ""

    init { load() }

    private fun load() {
        val d = Store.readObj(file) ?: return
        on = d.optBoolean("on", false)
        time = d.optString("time", "07:00").let { if (parseHm(it) != null) fmtHm(parseHm(it)!!) else "07:00" }
        tone = d.optString("tone", "classic").takeIf { t -> TONES.any { it.first == t } } ?: "classic"
        custom = d.optString("custom", "")
        audio = d.optString("file", "")
        audioName = d.optString("file_name", "")
        volume = d.optInt("volume", 80).coerceIn(0, 100)
        armed = d.optString("armed", "")
        fired = d.optString("fired", "")
        coSig = d.optString("co_sig", "")
    }

    private fun save() {
        Store.write(file, JSONObject().put("on", on).put("time", time).put("tone", tone).put("custom", custom).put("file", audio).put("file_name", audioName).put("volume", volume)
            .put("armed", armed).put("fired", fired).put("co_sig", coSig))
    }

    /** 改设置并马上存; 开关 / 时间变了就重新计时 (之前过去的钟点不补响). */
    @Synchronized
    fun set(on: Boolean? = null, time: String? = null, tone: String? = null, custom: String? = null, volume: Int? = null,
            audio: String? = null, audioName: String? = null) {
        var reArm = false
        on?.let { if (this.on != it) reArm = true; this.on = it }
        time?.let { t ->
            val hm = parseHm(t)
            if (hm != null) { val s = fmtHm(hm); if (this.time != s) reArm = true; this.time = s }
        }
        tone?.let { t -> if (TONES.any { it.first == t }) this.tone = t }
        custom?.let { this.custom = it }
        audio?.let { this.audio = it }
        audioName?.let { this.audioName = it }
        volume?.let { this.volume = it.coerceIn(0, 100) }
        if (reArm) armed = iso(now())
        save()
    }

    fun hm(): Pair<Int, Int> = parseHm(time) ?: (7 to 0)

    /** 交给播放器的"自定义声音"参数: 自选文件的路径 / 系统铃声的 Uri. */
    fun soundArg(): String = if (tone == "file") audio else custom

    private fun ref(t: LocalDateTime): LocalDateTime {
        val a = parseIso(armed) ?: t
        val f = parseIso(fired)
        return if (f != null && f.isAfter(a)) f else a
    }

    /** 下一次响铃的时刻 (没有 = null); 不改任何状态. coFn: 按共时闹钟响铃时的 f(after) → 下一次起床时刻. */
    @Synchronized
    fun nextRing(t: LocalDateTime = now(), coFn: ((LocalDateTime) -> LocalDateTime?)? = null): LocalDateTime? {
        if (coFn == null && !on) return null
        val floor = t.minusSeconds(MISS_SECONDS)
        val r = ref(t).let { if (it.isBefore(floor)) floor else it }
        return if (coFn != null) coFn(r) else nextTime(hm(), r)
    }

    /** 下一次响铃的时刻 (epoch 毫秒), 交给系统闹钟; 没有 = 0. */
    fun nextRingMs(t: LocalDateTime = now(), coFn: ((LocalDateTime) -> LocalDateTime?)? = null): Long =
        nextRing(t, coFn)?.atZone(ZoneId.systemDefault())?.toInstant()?.toEpochMilli() ?: 0L

    /** 每秒调一次. 该响了 → AlarmHit; 否则 null. 响过 / 错过的会记下来, 下一次排到之后. */
    @Synchronized
    fun check(t: LocalDateTime = now(), coFn: ((LocalDateTime) -> LocalDateTime?)? = null, sig: String = ""): AlarmHit? {
        val s = if (coFn != null) sig else ""
        if (s != coSig) { coSig = s; armed = iso(t); save() }       // 换了跟随的对象 / 开关了共时闹钟: 重新计时
        if (coFn == null && !on) return null
        if (parseIso(armed) == null) { armed = iso(t); save() }
        repeat(4) {
            val r = ref(t)
            val nxt = (if (coFn != null) coFn(r) else nextTime(hm(), r)) ?: return null
            if (nxt.isAfter(t)) return null
            fired = iso(nxt)
            save()
            if (java.time.Duration.between(nxt, t).seconds <= MISS_SECONDS) return AlarmHit(nxt, coFn != null)
        }
        return null
    }

    /** 设置页上一行说明: 下一次什么时候响. */
    fun statusText(t: LocalDateTime = now(), coFn: ((LocalDateTime) -> LocalDateTime?)? = null, coName: String = ""): String {
        if (coFn != null) {
            val n = coFn(t) ?: return ""
            return tr("共时闹钟: 跟随 {name} 的起床时间, 下一次 {day} {t}", "name" to coName, "day" to tr(dayWord(n, t)), "t" to "%02d:%02d".format(n.hour, n.minute))
        }
        val n = nextRing(t) ?: return tr("闹钟已关闭")
        return tr("下一次: {day} {t}", "day" to tr(dayWord(n, t)), "t" to "%02d:%02d".format(n.hour, n.minute))
    }

    companion object {
        const val MISS_SECONDS = 900L                 // 到点后超过这么久才发现 (手机没电关机 / 程序被杀): 这次不响了
        const val RING_SECONDS = 300L                 // 响铃最长多久 (没人关就自己停)
        const val PREVIEW_SECONDS = 6L                // 试听最长多久
        /** 铃声: classic 经典警报 (res/raw/target_alarm), chime 三音提示, soft 柔和钟声, custom 系统铃声 (设置里挑), file 自选的音频文件 (从文件里选). */
        val TONES = listOf("classic" to "经典警报", "chime" to "三音提示", "soft" to "柔和钟声", "custom" to "系统铃声", "file" to "自选文件")
        private val HM_RE = Regex("""^\s*(\d{1,2})\s*[:：]\s*(\d{1,2})\s*$""")

        fun parseHm(text: String): Pair<Int, Int>? {
            val m = HM_RE.find(text) ?: return null
            val h = m.groupValues[1].toInt()
            val mi = m.groupValues[2].toInt()
            return if (h > 23 || mi > 59) null else h to mi
        }

        fun fmtHm(hm: Pair<Int, Int>) = "%02d:%02d".format(hm.first, hm.second)

        /** after 之后 (不含) 第一个钟点是 hm 的时刻. */
        fun nextTime(hm: Pair<Int, Int>, after: LocalDateTime): LocalDateTime {
            val t = after.toLocalDate().atStartOfDay().plusHours(hm.first.toLong()).plusMinutes(hm.second.toLong())
            return if (t.isAfter(after)) t else t.plusDays(1)
        }

        /** 音量 0-100 → 幅度系数 (平方: 50 约等于听起来的一半). */
        fun gainOf(volume: Int): Float { val v = volume.coerceIn(0, 100) / 100f; return v * v }

        fun dayWord(t: LocalDateTime, now: LocalDateTime): String = when (java.time.temporal.ChronoUnit.DAYS.between(now.toLocalDate(), t.toLocalDate())) {
            0L -> "今天"; 1L -> "明天"; 2L -> "后天"; else -> "${t.monthValue}月${t.dayOfMonth}日"
        }

        private fun iso(t: LocalDateTime) = t.withNano(0).toString()
        private fun parseIso(s: String): LocalDateTime? = if (s.isEmpty()) null else runCatching { LocalDateTime.parse(s) }.getOrNull()
    }
}
