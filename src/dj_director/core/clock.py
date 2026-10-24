"""Clock abstraction.

Every time value inside the analysis and director layers is expressed in
seconds on a single *monotonic* timeline. Live sessions use
:class:`MonotonicClock`; simulation/replay uses :class:`SimClock`, which is
advanced explicitly so that runs are fully deterministic.
"""

from __future__ import annotations

import threading
import time


class Clock:
    def now(self) -> float:  # pragma: no cover - interface
        raise NotImplementedError


class MonotonicClock(Clock):
    def now(self) -> float:
        return time.monotonic()


class SimClock(Clock):
    def __init__(self, start: float = 0.0) -> None:
        self._t = float(start)
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self._t

    def set(self, t: float) -> None:
        with self._lock:
            if t < self._t:
                raise ValueError("SimClock cannot go backwards")
            self._t = float(t)

    def advance(self, dt: float) -> None:
        self.set(self._t + dt)
