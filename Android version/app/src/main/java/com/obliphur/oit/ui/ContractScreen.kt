package com.obliphur.oit.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.obliphur.oit.OitApp
import com.obliphur.oit.core.Contract
import com.obliphur.oit.core.Fmt
import com.obliphur.oit.core.contractResult
import com.obliphur.oit.core.fmtHm
import com.obliphur.oit.core.fmtMs
import com.obliphur.oit.core.numText
import com.obliphur.oit.core.parseIso
import com.obliphur.oit.core.secondsBetween
import com.obliphur.oit.core.tr
import java.time.LocalDateTime

// ════════════════ 合约页 (MECHANICS 18) ════════════════

private fun marks(level: Int) = "▲".repeat(maxOf(0, level))
private fun termName(id: String) = tr(Contract.BY_ID[id]!!.name)
private fun termDesc(id: String) = tr(Contract.BY_ID[id]!!.desc)

/** 格子里的名字: 去掉"环境:" / "目标:", 名字和罗马数字分开 (英文用更短的名字). */
private fun termShort(id: String): String {
    val base = Contract.BY_ID[id]!!.name.substringAfter(':')
    return (tr(base.substringBefore('-')) + " " + base.substringAfter('-', "")).trim()
}

private fun hms(sec: Double): String {
    val s = maxOf(0L, sec.toLong())
    return "%d:%02d:%02d".format(s / 3600, (s % 3600) / 60, s % 60)
}

/** 词条按等级 (行) 和类型 (列) 排成格子, 同一类型在同一列; 下面左边是已选中的词条, 右边是危机等级, 时长, 休息倍率.
 *  合约进行中时只能看. 这一页总是横屏 (Root 里设置). */
