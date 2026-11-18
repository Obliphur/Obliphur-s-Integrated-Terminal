# -*- coding: utf-8 -*-
"""Claudia可浏览的本地乐谱曲库。"""

import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree
from i18n import tr

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

    @staticmethod
    def _persona_key(persona):
        return hashlib.sha256(str(persona or "").encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def _is_claudia(persona):
        return Path(str(persona or "")).stem.casefold() == "claudia"

    def _uploads_for(self, persona):
        return self.uploads / self._persona_key(persona)

    def _upload_dirs(self, persona):
        directories = [self.uploads] if persona is None else [self._uploads_for(persona)]
        if persona is not None and self._is_claudia(persona):
            directories.append(self.uploads)
        return directories

    def _state_dir(self, persona):
        return self.root if persona is None or self._is_claudia(persona) else self.root / "personas" / self._persona_key(persona)

    def catalog(self, persona=None):
        include_defaults = persona is None or self._is_claudia(persona)
        upload_dirs = self._upload_dirs(persona)
        entries = []
        if include_defaults:
            for item in self.manifest:
                path = self.defaults / item["file"]
                if path.is_file():
                    entries.append({k: item[k] for k in ("id", "title", "title_zh", "composer", "tags", "license", "source")})
                    entries[-1]["kind"] = "default"
        seen_uploads = set()
        for directory in upload_dirs:
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir(), key=lambda p: p.name.casefold()):
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

    def add(self, source, persona=None):
        source = Path(source)
        if source.suffix.lower() not in SUPPORTED:
            raise MusicLibraryError("仅支持 MuseScore 和 MusicXML 乐谱")
        if not source.is_file() or source.stat().st_size <= 0 or source.stat().st_size > MAX_SCORE_BYTES:
            raise MusicLibraryError("乐谱文件不存在或超过50 MiB")
        title, _ = _metadata(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        directory = self.uploads if persona is None else self._uploads_for(persona)
        directory.mkdir(parents=True, exist_ok=True)
        dest = directory / (digest + source.suffix.lower())
        duplicate = next((path for path in directory.iterdir() if path.is_file() and path.stem == digest), None)
        if duplicate is None and not dest.exists():
            shutil.copy2(source, dest)
        return next(item for item in self.catalog(persona) if item["id"] == "upload-" + digest[:16])

    def get(self, score_id, persona=None):
        if persona is None or self._is_claudia(persona):
            for item in self.manifest:
                if item["id"] == score_id:
                    return item, self.defaults / item["file"]
        prefix = "upload-"
        if isinstance(score_id, str) and score_id.startswith(prefix):
            digest_prefix = score_id[len(prefix):]
            for directory in self._upload_dirs(persona):
                if not directory.is_dir():
                    continue
                for path in directory.iterdir():
                    if path.is_file() and path.stem.startswith(digest_prefix) and path.suffix.lower() in SUPPORTED:
                        title, composer = _metadata(path)
                        return {"id": score_id, "title": title, "composer": composer, "kind": "upload"}, path
        return None, None

    def select(self, score_id, persona=None):
        if score_id not in {entry["id"] for entry in self.catalog(persona)}:
            raise MusicLibraryError("乐谱不属于当前人设的曲库")
        item, path = self.get(score_id, persona)
        if not item or not path.is_file():
            raise MusicLibraryError("所选乐谱已不存在")
        state = {"id": item["id"], "title": item["title"], "composer": item.get("composer", ""),
                 "path": str(path), "selected_at": int(__import__("time").time())}
        state_dir = self._state_dir(persona)
        state_dir.mkdir(parents=True, exist_ok=True)
        tmp = state_dir / "selection.json.tmp"
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, state_dir / "selection.json")
        return state

    def selection(self, persona=None):
        try:
            state_dir = self._state_dir(persona)
            value = json.loads((state_dir / "selection.json").read_text(encoding="utf-8"))
            item, path = self.get(value.get("id"), persona)
            if item and path.is_file():
                return {**value, "path": str(path)}
        except (OSError, ValueError, TypeError):
            pass
        return None

    def midi_path(self, score_id):
        if not isinstance(score_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", score_id):
            raise MusicLibraryError("曲目编号无效")
        return self.root / "generated" / (score_id + ".mid")

    def generate_midi(self, score_id=None, persona=None):
        """使用本机 MuseScore 导出演奏 MIDI；复用当前人性化参数的有效缓存。"""
        selected = self.selection(persona) if score_id is None else None
        score_id = score_id or (selected or {}).get("id")
        item, source = self.get(score_id, persona)
        if not item or not source.is_file():
            raise MusicLibraryError("请先选择一首仍在曲库中的乐谱")
        destination = self.midi_path(score_id)
        from music_tools import MusicError, manager as music_manager, validate_output
        from music_humanize import is_current
        try:
            if destination.is_file() and destination.stat().st_mtime_ns >= source.stat().st_mtime_ns and is_current(destination):
                validate_output(destination)
                return {"id": score_id, "title": item["title"], "path": str(destination), "reused": True}
            import external_software
            data_dir = str(self.root.parent)
            tools = music_manager(data_dir, external_software.manager(data_dir))
            tools.export_performance(source, destination)
            validate_output(destination)
        except (OSError, MusicError) as error:
            raise MusicLibraryError(str(error)) from error
        return {"id": score_id, "title": item["title"], "path": str(destination), "reused": False}

    def prepare_performance(self, score_id, persona=None):
        """生成选中乐谱的人性化演奏音频，尚不播放。"""
        if sys.platform != "win32":
            raise MusicLibraryError(tr("本机演奏目前仅支持 Windows."))
        generated = self.generate_midi(score_id, persona)
        audio = self.root / "generated" / (score_id + ".mp3")
        metadata = audio.with_suffix(".mp3.json")
        import config
        signature = {"midi_sha256": hashlib.sha256(Path(generated["path"]).read_bytes()).hexdigest(),
                     "renderer": config.MUSIC_RENDER_VERSION}
        from music_tools import MusicError, validate_output, manager as music_manager
        try:
            valid = False
            if audio.is_file():
                try:
                    if json.loads(metadata.read_text(encoding="utf-8")) != signature:
                        raise ValueError("stale audio")
                    validate_output(audio)
                    valid = True
                except (OSError, ValueError, MusicError):
                    pass
            if not valid:
                import external_software
                data_dir = str(self.root.parent)
                music_manager(data_dir, external_software.manager(data_dir)).render_performance(generated["path"], audio)
                validate_output(audio)
                staged = metadata.with_suffix(".json.tmp")
                try:
                    staged.write_text(json.dumps(signature), encoding="utf-8")
                    os.replace(staged, metadata)
                finally:
                    if staged.exists():
                        staged.unlink()
        except (OSError, MusicError) as error:
            raise MusicLibraryError(str(error)) from error
        return {"id": score_id, "title": generated["title"], "path": str(audio), "reused": valid}


def manager(data_dir):
    return MusicLibrary(data_dir)
