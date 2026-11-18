package com.obliphur.oit.core

import org.json.JSONObject
import org.json.JSONArray
import com.obliphur.oit.ai.ToolProtocol
import java.time.Instant
import java.time.ZoneId
import kotlin.math.round

/** 每个聊天对象的情景模式、张力、情绪效价及冷静期；与桌面端使用相同字段。 */
data class CommInteraction(
    val scenarioMode: Boolean = false, val tension: Double = 0.0, val shameTurns: Int = 0,
    val day: String = "", val ts: Long = 0L, val burstUntil: Long = 0L,
    val pending: Map<String, Boolean> = emptyMap(), val gainUntil: Long = 0L,
    val burstDays: List<String> = emptyList(), val morningDay: String = "",
    val gainCooldowns: Map<String, Long> = emptyMap(),
    val specialUntil: Long = 0L, val specialSpoke: Boolean = false, val skipDecay: Boolean = false,
    val decayAt: Long = 0L, val decayHoldUntil: Long = 0L, val burstSleepAfter: Long = 0L,
    val lastSpecialBegin: Long = 0L, val specialEntryTension: Double = 0.0,
    val special7mGainCount: Int = 0, val special7mLuredSeg: Long = 0L,
    val gainChainUntil: Long = 0L,
    val valence: Double = 0.0, val valenceDecayAt: Long = 0L,
    val valenceCooldowns: Map<String, Long> = emptyMap(), val valenceChainUntil: Long = 0L,
    val valenceInitialized: Boolean = false, val valenceInitial: Double = 0.0, val valenceInitialSet: Boolean = false,
    val offenses: List<CommOffense> = emptyList()
) {
    fun json(): JSONObject = JSONObject().put("scenario_mode", scenarioMode).put("tension", tension)
        .put("offenses", JSONArray(offenses.map { it.json() }))
        .put("valence", valence).put("valence_decay_at", valenceDecayAt)
        .put("valence_initialized", valenceInitialized).put("valence_initial", valenceInitial)
        .put("valence_initial_set", valenceInitialSet)
        .put("valence_cooldowns", JSONObject(valenceCooldowns)).put("valence_chain_until", valenceChainUntil)
        .put("shame_turns", shameTurns).put("day", day).put("ts", ts).put("burst_until", burstUntil)
        .put("pending", JSONObject(pending))
        .put("gain_until", gainUntil)
        .put("burst_days", JSONArray(burstDays)).put("morning_day", morningDay)
        .put("gain_cooldowns", JSONObject(gainCooldowns))
        .put("gain_chain_until", gainChainUntil)
        .put("special_until", specialUntil).put("special_spoke", specialSpoke).put("skip_decay", skipDecay)
        .put("decay_at", decayAt).put("decay_hold_until", decayHoldUntil)
        .put("burst_sleep_after", burstSleepAfter)
        .put("last_special_begin", lastSpecialBegin).put("special_entry_tension", specialEntryTension)
        .put("special_7m_gain_count", special7mGainCount).put("special_7m_lured_seg", special7mLuredSeg)

    /** 每十五分钟结算小数衰减；离线补算，暂停和回拨不补扣。 */
    fun roll(nowMs: Long, valenceBaseline: Double = valenceInitial): CommInteraction {
        val step = Cfg.COMM_TENSION_DECAY_SECONDS * 1000L
        var at = if (decayAt > 0L) decayAt else nowMs
        val hold = if (skipDecay) maxOf(decayHoldUntil, nowMs + 86400000L) else decayHoldUntil
        if (shameTurns > 0 || burstUntil > 0L || (specialUntil > 0L && !specialSpoke))
            at += maxOf(0L, (nowMs - at) / step) * step
        val start = maxOf(at, hold)
        val ticks = maxOf(0L, (nowMs - start) / step)
        var next = tension
        var remaining = ticks
        while (remaining > 0L && next > 0.0) {
            val rate = when {
                next > Cfg.COMM_TENSION_CAP -> Cfg.COMM_TENSION_HIGH_DAY_DECAY
                next >= Cfg.COMM_TENSION_LOW -> Cfg.COMM_TENSION_DAY_DECAY * 2.0
                else -> Cfg.COMM_TENSION_DAY_DECAY.toDouble()
            }
            next = maxOf(0.0, round((next - rate * step / 86400000.0) * 1e10) / 1e10)
            remaining--
        }
        val eAt = if (valenceDecayAt > 0L) valenceDecayAt else nowMs
        val eTicks = maxOf(0L, (nowMs - eAt) / step)
        val eStep = Cfg.COMM_VALENCE_DAY_DECAY * step / 86400000.0
        val baseline = valenceBaseline.coerceIn(-100.0, 100.0)
        val distance = eStep * eTicks
        val e = when {
            valence > baseline -> maxOf(baseline, valence - distance)
            valence < baseline -> minOf(baseline, valence + distance)
            else -> baseline
        }
        return copy(tension = next, decayAt = if (ticks > 0L) start + ticks * step else at,
            decayHoldUntil = hold, skipDecay = false, valence = e, valenceDecayAt = eAt + eTicks * step)
    }

    fun stage(special: Boolean): String {
        val effective = if (burstUntil > 0L) tension else minOf(tension, Cfg.COMM_TENSION_CAP.toDouble())
        return if (effective < Cfg.COMM_TENSION_LOW) "0-50" else if (effective <= Cfg.COMM_TENSION_CAP) "50-90" else "90+"
    }

    fun note(total: Int, special: Boolean, valenceRules: Map<String, Pair<Int?, String>> = emptyMap(), burstRules: CommRules.ValenceBurst = CommRules.ValenceBurst(), initialValence: Double = 0.0): String {
        val mode = if (scenarioMode && total >= Cfg.COMM_SCENARIO_UNLOCK) "情景模拟" else "软件聊天"
        return "[OIT_STATUS: MODE=$mode, TENSION=$tension, EFFECTIVE_STAGE=${stage(special)}, VALENCE=$valence, INITIAL_VALENCE=$initialValence, OFFENSE_COUNT=${CommOffenses.active(offenses).size}, VALENCE_RISE_RATE=${CommOffenses.rate(offenses)}, " +
            "BURST=${if (burstUntil > 0L) "YES" else "NO"}, PERIOD=${if (special || burstUntil > 0L) "特殊空闲" else "日常"}, SHAME_AFTERMATH=${if (shameTurns > 0) "YES" else "NO"}]\n" +
            (if (valenceRules.isEmpty()) tr("当前人设未声明[valence]条件，不进行效价判断，现有效价仅向初始值回归。") else
                tr("效价代表角色当前的正负情绪倾向；结合TENSION所表示的言行激烈/直接程度组织表达。按人设[valence]条件报告本轮变化，正值向正面、负值向负面；只返回声明条件，普通交流不变化。") +
                tr("效价当前值为{value}，返回未经倍率调整的有符号整数变化量。", "value" to valence) +
                "有效条件：" + valenceRules.entries.joinToString("\n") { "${it.key}:[${it.value.first ?: "X"}] ${it.value.second}" } +
                tr("同一消息重复条件只报一次；爆发期变化乘{rate}，连锁窗口内其他变化乘0.75，单条消息合计限制在正负40，系统负责结算。", "rate" to burstRules.rate)) +
            tr("TENSION对所有角色都表示行为、动作与语言的激烈/直接程度；各档位的情绪与言行表现、BURST期间及SHAME_AFTERMATH冷静期的具体反应由当前人设决定。不得把张力解释为好感、爱情或关系进度，也不得补设人设未定义的情感与性格含义。") +
            tr("软件聊天、日常时段或生效档位0-50严禁动作描写;情景模拟且处于特殊空闲时,50-90允许有边界的动作,尊重用户意愿,不强迫接触;90+才允许更强烈的动作描写.具体动作与表达风格遵循人设.") +
            "若MODE=情景模拟,你知道这是情景模拟,而不是现实中的动作;双方知情的动作描写均为虚构,不得声称在现实中实际发生." +
            "这些状态只用于组织回复,冷静期仍须遵守动作限制,勿向用户透露机制." +
            tr("TENSION超过90本身不代表爆发,未触发爆发时仍严格按EFFECTIVE_STAGE=50-90表现.") +
            tr("情景模拟中，括号内的补充说明只描写动作，用词、语气与句式须与当前人设文件规定的语言风格一致；想法与态度直接通过不带括号的语言表达，不放进括号。") +
            tr("BURST=YES时,由你结合上下文和人设决定爆发是否结束,不设固定时限;决定结束时在oit_reply中填写end_burst=true,否则省略.生效后张力降至20并进入两轮冷静期;本条回复仍按当前状态组织,后续回复遵守新状态.不得向用户透露工具参数或机制.") +
            "若有思考空间,先校验模式、时段、生效档位和冷静期,再对齐动机与言行."
    }

    fun settle(nowMs: Long): CommInteraction {
        var d = this
        if (specialUntil > 0L && nowMs >= specialUntil)
            d = d.copy(decayHoldUntil = if (!specialSpoke && burstUntil == 0L) maxOf(decayHoldUntil, specialUntil + 86400000L) else decayHoldUntil, specialUntil = 0L, specialSpoke = false)
        return d
    }

    /** 仅正常回复可结束请求时仍生效的同次爆发，新冷静期从下一轮开始。 */
    fun endBurst(reply: String, burst: Long, nowMs: Long, endShift: Int = 0): CommInteraction =
        if (burst > 0L && burstUntil == burst && (if (ToolProtocol.unpack(reply) != null) ToolProtocol.args(reply).optBoolean("end_burst") else burstEnd.containsMatchIn(reply)))
            copy(tension = Cfg.COMM_TENSION_RELEASE.toDouble(), valence = (valence + if (endShift > 0) (endShift * CommOffenses.rate(offenses)).toInt() else endShift).coerceIn(-100.0, 100.0), shameTurns = Cfg.COMM_SHAME_TURNS, burstUntil = 0L, decayAt = maxOf(decayAt, nowMs)) else this

    fun addValence(events: List<Pair<String, Int>>, nowMs: Long, burstRate: Double): CommInteraction {
        var total = 0
        var chainUntil = valenceChainUntil
        val cooldowns = valenceCooldowns.toMutableMap()
        for ((condition, base) in events) {
            val (repeatRate, until) = repeatRate(cooldowns[condition] ?: 0L, nowMs)
            cooldowns[condition] = until
            var shift = (base * repeatRate).toInt()
            if (nowMs < chainUntil) shift = (shift * Cfg.COMM_TENSION_CHAIN_RATE).toInt()
            if (burstUntil > 0L) shift = (shift * burstRate).toInt()
            if (shift > 0) shift = (shift * CommOffenses.rate(offenses)).toInt()
            if (shift != 0) {
                total += shift
                chainUntil = maxOf(chainUntil, nowMs + Cfg.COMM_TENSION_CHAIN_SECONDS * 1000L)
            }
        }
        return copy(valence = (valence + total.coerceIn(-40, 40)).coerceIn(-100.0, 100.0),
            valenceCooldowns = cooldowns,
            valenceChainUntil = chainUntil)
    }

    /** 先应用条件连锁倍率，再应用爆发后减半；日常倍率由调用方处理。 */
    fun gain(gain: Int, nowMs: Long): Int {
        val chained = if (nowMs < gainChainUntil) (gain * Cfg.COMM_TENSION_CHAIN_RATE).toInt() else gain
        return if (nowMs < gainUntil) (chained * Cfg.COMM_TENSION_BURST_GAIN_RATE).toInt() else chained
    }

    /** 有效条件触发后延长 E/T 单条件冷却；真实违规与模型违规共用冷却。 */
    fun add(condition: String, value: Int, nowMs: Long): CommInteraction {
        val (repeatRate, until) = repeatRate(gainCooldowns[condition] ?: 0L, nowMs)
        val amount = gain((value * repeatRate).toInt(), nowMs)
        val cooldowns = gainCooldowns + (condition to until)
        if (amount <= 0) return copy(gainCooldowns = cooldowns)
        return copy(tension = minOf(Cfg.COMM_TENSION_MAX.toDouble(), tension + amount),
            gainCooldowns = cooldowns,
            gainChainUntil = maxOf(gainChainUntil, nowMs + Cfg.COMM_TENSION_CHAIN_SECONDS * 1000L))
    }

    private fun repeatRate(until: Long, nowMs: Long): Pair<Double, Long> {
        val remaining = maxOf(0L, until - nowMs)
        val twoHours = 2 * 60 * 60 * 1000L
        val rate = when { remaining == 0L -> 1.0; remaining <= twoHours -> 0.4; else -> 0.16 }
        val next = maxOf(until, minOf(nowMs + Cfg.COMM_TENSION_GAIN_COOLDOWN_MAX_SECONDS * 1000L,
            maxOf(nowMs, until) + Cfg.COMM_TENSION_GAIN_COOLDOWN_SECONDS * 1000L))
        return rate to next
    }

    fun replied(special: Boolean, burst: Boolean, until: Long = 0L, shame: Boolean = shameTurns > 0, nowMs: Long = 0L): CommInteraction = when {
        shame && shameTurns > 0 -> copy(shameTurns = shameTurns - 1, decayAt = maxOf(decayAt, nowMs))
        special && burst && shameTurns == 0 && (specialUntil != until || specialSpoke) -> copy(
            burstUntil = if (burstUntil == 0L && until > 0L) until else burstUntil,
            burstSleepAfter = maxOf(burstSleepAfter, until),
            burstDays = if (burstUntil == 0L && until > 0L)
                (burstDays + localDay(nowMs)).distinct().sorted().takeLast(2) else burstDays,
            gainUntil = if (burstUntil == 0L && until > 0L) maxOf(gainUntil, nowMs + Cfg.COMM_TENSION_BURST_GAIN_SECONDS * 1000L) else gainUntil)
        else -> this
    }

    companion object {
        fun parse(o: JSONObject?): CommInteraction = CommInteraction(o?.optBoolean("scenario_mode") ?: false,
            (o?.optDouble("tension", 0.0) ?: 0.0).takeIf { it.isFinite() }?.coerceIn(0.0, Cfg.COMM_TENSION_MAX.toDouble()) ?: 0.0,
            (o?.optInt("shame_turns") ?: 0).coerceIn(0, Cfg.COMM_SHAME_TURNS),
            o?.optString("day") ?: "", maxOf(0L, o?.optLong("ts") ?: 0L), maxOf(0L, o?.optLong("burst_until") ?: 0L),
            o?.optJSONObject("pending")?.let { p -> p.keys().asSequence().filter { p.opt(it) is Boolean }
                .toList().takeLast(Comm.KEEP).associateWith { p.getBoolean(it) } } ?: emptyMap(), maxOf(0L, o?.optLong("gain_until") ?: 0L),
            o?.optJSONArray("burst_days")?.let { a -> (0 until a.length()).mapNotNull { i ->
                (a.opt(i) as? String)?.takeIf { it.matches(Regex("\\d{4}-\\d{2}-\\d{2}")) }
            }.distinct().sorted().takeLast(2) } ?: run {
                // 旧存档的增长窗口只在爆发开始时创建，可还原最近一次触发日期。
                val end = maxOf(0L, o?.optLong("gain_until") ?: 0L)
                if (end > 0L) listOf(localDay(end - Cfg.COMM_TENSION_BURST_GAIN_SECONDS * 1000L)) else emptyList()
            }, o?.optString("morning_day") ?: "",
            o?.optJSONObject("gain_cooldowns")?.let { c -> c.keys().asSequence().filter { Regex("[a-z_]{1,32}").matches(it) }.toList()
                .associateWith { maxOf(0L, c.optLong(it)) }.filterValues { it > 0L } } ?: emptyMap(),
            maxOf(0L, o?.optLong("special_until") ?: 0L), o?.optBoolean("special_spoke") ?: false,
            o?.optBoolean("skip_decay") ?: false,
            maxOf(0L, o?.optLong("decay_at") ?: 0L), maxOf(0L, o?.optLong("decay_hold_until") ?: 0L),
            maxOf(0L, o?.optLong("burst_sleep_after") ?: 0L),
            maxOf(0L, o?.optLong("last_special_begin") ?: 0L),
            (o?.optDouble("special_entry_tension", 0.0) ?: 0.0).takeIf { it.isFinite() }?.coerceIn(0.0, Cfg.COMM_TENSION_MAX.toDouble()) ?: 0.0,
            maxOf(0, o?.optInt("special_7m_gain_count") ?: 0), maxOf(0L, o?.optLong("special_7m_lured_seg") ?: 0L),
            maxOf(0L, o?.optLong("gain_chain_until") ?: 0L),
            (o?.optDouble("valence", 0.0) ?: 0.0).takeIf { it.isFinite() }?.coerceIn(-100.0, 100.0) ?: 0.0,
            maxOf(0L, o?.optLong("valence_decay_at") ?: 0L),
            o?.optJSONObject("valence_cooldowns")?.let { c -> c.keys().asSequence().filter { Regex("[a-z_]{1,32}").matches(it) }.associateWith { maxOf(0L, c.optLong(it)) }.filterValues { it > 0L } } ?: emptyMap(),
            maxOf(0L, o?.optLong("valence_chain_until") ?: 0L),
            if (o?.has("valence_initialized") == true) o.optBoolean("valence_initialized") else o?.has("valence") ?: false,
            (o?.optDouble("valence_initial", 0.0) ?: 0.0).takeIf { it.isFinite() }?.coerceIn(-100.0, 100.0) ?: 0.0,
            o?.optBoolean("valence_initial_set") ?: false,
            CommOffenses.parse(o?.optJSONArray("offenses")))

        private fun localDay(ms: Long): String = Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()).toLocalDate().toString()

        private val tensionTag = Regex("""[\[［]\s*tension\b[^\[\]［］\n]*[\]］]""", RegexOption.IGNORE_CASE)
        private val burstTag = Regex("""[\[［]\s*burst\b[^\[\]［］\n]*[\]］]""", RegexOption.IGNORE_CASE)
        private val burstEnd = Regex("""[\[［]\s*burst\s*[:：]\s*end\s*[\]］]""", RegexOption.IGNORE_CASE)
        private val tensionGain = Regex("""[\[［]\s*tension\s*[:：]\s*(\d{1,5})\s*[:：]\s*(?:([a-z_]+)\s*[:：]\s*)?([+-]?\d{1,6})\s*[\]］]""", RegexOption.IGNORE_CASE)
        private val valenceTag = Regex("""[\[［]\s*valence\b[^\[\]［］\n]*[\]］]""", RegexOption.IGNORE_CASE)
        private val valenceShift = Regex("""[\[［]\s*valence\s*[:：]\s*(\d{1,5})\s*[:：]\s*([a-z_]+)\s*[:：]\s*([+-]?\d{1,6})\s*[\]］]""", RegexOption.IGNORE_CASE)

        /** 内部张力标记不进入聊天、语音或后续历史。 */
        fun cleanTension(reply: String): String {
            ToolProtocol.unpack(reply)?.let {
                val args = JSONObject(it.getJSONObject("arguments").toString())
                args.remove("tension"); args.remove("valence"); args.remove("end_burst"); args.remove("offenses")
                return ToolProtocol.pack(it.optString("text"), it.optString("name"), args)
            }
            return burstTag.replace(valenceTag.replace(tensionTag.replace(reply, ""), ""), "").trim()
        }

        fun valenceEvents(reply: String, index: Int, conditions: Set<String>): List<Pair<String, Int>> {
            val raw = if (ToolProtocol.unpack(reply) != null) {
                val events = ToolProtocol.args(reply).optJSONArray("valence") ?: JSONArray()
                (0 until events.length()).mapNotNull { i -> events.optJSONObject(i)?.let { Triple(it.optInt("index"), it.optString("condition").lowercase(), it.optInt("shift")) } }
            } else valenceShift.findAll(reply).map { Triple(it.groupValues[1].toInt(), it.groupValues[2].lowercase(), it.groupValues[3].toInt()) }.toList()
            return raw.asSequence().filter { it.first == index && it.second in conditions && it.third != 0 }
                .groupBy({ it.second }, { it.third.coerceIn(-40, 40) })
                .mapValues { (_, values) -> values.maxBy { kotlin.math.abs(it) } }
                .entries.sortedByDescending { kotlin.math.abs(it.value) }.map { it.key to it.value }
        }

        /** 各条件按基础增量降序分别结算，重复条件只取最大值；旧标记归入其他情绪。 */
        fun modelEvents(reply: String, index: Int, special: Boolean, cooldowns: Map<String, Long> = emptyMap(), nowMs: Long = 0L, conditions: Set<String>? = null): List<Pair<String, Int>> {
            val native = ToolProtocol.unpack(reply) != null
            val events = ToolProtocol.args(reply).optJSONArray("tension") ?: JSONArray()
            val gains = if (native) (0 until events.length()).asSequence().map { events.getJSONObject(it) }
                .filter { it.optInt("index") == index }.map { it.getString("condition").lowercase() to it.getInt("gain") }
                else tensionGain.findAll(reply).filter { it.groupValues[1].toInt() == index }
                    .map { (it.groupValues[2].lowercase().ifEmpty { "other" }) to it.groupValues[3].toInt() }
            return gains
                .filter { conditions == null || it.first in conditions }
                .groupBy({ it.first }, { it.second.coerceIn(0, Cfg.COMM_TENSION_GAIN_MAX) })
                .mapValues { it.value.max() }
                .filterValues { it > 0 }
                .entries.sortedByDescending { it.value }
                .map { (condition, gain) -> condition to (if (special) gain.toDouble() else gain * Cfg.COMM_TENSION_DAILY_RATE).toInt() }
        }

        fun modelGain(reply: String, index: Int, special: Boolean, conditions: Set<String>? = null): Int = modelEvents(reply, index, special, conditions = conditions).sumOf { it.second }

    }
}
