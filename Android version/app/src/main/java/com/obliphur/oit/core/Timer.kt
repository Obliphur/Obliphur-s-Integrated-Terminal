package com.obliphur.oit.core

import org.json.JSONObject
import java.io.File
import java.time.LocalDateTime
import java.time.temporal.IsoFields

enum class TState { IDLE, RUNNING, ON_BREAK }

/**
 * 专注计时器 (MECHANICS 6). 对应桌面版 app.py 里的计时器部分.
 * mono(): 单调时钟 (秒, 包括睡眠), 墙上时间用 LocalDateTime.now().
 */
class TimerEngine(private val core: Core, dir: File, private val mono: () -> Double) {
    private val file = File(dir, "timer_state.json")
    private val dm get() = core.dm
    private val settings get() = core.settings

    var state = TState.IDLE
        private set
    private var startMono = 0.0
    var startWall: LocalDateTime? = null
        private set
    var breakStartWall: LocalDateTime? = null
        private set
    private var breakEndMono = 0.0
    private var idleSince: LocalDateTime? = null
    private var nudges = 0
    private var nudgeId: Int? = null
    var bonusClicks = 0
        private set
    private var bonusFirst = 0.0
    var bonusBreakActive = false
        private set
    /** 完成合约后的第一次休息: 暂停时结转比例 1 - (1 - x) / 2 (MECHANICS 18). */
    private var contractBreakActive = false
    /** 这次休息里有多少秒是合约开始前积攒的 (放在最后用; 暂停时原样还回去). */
    var breakPre = 0.0
        private set
    /** 这次休息是在哪个合约中开始的 (0 = 不是; 合约结束后暂停: 只还合约开始前的那部分). */
    var breakCid = 0L
    /** 刚结束的那段专注的标签 (开始于哪个合约, 0 = 普通专注): 下一次休息按它的规则. */
    private var restTag = 0L
        private set
    /** 合约在休息中自然结束了, 这次休息继续 (手动暂停立即清空合约内获得的时间, 自然走完变空闲). */
    var contractRestOver = false
    /** 刚结束的那段专注换来的休息时间 (秒, 已乘倍率; 合约中已乘疲惫). */
    private var restBase = 0.0
    private var focusChecks = 0
    private var focusOk = 0.0
    private var checkId: Int? = null
    private var focusSaved = 0.0
    var lastTMinutes = 0.0
        private set
    var breakCarry = 0
        private set
    /** 非手动结束的专注 (没回应"还在吗", 程序退出, 共享的专注被终止) 存进积攒的休息时间、还没折算的部分 (6.8). */
    var autoRest = 0
        private set
    private var extra: JSONObject? = null
    /** 设备同步: 最后改动计时器状态的设备 (空 = 本机); 自动结束 / 记录由它做. */
    var owner = ""
        private set
    /** 这次休息结束的时刻 (墙上时间, 同步用). */
    private var breakEndWall: LocalDateTime? = null

    init {
        breakCarry = maxOf(0, readState().optInt("break_carry", 0))
        autoRest = readState().optInt("auto_rest", 0).coerceIn(0, breakCarry)
    }

    private fun readState(): JSONObject = Store.readObj(file) ?: JSONObject()

    val elapsed get() = if (state == TState.RUNNING) mono() - startMono else 0.0
    /** 这段专注的时间按多少增长: 阻滞 0.9, 否则 1. */
    val focusRate get() = if (state == TState.RUNNING) core.contract.focusRate(startWall) else 1.0
    val countdown get() = if (state == TState.ON_BREAK) maxOf(0.0, breakEndMono - mono()) else 0.0

    /** 不能暂停休息的原因 ("" = 可以): 网络正在使用 (领到休息时间后的第一次休息除外). */
    val pauseBlock: String get() = when {
        state != TState.ON_BREAK -> ""
        core.net.mode == "online" && !bonusBreakActive -> tr("网络正在使用,不能暂停休息.")
        else -> ""
    }
    val breakLocked get() = pauseBlock.isNotEmpty()

    // ---- 两个主按钮 ----

    fun onAction() {
        mine()
        if (state == TState.IDLE && core.behavior.active() != null) {
            core.toast(tr("外出中, 先结束外出再开始专注.")); return
        }
        when (state) {
            TState.IDLE -> {
                settleAutoRest()                 // 非手动结束的专注留下的休息时间: 先折算一次 (6.8)
                enterFocus(LocalDateTime.now(), mono())
                if (core.contract.slowBind(startWall!!))       // 阻滞: 合约内这段时间按 90% 增长
                    core.toast(tr("阻滞: 合约内这段专注的时间按 {p}% 增长", "p" to Math.round(Contract.RANDOM_SLOW * 100)), "warn", long = true)
            }
            TState.RUNNING -> {
                val end = LocalDateTime.now()
                val minutes = core.contract.focusMinutes(startWall, mono() - startMono)   // 阻滞: 只减慢合约内的那部分时间
                lastTMinutes = minutes                    // 建议休息时长照常按整段专注算
                restBase = core.focusRest(minutes, startWall!!, end)
                val off = cutOffline(startWall!!, end)
                dm.addStudySession(startWall!!, end, maxOf(0.0, minutes - off / 60.0))
                addStudyPieces(startWall!!, end)
                closeCheck()
                // 合约专注 (开始于合约里) 的下一次休息按合约规则 (合约在专注中完成了也一样, MECHANICS 18.2)
                restTag = core.contract.focusTag(startWall)
                state = TState.IDLE          // 专注已记下: 先清掉文件里的专注状态
                saveState()
                startBreak(announce = true)
                // 纵深推进: 这段专注不到要求的时长, 算一次违反
                core.contract.focusCheck(minutes * 60).ifEmpty { null }?.let {
                    core.contractViolation(tr("专注只有 {m} 分钟 (至少 {n} 分钟)", "m" to minutes.toInt(),
                        "n" to Contract.DEEP[it]!! / 60), it)
                }
            }
            TState.ON_BREAK -> {
                if (breakLocked) {
                    core.toast(pauseBlock, "warn"); return
                }
                endBreakEarly()
                if (state == TState.IDLE) onAction()
            }
        }
        core.changed()
    }

