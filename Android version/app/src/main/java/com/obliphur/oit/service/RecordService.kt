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
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.graphics.Rect
import android.media.Image
import android.media.ImageReader
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
 * 原始 mp4 写在缓存里 (不进数据目录, 防止被网盘自动上传).
 * 不录连续的视频 (视频有时长限制, 占地方): 每 [STILL_EVERY_MS] 毫秒 (30 秒) 拍一张照片 (目光) / 截一张图 (刻痕) 送进 mp4,
 * 没有声音; 电脑合并时把照片按顺序接成每张 0.5 秒的视频.
 */
class RecordService : Service() {
    private val thread = HandlerThread("oit-rec").apply { start() }
    private val h = Handler(thread.looper)
    private var cam: CamJob? = null
    private var screen: ScreenJob? = null
    private var screenLock: android.os.PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    // 录像期间屏幕不自动熄灭 (MECHANICS 18.8); 熄屏后摄像头可能断, 亮屏时自动接上 (通知 RecordManager 立刻重试)
    private val screenOn = object : android.content.BroadcastReceiver() {
        override fun onReceive(c: android.content.Context?, i: Intent?) {
            if (i?.action == Intent.ACTION_SCREEN_ON) OitApp.instance.recordManager.onScreenOn()
        }
    }

    override fun onCreate() {
        super.onCreate()
        runCatching { registerReceiver(screenOn, android.content.IntentFilter(Intent.ACTION_SCREEN_ON)) }
        runCatching { if (orient.canDetectOrientation()) orient.enable() }
    }

    // ---- 目光跟着手机的朝向转 (MECHANICS 18.8): 陀螺仪 / 加速度计给出手机转了多少度 ----
    /** 手机此刻的朝向 (顺时针转了 0 / 90 / 180 / 270 度, 平放 / 读不出时保持上一个).
     *  记在 companion 里: 服务每次开始专注都会重新创建, 不能每次都从"竖着"算起. */
    private var devDeg: Int
        get() = lastDeg
        set(v) { lastDeg = v }
    /** 这个服务启动后收到过朝向了没有 (开摄像头前先等一下, 第一段就按真实的朝向录). */
    @Volatile private var gotDeg = false
    private var turnAt = 0L

    private val orient by lazy { object : android.view.OrientationEventListener(this) {
        override fun onOrientationChanged(o: Int) {
            if (o == ORIENTATION_UNKNOWN) return
            // 只在明确靠近某个方向 (±30°) 时才换, 斜着拿的时候不来回跳
            val snap = ((o + 45) / 90 * 90) % 360
            val off = Math.floorMod(o - snap + 180, 360) - 180
            if (kotlin.math.abs(off) > 30) return
            gotDeg = true
            if (snap == devDeg) return
            devDeg = snap
            // 转过去停稳 2 秒: 正在录的目光另起一段, 新的一段按新的朝向录 (画面转正, 横拍 / 竖拍跟着变)
            val at = android.os.SystemClock.elapsedRealtime()
            turnAt = at
            h.postDelayed({
                val c = cam
                if (turnAt == at && c != null && c.devDeg != devDeg) {
                    OitApp.instance.core.logRecord("camera turned to $devDeg: new segment")
                    // 等上一段的摄像头真正关掉再开新的 (马上重开很多手机会报"摄像头被占用", 这一段就录不成)
                    var opened = false
                    val reopen = Runnable {
                        if (!opened && cam == null && state["camera"] == "running") {
                            opened = true
                            cam = CamJob(c.mgr, c.id).also { it.start() }
                        }
                    }
                    c.onClosed = { h.post(reopen) }
                    c.stop()
                    cam = null
                    h.postDelayed(reopen, 3000)              // 收不到"已关闭"的回调时也接着录
                }
            }, 2000)
        }
    } }

