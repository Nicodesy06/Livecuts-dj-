"""Load/save configuration atomically, surviving missing or corrupt files."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from dj_director.config.paths import config_dir
from dj_director.config.schema import AppConfig, from_dict, to_dict

log = logging.getLogger(__name__)
CONFIG_FILE = "config.json"


class ConfigManager:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or (config_dir() / CONFIG_FILE)
        self.issues: list[str] = []
        self.recovered_from_corruption = False

    def load(self) -> AppConfig:
        self.issues = []
        self.recovered_from_corruption = False
        try:
            text = self.path.read_text("utf-8")
        except FileNotFoundError:
            return AppConfig()
        except OSError as e:
            self.issues.append(f"cannot read config: {e}")
            return AppConfig()
        try:
            data = json.loads(text)
        except ValueError as e:
            backup = self.path.with_name(f"{self.path.stem}.corrupt-{int(time.time())}.json")
            try:
                os.replace(self.path, backup)
            except OSError:
                backup = None
            self.recovered_from_corruption = True
            self.issues.append(f"config was corrupt ({e}); defaults loaded, backup: {backup}")
            log.warning(self.issues[-1])
            return AppConfig()
        cfg = from_dict(AppConfig, data, self.issues)
        for i in self.issues:
            log.warning("config: %s", i)
        return cfg

    def save(self, cfg: AppConfig) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(to_dict(cfg), indent=2), "utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def export(cfg: AppConfig, path: Path) -> None:
        """Export for sharing. Contains no secrets by construction."""
        path.write_text(json.dumps(to_dict(cfg), indent=2), "utf-8")
