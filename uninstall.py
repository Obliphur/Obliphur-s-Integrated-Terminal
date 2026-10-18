"""
OIT 卸载程序
────────────
双击 uninstall.bat (或 python uninstall.py) 运行. 不依赖主程序能否正常打开.

按顺序:
  1. 结束正在运行的 OIT —— 否则它会马上把网断回去 (关闭程序本身就会断网, 见 NetGuard.shutdown)
  2. 还原系统代理: 按 sysproxy_backup.json 还原; 备份丢了但代理仍指向 OIT 时直接关掉
  3. 重新启用被禁用的物理网卡 (和 NetGuard.ensure_online 一样: Get-NetAdapter -Physical)
  4. 删除数据目录, 旧版本 mrsTimer 目录, 旧版本的卸载次数文件 (和 Reset 页的范围相同),
     以及设备同步加的两条防火墙规则 (firewall.py)
  5. (可选, 默认不勾) 删除程序文件夹本身: 卸载程序退出后由一个后台 PowerShell 删除

1 ~ 4 一定会做: 和 Reset 一样, 立即恢复网络的代价是全部数据. 没有"只恢复网络, 保留数据"的选项.

    python uninstall.py --selftest      自检 (不碰真实的注册表 / 网卡 / 进程)
"""

import os
import sys
import json
import time
import queue
import base64
import shutil
import threading
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import config
from config import APP_NAME, OLD_APP_DIR
import i18n
from i18n import tr
from netproxy.sysproxy import SystemProxy, parse_proxy_server
from netproxy.server import DEFAULT_PORT
import firewall


CONFIRM_WORD = "UNINSTALL"
OUR_PORTS = range(DEFAULT_PORT, DEFAULT_PORT + 21)     # FilterProxy.start 端口被占用时最多往后找 20 个
NO_WINDOW = 0x08000000
DETACHED = 0x00000008 | 0x00000200                     # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
# 确认是 OIT 的程序文件夹才允许整个删掉
PROGRAM_MARKERS = ("main.py", "app.py", "net_guard.py", "uninstall.py")


# ───────────────────────── 路径 ─────────────────────────

class Paths:
    def __init__(self, data_dir: str = None, program_dir: str = HERE):
        self.data_dir = data_dir or config.app_data_dir()
        low = os.path.dirname(self.data_dir)
        self.old_dir = os.path.join(low, OLD_APP_DIR)
        self.legacy_file = os.path.join(low, f"{APP_NAME}.uninstall.json")
        self.backup_file = os.path.join(self.data_dir, "sysproxy_backup.json")
        self.program_dir = os.path.abspath(program_dir)
        self.main_py = os.path.join(self.program_dir, "main.py")

    def data_targets(self):
        """要删除的数据: (路径, 是否目录). 只删名字对得上的, 防止环境变量异常时删错地方."""
        out = []
        if os.path.basename(self.data_dir) == APP_NAME:
            out.append((self.data_dir, True))
        if os.path.basename(self.old_dir) == OLD_APP_DIR:
            out.append((self.old_dir, True))
        out.append((self.legacy_file, False))
        return out


def dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} MB"


def looks_like_program_dir(path: str) -> bool:
    path = os.path.abspath(path)
    if os.path.dirname(path) == path:                  # 盘符根目录
        return False
    home = os.path.abspath(os.path.expanduser("~"))
    if os.path.normcase(path) == os.path.normcase(home):
        return False
    return all(os.path.isfile(os.path.join(path, m)) for m in PROGRAM_MARKERS)


def is_git_repo(path: str) -> bool:
    return os.path.exists(os.path.join(path, ".git"))


# ───────────────────────── PowerShell ─────────────────────────

def ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def powershell(cmd: str, timeout: float = 60):
    """运行一段 PowerShell, 返回 (退出码, 输出). 非 Windows 什么都不做.
    命令里不用双引号, 避免命令行转义的问题."""
    if sys.platform != "win32":
        return 0, ""
    full = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; " + cmd
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", full],
            capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        return r.returncode, (out + err).strip()
    except Exception as e:
        return -1, str(e)


def _lines(text: str):
    return [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]


# ───────────────────────── 1. 进程 ─────────────────────────

