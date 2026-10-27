# 都市之意 (The Will of the City)

试作主题：全黑背景、不画卡片边框、不用图标；正文字体是随主题附带的
GNU Unifont 像素字体；输入框 / 下拉框获得焦点时套一圈镂空的暖金色灯圈。

主题里还独占一个「指令」（Prescript）页，放在左侧导航第一位。规则见
`MECHANICS.md` 第 22 节，判定 / 存档在 `prescript.py`，界面在
`pages/prescript_page.py`。

## 目录

- `__init__.py` — 主题数据：颜色、字体元数据、`focus_halo` 灯圈颜色。
- `font/` — Unifont 字体和许可证。
- `preview.py` — 开发用预览：隔离用户目录启动一个只画界面的 OIT，把各页截图到 `screenshots/`。
- `screenshots/` — 预览截图（试作阶段）。

## 字体

- 来源：[GNU Unifont 16.0.04](https://unifoundry.com/pub/unifont/unifont-16.0.04/)
  （TrueType 构建，GNU GPLv2+，带字体嵌入例外；见 `font/LICENSE-unifont.txt`）。
- `font/unifont-16.0.04-oit.ttf` 在原版基础上只改了竖排度量：
  `unitsPerEm = 1024`，把 `hhea.descent` / `OS/2.sTypoDescender` 从 `-128` 改到 `-384`、
  `OS/2.usWinDescent` 从 `128` 改到 `384`。16px 字号下行距从 16px 变成 20px，
  多行中文才不会贴在一起；字形本身没有改。
- 程序用 `AddFontResourceExW(..., FR_PRIVATE, ...)` 私有加载，不安装到系统。

## 焦点灯圈

`focus_halo.colors` 是四层颜色（外 → 内），`pad` 是灯圈宽度。实现在
`pages/widgets.py::FocusGlow`：四块窄 Canvas 贴在输入框外侧、放在控件下层，
因此中间保持镂空，不挡输入；切换主题 / 失焦 / 控件销毁时会自动收起。

## 合约页

合约页固定使用自己的「起源」主题（`pages/contract_page.py::OPS`）和默认字体，
不跟随这里的全黑 / Unifont 设置。

## 预览

在项目根目录运行：

```bash
python themes/urban_will/preview.py
```

脚本会把预览用户目录放到系统临时目录，不碰真实 `%USERPROFILE%` 下的 OIT 数据。
截图输出到 `themes/urban_will/screenshots/`。
