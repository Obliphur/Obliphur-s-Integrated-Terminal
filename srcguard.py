"""
源码被"非更新"的形式改动 (MECHANICS.md 9.7): 清一次数据
──────────────────────────────────────────────────────
OIT 是 Python 源码, 装在程序文件夹里. 要是有人直接改源码 (去掉限制, 绕开核对 ……) 而不是通过更新, 就清一次数据.

程序文件 = 程序文件夹里的 .py (跳过 __pycache__, 版本库, 安卓版, 封闭测试数据 ……). 合法的改动只有两种:
  · 更新 (updater.install_zip, 包括其他设备发来的安装包): 覆盖完马上把新的清单记下来 (record);
  · 重新运行 setup.exe: 解压完用 DPAPI 封一个"令牌" (issue_token: 现在这些文件的指纹, 只有这台电脑上的这个用户做得出来),
    OIT 下次启动时发现程序文件和清单对不上, 但指纹和令牌一致 → 是安装, 接受, 记下新清单, 令牌作废.

清单 (每个文件的 SHA-256) 记在数据文件夹的 source_manifest.json 里 (securefile 加密 + 校验, 改不了, 删不了, 倒不回去).
  · 启动时 (main.py, 数据核对之前): verify() 对比; 对不上又没有令牌 → 数据核对多一个信号 "source-modified" (和数据被搬走一样: 清数据, 弹出 "<用户名>,覆水难收.");
    清完以现在的源码为新的基准 (settle → record), 所以是"清一次", 不是每次启动都清.
  · 运行中 (app.py 每 5 分钟): running_check() 对比内存里的清单, 对不上 (隔 20 秒再看一次, 免得撞上正在更新) → 在锚点里标记 pending, 立即重启, 下次启动清数据.
  · 第一次运行 (没有清单, 升级到有这个功能的版本 / 清过数据 / 重置之后): 以现在的程序文件为起点 (adopt).
  · 清单文件在却读不出来 / 被删 (索引里有): 当作被改过.
  git 仓库里的开发版本不检查. 局限: 源码是明文的, 能改源码的人可以同时改掉这个检查; 这里防的是随手改一两个文件.

    python srcguard.py       自检 (临时文件夹)
"""

import base64
import hashlib
import json
import os
import sys
import threading
import time

MANIFEST = "source_manifest.json"
TOKEN = "src_update.dat"
SKIP_DIRS = {"Android version", ".git", ".github", "__pycache__", ".vscode", ".idea", "closed_test", "build", "dist", "_review"}
EXTS = (".py", ".pyw")
RECHECK = 20.0                     # 秒: 运行中发现对不上, 隔这么久再看一次

program = None                     # configure 之后: 程序文件夹 / 数据文件夹
data = None
_manifest = None                   # 内存里的清单 {相对路径: sha256}
updating = False                   # 更新正在覆盖文件
_lock = threading.RLock()


def enabled() -> bool:
    return program is not None


def scan(root: str) -> dict:
    """程序文件夹里所有 .py 的 SHA-256: {相对路径 (用 /): 十六进制}. 读不了的文件 (被占用) 值是 None, 对比时不算."""
    out = {}
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for f in sorted(filenames):
            if not f.endswith(EXTS):
                continue
            p = os.path.join(dirpath, f)
            rel = os.path.relpath(p, root).replace("\\", "/")
            try:
                with open(p, "rb") as fh:
                    out[rel] = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                out[rel] = None
    return out


def digest(files: dict) -> str:
    h = hashlib.sha256()
    for k in sorted(files):
        h.update(k.encode("utf-8") + b"\0" + str(files[k]).encode("ascii") + b"\n")
    return h.hexdigest()


def diff(old: dict, new: dict) -> list:
    out = []
    for p in sorted(set(old) | set(new)):
        if p not in old:
            out.append("added:" + p)
        elif p not in new:
            out.append("removed:" + p)
        elif new[p] is not None and old[p] != new[p]:
            out.append("changed:" + p)
    return out


# ---- 清单 ----

def _manifest_path(data_dir: str) -> str:
    return os.path.join(data_dir, MANIFEST)


def load_manifest(data_dir: str):
    """None = 还没有清单 (第一次); {} = 有但读不出来 (一定对不上); 否则 {路径: 指纹}."""
    import securefile
    path = _manifest_path(data_dir)
    if not os.path.exists(path):
        # 清单被删了: 索引里还记着它就是被删的 (清数据 / 重置时索引一起清掉, 那时不算)
        return {} if securefile.known(MANIFEST) else None
    try:
        with securefile.open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        files = d.get("files")
        return files if isinstance(files, dict) and files else {}
    except (OSError, ValueError, AttributeError):
        return {}


