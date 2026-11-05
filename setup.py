"""
OIT 安装程序 (MECHANICS.md 16.4)
──────────────────────────────────────────────────────
release 里的 setup.exe 就是这个文件用 PyInstaller 打包出来的 (release.py 做, 程序文件打成 payload.zip 一起放进 exe).
运行 setup.exe:
  1. 要管理员权限 (写 Program Files 和注册表; OIT 本身也要管理员);
  2. 检查依赖: OIT 需要 Python 3.10+ (带 tkinter). 电脑上没有就从 python.org 下载官方安装包 (核对数字签名是 Python Software Foundation 才运行),
     静默装一份 (所有用户, 不改 PATH, 不抢 .py 的关联); 可选库 pywin32 用 pip 装 (装不上不影响使用);
  3. 把程序文件复制到安装位置 (默认 C:\\Program Files\\OIT; 同一个位置再装一遍 = 升级, 数据不动) 并预编译;
  4. 注册成系统里的软件 (registration.py): 设置 → 应用 里有 OIT, 卸载按钮运行 uninstall.py; 可选桌面 / 开始菜单快捷方式;
  5. 可选: 装完直接打开 OIT.
OIT 正在运行时不装 (关闭 OIT 会断网又恢复, 升级请用 OIT 自带的更新; 见 MECHANICS 16).

    setup.exe                        图形界面
    setup.exe /S [/D=安装位置]       静默安装 (没有界面, 日志在 %TEMP%\\oit-setup.log)
      选项  /nodesktop /nostart 不建桌面 / 开始菜单快捷方式   /nopython 没有 Python 也不去下载   /nopip 不装 pywin32   /run 装完打开 OIT
    python setup.py --selftest       自检 (不真的安装)
    python setup.py --payload 文件   用 git 里跟踪的文件打出 payload.zip (release.py 用)
"""

import ctypes
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

import registration

HERE = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "Obliphur's Integrated Terminal"
DEFAULT_DIR = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "OIT")
PYTHON_VERSION = "3.13.17"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-amd64.exe"
PYTHON_SIGNER = "Python Software Foundation"
MIN_PYTHON = (3, 10)
MAX_PYTHON_DOWNLOAD = 80 * 1024 * 1024
NO_WINDOW = 0x08000000
LOG_FILE = os.path.join(tempfile.gettempdir(), "oit-setup.log")
# 不进安装包的东西 (和 updater.SKIP_DIRS 一致, 再加上只在开发时用的)
SKIP_TOP = {"Android version", ".git", ".github", "__pycache__", ".vscode", ".idea", "_review", "build", "dist"}
SKIP_FILES = {".gitignore", ".gitattributes", "setup.py", "make_icon.py", "closed_test.bat"}
PYTHON_CHECK = ("import sys, tkinter, sqlite3, ssl, ctypes, winreg, json, socket, http.server; "
                "print(sys.version_info[0], sys.version_info[1], sys.executable)")

