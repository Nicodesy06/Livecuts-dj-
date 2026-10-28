"""Shot continuity: how well a cut from one shot to the next reads on screen.

Professional multicam grammar for DJ sets, applied only where the user told us
about the shots (framing metadata). Unknown metadata contributes nothing: angles,
scales or subjects are never guessed.

Rules (each one explained in the result):

* **near-identical shots** (same ``visual_group``) - cutting between them reads
  as a glitch: strong penalty.
* **crops of one physical camera** (same ``source_group``) are scale variations,
  not new perspectives: same scale = jump cut, one step = jump-cut risk, two or
  more steps = a clean punch-in / pull-out.
* **similar framing from another camera** (angle within 30 deg, same scale and
  subject): jump-cut risk.
* **axis**: front <-> back (>= 135 deg apart) crosses the line. Not a rigid 180
  deg rule: a *bridge* makes it acceptable - the previous or next shot is an
  overhead / close detail of hands or equipment, a wide environment or crowd
  shot, or the cut is an editorial exception (drop reveal).
* **screen direction** flipping left <-> right on the same subject: small penalty.
* **meaningful difference** (scale two steps apart, angle >= 60 deg, another
  subject): small bonus.
* **repetition**: the same transition already used twice among the last cuts
  (beyond the hard A-B-A-B ban): penalty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from dj_director.config.schema import ScreenDirection, ShotSize, Subject, ViewAngle

SIZE_STEP = {ShotSize.WIDE: 0, ShotSize.MEDIUM: 1, ShotSize.CLOSE: 2, ShotSize.EXTREME_CLOSE: 3}
ANGLE_DEG = {ViewAngle.FRONT: 0, ViewAngle.FRONT_RIGHT: 45, ViewAngle.RIGHT: 90, ViewAngle.BACK_RIGHT: 135,
             ViewAngle.BACK: 180, ViewAngle.BACK_LEFT: 225, ViewAngle.LEFT: 270, ViewAngle.FRONT_LEFT: 315}

P_SAME_GROUP = 0.8
P_SAME_SOURCE_SAME_SIZE = 0.9
P_SAME_SOURCE_ONE_STEP = 0.5
P_SAME_SOURCE_UNKNOWN_SIZE = 0.3
P_SIMILAR_FRAMING = 0.5
P_AXIS = 0.35
P_DIRECTION_FLIP = 0.2
P_REPEATED_TRANSITION = 0.3
B_SCALE = 0.15
B_ANGLE = 0.1
B_SUBJECT = 0.1
B_PUNCH_IN = 0.1
JUMP_CUT_PENALTY = 0.5          # at or above this a cut counts as a (near) jump cut


@dataclass
class Continuity:
    penalty: float = 0.0
    bonus: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def value(self) -> float:
        return self.bonus - self.penalty

    @property
    def jump_cut(self) -> bool:
        return self.penalty >= JUMP_CUT_PENALTY


def _angle_gap(a: ViewAngle, b: ViewAngle) -> Optional[float]:
    if a not in ANGLE_DEG or b not in ANGLE_DEG:
        return None
    d = abs(ANGLE_DEG[a] - ANGLE_DEG[b]) % 360
    return min(d, 360 - d)


def is_bridge(cam) -> bool:
    """A shot that resets spatial orientation: overhead, a close detail of hands / gear, a wide room shot."""
    if cam is None:
        return False
    if cam.view_angle == ViewAngle.TOP:
        return True
    if cam.subject in (Subject.HANDS, Subject.EQUIPMENT) and cam.shot_size in (ShotSize.CLOSE,
                                                                                 ShotSize.EXTREME_CLOSE):
        return True
    return cam.subject in (Subject.CROWD, Subject.ENVIRONMENT) and cam.shot_size == ShotSize.WIDE


def assess(prev, nxt, recent: Sequence[str] = (), editorial_exception: bool = False) -> Continuity:
    """Continuity of cutting from ``prev`` to ``nxt`` (runtime cameras with framing metadata).

    ``recent`` is the sequence of recent shot names, oldest first, ending with ``prev``.
    """
    c = Continuity()
    if prev is None or nxt is None or prev.name == nxt.name:
        return c
    size_a, size_b = SIZE_STEP.get(prev.shot_size), SIZE_STEP.get(nxt.shot_size)
    same_source = bool(prev.source_group) and prev.source_group == nxt.source_group

    if prev.visual_group and prev.visual_group == nxt.visual_group:
        c.penalty += P_SAME_GROUP
        c.notes.append("same visual group")

    if same_source:
        if size_a is None or size_b is None:
            c.penalty += P_SAME_SOURCE_UNKNOWN_SIZE
            c.notes.append("same physical camera")
        else:
            steps = abs(size_a - size_b)
            if steps == 0:
                c.penalty += P_SAME_SOURCE_SAME_SIZE
                c.notes.append("same camera, same scale (jump cut)")
            elif steps == 1:
                c.penalty += P_SAME_SOURCE_ONE_STEP
                c.notes.append("same camera, small scale change (jump-cut risk)")
            else:
                c.bonus += B_PUNCH_IN
                c.notes.append("same camera, clear scale change")
    else:
        gap = _angle_gap(prev.view_angle, nxt.view_angle)
        same_subject = (prev.subject != Subject.UNKNOWN and prev.subject == nxt.subject)
        if (gap is not None and gap < 30 and size_a is not None and size_a == size_b and same_subject):
            c.penalty += P_SIMILAR_FRAMING
            c.notes.append("similar angle and scale (jump-cut risk)")
        if gap is not None and gap >= 135:
            if editorial_exception or is_bridge(prev) or is_bridge(nxt):
                c.notes.append("axis change through a bridge")
            else:
                c.penalty += P_AXIS
                c.notes.append("crosses the axis front-back")
        if (same_subject and {prev.screen_direction, nxt.screen_direction} == {ScreenDirection.LEFT,
                                                                               ScreenDirection.RIGHT}):
            c.penalty += P_DIRECTION_FLIP
            c.notes.append("screen direction flips")
        if size_a is not None and size_b is not None and abs(size_a - size_b) >= 2:
            c.bonus += B_SCALE
            c.notes.append("clear scale change")
        if gap is not None and 60 <= gap < 135:
            c.bonus += B_ANGLE
            c.notes.append("different angle")
        if (prev.subject != Subject.UNKNOWN and nxt.subject != Subject.UNKNOWN
                and prev.subject != nxt.subject):
            c.bonus += B_SUBJECT
            c.notes.append("different subject")

    # the same transition, again and again (the hard A-B-A-B ban is enforced elsewhere)
    seq = list(recent)
    pairs = list(zip(seq, seq[1:]))[-6:]
    if pairs.count((prev.name, nxt.name)) >= 2:
        c.penalty += P_REPEATED_TRANSITION
        c.notes.append("repeats the same transition")
    return c


def sequence_report(shots: Sequence[str], cams: dict) -> dict:
    """Count continuity problems in a finished shot sequence (simulation metrics)."""
    jump = axis = repeated = 0
    for i, (a, b) in enumerate(zip(shots, shots[1:])):
        ca, cb = cams.get(a), cams.get(b)
        if ca is None or cb is None:
            continue
        r = assess(ca, cb, shots[:i + 1])
        jump += r.jump_cut
        axis += "crosses the axis front-back" in r.notes
        repeated += "repeats the same transition" in r.notes
    return {"jump_cuts": jump, "axis_crossings": axis, "repeated_transitions": repeated}
