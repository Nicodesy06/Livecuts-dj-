"""Mixer gestures heard in the audio: EQ / isolator kills and filter sweeps.

Works with any source (console, controller, MPC, turntables through a mixer)
because it only listens to the master output; no MIDI is needed.

The key distinction is *who* removed the bass:

* **The DJ** (EQ / isolator kill, filter): one end of the spectrum collapses
  while the rest of the mix stays exactly as it was - same mids and highs,
  same claps and hats. A kill is abrupt (well under 1.5 s); a filter sweep
  moves its cutoff band after band over seconds.
* **The track itself** (breakdown, elements dropping out): the low end goes,
  but the arrangement around it changes too (claps stop, pads come in, the
  overall level and density move). That is handled by the music-state
  estimator (sparse / re-entry), not here.

Measured per spectral band (20-60-120-250-500-1k-2k-4k-8k-14k Hz) as the
current level (1 s) against a reference (the 1.5-6 s before the gesture,
frozen once something starts to drop). Events:

* ``eq_cut``     detail "low" / "high": abrupt kill of the lows (or highs)
* ``filter``     detail "high-pass" / "low-pass": progressive sweep
* ``eq_return``  the cut part comes back; abrupt (bass swap, drop) = high
                 confidence, slow release = low confidence

When the arrangement only drops kick and bass while every other element
stays identical, the audio is indistinguishable from an EQ kill: that case is
reported as a DJ gesture (with its confidence), never as a certainty.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Optional

import numpy as np

from dj_director.core.events import MusicEvent
from dj_director.music.features import PROFILE_EDGES_HZ

N_BANDS = len(PROFILE_EDGES_HZ) - 1          # 9
STEP = 0.1                                   # s between evaluations
CUR_S = 1.0                                  # current level window
REF_FROM, REF_TO = 6.0, 1.5                  # reference window [t-6, t-1.5]
CUT_DB = -10.0                               # a band counts as removed below this
DROP_START_DB = -3.0                         # something starts to go
REST_TOL_DB = 4.0                            # untouched bands must stay within this
RETURN_DB = -4.0
ABRUPT_S = 1.5
MAX_GESTURE_S = 90.0


class MixGestureDetector:
    def __init__(self, frame_rate: float, silence_db: float = -55.0) -> None:
        self.fr = frame_rate
        self.silence_db = silence_db
        self._acc = np.zeros(N_BANDS)
        self._acc_n = 0
        self._acc_rms = 0.0
        self._next_eval: Optional[float] = None
        self.hist: deque[tuple[float, np.ndarray, float]] = deque(maxlen=int(12 / STEP))  # (t, power, rms dB)
        self.ref: Optional[np.ndarray] = None   # frozen reference (dB) during a gesture
        self.pending_since: Optional[float] = None
        self.side = ""                          # "low" | "high" while pending / active
        self.active: Optional[str] = None       # "eq_cut" | "filter" while a gesture is on
        self.active_since = 0.0
        self.max_cut = 0
        self.return_start: Optional[float] = None
        self.events: list[MusicEvent] = []
        self.last_atten: np.ndarray = np.zeros(N_BANDS)

    # ------------------------------------------------------------------ public
    @property
    def gesture(self) -> str:
        """"" or a short description of the gesture in progress (for the director)."""
        if not self.active:
            return ""
        return f"{self.active}:{self.side}"

    def update(self, t: float, profile: np.ndarray, rms: float, beat_conf: float = 0.0) -> None:
        self._acc += np.power(10.0, profile)
        self._acc_rms += rms * rms
        self._acc_n += 1
        if self._next_eval is None:
            self._next_eval = t + STEP
        if t < self._next_eval:
            return
        self._next_eval = t + STEP
        power = self._acc / self._acc_n
        rms_db = 10 * math.log10(self._acc_rms / self._acc_n + 1e-12)
        self._acc[:] = 0
        self._acc_rms = 0.0
        self._acc_n = 0
        self.hist.append((t, power, rms_db))
        self._evaluate(t, beat_conf)

    def drain_events(self) -> list[MusicEvent]:
        ev, self.events = self.events, []
        return ev

    # ------------------------------------------------------------------ internals
    def _window_db(self, t0: float, t1: float) -> Optional[np.ndarray]:
        pts = [p for (t, p, _) in self.hist if t0 <= t <= t1]
        if len(pts) < 3:
            return None
        return 10 * np.log10(np.mean(pts, axis=0) + 1e-12)

    def _silent(self, t: float) -> bool:
        recent = [r for (tt, _, r) in self.hist if t - tt <= CUR_S]
        return not recent or float(np.mean(recent)) < self.silence_db + 6

    @staticmethod
    def _cut_count(att: np.ndarray, side: str) -> int:
        idx = range(N_BANDS) if side == "low" else range(N_BANDS - 1, -1, -1)
        k = 0
        for i in idx:
            if att[i] <= CUT_DB:
                k += 1
            else:
                break
        return k

    @staticmethod
    def _rest_dev(att: np.ndarray, side: str, k: int) -> float:
        """Largest change among the bands the gesture has not reached (one transition band skipped)."""
        rest = range(k + 1, N_BANDS) if side == "low" else range(0, N_BANDS - k - 1)
        rest = [i for i in rest if (side == "low" and i >= 3) or (side == "high" and i <= 5)]
        if not rest:
            return 99.0
        return float(np.max(np.abs(att[list(rest)])))

    def _evaluate(self, t: float, beat_conf: float) -> None:
        if self._silent(t):
            self._reset()
            return
        cur = self._window_db(t - CUR_S, t)
        if cur is None:
            return
        if self.ref is None:
            ref = self._window_db(t - REF_FROM, t - REF_TO)
            if ref is None or t - self.hist[0][0] < REF_FROM - 0.5:
                return
        else:
            ref = self.ref
        att = cur - ref
        self.last_atten = att

        if self.active:
            self._track_active(t, att, beat_conf)
            return

        low_go = float(np.mean(att[0:2])) <= DROP_START_DB
        high_go = float(np.mean(att[7:9])) <= DROP_START_DB
        if self.pending_since is None:
            if low_go or high_go:
                # something starts to go: freeze the reference from just before
                self.ref = ref
                self.pending_since = t
                self.side = "low" if (low_go and not high_go) or (
                    low_go and np.mean(att[0:2]) <= np.mean(att[7:9])) else "high"
            return

        side = self.side
        k = self._cut_count(att, side)
        rest = self._rest_dev(att, side, k)
        elapsed = t - self.pending_since
        if rest > REST_TOL_DB + 2 or elapsed > 8.0:
            self._reset()                       # the whole mix changed, or nothing decisive: not a gesture
            return
        had_content = self._had_content(side)
        if k >= 2 and rest <= REST_TOL_DB and had_content:
            if elapsed <= ABRUPT_S and k <= 3:
                kind = "eq_cut"
            elif k >= 3 or elapsed > ABRUPT_S:
                kind = "filter"
            else:
                return
            depth = float(-np.mean(att[:k] if side == "low" else att[N_BANDS - k:]))
            # the evidence is the shape: one end gone, everything else untouched (depth keeps growing
            # after detection, so it only adds a little)
            conf = float(np.clip(0.7 + 0.15 * (1 - rest / REST_TOL_DB) + 0.1 * beat_conf
                                 + max(0.0, min(depth - 12.0, 20.0)) / 100.0, 0.3, 0.95))
            self.active, self.active_since, self.max_cut = kind, t, k
            detail = side if kind == "eq_cut" else ("high-pass" if side == "low" else "low-pass")
            self.events.append(MusicEvent(t, kind, conf, f"{detail}: -{depth:.0f} dB, rest within {rest:.1f} dB"))

    def _had_content(self, side: str) -> bool:
        """The removed end really carried energy before (a mix without bass cannot lose it)."""
        r = self.ref
        if r is None:
            return False
        if side == "low":
            return float(np.max(r[0:2])) > float(np.mean(r[4:7])) - 15
        return float(np.max(r[7:9])) > float(np.mean(r[2:5])) - 30

    def _track_active(self, t: float, att: np.ndarray, beat_conf: float) -> None:
        side = self.side
        k = self._cut_count(att, side)
        self.max_cut = max(self.max_cut, k)
        if self.active == "eq_cut" and k >= 4:
            self.active = "filter"              # a kill that keeps moving up is a sweep
        rest = self._rest_dev(att, side, k)
        if t - self.active_since > MAX_GESTURE_S or (rest > REST_TOL_DB + 4 and k == 0):
            self._reset()                       # track changed under the gesture: stop following it
            return
        edge = att[0:2] if side == "low" else att[7:9]
        back = float(np.min(edge))
        if back > CUT_DB and self.return_start is None:
            self.return_start = t
        if back >= RETURN_DB:
            dur = t - (self.return_start if self.return_start is not None else t)
            conf = float(np.clip(0.95 - max(0.0, dur - 0.6) * 0.25 + 0.05 * beat_conf, 0.35, 0.95))
            what = "bass" if side == "low" else "highs"
            self.events.append(MusicEvent(t, "eq_return", conf, f"{what} back in {dur:.1f} s"))
            self._reset()
            return
        if back <= CUT_DB:
            self.return_start = None

    def _reset(self) -> None:
        self.ref = None
        self.pending_since = None
        self.active = None
        self.side = ""
        self.return_start = None
        self.max_cut = 0
