"""Sync-offset calibration: cut between two cameras exactly on the beat grid.

The operator plays a track with a clear kick, watches OBS (program output or a
test recording) and moves the offset until each cut visibly lands on the kick.
The offset compensates for everything the software cannot measure: camera
capture delay, OBS processing and the video presentation delay.
"""

from __future__ import annotations

from typing import Optional

from dj_director.core.events import MusicSnapshot

MIN_CONFIDENCE = 0.45


class CalibrationRunner:
    def __init__(self, cam_a: str, cam_b: str, every_beats: int = 8) -> None:
        if cam_a == cam_b:
            raise ValueError("calibration needs two different cameras")
        self.cams = (cam_a, cam_b)
        self.every = max(1, int(every_beats))
        self.current = cam_a
        self._fired_target = 0.0
        self.message = "waiting for a steady beat"
        self.cuts = 0

    def next_target(self, now: float, snap: Optional[MusicSnapshot], offset: float) -> float:
        """Next grid beat (beat_index multiple of ``every``) whose fire time is in the future."""
        if snap is None or snap.beat.period <= 0 or snap.beat.confidence < MIN_CONFIDENCE:
            return 0.0
        b = snap.beat
        t, idx = b.next_beat_t, b.beat_index + 1      # next_beat_t is beat number beat_index+1
        while t - offset <= now or idx % self.every:
            t += b.period
            idx += 1
        return t

    def tick(self, now: float, snap: Optional[MusicSnapshot], offset: float) -> Optional[str]:
        """Return the scene to cut to *now*, or None."""
        if snap is None or snap.beat.confidence < MIN_CONFIDENCE:
            self.message = "waiting for a steady beat (play a track with a clear kick)"
            return None
        b = snap.beat
        if b.period <= 0 or b.last_beat_t <= 0:
            return None
        # grid beat (index multiple of `every`) at or just before now + offset
        aim = now + offset
        idx = b.beat_index + int((aim - b.last_beat_t) // b.period)
        idx -= idx % self.every
        t = b.last_beat_t + (idx - b.beat_index) * b.period
        if 0 <= aim - t < 0.25 and abs(t - self._fired_target) > b.period / 2:
            self._fired_target = t
            self.current = self.cams[1] if self.current == self.cams[0] else self.cams[0]
            self.cuts += 1
            self.message = f"cut {self.cuts} at {b.bpm:.1f} BPM — does it land on the kick?"
            return self.current
        return None
