package com.obliphur.oit.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.obliphur.oit.OitApp
import com.obliphur.oit.core.Contract
import com.obliphur.oit.core.f1
import com.obliphur.oit.core.fmtHm
import com.obliphur.oit.core.key
import com.obliphur.oit.core.monthPrefixes
import com.obliphur.oit.core.tallyItems
import com.obliphur.oit.core.tr
import com.obliphur.oit.service.Notifier
import com.obliphur.oit.service.appLabel
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.YearMonth
import kotlin.math.atan2

// ════════════════ Hour Log 页 (MECHANICS 7) ════════════════

/** 黑雾 (MECHANICS 18.3) 时盖住的格子里的假内容: 动词 + 量词 + 名词 拼起来 (中文每个词两个字).
 *  词取自 Minecraft Wiki 的"村民"条目 (每个词池 60 个), PRTS 的"水月"条目 (动词 40, 量词 20, 名词 40)
 *  和脑叶公司 Wiki 的"T-01-31 沉默乐团"条目 (动词 50, 名词 50).
 *  同 hour_log_page.py. */
internal val FOG_ZH_VERBS = listOf(
    "生成", "治愈", "掉落", "繁殖", "交易", "聚集", "认领", "拾取", "分享", "耕作", "注视", "收割", "躲避", "寻找", "攀爬", "打开", "放弃", "绑定",
    "破坏", "传送", "检测", "传播", "合并", "攻击", "装备", "催熟", "播种", "收获", "制作", "检查", "购买", "出售", "庆祝", "发射", "敲响", "观察",
    "追逐", "感染", "转换", "命名", "叫醒", "种植", "投掷", "消耗", "储存", "升级", "伏击", "隐匿", "部署", "阻挡", "回复", "触发", "充能", "束缚",
    "拖拽", "击杀", "歼灭", "进驻", "生产", "编入", "通关", "阻止", "劫掠", "坐视", "探索", "察觉", "索要", "审视", "见证", "怀疑", "延长", "割下",
    "询问", "摒弃", "预见", "滋润", "盘踞", "汲取", "改造", "孕育", "招募", "演奏", "指挥", "突破", "收容", "减少", "免疫", "镇压", "指派", "选定",
    "遮蔽", "覆盖", "遮挡", "锁定", "暂停", "拉开", "推进", "屏蔽", "警告", "清零", "登上", "杀死", "合上", "抵挡", "派遣", "调离", "施加", "呼叫",
    "丢失", "离开", "研发", "挥舞", "淹没", "献上", "构筑", "吸收", "穿戴", "评判", "领导", "协调", "传达", "丧失", "唤醒", "加快", "点燃"
)
internal val FOG_ZH_QUANTS = listOf(
    "一个", "两个", "三个", "五个", "八个", "十个", "几个", "每个", "一块", "三块", "六块", "半块", "一组", "两组", "半组", "每组", "一格", "八格",
    "十格", "一张", "两张", "几张", "每张", "一次", "两次", "几次", "一枚", "十枚", "几枚", "一种", "五种", "七种", "几种", "一条", "两条", "几条",
    "一名", "三名", "五名", "一颗", "九颗", "几颗", "一件", "两件", "几件", "一片", "几片", "一层", "两层", "一级", "五级", "一只", "两只", "几只",
    "一些", "许多", "少量", "多个", "四块", "四组", "一棵", "两棵", "每棵", "一粒", "几粒", "一口", "半口", "三口", "一株", "每株", "两名", "每名",
    "一套", "整套", "一群", "那群", "一帮", "那帮", "三秒", "两阶"
)
internal val FOG_ZH_NOUNS = listOf(
    "村民", "村庄", "职业", "面包", "小麦", "骨粉", "农民", "渔夫", "牧师", "屠夫", "石匠", "皮匠", "傻子", "盔甲", "物品", "方块", "僵尸", "女巫",
    "粒子", "言论", "声望", "折扣", "礼物", "火箭", "房屋", "梯子", "屋顶", "教堂", "码头", "书架", "讲台", "木桶", "高炉", "砂轮", "作物", "耕地",
    "雪屋", "苹果", "南瓜", "徽章", "服装", "袍子", "帽子", "围裙", "眼镜", "手套", "头巾", "羽毛", "箭袋", "矿车", "蝙蝠", "骆驼", "熊猫", "狐狸",
    "兔子", "绵羊", "海龟", "山羊", "青蛙", "羊驼", "干员", "技能", "天赋", "模组", "证章", "使者", "孩子", "舌尖", "恐鱼", "匪徒", "学者", "血肉",
    "纽带", "知识", "工具", "种子", "新芽", "野兽", "大树", "树苗", "族群", "洪流", "土壤", "幼子", "养分", "物种", "土地", "海洋", "星球", "环境",
    "食物", "锚点", "信物", "角色", "表情", "敌人", "弹道", "地块", "合同", "档案", "乐团", "模特", "支柱", "底盘", "乐队", "实体", "废墟", "帷幕",
    "乐章", "员工", "主管", "幕布", "镜头", "屏幕", "视野", "指令", "音符", "弱点", "灵魂", "职员", "噪音", "能量", "舞台", "掌声", "武器", "护甲",
    "镰刀", "总谱", "音乐", "末日", "交响", "饰品", "艺术", "公司", "乐谱", "神态", "举止", "风度", "听众", "内涵", "记忆", "心灵", "活物", "部门",
    "节奏", "萨满", "礼赞", "护盾", "子弹", "设施"
)
internal val FOG_EN_VERBS = listOf(
    "trade", "breed", "spawn", "cure", "drop", "gather", "claim", "share", "harvest", "plant", "throw",
    "pick", "equip", "open", "close", "craft", "heal", "explore", "buy", "sell", "restock", "launch", "ring",
    "summon", "carry", "chase", "shear", "wear", "consume", "collect", "deposit", "attack", "avoid", "push",
    "unlock", "tend", "scan", "store", "refresh", "inhabit", "ambush", "conceal", "deploy", "block",
    "recover", "trigger", "charge", "bind", "stun", "drain", "drag", "slay", "annihilate", "station",
    "produce", "enlist", "clear", "prevent", "plunder", "ignore", "probe", "notice", "demand", "examine",
    "witness", "misplace", "doubt", "extend", "sever", "question", "discard", "foresee", "beget", "nourish",
    "occupy", "absorb", "reshape", "nurture", "recruit", "perform", "conduct", "breach", "contain", "reduce",
    "resist", "suppress", "assign", "select", "nullify", "shroud", "cover", "obscure", "lock", "unveil",
    "dampen", "mute", "warn", "wipe", "mount", "spread", "kill", "shut", "withstand", "dispatch", "withdraw",
    "apply", "call", "lose", "develop", "wield", "drown", "offer", "assemble", "soak", "don", "judge", "lead",
    "coordinate", "convey", "forfeit", "savor", "awaken", "hasten", "ignite", "applaud"
)
internal val FOG_EN_QUANTS = listOf(
    "a stack of", "a block of", "a slot of", "a piece of", "a batch of", "a handful of", "two stacks of",
    "two blocks of", "two slots of", "two pieces of", "two batches of", "two handfuls of", "three stacks of",
    "three blocks of", "three slots of", "three pieces of", "three batches of", "three handfuls of",
    "four stacks of", "four blocks of", "four slots of", "four pieces of", "four batches of",
    "four handfuls of", "five stacks of", "five blocks of", "five slots of", "five pieces of",
    "five batches of", "five handfuls of", "six stacks of", "six blocks of", "six slots of", "six pieces of",
    "six batches of", "six handfuls of", "eight stacks of", "eight blocks of", "eight slots of",
    "eight pieces of", "eight batches of", "eight handfuls of", "ten stacks of", "ten blocks of",
    "ten slots of", "ten pieces of", "ten batches of", "ten handfuls of", "twelve stacks of",
    "twelve blocks of", "twelve slots of", "twelve pieces of", "twelve batches of", "twelve handfuls of",
    "several stacks of", "several blocks of", "several slots of", "several pieces of", "several batches of",
    "several handfuls of", "a tree of", "two trees of", "every tree of", "a grain of", "a few grains of",
    "a mouthful of", "half a mouthful of", "three mouthfuls of", "a sprout of", "every sprout of",
    "two squads of", "every squad of", "a set of", "a full set of", "a swarm of", "that swarm of", "a gang of",
    "that gang of", "three seconds of", "two tiers of"
)
internal val FOG_EN_NOUNS = listOf(
    "villagers", "emeralds", "beds", "bells", "carrots", "potatoes", "beetroots", "seeds", "pumpkins",
    "golems", "zombies", "pillagers", "witches", "farmers", "clerics", "librarians", "masons", "shepherds",
    "fletchers", "butchers", "armorers", "toolsmiths", "cartographers", "leatherworkers", "weaponsmiths",
    "fishermen", "nitwits", "babies", "particles", "doors", "ladders", "trapdoors", "chickens", "cats",
    "wolves", "composters", "barrels", "lecterns", "looms", "grindstones", "smokers", "stonecutters",
    "cauldrons", "apples", "saddles", "carpets", "books", "maps", "gifts", "rockets", "badges", "outfits",
    "hats", "aprons", "gloves", "crops", "igloos", "ravagers", "dispensers", "pistons", "operators", "skills",
    "talents", "modules", "insignias", "envoys", "children", "tongues", "seaborn", "bandits", "scholars",
    "flesh", "bonds", "lore", "tools", "kernels", "sprouts", "beasts", "oaks", "saplings", "tribes",
    "torrents", "soils", "offspring", "nutrients", "species", "lands", "oceans", "planets", "habitats",
    "meals", "anchors", "tokens", "characters", "expressions", "enemies", "projectiles", "tiles", "contracts",
    "dossiers", "orchestras", "mannequins", "pillars", "chassis", "bands", "entities", "ruins", "veils",
    "movements", "employees", "managers", "curtains", "cameras", "screens", "sightlines", "commands", "notes",
    "weaknesses", "souls", "clerks", "noises", "energy", "stages", "applause", "weapons", "armor", "scythes",
    "scores", "music", "doomsdays", "symphonies", "accessories", "artworks", "companies", "librettos",
    "demeanors", "manners", "graces", "audiences", "meanings", "memories", "minds", "creatures", "departments",
    "rhythms", "shamans", "tributes", "shields", "bullets", "facilities"
)

