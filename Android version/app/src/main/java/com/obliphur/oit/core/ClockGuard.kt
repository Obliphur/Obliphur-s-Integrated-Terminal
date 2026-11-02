package com.obliphur.oit.core

import org.json.JSONObject
import java.io.File

/**
 * 系统时间被改 (MECHANICS 9.6, 对应桌面版 clockguard.py): 警告一次, 10 分钟内改回来, 否则 (或者再改一次) 清掉数据.
 *
 * 系统时间减去开机以来的时间 (SystemClock.elapsedRealtime, 睡眠也算, 不受改时间影响) 就是"开机的时刻", 同一次开机里不会变;
 * 一下子变了超过 TOL (5 分钟) = 系统时间被改了. 状态存在 clock_guard.json, 重启不丢.
 * 第一次发现 → 警告 (调用方弹窗), 开始 10 分钟倒计时 (只在程序运行时走); 倒计时里改回来 → 解除;
 * 没改回来 / 又改了一次 → "wipe"; 警告之后 24 小时内再被改 → 直接 "wipe".
 */
class ClockGuard(
    private val up: () -> Double?,
    private val wall: () -> Double = { System.currentTimeMillis() / 1000.0 },
    private val load: () -> JSONObject? = { null },
    private val save: (JSONObject) -> Unit = {},
    private val log: (String) -> Unit = {},
) {
    companion object {
        const val TOL = 300.0          // 秒: "开机时刻"变了超过这么多 = 时间被改了
        const val FOLLOW = 30.0        // 秒: 变化小于这个的当作时钟漂移, 基准跟着走
        const val GRACE = 600.0        // 秒: 警告之后改回来的时间 (程序运行时才走)
        const val REPEAT = 24 * 3600.0 // 秒: 警告之后这么久之内再被改 = 直接清数据
        const val SAVE_EVERY = 30.0

        fun forFile(file: File, up: () -> Double?, log: (String) -> Unit = {}) =
            ClockGuard(up, load = { Store.readObj(file) }, save = { Store.write(file, it) }, log = log)
    }

    /** 倒计时中的警告: kind jump (程序运行时发现的) / back (程序没开着时往回改了); d 改了多少秒; legit 改之前应有的时间. */
    class Warn(val kind: String, val d: Double, var ref: Double, val legit: Double, var left: Double, var up: Double) {
        fun toJson(): JSONObject = JSONObject().put("kind", kind).put("d", d).put("ref", ref).put("legit", legit).put("left", left).put("up", up)
    }

    var on = false
        private set
    var warn: Warn? = null
        private set
    private var ref = 0.0
    private var lastU = 0.0
    private var lastWarn = 0.0
    private var savedAt = -1e9
    private var event: String? = null
    private var wiped = false

    private fun snapshot(): JSONObject = JSONObject().put("ref", ref).put("up", lastU).put("wall", wall()).put("last_warn", lastWarn)
        .put("warn", warn?.toJson() ?: JSONObject.NULL)

    private fun persist(force: Boolean = false) {
        if (force || lastU - savedAt >= SAVE_EVERY) {
            savedAt = lastU
            save(snapshot())
        }
    }

    /** 程序启动时: 读上次记的, 看有没有在程序没开着时被往回改, 有没有没走完的倒计时. 发现的事件由第一次 tick 交出去. */
    fun start() {
        val u = up() ?: return
        on = true
        val w = wall()
        val est = w - u
        val d = load()
        ref = est
        lastU = u
        if (d == null) { persist(true); return }
        val ref0 = d.optDouble("ref", Double.NaN)
        val up0 = d.optDouble("up", Double.NaN)
        val wall0 = d.optDouble("wall", Double.NaN)
        if (ref0.isNaN() || up0.isNaN() || wall0.isNaN()) { persist(true); return }
        lastWarn = d.optDouble("last_warn", 0.0)
        val sameBoot = u >= up0
        val pw = d.optJSONObject("warn")
        if (pw != null) {
            resumeWarn(pw, est, u, w, sameBoot)
            persist(true)
            return
        }
        if (sameBoot && Math.abs(est - ref0) <= TOL) {
            ref = if (Math.abs(est - ref0) >= FOLLOW) ref0 else est
        } else if (w < wall0 - TOL) {
            event = detect("back", w - wall0, wall0, u)       // 程序没开着时 (或重新开机时) 系统时间比上次记着的还早: 往回改了
        }
        persist(true)
    }

    private fun resumeWarn(pw: JSONObject, est: Double, u: Double, w: Double, sameBoot: Boolean) {
        val kind = pw.optString("kind")
        val dd = pw.optDouble("d", Double.NaN)
        val legit = pw.optDouble("legit", Double.NaN)
        val left = pw.optDouble("left", Double.NaN)
        val r = pw.optDouble("ref", Double.NaN)
        if (kind.isEmpty() || dd.isNaN() || legit.isNaN() || left.isNaN() || r.isNaN()) return
        val restored: Boolean
        if (kind == "back") {
            restored = w >= legit - TOL
            ref = est
        } else if (sameBoot && Math.abs(est - r) <= TOL) {
            restored = true; ref = est
        } else if (sameBoot && Math.abs(est - (r + dd)) <= TOL) {
            restored = false; ref = r                          // 同一次开机, 还是被改的状态
        } else {
            val apparent = w - legit                           // 重新开机了: 离改之前最后的时间有多远
            restored = if (dd > 0) apparent < dd / 2 else apparent > dd / 2
            ref = if (restored) est else est - dd
        }
        if (restored) { warn = null; event = "restored" } else warn = Warn(kind, dd, ref, legit, left, u)
    }

    /** 第一次发现被改: 警告, 或者 (24 小时内警告过) 直接清数据. 返回事件. */
    private fun detect(kind: String, delta: Double, legit: Double, u: Double): String {
        if (lastWarn != 0.0 && legit - lastWarn >= 0 && legit - lastWarn < REPEAT) {
            log("modified again within 24 h of the warning")
            return "wipe"
        }
        lastWarn = legit
        warn = Warn(kind, delta, ref, legit, GRACE, u)
        log("modified by ${"%+.0f".format(delta)}s ($kind), ${GRACE.toInt()}s to restore")
        return "warn"
    }

    /** 大约每秒一次. 返回事件: "warn" (第一次发现, 要弹窗) / "restored" (改回来了) / "wipe" (要清数据) / null. */
    fun tick(): String? {
        if (!on || wiped) return null
        val u = up() ?: return null
        var ev = event
        event = null
        val w = wall()
        val est = w - u
        lastU = u
        if (ev == null) {
            val g = warn
            if (g == null) {
                val delta = est - ref
                if (Math.abs(delta) <= TOL) {
                    if (Math.abs(delta) < FOLLOW) ref = est
                } else ev = detect("jump", delta, ref + u, u)
            } else ev = inGrace(g, w, u, est)
        }
        if (ev == "wipe") wiped = true
        persist(ev == "warn" || ev == "restored" || ev == "wipe")
        return ev
    }

    private fun inGrace(g: Warn, w: Double, u: Double, est: Double): String? {
        val restored = if (g.kind == "back") w >= g.legit - TOL else Math.abs(est - ref) <= TOL
        if (restored) {
            warn = null
            ref = if (g.kind == "back" || Math.abs(est - ref) < FOLLOW) est else ref
            return "restored"
        }
        if (g.kind == "jump" && Math.abs(est - (ref + g.d)) > TOL) {
            log("modified a second time during the grace period")
            return "wipe"
        }
        g.left -= maxOf(0.0, u - g.up)
        g.up = u
        if (g.left <= 0) {
            log("not restored within the grace period")
            return "wipe"
        }
        return null
    }
}
