# -*- coding: utf-8 -*-
"""独立生成人设草稿并保存人设、模板和简介。"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import ai_assist
from .comm_builtin import BUILTIN_PERSONAS, DEFAULT_PERSONA, DEFAULT_BACKGROUND, DEFAULT_EXAMPLE, DEFAULT_ROUTINE, DEFAULT_PROFILE
from .comm_persona import PROFILE_SUFFIX, ROUTINE_SUFFIX, companion_base, list_personas
from .comm_prompt import prefix_prompt
from .persona_codec import MAX_PERSONA, decode, encode, protected_name, read_file as read_persona_file


def claudia_reference_files(folder: str = "") -> dict[str, str]:
    """读取并解密目前存在的所有 Claudia 参考文件（磁盘优先，缺失项回退内置文件）。"""
    out: dict[str, str] = {}
    if folder:
        root = Path(folder)
        if root.is_dir():
            for path in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
                if not path.is_file() or path.name.lower().endswith(".bak"):
                    continue
                if protected_name(path.name):
                    try:
                        out[path.name] = read_persona_file(str(path))
                    except (OSError, ValueError):
                        pass
    for name in (DEFAULT_PERSONA, DEFAULT_BACKGROUND, DEFAULT_EXAMPLE, DEFAULT_ROUTINE, DEFAULT_PROFILE):
        if name not in out and name in BUILTIN_PERSONAS:
            try:
                out[name] = decode(BUILTIN_PERSONAS[name])
            except ValueError:
                pass
    return out


@dataclass(frozen=True)
class Draft:
    persona: str
    templates: str
    profile: str


def draft_parts(draft: Draft):
    parts = (draft.persona.strip(), draft.templates.strip(), draft.profile.strip())
    if not parts[0] or not parts[2] or any(len(part) > MAX_PERSONA for part in parts):
        raise ValueError("人设正文或简介为空，或内容过长。")
    return parts


def generate(profile, title: str, requirements: str, manual_profile: str,
             chat=ai_assist.chat_messages) -> Draft:
    """只发送创作要求和内置参考，手写简介留在本机。"""
    ai_assist.check_profile(profile)
    system = (Path(__file__).resolve().parent.parent / "defaults" / "persona_author.txt").read_text(encoding="utf-8")
    claudia_files = claudia_reference_files()
    refs = [f"=== {name} ===\n{content}" for name, content in claudia_files.items()]
    request = json.dumps({"name": title, "requirements": requirements.strip(),
                          "generate_profile": not manual_profile.strip()}, ensure_ascii=False)
    body = "\n\n".join(("【OIT 聊天前置协议】", prefix_prompt(), "【Claudia 参考文件】", *refs,
                          "【用户创作要求】", request))
    reply = chat(profile, system, [{"role": "user", "content": body}], max_tokens=8192).strip()
    reply = re.sub(r"^\s*\x60{3}(?:json)?\s*\n|\n\s*\x60{3}\s*$", "", reply, flags=re.IGNORECASE)
    try:
        data = json.loads(reply)
        values = tuple(data.get(key, "") for key in ("persona", "templates", "profile"))
        if not all(isinstance(value, str) for value in values):
            raise ValueError
        draft = Draft(values[0], values[1], manual_profile if manual_profile.strip() else values[2])
        return Draft(*draft_parts(draft))
    except (AttributeError, ValueError) as exc:
        raise ValueError("模型没有返回完整的人设草稿，请重新生成。") from exc


def file_names(folder: str, title: str):
    title = title.strip()
    if (not title or len(title) > 80 or title.endswith(".")
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', title)
            or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])", title.split(".")[0].rstrip())
            or (title + ".txt").lower().endswith(PROFILE_SUFFIX)):
        raise ValueError("人设名称无效。")
    base = title + ".txt"
    templates, profile = title + ROUTINE_SUFFIX + ".txt", title + PROFILE_SUFFIX
    title_key = title.casefold()
    existing = {p.name.casefold() for p in Path(folder).iterdir()}
    if (existing.intersection(n.casefold() for n in (base, title + ".md", templates, profile))
            or companion_base(folder, base)
            or any(n.casefold().startswith(title_key + "_") for n in list_personas(folder))):
        raise ValueError("人设名称与已有文件冲突。")
    return base, templates, profile


def save(folder: str, title: str, draft: Draft) -> str:
    persona, templates, profile = draft_parts(draft)
    base_name, templates_name, profile_name = file_names(folder, title)
    created = []
    try:
        # 先写配套文件，最后让人设出现在选择列表。
        for name, content in ((profile_name, profile), (templates_name, templates), (base_name, persona)):
            if not content:
                continue
            stored = encode(name, content)
            path = Path(folder) / name
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                created.append(path)
                handle.write(stored)
    except (OSError, ValueError):
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return base_name
