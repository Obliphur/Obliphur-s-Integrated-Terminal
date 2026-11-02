package com.obliphur.oit.service

import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.provider.Settings
import androidx.core.content.FileProvider
import com.obliphur.oit.BuildConfig
import org.json.JSONObject
import java.io.File
import java.io.RandomAccessFile
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference

/**
 * 从 GitHub 检查 / 下载更新 (MECHANICS 16), 和桌面版 updater.py 同一个仓库, 同一套版本号规则.
 * 安装包取发布里的 .apk 附件; 安装由系统的安装界面完成 (需要用户确认).
 * 走手机本身的网络 (Wi-Fi / 移动数据), 不经过 OIT 自己的 VPN, 所以任何断网方式下都能检查.
 * 下载服务器对每条连接限速 (国内不开 VPN 时每条 40 KB/s 上下, 20 MB 要 8 分钟), 条数越多总速度越高:
 * 知道大小的附件 (服务器支持 Range) 切成 256 KB 一段, 同时开 PARALLEL_CONNS 条连接分头下 (实测 20 MB 约 20 秒);
 * 不支持 Range / 分段失败时退回一条连接 (断了接着下). 同 updater.py.
 */
class Updater(private val ctx: Context) {
    companion object {
        const val REPO = "Obliphur/Obliphur-s-Integrated-Terminal"
        const val API_LATEST = "https://api.github.com/repos/$REPO/releases/latest"
        const val RELEASES_PAGE = "https://github.com/$REPO/releases"
        val ALLOWED_HOSTS = setOf("api.github.com", "github.com", "codeload.github.com",
            "objects.githubusercontent.com", "release-assets.githubusercontent.com")
        const val MAX_DOWNLOAD = 150L * 1024 * 1024
        /** 下载卡住: 每 STALL_MS 毫秒至少要收到 STALL_BYTES 字节, 否则断开重连 (接着已下载的部分继续). */
        const val STALL_MS = 30_000L
        const val STALL_BYTES = 64L * 1024
        /** 连着这么多次重连都没什么进展就放弃. */
        const val MAX_TRIES = 4

        fun parseVersion(text: String?): List<Int>? {
            val nums = Regex("\\d+").findAll(text ?: "").map { it.value.toInt() }.take(3).toList()
            if (nums.isEmpty()) return null
            return nums + List(3 - nums.size) { 0 }
        }

        /** 名称里有 "1.2" 这样的版本号就用名称的, 否则用标签的. */
        fun releaseVersion(rel: JSONObject): List<Int>? {
            val name = rel.optString("name")
            val tag = rel.optString("tag_name")
            val vn = parseVersion(name)
            return if (vn != null && Regex("\\d+\\.\\d+").containsMatchIn(name)) vn else parseVersion(tag) ?: vn
        }

        fun newer(a: List<Int>, b: List<Int>): Boolean {
            for (i in 0 until 3) if (a[i] != b[i]) return a[i] > b[i]
            return false
        }

        fun text(v: List<Int>?) = v?.joinToString(".") ?: "?"

        const val ACCEPT_JSON = "application/vnd.github+json"
        const val ACCEPT_FILE = "application/octet-stream"

        /** 多连接下载 (见 fetchRanges): 实测不开 VPN 时 16 条 ≈ 0.6 MB/s, 32 条 ≈ 1 MB/s, 单条 ≈ 0.04 MB/s.
         *  一段小一点, 慢的连接不会拖到最后. 小于 MIN_PARALLEL 的文件不分段; 一段连着失败 SEGMENT_TRIES 次就放弃分段下载. */
        const val PARALLEL_CONNS = 32
        const val SEGMENT = 256L * 1024
        const val MIN_PARALLEL = 1024L * 1024
        const val SEGMENT_TRIES = 3

        /** 把 size 字节切成 [起, 止] (闭区间) 的若干段. */
        fun planSegments(size: Long, seg: Long = SEGMENT): List<LongRange> =
            generateSequence(0L) { it + seg }.takeWhile { it < size }.map { it..(minOf(it + seg, size) - 1) }.toList()

        /** "bytes 0-0/15669254" → 15669254; 看不懂返回 null. */
        fun rangeTotal(contentRange: String?): Long? =
            Regex("bytes (\\d+)-(\\d+)/(\\d+)").matchEntire(contentRange?.trim() ?: "")?.groupValues?.get(3)?.toLongOrNull()

        /** 分段下载: 把 url 的 size 字节切成 seg 大小的段写进 part, workers 条连接各取各的 (Range, 要回 206 而且起点对得上).
         *  一段失败 (断了 / 超时 / 内容不对) 换新连接重试 SEGMENT_TRIES 次; stallMs 内总共没进展 STALL_BYTES 就算卡住.
         *  全部下完才返回, 失败 / 取消时抛出 (part 里是半成品, 由调用的人删). progress(已下载, 最近的速度 字节每秒). */
        fun fetchRanges(url: String, size: Long, part: File, workers: Int, seg: Long, stallMs: Long, headers: Map<String, String>,
                        connect: (URL) -> HttpURLConnection, cancelled: () -> Boolean, progress: (Long, Long) -> Unit) {
            RandomAccessFile(part, "rw").use { it.setLength(size) }
            val segs = planSegments(size, seg)
            val next = AtomicInteger(0)
            val got = AtomicLong(0)
            val failure = AtomicReference<Throwable?>(null)
            val active = ConcurrentHashMap.newKeySet<HttpURLConnection>()
            val threads = (0 until minOf(workers, segs.size)).map {
                Thread {
                    try {
                        RandomAccessFile(part, "rw").use { f ->
                            while (failure.get() == null) {
                                val i = next.getAndIncrement()
                                if (i >= segs.size) break
                                fetchSegment(url, segs[i], f, headers, connect, active, failure, got)
                            }
                        }
                    } catch (e: Throwable) {
                        failure.compareAndSet(null, e)
                    }
                }.apply { isDaemon = true; start() }
            }
            var winAt = System.currentTimeMillis()
            var winBytes = 0L
            var speed = 0L
            var stallAt = winAt
            var stallBytes = 0L
            while (threads.any { it.isAlive }) {
                Thread.sleep(250)
                val now = System.currentTimeMillis()
                val n = got.get()
                if (cancelled()) failure.compareAndSet(null, Cancelled())
                if (failure.get() != null) {               // 出错了: 掐断还在等数据的连接, 不用等它们超时
                    active.forEach { runCatching { it.disconnect() } }
                    continue
                }
                if (now - winAt >= 2000) { speed = (n - winBytes) * 1000 / (now - winAt); winAt = now; winBytes = n }
                if (now - stallAt >= stallMs) {
                    if (n - stallBytes < STALL_BYTES) failure.compareAndSet(null, IllegalStateException("download stalled"))
                    stallAt = now; stallBytes = n
                }
                progress(n, speed)
            }
            threads.forEach { it.join() }
            failure.get()?.let { throw it }
            progress(got.get(), speed)
        }

        /** 取一段 [seg.first, seg.last] 写到 f 的对应位置; 失败换新连接重试. */
        private fun fetchSegment(url: String, seg: LongRange, f: RandomAccessFile, headers: Map<String, String>,
                                 connect: (URL) -> HttpURLConnection, active: MutableSet<HttpURLConnection>,
                                 failure: AtomicReference<Throwable?>, got: AtomicLong) {
            var attempt = 0
            while (true) {
                var counted = 0L
                var c: HttpURLConnection? = null
                try {
                    val conn = connect(URL(url)).also { c = it; active.add(it) }
                    conn.instanceFollowRedirects = false
                    conn.connectTimeout = 20_000
                    conn.readTimeout = 20_000
                    for ((k, v) in headers) conn.setRequestProperty(k, v)
                    conn.setRequestProperty("Range", "bytes=${seg.first}-${seg.last}")
                    if (conn.responseCode != 206) throw IllegalStateException("HTTP ${conn.responseCode}")
                    if (conn.getHeaderField("Content-Range")?.startsWith("bytes ${seg.first}-") != true) throw IllegalStateException("wrong range")
                    var pos = seg.first
                    conn.inputStream.use { inp ->
                        val buf = ByteArray(65536)
                        while (pos <= seg.last) {
                            val n = inp.read(buf, 0, minOf(buf.size.toLong(), seg.last + 1 - pos).toInt())
                            if (n < 0) throw IllegalStateException("short read")
                            f.seek(pos)
                            f.write(buf, 0, n)
                            pos += n
                            counted += n
                            got.addAndGet(n.toLong())
                        }
                    }
                    return
                } catch (e: Exception) {
                    got.addAndGet(-counted)
                    if (failure.get() != null || ++attempt >= SEGMENT_TRIES) throw e
                    Thread.sleep(1000)
                } finally {
                    c?.let { active.remove(it); it.disconnect() }
                }
            }
        }

        /** 下载来源: 地址, Accept, 大小 (-1 = 不知道), SHA-256 (null = 不知道). */
        data class Source(val url: String, val accept: String, val size: Long, val sha256: String?)

        /** 附件走 GitHub API 的附件地址: 直接跳到 release-assets.githubusercontent.com, 不经过 github.com
         *  (有的网络里 github.com 连不上, api.github.com 和下载服务器却能直连). 大小和 SHA-256 用来核对 (同 updater.asset_source). */
        fun assetSource(a: JSONObject): Source {
            val api = a.optString("url")
            val url = if (api.startsWith("https://api.github.com/")) api else a.optString("browser_download_url")
            val d = a.optString("digest")
            return Source(url, ACCEPT_FILE, a.optLong("size", -1), if (d.startsWith("sha256:")) d.substring(7).lowercase() else null)
        }

        /** 发布里的电脑版安装包: 名字带 windows / win / pc / python 的 .zip, 没有就用源码包 (同 updater.pick_download). */
        fun pickWindows(rel: JSONObject): Source? {
            val assets = rel.optJSONArray("assets")
            if (assets != null) for (i in 0 until assets.length()) {
                val a = assets.optJSONObject(i) ?: continue
                val n = a.optString("name").lowercase()
                if (n.endsWith(".zip") && listOf("windows", "win", "pc", "python").any { it in n }) return assetSource(a)
            }
            return rel.optString("zipball_url").ifEmpty { null }?.let { Source(it, ACCEPT_JSON, -1, null) }
        }

        /** 发布里的安卓安装包: 优先名字里带 release / android 的 .apk. */
        fun pickApk(rel: JSONObject): Source? {
            val assets = rel.optJSONArray("assets") ?: return null
            val apks = (0 until assets.length()).mapNotNull { assets.optJSONObject(it) }
                .filter { it.optString("name").lowercase().endsWith(".apk") }
            val best = apks.firstOrNull { it.optString("name").lowercase().let { n -> "release" in n || "android" in n } } ?: apks.firstOrNull()
            return best?.let { assetSource(it) }
        }
    }