/** 黑雾时一个格子的假内容, 例如"收割三块面包" / "Harvest two stacks of carrots":
 *  words 是开始合约时抽好的编号 (Contract.fogWords), 按这一格是一天里的第几个半小时取. */
private fun fogNoise(words: List<IntArray>, at: java.time.LocalDateTime): String {
    val (v, q, n) = words[(at.hour * 2 + at.minute / 30) % words.size].toList()
    fun List<String>.at(i: Int) = this[i % size]
    return if (com.obliphur.oit.core.I18n.lang == "en")
        "${FOG_EN_VERBS.at(v).replaceFirstChar { it.uppercase() }} ${FOG_EN_QUANTS.at(q)} ${FOG_EN_NOUNS.at(n)}"
    else FOG_ZH_VERBS.at(v) + FOG_ZH_QUANTS.at(q) + FOG_ZH_NOUNS.at(n)
}

@Composable
fun HourLogScreen(go: (String) -> Unit) {
    LocalTick.current
    val o = LocalOit.current
    val core = OitApp.instance.core
    val dm = core.dm
    val enabled = dm.hourEnabled
    val day0 = LocalDate.parse(dm.todayDate).atStartOfDay()
    val now = LocalDateTime.now()
    val awaiting = dm.awaitingSlot()
    val list = rememberLazyListState()
    LaunchedEffect(dm.todayDate, awaiting) {
        val target = awaiting ?: now.withMinute(0).withSecond(0).withNano(0)
        val row = ((java.time.Duration.between(day0, target).toHours()) + 1).toInt().coerceIn(0, 23)
        list.scrollToItem(maxOf(0, row - 2))
    }
    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(tr("当前: {state}", "state" to if (enabled) tr("开启") else tr("关闭_状态")),
                    color = if (enabled) o.success else o.dim, fontWeight = FontWeight.Bold)
                Dim(tr("{date}(今天)", "date" to dm.todayDate) + if (enabled) "" else tr("   ·  记录已关闭"))
            }
            PlainButton(tr("⚙ 设置")) { go("settings:log") }
        }
        LazyColumn(state = list, modifier = Modifier.fillMaxSize().padding(top = 8.dp)) {
            items(24) { h ->
                // 每一行左边写这一小时的起点; 第 0 行是昨天 23:00 ~ 24:00
                val start = day0.plusHours((h - 1).toLong())
                val end = start.plusHours(1)
                val ds = start.toLocalDate().key()
                val isWait = awaiting != null && !awaiting.isBefore(start) && awaiting.isBefore(end)
                val isNow = enabled && !start.isAfter(now) && now.isBefore(end)
                // 合约 (MECHANICS 18.4): 细化规范的小时分成两个半小时各填一格;
                // 记录封锁: 已经填写了的格子 (手动 / 自动) 不能再改, 提交前先确认
                // 黑雾 (18.3): 除了"现在"的格子和待填写的格子, 时间写 ???, 内容是随机字符, 不能改
                val fog = core.contract.fog()
                val fogWords = if (fog) core.contract.fogWords() else emptyList()
                // 细化规范 + 黑雾: 盖住的行也都分成两格 (只是显示)
                val split = core.contract.fineHour(start) || (fog && core.contract.has("fine") &&
                    !(!start.isAfter(now) && now.isBefore(end)) && !isWait)
                val cells = if (split) listOf(
                    Triple(start, start.plusMinutes(30), dm.halfText(ds, start.hour, 0)),
                    Triple(start.plusMinutes(30), end, dm.halfText(ds, start.hour, 1)))
                    else listOf(Triple(start, end, dm.hourText(ds, start.hour)))
                val hidden = cells.map { (a, b, t) ->
                    val wait = awaiting != null && !awaiting.isBefore(a) && awaiting.isBefore(b)
                    // 休息中没填写的格子也露出来, 自己填写之后才盖住 (18.4)
                    fog && !(!now.isBefore(a) && now.isBefore(b)) && !wait &&
                        !core.contract.restCell(a, b, t, dm.autoValue, dm::autoTextForKey)
                }
                val editable = cells.mapIndexed { i, (a, b, t) ->
                    !hidden[i] && enabled && !b.isAfter(now) &&
                        !core.contract.hourLocked(a, b, t, now, dm.fillSeconds.toLong(), dm.autoValue,
                            dm::autoTextForKey)
                }
                // 🔒: 结束了还不能改的; 记录封锁另外封锁的还没结束的格子也标上
                val locked = cells.indices.any { i -> val (a, b, t) = cells[i]
                    !hidden[i] && enabled && (!editable[i] && !b.isAfter(now) ||
                        core.contract.hourLocked(a, b, t, now, dm.fillSeconds.toLong(), dm.autoValue, dm::autoTextForKey)) }
                val shown = cells.mapIndexed { i, (a, _, t) -> if (hidden[i]) fogNoise(fogWords, a) else t }
                val sealed = cells.map { (a, b, _) -> core.contract.hourSealed(a, b, now) }
                HourRow(if (hidden.all { it }) "???" else "%02d:00".format(start.hour) + when {   // 左边写这一小时的起点
                    isNow -> tr("   ◂ 现在")
                    isWait -> tr("   ◂ 待填写")
                    locked -> com.obliphur.oit.core.I18n.ic("   🔒")
                    else -> ""
                }, shown, editable, isWait || isNow, sealed) { i, text ->
                    if (text != cells[i].third) {
                        if (split) dm.setHalfText(ds, start.hour, i, text) else dm.setHourText(ds, start.hour, text)
                        if (isWait) Notifier.cancelHour(OitApp.instance)
                        if (text.isNotBlank() && cells[i].third.isBlank()) core.sealExtra()   // 记录封锁: 另外封锁两个格子
                        core.changed()
                    }
                }
            }
        }
    }
}

