package com.obliphur.oit.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.math.BigInteger
import java.security.MessageDigest
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.temporal.ChronoUnit

/** 日程引擎 (1.0.355; 同桌面版 routine.py): 读 <人设>_routine.txt, 按"持续时间"一项接一项地算出对方每天的日程, 并生成对方的 OIT 数据.
 *  每天的波动由 (人设 + 日期) 的 sha256 决定, 电脑和手机算出来一模一样. 所有数据实时算, 只给已经发生的部分; 算过的存下来 (RoutineStore). */

/** 日程里的一段 (绝对时间). kind = focus / rest / idle / sleep; loop = 第几个循环段生成的 (-1 没有); gap = 波动留下的空档; n = 循环的每轮专注分钟. */
data class RtSeg(val kind: String, val start: LocalDateTime, val end: LocalDateTime, val label: String,
                 val loop: Int = -1, val gap: Boolean = false, val n: Double = 0.0)

class RtItem(val isLoop: Boolean, val dur: Double, val label: String = "", val focus: Double = 0.0)

/** 列表 {} 里的一项: pct = null 没写概率 (平分剩下的); x = 写成 X% (随聊天变). */
class RtListItem(val name: String, val pct: Double?, val x: Boolean)

class RoutineSpec {
    var focusLabel = ""
    var start: Int? = null
    val items = ArrayList<RtItem>()
    val fluct = HashMap<String, Pair<Double, Double>>().apply { for (k in Routine.FLUCT_KEYS) put(k.first, 0.0 to 0.0) }
    val lists = LinkedHashMap<String, List<RtListItem>>()
    val base = HashSet<String>()
    var devices: List<String> = emptyList()
    val windows = HashMap<Int, String>()
    var diff = 20 to 30
    var skipTerms: List<String> = emptyList()
    var badTerms: List<String> = emptyList()
    internal val plans = HashMap<String, List<RtSeg>>()
    fun ok() = start != null && items.isNotEmpty()
}

object Routine {
    const val KEEP_DAYS = 35
    const val BACKFILL_DAYS = 7
    const val SYNC_MAX_DAYS = 14
    const val CHAT_FULL = 50.0
    const val CHAT_FULL_MSGS = 20
    const val BASE_FLUCT = 0.02
    const val NET_SHARE = 0.25
    const val NET_QUOTA_S = 2 * 3600.0
    const val CONTRACT_DAY_P = 0.3
    val FLUCT_KEYS = listOf("focus_start" to "下一个专注开始节点波动", "focus_end" to "下一个专注结束节点波动",
        "sleep_start" to "睡眠开始时间波动", "sleep_end" to "睡眠结束时间波动")

    private const val NUM = """(\d+(?:\.\d+)?)"""
    private const val UNIT = """(min|mins|分钟|h|hr|hrs|小时)"""
    private val DUR_RE = Regex("""^[ \t]*(?:[-*•][ \t]*)?$NUM[ \t]*$UNIT[ \t]*[:：][ \t]*(\S[^\n]*)$""", setOf(RegexOption.IGNORE_CASE, RegexOption.MULTILINE))
    private val LOOP_RE = Regex("""^[ \t]*(?:[-*•][ \t]*)?[\[［][ \t]*(?:循环|loop|cycle|repeat)[ \t]*$NUM[ \t]*$UNIT?[ \t]*[\]］][ \t]*(?:[\[［][ \t]*$NUM[ \t]*$UNIT?[ \t]*[\]］])?""",
        setOf(RegexOption.IGNORE_CASE, RegexOption.MULTILINE))
    private val START_RE = Regex("""初始起点[ \t]*[:：][ \t]*(\d{1,2})[:：](\d{2})""")
    private val FOCUS_RE = Regex("""^[ \t]*专注时段[ \t]*[:：][ \t]*(\S[^\n]*)$""", RegexOption.MULTILINE)
    private const val RANGE = """[\[［][ \t]*([+-]?\d+(?:\.\d+)?)[ \t]*[,，~～][ \t]*([+-]?\d+(?:\.\d+)?)[ \t]*[\]］]"""
    private val LIST_RE = Regex("""^[ \t]*(?:[-*•][ \t]*)?([^\n{}]+?)[ \t]*[:：][ \t]*[{｛]([^{}｛｝\n]*)[}｝][ \t]*(\([ \t]*基准数据[ \t]*\)|（[ \t]*基准数据[ \t]*）)?""", RegexOption.MULTILINE)
    private val DEV_WIN_RE = Regex("""^设备[-－](\d+)[ \t]*[:：][ \t]*窗口使用时间$""")
    private val ITEM_RE = Regex("""^[ \t]*(X|x|\d+(?:\.\d+)?)[ \t]*[%％][ \t]*[:：][ \t]*(\S.*)$""")
    private val DIFF_RE = Regex("""合约记录难度[ \t]*[:：][ \t]*$RANGE""")

