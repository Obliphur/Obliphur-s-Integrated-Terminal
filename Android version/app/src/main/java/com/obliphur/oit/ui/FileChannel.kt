package com.obliphur.oit.ui

import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.foundation.layout.Row
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.obliphur.oit.OitApp
import com.obliphur.oit.core.tr

/** 文件 / 图片的大小写成 KB / MB / GB. 同 pages/file_channel.py 的 fmt_size. */
fun fmtSize(n: Long): String {
    var v = n.toDouble()
    for (unit in listOf("B", "KB", "MB")) {
        if (v < 1024) return if (unit == "B") "${v.toLong()} B" else "%.1f %s".format(v, unit)
        v /= 1024
    }
    return "%.2f GB".format(v)
}

private val WHY = mapOf(
    "old" to "对方的 OIT 版本太旧, 收不了文件.",
    "size" to "文件太大 (最多 1 GB).",
    "unpaired" to "已经不是绑定的设备了.",
    "unsupported" to "对方收不了文件.",
    "busy" to "对方正忙, 稍后再试.",
)

/** 联网页的"📁 文件通道" (MECHANICS 15.6): 选一台绑定的设备和一个文件 / 图片发过去. */
@Composable
fun FileChannelButton() {
    val ctx = LocalContext.current
    val app = OitApp.instance
    var open by remember { mutableStateOf(false) }
    var target by remember { mutableStateOf("") }
    var file by remember { mutableStateOf<Triple<Uri, String, Long>?>(null) }
    var busy by remember { mutableStateOf(false) }
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        // 对方可能过一会儿才来取: 留住读取权限
        runCatching { ctx.contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION) }
        var name = "file"
        var size = -1L
        ctx.contentResolver.query(uri, null, null, null, null)?.use { c ->
            if (c.moveToFirst()) {
                c.getColumnIndex(OpenableColumns.DISPLAY_NAME).takeIf { it >= 0 }?.let { name = c.getString(it) ?: name }
                c.getColumnIndex(OpenableColumns.SIZE).takeIf { it >= 0 && !c.isNull(it) }?.let { size = c.getLong(it) }
            }
        }
        file = Triple(uri, name, size)
    }
    PlainButton(tr("📁  文件通道")) {
        val s = app.sync
        if (s == null || s.deviceStates().isEmpty()) {
            app.core.toast(tr("还没有绑定的设备 (设置 → 基本设置 → 设备同步)."), "info")
        } else {
            target = target.ifEmpty { s.deviceStates().first().id }
            open = true
        }
    }
    if (!open) return
    val devices = app.sync?.deviceStates() ?: emptyList()
    AlertDialog(onDismissRequest = { open = false },
        title = { Text(tr("📁  文件通道")) },
        text = {
            Column {
                Dim(tr("发给"), size = 13)
                for (d in devices) Row(verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.clickable { target = d.id }) {
                    RadioButton(target == d.id, { target = d.id })
                    Text(d.name + if (d.online) "" else "  · " + tr("离线"))
                }
                Dim(tr("文件 / 图片"), size = 13, modifier = Modifier.padding(top = 10.dp))
                Text(file?.let { "${it.second}  (${if (it.third >= 0) fmtSize(it.third) else "?"})" } ?: tr("还没有选择"))
                PlainButton(tr("选择文件…"), modifier = Modifier.padding(top = 6.dp)) { pick.launch(arrayOf("*/*")) }
            }
        },
        confirmButton = {
            TextButton({
                val f = file
                val s = app.sync
                if (f == null || s == null) {       // 大小未知 (f.third < 0) 也能发: 真正大小按读到的字节算
                    app.core.toast(tr("先选择一个文件."), "warn"); return@TextButton
                }
                val who = devices.firstOrNull { it.id == target }?.name ?: "?"
                busy = true
                Thread {
                    val result = runCatching {
                        s.sendFile(target, f.second, f.third) { ctx.contentResolver.openInputStream(f.first)!! }
                    }
                    app.main {
                        busy = false
                        result.onSuccess { why ->
                            if (why == "sent") app.core.toast(tr("已发给 {who}: {name}. 等对方接收.", "who" to who, "name" to f.second), "success")
                            else app.core.toast(tr(WHY[why] ?: "对方没有接收."), "warn")
                        }.onFailure { e ->
                            app.core.toast(tr("发送失败: {e}", "e" to (e.message ?: e.javaClass.simpleName)), "warn")
                        }
                    }
                }.start()
            }, enabled = !busy) { Text(tr("发送")) }
        },
        dismissButton = { TextButton({ open = false }) { Text(tr("关闭")) } })
}
