"""
数据和设置的加密 (MECHANICS.md 9.4): 数据文件夹里的 .json 都是加密 + 带校验的, 改不了, 删不了, 倒不回去
──────────────────────────────────────────────────────────────────────────────────────────────────────────
数据文件夹 (AppData\\LocalLow\\<程序名>) 顶层的 .json 文件 (设置, 记录, 名单, 合约, 同步的绑定 ……) 一律存成加密信封, 不是明文:

    "OITE1" | 随机数 nonce (16) | 写入计数 ctr (8) | 密文 | HMAC-SHA256 (32)

  · 加密: 流密码 SHAKE-256(密钥 ‖ nonce) 异或明文 (标准库没有 AES, 这是密钥化的 XOF); 校验: HMAC-SHA256 先加密后校验,
    校验的内容包括文件名 (把一个文件的内容拷到另一个文件名下会校验不过) 和计数.
  · 密钥: 32 字节随机数, 用 Windows DPAPI (绑定这台电脑 + 这个用户, 同 continuity.py) 封着放在 `oit.key`. 数据被拷到别的电脑 → 解不开.
  · 计数索引 `secure.idx` (同样是信封) 记着每个文件最近一次写入的计数: 文件被换成旧版本 (计数变小), 或者被删掉 (索引里有, 文件没了) 都能发现.
  · 校验不过 / 变成了明文 / 没有密钥 / 被删 / 被倒回 → 记为篡改 (tampered): 这个文件按"不存在"处理 (调用方本来就处理损坏的文件),
    并通知 on_tamper (main.py 把它接到连续性: 在锚点里标记 pending, 重启, 下一次启动时清掉数据, 见 continuity.py / MECHANICS 9.3).
  · 例外 (明文): sysproxy_backup.json (断网时还原系统代理要靠它, 密钥丢了也不能连累网络), continuity.json (自己有 DPAPI 封印).
  · 从旧版本升级: 第一次生成密钥时 (ensure_key) 把现有的明文 .json 全部转成加密的; 之后再出现明文的 .json 就是篡改.
  · git 仓库里的开发版本不加密 (main.py 不调用 configure), 方便开发和调试.
  断电 / 崩溃 (MECHANICS 9.5): 写盘是 "写临时文件 → fsync → 改名 (WRITE_THROUGH)", 先文件后索引, 完好的旧文件留一份 .bak.
  真的读到了半截的文件 (空的 / 全是 0 / 信封被截断), 或者上一次没有正常结束 (continuity.unclean) 时读到坏的文件: 先退回 .bak,
  没有就当作文件是空的 —— 都**不记篡改, 不会引起清数据**. 信封完整却校验不过 (在一次正常结束之后) 仍然是篡改.
  局限: 密钥在这台电脑的这个用户手里, 有本事的人可以自己调 DPAPI 解开再重写 —— 防的是用记事本改 json, 拿旧备份覆盖, 删文件.
  程序本身是 Python 源码, 能改代码的人什么都改得了, 这里不防.

    python securefile.py       自检 (只用临时文件夹)
"""

import builtins
import hashlib
import hmac
import io
import json
import os
import secrets
import sys
import threading

import continuity

MAGIC = b"OITE1"
KEYFILE = "oit.key"
IDXFILE = "secure.idx"
PLAIN_NAMES = ("sysproxy_backup.json", "continuity.json")     # 不加密的 .json

_dir = None                  # 数据文件夹 (configure 之后才加密)
_key = None                  # 32 字节的主密钥 (None = 还没有)
_strict = False              # True: 数据文件夹里出现明文 .json 算篡改 (ensure_key 之后)
_idx = None                  # {文件名: 最近一次写入的计数}
_lock = threading.RLock()
_on_tamper = None
tampered = []                # [(文件名, 原因)] 这次运行里发现的篡改


class _Plain(Exception):
    pass


def enabled() -> bool:
    return _dir is not None


