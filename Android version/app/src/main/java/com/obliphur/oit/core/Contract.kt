package com.obliphur.oit.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.LocalDateTime

/** 词条: id, 组 (同一组互相冲突, 界面上排在同一列), 等级, 名字, 描述, 另外冲突的词条. */
/** 未知变量用的随机数: roll() 是 [0, 1) 的小数, pick(n) 是 [0, n) 的下标 (测试时可以换成固定的). */
class Dice(val roll: () -> Double = { kotlin.random.Random.nextDouble() },
           val pick: (Int) -> Int = { kotlin.random.Random.nextInt(it) })

class Term(val id: String, val group: String, val level: Int, val name: String, val desc: String,
           val conflicts: List<String> = emptyList())

/** 词条组合 (MECHANICS 18.3.1): 列出的词条 / 词条系列 (任意一档都算) 同时选着时生效.
 *  level: 额外的危机等级; rest: 休息时间的额外乘区; gift: 开始时给的休息时间 (秒); end: 完成时给的休息时间 (秒).
 *  成员因违反失效 (降级不算失效) 时组合不再生效: 危机等级 / 乘区 / 结束后收益都没了, 开始时给的不收回. */
class Combo(val id: String, val name: String, val color: String, val reqs: List<String>,
            val level: Int, val rest: Double, val gift: Int, val end: Int, val desc: String)

/**
 * 合约 (MECHANICS 18). 对应桌面版 contract.py.
 * 存档 contract.json: 进行中的合约 (live), 结束了的合约 (history, 整点记录的锁定也看它), 没开始的草稿 (draft).
 * live 和 history 随设备同步, draft 只留在本机.
 */
