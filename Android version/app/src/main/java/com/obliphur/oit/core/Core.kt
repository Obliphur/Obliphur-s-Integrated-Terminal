package com.obliphur.oit.core

import java.io.File
import java.time.LocalDate
import java.time.LocalDateTime

data class Toast(val text: String, val kind: String, val long: Boolean)
data class NoticeButton(val label: String, val action: String, val primary: Boolean)

/** 只能手动关闭的提示窗. 关掉 (按任何按钮, 或 ✕ / 划掉通知) 时调用 onClose(按钮的 action, ✕ 是 null). */
class Notice(
    val id: Int, val title: String, val message: String, val buttons: List<NoticeButton>,
    val page: String?, val onClose: (String?) -> Unit, val tag: String = "",
)

/** 平台相关的部分 (Android 实现见 OitApp.kt). */
interface Host {
    fun toast(t: Toast)
    fun noticesChanged()
    fun changed()
    /** 按当前状态让 VPN 就位; force=true 是定期复查 (VPN 被关掉了就重新打开). */
    fun applyNet(force: Boolean)
    /** 放出整点提示音, 放完 (最多等 10 秒) 后调用 done. */
    fun playChime(done: () -> Unit)
    /** silent: 本机不是最后有人操作的设备, 只标出待填写, 不出声. */
    fun hourPrompt(slot: LocalDateTime, silent: Boolean)
    /** 当前前台应用的包名; "" 是空白 (桌面 / 系统界面 / 锁屏); null 是不计入 (屏幕关闭或没有权限). */
    fun foregroundApp(): String?
    /** 锁定前台的这个应用 (盖一层"已锁定"并回到桌面); why: blacklist-app / not-whitelisted. 每秒都可能调用. */
    fun lockApp(pkg: String, why: String)
    /** 前台已经不是被锁定的应用了: 撤掉那一层. */
    fun unlockApp()
    /** 永远不锁的应用 (设置, 电话, 权限确认 …); whitelist: 白名单模式下系统自带的应用也不锁. */
    fun lockExempt(pkg: String, whitelist: Boolean): Boolean
    fun openPage(page: String)
    fun scheduleWake(seconds: Double)
}

/** VPN 应该处于的状态 (见 MECHANICS 14.2). */
sealed class VpnPlan {
    /** 不断网 (开发者模式 / 重置中). */
    object None : VpnPlan()
    /** 全部流量进 VPN 后丢弃. keepLan: 局域网地址不进 VPN (设备同步要用). */
    data class BlockAll(val keepLan: Boolean = false) : VpnPlan()
    /** 只接管 DNS: filter=true 时按网站名单拦截域名, 否则全部放行. */
    data class Dns(val filter: Boolean, val rulesVersion: Long, val blockAll: Boolean = false) : VpnPlan()
}

class Core(val dir: File, val host: Host, val mono: () -> Double) {
    val settings = Settings(File(dir, "app_settings.json"))
    val firstRun = settings.fresh
    val rules: RuleStore
    val dm: DataStore
    val net: NetGuard
    val timer: TimerEngine
    private var setupWasActive: Boolean

    // ---- 设备同步 (MECHANICS 15) ----
    /** 本机最后一次有人操作的时刻 (毫秒). */
    @Volatile var lastOp = System.currentTimeMillis()
    var myId: () -> String = { "" }
    /** 这台设备最近 2 分钟内同步过吗. */
    var peerFresh: (String) -> Boolean = { false }
    /** 本机是不是最后有人操作的设备 (整点提示音只在它上面响). */
    var isLatestActive: () -> Boolean = { true }
    /** 绑定了其他设备吗. */
    var hasPeers: () -> Boolean = { false }
    /** 本机是子端 (开发者模式跟着母端, 自己不能开关). */
    var followsMother: () -> Boolean = { false }
    /** 母端最近 2 分钟内同步过吗. */
    var motherFresh: () -> Boolean = { true }
    /** [开始, 结束] (毫秒) 里有绑定的设备离线的时间段. */
    var offlineSpans: (Long, Long) -> List<Pair<Long, Long>> = { _, _ -> emptyList() }
    /** 每 5 秒: 记下设备离线的时间段. */
    var trackOffline: () -> Unit = {}
    /** 母端的开发者模式变了: 马上同步给子端. */
    var devChangedHook: () -> Unit = {}
    /** 绑定的设备里有没有哪台在这个时刻 (毫秒) 之后同步成功过. */
    var syncedSince: (Long) -> Boolean = { false }

