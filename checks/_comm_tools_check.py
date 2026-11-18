# -*- coding: utf-8 -*-
"""原生工具报文往返、正文隔离、状态机提交和双端协议一致性。"""

import json
from pathlib import Path
import sys
import time
from datetime import timedelta
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tempstore
import ai_assist
from ai_assist import ai_http
from ai_assist import ai_tools as tools
import comm
from comm.comm_tools import ToolsMixin
import config
from comm.comm_interaction import tension_clean, tension_model_events


def response(protocol, name, args):
    if protocol == "anthropic":
        return {"content": [{"type": "thinking", "thinking": "hidden", "signature": "sig"},
                            {"type": "tool_use", "id": "call_1", "name": name, "input": args}]}
    if protocol == "gemini":
        return {"candidates": [{"content": {"role": "model", "parts": [
            {"functionCall": {"name": name, "args": args, "id": "call_1"}, "thoughtSignature": "signed"}]}}]}
    return {"choices": [{"message": {"role": "assistant", "content": None, "reasoning_content": "hidden",
        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}]}


def call(name, args, protocol="openai"):
    return tools.parse_response(response(protocol, name, args), protocol, lambda: "")


def transport_checks():
    for protocol in ("openai", "anthropic", "gemini"):
        p = {"provider": "custom", "protocol": protocol, "base_url": "http://localhost:1", "model": "test", "api_key": "local"}
        packets = []
        scripted = [response(protocol, "oit_query", {"modules": ["timer", "condition"]}),
                    response(protocol, "oit_reply", {"text": "已读取", "wait_minutes": 2})]

        def send(profile, body, timeout, opener):
            packets.append(body)
            return scripted.pop(0)

        with patch.object(ai_http, "_send", side_effect=send):
            request = ai_assist.chat_messages(p, "system", [{"role": "user", "content": "请查询"}], tools=True)
            assert comm.info_tags(request) == ["timer", "condition"]
            result = json.dumps({"ok": True, "data": "真实结果"}, ensure_ascii=False)
            reply = ai_assist.chat_messages(p, "system", [{"role": "user", "content": "请查询"},
                {"role": "assistant", "content": request}, {"role": "user", "content": result}], tools=True)
        assert comm.clean_reply(reply) == ("已读取", 2)
        assert tools.text_of(reply) == "已读取"
        assert "wait_minutes" not in comm.clean_reply(reply)[0]
        assert packets[0]["tools"] and len(packets) == 2
        if protocol == "openai":
            history = packets[1]["messages"]
            assert history[-1] == {"role": "tool", "tool_call_id": "call_1", "content": result}
            assert history[-2]["reasoning_content"] == "hidden"
            assert packets[0]["parallel_tool_calls"] is False
        elif protocol == "anthropic":
            history = packets[1]["messages"]
            assert history[-1]["content"][0]["tool_use_id"] == "call_1"
            assert history[-2]["content"][0]["signature"] == "sig"
        else:
            history = packets[1]["contents"]
            assert history[-1]["parts"][0]["functionResponse"]["name"] == "oit_query"
            assert history[-1]["parts"][0]["functionResponse"]["id"] == "call_1"
            assert history[-2]["parts"][0]["thoughtSignature"] == "signed"
        native = call("oit_reply", {"text": "[timer] [cut off][9] [burst:end]", "tension": [{"index": 1, "condition": "other", "gain": 4}]}, protocol)
        assert not comm.info_tags(native) and comm.cut_minutes(native) is None
        assert comm.clean_reply(native)[0] == "[timer] [cut off][9] [burst:end]"
        assert tension_model_events(native, 1, True, conditions={"other"}) == [("other", 4)]
        assert "tension" not in tools.args_of(tension_clean(native))

    for name, args in [("unknown", {}), ("oit_reply", {"network_minutes": True}),
                       ("oit_reply", {"network_minutes": 31}), ("oit_reply", {"refuse": True, "text": "正文"}),
                       ("oit_query", {"modules": ["private_data"]}), ("oit_joint_select", {"terms": [1.5]}),
                       ("oit_reply", {"run_shell": "anything"})]:
        try:
            call(name, args)
        except ai_assist.AIError:
            pass
        else:
            raise AssertionError((name, args))

    literal = "[refuse to reply][timer][Yes][accept][apply for network][3]"
    p = {"protocol": "openai", "provider": "custom", "base_url": "http://localhost:1", "model": "test", "api_key": "k"}
    data = {"choices": [{"message": {"content": literal}}]}
    with patch.object(ai_http, "_send", return_value=data):
        reply = ai_assist.chat_messages(p, "system", [{"role": "user", "content": "x"}], tools=True)
    assert tools.text_of(reply) == literal and not comm.is_refusal(reply) and not comm.info_tags(reply)
    assert comm.joint_answer(reply) == "" and comm.net_minutes(reply) is None

    leaked = """<oit_reply
text="Reply text"
tension='[{"index":0,"condition":"rumination","gain":40}]'
valence='[{"index":0,"condition":"docile_reliance","shift":25}]'
wait_minutes="10"/>"""
    for protocol in ("openai", "anthropic", "gemini"):
        if protocol == "openai":
            data = {"choices": [{"message": {"content": leaked}}]}
        elif protocol == "anthropic":
            data = {"content": [{"type": "text", "text": leaked}]}
        else:
            data = {"candidates": [{"content": {"parts": [{"text": "<oit_final>" + leaked + "</oit_final>"}]}}]}
        p["protocol"] = protocol
        with patch.object(ai_http, "_send", return_value=data):
            sanitized = ai_assist.chat_messages(p, "system", [{"role": "user", "content": "x"}], tools=True)
        assert comm.clean_reply(sanitized) == ("Reply text", None)
        assert "tension" not in tools.args_of(sanitized) and "valence" not in tools.args_of(sanitized)
        native = call("oit_reply", {"text": leaked, "wait_minutes": 2}, protocol)
        assert comm.clean_reply(native) == ("Reply text", 2)
        assert comm.net_minutes(sanitized) is None and not comm.info_tags(sanitized)

    for leaked_text, expected in [
        ("<oit_reply wait_minutes='10' text='a > b\n[normal text]' />", "a > b\n[normal text]"),
        ('<oit_reply text="Keep = signs" valence=\'[{"text":"hidden"}]\'></oit_reply>', "Keep = signs"),
        ('Before <oit_reply text="Reply"/> after', "Before Reply after"),
    ]:
        sanitized = tools.parse_response({"choices": [{"message": {"content": leaked_text}}]}, "openai", lambda: leaked_text)
        assert comm.clean_reply(sanitized) == (expected, None)

    p["protocol"] = "openai"
    packets = []
    scripted = [response("openai", "oit_search", {"query": "天气"}), response("openai", "oit_reply", {"text": "查到了"})]
    def search_send(profile, body, timeout, opener):
        packets.append(body)
        return scripted.pop(0)
    with patch.object(ai_http, "_send", side_effect=search_send), patch.object(ai_http, "_web_chat", return_value="来源链接和摘要") as search:
        ai_assist.chat_messages(p, "system", [{"role": "user", "content": "x"}], tools=True, web=True)
        assert search.call_count == 1 and "来源链接和摘要" in packets[1]["messages"][-1]["content"]
    assert all(x["function"]["name"] != "oit_search" for x in tools.declarations("openai", False))
    with patch.object(ai_http, "_send", return_value=response("openai", "oit_search", {"query": "x"})), patch.object(ai_http, "_web_chat") as search:
        try:
            ai_assist.chat_messages(p, "system", [{"role": "user", "content": "x"}], tools=True)
        except ai_assist.AIError:
            pass
        else:
            raise AssertionError("未开通网络仍搜索")
        assert not search.called


def state_checks():
    root = Path(tempstore.mkdtemp("comm-tools"))
    scripted, packets = [], []
    def fake(profile, system, messages, **kwargs):
        assert kwargs.get("tools") is True and "oit_reply" in system
        packets.append([dict(m) for m in messages])
        return scripted.pop(0)
    c = comm.Comm(str(root / "data"), str(root / "personas"), chat_fn=fake)
    c.ai.set_profile("deepseek", api_key="local")
    c.ai.provider = "deepseek"
    c.set_cfg(persona="Assistant.txt", split_send=False)
    c.state_fn = lambda: "idle"
    c._away_kind = lambda: ""
    c.info_fn = lambda name: "参考模板 [waiting for reply]:[4]" if name == "timer" else ""
    def wait():
        until = time.monotonic() + 5
        while c.busy and time.monotonic() < until:
            time.sleep(0.01)
            c.tick()
        assert not c.busy and not c.error, c.error
    scripted.extend([call("oit_query", {"modules": ["timer"]}), call("oit_reply", {"text": "查询完成", "wait_minutes": 2})])
    assert c.send("看看计时器") == ""
    wait()
    assert c.visible()[-1]["text"] == "查询完成" and c.messages[-1]["wait"] == 2 and not c.messages[-1].get("api"), (c.visible()[-1], c.messages[-1])
    assert "wait_minutes" not in c.visible()[-1]["text"] and not str(c.messages[-1].get("api", "")).startswith(tools.PREFIX)
    assert "reference_template" in packets[-1][-1]["content"]
    assert "wait_minutes" in packets[-1][-1]["content"] and "[waiting for reply]" not in packets[-1][-1]["content"]
    assert all(not str(m.get("api", "")).startswith(tools.PREFIX) for m in c.messages)
    assert c.cfg.get("cut", {}) == {} or not c.cut_block()

    for protocol in ("openai", "anthropic", "gemini"):
        p = {"provider": "custom", "protocol": protocol, "base_url": "http://localhost:1", "model": "test", "api_key": "local"}
        image = ai_assist.image_content(p, b"\x89PNG\r\n\x1a\n", "image/png", "请看图")
        body = ai_http._build_body(p, "system", [{"role": "user", "content": image}], 100, 0.0, True)
        tools.apply_body(body, protocol, [{"role": "user", "content": image}])
        content = body["contents"][-1]["parts"] if protocol == "gemini" else body["messages"][-1]["content"]
        assert any("inlineData" in part for part in content) if protocol == "gemini" else any(
            part.get("type") in ("image", "image_url") for part in content)

    c._op_slots = lambda now: [(now + timedelta(hours=1), 36000, "")]
    scripted.extend([call("oit_joint_offer", {}), call("oit_joint_select", {"terms": [1, 2]}), call("oit_reply", {"text": "一起行动吧"})])
    c.send("邀请我一起行动")
    wait()
    assert c._joint()["state"] == "invite" and c.visible()[-1]["text"] == "一起行动吧"
    assert "queued_until_message_visible" in packets[-1][-1]["content"]
    assert "oit_joint_select" in packets[-2][-1]["content"]
    old_epoch = c._epoch
    c.clear()
    c._finish("user", None, call("oit_reply", {"text": "过期", "network_minutes": 3, "cut_minutes": 2}), "", old_epoch)
    assert not c.messages and not c.net_active() and not c.cut_block()

    query = call("oit_query", {"modules": ["timer"]})
    final = call("oit_reply", {"text": "完成"})
    responses = [query] * (config.COMM_TOOL_ROUNDS - 1) + [final]
    _, reply = c._tool_rounds(lambda _: responses.pop(0), [], query, "user", c._epoch)
    assert reply == final and not responses
    try:
        c._tool_rounds(lambda _: query, [], query, "user", c._epoch)
    except ai_assist.AIError:
        pass
    else:
        raise AssertionError("未限制工具轮次")
    c.info_fn = lambda _: (_ for _ in ()).throw(RuntimeError("unavailable"))
    records = []
    def unavailable(messages):
        records.append(json.loads(messages[-1]["content"]))
        return final
    c._tool_rounds(unavailable, [], query, "user", c._epoch)
    assert records[0]["data"]["timer"] == {"available": False, "error": "unavailable"}

    def ask(_):
        raise AssertionError("不应进入下一轮")
    c.hard_block = lambda: True
    try:
        c._tool_rounds(ask, [], call("oit_query", {"modules": ["timer"]}), "user", c._epoch)
    except ai_assist.AIError:
        pass
    else:
        raise AssertionError("硬限制下仍执行工具")
    c.flush()

    c2 = comm.Comm(str(root / "data-delay"), str(root / "personas-delay"), chat_fn=fake)
    c2.ai.set_profile("deepseek", api_key="local")
    c2.ai.provider = "deepseek"
    c2.set_cfg(persona="Assistant.txt", split_send=False)
    c2._away_kind = lambda: ""
    scripted.append("自然语言评判")
    assert c2.send("请评判演奏", reply_delay_seconds=65) == ""
    review_user = [m for m in c2.messages if m["role"] == "me"][-1]
    until = time.monotonic() + 5
    while c2.busy and time.monotonic() < until:
        time.sleep(0.01)
        c2.tick()
    review_reply = [m for m in c2.messages if m["role"] == "ai"][-1]
    assert review_user["reply_after"] >= review_user["ts"] + 65000
    assert review_reply.get("hold") and review_reply["due"] >= review_user["reply_after"]
    synced = c2.sync_doc()[f"m:{c2.pid}:{review_user['uid']}"]
    assert synced["reply_after"] == review_user["reply_after"]
    c2_peer = comm.Comm(str(root / "data-delay-peer"), str(root / "personas-delay-peer"), chat_fn=fake)
    c2_peer.apply_doc({f"m:{c2.pid}:{review_user['uid']}": synced})
    received = next(m for m in c2_peer._state_of(c2.pid)["messages"] if m["uid"] == review_user["uid"])
    assert received["reply_after"] == review_user["reply_after"]
    review_reply["late"] = True
    review_reply["due"] = review_user["reply_after"] + 60000
    c2._away_kind = lambda: ""
    c2._ms = lambda: review_user["reply_after"] - 1000
    c2._release_held()
    assert review_reply["hold"], "late acceleration must not release a performance review early"
    c2._ms = lambda: review_user["reply_after"] + 1000
    c2._release_held()
    assert not review_reply.get("hold")
    c2.flush()

    vision_calls = []
    vision_analysis_calls = []
    old_vision_chat = ai_assist.chat_messages
    ai_assist.chat_messages = lambda profile, system, messages, **kwargs: (
        vision_analysis_calls.append((profile["config_id"], messages)) or "检测到一张图片")
    def vision_fake(profile, system, messages, **kwargs):
        vision_calls.append((profile["config_id"], messages[-1]["content"]))
        if len(vision_calls) == 1:
            raise ai_assist.AIError("http", "服务商返回 400: image input unsupported")
        return "我看到了图片内容"
    c3 = comm.Comm(str(root / "data-vision"), str(root / "personas-vision"), chat_fn=vision_fake)
    c3.ai.set_profile("deepseek", api_key="local")
    c3.ai.provider = "deepseek"
    vision_id = c3.ai.add_config("视觉回退")
    c3.ai.set_profile(vision_id, base_url="http://localhost:1", model="vision-test", api_key="local", protocol="anthropic")
    c3.ai.assign_task("vision", vision_id)
    c3.set_cfg(persona="Assistant.txt", split_send=False)
    c3._away_kind = lambda: ""
    image_data = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    assert c3.send("请看图", image=image_data) == ""
    until = time.monotonic() + 5
    while c3.busy and time.monotonic() < until:
        time.sleep(0.01)
        c3.tick()
    assert [x[0] for x in vision_calls] == ["deepseek", "deepseek"]
    assert len(vision_analysis_calls) == 1 and vision_analysis_calls[0][0] == vision_id
    assert len(vision_analysis_calls[0][1]) == 1 and not c3.error
    assert vision_analysis_calls[0][1][-1]["content"][0]["type"] == "image"
    assert "独立图片分析" in vision_calls[1][1]
    ai_assist.chat_messages = old_vision_chat
    c3.flush()


def music_tool_checks():
    import music_library
    root = Path(tempstore.mkdtemp("music-tool-check"))
    library = music_library.manager(root)
    catalog = library.catalog()
    assert len(catalog) == 20

    class Context(ToolsMixin):
        _epoch = 7
        dir = str(root)
        hard_block = lambda self: False
        log = lambda self, message: None

    replies = [tools.pack(name="oit_music_select", arguments={"id": catalog[6]["id"]}),
               tools.pack(text="我选了梦幻曲", name="oit_reply", arguments={"text": "我选了梦幻曲"})]
    operation, reply = Context()._tool_rounds(lambda _: replies.pop(0), [], tools.pack(name="oit_music_catalog"), "user", 7)
    assert operation == {"type": "music_select", "id": catalog[6]["id"]}
    assert tools.text_of(reply) == "我选了梦幻曲" and library.selection() is None

    generated = {"id": catalog[6]["id"], "title": catalog[6]["title"], "reused": False}
    seen = []
    replies = [tools.pack(name="oit_music_select", arguments={"id": catalog[6]["id"]}),
               tools.pack(name="oit_music_generate_midi", arguments={"id": catalog[6]["id"]}),
               tools.pack(text="MIDI 已生成", name="oit_reply", arguments={"text": "MIDI 已生成"})]
    with patch("music_library.MusicLibrary.generate_midi", return_value=generated):
        operation, reply = Context()._tool_rounds(lambda history: seen.append(history) or replies.pop(0), [],
                                                  tools.pack(name="oit_music_catalog"), "user", 7)
    assert operation == {"type": "music_select", "id": catalog[6]["id"]} and tools.text_of(reply) == "MIDI 已生成"
    assert '"format": "MIDI"' in seen[0][-1]["content"]


if __name__ == "__main__":
    tempstore.session("comm-tools-check")
    transport_checks()
    state_checks()
    music_tool_checks()
    root = Path(__file__).resolve().parent.parent
    for name in ("comm_tools.json", "comm_protocol.txt", "comm_legacy_controls.json"):
        assert (root / "defaults" / name).read_text(encoding="utf-8") == (root / "Android version/app/src/main/resources" / name).read_text(encoding="utf-8")
    protocol = (root / "defaults/comm_protocol.txt").read_text(encoding="utf-8")
    specs = json.loads((root / "defaults/comm_tools.json").read_text(encoding="utf-8"))
    assert {s["name"] for s in specs} >= {"oit_music_catalog", "oit_music_select"}
    tool = next(s for s in specs if s["name"] == "oit_reply")
    assert "聊天只显示text" in protocol and "wait_minutes" in tool["description"] and "不会显示给用户" in tool["description"]
    legacy = "先输入[messages]查询；回复 [Yes]；结尾写 [waiting for reply]:[5]\n[condition]\nmessages:2\n[/condition]\n"
    migrated = comm.native_persona_text(legacy)
    assert "输入[messages]" not in migrated and 'answer="yes"' in migrated and "wait_minutes" in migrated
    assert "[condition]\nmessages:2\n[/condition]" in migrated
    print("comm tools check OK")
