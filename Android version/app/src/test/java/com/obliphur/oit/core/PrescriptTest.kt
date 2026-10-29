package com.obliphur.oit.core

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.LocalDateTime

/** 指令 (MECHANICS 22). 对应桌面版 prescript.py 的 _selftest. */
class PrescriptTest {

    private class Env {
        var mode = "focus"
        var page = "prescript"
        var minimized = false
        var dev = false
        var hours = 0.0
        val contracts = mutableListOf<JSONObject>()
        val rest = mutableListOf<Pair<Int, String>>()
        val dir: File = Files.createTempDirectory("oit").toFile()
        fun engine() = PrescriptEngine(
            File(dir, "prescript.json"),
            stateFn = { mode }, todayHoursFn = { hours }, contractsFn = { contracts },
            pageFn = { page }, minimizedFn = { minimized },
            grantRestFn = { s, c -> rest.add(s to c) }, devFn = { dev },
        )
    }

    private val t0 = LocalDateTime.of(2026, 10, 28, 10, 5, 0)

    @Test
    fun codes() {
        assertEquals("REST3", Prescript.codeFor(1.0))
        assertEquals("REST0.3", Prescript.codeFor(0.1))
        assertEquals("_%NONE%.", Prescript.padCode("NONE"))
        assertEquals("_FOCUS%.", Prescript.padCode(Prescript.typeCode("I")))
        assertEquals(8, Prescript.padCode(Prescript.typeCode("II")).length)
        assertEquals("_REST3%.", Prescript.padCode("REST3"))
        assertEquals("_REST20.", Prescript.padCode("REST20"))
        assertEquals("REST0.3.", Prescript.padCode("REST0.3"))
        assertEquals("_REST3%.", Prescript.padCode("1h*3min"))
        assertEquals("_REST20.", Prescript.padCode("20min"))
        for (c in listOf("A", "REST120", "REST1200", "REST12345678")) assertEquals(8, Prescript.padCode(c).length)
    }

    @Test
    fun noIconsTheme() {
        I18n.noIcons = true
        try {
            assertEquals("计时器", I18n.ic("⏱  计时器"))
            assertEquals("设置", I18n.ic("⚙  设置"))
            assertEquals("指令", tr("📜  指令"))
            assertEquals("整点记录", I18n.ic("📝  整点记录"))
            assertEquals("设置  2", I18n.ic("⚙  设置") + "  2")
            assertEquals("×", I18n.ic("✕"))
            assertEquals("≡", I18n.ic("☰"))
            assertEquals("◂ 现在", I18n.ic("◂ 现在"))
        } finally {
            I18n.noIcons = false
        }
        assertEquals("⏱  计时器", I18n.ic("⏱  计时器"))
    }

    @Test
    fun drawHasNoCooldownButFocusBlocks() {
        val env = Env()
        val eng = env.engine()
        assertTrue(eng.canDraw(t0))
        assertNotNull(eng.draw(t0, choose = "III"))
        assertTrue(eng.canDraw(t0.plusMinutes(1)))             // 没有领取冷却: 同一个小时里接着领
        assertNotNull(eng.draw(t0.plusMinutes(1), choose = "I"))
        assertEquals(listOf("III", "I"), eng.actives().map { it.getString("type") })
        // 专注指令 (I) 没完成 / 作废之前不能再领新的
        assertFalse(eng.canDraw(t0.plusMinutes(2)))
        assertNull(eng.draw(t0.plusMinutes(2), choose = "VI"))
        eng.tick(t0.plusMinutes(1).plusHours(1).plusSeconds(1))     // I 一小时后作废
        assertTrue(eng.canDraw(t0.plusHours(1).plusMinutes(3)))
    }

    @Test
    fun focusCompletesAndSettles() {
        val env = Env()
        val eng = env.engine()
        env.page = "timer"
        eng.draw(t0, choose = "I")
        var now = t0
        repeat(50 * 60) { now = now.plusSeconds(1); eng.tick(now) }
        assertNull(eng.current)
        // 当时不在指令页: 休息时间等回到页面时才结算
        assertTrue(env.rest.isEmpty())
        env.page = "prescript"
        val given = eng.settleRewards(now.plusSeconds(5))
        assertEquals(1, given.size)
        assertEquals("REST3", given[0].getString("code"))
        assertEquals(listOf(180 to "REST3"), env.rest)
        assertTrue(eng.settleRewards(now.plusSeconds(6)).isEmpty())
        assertEquals(1, eng.grantedToday(now).size)
    }