    fun onBreakButton() {
        mine()
        if (state == TState.IDLE && core.behavior.active() != null) {
            core.toast(tr("外出中, 先结束外出再休息.")); return
        }
        when (state) {
            TState.IDLE -> {
                if (breakCarry <= 0) {
                    core.toast(tr("还没有积攒的休息时间."), "info"); return
                }
                lastTMinutes = 0.0
                restBase = 0.0
                restTag = 0L                         // 不是接着专注的休息: 按普通规则
                val total = breakCarry
                startBreak()
                if (state == TState.ON_BREAK) core.toast(tr("开始休息 {t}.", "t" to fmtMs(total.toDouble())), "success")
            }
            TState.ON_BREAK -> endBreakEarly()
            else -> {}
        }
        core.changed()
    }

    // ---- 连点 10 次换休息时间 (6.5) ----

    private fun week(t: LocalDateTime = LocalDateTime.now()) =
        "%d-W%02d".format(t.get(IsoFields.WEEK_BASED_YEAR), t.get(IsoFields.WEEK_OF_WEEK_BASED_YEAR))

    fun bonusLeftThisWeek(): Int {
        val d = readState()
        val used = if (d.optString("bonus_week") == week()) d.optInt("bonus_used", 0) else 0
        return maxOf(0, Cfg.BONUS_BREAK_WEEKLY - used)
    }

    fun bonusLabel(): String = when {
        bonusClicks > 0 -> tr("☕  我累了 ({i})", "i" to bonusClicks)
        bonusLeftThisWeek() <= 0 -> tr("☕  本周已领完")
        else -> tr("☕  我累了")
    }

    fun onBonusClick() {
        if (core.contract.active) return            // 合约中"我累了"按钮隐藏
        val now = mono()
        if (bonusClicks == 0 || now - bonusFirst > Cfg.BONUS_BREAK_WINDOW) {
            bonusClicks = 0
            bonusFirst = now
        }
        bonusClicks += 1
        if (bonusClicks >= Cfg.BONUS_BREAK_CLICKS) {
            bonusClicks = 0
            grantBonus()
        }
        core.changed()
    }

    /** 指令给的休息时间 (MECHANICS 22.3): 正在休息就加进这次休息, 否则存进积攒的休息时间. */
    fun grantRest(seconds: Int) {
        if (seconds <= 0 || dm.resetting) return
        mine()
        if (state == TState.ON_BREAK) {
            breakEndMono += seconds
            breakEndWall = breakEndWall?.plusSeconds(seconds.toLong())
            saveState()
        } else {
            setCarry(breakCarry + seconds)
        }
        core.changed()
    }

    private fun grantBonus() {
        val left = bonusLeftThisWeek()
        if (left <= 0) {
            core.toast(tr("这一周的 {h} 已经领完了,下周一再来.", "h" to fmtHm(Cfg.BONUS_BREAK_WEEKLY.toDouble())), "warn")
            return
        }
        val add = minOf(Cfg.BONUS_BREAK_SECONDS, left)
        val d = readState()
        val w = week()
        val used = if (d.optString("bonus_week") == w) d.optInt("bonus_used", 0) else 0
        val count = (if (d.optString("bonus_week") == w) d.optInt("bonus_count", 0) else 0) + 1
        extra = JSONObject().put("bonus_week", w).put("bonus_used", used + add).put("bonus_count", count)
        var where: String
        mine()
        if (state == TState.ON_BREAK) {
            breakEndMono += add                // 正在休息: 直接加到这次休息里
            breakEndWall = breakEndWall?.plusSeconds(add.toLong())
            bonusBreakActive = true
            saveState()
            where = tr("已加到这次休息里.") + "\n" + tr("这次休息可以随时暂停,剩余时间不打折.")
        } else {
            extra!!.put("bonus_pending", true)
            setCarry(breakCarry + add)
            where = tr("已存入积攒的休息时间.") + "\n" + tr("下一次休息可以随时暂停,剩余时间不打折.")
        }
        var msg = tr("获得 {t} 休息时间.", "t" to fmtMs(add.toDouble())) + where
        msg += "\n" + tr("本周还能领 {left}.", "left" to fmtHm((left - add).toDouble()))
        msg += "\n\n" + tr("这么想休息,也许是真的累了.休息不需要理由.")
        msg += "\n" + tr("不要勉强.")
        core.toast(msg, "success", long = true)
        if (count == Cfg.BONUS_TIRED_TIMES) core.tiredNotice()
    }

    /** 1 分钟内没有点满: 次数清零. */
    private fun bonusExpire() {
        if (bonusClicks > 0 && mono() - bonusFirst > Cfg.BONUS_BREAK_WINDOW) {
            bonusClicks = 0
            core.changed()
        }
    }

    // ---- 休息 ----

    /** 本次休息总秒数 = 建议休息时长 + 积攒的休息时间. */
    fun breakSeconds(): Int {
        val base = Math.round(maxOf(0.0, restBase)).toInt()
        val total = base + breakCarry
        return if (total > 0) maxOf(1, total) else 0
    }

