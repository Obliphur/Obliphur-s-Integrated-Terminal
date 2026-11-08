package com.obliphur.oit.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.LocalDate
import java.time.LocalDateTime

/** 日程引擎 (MECHANICS 24.11). 对应桌面版 routine.py 的 _selftest; 下面的期望值是桌面版算出来的 (电脑和手机必须一样). */
class RoutineTest {
    // 1.0.359 起床波动改成了 [-20,0]; 这里仍用 [-20,10] 测 (期望值是按它算的, 电脑和手机必须一样), 新范围见 CommTest.coAlarm
    private val text = BuiltinPersonas.ALL.getValue(BuiltinPersonas.ROUTINE).also { assertTrue("睡眠结束时间波动:[-20,0]" in it) }.replace("睡眠结束时间波动:[-20,0]", "睡眠结束时间波动:[-20,10]")
    private val spec = Routine.parse(text)!!

    private fun fmt(s: RtSeg) = "${s.kind} %02d %02d:%02d-%02d %02d:%02d ${s.label}${if (s.gap) " gap" else ""}".format(
        s.start.dayOfMonth, s.start.hour, s.start.minute, s.end.dayOfMonth, s.end.hour, s.end.minute)

    @Test
    fun parseAndRules() {
        assertEquals(390, spec.start)
        assertEquals("学习(高考内容)", spec.focusLabel)
        assertEquals(listOf(30.0, 300.0, 30.0, 30.0, 300.0, 30.0, 30.0, 240.0, 30.0), spec.items.map { it.dur })
        assertEquals(listOf(100.0, 100.0, 50.0), spec.items.filter { it.isLoop }.map { it.focus })
        assertEquals(-3.0 to 2.0, spec.fluct["focus_start"]); assertEquals(-2.0 to 3.0, spec.fluct["focus_end"])
        assertEquals(0.0 to 5.0, spec.fluct["sleep_start"]); assertEquals(-20.0 to 10.0, spec.fluct["sleep_end"])
        assertEquals(0.0 to 5.0, spec.fluct["rest_end"])                                                         // 1.0.400: 每个循环最后那次休息比循环的结束晚 [0,5] 分钟
        assertEquals(0.0 to 0.0, Routine.parse(text.replace("循环末尾休息结束时间波动:[0,5]", ""))!!.fluct["rest_end"])   // 没写 = 不晚
        assertEquals(1.0 to 7.0, Routine.parse(text.replace("循环末尾休息结束时间波动:[0,5]", "循环末尾休息结束时间波动:[1,7]"))!!.fluct["rest_end"])   // 改了文件自动识别
        assertEquals(listOf("DESKTOP-CLAUDIA", "C1999W"), spec.devices)
        assertEquals(setOf(0, 1), spec.windows.keys)
        assertEquals(20 to 30, spec.diff)
        assertEquals(listOf("无言为真", "持久战"), spec.skipTerms)
        assertEquals(listOf("通道阻塞", "细化规范", "乐理之灾", "未知变量"), spec.badTerms)
        assertTrue("联网时间理由列表" in spec.base && "整点记录专注填写列表" !in spec.base)
        // IV: 没写概率的项平分剩下的; X% 随聊天变
        val foc = Routine.resolveList(spec.lists.getValue("整点记录专注填写列表"))
        assertEquals("英语" to 30.0, foc[0]); assertTrue(foc.drop(1).all { Math.abs(it.second - 14.0) < 1e-9 } && foc.size == 6)
        val rest = Routine.resolveList(spec.lists.getValue("整点记录休息填写列表"), 20.0)
        assertEquals("聊天" to 20.0, rest[0]); assertTrue(rest.drop(1).all { Math.abs(it.second - 20.0) < 1e-9 })
        assertEquals(25.0, Routine.resolveList(spec.lists.getValue("整点记录休息填写列表"), 0.0)[1].second, 1e-9)
        // V: 聊天概率 (细化规范的半小时格按比例)
        assertEquals(0.0, Routine.chatPct(0, 60.0), 0.0); assertEquals(50.0, Routine.chatPct(20, 60.0), 0.0)
        assertEquals(50.0, Routine.chatPct(40, 60.0), 0.0); assertEquals(25.0, Routine.chatPct(10, 60.0), 0.0)
        assertEquals(50.0, Routine.chatPct(10, 30.0), 0.0); assertEquals(25.0, Routine.chatPct(5, 30.0), 0.0)
        // VI: 基准数据 ±2%, 加起来是 100% 的归一
        val reasons = spec.lists.getValue("联网时间理由列表")
        for (day in 0 until 30) {
            val fl = Routine.fluctList(reasons, "s|$day", true)
            assertEquals(100.0, fl.sumOf { it.second }, 1e-6)
        }
        val w = Routine.fluctList(spec.lists.getValue("设备-1:窗口使用时间"), "s|w", true)
        val w0 = Routine.resolveList(spec.lists.getValue("设备-1:窗口使用时间"))
        assertTrue(w.zip(w0).all { (a, b) -> Math.abs(a.second / b.second - 1) <= 0.045 })   // 名单加起来 100% 时再归一, 最多约 ±4%
        assertEquals(Routine.resolveList(spec.lists.getValue("整点记录专注填写列表")), Routine.fluctList(spec.lists.getValue("整点记录专注填写列表"), "s|x", false))
    }