    @Test
    fun restPrescriptScalesWithRemainingRest() {
        assertEquals(25, Prescript.restMinutes(3000.0))
        assertEquals(30, Prescript.restMinutes(7200.0))
        assertEquals(1, Prescript.restMinutes(0.0))
        val dir = Files.createTempDirectory("oit").toFile()
        val eng = PrescriptEngine(File(dir, "prescript.json"), stateFn = { "break" }, remainingRestFn = { 3000.0 })
        val cur = eng.draw(t0, choose = "III")!!
        assertEquals(25, cur.getInt("n"))
        assertEquals("休息25min.", cur.getString("body"))
        assertEquals(25 / 30.0, cur.getDouble("t"), 1e-9)
        var now = t0
        repeat(25 * 60) { now = now.plusSeconds(1); eng.tick(now) }
        assertNull(eng.current)
        assertEquals(150, eng.rawData().getJSONArray("pending").getJSONObject(0).getInt("seconds"))
    }

    @Test
    fun noReflectDuringContract() {
        val dir = Files.createTempDirectory("oit").toFile()
        var active = true
        val rng = kotlin.random.Random(7)
        val eng = PrescriptEngine(File(dir, "prescript.json"), stateFn = { "focus" }, contractActiveFn = { active }, rng = rng)
        var seen = false
        repeat(300) { i ->
            val c = eng.draw(t0.plusHours(i.toLong() * 2))!!
            if (c.getString("type") == "VI") seen = true
            eng.rawData().put("current", org.json.JSONObject.NULL).put("extra", org.json.JSONArray())   // 手里最多 3 条: 清掉再抽
        }
        assertFalse("合约期间不会抽到 VI", seen)
        active = false
        repeat(1000) { i ->
            val c = eng.draw(t0.plusHours(1000L + i * 2))!!
            if (c.getString("type") == "VI") seen = true
            eng.rawData().put("current", org.json.JSONObject.NULL).put("extra", org.json.JSONArray())
        }
        assertTrue("没有合约时抽得到 VI", seen)
    }

    @Test
    fun drawingIdleEntryPausesMedia() {
        val dir = Files.createTempDirectory("oit").toFile()
        var paused = 0
        val eng = PrescriptEngine(File(dir, "prescript.json"), pauseMediaFn = { paused++ })
        eng.draw(t0, choose = "VI")
        assertEquals(0, paused)
        for ((i, kind) in Prescript.IDLE_KINDS.withIndex()) {
            eng.rawData().put("current", JSONObject.NULL).put("extra", org.json.JSONArray())
            eng.draw(t0.plusHours(1), choose = kind)
            assertEquals(i + 1, paused)
        }
    }

    @Test
    fun voidLeavesPage() {
        val env = Env()
        val eng = env.engine()
        eng.draw(t0, choose = "V")
        env.minimized = true
        eng.pageChanged(t0.plusSeconds(10))
        assertNull(eng.current)
        assertTrue(eng.canDraw(t0.plusSeconds(11)))            // V 作废后可以立即再领
    }

    @Test
    fun statsPageStay() {
        val env = Env()
        val eng = env.engine()
        env.page = "stats"
        eng.draw(t0, choose = "VI")
        var now = t0
        repeat(182) { now = now.plusSeconds(1); eng.tick(now) }
        assertNull(eng.current)
        assertEquals("completed", eng.rawData().getJSONArray("history").getJSONObject(0).getString("result"))
    }

    @Test
    fun keepsThreeInAnyMode() {
        for (dev in listOf(false, true)) {
            val env = Env()
            env.dev = dev
            env.mode = "idle"
            val eng = env.engine()
            assertNotNull(eng.draw(t0, choose = "III"))
            assertNotNull(eng.draw(t0, choose = "VI"))
            assertNotNull(eng.draw(t0, choose = "II"))
            assertEquals(Prescript.KEEP, eng.actives().size)       // 旧的没有被顶掉, 没有冷却
            assertEquals(setOf("break", "contract", "stats"), eng.marks())
            assertFalse(eng.canDraw(t0))
            assertNull(eng.draw(t0, choose = "IV"))                 // 满了: 过多久都不能领
            assertNull(eng.draw(t0.plusHours(5), choose = "IV"))
            env.mode = "break"
            env.page = "timer"
            var now = t0
            repeat(20 * 60 + 1) { now = now.plusSeconds(1); eng.tick(now) }
            assertEquals(listOf("VI", "II"), eng.actives().map { it.getString("type") })
            assertTrue(eng.canDraw(now))
        }
    }

