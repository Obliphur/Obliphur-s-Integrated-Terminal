package com.obliphur.oit.core

/** 两端一致的事件与内置音色；音效选择保存在本机设置里。 */
object SoundEffects {
    val events = linkedMapOf(
        "CHIME_WAV" to ("整点提示音" to "chime"),
        "DAMAGE_SOUND" to ("损失生命值" to "alarm"),
        "COMM_SOUND" to ("收到信息" to "soft"),
        "BREAK_SOUND" to ("休息即将结束" to "break"),
        "OFFLINE_SOUND" to ("设备离线" to "chime"),
        "PRESCRIPT_RECEIVE_SOUND" to ("领取指令" to "tick"),
        "PRESCRIPT_COMPLETE_SOUND" to ("完成指令" to "tick"),
    )
    val choices = listOf(
        "__builtin__" to "默认音效", "__silent__" to "静音",
        "__chime__" to "三音提示", "__soft__" to "柔和钟声",
        "__break__" to "低音钟声", "__alarm__" to "警报",
        "__tick__" to "随机滴答", "__bell__" to "清脆铃声",
        "__rise__" to "上行音阶", "__fall__" to "下行音阶",
        "__wood__" to "木琴轻敲", "__sparkle__" to "星光",
    )

    fun display(value: String): String = choices.firstOrNull {
        it.first == value.ifBlank { "__builtin__" }
    }?.let { tr(it.second) } ?: tr("自选音频")
}