    @Test
    fun matchesDesktop() {
        assertEquals(0.17557225990430197, Routine.u01("hello"), 0.0)
        assertEquals(0.8216104829254379, Routine.u01("t|wake|2026-03-01"), 0.0)
        assertEquals(0.37812505941008207, Routine.u01("t|fs|2026-03-01|0"), 0.0)
        assertEquals(listOf(-19, 1, -3), listOf(Routine.rint("a|b", -20.0, 10.0), Routine.rint("c", 0.0, 5.0), Routine.rint("d", -3.0, 2.0)))
        val plan = Routine.dayPlan(spec, LocalDate.of(2026, 3, 1), "t")
        assertEquals(listOf(
            "idle 01 06:35-01 07:04 洗漱,吃早饭",
            "focus 01 07:04-01 08:45 专注",
            "rest 01 08:45-01 09:21 休息",
            "focus 01 09:21-01 11:05 专注",
            "rest 01 11:05-01 11:39 休息",
            "focus 01 11:39-01 11:58 专注",
            "rest 01 11:58-01 12:07 休息",
            "idle 01 12:07-01 12:37 午饭",
            "idle 01 12:37-01 13:05 空闲",
            "focus 01 13:05-01 14:47 专注",
            "rest 01 14:47-01 15:22 休息",
            "focus 01 15:22-01 17:03 专注",
            "rest 01 17:03-01 17:41 休息",
            "focus 01 17:41-01 18:00 专注",
            "rest 01 18:00-01 18:09 休息",
            "idle 01 18:09-01 18:39 晚饭",
            "idle 01 18:39-01 19:06 空闲",
            "focus 01 19:06-01 19:57 专注",
            "rest 01 19:57-01 20:16 休息",
            "focus 01 20:16-01 21:08 专注",
            "rest 01 21:08-01 21:18 休息",
            "focus 01 21:18-01 22:10 专注",
            "rest 01 22:10-01 22:27 休息",
            "focus 01 22:27-01 23:00 专注",
            "rest 01 23:00-01 23:14 休息",
            "idle 01 23:14-01 23:44 特殊空闲",
            "idle 01 23:44-01 23:45 空闲 gap",
            "sleep 01 23:45-02 06:33 睡眠"), plan.map { fmt(it) })
        val dir = Files.createTempDirectory("rt").toFile()
        val st = RoutineStore(File(dir, "x.json"), spec, "t")
        val now = LocalDateTime.of(2026, 3, 10, 10, 20)
        val fn = { a: LocalDateTime, b: LocalDateTime -> Routine.planBetween(spec, a, b, "t") }
        st.fillTimeline(now, fn)
        st.fillCells(now, { _, _ -> 0 })
        assertEquals(mapOf("2026-03-09T06:00" to "洗漱,吃早饭", "2026-03-09T08:00" to "打扫", "2026-03-09T09:00" to "地理", "2026-03-09T12:00" to "阅读", "2026-03-09T13:00" to "英语", "2026-03-09T20:00" to "英语", "2026-03-09T23:00" to "睡眠", "2026-03-10T03:00" to "睡眠", "2026-03-10T07:00" to "数学", "2026-03-10T09:00" to "语文"), listOf("2026-03-09T06:00", "2026-03-09T08:00", "2026-03-09T09:00", "2026-03-09T12:00", "2026-03-09T13:00", "2026-03-09T20:00", "2026-03-09T23:00", "2026-03-10T03:00", "2026-03-10T07:00", "2026-03-10T09:00").associateWith { st.cells.getValue(it).second })
        val st2 = RoutineStore(File(dir, "y.json"), spec, "t")
        st2.fillTimeline(now, fn)
        st2.fillCells(now, { _, _ -> 15 })
        assertEquals(listOf("睡眠", "睡眠", "睡眠", "睡眠", "睡眠", "睡眠", "洗漱,吃早饭", "语文", "阅读", "地理", "政治", "阅读", "聊天", "英语", "打扫", "英语", "数学", "聊天", "聊天", "语文", "英语", "政治", "语文", "睡眠"), st2.cells.keys.sorted().filter { it.startsWith("2026-03-09") }.map { st2.cells.getValue(it).second })
        val day = LocalDate.of(2026, 3, 9)
        fun near(exp: Map<String, Double>, got: Map<String, Double>) {
            assertEquals(exp.keys, got.keys)
            for ((k, v) in exp) assertEquals(k, v, got.getValue(k), 1e-4)
        }
        near(mapOf("查资料" to 61.220054, "下载资源" to 20.487489, "娱乐" to 20.042457), st.netByReason(day))
        near(mapOf("chrome" to 11020.857202, "claude" to 3766.146247, "OIT" to 7460.917659, "anki" to 1859.972547, "cloudmusic" to 1862.233708, "" to 10929.872636), st.windowSeconds(day, 0))
        near(mapOf("微信" to 1244.097178, "OIT" to 4841.127025, "词典" to 4848.867846, "相册" to 489.608917, "anki" to 3160.968589, "" to 9835.330446), st.windowSeconds(day, 1))
        assertEquals(615.0, st.dayMinutes(day, setOf("focus")), 1e-6)
        assertEquals(listOf("00:00 睡眠", "01:00 睡眠", "02:00 睡眠", "03:00 睡眠", "04:00 睡眠", "05:00 睡眠", "06:00 洗漱,吃早饭", "07:00 数学", "08:00 语文", "09:00 语文"), st.logLines(now))
        val recs = Routine.contractsBefore(spec, LocalDateTime.of(2026, 4, 1, 0, 0), "t", days = 60).take(3)
        assertEquals(listOf(
            Triple("2026-03-29T12:58:00", 24, listOf("retreat2", "deep2", "tired2", "short2", "seal", "info3", "erode1", "suppress", "jam", "quiet", "game", "video", "gaze", "scar", "move", "confine", "fog")),
            Triple("2026-03-03T12:04:00", 21, listOf("retreat1", "deep1", "tired3", "short1", "seal", "info3", "erode1", "suppress", "jam", "quiet", "game", "video", "gaze", "scar", "confine", "fog")),
            Triple("2026-02-27T16:55:00", 23, listOf("last", "deep1", "tired3", "short2", "seal", "info3", "erode2", "suppress", "jam", "quiet", "game", "video", "scar", "fog"))), recs.map { r -> Triple(r.getString("end"), r.getInt("level"), (0 until r.getJSONArray("terms").length()).map { r.getJSONArray("terms").getString(it) }) })
    }