    private fun at(h: Int, m: Int = 0) = t0.withHour(h).withMinute(m)

    @Test
    fun dailyFocusCannotBeDrawnTwice() {
        val env = Env()
        env.page = "timer"
        val eng = env.engine()
        for (pool in listOf("focus", "break", "idle")) assertTrue(pool, eng.choices(pool, at(12)).toMap().containsKey("IV"))
        assertNotNull(eng.draw(at(12), choose = "IV", pool0 = "focus"))
        for (pool in listOf("focus", "break", "idle")) assertFalse(pool, eng.choices(pool, at(12)).toMap().containsKey("IV"))
        assertEquals(1.0, eng.choices("focus", at(12)).sumOf { it.second }, 1e-9)
        env.hours = 9.0
        eng.tick(at(12).plusSeconds(1))                         // 完成后今天也不能再领
        assertNull(eng.current)
        assertFalse(eng.choices("focus", at(12)).toMap().containsKey("IV"))
    }

    @Test
    fun idleEntriesAreBoostedByTimeOfDay() {
        val eng = Env().engine()
        assertEquals("V", eng.idleBoosts(at(5, 29)).single());  assertEquals("VII", eng.idleBoosts(at(5, 30)).single())
        assertEquals("VII", eng.idleBoosts(at(7, 29)).single()); assertEquals("V", eng.idleBoosts(at(7, 30)).single())
        assertEquals("VIII", eng.idleBoosts(at(13)).single());  assertEquals("VIII", eng.idleBoosts(at(14, 29)).single())
        assertEquals("V", eng.idleBoosts(at(14, 30)).single())
        assertEquals("VIII", eng.idleBoosts(at(19, 30)).single()); assertEquals("XI", eng.idleBoosts(at(20, 30)).single())
        assertEquals("IX", eng.idleBoosts(at(9)).single());    assertEquals("IX", eng.idleBoosts(at(22, 29)).single())
        assertEquals("V", eng.idleBoosts(at(22, 30)).single())
        assertEquals("XI", eng.idleBoosts(at(11, 59)).single()); assertEquals("X", eng.idleBoosts(at(23)).single())
        assertEquals("X", eng.idleBoosts(at(23, 59)).single()); assertEquals("V", eng.idleBoosts(at(0, 30)).single())
        for ((h, m) in listOf(10 to 0, 7 to 0, 23 to 30, 13 to 30, 3 to 0)) {
            val opts = eng.choices("idle", at(h, m)).toMap()
            assertEquals(1.0, opts.values.sum(), 1e-9)
            assertEquals(0.05, opts.getValue("I"), 1e-9)
            assertEquals(0.02, opts.getValue("IV"), 1e-9)
            val group = Prescript.IDLE_KINDS.associateWith { opts.getValue(it) }
            assertEquals(0.9, group.values.sum(), 1e-9)
            val boosted = eng.idleBoosts(at(h, m)).single()
            assertEquals(0.72, group.getValue(boosted), 1e-9)
            for ((k, v) in group) if (k != boosted) assertEquals(0.036, v, 1e-9)
        }
        // 专注 / 休息池不拆 V
        assertEquals(0.05, eng.choices("break", at(7)).toMap().getValue("V"), 1e-9)
        assertFalse(eng.choices("break", at(7)).toMap().containsKey("VII"))
        // 抽到过的 (V 除外) 这一轮不再抽, 对得上的词条抽过了就换成 V
        eng.rawData().put("run", org.json.JSONArray(listOf("VII")))
        val opts = eng.choices("idle", at(7)).toMap()
        assertFalse(opts.containsKey("VII"))
        assertEquals(0.72, opts.getValue("V"), 1e-9)
        assertEquals(1.0, opts.values.sum(), 1e-9)
    }

