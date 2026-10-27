package com.obliphur.oit.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.obliphur.oit.OitApp
import com.obliphur.oit.R
import com.obliphur.oit.core.Prescript
import com.obliphur.oit.core.iso
import com.obliphur.oit.core.tr
import kotlinx.coroutines.delay
import java.time.LocalDateTime

private const val SCRAMBLE_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789!@#\$%^&*()_+-=[]{}|;:,.?"
private val CODE_BLUE = Color(0xFFA5F3FF)
private val CODE_GLOW = Color(0xFF98D8F0)

/**
 * 指令页 (都市之意主题独占, MECHANICS 22): 整页黑底, 参照 https://fuming-397.github.io/command-receiver/ ,
 * 居中图标 / 蓝色代码 / 白色正文 / "- 接收指令 -". 不显示任何时间或限时. 左上角一个几乎看不见的 ×, 或按返回键退出.
 */
@Composable
fun PrescriptScreen(onExit: () -> Unit) {
    val core = OitApp.instance.core
    val eng = core.prescript
    val ctx = LocalContext.current
    LocalTick.current                                  // 每秒的刷新: 整点一过按钮就恢复
    BackHandler { onExit() }

    // 这一次打开页面的时刻: 蓝色代码只显示这之后结算出的奖励
    val visitSince = remember { LocalDateTime.now().withNano(0).iso() }
    LaunchedEffect(Unit) { eng.settleRewards() }

    // ---- 目标文字 ----
    val bodies = eng.actives().map { cur ->
        val n = if (cur.isNull("n")) 0 else cur.optInt("n")
        when (cur.optString("type")) {
            "II" -> tr("完成一次至少{n}级的合约.", "n" to n)
            "IV" -> tr("今天专注至少{n} h", "n" to n)
            else -> tr(cur.optString("body"))
        }
    }
    val bodyTarget = if (bodies.isEmpty()) tr("谨遵指令之意") else bodies.joinToString("\n")
    val fresh = eng.grantedToday().filter { it.optString("at") >= visitSince && it.optInt("seconds", 0) >= 180 }
    val codesTarget = fresh.map { Prescript.padCode(it.optString("code")) }.ifEmpty { listOf(Prescript.padCode("NONE")) }

    // ---- 动画: 第一次直接显示, 之后变了才播 ----
    var bodyShown by remember { mutableStateOf(bodyTarget) }
    var codesShown by remember { mutableStateOf(codesTarget) }
    LaunchedEffect(bodyTarget) {
        if (bodyShown == bodyTarget) return@LaunchedEffect
        for (i in 1..bodyTarget.length) {                // 逐字打出来, 每字 50 ms
            bodyShown = bodyTarget.take(i)
            delay(50)
        }
        bodyShown = bodyTarget
    }
    LaunchedEffect(codesTarget) {
        if (codesShown == codesTarget) return@LaunchedEffect
        val longest = codesTarget.maxOf { it.length }
        var done = -1
        var tick = 0
        while (true) {                                    // 乱码 25 ms 刷新一次, 每 100 ms 多定下一个字
            if (tick > 0 && tick % 4 == 0) done++
            if (done >= longest - 1) break
            codesShown = codesTarget.map { t ->
                t.mapIndexed { i, ch -> if (i <= done) ch else SCRAMBLE_CHARS.random() }.joinToString("")
            }
            delay(25)
            tick++
        }
        codesShown = codesTarget
    }
    val busy = bodyShown != bodyTarget || codesShown != codesTarget
    val can = eng.canDraw() && !busy

    Box(Modifier.fillMaxSize().background(Color.Black).navigationBarsPadding()) {
        Column(
            Modifier.align(Alignment.Center).fillMaxWidth(0.85f),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Image(painterResource(R.drawable.prescript_index), contentDescription = null, modifier = Modifier.size(128.dp))
            Spacer(Modifier.height(10.dp))
            for (line in codesShown) {
                Text(
                    line, color = CODE_BLUE, fontSize = 20.sp, fontWeight = FontWeight.Bold,
                    fontFamily = PixelFont.family.value ?: FontFamily.Monospace, textAlign = TextAlign.Center,
                    style = TextStyle(shadow = Shadow(CODE_GLOW, blurRadius = 8f)),
                )
            }
            Text(
                bodyShown, color = Color.White, fontSize = 14.sp, textAlign = TextAlign.Center,
                modifier = Modifier.padding(top = 4.dp, bottom = 18.dp).fillMaxWidth(),
            )
            val src = remember { MutableInteractionSource() }
            val pressed by src.collectIsPressedAsState()
            val color by animateColorAsState(
                if (!can) Color(0xFF444444) else if (pressed) Color.Red else Color.White,
                tween(500), label = "btn",
            )
            Text(
                tr("- 接收指令 -"), color = color, fontSize = 16.sp, textAlign = TextAlign.Center,
                modifier = Modifier.clickable(interactionSource = src, indication = null, enabled = can) {
                    if (eng.draw() != null) playSound(ctx)
                }.padding(12.dp),
            )
        }
        Text(
            "×", color = Color(0xFF2A3535), fontSize = 20.sp,
            modifier = Modifier.align(Alignment.TopStart).clickable { onExit() }.padding(horizontal = 14.dp, vertical = 8.dp),
        )
    }
}

/** 接收指令的 "滴答" 音效 (三个里随机一个). */
private fun playSound(ctx: android.content.Context) {
    try {
        val res = listOf(R.raw.ticktock_type1, R.raw.ticktock_type2, R.raw.ticktock_type3).random()
        val mp = android.media.MediaPlayer.create(ctx, res) ?: return
        mp.setOnCompletionListener { it.release() }
        mp.start()
    } catch (_: Exception) {
    }
}
