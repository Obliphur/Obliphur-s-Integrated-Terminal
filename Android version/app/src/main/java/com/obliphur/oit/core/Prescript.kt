package com.obliphur.oit.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.LocalDateTime
import kotlin.random.Random

/**
 * 指令 (Prescript, 都市之意主题独占, MECHANICS 22). 对应桌面版 prescript.py.
 * 判定 / 计时 / 存档都在这里, 界面在 ui/PrescriptScreen.kt. 数据在 prescript.json, 不同步.
 * 时间都按墙上时间算; 关掉程序期间照常流逝.
 */
object Prescript {
    val TYPES = listOf("I", "II", "III", "IV", "V", "VI")

    /** 每个池子里六条指令的概率: I, II, III, IV, V, VI */
    val POOLS = mapOf(
        "focus" to doubleArrayOf(0.8, 0.03, 0.03, 0.10, 0.00, 0.04),
        "break" to doubleArrayOf(0.0, 0.10, 0.70, 0.10, 0.05, 0.05),
        "idle" to doubleArrayOf(0.1, 0.02, 0.02, 0.04, 0.80, 0.02),
    )

    /** 领取后多久作废 (秒); IV 在这一天结束时作废. */
    val EXPIRY_SECONDS = mapOf("I" to 3600L, "II" to 8 * 3600L, "III" to 3600L, "V" to 300L, "VI" to 3600L)
    val FIXED_T = mapOf("I" to 1.0, "III" to 1.0, "IV" to 1.0, "V" to 0.1, "VI" to 1.0)
    const val FOCUS_TARGET = 50 * 60.0
    const val BREAK_TARGET = 20 * 60.0
    const val REFLECT_TARGET = 3 * 60.0
    const val DAILY_T_TARGET = 10.0
    const val DAILY_BONUS_SECONDS = 20 * 60
    const val CODE_WIDTH = 8
    const val CODE_LEFT = "_%"
    const val CODE_RIGHT = "%."
    /** 开发者模式下同时最多留几条指令. */
    const val DEV_KEEP = 3
    const val HISTORY_KEEP = 100
    const val GRANTED_KEEP = 40
    const val SAVE_EVERY = 10.0

    /** t 的小时数写法: 1 → "1", 0.1 → "0.1". */
    fun fmtT(t: Double): String {
        if (Math.abs(t - Math.round(t)) < 1e-9) return Math.round(t).toString()
        return "%.3f".format(java.util.Locale.ROOT, t).trimEnd('0').trimEnd('.')
    }

    /** 给出 N 分钟休息的代码正文: 3 → "REST3", 0.3 → "REST0.3". */
    fun restCode(minutes: Double): String {
        val m = Math.round(minutes * 10) / 10.0
        val text = if (Math.abs(m - Math.round(m)) < 1e-9) Math.round(m).toString()
        else "%.1f".format(java.util.Locale.ROOT, m)
        return "REST$text"
    }

    fun codeFor(t: Double): String = restCode(t * 3)

    /** 每条指令的蓝色代码用的词 (补位后正好 8 个字符以内). */
    val TYPE_WORDS = mapOf("I" to "FOCUS", "II" to "CONTRACT", "III" to "REST", "IV" to "DAILY", "V" to "STOP", "VI" to "REFLECT")

    fun typeCode(kind: String): String = TYPE_WORDS[kind] ?: "NONE"

    private val LEGACY_H = Regex("^(\\d+(?:\\.\\d+)?)h\\*3min$")
    private val LEGACY_MIN = Regex("^(\\d+(?:\\.\\d+)?)min$")

    /** 蓝色代码补到 8 个字符: 正文居中, 两边用 _ % . 补位 ("NONE" → "_%NONE%."). */
    fun padCode(raw: String): String {
        var code = raw
        val h = LEGACY_H.find(code)
        val m = LEGACY_MIN.find(code)
        if (h != null) code = restCode(h.groupValues[1].toDouble() * 3)
        else if (m != null) code = restCode(m.groupValues[1].toDouble())
        code = code.take(CODE_WIDTH)
        val gap = CODE_WIDTH - code.length
        val left = gap / 2
        val right = gap - left
        val l = CODE_LEFT.repeat(CODE_WIDTH).take(left)
        val r = CODE_RIGHT.repeat(CODE_WIDTH).takeLast(right)
        return l + code + r
    }

