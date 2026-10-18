package com.obliphur.oit.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.obliphur.oit.OitApp
import com.obliphur.oit.core.f1
import com.obliphur.oit.core.fmtHm
import com.obliphur.oit.core.key
import com.obliphur.oit.core.monthPrefixes
import com.obliphur.oit.core.tallyItems
import com.obliphur.oit.core.tr
import com.obliphur.oit.service.Notifier
import com.obliphur.oit.service.appLabel
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.YearMonth
import kotlin.math.atan2

// ════════════════ Hour Log 页 (MECHANICS 7) ════════════════

@Composable
fun HourLogScreen(go: (String) -> Unit) {
    LocalTick.current
    val o = LocalOit.current
    val core = OitApp.instance.core
    val dm = core.dm
    val enabled = dm.hourEnabled
    val day0 = LocalDate.parse(dm.todayDate).atStartOfDay()
    val now = LocalDateTime.now()
    val awaiting = dm.awaitingSlot()
    val list = rememberLazyListState()
    LaunchedEffect(dm.todayDate, awaiting) {
        val target = awaiting ?: now.withMinute(0).withSecond(0).withNano(0)
        val row = ((java.time.Duration.between(day0, target).toHours()) + 1).toInt().coerceIn(0, 23)
        list.scrollToItem(maxOf(0, row - 2))
    }
    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(tr("当前: {state}", "state" to if (enabled) tr("开启") else tr("关闭_状态")),
                    color = if (enabled) o.success else o.dim, fontWeight = FontWeight.Bold)
                Dim(tr("{date}(今天)", "date" to dm.todayDate) + if (enabled) "" else tr("   ·  记录已关闭"))
            }
            PlainButton(tr("⚙ 设置")) { go("settings:log") }
        }
        LazyColumn(state = list, modifier = Modifier.fillMaxSize().padding(top = 8.dp)) {
            items(24) { h ->
                // 每一行的时间是这一小时的结束时刻; 第 0 行是昨天 23:00 ~ 24:00
                val start = day0.plusHours((h - 1).toLong())
                val end = start.plusHours(1)
                val ended = !end.isAfter(now)
                val ds = start.toLocalDate().key()
                val stored = dm.hourText(ds, start.hour)
                val isWait = awaiting == start
                val isNow = enabled && !start.isAfter(now) && now.isBefore(end)
                HourRow("%02d:00".format(h) + when {
                    isNow -> tr("   ◂ 现在")
                    isWait -> tr("   ◂ 待填写")
                    else -> ""
                }, stored, enabled && ended, isWait || isNow) { text ->
                    if (text != stored) {
                        dm.setHourText(ds, start.hour, text)
                        if (isWait) Notifier.cancelHour(OitApp.instance)
                        core.changed()
                    }
                }
            }
        }
    }
}

@Composable
private fun HourRow(label: String, stored: String, editable: Boolean, highlight: Boolean, commit: (String) -> Unit) {
    val o = LocalOit.current
    var text by remember(stored) { mutableStateOf(stored) }
    val focus = LocalFocusManager.current
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 3.dp)) {
        Text(label, color = if (highlight) o.accent else if (editable) o.text else o.dim,
            fontSize = 14.sp, modifier = Modifier.width(118.dp))
        OutlinedTextField(text, { text = it }, enabled = editable, singleLine = true,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { commit(text.trim()); focus.clearFocus() }),
            modifier = Modifier.weight(1f).onFocusChanged { if (!it.isFocused && text.trim() != stored) commit(text.trim()) })
    }
}

// ════════════════ Calendar 页 (MECHANICS 8.2) ════════════════

@Composable
fun CalendarScreen() {
    LocalTick.current
    val o = LocalOit.current
    val core = OitApp.instance.core
    var last by rememberSaveable { mutableStateOf(false) }
    val today = LocalDate.now()
    val ym = YearMonth.from(today).let { if (last) it.minusMonths(1) else it }
    val prefix = "%d-%02d".format(ym.year, ym.monthValue)
    val data = core.dm.monthStudy(prefix)
    PageColumn {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(prefix, fontWeight = FontWeight.Bold, fontSize = 18.sp, color = o.text, modifier = Modifier.weight(1f))
            Segmented(listOf("last" to tr("‹ 上月"), "this" to tr("本月 ›")), if (last) "last" else "this") { last = it == "last" }
        }
        Dim(tr("合计 {h} h", "h" to f1(data.values.sum())))
        val names = if (OitApp.instance.core.settings.str("LANGUAGE") == "en")
            listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun") else listOf("一", "二", "三", "四", "五", "六", "日")
        Row { names.forEach { Text(it, color = o.dim, textAlign = TextAlign.Center, modifier = Modifier.weight(1f)) } }
        val first = ym.atDay(1)
        val lead = first.dayOfWeek.value - 1
        val cells = lead + ym.lengthOfMonth()
        for (week in 0 until (cells + 6) / 7) {
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                for (dow in 0 until 7) {
                    val idx = week * 7 + dow - lead
                    Box(Modifier.weight(1f).aspectRatio(0.8f)) {
                        if (idx in 0 until ym.lengthOfMonth()) {
                            val d = first.plusDays(idx.toLong())
                            val h = data[d.key()] ?: 0.0
                            val tint = o.accent.copy(alpha = (h / 8.0).coerceIn(0.0, 1.0).toFloat() * 0.55f)
                            Column(Modifier.fillMaxSize().background(o.card, RoundedCornerShape(6.dp))
                                .background(tint, RoundedCornerShape(6.dp))
                                .border(1.dp, if (d == today) o.accent else o.border, RoundedCornerShape(6.dp))
                                .padding(4.dp)) {
                                Text("${d.dayOfMonth}", fontSize = 12.sp, color = o.dim)
                                if (h > 0) Text(f1(h), fontSize = 13.sp, fontWeight = FontWeight.Bold, color = o.text,
                                    modifier = Modifier.fillMaxWidth(), textAlign = TextAlign.Center)
                            }
                        }
                    }
                }
            }
        }
    }
}

