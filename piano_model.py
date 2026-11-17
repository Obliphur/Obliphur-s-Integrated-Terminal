"""钢琴转 MIDI 预训练模型下载；文件放在设置里选择的外部软件目录。"""

import hashlib
import functools
import os
import shutil
import threading

import config
import tempstore
import updater
from i18n import tr


MODEL_NAME = "CRNN_note_F1=0.9677_pedal_F1=0.9186.pth"
MODEL_URL = "https://zenodo.org/records/4034264/files/CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1"
MODEL_MD5 = "22b961b77c1878239fec963362097045"
MODEL_SIZE = config.PIANO_MODEL_SIZE


def _allowed(url):
    return updater._allowed_url(url, {"zenodo.org"})


class ModelDownload:
    def __init__(self, data_dir, external):
        self.data_dir = data_dir
        self.external = external
        self.state, self.error, self.progress = "idle", "", 0
        self.path = ""
        self._lock = threading.Lock()
        self._verified = None
        self._load_path()

    def _load_path(self):
        root = self.external.root
        self.path = os.path.join(root, "PianoTranscription", MODEL_NAME) if root else ""

    @property
    def busy(self):
        return self.state == "downloading"

    @property
    def ready(self):
        try:
            stat = os.stat(self.path)
        except OSError:
            return False
        if stat.st_size != MODEL_SIZE:
            return False
        # 只在文件发生变化时校验，避免界面轮询反复读取整个模型。
        identity = (self.path, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if self._verified is None or self._verified[0] != identity:
            self._verified = (identity, self._md5(self.path) == MODEL_MD5)
        return self._verified[1]

    @staticmethod
    def _md5(path):
        h = hashlib.md5()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
        except OSError:
            return ""
        return h.hexdigest()

    def refresh(self):
        if not self.busy:
            self._load_path()

    def download(self):
        with self._lock:
            if self.busy:
                return False
            with self.external._lock:
                if self.external.busy:
                    raise ValueError(tr("部署进行中，请稍后下载模型."))
                self.refresh()
                if not self.external.root:
                    raise ValueError(tr("请先在基本设置中选择外部软件安装位置."))
                self.external.model_busy = True
                self.state, self.error, self.progress = "downloading", "", 0
            try:
                threading.Thread(target=self._download, daemon=True).start()
            except Exception:
                with self.external._lock:
                    self.external.model_busy = False
                self.state = "idle"
                raise
            return True

    def _download(self):
        work = None
        try:
            if not _allowed(MODEL_URL):
                raise ValueError(tr("模型下载地址不是 Zenodo 官方 HTTPS 地址."))
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            work = tempstore.mkdtemp("misc", prefix="piano-")
            tmp = os.path.join(work, MODEL_NAME + ".part")
            try:
                updater.download_file({"url": MODEL_URL, "accept": updater.ACCEPT_FILE,
                                       "size": MODEL_SIZE, "md5": MODEL_MD5}, tmp,
                                      allowed_hosts={"zenodo.org"}, max_download=MODEL_SIZE,
                                      progress=self._note_progress, workers=config.PIANO_MODEL_CONNECTIONS,
                                      segment=config.PIANO_MODEL_SEGMENT)
            except ValueError as e:
                raise ValueError(tr("模型文件校验失败，已删除不完整文件.")) from e
            # TEMP 和用户选择的目录可能不在同一磁盘，先复制到目标目录再原子替换。
            staged = self.path + ".part"
            try:
                shutil.copyfile(tmp, staged)
                os.replace(staged, self.path)
            finally:
                if os.path.exists(staged):
                    os.remove(staged)
            self.state = "ready"
        except Exception as e:
            self.error, self.state = str(e), "error"
        finally:
            with self.external._lock:
                self.external.model_busy = False
            if work:
                base = os.path.realpath(tempstore.path("misc"))
                resolved = os.path.realpath(work)
                if os.path.commonpath([base, resolved]) == base and resolved != base:
                    shutil.rmtree(resolved, ignore_errors=True)

    def _note_progress(self, done):
        self.progress = min(100, done * 100 // MODEL_SIZE)


@functools.lru_cache(maxsize=8)
def manager(data_dir, external):
    # 与部署管理器一样跨界面重建复用，避免重复下载。
    return ModelDownload(data_dir, external)
