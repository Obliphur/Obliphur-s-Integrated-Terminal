"""OIT 的临时文件: 全部放进系统临时目录下的 OIT 文件夹, 不再散落 (MECHANICS 10.6).

以前这些文件各自为政地丢在 %TEMP% 里: 录像的工作文件 (oit_rec) / 回放的解密文件 (oit_play) /
浏览器扫描的临时用户目录 (oit-scan-*) / 更新包 (oit-*.zip) / 安装日志 (oit-setup.log) ……
再加上每次自检、检查脚本留下的一堆 tmp?????? 目录 (从来不删), 一段时间就能攒出几十万个文件.

现在统一成:

    %TEMP%\\OIT\\
        rec\\      录像 / 回放的工作文件 (recorder.py)
        scan\\     浏览器扫描用的临时用户目录 (netproxy/sitescan.py)
        update\\   更新包和解包目录 (updater.py, sync_local.py)
        setup\\    安装程序的日志和下载 (setup.py)
        misc\\     其它: 自检 / 检查脚本 / 头像 / 主题预览 / 历史库副本 ……

规矩 (别处要写临时文件也走这里):

- 自己用的子目录: tempstore.path("rec", tag, kind), 目录会自动建好;
- 一次性的目录: tempstore.mkdtemp("misc"), 或者直接 tempfile.mkdtemp() ——
  config.py 导入时已经把这个进程的默认临时目录指到 misc, 所以第三方库写的也跟着进来;
- 直接 `python xxx.py` 跑自检 / 工具脚本: config.py 导入时自动开 session (见 autosession),
  这个进程的临时文件全在一个子目录里, 退出时整个删掉; 检查脚本也可以开头自己调 session("名字");
- 程序启动时 sweep() 清掉一天前没动过的东西, 每次开会话时再清掉一小时前的 misc 残留
  (上次被强杀 / 断电留下的); sweep_orphans() 清 %TEMP% 顶层老版本 / 没走 config 的模块
  自检留下的 tmp?????? 目录 (只认得出是 OIT 的), 所以它不会越攒越多;
- 想看看占了多大 / 手动清空: `python tempstore.py --status` / `python tempstore.py --clean`.
"""

import atexit
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile

NAME = "OIT"                     # %TEMP%\OIT
KEEP = 24 * 60 * 60              # sweep() 默认清掉一天前没动过的 (秒)
TEST_KEEP = 60 * 60              # 开会话时清掉 misc 里一小时前没动过的 (上次崩溃留下的测试目录)
SUBDIRS = ("rec", "scan", "update", "setup", "misc")     # root() 下面按用途分的固定几层
PRODUCTION = ("main.py", "setup.py", "release.py", "uninstall.py", "tempstore.py")   # 这些入口不做自动会话
_SYS_TEMP = tempfile.gettempdir()   # 改 tempfile.tempdir 之前先记下系统的临时目录
_session = ""                    # session() 给的本次运行目录 (有它就不再退回 misc)
_rec_dirs = set()                # 会话里用过的录像工作目录 (OIT\rec\<tag>), 退出时删掉


def root() -> str:
    """OIT 的临时文件夹 (%TEMP%\\OIT)."""
    return os.path.join(_SYS_TEMP, NAME)


def path(*parts: str) -> str:
    """root() 下面的子目录, 不存在就建好 (可以多级: path("rec", tag, kind))."""
    p = os.path.join(root(), *parts)
    os.makedirs(p, exist_ok=True)
    if _session and len(parts) >= 2 and parts[0] == "rec":
        _rec_dirs.add(os.path.join(root(), "rec", parts[1]))   # 自检用完的录像工作目录: 退出时删
    return p


def mkdtemp(*parts: str, prefix: str = "tmp") -> str:
    """在 root()/parts 下面建一个临时目录 (用完自己删, 或者留给 sweep 清)."""
    return tempfile.mkdtemp(prefix=prefix, dir=path(*parts))


def mkstemp(*parts: str, prefix: str = "tmp", suffix: str = ""):
    """在 root()/parts 下面建一个临时文件, 返回 (fd, path)."""
    return tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=path(*parts))