    @Test
    fun overlappingWindowsShareTheBoost() {
        val dir = Files.createTempDirectory("oit").toFile()
        val wins = HashMap<String, List<Pair<Int, Int>>>()
        for (k in Prescript.IDLE_KINDS) wins[k] = Cfg.defaultWindows(k)
        val eng = PrescriptEngine(File(dir, "prescript.json"), pageFn = { "prescript" }, windowsFn = { wins.getValue(it) })
        wins["XI"] = Cfg.parseWindows("05:00-08:00")!!
        assertEquals(setOf("VII", "XI"), eng.idleBoosts(at(7)).toSet())
        assertEquals(listOf("V"), eng.idleBoosts(at(8)))
        val opts = eng.choices("idle", at(7)).toMap()
        assertEquals(0.36, opts.getValue("VII"), 1e-9)
        assertEquals(0.36, opts.getValue("XI"), 1e-9)
        assertEquals(0.045, opts.getValue("V"), 1e-9)
        assertEquals(1.0, opts.values.sum(), 1e-9)
        eng.rawData().put("run", org.json.JSONArray(listOf("XI")))            // 抽到过的不再算重叠
        assertEquals(listOf("VII"), eng.idleBoosts(at(7)))
        eng.rawData().put("run", org.json.JSONArray())
        wins["V"] = Cfg.parseWindows("00:00-05:00")!!                           // V 也可以设时段
        assertEquals(listOf("V"), eng.idleBoosts(at(3)))
        wins["X"] = emptyList()
        assertEquals(listOf("V"), eng.idleBoosts(at(23, 30)))
        wins["IX"] = Cfg.parseWindows("22:00-02:00")!!                           // 跨零点
        assertEquals(listOf("IX"), eng.idleBoosts(at(23, 30)))
        assertEquals(setOf("IX", "V"), eng.idleBoosts(at(1)).toSet())
    }

    private fun push(src: PrescriptEngine, dst: PrescriptEngine, now: LocalDateTime) {
        val doc = src.syncDoc(now).mapValues { (_, v) -> JSONObject(v.toString()) }    // 过一遍 JSON, 和真的同步一样
        dst.syncApply(doc, now)
    }

