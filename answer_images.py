"""把学科子代理的解答排成 OIT 可发送的长 PNG。

排版在本机无头浏览器中完成，聊天控件只收到最终 PNG，不会渲染 Markdown/LaTeX。
"""

import base64
import html
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import tempstore

_LAST_ERROR = ""


def last_error() -> str:
    return _LAST_ERROR


def _browsers() -> list[str]:
    local_app = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.environ.get("OIT_BROWSER", ""),
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        os.path.join(local_app, "Microsoft", "Edge", "Application", "msedge.exe") if local_app else "",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(local_app, "Google", "Chrome", "Application", "chrome.exe") if local_app else "",
        "msedge", "chrome", "chromium", "chromium-browser",
    ]
    found, seen = [], set()
    for candidate in candidates:
        if not candidate:
            continue
        resolved = candidate if os.path.isfile(candidate) else (shutil.which(candidate) or "")
        if not resolved:
            continue
        key = os.path.normcase(os.path.abspath(resolved))
        if key not in seen:
            seen.add(key)
            found.append(resolved)
    return found


def _browser() -> str:
    found = _browsers()
    return found[0] if found else ""


def _inline(value: str) -> str:
    value = html.escape(str(value or ""), quote=False)
    value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
    value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", value)
    value = re.sub(r"\\\[([\s\S]+?)\\\]", lambda m: _math(m.group(1), True), value)
    value = re.sub(r"\$\$([^$]+?)\$\$", lambda m: _math(m.group(1), True), value)
    value = re.sub(r"(?<!\\)\$([^$\n]+?)\$", lambda m: _math(m.group(1), False), value)
    return value


def _math(source: str, display: bool) -> str:
    """将常见 LaTeX 片段转换为浏览器原生 MathML，公式始终只进入截图。"""
    symbols = {"pi": "π", "theta": "θ", "alpha": "α", "beta": "β", "gamma": "γ", "Delta": "Δ",
               "infty": "∞", "cdot": "·", "times": "×", "le": "≤", "leq": "≤", "ge": "≥",
               "geq": "≥", "neq": "≠", "pm": "±", "sum": "∑", "int": "∫", "rightarrow": "→",
               "approx": "≈", "mu": "μ", "left": "", "right": "", "quad": "　", "mathrm": ""}
    escaped = html.escape(str(source or "").strip(), quote=False)
    escaped = re.sub(r"\\(?:d?frac|tfrac)\s*\{([^{}]+)\}\s*\{([^{}]+)\}",
                     lambda m: f"<mfrac><mrow>{m.group(1)}</mrow><mrow>{m.group(2)}</mrow></mfrac>", escaped)
    escaped = re.sub(r"\\sqrt\s*\{([^{}]+)\}", r"<msqrt><mrow>\1</mrow></msqrt>", escaped)
    escaped = re.sub(r"\\text\s*\{([^{}]+)\}", r"<mtext>\1</mtext>", escaped)
    escaped = re.sub(r"\\([A-Za-z]+)", lambda m: html.escape(symbols.get(m.group(1), m.group(1))), escaped)
    escaped = re.sub(r"([A-Za-z0-9])\^\{([^{}]+)\}", r"<msup><mi>\1</mi><mn>\2</mn></msup>", escaped)
    escaped = re.sub(r"([A-Za-z0-9])_\{([^{}]+)\}", r"<msub><mi>\1</mi><mn>\2</mn></msub>", escaped)
    escaped = re.sub(r"([A-Za-z0-9])\^([A-Za-z0-9])", r"<msup><mi>\1</mi><mn>\2</mn></msup>", escaped)
    escaped = re.sub(r"([A-Za-z0-9])_([A-Za-z0-9])", r"<msub><mi>\1</mi><mn>\2</mn></msub>", escaped)
    tag = "div" if display else "span"
    return f'<{tag} class="math"><math xmlns="http://www.w3.org/1998/Math/MathML">{escaped}</math></{tag}>'


def _markdown(text: str) -> str:
    lines = str(text or "").replace("\r\n", "\n").split("\n")
    out, in_code, code = [], False, []
    for raw in lines:
        line = raw.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code, in_code = [], False
            else:
                in_code = True
            continue
        if in_code:
            code.append(line)
            continue
        if not line.strip():
            out.append("<div class=gap></div>")
            continue
        match = re.match(r"^\s*(#{1,3})\s+(.+)$", line)
        if match:
            level = len(match.group(1))
            out.append(f"<h{level}>{_inline(match.group(2))}</h{level}>")
            continue
        match = re.match(r"^\s*(?:[-*]|\d+[.)])\s+(.+)$", line)
        if match:
            out.append("<div class=list-item><span class=bullet>•</span>" + _inline(match.group(1)) + "</div>")
            continue
        out.append("<p>" + _inline(line) + "</p>")
    if in_code:
        out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
    return "\n".join(out)


