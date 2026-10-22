package com.obliphur.oit.service

import android.app.Notification
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CameraMetadata
import android.media.MediaRecorder
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.util.DisplayMetrics
import android.view.WindowManager
import com.obliphur.oit.OitApp
import com.obliphur.oit.core.RecCrypto
import java.io.File
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

/**
 * 合约录像的前台服务 (MECHANICS 18.8): 真正用摄像头 (目光) 和录屏 (刻痕, MediaProjection) 录像的那一层.
 * 由 [RecordManager] 用 Intent 指挥 (开始 / 停止某一路); 录屏需要先进入 mediaProjection 类型的前台服务,
 * 再用 MainActivity 拿到的授权造投屏. 每段写完 (stop / Finalize) 后用 [RecCrypto] 加密成 .oitv 存进数据目录,
 * 原始 mp4 写在缓存里 (不进数据目录, 防止被网盘自动上传). 声音: 麦克风按设置录 (手机上录屏录不到系统音).
 */
class RecordService : Service() {
    private val thread = HandlerThread("oit-rec").apply { start() }
    private val h = Handler(thread.looper)
    private var cam: CamJob? = null
    private var screen: ScreenJob? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val action = intent?.action
        // startForegroundService 之后必须尽快进入前台: 类型按这次要录的几路算 (停止指令时按剩下的)
        val wantCam = cam != null || action == ACT_CAMERA
        val wantScreen = screen != null || action == ACT_SCREEN
        enterForeground(wantCam, wantScreen)
        h.post {
            when (action) {
                ACT_CAMERA -> if (cam == null) startCam()
                ACT_SCREEN -> if (screen == null) startScreen(intent)
                ACT_STOP_CAMERA -> { cam?.stop(); cam = null }
                ACT_STOP_SCREEN -> { screen?.stop(); screen = null }
            }
            if (cam == null && screen == null) {
                stopForeground(STOP_FOREGROUND_REMOVE); stopSelf()
            } else enterForeground(cam != null, screen != null)
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        h.post { cam?.stop(); screen?.stop(); cam = null; screen = null }
        h.postDelayed({ thread.quitSafely() }, 500)
        state.clear()
        super.onDestroy()
    }

    /** 前台服务类型随正在录的几路变 (Android 14 起必须声明 camera / microphone / mediaProjection). */
    private fun enterForeground(cam: Boolean, screen: Boolean) {
        val note = Notifier.recording(this, cam, screen)
        try {
            if (Build.VERSION.SDK_INT >= 29) {
                var type = 0
                if (cam) type = type or ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
                if (screen) type = type or ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION
                if ((cam || screen) && micOn()) type = type or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
                if (type == 0) type = ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA   // 兜底 (manifest 已声明)
                startForeground(Notifier.ID_RECORD, note, type)
            } else startForeground(Notifier.ID_RECORD, note)
        } catch (e: Exception) {
            fail("screen", e); fail("camera", e)
            stopForeground(STOP_FOREGROUND_REMOVE); stopSelf()
        }
    }

    // ---------------------------------------------------------------- 摄像头 (目光)

