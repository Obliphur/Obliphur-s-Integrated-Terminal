# 10. 数据文件

[← 返回目录](../../MECHANICS.md)

数据目录: `%USERPROFILE%\AppData\LocalLow\Obliphur's Integrated Terminal\`

| 文件 | 内容 | 保留范围 |
| --- | --- | --- |
| `app_settings.json` | 设置的生效值, 排队中的修改, 配置期截止时间, 借贷后的一次免等待 | 永久 |
| `net_state.json` | 今天的已消耗额度, 借贷额度, 明天要还的部分, 欠款 | 当天; 换日时只带走要还的部分 |
| `net_usage.json` | 每次联网会话的理由与实际联网秒数 | 本月 + 上月 |
| `net_rules.json` | 四张名单, 一张 UP 主白名单 (4.8), 排队中的改动 | 永久 |
| `up_names.json` | UP 主的昵称 (名单里显示用, 后台查到的) | 永久; 不同步 |
| `study_log.json` | 每天的学习时长 | 本月 + 上月 |
| `hour_log.json` | 整点记录内容 | 本月 + 上月 |
| `focus_log.json` | 每一段专注 / 休息的起止时刻, 以及共享专注中 OIT 被关掉的空闲段 (8.4); 暂停专注用 `k=idle, p=true, f=专注开始时刻`, 只在统计/日历中扣除 (6.9) | 最近 7 天 |
| `window_usage.json` | 每天每个程序在前台的秒数 (`""` 是空白, `"?"` 是未知程序), 以及按分钟的时间线 (统计页合并多台设备用) | 本月 + 上月 |
| `timer_state.json` | 积攒的休息时间, 本周连点领取了多少 (时长和次数), 进行中的专注 | 永久 |
| `profile.json` | 用户名 (1.1) | 永久; 不同步 |
| `ai_config.json` | AI 服务商配置 (API Key 用 DPAPI 加密保存); 设置随绑定同步 (Key 到对方设备后用对方的 DPAPI 重新加密) | 永久 |
| `sysproxy_backup.json` | 启用 Proxy 前的系统代理设置 | 还原后删除 |
| `chime.wav` | 内置提示音 (自动生成) | 永久 |
| `comm_ping.wav` / `break_soon.wav` | 沟通提示音 / 休息剩 1 分钟的提示音 (自动生成) | 永久 |
| `net_requests.log` | 联网申请 / 开始 / 结束 / 借贷的流水 | 只追加, 不清理 |
| `settings_changes.log` | 设置生效的流水 | 只追加, 不清理 |
| `window_guard.log` | 任务栏窗口数上限的提醒 (OVER), 应用名单 (4.7) 关掉的窗口, 窗口功能检查出错的原因 | 只追加, 不清理 |
| `sync_state.json` | 设备同步: 本机的设备号, 绑定的设备和密钥, 其他设备的账和状态 (第 15 节) | 永久; 解除绑定时删掉那台设备的 |
| `oit.key` / `secure.idx` | 数据加密的密钥 (DPAPI 封着) 和每个文件的写入计数 (9.4) | 永久; 重置 / 卸载时删掉 |
| `continuity.json` | 数据的连续性: 世代, 序号, DPAPI 封印 (9.3); `.lock` 是运行期间独占打开的占位文件 | 永久; 重置 / 卸载时删掉 |
| `clock_guard.json` | 系统时间被改的检测: 基准, 倒计时, 上次警告的时间 (9.6) | 永久; 重置 / 卸载时删掉 |
| `run.flag` / `*.bak` | 运行标记 (正常退出时删, 还在 = 上一次没有正常结束) 和重要文件上一次的完好副本 (9.5) | 永久; `run.flag` 清数据时留着 |
| `source_manifest.json` | 程序文件 (.py) 的 SHA-256 清单, 用来发现源码被非更新地改动 (9.7) | 永久; 更新 / 安装后重记; 重置 / 卸载时删掉 |
| `achievements.json` | 成就: 第一次启动的时刻, 达成了哪些 (什么时候), 还没关掉的达成提示 (第 17 节) | 永久 |
| `contract.json` | 合约: 进行中的合约, 结束了的合约 (最近 30 个), 还没开始的词条选择 (第 18 节) | 永久 |
| `daily.json` | 日常: 睡觉时间的两个提示在哪天 / 哪个睡觉窗口弹过 (第 19 节) | 永久; 不同步 |
| `behavior.json` | 行为模式: 本机从什么时候开始用当前模式 (不同步), 每次外出的起止 / 时长 / 是否已发休息时间 (第 21 节) | 最近 62 天; 外出记录同步 |
| `prescript.json` | 指令: 进行中的几条, 今天抽到的 n, 还没结算 / 已经给出的蓝色代码, 归档, 输入框保存的记录, 关闭 OIT 的时刻 (第 22 节) | 最近 100 条归档; 领到的指令 / 奖励 / 结果随设备同步 (15.2), 其他各设备自己一份 |
| `violations.json` | 违规记录: 最近 15 条合约违规的提示文字和时间 (8.1) | 最近 15 条; 不同步 |
| `recordings/camera/`, `recordings/screen/`, `recordings/merged/` | 合约的录像 (目光 / 刻痕, 18.8), 加密的 `.oitv`; 文件名是这一段开始的时刻加合约 id | 超过 3 天自动清除 (没写完的 1 天, 用完的片段更早), 也可以在 设置 → 录像 里删; 不同步 |

仓库里的 `defaults/` 只放空模板 (空名单, 不含 API Key 的 AI 配置), 本机还没有对应文件时才会用到.

---

## 10.6 临时文件

OIT 自己的临时文件都在 `%TEMP%\OIT\` 一个文件夹里 (`tempstore.py`), 不再散落在系统临时目录里.

| 目录 | 内容 | 什么时候清 |
| --- | --- | --- |
| `rec\` | 正在录的 mp4 (按数据目录哈希分成 `camera` / `screen`), 回放时解密出来的 mp4 (`play`) | 加密 / 播放完就删; 没删掉的启动时清 |
| `scan\` | 扫描网站时无界面浏览器用的临时用户目录 (4.9) | 扫完就删; 浏览器占着删不掉的, 1 小时后再清 |
| `update\` | 更新包 (`.zip`) 和解包目录 (16) | 装完就删 |
| `setup\` | 安装程序的日志 `oit-setup.log` 和下载的 Python 安装包 | 安装完就删 (日志留到启动时清) |
| `misc\` | 其它: 自检 / 检查脚本, 头像临时图, 历史库副本, 主题预览 …… | 脚本退出时删自己那份; 开会话时清掉 1 小时前的残留; 启动时清超过 1 天的 |

- `config.py` 导入时把本进程的默认临时目录指到 `misc\`, 所以 `tempfile.mkdtemp()` 和第三方库写的临时文件也落在 OIT 里.
- 每次启动 (`main.py`) 调 `tempstore.sweep()`: 清掉 `%TEMP%\OIT` 里一天前没动过的, 所以它不会越攒越多.
- 旧版本直接把临时文件扔在 `%TEMP%` 顶层 (`oit_rec` / `oit_play` / `oit-scan-*` / `oit-*.zip` / 自检留下的一堆 `oit?????` 目录), 升级后第一次启动时由 `tempstore.sweep_legacy()` 清掉 (只认 `%TEMP%` 顶层 `oit` 开头的名字, 一天内动过的先不动).
- 自检 / 工具脚本 (`python xxx.py`): `config.py` 导入时调 `tempstore.autosession()`, 自动开一个会话目录, 退出时整个删掉
  (`main.py` / `setup.py` / `release.py` / `uninstall.py` 不开: 安装日志和更新包退出后还要能看; 打包后的 exe 也不会开).
  检查脚本也可以开头自己调 `tempstore.session("名字")`. 开会话前会把 `misc` 里 1 小时前没动过的清掉 (上次被强杀 / 断电留下的).
- 重置 (`data_manager.erase_all_data`) 调 `tempstore.sweep(0)`, 卸载 (`uninstall.delete_data`) 调 `tempstore.clean()`.
- 老版本 / 没有走 `config` 的模块自检会把 `tmp??????` 留在 `%TEMP%` 顶层 (这些模块不导入 config, `tempfile` 还没被重定向):
  这些入口的 `__main__` 现在先调 `tempstore.autosession()`; 以前留下的由 `tempstore.sweep_orphans()` 清
  (程序启动时, 以及 `python tempstore.py --clean`), 只认目录里全是 OIT 自检会留下的文件、并且有一眼能认出的名字
  (或 JSON 结构 / 内容对得上) 的; 旧版 recorder 留在顶层的 `rec<数字>` / `rec-data<数字>` 工作目录
  (里面只有 `recordings/` 和 mp4 / `.oitv`) 也一起清; 现在的录像工作文件都在 `OIT\rec` 里.
  别的程序的临时目录、没有权限读的目录 (别的账户建的) 不碰.
- 自检 / 工具脚本开会话时 (`autosession` / `session`), 用过的录像工作目录 `OIT\rec\<tag>` 退出时一起删
  (`tempstore.path` 自动记下来), 空的 `rec` / `OIT` 目录也一并去掉; 所以反复跑 `python -m recorder` 不会留下空目录.
- 想看占了多大 / 手动清空: `python tempstore.py --status` (含顶层 `tmp*` 残留的个数和大小) / `python tempstore.py --clean`.
- 新代码要临时目录就用 `tempstore.path(...)` / `tempstore.mkdtemp(...)`, 不要自己拼 `tempfile.gettempdir()`.

