<img src="oit.png" width="96" align="right" alt="OIT">

# Obliphur's Integrated Terminal (OIT)

> (并非集成终端...)

[中文](README.md) | [English](README.en.md)

你是否曾认为在网上度过的时间显得太过空虚 却又在事后无法控制地继续随着互联网的浪潮漂流?  
你是否曾发现自己时不时就会因为各种网站分心 因为低效而感到自责与痛苦?  
你是否曾想要改变自己的生活方式 专注非线上活动提升自我?  

这个项目正是为上面的问题而生.你可以根据你的生活习惯 在第一次使用时配置相关设置.
该项目尽可能照顾到新用户对这种模式的不适应 添加了一些功能.

你愿意把你剩余的人生交给我吗?


对了,赞美Claudia...

***

## ⚠️ 警告

- 本项目**包含 AI 生成的代码**,本文亦由 AI 整理.
- 由于iOS的特殊性与封闭性 永远不会适配iOS
- 包含:开机自启,跨设备拉起,阻止VPN修改等强制措施
不过 该项目除了AI相关的任何数据传输行为均基于本地局域网 不上传到任何服务器.

---

## 功能

| 页面 | 作用 |
| --- | --- |
| **Timer** | 专注计时,支持暂停并按可设倍率倒退,退到0自动结束并弹窗;暂停时段在统计/日历中记为空闲,其他模块仍视为专注;结束后给出建议休息时长|
| **Network** | 申请制联网:冷静期后自动联网,到点断网;另有快速访问与紧急借贷; "📁 文件通道" 在绑定的手机和电脑之间传文件 / 图片 |
| **Rules** | Proxy 模式下的网站 / 应用黑白名单 (被禁用的程序窗口一到前台就关掉),可让 AI 给出建议;bilibili.com 条目可再点开,按 UP 主放行 / 拦截 |
| **Chat** | 和人设 AI 聊天; 按人设日程显示对方状态, 对方也会主动发消息; 可开关 MiniMax 语音、按人设选音色配置, 新消息自动生成并缓存, 点击播放或自动播放最近未播消息; 模型与语音配置均有填写帮助; 日程跟随等提示在焦点设备显示, 自定义中转可独立选择模型类型与接口协议, OpenAI 兼容中转支持只填域名; Gemini 回复隔离思考内容, 分条回复由程序分别调用 |
| **Hour Log** | 每个整点提示记录上一小时做了什么 |
| **Calendar** | 按日查看学习时长 |
| **Statistics** | 活动, 联网理由, 各程序前台时间的分布, 最近顺利完成的合约与最近的违规记录 |
| **Contract** | 合约: 选词条加难度的限时挑战|
| **Settings** | 按模块分页的可调参数 |
| **Reset** | 恢复网络并清理数据 |

断网有两种方式:**Adapter** 直接禁用网卡;**Proxy** 通过系统代理过滤,不读取系统代理的程序不受其限制.

---

## 安装

