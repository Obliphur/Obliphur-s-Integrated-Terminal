"""本机音乐工具的统一接口：安装、状态、格式转换和结构化结果。"""

from dataclasses import asdict, dataclass
import functools
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import uuid
import zipfile

import config
import piano_model
import tempstore
from i18n import tr


class MusicError(RuntimeError):
    """可直接显示给用户的音乐工具错误。"""


@dataclass(frozen=True)
class ToolSpec:
    id: str
    name: str
    inputs: tuple
    outputs: tuple


@dataclass(frozen=True)
class MusicResult:
    operation: str
    tool: str
    source: str
    files: tuple

    def to_dict(self):
        return asdict(self)


TOOLS = {
    "audiveris": ToolSpec("audiveris", "Audiveris", (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"), (".mxl",)),
    "musescore": ToolSpec("musescore", "MuseScore Studio", (".mxl", ".musicxml", ".xml", ".mscz", ".mscx"), (".mid", ".midi", ".mscz")),
    "piano": ToolSpec("piano", "钢琴音频转 MIDI", (".wav", ".mp3", ".flac", ".ogg"), (".mid", ".midi")),
}


def run_process(args, *, cwd=None, timeout=None, env=None, accepted=(0,)):
    """输出写入 OIT 临时文件，避免长识谱日志无限占用内存。"""
    log = tempstore.mkdtemp("misc", prefix="music-log-")
    try:
        path = os.path.join(log, "process.log")
        with open(path, "wb") as out:
            result = subprocess.run(args, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT,
                                    timeout=timeout or config.MUSIC_PROCESS_TIMEOUT,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode not in accepted:
            with open(path, "rb") as out:
                out.seek(max(0, os.path.getsize(path) - config.MUSIC_LOG_TAIL))
                detail = out.read().decode("utf-8", "replace")
            raise MusicError(tr("音乐工具执行失败 ({code}): {detail}", code=result.returncode, detail=detail))
        return result.returncode
    finally:
        shutil.rmtree(log, ignore_errors=True)


def validate_output(path):
    """至少核对 MIDI 文件头，避免把空文件或错误页面当成转换成功。"""
    path = Path(path)
    if not path.is_file() or not path.stat().st_size:
        raise MusicError(tr("音乐工具没有生成有效输出."))
    if path.suffix.lower() in (".mid", ".midi"):
        with path.open("rb") as stream:
            if stream.read(4) != b"MThd":
                raise MusicError(tr("音乐工具没有生成有效输出."))
    elif path.suffix.lower() in (".mxl", ".mscz"):
        if not zipfile.is_zipfile(path):
            raise MusicError(tr("音乐工具没有生成有效输出."))


def musescore_env():
    """官方 Windows 包只有 windows Qt 插件，离屏插件会弹出致命错误框。"""
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH")}
    env["QT_QPA_PLATFORM"] = "windows"
    return env


class MusicTools:
    """同步接口供业务层调用，start_* 在线程中执行供设置页使用。"""

    def __init__(self, data_dir, external):
        self.external = external
        self.model = piano_model.manager(data_dir, external)
        self.state, self.error, self.current, self.progress = "idle", "", "", 0
        self.result = None
        self._lock = threading.Lock()

    @property
    def busy(self):
        return self.state in ("installing", "running")

    def capabilities(self):
        return [asdict(spec) for spec in TOOLS.values()]

    def _directory(self, tool):
        if tool not in TOOLS:
            raise MusicError(tr("未知音乐工具: {tool}", tool=tool))
        return Path(self.external.root) / {"audiveris": "Audiveris", "musescore": "MuseScore", "piano": "PianoTranscription"}[tool]

    def _receipt(self, tool):
        if not self.external.root:
            return {}
        try:
            value = json.loads((self._directory(tool) / "oit-install.json").read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def executable(self, tool):
        folder = self._directory(tool).resolve()
        relative = self._receipt(tool).get("executable", "")
        if not isinstance(relative, str) or not relative:
            return ""
        path = (folder / relative).resolve()
        if folder not in path.parents or not path.is_file():
            return ""
        return str(path)

    def status(self, tool=None):
        """每项都区分权重已下载与完整环境已就绪。"""
        if tool is None:
            return {key: self.status(key) for key in TOOLS}
        self._directory(tool)
        self.model.refresh()
        exe = self.executable(tool) if self.external.root else ""
        ready = bool(exe)
        weights = self.model.ready if tool == "piano" else None
        if tool == "piano":
            runtime = self._directory(tool) / "runtime"
            try:
                complete = (runtime / "oit-python-version.txt").read_text(encoding="utf-8") == config.MUSIC_PYTHON_VERSION
                complete = complete and (runtime / "oit-python-ready.txt").read_text(encoding="utf-8") == config.MUSIC_PYTHON_VERSION
            except OSError:
                complete = False
            ready = ready and bool(weights) and complete
        return {"id": tool, "name": TOOLS[tool].name, "ready": ready, "weights_ready": weights,
                "executable": exe, "version": self._receipt(tool).get("version", "")}

    def _begin(self, state):
        with self.external._lock, self._lock:
            if self.busy or self.external.location_busy:
                raise MusicError(tr("音乐工具正在执行其他任务."))
            if sys.platform != "win32" or not self.external.root:
                raise MusicError(tr("请先在 Windows 基本设置中选择外部软件安装位置."))
            self.external.model_busy = True
            self.state, self.error, self.progress, self.result, self.current = state, "", 0, None, ""

    def _finish(self):
        with self.external._lock:
            self.external.model_busy = False

    def _execute(self, state, action):
        self._begin(state)
        try:
            result = action()
            self.result, self.state = result, "ready"
            return result
        except Exception as error:
            self.error, self.state = str(error), "error"
            raise
        finally:
            self._finish()

    def _start(self, state, action):
        self._begin(state)
        def work():
            try:
                self.result = action()
                self.state = "ready"
            except Exception as error:
                self.error, self.state = str(error), "error"
            finally:
                self._finish()
        try:
            threading.Thread(target=work, name="oit-music", daemon=True).start()
        except Exception:
            self.state = "error"
            self._finish()
            raise
        return True

    def install(self, tool=None):
        """tool=None 安装全部缺失组件；已安装组件保留，失败后可重试续装。"""
        return self._execute("installing", lambda: self._install(tool))

    def start_install(self, tool=None):
        return self._start("installing", lambda: self._install(tool))

    def _install(self, tool):
        from music_install import install_tool
        selected = [tool] if tool is not None else list(TOOLS)
        for key in selected:
            self._directory(key)
            self.current, self.progress = key, 0
            if not self.status(key)["ready"]:
                install_tool(self, key)
            if not self.status(key)["ready"]:
                raise MusicError(tr("音乐组件部署不完整: {tool}", tool=TOOLS[key].name))
        return self.status()

    def convert(self, source, destination):
        """依据输入后缀选择音频转录、乐谱导出或图片→识谱→导出流水线。"""
        return self._execute("running", lambda: self._convert(source, destination))

    def start_convert(self, source, destination):
        return self._start("running", lambda: self._convert(source, destination))

    # 供调用方按能力选择的明确入口；它们仍通过同一条串行转换管线。
    def recognize(self, source, destination):
        if Path(destination).suffix.lower() != ".mxl":
            raise MusicError(tr("识谱输出必须是 .mxl 文件."))
        return self.convert(source, destination)

    def export(self, source, destination):
        if Path(source).suffix.lower() not in TOOLS["musescore"].inputs:
            raise MusicError(tr("MuseScore 导出需要 MusicXML 或 MuseScore 文件."))
        return self.convert(source, destination)

    def transcribe(self, source, destination):
        if Path(source).suffix.lower() not in TOOLS["piano"].inputs:
            raise MusicError(tr("钢琴转录需要 WAV、MP3、FLAC 或 OGG 音频."))
        return self.convert(source, destination)

    def _require(self, tool):
        self.current = tool
        if not self.status(tool)["ready"]:
            raise MusicError(tr("请先安装音乐组件: {tool}", tool=TOOLS[tool].name))
        return self.executable(tool)

    def _convert(self, source, destination):
        src, dest = Path(source).resolve(), Path(destination).resolve()
        if not src.is_file() or src == dest:
            raise MusicError(tr("请选择有效输入文件和不同的输出位置."))
        suffix, output = src.suffix.lower(), dest.suffix.lower()
        if suffix in TOOLS["audiveris"].inputs:
            key = "audiveris"
            if output not in (".mxl", ".mid", ".midi", ".mscz"):
                raise MusicError(tr("不支持此音乐文件转换."))
            self._require(key)
            if output != ".mxl":
                self._require("musescore")
        elif suffix in TOOLS["musescore"].inputs:
            key = "musescore"
            if output not in TOOLS[key].outputs:
                raise MusicError(tr("不支持此音乐文件转换."))
            self._require(key)
        elif suffix in TOOLS["piano"].inputs:
            key = "piano"
            if output not in TOOLS[key].outputs:
                raise MusicError(tr("不支持此音乐文件转换."))
            self._require(key)
        else:
            raise MusicError(tr("不支持此音乐文件转换."))
        dest.parent.mkdir(parents=True, exist_ok=True)
        work = Path(tempstore.mkdtemp("misc", prefix="music-convert-"))
        staged = dest.with_name(dest.name + "." + uuid.uuid4().hex + ".part")
        try:
            target = work / ("result" + output)
            if key == "audiveris":
                score = self._recognize(src, work)
                if output == ".mxl":
                    shutil.copyfile(score, target)
                else:
                    self._export(score, target)
            elif key == "musescore":
                self._export(src, target)
            else:
                self._transcribe(src, target)
            validate_output(target)
            shutil.copyfile(target, staged)
            os.replace(staged, dest)
            return MusicResult("convert", key, str(src), (str(dest),))
        finally:
            if staged.exists():
                staged.unlink()
            shutil.rmtree(work, ignore_errors=True)

    def _recognize(self, source, work):
        exe = self._require("audiveris")
        # 单线程逐页处理；只处理副本，避免 Audiveris 在原谱旁写入工程。
        copied = work / ("input" + source.suffix.lower())
        shutil.copyfile(source, copied)
        out = work / "omr"
        run_process([exe, "-batch", "-transcribe", "-export", "-constant",
                     "org.audiveris.omr.sheet.Book.processAllStubsInParallel=false",
                     "-output", str(out), "--", str(copied)], cwd=str(work))
        scores = sorted(out.rglob("*.mxl"))
        if len(scores) != 1:
            raise MusicError(tr("识谱未生成唯一乐谱，请在 Audiveris 中校对或分开乐章后重试."))
        validate_output(scores[0])
        return scores[0]

    def _export(self, source, target):
        exe = self._require("musescore")
        run_process([exe, "-o", str(target), str(source)], cwd=str(target.parent),
                    env=musescore_env())

    def _transcribe(self, source, target):
        exe = self._require("piano")
        worker = Path(__file__).with_name("music_worker.py")
        run_process([exe, "-X", "utf8", str(worker), "--audio", str(source), "--midi", str(target),
                     "--checkpoint", self.model.path, "--threads", str(config.MUSIC_CPU_THREADS)],
                    cwd=str(target.parent))


@functools.lru_cache(maxsize=8)
def manager(data_dir, external):
    return MusicTools(data_dir, external)