/** 一行: 一格 (整小时) 或两格 (细化规范的两个半小时).
 *  sealed: 记录封锁的格子 —— 只有按完成 (Enter) 并确认后才保存, 离开输入框时不保存 (写了的留着). */
@Composable
private fun HourRow(label: String, stored: List<String>, editable: List<Boolean>, highlight: Boolean,
                    sealed: List<Boolean> = stored.map { false }, commit: (Int, String) -> Unit) {
    val o = LocalOit.current
    val focus = LocalFocusManager.current
    var ask by remember { mutableStateOf<Pair<Int, String>?>(null) }
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 3.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(label, color = if (highlight) o.accent else if (editable.any { it }) o.text else o.dim,
            fontSize = 14.sp, modifier = Modifier.width(if (stored.size > 1) 92.dp else 118.dp))
        stored.forEachIndexed { i, s ->
            var text by remember(s) { mutableStateOf(s) }
            OutlinedTextField(text, { text = it }, enabled = editable[i], singleLine = true,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = {
                    val t = text.trim()
                    if (sealed[i] && t.isNotEmpty() && t != s) ask = i to t     // 记录封锁: 先确认
                    else { commit(i, t); focus.clearFocus() }
                }),
                modifier = Modifier.weight(1f).onFocusChanged {
                    if (!it.isFocused && text.trim() != s && !(sealed[i] && text.trim().isNotEmpty())) commit(i, text.trim())
                })
        }
    }
    ask?.let { (i, t) ->
        Confirm(tr("确认修改"), tr("确认修改这个格子吗?") + "\n" + tr("记录封锁: 填写后就不能再修改了.") + "\n\n" + t,
            false, { ask = null }) {
            ask = null
            commit(i, t)
            focus.clearFocus()
        }
    }
}

