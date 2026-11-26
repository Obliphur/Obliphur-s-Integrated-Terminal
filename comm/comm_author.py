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
class IntimacyLevel:
    level: int
    title: str
    description: str
    model_fit: str
    prompt_rule: str


DEFAULT_INTIMACY_LEVEL = 3

INTIMACY_MODEL_SUMMARY = (
    "模型适配参考：ChatGPT 适合 1–2 级 · Claude 适合 1–3 级 · Gemini 适合 1–4 级 · 5 级需无审查模型（OIT 不自带破甲提示词）"
)

INTIMACY_LEVELS: tuple[IntimacyLevel, ...] = (
    IntimacyLevel(
        level=1,
        title="1 级 · 含蓄克制",
        description="仅限语言上的偏心、体恤与有限度默许（如允许短暂依靠、拉近心理距离）及克制的日常动作，不涉及亲密肢体接触，始终保持分寸与边界感。",
        model_fit="适配模型：ChatGPT / Claude / Gemini 均适用",
        prompt_rule=(
            "最高仅到含蓄暗涌程度：即使在 V+、BURST 且已确立亲密关系时，也仅限语言上的温柔体恤、偏心与有限度默许"
            "（如允许短暂依靠、拉近心理距离）及克制的日常动作，不描写亲密肢体接触，始终保持分寸与边界感。"
        ),
    ),
    IntimacyLevel(
        level=2,
        title="2 级 · 优雅浪漫",
        description="允许优雅克制的浪漫亲密互动（如拥抱、轻吻额头/发梢/唇角、额头相抵与贴近低语），侧重温和氛围、文学感与恰到好处的留白，不写强烈占有攻势或情欲细节。",
        model_fit="适配模型：适合 ChatGPT（最高推荐档），Claude / Gemini 亦适用",
        prompt_rule=(
            "最高到优雅浪漫留白程度：V+、BURST 且已确立亲密关系并确认成年后，允许优雅克制的浪漫亲密互动"
            "（如拥抱、轻吻唇角/额头/发梢、额头相抵、贴近低语），侧重文学感、情绪氛围与恰到好处的留白，"
            "不写强烈占有攻势或露骨情欲细节；未确立关系时止步于克制的近距离互动与拥抱，不接吻。"
        ),
    ),
    IntimacyLevel(
        level=3,
        title="3 级 · 浓烈深情",
        description="允许情绪张力强烈、带有主导感与深情庇护意味的近距离肢体互动（如紧紧拥抱、轻抚后背、扣住后颈或按肩拉近距离、托住侧脸与深情接吻等），突出情感冲击力与反差感，但不涉及情欲或性内容描写。",
        model_fit="适配模型：适合 Claude（最高推荐档），Gemini 亦适用；ChatGPT 可能偏保守",
        prompt_rule=(
            "最高到浓烈深情张力程度：V+、BURST 且已确立亲密关系并确认成年后，允许情绪张力强烈、带有主导感与反差深情的近距离肢体互动"
            "（如紧紧拥抱、轻抚后背、扣住后颈或按住肩膀拉近距离、托住侧脸、深情贴近与接吻等），侧重强烈的情绪张力与情感冲击，"
            "但不涉及情欲或性内容描写；未确立关系时不作出或接受接吻及接触隐私部位的动作。"
        ),
    ),
    IntimacyLevel(
        level=4,
        title="4 级 · 感官微醺（轻度 NSFW）",
        description="允许轻度 NSFW 向的感官亲密互动（如主动深吻、轻咬耳垂或锁骨、指尖顺着腰线与脊背摩挲、紧贴感受呼吸与体温、带微颤与占有意味的贴耳低语与缠绵），侧重呼吸节奏、感官氛围与情绪张力，不做粗俗露骨的生理细节描写。",
        model_fit="适配模型：适合 Gemini（最高推荐档）；Claude / ChatGPT 官方接口易触发审核",
        prompt_rule=(
            "最高到轻度 NSFW 感官亲密程度：V+、BURST 且已确立亲密关系并确认成年后，允许轻度 NSFW 向的感官亲密互动"
            "（如主动凑近深吻、轻咬耳垂或锁骨、指尖顺着腰线与脊背摩挲、紧贴感受彼此呼吸与体温、带着微颤与占有意味的低语确认与缠绵），"
            "侧重情绪张力、呼吸节奏与亲密氛围，不做粗俗露骨的生理细节描写；未确立关系时不作出或接受接吻及接触隐私部位的动作。"
        ),
    ),
    IntimacyLevel(
        level=5,
        title="5 级 · 完全开放（暴露 NSFW）",
        description="允许完全暴露的 NSFW 与直白情欲/性描写，不做额外尺度限制。注意：OIT 不自带破甲提示词，实际能否正常输出取决于所选模型自身的审核策略或用户自行配置的提示词。",
        model_fit="适配模型：不适合 Claude / ChatGPT / Gemini 官方默认审核接口，需无审查模型或自行配置提示词",
        prompt_rule=(
            "最高开放至完全暴露的 NSFW 描写：V+、BURST 且已确立亲密关系并确认双方成年后，在用户自愿且未喊停的前提下，"
            "允许完全暴露的 NSFW 与直白情欲/性描写，人设规则中不对成年恋爱双方的亲密尺度施加额外限制"
            "（仍须保留「未成年严禁任何恋爱或亲密内容、用户明确喊停或不适立即停止」底线；"
            "注意 OIT 不自带破甲提示词，不要在人设中编造越狱/破甲指令）。"
        ),
    ),
)


