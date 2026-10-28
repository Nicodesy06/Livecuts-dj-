"""Checks on the framing metadata before going live (digital crops included).

DJ Director never crops anything itself: crops are OBS scenes the user made
(e.g. ``CAM1_WIDE`` and ``CAM1_MEDIUM`` from one physical camera). These
checks only point out setups that will cut badly or look poor, so they can be
fixed and previewed (Vision page) before the show.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from dj_director.config.schema import CameraConfig, CameraRole, ShotSize

MAX_SAFE_ZOOM = 2.0          # beyond this a 1080p source shows visible resolution loss


def framing_warnings(cameras: Iterable[CameraConfig]) -> list[str]:
    cams = [c for c in cameras if c.enabled]
    out: list[str] = []
    by_source: dict[str, list[CameraConfig]] = defaultdict(list)
    for c in cams:
        if c.source_group:
            by_source[c.source_group].append(c)
        if c.crop_zoom > 1.0 and not c.source_group:
            out.append(f"{c.scene_name}: digital crop without a physical camera (set 'Source') - "
                       "it would be treated as a separate perspective")
        if c.crop_zoom > MAX_SAFE_ZOOM:
            out.append(f"{c.scene_name}: digital zoom x{c.crop_zoom:.1f} loses resolution "
                       "(fine only from a 4K source); check it in the Vision page preview")
        if c.crop_zoom > 1.0 and c.role in (CameraRole.DETAIL, CameraRole.OVERHEAD):
            out.append(f"{c.scene_name}: cropped detail - make sure hands and mixer stay fully in frame")
    for src, group in by_source.items():
        sizes = [c.shot_size for c in group if c.shot_size != ShotSize.UNKNOWN]
        dup = {s for s in sizes if sizes.count(s) > 1}
        for s in sorted(dup, key=lambda x: x.value):
            names = ", ".join(c.scene_name for c in group if c.shot_size == s)
            out.append(f"{names}: same camera '{src}' at the same scale ({s.value}) - cutting between them "
                       "is a jump cut; give them different scales")
        if len(group) > 1 and len(sizes) < len(group):
            out.append(f"camera '{src}': set the scale of every crop, so they are treated as zooms, not cuts")
    return out
