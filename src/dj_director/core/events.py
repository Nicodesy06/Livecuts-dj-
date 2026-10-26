"""Typed contracts exchanged between components.

These dataclasses are the only things that cross component boundaries
(audio -> analysis -> director -> OBS). They are immutable snapshots so they
can be handed between threads without locking.
"""

from __future__ import annotations

import collections
import itertools
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


# --------------------------------------------------------------------------- audio
@dataclass(frozen=True)
class AudioStatusEvent:
    t: float
    backend: str
    device_name: str
    sample_rate: int
    channels: int
    running: bool
    level_db: float            # short-term RMS level, dBFS
    peak_db: float
    clipping: bool
    signal_present: bool
    overruns: int              # ring buffer overflows (samples dropped)
    stream_errors: int         # PortAudio status flags / backend errors
    buffer_fill: float         # 0..1
    capture_latency_ms: float  # backend-reported input latency
    error: str = ""
    digital_silence: bool = False   # samples exactly 0 (e.g. Windows mic privacy block)
    device_hint: str = ""           # what kind of device this looks like (see audio.devices)


# --------------------------------------------------------------------------- music
class MusicState(str, Enum):
    NO_SIGNAL = "no_signal"
    UNCERTAIN = "uncertain"
    STABLE_GROOVE = "stable_groove"
    INCREASING = "increasing_activity"
    DECREASING = "decreasing_activity"
    SPARSE = "sparse"
    TRANSITION = "transition_candidate"
    REENTRY = "strong_reentry"

    @property
    def label(self) -> str:
        return {
            "no_signal": "No signal",
            "uncertain": "Uncertain",
            "stable_groove": "Stable groove",
            "increasing_activity": "Increasing activity",
            "decreasing_activity": "Decreasing activity",
            "sparse": "Sparse / reduced rhythm",
            "transition_candidate": "Transition candidate",
            "strong_reentry": "Strong re-entry",
        }[self.value]


@dataclass(frozen=True)
class BeatInfo:
    bpm: float                 # 0 when unknown
    confidence: float          # 0..1 combined salience x stability
    period: float              # seconds, 0 when unknown
    last_beat_t: float         # clock time of last estimated beat (0 if unknown)
    next_beat_t: float         # clock time of next predicted beat (0 if unknown)
    beat_index: int            # running beat counter
    bar_position: int          # 0..beats_per_bar-1, -1 if unknown (heuristic)
    downbeat_confidence: float  # 0..1 (heuristic)
    octave_ambiguous: bool = False

    @property
    def reliable(self) -> bool:
        return self.bpm > 0 and self.confidence > 0

    def next_beat_after(self, t: float) -> float:
        if self.period <= 0 or self.next_beat_t <= 0:
            return 0.0
        nb = self.next_beat_t
        if nb < t:
            n = int((t - nb) / self.period) + 1
            nb += n * self.period
        return nb

    def next_downbeat_after(self, t: float, beats_per_bar: int) -> float:
        """Next beat whose bar position is 0. Returns 0 if unknown."""
        if self.bar_position < 0 or self.period <= 0 or self.next_beat_t <= 0:
            return 0.0
        nb = self.next_beat_t
        pos = (self.bar_position + 1) % beats_per_bar  # position of next_beat_t
        while nb < t or pos != 0:
            nb += self.period
            pos = (pos + 1) % beats_per_bar
            if nb - t > self.period * beats_per_bar * 2:
                return 0.0
        return nb


EMPTY_BEAT = BeatInfo(0.0, 0.0, 0.0, 0.0, 0.0, 0, -1, 0.0)


@dataclass(frozen=True)
class MusicFeatures:
    rms_db: float
    low_db: float
    mid_db: float
    high_db: float
    onset_density: float       # onsets / second (medium window)
    flux: float                # normalised spectral flux (short)
    novelty: float             # spectral-profile novelty 0..1
    energy_trend_db: float     # medium vs long loudness
    low_vs_groove_db: float    # medium low band vs groove reference
    density_ratio: float       # medium vs long onset density
    centroid_hz: float