class Contract(private val file: File) {
    companion object {
        const val BASE_SECONDS = 4 * 3600.0
        const val IDLE_LIMIT = 30 * 60.0
        /** 合约中有绑定的设备离线超过这么久就失败. */
        const val OFFLINE_LIMIT = 15 * 60.0
        /** 初始目标生命值: 每违反一次减一, 减到 0 合约失败. */
        const val VIOLATION_LIMIT = 4
        const val HISTORY_KEEP = 30
        const val HARD_LEVEL = 20

        // 成系列的 (有好几档的) 在前, 界面上放在左边, 一组一列; 同一模块的系列挨在一起.
        // 零散的 (只有一档的) 在后, 放在右边, 按等级排成行; 同一模块的上下对齐.
        val TERMS = listOf(
            // ---- 合约本身: 时长, 目标生命值 ----
            Term("long1", "long", 1, "环境:持久战-I", "时长改为120%"),
            Term("long2", "long", 2, "环境:持久战-II", "时长改为140%"),
            Term("long3", "long", 3, "环境:持久战-III", "时长改为160%"),
            Term("retreat1", "retreat", 1, "环境:退路封锁-I", "初始目标生命值为3"),
            Term("retreat2", "retreat", 2, "环境:退路封锁-II", "初始目标生命值为2"),
            Term("last", "retreat", 3, "最后防线", "初始目标生命值为1"),
            // ---- 专注 / 休息: 专注的下限, 休息时间的倍率, 合约开始前积攒的休息时间 ----
            Term("deep1", "deep", 1, "目标:纵深推进-I", "专注的时间不能少于40min"),
            Term("deep2", "deep", 2, "目标:纵深推进-II", "专注的时间不能少于60min"),
            Term("tired1", "tired", 1, "环境:疲惫-I", "期间获得的休息时间改为70%"),
            Term("tired2", "tired", 2, "环境:疲惫-II", "期间获得的休息时间改为60%"),
            Term("tired3", "tired", 3, "环境:疲惫-III", "期间获得的休息时间改为50%"),
            Term("short1", "short", 1, "环境:支援短缺-I", "可用的合约开始前已保存休息时间改为50%"),
            Term("short2", "short", 2, "环境:支援短缺-II", "不能使用合约开始前已经保存的休息时间"),
            // ---- 联网: 剩余额度, 冷静期 ----
            Term("info1", "info", 1, "环境:信息阻断-I", "可用的剩余联网时间改为80% 且不能使用借贷"),
            Term("info2", "info", 2, "环境:信息阻断-II", "可用的剩余联网时间改为65% 且不能使用借贷"),
            Term("info3", "info", 3, "环境:信息阻断-III", "可用的剩余联网时间改为50% 且不能使用借贷"),
            Term("erode1", "erode", 1, "环境:侵蚀-I", "网络申请冷却期改为200%"),
            Term("erode2", "erode", 2, "环境:侵蚀-II", "网络申请冷却期改为300%"),
            // ---- 窗口 ----
            Term("chan1", "chan", 1, "环境:通道阻塞-I", "在专注期间的最大窗口数改为4"),
            Term("chan2", "chan", 2, "环境:通道阻塞-II", "在专注期间的最大窗口数改为3"),
            // ---- 零散的 (只有一档的): 右边, 按等级排成行, 同一模块的放在一起 / 上下对齐 ----
            // 1 级: 联网 (抑制), 整点记录 (记录封锁), 禁止打开的软件 (乐理之灾 / 禁游令 / 低帧的诅咒),
            // 现实 (高机动 / 禁足), 黑雾, 休息 (禁止囤囤鼠!), 专注中的联网 (静默协议)
            Term("suppress", "suppress", 1, "环境:抑制", "不能使用快速访问功能"),
            Term("seal", "seal", 1, "环境:记录封锁", "超过填写时间则不能再修改该记录"),
            Term("music", "music", 1, "环境:乐理之灾", "合约期间音乐播放软件禁止联网 可以通过点击解除网络禁令"),
            Term("game", "game", 1, "环境:禁游令", "合约期间游戏和游戏平台禁止联网 可以通过点击解除网络禁令"),
            Term("video", "video", 1, "环境:低帧的诅咒", "合约期间视频平台禁止联网 可以通过点击解除网络禁令"),
            Term("move", "move", 1, "目标:高机动", "现实中的移动速度+50%"),
            Term("confine", "confine", 1, "目标:禁足", "在非休息时间不能离开工作岗位进行活动"),
            Term("fog", "fog", 1, "环境:黑雾", "合约期间 专注时长 合约的剩余时间 成就 日历 统计数据不可见 且无法打开设置"),
            Term("hoard", "hoard", 1, "禁止囤囤鼠!", "不能提前结束休息 但是获得的休息时间改为110%",
                listOf("short1", "short2")),
            Term("quiet", "quiet", 1, "环境:静默协议", "专注中不能处于联网状态"),
            // 2 级: 联网 (全频道阻塞干扰), 整点记录 (细化规范), 禁止打开软件里的 无言为真 (乐理之灾下面),
            // 录像 (目光 / 刻痕) 在 禁游令 / 低帧的诅咒 下面
            Term("jam", "jam", 2, "环境:全频道阻塞干扰", "期间使用proxy block-all模式"),
            Term("fine", "fine", 2, "目标:细化规范", "记录变为30min一次"),
            Term("silent", "silent", 2, "环境:无言为真", "合约期间社交软件和与AI聊天的软件禁止联网 可以通过点击解除网络禁令"),
            Term("gaze", "gaze", 2, "目光", "合约期间打开摄像头记录使用者的行为 结束后保存录像"),
            Term("scar", "scar", 2, "刻痕", "合约期间录制整个屏幕 结束后保存录像",
                listOf("gaze")),
            // 3 级: 未知变量
            Term("unknown", "unknown", 3, "未知变量", "合约开始时 以及之后每过一个整小时 从当前可以选择的1-2级词条中随机启用一个或随机施加效果\n选中的词条不算进危机等级"),
        )
        val BY_ID = TERMS.associateBy { it.id }
        val GROUPS = TERMS.map { it.group }.distinct()
        /** 词条板的布局: 成系列的每组一列 (左边); 零散的不单独占列, 按等级放进右边的几列, 每一行从左往右挨着排, 不留空. */
        val SERIES = GROUPS.filter { g -> TERMS.count { it.group == g } > 1 }
        /** 词条 → 列. */
        val LAYOUT: Map<String, Int> = run {
            val loose = HashMap<Int, Int>()
            TERMS.associate { t ->
                t.id to if (t.group in SERIES) SERIES.indexOf(t.group)
                else SERIES.size + loose.getOrDefault(t.level, 0).also { loose[t.level] = it + 1 }
            }
        }
        val BOARD_COLS = (LAYOUT.values.maxOrNull() ?: -1) + 1
        val MAX_LEVEL = TERMS.maxOf { it.level }
        /** 词条板翻页: 成系列的一页, 零散的一页 (每页的列); 第 3 页 (COMBO_PAGE) 是词条组合的效果. */
        val PAGES = listOf(0 until SERIES.size, SERIES.size until BOARD_COLS)
        const val COMBO_PAGE = 2
        private val ORDER = TERMS.mapIndexed { i, t -> t.id to i }.toMap()

        // 软规则: 界面上照样能做, 但会被算作违反, 这个词条降一级 (结算按降级后的等级);
        // 其余是硬规则, 立即生效 (黑雾, 记录封锁, 细化规范, 全频道阻塞干扰, 持久战, 疲惫).
        // 录像 (目光 / 刻痕) 违反一次就失效 (录像停止): 不在 LOWER / SINGLE 里.
        val SOFT = setOf("info1", "info2", "info3", "chan1", "chan2", "music", "suppress", "short1", "short2",
            "silent", "game", "video", "gaze", "scar", "deep1", "deep2", "quiet", "hoard", "unknown")
        /** 降一级换成哪个词条. */
        val LOWER = mapOf("info3" to "info2", "info2" to "info1", "chan2" to "chan1",
            "short2" to "short1", "deep2" to "deep1")
        /** 只有一档的软规则: 降级时等级减一, 减到 0 就失效. */
        val SINGLE = setOf("music", "suppress", "silent", "game", "video", "quiet", "hoard")

        val DURATION = mapOf("long1" to 1.2, "long2" to 1.4, "long3" to 1.6)
        val NET_SHARE = mapOf("info1" to 0.8, "info2" to 0.65, "info3" to 0.5)
        /** 合约中专注换来的休息时间的基础倍率; 有疲惫时换成疲惫的倍率. */
        const val BASE_REST = 0.8
        val REST = mapOf("tired1" to 0.7, "tired2" to 0.6, "tired3" to 0.5)
        /** 休息时间额外的乘区 (和疲惫相乘): 禁止囤囤鼠! ×1.1, 打年糕 ×1.2 (词条组合). */
        val REST_BONUS = mapOf("hoard" to 1.1)
        /** 合约中专注换来的休息时间乘多少: 基础 ×0.8, 有疲惫时换成疲惫的 (取最低的); 再乘额外的乘区 / 组合的乘区. */
        fun restFactorOf(terms: Collection<String>): Double =
            (terms.mapNotNull { REST[it] }.minOrNull() ?: BASE_REST) * terms.fold(1.0) { f, t -> f * (REST_BONUS[t] ?: 1.0) } * comboRest(terms)
        /** 录像的词条 → 录什么. */
        val RECORD = mapOf("gaze" to "camera", "scar" to "screen")
        /** 禁止打开某类应用的词条 (每秒看前台应用). */
        val BANS = listOf("music", "silent", "game", "video")
        val WINDOWS = mapOf("chan1" to 4, "chan2" to 3)
        /** 支援短缺: 合约开始前积攒的休息时间能用几成. */
        val SUPPLY = mapOf("short1" to 0.5, "short2" to 0.0)
        /** 整点记录的词条: 整点记录关着时, 这次合约中照样开启. */
        val HOUR_TERMS = setOf("seal", "fine")
        /** 初始目标生命值 (硬规则). */
        val LIFE = mapOf("retreat1" to 3, "retreat2" to 2, "last" to 1)

        // 词条组合 (MECHANICS 18.3.1): 列出的词条 / 词条系列 (任意一档都算) 同时选着时生效, 效果栏的第 3 页列出它们.
        val COMBOS = listOf(
            Combo("mochi", "打年糕", "white", listOf("tired", "long", "fine", "deep"), 1, 1.2, 0, 0,
                "额外 1 危机等级\n休息时间 ×120%"),
            Combo("nofun", "互联网娱乐的败退", "purple", listOf("music", "silent", "game", "video"), 0, 1.0, 20 * 60, 0,
                "合约开始时 休息 +20 分钟"),
            Combo("flawless", "无懈可击", "gray", listOf("last", "unknown", "move", "confine"), 0, 1.0, 20 * 60, 0,
                "合约开始时 休息 +20 分钟"),
            Combo("isolation", "信息隔绝", "red", listOf("info", "chan", "erode", "suppress", "fog", "quiet", "jam"), 1, 1.0, 0, 10 * 60,
                "额外 1 危机等级\n完成合约时 休息 +10 分钟"),
        )
        val COMBOS_BY_ID = COMBOS.associateBy { it.id }
        val COMBO_COLORS = mapOf("white" to "#ffffff", "purple" to "#b06cf0", "gray" to "#9a9ca1", "red" to "#ff2b3a")
        /** 组合的要求在界面上怎么写: 成系列的一组只写一个名字. */
        val GROUP_NAMES = mapOf("long" to "持久战", "tired" to "疲惫", "info" to "信息阻断",
            "chan" to "通道阻塞", "erode" to "侵蚀", "deep" to "纵深推进")

        fun reqName(req: String): String =
            GROUP_NAMES[req] ?: (BY_ID[req]?.name ?: req).substringAfter(':').substringBefore('-')

        /** 这个词条组合凑齐了没有: 每一条要求都有一个选着的词条 (系列的任意一档都算). */
        fun comboDone(combo: Combo, terms: Collection<String>): Boolean {
            val t = terms.filter { it in BY_ID }
            return combo.reqs.all { req -> t.any { id -> if (req in GROUPS) BY_ID[id]!!.group == req else id == req } }
        }

        /** 组合的状态: done 凑齐了 / void 开始时凑齐过, 有成员失效了 / open 还没凑齐. */
        fun comboState(combo: Combo, terms: Collection<String>, orig: Collection<String>? = null): String = when {
            comboDone(combo, terms) -> "done"
            orig != null && comboDone(combo, orig) -> "void"
            else -> "open"
        }

        /** 凑齐的组合带来的额外危机等级. */
        fun comboLevel(terms: Collection<String>): Int = COMBOS.filter { comboDone(it, terms) }.sumOf { it.level }
        /** 凑齐的组合带来的休息时间乘区. */
        fun comboRest(terms: Collection<String>): Double = COMBOS.filter { comboDone(it, terms) }.fold(1.0) { f, c -> f * c.rest }
        /** 合约开始时凑齐的组合给的休息时间 (秒, 算合约中得到的). */
        fun comboGift(terms: Collection<String>): Int = COMBOS.filter { comboDone(it, terms) }.sumOf { it.gift }
        /** 完成合约时凑齐的组合额外给的休息时间 (秒). */
        fun comboEnd(terms: Collection<String>): Int = COMBOS.filter { comboDone(it, terms) }.sumOf { it.end }

        /** 凑齐了的组合的成员词条 → 颜色 (界面上用这个颜色框住它们). 没凑齐的不算. */
        fun comboMarks(terms: Collection<String>): Map<String, String> {
            val out = LinkedHashMap<String, String>()
            for (c in COMBOS) {
                if (!comboDone(c, terms)) continue
                for (t in terms) {
                    if (t !in BY_ID) continue
                    val hit = c.reqs.any { req -> if (req in GROUPS) BY_ID[t]!!.group == req else t == req }
                    if (hit && t !in out) out[t] = COMBO_COLORS[c.color]!!
                }
            }
            return out
        }

        /** 选了这些词条时的初始目标生命值. */
        fun lifeOf(terms: Collection<String>): Int = terms.mapNotNull { LIFE[it] }.minOrNull() ?: VIOLATION_LIMIT
        /** 支援短缺: 还差这么多秒就用超时各提醒一次. */
        val SUPPLY_WARN = listOf(180, 60)
        /** 纵深推进: 一段专注至少这么多秒, 不到就结束算违反. */
        val DEEP = mapOf("deep1" to 40 * 60, "deep2" to 60 * 60)
        /** 侵蚀: 网络申请的冷静期乘多少 (硬规则). */
        val COOLDOWN_X = mapOf("erode1" to 2.0, "erode2" to 3.0)
        /** 未知变量: 合约的每个小时开始时随机抽一个 1-2 级的词条; 不会选中录像和持久战的词条.
         *  抽中的词条 3 分钟后生效, 只在抽中的那个小时里有效, 这个小时结束时移除 (不算违反); 同时只有一个.
         *  退路封锁只在目标生命值减少后不为 0 时才选. */
        const val RANDOM = "unknown"
        val RANDOM_LEVELS = 1..2
        val RANDOM_SKIP = setOf("gaze", "scar", "long1", "long2")
        /** 抽中后过这么多分钟才生效. */
        const val RANDOM_DELAY_MIN = 3L
        /** 未知变量的每次判定: 有能选的词条时 75% 抽词条, 25% 施加效果; 没有能选的词条时一定是效果.
         *  不选已经选过的词条; 通过违反失效的词条 (开始时选的, 或者未知变量选中的) 可以再回到词条池, 但只会再被选中一次. */
        const val RANDOM_TERM_P = 0.75
        val RANDOM_EFFECTS = listOf("rest_cut" to 15, "lock" to 15, "rest_add" to 15, "double" to 15, "slow" to 10, "none" to 30)
        /** 休息时间 -10 / +10 分钟 (正在休息时改这次休息, 否则改积攒的). */
        const val RANDOM_REST = 10 * 60
        /** 阻滞: 下一次专注的时间按 90% 增长. */
        const val RANDOM_SLOW = 0.9
        /** 一个小时里最多判定这么多次 ("再判定两次"连着抽到时). */
        const val RANDOM_ROLLS = 9

        /** 一次判定的效果: 按 RANDOM_EFFECTS 的权重. */
        fun effect(dice: Dice): String {
            var r = dice.roll() * RANDOM_EFFECTS.sumOf { it.second }
            for ((eff, w) in RANDOM_EFFECTS) {
                if (r < w) return eff
                r -= w
            }
            return RANDOM_EFFECTS.last().first
        }
        /** 被违反到失效的词条 (降到最低一档后又违反了) 照样检查: 再次触发只响警报, 不扣目标生命值;
         *  每个词条这么多秒最多响一次. */
        const val ECHO_COOLDOWN = 120L

        /** 总等级 → 完成合约时给的休息时间 (分钟, 固定; 按结束时的等级). */
        // 0 级 20 分钟, 38 级起封顶 90 分钟; 中间每一级按直线填充, 四舍五入到整分钟
        private const val REST_MIN = 20
        private const val REST_MAX = 90
        private const val REST_TOP = 38

        /** 乐理之灾: 常见的音乐播放应用 (包名). 视频播放器 (VLC, MX Player …) 不算. */
        val MUSIC_APPS = setOf(
            "com.netease.cloudmusic", "com.tencent.qqmusic", "com.kugou.android", "com.kugou.android.lite",
            "cn.kuwo.player", "cn.kuwo.kwmusichd", "com.spotify.music", "com.apple.android.music",
            "com.google.android.apps.youtube.music", "com.miui.player", "com.android.music",
            "com.sec.android.app.music", "com.huawei.music", "com.hihonor.cloudmusic", "com.android.mediacenter",
            "com.oppo.music", "com.heytap.music", "com.android.bbkmusic", "com.meizu.media.music",
            "com.tencent.blackkey", "cmccwm.mobilemusic", "com.luna.music", "com.ximalaya.ting.android",
            "com.soundcloud.android", "com.aspiro.tidal", "deezer.android.app", "com.amazon.mp3",
            "in.krosbits.musicolet", "com.maxmpz.audioplayer", "com.poweramp.v3",
            "com.foobar2000.foobar2000", "com.salt.music", "com.kanjian.music",
        )

        /** 无言为真: 常见的社交应用和与 AI 聊天的应用 (包名). */
        val SOCIAL_APPS = setOf(
            "com.tencent.mm", "com.tencent.mobileqq", "com.tencent.tim", "com.tencent.qqlite", "com.sina.weibo",
            "com.sina.weibolite", "com.zhihu.android", "com.xingin.xhs", "com.baidu.tieba", "com.douban.frodo",
            "org.telegram.messenger", "org.telegram.messenger.web", "com.discord", "com.whatsapp",
            "com.facebook.katana", "com.facebook.orca", "com.facebook.lite", "com.instagram.android",
            "com.twitter.android", "com.reddit.frontpage", "jp.naver.line.android", "com.skype.raider",
            "org.thoughtcrime.securesms", "com.kakao.talk", "com.instagram.barcelona", "com.soul.android",
            "com.openai.chatgpt", "com.anthropic.claude", "com.deepseek.chat", "com.larus.nova",
            "com.moonshot.kimichat", "com.tencent.hunyuan.app.chat", "com.aliyun.tongyi", "com.baidu.newapp",
            "com.google.android.apps.bard", "com.microsoft.copilot", "ai.perplexity.app.android", "com.poe.android",
            "ai.x.grok", "com.quark.browser.ai",
        )

        /** 禁游令: 常见的游戏和游戏平台 (包名). */
        val GAME_APPS = setOf(
            "com.tencent.tmgp.sgame", "com.tencent.tmgp.pubgmhd", "com.tencent.lolm", "com.tencent.jkchess",
            "com.tencent.tmgp.cf", "com.tencent.tmgp.speedmobile", "com.tencent.letsgo", "com.tencent.mf.uam",
            "com.tencent.tmgp.dnf", "com.tencent.af", "com.tencent.ig", "com.miHoYo.Yuanshen", "com.miHoYo.hkrpg",
            "com.miHoYo.Nap", "com.miHoYo.bh3", "com.miHoYo.GenshinImpact", "com.HoYoverse.hkrpgoversea",
            "com.HoYoverse.Nap", "com.kurogame.mingchao", "com.hypergryph.arknights", "com.bilibili.fatego",
            "com.netease.onmyoji", "com.netease.party", "com.netease.mc", "com.netease.hyxd", "com.netease.sky",
            "com.netease.dwrg", "com.netease.l22", "com.mojang.minecraftpe", "com.roblox.client",
            "com.supercell.clashofclans", "com.supercell.clashroyale", "com.supercell.brawlstars",
            "com.activision.callofduty.shooter", "com.dts.freefireth", "com.innersloth.spacemafia",
            "com.papegames.infinitynikki", "com.papegames.lysk", "com.lilithgame.roc.gp", "com.taptap",
            "com.taptap.global", "com.valvesoftware.android.steam.community", "com.huawei.gamebox",
            "com.xiaomi.gamecenter", "com.vivo.game", "com.nearme.gamecenter", "com.hihonor.gamecenter",
            "com.netease.gl", "com.yostar.aethergazer", "com.tencent.nrc", "com.gameloft.android.ANMP.GloftA9HM",
        )

        /** 低帧的诅咒: 常见的视频平台 (包名). 本地的视频播放器 (VLC, MX Player …) 不算. */
        val VIDEO_APPS = setOf(
            "tv.danmaku.bili", "com.bilibili.app.in", "tv.danmaku.bilibilihd", "com.bilibili.app.blue",
            "com.google.android.youtube", "app.revanced.android.youtube", "com.youku.phone", "com.tencent.qqlive",
            "com.qiyi.video", "com.qiyi.video.lite", "com.hunantv.imgo.activity", "com.ss.android.article.video",
            "com.ss.android.ugc.aweme", "com.ss.android.ugc.aweme.lite", "com.ss.android.ugc.live",
            "com.smile.gifmaker", "com.kuaishou.nebula", "com.zhiliaoapp.musically", "com.ss.android.ugc.trill",
            "air.tv.douyu.android", "com.duowan.kiwi", "tv.twitch.android.app", "com.netflix.mediaclient",
            "tv.acfundanmaku.video", "com.baidu.haokan", "com.tencent.weishi", "com.mgtv.tv",
        )

        /** 网络禁令也封锁这一类的网站 (MECHANICS 18.3): 域名本身和它的子域名; 优先级最高, 联网申请也解除不了. 同 contract.py 的 BAN_DOMAINS. */
        val BAN_DOMAINS: Map<String, Set<String>> = mapOf(
            "music" to setOf(
                "music.163.com", "y.qq.com", "kugou.com", "kuwo.cn", "music.migu.cn", "qianqian.com", "ximalaya.com",
                "spotify.com", "music.apple.com", "music.youtube.com", "soundcloud.com", "deezer.com", "tidal.com",
                "music.amazon.com", "pandora.com",
            ),
            "silent" to setOf(
                "weibo.com", "weibo.cn", "zhihu.com", "xiaohongshu.com", "tieba.baidu.com", "douban.com",
                "qzone.qq.com", "wx.qq.com", "web.wechat.com", "twitter.com", "x.com", "facebook.com",
                "instagram.com", "threads.net", "reddit.com", "discord.com", "telegram.org", "t.me",
                "web.whatsapp.com", "line.me", "chatgpt.com", "chat.openai.com", "claude.ai", "chat.deepseek.com",
                "gemini.google.com", "kimi.moonshot.cn", "kimi.com", "doubao.com", "yuanbao.tencent.com",
                "tongyi.aliyun.com", "tongyi.com", "yiyan.baidu.com", "copilot.microsoft.com", "poe.com",
                "perplexity.ai", "grok.com", "character.ai",
            ),
            "game" to setOf(
                "steampowered.com", "steamcommunity.com", "epicgames.com", "wegame.com.cn", "ea.com", "battle.net",
                "blizzard.com", "ubisoft.com", "gog.com", "riotgames.com", "leagueoflegends.com", "mihoyo.com",
                "hoyoverse.com", "taptap.cn", "taptap.io", "4399.com", "7k7k.com", "roblox.com", "minecraft.net",
                "poki.com", "crazygames.com", "itch.io", "xbox.com", "playstation.com",
            ),
            "video" to setOf(
                "bilibili.com", "b23.tv", "youtube.com", "youtu.be", "youku.com", "v.qq.com", "iqiyi.com",
                "mgtv.com", "ixigua.com", "douyin.com", "kuaishou.com", "tiktok.com", "douyu.com", "huya.com",
                "twitch.tv", "netflix.com", "acfun.cn", "nicovideo.jp", "haokan.baidu.com", "weishi.qq.com",
                "channels.weixin.qq.com",
            ),
        )

        /** 一类应用 (BANS 之一) 的包名. */
        fun banApps(kind: String): Set<String> = when (kind) {
            "music" -> MUSIC_APPS
            "silent" -> SOCIAL_APPS
            "game" -> GAME_APPS
            "video" -> VIDEO_APPS
            else -> emptySet()
        }

        fun levelOf(terms: Collection<String>) =
            terms.sumOf { BY_ID[it]?.level ?: 0 } + comboLevel(terms)

        fun restMinutes(level: Int): Int {
            val lv = level.coerceIn(0, REST_TOP)
            return REST_MIN + (2 * (REST_MAX - REST_MIN) * lv + REST_TOP) / (2 * REST_TOP)   // 四舍五入 (.5 进位)
        }
        /** 完成合约时存进积攒的休息时间的秒数 (按等级查表 + 凑齐的组合的结束后收益). */
        fun restReward(level: Int, terms: Collection<String> = emptyList()): Int = restMinutes(level) * 60 + comboEnd(terms)
        /** 完成合约时休息时间一共多少分钟 (界面上写 "完成后休息 +N 分钟"). */
        fun restMinutesTotal(level: Int, terms: Collection<String> = emptyList()): Int = restReward(level, terms) / 60
        /** 合约开始时存进积攒的休息时间的秒数 (凑齐的组合给的, 算合约中得到的). */
        fun restGift(terms: Collection<String>): Int = comboGift(terms)

        fun durationOf(terms: Collection<String>): Double =
            BASE_SECONDS * (terms.mapNotNull { DURATION[it] }.maxOrNull() ?: 1.0)

        fun conflicts(a: String, b: String): Boolean {
            if (a == b) return false
            val ta = BY_ID[a] ?: return false
            val tb = BY_ID[b] ?: return false
            return ta.group == tb.group || b in ta.conflicts || a in tb.conflicts
        }

        /** 选了 selected 之后, id 和其中哪一个冲突 (没有冲突返回 null). */
        fun blockedBy(id: String, selected: Collection<String>): String? =
            selected.firstOrNull { it != id && conflicts(id, it) }

        /** 去掉不认识的和互相冲突的 (后面的让位给前面的), 按 TERMS 的顺序. */
        fun clean(terms: Collection<String>): List<String> {
            val out = mutableListOf<String>()
            for (t in terms) if (t in BY_ID && t !in out && blockedBy(t, out) == null) out.add(t)
            return out.sortedBy { ORDER[it] }
        }

        /** 统计页用: 最近 n 个顺利完成的合约 (按结束时刻从新到旧). */
        fun recentCompleted(history: List<JSONObject>, n: Int = 5): List<JSONObject> =
            history.filter { it.optString("result") == "completed" && it.optString("end").isNotEmpty() }
                .sortedByDescending { it.optString("end") }
                .take(maxOf(0, n))

        /** 统计页看一个合约的词条: [(名字, 是不是失效了)], 按开始时的顺序; 降级的按降级后的名字. */
        fun termsOfRecord(rec: JSONObject): List<Pair<String, Boolean>> {
            val last = HashMap<String, String>()
            for (t in rec.optJSONArray("terms")?.strings() ?: emptyList()) {
                BY_ID[t]?.let { last[it.group] = t }
            }
            val out = mutableListOf<Pair<String, Boolean>>()
            for (t in rec.optJSONArray("orig")?.strings() ?: emptyList()) {
                val spec = BY_ID[t] ?: continue
                val ft = last[spec.group]
                out.add((BY_ID[ft ?: t]?.name ?: spec.name) to (ft == null))
            }
            return out
        }

        /** 完成合约后的第一次休息: 暂停时的结转比例 1 - (1 - x) / 2. */
        fun firstBreakRate(x: Double): Double = 1.0 - (1.0 - x.coerceIn(0.0, 1.0)) / 2.0
    }