TEXT = {
    "title": ("OIT 安装程序", "OIT Setup"),
    "dir": ("安装位置", "Install location"),
    "browse": ("浏览…", "Browse…"),
    "desktop": ("桌面快捷方式", "Desktop shortcut"),
    "start": ("开始菜单快捷方式", "Start menu shortcut"),
    "pywin32": ("安装可选依赖库 pywin32 (需要联网, 装不上不影响使用)", "Install optional library pywin32 (needs internet; OK if it fails)"),
    "run": ("安装完成后打开 OIT", "Launch OIT when finished"),
    "install": ("安装", "Install"),
    "close": ("关闭", "Close"),
    "cancel": ("取消", "Cancel"),
    "intro": ("将安装 Obliphur's Integrated Terminal {v}.\n需要 Python {p}+ (带 tkinter); 电脑上没有的话会从 python.org 下载安装官方版本.",
              "This installs Obliphur's Integrated Terminal {v}.\nIt needs Python {p}+ (with tkinter); if missing, the official build is downloaded from python.org."),
    "running": ("OIT 正在运行. 关闭 OIT 会断开 / 恢复网络, 请先在 OIT 里退出 (升级可以用设置里的\"检查更新\"), 再运行安装程序.",
                "OIT is running. Closing it toggles the network, so exit OIT first (to upgrade, use Check for updates in its settings), then run setup again."),
    "git": ("这个文件夹是 git 仓库 (开发版本), 不能装在这里.", "This folder is a git repository (a development checkout); cannot install here."),
    "step_check": ("检查 Python …", "Checking Python …"),
    "py_found": ("Python {v}: {p}", "Python {v}: {p}"),
    "py_missing": ("没有找到 Python {p}+ (带 tkinter), 从 python.org 下载 {v} …", "Python {p}+ (with tkinter) not found; downloading {v} from python.org …"),
    "py_download": ("下载 Python 安装包 {n}%", "Downloading the Python installer {n}%"),
    "py_sig": ("核对安装包的数字签名 …", "Verifying the installer's signature …"),
    "py_badsig": ("安装包的数字签名不是 Python Software Foundation, 已放弃.", "The installer is not signed by Python Software Foundation; aborted."),
    "py_install": ("安装 Python (几分钟) …", "Installing Python (a few minutes) …"),
    "py_fail": ("没能安装 Python ({e}). 请手动安装 Python {p}+ (勾选 tcl/tk) 后再运行安装程序.", "Could not install Python ({e}). Install Python {p}+ (with tcl/tk) yourself and run setup again."),
    "step_pip": ("安装可选依赖库 pywin32 …", "Installing optional library pywin32 …"),
    "token_fail": ("没能留下\"这是安装\"的令牌: 第一次启动时 OIT 会把这次安装当成源码被改过, 清掉数据! 请先备份数据文件夹, 或把这条日志发给开发者.",
                   "Could not leave the \"this is an install\" token: on first start OIT will treat this install as modified source and wipe its data! Back up the data folder first, or send this log to the developer."),
    "pip_fail": ("pywin32 没装上 (不影响使用).", "pywin32 was not installed (OIT works without it)."),
    "step_copy": ("复制程序文件到 {d} …", "Copying program files to {d} …"),
    "step_compile": ("预编译 …", "Precompiling …"),
    "step_reg": ("注册到系统 (设置 → 应用) …", "Registering with Windows (Settings → Apps) …"),
    "reg_fail": ("注册失败: 需要管理员权限.", "Registration failed: administrator rights are required."),
    "step_lnk": ("建快捷方式 …", "Creating shortcuts …"),
    "run_fail": ("没能自动打开 OIT ({e}); 用开始菜单 / 桌面上的 OIT 打开.", "Could not launch OIT automatically ({e}); use the Start menu / desktop shortcut."),
    "done": ("安装完成.", "Installation complete."),
    "failed": ("安装失败: {e}", "Installation failed: {e}"),
}


def lang() -> int:
    """0 = 中文, 1 = English (跟随系统界面语言)."""
    try:
        return 0 if (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0xFF) == 0x04 else 1
    except Exception:
        return 1


def t(key: str, **kw) -> str:
    s = TEXT[key][lang()]
    return s.format(**kw) if kw else s


class SetupError(Exception):
    pass


# ---------- 安装包里的程序文件 ----------

def tracked_files(root: str = HERE):
    """要装的程序文件 (git 跟踪的, 去掉安卓版和开发用的). 相对路径, 用 / 分隔."""
    r = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, timeout=60)
    if r.returncode != 0:
        raise SetupError("git ls-files failed")
    out = []
    for p in r.stdout.decode("utf-8").split("\0"):
        if not p or p.split("/")[0] in SKIP_TOP or p in SKIP_FILES or p.endswith((".pyc", ".pyo")):
            continue
        out.append(p)
    return out


def build_payload(zip_path: str, root: str = HERE) -> int:
    """把程序文件打成 payload.zip. 返回文件数."""
    files = tracked_files(root)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in files:
            z.write(os.path.join(root, p), p)
    return len(files)


def payload_path() -> str:
    base = getattr(sys, "_MEIPASS", None)
    return os.path.join(base, "payload.zip") if base else os.path.join(HERE, "payload.zip")


def payload_version(zip_path: str) -> str:
    with zipfile.ZipFile(zip_path) as z:
        text = z.read("config.py").decode("utf-8", "replace")
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else "?"


def extract(zip_path: str, dest: str, log=lambda s: None) -> int:
    """解压到 dest (覆盖同名文件, 不删多出来的). 返回文件数."""
    os.makedirs(dest, exist_ok=True)
    n = 0
    root = os.path.abspath(dest)
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            target = os.path.abspath(os.path.join(dest, info.filename))
            if os.path.commonpath([root, target]) != root:
                raise SetupError("unsafe path in the package: " + info.filename)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            n += 1
    return n


