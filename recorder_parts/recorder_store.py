"""
录像的存档 (MECHANICS 18.8): 文件夹布局, 加密 (.oitv), 播放, 清理, 合并结果的存放.

只依赖标准库 + recorder_common; 由 recorder.py 重新导出, 别处照旧写
recorder.work_dir / recorder.purge_old 之类.
"""

import ctypes
import hashlib
import json
import os
import re
import secrets
import shutil
import tempstore
import threading
import time
from ctypes import byref, c_ulong, c_void_p
from datetime import datetime

from recorder_parts.recorder_common import (
    CONSUMED, EXT, KINDS, MAGIC, MAX_AGE_DAYS, PART, PARTIAL_AGE_DAYS, PURGE_EXT,
    REC_KEY, SETTLE_HOURS, _CHUNK,
)


# ---------------------------------------------------------------- 文件夹

def folder(data_dir: str, kind: str) -> str:
    return os.path.join(data_dir, "recordings", kind)


def work_dir(data_dir: str, kind: str) -> str:
    """正在写的 (还没加密的) mp4 放在这里: OIT 的临时文件夹 (%TEMP%\\OIT\\rec, 不在数据目录, 数据目录可能在网盘里)."""
    tag = hashlib.sha1(os.path.abspath(data_dir).lower().encode("utf-8")).hexdigest()[:10]
    return tempstore.path("rec", tag, kind)


def play_dir() -> str:
    """回放时解密出来的 mp4 放在这里 (临时文件夹)."""
    return tempstore.path("rec", "play")


def list_files(data_dir: str, kind: str) -> list:
    """录像文件, 新的在前: [{path, name, size, start, partial}]. partial: 没有正常写完 (多半放不出来);
    正在写的那个在临时文件夹里 (还没加密), 也列出来, 同样是 partial."""
    out = []
    for d in (folder(data_dir, kind), work_dir(data_dir, kind)):
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            low = n.lower()
            ext = next((e for e in (EXT, ".mp4") if low.endswith(e)), "")
            if not ext:
                continue
            p = os.path.join(d, n)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            stem = n[:-len(ext)]
            partial = stem.endswith(PART)
            if partial:
                stem = stem[:-len(PART)]
            try:
                start = datetime.strptime(stem[:15], "%Y%m%d-%H%M%S")
            except ValueError:
                start = None
            out.append({"path": p, "name": n, "size": size, "start": start, "partial": partial})
    out.sort(key=lambda f: (f["start"] or datetime.min, f["name"]), reverse=True)
    return out


def total_size(data_dir: str, kind: str) -> int:
    return sum(f["size"] for f in list_files(data_dir, kind))


# ---------------------------------------------------------------- 合约 id / 文件名

def cid_of(name: str) -> str:
    """文件名里的合约 id (录像按它合并): "YYYYmmdd-HHMMSS[-n]__<cid>.ext" → cid; 没有则 ""."""
    stem = os.path.splitext(os.path.basename(name))[0]
    if stem.endswith(PART):
        stem = stem[:-len(PART)]
    _sep, _m, cid = stem.partition("__")
    return cid


