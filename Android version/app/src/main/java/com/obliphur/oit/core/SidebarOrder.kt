package com.obliphur.oit.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Local sidebar order, draggable divider and dimming rule.
 * Mirrors desktop `app/app_sidebar.py`: persisted locally in `ui_prefs.json` and not synced across devices.
 */
object SidebarOrder {
    const val DIVIDER = "__divider__"
    val DEFAULT_ORDER = listOf(
        "prescript", "timer", "net", "log", "cal", "stats",
        "contract", "settings", DIVIDER, "comm", "reset",
    )

    fun normalize(value: List<String>?, keys: List<String> = DEFAULT_ORDER): List<String> {
        val raw = value ?: keys
        val result = ArrayList<String>()
        val seen = HashSet<String>()
        for (k in raw) {
            if (k.isNotEmpty() && seen.add(k)) result.add(k)
        }
        if (DIVIDER !in seen) {
            result.add(DIVIDER)
            seen.add(DIVIDER)
        }
        for (key in keys) {
            if (key.isNotEmpty() && seen.add(key)) {
                result.add(result.indexOf(DIVIDER), key)
            }
        }
        return result
    }

    fun isDimmed(order: List<String>, key: String): Boolean {
        if (key == DIVIDER) return false
        val divIdx = order.indexOf(DIVIDER)
        val idx = order.indexOf(key)
        return divIdx >= 0 && idx > divIdx
    }

    fun move(order: List<String>, key: String, beforeKey: String?): List<String> {
        if (key !in order || key == beforeKey) return order
        val list = order.toMutableList()
        list.remove(key)
        val target = if (beforeKey != null) list.indexOf(beforeKey) else -1
        if (target >= 0) list.add(target, key) else list.add(key)
        return list
    }

    fun prefsFile(dataDir: File): File = File(dataDir, "ui_prefs.json")

    fun load(dataDir: File, keys: List<String> = DEFAULT_ORDER): List<String> {
        val obj = Store.readObj(prefsFile(dataDir))
        val arr = obj?.optJSONArray("sidebar_order")
        val raw = arr?.strings()
        return normalize(raw, keys)
    }

    fun save(dataDir: File, order: List<String>, keys: List<String> = DEFAULT_ORDER): Boolean {
        val f = prefsFile(dataDir)
        val obj = Store.readObj(f) ?: JSONObject()
        val norm = normalize(order, keys)
        obj.put("sidebar_order", JSONArray(norm))
        return Store.write(f, obj)
    }
}
