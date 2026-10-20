# -*- coding: utf-8 -*-
"""
本地上下文采集:已安装应用 + 浏览器访问记录
──────────────────────────────────────────
用途只有一个:在**用户明确同意**之后,收集一份「本机大概装了什么,常上哪些网站」
的概要,交给 AI 作为规则建议的参考 —— 否则模型只能凭空猜程序名和域名.

隐私约定(重要,改这个文件前请先读):
  · 浏览器记录**只取「域名 + 访问次数」**,不读取具体网址,页面标题,搜索词;
  · 数据只存在于内存里,交给 AI 的文本由界面显示给用户确认后才发送,
    本模块**不写任何文件,不做任何持久化,不联网**;
  · 所有采集都必须由调用方拿到用户同意后触发(见 pages/rules_page.py 的弹窗).

支持的大众浏览器:Chrome / Edge / Firefox / Brave.
Chromium 系(Chrome / Edge / Brave)共用同一套 History SQLite 结构,
Firefox 单独一套(places.sqlite),所以只有两条解析路径.
"""

import glob
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

# Windows FILETIME 纪元(1601-01-01)与 Unix 纪元之间的微秒差
_CHROME_EPOCH_OFFSET_US = 11644473600 * 1000 * 1000

# 最多带回多少条,避免把提示词撑爆(AI 也用不到那么多)
MAX_APPS = 120
MAX_DOMAINS = 50
PER_BROWSER_DOMAINS = 25
PER_BROWSER_ROWS = 20000
HISTORY_WINDOWS = (30, 90, 365)          # 可选的回看天数


# ══════════════ 已安装应用 ══════════════
# 开始菜单里的快捷方式最能代表「用户自己会觉得存在的程序」;
# App Paths 注册表再补一轮(很多程序不建快捷方式但有 App Paths 项).
_SKIP_EXE = {
    "unins000.exe", "unins001.exe", "uninstall.exe", "setup.exe", "install.exe",
    "update.exe", "updater.exe", "crashpad_handler.exe", "crashreporter.exe",
    "python.exe", "pythonw.exe", "pip.exe", "node.exe", "npm.exe", "cmd.exe",
    "powershell.exe", "pwsh.exe", "conhost.exe", "msiexec.exe", "rundll32.exe",
    "dllhost.exe", "regsvr32.exe", "mshta.exe", "wscript.exe", "cscript.exe",
    "java.exe", "javaw.exe", "javac.exe", "7z.exe", "7za.exe", "tar.exe",
    "curl.exe", "wget.exe", "ffmpeg.exe", "ffplay.exe", "ffprobe.exe",
    "git.exe", "git-bash.exe", "bash.exe", "wsl.exe", "wt.exe", "run.exe",
}
_SKIP_NAME_HINT = ("update", "updater", "uninstall", "卸载", "helper", "redistributable",
                   "runtime", "visual c++", "sdk", "driver", "驱动", "framework",
                   "release notes", "license", "readme", "read me", "help", "manual",
                   "documentation", "更多", "more", "what is new", "what's new",
                   "getting started", "welcome to", "start menu",
                   # 安装器 / 卸载器 / 系统工具 / 驱动 / 配置项: 提前启动 (prestart.py) 里这些都不是"正常程序"
                   "setup", "install", "安装", "系统", "配置", "服务", "service",
                   "registry", "注册表", "控制面板", "管理工具", "cmd", "powershell",
                   # 运行时和辅助进程 (Python / Node / SSH 代理 …), 不是给人用的程序
                   "python", "node.js", "askpass", "ssh", "crash", "daemon")
