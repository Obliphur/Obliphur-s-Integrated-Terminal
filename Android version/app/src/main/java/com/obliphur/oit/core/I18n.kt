package com.obliphur.oit.core

/**
 * 界面文字以中文为键, 英文在 [EnStrings.MAP] 里 (同桌面版 i18n.py).
 * 占位符写成 {name}, 由 tr 的参数填入.
 */
object I18n {
    @Volatile
    var lang: String = "zh"
    val isEn get() = lang == "en"

    /** 都市之意主题不画图标 (同桌面版 config.pixel_safe): 界面文字里的 emoji / 符号图标一律去掉. */
    @Volatile
    var noIcons: Boolean = false

    /** 都市之意主题不显示风味文本 (作者写的那些带竖线的小字). */
    @Volatile
    var noFlavor: Boolean = false

    /** 整点记录的自动填写内容 (MECHANICS 8.2 / 23): 英文名和中文名是同一项, 按界面语言写, 切换语言时合并 (同 i18n.py). */
    val AUTO_NAMES = listOf("Sleeping" to "睡眠", "Distracted" to "分心", "Idle" to "发呆", "Break" to "休息")

    /** Sleeping / Distracted / Idle / Break 和它们的中文名: 照现在的界面语言写; 别的文字原样. */
    fun autoName(text: String?): String {
        val t = text ?: ""
        val k = t.trim().lowercase()
        for ((en, zh) in AUTO_NAMES) if (k == en.lowercase() || k == zh) return if (isEn) en else zh
        return t
    }

    /** 一小时的记录里 (多项活动用 / 连接) 每一项都按 autoName 换成现在的语言; 没有要换的原样返回. */
    fun autoNamesIn(text: String?): String {
        val t = text ?: ""
        if (t.isEmpty()) return t
        val sep = if ("／" in t && "/" !in t) "／" else "/"
        val parts = t.replace("／", "/").split("/")
        val out = parts.map { if (it.isNotBlank()) autoName(it) else it }
        return if (out == parts) t else out.joinToString(sep)
    }

    private val ICON = Regex("[\u21A9\u21AA\u21BB\u2300-\u23FF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3\\x{1F000}-\\x{1FAFF}]")

    /** 去掉图标字符 (图标旁边的空格一起去掉); 只有一个图标的按钮 (✕ / ☰) 换成普通字符, 免得按钮没有字. */
    fun ic(text: String): String {
        if (!noIcons || text.isEmpty()) return text
        when (text) { "✕" -> return "×"; "☰" -> return "≡" }
        if (!ICON.containsMatchIn(text)) return text
        // 图标换成一个占位符, 再把占位符两边的空格一起去掉
        return text.replace(ICON, "\u0001").replace(Regex("\u0001[ \t]*"), "")
            .replace(Regex("[ \t]*\u0001"), "").replace("\u0001", "").trimEnd()
    }
}

fun tr(zh: String, vararg args: Pair<String, Any?>): String {
    var text = if (I18n.isEn) EnStrings.MAP[zh] ?: zh else zh
    for ((k, v) in args) text = text.replace("{$k}", v.toString())
    return I18n.ic(text)
}