// ════════════════ Statistics 页 (MECHANICS 8.1) ════════════════

private const val OTHER_BELOW = 0.01
private const val OTHER_TIP_LINES = 25

@Composable
fun StatsScreen() {
    LocalTick.current
    val o = LocalOit.current
    val core = OitApp.instance.core
    val ctx = LocalContext.current
    val logOn = core.dm.hourEnabled
    var page by rememberSaveable { mutableStateOf(if (logOn) "log" else "net") }
    if (!logOn && page == "log") page = "net"
    var last by rememberSaveable { mutableStateOf(false) }
    var device by rememberSaveable { mutableStateOf("all") }      // 应用页看哪台设备
    val modes = remember { mutableStateOf(mapOf<String, String>()) }
    val pct = (modes.value[page] ?: "pct") == "pct"
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        val tabs = buildList {
            if (logOn) add("log" to tr("📝  整点记录"))
            add("net" to tr("🌐  联网理由"))
            add("focus" to tr("⏱  专注计时"))
            add("window" to tr("📱  应用"))
        }
        Segmented(tabs, page) { page = it }
        if (page == "focus") {
            FocusBars()
            return@Column
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Segmented(listOf("this" to tr("本月"), "last" to tr("上月")), if (last) "last" else "this") { last = it == "last" }
            Segmented(listOf("pct" to tr("占比"), "time" to tr("总时间")), if (pct) "pct" else "time") {
                modes.value = modes.value + (page to it)
            }
        }
        // 应用页: 绑定了其他设备时按设备切换 (全部 / 本机 / 每台设备; 全部时重叠的分钟以母端为准)
        if (page == "window") {
            val devices = listOf("all" to tr("全部设备")) + core.dm.windowDevices().map { (k, n) -> k to if (k == "self") tr("本机") else n }
            if (devices.none { it.first == device }) device = "all"
            if (devices.size > 2) Segmented(devices, device) { device = it }
        }
        val (thisM, lastM) = monthPrefixes()
        val prefix = if (last) lastM else thisM
        val blank = tr("空白")
        val data: Map<String, Double> = when (page) {
            "log" -> core.dm.hourActivitySeconds(prefix)
            "window" -> core.dm.windowSeconds(prefix, device).entries.groupBy({
                when (it.key) { "" -> blank; "?" -> tr("未知程序"); "OIT" -> "OIT"; else -> appLabel(ctx, it.key) }
            }, { it.value }).mapValues { it.value.sum() }
            else -> tallyItems(core.net.usageByReason(prefix))
        }
        Text("${if (last) tr("上月") else tr("本月")} · $prefix", fontWeight = FontWeight.Bold, color = o.text)
        PieBlock(data, pct, if (page == "window") blank else null)
    }
}

