"""提前启动 (仅 PC, MECHANICS 20).

专注中可以先挑一两个程序"提前启动": 程序现在就跑起来 (或者已经在跑), 但窗口藏起来、静音,
专注结束时再放出来, 程序照常运行. 每次专注只能选一次.

- 挑程序: 开始菜单快捷方式 + App Paths 注册表 + 正在运行的程序 (复用 context_collect 的
  快捷方式解析 / 过滤规则, 和 window_guard 的窗口、进程枚举).
- 启动: 快捷方式 (.lnk) 或 exe 全路径交给 os.startfile; 已经在运行的不再启动.
- 跟踪: 记住这些程序, 以及它们派生出来的子进程 (启动器 → 游戏这种); 每秒:
  · 把它们可见的窗口 (包括后来才出现的子窗口) 藏起来 (最小化的不碰);
  · 把它们自己的音频会话静音 (audiomute.py), 不动别的程序.
- 结束专注: 藏起来的窗口全部显示出来 (不抢焦点), 取消静音.

选好程序以后先有 GRACE_SECONDS (3 分钟) 的**操作空窗期**: 程序照常显示、不静音, 方便先登录 / 下载;
这期间 Timer 页的按钮变成"提前终止", 点它或者等空窗期结束, 才开始藏窗口 / 静音.

休息中直接开始下一段专注时, 上一段专注的提前启动会原样接着 (不再重新给操作空窗期, 免得程序被应用名单关掉);
联网豁免只在专注期间生效 (休息中照常按名单判定).

另外管"触发隐藏的窗口" (设置 → 窗口): 标题匹配这些关键字的窗口藏起来而不关掉, 并把这个程序也标记成
只藏不联网的提前启动 (应用名单不关它, 但联网照常判定).

    python prestart.py            自检 (不启动、不碰真实窗口, 只测挑程序和进程树)
"""

import os
import sys
import time

import audiomute
import window_guard


MAX_APPS = 2                     # 一次专注最多提前启动几个程序
GRACE_SECONDS = 180.0            # 选好以后的"操作空窗期": 这么多秒内程序照常显示, 之后才藏 / 静音


# 空窗期里"新启动的进程"也要标记, 但这些系统 / 壳 / 辅助进程不算 (标记了等于放行 + 按名字藏窗口)
_NOISE_EXES = {
    "python.exe", "pythonw.exe", "svchost.exe", "conhost.exe", "cmd.exe", "powershell.exe",
    "pwsh.exe", "wmiprvse.exe", "werfault.exe", "wermgr.exe", "dllhost.exe", "runtimebroker.exe",
    "backgroundtaskhost.exe", "taskhostw.exe", "sihost.exe", "audiodg.exe", "smartscreen.exe",
    "searchprotocolhost.exe", "searchfilterhost.exe", "explorer.exe", "ctfmon.exe", "fontdrvhost.exe",
    "wudfhost.exe", "msiexec.exe", "tasklist.exe", "wmic.exe", "taskkill.exe", "net.exe", "net1.exe",
    "ipconfig.exe", "netsh.exe", "reg.exe", "sc.exe", "where.exe", "timeout.exe", "curl.exe",
    "consent.exe", "openconsole.exe", "windowsterminal.exe", "applicationframehost.exe",
}


def _skip_exes() -> set:
    return _NOISE_EXES | set(getattr(window_guard, "PROTECTED_EXES", set()))


def _clean(name: str) -> str:
    try:
        import context_collect
        return context_collect._clean_app_name(name)
    except Exception:
        return str(name or "").strip()


def _exe_of_link(path: str) -> str:
    try:
        import context_collect
        return str(context_collect._exe_from_lnk(path) or "").strip().lower()
    except Exception:
        return ""


def _normal(name: str, exe: str) -> bool:
    """只留正常程序: 安装器 / 卸载器 / 更新器 / 系统工具 / 驱动 / 配置项都去掉 (规则在 context_collect)."""
    try:
        import context_collect
        return context_collect.is_normal_app(name, exe)
    except Exception:
        return True