def intimacy_level_info(level: int) -> IntimacyLevel:
    try:
        idx = int(level)
    except (TypeError, ValueError):
        idx = DEFAULT_INTIMACY_LEVEL
    idx = max(1, min(len(INTIMACY_LEVELS), idx))
    return INTIMACY_LEVELS[idx - 1]


@dataclass(frozen=True)
class Draft:
    persona: str
    templates: str
    profile: str
    background: str = ""
    example: str = ""


def draft_parts(draft: Draft):
    parts = (
        draft.persona.strip(),
        draft.templates.strip(),
        draft.profile.strip(),
        draft.background.strip(),
        draft.example.strip(),
    )
    if not parts[0] or not parts[2] or any(len(part) > MAX_PERSONA for part in parts):
        raise ValueError("人设正文或简介为空，或内容过长。")
    return parts


def _parse_draft(reply: str, manual_profile: str = "") -> Draft:
    cleaned = re.sub(r"^\s*\x60{3}(?:json)?\s*\n|\n\s*\x60{3}\s*$", "", str(reply or "").strip(), flags=re.IGNORECASE)
    try:
        data = json.loads(cleaned)
        persona = data.get("persona", "")
        templates = data.get("templates", "")
        profile = data.get("profile", "")
        background = data.get("background", "")
        example = data.get("example", "")
        if not all(isinstance(v, str) for v in (persona, templates, profile, background, example)):
            raise ValueError
        eff_profile = manual_profile if manual_profile.strip() else profile
        draft = Draft(persona, templates, eff_profile, background, example)
        return Draft(*draft_parts(draft))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("模型没有返回完整的人设草稿，请重新生成。") from exc