    @Test
    fun instructionsSyncBetweenDevices() {
        val ts = LocalDateTime.of(2026, 10, 28, 15, 0, 0)
        var modeA = "idle"
        var pageA = "prescript"
        var pageB = "timer"
        val rest = mutableListOf<Pair<Int, String>>()
        val a = PrescriptEngine(File(Files.createTempDirectory("oit").toFile(), "prescript.json"), stateFn = { modeA },
            pageFn = { pageA }, peersOnPageFn = { pageB == "prescript" }, rng = kotlin.random.Random(1))
        val b = PrescriptEngine(File(Files.createTempDirectory("oit").toFile(), "prescript.json"), stateFn = { modeA },
            pageFn = { pageB }, peersOnPageFn = { pageA == "prescript" },
            grantRestFn = { s, c -> rest.add(s to c) }, rng = kotlin.random.Random(2))
        // A 领一条 III, B 同步到; 进度不同步
        val iii = a.draw(ts, choose = "III", pool0 = "break")!!
        push(a, b, ts)
        assertEquals(listOf("III"), b.actives().map { it.getString("type") })
        assertEquals(iii.getString("id"), b.current!!.getString("id"))
        assertFalse(a.syncDoc(ts).getValue("i:" + iii.getString("id")).toString().contains("break_sec"))
        // 数量上限 / 专注指令两边互相算
        a.draw(ts, choose = "I", pool0 = "focus")
        push(a, b, ts)
        assertEquals(2, b.actives().size)
        assertFalse(b.canDraw(ts))
        // B 完成 III: A 同步后也结束, 奖励只有一份
        val bIii = b.actives().first { it.getString("type") == "III" }
        modeA = "break"
        var now = ts
        repeat((iii.getInt("n") * 60) + 2) { now = now.plusSeconds(1); b.tick(now) }
        assertEquals(listOf("I"), b.actives().map { it.getString("type") })
        push(b, a, now)
        assertEquals(listOf("I"), a.actives().map { it.getString("type") })
        assertEquals(1, a.rawData().getJSONArray("pending").length())
        assertEquals(bIii.getString("id"), a.rawData().getJSONArray("pending").getJSONObject(0).getString("id"))
        // 进行中的旧版本盖不回结束的 (SyncService.wins 里的规则): 这里 syncApply 看到已有历史记录就忽略
        a.syncApply(mapOf("i:" + bIii.getString("id") to JSONObject(bIii.toString())), now)
        assertEquals(listOf("I"), a.actives().map { it.getString("type") })
        // 奖励在 A 的指令页上结算 -> B 同步后不再结算
        val given = a.settleRewards(now.plusSeconds(5))
        assertEquals(1, given.size)
        push(a, b, now.plusSeconds(6))
        assertEquals(0, b.rawData().getJSONArray("pending").length())
        assertTrue(b.settleRewards(now.plusSeconds(7)).isEmpty())
        assertTrue(rest.isEmpty())
        // 空闲词条: A 离开页面时 B 还停在页面上就不作废, 到点由 B 完成; 都离开才作废
        modeA = "idle"
        pageA = "prescript"; pageB = "prescript"
        a.rawData().put("current", JSONObject.NULL).put("extra", org.json.JSONArray())
        b.rawData().put("current", JSONObject.NULL).put("extra", org.json.JSONArray())
        val t1 = now.plusMinutes(10)
        val idle = a.draw(t1, choose = "VIII", pool0 = "idle")!!
        push(a, b, t1)
        assertEquals("VIII", b.current!!.getString("type"))
        pageA = "timer"
        a.tick(t1.plusMinutes(1))
        assertNotNull(a.current)
        a.tick(t1.plusMinutes(11))                     // 到点了但 A 不在页面上: 不由 A 完成
        assertNotNull(a.current)
        b.tick(t1.plusMinutes(11))
        assertNull(b.current)
        push(b, a, t1.plusMinutes(11))
        assertNull(a.current)
        assertEquals("completed", a.rawData().getJSONArray("history").let { it.getJSONObject(it.length() - 1) }.getString("result"))
        pageA = "prescript"
        a.draw(t1.plusMinutes(20), choose = "IX", pool0 = "idle")
        push(a, b, t1.plusMinutes(20))
        pageA = "timer"; pageB = "timer"
        a.tick(t1.plusMinutes(21))
        assertNull(a.current)
        assertEquals("void", a.rawData().getJSONArray("history").let { it.getJSONObject(it.length() - 1) }.getString("result"))
    }

    @Test
    fun windowTextParsing() {
        assertEquals(listOf(330 to 450, 780 to 870), Cfg.parseWindows("05:30-07:30,13:00-14:30"))
        assertEquals(listOf(1380 to 1440), Cfg.parseWindows("23:00-24:00"))
        assertEquals(listOf(1320 to 1440, 0 to 120), Cfg.parseWindows("22:00-02:00"))
        assertEquals(listOf(300 to 420), Cfg.parseWindows("5-7"))
        assertEquals(listOf(330 to 450), Cfg.parseWindows("0530-0730"))
        assertEquals(emptyList<Pair<Int, Int>>(), Cfg.parseWindows(""))
        assertNull(Cfg.parseWindows("坏的"))
        assertNull(Cfg.parseWindows("08:00-08:00"))
        assertNull(Cfg.parseWindows("25:00-26:00"))
        assertNull(Cfg.parseWindows("08:00-24:30"))
    }

    @Test
    fun consecutiveIdleDrawsOnlyRepeatV() {
        val env = Env()
        env.mode = "idle"
        val eng = env.engine()
        eng.rawData().put("focus_chance", -1.0)            // 这一段不让它触发专注池
        fun clear() = eng.rawData().put("current", JSONObject.NULL).put("extra", org.json.JSONArray())
        eng.draw(t0, choose = "VII"); clear()
        eng.draw(t0, choose = "V"); clear()
        eng.draw(t0, choose = "V")
        assertEquals(listOf("VII"), eng.rawData().getJSONArray("run").let { a -> (0 until a.length()).map { a.getString(it) } })
        // 一个都不剩时全是 V; 中间抽了专注池再回到空闲池则重新算
        clear()
        eng.draw(t0, choose = "VI", pool0 = "focus"); clear()
        eng.draw(t0, choose = "VIII"); clear()
        assertEquals(listOf("VIII"), eng.rawData().getJSONArray("run").let { a -> (0 until a.length()).map { a.getString(it) } })
    }

