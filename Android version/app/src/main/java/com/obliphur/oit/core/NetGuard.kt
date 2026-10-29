package com.obliphur.oit.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.LocalDate
import java.time.LocalDateTime

data class NetRequest(val minutes: Int, val at: Long, val reason: String, val quick: Boolean)

/**
 * 联网额度与申请 (MECHANICS 2). 对应桌面版 net_guard.py 的额度部分;
 * 真正断网 / 联网由 [backend] 完成 (Android 上是 VpnService, 见 service/OitVpnService.kt).
 *
 * 时间: 申请的冷静期按墙上时间 (System.currentTimeMillis) 算, 联网计时按 tick(dt) 传进来的
 * 经过秒数算, 每一步最多 TICK_CLAMP 秒.
 */
class NetGuard(
    dir: File,
    private val settings: Settings,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    /** 状态变了 (联网 / 断网 / 开发者模式) 时调用; force=true 是定期复查. */
    var backend: (force: Boolean) -> Unit = {}
    var dead = false
    /** 设备同步: 其他设备今天的 [used, bonus, tomorrow_used, debt, gift] 之和 (MECHANICS 15). */
    var peerNet: () -> DoubleArray = { DoubleArray(5) }
    /** 设备同步: 其他设备的联网会话 (按理由统计用). */
    var peerUsage: () -> List<JSONObject> = { emptyList() }
    /** 行为模式 (MECHANICS 21): 有外出的日子额度 ×0.2; 正在外出时冷静期 ×0.3. */
    var quotaFactor: (LocalDate) -> Double = { 1.0 }
    var cooldownFactor: (LocalDateTime) -> Double = { 1.0 }
    /** 联网状态 (申请中 / 联网中, MECHANICS 2.8) 随设备同步, 关掉程序再打开时接着用.
     *  liveId: 这一状态的编号 (每次改动换一个); liveOwner: 发起的设备, 由它扣额度 (它不在线时由本机扣). */
    var myId: () -> String = { "" }
    var chargeOk: (String) -> Boolean = { true }
    var liveId = ""
        private set
    var liveOwner = ""
        private set
    /** 这次联网到几点 (墙上时间, 毫秒). */
    private var grantEnd = 0L
    private var grantReason = ""
    private var grantMinutes = 0

    private val stateFile = File(dir, "net_state.json")
    private val logFile = File(dir, "net_requests.log")
    private val usageFile = File(dir, "net_usage.json")
    val usage = mutableListOf<JSONObject>()
    private var session: JSONObject? = null
    private var usageDirty = false

    var date: String = LocalDate.now().key()
        private set
    var used = 0.0
    var bonus = 0.0
    /** 今天合约奖励的额度 (完成带"互联网娱乐的败退"的合约), 不用还. */
    var gift = 0.0
    var tomorrowUsed = 0.0
    var debt = 0.0
    @Volatile
    var grantLeft = 0.0
        private set
    var grantRate = 1.0
        private set
    var grantQuick = false
        private set
    var pending: NetRequest? = null
        private set

    private var sinceSave = 0.0
    private var sinceEnforce = 0.0

    init {
        load()
        loadUsage()
        // 关闭前的联网状态: 申请还在等就接着等, 联网还没到时间就接着联网 (到原来的结束时刻为止)
        Store.readObj(stateFile)?.optJSONObject("live")?.let { adopt(it, apply = false) }
    }

    private fun day(offset: Long = 0): LocalDate =
        runCatching { LocalDate.parse(date) }.getOrElse { LocalDate.now() }.plusDays(offset)

    private fun qf(day: LocalDate): Double = runCatching { maxOf(0.0, quotaFactor(day)) }.getOrDefault(1.0)
    private fun cf(now: LocalDateTime = LocalDateTime.now()): Double =
        runCatching { maxOf(0.0, cooldownFactor(now)) }.getOrDefault(1.0)

    /** 今天的每日额度 (× 星期倍率). 外出的 20% 不在这里, 见 todayTotal. */
    val limit get() = settings.dailyQuota(day())
    val tomorrowLimit get() = settings.dailyQuota(day(1))
    val quotaMultiplier get() = settings.quotaMultiplier(day())
    /** 外出的日子今天的总额度只能用几成 (1.0 = 没有限制). */
    val outingFactor get() = qf(day())
    /** 现在的联网冷静期因为外出变成多少. */
    val outingCooldown get() = cf()
    val cooldown get() = settings.num("COOLDOWN")
    val enforceEvery get() = maxOf(5.0, settings.num("ENFORCE_EVERY"))
    val tickClamp get() = settings.num("TICK_CLAMP")
    private val quickMult get() = settings.num("QUICK_MULTIPLIER")
    private val loanRate get() = settings.num("LOAN_RATE")

    // 绑定了其他设备时额度是共用的: *All 是所有设备的合计
    val usedAll get() = used + peerNet()[0]
    val bonusAll get() = bonus + peerNet()[1]
    val tomorrowUsedAll get() = tomorrowUsed + peerNet()[2]
    val debtAll get() = debt + peerNet()[3]
    val giftAll get() = gift + peerNet().let { if (it.size > 4) it[4] else 0.0 }

    /** 今天的总额度 = 每日额度 + 今天借到的 + 今天合约奖励的. */
    val fullTotal get() = limit + bonusAll + giftAll
    /** 今天能用的额度: 有外出的日子只能用总额度的 20% (MECHANICS 21.4), 平时就是总额度. */
    val todayTotal get() = fullTotal * qf(day())
    val remaining get() = maxOf(0.0, todayTotal - usedAll - debtAll)
    val tomorrowRemaining get() = maxOf(0.0, tomorrowLimit - tomorrowUsedAll)
    val quickLeft get() = remaining / quickMult
    val loanLeft get() = tomorrowRemaining / loanRate
    val quotaUsed get() = minOf(todayTotal, usedAll + debtAll)

    val devMode get() = settings.devMode
    val online: Boolean get() = devMode || grantLeft > 0
    val mode: String
        get() = when {
            devMode -> "dev"
            online -> "online"
            pending != null -> "pending"
            else -> "offline"
        }

    // ---- 存档 ----

    private fun load() {
        val d = Store.readObj(stateFile) ?: return
        val saved = d.optString("date", "")
        fun num(k: String) = maxOf(0.0, d.dbl(k))
        if (saved == date) {
            used = num("used"); bonus = num("bonus"); gift = num("gift")
            tomorrowUsed = num("tomorrow_used"); debt = num("debt")
        } else if (saved.isNotEmpty() && saved < date) {
            // 程序关着跨过了零点: 借贷要还的部分照样变成今天的欠款 (隔了几天也一样)
            debt = num("tomorrow_used")
            if (debt > 0) {
                save()
                log("LOAN     repay ${(debt / 60).toInt()} min today (borrowed on $saved)")
            }
        }
    }

    fun save() {
        if (dead) return
        Store.write(stateFile, JSONObject().put("date", date).put("used", used.toLong())
            .put("bonus", bonus.toLong()).put("gift", gift.toLong()).put("tomorrow_used", tomorrowUsed.toLong())
            .put("debt", debt.toLong()).put("live", live()))
        saveUsage()
        sinceSave = 0.0
    }

    private fun loadUsage() {
        usage.clear()
        val arr = Store.readObj(usageFile)?.optJSONArray("sessions") ?: return
        val (a, b) = monthPrefixes()
        usage.addAll(arr.objects().filter { it.optString("date").take(7).let { p -> p == a || p == b } })
    }

    private fun saveUsage() {
        if (dead || !usageDirty) return
        val (a, b) = monthPrefixes()
        usage.removeAll { it.optString("date").take(7).let { p -> p != a && p != b } }
        if (session != null && usage.none { it === session }) session = null
        if (Store.write(usageFile, JSONObject().put("sessions", JSONArray(usage)))) usageDirty = false
    }

    private fun openSession(reason: String, quick: Boolean, minutes: Int, start: LocalDateTime = LocalDateTime.now()) {
        val s = JSONObject().put("date", start.toLocalDate().key()).put("start", start.format(Fmt.HM))
            .put("reason", reason).put("quick", quick).put("minutes", minutes).put("seconds", 0.0)
        session = s
        usage.add(s)
        usageDirty = true
    }

    /** 实际联网的秒数 (不乘快速访问倍数), 跨零点自动拆到新的一天. */
    private fun countOnline(dt: Double) {
        val s = session ?: return
        if (dt <= 0) return
        val now = LocalDateTime.now()
        if (s.optString("date") != now.toLocalDate().key())
            openSession(s.optString("reason"), s.optBoolean("quick"), s.optInt("minutes"), now.floorDay())
        val cur = session!!
        cur.put("seconds", Math.round((cur.dbl("seconds") + dt) * 10) / 10.0)
        usageDirty = true
    }

    /** 设备同步: 子端第一次绑定时清掉自己的联网会话记录 (今天已用的额度保留). */
    fun clearUsageForSync() {
        usage.clear()
        session = null
        usageDirty = true
        saveUsage()
    }

    fun usageByReason(prefix: String): List<Pair<String, Double>> =
        (usage + peerUsage()).filter { it.optString("date").startsWith(prefix) }.map { it.optString("reason") to it.dbl("seconds") }

    fun log(text: String) {
        if (!dead) Store.append(logFile, text)
    }

    // ---- 申请 ----

    fun canBorrow(minutes: Double): String? {
        val add = maxOf(0.0, minutes) * 60
        if (add <= 0) return tr("最少 1 分钟.")
        if (add > loanLeft + 1e-6) {
            if (loanLeft < 60) return tr("明天的额度已经借完了.")
            return tr("今天最多还能借 {max}(明天剩余 {left},每借 1 分钟扣 {rate} 分钟).",
                "max" to fmtHm(loanLeft), "left" to fmtHm(tomorrowRemaining), "rate" to numText(loanRate))
        }
        return null
    }

    /** 紧急借贷: 立即给今天加额度, 代价是明天扣 LOAN_RATE 倍. 不联网. */
    fun borrow(minutes: Int, reason: String): String? {
        if (devMode) return tr("已经在联网中.")          // 开发者模式在界面上不显示
        canBorrow(minutes.toDouble())?.let { return it }
        val add = minutes * 60.0
        bonus += add
        tomorrowUsed += add * loanRate
        save()
        log("LOAN     +$minutes min today, -${(minutes * loanRate).toInt()} min tomorrow  reason: $reason")
        return null
    }

    /** 合约的奖励: 今天的额度多给 seconds 秒 (不用还, 零点清零). */
    fun addGift(seconds: Double, reason: String) {
        gift += maxOf(0.0, seconds)
        save()
        log("GIFT     +${(seconds / 60).toInt()} min today  reason: $reason")
    }

    fun canQuick(minutes: Double): String? {
        val need = maxOf(0.0, minutes) * 60
        if (need <= 0) return tr("最少 1 分钟.")
        if (need * quickMult > remaining + 1e-6) {
            if (quickLeft < 60) return tr("今天剩余的额度不够快速访问了.")
            return tr("快速访问最多还能用 {max}(今日剩余 {left},按 ×{a} 消耗).",
                "max" to fmtHm(quickLeft), "left" to fmtHm(remaining), "a" to numText(quickMult))
        }
        return null
    }

    fun request(minutes: Int, reason: String, quick: Boolean = false, cooldownRate: Double = 1.0,
                cooldownMult: Double = 1.0): String? {
        if (devMode) return tr("已经在联网中.")
        if (online) return tr("已经在联网中.")
        if (pending != null) return tr("已经有一个申请在等待中.")
        if (minutes < 1) return tr("最少 1 分钟.")
        if (quick) {
            canQuick(minutes.toDouble())?.let { return it }
            pending = NetRequest(minutes, clock(), reason, true)
            touch()
            log("QUICK    $minutes min (cost ${(minutes * quickMult).toInt()} min quota)  reason: $reason")
            return null
        }
        if (minutes * 60 > remaining) {
            var extra = ""
            if (loanLeft >= 60) extra = tr("紧急借贷最多还能借 {max}.", "max" to fmtHm(loanLeft))
            return tr("今天只剩 {left} 了.", "left" to fmtHm(remaining)) + extra
        }
        val rate = (cooldownRate * cf()).coerceIn(0.0, 1.0)
        val mult = maxOf(1.0, cooldownMult)             // 合约的侵蚀: 冷静期 ×2 / ×3
        pending = NetRequest(minutes, clock() + (cooldown * rate * mult * 1000).toLong(), reason, false)
        touch()
        log("REQUEST  $minutes min" + (if (rate < 1.0) " (cooldown ${numText(rate)}x)" else "") +
            (if (mult > 1.0) " (contract, cooldown x${numText(mult)})" else "") + "  reason: $reason")
        return null
    }

    fun convertToQuick(): String? {
        val p = pending ?: return tr("没有等待中的申请.")
        if (p.quick) return tr("这个申请已经是快速访问了.")
        canQuick(p.minutes.toDouble())?.let { return it }
        pending = NetRequest(p.minutes, clock(), p.reason, true)
        touch()
        log("QUICK    upgrade ${p.minutes} min (cost ${(p.minutes * quickMult).toInt()} min quota)")
        return null
    }

    fun cancel() {
        pending?.let {
            log("CANCEL   ${it.minutes} min ${if (it.quick) "quick " else ""}request")
            touch()
        }
        pending = null
        save()
    }

    fun endEarly() {
        if (devMode) return
        if (online) {
            log("END      early, ${grantLeft.toInt()}s unused")
            touch()
        }
        resetGrant()
        backend(false)
        save()
    }

    private fun resetGrant() {
        grantLeft = 0.0
        grantRate = 1.0
        grantQuick = false
        grantEnd = 0L
        grantReason = ""
        grantMinutes = 0
        if (session != null) {
            session = null
            saveUsage()
        }
    }

    // ---- 联网状态: 随设备同步, 关掉程序再打开时接着用 (MECHANICS 2.8) ----

    /** 本机改动了联网状态: 换一个编号, 本机成为发起的设备. */
    private fun touch() {
        liveId = clock().toString()
        liveOwner = myId()
    }

    /** 此刻的联网状态 (只用墙上时间, 没有改动时内容不变; 只用整数: at 毫秒, end 秒, rate ×100). 同 net_guard.py 的 live. */
    fun live(): JSONObject {
        val o = JSONObject().put("id", liveId).put("owner", liveOwner)
        val p = pending
        return when {
            grantLeft > 0 && grantEnd > 0 && !devMode -> o.put("state", "online").put("end", grantEnd / 1000)
                .put("rate", Math.round(grantRate * 100)).put("quick", grantQuick).put("reason", grantReason).put("minutes", grantMinutes)
            p != null -> o.put("state", "pending").put("at", p.at).put("minutes", p.minutes)
                .put("reason", p.reason).put("quick", p.quick)
            else -> o.put("state", "offline")
        }
    }

    /** 联网到 endMs (墙上时间) 为止; 已经过了就不联网. left: 本机刚批下来的秒数. */
    private fun grant(endMs: Long, rate: Double, quick: Boolean, reason: String, minutes: Int, left: Double? = null): Boolean {
        resetGrant()
        val secs = left ?: ((endMs - clock()) / 1000.0)
        if (secs <= 0) return false
        grantLeft = secs
        grantEnd = endMs
        grantRate = rate
        grantQuick = quick
        grantReason = reason
        grantMinutes = minutes
        if (chargeOk(liveOwner)) openSession(reason, quick, minutes)
        return true
    }

    /** 别的设备上 (或关闭前) 的联网状态: 照着切换. apply=false: 只改状态, 不动后端 (启动时). 返回 "started" / "ended" / null. */
    fun adopt(live: JSONObject, apply: Boolean = true): String? {
        if (dead || devMode) return null
        val lid = live.optString("id")
        if (lid.isEmpty()) return null
        val st = live.optString("state")
        if (lid == liveId) {
            // 同一次联网, 各设备算出的结束时刻差一两秒: 对齐到同步来的, 免得来回改
            val end = live.optLong("end") * 1000
            if (st == "online" && online && grantEnd > 0 && end > 0 && end != grantEnd) {
                grantLeft = maxOf(0.0, grantLeft + (end - grantEnd) / 1000.0)
                grantEnd = end
            }
            return null
        }
        val was = online
        liveId = lid
        liveOwner = live.optString("owner")
        var event: String? = null
        if (st == "online") {
            pending = null
            if (grant(live.optLong("end") * 1000, live.optInt("rate", 100) / 100.0, live.optBoolean("quick"),
                    live.optString("reason"), live.optInt("minutes"))) {
                if (!was) event = "started"
            } else if (was) event = "ended"
        } else {
            if (was) {
                resetGrant()
                event = "ended"
            }
            pending = if (st == "pending") NetRequest(live.optInt("minutes"), live.optLong("at"),
                live.optString("reason"), live.optBoolean("quick")) else null
        }
        if (apply) {
            log("SYNC     net state $st from ${liveOwner.ifEmpty { "?" }}")
            backend(false)
            save()
        }
        return event
    }

    /** 程序启动 / 结束时: 排队中的申请取消, 进行中的会话结束, 断网. */
    fun shutdown() {
        pending = null
        resetGrant()
        save()
        backend(false)
    }

    /** 重置: 不再记录任何东西. */
    fun release() {
        dead = true
        pending = null
        grantLeft = 0.0
    }

    fun devChanged() {
        pending = null
        resetGrant()
        sinceEnforce = 0.0
        log(if (devMode) "DEV      network restrictions released" else "DEV      fail-closed restored")
        if (!devMode) {                     // 关了: 把这一段记成的联网收尾
            session = null
            saveUsage()
            save()
        }
        backend(false)
    }

    private fun rollover(today: String) {
        date = today
        debt = maxOf(0.0, tomorrowUsed)
        tomorrowUsed = 0.0
        bonus = 0.0
        gift = 0.0
        used = 0.0
        save()
    }

    /** 大约每秒调用一次. 返回 "started" / "ended" / null. */
    fun tick(dtIn: Double): String? {
        if (dead) return null
        val today = LocalDate.now().key()
        if (today != date) rollover(today)            // 开发者模式下也照常跨日, 关掉开发者模式时额度已经是今天的
        if (devMode) {
            if (pending != null) {
                pending = null
                log("DEV      pending request dropped (developer mode)")
            }
            // 开着的时间照常消耗联网额度 (1 倍), 记成一次联网; 只消耗, 不限制: 额度用完了也不会关, 随时能开
            val dt = minOf(maxOf(0.0, dtIn), tickClamp)
            used += dt
            if (session == null) openSession("开发者模式", false, 0)
            countOnline(dt)
            sinceSave += dt
            if (sinceSave >= 5) save()
            return null
        }

        var event: String? = null
        val p = pending
        if (p != null && clock() >= p.at) {
            pending = null
            resetGrant()
            val rate = if (p.quick) maxOf(1.0, quickMult) else 1.0
            val secs = minOf(p.minutes * 60.0, remaining / rate)
            // 编号接着申请的, 各设备算出来的一样 (结束时刻差一两秒的, 同步时对齐)
            liveId = liveId.ifEmpty { p.at.toString() } + "g"
            if (secs > 0 && grant(Math.round(clock() / 1000.0 + secs) * 1000, rate, p.quick, p.reason, p.minutes, left = secs)) {
                backend(false)
                save()
                log("START    ${(secs / 60).toInt()} min granted" + if (p.quick) " (quick, ${numText(rate)}x)" else "")
                event = "started"
            }
        }

        val dtRaw = maxOf(0.0, dtIn)
        sinceEnforce += dtRaw
        if (grantLeft > 0) {
            val dt = minOf(dtRaw, tickClamp, grantLeft)
            grantLeft -= dt
            if (grantEnd > 0 && (grantEnd - clock()) / 1000.0 < grantLeft - 1)
                grantLeft = maxOf(0.0, (grantEnd - clock()) / 1000.0)      // 按墙上时间到点 (设备睡眠过等)
            // 同一次联网在各设备上一起进行: 只由发起的设备扣额度 (它不在线时由本机扣)
            if (chargeOk(liveOwner)) {
                countOnline(dt)
                used += dt * grantRate
            }
            sinceSave += dt
            if (grantLeft <= 1e-6) {
                liveId += "e"
                resetGrant()
                backend(false)
                save()
                log("END      time is up")
                event = "ended"
            } else if (sinceSave >= 5) {
                save()
            }
        }
        if (sinceEnforce >= enforceEvery) {
            sinceEnforce = 0.0
            backend(true)
        }
        return event
    }
}
