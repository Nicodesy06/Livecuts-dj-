"""Protected storage for the OBS WebSocket password.

On Windows the secret is encrypted with DPAPI (CryptProtectData, current-user
scope) through ctypes, so the stored blob is useless on another account or
machine. The secret lives in its own file next to the config and is never
written into exportable configuration.

On other platforms (development/CI only) there is no equivalent built-in
facility without extra dependencies; the secret is stored base64-encoded in a
file with 0600 permissions and flagged as unprotected.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from dj_director.config.paths import config_dir

log = logging.getLogger(__name__)
_FILE = "secrets.json"


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(blob_in), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(blob_out))
    if not ok:
        raise OSError(f"DPAPI call failed (error {ctypes.GetLastError()})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


class SecretStore:
    def __init__(self, directory: Optional[Path] = None) -> None:
        self.path = (directory or config_dir()) / _FILE

    @property
    def protected(self) -> bool:
        return sys.platform == "win32"

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text("utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            log.warning("secret store unreadable; ignoring")
            return {}

    def get(self, key: str) -> str:
        entry = self._read().get(key)
        if not isinstance(entry, dict):
            return ""
        try:
            raw = base64.b64decode(entry.get("v", ""))
            if entry.get("scheme") == "dpapi":
                if sys.platform != "win32":
                    return ""
                raw = _dpapi(raw, protect=False)
            return raw.decode("utf-8")
        except Exception:
            log.warning("could not decrypt secret %r", key)
            return ""

    def set(self, key: str, value: str) -> None:
        data = self._read()
        if not value:
            data.pop(key, None)
        else:
            raw = value.encode("utf-8")
            scheme = "plain-b64"
            if sys.platform == "win32":
                raw = _dpapi(raw, protect=True)
                scheme = "dpapi"
            data[key] = {"scheme": scheme, "v": base64.b64encode(raw).decode("ascii")}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), "utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)