    /** idle / checking / latest / available / noapk / downloading / ready / error */
    @Volatile var state = "idle"
        private set
    @Volatile var latest: List<Int>? = null
        private set
    @Volatile var error = ""
        private set
    /** 正在下载的进度 (给界面显示): 已下载 / 总大小 (-1 = 不知道) / 最近的速度 (字节每秒) / 第几次重连. */
    @Volatile var got = 0L
        private set
    @Volatile var total = -1L
        private set
    @Volatile var speed = 0L
        private set
    @Volatile var retry = 0
        private set
    @Volatile private var cancelled = false
    private class Cancelled : Exception("cancelled")
    private var release: JSONObject? = null
    private var apk: File? = null
    val current: List<Int> = parseVersion(BuildConfig.VERSION_NAME) ?: listOf(0, 0, 0)
    val latestText get() = text(latest)

    /** 手机本身的网络 (不是 VPN); 找不到就用默认网络. */
    private fun connect(url: URL): HttpURLConnection {
        val cm = ctx.getSystemService(ConnectivityManager::class.java)
        @Suppress("DEPRECATION")
        val net = cm.allNetworks.firstOrNull { n ->
            cm.getNetworkCapabilities(n)?.let {
                !it.hasTransport(NetworkCapabilities.TRANSPORT_VPN) && it.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            } == true
        }
        return (try { net?.openConnection(url) } catch (e: Exception) { null } ?: url.openConnection()) as HttpURLConnection
    }

