package com.obliphur.oit.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.unit.sp
import com.obliphur.oit.OitApp
import com.obliphur.oit.ai.Ai
import com.obliphur.oit.ai.AiError
import com.obliphur.oit.ai.Profile
import com.obliphur.oit.core.tr
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 选模型: 一个下拉菜单 (当前模型 ▾) 加一个"↻"按钮向服务商取可用的模型列表.
 * 聊天和 AI 名单建议用的是同一份模型设置 (ai_config.json), 在哪里选了, 两边都生效.
 * profile: 现在用的那份模型设置 (取列表用它的地址和 Key); onPick: 选了一个模型.
 */
@Composable
fun ModelPicker(profile: () -> Profile, onPick: (String) -> Unit) {
    val o = LocalOit.current
    val scope = rememberCoroutineScope()
    var open by remember { mutableStateOf(false) }
    var fetching by remember { mutableStateOf(false) }
    var version by remember { mutableStateOf(0) }          // 取到新列表后刷新菜单
    val p = profile()
    val options = Ai.modelsFor(p).also { version }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box {
            TextButton({ open = true }) { Text("▾  " + p.model.ifEmpty { "—" }, color = o.text, fontSize = 13.sp) }
            DropdownMenu(open, { open = false }) {
                Box(androidx.compose.ui.Modifier.verticalScroll(rememberScrollState())) {
                    androidx.compose.foundation.layout.Column {
                        for (m in options) DropdownMenuItem(text = { Text(m, color = if (m == p.model) o.accent else o.text) },
                            onClick = { open = false; onPick(m) })
                    }
                }
            }
        }
        TextButton({
            if (fetching) return@TextButton
            fetching = true
            scope.launch {
                val core = OitApp.instance.core
                try {
                    val ids = withContext(Dispatchers.IO) { Ai.listModels(profile()) }
                    version++
                    core.toast(tr("取到 {n} 个模型.", "n" to ids.size), "success")
                    open = true
                } catch (e: AiError) {
                    core.toast(tr(e.message ?: ""), "warn")
                } catch (e: Exception) {
                    core.toast(e.javaClass.simpleName, "warn")
                } finally {
                    fetching = false
                }
            }
        }, enabled = !fetching) { Text("↻", color = o.dim) }
    }
}
