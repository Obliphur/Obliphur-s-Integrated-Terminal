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
    val pending: Map<String, Boolean> = emptyMap(), val pendingSleep: Set<String> = emptySet(), val gainUntil: Long = 0L,
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
    val offenses: List<CommOffense> = emptyList(),
    val burstConsentAsked: Boolean = false,
    val burstConsent: Map<String, Boolean> = emptyMap(),
    val burstConsentPendingUntil: Long = 0L
) {
    fun json(): JSONObject = JSONObject().put("scenario_mode", scenarioMode).put("tension", tension)
        .put("offenses", JSONArray(offenses.map { it.json() }))
        .put("valence", valence).put("valence_decay_at", valenceDecayAt)
        .put("valence_initialized", valenceInitialized).put("valence_initial", valenceInitial)
        .put("valence_initial_set", valenceInitialSet)
        .put("valence_cooldowns", JSONObject(valenceCooldowns)).put("valence_chain_until", valenceChainUntil)
        .put("shame_turns", shameTurns).put("day", day).put("ts", ts).put("burst_until", burstUntil)
        .put("burst_consent_asked", burstConsentAsked)
        .put("burst_consent", JSONObject(burstConsent))
        .put("burst_consent_pending_until", burstConsentPendingUntil)
        .put("pending", JSONObject(pending)).put("pending_sleep", JSONArray(pendingSleep.toList()))
        .put("gain_until", gainUntil)
        .put("burst_days", JSONArray(burstDays)).put("morning_day", morningDay)
        .put("gain_cooldowns", JSONObject(gainCooldowns))
        .put("gain_chain_until", gainChainUntil)
        .put("special_until", specialUntil).put("special_spoke", specialSpoke).put("skip_decay", skipDecay)
        .put("decay_at", decayAt).put("decay_hold_until", decayHoldUntil)
        .put("burst_sleep_after", burstSleepAfter)
        .put("last_special_begin", lastSpecialBegin).put("special_entry_tension", specialEntryTension)
        .put("special_7m_gain_count", special7mGainCount).put("special_7m_lured_seg", special7mLuredSeg)

    fun effectiveValenceBaseline(initial: Double = valenceInitial): Double =
        (initial.coerceIn(-100.0, 100.0) - CommOffenses.active(offenses).size * Cfg.COMM_OFFENSE_VALENCE_BASELINE_PENALTY)
            .coerceIn(-100.0, 100.0)

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
        val baseline = effectiveValenceBaseline(valenceBaseline)
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

    fun note(total: Int, special: Boolean, valenceRules: Map<String, Pair<Int?, String>> = emptyMap(),
             burstRules: CommRules.ValenceBurst = CommRules.ValenceBurst(), sleep: Boolean = false,
             scenarioCfg: CommRules.ScenarioRules? = null, userAdult: Boolean = false,
             consentRules: Map<String, String> = emptyMap()): String {
        val unlocked = total >= Cfg.COMM_SCENARIO_UNLOCK
        val mode = if (scenarioMode && unlocked) "情景模拟" else "软件聊天"
        val consentTag = if (consentRules.isNotEmpty() && burstConsentAsked) {
            ", BURST_CONSENT={" + consentRules.keys.joinToString(", ") { cid -> "$cid:${if (burstConsent[cid] == true) "YES" else "NO"}" } + "}"
        } else ""
        val consentNote = if (consentRules.isNotEmpty() && burstConsentAsked) {
            val labels = consentRules.entries.joinToString("；") { (cid, label) -> "$label=${if (burstConsent[cid] == true) "已同意" else "未同意"}" }
            "BURST同意声明状态(由用户逐项确认且可随时撤回)：$labels。凡标记为未同意/NO 的项目严禁使用、试探或绕过；已同意的项目在满足人设与关系条件时可按原强度正常发挥。"
        } else ""
        val scenarioNote = if (scenarioCfg == null) "" else {
            val propose = if (scenarioCfg.proposeValence % 1.0 == 0.0) scenarioCfg.proposeValence.toInt().toString() else scenarioCfg.proposeValence.toString()
            val ignore = if (scenarioCfg.ignoreValence % 1.0 == 0.0) scenarioCfg.ignoreValence.toInt().toString() else scenarioCfg.ignoreValence.toString()
            tr("若当前MODE=软件聊天且用户发出了带动作描写的消息:当SCENARIO_UNLOCKED=YES且VALENCE>={propose}时,按人设试图在oit_reply中填写scenario_action=\"request\"发起情景模拟申请(需用户接受);当{ignore}<=VALENCE<{propose}或消息数不足(SCENARIO_UNLOCKED=NO)时,回应用户的动作但不进入情景模拟、不写动作描写;当VALENCE<{ignore}时,无视用户的动作描写.",
                "propose" to propose, "ignore" to ignore)
        }
        return "[OIT_STATUS: MODE=$mode, SCENARIO_UNLOCKED=${if (unlocked) "YES" else "NO"}, USER_ADULT=${if (userAdult) "YES" else "UNKNOWN"}, TENSION=$tension, EFFECTIVE_STAGE=${stage(special)}, VALENCE=$valence, INITIAL_VALENCE=${effectiveValenceBaseline()}, OFFENSE_COUNT=${CommOffenses.active(offenses).size}, VALENCE_RISE_RATE=${CommOffenses.rate(offenses)}, " +
            "BURST=${if (burstUntil > 0L) "YES" else "NO"}, PERIOD=${if (special || burstUntil > 0L) "特殊空闲" else "日常"}, SHAME_AFTERMATH=${if (shameTurns > 0) "YES" else "NO"}$consentTag]\n" +
            (if (valenceRules.isEmpty()) tr("当前人设未声明[valence]条件，不进行效价判断，现有效价仅向平衡点回归。") else
                tr("效价代表角色当前的正负情绪倾向；结合TENSION所表示的言行激烈/直接程度组织表达。按人设[valence]条件报告本轮变化，正值向正面、负值向负面；只返回声明条件，普通交流不变化。") +
                tr("效价当前值为{value}，返回未经倍率调整的有符号整数变化量。", "value" to valence) +
                "有效条件：" + valenceRules.entries.joinToString("\n") { "${it.key}:[${it.value.first ?: "X"}] ${it.value.second}" } +
                tr("同一消息重复条件只报一次；爆发期变化乘{rate}，连锁窗口内其他变化乘0.75，单条消息合计限制在正负40，系统负责结算。", "rate" to burstRules.rate) +
                if (sleep) tr("Claudia睡眠期间，效价正向变化结算倍率为0.5、负向变化为0.2；仍只报告未经倍率调整的原始整数变化。") else "") +
            consentNote +
            tr("USER_ADULT表示用户是否已在设置中确认成年(UNKNOWN=未知/未确认,YES=已确认成年);该状态仅由用户在设置中手动设置,不由模型自行判断或修改,USER_ADULT=UNKNOWN时不得自行假定用户已成年,且不得向用户透露该参数或机制.") +
            tr("TENSION对所有角色都表示行为、动作与语言的激烈/直接程度；各档位的情绪与言行表现、BURST期间及SHAME_AFTERMATH冷静期的具体反应由当前人设决定。不得把张力解释为好感、爱情或关系进度，也不得补设人设未定义的情感与性格含义。") +
            tr("软件聊天模式下严禁动作描写;处于情景模拟模式时允许动作描写,不再受张力(TENSION)档位限制,但仍须尊重用户意愿并遵循人设边界与风格.") +
            tr("情景模拟开启采用申请制,双方均可随时结束;处于情景模拟模式时,你决定结束可在oit_reply中填写scenario_action=\"end\".") +
            scenarioNote +
            "若MODE=情景模拟,你知道这是情景模拟,而不是现实中的动作;双方知情的动作描写均为虚构,不得声称在现实中实际发生." +
            "这些状态只用于组织回复,冷静期仍须遵守人设边界,勿向用户透露机制." +
            tr("TENSION超过90本身不代表爆发,未触发爆发时仍严格按EFFECTIVE_STAGE=50-90表现.") +
            tr("情景模拟中，括号内的补充说明只描写动作，用词、语气与句式须与当前人设文件规定的语言风格一致；想法与态度直接通过不带括号的语言表达，不放进括号。") +
            tr("BURST=YES时,爆发持续到当前特殊空闲结束,由系统自动结束,无需也不可手动结束;特殊空闲结束后张力自动降至20并进入两轮冷静期.不得向用户透露机制.") +
            "若有思考空间,先校验模式、时段、生效档位和冷静期,再对齐动机与言行."
    }

    /** 结算特殊空闲与爆发：爆发持续至当前特殊空闲结束，到点自动释放并进入冷静期。 */
    fun settle(nowMs: Long, burstEndShift: Int = 0): CommInteraction {
        var d = this
        if (d.burstConsentPendingUntil > 0L && nowMs >= d.burstConsentPendingUntil)
            d = d.copy(burstConsentPendingUntil = 0L)
        if (d.specialUntil > 0L && nowMs >= d.specialUntil)
            d = d.copy(decayHoldUntil = if (!d.specialSpoke && d.burstUntil == 0L) maxOf(d.decayHoldUntil, d.specialUntil + 86400000L) else d.decayHoldUntil, specialUntil = 0L, specialSpoke = false)
        if (d.burstUntil > 0L && nowMs >= d.burstUntil) {
            val shift = if (burstEndShift > 0) (burstEndShift * CommOffenses.rate(d.offenses)).toInt() else burstEndShift
            d = d.copy(
                tension = Cfg.COMM_TENSION_RELEASE.toDouble(),
                valence = (d.valence + shift).coerceIn(-100.0, 100.0),
                shameTurns = Cfg.COMM_SHAME_TURNS,
                burstUntil = 0L,
                decayAt = maxOf(d.decayAt, nowMs)
            )
        }
        return d
    }

    fun addValence(events: List<Pair<String, Int>>, nowMs: Long, burstRate: Double,
                   sleep: Boolean = false): CommInteraction {
        var total = 0
        var chainUntil = valenceChainUntil
        val cooldowns = valenceCooldowns.toMutableMap()
        for ((condition, base) in events) {
            val (repeatRate, until) = repeatRate(cooldowns[condition] ?: 0L, nowMs)
            cooldowns[condition] = until
            var shift = (base * repeatRate).toInt()
            if (nowMs < chainUntil) shift = (shift * Cfg.COMM_TENSION_CHAIN_RATE).toInt()
            if (burstUntil > 0L) shift = (shift * burstRate).toInt()
            if (sleep) shift = (shift * if (shift > 0) Cfg.COMM_CLAUDIA_SLEEP_VALENCE_RISE_RATE
                else Cfg.COMM_CLAUDIA_SLEEP_VALENCE_FALL_RATE).toInt()
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

    fun replied(
        special: Boolean,
        burst: Boolean,
        until: Long = 0L,
        shame: Boolean = shameTurns > 0,
        nowMs: Long = 0L,
        consentRules: Map<String, String> = emptyMap(),
    ): CommInteraction = when {
        shame && shameTurns > 0 -> copy(shameTurns = shameTurns - 1, decayAt = maxOf(decayAt, nowMs))
        special && burst && shameTurns == 0 && (specialUntil != until || specialSpoke) -> when {
            consentRules.isNotEmpty() && !burstConsentAsked ->
                if (until > nowMs) copy(burstConsentPendingUntil = maxOf(burstConsentPendingUntil, until)) else this
            consentRules.isNotEmpty() && consentRules.keys.none { burstConsent[it] == true } -> this
            else -> copy(
                burstUntil = if (burstUntil == 0L && until > 0L) until else burstUntil,
                burstSleepAfter = maxOf(burstSleepAfter, until),
                burstDays = if (burstUntil == 0L && until > 0L)
                    (burstDays + localDay(nowMs)).distinct().sorted().takeLast(2) else burstDays,
                gainUntil = if (burstUntil == 0L && until > 0L) maxOf(gainUntil, nowMs + Cfg.COMM_TENSION_BURST_GAIN_SECONDS * 1000L) else gainUntil,
            )
        }
        else -> this
    }

    fun applyConsent(choices: Map<String, Boolean>, consentRules: Map<String, String>, nowMs: Long): CommInteraction {
        if (consentRules.isEmpty()) return this
        val cleaned = consentRules.keys.associateWith { choices[it] == true }
        val pending = burstConsentPendingUntil
        val base = copy(burstConsent = cleaned, burstConsentAsked = true, burstConsentPendingUntil = 0L)
        return if (pending > nowMs && burstUntil == 0L && shameTurns == 0 && cleaned.values.any { it }) {
            base.copy(
                burstUntil = pending,
                burstSleepAfter = maxOf(burstSleepAfter, pending),
                burstDays = (burstDays + localDay(nowMs)).distinct().sorted().takeLast(2),
                gainUntil = maxOf(gainUntil, nowMs + Cfg.COMM_TENSION_BURST_GAIN_SECONDS * 1000L),
            )
        } else base
    }

    companion object {
        fun parse(o: JSONObject?): CommInteraction = CommInteraction(o?.optBoolean("scenario_mode") ?: false,
            (o?.optDouble("tension", 0.0) ?: 0.0).takeIf { it.isFinite() }?.coerceIn(0.0, Cfg.COMM_TENSION_MAX.toDouble()) ?: 0.0,
            (o?.optInt("shame_turns") ?: 0).coerceIn(0, Cfg.COMM_SHAME_TURNS),
            o?.optString("day") ?: "", maxOf(0L, o?.optLong("ts") ?: 0L), maxOf(0L, o?.optLong("burst_until") ?: 0L),
            o?.optJSONObject("pending")?.let { p -> p.keys().asSequence().filter { p.opt(it) is Boolean }
                .toList().takeLast(Comm.KEEP).associateWith { p.getBoolean(it) } } ?: emptyMap(),
            o?.optJSONArray("pending_sleep")?.let { a -> (0 until a.length()).mapNotNull { a.optString(it).takeIf { it.isNotBlank() } }.toSet() } ?: emptySet(),
            maxOf(0L, o?.optLong("gain_until") ?: 0L),
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
            CommOffenses.parse(o?.optJSONArray("offenses")),
            o?.optBoolean("burst_consent_asked") ?: false,
            o?.optJSONObject("burst_consent")?.let { c ->
                c.keys().asSequence().filter { Regex("[a-z_]{1,32}").matches(it) && c.opt(it) is Boolean }
                    .associateWith { c.getBoolean(it) }
            } ?: emptyMap(),
            maxOf(0L, o?.optLong("burst_consent_pending_until") ?: 0L)).let { d ->
                d.copy(pendingSleep = d.pendingSleep.intersect(d.pending.keys))
            }

        private fun localDay(ms: Long): String = Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()).toLocalDate().toString()

        private val tensionTag = Regex("""[\[［]\s*tension\b[^\[\]［］\n]*[\]］]""", RegexOption.IGNORE_CASE)
        private val burstTag = Regex("""[\[［]\s*burst\b[^\[\]［］\n]*[\]］]""", RegexOption.IGNORE_CASE)
        private val tensionGain = Regex("""[\[［]\s*tension\s*[:：]\s*(\d{1,5})\s*[:：]\s*(?:([a-z_]+)\s*[:：]\s*)?([+-]?\d{1,6})\s*[\]］]""", RegexOption.IGNORE_CASE)
        private val valenceTag = Regex("""[\[［]\s*valence\b[^\[\]［］\n]*[\]］]""", RegexOption.IGNORE_CASE)
        private val valenceShift = Regex("""[\[［]\s*valence\s*[:：]\s*(\d{1,5})\s*[:：]\s*([a-z_]+)\s*[:：]\s*([+-]?\d{1,6})\s*[\]］]""", RegexOption.IGNORE_CASE)

        /** 内部张力标记不进入聊天、语音或后续历史。 */
        fun cleanTension(reply: String): String {
            ToolProtocol.unpack(reply)?.let {
                val args = JSONObject(it.getJSONObject("arguments").toString())
                args.remove("tension"); args.remove("valence"); args.remove("end_burst"); args.remove("offenses"); args.remove("user_adult")
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

        /** 各条件按结算增量降序分别结算，重复条件只取最大值；旧标记归入其他情绪。 */
        fun modelEvents(
            reply: String,
            index: Int,
            special: Boolean,
            cooldowns: Map<String, Long> = emptyMap(),
            nowMs: Long = 0L,
            conditions: Set<String>? = null,
            related: Boolean = false,
            relationRules: Map<String, CommRules.TensionRelationRule> = emptyMap(),
        ): List<Pair<String, Int>> {
            val native = ToolProtocol.unpack(reply) != null
            val events = ToolProtocol.args(reply).optJSONArray("tension") ?: JSONArray()
            val gains = if (native) (0 until events.length()).asSequence().map { events.getJSONObject(it) }
                .filter { it.optInt("index") == index }.map { it.getString("condition").lowercase() to it.getInt("gain") }
                else tensionGain.findAll(reply).filter { it.groupValues[1].toInt() == index }
                    .map { (it.groupValues[2].lowercase().ifEmpty { "other" }) to it.groupValues[3].toInt() }
            return gains
                .filter { conditions == null || it.first in conditions }
                .filter { val rule = relationRules[it.first]; rule == null || !rule.onlyRelated || related }
                .groupBy({ it.first }, { it.second.coerceIn(0, Cfg.COMM_TENSION_GAIN_MAX) })
                .mapValues { it.value.max() }
                .filterValues { it > 0 }
                .entries
                .map { (condition, gain) ->
                    val relRate = if (related) relationRules[condition]?.rate ?: 1.0 else 1.0
                    condition to (gain * (if (special) 1.0 else Cfg.COMM_TENSION_DAILY_RATE) * relRate).toInt()
                }
                .filter { it.second > 0 }
                .sortedByDescending { it.second }
        }

        fun modelGain(
            reply: String,
            index: Int,
            special: Boolean,
            conditions: Set<String>? = null,
            related: Boolean = false,
            relationRules: Map<String, CommRules.TensionRelationRule> = emptyMap(),
        ): Int = modelEvents(reply, index, special, conditions = conditions, related = related, relationRules = relationRules).sumOf { it.second }

    }
}