    /** 只连 GitHub 的 https 地址; 跳转也只能跳到 GitHub. range: 从第几个字节接着下载 (0 = 从头). */
    private fun open(start: String, timeoutMs: Int, accept: String = ACCEPT_JSON, range: Long = 0): HttpURLConnection =
        openFinal(start, timeoutMs, accept, if (range > 0) "bytes=$range-" else null).first

    /** 同 open, 另外返回跳转之后的最终地址; rangeSpec: Range 头的值 (如 "bytes=0-0"), null = 不带. */
    private fun openFinal(start: String, timeoutMs: Int, accept: String, rangeSpec: String?): Pair<HttpURLConnection, String> {
        var url = start
        repeat(6) {
            val u = URL(url)
            if (u.protocol != "https" || u.host.lowercase() !in ALLOWED_HOSTS) throw IllegalStateException("only GitHub https addresses are allowed")
            val c = connect(u)
            c.instanceFollowRedirects = false
            c.connectTimeout = timeoutMs
            c.readTimeout = timeoutMs
            c.setRequestProperty("User-Agent", "OIT/${BuildConfig.VERSION_NAME}")
            c.setRequestProperty("Accept", accept)
            if (rangeSpec != null) c.setRequestProperty("Range", rangeSpec)
            val code = c.responseCode
            if (code in 300..399) {
                url = URL(u, c.getHeaderField("Location") ?: throw IllegalStateException("HTTP $code")).toString()
                c.disconnect()
                return@repeat
            }
            if (code != 200 && !(code == 206 && rangeSpec != null)) { c.disconnect(); throw IllegalStateException("HTTP $code") }
            return c to url
        }
        throw IllegalStateException("too many redirects")
    }

