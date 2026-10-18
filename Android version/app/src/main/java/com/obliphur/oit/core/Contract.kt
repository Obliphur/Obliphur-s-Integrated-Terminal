package com.obliphur.oit.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.LocalDateTime

/** 词条: id, 组 (同一组互相冲突, 界面上排在同一列), 等级, 名字, 描述, 另外冲突的词条. */
class Term(val id: String, val group: String, val level: Int, val name: String, val desc: String,
           val conflicts: List<String> = emptyList())

/**
 * 合约 (MECHANICS 18). 对应桌面版 contract.py.
 * 存档 contract.json: 进行中的合约 (live), 结束了的合约 (history, 整点记录的锁定也看它), 没开始的草稿 (draft).
 * live 和 history 随设备同步, draft 只留在本机.
 */
class Contract(private val file: File) {
    companion object {
        const val BASE_SECONDS = 4 * 3600.0
        const val IDLE_LIMIT = 30 * 60.0
        const val VIOLATION_LIMIT = 3
        const val HISTORY_KEEP = 30
        const val HARD_LEVEL = 13

        val TERMS = listOf(
            Term("long1", "long", 1, "环境:持久战-I", "时长延长为1.1倍"),
            Term("long2", "long", 2, "环境:持久战-II", "时长延长为1.2倍"),
            Term("long3", "long", 3, "环境:持久战-III", "时长延长为1.3倍"),
            Term("move1", "move", 1, "目标:高机动-I", "现实中的移动速度加快25%"),
            Term("move2", "move", 2, "目标:高机动-II", "现实中的移动速度加快50%"),
            Term("info1", "info", 1, "环境:信息阻断-I", "只能使用剩余联网时间的75% 且不能使用借贷"),
            Term("info2", "info", 2, "环境:信息阻断-II", "只能使用剩余联网时间的50% 且不能使用借贷"),
            Term("info3", "info", 3, "环境:信息阻断-III", "只能使用剩余联网时间的25% 且不能使用借贷"),
            Term("isolate", "info", 4, "环境:信息隔绝", "不能使用联网功能", listOf("chan1", "chan2")),
            Term("suppress", "suppress", 2, "环境:抑制", "不能使用快速访问功能", listOf("isolate")),
            Term("jam", "jam", 2, "环境:全频道阻塞干扰", "期间使用proxy block-all模式"),
            Term("music", "music", 2, "环境:乐理之灾", "期间不能打开音乐播放软件 不能使用外部软件播放mp3等音频文件"),
            Term("fog", "fog", 1, "环境:黑雾", "期间不能查看统计数据 且在专注时无法看到专注时长"),
            Term("tired1", "tired", 1, "环境:疲惫-I", "期间获得的休息时间为0.9x"),
            Term("tired2", "tired", 2, "环境:疲惫-II", "期间获得的休息时间为0.75x"),
            Term("tired3", "tired", 3, "环境:疲惫-III", "期间获得的休息时间为0.6x"),
            Term("chan1", "chan", 1, "环境:通道阻塞-I", "期间的最大窗口数改为4"),
            Term("chan2", "chan", 2, "环境:通道阻塞-II", "期间的最大窗口数改为3"),
            Term("last", "last", 1, "最后防线", "违反一次词条即结束合约"),
        )
        val BY_ID = TERMS.associateBy { it.id }
        val GROUPS = TERMS.map { it.group }.distinct()
        val MAX_LEVEL = TERMS.maxOf { it.level }
        private val ORDER = TERMS.mapIndexed { i, t -> t.id to i }.toMap()

        // 用"不能" / "禁止"描述的词条是软规则: 界面上照样能做, 但会被算作违反, 这个词条降一级 (结算按降级后的等级);
        // 用"无法"描述的 (黑雾) 是硬规则, 立即生效; 全频道阻塞干扰 / 持久战 / 疲惫直接生效.
        val SOFT = setOf("info1", "info2", "info3", "isolate", "chan1", "chan2", "music", "suppress")
        /** 降一级换成哪个词条. */
        val LOWER = mapOf("isolate" to "info3", "info3" to "info2", "info2" to "info1", "chan2" to "chan1")
        /** 只有一档的词条: 降级时等级减一, 减到 0 就失效. */
        val SINGLE = setOf("music", "suppress")

        val DURATION = mapOf("long1" to 1.1, "long2" to 1.2, "long3" to 1.3)
        val NET_SHARE = mapOf("info1" to 0.75, "info2" to 0.5, "info3" to 0.25, "isolate" to 0.0)
        val REST = mapOf("tired1" to 0.9, "tired2" to 0.75, "tired3" to 0.6)
        val WINDOWS = mapOf("chan1" to 4, "chan2" to 3)

        /** 总等级 → 完成后的休息时间 (分钟, 20 分钟 = ×1); 倍率 = 分钟 / 20, 和"休息时间倍率"相乘. */
        private val REST_TABLE = listOf(0 to 20, 1 to 24, 4 to 28, 7 to 32, 10 to 36, 13 to 40, 16 to 44, 19 to 50)

        /** 乐理之灾: 常见的音乐播放应用 (包名). */
        val MUSIC_APPS = setOf(
            "com.netease.cloudmusic", "com.tencent.qqmusic", "com.kugou.android", "com.kugou.android.lite",
            "cn.kuwo.player", "cn.kuwo.kwmusichd", "com.spotify.music", "com.apple.android.music",
            "com.google.android.apps.youtube.music", "com.miui.player", "com.android.music",
            "com.sec.android.app.music", "com.huawei.music", "com.hihonor.cloudmusic", "com.android.mediacenter",
            "com.oppo.music", "com.heytap.music", "com.android.bbkmusic", "com.meizu.media.music",
            "com.tencent.blackkey", "cmccwm.mobilemusic", "com.luna.music", "com.ximalaya.ting.android",
            "com.soundcloud.android", "com.aspiro.tidal", "deezer.android.app", "com.amazon.mp3",
            "in.krosbits.musicolet", "com.maxmpz.audioplayer", "org.videolan.vlc", "com.poweramp.v3",
            "com.foobar2000.foobar2000", "com.salt.music", "com.kanjian.music",
        )

        fun levelOf(terms: Collection<String>) = terms.sumOf { BY_ID[it]?.level ?: 0 }

        fun restMinutes(level: Int): Int = REST_TABLE.last { level >= it.first }.second
        fun restMult(level: Int): Double = restMinutes(level) / 20.0

        fun durationOf(terms: Collection<String>): Double =
            BASE_SECONDS * (terms.mapNotNull { DURATION[it] }.maxOrNull() ?: 1.0)

        fun conflicts(a: String, b: String): Boolean {
            if (a == b) return false
            val ta = BY_ID[a] ?: return false
            val tb = BY_ID[b] ?: return false
            return ta.group == tb.group || b in ta.conflicts || a in tb.conflicts
        }

        /** 选了 selected 之后, id 和其中哪一个冲突 (没有冲突返回 null). */
        fun blockedBy(id: String, selected: Collection<String>): String? =
            selected.firstOrNull { it != id && conflicts(id, it) }

        /** 去掉不认识的和互相冲突的 (后面的让位给前面的), 按 TERMS 的顺序. */
        fun clean(terms: Collection<String>): List<String> {
            val out = mutableListOf<String>()
            for (t in terms) if (t in BY_ID && t !in out && blockedBy(t, out) == null) out.add(t)
            return out.sortedBy { ORDER[it] }
        }

        /** 完成合约后的第一次休息: 暂停时的结转比例 1 - (1 - x) / 2. */
        fun firstBreakRate(x: Double): Double = 1.0 - (1.0 - x.coerceIn(0.0, 1.0)) / 2.0
    }