    private fun startBreak(announce: Boolean = false) {
        val total = breakSeconds()
        if (total <= 0) {
            core.toast(tr("剩余时间为 0,已重置."), "info")
            resetToIdle(); return
        }
        val carry = breakCarry
        val tag = restTag
        restTag = 0L
        val c = core.contract
        // 合约专注的下一次休息, 合约已经完成了: 仍按合约规则 —— 这段专注换来的是合约休息时间 (暂停时清空),
        // 积攒的休息时间 (合约开始前剩下的 + 完成奖励) 是正常休息时间, 放在最后用, 暂停时原样留下不打折
        val after = !c.active && c.completed(tag)
        val bonus = readState().optBoolean("bonus_pending", false)
        if (bonus) {
            bonusBreakActive = true
            extra = JSONObject().put("bonus_pending", false)
        }
        if (!c.active && !after && c.takeFirstBreak()) {
            contractBreakActive = true
            core.toast(tr("合约完成后的第一次休息: 暂停时剩余时间 ×{r} 留给下次.",
                "r" to numText(Contract.firstBreakRate(settings.num("BREAK_CARRY_RATE")))), "info", long = true)
        }
        breakPre = if (c.active) c.preTake(carry.toDouble()) else 0.0
        breakCid = if (c.active) c.id else 0L
        contractRestOver = false
        if (after) { breakPre = carry.toDouble(); breakCid = tag; contractRestOver = true }
        core.supplyWarned.clear()
        breakCarry = 0
        autoRest = 0                             // 一起用进这次休息, 结束休息时照常折算 (6.8)
        breakEndMono = mono() + total
        state = TState.ON_BREAK
        breakStartWall = LocalDateTime.now().withNano(0)
        breakEndWall = breakStartWall!!.plusSeconds(total.toLong())
        saveState()
        if (bonus) core.toast(tr("这次休息可以随时暂停,剩余时间不打折."), "info", long = true)
        if (announce) {
            var msg = tr("本次专注 {m} 分钟,开始休息 {t}.", "m" to f1(lastTMinutes), "t" to fmtMs(total.toDouble()))
            if (carry > 0) msg += "\n" + tr("(含之前暂停休息结转的 {c})", "c" to fmtMs(carry.toDouble()))
            core.toast(msg, "success")
        }
    }

    /** 暂停休息: 剩余时间 × BREAK_CARRY_RATE 留给下一次休息. */
    fun endBreakEarly(silent: Boolean = false) {
        if (state != TState.ON_BREAK) return
        if (!silent && breakLocked) {
            core.toast(pauseBlock, "warn"); return
        }
        if (silent && !autoOk()) breakStartWall = null
        recordBreakEnd()
        val left = countdown.toInt()
        val full = bonusBreakActive
        var rate = settings.num("BREAK_CARRY_RATE")
        if (contractBreakActive) rate = Contract.firstBreakRate(rate)
        // 睡觉时间 (MECHANICS 19): 窗口内、非合约期间的第一次暂停休息, 结转比例 +5%
        val night = !silent && !core.contract.active && core.daily.takeBreakNotice()
        if (night) rate = core.daily.breakRate(rate)
        bonusBreakActive = false
        contractBreakActive = false
        // 合约中: 剩余时间里合约开始前积攒的那部分 (放在最后用) 原样还回去, 不打折;
        // 合约中开始的休息, 合约已经结束了: 只还这部分 (合约中得到的休息时间不留到合约之后)
        val pre = minOf(left, breakPre.toInt())
        val c = core.contract
        val same = c.active && c.id == breakCid
        val ended = breakCid != 0L && !same
        if (same) c.preSettle(breakPre, pre.toDouble())
        breakPre = 0.0
        breakCid = 0L
        // 领到休息时间后的第一次休息: 暂停时剩余时间全部保留, 不按倍率打折 (盖过合约完成后的比例)
        val add = when {
            ended -> pre
            full -> left
            else -> pre + Math.round((left - pre) * rate).toInt()
        }
        if (add > 0) {
            breakCarry += add
            if (!silent) {
                if (full || add == left) core.toast(tr("休息已暂停,剩余 {left} 留给下次.", "left" to fmtMs(left.toDouble())), "info")
                else core.toast(tr("休息已暂停,下次休息 +{add}.", "add" to fmtMs(add.toDouble())), "info")
            }
        }
        resetToIdle()
        if (!silent) {
            armIdleNudge()
            // 禁止囤囤鼠!: 提前结束休息照样能结束, 算一次违反 (软规则)
            c.hoardCheck().ifEmpty { null }?.let { core.contractViolation(tr("提前结束了休息"), it) }
            if (night) core.sleepNotice()
        }
    }

    private fun recordBreakEnd(at: LocalDateTime = LocalDateTime.now()) {
        breakStartWall?.let {
            dm.addFocusSession("break", it, at)       // 有设备离线也照常记
        }
        breakStartWall = null
    }

    private fun resetToIdle() {
        state = TState.IDLE
        startWall = null
        lastTMinutes = 0.0
        restBase = 0.0
        breakEndMono = 0.0
        contractRestOver = false
        saveState()
        core.changed()
    }

    // ---- 休息结束后迟迟不开始 (6.4) ----

    private fun armIdleNudge() {
        idleSince = LocalDateTime.now()
        nudges = 0
    }

    private fun checkIdleNudge() {
        val since = idleSince ?: return
        if (state != TState.IDLE) {
            idleSince = null; return
        }
        if (!core.isLatestActive()) return     // 只在最后有人操作的设备上提醒
        val now = LocalDateTime.now()
        var due = since.plusSeconds(Cfg.IDLE_NUDGE_FIRST + Cfg.IDLE_NUDGE_EVERY * nudges)
        if (now.isBefore(due)) return
        while (!due.isAfter(now)) {
            nudges += 1
            due = due.plusSeconds(Cfg.IDLE_NUDGE_EVERY)
        }
        if (nudgeId?.let { core.noticeOpen(it) } == true) return   // 上一个提醒还开着, 不重复弹
        val idle = fmtHm(secondsBetween(since, now))
        nudgeId = core.notice(tr("⏱  该开始下一轮了"),
            tr("休息结束已经 {t} 了,还没有开始下一轮专注.", "t" to idle),
            listOf(NoticeButton(tr("▶  开始专注"), "start", true), NoticeButton(tr("知道了"), "ok", false)),
            page = "timer") { action ->
            if (action == "start" && state == TState.IDLE) onAction()
        }
    }

    // ---- 专注状态: 随时写进文件, 进程被杀也不丢 (6.7) ----