    fun check(done: () -> Unit) {
        if (state == "checking" || state == "downloading") return
        state = "checking"
        Thread {
            try {
                val c = open(API_LATEST, 20_000)
                val rel = c.inputStream.use { JSONObject(it.readBytes().toString(Charsets.UTF_8)) }
                c.disconnect()
                val v = releaseVersion(rel) ?: throw IllegalStateException("no version number in the release")
                release = rel
                latest = v
                error = ""
                state = when {
                    BuildConfig.DEV_BUILD -> "dev"            // 开发版 (本地构建): 不会更新成发布版, 只记下最新的发布版本号
                    !newer(v, current) -> "latest"
                    pickApk(rel) == null -> "noapk"
                    else -> "available"
                }
            } catch (e: Exception) {
                error = e.message ?: e.javaClass.simpleName
                state = "error"
            }
            done()
        }.start()
    }

    /** 在当前线程里取 GitHub 上的最新发布 (给其他设备发更新时用). */
    fun fetchRelease(): JSONObject {
        val c = open(API_LATEST, 20_000)
        val rel = c.inputStream.use { JSONObject(it.readBytes().toString(Charsets.UTF_8)) }
        c.disconnect()
        val v = releaseVersion(rel) ?: throw IllegalStateException("no version number in the release")
        release = rel
        latest = v
        if (state in setOf("idle", "latest", "available", "noapk", "error"))
            state = if (!newer(v, current)) "latest" else if (pickApk(rel) == null) "noapk" else "available"
        return rel
    }