def find_oit_processes(paths: Paths, ps=powershell, own_pid: int = None):
    """[(pid, 名字)]. 两种认法:
    · 命令行里带着这个文件夹的 main.py (elevate / relaunch 都用绝对路径启动);
    · 监听着 OIT 过滤代理端口的 python 进程 (proxy 模式下, 不管它是怎么启动的)."""
    own_pid = os.getpid() if own_pid is None else own_pid
    lo, hi = OUR_PORTS[0], OUR_PORTS[-1]
    cmd = (
        f"$n = {ps_quote(paths.main_py.lower())}; "
        "$ids = @(Get-CimInstance Win32_Process -Filter 'Name like ''py%''' | "
        "Where-Object { $_.CommandLine -and $_.CommandLine.ToLower().Contains($n) } | "
        "ForEach-Object { $_.ProcessId }); "
        f"$ids += @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.LocalPort -ge {lo} -and $_.LocalPort -le {hi} }} | "
        "ForEach-Object { $_.OwningProcess }); "
        "$ids | Sort-Object -Unique | ForEach-Object { "
        "$p = Get-Process -Id $_ -ErrorAction SilentlyContinue; "
        "if ($p -and $p.ProcessName -like 'py*') { [string]$_ + ' ' + $p.ProcessName } }"
    )
    _rc, out = ps(cmd)
    found = []
    for ln in _lines(out):
        pid, _, name = ln.partition(" ")
        if pid.isdigit() and int(pid) != own_pid:
            found.append((int(pid), name.strip() or "python"))
    return found


def kill_processes(procs, run=None):
    """连同子进程 (正在执行的 PowerShell 断网命令) 一起结束. 返回没能结束的 pid."""
    if run is None:
        def run(args):
            if sys.platform != "win32":
                return 0
            try:
                return subprocess.run(args, capture_output=True, creationflags=NO_WINDOW,
                                      timeout=30).returncode
            except Exception:
                return -1
    failed = []
    for pid, _name in procs:
        if run(["taskkill", "/PID", str(pid), "/T", "/F"]) not in (0, 128):
            failed.append(pid)
    return failed


# ───────────────────────── 2. 系统代理 ─────────────────────────

def is_our_proxy(server) -> bool:
    hp = parse_proxy_server(server)
    return bool(hp) and hp[0] in ("127.0.0.1", "localhost") and hp[1] in OUR_PORTS


def proxy_points_to_oit(state: dict) -> bool:
    return state.get("ProxyEnable") == 1 and is_our_proxy(state.get("ProxyServer"))


def restore_proxy(paths: Paths, backend=None):
    """返回 (结果, 说明). 结果: "restored" 按备份还原 / "disabled" 直接关掉 / "ok" 本来就没问题."""
    sp = SystemProxy(paths.backup_file, backend)
    before = sp.backend.read()
    had_backup = os.path.exists(paths.backup_file)
    if is_our_proxy(before.get("ProxyServer")):
        sp.ours = before.get("ProxyServer")       # 备份丢了时 restore() 靠它认出是我们的设置
    sp.restore()
    after = sp.backend.read()
    if proxy_points_to_oit(after):
        # 备份里的「原设置」本身就是 OIT 的旧端口之类: 不能还原成它, 直接关掉
        sp.backend.write({"ProxyEnable": 0, "ProxyServer": None,
                          "ProxyOverride": None, "AutoConfigURL": None})
        sp.backend.notify()
        after = sp.backend.read()
        return "disabled", after
    if had_backup:
        return "restored", after
    if proxy_points_to_oit(before):
        return "disabled", after
    return "ok", after


def describe_proxy(state: dict) -> str:
    if state.get("AutoConfigURL"):
        return tr("PAC 脚本 {url}", url=state.get("AutoConfigURL"))
    if state.get("ProxyEnable") == 1 and state.get("ProxyServer"):
        return str(state.get("ProxyServer"))
    return tr("未使用代理")


# ───────────────────────── 3. 网卡 ─────────────────────────

def disabled_adapters(ps=powershell):
    _rc, out = ps("Get-NetAdapter -Physical | Where-Object { $_.Status -eq 'Disabled' } | "
                  "ForEach-Object { $_.Name }")
    return _lines(out)


def enable_adapters(ps=powershell):
    """启用全部被禁用的物理网卡. 返回 (启用了的, 仍然是禁用的)."""
    before = disabled_adapters(ps)
    if before:
        ps("Get-NetAdapter -Physical | Where-Object { $_.Status -eq 'Disabled' } | "
           "Enable-NetAdapter -Confirm:$false")
    still = disabled_adapters(ps) if before else []
    return [a for a in before if a not in still], still


# ───────────────────────── 4. 数据 ─────────────────────────

