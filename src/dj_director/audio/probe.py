"""Automatic source detection: briefly listen to every candidate endpoint.

Opens each input device (and each WASAPI loopback endpoint when available) for
about a second, measures level and rhythmic activity, and ranks the results:
endpoints carrying music beat silent ones, and line interfaces / DJ hardware
beat room microphones (which also hear the music, but through the air).

Runs on a worker thread; each device is opened in shared mode, so it does not
disturb OBS or the DJ software using the same device.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from dj_director.audio.backends import DeviceInfo, list_input_devices, list_loopback_devices
from dj_director.audio.devices import BUILTIN, classify

log = logging.getLogger(__name__)
SIGNAL_DB = -50.0


@dataclass(frozen=True)
class ProbeResult:
    device: DeviceInfo
    level_db: float          # RMS over the probe window
    peak_db: float
    activity: float          # 0..1 onset/rhythm activity (silence/hum = 0)
    error: str = ""

    @property
    def has_signal(self) -> bool:
        return not self.error and self.level_db > SIGNAL_DB

    def score(self) -> float:
        if self.error:
            return -1e9
        c = classify(self.device.name)
        s = c.preference
        if self.has_signal:
            s += 100 + 20 * self.activity + min(20.0, (self.level_db - SIGNAL_DB) / 2)
            if c.kind == BUILTIN:
                s -= 60          # hears music, but through the air: last resort
        return s


def _db(x: float) -> float:
    return 20 * math.log10(x) if x > 1e-9 else -120.0


def analyse_block(x: np.ndarray, sr: int) -> tuple[float, float, float]:
    """(rms dB, peak dB, activity 0..1) of a mono block."""
    if len(x) == 0:
        return -120.0, -120.0, 0.0
    x = x.astype(np.float64)
    rms, peak = float(np.sqrt(np.mean(x ** 2))), float(np.max(np.abs(x)))
    hop = max(1, sr // 100)
    n = len(x) // hop
    if n < 8 or rms < 1e-6:
        return _db(rms), _db(peak), 0.0
    env = np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1))
    flux = np.maximum(np.diff(np.log(env + 1e-6)), 0)
    # transient-rich (music) -> high mean positive log-envelope change; hum/noise -> ~0
    activity = float(np.clip(np.mean(flux) / 0.08, 0, 1))
    return _db(rms), _db(peak), activity


def _capture_input(dev: DeviceInfo, seconds: float) -> tuple[np.ndarray, int]:
    import sounddevice as sd
    sr = int(dev.default_sample_rate or 48000)
    ch = max(1, min(dev.max_input_channels, 8))
    data = sd.rec(int(seconds * sr), samplerate=sr, channels=ch, dtype="float32", device=dev.index,
                  blocking=True)
    return data.mean(axis=1), sr


def _capture_loopback(dev: DeviceInfo, seconds: float) -> tuple[np.ndarray, int]:
    import pyaudiowpatch as pyaudio
    sr = int(dev.default_sample_rate)
    ch = max(1, dev.max_input_channels)
    p = pyaudio.PyAudio()
    chunks = []
    try:
        s = p.open(format=pyaudio.paFloat32, channels=ch, rate=sr, input=True,
                   input_device_index=dev.index, frames_per_buffer=1024)
        t_end = time.monotonic() + seconds
        while time.monotonic() < t_end:
            # loopback delivers nothing while Windows plays silence: don't block forever
            avail = s.get_read_available()
            if avail:
                chunks.append(np.frombuffer(s.read(avail, exception_on_overflow=False), dtype=np.float32))
            else:
                time.sleep(0.02)
        s.stop_stream()
        s.close()
    finally:
        p.terminate()
    if not chunks:
        return np.zeros(int(seconds * sr), dtype=np.float32), sr
    return np.concatenate(chunks).reshape(-1, ch).mean(axis=1), sr


def probe_devices(seconds: float = 1.2, include_loopback: bool = True,
                  progress: Optional[Callable[[float, str], None]] = None,
                  capture: Optional[Callable[[DeviceInfo, float], tuple[np.ndarray, int]]] = None,
                  devices: Optional[list[DeviceInfo]] = None) -> list[ProbeResult]:
    """Probe candidate endpoints and return them best-first."""
    if devices is None:
        devices = _dedupe(list_input_devices())
        if include_loopback:
            devices += list_loopback_devices()
    out = []
    for i, d in enumerate(devices):
        if progress:
            progress(i / max(1, len(devices)), d.name)
        try:
            fn = capture or (_capture_loopback if d.kind == "loopback" else _capture_input)
            x, sr = fn(d, seconds)
            lvl, pk, act = analyse_block(x, sr)
            out.append(ProbeResult(d, lvl, pk, act))
        except Exception as e:
            out.append(ProbeResult(d, -120.0, -120.0, 0.0, str(e)))
    if progress:
        progress(1.0, "")
    return rank(out)


def rank(results: list[ProbeResult]) -> list[ProbeResult]:
    return sorted(results, key=lambda r: -r.score())


def _dedupe(devs: list[DeviceInfo]) -> list[DeviceInfo]:
    """The same hardware appears once per Windows host API; probe the best API only."""
    order = {"Windows WASAPI": 0, "MME": 1, "Windows DirectSound": 2, "Windows WDM-KS": 3}
    best: dict[str, DeviceInfo] = {}
    for d in devs:
        key = d.name[:31].strip().lower()     # MME truncates names to 31 characters
        cur = best.get(key)
        if cur is None or order.get(d.host_api, 9) < order.get(cur.host_api, 9):
            best[key] = d
    return list(best.values())


def recommend(devices: list[DeviceInfo]) -> Optional[DeviceInfo]:
    """Best guess without listening (used to preselect in the wizard)."""
    if not devices:
        return None
    order = {"Windows WASAPI": 0, "MME": 1, "Windows DirectSound": 2}
    return max(devices, key=lambda d: (classify(d.name).preference, -order.get(d.host_api, 5)))