    var live: JSONObject = JSONObject().put("state", "none")
        private set
    val history = mutableListOf<JSONObject>()
    var draft: List<String> = emptyList()
        private set
    /** 重置中: 不写文件. */
    var blocked: () -> Boolean = { false }
    /** 时间重叠的两个合约只能留一个 (MECHANICS 15.5): 作废了的合约 id (随同步); 等用户选的冲突 (只在本机, first 是本机原来的). */
    val voided = mutableSetOf<String>()
    val conflicts = mutableListOf<Pair<JSONObject, JSONObject>>()

    init {
        val d = Store.readObj(file)
        if (d != null) {
            d.optJSONObject("live")?.let { if (it.optString("state") in setOf("active", "none")) live = it }
            if (active) live.put("terms", JSONArray(clean(live.optJSONArray("terms")?.strings() ?: emptyList())))
            d.optJSONArray("history")?.objects()?.filter { it.has("start") }?.let { history.addAll(it) }
            draft = clean(d.optJSONArray("draft")?.strings() ?: emptyList())
            d.optJSONArray("void")?.strings()?.let { voided.addAll(it) }
            d.optJSONArray("conflicts")?.let { arr ->
                for (i in 0 until arr.length()) {
                    val p = arr.optJSONArray(i) ?: continue
                    val a = p.optJSONObject(0) ?: continue
                    val b = p.optJSONObject(1) ?: continue
                    conflicts.add(a to b)
                }
            }
        }
    }

