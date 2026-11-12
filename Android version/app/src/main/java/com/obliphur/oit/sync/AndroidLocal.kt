package com.obliphur.oit.sync

import com.obliphur.oit.OitApp
import com.obliphur.oit.ai.Ai
import com.obliphur.oit.ai.AiConfig
import com.obliphur.oit.ai.Profile
import com.obliphur.oit.core.Fmt
import com.obliphur.oit.core.I18n
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
    private var personaCache: Map<String, String>? = null
    private var personaAt = 0L

    /** 人设文件 (每 2 分钟读一次; 中间一直用缓存, 免得每次 20 秒同步都读一遍). */
    private fun personas(): Map<String, String> {
        val now = android.os.SystemClock.elapsedRealtime()
        if (personaCache == null || now - personaAt >= 120_000L) {
            personaCache = runCatching { core.comm.personaDoc() }.getOrDefault(emptyMap())
            personaAt = now
        }
        return personaCache ?: emptyMap()
    }

    /** 手机上两种断网方式都是代理 (全屏蔽 / 规则过滤), 局域网都保留, 所以都可以同步. */
    override fun netMode() = "proxy"
    override fun autoText() = core.settings.str("AUTO_TEXT")
    override fun autoTextFor(key: String) = core.dm.autoTextForKey(key)
    /** 别的设备 (界面语言可能不同) 写的 Sleeping / 睡眠 之类的默认内容, 换成本机的语言. */
    override fun autoNorm(text: String) = I18n.autoNamesIn(text)
    override fun changed() = core.changed()
    override fun appVersion(): String = com.obliphur.oit.BuildConfig.VERSION_NAME

    /** 别的设备重置 / 卸载了, 请本机也清空数据: 回复对方之后再做 (MECHANICS 9).
     *  leave: 它解除了和本机的绑定 (15.1): 只重置本机, 不转告其他设备. */
    override fun wipeRequested(wipeId: String, sender: String, name: String, leave: Boolean) {
        android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
            app.resetAll(wipeId = wipeId, exclude = sender, by = name, leave = leave)
        }, 300)
    }

    /** 别的设备发现本机离线了, 请本机重新启动 OIT (MECHANICS 15.3): 回复对方之后再重启. */
    override fun restartRequested(sender: String, name: String) {
        android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({ app.restartOit(name) }, 300)
    }

    override fun restartSent(name: String, status: Int) = core.logSync("SYNC restart request to $name: HTTP $status")

    // ---- 开发者模式: 子端跟着母端 ----
    override fun devMode() = core.settings.devMode
    override fun setDevMode(on: Boolean) {
        if (core.settings.devMode != on) core.setDevMode(on)
    }

    // ---- 其他设备直接发来的安装包 (MECHANICS 16) ----
    override fun updateWanted(platform: String, version: String): String {
        if (platform != "android") return "platform"
        if (com.obliphur.oit.BuildConfig.DEV_BUILD) return "dev"        // 开发版不装发布版的安装包
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

    override fun updateRequested(peerId: String): String = if (app.relayUpdates(true, only = peerId)) "" else "busy"

    /** 文件通道 (MECHANICS 15.6): 手机直接收下, 存进 图片/OIT (图片) 或 下载/OIT (其他), 存好后提示. */
    override fun fileOffered(peerId: String, who: String, o: JSONObject): String {
        val s = app.sync ?: return "busy"
        val name = o.optString("name")
        core.toast(tr("正在接收 {name}…", "name" to name), "info")
        Thread {
            val tmp = java.io.File(java.io.File(app.cacheDir, "incoming").apply { mkdirs() }, o.optString("token"))
            val result = runCatching {
                s.fetchFile(peerId, o, tmp)
                com.obliphur.oit.service.SharedFiles.save(app, tmp, name)
            }
            tmp.delete()
            app.main {
                result.onSuccess { where ->
                    core.notice(tr("📁  收到文件"), tr("{who} 发来文件: {name} ({size})", "who" to who, "name" to name,
                        "size" to com.obliphur.oit.ui.fmtSize(o.optLong("size"))) + "\n" + tr("已保存: {path}", "path" to where),
                        listOf(NoticeButton(tr("知道了"), "ok", true)))
                }.onFailure { e ->
                    core.toast(tr("接收失败: {e}", "e" to (e.message ?: e.javaClass.simpleName)), "warn")
                }
                core.changed()
            }
        }.start()
        return ""
    }

    override fun fileDelivered(who: String, name: String) =
        core.toast(tr("{who} 已收到 {name}.", "who" to who, "name" to name), "success")

    /** 合约录像 (MECHANICS 18.8): 交给录像调度收下 (片段存进 incoming, 最终录像存进 merged 展示). */
    override fun recOffered(peerId: String, who: String, o: JSONObject): String =
        app.recordManager.recordingOffered(peerId, who, o)

    /** 录像 (MECHANICS 18.8): 别的设备删了某个录像, 本机也删掉. */
    override fun recDeleted(cid: String, kind: String) = app.recordManager.recordingDeleted(cid, kind)

    /** 录像 (MECHANICS 18.8): 别的设备终止了录制, 本机也停. */
    override fun recStop(cid: String) = app.recordManager.recStop(cid)

    // ---- 账 ----

    override fun ledger(): JSONObject {
        val n = core.net
        val dm = core.dm
        val net = JSONObject().put("date", n.date).put("used", Math.round(n.used * 10) / 10.0).put("bonus", n.bonus)
            .put("tomorrow_used", n.tomorrowUsed).put("debt", n.debt).put("gift", n.gift)
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
        core.behavior.clearHistory()
    }

    // ---- 状态 ----

    /** 本机刚从自由学习的外出回来, 还没同步上: (开始毫秒, 结束毫秒). 这段时间本机的记录在同步里有优先权 (MECHANICS 21.6). */
    override fun awayWindow(): Pair<Long, Long>? {
        val s = app.sync ?: return null
        return runCatching {
            if (s.peers().isEmpty()) null
            else core.behavior.returned(s.myId, { ms -> s.peers().values.any { it.optLong("last_ok") >= ms } })
        }.getOrNull()
    }

    override fun status(): JSONObject {
        val t = core.timer
        val n = core.net
        val st = JSONObject().put("timer", "idle").put("net", n.mode).put("last_op", core.lastOp)
        // 关机 (MECHANICS 15.4): 收到关机指令 / 电量很低时突然离线, 其他设备把本机算作"关机" (效果和在线一样)
        if (app.powerOff) st.put("off", true)
        st.put("auto", app.autostartOk())        // 开机自启动成功过 (这次开机): 关机的前提 (MECHANICS 14.8)
        app.battery()?.let { st.put("battery", it) }
        awayWindow()?.let { st.put("away", JSONArray().put(it.first).put(it.second)) }     // 刚从自由学习的外出回来: 这次同步有优先权
        when (t.state) {
            TState.RUNNING -> st.put("timer", "running").put("since", t.startWall?.iso())
            TState.ON_BREAK -> st.put("timer", "break").put("left", t.countdown.toInt())
            else -> {}
        }
        if (n.mode == "online") st.put("net_left", n.grantLeft.toInt())
        // 本机停在指令页上: 空闲词条要所有设备都离开才作废 (MECHANICS 22.7)
        if (core.prescript.onPage()) st.put("prescript", true)
        // 本机此刻在录的几路 (合约页同步显示, MECHANICS 18.8)
        val rec = com.obliphur.oit.core.RecCrypto.KINDS.filter { app.recordManager.isRecording(it) }
        if (rec.isNotEmpty()) st.put("rec", org.json.JSONArray(rec))
        return st
    }

    // ---- 文档 ----

    private fun norm(key: String, v: Any?): Any? {
        val spec = Schema.BY_KEY[key] ?: return v
        return if (spec.kind == Kind.NUM || spec.kind == Kind.TIME) (v as? Number)?.toDouble() ?: v.toString().toDoubleOrNull()
        else v?.toString() ?: ""
    }

    /** API 配置 (1.0.443): 最多 10 份命名配置 + 默认配置 + 每个 AI 用哪份. Key 在同步通道里由配对密钥封着传,
     *  落到对方设备后再用本机的 Keystore 加密保存; 空 Key 不往外发 (免得未配置的设备把另一台的 Key 冲掉). */
    private fun aiDoc(): Map<String, Any?> {
        val cfg = AiConfig(java.io.File(core.dir, "ai_config.json"))
        val out = LinkedHashMap<String, Any?>()
        out["provider"] = cfg.provider
        out["order"] = cfg.order.joinToString(",")
        for (id in cfg.order) {
            val p = cfg.profiles[id] ?: continue
            out["$id/preset"] = p.preset
            out["$id/family"] = p.family
            out["$id/base_url"] = p.baseUrl
            out["$id/model"] = p.model
            if (p.apiKey.isNotEmpty()) out["$id/api_key"] = p.apiKey
            out["names/$id"] = cfg.configName(id)
        }
        for ((persona, id) in cfg.assign) out["assign/$persona"] = id
        return out
    }

    override fun voiceFiles() = core.comm.voiceFiles()
    override fun voicePlayed(keys: List<String>) = core.comm.voicePlayed(keys)
    override fun voiceReceived(key: String, source: java.io.File) = core.comm.voiceReceived(key, source)
    override fun voiceTempFile(token: String) = java.io.File(java.io.File(app.cacheDir, "incoming").apply { mkdirs() }, "voice_$token.mp3")

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
        // 细化规范的两个半小时 (MECHANICS 18.4): "日期/小时/1" "日期/小时/2"
        for ((ds, day) in core.dm.hourHalves) for ((h, pair) in day) pair.forEachIndexed { i, t ->
            if (t.isNotEmpty()) hours["$ds/$h/${i + 1}"] = t
        }

        val (week, used) = core.timer.bonusState()
        val timer = mapOf<String, Any?>("break_carry" to core.timer.breakCarry, "auto_rest" to core.timer.autoRest,
            "bonus" to JSONObject().put("week", week).put("used", used).put("count", core.timer.bonusCount()),
            // 此刻的专注 / 休息: 哪台设备最后改动就以哪台为准
            "live" to core.timer.liveState(),
            // 联网状态 (申请中 / 联网中, MECHANICS 2.8): 同上
            "net" to core.net.live())
        // 成就: 哪台设备达成都算 (达成时刻取最早的), 见 MECHANICS 17
        // 合约: 进行中的合约以后改的为准, 结束了的合约合并 (MECHANICS 18)
        // 行为模式的外出: 每一项单独比较, 计时 / 统计跟着它走 (MECHANICS 21)
        return mapOf("settings" to settings, "hours" to hours, "timer" to timer, "achievements" to core.ach.doc(),
            "contract" to core.contract.doc(), "behavior" to core.behavior.doc(),
            "prescript" to core.prescript.syncDoc(), "comm" to core.comm.syncDoc(), "personas" to personas(),
            "ai" to aiDoc())
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
                    val parts = key.split("/")
                    if (parts.size == 3 && parts[1].toIntOrNull() != null && parts[2] in setOf("1", "2")) {
                        val day = dm.hourHalves.getOrPut(parts[0]) { HashMap() }
                        val pair = day[parts[1]]?.copyOf() ?: arrayOf("", "")
                        pair[parts[2].toInt() - 1] = v?.toString() ?: ""
                        if (pair.any { it.isNotEmpty() }) day[parts[1]] = pair else day.remove(parts[1])
                        continue
                    }
                    val ds = key.substringBefore('/')
                    val h = key.substringAfter('/', "").toIntOrNull() ?: continue
                    val day = dm.hourDays.getOrPut(ds) { HashMap() }
                    val text = I18n.autoNamesIn(v?.toString() ?: "")
                    if (text.isNotEmpty()) day[h.toString()] = text else day.remove(h.toString())
                }
                dm.saveHours()
            }
            "timer" -> {
                val b = changes["bonus"] as? JSONObject
                val carry = (changes["break_carry"] as? Number)?.toInt()
                core.timer.syncSet(carry, b?.optString("week"), b?.optInt("used"), b?.optInt("count"),
                    (changes["auto_rest"] as? Number)?.toInt())
                (changes["live"] as? JSONObject)?.let { core.timer.adopt(it) }
                (changes["net"] as? JSONObject)?.let { core.adoptNet(it) }
            }
            "achievements" -> core.mergeAchievements(changes)
            "contract" -> core.mergeContract(changes)
            "behavior" -> {
                core.behavior.merge(changes)
                core.behaviorTick()
            }
            "prescript" -> core.prescript.syncApply(changes)
            "ai" -> {
                val cfg = AiConfig(java.io.File(core.dir, "ai_config.json"))
                val items = LinkedHashMap(changes)
                val orderText = items.remove("order")?.toString() ?: ""
                val active = items.remove("provider")?.toString() ?: ""
                if ("order" in changes) {                    // 配置列表 (增删) 以这份为准, 再把每一项填进去
                    val ids = orderText.split(",").filter { it.isNotEmpty() }
                    for (id in ids) if (id !in cfg.profiles) {
                        cfg.profiles[id] = Profile("custom", "", "openai", "", "", "other", "custom")
                        cfg.names.putIfAbsent(id, id)
                    }
                    cfg.order.clear()
                    cfg.order.addAll(ids.filter { it in cfg.profiles }.take(Ai.MAX_CONFIGS))
                    for (id in cfg.profiles.keys.toList()) {
                        if (id !in cfg.order) { cfg.profiles.remove(id); cfg.names.remove(id) }
                    }
                    cfg.assign.entries.removeAll { it.value !in cfg.order }
                    if (cfg.order.isNotEmpty() && cfg.provider !in cfg.order) cfg.provider = cfg.order.first()
                }
                for ((key, v) in items) {
                    val text = v?.toString() ?: ""
                    if (key.startsWith("names/")) {
                        val id = key.removePrefix("names/")
                        if (id in cfg.profiles && text.isNotEmpty()) cfg.renameConfig(id, text)
                        continue
                    }
                    if (key.startsWith("assign/")) {
                        val persona = key.removePrefix("assign/")
                        if (persona.isNotEmpty()) {
                            if (text.isEmpty()) cfg.assign.remove(persona) else cfg.assignConfig(persona, text)
                        }
                        continue
                    }
                    val id = key.substringBefore('/')
                    val field = key.substringAfter('/', "")
                    val cur = cfg.profiles[id] ?: continue
                    val next = when (field) {
                        "preset" -> if (text in Ai.PROVIDERS) cur.copy(preset = text) else cur
                        "family" -> if (text in Ai.FAMILY_PROTOCOL) cur.copy(family = text, protocol = Ai.FAMILY_PROTOCOL[text]!!) else cur
                        // 空值不覆盖已有的: 没配置过的设备不会把另一台的型号 / Key 冲掉
                        "base_url" -> if (text.isEmpty() && cur.baseUrl.isNotEmpty()) cur else cur.copy(baseUrl = text)
                        "model" -> if (text.isEmpty() && cur.model.isNotEmpty()) cur else cur.copy(model = text)
                        "api_key" -> if (text.isEmpty() && cur.apiKey.isNotEmpty()) cur else cur.copy(apiKey = text)
                        else -> cur
                    }
                    if (next != cur) cfg.putProfile(id, next)
                }
                if (active in cfg.profiles) cfg.setActive(active)
                cfg.save()
            }
            "comm" -> core.comm.applyDoc(changes)
            "personas" -> {
                core.comm.applyPersonaDoc(changes)
                personaCache = null          // 刚并进来的内容: 下次读盘 (不会把旧内容又发回去)
                personaAt = 0L
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
            NoticeButton(tr("接受"), "accept", true), NoticeButton(tr("拒绝"), "reject", false)), page = "settings:net") { action ->
            if (action == "accept") sync.answerPairing(req.token, true)
            else if (action == "reject") sync.answerPairing(req.token, false)
        }
    }
}
