package com.obliphur.oit.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.Duration
import java.time.LocalDateTime

/**
 * 规则库: 域名 / 应用 × 白名单 / 黑名单 (MECHANICS 4). 对应桌面版 netproxy/rules.py.
 * Android 上的"应用"是包名 (如 tv.danmaku.bili), 不是 exe 文件名.
 */
object RuleKeys {
    const val WHITE_DOMAINS = "white_domains"
    const val WHITE_APPS = "white_apps"
    const val BLACK_DOMAINS = "black_domains"
    const val BLACK_APPS = "black_apps"
    val ALL = listOf(WHITE_DOMAINS, WHITE_APPS, BLACK_DOMAINS, BLACK_APPS)

    const val BLACKLIST = "blacklist"
    const val WHITELIST = "whitelist"

    fun forMode(mode: String) =
        if (mode == WHITELIST) WHITE_DOMAINS to WHITE_APPS else BLACK_DOMAINS to BLACK_APPS

    fun isApp(key: String) = key == WHITE_APPS || key == BLACK_APPS
    fun isWhite(key: String) = key == WHITE_DOMAINS || key == WHITE_APPS
}

private val LOOPBACK = setOf("localhost", "::1", "0.0.0.0")
private val HOST_RE = Regex("[a-z0-9\\-._]+|[0-9a-f:]+")
private val PKG_RE = Regex("[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z][A-Za-z0-9_]*)+")

/** 网址 / 域名 → 规范主机名; 不合法返回 "". `*.a.com`, `https://a.com/x` 都归一成 `a.com`. */
fun normDomain(text: String?): String {
    var t = (text ?: "").trim().lowercase()
    if (t.isEmpty()) return ""
    t = t.replace(Regex("^[a-z][a-z0-9+.\\-]*://"), "")
    t = t.split(Regex("[/?#]"), 2)[0]
    t = t.substringAfterLast('@')
    var host = if (t.startsWith("[")) {
        val end = t.indexOf(']')
        if (end > 0) t.substring(1, end) else ""
    } else t.substringBefore(':')
    host = host.trim('.')
    if (host.startsWith("*.")) host = host.substring(2)
    if (host.isEmpty() || host.length > 253) return ""
    if (!HOST_RE.matches(host)) return ""
    if ('.' !in host && ':' !in host) return ""
    return host
}

/**
 * 包名 / 应用商店链接 → 包名; 不合法返回 "".
 * `https://play.google.com/store/apps/details?id=com.a.b` 和 `package:com.a.b` 都整理成 `com.a.b`.
 */
fun normApp(text: String?): String {
    var t = (text ?: "").trim().trim('"')
    if (t.isEmpty()) return ""
    Regex("[?&]id=([A-Za-z0-9_.]+)").find(t)?.let { t = it.groupValues[1] }
    t = t.removePrefix("package:").trim()
    if (t.length > 150 || !PKG_RE.matches(t)) return ""
    return t
}

fun normalizeRule(key: String, text: String?) = if (RuleKeys.isApp(key)) normApp(text) else normDomain(text)

fun domainMatch(host: String, rule: String) = host == rule || host.endsWith(".$rule")

data class RulePending(val op: String, val list: String, val item: String, val applyAt: LocalDateTime)

/** 不可变快照, VPN 线程直接读. */
class RuleSnap(val lists: Map<String, List<String>>, val version: Long) {
    private val apps = lists.mapValues { it.value.toHashSet() }

    /** 联网只看网站名单 (MECHANICS 4.1 / 14.2): 应用名单只管锁定应用 (appAllowed), 不参与联网判定.
     *  (放行?, 原因). 原因: local / online / blacklist-domain / default-allow / whitelist-domain / offline */
    fun decide(host: String?, online: Boolean, mode: String): Pair<Boolean, String> {
        val h = (host ?: "").lowercase().trim('.')
        if (h in LOOPBACK || h.startsWith("127.")) return true to "local"
        // 联网会话中两种名单模式都全部放行 (MECHANICS 4.1)
        if (online) return true to "online"
        if (mode == RuleKeys.WHITELIST) {
            if (lists[RuleKeys.WHITE_DOMAINS]!!.any { domainMatch(h, it) }) return true to "whitelist-domain"
            return false to "offline"
        }
        if (lists[RuleKeys.BLACK_DOMAINS]!!.any { domainMatch(h, it) }) return false to "blacklist-domain"
        return true to "default-allow"
    }

    /** 前台应用能不能用 (应用名单, MECHANICS 14.6, 对应桌面版 4.7). 不能用的会被锁定.
     *  (允许?, 原因). 原因: online / whitelist-app / not-whitelisted / blacklist-app / default-allow */
    /** 应用名单只管专注 / 空闲, 休息中 (rest) 全部放行; 和联不联网无关 (MECHANICS 4.1). */
    fun appAllowed(pkg: String, rest: Boolean, mode: String): Pair<Boolean, String> {
        if (rest) return true to "rest"
        if (mode == RuleKeys.WHITELIST) {
            if (pkg in apps[RuleKeys.WHITE_APPS]!!) return true to "whitelist-app"
            return false to "not-whitelisted"
        }
        if (pkg in apps[RuleKeys.BLACK_APPS]!!) return false to "blacklist-app"
        return true to "default-allow"
    }
}

enum class RuleResult { APPLIED, STAGED, CANCELLED, EXISTS, MISSING, INVALID }

