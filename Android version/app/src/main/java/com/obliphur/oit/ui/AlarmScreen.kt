package com.obliphur.oit.ui

import android.app.Activity
import android.app.TimePickerDialog
import android.content.Intent
import android.media.RingtoneManager
import android.net.Uri
import android.os.Build
import android.provider.Settings as SysSettings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.obliphur.oit.OitApp
import com.obliphur.oit.core.AlarmStore
import com.obliphur.oit.core.tr
import com.obliphur.oit.service.AlarmSound
import java.time.LocalDateTime

// ════════════════ 闹钟 (1.0.359, MECHANICS 25) ════════════════

@Composable
private fun AlarmSwitch(label: String, value: Boolean, onChange: (Boolean) -> Unit) {
    val o = LocalOit.current
    Row(Modifier.fillMaxWidth().padding(top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = o.text, modifier = Modifier.weight(1f))
        Switch(value, onChange, colors = SwitchDefaults.colors(checkedTrackColor = o.accent, checkedThumbColor = o.onAccent))
    }
}

private const val ALARM_FILE_MAX_MB = 25

/** 把用户选的音频文件复制进应用私有目录: 成功 = (复制后的路径, 原文件名); 太大 / 读不出 / 不是能播放的音频 = null. */
private fun importAlarmFile(ctx: android.content.Context, uri: Uri): Pair<String, String>? {
    var name = ""
    runCatching {
        ctx.contentResolver.query(uri, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) name = c.getString(0) ?: ""
        }
    }
    val ext = name.substringAfterLast('.', "").lowercase().takeIf { it.length in 1..5 && it.all { ch -> ch.isLetterOrDigit() } } ?: "audio"
    val dst = java.io.File(ctx.filesDir, "alarm_sound_${System.currentTimeMillis()}.$ext")
    try {
        var total = 0L
        val limit = ALARM_FILE_MAX_MB * 1024L * 1024L
        ctx.contentResolver.openInputStream(uri)?.use { ins ->
            dst.outputStream().use { out ->
                val buf = ByteArray(64 * 1024)
                while (true) {
                    val n = ins.read(buf)
                    if (n < 0) break
                    total += n
                    if (total > limit) throw java.io.IOException("too big")
                    out.write(buf, 0, n)
                }
            }
        } ?: throw java.io.IOException("cannot open")
        val r = android.media.MediaMetadataRetriever()
        try {
            r.setDataSource(dst.path)
            if (r.extractMetadata(android.media.MediaMetadataRetriever.METADATA_KEY_HAS_AUDIO) == null) throw java.io.IOException("no audio")
        } finally { runCatching { r.release() } }
        return dst.path to name.ifEmpty { dst.name }
    } catch (e: Exception) {
        runCatching { dst.delete() }
        return null
    }
}

