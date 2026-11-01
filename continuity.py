"""
数据的连续性 (MECHANICS.md 9.3): 发现数据被"非常规手段"搬走 / 倒回去 (备份还原, 整个文件夹复制到别处, 删掉连续性记录 ……) 就清掉
──────────────────────────────────────────────────────────────────────────────────────────────────────────────
OIT 的数据放在 AppData\\LocalLow\\<程序名> 里, 本来只有 OIT 自己写. 为了不让数据被备份 (拿旧的覆盖回来, 躲开已经记下的违规 / 额度 /
冷静期) 或者被搬到别的电脑上继续用, 数据里有一份"连续性记录" continuity.json, 程序每 30 秒推进一次, 并用几种互相独立的办法核对:

  1. 封印 (seal): 记录里的 `seal` 是用 Windows DPAPI (绑定这台电脑 + 这个用户) 加密的 "<世代>|<序号>". 数据被复制到别的电脑 / 别的用户 → 解不开 (foreign-seal);
     解开了内容和记录对不上 (手改过序号) → tampered-seal.
  2. 锚点 (anchor): 数据文件夹**以外**的三个地方 (注册表 HKCU\\Software\\Obliphur\\OIT, ProgramData 和 LocalAppData 里各一个文件) 同样记着 (世代, 序号), 同样用 DPAPI 封着.
     数据里的序号比锚点的小 → 倒回去了 (rollback, 拿旧备份覆盖的); 世代对不上 → 不是这次安装的数据 (other-epoch); 锚点被改坏 (anchor-tampered).
  3. 记录被删: 数据文件夹里别的文件都在, 连续性记录没了, 而锚点还在 (stripped).
  4. 同步的设备: 每台设备的状态里带着 cseq / cep (序号和世代), 其他设备会记着它最后一次发来的; 本机收到它们转来的"你上次是 cseq=…"
     比自己的序号大得多 (同一世代) → 本机的数据被倒回去了, 标记 pending (其他办法倒不回去的, 例如整盘镜像还原, 靠这个), 立即重启.
  任何一条成立, OIT 主程序**下一次启动**时 (main.py 最先做, guard()) 就把这份数据清掉 (只留系统代理的备份, 免得断网时还原不了), 弹出文本框 "<非法数据里的用户名>,覆水难收.",
  然后像第一次安装那样重新开始. 只清本机的数据, 不动别的设备 (被搬走的副本和原来的设备共用同一份绑定, 不能连累原来的).
  连续性只在装好的程序里生效 (git 仓库里的开发版本不核对, 也不写锚点). 重置 / 卸载时锚点一起清掉 (reset / clear_anchors).
  升级到有这个功能的版本时, 数据里还没有记录, 锚点也没有: 直接以现在的数据为起点, 不当作非法.
  数据文件夹是个符号链接 / 联接点 (被搬到别的盘) 只记一条日志, 不算非法 (C 盘满了把数据放到别的盘是正常用法).
  另外程序运行期间一直用独占方式打开数据文件夹里的 `.lock`: 资源管理器 / 备份软件复制文件夹时, 这个文件复制不了.

    python continuity.py       自检 (只用临时文件夹和 HKCU 下的临时项)
"""

import base64
import ctypes
import json
import os
import secrets
import shutil
import sys
import time

FILE = "continuity.json"
LOCK = ".lock"
TICK = 30                    # 秒: 序号多久推进一次
TOL = 4                      # 数据里的序号比锚点小不超过这么多不算倒回去 (崩溃 / 断电时最多少写一两次)
PEER_TOL = 20                # 其他设备记着的序号比本机的大这么多 (10 分钟) 才算倒回去
KEEP = ("sysproxy_backup.json", ".lock")       # 清数据时留下: 系统代理的备份 (网络断着时还要靠它还原), 被占着的 .lock
# 数据文件夹里有这些之一就说明"这里有用过的数据" (新装的程序文件夹里没有)
ESTABLISHED = ("profile.json", "app_settings.json", "study_log.json", "hour_log.json", "sync_state.json", "net_state.json")
REG_PATH = r"Software\Obliphur\OIT"
current = None               # 这次运行的 Continuity (guard 设置; 开发版本里是 None 的话什么都不做)
STRONG = ("foreign-seal", "tampered-seal", "other-epoch", "rollback", "stripped", "anchor-tampered", "pending", "key-lost", "key-changed")