    // ══════════════ 确定性随机 ══════════════
    fun u01(key: String): Double {
        val h = MessageDigest.getInstance("SHA-256").digest(key.toByteArray(Charsets.UTF_8))
        return BigInteger(1, h.copyOf(8)).toDouble() / 18446744073709551616.0
    }

    /** [lo, hi] 里的整数 (两端都可能取到). */
    fun rint(key: String, lo0: Double, hi0: Double): Int {
        val a = Math.rint(minOf(lo0, hi0)).toInt()
        val b = Math.rint(maxOf(lo0, hi0)).toInt()
        return a + minOf(b - a, (u01(key) * (b - a + 1)).toInt())
    }

    // ══════════════ 解析 ══════════════
    private fun minutes(v: Double, unit: String): Double {
        val u = unit.ifEmpty { "min" }.lowercase()
        return v * (if (u.startsWith("h") || u == "小时") 60.0 else 1.0)
    }

    fun parseList(body: String): List<RtListItem> {
        val out = ArrayList<RtListItem>()
        for (raw0 in body.split(',', '，')) {
            val raw = raw0.trim()
            if (raw.isEmpty()) continue
            val m = ITEM_RE.find(raw)
            if (m != null) {
                val x = m.groupValues[1] == "X" || m.groupValues[1] == "x"
                out.add(RtListItem(m.groupValues[2].trim(), if (x) null else m.groupValues[1].toDouble(), x))
            } else out.add(RtListItem(raw, null, false))
        }
        return out
    }

    /** 没写概率的项按其他项剩下的概率平分 (规则 IV). xValue: 写成 X% 的项此刻的概率. → (名字, 概率%). */
    fun resolveList(items: List<RtListItem>, xValue: Double? = null): List<Pair<String, Double>> {
        val fixed = items.filter { it.x || it.pct != null }.sumOf { if (it.x) (xValue ?: 0.0) else (it.pct ?: 0.0) }
        val free = items.count { !it.x && it.pct == null }
        val share = if (free > 0) maxOf(0.0, 100.0 - fixed) / free else 0.0
        return items.map { it.name to maxOf(0.0, if (it.x) (xValue ?: 0.0) else (it.pct ?: share)) }
    }

