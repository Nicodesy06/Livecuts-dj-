"""Structured logging: rotating text log + per-session JSONL decision log.

Secrets are never logged: the OBS password is only ever passed to the
WebSocket client, and log records are additionally scrubbed by a filter.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import logging.handlers
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Optional

from dj_director.config.paths import log_dir

_secrets: set[str] = set()


def register_secret(value: str) -> None:
    if value and len(value) >= 3:
        _secrets.add(value)


class _ScrubFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _secrets:
            msg = record.getMessage()
            for s in _secrets:
                if s in msg:
                    record.msg = msg.replace(s, "***")
                    record.args = ()
                    msg = record.msg
        return True


def setup_logging(verbose: bool = False, directory: Optional[Path] = None) -> Path:
    d = directory or log_dir()
    path = d / "dj-director.log"
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)-14s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.addFilter(_ScrubFilter())
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.addFilter(_ScrubFilter())
    sh.setLevel(logging.INFO if verbose else logging.WARNING)
    root.addHandler(sh)
    return path


def _jsonable(obj):
    if dataclasses.is_dataclass(obj):
        return {k: _jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    return obj


class DecisionLog:
    """Append-only JSONL file of director decisions and switch results for one session."""

    def __init__(self, directory: Optional[Path] = None, enabled: bool = True) -> None:
        self.enabled = enabled
        self.path = (directory or log_dir()) / time.strftime("decisions-%Y%m%d-%H%M%S.jsonl")
        self._lock = threading.Lock()
        self._fh = None

    def write(self, kind: str, event) -> None:
        if not self.enabled:
            return
        rec = {"wall": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, **_jsonable(event)}
        line = json.dumps(rec, ensure_ascii=False)
        with self._lock:
            try:
                if self._fh is None:
                    self._fh = open(self.path, "a", encoding="utf-8")
                self._fh.write(line + "\n")
                self._fh.flush()
            except OSError:
                logging.getLogger(__name__).exception("cannot write decision log")
                self.enabled = False

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None