    val notices = mutableListOf<Notice>()
    private var nextNoticeId = 1

    private var lastTick = mono()
    private var lastCheck = -1e9
    private var lastAppSample = mono()
    private var appSamples = 0
    private var nextHour = LocalDateTime.now().floorHour().plusHours(1)

    init {
        if (firstRun) settings.startSetup()
        setupWasActive = settings.inSetup()
        rules = RuleStore(File(dir, "net_rules.json")).also { it.instant = { settings.changesInstant() } }
        dm = DataStore(dir, { settings.num("FILL_WINDOW") }, { settings.str("AUTO_TEXT") })
        dm.hourEnabled = settings.bool("HOUR_LOG_ENABLED")
        dm.finalizeHours()
        net = NetGuard(dir, settings)
        timer = TimerEngine(this, dir, mono)
    }

    /** 界面和服务都就位后调用一次. */
    fun start() {
        net.backend = { force -> host.applyNet(force) }
        host.applyNet(true)               // 程序一启动就进入离线状态
        timer.recover()
        if (firstRun) showWelcome()
        changed()
    }

    // ---- 提示 ----

    fun toast(text: String, kind: String = "info", long: Boolean = false) = host.toast(Toast(text, kind, long))

    fun changed() = host.changed()

    fun notice(title: String, message: String, buttons: List<NoticeButton>, page: String? = null,
               tag: String = "", onClose: (String?) -> Unit = {}): Int {
        val n = Notice(nextNoticeId++, title, message, buttons, page, onClose, tag)
        notices.add(n)
        host.noticesChanged()
        return n.id
    }

    fun noticeOpen(id: Int) = notices.any { it.id == id }

    fun closeNotice(id: Int, action: String?) {
        val n = notices.firstOrNull { it.id == id } ?: return
        notices.remove(n)
        host.noticesChanged()
        n.onClose(action)
        changed()
    }

    fun dismissNotice(id: Int) {
        if (notices.removeAll { it.id == id }) host.noticesChanged()
    }

    private fun logNotice(text: String) = Store.append(File(dir, "settings_changes.log"), text)

    // ---- 定时 ----

    /** 大约每秒调用一次 (服务里的循环, 以及从睡眠中被闹钟叫醒时). */
    fun tick() {
        if (dm.resetting) return
        val m = mono()
        val dt = m - lastTick
        lastTick = m
        val now = LocalDateTime.now()
        if (now.toLocalDate().key() != dm.todayDate) onMidnight()

        when (net.tick(dt)) {
            "started" -> toast(tr("网络已开启."), "success")
            "ended" -> toast(tr("时间到,网络已断开."), "warn")
        }
        timer.tick()

        if (!now.isBefore(nextHour)) {
            val slot = nextHour.minusHours(1)
            nextHour = now.floorHour().plusHours(1)
            onHour(slot)
        }
        if (m - lastCheck >= 5) {
            lastCheck = m
            checkPendingSettings()
            timer.check()
            checkSetupPeriod()
            // 子端: 母端 2 分钟没同步过了 (关掉了 / 断开了), 开发者模式就关掉
            if (settings.devMode && followsMother() && !motherFresh()) setDevMode(false)
        }
        checkAppLock()
        trackOffline()                       // 每秒: 同步失败的设备马上算离线 (MECHANICS 8.4)
        if (m - lastAppSample >= Cfg.APP_FOCUS_EVERY) {
            lastAppSample = m
            sampleApp()
        }
        changed()
        host.scheduleWake(nextWake())
    }

