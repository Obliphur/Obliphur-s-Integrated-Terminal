"""都市之意 (The Will of the City): 全黑背景 + GNU Unifont 像素字体 + 暖金色镂空焦点提示.

主题数据是纯 dict, 不 import config; 字体文件放在本目录的 font/ 下, 由 config 私有加载.
"""

THEME = {
    "name": "都市之意",
    "dark": True,
    "seamless": True,          # 卡片 / 边框全部融进背景
    "font": {
        "family": "Unifont",
        "mono": "Unifont",
        "file": ("themes", "urban_will", "fonts", "unifont-16.0.04-oit.ttf"),
        "pixel": True,
    },
    # 输入框 / 下拉框获得焦点时的镂空暖色灯圈 (从外到内)
    "focus_halo": {
        "colors": ("#3A2511", "#7A4E1D", "#C08A3E", "#F5D49A"),
        "pad": 4,
    },
    "colors": {
        "bg":           "#000000",
        "bg_card":      "#000000",
        "accent":       "#A5F3FF",
        "accent_hover": "#D6FAFF",
        "accent_fill":  "#062226",
        "accent_fill_hover": "#0A3439",
        "on_accent_fill": "#A5F3FF",
        "chip_fill":    "#062226",
        "chip_fill_hover": "#0A3439",
        "chip_text":    "#A5F3FF",
        "focus":        "#0E3A3D",
        "input_focus":  "#E7C082",
        "text":         "#E8F7F6",
        "text_dim":     "#6F8F8C",
        "text_strong":  "#FFFFFF",
        "success":      "#7EE0B8",
        "warn":         "#E7C082",
        "border":       "#000000",
        "danger":       "#FF5C72",
        "danger_hover": "#FF7A8C",
        "danger_fill":  "#2A0E14",
        "danger_fill_hover": "#40151C",
        "on_danger_fill": "#FF7A8C",
        "danger_bg":    "#000000",
        "on_accent":    "#000000",
        "accent_disabled":    "#2E4A4A",
        "on_accent_disabled": "#5E7F7C",
        "danger_disabled":    "#4A2026",
        "switch_off":   "#1A2B2B",
        "chart_other":  "#3F5B5A",
        "chart_empty":  "#0A1414",
        "chart_future": "#050909",
        "focus_study":  "#A5F3FF",
        "focus_break":  "#7EE0B8",
        # 指令页的蓝色代码 (参考 prescript.neocities.org 的 #96c4ec)
        "prescript_code": "#96C4EC",
        # 顶部细线: 都市之意里不要 (其余主题仍用强调色)
        "topbar":       "#000000",
    },
    "chart": (
        "#A5F3FF", "#7EE0B8", "#E7C082", "#FF7A8C", "#8FA8FF",
        "#63D4D4", "#D39CFF", "#B7E36B", "#FFA45C",
    ),
    "ttk": {
        "frame": "#000000", "frame_hover": "#0B1B1D", "fg": "#E8F7F6",
        "field": "#000000", "border": "#000000", "light": "#000000",
        "dark": "#000000", "trough": "#000000", "select": "#12383C",
        "on_select": "#A5F3FF", "disabled_fg": "#4A6664",
        "list_bg": "#050A0A", "list_fg": "#E8F7F6",
        "list_select": "#12383C", "list_on_select": "#A5F3FF",
    },
}
