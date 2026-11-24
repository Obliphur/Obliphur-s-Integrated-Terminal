"""把学科子代理的解答排成 OIT 可发送的长 PNG。

排版在本机无头浏览器中完成，聊天控件只收到最终 PNG，不会渲染 Markdown/LaTeX。
"""

import base64
import ctypes
import html
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import tempstore

_LAST_ERROR = ""


def last_error() -> str:
    return _LAST_ERROR


def _long_path(path: str) -> str:
    if sys.platform != "win32" or not path:
        return path
    try:
        buf = ctypes.create_unicode_buffer(4096)
        n = ctypes.windll.kernel32.GetLongPathNameW(str(path), buf, len(buf))
        if 0 < n < len(buf):
            return buf.value
    except Exception:
        pass
    return path


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
    value = re.sub(r"\\\((.+?)\\\)", lambda m: _math(m.group(1), False), value)
    value = re.sub(r"(?<!\\)\$([^$\n]+?)\$", lambda m: _math(m.group(1), False), value)
    return value


def _math(source: str, display: bool) -> str:
    """将常见 LaTeX 片段转换为纯 HTML/CSS 排版，避免 Chromium MathML 吞掉裸文本字符。"""
    symbols = {
        "pi": "π", "theta": "θ", "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
        "Delta": "Δ", "omega": "ω", "Omega": "Ω", "lambda": "λ", "phi": "φ", "rho": "ρ",
        "sigma": "σ", "tau": "τ", "eta": "η", "xi": "ξ", "mu": "μ",
        "infty": "∞", "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓",
        "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "neq": "≠", "ne": "≠",
        "approx": "≈", "equiv": "≡", "propto": "∝", "sim": "∼",
        "sum": "∑", "prod": "∏", "int": "∫", "partial": "∂", "nabla": "∇",
        "rightarrow": "→", "to": "→", "leftarrow": "←", "Rightarrow": "⇒", "implies": "⇒",
        "Leftarrow": "⇐", "Leftrightarrow": "⇔", "iff": "⇔",
        "because": "∵", "therefore": "∴", "forall": "∀", "exists": "∃",
        "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆", "cup": "∪", "cap": "∩",
        "angle": "∠", "perp": "⊥", "parallel": "∥", "triangle": "△", "circ": "°", "degree": "°",
        "sin": "sin", "cos": "cos", "tan": "tan", "cot": "cot", "sec": "sec", "csc": "csc",
        "arcsin": "arcsin", "arccos": "arccos", "arctan": "arctan",
        "ln": "ln", "log": "log", "lg": "lg", "exp": "exp", "lim": "lim", "max": "max", "min": "min",
        "left": "", "right": "", "big": "", "Big": "", "bigg": "", "Bigg": "",
        "displaystyle": "", "textstyle": "", "quad": "　", "qquad": "　　",
        "mathrm": "", "rm": "", "mathbf": "", "boldsymbol": "", "vec": "",
    }
    escaped = html.escape(str(source or "").strip(), quote=False)
    escaped = re.sub(r"\\[,;:! ]", " ", escaped)
    escaped = re.sub(r"\\(?:left|right)\s*([()\[\]|.])", lambda m: "" if m.group(1) == "." else m.group(1), escaped)
    escaped = re.sub(r"\\(?:left|right)\\([{}])", r"\1", escaped)
    escaped = re.sub(r"\\([{}])", r"\1", escaped)
    escaped = re.sub(r"\\boxed\s*\{([^{}]+)\}", r'<span class="boxed">\1</span>', escaped)
    escaped = re.sub(r"\\(?:mathrm|text|operatorname|rm)\s*\{([^{}]+)\}", r'<span class="rm">\1</span>', escaped)
    escaped = re.sub(r"\\(?:mathbf|boldsymbol|bm)\s*\{([^{}]+)\}", r'<span class="bf">\1</span>', escaped)
    escaped = re.sub(r"\\vec\s*\{([^{}]+)\}", r'<span class="vec">\1</span>', escaped)
    for _ in range(4):
        prev = escaped
        escaped = re.sub(
            r"\\(?:d?frac|tfrac)\s*\{([^{}]+)\}\s*\{([^{}]+)\}",
            r'<span class="frac"><span class="num">\1</span><span class="den">\2</span></span>',
            escaped,
        )
        escaped = re.sub(
            r"\\sqrt\s*\[([^\[\]]+)\]\s*\{([^{}]+)\}",
            r'<span class="sqrt"><sup class="root-n">\1</sup><span class="rad">√</span><span class="rad-body">\2</span></span>',
            escaped,
        )
        escaped = re.sub(
            r"\\sqrt\s*\{([^{}]+)\}",
            r'<span class="sqrt"><span class="rad">√</span><span class="rad-body">\1</span></span>',
            escaped,
        )
        if escaped == prev:
            break
    escaped = re.sub(r"\\([A-Za-z]+)", lambda m: html.escape(symbols.get(m.group(1), m.group(1))), escaped)
    # 先处理花括号上下标，再处理单字符上下标（支持同时带上下标如 x_0^2）
    escaped = re.sub(r"\^\s*\{([^{}]+)\}", r"<sup>\1</sup>", escaped)
    escaped = re.sub(r"_\s*\{([^{}]+)\}", r"<sub>\1</sub>", escaped)
    escaped = re.sub(r"\^\s*([A-Za-z0-9°′'+-])", r"<sup>\1</sup>", escaped)
    escaped = re.sub(r"_\s*([A-Za-z0-9])", r"<sub>\1</sub>", escaped)
    # 清理残余分组花括号
    escaped = escaped.replace("{", "").replace("}", "")
    cls = "math math-block" if display else "math math-inline"
    tag = "div" if display else "span"
    return f'<{tag} class="{cls}">{escaped}</{tag}>'


