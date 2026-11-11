"""设备同步的密码学 (MECHANICS.md 第 15 节): 配对的 Diffie-Hellman、6 位配对码、HMAC 密钥流的封装 / 解封、文件通道的异或. 只用标准库; Android 版 sync/SyncCrypto.kt 是同一套算法."""

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time


TS_SKEW = 300              # 秒: 请求里的时间戳允许和本机相差多少

# RFC 3526, 2048-bit MODP Group 14
P = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A0879"
    "8E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B"
    "0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA4836"
    "1C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804"
    "F1746C08CA18217C32905E462E36CE3BE39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6"
    "955817183995497CEA956AE515D2261898FA051015728E5A8AACAA68FFFFFFFFFFFFFFFF", 16)
G = 2


def _h(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


def dh_keypair():
    x = secrets.randbits(256) | (1 << 255)
    return x, pow(G, x, P)


def dh_shared(x: int, peer_pub_hex: str) -> bytes:
    y = int(peer_pub_hex, 16)
    if not 1 < y < P - 1:
        raise ValueError("bad public key")
    s = pow(y, x, P)
    return hashlib.sha256(s.to_bytes(256, "big")).digest()


def pair_code(k: bytes) -> str:
    n = int.from_bytes(hashlib.sha256(k + b"code").digest()[:4], "big") % 1000000
    return f"{n:06d}"


def accept_proof(k: bytes, token: str) -> str:
    return _h(k, b"accepted:" + token.encode()).hex()


def _keystream_xor(enc: bytes, nonce: bytes, data: bytes) -> bytes:
    out = bytearray(len(data))
    for i in range(0, len(data), 32):
        block = _h(enc, nonce + (i // 32).to_bytes(4, "big"))
        for j in range(min(32, len(data) - i)):
            out[i + j] = data[i + j] ^ block[j]
    return bytes(out)


def file_key(k: bytes) -> bytes:
    """文件通道的加密密钥 (由绑定时的密钥算出)."""
    return _h(k, b"file")


def file_xor(fk: bytes, nonce: bytes, offset: int, data: bytes) -> bytes:
    """文件通道: 文件内容和密钥流异或 (加密 / 解密一样). 密钥流第 i 个 32 字节 = SHA-256(密钥 + nonce + i),
    可以从任意位置 offset 开始接着算. 同 Android 的 SyncCrypto.fileXor."""
    if not data:
        return b""
    first, skip = divmod(int(offset), 32)
    count = (skip + len(data) + 31) // 32
    pre = fk + nonce
    stream = b"".join(hashlib.sha256(pre + (first + i).to_bytes(8, "big")).digest() for i in range(count))
    ks = stream[skip:skip + len(data)]
    return (int.from_bytes(data, "big") ^ int.from_bytes(ks, "big")).to_bytes(len(data), "big")


def seal(k: bytes, sender: str, obj, nonce: bytes = None, ts: int = None) -> dict:
    nonce = nonce or secrets.token_bytes(16)
    ts = int(time.time() * 1000) if ts is None else ts
    pt = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ct = _keystream_xor(_h(k, b"enc"), nonce, pt)
    mac = _h(_h(k, b"mac"), nonce + ts.to_bytes(8, "big") + ct).hex()
    return {"from": sender, "nonce": base64.b64encode(nonce).decode(), "ts": ts,
            "ct": base64.b64encode(ct).decode(), "mac": mac}


class SealError(Exception):
    pass


_seen_nonces = {}
_seen_lock = threading.Lock()


def unseal(k: bytes, env: dict, replay: bool = True):
    try:
        nonce = base64.b64decode(env["nonce"])
        ct = base64.b64decode(env["ct"])
        ts = int(env["ts"])
        mac = str(env["mac"])
    except Exception:
        raise SealError("malformed")
    want = _h(_h(k, b"mac"), nonce + ts.to_bytes(8, "big") + ct).hex()
    if not hmac.compare_digest(want, mac):
        raise SealError("bad mac")
    if abs(time.time() * 1000 - ts) > TS_SKEW * 1000:
        raise SealError("clock")
    if replay:
        with _seen_lock:
            now = time.time()
            for n, t in list(_seen_nonces.items()):
                if now - t > 2 * TS_SKEW:
                    del _seen_nonces[n]
            if mac in _seen_nonces:
                raise SealError("replay")
            _seen_nonces[mac] = now
    return json.loads(_keystream_xor(_h(k, b"enc"), nonce, ct).decode("utf-8"))