# ---- DPAPI (绑定这台电脑 + 这个用户) ----

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def protect(text: str):
    """加密成只有这台电脑上的这个用户解得开的字节; 不是 Windows / 失败时 None."""
    if sys.platform != "win32":
        return None
    try:
        inb, keep1 = _blob(text.encode("utf-8"))
        ent, keep2 = _blob(b"OIT-continuity")
        out = _Blob()
        if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(inb), "OIT", ctypes.byref(ent), None, None, 0, ctypes.byref(out)):
            return None
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)
    except Exception:
        return None


def unprotect(blob: bytes):
    """protect 的逆: 解不开 (别的电脑 / 别的用户 / 被改过) 返回 None."""
    if sys.platform != "win32" or not blob:
        return None
    try:
        inb, keep1 = _blob(blob)
        ent, keep2 = _blob(b"OIT-continuity")
        out = _Blob()
        if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(inb), None, ctypes.byref(ent), None, None, 0, ctypes.byref(out)):
            return None
        try:
            return ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)
    except Exception:
        return None


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s) -> bytes:
    try:
        return base64.b64decode(str(s), validate=True)
    except Exception:
        return b""


def _atomic_write(path: str, data: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)


# ---- 锚点 ----

class FileAnchor:
    kind = "file"

    def __init__(self, path: str):
        self.path = path

    def read(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {"bad": True}
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return {"bad": True}

    def write(self, d: dict):
        try:
            _atomic_write(self.path, json.dumps(d))
        except OSError:
            pass

    def clear(self):
        try:
            os.remove(self.path)
        except OSError:
            pass


class RegAnchor:
    kind = "registry"

    def __init__(self, path: str = REG_PATH):
        self.path = path

    def read(self):
        if sys.platform != "win32":
            return None
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.path, 0, winreg.KEY_READ) as k:
                return {"v": winreg.QueryValueEx(k, "v")[0]}
        except FileNotFoundError:
            return None
        except OSError:
            return {"bad": True}

    def write(self, d: dict):
        if sys.platform != "win32":
            return
        import winreg
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.path, 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, "v", 0, winreg.REG_SZ, d.get("v", ""))
        except OSError:
            pass

    def clear(self):
        if sys.platform != "win32":
            return
        import winreg
        try:
            winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, self.path, 0, 0)
        except OSError:
            pass


