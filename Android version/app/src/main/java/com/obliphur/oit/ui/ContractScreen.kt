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
import com.obliphur.oit.core.Combo
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

internal fun marks(level: Int) = "▲".repeat(maxOf(0, level))
internal fun termName(id: String) = tr(Contract.BY_ID[id]!!.name)
private fun termDesc(id: String) = tr(Contract.BY_ID[id]!!.desc)
/** 名字 : 描述. 描述本身分了几行时, 从下一行开始写, 每一句一行. */
internal fun termLine(id: String): String {
    val d = termDesc(id)
    return termName(id) + " :" + (if ('\n' in d) "\n" else " ") + d
}

/** 格子里的名字: 去掉"环境:" / "目标:", 名字和罗马数字分开 (英文用更短的名字). */
internal fun termShort(id: String): String {
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
    val tile: Color, val tileText: Color, val tileRim: Color,
    val tileOn: Color, val tileOnText: Color, val tileOnRim: Color,
    val tileOff: Color, val tileOffText: Color, val tileOffRim: Color,
    val tileRand: Color, val tileRandText: Color,
    // 合约中禁止联网的词条 (乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒): 紫色, 点按解除网络禁令 (MECHANICS 18.3)
    val tileBan: Color, val tileBanText: Color, val tileBanRim: Color,
    val sel: Color, val selText: Color, val selDim: Color,
    val badgeHi: Color, val badgeHiText: Color, val badgeLo: Color, val badgeLoText: Color,
    val bar: Color, val level: Color, val warn: Color, val start: Color, val startText: Color,
    val startOff: Color, val startOffText: Color, val rules: Color, val rulesRim: Color,
    val clear: Color, val clearText: Color, val tag: Color, val tagText: Color,
    val live: Color, val liveRim: Color, val danger: Color, val idle: Color,
    val side: Color?, val sideText: Color, val sideOn: Color,
    // 词条组合的颜色 (打年糕 黄色 / 互联网娱乐的败退 紫色 / 无懈可击 深蓝色 / 信息隔绝 红色)
    val comboYellow: Color, val comboPurple: Color, val comboBlue: Color, val comboRed: Color,
    // 页眉的标签 / 右边的小字 (起源没有小字)
    val tagName: String = "合约详情", val brand: String = "",
)

private fun c(hex: Long) = Color(0xFF000000 or hex)

/** "起源": 深色页面, 浅灰的词条板, 六边形格子 (选中的是蓝色), 黑色的已选中词条, 红色的开始按钮. */
val OPS_COLORS = ContractColors(
    bg = c(0x1f2023), text = c(0xf2f2f2), dim = c(0x9a9ca1), line = c(0xc8202f),
    board = c(0xd6d7da), band = c(0x2c2d31), bandText = c(0xececec), bandDim = c(0x9a9ca1),
    tile = c(0x45474d), tileText = c(0xf4f4f4), tileRim = c(0x6b6d73),
    tileOn = c(0x1d6fc4), tileOnText = c(0xffffff), tileOnRim = c(0x9cc8ff),
    tileOff = c(0xbdbec1), tileOffText = c(0x8e9095), tileOffRim = c(0xa9aaad),
    tileRand = c(0xf2b33d), tileRandText = c(0x141416),
    tileBan = c(0x7c3aed), tileBanText = c(0xffffff), tileBanRim = c(0xc4a1ff),
    sel = c(0x141416), selText = c(0xf2f2f2), selDim = c(0x77797e),
    badgeHi = c(0xc8202f), badgeHiText = c(0xffffff), badgeLo = c(0xf2f2f2), badgeLoText = c(0x141416),
    bar = c(0x26272b), level = c(0xffffff), warn = c(0xffffff), start = c(0x9e1b28), startText = c(0xffffff),
    startOff = c(0x4a4b51), startOffText = c(0x8e9095), rules = c(0x18181b), rulesRim = c(0x3a3b40),
    clear = c(0x3a3b40), clearText = c(0xececec), tag = c(0xc8202f), tagText = c(0xffffff),
    live = c(0x18181b), liveRim = c(0xc8202f), danger = c(0xff4d5a), idle = c(0xf2b33d),
    side = c(0x18191c), sideText = c(0xcfd0d3), sideOn = c(0xc8202f),
    comboYellow = c(0xffd23f), comboPurple = c(0xb06cf0), comboBlue = c(0x2456c7), comboRed = c(0xff2b3a),
)

