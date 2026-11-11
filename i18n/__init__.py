

import threading

import config

LANGUAGES = [("zh", "中文"), ("en", "English")]
DEFAULT_LANGUAGE = "zh"

_lock = threading.RLock()
_current = DEFAULT_LANGUAGE


from .i18n_en import EN


def set_language(lang: str):
    global _current
    with _lock:
        _current = "en" if str(lang).lower().startswith("en") else "zh"


def get_language() -> str:
    return _current


def is_en() -> bool:
    return _current == "en"


def language_label(lang: str = None) -> str:
    target = lang or _current
    for code, label in LANGUAGES:
        if code == target:
            return label
    return target






_template_lock = threading.RLock()
_templates = []


def refresh_templates():
    import re
    compiled = []
    for zh_key, en_value in EN.items():
        m = re.search(r'\{(\w+)(?::[^}]*)?\}', zh_key)
        if not m:
            continue
        parts = re.split(r'\{(\w+)(?::[^}]*)?\}', zh_key)
        pattern, names = "", []
        for i, part in enumerate(parts):
            if i % 2 == 0:
                pattern += re.escape(part)
            else:
                names.append(part)
                pattern += "(.+?)"
        compiled.append((re.compile("^" + pattern + "$"), names, zh_key, en_value))
    with _template_lock:
        _templates[:] = compiled


def _match_template(text: str):
    if not isinstance(text, str) or '{' in text:
        return None
    with _template_lock:
        candidates = list(_templates)
    for pattern, names, _zh_key, en_value in candidates:
        m = pattern.match(text)
        if not m:
            continue
        try:
            return en_value.format(**dict(zip(names, m.groups())))
        except (KeyError, IndexError, ValueError, AttributeError):
            continue
    return None


refresh_templates()


def tr(zh: str, **kwargs) -> str:
    if not is_en():
        text = zh
    elif zh in EN:
        text = EN[zh]
    else:
        matched = _match_template(zh)
        if matched is not None:
            return config.pixel_safe(matched)
        text = zh
    if kwargs:
        try:
            return config.pixel_safe(text.format(**kwargs))
        except (KeyError, IndexError, ValueError, AttributeError):
            return config.pixel_safe(text)
    return config.pixel_safe(text)


def init_from_config():
    set_language(getattr(config, "LANGUAGE", DEFAULT_LANGUAGE))


# 整点记录的自动填写内容 (MECHANICS 8.2 / 23): 英文名和中文名是同一项, 按界面语言写, 切换语言时合并
AUTO_NAMES = (("Sleeping", "睡眠"), ("Distracted", "分心"), ("Idle", "发呆"), ("Break", "休息"),
              ("School", "上学"), ("Work", "工作"), ("Out", "外出"))


def auto_name(text) -> str:
    """Sleeping / Distracted / Idle / Break 和它们的中文名: 照现在的界面语言写; 别的文字原样."""
    t = str(text or "")
    key = t.strip().casefold()
    for en, zh in AUTO_NAMES:
        if key in (en.casefold(), zh):
            return en if is_en() else zh
    return t


def auto_names_in(text) -> str:
    """一小时的记录里 (多项活动用 / 连接) 每一项都按 auto_name 换成现在的语言; 没有要换的原样返回."""
    t = str(text or "")
    if not t:
        return t
    sep = "／" if "／" in t and "/" not in t else "/"
    parts = t.replace("／", "/").split("/")
    out = [auto_name(p) if p.strip() else p for p in parts]
    if out == parts:
        return t
    return sep.join(out)