# 一眼就知道不是"需要联网的程序"的可执行文件(系统组件,输入法,驱动管理器……).
# 与 _SKIP_EXE 的区别:这里是**子串**匹配,专门拦掉安装器/卸载器这一类变体名.
_SKIP_EXE_HINT = ("favicon.exe", "dfshim", "wab.exe", "wabmig",
                  "tabtip.exe", "table30.exe", "odbcad32.exe", "sapisvr.exe",
                  "store.exe", "licensemanager", "cmstp", "mshta",
                  "wscript", "cscript", "snippingtool", "magnify",
                  "narrator", "osk.exe", "perfmon", "iscsicpl.exe",
                  "dfrgui", "cleanmgr", "windowsmail", "onenotelauncher",
                  "unins", "setup.exe", "installer.exe", "install.exe",
                  "update.exe", "updater.exe",
                  # 运行时 / 辅助进程: python 3.13 / node / claude-ssh-askpass 这种
                  "python", "node.exe", "askpass", "ssh-", "crashpad", "crashreport",
                  # 系统组件 / 包管理器 / Store 应用的后台部分
                  "pip", "idle3", "winget", "windowspackagemanager", "xboxpcapp",
                  "messagehost", "recoverydrive")

# Windows 自带的系统工具 / 管理单元.它们都在开始菜单里,但对"该放行哪个程序"
# 毫无参考价值,还会把真正的应用挤出上限(清单多了 120 条根本读不下去).
_SKIP_NAME_EXACT = {
    "administrative tools", "component services", "computer management",
    "control panel", "event viewer", "disk cleanup", "character map",
    "command prompt", "file explorer", "memory diagnostics tool",
    "isci initiator", "magnify", "narrator", "on-screen keyboard",
    "print management", "recovery drive", "resource monitor", "registry editor",
    "services", "system configuration", "system information", "task scheduler",
    "windows defender firewall", "windows memory diagnostic", "windows powershell",
    "windows tools", "windows update", "windows features", "windows security",
    "steps recorder", "quick assist", "remote desktop connection",
    "task manager", "device manager", "dfrgui", "fsquirt", "gethelp",
    "livecaptions", "mspaint", "notepad", "mplayer2", "wordpad",
    "internet explorer", "iediag", "iediagcmd", "iexplore", "starting",
    "performance monitor", "speech recognition", "security configuration management",
    "odbc data sources", "odbc data sources (32-bit)", "odbc data sources (64-bit)",
    "windows fax and scan", "xps viewer", "color management",
    "bluetooth file transfer", "windows mobility center", "microsoft store",
    "xbox", "xbox game bar", "feedback hub", "tips", "snipping tool",
    "windows media player", "media player", "windows accessories",
    "windows administrative tools", "windows system", "windows ease of access",
}
_SKIP_NAME_PREFIX = ("windows ", "microsoft ")
_SKIP_NAME_SUFFIX = (" help", " tools", " saves & logs", " release notes")


def is_normal_app(name: str, exe: str) -> bool:
    """这个名字 + exe 像不像"正常的程序" (提前启动的程序清单用).

    去掉: 安装器 / 卸载器 (setup, uninstall, unins000 …), 更新器, 系统工具 / 驱动 /
    配置项 (.cpl / .msc / 控制面板 / 注册表 / 服务 …), 以及上面几张跳过表里的东西.
    只看名字和 exe 文件名, 不打开任何文件.
    """
    name = _clean_app_name(name)
    low = name.lower()
    base = os.path.basename(str(exe or "")).strip().lower()
    if not name or not base.endswith(".exe"):
        return False
    if base in _SKIP_EXE or any(h in base for h in _SKIP_EXE_HINT):
        return False
    if any(h in low for h in _SKIP_NAME_HINT):
        return False
    if low in _SKIP_NAME_EXACT or low.startswith(_SKIP_NAME_PREFIX):
        return False
    if any(low.endswith(s) for s in _SKIP_NAME_SUFFIX):
        return False
    return True


