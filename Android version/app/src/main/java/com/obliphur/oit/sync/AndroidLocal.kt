package com.obliphur.oit.sync

import com.obliphur.oit.OitApp
import com.obliphur.oit.core.Fmt
import com.obliphur.oit.core.Kind
import com.obliphur.oit.core.NoticeButton
import com.obliphur.oit.core.Pending
import com.obliphur.oit.core.Schema
import com.obliphur.oit.core.TState
import com.obliphur.oit.core.iso
import com.obliphur.oit.core.parseIso
import com.obliphur.oit.core.tr
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDateTime

/** 设备同步: 本机数据 <-> 同步的文档 / 账 / 状态 (同桌面版 sync_local.py). 都在界面线程里调用. */
class AndroidLocal(private val app: OitApp) : SyncLocal {
    private val core get() = app.core

    /** 手机上两种断网方式都是代理 (全屏蔽 / 规则过滤), 局域网都保留, 所以都可以同步. */
    override fun netMode() = "proxy"
    override fun autoText() = core.settings.str("AUTO_TEXT")
    override fun changed() = core.changed()
    override fun appVersion(): String = com.obliphur.oit.BuildConfig.VERSION_NAME

    // ---- 开发者模式: 子端跟着母端 ----
    override fun devMode() = core.settings.devMode
    override fun setDevMode(on: Boolean) {
        if (core.settings.devMode != on) core.setDevMode(on)
    }

    // ---- 其他设备直接发来的安装包 (MECHANICS 16) ----
    override fun updateWanted(platform: String, version: String): String {
        if (platform != "android") return "platform"
        val v = com.obliphur.oit.service.Updater.parseVersion(version) ?: return "not-newer"
        return if (com.obliphur.oit.service.Updater.newer(v, app.updater.current)) "" else "not-newer"
    }

    override fun updateDest(version: String): java.io.File {
        val dir = java.io.File(app.cacheDir, "updates").apply { mkdirs() }
        dir.listFiles()?.forEach { it.delete() }
        return java.io.File(dir, "OIT-$version.apk")
    }

    override fun updateReceived(file: java.io.File, version: String, from: String) {
        app.updater.acceptFile(file, com.obliphur.oit.service.Updater.parseVersion(version))
        core.notice(tr("⬆  新版本已下载"), tr("{name} 发来了新版本 {v}.", "name" to from, "v" to version), listOf(
            NoticeButton(tr("安装"), "install", true), NoticeButton(tr("稍后"), "ok", false))) {
            if (it == "install") app.updater.install()
        }
        core.changed()
    }

    override fun updateFailed(error: String) = core.toast(tr("接收新版本失败: {e}", "e" to error), "warn")

    // ---- 账 ----

    override fun ledger(): JSONObject {
        val n = core.net
        val dm = core.dm
        val net = JSONObject().put("date", n.date).put("used", Math.round(n.used * 10) / 10.0).put("bonus", n.bonus)
            .put("tomorrow_used", n.tomorrowUsed).put("debt", n.debt)
        val focus = JSONArray(dm.focus.map { it.json() })
        val window = JSONObject()
        for ((d, day) in dm.windowDays) window.put(d, JSONObject(day.mapValues { Math.round(it.value * 10) / 10.0 }))
        // 电脑那边只认得包名: 顺带发应用名过去
        val labels = JSONObject()
        for (day in dm.windowDays.values) for (pkg in day.keys) if (pkg.contains('.') && !labels.has(pkg))
            labels.put(pkg, com.obliphur.oit.service.appLabel(app, pkg))
        return JSONObject().put("net", net).put("study", JSONObject(dm.study as Map<*, *>)).put("focus", focus)
            .put("window_runs", dm.runsJson()).put("labels", labels)
            .put("usage", JSONArray(n.usage.map { JSONObject(it.toString()) })).put("window", window)
    }

    override fun clearHistory() {
        core.dm.clearHistoryForSync()
        core.net.clearUsageForSync()
    }

    // ---- 状态 ----

    override fun status(): JSONObject {
        val t = core.timer
        val n = core.net
        val st = JSONObject().put("timer", "idle").put("net", n.mode).put("last_op", core.lastOp)
        when (t.state) {
            TState.RUNNING -> st.put("timer", "running").put("since", t.startWall?.iso())
            TState.ON_BREAK -> st.put("timer", "break").put("left", t.countdown.toInt())
            else -> {}
        }
        if (n.mode == "online") st.put("net_left", n.grantLeft.toInt())
        return st
    }

    // ---- 文档 ----

    private fun norm(key: String, v: Any?): Any? {
        val spec = Schema.BY_KEY[key] ?: return v
        return if (spec.kind == Kind.NUM) (v as? Number)?.toDouble() ?: v.toString().toDoubleOrNull()
        else v?.toString() ?: ""
    }