    fun parse(text0: String): RoutineSpec? {
        val text = text0.replace("\r\n", "\n")
        val r = RoutineSpec()
        START_RE.find(text)?.let { m ->
            val h = m.groupValues[1].toInt(); val mi = m.groupValues[2].toInt()
            if (h < 24 && mi < 60) r.start = h * 60 + mi
        }
        r.focusLabel = FOCUS_RE.find(text)?.groupValues?.get(1)?.trim() ?: ""
        val pos = ArrayList<Pair<Int, RtItem>>()
        for (m in DUR_RE.findAll(text)) {
            val dur = minutes(m.groupValues[1].toDouble(), m.groupValues[2])
            if (dur > 0) pos.add(m.range.first to RtItem(false, dur, m.groupValues[3].trim()))
        }
        for (m in LOOP_RE.findAll(text)) {
            val dur = minutes(m.groupValues[1].toDouble(), m.groupValues[2].ifEmpty { "min" })
            val foc = if (m.groupValues[3].isNotEmpty()) minutes(m.groupValues[3].toDouble(), m.groupValues[4]) else 100.0
            if (dur > 0) pos.add(m.range.first to RtItem(true, dur, "", if (foc > 0) foc else 100.0))
        }
        r.items.addAll(pos.sortedBy { it.first }.map { it.second })
        for ((key, name) in FLUCT_KEYS) {
            Regex(Regex.escape(name) + """[ \t]*[:：][ \t]*$RANGE""").find(text)?.let { m ->
                r.fluct[key] = m.groupValues[1].toDouble() to m.groupValues[2].toDouble()
            }
        }
        for (m in LIST_RE.findAll(text)) {
            val name = m.groupValues[1].trim()
            val body = m.groupValues[2]
            if (name == "设备列表") { r.devices = body.split(',', '，').map { it.trim() }.filter { it.isNotEmpty() }; continue }
            r.lists[name] = parseList(body)
            if (m.groupValues[3].isNotEmpty()) r.base.add(name)
            DEV_WIN_RE.find(name)?.let { r.windows[it.groupValues[1].toInt() - 1] = name }
        }
        DIFF_RE.find(text)?.let { m -> r.diff = m.groupValues[1].toDouble().toInt() to m.groupValues[2].toDouble().toInt() }
        for ((key, name) in listOf("skip" to "不选择的词条", "bad" to "违规词条")) {
            val items = r.lists[name] ?: Regex(Regex.escape(name) + """[ \t]*[:：][ \t]*[{｛]([^{}｛｝\n]*)[}｝]""").find(text)?.let { parseList(it.groupValues[1]) } ?: emptyList()
            val names = items.map { it.name }
            if (key == "skip") r.skipTerms = names else r.badTerms = names
        }
        return if (r.ok()) r else null
    }

    // ══════════════ 日程 ══════════════
    fun loopSegs(start: LocalDateTime, end: LocalDateTime, nMin: Double, loop: Int = -1, anchor: LocalDateTime = start): List<RtSeg> {
        val rest = maxOf(1, Math.rint(computeF(nMin)).toInt()).toLong()
        val out = ArrayList<RtSeg>()
        var t = anchor
        val n = (nMin * 60).toLong()
        while (t.isBefore(end)) {
            val fEnd = minOf(t.plusSeconds(n), end)
            if (fEnd.isAfter(t)) out.add(RtSeg("focus", t, fEnd, "专注", loop, false, nMin))
            t = fEnd
            if (!t.isBefore(end)) break
            val rEnd = minOf(t.plusMinutes(rest), end)
            if (rEnd.isAfter(t)) out.add(RtSeg("rest", t, rEnd, "休息", loop, false, nMin))
            t = rEnd
        }
        return out
    }

    fun wakeAt(r: RoutineSpec, d: LocalDate, seed: String): LocalDateTime {
        val (lo, hi) = r.fluct.getValue("sleep_end")
        return d.atStartOfDay().plusMinutes((r.start!! + rint("$seed|wake|$d", lo, hi)).toLong())
    }

    /** d 这一天 (从起床到下一次起床) 的日程, 按时间排, 首尾相接: 各项活动 → 睡眠开始前的空闲 (如果有) → 睡眠. */
    fun dayPlan(r: RoutineSpec, d: LocalDate, seed: String, fluct: Boolean = true): List<RtSeg> {
        val ck = "$d|$seed|$fluct"
        r.plans[ck]?.let { return it }
        if (r.plans.size > 12) r.plans.clear()
        return computeDay(r, d, seed, fluct).also { r.plans[ck] = it }
    }