def _safe_cid(cid: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", str(cid))[:24] or "x"


def files_of(data_dir: str, kind: str, cid: str) -> list:
    """某个合约这一路的录像片段 (新的在前)."""
    return [f for f in list_files(data_dir, kind) if cid and cid_of(f["name"]) == cid]


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------- 加密 (AES-256-CTR, Windows CNG)

class _Aes:
    """AES-256-ECB (bcrypt.dll), 用来算 CTR 的密钥流."""
    _lock = threading.Lock()
    _key = None

    @classmethod
    def handle(cls):
        with cls._lock:
            if cls._key is None:
                b = ctypes.windll.bcrypt
                alg, key = c_void_p(), c_void_p()
                if b.BCryptOpenAlgorithmProvider(byref(alg), ctypes.c_wchar_p("AES"), None, 0):
                    raise OSError("BCryptOpenAlgorithmProvider")
                mode = ctypes.create_unicode_buffer("ChainingModeECB")
                if b.BCryptSetProperty(alg, ctypes.c_wchar_p("ChainingMode"), mode, ctypes.sizeof(mode), 0):
                    raise OSError("BCryptSetProperty")
                kb = ctypes.create_string_buffer(REC_KEY, len(REC_KEY))
                if b.BCryptGenerateSymmetricKey(alg, byref(key), None, 0, kb, len(REC_KEY), 0):
                    raise OSError("BCryptGenerateSymmetricKey")
                cls._alg, cls._key = alg, key
            return cls._key

    @classmethod
    def ecb(cls, data: bytes) -> bytes:
        key = cls.handle()
        src = ctypes.create_string_buffer(data, len(data))
        dst = ctypes.create_string_buffer(len(data))
        n = c_ulong()
        if ctypes.windll.bcrypt.BCryptEncrypt(key, src, len(data), None, None, 0, dst, len(data), byref(n), 0):
            raise OSError("BCryptEncrypt")
        return dst.raw


def rec_xor(nonce: bytes, offset: int, data: bytes) -> bytes:
    """录像的内容和密钥流异或 (加密 / 解密一样). 第 i 个 16 字节块的计数器 = nonce (8 字节) + i (8 字节, 大端);
    可以从任意位置 offset 开始. 同 Android 的 RecCrypto (AES/CTR/NoPadding, IV = nonce + 0)."""
    if not data:
        return b""
    first, skip = divmod(int(offset), 16)
    count = (skip + len(data) + 15) // 16
    ctr = b"".join(nonce + (first + i).to_bytes(8, "big") for i in range(count))
    ks = _Aes.ecb(ctr)[skip:skip + len(data)]
    return (int.from_bytes(data, "big") ^ int.from_bytes(ks, "big")).to_bytes(len(data), "big")


def encrypt_file(src: str, dst: str):
    """src (mp4) → dst (.oitv): 先写到 dst.tmp, 写完再改名. 不删 src."""
    nonce = secrets.token_bytes(8)
    tmp = dst + ".tmp"
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        fo.write(MAGIC + nonce)
        off = 0
        while True:
            b = fi.read(_CHUNK)
            if not b:
                break
            fo.write(rec_xor(nonce, off, b))
            off += len(b)
    os.replace(tmp, dst)


def decrypt_file(src: str, dst: str):
    """src (.oitv) → dst (mp4)."""
    with open(src, "rb") as fi:
        head = fi.read(len(MAGIC) + 8)
        if head[:len(MAGIC)] != MAGIC:
            raise ValueError("not an OIT recording")
        nonce = head[len(MAGIC):]
        with open(dst, "wb") as fo:
            off = 0
            while True:
                b = fi.read(_CHUNK)
                if not b:
                    break
                fo.write(rec_xor(nonce, off, b))
                off += len(b)


def seal_file(data_dir: str, kind: str, src: str) -> str:
    """写完的 mp4 (src, 在 work_dir 或旧版本留在数据目录里的) → 数据目录里的 .oitv, 删掉 src. 返回新的路径."""
    d = folder(data_dir, kind)
    os.makedirs(d, exist_ok=True)
    name = os.path.basename(src)
    if name.lower().endswith(".mp4"):
        name = name[:-4]
    dst = os.path.join(d, name + EXT)
    n = 1
    while os.path.exists(dst):
        dst = os.path.join(d, f"{name}-{n}{EXT}")
        n += 1
    encrypt_file(src, dst)
    _remove(src)
    return dst


def seal_leftovers(data_dir: str, busy=()) -> int:
    """打开程序时: 上次没加密的 (程序被强行关掉时留在临时文件夹里的, 旧版本存在数据目录里的 mp4) 都加密起来.
    busy: 正在写的文件 (跳过). 返回加密了几个."""
    n = 0
    for kind in KINDS:
        for d in (work_dir(data_dir, kind), folder(data_dir, kind)):
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for name in names:
                p = os.path.join(d, name)
                if not name.lower().endswith(".mp4") or p in busy:
                    continue
                try:
                    seal_file(data_dir, kind, p)
                    n += 1
                except OSError:
                    pass
    return n


# ---------------------------------------------------------------- 合并用掉的片段 (记录)

def _consumed_path(data_dir: str, cid: str, kind: str) -> str:
    return os.path.join(merged_dir(data_dir, kind), _safe_cid(cid) + CONSUMED)


def read_consumed(data_dir: str, cid: str, kind: str) -> set:
    try:
        with open(_consumed_path(data_dir, cid, kind), "r", encoding="utf-8") as f:
            return {str(x) for x in json.load(f)}
    except (OSError, ValueError, TypeError):
        return set()


def write_consumed(data_dir: str, cid: str, kind: str, names):
    try:
        with open(_consumed_path(data_dir, cid, kind), "w", encoding="utf-8") as f:
            json.dump(sorted(names), f)
    except OSError:
        pass


def settle_fragments(data_dir: str, hours: float = SETTLE_HOURS, now: float = None) -> int:
    """合并好的录像: 用掉的片段 (本机录的 + 收到的 incoming) 在合并 `hours` 小时之后清掉 (之前留着: 有片段迟到时要重合并).
    以后再有迟到的片段重合并时, 已经合进去的部分取自最终录像 (merge 里). 返回删了几个文件."""
    now = time.time() if now is None else now
    n = 0
    for kind in KINDS:
        md = merged_dir(data_dir, kind)
        try:
            names = os.listdir(md)
        except OSError:
            continue
        for name in names:                       # 旧版本合并的 (没有 .consumed 记录): 现有的同合约片段算用掉的, 从最终录像的时间起算
            if name.lower().endswith(EXT) and cid_of(name) and not os.path.exists(_consumed_path(data_dir, cid_of(name), kind)):
                cid = cid_of(name)
                frag = [x["name"] for x in files_of(data_dir, kind, cid) if not x.get("partial")]
                try:
                    frag += [x for x in os.listdir(incoming_dir(data_dir, cid, kind)) if x.lower().endswith(EXT)]
                except OSError:
                    pass
                if frag:
                    write_consumed(data_dir, cid, kind, frag)
                    try:
                        mt = os.path.getmtime(os.path.join(md, name))
                        os.utime(_consumed_path(data_dir, cid, kind), (mt, mt))
                    except OSError:
                        pass
        for name in os.listdir(md):
            if not name.endswith(CONSUMED):
                continue
            mp = os.path.join(md, name)
            try:
                if now - os.path.getmtime(mp) < hours * 3600:
                    continue
            except OSError:
                continue
            cid = name[:-len(CONSUMED)]
            for fn in read_consumed(data_dir, cid, kind):
                for d in (folder(data_dir, kind), incoming_dir(data_dir, cid, kind)):
                    p = os.path.join(d, fn)
                    if os.path.isfile(p):
                        _remove(p)
                        n += 1
    return n


def purge_old(data_dir: str, max_age_days: float = MAX_AGE_DAYS, busy=(), now: float = None) -> int:
    """录像的清理 (MECHANICS 18.8), 返回删了几个文件:
      1. 合并好 SETTLE_HOURS 之后用掉的片段 (settle_fragments);
      2. 超过 max_age_days 天的录像: 数据目录 recordings/ 下所有的 (camera / screen 片段, 收到的 incoming, 合并好的 merged) 和临时文件夹里没加密的 mp4,
         按文件最后修改的时间算 (正在写的文件一直在改, 不会被删); busy: 正在写的路径, 跳过;
      3. 没写完的 (.recording.oitv) 超过 PARTIAL_AGE_DAYS 天: 合并时本来就不用它们;
      4. 合并好的录像已经没了的 .consumed 记录, 和清空了的 incoming 文件夹.
    不同步给别的设备 (各台自己按时间清)."""
    now = time.time() if now is None else now
    n = settle_fragments(data_dir, now=now)
    busy = {os.path.abspath(p) for p in busy}
    roots = [os.path.join(data_dir, "recordings")] + [work_dir(data_dir, k) for k in KINDS]
    for root in roots:
        for dirpath, _dirs, filenames in os.walk(root):
            for name in filenames:
                p = os.path.join(dirpath, name)
                low = name.lower()
                if os.path.abspath(p) in busy:
                    continue
                age_days = max_age_days
                if low.endswith(PART + EXT):
                    age_days = min(age_days, PARTIAL_AGE_DAYS)
                elif not low.endswith(PURGE_EXT):
                    continue
                try:
                    if os.path.getmtime(p) < now - age_days * 86400:
                        os.remove(p)
                        n += 1
                except OSError:
                    pass
    for kind in KINDS:                                   # 最终录像没了的 .consumed
        md = merged_dir(data_dir, kind)
        try:
            names = os.listdir(md)
        except OSError:
            continue
        have = {cid_of(x) for x in names if x.lower().endswith(EXT)}
        for name in names:
            if name.endswith(CONSUMED) and name[:-len(CONSUMED)] not in have:
                _remove(os.path.join(md, name))
    inc = os.path.join(data_dir, "recordings", "incoming")      # 空了的 incoming/<cid>/<kind>
    try:
        for cid in os.listdir(inc):
            for kind in KINDS:
                try:
                    os.rmdir(os.path.join(inc, cid, kind))
                except OSError:
                    pass
            try:
                os.rmdir(os.path.join(inc, cid))
            except OSError:
                pass
    except OSError:
        pass
    return n


# ---------------------------------------------------------------- 播放

def clean_play():
    """删掉播放时解密出来的临时文件 (正被播放器打开着的删不掉, 下次再删)."""
    d = play_dir()
    try:
        names = os.listdir(d)
    except OSError:
        return
    for name in names:
        _remove(os.path.join(d, name))


def play(path: str) -> str:
    """在 OIT 里打开一个录像: .oitv 解密到临时文件夹 (play_dir) 再用系统默认的播放器打开. 返回打开的文件."""
    clean_play()
    if not path.lower().endswith(EXT):
        os.startfile(path)
        return path
    d = play_dir()
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, os.path.basename(path)[:-len(EXT)] + ".mp4")
    if os.path.exists(out):
        out = os.path.join(d, f"{os.path.basename(path)[:-len(EXT)]}-{secrets.token_hex(3)}.mp4")
    decrypt_file(path, out)
    os.startfile(out)
    return out


