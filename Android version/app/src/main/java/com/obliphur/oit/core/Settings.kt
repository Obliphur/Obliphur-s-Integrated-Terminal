package com.obliphur.oit.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.Duration
import java.time.LocalDateTime
import kotlin.math.abs

enum class Kind { NUM, CHOICE, INSTANT_TEXT, CHIME }

/**
 * 一个设置项. 数值项按 unit 填写, 存储值 = 填写值 × scale (例如按分钟填, 按秒存);
 * min / max 是允许范围, 按界面单位计.
 */
data class Spec(
    val key: String,
    val label: String,
    val kind: Kind,
    val delayed: Boolean,
    val page: String,
    val default: Any,
    val unit: String = "",
    val scale: Double = 1.0,
    val min: Double = 0.0,
    val max: Double = 0.0,
    val integer: Boolean = false,
    val choices: List<Pair<String, String>> = emptyList(),
    val group: String? = null,
    val short: String? = null,
)

object Schema {
    private fun num(
        key: String, label: String, unit: String, scale: Double, lo: Double, hi: Double,
        integer: Boolean, def: Double, page: String, group: String? = null, short: String? = null,
    ) = Spec(key, label, Kind.NUM, true, page, def, unit, scale, lo, hi, integer,
        group = group, short = short)

    private val ONOFF = listOf("1" to "开启 (ON)", "0" to "关闭 (OFF)")

    val ALL: List<Spec> = listOf(
        // ---- 基本设置 ----
        Spec("LANGUAGE", "Language", Kind.CHOICE, false, "basic", "zh",
            choices = listOf("zh" to "中文", "en" to "English")),
        Spec("FONT_SCALE", "界面字号", Kind.CHOICE, false, "basic", "0.9",
            choices = listOf("0.9" to "偏小 (90%)", "1.0" to "标准 (100%)", "1.15" to "偏大 (115%)",
                "1.3" to "很大 (130%)", "1.5" to "极大 (150%)")),
        Spec("THEME", "界面主题", Kind.CHOICE, false, "basic", "classic",
            choices = listOf("classic" to "经典 (蓝白)", "claude" to "Claude (浅色)",
                "claude_dark" to "Claude (深色)")),
        Spec("UPDATE_CHECK", "打开时检查更新", Kind.CHOICE, false, "basic", "1", choices = ONOFF),
        Spec("UPDATE_AUTO", "自动更新", Kind.CHOICE, false, "basic", "1", choices = ONOFF),
        // ---- 计时器 ----
        num("BREAK_TIME_SCALE", "休息时间倍率", "×", 1.0, 0.1, 5.0, false, 1.0, "timer"),
        num("BREAK_CARRY_RATE", "暂停休息结转比例", "×", 1.0, 0.0, 1.0, false, 0.8, "timer"),
        // ---- 联网 ----
        Spec("NET_MODE", "断网方式", Kind.CHOICE, true, "net", "proxy",
            choices = listOf("adapter" to "代理-全屏蔽 (Proxy block-all)", "proxy" to "代理 - 规则过滤")),
        num("DAILY_LIMIT", "每日联网额度", "min", 60.0, 0.0, 1440.0, true, 7200.0, "net"),
        num("COOLDOWN", "联网冷静期", "min", 60.0, 0.0, 240.0, true, 300.0, "net"),
        num("ENFORCE_EVERY", "断网复查间隔", "s", 1.0, 5.0, 3600.0, true, 60.0, "net"),
        num("TICK_CLAMP", "计时单步上限", "s", 1.0, 1.0, 300.0, true, 30.0, "net"),
        num("QUICK_MULTIPLIER", "快速访问消耗倍率", "×", 1.0, 1.0, 10.0, false, 2.0, "net"),
        num("LOAN_RATE", "紧急借贷偿还倍率", "×", 1.0, 1.0, 10.0, false, 2.0, "net"),
        num("BREAK_COOLDOWN_RATE", "休息中冷静期倍率", "×", 1.0, 0.0, 1.0, false, 0.4, "net"),
        num("QUOTA_MULT_MON", "周一联网倍率", "×", 1.0, 0.0, 10.0, false, 1.0, "net", "weekday", "周一"),
        num("QUOTA_MULT_TUE", "周二联网倍率", "×", 1.0, 0.0, 10.0, false, 1.0, "net", "weekday", "周二"),
        num("QUOTA_MULT_WED", "周三联网倍率", "×", 1.0, 0.0, 10.0, false, 1.0, "net", "weekday", "周三"),
        num("QUOTA_MULT_THU", "周四联网倍率", "×", 1.0, 0.0, 10.0, false, 1.0, "net", "weekday", "周四"),
        num("QUOTA_MULT_FRI", "周五联网倍率", "×", 1.0, 0.0, 10.0, false, 1.0, "net", "weekday", "周五"),
        num("QUOTA_MULT_SAT", "周六联网倍率", "×", 1.0, 0.0, 10.0, false, 1.0, "net", "weekday", "周六"),
        num("QUOTA_MULT_SUN", "周日联网倍率", "×", 1.0, 0.0, 10.0, false, 1.0, "net", "weekday", "周日"),
        // ---- 规则 ----
        Spec("FILTER_MODE", "过滤名单模式", Kind.CHOICE, true, "rules", "blacklist",
            choices = listOf("blacklist" to "黑名单模式", "whitelist" to "白名单模式")),
        // ---- 整点记录 ----
        Spec("HOUR_LOG_ENABLED", "整点记录开关", Kind.CHOICE, true, "log", "1", choices = ONOFF),
        Spec("AUTO_TEXT", "超时自动记录内容", Kind.INSTANT_TEXT, false, "log", "Sleeping",
            choices = listOf("Sleeping" to "Sleeping(睡觉)", "Idle" to "Idle(发呆)",
                "Break" to "Break(休息)", "Distracted" to "Distracted(分心)")),
        Spec("CHIME_WAV", "整点提示音", Kind.CHIME, false, "log", ""),
        num("FILL_WINDOW", "整点记录补填时限", "min", 60.0, 1.0, 1440.0, true, 3600.0, "log"),
        // ---- 窗口 (Android: 应用) ----
        Spec("WINDOW_FOCUS_ENABLED", "应用使用时间统计", Kind.CHOICE, true, "window", "1", choices = ONOFF),
    )