    private fun saveState() {
        if (dm.resetting) return
        val d = readState()
        extra?.let { e -> for (k in e.keys()) d.put(k, e.get(k)) }
        extra = null
        d.put("break_carry", breakCarry)
        d.put("auto_rest", autoRest)
        // owner: 重开时原样恢复, 同步的计时器状态才不会因为"主人变了"被当成新的改动 (盖掉其他设备更新的状态)
        val f = if (state == TState.RUNNING && startWall != null) JSONObject()
            .put("start", startWall!!.iso()).put("alive", LocalDateTime.now().iso())
            .put("checks", focusChecks).put("ok", focusOk.toLong()).put("owner", owner)
            // 关机 / 电量很低: 重新打开时不把关着的这段记成空闲, 跟着其他设备 (MECHANICS 15.4)
            .put("off", runCatching { core.wentOff() }.getOrDefault(false)) else JSONObject.NULL
        d.put("focus", f)
        // Android: 休息也写进文件, 进程被系统回收后照样接着倒计时
        val b = if (state == TState.ON_BREAK && breakStartWall != null) JSONObject()
            .put("start", breakStartWall!!.iso())
            .put("end", (breakEndWall ?: LocalDateTime.now().plusSec(countdown)).iso())
            .put("bonus", bonusBreakActive).put("cbreak", contractBreakActive)
            .put("cpre", breakPre).put("ccid", breakCid).put("crestover", contractRestOver) else JSONObject.NULL
        d.put("break", b)
        focusSaved = mono()
        Store.write(file, d)
    }

    /** 合约结束 (MECHANICS 18.2): 积攒的休息时间只留下合约开始前积攒的那部分, 加上奖励. */
    fun contractEnded(preLeft: Double, reward: Int) {
        setCarry(minOf(breakCarry, preLeft.toInt()) + reward)
    }

    /** 结束合约前先停止休息 (MECHANICS 18.1): 剩下的休息时间按"暂停休息"算, 合约中得到的部分由 endContract 作废.
     *  不算禁止囤囤鼠! 的违反 (不是手动提前结束). */
    fun stopBreakForContract() {
        if (state != TState.ON_BREAK) return
        val left = maxOf(0, countdown.toInt())
        recordBreakEnd()
        val pre = minOf(left, breakPre.toInt())
        val c = core.contract
        val same = c.active && c.id == breakCid
        if (same) c.preSettle(breakPre, pre.toDouble())
        var rate = settings.num("BREAK_CARRY_RATE")
        if (contractBreakActive) rate = Contract.firstBreakRate(rate)
        val add = if (bonusBreakActive) left else pre + Math.round((left - pre) * rate).toInt()
        breakPre = 0.0
        breakCid = 0L
        bonusBreakActive = false
        contractBreakActive = false
        if (add > 0) setCarry(breakCarry + add)
        resetToIdle()
        armIdleNudge()
    }

    /** 合约非自然结束 (失败 / 手动终止, MECHANICS 18.1): 正在进行的专注立即结束. 学习时长照常记,
     *  这段专注换来的休息时间是合约中获得的, 随合约一起清空 —— 不开始休息, 回到空闲. */
    fun stopFocusForContract() {
        if (state != TState.RUNNING) return
        val start = startWall ?: return
        val sec = mono() - startMono
        closeCheck()
        bookFocus(start, sec, null)              // 休息时间不要
        restTag = 0L
        mine()
        resetToIdle()
        saveState()
        armIdleNudge()
    }

    /** 合约开始时多给的休息时间 (凑齐的词条组合), 存进积攒的休息时间. */
    fun addCarry(seconds: Int) {
        if (seconds > 0) setCarry(breakCarry + seconds)
    }

    /** 未知变量: 休息时间 +/- delta 秒. 正在休息时改这次休息 (先减合约中得到的, 合约开始前的放在最后),
     *  否则改积攒的休息时间. */
    fun adjustBreak(delta: Int) {
        mine()
        if (state == TState.ON_BREAK) {
            val left = countdown.toInt()
            val new = maxOf(0, left + delta)
            if (new < left) {
                val preIn = minOf(left, breakPre.toInt())
                val lost = maxOf(0, (left - new) - (left - preIn))
                breakPre = maxOf(0.0, breakPre - lost)
            }
            breakEndMono += (new - left)
            breakEndWall = breakEndWall?.plusSeconds((new - left).toLong())
            saveState()
        } else {
            setCarry(breakCarry + delta)
            core.contract.preCap(breakCarry.toDouble())
        }
        core.changed()
    }

    private fun setCarry(seconds: Int) {
        breakCarry = maxOf(0, seconds)
        autoRest = minOf(autoRest, breakCarry)
        saveState()
    }

    /** 非手动结束的专注: 休息时间存进积攒的休息时间, 记下这部分还没折算 (6.8). */
    private fun addAutoRest(seconds: Int) {
        autoRest = minOf(breakCarry, autoRest + maxOf(0, seconds))
        saveState()
    }

    /** 手动开始专注 (6.8): 非手动结束的专注留下的那部分休息时间, 先按暂停休息的结转比例折算一次. */
    private fun settleAutoRest() {
        val a = minOf(autoRest, breakCarry)
        autoRest = 0
        if (a <= 0) {
            saveState(); return
        }
        val rate = settings.num("BREAK_CARRY_RATE")
        val keep = Math.round(a * rate).toInt()
        setCarry(breakCarry - (a - keep))
        if (core.contract.active) core.contract.preCap(breakCarry.toDouble())
        core.toast(tr("休息时间折算: 专注自动结束留下的 {a} ×{r} = {k}.", "a" to fmtMs(a.toDouble()),
            "r" to numText(rate), "k" to fmtMs(keep.toDouble())), "info", long = true)
    }

    /** 设备同步: 本周已领的连点休息时间 (周, 秒). */
    fun bonusState(): Pair<String, Int> = readState().let { it.optString("bonus_week") to it.optInt("bonus_used", 0) }

