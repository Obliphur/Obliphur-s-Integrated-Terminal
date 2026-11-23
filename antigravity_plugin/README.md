# OIT Antigravity Plugin

将 Obliphur's Integrated Terminal (OIT) 接入 Antigravity 桌面端（同时兼容 Claude Desktop / Claude Code / ChatGPT Streamable HTTP）。

## 安装与启用

1. 在 OIT「设置 → 模型」中开启 **由 Antigravity 桌面版接管**（OIT 会自动在本地启动 Loopback HTTP/SSE Bridge 并写入 `<OIT_DATA_DIR>/mcp_bridge.json`）。
2. 构建并安装本插件：
   ```bash
   cd antigravity_plugin/server && npm install && npm run build
   cd ../ui && npm install && npm run build
   agy plugin validate ./antigravity_plugin
   agy plugin install ./antigravity_plugin
   ```
3. 如需启用**主动对话唤醒**，请在 Antigravity App 的 **Automations / Sidecars** 面板中手动启用 `OIT Proactive Bridge`（`oit/oit-proactive`）并选择目标项目（`projectId`）。