def delete_data(paths: Paths, tries: int = 6):
    """返回删不掉的路径. 刚结束的进程可能还占着文件, 所以多试几次."""
    left = []
    for path, is_dir in paths.data_targets():
        for i in range(tries):
            if not os.path.exists(path):
                break
            try:
                if is_dir and sys.version_info >= (3, 12):
                    shutil.rmtree(path, onexc=_force_remove)
                elif is_dir:
                    shutil.rmtree(path, onerror=_force_remove)
                else:
                    os.remove(path)
            except OSError:
                pass
            if os.path.exists(path):
                time.sleep(0.5)
        if os.path.exists(path):
            left.append(path)
    return left


def _force_remove(func, path, _exc):
    import stat
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


# ───────────────────────── 5. 程序文件夹 ─────────────────────────

def folder_removal_script(folder: str, pid: int) -> str:
    """等卸载程序 (pid) 退出后删除整个文件夹. 只读文件 (.git 里很多) 用 -Force 一并删."""
    return (
        f"$p = {int(pid)}; $t = 0; "
        "while ((Get-Process -Id $p -ErrorAction SilentlyContinue) -and $t -lt 120) "
        "{ Start-Sleep -Milliseconds 500; $t++ }; "
        "Start-Sleep -Seconds 1; "
        f"for ($i = 0; $i -lt 5; $i++) {{ "
        f"Remove-Item -LiteralPath {ps_quote(folder)} -Recurse -Force -ErrorAction SilentlyContinue; "
        f"if (-not (Test-Path -LiteralPath {ps_quote(folder)})) {{ break }}; "
        "Start-Sleep -Seconds 2 }"
    )


def schedule_folder_removal(folder: str, pid: int = None, spawn=None) -> bool:
    folder = os.path.abspath(folder)
    if not looks_like_program_dir(folder):
        return False
    script = folder_removal_script(folder, os.getpid() if pid is None else pid)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    args = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden", "-EncodedCommand", encoded]
    if spawn is not None:
        spawn(args)
        return True
    if sys.platform != "win32":
        return False
    try:
        subprocess.Popen(args, cwd=os.environ.get("TEMP") or os.path.expanduser("~"),
                         creationflags=DETACHED | NO_WINDOW, close_fds=True)
        return True
    except OSError:
        return False


# ───────────────────────── 检测 / 执行 ─────────────────────────

def detect_state(paths: Paths, ps=powershell, backend=None) -> dict:
    sp = SystemProxy(paths.backup_file, backend)
    proxy = sp.backend.read()
    return {
        "procs": find_oit_processes(paths, ps),
        "proxy": proxy,
        "proxy_oit": proxy_points_to_oit(proxy),
        "backup": os.path.exists(paths.backup_file),
        "adapters": disabled_adapters(ps),
        "data": os.path.isdir(paths.data_dir),
        "data_size": dir_size(paths.data_dir) if os.path.isdir(paths.data_dir) else 0,
        "old": os.path.isdir(paths.old_dir) or os.path.isfile(paths.legacy_file),
        "git": is_git_repo(paths.program_dir),
    }


def _wipe_and_unpair(my_id: str, peer: dict) -> bool:
    """请一台绑定的设备清空 OIT 的数据, 并解除和本机的绑定 (MECHANICS 9.1); 对方是不认识清空请求的旧版本时,
    退回旧的做法: 只让它解除绑定, 删掉它保留的本机数据."""
    import devsync
    st = devsync.send_wipe_to(my_id, peer, devsync.SyncService.new_wipe_id(), True)
    if st == 404:
        return devsync.send_forget(my_id, peer)
    return st == 200


def forget_peers(paths: Paths, send=None) -> int:
    """读 sync_state.json, 请每台绑定的设备清空数据并解除和本机的绑定. 返回成功通知了几台."""
    try:
        with open(os.path.join(paths.data_dir, "sync_state.json"), "r", encoding="utf-8") as f:
            st = json.load(f)
        my_id = st["device"]["id"]
        peers = [p for p in (st.get("peers") or {}).values() if isinstance(p, dict) and p.get("key")]
    except Exception:
        return 0
    if send is None:
        send = _wipe_and_unpair
    return sum(1 for p in peers if send(my_id, p))