@Composable
private fun PieBlock(data: Map<String, Double>, pct: Boolean, blankName: String?) {
    val o = LocalOit.current
    val items = data.entries.sortedWith(compareBy({ -it.value }, { it.key })).map { it.key to it.value }
    val total = items.sumOf { it.second }
    Dim(tr("合计 {t} · {n} 项", "t" to fmtHm(total), "n" to items.size))
    if (items.isEmpty() || total <= 0) {
        Dim(tr("暂无数据")); return
    }
    // 占比 ≥ 1% 的项单独上色 (最多用完调色板), 其余归入「其他」; 「空白」用空白色, 不占调色板
    val main = items.filter { it.second / total >= OTHER_BELOW }.let { l ->
        val out = mutableListOf<Pair<String, Double>>()
        var colored = 0
        for (it in l) {
            if (it.first == blankName) out.add(it) else if (colored < o.chart.size) { out.add(it); colored++ }
        }
        out
    }
    val shown = main.map { it.first }.toSet()
    val other = items.filter { it.first !in shown }
    var ci = 0
    val slices = main.map { (k, v) -> Triple(k, v, if (k == blankName) o.chartEmpty else o.chart[ci++]) }.toMutableList()
    if (other.isNotEmpty()) slices.add(Triple(tr("其他"), other.sumOf { it.second }, o.chartOther))
    var tip by remember { mutableStateOf(false) }
    val otherName = tr("其他")
    Canvas(Modifier.fillMaxWidth().aspectRatio(1.6f).pointerInput(slices) {
        detectTapGestures { p ->
            val c = Offset(size.width / 2f, size.height / 2f)
            val ang = (Math.toDegrees(atan2((p.y - c.y).toDouble(), (p.x - c.x).toDouble())) + 450) % 360
            var acc = 0.0
            for (s in slices) {
                val sweep = s.second / total * 360
                if (ang < acc + sweep) { if (s.first == otherName) tip = true; break }
                acc += sweep
            }
        }
    }) {
        val d = minOf(size.width, size.height) * 0.92f
        val tl = Offset((size.width - d) / 2, (size.height - d) / 2)
        var start = -90f
        for ((_, v, color) in slices) {
            val sweep = (v / total * 360).toFloat()
            drawArc(color, start, sweep, true, tl, Size(d, d))
            start += sweep
        }
    }
    for ((k, v, color) in slices) {
        Row(verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.fillMaxWidth().clickable(enabled = k == otherName) { tip = true }.padding(vertical = 3.dp)) {
            Box(Modifier.size(12.dp).background(color, RoundedCornerShape(2.dp)))
            Text(k, color = o.text, fontSize = 14.sp, modifier = Modifier.weight(1f).padding(start = 8.dp))
            Text(if (pct) "%.1f%%".format(v / total * 100) else fmtHm(v), color = o.text, fontWeight = FontWeight.Bold, fontSize = 13.sp)
        }
    }
    if (tip && other.isNotEmpty()) {
        val lines = other.take(OTHER_TIP_LINES).map { (k, v) ->
            if (pct) "$k    %.2f%%".format(v / total * 100) else "$k    ${fmtHm(v)}"
        }.toMutableList()
        if (other.size > OTHER_TIP_LINES) lines.add(tr("…以及另外 {n} 项", "n" to other.size - OTHER_TIP_LINES))
        AlertDialog(onDismissRequest = { tip = false }, containerColor = o.bg, title = { Text(otherName) },
            text = { Text(lines.joinToString("\n")) },
            confirmButton = { TextButton({ tip = false }) { Text(tr("确定"), color = o.accent) } })
    }
}

/** 最近 7 天, 每天 24 根条, 每根代表一小时, 按比例分成专注 / 休息 / 空白. 点一下看分钟数. */
@Composable
private fun FocusBars() {
    val o = LocalOit.current
    val core = OitApp.instance.core
    val today = LocalDate.now()
    val now = LocalDateTime.now()
    var sel by remember { mutableStateOf<Pair<LocalDate, Int>?>(null) }
    val live = core.timer.live()
    Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
        for ((name, c) in listOf(tr("专注") to o.focusStudy, tr("休息") to o.focusBreak, tr("空白") to o.chartEmpty)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(10.dp).background(c, RoundedCornerShape(2.dp)))
                Text(name, fontSize = 12.sp, color = o.dim, modifier = Modifier.padding(start = 4.dp))
            }
        }
    }
    val weekNames = if (core.settings.str("LANGUAGE") == "en") listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    else listOf("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    for (i in 0 until 7) {
        val day = today.minusDays(i.toLong())
        val hours = core.dm.focusHours(day, live)
        Text("%02d-%02d %s".format(day.monthValue, day.dayOfMonth, weekNames[day.dayOfWeek.value - 1]) +
            if (i == 0) "  " + tr("今天") else "", fontSize = 12.sp, color = o.dim)
        Canvas(Modifier.fillMaxWidth().height(34.dp).pointerInput(day) {
            detectTapGestures { p -> sel = day to (p.x / size.width * 24).toInt().coerceIn(0, 23) }
        }) {
            val w = size.width / 24f
            for (h in 0 until 24) {
                val x = h * w + 1
                val bw = w - 2
                val future = day == today && h > now.hour
                if (future) { drawRect(o.chartFuture, Offset(x, 0f), Size(bw, size.height)); continue }
                val (s, b) = hours[h]
                val hs = (s / 3600).toFloat() * size.height
                val hb = (b / 3600).toFloat() * size.height
                drawRect(o.chartEmpty, Offset(x, 0f), Size(bw, size.height - hs - hb))
                drawRect(o.focusBreak, Offset(x, size.height - hs - hb), Size(bw, hb))
                drawRect(o.focusStudy, Offset(x, size.height - hs), Size(bw, hs))
                if (sel == day to h) drawRect(Color.Black.copy(alpha = 0.15f), Offset(x, 0f), Size(bw, size.height))
            }
        }
    }
    sel?.let { (d, h) ->
        val (s, b) = core.dm.focusHours(d, live)[h]
        Text("%02d-%02d %02d:00–%02d:00 · ".format(d.monthValue, d.dayOfMonth, h, (h + 1) % 24) +
            tr("专注 {s} 分 · 休息 {b} 分", "s" to Math.round(s / 60), "b" to Math.round(b / 60)),
            color = o.text, fontSize = 13.sp)
    }
}
