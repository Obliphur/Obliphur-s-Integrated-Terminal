package com.obliphur.oit.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGesturesAfterLongPress
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInParent
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex
import com.obliphur.oit.core.SidebarOrder
import com.obliphur.oit.core.tr
import kotlin.math.roundToInt

/**
 * Draggable navigation list with a movable divider line.
 * Items placed below the divider are visually dimmed (unless currently selected),
 * matching desktop `app/app_sidebar.py`.
 */
@Composable
fun SidebarNavList(
    pages: List<Pair<String, String>>,
    order: List<String>,
    currentPage: String,
    cp: ContractColors?,
    sideBg: Color,
    navLabel: (String, String) -> String,
    onSelect: (String) -> Unit,
    onReorder: (List<String>) -> Unit,
    onDragStateChanged: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    val o = LocalOit.current
    val haptic = LocalHapticFeedback.current
    val pageMap = remember(pages) { pages.toMap() }
    val visibleOrder = remember(order, pageMap) {
        order.filter { it == SidebarOrder.DIVIDER || it in pageMap }
    }

    var dragKey by remember { mutableStateOf<String?>(null) }
    var dragOffsetY by remember { mutableFloatStateOf(0f) }
    var dropBefore by remember { mutableStateOf<String?>(null) }
    val itemBounds = remember { mutableStateMapOf<String, Pair<Float, Float>>() }
    val scrollState = rememberScrollState()

    val currentOrder by rememberUpdatedState(order)
    val currentVisible by rememberUpdatedState(visibleOrder)
    val currentDropBefore by rememberUpdatedState(dropBefore)
    val currentOnReorder by rememberUpdatedState(onReorder)
    val currentOnDragStateChanged by rememberUpdatedState(onDragStateChanged)

    fun calcDropBefore(key: String, pointerY: Float): String? {
        val others = currentVisible.filter { it != key }
        for (other in others) {
            val (top, height) = itemBounds[other] ?: continue
            if (pointerY < top + height / 2f) return other
        }
        return null
    }

    val dividerColor = cp?.line ?: o.dim
    val indicatorColor = cp?.line ?: o.accent

    Box(modifier.verticalScroll(scrollState, enabled = dragKey == null)) {
        Column(Modifier.fillMaxWidth()) {
            for (key in visibleOrder) {
                val isDragging = dragKey == key
                val isDivider = key == SidebarOrder.DIVIDER
                val dragMod = Modifier
                    .zIndex(if (isDragging) 1f else 0f)
                    .onGloballyPositioned { coords ->
                        itemBounds[key] = coords.positionInParent().y to coords.size.height.toFloat()
                    }
                    .graphicsLayer {
                        if (isDragging) {
                            translationY = dragOffsetY
                            alpha = 0.85f
                        }
                    }
                    .pointerInput(key) {
                        var pointerY = 0f
                        detectDragGesturesAfterLongPress(
                            onDragStart = { offset ->
                                haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                                dragKey = key
                                dragOffsetY = 0f
                                pointerY = (itemBounds[key]?.first ?: 0f) + offset.y
                                dropBefore = calcDropBefore(key, pointerY)
                                currentOnDragStateChanged(true)
                            },
                            onDrag = { change, dragAmount ->
                                change.consume()
                                dragOffsetY += dragAmount.y
                                pointerY += dragAmount.y
                                dropBefore = calcDropBefore(key, pointerY)
                            },
                            onDragEnd = {
                                val next = SidebarOrder.move(currentOrder, key, currentDropBefore)
                                dragKey = null
                                dragOffsetY = 0f
                                dropBefore = null
                                currentOnDragStateChanged(false)
                                if (next != currentOrder) currentOnReorder(next)
                            },
                            onDragCancel = {
                                dragKey = null
                                dragOffsetY = 0f
                                dropBefore = null
                                currentOnDragStateChanged(false)
                            },
                        )
                    }

                if (isDivider) {
                    Box(
                        dragMod
                            .fillMaxWidth()
                            .height(28.dp)
                            .background(sideBg)
                            .then(if (isDragging) Modifier.border(1.dp, indicatorColor) else Modifier),
                        contentAlignment = Alignment.Center,
                    ) {
                        Box(
                            Modifier
                                .fillMaxWidth()
                                .padding(horizontal = 16.dp)
                                .height(2.dp)
                                .background(dividerColor),
                        )
                    }
                } else {
                    val label = pageMap[key] ?: continue
                    val on = key == currentPage || (key == "settings" && currentPage == "ach")
                    val dimmed = SidebarOrder.isDimmed(order, key)
                    val baseFg = if (cp != null) cp.sideText else o.text
                    val normalFg = if (dimmed) lerp(sideBg, baseFg, 0.45f) else baseFg
                    val textColor = if (on) (cp?.startText ?: o.accent) else normalFg
                    val itemBg = if (cp != null && on) cp.sideOn else sideBg

                    // 指令标记: 持有合约 / 统计任务时, 在对应入口上盖一朵几乎看不见的花
                    Box(
                        dragMod
                            .fillMaxWidth()
                            .background(itemBg)
                            .then(if (isDragging) Modifier.border(1.dp, indicatorColor) else Modifier)
                            .clickable(enabled = dragKey == null) { onSelect(key) },
                        contentAlignment = Alignment.CenterStart,
                    ) {
                        Text(
                            navLabel(key, tr(label)),
                            color = textColor,
                            fontSize = 16.sp,
                            fontWeight = if (on) FontWeight.Bold else FontWeight.Normal,
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(horizontal = 24.dp, vertical = 14.dp),
                        )
                        if ((key == "contract" && prescriptMark("contract")) || (key == "stats" && prescriptMark("stats"))) {
                            PrescriptMark(Modifier.align(Alignment.CenterEnd).padding(end = 24.dp))
                        }
                    }
                }
            }
        }

        if (dragKey != null) {
            val others = visibleOrder.filter { it != dragKey }
            val indicatorY = if (dropBefore != null) {
                itemBounds[dropBefore]?.first ?: 0f
            } else {
                others.lastOrNull()?.let { k -> itemBounds[k]?.let { (t, h) -> t + h } } ?: 0f
            }
            Box(
                Modifier
                    .fillMaxWidth()
                    .offset { IntOffset(0, indicatorY.roundToInt()) }
                    .padding(horizontal = 8.dp)
                    .height(2.dp)
                    .background(indicatorColor)
                    .zIndex(2f),
            )
        }
    }
}
