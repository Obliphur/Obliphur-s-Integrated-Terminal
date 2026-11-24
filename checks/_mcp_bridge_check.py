# -*- coding: utf-8 -*-
"""端到端检查：OIT MCP Bridge（HTTP + SSE）、子代理双路由、Antigravity Hooks、TypeScript MCP Server（stdio + Streamable HTTP）与 aux_pane 资源。"""

from datetime import datetime, timedelta
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import comm
import config
from mcp_bridge import OitMcpBridge


def _http_json(port: int, token: str, path: str, body=None):
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET", headers=headers)
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    tmp = tempfile.mkdtemp(prefix="oit_mcp_check_")
    personas_dir = os.path.join(tmp, "personas")
    os.makedirs(personas_dir, exist_ok=True)
    old_takeover = config.AI_CHAT_TAKEOVER
    bridge = None
    http_proc = None
    try:
        config.AI_CHAT_TAKEOVER = "antigravity"
        c = comm.Comm(tmp, personas_dir, now=lambda: datetime(2026, 11, 24, 12, 40, 0))
        opened = []
        bridge = OitMcpBridge(c, tmp, open_native_dialog=lambda kind, p: opened.append((kind, p)) or {"ok": True})
        c.mcp_bridge = bridge
        port = bridge.start()
        assert port > 0 and os.path.isfile(bridge.discovery_path), "应生成 mcp_bridge.json"

        # 1. 未鉴权请求必须 401
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=4)
            raise AssertionError("未带 Bearer Token 不应通过")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401, f"期望 401，实际 {exc.code}"

        health = _http_json(port, bridge.token, "/health")
        assert health["ok"] and health["takeover"] == "antigravity"
        assert not health["persona_name"].lower().endswith(".txt"), f"人设名不应带 .txt 后缀: {health['persona_name']}"

        # 2. 会话绑定 + 1对1人设自动切换验证 + SSE 订阅 + 主动事件推送与原子消费
        with open(os.path.join(personas_dir, "Beta.txt"), "w", encoding="utf-8") as fh:
            fh.write("你是 Beta。")
        first_pid = c.pid
        bind = _http_json(port, bridge.token, "/session/bind", {"conversation_id": "conv-1", "host": "antigravity"})
        assert bind["ok"] and bind["subagent_routes"]["subject"]["mode"] == "host_subagent"
        bind2 = _http_json(port, bridge.token, "/session/bind", {"conversation_id": "conv-2", "persona_name": "Beta", "host": "antigravity"})
        assert bind2["ok"] and bind2["persona_name"] == "Beta" and c.pid == bind2["persona_id"]
        # 切回 conv-1 时自动切回第一个人设，不串台
        ctx_back = _http_json(port, bridge.token, "/turn/context?conversation_id=conv-1&consume_trigger=0")
        assert ctx_back["persona_id"] == first_pid and c.pid == first_pid, "切换绑定对话应自动切回对应人设"
        # 严格排斥：conv-3 试图重复绑定已绑给 conv-2 的 Beta（未传 force=True）必须被拒绝
        conflict = _http_json(port, bridge.token, "/tool/execute", {
            "name": "oit_bind_persona",
            "arguments": {"persona_name": "Beta", "conversation_id": "conv-3"},
        })
        assert conflict["ok"] is False and conflict.get("conflict") is True and conflict.get("bound_conversation_id") == "conv-2"
        # 显式传 force=True 才允许解绑旧对话并迁移绑定
        rebind = _http_json(port, bridge.token, "/tool/execute", {
            "name": "oit_bind_persona",
            "arguments": {"persona_name": "Beta", "conversation_id": "conv-3", "force": True},
        })
        assert rebind["ok"] is True
        chk_old = _http_json(port, bridge.token, "/session/check?conversation_id=conv-2")
        assert chk_old["bound"] is False, "强制改绑后旧对话应解除绑定"
        # 重启持久化验证：新建 Comm + OitMcpBridge 后绑定不丢失
        c_restarted = comm.Comm(tmp, personas_dir)
        b_restarted = OitMcpBridge(c_restarted, tmp)
        assert b_restarted.is_bound_conversation("conv-1") and b_restarted.is_bound_conversation("conv-3"), "重启 OIT 后 1:1 绑定必须持久保留"
        # 切回 conv-1 供后续步骤使用
        _http_json(port, bridge.token, "/turn/context?conversation_id=conv-1&consume_trigger=0")

        # 2b. Antigravity 接管模式下在 OIT 内直接 send() 绝不调用 agy CLI（无 Sidecar 时给出友好提示）
        cli_called = []
        c.chat_fn = lambda *a, **kw: cli_called.append((a, kw)) or ""
        c.send("在 OIT 里直接发一条")
        c._request("chat")
        assert not cli_called, "Antigravity 接管模式下绝不能再调用 CLI/chat_fn"
        assert "Antigravity" in c.error, f"无活跃 Sidecar 时应给出友好提示，实际: {c.error}"

        sse_events = []

        def read_sse():
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/events/stream",
                headers={"Authorization": f"Bearer {bridge.token}"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                for raw in resp:
                    line = raw.decode("utf-8").strip()
                    if line.startswith("data:"):
                        sse_events.append(json.loads(line[5:].strip()))
                        break

        t_sse = threading.Thread(target=read_sse, daemon=True)
        t_sse.start()
        time.sleep(0.2)
        bridge.push_proactive(c.pid, "按你的日程现在有空，主动问候一下用户。", kind="proactive")
        t_sse.join(timeout=4)
        assert len(sse_events) == 1 and sse_events[0]["conversation_id"] == "conv-1", "Sidecar 应收到 SSE 主动事件"

        ctx1 = _http_json(port, bridge.token, "/turn/context?conversation_id=conv-1&consume_trigger=1")
        assert ctx1["trigger"] and "主动问候一下用户" in ctx1["system_message"]
        ctx2 = _http_json(port, bridge.token, "/turn/context?conversation_id=conv-1&consume_trigger=1")
        assert ctx2["trigger"] is None, "pending_trigger 消费后应原子清空"

        # 3. 子代理双路由：单独给 subject 配置外部 HTTP API 后自动切为 external_api
        cid_ext = c.ai.add_config("外置数理化")
        c.ai.profiles[cid_ext]["api_key"] = "sk-test-123"
        c.ai.profiles[cid_ext]["base_url"] = "https://api.example.com/v1"
        c.ai.assign_task("subject", cid_ext)
        ctx3 = _http_json(port, bridge.token, "/turn/context?conversation_id=conv-1&consume_trigger=0")
        assert ctx3["subagent_routes"]["subject"]["mode"] == "external_api"
        assert ctx3["subagent_routes"]["mental_health"]["mode"] == "host_subagent"

        # 4. 分条气泡提交 -> 逐条确认送达 -> 中途插话撤销
        rep = _http_json(port, bridge.token, "/reply/submit", {
            "conversation_id": "conv-1",
            "bubbles": ["第一条气泡", "第二条气泡", "第三条气泡"],
        })
        assert rep["ok"] and len(rep["items"]) == 3
        first_id = rep["items"][0]["id"]
        d1 = _http_json(port, bridge.token, "/ui/delivered", {"msg_id": first_id})
        assert d1["ok"]
        fb = _http_json(port, bridge.token, "/turn/fallback_sync", {"conversation_id": "conv-1", "text": "重复的未分段回复"})
        assert fb.get("skipped") == "already_replied_via_oit_reply", f"刚走完 oit_reply 后 fallback_sync 应跳过: {fb}"
        intr = _http_json(port, bridge.token, "/ui/interrupt", {"user_text": "我打断一下"})
        assert intr["ok"] and intr["cancelled"] == 2 and intr.get("msg_id")
        ctx4 = _http_json(port, bridge.token, "/turn/context?conversation_id=conv-1&consume_trigger=1")
        assert ctx4["interrupted"]["count"] == 2 and "2 条待发气泡" in ctx4["system_message"]

        # 5. 聊天栏工具按钮与原生弹窗委托
        act = _http_json(port, bridge.token, "/ui/action", {"action": "open_native_dialog", "params": {"kind": "review"}})
        assert act["ok"] and opened and opened[-1][0] == "review"

        # 6. PreInvocation Hook 验证（invocationNum == 1 注入，invocationNum == 2 跳过）
        hook_env = dict(os.environ, OIT_BRIDGE_FILE=bridge.discovery_path)
        hook_script = os.path.join(ROOT, "antigravity_plugin", "scripts", "hook_pre_invocation.py")
        p1 = subprocess.run(
            [sys.executable, hook_script],
            input=json.dumps({"conversationId": "conv-1", "invocationNum": 1, "lastUserInput": "你好"}),
            capture_output=True, text=True, encoding="utf-8", env=hook_env, timeout=10, check=True,
        )
        out1 = json.loads(p1.stdout)
        assert out1.get("injectSteps") and "[OIT动态状态]" in out1["injectSteps"][0]["systemMessage"]["systemMessage"]
        p2 = subprocess.run(
            [sys.executable, hook_script],
            input=json.dumps({"conversationId": "conv-1", "invocationNum": 2, "lastUserInput": "你好"}),
            capture_output=True, text=True, encoding="utf-8", env=hook_env, timeout=10, check=True,
        )
        assert json.loads(p2.stdout) == {}, "invocationNum != 1 不应重复注入"

        # 6b. 未绑定对话必须与 OIT 完全隔离（SessionStart / PreInvocation / fallback_sync 均不注入不同步）
        ss_script = os.path.join(ROOT, "antigravity_plugin", "scripts", "hook_session_start.py")
        p_unb_ss = subprocess.run(
            [sys.executable, ss_script],
            input=json.dumps({"conversationId": "conv-unbound"}),
            capture_output=True, text=True, encoding="utf-8", env=hook_env, timeout=10, check=True,
        )
        assert json.loads(p_unb_ss.stdout) == {}, "未绑定对话不应触发 SessionStart 注入"
        p_unb_pre = subprocess.run(
            [sys.executable, hook_script],
            input=json.dumps({"conversationId": "conv-unbound", "invocationNum": 1, "lastUserInput": "写一段 Python"}),
            capture_output=True, text=True, encoding="utf-8", env=hook_env, timeout=10, check=True,
        )
        assert json.loads(p_unb_pre.stdout) == {}, "未绑定对话不应触发 PreInvocation 注入"
        fb_unb = _http_json(port, bridge.token, "/turn/fallback_sync", {"conversation_id": "conv-unbound", "text": "普通工作回答"})
        assert fb_unb.get("skipped") == "unbound_conversation"

        # 7. TypeScript MCP Server (stdio) 端到端协议与资源验证
        server_js = os.path.join(ROOT, "antigravity_plugin", "server", "dist", "index.js")
        assert os.path.isfile(server_js), "请先构建 antigravity_plugin/server"
        mcp_proc = subprocess.Popen(
            ["node", server_js, "--transport", "stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", env=hook_env,
        )

        def rpc(req_obj):
            mcp_proc.stdin.write(json.dumps(req_obj) + "\n")
            mcp_proc.stdin.flush()
            line = mcp_proc.stdout.readline()
            assert line, "MCP Server 未返回 JSON-RPC 响应"
            return json.loads(line)

        init_res = rpc({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "check", "version": "1.0"}},
        })
        assert "result" in init_res
        mcp_proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        mcp_proc.stdin.flush()

        tools_res = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = {t["name"]: t for t in tools_res["result"]["tools"]}
        assert "oit_reply" in tools and tools["oit_reply"]["_meta"]["ui"]["resourceUri"] == "ui://oit/chat.html"
        assert tools["oit_reply"]["_meta"]["com.google.antigravity/ui"]["entrypoints"][0]["type"] == "aux_pane"
        aux_entries = [t for t in tools.values() if (t.get("_meta") or {}).get("com.google.antigravity/ui")]
        assert len(aux_entries) == 1, f"只能有 1 个 aux_pane 入口，避免菜单出现两个 OIT Chat: {[t['name'] for t in aux_entries]}"
        assert tools["oit_ui_state"]["_meta"]["ui"]["visibility"] == ["app"]
        assert "oit_get_turn_context" in tools and "oit_memory_save_auto" in tools and "oit_claudia_reference" in tools
        assert tools["oit_claudia_reference"]["annotations"]["readOnlyHint"] is True

        claudia_res = rpc({
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "oit_claudia_reference", "arguments": {}},
        })
        assert not claudia_res["result"].get("isError")
        cfiles = claudia_res["result"]["structuredContent"]["files"]
        for req_name in ("Claudia.txt", "Claudia_background.txt", "Claudia_example.txt", "Claudia_templates.txt", "Claudia_profile.txt"):
            assert req_name in cfiles and cfiles[req_name].strip(), f"应返回已解密的 {req_name}"
            assert not cfiles[req_name].startswith("OIT-PERSONA-ENC:"), f"{req_name} 必须先解密再返回"

        res_read = rpc({"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "ui://oit/chat.html"}})
        html = res_read["result"]["contents"][0]["text"]
        assert "OIT Chat" in html and len(html) > 2000, "应返回打包后的单文件 aux_pane HTML"

        call_res = rpc({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "oit_reply", "arguments": {"bubbles": ["来自 MCP stdio 的测试气泡"]}},
        })
        assert not call_res["result"].get("isError")
        assert call_res["result"]["structuredContent"]["items"][0]["text"] == "来自 MCP stdio 的测试气泡"
        mcp_proc.terminate()
        mcp_proc.wait(timeout=5)

        # 8. 自动部署辅助函数与帮助文案验证（使用 mock runner，绝不改动用户全局 config.json）
        from mcp_bridge import antigravity_help_text, deploy_antigravity_plugin
        mock_plugin = os.path.join(tmp, "mock_plugin")
        os.makedirs(os.path.join(mock_plugin, "server", "dist"), exist_ok=True)
        os.makedirs(os.path.join(mock_plugin, "ui", "dist"), exist_ok=True)
        with open(os.path.join(mock_plugin, "plugin.json"), "w", encoding="utf-8") as fh:
            fh.write('{"name": "oit"}')
        with open(os.path.join(mock_plugin, "server", "dist", "index.js"), "w", encoding="utf-8") as fh:
            fh.write("// ok")
        with open(os.path.join(mock_plugin, "ui", "dist", "index.html"), "w", encoding="utf-8") as fh:
            fh.write("<html></html>")
        cmds = []
        dep = deploy_antigravity_plugin(
            tmp, plugin_dir=mock_plugin,
            runner=lambda args, cwd, timeout=60: (cmds.append(args), (True, "ok"))[1],
        )
        assert dep["ok"], f"自动部署应成功: {dep}"
        assert any("validate" in a for a in cmds) and any("install" in a for a in cmds)
        with open(os.path.join(mock_plugin, ".oit_data_dir"), "r", encoding="utf-8") as fh:
            assert os.path.abspath(fh.read().strip()) == os.path.abspath(tmp)
        assert "自动部署" in antigravity_help_text(en=False) and "以 OIT 为主聊天界面" in antigravity_help_text(en=False)
        assert "Auto-Deploy" in antigravity_help_text(en=True) and "Chatting Directly in OIT" in antigravity_help_text(en=True)

        # 9. 请求类事件（coread / share data）接受与拒绝回归验证
        coread_opened = []
        share_opened = []
        notices = []
        c.on_coread_open = lambda: coread_opened.append(True)
        c.on_share_open = lambda: share_opened.append(True)
        c.on_share_notice = lambda msg: notices.append(msg)

        c.coread_ask = {"pid": c.pid, "ts": c._ms()}
        c._req_ts = 0  # 故意清零，验证 handle_reply_submit 会正确更新 _req_ts
        bridge.push_user_message(c.pid, "📚 共读\n一起看书吗", api_context="这是用户发来的共同阅读请求。仅对此请求填写answer。")
        ctx_cr = _http_json(port, bridge.token, "/turn/context?conversation_id=conv-1&consume_trigger=1")
        assert "[OIT本轮附加请求]" in ctx_cr["system_message"] and "[OIT待答复请求]" in ctx_cr["system_message"]
        r_cr_yes = _http_json(port, bridge.token, "/reply/submit", {
            "conversation_id": "conv-1", "bubbles": ["好啊，一起看吧。"], "answer": "yes",
        })
        assert r_cr_yes["ok"] and r_cr_yes["applied"].get("coread") == "yes"
        assert coread_opened == [True] and c.coread_ask is None, "同意共读后应触发 on_coread_open 并清空 coread_ask"

        c.coread_ask = {"pid": c.pid, "ts": c._ms()}
        r_cr_no = _http_json(port, bridge.token, "/reply/submit", {
            "conversation_id": "conv-1", "bubbles": ["现在不太方便。"], "answer": "no",
        })
        assert r_cr_no["ok"] and r_cr_no["applied"].get("coread") == "no"
        assert c.coread_ask is None and any("拒绝了共同阅读" in n for n in notices)

        c.share_ask = {"pid": c.pid, "ts": c._ms(), "sent": True}
        r_sh_yes = _http_json(port, bridge.token, "/reply/submit", {
            "conversation_id": "conv-1", "bubbles": ["可以，看看吧。"], "answer": "yes",
        })
        assert r_sh_yes["ok"] and r_sh_yes["applied"].get("share") == "yes"
        assert c.share["on"] is True and share_opened == [True] and c.share_ask is None
        c.share["on"] = False

        c.share_ask = {"pid": c.pid, "ts": c._ms(), "sent": True}
        r_sh_no = _http_json(port, bridge.token, "/reply/submit", {
            "conversation_id": "conv-1", "bubbles": ["这次先不了。"], "answer": "no",
        })
        assert r_sh_no["ok"] and r_sh_no["applied"].get("share") == "no"
        assert c.share["on"] is False and c.share_ask is None and any("拒绝了 share data" in n for n in notices)

        # 9d. 多气泡释放顺序与 wait_minutes 追问保留回归验证（防止 Bubble 0 覆盖 Bubble 1 顺序导致 wait 丢失）
        rep_w = _http_json(port, bridge.token, "/reply/submit", {
            "conversation_id": "conv-1",
            "bubbles": ["第一条说明", "第二条带追问"],
            "wait_minutes": 1,
        })
        assert rep_w["ok"] and len(rep_w["items"]) == 2
        b0_id, b1_id = rep_w["items"][0]["id"], rep_w["items"][1]["id"]
        assert _http_json(port, bridge.token, "/ui/delivered", {"msg_id": b0_id})["ok"]
        assert _http_json(port, bridge.token, "/ui/delivered", {"msg_id": b1_id})["ok"]
        ai_tail = [m for m in c.messages if m.get("role") == "ai"][-2:]
        assert [m["text"] for m in ai_tail] == ["第一条说明", "第二条带追问"], f"气泡顺序不应倒置: {[m['text'] for m in ai_tail]}"
        assert ai_tail[-1].get("wait") == 1.0 and ai_tail[-1].get("shown"), "最后一条气泡应保留 wait 且记录 shown"

        # 9d-1b. 接管模式延时接收回归：用户睡眠 / 专注期间触发的回复在恢复后必须显示 06:30 而非 07:05
        orig_now = c.now
        orig_blocked = c.blocked
        orig_away = c._away_kind
        orig_free = c.ai_free_at
        try:
            # 先把前面 12:40 测试遗留的待发气泡排空，后续按递增日期 11-25 / 11-26 / 11-27 推进
            sim_clock = [datetime(2026, 11, 24, 12, 45, 0)]
            sim_blocked = [""]
            c.now = lambda: sim_clock[0]
            c.blocked = lambda: sim_blocked[0]
            while any(m.get("hold") for m in c.messages):
                sim_clock[0] += timedelta(seconds=10)
                c.tick()

            # (a) 睡眠阻塞：06:30 主动触发并由 oit_reply 提交 -> 07:05 起床推送，消息仍显示 06:30
            sim_clock[0] = datetime(2026, 11, 25, 6, 30, 0)
            sim_blocked[0] = "睡眠中不能使用沟通"
            cursor_before_sleep = _http_json(port, bridge.token, "/ui/state", None)["cursor"]
            bridge.push_proactive(c.pid, "早安触发", kind="proactive")
            rep_sleep = _http_json(port, bridge.token, "/reply/submit", {
                "conversation_id": "conv-1",
                "bubbles": ["早安,睡醒了吗?", "昨晚休息得怎么样?"],
            })
            assert rep_sleep["ok"] and rep_sleep.get("held") is True
            assert [it["text"] for it in rep_sleep["items"]] == ["早安,睡醒了吗?", "昨晚休息得怎么样?"]
            assert all(it["at"].startswith("2026-11-25T06:30:") for it in rep_sleep["items"])

            st_sleep = _http_json(port, bridge.token, f"/ui/state?cursor={cursor_before_sleep}")
            assert st_sleep["blocked"] is True and st_sleep["pending"] == [] and st_sleep["messages"] == [], st_sleep
            assert st_sleep["cursor"] == cursor_before_sleep, "用户阻塞期间 cursor 不得跳过待显示消息"

            b_sleep_id = rep_sleep["items"][0]["id"]
            deliv_blocked = _http_json(port, bridge.token, "/ui/delivered", {"msg_id": b_sleep_id})
            assert deliv_blocked["ok"] is False and deliv_blocked.get("blocked") is True

            # 07:05 醒来：tick 释放消息，at 仍停在 06:30，且 aux_pane 用原 cursor 能完整拉出
            sim_clock[0] = datetime(2026, 11, 25, 7, 5, 0)
            sim_blocked[0] = ""
            c.tick()
            sleep_ai = [m for m in c.visible() if m.get("role") == "ai"][-2:]
            assert [m["text"] for m in sleep_ai] == ["早安,睡醒了吗?", "昨晚休息得怎么样?"]
            assert all(m["at"].startswith("2026-11-25T06:30:") for m in sleep_ai), f"睡醒后应显示 06:30: {sleep_ai}"
            st_awake = _http_json(port, bridge.token, f"/ui/state?cursor={cursor_before_sleep}")
            assert [m["text"] for m in st_awake["messages"][-2:]] == ["早安,睡醒了吗?", "昨晚休息得怎么样?"]
            assert all(m["at"].startswith("2026-11-25T06:30:") for m in st_awake["messages"][-2:])

            # (b) 专注中提交 + /ui/delivered 也保留 06:30
            sim_clock[0] = datetime(2026, 11, 26, 6, 30, 0)
            sim_blocked[0] = "专注中不能使用沟通"
            rep_focus = _http_json(port, bridge.token, "/reply/submit", {
                "conversation_id": "conv-1",
                "bubbles": ["专注中先别看手机"],
            })
            assert rep_focus["ok"] and rep_focus.get("held") is True
            assert _http_json(port, bridge.token, "/ui/delivered", {"msg_id": rep_focus["items"][0]["id"]})["ok"] is False
            sim_clock[0] = datetime(2026, 11, 26, 7, 5, 0)
            sim_blocked[0] = ""
            c.tick()
            assert c.visible()[-1]["text"] == "专注中先别看手机"
            assert c.visible()[-1]["at"].startswith("2026-11-26T06:30:"), f"结束专注后应显示 06:30: {c.visible()[-1]}"

            # (c) AI 在专注中 (ai_free_at = 06:30) 且用户在睡眠 (06:28 -> 07:05)：不得被打字延时覆盖成 06:28 或 07:05
            free_0630 = datetime(2026, 11, 27, 6, 30, 0)
            sim_clock[0] = datetime(2026, 11, 27, 6, 28, 0)
            sim_blocked[0] = "睡眠中不能使用沟通"
            c._away_kind = lambda now=None: "focus"
            c.ai_free_at = lambda now=None: free_0630
            rep_late = _http_json(port, bridge.token, "/reply/submit", {
                "conversation_id": "conv-1",
                "bubbles": ["我刚结束专注"],
            })
            assert rep_late["ok"] and rep_late.get("held") is True
            assert rep_late["items"][0]["at"] == "2026-11-27T06:30:00"
            c._away_kind = lambda now=None: ""
            c.ai_free_at = lambda now=None: None
            sim_clock[0] = datetime(2026, 11, 27, 7, 5, 0)
            sim_blocked[0] = ""
            c.tick()
            assert c.visible()[-1]["text"] == "我刚结束专注"
            assert c.visible()[-1]["at"] == "2026-11-27T06:30:00", f"late 消息应显示 06:30:00: {c.visible()[-1]}"
        finally:
            c.now = orig_now
            c.blocked = orig_blocked
            c._away_kind = orig_away
            c.ai_free_at = orig_free

        # 9d-2. Tension / Valence 自动判断提示注入与结算回归验证
        tv_persona = os.path.join(personas_dir, "TVTest.txt")
        tv_tpl = os.path.join(personas_dir, "TVTest_templates.txt")
        with open(tv_persona, "w", encoding="utf-8") as fh:
            fh.write("你是测试角色 TVTest。")
        with open(tv_tpl, "w", encoding="utf-8") as fh:
            fh.write("[tension]\nclose:[12] 用户表达亲近\n[/tension]\n\n[valence]\nwarm:[+10] 用户表达善意\n[/valence]\n")
        orig_persona = c.persona_name()
        c.set_persona("TVTest.txt")
        _http_json(port, bridge.token, "/session/bind", {"conversation_id": "conv-tv", "persona_name": "TVTest", "host": "antigravity"})
        um_tv = c._add("me", "今天也很想你呀")
        c._interaction_sent(um_tv["text"], um_tv)
        ctx_tv = _http_json(port, bridge.token, "/turn/context?conversation_id=conv-tv&consume_trigger=1")
        assert "close:[12]" in ctx_tv["system_message"] and "warm" in ctx_tv["system_message"], "应注入 [tension]/[valence] 判断规则与待评消息"
        assert "今天也很想你呀" in ctx_tv["system_message"]
        rep_tv = _http_json(port, bridge.token, "/reply/submit", {
            "conversation_id": "conv-tv",
            "bubbles": ["嗯，我也在。"],
            "tension": [{"index": 1, "condition": "close", "gain": 12}],
            "valence": [{"index": 1, "condition": "warm", "shift": 10}],
        })
        assert rep_tv["ok"] and rep_tv["applied"]["tension"] > 0 and rep_tv["applied"]["valence"] > 0, f"应结算张力与效价: {rep_tv}"

        # 9d-3. Share Data 页面数据增量下发回归验证（确保 AI 能看到各页共享数据且不重复下发）
        c.share_fn = lambda page: ("计时器", "今天专注 90 分钟", "对方专注 60 分钟")
        c._share_send("timer")
        ctx_sd1 = _http_json(port, bridge.token, "/turn/context?conversation_id=conv-tv&consume_trigger=1")
        assert "[OIT共享数据]" in ctx_sd1["system_message"] and "今天专注 90 分钟" in ctx_sd1["system_message"]
        ctx_sd2 = _http_json(port, bridge.token, "/turn/context?conversation_id=conv-tv&consume_trigger=1")
        assert "[OIT共享数据]" not in ctx_sd2["system_message"], "已下发过的共享数据不应重复占用上下文"

        c.set_persona(orig_persona)
        _http_json(port, bridge.token, "/session/bind", {"conversation_id": "conv-1", "persona_id": c.pid, "host": "antigravity", "force": True})

        # 9e. 清空对话应解绑旧会话，且下次发消息时推送空 conversation_id 由 Sidecar 懒创建新会话
        c.clear()
        assert not bridge.is_bound_conversation("conv-1"), "clear() 后旧会话应自动解绑"
        ctx_after_clear = _http_json(port, bridge.token, "/turn/context?conversation_id=conv-1&only_bound=1")
        assert ctx_after_clear.get("bound") is False, "解绑后的旧会话应恢复与 OIT 隔离"
        new_sse = []

        def read_new_sse():
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/events/stream",
                headers={"Authorization": f"Bearer {bridge.token}"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                for raw in resp:
                    line = raw.decode("utf-8").strip()
                    if line.startswith("data:"):
                        new_sse.append(json.loads(line[5:].strip()))
                        break

        t_sse2 = threading.Thread(target=read_new_sse, daemon=True)
        t_sse2.start()
        time.sleep(0.2)
        assert bridge.push_user_message(c.pid, "清空后的第一句新消息") is True
        t_sse2.join(timeout=4)
        assert len(new_sse) == 1 and new_sse[0]["conversation_id"] == "" and new_sse[0]["text"] == "清空后的第一句新消息"

        # 9f. 重启竞态回归验证：旧 Bridge stop() 不得误删新 Bridge 的 mcp_bridge.json，且误删后 ensure_discovery() 自愈
        bridge2 = OitMcpBridge(c, tmp)
        try:
            bridge2.start()
            bridge.stop()
            assert os.path.isfile(bridge2.discovery_path), "旧 Bridge stop() 不得删除新 Bridge 的 mcp_bridge.json"
            with open(bridge2.discovery_path, "r", encoding="utf-8") as fh:
                assert json.load(fh)["token"] == bridge2.token
            os.remove(bridge2.discovery_path)
            bridge2.ensure_discovery()
            assert os.path.isfile(bridge2.discovery_path), "ensure_discovery() 应自动恢复缺失的 mcp_bridge.json"
        finally:
            bridge2.stop()
        bridge = None

        # 10. 设置页「帮助」与接管功能帮助弹窗实际打开验证（离屏 tkinter）
        import tkinter as tk
        from pages.settings_page.settings_form import FormMixin
        from pages.ai_dialogs import open_takeover_feature_help
        from mcp_bridge import takeover_feature_help_text, takeover_feature_prompt
        for feat in ("suggest", "persona"):
            assert "用完删除" in takeover_feature_help_text(feat, en=False)
            assert "Delete the temporary conversation" in takeover_feature_help_text(feat, en=True)
            assert takeover_feature_prompt(feat, en=False) and takeover_feature_prompt(feat, en=True)
        assert "oit_claudia_reference" in takeover_feature_prompt("persona", en=False)
        assert "简略" in takeover_feature_prompt("persona", en=False) and "详细" in takeover_feature_prompt("persona", en=False)
        assert "Brief" in takeover_feature_prompt("persona", en=True) and "Detailed" in takeover_feature_prompt("persona", en=True)
        assert "萌娘百科" in takeover_feature_help_text("persona", en=False)
        assert "Moegirlpedia" in takeover_feature_help_text("persona", en=True)

        # 10a. API 版 5 文件人设生成、修改与保存验证
        from comm import comm_author
        fake_prof = {"provider": "openai", "protocol": "openai", "api_key": "sk-test", "base_url": "https://api.example.com/v1", "model": "gpt-4o"}
        def _fake_chat(_prof, _sys, msgs, **_kw):
            payload = msgs[-1]["content"]
            assert "OIT-PERSONA-ENC:" not in payload, "发给 API 的 Claudia 参考必须已解密"
            if "revision_instruction" in payload:
                return json.dumps({
                    "persona": "修改后的主人设", "background": "修改后的背景",
                    "example": "用户：早\n角色：早安。", "templates": "[schedule]\n08:00-09:00 idle",
                    "profile": "修改后的简介",
                }, ensure_ascii=False)
            return json.dumps({
                "persona": "初始主人设", "background": "初始背景",
                "example": "用户：你好\n角色：你好呀。", "templates": "",
                "profile": "初始简介",
            }, ensure_ascii=False)
        d0 = comm_author.generate(fake_prof, "测试角色五件套", "轻松温柔", "", chat=_fake_chat, mode="brief", folder=personas_dir)
        assert d0.persona == "初始主人设" and d0.background == "初始背景" and d0.example
        d1 = comm_author.revise(fake_prof, "测试角色五件套", d0, "把语气改得更活泼", chat=_fake_chat, mode="brief", folder=personas_dir)
        assert d1.persona == "修改后的主人设" and d1.templates
        saved_base = comm_author.save(personas_dir, "测试角色五件套", d1)
        assert saved_base == "测试角色五件套.txt"
        for fn in ("测试角色五件套.txt", "测试角色五件套_background.txt", "测试角色五件套_example.txt", "测试角色五件套_templates.txt", "测试角色五件套_profile.txt"):
            assert os.path.isfile(os.path.join(personas_dir, fn)), f"应保存 {fn}"
        root = tk.Tk()
        root.withdraw()
        try:
            class _Dummy(tk.Frame, FormMixin):
                pass
            host = _Dummy(root)
            root.after(120, lambda: [w.destroy() for w in root.winfo_children() if isinstance(w, tk.Toplevel)])
            host._antigravity_help()
            root.after(120, lambda: [w.destroy() for w in root.winfo_children() if isinstance(w, tk.Toplevel)])
            open_takeover_feature_help(root, "suggest")
            root.after(120, lambda: [w.destroy() for w in root.winfo_children() if isinstance(w, tk.Toplevel)])
            open_takeover_feature_help(root, "persona")
        finally:
            root.destroy()

        print("MCP BRIDGE & SERVER CHECK OK")
    finally:
        config.AI_CHAT_TAKEOVER = old_takeover
        if http_proc is not None:
            http_proc.terminate()
        if bridge is not None:
            bridge.stop()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
