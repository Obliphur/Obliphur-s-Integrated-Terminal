"""本机外部软件目录与 Process Lasso 部署；不参与设备同步。"""

import configparser
import ctypes
import functools
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

import config
import tempstore
from i18n import tr

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HOSTS = {"bitsum.com", "www.bitsum.com", "dl.bitsum.com"}
JAVA_URL = "https:" + "/" * 2 + "aka.ms/download-jdk/microsoft-jdk-17-windows-x64.zip"
JAVA_HOSTS = {"aka.ms", "download.visualstudio.microsoft.com"}


def _official_url(url):
    u = urllib.parse.urlsplit(url)
    return u.scheme == "https" and u.hostname in HOSTS and not u.username and u.port in (None, 443)


def installer_url():
    # 32 位 Python 也要按 Windows 的架构选择安装包。
    arch = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or platform.machine()
    suffix = "arm64" if "arm64" in arch.lower() else "64" if "64" in arch else ""
    return f"https://bitsum.com/files/processlassosetup{suffix}.exe"


def download_installer(dest, progress=lambda n: None):
    """通过统一下载器下载官方安装程序，再由调用方验 Authenticode。"""
    import updater
    updater.download_file({"url": installer_url(), "accept": "application/octet-stream"}, dest,
                          allowed_hosts=HOSTS, max_download=config.EXTERNAL_DOWNLOAD_MAX,
                          progress=lambda done: progress(min(99, done * 100 // config.EXTERNAL_DOWNLOAD_MAX)))
    with open(dest, "rb") as f:
        if f.read(2) != b"MZ":
            raise ValueError(tr("下载内容不是 Windows 安装程序."))


def install_java(target, work, progress=lambda n: None):
    import updater
    url = JAVA_URL
    with updater._open(url + ".sha256sum.txt", 30, accept="text/plain", allowed_hosts=JAVA_HOSTS) as response:
        digest = re.search(r"\b[0-9a-fA-F]{64}\b", response.read(4096).decode("ascii", "replace"))
        if not digest or urllib.parse.urlsplit(response.geturl()).hostname not in JAVA_HOSTS:
            raise ValueError(tr("无法读取 Java 安装包校验值."))
    archive = os.path.join(work, "jdk.zip")
    source = {"url": url, "accept": updater.ACCEPT_FILE, "size": None, "sha256": digest.group().lower()}
    updater.download_file(source, archive, allowed_hosts=JAVA_HOSTS, max_download=512 * 1024 * 1024,
                          progress=lambda done: progress(min(99, done * 100 // (512 * 1024 * 1024))))
    import music_install
    root = os.path.join(work, "extracted")
    music_install.extract_zip(archive, root)
    candidates = [str(path.parent.parent) for path in Path(root).rglob("java.exe")
                  if path.parent.name.lower() == "bin"]
    candidates = [path for path in candidates if os.path.isfile(os.path.join(path, "bin", "javac.exe"))]
    if len(candidates) != 1:
        raise ValueError(tr("Java 安装包结构无效."))
    result = subprocess.run([os.path.join(candidates[0], "bin", "java.exe"), "-version"],
                            capture_output=True, timeout=30, creationflags=NO_WINDOW)
    version = (result.stderr or result.stdout).decode("utf-8", "replace")
    if result.returncode or not re.search(r'version "17(?:\.|"|\+)', version):
        raise ValueError(tr("下载的 Java 不是可用的 JDK 17."))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    staged = target + ".new"
    if os.path.isdir(staged):
        shutil.rmtree(staged)
    shutil.copytree(candidates[0], staged)
    backup = target + ".old"
    if os.path.isdir(backup):
        shutil.rmtree(backup)
    if os.path.isdir(target):
        os.replace(target, backup)
    try:
        os.replace(staged, target)
    except Exception:
        if os.path.isdir(backup) and not os.path.exists(target):
            os.replace(backup, target)
        raise
    if os.path.isdir(backup):
        shutil.rmtree(backup)
    progress(100)


def signature_ok(path):
    """执行前要求有效的 Bitsum Authenticode 签名。"""
    script = ("$s=Get-AuthenticodeSignature -LiteralPath '" + path.replace("'", "''") + "'; "
              "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
              "Write-Output ($s.Status.ToString()+'|'+$s.SignerCertificate.Subject)")
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, timeout=60, creationflags=NO_WINDOW)
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    return r.returncode == 0 and out.startswith("Valid|") and bool(re.search(
        r"(?:^|,\s*)O=Bitsum (?:Technologies \(Bitsum LLC\)|LLC)(?:,|$)", out.partition("|")[2]))


def protected_processes():
    """覆盖虚拟环境、版本化解释器与 PyTorch 的 Python 子进程。"""
    return sorted({"python*.exe", "torchrun*.exe", "java*.exe", "audiveris.exe", "musescore4.exe", "mscore.exe",
                   "oit.exe", "oitlink.exe", os.path.basename(sys.executable).lower()})


def set_java_environment(java_home):
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, "Environment", 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            path, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            path, kind = "", winreg.REG_EXPAND_SZ
        java_bin = os.path.join(java_home, "bin")
        entries = [item for item in str(path).split(";") if item]
        entries = [item for item in entries if os.path.normcase(item.strip('"').rstrip("\\"))
                   != os.path.normcase(java_bin.rstrip("\\"))]
        winreg.SetValueEx(key, "JAVA_HOME", 0, winreg.REG_SZ, java_home)
        path_kind = kind if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) else winreg.REG_EXPAND_SZ
        winreg.SetValueEx(key, "Path", 0, path_kind, ";".join([java_bin, *entries]))
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 2, 5000, None)


def write_lasso_config(path):
    """OIT 独立管理的配置，不继承旧的限核、终止或节流规则。"""
    protected = ",".join(protected_processes())
    p = configparser.ConfigParser(interpolation=None)
    p.optionxform = str
    p["OutOfControlProcessRestraint"] = {"OocOn": "true", "OocExclusions": protected,
                                       "ExcludeForegroundProcesses2": "true",
                                       "ExcludeChildrenOfForeground": "true", "UseEfficiencyMode": "false"}
    p["MemoryManagement"] = {"SmartTrimIsEnabled": "true", "SmartTrimWorkingSetTrims": "true",
                             "SmartTrimClearStandbyList": "false", "SmartTrimClearFileCache": "false",
                             "SmartTrimAutoMinimumRAMLoad": str(config.EXTERNAL_TRIM_LOAD),
                             "MinimumProcessWSSInMb": str(config.EXTERNAL_TRIM_MIN_MB),
                             "SmartTrimIntervalMins": str(config.EXTERNAL_TRIM_INTERVAL),
                             "SmartTrimExclusions": protected}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Bitsum 的配置文件使用 UTF-16；不交给 OIT 的加密 JSON 通道。
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-16") as f:
            p.write(f, space_around_delimiters=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def install_command(installer, target, config_dir, log_dir, seed):
    args = [installer, "/S", "/gui_start_type=manual", "/governor_start_type=service",
            "/launch_gui=false", f"/configfolder={config_dir}", f"/logfolder={log_dir}",
            f"/importconfigfrom={seed}"]
    # NSIS 自行解析 /D 后的整段目录，要求放在最后且不加引号。
    return subprocess.list2cmdline(args) + " /D=" + target


class ExternalSoftware:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "external_software.json")
        data = config.load_json_file(self.path, {})
        self.data = data if isinstance(data, dict) else {}
        self.state, self.error, self.progress = "idle", "", 0
        self.java_state, self.java_error, self.java_progress = "idle", "", 0
        self.java_dir = str(self.data.get("java") or "")
        self.model_busy = False
        self._lock = threading.Lock()

    @property
    def root(self):
        return str(self.data.get("root") or "")

    @property
    def installed_dir(self):
        return str(self.data.get("process_lasso") or "")

    @property
    def busy(self):
        return self.state in ("downloading", "verifying", "installing", "configuring")

    @property
    def location_busy(self):
        return self.busy or self.model_busy or self.java_state in ("downloading", "verifying", "installing")

    @property
    def java_installed(self):
        return bool(self.java_dir and os.path.isfile(os.path.join(self.java_dir, "bin", "java.exe"))
                    and os.path.isfile(os.path.join(self.java_dir, "bin", "javac.exe")))

    @property
    def installed(self):
        return bool(self.installed_dir and all(os.path.isfile(os.path.join(self.installed_dir, name))
                    for name in ("ProcessLasso.exe", "ProcessGovernor.exe")))

    def select_root(self, path):
        with self._lock:
            if self.location_busy:
                raise ValueError(tr("部署进行中，请稍后修改安装位置."))
            path = os.path.normpath(str(path).strip())
            if (not os.path.isabs(path) or path.startswith("\\\\") or not os.path.isdir(path)
                    or any(c in path for c in '\r\n\x00"')):
                raise ValueError(tr("请选择本机现有的文件夹."))
            data = {**self.data, "root": path}
            if not config.save_json_file(self.path, data):
                raise OSError(tr("保存失败."))
            self.data = data

    def deploy(self):
        """重复点击只启动一个任务；UI 重建不打断后台任务。"""
        with self._lock:
            if self.location_busy:
                return False
            if sys.platform != "win32":
                raise ValueError(tr("Process Lasso 仅支持 Windows."))
            if not self.root:
                raise ValueError(tr("请先选择外部软件安装位置."))
            target = os.path.join(self.root, "Process Lasso")
            self.state, self.error, self.progress = "downloading", "", 0
            threading.Thread(target=self._deploy, args=(target,), daemon=True).start()
            return True

    def deploy_java(self):
        with self._lock:
            if self.location_busy:
                return False
            if sys.platform != "win32":
                raise ValueError(tr("Java 自动部署仅支持 Windows x64."))
            if not self.root:
                raise ValueError(tr("请先选择外部软件安装位置."))
            target = os.path.join(self.root, "Java", "Microsoft JDK 17")
            self.java_state, self.java_error, self.java_progress = "downloading", "", 0
            threading.Thread(target=self._deploy_java, args=(target,), daemon=True).start()
            return True

    def _deploy_java(self, target):
        work = None
        try:
            work = tempstore.mkdtemp("misc", prefix="java-")
            install_java(target, work, lambda n: setattr(self, "java_progress", n))
            self.java_state = "installing"
            set_java_environment(target)
            java_bin = os.path.join(target, "bin")
            os.environ["JAVA_HOME"] = target
            entries = [entry for entry in os.environ.get("PATH", "").split(os.pathsep)
                       if os.path.normcase(entry) != os.path.normcase(java_bin)]
            os.environ["PATH"] = os.pathsep.join([java_bin, *entries])
            with self._lock:
                data = {**self.data, "java": target}
                if not config.save_json_file(self.path, data):
                    raise OSError(tr("保存失败."))
                self.data, self.java_dir = data, target
            self.java_state = "ready"
        except Exception as error:
            self.java_error, self.java_state = str(error), "error"
        finally:
            if work:
                base, resolved = os.path.realpath(tempstore.path("misc")), os.path.realpath(work)
                if os.path.commonpath([base, resolved]) == base and resolved != base:
                    shutil.rmtree(resolved, ignore_errors=True)

    def _deploy(self, target):
        work = None
        try:
            config_dir = os.path.join(target, "oit-config")
            log_dir = os.path.join(target, "oit-logs")
            os.makedirs(config_dir, exist_ok=True)
            os.makedirs(log_dir, exist_ok=True)
            work = tempstore.mkdtemp("misc", prefix="lasso-")
            installer = os.path.join(work, "ProcessLassoSetup.exe")
            download_installer(installer, lambda n: setattr(self, "progress", n))
            self.state = "verifying"
            if not signature_ok(installer):
                raise ValueError(tr("安装包的 Bitsum 数字签名无效，已取消部署."))
            self.state = "configuring"
            ini = os.path.join(config_dir, "prolasso.ini")
            if os.path.isfile(ini):
                shutil.copy2(ini, ini + ".bak")
            write_lasso_config(ini)
            seed = os.path.join(work, "prolasso.ini")
            shutil.copy2(ini, seed)
            self.state = "installing"
            result = subprocess.run(install_command(installer, target, config_dir, log_dir, seed),
                                    timeout=600, creationflags=NO_WINDOW)
            if result.returncode != 0:
                raise RuntimeError(tr("安装程序退出代码: {code}", code=result.returncode))
            if not all(os.path.isfile(os.path.join(target, name)) for name in ("ProcessLasso.exe", "ProcessGovernor.exe")):
                raise RuntimeError(tr("未在所选位置找到 Process Lasso，部署未完成."))
            write_lasso_config(ini)
            with self._lock:
                data = {**self.data, "process_lasso": target}
                if not config.save_json_file(self.path, data):
                    raise OSError(tr("保存失败."))
                self.data = data
            self.state = "ready"
        except Exception as e:
            self.error, self.state = str(e), "error"
        finally:
            if work:
                base, resolved = os.path.realpath(tempstore.path("misc")), os.path.realpath(work)
                if os.path.commonpath([base, resolved]) == base and resolved != base:
                    shutil.rmtree(resolved, ignore_errors=True)

    def open_lasso(self):
        if not self.installed or self.busy:
            return
        subprocess.Popen([os.path.join(self.installed_dir, "ProcessLasso.exe"),
                          "/ConfigFolder=" + os.path.join(self.installed_dir, "oit-config"), "/ShowWindow"],
                         creationflags=NO_WINDOW)


@functools.lru_cache(maxsize=8)
def manager(data_dir):
    return ExternalSoftware(data_dir)