    fun save() {
        if (blocked()) return
        history.sortBy { it.optString("end") }
        while (history.size > HISTORY_KEEP) history.removeAt(0)
        Store.write(file, JSONObject().put("live", live).put("history", JSONArray(history)).put("draft", JSONArray(draft))
            .put("void", JSONArray(voided.sorted()))
            .put("conflicts", JSONArray(conflicts.map { JSONArray().put(it.first).put(it.second) })))
    }

    // ---- 进行中的合约 ----

    val active get() = live.optString("state") == "active"
    val terms: List<String> get() = if (active) live.optJSONArray("terms")?.strings() ?: emptyList() else emptyList()
    fun has(id: String) = id in terms
    /** 被违反到失效的词条 (失效前的最低一档; 不算未知变量选中的和录像). */
    val lost: List<String> get() = if (active) (live.optJSONArray("lost")?.strings() ?: emptyList()).filter { it in BY_ID } else emptyList()
    /** 这个词条要不要检查: 选着, 或者已经被违反到失效. */
    fun watching(id: String) = has(id) || id in lost
    /** 触发的是已经失效的词条: 只响警报, 不扣目标生命值. */
    fun echo(id: String) = id.isNotEmpty() && !has(id) && id in lost
    private val echoAt = HashMap<String, LocalDateTime>()
    /** 已失效的词条再次触发的警报: 每个词条 ECHO_COOLDOWN 秒最多响一次 (只记在本机). */
    fun echoDue(id: String, now: LocalDateTime = LocalDateTime.now()): Boolean {
        val last = echoAt[id]
        if (last != null && secondsBetween(last, now) < ECHO_COOLDOWN) return false
        echoAt[id] = now
        return true
    }
    /** 已经失效的词条里属于 table 的那个 (这一类现在没有选着的). */
    private fun lostOf(table: Map<String, *>): String =
        if (terms.any { it in table }) "" else lost.firstOrNull { it in table } ?: ""
    val startAt get() = if (active) parseIso(live.optString("start")) else null
    val endAt get() = if (active) parseIso(live.optString("end")) else null
    /** 只有一档的词条被降了几级. */
    private val cut: JSONObject get() = (if (active) live.optJSONObject("cut") else null) ?: JSONObject()
    fun termLevel(id: String) = (BY_ID[id]?.level ?: 0) - cut.optInt(id, 0)
    /** 未知变量随机启用的词条 (不算进危机等级; 可以点击关闭, 算一次违反). */
    val rand: List<String> get() = if (active) (live.optJSONArray("rand")?.strings() ?: emptyList()).filter { it in terms } else emptyList()
    /** 算进危机等级的词条 (开始时选的现在还在的, 降级后算现在的; 不算未知变量选中的). 词条组合只看这些. */
    val mine get() = rand.let { r -> terms.filter { it !in r } }
    val level get() = mine.let { m -> m.sumOf { termLevel(it) } + comboLevel(m) }
    /** 开始时选的词条 (降级前). */
    val orig: List<String> get() = if (active) clean(live.optJSONArray("orig")?.strings() ?: terms) else emptyList()
    val origLevel get() = levelOf(orig)
    val violations get() = if (active) live.optInt("violations", 0) else 0
    /** 初始目标生命值. */
    val violationLimit get() = lifeOf(terms)
    /** 目标生命值还剩多少 (每违反一次减一, 减到 0 合约失败). */
    val life get() = maxOf(0, violationLimit - violations)
    /** 合约中已经给出的休息时间 (秒, 已乘疲惫). */
    val rest get() = if (active) live.dbl("rest") else 0.0
    /** 完成时给的休息时间 (秒): 按现在的等级查表, 加上凑齐的组合的结束后收益; 结算按降级后的等级. */
    val reward get() = if (active) restReward(level, mine).toDouble() else 0.0
    val owner: String get() = live.optString("owner", "")
    val id: Long get() = live.optLong("id", 0)

    fun setDraft(terms: Collection<String>) {
        draft = clean(terms)
        save()
    }

    /** pre: 开始时积攒的休息时间 (秒). 合约中暂停休息时这部分不打折; 支援短缺限制它能用几成. */
    fun start(terms: Collection<String>, owner: String, now: LocalDateTime = LocalDateTime.now(), pre: Double = 0.0) {
        val t = clean(terms)
        val n = now.withNano(0)
        val p = maxOf(0.0, pre)
        val share = supplyShare(t)
        live = JSONObject().put("state", "active").put("id", System.currentTimeMillis())
            .put("start", n.iso()).put("end", n.plusSec(durationOf(t)).iso())
            .put("terms", JSONArray(t)).put("orig", JSONArray(t)).put("cut", JSONObject())
            .put("violations", 0).put("rest", 0).put("owner", owner)
            .put("idle_since", JSONObject.NULL).put("net", JSONObject.NULL).put("first_break", false)
            .put("pre", r1(p)).put("pre_left", r1(p)).put("pre_used", 0)
            .put("supply", if (share == null) JSONObject.NULL else r1(p * share)).put("hl", JSONArray())
            .put("rand", JSONArray()).put("rand_n", 0)
        watch.clear()
        save()
    }

    /** 结束合约: result = completed / failed / idle / abandoned. 返回这一条记录 (含给出的休息时间). */
    fun finish(result: String, at: LocalDateTime? = null, by: String = ""): JSONObject? {
        if (!active) return null
        val now = (at ?: LocalDateTime.now()).withNano(0)
        val plan = endAt ?: now
        val end = if (now.isBefore(plan)) now else plan
        val give = if (result == "completed") Math.round(reward).toInt() else 0
        val rec = JSONObject().put("id", live.optLong("id")).put("start", live.optString("start")).put("end", end.iso())
            .put("planned", live.optString("end")).put("terms", JSONArray(terms)).put("orig", JSONArray(orig))
            .put("level", level).put("orig_level", origLevel).put("cut", JSONObject(cut.toString()))
            .put("result", result).put("violations", violations).put("reward", give).put("pre_left", preLeft)
            .put("rand", JSONArray(rand))
        live.optJSONArray("locks")?.let { if (it.length() > 0) rec.put("locks", JSONArray(it.toString())) }   // 封锁的记录格合约结束后照样锁着
        live.optJSONObject("slow")?.optString("start")?.takeIf { it.isNotEmpty() }?.let { rec.put("slow", it) }
        history.add(rec)
        live = JSONObject().put("state", "none").put("owner", by).put("first_break", result == "completed")
        save()
        return rec
    }

    /** 最近结束的一个合约. */
    val last: JSONObject? get() = history.maxByOrNull { it.optString("end") }

    // ---- 到期 / 空闲 ----

    /** 该结束了吗: ("completed" / "idle", 结束的时刻) 或 null. 同时记下空闲从什么时候开始 (只由做自动结束的设备调用). */
    fun due(now: LocalDateTime, timerIdle: Boolean): Pair<String, LocalDateTime>? {
        if (!active) return null
        val end = endAt ?: return null
        var since = parseIso(live.strOrNull("idle_since"))
        if (timerIdle) {
            if (since == null) {
                since = now
                live.put("idle_since", now.iso())
                save()
            }
        } else if (since != null) {
            live.put("idle_since", JSONObject.NULL)
            save()
            since = null
        }
        if (since != null) {
            val fail = since.plusSec(IDLE_LIMIT)
            if (!fail.isAfter(if (now.isBefore(end)) now else end)) return "idle" to fail
        }
        if (!now.isBefore(end)) return "completed" to end
        return null
    }