def record(program_dir: str = None, data_dir: str = None) -> dict:
    """把现在的程序文件记成清单 (更新完 / 接受安装 / 清数据之后 / 第一次运行). 令牌作废."""
    global _manifest
    import securefile
    program_dir, data_dir = program_dir or program, data_dir or data
    files = {k: v for k, v in scan(program_dir).items() if v is not None}
    with _lock:
        os.makedirs(data_dir, exist_ok=True)
        try:
            import config
            ver = config.APP_VERSION
        except Exception:
            ver = ""
        with securefile.open(_manifest_path(data_dir), "w", encoding="utf-8") as f:
            json.dump({"v": 1, "ver": ver, "at": int(time.time()), "files": files}, f)
        _manifest = files
    _clear_token()
    return files


# ---- 令牌 (setup.exe 装好之后留给 OIT 的: "现在这些文件是我装的") ----

def token_path() -> str:
    return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Obliphur", "OIT", TOKEN)


def issue_token(program_dir: str) -> bool:
    """安装 / 修复之后 (setup.py): 用 DPAPI 封一个现在这些程序文件的指纹."""
    import continuity
    blob = continuity.protect("src|" + digest(scan(program_dir)))
    if not blob:
        return False
    p = token_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="ascii") as f:
        f.write(base64.b64encode(blob).decode("ascii"))
    return True


def _token_digest():
    import continuity
    try:
        with open(token_path(), "r", encoding="ascii") as f:
            text = continuity.unprotect(continuity._unb64(f.read().strip()))
        return text.split("|", 1)[1] if text and text.startswith("src|") else None
    except (OSError, IndexError):
        return None


def _clear_token():
    try:
        os.remove(token_path())
    except OSError:
        pass


# ---- 检查 ----

def verify(program_dir: str, data_dir: str):
    """启动时, 数据核对之前 (只读). → (状态, 对不上的文件): "ok" / "adopt" (没有清单) / "updated" (令牌对得上: 是安装) / "modified"."""
    files = scan(program_dir)
    old = load_manifest(data_dir)
    if old is None:
        return "adopt", []
    ch = diff(old, files)
    if not ch:
        return "ok", []
    td = _token_digest()
    if td and td == digest(files):
        return "updated", ch
    return "modified", ch


def configure(program_dir: str, data_dir: str):
    """开始守着这个程序文件夹 (main.py, 核对和 settle 之后); 把清单读进内存."""
    global program, data, _manifest
    program, data = os.path.abspath(program_dir), data_dir
    m = load_manifest(data_dir)
    _manifest = m if m else None


def settle(status: str):
    """verify 的结果落实: 没有清单 / 接受的安装 / 清过数据 → 记下现在的程序文件为新的基准."""
    if status != "ok" and enabled():
        record()


def begin_update():
    global updating
    updating = True


def end_update(target: str):
    """更新覆盖完文件之后 (不管成功没有, 文件已经被动过): 记下新的清单."""
    global updating
    try:
        if enabled() and os.path.normcase(os.path.abspath(target)) == os.path.normcase(program):
            record()
    except Exception:
        pass
    finally:
        updating = False


def running_check() -> list:
    """运行中 (每 5 分钟, 后台线程): 程序文件和内存里的清单对不上 → 返回对不上的文件; 隔 RECHECK 秒再看一次还对不上才算."""
    if not enabled() or _manifest is None:
        return []
    ch = []
    for attempt in range(2):
        if updating:
            return []
        ch = diff(_manifest, scan(program))
        if not ch:
            return []
        if attempt == 0:
            time.sleep(RECHECK)
    return [] if updating else ch


# ---- 自检 ----