def render(answer: str, source_image: bytes = None, title: str = "解答") -> bytes:
    """返回一张可发送的长 PNG；浏览器不可用或截图失败时返回空字节。"""
    global _LAST_ERROR
    _LAST_ERROR = ""
    browsers = _browsers()
    if not browsers or not str(answer or "").strip():
        _LAST_ERROR = "no browser found" if not browsers else "empty answer"
        return b""
    work = tempstore.mkdtemp("misc", prefix="answer-image-")
    page = os.path.join(work, "answer.html")
    shot = os.path.join(work, "answer.png")
    source = ""
    if source_image:
        mime = "image/png"
        if source_image.startswith(b"\xff\xd8"):
            mime = "image/jpeg"
        elif source_image.startswith(b"GIF8"):
            mime = "image/gif"
        elif source_image.startswith(b"RIFF"):
            mime = "image/webp"
        source = (f'<section class="source"><h2>题目原图</h2><img src="data:{mime};base64,'
                  + base64.b64encode(source_image).decode("ascii") + '"></section>')
    body = _markdown(answer)
    content_rows = sum(max(1, (len(line) + 52) // 53) for line in str(answer).splitlines())
    source_height = 1000 if source else 0
    shot_height = max(1000, min(16000, 230 + content_rows * 58 + source_height))
    document = f'''<!doctype html><meta charset="utf-8"><style>
*{{box-sizing:border-box}} html,body{{margin:0;background:#f7f8fa;color:#20242b}}
body{{width:1100px;min-height:100vh;padding:54px 72px 80px;font-family:"Segoe UI","Microsoft YaHei",sans-serif;font-size:30px;line-height:1.58}}
h1{{font-size:48px;margin:0 0 25px;border-bottom:3px solid #c9d3df;padding-bottom:18px}}
h2{{font-size:34px;margin:30px 0 14px;color:#315b82}} h3{{font-size:30px;margin:24px 0 10px}}
p{{margin:9px 0;white-space:pre-wrap}} .gap{{height:10px}} .list-item{{display:flex;margin:8px 0;padding-left:18px}}
.bullet{{width:32px;color:#477da7}} code,pre{{font-family:Consolas,monospace;background:#e9eef3;border-radius:6px}}
code{{padding:1px 7px}} pre{{padding:18px 22px;white-space:pre-wrap;font-size:25px;line-height:1.45}}
.math{{font-family:Cambria,"Times New Roman",serif;font-style:italic}} .math math{{font-size:1.12em}} .math math mfrac{{display:inline-table;vertical-align:middle;text-align:center}} .math math mfrac > *{{display:block;padding:0 4px}} .math math mfrac > :first-child{{border-bottom:1px solid #20242b}} .source{{margin:0 0 34px;padding-bottom:24px;border-bottom:2px solid #c9d3df}}
.source img{{display:block;max-width:956px;max-height:900px;margin:0 auto;border:1px solid #c9d3df}}
</style>{source}<h1>{html.escape(title)}</h1><main>{body}</main>'''
    run_kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.PIPE, "timeout": 30, "check": False}
    if sys.platform == "win32":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    errors = []
    try:
        with open(page, "w", encoding="utf-8") as stream:
            stream.write(document)
        page_uri = Path(page).resolve().as_uri()
        for idx, browser in enumerate(browsers):
            profile_dir = os.path.join(work, f"profile-{idx}")
            for headless_flag in ("--headless=new", "--headless"):
                try:
                    if os.path.isfile(shot):
                        os.remove(shot)
                except OSError:
                    pass
                cmd = [
                    browser, headless_flag,
                    f"--user-data-dir={profile_dir}",
                    "--no-sandbox", "--disable-gpu", "--disable-extensions",
                    "--disable-background-networking", "--disable-sync",
                    "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
                    "--allow-file-access-from-files",
                    f"--window-size=1100,{shot_height}",
                    f"--screenshot={shot}",
                    "--virtual-time-budget=1200",
                    "--run-all-compositor-stages-before-draw",
                    page_uri,
                ]
                try:
                    proc = subprocess.run(cmd, **run_kw)
                    if os.path.isfile(shot) and os.path.getsize(shot) > 100:
                        with open(shot, "rb") as stream:
                            return stream.read()
                    err = (proc.stderr or b"").decode("utf-8", "replace").strip().replace("\r", " ").replace("\n", " ")
                    errors.append(f"{os.path.basename(browser)}({headless_flag}) rc={proc.returncode} {err[:180]}")
                except (OSError, subprocess.SubprocessError) as exc:
                    errors.append(f"{os.path.basename(browser)}({headless_flag}) {type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    _LAST_ERROR = " | ".join(errors[:4])
    return b""


if __name__ == "__main__":
    print("answer_images: browser=", bool(_browser()))