    /** 设备离线超过 OFFLINE_LIMIT: (设备名, 失败的时刻) 或 null. devices: (名字, 离线开始的时刻);
     *  离线从合约开始, 以及 floor (本机这次打开程序的时刻: 本机关着时的事由其他设备判定) 之后才算. */
    fun offlineDue(devices: List<Pair<String, LocalDateTime>>, now: LocalDateTime, floor: LocalDateTime): Pair<String, LocalDateTime>? {
        val a = startAt ?: return null
        for ((name, s) in devices) {
            val since = listOf(s, a, floor).max()
            val fail = since.plusSec(OFFLINE_LIMIT)
            if (!fail.isAfter(now)) return name to fail
        }
        return null
    }

    /** 空闲还能持续多久 (秒); 不在空闲时 null. */
    fun idleLeft(now: LocalDateTime): Double? {
        val since = if (active) parseIso(live.strOrNull("idle_since")) else null
        return since?.let { maxOf(0.0, IDLE_LIMIT - secondsBetween(it, now)) }
    }

    // ---- 网络禁令: 乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒 (MECHANICS 18.3) ----

    /** 现在禁止联网的几类 (BANS 之一; 未知变量选中的也算). */
    fun netBans(): List<String> = if (active) BANS.filter { has(it) } else emptyList()

    /** 现在禁止联网的应用 (包名). */
    fun bannedApps(): Set<String> = netBans().flatMap { banApps(it) }.toSet()

    /** 现在封锁的网站 (域名, 子域名也算). */
    fun bannedDomains(): Set<String> = netBans().flatMap { BAN_DOMAINS[it] ?: emptySet() }.toSet()

    /** 点击词条可以解除网络禁令: 开始时选的 (未知变量选中的照旧是点击关闭). */
    fun canLift(id: String): Boolean = active && id in BANS && has(id) && id !in rand

    /** 解除网络禁令扣多少目标生命值: 词条现在的等级. */
    fun liftCost(id: String): Int = maxOf(1, termLevel(id))

    /** 解除网络禁令: 扣掉 liftCost 点目标生命值, 去掉这个词条 (组合里算成员失效; 不算违反, 也不会再响警报).
     *  返回 (是否达到了上限, 扣了几点); 不能解除时 (false, 0). 同 contract.py 的 lift_ban. */
    fun liftBan(id: String): Pair<Boolean, Int> {
        if (!canLift(id)) return false to 0
        val n = liftCost(id)
        downgrade(id, drop = true)
        live.put("violations", violations + n)
        save()
        return (violations >= violationLimit) to n
    }

    /** 违反了词条 id: 记一次, 这个词条降一级 (drop: 直接去掉, 关闭未知变量选中的词条).
     *  返回 (是否达到了上限, 降级后的词条 / "" = 失效). id = "": 不是哪个词条的违反 (整点记录超时未填写), 只记一次. */
    fun violate(id: String, drop: Boolean = false): Pair<Boolean, String> {
        if (!active) return false to ""
        val new = if (id.isEmpty()) "" else downgrade(id, drop)
        live.put("violations", violations + 1)
        save()
        return (violations >= violationLimit) to new
    }

    /** 词条降一级: 系列词条换成低一档的, 只有一档的等级减一; 降到底就失效 (drop: 直接去掉).
     *  返回降级后的词条 ("" = 失效). */
    fun downgrade(id: String, drop: Boolean = false): String {
        val t = terms.toMutableList()
        val i = t.indexOf(id)
        if (i < 0) return ""
        val oldShare = netShare()
        val oldSupply = supplyShare(t)
        val oldRand = rand
        val c = JSONObject(cut.toString())
        val new: String
        when {
            drop -> { t.removeAt(i); c.remove(id); new = "" }
            id in LOWER -> { new = LOWER[id]!!; t[i] = new }
            id in SINGLE && termLevel(id) > 1 -> { c.put(id, c.optInt(id, 0) + 1); new = id }
            else -> { t.removeAt(i); c.remove(id); new = "" }
        }
        live.put("terms", JSONArray(clean(t))).put("cut", c)
        val gone = new.isEmpty() && !drop && id !in oldRand && id !in RECORD
        if (gone && id !in lost) live.put("lost", JSONArray(lost + id))   // 失效了照样检查, 再次触发只响警报
        if (new.isEmpty() && id in oldRand) {        // 未知变量选中的词条因为违反 (或关闭) 失效: 可以再回到词条池一次
            val viol = live.optJSONArray("rand_viol")?.strings() ?: emptyList()
            if (id !in viol) live.put("rand_viol", JSONArray(viol + id))
        }
        // 未知变量选中的词条降级后还是它选中的 (移除的时刻不变)
        if (id in oldRand) {
            live.put("rand", JSONArray(oldRand.mapNotNull { if (it == id) new.ifEmpty { null } else it }))
            live.optJSONObject("rand_until")?.let { till ->
                val at = till.strOrNull(id)
                till.remove(id)
                if (new.isNotEmpty() && at != null) till.put(new, at)
            }
        }
        // 信息阻断: 降一级 = 多给低一档多出来的那部分额度 (从现在用到的地方算起)
        val n = live.optJSONObject("net")
        val newShare = netShare()
        if (n != null && oldShare != null && newShare != null)
            n.put("limit", n.dbl("limit") + n.dbl("base") * (newShare - oldShare))
        // 支援短缺: 同样多给低一档多出来的那部分; 失效了就不再限制
        val newSupply = supplyShare(terms)
        if (newSupply == null) {
            if (gone && id in SUPPLY && !live.isNull("supply")) live.put("supply_lost", live.dbl("supply"))   // 失效时的上限留着
            live.put("supply", JSONObject.NULL)
        }
        else if (oldSupply != null && !live.isNull("supply"))
            live.put("supply", r1(live.dbl("supply") + pre * (newSupply - oldSupply)))
        save()
        return new
    }

    // ---- 未知变量: 合约的第 1, 2, 3 … 个小时开始时 (刚开始时也算) 随机启用一个词条 ----

    /** 现在能被未知变量选中的词条: 1-2 级, 能选 (没有选着, 也不和选着的冲突), 不是录像;
     *  退路封锁不会让目标生命值直接归零. 不选已经选过的; 通过违反失效的词条可以再选中一次
     *  (选中期间违反照常扣目标生命值). */
    fun randomCandidates(): List<String> {
        val t = terms + waiting.keys
        val used = (live.optJSONArray("rand_used")?.strings() ?: emptyList()).toSet()
        val viol = lost.toSet() + (live.optJSONArray("rand_viol")?.strings() ?: emptyList())
        val again = (live.optJSONArray("rand_again")?.strings() ?: emptyList()).toSet()
        return TERMS.map { it.id }.filter { id ->
            ((id !in used && id !in viol) || (id in viol && id !in again)) &&
                BY_ID[id]!!.level in RANDOM_LEVELS && id !in RANDOM_SKIP && id !in t && blockedBy(id, t) == null &&
                !(id in LIFE && lifeOf(terms + id) <= violations)
        }
    }

    /** 到了新的一个小时就判定一次: 抽一个词条 (3 分钟后生效 (wake), 到这个小时结束时移除) 或者施加一个效果.
     *  返回这次的结果 (种类, 值): ("term", 词条) / ("rest_cut", "") / ("rest_add", "") / ("lock", 封锁的格子键或 "")
     *  / ("double", "") / ("slow", "") / ("none", ""); 休息时间的增减由调用方做 (只由做自动结束的设备调用).
     *  程序关着时错过的小时依次补上 (那时抽中的词条到了它那个小时的结束就移除, 不再返回). */
    fun roll(now: LocalDateTime, dice: Dice = Dice(), textOf: ((String) -> String)? = null, auto: String = "",
             fillSec: Long = 0, autoOf: ((String) -> String)? = null): List<Pair<String, String>> {
        if (!active || !has(RANDOM)) return emptyList()
        val a = startAt ?: return emptyList()
        val end = endAt ?: now
        val due = if (now.isBefore(end)) (secondsBetween(a, now) / 3600).toInt() + 1 else 0
        var done = live.optInt("rand_n", 0)
        val out = mutableListOf<Pair<String, String>>()
        while (done < due) {
            done++
            val at = a.plusHours((done - 1).toLong())
            expire(at)
            var n = 1
            var k = 0
            while (n > 0 && k < RANDOM_ROLLS) {
                n--; k++
                val pool = randomCandidates()
                if (pool.isNotEmpty() && dice.roll() < RANDOM_TERM_P) {
                    val id = pool[dice.pick(pool.size)]
                    pick(id, at)
                    out.add("term" to id)
                    continue
                }
                when (val eff = effect(dice)) {
                    "double" -> { n += 2; out.add(eff to "") }
                    "slow" -> { live.put("slow", JSONObject().put("pending", true)); out.add(eff to "") }
                    "lock" -> out.add(eff to lockCell(now, dice, textOf, auto, fillSec, autoOf))
                    else -> out.add(eff to "")
                }
            }
            live.put("rand_n", done)
            save()
        }
        expire(now)
        val w = waiting
        return out.filter { it.first != "term" || it.second in w }
    }

    /** 未知变量在 at 开始的这个小时抽中了 id: 记下选过 (通过违反失效后再选中的记下用掉了这一次). */
    fun pick(id: String, at: LocalDateTime) {
        val viol = lost.toSet() + (live.optJSONArray("rand_viol")?.strings() ?: emptyList())
        val used = live.optJSONArray("rand_used")?.strings() ?: emptyList()
        if (id in viol) live.put("rand_again", JSONArray((live.optJSONArray("rand_again")?.strings() ?: emptyList()) + id))
        if (id !in used) live.put("rand_used", JSONArray(used + id))
        putWaiting(waiting + (id to (at.plusMinutes(RANDOM_DELAY_MIN) to at.plusHours(1))))
    }

    // ---- 未知变量的效果 ----