    /** 下一次一定要醒来处理的时刻 (秒). */
    fun nextWake(): Double {
        val c = mutableListOf(secondsBetween(LocalDateTime.now(), nextHour) + 0.2)
        net.pending?.let { c.add((it.at - System.currentTimeMillis()) / 1000.0) }
        if (net.grantLeft > 0) c.add(net.grantLeft)
        timer.nextDeadline()?.let { c.add(it) }
        if (settings.inSetup()) c.add(settings.setupLeftSeconds().toDouble())
        settings.pending.keys.forEach { c.add(settings.pendingLeftSeconds(it).toDouble()) }
        rules.pending.forEach { c.add(secondsBetween(LocalDateTime.now(), it.applyAt)) }
        return maxOf(1.0, c.min())
    }

    private fun onMidnight() {
        val old = dm.todayDate
        dm.todayDate = LocalDate.now().key()
        if (old.take(7) != dm.todayDate.take(7)) dm.pruneStudy()
        // 不清 promptSlot: 昨天 23:00 那格现在显示在今天最上方, 仍可填写
        dm.finalizeHours()
        dm.saveHours()
    }

    private fun onHour(slot: LocalDateTime) {
        dm.finalizeHours()
        if (!dm.hourEnabled) return
        // 绑定了其他设备时, 只有最后有人操作的那台响 (其他设备只标出待填写)
        if (!isLatestActive()) {
            dm.promptSlot = slot
            host.hourPrompt(slot, silent = true)
            changed()
            return
        }
        host.playChime {
            if (!dm.hourEnabled) return@playChime
            dm.promptSlot = slot
            host.hourPrompt(slot, silent = false)
            changed()
        }
    }

    fun previewChime() = host.playChime {}

    // ---- 应用名单: 锁定前台应用 (MECHANICS 14.6, 对应桌面版 4.7) ----

    /** 此刻被锁定的应用. */
    var lockedApp: String? = null
        private set

    private fun checkAppLock() {
        // 和网络限制一样: 开发者模式 / 重置中 / 全屏蔽 (名单不起作用) 时不锁
        val active = !dm.resetting && !settings.devMode && settings.str("NET_MODE") == "proxy"
        val app = if (active) host.foregroundApp() else ""
        if (app == null) return                                  // 屏幕关闭 / 没有权限: 维持原样
        val mode = settings.str("FILTER_MODE")
        val why = if (app.isEmpty() || app == "OIT" || host.lockExempt(app, mode == RuleKeys.WHITELIST)) null
        else rules.snap.appAllowed(app, net.online, mode).takeIf { !it.first }?.second
        if (why == null) {
            if (lockedApp != null) { lockedApp = null; host.unlockApp() }
            return
        }
        if (lockedApp != app) {
            lockedApp = app
            Store.append(File(dir, "window_guard.log"), "LOCK     $why  $app")
        }
        host.lockApp(app, why)
    }

    private fun sampleApp() {
        if (!settings.bool("WINDOW_FOCUS_ENABLED")) return
        val app = host.foregroundApp() ?: return
        dm.addWindowTime(app, Cfg.APP_FOCUS_EVERY)
        appSamples += 1
        if (appSamples % 6 == 0) dm.saveWindows()     // 大约每分钟写一次文件
    }

    fun saveAll() {
        dm.saveWindows()
        timer.saveNow()
        net.save()
    }

    // ---- 设置 ----

    fun checkPendingSettings(): List<String> {
        val due = settings.promoteDue()
        val rdue = rules.promoteDue()
        if (rdue.isNotEmpty()) host.applyNet(false)
        if (due.isEmpty()) return due
        afterSettingsApplied(due)
        val labels = due.joinToString("\n") {
            "${tr(Schema.BY_KEY[it]!!.label)}: ${settings.display(it)}"
        }
        toast(tr("以下配置已生效:") + "\n" + labels, "success")
        logNotice("SETTINGS applied: ${due.joinToString(", ")}")
        return due
    }

