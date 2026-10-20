package com.obliphur.oit.core

import java.io.File
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import org.json.JSONArray
import org.json.JSONObject

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
    /** 新达成了成就 (界面上的提示由界面读 Core.ach.unseen 画出; 在后台时可以另外发通知). */
    fun achievementsUnlocked(ids: List<String>) {}
    /** 违反合约的警报 (res/raw/target_alarm.ogg). */
    fun playAlarm() {}
    /** 开着的应用 (后台的应用, 包名; 不含 OIT 自己 / 桌面 / 系统界面); 没有使用情况访问权限时 null. */
    fun openApps(): Set<String>? = null
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

/** 一周内第 5 次领"我累了"时弹窗的文字 (MECHANICS 6.5). */
const val TIRED_TEXT = "主管{name}你好.我是Obliphur.\n你的休息时间不够吗?你可能暂时还无法做到长时间专注 导致休息效率低下.\n或者说,你处于低谷期,没有什么精力长时间专注.\n没关系.到设置调整休息时间倍率吧,请不要直接卸载,哪怕RESET也好..."

class Core(val dir: File, val host: Host, val mono: () -> Double) {
    val settings = Settings(File(dir, "app_settings.json"))
    val firstRun = settings.fresh
    /** 睡觉时间的两个提示 (MECHANICS 19). */
    val daily = Daily(File(dir, "daily.json"), settings)
    val rules: RuleStore
    val dm: DataStore
    val net: NetGuard
    val timer: TimerEngine
    /** 成就 (MECHANICS 17). */
    val ach: AchievementStore
    /** 合约 (MECHANICS 18). */
    val contract: Contract
    /** 上次看到的合约 (id): 同步来的开始 / 结束也要让断网方式等跟着变. */
    private var contractSeen: Long? = null
    /** 支援短缺: 这次休息已经提醒过的 (还差 3 / 1 分钟). */
    val supplyWarned = mutableSetOf<String>()
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
    /** 此刻离线的设备: (名字, 最后一次有它消息的时刻, 毫秒). */
    var offlineDevices: () -> List<Pair<String, Long>> = { emptyList() }
    /** 绑定的设备里版本和本机不一样的: (名字, 版本, 是否比本机新). 不一样时不能开始合约. */
    var versionMismatch: () -> List<Triple<String, String, Boolean>> = { emptyList() }
    /** 解除全部绑定的计划到时间了就解除 (返回这次解除了没有). */
    var checkUnbind: () -> Boolean = { false }
    /** 本机这次打开程序 (开始同步) 的时刻 (毫秒). */
    var syncStartedMs: () -> Long = { System.currentTimeMillis() }
    /** 合约因为哪台设备离线而失败. */
    private var offlineName = ""
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
    private var lastOpenApps = -1e9
    /** 专注中后台应用数是不是已经超过上限; 上一次响警报的时刻 (超着时每 OPEN_APPS_REPEAT 秒再响一次). */
    private var appsOver = false
    private var appsAlarmAt = -1e9
    private var appSamples = 0
    private var nextHour = nextHalf(LocalDateTime.now())

    // 违规记录 (MECHANICS 8.1): 最近 15 条合约违规的提示文字 (新的在后); init 里要读, 所以先声明
    private val violationsFile = File(dir, "violations.json")
    private val violations = ArrayList<Pair<String, String>>()      // (时间, 提示文字)

    /** 下一个整点或半点 (半点只在合约的细化规范时响铃, MECHANICS 18.4). */
    private fun nextHalf(now: LocalDateTime): LocalDateTime =
        if (now.minute < 30) now.floorHour().plusMinutes(30) else now.floorHour().plusHours(1)

    init {
        if (firstRun) settings.startSetup()
        setupWasActive = settings.inSetup()
        rules = RuleStore(File(dir, "net_rules.json")).also { it.instant = { settings.changesInstant() } }
        dm = DataStore(dir, { settings.num("FILL_WINDOW") }, { settings.str("AUTO_TEXT") })
        dm.hourEnabled = settings.bool("HOUR_LOG_ENABLED")
        dm.finalizeHours()
        net = NetGuard(dir, settings)
        timer = TimerEngine(this, dir, mono)
        // 没有存档时 (新安装 / 旧版本升级上来) 估计第一次启动的时刻
        ach = AchievementStore(File(dir, "achievements.json")) {
            AchievementStore.guessFirstLaunch(dir, settings.setupUntil, dm.study.keys + dm.hourDays.keys)
        }
        ach.blocked = { dm.resetting }
        contract = Contract(File(dir, "contract.json"))
        contract.blocked = { dm.resetting }
        loadViolations()
    }