    var live: JSONObject = JSONObject().put("state", "none")
        private set
    val history = mutableListOf<JSONObject>()
    var draft: List<String> = emptyList()
        private set
    /** 重置中: 不写文件. */
    var blocked: () -> Boolean = { false }

    init {
        val d = Store.readObj(file)
        if (d != null) {
            d.optJSONObject("live")?.let { if (it.optString("state") in setOf("active", "none")) live = it }
            if (active) live.put("terms", JSONArray(clean(live.optJSONArray("terms")?.strings() ?: emptyList())))
            d.optJSONArray("history")?.objects()?.filter { it.has("start") }?.let { history.addAll(it) }
            draft = clean(d.optJSONArray("draft")?.strings() ?: emptyList())
        }
    }

    fun save() {
        if (blocked()) return
        history.sortBy { it.optString("end") }
        while (history.size > HISTORY_KEEP) history.removeAt(0)
        Store.write(file, JSONObject().put("live", live).put("history", JSONArray(history)).put("draft", JSONArray(draft)))
    }

    // ---- 进行中的合约 ----

    val active get() = live.optString("state") == "active"
    val terms: List<String> get() = if (active) live.optJSONArray("terms")?.strings() ?: emptyList() else emptyList()
    fun has(id: String) = id in terms
    val startAt get() = if (active) parseIso(live.optString("start")) else null
    val endAt get() = if (active) parseIso(live.optString("end")) else null
    /** 只有一档的词条被降了几级. */
    private val cut: JSONObject get() = (if (active) live.optJSONObject("cut") else null) ?: JSONObject()
    fun termLevel(id: String) = (BY_ID[id]?.level ?: 0) - cut.optInt(id, 0)
    val level get() = terms.sumOf { termLevel(it) }
    /** 开始时选的词条 (降级前). */
    val orig: List<String> get() = if (active) clean(live.optJSONArray("orig")?.strings() ?: terms) else emptyList()
    val origLevel get() = levelOf(orig)
    val violations get() = if (active) live.optInt("violations", 0) else 0
    val violationLimit get() = if (has("last")) 1 else VIOLATION_LIMIT
    /** 合约中已经给出的休息时间 (秒, 已乘疲惫). */
    val rest get() = if (active) live.dbl("rest") else 0.0
    /** 完成时多给的休息时间 (秒) = 合约中给出的休息时间 × (按现在的等级的倍率 - 1); 结算按降级后的等级. */
    val reward get() = rest * (restMult(level) - 1.0)
    val owner: String get() = live.optString("owner", "")
    val id: Long get() = live.optLong("id", 0)