def _start_menu_links() -> list:
    """[{"name", "exe", "launch"}]: 开始菜单里的快捷方式 (当前用户 + 所有用户)."""
    import glob
    if sys.platform != "win32":
        return []
    roots = [
        os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
        os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                     r"Microsoft\Windows\Start Menu\Programs"),
    ]
    out = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for path in glob.glob(os.path.join(root, "**", "*.lnk"), recursive=True):
            exe = _exe_of_link(path)
            name = _clean(os.path.splitext(os.path.basename(path))[0])
            if not exe or not exe.endswith(".exe") or not _normal(name, exe):
                continue
            out.append({"name": name, "exe": exe, "launch": path})
    return out


def _app_paths() -> list:
    """[{"name", "exe", "launch"}]: App Paths 注册表里的 exe (默认值就是全路径)."""
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []
    sub = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    roots = [(winreg.HKEY_LOCAL_MACHINE, getattr(winreg, "KEY_WOW64_64KEY", 0)),
             (winreg.HKEY_LOCAL_MACHINE, getattr(winreg, "KEY_WOW64_32KEY", 0)),
             (winreg.HKEY_CURRENT_USER, 0)]
    out = []
    for hive, flags in roots:
        try:
            with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | flags) as key:
                n = winreg.QueryInfoKey(key)[0]
                names = []
                for i in range(n):
                    try:
                        names.append(winreg.EnumKey(key, i))
                    except OSError:
                        continue
        except OSError:
            continue
        for name in names:
            exe = name if name.lower().endswith(".exe") else name + ".exe"
            path = ""
            try:
                with winreg.OpenKey(hive, sub + "\\" + name, 0, winreg.KEY_READ | flags) as k:
                    path = str(winreg.QueryValueEx(k, "")[0] or "").strip()
            except OSError:
                path = ""
            if not path.lower().endswith(".exe") or not os.path.isfile(path):
                path = ""
            name = _clean(os.path.splitext(exe)[0])
            if _normal(name, exe):
                out.append({"name": name, "exe": exe.lower(), "launch": path})
    return out


def _running_exes() -> set:
    """此刻在运行、而且有任务栏窗口的程序 (exe 小写)."""
    out = set()
    try:
        for w in window_guard.taskbar_windows():
            exe = window_guard.exe_name(w.get("pid") or 0).strip().lower()
            if exe:
                out.add(exe)
    except Exception:
        pass
    return out


def scan_programs() -> list:
    """可以提前启动的程序: [{"name", "exe", "launch", "running"}], 按名字排.

    开始菜单的快捷方式优先 (能直接启动); App Paths 里能补上全路径;
    正在运行但两边都没有的程序也列出来 (launch 是空的, 只藏窗口/静音, 不启动).
    """
    running = _running_exes()
    found = {}
    for item in _start_menu_links():
        cur = found.get(item["exe"])
        if cur is None or (not cur.get("launch") and item.get("launch")):
            found[item["exe"]] = item
    for item in _app_paths():
        cur = found.get(item["exe"])
        if cur is None:
            found[item["exe"]] = item
        elif not cur.get("launch") and item.get("launch"):
            cur["launch"] = item["launch"]
    own = {"python.exe", "pythonw.exe"} | set(getattr(window_guard, "PROTECTED_EXES", set()))
    for exe in running:
        if exe in own:
            continue
        cur = found.get(exe)
        if cur is None:
            found[exe] = {"name": _clean(os.path.splitext(exe)[0]), "exe": exe, "launch": ""}
    out = []
    for exe, item in found.items():
        if exe in own:
            continue
        name = str(item.get("name") or "").strip()
        if not name or not _normal(name, exe):
            continue
        launch = item.get("launch") or ""
        if not launch and exe not in running:
            continue                 # 不能启动、又没在跑: 选了也没用
        out.append({"name": name, "exe": exe, "launch": launch,
                    "running": exe in running})
    out.sort(key=lambda a: (not a["running"], a["name"].lower()))
    return out


