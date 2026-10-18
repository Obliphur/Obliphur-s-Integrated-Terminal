package com.obliphur.oit.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.GenericShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.layout.SubcomposeLayout
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.obliphur.oit.OitApp
import com.obliphur.oit.core.Contract
import com.obliphur.oit.core.Fmt
import com.obliphur.oit.core.contractResult
import com.obliphur.oit.core.fmtHm
import com.obliphur.oit.core.fmtMs
import com.obliphur.oit.core.parseIso
import com.obliphur.oit.core.secondsBetween
import com.obliphur.oit.core.tr
import java.time.LocalDateTime

// ════════════════ 合约页 (MECHANICS 18) ════════════════

private val BOARD_BAND = 64.dp
private val BOARD_TILE = 64.dp
private val BOARD_GAP = 5.dp

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

/** 合约页的颜色 (设置 → 合约 → 合约页主题, 同电脑版 pages/contract_page.py). side = null: 顶栏 / 侧边栏不变. */
class ContractColors(
    val bg: Color, val text: Color, val dim: Color, val line: Color,
    val board: Color, val band: Color, val bandText: Color, val bandDim: Color,
    val tile: Color, val tileText: Color, val tileRim: Color, val tileRim4: Color,
    val tileOn: Color, val tileOnText: Color, val tileOnRim: Color,
    val tileOff: Color, val tileOffText: Color, val tileOffRim: Color,
    val sel: Color, val selText: Color, val selDim: Color,
    val badgeHi: Color, val badgeHiText: Color, val badgeLo: Color, val badgeLoText: Color,
    val bar: Color, val level: Color, val warn: Color, val start: Color, val startText: Color,
    val startOff: Color, val startOffText: Color, val rules: Color, val rulesRim: Color,
    val clear: Color, val clearText: Color, val tag: Color, val tagText: Color,
    val live: Color, val liveRim: Color, val danger: Color, val idle: Color,
    val side: Color?, val sideText: Color, val sideOn: Color,
)

private fun c(hex: Long) = Color(0xFF000000 or hex)

/** "起源": 深色页面, 浅灰的词条板, 六边形格子 (选中的是蓝色), 黑色的已选中词条, 红色的开始按钮. */
val OPS_COLORS = ContractColors(
    bg = c(0x1f2023), text = c(0xf2f2f2), dim = c(0x9a9ca1), line = c(0xc8202f),
    board = c(0xd6d7da), band = c(0x2c2d31), bandText = c(0xececec), bandDim = c(0x9a9ca1),
    tile = c(0x45474d), tileText = c(0xf4f4f4), tileRim = c(0x6b6d73), tileRim4 = c(0xff2b3a),
    tileOn = c(0x1d6fc4), tileOnText = c(0xffffff), tileOnRim = c(0x9cc8ff),
    tileOff = c(0xbdbec1), tileOffText = c(0x8e9095), tileOffRim = c(0xa9aaad),
    sel = c(0x141416), selText = c(0xf2f2f2), selDim = c(0x77797e),
    badgeHi = c(0xc8202f), badgeHiText = c(0xffffff), badgeLo = c(0xf2f2f2), badgeLoText = c(0x141416),
    bar = c(0x26272b), level = c(0xffffff), warn = c(0xffffff), start = c(0x9e1b28), startText = c(0xffffff),
    startOff = c(0x4a4b51), startOffText = c(0x8e9095), rules = c(0x18181b), rulesRim = c(0x3a3b40),
    clear = c(0x3a3b40), clearText = c(0xececec), tag = c(0xc8202f), tagText = c(0xffffff),
    live = c(0x18181b), liveRim = c(0xc8202f), danger = c(0xff4d5a), idle = c(0xf2b33d),
    side = c(0x18191c), sideText = c(0xcfd0d3), sideOn = c(0xc8202f),
)