/** "渊默": 参考渊默行动的配色: 深绿灰的页面, 绿灰的词条板和格子, 深绿色的选中, 橙色的标签 / 开始按钮 (同电脑版 DEEP). */
val DEEP_COLORS = ContractColors(
    bg = c(0x14241b), text = c(0xe8f2ec), dim = c(0x8fae9c), line = c(0xe0782b),
    board = c(0xb5c9b9), band = c(0x1c3326), bandText = c(0xe3f0e6), bandDim = c(0x88a893),
    tile = c(0x3b5a45), tileText = c(0xeef6f0), tileRim = c(0x6f9a7e),
    tileOn = c(0x2a9356), tileOnText = c(0xffffff), tileOnRim = c(0xa4f0c0),
    tileOff = c(0xa2b8a7), tileOffText = c(0x6f8c78), tileOffRim = c(0x8ea693),
    tileRand = c(0xf0a04a), tileRandText = c(0x0e1b13),
    tileBan = c(0x6d56c9), tileBanText = c(0xffffff), tileBanRim = c(0xbdaef5),
    sel = c(0x0e1b13), selText = c(0xe8f2ec), selDim = c(0x6f9378),
    badgeHi = c(0xe0782b), badgeHiText = c(0xffffff), badgeLo = c(0xe8f2ec), badgeLoText = c(0x0e1b13),
    bar = c(0x182a1f), level = c(0xffffff), warn = c(0xffffff), start = c(0xc4631e), startText = c(0xffffff),
    startOff = c(0x3a5042), startOffText = c(0x88a893), rules = c(0x0e1b13), rulesRim = c(0x365a44),
    clear = c(0x33543d), clearText = c(0xe3f0e6), tag = c(0xe0782b), tagText = c(0xffffff),
    live = c(0x0e1b13), liveRim = c(0xe0782b), danger = c(0xff9a5c), idle = c(0xf0a04a),
    side = c(0x0f1c14), sideText = c(0xbdd4c3), sideOn = c(0x2a9356),
    comboYellow = c(0xffd23f), comboPurple = c(0xb06cf0), comboBlue = c(0x2456c7), comboRed = c(0xff2b3a),
    tagName = "渊默行动", brand = "OPERATION DEEPNESS",
)

@Composable
fun contractColors(): ContractColors {
    val core = OitApp.instance.core
    when (core.settings.str("CONTRACT_THEME")) {
        "deep" -> return DEEP_COLORS
        "ui" -> {}
        else -> return OPS_COLORS
    }
    val o = LocalOit.current
    return ContractColors(
        bg = o.bg, text = o.text, dim = o.dim, line = o.border,
        board = o.bg, band = o.card, bandText = o.text, bandDim = o.dim,
        tile = o.card, tileText = o.text, tileRim = o.border,
        tileOn = o.danger, tileOnText = o.onAccent, tileOnRim = o.danger,
        tileOff = o.bg, tileOffText = o.border, tileOffRim = o.border,
        tileRand = o.warn, tileRandText = c(0x141416),
        tileBan = c(0x8b5cf6), tileBanText = c(0xffffff), tileBanRim = c(0x8b5cf6),
        sel = o.card, selText = o.text, selDim = o.dim,
        badgeHi = o.danger, badgeHiText = o.onAccent, badgeLo = o.border, badgeLoText = o.text,
        bar = o.card, level = o.danger, warn = o.warn, start = o.danger, startText = o.onAccent,
        startOff = o.border, startOffText = o.dim, rules = o.card, rulesRim = o.border,
        clear = o.border, clearText = o.text, tag = o.danger, tagText = o.onAccent,
        live = o.card, liveRim = o.danger, danger = o.danger, idle = o.warn,
        side = null, sideText = o.text, sideOn = o.accent,
        // 白和灰在浅色主题里用文字色, 免得看不见
        comboYellow = c(0xd9a400), comboPurple = c(0x8b5cf6), comboBlue = c(0x2456c7), comboRed = o.danger,
    )
}

