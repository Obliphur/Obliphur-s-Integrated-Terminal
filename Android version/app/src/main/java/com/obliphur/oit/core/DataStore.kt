package com.obliphur.oit.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.LocalDate
import java.time.LocalDateTime

/** 应用使用时间线的一段: [s, e) 分钟都在用 app. */
class WinRun(var s: Int, var e: Int, val app: String)

fun parseRuns(a: JSONArray?): List<WinRun> = if (a == null) emptyList() else (0 until a.length()).mapNotNull { i ->
    val r = a.optJSONArray(i) ?: return@mapNotNull null
    if (r.length() < 3) null else WinRun(r.optInt(0), r.optInt(1), r.optString(2))
}

class WindowSource(val seconds: Map<String, Map<String, Double>>, val runs: Map<String, List<WinRun>>) {
    companion object {
        fun fromLedger(l: JSONObject): WindowSource {
            val sec = HashMap<String, Map<String, Double>>()
            l.optJSONObject("window")?.let { w ->
                for (ds in w.keys()) w.optJSONObject(ds)?.let { d -> sec[ds] = d.keys().asSequence().associateWith { d.optDouble(it, 0.0) } }
            }
            val runs = HashMap<String, List<WinRun>>()
            l.optJSONObject("window_runs")?.let { r -> for (ds in r.keys()) runs[ds] = parseRuns(r.optJSONArray(ds)) }
            return WindowSource(sec, runs)
        }
    }
}

fun sumDays(days: Map<String, Map<String, Double>>, prefix: String): Map<String, Double> {
    val out = HashMap<String, Double>()
    for ((ds, day) in days) if (ds.startsWith(prefix)) for ((n, s) in day) out[n] = (out[n] ?: 0.0) + s
    return out
}

/** 几台设备的应用时间合在一起 (同桌面版 data_manager.combine_window): sources 按优先级排 (母端在前).
 *  有时间线的日子按分钟合并, 同一分钟以排在前面的设备为准, 只有它是空白 (或没有记录) 时才看后面的;
 *  没有时间线的日子 (旧数据) 直接把秒数加起来. */
fun combineWindow(sources: List<WindowSource>, prefix: String): Map<String, Double> {
    val dates = sources.flatMap { it.seconds.keys + it.runs.keys }.filter { it.startsWith(prefix) }.toSet()
    val out = HashMap<String, Double>()
    for (d in dates) {
        val minutes = arrayOfNulls<String>(1440)
        for (src in sources) {
            val runs = src.runs[d]
            if (!runs.isNullOrEmpty()) {
                for (r in runs) for (m in maxOf(0, r.s) until minOf(1440, r.e)) {
                    val cur = minutes[m]
                    if (cur == null || (cur.isEmpty() && r.app.isNotEmpty())) minutes[m] = r.app
                }
            } else src.seconds[d]?.forEach { (n, s) -> out[n] = (out[n] ?: 0.0) + s }
        }
        for (a in minutes) if (a != null) out[a] = (out[a] ?: 0.0) + 60.0
    }
    return out
}

/** kind: study 专注 / break 休息 / idle 空闲 (共享专注中本机 OIT 被关掉的那一段; focus 是那段专注的开始时刻, MECHANICS 8.2) /
 *  sleep 睡眠 (单独的一种时间, MECHANICS 23). */
data class FocusSeg(val kind: String, val start: LocalDateTime, val end: LocalDateTime, val focus: String = "") {
    fun json(): JSONObject = JSONObject().put("k", kind).put("s", start.format(Fmt.TS)).put("e", end.format(Fmt.TS))
        .also { if (kind == "idle") it.put("f", focus) }
}

/**
 * 学习时长, 整点记录, 专注 / 休息时间段, 应用使用时间. 对应桌面版 data_manager.py.
 */
class DataStore(val dir: File, private val fillWindow: () -> Double, private val autoText: () -> String) {
    var resetting = false

    private val studyFile = File(dir, "study_log.json")
    private val hourFile = File(dir, "hour_log.json")
    private val focusFile = File(dir, "focus_log.json")
    private val windowFile = File(dir, "window_usage.json")

