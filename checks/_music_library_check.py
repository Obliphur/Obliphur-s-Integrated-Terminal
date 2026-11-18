# -*- coding: utf-8 -*-
"""默认曲库、乐谱导入、选曲与数据重置恢复。"""

import hashlib
import json
from pathlib import Path
import sys
import zipfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tempstore
import music_library
import music_humanize as H


tempstore.session("music-library-check")
root = Path(tempstore.mkdtemp("music-library-check"))
library = music_library.manager(root)
manifest = json.loads((music_library.DEFAULT_DIR / "catalog.json").read_text(encoding="utf-8"))
assert len(manifest) == 36
assert len(library.catalog()) == 32
assert len(library.catalog("Claudia.txt")) == 32
assert library.catalog("Other.txt") == []
intimate = [item for item in manifest if item.get("relationship_required")]
assert len(intimate) == 4 and all(item["valence_min"] == 70 for item in intimate)
intimate_ids = {item["id"] for item in intimate}
assert intimate_ids.isdisjoint({item["id"] for item in library.catalog("Claudia.txt")})
assert intimate_ids.isdisjoint({item["id"] for item in library.catalog(None, True, 100)})
assert len(library.catalog("Claudia.txt", True, float("inf"))) == 32
assert intimate_ids <= {item["id"] for item in library.catalog("Claudia.txt", True, 70)}
assert len(library.catalog("Claudia.txt", True, 69.99)) == 32
for item in intimate:
    assert library.get(item["id"], "Claudia.txt")[0] is None
    assert library.get(item["id"], "Claudia.txt", True, 69.99)[0] is None
    assert library.get(item["id"], "Claudia.txt", True, 70)[0] is not None
    assert library.get(item["id"], "Other.txt", True, 100)[0] is None
    try:
        library.select(item["id"], "Claudia.txt", True, 69.99)
    except music_library.MusicLibraryError:
        pass
    else:
        raise AssertionError("intimate score unlocked below the Valence threshold")
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
assert uploaded["id"].startswith("upload-") and len(library.catalog()) == 33
assert library.add(source)["id"] == uploaded["id"] and len(library.catalog()) == 33

persona_upload = library.add(source, "Other.txt")
assert len(library.catalog("Other.txt")) == 1
assert library.catalog("Other.txt")[0]["id"] == persona_upload["id"]
assert len(library.catalog("Claudia.txt")) == 33
library.select(persona_upload["id"], "Other.txt")
assert library.selection("Other.txt")["id"] == persona_upload["id"]
assert library.selection("Claudia.txt") is None
assert library.selection("Third.md") is None
for operation in (lambda: library.select(manifest[0]["id"], "Other.txt"),
                  lambda: library.generate_midi(manifest[0]["id"], "Other.txt")):
    try:
        operation()
    except music_library.MusicLibraryError:
        pass
    else:
        raise AssertionError("another persona accessed a Claudia default score")

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
    renders = 0
    def export_performance(self, source_path, destination):
        self.calls += 1
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        track = [H.Event(0, b"\x90\x3c\x50"), H.Event(480, b"\x80\x3c\x00"), H.Event(480, b"\xff\x2f\x00")]
        Path(destination).write_bytes(H.humanize(H.write_midi(0, 480, [track]), seed=7))

    def render_performance(self, source_path, destination):
        self.renders += 1
        Path(destination).write_bytes(b"\xff\xfb\x90\x00" + b"\0" * 500)

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
    prepared = library.prepare_performance(uploaded["id"])
    assert not prepared["reused"] and Path(prepared["path"]).suffix == ".mp3"
    assert library.prepare_performance(uploaded["id"])["reused"] and fake_music.renders == 1
    Path(prepared["path"]).write_bytes(b"bad audio")
    assert not library.prepare_performance(uploaded["id"])["reused"] and fake_music.renders == 2
    # MIDI 参数变动使 MP3 同时失效。
    with patch.object(H.config, "MUSIC_HUMANIZE_FINGER_MS", H.config.MUSIC_HUMANIZE_FINGER_MS + 1):
        assert not library.prepare_performance(uploaded["id"])["reused"] and fake_music.renders == 3

# 播放器在加载结束后重新检查状态，禁止切入专注后迟到的播放。
from music_playback import MusicPlayback
allowed, commands, closed = [True], [], threading.Event()
player = MusicPlayback(lambda: allowed[0])
def gate_mci(command):
    commands.append(command)
    if command.startswith("open "):
        allowed[0] = False
    if command.startswith("close ") and not allowed[0]:
        closed.set()
    return ""
with patch.object(player, "_mci", side_effect=gate_mci):
    assert player.play(prepared["path"])
    assert closed.wait(2) and not any(cmd.startswith("play ") for cmd in commands)
    assert not player.error

# 已开始播放后状态变化会关闭通道；底层打开失败会报告错误。
allowed[0] = True
commands.clear()
closed.clear()
def playing_mci(command):
    commands.append(command)
    if command.startswith("play "):
        allowed[0] = False
    if command.startswith("close ") and not allowed[0]:
        closed.set()
    return "playing"
with patch.object(player, "_mci", side_effect=playing_mci):
    assert player.play(prepared["path"])
    assert closed.wait(2) and any(cmd.startswith("play ") for cmd in commands)
allowed[0] = True
with patch.object(player, "_mci", side_effect=OSError("device failed")):
    assert player.play(prepared["path"])
    deadline = time.monotonic() + 2
    while not player.error and time.monotonic() < deadline:
        time.sleep(0.01)
    assert player.error

# MCI 别名属于创建它的线程：停止、换曲也必须由同一线程关闭，且两首不能叠播。
owner, plays, channels = [], [], []
first_play, second_play, all_closed = threading.Event(), threading.Event(), threading.Event()
def owned_mci(command):
    ident = threading.get_ident()
    if command.startswith("open "):
        assert not owner, "上一首未关闭就打开新通道"
        owner.append(ident)
        channels.append(("open", ident))
    else:
        assert owner == [ident], "跨线程操作 MCI 别名"
    if command.startswith("play "):
        plays.append(ident)
        (first_play if len(plays) == 1 else second_play).set()
    if command.startswith("close "):
        channels.append(("close", ident))
        owner.clear()
        if len(plays) == 2:
            all_closed.set()
    return "playing"
with patch.object(player, "_mci", side_effect=owned_mci):
    assert player.play(prepared["path"]) and first_play.wait(2)
    assert player.play(prepared["path"]) and second_play.wait(2)
    player.stop()
    assert all_closed.wait(2) and not owner and not player.error
    assert [kind for kind, ident in channels] == ["open", "close", "open", "close"]

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
assert len(restored.catalog()) == 32 and restored.selection() is None
assert not list(restored.uploads.iterdir())
assert restored.catalog("Other.txt") == [] and restored.selection("Other.txt") is None
assert len(restored.catalog("Claudia.txt")) == 32
assert restored.catalog("Other.txt") == [] and restored.selection("Other.txt") is None
assert len(restored.catalog("Claudia.txt")) == 32
print("music library check OK")
