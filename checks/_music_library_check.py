# -*- coding: utf-8 -*-
"""默认曲库、乐谱导入、选曲与数据重置恢复。"""

import hashlib
import json
from pathlib import Path
import sys
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tempstore
import music_library
import music_humanize as H


tempstore.session("music-library-check")
root = Path(tempstore.mkdtemp("music-library-check"))
library = music_library.manager(root)
manifest = json.loads((music_library.DEFAULT_DIR / "catalog.json").read_text(encoding="utf-8"))
assert len(manifest) == 20
assert len(library.catalog()) == 20
for entry in manifest:
    path = library.defaults / entry["file"]
    assert hashlib.sha256(path.read_bytes()).hexdigest().upper() == entry["sha256"]
    assert music_library._metadata(path)[0]
    android = Path(__file__).resolve().parent.parent / "Android version/app/src/main/assets/music_library/scores" / entry["file"]
    assert android.read_bytes() == path.read_bytes()

source = root / "uploaded.mscx"
source.write_text('<museScore><Score><metaTag name="workTitle">Test Piece</metaTag><metaTag name="composer">Test Composer</metaTag></Score></museScore>', encoding="utf-8")
uploaded = library.add(source)
assert uploaded["title"] == "Test Piece" and uploaded["composer"] == "Test Composer"
assert uploaded["id"].startswith("upload-") and len(library.catalog()) == 21
assert library.add(source)["id"] == uploaded["id"] and len(library.catalog()) == 21

musicxml = root / "uploaded.musicxml"
musicxml.write_text('<score-partwise><work><work-title>Wood Song</work-title></work><identification><creator type="composer">A Composer</creator></identification></score-partwise>', encoding="utf-8")
assert library.add(musicxml)["title"] == "Wood Song"
musescore_zip = root / "uploaded.mscz"
with zipfile.ZipFile(musescore_zip, "w") as archive:
    archive.writestr("score.mscx", source.read_text(encoding="utf-8"))
assert library.add(musescore_zip)["title"] == "Test Piece"
xml_zip = root / "uploaded.mxl"
with zipfile.ZipFile(xml_zip, "w") as archive:
    archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
    archive.writestr("score.xml", musicxml.read_text(encoding="utf-8"))
assert library.add(xml_zip)["title"] == "Wood Song"
selected = library.select(uploaded["id"])
assert Path(selected["path"]).is_file() and library.selection()["id"] == uploaded["id"]

class FakeMusicTools:
    calls = 0
    def export_performance(self, source_path, destination):
        self.calls += 1
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        track = [H.Event(0, b"\x90\x3c\x50"), H.Event(480, b"\x80\x3c\x00"), H.Event(480, b"\xff\x2f\x00")]
        Path(destination).write_bytes(H.humanize(H.write_midi(0, 480, [track]), seed=7))

fake_music = FakeMusicTools()
with patch("music_tools.manager", return_value=fake_music), patch("external_software.manager", return_value=object()):
    # 旧版只有文件头的机械缓存不能继续复用。
    legacy = library.midi_path(uploaded["id"])
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"MThd\0\0\0\x06")
    midi = library.generate_midi(uploaded["id"])
    assert Path(midi["path"]).read_bytes().startswith(b"MThd") and not midi["reused"]
    cached = library.generate_midi(uploaded["id"])
    assert cached["reused"] and fake_music.calls == 1
    with patch.object(H.config, "MUSIC_HUMANIZE_FINGER_MS", H.config.MUSIC_HUMANIZE_FINGER_MS + 1):
        assert not library.generate_midi(uploaded["id"])["reused"] and fake_music.calls == 2
    legacy.write_bytes(b"MThd broken")
    assert not library.generate_midi(uploaded["id"])["reused"] and fake_music.calls == 3

for invalid in (root / "bad.mscx", root / "wrong.txt"):
    invalid.write_text("<not-a-score/>", encoding="utf-8")
    try:
        library.add(invalid)
    except music_library.MusicLibraryError:
        pass
    else:
        raise AssertionError(f"invalid score accepted: {invalid.name}")

import shutil
shutil.rmtree(root / "music_library")
restored = music_library.manager(root)
assert len(restored.catalog()) == 20 and restored.selection() is None
assert not list(restored.uploads.iterdir())
print("music library check OK")
