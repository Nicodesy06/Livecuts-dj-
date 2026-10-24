"""Causal tempo / beat tracker (original implementation, no third-party code).

Algorithm (classic onset-autocorrelation + comb approach, BTrack-like in spirit):

1. Onset strength (ODF) per frame = full-band spectral flux + 0.5 x low-band flux.
2. Every ``update_s`` seconds, autocorrelate the last ``window_s`` seconds of ODF.
3. Tempo salience for each lag L = mean of normalised autocorrelation at
   L, 2L, 3L, 4L (comb), multiplied by a log-normal tempo prior centred on
   ``prior_bpm``. The comb + prior handles most half/double-time ambiguity.
4. Tempo hysteresis: small deviations are smoothed; a different tempo (or an
   octave jump) must win several consecutive estimates before being adopted.
5. Phase: comb-filter the ODF against a beat grid at the current period,
   searching all phase offsets; the best offset is blended into a running
   beat clock that predicts the next beats *ahead of time*.
6. Confidence = periodicity salience x tempo stability. Consumers must treat
   anything below their threshold as "no reliable beat".

Downbeat / bar position is a *heuristic*: chords, bass notes and new
elements tend to change at bar starts, so the spectral-profile change between
consecutive beats is accumulated per bar position and the strongest position
is taken as beat 1. Kick/clap accents are deliberately NOT used: in dance music
they are equal on every beat or stress the backbeat (2 and 4). When no
position clearly dominates, confidence stays low - the honest answer.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass
class TempoEstimate:
    bpm: float
    salience: float
    octave_ambiguous: bool


class BeatTracker:
    def __init__(self, frame_rate: float, min_bpm: float = 70.0, max_bpm: float = 180.0,
                 prior_bpm: float = 124.0, beats_per_bar: int = 4,
                 window_s: float = 8.0, update_s: float = 0.25) -> None:
        self.fr = frame_rate
        self.min_bpm, self.max_bpm, self.prior_bpm = min_bpm, max_bpm, prior_bpm
        self.bpb = beats_per_bar
        self.n_hist = int(window_s * frame_rate)
        self._odf = np.zeros(self.n_hist)
        self._low = np.zeros(self.n_hist)
        self._count = 0                      # frames ever pushed
        self._update_every = max(1, int(update_s * frame_rate))
        self._since_update = 0
        # tempo state
        self.period = 0.0                    # frames
        self.salience = 0.0
        self._history: deque[float] = deque(maxlen=8)   # recent raw bpm estimates
        self._candidate = 0.0
        self._candidate_hits = 0
        self.octave_ambiguous = False
        # beat clock (frame domain, absolute frame counter)
        self.next_beat = 0.0
        self.last_beat = 0.0
        self.beat_index = 0
        self._phase_miss = 0
        self.phase_confidence = 0.0
        # downbeat heuristic
        self._acc = np.zeros(beats_per_bar)
        self._acc_n = 0
        self._prof_sum = None                # profile sum over the current beat interval
        self._prof_cnt = 0
        self._prev_beat_prof = None          # mean profile of the previous beat interval
        # metrical alternation test (double-time correction)
        self._pending_acc: deque[tuple[float, int]] = deque(maxlen=8)
        self._beat_acc: deque[tuple[int, float]] = deque(maxlen=16)
        self.halvings = 0
        self._last_lags = None
        self._last_weighted = None
        self.bar_position = -1               # bar position of the *last* emitted beat
        self.downbeat_confidence = 0.0
        self._low_confidence_frames = 0
        self._odf_ref = 0.0                  # typical ODF level while rhythm is confident
        self.rhythm_strength = 0.0           # current ODF level / reference (0..1)
        self._low_ref = 0.0                  # typical low-band onset level in the groove
        self._confident_updates = 0          # updates with real rhythm since this tempo was adopted
        self.low_strength = 0.0              # kick/bass presence now vs groove (0..1)

    # ------------------------------------------------------------------ public
    @property
    def bpm(self) -> float:
        return 60.0 * self.fr / self.period if self.period > 0 else 0.0

    @property
    def confidence(self) -> float:
        if self.period <= 0 or self._count < self.n_hist // 2:
            return 0.0
        tol = 0.025
        if not self._history:
            return 0.0
        agree = 0.0
        for b in self._history:
            r = b / self.bpm
            if abs(r - 1) < tol:
                agree += 1
            elif abs(r - 2) < 2 * tol or abs(r - 0.5) < tol:
                agree += 0.4
        stability = agree / len(self._history)
        sal = min(1.0, max(0.0, (self.salience - 0.06) / 0.25))
        gate = min(1.0, self.rhythm_strength / 0.35)
        return float(sal * stability * gate * (0.6 + 0.4 * self.phase_confidence))

    def push(self, odf: float, low_flux: float, profile: np.ndarray | None = None) -> list[int]:
        """Push one frame. Returns indices of beats that fell in this frame."""
        i = self._count % self.n_hist
        self._odf[i] = odf
        self._low[i] = low_flux
        self._count += 1
        self._since_update += 1
        if self._since_update >= self._update_every and self._count >= int(4 * self.fr):
            self._since_update = 0
            self._update()
        beats = []
        if self.period > 0:
            while self.next_beat <= self._count:
                self.last_beat = self.next_beat
                self.beat_index += 1
                beats.append(self.beat_index)
                if self.bar_position >= 0:
                    self.bar_position = (self.bar_position + 1) % self.bpb
                self.next_beat += self.period
                self._close_beat_interval()
                self._pending_acc.append((self.last_beat, self.beat_index))
        self._measure_accents()
        if profile is not None:
            if self._prof_sum is None:
                self._prof_sum = np.zeros_like(profile)
            self._prof_sum += profile
            self._prof_cnt += 1
        return beats

    # ------------------------------------------------------------------ internals
    def _ordered(self, arr: np.ndarray) -> np.ndarray:
        n = min(self._count, self.n_hist)
        i = self._count % self.n_hist
        if self._count < self.n_hist:
            return arr[:n].copy()
        return np.concatenate([arr[i:], arr[:i]])

    @staticmethod
    def _autocorr(x: np.ndarray) -> np.ndarray | None:
        x = x - np.mean(x)
        n = len(x)
        if n < 16 or np.allclose(x, 0):
            return None
        spec = np.fft.rfft(x, 2 * n)
        ac = np.fft.irfft(spec.real ** 2 + spec.imag ** 2)[:n]
        if ac[0] <= 0:
            return None
        return ac / ac[0] * (n / np.maximum(n - np.arange(n), 1))   # unbiased, normalised

    def estimate_tempo(self, odf: np.ndarray, low: np.ndarray | None = None) -> TempoEstimate:
        ac = self._autocorr(odf)
        if ac is None:
            return TempoEstimate(0.0, 0.0, False)
        n = len(ac)
        # Low-band (kick/bass) periodicity: in dance music the kick defines the
        # beat, so it disambiguates double-time (off-beat hats) and 3:2 errors.
        ac_low = self._autocorr(low) if low is not None else None
        lag_min = int(math.floor(self.fr * 60.0 / self.max_bpm))
        lag_max = int(math.ceil(self.fr * 60.0 / self.min_bpm))
        lags = np.arange(max(2, lag_min), min(lag_max, n // 2) + 1)
        if len(lags) < 3:
            return TempoEstimate(0.0, 0.0, False)
        limit = int(n * 0.75)
        score = np.zeros(len(lags))
        for j, L in enumerate(lags):
            vals = [ac[k * L] for k in (1, 2, 3, 4) if k * L < limit]
            score[j] = np.mean(vals)
            if ac_low is not None:
                score[j] = 0.5 * score[j] + 0.5 * np.mean(
                    [ac_low[k * L] for k in (1, 2, 3, 4) if k * L < limit])
        bpms = 60.0 * self.fr / lags
        prior = np.exp(-0.5 * (np.log2(bpms / self.prior_bpm) / 0.9) ** 2)
        weighted = score * prior
        self._last_lags, self._last_weighted = lags, weighted
        j = int(np.argmax(weighted))
        # parabolic refinement on the raw comb score
        if 0 < j < len(lags) - 1:
            a, b, c = score[j - 1], score[j], score[j + 1]
            den = a - 2 * b + c
            delta = 0.5 * (a - c) / den if abs(den) > 1e-12 else 0.0
            delta = max(-0.5, min(0.5, delta))
        else:
            delta = 0.0
        lag = lags[j] + delta
        # refine using the highest usable multiple of the lag (k*L is k times
        # more precise in frames): local max + parabolic interpolation
        for k in (4, 3, 2):
            c = int(round(k * lag))
            if c + 2 < limit:
                lo_, hi_ = max(1, c - 2), c + 3
                m = lo_ + int(np.argmax(ac[lo_:hi_]))
                if 0 < m < n - 1:
                    a, b, cc = ac[m - 1], ac[m], ac[m + 1]
                    den = a - 2 * b + cc
                    d = 0.5 * (a - cc) / den if abs(den) > 1e-12 else 0.0
                    refined = (m + max(-0.5, min(0.5, d))) / k
                    if abs(refined / lag - 1) < 0.02:
                        lag = refined
                break
        # octave ambiguity: is the half/double lag almost as strong?
        amb = False
        for f in (0.5, 2.0):
            L2 = lag * f
            k = int(round(L2)) - lags[0]
            if 0 <= k < len(lags) and weighted[k] > 0.85 * weighted[j]:
                amb = True
        return TempoEstimate(60.0 * self.fr / lag, float(max(0.0, score[j])), amb)

    def _update(self) -> None:
        odf = self._ordered(self._odf)
        # Rhythmic strength: recent onset activity vs. the level seen while the
        # beat was confident. Autocorrelation is scale-invariant, so without
        # this gate faint noise in a beatless pad section can look periodic.
        recent = float(np.mean(odf[-int(2 * self.fr):]))
        if self._odf_ref <= 0:
            self._odf_ref = recent
        self.rhythm_strength = float(min(1.0, recent / (self._odf_ref + 1e-9)))
        low = self._ordered(self._low)
        low_recent = float(np.mean(low[-int(2 * self.fr):]))
        if self._low_ref <= 0:
            self._low_ref = low_recent
        self.low_strength = float(min(1.0, low_recent / (self._low_ref + 1e-9)))
        est = self.estimate_tempo(odf, low)
        if est.bpm > 0 and est.salience > 0.2 and self.rhythm_strength > 0.5:
            rate = 0.05 if recent > self._odf_ref else 0.01
            self._odf_ref += rate * (recent - self._odf_ref)
            rate = 0.05 if low_recent > self._low_ref else 0.01
            self._low_ref += rate * (low_recent - self._low_ref)
        if self.period > 0 and self.rhythm_strength < 0.3:
            # no rhythm to follow (e.g. beatless breakdown): coast on the last
            # tempo, never adopt a new one from noise
            self.salience *= 0.9
            self._history.append(0.0)
            return
        if est.bpm <= 0:
            self.salience *= 0.8
            return
        self._history.append(est.bpm)
        self.salience = 0.7 * self.salience + 0.3 * est.salience
        self.octave_ambiguous = est.octave_ambiguous
        if self.period <= 0:
            self._adopt(est.bpm)
        else:
            r = est.bpm / self.bpm
            if abs(r - 1) < 0.03:
                # smooth small drift (DJ pitch-fader moves, tracker jitter)
                new_bpm = 0.85 * self.bpm + 0.15 * est.bpm
                self.period = 60.0 * self.fr / new_bpm
                self._candidate_hits = 0
            else:
                if self._candidate and abs(est.bpm / self._candidate - 1) < 0.03:
                    self._candidate_hits += 1
                else:
                    self._candidate, self._candidate_hits = est.bpm, 1
                octave = abs(r - 2) < 0.06 or abs(r - 0.5) < 0.03
                if octave and (self.rhythm_strength < 0.75 or self.low_strength < 0.6
                               or not self._octave_clearly_better(est.bpm)):
                    # Tempo inertia: half/double readings that are merely comparable
                    # (e.g. an off-beat bass line entering a 90 BPM groove) do not
                    # move an established tempo, nor do reduced sections (build-ups with
                    # claps every other beat). Tracks rarely change tempo by 2x.
                    self._candidate_hits = 0
                    self._candidate = 0.0
                    if self._history:
                        self._history[-1] = self.bpm   # same pulse, other metrical level
                # octave jumps need ~6 s of evidence, ~40 s once the tempo is established
                established = self._confident_updates * self._update_every / self.fr > 30.0
                need = (160 if established else 24) if octave else 4
                if self._candidate and self._candidate_hits >= need:
                    self._adopt(self._candidate)
        self._update_phase(odf)
        if self.rhythm_strength > 0.5:
            self._confident_updates += 1     # time spent following real rhythm at this tempo
        if self.confidence < 0.1:
            self._low_confidence_frames += self._update_every
        else:
            self._low_confidence_frames = 0

    def _octave_clearly_better(self, bpm: float) -> bool:
        lags, w = self._last_lags, self._last_weighted
        if lags is None or self.period <= 0:
            return True
        def at(period: float) -> float:
            k = int(round(period)) - int(lags[0])
            return float(w[k]) if 0 <= k < len(w) else 0.0
        cur = at(self.period)
        new = at(60.0 * self.fr / bpm)
        # a weak/negative score at the current tempo (sparse section) is not
        # evidence for the octave: require a genuinely strong new periodicity
        return new > 0.15 and new > 1.3 * max(cur, 0.0)

    def _adopt(self, bpm: float) -> None:
        self._confident_updates = 0
        self.period = 60.0 * self.fr / bpm
        self._candidate, self._candidate_hits = 0.0, 0
        self._acc[:] = 0
        self._acc_n = 0
        self.bar_position = -1
        self.downbeat_confidence = 0.0

    def _update_phase(self, odf: np.ndarray) -> None:
        P = self.period
        n = len(odf)
        if P <= 0 or n < 2 * P:
            return
        sm = np.convolve(odf, np.array([0.25, 0.5, 0.25]), mode="same")
        K = int(min(8, (n - 1) // P))
        weights = 0.9 ** np.arange(K)
        offsets = np.arange(0, int(math.ceil(P)))
        scores = np.zeros(len(offsets))
        idx_end = n - 1
        for j, phi in enumerate(offsets):
            pos = idx_end - phi - np.arange(K) * P
            lo = np.floor(pos).astype(int)
            frac = pos - lo
            valid = lo >= 0
            v = sm[np.clip(lo, 0, n - 1)] * (1 - frac) + sm[np.clip(lo + 1, 0, n - 1)] * frac
            scores[j] = float(np.sum(weights * v * valid))
        j = int(np.argmax(scores))
        s_max, s_mean = scores[j], float(np.mean(scores))
        self.phase_confidence = float(np.clip((s_max - s_mean) / (s_max + 1e-9) * 1.5, 0, 1))
        # beat position in absolute frames (frame counter of last pushed frame = self._count)
        beat_abs = self._count - offsets[j]
        if self.next_beat <= 0:
            self.next_beat = beat_abs + P
            while self.next_beat <= self._count:
                self.next_beat += P
            return
        # phase error relative to the running grid, wrapped to [-P/2, P/2]
        err = (beat_abs - self.next_beat) % P
        if err > P / 2:
            err -= P
        if abs(err) < 0.2 * P:
            self.next_beat += 0.35 * err
            self._phase_miss = 0
        else:
            self._phase_miss += 1
            if self._phase_miss >= 3:
                self.next_beat += err
                self._phase_miss = 0
                self._acc[:] = 0
                self._acc_n = 0
                self.bar_position = -1
        while self.next_beat <= self._count:
            # never schedule into the past (no retroactive beats)
            self.next_beat += P

    def _measure_accents(self) -> None:
        """Low-band onset strength at each beat, measured 3 frames after it."""
        while self._pending_acc and self._pending_acc[0][0] + 3 <= self._count:
            frame, idx = self._pending_acc.popleft()
            back = int(round(self._count - frame))
            n = min(self._count, self.n_hist)
            if back >= n - 3:
                continue
            low = self._ordered(self._low)
            c = len(low) - 1 - back
            self._beat_acc.append((idx, float(np.max(low[max(0, c - 2):c + 3]))))
            if len(self._beat_acc) == self._beat_acc.maxlen and idx % 8 == 0:
                self._check_double_time()

    def _check_double_time(self) -> None:
        """If beats alternate strong/weak in the low band, we are tracking the
        off-beats too (double time): halve the tempo, keep the strong beats.

        Only applied when the halved tempo stays inside the configured range and
        is closer to the tempo prior, so drum & bass at 172 BPM (whose kick/snare
        pattern also alternates) is not halved to 86 with the default prior.
        """
        if self.confidence < 0.4 or self.period <= 0:
            return
        even = [a for i, a in self._beat_acc if i % 2 == 0]
        odd = [a for i, a in self._beat_acc if i % 2 == 1]
        e, o = float(np.median(even)), float(np.median(odd))
        strong, weak = max(e, o), min(e, o)
        if weak <= 0 or strong / weak < 1.8:
            return
        half = self.bpm / 2
        if half < self.min_bpm:
            return
        if abs(math.log2(half / self.prior_bpm)) >= abs(math.log2(self.bpm / self.prior_bpm)):
            return
        strong_parity = 0 if e > o else 1
        self.period *= 2
        # next beat = next strong one
        nb = self.next_beat
        idx = self.beat_index + 1          # index of the beat at next_beat (old grid)
        if idx % 2 != strong_parity:
            nb += self.period / 2
        self.next_beat = nb
        self._history.clear()
        self._candidate, self._candidate_hits = 0.0, 0
        self._beat_acc.clear()
        self._acc[:] = 0
        self._acc_n = 0
        self.bar_position = -1
        self.downbeat_confidence = 0.0
        self.halvings += 1

    def _close_beat_interval(self) -> None:
        """A beat just started: score the boundary at the *previous* beat.

        The interval that just closed began at beat ``beat_index - 1``; the
        profile change between it and the interval before is evidence that
        beat ``beat_index - 1`` starts a bar.
        """
        if self._prof_sum is None or self._prof_cnt == 0:
            return
        mean = self._prof_sum / self._prof_cnt
        self._prof_sum = np.zeros_like(mean)
        self._prof_cnt = 0
        prev, self._prev_beat_prof = self._prev_beat_prof, mean
        if prev is None or self.confidence < 0.3:
            self.downbeat_confidence *= 0.95
            return
        change = float(np.mean(np.abs(mean - prev)))
        pos = (self.beat_index - 1) % self.bpb
        self._acc[pos] = 0.9 * self._acc[pos] + 0.1 * change if self._acc_n >= self.bpb else (
            self._acc[pos] + change / 2)
        self._acc_n += 1
        if self._acc_n >= 8 * self.bpb:
            order = np.sort(self._acc)
            top, second = order[-1], order[-2]
            self.downbeat_confidence = float(np.clip((top - second) / (top + 1e-9) * 3.0, 0, 1))
            best = int(np.argmax(self._acc))
            self.bar_position = (self.beat_index - best) % self.bpb