    fun contractDurationHours(rec: JSONObject): Double {
        val start = parseIso(rec.optString("start")) ?: return 0.0
        val stop = parseIso(rec.optString("planned")) ?: parseIso(rec.optString("end")) ?: return 0.0
        if (!stop.isAfter(start)) return 0.0
        return secondsBetween(start, stop) / 3600.0
    }
}

class PrescriptEngine(
    private val file: File,
    private val stateFn: () -> String = { "idle" },          // "focus" / "break" / "idle"
    private val todayHoursFn: () -> Double = { 0.0 },
    private val contractsFn: () -> List<JSONObject> = { emptyList() },
    private val pageFn: () -> String = { "" },
    private val minimizedFn: () -> Boolean = { false },
    private val grantRestFn: ((Int, String) -> Unit)? = null,
    private val blockedFn: () -> Boolean = { false },
    private val devFn: () -> Boolean = { false },
    /** 领取 V 时暂停后台正在播放的音频 / 视频. */
    private val pauseMediaFn: (() -> Unit)? = null,
    private val onChange: (() -> Unit)? = null,
    private val rng: Random = Random.Default,
) {
    private var data: JSONObject = load()
    private var lastTick: LocalDateTime = LocalDateTime.now().withNano(0)
    private var lastSave: LocalDateTime = lastTick
    private var dirty = false

    init {
        ensureDay(lastTick)
    }

    // ---- 存档 ----

    private fun load(): JSONObject {
        val d = Store.readObj(file) ?: JSONObject()
        for (k in listOf("pending", "granted", "history", "extra")) {
            if (d.optJSONArray(k) == null) d.put(k, JSONArray())
        }
        if (d.optJSONObject("daily") == null) d.put("daily", JSONObject())
        if (d.optJSONObject("current") == null) d.put("current", JSONObject.NULL)
        for (k in listOf("last_draw")) if (!d.has(k)) d.put(k, JSONObject.NULL)
        for (k in listOf("last_pool", "last_type")) if (!d.has(k)) d.put(k, "")
        if (!d.has("v_reset")) d.put("v_reset", false)
        return d
    }

    @Synchronized
    fun save() {
        if (blockedFn()) return
        Store.write(file, data)
        lastSave = LocalDateTime.now()
        dirty = false
    }

    private fun saveIfDue(now: LocalDateTime) {
        if (dirty && secondsBetween(lastSave, now) >= Prescript.SAVE_EVERY) save()
    }

    private fun changed() {
        try { onChange?.invoke() } catch (_: Exception) {}
    }

    // ---- 当天 ----

    private fun ensureDay(now: LocalDateTime): JSONObject {
        val daily = data.getJSONObject("daily")
        val day = now.toLocalDate().key()
        if (daily.optString("date") == day) return daily
        for (cur in actives()) {
            if (cur.optString("type") == "IV") {
                finish("expired", now.toLocalDate().atStartOfDay(), save = false, notify = false, settle = false, cur = cur)
            }
        }
        for (k in daily.keys().asSequence().toList()) daily.remove(k)
        daily.put("date", day)
            .put("n_ii", rng.nextInt(1, 21))
            .put("n_iv", rng.nextInt(5, 9))
            .put("completed_t", 0.0)
            .put("bonus", false)
            .put("iv_done", false)
        dirty = true
        return daily
    }

    // ---- 当前指令 ----

    val current: JSONObject? @Synchronized get() = data.optJSONObject("current")

    /** 正在进行的指令, 先领的在前. 平时只有一条; 开发者模式下最多 DEV_KEEP 条. */
    @Synchronized
    fun actives(): List<JSONObject> {
        val out = data.getJSONArray("extra").objects().toMutableList()
        current?.let { out.add(it) }
        return out
    }

    private fun dev(): Boolean = try { devFn() } catch (_: Exception) { false }
    private fun page(): String = try { pageFn() } catch (_: Exception) { "" }
    private fun minimized(): Boolean = try { minimizedFn() } catch (_: Exception) { true }

    @Synchronized
    fun canDraw(now: LocalDateTime = LocalDateTime.now()): Boolean {
        if (dev()) return actives().size < Prescript.DEV_KEEP       // 没有冷却, 但满了就领不了
        if (data.optBoolean("v_reset")) return true
        val last = parseIso(data.optString("last_draw")) ?: return true
        if (last.isAfter(now)) return true
        val boundary = last.floorHour().plusHours(1)
        return !now.isBefore(boundary)
    }

    private fun available(kind: String, now: LocalDateTime): Boolean {
        if (kind == "IV") {
            val daily = ensureDay(now)
            if (daily.optBoolean("iv_done")) return false
            return todayHoursFn() < daily.optDouble("n_iv", 8.0) - 1e-9
        }
        if (kind == "V") return page() == "prescript" && !minimized()
        return true
    }

    private fun weights(pool: String, now: LocalDateTime): DoubleArray {
        val w = (Prescript.POOLS[pool] ?: Prescript.POOLS.getValue("idle")).copyOf()
        var usable = w.indices.filter { w[it] > 0 && available(Prescript.TYPES[it], now) }
        if (usable.isEmpty()) usable = w.indices.filter { w[it] > 0 }
        if (usable.isEmpty()) usable = w.indices.toList()
        val lost = w.indices.filter { w[it] > 0 && it !in usable }.sumOf { w[it] }
        for (i in w.indices) if (i !in usable) w[i] = 0.0
        if (lost > 0) for (i in usable) w[i] += lost / usable.size
        return w
    }

    private fun pick(pool: String, now: LocalDateTime): String {
        val w = weights(pool, now)
        val roll = rng.nextDouble()
        var acc = 0.0
        for (i in w.indices) {
            if (w[i] <= 0) continue
            acc += w[i]
            if (roll < acc) return Prescript.TYPES[i]
        }
        for (i in w.indices.reversed()) if (w[i] > 0) return Prescript.TYPES[i]
        return "I"
    }

    // ---- 领取 ----

    /** 领取一条指令; 领不了时返回 null. choose / pool 只给自检用. */
    @Synchronized
    fun draw(now0: LocalDateTime = LocalDateTime.now(), choose: String? = null, pool0: String? = null): JSONObject? {
        val now = now0.withNano(0)
        if (!canDraw(now)) return null
        ensureDay(now)
        if (dev()) {
            // 开发者模式: 上一条不作废, 留在后面继续判定.
            current?.let {
                data.getJSONArray("extra").put(it)
                data.put("current", JSONObject.NULL)
            }
        } else {
            for (old in actives()) finish("superseded", now, save = false, notify = false, settle = false, cur = old)
        }
        var pool = pool0
        if (pool == null) {
            pool = stateFn().let { if (it in Prescript.POOLS) it else "idle" }
            // 上一次是空闲池里的 V: 这一次从专注池抽.
            if (data.optString("last_pool") == "idle" && data.optString("last_type") == "V") pool = "focus"
        }
        val kind = if (choose != null && choose in Prescript.TYPES) choose else pick(pool, now)
        val daily = ensureDay(now)
        var n: Int? = null
        val body = when (kind) {
            "I" -> "接下来的1h专注50min."
            "II" -> { n = daily.optInt("n_ii", 1); "完成一次至少${n}级的合约." }
            "III" -> "休息20min."
            "IV" -> { n = daily.optInt("n_iv", 5); "今天专注至少$n h" }
            "V" -> "立即停下任何活动,接下来的5min什么也不做."
            else -> "检查你的统计数据,并作简要反思."
        }
        val expires = if (kind == "IV") now.toLocalDate().plusDays(1).atStartOfDay()
        else now.plusSeconds(Prescript.EXPIRY_SECONDS[kind] ?: 3600L)
        val cur = JSONObject()
            .put("id", now.format(java.time.format.DateTimeFormatter.ofPattern("yyyyMMddHHmmss")) + "-%03d".format(rng.nextInt(1000)))
            .put("type", kind).put("body", body)
            .put("accepted", now.iso()).put("expires", expires.iso())
            .put("pool", pool).put("n", n ?: JSONObject.NULL)
            .put("t", Prescript.FIXED_T[kind] ?: 0.0)
            .put("focus_sec", 0.0).put("break_sec", 0.0).put("stats_since", JSONObject.NULL)
        data.put("current", cur)
        data.put("last_draw", now.iso()).put("last_pool", pool).put("last_type", kind).put("v_reset", false)
        lastTick = now
        dirty = true
        save()
        if (kind == "V") try { pauseMediaFn?.invoke() } catch (_: Exception) {}
        changed()
        return cur
    }

    // ---- 完成 / 作废 ----

    private fun finish(result: String, now: LocalDateTime, t: Double? = null, save: Boolean = true,
                       notify: Boolean = true, settle: Boolean = true, cur: JSONObject? = null) {
        val c = cur ?: current ?: return
        val done = if (result == "completed") (t ?: c.optDouble("t", 0.0)) else null
        val rec = JSONObject()
            .put("id", c.optString("id")).put("type", c.optString("type")).put("body", c.optString("body"))
            .put("accepted", c.opt("accepted")).put("ended", now.iso()).put("result", result)
            .put("t", done ?: JSONObject.NULL)
            .put("code", if (done != null) Prescript.codeFor(done) else "")
        val extra = data.getJSONArray("extra")
        if (c === current) {
            // 当前这条结束了: 后面留着的最新一条顶上来.
            if (extra.length() > 0) data.put("current", extra.remove(extra.length() - 1))
            else data.put("current", JSONObject.NULL)
        } else {
            val keep = extra.objects().filter { it !== c }
            data.put("extra", JSONArray(keep))
        }
        val history = data.getJSONArray("history").also { it.put(rec) }
        while (history.length() > Prescript.HISTORY_KEEP) history.remove(0)

        if (done != null) {
            val daily = ensureDay(now)
            daily.put("completed_t", daily.optDouble("completed_t", 0.0) + done)
            if (rec.getString("type") == "IV") daily.put("iv_done", true)
            data.getJSONArray("pending").put(JSONObject()
                .put("kind", "complete").put("code", Prescript.codeFor(done)).put("t", done)
                .put("seconds", Math.round(done * 180).toInt()).put("at", now.iso()))
            // 今天的 t 合计到 10: 20 分钟当场排进待结算 (换日后打开页面也不会丢).
            if (!daily.optBoolean("bonus") && daily.optDouble("completed_t", 0.0) >= Prescript.DAILY_T_TARGET - 1e-9) {
                daily.put("bonus", true)
                data.getJSONArray("pending").put(JSONObject()
                    .put("kind", "daily").put("code", Prescript.restCode(20.0)).put("t", JSONObject.NULL)
                    .put("seconds", Prescript.DAILY_BONUS_SECONDS).put("at", now.iso()))
            }
        }
        dirty = true
        if (save) save()
        if (done != null && settle && page() == "prescript") settleRewards(now)
        if (notify) changed()
    }

    // ---- 判定 ----

    /** V: 页面 / 界面刚一变化就判一次 (不用等下一秒的 tick). */
    @Synchronized
    fun checkVisible(now0: LocalDateTime = LocalDateTime.now()) {
        if (page() == "prescript" && !minimized()) return
        val now = now0.withNano(0)
        for (cur in actives()) {
            if (cur.optString("type") == "V") {
                data.put("v_reset", true)
                finish("void", now, cur = cur)
            }
        }
    }

    /** 切页 / 退到后台时调用: V 立即作废, VI 的连续停留立即清零. */
    @Synchronized
    fun pageChanged(now: LocalDateTime = LocalDateTime.now()) {
        checkVisible(now)
        for (cur in actives()) {
            if (cur.optString("type") == "VI" && !cur.isNull("stats_since")) {
                if (page() != "stats" || minimized()) {
                    cur.put("stats_since", JSONObject.NULL)
                    dirty = true
                    saveIfDue(now)
                }
            }
        }
    }

    /** II: 领取之后完成、等级够的下一次合约 (取最早完成的那一个). */
    private fun matchingContract(cur: JSONObject): JSONObject? {
        val accepted = parseIso(cur.optString("accepted"))
        val expires = parseIso(cur.optString("expires"))
        val need = if (cur.isNull("n")) 0 else cur.optInt("n", 0)
        var best: JSONObject? = null
        var bestEnd: LocalDateTime? = null
        for (rec in contractsFn()) {
            if (rec.optString("result") != "completed") continue
            val end = parseIso(rec.optString("end")) ?: continue
            if (accepted == null || !end.isAfter(accepted)) continue
            if (expires != null && end.isAfter(expires)) continue
            if (rec.optInt("level", 0) < need) continue
            if (bestEnd == null || end.isBefore(bestEnd)) { best = rec; bestEnd = end }
        }
        return best
    }

    /** 每秒: 累计专注 / 休息, 看合约 / 统计页 / 当天专注, 处理作废. */
    @Synchronized
    fun tick(now0: LocalDateTime = LocalDateTime.now()) {
        val now = now0.withNano(0)
        if (blockedFn()) return
        ensureDay(now)
        val prev = lastTick
        val delta = maxOf(0.0, secondsBetween(prev, now))
        lastTick = now
        for (cur in actives()) tickOne(cur, now, prev, delta)
        saveIfDue(now)
    }

    private fun tickOne(cur: JSONObject, now: LocalDateTime, prev: LocalDateTime, delta: Double) {
        val kind = cur.optString("type")
        val expires = parseIso(cur.optString("expires"))
        val expired = expires != null && !now.isBefore(expires)
        // 只算到作废时刻为止: 过了期限之后的那一段不能算进去.
        fun gained(): Double {
            if (expires == null) return delta
            val cutoff = if (now.isBefore(expires)) now else expires
            return if (cutoff.isAfter(prev)) secondsBetween(prev, cutoff) else 0.0
        }
        when (kind) {
            "V" -> {
                if (page() != "prescript" || minimized()) {
                    data.put("v_reset", true); finish("void", now, cur = cur); return
                }
                if (expired) { data.put("v_reset", true); finish("completed", now, 0.1, cur = cur); return }
            }
            "I" -> {
                if (stateFn() == "focus") { cur.put("focus_sec", cur.optDouble("focus_sec", 0.0) + gained()); dirty = true }
                if (cur.optDouble("focus_sec", 0.0) >= Prescript.FOCUS_TARGET) { finish("completed", now, 1.0, cur = cur); return }
                if (expired) { finish("expired", now, cur = cur); return }
            }
            "III" -> {
                if (stateFn() == "break") { cur.put("break_sec", cur.optDouble("break_sec", 0.0) + gained()); dirty = true }
                if (cur.optDouble("break_sec", 0.0) >= Prescript.BREAK_TARGET) { finish("completed", now, 1.0, cur = cur); return }
                if (expired) { finish("expired", now, cur = cur); return }
            }
            "II" -> {
                val rec = matchingContract(cur)
                if (rec != null) { finish("completed", now, Prescript.contractDurationHours(rec), cur = cur); return }
                if (expired) { finish("expired", now, cur = cur); return }
            }
            "IV" -> {
                val daily = ensureDay(now)
                if (todayHoursFn() >= daily.optDouble("n_iv", 0.0) - 1e-9) { finish("completed", now, 1.0, cur = cur); return }
                if (expired) { finish("expired", now, cur = cur); return }
            }
            "VI" -> {
                if (page() == "stats" && !minimized()) {
                    if (cur.isNull("stats_since")) { cur.put("stats_since", now.iso()); dirty = true }
                    val since = parseIso(cur.optString("stats_since"))
                    if (since != null && secondsBetween(since, now) >= Prescript.REFLECT_TARGET) {
                        finish("completed", now, 1.0, cur = cur); return
                    }
                } else if (!cur.isNull("stats_since")) {
                    cur.put("stats_since", JSONObject.NULL); dirty = true
                }
                if (expired) { finish("expired", now, cur = cur); return }
            }
        }
    }

    // ---- 结算休息时间 ----

    /** 打开指令页时调用: 把满足条件的休息时间加进计时器, 返回这次给出的奖励. */
    @Synchronized
    fun settleRewards(now0: LocalDateTime = LocalDateTime.now()): List<JSONObject> {
        val now = now0.withNano(0)
        val daily = ensureDay(now)
        if (!daily.optBoolean("bonus") && daily.optDouble("completed_t", 0.0) >= Prescript.DAILY_T_TARGET - 1e-9) {
            daily.put("bonus", true)
            data.getJSONArray("pending").put(JSONObject()
                .put("kind", "daily").put("code", Prescript.restCode(20.0)).put("t", JSONObject.NULL)
                .put("seconds", Prescript.DAILY_BONUS_SECONDS).put("at", now.iso()))
            dirty = true
        }
        val pending = data.getJSONArray("pending").objects()
        if (pending.isEmpty()) {
            if (dirty) save()
            return emptyList()
        }
        val granted = data.getJSONArray("granted")
        for (item in pending) {
            val seconds = maxOf(0, item.optInt("seconds", 0))
            if (seconds > 0) try { grantRestFn?.invoke(seconds, item.optString("code")) } catch (_: Exception) {}
            item.put("at", now.iso())
            granted.put(JSONObject(item.toString()))
        }
        data.put("pending", JSONArray())
        while (granted.length() > Prescript.GRANTED_KEEP) granted.remove(0)
        dirty = true
        save()
        return pending
    }

    /** 今天结算出的奖励 (最多 8 条). */
    @Synchronized
    fun grantedToday(now: LocalDateTime = LocalDateTime.now()): List<JSONObject> {
        val day = now.toLocalDate().key()
        return data.getJSONArray("granted").objects().filter { it.optString("at").startsWith(day) }.takeLast(8)
    }

    @Synchronized
    fun completedToday(now: LocalDateTime = LocalDateTime.now()): Double = ensureDay(now).optDouble("completed_t", 0.0)

    /** 测试用: 直接改存档. */
    internal fun rawData(): JSONObject = data
}