    private fun computeDay(r: RoutineSpec, d: LocalDate, seed: String, fluct: Boolean): List<RtSeg> {
        val base = wakeAt(r, d, seed)
        var segs = ArrayList<RtSeg>()
        var c = base
        for ((idx, it) in r.items.withIndex()) {
            val end = c.plusNanos((it.dur * 60e9).toLong())
            if (!it.isLoop) segs.add(RtSeg("idle", c, end, it.label))
            else segs.addAll(loopSegs(c, end, it.focus, idx))
            c = end
        }
        val n = segs.size
        val ns = segs.map { it.start }.toTypedArray()
        val ne = segs.map { it.end }.toTypedArray()
        val gaps = HashMap<Int, Pair<LocalDateTime, LocalDateTime>>()
        if (fluct) {
            val fs = r.fluct.getValue("focus_start")
            val fe = r.fluct.getValue("focus_end")
            var k = 0
            for ((i, s) in segs.withIndex()) {
                if (s.kind != "focus") continue
                val ds = rint("$seed|fs|$d|$k", fs.first, fs.second)
                val de = rint("$seed|fe|$d|$k", fe.first, fe.second)
                k++
                val cs = s.start.plusMinutes(ds.toLong())
                val ce = s.end.plusMinutes(de.toLong())
                if (ChronoUnit.SECONDS.between(cs, ce) < 600) continue
                if (i > 0 && cs != s.start) {
                    if (cs.isBefore(s.start)) {
                        if (cs.isAfter(ns[i - 1].plusMinutes(1))) { ne[i - 1] = cs; ns[i] = cs }
                    } else { ns[i] = cs; gaps[i] = ne[i - 1] to cs }
                }
                if (i + 1 < n && ce != s.end && ce.isBefore(ne[i + 1].minusMinutes(1)) && ce.isAfter(ns[i].plusMinutes(5))) {
                    ne[i] = ce; ns[i + 1] = ce
                }
            }
        }
        val out = ArrayList<RtSeg>()
        for ((i, s) in segs.withIndex()) {
            gaps[i]?.let { g -> if (g.second.isAfter(g.first)) out.add(RtSeg("idle", g.first, g.second, "空闲", -1, true)) }
            out.add(s.copy(start = ns[i], end = ne[i]))
        }
        val nxt = wakeAt(r, d.plusDays(1), seed)
        val (lo, hi) = if (fluct) r.fluct.getValue("sleep_start") else (0.0 to 0.0)
        val sleepStart = maxOf(c, c.plusMinutes(rint("$seed|ss|$d", lo, hi).toLong()))
        val lastEnd = if (out.isNotEmpty()) out.last().end else c
        if (sleepStart.isAfter(lastEnd) && sleepStart.isAfter(c)) out.add(RtSeg("idle", lastEnd, minOf(sleepStart, nxt), "空闲", -1, true))
        if (sleepStart.isBefore(nxt)) out.add(RtSeg("sleep", sleepStart, nxt, "睡眠"))
        return out.sortedBy { it.start }
    }

    /** [a, b) 里的日程段 (整段, 不截断), 按时间排. */
    fun planBetween(r: RoutineSpec, a: LocalDateTime, b: LocalDateTime, seed: String, fluct: Boolean = true): List<RtSeg> {
        val out = ArrayList<RtSeg>()
        var d = a.toLocalDate().minusDays(1)
        while (!d.isAfter(b.toLocalDate())) {
            for (s in dayPlan(r, d, seed, fluct)) if (s.end.isAfter(a) && s.start.isBefore(b)) out.add(s)
            d = d.plusDays(1)
        }
        out.sortBy { it.start }
        val uniq = ArrayList<RtSeg>()
        var last: RtSeg? = null
        for (s in out) {
            if (last != null && s.start.isBefore(last.end)) continue
            uniq.add(s); last = s
        }
        return uniq
    }

