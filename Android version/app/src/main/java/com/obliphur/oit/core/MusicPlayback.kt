package com.obliphur.oit.core

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.media.MediaPlayer
import android.os.Handler
import android.os.Looper
import java.io.File

/** 播放动作只存在本机会话；准备成功也必须等承载气泡真正展示。 */
class MusicPlayback(private val ctx: Context) {
    private val main = Handler(Looper.getMainLooper())
    private var player: MediaPlayer? = null
    private var focus: AudioFocusRequest? = null
    private var generation = 0
    @Volatile var playing = false; private set
    @Volatile var activeId = ""; private set
    @Volatile var error = ""; private set
    fun stop() {
        playing = false; activeId = ""; val ticket = ++generation
        main.post { if (ticket == generation) release() }
    }
    private fun release() {
        player?.let { runCatching { it.stop() }; it.release() }; player = null
        focus?.let { (ctx.getSystemService(Context.AUDIO_SERVICE) as AudioManager).abandonAudioFocusRequest(it) }; focus = null
    }
    fun play(id: String, file: File, allowed: () -> Boolean) {
        val ticket = ++generation
        main.post {
            if (ticket != generation) return@post
            release(); playing = false; activeId = ""
            if (!allowed() || !file.isFile) return@post
            try {
                val manager = ctx.getSystemService(Context.AUDIO_SERVICE) as AudioManager
                val attributes = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build()
                val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN).setAudioAttributes(attributes)
                    .setOnAudioFocusChangeListener { if (it < 0) stop() }.build()
                if (manager.requestAudioFocus(request) != AudioManager.AUDIOFOCUS_REQUEST_GRANTED) return@post
                focus = request
                val mp = MediaPlayer(); player = mp
                mp.setAudioAttributes(attributes); mp.setDataSource(file.absolutePath)
                mp.setOnPreparedListener {
                    if (ticket != generation || !allowed()) { stop() }
                    else { mp.start(); activeId = id; playing = true }
                }
                mp.setOnCompletionListener { stop() }
                mp.setOnErrorListener { _, _, _ -> error = "演奏播放失败"; stop(); true }
                mp.prepareAsync()
            } catch (e: Exception) { error = e.message.orEmpty(); stop() }
        }
    }
}