_TOKEN_CODE = ("import sys; sys.path.insert(0, sys.argv[1]); import srcguard; sys.exit(0 if srcguard.issue_token(sys.argv[1]) else 1)")


def issue_source_token(dest: str, python: str = "", run=subprocess.run) -> bool:
    """装好 / 修复之后: 用 DPAPI 封一个"现在这些程序文件是安装程序装的"的令牌, OIT 下次启动时据此知道程序文件的变化是安装, 不是被人改的 (srcguard.py).
    1.0.374 的坑: setup.exe 是 PyInstaller 打的, 只带了它自己 import 的标准库, srcguard / continuity 需要的 secrets / hmac 不在里面,
    在 exe 里 import 失败, 异常被吞掉, 令牌没发出去 → OIT 以为源码被改了, 清了数据. 所以优先让装好的那个 Python (有完整标准库) 来发令牌; 发不出去才在本进程里试, 都不行返回 False (调用方要告诉用户)."""
    if python:
        try:
            r = run([python, "-c", _TOKEN_CODE, dest], capture_output=True, timeout=120, creationflags=NO_WINDOW)
            if r.returncode == 0:
                return True
        except Exception:
            pass
    sys.path.insert(0, dest)
    try:
        import importlib
        return bool(importlib.import_module("srcguard").issue_token(dest))
    except Exception:
        return False
    finally:
        try:
            sys.path.remove(dest)
        except ValueError:
            pass
        for m in ("srcguard", "continuity"):
            sys.modules.pop(m, None)


def dir_size_kb(path: str) -> int:
    total = 0
    for r, _d, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return total // 1024


# ---------- Python ----------

def python_info(python: str, run=subprocess.run):
    """能用就返回 (主版本, 次版本, 真实路径), 否则 None (版本太低 / 没有 tkinter / 是个 Microsoft Store 的空壳 ……)."""
    if not python or not os.path.isfile(python):
        return None
    try:
        r = run([python, "-c", PYTHON_CHECK], capture_output=True, timeout=30, creationflags=NO_WINDOW)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    m = re.match(r"(\d+) (\d+) (.+)", (r.stdout or b"").decode("utf-8", "replace").strip())
    if not m or (int(m.group(1)), int(m.group(2))) < MIN_PYTHON:
        return None
    return int(m.group(1)), int(m.group(2)), m.group(3).strip()


def python_candidates():
    """可能的 python.exe: 注册表 (PEP 514, 所有用户 + 当前用户), py 启动器, PATH."""
    out = []
    if sys.platform == "win32":
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(root, r"Software\Python\PythonCore", 0, winreg.KEY_READ | view) as core:
                        i = 0
                        while True:
                            try:
                                ver = winreg.EnumKey(core, i)
                            except OSError:
                                break
                            i += 1
                            try:
                                with winreg.OpenKey(core, ver + r"\InstallPath", 0, winreg.KEY_READ | view) as k:
                                    try:
                                        exe = winreg.QueryValueEx(k, "ExecutablePath")[0]
                                    except OSError:
                                        exe = os.path.join(winreg.QueryValueEx(k, "")[0], "python.exe")
                                    out.append(exe)
                            except OSError:
                                pass
                except OSError:
                    pass
        try:
            r = subprocess.run(["py", "-0p"], capture_output=True, timeout=20, creationflags=NO_WINDOW)
            for line in (r.stdout or b"").decode("utf-8", "replace").splitlines():
                m = re.search(r"([A-Za-z]:\\.*?python\.exe)\s*$", line.strip(), re.I)
                if m:
                    out.append(m.group(1))
        except Exception:
            pass
    w = shutil.which("python")
    if w:
        out.append(w)
    seen, res = set(), []
    for p in out:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            res.append(p)
    return res


def find_python(candidates=None, run=subprocess.run):
    """最合适的 Python: (python.exe, (主, 次)), 没有 None. 版本高的优先; 要求同目录有 pythonw.exe."""
    best = None
    for c in (python_candidates() if candidates is None else candidates):
        info = python_info(c, run)
        if not info:
            continue
        exe = info[2]
        if not os.path.exists(os.path.join(os.path.dirname(exe), "pythonw.exe")) and not os.path.exists(os.path.join(os.path.dirname(c), "pythonw.exe")):
            continue
        if best is None or info[:2] > best[1]:
            best = (registration.launchable_python(c if os.path.isfile(c) else exe), info[:2])
    return best


