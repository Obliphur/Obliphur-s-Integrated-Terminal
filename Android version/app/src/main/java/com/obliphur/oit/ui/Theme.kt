package com.obliphur.oit.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.material3.ProvideTextStyle
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.Density

/** 一整套颜色 (同桌面版 config.THEMES). 程序各处只从这里取色. */
data class OitColors(
    val dark: Boolean,
    val bg: Color, val card: Color, val accent: Color, val text: Color, val dim: Color, val strong: Color,
    val success: Color, val warn: Color, val border: Color, val danger: Color, val dangerBg: Color,
    val onAccent: Color, val chartOther: Color, val chartEmpty: Color, val chartFuture: Color,
    val focusStudy: Color, val focusBreak: Color, val chart: List<Color>,
)

private fun c(hex: String) = Color(android.graphics.Color.parseColor(hex))

val THEMES = mapOf(
    "classic" to OitColors(false,
        c("#ffffff"), c("#f8f9fa"), c("#2563eb"), c("#1e293b"), c("#64748b"), c("#0f172a"),
        c("#16a34a"), c("#d97706"), c("#e2e8f0"), c("#dc2626"), c("#fef2f2"),
        c("#ffffff"), c("#94a3b8"), c("#e2e8f0"), c("#f1f5f9"), c("#2563eb"), c("#16a34a"),
        listOf("#2563eb", "#16a34a", "#d97706", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#65a30d", "#ea580c").map(::c)),
    // 参考 Claude 的浅色界面: 通体米白, 卡片只靠细描边区分, 陶土橙强调色
    "claude" to OitColors(false,
        c("#faf9f5"), c("#faf9f5"), c("#c96442"), c("#141413"), c("#73726c"), c("#141413"),
        c("#5b7f4a"), c("#b8741a"), c("#e6e3d8"), c("#b5412f"), c("#f8ebe5"),
        c("#ffffff"), c("#b3b0a6"), c("#e6e3d9"), c("#f4f2eb"), c("#c96442"), c("#788c5d"),
        listOf("#c96442", "#6a9bcc", "#788c5d", "#d4a27f", "#9b7bb8", "#4a8f86", "#b5577a", "#a68a3e", "#5e6b8a").map(::c)),
    // 参考 Claude 的深色界面: 炭灰底色, 亮一点的陶土橙
    "claude_dark" to OitColors(true,
        c("#262624"), c("#30302e"), c("#d97757"), c("#f5f4ee"), c("#a3a199"), c("#ffffff"),
        c("#8fb47a"), c("#e0a458"), c("#43423d"), c("#e5705e"), c("#3a2925"),
        c("#ffffff"), c("#77756e"), c("#3d3c38"), c("#2d2d2b"), c("#d97757"), c("#8fa673"),
        listOf("#d97757", "#7eaedb", "#8fa673", "#ddb28f", "#ad8fca", "#5fa89e", "#cc6f91", "#c2a457", "#8090b5").map(::c)),
    // 都市之意 (同桌面版 themes/urban_will): 全黑底色, 不画卡片边框; 独占「指令」页
    "urban_will" to OitColors(true,
        c("#000000"), c("#000000"), c("#a5f3ff"), c("#e8f7f6"), c("#6f8f8c"), c("#ffffff"),
        c("#7ee0b8"), c("#e7c082"), c("#0b1b1d"), c("#ff5c72"), c("#000000"),
        c("#000000"), c("#3f5b5a"), c("#0a1414"), c("#050909"), c("#a5f3ff"), c("#7ee0b8"),
        listOf("#a5f3ff", "#7ee0b8", "#e7c082", "#ff7a8c", "#8fa8ff", "#63d4d4", "#d39cff", "#b7e36b", "#ffa45c").map(::c)),
)

val LocalOit = staticCompositionLocalOf { THEMES["classic"]!! }
/** 状态版本号: 读它的界面会随 Core 的每次变化刷新. */
val LocalTick = compositionLocalOf { 0L }

@Composable
fun OitTheme(theme: String, fontScale: Float, content: @Composable () -> Unit) {
    val o = THEMES[theme] ?: THEMES["classic"]!!
    val scheme = if (o.dark) darkColorScheme(
        primary = o.accent, onPrimary = o.onAccent, background = o.bg, onBackground = o.text,
        surface = o.bg, onSurface = o.text, surfaceVariant = o.card, onSurfaceVariant = o.dim,
        outline = o.border, error = o.danger, surfaceContainer = o.card, surfaceContainerHigh = o.card,
        secondaryContainer = o.accent.copy(alpha = 0.25f), onSecondaryContainer = o.text,
    ) else lightColorScheme(
        primary = o.accent, onPrimary = o.onAccent, background = o.bg, onBackground = o.text,
        surface = o.bg, onSurface = o.text, surfaceVariant = o.card, onSurfaceVariant = o.dim,
        outline = o.border, error = o.danger, surfaceContainer = o.card, surfaceContainerHigh = o.bg,
        secondaryContainer = o.accent.copy(alpha = 0.15f), onSecondaryContainer = o.text,
    )
    val d = LocalDensity.current
    CompositionLocalProvider(LocalOit provides o, LocalDensity provides Density(d.density, d.fontScale * fontScale)) {
        // 都市之意: 字体下载好之后整个界面换成像素字体 (没下载好之前用系统字体)
        val pixel = if (theme == "urban_will") PixelFont.family.value else null
        MaterialTheme(colorScheme = scheme) {
            if (pixel != null) ProvideTextStyle(TextStyle(fontFamily = pixel), content) else content()
        }
    }
}