def _selftest():
    import shutil
    import tempfile
    global program, data, _manifest, updating, RECHECK
    if sys.platform != "win32":
        return "srcguard selftest OK (non-Windows)"
    import securefile
    saved = (program, data, _manifest, updating, RECHECK)
    saved_sf = (securefile._dir, securefile._key, securefile._strict, securefile._idx, securefile._on_tamper)
    real_local = os.environ.get("LOCALAPPDATA")
    t = tempfile.mkdtemp()
    os.environ["LOCALAPPDATA"] = os.path.join(t, "local")
    try:
        prog, dd = os.path.join(t, "prog"), os.path.join(t, "data")
        os.makedirs(os.path.join(prog, "sub"))
        os.makedirs(os.path.join(prog, "__pycache__"))
        os.makedirs(os.path.join(prog, ".git"))
        for name, text in (("main.py", "print(1)\n"), ("app.py", "x = 1\n"), ("sub/m.py", "y = 2\n"),
                           ("__pycache__/a.py", "ignored"), (".git/hook.py", "ignored"), ("note.txt", "not code")):
            with open(os.path.join(prog, name), "w") as f:
                f.write(text)
        sc = scan(prog)
        assert sorted(sc) == ["app.py", "main.py", "sub/m.py"], sc
        os.makedirs(dd)
        securefile.configure(dd)
        securefile.ensure_key()
        # 第一次: 没有清单 → adopt, 记下后 ok
        assert verify(prog, dd) == ("adopt", [])
        program, data = os.path.abspath(prog), dd
        settle("adopt")
        assert verify(prog, dd) == ("ok", []) and os.path.exists(os.path.join(dd, MANIFEST))
        assert b"main.py" not in open(os.path.join(dd, MANIFEST), "rb").read(), "清单是加密的"
        # 直接改源码 / 加文件 / 删文件 → modified
        with open(os.path.join(prog, "app.py"), "a") as f:
            f.write("# edit\n")
        st, ch = verify(prog, dd)
        assert st == "modified" and ch == ["changed:app.py"], (st, ch)
        with open(os.path.join(prog, "evil.py"), "w") as f:
            f.write("pass\n")
        os.remove(os.path.join(prog, "sub", "m.py"))
        st, ch = verify(prog, dd)
        assert st == "modified" and ch == ["changed:app.py", "added:evil.py", "removed:sub/m.py"], ch
        # 清数据之后以现在的源码为新基准: 只清一次
        securefile.reset()
        for n in os.listdir(dd):
            os.remove(os.path.join(dd, n))
        securefile.configure(dd)
        assert verify(prog, dd)[0] == "adopt", "清过数据: 没有清单"
        securefile.ensure_key()
        settle("modified")
        assert verify(prog, dd) == ("ok", [])
        # 更新: 覆盖文件, end_update 记下新清单
        begin_update()
        assert updating and running_check() == []
        with open(os.path.join(prog, "main.py"), "w") as f:
            f.write("print(2)\n")
        end_update(prog)
        assert not updating and verify(prog, dd) == ("ok", [])
        # setup.exe 重新安装: 文件变了, 有令牌 → updated, 不算改动; 令牌用过就作废
        with open(os.path.join(prog, "main.py"), "w") as f:
            f.write("print(3)\n")
        assert verify(prog, dd)[0] == "modified"
        assert issue_token(prog) and verify(prog, dd)[0] == "updated"
        with open(os.path.join(prog, "main.py"), "a") as f:     # 令牌之后又改了: 令牌对不上
            f.write("# after\n")
        assert verify(prog, dd)[0] == "modified"
        os.remove(os.path.join(prog, "main.py"))
        with open(os.path.join(prog, "main.py"), "w") as f:
            f.write("print(3)\n")
        settle("updated")
        assert not os.path.exists(token_path()) and verify(prog, dd) == ("ok", [])
        # 清单被改坏 / 被删: 当作改过
        mp = os.path.join(dd, MANIFEST)
        good = open(mp, "rb").read()
        bad = bytearray(good)
        bad[40] ^= 1
        open(mp, "wb").write(bytes(bad))
        securefile.configure(dd)
        assert verify(prog, dd)[0] == "modified"
        open(mp, "wb").write(good)
        securefile.configure(dd)
        os.remove(mp)
        assert verify(prog, dd)[0] == "modified", "清单被删了"
        open(mp, "wb").write(good)
        securefile.configure(dd)
        assert verify(prog, dd) == ("ok", [])
        # 运行中: 对不上 (隔一会儿还对不上) 才报; 正在更新时不报
        configure(prog, dd)
        RECHECK = 0.05
        assert running_check() == []
        with open(os.path.join(prog, "app.py"), "a") as f:
            f.write("# runtime edit\n")
        assert running_check() == ["changed:app.py"]
        updating = True
        assert running_check() == []
        updating = False
        record()
        assert running_check() == []
        # 读不了的文件 (被占用) 不算改动
        assert diff({"a.py": "1"}, {"a.py": None}) == []
    finally:
        program, data, _manifest, updating, RECHECK = saved
        securefile._dir, securefile._key, securefile._strict, securefile._idx, securefile._on_tamper = saved_sf
        del securefile.tampered[:]
        if real_local is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = real_local
        shutil.rmtree(t, ignore_errors=True)
    return "srcguard selftest OK"


if __name__ == "__main__":
    print(_selftest())