def install_default() -> str:
    """把这个进程的默认临时目录指到 misc (config.py 导入时调用).

    已经开过 session() 的进程不覆盖. 建不出目录 (临时目录不可写) 时什么也不做, 返回 "".
    """
    if _session:
        return _session
    try:
        d = path("misc")
    except OSError:
        return ""
    tempfile.tempdir = d
    return d


def _clean_rec_dirs():
    """删掉本次会话用过的录像工作目录 (path("rec", tag, kind) 记下来的)."""
    for p in list(_rec_dirs):
        shutil.rmtree(p, ignore_errors=True)
    try:
        os.rmdir(os.path.join(root(), "rec"))       # 空了的 rec 根目录也去掉
    except OSError:
        pass


def _drop_empty_root():
    """会话目录删完之后: OIT 临时文件夹空了就一起删掉 (别留下空目录)."""
    for sub in SUBDIRS:
        try:
            os.rmdir(os.path.join(root(), sub))
        except OSError:
            pass
    try:
        os.rmdir(root())
    except OSError:
        pass


def session(tag: str) -> str:
    """自检 / 检查脚本专用的临时目录: 本次运行的临时文件都写在这里, 退出时整个删掉.

    调用之后 tempfile.mkdtemp() / mkstemp() 也落在这个目录里, 所以不用逐个改调用点.
    开会话前先把 misc 里一小时前没动过的清掉: 上次被强杀 / 断电留下的测试目录不会越攒越多.
    """
    global _session
    safe = re.sub(r"[^0-9A-Za-z_-]+", "_", str(tag or "run"))[:40].strip("_-") or "run"
    sweep(TEST_KEEP, only=("misc",))
    d = mkdtemp("misc", prefix=safe + "-")
    _session = d
    tempfile.tempdir = d
    atexit.register(_drop_empty_root)            # 最后: OIT 文件夹空了就删掉它 (空的 rec 也删)
    atexit.register(shutil.rmtree, d, True)      # 不注册 onerror: 删不掉的留给 sweep
    atexit.register(_clean_rec_dirs)             # 这次用过录像工作目录 (OIT\rec\<tag>) 也删掉
    return d


def autosession(main_name: str = None) -> str:
    """直接 `python xxx.py` 跑自检 / 工具脚本时, 自动开一个会话 (退出即删).

    正常启动 (main.py) 和 setup / release / uninstall 不开: 安装日志、更新包退出后还要能看.
    打包后的 exe 没有 __main__.__file__ (或不是 .py), 也不会开. 已经有会话时直接用它.
    """
    if _session:
        return _session
    if main_name is None:
        main_name = os.path.basename(getattr(sys.modules.get("__main__"), "__file__", "") or "")
    stem = os.path.splitext(main_name)[0]
    if not main_name.endswith(".py") or main_name in PRODUCTION or not stem:
        return ""
    return session(stem)


def _old(p: str, cutoff: float) -> bool:
    try:
        return os.path.getmtime(p) <= cutoff
    except OSError:
        return False


def _remove(p: str) -> bool:
    if os.path.isdir(p):
        shutil.rmtree(p, ignore_errors=True)
    else:
        try:
            os.remove(p)
        except OSError:
            pass
    return not os.path.exists(p)


def sweep(older_than: float = KEEP, only=None) -> int:
    """删掉 OIT 临时文件夹里 older_than 秒没动过的东西, 返回删掉的个数 (程序启动时调用).

    固定子目录 (rec / scan / update / setup / misc) 自己保留, 只清里面的东西;
    only: 只清这几个子目录 (开会话时只清 misc, 不碰正在录的 rec);
    正在用的文件 (录像 / 播放器占着) 多半删不掉, 留给下一次.
    """
    n = 0
    cutoff = time.time() - older_than
    subs = tuple(SUBDIRS if only is None else only)
    try:
        names = os.listdir(root())
    except OSError:
        return 0
    for name in names:
        p = os.path.join(root(), name)
        if name in subs:
            if os.path.isdir(p):
                try:
                    kids = os.listdir(p)
                except OSError:
                    continue
                for kid in kids:
                    c = os.path.join(p, kid)
                    if _old(c, cutoff) and _remove(c):
                        n += 1
            elif _old(p, cutoff) and _remove(p):
                n += 1
        elif only is None and _old(p, cutoff) and _remove(p):
            n += 1
    return n


