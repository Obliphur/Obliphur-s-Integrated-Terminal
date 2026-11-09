# CLAUDE.md

OIT (Obliphur's Integrated Terminal)：Windows 10/11 + Python 3.10+ 专注计时/断网程序（tkinter，仅标准库，可选 pywin32），外加 `Android version/` 的 Kotlin/Compose 手机版。入口 `python main.py`，需管理员权限。

## 命令

```bash
python main.py                  # 运行（自动提权）
python <module>.py              # 模块自检，如 net_guard.py / settings_manager.py / prestart.py / netproxy/selftest.py
python setup.py --selftest      # 改 setup.py / registration.py 后跑
python registration.py          # 注册表相关自检
python release.py               # 发布（PyInstaller）
```

- 无统一测试套件：各文件 `if __name__ == "__main__"` 的断言即自检。
- 别在本机试跑 `setup.exe`（会装 Program Files 并写注册表）。
- `config_local.py` 只在 git 开发版生效，用于本地覆盖 config 数值。
- 每次开发完成递增 `config.py` 的 `APP_VERSION`。
- 别提交调试/预览截图，截图放系统临时目录。

## 检查脚本（根目录 `_*_check.py`）

这些是带断言的独立脚本：`python _xxx_check.py`，退出码 0 且最后打印 `… OK` 即通过。它们会开 tkinter 窗口，窗口开在屏幕外；不碰真实数据（用临时目录）。

| 脚本 | 覆盖 | 改这些之后跑 |
| --- | --- | --- |
| `_prestart_check.py` | 提前启动：挑选/搜索/去重逻辑，弹窗 | `prestart.py`、`pages/prestart_dialog.py` |
| `_hold_check.py` | 休息 / 外出 / 联网时不能开始合约（`contract_block`） | `app.contract_block`、合约开始入口 |
| `_ui_check.py` | 合约页底部按钮：变灰 + 上方提示 + 恢复 | `pages/contract_page.py` 的 `_build_bar` / `refresh` |
| `_contract_board_check.py` | 词条板分页 / 翻页 / 组合页排版 / 锁定描述 | `contract.py` 的 `TERMS`、`PAGES`、`LAYOUT`、`_draw_*` |
| `_stats_ui_check.py` | 统计页：合约记录、违规记录、黑雾、等级写法 | `pages/stats_page.py`、`contract.level_label` |
| `_behavior_check.py` | 行为模式 / 外出与数据叠加 | `behavior.py`、`data_manager.py` |
| `_daily_ui_check.py` | 设置 → 日常（睡觉时间、行为模式） | `pages/settings_page.py` |
| `_ai_dialog_check.py` | AI 模型设置弹窗：小屏 / 大字号下能滚到底、字段点得到 | `pages/ai_dialogs.py`、`pages/widgets.py` 的 `Modal` / `ScrollArea` |
| `_joint_invite_check.py` | AI 发起联合行动的邀请模式 | `pages/contract_page.py` 邀请、`app_comm._joint_invite_watch` |

**规矩**：

- 新增 `*_check.py` 必须登记到上表，否则下一个会话找不到它。
- 提交前跑与改动相关的检查；红了当场修，修不动就删掉——留一个红着的检查比没有更费时间。
- 检查脚本进仓库，不再被 `.gitignore` 忽略。

## 实现地图（`app.py`，约 400 行）

`BreakTimerApp` 由 16 个 mixin 拼成；`app.py` 只剩 import、类声明和 `__init__`（装配：读设置/合约/同步状态，建页面，起各种定时器）。找方法直接看下面这张表，不用再翻 6000 行的机身。

| 文件 | Mixin | 内容 |
| --- | --- | --- |
| `app_base.py` | — | 无状态小工具：`ALARM_WAV` / `_play_audio_file` / `tired_text` / 颜色混合 |
| `app_window.py` | WindowMixin | 窗口与主题、布局、重建界面、页面切换（`show_page`） |
| `app_pages.py` | PagesMixin | 日程跟随、闹钟、规则弹窗、侧边栏、计时页 UI 与按钮状态 |
| `app_guard.py` | GuardMixin | 窗口事件与 5 秒心跳、窗口守护、自启、网卡重叠/应用规则/窗口使用时间 |
| `app_timer.py` | TimerMixin | 状态机入口、专注存档恢复、结算、每秒 tick、跨天/整点、提示音 |
| `app_break.py` | BreakMixin | 奖励休息、开始/暂停休息、结转、休息后提醒 |
| `app_daily.py` | DailyMixin | 睡觉、外出与行为模式、提前启动 |
| `app_contract.py` | ContractMixin | 合约检查 / 开始 / 结束 |
| `app_contract_media.py` | ContractMediaMixin | 合约录像、合并与发送、违规、禁网 |
| `app_comm.py` | CommMixin | 沟通页状态、分享、联合行动 |
| `app_sync.py` | SyncMixin | 设备同步、检查与安装更新 |
| `app_net.py` | NetMixin | 联网页、额度/申请/借贷、`_net_tick`、源码与时间篡改检查 |
| `app_settings.py` | SettingsMixin | 设置生效、48 小时冷静期、关闭程序、日志与徽章 |
| `app_achievements.py` | AchievementsMixin | 成就判定 / 同步 / 弹窗 |
| `app_prescript.py` | PrescriptMixin | 指令（都市之意）：抽取 / 计时 / 刻痕 |
| `app_update.py` | UpdateMixin | 开发者模式跟着母端、更新中继 |
| `app_misc.py` | MiscMixin | 配置期、第一次打开的快捷方式、重置页 |

高频定位：断网判定在 `netproxy/rules.py` 的 `decide()`；数据读写看 `data_manager.py` + MECHANICS 第 10 节；设置项在 `settings_manager.py` + `pages/settings_page.py`；页面 UI 在 `pages/`，`app.py` 只留状态与调度。

## 读文件规则（控制上下文）

- **禁止整文件读取大文件**：`comm.py`(500KB)、`app_timer.py`(45KB)、`app_contract_media.py`、`contract.py`、`devsync.py`、`i18n.py`、`ai_assist.py` 等。先用 Grep 搜关键词定位行号，再用 Read 的 `offset/limit` 只读相关片段。
- **查机制先看 `MECHANICS.md` 的目录**，再只读 `docs/mechanics/` 里对应的那一节（多数 1–30KB；18 合约 56KB、24 沟通 119KB 仍然只读相关段落）。不要通读。
- 宽泛的跨文件搜索交给 Explore 子代理，只带回结论。

## 硬性约定（见 for_developer.md）

1. 改机制前查 `docs/mechanics/` 里对应的那一节（目录在 `MECHANICS.md`）保兼容；改完同步 `i18n.py`、那一节、`README.md`（README 只记模块级要点，细节进 MECHANICS）。
2. 注意**跨天问题**（额度、整点记录、睡觉时间等跨零点）。
3. 设置项按模块分页（顺序同左侧导航，基本设置永远第一页）；新功能：选项在上、数值在下。
4. UI 文案走 `i18n.py` 的 `tr()`，不加小字解释。
5. 双端功能实时适配；设备同步每 20s 由母端检测，以"焦点设备"操作为最新。
6. 开发者模式只在 git 开发版存在。

## 架构

- **`config.py` 是数值中枢**：默认常量都在此，`settings_manager.py` 把用户设置 apply 回 config 的 globals（代码里常见 `config.X` / `globals().get(...)`）。别处不要复制数值。
- **`app.py` 只剩装配**：`__init__` 里读状态、建页面、挂定时器；页面、计时器状态机（`IDLE / RUNNING / READY_BREAK / ON_BREAK`）、tick、关闭/重置都在 `app_*.py` 的 mixin 里（明细见「实现地图」）。页面 UI 在 `pages/`。再拆新子系统就照这个模式：一个 `app_xxx.py` + 一个 `XxxMixin`，状态仍全放 `self`，无状态工具放 `app_base.py`。
- **断网两条路**（`NET_MODE`）：Adapter 直接禁网卡；Proxy 走 `netproxy/` 过滤代理（`bypassguard.py` 补拦直连）。优先级链在 `rules.py` 的 `decide()`：localhost → 合约网络禁令 → 联网会话 → 提前启动豁免 → 全屏蔽 → 白/黑名单。
- **提前启动豁免**：`app_daily.py` 每秒把 `prestart.exe_names()`（含子进程）刷到 `rules.exempt`，绕过名单和全屏蔽，但绕不过合约网络禁令。
- **合约 override 设置**：`config.CONTRACT_OVERRIDES` 由 settings_manager 套在生效值之上。
- 数据在 `%USERPROFILE%\AppData\LocalLow\Obliphur's Integrated Terminal\`，发布版经 `securefile.py` 加密；`continuity.py` 处理数据被搬走/回滚。改数据读写前读 `docs/mechanics/10-data-files.md`、`15-sync.md`。
- 手机版对应模块在 `Android version/app/src/main/java/com/obliphur/oit/core/`，与桌面版同名一一对应，改一边要查另一边。

## 风格

注释、docstring 用中文；文案走 `tr()`，不硬编码字符串。
