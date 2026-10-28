"""Camera registry: editorial roles + per-camera runtime state."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from dj_director.config.schema import (COVER_MASTER, ROLE_COVERAGE, CameraConfig, CameraRole, ScreenDirection,
                                       ShotSize, Subject, ViewAngle)


@dataclass
class Camera:
    name: str                      # OBS scene name (current)
    role: CameraRole
    is_master: bool
    fallback: bool
    weight: float
    priority: int
    min_duration: float
    pref_min: float
    pref_max: float
    hard_cap: float                # 0 = class default (secondaries) / none (master)
    cooldown: float
    allowed_states: frozenset[str] = frozenset()
    enabled: bool = True
    available: bool = True         # scene exists in OBS right now
    uuid: str = ""
    last_used_end: float = -1e9    # clock time the camera last went off air
    uses: int = 0
    airtime: float = 0.0
    # framing metadata (UNKNOWN / "" = not known)
    shot_size: ShotSize = ShotSize.UNKNOWN
    subject: Subject = Subject.UNKNOWN
    view_angle: ViewAngle = ViewAngle.UNKNOWN
    visual_group: str = ""
    source_group: str = ""
    screen_direction: ScreenDirection = ScreenDirection.UNKNOWN

    @classmethod
    def from_config(cls, c: CameraConfig) -> "Camera":
        d = c.durations()
        return cls(name=c.scene_name, role=c.role, is_master=c.is_master, fallback=c.fallback,
                   weight=c.weight, priority=c.priority, min_duration=d.min, pref_min=d.pref_min,
                   pref_max=d.pref_max, hard_cap=d.hard_cap, cooldown=d.cooldown,
                   allowed_states=frozenset(c.allowed_states), enabled=c.enabled, uuid=c.scene_uuid,
                   shot_size=c.shot_size, subject=c.subject, view_angle=c.view_angle,
                   visual_group=c.visual_group.strip(), source_group=c.source_group.strip(),
                   screen_direction=c.screen_direction)

    @property
    def coverage_class(self) -> str:
        return COVER_MASTER if self.is_master else ROLE_COVERAGE.get(self.role, "side")

    @property
    def usable(self) -> bool:
        return self.enabled and self.available

    def allows(self, state_value: str) -> bool:
        return not self.allowed_states or state_value in self.allowed_states


class CameraRegistry:
    def __init__(self, cameras: Iterable[Camera] = ()) -> None:
        self._cams: dict[str, Camera] = {}
        for c in cameras:
            self._cams[c.name] = c

    @classmethod
    def from_configs(cls, configs: Iterable[CameraConfig]) -> "CameraRegistry":
        return cls(Camera.from_config(c) for c in configs if c.scene_name)

    def __iter__(self):
        return iter(self._cams.values())

    def __len__(self) -> int:
        return len(self._cams)

    def get(self, name: Optional[str]) -> Optional[Camera]:
        return self._cams.get(name) if name else None

    def usable(self) -> list[Camera]:
        return [c for c in self._cams.values() if c.usable]

    def master(self) -> Optional[Camera]:
        for c in self._cams.values():
            if c.is_master and c.usable:
                return c
        return None

    def anchor(self) -> Optional[Camera]:
        """Master if usable, else a fallback camera, else the widest usable shot."""
        m = self.master()
        if m:
            return m
        for c in self._cams.values():
            if c.fallback and c.usable:
                return c
        order = [CameraRole.WIDE, CameraRole.SIDE, CameraRole.BEHIND, CameraRole.CROWD, CameraRole.CUSTOM,
                 CameraRole.OVERHEAD, CameraRole.DETAIL]
        for role in order:
            for c in self._cams.values():
                if c.role == role and c.usable:
                    return c
        return None

    def set_available(self, scene_names: Iterable[str]) -> list[str]:
        """Mark cameras available iff their scene exists. Returns newly missing names."""
        present = set(scene_names)
        missing = []
        for c in self._cams.values():
            was = c.available
            c.available = c.name in present
            if was and not c.available:
                missing.append(c.name)
        return missing

    def rename(self, old: str, new: str) -> bool:
        cam = self._cams.pop(old, None)
        if cam is None:
            return False
        cam.name = new
        self._cams[new] = cam
        return True

    def is_anchor(self, cam: Optional[Camera]) -> bool:
        return cam is not None and cam is self.anchor()
