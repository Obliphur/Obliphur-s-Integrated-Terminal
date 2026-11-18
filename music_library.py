# -*- coding: utf-8 -*-
"""Claudia可浏览的本地乐谱曲库。"""

import hashlib
import json
import os
import re
import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree

DEFAULT_DIR = Path(__file__).resolve().parent / "defaults" / "music_library"
MAX_SCORE_BYTES = 50 * 1024 * 1024
SUPPORTED = {".mscx", ".mscz", ".mxl", ".musicxml", ".xml"}


class MusicLibraryError(ValueError):
    pass


def _xml_text(path: Path) -> bytes:
    suffix = path.suffix.lower()
    if suffix in (".mscz", ".mxl"):
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                preferred = "score.mscx" if suffix == ".mscz" else "META-INF/container.xml"
                if preferred in names and suffix == ".mscz":
                    return archive.read(preferred)
                if suffix == ".mxl" and preferred in names:
                    root = ElementTree.fromstring(archive.read(preferred))
                    target = next((x.attrib.get("full-path") for x in root.iter() if x.tag.endswith("rootfile")), None)
                    if target in names:
                        return archive.read(target)
                for name in names:
                    if name.lower().endswith((".mscx", ".musicxml", ".xml")) and not name.lower().startswith("meta-inf/"):
                        return archive.read(name)
        except (OSError, zipfile.BadZipFile, ElementTree.ParseError):
            raise MusicLibraryError("乐谱文件无效")
        raise MusicLibraryError("压缩包中没有可读取的乐谱")
    return path.read_bytes()


def _metadata(path: Path):
    try:
        raw = _xml_text(path)
        root = ElementTree.fromstring(raw)
    except (OSError, ElementTree.ParseError, MusicLibraryError):
        raise MusicLibraryError("无法读取乐谱内容")
    root_name = root.tag.rsplit("}", 1)[-1].lower()
    if root_name not in ("musescore", "score-partwise", "score-timewise"):
        raise MusicLibraryError("文件不是可识别的 MuseScore 或 MusicXML 乐谱")
    title = composer = ""
    for node in root.iter():
        name = node.tag.rsplit("}", 1)[-1].lower()
        if name == "metatag":
            kind = node.attrib.get("name", "").lower()
            value = (node.text or "").strip()
            if kind in ("worktitle", "movementtitle", "title") and value and not title:
                title = value
            elif kind in ("composer", "arranger") and value and not composer:
                composer = value
        elif name in ("work-title", "movement-title") and not title:
            title = (node.text or "").strip()
        elif name == "creator" and node.attrib.get("type", "").lower() == "composer" and not composer:
            composer = (node.text or "").strip()
    if not title:
        title = path.stem.replace("_", " ").strip()
    return title[:160], composer[:100]


