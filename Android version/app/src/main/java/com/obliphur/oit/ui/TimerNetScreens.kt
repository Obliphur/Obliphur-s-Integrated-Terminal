package com.obliphur.oit.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.obliphur.oit.OitApp
import com.obliphur.oit.core.Fmt
import com.obliphur.oit.core.TState
import com.obliphur.oit.core.computeF
import com.obliphur.oit.core.f1
import com.obliphur.oit.core.f2
import com.obliphur.oit.core.fmtHm
import com.obliphur.oit.core.fmtMs
import com.obliphur.oit.core.numText
import com.obliphur.oit.core.secondsBetween
import com.obliphur.oit.core.tr
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneId

// ════════════════ Timer 页 (MECHANICS 6) ════════════════

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun TimerScreen() {
    LocalTick.current
    val o = LocalOit.current
    val core = OitApp.instance.core
    val t = core.timer
    val scale = core.settings.num("BREAK_TIME_SCALE")
    val fog = core.contract.fog()
    val outing = core.outing
    var askOuting by rememberSaveable { mutableStateOf(false) }
    var outingHours by rememberSaveable { mutableStateOf("2") }
    var askEnd by rememberSaveable { mutableStateOf(false) }
    PageColumn {
        OitCard {
            val (status, color) = when {
                outing != null -> tr("外出中 🚪") to o.warn
                t.state == TState.RUNNING -> tr("专注中…") to o.accent
                t.state == TState.ON_BREAK -> tr("休息中 ☕") to o.warn
                else -> tr("准备就绪") to o.dim
            }
            StatusRow(status, color)
            if (outing != null) {
                val left = maxOf(0.0, secondsBetween(LocalDateTime.now(), outing.end))
                BigText(fmtHm(left))
                Dim(tr("外出中 · 已经记录 {h} 小时", "h" to numText(core.behavior.recordedHours(outing))))
            } else when (t.state) {
                TState.RUNNING -> if (fog) BigText("???") else {
                    // 黑雾: 专注时看不到专注时长 (预计休息时长也能推出时长, 一起不显示)
                    val rate = t.focusRate                   // 阻滞: 合约内的那部分时间按 90% 增长
                    val m = core.contract.focusMinutes(t.startWall, t.elapsed)
                    BigText("${f2(m)} min")
                    val rem = computeF(m) * scale * core.contract.restFactor()
                    Dim(if (rem > 0) tr("预计停止时的休息时长:  {m} 分钟", "m" to f2(rem)) else tr("预计休息时长: 0.00 分钟"))
                    if (rate < 1) Dim(tr("阻滞: 合约内这段专注的时间按 {p}% 增长", "p" to Math.round(rate * 100)))
                }
                TState.ON_BREAK -> {
                    val left = t.countdown
                    BigText(fmtMs(left))
                    val s = kotlin.math.ceil(left).toLong()
                    var sub = tr("休息剩余 {m} 分 {s} 秒", "m" to s / 60, "s" to "%02d".format(s % 60))
                    if (t.contractRestOver) sub += "\n" + tr("暂停休息: 立即消耗所有的合约休息时间, 但是不对正常休息打折")
                    else if (core.contract.active && t.breakPre > 0) sub += "\n" + tr("合约前保存的休息时间: {t}", "t" to fmtMs(t.breakPre.toInt().toDouble()))
                    if (t.breakLocked) sub += "\n" + t.pauseBlock
                    Dim(sub)
                }
                TState.IDLE -> {
                    BigText("0.00 min")
                    if (t.breakCarry > 0) Dim(tr("积攒的休息时间: {c}", "c" to fmtMs(t.breakCarry.toDouble())))
                    if (t.autoRest > 0) Dim(tr("立即开始专注:先进行一次休息时间的折算"))
                }
            }
            Row(Modifier.padding(top = 12.dp), horizontalArrangement = Arrangement.spacedBy(24.dp)) {
                Text(if (fog) tr("今日: ???") else tr("今日: {h} h", "h" to f1(core.dm.todayHours())),
                    color = o.accent, fontWeight = FontWeight.Bold)
            }
            // 绑定的其他设备此刻的状态 (MECHANICS 15); 有设备离线时提醒离线的那段专注不会被记录 (8.4)
            for (line in peerLines()) Dim(line, Modifier.padding(top = 4.dp), size = 13)
            if (OitApp.instance.sync?.deviceStates()?.any { !it.online } == true)
                Dim(tr("⚠ 有设备离线: 离线的这段时间专注不会被记录."), Modifier.padding(top = 4.dp), size = 13, color = o.warn)
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (outing != null) {
                PlainButton(tr("🚪  结束外出")) { askEnd = true }
            } else {
                val locked = t.breakLocked
                when (t.state) {
                    TState.RUNNING -> {
                        AccentButton(tr("■  结束专注")) { t.onAction() }
                        PlainButton(tr("☕  开始休息"), enabled = false) {}
                    }
                    TState.ON_BREAK -> {
                        AccentButton(tr("▶  开始专注"), enabled = !locked) { t.onAction() }
                        PlainButton(tr("⏸  暂停休息"), enabled = !locked) { t.onBreakButton() }
                    }
                    TState.IDLE -> {
                        AccentButton(tr("▶  开始专注")) { t.onAction() }
                        PlainButton(tr("☕  开始休息"), enabled = t.breakCarry > 0) { t.onBreakButton() }
                    }
                }
                if (core.behavior.enabled()) PlainButton(tr("🚪  外出")) { askOuting = true }
                // 连点按钮: 按钮上只写这几个字, 不写具体规则; 合约中 / 外出中隐藏
                if (!core.contract.active) PlainButton(t.bonusLabel()) { t.onBonusClick() }
            }
        }
        Flavor(tr("在第二段区间内 学的时间越长得到的休息时间越长.请尽量长时间地专注."), Modifier.padding(top = 24.dp))
    }
    if (askOuting) {
        AlertDialog(
            onDismissRequest = { askOuting = false },
            title = { Text(tr("🚪  外出")) },
            text = {
                Column {
                    Text(tr("持续多少小时?"))
                    OutlinedTextField(outingHours, { outingHours = it.filter(Char::isDigit).take(3) },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        modifier = Modifier.fillMaxWidth().padding(top = 8.dp))
                }
            },
            confirmButton = {
                TextButton({
                    val h = outingHours.toIntOrNull()
                    if (h == null) { core.toast(tr("请填写整数小时."), "warn"); return@TextButton }
                    val err = core.startOuting(h)
                    if (err.isNotEmpty()) { core.toast(err, "warn"); return@TextButton }
                    askOuting = false
                }) { Text(tr("开始外出"), color = o.accent) }
            },
            dismissButton = { TextButton({ askOuting = false }) { Text(tr("取消"), color = o.dim) } },
            containerColor = o.bg,
        )
    }
    if (askEnd) {
        val cur = outing
        if (cur == null) askEnd = false
        else Confirm(tr("结束外出?"),
            tr("已经记录了 {h} 小时. 现在结束外出吗?", "h" to numText(core.behavior.recordedHours(cur))),
            true, { askEnd = false }) {
            askEnd = false
            core.stopOuting()
        }
    }
}