    /** 随机封锁一个还没被封锁, 已经过了填写时限的整点记录格 (最近 24 小时里的; 合约结束后照样锁着).
     *  返回格子的键 ("日期/小时", 细化规范的半小时 "日期/小时/1"); 没有可以封锁的返回 "". */
    fun lockCell(now: LocalDateTime, dice: Dice = Dice(), textOf: ((String) -> String)? = null, auto: String = "",
                 fillSec: Long = 0, autoOf: ((String) -> String)? = null): String {
        if (!active || textOf == null || fillSec <= 0) return ""
        var h = now.minusHours(24).withMinute(0).withSecond(0).withNano(0)
        val pool = mutableListOf<Triple<String, LocalDateTime, LocalDateTime>>()
        while (!h.plusHours(1).isAfter(now)) {
            val key = "${h.toLocalDate()}/${h.hour}"
            val cells = if (fineHour(h)) listOf(Triple("$key/1", h, h.plusMinutes(30)), Triple("$key/2", h.plusMinutes(30), h.plusHours(1)))
                else listOf(Triple(key, h, h.plusHours(1)))
            for (c in cells) {
                if (!c.third.plusSeconds(cellFill(c.second, c.third, fillSec)).isAfter(now) &&
                    !hourLocked(c.second, c.third, textOf(c.first), now, fillSec, auto, autoOf, c.first)) pool.add(c)
            }
            h = h.plusHours(1)
        }
        if (pool.isEmpty()) return ""
        val (k, a, b) = pool[dice.pick(pool.size)]
        val locks = live.optJSONArray("locks") ?: JSONArray()
        live.put("locks", JSONArray(locks.toString()).put(JSONArray(listOf(a.iso(), b.iso()))))
        save()
        return k
    }

    /** 未知变量封锁的记录格 (所有合约的). */
    fun locks(): List<Pair<LocalDateTime, LocalDateTime>> {
        val srcs = history.mapNotNull { it.optJSONArray("locks") } + listOfNotNull(if (active) live.optJSONArray("locks") else null)
        return srcs.flatMap { arr ->
            (0 until arr.length()).mapNotNull { i ->
                val v = arr.optJSONArray(i) ?: return@mapNotNull null
                val a = parseIso(v.optString(0, "")) ?: return@mapNotNull null
                val b = parseIso(v.optString(1, "")) ?: return@mapNotNull null
                a to b
            }
        }
    }

    /** 开始专注: 等着的阻滞落到这段专注上 (专注时间按 RANDOM_SLOW 增长). 返回落上了没有.
     *  阻滞只对合约内的专注生效: 合约已经结束 / 这段专注开始于合约结束之后时不落上. */
    fun slowBind(start: LocalDateTime): Boolean {
        if (!slowPending) return false
        val e = endAt ?: return false
        if (!start.isBefore(e)) return false
        live.put("slow", JSONObject().put("start", start.iso()))
        save()
        return true
    }

    val slowPending: Boolean get() = active && live.optJSONObject("slow")?.optBoolean("pending") == true

    /** 阻滞让这段专注按 RANDOM_SLOW 增长到什么时候 (合约结束的时刻); 没有阻滞时 null. */
    fun slowUntil(start: LocalDateTime?): LocalDateTime? {
        val mark = start?.iso() ?: return null
        if (active && live.optJSONObject("slow")?.optString("start") == mark) return endAt
        return history.takeLast(3).firstOrNull { it.optString("slow") == mark }?.let { parseIso(it.optString("end")) }
    }

    /** 开始于 start 的这段专注现在按多少增长: 阻滞 RANDOM_SLOW (只到合约结束), 之后 1. */
    fun focusRate(start: LocalDateTime?, now: LocalDateTime = LocalDateTime.now()): Double {
        val until = slowUntil(start) ?: return 1.0
        return if (now.isBefore(until)) RANDOM_SLOW else 1.0
    }

    /** 这一段专注计入的分钟数: 阻滞只减慢合约内的那部分时间, 合约结束后的部分按正常速度 (MECHANICS 18.3). */
    fun focusMinutes(start: LocalDateTime?, seconds: Double): Double {
        val s = maxOf(0.0, seconds)
        val until = slowUntil(start) ?: return s / 60.0
        if (start == null) return s / 60.0
        val left = maxOf(0.0, secondsBetween(start, until))     // 合约里还剩多少秒
        val inside = minOf(s, left)
        return (inside * RANDOM_SLOW + (s - inside)) / 60.0
    }

    /** 积攒的休息时间减少到 carry: 合约开始前的那部分最多剩这么多 (先减合约中得到的). */
    fun preCap(carry: Double) {
        if (active && preLeft > carry) {
            live.put("pre_left", r1(maxOf(0.0, carry)))
            save()
        }
    }

    /** 未知变量抽中了, 还没生效的词条 → (生效的时刻, 移除的时刻). */
    val waiting: Map<String, Pair<LocalDateTime, LocalDateTime>> get() {
        val w = (if (active) live.optJSONObject("rand_wait") else null) ?: return emptyMap()
        val out = linkedMapOf<String, Pair<LocalDateTime, LocalDateTime>>()
        for (k in w.keys()) {
            val v = w.optJSONArray(k) ?: continue
            val a = parseIso(v.optString(0, "")) ?: continue
            val b = parseIso(v.optString(1, "")) ?: continue
            if (k in BY_ID) out[k] = a to b
        }
        return out
    }

    private fun putWaiting(w: Map<String, Pair<LocalDateTime, LocalDateTime>>) {
        val o = JSONObject()
        w.forEach { (k, v) -> o.put(k, JSONArray(listOf(v.first.iso(), v.second.iso()))) }
        live.put("rand_wait", o)
    }

    /** 抽中的词条到了生效的时刻就启用 (到它那个小时结束时移除). 返回启用的词条. */
    fun wake(now: LocalDateTime): List<String> {
        val w = waiting
        val due = w.filter { !it.value.first.isAfter(now) }.keys
        if (due.isEmpty()) return emptyList()
        putWaiting(w.filterKeys { it !in due })
        val out = mutableListOf<String>()
        for (id in due) {
            val until = w[id]!!.second
            if (now.isBefore(until) && id !in terms && blockedBy(id, terms) == null) {
                enable(id, until); out.add(id)
            }
        }
        save()
        return out
    }

    /** 合约中途启用一个词条: 开始时才定下来的效果在这里补上 (支援短缺: 能用多少; 信息阻断: 重新记下额度).
     *  until: 到这个时刻移除. */
    fun enable(id: String, until: LocalDateTime? = null) {
        val t = clean(terms + id)
        live.put("terms", JSONArray(t)).put("rand", JSONArray(rand + id))
        if (id in HOUR_TERMS) live.put("hour_forced", true)   // 整点记录在这次合约中一直开启 (词条移除后也是)
        val till = JSONObject((live.optJSONObject("rand_until") ?: JSONObject()).toString())
        till.remove(id)
        if (until != null) till.put(id, until.iso())
        live.put("rand_until", till)
        if (id in SUPPLY) live.put("supply", r1(maxOf(pre * (supplyShare(t) ?: 1.0), preUsed)))
        if (id in NET_SHARE) live.remove("net")       // 之前选中过又移除了: 按现在的剩余额度重新算
        save()
    }

    /** 未知变量选中的这个词条什么时候移除 (选中的那个小时结束时); 一直有效时 null. */
    fun randUntil(id: String): LocalDateTime? =
        if (id in rand) parseIso(live.optJSONObject("rand_until")?.strOrNull(id)) else null

    /** 未知变量选中的词条: 那个小时结束了就移除 (不算违反; 还没生效的也去掉). 返回移除的 (已生效的) 词条. */
    fun expire(now: LocalDateTime): List<String> {
        val w = waiting
        if (w.values.any { !it.second.isAfter(now) }) {
            putWaiting(w.filterValues { it.second.isAfter(now) })
            save()
        }
        val till = (if (active) live.optJSONObject("rand_until") else null) ?: return emptyList()
        val gone = till.keys().asSequence().toList().filter { k -> parseIso(till.strOrNull(k))?.isAfter(now) != true }
        if (gone.isEmpty()) return emptyList()
        val r = rand
        val out = gone.filter { it in r }
        live.put("terms", JSONArray(terms.filter { it !in out })).put("rand", JSONArray(r.filter { it !in out }))
        gone.forEach { till.remove(it) }
        save()
        return out
    }

    // ---- 合约开始前积攒的休息时间 (暂停不打折; 支援短缺) ----
    // 一次休息 = 建议休息时长 + 积攒的休息时间; 先用合约中得到的, 合约开始前积攒的放在最后用.

    val pre get() = if (active) live.dbl("pre") else 0.0
    /** 还在积攒的休息时间里的那部分 (没用掉, 也没有拿进正在进行的休息). */
    val preLeft get() = if (active) live.dbl("pre_left") else 0.0
    val preUsed get() = if (active) live.dbl("pre_used") else 0.0

    /** 开始休息, 积攒的 carry 秒全部拿进这次休息: 其中合约开始前的有多少 (从 pre_left 里拿走). */
    fun preTake(carry: Double): Double {
        if (!active) return 0.0
        val x = minOf(maxOf(0.0, carry), preLeft)
        if (x > 0) {
            live.put("pre_left", r1(preLeft - x))
            save()
        }
        return x
    }

    /** 一次休息结束 (back = 暂停时还给积攒的那部分合约开始前的时间, 自然结束时 0). */
    fun preSettle(taken: Double, back: Double) {
        if (!active) return
        val t = maxOf(0.0, taken)
        val b = back.coerceIn(0.0, t)
        live.put("pre_left", r1(preLeft + b)).put("pre_used", r1(preUsed + t - b))
        save()
    }

    private fun supplyShare(t: Collection<String>): Double? = t.mapNotNull { SUPPLY[it] }.minOrNull()
    fun supplyTerm(): String = terms.firstOrNull { it in SUPPLY } ?: ""
    /** 支援短缺: 合约开始前积攒的休息时间最多能用多少秒 (null = 不限). */
    val supplyLimit: Double? get() = if (supplyTerm().isEmpty() || live.isNull("supply")) null else live.dbl("supply")