def default_anchors():
    user = os.environ.get("USERNAME", "user")
    return [RegAnchor(),
            FileAnchor(os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Obliphur", "OIT", f"{user}.anchor")),
            FileAnchor(os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Obliphur", "OIT", "anchor.dat"))]


def _encode_anchor(ep: str, seq: int, pending: bool):
    """锚点的内容: DPAPI 封着的 "<世代>|<序号>|<pending>" (封不了 = 非 Windows, 不用锚点)."""
    blob = protect(f"{ep}|{int(seq)}|{1 if pending else 0}")
    return {"v": _b64(blob)} if blob else None


def _decode_anchor(raw):
    """→ (None 没有 / "bad" 改坏了 / (世代, 序号, pending))."""
    if raw is None:
        return None
    if raw.get("bad"):
        return "bad"
    text = unprotect(_unb64(raw.get("v")))
    if text is None:
        return "bad"
    try:
        ep, seq, pend = text.split("|")
        return ep, int(seq), pend == "1"
    except ValueError:
        return "bad"


# ---- 判断 (纯函数, 好测) ----

def evaluate(data, seal_text, anchors, established: bool, kid_now: str = ""):
    """data: continuity.json 的内容 (None = 没有 / 读不出); seal_text: 解开 seal 得到的 (解不开 None); anchors: [_decode_anchor 的结果].
    返回 (判定, [信号]). 判定: "fresh" 新数据 (没有用过的数据) / "adopt" 以现在的数据为起点 / "ok" / "break"."""
    if not established:
        return "fresh", []
    sig = []
    good = [a for a in anchors if isinstance(a, tuple)]
    if data is None:
        if good or any(a == "bad" for a in anchors):
            return "break", ["stripped"]
        return "adopt", []
    ep, seq = str(data.get("ep") or ""), int(data.get("seq") or 0)
    kid = str(data.get("kid") or "")
    if seal_text is None:
        sig.append("foreign-seal")
    elif seal_text != f"{ep}|{seq}" + (f"|{kid}" if kid else ""):
        sig.append("tampered-seal")
    if kid and not kid_now:
        sig.append("key-lost")                     # 加密密钥 (securefile.py) 没了
    elif kid and kid != kid_now:
        sig.append("key-changed")                  # 密钥被换了 (删掉密钥再把数据换成明文的)
    if any(a == "bad" for a in anchors):
        sig.append("anchor-tampered")
    for a in good:
        if a[0] != ep:
            sig.append("other-epoch")
        elif a[1] > seq + TOL:
            sig.append("rollback")
        if a[2]:
            sig.append("pending")
    sig = list(dict.fromkeys(sig))
    return ("break" if any(s in STRONG for s in sig) else "ok"), sig


# ---- 连续性 ----

class Continuity:
    def __init__(self, data_dir: str, anchors=None, log=lambda s: None):
        self.dir = data_dir
        self.path = os.path.join(data_dir, FILE)
        self.anchors = default_anchors() if anchors is None else anchors
        self.log = log
        self.ep = ""
        self.seq = 0
        self._lock_handle = None

    # -- 读写 --

    def read(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) and d.get("ep") else None
        except (OSError, ValueError):
            return None

    def established(self) -> bool:
        return os.path.exists(self.path) or any(os.path.exists(os.path.join(self.dir, n)) for n in ESTABLISHED)

    def _kid(self) -> str:
        try:
            import securefile
            return securefile.key_id()
        except Exception:
            return ""

    def _write_data(self, pending_ignored: bool = True):
        kid = self._kid()
        seal = protect(f"{self.ep}|{self.seq}" + (f"|{kid}" if kid else ""))
        d = {"v": 1, "ep": self.ep, "seq": self.seq, "ts": int(time.time() * 1000), "seal": _b64(seal) if seal else "",
             "kid": kid, "user": self.username()}
        try:
            _atomic_write(self.path, json.dumps(d))
        except OSError:
            pass

    def _write_anchors(self, pending: bool = False):
        d = _encode_anchor(self.ep, self.seq, pending)
        if d is not None:
            for a in self.anchors:
                a.write(d)

    def begin(self):
        """新的世代: 以现在的数据为起点 (第一次运行 / 重置之后 / 升级到有这个功能的版本)."""
        self.ep, self.seq = secrets.token_hex(8), 1
        self._write_data()
        self._write_anchors()

    def load(self):
        d = self.read()
        if d:
            self.ep, self.seq = str(d["ep"]), int(d.get("seq") or 0)

    def tick(self):
        """每 TICK 秒 (和关闭程序时): 序号 +1, 数据和锚点一起更新."""
        if not self.ep:
            return
        self.seq += 1
        self._write_data()
        self._write_anchors()

    def status(self) -> dict:
        """同步的状态里带给其他设备的 (它们会记着, 本机被倒回去时靠它们发现)."""
        return {"cseq": self.seq, "cep": self.ep} if self.ep else {}

    def mark_pending(self):
        """其他设备发现本机被倒回去了: 在锚点里记下, 下一次启动时 check() 判为非法."""
        self._write_anchors(pending=True)

    def peer_saw(self, st) -> bool:
        """其他设备转来的"本机上次的状态"; 同一世代里它记着的序号比本机的大得多 = 本机的数据被倒回去了. 返回是不是."""
        try:
            return bool(self.ep) and str(st.get("cep")) == self.ep and int(st.get("cseq")) > self.seq + PEER_TOL
        except (AttributeError, TypeError, ValueError):
            return False

    def reset(self):
        """重置 / 卸载: 锚点清掉 (下一次启动算新数据)."""
        for a in self.anchors:
            a.clear()
        self.ep, self.seq = "", 0

    def hold_lock(self):
        """用独占方式打开数据文件夹里的 .lock 直到程序退出: 复制整个文件夹的操作会在这个文件上失败."""
        if sys.platform != "win32" or self._lock_handle:
            return
        try:
            os.makedirs(self.dir, exist_ok=True)
            k = ctypes.windll.kernel32
            k.CreateFileW.restype = ctypes.c_void_p
            h = k.CreateFileW(os.path.join(self.dir, LOCK), 0x80000000 | 0x40000000, 0, None, 4, 0x80, None)    # 读 + 写, 不共享, OPEN_ALWAYS, NORMAL
            if h and h != ctypes.c_void_p(-1).value:
                self._lock_handle = h
        except Exception:
            pass

    # -- 启动时的核对 --

    def check(self):
        """→ (判定, 信号). 判定为 fresh / adopt 时已经建好了新的起点; ok 时已经对齐了锚点; break 时什么都没动 (调用方清数据)."""
        data = self.read()
        seal_text = None
        if data:
            seal_text = unprotect(_unb64(data.get("seal")))
        anchors = [_decode_anchor(a.read()) for a in self.anchors]
        verdict, sig = evaluate(data, seal_text, anchors, self.established(), self._kid())
        if verdict in ("fresh", "adopt"):
            self.begin()
        elif verdict == "ok":
            self.load()
            self._write_anchors()                     # 补上缺的锚点 / 对齐序号
            self.tick()
        try:
            if os.path.exists(self.dir) and (os.stat(self.dir).st_file_attributes & 0x400):
                self.log("continuity: the data folder is a link (moved to another drive)")
        except (OSError, AttributeError):
            pass
        return verdict, sig

    def username(self) -> str:
        """数据里的用户名 (弹框用). 数据被搬到别的电脑上时 profile.json 解不开, 所以每次推进时也记在 continuity.json 里."""
        try:
            import securefile
            opener = securefile.open
        except Exception:
            opener = open
        try:
            with opener(os.path.join(self.dir, "profile.json"), "r", encoding="utf-8") as f:
                name = str(json.load(f).get("username") or "").strip()
            if name:
                return name
        except (OSError, ValueError, AttributeError):
            pass
        d = self.read()
        return str(d.get("user") or "").strip() if d else ""

    def wipe(self):
        """清掉数据文件夹里的一切 (只留 KEEP), 再以空数据为新的起点. 先试着停掉录像之类写在别处的东西."""
        try:
            import recorder
            for kind in recorder.KINDS:
                shutil.rmtree(recorder.work_dir(self.dir, kind), ignore_errors=True)
            shutil.rmtree(recorder.play_dir(), ignore_errors=True)
        except Exception:
            pass
        if os.path.isdir(self.dir):
            for name in os.listdir(self.dir):
                if name in KEEP:
                    continue
                p = os.path.join(self.dir, name)
                try:
                    if os.path.isdir(p) and not os.path.islink(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        os.remove(p)
                except OSError:
                    pass
        self.reset()


def guard(data_dir: str, enabled: bool = True, anchors=None, log=lambda s: None):
    """main.py 最先调用: 核对数据. 返回 None (没问题) 或 非法数据里的用户名 (数据已经清掉, 要弹 "<用户名>,覆水难收."). 返回 (cont, 用户名或 None)."""
    global current
    c = Continuity(data_dir, anchors, log)
    if not enabled:
        return c, None
    current = c
    verdict, sig = c.check()
    if verdict != "break":
        c.hold_lock()
        return c, None
    who = c.username()
    log("continuity BREAK " + ",".join(sig))
    c.wipe()
    c.begin()
    c.hold_lock()
    return c, who


def message(username: str) -> str:
    return f"{username},覆水难收." if username else "覆水难收."


# ---- 自检 ----

def _selftest():
    import tempfile
    if sys.platform != "win32":
        assert evaluate(None, None, [], False)[0] == "fresh"
        return "continuity selftest OK (non-Windows)"
    assert unprotect(protect("abc|7")) == "abc|7" and unprotect(b"junk") is None
    # 纯判断
    A = ("e1", 10, False)
    assert evaluate(None, None, [], False) == ("fresh", [])
    assert evaluate(None, None, [], True) == ("adopt", [])
    assert evaluate(None, None, [A], True) == ("break", ["stripped"])
    d = {"ep": "e1", "seq": 10}
    assert evaluate(d, "e1|10", [A, A, None], True) == ("ok", [])
    assert evaluate(d, "e1|10", [("e1", 10 + TOL, False)], True)[0] == "ok", "差一两次不算"
    assert evaluate(d, "e1|10", [("e1", 11 + TOL, False)], True) == ("break", ["rollback"])
    assert evaluate(d, "e1|10", [("e2", 1, False)], True) == ("break", ["other-epoch"])
    assert evaluate(d, None, [None, None], True) == ("break", ["foreign-seal"])
    assert evaluate(d, "e1|99", [A], True) == ("break", ["tampered-seal"])
    assert evaluate(d, "e1|10", [A, "bad"], True) == ("break", ["anchor-tampered"])
    assert evaluate(d, "e1|10", [("e1", 10, True)], True) == ("break", ["pending"])

    tmp = tempfile.mkdtemp()
    reg = r"Software\OIT-continuity-selftest"
    try:
        def make(name):
            dd = os.path.join(tmp, name)
            os.makedirs(dd, exist_ok=True)
            return dd, [RegAnchor(reg), FileAnchor(os.path.join(tmp, name + ".a1")), FileAnchor(os.path.join(tmp, name + ".a2"))]

        dd, an = make("data")
        # 没有数据: 新的起点
        c, who = guard(dd, anchors=an)
        assert who is None and c.ep and c.seq == 1
        # 用起来 (有用户名等): 每次推进
        with open(os.path.join(dd, "profile.json"), "w", encoding="utf-8") as f:
            json.dump({"username": "小明"}, f, ensure_ascii=False)
        for _ in range(30):
            c.tick()
        ep, seq = c.ep, c.seq
        assert c.status() == {"cseq": seq, "cep": ep}
        # 正常重新打开: 没问题, 序号继续
        c2, who = guard(dd, anchors=an)
        assert who is None and c2.ep == ep and c2.seq == seq + 1
        # 备份还原: 把数据倒回 20 次之前 (锚点在数据以外, 没被还原)
        snap = {n: open(os.path.join(dd, n), "rb").read() for n in os.listdir(dd) if n != LOCK}
        for _ in range(20):
            c2.tick()
        for n, b in snap.items():
            with open(os.path.join(dd, n), "wb") as f:
                f.write(b)
        with open(os.path.join(dd, "extra.json"), "w") as f:
            f.write("{}")
        c3, who = guard(dd, anchors=an)
        assert who == "小明", "倒回去: 清掉, 用户名取自非法数据"
        assert not os.path.exists(os.path.join(dd, "profile.json")) and not os.path.exists(os.path.join(dd, "extra.json"))
        assert c3.ep != ep and c3.seq == 1
        assert message(who) == "小明,覆水难收." and message("") == "覆水难收."
        # 清掉之后是新的起点, 再打开不会再判非法
        with open(os.path.join(dd, "profile.json"), "w", encoding="utf-8") as f:
            json.dump({"username": "新"}, f)
        c3.tick()
        assert guard(dd, anchors=an)[1] is None
        # 连续性记录被删: stripped
        os.remove(os.path.join(dd, FILE))
        assert guard(dd, anchors=an)[1] == "新"
        # 别的电脑 / 别的用户 (封印解不开): 数据里的 seal 换成别人的
        with open(os.path.join(dd, "profile.json"), "w", encoding="utf-8") as f:
            json.dump({"username": "搬运"}, f)
        d0 = json.load(open(os.path.join(dd, FILE)))
        d0["seal"] = _b64(b"not-ours")
        json.dump(d0, open(os.path.join(dd, FILE), "w"))
        assert guard(dd, anchors=make("nowhere")[1])[1] == "搬运", "新电脑上没有锚点, 靠封印"
        # 其他设备的状态: 记着的序号比本机的大得多 = 倒回去了
        for a in an:
            a.clear()
        with open(os.path.join(dd, "profile.json"), "w", encoding="utf-8") as f:
            json.dump({"username": "同步"}, f)
        c4 = guard(dd, anchors=an)[0]
        assert c4.ep
        assert not c4.peer_saw({"cep": c4.ep, "cseq": c4.seq + PEER_TOL})
        assert c4.peer_saw({"cep": c4.ep, "cseq": c4.seq + PEER_TOL + 1})
        assert not c4.peer_saw({"cep": "other", "cseq": 10 ** 9}) and not c4.peer_saw(None) and not c4.peer_saw({})
        c4.mark_pending()
        assert guard(dd, anchors=an)[1] == "同步", "pending: 下一次启动清掉"
        # 重置: 锚点清掉, 之后只有重置留下的绑定: 算新数据
        c5 = guard(dd, anchors=an)[0]
        with open(os.path.join(dd, "sync_state.json"), "w") as f:
            f.write("{}")
        c5.reset()
        os.remove(os.path.join(dd, FILE))
        c6, who = guard(dd, anchors=an)
        assert who is None and c6.ep, "重置后不能判成 stripped"
        # 清数据时留下系统代理的备份
        with open(os.path.join(dd, "sysproxy_backup.json"), "w") as f:
            f.write("{}")
        c6.wipe()
        assert sorted(os.listdir(dd)) == [LOCK, "sysproxy_backup.json"], os.listdir(dd)
        # 独占锁: 别的打开方式读不了
        c7 = Continuity(dd, an)
        c7.hold_lock()
        try:
            open(os.path.join(dd, LOCK), "rb").read()
            raise AssertionError(".lock 应该被独占")
        except PermissionError:
            pass
        # 开发版本 (enabled=False): 什么都不做
        assert guard(os.path.join(tmp, "dev"), enabled=False, anchors=make("dev")[1])[1] is None
        assert not os.path.exists(os.path.join(tmp, "dev", FILE))
    finally:
        for a in an:
            a.clear()
        import winreg
        try:
            winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, reg, 0, 0)
        except OSError:
            pass
        try:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(c7._lock_handle))
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)
    return "continuity selftest OK"


if __name__ == "__main__":
    print(_selftest())