    private class Roll(vararg v: Double) : kotlin.random.Random() {
        val q = ArrayDeque(v.toList())
        private val base = kotlin.random.Random(3)
        override fun nextBits(bitCount: Int) = base.nextBits(bitCount)
        override fun nextDouble(): Double = if (q.isNotEmpty()) q.removeFirst() else base.nextDouble()
    }

    @Test
    fun idleDrawMayForceFocusPoolNext() {
        val env = Env()
        env.mode = "idle"
        val rng = Roll(0.9, 0.6, 0.8, 0.99)
        val eng = PrescriptEngine(File(env.dir, "prescript.json"), stateFn = { env.mode }, pageFn = { env.page }, rng = rng)
        fun clear() = eng.rawData().put("current", JSONObject.NULL).put("extra", org.json.JSONArray())
        eng.rawData().put("last_pool", "idle")
        for (want in listOf(0.5, 0.75, 1.0)) {                // 没触发: 概率 +25%
            clear()
            eng.draw(t0, choose = "V")
            assertEquals(want, eng.rawData().getDouble("focus_chance"), 1e-9)
            assertFalse(eng.rawData().getBoolean("force_focus"))
        }
        clear()
        eng.draw(t0, choose = "V")                            // 100%: 必定触发, 概率回到 25%
        assertTrue(eng.rawData().getBoolean("force_focus"))
        assertEquals(Prescript.FOCUS_CHANCE_START, eng.rawData().getDouble("focus_chance"), 1e-9)
        clear()
        val cur = eng.draw(t0, choose = "VI")!!                // 下一次抽取不看状态: 专注池
        assertEquals("focus", cur.getString("pool"))
        assertFalse(eng.rawData().getBoolean("force_focus"))
    }

    @Test
    fun idleEntriesAutoCompleteAndVoid() {
        val env = Env()
        env.mode = "idle"
        val eng = env.engine()
        val cur = eng.draw(t0, choose = "VII")!!
        assertTrue(cur.isNull("expires"))                      // 没有作废时限
        assertEquals(t0.plusMinutes(20).iso(), cur.getString("due"))
        eng.tick(t0.plusMinutes(19).plusSeconds(59))
        assertNotNull(eng.current)
        eng.tick(t0.plusMinutes(20))
        assertNull(eng.current)
        val rec = eng.rawData().getJSONArray("history").getJSONObject(0)
        assertEquals("completed", rec.getString("result"))
        assertEquals(0.4, rec.getDouble("t"), 1e-9)
        assertEquals(listOf(72 to "REST1.2"), env.rest)        // 在指令页上, 当场结算
        // 离开指令页 / 退到后台立即作废, 可以马上再领 (没有冷却)
        for (leave in listOf("page", "min")) {
            env.page = "prescript"; env.minimized = false
            eng.draw(t0.plusHours(1), choose = "VIII")
            if (leave == "page") env.page = "stats" else env.minimized = true
            eng.tick(t0.plusHours(1).plusMinutes(1))
            assertNull(eng.current)
            assertEquals("void", eng.rawData().getJSONArray("history").let { it.getJSONObject(it.length() - 1) }.getString("result"))
            assertTrue(eng.canDraw(t0.plusHours(1).plusMinutes(2)))
        }
    }

    @Test
    fun contractIdleDrawsFocusOrBreakPool() {
        val dir = Files.createTempDirectory("oit").toFile()
        var contract = true
        val eng = PrescriptEngine(File(dir, "prescript.json"), stateFn = { "idle" }, contractActiveFn = { contract },
            pageFn = { "prescript" }, rng = kotlin.random.Random(5))
        val pools = HashSet<String>()
        repeat(60) { i ->
            eng.rawData().put("current", JSONObject.NULL).put("extra", org.json.JSONArray())
            pools.add(eng.draw(t0.plusHours(i.toLong()))!!.getString("pool"))
        }
        assertEquals(setOf("focus", "break"), pools)
        contract = false
        eng.rawData().put("current", JSONObject.NULL).put("extra", org.json.JSONArray()).put("force_focus", false)
        assertEquals("idle", eng.draw(t0.plusHours(100))!!.getString("pool"))
    }

