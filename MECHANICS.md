# OIT 机制说明

本文逐项说明程序里各个机制具体怎样运作, 以及它们的默认数值.
默认值取自 `config.py` 与 `settings_manager.py`; 实际生效的数值以 Settings 页显示的为准.

该文档并不是面向普通用户的.

里面有作者亲自写的风味文本!
细化规范了.

---

## 目录

每一节一个文件, 改某个机制只看对应文件; 新增章节时同步这张表.

| 节 | 主题 | 文件 |
| --- | --- | --- |
| 1 | 设置与设置冷静期 | [01-settings.md](docs/mechanics/01-settings.md) |
| 2 | 联网额度与申请 (Network 页) | [02-network-quota.md](docs/mechanics/02-network-quota.md) |
| 3 | 断网方式 | [03-net-modes.md](docs/mechanics/03-net-modes.md) |
| 4 | 网站 / 应用名单 (设置 → 联网 → 🛡 规则, 仅 Proxy 模式; 代理-全屏蔽 (Proxy block-all) 下只有应用名单) | [04-lists.md](docs/mechanics/04-lists.md) |
| 5 | AI 名单建议 (Rules 页) | [05-ai-suggest.md](docs/mechanics/05-ai-suggest.md) |
| 6 | 专注计时器 (Timer 页) | [06-timer.md](docs/mechanics/06-timer.md) |
| 7 | 整点记录 (Hour Log 页) | [07-hour-log.md](docs/mechanics/07-hour-log.md) |
| 8 | 统计与日历 | [08-stats.md](docs/mechanics/08-stats.md) |
| 9 | 重置 (Reset 页) | [09-reset.md](docs/mechanics/09-reset.md) |
| 10 | 数据文件 | [10-data-files.md](docs/mechanics/10-data-files.md) |
| 11 | 界面 | [11-ui.md](docs/mechanics/11-ui.md) |
| 12 | 任务栏窗口数上限 (window_guard.py) | [12-window-guard.md](docs/mechanics/12-window-guard.md) |
| 13 | 窗口使用时间统计 | [13-window-time.md](docs/mechanics/13-window-time.md) |
| 14 | Android 版 (`Android version/`) | [14-android.md](docs/mechanics/14-android.md) |
| 15 | 设备同步 (设置 → 基本设置, 两端都是 Proxy 断网方式时) | [15-sync.md](docs/mechanics/15-sync.md) |
| 16 | 更新 (设置 → 基本设置) | [16-update.md](docs/mechanics/16-update.md) |
| 17 | 成就 (设置 → 基本设置 → 🏆 成就) | [17-achievements.md](docs/mechanics/17-achievements.md) |
| 18 | 合约 (Contract 页) | [18-contract.md](docs/mechanics/18-contract.md) |
| 19 | 睡觉时间 (设置 → 日常) | [19-sleep-time.md](docs/mechanics/19-sleep-time.md) |
| 20 | 提前启动 (仅 PC) | [20-prestart.md](docs/mechanics/20-prestart.md) |
| 21 | 行为模式 (设置 → 日常) | [21-behavior.md](docs/mechanics/21-behavior.md) |
| 22 | 指令 (Prescript, 都市之意主题) | [22-prescript.md](docs/mechanics/22-prescript.md) |
| 23 | 睡眠 (Timer 页) | [23-sleep.md](docs/mechanics/23-sleep.md) |
| 23.1 | 睡眠时的整点记录 | [23-1-sleep-hour-log.md](docs/mechanics/23-1-sleep-hour-log.md) |
| 24 | 沟通 (左侧导航, 在"重置"上面; 设置 → 沟通) | [24-comm.md](docs/mechanics/24-comm.md) |
| 25 | 闹钟 (设置 → 闹钟, 1.0.359) | [25-alarm.md](docs/mechanics/25-alarm.md) |