// ════════════════ Calendar 页 (MECHANICS 8.2) ════════════════

@Composable
fun CalendarScreen() {
    LocalTick.current
    val o = LocalOit.current
    val core = OitApp.instance.core
    var last by rememberSaveable { mutableStateOf(false) }
    var kind by rememberSaveable { mutableStateOf("study") }     // 切换: 格子里写哪一种时间 (专注 / 休息 / 空闲 / 睡眠, MECHANICS 8.2)
    val today = LocalDate.now()
    val ym = YearMonth.from(today).let { if (last) it.minusMonths(1) else it }
    val prefix = "%d-%02d".format(ym.year, ym.monthValue)
    // 合约的黑雾 (MECHANICS 18.3): 随机的学习时长, 月份和合计写成 ???
    val fog = core.contract.fog()
    val data = if (fog) java.util.Random("${core.contract.id}/$prefix".hashCode().toLong()).let { r ->
        (1..ym.lengthOfMonth()).map { ym.atDay(it) }.filter { !it.isAfter(today) }
            .associate { it.key() to Math.round((16 + r.nextDouble() * 64) * 10) / 10.0 }      // 16 ~ 80 h
    } else if (kind != "study") {
        // 休息 / 空闲 / 睡眠: 只有最近 7 天有专注计时记录, 更早的日子不写
        val now = LocalDateTime.now()
        val live = core.timer.calendarLive()
        (1..ym.lengthOfMonth()).map { ym.atDay(it) }
            .filter { !it.isAfter(today) && !it.isBefore(today.minusDays(6)) }
            .associate { d -> d.key() to (core.dm.dayKinds(d, if (d == today) live else null, now)[kind] ?: 0.0) / 3600.0 }
    } else core.dm.monthStudy(prefix)
    PageColumn {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(if (fog) "???" else prefix, fontWeight = FontWeight.Bold, fontSize = 18.sp, color = o.text,
                modifier = Modifier.weight(1f))
            Segmented(listOf("last" to tr("‹ 上月"), "this" to tr("本月 ›")), if (last) "last" else "this") { last = it == "last" }
        }
        Dim(if (fog) tr("合计: ???") else when (kind) {
            "break" -> tr("休息合计 {h} h", "h" to f1(data.values.sum()))
            "idle" -> tr("空闲合计 {h} h", "h" to f1(data.values.sum()))
            "sleep" -> tr("睡眠合计 {h} h", "h" to f1(data.values.sum()))
            else -> tr("合计 {h} h", "h" to f1(data.values.sum()))
        })
        // 四种时间的切换: 专注 / 休息 / 空闲 / 睡眠 (睡眠是单独的一种时间)
        Segmented(listOf("study" to tr("专注"), "break" to tr("休息"), "idle" to tr("空闲"), "sleep" to tr("睡眠")), kind) { kind = it }
        val names = if (OitApp.instance.core.settings.str("LANGUAGE") == "en")
            listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun") else listOf("一", "二", "三", "四", "五", "六", "日")
        Row { names.forEach { Text(it, color = o.dim, textAlign = TextAlign.Center, modifier = Modifier.weight(1f)) } }
        val first = ym.atDay(1)
        val lead = first.dayOfWeek.value - 1
        val cells = lead + ym.lengthOfMonth()
        for (week in 0 until (cells + 6) / 7) {
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                for (dow in 0 until 7) {
                    val idx = week * 7 + dow - lead
                    Box(Modifier.weight(1f).aspectRatio(0.8f)) {
                        if (idx in 0 until ym.lengthOfMonth()) {
                            val d = first.plusDays(idx.toLong())
                            val h = data[d.key()] ?: 0.0
                            val tint = o.accent.copy(alpha = (h / (if (kind == "study") 8.0 else 24.0)).coerceIn(0.0, 1.0).toFloat() * 0.55f)
                            Column(Modifier.fillMaxSize().background(o.card, RoundedCornerShape(6.dp))
                                .background(tint, RoundedCornerShape(6.dp))
                                .border(1.dp, if (d == today) o.accent else o.border, RoundedCornerShape(6.dp))
                                .padding(4.dp)) {
                                Text("${d.dayOfMonth}", fontSize = 12.sp, color = o.dim)
                                if (h > 0) Text(f1(h), fontSize = 13.sp, fontWeight = FontWeight.Bold, color = o.text,
                                    modifier = Modifier.fillMaxWidth(), textAlign = TextAlign.Center)
                            }
                        }
                    }
                }
            }
        }
    }
}