    val study = HashMap<String, Double>()
    val hourDays = HashMap<String, HashMap<String, String>>()
    /** 合约的细化规范 (MECHANICS 18.4): 一小时分成两个半小时各填一格 {日期: {小时: [前半, 后半]}};
     *  hourDays 里那一小时是两格用 "/" 连起来的内容, 统计照常按它算. */
    val hourHalves = HashMap<String, HashMap<String, Array<String>>>()
    var hourCursor: LocalDateTime? = null
    var hourEnabled = true
    var todayDate: String = LocalDate.now().key()
    /** 正在等待填写的那一小时 (开始时刻). */
    var promptSlot: LocalDateTime? = null
    /** 待填写的那一格有几分钟 (细化规范: 30). */
    var promptLen = 60
    val focus = mutableListOf<FocusSeg>()
    val windowDays = HashMap<String, HashMap<String, Double>>()
    /** 设备同步: 其他设备的账 (MECHANICS 15), 显示合计用. */
    var peers: () -> List<JSONObject> = { emptyList() }
    var peersById: () -> Map<String, JSONObject> = { emptyMap() }
    /** 有设备离线的时间段 (毫秒): 正在进行的那段也按它画成空白. */
    var offlineSpans: (Long, Long) -> List<Pair<Long, Long>> = { _, _ -> emptyList() }
    var myId: () -> String = { "" }
    var motherId: () -> String? = { null }
    val windowRuns = HashMap<String, MutableList<WinRun>>()
    private var windowDirty = false
    /** 行为模式的外出记录 (MECHANICS 21): 不写进 study_log / focus_log, 显示时叠加. */
    var extraStudy: (String) -> Double = { 0.0 }
    var extraDays: () -> Set<String> = { emptySet() }
    var extraFocus: (LocalDate) -> List<BehaviorSeg> = { emptyList() }
    /** 某一小时的整点记录自动填充内容; "" = 用普通的 AUTO_TEXT. */
    var hourAutoText: (LocalDateTime) -> String = { "" }
    /** 正在睡眠时, 开始睡眠的时刻 (睡眠期间的小时自动记成"睡眠"). */
    var sleepLive: () -> LocalDateTime? = { null }

    init {
        dir.mkdirs()
        loadStudy()
        pruneStudy()
        loadHours()
        loadFocus()
        loadWindows()
    }

    // ---- 学习时长 ----

    private fun loadStudy() {
        study.clear()
        val o = Store.readObj(studyFile) ?: return
        for (k in o.keys()) study[k] = o.dbl(k)
    }

    private fun saveStudy() {
        if (resetting) return
        Store.write(studyFile, JSONObject(study as Map<*, *>))
    }

    private fun studyAll(day: String): Double =
        (study[day] ?: 0.0) + peers().sumOf { it.optJSONObject("study")?.optDouble(day, 0.0)?.takeIf { v -> !v.isNaN() } ?: 0.0 } +
            (runCatching { extraStudy(day) }.getOrDefault(0.0))

    fun todayHours() = studyAll(todayDate)

    /** 每天的学习时长 (所有设备合计), 成就判定用. */
    fun studyByDay(): Map<String, Double> {
        val days = HashSet(study.keys)
        for (p in peers()) p.optJSONObject("study")?.keys()?.forEach { days.add(it) }
        days.addAll(runCatching { extraDays() }.getOrDefault(emptySet()))
        return days.associateWith { studyAll(it) }
    }
    fun yesterdayHours() = studyAll(LocalDate.parse(todayDate).minusDays(1).key())

    /** 一段专注计入学习时长; 跨过零点的按时间比例拆到两天. */
    fun addStudySession(start: LocalDateTime, end: LocalDateTime, minutes: Double) {
        if (minutes <= 0) return
        val sd = start.toLocalDate().key()
        val ed = end.toLocalDate().key()
        if (sd == ed) {
            study[sd] = (study[sd] ?: 0.0) + minutes / 60.0
        } else {
            val midnight = start.floorDay().plusDays(1)
            val first = secondsBetween(start, midnight)
            val second = secondsBetween(midnight, end)
            val total = first + second
            if (total > 0) {
                study[sd] = (study[sd] ?: 0.0) + minutes * (first / total) / 60.0
                study[ed] = (study[ed] ?: 0.0) + minutes * (second / total) / 60.0
            } else {
                study[sd] = (study[sd] ?: 0.0) + minutes / 60.0
            }
        }
        saveStudy()
    }

    fun pruneStudy() {
        val (a, b) = monthPrefixes()
        if (study.keys.removeAll { it.take(7) != a && it.take(7) != b }) saveStudy()
    }

    fun monthStudy(prefix: String): Map<String, Double> {
        val days = study.keys.filter { it.startsWith(prefix) }.toMutableSet()
        for (l in peers()) l.optJSONObject("study")?.keys()?.forEach { if (it.startsWith(prefix)) days.add(it) }
        runCatching { extraDays() }.getOrDefault(emptySet()).forEach { if (it.startsWith(prefix)) days.add(it) }
        return days.associateWith { studyAll(it) }
    }