    @Test
    fun dayPlanInvariants() {
        var sawEarly = false
        for (k in 0 until 60) {
            val d = LocalDate.of(2026, 3, 1).plusDays(k.toLong())
            val plan = Routine.dayPlan(spec, d, "t")
            val wk = Routine.wakeAt(spec, d, "t")
            assertTrue(wk >= d.atStartOfDay().plusMinutes(370) && wk <= d.atStartOfDay().plusMinutes(400))
            assertEquals(wk, plan[0].start); assertEquals("洗漱,吃早饭", plan[0].label)
            for (i in 0 until plan.size - 1) assertEquals(plan[i].end, plan[i + 1].start)           // 首尾相接
            assertEquals("sleep", plan.last().kind); assertEquals(Routine.wakeAt(spec, d.plusDays(1), "t"), plan.last().end)
            val late = spec.items.withIndex().filter { it.value.isLoop }.sumOf { Routine.rint("t|le|$d|${it.index}", 0.0, 5.0) }     // 三个循环最后一次休息各自晚结束的分钟, 后面的日程跟着推
            val actEnd = wk.plusMinutes(spec.items.sumOf { it.dur }.toLong() + late)
            assertTrue(plan.last().start >= actEnd && plan.last().start <= actEnd.plusMinutes(5))   // 睡眠开始 = 做完最后一项 + [0,5]
            if (plan.last().start > actEnd) { assertTrue(plan[plan.size - 2].gap && plan[plan.size - 2].kind == "idle") }    // II: 被延迟的这段算空闲
            val foc = plan.filter { it.kind == "focus" }
            assertEquals(10, foc.size)
            // 循环总以休息结束, 休息的末尾 = 循环的末尾 + [0,5] 分钟, 下一项活动紧接着从那里开始
            var curNom = wk
            for ((i, it) in spec.items.withIndex()) {
                var nomEnd = curNom.plusMinutes(it.dur.toLong())
                if (it.isLoop) {
                    val ls = plan.filter { s -> s.loop == i }
                    assertTrue(ls.last().kind == "rest" && ls.first().kind == "focus")
                    val dl = java.time.Duration.between(nomEnd, ls.last().end).toMinutes()
                    assertEquals(Routine.rint("t|le|$d|$i", 0.0, 5.0).toLong(), dl)
                    nomEnd = ls.last().end
                    assertTrue(plan.any { s -> s.start == nomEnd && s.loop < 0 })
                }
                curNom = nomEnd
            }
            assertFalse(plan.dropLast(2).any { it.gap })                                      // 循环里不留空档 (专注晚开始 → 前一段变长)
            val nominal = wk.plusMinutes(30)
            val diff = java.time.Duration.between(nominal, foc[0].start).toMinutes()
            assertTrue(diff in -3..2)
            sawEarly = sawEarly || foc[0].start < nominal
        }
        assertTrue(sawEarly)
        val p0 = Routine.dayPlan(spec, LocalDate.of(2026, 3, 1), "t", fluct = false)
        // 循环以休息结束: 满轮排完, 最后一轮专注提前结束, 休息随它重新算, 末尾对上循环的末尾
        assertEquals(listOf("idle" to 30L, "focus" to 100L, "rest" to 38L, "focus" to 100L, "rest" to 38L, "focus" to 19L, "rest" to 5L, "idle" to 30L, "idle" to 30L),
            p0.take(9).map { it.kind to java.time.Duration.between(it.start, it.end).toMinutes() })
        assertEquals(p0[0].end.plusMinutes(300), p0[6].end); assertEquals("午饭", p0[7].label)
        assertEquals(listOf("focus" to 50L, "rest" to 16L, "focus" to 50L, "rest" to 16L, "focus" to 50L, "rest" to 16L, "focus" to 32L, "rest" to 10L),
            p0.filter { it.loop == 7 }.map { it.kind to java.time.Duration.between(it.start, it.end).toMinutes() })
        fun pp(total: Int, n: Int) = Routine.loopPieces(total * 60L, n * 60L).map { it.first to it.second / 60.0 }
        assertEquals(listOf("focus" to 100.0, "rest" to 38.0, "focus" to 100.0, "rest" to 38.0), pp(276, 100))
        assertEquals(listOf("focus" to 100.0, "rest" to 38.0, "focus" to 47.0, "rest" to 15.0), pp(200, 100))
        assertEquals(listOf("focus" to 74.0, "rest" to 26.0), pp(100, 100)); assertEquals(listOf("focus" to 16.0, "rest" to 4.0), pp(20, 100))
        assertEquals(listOf("focus" to 100.0, "rest" to 39.0), pp(139, 100)); assertEquals(listOf("focus" to 1.0), pp(1, 100))
        for (tot in 2 until 700 step 7) {
            val ps = pp(tot, 100)
            assertTrue(ps.last().first == "rest" && Math.abs(ps.sumOf { it.second } - tot) < 1e-6 && ps.filter { it.first == "focus" }.all { it.second <= 100.0 })
        }
        // 联合行动之后: 休息结束落在循环里 → 立即开始专注; 落在吃饭里不影响
        val segs = Routine.planBetween(spec, LocalDateTime.of(2026, 3, 2, 6, 0), LocalDateTime.of(2026, 3, 2, 23, 0), "t")
        val rs = segs.first { it.kind == "rest" }
        val t = rs.start.plusMinutes(10)
        val sh = Routine.shiftForJoint(segs, t, t.plusMinutes(1))
        val cur = Routine.statusIn(sh, t.plusMinutes(1))!!
        assertTrue(cur.kind == "focus" && cur.start == t)
        val meal = segs.first { it.kind == "idle" && it.label == "午饭" }
        assertEquals(segs, Routine.shiftForJoint(segs, meal.start.plusMinutes(5), meal.start.plusMinutes(6)))
        // 合约记录: 不碰不选择 / 违规的词条
        val banned = Routine.bannedIds(spec)
        assertTrue(banned.containsAll(listOf("long1", "long2", "long3", "silent", "chan1", "chan2", "fine", "music", "unknown")) && "tired1" !in banned)
    }

