package com.obliphur.oit.core

import com.obliphur.oit.ai.ToolProtocol
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlin.math.round

/** 冒犯按用户消息去重；原谅后只保留同步墓碑，不能被旧设备重新带回。 */
data class CommOffense(val id: String, val at: Long, val reason: String, val ts: Long, val forgiven: Boolean = false) {
    fun json(): JSONObject = JSONObject().put("id", id).put("at", at).put("reason", reason)
        .put("ts", ts).put("forgiven", forgiven)
}

object CommOffenses {
    fun parse(array: JSONArray?): List<CommOffense> = merge((0 until (array?.length() ?: 0)).mapNotNull { i ->
        val o = array?.optJSONObject(i) ?: return@mapNotNull null
        val id = o.opt("id") as? String ?: return@mapNotNull null
        val raw = o.opt("reason") as? String ?: return@mapNotNull null
        val reason = raw.trim().split(Regex("\\s+")).joinToString(" ").take(120)
        val forgiven = o.opt("forgiven") == true
        val at = o.optLong("at")
        val ts = o.optLong("ts")
        if (!id.matches(Regex("[A-Za-z0-9_.:-]{1,120}")) || at <= 0L || ts <= 0L || (reason.isEmpty() && !forgiven)) null
        else CommOffense(id, at, if (forgiven) "" else reason, ts, forgiven)
    })

    fun merge(records: List<CommOffense>): List<CommOffense> = records.groupBy { it.id }.values.map { group ->
        group.maxWith(compareBy<CommOffense> { it.forgiven }.thenBy { it.ts }.thenBy { it.reason })
    }.sortedWith(compareBy({ it.at }, { it.id }))

    fun active(records: List<CommOffense>): List<CommOffense> = records.filter { !it.forgiven }

    fun rate(records: List<CommOffense>): Double = maxOf(0.0,
        round((1.0 - active(records).size * Cfg.COMM_OFFENSE_VALENCE_RATE_PENALTY) * 1e10) / 1e10)

    fun apply(records: List<CommOffense>, reply: String, index: Int, uid: String, sent: Long, nowMs: Long): List<CommOffense> {
        if (ToolProtocol.unpack(reply) == null) return records
        val updated = records.associateBy { it.id }.toMutableMap()
        val existing = updated.keys.toSet()
        val events = ToolProtocol.args(reply).optJSONArray("offenses") ?: JSONArray()
        for (i in 0 until events.length()) {
            val e = events.optJSONObject(i) ?: continue
            if (e.optInt("index") != index) continue
            val reason = e.optString("reason")
            if (reason.isBlank() || reason.length > 120 || '\n' in reason || '\r' in reason || "【系统记录】" in reason) continue
            val target = e.optString("target")
            when (e.optString("action")) {
                "add" -> if (target.isEmpty() && uid !in updated && sent > 0L)
                    updated[uid] = CommOffense(uid, sent, reason.trim(), nowMs)
                "forgive" -> if (target in existing) updated[target]?.takeIf { !it.forgiven }?.let { old ->
                    updated[target] = old.copy(reason = "", ts = maxOf(nowMs, old.ts + 1L), forgiven = true)
                }
            }
        }
        return merge(updated.values.toList())
    }

    fun line(records: List<CommOffense>): String {
        val active = active(records)
        if (active.isEmpty()) return ""
        val values = JSONArray(active.map { r -> JSONObject().put("id", r.id).put("reason", r.reason)
            .put("time", Instant.ofEpochMilli(r.at).atZone(ZoneId.systemDefault()).format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm"))) })
        return tr("- 【系统记录】未获原谅的冒犯 ({count} 条): {records}", "count" to active.size, "records" to values.toString())
    }
}