def run_uninstall(paths: Paths, remove_program: bool, log,
                  ps=powershell, backend=None, kill_run=None, spawn=None, fw_run=None, forget=None) -> bool:
    """按顺序执行; log(文本, 级别) 级别为 info / ok / warn. 返回网络是否已完全恢复."""
    ok = True

    log(tr("① 结束正在运行的 OIT…"), "info")
    procs = find_oit_processes(paths, ps)
    if procs:
        failed = kill_processes(procs, kill_run)
        for pid, name in procs:
            if pid in failed:
                ok = False
                log(tr("   结束失败: {name} (PID {pid})", name=name, pid=pid), "warn")
            else:
                log(tr("   已结束: {name} (PID {pid})", name=name, pid=pid), "ok")
        time.sleep(1.0 if kill_run is None else 0)
    else:
        log(tr("   没有正在运行的 OIT."), "ok")

    log(tr("② 还原系统代理…"), "info")
    try:
        result, state = restore_proxy(paths, backend)
        msg = {"restored": tr("   已按备份还原: {s}"),
               "disabled": tr("   代理指向 OIT 但没有可用的备份,已关闭代理: {s}"),
               "ok": tr("   系统代理没有被 OIT 占用: {s}")}[result]
        log(msg.format(s=describe_proxy(state)), "ok")
        if proxy_points_to_oit(state):
            ok = False
            log(tr("   系统代理仍指向 OIT."), "warn")
    except Exception as e:
        ok = False
        log(tr("   还原失败: {e}", e=e), "warn")

    log(tr("③ 启用物理网卡…"), "info")
    enabled, still = enable_adapters(ps)
    for name in enabled:
        log(tr("   已启用: {name}", name=name), "ok")
    for name in still:
        ok = False
        log(tr("   启用失败: {name}", name=name), "warn")
    if not enabled and not still:
        log(tr("   没有被禁用的物理网卡."), "ok")

    log(tr("④ 删除数据…"), "info")
    # 先请绑定的设备清空数据, 并解除和本机的绑定 (网络这时已经恢复)
    told = forget_peers(paths, forget)
    if told:
        log(tr("   已请 {n} 台绑定的设备清空数据并解除绑定.", n=told), "ok")
    left = delete_data(paths)
    for p in left:
        log(tr("   删不掉 (可能被占用): {p}", p=p), "warn")
    if not left:
        log(tr("   数据已全部删除."), "ok")
    # 设备同步的防火墙规则 (firewall.py) 也是 OIT 留下的
    fw_left = firewall.remove(fw_run) if fw_run else firewall.remove()
    for name in fw_left:
        log(tr("   防火墙规则删不掉: {name}", name=name), "warn")

    if remove_program:
        log(tr("⑤ 删除程序文件夹…"), "info")
        if schedule_folder_removal(paths.program_dir, spawn=spawn):
            log(tr("   关闭本窗口后自动删除: {p}", p=paths.program_dir), "ok")
            log(tr("   如果文件夹没有完全消失,关掉所有打开着它的窗口 (资源管理器 / 终端 / 编辑器) 后手动删除."),
                "info")
        else:
            log(tr("   这里看起来不是 OIT 的程序文件夹,没有删除: {p}", p=paths.program_dir), "warn")

    log("", "info")
    if ok:
        log(tr("✔ 网络限制已全部解除."), "ok")
    else:
        log(tr("⚠ 有几项没能完成,见上方.可以再运行一次卸载程序."), "warn")
    return ok


# ───────────────────────── 界面 ─────────────────────────

def _dpi_aware():
    if sys.platform != "win32":
        return
    try:
        from ctypes import windll
    except Exception:
        return
    for f in (lambda: windll.user32.SetProcessDpiAwarenessContext(-4),
              lambda: windll.shcore.SetProcessDpiAwareness(2),
              lambda: windll.user32.SetProcessDPIAware()):
        try:
            f()
            return
        except Exception:
            continue


def _load_ui_settings():
    """沿用主程序的语言 / 主题 / 字号; 读不到就用默认值."""
    raw = config.load_json_file(config.settings_file(), {})
    eff = raw.get("effective") if isinstance(raw, dict) and isinstance(raw.get("effective"), dict) else {}
    i18n.set_language(eff.get("LANGUAGE") or config.LANGUAGE)
    config.apply_theme(eff.get("THEME"))
    try:
        scale = max(0.75, min(2.0, float(eff.get("FONT_SCALE") or 1.0)))
    except (TypeError, ValueError):
        scale = 1.0
    config.UI_SCALE = config.detect_ui_scale() * scale