    /** 其他设备的专注 / 休息时间段. */
    private fun peerFocus(): List<FocusSeg> = peers().flatMap { l ->
        val arr = l.optJSONArray("focus") ?: return@flatMap emptyList()
        arr.objects().mapNotNull { r ->
            val s = runCatching { LocalDateTime.parse(r.optString("s"), Fmt.TS) }.getOrNull()
            val e = runCatching { LocalDateTime.parse(r.optString("e"), Fmt.TS) }.getOrNull()
            if (s != null && e != null && e.isAfter(s)) FocusSeg(r.optString("k"), s, e, r.optString("f")) else null
        }
    }

    // ---- 专注计时: 每一段专注 / 休息的起止时刻, 只留最近 7 天 ----

    private fun focusFloor() = LocalDateTime.now().floorDay().minusDays(6)

    private fun loadFocus() {
        focus.clear()
        val arr = Store.readObj(focusFile)?.optJSONArray("sessions") ?: JSONArray()
        for (r in arr.objects()) {
            val s = runCatching { LocalDateTime.parse(r.optString("s"), Fmt.TS) }.getOrNull() ?: continue
            val e = runCatching { LocalDateTime.parse(r.optString("e"), Fmt.TS) }.getOrNull() ?: continue
            val k = r.optString("k")
            if ((k == "study" || k == "break" || k == "idle" || k == "sleep") && e.isAfter(s)) focus.add(FocusSeg(k, s, e, r.optString("f")))
        }
        focus.removeAll { !it.end.isAfter(focusFloor()) }
    }

    private fun saveFocus() {
        if (resetting) return
        focus.removeAll { !it.end.isAfter(focusFloor()) }
        Store.write(focusFile, JSONObject().put("sessions", JSONArray(focus.map { it.json() })))
    }

    fun addFocusSession(kind: String, start: LocalDateTime?, end: LocalDateTime?, focusStart: LocalDateTime? = null) {
        if (start == null || end == null || secondsBetween(start, end) < 1) return
        focus.add(FocusSeg(kind, start.withNano(0), end.withNano(0), focusStart?.withNano(0)?.format(Fmt.TS) ?: ""))
        saveFocus()
    }

    /** [start, end] 里有没有别的设备记下的专注 / 休息 (本机关机时有没有别的设备在用, MECHANICS 15.4). */
    fun peerActive(start: LocalDateTime, end: LocalDateTime): Boolean =
        peerFocus().any { (it.kind == "study" || it.kind == "break") && it.start.isBefore(end) && start.isBefore(it.end) }

    /** 某一天四种时间各多少秒 (只有最近 7 天有专注计时记录): study 专注 / break 休息 / sleep 睡眠 / idle 空闲.
     *  空闲 = 这一天 (今天只算到此刻) 里不属于前三种的时间. live: 正在进行的一段 (kind, start), kind 可以是 sleep. 同 data_manager.day_kinds. */
    fun dayKinds(day: LocalDate, live: Pair<String, LocalDateTime>?, now: LocalDateTime = LocalDateTime.now()): Map<String, Double> {
        val d0 = day.atStartOfDay()
        val limit = minOf(d0.plusDays(1), now)
        val span = maxOf(0.0, secondsBetween(d0, limit))
        val hours = focusHours(day, live?.takeIf { it.first == "study" || it.first == "break" })
        val study = hours.sumOf { it.first }
        val rest = hours.sumOf { it.second }
        val spans = (focus + peerFocus()).filter { it.kind == "sleep" }.map { it.start to it.end }.toMutableList()
        if (live != null && live.first == "sleep") spans.add(live.second to now)
        val sleep = mergeSpans(spans).sumOf { (a, b) -> maxOf(0.0, secondsBetween(maxOf(a, d0), minOf(b, limit))) }
        return mapOf("study" to study, "break" to rest, "sleep" to sleep, "idle" to maxOf(0.0, span - study - rest - sleep))
    }

    private fun mergeSpans(spans: List<Pair<LocalDateTime, LocalDateTime>>): List<Pair<LocalDateTime, LocalDateTime>> {
        val out = mutableListOf<Pair<LocalDateTime, LocalDateTime>>()
        for ((a, b) in spans.sortedBy { it.first }) {
            val last = out.lastOrNull()
            if (last != null && !a.isAfter(last.second)) out[out.size - 1] = last.first to maxOf(last.second, b) else out.add(a to b)
        }
        return out
    }

