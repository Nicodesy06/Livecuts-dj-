"""Visual analysis interface.

The director consumes a per-camera ``VisualFit`` term, an activity value and
(0.2) a quality veto. The implementation is
:class:`dj_director.visual.vision.VisionService`: small OBS stills
(``GetSourceScreenshot``, read-only) analysed by the fast eye, plus an optional
vision-language model (slow eye). :class:`NullVisualAnalyzer` keeps the engine
usable without any visual information (tests, offline replay).

Contract:
* ``score`` returns a value roughly in [-1, 1] (negative = shot currently not
  usable, e.g. black frame or DJ out of frame) or ``None`` when there is no
  information.
* ``activity`` returns the current amount of visual activity in [0, 1] (e.g.
  hand motion energy in the mixer close-up) or ``None``. The engine never uses
  it as "presence": it tracks a per-camera baseline and only a clear rise above
  it (novelty) creates an opportunity. Continuous activity habituates to zero,
  so hands resting on the mixer can neither re-trigger nor extend a Detail shot.
* ``issue`` returns "" or why the camera is unairable right now (black,
  frozen, blurred / blocked, or judged unusable by the slow eye): a veto.
* ``reveal_hint`` returns the scene the slow eye suggests for the next drop.
* ``quality`` returns HEALTHY / UNKNOWN / FAILED; missing analysis is UNKNOWN,
  never assumed good.
* ``editor_hint`` (0.5, optional) returns the slow eye's editorial advice - the
  shot to cut to next, or to stay on the shot on air - with its reason and a
  weight. It is advice: the engine adds it to its ranking and may let an insert
  run to the end of its preferred window, never beyond, and it never times a cut.
All must be cheap (cached), never block, and never claim more than they
measure (hand *movement* is not "EQ adjustment").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass(frozen=True)
class EditorHint:
    next_pick: Optional[str] = None      # scene the AI would cut to next
    hold: Optional[str] = None           # on-air scene the AI wants to stay on a little longer
    reason: str = ""
    weight: float = 1.0                  # user setting (Vision page), 0 = advice ignored


class VisualAnalyzer(Protocol):
    def score(self, camera_name: str) -> Optional[float]: ...

    def available(self, camera_name: str) -> bool: ...

    def activity(self, camera_name: str) -> Optional[float]: ...

    def issue(self, camera_name: str) -> str: ...

    def reveal_hint(self) -> Optional[str]: ...

    def quality(self, camera_name: str) -> str: ...

    def editor_hint(self) -> Optional[EditorHint]: ...


class NullVisualAnalyzer:
    """No visual information (offline replay, tests)."""

    def score(self, camera_name: str) -> Optional[float]:
        return None

    def available(self, camera_name: str) -> bool:
        return True

    def activity(self, camera_name: str) -> Optional[float]:
        return None

    def issue(self, camera_name: str) -> str:
        return ""

    def reveal_hint(self) -> Optional[str]:
        return None

    def quality(self, camera_name: str) -> str:
        return "UNKNOWN"

    def editor_hint(self) -> Optional[EditorHint]:
        return None