@Composable
fun contractColors(): ContractColors {
    val core = OitApp.instance.core
    if (core.settings.str("CONTRACT_THEME") != "ui") return OPS_COLORS
    val o = LocalOit.current
    return ContractColors(
        bg = o.bg, text = o.text, dim = o.dim, line = o.border,
        board = o.bg, band = o.card, bandText = o.text, bandDim = o.dim,
        tile = o.card, tileText = o.text, tileRim = o.border, tileRim4 = o.danger,
        tileOn = o.danger, tileOnText = o.onAccent, tileOnRim = o.danger,
        tileOff = o.bg, tileOffText = o.border, tileOffRim = o.border,
        sel = o.card, selText = o.text, selDim = o.dim,
        badgeHi = o.danger, badgeHiText = o.onAccent, badgeLo = o.border, badgeLoText = o.text,
        bar = o.card, level = o.danger, warn = o.warn, start = o.danger, startText = o.onAccent,
        startOff = o.border, startOffText = o.dim, rules = o.card, rulesRim = o.border,
        clear = o.border, clearText = o.text, tag = o.danger, tagText = o.onAccent,
        live = o.card, liveRim = o.danger, danger = o.danger, idle = o.warn,
        side = null, sideText = o.text, sideOn = o.accent,
    )
}

/** 六边形 (左右两个尖). */
/** 放进格子里, 居中的正六边形 (平顶). 格子本身也按正六边形的比例 (高 = 宽 × √3 / 2). */
private val HEX = GenericShape { size, _ ->
    val r = minOf(size.width / 2, size.height / SQRT3)
    val cx = size.width / 2
    val cy = size.height / 2
    val hh = r * SQRT3 / 2
    moveTo(cx - r / 2, cy - hh); lineTo(cx + r / 2, cy - hh); lineTo(cx + r, cy)
    lineTo(cx + r / 2, cy + hh); lineTo(cx - r / 2, cy + hh); lineTo(cx - r, cy); close()
}
private val SQRT3 = kotlin.math.sqrt(3f)

/** 整页缩小到一屏放得下 (手机横屏的高度不够, 词条板也可能太宽): 先按可用宽度 (至少 minWidth) 量一次;
 *  太高时按比例放宽再量一次 (放宽后文字换行少, 缩小得也少), 再整体缩小. 左右空出来的地方是页面的底色. */
@Composable
private fun FitToScreen(minWidth: androidx.compose.ui.unit.Dp, content: @Composable () -> Unit) {
    SubcomposeLayout(Modifier.fillMaxSize()) { cs ->
        val w = cs.maxWidth
        val h = if (cs.hasBoundedHeight) cs.maxHeight else Constraints.Infinity
        val w1 = maxOf(w, minWidth.roundToPx())
        val first = subcompose(1, content).map { it.measure(Constraints(minWidth = w1, maxWidth = w1)) }
        val h1 = maxOf(1, first.maxOfOrNull { it.height } ?: 0)
        if (w1 <= w && h1 <= h) return@SubcomposeLayout layout(w, h1) { first.forEach { it.placeRelative(0, 0) } }
        // 高度更不够: 放宽到缩小后正好填满宽度, 再量一次
        val tall = h1.toLong() * w > h.toLong() * w1
        val pl = if (!tall) first
            else subcompose(2, content).map { val wide = (w.toLong() * h1 / h).toInt()
                it.measure(Constraints(minWidth = wide, maxWidth = wide)) }
        val w2 = maxOf(1, pl.maxOf { it.width })
        val h2 = maxOf(1, pl.maxOf { it.height })
        val sc = minOf(1f, w.toFloat() / w2, if (h == Constraints.Infinity) 1f else h.toFloat() / h2)
        val x = ((w - w2 * sc) / 2).toInt()
        layout(w, if (h == Constraints.Infinity) (h2 * sc).toInt() else h) {
            pl.forEach { it.placeRelativeWithLayer(x, 0) { scaleX = sc; scaleY = sc; transformOrigin = TransformOrigin(0f, 0f) } }
        }
    }
}

/** 词条板 (行 = 等级, 列 = 类型, 同一类型在同一列), 下面是已选中的词条 (两栏), 最下面一条: 清空 | 危机等级 | 开始行动.
 *  合约进行中时只能看. 这一页总是横屏 (Root 里设置), 整页缩小到一屏显示全部 (FitToScreen). 颜色按"合约页主题". */
