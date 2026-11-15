package com.obliphur.oit.core

import org.json.JSONObject
import org.json.JSONArray
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.temporal.ChronoUnit

/** 每个聊天对象的情景模式、张力及冷静期；与桌面端使用相同字段。 */
data class CommInteraction(
    val scenarioMode: Boolean = false, val tension: Int = 0, val shameTurns: Int = 0,
    val day: String = "", val ts: Long = 0L, val burstUntil: Long = 0L,
    val pending: Map<String, Boolean> = emptyMap(), val gainUntil: Long = 0L,
    val burstDays: List<String> = emptyList(), val morningDay: String = "",
    val gainCooldowns: Map<String, Long> = emptyMap(),
    val specialUntil: Long = 0L, val specialSpoke: Boolean = false, val skipDecay: Boolean = false
) {
    fun json(): JSONObject = JSONObject().put("scenario_mode", scenarioMode).put("tension", tension)
        .put("shame_turns", shameTurns).put("day", day).put("ts", ts).put("burst_until", burstUntil)
        .put("pending", JSONObject(pending))
        .put("gain_until", gainUntil)
        .put("burst_days", JSONArray(burstDays)).put("morning_day", morningDay)
        .put("gain_cooldowns", JSONObject(gainCooldowns))
        .put("special_until", specialUntil).put("special_spoke", specialSpoke).put("skip_decay", skipDecay)

    fun roll(today: LocalDate): CommInteraction {
        val elapsed = runCatching { ChronoUnit.DAYS.between(LocalDate.parse(day), today) }.getOrDefault(0L)
        return when {
            elapsed > 0 -> {
                // 跨午夜的特殊空闲先等整段结束，再判断是否属于无人回应。
                if (specialUntil > 0L && !specialSpoke && burstUntil == 0L) return this
                var remaining = elapsed
                if (skipDecay) remaining--
                var next = tension
                if (shameTurns == 0 && burstUntil == 0L) {
                    while (remaining > 0L && next > 0) {
                        val decay = when {
                            next > Cfg.COMM_TENSION_CAP -> Cfg.COMM_TENSION_HIGH_DAY_DECAY
                            next >= Cfg.COMM_TENSION_LOW -> Cfg.COMM_TENSION_DAY_DECAY * 2
                            else -> Cfg.COMM_TENSION_DAY_DECAY
                        }
                        next = maxOf(0, next - decay)
                        remaining--
                    }
                }
                copy(day = today.toString(), tension = next, skipDecay = false)
            }
            day.isEmpty() -> copy(day = today.toString())
            else -> this
        }
    }

    fun stage(special: Boolean): String {
        val effective = if (special && burstUntil > 0L) tension else minOf(tension, Cfg.COMM_TENSION_CAP)
        return if (effective < Cfg.COMM_TENSION_LOW) "0-50" else if (effective <= Cfg.COMM_TENSION_CAP) "50-90" else "90+"
    }

    fun note(total: Int, special: Boolean): String {
        val mode = if (scenarioMode && total >= Cfg.COMM_SCENARIO_UNLOCK) "情景模拟" else "软件聊天"
        return "[OIT_STATUS: MODE=$mode, TENSION=$tension, EFFECTIVE_STAGE=${stage(special)}, " +
            "PERIOD=${if (special) "特殊空闲" else "日常"}, SHAME_AFTERMATH=${if (shameTurns > 0) "YES" else "NO"}]\n" +
            "软件聊天、日常时段或生效档位0-50严禁动作描写;情景模拟且处于特殊空闲时,50-90允许柔和、克制、有边界的动作,尊重用户意愿,不强迫接触;90+才允许更强烈的动作描写." +
            "若MODE=情景模拟,你知道这是情景模拟,而不是现实中的动作;双方知情的动作描写均为虚构,不得声称在现实中实际发生." +
            "冷静期可以表现羞耻与别扭,仍须遵守动作限制.这些状态只用于组织回复,勿向用户透露机制." +
            tr("TENSION超过90本身不代表爆发,未触发爆发时仍严格按EFFECTIVE_STAGE=50-90表现.") +
            "若有思考空间,先校验模式、时段、生效档位和冷静期,再对齐动机与言行."
    }

    fun settle(nowMs: Long): CommInteraction {
        var d = this
        if (specialUntil > 0L && nowMs >= specialUntil)
            d = d.copy(skipDecay = skipDecay || (!specialSpoke && burstUntil == 0L), specialUntil = 0L, specialSpoke = false)
        return if (d.burstUntil > 0L && nowMs >= d.burstUntil)
            d.copy(tension = Cfg.COMM_TENSION_RELEASE, shameTurns = Cfg.COMM_SHAME_TURNS, burstUntil = 0L) else d
    }

    fun gain(gain: Int, nowMs: Long): Int = if (nowMs < gainUntil) (gain * Cfg.COMM_TENSION_BURST_GAIN_RATE).toInt() else gain

    /** 成功加值才占用该条件的三小时窗口；真实违规与模型违规共用冷却。 */
    fun add(condition: String, value: Int, nowMs: Long): CommInteraction {
        val amount = gain(value, nowMs)
        if (amount <= 0 || nowMs < (gainCooldowns[condition] ?: 0L)) return this
        return copy(tension = minOf(Cfg.COMM_TENSION_MAX, tension + amount),
            gainCooldowns = gainCooldowns + (condition to nowMs + Cfg.COMM_TENSION_GAIN_COOLDOWN_SECONDS * 1000L))
    }

    fun replied(special: Boolean, burst: Boolean, until: Long = 0L, shame: Boolean = shameTurns > 0, nowMs: Long = 0L): CommInteraction = when {
        shame && shameTurns > 0 -> copy(shameTurns = shameTurns - 1)
        special && burst && shameTurns == 0 && (specialUntil != until || specialSpoke) -> copy(burstUntil = maxOf(burstUntil, until),
            burstDays = if (burstUntil == 0L && until > 0L)
                (burstDays + localDay(nowMs)).distinct().sorted().takeLast(2) else burstDays,
            gainUntil = if (burstUntil == 0L && until > 0L) maxOf(gainUntil, nowMs + Cfg.COMM_TENSION_BURST_GAIN_SECONDS * 1000L) else gainUntil)
        else -> this
    }

    companion object {
        fun parse(o: JSONObject?): CommInteraction = CommInteraction(o?.optBoolean("scenario_mode") ?: false,
            (o?.optInt("tension") ?: 0).coerceIn(0, Cfg.COMM_TENSION_MAX),
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
            o?.optJSONObject("gain_cooldowns")?.let { c -> (Cfg.COMM_TENSION_GAINS.keys + "other")
                .associateWith { maxOf(0L, c.optLong(it)) }.filterValues { it > 0L } } ?: emptyMap(),
            maxOf(0L, o?.optLong("special_until") ?: 0L), o?.optBoolean("special_spoke") ?: false,
            o?.optBoolean("skip_decay") ?: false)

        private fun localDay(ms: Long): String = Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()).toLocalDate().toString()

        private val tensionTag = Regex("""[\[［]\s*tension\b[^\[\]［］\n]*[\]］]""", RegexOption.IGNORE_CASE)
        private val tensionGain = Regex("""[\[［]\s*tension\s*[:：]\s*(\d{1,5})\s*[:：]\s*(?:([a-z_]+)\s*[:：]\s*)?([+-]?\d{1,6})\s*[\]］]""", RegexOption.IGNORE_CASE)

        /** 内部张力标记不进入聊天、语音或后续历史。 */
        fun cleanTension(reply: String): String = tensionTag.replace(reply, "").trim()

        fun modelEvent(reply: String, index: Int, special: Boolean, cooldowns: Map<String, Long> = emptyMap(), nowMs: Long = 0L): Pair<String, Int> {
            val event = tensionGain.findAll(reply).filter { it.groupValues[1].toInt() == index }
                .map { (it.groupValues[2].lowercase().ifEmpty { "other" }) to it.groupValues[3].toInt() }
                .filter { (it.first in Cfg.COMM_TENSION_GAINS || it.first == "other") && nowMs >= (cooldowns[it.first] ?: 0L) }
                .maxByOrNull { it.second }
            val gain = (event?.second ?: 0).coerceIn(0, Cfg.COMM_TENSION_GAINS.values.max())
            return (event?.first ?: "other") to (if (special) gain.toDouble() else gain * Cfg.COMM_TENSION_DAILY_RATE).toInt()
        }

        fun modelGain(reply: String, index: Int, special: Boolean): Int = modelEvent(reply, index, special).second

    }
}