    /** 联合行动之后休息结束的时刻 t 落在某个循环里: 从 t 起"立即开始专注", 到那个循环结束; 落在循环外不影响. */
    fun shiftForJoint(segs: List<RtSeg>, t: LocalDateTime, now: LocalDateTime): List<RtSeg> {
        if (now.isBefore(t)) return segs
        for (s in segs) {
            if (s.loop < 0 || s.start.isAfter(t) || !t.isBefore(s.end)) continue
            val same = segs.filter { it.loop == s.loop && Math.abs(ChronoUnit.SECONDS.between(it.start, s.start)) < 12 * 3600 }
            val end = same.maxOf { it.end }
            if (!now.isBefore(end)) return segs
            val n = if (s.n > 0) s.n else 100.0
            val fresh = loopSegs(t, end, n, s.loop)
            val keep = ArrayList<RtSeg>()
            for (x in segs) {
                if (x in same) {
                    if (!x.end.isAfter(t)) keep.add(x) else if (x.start.isBefore(t)) keep.add(x.copy(end = t))
                } else keep.add(x)
            }
            return (keep + fresh).sortedBy { it.start }
        }
        return segs
    }

    fun statusIn(segs: List<RtSeg>, now: LocalDateTime): RtSeg? = segs.firstOrNull { !it.start.isAfter(now) && now.isBefore(it.end) }

    // ══════════════ 带波动的概率 ══════════════
    fun fluctList(items: List<RtListItem>, seed: String, base: Boolean, xValue: Double? = null): List<Pair<String, Double>> {
        val res = resolveList(items, xValue)
        if (!base) return res
        val total = res.sumOf { it.second }
        var out = res.map { (n, p) -> n to p * (1.0 + (u01("$seed|$n") * 2.0 - 1.0) * BASE_FLUCT) }
        val s2 = out.sumOf { it.second }
        if (total >= 99.99 && s2 > 0) out = out.map { (n, p) -> n to p * total / s2 }
        return out
    }

    fun pick(weights: List<Pair<String, Double>>, key: String): String {
        if (weights.isEmpty()) return ""
        val tot = weights.sumOf { it.second }
        if (tot <= 0) return weights[0].first
        var x = u01(key) * tot
        for ((n, p) in weights) { x -= p; if (x < 0) return n }
        return weights.last().first
    }

    /** 用户在这个区间 (整点记录的一格) 发了 n 条消息 → "聊天"的概率 (%): 0 条 0, 每小时 >= 20 条 50%, 中间均匀分配; 半小时的格子按比例. */
    fun chatPct(nMsgs: Int, minutes: Double): Double {
        val full = maxOf(1.0, CHAT_FULL_MSGS * minutes / 60.0)
        return CHAT_FULL * minOf(1.0, maxOf(0.0, nMsgs / full))
    }

    fun plain(label: String): Boolean {
        val head = label.split('(', '（')[0].trim().lowercase()
        return head in setOf("空闲", "idle", "free", "休息", "rest", "")
    }

    // ══════════════ 合约记录 ══════════════
    fun bannedIds(r: RoutineSpec): Set<String> {
        val names = (r.skipTerms + r.badTerms).map { it.replace(Regex("""\s+"""), "") }.toSet()
        val series = HashSet<String>()
        for (t in Contract.TERMS) {
            val short = t.name.substringAfter(':')
            val base = short.replace(Regex("""-(?:I{1,3}|IV|V)$"""), "")
            if (short in names || base in names || t.name in names) series.add(t.group)
        }
        return Contract.TERMS.filter { it.group in series }.map { it.id }.toSet()
    }

    private fun iso(t: LocalDateTime) = t.withNano(0).toString().let { if (it.length == 16) "$it:00" else it }