    /** 下载到 f: 先写 .part, 核对过大小和 SHA-256 再改名; 失败时删掉 .part.
     *  断了 / 卡住 (STALL_MS 内收不到 STALL_BYTES) 时重连, 接着已下载的部分继续 (Range);
     *  连着 MAX_TRIES 次没什么进展才放弃. track: 进度给界面显示, 可以取消 (自己的更新; 给其他设备下载时不显示). */
    private fun downloadTo(src: Source, f: File, track: Boolean) {
        val part = File(f.path + ".part")
        part.delete()
        if (track) { got = 0; total = src.size; speed = 0; retry = 0 }
        try {
            if (!(src.size >= MIN_PARALLEL && tryParallel(src, part, track))) streamTo(src, part, track)
            val total = part.length()
            if (src.size >= 0 && total != src.size) throw IllegalStateException("size mismatch ($total != ${src.size})")
            val md = java.security.MessageDigest.getInstance("SHA-256")
            part.inputStream().use { inp ->
                val buf = ByteArray(65536)
                while (true) { val n = inp.read(buf); if (n < 0) break; md.update(buf, 0, n) }
            }
            val sha = md.digest().joinToString("") { "%02x".format(it) }
            if (src.sha256 != null && sha != src.sha256) throw IllegalStateException("SHA-256 mismatch")
            f.delete()
            if (!part.renameTo(f)) throw IllegalStateException("cannot save the download")
        } catch (e: Exception) {
            part.delete()
            throw e
        }
    }

    /** 知道大小的附件先分段下载 (多条连接). false = 不行 (服务器不支持 Range / 中途失败): part 已清掉, 该走一条连接. */
    private fun tryParallel(src: Source, part: File, track: Boolean): Boolean {
        try {
            if (fetchParallel(src, part, track)) return true
        } catch (e: Cancelled) {
            throw e
        } catch (e: Exception) {
            // 退回一条连接
        }
        part.delete()
        if (track) { got = 0; speed = 0 }
        return false
    }

    /** 先要第 0 个字节: 走完 api.github.com 的跳转, 拿到下载服务器上带签名的地址, 确认支持 Range 而且总大小对得上; 再分段下载. */
    private fun fetchParallel(src: Source, part: File, track: Boolean): Boolean {
        if (src.size > MAX_DOWNLOAD) throw IllegalStateException("download too large")
        val (c0, final) = openFinal(src.url, 20_000, src.accept, "bytes=0-0")
        try {
            if (c0.responseCode != 206 || rangeTotal(c0.getHeaderField("Content-Range")) != src.size) return false
        } finally {
            c0.disconnect()
        }
        fetchRanges(final, src.size, part, PARALLEL_CONNS, SEGMENT, STALL_MS,
            mapOf("User-Agent" to "OIT/${BuildConfig.VERSION_NAME}", "Accept" to src.accept),
            { connect(it) }, { track && cancelled }) { n, s -> if (track) { got = n; speed = s } }
        return true
    }

    /** 一条连接下完整文件: 断了 / 卡住时重连, 接着 part 已有的部分继续 (Range); 连着 MAX_TRIES 次没什么进展才放弃. */
    private fun streamTo(src: Source, part: File, track: Boolean) {
        var fails = 0
        while (true) {
            val before = part.length()
            try {
                fetchPart(src, part, track)
                break
            } catch (e: Cancelled) {
                throw e
            } catch (e: Exception) {
                if (part.length() - before < 256 * 1024) fails++ else fails = 0
                if (fails >= MAX_TRIES) throw e
                if (track) retry++
                Thread.sleep(2000)
                if (track && cancelled) throw Cancelled()
            }
        }
    }

