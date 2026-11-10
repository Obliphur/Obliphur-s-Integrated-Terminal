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
- 自检 / 检查脚本: 开头调 session("名字"), 这个进程的临时文件全在一个子目录里, 退出时整个删掉;
- 程序启动时 sweep() 清掉一天前没动过的东西, 所以它不会越攒越多.
"""

import atexit
import os
import re
import shutil
import tempfile
import time

NAME = "OIT"                     # %TEMP%\OIT
KEEP = 24 * 60 * 60              # sweep() 默认清掉一天前没动过的 (秒)
SUBDIRS = ("rec", "scan", "update", "setup", "misc")     # root() 下面按用途分的固定几层
_SYS_TEMP = tempfile.gettempdir()   # 改 tempfile.tempdir 之前先记下系统的临时目录
_session = ""                    # session() 给的本次运行目录 (有它就不再退回 misc)


def root() -> str:
    """OIT 的临时文件夹 (%TEMP%\\OIT)."""
    return os.path.join(_SYS_TEMP, NAME)


def path(*parts: str) -> str:
    """root() 下面的子目录, 不存在就建好 (可以多级: path("rec", tag, kind))."""
    p = os.path.join(root(), *parts)
    os.makedirs(p, exist_ok=True)
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


def session(tag: str) -> str:
    """自检 / 检查脚本专用的临时目录: 本次运行的临时文件都写在这里, 退出时整个删掉.

    调用之后 tempfile.mkdtemp() / mkstemp() 也落在这个目录里, 所以不用逐个改调用点.
    """
    global _session
    safe = re.sub(r"[^0-9A-Za-z_-]+", "_", str(tag or "run"))[:40].strip("_-") or "run"
    d = mkdtemp("misc", prefix=safe + "-")
    _session = d
    tempfile.tempdir = d
    atexit.register(shutil.rmtree, d, True)      # 不注册 onerror: 删不掉的留给 sweep
    return d


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


def sweep(older_than: float = KEEP) -> int:
    """删掉 OIT 临时文件夹里 older_than 秒没动过的东西, 返回删掉的个数 (程序启动时调用).

    固定子目录 (rec / scan / update / setup / misc) 自己保留, 只清里面的东西;
    正在用的文件 (录像 / 播放器占着) 多半删不掉, 留给下一次.
    """
    n = 0
    cutoff = time.time() - older_than
    try:
        names = os.listdir(root())
    except OSError:
        return 0
    for name in names:
        p = os.path.join(root(), name)
        if name in SUBDIRS and os.path.isdir(p):
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
    return n


def clean():
    """删掉整个 OIT 临时文件夹 (重置 / 卸载时用). 返回是否删干净了."""
    shutil.rmtree(root(), ignore_errors=True)
    return not os.path.exists(root())


LEGACY = re.compile(r"^oit")          # 旧版本散落在 %TEMP% 顶层的名字: oit_rec / oit_play / oit-scan-… / oit-*.zip


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


def _selftest():
    r = root()
    assert os.path.abspath(r) == os.path.join(os.path.abspath(_SYS_TEMP), NAME), r
    assert os.path.isdir(path("misc")) and os.path.isdir(path("rec", "x", "y"))

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
    assert os.path.dirname(tempfile.mkdtemp()) == path("misc"), tempfile.gettempdir()
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

    print("tempstore selftest OK (%s, 启动时清掉 %d 秒没动过的)" % (r, KEEP))


if __name__ == "__main__":
    _selftest()
