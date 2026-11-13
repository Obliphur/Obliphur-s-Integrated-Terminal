# release-workflow

来源：Claude 项目记忆 feedback_release_workflow.md，已转换为 Codex 项目说明。

每次开发完成后, 直接做三件事, 不用再问:
1. 递增版本号, 两端一起改 (一次都不能漏):
   - `config.py` 的 `APP_VERSION`;
   - `Android version/app/build.gradle.kts` 的 `versionName` (与 APP_VERSION 保持一致) 和 `versionCode` (+1).
   没改 Android 代码也要升安卓版本号. 现读现值再 +1, 不要凭记忆写数字.
   (`Android version/app/build/` 下生成的 AndroidManifest.xml 是产物, 不用改.)
2. 自动提交, commit 信息就写 `1` (不加 Co-Authored-By 之类的尾巴), 只 add 本次改动的文件, 不提交未跟踪的 `CLAUDE.md`.
3. 提交后推送到 `origin main`.

**Why:** 用户明确要求"总是递增, 并且自动提交, 信息为'1'" 以及"并且总是推送", 又补充"更新版本号要带上安卓端的版本号和 versioncode". 这是长期授权, 覆盖默认的"先确认再提交/推送".

**How to apply:** 任务收尾时, 跑完相关自检（纯文本修改无需运行测试，只核对内容与双端同步）, 递增 APP_VERSION + 安卓 versionName/versionCode, `git add` 具体文件, `git commit -m "1"`, `git push`. 不要 force push. 如果推送失败 (比如远端变化), 报告给用户而不是改远端配置.