@Composable
fun ContractScreen() {
    LocalTick.current
    val o = LocalOit.current
    val core = OitApp.instance.core
    val c = core.contract
    val active = c.active
    val now = LocalDateTime.now()
    var tapped by rememberSaveable { mutableStateOf("") }
    var ask by rememberSaveable { mutableStateOf("") }        // start / end
    val sel = if (active) c.terms else c.draft
    PageColumn {
        if (active) OitCard(border = o.danger) {
            val end = c.endAt ?: now
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(tr("合约进行中"), color = o.danger, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                Text(tr("违反 {n} / {m}", "n" to c.violations, "m" to c.violationLimit), color = o.danger,
                    fontWeight = FontWeight.Bold, fontSize = 16.sp)
            }
            BigText(hms(secondsBetween(now, end)))
            var line = "${c.startAt?.format(Fmt.HM)} — ${end.format(Fmt.MDHM)}"
            if (c.reward >= 1) line += "\n" + tr("完成后休息 +{t}", "t" to fmtMs(c.reward))
            c.netLeft(core.net.usedAll)?.let { line += "\n" + tr("合约的联网额度还剩 {t}", "t" to fmtHm(it)) }
            Dim(line, size = 13)
            c.idleLeft(now)?.let { Dim(tr("空闲中: {t} 后合约终止", "t" to fmtMs(it)), color = o.warn) }
        }

        // 格子: 行 = 等级, 列 = 类型 (同一类型在同一列), 同电脑版; 合约页总是横屏
        BoxWithConstraints(Modifier.fillMaxWidth()) {
            val labelW = 30.dp
            val gap = 4.dp
            val n = Contract.GROUPS.size
            val tileW = minOf(84.dp, (maxWidth - labelW - gap * n) / n)
            Column(verticalArrangement = Arrangement.spacedBy(gap)) {
                for (lv in 1..Contract.MAX_LEVEL) Row(horizontalArrangement = Arrangement.spacedBy(gap),
                    verticalAlignment = Alignment.CenterVertically) {
                    Text("$lv▲", color = o.text, fontWeight = FontWeight.Bold, fontSize = 16.sp, modifier = Modifier.width(labelW - gap))
                    for (group in Contract.GROUPS) {
                        val t = Contract.TERMS.firstOrNull { it.group == group && it.level == lv }
                        if (t == null) {
                            Spacer(Modifier.width(tileW).height(48.dp)); continue
                        }
                        val on = t.id in sel
                        val blocked = !on && (active || Contract.blockedBy(t.id, sel) != null)
                        val bg = if (on) o.danger else if (blocked) o.bg else o.card
                        val fg = if (on) o.onAccent else if (blocked) o.border else o.text
                        Box(Modifier.width(tileW).height(48.dp)
                            .background(bg, RoundedCornerShape(6.dp))
                            .border(1.dp, if (on) o.danger else o.border, RoundedCornerShape(6.dp))
                            .clickable {
                                tapped = t.id
                                if (active) return@clickable
                                val cur = c.draft.toMutableList()
                                if (t.id in cur) cur.remove(t.id)
                                else if (Contract.blockedBy(t.id, cur) == null) cur.add(t.id)
                                c.setDraft(cur)
                                core.changed()
                            }, contentAlignment = Alignment.Center) {
                            Text(marks(t.level) + "\n" + termShort(t.id), color = fg, fontSize = 11.sp, lineHeight = 13.sp,
                                fontWeight = FontWeight.Bold, textAlign = TextAlign.Center)
                        }
                    }
                }
            }
        }
        if (tapped.isNotEmpty()) {
            var text = "${marks(Contract.BY_ID[tapped]!!.level)}  ${termName(tapped)} : ${termDesc(tapped)}"
            val other = Contract.blockedBy(tapped, sel)
            if (other != null && tapped !in sel) text += "  " + tr("[与 {name} 冲突]", "name" to termName(other))
            Dim(text, size = 13)
        }

        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            OitCard(Modifier.weight(1f)) {
                Text(tr("已选中词条"), color = o.text, fontWeight = FontWeight.Bold)
                val rows = if (!active) sel.map { Triple(Contract.BY_ID[it]!!.level, "${termName(it)} : ${termDesc(it)}", false) }
                else c.orig.map { t ->
                    val g = Contract.BY_ID[t]!!.group
                    val cur = c.terms.firstOrNull { it == t || Contract.BY_ID[it]!!.group == g }
                    when {
                        cur == null -> Triple(Contract.BY_ID[t]!!.level, termName(t) + "   " + tr("[已失效]"), true)
                        cur != t -> Triple(c.termLevel(cur), "${termName(cur)} : ${termDesc(cur)}   " +
                            tr("[原 {name}]", "name" to termShort(t)), false)
                        c.termLevel(t) < Contract.BY_ID[t]!!.level -> Triple(c.termLevel(t), "${termName(t)} : ${termDesc(t)}   " +
                            tr("[降了 {n} 级]", "n" to Contract.BY_ID[t]!!.level - c.termLevel(t)), false)
                        else -> Triple(c.termLevel(t), "${termName(t)} : ${termDesc(t)}", false)
                    }
                }
                if (rows.isEmpty()) Dim("—")
                for ((lv, text, void) in rows) Row(Modifier.padding(top = 4.dp)) {
                    Text(marks(lv), color = if (void) o.dim else o.danger, fontSize = 12.sp, fontWeight = FontWeight.Bold,
                        modifier = Modifier.width(44.dp))
                    Text(text, color = if (void) o.dim else o.text, fontSize = 13.sp)
                }
            }
            OitCard(Modifier.width(240.dp)) {
                val level = if (active) c.level else Contract.levelOf(sel)
                val orig = if (active) c.origLevel else level
                Text(tr("危机等级"), color = o.dim, fontWeight = FontWeight.Bold)
                Text("$level", color = o.danger, fontSize = 40.sp, fontWeight = FontWeight.Bold)
                var info = ""
                if (orig != level) info += tr("开始时 {n}", "n" to orig) + "\n"
                info += tr("时长 {t}", "t" to fmtHm(Contract.durationOf(if (active) c.orig else sel))) + "\n" +
                    tr("休息倍率 ×{m}", "m" to numText(Contract.restMult(level)))
                Text(info, color = o.text, fontSize = 14.sp)
                if (level >= Contract.HARD_LEVEL)
                    Dim(tr("⚠  当前合约难度极大 请谨慎行动"), Modifier.padding(top = 6.dp), color = o.warn)
                Row(Modifier.padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (active) DangerButton(tr("■  提前结束")) { ask = "end" }
                    else {
                        PlainButton(tr("清空")) { c.setDraft(emptyList()); core.changed() }
                        DangerButton(tr("▶  开始行动")) { ask = "start" }
                    }
                }
            }
        }

        if (!active) c.last?.let { last ->
            val a = parseIso(last.optString("start"))
            val b = parseIso(last.optString("end"))
            var text = tr("上次合约: {r} · 危机等级 {lv} · {when}", "r" to tr(contractResult(last.optString("result"))),
                "lv" to last.optInt("level"), "when" to if (a != null && b != null) "${a.format(Fmt.MDHM)} — ${b.format(Fmt.HM)}" else "")
            if (last.optInt("reward") > 0) text += "   ·   " + tr("休息 +{t}", "t" to fmtMs(last.optInt("reward").toDouble()))
            Dim(text, size = 13)
        }
    }
    when (ask) {
        "start" -> {
            val lv = Contract.levelOf(c.draft)
            val dur = Contract.durationOf(c.draft)
            Confirm(tr("▶  开始行动"), tr("危机等级 {lv} · 时长 {t} · 到 {at}", "lv" to lv, "t" to fmtHm(dur),
                "at" to now.plusSeconds(dur.toLong()).format(Fmt.HM)) + "\n" +
                tr("休息倍率 ×{m}", "m" to numText(Contract.restMult(lv))), true, { ask = "" }) {
                core.startContract(c.draft)
            }
        }
        "end" -> {
            var msg = tr("提前结束合约?")
            if (c.reward >= 1) msg += "\n" + tr("积攒的 {t} 休息时间不会给出.", "t" to fmtMs(c.reward))
            Confirm(tr("■  提前结束"), msg, true, { ask = "" }) { core.abandonContract() }
        }
    }
}