def _clean_app_name(name: str) -> str:
    """快捷方式名 / 显示名整理成人类看得懂的应用名"""
    name = str(name or "").strip()
    name = re.sub(r'\.(lnk|url|exe)$', '', name, flags=re.I)
    name = re.sub(r'\s*[-–—]\s*(快捷方式|shortcut)$', '', name, flags=re.I)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def _add_app(found: dict, name: str, exe: str = None, prefer: bool = False,
             from_menu: bool = False):
    name = _clean_app_name(name)
    if not name or len(name) > 60:
        return
    low = name.lower()
    if any(h in low for h in _SKIP_NAME_HINT):
        return
    if low in _SKIP_NAME_EXACT or low.startswith(_SKIP_NAME_PREFIX):
        return
    if any(low.endswith(s) for s in _SKIP_NAME_SUFFIX):
        return
    if not exe:
        return
    base = os.path.basename(str(exe))
    if not base.lower().endswith(".exe"):
        return
    key = base.lower()
    if key in _SKIP_EXE or any(h in key for h in _SKIP_EXE_HINT):
        return
    cur = found.get(key)
    if cur is None:
        found[key] = {"name": name, "exe": base,
                      "_prefer": bool(prefer), "_menu": bool(from_menu)}
        return
    # 已经由开始菜单收进来的条目不要被注册表的裸 exe 名顶掉 ——
    # 注册表里既有 "chrome.exe" 也可能有别的别名,重复收录只会让清单变乱
    if cur.get("_menu") and not from_menu:
        return
    if prefer and not cur.get("_prefer"):
        cur["name"] = name
        cur["_prefer"] = True
    if from_menu:
        cur["_menu"] = True


def _exe_from_lnk(path: str) -> str:
    """不依赖 pywin32 从 .lnk 里找目标 exe 名.

    .lnk 是二进制格式,完整解析要处理 LinkTargetIDList / LinkInfo 两套结构;
    这里只需要「目标是哪个 exe」,所以直接抽取结尾的 .exe 字符串即可 ——
    比引入 pywin32 依赖划算得多,也足够给出规则建议用的名字.

    找不到就退回快捷方式自身的名字(如「Steam.exe」),它通常就等于目标文件名.
    """
    import struct as _struct
    try:
        with open(path, "rb") as f:
            data = f.read(65536)
    except OSError:
        return ""
    header = _struct.unpack_from("<I", data, 0)[0] if len(data) >= 4 else 0
    if header != 0x4C:                       # 不是 .lnk(可能只是改了扩展名)
        return ""
    exes = []
    for m in re.finditer(rb'[\x20-\x7e]{1,200}?\.exe', data):
        exes.append(m.group(0).decode("ascii", "ignore"))
    # 目标路径通常出现在文件靠后的位置,取最后一个
    if exes:
        candidate = exes[-1]
        if "\\" in candidate:
            candidate = candidate.rsplit("\\", 1)[-1]
        return candidate
    name = _clean_app_name(os.path.splitext(os.path.basename(path))[0])
    return (name + ".exe") if name else ""


def apps_from_start_menu(found: dict) -> None:
    """开始菜单快捷方式(当前用户 + 所有用户两份).

    优先用 pywin32 解析快捷方式;不可用时退回读 .lnk 二进制 ——
    否则整批用户可见的应用都会因为「解不出目标」而被丢掉.
    """
    roots = [
        os.path.join(os.environ.get("APPDATA", ""),
                     r"Microsoft\Windows\Start Menu\Programs"),
        os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                     r"Microsoft\Windows\Start Menu\Programs"),
    ]
    if sys.platform != "win32":
        return
    shell = None
    try:
        import win32com.client                     # type: ignore
        shell = win32com.client.Dispatch("WScript.Shell")
    except Exception:
        shell = None
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for path in glob.glob(os.path.join(root, "**", "*.lnk"), recursive=True):
            name = os.path.splitext(os.path.basename(path))[0]
            target = ""
            if shell is not None:
                try:
                    target = shell.CreateShortCut(path).TargetPath or ""
                except Exception:
                    target = ""
            # 解析到真实目标时,快捷方式名可信,允许它覆盖注册表里的裸 exe 名
            trusted = bool(target)
            exe = target or _exe_from_lnk(path)
            _add_app(found, name, exe, prefer=trusted, from_menu=True)