class UninstallWindow:

    def __init__(self, root, paths: Paths):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root, self.paths = root, paths
        self.C = config.COLORS
        self.q = queue.Queue()
        self.busy = False
        self.done = False
        self.state = None

        root.title(tr("卸载 {app}", app=APP_NAME))
        root.configure(bg=self.C["bg"])
        w, h = config.scaled(640), config.scaled(720)
        try:
            w = min(w, int(root.winfo_screenwidth() * 0.96))
            h = min(h, int(root.winfo_screenheight() * 0.92) - 40)
        except Exception:
            pass
        root.geometry(f"{w}x{h}")
        root.minsize(config.scaled(560), config.scaled(560))
        self._styles()

        self.var_prog = tk.BooleanVar(value=False)
        self.var_confirm = tk.StringVar()
        self.var_confirm.trace_add("write", lambda *_: self._sync_button())
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        threading.Thread(target=self._inspect_worker, daemon=True).start()
        self._poll()

    # ---- 样式 ----
    def _styles(self):
        ttk, C, T = self.ttk, self.C, config.TTK_COLORS
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure(".", background=T["frame"], foreground=T["fg"], fieldbackground=T["field"],
                    bordercolor=T["border"], lightcolor=T["light"], darkcolor=T["dark"],
                    troughcolor=T["trough"], selectbackground=T["select"],
                    selectforeground=T["on_select"], font=config.font("body"))
        s.configure("TCheckbutton", background=C["bg"], foreground=C["text"], font=config.font("body"))
        s.map("TCheckbutton", background=[("active", C["bg"])],
              foreground=[("disabled", C["text_dim"])])
        s.configure("TEntry", fieldbackground=T["field"], foreground=T["fg"])
        dg, on = C["danger"], C["on_accent"]
        s.configure("Danger.TButton", background=dg, foreground=on, bordercolor=dg,
                    lightcolor=dg, darkcolor=dg, font=config.font("body", bold=True),
                    borderwidth=0, focusthickness=0, padding=(18, 9))
        s.map("Danger.TButton",
              background=[("disabled", C["danger_disabled"]), ("active", C["danger_hover"])],
              foreground=[("disabled", on)],
              bordercolor=[("disabled", C["danger_disabled"]), ("active", C["danger_hover"])],
              lightcolor=[("disabled", C["danger_disabled"]), ("active", C["danger_hover"])],
              darkcolor=[("disabled", C["danger_disabled"]), ("active", C["danger_hover"])])
        ac = C["accent"]
        s.configure("Accent.TButton", background=ac, foreground=on, bordercolor=ac,
                    lightcolor=ac, darkcolor=ac, font=config.font("body", bold=True),
                    borderwidth=0, focusthickness=0, padding=(18, 9))
        s.map("Accent.TButton", background=[("active", C["accent_hover"])],
              lightcolor=[("active", C["accent_hover"])], darkcolor=[("active", C["accent_hover"])])

    # ---- 布局 ----
    def _build(self):
        tk, ttk, C = self.tk, self.ttk, self.C
        bg = C["bg"]
        pad = config.scaled(24)
        wrap = config.scaled(570)

        hdr = tk.Frame(self.root, bg=bg)
        hdr.pack(fill="x", padx=pad, pady=(config.scaled(20), config.scaled(6)))
        tk.Label(hdr, text=tr("🗑  卸载"), bg=bg, fg=C["text"],
                 font=config.font("h1", bold=True)).pack(anchor="w")
        tk.Label(hdr, text=tr("解除网络限制,并清除本程序在这台电脑上留下的东西."), bg=bg,
                 fg=C["text_dim"], font=config.font("body"), wraplength=wrap,
                 justify="left").pack(anchor="w", pady=(4, 0))

        # 当前状态
        card = tk.Frame(self.root, bg=C["bg_card"], highlightbackground=C["border"],
                        highlightthickness=1)
        card.pack(fill="x", padx=pad, pady=(config.scaled(10), config.scaled(10)))
        tk.Label(card, text=tr("当前状态"), bg=C["bg_card"], fg=C["text"],
                 font=config.font("body", bold=True)).pack(anchor="w", padx=14, pady=(10, 2))
        self.lbl_state = tk.Label(card, text=tr("正在检测…"), bg=C["bg_card"], fg=C["text_dim"],
                                  font=config.font("small"), justify="left", anchor="w",
                                  wraplength=wrap)
        self.lbl_state.pack(fill="x", padx=14, pady=(0, 10))

        # 选项: 勾选框只放短标题, 说明另起一行 (ttk 勾选框不会自动换行)
        opts = tk.Frame(self.root, bg=bg)
        opts.pack(fill="x", padx=pad)
        indent = (config.scaled(26), 0)

        def option(text, var, detail, fg=None):
            cb = ttk.Checkbutton(opts, text=text, variable=var, command=self._sync_button)
            cb.pack(anchor="w", pady=(6, 0))
            lbl = tk.Label(opts, text=detail, bg=bg, fg=fg or C["text_dim"],
                           font=config.font("small"), justify="left", anchor="w",
                           wraplength=wrap - indent[0])
            lbl.pack(fill="x", padx=indent)
            return cb, lbl

        self.var_net = tk.BooleanVar(value=True)
        cb, _ = option(tr("解除网络限制 (必做)"), self.var_net,
                       tr("结束 OIT,还原系统代理,启用网卡."))
        cb.state(["disabled"])
        self.var_data = tk.BooleanVar(value=True)
        cb, _ = option(tr("删除全部数据 (必做)"), self.var_data,
                       tr("学习记录,整点记录,统计,名单,设置,AI 配置等.无法撤销."))
        cb.state(["disabled"])
        self.cb_prog, self.lbl_prog = option(tr("同时删除程序文件夹"), self.var_prog,
                                             self.paths.program_dir)
        if not looks_like_program_dir(self.paths.program_dir):
            self.var_prog.set(False)
            self.cb_prog.state(["disabled"])
            self.lbl_prog.configure(text=tr("(这里看起来不是 OIT 的程序文件夹,不能删除)"))

        # 确认
        form = tk.Frame(self.root, bg=bg)
        form.pack(fill="x", padx=pad, pady=(config.scaled(12), config.scaled(6)))
        row = tk.Frame(form, bg=bg)
        row.pack(anchor="w", pady=(0, 8))
        tk.Label(row, text=tr("输入 {w} 以确认", w=CONFIRM_WORD), bg=bg, fg=C["text"],
                 font=config.font("body")).pack(side="left", padx=(0, 8))
        self.entry = ttk.Entry(row, textvariable=self.var_confirm, width=16)
        self.entry.pack(side="left")
        self.btn = ttk.Button(form, text=tr("🗑  卸载"), style="Danger.TButton", command=self._on_go)
        self.btn.pack(anchor="w")
        self.btn.state(["disabled"])

        # 进度
        self.txt = tk.Text(self.root, height=10, relief="flat", wrap="word",
                           font=config.font("small", family=config.FONT_MONO),
                           bg=config.TTK_COLORS["list_bg"], fg=config.TTK_COLORS["list_fg"],
                           highlightthickness=1, highlightbackground=C["border"], bd=0,
                           padx=10, pady=8)
        self.txt.pack(fill="both", expand=True, padx=pad, pady=(config.scaled(8), pad))
        self.txt.tag_configure("ok", foreground=C["success"])
        self.txt.tag_configure("warn", foreground=C["danger"])
        self.txt.tag_configure("info", foreground=C["text"])
        self.txt.configure(state="disabled")

    # ---- 检测 ----
    def _inspect_worker(self):
        try:
            st = detect_state(self.paths)
        except Exception as e:
            st = {"error": str(e)}
        self.q.put(("state", st))

    def _show_state(self, st):
        self.state = st
        if "error" in st:
            self.lbl_state.configure(text=tr("检测失败: {e}", e=st["error"]))
            return
        lines = []
        if st["procs"]:
            names = ", ".join(f"{n} ({p})" for p, n in st["procs"])
            lines.append(tr("• 正在运行的 OIT: {s}", s=names))
        else:
            lines.append(tr("• 正在运行的 OIT: 无"))
        proxy = describe_proxy(st["proxy"])
        if st["proxy_oit"]:
            lines.append(tr("• 系统代理: {s} (指向 OIT)", s=proxy))
        else:
            lines.append(tr("• 系统代理: {s}", s=proxy))
        if st["backup"]:
            lines.append(tr("• 有 OIT 接管前的系统代理备份,卸载时按它还原"))
        if st["adapters"]:
            lines.append(tr("• 被禁用的物理网卡: {s}", s=", ".join(st["adapters"])))
        else:
            lines.append(tr("• 被禁用的物理网卡: 无"))
        if st["data"]:
            lines.append(tr("• 数据目录: {p} ({size})", p=self.paths.data_dir, size=fmt_size(st["data_size"])))
        else:
            lines.append(tr("• 数据目录: 不存在"))
        if st["old"]:
            lines.append(tr("• 还有旧版本留下的文件,删除数据时一并删除"))
        self.lbl_state.configure(text="\n".join(lines), fg=self.C["text"])
        if st["git"]:
            self.lbl_prog.configure(
                text=self.paths.program_dir + "\n" +
                tr("⚠ 这是一个 git 仓库: 没有推送的提交和修改会一起丢失."),
                fg=self.C["danger"])

    # ---- 执行 ----
    def _sync_button(self):
        if self.done:
            return
        ok = self.var_confirm.get().strip() == CONFIRM_WORD and not self.busy
        self.btn.state(["!disabled" if ok else "disabled"])

    def _on_go(self):
        if self.done:
            self.root.destroy()
            return
        if self.busy or self.var_confirm.get().strip() != CONFIRM_WORD:
            return
        self.busy = True
        self._sync_button()
        self.entry.state(["disabled"])
        prog = bool(self.var_prog.get())

        def log(text, level="info"):
            self.q.put(("log", (text, level)))

        def work():
            try:
                ok = run_uninstall(self.paths, prog, log)
            except Exception as e:
                log(tr("出错了: {e}", e=e), "warn")
                ok = False
            self.q.put(("done", ok))

        threading.Thread(target=work, daemon=True).start()

    def _append(self, text, level):
        self.txt.configure(state="normal")
        self.txt.insert("end", text + "\n", level)
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "state":
                    self._show_state(val)
                elif kind == "log":
                    self._append(*val)
                elif kind == "done":
                    self.busy = False
                    self.done = True
                    self.btn.configure(text=tr("关闭"), style="Accent.TButton")
                    self.btn.state(["!disabled"])
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _on_close(self):
        if self.busy:          # 进行到一半不要关: 网卡 / 代理可能停在中间状态
            return
        self.root.destroy()


