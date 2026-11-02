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
class ChatMsg(val id: Int, val role: String, val text: String, val at: String, val api: String = "")

/** 发给对方的计时器状态. state: focus / break / idle / sleep / out. */
class CommSnap(
    val state: String, val restLeftMin: Double = 0.0, val focusTotalMin: Double = 0.0, val focusCurMin: Double = 0.0,
    val breakStart: LocalDateTime? = null, val breakTotalS: Double = 0.0,
)

/** 沟通的文字: 前置 prompt, README. 对应桌面版 comm.py. */
object CommText {
    const val REFUSE = "[refuse to reply]"
    private const val WEEKDAY = "一二三四五六日"

    fun restFunctionText(): String {
        val g = REST_GAIN.toString()
        return "专注 t 分钟后建议休息 F(t) 分钟: t≤15 时 F=$g·t/6; 15<t<150 时 " +
            "F=$g·(7/14850·t²+407/1485·t−170/99); t≥150 时 F=$g·(50+5/ln1.2·ln(t/150)); " +
            "例如专注 50 分钟休息约 ${"%.0f".format(computeF(50.0))} 分钟"
    }

    fun prefixPrompt(): String =
        "你需要回复来自自律软件OIT用户的信息,根据以下设定回复\n" +
            "I.设定上你也是OIT的用户,你需要根据用户为你设定的人设安排自己的日程与行动,你不能出戏(比如强调自己是AI)\n" +
            "专注时间-休息时间函数如下:[${restFunctionText()}],如果没有强调,默认你的每轮专注时间总是50min,并且一休息完就继续专注.\n" +
            "II.根据日程,你在专注/外出/睡觉和其他不能随时回复的时段是不能回复消息的:返回$REFUSE" +
            "则OIT不会显示你的回复,你到下一次空闲时需要一次性处理用户发来的消息.\n" +
            "III.系统有的时候会要求你主动发消息,你需要根据人设自己找话题.\n" +
            "IV.根据你的专注时间段 你可能需要主动结束话题.你可以稍微延迟你的进入专注的时间点,不超过5min.\n" +
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

    private fun hm(minutes: Double): String {
        val m = Math.round(maxOf(0.0, minutes)).toInt()
        return if (m >= 60) "${m / 60} 小时 ${m % 60} 分钟" else "$m 分钟"
    }

    private val STATE = mapOf("focus" to "专注中", "break" to "休息中", "idle" to "空闲", "sleep" to "睡觉中", "out" to "外出中")

    fun timerLine(s: CommSnap): String {
        val left = if (s.state == "break") hm(s.restLeftMin) else "无"
        val cur = if (s.state == "focus") hm(s.focusCurMin) else "0 分钟(未在专注)"
        return "目前状态: ${STATE[s.state] ?: s.state}; 剩余休息时间: $left; 今日总专注时长: ${hm(s.focusTotalMin)}; 目前这轮专注时长: $cur"
    }

    fun isRefusal(text: String) = REFUSE in text.replace(Regex("\\s+"), " ").lowercase()
}

