"""How a cut looks (0.6): plain cut, light zoom, or now and then the user's own OBS transition.

Deterministic and independent of the engine's choice of *what* and *when*:

* **special** (the custom transition, e.g. a stinger) about once every
  ``special_every`` cuts. From 60 % of that count on it waits for a moment that
  deserves it - a drop reveal, a re-entry, a phrase start - and takes the next
  cut anyway once the count is reached. Never on a DJ-gesture cut (those
  must be snappy) or an emergency exit.
  It is chosen only when the cut is scheduled far enough ahead for the
  transition's change point to land on the beat (the cut is then sent that
  much earlier); after 1.5 x the count it is allowed even if slightly late.
* **zoom** on the other cuts when enabled, if the cut is known in advance (the
  incoming camera must be set up off air) and is not an emergency exit.
* **cut** otherwise.
"""

from __future__ import annotations

from typing import Optional

from dj_director.config.schema import TransitionsConfig
from dj_director.core.events import DirectorDecisionEvent, TimingMode

CUT, ZOOM, SPECIAL = "cut", "zoom", "special"
ZOOM_MIN_WAIT = 0.12          # s: time to set the incoming camera up off air


def is_emergency(reason: str) -> bool:
    return "unusable" in reason or "unavailable" in reason or "hard cap" in reason


def is_gesture(reason: str) -> bool:
    return reason.startswith(("entry: DJ ", "exit: DJ "))


def is_moment(d: DirectorDecisionEvent) -> bool:
    r = d.reason
    return "drop reveal" in r or "reentry" in r or d.timing == TimingMode.PHRASE


class CutStyler:
    def __init__(self, cfg: TransitionsConfig) -> None:
        self.cfg = cfg
        self.since_special = 0             # cuts made since the last custom transition

    def plan(self, d: DirectorDecisionEvent, wait_s: float, special_lead: Optional[float]) -> str:
        """Style of the cut ``d`` that will happen in ``wait_s`` s."""
        c = self.cfg
        if is_emergency(d.reason):
            return CUT
        if c.special and c.special_name and special_lead is not None and not is_gesture(d.reason):
            n = max(3, int(c.special_every))
            count = self.since_special + 1
            on_time = wait_s >= special_lead + 0.03
            if ((count >= n and on_time) or count >= int(n * 1.5)
                    or (c.special_on_moments and count >= int(n * 0.6) and on_time and is_moment(d))):
                return SPECIAL
        if c.zoom and wait_s >= ZOOM_MIN_WAIT:
            return ZOOM
        return CUT

    def on_cut(self, style: str) -> None:
        self.since_special = 0 if style == SPECIAL else self.since_special + 1
