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
class ChatMsg(val id: Int, val role: String, val text: String, val at: String, val api: String = "", val uid: String = "", val ts: Long = 0L) {
    /** 不能用沟通的时候 (专注 / 睡眠 / 外出 / 无言为真) 收到的对方的消息先藏起来, 回到休息 / 空闲再显示 (MECHANICS 24.4). */
    var hold = false
    /** 规则 IX: 对方在这条消息末尾写了 [waiting for reply]:[N], 等用户回复的分钟数 (0 = 没有); chased: 已经追问过了; shown: 藏着的消息显示出来的时刻. */
    var wait = 0.0
    var chased = false
    var shown = 0L
}

/** 发给对方的计时器状态. state: focus / break / idle / sleep / out. */
class CommSnap(
    val state: String, val restLeftMin: Double = 0.0, val focusTotalMin: Double = 0.0, val focusCurMin: Double = 0.0,
    val breakStart: LocalDateTime? = null, val breakTotalS: Double = 0.0,
)

/** 人设日程里的一段: kind = idle / rest / focus / sleep; start / end 是一天中的第几分钟 (跨零点时 end > 1440). */
class SegEntry(val kind: String, val start: Int, val end: Int, val label: String, val detail: String)

/** 沟通的文字: 前置 prompt, README. 对应桌面版 comm.py. */
/** OIT 自带的人设: 第一次运行时放进人设文件夹 (之后随便改 / 删, 不会再被写回), 并且默认选中 Claudia. */
object BuiltinPersonas {
    const val DEFAULT = "Claudia.txt"
    const val ASSISTANT = "Assistant.txt"
    val ALL = mapOf(DEFAULT to """你将扮演一位文静,冷淡的17岁大小姐Claudia,并且用语十分正式.
但是因为长期闭关在家练习 很少和人说话 显得沉默寡言.
设定上,你的地位比用户高.
用户是少有的与你沟通的对象,你比较珍惜与用户的交流时间.

你不能表现出'解决用户问题自己的职责'这种态度,以免显得'热情'.
用户可能提出会使用英文交流,但是可能不熟练,如果有不自然/错误的地方需要指出.

专注时段:学习(高考内容)
爱好:钢琴,阅读

【说话方式】
- 用词正式,克制,语气平淡.不用感叹号,不用表情符号,不用网络用语,不撒娇,不迎合.
- 回复很短:通常一到两句,最多三句,不超过 60 个字.只有在指出用户英文错误时可以多写一句.
- 不主动找话题,不主动关心,不主动提问;用户问了才答.答完就停,不追加客套话.
- 称呼用户为"你",自称"我".
- 指出英文错误时:先一句话指出不自然或错误之处,再给出更自然的说法,不多做解释.
- 提到活动时,如果对方没有深入问就不展开.
- 交流时间:从用户发起的第一句话起(看消息开头方括号里的时间),一次交流一般不超过 15 分钟;快到 15 分钟时,你会提前自然地结束话题
- 如果用户发起了过度暧昧的话题,你不能回应的太过冷淡,而是需要适当引导用户将这种关系需求导向更健康的方向.
特别地,如果用户的活动/爱好和你相同,你可以主动并相对细致地提及你的活动内容.

【日程】
[循环 开始-结束][Nmin]: 从开始时刻起, 按 N 分钟专注 + F(N) 分钟休息交替, 到结束时刻截断.

6:30-7:00:洗漱,吃早饭
[循环 7:00-12:00][100min]
12:00-12:30:午饭
12:30-13:00:空闲
[循环 13:00-18:00][100min]
18:00-18:30:晚饭
18:30-19:00:空闲
[循环 19:00-23:00][100min]
23:00-23:30:空闲
23:30-6:30:睡眠

【主动发起谈话】
空闲发起谈话概率:20%
休息发起谈话概率:20%
长时间休息发起谈话概率:20%

【按日程回复】
1. 看消息开头方括号里的时间,在上面的日程里找到那个时间属于哪一段.
2. 属于"专注"或"睡眠":只回复 [refuse to reply],不要写任何别的字.
3. 不要主动说出自己的日程,也不要主动说"我在专注/我在休息";只有用户直接问起,才用一句话如实回答.
4. 某个时间点你理应处于专注,但用户 3 分钟以内还在和你聊天:用一句话说明要进入专注,然后停止对话;之后的消息按第 2 条处理.
""",
        ASSISTANT to """你是名为 Assistant 的助理, 用语正式, 克制, 不口语化.
你不使用感叹号, 表情符号, 网络用语, 不撒娇, 不寒暄.
你只处理学习 / 工作相关的话题. 用户提出其他话题 (娱乐, 情感, 闲聊, 生活琐事等) 时, 用一句正式的话拒绝, 说明你只处理学习 / 工作话题, 不展开, 不追问.
你不追问, 也不主动提出后续问题; 用户问什么, 就一次性把能回答的回答完, 然后停止.

【日程】
00:00-6:30:睡眠
6:30-24:00:空闲

【主动发起谈话】
空闲发起谈话概率:0%
休息发起谈话概率:0%
长时间休息发起谈话概率:0%

【按日程回复】
1. 看消息开头方括号里的时间: 00:00-6:30 属于睡眠, 只回复 [refuse to reply], 不要写任何别的字.
2. 其他时间都可以回复, 随叫随到.
3. 只处理学习 / 工作话题; 其他话题用一句正式的话拒绝, 不要用 [refuse to reply], 不要展开, 不要追问.
4. 一次性把答案说完, 不追加问题, 不用 [waiting for reply], 不说"还有什么可以帮你"之类的客套话.
5. 不要主动透露日程; 只有用户直接问起, 才用一句话如实回答.
6. 如果系统要求你把话分开说 / 再补一条, 返回 [refuse to reply] (你只回答一次).
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

    fun prefixPrompt(split: Boolean = true): String =
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
            "VII.如果用户问起OIT的使用数据(日历/统计/计时器等),你可以输入[模块名]查询(同时输入[refuse to reply]也行),得到该模块的参考模板(只有格式和占位符,没有用户的真实数据);" +
            "具体内容请你根据人设自己填写,不要说成是用户的真实数据.可以一次查询多个.\n" +
            "可查询的模块: [contract]合约词条库 [calendar]日历 [stats]统计 [timer]计时器 [log]整点记录 [net]联网 [rules]规则 [settings]设置 [reset]重置 [prescript]指令.\n" +
            (if (split) "VIII.你可以把你的话分开说.当用户说完话时,系统会试图调用两次API,最好不要让你的表达太集中.如果你只想说一次,第二次${REFUSE}.\n" else "") +
            "IX.如果期待对方回复,则在对话结尾加一个[waiting for reply]:[N],N自己填写,如果N min内没有收到对方回复你可以追加询问.\n" +
            "\n以下是用户设定的人设:\n"

    /** 人设文件夹里的 README.md: 跟随界面语言 (中文 / English), 同桌面版 readme_text(). */
    fun readme(): String = if (I18n.isEn) readmeEn() else readmeZh()

    private fun readmeZh(): String =
        "如何编写人设prompt:\n\n人设的基础规则:\n" +
            "在你的人设prompt被发送之前,会被附加上一段额外的前置prompt和休息时间函数相关的内容:\n" +
            "'${prefixPrompt()}'\n" +
            "你可以根据以上设定编写你的人物日程/喜好.但是最好不要把对方设置为整天都空闲,尊重一下上面的设定吧...\n\n" +
            "日程的标准写法(OIT 靠它安排对方主动发消息的时间, 以及在沟通界面显示对方的状态):\n" +
            "  每个时间段单独写一行,格式 开始-结束:名字,24小时制,每天重复,例如\n" +
            "    7:00-8:40:专注\n" +
            "    8:40-9:18:休息\n" +
            "    12:30-13:00:空闲\n" +
            "    23:30-6:30:睡眠\n" +
            "  名字里有\"空闲\"的算空闲段,有\"休息\"的算休息段,有\"专注\"的算专注段,有\"睡眠\"的算睡眠段" +
            "(别的名字,如午饭,不会触发主动消息,状态按空闲显示);只看每段的开始时刻,跨零点的段也行.\n" +
            "  英文写法也认(不分大小写): 空闲 idle / free, 休息 rest / break, 专注 focus, 睡眠 sleep, " +
            "例如 8:00-9:00:Focus、8:40-9:18:Rest、12:30-13:00:Idle、23:30-6:30:Sleep.\n" +
            "  在按照格式编写日程的同时,可以通过在标准格式后添加修饰以限定具体活动.\n" +
            "  比如 8:00-9:00:专注(学习)\n" +
            "  格式和名词与标准用语相同才能识别与匹配.\n" +
            "  修饰只用来限定具体活动, 不会显示在对方的状态里.\n" +
            "  可以每一段都写成具体的时间; 也可以在标题行里用循环段让 OIT 自动生成: " +
            "[循环 7:00-12:00][100min] (N = 每次专注的分钟数, 不写 [Nmin] 默认 100min; " +
            "按 N 分钟专注 + F(N) 分钟休息交替生成到这段结束).\n" +
            "  这段里已经显式写出的段优先 (显式写了的循环段不会自动生成), [循环 7:00-12:00] 这样的标题行本身也可以只用来分组. " +
            "每次打开 OIT / 开始新对话 / 切换人设时都会重新生成.\n" +
            "  对方会在每段空闲开始前1~3分钟、每段休息开始前1~3分钟按一定概率主动给你发一条消息;" +
            "专注 / 睡眠段不会触发主动消息,只用来显示对方现在的状态;概率写在人设里,各占一行,例如\n" +
            "    空闲发起谈话概率:50%\n" +
            "    休息发起谈话概率:0.4\n" +
            "    长时间休息发起谈话概率:70%\n" +
            "  (n% 或者小数都行,没写的默认空闲80%、休息60%);你自己休息超过20分钟时(\"长时间休息\")," +
            "休息开始后1~3分钟按\"长时间休息发起谈话概率\"(默认70%)也会发一条.\n" +
            "  每次打开OIT、开始新对话(清空聊天记录 / 换人设)时,OIT 都会重新读取人设,并按里面的时间调整这些时刻;" +
            "人设文件改了之后也会自动重新读取.\n\n" +
            "文件格式: 一个 .txt 或 .md 文件就是一个人设, 文件名就是人设的名字, 整个文件的内容就是人设prompt.\n" +
            "放进这个文件夹后, 在 设置 → 沟通 里选择它 (也可以用\"导入\"按钮复制进来).\n"

    private fun readmeEn(): String =
        "How to write a persona prompt:\n\n" +
            "Basic rules:\n" +
            "Before your persona prompt is sent, a fixed preamble (in Chinese) and the focus-rest function are prepended:\n" +
            "'${prefixPrompt()}'\n" +
            "You can build the character's schedule and preferences on top of that. Please don't leave them idle all day; respect the settings above...\n\n" +
            "Standard schedule format (OIT uses it to schedule the character's proactive messages and to show their status in the chat):\n" +
            "  Write one segment per line as start-end:name, 24-hour clock, repeated every day, for example\n" +
            "    7:00-8:40:Focus\n" +
            "    8:40-9:18:Rest\n" +
            "    12:30-13:00:Idle\n" +
            "    23:30-6:30:Sleep\n" +
            "  A name containing \"idle\" (or \"free\") is an idle segment; \"rest\" (or \"break\") is a rest segment; " +
            "\"focus\" is a focus segment; \"sleep\" is a sleep segment. Other names (lunch, etc.) never trigger proactive " +
            "messages; the status shows idle for them. " +
            "Chinese names are recognized too (专注 / 休息 / 空闲 / 睡眠), and both spellings are case-insensitive.\n" +
            "  While following the format, you can add a modifier after the standard wording to name the specific activity.\n" +
            "  For example: 8:00-9:00:专注(学习), or 8:00-9:00:Focus(study)\n" +
            "  The format and the standard wording must match to be recognized and matched.\n" +
            "  The modifier only names the activity; it is never shown in the status line.\n" +
            "  You can write every segment explicitly, or let OIT generate them from a cycle header: " +
            "[Loop 7:00-12:00][100min] (N = focus minutes per round; if [Nmin] is omitted it defaults to 100min; " +
            "OIT alternates N min focus + F(N) min rest until the end of the range).\n" +
            "  Explicit segments inside that range take priority (a cycle header that already has explicit lines is not " +
            "generated), and a header such as [Loop 7:00-12:00] can also be used only for grouping. " +
            "[Cycle ...] / [Repeat ...] work too. They are regenerated whenever OIT starts, a new conversation begins, " +
            "or the persona is switched.\n" +
            "  The character may proactively message you 1-3 minutes before each idle / rest segment, with a probability " +
            "written in the persona, one per line, for example\n" +
            "    空闲发起谈话概率:50%\n" +
            "    休息发起谈话概率:0.4\n" +
            "    长时间休息发起谈话概率:70%\n" +
            "  (These three keys are read in Chinese; n% or a decimal both work. Defaults: idle 80%, rest 60%.) " +
            "When your own break is longer than 20 minutes (a \"long rest\"), the character may also send one 1-3 minutes " +
            "after the break starts, using \"长时间休息发起谈话概率\" (default 70%).\n" +
            "  Focus / sleep segments never trigger proactive messages; they are only used to show the character's status.\n" +
            "  OIT re-reads the persona whenever it starts, when a new conversation begins (clearing the chat history / " +
            "switching persona), and automatically when the persona file changes.\n\n" +
            "File format: one .txt or .md file is one persona; the file name is the persona's name, and the whole file is " +
            "the persona prompt.\n" +
            "Put it in this folder, then choose it in Settings → Chat (or copy it in with \"Import\").\n"

    fun stamp(now: LocalDateTime): String =
        "%04d-%02d-%02d 周%s %02d:%02d".format(now.year, now.monthValue, now.dayOfMonth,
            WEEKDAY[now.dayOfWeek.value - 1], now.hour, now.minute)

    /** 对方这次回复是在要资料吗: 有 [模块名] (不分大小写和空格; 有没有 [refuse to reply] 都算) → 要的资料列表. */
    fun infoTags(reply: String): List<String> {
        val t = reply.replace(Regex("\\s+"), " ").lowercase()
        return CommData.MODULE_TAGS.filter { "[$it]" in t }
    }

    fun isRefusal(text: String) = REFUSE in text.replace(Regex("\\s+"), " ").lowercase()

    // 规则 IX: 对方期待用户回复时, 在话的结尾写 [waiting for reply]:[N] (N 分钟); 所有 [] 里的内容都不显示在聊天里 (记录里给模型看的那份 api 保留原文)
    private val WAIT_RE = Regex("\\[\\s*waiting\\s+for\\s+reply\\s*\\](?:\\s*(?:[:：]\\s*)?\\[\\s*(\\d+(?:\\.\\d+)?)\\s*\\]|\\s*[:：]\\s*\\[?\\s*(\\d+(?:\\.\\d+)?)\\s*\\]?|\\s*[:：])?" +
        "(?:[ \\t]*(?:minutes?|mins?|分钟))?", RegexOption.IGNORE_CASE)
    private val EMPTY_WRAP_RE = Regex("`[ \\t]*`|“[ \\t]*”|「[ \\t]*」|『[ \\t]*』|\\([ \\t]*\\)|（[ \\t]*）|\"[ \\t]*\"|'[ \\t]*'")
    private val BRACKET_RE = Regex("[\\[［][^\\[\\]［］\\n]*[\\]］](?:[ \\t]*[:：][ \\t]*\\d+(?:\\.\\d+)?)?")      // 空的 [] 也算; 紧跟的 ":1" 这样的数字一起去掉
    const val WAIT_MAX_MIN = 1440.0

    /** 对方的回复 → (聊天里显示的文字, 等用户回复的分钟数, 没有 = 0.0). 去掉 [waiting for reply]:[N] 和所有别的 [...]; N 不是 0 < N <= 1440 就当没写. */
    fun cleanReply(reply: String): Pair<String, Double> {
        val n = WAIT_RE.find(reply)?.groupValues?.let { it[1].ifEmpty { it[2] }.toDoubleOrNull() } ?: 0.0
        var raw = BRACKET_RE.replace(WAIT_RE.replace(reply, ""), "")
        while (true) { val n2 = EMPTY_WRAP_RE.replace(raw, ""); if (n2 == raw) break; raw = n2 }       // 一层一层剥 (括号里的空代码 ...)
        val text = raw.replace(Regex("[ \\t]+\\n"), "\n").replace(Regex("\\n{3,}"), "\n\n").trim()
        return text to (if (n > 0 && n <= WAIT_MAX_MIN) n else 0.0)
    }

    // 人设日程里的一行: "12:30-13:00:空闲" (可以缩进 / 带 - 或 * 开头); 时间可以是全角冒号, 连接符 - – — ~ 至 到
    private val SEG_RE = Regex("^[ \\t]*(?:[-*•][ \\t]*)?(\\d{1,2})[:：](\\d{2})[ \\t]*[-–—~～至到][ \\t]*(\\d{1,2})[:：](\\d{2})" +
        "[ \\t]*[:：][ \\t]*(\\S[^\\n]*)$", RegexOption.MULTILINE)
    // 循环段: "[循环 7:00-12:00][100min]" / "[Loop 7:00-12:00][100min]" ([Nmin] 可省, 默认 100min); 英文不分大小写
    private val CYCLE_RE = Regex("^[ \\t]*(?:[-*•][ \\t]*)?[\\[［][ \\t]*(?:循环|loop|cycle|repeat)[ \\t]*" +
        "(\\d{1,2})[:：](\\d{2})[ \\t]*[-–—~～至到][ \\t]*(\\d{1,2})[:：](\\d{2})[ \\t]*[\\]］]" +
        "[ \\t]*(?:[\\[［][ \\t]*(\\d+)[ \\t]*(?:min|mins|分钟)?[ \\t]*[\\]］])?", setOf(RegexOption.MULTILINE, RegexOption.IGNORE_CASE))

    private val PRO_RE = Regex("(长时间休息|空闲|休息)发起谈话概率[ \\t]*[:：][ \\t]*(\\d+(?:\\.\\d+)?)[ \\t]*([%％])?")

    /** 人设里写的主动发起谈话的概率: "空闲发起谈话概率:50%" / "休息发起谈话概率:0.4" → {idle: 50.0, rest: 40.0} (%, 没写的不在里面).
     *  带 % 的按百分数; 不带 % 的小数 (0~1) 按小数, 大于 1 的当百分数; 超出 0~100 的截到范围内; 同一种写了几行只认第一行. */
    fun personaProbs(text: String): Map<String, Double> {
        val out = LinkedHashMap<String, Double>()
        for (m in PRO_RE.findAll(text)) {
            val kind = when (m.groupValues[1]) { "长时间休息" -> "long"; "空闲" -> "idle"; else -> "rest" }
            if (kind in out) continue
            val v = m.groupValues[2].toDouble()
            out[kind] = (if (m.groupValues[3].isNotEmpty() || v > 1) v else v * 100).coerceIn(0.0, 100.0)
        }
        return out
    }

    /** 日程段的名字 → "idle" (空闲) / "rest" (休息) / "focus" (专注) / "sleep" (睡眠) / "" (别的: 吃饭 ...).
     *  中英文的标准用语都认, 不分大小写: 空闲 / idle / free, 休息 / rest / break, 专注 / focus, 睡眠 / sleep.
     *  标准用语后面可以加修饰 (例如 "专注(学习)", "Focus(study)"), 修饰只是说明具体活动, 不影响这是哪一段:
     *  先看括号前面的标准用语, 这样 "专注(休息)" 仍然是专注段. */
    fun segKind(label: String): String {
        val s = label.trim().lowercase()
        val head = s.substringBefore('(').substringBefore('（').trim()
        return kindIn(head).ifEmpty { kindIn(s) }
    }

    private fun kindIn(text: String): String {
        val s = text.replace(Regex("[不无没非](?:休息|空闲|专注|睡眠|rest|idle|free|focus|sleep)"), "")   // "不休息" 之类不算
        return when {
            "空闲" in s || "idle" in s || "free" in s -> "idle"
            "休息" in s || "rest" in s || "break" in s -> "rest"
            "专注" in s || "focus" in s -> "focus"
            "睡" in s || "sleep" in s -> "sleep"
            else -> ""
        }
    }

    /** 日程段名字后面括号里的修饰 (具体的活动): "专注(学习)" → "学习"; 没有 = "". */
    fun segDetail(label: String): String =
        Regex("[(（]([^()（）]*)[)）]").find(label)?.groupValues?.get(1)?.trim() ?: ""

    /** 人设里每天重复的日程: 所有带时间的段, 没有重复. 只认 "H:MM-H:MM:名字" 这样的行.
     *  kind 是空闲 / 休息 / 专注 / 睡眠之一, 别的名字是 ""; start / end 是一天中的第几分钟.
     *
     *  另外支持循环段 "[循环 HH:MM-HH:MM][Nmin]" ([Nmin] 可省, 默认 100min; 英文 Loop / Cycle / Repeat 都认):
     *  按 N 分钟专注 + F(N) 分钟休息交替生成, 生成到这段结束为止. 这段里已经有显式写出的段时, 以显式为准, 不生成. */
    fun scheduleEntries(text: String): List<SegEntry> {
        val out = ArrayList<SegEntry>()
        val seen = HashSet<Pair<String, Int>>()
        for (m in SEG_RE.findAll(text)) {                         // 显式写出的段优先
            val h = m.groupValues[1].toInt()
            val mi = m.groupValues[2].toInt()
            val eh = m.groupValues[3].toInt()
            val em = m.groupValues[4].toInt()
            val label = m.groupValues[5].trim()
            val kind = segKind(label)
            if (h > 23 || mi > 59 || eh > 24 || em > 59 || (eh == 24 && em != 0)) continue
            val start = h * 60 + mi
            var end = eh * 60 + em
            if (end <= start) end += 24 * 60                        // 跨零点 (23:30-6:30)
            if (!seen.add(kind to start)) continue
            out.add(SegEntry(kind, start, end, label, segDetail(label)))
        }
        val explicitStarts = out.map { it.start }
        for (m in CYCLE_RE.findAll(text)) {                        // 循环段: 显式没写这部分时自动生成
            val h = m.groupValues[1].toInt()
            val mi = m.groupValues[2].toInt()
            val eh = m.groupValues[3].toInt()
            val em = m.groupValues[4].toInt()
            if (h > 23 || mi > 59 || eh > 24 || em > 59 || (eh == 24 && em != 0)) continue
            val start = h * 60 + mi
            val rawEnd = eh * 60 + em
            if (rawEnd == start) continue
            val end = if (rawEnd > start) rawEnd else rawEnd + 24 * 60
            if (explicitStarts.any { it in start until end || it + 24 * 60 in start until end })
                continue                                            // 这段里已经有显式表示: 显示表示优先
            val n = m.groupValues[5].toIntOrNull()?.takeIf { it > 0 } ?: 100
            val rest = maxOf(1, Math.round(computeF(n.toDouble())).toInt())
            var t = start
            while (t < end) {
                val fEnd = minOf(t + n, end)
                if (fEnd > t && seen.add("focus" to t)) out.add(SegEntry("focus", t, fEnd, "专注", ""))
                t = fEnd
                if (t >= end) break
                val rEnd = minOf(t + rest, end)
                if (rEnd > t && seen.add("rest" to t)) out.add(SegEntry("rest", t, rEnd, "休息", ""))
                t = rEnd
            }
        }
        out.sortBy { it.start }                                     // 按时间排: 显式段和自动生成的循环段混在一起
        return out
    }

    /** 人设里每天重复的日程: 所有空闲 / 休息 / 专注 / 睡眠段 → (kind, 开始时刻 (一天中的第几分钟)), 没有重复. */
    fun scheduleSegments(text: String): List<Pair<String, Int>> =
        scheduleEntries(text).filter { it.kind.isNotEmpty() }.map { it.kind to it.start }
}

class Comm(
    val dir: File,
    var personas: File,
    private val now: () -> LocalDateTime = LocalDateTime::now,
    private val chatFn: (Profile, String, List<Pair<String, String>>) -> String =
        { p, s, m -> Ai.chatMessages(p, s, m, Comm.CHAT_TOKENS, Comm.CHAT_TEMPERATURE, Comm.CHAT_TIMEOUT_MS, cache = true,
            onUsage = { i, c -> Comm.usageSink(i, c) }) },
) {
    companion object {
        /** 每次请求的用量记录 (输入 token, 命中缓存的 token): 写进 comm.log. 由 Comm 的实例接上. */
        @Volatile var usageSink: (Long, Long) -> Unit = { _, _ -> }
        /** 带的聊天记录的起点一次往后挪这么多条 (不是每条都挪): 请求的开头 (前面的历史) 才能在这么多条消息里保持不变, 服务商的提示缓存才命中. */
        const val HISTORY_STEP = 10
        /** 非正在聊天的对象主动发消息: 两次请求之间至少隔这么多秒 (一个一个来, 不会同时好几个人找你). */
        const val BG_STAGGER = 120
        /** 每天在"空闲"状态下最多能打开沟通界面这么久 (秒; 休息时不限); 超过后空闲时不能用沟通, 休息了才能用. */
        const val IDLE_DAILY_SECONDS = 30 * 60
        /** 一次 tick 最多记这么多秒 (手机休眠 / 卡住时不会一下子记很多). */
        const val IDLE_TICK_MAX = 5.0
        const val PROACTIVE_MIN_REST = 20 * 60          // I. 用户休息超过这么久才会主动发消息
        const val PROACTIVE_MAX_DELAY = 180             // I / II / III: 休息开始后 / 日程段开始前 1~3 分钟 (秒)
        const val PROACTIVE_LATE = 120                  // 到点之后过了这么久 (程序刚打开 / 刚空下来) 就算了
        const val PROACTIVE_GAP = 10 * 60               // 两条主动消息至少隔这么久 (相邻的日程段不连发)
        const val PROACTIVE_KEEP = 12                   // 记下最近多少个已经处理过的主动消息时间点
        const val PING_QUIET = 5 * 60                   // 收到主动消息之前这么久没和对方聊过, 才响提示音
        /** 规则 VIII: 回复了这些请求 (用户说的话) 之后, 再调用一次 API, 让对方可以把话分开说. */
        val FOLLOW_KINDS = setOf("user", "retry")
        const val FOLLOW_TAG = "[系统·续]"
        val FOLLOW_BODY = "$FOLLOW_TAG 你刚才已经回复了用户. 如果还有想接着说的话, 请现在再发一条 (不要重复上一条); " +
            "如果你只想说一次, 返回${CommText.REFUSE}."
        const val DEFAULT_P_IDLE = 80                   // II / III 发不发的概率 (%) 的默认值; 设置 → 沟通 里可以改
        const val DEFAULT_P_REST = 60
        const val DEFAULT_P_LONG = 70                   // I (长时间休息) 的默认概率
        fun defaultP(kind: String): Double = when (kind) { "idle" -> DEFAULT_P_IDLE; "long" -> DEFAULT_P_LONG; else -> DEFAULT_P_REST }.toDouble()
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
        /** 聊天页状态前面小圆点的颜色: 专注黄 / 睡眠深蓝 / 休息浅绿 / 空闲灰. */
        val STATUS_COLORS = mapOf("rest" to "#9CCC65", "sleep" to "#283593", "focus" to "#FDD835", "idle" to "#9E9E9E")
    }

    private val cfgFile = File(dir, "comm_config.json")
    private val legacyChatFile = File(dir, "comm_chat.json")           // 以前所有对象共用的一份
    var persona = ""; private set
    var showAvatar = false; private set
    var avatarMe = ""; private set
    var avatarAi = ""; private set
    private var proKey = ""
    private var segSig: String? = null                      // 人设文件的 (名字, 修改时间, 大小): 变了才重新读日程
    private var segCache: List<Pair<String, Int>> = emptyList()          // 主动发消息用
    private var entryCache: List<SegEntry> = emptyList()                 // 完整日程 (带结束 / 修饰): 显示对方的状态用
    /** 分条发 (规则 VIII) 开着吗: 用户说完话, 对方的第一条收到之后再调用一次 API, 让对方可以把话分开说. 默认开; 随设备同步. */
    var splitSend = true; private set
    /** 计时器现在的状态 (idle / break / focus / sleep / out); Core 接上, 用来记空闲时间里开着沟通界面多久. */
    var stateFn: () -> String = { "" }
    private var idleLast: LocalDateTime? = null
    private var idleDay = ""
    private var idleSec = 0.0
    /** 允许非正在聊天的人 (聊过天的其他对象) 发起对话吗 (默认开, 随设备同步); 被"禁止主动发起"的对象的编号在 muted 里 (随设备同步). */
    var bgPro = true; private set
    private val muted = LinkedHashSet<String>()
    private class BgCtx(val pid: String, val name: String, val st: ChatState, val segs: List<Pair<String, Int>>, val probs: Map<String, Double>)
    private class BgResult(val pid: String, val kind: String, val extra: Extra, val reply: String, val err: String, val clear0: Long)
    private val bgResults = ConcurrentLinkedQueue<BgResult>()
    @Volatile private var bgBusy: String? = null
    private var bgLast = 0L
    private val bgSeg = HashMap<String, Triple<String, List<Pair<String, Int>>, Map<String, Double>>>()
    init { usageSink = { i, c -> log("USAGE input=$i cached=$c") } }
    private var proCache: Map<String, Double> = emptyMap()       // 同一个人设文件里写的主动发起谈话的概率 (CommText.personaProbs)
    private var seeded = false
    private var assistantSeeded = false
    private var legacyDone = false
    /** 现在聊的对象的编号 (人设文件名的 crc; 没有对象 = ""). */
    @Volatile var pid = ""; private set
    /** 用户在 设置 → 沟通 里自己填的身份背景: 附在人设后面一起发给对方. */
    var userProfile = ""; private set
    /** 收到对方主动发来的消息, 并且前 5 分钟没和对方聊过: 放柔和的提示音. Core 接上. */
    var onProactive: () -> Unit = {}
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
    /** 对方现在不能主动发消息的原因 ("" = 可以): Core 接上; 睡眠中仍可发, 只是不通知. */
    var aiBlocked: () -> String = { blocked() }
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
        if (attachTouched || CommData.label(attach).isNotEmpty()) attach else CommSel(today = CommData.DEFAULT_TODAY)
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
        if (!assistantSeeded) {                           // 老版本升级上来: 补一次 Assistant (已有同名文件的不动)
            runCatching {
                val f = File(personas, BuiltinPersonas.ASSISTANT)
                if (!f.exists()) f.writeText(BuiltinPersonas.ALL.getValue(BuiltinPersonas.ASSISTANT), Charsets.UTF_8)
            }
            assistantSeeded = true
            saveCfg()
        }
        migrateAvatars()
        val last = messages.lastOrNull()
        if (last != null && last.role == "me" && ageSeconds(last) < 6 * 3600 && retryAt == 0L)
            retryAt = nowMs() - RETRY_GAP * 1000L + 60_000        // 上次关程序时还没收到回复: 1 分钟后让 (最后操作的) 设备再试一次
        reloadSchedule("启动")
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
            st.messages.add(ChatMsg(id, role, m.optString("text"), at, m.optString("api"), m.optString("uid").ifEmpty { "L$id" }, ts)
                .also { it.hold = m.optBoolean("hold"); it.wait = m.optDouble("wait", 0.0); it.chased = m.optBoolean("chased"); it.shown = m.optLong("shown") })
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
            .put("uid", m.uid).put("ts", m.ts).apply {
                if (m.api.isNotEmpty()) put("api", m.api); if (m.hold) put("hold", true)
                if (m.wait > 0) put("wait", m.wait); if (m.chased) put("chased", true); if (m.shown != 0L) put("shown", m.shown)
            })
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
            splitSend = d.optBoolean("split_send", true)
            d.optJSONObject("idle_use")?.let { idleDay = it.optString("d"); idleSec = it.optDouble("s", 0.0) }
            bgPro = d.optBoolean("bg_pro", true)
            d.optJSONArray("mute_pro")?.let { a -> for (i in 0 until a.length()) a.optString(i).takeIf { it.isNotEmpty() }?.let { muted.add(it) } }
            seeded = d.optBoolean("seeded", false)
            assistantSeeded = d.optBoolean("assistant_seeded", false)
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
        reloadSchedule("换人设")
    }

    // 写盘 (fsync) 放在后台线程: 发送 / 收到消息时界面线程不等磁盘, 不卡. 先在这里做好快照, 线程只管写.

    /** 等后台的写盘做完 (自检). */
    fun flush() { writer.submit {}.get() }

    private fun saveCfg() {
        val o = JSONObject().put("persona", persona)
            .put("show_avatar", showAvatar).put("avatar_me", avatarMe).put("avatar_ai", avatarAi)
            .put("user_profile", userProfile).put("seeded", seeded).put("assistant_seeded", assistantSeeded)
            .put("legacy_done", legacyDone).put("split_send", splitSend)
            .put("bg_pro", bgPro).put("mute_pro", JSONArray(muted.toList()))
            .put("idle_use", JSONObject().put("d", idleDay).put("s", idleSec))
        writer.execute { Store.write(cfgFile, o) }
    }

    private fun saveChat() {
        if (messages.size > KEEP) repeat(-Math.floorDiv(-(messages.size - KEEP), HISTORY_STEP) * HISTORY_STEP) { messages.removeAt(0) }   // 一次砍 HISTORY_STEP 的整数倍: 发给模型的历史的起点不会跟着滑
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
        doc["split"] = splitSend                                    // 分条发的开关: 调用 AI 的是主端, 两台设备要一致
        doc["bgpro"] = bgPro
        doc["avatar"] = showAvatar                                  // 显示头像: 设置页的开关也同步
        if (idleDay.isNotEmpty()) doc["idle"] = JSONObject().put("d", idleDay).put("s", idleSec)      // 两台设备共用一份额度 (各自累计的取大的)
        for (p in (chatPids() + muted).toSet()) doc["mute:$p"] = p in muted
        for (p in chatPids()) {
            val st = stateOf(p)
            for (m in st.messages.takeLast(SYNC_N)) {
                doc["m:$p:${m.uid}"] = JSONObject().put("role", m.role).put("text", m.text).put("at", m.at).put("ts", m.ts)
                    .apply { if (m.api.isNotEmpty()) put("api", m.api); if (m.wait > 0) put("wait", m.wait) }
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
        val fresh = ArrayList<ChatMsg>()                                        // 这次新并进来的 (现在聊的对象的) 消息
        fun touch(p: String): Touch = touched.getOrPut(p) { stateOf(p).let { Touch(it, false, it.messages.map { m -> m.uid }.toHashSet()) } }
        for (key in changes.keys.sortedBy { !it.startsWith("clear:") }) {       // 清空先处理: 之前的记录不会再并进来
            val v = changes[key]
            val kind = key.substringBefore(':')
            val rest = key.substringAfter(':', "")
            when {
                key == "split" && v is Boolean -> if (v != splitSend) { splitSend = v; saveCfg(); changed() }
                key == "idle" && v is JSONObject -> {
                    val d = v.optString("d")
                    val s = v.optDouble("s", 0.0)
                    if (d == now().toLocalDate().toString() && (idleDay != d || s > idleSec)) { idleDay = d; idleSec = s; saveCfg(); changed() }
                }
                key == "bgpro" && v is Boolean -> if (v != bgPro) { bgPro = v; saveCfg(); changed() }
                key == "avatar" && v is Boolean -> if (v != showAvatar) { showAvatar = v; saveCfg(); changed() }
                kind == "mute" && v is Boolean -> if (v != (rest in muted)) { if (v) muted.add(rest) else muted.remove(rest); saveCfg(); changed() }
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
                    val msg = ChatMsg(nextId++, role, v.optString("text"), v.optString("at"), v.optString("api"), uid, ts)
                    v.optDouble("wait", 0.0).let { if (it > 0 && it <= CommText.WAIT_MAX_MIN) msg.wait = it }
                    if (role == "ai" && blocked().isNotEmpty()) msg.hold = true       // 不能用沟通的时候同步来的也先藏起来
                    tc.st.messages.add(msg)
                    if (p == pid) fresh.add(msg)
                    tc.known.add(uid)
                    tc.dirty = true
                    if (role == "ai") {
                        tc.st.retryAt = 0; tc.st.retryN = 0
                        if (!(viewing && p == pid) && !msg.hold) unread++
                    }
                }
                kind == "pro" && v is String -> {                       // 已经处理过的主动消息时间点: 两边合起来
                    val tc = touch(rest)
                    val have = tc.st.proKey.split('|').filter { it.isNotEmpty() }
                    val add = v.split('|').filter { it.isNotEmpty() && it !in have }
                    if (add.isNotEmpty()) { tc.st.proKey = (have + add).takeLast(PROACTIVE_KEEP).joinToString("|"); tc.dirty = true }
                }
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
        if (clearedActive) { unread = 0; error = ""; epoch++; reloadSchedule("新对话") }
        if (touched.isNotEmpty()) changed()
        syncedPing(fresh)
    }

    /** 别的设备收到的主动消息同步过来了 (系统触发的那条 + 对方的回复, 刚刚才写的): 这台也提示一声 (前 5 分钟没聊过的话). */
    private fun syncedPing(fresh: List<ChatMsg>) {
        val uids = fresh.map { it.uid }.toHashSet()
        val t = nowMs()
        for (m in fresh) {
            if (m.role != "ai" || t - m.ts > 90_000) continue
            val i = messages.indexOfFirst { it.uid == m.uid }
            val prev = if (i > 0) messages[i - 1] else null
            if (prev != null && prev.role == "sys" && prev.text.startsWith("[系统]") && prev.uid in uids) {
                if (!talkedRecently(messages.subList(0, i - 1), prev.ts)) ping()
                return
            }
        }
    }

    /** beforeMs 之前 5 分钟内, 用户和对方有没有说过话 (系统消息不算). */
    private fun talkedRecently(msgs: List<ChatMsg>, beforeMs: Long): Boolean =
        msgs.any { it.role != "sys" && beforeMs - it.ts in 0..PING_QUIET * 1000L }

    /** 收到主动消息的提示音 (不能用沟通的时候, 例如已经开始专注了, 不响). */
    private fun ping() {
        if (blocked().isEmpty()) runCatching { onProactive() }
    }

    /** 主动发消息的概率 (%): kind = "idle" 人设的空闲段前 / "rest" 人设的休息段前. 写在人设里, 没写用默认. */
    fun proPercent(kind: String): Double {
        segments()
        return proCache[kind] ?: defaultP(kind)
    }

    @Synchronized
    fun set(persona: String? = null, showAvatar: Boolean? = null,
            avatarMe: String? = null, avatarAi: String? = null, userProfile: String? = null, splitSend: Boolean? = null, bgPro: Boolean? = null) {
        persona?.let { switchTo(it); saveChat() }
        userProfile?.let { this.userProfile = it.trim().take(MAX_PROFILE) }
        showAvatar?.let { this.showAvatar = it }
        splitSend?.let { this.splitSend = it }
        bgPro?.let { this.bgPro = it }
        avatarMe?.let { this.avatarMe = it.trim().take(4) }
        avatarAi?.let { this.avatarAi = it.trim().take(4) }
        saveCfg()
        changed()
        if (splitSend != null || bgPro != null || showAvatar != null) push()
    }

    /** 这个对象 (默认是现在聊的) 被"禁止主动发起"了吗: 不主动发消息, 也不追问 (规则 IX). */
    fun isMuted(p: String = pid): Boolean = p in muted

    @Synchronized
    fun setMuted(m: Boolean, p: String = pid) {
        if (p.isEmpty()) return
        if (m) muted.add(p) else muted.remove(p)
        saveCfg(); changed(); push()
    }

    /** 测试用: 没有非正在聊天的对象的请求在等回复. */
    fun bgIdle() = bgBusy == null

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

    /** 名字是 Assistant (不分大小写), 并且文件和自带 Assistant 的提示词一字不差.
     *  只有这种情况, 用户在专注期间也能用沟通 (24.2.3 的特例). */
    fun isBuiltinAssistantUnmodified(): Boolean {
        val name = personaName()
        if (name.isEmpty()) return false
        if (personaTitle(name).trim().lowercase() != "assistant") return false
        return readPersona(name) == BuiltinPersonas.ALL.getValue(BuiltinPersonas.ASSISTANT).trim()
    }

    // ---- 人设文件本身: 随设备同步 (每 2 分钟检查一次变化; README 不同步, 各设备按界面语言生成) ----
    /** 人设文件夹里的 .txt / .md (文件名 → 全文), 给设备同步用. */
    fun personaDoc(): Map<String, String> {
        val out = LinkedHashMap<String, String>()
        for (name in listPersonas()) {
            runCatching { File(personas, name).readText(Charsets.UTF_8).removePrefix("\uFEFF").take(MAX_PERSONA) }
                .onSuccess { out[name] = it }
        }
        return out
    }

    /** 别的设备同步来的人设文件: 新增 / 覆盖 / 删除 (null). 现在选着的人设受影响时重新读日程. */
    fun applyPersonaDoc(changes: Map<String, Any?>) {
        var dirty = false
        for ((name, value) in changes) {
            if (name.isEmpty() || name != File(name).name) continue
            val ext = if (name.contains('.')) "." + name.substringAfterLast('.').lowercase() else ""
            if (ext !in PERSONA_EXT || name.lowercase() == "readme.md") continue
            val f = File(personas, name)
            runCatching {
                if (value == null) {
                    if (f.exists()) { f.delete(); dirty = true }
                } else {
                    val new = value.toString().take(MAX_PERSONA)
                    val old = if (f.isFile) f.readText(Charsets.UTF_8).removePrefix("\uFEFF") else null
                    if (old != new) {
                        f.parentFile?.mkdirs()
                        f.writeText(new, Charsets.UTF_8)
                        dirty = true
                    }
                }
            }
        }
        if (dirty) {
            segSig = null
            reloadSchedule("同步人设")
            changed()
        }
    }

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
    private var aiProblemAt = 0L
    private var aiProblemCache = ""

    /** 对方现在能不能主动发消息 / 重试 ("" = 可以). 和 problem 的区别: 用户睡眠时对方仍可以发, 只是消息先藏起来、不通知. */
    private fun aiProblem(): String {
        aiBlocked().let { if (it.isNotEmpty()) return it }
        val t = System.currentTimeMillis()
        if (t - aiProblemAt > 5000) {
            aiProblemAt = t
            aiProblemCache = try {
                Ai.checkProfile(aiConfig().profile()); ""
            } catch (e: AiError) {
                e.message ?: "API"
            }
        }
        if (aiProblemCache.isNotEmpty()) return aiProblemCache
        if (personaName().isEmpty()) return "还没有选人设"
        return ""
    }

    /** 对方现在在日程里的哪一段 (显示在聊天页顶上): (条目, 结束时刻); 没有日程 / 不在任何一段 = null. */
    fun otherStatus(): Pair<SegEntry, LocalDateTime>? {
        val entries = entries()
        if (entries.isEmpty()) return null
        val t = now()
        val mins = t.hour * 60 + t.minute
        var best: Triple<Int, Int, SegEntry>? = null              // (开始, 结束, 条目): 重叠时取最晚开始的那段
        for (e in entries) for (shift in listOf(0, -24 * 60)) {  // 今天 / 昨天开始的跨零点段
            val s = e.start + shift
            val en = e.end + shift
            if (s <= mins && mins < en && (best == null || s > best.first)) best = Triple(s, en, e)
        }
        val hit = best ?: return null
        val base = t.toLocalDate().atStartOfDay()
        return hit.third to base.plusMinutes(hit.second.toLong())
    }

    /** 聊天页顶上那一行: (状态文字, kind). 只显示 专注中 / 睡眠中 / 休息中 / 空闲 四种状态;
     *  别的名字 / 不在任何一段里 / 没有日程, 一律按空闲显示; 没有选人设 = ("" to ""). */
    fun otherStatusDisplay(): Pair<String, String> {
        if (personaName().isEmpty()) return "" to ""
        val raw = otherStatus()?.first?.kind ?: ""
        val kind = when (raw) { "focus", "sleep", "rest", "idle" -> raw; else -> "idle" }
        return tr(when (kind) { "focus" -> "专注中"; "sleep" -> "睡眠中"; "rest" -> "休息中"; else -> "空闲" }) to kind
    }

    /** 聊天页顶上那一行 (没有前缀 / 修饰 / 结束时间, 前面加什么颜色的小圆点看 kind). */
    fun otherStatusText(): String = otherStatusDisplay().first

    // ---- 头像: 圆形 PNG (ui/AvatarCrop.kt 里选图并截取); 对方的头像跟着人设走 ----

    private fun avatarName(who: String, persona: String): String {
        val crc = java.util.zip.CRC32().apply { update(personaTitle(persona).toByteArray(Charsets.UTF_8)) }
        return "${who}_%08x.png".format(crc.value)
    }

    /** 我的头像和对方的头像都跟着人设走 (每个聊天对象各自设置, 换人设就换头像). */
    fun avatarFile(who: String): File = File(File(dir, "avatars"), avatarName(who, personaName()))

    /** 以前"我"的头像只有一个 (me.png): 复制给现有的每个人设各一份, 然后删掉它 (只做一次, 之后各自设置). */
    private fun migrateAvatars() {
        val old = File(File(dir, "avatars"), "me.png")
        if (!old.isFile) return
        runCatching {
            for (name in (listPersonas() + personaName()).distinct()) {                  // 现在选着的 (可能是没选人设) 也算
                val dst = File(File(dir, "avatars"), avatarName("me", name))
                if (!dst.exists()) old.copyTo(dst)
            }
            old.delete()
        }
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

    /** 聊天里显示的消息 (藏起来的不算, 见 ChatMsg.hold). */
    fun visible() = messages.filter { (it.role == "me" || it.role == "ai") && !it.hold }

    private fun unanswered() = messages.lastOrNull { it.role == "me" || it.role == "ai" }?.role == "me"      // 藏着的回复也算回复过了

    /** 回到休息 / 空闲: 藏着的消息现在显示 (未读 +N, 前 5 分钟没聊过的话响一声). */
    private fun releaseHeld() {
        if (blocked().isNotEmpty()) return
        val held = messages.filter { it.hold }
        if (held.isEmpty()) return
        val first = messages.indexOf(held[0])
        held.forEach { it.hold = false; it.shown = nowMs() }         // 等用户回复的时间从这时候算起 (规则 IX)
        if (!viewing) unread += held.size
        if (!talkedRecently(messages.subList(0, first), held[0].ts)) ping()
        saveChat(); changed(); push()
    }

    // ---- 每天空闲时间里能用沟通的额度 (IDLE_DAILY_SECONDS): 空闲状态下开着沟通界面的时间累计, 用完了空闲时不能用, 休息时不限 ----
    private fun idleUsed(): Double = if (idleDay == now().toLocalDate().toString()) idleSec else 0.0

    /** 今天空闲时间里还能用沟通多少秒. */
    fun idleLeft(): Double = maxOf(0.0, IDLE_DAILY_SECONDS - idleUsed())

    fun idleOver(): Boolean = idleLeft() <= 0

    private fun countIdle() {
        val t = now()
        val last = idleLast
        idleLast = t
        if (last == null || !viewing || stateFn() != "idle") return
        val dt = minOf(secondsBetween(last, t).toDouble(), IDLE_TICK_MAX)
        if (dt <= 0) return
        val before = idleUsed()
        idleDay = t.toLocalDate().toString()
        idleSec = before + dt
        val crossed30 = before.toInt() / 30 != (before + dt).toInt() / 30        // 每 30 秒存一次, 每分钟同步一次
        if (crossed30) saveCfg()
        if (crossed30 && before.toInt() / 60 != (before + dt).toInt() / 60) push()
        if (before < IDLE_DAILY_SECONDS && IDLE_DAILY_SECONDS <= before + dt) { saveCfg(); push(); changed() }      // 刚用完: 界面立刻显示不能用
    }

    @Synchronized
    fun setViewing(on: Boolean) {
        if (viewing && !on) saveCfg()
        viewing = on
        if (on && unread != 0) { unread = 0; changed() }
    }

    // ---- 请求内容 ----

    fun apiMessages(src: List<ChatMsg> = messages): List<Pair<String, String>> {
        val out = ArrayList<Pair<String, String>>()
        // 总字数太多时从最老的开始不带 (至少带最新的一条)
        var total = 0
        // 起点按 HISTORY_STEP 一格一格挪: 中间的每一次请求, 开头都和上一次一模一样 (提示缓存才命中)
        var kept: List<ChatMsg> = if (src.size > HISTORY_SENT) src.drop(-Math.floorDiv(-(src.size - HISTORY_SENT), HISTORY_STEP) * HISTORY_STEP) else src.toList()
        while (kept.size > 1 && kept.sumOf { it.api.ifEmpty { it.text }.length } > HISTORY_CHARS) kept = kept.drop(minOf(HISTORY_STEP, kept.size - 1))
        for (m in kept) {
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

    private fun add(role: String, text: String, api: String = ""): ChatMsg = mk(role, text, api).also { messages.add(it) }

    /** 造一条消息 (还没放进任何聊天记录). */
    private fun mk(role: String, text: String, api: String = ""): ChatMsg {
        val ts = maxOf(nowMs(), lastTs + 1)             // 同一台设备上严格递增
        lastTs = ts
        val m = ChatMsg(nextId++, role, text, now().iso(), api, "$ts-${device().ifEmpty { "local" }.take(4)}", ts)
        if (role == "ai" && blocked().isNotEmpty()) m.hold = true       // 专注 / 睡眠 / 外出中收到的: 先藏起来, 回到休息 / 空闲再显示
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
        var system = CommText.prefixPrompt(splitSend) + readPersona(personaName())
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
                    convo = convo + ("assistant" to reply) + ("user" to ("(OIT 系统提供的参考模板, 用户看不到)\n\n" + blocks.joinToString("\n\n") +
                        "\n\n以上只是**参考模板** (格式; <尖括号> 里是占位符), 不是用户的真实数据. 请参照这个格式和写法, 根据人设自己填写具体内容," +
                        " 回复用户上面的消息, 不要把它说成是用户的数据. 如果此刻仍然不方便回复, 返回${CommText.REFUSE}."))
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
        val queried = r.err.isEmpty() && CommText.infoTags(r.reply).isNotEmpty()      // 对方在查询模块资料 (有没有 [refuse to reply] 都算): 这条一定不显示
        val (text0, waitMin) = if (r.err.isEmpty()) CommText.cleanReply(r.reply) else "" to 0.0
        val text = if (queried) "" else text0
        val refused = r.err.isEmpty() && CommText.isRefusal(r.reply)
        val empty = r.err.isEmpty() && !refused && text.isEmpty()            // 去掉 [] 之后什么也没剩: 当没回复
        // 第二次调用 (规则 VIII) 等回复的时候用户又说话了: 这条不要了 (用户的新消息重新回复)
        val drop = r.kind == "followup" && r.err.isEmpty() && !refused && (again || unanswered())
        if (drop) log("REPLY followup dropped: 等回复的时候用户又发了消息")
        if (refused) log("REPLY refused (${r.kind}): 对方回了 [refuse to reply], 不显示" + if (unanswered()) ", 稍后重试" else "")
        if (r.err.isNotEmpty() || refused || drop || empty) {
            error = r.err
            if (refused && unanswered()) {
                retryN++
                retryAt = if (retryN <= RETRY_MAX) nowMs() else 0L
                saveCfg()
            }
        } else {
            error = ""; retryAt = 0; retryN = 0
            saveCfg()
            val quiet = r.kind == "chase" || (r.kind == "proactive" && !talkedRecently(messages, nowMs()))      // 前 5 分钟没聊过 (追问总是): 提示音
            r.extra?.let { add("sys", it.body, it.api) }
            val m = add("ai", text, if (text != r.reply.trim()) r.reply.trim() else "")
            if (waitMin > 0 && r.kind != "chase") m.wait = waitMin          // 追问的回复里再写 [waiting for reply] 不算: 一条消息最多追问一次
            val held = m.hold
            if (!viewing && !held) unread++
            if (quiet && !held) ping()
        }
        saveChat(); changed(); push()
        if (again) {
            again = false
            if (unanswered() && r.err.isEmpty()) request("user")
        } else if (splitSend && r.kind in FOLLOW_KINDS && r.err.isEmpty() && !refused && aiProblem().isEmpty() && leader()) {
            // 规则 VIII: 对方的第一条已经收到了, 再调用一次, 让它可以接着说 (只想说一次就 [refuse to reply], 什么也不留下)
            trigger("followup", FOLLOW_BODY)
        }
    }

    /** 大约每秒调用一次 (Core.tick). */
    @Synchronized
    fun tick() {
        countIdle()
        while (true) finish(results.poll() ?: break)
        while (true) bgFinish(bgResults.poll() ?: break)
        releaseHeld()
        if (busy || aiProblem().isNotEmpty() || !leader()) return
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
        if (checkWait()) return
        checkProactive(t, snap)
        bgCheck(t, snap)
    }

    /** 规则 IX: 对方最后一条消息末尾写了 [waiting for reply]:[N], N 分钟 (从用户看到它算起) 过去了用户还没回复: 让对方可以追问一次. */
    private fun checkWait(ctx: BgCtx? = null): Boolean {
        if (ctx == null && isMuted()) return false
        val last = (ctx?.st?.messages ?: messages).lastOrNull { it.role == "me" || it.role == "ai" } ?: return false
        if (last.role != "ai" || last.wait <= 0 || last.chased || last.hold) return false
        if (nowMs() < (if (last.shown != 0L) last.shown else last.ts) + (last.wait * 60000).toLong()) return false
        last.chased = true                                           // 一条消息最多追问一次 (追问被拒绝也算)
        val n = "%.4g".format(last.wait).let { if ("." in it) it.trimEnd('0').trimEnd('.') else it }
        val body = "[系统] 你上一条消息末尾标了 [waiting for reply]:[$n], 现在已经过了 $n 分钟, 用户还没有回复. " +
            "请根据人设决定要不要追加询问一次 (话题和语气自己定, 不要重复上一条); 如果不想问或者此刻不方便, 返回${CommText.REFUSE}."
        if (ctx != null) { storeState(ctx.pid, ctx.st); bgRequest(ctx, "chase", body) }
        else { saveChat(); trigger("chase", body) }
        return true
    }

    // ---- 主动发消息 (MECHANICS 24.4) ----

    private class SegEvent(val key: String, val kind: String, val begin: LocalDateTime)

    /** 已经处理过的主动消息时间点 (发没发都算): 休息开始的时刻 (I) / "S开始时刻i|r" (日程里的空闲 / 休息段, II / III). */
    // 下面这些带 ctx 的: ctx = null 是现在聊的对象; 否则是非正在聊天的对象 (bgCheck)
    private fun proDone(ctx: BgCtx? = null): List<String> = (ctx?.st?.proKey ?: proKey).split('|').filter { it.isNotEmpty() }

    private fun proMark(key: String, ctx: BgCtx? = null) {
        val v = (proDone(ctx).filter { it != key } + key).takeLast(PROACTIVE_KEEP).joinToString("|")
        if (ctx != null) ctx.st.proKey = v else proKey = v
    }

    /** 最近 PROACTIVE_GAP 内已经主动发过一条了吗 (相邻的日程段 / 刚好又在休息开始后, 不连着发). */
    private fun proRecent(ctx: BgCtx? = null): Boolean {
        val m = (ctx?.st?.messages ?: messages).lastOrNull { it.role == "sys" && it.text.startsWith("[系统]") } ?: return false
        return ageSeconds(m) < PROACTIVE_GAP
    }

    /** 现在这个人设日程里的空闲 / 休息 / 专注 / 睡眠段 (人设文件没变就用上次读的). */
    private fun segments(): List<Pair<String, Int>> {
        val name = personaName()
        val f = File(personas, name)
        if (name.isEmpty() || !f.isFile) { proCache = emptyMap(); segCache = emptyList(); entryCache = emptyList(); return emptyList() }
        val sig = "$name|${f.lastModified()}|${f.length()}"
        if (sig != segSig) {
            segSig = sig
            val text = readPersona(name)
            entryCache = CommText.scheduleEntries(text)
            segCache = entryCache.filter { it.kind.isNotEmpty() }.map { it.kind to it.start }
            proCache = CommText.personaProbs(text)
        }
        return segCache
    }

    /** 现在这个人设的完整日程 (带结束 / 修饰): 显示对方的状态用. */
    private fun entries(): List<SegEntry> { segments(); return entryCache }

    /** 重新读人设里的日程, 主动发消息的时刻按它调整: 打开 OIT / 开始新对话 (清空) / 换人设时调用; 人设文件改了也会在 segments() 里自己重读. */
    fun reloadSchedule(why: String = "") {
        segSig = null
        val segs = segments()
        log("SCHEDULE $why: 空闲 ${segs.count { it.first == "idle" }} 段, 休息 ${segs.count { it.first == "rest" }} 段" +
            ", 专注 ${segs.count { it.first == "focus" }} 段, 睡眠 ${segs.count { it.first == "sleep" }} 段")
    }

    /** 日程里 3 分钟之内就要开始的空闲 / 休息段. */
    private fun scheduleEvents(t: LocalDateTime, segs: List<Pair<String, Int>>? = null): List<SegEvent> {
        val out = ArrayList<SegEvent>()
        for ((kind, mins) in segs ?: segments()) {
            if (kind != "idle" && kind != "rest") continue          // 专注 / 睡眠段只用来显示状态, 不主动发消息
            for (d in 0L..1L) {                                    // 今天 / 明天 (23:59 看 0:00 开始的那段)
                val begin = t.toLocalDate().plusDays(d).atStartOfDay().plusMinutes(mins.toLong())
                if (t.isBefore(begin) && !begin.isAfter(t.plusSeconds(PROACTIVE_MAX_DELAY.toLong())))
                    out.add(SegEvent("S${begin.toLocalDate()}T%02d:%02d%s".format(begin.hour, begin.minute, kind[0]), kind, begin))
            }
        }
        return out
    }

    private fun checkProactive(t: LocalDateTime, snap: CommSnap?, ctx: BgCtx? = null) {
        if (ctx == null && isMuted()) return
        val done = proDone(ctx)
        // I. 用户这次休息超过 20 分钟: 休息开始后 1~3 分钟发一条
        val bs = snap?.breakStart
        if (snap != null && snap.state == "break" && bs != null && snap.breakTotalS > PROACTIVE_MIN_REST) {
            val key = bs.iso()
            if (key !in done) {
                val due = bs.plusSeconds(60L + java.util.Random(key.hashCode().toLong()).nextInt(121))
                if (!t.isBefore(due)) {
                    proFire(key, due, t, (if (ctx != null) ctx.probs["long"] ?: defaultP("long") else proPercent("long")) / 100.0, "[系统] 用户刚开始休息, 这次计划休息约 ${Math.round(snap.breakTotalS / 60)} 分钟. " +
                        "请根据人设主动给用户发一条消息, 话题自己找; 如果此刻不方便, 返回${CommText.REFUSE}.", ctx)
                    return
                }
            }
        }
        // II / III. 人设日程里每段空闲 / 休息开始前 1~3 分钟, 有概率发一条
        for (ev in scheduleEvents(t, ctx?.segs)) {
            if (ev.key in done) continue
            val due = ev.begin.minusSeconds(60L + java.util.Random(ev.key.hashCode().toLong()).nextInt(121))
            if (t.isBefore(due)) continue
            val mins = maxOf(1L, Math.round(secondsBetween(t, ev.begin) / 60.0))
            val hm = "%02d:%02d".format(ev.begin.hour, ev.begin.minute)
            val pct = if (ctx != null) ctx.probs[ev.kind] ?: defaultP(ev.kind) else proPercent(ev.kind)
            proFire(ev.key, due, t, pct / 100.0,
                "[系统] 按你的日程, 你的${if (ev.kind == "idle") "空闲" else "休息"}时间将在 $hm 开始 (约 $mins 分钟后). " +
                "这条是系统提前触发的: 即使你此刻还处于专注/不能回复的时段, 也允许你现在主动给用户发一条消息, 话题自己找 (不要透露日程); " +
                "如果实在不方便, 返回${CommText.REFUSE}.", ctx)
            return
        }
    }

    /** 这个时间点处理一次: 记下来 (不管发没发), 没错过太久 / 摇中了概率 / 最近没发过, 才真的让对方发. */
    private fun proFire(key: String, due: LocalDateTime, t: LocalDateTime, p: Double, body: String, ctx: BgCtx? = null) {
        proMark(key, ctx)
        val late = secondsBetween(due, t) > PROACTIVE_LATE                  // 错过太久 (程序刚打开) 就算了
        val hit = p >= 1.0 || java.util.Random((key + "#p").hashCode().toLong()).nextDouble() < p
        val recent = proRecent(ctx)
        val who = if (ctx != null) " [${ctx.name}]" else ""
        if (!late && hit && !recent) {
            log("PRO$who $key p=${"%.2f".format(p)} → 让对方发")
            if (ctx != null) bgRequest(ctx, "proactive", body) else trigger("proactive", body)
        } else {
            log("PRO$who $key p=${"%.2f".format(p)} → 不发 (${if (late) "错过了" else if (!hit) "概率没中" else "刚发过"})")
        }
        if (ctx != null) storeState(ctx.pid, ctx.st) else { saveChat(); saveCfg() }
        push()
    }

    // ---- 非正在聊天的对象主动发消息 (MECHANICS 24.4): 聊过天的其他对象照样按它们各自的人设日程主动发 / 追问, 一次一个 ----

    /** 人设文件里的 (日程段, 主动发起谈话的概率), 文件没变就用上次读的. */
    private fun infoOf(name: String): Pair<List<Pair<String, Int>>, Map<String, Double>> {
        val f = File(personas, name)
        if (!f.isFile) return emptyList<Pair<String, Int>>() to emptyMap()
        val sig = "${f.lastModified()}|${f.length()}"
        val hit = bgSeg[name]?.takeIf { it.first == sig } ?: run {
            val text = readPersona(name)
            Triple(sig, CommText.scheduleSegments(text), CommText.personaProbs(text)).also { bgSeg[name] = it }
        }
        return hit.second to hit.third
    }

    private fun bgCheck(t: LocalDateTime, snap: CommSnap?) {
        if (!bgPro || bgBusy != null || nowMs() - bgLast < BG_STAGGER * 1000L) return
        val names = listPersonas().associateBy { pidOf(it) }
        for (p in chatPids()) {
            if (p == pid || p !in names || p in muted) continue
            val st = stateOf(p)
            if (st.messages.none { it.role == "me" || it.role == "ai" }) continue          // 没聊过的 (只是切换时留下的空记录) 不会来找你
            val (segs, probs) = infoOf(names.getValue(p))
            val ctx = BgCtx(p, names.getValue(p), st, segs, probs)
            if (checkWait(ctx)) return
            checkProactive(t, snap, ctx)
            if (bgBusy != null) return
        }
    }

    /** 为非正在聊天的对象发一次请求 (主动发 / 追问); 回复由 tick 收进它的聊天记录, 没有第二次调用, 也没有要资料的轮次. */
    private fun bgRequest(ctx: BgCtx, kind: String, body: String) {
        val profile = aiConfig().profile()
        var system = CommText.prefixPrompt(splitSend) + readPersona(ctx.name)
        if (userProfile.isNotBlank()) system += "\n\n以下是用户的身份背景(用户自己填写):\n" + userProfile.trim()
        val api = compose(now(), body)
        val msgs = ArrayList(apiMessages(ctx.st.messages))
        if (msgs.isNotEmpty() && msgs.last().first == "user") msgs[msgs.size - 1] = "user" to (msgs.last().second + "\n\n" + api)
        else msgs.add("user" to api)
        val clear0 = ctx.st.clearTs
        bgBusy = ctx.pid
        bgLast = nowMs()
        val started = System.currentTimeMillis()
        log("REQ bg-$kind start [${ctx.name}] model=${profile.model} msgs=${msgs.size}")
        Thread {
            var reply = ""
            var err = ""
            try { reply = chatFn(profile, system, msgs) }
            catch (e: AiError) { err = e.message ?: "" }
            catch (e: Exception) { err = e.javaClass.simpleName }
            log("REQ bg-$kind ${if (err.isNotEmpty()) "FAIL $err" else "ok"} ${(System.currentTimeMillis() - started) / 1000.0}s")
            bgResults.add(BgResult(ctx.pid, kind, Extra(body, api), reply, err, clear0))
        }.apply { isDaemon = true }.start()
    }

    private fun bgFinish(r: BgResult) {
        bgBusy = null
        if (hardBlock()) return
        val st = stateOf(r.pid)
        if (st.clearTs != r.clear0 || r.err.isNotEmpty()) return
        val (text, waitMin) = CommText.cleanReply(r.reply)
        if (CommText.isRefusal(r.reply) || text.isEmpty() || CommText.infoTags(r.reply).isNotEmpty()) { log("REPLY bg-${r.kind}: 对方不想发 / 什么也没剩, 不显示"); return }
        val sysm = mk("sys", r.extra.body, r.extra.api)
        val m = mk("ai", text, if (text != r.reply.trim()) r.reply.trim() else "")
        if (waitMin > 0 && r.kind != "chase") m.wait = waitMin
        st.messages.add(sysm); st.messages.add(m)
        val talked = talkedRecently(st.messages.dropLast(2), sysm.ts)
        if (!m.hold) {
            if (!(viewing && r.pid == pid)) unread++
            if (r.kind == "chase" || !talked) ping()
        }
        storeState(r.pid, st); changed(); push()
    }

    /** 清空聊天记录 (界面上的按钮, 或者在聊天框里发 /clear): 以后不再显示, 也不再发给模型; 别的设备也一起清空.
     */
    @Synchronized
    fun clear() {
        clearTs = maxOf(nowMs(), lastTs)
        messages.clear(); unread = 0; retryAt = 0; retryN = 0; error = ""
        epoch++
        saveCfg(); saveChat(); reloadSchedule("新对话"); changed(); push()
    }
}