    fun setDraft(terms: Collection<String>) {
        draft = clean(terms)
        save()
    }

    fun start(terms: Collection<String>, owner: String, now: LocalDateTime = LocalDateTime.now()) {
        val t = clean(terms)
        val n = now.withNano(0)
        live = JSONObject().put("state", "active").put("id", System.currentTimeMillis())
            .put("start", n.iso()).put("end", n.plusSec(durationOf(t)).iso())
            .put("terms", JSONArray(t)).put("orig", JSONArray(t)).put("cut", JSONObject())
            .put("violations", 0).put("rest", 0).put("owner", owner)
            .put("idle_since", JSONObject.NULL).put("net", JSONObject.NULL).put("first_break", false)
        watch.clear()
        save()
    }

    /** 结束合约: result = completed / failed / idle / abandoned. 返回这一条记录 (含给出的休息时间). */
    fun finish(result: String, at: LocalDateTime? = null, by: String = ""): JSONObject? {
        if (!active) return null
        val now = (at ?: LocalDateTime.now()).withNano(0)
        val plan = endAt ?: now
        val end = if (now.isBefore(plan)) now else plan
        val give = if (result == "completed") Math.round(reward).toInt() else 0
        val rec = JSONObject().put("id", live.optLong("id")).put("start", live.optString("start")).put("end", end.iso())
            .put("planned", live.optString("end")).put("terms", JSONArray(terms)).put("orig", JSONArray(orig))
            .put("level", level).put("orig_level", origLevel).put("cut", JSONObject(cut.toString()))
            .put("result", result).put("violations", violations).put("reward", give)
        history.add(rec)
        live = JSONObject().put("state", "none").put("owner", by).put("first_break", result == "completed")
        save()
        return rec
    }

    /** 最近结束的一个合约. */
    val last: JSONObject? get() = history.maxByOrNull { it.optString("end") }

    // ---- 到期 / 空闲 ----

    /** 该结束了吗: ("completed" / "idle", 结束的时刻) 或 null. 同时记下空闲从什么时候开始 (只由做自动结束的设备调用). */
    fun due(now: LocalDateTime, timerIdle: Boolean): Pair<String, LocalDateTime>? {
        if (!active) return null
        val end = endAt ?: return null
        var since = parseIso(live.strOrNull("idle_since"))
        if (timerIdle) {
            if (since == null) {
                since = now
                live.put("idle_since", now.iso())
                save()
            }
        } else if (since != null) {
            live.put("idle_since", JSONObject.NULL)
            save()
            since = null
        }
        if (since != null) {
            val fail = since.plusSec(IDLE_LIMIT)
            if (!fail.isAfter(if (now.isBefore(end)) now else end)) return "idle" to fail
        }
        if (!now.isBefore(end)) return "completed" to end
        return null
    }

