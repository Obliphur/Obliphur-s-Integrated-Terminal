package com.obliphur.oit.core

import org.json.JSONObject
import java.time.LocalDate
import java.time.temporal.ChronoUnit

/** 每个聊天对象的情景模式、张力及冷静期；与桌面端使用相同字段。 */
data class CommInteraction(
    val scenarioMode: Boolean = false, val tension: Int = 0, val shameTurns: Int = 0,
    val day: String = "", val ts: Long = 0L, val burstUntil: Long = 0L
) {
    fun json(): JSONObject = JSONObject().put("scenario_mode", scenarioMode).put("tension", tension)
        .put("shame_turns", shameTurns).put("day", day).put("ts", ts).put("burst_until", burstUntil)

    fun roll(today: LocalDate): CommInteraction {
        val elapsed = runCatching { ChronoUnit.DAYS.between(LocalDate.parse(day), today) }.getOrDefault(0L)
        return when {
            elapsed > 0 -> {
                var remaining = elapsed
                var next = tension
                if (shameTurns == 0 && burstUntil == 0L) {
                    while (remaining > 0L && next > 0) {
                        val decay = if (next in Cfg.COMM_TENSION_LOW..Cfg.COMM_TENSION_CAP)
                            Cfg.COMM_TENSION_DAY_DECAY * 2 else Cfg.COMM_TENSION_DAY_DECAY
                        next = maxOf(0, next - decay)
                        remaining--
                    }
                }
                copy(day = today.toString(), tension = next)
            }
            day.isEmpty() -> copy(day = today.toString())
            else -> this
        }
    }

    fun stage(special: Boolean): String {
        val effective = if (special) tension else minOf(tension, Cfg.COMM_TENSION_CAP)
        return if (effective < Cfg.COMM_TENSION_LOW) "0-50" else if (effective <= Cfg.COMM_TENSION_CAP) "50-70" else "70+"
    }

    fun note(total: Int, special: Boolean): String {
        val mode = if (scenarioMode && total >= Cfg.COMM_SCENARIO_UNLOCK) "情景模拟" else "软件聊天"
        return "[OIT_STATUS: MODE=$mode, TENSION=$tension, EFFECTIVE_STAGE=${stage(special)}, " +
            "PERIOD=${if (special) "特殊空闲" else "日常"}, SHAME_AFTERMATH=${if (shameTurns > 0) "YES" else "NO"}]\n" +
            "软件聊天严禁动作描写;情景模拟仅在特殊空闲且生效档位70+时允许动作描写." +
            "冷静期可以表现羞耻与别扭,仍须遵守动作限制.这些状态只用于组织回复,勿向用户透露机制." +
            "若有思考空间,先校验模式、时段、生效档位和冷静期,再对齐动机与言行."
    }

    fun settle(nowMs: Long): CommInteraction = if (burstUntil > 0L && nowMs >= burstUntil)
        copy(tension = Cfg.COMM_TENSION_RELEASE, shameTurns = Cfg.COMM_SHAME_TURNS, burstUntil = 0L) else this

    fun replied(special: Boolean, burst: Boolean, until: Long = 0L, shame: Boolean = shameTurns > 0): CommInteraction = when {
        shame && shameTurns > 0 -> copy(shameTurns = shameTurns - 1)
        special && burst && shameTurns == 0 -> copy(burstUntil = maxOf(burstUntil, until))
        else -> this
    }

    companion object {
        fun parse(o: JSONObject?): CommInteraction = CommInteraction(o?.optBoolean("scenario_mode") ?: false,
            (o?.optInt("tension") ?: 0).coerceIn(0, Cfg.COMM_TENSION_MAX),
            (o?.optInt("shame_turns") ?: 0).coerceIn(0, Cfg.COMM_SHAME_TURNS),
            o?.optString("day") ?: "", maxOf(0L, o?.optLong("ts") ?: 0L), maxOf(0L, o?.optLong("burst_until") ?: 0L))

        // 单条只取最强分类，避免重试、分条回复与后台触发重复增长。
        private val patterns = listOf(
            "情感操控|全自动告解|自我构陷|自我厌恶|我(?:一文不值|不配|很没用|是个废物)|道德死罪|反刍|self.loath|worthless|don't deserve" to "rumination",
            "不希望.{0,8}压力|不寻求.{0,8}关系|不(?:想|要)给你.{0,6}压力|大部分人无法适应|以退为进|不想拖累|don't want to burden|no pressure" to "boundary",
            "好累|很累|累了|疲惫|撒泼|打滚|台灯.{0,6}亮|抱抱|抱我|陪陪我|想被.{0,4}宠|exhausted|hug me|so tired" to "comfort",
            "专注.{0,8}违规|熬夜|又晚睡|深夜闲聊|stayed up|staying up" to "violation"
        ).map { Regex(it.first, RegexOption.IGNORE_CASE) to it.second }

        fun delta(text: String, special: Boolean): Int {
            val gain = patterns.filter { it.first.containsMatchIn(text) }.maxOfOrNull { Cfg.COMM_TENSION_GAINS.getValue(it.second) } ?: 0
            return (if (special) gain.toDouble() else gain * Cfg.COMM_TENSION_DAILY_RATE).toInt()
        }
    }
}
