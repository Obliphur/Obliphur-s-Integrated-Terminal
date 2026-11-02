package com.obliphur.oit.ui

import android.graphics.Bitmap
import android.graphics.ImageDecoder
import android.net.Uri
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathFillType
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import com.obliphur.oit.core.tr
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

private const val OUT_PX = 256
private const val ZOOM_RANGE = 8f           // 最多放大到刚好盖住圆框的 8 倍

/** 把相册 / 文件管理器里选的图读成不太大的 Bitmap (按 EXIF 转正). */
private fun decode(ctx: android.content.Context, uri: Uri, maxSide: Int): Bitmap? = runCatching {
    ImageDecoder.decodeBitmap(ImageDecoder.createSource(ctx.contentResolver, uri)) { decoder, info, _ ->
        decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
        val big = max(info.size.width, info.size.height)
        if (big > maxSide) decoder.setTargetSize(info.size.width * maxSide / big, info.size.height * maxSide / big)
    }
}.getOrNull()

/** 截取头像: 拖动 / 双指缩放, 圆框里的就是头像. onDone(null) = 取消. */
@Composable
fun AvatarCropDialog(uri: Uri, onDone: (Bitmap?) -> Unit) {
    val o = LocalOit.current
    val ctx = LocalContext.current
    val src = remember(uri) { decode(ctx, uri, 1600) }
    if (src == null) {
        LaunchedEffect(uri) { OitAppToast(tr("打不开这张图片.")); onDone(null) }
        return
    }
    val box = 300.dp
    val boxPx = with(LocalDensity.current) { box.toPx() }
    val r = boxPx * 0.46f
    val minScale = 2f * r / min(src.width, src.height)
    var scale by remember { mutableFloatStateOf(minScale) }
    var cx by remember { mutableFloatStateOf(src.width / 2f) }
    var cy by remember { mutableFloatStateOf(src.height / 2f) }
    val image = remember(src) { src.asImageBitmap() }

    fun clamp() {
        scale = scale.coerceIn(minScale, minScale * ZOOM_RANGE)
        val half = r / scale
        cx = cx.coerceIn(half, src.width - half)
        cy = cy.coerceIn(half, src.height - half)
    }

    AlertDialog(onDismissRequest = { onDone(null) }, containerColor = o.bg,
        title = { Text(tr("截取头像")) },
        text = {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Canvas(Modifier.size(box).pointerInput(Unit) {
                    detectTransformGestures { _, pan, zoom, _ ->
                        scale *= zoom
                        cx -= pan.x / scale
                        cy -= pan.y / scale
                        clamp()
                    }
                }) {
                    drawRect(Color.Black.copy(alpha = 0.08f))
                    withTransform({
                        translate(boxPx / 2 - cx * scale, boxPx / 2 - cy * scale)
                        scale(scale, scale, pivot = Offset.Zero)
                    }) { drawImage(image) }
                    val c = Offset(boxPx / 2, boxPx / 2)
                    val hole = Path().apply {
                        fillType = PathFillType.EvenOdd
                        addRect(Rect(0f, 0f, boxPx, boxPx))
                        addOval(Rect(c.x - r, c.y - r, c.x + r, c.y + r))
                    }
                    drawPath(hole, Color.Black.copy(alpha = 0.55f))
                    drawCircle(Color.White, r, c, style = Stroke(2f))
                }
                Slider(scale, { scale = it; clamp() }, valueRange = minScale..(minScale * ZOOM_RANGE),
                    colors = SliderDefaults.colors(thumbColor = o.accent, activeTrackColor = o.accent),
                    modifier = Modifier.fillMaxWidth())
            }
        },
        confirmButton = {
            TextButton({
                clamp()
                val out = Bitmap.createBitmap(OUT_PX, OUT_PX, Bitmap.Config.ARGB_8888)
                val canvas = android.graphics.Canvas(out)
                canvas.clipPath(android.graphics.Path().apply { addCircle(OUT_PX / 2f, OUT_PX / 2f, OUT_PX / 2f, android.graphics.Path.Direction.CW) })
                val half = r / scale
                val rect = android.graphics.Rect((cx - half).roundToInt(), (cy - half).roundToInt(),
                    (cx + half).roundToInt(), (cy + half).roundToInt())
                canvas.drawBitmap(src, rect, android.graphics.Rect(0, 0, OUT_PX, OUT_PX),
                    android.graphics.Paint(android.graphics.Paint.FILTER_BITMAP_FLAG or android.graphics.Paint.ANTI_ALIAS_FLAG))
                onDone(out)
            }) { Text(tr("确定"), color = o.accent) }
        },
        dismissButton = { TextButton({ onDone(null) }) { Text(tr("取消"), color = o.dim) } })
}

private fun OitAppToast(text: String) = com.obliphur.oit.OitApp.instance.core.toast(text, "warn")
