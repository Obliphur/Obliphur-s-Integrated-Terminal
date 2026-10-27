# Obliphur's Integrated Terminal (OIT)

> (并非集成终端...)

你是否曾认为在网上度过的时间显得太过空虚 却又在事后无法控制地继续随着互联网的浪潮漂流?  
你是否曾发现自己时不时就会因为各种网站分心 因为低效而感到自责与痛苦?  
你是否曾想要改变自己的生活方式 专注非线上活动提升自我?  

这个项目正是为上面的问题而生.你可以根据你的生活习惯 在第一次使用时配置相关设置.
该项目尽可能照顾到新用户对这种模式的不适应 添加了一些功能.

你愿意把你剩余的人生交给我吗?


对了,赞美claudia...

***

## ⚠️ 警告

- 本项目**包含 AI 生成的代码**,本文亦由 AI 整理.
- 由于iOS的特殊性与封闭性 永远不会适配iOS

---

## 功能

| 页面 | 作用 |
| --- | --- |
| **Timer** | 专注计时,结束后给出建议休息时长|
| **Network** | 申请制联网:冷静期后自动联网,到点断网;另有快速访问与紧急借贷; "📁 文件通道" 在绑定的手机和电脑之间传文件 / 图片 |
| **Rules** | Proxy 模式下的网站 / 应用黑白名单 (被禁用的程序窗口一到前台就关掉),可让 AI 给出建议 |
| **Hour Log** | 每个整点提示记录上一小时做了什么 |
| **Calendar** | 按日查看学习时长 |
| **Statistics** | 活动, 联网理由, 各程序前台时间的分布, 最近顺利完成的合约与最近的违规记录 |
| **Contract** | 合约: 选词条加难度的限时挑战|
| **Settings** | 按模块分页的可调参数 |
| **Reset** | 恢复网络并清理数据 |

断网有两种方式:**Adapter** 直接禁用网卡;**Proxy** 通过系统代理过滤,不读取系统代理的程序不受其限制.

---

## 运行

- Windows 10 / 11,Python 3.10+,仅用标准库
- 启动时会申请管理员权限(断网需要)

```bash
python main.py
```

## 更新

每次打开程序时从 GitHub 的最新发布检查更新, 可以在 设置 → 基本设置 里关闭自动检查 / 自动更新. 规则见 `MECHANICS.md` 第 16 节.

## Android 版

`Android version/` 是 Android 版 (Kotlin + Jetpack Compose, Android 10 及以上), 用本地 VPN 断网. 构建方法见其中的 `README.md`, 和桌面版的差别见 `MECHANICS.md` 第 14 节.

## 开发

各模块可单独自检,如 `python net_guard.py`, `python settings_manager.py`...

### 项目结构

```text
obliphur_terminal/
├─ main.py               入口: 申请管理员权限, 启动界面
├─ app.py                主窗口: 各页面, 计时器, 联网页, 定时检查, 关闭 / 重置
├─ config.py             默认数值, 主题与字号, 版本号 (APP_VERSION)
├─ themes/               界面主题 (classic / claude / urban_will; 都市之意的字体和预览都在里面)
├─ settings_manager.py   设置项定义, 48 小时冷静期, 配置期
├─ data_manager.py       学习时长, 整点记录, 专注时间段, 窗口使用时间 (统计)
├─ daily.py              睡觉时间 (设置 → 日常)
├─ behavior.py           行为模式 / 外出 (可选, 统计与整点记录叠加)
├─ prescript.py          指令: 都市之意主题独占的抽取 / 计时 / 存档
├─ achievements.py       成就: 条件判定与存档
├─ contract.py           合约: 词条, 违反与降级, 休息倍率, 整点记录锁定
├─ net_guard.py          联网额度, 申请 / 快速访问 / 借贷, 断网方式切换
├─ netproxy/             本地过滤代理 (Proxy 断网方式)
│  ├─ server.py          HTTP / HTTPS 隧道代理, 403 拦截页
│  ├─ rules.py           黑白名单与判定, 名单改动的冷静期
│  ├─ controller.py      把代理, 名单, 系统代理拼在一起
│  ├─ sysproxy.py        Windows 系统代理的安装 / 还原
│  ├─ procinfo.py        连接 → 发起它的程序
│  └─ selftest.py        端到端自检
├─ window_guard.py       任务栏窗口数上限 (专注中提醒), 按前台窗口关程序, 前台程序识别, 窗口藏 / 显示
├─ prestart.py           提前启动 (仅 PC): 专注中先跑起程序, 藏窗口 + 静音, 结束专注放出来
├─ audiomute.py          按进程静音 / 取消静音 (音频会话, 提前启动用)
├─ recorder.py          合约的录像 (摄像头 / 屏幕, Windows 自带的 H.264 编码器)
├─ devsync.py            设备同步: 发现, 绑定, 加密, 同步, 安装包 / 文件直接传输
├─ sync_local.py         设备同步 ↔ 本机数据
├─ firewall.py           Windows 防火墙: 放行同步端口
├─ updater.py            从 GitHub 检查 / 安装更新
├─ ai_assist.py          AI 名单建议: 服务商, 提示词, 解析
├─ context_collect.py    AI 建议用的本机信息 (已安装应用, 浏览记录里的域名)
├─ i18n.py               中英文界面文字
├─ toast.py              提示 / 确认 / 只能手动关闭的提醒窗
├─ shortcut.py           桌面快捷方式, 开始菜单
├─ uninstall.py / .bat   独立的卸载程序
├─ pages/                各页面的界面
│  ├─ settings_page.py   设置 (按模块分页)
│  ├─ sync_panel.py      设备同步 (设置 → 基本设置)
│  ├─ rules_page.py      规则 (名单)
│  ├─ file_channel.py    文件通道: 发文件 / 图片给绑定的设备
│  ├─ ai_dialogs.py      AI 建议的对话框
│  ├─ stats_page.py      统计
│  ├─ calendar_page.py   日历
│  ├─ hour_log_page.py   整点记录
│  ├─ outing_dialog.py   外出的时长窗口
│  ├─ achievements_page.py  成就页 (设置 → 基本设置 → 🏆 成就), 达成提示
│  ├─ contract_page.py   合约页
│  ├─ prescript_page.py  指令页 (都市之意主题独占)
│  ├─ prestart_dialog.py 提前启动的程序清单弹窗
│  └─ widgets.py         共用控件 (滚动区, 开关, 加载页 …)
├─ defaults/             空模板 (名单, AI 配置)
├─ sounds/               违反合约的警报
├─ MECHANICS.md          各机制的具体规则与数值
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