    @Test
    fun storeSaveAndFill() {
        val dir = Files.createTempDirectory("rt").toFile()
        val file = File(dir, "routine_data.json")
        val fn = { a: LocalDateTime, b: LocalDateTime -> Routine.planBetween(spec, a, b, "t") }
        val st = RoutineStore(file, spec, "t")
        val now = LocalDateTime.of(2026, 3, 10, 10, 20)
        st.fillTimeline(now, fn)
        assertEquals(now, st.upto)
        assertTrue(Math.abs(st.minutes(LocalDateTime.of(2026, 3, 3, 0, 0), now) - java.time.Duration.between(LocalDateTime.of(2026, 3, 3, 0, 0), now).toMinutes()) < 1e-6)   // 时间线没有缺口
        st.fillCells(now, { _, _ -> 0 })
        val lines = st.logLines(now)
        assertTrue(lines.isNotEmpty() && lines.none { it.startsWith("10:") })                       // 没结束的格子不给
        val later = LocalDateTime.of(2026, 3, 13, 9, 40)                                           // 3 天没人调用: 自动补齐
        st.fillTimeline(later, fn); st.fillCells(later, { _, _ -> 0 })
        assertTrue(st.cells.getValue("2026-03-11T08:00").second.isNotEmpty() && st.cells.getValue("2026-03-11T03:00").second == "睡眠")
        assertTrue(st.netByReason(later.toLocalDate().plusDays(1)).isEmpty())                        // 还没发生: 没有数据
        Store.write(file, st.payload())
        val st4 = RoutineStore(file, spec, "t")
        assertEquals(st.upto, st4.upto); assertEquals(st.cells, st4.cells); assertEquals(st.tl.map { it.toList() }, st4.tl.map { it.toList() })
        assertTrue(RoutineStore(file, spec, "other").tl.isEmpty())                                   // 种子不同的存档不读
        // 聊天概率: 一小时 40 条 → 休息格里 "聊天" 的概率 50%
        val s0 = RoutineStore(File(dir, "a.json"), spec, "chat"); val fn2 = { a: LocalDateTime, b: LocalDateTime -> Routine.planBetween(spec, a, b, "chat") }
        s0.fillTimeline(LocalDateTime.of(2026, 3, 12, 0, 0), fn2); s0.fillCells(LocalDateTime.of(2026, 3, 12, 0, 0), { _, _ -> 0 })
        assertTrue(s0.cells.values.none { it.second == "聊天" })
        val s1 = RoutineStore(File(dir, "b.json"), spec, "chat")
        s1.fillTimeline(LocalDateTime.of(2026, 3, 12, 0, 0), fn2); s1.fillCells(LocalDateTime.of(2026, 3, 12, 0, 0), { _, _ -> 40 })
        val chats = s1.cells.values.count { it.second == "聊天" }
        val rests = s1.cells.values.count { it.second in listOf("聊天", "弹琴", "阅读", "打扫", "散步") }
        assertTrue(rests > 0 && chats.toDouble() / rests > 0.25)
    }
}
