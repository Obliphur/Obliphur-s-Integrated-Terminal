package com.obliphur.oit.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.obliphur.oit.core.tr

@Composable
fun OitCard(modifier: Modifier = Modifier, border: Color? = null, bg: Color? = null, content: @Composable ColumnScope.() -> Unit) {
    val o = LocalOit.current
    Column(
        modifier.fillMaxWidth()
            .background(bg ?: o.card, RoundedCornerShape(10.dp))
            .border(1.dp, border ?: o.border, RoundedCornerShape(10.dp))
            .padding(horizontal = 16.dp, vertical = 14.dp),
        content = content,
    )
}

@Composable
fun Dot(color: Color) = Box(Modifier.size(10.dp).background(color, CircleShape))

@Composable
fun StatusRow(text: String, color: Color) {
    val o = LocalOit.current
    Row(verticalAlignment = Alignment.CenterVertically) {
        Dot(color)
        Text(text, color = o.dim, fontSize = 14.sp, modifier = Modifier.padding(start = 8.dp))
    }
}

@Composable
fun BigText(text: String) =
    Text(text, color = LocalOit.current.text, fontSize = 40.sp, fontWeight = FontWeight.Bold,
        modifier = Modifier.padding(vertical = 4.dp))

@Composable
fun Dim(text: String, modifier: Modifier = Modifier, size: Int = 14, color: Color? = null) {
    if (text.isNotEmpty()) Text(text, color = color ?: LocalOit.current.dim, fontSize = size.sp, modifier = modifier)
}

@Composable
fun Heading(text: String, modifier: Modifier = Modifier) =
    Text(text, color = LocalOit.current.text, fontWeight = FontWeight.Bold, fontSize = 16.sp,
        modifier = modifier.padding(top = 14.dp, bottom = 6.dp))

/** 作者写的风味文本: 左边带一道细竖线. */
@Composable
fun Flavor(text: String, modifier: Modifier = Modifier) {
    if (com.obliphur.oit.core.I18n.noFlavor) return          // 都市之意不显示风味文本
    val o = LocalOit.current
    Row(modifier.fillMaxWidth().height(IntrinsicSize.Min).padding(vertical = 8.dp)) {
        Box(Modifier.width(2.dp).fillMaxHeight().background(o.border))
        Text(text, color = o.dim, fontSize = 13.sp, lineHeight = 19.sp, modifier = Modifier.padding(start = 10.dp))
    }
}

@Composable
fun AccentButton(text: String, enabled: Boolean = true, modifier: Modifier = Modifier, onClick: () -> Unit) {
    val o = LocalOit.current
    Button(onClick, enabled = enabled, modifier = modifier, shape = RoundedCornerShape(8.dp),
        colors = ButtonDefaults.buttonColors(containerColor = o.accent, contentColor = o.onAccent)) { Text(text) }
}

@Composable
fun DangerButton(text: String, enabled: Boolean = true, modifier: Modifier = Modifier, onClick: () -> Unit) {
    val o = LocalOit.current
    Button(onClick, enabled = enabled, modifier = modifier, shape = RoundedCornerShape(8.dp),
        colors = ButtonDefaults.buttonColors(containerColor = o.danger, contentColor = o.onAccent)) { Text(text) }
}

@Composable
fun PlainButton(text: String, enabled: Boolean = true, modifier: Modifier = Modifier, onClick: () -> Unit) {
    val o = LocalOit.current
    OutlinedButton(onClick, enabled = enabled, modifier = modifier, shape = RoundedCornerShape(8.dp),
        border = BorderStroke(1.dp, o.border)) { Text(text, color = if (enabled) o.text else o.dim) }
}

/** 一组互斥的选项 (分段按钮). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun Segmented(options: List<Pair<String, String>>, selected: String, onSelect: (String) -> Unit) {
    val o = LocalOit.current
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        for ((value, label) in options) {
            val on = value == selected
            Text(label, color = if (on) o.onAccent else o.text, fontSize = 13.sp, textAlign = TextAlign.Center,
                modifier = Modifier
                    .background(if (on) o.accent else o.bg, RoundedCornerShape(6.dp))
                    .border(1.dp, if (on) o.accent else o.border, RoundedCornerShape(6.dp))
                    .clickable { onSelect(value) }
                    .padding(horizontal = 12.dp, vertical = 7.dp))
        }
    }
}

/** 确认框. danger=true 时确认按钮是红色的. */
@Composable
fun Confirm(title: String, text: String, danger: Boolean = false, onDismiss: () -> Unit, onOk: () -> Unit) {
    val o = LocalOit.current
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { Text(text) },
        confirmButton = {
            TextButton({ onDismiss(); onOk() }) { Text(tr("确定"), color = if (danger) o.danger else o.accent) }
        },
        dismissButton = { TextButton(onDismiss) { Text(tr("取消"), color = o.dim) } },
        containerColor = o.bg,
    )
}

@Composable
fun PageColumn(content: @Composable ColumnScope.() -> Unit) =
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp), content = content)
