package com.obliphur.oit.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.LocalDate
import java.time.LocalDateTime
import kotlin.math.ceil
import kotlin.math.floor

/** 一次外出. */
data class Outing(
    var id: String,
    var start: LocalDateTime,
    var hours: Int,
    var end: LocalDateTime,
    var stopped: LocalDateTime? = null,
    var rewarded: Boolean = false,
    var owner: String = "",
    var mode: String = "",
) {
    /** 实际结束 (或到此刻) 的时刻. */
    fun effectiveEnd(now: LocalDateTime): LocalDateTime {
        val s = stopped ?: return if (now < end) now else end
        return if (s < end) s else end
    }

    fun json(): JSONObject = JSONObject().put("id", id).put("start", start.iso())
        .put("hours", hours).put("end", end.iso()).put("stopped", stopped?.iso() ?: "")
        .put("rewarded", rewarded).put("owner", owner).put("mode", mode)
}

/** 到时间结束 / 发休息时间的结果. */
data class BehaviorEnd(
    val ended: List<Outing> = emptyList(),
    val reward: Int = 0,
    val hours: Double = 0.0,
)

/** 外出记录出来的一段: study 专注 / break 休息. */
data class BehaviorSeg(val kind: String, val start: LocalDateTime, val end: LocalDateTime)

/**
 * 行为模式 (设置 → 日常) 与"外出" (MECHANICS 21). 对应桌面版 behavior.py.
 *
 * 封闭式管理 / 工作: 专注页多一个"外出"按钮; 外出期间每天最多记 10 小时,
 * 7:00 ~ 23:00 之内按 80% 专注 / 20% 休息填充统计, 夜里只算休息; 整点记录的自动填充
 * 内容是「外出」/「休息」; 当天联网额度 ×0.2, 正在外出时冷静期 ×0.3;
 * 外出结束后按记录的小时数 ×6 分钟发休息时间 (最多 3 小时).
 *
 * 外出记录通过设备同步 (behavior 文档) 共用; 休息时间只由开始外出的设备发.
 * mode_since 只在本机记 (整点记录只改切换之后的小时).
 */
