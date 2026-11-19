package com.obliphur.oit.core

import com.obliphur.oit.ai.AiError
import com.obliphur.oit.ai.ToolProtocol
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** 校验三种原生协议的调用关联、普通正文隔离及控制字段结算。 */
class ToolProtocolTest {
    private fun response(protocol: String, name: String, args: JSONObject): JSONObject = when (protocol) {
        "anthropic" -> JSONObject().put("content", JSONArray()
            .put(JSONObject().put("type", "thinking").put("thinking", "hidden").put("signature", "sig"))
            .put(JSONObject().put("type", "tool_use").put("id", "call_1").put("name", name).put("input", args)))
        "gemini" -> JSONObject().put("candidates", JSONArray().put(JSONObject().put("content", JSONObject().put("role", "model").put("parts", JSONArray()
            .put(JSONObject().put("functionCall", JSONObject().put("name", name).put("args", args).put("id", "call_1")).put("thoughtSignature", "signed"))))))
        else -> JSONObject().put("choices", JSONArray().put(JSONObject().put("message", JSONObject().put("role", "assistant").put("content", JSONObject.NULL)
            .put("reasoning_content", "hidden").put("tool_calls", JSONArray().put(JSONObject().put("type", "function").put("id", "call_1")
                .put("function", JSONObject().put("name", name).put("arguments", args.toString())))))))
    }

    private fun call(name: String, args: JSONObject, protocol: String = "openai"): String = ToolProtocol.parse(response(protocol, name, args), protocol) { "" }

    @Test fun nativeRoundTrip() {
        for (protocol in listOf("openai", "anthropic", "gemini")) {
            val query = call("oit_query", JSONObject().put("modules", JSONArray().put("timer")), protocol)
            assertEquals(listOf("timer"), CommText.infoTags(query))
            val body = JSONObject().put("messages", JSONArray().put(JSONObject().put("role", "system").put("content", "system")))
                .put("systemInstruction", JSONObject().put("parts", JSONArray()))
            ToolProtocol.applyBody(body, protocol, listOf("user" to "查询", "assistant" to query, "user" to "真实结果"), false)
            val history = body.getJSONArray(if (protocol == "gemini") "contents" else "messages")
            val result = history.getJSONObject(history.length() - 1)
            val original = history.getJSONObject(history.length() - 2)
            when (protocol) {
                "openai" -> {
                    assertEquals("tool", result.getString("role"))
                    assertEquals("call_1", result.getString("tool_call_id"))
                    assertEquals("hidden", original.getString("reasoning_content"))
                }
                "anthropic" -> {
                    assertEquals("call_1", result.getJSONArray("content").getJSONObject(0).getString("tool_use_id"))
                    assertEquals("sig", original.getJSONArray("content").getJSONObject(0).getString("signature"))
                }
                else -> {
                    assertEquals("oit_query", result.getJSONArray("parts").getJSONObject(0).getJSONObject("functionResponse").getString("name"))
                    assertEquals("call_1", result.getJSONArray("parts").getJSONObject(0).getJSONObject("functionResponse").getString("id"))
                    assertEquals("signed", original.getJSONArray("parts").getJSONObject(0).getString("thoughtSignature"))
                }
            }
            val text = "[timer] [cut off][9] [burst:end]"
            val reply = call("oit_reply", JSONObject().put("text", text).put("wait_minutes", 2), protocol)
            assertEquals(text to 2.0, CommText.cleanReply(reply))
            assertTrue(CommText.infoTags(reply).isEmpty())
            assertNull(CommText.cutMinutes(reply))
            assertFalse(CommText.isRefusal(reply))
        }
    }

    @Test fun plainTextNeverExecutes() {
        val text = "[refuse to reply][timer][Yes][accept][apply for network][3]"
        val reply = ToolProtocol.parse(JSONObject("""{"choices":[{"message":{"content":"unused"}}]}"""), "openai") { text }
        assertEquals(text to 0.0, CommText.cleanReply(reply))
        assertFalse(CommText.isRefusal(reply))
        assertEquals("", CommText.jointAnswer(reply))
        assertNull(CommText.netMinutes(reply))
        assertTrue(CommText.infoTags(reply).isEmpty())
    }