    /** 录像时拿一个屏幕唤醒锁, 让屏幕不自动熄灭 (停录时放开). */
    @Suppress("DEPRECATION", "WakelockTimeout")
    private fun keepScreen(on: Boolean) {
        if (on && screenLock == null) {
            screenLock = runCatching {
                (getSystemService(POWER_SERVICE) as android.os.PowerManager)
                    .newWakeLock(android.os.PowerManager.SCREEN_BRIGHT_WAKE_LOCK, "oit:rec").apply { acquire() }
            }.getOrNull()
        } else if (!on) {
            runCatching { screenLock?.release() }
            screenLock = null
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val action = intent?.action
        // startForegroundService 之后必须尽快进入前台: 类型按这次要录的几路算 (停止指令时按剩下的)
        val wantCam = cam != null || camPending || action == ACT_CAMERA
        val wantScreen = screen != null || action == ACT_SCREEN
        enterForeground(wantCam, wantScreen)
        h.post {
            when (action) {
                ACT_CAMERA -> if (cam == null) startCamWhenOriented(0)
                ACT_SCREEN -> if (screen == null) startScreen(intent)
                ACT_STOP_CAMERA -> { camPending = false; cam?.stop(); cam = null }
                ACT_STOP_SCREEN -> { screen?.stop(); screen = null }
            }
            if (cam == null && !camPending && screen == null) {
                keepScreen(false)
                stopForeground(STOP_FOREGROUND_REMOVE); stopSelf()
            } else { enterForeground(cam != null, screen != null); keepScreen(true) }
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        runCatching { orient.disable() }
        keepScreen(false)
        runCatching { unregisterReceiver(screenOn) }
        h.post { cam?.stop(); screen?.stop(); cam = null; screen = null }
        h.postDelayed({ thread.quitSafely() }, 500)
        state.clear()
        super.onDestroy()
    }

    /** 前台服务类型随正在录的几路变 (Android 14 起必须声明 camera / mediaProjection). */
    private fun enterForeground(cam: Boolean, screen: Boolean) {
        val note = Notifier.recording(this, cam, screen)
        try {
            if (Build.VERSION.SDK_INT >= 29) {
                var type = 0
                if (cam) type = type or ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
                if (screen) type = type or ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION
                if (type == 0) type = ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA   // 兜底 (manifest 已声明)
                startForeground(Notifier.ID_RECORD, note, type)
            } else startForeground(Notifier.ID_RECORD, note)
        } catch (e: Exception) {
            fail("screen", e); fail("camera", e)
            stopForeground(STOP_FOREGROUND_REMOVE); stopSelf()
        }
    }

    // ---------------------------------------------------------------- 摄像头 (目光)

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

    /** 先等收到手机的朝向 (最多 1.5 秒) 再开摄像头, 第一段就按真实的朝向写旋转标记 (MECHANICS 18.8). */
    private fun startCamWhenOriented(tries: Int) {
        if (cam != null) { camPending = false; return }
        if (tries > 0 && !camPending) return                     // 等的时候被叫停了
        if (gotDeg || tries >= 5 || !orient.canDetectOrientation()) { camPending = false; startCam(); return }
        camPending = true
        h.postDelayed({ startCamWhenOriented(tries + 1) }, 300)
    }

    /** 正在等朝向, 马上就要开摄像头 (这时服务不能因为"什么都没在录"而停掉). */
    private var camPending = false

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

    /** 摄像头实际支持的录制尺寸里, 挑最接近 640×480 的 (取不到就退回 640×480). */
    private fun camSize(mgr: CameraManager, id: String): Pair<Int, Int> {
        val target = 640 * 480
        val sizes = runCatching {
            mgr.getCameraCharacteristics(id)
                .get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
                ?.getOutputSizes(MediaRecorder::class.java)
        }.getOrNull()
        val s = sizes?.filter { it.width <= 1280 && it.height <= 1280 }
            ?.minByOrNull { kotlin.math.abs(it.width * it.height - target) }
        return if (s != null) s.width to s.height else 640 to 480
    }

    /** 测光用的 ImageReader 的尺寸: 摄像头支持的 YUV 尺寸里最小的 (不小于 320×240; 取不到就 320×240). */
    private fun meterSize(mgr: CameraManager, id: String): Pair<Int, Int> {
        val s = runCatching {
            mgr.getCameraCharacteristics(id)
                .get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
                ?.getOutputSizes(android.graphics.ImageFormat.YUV_420_888)
        }.getOrNull()?.filter { it.width >= 320 && it.height >= 240 }?.minByOrNull { it.width * it.height }
        return if (s != null) s.width to s.height else 320 to 240
    }

    private inner class CamJob(val mgr: CameraManager, val id: String) {
        private val part: File; private val fin: File
        private var rec: MediaRecorder? = null
        private var device: CameraDevice? = null
        private var session: CameraCaptureSession? = null
        private var meter: android.media.ImageReader? = null
        @Volatile private var closed = false
        /** 摄像头真正关掉之后 (手机转了, 换一段时用). */
        @Volatile var onClosed: (() -> Unit)? = null
        /** 这一段开始时手机的朝向 (MECHANICS 18.8): 按它写 mp4 的旋转标记; 手机转了就另起一段. */
        val devDeg = this@RecordService.devDeg
        init { val (p, f) = newPart("camera"); part = p; fin = f }

        fun start() {
            // 用摄像头 / MediaRecorder 真正支持的尺寸 (480×360 这类不是标准采集尺寸, 很多手机不支持, prepare 会失败);
            // 在支持的尺寸里挑最接近 640×480 的 (压一下但保证能录)
            val (cw, ch) = camSize(mgr, id)
            rec = buildRecorder(part, cw, ch, STILL_BITRATE, hint(mgr, id, devDeg))
            // 每 30 秒一张照片: 摄像头连续出画面到一个小的 ImageReader (只为了让自动曝光 / 对焦一直跑着, 画面直接丢掉),
            // 每 STILL_EVERY_MS 毫秒单独拍一帧送进 MediaRecorder (连续送多少帧就录多少帧; 视频帧率只是提示)
            val (mw, mh) = meterSize(mgr, id)
            meter = android.media.ImageReader.newInstance(mw, mh, android.graphics.ImageFormat.YUV_420_888, 2).apply {
                setOnImageAvailableListener({ r -> runCatching { r.acquireLatestImage()?.close() } }, h)
            }
            @Suppress("MissingPermission")
            mgr.openCamera(id, object : CameraDevice.StateCallback() {
                override fun onOpened(d: CameraDevice) {
                    device = d
                    val surface = rec!!.surface
                    val meterSurface = meter!!.surface
                    @Suppress("DEPRECATION")
                    d.createCaptureSession(listOf(surface, meterSurface), object : CameraCaptureSession.StateCallback() {
                        override fun onConfigured(s: CameraCaptureSession) {
                            if (closed) return
                            session = s
                            val run = d.createCaptureRequest(CameraDevice.TEMPLATE_RECORD).apply { addTarget(meterSurface) }
                            s.setRepeatingRequest(run.build(), null, h)
                            val shot = d.createCaptureRequest(CameraDevice.TEMPLATE_RECORD).apply { addTarget(surface) }.build()
                            runCatching { rec!!.start() }.onFailure { fail("camera", it as Exception); return }
                            val tick = object : Runnable {
                                override fun run() {
                                    if (closed) return
                                    runCatching { s.capture(shot, null, h) }
                                    h.postDelayed(this, STILL_EVERY_MS)
                                }
                            }
                            h.postDelayed(tick, FIRST_SHOT_MS)       // 等一下再拍第一张 (自动曝光要几帧才稳)
                        }
                        override fun onConfigureFailed(s: CameraCaptureSession) = fail("camera", Exception("session"))
                    }, h)
                }
                override fun onClosed(d: CameraDevice) { onClosed?.invoke(); onClosed = null }
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
            runCatching { meter?.close() }
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
        private var reader: ImageReader? = null
        private var recSurface: android.view.Surface? = null
        /** 屏幕最近的一帧 (屏幕没变化时不会有新的, 截图用的就是它). */
        private var latest: Image? = null
        private var w = 0
        private var hgt = 0
        @Volatile private var closed = false
        private val cb = object : MediaProjection.Callback() {
            override fun onStop() { if (!closed) { fail("screen", Exception("projection stopped")) } }
        }
        init { val (p, f) = newPart("screen"); part = p; fin = f }

        fun start() {
            val dm = DisplayMetrics()
            @Suppress("DEPRECATION")
            (getSystemService(WINDOW_SERVICE) as WindowManager).defaultDisplay.getRealMetrics(dm)
            w = dm.widthPixels; hgt = dm.heightPixels
            val cap = 960
            if (maxOf(w, hgt) > cap) { val s = cap.toDouble() / maxOf(w, hgt); w = even((w * s).toInt()); hgt = even((hgt * s).toInt()) }
            else { w = even(w); hgt = even(hgt) }
            rec = buildRecorder(part, w, hgt, STILL_BITRATE)
            recSurface = rec!!.surface
            // 投屏的画面先到 ImageReader (只留最近的一帧), 每 30 秒把它截下来画进 MediaRecorder 的 surface (= 一张截图)
            reader = ImageReader.newInstance(w, hgt, PixelFormat.RGBA_8888, 3).apply {
                setOnImageAvailableListener({ r ->
                    val img = runCatching { r.acquireLatestImage() }.getOrNull() ?: return@setOnImageAvailableListener
                    if (closed) { img.close(); return@setOnImageAvailableListener }
                    latest?.close()
                    latest = img
                }, h)
            }
            proj.registerCallback(cb, h)
            runCatching {
                vd = proj.createVirtualDisplay("oit-rec", w, hgt, dm.densityDpi,
                    android.hardware.display.DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, reader!!.surface, null, h)
                rec!!.start()
                h.postDelayed(shot, FIRST_SHOT_MS)
            }.onFailure { fail("screen", it as Exception); stop() }
        }

        /** 截一张图 (最近的一帧) 送进录像; 每 [STILL_EVERY_MS] 毫秒一次. */
        private val shot = object : Runnable {
            override fun run() {
                if (closed) return
                h.postDelayed(this, STILL_EVERY_MS)
                val img = latest ?: return                       // 还没有画面: 下一次再截
                runCatching {
                    val plane = img.planes[0]
                    val px = plane.pixelStride
                    val bmp = Bitmap.createBitmap(plane.rowStride / px, img.height, Bitmap.Config.ARGB_8888)
                    plane.buffer.rewind()
                    bmp.copyPixelsFromBuffer(plane.buffer)
                    val surface = recSurface ?: return@runCatching
                    val canvas = runCatching { surface.lockHardwareCanvas() }.getOrNull() ?: surface.lockCanvas(null)
                    try { canvas.drawBitmap(bmp, Rect(0, 0, w, hgt), Rect(0, 0, w, hgt), null) }
                    finally { surface.unlockCanvasAndPost(canvas) }
                    bmp.recycle()
                }.onFailure { OitApp.instance.core.logRecord("screen shot failed: ${it.message}") }
            }
        }

        fun stop() {
            closed = true
            h.removeCallbacks(shot)
            runCatching { latest?.close() }
            latest = null
            runCatching { vd?.release() }
            runCatching { reader?.close() }
            runCatching { rec?.stop() }
            runCatching { rec?.release() }
            runCatching { proj.unregisterCallback(cb) }
            runCatching { proj.stop() }
            seal("screen", part, fin)
        }
    }

    private fun even(x: Int) = if (x % 2 == 0) x else x - 1

    /** 一路录像的 MediaRecorder: H.264 (没有声音), 写到 part. video source 由调用处用 surface 接. */
    /** 目光的 mp4 播放时要顺时针转多少度才是正的 (摄像头装在机身里的角度 + 手机此刻的朝向; 前置摄像头是镜像的, 反着算).
     *  同 Camera2 文档里 JPEG_ORIENTATION 的算法. 电脑合并时按这个标记把画面转正. */
    private fun hint(mgr: CameraManager, id: String, dev: Int): Int {
        val ch = runCatching { mgr.getCameraCharacteristics(id) }.getOrNull() ?: return 0
        val sensor = ch.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 0
        val front = ch.get(CameraCharacteristics.LENS_FACING) == CameraMetadata.LENS_FACING_FRONT
        return Math.floorMod(sensor + (if (front) -dev else dev), 360)
    }

    private fun buildRecorder(part: File, w: Int, hgt: Int, bitrate: Int, orientation: Int = 0): MediaRecorder {
        @Suppress("DEPRECATION")
        val r = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(this) else MediaRecorder()
        r.setVideoSource(MediaRecorder.VideoSource.SURFACE)
        r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
        r.setVideoEncoder(MediaRecorder.VideoEncoder.H264)
        r.setVideoSize(w, hgt)
        r.setVideoFrameRate(1)
        r.setVideoEncodingBitRate(bitrate)
        if (orientation != 0) r.setOrientationHint(orientation)
        r.setOutputFile(part.absolutePath)
        r.prepare()
        return r
    }

    private fun fail(kind: String, e: Exception) {
        state[kind] = "failed:" + (e.message ?: e.javaClass.simpleName)
        OitApp.instance.core.logRecord("$kind failed: ${e.message}")
    }

    companion object {
        /** 每隔多久拍一张照片 / 截一张图 (毫秒): 30 秒. */
        const val STILL_EVERY_MS = 30_000L
        /** 开始录之后第一张照片等多久再拍. */
        const val FIRST_SHOT_MS = 1_500L
        /** 最后知道的手机朝向 (跨服务实例保留). */
        @Volatile private var lastDeg = 0
        /** 两张照片隔 30 秒: 码率只是个上限, 每一张都能分到足够的码率; 文件大小由每张的画质决定. */
        const val STILL_BITRATE = 200_000
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