    /** 设置项生效之后 (立即生效或冷静期到期). */
    fun afterSettingsApplied(keys: Collection<String>) {
        if ("HOUR_LOG_ENABLED" in keys) syncHourLog()
        if (keys.any { it == "NET_MODE" || it == "FILTER_MODE" }) {
            host.applyNet(false)
            if ("NET_MODE" in keys) net.log("MODE     switched to ${settings.str("NET_MODE")}")
        }
        changed()
    }

    fun stageSetting(key: String, value: Any?): Pair<StageResult, String> {
        // 开 / 关开发者模式: 不是真的修改, 不排队, 不写文件, 不记流水, 不提示
        if (isDevToggle(key, value)) {
            if (!followsMother()) {                  // 子端跟着母端, 自己不能开关
                setDevMode(!settings.devMode)
                devChangedHook()
            }
            return StageResult.NOCHANGE to ""
        }
        val r = settings.stage(key, value)
        if (r.first == StageResult.INSTANT) afterSettingsApplied(listOf(key))
        changed()
        return r
    }

    private fun syncHourLog() {
        val want = settings.bool("HOUR_LOG_ENABLED")
        if (want != dm.hourEnabled) dm.applyHourEnabled(want)
    }

    private fun showWelcome() {
        val msg = tr("联网要先申请,等过冷静期才连上,每天有额度.") + "\n\n" +
            tr("接下来{h}小时是配置期:所有修改立即生效.", "h" to Cfg.SETUP_HOURS) + "\n" +
            tr("之后每次修改都要等 48 小时.")
        notice(tr("👋  欢迎使用 OIT"), msg, listOf(
            NoticeButton(tr("去设置"), "settings", true), NoticeButton(tr("开始使用"), "ok", false)), tag = "welcome") {
            if (it == "settings") host.openPage("settings:basic")
        }
    }

    fun endSetupPeriod() {
        settings.endSetup()
        setupWasActive = false
        logNotice("SETUP period ended by user")
        toast(tr("配置期已结束."))
        changed()
    }

    private fun checkSetupPeriod() {
        val active = settings.inSetup()
        if (setupWasActive && !active) {
            setupWasActive = false
            logNotice("SETUP period ended (time is up)")
            notice(tr("⏳  配置期结束了"), tr("之后的修改都要等 48 小时."),
                listOf(NoticeButton(tr("知道了"), "ok", true)))
        }
    }

    fun setDevMode(enabled: Boolean) {
        settings.devMode = enabled
        net.devChanged()
        // 开发者模式: 关掉代理 (VPN), 应用也不再锁定, 可以自由使用
        host.applyNet(true)
        if (enabled && lockedApp != null) {
            lockedApp = null
            host.unlockApp()
        }
        checkPendingSettings()
        changed()
    }

    // ---- 联网 (Network 页的按钮) ----

    private fun readForm(minutesText: String, reason: String): Pair<Int, String>? {
        val m = minutesText.trim().toIntOrNull()
        if (m == null) {
            toast(tr("分钟数必须是整数."), "warn"); return null
        }
        if (reason.trim().isEmpty()) {
            toast(tr("请写一个申请理由."), "warn"); return null
        }
        return m to reason.trim()
    }

    /** 普通申请. 返回是否成功 (成功后界面清空理由). */
    fun netRequest(minutesText: String, reason: String): Boolean {
        val (m, r) = readForm(minutesText, reason) ?: return false
        var rate = 1.0
        // 休息中, 且申请时长不超过剩余休息时间 → 冷静期 ×BREAK_COOLDOWN_RATE
        if (timer.state == TState.ON_BREAK && m * 60 <= timer.countdown) rate = settings.num("BREAK_COOLDOWN_RATE")
        val err = net.request(m, r, cooldownRate = rate)
        if (err != null) { toast(err, "warn"); return false }
        changed()
        return true
    }

    /** 快速访问 / 紧急借贷的提交前检查; 返回 null 表示可以弹确认框. */
    fun netPrecheck(minutesText: String, reason: String, loan: Boolean): Int? {
        val (m, _) = readForm(minutesText, reason) ?: return null
        val err = if (loan) net.canBorrow(m.toDouble()) else net.canQuick(m.toDouble())
        if (err != null) { toast(err, "warn"); return null }
        return m
    }