    /** 空闲还能持续多久 (秒); 不在空闲时 null. */
    fun idleLeft(now: LocalDateTime): Double? {
        val since = if (active) parseIso(live.strOrNull("idle_since")) else null
        return since?.let { maxOf(0.0, IDLE_LIMIT - secondsBetween(it, now)) }
    }

    /** 违反了词条 id: 记一次, 这个词条降一级. 返回 (是否达到了上限, 降级后的词条 / "" = 失效). */
    fun violate(id: String): Pair<Boolean, String> {
        if (!active) return false to ""
        val new = downgrade(id)
        live.put("violations", violations + 1)
        save()
        return (violations >= violationLimit) to new
    }

    /** 词条降一级: 系列词条换成低一档的, 只有一档的等级减一; 降到底就失效. 返回降级后的词条 ("" = 失效). */
    fun downgrade(id: String): String {
        val t = terms.toMutableList()
        val i = t.indexOf(id)
        if (i < 0) return ""
        val oldShare = netShare()
        val c = JSONObject(cut.toString())
        val new: String
        when {
            id in LOWER -> { new = LOWER[id]!!; t[i] = new }
            id in SINGLE && termLevel(id) > 1 -> { c.put(id, c.optInt(id, 0) + 1); new = id }
            else -> { t.removeAt(i); c.remove(id); new = "" }
        }
        live.put("terms", JSONArray(clean(t))).put("cut", c)
        // 信息阻断: 降一级 = 多给低一档多出来的那部分额度 (从现在用到的地方算起)
        val n = live.optJSONObject("net")
        val newShare = netShare()
        if (n != null && oldShare != null && newShare != null)
            n.put("limit", n.dbl("limit") + n.dbl("base") * (newShare - oldShare))
        save()
        return new
    }

    // ---- 词条的效果 ----

    /** 只能用剩余联网时间的几成 (null = 不限). */
    fun netShare(): Double? = terms.mapNotNull { NET_SHARE[it] }.minOrNull()
    fun fog() = has("fog")
    fun windowLimit(): Int = terms.mapNotNull { WINDOWS[it] }.minOrNull() ?: 0
    fun restFactor(): Double = terms.mapNotNull { REST[it] }.minOrNull() ?: 1.0

    // ---- 软规则: 有没有违反 (由调用方算作违反: violate) ----

    fun netTerm(): String = terms.firstOrNull { it in NET_SHARE } ?: ""

    /** 信息阻断 / 信息隔绝: 合约开始时 (以及合约中换日时) 记下当时的剩余额度, 只能用它的几成, 不能借贷.
     *  返回 (要降级的词条, "loan" / "over"), 没有违反时 null (只由做自动结束的设备调用, 额度是各设备共用的). */
    fun netCheck(used: Double, bonus: Double, raw: Double, date: String): Pair<String, String>? {
        val id = netTerm().ifEmpty { return null }
        val n = live.optJSONObject("net")
        if (n == null || n.optString("date") != date) {
            val r = maxOf(0.0, raw)
            live.put("net", JSONObject().put("date", date).put("base", r1(r)).put("bonus", r1(bonus))
                .put("limit", r1(used + r * NET_SHARE[id]!!)))
            save()
            return null
        }
        if (bonus > n.dbl("bonus") + 0.5) {
            n.put("bonus", r1(bonus))
            save()
            return id to "loan"
        }
        if (used > n.dbl("limit") + 0.5) return id to "over"
        return null
    }

    private fun r1(x: Double) = Math.round(x * 10) / 10.0

    /** 合约还允许用多少联网额度 (秒); 没有信息阻断时 null. */
    fun netLeft(used: Double): Double? {
        if (netTerm().isEmpty()) return null
        val n = live.optJSONObject("net") ?: return null
        return maxOf(0.0, n.dbl("limit") - used)
    }