    /** 界面和服务都就位后调用一次. */
    fun start() {
        net.backend = { force -> host.applyNet(force) }
        host.applyNet(true)               // 程序一启动就进入离线状态
        timer.recover()
        // 还没有用户名时界面先请用户填写 (MECHANICS 1.1), 填好后再显示介绍窗口
        if (firstRun && username.isNotEmpty()) showWelcome()
        checkAchievements()
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

    // ---- 违规记录 (MECHANICS 8.1): 最近 15 条合约违规的提示文字 (新的在后) ----

    private fun loadViolations() {
        violations.clear()
        val arr = Store.readObj(violationsFile)?.optJSONArray("items") ?: return
        for (o in arr.objects()) {
            val text = o.optString("text").trim()
            if (text.isNotEmpty()) violations.add(o.optString("at") to text)
        }
        while (violations.size > Cfg.VIOLATION_KEEP) violations.removeAt(0)
    }

    /** 记一条违规 (text = 弹出的那段提示); 只留最近 15 条. */
    fun addViolation(text: String) {
        val t = text.trim()
        if (t.isEmpty() || dm.resetting) return
        violations.add(LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")) to t)
        while (violations.size > Cfg.VIOLATION_KEEP) violations.removeAt(0)
        val arr = JSONArray()
        for ((at, s) in violations) arr.put(JSONObject().put("at", at).put("text", s))
        Store.write(violationsFile, JSONObject().put("items", arr))
    }

    /** 最近的违规: 新的在前. */
    fun recentViolations(n: Int = Cfg.VIOLATION_KEEP): List<Pair<String, String>> =
        violations.takeLast(n.coerceAtLeast(0)).reversed()


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
        checkContract()

        if (!now.isBefore(nextHour)) {
            val at = nextHour
            nextHour = nextHalf(now)
            onHour(at)
        }
        if (m - lastCheck >= 5) {
            lastCheck = m
            checkPendingSettings()
            timer.check()
            checkSetupPeriod()
            if (runCatching { checkUnbind() }.getOrDefault(false)) {
                logNotice("SYNC unbind-all done")
                toast(tr("已解除和所有设备的绑定."))
            }
            checkAchievements()
            // 子端: 母端 2 分钟没同步过了 (关掉了 / 断开了), 开发者模式就关掉
            if (settings.devMode && followsMother() && !motherFresh()) setDevMode(false)
        }
        checkAppLock()
        trackOffline()                       // 每秒: 同步失败的设备马上算离线 (MECHANICS 8.4)
        if (m - lastAppSample >= Cfg.APP_FOCUS_EVERY) {
            lastAppSample = m
            sampleApp()
        }
        if (m - lastOpenApps >= Cfg.OPEN_APPS_EVERY) {
            lastOpenApps = m
            checkOpenApps()
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
        contract.endAt?.let { c.add(secondsBetween(LocalDateTime.now(), it)) }
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

    /** at: 刚到的整点 / 半点. */
    private fun onHour(at: LocalDateTime) {
        val slot: LocalDateTime
        val len: Int
        if (at.minute == 30) {
            val hour = at.minusMinutes(30)
            if (!dm.hourEnabled || !contract.fineHour(hour)) return
            slot = hour; len = 30                                    // 前半小时
        } else {
            val hour = at.minusHours(1)
            dm.finalizeHours()
            if (!dm.hourEnabled) return
            if (contract.fineHour(hour)) { slot = at.minusMinutes(30); len = 30 }   // 后半小时
            else { slot = hour; len = 60 }
        }
        // 绑定了其他设备时, 只有最后有人操作的那台响 (其他设备只标出待填写)
        if (!isLatestActive()) {
            dm.promptSlot = slot
            dm.promptLen = len
            host.hourPrompt(slot, silent = true)
            changed()
            return
        }
        host.playChime {
            if (!dm.hourEnabled) return@playChime
            dm.promptSlot = slot
            dm.promptLen = len
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
        // 和网络限制一样: 开发者模式 / 重置中不锁. 两种断网方式都锁 (全屏蔽时只剩应用名单起作用)
        val active = !dm.resetting && !settings.devMode
        val app = if (active) host.foregroundApp() else ""
        if (app == null) return                                  // 屏幕关闭 / 没有权限: 维持原样
        // 合约 · 乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒 (软规则):
        // 前台是这一类应用就算违反 (不锁; 一直开着只算一次). 音乐在休息中可以听, 休息结束时还开着就算一次
        for (kind in Contract.BANS) {
            if (contract.banTerm(kind).isEmpty()) continue
            val hit = app in Contract.banApps(kind) && !(kind == "music" && timer.state == TState.ON_BREAK)
            val id = contract.banCheck(kind, hit)
            if (id.isEmpty()) continue
            contractViolation(when (kind) {
                "music" -> tr("打开了音乐播放软件 {exe}", "exe" to app)
                "silent" -> tr("打开了社交 / AI 聊天软件 {exe}", "exe" to app)
                "game" -> tr("打开了游戏 {exe}", "exe" to app)
                else -> tr("打开了视频平台 {exe}", "exe" to app)
            }, id)
            if (!contract.active) break
        }
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

    /** 后台应用数 (电脑上的任务栏窗口数, MECHANICS 14.6): 只检测, 不关应用. 超过上限时响警报 (超着时每 2 分钟再响一次);
     *  合约的"通道阻塞"按它算违反 (MECHANICS 18). */
    private fun checkOpenApps() {
        if (dm.resetting) return
        val focusing = timer.state == TState.RUNNING
        val limitOn = settings.bool("WINDOW_LIMIT_ENABLED")
        val chan = contract.windowWatch().second      // 已经失效的按失效前的上限 (再次超过只响警报)
        if (!focusing) {
            appsOver = false
            contract.windowReset()          // 离开专注: 通道阻塞下次专注重新算
            return
        }
        val n = host.openApps()?.size ?: return
        if (chan > 0) {
            val id = contract.windowCheck(n)
            if (id.isNotEmpty()) contractViolation(tr("后台应用超过 {n} 个", "n" to chan), id)
        }
        val lim = settings.num("WINDOW_LIMIT").toInt()
        if (!limitOn || n <= lim) {
            appsOver = false; return
        }
        if (!appsOver || mono() - appsAlarmAt >= Cfg.OPEN_APPS_REPEAT) {
            appsAlarmAt = mono()
            host.playAlarm()
            toast(tr("后台开着 {n} 个应用, 超过上限 {x} 个.", "n" to n, "x" to lim), "warn")
            Store.append(File(dir, "window_guard.log"), "APPS     $n > $lim")
        }
        appsOver = true
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
        // 整点记录关着, 合约选了整点记录的词条: 这次合约中照样开启, 结束后关闭 (MECHANICS 18.4)
        val want = settings.bool("HOUR_LOG_ENABLED") || contract.hourForced()
        if (want != dm.hourEnabled) dm.applyHourEnabled(want)
    }

    // ---- 用户名 (MECHANICS 1.1) ----

    private val profileFile get() = File(dir, "profile.json")

    /** 第一次打开时填写的用户名; 还没填过时是空的. */
    val username: String get() = Store.readObj(profileFile)?.optString("username")?.trim() ?: ""

    fun setUsername(name: String) {
        val n = name.trim().take(Cfg.USERNAME_MAX)
        if (n.isEmpty() || dm.resetting) return
        Store.write(profileFile, JSONObject().put("username", n))
        logNotice("USERNAME set: $n")
        if (firstRun) showWelcome()
        changed()
    }

    /** 一周内第 5 次领"我累了" (MECHANICS 6.5): 劝去调休息时间倍率, 它的下一次修改免等待
     *  (已经有排队中的修改时, 那一项立即生效). */
    fun tiredNotice() {
        if (settings.makeDueNow("BREAK_TIME_SCALE")) {
            logNotice("BREAK_TIME_SCALE pending change applied now — 5 bonus breaks this week")
            checkPendingSettings()
        } else if (settings.grantFreeChange("BREAK_TIME_SCALE")) {
            logNotice("BREAK_TIME_SCALE next change free — 5 bonus breaks this week")
        }
        notice(tr("☕  来自 Obliphur"), tr(TIRED_TEXT, "name" to username), listOf(
            NoticeButton(tr("去设置"), "settings", true), NoticeButton(tr("知道了"), "ok", false))) {
            if (it == "settings") host.openPage("settings:timer")
        }
        changed()
    }

    /** 睡觉时间之后 3 小时内第一次暂停休息: 提醒去睡觉 (MECHANICS 19). */
    fun sleepNotice() {
        notice(tr("🌙  该睡觉了"), daily.sleepText(username),
            listOf(NoticeButton(tr("晚安"), "ok", true)))
        changed()
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
        checkAchievements()
        changed()
    }

    // ---- 成就 (MECHANICS 17) ----

    /** 每 5 秒 (以及启动时) 判定一遍. 新达成的显示在界面上方, 直到手动关掉 (界面读 ach.unseen). */
    fun checkAchievements() {
        if (dm.resetting) return
        val new = ach.check(AchFacts(LocalDateTime.now(), ach.first, settings.inSetup(), dm.studyByDay(), dm.hourDays,
            contract.history, timer.breakCarry.toDouble()))
        if (new.isNotEmpty()) achievementsUnlocked(new)
    }

    /** 别的设备达成的成就 (设备同步): 本机也算达成, 同样显示提示. */
    fun mergeAchievements(changes: Map<String, Any?>) {
        val new = ach.merge(changes)
        if (new.isNotEmpty()) achievementsUnlocked(new)
    }

    /** 黑雾 (MECHANICS 18.3) 时新达成的成就: 等合约结束后再提示. */
    private val heldAchievements = mutableListOf<String>()

    private fun achievementsUnlocked(ids: List<String>) {
        if (contract.fog()) heldAchievements.addAll(ids) else host.achievementsUnlocked(ids)
        changed()
    }

    fun closeAchievement(id: String) {
        ach.markSeen(id)
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
        // 合约的侵蚀: 冷静期 ×2 / ×3
        val err = net.request(m, r, cooldownRate = rate, cooldownMult = contract.cooldownFactor())
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
        contractQuick()
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
        contractQuick()
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

    // ---- 合约 (MECHANICS 18) ----

    /** 生效的断网方式: 合约的"全频道阻塞干扰"期间是代理-全屏蔽 (设置值 adapter), 不改设置, 不计冷却. */
    fun netMode(): String = if (contract.has("jam")) "adapter" else settings.str("NET_MODE")

    /** 一段专注换来的休息时间 (秒): F(t) × 休息时间倍率, 再按合约调整. */
    fun focusRest(minutes: Double, start: LocalDateTime, end: LocalDateTime): Double {
        val sec = maxOf(0.0, computeF(minutes)) * settings.num("BREAK_TIME_SCALE") * 60.0
        return if (contract.active && contract.startAt?.isAfter(end) == false) contract.bookRest(sec)
        else contract.bookRestAfter(sec, start)
    }

    private fun Long.toLocalDateTime(): LocalDateTime =
        LocalDateTime.ofInstant(java.time.Instant.ofEpochMilli(this), java.time.ZoneId.systemDefault())

    /** 自动的操作由状态的主人做; 主人不在线 (2 分钟没同步) 时本机来做. */
    private fun ownerAway(owner: String) = owner.isEmpty() || owner == myId() || !peerFresh(owner)

    /** 每秒: 到时间就完成, 空闲太久就失败, 联网用超了算违反 (由合约的主人做). */
    private fun checkContract() {
        val c = contract
        val seen = if (c.active) c.id else null
        if (seen != contractSeen) {
            val was = contractSeen
            contractSeen = seen
            host.applyNet(false)                 // 全频道阻塞干扰: 断网方式跟着变
            syncHourLog()
            if (!c.fog() && heldAchievements.isNotEmpty()) {
                host.achievementsUnlocked(heldAchievements.toList())
                heldAchievements.clear()
            }
            if (was != null && seen == null) c.last?.let {
                toast(tr("合约已结束: {r}", "r" to tr(contractResult(it.optString("result")))))
            }
        }
        if (!c.active) return
        contractHours()
        checkSupply()
        checkQuiet()
        // 绑定的设备离线超过 15 分钟: 合约失败 (看到它离线的设备都可以判定)
        if (c.active && hasPeers()) {
            val devices = offlineDevices().map { (n, ms) -> n to ms.toLocalDateTime() }
            c.offlineDue(devices, LocalDateTime.now(), syncStartedMs().toLocalDateTime())?.let { (name, at) ->
                offlineName = name
                endContract("offline", at); return
            }
        }
        if (!c.active || !ownerAway(c.owner)) return
        c.due(LocalDateTime.now(), timer.state == TState.IDLE)?.let { (why, at) ->
            endContract(why, at); return
        }
        c.netCheck(net.usedAll, net.bonusAll, net.remaining, net.date)?.let { (id, kind) ->
            contractViolation(if (kind == "loan") tr("使用了紧急借贷") else tr("联网超过了合约的额度"), id)
        }
        if (!c.active) return
        // 未知变量: 合约的每个小时开始时 (刚开始时也算) 随机抽一个词条, 3 分钟后生效, 那个小时结束时移除
        val now = LocalDateTime.now()
        val gone = c.expire(now)
        for (id in gone) {
            logNotice("CONTRACT unknown expired $id")
            toast(tr("未知变量: {name} 已移除", "name" to tr(Contract.BY_ID[id]!!.name)))
        }
        val picked = c.roll(now, Dice(), dm::unitText, dm.autoValue, dm.fillSeconds.toLong())
        val woke = c.wake(now)
        for ((kind, v) in picked) {
            logNotice("CONTRACT unknown -> $kind $v")
            randomEffect(kind, v)
        }
        for (id in woke) {
            logNotice("CONTRACT unknown on $id")
            toast(tr("未知变量: 启用了 {name}", "name" to tr(Contract.BY_ID[id]!!.name)), "warn", long = true)
        }
        if (picked.isNotEmpty() || woke.isNotEmpty() || gone.isNotEmpty()) {
            host.applyNet(false)                 // 选中全频道阻塞干扰: 断网方式跟着变
            syncHourLog()
        }
    }

    /** 未知变量的一次判定结果: 抽中的词条 (提示), 或者效果 (休息时间 ±10 分钟由本机改计时器). */
    private fun randomEffect(kind: String, v: String) {
        when (kind) {
            "term" -> contract.waiting[v]?.let {
                toast(tr("未知变量: 抽中了 {name}, {t} 生效", "name" to tr(Contract.BY_ID[v]!!.name),
                    "t" to it.first.format(Fmt.HM)), "warn", long = true)
            }
            "rest_cut", "rest_add" -> {
                val d = if (kind == "rest_add") Contract.RANDOM_REST else -Contract.RANDOM_REST
                timer.adjustBreak(d)
                toast(tr(if (d > 0) "未知变量: 休息时间 +{n} 分钟" else "未知变量: 休息时间 -{n} 分钟", "n" to Contract.RANDOM_REST / 60),
                    "warn", long = true)
            }
            "lock" -> if (v.isNotEmpty()) {
                val p = v.split("/")
                val t = "${p[0].substring(5)} %02d:%s".format(p[1].toInt(), if (p.size == 3 && p[2] == "2") "30" else "00")
                toast(tr("未知变量: 封锁了整点记录 {t} (不能再修改)", "t" to t), "warn", long = true)
            } else toast(tr("未知变量: 没有可以封锁的整点记录, 无事发生"))
            "double" -> toast(tr("未知变量: 这次判定作废, 改为判定两次"))
            "slow" -> toast(tr("未知变量: 下一次专注启用阻滞 (只减慢合约内的时间, 按 {p}% 增长)", "p" to Math.round(Contract.RANDOM_SLOW * 100)),
                "warn", long = true)
            else -> toast(tr("未知变量: 无事发生"))
        }
    }

    /** 静默协议: 专注中这台设备处于联网状态 (各设备看自己的). */
    private fun checkQuiet() {
        val id = contract.quietCheck(timer.state == TState.RUNNING && net.mode == "online")
        if (id.isNotEmpty()) contractViolation(tr("专注中处于联网状态"), id)
    }

    /** 未知变量选中的词条: 点击关闭, 算一次违反. */
    fun closeRandomTerm(id: String) {
        if (!contract.active || id !in contract.rand) return
        contractViolation(tr("关闭了未知变量选中的 {name}", "name" to tr(Contract.BY_ID[id]!!.name)), id, drop = true)
        host.applyNet(false)
        syncHourLog()
    }

    /** 整点记录 (MECHANICS 18.4): 细化规范的半小时补记; 合约中过了填写时限还没填写算一次违反 (由合约的主人判定). */
    private fun contractHours() {
        val c = contract
        if (!dm.hourEnabled) return
        val now = LocalDateTime.now()
        dm.finalizeHalves(c.hourUnits(now), now)
        if (!ownerAway(c.owner)) return
        for (key in c.hourMisses(now, dm.fillSeconds.toLong(), dm::unitText, dm.autoValue)) {
            val p = key.split("/")
            val h = p[1].toInt()
            val label = if (p.size == 3) "%02d:%s".format(h, if (p[2] == "1") "30" else "00")
                else "%02d:00".format((h + 1) % 24)
            contractViolation(tr("整点记录 {t} 超时未填写", "t" to label), "")
            if (!c.active) return
        }
    }

    /** 支援短缺: 休息用到合约开始前积攒的休息时间之前 3 / 1 分钟各提醒一次; 用超了算一次违反. */
    private fun checkSupply() {
        val c = contract
        if (timer.state != TState.ON_BREAK || c.supplyWatch().isEmpty() || c.id != timer.breakCid) return
        val left = timer.countdown
        val until = c.supplyUntil(left, timer.breakPre)
        for (sec in Contract.SUPPLY_WARN) {
            val key = "${c.id}/${Math.round(c.supplyLimit ?: 0.0)}/$sec"
            if (until != null && until > 0 && until <= sec && key !in supplyWarned) {
                supplyWarned.add(key)
                toast(tr("支援短缺: {n} 分钟后将超出可用的合约开始前休息时间", "n" to sec / 60), "warn", long = true)
            }
        }
        if (ownerAway(c.owner)) {
            val id = c.supplyCheck(left, timer.breakPre)
            if (id.isNotEmpty()) contractViolation(tr("用超了合约开始前积攒的休息时间"), id)
        }
    }

    /** 休息 / 联网中不能开始合约: 返回提示 (没有限制时 ""). */
    fun contractBlock(): String {
        if (timer.state == TState.ON_BREAK) return tr("请先结束休息再开始合约")
        if (net.mode == "online") return tr("请先结束联网再开始合约")
        val diff = versionMismatch().firstOrNull() ?: return ""
        return tr("{name} 的版本 ({v}) 和本机 ({me}) 不一样, 不能开始合约", "name" to diff.first, "v" to diff.second,
            "me" to com.obliphur.oit.BuildConfig.VERSION_NAME)
    }

    fun startContract(terms: Collection<String>) {
        if (contract.active) return
        contractBlock().let { if (it.isNotEmpty()) { toast(it, "warn"); return } }
        contract.start(terms, myId(), pre = timer.breakCarry.toDouble())
        // 凑齐的词条组合: 积攒的休息时间 +N 分钟 (在 pre 之后加: 算合约中得到的, 合约结束后不留下)
        val gift = Contract.restGift(contract.terms)
        timer.addCarry(gift)
        contractSeen = contract.id
        host.applyNet(false)
        syncHourLog()
        logNotice("CONTRACT start level ${contract.level} terms ${contract.terms.joinToString(",").ifEmpty { "-" }} until ${contract.endAt?.format(Fmt.YMDHM)}" +
            (if (gift > 0) " gift ${gift}s" else ""))
        toast(tr("合约开始.") + (if (gift > 0) "\n" + tr("积攒的休息时间 +{t}.", "t" to fmtMs(gift.toDouble())) else ""), "success")
        changed()
    }

    fun abandonContract() = endContract("abandoned")

    fun endContract(result: String, at: LocalDateTime? = null) {
        val c = contract
        if (!c.active) return
        timer.stopBreakForContract()      // 结束合约前先停止休息
        val pending = c.reward
        val rec = c.finish(result, at, myId()) ?: return
        contractSeen = null
        host.applyNet(false)
        syncHourLog()
        val reward = rec.optInt("reward", 0)
        // 合约中得到的休息时间不留下: 只留合约开始前积攒的那部分, 加上奖励
        timer.contractEnded(rec.optDouble("pre_left", 0.0), reward)
        checkAchievements()
        if (heldAchievements.isNotEmpty()) {
            host.achievementsUnlocked(heldAchievements.toList())
            heldAchievements.clear()
        }
        logNotice("CONTRACT $result level ${rec.optInt("level")} violations ${rec.optInt("violations")} reward ${reward}s")
        val lv = rec.optInt("level")
        when (result) {
            "completed" -> {
                var msg = tr("危机等级 {lv}", "lv" to lv)
                if (reward > 0) msg += "\n" + tr("积攒的休息时间 +{t}.", "t" to fmtMs(reward.toDouble()))
                notice(tr("⚔  合约完成"), msg, listOf(NoticeButton(tr("知道了"), "ok", true)), page = "contract")
            }
            "abandoned" -> toast(tr("⚔ 行动终止"))
            else -> {
                var msg = when (result) {
                    "failed" -> tr("目标生命值归零 (违反 {n} 次), 合约终止.", "n" to rec.optInt("violations"))
                    "offline" -> tr("{name} 离线超过 15 分钟, 合约终止.", "name" to offlineName.ifEmpty { "?" })
                    else -> tr("空闲超过 30 分钟, 合约终止.")
                }
                if (pending >= 1) msg += "\n" + tr("完成后的 {n} 分钟休息时间没有给出.", "n" to Math.round(pending / 60))
                notice(tr("⚔ 行动失败"), msg, listOf(NoticeButton(tr("知道了"), "ok", true)), page = "contract")
            }
        }
        changed()
    }

    /** 违反了词条 id (软规则): 响警报, 记一次, 这个词条降一级 (drop: 直接去掉); 到上限就结束合约. */
    fun contractViolation(why: String, id: String, drop: Boolean = false) {
        val c = contract
        if (c.active && !dm.resetting && c.echo(id)) {
            // 已经失效的词条再次触发: 照常响警报 (每个词条 2 分钟最多一次), 不扣目标生命值
            if (c.echoDue(id)) {
                host.playAlarm()
                logNotice("CONTRACT echo $id  $why")
                toast(tr("⚠ {why} · {name} 已失效, 不扣目标生命值", "why" to why, "name" to tr(Contract.BY_ID[id]!!.name)),
                    "warn", long = true)
            }
            return
        }
        if (!c.active || dm.resetting || (id.isNotEmpty() && !c.has(id))) return
        host.playAlarm()
        val old = Contract.BY_ID[id]?.let { tr(it.name) } ?: ""
        val (over, new) = c.violate(id, drop)
        val after = when {
            id.isEmpty() -> tr("违反一次")
            new.isEmpty() -> tr("{a} 失效", "a" to old)
            new == id -> tr("{a} 降一级", "a" to old)
            else -> tr("{a} 降为 {b}", "a" to old, "b" to tr(Contract.BY_ID[new]!!.name))
        }
        logNotice("CONTRACT violation ${c.violations}/${c.violationLimit}: ${id.ifEmpty { "-" }} -> ${new.ifEmpty { "-" }}  $why")
        val msg = tr("⚠ 违反合约: {why} · 目标生命值 {n} / {m}", "why" to why, "n" to c.life, "m" to c.violationLimit) +
            "\n" + after + "  ·  " + tr("危机等级 {lv}", "lv" to c.level)
        toast(msg, "warn", long = true)
        addViolation(msg)               // 违规记录页 (MECHANICS 8.1): 内容照搬这段提示
        if (over) endContract("failed")
        changed()
    }

    /** 抑制: 用了快速访问. */
    private fun contractQuick() {
        val id = contract.quickCheck()
        if (id.isNotEmpty()) contractViolation(tr("使用了快速访问"), id)
    }

    /** 同步来的合约. */
    fun mergeContract(changes: Map<String, Any?>) {
        contract.merge(changes)
        checkContract()
        changed()
    }

    // ---- 断网方式 ----

    /** 联网只看网站名单, 应用名单只管锁定应用 (checkAppLock). 局域网通道在每种计划里都保留 (MECHANICS 15). */
    fun vpnPlan(): VpnPlan {
        if (dm.resetting || net.dead) return VpnPlan.None
        // 开发者模式不断网. 但绑定了其他设备时仍保留一个全部放行的 VPN (只接管 DNS): 没有 VPN 时
        // vivo 等手机一离开 OIT 就把它整个冻结, 别的设备连过来等不到回复 (MECHANICS 15.3)
        if (settings.devMode) return if (hasPeers()) VpnPlan.Dns(false, 0) else VpnPlan.None
        val online = net.online
        val snap = rules.snap
        // 代理-全屏蔽 (Proxy block-all): 离线时接管 DNS, 所有域名一律拦截 (不看网站名单,
        // 局域网照常, 设备同步要用); 联网中全部放行. 应用名单照样按过滤名单模式锁定应用 (checkAppLock)
        if (netMode() == "adapter")
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
