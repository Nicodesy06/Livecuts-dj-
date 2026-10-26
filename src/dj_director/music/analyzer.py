"""Analysis pipeline: samples -> features -> beat tracker -> state -> snapshot.

:class:`AnalysisPipeline` is pure (no threads, no clock): it is driven with
sample blocks and a ``time_of_sample`` mapping. This lets the very same code
run live (mapping from the audio callback time anchor) and in offline
simulation (mapping = index / sample_rate).

:class:`AnalysisWorker` is the live wrapper: a thread that drains the ring
buffer every few milliseconds and publishes the latest snapshot.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable, Optional

import numpy as np

from dj_director.config.schema import AnalysisConfig
from dj_director.core.events import BeatInfo, EMPTY_BEAT, MusicEvent, MusicSnapshot, MusicState
from dj_director.music.beat import BeatTracker
from dj_director.music.features import FeatureExtractor
from dj_director.music.mixing import MixGestureDetector
from dj_director.music.state import MusicStateEstimator

log = logging.getLogger(__name__)


class AnalysisPipeline:
    def __init__(self, sample_rate: int, cfg: AnalysisConfig,
                 time_of_sample: Callable[[float], float]) -> None:
        self.sr = sample_rate
        self.cfg = cfg
        self.time_of_sample = time_of_sample
        self.fx = FeatureExtractor(sample_rate)
        self.beats = BeatTracker(self.fx.frame_rate, cfg.min_bpm, cfg.max_bpm, cfg.prior_bpm,
                                 cfg.beats_per_bar)
        self.state = MusicStateEstimator(self.fx.frame_rate, cfg.silence_db, cfg.sensitivity)
        self.mix = MixGestureDetector(self.fx.frame_rate, cfg.silence_db)
        self._base_sample = 0          # absolute sample index of feature frame 0 start
        self._expected_next = None     # next expected absolute sample index
        self.gaps = 0
        self.last_t = 0.0
        self._snapshot: Optional[MusicSnapshot] = None
        self.beat_times: deque[float] = deque(maxlen=64)   # emitted beat clock times

    # frame F (1-based) "happens" roughly at the centre of its analysis window
    def frame_time(self, frame: float) -> float:
        return self.time_of_sample(self._base_sample + frame * self.fx.hop - self.fx.n_fft / 2)

    def push(self, samples: np.ndarray, first_index: int) -> None:
        if self._expected_next is not None and first_index != self._expected_next:
            gap = first_index - self._expected_next
            if gap > 0:
                # samples were lost (ring overrun): shift the frame->sample mapping
                self._base_sample += gap
                self.gaps += 1
        self._expected_next = first_index + len(samples)
        for f in self.fx.push(samples):
            t = self.frame_time(f.index)
            self.last_t = t
            new_beats = self.beats.push(f.flux + 0.5 * f.low_flux, f.low_flux, f.profile)
            for _ in new_beats:
                self.beat_times.append(self.frame_time(self.beats.last_beat))
            self.state.update(f, t, self.beats.confidence)
            self.mix.update(t, f.profile, f.rms, self.beats.confidence)
            if self.mix.events:                 # one event stream for the director
                self.state.events.extend(self.mix.drain_events())
        self._snapshot = None

    def beat_info(self) -> BeatInfo:
        b = self.beats
        if b.period <= 0:
            return EMPTY_BEAT
        period_s = b.period / self.fx.frame_rate
        return BeatInfo(bpm=b.bpm, confidence=b.confidence, period=period_s,
                        last_beat_t=self.frame_time(b.last_beat) if b.last_beat else 0.0,
                        next_beat_t=self.frame_time(b.next_beat),
                        beat_index=b.beat_index, bar_position=b.bar_position,
                        downbeat_confidence=b.downbeat_confidence,
                        octave_ambiguous=b.octave_ambiguous)

    def snapshot(self, events: tuple[MusicEvent, ...] = (), latency_ms: float = 0.0) -> MusicSnapshot:
        s = self.state
        warmed = s.state not in (MusicState.NO_SIGNAL, MusicState.UNCERTAIN) or (
            s._signal_since is not None and self.last_t - s._signal_since >= s.th.warmup_s)
        return MusicSnapshot(t=self.last_t, state=s.state, state_confidence=s.confidence,
                             state_since=s.state_since, previous_state=s.previous,
                             features=s.features, beat=self.beat_info(), events=events,
                             analysis_latency_ms=latency_ms, warmed_up=warmed, mix_gesture=self.mix.gesture)


class AnalysisWorker:
    """Live analysis thread. Never touches OBS or the GUI."""

    def __init__(self, audio_manager, cfg: AnalysisConfig) -> None:
        self.audio = audio_manager
        self.cfg = cfg
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest: Optional[MusicSnapshot] = None
        self._events: deque[MusicEvent] = deque(maxlen=64)
        self._generation = -1
        self.pipeline: Optional[AnalysisPipeline] = None
        self.processing_ms = 0.0       # per-iteration DSP cost (EMA)

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="analysis", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def latest(self) -> Optional[MusicSnapshot]:
        with self._lock:
            return self._latest

    def drain_events(self) -> list[MusicEvent]:
        with self._lock:
            ev = list(self._events)
            self._events.clear()
            return ev

    def _run(self) -> None:
        while not self._stop.wait(0.01):
            try:
                self._step()
            except Exception:
                log.exception("analysis step failed; resetting pipeline")
                self.pipeline = None
                time.sleep(0.2)

    def _step(self) -> None:
        backend, ring = self.audio.backend, self.audio.ring
        if backend is None or ring is None or not backend.sample_rate or backend.anchor is None:
            if self._latest is not None:
                with self._lock:
                    self._latest = None
            return
        if self.audio.generation != self._generation or self.pipeline is None:
            self._generation = self.audio.generation
            self.pipeline = AnalysisPipeline(backend.sample_rate, self.cfg, backend.anchor.time_of)
            ring.read(ring.available())   # start fresh, do not analyse stale audio
        # bounded work per iteration: at most 0.5 s of audio
        samples, first = ring.read(min(ring.available(), backend.sample_rate // 2))
        if len(samples) == 0:
            return
        t0 = time.perf_counter()
        self.pipeline.push(samples, first)
        dt = (time.perf_counter() - t0) * 1000
        self.processing_ms = 0.9 * self.processing_ms + 0.1 * dt
        events = tuple(self.pipeline.state.drain_events())
        last_sample_t = backend.anchor.time_of(first + len(samples))
        latency = (time.monotonic() - last_sample_t) * 1000 + (
            self.pipeline.fx.n_fft / 2 / backend.sample_rate * 1000)
        snap = self.pipeline.snapshot(events, latency)
        with self._lock:
            self._latest = snap
            self._events.extend(events)
