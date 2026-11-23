# -*- coding: utf-8 -*-
"""端到端检查：OIT MCP Bridge（HTTP + SSE）、子代理双路由、Antigravity Hooks、TypeScript MCP Server（stdio + Streamable HTTP）与 aux_pane 资源。"""

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
        c = comm.Comm(tmp, personas_dir)
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
        assert "oit_get_turn_context" in tools and "oit_memory_save_auto" in tools

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
        assert "自动部署" in antigravity_help_text(en=False) and "Ctrl+Shift+M" in antigravity_help_text(en=False)
        assert "Auto-Deploy" in antigravity_help_text(en=True) and "Ctrl+Shift+M" in antigravity_help_text(en=True)

        # 9. 设置页「帮助」弹窗实际打开验证（离屏 tkinter）
        import tkinter as tk
        from pages.settings_page.settings_form import FormMixin
        root = tk.Tk()
        root.withdraw()
        try:
            class _Dummy(tk.Frame, FormMixin):
                pass
            host = _Dummy(root)
            root.after(120, lambda: [w.destroy() for w in root.winfo_children() if isinstance(w, tk.Toplevel)])
            host._antigravity_help()
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