def configure(data_dir: str, on_tamper=None):
    """开始管理这个数据文件夹: 已有密钥就读进来 (不会生成新的, 生成见 ensure_key). 其他进程 (联动程序, 卸载程序) 也这样用."""
    global _dir, _key, _strict, _idx, _on_tamper
    with _lock:
        _dir, _on_tamper, _idx = os.path.abspath(data_dir), on_tamper, None
        del tampered[:]                                  # 重新开始管理: 以前的记录作废
        _key = _read_key()
        _strict = _key is not None


def _read_key():
    base = os.path.join(_dir, KEYFILE)
    raw = b""
    try:
        with builtins.open(base, "rb") as f:
            raw = f.read()
        text = continuity.unprotect(continuity._unb64(raw.decode("ascii", "replace").strip()))
        if text:
            return bytes.fromhex(text)
    except (OSError, ValueError):
        pass
    if continuity.unclean or not raw.strip(b"\x00 \r\n"):      # 密钥文件是半截的 / 空的 (断电), 或上一次没有正常结束: 用上一份
        try:
            with builtins.open(base + ".bak", "rb") as f:
                text = continuity.unprotect(continuity._unb64(f.read().decode("ascii", "replace").strip()))
            if text:
                return bytes.fromhex(text)
        except (OSError, ValueError):
            pass
    return None


def key_id() -> str:
    """密钥的指纹 (连续性记录用它发现密钥被换): 没有密钥时 ""."""
    return hashlib.sha256(b"kid" + _key).hexdigest()[:12] if _key else ""


def known(name: str) -> bool:
    """索引里记着这个文件 (写过它): 它不在了就是被删的."""
    with _lock:
        return _dir is not None and _key is not None and name in _load_idx()


def has_key_file() -> bool:
    return _dir is not None and os.path.exists(os.path.join(_dir, KEYFILE))


def ensure_key() -> bool:
    """没有密钥就生成一个, 并把现有的明文 .json 全部转成加密的 (旧版本升级 / 重置之后留下的绑定). 返回是不是这次生成的.
    DPAPI 用不了 (不是 Windows) 时什么都不做."""
    global _key, _strict
    with _lock:
        if _dir is None or _key is not None:
            return False
        key = secrets.token_bytes(32)
        blob = continuity.protect(key.hex())
        if not blob:
            return False
        os.makedirs(_dir, exist_ok=True)
        path = os.path.join(_dir, KEYFILE)
        continuity.durable_write(path + ".bak", continuity._b64(blob).encode("ascii"))     # 密钥不会再变: 备份和正本一样
        continuity.durable_write(path, continuity._b64(blob).encode("ascii"))
        _key, _strict = key, False                  # 迁移期间明文照样读
        try:
            for name in sorted(os.listdir(_dir)):
                if _managed_name(name) and os.path.isfile(os.path.join(_dir, name)):
                    with builtins.open(os.path.join(_dir, name), "rb") as f:
                        raw = f.read()
                    if not raw.startswith(MAGIC):
                        _commit(os.path.join(_dir, name), name, raw)
        finally:
            _strict = True
        return True


def _managed_name(name: str) -> bool:
    return name.endswith(".json") and name not in PLAIN_NAMES


def managed(path) -> bool:
    """这个路径是被加密管理的数据文件吗."""
    return _name_of(os.fspath(path)) is not None and _key is not None


def reset():
    """重置之后 (数据文件夹刚被删掉): 内存里的密钥和索引作废, 之后写的 (devsync.write_state 留下的绑定) 是明文, 下次启动时 ensure_key 转成加密的."""
    global _key, _strict, _idx
    with _lock:
        _key, _strict, _idx = None, False, None
        del tampered[:]