// ════════════════ Network 页 (MECHANICS 2) ════════════════

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun NetScreen() {
    LocalTick.current
    val o = LocalOit.current
    val core = OitApp.instance.core
    val n = core.net
    val qm = core.settings.num("QUICK_MULTIPLIER")
    var minutes by rememberSaveable { mutableStateOf("15") }
    var reason by rememberSaveable { mutableStateOf("") }
    var ask by rememberSaveable { mutableStateOf("") }      // quick / loan / upgrade
    // 状态后面写上当前的断网方式 (代理-全屏蔽 (Proxy block-all) / 代理 - 规则过滤)
    val mode = "  ·  " + core.settings.display("NET_MODE", core.netMode())
    PageColumn {
        FileChannelButton()                       // 文件通道 (MECHANICS 15.6)
        OitCard {
            // 开发者模式在界面上不显示: 照常显示成离线
            when (n.mode) {
                "pending" -> {
                    val p = n.pending!!
                    StatusRow(tr("冷静期中 —— 仍可取消") + mode, o.warn)
                    BigText(fmtMs((p.at - System.currentTimeMillis()) / 1000.0))
                    val at = LocalDateTime.ofInstant(Instant.ofEpochMilli(p.at), ZoneId.systemDefault()).format(Fmt.HM)
                    Dim(tr("已申请 {m} 分钟 · {at} 联网", "m" to p.minutes, "at" to at))
                }
                "online" -> {
                    StatusRow(tr("联网中") + mode, o.success)
                    BigText(fmtMs(n.grantLeft))
                    if (n.grantQuick) Dim(tr("⚡ 快速访问 ×{a}", "a" to numText(n.grantRate)))
                }
                else -> {
                    StatusRow(tr("离线") + mode, o.dim)
                    BigText(tr("离线"))
                    if (n.remaining < 60) Dim(tr("今天的额度已用完."))
                }
            }
            val total = maxOf(1.0, n.todayTotal)
            LinearProgressIndicator(progress = { (n.quotaUsed / total).toFloat().coerceIn(0f, 1f) },
                color = o.accent, trackColor = o.border, strokeCap = StrokeCap.Round,
                modifier = Modifier.fillMaxWidth().padding(top = 14.dp, bottom = 6.dp))
            Text(tr("今日已用: {used} / {total}     今日剩余: {left}     可快速访问: {quick}",
                "used" to fmtHm(n.quotaUsed), "total" to fmtHm(n.todayTotal),
                "left" to fmtHm(n.remaining), "quick" to fmtHm(n.quickLeft)),
                color = o.accent, fontWeight = FontWeight.Bold, fontSize = 13.sp)
            val bits = mutableListOf<String>()
            val mult = n.quotaMultiplier
            if (kotlin.math.abs(mult - 1.0) > 1e-9) bits.add(tr("今天的联网倍率 ×{m}: 每日额度 {base} → 今天 {quota}",
                "m" to numText(mult), "base" to fmtHm(core.settings.num("DAILY_LIMIT")), "quota" to fmtHm(n.limit)))
            if (kotlin.math.abs(n.outingFactor - 1.0) > 1e-9) bits.add(tr("外出: 今天只能用总额度的 {p}% (总额度 {full} → 能用 {total})",
                "p" to numText(n.outingFactor * 100), "full" to fmtHm(n.fullTotal), "total" to fmtHm(n.todayTotal)))
            if (n.outingCooldown < 1.0) bits.add(tr("外出中: 联网冷静期 ×{f}", "f" to numText(n.outingCooldown)))
            if (n.bonusAll > 0) bits.add(tr("今天已借 {bonus},明天将扣 {cost}(明天剩余 {left})",
                "bonus" to fmtHm(n.bonusAll), "cost" to fmtHm(n.tomorrowUsedAll), "left" to fmtHm(n.tomorrowRemaining)))
            if (n.debtAll > 0) bits.add(tr("今天要先还昨天借的 {debt}", "debt" to fmtHm(n.debtAll)))
            if (n.giftAll > 0) bits.add(tr("今天合约奖励的联网额度 {t}", "t" to fmtHm(n.giftAll)))
            if (bits.isNotEmpty()) Dim(bits.joinToString("\n"), size = 13, color = o.warn)
        }
        when (n.mode) {
            "offline" -> {
                OutlinedTextField(minutes, { minutes = it.filter(Char::isDigit).take(4) }, label = { Text(tr("分钟")) },
                    singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.fillMaxWidth())
                OutlinedTextField(reason, { reason = it }, label = { Text(tr("理由")) }, singleLine = true,
                    modifier = Modifier.fillMaxWidth())
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    AccentButton(tr("📨  提交申请(需冷静期)"), enabled = n.remaining >= 60) {
                        if (core.netRequest(minutes, reason)) reason = ""
                    }
                    DangerButton(tr("⚡  快速访问(×{a})", "a" to numText(qm)), enabled = n.quickLeft >= 60) {
                        if (core.netPrecheck(minutes, reason, loan = false) != null) ask = "quick"
                    }
                    DangerButton(tr("💳  紧急借贷"), enabled = n.loanLeft >= 60) {
                        if (core.netPrecheck(minutes, reason, loan = true) != null) ask = "loan"
                    }
                }
            }
            "pending", "online" -> FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AccentButton(if (n.mode == "pending") tr("✕  取消申请") else tr("■  结束本次会话")) { core.netCtrl() }
                if (n.mode == "pending" && n.pending?.quick == false)
                    DangerButton(tr("⚡  改为快速访问(×{a})", "a" to numText(qm))) {
                        val err = n.canQuick(n.pending!!.minutes.toDouble())
                        if (err != null) core.toast(err, "warn") else ask = "upgrade"
                    }
            }
        }
    }
    when (ask) {
        "quick" -> Confirm(tr("快速访问"), tr("快速访问跳过冷静期立刻联网,额度按 ×{a} 消耗.", "a" to numText(qm)), true,
            { ask = "" }) { if (core.netQuick(minutes.toInt(), reason)) reason = "" }
        "loan" -> Confirm(tr("紧急借贷"), tr("紧急借贷不会联网,只会给今天加额度;明天扣 {b} 倍.", "b" to numText(core.settings.num("LOAN_RATE"))),
            true, { ask = "" }) { if (core.netLoan(minutes.toInt(), reason)) reason = "" }
        "upgrade" -> Confirm(tr("快速访问"), tr("立刻结束冷静期并联网?额度按 ×{a} 消耗.", "a" to numText(qm)), true,
            { ask = "" }) { core.netUpgrade() }
    }
}