@Composable
fun ContractScreen() {
    LocalTick.current
    val p = contractColors()
    val core = OitApp.instance.core
    val c = core.contract
    val active = c.active
    val now = LocalDateTime.now()
    var tapped by rememberSaveable { mutableStateOf("") }
    var ask by rememberSaveable { mutableStateOf("") }        // start / end
    val sel = if (active) c.terms else c.draft
    // 词条板最窄要放下每列 64dp 的格子, 再窄就整页缩小
    val minW = BOARD_BAND + (BOARD_TILE + BOARD_GAP) * Contract.BOARD_COLS + BOARD_GAP + 32.dp
    FitToScreen(minW) { Column(Modifier.fillMaxWidth().background(p.bg).padding(horizontal = 16.dp, vertical = 6.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(tr("合约详情"), color = p.tagText, fontSize = 12.sp, fontWeight = FontWeight.Bold,
                modifier = Modifier.background(p.tag).padding(horizontal = 8.dp, vertical = 2.dp))
            Text("CONTRACT", color = p.dim, fontWeight = FontWeight.Bold, fontSize = 14.sp,
                modifier = Modifier.padding(start = 10.dp).weight(1f))
            if (active) Text(tr("合约进行中"), color = p.danger, fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(end = 12.dp))
            // 右上角: 合约期间的特别规则 (两栏)
            val n = if (active) c.violationLimit else Contract.lifeOf(c.draft)
            Row(Modifier.background(p.rules).border(1.dp, p.rulesRim).padding(horizontal = 8.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(tr("合约期间"), color = p.danger, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                Text(listOf(tr("· 目标生命值 {n}: 违反一次 -1, 归零即失败", "n" to n), tr("· 空闲 30 分钟 / 设备离线 15 分钟: 失败"),
                    tr("· 整点记录超时未填写: 违反 1 次")).joinToString("\n"), color = p.dim, fontSize = 10.sp, lineHeight = 13.sp)
                Text(listOf(tr("· \"我累了\"不可用"), tr("· 暂停休息: 合约前积攒的时间不打折"),
                    tr("· 合约中得到的休息时间结束后作废 (奖励照常)")).joinToString("\n"),
                    color = p.dim, fontSize = 10.sp, lineHeight = 13.sp)
            }
        }
        Box(Modifier.fillMaxWidth().height(2.dp).background(p.line))

        if (active) Column(Modifier.fillMaxWidth().background(p.live).border(2.dp, p.liveRim).padding(12.dp)) {
            val end = c.endAt ?: now
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(hms(secondsBetween(now, end)), color = p.text, fontSize = 34.sp, fontWeight = FontWeight.Bold,
                    modifier = Modifier.weight(1f))
                // 目标生命值: 每违反一次减一, 减到 0 合约失败
                Text(tr("目标生命值") + "  " + "♥".repeat(c.life) + "♡".repeat(c.violationLimit - c.life) +
                    "  ${c.life} / ${c.violationLimit}", color = p.danger, fontWeight = FontWeight.Bold, fontSize = 18.sp)
            }
            var line = "${c.startAt?.format(Fmt.HM)} — ${end.format(Fmt.MDHM)}"
            c.netLeft(core.net.usedAll)?.let { line += "   ·   " + tr("合约的联网额度还剩 {t}", "t" to fmtHm(it)) }
            Text(line, color = p.dim, fontSize = 13.sp)
            c.idleLeft(now)?.let {
                Text(tr("空闲中: {t} 后合约终止", "t" to fmtMs(it)), color = p.idle, fontWeight = FontWeight.Bold, fontSize = 13.sp)
            }
        }

        // 词条板: 左边一条写着等级, 右边六边形的格子
        BoxWithConstraints(Modifier.fillMaxWidth().background(p.board)) {
            val bandW = BOARD_BAND
            val gap = BOARD_GAP
            val n = Contract.BOARD_COLS
            val tileW = maxOf(BOARD_TILE, minOf(84.dp, (maxWidth - bandW - gap * (n + 1)) / n))
            val tileH = tileW * (SQRT3 / 2)
            Row {
                Column(Modifier.width(bandW).background(p.band).padding(vertical = gap),
                    verticalArrangement = Arrangement.spacedBy(gap)) {
                    for (lv in 1..Contract.MAX_LEVEL) Row(Modifier.height(tileH).padding(horizontal = 6.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("Level", color = p.bandDim, fontSize = 8.sp)
                            Text("▲", color = p.badgeHi, fontSize = 9.sp,
                                modifier = Modifier.background(p.badgeLo).padding(horizontal = 4.dp))
                        }
                        Text("$lv", color = p.bandText, fontSize = 24.sp, fontWeight = FontWeight.Bold)
                    }
                }
                Column(Modifier.padding(gap), verticalArrangement = Arrangement.spacedBy(gap)) {
                    for (lv in 1..Contract.MAX_LEVEL) Row(horizontalArrangement = Arrangement.spacedBy(gap)) {
                        // 成系列的每组一列, 零散的在右边挨着排 (Contract.LAYOUT)
                        for (col in 0 until Contract.BOARD_COLS) {
                            val t = Contract.TERMS.firstOrNull { it.level == lv && Contract.LAYOUT[it.id] == col }
                            if (t == null) {
                                Spacer(Modifier.width(tileW).height(tileH)); continue
                            }
                            val on = t.id in sel
                            val off = !on && (active || Contract.blockedBy(t.id, sel) != null)
                            val fill = if (on) p.tileOn else if (off) p.tileOff else p.tile
                            val fg = if (on) p.tileOnText else if (off) p.tileOffText else p.tileText
                            val rim = if (t.level >= Contract.RIM_LEVEL) p.tileRim4
                                else if (on) p.tileOnRim else if (off) p.tileOffRim else p.tileRim
                            Box(Modifier.width(tileW).height(tileH).background(fill, HEX)
                                .border(if (t.level >= Contract.RIM_LEVEL) 3.dp else 2.dp, rim, HEX)
                                .clickable {
                                    tapped = t.id
                                    if (active) return@clickable
                                    val cur = c.draft.toMutableList()
                                    if (t.id in cur) cur.remove(t.id)
                                    else if (Contract.blockedBy(t.id, cur) == null) cur.add(t.id)
                                    c.setDraft(cur)
                                    core.changed()
                                }, contentAlignment = Alignment.Center) {
                                Text(marks(t.level) + "\n" + termShort(t.id), color = fg, fontSize = 10.sp, lineHeight = 12.sp,
                                    fontWeight = FontWeight.Bold, textAlign = TextAlign.Center,
                                    modifier = Modifier.padding(horizontal = 8.dp))
                            }
                        }
                    }
                }
            }
        }
        if (tapped.isNotEmpty()) {
            var text = "${marks(Contract.BY_ID[tapped]!!.level)}  ${termName(tapped)} : ${termDesc(tapped)}"
            val other = Contract.blockedBy(tapped, sel)
            if (other != null && tapped !in sel) text += "  " + tr("[与 {name} 冲突]", "name" to termName(other))
            Text(text, color = p.dim, fontSize = 13.sp)
        }

        // 已选中的词条: 黑底, 两栏
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
        Column(Modifier.fillMaxWidth().background(p.sel).padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("CONTRACT SELECTED", color = p.selDim, fontSize = 11.sp, fontWeight = FontWeight.Bold)
                Text(tr("已选中词条"), color = p.selText, fontSize = 13.sp, fontWeight = FontWeight.Bold,
                    modifier = Modifier.padding(start = 8.dp))
            }
            if (rows.isEmpty()) Text("—", color = p.selDim, modifier = Modifier.padding(top = 6.dp))
            val half = (rows.size + 1) / 2
            Row(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                for (part in listOf(rows.take(half), rows.drop(half))) Column(Modifier.weight(1f)) {
                    for ((lv, text, void) in part) Row(Modifier.padding(vertical = 3.dp)) {
                        val hi = lv >= 2 && !void
                        Text(marks(lv).ifEmpty { "—" }, color = if (hi) p.badgeHiText else p.badgeLoText, fontSize = 11.sp,
                            fontWeight = FontWeight.Bold, textAlign = TextAlign.Center,
                            modifier = Modifier.widthIn(min = 36.dp).background(if (hi) p.badgeHi else p.badgeLo)
                                .padding(horizontal = 4.dp, vertical = 1.dp))
                        Text(text, color = if (void) p.selDim else p.selText, fontSize = 12.sp,
                            modifier = Modifier.padding(start = 8.dp))
                    }
                }
            }
        }

        if (!active) c.last?.let { last ->
            val a = parseIso(last.optString("start"))
            val b = parseIso(last.optString("end"))
            var text = tr("上次合约: {r} · 危机等级 {lv} · {when}", "r" to tr(contractResult(last.optString("result"))),
                "lv" to last.optInt("level"), "when" to if (a != null && b != null) "${a.format(Fmt.MDHM)} — ${b.format(Fmt.HM)}" else "")
            if (last.optInt("reward") > 0) text += "   ·   " + tr("休息 +{n} 分钟", "n" to Math.round(last.optInt("reward") / 60.0))
            Text(text, color = p.dim, fontSize = 12.sp)
        }

        // 最下面一条: 清空 | 危机等级 | 时长 / 完成后的休息时间 | 难度提示 | 开始行动
        val level = if (active) c.level else Contract.levelOf(sel)
        val orig = if (active) c.origLevel else level
        Row(Modifier.fillMaxWidth().background(p.bar).padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically) {
            if (!active) Text(tr("🗑  清空"), color = p.clearText, fontWeight = FontWeight.Bold,
                modifier = Modifier.background(p.clear, RoundedCornerShape(2.dp))
                    .clickable { c.setDraft(emptyList()); core.changed() }.padding(horizontal = 14.dp, vertical = 8.dp))
            Column(Modifier.padding(start = 16.dp)) {
                Text(tr("危机等级"), color = p.dim, fontSize = 11.sp, fontWeight = FontWeight.Bold)
                Text("$level", color = p.level, fontSize = 32.sp, fontWeight = FontWeight.Bold)
            }
            var info = ""
            if (orig != level) info += tr("开始时 {n}", "n" to orig) + "\n"
            info += tr("时长 {t}", "t" to fmtHm(Contract.durationOf(if (active) c.orig else sel))) + "\n" +
                tr("完成后休息 +{n} 分钟", "n" to Contract.restMinutes(level))
            Text(info, color = p.text, fontSize = 12.sp, modifier = Modifier.padding(start = 14.dp).weight(1f))
            if (level >= Contract.HARD_LEVEL)
                Text(tr("⚠  当前合约难度极大 请谨慎行动"), color = p.warn, fontSize = 12.sp, fontWeight = FontWeight.Bold,
                    modifier = Modifier.widthIn(max = 170.dp).padding(end = 12.dp))
            val why = if (active) "" else core.contractBlock()
            Column(horizontalAlignment = Alignment.End) {
                if (why.isNotEmpty()) Text(why, color = p.dim, fontSize = 10.sp, modifier = Modifier.padding(bottom = 2.dp))
                Text(if (active) tr("■  提前结束") else tr("开始行动"), color = if (why.isEmpty()) p.startText else p.startOffText,
                    fontSize = 18.sp, fontWeight = FontWeight.Bold,
                    modifier = Modifier.background(if (why.isEmpty()) p.start else p.startOff, RoundedCornerShape(2.dp))
                        .clickable(enabled = why.isEmpty()) { ask = if (active) "end" else "start" }
                        .padding(horizontal = 26.dp, vertical = 12.dp))
            }
        }
    } }
    when (ask) {
        "start" -> {
            val lv = Contract.levelOf(c.draft)
            val dur = Contract.durationOf(c.draft)
            Confirm(tr("▶  开始行动"), tr("危机等级 {lv} · 时长 {t} · 到 {at}", "lv" to lv, "t" to fmtHm(dur),
                "at" to now.plusSeconds(dur.toLong()).format(Fmt.HM)) + "\n" +
                tr("完成后休息 +{n} 分钟", "n" to Contract.restMinutes(lv)), true, { ask = "" }) {
                core.startContract(c.draft)
            }
        }
        "end" -> {
            var msg = tr("提前结束合约?")
            if (c.reward >= 1) msg += "\n" + tr("完成后的 {n} 分钟休息时间不会给出.", "n" to Math.round(c.reward / 60))
            Confirm(tr("■  提前结束"), msg, true, { ask = "" }) { core.abandonContract() }
        }
    }
}