def download(url: str, dest: str, progress=lambda n: None):
    req = urllib.request.Request(url, headers={"User-Agent": "OIT-setup"})
    total = done = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            done += len(chunk)
            if done > MAX_PYTHON_DOWNLOAD:
                raise SetupError("download too large")
            f.write(chunk)
            if total:
                progress(done * 100 // total)


def signature_ok(path: str, run=subprocess.run) -> bool:
    """安装包的 Authenticode 签名有效, 而且签名人是 Python Software Foundation."""
    script = ("$s = Get-AuthenticodeSignature -LiteralPath '" + path.replace("'", "''") + "'; "
              "[Console]::OutputEncoding=[Text.Encoding]::UTF8; \"$($s.Status)|$($s.SignerCertificate.Subject)\"")
    r = run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=60, creationflags=NO_WINDOW)
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    return out.startswith("Valid|") and PYTHON_SIGNER in out


def install_python(log, progress, tmp: str) -> None:
    os.makedirs(tmp, exist_ok=True)
    exe = os.path.join(tmp, f"python-{PYTHON_VERSION}-amd64.exe")
    download(PYTHON_URL, exe, progress)
    log(t("py_sig"))
    if not signature_ok(exe):
        os.remove(exe)
        raise SetupError(t("py_badsig"))
    log(t("py_install"))
    r = subprocess.run([exe, "/quiet", "InstallAllUsers=1", "PrependPath=0", "Include_launcher=0", "Include_test=0",
                        "Include_doc=0", "Shortcuts=0", "Include_tcltk=1", "Include_pip=1", "AssociateFiles=0"],
                       timeout=1800, creationflags=NO_WINDOW)
    if r.returncode not in (0, 3010):
        raise SetupError(f"exit code {r.returncode}")


def ensure_python(opts: dict, log, progress, tmp: str):
    """(python.exe, 是不是这次装的)."""
    log(t("step_check"))
    found = find_python()
    if found:
        log(t("py_found", v="%d.%d" % found[1], p=found[0]))
        return found[0], False
    if not opts.get("python", True):
        raise SetupError(t("py_fail", e="--nopython", p="%d.%d" % MIN_PYTHON))
    log(t("py_missing", p="%d.%d" % MIN_PYTHON, v=PYTHON_VERSION))
    try:
        install_python(log, progress, tmp)
    except SetupError as e:
        raise SetupError(t("py_fail", e=e, p="%d.%d" % MIN_PYTHON))
    except Exception as e:
        raise SetupError(t("py_fail", e=f"{type(e).__name__}: {e}", p="%d.%d" % MIN_PYTHON))
    found = find_python()
    if not found:
        raise SetupError(t("py_fail", e="not found after installing", p="%d.%d" % MIN_PYTHON))
    log(t("py_found", v="%d.%d" % found[1], p=found[0]))
    return found[0], True


# ---------- 注册 / 快捷方式 ----------

def oit_running() -> bool:
    try:
        import instance
        return instance.exists()
    except Exception:
        return False


def make_shortcut(lnk: str, python: str, program_dir: str) -> bool:
    import shortcut
    os.makedirs(os.path.dirname(lnk), exist_ok=True)
    ico = os.path.join(program_dir, "oit.ico")
    script = shortcut.build_script(lnk, registration.pythonw_of(python), f'"{os.path.join(program_dir, "main.py")}"',
                                   program_dir, APP_NAME, ico if os.path.exists(ico) else "")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=60, creationflags=NO_WINDOW)
    return os.path.exists(lnk)


def launch_oit(python: str, program_dir: str):
    subprocess.Popen([registration.pythonw_of(python), os.path.join(program_dir, "main.py")], cwd=program_dir,
                     close_fds=True, creationflags=NO_WINDOW | 0x00000008)


# ---------- 安装 ----------