    val PAGES = listOf(
        "basic" to "基本设置", "timer" to "计时器", "net" to "联网",
        "rules" to "规则", "log" to "整点记录", "window" to "应用",
    )

    val BY_KEY = ALL.associateBy { it.key }

    /** (选项类, 数值类) 两组设置项, 各自按 ALL 里的顺序. */
    fun pageSpecs(page: String): Pair<List<Spec>, List<Spec>> {
        val specs = ALL.filter { it.page == page }
        return specs.filter { it.kind != Kind.NUM } to specs.filter { it.kind == Kind.NUM }
    }
}

data class Pending(val value: Any, val requestedAt: String, val applyAt: LocalDateTime)

enum class StageResult { INSTANT, STAGED, NOCHANGE, INVALID, UNKNOWN }

/** 在"每日联网额度"里填这个数 (分钟) 并保存 = 开 / 关开发者模式. 两端相同 (桌面版: settings_manager.DEV_TOGGLE_CODE). */
const val DEV_TOGGLE_CODE = 325799

fun isDevToggle(key: String, value: Any?): Boolean {
    if (key != "DAILY_LIMIT") return false
    val v = (value as? Number)?.toDouble() ?: return false
    return kotlin.math.abs(v - DEV_TOGGLE_CODE * Schema.BY_KEY[key]!!.scale) < 1e-6
}

/**
 * 设置与设置冷静期 (MECHANICS 1). 对应桌面版 settings_manager.py.
 * 数值项在内存里是 Double, 其余是 String.
 */