/**
 * 名单的所有改动 (加入和删除) 都要等 48 小时才生效; instant() 为真 (配置期 / 开发者模式) 时立即生效.
 */
class RuleStore(
    private val file: File,
    private val delay: Duration = Duration.ofHours(48),
    private val now: () -> LocalDateTime = LocalDateTime::now,
) {
    var instant: () -> Boolean = { false }
    private val lists = RuleKeys.ALL.associateWith { mutableListOf<String>() }
    val pending = mutableListOf<RulePending>()
    @Volatile
    var snap = RuleSnap(RuleKeys.ALL.associateWith { emptyList() }, 0)
        private set

    init {
        load()
    }

    fun load() {
        val raw = Store.readObj(file) ?: JSONObject()
        val ls = raw.optJSONObject("lists") ?: JSONObject()
        for (k in RuleKeys.ALL) {
            val out = lists[k]!!
            out.clear()
            ls.optJSONArray(k)?.strings()?.forEach {
                val n = normalizeRule(k, it)
                if (n.isNotEmpty() && n !in out) out.add(n)
            }
        }
        pending.clear()
        raw.optJSONArray("pending")?.objects()?.forEach { p ->
            val op = p.optString("op")
            val key = p.optString("list")
            val item = normalizeRule(key, p.optString("item"))
            val at = parseIso(p.optString("apply_at"))
            if ((op == "add" || op == "remove") && key in RuleKeys.ALL && item.isNotEmpty() && at != null)
                pending.add(RulePending(op, key, item, at))
        }
        rebuild()
    }

    private fun save(): Boolean {
        val o = JSONObject()
        val ls = JSONObject()
        for (k in RuleKeys.ALL) ls.put(k, JSONArray(lists[k]))
        o.put("lists", ls)
        o.put("pending", JSONArray(pending.map {
            JSONObject().put("op", it.op).put("list", it.list).put("item", it.item).put("apply_at", it.applyAt.iso())
        }))
        return Store.write(file, o)
    }

    private fun rebuild() {
        snap = RuleSnap(lists.mapValues { it.value.toList() }, snap.version + 1)
    }

    fun items(key: String): List<String> = snap.lists[key] ?: emptyList()

    fun pendingFor(key: String, item: String) = pending.firstOrNull { it.list == key && it.item == item }

    private fun dropPending(op: String, key: String, item: String): Boolean {
        val i = pending.indexOfFirst { it.op == op && it.list == key && it.item == item }
        if (i < 0) return false
        pending.removeAt(i)
        return true
    }

    @Synchronized
    fun add(key: String, raw: String?): Pair<RuleResult, String> {
        val item = normalizeRule(key, raw)
        if (item.isEmpty()) return RuleResult.INVALID to ""
        val lst = lists[key]!!
        if (item in lst) {
            if (dropPending("remove", key, item)) {
                save(); return RuleResult.CANCELLED to item
            }
            return RuleResult.EXISTS to item
        }
        if (pending.any { it.op == "add" && it.list == key && it.item == item }) return RuleResult.STAGED to item
        if (!instant()) {
            pending.add(RulePending("add", key, item, now().plus(delay)))
            save()
            return RuleResult.STAGED to item
        }
        lst.add(item)
        save(); rebuild()
        return RuleResult.APPLIED to item
    }

    @Synchronized
    fun remove(key: String, raw: String?): Pair<RuleResult, String> {
        val item = normalizeRule(key, raw)
        if (item.isEmpty()) return RuleResult.INVALID to ""
        val lst = lists[key]!!
        if (item !in lst) {
            if (dropPending("add", key, item)) {
                save(); return RuleResult.CANCELLED to item
            }
            return RuleResult.MISSING to item
        }
        if (pending.any { it.op == "remove" && it.list == key && it.item == item }) return RuleResult.STAGED to item
        if (!instant()) {
            pending.add(RulePending("remove", key, item, now().plus(delay)))
            save()
            return RuleResult.STAGED to item
        }
        lst.remove(item)
        save(); rebuild()
        return RuleResult.APPLIED to item
    }

    /** 设备同步: 换掉网站名单 (key → 名单) 和网站名单的排队改动; 应用名单各设备自己管. */
    @Synchronized
    fun syncSetDomains(lists: Map<String, List<String>>, domainPending: List<RulePending>?) {
        for ((k, items) in lists) {
            if (k != RuleKeys.WHITE_DOMAINS && k != RuleKeys.BLACK_DOMAINS) continue
            val out = this.lists[k]!!
            out.clear()
            items.map { normalizeRule(k, it) }.filter { it.isNotEmpty() }.distinct().forEach { out.add(it) }
        }
        if (domainPending != null) {
            pending.removeAll { !RuleKeys.isApp(it.list) }
            pending.addAll(domainPending.filter { !RuleKeys.isApp(it.list) })
        }
        save(); rebuild()
    }

    @Synchronized
    fun cancelPending(index: Int): Boolean {
        if (index !in pending.indices) return false
        pending.removeAt(index)
        save()
        return true
    }

    @Synchronized
    fun promoteDue(): List<RulePending> {
        val t = now()
        val inst = instant()
        val due = pending.filter { inst || !it.applyAt.isAfter(t) }
        if (due.isEmpty()) return due
        for (p in due) {
            pending.remove(p)
            val lst = lists[p.list]!!
            if (p.op == "add" && p.item !in lst) lst.add(p.item)
            else if (p.op == "remove") lst.remove(p.item)
        }
        save(); rebuild()
        return due
    }
}