    /** 此刻是不是已经超了 (只记在本机): 只在刚越过的那一下算一次违反, 回到限额以内后再越过才再算. */
    private val watch = HashMap<String, Boolean>()
    private fun edge(key: String, over: Boolean): Boolean {
        val was = watch[key] ?: false
        watch[key] = over
        return over && !was
    }

    /** 乐理之灾: 开着音乐播放应用. */
    fun musicCheck(playing: Boolean): String = if (has("music") && edge("music", playing)) "music" else ""

    /** 抑制: 用了快速访问. */
    fun quickCheck(): String = if (has("suppress")) "suppress" else ""

    /** 一段专注换来的休息时间 (秒, 已乘"休息时间倍率"). 返回现在就给的部分 (乘了疲惫);
     *  按总等级多给的部分完成时才给 (reward). */
    fun bookRest(seconds: Double): Double {
        val now = maxOf(0.0, seconds) * restFactor()
        live.put("rest", r1(rest + now))
        save()
        return now
    }

    /** 完成之后才结束的专注, 开始于刚完成的合约里: 按那个合约的疲惫和倍率给. */
    fun bookRestAfter(seconds: Double, start: LocalDateTime): Double {
        val h = last ?: return seconds
        val hs = parseIso(h.optString("start"))
        val he = parseIso(h.optString("end"))
        if (h.optString("result") == "completed" && hs != null && he != null && !start.isBefore(hs) && start.isBefore(he)) {
            val t = h.optJSONArray("terms")?.strings() ?: emptyList()
            val f = t.mapNotNull { REST[it] }.minOrNull() ?: 1.0
            return seconds * f * restMult(h.optInt("level", levelOf(t)))
        }
        return seconds
    }

    /** 完成合约后的第一次休息: 用掉这个标记. */
    fun takeFirstBreak(): Boolean {
        if (active || !live.optBoolean("first_break", false)) return false
        live.put("first_break", false)
        save()
        return true
    }

    // ---- 整点记录的锁定 ----

    private fun periods(now: LocalDateTime): List<Pair<LocalDateTime, LocalDateTime>> {
        val out = history.mapNotNull { h ->
            val a = parseIso(h.optString("start")) ?: return@mapNotNull null
            val b = parseIso(h.optString("end")) ?: return@mapNotNull null
            a to b
        }.toMutableList()
        startAt?.let { out.add(it to now) }
        return out
    }

    /** 以 slotStart 开始的那一小时: 和合约有重叠, 并且它结束后又过了一小时 (进入了下一个时间段之后), 就不能再改. */
    fun hourLocked(slotStart: LocalDateTime, now: LocalDateTime = LocalDateTime.now()): Boolean {
        val slotEnd = slotStart.plusHours(1)
        if (now.isBefore(slotEnd.plusHours(1))) return false
        return periods(now).any { (a, b) -> a.isBefore(slotEnd) && slotStart.isBefore(b) }
    }

    // ---- 设备同步 ----

    fun doc(): Map<String, Any?> = mapOf("live" to JSONObject(live.toString()), "history" to JSONArray(history.map { JSONObject(it.toString()) }))

    /** 同步来的改动. 进行中的合约: 后改的覆盖先改的; 记录: 合并 (按 id). */
    fun merge(changes: Map<String, Any?>) {
        (changes["live"] as? JSONObject)?.let { l ->
            if (l.optString("state") in setOf("active", "none") && l.toString() != live.toString()) {
                live = JSONObject(l.toString())
                if (active) live.put("terms", JSONArray(clean(live.optJSONArray("terms")?.strings() ?: emptyList())))
            }
        }
        (changes["history"] as? JSONArray)?.let { arr ->
            val have = history.map { it.opt("id")?.toString() }.toMutableSet()
            for (h in arr.objects()) {
                val id = h.opt("id")?.toString()
                if (h.has("start") && id !in have) {
                    history.add(JSONObject(h.toString()))
                    have.add(id)
                }
            }
        }
        save()
    }
}

/** 合约结束的结果 → 显示的文字 (中文原文, 由 tr 翻译). */
fun contractResult(result: String): String = when (result) {
    "completed" -> "完成"
    "failed" -> "失败: 违反词条"
    "idle" -> "失败: 空闲超过 30 分钟"
    "abandoned" -> "提前结束"
    else -> ""
}
