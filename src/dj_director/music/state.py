"""Music state estimation from frame features.

States describe *observable* behaviour (e.g. "low-frequency energy is far
below the groove reference"), not certain musicological labels. Every state
has a confidence and a minimum dwell time; transitions use hysteresis so the
state does not flicker. Discrete events (re-entry, transition candidate,
sparse start) are emitted with a confidence and are always probabilistic.

Time windows (seconds) and why:
* short  0.3 s : transients, loudness right now (re-entry detection)
* medium 1.5 s : current texture; long enough to ignore single kicks
* long   20 s  : context the medium window is compared against
* groove reference (tau 30 s, updated only during groove-like states):
  "what the full groove sounds like", used to detect reduced sections that
  last longer than the long window would otherwise adapt to.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from dj_director.core.events import MusicEvent, MusicFeatures, MusicState
from dj_director.music.features import Boxcar, Ema, Frame, db

GROOVE_LIKE = (MusicState.STABLE_GROOVE, MusicState.INCREASING, MusicState.REENTRY)


@dataclass
class StateThresholds:
    sparse_enter_db: float = -8.0      # medium low band vs groove reference
    sparse_exit_db: float = -4.0
    sparse_density_ratio: float = 0.45
    reentry_jump_db: float = 7.0       # short low band above the sparse level
    reentry_min_sparse_s: float = 3.0
    trend_db: float = 2.5
    density_up: float = 1.35
    density_down: float = 0.7
    novelty_sigma: float = 4.0
    novelty_min: float = 0.12
    dwell_s: float = 0.8
    warmup_s: float = 6.0
    silence_hold_s: float = 1.0
    groove_beat_conf: float = 0.3

    def scaled(self, sensitivity: float) -> "StateThresholds":
        s = max(0.25, sensitivity)
        return StateThresholds(
            sparse_enter_db=self.sparse_enter_db / s, sparse_exit_db=self.sparse_exit_db / s,
            sparse_density_ratio=self.sparse_density_ratio, reentry_jump_db=self.reentry_jump_db / s,
            reentry_min_sparse_s=self.reentry_min_sparse_s, trend_db=self.trend_db / s,
            density_up=1 + (self.density_up - 1) / s, density_down=1 - (1 - self.density_down) / s,
            novelty_sigma=self.novelty_sigma / s, novelty_min=self.novelty_min / s,
            dwell_s=self.dwell_s, warmup_s=self.warmup_s, silence_hold_s=self.silence_hold_s,
            groove_beat_conf=self.groove_beat_conf)


class MusicStateEstimator:
    def __init__(self, frame_rate: float, silence_db: float = -55.0, sensitivity: float = 1.0,
                 thresholds: StateThresholds | None = None) -> None:
        fr = frame_rate
        self.fr = fr
        self.silence_db = silence_db
        self.th = (thresholds or StateThresholds()).scaled(sensitivity)
        self.rms_s, self.rms_m, self.rms_l = Ema(0.3, fr), Boxcar(1.5, fr), Ema(20, fr)
        self.low_s, self.low_m, self.low_l = Ema(0.3, fr), Boxcar(1.5, fr), Ema(20, fr)
        self.mid_m, self.high_m = Ema(1.5, fr), Ema(1.5, fr)
        self.cent_m = Ema(1.5, fr)
        self.flux_s = Ema(0.3, fr)
        self.flux_base = Ema(2.0, fr)
        self.prof_s, self.prof_l = None, None
        self._a_ps = 1 - math.exp(-1 / (4.0 * fr))
        self._a_pl = 1 - math.exp(-1 / (24.0 * fr))
        self.nov_base, self.nov_dev = Ema(30, fr), Ema(30, fr)
        self.dens_l = Ema(20, fr)
        self._onsets: deque[float] = deque()
        self._last_onset = -1e9
        self._prev_flux = [0.0, 0.0]
        self.ref_low = None                # groove reference (dB)
        self._a_ref_up = 1 - math.exp(-1 / (8.0 * fr))     # follows a fuller groove quickly
        self._a_ref_down = 1 - math.exp(-1 / (45.0 * fr))  # forgets it slowly
        self._last_transition_event = -1e9
        # state machine
        self.state = MusicState.NO_SIGNAL
        self.previous = MusicState.NO_SIGNAL
        self.state_since = 0.0
        self.confidence = 0.0
        self._cand = None
        self._cand_since = 0.0
        self._signal_since = None
        self._silent_since = None
        self._sparse_level = 0.0           # low band level during the sparse section
        self._sparse_since = 0.0
        self._last_sparse_end = -1e9
        self._last_sparse_duration = 0.0
        self._novelty_high = False
        self._eval_every = max(1, int(0.1 * fr))
        self._n = 0
        self.features = MusicFeatures(-120, -120, -120, -120, 0, 0, 0, 0, 0, 1, 0)
        self.events: list[MusicEvent] = []

    # ------------------------------------------------------------------ per frame
    def update(self, f: Frame, t: float, beat_conf: float) -> None:
        self._n += 1
        rs = self.rms_s.update(f.rms ** 2)
        self.rms_m.update(f.rms ** 2)
        self.rms_l.update(f.rms ** 2)
        self.low_s.update(f.low)
        self.low_m.update(f.low)
        self.low_l.update(f.low)
        self.mid_m.update(f.mid)
        self.high_m.update(f.high)
        self.cent_m.update(f.centroid)
        self.flux_s.update(f.flux)
        base = self.flux_base.update(f.flux)
        # spectral profile novelty (log10 band powers)
        if self.prof_s is None:
            self.prof_s = f.profile.copy()
            self.prof_l = f.profile.copy()
        else:
            self.prof_s += self._a_ps * (f.profile - self.prof_s)
            self.prof_l += self._a_pl * (f.profile - self.prof_l)
        # onset peak picking on flux (local max above adaptive threshold, >= 60 ms apart)
        p2, p1 = self._prev_flux
        if p1 > p2 and p1 >= f.flux and p1 > base * 1.4 + 1e-4 and t - self._last_onset > 0.06:
            self._onsets.append(t)
            self._last_onset = t
        self._prev_flux = [p1, f.flux]
        while self._onsets and t - self._onsets[0] > 2.0:
            self._onsets.popleft()
        dens = len(self._onsets) / 2.0
        if db(rs) > self.silence_db:
            self.dens_l.update(dens)
        if self._n % self._eval_every == 0:
            self._evaluate(t, dens, beat_conf)

    # ------------------------------------------------------------------ evaluation
    def _evaluate(self, t: float, dens: float, beat_conf: float) -> None:
        th = self.th
        rms_s, rms_m, rms_l = db(self.rms_s.value), db(self.rms_m.value), db(self.rms_l.value)
        low_s, low_m = db(self.low_s.value), db(self.low_m.value)
        if self.ref_low is None and self.low_l.n > self.fr * 4:
            self.ref_low = db(self.low_l.value)
        ref = self.ref_low if self.ref_low is not None else db(self.low_l.value)
        low_vs_groove = low_m - ref
        dens_l = self.dens_l.value or 0.0
        density_ratio = dens / max(dens_l, 0.25)
        trend = rms_m - rms_l
        nov_raw = float(np.mean(np.abs(self.prof_s - self.prof_l))) if self.prof_s is not None else 0.0
        nb = self.nov_base.value if self.nov_base.value is not None else nov_raw
        nd = self.nov_dev.value if self.nov_dev.value is not None else 0.05
        nov_z = (nov_raw - nb) / max(nd, 0.01)
        novelty = float(np.clip((nov_raw - th.novelty_min) / max(th.novelty_min, 1e-3), 0, 1)
                        * np.clip(nov_z / th.novelty_sigma, 0, 1))
        novelty_ready = self._signal_since is not None and t - self._signal_since > 15.0
        if not novelty_ready:
            novelty = 0.0
        if not self._novelty_high:          # don't let a transition teach its own baseline
            self.nov_base.update(nov_raw)
            self.nov_dev.update(abs(nov_raw - nb))
        self.features = MusicFeatures(
            rms_db=rms_s, low_db=low_m, mid_db=db(self.mid_m.value), high_db=db(self.high_m.value),
            onset_density=dens, flux=self.flux_s.value or 0.0, novelty=novelty,
            energy_trend_db=trend, low_vs_groove_db=low_vs_groove, density_ratio=density_ratio,
            centroid_hz=self.cent_m.value or 0.0)

        # --- silence / warm-up
        if rms_s < self.silence_db:
            self._silent_since = self._silent_since if self._silent_since is not None else t
            if t - self._silent_since >= th.silence_hold_s or self.state == MusicState.NO_SIGNAL:
                self._signal_since = None
                self._set(MusicState.NO_SIGNAL, 1.0, t, force=True)
            return
        self._silent_since = None
        if self._signal_since is None:
            self._signal_since = t
        if t - self._signal_since < th.warmup_s:
            self._set(MusicState.UNCERTAIN, 0.3, t, force=True)
            return

        cur = self.state
        # --- re-entry (event): strong return of low end after a reduced section
        if cur == MusicState.SPARSE or t - self._last_sparse_end < 1.5:
            sparse_dur = (t - self._sparse_since) if cur == MusicState.SPARSE else self._last_sparse_duration
            jump = low_s - self._sparse_level
            if (sparse_dur >= th.reentry_min_sparse_s and jump >= th.reentry_jump_db
                    and low_s - ref > th.sparse_exit_db - 1.0 and rms_s > self.silence_db + 10):
                conf = float(np.clip(0.35 + (jump - th.reentry_jump_db) / 20 + min(sparse_dur, 16) / 40
                                     + 0.15 * beat_conf, 0, 0.95))
                self.events.append(MusicEvent(t, "reentry", conf,
                                              f"low end +{jump:.0f} dB after {sparse_dur:.0f}s reduced"))
                self._last_sparse_end = -1e9
                self._set(MusicState.REENTRY, conf, t, force=True)
                return
        if cur == MusicState.REENTRY and t - self.state_since < 4.0:
            return

        # --- sparse / reduced rhythm (with hysteresis)
        is_sparse = (low_vs_groove < th.sparse_enter_db
                     or (density_ratio < th.sparse_density_ratio and trend < -th.trend_db))
        if cur == MusicState.SPARSE:
            is_sparse = low_vs_groove < th.sparse_exit_db or density_ratio < th.sparse_density_ratio
            self._sparse_level = min(self._sparse_level, low_m) if is_sparse else self._sparse_level
        if is_sparse:
            conf = float(np.clip(0.5 + (-(low_vs_groove) + th.sparse_enter_db) / 12, 0.3, 0.95))
            if cur != MusicState.SPARSE and self._set(MusicState.SPARSE, conf, t):
                self._sparse_since = t
                self._sparse_level = low_m
                self.events.append(MusicEvent(t, "sparse_start", conf,
                                              f"low end {low_vs_groove:.0f} dB vs groove"))
            elif cur == MusicState.SPARSE:
                self.confidence = conf
            return
        if cur == MusicState.SPARSE:
            self._last_sparse_end = t
            self._last_sparse_duration = t - self._sparse_since

        # --- transition candidate (spectral novelty spike)
        # The state lasts 3-6 s after the event; elevated novelty afterwards is
        # the new material settling in, not a new transition.
        since_event = t - self._last_transition_event
        if novelty > 0.5 and not self._novelty_high and since_event > 8.0:
            self._novelty_high = True
            self._last_transition_event = t
            since_event = 0.0
            self.events.append(MusicEvent(t, "transition", novelty,
                                          f"spectral change {nov_raw:.2f} ({nov_z:.1f} sigma)"))
            self._set(MusicState.TRANSITION, novelty, t, force=True)
            return
        if self._novelty_high and (novelty < 0.2 or since_event > 20.0):
            self._novelty_high = False
        if cur == MusicState.TRANSITION and (since_event < 3.0 or (since_event < 6.0 and novelty > 0.5)):
            return

        # --- trends (need a filled long window)
        trends_ready = t - self._signal_since > 12.0
        if trends_ready and trend > th.trend_db or density_ratio > th.density_up:
            conf = float(np.clip(0.4 + (trend - th.trend_db) / 8 + (density_ratio - th.density_up), 0.3, 0.9))
            self._set(MusicState.INCREASING, conf, t)
        elif trends_ready and (trend < -th.trend_db or density_ratio < th.density_down):
            conf = float(np.clip(0.4 + (-trend - th.trend_db) / 8, 0.3, 0.9))
            self._set(MusicState.DECREASING, conf, t)
        elif beat_conf >= th.groove_beat_conf:
            self._set(MusicState.STABLE_GROOVE, float(np.clip(0.4 + beat_conf * 0.6, 0, 0.95)), t)
        else:
            self._set(MusicState.UNCERTAIN, 0.3, t)

        if self.state in GROOVE_LIKE:
            a = self._a_ref_up if low_m > ref else self._a_ref_down
            self.ref_low = ref + min(1.0, a * self._eval_every) * (low_m - ref)

    def _set(self, state: MusicState, conf: float, t: float, force: bool = False) -> bool:
        """Request a state; applied after ``dwell_s`` of persistence unless forced."""
        if state == self.state:
            self.confidence = conf
            self._cand = None
            return False
        if not force:
            if self._cand != state:
                self._cand, self._cand_since = state, t
                return False
            if t - self._cand_since < self.th.dwell_s:
                return False
        self.previous, self.state = self.state, state
        self.state_since = t
        self.confidence = conf
        self._cand = None
        return True

    def drain_events(self) -> list[MusicEvent]:
        ev, self.events = self.events, []
        return ev
