# CLAUDE.md

OIT (Obliphur's Integrated Terminal)：Windows 10/11 + Python 3.10+ 专注计时/断网程序（tkinter，仅标准库，可选 pywin32），外加 `Android version/` 的 Kotlin/Compose 手机版。入口 `python main.py`，需管理员权限。

## 命令

```bash
python main.py                  # 运行（自动提权）
python -m <module>              # 模块自检，如 python -m net_guard / python -m settings_manager / python -m netproxy.selftest
python setup.py --selftest      # 改 setup.py / registration.py 后跑
python registration.py          # 注册表相关自检
python release.py               # 发布（PyInstaller）
```

- 无统一测试套件：各模块 `__main__.py` / `if __name__ == "__main__"` 里的断言即自检（包用 `python -m <包>`）。
- 别在本机试跑 `setup.exe`（会装 Program Files 并写注册表）。
- `config_local.py` 只在 git 开发版生效，用于本地覆盖 config 数值。
- 每次开发完成递增 `config.py` 的 `APP_VERSION`，同时把 `Android version/app/build.gradle.kts` 的 `versionName` 设为同一个版本号、`versionCode` 加一（手机版与桌面版版本号保持一致）。
- 别提交调试/预览截图，截图放系统临时目录。

## 检查脚本（`checks/_*_check.py`）

这些是带断言的独立脚本：`python checks/_xxx_check.py`，退出码 0 且最后打印 `… OK` 即通过。它们会开 tkinter 窗口，窗口开在屏幕外；不碰真实数据（用临时目录）。

| 脚本 | 覆盖 | 改这些之后跑 |
| --- | --- | --- |
| `_prestart_check.py` | 提前启动：挑选/搜索/去重逻辑，弹窗 | `prestart.py`、`pages/prestart_dialog.py` |
| `_hold_check.py` | 休息 / 外出 / 联网时不能开始合约（`contract_block`） | `app.contract_block`、合约开始入口 |
| `_ui_check.py` | 合约页底部按钮：变灰 + 上方提示 + 恢复 | `pages/contract_page.py` 的 `_build_bar` / `refresh` |
| `_contract_board_check.py` | 词条板分页 / 翻页 / 组合页排版 / 锁定描述 | `contract/contract_rules.py` 的 `TERMS`、`PAGES`、`LAYOUT`；`_draw_*` 在 `pages/contract_page.py` |
| `_stats_ui_check.py` | 统计页：合约记录、违规记录、黑雾、等级写法 | `pages/stats_page.py`、`contract.level_label` |
| `_behavior_check.py` | 行为模式 / 外出与数据叠加 | `behavior.py`、`data_manager/` |
| `_daily_ui_check.py` | 设置 → 日常（睡觉时间、行为模式） | `pages/settings_page/` |
| `_ai_dialog_check.py` | AI 模型设置弹窗：小屏 / 大字号下能滚到底、字段点得到 | `pages/ai_dialogs.py`、`pages/widgets.py` 的 `Modal` / `ScrollArea` |
| `_joint_invite_check.py` | AI 发起联合行动的邀请模式 | `pages/contract_page.py` 邀请、`app_comm._joint_invite_watch` |
| `_focus_check.py` | 焦点设备（提示音 / 刻痕）的「最后操作时刻」：Windows 全系统输入、只前进、取不到时不动 | `app/app_sync.py` 的 `system_idle_ms` / `_note_input`、`sync_local.py` 的 `status()` |
| `_temp_check.py` | 临时文件都在 `%TEMP%\OIT` 里：启动清旧（含旧版本散落的 `oit*` 与顶层 `tmp*` 自检残留）、自检自动开会话退出即删、开会话清 1 小时前残留、重置 / 卸载删干净 | `tempstore.py`、`recorder/`、`updater.py`、`setup.py`、`netproxy/sitescan.py` |

**规矩**：

- 新增 `checks/*_check.py` 必须登记到上表，否则下一个会话找不到它。
- 提交前跑与改动相关的检查；红了当场修，修不动就删掉——留一个红着的检查比没有更费时间。
- 检查脚本进仓库，不再被 `.gitignore` 忽略。

## 实现地图（`app/`，约 400 行）

`BreakTimerApp` 由 16 个 mixin 拼成；`app/` 只剩 import、类声明和 `__init__`（装配：读设置/合约/同步状态，建页面，起各种定时器）。找方法直接看下面这张表，不用再翻 6000 行的机身。

| 文件 | Mixin | 内容 |
| --- | --- | --- |
| `app/app_base.py` | — | 无状态小工具：`ALARM_WAV` / `_play_audio_file` / `tired_text` / 颜色混合 |
| `app/app_window.py` | WindowMixin | 窗口与主题、布局、重建界面、页面切换（`show_page`） |
| `app/app_pages.py` | PagesMixin | 日程跟随、闹钟、规则弹窗、侧边栏、计时页 UI 与按钮状态 |
| `app/app_guard.py` | GuardMixin | 窗口事件与 5 秒心跳、窗口守护、自启、网卡重叠/应用规则/窗口使用时间 |
| `app/app_timer.py` | TimerMixin | 状态机入口、专注存档恢复、结算、每秒 tick、跨天/整点、提示音 |
| `app/app_break.py` | BreakMixin | 奖励休息、开始/暂停休息、结转、休息后提醒 |
| `app/app_daily.py` | DailyMixin | 睡觉、外出与行为模式、提前启动 |
| `app/app_contract.py` | ContractMixin | 合约检查 / 开始 / 结束 |
| `app/app_contract_media.py` | ContractMediaMixin | 合约录像、合并与发送、违规、禁网 |
| `app/app_comm.py` | CommMixin | 沟通页状态、分享、联合行动 |
| `app/app_sync.py` | SyncMixin | 设备同步、检查与安装更新 |
| `app/app_net.py` | NetMixin | 联网页、额度/申请/借贷、`_net_tick`、源码与时间篡改检查 |
| `app/app_settings.py` | SettingsMixin | 设置生效、48 小时冷静期、关闭程序、日志与徽章 |
| `app/app_achievements.py` | AchievementsMixin | 成就判定 / 同步 / 弹窗 |
| `app/app_prescript.py` | PrescriptMixin | 指令（都市之意）：抽取 / 计时 / 刻痕 |
| `app/app_update.py` | UpdateMixin | 开发者模式跟着母端、更新中继 |
| `app/app_misc.py` | MiscMixin | 配置期、第一次打开的快捷方式、重置页 |

### 沟通模块（`comm/`）

`comm/`（约 1360 行）只留装配：常量再导出、`char_delay` / `follow_delay`、`Comm` 的 `__init__` 与聊天存档 / 设备同步 / 人设文件与升级链、`_mk` / `_finish` / `tick` 等要读模块全局量的方法。`Comm` 由 8 个 mixin 拼成；自检（约 3000 行）在 `comm/comm_selftest.py`，`python -m comm` 时用 `exec(..., globals())` 在 comm 的命名空间里执行——所以它照旧能 `global FOLLOW_DELAY`，也能直接读常量。依赖方向：`comm/` 导入它们，它们从不导入 `comm/`。

| 文件 | Mixin | 内容 |
| --- | --- | --- |
| `comm/comm_joint.py` | JointMixin | 联合行动：邀请 / 开始 / 结束 / 生命值 / 同步阶段（24.9） |
| `comm/comm_status.py` | StatusMixin | 对方状态 / 静音与共时闹钟 / 思考 / 主动谈话语言 / 日程跟随（24.1、24.17） |
| `comm/comm_chat.py` | ChatMixin | 头像 / 未读 / 空闲额度 / 消息组装与发送（24.2、24.3） |
| `comm/comm_flow.py` | FlowMixin | 切断 / 限时网络 / 记忆卡 / 请求发出（24.7、24.8、24.11） |
| `comm/comm_sched.py` | SchedMixin | 日程与段 / 续话 / 后台对象 / 主动消息判定（24.4、24.5） |
| `comm/comm_share.py` | ShareMixin | share data / 起点平移（24.13、24.14） |
| `comm/comm_rt.py` | RtMixin | 日程跟随 / 条件与告白 / 主动消息触发（24.15、24.16、24.18） |
| `comm/comm_misc.py` | MiscMixin | 清空聊天记录 |

无状态的部分（mixin 和 `comm/` 都从这里取）：

| 文件 | 内容 |
| --- | --- |
| `comm/comm_const.py` | 标签 / 概率 / 超时 / 默认值 / 提示词片段等全部模块常量（`comm/` 用 `import *` 再导出） |
| `comm/comm_util.py` | 无状态小工具：消息包装（`oit_text`）、历史截取（`api_messages`）、记忆卡整理、拒绝语判断、缓存布局 |
| `comm/comm_persona.py` | 人设文件 / 伴侣文件 / 日程文件的读写、平移与告白设置、系统 prompt 拼装 |
| `comm/comm_prompt.py` | 前置 prompt 文本（日程 / 休息函数 / 回复规则）与各种 `[标记]` 常量 |
| `comm/comm_reply.py` | 回复解析：去掉标记、`[wait]` / `[cut]` / `[network]` 的分钟数、半角化 |
| `comm/comm_schedule.py` | 人设日程解析：段类型 / 段内容、循环段、主动发消息概率 |
| `comm/comm_builtin.py` | 内置人设、默认背景 / 日程 / 助理文本、旧版升级链 |
| `comm/comm_readme.py` | OIT_personas 文件夹里的 README 文本（中 / 英） |
| `comm/comm_data.py` | 今天 / 日历 / 统计 / 合约记录等数据的文字化（分享给对方、附件用） |

自检会改写的模块全局量（`CLAUDIA_LEGACIES`、`ROUTINE_LEGACIES`、`FOLLOW_DELAY`、`CHAR_DELAY`、`CHAR_DELAY_CJK`）默认值在 `comm/comm_const.py`，但 `comm/` 通过 `import *` 持有自己的绑定；用到它们的函数（`char_delay` / `follow_delay` / `_mk` / `_finish` / 升级链 / `__init__`）都还在 `comm/`，自检 `exec` 进 comm 的命名空间去改，所以改到的就是这些函数读的那一份。

### 录像模块（`recorder/`）

`recorder/` 只剩 `Recorder` 线程和自检（`python -m recorder`、`python -m recorder --cleanup-test`）；常量和无状态的部分拆到 `recorder/`。依赖方向：`recorder/` 导入它们，它们从不导入 `recorder/`；`recorder/` 用显式 import 重新导出旧名字，`recorder.work_dir` / `recorder.merge` 之类照旧。

| 文件 | 内容 |
| --- | --- |
| `recorder/recorder_common.py` | 常量：后缀 / 加密头 / 分段与画质 / 清理期限 |
| `recorder/recorder_store.py` | 文件夹布局、加密（`_Aes` / `rec_xor` / `.oitv`）、播放、清理、合并结果的存放 |
| `recorder/recorder_media.py` | Media Foundation / GDI / 摄像头的 ctypes 封装：`_Writer`、`_Screen`、摄像头枚举、读 mp4 的 Source Reader 工具 |
| `recorder/recorder_merge.py` | 合并：画面转正 / 缩放 / 画布、重编码拼接、`merge()` |

### AI 接口模块（`ai_assist/`）

`ai_assist/`（约 800 行）只剩名单建议的提示词、解析（`extract_known` / `parse_suggestions`）和自检；服务商配置、发请求、联网搜索拆到 `ai_assist/`。依赖方向：`ai_assist/` 导入它们，它们从不导入 `ai_assist/`；`ai_assist/` 用显式 import 重新导出所有旧名字（`ai_assist.PROVIDERS` / `AIConfig` / `chat_messages` 照旧）。自检改写 `SEARCH_URL` / `_dpapi` / `can_encrypt` / `_OPENER` / `_DIRECT` 时，直接改对应 parts 模块的属性。

| 文件 | 内容 |
| --- | --- |
| `ai_assist/ai_common.py` | 常量 / `AIError` / 提示词 / `DEFAULT_TEMPLATE`（`defaults/` 按仓库根定位） |
| `ai_assist/ai_key.py` | API Key 的 DPAPI 加解密、错误信息里抹掉 Key |
| `ai_assist/ai_config.py` | 服务商配置的加载 / 保存 / 加密 / 校验 / 选择 |
| `ai_assist/ai_http.py` | 三种协议的发请求、重试、用量、思考、图片、模型列表 |
| `ai_assist/ai_web.py` | 联网搜索（OIT 自己抓 Bing，给没有搜索接口的模型用） |

### 设备同步模块（`devsync/`）

`devsync/`（约 1020 行，其中自检约 600 行）只剩装配：`MainRunner`、`SyncService` 的 `__init__` / 存档 / `_http` / 模块级发送函数 / 自检，并重新导出旧名字。`SyncService` 由 7 个 mixin 拼成；共享常量与纯工具在 `devsync/devsync_util.py`。依赖方向：`devsync/` 导入它们，它们从不导入 `devsync/`。

| 文件 | Mixin | 内容 |
| --- | --- | --- |
| `devsync/devsync_discover.py` | DiscoverMixin | UDP 广播发现 / HTTP 端点 / 启动停止 / hello |
| `devsync/devsync_pair.py` | PairMixin | DH 配对 / 配对码确认 / 解绑 / 忘记本机 |
| `devsync/devsync_wipe.py` | WipeMixin | 换设备清除数据 / wipe 去重 / 保留本机数据 |
| `devsync/devsync_link.py` | LinkMixin | 离线检测 / 请求重启 / 解绑与强制下线 |
| `devsync/devsync_docs.py` | DocSyncMixin | 快照 / 合并 / 每 20 秒同步 / 关闭前推送 |
| `devsync/devsync_transfer.py` | TransferMixin | 文件通道：安装包 / 录像 / 照片 |
| `devsync/devsync_status.py` | StatusMixin | 设备状态 / 账本合计 / 适配器与离线时间段 |
| `devsync/devsync_util.py` | — | 常量 + 纯工具（`refused` / `_now_ms` / `_hash` / `http_call` / `_make_handler` / 发送函数…） |

`SyncService._http` 是 `http_call` 的薄包装；`_send_wipe_to` 之类的薄包装方法让自检改写 `devsync.send_restart_to` 仍然生效。

### 合约模块（`contract/`）

`contract/`（约 770 行，其中自检约 710 行）只剩 `Contract.__init__`、`cell_fill` 的再导出和自检；`Contract` 由 7 个 mixin 拼成，纯规则仍在 `contract/contract_rules.py`。依赖方向：`contract/` 导入它们，它们从不导入 `contract/`；`contract/` 用显式 import 重新导出 `contract_rules` 的名字，`C.TERMS` 之类的写法不变。

| 文件 | Mixin | 内容 |
| --- | --- | --- |
| `contract/contract_store.py` | StoreMixin | 存档读写、`doc` / `merge`、时间重叠的两个合约（冲突 / 作废） |
| `contract/contract_live.py` | LiveMixin | 进行中的合约：状态 / 词条 / 危机等级 / 开始与结束 / 到期与空闲累计 |
| `contract/contract_net.py` | NetMixin | 网络禁令：乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒 |
| `contract/contract_random.py` | RandomMixin | 未知变量：每小时随机启用词条或施加效果、记录封锁的格子 |
| `contract/contract_watch.py` | WatchMixin | 软规则：黑雾 / 全频道阻塞 / 通道阻塞 / 录像 / 抑制 / 纵深 / 静默 |
| `contract/contract_effects.py` | EffectsMixin | 开始前积攒的休息时间、支援短缺、词条对设置 / 组合 / 休息结算的影响 |
| `contract/contract_hours.py` | HoursMixin | 整点记录：记录格 / 补填时限（`cell_fill`）/ 超时违反 / 休息中的格子 / 记录封锁 |

### 指令模块（`prescript/`）

`prescript/`（约 620 行，其中自检约 520 行）只剩 `PrescriptEngine.__init__` 与自检；常量 / 无状态小工具在 `prescript/prescript_common.py`，`PrescriptEngine` 由 7 个 mixin 拼成。依赖方向：`prescript/` 导入它们，它们从不导入 `prescript/`；`prescript/` 用显式 import 重新导出旧名字（`prescript.IDLE_ENTRIES` / `pad_code` / `type_code` 照旧）。

| 文件 | Mixin | 内容 |
| --- | --- | --- |
| `prescript/prescript_common.py` | — | 常量（池子概率 / 时限 / 蓝色代码 / 日常目标）+ `_iso` / `_parse` / `fmt_t` / `pad_code` / `rest_minutes` / `fmt_clock` 等纯工具 |
| `prescript/prescript_store.py` | StoreMixin | 存档读写、换日、每天的随机次数 |
| `prescript/prescript_draw.py` | DrawMixin | 当前指令、池子 / 权重 / 空闲词条时段、领取 |
| `prescript/prescript_flow.py` | FlowMixin | 完成 / 作废、页面停留、合约匹配、每秒 tick |
| `prescript/prescript_daily.py` | DailyMixin | 奖励与当天合计、凌晨的休息时间 |
| `prescript/prescript_sync.py` | SyncMixin | 设备同步：`journal_view` / `sync_doc` / `sync_apply` |
| `prescript/prescript_log.py` | LogMixin | X 词条的记录输入框 |
| `prescript/prescript_settle.py` | SettleMixin | 把完成的指令结算成休息时间 |

### 设置页（`pages/settings_page/`）

`pages/settings_page/`（约 340 行）只剩 `SettingsPage.__init__`、标签页框架 / 重建 / `refresh` 与模块级小工具；各模块的表单和卡片拆到 `pages/settings_page/`，依赖方向同上（页面导入 mixin，mixin 不导入页面）。

| 文件 | Mixin | 内容 |
| --- | --- | --- |
| `pages/settings_page/settings_form.py` | FormMixin | 选项 / 数值行、取值与暂存、保存 / 取消 / 试听 |
| `pages/settings_page/settings_cards.py` | CardsMixin | 首次设置、更新、成就、规则 |
| `pages/settings_page/settings_comm.py` | CommSettingsMixin | 沟通：API / 人设 / 头像 / 思考 / 主动发起 / 日程跟随 |
| `pages/settings_page/settings_record.py` | RecordSettingsMixin | 录像：摄像头权限 / 选择摄像头 / 录像文件列表（`_fmt_size`） |

### 联网守卫（`net_guard/`）

`net_guard/`（约 450 行，其中自检约 330 行）只剩 `NetGuard` 的类属性 / `__init__` 与自检；`NetGuard` 由 6 个 mixin 拼成。依赖方向：`net_guard/` 导入它们，它们从不导入 `net_guard/`；`net_guard/` 用显式 import 重新导出旧名字（`from net_guard import NetGuard` 照旧）。

| 文件 | Mixin | 内容 |
| --- | --- | --- |
| `net_guard/net_quota.py` | QuotaMixin | 额度上限 / 倍率 / 冷静期、各设备合计与剩余 |
| `net_guard/net_store.py` | StoreMixin | `net_state.json` / `net_usage.json` 的读写、会话记录与日志 |
| `net_guard/net_backend.py` | BackendMixin | 禁用网卡 (Adapter) / 过滤代理 (Proxy) 的切换与系统代理 |
| `net_guard/net_session.py` | SessionMixin | 申请 / 快速访问 / 借贷 / 提前结束 / 取消、额度扣除与奖励 |
| `net_guard/net_live.py` | LiveMixin | 进行中的联网状态 `live` / `adopt`（随设备同步） |
| `net_guard/net_tick.py` | TickMixin | 防绕过：系统代理改回、浏览器围栏、直连程序封锁、每秒 tick |

### 数据层（`data_manager/`）

`data_manager/`（约 110 行）只剩 `DataManager.__init__`；`DataManager` 由 7 个 mixin 拼成，公共常量与纯工具（`month_prefixes` / `split_items` / `combine_window` / `tally_items` / `half_fill`）在 `data_common.py`。依赖方向同上；`data_manager/` 用显式 import 重新导出旧名字（`from data_manager import month_prefixes` / `USERNAME_MAX` 照旧）。

| 文件 | Mixin | 内容 |
| --- | --- | --- |
| `data_manager/data_auto.py` | AutoMixin | 小时 / 半小时格子的自动填充（分心 / 睡眠 / 行为模式）、睡眠的填写与取消 |
| `data_manager/data_study.py` | StudyMixin | 旧数据迁移、`study_log.json`、当天 / 昨天 / 本月合计 |
| `data_manager/data_focus.py` | FocusMixin | 专注 / 休息的起止时刻（`focus_log.json`）、按天的时间分布 |
| `data_manager/data_overlap.py` | OverlapMixin | 断网期间与其他设备重叠的记录只算一次（MECHANICS 15.5） |
| `data_manager/data_window.py` | WindowMixin | 窗口使用时间（`window_usage.json`） |
| `data_manager/data_hours.py` | HoursMixin | 整点记录、细化规范的半小时格、待填写格子 |
| `data_manager/data_misc.py` | MiscMixin | 用户名、日常标记、违规记录、重置时删数据 |

### 日程引擎（`routine/`）

`routine/`（约 270 行，其中自检约 185 行）只剩文档、再导出与自检；`Routine` / `Store` 和推算函数拆到 `routine/`，依赖方向 `routine_common.py` ← `routine_parse.py` ← `routine_plan.py` ← `routine_query.py` ← `routine_store.py`。`routine/` 用显式 import 重新导出所有旧名字（`routine.Routine` / `routine.Store` / `routine.plan_between` 照旧）。`seg_class` 用的「普通空闲 / 休息」判断在 `routine_common.plain_label`，`Store.plain` 也走它。

| 文件 | 内容 |
| --- | --- |
| `routine/routine_common.py` | 常量 / 正则、sha256 确定性随机（`u01` / `rint`）、列表解析、`_iso` / `_dt` / `plain_label` |
| `routine/routine_parse.py` | `Routine` 与日程文件解析 `parse` |
| `routine/routine_plan.py` | 起床 / 一天安排 / 循环与波动 / 联合行动平移 / 当前状态（`day_plan` / `plan_between` / `status_in` / `seg_class`…） |
| `routine/routine_store.py` | `Store`：时间线 / 整点记录 / 联合行动 / 按天统计的存档 |
| `routine/routine_query.py` | `contract_for_day` / `banned_ids` / `contracts_before` |

高频定位：断网判定在 `netproxy/rules.py` 的 `decide()`；数据读写看 `data_manager/` + MECHANICS 第 10 节；设置项在 `settings_manager.py` + `pages/settings_page/`；页面 UI 在 `pages/`，`app/` 只留状态与调度。

## 读文件规则（控制上下文）

- **禁止整文件读取大文件**：`app/app_timer.py`(45KB)、`app/app_contract_media.py`、`comm/`、`devsync/`、`i18n/i18n_en.py`、`comm/comm_selftest.py` 等（纯规则已拆到 `contract/`，`Contract` 的 7 个 mixin 也在那里，`contract/` 只剩约 770 行装配与自检；`devsync/` 的密码学 / 工具 / 7 个 mixin 已拆到 `devsync/`，只剩约 1020 行装配与自检；录像的存档 / MF / 合并已拆到 `recorder/`；沟通的常量 / 8 个 mixin / 自检已拆到 `comm/`，`comm/` 只剩约 1400 行装配；AI 接口的配置 / 请求 / 搜索已拆到 `ai_assist/`，`ai_assist/` 只剩约 800 行建议解析与自检；`prescript/` 的常量与 7 个 mixin 已拆到 `prescript/`，只剩约 620 行装配与自检；设置页的表单 / 卡片 / 沟通 / 录像已拆到 `pages/settings_page/`；`net_guard/` 的额度 / 会话 / 后端 / 状态 / 防绕过已拆到 `net_guard/`，只剩约 450 行装配与自检；`data_manager/` 的 7 个 mixin 已拆到 `data_manager/`，只剩约 110 行装配；`routine/` 的解析 / 推算 / 存档 / 查询已拆到 `routine/`，只剩约 270 行再导出与自检）。先用 Grep 搜关键词定位行号，再用 Read 的 `offset/limit` 只读相关片段。
- **查机制先看 `MECHANICS.md` 的目录**，再只读 `docs/mechanics/` 里对应的那一节（多数 1–30KB；18 合约 56KB、24 沟通 119KB 仍然只读相关段落）。不要通读。
- 宽泛的跨文件搜索交给 Explore 子代理，只带回结论。

## 硬性约定（见 for_developer.md）

1. 改机制前查 `docs/mechanics/` 里对应的那一节（目录在 `MECHANICS.md`）保兼容；改完同步 `i18n/i18n_en.py`（英文译文）、那一节、`README.md`（README 只记模块级要点，细节进 MECHANICS）。
2. 注意**跨天问题**（额度、整点记录、睡觉时间等跨零点）。
3. 设置项按模块分页（顺序同左侧导航，基本设置永远第一页）；新功能：选项在上、数值在下。
4. UI 文案走 `i18n/` 的 `tr()`，不加小字解释。
5. 双端功能实时适配；设备同步每 20s 由母端检测，以"焦点设备"操作为最新。
6. 开发者模式只在 git 开发版存在。

## 架构

- **`config.py` 是数值中枢**：默认常量都在此，`settings_manager.py` 把用户设置 apply 回 config 的 globals（代码里常见 `config.X` / `globals().get(...)`）。别处不要复制数值。
- **`app/` 只剩装配**：`__init__` 里读状态、建页面、挂定时器；页面、计时器状态机（`IDLE / RUNNING / READY_BREAK / ON_BREAK`）、tick、关闭/重置都在 `app/` 里的 mixin 里（明细见「实现地图」）。页面 UI 在 `pages/`。再拆新子系统就照这个模式：一个 `app/xxx.py` + 一个 `XxxMixin`，状态仍全放 `self`，无状态工具放 `app/app_base.py`。
- **断网两条路**（`NET_MODE`）：Adapter 直接禁网卡；Proxy 走 `netproxy/` 过滤代理（`bypassguard.py` 补拦直连）。优先级链在 `rules.py` 的 `decide()`：localhost → 合约网络禁令 → 联网会话 → 提前启动豁免 → 全屏蔽 → 白/黑名单。
- **提前启动豁免**：`app/app_daily.py` 每秒把 `prestart.exe_names()`（含子进程）刷到 `rules.exempt`，绕过名单和全屏蔽，但绕不过合约网络禁令。
- **合约纯规则**在 `contract/contract_rules.py`（词条表、组合、公式，无存档状态）；`contract/` 用显式 import 重新导出，`C.TERMS` 之类的写法不变。**同步密码学**在 `devsync/devsync_crypto.py`，`devsync/` 同样重新导出 `seal` / `unseal` / `SealError`。**录像**的存档 / Media Foundation / 合并在 `recorder/`，`recorder/` 只留 `Recorder` 线程并重新导出旧名字。
- **合约 override 设置**：`config.CONTRACT_OVERRIDES` 由 settings_manager 套在生效值之上。
- **临时文件**：全部在 `%TEMP%\OIT\`（`tempstore.py`）。启动时 `sweep()` 清掉一天前没动过的；直接 `python xxx.py` 跑自检 / 工具脚本时 `config.py` 会 `autosession()` 自动开会话（退出即删，开会话前清掉 1 小时前的 `misc` 残留），检查脚本也可自己调 `session("名字")`。`python tempstore.py --status` / `--clean` 看占用 / 手动清空（`--clean` 连 `%TEMP%` 顶层老自检留下的 `tmp*` 和旧版 recorder 的 `rec<数字>` 工作目录一起清，按文件指纹，不碰别人的）。不导入 `config` 的模块自检要在 `__main__` 里自己调 `tempstore.autosession()`；会话里用过的 `OIT\rec\<tag>` 退出时一并删。要临时目录就用 `tempstore.path(...)` / `tempstore.mkdtemp(...)`，别再自己拼 `tempfile.gettempdir()`。
- 数据在 `%USERPROFILE%\AppData\LocalLow\Obliphur's Integrated Terminal\`，发布版经 `securefile.py` 加密；`continuity.py` 处理数据被搬走/回滚。改数据读写前读 `docs/mechanics/10-data-files.md`、`15-sync.md`。
- 手机版对应模块在 `Android version/app/src/main/java/com/obliphur/oit/core/`，与桌面版同名一一对应，改一边要查另一边。

## 风格

注释、docstring 用中文；文案走 `tr()`，不硬编码字符串。
