package com.obliphur.oit.service

import android.content.Intent
import android.os.SystemClock
import com.obliphur.oit.OitApp
import com.obliphur.oit.core.RecCrypto
import org.json.JSONObject
import java.io.File

/**
 * 合约录像的调度 (MECHANICS 18.8): 每秒看合约要录什么 (目光 → 摄像头, 刻痕 → 屏幕), 指挥 [RecordService] 开始 / 停止.
 * - 目光只由开始时选定的那台设备录 (records(myId)); 本机被选中才录.
 * - 刻痕只有"焦点设备"录屏: 哪台设备在操作就录哪台, 每 10 秒判定一次; 两台都有操作时优先电脑; 都没操作时保持上一个.
 *   手机上的"操作"= 正在用 OIT (core.lastOp).
 * - 录屏需要系统授权 (每次一个): 没有授权且界面在前台时, 请界面弹出授权 (needProjection); 拿到后存在 OitApp.recProjection.
 * - 合约结束后, 刻痕的片段要合并成一个视频: 本机不是合约主人时, 问一次用户, 同意就把片段发给主人 (在线的电脑) 合并;
 *   本机是主人时手机还不能合并 (没有 Media Foundation), 片段留在本机 (设置 → 录像 仍可逐段查看 / 播放).
 */
class RecordManager(private val app: OitApp) {
    private val core get() = app.core
    private val cacheDir get() = app.cacheDir
    private val dataDir get() = File(app.filesDir, "data")

    private var scarFocusId: String = ""
    private var scarFocusAt = 0L
    private val skipCid = HashMap<String, Long>()     // kind → 这个 cid 暂时不录 (出错 / 没授权) 到的时刻
    private val retryAt = HashMap<String, Long>()
    private var activeCid = ""
    private var activeOwner = ""

    /** 界面该做的事 (MainActivity 每次可见时看一眼): 要录屏授权 / 要摄像头 + 麦克风权限. */
    @Volatile var needProjection = false
        private set
    @Volatile var needPerms = false
        private set
    @Volatile var needCamera = false
        private set
    @Volatile var needAudio = false
        private set

    fun tick() {
        val c = core.contract
        val me = core.myId()
        val cid = if (c.active) c.live.optString("id", "") else ""
        // 记下进行中合约的 主人 (结束后要用它来决定片段发给谁)
        if (c.active) { activeCid = cid; activeOwner = c.owner }
        else if (activeCid.isNotEmpty()) { afterContract(activeCid, activeOwner); activeCid = ""; activeOwner = "" }

        val want = if (c.active && !core.dm.resetting && me.isNotEmpty()) c.records(me).toMutableSet() else mutableSetOf()
        // 刻痕: 只有焦点设备录屏
        if ("screen" in want && scarFocus() != me) want.remove("screen")

        needProjection = "screen" in want && app.recProjection == null && !recording("screen")
        needCamera = "camera" in want
        needAudio = want.isNotEmpty() && core.settings.bool("REC_MIC")
        needPerms = needCamera || needAudio

        val now = SystemClock.elapsedRealtime()
        for (kind in RecCrypto.KINDS) {
            val st = RecordService.state[kind] ?: ""
            if (kind !in want) {
                if (recording(kind) || st.isNotEmpty()) stop(kind)
                continue
            }
            if (skipCid[kind] == cidKey(cid)) continue
            if (st.startsWith("failed")) {
                val why = st.removePrefix("failed:")
                core.logRecord("$kind failed ($cid): $why")
                stop(kind)
                // 本机没有摄像头: 这个合约不再试 (不算违反); 其他 (权限 / 被占用 / 出错): 过一会儿重试
                if (kind == "camera" && why.contains("no camera")) skipCid[kind] = cidKey(cid)
                else retryAt[kind] = now + 15_000
                continue
            }
            if (recording(kind)) continue
            if (now < (retryAt[kind] ?: 0)) continue
            // 摄像头要先有 CAMERA 权限才开始 (没有就等界面申请, needCamera 已置位), 否则会一开就失败
            if (kind == "camera" && !granted(android.Manifest.permission.CAMERA)) continue
            start(kind)
        }
        // 换了合约: 清掉上一个合约的跳过记录
        skipCid.entries.removeAll { it.value != cidKey(cid) }
    }

    private fun cidKey(cid: String) = cid.hashCode().toLong()
    private fun recording(kind: String) = RecordService.state[kind] == "running"
    private fun granted(p: String) = app.checkSelfPermission(p) == android.content.pm.PackageManager.PERMISSION_GRANTED

    private fun start(kind: String) {
        when (kind) {
            "camera" -> RecordService.command(app, RecordService.ACT_CAMERA)
            "screen" -> {
                val p = app.recProjection ?: return             // 没有授权: 等界面弹出授权 (needProjection)
                app.recProjection = null                         // 授权一次只用一次
                RecordService.command(app, RecordService.ACT_SCREEN, p.first, p.second)
            }
        }
        core.logRecord("$kind start")
    }

