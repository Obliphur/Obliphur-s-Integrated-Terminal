"""本机外部软件目录与 Process Lasso 部署；不参与设备同步。"""

import configparser
import ctypes
import functools
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
import webbrowser
from pathlib import Path

import config
import tempstore
from i18n import tr

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HOSTS = {"bitsum.com", "www.bitsum.com", "dl.bitsum.com"}
JAVA_URL = "https:" + "/" * 2 + "aka.ms/download-jdk/microsoft-jdk-17-windows-x64.zip"
JAVA_HOSTS = {"aka.ms", "download.visualstudio.microsoft.com"}

CLI_INSTALLERS = {
    "claude_code": ("Claude Code", "@anthropic-ai/claude-code", "claude"),
    "codex": ("Codex", "@openai/codex", "codex"),
    # Antigravity CLI 随官方安装器发布，不存在可验证的官方 npm 包。
    "antigravity": ("Antigravity CLI", None, "agy"),
}


def _obsidian_config_path():
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "obsidian" / "obsidian.json" if appdata else None


def _obsidian_running():
    if os.name != "nt":
        return False
    from ctypes import wintypes

    class _PE(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PE)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PE)]
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return False
    try:
        entry = _PE()
        entry.dwSize = ctypes.sizeof(_PE)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if str(entry.szExeFile).lower() == "obsidian.exe":
                return True
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return False


def _norm_path(path):
    return os.path.normcase(os.path.normpath(str(Path(path).resolve())))


def _find_obsidian_vault(vaults, target):
    tnorm = _norm_path(target)
    best = None
    best_len = -1
    for vault_id, entry in vaults.items():
        if not isinstance(entry, dict) or not entry.get("path"):
            continue
        try:
            vpath = Path(entry["path"]).resolve()
            vnorm = _norm_path(vpath)
            if os.path.commonpath([tnorm, vnorm]) == vnorm and len(vnorm) > best_len:
                best = (str(vault_id), vpath)
                best_len = len(vnorm)
        except (OSError, ValueError):
            continue
    return best


def _infer_obsidian_vault_root(target):
    if target.is_dir() or (not target.exists() and target.suffix.lower() != ".md"):
        return target
    for parent in target.parents:
        if (parent / ".oit_kb").is_dir() or (parent / ".obsidian").is_dir():
            return parent
    return target.parent


def _ensure_obsidian_uri(path):
    target = Path(path).resolve()
    cfg_path = _obsidian_config_path()
    if cfg_path is None:
        return "obsidian://open?path=" + urllib.parse.quote(str(target), safe="")
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    vaults = data.get("vaults")
    if not isinstance(vaults, dict):
        vaults = {}
        data["vaults"] = vaults
    match = _find_obsidian_vault(vaults, target)
    if match is None:
        vault_root = _infer_obsidian_vault_root(target)
        if _obsidian_running():
            raise ValueError(tr(
                "该知识库目录尚未在 Obsidian 中注册，且 Obsidian 正在运行。请先关闭 Obsidian 后重试，或在 Obsidian 中打开文件夹：{path}",
                path=str(vault_root),
            ))
        vault_root.mkdir(parents=True, exist_ok=True)
        vault_id = uuid.uuid4().hex[:16]
        while vault_id in vaults:
            vault_id = uuid.uuid4().hex[:16]
        vaults[vault_id] = {"path": str(vault_root), "ts": int(time.time() * 1000)}
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cfg_path.with_name(cfg_path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, cfg_path)
        match = (vault_id, vault_root)
    vault_id, vault_path = match
    uri = "obsidian://open?vault=" + urllib.parse.quote(vault_id, safe="")
    if _norm_path(target) != _norm_path(vault_path) and not target.is_dir():
        rel = os.path.relpath(str(target), str(vault_path)).replace("\\", "/")
        uri += "&file=" + urllib.parse.quote(rel, safe="")
    return uri


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

    @property
    def obsidian_dir(self):
        """优先使用手选目录，再检查 Windows 的用户级和系统级默认位置。"""
        candidates = [self.data.get("obsidian")]
        for variable, suffixes in (
                ("LOCALAPPDATA", ("Obsidian", os.path.join("Programs", "Obsidian"))),
                ("ProgramFiles", ("Obsidian",)),
                ("ProgramW6432", ("Obsidian",)),
                ("ProgramFiles(x86)", ("Obsidian",))):
            base = os.environ.get(variable)
            if base:
                candidates.extend(os.path.join(base, suffix) for suffix in suffixes)
        return next((str(path) for path in candidates
                     if path and os.path.isfile(os.path.join(path, "Obsidian.exe"))), "")

    def select_obsidian_dir(self, path):
        path = os.path.normpath(str(path).strip())
        if (not os.path.isabs(path) or path.startswith("\\\\")
                or any(c in path for c in '\r\n\x00"')
                or not os.path.isfile(os.path.join(path, "Obsidian.exe"))):
            raise ValueError(tr("请选择包含 Obsidian.exe 的安装目录。"))
        with self._lock:
            data = {**self.data, "obsidian": path}
            if not config.save_json_file(self.path, data):
                raise OSError(tr("保存失败."))
            self.data = data

    def open_obsidian(self, path=None):
        """直接启动识别到的程序；非默认安装仍可通过已注册的 URI 打开。"""
        uri = _ensure_obsidian_uri(path) if path is not None else "obsidian://open"
        directory = self.obsidian_dir
        if directory:
            subprocess.Popen([os.path.join(directory, "Obsidian.exe"), uri], creationflags=NO_WINDOW)
            return
        try:
            os.startfile(uri)
        except OSError as error:
            raise OSError(tr("无法打开 Obsidian，请在「额外功能」中选择安装目录。")) from error

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


