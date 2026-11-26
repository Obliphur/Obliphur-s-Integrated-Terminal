# -*- coding: utf-8 -*-
"""原生工具报文往返、正文隔离、状态机提交和双端协议一致性。"""

import json
from pathlib import Path
import sys
import time
from datetime import datetime, timedelta
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
        normalized = tools.unpack(call("oit_reading_offer", {"request_statement": "我们现在一起读吧。"}, protocol))["arguments"]
        assert normalized == {"free_statement": "", "request_statement": "我们现在一起读吧。"}
        normalized = tools.unpack(call("oit_reading_offer", {"free_statement": "我现在有空。"}, protocol))["arguments"]
        assert normalized == {"free_statement": "我现在有空。", "request_statement": ""}
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
            assert packets[0]["parallel_tool_calls"] is False
        native = call("oit_reply", {"text": "[timer] [cut off][9] [burst:end]", "tension": [{"index": 1, "condition": "other", "gain": 4}]}, protocol)
        assert not comm.info_tags(native) and comm.cut_minutes(native) is None
        assert comm.clean_reply(native)[0] == "[timer] [cut off][9] [burst:end]"
        assert tension_model_events(native, 1, True, conditions={"other"}) == [("other", 4)]
        assert "tension" not in tools.args_of(tension_clean(native))

    for name, args in [("unknown", {}), ("oit_reply", {"network_minutes": True}),
                       ("oit_reply", {"network_minutes": 31}), ("oit_reply", {"refuse": True, "text": "正文"}),
                       ("oit_reply", {"silent": True, "text": "正文"}), ("oit_reply", {"silent": True, "refuse": True}),
                       ("oit_reply", {"silent": True, "wait_minutes": 1}),
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


def silence_checks():
    """沉默不生成气泡、不重试、不结算情绪；重启与同步后仍记为已处理。"""
    root = Path(tempstore.mkdtemp("comm-silence"))
    packets = []
    def fake(*args, **kwargs):
        packets.append(args)
        return call("oit_reply", {"silent": True})
    c = comm.Comm(str(root / "data"), str(root / "personas"), chat_fn=fake)
    c.ai.set_profile("deepseek", api_key="local")
    c.ai.provider = "deepseek"
    c.set_cfg(persona="Assistant.txt")
    c.state_fn = lambda: "idle"
    c._away_kind = lambda: ""
    assert c.send("好的") == ""
    until = time.monotonic() + 5
    while c.busy and time.monotonic() < until:
        time.sleep(0.01)
        c.tick()
    assert not c.busy and not c.error and len(packets) == 1
    message = c.messages[-1]
    assert message["role"] == "me" and message["no_reply"] and not c.unanswered()
    assert not c.cfg["retry"] and not c.cfg["interaction"]["pending"]
    assert len(c.visible()) == 1 and c.unread == 0
    key = f"m:{c.pid}:{message['uid']}"
    doc = c.sync_doc()
    assert doc[key]["no_reply"] is True
    peer = comm.Comm(str(root / "peer"), str(root / "peer-personas"))
    old_message = dict(doc[key]); old_message.pop("no_reply")
    peer.apply_doc({key: old_message})
    peer.apply_doc({key: doc[key]})
    peer.apply_doc({key: old_message})
    assert peer._state_of(c.pid)["messages"][-1]["no_reply"], "已有消息合并沉默标记，旧设备不能撤回"
    c.flush()
    restored = comm.Comm(str(root / "data"), str(root / "personas"), chat_fn=fake)
    assert not restored.unanswered() and not restored.cfg["retry"], "重启不重新请求客套话"
    clock = c.now()
    c.now = lambda: clock + timedelta(minutes=11)
    c.tick()
    assert len(packets) == 1, "沉默不进入十分钟拒绝重试"
    newer = c._add("me", "我有一个问题")
    assert c.unanswered() and not newer.get("no_reply"), "新消息仍需处理"
    c._finish("user", None, call("oit_reply", {"silent": True}), "", c._epoch - 1)
    assert not newer.get("no_reply"), "过期沉默不能处理新消息"
    c._finish("user", None, call("oit_reply", {"silent": True}), "network error", c._epoch)
    assert not newer.get("no_reply"), "失败沉默不能处理新消息"
    c._finish("user", None, call("oit_reply", {"silent": True}), "", c._epoch)
    assert c.unanswered() and not newer.get("no_reply"), "旧请求的沉默不能吞掉请求后新增的用户消息"
    c.flush(); restored.flush(); peer.flush()


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

    image_calls = []
    def image_fake(profile, system, messages, **kwargs):
        image_calls.append((profile["config_id"], messages[-1]["content"]))
        return "我看到了图片内容"
    c3 = comm.Comm(str(root / "data-vision"), str(root / "personas-vision"), chat_fn=image_fake)
    c3.ai.set_profile("deepseek", api_key="local")
    c3.ai.provider = "deepseek"
    c3.set_cfg(persona="Assistant.txt", split_send=False)
    c3._away_kind = lambda: ""
    image_data = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    assert c3.send("请看图", image=image_data) == ""
    until = time.monotonic() + 5
    while c3.busy and time.monotonic() < until:
        time.sleep(0.01)
        c3.tick()
    assert [x[0] for x in image_calls] == ["deepseek"]
    assert len(image_calls) == 1 and not c3.error
    c3.flush()


def music_tool_checks():
    import music_library
    root = Path(tempstore.mkdtemp("music-tool-check"))
    library = music_library.manager(root)
    catalog = library.catalog()
    assert len(catalog) == len(library.manifest) - 4
    intimate_id = next(item["id"] for item in library.manifest if item.get("relationship_required"))

    class Context(ToolsMixin):
        _epoch = 7
        dir = str(root)
        _related = False
        _valence = 0
        hard_block = lambda self: False
        log = lambda self, message: None
        state_fn = lambda self: "idle"
        persona_name = lambda self: "Claudia.txt"
        related = lambda self: self._related
        offense_blocked = lambda self: False
        _interaction_prepare = lambda self: {"valence": self._valence}

    access = Context()
    access._related, access._valence = True, 70
    visible = library.catalog("Claudia.txt", access.related(), access._interaction_prepare()["valence"])
    assert intimate_id in {item["id"] for item in visible}
    access._valence = 69.99
    assert intimate_id not in {item["id"] for item in library.catalog("Claudia.txt", access.related(), access._interaction_prepare()["valence"])}

    # 目录通过后状态若跌破门槛，后续选曲仍被拒绝。
    access._valence = 70
    select_after_gate = [tools.pack(name="oit_music_catalog"), tools.pack(name="oit_music_select", arguments={"id": intimate_id}),
                         tools.pack(text="不能选", name="oit_reply", arguments={"text": "不能选"})]
    calls = 0
    def drop_valence(history):
        nonlocal calls
        calls += 1
        if calls == 1:
            access._valence = 69.99
        return select_after_gate.pop(0)
    operation, _ = access._tool_rounds(drop_valence, [], select_after_gate.pop(0), "user", 7)
    assert operation is None

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

    replies = [tools.pack(name="oit_music_select", arguments={"id": catalog[6]["id"]}),
               tools.pack(name="oit_music_perform", arguments={"id": catalog[6]["id"]}),
               tools.pack(text="我来演奏", name="oit_reply", arguments={"text": "我来演奏"})]
    access._valence = 30
    ready = {"id": catalog[6]["id"], "title": catalog[6]["title"], "path": str(root / "song.mp3")}
    with patch("music_library.MusicLibrary.prepare_performance", return_value=ready):
        operation, reply = access._tool_rounds(lambda _: replies.pop(0), [],
                                                  tools.pack(name="oit_music_catalog"), "user", 7)
    assert operation == {"type": "music_perform", "id": catalog[6]["id"], "path": ready["path"]}
    assert tools.text_of(reply) == "我来演奏"

    # 没有选曲、主动消息或专注时均不能生成演奏音频。
    for kind, context in (("user", Context()), ("proactive", Context()), ("user", Context())):
        context._valence = 30
        if kind == "user" and library.selection("Claudia.txt"):
            context.state_fn = lambda: "focus"
        seen.clear()
        replies = [tools.pack(name="oit_reply", arguments={"text": "暂时不能演奏"})]
        with patch("music_library.MusicLibrary.prepare_performance") as prepare:
            context._tool_rounds(lambda history: seen.append(history) or replies.pop(0), [],
                                 tools.pack(name="oit_music_perform", arguments={"id": catalog[6]["id"]}), kind, 7)
        assert not prepare.called and '"ok": false' in seen[0][-1]["content"]
        library.select(catalog[6]["id"], "Claudia.txt")

    access._valence = 29.99
    replies = [tools.pack(name="oit_music_perform", arguments={"id": catalog[6]["id"]}),
               tools.pack(text="现在不想弹", name="oit_reply", arguments={"text": "现在不想弹"})]
    seen.clear()
    with patch("music_library.MusicLibrary.prepare_performance") as prepare:
        access._tool_rounds(lambda history: seen.append(history) or replies.pop(0), [],
                            tools.pack(name="oit_music_catalog"), "user", 7)
    assert not prepare.called and "演奏意愿门槛" in seen[0][-1]["content"]
    access._valence = 70
    access.offense_blocked = lambda: True
    seen.clear()
    replies = [tools.pack(text="尚未原谅", name="oit_reply", arguments={"text": "尚未原谅"})]
    with patch("music_library.MusicLibrary.prepare_performance") as prepare:
        access._tool_rounds(lambda history: seen.append(history) or replies.pop(0), [],
                           tools.pack(name="oit_music_perform", arguments={"id": catalog[6]["id"]}), "user", 7)
    assert not prepare.called and "尚未原谅" in seen[0][-1]["content"]


def request_attachment_checks():
    """请求附件随下一条消息提交；用户发起共读须经人设明确同意。"""
    root = Path(tempstore.mkdtemp("comm-request-attachments"))
    packets, replies = [], []
    def fake(_profile, _system, messages, **_kwargs):
        packets.append([dict(m) for m in messages])
        return replies.pop(0)
    c = comm.Comm(str(root / "data"), str(root / "personas"), chat_fn=fake)
    c.ai.set_profile("deepseek", api_key="local")
    c.ai.provider = "deepseek"
    c.set_cfg(persona="Assistant.txt", split_send=False)
    c.state_fn = lambda: "idle"
    c._away_kind = lambda: ""
    opened = []
    c.on_coread_open = lambda: opened.append("opened")
    replies.append(call("oit_reply", {"text": "好。", "answer": "no"}))
    assert c.toggle_request_draft("coread") == ""
    assert c.toggle_request_draft("share") == ""
    assert c.request_draft_labels()
    assert c.send("") == ""
    until = time.monotonic() + 5
    while c.busy and time.monotonic() < until:
        time.sleep(0.01); c.tick()
    assert not c.busy and not c.error, c.error
    assert opened == [], "拒绝共读后不得打开阅读室"
    assert not c.share_on(), "share data 仍须明确同意"
    assert not any(c.request_draft.values()) and not c.request_draft_labels()
    sent = next(m["content"] for m in packets[0] if m["role"] == "user")
    assert "共同阅读" in sent and "share data" in sent and "只有明确同意后" in sent
    assert c.visible()[0]["text"] == "🙈 share data\n📚 共读"

    c.set_music_review_draft("请评判演奏。", 0)
    assert c.request_draft_labels() == ["🎼 演奏评判"]
    assert c.send("这是我的演奏") == ""
    until = time.monotonic() + 5
    while c.busy and time.monotonic() < until:
        time.sleep(0.01); c.tick()
    message = c.visible()[-1]["text"]
    assert message == "🎼 演奏评判\n这是我的演奏"
    assert "请评判演奏。" in next(m["api"] for m in reversed(c.messages) if m["role"] == "me")

    replies.append(call("oit_reply", {"text": "可以。", "answer": "yes"}))
    assert c.toggle_request_draft("coread") == ""
    assert c.send("") == ""
    until = time.monotonic() + 5
    while c.busy and time.monotonic() < until:
        time.sleep(0.01); c.tick()
    assert not c.busy and not c.error, c.error
    assert opened == ["opened"], "只有明确同意后才打开共读室"

    c.on_share_open = lambda: opened.append("share")
    replies.append(call("oit_reply", {"text": "可以。", "answer": "yes"}))
    assert c.toggle_request_draft("share") == ""
    assert c.send("") == ""
    until = time.monotonic() + 5
    while c.busy and time.monotonic() < until:
        time.sleep(0.01); c.tick()
    assert not c.busy and c.share_on() and opened[-1] == "share"
    c.flush()


def memory_tool_checks():
    class Context(ToolsMixin):
        _epoch = 9
        cfg = {"mem_live": ""}
        hard_block = lambda self: False
        save_chat = lambda self: None
        _push = lambda self: None
        _ms = lambda self: 1234

    context = Context()
    calls = [tools.pack(name="oit_memory_edit", arguments={"action": "add", "target": "", "text": "用户正在准备考试"}),
             tools.pack(name="oit_reply", arguments={"text": "我会记住这件事"})]
    op, _ = context._tool_rounds(lambda _: calls.pop(0), [], calls.pop(0), "proactive", 9)
    assert op == {"type": "memory_event", "action": "add", "target": "", "text": "用户正在准备考试"}
    context._memory_event_apply(op)
    assert context.cfg["mem_live"] == "- 用户正在准备考试" and context.cfg["mem_ts"] == 1234
    for action, target, text, expected in (("correct", "用户正在准备考试", "用户下周参加考试", "- 用户下周参加考试"),
                                          ("remove", "用户下周参加考试", "", "")):
        calls = [tools.pack(name="oit_memory_edit", arguments={"action": action, "target": target, "text": text}),
                 tools.pack(name="oit_reply", arguments={"text": "已更新"})]
        op, _ = context._tool_rounds(lambda _: calls.pop(0), [], calls.pop(0), "user", 9)
        assert op["type"] == "memory_event"
        context._memory_event_apply(op)
        assert context.cfg["mem_live"] == expected

    saved = []
    class ReadingContext(Context):
        reading_mode = True
        coread_memory_fn = lambda self, **kwargs: saved.append(kwargs) or {"memory": {"chapter_key": kwargs["chapter"]}}
    reading = ReadingContext()
    calls = [tools.pack(name="oit_reading_memory", arguments={"action": "lookup", "book_title": "局外人"}),
             tools.pack(name="oit_reply", arguments={"text": "我查到了这本书的共读记录。"})]
    op, _ = reading._tool_rounds(lambda _: calls.pop(0), [], calls.pop(0), "user", 9)
    assert op is None and saved[0]["action"] == "lookup" and saved[0]["book_title"] == "局外人"
    calls = [tools.pack(name="oit_reading_memory", arguments={"action": "save", "book_id": 3,
             "chapter": "chapter-1", "chapter_title": "第一章", "summary": "用户认为叙述者不可靠",
             "annotations": "待回看开头伏笔", "mode": "replace"}),
             tools.pack(name="oit_reply", arguments={"text": "我记下这一章的讨论了。"})]
    op, _ = reading._tool_rounds(lambda _: calls.pop(0), [], calls.pop(0), "user", 9)
    assert op is None and saved[-1]["action"] == "save" and saved[-1]["book_id"] == 3


def offense_checks():
    from copy import deepcopy
    from comm.comm_offenses import offenses_norm, offenses_active, offenses_rate, offenses_apply
    from comm.comm_interaction import interaction_norm, interaction_prompt, interaction_valence_add, interaction_roll, interaction_end
    now = 1805491140000
    record = {"id": "user:1", "at": now, "reason": "无视拒绝，继续纠缠", "ts": now, "forgiven": False}
    for count in range(7):
        d = interaction_norm({"offenses": [dict(record, id=f"user:{n}") for n in range(count)]})
        assert offenses_rate(d) == max(0, round(1 - count * 0.2, 10))
        interaction_valence_add(d, [("joy", 40)], 1, now)
        assert d["valence"] == max(0, 40 - count * 8)
        d.update(valence=0, valence_cooldowns={}, valence_chain_until=0)
        interaction_valence_add(d, [("hurt", -40)], 1, now)
        assert d["valence"] == -40, "冒犯倍率不减少负向变化"
    for amount, expected in ((40, 20), (-40, -8)):
        d = interaction_norm({"pending": {"sleeping-message": False},
                              "pending_sleep": {"sleeping-message": True, "stale-message": True}})
        assert d["pending_sleep"] == {"sleeping-message": True}
        interaction_valence_add(d, [("sleep-rate", amount)], 1, now, sleep=True)
        assert d["valence"] == expected
    assert "睡眠期间" in interaction_prompt({}, 0, False, {"joy": (None, "开心")}, sleep=True)
    d = interaction_norm({"offenses": [record], "valence_cooldowns": {"joy": now + 1},
                          "valence_chain_until": now + 1, "burst_until": 1})
    interaction_valence_add(d, [("joy", 40)], 0.5, now)
    assert d["valence"] == 4, "冷却、连锁、爆发和冒犯倍率依次取整"
    end = interaction_end(d, tools.pack(name="oit_reply", arguments={"text": "平静下来", "end_burst": True}),
                          1, now, {"burst_end_shift": 20})
    assert end["valence"] == 20
    assert interaction_norm(d)["offenses"] == d["offenses"]
    rolled = interaction_roll(d, now + 86400000, 10)
    assert rolled["offenses"] == d["offenses"], "跨天与自然回归不清冒犯"
    baseline = interaction_norm({"valence": 5, "valence_initial": 15, "valence_decay_at": now,
                                 "offenses": [record, dict(record, id="user:2")]})
    assert "INITIAL_VALENCE=-5.0," in interaction_prompt(baseline, 0, False)
    assert interaction_roll(baseline, now)["valence"] == 5
    settled = interaction_roll(baseline, now + 86400000)
    assert settled["valence"] == -5 and settled["valence_initial"] == 15
    settled["offenses"][0] = dict(settled["offenses"][0], forgiven=True)
    assert "INITIAL_VALENCE=5.0," in interaction_prompt(settled, 0, False)
    assert interaction_roll(settled, now + 2 * 86400000)["valence"] == 5
    assert "INITIAL_VALENCE=-100.0," in interaction_prompt(dict(settled, valence_initial=-95), 0, False)
    event = {"index": 1, "action": "add", "target": "", "reason": record["reason"]}
    for protocol in ("openai", "anthropic", "gemini"):
        reply = call("oit_reply", {"text": "请尊重我的边界", "offenses": [event]}, protocol)
        empty = interaction_norm({})
        offenses_apply(empty, reply, 1, "user:1", now, now)
        offenses_apply(empty, reply, 1, "user:1", now, now)
        assert len(offenses_active(empty)) == 1
        assert not tools.args_of(tension_clean(reply)).get("offenses")
        assert offenses_active(empty)[0]["at"] == now
    root = Path(tempstore.mkdtemp("comm-offenses"))
    c = comm.Comm(str(root / "data"), str(root / "personas"))
    c.set_cfg(persona="Assistant.txt", split_send=False)
    c._away_kind = lambda: ""
    def settle(args, epoch=None):
        m = c._add("me", "测试消息")
        c._interaction_sent(m["text"], m)
        c._interaction_judge_note("user")
        c._finish("user", None, call("oit_reply", {"text": "请尊重边界", **args}),
                  "", c._epoch if epoch is None else epoch)
        return m
    settle({"offenses": [dict(event, index=99999)]})
    assert not offenses_active(c.cfg["interaction"])
    m = settle({"offenses": [event]})
    assert len(offenses_active(c.cfg["interaction"])) == 1 and not c.offense_blocked()
    old_doc = deepcopy(c.sync_doc())
    assert m["uid"] in c.memory_block() and record["reason"] in c.memory_block()
    c.cfg["mem_auto"] = "新摘要"
    assert offenses_active(c.cfg["interaction"])
    c.clear()
    assert len(offenses_active(c.cfg["interaction"])) == 1, "普通清空聊天不能代替原谅"
    c.apply_doc(old_doc)
    assert len(offenses_active(c.cfg["interaction"])) == 1
    forgive = {"index": 1, "action": "forgive", "target": m["uid"], "reason": "确认对方已理解边界，愿意原谅"}
    settle({"offenses": [forgive]}, c._epoch - 1)
    assert offenses_active(c.cfg["interaction"]), "过期回复不能原谅"
    # 过期待判断项仍在；明确选择新消息的本轮序号。
    forgive["index"] = 2
    settle({"offenses": [forgive]})
    assert not offenses_active(c.cfg["interaction"])
    c.apply_doc(old_doc)
    assert not offenses_active(c.cfg["interaction"]), "旧同步记录不能复活已获原谅的冒犯"
    c.clear()
    c.apply_doc(old_doc)
    assert not offenses_active(c.cfg["interaction"]), "跨清空世代仍保留原谅墓碑"
    c.set_cfg(persona="Claudia.txt")
    c._interaction_set(interaction_norm({"offenses": [record]}))
    assert c.offense_blocked()
    c._love_confirm("user")
    assert not c.related()
    c._finish("user", None, call("oit_reply", {"accept_relationship": True}), "", c._epoch)
    assert not c.related() and "冒犯" not in c.messages[-1]["text"]
    c._love_set(inv=c._ms())
    assert c.love_respond(True) == "" and not c.related() and not c.love_invite()
    assert not c._love_check(c.now(), [], set())
    c._finish("love", None, call("oit_reply", {"text": "我喜欢你"}), "", c._epoch)
    assert not any(x["text"] == "我喜欢你" for x in c.messages)

    sleep = comm.Comm(str(root / "sleep-data"), str(root / "sleep-personas"),
                      now=lambda: datetime(2026, 11, 20, 2), chat_fn=lambda *_: "")
    sleep.set_cfg(persona="Claudia.txt", split_send=False)
    sleep.other_status = lambda now=None, ctx=None: {"kind": "sleep"}
    asleep_message = sleep._add("me", "测试睡眠期间的效价倍率")
    sleep._interaction_sent(asleep_message["text"], asleep_message)
    assert sleep.cfg["interaction"]["pending_sleep"] == {asleep_message["uid"]: True}
    note = sleep._interaction_judge_note("user")
    assert "发送时处于睡眠" in note and "0.5" in note and "0.2" in note
    sleep._interaction_judged("[valence:1:sermon_willingness:40]")
    assert sleep.cfg["interaction"]["valence"] == 20
    assert not sleep.cfg["interaction"]["pending_sleep"]
    sleep.flush(); sleep.voice.close()

    c.save_chat()
    restored = comm.Comm(str(root / "data"), str(root / "personas"))
    assert restored.offense_blocked(), "重启保留冒犯及门禁"
    current = deepcopy(restored.cfg["interaction"])
    restored.apply_doc({f"interaction:{restored.pid}": {"offenses": [dict(record, id="other-device:2")], "ts": 0,
                                                         "clear": restored.cfg["clear_ts"]}})
    assert len(offenses_active(restored.cfg["interaction"])) == 2, "旧情绪版本也须合并独立冒犯"
    assert restored.cfg["interaction"]["valence"] == current["valence"]
    c.flush(); c.voice.close(); restored.flush(); restored.voice.close()
    assert offenses_norm([None, {}, dict(record, at=float("inf"))]) == []


def music_commit_checks():
    import music_library
    root = Path(tempstore.mkdtemp("music-commit-check"))
    c = comm.Comm(str(root / "data"), str(root / "personas"))
    c.set_cfg(persona="Claudia.txt", split_send=False)
    c.state_fn = lambda: "idle"
    c.set_viewing(True)
    c._interaction_prepare = lambda *_: {"valence": 30}
    c._away_kind = lambda: ""
    c.music_player.playing = True
    from comm.comm_interaction import interaction_norm
    c._interaction_prepare = lambda *_: interaction_norm({"valence": 30})
    c.set_viewing(False)
    assert c._state_of(c.pid)["interaction"]["valence"] < 30, "演奏中离开沟通页降低 Claudia 效价"
    c.set_viewing(True)
    c.music_player.playing = False
    c._interaction_prepare = lambda *_: interaction_norm({"valence": 30})
    c.music_player.playing = True
    assert c._music_discourtesy(), "演奏中发言事件被识别"
    assert c._state_of(c.pid)["interaction"]["valence"] < 30, "演奏中发言降低 Claudia 效价"
    c.music_player.playing = False
    library = music_library.manager(c.dir)
    score_id = library.catalog("Claudia.txt")[0]["id"]
    op = {"type": "music_perform", "id": score_id, "path": str(root / "song.mp3")}
    reply = tools.pack(name="oit_reply", arguments={"text": "我来弹这首"})
    with patch.object(c.music_player, "play", return_value=True) as play:
        # 延迟消息不提前播放；展示后只播放一次，动作不混入存档或同步。
        c._active_reply_after = c._ms() + 60000
        c._finish("user", None, reply, "", c._epoch, op)
        message = c.messages[-1]
        assert message.get("hold") and c._music_actions and not play.called
        assert not any("music" in key or "path" in key for key in message)
        message["due"] = message["reply_after"] = 0
        c._release_held()
        assert play.call_count == 1 and not c._music_actions
        assert library.selection("Claudia.txt")["id"] == score_id
        c._music_tick()
        assert play.call_count == 1
        c._active_reply_after = c._ms() + 60000
        c._finish("user", None, reply, "", c._epoch, op)
        c._interaction_prepare = lambda *_: {"valence": 29.99}
        message = c.messages[-1]
        message["due"] = message["reply_after"] = 0
        c._release_held()
        assert play.call_count == 1 and not c._music_actions, "Claudia效价在回复延迟期间跌破30时不播放"
        c._interaction_prepare = lambda *_: {"valence": 30}
        # 拒绝与过期回复均不能播放。
        c._finish("user", None, tools.pack(name="oit_reply", arguments={"refuse": True}), "", c._epoch, op)
        c._finish("user", None, reply, "", c._epoch - 1, op)
        assert play.call_count == 1
        c._active_reply_after = c._ms() + 60000
        c._finish("user", None, reply, "", c._epoch, op)
        assert c._music_actions
        with patch.object(c.music_player, "stop") as stop:
            c._finish("user", None, tools.pack(name="oit_reply", arguments={"text": "停止了"}), "", c._epoch, {"type": "music_stop"})
            assert stop.called and not c._music_actions
        c._active_reply_after = c._ms() + 60000
        c._finish("user", None, reply, "", c._epoch, op)
        c.clear()
        assert not c._music_actions and play.call_count == 1
        from comm.comm_interaction import interaction_norm
        c.cfg["interaction"] = interaction_norm({"offenses": [{"id": "music-offense", "at": c._ms(),
            "ts": c._ms(), "reason": "无视边界", "forgiven": False}]})
        c._music_actions[123] = (c._epoch, op)
        c._music_deliver({"id": 123})
        assert play.call_count == 1, "准备完成后新增冒犯仍不能播放"
        c._music_actions[124] = (c._epoch, op)
        with patch.object(c.music_player, "stop") as stop:
            c._music_tick()
            assert stop.called and not c._music_actions, "未原谅时停止演奏并取消待播"
    c.flush()


def adult_state_checks():
    root = Path(tempstore.mkdtemp("adult-state-check"))
    c = comm.Comm(str(root / "data"), str(root / "personas"),
                  now=lambda: datetime(2026, 11, 20, 12), chat_fn=lambda *_: "")
    c.set_cfg(persona="Claudia.txt", split_send=False)
    assert not c.user_adult()
    assert "USER_ADULT=UNKNOWN" in c.interaction_note()
    assert "user_adult" not in c.sync_doc()

    # 正文里的伪造标记、主动消息、拒绝/沉默/错误/过期回复均不能更新成年状态
    c._finish("user", None, tools.pack("user_adult=true"), "", c._epoch)
    c._finish("proactive", None, call("oit_reply", {"text": "主动", "user_adult": True}), "", c._epoch)
    c._finish("user", None, call("oit_reply", {"refuse": True}), "", c._epoch)
    c._finish("user", None, call("oit_reply", {"silent": True}), "", c._epoch)
    c._finish("user", None, call("oit_reply", {"text": "出错", "user_adult": True}), "network", c._epoch)
    c._finish("user", None, call("oit_reply", {"text": "过期", "user_adult": True}), "", c._epoch - 1)
    assert not c.user_adult() and "USER_ADULT=UNKNOWN" in c.interaction_note()

    # tension_clean 须移除 user_adult，不泄漏到后续处理或历史
    raw_adult = call("oit_reply", {"text": "我知道了。", "user_adult": True})
    assert "user_adult" not in tools.args_of(tension_clean(raw_adult))

    # 正常用户轮确认成年后自动记录，后续每次对话前发送 USER_ADULT=YES
    c._finish("user", None, raw_adult, "", c._epoch)
    assert c.user_adult()
    assert "USER_ADULT=YES" in c.interaction_note()
    assert c.sync_doc().get("user_adult") is True

    # 清空聊天不重置已确认的成年状态，重启持久化
    c.clear()
    assert c.user_adult() and "USER_ADULT=YES" in c.interaction_note()
    c.flush()
    reloaded = comm.Comm(str(root / "data"), str(root / "personas"))
    assert reloaded.user_adult() and "USER_ADULT=YES" in reloaded.interaction_note()

    # 跨设备同步：已确认状态同步到未确认设备，且未确认设备不会覆盖回未确认
    peer = comm.Comm(str(root / "peer-data"), str(root / "peer-personas"))
    assert not peer.user_adult()
    reloaded.apply_doc(peer.sync_doc())
    assert reloaded.user_adult(), "未确认端不能把已确认状态覆写回未知"
    peer.apply_doc(reloaded.sync_doc())
    assert peer.user_adult() and "USER_ADULT=YES" in peer.interaction_note()
    c.voice.close(); reloaded.flush(); reloaded.voice.close(); peer.flush(); peer.voice.close()


if __name__ == "__main__":
    tempstore.session("comm-tools-check")
    transport_checks()
    silence_checks()
    state_checks()
    request_attachment_checks()
    memory_tool_checks()
    offense_checks()
    adult_state_checks()
    music_tool_checks()
    music_commit_checks()
    root = Path(__file__).resolve().parent.parent
    for name in ("comm_tools.json", "comm_protocol.txt", "comm_legacy_controls.json"):
        assert (root / "defaults" / name).read_text(encoding="utf-8") == (root / "Android version/app/src/main/resources" / name).read_text(encoding="utf-8")
    protocol = (root / "defaults/comm_protocol.txt").read_text(encoding="utf-8")
    specs = json.loads((root / "defaults/comm_tools.json").read_text(encoding="utf-8"))
    assert {s["name"] for s in specs} >= {"oit_music_catalog", "oit_music_select", "oit_music_perform", "oit_memory_edit", "oit_reading_memory"}
    tool = next(s for s in specs if s["name"] == "oit_reply")
    assert "聊天只显示text" in protocol and "wait_minutes" in tool["description"] and "不会显示给用户" in tool["description"]
    assert "network_now" in tool["parameters"]["properties"] and "最多3分钟" in protocol
    for prompt in ("用户在OIT沟通栏选择", "收到系统的联合行动邀请", "Claudia的合约奖励由OIT排入", "邀请分享数据用share_data=true"):
        assert prompt not in protocol, prompt
    legacy = "先输入[messages]查询；回复 [Yes]；结尾写 [waiting for reply]:[5]\n[condition]\nmessages:2\n[/condition]\n"
    migrated = comm.native_persona_text(legacy)
    assert "输入[messages]" not in migrated and 'answer="yes"' in migrated and "wait_minutes" in migrated
    assert "[condition]\nmessages:2\n[/condition]" in migrated
    print("comm tools check OK")