def clean():
    """删掉整个 OIT 临时文件夹 (重置 / 卸载时用). 返回是否删干净了."""
    shutil.rmtree(root(), ignore_errors=True)
    return not os.path.exists(root())


LEGACY = re.compile(r"^oit")          # 旧版本散落在 %TEMP% 顶层的名字: oit_rec / oit_play / oit-scan-… / oit-*.zip

# 老版本 / 没走 config 的模块自检会在 %TEMP% 顶层留下的 tmp?????? 目录: 里面只有这些文件 / 目录.
# (python xxx.py 跑自检时不导入 config 的模块, tempfile 还落在系统临时目录里; 现在由各模块 __main__
#  里的 tempstore.autosession() 兜住, 这里清掉以前留下的.)
ORPHAN_NAMES = {
    # 规则库 / 设置 / 同步 / 其它 json
    "settings.json", "sync_state.json", "rules.json", "r.json", "m.json", "d.json", "u.json",
    "g.json", "g3.json", "n.json", "old.json", "t.json", "prestart_list.json", "hidden_windows.json",
    "alarm_config.json",
    # 音频 / 图标 / 录像 / 更新包 / 加密样本
    "alarm_play.wav", "chime.wav", "comm_ping.wav", "half.wav", "zero.wav", "break_soon.wav", "soft.wav",
    "x.png", "照片 1.png", "seg.oitv", "check.mp4", "merged.mp4", "OIT-9.9.9.bin", "pkg-9.9.9",
    "a.zip", "b.zip", "a.bin", "b.bin", "c.bin", "bad.bin", "big.bin", "no.bin",
    # 同步 / 更新自检造出来的假项目
    "app.py", "main.py", "keep.txt", "README.md",
    # 目录
    "pages", "themes", "recordings", "AppData", "behavior", "behavior2", "behavior_free", "behavior_work",
}
# 一眼就能认出是 OIT 的文件名 (不用再看内容)
ORPHAN_STRICT = {
    "seg.oitv", "OIT-9.9.9.bin", "pkg-9.9.9", "check.mp4", "merged.mp4", "recordings",
    "prestart_list.json", "hidden_windows.json", "alarm_config.json", "rules.json", "照片 1.png",
    "behavior", "behavior2", "behavior_free", "behavior_work", "AppData",
    "alarm_play.wav", "chime.wav", "comm_ping.wav", "half.wav", "zero.wav", "break_soon.wav", "soft.wav",
}
# 认不出的 json: 看内容里的顶层键是不是 OIT 的某一套
_OIT_JSON_SHAPES = (
    ("lists", "created"),        # 规则库
    ("effective",),              # settings.json
    ("device", "peers"),         # sync_state.json
    ("added", "removed"),        # prestart_list.json
    ("programs",),               # hidden_windows.json
    ("mode", "applied_mode"),    # rules.json
    ("provider", "profiles"),    # t.json (AI 配置)
    ("armed",),                  # alarm_config.json
)


def _oit_json(path: str) -> bool:
    try:
        if os.path.getsize(path) > 1 << 20:
            return False
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    if not isinstance(d, dict):
        return False
    return any(all(k in d for k in shape) for shape in _OIT_JSON_SHAPES)


def _starts(p: str, name: str, head: bytes) -> bool:
    try:
        with open(os.path.join(p, name), "rb") as f:
            return f.read(len(head)) == head
    except OSError:
        return False


def _zip_oit(path: str) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            return all(n.startswith("Obliphur-OIT-") for n in z.namelist()[:5])
    except (OSError, zipfile.BadZipFile):
        return False


def _oit_combo(p: str, kids) -> bool:
    """几个自检造出来的假文件组合 (名字太普通, 要一起看 / 看内容)."""
    s = set(kids)
    if {"app.py", "main.py", "keep.txt", "pages", "themes"} <= s:
        return True                                   # 设备同步: 假项目
    if {"bad.bin", "big.bin", "no.bin"} <= s:
        return True                                   # securefile: 损坏 / 超大 / 不存在
    if {"a.bin", "b.bin", "c.bin"} <= s:
        return _starts(p, "a.bin", b"hello world")    # securefile: 加密样本
    if {"a.zip", "b.zip"} <= s:
        return _zip_oit(os.path.join(p, "a.zip"))     # 更新包
    if s == {"x.png"}:
        try:                                          # 头像截取的自检图: 加密后的 150000 字节, 不是真 PNG
            q = os.path.join(p, "x.png")
            return os.path.getsize(q) == 150000 and not _starts(p, "x.png", b"\x89PNG")
        except OSError:
            return False
    return False