class CliInstallManager:
    """额外功能页中的订阅 CLI 安装器；只调用用户本机的 npm。"""
    def __init__(self):
        self.states = {key: {"state": "idle", "progress": 0, "error": ""} for key in CLI_INSTALLERS}
        self._lock = threading.Lock()

    @staticmethod
    def _installed(command):
        from ai_assist.ai_cli import command_path
        return bool(command_path({"command": command}))

    def status(self):
        with self._lock:
            out = {key: dict(value) for key, value in self.states.items()}
        for key, value in out.items():
            value["installed"] = self._installed(CLI_INSTALLERS[key][2])
        return out

    def start(self, key):
        if key not in CLI_INSTALLERS:
            raise ValueError(tr("未知的 CLI."))
        with self._lock:
            if self.states[key]["state"] == "installing":
                return
            self.states[key] = {"state": "installing", "progress": 0, "error": ""}
        threading.Thread(target=self._install, args=(key,), daemon=True).start()

    def _install(self, key):
        name, package, command = CLI_INSTALLERS[key]
        try:
            if package is None:
                if not webbrowser.open("https://antigravity.google/download", new=2):
                    raise RuntimeError(tr("无法打开 Antigravity 官方下载页面."))
                with self._lock:
                    self.states[key] = {"state": "manual", "progress": 0, "error": ""}
                return
            npm = shutil.which("npm") or shutil.which("npm.cmd")
            if not npm:
                raise RuntimeError(tr("未找到 npm，请先安装 Node.js."))
            proc = subprocess.run([npm, "install", "--global", package], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=900,
                                  creationflags=NO_WINDOW)
            if proc.returncode:
                detail = (proc.stderr or proc.stdout or "").strip()[-400:]
                raise RuntimeError(tr("{name} 安装失败", name=name) + (": " + detail if detail else "."))
            if not self._installed(command):
                raise RuntimeError(tr("安装完成，但找不到 {name} 命令.", name=name))
            with self._lock:
                self.states[key] = {"state": "ready", "progress": 100, "error": ""}
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.states[key] = {"state": "error", "progress": 0, "error": str(exc)}


_CLI_MANAGER = None
_CLI_LOCK = threading.Lock()


def cli_manager():
    global _CLI_MANAGER
    with _CLI_LOCK:
        if _CLI_MANAGER is None:
            _CLI_MANAGER = CliInstallManager()
        return _CLI_MANAGER


@functools.lru_cache(maxsize=8)
def manager(data_dir):
    return ExternalSoftware(data_dir)
