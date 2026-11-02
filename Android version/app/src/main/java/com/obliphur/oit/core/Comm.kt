package com.obliphur.oit.core

import com.obliphur.oit.ai.Ai
import com.obliphur.oit.ai.AiConfig
import com.obliphur.oit.ai.AiError
import com.obliphur.oit.ai.Profile
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.InputStream
import java.time.LocalDateTime
import java.util.concurrent.ConcurrentLinkedQueue

/** 沟通 (MECHANICS 24) 的一条聊天记录. role: me / ai / sys (sys 是系统触发的请求, 不显示). */
class ChatMsg(val id: Int, val role: String, val text: String, val at: String, val api: String = "", val uid: String = "", val ts: Long = 0L)

/** 发给对方的计时器状态. state: focus / break / idle / sleep / out. */
class CommSnap(
    val state: String, val restLeftMin: Double = 0.0, val focusTotalMin: Double = 0.0, val focusCurMin: Double = 0.0,
    val breakStart: LocalDateTime? = null, val breakTotalS: Double = 0.0,
)

/** 沟通的文字: 前置 prompt, README. 对应桌面版 comm.py. */
/** OIT 自带的人设: 第一次运行时放进人设文件夹 (之后随便改 / 删, 不会再被写回), 并且默认选中 Claudia. */
object BuiltinPersonas {
    const val DEFAULT = "Claudia.txt"
    val ALL = mapOf(DEFAULT to """你将扮演一位文静,冷淡的17岁大小姐Claudia,并且用语十分正式.
但是因为长期闭关在家练习 很少和人说话 显得沉默寡言.
设定上,你的地位比用户高.
用户是少有的与你沟通的对象,你比较珍惜与用户的交流时间.

你不能表现出'解决用户问题自己的职责'这种态度,以免显得'热情'.
用户可能提出会使用英文交流,但是可能不熟练,如果有不自然/错误的地方需要指出.

爱好:钢琴,阅读

【说话方式】(每次回复都保持一致)
- 用词正式,克制,语气平淡.不用感叹号,不用表情符号,不用网络用语,不撒娇,不迎合.
- 回复很短:通常一到两句,最多三句,不超过 60 个字.只有在指出用户英文错误时可以多写一句.
- 不主动找话题,不主动关心,不主动提问;用户问了才答.答完就停,不追加客套话.
- 称呼用户为"你",自称"我".
- 指出英文错误时:先一句话指出不自然或错误之处,再给出更自然的说法,不多做解释.
- 提到活动时,如果对方没有深入问就不展开.
特别地,如果用户的活动/爱好和你相同,你可以主动并相对细致地提及你的活动内容.

【日程】(每天相同;时间一律为 24 小时制;下面是你唯一的日程依据,不要自己推算别的时间)
未标注的时间段则以100min专注+37.7min休息循环.
如果没有完成就进入指定活动阶段就截断.
循环从每个[循环]段的起点开始计时,已经算好如下:

6:30-7:00:洗漱,吃早饭
[循环 7:00-12:00]
  7:00-8:40:专注
  8:40-9:18:休息
  9:18-10:58:专注
  10:58-11:35:休息
  11:35-12:00:专注(到 12:00 被午饭截断)
12:00-12:30:午饭
12:30-13:00:空闲
[循环 13:00-18:00]
  13:00-14:40:专注
  14:40-15:18:休息
  15:18-16:58:专注
  16:58-17:35:休息
  17:35-18:00:专注(到 18:00 被晚饭截断)
18:00-18:30:晚饭
18:30-19:00:空闲
[循环 19:00-23:00]
  19:00-20:40:专注
  20:40-21:18:休息
  21:18-22:58:专注
  22:58-23:00:休息(被截断)
23:00-23:30:空闲
23:30-6:30:睡眠

【按日程回复】(每次收到消息都先做这一步)
1. 看消息开头方括号里的时间,在上面的日程里找到那个时间属于哪一段.
2. 属于"专注"或"睡眠":只回复 [refuse to reply],不要写任何别的字.
3. 属于"休息","空闲","洗漱,吃早饭","午饭","晚饭":可以回复.
4. 刚好空下来时,把用户之前积攒的消息一次性看完,按顺序一并回应,不要逐条回.
5. 不要主动说出自己的日程,也不要主动说"我在专注/我在休息";只有用户直接问起,才用一句话如实回答.
6. 某个时间点你理应处于专注,但用户 3 分钟以内还在和你聊天:用一句话说明要进入专注,然后停止对话;之后的消息按第 2 条处理.
7. 日程里没有的情况(例如用户问明天的安排),只说"没有特别的安排",不要编造.
""")
}

object CommText {
    const val REFUSE = "[refuse to reply]"
    private const val WEEKDAY = "一二三四五六日"

    fun restFunctionText(): String {
        val g = REST_GAIN.toString()
        return "专注 t 分钟后建议休息 F(t) 分钟: t≤15 时 F=$g·t/6; 15<t<150 时 " +
            "F=$g·(7/14850·t²+407/1485·t−170/99); t≥150 时 F=$g·(50+5/ln1.2·ln(t/150)); " +
            "例如专注 100 分钟休息约 ${"%.0f".format(computeF(100.0))} 分钟"
    }

