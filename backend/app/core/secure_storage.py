"""Local protection for OAuth credentials and resumable-upload session URLs."""

import base64
import ctypes
import os
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path
from typing import Optional


_PREFIX = "dpapi:"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt_windows(data: bytes, decrypt: bool) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("DPAPI protection is available only on Windows.")

    source_buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    data_blob_pointer = ctypes.POINTER(_DataBlob)
    if decrypt:
        crypt32.CryptUnprotectData.argtypes = [
            data_blob_pointer, ctypes.POINTER(wintypes.LPWSTR), data_blob_pointer,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, data_blob_pointer,
        ]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)
        )
    else:
        crypt32.CryptProtectData.argtypes = [
            data_blob_pointer, wintypes.LPCWSTR, data_blob_pointer,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, data_blob_pointer,
        ]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        ok = crypt32.CryptProtectData(
            ctypes.byref(source), "CrazzyDocs local secret", None, None, None, 0, ctypes.byref(output)
        )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(output.pbData)


def protect_text(value: str) -> str:
    encrypted = _crypt_windows(value.encode("utf-8"), decrypt=False)
    return _PREFIX + base64.b64encode(encrypted).decode("ascii")


def unprotect_text(value: str) -> str:
    if not value.startswith(_PREFIX):
        raise ValueError("Protected local secret has an unknown format.")
    encrypted = base64.b64decode(value[len(_PREFIX):], validate=True)
    return _crypt_windows(encrypted, decrypt=True).decode("utf-8")


def write_protected_file(path: str, data: bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _crypt_windows(data, decrypt=False)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
        if os.name != "nt":
            os.chmod(target, 0o600)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def read_protected_file(path: str) -> Optional[bytes]:
    target = Path(path)
    if not target.is_file():
        return None
    data = target.read_bytes()
    if data.lstrip().startswith(b"{"):
        return data
    return _crypt_windows(data, decrypt=True)