    /** [start, end] 里有多少秒在睡眠 (本机和其他设备记下的睡眠, 加上正在进行的这一段). */
    fun sleepSeconds(start: LocalDateTime, end: LocalDateTime): Double {
        val spans = (focus + peerFocus()).filter { it.kind == "sleep" }.map { it.start to it.end }.toMutableList()
        sleepLive()?.let { spans.add(it to maxOf(it, LocalDateTime.now())) }
        return mergeSpans(spans).sumOf { (a, b) -> maxOf(0.0, secondsBetween(maxOf(a, start), minOf(b, end))) }
    }

    /** 某一天 24 个小时各自的 (专注秒数, 休息秒数). live: 正在进行的一段 (kind, start). */
    fun focusHours(day: LocalDate, live: Pair<String, LocalDateTime>?): List<Pair<Double, Double>> {
        val d0 = day.atStartOfDay()
        val st = DoubleArray(24)
        val rs = DoubleArray(24)
        val all = (focus + peerFocus()).toMutableList()
        if (live != null && live.first != "sleep") {
            val now = LocalDateTime.now()
            all.add(FocusSeg(live.first, live.second, now))
            // 还没记下来的这段专注: 有设备离线的部分现在就画成空白 (休息照常)
            val z = java.time.ZoneId.systemDefault()
            if (live.first != "break") runCatching {
                for ((a, b) in offlineSpans(live.second.atZone(z).toInstant().toEpochMilli(), now.atZone(z).toInstant().toEpochMilli()))
                    all.add(FocusSeg("idle", LocalDateTime.ofInstant(java.time.Instant.ofEpochMilli(a), z),
                        LocalDateTime.ofInstant(java.time.Instant.ofEpochMilli(b), z), live.second.withNano(0).format(Fmt.TS)))
            }
        }
        // 行为模式的外出: 8:2 填充出来的专注 / 休息, 和普通的段落一起画 (MECHANICS 21)
        runCatching { extraFocus(day) }.getOrDefault(emptyList()).forEach {
            all.add(FocusSeg(it.kind, it.start, it.end))
        }
        // 空闲的优先级最高: 同一段专注 (开始时刻相同) 里和空闲重叠的部分画成空白 (哪台设备记的都一样); 休息照常
        val idle = all.filter { it.kind == "idle" }.groupBy { it.focus }
        val segs = all.filter { it.kind == "study" || it.kind == "break" }.flatMap { s ->      // 睡眠不画进专注计时条形图 (留白)
            if (s.kind == "break") listOf(s) else subtract(s, idle[s.start.withNano(0).format(Fmt.TS)] ?: emptyList())
        }
        for (s in segs) {
            if (!s.end.isAfter(d0) || !s.start.isBefore(d0.plusDays(1))) continue
            for (h in 0 until 24) {
                val h0 = d0.plusHours(h.toLong())
                val h1 = h0.plusHours(1)
                val a = if (s.start.isAfter(h0)) s.start else h0
                val b = if (s.end.isBefore(h1)) s.end else h1
                val sec = secondsBetween(a, b)
                if (sec > 0) if (s.kind == "study") st[h] += sec else rs[h] += sec
            }
        }
        // 理论上不会重叠; 万一重叠, 专注优先, 一小时最多 3600 秒
        return (0 until 24).map {
            val a = minOf(3600.0, st[it])
            a to minOf(3600.0 - a, rs[it])
        }
    }

    /** s 去掉 holes 里各段之后剩下的部分. */
    // ---- 断网 (Adapter) 期间的重叠 (MECHANICS 15.5) ----

