"""Visual analysis interface (implementation planned for v0.2).

The director already consumes a per-camera ``VisualFit`` term; in v0.1 it is
always 0 because no video is analysed. A v0.2 analyser (OpenCV/MediaPipe on
low-resolution frames obtained through a supported OBS mechanism such as
``GetSourceScreenshot`` or a virtual camera) only has to implement
:class:`VisualAnalyzer`; nothing in the engine changes.

Contract:
* ``score`` returns a value roughly in [-1, 1] (negative = shot currently not
  usable, e.g. black frame or DJ out of frame) or ``None`` when there is no
  information.
* ``activity`` returns the current amount of visual activity in [0, 1] (e.g.
  hand motion energy in the mixer close-up) or ``None``. The engine never uses
  it as "presence": it tracks a per-camera baseline and only a clear rise above
  it (novelty) creates an opportunity. Continuous activity habituates to zero,
  so hands resting on the mixer can neither re-trigger nor extend a Detail shot.
Both must be cheap (cached), never block, and never claim more than they
measure (hand *movement* is not "EQ adjustment").
"""

from __future__ import annotations

from typing import Optional, Protocol


class VisualAnalyzer(Protocol):
    def score(self, camera_name: str) -> Optional[float]: ...

    def available(self, camera_name: str) -> bool: ...

    def activity(self, camera_name: str) -> Optional[float]: ...


class NullVisualAnalyzer:
    """v0.1 default: no visual information."""

    def score(self, camera_name: str) -> Optional[float]:
        return None

    def available(self, camera_name: str) -> bool:
        return True

    def activity(self, camera_name: str) -> Optional[float]:
        return None
