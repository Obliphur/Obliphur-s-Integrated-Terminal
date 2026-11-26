package com.obliphur.oit.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.obliphur.oit.core.Comm
import com.obliphur.oit.core.PersonaAuthor
import com.obliphur.oit.core.tr
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.IOException
import kotlin.math.roundToInt

@Composable
internal fun PersonaProfileDialog(profile: String, onDismiss: () -> Unit) {
    AlertDialog(onDismissRequest = onDismiss, title = { Text(tr("人设简介")) },
        text = {
            SelectionContainer(Modifier.verticalScroll(rememberScrollState())) {
                Text(profile.ifBlank { tr("当前人设还没有简介。") })
            }
        },
        confirmButton = { TextButton(onDismiss) { Text(tr("关闭")) } })
}

@Composable
internal fun PersonaAuthorDialog(comm: Comm, onDismiss: () -> Unit, onSave: (String) -> Unit) {
    val scope = rememberCoroutineScope()
    var title by remember { mutableStateOf("") }
    var request by remember { mutableStateOf("") }
    var profile by remember { mutableStateOf("") }
    var intimacyLevel by remember { mutableIntStateOf(PersonaAuthor.DEFAULT_INTIMACY_LEVEL) }
    var consentMode by remember { mutableStateOf(false) }
    var draft by remember { mutableStateOf<PersonaAuthor.Draft?>(null) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }
    val preview = draft
    val intimacyInfo = PersonaAuthor.intimacyLevelInfo(intimacyLevel)
    val consentAvailable = intimacyInfo.level in PersonaAuthor.CONSENT_MODE_LEVELS
    AlertDialog(onDismissRequest = onDismiss, title = { Text(tr("创建人设")) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (preview == null) {
                    OutlinedTextField(title, { title = it }, Modifier.fillMaxWidth(),
                        label = { Text(tr("人设名称")) }, singleLine = true, enabled = !busy)
                    Surface(
                        shape = MaterialTheme.shapes.small,
                        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Text(
                                tr("亲密动作最高开放程度（V+ / BURST / 确立关系后）"),
                                style = MaterialTheme.typography.labelLarge,
                            )
                            Text(
                                tr(intimacyInfo.title),
                                style = MaterialTheme.typography.bodyMedium,
                                fontWeight = FontWeight.SemiBold,
                                color = MaterialTheme.colorScheme.primary,
                            )
                            Slider(
                                value = intimacyLevel.toFloat(),
                                onValueChange = {
                                    val next = it.roundToInt().coerceIn(1, PersonaAuthor.INTIMACY_LEVELS.size)
                                    intimacyLevel = next
                                    if (next !in PersonaAuthor.CONSENT_MODE_LEVELS) consentMode = false
                                },
                                valueRange = 1f..PersonaAuthor.INTIMACY_LEVELS.size.toFloat(),
                                steps = PersonaAuthor.INTIMACY_LEVELS.size - 2,
                                enabled = !busy,
                            )
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                                PersonaAuthor.INTIMACY_LEVELS.forEach { item ->
                                    Text(
                                        item.level.toString(),
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                            }
                            Text(tr(intimacyInfo.description), style = MaterialTheme.typography.bodySmall)
                            Text(
                                tr(intimacyInfo.modelFit),
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Text(
                                tr(PersonaAuthor.INTIMACY_MODEL_SUMMARY),
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                Checkbox(
                                    checked = consentAvailable && consentMode,
                                    onCheckedChange = { consentMode = it },
                                    enabled = !busy && consentAvailable,
                                )
                                Text(
                                    tr(PersonaAuthor.CONSENT_MODE_LABEL),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = if (consentAvailable) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            Text(
                                tr(PersonaAuthor.CONSENT_MODE_HINT),
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                    OutlinedTextField(request, { request = it }, Modifier.fillMaxWidth(),
                        label = { Text(tr("创作要求")) }, minLines = 3, maxLines = 6, enabled = !busy)
                    OutlinedTextField(profile, { profile = it }, Modifier.fillMaxWidth(),
                        label = { Text(tr("人设简介（可手写，留空由 AI 生成）")) }, minLines = 2, maxLines = 5, enabled = !busy)
                } else {
                    OutlinedTextField(preview.persona, { draft = preview.copy(persona = it) }, Modifier.fillMaxWidth(),
                        label = { Text(tr("人设正文")) }, minLines = 4, maxLines = 8)
                    OutlinedTextField(preview.templates, { draft = preview.copy(templates = it) }, Modifier.fillMaxWidth(),
                        label = { Text(tr("配套模板（可选）")) }, minLines = 2, maxLines = 5)
                    OutlinedTextField(preview.profile, { draft = preview.copy(profile = it) }, Modifier.fillMaxWidth(),
                        label = { Text(tr("人设简介")) }, minLines = 2, maxLines = 5)
                }
                if (busy) Text(tr("正在生成人设草稿…"))
                if (error.isNotEmpty()) Text(tr(error))
            }
        },
        dismissButton = { TextButton(onDismiss) { Text(tr("取消")) } },
        confirmButton = {
            TextButton(enabled = !busy, onClick = {
                if (preview != null) {
                    runCatching { comm.saveNewPersona(title, preview) }.onSuccess(onSave).onFailure {
                        error = if (it is IOException) "保存失败." else it.message.orEmpty()
                    }
                } else {
                    val name = title.trim()
                    val requirements = request.trim()
                    val manualProfile = profile
                    val chosenIntimacy = intimacyLevel
                    val chosenConsent = consentAvailable && consentMode
                    val problem = runCatching {
                        comm.checkNewPersonaName(name)
                        require(requirements.isNotEmpty()) { "请填写创作要求。" }
                    }.exceptionOrNull()
                    error = problem?.message.orEmpty()
                    if (problem == null) {
                        busy = true
                        scope.launch {
                            val result = withContext(Dispatchers.IO) {
                                runCatching {
                                    PersonaAuthor.generate(
                                        comm.aiConfig().profile(), name, requirements, manualProfile,
                                        chosenIntimacy, chosenConsent,
                                    )
                                }
                            }
                            busy = false
                            result.onSuccess { draft = it }.onFailure { error = it.message.orEmpty() }
                        }
                    }
                }
            }) { Text(tr(if (preview == null) "生成草稿" else "保存")) }
        })
}
