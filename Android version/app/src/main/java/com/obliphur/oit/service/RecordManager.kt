package com.obliphur.oit.service

import android.content.Intent
import android.os.SystemClock
import com.obliphur.oit.OitApp
import com.obliphur.oit.core.RecCrypto
import com.obliphur.oit.core.tr
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
    private var camSince = 0L                            // 这一段摄像头录像从什么时候开始 (每 30 分钟换一段)
    private var flushAt = 0L
    @Volatile private var flushing = false
    private val outboxFile get() = File(dataDir, "rec_outbox.json")

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
        // 只在专注期间录像 (设置 → 录像, 默认开启): 休息 / 空闲时停下 (不算违反), 再开始专注时另起一段, 最后拼接成一个视频
        if (core.settings.bool("REC_FOCUS_ONLY") && core.timer.state != com.obliphur.oit.core.TState.RUNNING) want.clear()

        needProjection = "screen" in want && app.recProjection == null && !recording("screen")
        needCamera = "camera" in want
        needAudio = want.isNotEmpty() && core.settings.bool("REC_MIC")
        needPerms = needCamera || needAudio

        val now = SystemClock.elapsedRealtime()
        // 摄像头每 30 分钟换一段 (同电脑): 一段不会太大 (文件通道一次最多 1 GB), 出事时也只丢最后一段
        if (recording("camera") && camSince > 0 && now - camSince >= SEGMENT_MS) {
            stop("camera")
            camSince = 0L
        }
        // 没发出去的片段 (主人当时不在线 / 还没封装完 / 传输失败): 每 30 秒再试一次
        if (now >= flushAt && !flushing && outboxFile.exists()) {
            flushAt = now + 30_000
            flushOutbox()
        }
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

    /** 屏幕又亮了 (MECHANICS 18.8): 熄屏可能把摄像头录像弄断了, 清掉重试冷却, 下一拍 (tick) 立刻接上. */
    fun onScreenOn() {
        retryAt.remove("camera")
    }
    private fun granted(p: String) = app.checkSelfPermission(p) == android.content.pm.PackageManager.PERMISSION_GRANTED

    private fun start(kind: String) {
        when (kind) {
            "camera" -> { RecordService.command(app, RecordService.ACT_CAMERA); camSince = SystemClock.elapsedRealtime() }
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
        // 手机不能合并 (没有 Media Foundation): 本机是主人时片段留在本机 (设置 → 录像 还看不到最终结果, 要电脑合并);
        // 不是主人时, 把本机录的各路片段自动发给主人 (在线的电脑) 合并 + 同步回来 (MECHANICS 18.8)
        if (owner.isEmpty() || owner == me) {
            core.logRecord("owner on phone: segments kept for $cid (merge on a PC)")
            return
        }
        // 记进待发送的清单 (存在数据目录里, 重开 OIT 也还在), 由 tick 每 30 秒试一次:
        // 等这个合约的片段都封装 (加密) 完, 主人是在线的电脑时把各路片段全部发出去, 全部发成功才划掉
        val box = readOutbox()
        if ((0 until box.length()).none { box.optJSONObject(it)?.optString("cid") == cid }) {
            box.put(JSONObject().put("cid", cid).put("owner", owner).put("at", System.currentTimeMillis()))
            com.obliphur.oit.core.Store.write(outboxFile, JSONObject().put("items", box))
        }
        flushAt = 0L
    }

    private fun readOutbox(): org.json.JSONArray =
        com.obliphur.oit.core.Store.readObj(outboxFile)?.optJSONArray("items") ?: org.json.JSONArray()

    /** 把待发送清单里的片段发给各自的合约主人 (后台线程). 片段在接收端按文件名覆盖, 重发不会重复. */
    private fun flushOutbox() {
        val sync = app.sync ?: return
        flushing = true
        Thread {
            try {
                val box = readOutbox()
                val left = org.json.JSONArray()
                val now = System.currentTimeMillis()
                for (i in 0 until box.length()) {
                    val e = box.optJSONObject(i) ?: continue
                    val cid = e.optString("cid"); val owner = e.optString("owner")
                    if (cid.isEmpty() || now - e.optLong("at") > OUTBOX_DAYS * 86_400_000L) continue   // 太久了: 放弃 (片段仍在本机)
                    if (!sendAll(sync, cid, owner)) left.put(e)
                }
                if (left.length() == 0) outboxFile.delete()
                else com.obliphur.oit.core.Store.write(outboxFile, JSONObject().put("items", left))
            } finally { flushing = false }
        }.apply { isDaemon = true }.start()
    }

    /** 这个合约的各路片段全部发给主人了吗 (还在写 / 封装的、主人不在线 / 不是电脑时返回 false, 等下次). */
    private fun sendAll(sync: com.obliphur.oit.sync.SyncService, cid: String, owner: String): Boolean {
        if (RecCrypto.KINDS.any { k -> RecCrypto.filesOf(dataDir, k, cid, cacheDir).any { it.partial } }) return false
        val p = sync.peers()[owner] ?: return true                     // 已经解绑了: 不用再发
        val online = sync.deviceStates().any { it.id == owner && it.online }
        if (!online || !p.optString("platform").lowercase().startsWith("win")) return false   // 主人必须是在线的电脑
        var all = true
        for (kind in RecCrypto.KINDS) {
            val segs = RecCrypto.filesOf(dataDir, kind, cid).filter { !it.partial }
            var ok = 0
            for (s in segs) {
                val r = runCatching { sync.sendFile(owner, s.file, cid, kind) }
                    .onFailure { core.logRecord("send $cid/$kind failed: ${it.message}") }.getOrNull()
                if (r == "sent") ok++ else if (r != null) core.logRecord("send $cid/$kind ${s.name}: $r")
            }
            if (ok < segs.size) all = false
            if (segs.isNotEmpty()) core.logRecord("sent $ok/${segs.size} $kind segments for $cid to $owner")
        }
        return all
    }

    /** 收到别的设备发来的录像 (MECHANICS 18.8): final 是合约主人发来的最终录像 (直接存进 merged 展示);
     *  否则是片段 (本机手机不能合并, 只收下留着). 返回 "" 表示收下. */
    fun recordingOffered(peerId: String, who: String, o: JSONObject): String {
        val cid = o.optString("cid")
        val kind = if (o.optString("kind") == "camera") "camera" else "screen"
        if (cid.isEmpty()) return "bad-request"
        val finalRec = o.optBoolean("final")
        val d = (if (finalRec) File(app.cacheDir, "recv") else RecCrypto.incomingDir(dataDir, cid, kind))
        if (!d.exists() && !d.mkdirs()) return "busy"
        var dest = File(d, com.obliphur.oit.sync.SyncCrypto.safeName(o.optString("name")))
        var n = 1
        // 片段的文件名 (开始时刻 + 合约 id) 是唯一的: 同名的就是同一段 (对方重发), 直接覆盖, 不能合并两次
        while (finalRec && dest.exists()) {
            val base = o.optString("name")
            val stem = base.substringBeforeLast('.', base); val ext = base.substringAfterLast('.', "")
            dest = File(d, com.obliphur.oit.sync.SyncCrypto.safeName(stem + "-" + n + (if (ext.isEmpty()) "" else ".$ext"))); n++
        }
        val sync = app.sync ?: return "busy"
        Thread {
            runCatching { sync.fetchFile(peerId, o, dest) }
                .onFailure { core.logRecord("recv $cid/$kind failed: ${it.message}") }
                .onSuccess {
                    if (finalRec) { RecCrypto.storeMerged(dataDir, cid, kind, dest); core.logRecord("got merged $cid/$kind from $who") }
                    else core.logRecord("recv $cid/$kind segment from $who (kept; merge on a PC)")
                    core.changed()
                }
        }.apply { isDaemon = true }.start()
        return ""
    }

    /** 别的设备删了某个录像 (MECHANICS 18.8): 本机也删掉对应的最终录像. */
    fun recordingDeleted(cid: String, kind: String) {
        if (RecCrypto.deleteMerged(dataDir, cid, kind)) { core.logRecord("deleted $cid/$kind (synced)"); core.changed() }
    }

    /** 终止整个录制过程 (MECHANICS 18.8): 任意一端都能终止 —— 正在录的每一路各算一次违反 (对应词条失效),
     *  停掉本机并通知所有设备也停 (违反随合约同步). 返回终止了几路 (0 表示没有在录的词条). */
    fun terminate(): Int {
        val c = core.contract
        val terms = listOf("camera" to "gaze", "screen" to "scar")
            .filter { it.first in c.records() && c.has(it.second) }
        if (terms.isEmpty()) return 0
        val cid = c.live.optString("id", "")
        for ((kind, term) in terms) { forceStop(kind); core.contractViolation(tr("终止了录像"), term) }
        recStop(cid)
        val sync = app.sync
        if (sync != null && sync.peers().isNotEmpty())
            Thread { sync.stopRecording(cid); sync.syncNow() }.apply { isDaemon = true }.start()
        return terms.size
    }

    /** 别的设备 (或本机) 终止了录制 (MECHANICS 18.8): 停掉本机正在录的各路, 这个合约不再自动重开 (违反随合约同步). */
    fun recStop(cid: String) {
        val cur = core.contract.live.optString("id", "")
        if (cid.isNotEmpty() && cur.isNotEmpty() && cid != cur) return
        for (kind in RecCrypto.KINDS) { stop(kind); skipCid[kind] = cidKey(cur) }
    }

    /** 用户在 设置 → 录像 删掉一个录像: 本机删, 并通知所有绑定设备也删 (MECHANICS 18.8). */
    fun deleteRecording(cid: String, kind: String) {
        RecCrypto.deleteMerged(dataDir, cid, kind)
        val sync = app.sync
        if (sync != null && sync.peers().isNotEmpty())
            Thread { sync.delRecording(cid, kind) }.apply { isDaemon = true }.start()
        core.changed()
    }


    companion object {
        const val FOCUS_EVERY_MS = 10_000L
        /** 摄像头每这么久换一段 (同电脑的 recorder.SEGMENT). */
        const val SEGMENT_MS = 30 * 60 * 1000L
        /** 没发出去的片段最多再试几天. */
        const val OUTBOX_DAYS = 7
    }
}
