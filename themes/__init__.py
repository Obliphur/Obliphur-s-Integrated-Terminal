"""OIT 的界面主题.

每个主题一个模块 / 包:
  classic.py       经典 (蓝白)
  claude.py        Claude 浅色
  claude_dark.py   Claude 深色
  urban_will/      都市之意 (自带 Unifont 字体和焦点灯圈配置, 另有独占的指令页)

主题模块只放纯数据 (dict), 不 import config; 颜色 / 字体如何生效由 config.apply_theme 负责.
"""

from .classic import THEME as _classic
from .claude import THEME as _claude
from .claude_dark import THEME as _claude_dark
from .urban_will import THEME as _urban_will

THEMES = {
    "classic": _classic,
    "claude": _claude,
    "claude_dark": _claude_dark,
    "urban_will": _urban_will,
}
