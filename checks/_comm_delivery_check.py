# -*- coding: utf-8 -*-
"""分隔符分条、完整气泡延时、插话重写与本机待发队列回归。"""
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from queue import Queue

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempstore
import ai_assist
import comm
from ai_assist.ai_tools import pack
from comm.comm_delivery import ReplyParts, reply_parts


def main():
    root = tempstore.mkdtemp("comm-delivery")
    data, personas = os.path.join(root, "d"), os.path.join(root, "p")
    os.makedirs(data); os.makedirs(personas)
    with open(os.path.join(personas, "小夏.txt"), "w", encoding="utf-8") as f:
        f.write("你是小夏")
    clock = [datetime(2026, 11, 18, 23, 59, 59)]
    tasks, calls, all_tasks = Queue(), [], []
    old_chat, old_delay, old_cjk = ai_assist.chat_messages, comm.CHAR_DELAY, comm.CHAR_DELAY_CJK

    def task(text, chunks=(), fail=False):
        item = dict(text=text, chunks=chunks, fail=fail, ready=threading.Event(), done=threading.Event())
        all_tasks.append(item); tasks.put(item)
        return item

    def chat(profile, system, messages, **kwargs):
        item = tasks.get(timeout=3)
        calls.append([dict(m) for m in messages])
        callback = kwargs["on_delta"]
        for piece in item["chunks"]:
            callback(piece)
        item["ready"].set()
        assert item["done"].wait(5)
        prefix = "".join(item["chunks"])
        assert item["text"].startswith(prefix)
        callback(item["text"][len(prefix):])
        if item["fail"]:
            raise ai_assist.AIError("network", "流式响应中断，回复尚未完成")
        return pack(text=item["text"])

    def pump(until):
        end = time.monotonic() + 5
        while not until() and time.monotonic() < end:
            c.tick(); time.sleep(0.005)
        assert until(), "后台请求未按预期结束"

    def start(item, message="在吗", delay=0):
        assert c.send(message, reply_delay_seconds=delay) == ""
        assert item["ready"].wait(3)
        c.tick()

    def end(item):
        item["done"].set()
        pump(lambda: not c.busy)

    c = None
    try:
        # 普通换行、空段、跨传输片段、CRLF 和代码块。
        assert reply_parts("甲\n\n乙") == ["甲\n\n乙"]
        assert reply_parts("<oit_message>\n甲\r\n<oit_message>\r\n\n<oit_message>\n乙\n<oit_message>") == ["甲", "乙"]
        assert reply_parts("甲<oit_message>乙 <OIT-MESSAGE> 丙") == ["甲", "乙", "丙"]
        assert reply_parts("甲&lt; oit_message &gt;乙<oit_message   >丙") == ["甲", "乙", "丙"]
        assert reply_parts("```txt\n<oit_message>\n```\n<oit_message>\n乙") == ["```txt\n<oit_message>\n```", "乙"]
        assert reply_parts("```txt\n正文 <oit_message> 仍在代码块\n```\n甲<oit_message>乙") == ["```txt\n正文 <oit_message> 仍在代码块\n```\n甲", "乙"]
        parser = ReplyParts()
        assert parser.feed("甲\n<oit_mes") == []
        assert parser.feed("sage>\n乙") == ["甲"]
        assert parser.feed("", final=True) == ["乙"]

        ai_assist.chat_messages = chat
        comm.CHAR_DELAY, comm.CHAR_DELAY_CJK = 0.1, 0.2
        c = comm.Comm(data, personas, now=lambda: clock[0])
        c.ai.set_profile("deepseek", api_key="k"); c.ai.provider = "deepseek"
        c.set_cfg(persona="小夏.txt")
        c.snapshot = lambda: {"state": "idle"}
        # 分隔符前的整段可在流结束之前发送；后面完整收齐也不重复调用。
        first = task("甲\n<oit_message>\n乙\n<oit_message>\n丙", ("甲\n<oit_m", "essage>\n"))
        start(first)
        assert [m["text"] for m in c.visible()] == ["在吗"]
        pending = c.messages[-1]
        assert pending["text"] == "甲" and pending["due"] - c._ms() == 200
        assert f"m:{c.pid}:{pending['uid']}" not in c.sync_doc()
        assert all(m["role"] != "assistant" for m in comm.api_messages(c.messages))
        clock[0] += timedelta(milliseconds=200); c.tick()
        assert c.busy and c.visible()[-1]["text"] == "甲"
        assert f"m:{c.pid}:{pending['uid']}" in c.sync_doc()
        end(first)
        queued = [m for m in c.messages if "pending_reply" in m]
        assert [m["text"] for m in queued] == ["乙", "丙"]
        assert queued[1]["due"] - queued[0]["due"] == 200
        # 跨午夜后也按绝对时刻逐条发送。
        clock[0] += timedelta(milliseconds=200); c.tick()
        assert c.visible()[-1]["text"] == "乙"
        clock[0] += timedelta(milliseconds=200); c.tick()
        assert [m["text"] for m in c.visible()] == ["在吗", "甲", "乙", "丙"]
        assert len(calls) == 1

        # 等待期间插话：已发保留，旧待发从历史和同步中同时消失。
        second = task("已发\n<oit_message>\n旧后续很长")
        second["done"].set(); start(second); pump(lambda: not c.busy)
        clock[0] += timedelta(milliseconds=400); c.tick()
        assert c.visible()[-1]["text"] == "已发"
        revised = task("按新消息修改")
        start(revised, "改一下")
        assert not any(m["text"] == "旧后续很长" for m in c.messages)
        history = "\n".join(m["content"] for m in calls[-1])
        assert "已发" in history and "旧后续很长" not in history and "改一下" in history and "改写" in history
        end(revised)
        clock[0] += timedelta(seconds=5); c.tick()
        assert c.visible()[-1]["text"] == "按新消息修改"

        # 生成期间插话，旧回调失效；即使旧响应失败，也根据新消息重新请求。
        old = task("前段\n<oit_message>\n废弃尾段", ("前段\n<oit_message>\n",), fail=True)
        start(old)
        clock[0] += timedelta(milliseconds=400); c.tick()
        assert c.visible()[-1]["text"] == "前段"
        replacement = task("改写结果")
        assert c.send("新的要求") == ""
        old["done"].set()
        pump(lambda: replacement["ready"].is_set())
        assert "废弃尾段" not in str(c.messages) and "废弃尾段" not in str(calls[-1])
        end(replacement)
        clock[0] += timedelta(seconds=5); c.tick()
        assert c.visible()[-1]["text"] == "改写结果"

        # 另一台设备插话，原设备失去焦点时不能自行再请求；恢复焦点才改写。
        remote_old = task("远端插话前的待发内容")
        remote_old["done"].set(); start(remote_old); pump(lambda: not c.busy)
        count = len(calls)
        ts = c._last_ts + 1
        c.leader = lambda: False
        c.apply_doc({f"m:{c.pid}:remote-{ts}": dict(role="me", text="远端新要求", ts=ts, at=clock[0].isoformat())})
        c.tick()
        assert not any(m["text"] == "远端插话前的待发内容" for m in c.messages)
        assert len(calls) == count and not c.busy
        remote_new = task("远端改写结果")
        c.leader = lambda: True
        c.tick()
        assert remote_new["ready"].wait(3)
        assert "远端新要求" in str(calls[-1]) and "远端插话前的待发内容" not in str(calls[-1])
        end(remote_new)
        clock[0] += timedelta(seconds=5); c.tick()

        # 不完整尾段绝不出现；已经发出的完整气泡不因断流撤回。
        broken = task("保留\n<oit_message>\n不完整尾段", ("保留\n<oit_message>\n不完",), fail=True)
        start(broken)
        clock[0] += timedelta(milliseconds=400); c.tick()
        assert c.visible()[-1]["text"] == "保留"
        end(broken)
        assert c.visible()[-1]["text"] == "保留" and c.error
        assert not any("不完整尾段" in m["text"] for m in c.messages)

        # 完整响应的待发正文可持久化，未验证片段重启后不复活。
        saved = task("持久化正文", ("持久化正文\n<oit_message>\n",))
        saved["text"] = "持久化正文\n<oit_message>\n"
        start(saved)
        raw = c._state_payload(c._active_state())
        assert not any(m["text"] == "持久化正文" for m in c._norm_state(raw)["messages"])
        end(saved)
        raw = c._state_payload(c._active_state())
        assert any(m["text"] == "持久化正文" for m in c._norm_state(raw)["messages"])
        c.flush()
        restarted = comm.Comm(data, personas, now=lambda: clock[0])
        clock[0] += timedelta(seconds=10); restarted.tick()
        assert restarted.visible()[-1]["text"] == "持久化正文"
        restarted.flush(); restarted.voice.close()

        # Assistant 同样按分隔符分条，只免去字数等待；普通半段不展示。
        c.clear(); clock[0] = clock[0].replace(hour=12)
        c.set_persona("Assistant.txt")
        instant = task("第一条\n<oit_message>\n第二条", ("第一条\n<oit_message>\n第",))
        start(instant)
        assert c.visible()[-1]["text"] == "第一条" and c.busy
        assert c.visible()[-1]["at"].startswith("2026-")
        end(instant)
        assert [m["text"] for m in c.visible() if m["role"] == "ai"] == ["第一条", "第二条"]
        # 清空后返回的分片和最终正文都不能恢复旧聊天。
        stale = task("过期\n<oit_message>\n尾段", ("过期\n<oit_message>\n",))
        start(stale); c.clear(); end(stale)
        assert not c.visible()
        # 旧结果不能吞掉清空之后紧接着发送的新消息。
        stale = task("清空前旧内容")
        start(stale)
        c.clear()
        fresh = task("新会话回复")
        assert c.send("新会话问题") == ""
        stale["done"].set()
        pump(lambda: fresh["ready"].is_set())
        end(fresh)
        assert [m["text"] for m in c.visible()] == ["新会话问题", "新会话回复"]
    finally:
        for item in all_tasks:
            item["done"].set()
        if c:
            c.flush(); c.voice.close()
        ai_assist.chat_messages = old_chat
        comm.CHAR_DELAY, comm.CHAR_DELAY_CJK = old_delay, old_cjk
    print("comm delivery check OK")


if __name__ == "__main__":
    tempstore.session("comm-delivery-check")
    main()