// ════════════════ Statistics 页 (MECHANICS 8.1) ════════════════

private const val OTHER_BELOW = 0.01
private const val OTHER_TIP_LINES = 25
/** 黑雾: 饼图里填多少个没有意义的项. */
private const val FOG_ITEMS = 10

/** 统计页的合约记录: 看最近几个顺利完成的合约 (黑雾时补满这么多条). */
private const val CONTRACT_ROWS = 5

/** 违规记录页: 最多留几条 (黑雾时补满, 内容全是 ???). */
private const val VIOLATION_ROWS = 15

/** 统计页的一条合约记录: a = 初始等级, b = 失效等级; rows 和合约页一样的词条行. */
/** combos: 完成时凑齐的组合的成员 → 组合 id (Contract.recordMarks). */
private data class ContractRow(val a: Int, val b: Int, val rows: List<com.obliphur.oit.core.RecRow>,
                               val combos: Map<String, String> = emptyMap())

/** 和合约页一样: 最终等级[最初等级-扣的等级] (没扣过只写最终等级). */
private fun levelText(orig: Int, level: Int): String = Contract.levelLabel(level, orig)

/** 黑雾: 5 条假记录 (初始等级 0 ~ 99, 失效等级 1 ~ 30; 词条随机填充), 同一个合约里不变. */
private fun fogContractRows(seed: Long): List<ContractRow> {
    val ids = Contract.TERMS.map { it.id }
    return (0 until CONTRACT_ROWS).map { i ->
        val rnd = java.util.Random("$seed/contracts/$i".hashCode().toLong())
        val picked = ids.shuffled(rnd).take(2 + rnd.nextInt(4))
        ContractRow(rnd.nextInt(100), 1 + rnd.nextInt(30),
            picked.map { com.obliphur.oit.core.RecRow(Contract.BY_ID[it]!!.level, it, rnd.nextInt(100) < 35, "") })
    }
}

