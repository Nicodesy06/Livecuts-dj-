"""Directorial shot planner: the four questions behind every cut, kept separate.

1. **WHY**   - the reason to leave the current shot (musical event, DJ gesture,
               visual novelty, editorial dwell, cap, veto). Decided by the engine's
               entry / exit logic, recorded here as text.
2. **WHAT**  - which shot serves that moment: :meth:`ShotPlanner.what` ranks the
               candidates with an explainable score.
3. **WHEN**  - musical timing (phrase > downbeat > beat, latency compensated):
               the engine's timing, recorded here.
4. **HOW LONG** - the planned length of the new shot inside its preferred window
               (:meth:`how_long_insert`, :meth:`how_long_master`).

WHAT combines, with every term visible in ``parts``:

* ``coverage``    soft editorial budget (time distribution of the classes)
* ``musical``     how the camera's role tells the current musical state
* ``visual``      visual novelty / slow-eye interest / beat coherence
* ``preference``  user weight and priority
* ``freshness``   time since the camera was last used
* ``sequence``    alternate detail / side rather than repeating a class
* ``continuity``  cut grammar from the current shot (director.continuity)
* ``context``     DJ-set grammar: breakdown, build-up, drop, active mixing
* ``quality``     source health (HEALTHY / UNKNOWN; FAILED never reaches here)

No randomness: the same inputs give the same ranking (ties broken by name).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from dj_director.config.schema import COVER_MASTER, CameraRole, ShotSize, Subject
from dj_director.core.events import MusicState
from dj_director.director.continuity import Continuity, assess

if TYPE_CHECKING:                                  # pragma: no cover
    from dj_director.director.cameras import Camera
    from dj_director.director.engine import DirectorEngine, ShotSpec

R = CameraRole
GESTURE_BONUS = 0.9            # mixer shot relevance while the DJ works the mixer (x corroboration)


@dataclass
class Candidate:
    camera: "Camera"
    score: float
    parts: dict
    continuity: Continuity = field(default_factory=Continuity)


@dataclass(frozen=True)
class ShotPlan:
    """Explanation of one planned cut (shown in diagnostics, written to the decision log)."""
    why: str
    what: str
    when: str
    how_long: str

    def as_tuple(self) -> tuple:
        return (("why", self.why), ("what", self.what), ("when", self.when), ("how_long", self.how_long))


def describe_shot(cam: "Camera") -> str:
    bits = [cam.role.value]
    if cam.shot_size != ShotSize.UNKNOWN:
        bits.append(cam.shot_size.value)
    if cam.subject != Subject.UNKNOWN:
        bits.append(cam.subject.value)
    return f"{cam.name} ({' '.join(bits)})"


class ShotPlanner:
    def __init__(self, engine: "DirectorEngine") -> None:
        self.e = engine

    # ------------------------------------------------------------------ WHAT
    def gesture_corroboration(self) -> float:
        """How much the picture backs up an audio gesture (audio alone is not proof of a DJ action).

        1.0 hands visibly moving in the mixer shot, 0.75 no picture information, 0.4 picture shows
        no movement there.
        """
        e = self.e
        mc = e.mixer_camera()
        if mc is None:
            return 0.75
        nov = e._novelty.get(mc.name)
        try:
            act = e.visual_activity(mc)
        except Exception:
            act = None
        if act is None:
            return 0.75
        if act >= 0.3 or (nov is not None and nov.novelty >= e.p.visual_novelty_threshold):
            return 1.0
        return 0.4 if act < 0.1 else 0.75

    def what(self, now: float, cur: "Camera", state: MusicState, cov: dict, targets: dict, vis_ev,
             ignore_cooldown: bool = False, gesture: bool = False) -> list[Candidate]:
        e = self.e
        p = e.p
        from dj_director.director.engine import MUSICAL_FIT     # local import: no cycle at load time
        last_insert = next((s for s in reversed(e.history)
                            if e.reg.get(s.camera) is not None
                            and not e.reg.is_anchor(e.reg.get(s.camera))
                            and s.camera != cur.name), None)
        last_cls = e.class_of(e.reg.get(last_insert.camera)) if last_insert else None
        recent = [s.camera for s in e.history[-8:]]
        mixer = e.mixer_camera() if gesture else None
        corroboration = self.gesture_corroboration() if gesture else 0.0
        quality_known = e.quality_known()
        out: list[Candidate] = []
        for c in e.reg.usable():
            if c.name == cur.name or e.reg.is_anchor(c) or now < e._failed_until.get(c.name, 0):
                continue
            if not c.allows(state.value):
                continue
            cls = e.class_of(c)
            if targets.get(cls, 0.0) <= 0:
                continue
            if now - c.last_used_end < c.cooldown and not ignore_cooldown:
                continue
            if e.veto(c):
                continue
            parts: dict[str, float] = {
                "coverage": p.coverage_gain * (targets[cls] - cov.get(cls, 0.0)) * 8.0,
                "musical": MUSICAL_FIT.get(c.role, MUSICAL_FIT[R.CUSTOM]).get(state, 0.0),
                "visual": (vis_ev.strength if vis_ev is not None and vis_ev.camera == c.name else 0.0)
                          + float(e.visual_fit(c)),
                "preference": 0.25 * (c.weight - 1.0) + 0.02 * (c.priority - 5),
                "freshness": 0.15 * min(1.0, (now - c.last_used_end) / max(p.coverage_window, 1.0)),
                "sequence": -0.15 if last_cls is not None and last_cls == cls else 0.0,
            }
            cont = assess(cur, c, recent)
            if p.continuity_weight > 0:
                parts["continuity"] = p.continuity_weight * cont.value
            if p.contextual_grammar:
                parts["context"] = self._context(c, state, mixer, corroboration)
            if quality_known and e.quality_of(c) == "UNKNOWN":
                parts["quality"] = -0.05                # no analysis is not proof of a good picture
            out.append(Candidate(c, sum(parts.values()), parts, cont))
        if last_insert is not None and len(out) > 1:
            # never the same insert twice around the master (M-A-M-A) while an alternative exists
            out = [x for x in out if x.camera.name != last_insert.camera]
        if state == MusicState.INCREASING and len(out) > 1 and e.build_up_reliable():
            # build-up: keep the reveal shot back for the drop (tension, then release)
            rc = e.reveal_camera()
            if rc is not None:
                out = [x for x in out if x.camera.name != rc.name]
        if p.continuity_weight > 0 and len(out) > 1:
            # a (near) jump cut is only taken when nothing better exists
            clean = [x for x in out if not x.continuity.jump_cut]
            if clean:
                out = clean
        out.sort(key=lambda x: (-x.score, x.camera.name))
        return out

    def _context(self, c: "Camera", state: MusicState, mixer: Optional["Camera"], corroboration: float) -> float:
        """DJ-set editing grammar on top of the role x state table."""
        v = 0.0
        if mixer is not None and c.name == mixer.name:
            v += GESTURE_BONUS * corroboration           # active mixing: the mixer shot matters now
        if state == MusicState.SPARSE:
            # breakdown: atmosphere and performer, fewer tight details
            if c.subject in (Subject.DJ, Subject.ENVIRONMENT, Subject.CROWD) or c.shot_size in (
                    ShotSize.WIDE, ShotSize.MEDIUM):
                v += 0.2
            if c.role in (R.DETAIL, R.OVERHEAD) and mixer is None:
                v -= 0.1
        elif state == MusicState.REENTRY:
            if c.subject in (Subject.CROWD, Subject.ENVIRONMENT) or c.shot_size == ShotSize.WIDE:
                v += 0.2
        return v

    # ------------------------------------------------------------------ HOW LONG
    def how_long_master(self, sp: "ShotSpec", cov: dict, targets: dict,
                        state: Optional[MusicState] = None) -> float:
        """Planned master dwell, pulled shorter when the master is over budget and longer when under.

        In a breakdown the master breathes longer (x1.3, still inside its preferred window) and the
        budget may not shorten it.
        """
        e = self.e
        over = cov.get(COVER_MASTER, 0.0) - targets.get(COVER_MASTER, 0.7)
        factor = min(1.5, max(0.5, 1.0 - e.p.coverage_gain * over * 8.0))
        if state == MusicState.SPARSE and e.p.contextual_grammar:
            # the budget never shortens a breakdown master: no cut just to fix a percentage
            factor = max(factor, 1.0) * 1.3
        return min(max(e.shot_target * factor, sp.pref_min), sp.pref_max)

    def how_long_insert(self, sp: "ShotSpec", cov: dict, targets: dict) -> float:
        """Planned insert length, pulled inside the preferred window by the class budget.

        The variety-sequence length is *compressed* towards the short end when the
        class is over budget and towards the long end when under (at most 70 %), so
        some variety always survives and lengths never collapse onto one value.
        """
        e = self.e
        over = cov.get(sp.cls, 0.0) - targets.get(sp.cls, 0.0)
        rel = {c: cov.get(c, 0.0) / t - 1.0 for c, t in targets.items() if c != COVER_MASTER and t > 0}
        lead = rel.get(sp.cls, 0.0) - min(rel.values()) if rel else 0.0
        g = e.p.coverage_gain
        if over > 0 or lead > 0.05:
            bound, k = sp.pref_min, min(0.7, g * max(over * 10.0, lead * 1.5))
        else:
            bound, k = sp.pref_max, min(0.85, g * -over * 15.0)
        return min(max(e.shot_target + (bound - e.shot_target) * k, sp.pref_min), sp.pref_max)

    # ------------------------------------------------------------------ the plan, explained
    def plan(self, why: str, target: "Camera", timing, wait_s: float, cand: Optional[Candidate] = None) -> ShotPlan:
        e = self.e
        sp = e.spec(target)
        what = describe_shot(target)
        if cand is not None:
            top = sorted(cand.parts.items(), key=lambda kv: -abs(kv[1]))[:3]
            what += " · " + ", ".join(f"{k} {v:+.2f}" for k, v in top)
            if cand.continuity.notes:
                what += " · " + "; ".join(cand.continuity.notes)
        when = timing.value if timing is not None else "immediate"
        if wait_s > 0.01:
            when += f" in {wait_s:.2f} s"
        if math.isinf(sp.cap):
            how = f"{sp.pref_min:.0f}-{sp.pref_max:.0f} s, no rigid maximum"
        else:
            how = f"{sp.pref_min:.0f}-{sp.pref_max:.0f} s, max {sp.cap:.0f} s"
        return ShotPlan(why, what, when, how)