def apps_from_registry(found: dict) -> None:
    """App Paths:exe 名 → 完整路径,是最可靠的可执行文件名单"""
    if sys.platform != "win32":
        return
    try:
        import winreg
    except ImportError:                            # pragma: no cover
        return

    def read(root, sub, access=0):
        try:
            with winreg.OpenKey(root, sub, 0, winreg.KEY_READ | access) as key:
                n = winreg.QueryInfoKey(key)[0]
                for i in range(n):
                    try:
                        name = winreg.EnumKey(key, i)
                    except OSError:
                        continue
                    exe = name if name.lower().endswith(".exe") else name + ".exe"
                    pretty = _clean_app_name(os.path.splitext(exe)[0])
                    _add_app(found, pretty, exe)
        except OSError:
            pass

    sub = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    flags = 0
    try:
        flags = winreg.KEY_WOW64_64KEY
    except AttributeError:                         # pragma: no cover
        flags = 0
    read(winreg.HKEY_LOCAL_MACHINE, sub, flags)
    read(winreg.HKEY_LOCAL_MACHINE, sub, getattr(winreg, "KEY_WOW64_32KEY", 0))
    read(winreg.HKEY_CURRENT_USER, sub)


def collect_apps() -> list:
    """已安装 / 常用应用的清单,返回 [{"name", "exe"}]"""
    found = {}
    apps_from_start_menu(found)
    apps_from_registry(found)
    items = list(found.values())
    for it in items:                      # 内部标记不进结果
        it.pop("_prefer", None)
        it.pop("_menu", None)
    items.sort(key=lambda a: a["name"].lower())
    return items[:MAX_APPS]


# ══════════════ 浏览器记录(只有域名 + 次数) ══════════════
# 明确不采集的地址:搜索引擎结果页,扩展内部页,本机地址
_SKIP_DOMAIN = (
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "googleusercontent.com", "gstatic.com", "googleapis.com",
    "bing.com", "google.com", "duckduckgo.com", "baidu.com",
    "mozilla.org", "mozilla.net", "microsoft.com", "windows.com",
    "msn.com", "live.com", "office.com", "akamaihd.net", "cloudflare.com",
    "gvt1.com", "gvt2.com", "doubleclick.net", "googlesyndication.com",
    "scorecardresearch.com", "adservice.google.com", "ytimg.com",
)
_SKIP_SCHEME = ("chrome://", "edge://", "brave://", "about:", "devtools://",
                "chrome-extension://", "moz-extension://", "file:", "data:",
                "javascript:", "view-source:", "chrome-search://", "edge-search://")


def domain_of(url: str) -> str:
    """从 URL 里只取主机名(去掉 scheme / 路径 / 端口 / www.)"""
    if not url:
        return ""
    low = url.strip().lower()
    if low.startswith(_SKIP_SCHEME):
        return ""
    low = re.sub(r'^[a-z][a-z0-9+.\-]*://', '', low)
    host = re.split(r'[/?#]', low, maxsplit=1)[0]
    host = host.split('@')[-1]                   # 去掉 user:pass@
    host = host.split(':')[0]                    # 去掉端口
    host = host.strip('.')
    if host.startswith("www."):
        host = host[4:]
    if not host or len(host) > 100:
        return ""
    if not re.fullmatch(r'[a-z0-9\-._]+', host):
        return ""
    if '.' not in host:                          # 没有点的一般是内网名
        return ""
    if any(host == d or host.endswith('.' + d) for d in _SKIP_DOMAIN):
        return ""
    return host


def browser_history_paths() -> list:
    """[(浏览器显示名, History/places.sqlite 路径)] —— 只覆盖大众浏览器"""
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    home = os.path.expanduser("~")
    out = []

    chromium = [
        ("Chrome", os.path.join(local, r"Google\Chrome\User Data")),
        ("Edge", os.path.join(local, r"Microsoft\Edge\User Data")),
        ("Brave", os.path.join(local, r"BraveSoftware\Brave-Browser\User Data")),
    ]
    for label, root in chromium:
        if not root or not os.path.isdir(root):
            continue
        # Default + Profile 1..N
        profs = [os.path.join(root, "Default")]
        profs += sorted(glob.glob(os.path.join(root, "Profile *")))
        for prof in profs:
            path = os.path.join(prof, "History")
            if os.path.isfile(path):
                out.append((label, path))

    ff_root = os.path.join(roaming, r"Mozilla\Firefox\Profiles")
    if ff_root and os.path.isdir(ff_root):
        for prof in sorted(glob.glob(os.path.join(ff_root, "*"))):
            path = os.path.join(prof, "places.sqlite")
            if os.path.isfile(path):
                out.append(("Firefox", path))
    elif home:                                    # 少数安装把 profile 放在别处
        for path in glob.glob(os.path.join(home, r".mozilla\firefox\*", "places.sqlite")):
            out.append(("Firefox", path))
    return out