    /** 一台设备用 Adapter 断网 (不同步) 的这段 [start, end] 里, 本机和其他设备各自记的专注计时有重叠时:
     *  按 休息 > 专注 > 空闲 叠加 —— 本机的记录被别的设备优先级更高的记录盖住的部分删掉;
     *  一样的只由断网的那台设备删 (tieMine; 手机不会是断网的那台). 每台设备只改自己的记录, 删掉的专注同时从学习时长里扣掉.
     *  priority: 对方刚从自由学习的外出回来 (MECHANICS 21.6), 它的记录优先 —— 不看 休息 > 专注 > 空闲, 本机和它重叠的部分都删掉.
     *  返回 {kind: 删掉的秒数}. 同 data_manager.resolve_overlap. */
    fun resolveOverlap(peerRecs: List<JSONObject>, start: LocalDateTime, end: LocalDateTime, tieMine: Boolean,
                       priority: Boolean = false): Map<String, Double> {
        val out = mutableMapOf("study" to 0.0, "break" to 0.0, "idle" to 0.0)
        if (!end.isAfter(start)) return out
        val rank = mapOf("break" to 3, "study" to 2, "idle" to 1)
        val peers = peerRecs.mapNotNull { r ->
            val a = runCatching { LocalDateTime.parse(r.optString("s"), Fmt.TS) }.getOrNull() ?: return@mapNotNull null
            val b = runCatching { LocalDateTime.parse(r.optString("e"), Fmt.TS) }.getOrNull() ?: return@mapNotNull null
            val k = r.optString("k")
            val x = maxOf(a, start)
            val y = minOf(b, end)
            if (k in rank && y.isAfter(x)) FocusSeg(k, x, y) else null
        }
        if (peers.isEmpty()) return out
        val kept = mutableListOf<FocusSeg>()
        val cut = HashMap<String, Double>()
        for (r in focus) {
            val mine = rank[r.kind] ?: 0
            val cover = peers.filter { val pk = rank[it.kind]!!; priority || pk > mine || (pk == mine && tieMine) }
            val pieces = if (cover.isEmpty()) listOf(r) else subtract(r, cover)
            val removed = secondsBetween(r.start, r.end) - pieces.sumOf { secondsBetween(it.start, it.end) }
            if (removed <= 0) { kept.add(r); continue }
            out[r.kind] = (out[r.kind] ?: 0.0) + removed
            if (r.kind == "study") for (gone in subtract(r, pieces)) {       // 被删掉的那几段, 按天扣学习时长
                var x = gone.start
                while (x < gone.end) {
                    val nxt = minOf(gone.end, x.floorDay().plusDays(1))
                    val ds = x.toLocalDate().key()
                    cut[ds] = (cut[ds] ?: 0.0) + secondsBetween(x, nxt)
                    x = nxt
                }
            }
            for (p in pieces) if (secondsBetween(p.start, p.end) >= 1) kept.add(FocusSeg(r.kind, p.start, p.end, r.focus))
        }
        focus.clear()
        focus.addAll(kept)
        for ((ds, sec) in cut) study[ds]?.let { study[ds] = maxOf(0.0, it - sec / 3600.0) }
        saveFocus()
        if (cut.isNotEmpty()) saveStudy()
        return out
    }

    private fun subtract(s: FocusSeg, holes: List<FocusSeg>): List<FocusSeg> {
        var pieces = listOf(s.start to s.end)
        for (h in holes) pieces = pieces.flatMap { (a, b) ->
            if (!h.end.isAfter(a) || !h.start.isBefore(b)) listOf(a to b)
            else listOfNotNull(if (h.start.isAfter(a)) a to h.start else null, if (h.end.isBefore(b)) h.end to b else null)
        }
        return pieces.map { FocusSeg(s.kind, it.first, it.second) }
    }

    // ---- 应用使用时间: {日期: {包名: 秒}}, "" 是空白 ----
    // 同一份数据按分钟的时间线 windowRuns: {日期: [[开始分钟, 结束分钟, 包名], …]};
    // 统计页看"全部设备"时用它判断几台设备是不是同时在用 (重叠的分钟以母端为准)

    private fun loadWindows() {
        windowDays.clear()
        windowRuns.clear()
        val o = Store.readObj(windowFile) ?: return
        val days = o.optJSONObject("days") ?: JSONObject()
        for (ds in days.keys()) {
            val d = days.optJSONObject(ds) ?: continue
            val m = HashMap<String, Double>()
            for (n in d.keys()) {
                val v = d.dbl(n)
                if (v > 0) m[n] = v
            }
            if (m.isNotEmpty()) windowDays[ds] = m
        }
        o.optJSONObject("runs")?.let { r -> for (ds in r.keys()) windowRuns[ds] = parseRuns(r.optJSONArray(ds)).toMutableList() }
        pruneWindows()
    }

    private fun pruneWindows() {
        val (a, b) = monthPrefixes()
        windowDays.keys.removeAll { it.take(7) != a && it.take(7) != b }
        windowRuns.keys.removeAll { it.take(7) != a && it.take(7) != b }
    }