def install(opts: dict, log=print, progress=lambda n: None, zip_path: str = None) -> dict:
    """opts: dir, desktop, start, pip, python, run. 返回 install.json 的内容. 失败抛 SetupError."""
    dest = os.path.abspath(opts.get("dir") or DEFAULT_DIR)
    zip_path = zip_path or payload_path()
    if oit_running():
        raise SetupError(t("running"))
    if os.path.isdir(os.path.join(dest, ".git")):
        raise SetupError(t("git"))
    version = payload_version(zip_path)
    tmp = os.path.join(tempfile.gettempdir(), "oit-setup")
    try:
        python, installed_python = ensure_python(opts, log, progress, tmp) if opts.get("python_exe") is None else (opts["python_exe"], False)
        if opts.get("pip", True) and opts.get("python_exe") is None:
            log(t("step_pip"))
            try:
                r = subprocess.run([python, "-m", "pip", "install", "--disable-pip-version-check", "pywin32"],
                                   capture_output=True, timeout=600, creationflags=NO_WINDOW)
                if r.returncode != 0:
                    log(t("pip_fail"))
            except Exception:
                log(t("pip_fail"))
        log(t("step_copy", d=dest))
        n = extract(zip_path, dest, log)
        log(f"{n} files")
        if not issue_source_token(dest, python):
            log(t("token_fail"))
        if opts.get("compile", True):
            log(t("step_compile"))
            try:
                subprocess.run([python, "-m", "compileall", "-q", dest], capture_output=True, timeout=300, creationflags=NO_WINDOW)
            except Exception:
                pass
        links = []
        if opts.get("desktop") or opts.get("start"):
            log(t("step_lnk"))
            import shortcut
            for want, folder in ((opts.get("desktop"), shortcut.desktop_dir), (opts.get("start"), shortcut.start_menu_dir)):
                if want:
                    lnk = os.path.join(folder(), shortcut.SHORTCUT_NAME)
                    if make_shortcut(lnk, python, dest):
                        links.append(lnk)
        size = dir_size_kb(dest)
        marker = {"version": version, "python": python, "installed_python": installed_python, "shortcuts": links,
                  "size_kb": size, "installed_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        registration.write_marker(dest, marker)
        if opts.get("register", True):
            log(t("step_reg"))
            if not registration.register(dest, version, python, size):
                raise SetupError(t("reg_fail"))
        if opts.get("run"):
            try:
                launch_oit(python, dest)
            except OSError as e:               # 打不开不算安装失败: 已经装好了
                log(t("run_fail", e=e))
        log(t("done"))
        return marker
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 命令行 / 界面 ----------

def parse_args(argv) -> dict:
    o = {"silent": False, "dir": None, "desktop": True, "start": True, "pip": True, "python": True, "run": False}
    for a in argv:
        low = a.lower()
        if low in ("/s", "/silent", "/quiet", "-s"):
            o["silent"] = True
        elif low.startswith("/d="):
            o["dir"] = a[3:].strip('"')
        elif low == "/nodesktop":
            o["desktop"] = False
        elif low == "/nostart":
            o["start"] = False
        elif low == "/nopip":
            o["pip"] = False
        elif low == "/nopython":
            o["python"] = False
        elif low == "/run":
            o["run"] = True
    return o


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate_self():
    """没打包成 exe 直接 python setup.py 运行时: 以管理员身份重新打开 (打包后的 exe 清单里已经要求管理员)."""
    exe = sys.executable
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, " ".join(f'"{a}"' for a in sys.argv), None, 1)


def run_silent(opts: dict) -> int:
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        def log(s):
            f.write(s + "\n")
            f.flush()
        try:
            install(opts, log)
            return 0
        except Exception as e:
            log(t("failed", e=e))
            return 1


