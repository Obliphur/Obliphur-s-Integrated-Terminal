"""Obsidian Markdown storage. The index is disposable; Markdown is authoritative."""
import json
import os
import re
import tempfile
from pathlib import Path


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".oit-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def scalar(text):
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        if text.startswith("'") and text.endswith("'"):
            return text[1:-1].replace("''", "'")
        if text.startswith("[") and text.endswith("]"):
            return [scalar(x) for x in re.split(r",\s*", text[1:-1]) if x.strip()]
        return text


def parse_markdown(text):
    """Parse the documented YAML subset; retain unknown YAML verbatim on edit."""
    match = re.match(r"\A\ufeff?---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
    if not match:
        return {}, text, ""
    front = match.group(1)
    meta, current = {}, None
    for line in front.splitlines():
        field = re.match(r"^([\w-]+):\s*(.*)$", line)
        if field:
            current, value = field.groups()
            meta[current] = scalar(value) if value else []
        elif current and re.match(r"^\s+-\s+", line) and isinstance(meta[current], list):
            meta[current].append(scalar(re.sub(r"^\s+-\s+", "", line)))
    return meta, text[match.end():], front


def serialize(meta, body, original=""):
    # Leave unrecognised property blocks intact, including multiline YAML.
    blocks = re.split(r"(?m)(?=^[\w-]+:)", original) if original else []
    known, prefix = {}, []
    for block in blocks:
        m = re.match(r"^([\w-]+):", block)
        if m:
            known[m.group(1)] = block.rstrip("\n")
        elif block:
            prefix.append(block.rstrip("\n"))
    old, _, _ = parse_markdown("---\n" + original + "\n---\n")
    lines = prefix[:]
    for key, value in meta.items():
        if key in known and old.get(key) == value:
            lines.append(known[key])
        elif isinstance(value, list):
            lines.append(key + ":" + (" []" if not value else ""))
            lines.extend("  - " + json.dumps(x, ensure_ascii=False) for x in value)
        else:
            lines.append(key + ": " + json.dumps(value, ensure_ascii=False))
    lines.extend(block for key, block in known.items() if key not in meta)
    return "---\n" + "\n".join(lines) + "\n---\n" + body


SECTIONS = {"triggers": "触发特征", "statement": "结论本体", "preconditions": "适用前提与禁区",
            "proof_sketch": "大题补证提示", "relations": "衍生与关联", "examples": "典型例题"}


def section_fields(body):
    sections = re.split(r"(?m)^##\s+", body)
    out = {}
    for block in sections[1:]:
        title, _, content = block.partition("\n")
        key = next((k for k, v in SECTIONS.items() if v == title.strip()), None)
        if key and key != "triggers":
            out[key] = content.strip()
    return out


_ROMAN_ITEM_RE = re.compile(r"^(?:i|ii|iii|iv|v|vi|vii|viii|ix|x)\.\s+", re.I)
_INLINE_ROMAN_RE = re.compile(r"(?:[ \t]+|(?<=[；;。！？!?]))((?:ii|iii|iv|v|vi|vii|viii|ix|x)\.\s+)")
_CODE_SPAN_RE = re.compile(r"(```[\s\S]*?```|`[^`\n]+`)")
_DISPLAY_BRACKET_RE = re.compile(r"\\\[([\s\S]*?)\\\]")
_INLINE_PAREN_RE = re.compile(r"\\\((.+?)\\\)", re.S)
_SPACED_INLINE_DOLLAR_RE = re.compile(r"(?<!\$)\$(?!\$)([ \t]+[^$\n]+?|[ \t]*[^$\n]+?[ \t]+)(?<!\$)\$(?!\$)")


def _trim_inline_dollar(match):
    inner = match.group(1).strip()
    if not inner:
        return match.group(0)
    if re.search(r"[\\=^_<>+\-*/]|[a-zA-Z]", inner) and not re.match(r"^\d+[\u4e00-\u9fff\s]", inner):
        return "$" + inner + "$"
    return match.group(0)


def normalize_obsidian_math(text):
    """将 \\(...\\) / \\[...\\] 转为 Obsidian 可渲染的 $...$ / $$...$$，并去掉行内 $ 内侧多余空格。"""
    text = str(text or "")
    if not text or ("\\" not in text and "$" not in text):
        return text
    parts = _CODE_SPAN_RE.split(text)
    for i in range(0, len(parts), 2):
        seg = parts[i]
        seg = _DISPLAY_BRACKET_RE.sub(lambda m: "\n$$\n" + m.group(1).strip() + "\n$$\n" if m.group(1).strip() else "", seg)
        seg = _INLINE_PAREN_RE.sub(lambda m: "$" + m.group(1).strip() + "$" if m.group(1).strip() else "", seg)
        seg = _SPACED_INLINE_DOLLAR_RE.sub(_trim_inline_dollar, seg)
        seg = re.sub(r"\n{3,}", "\n\n", seg)
        parts[i] = seg
    return "".join(parts).strip() if text == text.strip() else "".join(parts)


def format_roman_lines(text):
    """确保多条小写罗马数字序号（i. ii. iii. ...）各自独立换行，不破坏 $...$ 公式。"""
    text = normalize_obsidian_math(text)
    if not text or not re.search(r"\bii\.\s+", text):
        return text
    parts = re.split(r"(\$\$[\s\S]*?\$\$|\$[^$\n]+\$)", text)
    for i in range(0, len(parts), 2):
        parts[i] = _INLINE_ROMAN_RE.sub(r"\n\1", parts[i])
    return "".join(parts)


def update_body(body, changes):
    for key, heading in SECTIONS.items():
        if key not in changes:
            continue
        value = changes[key]
        if isinstance(value, list):
            lines = []
            for x in value:
                item = normalize_obsidian_math(str(x).strip())
                if not item:
                    continue
                lines.append(item if _ROMAN_ITEM_RE.match(item) else "- " + item)
            value = "\n".join(lines)
        else:
            value = format_roman_lines(value)
        block = "## " + heading + "\n" + str(value).strip() + "\n\n"
        pattern = r"(?ms)^## " + re.escape(heading) + r"\s*\n.*?(?=^## |\Z)"
        if re.search(pattern, body):
            body = re.sub(pattern, lambda _: block, body, count=1)
        else:
            body = body.rstrip() + "\n\n" + block
    return body


def safe_name(title):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(title)).strip(" .")[:100] or "Untitled"
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", name):
        name = "_" + name
    return name


def read_card(path):
    meta, body, front = parse_markdown(Path(path).read_text(encoding="utf-8-sig"))
    return {**section_fields(body), **meta, "generality": meta.get("generality", "explain_first"),
            "body": body, "_front": front, "file_path": str(path)}