def _copy_sidecar(path: str, tmpdir: str) -> str:
    """把 sqlite 库(含 -wal/-shm)复制到临时目录,避免和正在运行的浏览器抢锁."""
    name = os.path.basename(path).replace(" ", "_")
    dst = os.path.join(tmpdir, name)
    shutil.copy2(path, dst)
    for suffix in ("-wal", "-shm"):
        src = path + suffix
        if os.path.isfile(src):
            try:
                shutil.copy2(src, dst + suffix)
            except OSError:
                pass
    return dst


def _query(db: str, sql: str, params=()) -> list:
    """只读查询;失败(损坏/锁住/结构不同)返回空列表,不影响其它浏览器."""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db.replace("?", "%3f"), uri=True)
    except sqlite3.Error:
        return []
    try:
        return list(con.execute(sql, params))
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _chromium_domains(db: str, min_time_us: int) -> dict:
    """Chrome / Edge / Brave:urls 表,时间是 1601 纪元的微秒"""
    rows = _query(
        db,
        "SELECT url, visit_count, last_visit_time FROM urls "
        "WHERE last_visit_time >= ? ORDER BY visit_count DESC LIMIT ?",
        (min_time_us, PER_BROWSER_ROWS),
    )
    return _tally(rows)


def _firefox_domains(db: str, min_time_us: int) -> dict:
    """Firefox:places 表,时间是 Unix 纪元的微秒"""
    rows = _query(
        db,
        "SELECT url, visit_count, last_visit_date FROM moz_places "
        "WHERE last_visit_date >= ? AND visit_count > 0 "
        "ORDER BY visit_count DESC LIMIT ?",
        (min_time_us, PER_BROWSER_ROWS),
    )
    return _tally(rows)


def _tally(rows) -> dict:
    """[(url, count, ts)] → {domain: 访问次数(去重合并)}"""
    out = {}
    for row in rows:
        try:
            url, count, _ts = row[0], int(row[1] or 0), row[2]
        except (TypeError, ValueError, IndexError):
            continue
        domain = domain_of(url)
        if not domain:
            continue
        out[domain] = out.get(domain, 0) + max(1, count)
    return out


def collect_browser_domains(days: int = 90, progress=None) -> tuple:
    """跨浏览器汇总最近 days 天的域名访问次数.

    返回 (domains, sources):
      domains = [{"domain", "visits", "browsers": [...]}] 按访问次数降序
      sources = [{"name", "profiles", "error"}] 每个浏览器用了几个库 / 是否读不到
    """
    days = days if days in HISTORY_WINDOWS else 90
    # 注意:时间阈值必须用整数运算.`(ts - days*86400) * 1e6` 会超过 float 的
    # 2^53 精度上限,微秒被舍入 → 边界附近的记录会被莫名丢掉.
    cutoff = datetime.now() - timedelta(days=days)
    min_unix_us = int(cutoff.timestamp()) * 1000 * 1000
    chromium_min = min_unix_us + _CHROME_EPOCH_OFFSET_US
    firefox_min = min_unix_us

    merged = {}
    sources = []
    paths = browser_history_paths()
    tmpdir = tempfile.mkdtemp(prefix="oit_hist_")
    try:
        by_browser = {}
        for label, path in paths:
            by_browser.setdefault(label, []).append(path)

        for label, group in by_browser.items():
            info = {"name": label, "profiles": 0, "error": ""}
            total = {}
            for path in group:
                if progress:
                    progress(label)
                try:
                    db = _copy_sidecar(path, tmpdir)
                except OSError:
                    # 浏览器正在跑且文件被独占:退回直接只读打开
                    db = path
                tally = (_firefox_domains(db, firefox_min) if label == "Firefox"
                         else _chromium_domains(db, chromium_min))
                if tally:
                    info["profiles"] += 1
                for domain, visits in tally.items():
                    total[domain] = total.get(domain, 0) + visits
            if not info["profiles"]:
                info["error"] = "no_data"
            # 按访问次数降序(只取前 PER_BROWSER_DOMAINS 个,避免单浏览器刷屏)
            ranked = sorted(total.items(),
                            key=lambda item: (-item[1], item[0]))[:PER_BROWSER_DOMAINS]
            for domain, visits in ranked:
                item = merged.setdefault(domain, {"domain": domain, "visits": 0, "browsers": []})
                item["visits"] += visits
                if label not in item["browsers"]:
                    item["browsers"].append(label)
            sources.append(info)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    domains = sorted(merged.values(), key=lambda d: (-d["visits"], d["domain"]))[:MAX_DOMAINS]
    return domains, sources