REC_ORPHAN = re.compile(r"^rec(?:-data)?\d{6,}$")     # 旧版 recorder 的工作目录: rec<数字> / rec-data<数字>


def _oit_orphan(p: str) -> bool:
    """p 是不是 OIT 自检留下的 tmp 目录: 空的, 或者条目全在名单里且有一眼能认出的东西."""
    try:
        kids = os.listdir(p)
    except OSError:
        return False
    if not kids:
        return True                                   # 空目录 (自检中断留下的)
    if any(k not in ORPHAN_NAMES for k in kids):
        return False
    if ORPHAN_STRICT & set(kids):
        return True
    if _oit_combo(p, kids):
        return True
    for k in kids:
        q = os.path.join(p, k)
        if os.path.isfile(q) and q.lower().endswith(".json") and _oit_json(q):
            return True
    return False


def _rec_orphan(p: str) -> bool:
    """p 是不是旧版 recorder 的工作目录: 空的, 或者只有 recordings/ 和 mp4 / .oitv / .recording."""
    try:
        kids = os.listdir(p)
    except OSError:
        return False
    if not kids:
        return True
    for k in kids:
        q = os.path.join(p, k)
        if os.path.isdir(q):
            if k != "recordings":
                return False
        elif not k.lower().endswith((".mp4", ".oitv", ".recording", ".tmp")):
            return False
    return True


def _is_orphan(name: str, p: str) -> bool:
    """%TEMP% 顶层的这个目录要不要清 (名字先筛一遍)."""
    if name.startswith("tmp"):
        return _oit_orphan(p)
    if REC_ORPHAN.match(name):
        return _rec_orphan(p)
    return False


def sweep_orphans(older_than: float = KEEP) -> int:
    """清掉 %TEMP% 顶层 OIT 自检 / 旧版本留下的目录, 返回删掉的个数.

    两类:
      - tmp??????: 老版本 / 没走 config 的模块自检留下的 (空的或条目全在 OIT 自检文件名单里,
        而且有一眼能认出的 OIT 文件, 或 JSON 内容对得上 OIT 的结构);
      - rec<数字> / rec-data<数字>: 旧版 recorder 的录像工作目录 (recordings/ 和 mp4 / .oitv).
    别的程序的临时目录不碰.
    """
    n = 0
    cutoff = time.time() - older_than
    try:
        names = os.listdir(_SYS_TEMP)
    except OSError:
        return 0
    for name in names:
        p = os.path.join(_SYS_TEMP, name)
        if os.path.isdir(p) and _old(p, cutoff) and _is_orphan(name, p) and _remove(p):
            n += 1
    return n


def sweep_legacy(older_than: float = KEEP) -> int:
    """删掉旧版本留在系统临时目录顶层的 oit* 条目, 返回删掉的个数.

    升级之后第一次启动时跑一遍. 以前 OIT 把临时文件直接扔在 %TEMP% 里 (oit_rec, oit_play,
    oit-scan-*, oit-*.zip, 以及自检留下的一堆 oit????? 目录), 这些现在都不用了.
    只认顶层、只认 oit 开头的名字, 而且一天内动过的先不动; OIT 自己的文件夹 (大写 OIT) 不碰.
    """
    n = 0
    cutoff = time.time() - older_than
    try:
        names = os.listdir(_SYS_TEMP)
    except OSError:
        return 0
    for name in names:
        if name == NAME or not LEGACY.match(name):
            continue
        p = os.path.join(_SYS_TEMP, name)
        if _old(p, cutoff) and _remove(p):
            n += 1
    return n


def report() -> list:
    """每个固定子目录的占用: [(名字, 文件数, 字节数)], 给 --status 用."""
    out = []
    for name in SUBDIRS:
        p = os.path.join(root(), name)
        n = b = 0
        for dirpath, _dirs, files in os.walk(p):
            for f in files:
                try:
                    b += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    continue
                n += 1
        out.append((name, n, b))
    return out