class Comm(
    val dir: File,
    var personas: File,
    private val now: () -> LocalDateTime = LocalDateTime::now,
    private val chatFn: (Profile, String, List<Pair<String, String>>) -> String =
        { p, s, m -> Ai.chatMessages(p, s, m, Comm.CHAT_TOKENS, Comm.CHAT_TEMPERATURE) },
) {
    companion object {
        const val RESEND_GAP = 30 * 60
        const val PROACTIVE_MIN_REST = 20 * 60
        const val RETRY_GAP = 10 * 60
        const val RETRY_MAX = 12
        const val HISTORY_SENT = 60
        const val KEEP = 400
        const val MAX_PERSONA = 20000
        const val CHAT_TOKENS = 1000
        const val CHAT_TEMPERATURE = 0.8
        val PERSONA_EXT = listOf(".txt", ".md")
    }

    private val cfgFile = File(dir, "comm_config.json")
    private val chatFile = File(dir, "comm_chat.json")
    var persona = ""; private set
    var sendHour = true; private set
    var sendTimer = true; private set
    var showAvatar = false; private set
    var avatarMe = ""; private set
    var avatarAi = ""; private set
    private val lastSent = HashMap<String, String>()
    private var proKey = ""
    val messages = ArrayList<ChatMsg>()
    @Volatile var version = 0; private set
    @Volatile var unread = 0; private set
    @Volatile var busy = false; private set
    @Volatile var error = ""; private set
    @Volatile var viewing = false; private set
    /** 📎 勾选的内容 (统计 / 日历 / 合约词条); Core 接上. 空 = 没有可发的. */
    var dataFn: (CommSel) -> String = { "" }
    var fog: () -> Boolean = { false }
    /** 现在不能用沟通的原因 ("" = 可以): 合约的"无言为真". Core 接上. */
    var blocked: () -> String = { "" }
    /** 📎 窗口点"确定"记下的附加数据: 随下一条消息一起发 (发出去之后清掉). */
    @Volatile var attach = CommSel(); private set
    /** 用户确定过一次之后, 📎 窗口不再默认勾 整点记录 / 专注计时. */
    @Volatile var attachTouched = false; private set

    @Synchronized
    fun setAttach(sel: CommSel) { attach = sel; attachTouched = true; changed() }

    /** 📎 窗口打开时的勾选: 确定过的; 还没确定过 = 默认勾 整点记录 和 专注计时. */
    fun attachForm(): CommSel =
        if (attachTouched || CommData.label(attach).isNotEmpty()) attach else CommSel(stats = CommData.DEFAULT_STATS)
    var hourLines: () -> List<String> = { emptyList() }
    var snapshot: () -> CommSnap? = { null }
    private class Result(val kind: String, val trig: Int, val reply: String, val err: String, val epoch: Int)
    private val results = ConcurrentLinkedQueue<Result>()
    private var again = false
    private var retryAt: LocalDateTime? = null
    private var retries = 0
    private var nextId = 1
    /** clear() 加一: 清空之前发出的请求, 回复到了也丢掉. */
    private var epoch = 0

    init {
        load()
        ensurePersonas()
        val last = messages.lastOrNull()
        if (last != null && last.role == "me" && ageSeconds(last) < 6 * 3600) retryAt = now().plusSeconds(60)
    }

    // ---- 存取 ----

    private fun load() {
        Store.readObj(cfgFile)?.let { d ->
            persona = d.optString("persona", "")
            sendHour = d.optBoolean("send_hour", true)
            sendTimer = d.optBoolean("send_timer", true)
            showAvatar = d.optBoolean("show_avatar", false)
            avatarMe = d.optString("avatar_me", "")
            avatarAi = d.optString("avatar_ai", "")
            proKey = d.optString("pro_key", "")
            d.optJSONObject("last_sent")?.let { o -> o.keys().forEach { k -> lastSent[k] = o.optString(k) } }
        }
        val arr = Store.readObj(chatFile)?.optJSONArray("messages")
        if (arr != null) for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val role = o.optString("role")
            if (role in listOf("me", "ai", "sys"))
                messages.add(ChatMsg(o.optInt("id"), role, o.optString("text"), o.optString("at"), o.optString("api")))
        }
        nextId = (messages.maxOfOrNull { it.id } ?: 0) + 1
    }

    // 写盘 (fsync) 放在后台线程: 发送 / 收到消息时界面线程不等磁盘, 不卡. 先在这里做好快照, 线程只管写.
    private val writer = java.util.concurrent.Executors.newSingleThreadExecutor { r -> Thread(r, "comm-save").apply { isDaemon = true } }

    /** 等后台的写盘做完 (自检). */
    fun flush() { writer.submit {}.get() }

    private fun saveCfg() {
        val o = JSONObject().put("persona", persona).put("send_hour", sendHour).put("send_timer", sendTimer)
            .put("show_avatar", showAvatar).put("avatar_me", avatarMe).put("avatar_ai", avatarAi).put("pro_key", proKey)
            .put("last_sent", JSONObject(lastSent as Map<*, *>))
        writer.execute { Store.write(cfgFile, o) }
    }

    private fun saveChat() {
        while (messages.size > KEEP) messages.removeAt(0)
        val arr = JSONArray()
        for (m in messages) arr.put(JSONObject().put("id", m.id).put("role", m.role).put("text", m.text).put("at", m.at)
            .apply { if (m.api.isNotEmpty()) put("api", m.api) })
        val o = JSONObject().put("messages", arr)
        writer.execute { Store.write(chatFile, o) }
    }

    private fun changed() { version++ }

    @Synchronized
    fun set(persona: String? = null, sendHour: Boolean? = null, sendTimer: Boolean? = null, showAvatar: Boolean? = null,
            avatarMe: String? = null, avatarAi: String? = null) {
        persona?.let { this.persona = it }
        sendHour?.let { this.sendHour = it }
        sendTimer?.let { this.sendTimer = it }
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
        for (m in messages.takeLast(HISTORY_SENT)) {
            val role = if (m.role == "ai") "assistant" else "user"
            val text = m.api.ifEmpty { m.text }
            if (text.isEmpty()) continue
            if (out.isNotEmpty() && out.last().first == role) out[out.size - 1] = role to (out.last().second + "\n\n" + text)
            else out.add(role to text)
        }
        while (out.isNotEmpty() && out.first().first == "assistant") out.removeAt(0)
        return out
    }

    private fun info(snap: CommSnap?, t: LocalDateTime): String {
        val parts = ArrayList<String>()
        fun due(key: String): Boolean {
            val at = parseIso(lastSent[key]) ?: return true
            return secondsBetween(at, t) >= RESEND_GAP
        }
        if (sendHour && due("hour")) {
            val lines = hourLines()
            if (lines.isNotEmpty()) { parts.add("整点记录: " + lines.joinToString("; ")); lastSent["hour"] = t.iso() }
        }
        if (sendTimer && due("timer") && snap != null) {
            parts.add("计时器: " + CommText.timerLine(snap)); lastSent["timer"] = t.iso()
        }
        return parts.joinToString("\n")
    }

    private fun compose(t: LocalDateTime, body: String): String {
        val info = info(snapshot(), t)
        return "[${CommText.stamp(t)}]\n" + (if (info.isNotEmpty()) "(OIT 附带信息)\n$info\n" else "") + body
    }

    private fun add(role: String, text: String, api: String = ""): ChatMsg {
        val m = ChatMsg(nextId++, role, text, now().iso(), api)
        messages.add(m)
        return m
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
        error = ""; retries = 0; retryAt = null
        saveCfg(); saveChat(); changed()
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
        error = ""; retries = 0; retryAt = null
        saveCfg(); saveChat(); changed()
        request("user")
        return ""
    }

    private fun trigger(kind: String, body: String) {
        val m = add("sys", body, compose(now(), body))
        saveCfg()
        request(kind, m.id)
    }

    private fun request(kind: String, trig: Int = 0) {
        if (busy) { again = true; return }
        val profile = aiConfig().profile()
        val system = CommText.prefixPrompt() + readPersona(personaName())
        val msgs = apiMessages()
        val ep = epoch
        busy = true
        changed()
        Thread {
            val res = try {
                Result(kind, trig, chatFn(profile, system, msgs), "", ep)
            } catch (e: AiError) {
                Result(kind, trig, "", e.message ?: "", ep)
            } catch (e: Exception) {
                Result(kind, trig, "", e.javaClass.simpleName, ep)
            }
            results.add(res)
        }.apply { isDaemon = true }.start()
    }

    private fun finish(r: Result) {
        busy = false
        if (blocked().isNotEmpty()) { again = false; changed(); return }       // 等回复的时候"无言为真"生效了: 这条回复不要了
        if (r.epoch != epoch) { again = false; changed(); return }       // 发出之后清空过聊天记录: 这条回复不要了
        val t = now()
        if (r.err.isNotEmpty()) {
            error = r.err
            if (r.trig != 0) messages.removeAll { it.id == r.trig }
        } else if (CommText.isRefusal(r.reply)) {
            error = ""
            if (r.trig != 0) messages.removeAll { it.id == r.trig }
            if (unanswered() && retries < RETRY_MAX) { retries++; retryAt = t.plusSeconds(RETRY_GAP.toLong()) }
        } else {
            error = ""; retries = 0; retryAt = null
            add("ai", r.reply.trim())
            if (!viewing) unread++
        }
        saveChat(); changed()
        if (again) {
            again = false
            if (unanswered() && r.err.isEmpty()) request("user")
        }
    }

    /** 大约每秒调用一次 (Core.tick). */
    @Synchronized
    fun tick() {
        while (true) finish(results.poll() ?: break)
        if (busy || problem().isNotEmpty()) return
        val t = now()
        val snap = snapshot()
        val ra = retryAt
        if (ra != null && !t.isBefore(ra)) {
            retryAt = null
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
                    } else saveCfg()
                }
            }
        }
    }

    /** 清空聊天记录 (界面上的按钮, 或者在聊天框里发 /clear): 以后不再显示, 也不再发给模型.
     *  之前附带过的信息也一起没了, 所以"30 分钟内不重复"的记录清零. */
    @Synchronized
    fun clear() {
        messages.clear(); unread = 0; retryAt = null; retries = 0; error = ""
        epoch++
        lastSent.clear()
        saveCfg(); saveChat(); changed()
    }
}