class Settings(
    private val file: File,
    private val delay: Duration = Duration.ofHours(Cfg.APPLY_DELAY_HOURS),
    private val now: () -> LocalDateTime = LocalDateTime::now,
) {
    val effective = HashMap<String, Any>()
    val pending = LinkedHashMap<String, Pending>()
    val since = HashMap<String, String>()
    val freeNext = HashSet<String>()
    var setupUntil: LocalDateTime? = null
        private set
    /** 开发者模式: 只能用 DEV_TOGGLE_CODE 开 / 关, 不写文件 (每次启动都是关的), 界面上不显示. */
    var devMode = false

    val fresh: Boolean = !file.exists()

    init {
        load()
        promoteDue()
    }

    fun load() {
        val raw = Store.readObj(file) ?: JSONObject()
        val eff = raw.optJSONObject("effective") ?: JSONObject()
        val sin = raw.optJSONObject("since") ?: JSONObject()
        for (s in Schema.ALL) {
            effective[s.key] = coerce(s.key, if (eff.has(s.key)) eff.get(s.key) else s.default) ?: s.default
            since[s.key] = sin.optString(s.key, "2024-01-01 00:00")
        }
        setupUntil = parseIso(raw.optString("setup_until", ""))
        freeNext.clear()
        raw.optJSONArray("free_next")?.strings()?.filter { it in Schema.BY_KEY }?.let { freeNext.addAll(it) }
        pending.clear()
        val pen = raw.optJSONObject("pending") ?: JSONObject()
        for (k in pen.keys()) {
            if (k !in Schema.BY_KEY) continue
            val item = pen.optJSONObject(k) ?: continue
            val at = parseIso(item.optString("apply_at")) ?: continue
            val v = coerce(k, item.opt("value")) ?: continue
            pending[k] = Pending(v, item.optString("requested_at"), at)
        }
    }

    fun save(): Boolean {
        val o = JSONObject()
        o.put("effective", JSONObject(effective as Map<*, *>))
        o.put("since", JSONObject(since as Map<*, *>))
        o.put("free_next", JSONArray(freeNext.sorted()))
        o.put("setup_until", setupUntil?.iso() ?: JSONObject.NULL)
        val pen = JSONObject()
        for ((k, p) in pending) {
            pen.put(k, JSONObject().put("value", p.value).put("requested_at", p.requestedAt)
                .put("apply_at", p.applyAt.iso()))
        }
        o.put("pending", pen)
        return Store.write(file, o)
    }

    // ---- 取值 ----

    fun raw(key: String): Any = effective[key] ?: Schema.BY_KEY[key]!!.default

    /** 数值项的生效值; 文件被手动改成超出范围的值时退回默认值. */
    fun num(key: String): Double {
        val spec = Schema.BY_KEY[key]!!
        val v = raw(key) as? Double ?: return (spec.default as Double)
        return if (validate(key, v) == null) v else spec.default as Double
    }

    fun str(key: String): String {
        val spec = Schema.BY_KEY[key]!!
        val v = raw(key).toString()
        if (spec.kind == Kind.CHOICE && spec.choices.none { it.first == v }) return spec.default.toString()
        return v
    }

    fun bool(key: String): Boolean = str(key) == "1"

    fun dailyQuota(day: java.time.LocalDate): Double = num("DAILY_LIMIT") * quotaMultiplier(day)
    fun quotaMultiplier(day: java.time.LocalDate): Double = maxOf(0.0, num(Cfg.weekdayKey(day)))

    // ---- 配置期 / 免等待 ----

    fun inSetup(): Boolean = setupUntil?.let { now().isBefore(it) } ?: false
    fun changesInstant(): Boolean = devMode || inSetup()
    fun setupLeftSeconds(): Long =
        if (!inSetup()) 0 else Duration.between(now(), setupUntil).seconds

    fun startSetup(hours: Long = Cfg.SETUP_HOURS) {
        setupUntil = now().plusHours(hours)
        save()
    }

    /** 设备同步: 母端 / 其他设备的配置期截止时间. */
    fun syncSetSetup(until: LocalDateTime?) {
        setupUntil = until
    }

    fun endSetup() {
        setupUntil = null
        save()
    }

    /** 修改这一项是否需要等待: instant / setup / free / "" (需要等待, 或开发者模式). */
    fun waitTag(key: String): String {
        val spec = Schema.BY_KEY[key] ?: return ""
        return when {
            !spec.delayed -> "instant"
            devMode -> ""                     // 开发者模式在界面上不显示
            inSetup() -> "setup"
            key in freeNext -> "free"
            else -> ""
        }
    }

    /** 给某一项一次"下一次修改免等待". 已经有了就不重复给, 返回是否新给了. */
    fun grantFreeChange(key: String): Boolean {
        if (key !in Schema.BY_KEY || key in freeNext) return false
        freeNext.add(key)
        save()
        return true
    }

    // ---- 修改 ----

    fun coerce(key: String, value: Any?): Any? {
        val spec = Schema.BY_KEY[key] ?: return value
        if (value == null || value == JSONObject.NULL) return null
        return when (spec.kind) {
            Kind.NUM -> {
                val x = when (value) {
                    is Boolean -> if (value) 1.0 else 0.0
                    is Number -> value.toDouble()
                    else -> value.toString().trim().toDoubleOrNull()
                } ?: return value.toString()
                if (x.isNaN() || x.isInfinite()) return value.toString()
                if (spec.integer) Math.round(x).toDouble() else x
            }
            Kind.CHOICE -> {
                val t = when (value) {
                    is Boolean -> if (value) "1" else "0"
                    is Number -> numText(value.toDouble())
                    else -> value.toString().trim()
                }
                // "1.0" 和 "1" 是同一个选项
                spec.choices.firstOrNull { it.first == t }?.first
                    ?: spec.choices.firstOrNull { c ->
                        val a = c.first.toDoubleOrNull()
                        val b = t.toDoubleOrNull()
                        a != null && b != null && abs(a - b) < 1e-9
                    }?.first ?: t
            }
            else -> value.toString().trim()
        }
    }

    /** 数值项: 必须是数字且在允许范围内. 返回错误信息, 通过时返回 null. */
    fun validate(key: String, value: Any): String? {
        val spec = Schema.BY_KEY[key] ?: return null
        if (spec.kind != Kind.NUM) return null
        val label = tr(spec.label)
        if (value !is Double) return tr("{label}: 请填写数字.", "label" to label)
        val x = value / spec.scale
        if (x < spec.min - 1e-9 || x > spec.max + 1e-9) {
            val rng = if (spec.unit == "×" || spec.unit.isEmpty())
                "${numText(spec.min)} ~ ${numText(spec.max)}"
            else "${numText(spec.min)} ~ ${numText(spec.max)} ${spec.unit}"
            return tr("{label} 允许的范围是 {rng}.", "label" to label, "rng" to rng)
        }
        return null
    }

    private fun same(a: Any, b: Any): Boolean =
        if (a is Double && b is Double) abs(a - b) < 1e-9 else a.toString() == b.toString()

    fun stage(key: String, rawValue: Any?): Pair<StageResult, String> {
        val spec = Schema.BY_KEY[key] ?: return StageResult.UNKNOWN to tr("未知配置项 {key}", "key" to key)
        val value = coerce(key, rawValue) ?: return StageResult.INVALID to tr("{label}: 请填写数字.", "label" to tr(spec.label))
        validate(key, value)?.let { return StageResult.INVALID to it }
        val label = tr(spec.label)
        val t = now()
        if (!spec.delayed || changesInstant()) {
            applyNow(key, value, t)
            return StageResult.INSTANT to tr("{label} 已立即生效.", "label" to label)
        }
        if (same(value, raw(key))) {
            if (pending.remove(key) != null) {
                save()
                return StageResult.STAGED to tr("{label} 的待生效修改已取消.", "label" to label)
            }
            return StageResult.NOCHANGE to tr("{label} 与当前生效值相同.", "label" to label)
        }
        if (key in freeNext) {
            // 一次性的免等待 (紧急借贷借掉明天额度一半以上时给的), 用掉就没了
            freeNext.remove(key)
            applyNow(key, value, t)
            return StageResult.INSTANT to tr("{label} 已立即生效.", "label" to label)
        }
        pending[key] = Pending(value, t.format(Fmt.YMDHM), t.plus(delay))
        save()
        return StageResult.STAGED to tr("{label} 已提交,将于 {at}.", "label" to label,
            "at" to pending[key]!!.applyAt.format(Fmt.YMDHM))
    }

    private fun applyNow(key: String, value: Any, t: LocalDateTime) {
        effective[key] = value
        since[key] = t.format(Fmt.YMDHM)
        pending.remove(key)
        save()
    }

    fun cancel(key: String): Boolean {
        if (pending.remove(key) == null) return false
        save()
        return true
    }

    fun cancelAll(): Int {
        val n = pending.size
        if (n > 0) {
            pending.clear()
            save()
        }
        return n
    }

    /** 到期 (或配置期 / 开发者模式) 的修改生效. 返回生效的项. */
    fun promoteDue(): List<String> {
        val t = now()
        val instant = changesInstant()
        val due = pending.filter { instant || !it.value.applyAt.isAfter(t) }.keys.toList()
        for (k in due) {
            effective[k] = pending[k]!!.value
            since[k] = t.format(Fmt.YMDHM)
            pending.remove(k)
        }
        if (due.isNotEmpty()) save()
        return due
    }

    fun pendingLeftSeconds(key: String): Long =
        pending[key]?.let { maxOf(0L, Duration.between(now(), it.applyAt).seconds) } ?: 0L

    fun applyAtText(key: String): String {
        val at = pending[key]?.applyAt ?: return ""
        val secs = Duration.between(now(), at).seconds
        if (secs <= 0) return tr("立即生效中…")
        val d = secs / 86400
        val h = (secs % 86400) / 3600
        val m = (secs % 3600) / 60
        return when {
            d > 0 -> tr("{at} 生效 (还有 {d} 天 {h} 小时)", "at" to at.format(Fmt.YMDHM), "d" to d, "h" to h)
            h > 0 -> tr("{at} 生效 (还有 {h} 小时 {m} 分)", "at" to at.format(Fmt.HM), "h" to h, "m" to m)
            else -> tr("{at} 生效 (还有 {m} 分)", "at" to at.format(Fmt.HM), "m" to m)
        }
    }

    // ---- 显示 ----

    fun uiNumber(key: String, value: Any? = null): String {
        val spec = Schema.BY_KEY[key]!!
        val v = (value ?: raw(key)) as? Double ?: return (value ?: raw(key)).toString()
        return numText(v / spec.scale)
    }

    /** 填写框里的文字 → 存储值; 不是数字时原样返回 (stage 会拒绝). */
    fun fromUiNumber(key: String, text: String): Any {
        val spec = Schema.BY_KEY[key]!!
        val x = text.trim().toDoubleOrNull() ?: return text.trim()
        return coerce(key, x * spec.scale) ?: text
    }

    fun display(key: String, value: Any? = null): String {
        val spec = Schema.BY_KEY[key] ?: return (value ?: raw(key)).toString()
        val v = value ?: raw(key)
        return when (spec.kind) {
            Kind.NUM -> {
                val x = (v as? Double ?: return v.toString()) / spec.scale
                if (spec.unit == "×") "×${numText(x)}" else if (spec.unit.isEmpty()) numText(x) else "${numText(x)} ${spec.unit}"
            }
            Kind.CHOICE -> spec.choices.firstOrNull { it.first == v.toString() }?.second?.let { tr(it) } ?: v.toString()
            Kind.CHIME -> if (v.toString().isBlank()) tr("内置三音提示音") else tr("自选音频")
            Kind.INSTANT_TEXT -> v.toString()
        }
    }
}