    /** 录麦克风: 设置开着, 而且真的有 RECORD_AUDIO 权限 (没权限就只录画面, 不让整段录像失败). */
    private fun micOn() = OitApp.instance.core.settings.bool("REC_MIC") &&
        checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) == android.content.pm.PackageManager.PERMISSION_GRANTED

    private fun newPart(kind: String): Pair<File, File> {
        val cache = RecCrypto.workDir(cacheDir, kind).apply { mkdirs() }
        val stem = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss"))
        val cid = RecCrypto.safeCid(recCid).let { if (recCid.isEmpty()) "" else "__$it" }
        var n = 0
        while (true) {
            val name = stem + (if (n > 0) "-$n" else "") + cid
            val part = File(cache, name + RecCrypto.PART + ".mp4")
            val fin = File(cache, name + ".mp4")
            if (!part.exists() && !fin.exists()) return part to fin
            n++
        }
    }

    private val recCid: String get() = OitApp.instance.core.contract.live.optString("id", "")
    private val recDataDir: File get() = File(OitApp.instance.filesDir, "data")

    private fun seal(kind: String, part: File, fin: File) {
        val src = if (part.renameTo(fin)) fin else part
        if (src.length() <= 0) { src.delete(); return }
        runCatching { RecCrypto.seal(recDataDir, kind, src) }
            .onFailure { OitApp.instance.core.logRecord("seal $kind failed: ${it.message}") }
            .onSuccess { OitApp.instance.core.logRecord("$kind saved ${it.name}") }
    }

    private fun startCam() {
        if (checkSelfPermission(android.Manifest.permission.CAMERA) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            fail("camera", Exception("no camera permission")); return
        }
        try {
            val mgr = getSystemService(CAMERA_SERVICE) as CameraManager
            val id = mgr.cameraIdList.firstOrNull {
                mgr.getCameraCharacteristics(it).get(CameraCharacteristics.LENS_FACING) == CameraMetadata.LENS_FACING_FRONT
            } ?: mgr.cameraIdList.firstOrNull() ?: run { fail("camera", Exception("no camera")); return }
            cam = CamJob(mgr, id).also { it.start() }
            state["camera"] = "running"
        } catch (e: Exception) { fail("camera", e) }
    }

    private inner class CamJob(val mgr: CameraManager, val id: String) {
        private val part: File; private val fin: File
        private var rec: MediaRecorder? = null
        private var device: CameraDevice? = null
        private var session: CameraCaptureSession? = null
        @Volatile private var closed = false
        init { val (p, f) = newPart("camera"); part = p; fin = f }

        fun start() {
            rec = buildRecorder(part, 480, 360, 5, 160_000)
            @Suppress("MissingPermission")
            mgr.openCamera(id, object : CameraDevice.StateCallback() {
                override fun onOpened(d: CameraDevice) {
                    device = d
                    val surface = rec!!.surface
                    @Suppress("DEPRECATION")
                    d.createCaptureSession(listOf(surface), object : CameraCaptureSession.StateCallback() {
                        override fun onConfigured(s: CameraCaptureSession) {
                            if (closed) return
                            session = s
                            val req = d.createCaptureRequest(CameraDevice.TEMPLATE_RECORD).apply { addTarget(surface) }
                            s.setRepeatingRequest(req.build(), null, h)
                            runCatching { rec!!.start() }.onFailure { fail("camera", it as Exception) }
                        }
                        override fun onConfigureFailed(s: CameraCaptureSession) = fail("camera", Exception("session"))
                    }, h)
                }
                override fun onDisconnected(d: CameraDevice) { fail("camera", Exception("disconnected")); stop() }
                override fun onError(d: CameraDevice, err: Int) { fail("camera", Exception("camera error $err")) }
            }, h)
        }

        fun stop() {
            closed = true
            runCatching { session?.stopRepeating() }
            runCatching { session?.close() }
            runCatching { rec?.stop() }
            runCatching { rec?.release() }
            runCatching { device?.close() }
            seal("camera", part, fin)
        }
    }

    // ---------------------------------------------------------------- 录屏 (刻痕)

    private fun startScreen(intent: Intent) {
        val code = intent.getIntExtra(EXTRA_CODE, 0)
        val data: Intent? = if (Build.VERSION.SDK_INT >= 33)
            intent.getParcelableExtra(EXTRA_DATA, Intent::class.java) else @Suppress("DEPRECATION") intent.getParcelableExtra(EXTRA_DATA)
        if (code == 0 || data == null) { fail("screen", Exception("no projection consent")); return }
        try {
            val mpm = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
            val proj = mpm.getMediaProjection(code, data) ?: run { fail("screen", Exception("projection")); return }
            screen = ScreenJob(proj).also { it.start() }
            state["screen"] = "running"
        } catch (e: Exception) { fail("screen", e) }
    }

    private inner class ScreenJob(val proj: MediaProjection) {
        private val part: File; private val fin: File
        private var rec: MediaRecorder? = null
        private var vd: android.hardware.display.VirtualDisplay? = null
        @Volatile private var closed = false
        private val cb = object : MediaProjection.Callback() {
            override fun onStop() { if (!closed) { fail("screen", Exception("projection stopped")) } }
        }
        init { val (p, f) = newPart("screen"); part = p; fin = f }

        fun start() {
            val dm = DisplayMetrics()
            @Suppress("DEPRECATION")
            (getSystemService(WINDOW_SERVICE) as WindowManager).defaultDisplay.getRealMetrics(dm)
            var w = dm.widthPixels; var hgt = dm.heightPixels
            val cap = 960
            if (maxOf(w, hgt) > cap) { val s = cap.toDouble() / maxOf(w, hgt); w = even((w * s).toInt()); hgt = even((hgt * s).toInt()) }
            else { w = even(w); hgt = even(hgt) }
            rec = buildRecorder(part, w, hgt, 5, 450_000)
            proj.registerCallback(cb, h)
            runCatching {
                vd = proj.createVirtualDisplay("oit-rec", w, hgt, dm.densityDpi,
                    android.hardware.display.DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, rec!!.surface, null, h)
                rec!!.start()
            }.onFailure { fail("screen", it as Exception); stop() }
        }

        fun stop() {
            closed = true
            runCatching { rec?.stop() }
            runCatching { rec?.release() }
            runCatching { vd?.release() }
            runCatching { proj.unregisterCallback(cb) }
            runCatching { proj.stop() }
            seal("screen", part, fin)
        }
    }

    private fun even(x: Int) = if (x % 2 == 0) x else x - 1

    /** 一路录像的 MediaRecorder: H.264 + (开着麦克风时) AAC, 写到 part. video source 由调用处用 surface 接. */
    private fun buildRecorder(part: File, w: Int, hgt: Int, fps: Int, bitrate: Int): MediaRecorder {
        val mic = micOn()
        @Suppress("DEPRECATION")
        val r = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(this) else MediaRecorder()
        if (mic) r.setAudioSource(MediaRecorder.AudioSource.MIC)
        r.setVideoSource(MediaRecorder.VideoSource.SURFACE)
        r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
        r.setVideoEncoder(MediaRecorder.VideoEncoder.H264)
        if (mic) { r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC); r.setAudioEncodingBitRate(128_000); r.setAudioSamplingRate(48_000) }
        r.setVideoSize(w, hgt)
        r.setVideoFrameRate(fps)
        r.setVideoEncodingBitRate(bitrate)
        r.setOutputFile(part.absolutePath)
        r.prepare()
        return r
    }

    private fun fail(kind: String, e: Exception) {
        state[kind] = "failed:" + (e.message ?: e.javaClass.simpleName)
        OitApp.instance.core.logRecord("$kind failed: ${e.message}")
    }

    companion object {
        const val ACT_CAMERA = "oit.rec.camera"
        const val ACT_SCREEN = "oit.rec.screen"
        const val ACT_STOP_CAMERA = "oit.rec.stop_camera"
        const val ACT_STOP_SCREEN = "oit.rec.stop_screen"
        const val EXTRA_CODE = "code"
        const val EXTRA_DATA = "data"

        /** kind → "running" / "failed:<原因>"; RecordManager 每秒读一次判断状态. */
        val state = java.util.concurrent.ConcurrentHashMap<String, String>()

        fun command(ctx: Context, action: String, projCode: Int = 0, projData: Intent? = null) {
            val i = Intent(ctx, RecordService::class.java).setAction(action)
            if (projData != null) i.putExtra(EXTRA_CODE, projCode).putExtra(EXTRA_DATA, projData)
            runCatching { ctx.startForegroundService(i) }
        }
    }
}
