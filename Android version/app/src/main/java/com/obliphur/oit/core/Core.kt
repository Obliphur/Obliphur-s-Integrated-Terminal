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
    /** 暂停后台正在播放的音频 / 视频 (指令 V 被领取时, MECHANICS 22.2). */
    fun pauseMedia() {}
}

/** VPN 应该处于的状态 (见 MECHANICS 14.2). */
sealed class VpnPlan {
    /** 不断网 (开发者模式 / 重置中). */
    object None : VpnPlan()
    /** 全部流量进 VPN 后丢弃. keepLan: 局域网地址不进 VPN (设备同步要用). */
    data class BlockAll(val keepLan: Boolean = false) : VpnPlan()
    /** 只接管 DNS: filter=true 时按网站名单拦截域名, 否则全部放行.
     *  blockApps 不为空时 (合约的无言为真, MECHANICS 18.6) 这些应用的网络全部屏蔽: 见 OitVpnService. */
    data class Dns(val filter: Boolean, val rulesVersion: Long, val blockAll: Boolean = false,
                   val blockApps: Set<String> = emptySet(), val blockDomains: Set<String> = emptySet()) : VpnPlan()
}

/** 一周内第 5 次领"我累了"时弹窗的文字 (MECHANICS 6.5). */
const val TIRED_TEXT = "主管{name}你好.我是Obliphur.\n你的休息时间不够吗?你可能暂时还无法做到长时间专注 导致休息效率低下.\n或者说,你处于低谷期,没有什么精力长时间专注.\n没关系.到设置调整休息时间倍率吧,请不要直接卸载,哪怕RESET也好..."

class Core(val dir: File, val host: Host, val mono: () -> Double) {
    val settings = Settings(File(dir, "app_settings.json"))
    val firstRun = settings.fresh
    /** 睡觉时间的两个提示 (MECHANICS 19). */
    val daily = Daily(File(dir, "daily.json"), settings)
    /** 行为模式 / 外出 (MECHANICS 21). */
    val behavior = Behavior(File(dir, "behavior.json"), settings)
    val rules: RuleStore
    val dm: DataStore
    val net: NetGuard
    val timer: TimerEngine
    /** 成就 (MECHANICS 17). */
    val ach: AchievementStore
    /** 合约 (MECHANICS 18). */
    val contract: Contract
    /** 指令 (Prescript, 都市之意主题独占, MECHANICS 22). */
    val prescript: PrescriptEngine
    /** 界面当前页 (指令的 V / VI 要看) 和界面是不是在前台 (退到后台相当于电脑版的最小化). */
    @Volatile var currentPage = "timer"
    @Volatile var uiForeground = true
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
    /** 冷静期到了的解除绑定就解除, 并请那台设备清空全部数据 (返回这次解除了的设备的名字, MECHANICS 15.1). */
    var checkUnbind: () -> List<String> = { emptyList() }
    /** 此刻离线的绑定的设备: id → 名字. */
    var offlinePeers: () -> Map<String, String> = { emptyMap() }
    /** 上一次看到的离线设备 (null = 还没看过: 刚打开时已经离线的不响). */
    private var offlineSeen: Set<String>? = null
    /** 本机这次打开程序 (开始同步) 的时刻 (毫秒). */
    var syncStartedMs: () -> Long = { System.currentTimeMillis() }
    /** 合约因为哪台设备离线而失败. */
    private var offlineName = ""
    /** [开始, 结束] (毫秒) 里有绑定的设备离线的时间段. */
    var offlineSpans: (Long, Long) -> List<Pair<Long, Long>> = { _, _ -> emptyList() }
    /** 本机关机的那几段 (15.4): 本机这段的数据不计入专注计时. */
    var selfOffSpans: (Long, Long) -> List<Pair<Long, Long>> = { _, _ -> emptyList() }
    /** 每 5 秒: 记下设备离线的时间段. */
    var trackOffline: () -> Unit = {}
    /** 母端的开发者模式变了: 马上同步给子端. */
    var devChangedHook: () -> Unit = {}
    /** 绑定的设备里有没有哪台在这个时刻 (毫秒) 之后同步成功过. */
    var syncedSince: (Long) -> Boolean = { false }
    /** 本机这次停下来算不算关机: 收到了关机指令, 或者电量很低 (MECHANICS 15.4). */
    var wentOff: () -> Boolean = { false }
    /** 其他设备从 Adapter 断网回来了: (设备 id, 开始毫秒, 结束毫秒) (MECHANICS 15.5). */
    var popAdapterBack: () -> List<Triple<String, Long, Long>> = { emptyList() }
    /** 本机关机的那段 (MECHANICS 15.4): 记下来, 之后记下的专注里这段记为空闲, 不计入统计. */
    var addSelfOff: (Long, Long) -> Unit = { _, _ -> }
    private var powerAt = -1e9
    private var conflictNotice = -1

