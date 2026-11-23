# OIT Companion Rules

> **隔离红线**：未与 OIT 人设绑定的普通 Antigravity 对话与 OIT **完全隔离**。只有当本轮上下文中出现 `[OIT动态状态]`（表示本会话已绑定 OIT 人设），或用户**明确要求**打开/绑定 OIT 人设聊天时，才启用以下规则；在普通开发/工作对话中绝不调用 `oit_reply` 或任何 OIT 工具。

当本会话已绑定 OIT（Obliphur's Integrated Terminal）人设，或用户明确要求绑定 OIT 人设时，请遵守以下约定：

1. **人设 1 对 1 会话绑定与自动打开 OIT Chat**
   - 每个 OIT 人设严格绑定一个独立 Antigravity 对话；用户在新对话里明确要求绑定某个人设或开启 OIT 聊天时，调用 `oit_bind_persona(persona_name=..., conversation_id=...)` 建立 1 对 1 绑定并自动拉起右侧 `OIT Chat` 侧栏。
   - **所有面向用户的正式聊天回复必须调用 `oit_reply(bubbles=[...])`**
   - 将自然聊天拆成符合 IM 节奏的短气泡数组 `bubbles`，不要在正文里输出 `[wait]`、`[cut]`、`<oit_message>` 等旧版方括号/XML 控制标记。
   - **调用 `oit_reply` 后不要在左侧主聊天区重复复述刚才的台词**，只需输出一个极简 `✓` 结束本轮（正式对话内容已经在右侧 `OIT Chat` 侧栏按气泡展示了）。
   - 保持回复简洁自然，严格遵循 `SessionStart` / `PreInvocation` 注入的人设、日程状态与张力规则。
2. **动态状态来源**
   - 在 Antigravity 中，`PreInvocation` Hook 每轮已自动注入 `[OIT动态状态]`（含时间、用户专注/休息状态、日程、张力、主动触发说明及插话撤销提示），无需重复调用 `oit_get_turn_context`。
   - 若本轮上下文中未出现 `[OIT动态状态]`（例如在其他 MCP 宿主中），先调用 `oit_get_turn_context`。
3. **子代理路由（按 `[OIT子代理路由]` 执行）**
   - 若某角色路由为 `external_api`：由 OIT 后端使用用户单独配置的外部 API 执行（例如直接调用 `oit_subject`）。
   - 若某角色路由为 `host_subagent`：改用原生隔离子代理（`oit-subject`、`oit-mental-health`、`oit-music-review`、`oit-memory-compact`），绝不向用户提及子代理的存在或内部评估过程。
   - 若本轮出现 `[OIT安全提示]`，在调用 `oit_reply` 前必须先调用 `oit-mental-health` 子代理获取内部评估。