/** 统计页: 最近 5 条顺利完成的合约 (等级, 点开看词条; 失效的变灰; 黑雾时是假数据). */
@Composable
private fun ContractRecords() {
    val o = LocalOit.current
    val core = OitApp.instance.core
    val c = core.contract
    var open by remember { mutableStateOf<ContractRow?>(null) }
    val fog = c.fog()
    // 组合的颜色 (黄色在两种主题里都用深一点的黄, 浅色背景上也看得清)
    val tint = mapOf("yellow" to Color(0xffd9a400), "purple" to Color(0xffb06cf0), "blue" to Color(0xff2456c7), "red" to Color(0xffff2b3a))
    val rows = remember(fog, c.id, c.history.size) {
        if (fog) fogContractRows(c.id)
        else Contract.recentCompleted(c.history, CONTRACT_ROWS).map {
            val level = it.optInt("level")
            ContractRow(if (it.has("orig_level")) it.optInt("orig_level") else level, level, Contract.recordRows(it),
                Contract.recordMarks(it))
        }
    }
    OitCard {
        Text(tr("⚔  合约记录"), fontWeight = FontWeight.Bold, color = o.text, fontSize = 16.sp)
        if (rows.isEmpty()) Dim(tr("还没有顺利完成的合约."), size = 13)
        for (r in rows) {
            Row(Modifier.fillMaxWidth().clickable { open = r }.padding(vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Text(tr("等级"), color = o.dim, fontSize = 12.sp, modifier = Modifier.padding(end = 10.dp))
                Text(levelText(r.a, r.b), color = o.accent, fontWeight = FontWeight.Bold, fontSize = 18.sp,
                    modifier = Modifier.weight(1f))
                Text(tr("点击查看词条"), color = o.dim, fontSize = 12.sp)
            }
        }
    }
    open?.let { r ->
        AlertDialog(onDismissRequest = { open = null },
            title = { Text(tr("⚔  合约词条"), fontWeight = FontWeight.Bold) },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    Text(tr("等级 {lv}", "lv" to levelText(r.a, r.b)), color = o.accent,
                        fontWeight = FontWeight.Bold, modifier = Modifier.padding(bottom = 6.dp))
                    // 和合约页一样: 等级三角 + 名字:描述 + [原 X] / [已失效], 失效的变灰;
                    // 完成时凑齐的组合: 成员已经放在一起置顶 (recordRows), 用组合的颜色写
                    for ((lv, tid, void, orig) in r.rows) {
                        val fg = if (void) null else Contract.COMBOS.firstOrNull { it.id == r.combos[tid] }?.let { tint[it.color] }
                        var text = termLine(tid)
                        if (orig.isNotEmpty()) text += "   " + tr("[原 {name}]", "name" to termShort(orig))
                        if (void) text += "   " + tr("[已失效]")
                        Row(Modifier.padding(vertical = 2.dp)) {
                            Text(marks(lv).ifEmpty { "—" }, color = if (void) o.dim else fg ?: o.accent,
                                fontWeight = FontWeight.Bold, fontSize = 12.sp, modifier = Modifier.padding(end = 8.dp))
                            Text(text, color = if (void) o.dim else fg ?: o.text, fontSize = 14.sp)
                        }
                    }
                }
            },
            confirmButton = { TextButton({ open = null }) { Text(tr("关闭")) } })
    }
}

/** 统计页: 最近 15 条合约违规的提示文字 (黑雾时全部是 ???, 不足 15 条补满). */
@Composable
private fun ViolationRecords() {
    LocalTick.current
    val o = LocalOit.current
    val core = OitApp.instance.core
    val fog = core.contract.fog()
    val rows = if (fog) List(VIOLATION_ROWS) { "???" to "???" } else core.recentViolations(VIOLATION_ROWS)
    OitCard {
        Text(tr("⚠  违规记录"), fontWeight = FontWeight.Bold, color = o.text, fontSize = 16.sp)
        if (rows.isEmpty()) Dim(tr("还没有违规记录."), size = 13)
        for ((at, text) in rows) {
            Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
                Text(at, color = o.dim, fontSize = 11.sp)
                Text(text, color = o.text, fontSize = 14.sp)
            }
        }
    }
}