/** 组合的颜色按当前主题的. */
private fun comboColor(p: ContractColors, combo: Combo): Color = when (combo.color) {
    "yellow" -> p.comboYellow
    "purple" -> p.comboPurple
    "blue" -> p.comboBlue
    else -> p.comboRed
}

/** 凑齐了的组合的成员: 词条 → 颜色 (界面上框住它们), 颜色按当前主题的. */
private fun comboMarksOf(c: Contract, sel: Collection<String>, p: ContractColors): Map<String, Color> {
    val raw = Contract.comboMarks(sel)
    if (raw.isEmpty()) return emptyMap()
    val byHex = Contract.COMBOS.associate { Contract.COMBO_COLORS[it.color]!! to it.color }
    return raw.mapValues { (_, hex) ->
        when (byHex[hex]) {
            "yellow" -> p.comboYellow
            "purple" -> p.comboPurple
            "blue" -> p.comboBlue
            else -> p.comboRed
        }
    }
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

/** 第 3 页的一块组合: 名字 (组合的颜色) + 状态, 凑齐要哪些词条, 效果. 凑齐了才亮. */
@Composable
private fun ComboCard(p: ContractColors, combo: Combo, state: String, theme: String, modifier: Modifier = Modifier) {
    val color = comboColor(p, combo)
    val on = state == "done"
    val status = when (state) {
        "done" -> tr("已凑齐")
        "void" -> tr("已失效")
        else -> tr("未凑齐")
    }
    val rim = if (on) color else if (state == "void") p.tileRim else p.tileOffRim
    Column(modifier.background(p.sel).border(if (on) 2.dp else 1.dp, rim, RoundedCornerShape(2.dp)).padding(8.dp),
        verticalArrangement = Arrangement.spacedBy(3.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(tr(combo.name), color = color, fontSize = 13.sp, fontWeight = FontWeight.Bold,
                modifier = Modifier.weight(1f))
            Text(status, color = if (on) color else if (state == "void") p.danger else p.selDim,
                fontSize = 10.sp, fontWeight = FontWeight.Bold)
        }
        Text(tr("需要") + "  " + combo.reqs.joinToString(" · ") { tr(Contract.reqName(it)) },
            color = if (on) p.selText else p.selDim, fontSize = 10.sp, lineHeight = 12.sp)
        // 主题绑定的组合多一行主题加成
        val note = Contract.themeNote(combo, theme)
        Text(tr(combo.desc) + if (note.isNotEmpty()) "\n" + tr(note) else "", color = if (on) color else p.selDim,
            fontSize = 10.sp, lineHeight = 12.sp)
    }
}

/** 词条板 (行 = 等级, 列 = 类型, 同一类型在同一列; 翻页: 成系列的一页, 零散的一页, 第 3 页是词条组合), 下面是已选中的词条 (两栏),
 *  最下面一条: 清空 | 危机等级 | 开始行动.
 *  合约进行中时只能看. 这一页总是横屏 (Root 里设置), 整页缩小到一屏显示全部 (FitToScreen). 颜色按"合约页主题". */
/** 合约页的录像状态 (MECHANICS 18.8): 各设备在录哪一路也同步显示. */
private fun recStatus(c: Contract): String {
    val app = OitApp.instance
    val recs = c.records()
    val peerRec = HashMap<String, MutableList<String>>()
    app.sync?.deviceStates()?.forEach { d ->
        if (!d.online) return@forEach
        val arr = d.status.optJSONArray("rec") ?: return@forEach
        for (i in 0 until arr.length()) peerRec.getOrPut(arr.getString(i)) { mutableListOf() }.add(d.name)
    }
    val parts = ArrayList<String>()
    for ((kind, label) in listOf("camera" to tr("📷 摄像头"), "screen" to tr("🖥 屏幕"))) {
        if (kind !in recs) continue
        val who = ArrayList<String>()
        if (app.recordManager.isRecording(kind)) who.add(tr("本机"))
        who.addAll(peerRec[kind] ?: emptyList())
        parts.add(label + " " + if (who.isNotEmpty()) tr("{who} 录像中", "who" to who.joinToString("、")) else tr("未录像"))
    }
    return parts.joinToString("   ·   ")
}

@Composable
fun ContractScreen() {
    LocalTick.current
    val p = contractColors()
    val core = OitApp.instance.core
    val c = core.contract
    val active = c.active
    val now = LocalDateTime.now()
    var tapped by rememberSaveable { mutableStateOf("") }
    var ask by rememberSaveable { mutableStateOf("") }        // start / prestart / end / close:词条
    var preDelay by rememberSaveable { mutableStateOf(0) }    // 睡眠提醒: 至少会延迟多少分钟
    var page by rememberSaveable { mutableStateOf(0) }
    val sel = if (active) c.terms else c.draft
    val rand = c.rand
    // 完成合约时按哪个主题结算: 进行中的合约按开始时记下的, 没开始时按现在选的 (设置里的"跟随界面主题"没有主题)
    val bonusTheme = if (active) c.theme else Contract.cleanTheme(core.settings.str("CONTRACT_THEME"))
    // 词条板最窄要放下一页 (列数最多的那页) 每列 64dp 的格子, 再窄就整页缩小
    val pageCols = Contract.PAGES.maxOf { it.count() }
    val minW = BOARD_BAND + (BOARD_TILE + BOARD_GAP) * pageCols + BOARD_GAP + 32.dp
    FitToScreen(minW) { Column(Modifier.fillMaxWidth().background(p.bg).padding(horizontal = 16.dp, vertical = 6.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(tr(p.tagName), color = p.tagText, fontSize = 12.sp, fontWeight = FontWeight.Bold,
                modifier = Modifier.background(p.tag).padding(horizontal = 8.dp, vertical = 2.dp))
            Text(if (p.brand.isEmpty()) "CONTRACT" else "CONTRACT · ${p.brand}", color = p.dim, fontWeight = FontWeight.Bold, fontSize = 14.sp,
                modifier = Modifier.padding(start = 10.dp).weight(1f))
            if (active) Text(tr("合约进行中"), color = p.danger, fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(end = 12.dp))
            // 右上角: 合约期间的特别规则 (两栏)
            val n = if (active) c.violationLimit else Contract.lifeOf(c.draft)
            Row(Modifier.background(p.rules).border(1.dp, p.rulesRim).padding(horizontal = 8.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(tr("合约期间"), color = p.danger, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                Text(listOf(tr("· 目标生命值 {n}: 违反一次 -1, 归零即失败", "n" to n), tr("· 空闲 / 设备离线共 15 分钟: 失败"),
                    tr("· 整点记录超时未填写: 违反 1 次")).joinToString("\n"), color = p.dim, fontSize = 10.sp, lineHeight = 13.sp)
                Text(listOf(tr("· \"我累了\"不可用"), tr("· 暂停休息: 合约前积攒的时间不打折"),
                    tr("· 合约中得到的休息时间结束后作废 (奖励照常)")).joinToString("\n"),
                    color = p.dim, fontSize = 10.sp, lineHeight = 13.sp)
            }
        }
        Box(Modifier.fillMaxWidth().height(2.dp).background(p.line))

        if (active) Column(Modifier.fillMaxWidth().background(p.live).border(2.dp, p.liveRim).padding(12.dp)) {
            val end = c.endAt ?: now
            val fog = c.fog()
            Row(verticalAlignment = Alignment.CenterVertically) {
                // 黑雾: 看不到合约的剩余时间
                Text(if (fog) "???" else hms(secondsBetween(now, end)), color = p.text, fontSize = 34.sp, fontWeight = FontWeight.Bold,
                    modifier = Modifier.weight(1f))
                // 目标生命值: 每违反一次减一, 减到 0 合约失败
                Text(tr("目标生命值") + "  " + "♥".repeat(c.life) + "♡".repeat(c.violationLimit - c.life) +
                    "  ${c.life} / ${c.violationLimit}", color = p.danger, fontWeight = FontWeight.Bold, fontSize = 18.sp)
            }
            // 黑雾: 开始和结束的时刻都不显示
            var line = (if (fog) "??:??" else c.startAt?.format(Fmt.HM) ?: "") + " — " + (if (fog) "??:??" else end.format(Fmt.MDHM))
            c.netLeft(core.net.usedAll)?.let { line += "   ·   " + tr("合约的联网额度还剩 {t}", "t" to fmtHm(it)) }
            Text(line, color = p.dim, fontSize = 13.sp)
            c.idleLeft(now)?.let {
                Text(tr("空闲中: {t} 后合约终止", "t" to fmtMs(it)), color = p.idle, fontWeight = FontWeight.Bold, fontSize = 13.sp)
            }
        }

        // 词条板: 左边一条写着等级, 右边六边形的格子; 最后一页是词条组合的效果
        val comboPage = page == Contract.COMBO_PAGE
        val comboMarks = comboMarksOf(c, if (active) c.mine else sel, p)
        BoxWithConstraints(Modifier.fillMaxWidth().background(p.board)) {
            val bandW = BOARD_BAND
            val gap = BOARD_GAP
            val n = pageCols
            val tileW = maxOf(BOARD_TILE, minOf(84.dp, (maxWidth - bandW - gap * (n + 1)) / n))
            val tileH = tileW * (SQRT3 / 2)
            Row {
                Column(Modifier.width(bandW).background(p.band).padding(vertical = gap),
                    verticalArrangement = Arrangement.spacedBy(gap)) {
                    if (comboPage) {
                        Text("CONTRACT\nEFFECT", color = p.bandDim, fontSize = 9.sp, fontWeight = FontWeight.Bold,
                            textAlign = TextAlign.Center, lineHeight = 11.sp,
                            modifier = Modifier.fillMaxWidth().padding(top = 6.dp))
                        Text(tr("词条组合").chunked(1).joinToString("\n"), color = p.bandText, fontSize = 13.sp,
                            fontWeight = FontWeight.Bold, textAlign = TextAlign.Center, lineHeight = 15.sp,
                            modifier = Modifier.fillMaxWidth().padding(top = 6.dp))
                    } else {
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
                }
                if (comboPage) {
                    val combos = if (active) c.combos() else Contract.COMBOS.map { it to Contract.comboState(it, c.draft) }
                    Column(Modifier.weight(1f).padding(gap), verticalArrangement = Arrangement.spacedBy(gap)) {
                        for (row in combos.chunked(2)) Row(horizontalArrangement = Arrangement.spacedBy(gap)) {
                            for ((combo, state) in row) ComboCard(p, combo, state, bonusTheme, Modifier.weight(1f))
                            if (row.size == 1) Spacer(Modifier.weight(1f))
                        }
                    }
                } else {
                    Column(Modifier.padding(gap), verticalArrangement = Arrangement.spacedBy(gap)) {
                        for (lv in 1..Contract.MAX_LEVEL) Row(horizontalArrangement = Arrangement.spacedBy(gap)) {
                            // 成系列的每组一列, 零散的在右边挨着排 (Contract.LAYOUT); 只画这一页的列
                            for (col in Contract.PAGES[page.coerceIn(0, Contract.PAGES.size - 1)]) {
                                val t = Contract.TERMS.firstOrNull { it.level == lv && Contract.LAYOUT[it.id] == col }
                                if (t == null) {
                                    Spacer(Modifier.width(tileW).height(tileH)); continue
                                }
                                val picked = t.id in rand               // 未知变量选中的: 黄色, 点按关闭
                                val ban = active && c.canLift(t.id)     // 禁止联网中: 紫色, 点按解除网络禁令
                                val on = t.id in sel
                                val lost = active && !on && t.id in c.lost    // 违反到失效: "未选择"的样子
                                val off = !on && !lost && (active || Contract.blockedBy(t.id, sel) != null)
                                val fill = if (picked) p.tileRand else if (ban) p.tileBan else if (on) p.tileOn else if (off) p.tileOff else p.tile
                                val fg = if (picked) p.tileRandText else if (ban) p.tileBanText else if (on) p.tileOnText else if (off) p.tileOffText else p.tileText
                                // 凑齐了的组合: 用组合的颜色框住
                                val cm = comboMarks[t.id]
                                val rim = if (picked) p.tileRand else cm ?: if (ban) p.tileBanRim else if (on) p.tileOnRim else if (off) p.tileOffRim else p.tileRim
                                Box(Modifier.width(tileW).height(tileH).background(fill, HEX)
                                    .border(if (cm != null) 3.dp else 2.dp, rim, HEX)
                                    .clickable {
                                        tapped = t.id
                                        if (active) {
                                            if (picked) ask = "close:" + t.id
                                            else if (ban) ask = "lift:" + t.id
                                            return@clickable
                                        }
                                        val cur = c.draft.toMutableList()
                                        if (t.id in cur) cur.remove(t.id)
                                        else if (Contract.blockedBy(t.id, cur) == null) {
                                            // 目光 / 刻痕: 选上前先提示会录像 + 加密 (MECHANICS 18.8)
                                            if (t.id == "gaze" || t.id == "scar") { ask = "pickwarn:" + t.id; return@clickable }
                                            cur.add(t.id)
                                        }
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
        }
        // 点过的词条的内容 | 翻页
        Row(verticalAlignment = Alignment.CenterVertically) {
            var text = ""
            if (comboPage) text = tr("凑齐列出的词条 / 词条系列时生效")
            else if (tapped.isNotEmpty()) {
                text = "${marks(Contract.BY_ID[tapped]!!.level)}  " + termLine(tapped)
                val other = Contract.blockedBy(tapped, sel)
                if (other != null && tapped !in sel) text += "  " + tr("[与 {name} 冲突]", "name" to termName(other))
                if (tapped in rand) text += "  " + tr("[未知变量选中 · 点击关闭, 算一次违反]")
                else if (active && c.canLift(tapped)) text += "  " + tr("[禁止联网中 · 点击解除, 目标生命值 -{n}]", "n" to c.liftCost(tapped))
            }
            Text(text, color = p.dim, fontSize = 13.sp, modifier = Modifier.weight(1f))
            val last = Contract.COMBO_PAGE
            Text("◀", color = if (page > 0) p.text else p.startOff, fontSize = 20.sp, fontWeight = FontWeight.Bold,
                modifier = Modifier.clickable(enabled = page > 0) { page-- }.padding(horizontal = 14.dp, vertical = 2.dp))
            Text("${page + 1} / ${last + 1}", color = p.text, fontSize = 14.sp, fontWeight = FontWeight.Bold)
            Text("▶", color = if (page < last) p.text else p.startOff, fontSize = 20.sp, fontWeight = FontWeight.Bold,
                modifier = Modifier.clickable(enabled = page < last) { page++ }.padding(horizontal = 14.dp, vertical = 2.dp))
        }

        // 已选中的词条: 黑底, 两栏. (等级, 文字, 是否失效, 未知变量选中的词条 / "", 现在的词条); 合约中最后是未知变量选中的 (黄色, 点按关闭)
        data class SelRow(val lv: Int, val text: String, val void: Boolean, val rand: String = "", val id: String = "")
        val rows = if (!active) sel.map { SelRow(Contract.BY_ID[it]!!.level, termLine(it), false, "", it) }
        else c.orig.map { t ->
            val g = Contract.BY_ID[t]!!.group
            val cur = c.terms.firstOrNull { it !in rand && (it == t || Contract.BY_ID[it]!!.group == g) }
            when {
                cur == null -> SelRow(Contract.BY_ID[t]!!.level, termName(t) + "   " + tr("[已失效]"), true)
                cur != t -> SelRow(c.termLevel(cur), termLine(cur) + "   " +
                    tr("[原 {name}]", "name" to termShort(t)), false, "", cur)
                c.termLevel(t) < Contract.BY_ID[t]!!.level -> SelRow(c.termLevel(t), termLine(t) + "   " +
                    tr("[降了 {n} 级]", "n" to Contract.BY_ID[t]!!.level - c.termLevel(t)), false, "", t)
                else -> SelRow(c.termLevel(t), termLine(t), false, "", t)
            }
        } + rand.map { id ->
            var text = termLine(id) + "   " + tr("[未知变量选中 · 点击关闭]")
            c.randUntil(id)?.let { text += "   " + tr("[{t} 移除]", "t" to it.format(Fmt.HM)) }
            SelRow(c.termLevel(id), text, false, id)
        } + c.waiting.map { (id, at) ->        // 抽中了还没生效的
            SelRow(Contract.BY_ID[id]!!.level, termLine(id) + "   " +
                tr("[未知变量抽中 · {t} 生效]", "t" to at.first.format(Fmt.HM)), true)
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
                    for ((lv, text, void, picked, tid) in part) {
                        val cm = comboMarks[tid]        // 凑齐了的组合: 用组合的颜色框住
                        val ban = active && tid.isNotEmpty() && picked.isEmpty() && c.canLift(tid)   // 禁止联网中: 点按解除
                        Row(Modifier.padding(vertical = 3.dp)
                            .then(if (cm != null) Modifier.border(2.dp, cm, RoundedCornerShape(2.dp)) else Modifier)
                            .clickable(enabled = picked.isNotEmpty() || ban) { ask = if (ban) "lift:$tid" else "close:$picked" }) {
                            val hi = lv >= 2 && !void
                            val badge = if (picked.isNotEmpty()) p.tileRand else if (ban) p.tileBan else if (hi) p.badgeHi else p.badgeLo
                            Text(marks(lv).ifEmpty { "—" }, fontSize = 11.sp,
                                color = if (picked.isNotEmpty()) p.tileRandText else if (ban) p.tileBanText else if (hi) p.badgeHiText else p.badgeLoText,
                                fontWeight = FontWeight.Bold, textAlign = TextAlign.Center,
                                modifier = Modifier.widthIn(min = 36.dp).background(badge)
                                    .padding(horizontal = 4.dp, vertical = 1.dp))
                            Text(text + if (ban) "   " + tr("[禁止联网 · 点击解除]") else "",
                                color = if (picked.isNotEmpty()) p.tileRand else if (ban) p.tileBanRim else if (void) p.selDim else p.selText,
                                fontSize = 12.sp, modifier = Modifier.padding(start = 8.dp))
                        }
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

        // 录像状态 (目光 / 刻痕): 各设备在录哪一路也同步显示 (MECHANICS 18.8)
        if (active && c.records().isNotEmpty()) {
            val rec = recStatus(c)
            if (rec.isNotEmpty()) Text(rec, color = p.danger, fontSize = 12.sp, fontWeight = FontWeight.Bold,
                modifier = Modifier.fillMaxWidth().background(p.live).padding(horizontal = 16.dp, vertical = 4.dp))
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
                // 扣过等级: 最终等级[最初等级-扣的等级]
                Text(Contract.levelLabel(level, orig), color = p.level, fontSize = 32.sp, fontWeight = FontWeight.Bold)
            }
            var info = ""
            info += tr("时长 {t}", "t" to fmtHm(Contract.durationOf(if (active) c.orig else sel))) + "\n" +
                tr("完成后休息 +{n} 分钟", "n" to Contract.restMinutesTotal(level, if (active) c.mine else sel, bonusTheme))
            // 合约中: 开始时已经给出的 (按开始时选的词条算, 之后成员失效 / 未知变量选中都不变, MECHANICS 18.3.1)
            val gift = Contract.restGift(if (active) c.orig else sel)
            if (gift > 0) info += "\n" + tr("开始时休息 +{n} 分钟", "n" to gift / 60)
            Text(info, color = p.text, fontSize = 12.sp, modifier = Modifier.padding(start = 14.dp).weight(1f))
            if (level >= Contract.HARD_LEVEL)
                Text(tr("⚠  当前合约难度极大 请谨慎行动"), color = p.warn, fontSize = 12.sp, fontWeight = FontWeight.Bold,
                    modifier = Modifier.widthIn(max = 170.dp).padding(end = 12.dp))
            val why = if (active) "" else core.contractBlock()
            // 合约中只要还有在录的词条 (任意设备) 就多一个"终止录像"按钮 (任意一端都能终止整个录制, MECHANICS 18.8):
            // 和"提前结束"同时出现时, 终止录像在左, 提前结束在右; 否则 (只有一个按钮) 还按原来的靠右
            val recording = active && c.records().isNotEmpty()
            val endBtn: @Composable () -> Unit = {
                Text(if (active) tr("■  提前结束") else tr("开始行动"), color = if (why.isEmpty()) p.startText else p.startOffText,
                    fontSize = 18.sp, fontWeight = FontWeight.Bold,
                    modifier = Modifier.background(if (why.isEmpty()) p.start else p.startOff, RoundedCornerShape(2.dp))
                        .clickable(enabled = why.isEmpty()) {
                            if (active) {
                                ask = "end"
                            } else {
                                // 睡觉时间 (MECHANICS 19): 合约结束时间晚于这次要睡的时间时, 当天第一次先确认一次
                                val end = LocalDateTime.now().plusSeconds(Contract.durationOf(c.draft).toLong())
                                preDelay = core.daily.contractDelay(end)
                                ask = if (preDelay > 0) "prestart" else "start"
                            }
                        }
                        .padding(horizontal = 26.dp, vertical = 12.dp))
            }
            if (recording) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(tr("■  终止录像"), color = p.clearText, fontSize = 15.sp, fontWeight = FontWeight.Bold,
                        modifier = Modifier.background(p.clear, RoundedCornerShape(2.dp))
                            .clickable { ask = "stoprec" }.padding(horizontal = 16.dp, vertical = 12.dp))
                    endBtn()
                }
            } else {
                Column(horizontalAlignment = Alignment.End) {
                    if (why.isNotEmpty()) Text(why, color = p.dim, fontSize = 10.sp, modifier = Modifier.padding(bottom = 2.dp))
                    endBtn()
                }
            }
        }
    } }
    when (ask) {
        "prestart" -> Confirm(tr("🌙  该睡觉了"), core.daily.lateText(preDelay), true, { ask = "" }) {
            ask = "start"
        }
        "start" -> {
            val lv = Contract.levelOf(c.draft)
            // 不显示时长和结束时刻
            Confirm(tr("▶  开始行动"), tr("危机等级 {lv}", "lv" to lv) + "\n" +
                tr("完成后休息 +{n} 分钟", "n" to Contract.restMinutesTotal(lv, c.draft, bonusTheme)), true, { ask = "" }) {
                core.startContract(c.draft)
            }
        }
        "end" -> {
            var msg = tr("提前结束合约?")
            if (c.reward >= 1) msg += "\n" + tr("现在结束只给 {m} 分钟休息时间 (完成给 {n} 分钟).",
                "m" to Math.round(c.rewardAt(java.time.LocalDateTime.now()) / 60.0), "n" to Math.round(c.reward / 60))
            Confirm(tr("■  提前结束"), msg, true, { ask = "" }) { core.abandonContract() }
        }
        else -> if (ask.startsWith("lift:")) {
            val id = ask.removePrefix("lift:")
            if (!c.canLift(id)) ask = ""
            else {
                val n = c.liftCost(id)
                var msg = tr("解除「{name}」的网络禁令?", "name" to termName(id)) + "\n" +
                    tr("将扣除目标生命值 {n} (现在 {life} / {m}).", "n" to n, "life" to c.life, "m" to c.violationLimit)
                if (n >= c.life) msg += "\n" + tr("目标生命值会归零, 合约失败.")
                Confirm(tr("解除网络禁令"), msg, true, { ask = "" }) { core.liftNetBan(id) }
            }
        } else if (ask.startsWith("close:")) {
            val id = ask.removePrefix("close:")
            if (id !in c.rand) ask = ""
            else Confirm(tr("关闭词条"), tr("关闭未知变量选中的 {name}?", "name" to termName(id)) + "\n" + tr("会算作一次违反."),
                true, { ask = "" }) { core.closeRandomTerm(id) }
        } else if (ask == "stoprec") {
            // 终止整个录制过程 (任意一端都能终止, MECHANICS 18.8): 正在录的每一路各算一次违反, 所有设备都停, 已录的照样保存
            if (c.records().isEmpty()) ask = ""
            else Confirm(tr("■ 终止录像"),
                tr("终止整个录制过程?"),
                true, { ask = "" }) { OitApp.instance.recordManager.terminate() }
        } else if (ask.startsWith("pickwarn:")) {
            // 目光 / 刻痕: 选上前提示会录像 + 加密 (MECHANICS 18.8). 选不选由用户决定.
            val id = ask.removePrefix("pickwarn:")
            val msg = if (id == "gaze")
                tr("该词条会使用摄像头记录你的行为(和声音,可在设置关闭),但记录的视频会被加密,只能在OIT终端查看,防止自动上传类似的功能泄露隐私.")
            else
                tr("该词条会使用录屏记录你的行为(和系统声音,可在设置关闭),但记录的视频会被加密,只能在OIT终端查看,防止自动上传类似的功能泄露隐私.")
            Confirm(tr("主管!"), msg, true, { ask = "" }) {
                val cur = c.draft.toMutableList()
                if (id !in cur && Contract.blockedBy(id, cur) == null) { cur.add(id); c.setDraft(cur); core.changed() }
            }
        }
    }
}
