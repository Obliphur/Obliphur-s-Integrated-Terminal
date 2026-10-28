package com.obliphur.oit.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.text.font.FontWeight
import com.obliphur.oit.OitApp
import com.obliphur.oit.core.AchievementStore
import com.obliphur.oit.core.Fmt
import com.obliphur.oit.core.tr

// ════════════════ 成就 (MECHANICS 17): 单独一页, 从 设置 → 基本设置 最上面的按钮打开 ════════════════

/** 每个成就一张卡片. 没达成的只显示名字 (隐藏成就连名字也不显示), 不显示进度和条件. */
@Composable
fun AchievementScreen(onBack: () -> Unit) {
    LocalTick.current
    val o = LocalOit.current
    val ach = OitApp.instance.core.ach
    PageColumn {
        PlainButton(tr("← 返回设置"), onClick = onBack)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(tr("🏆  成就"), color = o.text, fontWeight = FontWeight.Bold, fontSize = 16.sp, modifier = Modifier.weight(1f))
            Text(tr("已达成 {n}", "n" to ach.unlocked.size), color = o.accent, fontWeight = FontWeight.Bold, fontSize = 14.sp)
        }
        for (a in AchievementStore.ALL) {
            val got = ach.unlocked[a.id]
            OitCard {
                Row(verticalAlignment = Alignment.Top) {
                    Text(com.obliphur.oit.core.I18n.ic(if (got != null) "🏆" else "🔒"), fontSize = 22.sp, color = if (got != null) o.accent else o.dim,
                        modifier = Modifier.padding(end = 12.dp))
                    Column(Modifier.weight(1f)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(if (got == null && a.hidden) "???" else AchievementStore.name(a),
                                color = if (got != null) o.text else o.dim, fontWeight = FontWeight.Bold, fontSize = 16.sp,
                                modifier = Modifier.weight(1f))
                            if (got != null) Dim(got.format(Fmt.DAY), size = 12)
                        }
                        if (got != null) {
                            Text(AchievementStore.text(a), color = o.text, fontSize = 14.sp, modifier = Modifier.padding(top = 4.dp))
                            if (a.note.isNotEmpty()) Flavor(tr(a.note))
                        }
                    }
                }
            }
        }
    }
}

/** 达成提示: 浮在界面上方, 只能手动关闭 (✕); 点提示本身打开成就页 (同时关掉它). 同时最多显示 3 个, 其余排队. */
@Composable
fun AchievementPopups(onOpen: () -> Unit) {
    LocalTick.current
    val o = LocalOit.current
    val core = OitApp.instance.core
    // 合约的黑雾 (MECHANICS 18.3): 达成提示等合约结束后再弹出
    val ids = if (core.contract.fog()) emptyList() else core.ach.unseen.take(3)
    if (ids.isEmpty()) return
    Box(Modifier.fillMaxSize().statusBarsPadding().padding(top = 60.dp, end = 12.dp, start = 12.dp),
        contentAlignment = Alignment.TopEnd) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp), horizontalAlignment = Alignment.End) {
            for (id in ids) {
                val a = AchievementStore.BY_ID[id] ?: continue
                Row(Modifier.widthIn(max = 340.dp).fillMaxWidth()
                    .background(o.card, RoundedCornerShape(10.dp))
                    .border(2.dp, o.accent, RoundedCornerShape(10.dp))
                    .clickable { core.closeAchievement(id); onOpen() }
                    .padding(start = 14.dp, top = 8.dp, bottom = 12.dp)) {
                    Column(Modifier.weight(1f)) {
                        Text(tr("🏆  达成成就"), color = o.accent, fontWeight = FontWeight.Bold, fontSize = 12.sp)
                        Text(AchievementStore.name(a), color = o.text, fontWeight = FontWeight.Bold, fontSize = 17.sp,
                            modifier = Modifier.padding(top = 2.dp))
                        Text(AchievementStore.text(a), color = o.text, fontSize = 13.sp, modifier = Modifier.padding(top = 2.dp))
                    }
                    Text(com.obliphur.oit.core.I18n.ic("✕"), color = o.dim, fontSize = 16.sp,
                        modifier = Modifier.clickable { core.closeAchievement(id) }.padding(horizontal = 12.dp, vertical = 2.dp))
                }
            }
        }
    }
}
