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