    /** 把截止到 end 的这 seconds 秒记给 name; 跨过零点的部分记到前一天. */
    fun addWindowTime(name: String, seconds: Double, end: LocalDateTime = LocalDateTime.now()) {
        if (seconds <= 0) return
        var start = end.minusNanos((seconds * 1e9).toLong())
        while (start.isBefore(end)) {
            val next = start.floorDay().plusDays(1)
            val cut = if (end.isBefore(next)) end else next
            val day = windowDays.getOrPut(start.toLocalDate().key()) { HashMap() }
            day[name] = (day[name] ?: 0.0) + secondsBetween(start, cut)
            start = cut
        }
        noteRun(name, end.minusNanos((seconds / 2 * 1e9).toLong()))
        windowDirty = true
    }

    /** 把 at 所在的这一分钟记给 name (同一分钟里换了应用, 记给最后一个). */
    fun noteRun(name: String, at: LocalDateTime) {
        val runs = windowRuns.getOrPut(at.toLocalDate().key()) { mutableListOf() }
        val m = at.hour * 60 + at.minute
        val last = runs.lastOrNull()
        if (last != null) {
            if (last.app == name && m <= last.e) { last.e = maxOf(last.e, m + 1); return }
            if (m < last.e - 1) return                // 时间往回走了 (改过系统时间): 不记
            if (m == last.e - 1) {                    // 这一分钟刚记给了别的应用: 改记给最新的
                if (last.s >= last.e - 1) runs.removeAt(runs.size - 1) else last.e -= 1
                val prev = runs.lastOrNull()
                if (prev != null && prev.app == name && prev.e == m) { prev.e = m + 1; return }
            }
        }
        runs.add(WinRun(m, m + 1, name))
    }

    /** 统计页可选的设备: (键, 名字); 键 "self" 是本机. */
    fun windowDevices(): List<Pair<String, String>> =
        listOf("self" to "") + peersById().map { (id, l) -> id to l.optString("name", id) }

    /** 某个月每个应用的前台时间 (秒); 键 "" 是空白.
     *  device: "self" 本机 / 某台设备的 id / "all" 全部设备 (同一分钟几台设备都在用时以母端为准). */
    fun windowSeconds(prefix: String, device: String = "all"): Map<String, Double> {
        val peers = peersById()
        if (device == "self" || peers.isEmpty()) return sumDays(windowDays, prefix)
        peers[device]?.let { return sumDays(WindowSource.fromLedger(it).seconds, prefix) }
        val mine = myId()
        val mother = motherId()
        val ids = (listOf(mine) + peers.keys).sortedBy { if (it == mother) 0 else 1 }
        return combineWindow(ids.map { if (it == mine) WindowSource(windowDays, windowRuns) else WindowSource.fromLedger(peers[it]!!) }, prefix)
    }

    fun saveWindows(force: Boolean = false) {
        if (resetting || !(windowDirty || force)) return
        pruneWindows()
        val days = JSONObject()
        for ((ds, day) in windowDays) {
            val o = JSONObject()
            for ((n, s) in day) o.put(n, Math.round(s * 10) / 10.0)
            days.put(ds, o)
        }
        if (Store.write(windowFile, JSONObject().put("days", days).put("runs", runsJson()))) windowDirty = false
    }

    fun runsJson(): JSONObject {
        val runs = JSONObject()
        for ((ds, list) in windowRuns) runs.put(ds, JSONArray(list.map { JSONArray().put(it.s).put(it.e).put(it.app) }))
        return runs
    }

    // ---- 整点记录 ----

    private fun loadHours() {
        hourDays.clear()
        val o = Store.readObj(hourFile)
        if (o != null) {
            val days = o.optJSONObject("days") ?: JSONObject()
            for (ds in days.keys()) {
                val d = days.optJSONObject(ds) ?: continue
                val m = HashMap<String, String>()
                for (h in d.keys()) m[h] = d.optString(h)
                hourDays[ds] = m
            }
            val halves = o.optJSONObject("halves") ?: JSONObject()
            for (ds in halves.keys()) {
                val d = halves.optJSONObject(ds) ?: continue
                val m = HashMap<String, Array<String>>()
                for (h in d.keys()) d.optJSONArray(h)?.let { if (it.length() == 2) m[h] = arrayOf(it.optString(0), it.optString(1)) }
                hourHalves[ds] = m
            }
            hourCursor = parseIso(o.optString("cursor", ""))
        }
        pruneHours()
    }

    private fun pruneHours() {
        val (a, b) = monthPrefixes()
        hourDays.keys.removeAll { it.take(7) != a && it.take(7) != b }
        hourHalves.keys.removeAll { it.take(7) != a && it.take(7) != b }
    }