def _status():
    print("OIT temp: %s" % root())
    if not os.path.isdir(root()):
        print("  (没有)")
    else:
        for name, n, b in report():
            print("  %-7s %7d files  %9.1f MB" % (name + os.sep, n, b / 1e6))
    n = b = 0
    try:
        names = os.listdir(_SYS_TEMP)
    except OSError:
        names = []
    for name in names:
        p = os.path.join(_SYS_TEMP, name)
        if not os.path.isdir(p) or not _is_orphan(name, p):
            continue
        n += 1
        for dirpath, _dirs, files in os.walk(p):
            for f in files:
                try:
                    b += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
    print("  %-7s %7d dirs   %9.1f MB  (%%TEMP%% 顶层的自检 / 旧版残留)" % ("tmp*/rec*", n, b / 1e6))


def _selftest():
    r = root()
    assert os.path.abspath(r) == os.path.join(os.path.abspath(_SYS_TEMP), NAME), r
    assert os.path.isdir(path("misc")) and os.path.isdir(path("rec", "x", "y"))
    shutil.rmtree(os.path.join(r, "rec", "x"), ignore_errors=True)      # 自检自己建的目录, 用完删掉

    d = mkdtemp("misc", prefix="selftest-")
    assert os.path.dirname(d) == path("misc"), d
    f = os.path.join(d, "a.txt")
    open(f, "w", encoding="utf-8").close()
    fd, p = mkstemp("misc", prefix="selftest-", suffix=".zip")
    os.close(fd)
    assert os.path.dirname(p) == path("misc") and os.path.exists(f)

    # sweep 只清老的, 新的留着
    old = mkdtemp("misc", prefix="selftest-old-")
    os.utime(old, (time.time() - 2 * KEEP,) * 2)
    n = sweep()
    assert n >= 1 and not os.path.exists(old), (n, old)
    assert os.path.isdir(d) and os.path.exists(p), "新的不该被删"

    # install_default: 默认临时目录进 misc
    tempfile.tempdir = None
    assert install_default() == path("misc")
    d0 = tempfile.mkdtemp()
    assert os.path.dirname(d0) == path("misc"), tempfile.gettempdir()
    os.rmdir(d0)
    fd2, p2 = tempfile.mkstemp()
    os.close(fd2)
    assert os.path.dirname(p2) == path("misc")
    os.remove(p2)

    # session: 本次运行的临时文件单独一个目录, 退出时删 (这里手动模拟)
    s = session("tempstore-selftest")
    assert os.path.dirname(s) == path("misc"), s
    assert tempfile.gettempdir() == s, "session 之后默认临时目录要换成它"
    t = tempfile.mkdtemp()
    assert os.path.dirname(t) == s, t
    assert install_default() == s, "有 session 时不该退回 misc"
    shutil.rmtree(s, ignore_errors=True)

    shutil.rmtree(d, ignore_errors=True)
    os.remove(p)

    # 旧版本散落在 %TEMP% 顶层的东西: 在假的临时目录里试, 不碰真的 %TEMP%
    probe = tempfile.mkdtemp(dir=_SYS_TEMP)
    saved = globals()["_SYS_TEMP"]
    try:
        old_legacy = os.path.join(probe, "oit_selftest_legacy")
        fresh_legacy = os.path.join(probe, "oit_selftest_fresh")
        os.makedirs(old_legacy)
        os.makedirs(fresh_legacy)
        os.makedirs(os.path.join(probe, NAME, "misc"))
        os.utime(old_legacy, (time.time() - 2 * KEEP,) * 2)
        globals()["_SYS_TEMP"] = probe
        n = sweep_legacy()
        assert n >= 1 and not os.path.exists(old_legacy), (n, old_legacy)
        assert os.path.isdir(fresh_legacy), "一天的先不动"
        assert os.path.isdir(os.path.join(probe, NAME)), "OIT 文件夹自己不能删"
    finally:
        globals()["_SYS_TEMP"] = saved
        shutil.rmtree(probe, ignore_errors=True)

    # session 开会话时先清 misc 里一小时前的残留 (上次被强杀 / 断电留下的测试目录)
    stale = mkdtemp("misc", prefix="selftest-stale-")
    os.utime(stale, (time.time() - 2 * TEST_KEEP,) * 2)
    s2 = session("stale-check")
    try:
        assert not os.path.exists(stale), stale
    finally:
        shutil.rmtree(s2, ignore_errors=True)
        globals()["_session"] = ""
        tempfile.tempdir = path("misc")

    # autosession: 自检 / 工具脚本自动开会话, 正常启动和安装 / 发布不开
    saved_dir = tempfile.tempdir
    a = ""
    try:
        assert autosession("main.py") == "" and autosession("setup.py") == ""
        assert autosession("release.py") == "" and autosession("uninstall.py") == ""
        assert autosession("-c") == "", "python -c 没有 __file__, 不开会话"
        a = autosession("contract.py")
        assert a and os.path.dirname(a) == path("misc"), a
        assert autosession("other.py") == a, "已经有会话就不再开一个"
        assert os.path.abspath(tempfile.gettempdir()) == os.path.abspath(a)
    finally:
        shutil.rmtree(a, ignore_errors=True)
        globals()["_session"] = ""
        tempfile.tempdir = saved_dir

    # %TEMP% 顶层的自检残留 (老版本 / 没走 config 的模块留下的 tmp??????): 只删认得出是 OIT 的
    probe = tempfile.mkdtemp(dir=_SYS_TEMP)
    saved = globals()["_SYS_TEMP"]
    try:
        globals()["_SYS_TEMP"] = probe
        oit = os.path.join(probe, "tmp_oit_like")
        os.makedirs(oit)
        with open(os.path.join(oit, "r.json"), "w", encoding="utf-8") as f:
            json.dump({"created": "2026-01-01T12:00:00", "lists": {}}, f)
        empty = os.path.join(probe, "tmp_empty")
        os.makedirs(empty)
        other = os.path.join(probe, "tmp_other")
        os.makedirs(other)
        open(os.path.join(other, "notes.txt"), "w", encoding="utf-8").close()
        foreign = os.path.join(probe, "tmp_foreign")
        os.makedirs(foreign)
        with open(os.path.join(foreign, "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"theme": "dark"}, f)
        rec = os.path.join(probe, "rec1234567890123456789")
        os.makedirs(os.path.join(rec, "recordings", "camera"))
        open(os.path.join(rec, "p.mp4"), "wb").close()
        open(os.path.join(rec, "p.oitv"), "wb").close()
        foreign_rec = os.path.join(probe, "rec9999999999999999999")
        os.makedirs(foreign_rec)
        open(os.path.join(foreign_rec, "notes.txt"), "w", encoding="utf-8").close()
        old = time.time() - 2 * KEEP
        for p_ in (oit, empty, other, foreign, rec, foreign_rec):
            os.utime(p_, (old, old))
        n = sweep_orphans()
        assert n >= 3 and not os.path.exists(oit) and not os.path.exists(empty), (n, oit)
        assert not os.path.exists(rec), "旧版 recorder 的工作目录要清掉"
        assert os.path.isdir(other), "别人的目录不能删"
        assert os.path.isdir(foreign), "内容对不上 OIT 的 settings.json 不能删"
        assert os.path.isdir(foreign_rec), "名字像但不是录像内容的不能删"
    finally:
        globals()["_SYS_TEMP"] = saved
        shutil.rmtree(probe, ignore_errors=True)

    for sub in SUBDIRS:                          # 自检完了: 空的都删掉, 不留空目录
        try:
            os.rmdir(os.path.join(r, sub))
        except OSError:
            pass
    try:
        os.rmdir(r)
    except OSError:
        pass

    print("tempstore selftest OK (%s, 启动时清掉 %d 秒没动过的)" % (r, KEEP))


if __name__ == "__main__":
    if "--clean" in sys.argv:
        ok = clean()
        n = sweep_orphans(0)
        print("OIT temp cleaned" if ok else "OIT temp: 有程序正在用, 下次再试")
        print("顶层自检残留清掉 %d 个" % n)
    elif "--status" in sys.argv:
        _status()
    else:
        _selftest()