class MusicLibrary:
    def __init__(self, data_dir):
        self.root = Path(data_dir).resolve() / "music_library"
        self.defaults = self.root / "defaults"
        self.uploads = self.root / "uploads"
        self.manifest = json.loads((DEFAULT_DIR / "catalog.json").read_text(encoding="utf-8"))
        self.ensure_defaults()

    def ensure_defaults(self):
        self.defaults.mkdir(parents=True, exist_ok=True)
        self.uploads.mkdir(parents=True, exist_ok=True)
        for item in self.manifest:
            source = DEFAULT_DIR / "scores" / item["file"]
            dest = self.defaults / item["file"]
            if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest().upper() != item["sha256"]:
                raise MusicLibraryError("默认乐谱资源校验失败")
            if not dest.is_file() or hashlib.sha256(dest.read_bytes()).hexdigest().upper() != item["sha256"]:
                shutil.copy2(source, dest)

    def catalog(self):
        entries = []
        for item in self.manifest:
            path = self.defaults / item["file"]
            if path.is_file():
                entries.append({k: item[k] for k in ("id", "title", "title_zh", "composer", "tags", "license", "source")})
                entries[-1]["kind"] = "default"
        seen_uploads = set()
        for path in sorted(self.uploads.iterdir(), key=lambda p: p.name.casefold()):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED:
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            score_id = "upload-" + digest[:16]
            if score_id in seen_uploads:
                continue
            seen_uploads.add(score_id)
            title, composer = _metadata(path)
            entries.append({"id": score_id, "title": title, "composer": composer,
                            "tags": ["user-upload"], "kind": "upload", "format": path.suffix.lower()})
        return entries

    def add(self, source):
        source = Path(source)
        if source.suffix.lower() not in SUPPORTED:
            raise MusicLibraryError("仅支持 MuseScore 和 MusicXML 乐谱")
        if not source.is_file() or source.stat().st_size <= 0 or source.stat().st_size > MAX_SCORE_BYTES:
            raise MusicLibraryError("乐谱文件不存在或超过50 MiB")
        title, _ = _metadata(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        dest = self.uploads / (digest + source.suffix.lower())
        duplicate = next((path for path in self.uploads.iterdir() if path.is_file() and path.stem == digest), None)
        if duplicate is None and not dest.exists():
            shutil.copy2(source, dest)
        return next(item for item in self.catalog() if item["id"] == "upload-" + digest[:16])

    def get(self, score_id):
        for item in self.manifest:
            if item["id"] == score_id:
                return item, self.defaults / item["file"]
        prefix = "upload-"
        if isinstance(score_id, str) and score_id.startswith(prefix):
            digest_prefix = score_id[len(prefix):]
            for path in self.uploads.iterdir():
                if path.is_file() and path.stem.startswith(digest_prefix) and path.suffix.lower() in SUPPORTED:
                    title, composer = _metadata(path)
                    return {"id": score_id, "title": title, "composer": composer, "kind": "upload"}, path
        return None, None

    def select(self, score_id):
        item, path = self.get(score_id)
        if not item or not path.is_file():
            raise MusicLibraryError("所选乐谱已不存在")
        state = {"id": item["id"], "title": item["title"], "composer": item.get("composer", ""),
                 "path": str(path), "selected_at": int(__import__("time").time())}
        tmp = self.root / "selection.json.tmp"
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.root / "selection.json")
        return state

    def selection(self):
        try:
            value = json.loads((self.root / "selection.json").read_text(encoding="utf-8"))
            item, path = self.get(value.get("id"))
            if item and path.is_file():
                return {**value, "path": str(path)}
        except (OSError, ValueError, TypeError):
            pass
        return None

    def midi_path(self, score_id):
        if not isinstance(score_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", score_id):
            raise MusicLibraryError("曲目编号无效")
        return self.root / "generated" / (score_id + ".mid")

    def generate_midi(self, score_id=None):
        """使用本机 MuseScore 导出选曲 MIDI；已有且较新的有效结果直接复用。"""
        selected = self.selection() if score_id is None else None
        score_id = score_id or (selected or {}).get("id")
        item, source = self.get(score_id)
        if not item or not source.is_file():
            raise MusicLibraryError("请先选择一首仍在曲库中的乐谱")
        destination = self.midi_path(score_id)
        from music_tools import MusicError, manager as music_manager, validate_output
        try:
            if destination.is_file() and destination.stat().st_mtime_ns >= source.stat().st_mtime_ns:
                validate_output(destination)
                return {"id": score_id, "title": item["title"], "path": str(destination), "reused": True}
            import external_software
            data_dir = str(self.root.parent)
            tools = music_manager(data_dir, external_software.manager(data_dir))
            tools.export(source, destination)
            validate_output(destination)
        except (OSError, MusicError) as error:
            raise MusicLibraryError(str(error)) from error
        return {"id": score_id, "title": item["title"], "path": str(destination), "reused": False}


def manager(data_dir):
    return MusicLibrary(data_dir)