/** 设置 → 闹钟: 开关 + 时间, 铃声 (+ 试听), 音量. 即时保存, 没有等待期. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun AlarmSettings() {
    LocalTick.current
    val o = LocalOit.current
    val ctx = LocalContext.current
    val core = OitApp.instance.core
    val a = core.alarm
    val comm = core.comm
    val now = LocalDateTime.now()
    val coFn = comm.coAlarmFn()
    var vol by remember { mutableFloatStateOf(a.volume.toFloat()) }

    val pick = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        if (r.resultCode == Activity.RESULT_OK) {
            val uri: Uri? = if (Build.VERSION.SDK_INT >= 33) r.data?.getParcelableExtra(RingtoneManager.EXTRA_RINGTONE_PICKED_URI, Uri::class.java)
            else @Suppress("DEPRECATION") r.data?.getParcelableExtra(RingtoneManager.EXTRA_RINGTONE_PICKED_URI)
            a.set(custom = uri?.toString() ?: "", tone = "custom")
        }
    }

    // 从文件里选一个音频当铃声: 复制进应用私有目录 (大小有限制), 能被系统读出时长才算数
    val pickFile = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        Thread {
            val res = importAlarmFile(ctx, uri)
            android.os.Handler(android.os.Looper.getMainLooper()).post {
                if (res == null) core.toast(tr("音频文件无法使用 (要能播放, 且不超过 {n} MB).", "n" to ALARM_FILE_MAX_MB), "warn")
                else {
                    if (a.audio.isNotEmpty() && a.audio != res.first) runCatching { java.io.File(a.audio).delete() }
                    a.set(audio = res.first, audioName = res.second, tone = "file")
                }
            }
        }.apply { isDaemon = true }.start()
    }

    OitCard {
        Text(tr("⏰  闹钟"), fontWeight = FontWeight.Bold, color = o.text)
        AlarmSwitch(tr("开启 (每天重复)"), a.on) { a.set(on = it) }
        Row(Modifier.fillMaxWidth().padding(top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(tr("时间"), color = o.text, modifier = Modifier.weight(1f))
            PlainButton(a.time) {
                val (h, m) = a.hm()
                TimePickerDialog(ctx, { _, hh, mm -> a.set(time = "%d:%02d".format(hh, mm)) }, h, m, true).show()
            }
        }
        if (coFn != null) Dim(tr("共时闹钟已开启: 本机按「{name}」的起床时间响铃 (在 沟通 → ⏰ 里设置), 上面的开关和时间现在不生效; 铃声和音量照这里的设置.",
            "name" to comm.coAlarmName()), size = 13, color = o.accent, modifier = Modifier.padding(top = 4.dp))
        val status = a.statusText(now, coFn, comm.coAlarmName())
        Dim(status.ifEmpty { tr("这个对象的日程里没有起床时间, 共时闹钟不会响.") }, size = 13, modifier = Modifier.padding(top = 4.dp))
        if (Build.VERSION.SDK_INT >= 31 && !ctx.getSystemService(android.app.AlarmManager::class.java).canScheduleExactAlarms()) {
            Dim(tr("未允许精确闹钟: 熄屏后可能不能准点响铃."), size = 13, color = o.warn, modifier = Modifier.padding(top = 4.dp))
            PlainButton(tr("允许精确闹钟")) {
                runCatching { ctx.startActivity(Intent(SysSettings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, Uri.parse("package:${ctx.packageName}")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
            }
        }
    }

    OitCard {
        Text(tr("🔔  铃声"), fontWeight = FontWeight.Bold, color = o.text)
        Column(Modifier.padding(top = 6.dp)) {
            Segmented(AlarmStore.TONES.map { it.first to tr(it.second) }, a.tone) { a.set(tone = it) }
        }
        if (a.tone == "custom") {
            val title = runCatching {
                if (a.custom.isBlank()) null else RingtoneManager.getRingtone(ctx, Uri.parse(a.custom))?.getTitle(ctx)
            }.getOrNull() ?: tr("系统默认闹钟铃声")
            Row(Modifier.fillMaxWidth().padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                Dim(title, size = 13, modifier = Modifier.weight(1f))
                PlainButton(tr("选择铃声…")) {
                    pick.launch(Intent(RingtoneManager.ACTION_RINGTONE_PICKER)
                        .putExtra(RingtoneManager.EXTRA_RINGTONE_TYPE, RingtoneManager.TYPE_ALARM)
                        .putExtra(RingtoneManager.EXTRA_RINGTONE_SHOW_DEFAULT, true)
                        .putExtra(RingtoneManager.EXTRA_RINGTONE_EXISTING_URI, if (a.custom.isBlank()) null else Uri.parse(a.custom)))
                }
            }
        }
        if (a.tone == "file") {
            val ok = a.audio.isNotEmpty() && java.io.File(a.audio).isFile
            Row(Modifier.fillMaxWidth().padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                Dim(if (ok) a.audioName.ifEmpty { java.io.File(a.audio).name } else tr("还没有选择文件 (现在响经典警报)"), size = 13, modifier = Modifier.weight(1f))
                PlainButton(tr("选择音频文件…")) { pickFile.launch(arrayOf("audio/*")) }
            }
        }
        Row(Modifier.fillMaxWidth().padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Dim(tr("铃声会循环播放, 直到你把它关掉 (最多 5 分钟). 睡眠中也会响."), size = 12, modifier = Modifier.weight(1f))
            PlainButton(if (AlarmSound.playing) tr("■ 停止") else tr("▶ 试听"), enabled = !core.alarmRinging()) {
                if (AlarmSound.playing) AlarmSound.stop(ctx) else AlarmSound.start(ctx, a.tone, a.soundArg(), a.volume, AlarmStore.PREVIEW_SECONDS)
            }
        }
    }

    OitCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(tr("🔊  音量"), fontWeight = FontWeight.Bold, color = o.text, modifier = Modifier.weight(1f))
            Text("${vol.toInt()}%", color = o.text)
        }
        Slider(vol, { vol = it }, valueRange = 0f..100f, onValueChangeFinished = { a.set(volume = vol.toInt()) },
            colors = SliderDefaults.colors(thumbColor = o.accent, activeTrackColor = o.accent))
        Dim(tr("响铃时会把手机的闹钟音量拉到最大, 这里的音量是在此基础上的比例; 响完恢复."), size = 12)
    }
}

/** 沟通页 ⏰: 把现在聊的对象设为共时闹钟 (一次只有一个), 手动选哪些设备响铃. 改了就生效, 随设备同步. */
@Composable
fun CoAlarmDialog(onDismiss: () -> Unit) {
    LocalTick.current
    val o = LocalOit.current
    val core = OitApp.instance.core
    val comm = core.comm
    val pid = comm.pid
    val name = comm.personaTitle(comm.personaName())
    val on = comm.coAlarmPid().isNotEmpty() && comm.coAlarmPid() == pid
    val me = comm.device()
    val devices = comm.devices().ifEmpty { if (me.isEmpty()) emptyList() else listOf(me to tr("本机")) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(tr("共时闹钟")) },
        text = {
            Column {
                Dim(tr("开启后, 闹钟的时间跟随这个对象的日程里的实际起床时间 (睡眠中也会响). 响铃用「设置 → 闹钟」里的铃声和音量; 哪些设备响, 在下面手动选."), size = 12)
                Row(Modifier.fillMaxWidth().padding(top = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(tr("把「{name}」设为共时闹钟", "name" to name), color = o.text, fontWeight = FontWeight.Bold, fontSize = 14.sp, modifier = Modifier.weight(1f))
                    Switch(on, { comm.setCoAlarm(if (it) pid else "") }, enabled = pid.isNotEmpty(),
                        colors = SwitchDefaults.colors(checkedTrackColor = o.accent, checkedThumbColor = o.onAccent))
                }
                if (comm.coAlarmPid().isNotEmpty() && !on)
                    Dim(tr("现在的共时闹钟是「{name}」, 打开会换成这个对象.", "name" to comm.coAlarmName()), size = 12, color = o.warn)
                if (on) {
                    Text(tr("响铃的设备"), color = o.text, fontWeight = FontWeight.Bold, fontSize = 14.sp, modifier = Modifier.padding(top = 10.dp))
                    val chosen = comm.coAlarmDevs()
                    for ((id, dn) in devices) Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(id in chosen, { c -> comm.setCoAlarmDevs(if (c) chosen + id else chosen - id) },
                            colors = CheckboxDefaults.colors(checkedColor = o.accent, checkmarkColor = o.onAccent))
                        Text(dn + if (id == me) " (" + tr("本机") + ")" else "", color = o.text)
                    }
                    val t = comm.wakeAfter(pid, LocalDateTime.now())
                    val line = when {
                        t == null -> tr("这个对象的日程里没有起床时间 (没有日程文件, 也没有睡眠段), 闹钟不会响.")
                        chosen.isEmpty() -> tr("没有选响铃的设备: 不会响.")
                        else -> tr("下一次起床: {day} {t}", "day" to tr(AlarmStore.dayWord(t, LocalDateTime.now())), "t" to "%02d:%02d".format(t.hour, t.minute))
                    }
                    Dim(line, size = 13, color = o.accent, modifier = Modifier.padding(top = 8.dp))
                }
            }
        },
        confirmButton = { TextButton(onDismiss) { Text(tr("关闭"), color = o.accent) } },
        containerColor = o.bg,
    )
}