在 GitHub 的 [Releases](https://github.com/Obliphur/Obliphur-s-Integrated-Terminal/releases) 里下载最新的 **`setup.exe`** 运行:
它会检查并安装依赖库 (电脑上没有 Python 3.10+ 时从 python.org 下载官方版本), 把 OIT 装到 `C:\Program Files\OIT` (可改),
并注册成系统里的软件 (设置 → 应用 → 已安装的应用), 可选桌面 / 开始菜单快捷方式. 静默安装: `setup.exe /S`. 细节见 `MECHANICS.md` 16.4.
手机版下载同一个发布里的 `OIT.apk`.

## 运行

- Windows 10 / 11,Python 3.10+,仅用标准库 (可选 pywin32)
- 启动时会申请管理员权限(断网需要)
- 不用 setup.exe 时, 下载源码后直接运行:

```bash
python main.py
```

## 更新

每次打开程序时从 GitHub 的最新发布检查更新, 可以在 设置 → 基本设置 里关闭自动检查 / 自动更新. 规则见 `MECHANICS.md` 第 16 节.

## Android 版

`Android version/` 是 Android 版 (Kotlin + Jetpack Compose, Android 10 及以上), 用本地 VPN 断网. 构建方法见其中的 `README.md`, 和桌面版的差别见 `MECHANICS.md` 第 14 节.

## 开发

各模块可单独自检,如 `python -m net_guard`, `python settings_manager.py`...

### 项目结构

```text
obliphur_terminal/
├─ main.py               入口: 申请管理员权限, 启动界面
├─ app/                   主窗口: 各页面, 计时器, 联网页, 定时检查, 关闭 / 重置
│  ├─ app_base.py           共用的小工具 (提示音文件, 放音频, 疲劳文案)
│  ├─ app_comm.py           沟通 / 分享 / 联合行动 (BreakTimerApp 的 CommMixin)
│  ├─ app_sync.py           设备同步 / 更新 (BreakTimerApp 的 SyncMixin)
│  ├─ app_contract.py       合约的检查 / 开始 / 结束 (ContractMixin)
│  ├─ app_contract_media.py 合约的录像 / 合并发送 / 违规 / 禁网 (ContractMediaMixin)
│  ├─ app_timer.py          计时器状态机 / 专注存档恢复 / 结算 / 每秒 tick (TimerMixin)
│  ├─ app_break.py          休息与结转 / 奖励休息 / 休息后提醒 (BreakMixin)
│  ├─ app_daily.py          睡觉 / 外出与行为模式 / 提前启动 (DailyMixin)
│  ├─ app_net.py            联网页: 额度 / 申请 / 借贷 / 断网检查 (NetMixin)
│  ├─ app_achievements.py   成就的判定 / 同步 / 弹窗 (AchievementsMixin)
│  ├─ app_prescript.py      指令 (Prescript): 抽取 / 计时 / 刻痕 (PrescriptMixin)
│  ├─ app_update.py         开发者模式跟着母端 / 更新中继 (UpdateMixin)
│  ├─ app_settings.py       设置生效 / 冷静期 / 关闭程序 / 日志与徽章 (SettingsMixin)
│  ├─ app_window.py         窗口 / 主题 / 布局 / 重建界面 / 页面切换 (WindowMixin)
│  ├─ app_pages.py          日程跟随 / 闹钟 / 规则弹窗 / 侧边栏 / 计时页 UI (PagesMixin)
│  ├─ app_guard.py          窗口事件与心跳 / 窗口守护 / 自启 / 网卡与应用规则检查 (GuardMixin)
│  ├─ app_misc.py           配置期 / 快捷方式 / 重置页 (MiscMixin)
├─ config.py             默认数值, 主题与字号, 版本号 (APP_VERSION)
├─ tempstore.py          临时文件: 全部放进 %TEMP%\OIT 一个文件夹 (启动时清掉一天前没动过的)
├─ themes/               界面主题 (classic / claude / urban_will; 都市之意的字体和预览都在里面)
├─ settings_manager.py   设置项定义, 48 小时冷静期, 配置期
├─ data_manager/         学习时长, 整点记录, 专注时间段, 窗口使用时间 (统计; mixin 在 data_manager/)
├─ daily.py              睡觉时间 (设置 → 日常)
├─ behavior.py           行为模式 / 外出 (可选, 统计与整点记录叠加)
├─ prescript/            指令: 都市之意主题独占的抽取 / 计时 / 存档 (mixin 在 prescript/)
├─ comm/                 沟通: 人设, 聊天记录, 主动消息, 对方的状态 (mixin 在 comm/)
│  ├─ comm_data.py          沟通: 📎 发送的数据 / 对方查询的参考模板
├─ alarm.py              闹钟: 本机闹钟, 铃声, 共时闹钟 (跟随对方的起床时间) 的响铃判断
├─ achievements.py       成就: 条件判定与存档
├─ contract/             合约: 词条, 违反与降级, 休息倍率, 整点记录锁定 (mixin 在 contract/)
├─ net_guard/            联网额度, 申请 / 快速访问 / 借贷, 断网方式切换 (mixin 在 net_guard/)
├─ netproxy/             本地过滤代理 (Proxy 断网方式)
│  ├─ server.py          HTTP / HTTPS 隧道代理, 403 拦截页
│  ├─ rules.py           黑白名单与判定, 名单改动的冷静期
│  ├─ controller.py      把代理, 名单, 系统代理拼在一起
│  ├─ sysproxy.py        Windows 系统代理的安装 / 还原
│  ├─ procinfo.py        连接 → 发起它的程序
│  ├─ upgate.py          按 UP 主放行 / 拦截: 页面 → UP 主 (哔哩哔哩公开接口)
│  ├─ browserurl.py      读浏览器地址栏 (UI Automation)
│  └─ selftest.py        端到端自检
├─ window_guard.py       任务栏窗口数上限 (专注中提醒), 按前台窗口关程序, 前台程序识别, 窗口藏 / 显示
├─ prestart.py           提前启动 (仅 PC): 藏窗口 / 静音, 休息中直接开始专注时接着上一段
├─ audiomute.py          按进程静音 / 取消静音 (音频会话, 提前启动用)
├─ recorder/            合约的录像 (摄像头 / 屏幕, Windows 自带的 H.264 编码器; 存档 / MF / 合并在 recorder/)
├─ devsync/              设备同步: 发现, 绑定, 加密, 同步, 安装包 / 文件直接传输 (mixin / 工具在 devsync/)
├─ sync_local.py         设备同步 ↔ 本机数据
├─ firewall.py           Windows 防火墙: 放行同步端口
├─ updater.py            从 GitHub 检查 / 安装更新
├─ ai_assist/            AI 名单建议: 服务商, 提示词, 解析 (配置 / 请求 / 搜索在 ai_assist/)
├─ context_collect.py    AI 建议用的本机信息 (已安装应用, 浏览记录里的域名)
├─ i18n/                 中英文界面文字
├─ toast.py              提示 / 确认 / 只能手动关闭的提醒窗
├─ shortcut.py           桌面快捷方式, 开始菜单
├─ uninstall.py / .bat   独立的卸载程序
├─ pages/                各页面的界面
│  ├─ settings_page/     设置 (按模块分页; 表单 / 卡片 / 沟通 / 录像在 settings_page/)
│  ├─ sync_panel.py      设备同步 (设置 → 基本设置)
│  ├─ rules_page.py      规则 (名单)
│  ├─ up_dialog.py       网站条目下的 UP 主名单
│  ├─ file_channel.py    文件通道: 发文件 / 图片给绑定的设备
│  ├─ ai_dialogs.py      AI 建议的对话框
│  ├─ stats_page.py      统计
│  ├─ calendar_page.py   日历
│  ├─ comm_page.py       沟通 (人设聊天)
│  ├─ hour_log_page.py   整点记录
│  ├─ outing_dialog.py   外出的时长窗口
│  ├─ achievements_page.py  成就页 (设置 → 基本设置 → 🏆 成就), 达成提示
│  ├─ contract_page.py   合约页
│  ├─ prescript_page.py  指令页 (都市之意主题独占)
│  ├─ prestart_dialog.py 提前启动的程序清单弹窗
│  └─ widgets.py         共用控件 (滚动区, 开关, 加载页 …)
├─ defaults/             空模板 (名单, AI 配置)
├─ sound_effects.py     事件音效与独立音效设置页（内置 / 自选 / 静音 / 试听）
├─ sounds/               合约警报与内置音效
├─ MECHANICS.md          机制说明的目录 (每节一个文件, 在 docs/mechanics/)
├─ docs/mechanics/       各机制的具体规则与数值 (一节一个文件)
├─ for_developer.md      开发约定
└─ Android version/      Android 版
   └─ app/src/main/java/com/obliphur/oit/
      ├─ OitApp.kt       应用入口, 平台相关的接口实现, 更新 / 发更新给其他设备
      ├─ core/           和平台无关的逻辑 (对应桌面版的同名模块)
      │  ├─ Core.kt      各部分拼在一起: 定时, VPN 计划, 锁定应用
      │  ├─ Settings.kt  设置项与冷静期
      │  ├─ NetGuard.kt  联网额度与申请
      │  ├─ Timer.kt     专注计时器
      │  ├─ Daily.kt     睡觉时间
      │  ├─ Behavior.kt  行为模式 / 外出
      │  ├─ Rules.kt     黑白名单
      │  ├─ DataStore.kt 统计数据
      │  ├─ Achievements.kt 成就
      │  ├─ Contract.kt  合约
      │  ├─ Config.kt    数值, 工具函数
      │  ├─ Store.kt     读写文件
      │  └─ I18n.kt / EnStrings.kt  英文界面
      ├─ service/        前台服务 (Platform.kt), VPN (OitVpnService.kt, Packets.kt),
      │                  锁定应用 (AppLock.kt), 更新 (Updater.kt)
      ├─ sync/           设备同步 (SyncService.kt, SyncCrypto.kt, AndroidLocal.kt)
      ├─ ai/Ai.kt        AI 名单建议
      └─ ui/             各页面 (Jetpack Compose)
```

对了,Claudia的默认头像取自https://www.pixiv.net/artworks/150995828.