    fun contractForDay(r: RoutineSpec, d: LocalDate, seed: String): JSONObject? {
        if (u01("$seed|ct|$d") >= CONTRACT_DAY_P) return null
        val banned = bannedIds(r)
        val pool = Contract.TERMS.map { it.id }.filter { it !in banned }
        val (lo, hi) = r.diff
        val target = rint("$seed|ct|$d|lv", lo.toDouble(), hi.toDouble())     // 这个合约要凑到的危机等级 (落在 [lo, hi] 里)
        var best: List<String>? = null
        for (attempt in 0 until 40) {
            val order = pool.sortedBy { u01("$seed|ct|$d|$attempt|$it") }
            var terms = ArrayList<String>()
            for (t in order) {
                if (Contract.blockedBy(t, terms) != null) continue
                val trial = terms + t
                if (Contract.needsOf(t, trial) != null || Contract.levelOf(trial) > hi) continue
                terms = ArrayList(trial)
                if (Contract.levelOf(terms) >= target) break
            }
            val cleaned = Contract.clean(terms, true)
            val lv = Contract.levelOf(cleaned)
            if (best == null) best = cleaned
            if (lv in lo..hi) { best = cleaned; break }
        }
        val terms = best ?: emptyList()
        val lv = Contract.levelOf(terms)
        val mins = 10 * 60 + rint("$seed|ct|$d|t", 0.0, 11.0 * 60)
        val end = d.atStartOfDay().plusMinutes(mins.toLong())
        return JSONObject().put("result", "completed").put("end", iso(end)).put("level", lv).put("orig_level", lv)
            .put("orig", JSONArray(terms)).put("terms", JSONArray(terms)).put("rand", JSONArray())
    }

    /** 最近 days 天里已经结束的合约记录 (从新到旧). */
    fun contractsBefore(r: RoutineSpec, now: LocalDateTime, seed: String, days: Int = 14): List<JSONObject> {
        val out = ArrayList<JSONObject>()
        for (i in 0 until days) {
            val rec = contractForDay(r, now.toLocalDate().minusDays(i.toLong()), seed) ?: continue
            if (!LocalDateTime.parse(rec.getString("end")).isAfter(now)) out.add(rec)
        }
        out.sortByDescending { it.getString("end") }
        return out
    }
}

/** 对方实际发生过的数据: 时间线 (一段段 [kind, 开始, 结束, 名字]) 和整点记录的格子. 存成 JSON 文件. */
class RoutineStore(val file: File, var r: RoutineSpec, val seed: String) {
    var upto: LocalDateTime? = null
    val tl = ArrayList<Array<String>>()
    val cells = LinkedHashMap<String, Pair<Int, String>>()
    var dirty = false

    init { load() }

    private fun load() {
        val d = Store.readObj(file) ?: return
        if (d.optString("seed") != seed) return
        runCatching {
            upto = d.optString("upto").takeIf { it.isNotEmpty() }?.let { LocalDateTime.parse(it) }
            val a = d.optJSONArray("tl") ?: JSONArray()
            for (i in 0 until a.length()) a.optJSONArray(i)?.takeIf { it.length() == 4 }?.let { tl.add(arrayOf(it.getString(0), it.getString(1), it.getString(2), it.getString(3))) }
            val c = d.optJSONObject("cells") ?: JSONObject()
            for (k in c.keys()) c.optJSONArray(k)?.takeIf { it.length() == 2 }?.let { cells[k] = it.getInt(0) to it.getString(1) }
        }.onFailure { upto = null; tl.clear(); cells.clear() }
    }

    fun payload(): JSONObject {
        val c = JSONObject()
        for ((k, v) in cells) c.put(k, JSONArray().put(v.first).put(v.second))
        return JSONObject().put("seed", seed).put("upto", upto?.let { iso(it) } ?: "")
            .put("tl", JSONArray(tl.map { JSONArray(it.toList()) })).put("cells", c)
    }

    private fun iso(t: LocalDateTime): String = t.withNano(0).toString().let { if (it.length == 16) "$it:00" else it }

    private fun append(kind: String, a: LocalDateTime, b: LocalDateTime, label: String) {
        if (!b.isAfter(a)) return
        val last = tl.lastOrNull()
        if (last != null && last[0] == kind && last[3] == label && last[2] == iso(a)) last[2] = iso(b) else tl.add(arrayOf(kind, iso(a), iso(b), label))
    }