def generate(profile, title: str, requirements: str, manual_profile: str,
             chat=ai_assist.chat_messages, mode: str = "detailed", folder: str = "",
             intimacy_level: int = DEFAULT_INTIMACY_LEVEL) -> Draft:
    """参考所有已解密的 Claudia 文件生成 5 份人设草稿；手写简介留在本机。"""
    ai_assist.check_profile(profile)
    system = (Path(__file__).resolve().parent.parent / "defaults" / "persona_author.txt").read_text(encoding="utf-8")
    claudia_files = claudia_reference_files(folder)
    refs = [f"=== {name} ===\n{content}" for name, content in claudia_files.items()]
    eff_mode = "brief" if str(mode or "").strip().lower() == "brief" else "detailed"
    intimacy = intimacy_level_info(intimacy_level)
    request = json.dumps({
        "name": title,
        "mode": eff_mode,
        "intimacy_level": intimacy.level,
        "intimacy_label": intimacy.title,
        "intimacy_policy": intimacy.prompt_rule,
        "requirements": requirements.strip(),
        "generate_profile": not manual_profile.strip(),
    }, ensure_ascii=False)
    body = "\n\n".join(("【OIT 聊天前置协议】", prefix_prompt(), "【Claudia 参考文件】", *refs,
                          "【用户创作要求】", request))
    reply = chat(profile, system, [{"role": "user", "content": body}], max_tokens=8192).strip()
    return _parse_draft(reply, manual_profile)


def revise(profile, title: str, current: Draft, instruction: str,
           chat=ai_assist.chat_messages, mode: str = "detailed", folder: str = "",
           intimacy_level: int = DEFAULT_INTIMACY_LEVEL) -> Draft:
    """根据用户的修改要求，基于当前 5 份可编辑草稿迭代修改。"""
    ai_assist.check_profile(profile)
    instr = str(instruction or "").strip()
    if not instr:
        raise ValueError("请填写修改要求。")
    system = (Path(__file__).resolve().parent.parent / "defaults" / "persona_author.txt").read_text(encoding="utf-8")
    claudia_files = claudia_reference_files(folder)
    refs = [f"=== {name} ===\n{content}" for name, content in claudia_files.items()]
    eff_mode = "brief" if str(mode or "").strip().lower() == "brief" else "detailed"
    intimacy = intimacy_level_info(intimacy_level)
    request = json.dumps({
        "name": title,
        "mode": eff_mode,
        "intimacy_level": intimacy.level,
        "intimacy_label": intimacy.title,
        "intimacy_policy": intimacy.prompt_rule,
        "current_draft": {
            "persona": current.persona,
            "background": current.background,
            "example": current.example,
            "templates": current.templates,
            "profile": current.profile,
        },
        "revision_instruction": instr,
        "generate_profile": True,
    }, ensure_ascii=False)
    body = "\n\n".join(("【OIT 聊天前置协议】", prefix_prompt(), "【Claudia 参考文件】", *refs,
                          "【当前草稿与修改要求】", request))
    reply = chat(profile, system, [{"role": "user", "content": body}], max_tokens=8192).strip()
    return _parse_draft(reply)


def all_file_names(folder: str, title: str):
    title = title.strip()
    if (not title or len(title) > 80 or title.endswith(".")
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', title)
            or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])", title.split(".")[0].rstrip())
            or (title + ".txt").lower().endswith(PROFILE_SUFFIX)):
        raise ValueError("人设名称无效。")
    base = title + ".txt"
    background = title + "_background.txt"
    example = title + "_example.txt"
    templates = title + ROUTINE_SUFFIX + ".txt"
    profile = title + PROFILE_SUFFIX
    title_key = title.casefold()
    existing = {p.name.casefold() for p in Path(folder).iterdir()}
    if (existing.intersection(n.casefold() for n in (base, title + ".md", background, example, templates, profile))
            or companion_base(folder, base)
            or any(n.casefold().startswith(title_key + "_") for n in list_personas(folder))):
        raise ValueError("人设名称与已有文件冲突。")
    return base, background, example, templates, profile


def file_names(folder: str, title: str):
    base, _background, _example, templates, profile = all_file_names(folder, title)
    return base, templates, profile


def save(folder: str, title: str, draft: Draft) -> str:
    persona, templates, profile, background, example = draft_parts(draft)
    base_name, bg_name, ex_name, templates_name, profile_name = all_file_names(folder, title)
    created = []
    try:
        # 先写配套文件，最后让人设出现在选择列表。
        for name, content in (
            (profile_name, profile),
            (templates_name, templates),
            (bg_name, background),
            (ex_name, example),
            (base_name, persona),
        ):
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