    /** 设备同步: 本周领了几次. */
    fun bonusCount(): Int = readState().optInt("bonus_count", 0)

    /** 设备同步: 积攒的休息时间 / 本周连点领取的记录 (同步来的值). */
    fun syncSet(carry: Int?, bonusWeek: String?, bonusUsed: Int?, bonusCount: Int? = null, auto: Int? = null) {
        if (bonusWeek != null) extra = JSONObject().put("bonus_week", bonusWeek).put("bonus_used", bonusUsed ?: 0)
            .put("bonus_count", bonusCount ?: 0)
        if (carry != null) breakCarry = maxOf(0, carry)
        if (auto != null) autoRest = maxOf(0, auto)
        autoRest = minOf(autoRest, breakCarry)
        saveState()
        core.changed()
    }

    fun saveNow() {
        if (state != TState.IDLE) saveState()
    }

    private fun enterFocus(wall: LocalDateTime, m: Double, checks: Int = 0, ok: Double = 0.0) {
        startWall = wall
        startMono = m
        focusChecks = checks
        focusOk = ok
        closeCheck()
        state = TState.RUNNING
        saveState()
        core.changed()
    }

    /** 启动时: 上次退出时还在专注 / 休息 → 接着计时或记下来. */
    fun recover() {
        val d = readState()
        val brk = d.optJSONObject("break")
        if (brk != null) {
            val st = parseIso(brk.optString("start"))
            val en = parseIso(brk.optString("end"))
            val now = LocalDateTime.now()
            if (st != null && en != null && !st.isAfter(now)) {
                if (en.isAfter(now)) {
                    state = TState.ON_BREAK
                    breakStartWall = st
                    breakEndMono = mono() + secondsBetween(now, en)
                    breakEndWall = en
                    bonusBreakActive = brk.optBoolean("bonus", false)
                    contractBreakActive = brk.optBoolean("cbreak", false)
                    breakPre = brk.optDouble("cpre", 0.0)
                    breakCid = brk.optLong("ccid", 0L)
                    contractRestOver = brk.optBoolean("crestover", false)
                    core.changed()
                    return
                }
                dm.addFocusSession("break", st, en)
            }
            saveState()
        }
        val rec = d.optJSONObject("focus") ?: return
        val start = parseIso(rec.optString("start"))
        var alive = parseIso(rec.optString("alive"))
        val checks = maxOf(0, rec.optInt("checks", 0))
        var ok = maxOf(0.0, rec.dbl("ok"))
        val now = LocalDateTime.now()
        if (start == null || alive == null || start.isAfter(now)) {
            saveState(); return               // 记录坏了或系统时间被往回调过: 丢掉
        }
        if (alive.isBefore(start)) alive = start
        if (alive.isAfter(now)) alive = now
        owner = rec.optString("owner")
        if (rec.optBoolean("off") && secondsBetween(alive, now) > Cfg.CLOSED_GAP) {
            // 本机是关机 / 没电停下的 (MECHANICS 15.4): 效果和在线一样, 专注照常接着计时 ("还在吗"照常按开始时刻算);
            // 关着的这段本机的数据不计入 (见 cutOffline). 绑定了其他设备时, 同步上以后跟着它们
            enterFocus(start, mono() - secondsBetween(start, now), checks, ok)
            core.toast(tr("关机期间专注照常进行, 已接着计时."), "info", long = true)
            return
        }
        if (core.hasPeers() && secondsBetween(alive, now) > Cfg.CLOSED_GAP) {
            // 绑定了其他设备, 共享的专注中 OIT 被关掉了: 关掉的这段记为空闲 (总统计里优先级最高),
            // 专注照常恢复, 等第一次同步看其他设备是不是已经结束了它, 没有就由本机终止 (checkClosePending).
            // 本机是关机 / 没电了 (MECHANICS 15.4): 其他设备把本机算作在线, 这段不记空闲; 同步上了就跟着它们
            val off = rec.optBoolean("off")
            if (!off) dm.addFocusSession("idle", alive, now, start)
            enterFocus(start, mono() - secondsBetween(start, now), checks, ok)
            closePending = ClosePending(start, alive, mono(), System.currentTimeMillis(), off, now)
            return
        }
        if (secondsBetween(alive, now) <= Cfg.FOCUS_RESUME_GAP) {
            enterFocus(start, mono() - secondsBetween(start, now), checks, ok)
            core.toast(tr("上次专注时程序退出了.已接着计时(从 {at} 开始).", "at" to start.format(Fmt.HM)), "info", long = true)
            return
        }
        var sec = secondsBetween(start, alive)
        val limit = checkMark(checks + 1, start) + Cfg.FOCUS_CHECK_GRACE
        val penalized = sec >= limit
        if (penalized) sec = limit
        val (minutes, brkSec) = bookFocus(start, sec, if (penalized) ok else null)
        setCarry(breakCarry + brkSec)
        addAutoRest(brkSec)
        if (!rec.optBoolean("off")) deepCheckClosed(start, sec)
        if (minutes <= 0) return
        var msg: String
        if (penalized) {
            ok = minOf(ok, sec)
            msg = tr("上次专注时程序退出了,专注已按{at}自动结束.", "at" to start.plusSec(sec).format(Fmt.MDHM))
            msg += "\n" + tr("没有确认的部分按×{p}计入,共计入{m}分钟.", "p" to numText(Cfg.FOCUS_CHECK_PENALTY), "m" to f1(minutes))
        } else {
            msg = tr("上次专注时程序退出了,专注记到 {at} 为止: 计入 {m} 分钟.", "at" to alive.format(Fmt.MDHM), "m" to f1(minutes))
        }
        if (brkSec > 0) msg += "\n" + tr("积攒的休息时间 +{t}.", "t" to fmtMs(brkSec.toDouble())) + "\n" + tr("立即开始专注:先进行一次休息时间的折算")
        core.notice(tr("⏱  上次的专注已记录"), msg, listOf(NoticeButton(tr("知道了"), "ok", true)), page = "timer")
    }

