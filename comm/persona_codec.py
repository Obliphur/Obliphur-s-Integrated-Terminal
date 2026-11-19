# -*- coding: utf-8 -*-
"""双端通用的人设加密信封；应用内置密钥，只保护文件免于直接阅读。"""

import base64
import hashlib
import hmac
import os
import uuid
from pathlib import Path

MAX_PERSONA = 60000
MAX_STORED = MAX_PERSONA * 6 + 128
MAGIC = "OIT-PERSONA-ENC:1\n"
_PREFIX = "OIT-PERSONA-ENC:"
_ENC = hashlib.sha256(b"OIT/persona/v1/encryption").digest()
_AUTH = hashlib.sha256(b"OIT/persona/v1/authentication").digest()
_SOURCE = Path(__file__).resolve().parent.parent / "personas"


def protected_name(name: str) -> bool:
    """Claudia 本体与配套文件（包括旧模板名）保存为密文。"""
    stem, ext = os.path.splitext(name.lower().removesuffix(".bak"))
    return ext in (".txt", ".md") and (stem == "claudia" or stem.startswith("claudia_"))


def _xor(data: bytes, nonce: bytes) -> bytes:
    out = bytearray()
    for start in range(0, len(data), 32):
        key = hmac.digest(_ENC, nonce + (start // 32).to_bytes(4, "big"), "sha256")
        out.extend(a ^ b for a, b in zip(data[start:start + 32], key))
    return bytes(out)


def decode(text: str) -> str:
    """兼容旧明文；损坏、未知版本或超限的密文拒绝读取，不能当提示词发送。"""
    text = text.removeprefix("\ufeff").replace("\r\n", "\n")
    if not text.startswith(_PREFIX):
        return text[:MAX_PERSONA]
    if not text.startswith(MAGIC) or len(text) > MAX_STORED:
        raise ValueError("无效的人设加密格式")
    try:
        raw = base64.b64decode("".join(text[len(MAGIC):].split()), validate=True)
        if len(raw) < 48 or len(raw) > MAX_PERSONA * 4 + 48:
            raise ValueError("人设密文长度无效")
        body, tag = raw[:-32], raw[-32:]
        if not hmac.compare_digest(hmac.digest(_AUTH, MAGIC.encode() + body, "sha256"), tag):
            raise ValueError("人设密文校验失败")
        plain = _xor(body[16:], body[:16]).decode("utf-8")
        if len(plain) > MAX_PERSONA:
            raise ValueError("人设文本过长")
        return plain
    except (UnicodeError, ValueError) as exc:
        raise ValueError("无法读取加密人设") from exc


def encode(name: str, text: str) -> str:
    """固定内容产生固定信封，避免设备同步反复发现仅密文变化。"""
    encrypted = text.removeprefix("\ufeff").startswith(_PREFIX)
    plain = decode(text)
    if not protected_name(name) and not encrypted:
        return plain
    data = plain.encode("utf-8")
    nonce = hmac.digest(_AUTH, b"nonce\0" + data, "sha256")[:16]
    body = nonce + _xor(data, nonce)
    tag = hmac.digest(_AUTH, MAGIC.encode() + body, "sha256")
    payload = base64.b64encode(body + tag).decode("ascii")
    return MAGIC + "\n".join(payload[i:i + 96] for i in range(0, len(payload), 96)) + "\n"


def read_file(path: str) -> str:
    """限量读取后解密；明文长度限制与旧版一致。"""
    with open(path, "r", encoding="utf-8-sig") as f:
        return decode(f.read(MAX_STORED + 1))


def write_file(path: str, text: str):
    """人设与修改检测备份共用同一落盘格式。"""
    stored = decode(text) if _SOURCE in Path(path).resolve().parents else encode(os.path.basename(path), text)
    if not stored.startswith(MAGIC):
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(stored)
        return
    tmp = f"{path}.{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp, "x", encoding="utf-8", newline="\n") as f:
            f.write(stored)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def migrate_folder(folder: str):
    """仅改变存储格式，保留当前正文与旧备份之间的内容差异。"""
    if Path(folder).resolve() == _SOURCE or _SOURCE in Path(folder).resolve().parents:
        return
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if not protected_name(name):
            continue
        migrate_file(os.path.join(folder, name))


def migrate_file(path: str):
    if not protected_name(os.path.basename(path)) or _SOURCE in Path(path).resolve().parents:
        return
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            stored = f.read(MAX_PERSONA + 1)
        if len(stored) <= MAX_PERSONA and not stored.startswith(_PREFIX):
            write_file(path, stored)
    except (OSError, ValueError):
        pass