    private fun stop(kind: String) {
        RecordService.command(app, if (kind == "camera") RecordService.ACT_STOP_CAMERA else RecordService.ACT_STOP_SCREEN)
        RecordService.state.remove(kind)
    }

    /** 本机此刻在录这一路吗 (设置 → 录像: 正在写的不能删). */
    fun isRecording(kind: String) = recording(kind)

    /** 用户在合约页手动终止这一路录像 (算违反, 调用处负责记违反): 停掉, 并且这个合约不再自动重开. */
    fun forceStop(kind: String) {
        stop(kind)
        skipCid[kind] = cidKey(core.contract.live.optString("id", ""))
    }

    // ---------------------------------------------------------------- 焦点设备 (刻痕)

    private fun scarFocus(): String {
        val mono = SystemClock.elapsedRealtime()
        if (scarFocusId.isNotEmpty() && mono - scarFocusAt < FOCUS_EVERY_MS) return scarFocusId
        scarFocusAt = mono
        val sync = app.sync ?: return core.myId()
        val nowms = System.currentTimeMillis()
        val peers = sync.peers()
        data class Cand(val pc: Boolean, val op: Long, val id: String)
        val cands = ArrayList<Cand>()
        cands.add(Cand(false, core.lastOp, core.myId()))             // 本机 (手机)
        for (d in sync.deviceStates()) {
            if (!d.online) continue
            val pc = (peers[d.id]?.optString("platform") ?: "").lowercase().startsWith("win")
            cands.add(Cand(pc, d.status.optLong("last_op", 0), d.id))
        }
        var active = cands.filter { nowms - it.op <= FOCUS_EVERY_MS }
        if (active.isEmpty()) {
            if (cands.any { it.id == scarFocusId }) return scarFocusId   // 都没操作: 保持上一个
            active = cands
        }
        if (active.any { it.pc }) active = active.filter { it.pc }       // 两台都有操作: 优先电脑
        scarFocusId = active.maxByOrNull { it.op }?.id ?: core.myId()
        return scarFocusId
    }

    // ---------------------------------------------------------------- 结束后合并 / 传输

    private fun afterContract(cid: String, owner: String) {
        stop("camera"); stop("screen")
        val me = core.myId()
        if (owner.isEmpty() || owner == me) {
            // 本机是主人: 手机还不能合并 (没有 Media Foundation), 片段留在本机
            Thread {
                Thread.sleep(1500)
                val segs = RecCrypto.filesOf(dataDir, "screen", cid)
                if (segs.isNotEmpty()) core.logRecord("owner on phone: ${segs.size} scar segments kept (merge on a PC)")
            }.apply { isDaemon = true }.start()
            return
        }
        Thread {
            Thread.sleep(1500)                                          // 等最后一段封装 (加密) 完
            val segs = RecCrypto.filesOf(dataDir, "screen", cid).filter { !it.partial }
            if (segs.isEmpty()) return@Thread
            val sync = app.sync ?: return@Thread
            val p = sync.peers()[owner] ?: return@Thread
            val online = sync.deviceStates().any { it.id == owner && it.online }
            if (!online || !p.optString("platform").lowercase().startsWith("win")) return@Thread   // 主人必须是在线的电脑
            app.askSendSegments(cid, owner, p.optString("name", "?"), segs.map { it.file }, segs.sumOf { it.size })
        }.apply { isDaemon = true }.start()
    }

    /** 收到别的设备发来的刻痕片段 (MECHANICS 18.8): 存进 incoming/<cid>. 手机不能合并, 只收下留着. 返回 "" 表示收下. */
    fun recordingOffered(peerId: String, who: String, o: JSONObject): String {
        val cid = o.optString("cid")
        if (cid.isEmpty()) return "bad-request"
        val d = RecCrypto.incomingDir(dataDir, cid)
        if (!d.exists() && !d.mkdirs()) return "busy"
        var dest = File(d, com.obliphur.oit.sync.SyncCrypto.safeName(o.optString("name")))
        var n = 1
        while (dest.exists()) {
            val base = o.optString("name")
            val stem = base.substringBeforeLast('.', base); val ext = base.substringAfterLast('.', "")
            dest = File(d, com.obliphur.oit.sync.SyncCrypto.safeName(stem + "-" + n + (if (ext.isEmpty()) "" else ".$ext"))); n++
        }
        val sync = app.sync ?: return "busy"
        Thread {
            runCatching { sync.fetchFile(peerId, o, dest) }
                .onFailure { core.logRecord("recv $cid failed: ${it.message}") }
                .onSuccess { core.logRecord("recv $cid segment ${dest.name} from $who (kept; merge on a PC)") }
        }.apply { isDaemon = true }.start()
        return ""
    }

    companion object { const val FOCUS_EVERY_MS = 10_000L }
}
