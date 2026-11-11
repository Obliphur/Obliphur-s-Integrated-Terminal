package com.obliphur.oit.service

import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.net.Uri
import android.os.Handler
import android.os.Looper
import com.obliphur.oit.R
import com.obliphur.oit.core.SoundEffects

/** 事件与试听共用播放器；指令领取可以先准备音频，以其长度驱动动画。 */
object EffectPlayer {
    private val handler = Handler(Looper.getMainLooper())
    private val resources = mapOf(
        "chime" to R.raw.effect_chime, "soft" to R.raw.effect_soft,
        "break" to R.raw.effect_break, "alarm" to R.raw.target_alarm,
        "bell" to R.raw.effect_bell, "rise" to R.raw.effect_rise,
        "fall" to R.raw.effect_fall, "wood" to R.raw.effect_wood,
        "sparkle" to R.raw.effect_sparkle,
    )

    fun prepare(ctx: Context, key: String, value: String): MediaPlayer? {
        if (value == "__silent__") return null
        val default = SoundEffects.events.getValue(key).second
        val tone = if (value.isBlank() || value == "__builtin__") default else value.trim('_')
        val resource = if (tone == "tick") listOf(R.raw.ticktock_type1,
            R.raw.ticktock_type2, R.raw.ticktock_type3).random() else resources[tone]
        val player = MediaPlayer()
        try {
            player.setAudioAttributes(AudioAttributes.Builder()
                .setUsage(if (key == "DAMAGE_SOUND") AudioAttributes.USAGE_ALARM else AudioAttributes.USAGE_NOTIFICATION_EVENT)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
            if (resource != null) ctx.resources.openRawResourceFd(resource).use {
                player.setDataSource(it.fileDescriptor, it.startOffset, it.length)
            } else player.setDataSource(ctx, Uri.parse(value))
            player.prepare()
            player.setOnCompletionListener { it.release() }
            player.setOnErrorListener { p, _, _ -> p.release(); true }
            return player
        } catch (_: Exception) {
            player.release()
            return if (tone != default) prepare(ctx, key, "__builtin__") else null
        }
    }

    /** 填写提醒最多等十秒；静音或播放失败仍然调用完成回调。 */
    fun play(ctx: Context, key: String, value: String, done: () -> Unit) {
        val player = prepare(ctx, key, value)
        if (player == null) { done(); return }
        var finished = false
        val fin = { if (!finished) { finished = true; done() } }
        val timeout = Runnable { fin() }
        handler.postDelayed(timeout, 10_000)
        player.setOnCompletionListener { it.release(); handler.removeCallbacks(timeout); fin() }
        player.setOnErrorListener { p, _, _ -> p.release(); handler.removeCallbacks(timeout); fin(); true }
        try { player.start() } catch (_: Exception) {
            player.release(); handler.removeCallbacks(timeout); fin()
        }
    }
}
