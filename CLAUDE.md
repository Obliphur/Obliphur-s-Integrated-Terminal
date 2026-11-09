# CLAUDE.md

OIT (Obliphur's Integrated Terminal)：Windows 10/11 + Python 3.10+ 专注计时/断网程序（tkinter，仅标准库，可选 pywin32），外加 `Android version/` 的 Kotlin/Compose 手机版。入口 `python main.py`，需管理员权限。

## 命令

```bash
python main.py                  # 运行（自动提权）
python <module>.py              # 模块自检，如 net_guard.py / settings_manager.py / prestart.py / netproxy/selftest.py
python setup.py --selftest      # 改 setup.py / registration.py 后跑
python release.py               # 发布（PyInstaller）
```

- 无统一测试套件：各文件 `if __name__ == "__main__"` 的断言即自检。
- 别在本机试跑 `setup.exe`（会装 Program Files 并写注册表）。
- `config_local.py` 只在 git 开发版生效，用于本地覆盖 config 数值。
- 每次开发完成递增 `config.py` 的 `APP_VERSION`。
- 别提交调试/预览截图，截图放系统临时目录。

## 硬性约定（见 for_developer.md）

1. 改机制前查 `MECHANICS.md` 保兼容；改完同步 `i18n.py`、`MECHANICS.md`、`README.md`（README 只记模块级要点，细节进 MECHANICS）。
2. 注意**跨天问题**（额度、整点记录、睡觉时间等跨零点）。
3. 设置项按模块分页（顺序同左侧导航，基本设置永远第一页）；新功能：选项在上、数值在下。
4. UI 文案走 `i18n.py` 的 `tr()`，不加小字解释。
5. 双端功能实时适配；设备同步每 20s 由母端检测，以"焦点设备"操作为最新。
6. 开发者模式只在 git 开发版存在。

## 架构

- **`config.py` 是数值中枢**：默认常量都在此，`settings_manager.py` 把用户设置 apply 回 config 的 globals（代码里常见 `config.X` / `globals().get(...)`）。别处不要复制数值。
- **`app.py` 是主窗口**：页面、计时器状态机（`IDLE / RUNNING / READY_BREAK / ON_BREAK`）、每秒/每 5s tick、关闭/重置都在此。页面 UI 在 `pages/`，状态与调度在 app.py。
- **断网两条路**（`NET_MODE`）：Adapter 直接禁网卡；Proxy 走 `netproxy/` 过滤代理（`bypassguard.py` 补拦直连）。优先级链在 `rules.py` 的 `decide()`：localhost → 合约网络禁令 → 联网会话 → 提前启动豁免 → 全屏蔽 → 白/黑名单。
- **提前启动豁免**：`app.py` 每秒把 `prestart.exe_names()`（含子进程）刷到 `rules.exempt`，绕过名单和全屏蔽，但绕不过合约网络禁令。
- **合约 override 设置**：`config.CONTRACT_OVERRIDES` 由 settings_manager 套在生效值之上。
- 数据在 `%USERPROFILE%\AppData\LocalLow\Obliphur's Integrated Terminal\`，发布版经 `securefile.py` 加密；`continuity.py` 处理数据被搬走/回滚。改数据读写前读 MECHANICS 第 9、15 节。
- 手机版对应模块在 `Android version/app/src/main/java/com/obliphur/oit/core/`，与桌面版同名一一对应，改一边要查另一边。

## 风格

注释、docstring 用中文；文案走 `tr()`，不硬编码字符串。
