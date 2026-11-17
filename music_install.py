"""音乐工具部署适配器；复用 OIT 下载器，所有负载保存在用户选择的目录。"""

import json
import os
from pathlib import Path
import platform
import shutil
import zipfile

import config
import external_software
import tempstore
import updater
from music_tools import MusicError, musescore_env, run_process
from i18n import tr


RELEASES = {"audiveris": ("Audiveris/audiveris", "5.11.0"),
            "musescore": ("musescore/MuseScore", "v4.7.7")}


def read_json(url, hosts):
    """元数据与附件使用相同的官方来源限制。"""
    with updater._open(url, 60, allowed_hosts=hosts) as response:
        return json.load(response)


def release_asset(tool):
    repo, version = RELEASES[tool]
    release = read_json(f"https://api.github.com/repos/{repo}/releases/tags/{version}", updater.ALLOWED_HOSTS)
    for asset in release.get("assets", []):
        name = str(asset.get("name", "")).lower()
        if name.endswith(".msi") and "x86_64" in name and (tool != "audiveris" or "windows-x86_64" in name):
            source = updater.asset_source(asset)
            if not source.get("sha256"):
                raise MusicError(tr("官方安装包缺少 SHA-256 校验信息."))
            return version, source
    raise MusicError(tr("没有找到兼容的官方音乐工具安装包."))


def extract_zip(archive, target):
    """只解压普通文件，不允许压缩包路径逃出指定目录。"""
    target = Path(target).resolve()
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for info in package.infolist():
            destination = (target / info.filename).resolve()
            if target not in destination.parents or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise MusicError(tr("音乐工具安装包包含无效路径."))
        package.extractall(target)