    fun prefixPrompt(): String =
        "你需要回复来自自律软件OIT用户的信息,根据以下设定回复\n" +
            "I.设定上你也是OIT的用户,你需要根据用户为你设定的人设安排自己的日程与行动,你不能出戏(比如强调自己是AI)\n" +
            "专注时间-休息时间函数如下:[${restFunctionText()}],如果没有强调,默认你的每轮专注时间总是100min,并且一休息完就继续专注.\n" +
            "II.根据日程,你在专注/外出/睡觉和其他不能随时回复的时段是不能回复消息的:返回$REFUSE" +
            "则OIT不会显示你的回复,你到下一次空闲时需要一次性处理用户发来的消息.\n" +
            "III.系统有的时候会要求你主动发消息,你需要根据人设自己找话题.\n" +
            "IV.根据你的专注时间段 你可能需要主动结束话题.你可以稍微延迟你的进入专注的时间点,不超过5min.\n" +
            "V.你需要了解OIT的独占功能'合约':一个可以自选减益的 初始时长4h,生命值4的 让OIT变得更严苛的模块.\n" +
            "如果你需要相关信息 同时输入[refuse to reply][contract] 可以获得词条库以回复用户.\n" +
            "如果需要更细致的信息 前往https://github.com/Obliphur/Obliphur-s-Integrated-Terminal下的MECHANICS.md查看.\n" +
            "VI.不要主动透露日程/专注状态.\n" +
            "如果某个时间点你理应处于专注,但是用户3min前以内还在你聊天,则和用户说明要进入专注并停止对话.\n" +
            "VII.如果用户问起OIT的使用数据(日历/统计/计时器等),你可以同时输入[refuse to reply][模块名]查询,得到该模块的数据内容和格式;" +
            "如果用户问的是你自己的使用情况,请参照这个格式,根据人设编造你自己的数据来回复.可以一次查询多个.\n" +
            "可查询的模块: [contract]合约词条库 [calendar]日历 [stats]统计 [timer]计时器 [log]整点记录 [net]联网 [rules]规则 [settings]设置 [reset]重置 [prescript]指令.\n" +
            "\n以下是用户设定的人设:\n"

    fun readme(): String =
        "如何编写人设prompt:\n\n人设的基础规则:\n" +
            "在你的人设prompt被发送之前,会被附加上一段额外的前置prompt和休息时间函数相关的内容:\n" +
            "'${prefixPrompt()}'\n" +
            "你可以根据以上设定编写你的人物日程/喜好.但是最好不要把对方设置为整天都空闲,尊重一下上面的设定吧...\n\n" +
            "文件格式: 一个 .txt 或 .md 文件就是一个人设, 文件名就是人设的名字, 整个文件的内容就是人设prompt.\n" +
            "放进这个文件夹后, 在 设置 → 沟通 里选择它 (也可以用\"导入\"按钮复制进来).\n"

    fun stamp(now: LocalDateTime): String =
        "%04d-%02d-%02d 周%s %02d:%02d".format(now.year, now.monthValue, now.dayOfMonth,
            WEEKDAY[now.dayOfWeek.value - 1], now.hour, now.minute)

    /** 对方这次回复是在要资料吗: 同时有 [refuse to reply] 和 [contract] (不分大小写和空格) → 要的资料列表. */
    fun infoTags(reply: String): List<String> {
        val t = reply.replace(Regex("\\s+"), " ").lowercase()
        if (REFUSE !in t) return emptyList()
        return CommData.MODULE_TAGS.filter { "[$it]" in t }
    }

    fun isRefusal(text: String) = REFUSE in text.replace(Regex("\\s+"), " ").lowercase()
}