    /** 把时间线补到 now (整分钟). segsFn(a, b): 现在生效的日程; override: (kind, label) = 这段时间对方在联合行动里. */
    fun fillTimeline(now0: LocalDateTime, segsFn: (LocalDateTime, LocalDateTime) -> List<RtSeg>, override: Pair<String, String>? = null) {
        val now = now0.withSecond(0).withNano(0)
        if (upto == null) upto = now.toLocalDate().minusDays(Routine.BACKFILL_DAYS.toLong()).atStartOfDay()
        val floor = now.toLocalDate().minusDays(Routine.SYNC_MAX_DAYS.toLong()).atStartOfDay()
        if (upto!!.isBefore(floor)) upto = floor
        val a = upto!!
        if (!now.isAfter(a)) return
        if (override != null) append(override.first, a, now, override.second)
        else {
            var cur = a
            for (s in segsFn(a, now)) {
                val s0 = maxOf(cur, s.start)
                val s1 = minOf(now, s.end)
                if (s0.isAfter(cur)) append("idle", cur, s0, "空闲")
                if (s1.isAfter(s0)) { append(s.kind, s0, s1, s.label); cur = s1 }
            }
            if (cur.isBefore(now)) append("idle", cur, now, "空闲")
        }
        upto = now
        dirty = true
        val cut = iso(now.toLocalDate().minusDays(Routine.KEEP_DAYS.toLong()).atStartOfDay())
        tl.removeAll { it[2] <= cut }
        cells.keys.removeAll { it < cut }
    }

    /** [a, b) 里已经发生的时间线段 (截断到 [a, min(b, upto)]). */
    fun segments(a: LocalDateTime, b0: LocalDateTime): List<RtSeg> {
        val up = upto ?: return emptyList()
        val b = minOf(b0, up)
        val out = ArrayList<RtSeg>()
        for (x in tl) {
            val s = maxOf(a, LocalDateTime.parse(x[1]))
            val e = minOf(b, LocalDateTime.parse(x[2]))
            if (e.isAfter(s)) out.add(RtSeg(x[0], s, e, x[3]))
        }
        return out
    }

    fun minutes(a: LocalDateTime, b: LocalDateTime, kinds: Set<String>? = null): Double =
        segments(a, b).filter { kinds == null || it.kind in kinds }.sumOf { ChronoUnit.SECONDS.between(it.start, it.end) / 60.0 }

    /** 把还没记的整点记录格子补上. fineFn(整点) 为真的小时分成两个半小时. msgsFn(a, b): 这个区间里用户发了几条消息. */
    fun fillCells(now: LocalDateTime, msgsFn: (LocalDateTime, LocalDateTime) -> Int, fineFn: (LocalDateTime) -> Boolean = { false }) {
        val up = upto ?: return
        val first = tl.firstOrNull()?.let { LocalDateTime.parse(it[1]) } ?: return
        val limit = minOf(now, up)
        var d = maxOf(first.toLocalDate(), limit.toLocalDate().minusDays(Routine.SYNC_MAX_DAYS.toLong()))
        while (!d.isAfter(limit.toLocalDate())) {
            val day0 = d.atStartOfDay()
            for (h in 0 until 24) {
                val t = day0.plusHours(h.toLong())
                val parts = if (fineFn(t)) listOf(t to 30, t.plusMinutes(30) to 30) else listOf(t to 60)
                for ((st, ln) in parts) {
                    if (st.isBefore(first) || st.plusMinutes(ln.toLong()).isAfter(limit)) continue
                    val key = cellKey(st)
                    if (key in cells) continue
                    cells[key] = ln to cellText(st, ln, msgsFn(st, st.plusMinutes(ln.toLong())))
                    dirty = true
                }
            }
            d = d.plusDays(1)
        }
    }

    private fun cellKey(st: LocalDateTime) = "%04d-%02d-%02dT%02d:%02d".format(st.year, st.monthValue, st.dayOfMonth, st.hour, st.minute)