class Behavior(
    private val file: File,
    private val settings: Settings,
    private val now: () -> LocalDateTime = LocalDateTime::now,
) {
    val outings = mutableListOf<Outing>()
    var modeSince: LocalDateTime? = null
        private set
    private var mode = settings.str("BEHAVIOR_MODE")
    private val existed = file.exists()

    init {
        load()
        if (!existed || modeSince == null) {
            modeSince = now().withNano(0)
            save()
        }
        syncMode()
    }

    // ---- 文件 ----

    private fun load() {
        outings.clear()
        val d = Store.readObj(file) ?: return
        mode = d.optString("mode", mode)
        modeSince = parseIso(d.optString("mode_since"))
        val arr = d.optJSONArray("outings") ?: JSONArray()
        for (o in arr.objects()) clean(o)?.let { outings.add(it) }
        outings.sortBy { it.start }
        prune()
    }

    private fun clean(raw: JSONObject): Outing? {
        val start = parseIso(raw.optString("start")) ?: return null
        val end = parseIso(raw.optString("end")) ?: return null
        if (!end.isAfter(start)) return null
        var hours = raw.optInt("hours", 0)
        if (hours <= 0) hours = maxOf(1, ceil(secondsBetween(start, end) / 3600.0).toInt())
        hours = hours.coerceIn(1, Cfg.OUTING_MAX_HOURS)
        val id = raw.optString("id").ifBlank { System.currentTimeMillis().toString() + outings.size }
        return Outing(id, start, hours, end, parseIso(raw.optString("stopped")),
            raw.optBoolean("rewarded", false), raw.optString("owner"), raw.optString("mode"))
    }

    fun save(): Boolean {
        val arr = JSONArray()
        for (o in outings.sortedBy { it.start }) arr.put(o.json())
        return Store.write(file, JSONObject().put("mode", mode)
            .put("mode_since", modeSince?.iso() ?: JSONObject.NULL).put("outings", arr))
    }

    private fun prune() {
        val floor = now().minusDays(62)
        val before = outings.size
        outings.removeAll { it.stopped != null && it.effectiveEnd(now()) < floor }
        if (outings.size != before) save()
    }

    // ---- 模式 ----

    fun mode(): String = settings.str("BEHAVIOR_MODE").let { if (it in Cfg.BEHAVIOR_MODES) it else Cfg.BEHAVIOR_FREE }

    fun enabled(): Boolean = mode() == Cfg.BEHAVIOR_CLOSED || mode() == Cfg.BEHAVIOR_WORK

    /** 设置里的行为模式变了: 记下本机从什么时候开始用这个模式. */
    fun syncMode(): Boolean {
        val m = mode()
        if (mode == m) return false
        mode = m
        modeSince = now().withNano(0)
        save()
        return true
    }

    // ---- 外出 ----

    fun sorted(): List<Outing> = outings.sortedBy { it.start }

    fun active(now: LocalDateTime = this.now()): Outing? {
        var cur: Outing? = null
        for (o in outings) {
            if (o.stopped != null) continue
            if (!o.start.isAfter(now) && now.isBefore(o.end)) {
                if (cur == null || o.start.isAfter(cur!!.start)) cur = o
            }
        }
        return cur
    }

    /** 开始一次外出; "" = 好, 否则是原因. */
    fun start(hours: Int, owner: String, at: LocalDateTime = now()): String {
        if (!enabled()) return tr("当前行为模式没有外出.")
        if (hours < 1 || hours > Cfg.OUTING_MAX_HOURS) return tr("持续时间要在 1 ~ {n} 小时之间.", "n" to Cfg.OUTING_MAX_HOURS)
        val t = at.withNano(0)
        tick(t) { false }                 // 先把已经到时间的处理掉
        if (active(t) != null) return tr("已经在外面了.")
        val o = Outing(
            t.format(Fmt.TS) + System.currentTimeMillis().toString().takeLast(3),
            t, hours, t.plusHours(hours.toLong()), owner = owner, mode = mode(),
        )
        outings.add(o)
        prune()
        save()
        return ""
    }

    /** 结束当前这次外出 (提前结束也算). */
    fun stop(canReward: (Outing) -> Boolean, at: LocalDateTime = now()): BehaviorEnd {
        val t = at.withNano(0)
        val o = active(t) ?: outings.filter { it.stopped == null }.maxByOrNull { it.start }
        if (o != null && o.stopped == null) {
            o.stopped = if (t < o.end) t else o.end
            save()
        }
        return tick(t, canReward)
    }

    /** 到时间的外出自动结束; 把还没发的休息时间发掉. */
    fun tick(at: LocalDateTime = now(), canReward: (Outing) -> Boolean = { true }): BehaviorEnd {
        val t = at.withNano(0)
        val ended = mutableListOf<Outing>()
        for (o in outings) {
            if (o.stopped == null && !o.end.isAfter(t)) {
                o.stopped = o.end
                ended.add(o)
            }
        }
        if (ended.isNotEmpty()) save()
        var reward = 0
        var hours = 0.0
        var changed = false
        for (o in sorted()) {
            if (o.stopped == null || o.rewarded) continue
            val ok = try { canReward(o) } catch (e: Exception) { false }
            if (!ok) continue
            val h = recordedHours(o, t)
            reward += minOf(Cfg.OUTING_REST_MAX, Math.round(h * Cfg.OUTING_REST_PER_HOUR).toInt())
            hours += h
            o.rewarded = true
            changed = true
        }
        if (changed) save()
        return BehaviorEnd(ended, reward, hours)
    }

    // ---- 记录 ----

    private fun overlap(o: Outing, day: LocalDateTime, at: LocalDateTime = now()): Pair<LocalDateTime, LocalDateTime>? {
        val d0 = day.toLocalDate().atStartOfDay()
        val a = if (o.start.isAfter(d0)) o.start else d0
        val e = o.effectiveEnd(at)
        val b = if (e.isBefore(d0.plusDays(1))) e else d0.plusDays(1)
        return if (b.isAfter(a)) a to b else null
    }

    private fun daySeconds(o: Outing, day: LocalDateTime, at: LocalDateTime = now()): Double =
        overlap(o, day, at)?.let { secondsBetween(it.first, it.second) } ?: 0.0

    /** 这段外出在某一天被记录的小时数: 向下取整, 扣掉同一天更早的外出已经记过的小时. */
    private fun recordedHoursOn(o: Outing, day: LocalDateTime, at: LocalDateTime): Double {
        val sec = daySeconds(o, day, at)
        if (sec <= 0) return 0.0
        var used = 0.0
        for (prev in sorted()) {
            if (prev.id == o.id) break
            val psec = daySeconds(prev, day, at)
            if (psec > 0) used += minOf(10.0, floor(psec / 3600.0 + 1e-9))
        }
        return minOf(maxOf(0.0, 10.0 - used), floor(sec / 3600.0 + 1e-9))
    }

    /** 这段外出被记录的小时数: 每天各自向下取整, 每天最多 10 小时.
     *  不足 1 小时的部分不计; 同一天有多次外出时, 先开始的先记, 后面的只记当天剩下的小时数. */
    fun recordedHours(o: Outing, at: LocalDateTime = now()): Double {
        var day = o.start.toLocalDate().atStartOfDay()
        val last = o.effectiveEnd(at).toLocalDate().atStartOfDay()
        var total = 0.0
        while (!day.isAfter(last)) {
            total += recordedHoursOn(o, day, at)
            day = day.plusDays(1)
        }
        return total
    }

    /** 有外出的日期 ("YYYY-MM-DD"), 统计 / 日历用. */
    fun days(): Set<String> {
        val out = HashSet<String>()
        val at = now()
        for (o in outings) {
            var day = o.start.toLocalDate().atStartOfDay()
            val last = o.effectiveEnd(at).toLocalDate().atStartOfDay()
            while (!day.isAfter(last)) {
                if (daySeconds(o, day, at) > 0) out.add(day.toLocalDate().key())
                day = day.plusDays(1)
            }
        }
        return out
    }

    /** 某一天外出记录出来的一段段: 每一段按被记录的小时数 (向下取整) 填充;
     *  7:00~23:00 之内按 80/20 分, 夜里只算休息; 不足 1 小时的部分不计, 也不画. */
    fun focusSegments(day: LocalDate, at: LocalDateTime = now()): List<BehaviorSeg> {
        val d0 = day.atStartOfDay()
        val out = mutableListOf<BehaviorSeg>()
        for (o in sorted()) {
            val hours = recordedHoursOn(o, d0, at)
            if (hours <= 0) continue
            val ov = overlap(o, d0, at) ?: continue
            val a = ov.first
            val end = minOf(a.plusSec(hours * 3600.0), d0.plusDays(1))
            var cur = a
            while (cur < end) {
                val h0 = cur.floorHour()
                val h1 = h0.plusHours(1)
                val pe = if (end < h1) end else h1
                val sec = secondsBetween(cur, pe)
                if (sec <= 0) break
                val minutes = h0.hour * 60
                if (minutes in Cfg.OUTING_WINDOW_START until Cfg.OUTING_WINDOW_END) {
                    val study = sec * Cfg.OUTING_FOCUS_SHARE
                    val mid = cur.plusSec(study)
                    if (mid > cur) out.add(BehaviorSeg("study", cur, mid))
                    if (pe > mid) out.add(BehaviorSeg("break", mid, pe))
                } else {
                    out.add(BehaviorSeg("break", cur, pe))
                }
                cur = pe
            }
        }
        return out
    }

    fun studyHours(day: LocalDate, at: LocalDateTime = now()): Double =
        focusSegments(day, at).filter { it.kind == "study" }.sumOf { secondsBetween(it.start, it.end) } / 3600.0

    // ---- 联网 ----

    fun quotaFactor(day: LocalDate): Double {
        if (!enabled()) return 1.0
        val at = now()
        val d0 = day.atStartOfDay()
        return if (outings.any { overlap(it, d0, at) != null }) Cfg.OUTING_QUOTA_FACTOR else 1.0
    }

    fun cooldownFactor(at: LocalDateTime = now()): Double =
        if (enabled() && active(at) != null) Cfg.OUTING_COOLDOWN_FACTOR else 1.0

    // ---- 整点记录 ----

    fun hourAutoText(hourStart: LocalDateTime): String {
        if (!enabled()) return ""
        val since = modeSince
        val hourEnd = hourStart.plusHours(1)
        if (since != null && !hourEnd.isAfter(since)) return ""
        val at = now()
        for (o in outings) {
            if (o.start < hourEnd && o.effectiveEnd(at) > hourStart) {
                return settings.str("OUTING_TEXT").ifBlank { Cfg.OUTING_TEXT }
            }
        }
        return settings.str("REST_TEXT").ifBlank { Cfg.REST_TEXT }
    }

    // ---- 设备同步 ----

    fun doc(): Map<String, Any?> = outings.associateBy({ "o:${it.id}" }, { it.json() })

    /** 同步来的外出记录 (每一项单独比较, 由 SyncService 决定哪一份新). */
    fun merge(changes: Map<String, Any?>): Boolean {
        var changed = false
        for ((key, v) in changes) {
            if (!key.startsWith("o:")) continue
            val id = key.substring(2)
            val oldIndex = outings.indexOfFirst { it.id == id }
            val old = if (oldIndex >= 0) outings[oldIndex] else null
            if (v == null || v == JSONObject.NULL) {
                if (old != null) { outings.removeAt(oldIndex); changed = true }
                continue
            }
            val raw = v as? JSONObject ?: continue
            val new = clean(raw) ?: continue
            if (new.id != id) continue
            if (old != null) {
                // 已经结束 / 发过休息时间的不会被旧记录复活
                if (old.stopped != null && new.stopped == null) new.stopped = old.stopped
                new.rewarded = old.rewarded || new.rewarded
                if (new.owner.isEmpty()) new.owner = old.owner
                outings[oldIndex] = new
            } else {
                outings.add(new)
            }
            changed = true
        }
        if (changed) {
            outings.sortBy { it.start }
            normalize()
            save()
        }
        return changed
    }

    /** 两台设备同时开始: 只保留最晚开始的那次, 早的那次在后者开始时结束. */
    private fun normalize() {
        val activeNow = sorted().filter { it.stopped == null }
        for (o in activeNow.dropLast(1)) {
            val later = activeNow.filter { it !== o && !it.start.isBefore(o.start) }.maxByOrNull { it.start }
            val at = later?.start ?: o.end
            o.stopped = when {
                at < o.start -> o.start
                at > o.end -> o.end
                else -> at
            }
        }
    }

    fun clearHistory() {
        outings.clear()
        save()
    }

    fun erase() {
        outings.clear()
        file.delete()
    }
}