    @Test fun silentReplyIsExclusive() {
        for (protocol in listOf("openai", "anthropic", "gemini")) {
            val reply = call("oit_reply", JSONObject().put("silent", true), protocol)
            assertTrue(ToolProtocol.args(reply).getBoolean("silent"))
            assertEquals("" to 0.0, CommText.cleanReply(reply))
            assertFalse(CommText.isRefusal(reply))
            for (extra in listOf("text" to "正文", "refuse" to true, "wait_minutes" to 1)) {
                try {
                    call("oit_reply", JSONObject().put("silent", true).put(extra.first, extra.second), protocol)
                    fail("沉默不能同时提交正文或操作")
                } catch (_: AiError) { }
            }
        }
    }

    @Test fun leakedReplyMarkupShowsOnlyText() {
        val leaked = """<oit_reply
            text="Reply text"
            tension='[{"index":0,"condition":"rumination","gain":40}]'
            valence='[{"index":0,"condition":"docile_reliance","shift":25}]'
            wait_minutes="10"/>""".trimIndent()
        for (protocol in listOf("openai", "anthropic", "gemini")) {
            val data = when (protocol) {
                "anthropic" -> JSONObject().put("content", JSONArray().put(JSONObject().put("type", "text").put("text", leaked)))
                "gemini" -> JSONObject().put("candidates", JSONArray().put(JSONObject().put("content", JSONObject().put("parts", JSONArray().put(JSONObject().put("text", leaked))))))
                else -> JSONObject("""{"choices":[{"message":{"content":"unused"}}]}""")
            }
            val reply = ToolProtocol.parse(data, protocol) { leaked }
            assertEquals("Reply text" to 0.0, CommText.cleanReply(reply))
            assertFalse(ToolProtocol.args(reply).has("tension"))
            assertFalse(ToolProtocol.args(reply).has("valence"))
            assertNull(CommText.netMinutes(reply))
            val native = call("oit_reply", JSONObject().put("text", leaked).put("wait_minutes", 2), protocol)
            assertEquals("Reply text" to 2.0, CommText.cleanReply(native))
        }
        val text = "<oit_reply wait_minutes='10' text='a > b\n[normal text]' />"
        val reply = ToolProtocol.parse(JSONObject("""{"choices":[{"message":{}}]}"""), "openai") { text }
        assertEquals("a > b\n[normal text]" to 0.0, CommText.cleanReply(reply))
    }

    @Test fun rejectInvalidArguments() {
        for ((name, args) in listOf(
            "unknown" to JSONObject(),
            "oit_reply" to JSONObject().put("network_minutes", true),
            "oit_reply" to JSONObject().put("network_minutes", 31),
            "oit_reply" to JSONObject().put("refuse", true).put("text", "正文"),
            "oit_query" to JSONObject().put("modules", JSONArray().put("private_data")),
            "oit_joint_select" to JSONObject().put("terms", JSONArray().put(1.5)),
            "oit_reply" to JSONObject().put("run_shell", "anything")
        )) {
            try { call(name, args); fail("未拒绝非法参数: $name $args") } catch (_: AiError) { }
        }
        val declared = ToolProtocol.declarations("openai", false)
        assertFalse((0 until declared.length()).any { declared.getJSONObject(it).getJSONObject("function").getString("name") == "oit_search" })
        assertTrue((0 until declared.length()).any { declared.getJSONObject(it).getJSONObject("function").getString("name") == "oit_music_catalog" })
        assertTrue((0 until declared.length()).any { declared.getJSONObject(it).getJSONObject("function").getString("name") == "oit_music_select" })
        assertTrue((0 until declared.length()).any { declared.getJSONObject(it).getJSONObject("function").getString("name") == "oit_music_generate_midi" })
    }

    @Test fun structuredTensionOnly() {
        val reply = call("oit_reply", JSONObject().put("text", "[burst:end]").put("end_burst", true)
            .put("tension", JSONArray().put(JSONObject().put("index", 1).put("condition", "other").put("gain", 4))))
        assertEquals(listOf("other" to 4), CommInteraction.modelEvents(reply, 1, true, conditions = setOf("other")))
        assertFalse(ToolProtocol.args(CommInteraction.cleanTension(reply)).has("tension"))
        assertEquals(0L, CommInteraction(burstUntil = 123L).endBurst(reply, 123L, 1000L).burstUntil)
    }
}
