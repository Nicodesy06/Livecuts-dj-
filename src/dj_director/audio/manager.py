"""AudioInputManager: owns the capture backend, ring buffer and watchdog.

Responsibilities:
* build the right backend from :class:`AudioConfig`;
* expose a cheap, thread-safe :class:`AudioStatusEvent` for the UI;
* detect stalled streams (device unplugged, driver reset) and retry opening
  the configured device *by name* with backoff (indices change on hotplug).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Optional

import numpy as np

from dj_director.audio.backends import (AudioBackend, DeviceInfo, FileBackend, LoopbackBackend,
                                        SoundDeviceBackend, find_device, list_loopback_devices)
from dj_director.audio.ringbuffer import AudioRingBuffer
from dj_director.config.schema import AudioConfig
from dj_director.core.events import AudioStatusEvent

log = logging.getLogger(__name__)

RING_SECONDS = 4.0          # hard upper bound on buffered (unanalysed) audio
STALL_TIMEOUT = 1.5         # no callback for this long => stream considered dead
CLIP_LEVEL = 0.999


def _db(x: float) -> float:
    return 20.0 * math.log10(x) if x > 1e-9 else -120.0


class AudioInputManager:
    def __init__(self, cfg: AudioConfig, silence_db: float = -55.0) -> None:
        self.cfg = cfg
        self.silence_db = silence_db
        self.backend: Optional[AudioBackend] = None
        self.ring: Optional[AudioRingBuffer] = None
        self._want_running = False
        self._lock = threading.RLock()
        self._watchdog: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._last_error = ""
        self._restarts = 0
        self._logged_error = ""
        self._clip_until = 0.0
        self.generation = 0         # bumps whenever a new stream (new sample index space) starts

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        with self._lock:
            self._want_running = True
            self._stop_evt.clear()
            self._open()
            if self._watchdog is None or not self._watchdog.is_alive():
                self._watchdog = threading.Thread(target=self._watch, name="audio-watchdog",
                                                  daemon=True)
                self._watchdog.start()

    def stop(self) -> None:
        with self._lock:
            self._want_running = False
            self._stop_evt.set()
            self._close()
        if self._watchdog is not None:
            self._watchdog.join(timeout=3)
            self._watchdog = None

    def reconfigure(self, cfg: AudioConfig) -> None:
        running = self._want_running
        self.stop()
        self.cfg = cfg
        if running:
            self.start()

    def _build(self, ring: AudioRingBuffer) -> AudioBackend:
        c = self.cfg
        if c.backend == "file":
            if not c.file_path:
                raise RuntimeError("no audio file selected")
            return FileBackend(ring, c.channels, c.file_path, loop=c.file_loop)
        if c.backend == "loopback":
            devs = list_loopback_devices()
            if not devs:
                raise RuntimeError("WASAPI loopback unavailable (Windows + PyAudioWPatch required)")
            dev = next((d for d in devs if d.name == c.device_name), devs[0])
            return LoopbackBackend(ring, c.channels, dev, c.block_size)
        dev: Optional[DeviceInfo] = find_device(c.device_name, c.host_api) if c.device_name else None
        if dev is None:
            raise RuntimeError(f"audio input device not found: {c.device_name or '(none selected)'}")
        return SoundDeviceBackend(ring, c.channels, dev, c.sample_rate, c.block_size)

    def configured(self) -> bool:
        c = self.cfg
        return bool(c.file_path) if c.backend == "file" else (c.backend == "loopback" or bool(c.device_name))

    def _open(self) -> None:
        self._close()
        if not self.configured():
            self._last_error = "no audio source selected"
            return
        try:
            # Ring size depends on the sample rate, which we only know after
            # opening; 192 kHz worst case keeps the buffer bounded either way.
            ring = AudioRingBuffer(int(192000 * RING_SECONDS))
            backend = self._build(ring)
            backend.start()
            self.ring, self.backend = ring, backend
            self.generation += 1
            self._last_error = ""
            log.info("audio started: %s (%s Hz, %s ch)", backend.device_name,
                     backend.sample_rate, backend.device_channels)
        except Exception as e:
            self._last_error = str(e)
            if self._last_error != self._logged_error:      # don't flood the log while retrying
                log.warning("audio open failed: %s (will keep retrying)", e)
                self._logged_error = self._last_error

    def _close(self) -> None:
        b, self.backend = self.backend, None
        if b is not None:
            try:
                b.stop()
            except Exception as e:
                log.debug("backend stop: %s", e)

    def _watch(self) -> None:
        backoff = 1.0
        while not self._stop_evt.wait(0.5):
            with self._lock:
                if not self._want_running:
                    return
                if not self.configured():
                    continue
                b = self.backend
                dead = b is None or not b.running or (
                    b.anchor is not None and b.anchor.last_callback_t > 0
                    and time.monotonic() - b.anchor.last_callback_t > STALL_TIMEOUT)
                if isinstance(b, FileBackend) and b.finished and not b.error:
                    dead = False    # a non-looping file that ended is not an error
            if dead:
                if b is not None and not self._last_error:
                    self._last_error = "audio stream stalled (device lost?)"
                    log.warning(self._last_error)
                if self._stop_evt.wait(backoff):
                    return
                with self._lock:
                    if self._want_running:
                        self._restarts += 1
                        self._open()
                        backoff = 1.0 if self.backend else min(backoff * 2, 8.0)

    # ------------------------------------------------------------------ status
    @property
    def sample_rate(self) -> int:
        b = self.backend
        return b.sample_rate if b else 0

    def status(self) -> AudioStatusEvent:
        b, ring = self.backend, self.ring
        now = time.monotonic()
        if b is None or ring is None or not b.sample_rate:
            return AudioStatusEvent(now, self.cfg.backend, self.cfg.device_name or self.cfg.file_path,
                                    0, 0, False, -120.0, -120.0, False, False, 0, 0, 0.0, 0.0,
                                    self._last_error)
        recent = ring.peek_latest(int(b.sample_rate * 0.3))
        rms = float(np.sqrt(np.mean(recent.astype(np.float64) ** 2))) if len(recent) else 0.0
        peak = b.read_peak()
        if peak >= CLIP_LEVEL:
            self._clip_until = now + 2.0
        level = _db(rms)
        return AudioStatusEvent(
            t=now, backend=b.name, device_name=b.device_name, sample_rate=b.sample_rate,
            channels=b.device_channels, running=b.running, level_db=level, peak_db=_db(peak),
            clipping=now < self._clip_until, signal_present=level > self.silence_db,
            overruns=ring.overrun_frames, stream_errors=b.status_errors,
            buffer_fill=ring.fill_ratio() * ring.capacity / max(1, b.sample_rate * RING_SECONDS),
            capture_latency_ms=b.input_latency * 1000.0, error=self._last_error or b.error)
