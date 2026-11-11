"""OIT 的临时文件都装进 %TEMP%\\OIT 一个文件夹 (tempstore.py, MECHANICS 10.6).

检查: 默认临时目录、录像 / 回放目录、安装日志都在 OIT 临时文件夹下面;
启动时清旧的逻辑真的能清一天前的东西; 自检用的 session() 退出即删, 开会话时清掉旧的残留;
%TEMP% 顶层老版本自检留下的 tmp?????? 只清认得出是 OIT 的; 重置 / 卸载的 clean() 删得干净.
不碰真实数据, 也不用开窗口.
"""
import json
import os
import shutil
import tempfile
import time

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempstore

_S = tempstore.session("temp_check")      # 整个检查的临时文件 (退出时删); 顺便测 session
_SYS_TEMP = tempstore._SYS_TEMP           # 系统临时目录 (tempstore 自己在改 tempfile.tempdir 之前记下的)


def _inside(p: str, sub: str) -> bool:
    return os.path.abspath(p).startswith(os.path.join(os.path.abspath(tempstore.root()), sub) + os.sep)


def test_default_temp():
    # 程序正常启动 (没有会话) 时: 默认临时目录 = OIT\misc
    saved_session, saved_dir = tempstore._session, tempfile.tempdir
    saved_auto = tempstore.autosession
    tempstore._session = ""
    tempfile.tempdir = None
    tempstore.autosession = lambda *a, **k: ""        # 自动会话在 test_autosession 里单独测
    try:
        import config                                  # noqa: F401  导入时把默认临时目录指到 misc
        assert os.path.abspath(tempfile.gettempdir()) == os.path.abspath(tempstore.path("misc")), tempfile.gettempdir()
        d = tempfile.mkdtemp()
        assert os.path.dirname(d) == tempstore.path("misc"), "没指定目录的 mkdtemp 也要落在 OIT 里"
        os.rmdir(d)
    finally:
        tempstore.autosession = saved_auto
        tempstore._session = saved_session
        tempfile.tempdir = saved_dir


def test_session_rec_dirs():
    # 会话里用过的录像工作目录 (OIT\rec\<tag>) 退出时一起删
    d = tempstore.session("rec_check")
    try:
        p = tempstore.path("rec", "tagcheck", "screen")
        assert os.path.isdir(p)
        tempstore._clean_rec_dirs()
        assert not os.path.exists(os.path.join(tempstore.root(), "rec", "tagcheck")), "空的 rec 工作目录要删"
    finally:
        tempstore._rec_dirs.clear()
        tempstore._session = _S
        tempfile.tempdir = _S


def test_orphans():
    # %TEMP% 顶层老版本 / 没走 config 的模块自检留下的 tmp?????? 目录: 只清认得出是 OIT 的
    probe = tempfile.mkdtemp(dir=_SYS_TEMP)
    saved, tempstore._SYS_TEMP = tempstore._SYS_TEMP, probe
    try:
        oit = os.path.join(probe, "tmp_oit_like")
        os.makedirs(oit)
        with open(os.path.join(oit, "r.json"), "w", encoding="utf-8") as f:
            json.dump({"created": "2026-01-01T12:00:00", "lists": {}}, f)
        other = os.path.join(probe, "tmp_other")
        os.makedirs(other)
        open(os.path.join(other, "notes.txt"), "w", encoding="utf-8").close()
        foreign = os.path.join(probe, "tmp_foreign")
        os.makedirs(foreign)
        with open(os.path.join(foreign, "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"theme": "dark"}, f)
        rec = os.path.join(probe, "rec1234567890123456789")
        os.makedirs(os.path.join(rec, "recordings", "camera"))
        open(os.path.join(rec, "p.oitv"), "wb").close()
        old = time.time() - 2 * tempstore.KEEP
        for p in (oit, other, foreign, rec):
            os.utime(p, (old, old))
        n = tempstore.sweep_orphans()
        assert n >= 2 and not os.path.exists(oit), "OIT 自检留下的 tmp 目录要清掉"
        assert not os.path.exists(rec), "旧版 recorder 的录像工作目录要清掉"
        assert os.path.isdir(other), "别人的目录不能删"
        assert os.path.isdir(foreign), "内容对不上 OIT 的 settings.json 不能删"
    finally:
        tempstore._SYS_TEMP = saved
        shutil.rmtree(probe, ignore_errors=True)


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
    d = tempstore.session("temp_check_2")              # 嵌套会话; 退出时由 atexit 删, 这里手动删
    try:
        assert os.path.dirname(d) == tempstore.path("misc"), d
        assert os.path.abspath(tempfile.gettempdir()) == os.path.abspath(d), "session 里默认临时目录要换成它"
        assert os.path.dirname(tempfile.mkdtemp()) == d
    finally:
        shutil.rmtree(d, ignore_errors=True)
        tempstore._session = _S
        tempfile.tempdir = _S


def test_session_sweeps_stale():
    # 上次被强杀 / 断电留下的测试目录: 下次开会话时清掉 (免得越攒越多)
    stale = tempstore.mkdtemp("misc", prefix="stale-")
    os.utime(stale, (time.time() - 2 * tempstore.TEST_KEEP,) * 2)
    d = tempstore.session("stale_check")
    try:
        assert not os.path.exists(stale), "开会话时要清掉一小时前的 misc 残留"
        assert os.path.isdir(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
        tempstore._session = _S
        tempfile.tempdir = _S


def test_autosession():
    saved_session, saved_dir = tempstore._session, tempfile.tempdir
    tempstore._session = ""
    d = ""
    try:
        for name in ("main.py", "setup.py", "release.py", "uninstall.py", "tempstore.py", "-c", "OIT.exe"):
            assert tempstore.autosession(name) == "", name
        d = tempstore.autosession("contract.py")
        assert d and os.path.dirname(d) == tempstore.path("misc"), d
        assert os.path.abspath(tempfile.gettempdir()) == os.path.abspath(d), "自检进程的临时文件要进会话目录"
        assert tempstore.autosession("other.py") == d, "已经有会话就不再开一个"
    finally:
        if d:
            shutil.rmtree(d, ignore_errors=True)
        tempstore._session = saved_session
        tempfile.tempdir = saved_dir


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
test_session_sweeps_stale()
test_autosession()
test_session_rec_dirs()
test_orphans()
test_legacy()
test_clean()
print("temp files check OK (都在 %s 里)" % tempstore.root())