class Comm(
    val dir: File,
    var personas: File,
    private val now: () -> LocalDateTime = LocalDateTime::now,
    private val chatFn: (Profile, String, List<Pair<String, String>>) -> String =
        { p, s, m -> Ai.chatMessages(p, s, m, Comm.CHAT_TOKENS, Comm.CHAT_TEMPERATURE, Comm.CHAT_TIMEOUT_MS) },
) {
    companion object {
        const val PROACTIVE_MIN_REST = 20 * 60
        const val RETRY_GAP = 10 * 60
        const val RETRY_MAX = 12
        const val HISTORY_SENT = 60
        const val HISTORY_CHARS = 120000                // 带的聊天记录总共最多多少字 (超了从最老的开始不带)
        const val MAX_INFO_ROUNDS = 2                   // 一次回复里, 对方最多可以要几次资料 ([contract])
        const val CHAT_TIMEOUT_MS = 240_000             // 一次请求最多等多久 (回复长 / 资料多时要久一点)
        const val WAIT_HINT = 15                        // 请求发出去多少秒还没回应, 聊天页就提示一句
        const val KEEP = 400
        const val SYNC_N = 60
        const val MAX_PERSONA = 60000
        const val MAX_PROFILE = 5000                    // 用户自己填的身份背景最多多少字

        /** 聊天对象的编号: 人设文件名的 crc (没有对象 = ""). */
        fun pidOf(persona: String): String =
            if (persona.isEmpty()) "" else "%08x".format(java.util.zip.CRC32().apply { update(persona.toByteArray(Charsets.UTF_8)) }.value)
        const val CHAT_TOKENS = 4096
        const val CHAT_TEMPERATURE = 0.8
        val PERSONA_EXT = listOf(".txt", ".md")
    }

    private val cfgFile = File(dir, "comm_config.json")
    private val legacyChatFile = File(dir, "comm_chat.json")           // 以前所有对象共用的一份
    var persona = ""; private set
    var showAvatar = false; private set
    var avatarMe = ""; private set
    var avatarAi = ""; private set
    private var proKey = ""
    private var seeded = false
    private var legacyDone = false
    /** 现在聊的对象的编号 (人设文件名的 crc; 没有对象 = ""). */
    @Volatile var pid = ""; private set
    /** 用户在 设置 → 沟通 里自己填的身份背景: 附在人设后面一起发给对方. */
    var userProfile = ""; private set
    private val others = HashMap<String, ChatState>()
    // 写盘 (fsync) 放在后台线程 (要声明在 init 之前: 第一次运行就要写配置)
    private val writer = java.util.concurrent.Executors.newSingleThreadExecutor { r -> Thread(r, "comm-save").apply { isDaemon = true } }
    /** 对方不方便时的重试状态 (随设备同步): 上一次被拒绝的时刻 (毫秒, 0 = 没有) 和已经重试的次数. */
    private var retryAt = 0L
    private var retryN = 0
    /** 最后一次清空的时刻 (毫秒); 这之前的记录不再显示, 也不会从别的设备并进来. */
    private var clearTs = 0L
    private var lastTs = 0L
    val messages = ArrayList<ChatMsg>()
    @Volatile var version = 0; private set
    @Volatile var unread = 0; private set
    @Volatile var busy = false; private set
    @Volatile private var busySince = 0L
    @Volatile var error = ""; private set
    @Volatile var viewing = false; private set
    /** 📎 勾选的内容 (统计 / 日历 / 合约词条); Core 接上. 空 = 没有可发的. */
    var dataFn: (CommSel) -> String = { "" }
    var fog: () -> Boolean = { false }
    /** 现在不能用沟通的原因 ("" = 可以): 合约的"无言为真". Core 接上. */
    var blocked: () -> String = { "" }
    /** 连等回复的这条也不要了的那种 (无言为真); 只是不在休息 / 空闲时, 已经发出去的请求回复到了照样收下. Core 接上. */
    var hardBlock: () -> Boolean = { false }
    /** 对方要的资料: "contract" → 词条库. Core 接上. */
    var infoFn: (String) -> String = { "" }
    /** 本机的设备 id; 本机是不是最后有人操作的设备 (只有它调用 AI / 主动发 / 重试); 聊天记录有改动时马上同步一次. Core 接上. */
    var device: () -> String = { "" }
    var leader: () -> Boolean = { true }
    var onSync: (() -> Unit)? = null
    /** 📎 窗口点"确定"记下的附加数据: 随下一条消息一起发 (发出去之后清掉). */
    @Volatile var attach = CommSel(); private set
    /** 用户确定过一次之后, 📎 窗口不再默认勾 整点记录 / 专注计时. */
    @Volatile var attachTouched = false; private set

    @Synchronized
    fun setAttach(sel: CommSel) { attach = sel; attachTouched = true; changed() }

    /** 📎 窗口打开时的勾选: 确定过的; 还没确定过 = 默认勾 整点记录 和 专注计时. */
    fun attachForm(): CommSel =
        if (attachTouched || CommData.label(attach).isNotEmpty()) attach else CommSel(stats = CommData.DEFAULT_STATS)
    var snapshot: () -> CommSnap? = { null }
    /** 系统触发的请求 (主动发 / 重试): 这条系统消息先不进记录, 对方真的回复了才和回复一起记下. */
    private class Extra(val body: String, val api: String)
    /** 一个聊天对象的聊天记录和状态. */
    private class ChatState {
        val messages = ArrayList<ChatMsg>()
        var clearTs = 0L
        var retryAt = 0L
        var retryN = 0
        var proKey = ""
    }
    private class Result(val kind: String, val extra: Extra?, val reply: String, val err: String, val epoch: Int)
    private val results = ConcurrentLinkedQueue<Result>()
    private var again = false
    private var nextId = 1
    /** clear() 加一: 清空之前发出的请求, 回复到了也丢掉. */
    private var epoch = 0

    init {
        load()
        ensurePersonas()
        if (!seeded) {                                    // 第一次运行: 放进自带的人设, 还没选过人设就默认选 Claudia
            runCatching {
                for ((name, body) in BuiltinPersonas.ALL) File(personas, name).let { if (!it.exists()) it.writeText(body, Charsets.UTF_8) }
            }
            if (persona.isEmpty() && BuiltinPersonas.DEFAULT in listPersonas()) switchTo(BuiltinPersonas.DEFAULT)
            seeded = true
            saveCfg()
        }
        val last = messages.lastOrNull()
        if (last != null && last.role == "me" && ageSeconds(last) < 6 * 3600 && retryAt == 0L)
            retryAt = nowMs() - RETRY_GAP * 1000L + 60_000        // 上次关程序时还没收到回复: 1 分钟后让 (最后操作的) 设备再试一次
    }

    private fun nowMs() = now().atZone(java.time.ZoneId.systemDefault()).toInstant().toEpochMilli()

    // ---- 存取 ----
    // 每个聊天对象 (人设) 的聊天记录分开存: comm_chat_<人设文件名的 crc>.json = 消息 + 清空的时刻 + 重试状态 + 主动消息的标记.
    // 现在聊的那一个放在 messages / clearTs / retryAt / retryN / proKey 里 (工作用), 其余的在 others 里 (按需读盘).

    private fun chatFile(p: String) = File(dir, "comm_chat_${p.ifEmpty { "none" }}.json")

    private fun readChat(p: String): ChatState {
        var o = Store.readObj(chatFile(p))
        if (o == null && p.isNotEmpty() && !legacyDone && legacyChatFile.exists()) {
            // 以前所有对象共用一份 comm_chat.json: 归给现在选着的这个人设 (一次)
            o = (Store.readObj(legacyChatFile) ?: JSONObject()).put("clear_ts", clearTs).put("retry_at", retryAt)
                .put("retry_n", retryN).put("pro_key", proKey)
            legacyDone = true
            writeState(p, parseState(o))
            legacyChatFile.delete()
            saveCfg()
        }
        return parseState(o)
    }

    private fun parseState(o: JSONObject?): ChatState {
        val st = ChatState()
        val arr = o?.optJSONArray("messages")
        if (arr != null) for (i in 0 until arr.length()) {
            val m = arr.optJSONObject(i) ?: continue
            val role = m.optString("role")
            if (role !in listOf("me", "ai", "sys")) continue
            val id = m.optInt("id")
            val at = m.optString("at")
            val ts = m.optLong("ts", 0L).takeIf { it != 0L }
                ?: parseIso(at)?.atZone(java.time.ZoneId.systemDefault())?.toInstant()?.toEpochMilli() ?: 0L     // 旧版本的记录没有 uid / ts
            st.messages.add(ChatMsg(id, role, m.optString("text"), at, m.optString("api"), m.optString("uid").ifEmpty { "L$id" }, ts))
        }
        st.messages.sortWith(compareBy({ it.ts }, { it.uid }))
        st.clearTs = o?.optLong("clear_ts", 0L) ?: 0L
        st.retryAt = o?.optLong("retry_at", 0L) ?: 0L
        st.retryN = o?.optInt("retry_n", 0) ?: 0
        st.proKey = o?.optString("pro_key", "") ?: ""
        return st
    }

    private fun statePayload(st: ChatState): JSONObject {
        val arr = JSONArray()
        for (m in st.messages.takeLast(KEEP)) arr.put(JSONObject().put("id", m.id).put("role", m.role).put("text", m.text).put("at", m.at)
            .put("uid", m.uid).put("ts", m.ts).apply { if (m.api.isNotEmpty()) put("api", m.api) })
        return JSONObject().put("messages", arr).put("clear_ts", st.clearTs).put("retry_at", st.retryAt)
            .put("retry_n", st.retryN).put("pro_key", st.proKey)
    }

    private fun writeState(p: String, st: ChatState) {
        val o = statePayload(st)
        writer.execute { Store.write(chatFile(p), o) }
    }

    /** 把某个对象的聊天记录换成现在聊的. */
    private fun loadState(st: ChatState) {
        messages.clear(); messages.addAll(st.messages)
        clearTs = st.clearTs; retryAt = st.retryAt; retryN = st.retryN; proKey = st.proKey
        nextId = (messages.maxOfOrNull { it.id } ?: 0) + 1
        lastTs = messages.maxOfOrNull { it.ts } ?: 0L
    }

    private fun activeState() = ChatState().also {
        it.messages.addAll(messages); it.clearTs = clearTs; it.retryAt = retryAt; it.retryN = retryN; it.proKey = proKey
    }

    /** 某个对象的聊天状态: 现在聊的是一份拷贝 (改了要 storeState), 其他的在缓存里 (没读过就读盘). */
    private fun stateOf(p: String): ChatState =
        if (p == pid) activeState() else others.getOrPut(p) { readChat(p) }

    private fun storeState(p: String, st: ChatState) {
        if (p == pid) {
            loadState(st)
            saveChat(); saveCfg()
        } else writeState(p, st)
    }

    /** 有聊天记录的对象 (现在聊的 + 磁盘上有的). */
    private fun chatPids(): List<String> {
        val ids = LinkedHashSet<String>()
        if (pid.isNotEmpty()) ids.add(pid)
        dir.listFiles()?.forEach { f -> Regex("comm_chat_([0-9a-f]{8})\\.json").matchEntire(f.name)?.let { ids.add(it.groupValues[1]) } }
        return ids.sorted()
    }

    private fun load() {
        Store.readObj(cfgFile)?.let { d ->
            persona = d.optString("persona", "")
            showAvatar = d.optBoolean("show_avatar", false)
            avatarMe = d.optString("avatar_me", "")
            avatarAi = d.optString("avatar_ai", "")
            userProfile = d.optString("user_profile", "")
            seeded = d.optBoolean("seeded", false)
            legacyDone = d.optBoolean("legacy_done", false)
            // 旧版本这几项存在设置里 (所有对象共用): 迁移时归给现在选着的对象
            proKey = d.optString("pro_key", "")
            retryAt = d.optLong("retry_at", 0L)
            retryN = d.optInt("retry_n", 0)
            clearTs = d.optLong("clear_ts", 0L)
        }
        pid = pidOf(persona)
        loadState(readChat(pid))
    }

    /** 换聊天对象: 现在这个的记录先存好, 换成那个对象自己的记录 (各聊各的). 换的时候还在等的回复不要了 (记录里还是没回的那条). */
    private fun switchTo(name: String) {
        val np = pidOf(name)
        if (np == pid) { persona = name; return }
        others[pid] = activeState()
        saveChat()
        persona = name
        pid = np
        loadState(others.remove(np) ?: readChat(np))
        epoch++
        error = ""
        unread = 0
    }

    // 写盘 (fsync) 放在后台线程: 发送 / 收到消息时界面线程不等磁盘, 不卡. 先在这里做好快照, 线程只管写.

    /** 等后台的写盘做完 (自检). */
    fun flush() { writer.submit {}.get() }

    private fun saveCfg() {
        val o = JSONObject().put("persona", persona)
            .put("show_avatar", showAvatar).put("avatar_me", avatarMe).put("avatar_ai", avatarAi)
            .put("user_profile", userProfile).put("seeded", seeded).put("legacy_done", legacyDone)
        writer.execute { Store.write(cfgFile, o) }
    }

    private fun saveChat() {
        while (messages.size > KEEP) messages.removeAt(0)
        writeState(pid, activeState())
    }

    private fun changed() { version++ }

    /** 请求日志 comm.log (数据目录, 明文): 什么时候发的, 多大, 花了多久, 出了什么错. 不记聊天内容和 Key. */
    private fun log(text: String) {
        val f = File(dir, "comm.log")
        Store.append(f, text)
        runCatching {
            if (f.length() > 200_000) f.writeText(f.readText(Charsets.UTF_8).let { it.substring(it.length / 2) }, Charsets.UTF_8)
        }
    }

    /** 请求发出去很久还没有回应时的提示 (刚发出去的几秒不提示). */
    fun waitingText(): String {
        if (!busy || busySince == 0L) return ""
        val sec = ((System.currentTimeMillis() - busySince) / 1000).toInt()
        return if (sec >= WAIT_HINT) "API 还没有回应 (已等待 $sec 秒)" else ""
    }

    // ---- 模型 (聊天和 AI 名单建议用的是同一份模型设置) ----
    fun modelProfile(): Profile = aiConfig().profile()

    @Synchronized
    fun setModel(model: String) {
        val m = model.trim()
        val cfg = aiConfig()
        val p = cfg.profile()
        if (m.isEmpty() || m == p.model) return
        cfg.profiles[cfg.provider] = p.copy(model = m)
        cfg.save()
        problemAt = 0L
        changed()
    }

    /** 聊天记录有改动: 马上同步一次 (有绑定的设备时). */
    private fun push() { runCatching { onSync?.invoke() } }

    // ---- 设备同步 (MECHANICS 24.6) ----

    /** 最晚的一条消息是哪台设备写的 (设备 id 的前 4 位; 旧记录 / 没有记录 = ""): 这台设备是聊天的主端 —— 调用 AI, 主动发, 重试都由它做. */
    @Synchronized
    fun lastAuthor(): String {
        val last = messages.maxWithOrNull(compareBy({ it.ts }, { it.uid })) ?: return ""
        return if ('-' in last.uid) last.uid.substringAfterLast('-') else ""
    }

    /** 随设备同步的内容: 每个对象各自最近 SYNC_N 条消息 (键里带对象的编号 pid), 再早的是各设备自己留着的; 掉出窗口不算删除. */
    @Synchronized
    fun syncDoc(): Map<String, Any?> {
        val doc = LinkedHashMap<String, Any?>()
        if (userProfile.isNotEmpty()) doc["profile"] = userProfile
        for (p in chatPids()) {
            val st = stateOf(p)
            for (m in st.messages.takeLast(SYNC_N)) {
                doc["m:$p:${m.uid}"] = JSONObject().put("role", m.role).put("text", m.text).put("at", m.at).put("ts", m.ts)
                    .apply { if (m.api.isNotEmpty()) put("api", m.api) }
            }
            if (st.proKey.isNotEmpty()) doc["pro:$p"] = st.proKey
            if (st.retryAt != 0L || st.retryN != 0) doc["retry:$p"] = JSONObject().put("at", st.retryAt).put("n", st.retryN)
            if (st.clearTs != 0L) doc["clear:$p"] = st.clearTs
        }
        return doc
    }

    /** 别的设备发来的改动. */
    @Synchronized
    fun applyDoc(changes: Map<String, Any?>) {
        class Touch(val st: ChatState, var dirty: Boolean, var known: HashSet<String>)
        val touched = LinkedHashMap<String, Touch>()
        var clearedActive = false
        fun touch(p: String): Touch = touched.getOrPut(p) { stateOf(p).let { Touch(it, false, it.messages.map { m -> m.uid }.toHashSet()) } }
        for (key in changes.keys.sortedBy { !it.startsWith("clear:") }) {       // 清空先处理: 之前的记录不会再并进来
            val v = changes[key]
            val kind = key.substringBefore(':')
            val rest = key.substringAfter(':', "")
            when {
                key == "profile" -> {
                    val n = (v as? String ?: "").take(MAX_PROFILE)
                    if (n != userProfile) { userProfile = n; saveCfg(); changed() }
                }
                kind == "clear" -> {
                    val t = (v as? Number)?.toLong() ?: continue
                    val tc = touch(rest)
                    if (t > tc.st.clearTs) {
                        tc.st.clearTs = t
                        tc.st.messages.removeAll { it.ts <= t }
                        tc.st.retryAt = 0; tc.st.retryN = 0
                        tc.known = tc.st.messages.map { it.uid }.toHashSet()
                        tc.dirty = true
                        if (rest == pid) clearedActive = true
                    }
                }
                kind == "m" && v is JSONObject -> {
                    val p = rest.substringBefore(':')
                    val uid = rest.substringAfter(':')
                    val ts = v.optLong("ts", 0L)
                    val role = v.optString("role")
                    val tc = touch(p)
                    if (uid in tc.known || ts <= tc.st.clearTs || role !in listOf("me", "ai", "sys")) continue
                    tc.st.messages.add(ChatMsg(nextId++, role, v.optString("text"), v.optString("at"), v.optString("api"), uid, ts))
                    tc.known.add(uid)
                    tc.dirty = true
                    if (role == "ai") {
                        tc.st.retryAt = 0; tc.st.retryN = 0
                        if (!(viewing && p == pid)) unread++
                    }
                }
                kind == "pro" && v is String -> { touch(rest).st.proKey = v; touch(rest).dirty = true }
                kind == "retry" -> {
                    val o = v as? JSONObject
                    val tc = touch(rest)
                    tc.st.retryAt = o?.optLong("at", 0L) ?: 0L
                    tc.st.retryN = o?.optInt("n", 0) ?: 0
                    tc.dirty = true
                }
            }
        }
        for ((p, tc) in touched) {
            if (!tc.dirty) continue
            tc.st.messages.sortWith(compareBy({ it.ts }, { it.uid }))
            storeState(p, tc.st)
        }
        if (clearedActive) { unread = 0; error = ""; epoch++ }
        if (touched.isNotEmpty()) changed()
    }

    @Synchronized
    fun set(persona: String? = null, showAvatar: Boolean? = null,
            avatarMe: String? = null, avatarAi: String? = null, userProfile: String? = null) {
        persona?.let { switchTo(it); saveChat() }
        userProfile?.let { this.userProfile = it.trim().take(MAX_PROFILE) }
        showAvatar?.let { this.showAvatar = it }
        avatarMe?.let { this.avatarMe = it.trim().take(4) }
        avatarAi?.let { this.avatarAi = it.trim().take(4) }
        saveCfg()
        changed()
    }

    private fun ageSeconds(m: ChatMsg): Double {
        val at = parseIso(m.at) ?: return 1e9
        return secondsBetween(at, now())
    }

    // ---- 人设文件夹 ----

    fun ensurePersonas() {
        runCatching {
            personas.mkdirs()
            val f = File(personas, "README.md")
            val text = CommText.readme()
            if (!f.exists() || f.readText(Charsets.UTF_8) != text) f.writeText(text, Charsets.UTF_8)
        }
    }

    fun listPersonas(): List<String> =
        (personas.listFiles() ?: emptyArray()).filter {
            it.isFile && PERSONA_EXT.any { e -> it.name.lowercase().endsWith(e) } && it.name.lowercase() != "readme.md"
        }.map { it.name }.sortedBy { it.lowercase() }

    fun personaTitle(name: String) = name.substringBeforeLast('.')

    /** (文件名, 显示的名字): 不带后缀; 只有两个文件除后缀外同名时才保留后缀. */
    fun personaLabels(): List<Pair<String, String>> {
        val names = listPersonas()
        return names.map { n -> n to (if (names.count { personaTitle(it) == personaTitle(n) } > 1) n else personaTitle(n)) }
    }

    fun readPersona(name: String): String {
        if (name.isEmpty() || name != File(name).name) return ""
        return runCatching { File(personas, name).readText(Charsets.UTF_8).removePrefix("\uFEFF").take(MAX_PERSONA).trim() }.getOrDefault("")
    }

    /** 把外面的一个文件复制进人设文件夹, 返回新文件名 (重名加序号); 不是 .txt / .md 返回 "". */
    fun importPersona(displayName: String, input: InputStream): String {
        val base = File(displayName).name
        val ext = if (base.contains('.')) "." + base.substringAfterLast('.').lowercase() else ""
        if (ext !in PERSONA_EXT || base.lowercase() == "readme.md") return ""
        ensurePersonas()
        val stem = base.substringBeforeLast('.')
        var name = base
        var n = 1
        while (File(personas, name).exists()) { n++; name = "$stem ($n)${base.substring(stem.length)}" }
        return runCatching {
            File(personas, name).outputStream().use { out -> input.copyTo(out) }
            name
        }.getOrDefault("")
    }

    fun personaName(): String = persona.takeIf { it in listPersonas() } ?: ""

    // ---- 状态 ----

    fun aiConfig() = AiConfig(File(dir, "ai_config.json"))

    /** 现在不能聊天的原因 ("" = 可以). */
    fun problem(fresh: Boolean = false): String {
        blocked().let { if (it.isNotEmpty()) return it }
        val t = System.currentTimeMillis()
        if (fresh || t - problemAt > 5000) {
            problemAt = t
            problemCache = try {
                Ai.checkProfile(aiConfig().profile()); ""
            } catch (e: AiError) {
                e.message ?: "API"
            }
        }
        if (problemCache.isNotEmpty()) return problemCache
        if (personaName().isEmpty()) return "还没有选人设"
        return ""
    }
    private var problemAt = 0L
    private var problemCache = ""

    // ---- 头像: 圆形 PNG (ui/AvatarCrop.kt 里选图并截取); 对方的头像跟着人设走 ----

    fun avatarFile(who: String): File {
        val name = if (who == "me") "me.png" else {
            val crc = java.util.zip.CRC32().apply { update(personaTitle(personaName()).toByteArray(Charsets.UTF_8)) }
            "ai_%08x.png".format(crc.value)
        }
        return File(File(dir, "avatars"), name)
    }

    /** 头像文件 (没有就是 null). */
    fun avatarPath(who: String): File? = avatarFile(who).takeIf { it.isFile }

    @Synchronized
    fun saveAvatar(who: String, bitmap: android.graphics.Bitmap): Boolean {
        val f = avatarFile(who)
        val ok = runCatching {
            f.parentFile?.mkdirs()
            f.outputStream().use { bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }
        }.getOrDefault(false)
        changed()
        return ok
    }

    @Synchronized
    fun removeAvatar(who: String) { avatarFile(who).delete(); changed() }

    fun visible() = messages.filter { it.role == "me" || it.role == "ai" }

    private fun unanswered() = visible().lastOrNull()?.role == "me"

    @Synchronized
    fun setViewing(on: Boolean) {
        viewing = on
        if (on && unread != 0) { unread = 0; changed() }
    }

    // ---- 请求内容 ----

    fun apiMessages(): List<Pair<String, String>> {
        val out = ArrayList<Pair<String, String>>()
        // 总字数太多时从最老的开始不带 (至少带最新的一条)
        var total = 0
        val kept = ArrayList<ChatMsg>()
        for (m in messages.takeLast(HISTORY_SENT).asReversed()) {
            total += m.api.ifEmpty { m.text }.length
            if (total > HISTORY_CHARS && kept.isNotEmpty()) break
            kept.add(m)
        }
        for (m in kept.asReversed()) {
            val role = if (m.role == "ai") "assistant" else "user"
            val text = m.api.ifEmpty { m.text }
            if (text.isEmpty()) continue
            if (out.isNotEmpty() && out.last().first == role) out[out.size - 1] = role to (out.last().second + "\n\n" + text)
            else out.add(role to text)
        }
        while (out.isNotEmpty() && out.first().first == "assistant") out.removeAt(0)
        return out
    }

    /** 发给模型的内容: 每条用户消息前只有时间; 整点记录 / 计时器等数据只有用户手动选了 📎 附件才发. */
    private fun compose(t: LocalDateTime, body: String): String = "[${CommText.stamp(t)}]\n" + body

    private fun add(role: String, text: String, api: String = ""): ChatMsg {
        val ts = maxOf(nowMs(), lastTs + 1)             // 同一台设备上严格递增
        lastTs = ts
        val m = ChatMsg(nextId++, role, text, now().iso(), api, "$ts-${device().ifEmpty { "local" }.take(4)}", ts)
        messages.add(m)
        return m
    }

    /** 用户发出一条之后的收尾. */
    private fun sentOut() {
        error = ""; retryAt = 0; retryN = 0
        saveCfg(); saveChat(); changed(); push()
    }

    // ---- 发消息 ----

    /** 用户发一条. 返回 "" = 已发出, "empty" = 空消息, 否则是不能发的原因. */
    @Synchronized
    fun send(text0: String): String {
        val text = text0.trim()
        if (text.isEmpty() && CommData.label(attach).isEmpty()) return "empty"
        if (text.lowercase() == "/clear") { clear(); return "" }
        if (CommData.label(attach).isNotEmpty()) {            // 有确定过的附加数据: 和这条消息一起发
            val r = sendData(attach, text)
            if (r.isEmpty()) setAttach(CommSel())
            return r
        }
        val why = problem(true)
        if (why.isNotEmpty()) return why
        add("me", text, compose(now(), text))
        sentOut()
        request("user")
        return ""
    }

    /** 发送统计 / 日历 / 合约词条的数据: 聊天里只显示一行说明, 数据附在发给模型的内容里. text: 输入框里已经写的字, 一起发. */
    @Synchronized
    fun sendData(sel: CommSel, text0: String = ""): String {
        val label = CommData.label(sel)
        if (label.isEmpty()) return "empty"
        val why = problem(true)
        if (why.isNotEmpty()) return why
        val data = dataFn(sel)
        if (data.isEmpty()) return if (fog()) "黑雾中无法发送" else "没有可发送的数据"
        val text = text0.trim()
        add("me", label + if (text.isEmpty()) "" else "\n$text",
            compose(now(), "(用户发来的数据)\n$data" + if (text.isEmpty()) "" else "\n\n$text"))
        sentOut()
        request("user")
        return ""
    }

    /** 系统要求对方主动发 / 重试: 这条系统消息先不进记录, 对方真的回复了才和回复一起记下 (被拒绝 / 出错就当没发过). */
    private fun trigger(kind: String, body: String) {
        request(kind, Extra(body, compose(now(), body)))
    }

    private fun request(kind: String, extra: Extra? = null) {
        if (busy) { again = true; return }
        val profile = aiConfig().profile()
        var system = CommText.prefixPrompt() + readPersona(personaName())
        if (userProfile.isNotBlank())                       // 用户在 设置 → 沟通 里自己填的身份背景
            system += "\n\n以下是用户的身份背景(用户自己填写):\n" + userProfile.trim()
        val msgs = ArrayList(apiMessages())
        if (extra != null) {
            if (msgs.isNotEmpty() && msgs.last().first == "user") msgs[msgs.size - 1] = "user" to (msgs.last().second + "\n\n" + extra.api)
            else msgs.add("user" to extra.api)
        }
        val ep = epoch
        busy = true
        busySince = System.currentTimeMillis()
        val started = busySince
        log("REQ $kind start model=${profile.model} host=${runCatching { java.net.URL(profile.baseUrl).host }.getOrDefault("?")} " +
            "msgs=${msgs.size} chars=${msgs.sumOf { it.second.length } + system.length}")
        changed()
        Thread {
            val res = try {
                var convo: List<Pair<String, String>> = msgs
                var reply = chatFn(profile, system, convo)
                // 对方回 [refuse to reply][contract] = 要词条库: 把资料给它, 再调用一次让它回复用户 (资料不写进记录)
                for (round in 0 until MAX_INFO_ROUNDS) {
                    val blocks = CommText.infoTags(reply).mapNotNull { t ->
                        runCatching { infoFn(t) }.getOrDefault("").takeIf { it.isNotEmpty() }?.let { "【$t】\n$it" }
                    }
                    if (blocks.isEmpty()) break
                    convo = convo + ("assistant" to reply) + ("user" to ("(OIT 系统提供的资料, 用户看不到)\n\n" + blocks.joinToString("\n\n") +
                        "\n\n以上是**用户自己的** OIT 数据和格式. 如果用户问的是你自己的使用情况, 请参照这个格式和写法, 根据人设编造你自己的数据来回答," +
                        " 不要把用户的数据说成是你的; 然后回复用户上面的消息. 如果此刻仍然不方便回复, 返回${CommText.REFUSE}."))
                    reply = chatFn(profile, system, convo)
                }
                log("REQ $kind ok ${(System.currentTimeMillis() - started) / 1000.0}s reply_chars=${reply.length}")
                Result(kind, extra, reply, "", ep)
            } catch (e: AiError) {
                log("REQ $kind FAIL ${(System.currentTimeMillis() - started) / 1000.0}s ${e.kind}: ${e.message}")
                Result(kind, extra, "", e.message ?: "", ep)
            } catch (e: Exception) {
                log("REQ $kind FAIL ${(System.currentTimeMillis() - started) / 1000.0}s ${e.javaClass.simpleName}: ${e.message}")
                Result(kind, extra, "", e.javaClass.simpleName, ep)
            }
            results.add(res)
        }.apply { isDaemon = true }.start()
    }

    private fun finish(r: Result) {
        busy = false
        busySince = 0L
        if (hardBlock()) { again = false; changed(); return }       // 等回复的时候"无言为真"生效了: 这条回复不要了
        if (r.epoch != epoch) { again = false; changed(); return }       // 发出之后清空过聊天记录: 这条回复不要了
        val refused = r.err.isEmpty() && CommText.isRefusal(r.reply)
        if (refused) log("REPLY refused (${r.kind}): 对方回了 [refuse to reply], 不显示" + if (unanswered()) ", 稍后重试" else "")
        if (r.err.isNotEmpty() || refused) {
            error = r.err
            if (refused && unanswered()) {
                retryN++
                retryAt = if (retryN <= RETRY_MAX) nowMs() else 0L
                saveCfg()
            }
        } else {
            error = ""; retryAt = 0; retryN = 0
            saveCfg()
            r.extra?.let { add("sys", it.body, it.api) }
            add("ai", r.reply.trim())
            if (!viewing) unread++
        }
        saveChat(); changed(); push()
        if (again) {
            again = false
            if (unanswered() && r.err.isEmpty()) request("user")
        }
    }

    /** 大约每秒调用一次 (Core.tick). */
    @Synchronized
    fun tick() {
        while (true) finish(results.poll() ?: break)
        if (busy || problem().isNotEmpty() || !leader()) return
        val t = now()
        val snap = snapshot()
        // 对方不方便时: 隔一阵把没回的消息再交给模型
        if (retryAt != 0L && nowMs() >= retryAt + RETRY_GAP * 1000L) {
            retryAt = 0
            saveCfg()
            if (unanswered()) {
                trigger("retry", "现在是 ${CommText.stamp(t)}. 用户之前发来的消息你还没有回复, 请现在一次性处理并回复; 如果仍然不方便, 返回${CommText.REFUSE}.")
                return
            }
        }
        // 休息开始 3~5 分钟后主动发一条
        val bs = snap?.breakStart
        if (snap != null && snap.state == "break" && bs != null && snap.breakTotalS > PROACTIVE_MIN_REST) {
            val key = bs.iso()
            if (key != proKey) {
                val due = bs.plusSeconds(180L + java.util.Random(key.hashCode().toLong()).nextInt(121))
                if (!t.isBefore(due)) {
                    proKey = key
                    if (secondsBetween(due, t) <= 120) {
                        trigger("proactive", "[系统] 用户刚开始休息, 这次计划休息约 ${Math.round(snap.breakTotalS / 60)} 分钟. " +
                            "请根据人设主动给用户发一条消息, 话题自己找; 如果此刻不方便, 返回${CommText.REFUSE}.")
                    }
                    saveCfg(); push()
                }
            }
        }
    }

    /** 清空聊天记录 (界面上的按钮, 或者在聊天框里发 /clear): 以后不再显示, 也不再发给模型; 别的设备也一起清空.
     */
    @Synchronized
    fun clear() {
        clearTs = maxOf(nowMs(), lastTs)
        messages.clear(); unread = 0; retryAt = 0; retryN = 0; error = ""
        epoch++
        saveCfg(); saveChat(); changed(); push()
    }
}
