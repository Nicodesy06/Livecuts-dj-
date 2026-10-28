"""Director decision engine.

Explicit, deterministic and testable: given the clock time, the latest music
snapshot and musical events, it returns one :class:`DirectorDecisionEvent`
per tick. It never talks to OBS itself; the caller executes SWITCH decisions
and reports back (``on_switch_failed`` / ``on_external_change``).

Editorial model (0.1.3)
-----------------------
The master is home. A cut either *enters* a secondary camera or *exits* it, and
the two are decided by different rules:

ENTRY (on the master)
    Born from an opportunity, never from a timer alone:
      * musical   - probabilistic events (re-entry, transition, sparse start),
      * visual    - *novelty* of activity on a camera relative to its own recent
                    baseline (continuous presence habituates to zero),
      * editorial - the master has had its share of the coverage budget and the
                    planned dwell (20-50 s, budget-adjusted) is reached.
    Entries prefer a reliable metrical boundary (phrase > downbeat > beat); with
    an unreliable beat they are taken at once rather than faking precision.
    Destination = coverage deficit (soft quota) + musical fit + visual novelty +
    sequence context (no immediate repetition of the same insert).

EXIT (on a secondary)
    Governed by duration: a planned length inside the preferred window (Detail
    6-10 s, Side 10-18 s), shifted by the class budget, aligned to a beat, and an
    absolute hard cap (Detail 12-15 s, Side 20-25 s) that is never exceeded.
    Continuity may stretch a shot to the end of its preferred window - visual
    novelty that is still decaying, or the musical moment that justified it - but
    never beyond. A strong drop returns to the master early. The destination is
    the master unless the budget clearly asks for a second insert.

Editorial coverage budget
    Shares of on-air time per class (master / detail / side) over a sliding
    window (5 min) are compared with soft targets (70/15/15). The deficit steers
    master dwell, destination choice and planned insert lengths. Nothing chases
    the targets second by second.

Scene awareness (0.2)
    A visual *veto* (black, frozen, blurred / blocked, or judged unusable by the
    slow eye) removes a camera from the candidates and ends its shot early when
    it is on air. Drop grammar: the *reveal* camera (chosen scene, the slow
    eye's suggestion, else wide / behind / side) is held back during the
    build-up and opened on the drop, on the beat.

DJ gestures (0.3)
    Heard in the master audio, no MIDI needed: an EQ / isolator kill or a filter
    sweep (one end of the spectrum gone, the rest of the mix untouched) cuts to
    the *mixer camera*, which is held while the gesture lasts, up to its own
    cap ``mix_shot_cap`` (14 s by default = the ordinary Detail maximum,
    configurable 12-25 s). When the bass slams back in (or a drop re-enters),
    the reveal shot opens on the beat. A track that empties by itself is not a
    gesture: that is the sparse / re-entry logic.

Determinism
    No random numbers. Shot lengths vary through a low-discrepancy (golden
    ratio) sequence per coverage class, offset by the seed, and through the
    music itself (boundary alignment). Same inputs + seed => same decisions;
    consecutive shots never repeat the same planned length.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

from dj_director.config.schema import (COVER_DETAIL, COVER_MASTER, COVER_SIDE, HARD_CAP_RANGE,
                                       CameraRole, DirectorParams, ROLE_COVERAGE)
from dj_director.core.events import (DirectorAction, DirectorDecisionEvent, MusicEvent,
                                     MusicSnapshot, MusicState, TimingMode, next_correlation_id)
from dj_director.director.cameras import Camera, CameraRegistry
from dj_director.director.continuity import assess
from dj_director.director.shot_planner import ShotPlanner

S = MusicState
R = CameraRole

# How well each role tells each musical situation (editorial grammar, tunable).
MUSICAL_FIT: dict[CameraRole, dict[MusicState, float]] = {
    R.MASTER:   {S.STABLE_GROOVE: .25, S.INCREASING: .10, S.DECREASING: .20, S.SPARSE: .15,
                 S.TRANSITION: .00, S.REENTRY: .35, S.UNCERTAIN: .20, S.NO_SIGNAL: .50},
    R.DETAIL:   {S.STABLE_GROOVE: .05, S.INCREASING: .30, S.DECREASING: .10, S.SPARSE: .30,
                 S.TRANSITION: .45, S.REENTRY: -.20, S.UNCERTAIN: .00, S.NO_SIGNAL: -.50},
    R.SIDE:     {S.STABLE_GROOVE: .20, S.INCREASING: .20, S.DECREASING: .15, S.SPARSE: .20,
                 S.TRANSITION: .20, S.REENTRY: .25, S.UNCERTAIN: .05, S.NO_SIGNAL: -.30},
    R.BEHIND:   {S.STABLE_GROOVE: .20, S.INCREASING: .25, S.DECREASING: .10, S.SPARSE: .20,
                 S.TRANSITION: .15, S.REENTRY: .30, S.UNCERTAIN: .05, S.NO_SIGNAL: -.30},
    R.WIDE:     {S.STABLE_GROOVE: .20, S.INCREASING: .10, S.DECREASING: .15, S.SPARSE: .25,
                 S.TRANSITION: .10, S.REENTRY: .45, S.UNCERTAIN: .05, S.NO_SIGNAL: .00},
    R.OVERHEAD: {S.STABLE_GROOVE: .10, S.INCREASING: .25, S.DECREASING: .10, S.SPARSE: .30,
                 S.TRANSITION: .40, S.REENTRY: .00, S.UNCERTAIN: .00, S.NO_SIGNAL: -.40},
    R.CROWD:    {S.STABLE_GROOVE: .15, S.INCREASING: .25, S.DECREASING: .10, S.SPARSE: .15,
                 S.TRANSITION: .05, S.REENTRY: .50, S.UNCERTAIN: .00, S.NO_SIGNAL: -.30},
    R.CUSTOM:   {s: .15 for s in MusicState} | {S.NO_SIGNAL: -.30},
}

# Base strength of each musical event as a reason to cut (times its confidence).
EVENT_WEIGHT = {"reentry": 0.95, "transition": 0.5, "sparse_start": 0.4,
                # DJ gestures heard in the audio (music.mixing): EQ / isolator kills, filters, bass back
                "eq_cut": 1.0, "filter": 0.9, "eq_return": 1.0}
EVENT_TTL = {"reentry": 2.5, "transition": 4.0, "sparse_start": 3.0, "eq_cut": 3.0, "filter": 3.0,
             "eq_return": 2.5}
GESTURES = ("eq_cut", "filter")            # the DJ is working the mixer -> mixer camera
DROP_EVENTS = ("reentry", "eq_return")     # the low end slams back in -> reveal shot
GESTURE_TEXT = {"low": "cuts the lows", "high": "cuts the highs", "high-pass": "high-pass filter",
                "low-pass": "low-pass filter"}
MIX_SHOT_RANGE = (12.0, 25.0)             # cap of a mixer shot held during a DJ gesture
PHRASE_BEATS = 16      # 4 bars of 4/4: the smallest phrase unit dance music is built from
PHI_INV = 0.6180339887498949
COVERAGE_PRIOR_S = 45.0            # pseudo-seconds at target shares: smooths the first minutes
VISUAL_EVENT_TTL = 3.0
VISUAL_REFRACTORY = 15.0
CONTINUITY_STATES = (S.TRANSITION, S.SPARSE, S.INCREASING)


@dataclass
class Shot:
    camera: str
    start: float
    end: Optional[float] = None
    reason: str = ""


@dataclass
class PendingSwitch:
    target: str
    fire_at: float
    created: float
    timing: TimingMode
    reason: str
    opportunity: float
    confidence: float
    correlation_id: int
    music_triggered: bool
    scores: tuple = ()
    kind: str = "entry"               # "entry" | "exit"
    trigger: str = ""                 # "editorial" | "visual" | "musical" | "planned" | ...
    deadline: float = math.inf        # exits: must fire before the hard cap
    plan: tuple = ()                  # ShotPlan.as_tuple(): why / what / when / how long


@dataclass(frozen=True)
class ShotSpec:
    """Effective (preset-scaled, cap-clamped) duration grammar of a camera."""
    min: float
    pref_min: float
    pref_max: float
    cap: float                        # math.inf = no rigid maximum (master)
    cooldown: float
    cls: str


@dataclass
class EngineSettings:
    beats_per_bar: int = 4
    latency_offset: float = 0.04      # s, command sent this early
    override_policy: str = "timed"
    override_seconds: float = 30.0
    no_signal_policy: str = "master"
    drop_scene: str = ""              # reveal camera for drops ("" = automatic)
    mixer_scene: str = ""             # camera on the mixer for DJ gestures ("" = automatic)


class VisualNovelty:
    """Novelty of visual activity relative to the camera's own recent baseline.

    The baseline rises quickly (habituation, ~4 s) and falls slowly (~30 s), so
    *continuous* activity - hands resting on the mixer - converges to zero
    novelty within seconds and can neither trigger repeatedly nor extend a shot.
    Only a clear rise above what the camera has been showing counts.
    """

    def __init__(self, up_tau: float = 4.0, down_tau: float = 30.0, margin: float = 0.1,
                 scale: float = 0.3) -> None:
        self.up_tau, self.down_tau, self.margin, self.scale = up_tau, down_tau, margin, scale
        self.baseline: Optional[float] = None
        self.novelty = 0.0
        self.last_t = 0.0
        self.last_event_t = -1e9

    def update(self, t: float, activity: Optional[float]) -> float:
        if activity is None:
            self.novelty = 0.0
            return 0.0
        a = min(1.0, max(0.0, float(activity)))
        if self.baseline is None:            # first sight: whatever is there is the baseline
            self.baseline, self.last_t = a, t
            return 0.0
        dt = max(0.0, t - self.last_t)
        self.last_t = t
        self.novelty = min(1.0, max(0.0, (a - self.baseline - self.margin) / self.scale))
        tau = self.up_tau if a > self.baseline else self.down_tau
        self.baseline += (a - self.baseline) * (1.0 - math.exp(-dt / tau))
        return self.novelty


@dataclass(frozen=True)
class VisualEvent:
    t: float
    camera: str
    strength: float


def _gesture_text(ev: MusicEvent) -> str:
    side = ev.detail.split(":", 1)[0].strip()
    return GESTURE_TEXT.get(side, "works the mixer")


class DirectorEngine:
    def __init__(self, registry: CameraRegistry, params: DirectorParams,
                 settings: EngineSettings | None = None, seed: int = 0,
                 visual_fit: Callable[[Camera], float] | None = None,
                 visual_activity: Callable[[Camera], Optional[float]] | None = None,
                 visual_issue: Callable[[Camera], str] | None = None,
                 reveal_hint: Callable[[], Optional[str]] | None = None,
                 visual_quality: Callable[[Camera], str] | None = None) -> None:
        self.reg = registry
        self.p = params
        self.s = settings or EngineSettings()
        self.seed = seed
        self._seed_offset = (seed * 0.7548776662466927) % 1.0
        self.visual_fit = visual_fit or (lambda cam: 0.0)
        self.visual_activity = visual_activity or (lambda cam: None)
        self.visual_issue = visual_issue or (lambda cam: "")
        self.reveal_hint = reveal_hint or (lambda: None)
        self.visual_quality = visual_quality or (lambda cam: "UNKNOWN")
        self.planner = ShotPlanner(self)
        self._last_snap: Optional[MusicSnapshot] = None
        self._ranked: list = []
        self.current: Optional[str] = None
        self.shot_start = 0.0
        self.shot_target = 0.0
        self.shot_spec: Optional[ShotSpec] = None
        self.history: list[Shot] = []
        self.cut_times: deque[float] = deque(maxlen=64)
        self.pending: Optional[PendingSwitch] = None
        self.override_until = 0.0
        self.consecutive_inserts = 0
        self._triggers: list[MusicEvent] = []
        self._failed_until: dict[str, float] = {}
        self.phrase_anchor = 0.0           # time of the last confident re-entry (0 = none)
        self._anchor_period = 0.0
        self._variety_index: dict[str, int] = {}
        self._novelty: dict[str, VisualNovelty] = {}
        self._visual_events: list[VisualEvent] = []
        self.last_decision: Optional[DirectorDecisionEvent] = None
        self._mix_shot = False             # current shot was opened by a DJ gesture
        self._gesture_off_since: Optional[float] = None
        self._last_frac = 0.5

    # ------------------------------------------------------------------ external notifications
    def set_params(self, params: DirectorParams) -> None:
        self.p = params

    def sync_current(self, scene: Optional[str], now: float) -> None:
        """Adopt the scene currently on air (start-up / resume), without a decision."""
        if scene != self.current:
            self._start_shot(scene, now, "adopted from OBS")

    def on_external_change(self, scene: str, now: float) -> DirectorDecisionEvent:
        """A scene change we did not command (operator clicked in OBS)."""
        self.pending = None
        self._start_shot(scene, now, "manual change")
        pol = self.s.override_policy
        if self.reg.get(scene) is None:
            # The operator went to a scene we do not manage (e.g. "Be right back"):
            # never pull them out of it automatically.
            self.override_until = math.inf
            why = f"switched to unmanaged scene '{scene}'; automatic direction paused until Resume"
        elif pol == "timed":
            self.override_until = now + self.s.override_seconds
            why = f"manual change respected for {self.s.override_seconds:.0f}s"
        elif pol == "until_resume":
            self.override_until = math.inf
            why = "manual change respected until Resume"
        else:
            self.override_until = 0.0
            why = "manual change adopted; automatic direction continues"
        return self._decision(now, DirectorAction.OVERRIDE, None, why, 1.0, None)

    def clear_override(self) -> None:
        self.override_until = 0.0

    def on_switch_failed(self, scene: str, previous: Optional[str], now: float) -> None:
        """OBS refused the cut: restore the previous shot exactly as it was."""
        self._failed_until[scene] = now + 10.0
        failed = self.reg.get(scene)
        if self.history and self.history[-1].camera == scene:
            self.history.pop()
            if failed is not None:
                cls = self.class_of(failed)
                self._variety_index[cls] = max(0, self._variety_index.get(cls, 1) - 1)
        self.current = previous
        self._mix_shot = False
        prev_cam = self.reg.get(previous)
        if self.history and self.history[-1].camera == previous:
            self.history[-1].end = None
            self.shot_start = self.history[-1].start
        if prev_cam is not None:
            self.shot_spec = self.spec(prev_cam)
            if self.reg.is_anchor(prev_cam):
                self.consecutive_inserts = 0
        if self.cut_times:
            self.cut_times.pop()

    def on_switch_confirmed(self, scene: str, t: float) -> None:
        """OBS confirmed ``scene`` in Program at ``t``: exposure statistics use the real on-air time.

        Only the bookkeeping of the shot history moves (by at most 3 s); planning keeps the decision
        time, so caps stay conservative.
        """
        if not self.history:
            return
        cur = self.history[-1]
        if cur.camera != scene or cur.end is not None or t <= cur.start:
            return
        cur.start += min(t - cur.start, 3.0)
        if len(self.history) >= 2 and self.history[-2].end is not None:
            self.history[-2].end = cur.start

    def force(self, scene: str, now: float, reason: str) -> DirectorDecisionEvent:
        """Explicit operator command (e.g. Force Master). Bypasses opportunity."""
        self.pending = None
        cid = next_correlation_id()
        self._start_shot(scene, now, reason)
        self.cut_times.append(now)
        return self._decision(now, DirectorAction.SWITCH, scene, reason, 1.0, None,
                              timing=TimingMode.IMMEDIATE, target_t=now, cid=cid, priority=10)

    # ------------------------------------------------------------------ public introspection
    def class_of(self, cam: Camera) -> str:
        if self.reg.is_anchor(cam):
            return COVER_MASTER
        cls = ROLE_COVERAGE.get(cam.role, COVER_SIDE)
        return COVER_SIDE if cls == COVER_MASTER else cls   # a second "master" works as an insert

    def spec(self, cam: Camera) -> ShotSpec:
        cls = self.class_of(cam)
        if cls == COVER_MASTER:
            sc = self.p.master_duration_scale
            return ShotSpec(cam.min_duration * sc, cam.pref_min * sc, max(cam.pref_max, cam.pref_min) * sc,
                            math.inf, 0.0, cls)
        sc = self.p.insert_duration_scale
        lo, hi = HARD_CAP_RANGE[cls]
        default_cap = self.p.detail_hard_cap if cls == COVER_DETAIL else self.p.side_hard_cap
        cap = min(max(cam.hard_cap or default_cap, lo), hi)
        pmax = min(cam.pref_max * sc, cap - 1.0)
        pmin = min(cam.pref_min * sc, pmax)
        mn = min(cam.min_duration * sc, pmin)
        return ShotSpec(mn, pmin, pmax, cap, cam.cooldown, cls)

    def veto(self, cam: Optional[Camera]) -> str:
        """Visual quality problem that makes the camera unairable right now ("" = none)."""
        if cam is None:
            return ""
        try:
            return self.visual_issue(cam) or ""
        except Exception:
            return ""

    def reveal_camera(self) -> Optional[Camera]:
        """The shot opened on a drop: chosen scene, else the slow eye's pick, else the widest insert."""
        if not self.p.drop_reveal:
            return None
        names = [self.s.drop_scene]
        try:
            names.append(self.reveal_hint())
        except Exception:
            pass
        for n in names:
            c = self.reg.get(n) if n else None
            if c is not None and c.usable and not self.reg.is_anchor(c):
                return c
        order = (R.CROWD, R.WIDE, R.BEHIND, R.SIDE, R.CUSTOM)
        cands = [c for c in self.reg.usable() if not self.reg.is_anchor(c) and c.role in order]
        if not cands:
            return None

        def opens_up(c: Camera) -> int:          # framing metadata, when known: wide / crowd shots first
            from dj_director.config.schema import ShotSize, Subject
            return 0 if (c.shot_size == ShotSize.WIDE or c.subject in (Subject.CROWD, Subject.ENVIRONMENT)) else 1
        return min(cands, key=lambda c: (opens_up(c), order.index(c.role), -c.weight, c.name))

    def quality_of(self, cam: Camera) -> str:
        """HEALTHY / UNKNOWN / FAILED (source health from the visual analysis; UNKNOWN = no information)."""
        try:
            q = self.visual_quality(cam) or "UNKNOWN"
        except Exception:
            q = "UNKNOWN"
        return "FAILED" if self.veto(cam) else q

    def quality_known(self) -> bool:
        """Visual analysis is running (some camera is HEALTHY): UNKNOWN then means "not verified"."""
        return any(self.quality_of(c) == "HEALTHY" for c in self.reg.usable())

    def build_up_reliable(self) -> bool:
        """Build-up predictions are used only with a confident beat and a confident state."""
        sn = self._last_snap
        return bool(sn is not None and sn.beat.confidence >= self.p.sync_confidence
                    and sn.state_confidence >= 0.5)

    def mixer_camera(self) -> Optional[Camera]:
        """The shot on the mixer used for DJ gestures: chosen scene, else the best DETAIL / OVERHEAD."""
        if not self.p.mix_gestures:
            return None
        c = self.reg.get(self.s.mixer_scene) if self.s.mixer_scene else None
        if c is not None and c.usable and not self.reg.is_anchor(c):
            return c
        cands = [c for c in self.reg.usable() if not self.reg.is_anchor(c) and c.role in (R.DETAIL, R.OVERHEAD)]
        if not cands:
            return None
        return min(cands, key=lambda c: (c.role != R.DETAIL, -c.weight, c.name))

    def _reveal_ok(self, now: float, rc: Optional[Camera], cur: Camera) -> bool:
        return (rc is not None and rc.name != cur.name and not self.veto(rc)
                and now >= self._failed_until.get(rc.name, 0) and now - rc.last_used_end >= 3.0
                and not self._would_ping_pong(rc.name)
                and not assess(cur, rc, [s.camera for s in self.history[-8:]], editorial_exception=True).jump_cut)

    def _would_ping_pong(self, target: str) -> bool:
        """Cutting to ``target`` would complete an A-B-A-B pattern (e.g. M-S-M-S)."""
        h = self.history
        return len(h) >= 3 and h[-2].camera == target and h[-3].camera == h[-1].camera

    def coverage_targets(self) -> dict[str, float]:
        """Soft targets, renormalised over the classes that actually have usable cameras."""
        present = {COVER_MASTER}
        for c in self.reg.usable():
            if not self.reg.is_anchor(c):
                present.add(self.class_of(c))
        raw = {COVER_MASTER: self.p.coverage_master, COVER_DETAIL: self.p.coverage_detail,
               COVER_SIDE: self.p.coverage_side}
        raw = {k: v for k, v in raw.items() if k in present and v > 0}
        if COVER_MASTER not in raw:
            raw[COVER_MASTER] = 0.5
        total = sum(raw.values())
        return {k: v / total for k, v in raw.items()}

    def coverage(self, now: float, prior: bool = True) -> dict[str, float]:
        """On-air share per coverage class over the sliding window (soft-quota input)."""
        targets = self.coverage_targets()
        t0 = now - self.p.coverage_window
        acc = {k: 0.0 for k in targets}
        for shot in reversed(self.history):
            end = shot.end if shot.end is not None else now
            if end <= t0:
                break
            cam = self.reg.get(shot.camera)
            if cam is None:
                continue                     # unmanaged scenes (manual) do not count
            cls = self.class_of(cam)
            if cls in acc:
                acc[cls] += max(0.0, min(end, now) - max(shot.start, t0))
        if prior:
            for k, tgt in targets.items():
                acc[k] += COVERAGE_PRIOR_S * tgt
        total = sum(acc.values())
        return {k: (v / total if total > 0 else 0.0) for k, v in acc.items()}

    # ------------------------------------------------------------------ main entry point
    def tick(self, now: float, snap: Optional[MusicSnapshot],
             events: list[MusicEvent] | tuple = ()) -> DirectorDecisionEvent:
        for e in events:
            if e.kind in EVENT_WEIGHT:
                self._triggers.append(e)
            if e.kind in DROP_EVENTS and e.confidence >= self.p.musical_cut_confidence:
                self._set_phrase_anchor(e.t, snap)
        self._triggers = [e for e in self._triggers if now - e.t <= EVENT_TTL[e.kind]]
        self._update_visual(now)
        self._last_snap = snap
        self._ranked = []
        state = snap.state if snap else MusicState.NO_SIGNAL
        d = self._tick(now, snap, state)
        self.last_decision = d
        return d

    def _tick(self, now: float, snap: Optional[MusicSnapshot], state: MusicState) -> DirectorDecisionEvent:
        cur = self.reg.get(self.current)

        # 1. pending switch: revalidate, fire, keep waiting or cancel
        if self.pending is not None:
            return self._handle_pending(now, snap, state)

        # 2. a manual change is being respected: never fight the operator
        if now < self.override_until:
            left = "until Resume" if math.isinf(self.override_until) else (
                f"{self.override_until - now:.0f}s left")
            return self._decision(now, DirectorAction.HOLD, None, f"manual override respected ({left})",
                                  1.0, state)

        # 3. nothing on air, or the camera on air is gone: go to the anchor now
        anchor = self.reg.anchor()
        if cur is None or not cur.usable:
            if anchor is None:
                return self._decision(now, DirectorAction.HOLD, None, "no usable camera configured", 0)
            why = "initial shot" if cur is None and self.current is None else (
                f"camera '{self.current}' unavailable; returning to anchor")
            return self._switch_now(now, anchor.name, why, 1.0, 0.0, state)

        tos = now - self.shot_start
        sp = self.shot_spec or self.spec(cur)

        # 4. no audio: hold, or return to the anchor
        if state == MusicState.NO_SIGNAL:
            if (self.s.no_signal_policy == "master" and anchor is not None and cur is not anchor
                    and tos >= sp.min):
                return self._switch_now(now, anchor.name, "no audio signal; returning to master",
                                        0.9, 0.0, state)
            return self._decision(now, DirectorAction.HOLD, None, "no audio signal; holding", 0.9, state)

        if self.reg.is_anchor(cur):
            return self._entry(now, snap, state, cur, sp, tos)
        return self._exit(now, snap, state, cur, sp, tos, anchor)

    # ------------------------------------------------------------------ ENTRY (on the master)
    def _entry(self, now: float, snap, state: MusicState, cur: Camera, sp: ShotSpec,
               tos: float) -> DirectorDecisionEvent:
        bad = self.veto(cur)
        if bad and tos >= 1.0:
            ranked = [x for x in self._rank_inserts(now, cur, state, self.coverage(now),
                                                    self.coverage_targets(), None, ignore_cooldown=True)]
            if ranked:
                best = ranked[0][0]
                timing, target_t = self._timing(now, snap, urgent=True, deadline=now + 1.0)
                return self._commit(now, best.name, f"entry: master shot unusable ({bad})", 0.9, 1.0,
                                    state, timing, target_t, (), kind="entry", trigger="visual")
        if tos < sp.min:
            return self._decision(now, DirectorAction.HOLD, None,
                                  f"minimum shot length not reached ({tos:.0f}/{sp.min:.0f}s)", 1.0, state)
        cov = self.coverage(now)
        targets = self.coverage_targets()
        dwell = self._master_dwell(sp, cov, targets, state)
        trig, trig_ev = self._music_trigger()
        vis_ev = self._best_visual_event(now)
        vis = vis_ev.strength if vis_ev is not None else 0.0

        editorial = 0.0
        if self.p.editorial_cuts and tos >= sp.pref_min:
            span = max(dwell - sp.pref_min, 1e-6)
            editorial = (tos - sp.pref_min) / span if dwell > sp.pref_min else 1.0 + (tos - sp.pref_min) / 10
            needs = [targets[k] - cov.get(k, 0.0) for k in targets if k != COVER_MASTER]
            if needs and max(needs) < -0.08:         # every insert class is over budget
                editorial *= 0.5
            if state == MusicState.UNCERTAIN:
                editorial *= 0.8
            if state == MusicState.SPARSE and self.p.contextual_grammar:
                editorial *= 0.7                    # breakdown: let it breathe, fewer unmotivated cuts
        early = tos < sp.pref_min
        burst = max(trig, vis)
        # Between the master minimum and its preferred dwell only a strong musical or
        # visual moment may cut in (e.g. a confident drop): 80 % of the normal threshold.
        opportunity = burst * 1.25 if early else editorial + burst
        m_share = cov.get(COVER_MASTER, 0.0)
        cov_txt = f"master {m_share * 100:.0f}%/{targets[COVER_MASTER] * 100:.0f}%"
        if opportunity < self.p.cut_threshold:
            if trig_ev is not None:
                why = f"{trig_ev.kind.replace('_', ' ')} noted but not enough reason to cut yet"
            elif tos < sp.pref_min:
                why = f"master {tos:.0f}s, inside preferred dwell ({sp.pref_min:.0f}-{sp.pref_max:.0f}s)"
            else:
                why = f"master {tos:.0f}s of ~{dwell:.0f}s planned ({cov_txt})"
            return self._decision(now, DirectorAction.HOLD, None, why, 1.0 - min(1.0, opportunity), state,
                                  opportunity=opportunity)
        if not self._gap_ok(now):
            return self._decision(now, DirectorAction.HOLD, None,
                                  "cut wanted but too soon after previous cut", 0.8, state,
                                  opportunity=opportunity)
        if not self._rate_ok(now):
            return self._decision(now, DirectorAction.HOLD, None,
                                  "cut wanted but cuts-per-minute limit reached", 0.8, state,
                                  opportunity=opportunity)

        gesture = bool(trig_ev is not None and trig_ev.kind in GESTURES) or bool(snap is not None
                                                                              and snap.mix_gesture)
        ranked = self._rank_inserts(now, cur, state, cov, targets, vis_ev, gesture=gesture)
        rc = self.reveal_camera()
        if (trig_ev is not None and trig_ev.kind in DROP_EVENTS and trig >= vis
                and self._reveal_ok(now, rc, cur) and rc.allows(state.value)):
            # drop grammar: open on the reveal shot, on the next beat - but never late
            timing, target_t = self._drop_timing(now, snap)
            reason = (f"entry: drop reveal on {rc.name} ({trig_ev.confidence:.2f}); "
                      f"{self.class_of(rc)} coverage {cov.get(self.class_of(rc), 0) * 100:.0f}%/"
                      f"{targets.get(self.class_of(rc), 0) * 100:.0f}%")
            return self._commit(now, rc.name, reason, max(0.6, trig_ev.confidence), opportunity, state,
                                timing, target_t, (), kind="entry", trigger="musical", music=True)
        mc = self.mixer_camera()
        if (trig_ev is not None and trig_ev.kind in GESTURES and trig >= vis and ranked
                and mc is not None and ranked[0][0].name == mc.name and not self._would_ping_pong(mc.name)):
            # the DJ is working the mixer and the planner picked the mixer shot: show it on the next beat
            timing, target_t = self._timing(now, snap, urgent=True)
            mcls = self.class_of(mc)
            reason = (f"entry: DJ {_gesture_text(trig_ev)} ({trig_ev.confidence:.2f}); "
                      f"{mcls} coverage {cov.get(mcls, 0) * 100:.0f}%/{targets.get(mcls, 0) * 100:.0f}%")
            return self._commit(now, mc.name, reason, max(0.6, trig_ev.confidence), opportunity, state,
                                timing, target_t, (), kind="entry", trigger="gesture", music=True)
        if not ranked:
            why = "only one usable camera" if len(self.reg.usable()) <= 1 else (
                "all alternatives cooling down or unsuitable")
            return self._decision(now, DirectorAction.HOLD, None, why, 0.7, state, opportunity=opportunity)
        best, _score, parts = ranked[0]

        if vis_ev is not None and vis >= trig and vis_ev.camera == best.name:
            source, urgent = f"entry: visual novelty on {best.name} ({vis:.2f})", True
            trigger = "visual"
        elif trig_ev is not None and trig >= vis:
            source = f"entry: {trig_ev.kind.replace('_', ' ')} ({trig_ev.confidence:.2f})"
            urgent, trigger = True, "musical"
        else:
            source = f"entry: editorial, master {tos:.0f}s of ~{dwell:.0f}s ({cov_txt})"
            urgent, trigger = False, "editorial"
        bcls = self.class_of(best)
        reasons = [source, f"{bcls} coverage {cov.get(bcls, 0) * 100:.0f}%/{targets.get(bcls, 0) * 100:.0f}%"]
        if parts.get("musical", 0) >= 0.3:
            reasons.append(f"{best.role.value.lower()} suits {state.label.lower()}")
        if best.cooldown > 0:
            reasons.append("cooldown passed")
        confidence = min(1.0, 0.3 + 0.5 * opportunity / max(self.p.cut_threshold, 1e-6))
        if trig_ev is not None and trigger == "musical":
            confidence = max(confidence, trig_ev.confidence)
        scores = tuple((c.name, round(s, 3)) for c, s, _ in ranked)
        timing, target_t = self._timing(now, snap, urgent=urgent)
        return self._commit(now, best.name, "; ".join(reasons), confidence, opportunity, state, timing,
                            target_t, scores, kind="entry", trigger=trigger, music=trigger == "musical")

    def _master_dwell(self, sp: ShotSpec, cov: dict, targets: dict, state: Optional[MusicState] = None) -> float:
        return self.planner.how_long_master(sp, cov, targets, state)

    def _rank_inserts(self, now: float, cur: Camera, state: MusicState, cov: dict, targets: dict,
                      vis_ev: Optional[VisualEvent], ignore_cooldown: bool = False, gesture: bool = False
                      ) -> list[tuple[Camera, float, dict]]:
        """WHAT: candidates ranked by the shot planner (explainable, deterministic)."""
        ranked = self.planner.what(now, cur, state, cov, targets, vis_ev, ignore_cooldown, gesture)
        self._ranked = ranked
        return [(c.camera, c.score, c.parts) for c in ranked]

    # ------------------------------------------------------------------ EXIT (on a secondary)
    def _exit(self, now: float, snap, state: MusicState, cur: Camera, sp: ShotSpec, tos: float,
              anchor: Optional[Camera]) -> DirectorDecisionEvent:
        deadline = self.shot_start + sp.cap
        safety = self.s.latency_offset + 0.15
        if anchor is None:
            return self._decision(now, DirectorAction.HOLD, None, "no usable camera configured", 0, state)
        if now >= deadline - safety:
            home = self._home(now, cur, state, anchor)
            return self._switch_now(now, home.name, f"exit: hard cap {sp.cap:.0f}s; back to anchor {home.name}",
                                    1.0, 1.0, state, kind="exit")
        bad = self.veto(cur)
        if bad and tos >= 1.0:
            home = self._home(now, cur, state, anchor)
            timing, target_t = self._timing(now, snap, urgent=True, deadline=min(deadline, now + 1.0))
            return self._commit(now, home.name, f"exit: shot unusable ({bad}); back to anchor {home.name}",
                                0.9, 1.0, state, timing, target_t, (), kind="exit", trigger="visual",
                                deadline=min(deadline, now + 1.0))
        trig, trig_ev = self._music_trigger()
        rc = self.reveal_camera()
        if (trig_ev is not None and trig_ev.kind in DROP_EVENTS and tos >= sp.min
                and self._reveal_ok(now, rc, cur) and rc.allows(state.value)):
            timing, target_t = self._drop_timing(now, snap, deadline=deadline)
            return self._commit(now, rc.name, f"exit: drop reveal on {rc.name} ({trig_ev.confidence:.2f})",
                                max(0.6, trig_ev.confidence), trig, state, timing, target_t, (),
                                kind="exit", trigger="musical", music=True, deadline=deadline)
        mc = self.mixer_camera()
        if (trig_ev is not None and trig_ev.kind in GESTURES and tos >= sp.min
                and self._reveal_ok(now, mc, cur) and mc.allows(state.value)
                and self.planner.gesture_corroboration() > 0.5):
            timing, target_t = self._timing(now, snap, urgent=True, deadline=deadline)
            return self._commit(now, mc.name, f"exit: DJ {_gesture_text(trig_ev)} ({trig_ev.confidence:.2f}); "
                                f"to the mixer {mc.name}", max(0.6, trig_ev.confidence), trig, state, timing,
                                target_t, (), kind="exit", trigger="gesture", music=True, deadline=deadline)
        if tos < sp.min:
            return self._decision(now, DirectorAction.HOLD, None,
                                  f"minimum shot length not reached ({tos:.0f}/{sp.min:.0f}s)", 1.0, state)
        cov = self.coverage(now)
        targets = self.coverage_targets()
        planned = self._insert_length(sp, cov, targets)
        gesture_note = ""
        if self._mix_shot:
            # mixer shot opened by a DJ gesture: held while the gesture lasts (inside its own cap),
            # released once the DJ has let go for a couple of seconds
            on = bool(snap is not None and snap.mix_gesture)
            if on:
                self._gesture_off_since = None
                planned = max(planned, self.shot_target)
                gesture_note = "DJ at work on the mixer"
            else:
                if self._gesture_off_since is None:
                    self._gesture_off_since = now
                if now - self._gesture_off_since >= 2.0:
                    planned = min(planned, max(tos, 6.0))
        continuity = self._continuity(now, cur, state, snap)
        if not continuity and state == MusicState.SPARSE and self.p.contextual_grammar and (
                self.planner._context(cur, state, None, 0.0) > 0):
            continuity = "breakdown atmosphere"           # let the atmosphere / performer shot breathe
        if continuity and not self._mix_shot:
            planned = max(planned, sp.pref_max)
        if gesture_note:
            continuity = gesture_note
        is_reveal = rc is not None and rc.name == cur.name

        # opportune early return: a drop belongs on the master / wide shot (or stays on the reveal)
        if (trig_ev is not None and trig_ev.kind in DROP_EVENTS and not is_reveal
                and MUSICAL_FIT.get(cur.role, {}).get(S.REENTRY, 0) < MUSICAL_FIT[R.MASTER][S.REENTRY]):
            timing, target_t = self._timing(now, snap, urgent=True, deadline=deadline)
            return self._commit(now, anchor.name,
                                f"exit: reentry ({trig_ev.confidence:.2f}); back to anchor {anchor.name}",
                                max(0.6, trig_ev.confidence), trig, state, timing, target_t, (),
                                kind="exit", trigger="musical", music=True, deadline=deadline)
        if tos < planned:
            why = f"insert {tos:.0f}s of ~{planned:.0f}s planned (preferred {sp.pref_min:.0f}-{sp.pref_max:.0f}s)"
            if continuity:
                why += f"; continuity: {continuity}"
            return self._decision(now, DirectorAction.HOLD, None, why, 0.8, state)

        # where to go: home, unless the budget clearly asks for a second insert
        anchor = self._home(now, cur, state, anchor)
        target, reasons = anchor, [f"exit: planned length reached ({tos:.0f}s, preferred "
                                   f"{sp.pref_min:.0f}-{sp.pref_max:.0f}s)", f"back to anchor {anchor.name}"]
        chain = self._chain_candidate(now, cur, state, cov, targets)
        if chain is not None:
            c, ccls = chain
            target = c
            reasons = [reasons[0], f"chain: {c.name} ({ccls} coverage {cov.get(ccls, 0) * 100:.0f}%/"
                                   f"{targets[ccls] * 100:.0f}%)"]
        timing, target_t = self._timing(now, snap, urgent=True, deadline=deadline)
        return self._commit(now, target.name, "; ".join(reasons), 0.8, 1.0, state, timing, target_t, (),
                            kind="exit", trigger="planned", deadline=deadline)

    def _home(self, now: float, cur: Camera, state: MusicState, anchor: Camera) -> Camera:
        """The anchor, unless its picture is unusable: then the best other usable insert."""
        if not self.veto(anchor):
            return anchor
        alts = self._rank_inserts(now, cur, state, self.coverage(now), self.coverage_targets(), None,
                                  ignore_cooldown=True)
        return alts[0][0] if alts else anchor

    def _insert_length(self, sp: ShotSpec, cov: dict, targets: dict) -> float:
        return self.planner.how_long_insert(sp, cov, targets)

    def _continuity(self, now: float, cur: Camera, state: MusicState, snap) -> str:
        """Reason to let the insert run to the end of its preferred window (never beyond)."""
        nov = self._novelty.get(cur.name)
        if nov is not None and nov.novelty >= self.p.visual_novelty_threshold:
            return "visual novelty"
        if (state in CONTINUITY_STATES and MUSICAL_FIT.get(cur.role, {}).get(state, 0.0) >= 0.3
                and snap is not None and snap.state_since >= self.shot_start - 1.0):
            return state.label.lower()
        return ""

    def _chain_candidate(self, now: float, cur: Camera, state: MusicState, cov: dict,
                         targets: dict) -> Optional[tuple[Camera, str]]:
        if self.consecutive_inserts >= self.p.max_consecutive_inserts:
            return None
        if cov.get(COVER_MASTER, 0.0) <= targets.get(COVER_MASTER, 0.7) + 0.05:
            return None
        best = None
        for c, score, parts in self._rank_inserts(now, cur, state, cov, targets, None):
            cls = self.class_of(c)
            if cls == self.class_of(cur):
                continue
            if targets[cls] - cov.get(cls, 0.0) > 0.03 and parts["musical"] >= 0.15 and score > self.p.hysteresis:
                best = (c, cls)
                break
        return best

    # ------------------------------------------------------------------ pending / revalidation
    def _handle_pending(self, now: float, snap, state: MusicState) -> DirectorDecisionEvent:
        p = self.pending
        target = self.reg.get(p.target)
        cancel = None
        if target is None or not target.usable or now < self._failed_until.get(p.target, 0):
            cancel = f"target {p.target} became unavailable"
        elif state == MusicState.NO_SIGNAL and not self.reg.is_anchor(target):
            cancel = "audio signal lost while waiting"
        elif now < self.override_until:
            cancel = "manual override"
        elif p.kind == "entry":
            if now - p.created > max(self.p.max_sync_wait, self.p.max_phrase_wait) + 1.0:
                cancel = "beat sync window expired"
            elif not target.allows(state.value):
                cancel = f"target no longer suits {state.label.lower()}"
            elif p.trigger == "editorial" and now >= p.fire_at:
                cls = self.class_of(target)
                cov, tg = self.coverage(now), self.coverage_targets()
                if cov.get(cls, 0.0) - tg.get(cls, 0.0) > 0.10:
                    cancel = "coverage target already reached"
        if cancel:
            self.pending = None
            return self._decision(now, DirectorAction.CANCEL, p.target, cancel, p.confidence, state,
                                  cid=p.correlation_id)
        # exits never wait past the hard cap
        due = now >= p.fire_at or (p.kind == "exit" and now >= p.deadline - self.s.latency_offset - 0.15) \
            or (p.kind == "exit" and now - p.created > self.p.max_sync_wait + 1.0)
        if due:
            self.pending = None
            return self._switch_now(now, p.target, p.reason, p.confidence, p.opportunity, state,
                                    timing=p.timing, target_t=p.fire_at + self.s.latency_offset,
                                    cid=p.correlation_id, scores=p.scores, kind=p.kind, trigger=p.trigger,
                                    plan=p.plan)
        return self._decision(now, DirectorAction.HOLD, p.target,
                              f"switch to {p.target} scheduled on {p.timing.value}", p.confidence, state,
                              cid=p.correlation_id, target_t=p.fire_at + self.s.latency_offset)

    def _commit(self, now: float, target: str, reason: str, confidence: float, opportunity: float,
                state: MusicState, timing: TimingMode, target_t: float, scores: tuple, kind: str,
                trigger: str, music: bool = False, deadline: float = math.inf) -> DirectorDecisionEvent:
        cid = next_correlation_id()
        plan = self._plan(reason, target, timing, max(0.0, (target_t or now) - now))
        if timing == TimingMode.IMMEDIATE or target_t - self.s.latency_offset <= now:
            return self._switch_now(now, target, reason, confidence, opportunity, state, timing=timing,
                                    target_t=target_t or now, cid=cid, scores=scores, kind=kind, trigger=trigger,
                                    plan=plan)
        self.pending = PendingSwitch(target, target_t - self.s.latency_offset, now, timing, reason,
                                     opportunity, confidence, cid, music, scores, kind, trigger, deadline, plan)
        wait = {TimingMode.PHRASE: "phrase boundary (4/8 bars after the last drop)"}.get(timing, timing.value)
        return self._decision(now, DirectorAction.SCHEDULE, target, f"{reason}; waiting for next {wait}",
                              confidence, state, opportunity=opportunity, timing=timing, target_t=target_t,
                              cid=cid, scores=scores, plan=plan)

    def _plan(self, reason: str, target: str, timing, wait_s: float) -> tuple:
        cam = self.reg.get(target)
        if cam is None:
            return ()
        cand = next((c for c in self._ranked if c.camera.name == target), None)
        return self.planner.plan(reason.split("; ")[0], cam, timing, wait_s, cand).as_tuple()

    # ------------------------------------------------------------------ opportunities
    def _music_trigger(self) -> tuple[float, Optional[MusicEvent]]:
        """Strongest pending musical / gesture reason to cut.

        Low confidence is conservative: with an uncertain music state only clear events count.
        A gesture heard in the audio is weighted by how much the picture backs it up.
        """
        best, best_ev = 0.0, None
        sn = self._last_snap
        uncertain = sn is not None and sn.state in (MusicState.UNCERTAIN,)
        corroboration = None
        for e in self._triggers:
            if e.confidence < self.p.musical_cut_confidence:
                continue
            if uncertain and e.confidence < 0.75:
                continue
            v = EVENT_WEIGHT[e.kind] * e.confidence * self.p.music_trigger_gain
            if e.kind in GESTURES:
                if corroboration is None:
                    corroboration = self.planner.gesture_corroboration()
                v *= corroboration
            if v > best:
                best, best_ev = v, e
        return best, best_ev

    def _update_visual(self, now: float) -> None:
        for c in self.reg.usable():
            act = self.visual_activity(c)
            if act is None:
                continue
            nov = self._novelty.setdefault(c.name, VisualNovelty())
            n = nov.update(now, act)
            if n >= self.p.visual_novelty_threshold and now - nov.last_event_t >= VISUAL_REFRACTORY:
                nov.last_event_t = now
                self._visual_events.append(VisualEvent(now, c.name, n))
        self._visual_events = [e for e in self._visual_events if now - e.t <= VISUAL_EVENT_TTL]

    def _best_visual_event(self, now: float) -> Optional[VisualEvent]:
        best = None
        for e in self._visual_events:
            cam = self.reg.get(e.camera)
            if cam is None or not cam.usable or self.reg.is_anchor(cam) or e.camera == self.current:
                continue
            if best is None or e.strength > best.strength:
                best = e
        return best

    def _gap_ok(self, now: float) -> bool:
        return not self.cut_times or now - self.cut_times[-1] >= self.p.min_cut_gap

    def _rate_ok(self, now: float) -> bool:
        recent = sum(1 for t in self.cut_times if now - t < 60.0)
        return recent < self.p.max_cuts_per_minute

    def _drop_timing(self, now: float, snap, deadline: float = math.inf) -> tuple[TimingMode, float]:
        """A drop is already happening: take the next beat only if it is close, otherwise cut now."""
        timing, t = self._timing(now, snap, urgent=True, deadline=deadline)
        if snap is not None and snap.beat.period > 0 and t - now > 0.5 * snap.beat.period:
            return TimingMode.IMMEDIATE, now
        return timing, t

    # ------------------------------------------------------------------ musical timing
    def _set_phrase_anchor(self, t: float, snap: Optional[MusicSnapshot]) -> None:
        """Remember when a confident re-entry (drop) happened.

        The raw event time is kept and snapped to the beat grid only when used:
        right after a beatless breakdown the beat phase is still re-locking, so
        snapping immediately could be half a beat off.
        """
        if snap is None or snap.beat.period <= 0:
            return
        self.phrase_anchor = t
        self._anchor_period = snap.beat.period

    def next_phrase_boundary(self, after: float, snap: Optional[MusicSnapshot],
                             beats: int = PHRASE_BEATS) -> float:
        """Predicted 4-bar boundary after ``after`` (0 if no valid anchor).

        Heuristic: dance music is arranged in blocks of 4/8/16 bars and drops start
        a block, so boundaries repeat every 16 beats from the last detected drop.
        Invalidated when the tempo changes (another track / pitch move).
        """
        if not self.phrase_anchor or snap is None or snap.beat.period <= 0 or snap.beat.next_beat_t <= 0:
            return 0.0
        P = snap.beat.period
        if abs(P / self._anchor_period - 1) > 0.02:
            self.phrase_anchor = 0.0
            return 0.0
        nb = snap.beat.next_beat_t
        anchor = nb + round((self.phrase_anchor - nb) / P) * P     # on the current beat grid
        L = beats * P
        return anchor + math.ceil((after - anchor) / L - 1e-9) * L

    def _timing(self, now: float, snap: Optional[MusicSnapshot], urgent: bool = False,
                deadline: float = math.inf) -> tuple[TimingMode, float]:
        """Phrase > downbeat > beat when the beat is reliable; never past ``deadline``."""
        if snap is None:
            return TimingMode.IMMEDIATE, now
        b = snap.beat
        if b.period <= 0 or b.confidence < self.p.sync_confidence:
            return TimingMode.IMMEDIATE, now
        earliest = now + self.s.latency_offset + 0.01
        hard = deadline - 0.15
        if self.p.phrase_sync and not urgent:
            # an 8-bar boundary if one is close enough (more likely a section change), else 4-bar
            for beats in (2 * PHRASE_BEATS, PHRASE_BEATS):
                t = self.next_phrase_boundary(earliest, snap, beats)
                if 0 < t <= min(now + self.p.max_phrase_wait, hard):
                    return TimingMode.PHRASE, t
        limit = min(now + self.p.max_sync_wait, hard)
        if self.p.prefer_downbeat and b.downbeat_confidence >= self.p.downbeat_confidence:
            t = b.next_downbeat_after(earliest, self.s.beats_per_bar)
            if 0 < t <= limit:
                return TimingMode.DOWNBEAT, t
        t = b.next_beat_after(earliest)
        if 0 < t <= limit:
            return TimingMode.BEAT, t
        return TimingMode.IMMEDIATE, now

    # ------------------------------------------------------------------ shots
    def _variety(self, cls: str) -> float:
        """Deterministic, non-repeating value in [0, 1) for the next shot of this class."""
        k = self._variety_index.get(cls, 0)
        self._variety_index[cls] = k + 1
        return (self._seed_offset + (k + 1) * PHI_INV) % 1.0

    def _switch_now(self, now: float, target: str, reason: str, confidence: float, opportunity: float,
                    state: Optional[MusicState], timing: TimingMode = TimingMode.IMMEDIATE,
                    target_t: float = 0.0, cid: int = 0, scores: tuple = (),
                    kind: str = "", trigger: str = "", plan: tuple = ()) -> DirectorDecisionEvent:
        cid = cid or next_correlation_id()
        prev = self.current
        tos = now - self.shot_start
        self._start_shot(target, now, reason)
        if trigger == "gesture":
            self._open_mix_shot()
        if not plan:
            plan = self._plan(reason, target, timing, 0.0)
        self.cut_times.append(now)
        return self._decision(now, DirectorAction.SWITCH, target, reason, confidence, state,
                              opportunity=opportunity, timing=timing, target_t=target_t or now, cid=cid,
                              scores=scores, current=prev, tos=tos, plan=plan)

    def _open_mix_shot(self) -> None:
        """Turn the shot just started into a mixer shot: preferred ~0.72*cap .. cap-1, its own cap.

        The default cap equals the ordinary Detail maximum (14 s): a gesture makes the mixer shot
        relevant and lets it run to the end of what a Detail may last, never beyond.
        """
        sp = self.shot_spec
        if sp is None:
            return
        lo, hi = MIX_SHOT_RANGE
        cap = min(max(self.p.mix_shot_cap, lo), hi)
        pmin, pmax = round(cap * 0.72, 1), cap - 1.0
        self.shot_spec = ShotSpec(sp.min, pmin, pmax, cap, sp.cooldown, sp.cls)
        self.shot_target = pmin + self._last_frac * (pmax - pmin)
        self._mix_shot = True
        self._gesture_off_since = None

    def _start_shot(self, scene: Optional[str], now: float, reason: str) -> None:
        if self.history and self.history[-1].end is None:
            self.history[-1].end = now
        old = self.reg.get(self.current)
        if old is not None:
            old.last_used_end = now
            old.airtime += now - self.shot_start
        self.current = scene
        self.shot_start = now
        self._mix_shot = False
        self._gesture_off_since = None
        cam = self.reg.get(scene)
        if cam is None:
            self.shot_spec = None
            self.shot_target = 30.0
        else:
            cam.uses += 1
            sp = self.spec(cam)
            self.shot_spec = sp
            v = self._variety(sp.cls)
            frac = 0.5 + self.p.duration_spread * (v - 0.5)
            self._last_frac = frac
            self.shot_target = sp.pref_min + frac * (sp.pref_max - sp.pref_min)
            if self.reg.is_anchor(cam):
                self.consecutive_inserts = 0
            else:
                self.consecutive_inserts += 1
        if scene is not None:
            self.history.append(Shot(scene, now, None, reason))
            if len(self.history) > 500:
                del self.history[:100]
        self._triggers.clear()                 # an event is consumed by at most one cut
        self._visual_events.clear()

    def _decision(self, now: float, action: DirectorAction, candidate: Optional[str], reason: str,
                  confidence: float, state: Optional[MusicState], opportunity: float = 0.0,
                  timing: Optional[TimingMode] = None, target_t: float = 0.0, cid: int = 0,
                  scores: tuple = (), priority: int = 0, current: Optional[str] = None,
                  tos: Optional[float] = None, plan: tuple = ()) -> DirectorDecisionEvent:
        return DirectorDecisionEvent(
            t=now, action=action, current_camera=current if current is not None else self.current,
            candidate_camera=candidate, reason=reason, confidence=round(float(confidence), 3),
            time_on_shot=round(tos if tos is not None else now - self.shot_start, 3),
            music_state=state or MusicState.NO_SIGNAL, opportunity=round(opportunity, 3),
            timing=timing, target_t=target_t, priority=priority, correlation_id=cid, scores=scores, plan=plan)
