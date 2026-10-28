# 界面主题

每个主题一个模块 / 包，只放**纯数据**（dict），不 import config：

| 主题 | 文件 | 说明 |
| --- | --- | --- |
| 经典 (蓝白) | `classic.py` | 原版浅色 |
| Claude (浅色) | `claude.py` | 米白 + 陶土橙 |
| Claude (深色) | `claude_dark.py` | 炭灰 + 亮陶土橙 |
| 都市之意 | `urban_will/` | 全黑 + Unifont 像素字体 + 暖金焦点灯圈; 独占「指令」页 |

`themes/__init__.py` 把各模块汇总成 `THEMES`；`config.apply_theme()` 负责把主题的
颜色 / 字体 / ttk 底色生效，并给旧主题补上 `accent_fill`、`chip_fill`、`input_focus`
等可选键的默认值。

合约页（`pages/contract_page.py`）使用自己的「起源」/「渊默」配色（设置 → 合约页主题）和默认字体，不读取界面主题，
所以切换界面的像素主题不会改变合约页（包括侧边栏字体）。新增主题时不要把
合约页的颜色接回界面主题。