    /** 这一格对方做了什么 ("" = 没有记录: 睡着的格子). */
    fun cellText(st: LocalDateTime, ln: Int, nMsgs: Int): String {
        val en = st.plusMinutes(ln.toLong())
        val by = LinkedHashMap<Pair<String, String>, Double>()
        for (s in segments(st, en)) {
            val k = s.kind to s.label
            by[k] = (by[k] ?: 0.0) + ChronoUnit.SECONDS.between(s.start, s.end) / 60.0
        }
        val sleep = by.filter { it.key.first == "sleep" }.values.sum()
        if (by.isEmpty() || sleep >= ln / 2.0) return ""
        val awake = by.filter { it.key.first != "sleep" }.values.sum()
        val focus = by.filter { it.key.first == "focus" }.values.sum()
        val key = "$seed|cell|${cellKey(st)}"
        if (focus >= awake / 2.0 && focus > 0) {
            return Routine.pick(Routine.resolveList(r.lists["整点记录专注填写列表"] ?: emptyList()), key).ifEmpty { r.focusLabel.ifEmpty { "专注" } }
        }
        val named = LinkedHashMap<String, Double>()
        for ((k, v) in by) if (k.first == "idle" && !Routine.plain(k.second)) named[k.second] = (named[k.second] ?: 0.0) + v
        if (named.isNotEmpty()) {
            val (label, v) = named.entries.maxByOrNull { it.value }!!.let { it.key to it.value }
            if (v >= (awake - focus) / 2.0) return label
        }
        val items = r.lists["整点记录休息填写列表"] ?: emptyList()
        return Routine.pick(Routine.resolveList(items, Routine.chatPct(nMsgs, ln.toDouble())), key).ifEmpty { "休息" }
    }

    /** 今天已经结束的格子: ["09:00 英语", ...]. */
    fun logLines(now: LocalDateTime): List<String> {
        val day = "%04d-%02d-%02d".format(now.year, now.monthValue, now.dayOfMonth)
        val out = ArrayList<String>()
        for (key in cells.keys.sorted()) {
            val (ln, text) = cells.getValue(key)
            if (!key.startsWith(day) || text.isEmpty()) continue
            val st = LocalDateTime.parse(key + ":00")
            if (st.plusMinutes(ln.toLong()).isAfter(now)) continue
            out.add("%02d:%02d %s".format(st.hour, st.minute, text))
        }
        return out
    }

    fun dayMinutes(d: LocalDate, kinds: Set<String>): Double = minutes(d.atStartOfDay(), d.plusDays(1).atStartOfDay(), kinds)
    fun awakeFreeMinutes(d: LocalDate) = dayMinutes(d, setOf("rest", "idle"))
    fun monthDays(now: LocalDateTime): List<LocalDate> = (1..now.dayOfMonth).map { LocalDate.of(now.year, now.monthValue, it) }

    /** d 这天每个联网理由的分钟数 (只算已经过去的部分). */
    fun netByReason(d: LocalDate): Map<String, Double> {
        val total = awakeFreeMinutes(d) * Routine.NET_SHARE
        val items = r.lists["联网时间理由列表"] ?: emptyList()
        if (items.isEmpty() || total <= 0) return emptyMap()
        val w = Routine.fluctList(items, "$seed|net|$d", "联网时间理由列表" in r.base)
        val out = LinkedHashMap<String, Double>()
        for ((n, p) in w) if (p > 0) out[n] = total * p / 100.0
        return out
    }

    /** d 这天第 dev 台设备每个程序的秒数 (第一台用在专注的时间, 其余用在休息 / 空闲的时间; 没写满 100% 的剩下算"空白"). */
    fun windowSeconds(d: LocalDate, dev: Int): Map<String, Double> {
        val name = r.windows[dev] ?: return emptyMap()
        val items = r.lists[name] ?: return emptyMap()
        if (items.isEmpty()) return emptyMap()
        val used = if (dev == 0) dayMinutes(d, setOf("focus")) else awakeFreeMinutes(d)
        val w = Routine.fluctList(items, "$seed|win$dev|$d", name in r.base)
        val out = LinkedHashMap<String, Double>()
        for ((n, p) in w) if (p > 0) out[n] = used * 60.0 * p / 100.0
        val rest = maxOf(0.0, 100.0 - w.sumOf { it.second })
        if (rest > 0.05) out[""] = used * 60.0 * rest / 100.0
        return out
    }
}