def _name_of(path: str):
    """受管理的文件返回 (文件名), 其他返回 None. 写到 xxx.json.tmp 再改名的, 算 xxx.json 的."""
    if _dir is None:
        return None
    p = os.path.abspath(path)
    if p.endswith(".tmp"):
        p = p[:-4]
    if os.path.normcase(os.path.dirname(p)) != os.path.normcase(_dir):
        return None
    name = os.path.basename(p)
    return name if _managed_name(name) else None


# ---- 信封 ----

def _keys():
    return (hmac.new(_key, b"enc", hashlib.sha256).digest(), hmac.new(_key, b"mac", hashlib.sha256).digest())


def _xor(data: bytes, ks: bytes) -> bytes:
    return (int.from_bytes(data, "big") ^ int.from_bytes(ks, "big")).to_bytes(len(data), "big") if data else b""


def seal(name: str, ctr: int, plain: bytes) -> bytes:
    ke, km = _keys()
    nonce = secrets.token_bytes(16)
    ct = _xor(plain, hashlib.shake_256(ke + nonce).digest(len(plain)))
    head = MAGIC + nonce + ctr.to_bytes(8, "big")
    mac = hmac.new(km, head + name.encode("utf-8") + b"\0" + ct, hashlib.sha256).digest()
    return head + ct + mac


def unseal(name: str, raw: bytes):
    """→ (计数, 明文); 校验不过抛 ValueError."""
    if len(raw) < len(MAGIC) + 16 + 8 + 32 or not raw.startswith(MAGIC):
        raise ValueError("not an envelope")
    ke, km = _keys()
    head, ct, mac = raw[:29], raw[29:-32], raw[-32:]
    if not hmac.compare_digest(mac, hmac.new(km, head + name.encode("utf-8") + b"\0" + ct, hashlib.sha256).digest()):
        raise ValueError("bad mac")
    nonce = head[5:21]
    return int.from_bytes(head[21:29], "big"), _xor(ct, hashlib.shake_256(ke + nonce).digest(len(ct)))


def _tamper(name: str, why: str):
    tampered.append((name, why))
    cb = _on_tamper
    if cb:
        try:
            cb(name, why)
        except Exception:
            pass


# ---- 计数索引 ----

def _valid(name: str):
    def ok(raw: bytes) -> bool:
        try:
            unseal(name, raw)
            return True
        except ValueError:
            return False
    return ok


def _torn(raw: bytes) -> bool:
    """断电留下的半截文件的样子: 空的 / 全是 0 / 信封被截断 或 末尾补着 0."""
    if not raw or not raw.strip(b"\0"):
        return True
    return raw.startswith(MAGIC) and (len(raw) < len(MAGIC) + 16 + 8 + 32 or raw.endswith(b"\0\0\0\0"))


def _idx_plain(path: str):
    try:
        with builtins.open(path, "rb") as f:
            _, plain = unseal(IDXFILE, f.read())
        return {str(k): int(v) for k, v in json.loads(plain.decode("utf-8")).items()}
    except (OSError, ValueError, TypeError):
        return None


def _load_idx() -> dict:
    global _idx
    if _idx is not None:
        return _idx
    _idx = {}
    path = os.path.join(_dir, IDXFILE)
    if os.path.exists(path):
        got = _idx_plain(path)
        if got is None:
            try:
                with builtins.open(path, "rb") as f:
                    raw = f.read()
            except OSError:
                raw = b""
            if continuity.unclean or _torn(raw):
                got = _idx_plain(path + ".bak")           # 上一次的索引 (只比现在落后一次写入: 文件的计数不会比它小)
                if got is None:
                    got = _rebuild_idx()                  # 没有可用的备份: 按现有文件里的计数重建 (这一次对倒回去不设防, 之后照常)
                continuity.log_event("RECOVER secure.idx unreadable after an unclean exit / torn write: recovered")
                if _key is not None:
                    try:
                        _atomic(path, seal(IDXFILE, 0, json.dumps(got).encode("utf-8")))      # 正本也补好
                    except OSError:
                        pass
            else:
                _tamper(IDXFILE, "bad-index")
        if got is not None:
            _idx = got
    return _idx