    @Test
    fun recordInstructionLog() {
        val env = Env()
        env.mode = "idle"
        var hour = "09:00  学习\n10:00  写代码"
        val eng = PrescriptEngine(File(env.dir, "prescript.json"), stateFn = { env.mode }, pageFn = { env.page },
            hourLogFn = { hour })
        val day = at(23, 10)
        eng.rawData().getJSONArray("history").put(JSONObject().put("id", "h1").put("type", "III").put("body", "休息5min.")
            .put("accepted", at(13).iso()).put("ended", at(13).iso()).put("result", "completed").put("t", 0.2))
        eng.rawData().getJSONArray("history").put(JSONObject().put("id", "h2").put("type", "I").put("body", "接下来的1h专注50min.")
            .put("accepted", at(14).iso()).put("ended", at(15).iso()).put("result", "expired").put("t", JSONObject.NULL))
        eng.noteClose(at(8, 15))
        eng.rawData().getJSONArray("closes").put(day.minusDays(1).iso())
        val cur = eng.draw(day, choose = "X")!!
        assertTrue(cur.isNull("due") && cur.isNull("expires"))
        val base = eng.logPrefill(day)
        val paras = base.split("\n\n")
        assertEquals(3, paras.size)
        assertEquals("09:00  学习\n10:00  写代码", paras[0])
        assertTrue(paras[1].contains("休息5min. (完成)"))
        assertTrue(paras[1].contains("接下来的1h专注50min. (废弃)"))
        assertTrue(paras[1].contains("(进行中)"))
        assertEquals("08:15", paras[2])
        eng.tick(day.plusMinutes(40))                          // 没有计时: 一直等着
        assertNotNull(eng.current)
        assertEquals("void", eng.submitLog(base + "  \n", day.plusMinutes(41)))        // 一点没改: 作废
        assertNull(eng.current)
        eng.draw(day.plusMinutes(42), choose = "X")
        val base2 = eng.logPrefill(day.plusMinutes(42))
        assertEquals("completed", eng.submitLog(base2 + "\n今天读了书", day.plusMinutes(43)))
        val rec = eng.rawData().getJSONArray("history").let { it.getJSONObject(it.length() - 1) }
        assertEquals("completed", rec.getString("result"))
        assertEquals(0.1, rec.getDouble("t"), 1e-9)
        assertTrue(eng.rawData().getJSONObject("logs").getString(day.toLocalDate().key()).contains("今天读了书"))
        assertNull(eng.submitLog("x"))
        hour = ""                                              // 没启用整点记录: 第一段空白
        eng.draw(day.plusMinutes(44), choose = "X")
        assertTrue(eng.logPrefill(day.plusMinutes(44)).startsWith("\n\n"))
        eng.abandonLog(day.plusMinutes(45))                    // 没保存就关了: 作废
        assertNull(eng.current)
        eng.draw(day.plusMinutes(46), choose = "X")            // 离开指令页也作废
        env.page = "timer"
        eng.tick(day.plusMinutes(47))
        assertNull(eng.current)
    }

    @Test
    fun completionNotifies() {
        val env = Env()
        env.page = "timer"
        var done = 0
        val eng = PrescriptEngine(
            File(env.dir, "prescript.json"), stateFn = { env.mode }, pageFn = { env.page },
            minimizedFn = { false }, onComplete = { done++ },
        )
        eng.draw(t0, choose = "I")
        var now = t0
        repeat(50 * 60 + 1) { now = now.plusSeconds(1); eng.tick(now) }
        assertNull(eng.current)
        assertEquals(1, done)
    }

    @Test
    fun contractMatch() {
        val env = Env()
        val eng = env.engine()
        env.page = "timer"
        eng.draw(t0, choose = "II")
        val n = eng.current!!.getInt("n")
        val rec = JSONObject().put("result", "completed").put("level", n)
            .put("start", t0.plusMinutes(5).iso()).put("planned", t0.plusMinutes(65).iso())
            .put("end", t0.plusMinutes(66).iso())
        env.contracts.add(rec)
        eng.tick(t0.plusMinutes(67))
        assertNull(eng.current)
        assertEquals("REST3", eng.rawData().getJSONArray("pending").getJSONObject(0).getString("code"))
    }

    @Test
    fun saveAndReload() {
        val env = Env()
        val eng = env.engine()
        eng.draw(t0, choose = "III")
        eng.save()
        val again = env.engine()
        assertEquals("III", again.current!!.getString("type"))
    }
}