    fun saveHours() {
        if (resetting) return
        pruneHours()
        val days = JSONObject()
        for ((ds, d) in hourDays) days.put(ds, JSONObject(d as Map<*, *>))
        val halves = JSONObject()
        for ((ds, d) in hourHalves) {
            val o = JSONObject()
            for ((h, pair) in d) o.put(h, JSONArray(pair.toList()))
            halves.put(ds, o)
        }
        Store.write(hourFile, JSONObject().put("enabled", hourEnabled)
            .put("cursor", hourCursor?.iso() ?: JSONObject.NULL).put("days", days).put("halves", halves))
    }

    fun hourActivitySeconds(prefix: String): Map<String, Double> =
        tallyItems(hourDays.filterKeys { it.startsWith(prefix) }.values.flatMap { d -> d.values.map { it to 3600.0 } })

    fun applyHourEnabled(enabled: Boolean) {
        if (enabled == hourEnabled) return
        hourEnabled = enabled
        if (enabled) hourCursor = LocalDateTime.now().floorHour() else promptSlot = null
        saveHours()
    }

    fun hourText(date: String, hour: Int): String = hourDays[date]?.get(hour.toString()) ?: ""

    fun setHourText(date: String, hour: Int, text: String) {
        hourHalves[date]?.remove(hour.toString())          // 整小时一起改了: 不再分两格
        val d = hourDays.getOrPut(date) { HashMap() }
        if (text.isNotEmpty()) d[hour.toString()] = text else d.remove(hour.toString())
        saveHours()
    }

    // ---- 细化规范: 半小时一格 ----

    val fillSeconds get() = fillWindow()
    /** 细化规范的半小时格: 补填时限只有 30 min (补填时限设得更短时照设置). */
    val halfFillSeconds get() = minOf(fillWindow(), 1800.0)
    /** 非睡眠时间的自动记录内容 (默认 分心 / Distracted, 跟随界面语言). */
    val autoValue get() = I18n.autoName(autoText())
    val sleepText get() = I18n.autoName("Sleeping")

    /** 这一小时的超时自动记录内容: 行为模式优先; 睡眠占了这一小时一半以上记成"睡眠"; 否则用全局的那一项 (默认 分心). */
    fun autoTextFor(hourStart: LocalDateTime): String {
        val behavior = runCatching { hourAutoText(hourStart) }.getOrDefault("")
        if (behavior.isNotBlank()) return behavior
        if (runCatching { sleepSeconds(hourStart, hourStart.plusHours(1)) }.getOrDefault(0.0) >= 1800.0) return sleepText
        return autoValue
    }

    /** 切换语言时: 整点记录里 睡眠 / Sleeping, 分心 / Distracted 这些默认的内容换成现在的语言, 同一项合并到一起. */
    fun localizeAutoTexts(): Boolean {
        var changed = false
        for (day in hourDays.values) for (h in day.keys.toList()) {
            val t = day[h] ?: continue
            val n = I18n.autoNamesIn(t)
            if (n != t) { day[h] = n; changed = true }
        }
        for (day in hourHalves.values) for (h in day.keys.toList()) {
            val pair = day[h] ?: continue
            val n = Array(pair.size) { I18n.autoNamesIn(pair[it]) }
            if (!n.contentEquals(pair)) { day[h] = n; changed = true }
        }
        if (changed) saveHours()
        return changed
    }

    /** 起床后: [start, end] 里已经自动记成非睡眠内容的小时 (睡眠记录晚到时) 改成"睡眠". */
    fun relabelSleep(start: LocalDateTime, end: LocalDateTime): Boolean {
        var changed = false
        var h = start.floorHour()
        while (h.isBefore(end)) {
            val ds = h.toLocalDate().key()
            val cur = hourDays[ds]?.get(h.hour.toString())
            if (!cur.isNullOrEmpty() && cur == autoValue && sleepSeconds(h, h.plusHours(1)) >= 1800.0) {
                hourDays[ds]!![h.hour.toString()] = sleepText
                changed = true
            }
            h = h.plusHours(1)
        }
        if (changed) saveHours()
        return changed
    }

    /** 整点记录的键 ("日期/小时" 或 "日期/小时/1") → 自动记录内容. */
    fun autoTextForKey(key: String): String {
        val parts = key.split("/")
        return runCatching {
            LocalDate.parse(parts[0]).atStartOfDay().plusHours((parts[1].toInt()).toLong())
        }.getOrNull()?.let { autoTextFor(it) } ?: autoValue
    }