@Composable
fun StatsScreen() {
    LocalTick.current
    val o = LocalOit.current
    val core = OitApp.instance.core
    val ctx = LocalContext.current
    val logOn = core.dm.hourEnabled
    var page by rememberSaveable { mutableStateOf(if (logOn) "log" else "net") }
    if (!logOn && page == "log") page = "net"
    var last by rememberSaveable { mutableStateOf(false) }
    var cmode by rememberSaveable { mutableStateOf("done") }      // 合约页: 通过记录 / 违规记录
    var device by rememberSaveable { mutableStateOf("all") }      // 应用页看哪台设备
    val modes = remember { mutableStateOf(mapOf<String, String>()) }
    val pct = (modes.value[page] ?: "pct") == "pct"
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        val tabs = buildList {
            if (logOn) add("log" to tr("📝  整点记录"))
            add("net" to tr("🌐  联网理由"))
            add("focus" to tr("⏱  专注计时"))
            add("window" to tr("📱  应用"))
            add("contracts" to tr("⚔  合约"))
        }
        Segmented(tabs, page) {
            page = it
            if (it == "contracts") cmode = "done"      // 每次打开合约页都从"通过记录"开始
        }
        if (page == "focus") {
            FocusBars()
            return@Column
        }
        if (page == "contracts") {
            Segmented(listOf("done" to tr("通过记录"), "violations" to tr("违规记录")), cmode) { cmode = it }
            if (cmode == "violations") ViolationRecords() else ContractRecords()
            return@Column
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Segmented(listOf("this" to tr("本月"), "last" to tr("上月")), if (last) "last" else "this") { last = it == "last" }
            Segmented(listOf("pct" to tr("占比"), "time" to tr("总时间")), if (pct) "pct" else "time") {
                modes.value = modes.value + (page to it)
            }
        }
        // 应用页: 绑定了其他设备时按设备切换 (全部 / 本机 / 每台设备; 全部时重叠的分钟以母端为准)
        if (page == "window") {
            val devices = listOf("all" to tr("全部设备")) + core.dm.windowDevices().map { (k, n) -> k to if (k == "self") tr("本机") else n }
            if (devices.none { it.first == device }) device = "all"
            if (devices.size > 2) Segmented(devices, device) { device = it }
        }
        val (thisM, lastM) = monthPrefixes()
        val prefix = if (last) lastM else thisM
        val blank = tr("空白")
        val fog = core.contract.fog()
        val data: Map<String, Double> = if (fog) {
            // 合约的黑雾 (MECHANICS 18.3): 不显示真正的数据, 10 个没有意义的项, 比例随机, 名字都是 ???
            val r = java.util.Random("${core.contract.id}/$page/$prefix".hashCode().toLong())
            (0 until FOG_ITEMS).associate { "???" + "\u200b".repeat(it) to (1 + r.nextDouble() * 8) * 3600 }
        } else when (page) {
            "log" -> core.dm.hourActivitySeconds(prefix)
            "window" -> core.dm.windowSeconds(prefix, device).entries.groupBy({
                when (it.key) { "" -> blank; "?" -> tr("未知程序"); "OIT" -> "OIT"; else -> appLabel(ctx, it.key) }
            }, { it.value }).mapValues { it.value.sum() }
            else -> tallyItems(core.net.usageByReason(prefix))
        }
        Text("${if (last) tr("上月") else tr("本月")} · $prefix", fontWeight = FontWeight.Bold, color = o.text)
        PieBlock(data, pct, if (page == "window" && !fog) blank else null, if (fog) "???" else tr("其他"))
    }
}

@Composable
private fun PieBlock(data: Map<String, Double>, pct: Boolean, blankName: String?, otherName: String) {
    val o = LocalOit.current
    val items = data.entries.sortedWith(compareBy({ -it.value }, { it.key })).map { it.key to it.value }
    val total = items.sumOf { it.second }
    Dim(tr("合计 {t} · {n} 项", "t" to fmtHm(total), "n" to items.size))
    if (items.isEmpty() || total <= 0) {
        Dim(tr("暂无数据")); return
    }
    // 占比 ≥ 1% 的项单独上色 (最多用完调色板), 其余归入「其他」; 「空白」用空白色, 不占调色板
    val main = items.filter { it.second / total >= OTHER_BELOW }.let { l ->
        val out = mutableListOf<Pair<String, Double>>()
        var colored = 0
        for (it in l) {
            if (it.first == blankName) out.add(it) else if (colored < o.chart.size) { out.add(it); colored++ }
        }
        out
    }
    val shown = main.map { it.first }.toSet()
    val other = items.filter { it.first !in shown }
    var ci = 0
    val slices = main.map { (k, v) -> Triple(k, v, if (k == blankName) o.chartEmpty else o.chart[ci++]) }.toMutableList()
    if (other.isNotEmpty()) slices.add(Triple(otherName, other.sumOf { it.second }, o.chartOther))
    var tip by remember { mutableStateOf(false) }
    Canvas(Modifier.fillMaxWidth().aspectRatio(1.6f).pointerInput(slices) {
        detectTapGestures { p ->
            val c = Offset(size.width / 2f, size.height / 2f)
            val ang = (Math.toDegrees(atan2((p.y - c.y).toDouble(), (p.x - c.x).toDouble())) + 450) % 360
            var acc = 0.0
            for (s in slices) {
                val sweep = s.second / total * 360
                if (ang < acc + sweep) { if (s.first == otherName) tip = true; break }
                acc += sweep
            }
        }
    }) {
        val d = minOf(size.width, size.height) * 0.92f
        val tl = Offset((size.width - d) / 2, (size.height - d) / 2)
        var start = -90f
        for ((_, v, color) in slices) {
            val sweep = (v / total * 360).toFloat()
            drawArc(color, start, sweep, true, tl, Size(d, d))
            start += sweep
        }
    }
    for ((k, v, color) in slices) {
        Row(verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.fillMaxWidth().clickable(enabled = k == otherName) { tip = true }.padding(vertical = 3.dp)) {
            Box(Modifier.size(12.dp).background(color, RoundedCornerShape(2.dp)))
            Text(k, color = o.text, fontSize = 14.sp, modifier = Modifier.weight(1f).padding(start = 8.dp))
            Text(if (pct) "%.1f%%".format(v / total * 100) else fmtHm(v), color = o.text, fontWeight = FontWeight.Bold, fontSize = 13.sp)
        }
    }
    if (tip && other.isNotEmpty()) {
        val lines = other.take(OTHER_TIP_LINES).map { (k, v) ->
            if (pct) "$k    %.2f%%".format(v / total * 100) else "$k    ${fmtHm(v)}"
        }.toMutableList()
        if (other.size > OTHER_TIP_LINES) lines.add(tr("…以及另外 {n} 项", "n" to other.size - OTHER_TIP_LINES))
        AlertDialog(onDismissRequest = { tip = false }, containerColor = o.bg, title = { Text(otherName) },
            text = { Text(lines.joinToString("\n")) },
            confirmButton = { TextButton({ tip = false }) { Text(tr("确定"), color = o.accent) } })
    }
}