    /** 记下一段专注, 返回 (计入的分钟数, 对应的休息秒数). ok: 自动结束时最后一次确认时已专注的秒数. */
    private fun bookFocus(start: LocalDateTime, secondsIn: Double, okIn: Double?): Pair<Double, Int> {
        var seconds = maxOf(0.0, secondsIn)
        if (okIn != null) {
            val ok = okIn.coerceIn(0.0, seconds)
            seconds = ok + (seconds - ok) * Cfg.FOCUS_CHECK_PENALTY
        }
        val end = start.plusSec(seconds)
        val minutes = core.contract.focusMinutes(start, seconds)      // 阻滞: 只减慢合约内的那部分时间
        // 有设备离线的那段记为空闲, 不计入学习时长; 建议休息时长照常按计入前的时长算
        val counted = maxOf(0.0, minutes - cutOffline(start, end) / 60.0)
        dm.addStudySession(start, end, counted)
        addStudyPieces(start, end)
        val brk = Math.round(core.focusRest(minutes, start, end)).toInt()
        return counted to brk
    }

    /** 专注停止 (手动结束 / 提醒超时自动结束 / OIT 被关掉等, 非关机) 时按纵深推进检查这段够不够长 (18.3).
     *  关机 / 断电导致的停止由调用方用 off 判断豁免 (MECHANICS 15.4), 不走到这里. */
    private fun deepCheckClosed(start: LocalDateTime, seconds: Double) {
        val minutes = core.contract.focusMinutes(start, seconds)
        core.contract.focusCheck(minutes * 60).ifEmpty { null }?.let {
            core.contractViolation(tr("专注只有 {m} 分钟 (至少 {n} 分钟)", "m" to minutes.toInt(),
                "n" to Contract.DEEP[it]!! / 60), it)
        }
    }

    private fun ms(t: LocalDateTime) = t.atZone(java.time.ZoneId.systemDefault()).toInstant().toEpochMilli()
    private fun wall(ms: Long) = LocalDateTime.ofInstant(java.time.Instant.ofEpochMilli(ms), java.time.ZoneId.systemDefault())

    /** [start, end] 这段专注里不计入学习时长的部分, 返回一共多少秒:
     *  有绑定的设备离线的时间段记为空闲 (统计里压过同一段专注);
     *  本机关机的时间段 (MECHANICS 15.4) 本机的数据不计入 (见 addStudyPieces), 只有关机的设备 (没有别的设备记下专注 / 休息) 时记为空闲. */
    private fun cutOffline(start: LocalDateTime, end: LocalDateTime): Double {
        val spans = core.offlineSpans(ms(start), ms(end)).filter { it.second > it.first }
        val mine = core.selfOffSpans(ms(start), ms(end)).filter { it.second > it.first }
        val idle = spans + mine.filter { (a, b) -> !dm.peerActive(wall(a), wall(b)) }
        for ((a, b) in idle) dm.addFocusSession("idle", wall(a), wall(b), start)
        var total = 0.0
        var last: Long? = null
        for ((a0, b) in (spans + mine).sortedBy { it.first }) {      // 合并后的长度 (两种重叠时不重复算)
            val a = if (last != null && a0 < last) last else a0
            if (b > a) total += (b - a) / 1000.0
            last = if (last == null) b else maxOf(last, b)
        }
        return total
    }

    /** 记下本机这段专注; 本机关机的那几段 (MECHANICS 15.4) 不记, 不参与专注计时的计算. */
    private fun addStudyPieces(start: LocalDateTime, end: LocalDateTime) {
        var a = start
        for ((x, y) in core.selfOffSpans(ms(start), ms(end))) {
            if (wall(x).isAfter(a)) dm.addFocusSession("study", a, wall(x))
            if (wall(y).isAfter(a)) a = wall(y)
        }
        if (end.isAfter(a)) dm.addFocusSession("study", a, end)
    }

    // ---- 专注中每 1.5 小时: 还在吗 (6.6) ----

    private fun closeCheck() {
        checkId?.let { core.dismissNotice(it) }
        checkId = null
    }

    private class ClosePending(val start: LocalDateTime, val alive: LocalDateTime, val mono: Double, val ms: Long,
                               val off: Boolean = false, val reopened: LocalDateTime = alive)
    private var closePending: ClosePending? = null

    /** 上次在共享的专注中 OIT 被关掉了 (recover): 第一次同步之后 (最多等 CLOSED_DECIDE_WAIT 秒),
     *  这段专注还在进行就终止它 —— 专注记到被关掉的时刻为止, 之后的那段已经记为空闲. */
    private fun checkClosePending() {
        val p = closePending ?: return
        if (!core.syncedSince(p.ms) && mono() - p.mono < Cfg.CLOSED_DECIDE_WAIT) return
        closePending = null
        if (state != TState.RUNNING || startWall != p.start) return   // 其他设备已经结束了这段专注
        if (p.off) {
            if (core.syncedSince(p.ms)) {
                // 关机期间其他设备把本机算作在线, 共享的专注照常进行: 接着计时
                core.toast(tr("关机期间共享的专注照常进行, 已接着计时."), "info", long = true)
                return
            }
            // 没能和任何设备同步上: 和普通的被关掉一样, 关着的那段记为空闲, 专注终止
            dm.addFocusSession("idle", p.alive, p.reopened, p.start)
        }
        var sec = secondsBetween(p.start, p.alive)
        val limit = checkMark(focusChecks + 1, p.start) + Cfg.FOCUS_CHECK_GRACE
        val penalized = sec >= limit
        if (penalized) sec = limit
        closeCheck()
        val (minutes, brk) = bookFocus(p.start, sec, if (penalized) focusOk else null)
        mine()                                     // 终止由本机做, 其他设备照着结束
        resetToIdle()
        setCarry(breakCarry + brk)
        addAutoRest(brk)
        if (!p.off) deepCheckClosed(p.start, sec)
        var msg = tr("这台设备上的 OIT 在 {at} 被关掉了, 共享的专注已终止.", "at" to p.alive.format(Fmt.MDHM))
        msg += "\n" + tr("专注记到被关掉时为止: 计入 {m} 分钟; 关掉的这段记为空闲.", "m" to f1(minutes))
        if (brk > 0) msg += "\n" + tr("积攒的休息时间 +{t}.", "t" to fmtMs(brk.toDouble())) + "\n" + tr("立即开始专注:先进行一次休息时间的折算")
        core.notice(tr("⏹  专注已终止"), msg, listOf(NoticeButton(tr("知道了"), "ok", true)), page = "timer")
    }