    /** i = 0 前半小时, 1 后半小时. 还没分开填过的小时: 前半显示整小时的内容. */
    fun halfText(date: String, hour: Int, i: Int): String {
        val pair = hourHalves[date]?.get(hour.toString())
        if (pair != null) return pair[i]
        return if (i == 0) hourText(date, hour) else ""
    }

    fun setHalfText(date: String, hour: Int, i: Int, text: String, save: Boolean = true) {
        val pair = arrayOf(halfText(date, hour, 0), halfText(date, hour, 1))
        pair[i] = text
        val day = hourHalves.getOrPut(date) { HashMap() }
        if (pair.any { it.isNotEmpty() }) day[hour.toString()] = pair else day.remove(hour.toString())
        // 整小时的内容 = 两个半小时用 "/" 连起来 (一样的只写一次)
        val joined = pair.filter { it.isNotEmpty() }.distinct().joinToString("/")
        val d = hourDays.getOrPut(date) { HashMap() }
        if (joined.isNotEmpty()) d[hour.toString()] = joined else d.remove(hour.toString())
        if (save) saveHours()
    }

    /** 同步 / 合约用的键: "日期/小时" 整小时, "日期/小时/1" "日期/小时/2" 两个半小时. */
    fun unitText(key: String): String {
        val p = key.split("/")
        val h = p.getOrNull(1)?.toIntOrNull() ?: return ""
        return if (p.size == 3) halfText(p[0], h, (p[2].toIntOrNull() ?: 1) - 1) else hourText(p[0], h)
    }

    /** 细化规范的半小时过了补填时限还空着: 记为超时自动记录的内容. */
    fun finalizeHalves(units: List<Triple<String, LocalDateTime, LocalDateTime>>, now: LocalDateTime = LocalDateTime.now()) {
        if (!hourEnabled) return
        var changed = false
        for ((key, _, b) in units) {
            val p = key.split("/")
            if (p.size != 3 || b.plusSec(halfFillSeconds).isAfter(now)) continue
            val h = p[1].toIntOrNull() ?: continue
            val i = (p[2].toIntOrNull() ?: continue) - 1
            if (halfText(p[0], h, i).isEmpty()) {
                val hour0 = runCatching { LocalDate.parse(p[0]).atStartOfDay().plusHours(h.toLong()) }.getOrNull()
                setHalfText(p[0], h, i, hour0?.let { autoTextFor(it) } ?: autoValue, save = false)
                changed = true
            }
        }
        if (changed) saveHours()
    }

    /** 超过补填时限还没填写的小时自动记录; 关闭期间的小时最早只补到昨天 23:00. */
    fun finalizeHours() {
        val now = LocalDateTime.now()
        val floor = now.floorHour()
        val today0 = now.floorDay()
        val cur = hourCursor
        if (cur == null) {
            hourCursor = floor
            saveHours()
            return
        }
        var c = if (cur.isBefore(today0.minusHours(1))) today0.minusHours(1) else cur
        if (!hourEnabled) {
            if (c.isBefore(floor)) c = floor
            if (c != cur) { hourCursor = c; saveHours() }
            return
        }
        var changed = c != cur
        while (!c.plusHours(1).plusSec(fillWindow()).isAfter(now)) {
            val ds = c.toLocalDate().key()
            if (hourText(ds, c.hour).isEmpty()) hourDays.getOrPut(ds) { HashMap() }[c.hour.toString()] = autoTextFor(c)
            c = c.plusHours(1)
            changed = true
        }
        hourCursor = c
        if (changed) saveHours()
    }

    fun awaitingSlot(): LocalDateTime? {
        if (!hourEnabled) return null
        val s = promptSlot ?: return null
        val text = if (promptLen == 30) halfText(s.toLocalDate().key(), s.hour, if (s.minute >= 30) 1 else 0)
            else hourText(s.toLocalDate().key(), s.hour)
        val fill = if (promptLen == 30) halfFillSeconds else fillWindow()
        if (!LocalDateTime.now().isBefore(s.plusMinutes(promptLen.toLong()).plusSec(fill)) || text.isNotEmpty()) {
            promptSlot = null
            return null
        }
        return s
    }

    /** 设备同步: 子端第一次绑定时, 自己的历史换成母端的. */
    fun clearHistoryForSync() {
        study.clear(); saveStudy()
        focus.clear(); saveFocus()
        windowDays.clear(); windowRuns.clear(); saveWindows(force = true)
    }

    /** 重置: 删除本程序留下的全部数据. */
    fun eraseAll() {
        dir.listFiles()?.forEach { it.deleteRecursively() }
    }
}