@dataclass(frozen=True)
class MusicEvent:
    """Discrete, probabilistic musical events (never certainties)."""
    t: float
    kind: str                  # "reentry" | "transition" | "sparse_start" | "eq_cut" | "filter" | "eq_return"
    confidence: float
    detail: str = ""


@dataclass(frozen=True)
class MusicSnapshot:
    t: float                   # clock time this snapshot describes
    state: MusicState
    state_confidence: float
    state_since: float         # clock time the current state began
    previous_state: MusicState
    features: MusicFeatures
    beat: BeatInfo
    events: tuple[MusicEvent, ...] = ()
    analysis_latency_ms: float = 0.0
    warmed_up: bool = False
    mix_gesture: str = ""      # DJ gesture heard in progress, e.g. "eq_cut:low", "filter:high" ("" = none)

    @property
    def state_duration(self) -> float:
        return max(0.0, self.t - self.state_since)


# --------------------------------------------------------------------------- director
class DirectorAction(str, Enum):
    HOLD = "HOLD"
    SWITCH = "SWITCH"
    SCHEDULE = "SCHEDULE"      # switch requested, waiting for a musical boundary
    CANCEL = "CANCEL"          # previously scheduled switch dropped
    OVERRIDE = "OVERRIDE"      # manual/external change respected


class TimingMode(str, Enum):
    IMMEDIATE = "immediate"
    BEAT = "beat"
    DOWNBEAT = "downbeat"
    PHRASE = "phrase"          # predicted 4-bar boundary, anchored to a detected re-entry (heuristic)


_corr = itertools.count(1)


def next_correlation_id() -> int:
    return next(_corr)


@dataclass(frozen=True)
class DirectorDecisionEvent:
    t: float
    action: DirectorAction
    current_camera: Optional[str]
    candidate_camera: Optional[str]
    reason: str
    confidence: float
    time_on_shot: float
    music_state: MusicState
    opportunity: float = 0.0
    timing: Optional[TimingMode] = None
    target_t: float = 0.0
    priority: int = 0
    correlation_id: int = 0
    scores: tuple[tuple[str, float], ...] = ()

    def short(self) -> str:
        if self.action in (DirectorAction.SWITCH, DirectorAction.SCHEDULE):
            return f"{self.action.value} {self.candidate_camera} — {self.reason}"
        return f"{self.action.value} {self.current_camera or '-'} — {self.reason}"


@dataclass(frozen=True)
class SceneSwitchRequested:
    t: float
    scene: str
    correlation_id: int


@dataclass(frozen=True)
class SceneSwitchConfirmed:
    t: float
    scene: str
    correlation_id: int
    round_trip_ms: float


@dataclass(frozen=True)
class SceneSwitchFailed:
    t: float
    scene: str
    correlation_id: int
    error: str


@dataclass(frozen=True)
class ObsStatusEvent:
    t: float
    connected: bool
    state: str                 # "disconnected" | "connecting" | "connected" | "auth_failed" | "error"
    obs_version: str = ""
    ws_version: str = ""
    scene_collection: str = ""
    program_scene: str = ""
    preview_scene: str = ""
    studio_mode: bool = False
    scenes: tuple[str, ...] = ()
    error: str = ""


@dataclass(frozen=True)
class ExternalSceneChange:
    t: float
    scene: str


# --------------------------------------------------------------------------- bus
class EventBus:
    """Tiny synchronous pub/sub plus a bounded history for the UI.

    Handlers run on the publisher's thread and must be cheap (append to a
    queue, set a flag). The bus never blocks and never grows unbounded.
    """

    def __init__(self, history: int = 500) -> None:
        self._subs: list[tuple[type, Callable[[object], None]]] = []
        self._lock = threading.Lock()
        self.history: collections.deque = collections.deque(maxlen=history)

    def subscribe(self, event_type: type, handler: Callable[[object], None]) -> None:
        with self._lock:
            self._subs.append((event_type, handler))

    def publish(self, event: object) -> None:
        self.history.append(event)
        with self._lock:
            subs = list(self._subs)
        for et, h in subs:
            if isinstance(event, et):
                try:
                    h(event)
                except Exception:  # a broken subscriber must never break the pipeline
                    import logging
                    logging.getLogger(__name__).exception("event handler failed")
