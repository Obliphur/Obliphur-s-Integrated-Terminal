"""OIT 的临时文件都装进 %TEMP%\\OIT 一个文件夹 (tempstore.py, MECHANICS 10.6).

检查: 默认临时目录、录像 / 回放目录、安装日志都在 OIT 临时文件夹下面;
启动时清旧的逻辑真的能清一天前的东西; 自检用的 session() 退出即删; 重置 / 卸载的 clean() 删得干净.
不碰真实数据, 也不用开窗口.
"""
import os
import shutil
import tempfile
import time

import tempstore

_SYS_TEMP = tempstore._SYS_TEMP          # 系统临时目录 (tempstore 自己在改 tempfile.tempdir 之前记下的)


def _inside(p: str, sub: str) -> bool:
    return os.path.abspath(p).startswith(os.path.join(os.path.abspath(tempstore.root()), sub) + os.sep)


def test_default_temp():
    import config                                    # noqa: F401  导入时把默认临时目录指到 misc
    misc = tempstore.path("misc")
    assert os.path.abspath(tempfile.gettempdir()) == os.path.abspath(misc), tempfile.gettempdir()
    d = tempfile.mkdtemp()
    assert os.path.dirname(d) == misc, "没指定目录的 mkdtemp 也要落在 OIT 里"
    os.rmdir(d)


def test_recorder_paths():
    import recorder
    w = recorder.work_dir(r"C:\some\data", "screen")
    assert _inside(w, "rec"), w
    assert os.path.isdir(w), "录像的工作目录要自动建好"
    assert _inside(recorder.play_dir(), "rec"), recorder.play_dir()


def test_setup_log():
    import setup
    assert os.path.abspath(setup.LOG_FILE) == os.path.abspath(os.path.join(tempstore.root(), "setup", "oit-setup.log")), setup.LOG_FILE


def test_sweep():
    old = tempstore.mkdtemp("rec", prefix="old-")
    keep = tempstore.mkdtemp("rec", prefix="keep-")
    os.utime(old, (time.time() - 2 * tempstore.KEEP,) * 2)
    n = tempstore.sweep()
    assert n >= 1 and not os.path.exists(old), "一天前没动过的要清掉"
    assert os.path.isdir(keep), "新的不能清"
    os.rmdir(keep)


def test_session():
    d = tempstore.session("temp_check")              # 退出时由 atexit 删掉, 这里手动删
    try:
        assert os.path.dirname(d) == tempstore.path("misc"), d
        assert os.path.abspath(tempfile.gettempdir()) == os.path.abspath(d), "session 里默认临时目录要换成它"
        assert os.path.dirname(tempfile.mkdtemp()) == d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_clean():
    probe = tempfile.mkdtemp(dir=_SYS_TEMP)          # 拿假的根目录试, 不碰真的 %TEMP%\OIT
    saved, tempstore._SYS_TEMP = tempstore._SYS_TEMP, probe
    try:
        tempstore.path("rec", "x", "y")
        tempstore.path("misc")
        assert tempstore.clean(), "clean() 要把整个临时文件夹删掉"
        assert not os.path.exists(os.path.join(probe, "OIT"))
    finally:
        tempstore._SYS_TEMP = saved
        shutil.rmtree(probe, ignore_errors=True)


def test_legacy():
    probe = tempfile.mkdtemp(dir=_SYS_TEMP)          # 在假的临时目录里试, 不碰真的 %TEMP%
    saved, tempstore._SYS_TEMP = tempstore._SYS_TEMP, probe
    try:
        old = os.path.join(probe, "oit_old")
        new = os.path.join(probe, "oit_new")
        os.makedirs(old)
        os.makedirs(new)
        os.makedirs(os.path.join(probe, "OIT", "misc"))
        os.utime(old, (time.time() - 2 * tempstore.KEEP,) * 2)
        n = tempstore.sweep_legacy()
        assert n >= 1 and not os.path.exists(old), "旧版本散在 %TEMP% 顶层的 oit* 要清掉"
        assert os.path.isdir(new), "一天内动过的先不动"
        assert os.path.isdir(os.path.join(probe, "OIT")), "OIT 文件夹自己不能删"
    finally:
        tempstore._SYS_TEMP = saved
        shutil.rmtree(probe, ignore_errors=True)


test_default_temp()
test_recorder_paths()
test_setup_log()
test_sweep()
test_session()
test_legacy()
test_clean()
print("temp files check OK (都在 %s 里)" % tempstore.root())
