# token-efficiency

来源：Claude 项目记忆 feedback_token_efficiency.md，已转换为 Codex 项目说明。

用户说过"这次的 token 消耗量巨大", 并专门问项目要怎么改才能减小消耗 —— 说明 token 成本是他在意的实际指标.

**Why:** 项目里几个几百 KB 的单体文件 (app.py 326KB, MECHANICS.md 374KB, comm.py 489KB, contract.py/devsync.py/i18n.py/ai_assist.py 100-160KB) 让"定位"和"验证"两步被放大; 修 prestart 这种跨十几个片段的问题时尤其明显.

**How to apply:** 先用 `rg -n` 定位，再按行号局部读取; 不为"了解全貌"整读大文件; 验证优先跑现成的 `_*_check.py` 与模块 selftest (`python prestart.py` 等), 不靠反复读代码推断语义; 不重复读同一片段; 宽泛的跨文件搜索先用 `rg` 缩小范围；当前任务和运行环境允许委派时，才交给 Codex 子代理只带回结论. 做结构性改进时优先考虑拆 app.py / MECHANICS.md 和建统一检查入口.

纯文本修改（如人设、提示词、文案、文档）无需运行测试，只核对内容与双端同步；这是用户于 2026-11-14 补充的长期偏好。

相关：[开发收尾流程](feedback_release_workflow.md)

迁移说明：上面提到的大型单文件及大小是历史背景；当前模块拆分位置以根目录 `AGENTS.md` 的实现地图为准。