def main():
    if "--selftest" in sys.argv:
        print(_selftest())
        return
    if sys.platform == "win32" and not config.is_admin():
        config.elevate()          # 启用网卡, 结束以管理员身份运行的 OIT 都需要管理员权限
        sys.exit(0)
    import tkinter as tk
    _dpi_aware()
    _load_ui_settings()
    root = tk.Tk()
    UninstallWindow(root, Paths())
    root.mainloop()


# ───────────────────────── 自检 ─────────────────────────

def _selftest():
    import tempfile
    from netproxy.sysproxy import MemoryBackend

    assert is_our_proxy("127.0.0.1:17890") and is_our_proxy("localhost:17910")
    assert not is_our_proxy("127.0.0.1:7890") and not is_our_proxy("127.0.0.1:17911")
    assert not is_our_proxy("10.0.0.1:17890") and not is_our_proxy("")
    assert ps_quote("a'b") == "'a''b'"

    tmp = tempfile.mkdtemp()
    low = os.path.join(tmp, "LocalLow")
    prog = os.path.join(tmp, "prog")
    os.makedirs(prog)
    for m in PROGRAM_MARKERS:
        open(os.path.join(prog, m), "w").close()

    def fresh():
        d = os.path.join(low, APP_NAME)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "study_log.json"), "w") as f:
            f.write("{}")
        os.makedirs(os.path.join(low, OLD_APP_DIR), exist_ok=True)
        open(os.path.join(low, f"{APP_NAME}.uninstall.json"), "w").close()
        return Paths(d, prog)

    # ── 代理: 按备份还原 (原来是 Clash)
    p = fresh()
    be = MemoryBackend({"ProxyEnable": 1, "ProxyServer": "127.0.0.1:7890", "ProxyOverride": "<local>"})
    SystemProxy(p.backup_file, be).install("127.0.0.1", 17890)
    assert proxy_points_to_oit(be.read())
    res, st = restore_proxy(p, be)
    assert res == "restored" and st["ProxyServer"] == "127.0.0.1:7890", (res, st)
    assert not os.path.exists(p.backup_file)

    # ── 代理: 备份丢了, 仍指向 OIT (端口回退到 17893) → 关掉
    be = MemoryBackend({"ProxyEnable": 1, "ProxyServer": "127.0.0.1:17893",
                        "ProxyOverride": "localhost;127.*;[::1]"})
    res, st = restore_proxy(p, be)
    assert res == "disabled" and not proxy_points_to_oit(st), (res, st)

    # ── 代理: 备份里的「原设置」是 OIT 的旧端口 → 不能还原成它
    be = MemoryBackend({"ProxyEnable": 1, "ProxyServer": "127.0.0.1:17891"})
    with open(p.backup_file, "w") as f:
        json.dump({"ours": "127.0.0.1:17891",
                   "orig": {"ProxyEnable": 1, "ProxyServer": "127.0.0.1:17890"}}, f)
    res, st = restore_proxy(p, be)
    assert res == "disabled" and not proxy_points_to_oit(st), (res, st)

    # ── 代理: 本来就没问题 → 什么都不改
    be = MemoryBackend({"ProxyEnable": 1, "ProxyServer": "127.0.0.1:7890"})
    res, st = restore_proxy(p, be)
    assert res == "ok" and st["ProxyServer"] == "127.0.0.1:7890"

    # ── 假的 PowerShell: 进程 + 网卡
    nic = {"以太网": True, "WLAN": False}       # True = 被禁用
    ran = []

    def fake_ps(cmd, timeout=60):
        ran.append(cmd)
        if "Win32_Process" in cmd:
            assert p.main_py.lower() in cmd
            return 0, f"4321 pythonw\n{os.getpid()} python\n"
        if "Enable-NetAdapter" in cmd:
            for k in nic:
                nic[k] = False
            return 0, ""
        if "Get-NetAdapter" in cmd:
            return 0, "\n".join(k for k, v in nic.items() if v)
        return 0, ""

    assert find_oit_processes(p, fake_ps) == [(4321, "pythonw")], "要排除自己"
    assert disabled_adapters(fake_ps) == ["以太网"]
    killed = []
    spawned = []
    logs = []
    fw_calls = []

    def fw_fake(args, timeout=30):              # 不碰真的防火墙: 规则都不在
        fw_calls.append(args)
        return 1, ""
    be = MemoryBackend({"ProxyEnable": 1, "ProxyServer": "127.0.0.1:17890"})
    ok = run_uninstall(p, True, lambda t, lv: logs.append((lv, t)), ps=fake_ps,
                       backend=be, kill_run=lambda a: killed.append(a) or 0,
                       spawn=spawned.append, fw_run=fw_fake)
    assert ok, logs
    assert fw_calls and all(c[:3] == ["netsh", "advfirewall", "firewall"] for c in fw_calls)
    assert killed and killed[0][:3] == ["taskkill", "/PID", "4321"] and "/T" in killed[0]
    assert not proxy_points_to_oit(be.read())
    assert nic == {"以太网": False, "WLAN": False}
    assert not os.path.exists(p.data_dir) and not os.path.exists(p.old_dir)
    assert not os.path.exists(p.legacy_file)
    assert spawned and "-EncodedCommand" in spawned[0]
    script = base64.b64decode(spawned[0][-1]).decode("utf-16-le")
    assert ps_quote(prog) in script and str(os.getpid()) in script
    assert os.path.isdir(prog), "程序文件夹要等卸载程序退出后才删"

    # ── 数据一定会删 (没有保留数据的选项)
    p = fresh()
    ok = run_uninstall(p, False, lambda t, lv: None, ps=fake_ps,
                       backend=MemoryBackend({"ProxyEnable": 0}), kill_run=lambda a: 0, fw_run=fw_fake)
    assert ok and not os.path.exists(p.data_dir) and os.path.isdir(prog)
    p = fresh()

    # ── 网卡启用失败 / 进程结束失败 → 报告未完成
    def stuck_ps(cmd, timeout=60):
        if "Win32_Process" in cmd:
            return 0, "99 python"
        if "Get-NetAdapter" in cmd and "Enable" not in cmd:
            return 0, "以太网"
        return 1, "denied"
    ok = run_uninstall(p, False, lambda t, lv: None, ps=stuck_ps,
                       backend=MemoryBackend({}), kill_run=lambda a: 1, fw_run=fw_fake)
    assert not ok

    # ── 数据目录名字不对时不删 (环境变量异常的保护)
    weird = Paths(os.path.join(tmp, "Users"), prog)
    assert all(t[0] != weird.data_dir for t in weird.data_targets())

    # ── 不像程序文件夹的地方不删
    assert looks_like_program_dir(prog)
    assert not looks_like_program_dir(tmp)
    assert not looks_like_program_dir(os.path.abspath(os.sep))
    assert not schedule_folder_removal(tmp, spawn=lambda a: None)

    # ── 卸载前请绑定的设备清空数据并解除绑定 (读 sync_state.json)
    p = fresh()
    with open(os.path.join(p.data_dir, "sync_state.json"), "w", encoding="utf-8") as f:
        json.dump({"device": {"id": "me"}, "peers": {"a": {"key": "00", "addr": "x", "port": 1},
                                                     "b": {"key": "01", "addr": "y", "port": 2}}}, f)
    sent = []
    assert forget_peers(p, lambda mid, peer: sent.append((mid, peer["addr"])) or peer["addr"] == "x") == 1
    assert sent == [("me", "x"), ("me", "y")]
    assert forget_peers(Paths(os.path.join(tmp, "none"), prog), lambda *a: True) == 0

    shutil.rmtree(tmp, ignore_errors=True)
    return "uninstall selftest OK"


if __name__ == "__main__":
    main()
