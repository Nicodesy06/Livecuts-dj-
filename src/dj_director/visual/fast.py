"""Fast eye: cheap per-camera measurements on small OBS stills (no AI model).

For each camera, every new frame updates:

* **quality**  exposure (black / blown out), sharpness against the camera's own
  reference (blurred, or something right in front of the lens) and frozen
  image (identical frames: capture stopped). A problem must persist for a
  moment (strobes and flashes are not problems) and becomes a *veto*: the
  director does not cut to that camera and leaves it early if it is on air.
* **activity**  amount of movement, inside the camera's region of interest
  when one is set (e.g. the mixer in the Detail shot). The engine never uses it
  as presence: it tracks novelty against a baseline (see engine.VisualNovelty).
* **beat coherence**  how much the movement pulses with the musical beat
  (people dancing together, lights on the beat). Needs a few frames per beat,
  so the reveal camera is sampled more often.

Everything is measured, nothing is guessed: hand movement is "movement in the
mixer region", not "EQ adjustment".

Quality gate (0.4): each camera is HEALTHY (fresh frames, warmed up, no
problem), FAILED (a persisting problem) or UNKNOWN (no picture, stale, still
learning). UNKNOWN is never read as "good". Club lighting is accounted for:
"black" means an empty picture (dark *and* flat), sharpness is measured
relative to contrast (a dark moment is not a blur), a strobe changes brightness
but does not move the picture. Extra problems: the camera itself moving
(repositioning: the whole image shifts consistently) and, with a region of
interest, the region alone losing its detail ("mixer not visible").
"""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

ANALYSIS_W = 128            # frames are reduced to this width before analysis
ISSUE_PERSIST = 1.5         # s a problem must last before it vetoes the camera
CLEAR_PERSIST = 1.0         # s of good frames before a veto is lifted
FROZEN_AFTER = 4.0          # s of bit-identical frames
STALE_AFTER = 3.0           # s without frames: no information (never a veto)
MOTION_FULL_SCALE = 0.06    # mean abs frame difference (0..1) that maps to activity 1.0


@dataclass(frozen=True)
class CameraVisionStatus:
    name: str
    has_frame: bool
    age: float                 # s since the last frame
    fps: float
    brightness: float          # 0..1
    sharpness: float           # relative to the camera's own reference (1 = normal)
    motion: float              # 0..1 activity (region of interest if set)
    coherence: float           # 0..1 movement pulsing with the beat
    issue: str                 # "" or a short description of a quality problem
    roi: Optional[tuple]
    quality: str = "UNKNOWN"   # HEALTHY / UNKNOWN / FAILED


