"""音乐工具接口的调度、部署、失败保留与并发检查；不安装系统软件。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import external_software as E
import music_install as I
import music_tools as M
import bypassguard
import tempstore


base = Path(tempstore.session("music_tools_check")).resolve()
external = E.ExternalSoftware(str(base / "data"))
root = base / "软件 目录"
root.mkdir()
external.select_root(str(root))
music = M.manager(str(base / "data"), external)
assert music is M.manager(str(base / "data"), external)
assert {spec["id"] for spec in music.capabilities()} == {"audiveris", "musescore", "piano", "partitura"}
assert not any(item["ready"] for item in music.status().values())


def fails(action, exception=M.MusicError):
    try:
        action()
    except exception:
        return
    raise AssertionError("应拒绝此操作")


# 收据不能把可执行路径指向部署目录之外，损坏或非对象 JSON 视为未安装。
folder = music._directory("audiveris")
folder.mkdir()
outside = root / "outside.exe"
outside.write_bytes(b"MZ")
receipt = folder / "oit-install.json"
for value in ([], {"executable": "../outside.exe"}, {"executable": 3}, {"executable": str(outside)}):
    receipt.write_text(json.dumps(value), encoding="utf-8")
    assert not music.status("audiveris")["ready"]


def fake_install(manager, tool):
    path = manager._directory(tool)
    path.mkdir(parents=True, exist_ok=True)
    executable = path / ("python.exe" if tool == "piano" else tool + ".exe")
    executable.write_bytes(b"MZ")
    if tool == "piano":
        (path / "runtime").mkdir(exist_ok=True)
        (path / "runtime" / "oit-python-version.txt").write_text(config.MUSIC_PYTHON_VERSION, encoding="utf-8")
        (path / "runtime" / "oit-python-ready.txt").write_text(config.MUSIC_PYTHON_VERSION, encoding="utf-8")
    if tool == "partitura":
        (path / "runtime").mkdir(exist_ok=True)
        (path / "runtime" / "oit-python-version.txt").write_text(config.MUSIC_PYTHON_VERSION, encoding="utf-8")
        (path / "runtime" / "oit-python-ready.txt").write_text(config.MUSIC_PARTITURA_VERSION, encoding="utf-8")
    I._receipt(path, executable, "test")


with patch.object(type(music.model), "ready", new=property(lambda _: True)), patch.object(I, "install_tool", side_effect=fake_install) as install:
    assert all(s["ready"] for s in music.install().values())
    assert [call.args[1] for call in install.call_args_list] == list(M.TOOLS)
    music.install()
    assert install.call_count == len(M.TOOLS), "重复安装应跳过已有组件"
    assert not external.location_busy

    midi = b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk\x00\x00\x00\x04\x00\xff\x2f\x00"
    calls = []

    def fake_process(args, **kwargs):
        calls.append((args, kwargs))
        if "-transcribe" in args:
            dest = Path(args[args.index("-output") + 1]) / "input.mxl"
            dest.parent.mkdir(parents=True)
            with zipfile.ZipFile(dest, "w") as package:
                package.writestr("score.xml", "<score-partwise/>")
            assert "org.audiveris.omr.sheet.Book.processAllStubsInParallel=false" in args
            assert Path(args[-1]).parent != source.parent
        else:
            dest = Path(args[args.index("-o") + 1] if "-o" in args else args[args.index("--midi") + 1])
            dest.write_bytes(midi)
        return 0

    source = base / "原谱 带空格.pdf"
    source.write_bytes(b"test")
    dest = base / "导出 MIDI.mid"
    with patch.object(M, "run_process", side_effect=fake_process):
        result = music.convert(source, dest)
        assert result.to_dict()["files"] == (str(dest),)
        assert dest.read_bytes() == midi and len(calls) == 2
        calls.clear()
        score = base / "score.mxl"
        music.recognize(source, score)
        assert zipfile.is_zipfile(score) and len(calls) == 1
        calls.clear()
        music.export(score, dest)
        assert len(calls) == 1 and "-o" in calls[0][0]
        calls.clear()
        audio = base / "钢琴.mp3"
        audio.write_bytes(b"audio")
        music.transcribe(audio, dest)
        assert len(calls) == 1 and "--checkpoint" in calls[0][0]
        assert any(str(arg).endswith("music_worker.py") for arg in calls[0][0])
    assert not external.location_busy

    comparison = {"schema": 1, "summary": {"matched": 3, "performance_duration_ms": 1250}}
    def fake_compare(args, **kwargs):
        Path(args[args.index("--output") + 1]).write_text(json.dumps(comparison), encoding="utf-8")
        assert str(args[args.index("--reference") + 1]).endswith("导出 MIDI.mid")
        assert str(args[args.index("--performance") + 1]).endswith("导出 MIDI.mid")
    with patch.object(M, "run_process", side_effect=fake_compare):
        assert music.compare_midi(dest, dest) == comparison

    # 报错或生成无效结果时不能破坏原文件，并应清除临时输出。
    for effect in (RuntimeError("conversion failed"), lambda args, **kw: Path(args[args.index("-o") + 1]).write_bytes(b"bad")):
        with patch.object(M, "run_process", side_effect=effect):
            fails(lambda: music.convert(score, dest), (M.MusicError, RuntimeError))
        assert dest.read_bytes() == midi and not list(dest.parent.glob(dest.name + ".*.part"))
        assert music.state == "error" and not external.location_busy
    fails(lambda: music.convert(source, source))
    fails(lambda: music.convert(source, base / "bad.mp3"))
    fails(lambda: music.convert(base / "missing.pdf", dest))
    fails(lambda: music.recognize(source, dest))
    fails(lambda: music.export(source, dest))
    fails(lambda: music.transcribe(score, dest))

    # 多乐章输出不能静默只取第一份。
    def multiple(args, **kw):
        out = Path(args[args.index("-output") + 1])
        out.mkdir()
        for name in ("one.mxl", "two.mxl"):
            (out / name).write_bytes(b"score")
    with patch.object(M, "run_process", side_effect=multiple):
        fails(lambda: music.convert(source, base / "multi.mxl"))

    # 后台任务跨页面复用；同一目录的模型下载、部署和修改路径均被阻止。
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    def held(*args):
        entered.set()
        assert release.wait(10)
        return M.MusicResult("convert", "piano", str(audio), (str(dest),))
    original_finish = music._finish
    def finish():
        original_finish()
        finished.set()
    with patch.object(music, "_convert", side_effect=held), patch.object(music, "_finish", side_effect=finish):
        music.start_convert(audio, dest)
        assert entered.wait(10)
        assert music.busy and external.location_busy
        fails(lambda: music.start_install())
        fails(lambda: music.model.download(), ValueError)
        fails(lambda: external.select_root(str(base)), ValueError)
        assert external.deploy() is False
        release.set()
        assert finished.wait(10)
    assert music.state == "ready" and not external.location_busy

# GitHub 资产必须保留来源、大小与 SHA-256，不能改用浏览器下载 URL。
asset = {"name": "Audiveris-5.11.0-windows-x86_64.msi", "url": "https://api.github.com/repos/Audiveris/audiveris/releases/assets/123",
         "size": 100, "digest": "sha256:" + "a" * 64}
with patch.object(I, "read_json", return_value={"assets": [asset]}):
    _, value = I.release_asset("audiveris")
    assert value["sha256"] == "a" * 64 and value["url"] == asset["url"]
with patch.object(I, "read_json", return_value={"assets": [{**asset, "digest": None}]}):
    fails(lambda: I.release_asset("audiveris"))

# 解压目录逃逸被拒绝。
archive = base / "unsafe.zip"
with zipfile.ZipFile(archive, "w") as package:
    package.writestr("../escape", "bad")
fails(lambda: I.extract_zip(archive, base / "extract"))
assert not (base / "escape").exists()

# MSI 通过行政解包放入所选目录，不触发系统安装；探测失败不能写就绪收据。
work = base / "msi-work"
work.mkdir()
shutil_folder = music._directory("musescore")
(shutil_folder / "oit-install.json").unlink()
def msi_process(args, **kwargs):
    if args[0] == "msiexec.exe":
        assert "/a" in args and "/i" not in args and "/qn" in args
        assert "TARGETDIR=" + str(shutil_folder.resolve()) in args
        (shutil_folder / "MuseScore4.exe").write_bytes(b"MZ")
    return 0
with patch.object(I, "release_asset", return_value=("test", {"size": 100})), patch.object(I.updater, "download") as download, patch.object(I, "run_process", side_effect=msi_process):
    I._install_msi(music, "musescore", work)
    assert download.call_count == 1 and music.status("musescore")["ready"]

# CPU 环境只用专属 Python；显式固定 PyTorch 并隔离用户 pip 配置和缓存。
piano_calls = []
download_sources = []
def fake_file(source, destination, **kwargs):
    download_sources.append(source)
    if "torch-" in str(destination):
        with open(destination, "wb") as f:
            f.write(b"wheel")
        return
    with zipfile.ZipFile(destination, "w") as package:
        package.writestr("python.exe" if str(destination).endswith("python.zip") else "pip/__init__.py", b"test")
def piano_process(args, **kwargs):
    piano_calls.append((args, kwargs))
    assert args[0] == str(music._directory("piano") / "runtime" / "python.exe")
    if "--no-index" in args:
        return 0
    if "pip" in args:
        assert "--isolated" in args and "--no-cache-dir" in args and "--only-binary=:all:" in args
        assert kwargs["env"]["TEMP"] == str(work)
        assert kwargs["env"]["NO_PROXY"] == "*"
    if str(args[-1]).startswith("import numpy"):
        assert "n.complex=complex" in args[-1]
    return 0
with patch.object(type(music.model), "ready", new=property(lambda _: True)), \
        patch.object(I.updater, "download_file", side_effect=fake_file), \
        patch.object(I.updater, "upstream_proxy", return_value=("127.0.0.1", 7890)), \
        patch.object(I, "_pip_source", return_value={"size": 100}), patch.object(I, "run_process", side_effect=piano_process):
    I._install_piano(music, work)
    (music._directory("piano") / "runtime" / "oit-python-version.txt").write_text(config.MUSIC_PYTHON_VERSION, encoding="utf-8")
    (music._directory("piano") / "runtime" / "oit-python-ready.txt").write_text(config.MUSIC_PYTHON_VERSION, encoding="utf-8")
    assert music.status("piano")["ready"]
assert "torch-2.5.1+cpu-cp311-cp311-win_amd64.whl" in piano_calls[0][0][-1]
torch_source = next(source for source in download_sources if "download-r2.pytorch.org" in source.get("url", ""))
assert torch_source["size"] == config.MUSIC_TORCH_WHEEL_SIZE
assert torch_source["sha256"] == config.MUSIC_TORCH_WHEEL_SHA256
assert "--no-index" in piano_calls[0][0]
assert "setuptools==75.6.0" in piano_calls[1][0]
assert "-c" in piano_calls[2][0] and "--proxy" not in piano_calls[1][0]

# Partitura 独立安装，并通过 MIDI 样本确认可读取音符事件。
partitura_folder = music._directory("partitura")
partitura_work = base / "partitura-work"
partitura_work.mkdir()
partitura_downloads = []
def partitura_file(source, destination, **kwargs):
    partitura_downloads.append(source)
    with zipfile.ZipFile(destination, "w") as package:
        package.writestr("python.exe" if "python.zip" in str(destination) else "pip/__init__.py", b"test")
partitura_calls = []
def partitura_process(args, **kwargs):
    partitura_calls.append((args, kwargs))
    if "-c" in args:
        assert "partitura.load_performance_midi" in args[args.index("-c") + 1]
        assert "note_array()" in args[args.index("-c") + 1] and "'pitch'" in args[args.index("-c") + 1]
        assert Path(args[-1]).is_file() and Path(args[-1]).suffix == ".mid"
with patch.object(I.updater, "download_file", side_effect=partitura_file), \
        patch.object(I.updater, "upstream_proxy", return_value=None), \
        patch.object(I, "_pip_source", return_value={"url": "https://files.pythonhosted.org/pip.whl", "size": 100}), \
        patch.object(I, "run_process", side_effect=partitura_process):
    I._install_partitura(music, partitura_work)
assert any("python.org" in source.get("url", "") for source in partitura_downloads)
assert len(partitura_downloads) == 2
assert "partitura==" + config.MUSIC_PARTITURA_VERSION in partitura_calls[0][0]
assert music.status("partitura")["ready"]
assert music._directory("partitura") not in [music._directory(key) for key in M.CONVERTERS]
assert M.musescore_env()["QT_QPA_PLATFORM"] == "windows"
with patch.object(type(music.model), "ready", new=property(lambda _: True)), \
        patch.object(I.updater, "download_file", side_effect=fake_file), \
        patch.object(I.updater, "upstream_proxy", return_value=("127.0.0.1", 7890)), \
        patch.object(I, "_pip_source", return_value={"size": 100}), \
        patch.object(I, "run_process", side_effect=[0, M.MusicError("direct failed"), 0, 0, 0]) as fallback:
    I._install_piano(music, work)
    assert "--proxy" in fallback.call_args_list[2].args[0]

# 子进程错误/超时向上传递，3010 仅供 MSI 显式接受。
with patch.object(M.subprocess, "run", return_value=subprocess.CompletedProcess([], 3010)):
    fails(lambda: M.run_process(["tool"]))
    assert M.run_process(["msiexec"], accepted=(0, 3010)) == 3010
with patch.object(M.subprocess, "run", side_effect=subprocess.TimeoutExpired("tool", 1)):
    fails(lambda: M.run_process(["tool"]), subprocess.TimeoutExpired)

runtime_bin = str(base / "PianoTranscription" / "runtime")
runtime_prefix = os.path.normcase(os.path.abspath(runtime_bin)).rstrip("\\") + "\\"
assert runtime_prefix not in bypassguard._exempt_prefixes()
with bypassguard.allow_path_prefix(runtime_bin):
    assert runtime_prefix in bypassguard._exempt_prefixes()
assert runtime_prefix not in bypassguard._exempt_prefixes()

# 演奏渲染必须输出可识别的 MP3，失败不能覆盖旧音频。
render_input, render_output = base / "performance.mid", base / "performance.mp3"
render_input.write_bytes(b"MThd" + b"\0" * 10)
mp3 = b"\xff\xfb\x90\x00" + b"\0" * 500
def render_audio(source, destination):
    assert Path(source) == render_input and Path(destination).suffix == ".mp3"
    Path(destination).write_bytes(mp3)
with patch.object(music, "_require", return_value="MuseScore4.exe"), patch.object(music, "_export", side_effect=render_audio):
    assert music.render_performance(render_input, render_output).files == (str(render_output),)
    M.validate_output(render_output)
    assert render_output.read_bytes() == mp3
with patch.object(music, "_require", return_value="MuseScore4.exe"), patch.object(music, "_export", side_effect=lambda src, dst: Path(dst).write_bytes(b"ID3bad")):
    fails(lambda: music.render_performance(render_input, render_output))
    assert render_output.read_bytes() == mp3 and not music.busy and not music.external.model_busy
    assert not list(base.glob("performance.mp3.*"))
fails(lambda: music.render_performance(render_input, base / "wrong.wav"))

print("music tools check OK")
