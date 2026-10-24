"""Visual analysis interface (implementation planned for v0.2).

The director already consumes a per-camera ``VisualFit`` term; in v0.1 it is
always 0 because no video is analysed. A v0.2 analyser (OpenCV/MediaPipe on
low-resolution frames obtained through a supported OBS mechanism such as
``GetSourceScreenshot`` or a virtual camera) only has to implement
:class:`VisualAnalyzer`; nothing in the engine changes.

Contract: ``score`` returns a value roughly in [-1, 1] (negative = shot
currently not usable, e.g. black frame or DJ out of frame) or ``None`` when
there is no information. It must be cheap (cached), never block, and never
claim more than it measures (hand *movement* is not "EQ adjustment").
"""

from __future__ import annotations

from typing import Optional, Protocol


class VisualAnalyzer(Protocol):
    def score(self, camera_name: str) -> Optional[float]: ...

    def available(self, camera_name: str) -> bool: ...


class NullVisualAnalyzer:
    """v0.1 default: no visual information."""

    def score(self, camera_name: str) -> Optional[float]:
        return None

    def available(self, camera_name: str) -> bool:
        return True
