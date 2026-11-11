# -*- coding: utf-8 -*-
"""API Key 的存盘保护 (Windows DPAPI), 以及错误信息里抹掉 Key. 依赖 ai_common."""

import base64
import json
import os
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.parse
import urllib.request

import securefile


_DPAPI_ENTROPY = b"OIT ai_config api_key"


_ENC_PREFIX = "dpapi:"


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b: bytes):
        buf = ctypes.create_string_buffer(b, len(b))
        return _Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    src, _keep1 = blob(data)
    ent, _keep2 = blob(_DPAPI_ENTROPY)
    out = _Blob()
    crypt32 = ctypes.windll.crypt32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    # CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if not fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, 0x1, ctypes.byref(out)):
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def can_encrypt() -> bool:
    return sys.platform == "win32"


def encrypt_key(key: str) -> str:
    """明文 Key → 'dpapi:...' ; 空 Key 原样返回 ''. 不能加密时抛 OSError."""
    if not key:
        return ""
    if not can_encrypt():
        raise OSError("DPAPI not available")
    return _ENC_PREFIX + base64.b64encode(_dpapi(key.encode("utf-8"), True)).decode("ascii")


def decrypt_key(text: str) -> str:
    """'dpapi:...' → 明文 Key. 解不开 (换了电脑 / 换了账户) 时返回 ''."""
    if not text or not text.startswith(_ENC_PREFIX):
        return ""
    try:
        raw = base64.b64decode(text[len(_ENC_PREFIX):])
        return _dpapi(raw, False).decode("utf-8")
    except Exception:
        return ""