# ---------------------------------------------------------------- 合并 / 同步好的最终录像

def incoming_dir(data_dir: str, cid: str, kind: str = "screen") -> str:
    """从别的设备收到的某一路 (camera / screen) 录像片段先放这里 (按合约 id), 合并时一起用."""
    return os.path.join(data_dir, "recordings", "incoming", _safe_cid(cid), kind)


def merged_dir(data_dir: str, kind: str) -> str:
    """合并 / 同步好的最终录像 (camera / screen) 存这里; 界面只展示这里的 (不展示片段)."""
    return os.path.join(data_dir, "recordings", "merged", kind)


def merged_file(data_dir: str, cid: str, kind: str, src: str):
    """别的设备 (合约主人) 发来的已经合并好的最终录像: 直接存进 merged/<kind> (替换这个合约旧的), 不再合并."""
    d = merged_dir(data_dir, kind)
    os.makedirs(d, exist_ok=True)
    for n in os.listdir(d):                       # 同一个合约的旧结果先删掉 (重合并后会再发来)
        if cid_of(n) == _safe_cid(cid):
            _remove(os.path.join(d, n))
    stem = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(d, f"{stem}__{_safe_cid(cid)}{EXT}")
    try:
        os.replace(src, dst)
    except OSError:
        shutil.move(src, dst)
    return dst


def list_merged(data_dir: str, kind: str) -> list:
    """合并 / 同步好的最终录像 (camera / screen), 新的在前: [{path, name, size, start, partial}]. 界面只展示这些."""
    out = []
    try:
        names = os.listdir(merged_dir(data_dir, kind))
    except OSError:
        return out
    for n in names:
        if not n.lower().endswith(EXT):
            continue
        p = os.path.join(merged_dir(data_dir, kind), n)
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        try:
            start = datetime.strptime(n[:15], "%Y%m%d-%H%M%S")
        except ValueError:
            start = None
        out.append({"path": p, "name": n, "size": size, "start": start, "partial": False})
    out.sort(key=lambda f: (f["start"] or datetime.min, f["name"]), reverse=True)
    return out


def delete_merged(data_dir: str, cid: str, kind: str) -> bool:
    """删掉某个合约某一路合并好的最终录像 (删除要在设备间同步, MECHANICS 18.8)."""
    removed = False
    try:
        for n in os.listdir(merged_dir(data_dir, kind)):
            if cid_of(n) == _safe_cid(cid):
                _remove(os.path.join(merged_dir(data_dir, kind), n))
                removed = True
    except OSError:
        pass
    return removed
