"""Frame-level spectral features, computed causally hop by hop.

Frame rate is ~86 frames/s regardless of sample rate (hop = sr/86.13, FFT size
the next power of two >= 4*hop), so beat-tracking parameters expressed in
seconds stay valid at 44.1, 48 or 96 kHz without resampling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

TARGET_FRAME_RATE = 86.13
BANDS_HZ = (20.0, 150.0, 2000.0, 10000.0)        # low | mid | high
PROFILE_EDGES_HZ = (20, 60, 120, 250, 500, 1000, 2000, 4000, 8000, 14000)


@dataclass(frozen=True)
class Frame:
    index: int          # frame counter
    rms: float          # linear RMS of the hop
    low: float          # band power (linear)
    mid: float
    high: float
    flux: float         # log-spectral flux, full band (onset strength)
    low_flux: float     # log-spectral flux, < 150 Hz (kick / bass onsets)
    centroid: float     # Hz
    profile: np.ndarray  # log band powers (len = len(PROFILE_EDGES_HZ)-1)


class FeatureExtractor:
    def __init__(self, sample_rate: int) -> None:
        self.sr = sample_rate
        self.hop = max(64, int(round(sample_rate / TARGET_FRAME_RATE)))
        self.n_fft = 1 << int(math.ceil(math.log2(self.hop * 4)))
        self.frame_rate = sample_rate / self.hop
        self.window = np.hanning(self.n_fft).astype(np.float32)
        self._win_norm = float(np.sum(self.window ** 2))
        freqs = np.fft.rfftfreq(self.n_fft, 1.0 / sample_rate)
        self.freqs = freqs

        def mask(lo, hi):
            return (freqs >= lo) & (freqs < hi)

        self._low = mask(BANDS_HZ[0], BANDS_HZ[1])
        self._mid = mask(BANDS_HZ[1], BANDS_HZ[2])
        self._high = mask(BANDS_HZ[2], BANDS_HZ[3])
        self._flux_band = mask(20, 11000)
        self._prof = [mask(a, b) for a, b in zip(PROFILE_EDGES_HZ[:-1], PROFILE_EDGES_HZ[1:])]
        self._buf = np.zeros(self.n_fft, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)
        self._prev_log = None
        self.frames = 0

    def push(self, samples: np.ndarray) -> list[Frame]:
        """Feed mono samples, return the frames completed by them."""
        if len(self._pending):
            samples = np.concatenate([self._pending, samples])
        out = []
        pos = 0
        h = self.hop
        while pos + h <= len(samples):
            hop_samples = samples[pos:pos + h]
            self._buf[:-h] = self._buf[h:]
            self._buf[-h:] = hop_samples
            out.append(self._analyse(hop_samples))
            pos += h
        self._pending = samples[pos:].copy()
        return out

    def _analyse(self, hop_samples: np.ndarray) -> Frame:
        spec = np.fft.rfft(self._buf * self.window)
        power = (spec.real ** 2 + spec.imag ** 2) / self._win_norm
        mag_log = np.log1p(100.0 * np.sqrt(power))
        if self._prev_log is None:
            diff = np.zeros_like(mag_log)
        else:
            diff = np.maximum(mag_log - self._prev_log, 0.0)
        self._prev_log = mag_log
        flux = float(np.mean(diff[self._flux_band]))
        low_flux = float(np.mean(diff[self._low]))
        total = float(np.sum(power)) + 1e-12
        centroid = float(np.sum(self.freqs * power) / total)
        profile = np.array([math.log10(float(np.sum(power[m])) + 1e-10) for m in self._prof])
        rms = float(np.sqrt(np.mean(hop_samples.astype(np.float64) ** 2)))
        self.frames += 1
        return Frame(self.frames, rms, float(np.sum(power[self._low])),
                     float(np.sum(power[self._mid])), float(np.sum(power[self._high])),
                     flux, low_flux, centroid, profile)


class Ema:
    """Exponential moving average with time constant in seconds."""

    def __init__(self, tau: float, frame_rate: float, init: float | None = None) -> None:
        self.alpha = 1.0 - math.exp(-1.0 / (tau * frame_rate))
        self.value = init
        self.n = 0

    def update(self, x: float) -> float:
        self.n += 1
        if self.value is None:
            self.value = x
        else:
            self.value += self.alpha * (x - self.value)
        return self.value


def db(power: float) -> float:
    return 10.0 * math.log10(power) if power > 1e-12 else -120.0


class Boxcar:
    """Sliding-window mean (symmetric response: clears fully after ``window`` s)."""

    def __init__(self, window: float, frame_rate: float) -> None:
        self.n_max = max(1, int(window * frame_rate))
        self._buf = np.zeros(self.n_max)
        self._sum = 0.0
        self.n = 0
        self.value = None

    def update(self, x: float) -> float:
        i = self.n % self.n_max
        self._sum += x - self._buf[i]
        self._buf[i] = x
        self.n += 1
        self.value = max(0.0, self._sum) / min(self.n, self.n_max)
        return self.value