    /** 第 n 次"还在吗"提醒在开始专注后多少秒: 合约专注每次随机 40 ~ 60 分钟 (Contract.checkMark). */
    private fun checkMark(n: Int, start: LocalDateTime? = startWall): Double =
        Contract.checkMark(n, start, core.contract.focusTag(start) != 0L, Cfg.FOCUS_CHECK_EVERY)

    private fun checkFocus() {
        if (state != TState.RUNNING) {
            closeCheck(); return
        }
        if (closePending != null) return         // 先等 checkClosePending 决定这段专注还要不要
        // 绑定的设备非关机离线累计超过 5 分钟: 自动结束专注 (MECHANICS 15.4; 关机算在线, 不计)
        if (core.hasPeers() && autoOk() && offlineSecs(startWall!!) >= Cfg.OFFLINE_FOCUS_END) {
            offlineEndFocus(mono() - startMono); return
        }
        val el = mono() - startMono
        val mark = checkMark(focusChecks + 1)
        if (el >= mark + Cfg.FOCUS_CHECK_GRACE) {
            // 别的设备是这个状态的主人时等它结束, 结果会同步过来
            if (autoOk()) autoEndFocus(mark + Cfg.FOCUS_CHECK_GRACE)
            return
        }
        if (el >= mark && checkId == null) {
            val due = LocalDateTime.now().plusSec(mark + Cfg.FOCUS_CHECK_GRACE - el)
            // 合约专注: 写准确的分钟数 (间隔是随机的); 黑雾时不写专注了多久 (18.3), 其余照常显示
            val spent = when {
                core.contract.focusTag(startWall) == 0L -> tr("已经专注 {t} 了.", "t" to fmtHm(mark)) + "\n"
                core.contract.active && core.contract.fog() -> ""
                else -> tr("已经专注 {n} 分钟了.", "n" to Math.round(mark / 60).toInt()) + "\n"
            }
            checkId = core.notice(tr("⏱  还在专注吗?"),
                spent + tr("请在{at}之前关掉这个窗口,否则专注会自动结束.", "at" to due.format(Fmt.HM)),
                listOf(NoticeButton(tr("还在,继续专注"), "ok", true), NoticeButton(tr("■  结束专注"), "end", false)),
                page = "timer", tag = "check") { action ->
                checkId = null
                if (state != TState.RUNNING) return@notice
                // 被手动关掉了 = 人还在
                mine()
                focusChecks += 1
                focusOk = mono() - startMono
                saveState()
                if (action == "end") onAction()
            }
        }
        // 绑定了其他设备时写得勤一些: 被关掉之后才知道是什么时候关的
        if (mono() - focusSaved >= if (core.hasPeers()) Cfg.FOCUS_ALIVE_EVERY else Cfg.FOCUS_SAVE_EVERY) saveState()
    }

    /** 这段专注里绑定设备非关机离线累计了多少秒 (关机算在线, 不计). */
    private fun offlineSecs(start: LocalDateTime): Double =
        core.offlineSpans(ms(start), ms(LocalDateTime.now())).filter { it.second > it.first }
            .sumOf { (a, b) -> (b - a) / 1000.0 }

    /** 绑定设备非关机离线累计超过 5 分钟: 自动结束专注 (MECHANICS 15.4). 休息时间存进积攒的休息时间. */
    private fun offlineEndFocus(seconds: Double) {
        val start = startWall!!
        closeCheck()
        val (_, brk) = bookFocus(start, seconds, null)
        mine()                                     // 离线判定由本机做, 其他设备照着结束
        resetToIdle()
        setCarry(breakCarry + brk)
        addAutoRest(brk)
        deepCheckClosed(start, seconds)            // 任何原因停止都查纵深推进
        var msg = tr("绑定的设备离线累计超过 {n} 分钟, 专注已自动结束.", "n" to (Cfg.OFFLINE_FOCUS_END / 60).toInt())
        if (brk > 0) msg += "\n" + tr("积攒的休息时间 +{t}.", "t" to fmtMs(brk.toDouble())) + "\n" + tr("立即开始专注:先进行一次休息时间的折算")
        core.notice(tr("⏹  专注已自动结束"), msg, listOf(NoticeButton(tr("知道了"), "ok", true)), page = "timer")
    }

    /** 提醒窗没人关: 自动结束专注. 不开始休息倒计时 (人不在), 休息时间存进积攒的休息时间. */
    private fun autoEndFocus(seconds: Double) {
        val start = startWall!!
        val ok = minOf(focusOk, seconds)
        closeCheck()
        val (minutes, brk) = bookFocus(start, seconds, ok)
        mine()                                     // 自动结束由本机做, 其他设备照着结束
        resetToIdle()
        setCarry(breakCarry + brk)
        addAutoRest(brk)
        deepCheckClosed(start, seconds)            // 纵深推进: 自动结束的这段也查够不够长 (任何原因停止都查)
        var msg = tr("提醒窗{n}分钟内没有被关掉,专注已在{at}自动结束.",
            "n" to Math.round(Cfg.FOCUS_CHECK_GRACE / 60).toInt(), "at" to start.plusSec(seconds).format(Fmt.HM))
        msg += "\n" + tr("没有确认的部分按×{p}计入,共计入{m}分钟.", "p" to numText(Cfg.FOCUS_CHECK_PENALTY), "m" to f1(minutes))
        if (brk > 0) msg += "\n" + tr("积攒的休息时间 +{t}.", "t" to fmtMs(brk.toDouble())) + "\n" + tr("立即开始专注:先进行一次休息时间的折算")
        core.notice(tr("⏹  专注已自动结束"), msg, listOf(NoticeButton(tr("知道了"), "ok", true)), page = "timer")
    }