def _rebuild_idx() -> dict:
    out = {}
    try:
        for name in os.listdir(_dir):
            if _managed_name(name):
                try:
                    with builtins.open(os.path.join(_dir, name), "rb") as f:
                        raw = f.read()
                    ctr, _ = unseal(name, raw)
                    out[name] = ctr
                except (OSError, ValueError):
                    pass
    except OSError:
        pass
    return out


def _atomic(path: str, data: bytes, name: str = ""):
    continuity.durable_write(path, data, bak=_valid(name) if name and _key is not None else None)


def _commit(path: str, name: str, plain: bytes):
    """写一个受管理的文件: 计数 +1, 信封写盘 (先写临时文件再改名), 索引更新."""
    with _lock:
        idx = _load_idx()
        # 两个 OIT 同时开着 (开发版本 + 装好的版本, 9.4) 时各有一份内存里的计数: 写之前先看盘上的索引
        # (别的进程可能刚写过), 取大的那个. 否则写得少的那个进程会用旧的计数盖掉新文件, 对方下一次读就
        # 当成 "rollback", 在锚点里标记 pending, 下一次启动清掉数据 (1.0.467 前发生过的误清).
        disk = _idx_plain(os.path.join(_dir, IDXFILE))
        if disk:
            for k, v in disk.items():
                if v > idx.get(k, 0):
                    idx[k] = v
        ctr = idx.get(name, 0) + 1
        given = os.path.abspath(path)
        final = given[:-4] if given.endswith(".tmp") else given
        data = seal(name, ctr, plain)
        _atomic(final, data, name)                      # 先文件 (fsync) 后索引: 断电时文件的计数只会比索引大, 不会小
        idx[name] = ctr
        _atomic(os.path.join(_dir, IDXFILE), seal(IDXFILE, 0, json.dumps(idx).encode("utf-8")), IDXFILE)
        if given != final:
            with builtins.open(given, "wb") as f:       # 调用方 (写临时文件再改名的老写法) 接着会把它改名成 final: 内容一样
                f.write(data)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass


def _from_bak(path: str, name: str, slack: int = 1):
    """上一次的完好文件 (.bak) 的明文; 计数比索引小的 (被换成旧的了) 不要. 用上了就把它放回正本."""
    try:
        with builtins.open(path + ".bak", "rb") as f:
            raw = f.read()
        ctr, plain = unseal(name, raw)
    except (OSError, ValueError):
        return None
    if ctr < _load_idx().get(name, 0) - slack:        # .bak 总是比索引落后一次写入; 再旧的是被人换上去的
        return None
    try:
        continuity.durable_write(path, raw)
    except OSError:
        pass
    return plain


def _recover(path: str, name: str, raw: bytes, why: str):
    """文件读不出来 (raw: 读到的内容, 文件不存在 / 内容没问题只是计数不对时是 None): 是断电 / 崩溃留下的 (半截的, 或上一次没有正常结束) 就退回 .bak,
    没有 .bak 的半截文件当作空的. 返回明文; 不是断电的痕迹返回 None (调用方记篡改)."""
    torn = raw is not None and _torn(raw)
    if not (continuity.unclean or torn):
        return None
    plain = _from_bak(path, name)
    if plain is not None:
        continuity.log_event(f"RECOVER {name} ({why}): restored from .bak")
        return plain
    if torn:
        continuity.log_event(f"RECOVER {name} ({why}): half-written file, treated as empty")
        return b""
    return None


