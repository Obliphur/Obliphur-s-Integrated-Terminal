package com.obliphur.oit.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.obliphur.oit.OitApp
import com.obliphur.oit.core.Comm
import com.obliphur.oit.core.ShareBlock
import com.obliphur.oit.core.ShareCal
import com.obliphur.oit.core.ShareCard
import com.obliphur.oit.core.ShareRows
import com.obliphur.oit.core.ShareView
import com.obliphur.oit.core.tr
import java.time.LocalDate

// ════════════════ share data (MECHANICS 24.12): 页面下方的聊天悬浮窗 + "AI 的数据" ════════════════

/** 页面下方的聊天悬浮窗: 最近的几条消息 + 输入框; 计时器 / 整点记录 / 日历 / 统计页还有 我的数据 / AI 的数据 的切换. */
@Composable
fun ShareDock(page: String, hasAi: Boolean) {
    val o = LocalOit.current
    val comm = OitApp.instance.core.comm
    var ver by remember { mutableIntStateOf(comm.version) }
    LaunchedEffect(Unit) { while (true) { if (comm.version != ver) ver = comm.version; kotlinx.coroutines.delay(300) } }
    var folded by rememberSaveable { mutableStateOf(false) }
    var input by rememberSaveable { mutableStateOf("") }
    var err by remember { mutableStateOf("") }
    val msgs = remember(ver) { comm.visible().takeLast(8) }
    val view = remember(ver) { comm.shareView() }
    val list = rememberLazyListState()
    LaunchedEffect(msgs.lastOrNull()?.id, folded) { if (msgs.isNotEmpty()) list.scrollToItem(msgs.size - 1) }
    Column(Modifier.fillMaxWidth().imePadding().background(o.card).border(1.dp, o.border).padding(horizontal = 10.dp, vertical = 6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("💬 share data · " + tr(Comm.SHARE_PAGE_NAMES[page] ?: page), color = o.text, fontSize = 13.sp, fontWeight = FontWeight.Bold,
                modifier = Modifier.weight(1f))
            if (hasAi) {
                TextButton({ comm.setShareView("me") }) { Text(tr("我的数据"), color = if (view == "me") o.accent else o.dim, fontSize = 12.sp) }
                TextButton({ comm.setShareView("ai") }) { Text(tr("{name} 的数据", "name" to comm.personaTitle(comm.personaName()).ifEmpty { "AI" }), color = if (view == "ai") o.accent else o.dim, fontSize = 12.sp) }
            }
            TextButton({ folded = !folded }) { Text(if (folded) "▴" else "▾", color = o.dim) }
        }
        if (!folded) {
            LazyColumn(Modifier.fillMaxWidth().height(130.dp), state = list, verticalArrangement = Arrangement.spacedBy(4.dp)) {
                items(msgs, key = { it.id }) { m ->
                    Text(m.text, color = if (m.role == "ai") o.text else o.accent, fontSize = 13.sp,
                        modifier = Modifier.fillMaxWidth())
                }
            }
            if (err.isNotEmpty()) Text(tr(err), color = o.danger, fontSize = 12.sp)
            Row(Modifier.fillMaxWidth().padding(top = 4.dp), verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(input, { input = it }, modifier = Modifier.weight(1f), maxLines = 3, singleLine = false)
                AccentButton(tr("发送")) {
                    val why = comm.send(input)
                    if (why.isEmpty()) { input = ""; err = "" } else if (why != "empty") err = why
                }
            }
        }
    }
}