    val notices = mutableListOf<Notice>()
    private var nextNoticeId = 1

    private var lastTick = mono()
    private var lastCheck = -1e9
    private var lastAppSample = mono()
    private var lastOpenApps = -1e9
    /** 专注中后台应用数是不是已经超过上限; 上一次响警报的时刻 (超着时每 OPEN_APPS_REPEAT 秒再响一次). */
    private var appsOver = false
    /** 每个后台应用第一次被看到的时刻 (mono): 开着超过 2 分钟才计入后台应用数. */
    private val appSeen = HashMap<String, Double>()
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
        if (firstRun) {
            settings.startSetup()
            // 第一次打开时可以设置行为模式 (这一次立即生效); 之后再改要等 7 天 (MECHANICS 21)
            settings.grantFreeChange("BEHAVIOR_MODE")
        }
        setupWasActive = settings.inSetup()
        rules = RuleStore(File(dir, "net_rules.json")).also { it.instant = { settings.changesInstant() } }
        dm = DataStore(dir, { settings.num("FILL_WINDOW") }, { settings.str("AUTO_TEXT") })
        dm.extraStudy = { day -> behavior.studyHours(LocalDate.parse(day)) }
        dm.extraDays = { behavior.days() }
        dm.extraFocus = { day -> behavior.focusSegments(day) }
        dm.hourAutoText = { hour -> behavior.hourAutoText(hour) }
        dm.hourEnabled = settings.bool("HOUR_LOG_ENABLED")
        dm.finalizeHours()
        net = NetGuard(dir, settings)
        net.quotaFactor = { day -> behavior.quotaFactor(day) }
        net.cooldownFactor = { at -> behavior.cooldownFactor(at) }
        timer = TimerEngine(this, dir, mono)
        // 没有存档时 (新安装 / 旧版本升级上来) 估计第一次启动的时刻
        ach = AchievementStore(File(dir, "achievements.json")) {
            AchievementStore.guessFirstLaunch(dir, settings.setupUntil, dm.study.keys + dm.hourDays.keys)
        }
        ach.blocked = { dm.resetting }
        contract = Contract(File(dir, "contract.json"))
        contract.blocked = { dm.resetting }
        prescript = PrescriptEngine(
            File(dir, "prescript.json"),
            stateFn = { when (timer.state) { TState.RUNNING -> "focus"; TState.ON_BREAK -> "break"; else -> "idle" } },
            todayHoursFn = { todayHoursLive() },
            contractsFn = { contract.history.toList() },
            pageFn = { currentPage },
            minimizedFn = { !uiForeground },
            grantRestFn = { seconds, code ->
                logNotice("PRESCRIPT +${seconds}s $code".trimEnd())
                timer.grantRest(seconds)
            },
            blockedFn = { dm.resetting },
            devFn = { settings.devMode },
            pauseMediaFn = { host.pauseMedia() },
            contractActiveFn = { contract.active },
            remainingRestFn = {
                if (timer.state == TState.ON_BREAK) timer.countdown else timer.breakCarry.toDouble()
            },
            onChange = { changed() },
        )
        loadViolations()
    }

    /** 今天的专注小时数, 包括正在进行、还没记进统计的这一段. */
    private fun todayHoursLive(): Double {
        var hours = dm.todayHours()
        if (timer.state == TState.RUNNING) {
            val sinceMidnight = secondsBetween(LocalDate.now().atStartOfDay(), LocalDateTime.now())
            hours += minOf(timer.elapsed, sinceMidnight) / 3600.0
        }
        return hours
    }

    /** 界面和服务都就位后调用一次. */
    fun start() {
        net.backend = { force -> host.applyNet(force) }
        // 联网状态随设备同步 (MECHANICS 2.8): 同一次联网由发起的设备扣额度, 它不在线时由本机扣
        net.myId = { myId() }
        net.chargeOk = { ownerAway(it) }
        host.applyNet(true)               // 程序一启动先按存下的联网状态 (多半是离线) 设好
        notePowerBack()
        timer.recover()
        behaviorTick()
        // 还没有用户名时界面先请用户填写 (MECHANICS 1.1), 填好后再显示介绍窗口
        if (firstRun && username.isNotEmpty()) showWelcome()
        checkAchievements()
        changed()
    }

    // ---- 提示 ----

    // 黑雾: 遮住除违规记录 (error) 外的所有提示, 显示为 [???] (MECHANICS 18.3); clear: 词条自己的提示 (支援短缺, 未知变量) 不遮
    fun toast(text: String, kind: String = "info", long: Boolean = false, clear: Boolean = false) =
        host.toast(Toast(if (kind != "error" && !clear && contract.fog()) "[???]" else text, kind, long))

    fun changed() = host.changed()

    fun notice(title: String, message: String, buttons: List<NoticeButton>, page: String? = null,
               tag: String = "", onClose: (String?) -> Unit = {}): Int {
        // 黑雾: 只能手动关闭的提醒 (也是系统通知) 一样遮住内容, 例如专注记了多少分钟; 合约冲突要靠内容选, 不遮;
        // "还在吗"自己在黑雾时不写专注了多久, 不遮 (要看到最晚关闭的时刻)
        val shown = if (contract.fog() && tag != "conflict" && tag != "check") "[???]" else message
        val n = Notice(nextNoticeId++, title, shown, buttons, page, onClose, tag)
        notices.add(n)
        host.noticesChanged()
        return n.id
    }

    fun noticeOpen(id: Int) = notices.any { it.id == id }

    // 关机的时间段 (MECHANICS 15.4): 每 30 秒写一次 power.json (此刻, 算不算关机); 重新打开时据此知道关了多久
    private val powerFile get() = File(dir, "power.json")

    fun beatPower(force: Boolean = false) {
        val m = mono()
        if (!force && m - powerAt < 30) return
        powerAt = m
        Store.write(powerFile, JSONObject().put("alive", System.currentTimeMillis())
            .put("off", runCatching { wentOff() }.getOrDefault(false)))
    }

    private fun notePowerBack() {
        val d = Store.readObj(powerFile)
        val alive = d?.optLong("alive", 0L) ?: 0L
        val now = System.currentTimeMillis()
        if (d != null && d.optBoolean("off") && alive > 0 && now - alive > Cfg.CLOSED_GAP * 1000) {
            addSelfOff(alive, now)
            logNotice("POWER    was off since ${java.time.Instant.ofEpochMilli(alive)}")
        }
        beatPower(force = true)
    }

    /** 别的设备断网回来: 断网期间重叠的记录按 休息 > 专注 > 空闲 处理 (只改本机的, MECHANICS 15.5). */
    private fun checkAdapterBack() {
        val z = java.time.ZoneId.systemDefault()
        var s = 0.0; var b = 0.0; var i = 0.0
        for ((did, a, e) in popAdapterBack()) {
            val led = dm.peersById()[did] ?: continue
            val recs = led.optJSONArray("focus")?.objects() ?: emptyList()
            val r = dm.resolveOverlap(recs, LocalDateTime.ofInstant(java.time.Instant.ofEpochMilli(a), z),
                LocalDateTime.ofInstant(java.time.Instant.ofEpochMilli(e), z), tieMine = false)
            s += r["study"] ?: 0.0; b += r["break"] ?: 0.0; i += r["idle"] ?: 0.0
        }
        if (s + b + i > 0) {
            toast(tr("断网期间和其他设备重叠的记录已按 休息 > 专注 > 空闲 处理: 去掉了专注 {s}, 休息 {b}, 空闲 {i}.",
                "s" to fmtHm(s), "b" to fmtHm(b), "i" to fmtHm(i)), "info", long = true)
            changed()
        }
    }

    /** 时间重叠的两个合约 (MECHANICS 15.5): 问用户留哪个; 在别的设备上选过了就撤掉提示. */
    private fun checkContractConflicts() {
        val c = contract
        if (c.conflicts.isEmpty()) {
            if (conflictNotice >= 0 && noticeOpen(conflictNotice)) dismissNotice(conflictNotice)
            conflictNotice = -1
            return
        }
        if (conflictNotice >= 0 && noticeOpen(conflictNotice)) return
        val (a, b) = c.conflicts.first()
        fun line(x: org.json.JSONObject): String {
            val s0 = parseIso(x.optString("start")); val s1 = parseIso(x.optString("end"))
            val whenText = if (c.fog()) "??:?? — ??:??"           // 黑雾: 不显示合约的开始 / 结束时刻
                else if (s0 != null && s1 != null) "${s0.format(Fmt.MDHM)} — ${s1.format(Fmt.HM)}" else "?"
            val state = if (x.optString("result").isEmpty()) tr("进行中") else tr(contractResult(x.optString("result")))
            return tr("{when} · 危机等级 {lv} · {state}", "when" to whenText, "lv" to x.optInt("level"), "state" to state)
        }
        val msg = tr("断网期间两台设备各自进行了合约, 时间有重叠. 只能保留一个, 另一个作废 (已经给出的休息时间不收回).") +
            "\n\nA  " + line(a) + "\n\nB  " + line(b)
        conflictNotice = notice(tr("⚔ 合约冲突"), msg, listOf(NoticeButton(tr("保留 A"), "a", true), NoticeButton(tr("保留 B"), "b", true)),
            page = "contract", tag = "conflict") { act ->
            conflictNotice = -1
            val keep = when (act) { "a" -> a.optString("id"); "b" -> b.optString("id"); else -> null } ?: return@notice
            if (c.resolveConflict(keep)) {
                logNotice("CONTRACT conflict: keep $keep")
                host.applyNet(false)
                syncHourLog()
                changed()
            }
        }
    }

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

    /** 录像 (MECHANICS 18.8) 的日志: 开始 / 保存 / 出错 / 跨设备传输, 记到单独的 record.log. */
    fun logRecord(text: String) = Store.append(File(dir, "record.log"), "RECORD $text")

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

    /** 别的设备上改动了联网状态 (申请 / 联网 / 结束): 照着切换 (MECHANICS 2.8). */
    fun adoptNet(live: JSONObject) {
        when (net.adopt(live)) {
            "started" -> toast(tr("网络已开启."), "success")
            "ended" -> toast(tr("联网已在其他设备上结束,网络已断开."), "warn")
        }
        changed()
    }

    /** 这个记录格被未知变量 / 记录封锁另外封锁了 (填不了). */
    private fun slotLocked(slot: LocalDateTime, len: Int) =
        contract.hourLocked(slot, slot.plusMinutes(len.toLong()), "", LocalDateTime.now(), dm.fillSeconds.toLong(), dm.autoValue)

    /** 记录封锁 (MECHANICS 18.4): 完成了一次填写, 另外随机封锁两个可以封锁的格子. */
    fun sealExtra() {
        val keys = contract.sealExtra(LocalDateTime.now(), Dice(), dm::unitText, dm.autoValue, dm.fillSeconds.toLong(),
            dm::autoTextForKey)
        if (keys.isEmpty()) return
        logNotice("CONTRACT seal locks ${keys.joinToString(", ")}")
        dm.awaitingSlot()?.let { if (slotLocked(it, dm.promptLen)) dm.promptSlot = null }   // 待填写的那格被封锁了: 不再等它
        toast(tr("记录封锁: 另外封锁了 {t} (不能再修改)", "t" to keys.joinToString(", ") { cellLabel(it) }), "warn", long = true)
        syncHourLog()
        changed()
    }

    /** 整点记录格的键 ("日期/小时", 细化规范的半小时 "日期/小时/1") → "10-18 09:00". */
    private fun cellLabel(key: String): String {
        val p = key.split("/")
        return "${p[0].drop(5)} %02d:%s".format(p[1].toInt(), if (p.getOrNull(2) == "2") "30" else "00")
    }

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
        runCatching { prescript.tick() }
        beatPower()
        runCatching { checkAdapterBack(); checkContractConflicts() }
        checkContract()
        behaviorTick()

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
            for (name in runCatching { checkUnbind() }.getOrDefault(emptyList())) {
                logNotice("SYNC unbind done $name")
                toast(tr("已解除和 {name} 的绑定, {name} 上的数据会被清空 (离线时等它上线).", "name" to name))
            }
            checkOfflineChime()
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
        behavior.active()?.let { c.add(secondsBetween(LocalDateTime.now(), it.end)) }
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
        if (slotLocked(slot, len)) { changed(); return }            // 记录封锁另外封锁了这一格: 填不了, 不提示
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

    /** 有绑定的设备刚离线时, 在焦点设备 (最后有人操作的那台) 上响一次提示音 (MECHANICS 15.2). */
    private fun checkOfflineChime() {
        val off = runCatching { offlinePeers() }.getOrNull() ?: return
        val was = offlineSeen
        offlineSeen = off.keys
        val fresh = if (was == null) emptySet() else off.keys - was
        if (fresh.isEmpty() || !isLatestActive()) return
        val names = fresh.sorted().joinToString(", ") { off[it] ?: "?" }
        logNotice("SYNC offline $names")
        host.playChime {}
        toast(tr("{name} 离线了.", "name" to names), "warn", clear = true)   // 离线消息黑雾也不遮 (18.3)
    }

    // ---- 应用名单: 锁定前台应用 (MECHANICS 14.6, 对应桌面版 4.7) ----

    /** 此刻被锁定的应用. */
    var lockedApp: String? = null
        private set

    private fun checkAppLock() {
        // 和网络限制一样: 开发者模式 / 重置中不锁. 两种断网方式都锁 (全屏蔽时只剩应用名单起作用)
        val active = !dm.resetting && !settings.devMode
        val app = if (active) host.foregroundApp() else ""
        if (app == null) return                                  // 屏幕关闭 / 没有权限: 维持原样
        // 合约 · 乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒: 打开它们不算违反, 只是禁止联网 (vpnPlan, MECHANICS 18.3)
        val mode = settings.str("FILTER_MODE")
        val why = if (app.isEmpty() || app == "OIT" || host.lockExempt(app, mode == RuleKeys.WHITELIST)) null
        else rules.snap.appAllowed(app, timer.state == TState.ON_BREAK, mode).takeIf { !it.first }?.second
        if (why == null) {
            if (lockedApp != null) { lockedApp = null; host.unlockApp() }
            return
        }
        if (lockedApp != app) {
            lockedApp = app
            Store.append(File(dir, "window_guard.log"), "LOCK     $why  $app")
            // 全频道阻塞干扰 (软): 打开被禁止的应用, 这个钟点里各设备合计满 20 次算一次违反
            val id = contract.jamBlock(LocalDateTime.now(), myId())
            if (id.isNotEmpty()) contractViolation(tr("全频道阻塞干扰中频繁打开被禁止的程序"), id)
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
        // 不在专注时也记着各应用开了多久, 开始专注时早就开着的应用马上算; 用不到这两项时不数
        val apps = if (limitOn || chan > 0) host.openApps() else null.also { appSeen.clear() }
        if (apps != null) {
            appSeen.keys.retainAll(apps)
            for (a in apps) appSeen.getOrPut(a) { mono() }
        }
        if (!focusing) {
            appsOver = false
            contract.windowReset()          // 离开专注: 通道阻塞下次专注重新算
            return
        }
        apps ?: return
        val n = apps.count { mono() - (appSeen[it] ?: mono()) >= Cfg.APP_MIN_AGE }
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
        prescript.save()
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
        if ("BEHAVIOR_MODE" in keys) {
            behavior.syncMode()
            if (!behavior.enabled() && behavior.active() != null) behavior.stop(::canRewardOuting)
        }
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

    // ---- 行为模式 / 外出 (MECHANICS 21) ----

    /** 现在正在外出吗. */
    val outing: Outing? get() = behavior.active()

    /** 休息时间只由开始这次外出的设备发 (和计时器的记录只记一次一样). */
    private fun canRewardOuting(o: Outing): Boolean {
        if (!hasPeers()) return true
        return o.owner.isEmpty() || o.owner == myId()
    }

    private fun applyBehaviorEnd(ev: BehaviorEnd) {
        if (ev.reward > 0) {
            timer.addCarry(ev.reward)
            toast(tr("外出结束: 记录了 {h} 小时, 获得 {t} 休息时间.",
                "h" to numText(ev.hours), "t" to fmtMs(ev.reward.toDouble())), "success", long = true)
        } else if (ev.ended.isNotEmpty()) {
            toast(tr("外出结束."))
        }
    }

    /** 每秒: 到时间的外出自动结束, 发休息时间. */
    fun behaviorTick() {
        behavior.syncMode()
        if (!behavior.enabled() && behavior.active() != null) {
            applyBehaviorEnd(behavior.stop(::canRewardOuting))
        }
        if (contract.active && behavior.active() != null) {
            // 别的设备同步来的合约 / 外出撞上了: 外出在合约开始时结束 (MECHANICS 21.1)
            behavior.yieldToContract(contract.startAt)?.let { o ->
                logNotice("OUTING yield to contract, stopped ${o.stopped?.format(Fmt.YMDHM)}")
                toast(tr("合约进行中, 外出已结束."), "warn")
                changed()
            }
        }
        applyBehaviorEnd(behavior.tick(canReward = ::canRewardOuting))
    }

    /** 开始外出. 返回 "" = 好, 否则是原因 (界面直接显示). */
    fun startOuting(hours: Int): String {
        if (contract.active) return tr("合约中不能外出.")
        if (timer.state != TState.IDLE) return tr("请先结束专注 / 休息再外出.")
        val err = behavior.start(hours, myId())
        if (err.isEmpty()) {
            val end = behavior.active()?.end
            logNotice("OUTING start ${hours}h" + (end?.let { " until ${it.format(Fmt.YMDHM)}" } ?: ""))
            toast(tr("外出 {h} 小时, 到 {at} 结束.", "h" to hours,
                "at" to (end?.format(Fmt.MDHM) ?: "")), "success")
            changed()
        }
        return err
    }

    fun stopOuting() {
        applyBehaviorEnd(behavior.stop(::canRewardOuting))
        changed()
    }

    private fun showWelcome() {
        val msg = tr("联网要先申请,等过冷静期才连上,每天有额度.") + "\n\n" +
            tr("接下来{h}小时是配置期:所有修改立即生效.", "h" to Cfg.SETUP_HOURS) + "\n" +
            tr("之后每次修改都要等 48 小时.") + "\n\n" +
            tr("第一次可以在 设置 → 日常 选择行为模式 (这一次立即生效).")
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
        // 整点记录的词条 (记录封锁 / 细化规范) 可能随同步 / 未知变量中途出现: 每秒看一次要不要强制开启整点记录 (18.4),
        // 不然整点记录关着的设备上既不响提示音, 也不能填写, 超时也不判定
        if ((settings.bool("HOUR_LOG_ENABLED") || c.hourForced()) != dm.hourEnabled) { syncHourLog(); changed() }
        val seen = if (c.active) c.id else null
        if (seen != contractSeen) {
            val was = contractSeen
            contractSeen = seen
            host.applyNet(false)                 // 全频道阻塞干扰: 断网方式跟着变
            syncHourLog()
            // 合约结束, 细化规范的半小时格合回一格: 不再等那半小时
            dm.promptSlot?.let { if (dm.promptLen == 30 && !c.fineHour(it)) dm.promptSlot = null }
            if (!c.fog() && heldAchievements.isNotEmpty()) {
                host.achievementsUnlocked(heldAchievements.toList())
                heldAchievements.clear()
            }
            if (was != null && seen == null) c.last?.let {
                toast(tr("合约已结束: {r}", "r" to tr(contractResult(it.optString("result")))))
            }
        }
        if (!c.active) return
        if (c.markFine(LocalDateTime.now())) changed()   // 细化规范生效过的小时一直分两格 (未知变量到时移除后也是, 18.4)
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
            toast(tr("未知变量: {name} 已移除", "name" to tr(Contract.BY_ID[id]!!.name)), clear = true)
        }
        val picked = c.roll(now, Dice(), dm::unitText, dm.autoValue, dm.fillSeconds.toLong(), dm::autoTextForKey)
        val woke = c.wake(now)
        for ((kind, v) in picked) {
            logNotice("CONTRACT unknown -> $kind $v")
            randomEffect(kind, v)
        }
        for (id in woke) {
            logNotice("CONTRACT unknown on $id")
            toast(tr("未知变量: 启用了 {name}", "name" to tr(Contract.BY_ID[id]!!.name)), "warn", long = true, clear = true)
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
                    "t" to it.first.format(Fmt.HM)), "warn", long = true, clear = true)
            }
            "rest_cut", "rest_add" -> {
                val d = if (kind == "rest_add") Contract.RANDOM_REST else -Contract.RANDOM_REST
                timer.adjustBreak(d)
                toast(tr(if (d > 0) "未知变量: 休息时间 +{n} 分钟" else "未知变量: 休息时间 -{n} 分钟", "n" to Contract.RANDOM_REST / 60),
                    "warn", long = true, clear = true)
            }
            "lock" -> if (v.isNotEmpty()) {
                val p = v.split("/")
                val t = "${p[0].substring(5)} %02d:%s".format(p[1].toInt(), if (p.size == 3 && p[2] == "2") "30" else "00")
                toast(tr("未知变量: 封锁了整点记录 {t} (不能再修改)", "t" to t), "warn", long = true, clear = true)
            } else toast(tr("未知变量: 没有可以封锁的整点记录, 无事发生"), clear = true)
            "double" -> toast(tr("未知变量: 这次判定作废, 改为判定两次"), clear = true)
            "slow" -> toast(tr("未知变量: 下一次专注启用阻滞 (只减慢合约内的时间, 按 {p}% 增长)", "p" to Math.round(Contract.RANDOM_SLOW * 100)),
                "warn", long = true, clear = true)
            else -> toast(tr("未知变量: 无事发生"), clear = true)
        }
    }

    /** 静默协议: 专注中这台设备处于联网状态 (各设备看自己的). */
    private fun checkQuiet() {
        val id = contract.quietCheck(timer.state == TState.RUNNING && net.mode == "online")
        if (id.isNotEmpty()) contractViolation(tr("专注中处于联网状态"), id)
    }

    /** 未知变量选中的词条: 点击关闭, 算一次违反. */
    /** 合约页上点击乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒: 解除这一类应用的网络禁令,
     *  扣掉词条等级那么多目标生命值 (MECHANICS 18.3). 界面先确认. */
    fun liftNetBan(id: String) {
        val c = contract
        if (!c.canLift(id)) return
        val name = tr(Contract.BY_ID[id]!!.name)
        val (over, n) = c.liftBan(id)
        logNotice("CONTRACT lift ban $id -$n: ${c.violations}/${c.violationLimit}")
        val text = tr("解除了「{name}」的网络禁令 · 目标生命值 -{n} ({life} / {m})", "name" to name, "n" to n,
            "life" to c.life, "m" to c.violationLimit) + "\n" + tr("危机等级 {lv}", "lv" to c.level)
        toast(text, "warn", long = true)
        addViolation(text)               // 违规记录页 (MECHANICS 8.1): 目标生命值的变化都记下来
        host.applyNet(false)             // 这一类应用的网络放开
        if (over) endContract("failed")
        syncHourLog()
        changed()
    }

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
        if (timer.state == TState.ON_BREAK && c.markRestCells(now, dm.fillSeconds.toLong(), dm::unitText, dm.autoValue, dm::autoTextForKey))
            changed()
        // 记录封锁: 合约里填写了的整小时格记下来, 合约结束后照样锁着 (18.4)
        if (c.sealFilled(now, dm::unitText, dm.autoValue, dm::autoTextForKey)) changed()
        if (!ownerAway(c.owner)) return
        for (key in c.hourMisses(now, dm.fillSeconds.toLong(), dm::unitText, dm.autoValue, dm::autoTextForKey)) {
            val p = key.split("/")
            val h = p[1].toInt()
            // 写这一格的起点 (同整点记录页)
            val label = "%02d:%s".format(h, if (p.size == 3 && p[2] == "2") "30" else "00")
            if (timer.state == TState.ON_BREAK) {
                // 休息期间到了填写时限还没填: 不扣目标生命值 (这一格照样算过, 不会之后再算)
                logNotice("CONTRACT hour miss $key during break: not counted")
                continue
            }
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
                toast(tr("支援短缺: {n} 分钟后将超出可用的合约开始前休息时间", "n" to sec / 60), "warn", long = true, clear = true)
            }
        }
        if (ownerAway(c.owner)) {
            val id = c.supplyCheck(left, timer.breakPre)
            if (id.isNotEmpty()) contractViolation(tr("用超了合约开始前积攒的休息时间"), id)
        }
    }

    /** 休息 / 联网 / 外出中不能开始合约: 返回提示 (没有限制时 ""). */
    fun contractBlock(): String {
        if (timer.state == TState.ON_BREAK) return tr("请先结束休息再开始合约")
        if (behavior.active() != null) return tr("请先结束外出再开始合约")
        if (net.mode == "online") return tr("请先结束联网再开始合约")
        val diff = versionMismatch().firstOrNull() ?: return ""
        return tr("{name} 的版本 ({v}) 和本机 ({me}) 不一样, 不能开始合约", "name" to diff.first, "v" to diff.second,
            "me" to com.obliphur.oit.BuildConfig.VERSION_NAME)
    }

    fun startContract(terms: Collection<String>) {
        if (contract.active) return
        contractBlock().let { if (it.isNotEmpty()) { toast(it, "warn"); return } }
        contract.start(terms, myId(), pre = timer.breakCarry.toDouble(),
            theme = Contract.cleanTheme(settings.str("CONTRACT_THEME")))
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
        // 合约完成时正在休息: 不立即结束这次休息, 继续休息 (仍算合约内的休息);
        // 奖励照常计入积攒的休息时间 (算正常休息时间); 手动暂停清空合约内获得的时间, 自然走完变空闲 (MECHANICS 18.1)
        val restOver = result == "completed" && timer.state == TState.ON_BREAK &&
            timer.breakCid != 0L && timer.breakCid == c.id
        if (restOver) timer.contractRestOver = true else timer.stopBreakForContract()
        if (result != "completed") timer.stopFocusForContract()   // 失败 / 手动终止: 正在进行的专注也立即结束 (不给休息时间)
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
            "abandoned" -> toast(tr("⚔ 行动终止") + if (reward > 0) "\n" + tr("积攒的休息时间 +{t}.", "t" to fmtMs(reward.toDouble())) else "")
            else -> {
                var msg = when (result) {
                    "failed" -> tr("目标生命值归零 (违反 {n} 次), 合约终止.", "n" to rec.optInt("violations"))
                    "offline" -> tr("{name} 离线超过 15 分钟, 合约终止.", "name" to offlineName.ifEmpty { "?" })
                    else -> tr("空闲超过 15 分钟, 合约终止.")
                }
                if (pending >= 1) msg += "\n" + tr("按进行的比例给出 {m} 分钟休息时间 (完成给 {n} 分钟).",
                    "m" to Math.round(reward / 60.0), "n" to Math.round(pending / 60))
                notice(tr("⚔ 行动失败"), msg, listOf(NoticeButton(tr("知道了"), "ok", true)), page = "contract")
            }
        }
        changed()
    }

    /** 违反了词条 id (软规则): 响警报, 记一次, 这个词条降一级 (drop: 直接去掉); 到上限就结束合约. */
    /** 黑雾 (MECHANICS 18.3): 要切到计时器以外的页面 (界面上调用). */
    fun fogSwitch() {
        val id = contract.fogSwitch(LocalDateTime.now(), myId())
        if (id.isNotEmpty()) contractViolation(tr("黑雾中频繁切换页面"), id)
    }

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
        toast(msg, "error", long = true)   // 违规记录: 黑雾中也照常显示 (error 不遮)
        addViolation(msg)               // 违规记录页 (MECHANICS 8.1): 内容照搬这段提示
        if (over) endContract("failed")
        else if (id in Contract.BANS && new.isEmpty()) host.applyNet(false)   // 禁止联网的词条失效: 这一类应用的网络放开
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
        host.applyNet(false)            // 别的设备上词条变了 (无言为真失效等): 计划变了才动 VPN
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
        // 合约的网络禁令 (休息中也算): 乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒 的应用网络全部屏蔽 (MECHANICS 18.3, 18.6)
        val ban = contract.bannedApps()
        val sites = contract.bannedDomains()          // 这一类的网站也封锁 (联网中也一样)
        // 代理-全屏蔽 (Proxy block-all): 离线时接管 DNS, 所有域名一律拦截 (不看网站名单,
        // 局域网照常, 设备同步要用); 联网中全部放行. 应用名单照样按过滤名单模式锁定应用 (checkAppLock)
        if (netMode() == "adapter")
            return if (online) VpnPlan.Dns(false, 0, blockApps = ban, blockDomains = sites) else VpnPlan.Dns(true, 0, blockAll = true, blockApps = ban, blockDomains = sites)
        // 联网中两种名单模式都全部放行 (MECHANICS 4.1)
        if (online) return VpnPlan.Dns(false, 0, blockApps = ban, blockDomains = sites)
        if (settings.str("FILTER_MODE") == RuleKeys.WHITELIST) {
            // 网站白名单为空 = 什么网站都不能上: 全部流量进 VPN (比按 DNS 拦更彻底)
            if (snap.lists[RuleKeys.WHITE_DOMAINS]!!.isEmpty()) return VpnPlan.BlockAll(keepLan = true)
            return VpnPlan.Dns(true, snap.version, blockApps = ban, blockDomains = sites)
        }
        if (snap.lists[RuleKeys.BLACK_DOMAINS]!!.isEmpty()) return VpnPlan.Dns(false, 0, blockApps = ban, blockDomains = sites)
        return VpnPlan.Dns(true, snap.version, blockApps = ban, blockDomains = sites)
    }

    // ---- 重置 ----

    /** 删除全部数据. 之后由 Host 新建一个 Core, 就像第一次安装. */
    fun eraseEverything() {
        timer.abandon()
        notices.clear()
        dm.resetting = true
        net.release()
        host.applyNet(false)          // 计划是 None: 关掉 VPN, 恢复网络
        behavior.erase()
        dm.eraseAll()
    }
}
