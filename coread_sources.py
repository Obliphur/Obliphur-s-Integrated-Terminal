"""共读官方书源的地址约束与来源版本校验（仅使用标准库）。"""

import re
import urllib.parse
import xml.etree.ElementTree as ET

from i18n import tr


def book_source(source_url):
    source = urllib.parse.urlsplit(str(source_url))
    if source.scheme == "https" and not source.query and not source.fragment:
        match = re.fullmatch(r"/ebooks/([1-9]\d{0,6})/?", source.path)
        if source.netloc in ("www.gutenberg.org", "gutenberg.org") and match:
            book_id = match[1]
            canonical = f"https://www.gutenberg.org/ebooks/{book_id}"
            download = f"https://www.gutenberg.org/cache/epub/{book_id}/pg{book_id}.txt"
            return canonical, download, {"www.gutenberg.org", "gutenberg.org"}, book_id
        if (source.netloc == "standardebooks.org" and
                re.fullmatch(r"/ebooks/(?:[a-z0-9]+(?:-[a-z0-9]+)*/){1,}[a-z0-9]+(?:-[a-z0-9]+)*/?", source.path)
                and not re.search(r"/(?:text|downloads)(?:/|$)", source.path)):
            canonical = "https://standardebooks.org" + source.path.rstrip("/")
            return canonical, canonical + "/text/single-page", {"standardebooks.org"}, None
    raise ValueError(tr("请提供 Project Gutenberg 或 Standard Ebooks 的官方书目地址。"))


def verified_book(raw, canonical, book_id):
    text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    if book_id is None:
        return _standard_book(text, canonical)
    start = re.search(r"^\*\*\* START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK .+?\*\*\*\s*$", text, re.M)
    end = re.search(r"^\*\*\* END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK .+?\*\*\*\s*$", text, re.M)
    header = text[:start.start()] if start else ""
    title = re.search(r"^Title:\s*(.+)$", header, re.M)
    identity = re.search(r"\[\s*e-?book\s*#\s*" + book_id + r"\s*\]", header, re.I)
    if not start or not end or end.start() <= start.end() or not title or not identity:
        raise ValueError(tr("未通过全文校验：缺少对应书号、书名或完整首尾标记"))
    content = text[start.end():end.start()].strip()
    if not content or "\x00" in content or re.search(r"<(?:!doctype|html)\b", content, re.I):
        raise ValueError(tr("下载结果不是可验证的完整书籍文本"))
    return (title[1].strip(), text, "source_edition_markers_checked",
            "官方 HTTPS 来源、书号、UTF-8 文本及全文首尾标记。",
            "Project Gutenberg 电子书；具体使用条件见文件末尾许可。")


def _standard_book(text, canonical):
    error = tr("未通过 Standard Ebooks 全文校验：书名、来源书目、正文或完整结构不匹配。")
    # 不接收实体声明；ElementTree 不解析外部 DTD，也不会请求外部资源。
    if "<!ENTITY" in text.upper():
        raise ValueError(error)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(error) from exc
    ns = {"h": "http://www.w3.org/1999/xhtml"}
    epub_type = "{http://www.idpf.org/2007/ops}type"
    title = root.find("h:head/h:title", ns)
    body = root.find("h:body", ns)
    links = root.findall("h:head/h:link", ns)
    if (root.tag != "{http://www.w3.org/1999/xhtml}html" or body is None or title is None
            or not "".join(title.itertext()).strip()
            or not any(link.get("rel") == "canonical" and link.get("href") == canonical + "/text/single-page"
                       for link in links)):
        raise ValueError(error)
    elements = list(body.iter())
    colophon = next((el for el in elements if "colophon" in el.get(epub_type, "").split()), None)
    license_page = next((el for el in elements if "copyright-page" in el.get(epub_type, "").split()), None)
    matter = [el for el in elements if "bodymatter" in el.get(epub_type, "").split()]
    if (colophon is None or license_page is None or not matter
            or not all("".join(el.itertext()).strip() for el in matter)
            or not max(elements.index(el) for el in matter) < elements.index(colophon) < elements.index(license_page)
            or not any(el.get("href") == canonical for el in colophon.findall(".//h:a", ns))
            or not "".join(license_page.itertext()).strip()):
        raise ValueError(error)
    blocks = {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6",
              "li", "blockquote", "pre", "figcaption", "tr"}
    parts = []

    def render(el):
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in {"script", "style", "nav", "svg"}:
            return
        if tag in blocks:
            parts.append("\n\n")
        if tag == "br":
            parts.append("\n")
        if el.text:
            parts.append(re.sub(r"\s+", " ", el.text))
        for child in el:
            render(child)
            if child.tail:
                parts.append(re.sub(r"\s+", " ", child.tail))
        if tag in blocks:
            parts.append("\n\n")

    render(body)
    content = re.sub(r"\n\s*\n", "\n\n", "".join(parts)).strip()
    return ("".join(title.itertext()).strip(), content, "source_edition_structure_checked",
            "Standard Ebooks 官方 HTTPS 来源、UTF-8、书目链接、完整 XHTML、正文、制作信息和许可页。",
            "Standard Ebooks 版本；具体使用条件见正文许可页。")