    /** 正在休息 (还剩 left 秒, 这次休息里有 taken 秒是合约开始前的): 再过多少秒就用超了 (null = 这次用不超). */
    fun supplyUntil(left: Double, taken: Double): Double? {
        val lim = supplyLimit ?: return null
        if (taken <= 0) return null
        val cross = taken - (lim - preUsed)
        if (cross <= 0) return null
        return maxOf(0.0, left - cross)
    }

    /** 支援短缺要检查的词条: 选着的, 或者已经失效的 (按失效时的上限). */
    fun supplyWatch(): String = supplyTerm().ifEmpty { if (live.has("supply_lost")) lostOf(SUPPLY) else "" }

    /** 支援短缺: 合约开始前积攒的休息时间用超了 (正在休息时按已经用掉的算).
     *  已经失效的: 按失效时的上限, 还在继续用就返回它 (只响警报). */
    fun supplyCheck(left: Double, taken: Double): String {
        val used = preUsed + maxOf(0.0, taken - maxOf(0.0, left))
        val id = supplyTerm()
        if (id.isEmpty()) {
            val l = supplyWatch().ifEmpty { return "" }
            val last = watchUsed["supply"] ?: used
            watchUsed["supply"] = used
            return if (used > live.dbl("supply_lost") + 0.5 && used > last + 0.5) l else ""
        }
        val lim = supplyLimit ?: return ""
        return if (used > lim + 0.5) id else ""
    }

    // ---- 词条的效果 ----

    /** 只能用剩余联网时间的几成 (null = 不限). */
    fun netShare(): Double? = terms.mapNotNull { NET_SHARE[it] }.minOrNull()
    fun fog() = has("fog")
    /** 选了整点记录的词条: 整点记录关着时, 这次合约中照样开启. */
    fun hourForced() = terms.any { it in HOUR_TERMS } || (active && live.optBoolean("hour_forced"))
    /** 细化规范: 这一小时 (按开始时刻) 和进行中的合约重叠, 记录改为每 30 分钟一次. */
    fun fineHour(slotStart: LocalDateTime): Boolean {
        if (!has("fine")) return false
        val a = startAt ?: return false
        val b = endAt ?: return false
        return a.isBefore(slotStart.plusHours(1)) && slotStart.isBefore(b)
    }
    fun windowLimit(): Int = terms.mapNotNull { WINDOWS[it] }.minOrNull() ?: 0
    fun restFactor(): Double = restFactorOf(mine)

    /** (组合, 状态) 现在的样子: 效果栏 (第 3 页) 照着画. */
    fun combos(): List<Pair<Combo, String>> {
        val t = mine
        val o = if (active) orig else emptyList()
        return COMBOS.map { it to comboState(it, t, if (active) o else null) }
    }

    /** 凑齐了的组合的成员词条 → 颜色 (界面上框住它们). */
    fun comboMarks(): Map<String, String> = comboMarks(mine)

    // ---- 软规则: 有没有违反 (由调用方算作违反: violate) ----

    fun netTerm(): String = terms.firstOrNull { it in NET_SHARE } ?: ""

    /** 信息阻断 / 信息隔绝: 合约开始时 (以及合约中换日时) 记下当时的剩余额度, 只能用它的几成, 不能借贷.
     *  返回 (要降级的词条, "loan" / "over"), 没有违反时 null (只由做自动结束的设备调用, 额度是各设备共用的).
     *  已经失效的: 照样按失效时的额度看, 还在继续用超 / 又借贷了就返回它 (只响警报). */
    fun netCheck(used: Double, bonus: Double, raw: Double, date: String): Pair<String, String>? {
        val id = netTerm().ifEmpty { lostOf(NET_SHARE) }.ifEmpty { return null }
        val n = live.optJSONObject("net")
        if (n == null || n.optString("date") != date) {
            val r = maxOf(0.0, raw)
            live.put("net", JSONObject().put("date", date).put("base", r1(r)).put("bonus", r1(bonus))
                .put("limit", r1(used + r * NET_SHARE[id]!!)))
            save()
            return null
        }
        if (bonus > n.dbl("bonus") + 0.5) {
            n.put("bonus", r1(bonus))
            save()
            return id to "loan"
        }
        var over = used > n.dbl("limit") + 0.5
        if (echo(id)) {                                    // 失效后: 还在继续用才算
            val last = watchUsed["net"] ?: used
            watchUsed["net"] = used
            over = over && used > last + 0.5
        }
        return if (over) id to "over" else null
    }

    private fun r1(x: Double) = Math.round(x * 10) / 10.0

    /** 合约还允许用多少联网额度 (秒); 没有信息阻断时 null. */
    fun netLeft(used: Double): Double? {
        if (netTerm().isEmpty()) return null
        val n = live.optJSONObject("net") ?: return null
        return maxOf(0.0, n.dbl("limit") - used)
    }

    /** 此刻是不是已经超了 (只记在本机): 只在刚越过的那一下算一次违反, 回到限额以内后再越过才再算. */
    private val watch = HashMap<String, Boolean>()
    /** 失效后的信息阻断 / 支援短缺: 上次看到的用量 (只记在本机). */
    private val watchUsed = HashMap<String, Double>()
    private fun edge(key: String, over: Boolean): Boolean {
        val was = watch[key] ?: false
        watch[key] = over
        return over && !was
    }

    /** 通道阻塞: (要检查的词条, 上限); 已经失效的按失效前的上限. 没有时 ("", 0). */
    fun windowWatch(): Pair<String, Int> {
        val id = (terms.firstOrNull { it in WINDOWS } ?: "").ifEmpty { lostOf(WINDOWS) }
        return id to (WINDOWS[id] ?: 0)
    }

    /** 通道阻塞: 专注中后台的应用数 (Android 上的"窗口数") 超过词条的上限 (越过的那一下算一次). */
    fun windowCheck(n: Int): String {
        val (id, lim) = windowWatch()
        if (lim == 0 || id.isEmpty()) return ""
        return if (edge("win", n > lim)) id else ""
    }

    /** 离开专注时段: 通道阻塞的"越过"状态清掉, 下次专注重新算. */
    fun windowReset() {
        edge("win", false)
    }

    /** 禁止打开这一类应用 (BANS 之一) 的是哪个词条: 它自己; 不禁止时 "". */
    fun banTerm(kind: String): String = if (watching(kind)) kind else ""

    /** 乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒: 开着这一类应用.
     *  每一类只在从没有到有的那一下算一次. 返回要降级的词条. */
    fun banCheck(kind: String, hit: Boolean): String {
        val id = banTerm(kind)
        return if (id.isNotEmpty() && edge(kind, hit)) id else ""
    }

    /** 合约中要录什么: camera (目光) / screen (刻痕). */
    fun records(): List<String> = terms.mapNotNull { RECORD[it] }

    /** 抑制: 用了快速访问. */
    fun quickCheck(): String = if (watching("suppress")) "suppress" else ""

    /** 纵深推进: 结束的这段专注不到要求的时长. */
    fun focusCheck(seconds: Double): String {
        val id = (terms.firstOrNull { it in DEEP } ?: "").ifEmpty { lostOf(DEEP) }.ifEmpty { return "" }
        return if (seconds < DEEP[id]!!) id else ""
    }

    /** 静默协议: 专注中处于联网状态 (只在刚变成这样的那一下算一次). */
    fun quietCheck(hit: Boolean): String = if (watching("quiet") && edge("quiet", hit)) "quiet" else ""

    /** 禁止囤囤鼠!: 提前结束了休息 (软规则: 照样能结束, 算一次违反). */
    fun hoardCheck(): String = if (watching("hoard")) "hoard" else ""

    /** 侵蚀: 网络申请的冷静期乘多少. */
    fun cooldownFactor(): Double = terms.mapNotNull { COOLDOWN_X[it] }.maxOrNull() ?: 1.0

    /** 一段专注换来的休息时间 (秒, 已乘"休息时间倍率"). 返回给出的休息时间 (乘了疲惫);
     *  完成合约的奖励另外按等级给 (reward). */
    fun bookRest(seconds: Double): Double {
        val now = maxOf(0.0, seconds) * restFactor()
        live.put("rest", r1(rest + now))
        save()
        return now
    }

    /** 完成之后才结束的专注, 开始于刚完成的合约里: 按那个合约的疲惫给 (未知变量选中的不算组合). */
    fun bookRestAfter(seconds: Double, start: LocalDateTime): Double {
        val h = last ?: return seconds
        val hs = parseIso(h.optString("start"))
        val he = parseIso(h.optString("end"))
        if (h.optString("result") == "completed" && hs != null && he != null && !start.isBefore(hs) && start.isBefore(he)) {
            val rand = (h.optJSONArray("rand")?.strings() ?: emptyList()).toSet()
            val t = (h.optJSONArray("terms")?.strings() ?: emptyList()).filter { it !in rand }
            val f = restFactorOf(t)
            return seconds * f
        }
        return seconds
    }

    /** 完成合约后的第一次休息: 用掉这个标记. */
    fun takeFirstBreak(): Boolean {
        if (active || !live.optBoolean("first_break", false)) return false
        live.put("first_break", false)
        save()
        return true
    }

    // ---- 整点记录: 超时未填写算一次违反; 记录封锁 ----

    /** 各个合约 (结束了的和进行中的) 的 (开始, 结束); term: 只要选了这个词条的. */
    private fun periods(now: LocalDateTime, term: String = ""): List<Pair<LocalDateTime, LocalDateTime>> {
        val out = history.mapNotNull { h ->
            val a = parseIso(h.optString("start")) ?: return@mapNotNull null
            val b = parseIso(h.optString("end")) ?: return@mapNotNull null
            val t = (h.optJSONArray("orig") ?: h.optJSONArray("terms"))?.strings() ?: emptyList()
            if (term.isNotEmpty() && term !in t) return@mapNotNull null
            a to b
        }.toMutableList()
        if (term.isEmpty() || term in orig) startAt?.let { out.add(it to now) }
        return out
    }