# ══════════════ 汇总成给 AI 的文本 ══════════════
def build_context_text(apps: list = None, domains: list = None,
                       days: int = 90, app_limit: int = 80,
                       domain_limit: int = MAX_DOMAINS) -> str:
    """把采集结果拼成一段参考信息(纯文本,供用户预览与随请求发送).

    **每项独占一行**.上百条应用若挤成一行再自动折行,预览区会变成一坨,
    根本没法核对(这正是"页面错乱"的来源之一).

    文案走 i18n.tr():这段字会显示给用户看,也会发给模型,界面语言是英文时
    不该漏出中文.带数值的部分必须先查表再 .format(),不能先拼好.
    """
    from i18n import tr

    parts = []
    if apps:
        names = []
        for a in apps[:app_limit]:
            exe = a.get("exe") or ""
            names.append(f"{a['name']} ({exe})" if exe else a["name"])
        parts.append(tr("【本机已安装的应用(来自开始菜单与注册表)】\n{list}").format(
            list="\n".join(names)))
    if domains:
        lines = [tr("{domain} —— {visits} 次").format(
            domain=d["domain"], visits=d["visits"]) for d in domains[:domain_limit]]
        parts.append(
            tr("【最近 {days} 天浏览器访问最多的网站(只有域名与次数,无具体网址)】\n{list}").format(
                days=days, list="\n".join(lines)))
    return "\n\n".join(parts)


def collect_context(want_apps: bool = True, want_history: bool = True,
                    days: int = 90, progress=None) -> dict:
    """采集入口.返回:

        {"apps": [...], "domains": [...], "days": n,
         "sources": [...],             # 各浏览器读取情况(用于告诉用户"没读到")
         "errors": [...],
         "text": "给 AI / 给用户预览的整段文本"}
    """
    apps, domains, sources, errors = [], [], [], []
    if want_apps:
        try:
            apps = collect_apps()
        except Exception as e:                     # pragma: no cover - 环境相关
            errors.append("apps: %s" % e)
    if want_history:
        try:
            domains, sources = collect_browser_domains(days=days, progress=progress)
        except Exception as e:                     # pragma: no cover - 环境相关
            errors.append("history: %s" % e)
    return {
        "apps": apps,
        "domains": domains,
        "days": days if days in HISTORY_WINDOWS else 90,
        "sources": sources,
        "errors": errors,
        "text": build_context_text(apps, domains, days=days),
    }


# ══════════════ 自检(不读取真实浏览器数据) ══════════════
def _selftest():
    assert domain_of("https://www.bilibili.com/video/BV1x?t=1") == "bilibili.com"
    assert domain_of("http://Sub.Example.COM:8443/a") == "sub.example.com"
    assert domain_of("chrome://settings") == ""
    assert domain_of("https://www.google.com/search?q=secret") == ""
    assert domain_of("about:blank") == ""
    assert domain_of("http://127.0.0.1:8080") == ""
    assert domain_of("https://user:pw@github.com/x") == "github.com"
    assert domain_of("https://ads.doubleclick.net/x") == ""
    assert domain_of("not a url") == ""

    txt = build_context_text(
        apps=[{"name": "Steam", "exe": "steam.exe"}],
        domains=[{"domain": "bilibili.com", "visits": 312, "browsers": ["Chrome"]}],
    )
    assert "steam.exe" in txt and "bilibili.com —— 312 次" in txt
    assert "无具体网址" in txt
    return "context_collect selftest OK"


if __name__ == "__main__":
    print(_selftest())