def run_gui(opts: dict):
    import tkinter as tk
    from tkinter import filedialog, ttk
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    root.title(t("title"))
    try:
        root.iconbitmap(default=os.path.join(getattr(sys, "_MEIPASS", HERE), "oit.ico"))
    except Exception:
        pass
    root.geometry("640x470")
    root.resizable(False, False)
    q = queue.Queue()
    try:
        version = payload_version(payload_path())
    except Exception:
        version = "?"
    pad = {"padx": 16}
    ttk.Label(root, text=t("intro", v=version, p="%d.%d" % MIN_PYTHON), justify="left", wraplength=600).pack(anchor="w", pady=(14, 8), **pad)
    row = ttk.Frame(root)
    row.pack(fill="x", pady=(0, 6), **pad)
    ttk.Label(row, text=t("dir")).pack(side="left")
    var_dir = tk.StringVar(value=opts["dir"] or DEFAULT_DIR)
    ttk.Entry(row, textvariable=var_dir, width=52).pack(side="left", padx=8)
    ttk.Button(row, text=t("browse"), command=lambda: var_dir.set(os.path.normpath(filedialog.askdirectory(initialdir=var_dir.get()) or var_dir.get()))).pack(side="left")
    v_desktop, v_start = tk.BooleanVar(value=opts["desktop"]), tk.BooleanVar(value=opts["start"])
    v_pip, v_run = tk.BooleanVar(value=opts["pip"]), tk.BooleanVar(value=True)
    for text, var in ((t("desktop"), v_desktop), (t("start"), v_start), (t("pywin32"), v_pip), (t("run"), v_run)):
        ttk.Checkbutton(root, text=text, variable=var).pack(anchor="w", **pad)
    bar = ttk.Progressbar(root, mode="indeterminate", length=600)
    bar.pack(pady=(10, 4), **pad)
    txt = tk.Text(root, height=10, state="disabled", wrap="word", relief="flat", bg="#f4f4f4")
    txt.pack(fill="both", expand=True, pady=(0, 6), **pad)
    btns = ttk.Frame(root)
    btns.pack(fill="x", pady=(0, 12), **pad)
    go = ttk.Button(btns, text=t("install"))
    go.pack(side="right")
    ttk.Button(btns, text=t("cancel"), command=root.destroy).pack(side="right", padx=8)

    def append(s):
        txt.configure(state="normal")
        txt.insert("end", s + "\n")
        txt.see("end")
        txt.configure(state="disabled")

    def pump():
        try:
            while True:
                kind, val = q.get_nowait()
                if kind == "log":
                    append(val)
                elif kind == "done":
                    bar.stop()
                    bar.configure(mode="determinate", value=100 if val else 0)
                    go.configure(text=t("close"), state="normal", command=root.destroy)
        except queue.Empty:
            pass
        root.after(100, pump)

    def start():
        go.configure(state="disabled")
        bar.start(12)
        o = dict(opts, dir=var_dir.get(), desktop=v_desktop.get(), start=v_start.get(), pip=v_pip.get(), run=v_run.get())
        last = {"n": -1}

        def log(s):
            q.put(("log", s))

        def progress(n):
            if n // 10 != last["n"]:
                last["n"] = n // 10
                q.put(("log", t("py_download", n=n)))

        def work():
            ok = True
            try:
                install(o, log, progress)
            except Exception as e:
                ok = False
                log(t("failed", e=e))
            q.put(("done", ok))
        threading.Thread(target=work, daemon=True).start()

    go.configure(command=start)
    pump()
    root.mainloop()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        print(_selftest())
        return 0
    if "--payload" in argv:
        print(build_payload(argv[argv.index("--payload") + 1]), "files")
        return 0
    opts = parse_args(argv)
    if sys.platform == "win32" and not is_admin():
        if opts["silent"]:
            return 1
        elevate_self()
        return 0
    if opts["silent"]:
        return run_silent(opts)
    run_gui(opts)
    return 0


# ---------- 自检 ----------

