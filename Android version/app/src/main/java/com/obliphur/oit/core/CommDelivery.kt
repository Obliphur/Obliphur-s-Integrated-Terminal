package com.obliphur.oit.core

/** 完整气泡的流式边界；普通换行和代码围栏中的分隔符不分条。 */
internal class ReplyParts {
    companion object {
        private val messageBreak = Regex("<\\s*oit[_-]message\\s*>|&lt;\\s*oit[_-]message\\s*&gt;", RegexOption.IGNORE_CASE)
        fun split(text: String): List<String> = ReplyParts().feed(text, final = true)
    }

    private var tail = ""
    private val lines = ArrayList<String>()
    private var fence = ""

    fun feed(delta: String, final: Boolean = false): List<String> {
        tail += delta
        val incoming = tail.split('\n').toMutableList()
        tail = if (final) "" else incoming.removeAt(incoming.lastIndex)
        val out = ArrayList<String>()
        fun emit() {
            lines.joinToString("\n").trim().takeIf { it.isNotEmpty() }?.let { out.add(it) }
            lines.clear()
        }
        for (line in incoming) {
            val stripped = line.trim()
            val marker = Regex("^(`{3,}|~{3,})").find(stripped)?.value
            if (marker != null) {
                if (fence.isEmpty()) fence = marker
                else if (marker[0] == fence[0] && marker.length >= fence.length && stripped.drop(marker.length).isBlank()) fence = ""
            }
            val cleanLine = line.trimEnd('\r')
            if (fence.isNotEmpty()) {
                lines.add(cleanLine)
                continue
            }
            var start = 0
            for (match in messageBreak.findAll(cleanLine)) {
                lines.add(cleanLine.substring(start, match.range.first))
                emit()
                start = match.range.last + 1
            }
            lines.add(cleanLine.substring(start))
        }
        if (final) emit()
        return out
    }

}

/** 仅当前请求持有的分条状态，旧请求在插话或切换对象后不能再入队。 */
internal class ReplyBatch(val epoch: Int, val source: Long, val after: Long) {
    val parser = ReplyParts()
    val messages = ArrayList<ChatMsg>()
    var cancelled = false
    var finished = false
}