    override fun docs(): Map<String, Map<String, Any?>> {
        val s = core.settings
        val settings = LinkedHashMap<String, Any?>()
        for (spec in Schema.ALL) {
            if (spec.key in SyncService.LOCAL_SETTINGS) continue
            val p = s.pending[spec.key]
            settings[spec.key] = JSONObject().put("value", norm(spec.key, s.raw(spec.key)))
                .put("pending", if (p == null) JSONObject.NULL else JSONObject().put("value", norm(spec.key, p.value))
                    .put("requested_at", p.requestedAt).put("apply_at", p.applyAt.iso()))
        }
        settings["_setup_until"] = s.setupUntil?.iso()
        settings["_free_next"] = JSONArray(s.freeNext.sorted())

        // 规则页的名单不同步 (每台设备自己管, 见 MECHANICS 15.2)
        val hours = LinkedHashMap<String, Any?>()
        for ((ds, day) in core.dm.hourDays) for ((h, t) in day) if (t.isNotEmpty()) hours["$ds/$h"] = t

        val (week, used) = core.timer.bonusState()
        val timer = mapOf<String, Any?>("break_carry" to core.timer.breakCarry,
            "bonus" to JSONObject().put("week", week).put("used", used),
            // 此刻的专注 / 休息: 哪台设备最后改动就以哪台为准
            "live" to core.timer.liveState())
        return mapOf("settings" to settings, "hours" to hours, "timer" to timer)
    }

    override fun apply(doc: String, changes: Map<String, Any?>) {
        when (doc) {
            "settings" -> {
                val s = core.settings
                val applied = mutableListOf<String>()
                for ((key, v) in changes) {
                    when (key) {
                        "_setup_until" -> s.syncSetSetup(parseIso(v?.toString()))
                        "_free_next" -> {
                            s.freeNext.clear()
                            (v as? JSONArray)?.let { a -> (0 until a.length()).map { a.optString(it) }.filter { it in Schema.BY_KEY }.forEach { s.freeNext.add(it) } }
                        }
                        else -> {
                            val spec = Schema.BY_KEY[key] ?: continue
                            if (key in SyncService.LOCAL_SETTINGS || v !is JSONObject) continue
                            s.coerce(key, v.opt("value"))?.let { s.effective[key] = it }
                            val p = v.optJSONObject("pending")
                            val at = parseIso(p?.optString("apply_at"))
                            val pv = p?.let { s.coerce(key, it.opt("value")) }
                            if (at != null && pv != null) s.pending[key] = Pending(pv, p.optString("requested_at"), at)
                            else s.pending.remove(key)
                            s.since[key] = LocalDateTime.now().format(Fmt.YMDHM)
                            applied.add(spec.key)
                        }
                    }
                }
                s.save()
                core.afterSettingsApplied(applied)
            }
            "hours" -> {
                val dm = core.dm
                for ((key, v) in changes) {
                    val ds = key.substringBefore('/')
                    val h = key.substringAfter('/', "").toIntOrNull() ?: continue
                    val day = dm.hourDays.getOrPut(ds) { HashMap() }
                    val text = v?.toString() ?: ""
                    if (text.isNotEmpty()) day[h.toString()] = text else day.remove(h.toString())
                }
                dm.saveHours()
            }
            "timer" -> {
                val b = changes["bonus"] as? JSONObject
                val carry = (changes["break_carry"] as? Number)?.toInt()
                core.timer.syncSet(carry, b?.optString("week"), b?.optInt("used"))
                (changes["live"] as? JSONObject)?.let { core.timer.adopt(it) }
            }
        }
        core.changed()
    }

    // ---- 绑定请求 ----

    override fun pairRequest(req: PairRequest) {
        val sync = app.sync ?: return
        val mother = if (req.mother == req.id) tr("对方") else tr("本机")
        var msg = tr("{name} ({platform}) 请求绑定.", "name" to req.name, "platform" to req.platform)
        msg += "\n\n" + tr("配对码: {code}", "code" to req.code)
        msg += "\n" + tr("母端: {who}", "who" to mother)
        if (req.mother == req.id) msg += "\n\n" + tr("本机的设置, 名单和记录将被对方的数据替换.")
        core.notice(tr("🔗  绑定请求"), msg, listOf(
            NoticeButton(tr("接受"), "accept", true), NoticeButton(tr("拒绝"), "reject", false)), page = "rules") { action ->
            if (action == "accept") sync.answerPairing(req.token, true)
            else if (action == "reject") sync.answerPairing(req.token, false)
        }
    }
}
