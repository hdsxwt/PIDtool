from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes

DPAPI_PREFIX = "dpapi:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def available() -> bool:
    return sys.platform.startswith("win")


def _blob_from_bytes(data: bytes) -> _DataBlob:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob._keepalive = buffer
    return blob


def protect(plaintext: str) -> str:
    if not plaintext:
        return ""
    if not available():
        return plaintext
    in_blob = _blob_from_bytes(plaintext.encode("utf-8"))
    out_blob = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    ):
        return plaintext
    try:
        sealed = ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)
    return DPAPI_PREFIX + base64.b64encode(sealed).decode("ascii")


def unprotect(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(DPAPI_PREFIX):
        return value
    if not available():
        return ""
    try:
        sealed = base64.b64decode(value[len(DPAPI_PREFIX):], validate=True)
    except ValueError:
        return ""
    in_blob = _blob_from_bytes(sealed)
    out_blob = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    ):
        return ""
    try:
        data = ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return ""
