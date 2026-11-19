"""生成安卓离线音乐资源；下载统一复用 updater，内置 MIDI 由 MuseScore 精确导出。"""

import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
import urllib.request
import zipfile

import config
import external_software
import music_tools
import tempstore
import updater

ROOT = Path(__file__).resolve().parent
APP = ROOT / "Android version/app/src/main"
ASSETS = APP / "assets/music_engine"


def metadata(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "OIT-music-assets"}), timeout=45) as response:
        return json.load(response)


def release(repo, tag, name, work):
    info = metadata(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
    asset = next(a for a in info["assets"] if a["name"] == name)
    target = work / name
    updater.download(updater.asset_source(asset), str(target), max_download=200 * 1024 * 1024)
    return target


def blob(repo, ref, path, target):
    info = metadata(f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}")
    raw = metadata(info["git_url"])
    content = base64.b64decode(raw["content"])
    assert hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest() == info["sha"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    tempstore.session("android-music-assets")
    work = None
    try:
        work = Path(tempstore.mkdtemp("assets-"))
        archive = release("FluidSynth/fluidsynth", "v2.6.2", "fluidsynth-v2.6.2-android24.zip", work)
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                parts = Path(name).parts
                if name.endswith(".so") and "libfluidsynth-assetloader" not in name:
                    abi = next((x for x in parts if x in ("arm64-v8a", "armeabi-v7a", "x86", "x86_64")), None)
                    if abi:
                        target = APP / "jniLibs" / abi / Path(name).name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(z.read(name))
            print("FluidSynth", z.namelist()[:18], flush=True)
        archive = release("rism-digital/verovio", "version-6.4.1", "verovio-toolkit-wasm.js.zip", work)
        with zipfile.ZipFile(archive) as z:
            print("Verovio", z.namelist(), flush=True)
            for name in z.namelist():
                if name.endswith((".js", ".wasm")):
                    (ASSETS / ("verovio-toolkit.js" if name.endswith(".js") else Path(name).name)).write_bytes(z.read(name))
        blob("spotify/basic-pitch", "v0.4.0", "basic_pitch/saved_models/icassp_2022/nmp.onnx", ASSETS / "basic-pitch.onnx")
        blob("spotify/basic-pitch", "v0.4.0", "LICENSE", ASSETS / "BASIC-PITCH-LICENSE.txt")
        blob("FluidSynth/fluidsynth", "v2.6.2", "LICENSE", ASSETS / "FLUIDSYNTH-LICENSE.txt")
        blob("rism-digital/verovio", "version-6.4.1", "COPYING.LESSER", ASSETS / "VEROVIO-LICENSE.txt")
        blob("rism-digital/verovio", "version-6.4.1", "COPYING", ASSETS / "VEROVIO-GPL.txt")
        # Debian 原包包含音色、作者和完整许可；不从第三方网盘获取音色。
        font = work / "font.deb"
        updater.download_file("https://deb.debian.org/debian/pool/main/t/timgm6mb-soundfont/timgm6mb-soundfont_1.3-5_all.deb",
                              str(font), allowed_hosts={"deb.debian.org"}, max_download=10 * 1024 * 1024)
        data = font.read_bytes()
        pos = 8
        while pos + 60 <= len(data):
            header = data[pos:pos + 60]
            name, size = header[:16].decode().strip().rstrip("/"), int(header[48:58])
            payload = data[pos + 60:pos + 60 + size]
            pos += 60 + size + size % 2
            if name.startswith("data.tar"):
                with tarfile.open(fileobj=io.BytesIO(payload)) as tar:
                    for member in tar.getmembers():
                        if member.name.endswith(".sf2"):
                            (ASSETS / "piano.sf2").write_bytes(tar.extractfile(member).read())
                        elif member.name.endswith("/copyright"):
                            (ASSETS / "SOUNDFONT-LICENSE.txt").write_bytes(tar.extractfile(member).read())
        print("Engines ready", flush=True)
        data_dir = work / "settings"
        data_dir.mkdir()
        external = external_software.ExternalSoftware(str(data_dir))
        deploy = work / "tools"
        deploy.mkdir()
        external.select_root(str(deploy))
        music = music_tools.manager(str(data_dir), external)
        music.install("musescore")
        midi_dir = APP / "assets/music_library/midi"
        midi_dir.mkdir(parents=True, exist_ok=True)
        for item in json.loads((ROOT / "defaults/music_library/catalog.json").read_text(encoding="utf-8")):
            source = ROOT / "defaults/music_library/scores" / item["file"]
            target = midi_dir / (item["id"] + ".mid")
            music.export(str(source), str(target))
            print(item["id"], target.stat().st_size, flush=True)
        (ASSETS / "SOURCES.txt").write_text(
            "FluidSynth v2.6.2 (LGPL-2.1): https://github.com/FluidSynth/fluidsynth/tree/v2.6.2\n"
            "Official Android libraries, unmodified. Dependencies and their licenses are listed in FluidSynth's Android release archive.\n"
            "Verovio 6.4.1 (LGPL-3.0): https://github.com/rism-digital/verovio/tree/version-6.4.1\n"
            "Official JavaScript toolkit, unmodified; source/build instructions at the URL above.\n"
            "Spotify Basic Pitch 0.4.0 (Apache-2.0): https://github.com/spotify/basic-pitch/tree/v0.4.0\n"
            "TimGM6mb 1.3 (GPL-2.0): https://deb.debian.org/debian/pool/main/t/timgm6mb-soundfont/\n"
            "Score licenses and source attribution: ../music_library/*-LICENSE.txt and catalog.json.\n",
            encoding="utf-8")
        files = list(ASSETS.glob("*")) + list((APP / "jniLibs").rglob("*.so")) + list(midi_dir.glob("*.mid"))
        manifest = {str(p.relative_to(APP)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in files if p.is_file() and p.name != "manifest.json"}
        (ASSETS / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