def _markdown(text: str) -> str:
    lines = str(text or "").replace("\r\n", "\n").split("\n")
    out, in_code, code = [], False, []
    in_math, math_end, math_buf = False, "", []
    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code, in_code = [], False
            else:
                in_code = True
            continue
        if in_code:
            code.append(line)
            continue
        if in_math:
            if stripped == math_end or stripped.endswith(math_end):
                tail = stripped[:-len(math_end)].strip()
                if tail:
                    math_buf.append(tail)
                out.append(_math(" ".join(math_buf), True))
                in_math, math_end, math_buf = False, "", []
            else:
                math_buf.append(stripped)
            continue
        if stripped in (r"\[", "$$"):
            in_math = True
            math_end = r"\]" if stripped == r"\[" else "$$"
            math_buf = []
            continue
        if stripped.startswith(r"\[") and not stripped.endswith(r"\]"):
            in_math, math_end, math_buf = True, r"\]", [stripped[2:].strip()]
            continue
        if stripped.startswith("$$") and len(stripped) > 2 and not stripped.endswith("$$"):
            in_math, math_end, math_buf = True, "$$", [stripped[2:].strip()]
            continue
        if not stripped:
            out.append("<div class=gap></div>")
            continue
        match = re.match(r"^\s*(#{1,3})\s+(.+)$", line)
        if match:
            level = len(match.group(1))
            out.append(f"<h{level}>{_inline(match.group(2))}</h{level}>")
            continue
        match = re.match(r"^\s*(?:[-*]|\d+[.)])\s+(.+)$", line)
        if match:
            out.append("<div class=list-item><span class=bullet>•</span><div class=li-body>" + _inline(match.group(1)) + "</div></div>")
            continue
        out.append("<p>" + _inline(line) + "</p>")
    if in_math and math_buf:
        out.append(_math(" ".join(math_buf), True))
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
    work = _long_path(tempstore.mkdtemp("misc", prefix="answer-image-"))
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
p{{margin:9px 0;white-space:pre-wrap}} .gap{{height:10px}} .list-item{{display:flex;align-items:flex-start;margin:10px 0;padding-left:14px}}
.bullet{{flex:0 0 28px;color:#477da7}} .li-body{{flex:1;min-width:0}} code,pre{{font-family:Consolas,monospace;background:#e9eef3;border-radius:6px}}
code{{padding:1px 7px}} pre{{padding:18px 22px;white-space:pre-wrap;font-size:25px;line-height:1.45}}
.math{{font-family:"Cambria Math",Cambria,"Times New Roman",serif;font-style:normal;letter-spacing:.01em}}
.math-inline{{display:inline;padding:0 2px}} .math-block{{margin:14px 0;padding:10px 18px;text-align:center;font-size:1.08em;background:#eef2f7;border-radius:10px}}
.math .rm{{font-family:"Segoe UI","Microsoft YaHei",sans-serif;font-style:normal}} .math .bf{{font-weight:700}} .math .boxed{{border:1.5px solid #20242b;padding:2px 8px;border-radius:4px}}
.math sup,.math sub{{font-size:0.72em;line-height:0}} .math sup{{vertical-align:super}} .math sub{{vertical-align:sub}}
.math .frac{{display:inline-flex;flex-direction:column;align-items:center;vertical-align:middle;margin:0 4px;line-height:1.15;font-size:0.92em}}
.math .frac .num{{padding:0 5px 2px;border-bottom:1.8px solid #20242b}} .math .frac .den{{padding:2px 5px 0}}
.math .sqrt{{display:inline-flex;align-items:baseline;vertical-align:middle}} .math .rad{{font-size:1.08em;margin-right:1px}}
.math .rad-body{{border-top:1.8px solid #20242b;padding:0 4px}} .math .root-n{{font-size:0.65em;margin-right:-2px}}
.source{{margin:0 0 34px;padding-bottom:24px;border-bottom:2px solid #c9d3df}}
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
            os.makedirs(profile_dir, exist_ok=True)
            for headless_flag in ("--headless=new", "--headless"):
                try:
                    if os.path.isfile(shot):
                        os.remove(shot)
                except OSError:
                    pass
                cmd = [
                    browser, headless_flag,
                    "--do-not-de-elevate",
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
                    if not (os.path.isfile(shot) and os.path.getsize(shot) > 100) and proc.returncode == 0:
                        # 某些浏览器在管理员进程下仍可能通过 Shell 降权异步启动，等待截图落盘
                        deadline = time.monotonic() + 8.0
                        last_size = -1
                        while time.monotonic() < deadline:
                            if os.path.isfile(shot):
                                size = os.path.getsize(shot)
                                if size > 100 and size == last_size:
                                    break
                                last_size = size
                            time.sleep(0.2)
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