    /** 进行中的合约里已经结束的记录格: (键, 开始, 结束).
     *  键是 "日期/小时", 细化规范的两个半小时是 "日期/小时/1" "日期/小时/2". */
    fun hourUnits(now: LocalDateTime): List<Triple<String, LocalDateTime, LocalDateTime>> {
        val a = startAt ?: return emptyList()
        val e = endAt ?: now
        val b = if (now.isBefore(e)) now else e
        val out = mutableListOf<Triple<String, LocalDateTime, LocalDateTime>>()
        var h = a.withMinute(0).withSecond(0).withNano(0)
        while (h.isBefore(b)) {
            val key = "${h.toLocalDate()}/${h.hour}"
            val parts = if (fineHour(h)) listOf(Triple("$key/1", h, h.plusMinutes(30)), Triple("$key/2", h.plusMinutes(30), h.plusHours(1)))
                else listOf(Triple(key, h, h.plusHours(1)))
            out += parts.filter { it.second.isBefore(b) && a.isBefore(it.third) && !it.third.isAfter(now) }
            h = h.plusHours(1)
        }
        return out
    }

    /** 合约中过了填写时限还没填写的记录格 (空着, 或者是超时自动记录的内容): 每格只算一次违反.
     *  返回新算的键 (只由做自动结束的设备调用). */
    fun hourMisses(now: LocalDateTime, fillSec: Long, textOf: (String) -> String, auto: String,
                   autoOf: ((String) -> String)? = null): List<String> {
        if (!active) return emptyList()
        val old = live.optJSONArray("hl")?.strings() ?: emptyList()
        val seen = old.toMutableSet()
        val new = mutableListOf<String>()
        for ((key, a, b) in hourUnits(now)) {
            if (key in seen || b.plusSeconds(cellFill(a, b, fillSec)).isAfter(now)) continue
            seen.add(key)
            if (isAuto(textOf(key), key, auto, autoOf)) new.add(key)
        }
        if (seen.size != old.size) {
            live.put("hl", JSONArray(seen.sorted()))
            save()
        }
        return new
    }

    private fun isAuto(text: String, key: String, auto: String, autoOf: ((String) -> String)?): Boolean {
        val wanted = try { autoOf?.invoke(key) ?: auto } catch (e: Exception) { auto }
        return text in setOf("", wanted)
    }

    /** 记录封锁: 和选了这个词条的合约重叠的记录格, 过了填写时限就不能再改 (不管填没填, 填了什么)
     *  —— 合约结束了也一样. 未知变量封锁的格子也是不管填没填都不能再改. */
    fun hourLocked(start: LocalDateTime, end: LocalDateTime, text: String, now: LocalDateTime, fillSec: Long,
                   auto: String, autoOf: ((String) -> String)? = null, key: String = ""): Boolean {
        if (locks().any { (a, b) -> a.isBefore(end) && start.isBefore(b) }) return true
        if (now.isBefore(end.plusSeconds(cellFill(start, end, fillSec)))) return false
        return periods(now, "seal").any { (a, b) -> a.isBefore(end) && start.isBefore(b) }
    }

    /** 记录格的补填时限: 细化规范的半小时格只有 30 min (MECHANICS 18.4). */
    private fun cellFill(start: LocalDateTime, end: LocalDateTime, fillSec: Long): Long =
        if (!end.isAfter(start.plusMinutes(30))) minOf(fillSec, 1800L) else fillSec

    // ---- 设备同步 ----

    fun doc(): Map<String, Any?> {
        val out = mutableMapOf<String, Any?>("live" to JSONObject(live.toString()),
            "history" to JSONArray(history.map { JSONObject(it.toString()) }))
        for (v in voided) out["void:$v"] = 1          // 每个作废的 id 单独一项: 同步时只会增加
        return out
    }

    // ---- 时间重叠的两个合约 (例如一台设备用 Adapter 断网期间各自开始了合约, MECHANICS 15.5) ----

    /** 冲突里一个合约的摘要: id, 起止, 危机等级, 词条, 结果 ("" = 进行中); 进行中的带上整个 live (保留它时恢复). 同 contract.py 的 _brief. */
    private fun brief(rec: JSONObject, isLive: Boolean): JSONObject {
        val terms = rec.optJSONArray("terms")?.strings() ?: emptyList()
        val rnd = (rec.optJSONArray("rand")?.strings() ?: emptyList()).toSet()
        val m = terms.filter { it in BY_ID && it !in rnd }
        val lv = rec.optInt("level", 0).takeIf { it > 0 } ?: (m.sumOf { BY_ID[it]!!.level } + comboLevel(m))
        val out = JSONObject().put("id", rec.opt("id")?.toString() ?: "").put("start", rec.optString("start"))
            .put("end", rec.optString("end")).put("level", lv).put("terms", JSONArray(terms))
            .put("result", if (isLive) "" else rec.optString("result"))
        if (isLive) out.put("live", JSONObject(rec.toString()))
        return out
    }

    private fun overlaps(a: JSONObject, b: JSONObject): Boolean {
        val sa = parseIso(a.optString("start")) ?: return false
        val ea = parseIso(a.optString("end")) ?: return false
        val sb = parseIso(b.optString("start")) ?: return false
        val eb = parseIso(b.optString("end")) ?: return false
        return sa.isBefore(eb) && sb.isBefore(ea)
    }

    private fun addConflict(mine: JSONObject, theirs: JSONObject) {
        val ids = setOf(mine.optString("id"), theirs.optString("id"))
        if (ids.size < 2 || ids.any { it in voided }) return
        if (conflicts.any { setOf(it.first.optString("id"), it.second.optString("id")) == ids }) return
        conflicts.add(mine to theirs)
    }

    /** 用户选了留下 keepId: 另一个作废 (进行中的直接结束, 不给奖励; 记录里删掉), 留下的要是被盖掉的进行中合约就恢复它.
     *  已经给出的休息时间不收回. 返回有没有处理. 同 contract.py 的 resolve_conflict. */
    fun resolveConflict(keepId: String): Boolean {
        val hit = conflicts.firstOrNull { keepId == it.first.optString("id") || keepId == it.second.optString("id") } ?: return false
        val keep = if (hit.first.optString("id") == keepId) hit.first else hit.second
        val drop = if (keep === hit.first) hit.second else hit.first
        voided.add(drop.optString("id"))
        applyVoids()
        val kept = keep.optJSONObject("live")
        if (kept != null && !active && history.none { it.opt("id")?.toString() == keepId }) {
            live = JSONObject(kept.toString())       // 留下的是被同步盖掉的那个进行中合约: 恢复 (之后随同步)
            live.put("terms", JSONArray(clean(live.optJSONArray("terms")?.strings() ?: emptyList())))
        }
        save()
        return true
    }

    /** 作废的合约: 进行中的结束 (不给奖励, 不留记录), 记录里删掉, 相关的冲突不用再问. */
    private fun applyVoids() {
        if (active && live.opt("id")?.toString() in voided) {
            live = JSONObject().put("state", "none").put("owner", live.optString("owner"))
            watch.clear()
        }
        history.removeAll { it.opt("id")?.toString() in voided }
        conflicts.removeAll { it.first.optString("id") in voided || it.second.optString("id") in voided }
    }

    /** 同步来的改动. 进行中的合约: 后改的覆盖先改的; 记录: 合并 (按 id).
     *  两个不同的合约时间重叠 (进行中的被另一个盖掉, 或者送来的记录和本机的重叠): 记下冲突, 让用户选留哪个 (15.5). */
    fun merge(changes: Map<String, Any?>) {
        for ((k, v) in changes) if (k.startsWith("void:") && v != null && v != JSONObject.NULL) voided.add(k.removePrefix("void:"))
        val incoming = (changes["history"] as? JSONArray)?.objects() ?: emptyList()
        val ended = (incoming.map { it.opt("id")?.toString() } + history.map { it.opt("id")?.toString() }).toSet()
        // 本机这个合约在别的设备上已经结束了 (送来的记录里有它): 正常切换, 不算冲突
        val mineLive = if (active && live.opt("id")?.toString() !in ended) brief(live, true) else null
        (changes["live"] as? JSONObject)?.let { l ->
            val gone = l.optString("state") == "active" && l.opt("id")?.toString() in voided   // 已经作废的又被送回来: 不收
            if (!gone && l.optString("state") in setOf("active", "none") && l.toString() != live.toString()) {
                if (mineLive != null && l.optString("state") == "active" && l.opt("id")?.toString() != mineLive.optString("id"))
                    addConflict(mineLive, brief(l, true))
                live = JSONObject(l.toString())
                if (active) live.put("terms", JSONArray(clean(live.optJSONArray("terms")?.strings() ?: emptyList())))
            }
        }
        if (incoming.isNotEmpty()) {
            val have = history.map { it.opt("id")?.toString() }.toMutableSet()
            val local = history.map { brief(it, false) } + listOfNotNull(mineLive)
            for (h in incoming) {
                val id = h.opt("id")?.toString()
                if (h.has("start") && id !in have && id !in voided) {
                    for (loc in local) if (loc.optString("id") != id && overlaps(loc, h)) addConflict(loc, brief(h, false))
                    history.add(JSONObject(h.toString()))
                    have.add(id)
                }
            }
        }
        applyVoids()
        save()
    }
}

/** 合约结束的结果 → 显示的文字 (中文原文, 由 tr 翻译). */
fun contractResult(result: String): String = when (result) {
    "completed" -> "完成"
    // 手动提前结束是"行动终止"; 被动结束 (违反 / 空闲 / 设备离线) 是"行动失败"
    "failed" -> "行动失败: 目标生命值归零"
    "idle" -> "行动失败: 空闲超过 30 分钟"
    "offline" -> "行动失败: 设备离线超过 15 分钟"
    "abandoned" -> "行动终止"
    else -> ""
}
