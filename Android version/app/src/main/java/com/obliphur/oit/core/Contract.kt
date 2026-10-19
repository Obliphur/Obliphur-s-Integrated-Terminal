package com.obliphur.oit.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.LocalDateTime

/** 词条: id, 组 (同一组互相冲突, 界面上排在同一列), 等级, 名字, 描述, 另外冲突的词条. */
class Term(val id: String, val group: String, val level: Int, val name: String, val desc: String,
           val conflicts: List<String> = emptyList())

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

        // 成系列的 (有好几档的) 在前, 界面上放在左边; 零散的 (只有一档的) 在后, 放在右边.
        val TERMS = listOf(
            Term("long1", "long", 1, "环境:持久战-I", "时长改为120%"),
            Term("long2", "long", 2, "环境:持久战-II", "时长改为140%"),
            Term("long3", "long", 3, "环境:持久战-III", "时长改为160%"),
            Term("move1", "move", 1, "目标:高机动-I", "现实中的移动速度改为125%"),
            Term("move2", "move", 2, "目标:高机动-II", "现实中的移动速度改为150%"),
            Term("info1", "info", 1, "环境:信息阻断-I", "可用的剩余联网时间改为75% 且不能使用借贷"),
            Term("info2", "info", 2, "环境:信息阻断-II", "可用的剩余联网时间改为50% 且不能使用借贷"),
            Term("info3", "info", 3, "环境:信息阻断-III", "可用的剩余联网时间改为25% 且不能使用借贷"),
            Term("isolate", "info", 4, "信息隔绝", "不能使用联网功能 选择时 获得的休息时间改为130%",
                listOf("music", "silent", "game", "video")),
            Term("tired1", "tired", 1, "环境:疲惫-I", "期间获得的休息时间改为70%"),
            Term("tired2", "tired", 2, "环境:疲惫-II", "期间获得的休息时间改为60%"),
            Term("tired3", "tired", 3, "环境:疲惫-III", "期间获得的休息时间改为50%"),
            Term("short1", "short", 1, "环境:支援短缺-I", "可用的合约开始前已保存休息时间改为50%"),
            Term("short2", "short", 2, "环境:支援短缺-II", "不能使用合约开始前已经保存的休息时间"),
            Term("retreat1", "retreat", 1, "环境:退路封锁-I", "初始目标生命值为3"),
            Term("retreat2", "retreat", 2, "环境:退路封锁-II", "初始目标生命值为2"),
            Term("last", "retreat", 3, "最后防线", "初始目标生命值为1\n危机等级每有10级 词条等级增加一级"),
            Term("chan1", "chan", 1, "环境:通道阻塞-I", "期间的最大窗口数改为4"),
            Term("chan2", "chan", 2, "环境:通道阻塞-II", "期间的最大窗口数改为3"),
            Term("deep1", "deep", 1, "目标:纵深推进-I", "专注的时间不能少于40min"),
            Term("deep2", "deep", 2, "目标:纵深推进-II", "专注的时间不能少于60min"),
            Term("erode1", "erode", 1, "环境:侵蚀-I", "网络申请冷却期改为200%", listOf("isolate")),
            Term("erode2", "erode", 2, "环境:侵蚀-II", "网络申请冷却期改为300%", listOf("isolate")),
            // 零散的按这里的顺序在各自等级的那一行挨着排, 所以行为类似的写在一起 (上下两行也对齐):
            // 联网 (抑制 / 全频道阻塞干扰), 整点记录 (记录封锁 / 细化规范), 禁止打开的软件
            // (乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒, 下面是录像: 目光 / 刻痕, 再下面是互联网娱乐的败退), 黑雾,
            // 最后是休息 / 专注 (禁止囤囤鼠! / 静默协议 / 未知变量).
            Term("suppress", "suppress", 1, "环境:抑制", "不能使用快速访问功能", listOf("isolate")),
            Term("jam", "jam", 2, "环境:全频道阻塞干扰", "期间使用proxy block-all模式"),
            Term("seal", "seal", 1, "环境:记录封锁", "超过填写时间未填写 则不能再修改该记录"),
            Term("fine", "fine", 2, "目标:细化规范", "记录变为30min一次"),
            Term("music", "music", 1, "环境:乐理之灾", "非休息期间不能打开音乐播放软件 不能使用外部软件播放mp3等音频文件"),
            Term("gaze", "gaze", 2, "目光", "合约期间打开摄像头记录使用者的行为 结束后保存录像"),
            Term("silent", "silent", 1, "环境:无言为真", "合约期间不能打开社交软件和与AI聊天的软件 不能使用社交网站"),
            Term("scar", "scar", 2, "刻痕", "合约期间录制整个屏幕 结束后保存录像",
                listOf("gaze")),
            Term("game", "game", 1, "环境:禁游令", "合约期间不能使用游戏和游戏平台"),
            Term("video", "video", 1, "环境:低帧的诅咒", "合约期间不能使用视频平台"),
            Term("ent", "ent", 4, "互联网娱乐的败退", "同时有乐理之灾 无言为真 禁游令 低帧的诅咒的效果\n违反任意一项即整个失效\n合约开始时获得额外20min的休息时间",
                listOf("music", "silent", "game", "video", "isolate")),
            Term("fog", "fog", 1, "环境:黑雾", "合约期间 专注时长 成就 日历 统计数据不可见 且无法打开设置"),
            Term("hoard", "hoard", 1, "禁止囤囤鼠!", "不能提前结束休息 但是获得的休息时间改为105%"),
            Term("quiet", "quiet", 1, "环境:静默协议", "专注中不能处于联网状态", listOf("isolate")),
            Term("unknown", "unknown", 3, "未知变量", "合约开始时 以及之后每过一个整小时 从当前可以选择的1-2级词条中随机启用一个\n选中的词条不算进危机等级"),
        )
        val BY_ID = TERMS.associateBy { it.id }
        val GROUPS = TERMS.map { it.group }.distinct()
        /** 词条板的布局: 成系列的每组一列 (左边); 零散的不单独占列, 按等级放进右边的几列, 每一行从左往右挨着排, 不留空. */
        val SERIES = GROUPS.filter { g -> TERMS.count { it.group == g } > 1 }
        /** 放在另一个词条的那一列 (那一列在这个等级的格子空着): 互联网娱乐的败退在乐理之灾那一列的 4 级. */
        val ALIGN = mapOf("ent" to "music")
        /** 词条 → 列. */
        val LAYOUT: Map<String, Int> = run {
            val loose = HashMap<Int, Int>()
            val m = TERMS.filter { it.id !in ALIGN }.associate { t ->
                t.id to if (t.group in SERIES) SERIES.indexOf(t.group)
                else SERIES.size + loose.getOrDefault(t.level, 0).also { loose[t.level] = it + 1 }
            }
            m + ALIGN.mapValues { m[it.value]!! }
        }
        val BOARD_COLS = (LAYOUT.values.maxOrNull() ?: -1) + 1
        val MAX_LEVEL = TERMS.maxOf { it.level }
        /** 词条板翻页: 成系列的一页, 零散的一页 (每页的列). */
        val PAGES = listOf(0 until SERIES.size, SERIES.size until BOARD_COLS)
        private val ORDER = TERMS.mapIndexed { i, t -> t.id to i }.toMap()

        // 软规则: 界面上照样能做, 但会被算作违反, 这个词条降一级 (结算按降级后的等级);
        // 其余是硬规则, 立即生效 (黑雾, 记录封锁, 细化规范, 全频道阻塞干扰, 持久战, 疲惫).
        // 录像 (目光 / 刻痕) 违反一次就失效 (录像停止): 不在 LOWER / SINGLE 里.
        val SOFT = setOf("info1", "info2", "info3", "isolate", "chan1", "chan2", "music", "suppress", "short1", "short2",
            "silent", "game", "video", "ent", "gaze", "scar", "deep1", "deep2", "quiet", "hoard", "unknown")
        /** 降一级换成哪个词条. */
        val LOWER = mapOf("isolate" to "info3", "info3" to "info2", "info2" to "info1", "chan2" to "chan1",
            "short2" to "short1", "deep2" to "deep1")
        /** 只有一档的软规则: 降级时等级减一, 减到 0 就失效. */
        val SINGLE = setOf("music", "suppress", "silent", "game", "video", "quiet", "hoard")
        /** 这个等级的词条用红色边框. */
        const val RIM_LEVEL = 4

        val DURATION = mapOf("long1" to 1.2, "long2" to 1.4, "long3" to 1.6)
        val NET_SHARE = mapOf("info1" to 0.75, "info2" to 0.5, "info3" to 0.25, "isolate" to 0.0)
        /** 合约中专注换来的休息时间的基础倍率; 有疲惫时换成疲惫的倍率. */
        const val BASE_REST = 0.8
        val REST = mapOf("tired1" to 0.7, "tired2" to 0.6, "tired3" to 0.5)
        /** 休息时间额外的乘区 (和疲惫相乘): 信息隔绝 ×1.3, 禁止囤囤鼠! ×1.05. */
        val REST_BONUS = mapOf("isolate" to 1.3, "hoard" to 1.05)
        /** 合约中专注换来的休息时间乘多少: 基础 ×0.8, 有疲惫时换成疲惫的 (取最低的); 再乘额外的乘区. */
        fun restFactorOf(terms: Collection<String>): Double =
            (terms.mapNotNull { REST[it] }.minOrNull() ?: BASE_REST) * terms.fold(1.0) { f, t -> f * (REST_BONUS[t] ?: 1.0) }
        /** 录像的词条 → 录什么. */
        val RECORD = mapOf("gaze" to "camera", "scar" to "screen")
        /** 禁止打开某类应用的词条 (每秒看前台应用). */
        val BANS = listOf("music", "silent", "game", "video")
        /** 互联网娱乐的败退: 同时有上面四个的效果; 违反任意一项就整个失效. */
        const val BANS_ALL = "ent"
        /** 合约开始时积攒的休息时间多给这么多秒 (算合约中得到的, 合约结束后不留下). */
        val REST_GIFT = mapOf("ent" to 20 * 60)
        val WINDOWS = mapOf("chan1" to 4, "chan2" to 3)
        /** 支援短缺: 合约开始前积攒的休息时间能用几成. */
        val SUPPLY = mapOf("short1" to 0.5, "short2" to 0.0)
        /** 整点记录的词条: 整点记录关着时, 这次合约中照样开启. */
        val HOUR_TERMS = setOf("seal", "fine")
        /** 初始目标生命值 (硬规则). */
        val LIFE = mapOf("retreat1" to 3, "retreat2" to 2, "last" to 1)
        /** 最后防线: 危机等级 (不算这份加成) 每有 LAST_STEP 级, 它的等级 +1. */
        const val LAST = "last"
        const val LAST_STEP = 10

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

        /** 一类应用 (BANS 之一) 的包名. */
        fun banApps(kind: String): Set<String> = when (kind) {
            "music" -> MUSIC_APPS
            "silent" -> SOCIAL_APPS
            "game" -> GAME_APPS
            "video" -> VIDEO_APPS
            else -> emptySet()
        }

        /** 最后防线额外的等级: 选着它时, 危机等级 (不算这份加成) 每有 LAST_STEP 级 +1. */
        fun lastBonus(base: Int, terms: Collection<String>) = if (LAST in terms) base / LAST_STEP else 0

        fun levelOf(terms: Collection<String>) = terms.sumOf { BY_ID[it]?.level ?: 0 }.let { it + lastBonus(it, terms) }

        fun restMinutes(level: Int): Int {
            val lv = level.coerceIn(0, REST_TOP)
            return REST_MIN + (2 * (REST_MAX - REST_MIN) * lv + REST_TOP) / (2 * REST_TOP)   // 四舍五入 (.5 进位)
        }
        /** 完成合约时存进积攒的休息时间的秒数. */
        fun restReward(level: Int): Int = restMinutes(level) * 60
        /** 合约开始时存进积攒的休息时间的秒数 (互联网娱乐的败退: 20 分钟). */
        fun restGift(terms: Collection<String>): Int = terms.sumOf { REST_GIFT[it] ?: 0 }

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

    init {
        val d = Store.readObj(file)
        if (d != null) {
            d.optJSONObject("live")?.let { if (it.optString("state") in setOf("active", "none")) live = it }
            if (active) live.put("terms", JSONArray(clean(live.optJSONArray("terms")?.strings() ?: emptyList())))
            d.optJSONArray("history")?.objects()?.filter { it.has("start") }?.let { history.addAll(it) }
            draft = clean(d.optJSONArray("draft")?.strings() ?: emptyList())
        }
    }

    fun save() {
        if (blocked()) return
        history.sortBy { it.optString("end") }
        while (history.size > HISTORY_KEEP) history.removeAt(0)
        Store.write(file, JSONObject().put("live", live).put("history", JSONArray(history)).put("draft", JSONArray(draft)))
    }

    // ---- 进行中的合约 ----

    val active get() = live.optString("state") == "active"
    val terms: List<String> get() = if (active) live.optJSONArray("terms")?.strings() ?: emptyList() else emptyList()
    fun has(id: String) = id in terms
    val startAt get() = if (active) parseIso(live.optString("start")) else null
    val endAt get() = if (active) parseIso(live.optString("end")) else null
    /** 只有一档的词条被降了几级. */
    private val cut: JSONObject get() = (if (active) live.optJSONObject("cut") else null) ?: JSONObject()
    fun termLevel(id: String) = (BY_ID[id]?.level ?: 0) - cut.optInt(id, 0)
    /** 未知变量随机启用的词条 (不算进危机等级; 可以点击关闭, 算一次违反). */
    val rand: List<String> get() = if (active) (live.optJSONArray("rand")?.strings() ?: emptyList()).filter { it in terms } else emptyList()
    /** 算进危机等级的词条 (不算未知变量选中的). */
    private val counted get() = rand.let { r -> terms.filter { it !in r } }
    val level get() = counted.let { m -> m.sumOf { termLevel(it) }.let { it + lastBonus(it, m) } }
    /** 最后防线现在额外的等级 (按现在的危机等级算). */
    val lastExtra get() = counted.let { m -> lastBonus(m.sumOf { termLevel(it) }, m) }
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
    /** 完成时给的休息时间 (秒): 按现在的等级查表 (固定); 结算按降级后的等级. */
    val reward get() = if (active) restReward(level).toDouble() else 0.0
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
        if (newSupply == null) live.put("supply", JSONObject.NULL)
        else if (oldSupply != null && !live.isNull("supply"))
            live.put("supply", r1(live.dbl("supply") + pre * (newSupply - oldSupply)))
        save()
        return new
    }

    // ---- 未知变量: 合约的第 1, 2, 3 … 个小时开始时 (刚开始时也算) 随机启用一个词条 ----

    /** 现在能被未知变量选中的词条: 1-2 级, 能选 (没有选着, 也不和选着的冲突), 不是录像;
     *  退路封锁不会让目标生命值直接归零. */
    fun randomCandidates(): List<String> {
        val t = terms
        return TERMS.map { it.id }.filter { id ->
            BY_ID[id]!!.level in RANDOM_LEVELS && id !in RANDOM_SKIP && id !in t && blockedBy(id, t) == null &&
                id !in waiting && !(id in LIFE && lifeOf(t + id) <= violations)
        }
    }

    /** 到了新的一个小时就抽一个词条: 3 分钟后生效 (wake), 到这个小时结束时移除. 返回这次抽中的词条
     *  (只由做自动结束的设备调用). 程序关着时错过的小时依次补上 (那时抽中的到了它那个小时的结束就移除). */
    fun roll(now: LocalDateTime, rng: kotlin.random.Random = kotlin.random.Random.Default): List<String> {
        if (!active || !has(RANDOM)) return emptyList()
        val a = startAt ?: return emptyList()
        val end = endAt ?: now
        val due = if (now.isBefore(end)) (secondsBetween(a, now) / 3600).toInt() + 1 else 0
        var done = live.optInt("rand_n", 0)
        val picked = mutableListOf<String>()
        while (done < due) {
            done++
            expire(a.plusHours((done - 1).toLong()))
            val pool = randomCandidates()
            if (pool.isNotEmpty()) pool[rng.nextInt(pool.size)].let {
                val at = a.plusHours((done - 1).toLong())
                putWaiting(mapOf(it to (at.plusMinutes(RANDOM_DELAY_MIN) to at.plusHours(1))))
                picked.add(it)
            }
        }
        if (done != live.optInt("rand_n", 0)) {
            live.put("rand_n", done)
            save()
        }
        expire(now)
        val w = waiting
        return picked.filter { it in w }.distinct()
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

    /** 支援短缺: 合约开始前积攒的休息时间用超了 (正在休息时按已经用掉的算). */
    fun supplyCheck(left: Double, taken: Double): String {
        val lim = supplyLimit ?: return ""
        val id = supplyTerm().ifEmpty { return "" }
        val used = preUsed + maxOf(0.0, taken - maxOf(0.0, left))
        return if (used > lim + 0.5) id else ""
    }

    // ---- 词条的效果 ----

    /** 只能用剩余联网时间的几成 (null = 不限). */
    fun netShare(): Double? = terms.mapNotNull { NET_SHARE[it] }.minOrNull()
    fun fog() = has("fog")
    /** 选了整点记录的词条: 整点记录关着时, 这次合约中照样开启. */
    fun hourForced() = terms.any { it in HOUR_TERMS }
    /** 细化规范: 这一小时 (按开始时刻) 和进行中的合约重叠, 记录改为每 30 分钟一次. */
    fun fineHour(slotStart: LocalDateTime): Boolean {
        if (!has("fine")) return false
        val a = startAt ?: return false
        val b = endAt ?: return false
        return a.isBefore(slotStart.plusHours(1)) && slotStart.isBefore(b)
    }
    fun windowLimit(): Int = terms.mapNotNull { WINDOWS[it] }.minOrNull() ?: 0
    fun restFactor(): Double = restFactorOf(terms)

    // ---- 软规则: 有没有违反 (由调用方算作违反: violate) ----

    fun netTerm(): String = terms.firstOrNull { it in NET_SHARE } ?: ""

    /** 信息阻断 / 信息隔绝: 合约开始时 (以及合约中换日时) 记下当时的剩余额度, 只能用它的几成, 不能借贷.
     *  返回 (要降级的词条, "loan" / "over"), 没有违反时 null (只由做自动结束的设备调用, 额度是各设备共用的). */
    fun netCheck(used: Double, bonus: Double, raw: Double, date: String): Pair<String, String>? {
        val id = netTerm().ifEmpty { return null }
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
        if (used > n.dbl("limit") + 0.5) return id to "over"
        return null
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
    private fun edge(key: String, over: Boolean): Boolean {
        val was = watch[key] ?: false
        watch[key] = over
        return over && !was
    }

    /** 通道阻塞: 后台的应用数 (Android 上的"窗口数") 超过词条的上限. */
    fun windowCheck(n: Int): String {
        val lim = windowLimit()
        val id = terms.firstOrNull { it in WINDOWS } ?: ""
        if (lim == 0 || id.isEmpty()) return ""
        return if (edge("win", n > lim)) id else ""
    }

    /** 禁止打开这一类应用 (BANS 之一) 的是哪个词条: 它自己, 或者互联网娱乐的败退; 不禁止时 "". */
    fun banTerm(kind: String): String = if (has(kind)) kind else if (has(BANS_ALL)) BANS_ALL else ""

    /** 乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒 (或者互联网娱乐的败退): 开着这一类应用.
     *  每一类只在从没有到有的那一下算一次. 返回要降级的词条. */
    fun banCheck(kind: String, hit: Boolean): String {
        val id = banTerm(kind)
        return if (id.isNotEmpty() && edge(kind, hit)) id else ""
    }

    /** 合约中要录什么: camera (目光) / screen (刻痕). */
    fun records(): List<String> = terms.mapNotNull { RECORD[it] }

    /** 抑制: 用了快速访问. */
    fun quickCheck(): String = if (has("suppress")) "suppress" else ""

    /** 纵深推进: 结束的这段专注不到要求的时长. */
    fun focusCheck(seconds: Double): String {
        val id = terms.firstOrNull { it in DEEP } ?: return ""
        return if (seconds < DEEP[id]!!) id else ""
    }

    /** 静默协议: 专注中处于联网状态 (只在刚变成这样的那一下算一次). */
    fun quietCheck(hit: Boolean): String = if (has("quiet") && edge("quiet", hit)) "quiet" else ""

    /** 禁止囤囤鼠!: 提前结束了休息 (软规则: 照样能结束, 算一次违反). */
    fun hoardCheck(): String = if (has("hoard")) "hoard" else ""

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

    /** 完成之后才结束的专注, 开始于刚完成的合约里: 按那个合约的疲惫给. */
    fun bookRestAfter(seconds: Double, start: LocalDateTime): Double {
        val h = last ?: return seconds
        val hs = parseIso(h.optString("start"))
        val he = parseIso(h.optString("end"))
        if (h.optString("result") == "completed" && hs != null && he != null && !start.isBefore(hs) && start.isBefore(he)) {
            val t = h.optJSONArray("terms")?.strings() ?: emptyList()
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
    fun hourMisses(now: LocalDateTime, fillSec: Long, textOf: (String) -> String, auto: String): List<String> {
        if (!active) return emptyList()
        val old = live.optJSONArray("hl")?.strings() ?: emptyList()
        val seen = old.toMutableSet()
        val new = mutableListOf<String>()
        for ((key, a, b) in hourUnits(now)) {
            if (key in seen || b.plusSeconds(cellFill(a, b, fillSec)).isAfter(now)) continue
            seen.add(key)
            if (textOf(key) in setOf("", auto)) new.add(key)
        }
        if (seen.size != old.size) {
            live.put("hl", JSONArray(seen.sorted()))
            save()
        }
        return new
    }

    /** 记录封锁: 和选了这个词条的合约重叠的记录格, 过了填写时限还没填写 (空着 / 超时自动记录),
     *  就不能再改 —— 合约结束了也一样. */
    fun hourLocked(start: LocalDateTime, end: LocalDateTime, text: String, now: LocalDateTime, fillSec: Long,
                   auto: String): Boolean {
        if (now.isBefore(end.plusSeconds(cellFill(start, end, fillSec))) || text !in setOf("", auto)) return false
        return periods(now, "seal").any { (a, b) -> a.isBefore(end) && start.isBefore(b) }
    }

    /** 记录格的补填时限: 细化规范的半小时格只有 30 min (MECHANICS 18.4). */
    private fun cellFill(start: LocalDateTime, end: LocalDateTime, fillSec: Long): Long =
        if (!end.isAfter(start.plusMinutes(30))) minOf(fillSec, 1800L) else fillSec

    // ---- 设备同步 ----

    fun doc(): Map<String, Any?> = mapOf("live" to JSONObject(live.toString()), "history" to JSONArray(history.map { JSONObject(it.toString()) }))

    /** 同步来的改动. 进行中的合约: 后改的覆盖先改的; 记录: 合并 (按 id). */
    fun merge(changes: Map<String, Any?>) {
        (changes["live"] as? JSONObject)?.let { l ->
            if (l.optString("state") in setOf("active", "none") && l.toString() != live.toString()) {
                live = JSONObject(l.toString())
                if (active) live.put("terms", JSONArray(clean(live.optJSONArray("terms")?.strings() ?: emptyList())))
            }
        }
        (changes["history"] as? JSONArray)?.let { arr ->
            val have = history.map { it.opt("id")?.toString() }.toMutableSet()
            for (h in arr.objects()) {
                val id = h.opt("id")?.toString()
                if (h.has("start") && id !in have) {
                    history.add(JSONObject(h.toString()))
                    have.add(id)
                }
            }
        }
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