def _read(path: str, name: str) -> bytes:
    """受管理的文件的明文. 篡改时 _tamper 并返回空字节 (调用方当作损坏的文件); 文件不存在时 FileNotFoundError (索引里有的先记篡改).
    断电 / 崩溃留下的半截文件不算篡改 (见 _recover)."""
    with _lock:
        try:
            with builtins.open(path, "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            if _key is not None and _strict and name in _load_idx():
                rec = _recover(path, name, None, "missing")
                if rec is not None:
                    return rec
                _tamper(name, "deleted")
                return b""
            raise
        if _key is None:
            if raw.startswith(MAGIC):
                _tamper(name, "no-key")
                return b""
            return raw                                   # 还没有密钥 (旧版本 / 联动程序先于 OIT 启动): 明文照读
        if not raw.startswith(MAGIC):
            if _strict:
                rec = _recover(path, name, raw, "not-envelope")
                if rec is not None:
                    return rec
                _tamper(name, "plain")
                return b""
            return raw
        try:
            ctr, plain = unseal(name, raw)
        except ValueError:
            rec = _recover(path, name, raw, "bad-mac")
            if rec is not None:
                return rec
            _tamper(name, "bad-mac")
            return b""
        if _strict and ctr < _load_idx().get(name, 0):
            if continuity.unclean and ctr >= _load_idx().get(name, 0) - 1:
                return plain                             # 断电时刚好少了最后一次写入
            rec = _recover(path, name, None, "rollback")
            if rec is not None:
                return rec
            _tamper(name, "rollback")
            return b""
        return plain


# ---- 替换 open ----

class _Writer(io.StringIO):
    def __init__(self, path: str, name: str, encoding: str, append_text: str = ""):
        super().__init__(append_text)
        if append_text:
            self.seek(0, io.SEEK_END)
        self._path, self._name, self._enc, self._done = path, name, encoding, False

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None and not self._done:
            self._done = True                      # 写到一半出了错: 不要把半截内容写进去
        return super().__exit__(exc_type, exc, tb)

    def close(self):
        if not self._done:
            self._done = True
            try:
                _commit(self._path, self._name, self.getvalue().encode(self._enc))
            finally:
                super().close()
        else:
            super().close()


def open(path, mode="r", encoding=None, **kw):                       # noqa: A001
    """内置 open 的替代 (只处理文本的 "r" / "w"): 数据文件夹里的 .json 自动加密 / 解密, 其他文件原样."""
    name = _name_of(path) if isinstance(path, (str, os.PathLike)) else None
    if name is None or _dir is None or "b" in mode or mode not in ("r", "w"):
        return builtins.open(path, mode, encoding=encoding, **kw)
    path = os.fspath(path)
    enc = encoding or "utf-8"
    if mode == "r":
        return io.StringIO(_read(path, name).decode(enc, "replace"))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if _key is None:
        return builtins.open(path, mode, encoding=encoding, **kw)     # 还没有密钥: 明文 (ensure_key 之后的第一次启动会转成加密的)
    return _Writer(path, name, enc)


def remove(path):
    """删一个数据文件 (程序自己要删的): 索引里的记录也去掉, 不然下次读会当成被人删了."""
    name = _name_of(path)
    with _lock:
        if name is not None and _key is not None:
            idx = _load_idx()
            if name in idx:
                del idx[name]
                _atomic(os.path.join(_dir, IDXFILE), seal(IDXFILE, 0, json.dumps(idx).encode("utf-8")), IDXFILE)
    try:
        os.remove(path + ".bak")
    except OSError:
        pass
    os.remove(path)


# ---- 自检 ----

def _selftest():
    import shutil
    import tempfile
    global _dir, _key, _strict, _idx, _on_tamper
    if sys.platform != "win32":
        return "securefile selftest OK (non-Windows)"
    saved = (_dir, _key, _strict, _idx, _on_tamper)
    d = tempfile.mkdtemp()
    events = []
    try:
        # 没配置: 原样
        p0 = os.path.join(d, "x.json")
        with open(p0, "w", encoding="utf-8") as f:
            f.write('{"a": 1}')
        assert builtins.open(p0).read() == '{"a": 1}'
        os.remove(p0)
        # 旧版本升级: 先有明文的 json, 配置 (没有密钥时读明文), ensure_key 把它们转成加密的
        with builtins.open(os.path.join(d, "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"limit": 7200}, f)
        with builtins.open(os.path.join(d, "sysproxy_backup.json"), "w") as f:
            f.write('{"orig": {}}')
        configure(d, lambda n, w: events.append((n, w)))
        assert enabled() and key_id() == ""
        with open(os.path.join(d, "settings.json"), "r") as f:
            assert json.load(f) == {"limit": 7200}
        assert ensure_key() and key_id() and not ensure_key()
        raw = builtins.open(os.path.join(d, "settings.json"), "rb").read()
        assert raw.startswith(MAGIC) and b"7200" not in raw, "已经转成加密的"
        assert builtins.open(os.path.join(d, "sysproxy_backup.json")).read() == '{"orig": {}}', "系统代理的备份不加密"
        with open(os.path.join(d, "settings.json"), "r") as f:
            assert json.load(f) == {"limit": 7200} and not events
        # 写 / 读
        for i in range(3):
            with open(os.path.join(d, "log.json"), "w", encoding="utf-8") as f:
                json.dump({"days": {"x": i}, "中文": "字"}, f, ensure_ascii=False)
        with open(os.path.join(d, "log.json"), "r", encoding="utf-8") as f:
            assert json.load(f) == {"days": {"x": 2}, "中文": "字"}
        assert not events and _load_idx()["log.json"] == 3
        good = builtins.open(os.path.join(d, "log.json"), "rb").read()
        # 用记事本改: 改一个字节 → 校验不过, 当作损坏的文件, 通知篡改
        bad = bytearray(good)
        bad[40] ^= 1
        builtins.open(os.path.join(d, "log.json"), "wb").write(bytes(bad))
        with open(os.path.join(d, "log.json"), "r", encoding="utf-8") as f:
            assert f.read() == "" and events[-1] == ("log.json", "bad-mac")
        # 换成明文
        builtins.open(os.path.join(d, "log.json"), "w").write('{"days": {}}')
        with open(os.path.join(d, "log.json"), "r") as f:
            assert f.read() == "" and events[-1] == ("log.json", "plain")
        # 换成别的文件的内容 (名字也参与校验)
        builtins.open(os.path.join(d, "log.json"), "wb").write(builtins.open(os.path.join(d, "settings.json"), "rb").read())
        with open(os.path.join(d, "log.json"), "r") as f:
            assert f.read() == "" and events[-1] == ("log.json", "bad-mac")
        # 倒回旧版本 (计数变小)
        with open(os.path.join(d, "log.json"), "w") as f:
            f.write('{"v": 1}')
        old = builtins.open(os.path.join(d, "log.json"), "rb").read()
        with open(os.path.join(d, "log.json"), "w") as f:
            f.write('{"v": 2}')
        builtins.open(os.path.join(d, "log.json"), "wb").write(old)
        with open(os.path.join(d, "log.json"), "r") as f:
            assert f.read() == "" and events[-1] == ("log.json", "rollback")
        # 删掉 (程序自己删用 remove, 不算)
        with open(os.path.join(d, "log.json"), "w") as f:
            f.write("{}")
        os.remove(os.path.join(d, "log.json"))
        with open(os.path.join(d, "log.json"), "r") as f:
            assert f.read() == "" and events[-1] == ("log.json", "deleted")
        n = len(events)
        with open(os.path.join(d, "other.json"), "w") as f:
            f.write("{}")
        remove(os.path.join(d, "other.json"))
        try:
            open(os.path.join(d, "other.json"), "r")
            raise AssertionError("应该是 FileNotFoundError")
        except FileNotFoundError:
            pass
        assert len(events) == n, "程序自己删的不算篡改"
        # 重新配置 (模拟重新打开程序) 读到的密钥一致
        k = key_id()
        configure(d, lambda n_, w: events.append((n_, w)))
        assert key_id() == k and _strict
        with open(os.path.join(d, "settings.json"), "r") as f:
            assert json.load(f) == {"limit": 7200}
        # 密钥文件被换成别人的: 全部解不开
        kf = builtins.open(os.path.join(d, KEYFILE), "rb").read()
        builtins.open(os.path.join(d, KEYFILE), "wb").write(b"junk")
        configure(d, lambda n_, w: events.append((n_, w)))
        assert key_id() == ""
        with open(os.path.join(d, "settings.json"), "r") as f:
            assert f.read() == "" and events[-1] == ("settings.json", "no-key")
        builtins.open(os.path.join(d, KEYFILE), "wb").write(kf)
        configure(d)
        # 篡改的文件被程序重写后就好了
        with open(os.path.join(d, "settings.json"), "w") as f:
            f.write('{"new": 1}')
        with open(os.path.join(d, "settings.json"), "r") as f:
            assert json.load(f) == {"new": 1}
        # 老写法 (写 xxx.json.tmp 再改名): 索引照样更新, 改名之后读得出
        tmp = os.path.join(d, "t.json.tmp")
        with open(tmp, "w") as f:
            f.write('{"t": 1}')
        os.replace(tmp, os.path.join(d, "t.json"))
        with open(os.path.join(d, "t.json"), "r") as f:
            assert json.load(f) == {"t": 1}
        assert _load_idx()["t.json"] == 1
        # 两个 OIT 同时开着写同一个数据文件夹 (开发版本 + 装好的版本, 9.4): 写之前先看盘上的索引 (取大的),
        # 另一个进程写得更多时计数也不会倒退 —— 不然对方读到的计数比索引小, 当成 "rollback" 清掉数据 (1.0.467)
        p2 = os.path.join(d, "two.json")
        with open(p2, "w") as f:
            f.write("{}")
        mine = dict(_idx)                       # 本进程内存里的索引 (two.json = 1)
        _idx = None                             # 另一个进程: 自己的缓存, 又写了两次 (2, 3)
        with open(p2, "w") as f:
            f.write("{}")
        with open(p2, "w") as f:
            f.write("{}")
        assert _idx_plain(os.path.join(d, IDXFILE))["two.json"] == 3
        _idx = mine                             # 切回本进程: 缓存里记着的还是 1
        n = len(tampered)
        with open(p2, "w") as f:
            f.write("{}")
        assert _idx["two.json"] == 4, "按盘上的索引往上写 (3 + 1), 不倒退"
        with open(p2, "r", encoding="utf-8") as f:
            assert f.read() == "{}" and len(tampered) == n, "不算倒回去"
        # 写到一半出了错: 不写半截内容
        try:
            with open(os.path.join(d, "t.json"), "w") as f:
                f.write("{half")
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        with open(os.path.join(d, "t.json"), "r") as f:
            assert json.load(f) == {"t": 1}
        # 断电 / 崩溃 (MECHANICS 9.5): 半截的文件不算篡改, 不引起清数据
        real_log, continuity.log_event = continuity.log_event, lambda text: None
        try:
            configure(d, lambda n_, w: events.append((n_, w)))
            n = len(events)
            pj = os.path.join(d, "pw.json")
            for i in range(3):
                with open(pj, "w", encoding="utf-8") as f:
                    f.write(json.dumps({"i": i}))
            assert os.path.exists(pj + ".bak"), "写的时候留了上一次的备份"
            good = builtins.open(pj, "rb").read()
            for torn in (b"", b"\0" * len(good), good[:40], good[:-5] + b"\0" * 5):      # 空的 / 全是 0 / 截断 / 末尾补 0
                builtins.open(pj, "wb").write(torn)
                with open(pj, "r", encoding="utf-8") as f:
                    assert json.load(f) == {"i": 1}, "半截的文件: 退回上一次 (.bak)"
                assert len(events) == n and not tampered, (events[n:], torn[:8])
                assert builtins.open(pj, "rb").read() == builtins.open(pj + ".bak", "rb").read(), "退回之后正本也补好了"
            os.remove(pj + ".bak")
            builtins.open(pj, "wb").write(b"")
            with open(pj, "r", encoding="utf-8") as f:
                assert f.read() == "" and len(events) == n, "半截的文件又没有备份: 当作空的, 不记篡改"
            # 信封完整却校验不过 (正常结束之后): 照样是篡改
            with open(pj, "w", encoding="utf-8") as f:
                f.write('{"i": 9}')
            bad = bytearray(builtins.open(pj, "rb").read())
            bad[40] ^= 1
            builtins.open(pj, "wb").write(bytes(bad))
            with open(pj, "r", encoding="utf-8") as f:
                assert f.read() == "" and events[-1] == ("pw.json", "bad-mac")
            # 上一次没有正常结束: 坏的文件先退回 .bak, 少了最后一次写入也不算倒回去
            with open(pj, "w", encoding="utf-8") as f:
                f.write('{"i": 10}')
            with open(pj, "w", encoding="utf-8") as f:
                f.write('{"i": 11}')
            n = len(events)
            continuity.unclean = True
            try:
                builtins.open(pj, "wb").write(bytes(bad))
                with open(pj, "r", encoding="utf-8") as f:
                    assert json.load(f) == {"i": 10} and len(events) == n, "unclean: 坏的文件退回 .bak"
                # 现在文件是 {"i": 10}, 索引记着 {"i": 11} 的计数: 比文件多一次 (断电时最后一次写入没来得及落盘)
                with open(pj, "r", encoding="utf-8") as f:
                    assert json.load(f) == {"i": 10} and len(events) == n, "unclean: 少一次写入不算倒回去"
            finally:
                continuity.unclean = False
            # 索引是半截的: 退回 .idx.bak / 重建, 不是篡改; 完整却坏的才是 bad-index
            n = len(events)
            ip = os.path.join(d, IDXFILE)
            assert os.path.exists(ip + ".bak")
            builtins.open(ip, "wb").write(b"")
            _idx = None
            assert _load_idx().get("settings.json") and len(events) == n, "索引半截: 恢复"
            iraw = bytearray(builtins.open(ip, "rb").read())
            iraw[40] ^= 1
            builtins.open(ip, "wb").write(bytes(iraw))
            _idx = None
            _load_idx()
            assert events[-1] == (IDXFILE, "bad-index"), "索引完整却坏了 (正常结束之后): 篡改"
            with open(os.path.join(d, "settings.json"), "w") as f:      # 把索引写好
                f.write('{"new": 1}')
            # 密钥文件是半截的: 用 .bak
            kf0 = builtins.open(os.path.join(d, KEYFILE), "rb").read()
            kid0 = key_id()
            builtins.open(os.path.join(d, KEYFILE), "wb").write(b"")
            configure(d, lambda n_, w: events.append((n_, w)))
            assert key_id() == kid0, "密钥文件半截: 用 .bak"
            builtins.open(os.path.join(d, KEYFILE), "wb").write(kf0)
        finally:
            continuity.log_event = real_log
        # 不在数据文件夹里 / 不是 .json: 原样
        sub = os.path.join(d, "sub")
        os.makedirs(sub)
        with open(os.path.join(sub, "a.json"), "w") as f:
            f.write("plain")
        assert builtins.open(os.path.join(sub, "a.json")).read() == "plain"
        with open(os.path.join(d, "a.log"), "w") as f:
            f.write("plain")
        assert builtins.open(os.path.join(d, "a.log")).read() == "plain"
    finally:
        _dir, _key, _strict, _idx, _on_tamper = saved
        shutil.rmtree(d, ignore_errors=True)
    return "securefile selftest OK"


if __name__ == "__main__":
    print(_selftest())