/** 最近 7 天, 每天 24 根条, 每根代表一小时, 按比例分成专注 / 休息 / 空白. 点一下看分钟数. */
@Composable
private fun FocusBars() {
    val o = LocalOit.current
    val core = OitApp.instance.core
    val today = LocalDate.now()
    val now = LocalDateTime.now()
    var sel by remember { mutableStateOf<Pair<LocalDate, Int>?>(null) }
    val live = core.timer.live()
    Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
        for ((name, c) in listOf(tr("专注") to o.focusStudy, tr("休息") to o.focusBreak, tr("空白") to o.chartEmpty)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(10.dp).background(c, RoundedCornerShape(2.dp)))
                Text(name, fontSize = 12.sp, color = o.dim, modifier = Modifier.padding(start = 4.dp))
            }
        }
    }
    val weekNames = if (core.settings.str("LANGUAGE") == "en") listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    else listOf("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    val fog = core.contract.fog()
    for (i in 0 until 7) {
        val day = today.minusDays(i.toLong())
        val hours = if (fog) fogHours(core.contract.id, day, now) else core.dm.focusHours(day, live)
        Text(if (fog) "???" else "%02d-%02d %s".format(day.monthValue, day.dayOfMonth, weekNames[day.dayOfWeek.value - 1]) +
            if (i == 0) "  " + tr("今天") else "", fontSize = 12.sp, color = o.dim)
        Canvas(Modifier.fillMaxWidth().height(34.dp).pointerInput(day) {
            detectTapGestures { p -> sel = day to (p.x / size.width * 24).toInt().coerceIn(0, 23) }
        }) {
            val w = size.width / 24f
            for (h in 0 until 24) {
                val x = h * w + 1
                val bw = w - 2
                val future = day == today && h > now.hour
                if (future) { drawRect(o.chartFuture, Offset(x, 0f), Size(bw, size.height)); continue }
                val (s, b) = hours[h]
                val hs = (s / 3600).toFloat() * size.height
                val hb = (b / 3600).toFloat() * size.height
                drawRect(o.chartEmpty, Offset(x, 0f), Size(bw, size.height - hs - hb))
                drawRect(o.focusBreak, Offset(x, size.height - hs - hb), Size(bw, hb))
                drawRect(o.focusStudy, Offset(x, size.height - hs), Size(bw, hs))
                if (sel == day to h) drawRect(Color.Black.copy(alpha = 0.15f), Offset(x, 0f), Size(bw, size.height))
            }
        }
    }
    sel?.let { (d, h) ->
        val (s, b) = (if (fog) fogHours(core.contract.id, d, now) else core.dm.focusHours(d, live))[h]
        Text((if (fog) "??? " else "%02d-%02d ".format(d.monthValue, d.dayOfMonth)) +
            "%02d:00–%02d:00 · ".format(h, (h + 1) % 24) +
            tr("专注 {s} 分 · 休息 {b} 分", "s" to Math.round(s / 60), "b" to Math.round(b / 60)),
            color = o.text, fontSize = 13.sp)
    }
}

/** 黑雾 (MECHANICS 18.3): 每一小时随机填上专注 / 休息 / 空白 (同一个合约里不变). */
private fun fogHours(seed: Long, day: LocalDate, now: LocalDateTime): List<Pair<Double, Double>> {
    val r = java.util.Random("$seed/focus/${day.key()}".hashCode().toLong())
    return (0 until 24).map { h ->
        val st = listOf(0.0, 0.0, r.nextDouble() * 3600, 3600.0)[r.nextInt(4)]
        val br = r.nextDouble() * (3600 - st)
        if (day.atStartOfDay().plusHours(h.toLong()).isAfter(now)) 0.0 to 0.0 else st to br
    }
}