    // ---- 定时 ----

    /** 每秒. */
    fun tick() {
        bonusExpire()
        if (state == TState.ON_BREAK && countdown <= 0) {
            // 设备睡眠中走完的: 按倒计时真正走完的时刻记; 这一段由开始休息的那台设备记
            if (!autoOk()) breakStartWall = null
            recordBreakEnd(LocalDateTime.now().plusSec(-(mono() - breakEndMono).coerceAtLeast(0.0)))
            bonusBreakActive = false
            contractBreakActive = false
            if (core.contract.active && core.contract.id == breakCid) core.contract.preSettle(breakPre, 0.0)
            breakPre = 0.0
            breakCid = 0L
            core.toast(tr("休息结束,可以开始下一轮了!"), "success")
            resetToIdle()
            armIdleNudge()
        }
    }

    /** 每 5 秒. */
    fun check() {
        checkClosePending()
        checkFocus()
        checkIdleNudge()
    }

    /** 下一次需要醒来处理的时刻 (秒, 相对现在); 没有就是 null. */
    fun nextDeadline(): Double? = when (state) {
        TState.ON_BREAK -> countdown
        TState.RUNNING -> {
            val mark = checkMark(focusChecks + 1)
            val el = elapsed
            if (el < mark) mark - el else maxOf(0.0, mark + Cfg.FOCUS_CHECK_GRACE - el)
        }
        TState.IDLE -> idleSince?.let {
            val due = it.plusSeconds(Cfg.IDLE_NUDGE_FIRST + Cfg.IDLE_NUDGE_EVERY * nudges)
            maxOf(0.0, secondsBetween(LocalDateTime.now(), due))
        }
    }

    /** 正在进行的一段, 供统计页画到此刻. */
    fun live(): Pair<String, LocalDateTime>? = when {
        state == TState.RUNNING && startWall != null -> "study" to startWall!!
        state == TState.ON_BREAK && breakStartWall != null -> "break" to breakStartWall!!
        else -> null
    }

    // ---- 设备同步: 计时器此刻的状态 (MECHANICS 15.2) ----

    /** 本机改动了计时器状态: 本机成为这个状态的主人. */
    private fun mine() {
        owner = core.myId()
        core.lastOp = System.currentTimeMillis()
    }

    /** 自动结束专注, 记下休息这类没人按按钮的操作由主人做; 主人不在线 (2 分钟没同步) 时本机来做. */
    private fun autoOk(): Boolean = owner.isEmpty() || owner == core.myId() || !core.peerFresh(owner)

    fun liveState(): JSONObject {
        val o = owner.ifEmpty { core.myId() }
        val sw = startWall
        val bs = breakStartWall
        val be = breakEndWall
        return when {
            state == TState.RUNNING && sw != null -> JSONObject().put("state", "running").put("owner", o)
                .put("start", sw.iso()).put("checks", focusChecks).put("ok", focusOk.toLong())
            state == TState.ON_BREAK && bs != null && be != null -> JSONObject().put("state", "break").put("owner", o)
                .put("break_start", bs.iso()).put("break_end", be.iso()).put("bonus", bonusBreakActive)
                .put("cbreak", contractBreakActive).put("cpre", breakPre).put("ccid", breakCid)
                .put("crestover", contractRestOver)
            else -> JSONObject().put("state", "idle").put("owner", o)
        }
    }

    /** 别的设备上更新的计时器状态: 照着切换, 不记录本机原来那一段 (那台设备会记). */
    fun adopt(l: JSONObject) {
        val cur = liveState()
        val st = l.optString("state")
        val newOwner = l.optString("owner")
        if (st == cur.optString("state") && l.optString("start") == cur.optString("start") &&
            l.optString("break_end") == cur.optString("break_end")) {
            if (st == "running" && l.optInt("checks") > focusChecks) {
                closeCheck()                    // 在别的设备上确认过"还在吗"了
                focusChecks = l.optInt("checks")
                focusOk = l.optDouble("ok", 0.0)
            }
            owner = newOwner
            saveState()
            return
        }
        closeCheck()
        idleSince = null
        val now = LocalDateTime.now()
        when (st) {
            "running" -> {
                val start = parseIso(l.optString("start")) ?: return
                breakStartWall = null
                enterFocus(start, mono() - secondsBetween(start, now), l.optInt("checks"), l.optDouble("ok", 0.0))
            }
            "break" -> {
                val end = parseIso(l.optString("break_end")) ?: return
                val left = secondsBetween(now, end)
                if (left <= 0) {
                    breakStartWall = null
                    resetToIdle()
                } else {
                    state = TState.ON_BREAK
                    startWall = null
                    breakStartWall = parseIso(l.optString("break_start"))
                    breakEndWall = end
                    breakEndMono = mono() + left
                    bonusBreakActive = l.optBoolean("bonus")
                    contractBreakActive = l.optBoolean("cbreak")
                    breakPre = l.optDouble("cpre", 0.0)
                    breakCid = l.optLong("ccid", 0L)
                    contractRestOver = l.optBoolean("crestover", false)
                }
            }
            else -> {
                breakStartWall = null
                resetToIdle()
            }
        }
        owner = newOwner
        saveState()
        core.changed()
    }

    /** 重置: 进行中的专注不记录. */
    fun abandon() {
        closeCheck()
        nudgeId?.let { core.dismissNotice(it) }
        state = TState.IDLE
    }
}