def _selftest():
    real_lad = os.environ.get("LOCALAPPDATA")
    global oit_running
    real_running = oit_running
    d = tempfile.mkdtemp()
    try:
        files = tracked_files()
        assert "main.py" in files and "app.py" in files and "uninstall.py" in files and "registration.py" in files
        assert not any(f.startswith("Android version/") or f in ("setup.py", ".gitignore") for f in files)
        assert not any(f in ("config_local.py", "release.py", "release_token.txt") for f in files)
        z = os.path.join(d, "payload.zip")
        assert build_payload(z) == len(files)
        v = payload_version(z)
        assert re.match(r"\d+\.\d+\.\d+$", v), v
        # 解压: 文件都在, 路径穿越被拒绝
        dest = os.path.join(d, "prog")
        assert extract(z, dest) == len(files)
        assert os.path.isfile(os.path.join(dest, "main.py")) and os.path.isfile(os.path.join(dest, "config.py"))
        bad = os.path.join(d, "bad.zip")
        with zipfile.ZipFile(bad, "w") as zz:
            zz.writestr("../evil.txt", "x")
        try:
            extract(bad, os.path.join(d, "prog2"))
            raise AssertionError("路径穿越应该被拒绝")
        except SetupError:
            pass
        # 命令行
        o = parse_args(["/S", "/D=C:\\x y\\OIT", "/nodesktop", "/nopip", "/run"])
        assert o["silent"] and o["dir"] == "C:\\x y\\OIT" and not o["desktop"] and o["start"] and not o["pip"] and o["run"]
        # Python 检测: 当前解释器可用; 假的不可用
        found = find_python([sys.executable])
        assert found is None or found[1] >= MIN_PYTHON
        assert python_info(os.path.join(d, "nope.exe")) is None
        fake_py = os.path.join(d, "python.exe")
        open(fake_py, "w").close()

        def run_old(*a, **k):
            return subprocess.CompletedProcess(a, 0, b"3 8 C:\\Py\\python.exe\n", b"")

        def run_ok(*a, **k):
            return subprocess.CompletedProcess(a, 0, b"3 12 " + fake_py.encode() + b"\n", b"")

        def run_stub(*a, **k):
            return subprocess.CompletedProcess(a, 9009, b"", b"Python was not found")
        assert python_info(fake_py, run_old) is None and python_info(fake_py, run_stub) is None
        assert python_info(fake_py, run_ok) == (3, 12, fake_py)
        open(os.path.join(d, "pythonw.exe"), "w").close()
        assert find_python([fake_py], run_ok) == (fake_py, (3, 12))
        # 签名检查: 只认有效 + Python Software Foundation
        sig = lambda s: (lambda *a, **k: subprocess.CompletedProcess(a, 0, s.encode(), b""))
        assert signature_ok("x.exe", sig("Valid|CN=Python Software Foundation, O=Python Software Foundation, C=US"))
        assert not signature_ok("x.exe", sig("Valid|CN=Somebody Else"))
        assert not signature_ok("x.exe", sig("NotSigned|"))
        # 整个安装流程 (不注册表, 不快捷方式, 不装 Python / pip, 不预编译)
        oit_running = lambda: False        # 本机真有 OIT 在运行也不影响自检
        os.environ["LOCALAPPDATA"] = os.path.join(d, "LocalAppData")      # 令牌写在这里, 不碰真正的
        prog = os.path.join(d, "Program Files", "OIT")
        m = install({"dir": prog, "python_exe": fake_py, "register": False, "compile": False, "pip": False, "desktop": False, "start": False},
                    lambda s: None, zip_path=z)
        assert m["version"] == v and m["python"] == fake_py and registration.read_marker(prog)["version"] == v
        assert os.path.isfile(os.path.join(prog, "main.py"))
        # 令牌: 用完整的 Python 发 (1.0.374: exe 里缺 secrets / hmac, 在 exe 里发不出, OIT 把安装当成源码被改, 清了数据), 而且发出来的令牌 srcguard 认得
        tok = os.path.join(d, "LocalAppData", "Obliphur", "OIT", "src_update.dat")
        assert issue_source_token(prog, sys.executable) and os.path.isfile(tok)
        os.remove(tok)
        assert not issue_source_token(prog, os.path.join(d, "no-such-python.exe"), run=lambda *a, **k: (_ for _ in ()).throw(OSError("x"))) or os.path.isfile(tok)
        # 再装一遍 = 升级: 不报错
        install({"dir": prog, "python_exe": fake_py, "register": False, "compile": False, "pip": False}, lambda s: None, zip_path=z)
        # git 仓库不能装
        os.makedirs(os.path.join(d, "repo", ".git"))
        try:
            install({"dir": os.path.join(d, "repo"), "python_exe": fake_py, "register": False}, lambda s: None, zip_path=z)
            raise AssertionError("git 仓库应该被拒绝")
        except SetupError:
            pass
        oit_running = lambda: True
        try:
            install({"dir": prog, "python_exe": fake_py, "register": False}, lambda s: None, zip_path=z)
            raise AssertionError("OIT 在运行时不能装")
        except SetupError:
            pass
        assert lang() in (0, 1) and t("done") and t("token_fail")
    finally:
        if real_lad is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = real_lad
        oit_running = real_running
        shutil.rmtree(d, ignore_errors=True)
    return "setup selftest OK"


if __name__ == "__main__":
    sys.exit(main())