    /** 连一次, 接着 part 已有的部分往下写, 直到下完 / 断开 / 卡住. */
    private fun fetchPart(src: Source, part: File, track: Boolean) {
        val have = part.length()
        val c = open(src.url, 20_000, src.accept, have)
        try {
            val append = have > 0 && c.responseCode == 206
            var n = if (append) have else 0L
            if (track && !append && c.contentLengthLong > 0 && total < 0) total = c.contentLengthLong
            var winAt = System.currentTimeMillis()
            var winBytes = 0L
            c.inputStream.use { inp ->
                java.io.FileOutputStream(part, append).use { out ->
                    val buf = ByteArray(65536)
                    while (true) {
                        if (track && cancelled) throw Cancelled()
                        val r = inp.read(buf)
                        if (r < 0) break
                        n += r
                        winBytes += r
                        if (n > MAX_DOWNLOAD) throw IllegalStateException("download too large")
                        out.write(buf, 0, r)
                        if (track) got = n
                        val now = System.currentTimeMillis()
                        if (now - winAt >= STALL_MS) {
                            if (track) speed = winBytes * 1000 / (now - winAt)
                            if (winBytes < STALL_BYTES) throw IllegalStateException("download stalled")
                            winAt = now; winBytes = 0
                        } else if (track && now - winAt >= 2000) speed = winBytes * 1000 / (now - winAt)
                    }
                }
            }
        } finally {
            c.disconnect()
        }
    }

    /** 给其他设备下载这个平台的安装包 (同一个版本只下载一次). 在后台线程里调用. */
    fun fetchPackage(rel: JSONObject, platform: String): File {
        val src = when (platform) {
            "android" -> pickApk(rel)
            "windows" -> pickWindows(rel)
            else -> null
        } ?: throw IllegalStateException("no-package")
        val dir = File(ctx.cacheDir, "relay").apply { mkdirs() }
        val f = File(dir, "OIT-$platform-${text(releaseVersion(rel))}" + if (platform == "android") ".apk" else ".zip")
        dir.listFiles()?.forEach { if (it != f) it.delete() }
        if (f.length() > 0) return f
        downloadTo(src, f, track = false)
        return f
    }

    /** 其他设备直接发来的安装包 (已核对过 SHA-256): 和自己下载好的一样, 等用户点安装. */
    fun acceptFile(f: File, v: List<Int>?) {
        if (BuildConfig.DEV_BUILD) return        // 开发版不装发布版的安装包
        cancelled = true            // 自己还在从 GitHub 下载: 不用了
        apk = f
        if (v != null) latest = v
        error = ""
        state = "ready"
    }

    /** 有新版本的安装包可以下载 (包括上一次下载失败之后再试). */
    val canDownload get() = release?.let { rel -> state in setOf("available", "error") &&
        latest?.let { newer(it, current) } == true && pickApk(rel) != null } == true

    fun download(done: () -> Unit) {
        if (BuildConfig.DEV_BUILD) return
        val rel = release ?: return
        if (!canDownload) return
        val src = pickApk(rel) ?: return
        state = "downloading"
        cancelled = false
        Thread {
            try {
                val dir = File(ctx.cacheDir, "updates").apply { mkdirs() }
                dir.listFiles()?.forEach { it.delete() }
                val f = File(dir, "OIT-${text(latest)}.apk")
                downloadTo(src, f, track = true)
                apk = f
                error = ""
                state = "ready"
            } catch (e: Cancelled) {
                if (state != "downloading") return@Thread      // 其他设备已经发来了 (acceptFile): 那边会提示
                error = ""
                state = "available"
            } catch (e: Throwable) {
                // 任何错 (包括 Error) 都要结束"正在下载", 不然界面一直停在这里
                if (state == "downloading") { error = e.message ?: e.javaClass.simpleName; state = "error" }
            }
            done()
        }.start()
    }

    /** 取消正在进行的下载. */
    fun cancel() {
        if (state == "downloading") cancelled = true
    }

    /** 打开系统的安装界面. 还没允许 OIT 安装应用时先打开那个设置页. */
    fun install() {
        if (BuildConfig.DEV_BUILD) return
        val f = apk ?: return
        if (!ctx.packageManager.canRequestPackageInstalls()) {
            ctx.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${ctx.packageName}"))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            return
        }
        val uri = FileProvider.getUriForFile(ctx, "${ctx.packageName}.files", f)
        ctx.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    fun openReleasePage() {
        ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(RELEASES_PAGE)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}
