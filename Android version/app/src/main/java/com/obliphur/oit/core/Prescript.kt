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
    /** 池子里的六列; V 是空闲词条 (见 IDLE_ENTRIES). */
    val TYPES = listOf("I", "II", "III", "IV", "V", "VI")

    /** 每个池子里六条指令的概率: I, II, III, IV, V, VI */
    val POOLS = mapOf(
        "focus" to doubleArrayOf(0.8, 0.03, 0.03, 0.10, 0.00, 0.04),
        "break" to doubleArrayOf(0.0, 0.10, 0.70, 0.10, 0.05, 0.05),
        "idle" to doubleArrayOf(0.05, 0.01, 0.01, 0.02, 0.90, 0.01),
    )

    /** 领取后多久作废 (秒); IV 在这一天结束时作废. 空闲词条 (IDLE_ENTRIES) 没有时限. */
    val EXPIRY_SECONDS = mapOf("I" to 3600L, "II" to 8 * 3600L, "III" to 3600L, "VI" to 3600L)

    /**
     * 空闲池里 V 那 0.9 由这几个词条分. body 正文; minutes 自动完成要几分钟 (0 = 没有计时); word 蓝色代码的词.
     * 设备时间的哪几段里它的概率被抬高在 设置 → 日常 里改 (PRESCRIPT_WIN_*, 默认值见 Cfg.PRESCRIPT_WIN_DEFAULTS).
     */
    class IdleEntry(val body: String, val minutes: Int, val word: String)

    val IDLE_ENTRIES = linkedMapOf(
        "V" to IdleEntry("立即停下任何活动,接下来的5min什么也不做.", 5, "STOP"),
        "VII" to IdleEntry("到开阔场地进行运动,持续20min.", 20, "EXERCISE"),
        "VIII" to IdleEntry("进行一次淋浴.", 10, "SHOWER"),
        "IX" to IdleEntry("凝视天空.", 5, "GAZE"),
        "X" to IdleEntry("记录,核对并反思这一天的活动.", 0, "RECORD"),
        "XI" to IdleEntry("打扫并整理房间.", 15, "CLEAN"),
    )
    val IDLE_KINDS: List<String> = IDLE_ENTRIES.keys.toList()
    val ALL_TYPES: List<String> = TYPES + IDLE_KINDS.filter { it !in TYPES }
    /** 时间段对得上的词条 (几个就均分) 拿走这一格概率的 80%, 其余词条平分剩下的. */
    const val IDLE_BOOST = 0.8
    /** 每次空闲池抽取之后, 下一次改抽专注池的概率; 没触发 +25%, 触发后回到 25%. */
    const val FOCUS_CHANCE_START = 0.25
    const val FOCUS_CHANCE_STEP = 0.25
    const val LOG_KEEP_DAYS = 30
    /** 其他设备的进入 / 退出指令页事件, 多少秒之内收到才跟着做 (太旧的是以前留下的, 不理). */
    const val PAGE_EVENT_FRESH = 180L

    /** 同步给其他设备的指令记录: 最近这么多天的; 各设备自己累计的进度 (SYNC_VOLATILE) 不同步. */
    const val SYNC_DAYS = 3L
    val SYNC_VOLATILE = setOf("focus_sec", "break_sec", "stats_since", "log_base")

    /** t = 分钟数 × 0.02 (5 分钟 0.1); X 没有计时, t 固定 0.1. */
    val FIXED_T: Map<String, Double> = mapOf("I" to 1.0, "III" to 1.0, "IV" to 1.0, "VI" to 1.0) +
        IDLE_ENTRIES.mapValues { (_, e) -> if (e.minutes == 0) 0.1 else Math.round(e.minutes * 0.02 * 1000) / 1000.0 }
    const val FOCUS_TARGET = 50 * 60.0
    /** III (旧存档里没有分钟数的): 20 分钟休息. */
    const val BREAK_TARGET = 20 * 60.0
    /** III: 要休息的分钟数 = 用户剩余休息时间的 50%, 最多 30 分钟. */
    const val REST_SHARE = 0.5
    const val REST_MAX_MIN = 30
    const val REFLECT_TARGET = 3 * 60.0
    const val DAILY_T_TARGET = 10.0
    const val DAILY_BONUS_SECONDS = 20 * 60
    const val CODE_WIDTH = 8
    const val CODE_LEFT = "_%"
    const val CODE_RIGHT = "%."
    /** 每个用户同时最多持有几条指令, 满了就不能再领 (没有领取冷却). */
    const val KEEP = 3
    const val DEV_KEEP = KEEP

    /** 每种指令对应界面上哪个入口 (在那个按钮上标记): 专注 / 休息 开始按钮, 合约 / 统计 侧边栏入口. */
    val MARK_OF = mapOf("I" to "focus", "III" to "break", "II" to "contract", "VI" to "stats")
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

    /** III 要休息几分钟: 剩余休息时间的 50%, 最多 30 分钟, 至少 1 分钟. */
    fun restMinutes(remainingSeconds: Double): Int =
        (remainingSeconds * REST_SHARE / 60).toInt().coerceIn(1, REST_MAX_MIN)

    /** 每条指令的蓝色代码用的词 (补位后正好 8 个字符以内). */
    val TYPE_WORDS: Map<String, String> = mapOf("I" to "FOCUS", "II" to "CONTRACT", "III" to "REST", "IV" to "DAILY", "VI" to "REFLECT") +
        IDLE_ENTRIES.mapValues { it.value.word }

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
    /** 领取空闲词条 (IDLE_ENTRIES) 时暂停后台正在播放的音频 / 视频. */
    private val pauseMediaFn: (() -> Unit)? = null,
    /** 用户剩余的休息时间 (秒): 休息中是这次休息的倒计时, 否则是积攒的休息时间. */
    private val remainingRestFn: () -> Double = { 0.0 },
    /** 现在有没有进行中的合约 (合约期间抽不到 VI). */
    private val contractActiveFn: () -> Boolean = { false },
    private val onChange: (() -> Unit)? = null,
    /** 有一条指令被完成了 (播完成音效). */
    private val onComplete: (() -> Unit)? = null,
    /** 今天的整点记录 (没启用返回空串), X 词条的输入框第一段. */
    private val hourLogFn: () -> String = { "" },
    /** 某个空闲词条现在的提升时段 (设置 → 日常; 起止分钟, 左闭右开). */
    private val windowsFn: (String) -> List<Pair<Int, Int>> = { Cfg.defaultWindows(it) },
    /** 绑定的其他设备里有没有哪台此刻正停在指令页 (空闲词条要所有设备都离开才作废). */
    private val peersOnPageFn: () -> Boolean = { false },
    /** 其他设备进入 / 退出了指令页 (事件): 本机跟着进入 / 退出 (MECHANICS 22.8). */
    private val pageEventFn: ((Boolean) -> Unit)? = null,
    private val rng: Random = Random.Default,
) {
    /** 最近一次进入 / 退出指令页的事件 {on, at, by}, 随同步; 不存档. */
    private var pageRec: JSONObject? = null
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
        for (k in listOf("pending", "granted", "history", "extra", "run", "closes")) {
            if (d.optJSONArray(k) == null) d.put(k, JSONArray())
        }
        for (k in listOf("daily", "logs", "settled")) if (d.optJSONObject(k) == null) d.put(k, JSONObject())
        if (d.optJSONObject("current") == null) d.put("current", JSONObject.NULL)
        for (k in listOf("last_pool", "last_type")) if (!d.has(k)) d.put(k, "")
        if (!d.has("force_focus")) d.put("force_focus", false)
        val chance = d.optDouble("focus_chance", Prescript.FOCUS_CHANCE_START)
        d.put("focus_chance", if (chance.isNaN()) Prescript.FOCUS_CHANCE_START else chance.coerceIn(0.0, 1.0))
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
        data.put("run", JSONArray())
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

    /** 正在进行的指令, 先领的在前; 同时最多 KEEP 条. */
    @Synchronized
    fun actives(): List<JSONObject> {
        val out = data.getJSONArray("extra").objects().toMutableList()
        current?.let { out.add(it) }
        return out
    }

    /** 现在持有的指令对应哪些入口要标记: focus / break / contract / stats. */
    @Synchronized
    fun marks(): Set<String> = actives().mapNotNull { Prescript.MARK_OF[it.optString("type")] }.toSet()

    private fun dev(): Boolean = try { devFn() } catch (_: Exception) { false }
    private fun page(): String = try { pageFn() } catch (_: Exception) { "" }
    private fun minimized(): Boolean = try { minimizedFn() } catch (_: Exception) { true }

    /** 现在能不能领: 没有冷却, 但手里已经有 KEEP 条就领不了; 专注指令 (I) 没完成 / 作废之前也不能领新的. */
    @Synchronized
    fun canDraw(now: LocalDateTime = LocalDateTime.now()): Boolean {
        val acts = actives()
        if (acts.size >= Prescript.KEEP) return false
        return acts.none { it.optString("type") == "I" }
    }

    private fun available(kind: String, now: LocalDateTime): Boolean {
        if (kind == "IV") {
            val daily = ensureDay(now)
            if (daily.optBoolean("iv_done") || activeOf("IV") != null) return false      // 今天完成过 / 手里已经有一条: 不能再领
            return todayHoursFn() < daily.optDouble("n_iv", 8.0) - 1e-9
        }
        if (kind in Prescript.IDLE_ENTRIES) return page() == "prescript" && !minimized()
        if (kind == "VI") return !(try { contractActiveFn() } catch (_: Exception) { false })   // 合约期间抽不到 VI
        return true
    }

    private fun poolNow(): String {
        val mode = stateFn()
        if (mode == "focus" || mode == "break") return mode
        val inContract = try { contractActiveFn() } catch (_: Exception) { false }
        return if (inContract) listOf("focus", "break").random(rng) else "idle"     // 合约期间空闲: 随机抽专注 / 休息池
    }

    private fun run(): List<String> = data.getJSONArray("run").strings()

    internal fun weights(pool: String, now: LocalDateTime): DoubleArray {
        val w = (Prescript.POOLS[pool] ?: Prescript.POOLS.getValue("idle")).copyOf()
        val run = if (pool == "idle") run().toSet() else emptySet()     // 连续空闲抽取里抽到过的 (V 除外) 不再抽
        var usable = w.indices.filter { w[it] > 0 && Prescript.TYPES[it] !in run && available(Prescript.TYPES[it], now) }
        if (usable.isEmpty()) usable = w.indices.filter { w[it] > 0 }
        if (usable.isEmpty()) usable = w.indices.toList()
        val lost = w.indices.filter { w[it] > 0 && it !in usable }.sumOf { w[it] }
        for (i in w.indices) if (i !in usable) w[i] = 0.0
        if (lost > 0) for (i in usable) w[i] += lost / usable.size
        return w
    }

    /** 设备现在的时间落在这个词条的时间段里吗. */
    fun inWindow(kind: String, now: LocalDateTime): Boolean {
        val minute = now.hour * 60 + now.minute
        val windows = try { windowsFn(kind) } catch (_: Exception) { Cfg.defaultWindows(kind) }
        return windows.any { minute >= it.first && minute < it.second }
    }

    /**
     * 现在概率被抬高的空闲词条们: 时间段对得上、而且这一轮还没抽到过的 (V 可以重复, 不看这一轮);
     * 时段有重叠就有好几个, 没有就是 V (空闲5min).
     */
    @Synchronized
    fun idleBoosts(now: LocalDateTime = LocalDateTime.now()): List<String> {
        val run = run().toSet()
        val hit = Prescript.IDLE_KINDS.filter { (it == "V" || it !in run) && inWindow(it, now) }
        return hit.ifEmpty { listOf("V") }
    }

    /** 空闲池里 V 这一格的概率 (mass) 拆给各个空闲词条: 抬高的 (重叠就几个均分) 拿 80%, 其余还能抽的平分剩下的. */
    private fun idleSplit(mass: Double, now: LocalDateTime): List<Pair<String, Double>> {
        val run = run().toSet()
        val cands = Prescript.IDLE_KINDS.filter { it == "V" || it !in run }
        val boosted = idleBoosts(now)
        val others = cands.filter { it !in boosted }
        if (others.isEmpty()) return boosted.map { it to mass / boosted.size }
        return boosted.map { it to mass * Prescript.IDLE_BOOST / boosted.size } +
            others.map { it to mass * (1.0 - Prescript.IDLE_BOOST) / others.size }
    }

    /** 这个池子现在每条指令的概率; 空闲池里 V 那一格已经拆给各个空闲词条. */
    @Synchronized
    fun choices(pool: String, now: LocalDateTime = LocalDateTime.now()): List<Pair<String, Double>> {
        val w = weights(pool, now)
        val out = ArrayList<Pair<String, Double>>()
        for ((i, kind) in Prescript.TYPES.withIndex()) {
            if (w[i] <= 0) continue
            if (kind == "V" && pool == "idle") out.addAll(idleSplit(w[i], now)) else out.add(kind to w[i])
        }
        return out
    }

    private fun pick(pool: String, now: LocalDateTime): String {
        val options = choices(pool, now)
        val roll = rng.nextDouble()
        var acc = 0.0
        for ((kind, weight) in options) {
            acc += weight
            if (roll < acc) return kind
        }
        return options.lastOrNull()?.first ?: "I"
    }

    // ---- 领取 ----

    /** 领取一条指令; 领不了时返回 null. choose / pool 只给自检用. */
    @Synchronized
    fun draw(now0: LocalDateTime = LocalDateTime.now(), choose: String? = null, pool0: String? = null): JSONObject? {
        val now = now0.withNano(0)
        if (!canDraw(now)) return null
        ensureDay(now)
        // 上一条不作废, 留在后面继续判定 (同时最多 KEEP 条, canDraw 已经拦住了).
        current?.let {
            data.getJSONArray("extra").put(it)
            data.put("current", JSONObject.NULL)
        }
        var pool = pool0
        if (pool == null) {
            // 上一次空闲池抽取触发了: 这一次改抽专注池 (不看当时的状态).
            pool = if (data.optBoolean("force_focus")) "focus" else poolNow()
            data.put("force_focus", false)
        }
        if (pool == "idle" && data.optString("last_pool") != "idle") data.put("run", JSONArray())   // 重新进入空闲池: 这一轮重新算
        val kind = if (choose != null && choose in Prescript.ALL_TYPES) choose else pick(pool, now)
        val daily = ensureDay(now)
        var n: Int? = null
        val body = when (kind) {
            "I" -> "接下来的1h专注50min."
            "II" -> { n = daily.optInt("n_ii", 1); "完成一次至少${n}级的合约." }
            "III" -> { n = Prescript.restMinutes(try { remainingRestFn() } catch (_: Exception) { 0.0 }); "休息${n}min." }
            "IV" -> { n = daily.optInt("n_iv", 5); "今天专注至少$n h" }
            in Prescript.IDLE_ENTRIES -> Prescript.IDLE_ENTRIES.getValue(kind).body
            else -> "检查你的统计数据,并作简要反思."
        }
        val idle = Prescript.IDLE_ENTRIES[kind]
        // 空闲词条没有作废时限, 只有自动完成的时刻 due; 其他指令到 expires 作废
        val expires: LocalDateTime? = when {
            kind == "IV" -> now.toLocalDate().plusDays(1).atStartOfDay()
            idle != null -> null
            else -> now.plusSeconds(Prescript.EXPIRY_SECONDS[kind] ?: 3600L)
        }
        val due: LocalDateTime? = if (idle != null && idle.minutes > 0) now.plusMinutes(idle.minutes.toLong()) else null
        val cur = JSONObject()
            .put("id", now.format(java.time.format.DateTimeFormatter.ofPattern("yyyyMMddHHmmss")) + "-%03d".format(rng.nextInt(1000)))
            .put("type", kind).put("body", body)
            .put("accepted", now.iso()).put("expires", expires?.iso() ?: JSONObject.NULL)
            .put("due", due?.iso() ?: JSONObject.NULL)
            .put("pool", pool).put("n", n ?: JSONObject.NULL)
            // III 的 t (小时) = 休息时间 (小时) × 2; 其他是固定值
            .put("t", if (kind == "III") (n ?: 1) / 60.0 * 2 else Prescript.FIXED_T[kind] ?: 0.0)
            .put("focus_sec", 0.0).put("break_sec", 0.0).put("stats_since", JSONObject.NULL)
        data.put("current", cur)
        data.put("last_pool", pool).put("last_type", kind)
        if (pool == "idle") {
            if (kind != "V") data.getJSONArray("run").put(kind)
            // 每次空闲池抽取都有一定概率让下一次改抽专注池, 没触发概率 +25%, 触发后回到 25%
            val chance = data.optDouble("focus_chance", Prescript.FOCUS_CHANCE_START)
            if (rng.nextDouble() < chance) {
                data.put("force_focus", true).put("focus_chance", Prescript.FOCUS_CHANCE_START)
            } else {
                data.put("focus_chance", minOf(1.0, chance + Prescript.FOCUS_CHANCE_STEP))
            }
        }
        lastTick = now
        dirty = true
        save()
        if (kind in Prescript.IDLE_ENTRIES) try { pauseMediaFn?.invoke() } catch (_: Exception) {}
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
            addReward(JSONObject().put("id", rec.getString("id"))
                .put("kind", "complete").put("code", Prescript.codeFor(done)).put("t", done)
                .put("seconds", Math.round(done * 180).toInt()).put("at", now.iso()))
            recomputeDaily(now)
            checkDailyBonus(now)
        }
        dirty = true
        if (save) save()
        if (done != null && settle && page() == "prescript") settleRewards(now)
        if (notify) changed()
        if (done != null) try { onComplete?.invoke() } catch (_: Exception) {}
    }

    // ---- 判定 ----

    /** 空闲词条: 页面 / 界面刚一变化就判一次 (不用等下一秒的 tick). */
    @Synchronized
    fun checkVisible(now0: LocalDateTime = LocalDateTime.now()) {
        if (onPage() || peersOnPage()) return
        val now = now0.withNano(0)
        for (cur in actives()) {
            if (cur.optString("type") in Prescript.IDLE_ENTRIES) finish("void", now, cur = cur)
        }
    }

    /** 切页 / 退到后台时调用: 空闲词条立即作废, VI 的连续停留立即清零. */
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
            in Prescript.IDLE_ENTRIES -> {
                val here = onPage()
                if (!here && !peersOnPage()) { finish("void", now, cur = cur); return }   // 所有设备都离开了指令页 / 退到后台: 作废
                // 自动完成的时刻 (旧存档里的 V 只有 expires); X 没有计时, 等输入框保存.
                // 只有停在指令页的设备去完成 (别的设备还停在页面上时, 由它来完成)
                val due = parseIso(cur.optString("due")) ?: expires
                if (here && due != null && !now.isBefore(due)) {
                    finish("completed", now, cur.optDouble("t", Prescript.FIXED_T[kind] ?: 0.1), cur = cur); return
                }
            }
            "I" -> {
                if (stateFn() == "focus") { cur.put("focus_sec", cur.optDouble("focus_sec", 0.0) + gained()); dirty = true }
                if (cur.optDouble("focus_sec", 0.0) >= Prescript.FOCUS_TARGET) { finish("completed", now, 1.0, cur = cur); return }
                if (expired) { finish("expired", now, cur = cur); return }
            }
            "III" -> {
                if (stateFn() == "break") { cur.put("break_sec", cur.optDouble("break_sec", 0.0) + gained()); dirty = true }
                val target = if (cur.isNull("n")) Prescript.BREAK_TARGET else cur.optInt("n", 0) * 60.0
                if (cur.optDouble("break_sec", 0.0) >= target) { finish("completed", now, cur.optDouble("t", 1.0), cur = cur); return }
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

    /** 本机此刻是不是停在指令页上 (没有最小化 / 退到后台). */
    fun onPage(): Boolean = page() == "prescript" && !minimized()

    private fun peersOnPage(): Boolean = try { peersOnPageFn() } catch (_: Exception) { false }

    // ---- 奖励 / 当天合计 (跟着记录走, 多台设备算出来一样) ----

    private fun rewardId(item: JSONObject): String =
        item.optString("id").ifEmpty { "legacy-${item.optString("at")}-${item.optString("code")}" }

    private fun hasReward(id: String): Boolean =
        data.getJSONObject("settled").has(id) || data.getJSONArray("pending").objects().any { rewardId(it) == id }

    /** 排进待结算; 同一个 id (别的设备也完成了同一条) 已经有了就不重复. */
    private fun addReward(item: JSONObject): Boolean {
        val id = rewardId(item)
        item.put("id", id)
        if (hasReward(id)) return false
        data.getJSONArray("pending").put(item)
        return true
    }

    /** 今天完成的 t 合计 / IV 完成过 / 20 分钟奖励给过没有: 从记录里算, 多台设备同步之后也一样. */
    private fun recomputeDaily(now: LocalDateTime) {
        val daily = data.getJSONObject("daily")
        val day = daily.optString("date").ifEmpty { now.toLocalDate().key() }
        var total = 0.0
        var iv = false
        for (rec in data.getJSONArray("history").objects()) {
            if (rec.optString("result") == "completed" && rec.optString("ended").startsWith(day)) {
                total += rec.optDouble("t", 0.0)
                if (rec.optString("type") == "IV") iv = true
            }
        }
        daily.put("completed_t", total).put("iv_done", iv).put("bonus", hasReward("daily-$day"))
    }

    /** 今天的 t 合计到 10: 20 分钟当场排进待结算 (换日后打开页面也不会丢). */
    private fun checkDailyBonus(now: LocalDateTime) {
        val daily = ensureDay(now)
        val day = daily.optString("date")
        if (!daily.optBoolean("bonus") && daily.optDouble("completed_t", 0.0) >= Prescript.DAILY_T_TARGET - 1e-9) {
            daily.put("bonus", true)
            addReward(JSONObject().put("id", "daily-$day").put("kind", "daily").put("code", Prescript.restCode(20.0))
                .put("t", JSONObject.NULL).put("seconds", Prescript.DAILY_BONUS_SECONDS).put("at", now.iso()))
            dirty = true
        }
    }

    // ---- 设备同步 (SyncService 的 "prescript" 文档) ----
    // 同步的是: 领到的指令 (进行中的和已经完成 / 作废的; 各设备自己累计的进度不同步), 奖励 (结算过没有), 今天的 n.
    // 已经结束的记录永远盖过进行中的 (SyncService.wins); 其他按修改时间.

    private fun staticView(rec: JSONObject): JSONObject {
        val out = JSONObject(rec.toString())
        for (k in Prescript.SYNC_VOLATILE) out.remove(k)
        return out
    }

    /** 统计页的日志上一次被看的时刻 (ISO, 没看过 = ""); 随同步, 几台设备共用 2 小时的冷却 (MECHANICS 8.7). */
    @Synchronized
    fun journalView(): String = data.optString("journal_view", "")

    @Synchronized
    fun noteJournal(now0: LocalDateTime = LocalDateTime.now()) {
        data.put("journal_view", now0.withNano(0).iso())
        dirty = true
        save()
    }

    /** 本机进入 / 退出了指令页: 记成一个事件, 随同步发给其他设备, 它们跟着进入 / 退出 (调用的人负责马上同步一次). */
    @Synchronized
    fun notePage(on: Boolean, deviceId: String, now0: LocalDateTime = LocalDateTime.now()) {
        pageRec = JSONObject().put("on", on).put("at", now0.withNano(0).iso()).put("by", deviceId)
    }

    /** 别的设备发来的进入 / 退出指令页的事件: 记下 (本机之后不会又发回去), 够新的话让本机跟着做. */
    private fun adoptPage(value: JSONObject, now: LocalDateTime) {
        val on = value.optBoolean("on")
        val at = value.optString("at")
        val by = value.optString("by")
        val cur = pageRec
        if (cur != null && cur.optBoolean("on") == on && cur.optString("at") == at && cur.optString("by") == by) return
        pageRec = JSONObject().put("on", on).put("at", at).put("by", by)
        val t = parseIso(at) ?: return
        if (Math.abs(secondsBetween(t, now)) > Prescript.PAGE_EVENT_FRESH) return
        try { pageEventFn?.invoke(on) } catch (_: Exception) {}
    }

    @Synchronized
    fun syncDoc(now0: LocalDateTime = LocalDateTime.now()): Map<String, Any?> {
        val now = now0.withNano(0)
        val floor = now.minusDays(Prescript.SYNC_DAYS).iso()
        val out = LinkedHashMap<String, Any?>()
        for (rec in data.getJSONArray("history").objects()) {
            if (rec.optString("id").isNotEmpty() && rec.optString("accepted") >= floor)
                out["i:" + rec.getString("id")] = JSONObject(rec.toString())
        }
        for (cur in actives()) if (cur.optString("id").isNotEmpty()) out["i:" + cur.getString("id")] = staticView(cur)
        for (item in data.getJSONArray("pending").objects())
            out["r:" + rewardId(item)] = JSONObject(item.toString()).put("settled", false)
        val settled = data.getJSONObject("settled")
        for (id in settled.keys()) settled.optJSONObject(id)?.let { out["r:$id"] = JSONObject(it.toString()).put("settled", true) }
        val daily = data.getJSONObject("daily")
        if (daily.has("date")) out["n:" + daily.getString("date")] = JSONObject()
            .put("n_ii", daily.opt("n_ii") ?: JSONObject.NULL).put("n_iv", daily.opt("n_iv") ?: JSONObject.NULL)
        pageRec?.let { out["p:page"] = JSONObject(it.toString()) }
        if (journalView().isNotEmpty()) out["j:view"] = JSONObject().put("at", journalView())
        return out
    }

    private fun historyIds(): Set<String> = data.getJSONArray("history").objects().map { it.optString("id") }.toSet()

    /** 别的设备发来的改动并进来 (界面线程). 完成 / 作废不再给奖励 (奖励有自己的一项), 也不响. */
    @Synchronized
    fun syncApply(changes: Map<String, Any?>, now0: LocalDateTime = LocalDateTime.now()) {
        val now = now0.withNano(0)
        val floor = now.minusDays(Prescript.SYNC_DAYS).iso()
        var touched = false
        for ((key, v) in changes) {
            val value = v as? JSONObject ?: continue
            val kind = key.substringBefore(':')
            val ident = key.substringAfter(':', "")
            when (kind) {
                "i" -> touched = adoptInstruction(ident, value, floor) || touched
                "r" -> touched = adoptReward(ident, value) || touched
                "p" -> if (ident == "page") adoptPage(value, now)
                "j" -> if (ident == "view" && value.optString("at") > journalView()) {     // 几台设备谁看得最晚, 冷却就从谁算
                    data.put("journal_view", value.optString("at")); touched = true
                }
                "n" -> if (ident == data.getJSONObject("daily").optString("date")) {
                    val daily = data.getJSONObject("daily")
                    for (k in listOf("n_ii", "n_iv")) if (value.has(k) && !value.isNull(k) && daily.optInt(k, -1) != value.optInt(k)) {
                        daily.put(k, value.optInt(k)); touched = true
                    }
                }
            }
        }
        if (touched) {
            recomputeDaily(now)
            dirty = true
            save()
            changed()
        }
    }

    private fun adoptInstruction(ident: String, value: JSONObject, floor: String): Boolean {
        if (ident.isEmpty() || value.optString("accepted") < floor) return false
        if (ident in historyIds()) return false
        val local = actives().firstOrNull { it.optString("id") == ident }
        if (value.optString("result").isNotEmpty()) {
            if (local != null) {
                val extra = data.getJSONArray("extra")
                if (local === current) {
                    if (extra.length() > 0) data.put("current", extra.remove(extra.length() - 1))
                    else data.put("current", JSONObject.NULL)
                } else data.put("extra", JSONArray(extra.objects().filter { it !== local }))
            }
            val rec = JSONObject()
            for (k in listOf("id", "type", "body", "accepted", "ended", "result", "t", "code")) rec.put(k, value.opt(k) ?: JSONObject.NULL)
            val history = JSONArray((data.getJSONArray("history").objects() + rec).sortedBy { it.optString("ended") })
            while (history.length() > Prescript.HISTORY_KEEP) history.remove(0)
            data.put("history", history)
            return true
        }
        if (local != null) return false
        val cur = JSONObject(value.toString()).put("focus_sec", 0.0).put("break_sec", 0.0).put("stats_since", JSONObject.NULL)
        val acts = (actives() + cur).sortedWith(compareBy({ it.optString("accepted") }, { it.optString("id") }))
        data.put("current", acts.last())
        data.put("extra", JSONArray(acts.dropLast(1)))
        return true
    }

    private fun adoptReward(ident: String, value: JSONObject): Boolean {
        val item = JSONObject(value.toString()).also { it.remove("settled"); it.put("id", ident) }
        if (value.optBoolean("settled", false)) {
            val pending = data.getJSONArray("pending")
            val keep = pending.objects().filter { rewardId(it) != ident }
            val known = data.getJSONObject("settled").has(ident)
            if (keep.size != pending.length()) data.put("pending", JSONArray(keep))
            if (!known) data.getJSONObject("settled").put(ident, item)
            return !known || keep.size != pending.length()
        }
        return addReward(item)
    }

    // ---- X: 记录输入框 ----

    @Synchronized
    fun activeOf(kind: String): JSONObject? = actives().firstOrNull { it.optString("type") == kind }

    /** OIT 被手动关闭了 (X 的输入框第三段要写今天的这些时刻). */
    @Synchronized
    fun noteClose(now0: LocalDateTime = LocalDateTime.now()) {
        val closes = data.getJSONArray("closes")
        closes.put(now0.withNano(0).iso())
        while (closes.length() > 50) closes.remove(0)
        dirty = true
        save()
    }

    private fun todayInstructionLines(now: LocalDateTime): List<String> {
        val day = now.toLocalDate().key()
        val rows = ArrayList<Triple<String, String, String>>()
        for (rec in data.getJSONArray("history").objects()) {
            if (rec.optString("accepted").startsWith(day))
                rows.add(Triple(rec.optString("accepted"), rec.optString("body"), if (rec.optString("result") == "completed") "完成" else "废弃"))
        }
        for (cur in actives()) {
            if (cur.optString("accepted").startsWith(day)) rows.add(Triple(cur.optString("accepted"), cur.optString("body"), "进行中"))
        }
        return rows.sortedBy { it.first }.map { (accepted, body, word) ->
            val at = parseIso(accepted)
            val head = if (at != null) "%02d:%02d  ".format(at.hour, at.minute) else ""
            "$head${tr(body)} (${tr(word)})"
        }
    }

    private fun todayCloseLines(now: LocalDateTime): List<String> {
        val day = now.toLocalDate().key()
        return data.getJSONArray("closes").strings().mapNotNull { v ->
            parseIso(v)?.takeIf { it.toLocalDate().key() == day }?.let { "%02d:%02d".format(it.hour, it.minute) }
        }
    }

    /**
     * X 的输入框一开始的文字: 三段 -- 今天的整点记录 (没启用是空白) / 今天领到的指令和状态 /
     * 今天 OIT 被手动关闭的时刻. 第一次生成时存下来, 保存时拿它判断用户改没改.
     */
    @Synchronized
    fun logPrefill(now: LocalDateTime = LocalDateTime.now()): String {
        val cur = activeOf("X")
        if (cur != null && cur.has("log_base")) return cur.optString("log_base")
        val hour = try { hourLogFn().trim() } catch (_: Exception) { "" }
        val text = listOf(hour, todayInstructionLines(now).joinToString("\n"), todayCloseLines(now).joinToString("\n"))
            .joinToString("\n\n")
        if (cur != null) { cur.put("log_base", text); dirty = true }
        return text
    }

    private fun normLog(text: String): String = text.replace("\r\n", "\n").trim()

    /** 保存 X 的输入框: 一点没改视为作废, 改过视为完成 (文字存进 logs). 返回 "completed" / "void" / null. */
    @Synchronized
    fun submitLog(text: String, now0: LocalDateTime = LocalDateTime.now()): String? {
        val cur = activeOf("X") ?: return null
        val now = now0.withNano(0)
        val base = if (cur.has("log_base")) cur.optString("log_base") else logPrefill(now)
        if (normLog(text) == normLog(base)) {
            finish("void", now, cur = cur)
            return "void"
        }
        val logs = data.getJSONObject("logs")
        logs.put(now.toLocalDate().key(), text)
        logs.keys().asSequence().toList().sorted().dropLast(Prescript.LOG_KEEP_DAYS).forEach { logs.remove(it) }
        finish("completed", now, cur.optDouble("t", Prescript.FIXED_T["X"] ?: 0.1), cur = cur)
        return "completed"
    }

    /** 输入框没保存就关了: 等于没改, 作废. */
    @Synchronized
    fun abandonLog(now0: LocalDateTime = LocalDateTime.now()) {
        val cur = activeOf("X") ?: return
        finish("void", now0.withNano(0), cur = cur)
    }

    // ---- 结算休息时间 ----

    /** 打开指令页时调用: 把满足条件的休息时间加进计时器, 返回这次给出的奖励. */
    @Synchronized
    fun settleRewards(now0: LocalDateTime = LocalDateTime.now()): List<JSONObject> {
        val now = now0.withNano(0)
        checkDailyBonus(now)
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
            data.getJSONObject("settled").put(rewardId(item), JSONObject(item.toString()))     // 其他设备看到就不再结算
        }
        data.put("pending", JSONArray())
        val settled = data.getJSONObject("settled")
        settled.keys().asSequence().toList().sortedBy { settled.getJSONObject(it).optString("at") }
            .dropLast(Prescript.GRANTED_KEEP).forEach { settled.remove(it) }
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
