"""Frames of every cut, saved next to the decision log for the post-set review.

At each cut the director keeps two small stills taken from the fast eye's
latest frames: the last picture of the outgoing shot ("before") and the first
picture of the incoming one ("after"). Together with the decision log they let
the review (``review.critic``) judge the edit after the show without decoding
the video recording. Writing happens on a background thread; a failure only
disables the journal, never the director.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

THUMB_W, THUMB_H = 384, 216


def frames_dir(decision_log: Path) -> Path:
    return decision_log.with_name(decision_log.stem + "-frames")


class FrameJournal:
    def __init__(self, decision_log: Path) -> None:
        self.dir = frames_dir(decision_log)
        self.enabled = True
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="frame-journal")
        self._lock = threading.Lock()

    def save(self, n: int, before: Optional[np.ndarray], after: Optional[np.ndarray]) -> tuple[str, str]:
        """Queue the two stills of cut ``n``; returns their paths relative to the log folder ("" = none)."""
        if not self.enabled:
            return "", ""
        names = [f"{self.dir.name}/{n:04d}-{kind}.jpg" if img is not None else ""
                 for kind, img in (("before", before), ("after", after))]
        jobs = [(self.dir.parent / name, img) for name, img in zip(names, (before, after)) if name]
        if jobs:
            self._pool.submit(self._write, jobs)
        return names[0], names[1]

    def _write(self, jobs) -> None:
        from dj_director.visual.imaging import encode, resize
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            for path, img in jobs:
                path.write_bytes(encode(resize(img, THUMB_W, THUMB_H), "JPG", 80))
        except Exception:
            log.exception("cannot save review frames: journal disabled")
            self.enabled = False

    def close(self) -> None:
        self._pool.shutdown(wait=True)