def _small_gray(rgb: np.ndarray) -> np.ndarray:
    h, w = rgb.shape[:2]
    step = max(1, w // ANALYSIS_W)
    g = rgb[::step, ::step]
    g = g[..., 0] * 0.299 + g[..., 1] * 0.587 + g[..., 2] * 0.114 if g.ndim == 3 else g.astype(float)
    return g.astype(np.float32) / 255.0


def _sharpness(g: np.ndarray) -> float:
    """Edge energy relative to contrast: a darker or dimmer frame is not a blurred one."""
    if g.shape[0] < 3 or g.shape[1] < 3:
        return 0.0
    lap = 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(np.mean(np.abs(lap))) / (float(g.std()) + 0.03)


def _global_shift(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """(peak, shift in px) of the phase correlation of two frames: the whole picture moving."""
    za = (a - a.mean()) / (a.std() + 1e-3)
    zb = (b - b.mean()) / (b.std() + 1e-3)
    fa, fb = np.fft.fft2(za), np.fft.fft2(zb)
    r = fa * np.conj(fb)
    r /= np.abs(r) + 1e-9
    corr = np.fft.ifft2(r).real
    iy, ix = np.unravel_index(int(np.argmax(corr)), corr.shape)
    h, w = corr.shape
    dy, dx = (iy if iy <= h // 2 else iy - h), (ix if ix <= w // 2 else ix - w)
    return float(corr.max()), float((dx * dx + dy * dy) ** 0.5)


def _crop(g: np.ndarray, roi: Optional[tuple]) -> np.ndarray:
    if not roi:
        return g
    h, w = g.shape
    x, y, rw, rh = roi
    x0, y0 = int(x * w), int(y * h)
    x1, y1 = max(x0 + 2, int((x + rw) * w)), max(y0 + 2, int((y + rh) * h))
    return g[y0:y1, x0:x1]


class CameraVision:
    def __init__(self, name: str) -> None:
        self.name = name
        self.prev: Optional[np.ndarray] = None
        self.last_t = -1e9
        self.times: deque[float] = deque(maxlen=20)
        self.brightness = 0.0
        self.clipped = 0.0
        self.sharp = 0.0
        self.sharp_ref: Optional[float] = None
        self.sharp_samples = 0
        self.std = 0.0
        self.roi_sharp = 0.0
        self.roi_ref: Optional[float] = None
        self.moving_frames = 0
        self.motion = 0.0
        self.motion_full = 0.0
        self.same_since: Optional[float] = None
        self.series: deque[tuple[float, float]] = deque(maxlen=200)   # (t, full-frame motion)
        self.light: deque[tuple[float, float]] = deque(maxlen=200)    # (t, mean brightness)
        self.raw_issue = ""
        self.raw_issue_since = 0.0
        self.issue = ""
        self.ok_since = 0.0
        self.rgb: Optional[np.ndarray] = None
        self._roi: Optional[tuple] = None

    def update(self, t: float, rgb: np.ndarray, roi: Optional[tuple] = None) -> None:
        g = _small_gray(rgb)
        self.rgb = rgb
        self.times.append(t)
        self.brightness = float(g.mean())
        self.std = float(g.std())
        self.clipped = float((g > 0.97).mean())
        self.sharp = _sharpness(g)
        self.roi_sharp = _sharpness(_crop(g, roi)) if roi else 0.0
        self._roi = roi
        if self.prev is not None and self.prev.shape == g.shape:
            peak, shift = _global_shift(self.prev, g)
            # the whole picture slides consistently: the camera is being moved (not people, not lights)
            self.moving_frames = self.moving_frames + 1 if (peak > 0.25 and shift >= 2.0) else 0
            diff = np.abs(g - self.prev)
            self.motion_full = float(diff.mean())
            reg = _crop(diff, roi)
            self.motion = min(1.0, float(reg.mean()) / MOTION_FULL_SCALE) if reg.size else 0.0
            if float(diff.max()) == 0.0:
                self.same_since = self.same_since if self.same_since is not None else self.last_t
            else:
                self.same_since = None
            self.series.append((t, self.motion_full))
        self.light.append((t, self.brightness))
        self.prev = g
        self.last_t = t
        self._update_issue(t)

    def _raw_issue(self, t: float) -> str:
        if self.brightness < 0.04 and self.std < 0.03:
            return "black frame"                   # empty picture, not just a dark club
        if self.clipped > 0.6:
            return "overexposed"
        if self.same_since is not None and t - self.same_since >= FROZEN_AFTER:
            return "frozen image"
        if self.moving_frames >= 3:
            return "camera moving"
        if self.sharp_ref is not None and self.sharp_samples >= 10 and self.sharp < 0.35 * self.sharp_ref:
            return "blurred or blocked"
        if (self._roi and self.roi_ref is not None and self.sharp_samples >= 10
                and self.roi_sharp < 0.35 * self.roi_ref):
            return "mixer not visible"
        return ""

    def quality(self, t: float) -> str:
        if t - self.last_t > STALE_AFTER:
            return "UNKNOWN"
        if self.issue:
            return "FAILED"
        return "HEALTHY" if self.sharp_samples >= 10 else "UNKNOWN"

    def _update_issue(self, t: float) -> None:
        raw = self._raw_issue(t)
        if not raw and self.brightness >= 0.05:
            # reference sharpness learns only from good frames (slow EMA, ~60 frames)
            if self.sharp_ref is None:
                self.sharp_ref = self.sharp
            else:
                self.sharp_ref += (self.sharp - self.sharp_ref) * 0.03
            if self._roi:
                self.roi_ref = self.roi_sharp if self.roi_ref is None else (
                    self.roi_ref + (self.roi_sharp - self.roi_ref) * 0.03)
            self.sharp_samples += 1
        if raw != self.raw_issue:
            self.raw_issue, self.raw_issue_since = raw, t
        if raw and t - self.raw_issue_since >= ISSUE_PERSIST:
            self.issue = raw
            self.ok_since = 0.0
        elif not raw and self.issue:
            if not self.ok_since:
                self.ok_since = t
            if t - self.ok_since >= CLEAR_PERSIST:
                self.issue = ""

    def fps(self) -> float:
        if len(self.times) < 2:
            return 0.0
        span = self.times[-1] - self.times[0]
        return (len(self.times) - 1) / span if span > 0 else 0.0

    def coherence(self, now: float, beat_period: float, window: float = 8.0) -> float:
        """0..1: how strongly the picture pulses with the beat.

        Two signals are tested: full-frame movement (people jumping: often two
        peaks per beat, up and down, so the beat and half-beat periods are both
        tried) and mean brightness (lights flashing on the beat). The best
        debiased phase concentration wins. Needs >= ~2.5 samples per period.
        """
        if beat_period <= 0:
            return 0.0
        best = 0.0
        moving = [(t, m) for t, m in self.series if now - t <= window]
        if moving and float(np.mean([m for _, m in moving])) >= 0.004:
            for period in (beat_period, beat_period / 2):
                best = max(best, _phase_concentration(moving, period))
        best = max(best, _phase_concentration([(t, b) for t, b in self.light if now - t <= window], beat_period))
        return best


def _phase_concentration(pts: list[tuple[float, float]], period: float) -> float:
    """Debiased Rayleigh statistic of a (mean-removed) series at 1/period, scaled to 0..1."""
    if len(pts) < 12:
        return 0.0
    ts = np.array([p[0] for p in pts])
    xs = np.array([p[1] for p in pts])
    span = ts[-1] - ts[0]
    if span < 3 * period or len(pts) / span < 2.5 / period:
        return 0.0
    x = xs - xs.mean()
    denom = float(np.abs(x).sum())
    if denom <= 1e-6 * len(pts):
        return 0.0
    phase = 2 * math.pi * ts / period
    z = complex(float((x * np.cos(phase)).sum()), float((x * np.sin(phase)).sum()))
    r = abs(z) / denom
    # remove what chance alone produces with this many samples (~2/sqrt(N)), rescale to 0..1;
    # a clean periodic pulse gives r of about 0.6-0.7
    chance = 2.0 / math.sqrt(len(pts))
    return float(min(1.0, max(0.0, (r - chance) / max(0.65 - chance, 1e-6))))


class FastVisualAnalyzer:
    """Thread-safe store of per-camera measurements (frames in, scores out)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cams: dict[str, CameraVision] = {}
        self.rois: dict[str, tuple] = {}
        self.beat_period = 0.0
        self.clock = None                       # optional callable() -> now, for staleness

    def set_rois(self, rois: dict[str, list[float]]) -> None:
        with self._lock:
            self.rois = {k: tuple(v) for k, v in rois.items()}

    def ingest(self, name: str, t: float, rgb: np.ndarray) -> None:
        with self._lock:
            cam = self._cams.get(name)
            if cam is None:
                cam = self._cams[name] = CameraVision(name)
            cam.update(t, rgb, self.rois.get(name))

    def rename(self, old: str, new: str) -> None:
        with self._lock:
            if old in self._cams:
                cam = self._cams.pop(old)
                cam.name = new
                self._cams[new] = cam
            if old in self.rois:
                self.rois[new] = self.rois.pop(old)

    def _fresh(self, name: str, now: float) -> Optional[CameraVision]:
        cam = self._cams.get(name)
        if cam is None or now - cam.last_t > STALE_AFTER:
            return None
        return cam

    # ------------------------------------------------------------------ queries (any thread)
    def issue(self, name: str, now: float) -> str:
        with self._lock:
            cam = self._fresh(name, now)
            return cam.issue if cam is not None else ""

    def quality(self, name: str, now: float) -> str:
        with self._lock:
            cam = self._cams.get(name)
            return cam.quality(now) if cam is not None else "UNKNOWN"

    def activity(self, name: str, now: float) -> Optional[float]:
        with self._lock:
            cam = self._fresh(name, now)
            if cam is None or cam.issue or len(cam.series) < 2:
                return None
            return cam.motion

    def coherence(self, name: str, now: float) -> float:
        with self._lock:
            cam = self._fresh(name, now)
            return cam.coherence(now, self.beat_period) if cam is not None and not cam.issue else 0.0

    def latest_rgb(self, name: str, now: float, max_age: float = 10.0) -> Optional[np.ndarray]:
        with self._lock:
            cam = self._cams.get(name)
            if cam is None or cam.rgb is None or now - cam.last_t > max_age:
                return None
            return cam.rgb

    def status(self, name: str, now: float) -> CameraVisionStatus:
        with self._lock:
            cam = self._cams.get(name)
            if cam is None:
                return CameraVisionStatus(name, False, math.inf, 0.0, 0.0, 0.0, 0.0, 0.0, "", self.rois.get(name))
            rel = cam.sharp / cam.sharp_ref if cam.sharp_ref else 1.0
            return CameraVisionStatus(name, True, now - cam.last_t, cam.fps(), cam.brightness, rel,
                                      cam.motion, cam.coherence(now, self.beat_period), cam.issue,
                                      self.rois.get(name), cam.quality(now))