def _progress(manager, source):
    size = int(source.get("size") or 0)
    def note(done):
        manager.progress = min(100, done * 100 // size) if size else 0
    return note


def _receipt(folder, executable, version):
    value = {"executable": str(Path(executable).relative_to(folder)), "version": version}
    staged = folder / "oit-install.json.part"
    staged.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(staged, folder / "oit-install.json")


def _install_msi(manager, tool, work):
    version, source = release_asset(tool)
    installer = work / "installer.msi"
    updater.download(source, str(installer), progress=_progress(manager, source),
                     max_download=config.MUSIC_INSTALLER_MAX)
    folder = manager._directory(tool)
    folder.mkdir(parents=True, exist_ok=True)
    # 管理员解包安装：保留自带运行库，不改系统软件注册表、关联或已有版本。
    run_process(["msiexec.exe", "/a", str(installer), "/qn", "/norestart",
                 "TARGETDIR=" + str(folder.resolve()), "/L*v", str(work / "install.log")],
                timeout=config.MUSIC_INSTALL_TIMEOUT, accepted=(0, 3010))
    names = ("Audiveris.exe",) if tool == "audiveris" else ("MuseScore4.exe", "mscore.exe")
    found = [path for name in names for path in folder.rglob(name)]
    if len(found) != 1:
        raise MusicError(tr("音乐组件部署不完整: {tool}", tool=tool))
    # 启动一次命令行探测，不能仅因解包成功就声称可用。
    run_process([str(found[0]), "-help" if tool == "audiveris" else "--version"],
                cwd=str(work), env=musescore_env(), timeout=config.MUSIC_PROBE_TIMEOUT)
    _receipt(folder, found[0], version)


def _pip_source():
    version = config.MUSIC_PIP_VERSION
    metadata = read_json(f"https://pypi.org/pypi/pip/{version}/json", {"pypi.org"})
    wheel = next(item for item in metadata["urls"] if item["filename"].endswith(".whl"))
    return {"url": wheel["url"], "accept": updater.ACCEPT_FILE, "size": wheel["size"],
            "sha256": wheel["digests"]["sha256"]}


def _install_piano(manager, work):
    folder = manager._directory("piano")
    folder.mkdir(parents=True, exist_ok=True)
    manager.model.refresh()
    if not manager.model.ready:
        # 调用既有同步下载实现，不再另起线程争抢外部目录锁。
        manager.model.state, manager.model.error, manager.model.progress = "downloading", "", 0
        manager.model._download(release_location=False)
        if not manager.model.ready:
            raise MusicError(manager.model.error or tr("音乐工具没有生成有效输出."))
    runtime = folder / "runtime"
    python = runtime / "python.exe"
    version = config.MUSIC_PYTHON_VERSION
    runtime_marker = runtime / "oit-python-version.txt"
    ready_marker = runtime / "oit-python-ready.txt"
    complete = (runtime_marker.is_file() and runtime_marker.read_text(encoding="utf-8") == version
                and ready_marker.is_file() and ready_marker.read_text(encoding="utf-8") == version)
    if not python.is_file() or not complete:
        source = {"url": f"https://www.python.org/ftp/python/{version}/python-{version}-embed-amd64.zip",
                  "accept": updater.ACCEPT_FILE, "size": config.MUSIC_PYTHON_SIZE}
        archive = work / "python.zip"
        updater.download_file(source, str(archive), allowed_hosts={"www.python.org"},
                              max_download=config.MUSIC_PYTHON_MAX, progress=_progress(manager, source),
                              workers=config.PIANO_MODEL_CONNECTIONS, segment=config.PIANO_MODEL_SEGMENT)
        extract_zip(archive, runtime)
    site = runtime / "Lib" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    # Embeddable Python 默认隔离；只加入它自己的包目录，不读取系统 site-packages。
    (runtime / "python311._pth").write_text("python311.zip\n.\nLib/site-packages\nimport site\n", encoding="utf-8")
    source = _pip_source()
    archive = work / "pip.whl"
    updater.download_file(source, str(archive), allowed_hosts={"files.pythonhosted.org"},
                          max_download=config.MUSIC_PYTHON_MAX, progress=_progress(manager, source))
    extract_zip(archive, site)
    proxy = updater.upstream_proxy()
    common = [str(python), "-X", "utf8", "-m", "pip", "--isolated", "install", "--no-cache-dir", "--disable-pip-version-check",
              "--only-binary=:all:"]
    common += ["--timeout", str(config.MUSIC_PIP_TIMEOUT), "--retries", str(config.MUSIC_PIP_RETRIES)]
    env = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy", "pip_index_url", "pip_extra_index_url")}
    # requests 在 Windows 还会读取注册表代理；NO_PROXY 明确让首轮直连。
    env.update(TEMP=str(work), TMP=str(work), MPLBACKEND="Agg", NO_PROXY="*")
    manager.progress = 0
    def pip_install(packages):
        # 与更新器一样先直连，失败再走原有上游代理。
        try:
            run_process(common + packages, cwd=str(work), env=env, timeout=config.MUSIC_INSTALL_TIMEOUT)
        except (MusicError, OSError):
            if not proxy:
                raise
            run_process(common + ["--proxy", f"http://{proxy[0]}:{proxy[1]}"] + packages,
                        cwd=str(work), env={**env, "NO_PROXY": ""}, timeout=config.MUSIC_INSTALL_TIMEOUT)
    torch_package = "torch==" + config.MUSIC_TORCH_VERSION
    torch_source = {"url": config.MUSIC_TORCH_WHEEL_URL, "accept": updater.ACCEPT_FILE,
                    "size": config.MUSIC_TORCH_WHEEL_SIZE, "sha256": config.MUSIC_TORCH_WHEEL_SHA256}
    torch_wheel = work / "torch-2.5.1+cpu-cp311-cp311-win_amd64.whl"
    updater.download_file(torch_source, str(torch_wheel), allowed_hosts={"download-r2.pytorch.org"},
                          max_download=config.MUSIC_INSTALLER_MAX, progress=_progress(manager, torch_source),
                          workers=config.PIANO_MODEL_CONNECTIONS, segment=config.PIANO_MODEL_SEGMENT)
    run_process([str(python), "-X", "utf8", "-m", "pip", "--isolated", "install", "--no-index", "--no-deps",
                 str(torch_wheel)], cwd=str(work), env=env, timeout=config.MUSIC_INSTALL_TIMEOUT)
    constraints = work / "constraints.txt"
    constraints.write_text(torch_package + "\n", encoding="utf-8")
    pip_install(["--index-url", "https://pypi.org/simple", "-c", str(constraints),
                 *config.MUSIC_PIANO_SUPPORT_PACKAGES])
    pip_install(["--index-url", "https://pypi.org/simple", "-c", str(constraints), *config.MUSIC_PIANO_PACKAGES])
    smoke = ("import numpy as n; n.float=float; n.int=int; n.complex=complex; import torch,librosa,soundfile; "
             "from piano_transcription_inference import PianoTranscription, sample_rate; assert not torch.cuda.is_available()")
    run_process([str(python), "-X", "utf8", "-c", smoke],
                cwd=str(work), env=env)
    runtime_marker.write_text(version, encoding="utf-8")
    ready_marker.write_text(version, encoding="utf-8")
    _receipt(folder, python, "piano-transcription-inference 0.0.6 / torch " + config.MUSIC_TORCH_VERSION)


def _install_partitura(manager, work):
    folder = manager._directory("partitura")
    runtime = folder / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    python = runtime / "python.exe"
    version = config.MUSIC_PYTHON_VERSION
    runtime_marker = runtime / "oit-python-version.txt"
    if not python.is_file() or not runtime_marker.is_file() or runtime_marker.read_text(encoding="utf-8") != version:
        source = {"url": f"https://www.python.org/ftp/python/{version}/python-{version}-embed-amd64.zip",
                  "accept": updater.ACCEPT_FILE, "size": config.MUSIC_PYTHON_SIZE}
        archive = work / "partitura-python.zip"
        updater.download_file(source, str(archive), allowed_hosts={"www.python.org"},
                              max_download=config.MUSIC_PYTHON_MAX, progress=_progress(manager, source),
                              workers=config.PIANO_MODEL_CONNECTIONS, segment=config.PIANO_MODEL_SEGMENT)
        extract_zip(archive, runtime)
    (runtime / "python311._pth").write_text("python311.zip\n.\nLib/site-packages\nimport site\n", encoding="utf-8")
    site = runtime / "Lib" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    source = _pip_source()
    pip_archive = work / "partitura-pip.whl"
    updater.download_file(source, str(pip_archive), allowed_hosts={"files.pythonhosted.org"},
                          max_download=config.MUSIC_PYTHON_MAX, progress=_progress(manager, source))
    extract_zip(pip_archive, site)
    proxy = updater.upstream_proxy()
    env = {key: value for key, value in os.environ.items()
           if key.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy", "pip_index_url", "pip_extra_index_url")}
    env.update(TEMP=str(work), TMP=str(work), NO_PROXY="*")
    packages = ["--index-url", "https://pypi.org/simple", "numpy==1.26.4",
                "partitura==" + config.MUSIC_PARTITURA_VERSION]
    common = [str(python), "-X", "utf8", "-m", "pip", "--isolated", "install", "--no-cache-dir",
              "--disable-pip-version-check", "--only-binary=:all:", "--timeout", str(config.MUSIC_PIP_TIMEOUT),
              "--retries", str(config.MUSIC_PIP_RETRIES)]
    try:
        run_process(common + packages, cwd=str(work), env=env, timeout=config.MUSIC_INSTALL_TIMEOUT)
    except (MusicError, OSError):
        if not proxy:
            raise
        run_process(common + ["--proxy", f"http://{proxy[0]}:{proxy[1]}"] + packages,
                    cwd=str(work), env={**env, "NO_PROXY": ""}, timeout=config.MUSIC_INSTALL_TIMEOUT)

    # 安装验收实际读取 MIDI 音符事件，确认 Partitura 与其 mido 依赖可用。
    midi = work / "partitura-probe.mid"
    midi.write_bytes(b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0MTrk\x00\x00\x00\x0d"
                     b"\x00\x90\x3c\x50\x83\x60\x80\x3c\x00\x00\xff\x2f\x00")
    smoke = ("import sys,partitura,mido; p=partitura.load_performance_midi(sys.argv[1]); a=p.note_array(); "
             "assert len(a)>0 and 'pitch' in a.dtype.names")
    run_process([str(python), "-X", "utf8", "-c", smoke, str(midi)], cwd=str(work), env=env)
    (runtime / "oit-python-version.txt").write_text(version, encoding="utf-8")
    (runtime / "oit-python-ready.txt").write_text(config.MUSIC_PARTITURA_VERSION, encoding="utf-8")
    _receipt(folder, python, "Partitura " + config.MUSIC_PARTITURA_VERSION)


def install_tool(manager, tool):
    arch = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or platform.machine()
    if arch.lower() not in ("amd64", "x86_64"):
        raise MusicError(tr("音乐工具自动部署目前仅支持 Windows x64."))
    required_free = config.MUSIC_PARTITURA_FREE if tool == "partitura" else config.MUSIC_INSTALL_FREE
    if shutil.disk_usage(manager.external.root).free < required_free:
        raise MusicError(tr("外部软件目录可用空间不足，请至少预留 {size} MiB.", size=required_free // (1024 * 1024)))
    if manager.external.installed:
        # 更新 OIT 专管的配置，让早先部署的 Lasso 也保护 Java / MuseScore。
        ini = Path(manager.external.installed_dir) / "oit-config" / "prolasso.ini"
        ini.parent.mkdir(parents=True, exist_ok=True)
        if ini.exists():
            shutil.copy2(ini, ini.with_suffix(".ini.bak"))
        external_software.write_lasso_config(str(ini))
    work = Path(tempstore.mkdtemp("misc", prefix="music-install-"))
    try:
        if tool == "piano":
            _install_piano(manager, work)
        elif tool == "partitura":
            _install_partitura(manager, work)
        else:
            _install_msi(manager, tool, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