def program_from_path(path: str):
    """从文件管理器里挑的程序 (.exe / .lnk) → {"name", "exe", "launch", "running"}.

    同样要过"正常程序"的过滤 (安装器 / 卸载器 / 系统工具之类返回 None)."""
    path = str(path or "").strip()
    if not path or not os.path.isfile(path):
        return None
    low = path.lower()
    base = os.path.basename(path)
    if low.endswith(".lnk"):
        exe = _exe_of_link(path)
        if not exe:
            return None
    elif low.endswith(".exe"):
        exe = base.lower()
    else:
        return None
    name = _clean(os.path.splitext(base)[0])
    if not name or not _normal(name, exe):
        return None
    return {"name": name, "exe": exe, "launch": path, "running": exe in _running_exes()}


class ListStore:
    """提前启动清单的用户修改 (存档 prestart_list.json): 手动添加的程序 + 被 ✕ 去掉的程序.

    每次扫描完用 apply() 套上去: 去掉的不再出现, 添加的一直在 (除非文件没了又没在跑); 再次添加会取消"去掉"."""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "prestart_list.json")
        self.added = []              # [{"name", "exe", "launch"}]
        self.removed = set()         # exe 小写
        self.blocked = lambda: False  # 重置中: 不再写文件
        self._load()

    def _load(self):
        import json
        import securefile
        try:
            with securefile.open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(d, dict):
            return
        for it in d.get("added") or []:
            if isinstance(it, dict) and str(it.get("exe") or "").strip():
                self.added.append({"name": str(it.get("name") or it["exe"]),
                                   "exe": str(it["exe"]).strip().lower(),
                                   "launch": str(it.get("launch") or "")})
        self.removed = {str(e).strip().lower() for e in (d.get("removed") or []) if str(e).strip()}

    def save(self):
        if self.blocked():
            return
        import json
        import securefile
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with securefile.open(tmp, "w", encoding="utf-8") as f:
                json.dump({"added": self.added, "removed": sorted(self.removed)},
                          f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def add(self, item: dict):
        exe = str(item.get("exe") or "").strip().lower()
        if not exe:
            return
        self.removed.discard(exe)
        self.added = [a for a in self.added if a["exe"] != exe]
        self.added.append({"name": str(item.get("name") or exe), "exe": exe,
                           "launch": str(item.get("launch") or "")})
        self.save()

    def remove(self, exe: str):
        exe = str(exe or "").strip().lower()
        if not exe:
            return
        self.added = [a for a in self.added if a["exe"] != exe]
        self.removed.add(exe)
        self.save()

    def apply(self, apps: list) -> list:
        """扫描结果 + 用户修改 → 最终清单 (去掉被 ✕ 的, 补上手动添加的)."""
        out = [dict(a) for a in apps if str(a.get("exe") or "").lower() not in self.removed]
        have = {a["exe"] for a in out}
        running = None
        for it in self.added:
            if it["exe"] in have or it["exe"] in self.removed:
                continue
            if running is None:
                running = _running_exes()
            is_run = it["exe"] in running
            if not is_run and not (it["launch"] and os.path.isfile(it["launch"])):
                continue                 # 文件没了又没在跑: 选了也没用
            out.append({"name": it["name"], "exe": it["exe"], "launch": it["launch"],
                        "running": is_run})
        return out


class TriggerStore:
    """触发隐藏的窗口 (设置 → 窗口): 窗口标题关键字列表, 存档 hidden_windows.json.

    只要窗口标题里包含其中一项 (不分大小写) 就把它藏起来 —— 这类窗口总被当成"提前启动"藏起来,
    不会被应用名单关掉; 但它们不因此获得联网权限 (MECHANICS 20).
    """

    MAX_ITEMS = 100
    MAX_LEN = 120

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "hidden_windows.json")
        self.titles = []             # 原样保存, 匹配时 casefold
        self.blocked = lambda: False  # 重置中: 不再写文件
        self._load()

    def _load(self):
        import json
        import securefile
        try:
            with securefile.open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return
        raw = d.get("titles") if isinstance(d, dict) else d
        self.titles = self._clean(raw)

    @classmethod
    def _clean(cls, lines) -> list:
        out, seen = [], set()
        for line in lines or []:
            text = str(line or "").strip()[:cls.MAX_LEN]
            key = text.casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            out.append(text)
            if len(out) >= cls.MAX_ITEMS:
                break
        return out

    def save(self):
        if self.blocked():
            return
        import json
        import securefile
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with securefile.open(tmp, "w", encoding="utf-8") as f:
                json.dump({"titles": self.titles}, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def set_titles(self, lines):
        """整份替换 (设置页的文本框保存). 返回有没有变."""
        titles = self._clean(lines)
        if titles == self.titles:
            return False
        self.titles = titles
        self.save()
        return True

    def matches(self, title: str) -> bool:
        text = str(title or "").casefold()
        return bool(text) and any(t.casefold() in text for t in self.titles)


def descendants(pids, procs=None) -> set:
    """pids 以及它们派生出来的子进程 (启动器 → 游戏这种)."""
    procs = window_guard.list_processes_full() if procs is None else procs
    out = {int(p) for p in pids}
    changed = True
    while changed:
        changed = False
        for pid, ppid, _exe in procs:
            if ppid in out and pid not in out:
                out.add(int(pid))
                changed = True
    return out


def track(procs, exes, pids, marked) -> tuple:
    """每秒的跟踪: 选中的程序 (exes) 和上一秒还活着的跟踪进程, 连同它们的子进程 → (pids, marked).

    marked 是这次专注里所有跟踪过的程序 exe 名, 只增不减: 程序退出 (比如启动器关掉) 也保留."""
    alive = {int(pid) for pid, _ppid, _exe in procs}
    seed = {pid for pid in pids if pid in alive} | {pid for pid, _ppid, exe in procs if exe in exes}
    pids = descendants(seed, procs)
    pids.discard(os.getpid())
    marked = set(marked) | {exe for pid, _ppid, exe in procs if pid in pids}
    return pids, marked


class Prestart:
    """一次专注的"提前启动": 启动、藏窗口、静音; 专注结束时全部放出来.

    MECHANICS 20: 选好以后先有 GRACE_SECONDS 的**操作空窗期** (程序照常显示、不出声地跑着),
    这期间按钮是"提前终止"; 点它或者等空窗期结束, 才开始藏窗口 / 静音.
    """

    def __init__(self):
        self.items = []          # 选中的程序 {"name", "exe", "launch", "running"}
        self.last_items = []     # 上一段专注的提前启动 (休息中保留, 休息中直接开始专注时接着用)
        self.active = False
        self.pids = set()        # 跟踪的进程 (含子进程, 只含还活着的)
        self.marked = set()      # 被标记的程序 exe 名 (整个专注期间保留, 退出也不删)
        self.hidden = set()      # 藏起来的窗口 (选中的程序)
        self.phase = ""          # "" 没开始 / "grace" 操作空窗期 / "active" 藏窗口 + 静音
        self.grace_until = 0.0   # 操作空窗期结束的时刻 (monotonic)
        self._exes = set()
        self.keep = lambda exe: False   # 用户本来就放行的程序 (应用白名单): 不标记、不藏、不静音 (除非是专门选中的)
        self._seeds = set()      # 空窗期里新启动的程序的进程
        self._baseline = None    # 开始时已在运行的进程 (空窗期里不算"新启动")
        self.baseline_exes = None  # 开始时已在运行的程序 exe 名 (空窗期里联网 / 名单放行"新程序"用)
        # 触发隐藏的窗口 (设置 → 窗口): 只藏 / 不联网放行. 这些窗口和程序在 OIT 运行期间一直藏着,
        # 直到列表里去掉对应的标题 / 关闭 OIT.
        self.triggers = lambda: []      # 窗口标题关键字 (小写也行)
        self.hidden_only = set()        # 被触发隐藏的程序的 exe 名 (应用名单不关, 但联网照常判定)
        self.auto_hidden = set()        # 自动藏起来的窗口
        self.auto_pids = set()          # 自动藏起来的进程 (静音用)

    @property
    def chosen(self) -> bool:
        """这次专注已经选过了 (不能再改选)."""
        return bool(self.items)

    @property
    def in_grace(self) -> bool:
        """还在操作空窗期 (程序还没被藏起来 / 静音)."""
        return self.active and self.phase == "grace"

    def start(self, items, grace: bool = True) -> list:
        """开始: 没在运行的程序启动它, 进入 GRACE_SECONDS 的操作空窗期. 返回启动失败的名字.

        grace=False: 休息中直接接着上一段专注, 不再给操作空窗期, 马上藏窗口 / 静音."""
        self.items = [dict(it) for it in (items or [])][:MAX_APPS]
        self.last_items = []
        self.active = True
        self.pids = set()
        self.marked = set()
        self.hidden = set()
        self.phase = "grace" if grace else "active"
        self.grace_until = time.monotonic() + GRACE_SECONDS if grace else 0.0
        self._exes = {str(it.get("exe") or "").lower() for it in self.items if it.get("exe")}
        self._seeds = set()
        try:
            base = window_guard.list_processes_full()
            self._baseline = {int(pid) for pid, _p, _e in base}
            self.baseline_exes = {exe for _pid, _p, exe in base} | _skip_exes()
        except Exception:
            self._baseline = None
            self.baseline_exes = None
        failed = []
        for it in self.items:
            if it.get("running") or not it.get("launch"):
                continue
            try:
                os.startfile(it["launch"])
            except OSError:
                failed.append(str(it.get("name") or it.get("exe") or "?"))
        self.step()                  # 空窗期里只认进程, 不藏窗口
        return failed

    def park(self):
        """结束专注: 记下这次选过的程序 (休息中直接开始专注时接着用), 然后由 stop() 放出来."""
        if self.items:
            self.last_items = [dict(it) for it in self.items]

    def resume_last(self) -> bool:
        """休息中直接开始专注: 接着上一段专注的提前启动 (不给新的操作空窗期). 返回有没有接着."""
        items = [dict(it) for it in self.last_items]
        self.last_items = []
        if not items:
            return False
        try:
            running = _running_exes()        # 休息中关掉了的程序: 重新启动它 (没关的不重复启动)
            for it in items:
                it["running"] = str(it.get("exe") or "").lower() in running
        except Exception:
            pass
        self.start(items, grace=False)
        return True

    def drop_last(self):
        """从空闲开始的新专注: 上一段专注的提前启动不再沿用 (重新选)."""
        self.last_items = []

    def terminate(self):
        """提前终止操作空窗期: 立刻开始藏窗口 / 静音."""
        if self.active and self.phase == "grace":
            self.grace_until = 0.0
            self.phase = "active"
            self.step()

    def exe_names(self) -> set:
        """被标记的程序 exe 名 (选中的程序 + 它们派生的子进程, 整个专注期间保留): 网络 / 应用名单放行它们用 (MECHANICS 20).
        不在提前启动状态时返回空集合; 休息中 (park 之后) 不返回, 只有专注期间才有联网豁免."""
        return set(self.marked) if self.active else set()

    def hidden_exes(self) -> set:
        """触发隐藏的程序: 应用名单不关它们 (当提前启动藏起来), 但联网不因此放行."""
        return set(self.hidden_only)

    def count_pids(self) -> set:
        """窗口数上限 / 通道阻塞不数这些进程: 提前启动的 + 触发隐藏的."""
        return set(self.pids) | set(self.auto_pids)

    def _matching_title(self, title: str) -> bool:
        text = str(title or "").casefold()
        if not text:
            return False
        try:
            return any(str(t).strip().casefold() in text for t in (self.triggers() or [])
                       if str(t).strip())
        except Exception:
            return False

    def hide_triggered(self, w: dict) -> bool:
        """一个前台窗口的标题命中"触发隐藏的窗口": 藏起来并标记它的程序 (只藏, 不联网放行).
        w: window_guard.foreground_window_info() 的结果 (含 root / title / pid / exe)."""
        title = str((w or {}).get("title") or "")
        if not self._matching_title(title):
            return False
        exe = str((w or {}).get("exe") or "").strip().lower()
        pid = int((w or {}).get("pid") or 0)
        if exe and exe in _skip_exes():
            return False
        hwnd = (w or {}).get("root") or (w or {}).get("hwnd")
        if not hwnd or not (w or {}).get("visible", True) or window_guard.is_iconic(hwnd):
            return False
        if exe:
            self.hidden_only.add(exe)
        if pid:
            self.auto_pids.add(pid)
        if window_guard.hide_window(hwnd):
            self.auto_hidden.add(hwnd)
        if self.auto_pids:
            try:
                audiomute.set_muted(self.auto_pids, True)
            except Exception:
                pass
        return True

    def auto_step(self):
        """每秒: 标题命中"触发隐藏的窗口"的窗口藏起来, 并把它们的程序一直当提前启动藏着.
        只藏窗口 / 静音, 不加入联网豁免 (MECHANICS 20)."""
        patterns = [str(t).strip() for t in (self.triggers() or []) if str(t).strip()]
        if not patterns:
            return
        try:
            procs = window_guard.list_processes_full()
            windows = window_guard.list_top_windows()
        except Exception:
            return
        exe_of = {int(pid): str(exe or "").lower() for pid, _ppid, exe in procs}
        skip = _skip_exes()
        me = os.getpid()
        alive = set(exe_of)
        matched = set()
        # 标题命中的窗口: 标记程序, 藏窗口
        for w in windows:
            title = str(w.get("title") or "")
            if not self._matching_title(title):
                continue
            pid = int(w.get("pid") or 0)
            exe = exe_of.get(pid, "")
            if not exe or pid == me or exe in skip:
                continue
            matched.add(pid)
            self.hidden_only.add(exe)
            self.auto_pids.add(pid)
            hwnd = w.get("hwnd")
            if not w.get("visible") or not hwnd or hwnd in self.auto_hidden \
                    or window_guard.is_iconic(hwnd):
                continue
            if window_guard.hide_window(hwnd):
                self.auto_hidden.add(hwnd)
        # 和提前启动一样把子进程也算进来 (启动器 → 游戏这种), 之后它们开的窗口也藏
        tree = descendants((self.auto_pids & alive) | matched, procs)
        tree.discard(me)
        for pid in tree:
            exe = exe_of.get(pid, "")
            if exe and exe not in skip:
                self.hidden_only.add(exe)
                self.auto_pids.add(pid)
        self.auto_pids &= tree
        # 被标记的程序之后新开的窗口也藏 (按 exe 名认)
        hide_exes = self.hidden_only - skip
        for w in windows:
            pid = int(w.get("pid") or 0)
            if pid == me or exe_of.get(pid) not in hide_exes:
                continue
            hwnd = w.get("hwnd")
            if not w.get("visible") or not hwnd or hwnd in self.auto_hidden \
                    or window_guard.is_iconic(hwnd):
                continue
            if window_guard.hide_window(hwnd):
                self.auto_hidden.add(hwnd)
            self.auto_pids.add(pid)
        if self.auto_pids:
            try:
                audiomute.set_muted(self.auto_pids, True)
            except Exception:
                pass

    def release_auto(self):
        """去掉"触发隐藏的窗口"列表 / 关闭 OIT: 把自动藏起来的窗口放出来, 取消静音, 清掉标记."""
        for hwnd in list(self.auto_hidden):
            try:
                if window_guard.window_alive(hwnd):
                    window_guard.show_window(hwnd)
            except Exception:
                pass
        self.auto_hidden.clear()
        try:
            if self.auto_pids:
                audiomute.set_muted(self.auto_pids, False)
        except Exception:
            pass
        self.auto_pids.clear()
        self.hidden_only.clear()

    def _kept(self, exe: str) -> bool:
        exe = str(exe or "").lower()
        if not exe or exe in self._exes:
            return False
        try:
            return bool(self.keep(exe))
        except Exception:
            return False

    def _collect_new(self, procs):
        """操作空窗期里新出现了窗口的程序 (开始时不在运行的, 排除 OIT 自己和系统程序) → 记为跟踪种子."""
        if self._baseline is None:
            self._baseline = {int(pid) for pid, _p, _e in procs}
            return
        skip = _skip_exes()
        me = os.getpid()
        procs = [p for p in procs if not self._kept(p[2])]
        # 开始时就有同名程序在跑的: 标记是按 exe 名认的, 标了会连原来那个一起放行 / 藏起来, 所以不标
        old_exes = self.baseline_exes or set()
        old_exes = old_exes - self._exes
        # 不要求有任务栏窗口: java.exe 这类后台 / 无窗口的衍生进程也算 (启动器退出后链条断了也能认到)
        for pid, _ppid, exe in procs:
            if pid != me and pid not in self._baseline and exe and exe not in skip \
                    and exe not in old_exes:
                self._seeds.add(int(pid))

    def step(self):
        """每秒调一次: 找进程 (含新出现的子进程), 更新标记; 空窗期过了才藏窗口, 静音."""
        if not self.active:
            return
        try:
            procs = window_guard.list_processes_full()
        except Exception:
            return
        if self.phase == "grace":
            self._collect_new(procs)     # 空窗期里新启动的程序也标记
        self.pids, self.marked = track(procs, self._exes, self.pids | self._seeds, self.marked)
        # 应用白名单里的程序不算"提前启动的": 去掉它们的标记 / 跟踪 (专门选中的除外)
        exe_of = {int(pid): exe for pid, _p, exe in procs}
        self.marked = {e for e in self.marked if not self._kept(e)}
        self.pids = {p for p in self.pids if not self._kept(exe_of.get(p, ""))}
        self._seeds = {p for p in self._seeds if not self._kept(exe_of.get(p, ""))}
        # 被标记的程序再次开窗 (比如启动器退出后又被拉起来): 按 exe 名也认
        hide_exes = self.marked - _skip_exes()
        pids = self.pids | {int(pid) for pid, _ppid, exe in procs
                            if exe in hide_exes and pid != os.getpid()}
        if self.phase == "grace":
            if time.monotonic() < self.grace_until:
                return                   # 操作空窗期: 什么都不藏、不静音
            self.phase = "active"
        try:
            for w in window_guard.list_top_windows():
                hwnd = w.get("hwnd")
                if w.get("pid") not in pids or not w.get("visible") or hwnd in self.hidden:
                    continue
                if window_guard.is_iconic(hwnd):        # 最小化的不碰 (本来就看不见)
                    continue
                if window_guard.hide_window(hwnd):
                    self.hidden.add(hwnd)
        except Exception:
            pass
        try:
            if pids:
                audiomute.set_muted(pids, True)         # 新出现的会话也静音
        except Exception:
            pass

    def stop(self, final: bool = False):
        """结束专注: 显示藏起来的窗口 (不抢焦点), 取消静音; 标记清空.

        final=True (关闭 OIT): 连"触发隐藏的窗口"也一起放出来; 否则它们继续保持隐藏."""
        self.active = False
        self.phase = ""
        self.grace_until = 0.0
        for hwnd in list(self.hidden):
            try:
                if window_guard.window_alive(hwnd):
                    window_guard.show_window(hwnd)
            except Exception:
                pass
        self.hidden.clear()
        try:
            if self.pids:
                audiomute.set_muted(self.pids, False)
        except Exception:
            pass
        self.pids = set()
        self.marked = set()
        self.items = []
        self._seeds = set()
        self._baseline = None
        if final:
            self.release_auto()
            self.last_items = []


def _selftest():
    if sys.platform != "win32":
        return "prestart selftest OK (非 Windows, 跳过)"
    apps = scan_programs()
    assert apps and all({"name", "exe", "launch", "running"} <= set(a) for a in apps)
    assert all(a["exe"].endswith(".exe") for a in apps)
    exes = [a["exe"] for a in apps]
    assert len(exes) == len(set(exes)), "同一个 exe 只列一次"
    print(f"扫到 {len(apps)} 个程序, 其中 {sum(1 for a in apps if a['running'])} 个正在运行")
    # 只留正常程序: 安装器 / 卸载器 / 系统工具 / 驱动 / 配置项 / 运行时 都不在清单里
    for name, exe in [("Setup", "setup.exe"), ("Uninstall A", "unins000.exe"),
                      ("安装向导", "setup.exe"), ("驱动人生", "drivergenius.exe"),
                      ("控制面板", "control.exe"), ("注册表编辑器", "regedit.exe"),
                      ("pythonw3.13", "pythonw3.13.exe"), ("Node.js", "node.exe")]:
        assert not _normal(name, exe), (name, exe)
    for name, exe in [("Steam", "steam.exe"), ("微信", "weixin.exe"),
                      ("MuseScore Studio 4", "musescore4.exe")]:
        assert _normal(name, exe), (name, exe)
    for a in apps:
        low = (a["name"] + " " + a["exe"]).lower()
        assert not any(k in low for k in ("setup", "uninstall", "unins", "installer")), a
        assert a["launch"] or a["running"], "不能启动又没在跑的不要"

    # 进程树: 1 → 2 → 3, 4 无关
    procs = [(1, 0, "a.exe"), (2, 1, "b.exe"), (3, 2, "c.exe"), (4, 0, "d.exe")]
    assert descendants({1}, procs) == {1, 2, 3}
    assert descendants({4}, procs) == {4}
    assert descendants(set(), procs) == set()
    pids, marked = track(procs, {"a.exe"}, set(), set())
    assert pids == {1, 2, 3} and marked == {"a.exe", "b.exe", "c.exe"}
    procs2 = [(2, 0, "b.exe"), (3, 2, "c.exe"), (4, 0, "d.exe")]          # 启动器 (1) 退出了
    pids, marked = track(procs2, {"a.exe"}, pids, marked)
    assert pids == {2, 3} and marked == {"a.exe", "b.exe", "c.exe"}, "退出的程序仍然标记着"

    # 清单存档: 删除 / 添加都留着, 再添加会取消删除
    import tempfile
    td = tempfile.mkdtemp()
    st = ListStore(td)
    base = [{"name": "A", "exe": "a.exe", "launch": "x", "running": True},
            {"name": "B", "exe": "b.exe", "launch": "x", "running": True}]
    st.remove("a.exe")
    st.add({"name": "C", "exe": "C.exe", "launch": ""})
    st2 = ListStore(td)
    assert st2.removed == {"a.exe"} and [a["exe"] for a in st2.added] == ["c.exe"]
    assert [a["exe"] for a in st2.apply(base)] == ["b.exe"], "C 没有文件又没在跑, 不显示"
    st2.add({"name": "A", "exe": "a.exe", "launch": ""})
    assert not st2.removed and "a.exe" in {a["exe"] for a in ListStore(td).added}

    ps = Prestart()
    assert not ps.chosen
    # 用一个不存在的程序测流程: 不启动、不藏任何东西
    ps.start([{"name": "x", "exe": "oit-selftest-nonexistent.exe", "launch": "", "running": True}])
    assert ps.chosen and ps.active and ps.items
    ps.step()
    assert not ps.hidden
    ps.stop()
    assert not ps.chosen and not ps.active and not ps.hidden
    return "prestart selftest OK"


if __name__ == "__main__":
    print(_selftest())
