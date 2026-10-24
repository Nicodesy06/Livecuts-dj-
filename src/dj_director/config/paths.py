"""Per-user writable locations (never the install directory)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dj_director import APP_ID


def _base_dirs() -> tuple[Path, Path]:
    override = os.environ.get("DJ_DIRECTOR_HOME")
    if override:
        p = Path(override)
        return p, p
    if sys.platform == "win32":
        roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return roaming / APP_ID, local / APP_ID
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    xdg_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return xdg_config / "dj-director", xdg_state / "dj-director"


def config_dir() -> Path:
    d = _base_dirs()[0]
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_dir() -> Path:
    d = _base_dirs()[1]
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d