/** AI 的数据: 盖在原页面上 (只读). 内容是 Comm.shareAiView 给的. */
@Composable
fun ShareAiScreen(page: String) {
    val o = LocalOit.current
    val comm = OitApp.instance.core.comm
    var view by remember { mutableStateOf<ShareView?>(null) }
    var kind by rememberSaveable { mutableStateOf("focus") }
    LaunchedEffect(page) {
        while (true) {
            view = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Default) { runCatching { comm.shareAiView(page) }.getOrNull() }
            kotlinx.coroutines.delay(5000)
        }
    }
    Column(Modifier.fillMaxSize().background(o.bg).verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        val v = view
        val who = comm.personaTitle(comm.personaName()).ifEmpty { "AI" }          // 对方的数据写人设名
        if (v == null) { Text(tr("这一页没有 {name} 的数据.", "name" to who), color = o.dim); return@Column }
        Text(tr("{name} 的数据", "name" to who) + " · " + tr(v.title), color = o.text, fontSize = 20.sp, fontWeight = FontWeight.Bold)
        for (b in v.blocks) ShareBlockView(b, kind) { kind = it }
    }
}

@Composable
private fun ShareBlockView(b: ShareBlock, kind: String, onKind: (String) -> Unit) {
    val o = LocalOit.current
    when (b) {
        is ShareCard -> Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(o.card).border(1.dp, o.border, RoundedCornerShape(8.dp)).padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(10.dp).clip(CircleShape).background(Color(android.graphics.Color.parseColor(b.dot))))
                Text(tr(b.lines.getOrElse(0) { "" }), color = o.dim, fontSize = 14.sp, modifier = Modifier.padding(start = 8.dp))
            }
            if (b.lines.size > 1) Text(tr(b.lines[1]), color = o.text, fontSize = 28.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(vertical = 6.dp))
            for (x in b.lines.drop(2)) Text(if (x.firstOrNull()?.isDigit() == true) x else tr(x), color = o.accent, fontSize = 14.sp, fontWeight = FontWeight.Bold)
        }
        is ShareRows -> Column {
            Text(tr(b.title), color = o.text, fontWeight = FontWeight.Bold, fontSize = 14.sp)
            Column(Modifier.fillMaxWidth().padding(top = 4.dp).clip(RoundedCornerShape(8.dp)).background(o.card).padding(10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                for (r in b.rows) Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(tr(r.left), color = o.text, fontSize = 13.sp, modifier = Modifier.weight(if (r.frac == null) 0.35f else 0.4f))
                    if (r.frac != null) Box(Modifier.weight(0.4f).padding(horizontal = 4.dp)) {
                        Box(Modifier.fillMaxWidth(r.frac.toFloat().coerceIn(0.02f, 1f)).height(8.dp).clip(RoundedCornerShape(4.dp)).background(o.accent))
                    }
                    Text(tr(r.right), color = o.dim, fontSize = 12.sp, modifier = Modifier.weight(if (r.frac == null) 0.65f else 0.2f))
                }
            }
        }
        is ShareCal -> Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            val days = b.days[kind] ?: emptyMap()
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("${b.year}-%02d".format(b.month), color = o.text, fontWeight = FontWeight.Bold, fontSize = 16.sp, modifier = Modifier.weight(1f))
                Text(tr("合计: %.1f h").format(days.values.sum()), color = o.accent, fontSize = 13.sp)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                for ((k, name) in listOf("focus" to "专注", "rest" to "休息", "idle" to "空闲", "sleep" to "睡眠"))
                    TextButton({ onKind(k) }) { Text(tr(name), color = if (k == kind) o.accent else o.dim, fontSize = 13.sp) }
            }
            val first = LocalDate.of(b.year, b.month, 1)
            val lead = first.dayOfWeek.value - 1
            val total = first.lengthOfMonth()
            val cells = (0 until lead).map { 0 } + (1..total).toList()
            for (week in cells.chunked(7)) Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                for (i in 0 until 7) {
                    val day = week.getOrElse(i) { 0 }
                    val ds = "${b.year}-%02d-%02d".format(b.month, day)
                    val h = days[ds] ?: 0.0
                    Box(Modifier.weight(1f).height(44.dp).clip(RoundedCornerShape(4.dp))
                        .background(if (day == 0) Color.Transparent else if (ds == b.today) o.accent else o.card), contentAlignment = Alignment.Center) {
                        if (day != 0) Text(if (h > 0) "$day\n%.1fh".format(h) else "$day", fontSize = 11.sp,
                            color = if (ds == b.today) o.onAccent else o.text)
                    }
                }
            }
        }
    }
}