    fun netQuick(minutes: Int, reason: String): Boolean {
        val err = net.request(minutes, reason.trim(), quick = true)
        if (err != null) { toast(err, "warn"); return false }
        changed()
        return true
    }

    fun netLoan(minutes: Int, reason: String): Boolean {
        val before = net.tomorrowUsedAll
        val err = net.borrow(minutes, reason.trim())
        if (err != null) { toast(err, "warn"); return false }
        toast(tr("今天额度 +{add}.", "add" to fmtHm(minutes * 60.0)), "success")
        checkHeavyLoan(before)
        changed()
        return true
    }

    fun netUpgrade(): Boolean {
        val err = net.convertToQuick()
        if (err != null) { toast(err, "warn"); return false }
        changed()
        return true
    }

    fun netCtrl() {
        if (net.mode == "pending") net.cancel() else if (net.mode == "online") net.endEarly()
        changed()
    }

    /** 借贷借掉明天额度的一半以上: 提醒增加休息时间, 并让下一次修改「每日联网额度」立即生效. */
    private fun checkHeavyLoan(before: Double) {
        val limit = net.tomorrowLimit
        if (limit <= 0) return
        val line = limit * Cfg.LOAN_WARN_SHARE
        val after = net.tomorrowUsedAll
        if (!(before < line && line <= after)) return
        if (settings.grantFreeChange("DAILY_LIMIT"))
            logNotice("DAILY_LIMIT next change free — loan took ${Math.round(after / limit * 100)}% of tomorrow's quota")
        var msg = tr("明天的联网额度已经借掉了 {pct} ({used} / {limit}).",
            "pct" to "${Math.round(after / limit * 100)}%", "used" to fmtHm(after), "limit" to fmtHm(limit))
        msg += "\n\n" + tr("也许是休息不够,可以调高「休息时间倍率」.")
        msg += "\n\n" + tr("下一次修改「每日联网额度」免等待.")
        notice(tr("💳  借得有点多了"), msg, listOf(
            NoticeButton(tr("去设置"), "settings", true), NoticeButton(tr("知道了"), "ok", false))) {
            if (it == "settings") host.openPage("settings:timer")
        }
    }

    // ---- 断网方式 ----

    /** 联网只看网站名单, 应用名单只管锁定应用 (checkAppLock). 局域网通道在每种计划里都保留 (MECHANICS 15). */
    fun vpnPlan(): VpnPlan {
        if (dm.resetting || net.dead || settings.devMode) return VpnPlan.None
        val online = net.online
        val snap = rules.snap
        // 代理-全屏蔽 (Proxy block-all) (设置里的 Adapter): 离线时接管 DNS, 所有域名一律拦截; 联网中全部放行
        if (settings.str("NET_MODE") == "adapter")
            return if (online) VpnPlan.Dns(false, 0) else VpnPlan.Dns(true, 0, blockAll = true)
        // 联网中两种名单模式都全部放行 (MECHANICS 4.1)
        if (online) return VpnPlan.Dns(false, 0)
        if (settings.str("FILTER_MODE") == RuleKeys.WHITELIST) {
            // 网站白名单为空 = 什么网站都不能上: 全部流量进 VPN (比按 DNS 拦更彻底)
            if (snap.lists[RuleKeys.WHITE_DOMAINS]!!.isEmpty()) return VpnPlan.BlockAll(keepLan = true)
            return VpnPlan.Dns(true, snap.version)
        }
        if (snap.lists[RuleKeys.BLACK_DOMAINS]!!.isEmpty()) return VpnPlan.Dns(false, 0)
        return VpnPlan.Dns(true, snap.version)
    }

    // ---- 重置 ----

    /** 删除全部数据. 之后由 Host 新建一个 Core, 就像第一次安装. */
    fun eraseEverything() {
        timer.abandon()
        notices.clear()
        dm.resetting = true
        net.release()
        host.applyNet(false)          // 计划是 None: 关掉 VPN, 恢复网络
        dm.eraseAll()
    }
}
