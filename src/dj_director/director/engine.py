"""Director decision engine.

Explicit, deterministic and testable: given the clock time, the latest music
snapshot and musical events, it returns one :class:`DirectorDecisionEvent`
per tick. It never talks to OBS itself; the caller executes SWITCH decisions
and reports back (``on_switch_failed`` / ``on_external_change``).

Three separate decisions (architectural requirement):

A. Opportunity - *should we cut at all?*
   opportunity = duration pressure (time on shot vs. a per-shot sampled
   target length) + musical trigger (probabilistic events weighted by their
   confidence). Below ``cut_threshold`` the answer is HOLD, which is a normal
   editorial action, not a failure. Hard constraints (minimum shot length,
   global gap between cuts, cuts-per-minute cap, manual override, no usable
   alternative) veto any cut.

B. Destination - *which camera tells the moment best?*
   CameraScore = EditorialFit + MusicalFit + VisualFit + VarietyBonus
                 + Preference - RepetitionPenalty (+ tiny seeded jitter)
   compared with the value of staying (hysteresis), so near-equal options
   never cause ping-pong.

C. Timing - *now, on the next reliable beat, on the next downbeat, or never?*
   Beat sync only when the beat confidence clears ``sync_confidence``; the
   command is sent ``latency_offset`` early so the picture change lands on the
   beat. If the musical context changes while waiting, the switch is cancelled.

Randomness (shot-length sampling, jitter) comes from a seeded RNG, so a
replay with the same seed and inputs yields identical decisions.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

from dj_director.config.schema import CameraRole, DirectorParams
from dj_director.core.events import (DirectorAction, DirectorDecisionEvent, MusicEvent,
                                     MusicSnapshot, MusicState, TimingMode, next_correlation_id)
from dj_director.director.cameras import Camera, CameraRegistry

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
    R.WIDE:     {S.STABLE_GROOVE: .20, S.INCREASING: .10, S.DECREASING: .15, S.SPARSE: .25,
                 S.TRANSITION: .10, S.REENTRY: .45, S.UNCERTAIN: .05, S.NO_SIGNAL: .00},
    R.OVERHEAD: {S.STABLE_GROOVE: .10, S.INCREASING: .25, S.DECREASING: .10, S.SPARSE: .30,
                 S.TRANSITION: .40, S.REENTRY: .00, S.UNCERTAIN: .00, S.NO_SIGNAL: -.40},
    R.CUSTOM:   {s: .15 for s in MusicState} | {S.NO_SIGNAL: -.30},
}

# Base strength of each musical event as a reason to cut (times its confidence).
EVENT_WEIGHT = {"reentry": 0.95, "transition": 0.5, "sparse_start": 0.4}
EVENT_TTL = {"reentry": 2.5, "transition": 4.0, "sparse_start": 3.0}


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


@dataclass
class EngineSettings:
    beats_per_bar: int = 4
    latency_offset: float = 0.04      # s, command sent this early
    override_policy: str = "timed"
    override_seconds: float = 30.0
    no_signal_policy: str = "master"


class DirectorEngine:
    def __init__(self, registry: CameraRegistry, params: DirectorParams,
                 settings: EngineSettings | None = None, seed: int = 0,
                 visual_fit: Callable[[Camera], float] | None = None) -> None:
        self.reg = registry
        self.p = params
        self.s = settings or EngineSettings()
        self.rng = random.Random(seed)
        self.visual_fit = visual_fit or (lambda cam: 0.0)
        self.current: Optional[str] = None
        self.shot_start = 0.0
        self.shot_target = 0.0
        self.history: list[Shot] = []
        self.cut_times: deque[float] = deque(maxlen=64)
        self.pending: Optional[PendingSwitch] = None
        self.override_until = 0.0
        self.consecutive_inserts = 0
        self._triggers: list[MusicEvent] = []
        self._failed_until: dict[str, float] = {}
        self.last_decision: Optional[DirectorDecisionEvent] = None

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
        self._failed_until[scene] = now + 10.0
        if self.history and self.history[-1].camera == scene:
            self.history.pop()
            if self.history:
                self.history[-1].end = None
        self.current = previous
        if self.cut_times:
            self.cut_times.pop()

    def force(self, scene: str, now: float, reason: str) -> DirectorDecisionEvent:
        """Explicit operator command (e.g. Force Master). Bypasses opportunity."""
        self.pending = None
        cid = next_correlation_id()
        self._start_shot(scene, now, reason)
        self.cut_times.append(now)
        return self._decision(now, DirectorAction.SWITCH, scene, reason, 1.0, None,
                              timing=TimingMode.IMMEDIATE, target_t=now, cid=cid, priority=10)

    # ------------------------------------------------------------------ main entry point
    def tick(self, now: float, snap: Optional[MusicSnapshot],
             events: list[MusicEvent] | tuple = ()) -> DirectorDecisionEvent:
        for e in events:
            if e.kind in EVENT_WEIGHT:
                self._triggers.append(e)
        self._triggers = [e for e in self._triggers if now - e.t <= EVENT_TTL[e.kind]]
        state = snap.state if snap else MusicState.NO_SIGNAL
        d = self._tick(now, snap, state)
        self.last_decision = d
        return d

    def _tick(self, now: float, snap: Optional[MusicSnapshot], state: MusicState) -> DirectorDecisionEvent:
        cur = self.reg.get(self.current)

        # 1. pending switch: fire, keep waiting, or cancel
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

        # 4. no audio: hold, or return to the anchor
        if state == MusicState.NO_SIGNAL:
            if (self.s.no_signal_policy == "master" and anchor is not None and cur is not anchor
                    and tos >= cur.min_duration and self._gap_ok(now)):
                return self._switch_now(now, anchor.name, "no audio signal; returning to master",
                                        0.9, 0.0, state)
            return self._decision(now, DirectorAction.HOLD, None, "no audio signal; holding", 0.9, state)

        # ---------------------------------------------------------------- A. opportunity
        min_d = cur.min_duration * self._scale(cur)
        max_d = cur.max_duration * self._scale(cur)
        if tos < min_d:
            return self._decision(now, DirectorAction.HOLD, None,
                                  f"minimum shot length not reached ({tos:.0f}/{min_d:.0f}s)", 1.0, state)
        dur_p = self._duration_pressure(tos, min_d, self.shot_target, max_d)
        if state == MusicState.UNCERTAIN:
            dur_p *= 0.7                     # unsure about the music: let shots breathe
        trig, trig_ev = self._music_trigger(snap)
        opportunity = dur_p + trig
        is_anchor = self.reg.is_anchor(cur)
        overdue = (not is_anchor) and tos >= max_d
        reasons = []
        if trig_ev is not None:
            reasons.append(f"{trig_ev.kind.replace('_', ' ')} ({trig_ev.confidence:.2f})")
        if overdue:
            reasons.append(f"insert at max length ({tos:.0f}s)")
        elif dur_p >= 0.5:
            reasons.append(f"shot length {tos:.0f}s of ~{self.shot_target:.0f}s planned")

        if opportunity < self.p.cut_threshold and not overdue:
            why = "stable; no editorial reason to cut" if trig_ev is None else (
                f"{trig_ev.kind.replace('_', ' ')} noted but not enough reason to cut yet")
            if state == MusicState.STABLE_GROOVE and trig_ev is None:
                why = "stable groove; insufficient editorial benefit"
            return self._decision(now, DirectorAction.HOLD, None, why, 1.0 - opportunity, state,
                                  opportunity=opportunity)

        if not self._gap_ok(now):
            return self._decision(now, DirectorAction.HOLD, None,
                                  "cut wanted but too soon after previous cut", 0.8, state,
                                  opportunity=opportunity)
        if not self._rate_ok(now):
            return self._decision(now, DirectorAction.HOLD, None,
                                  "cut wanted but cuts-per-minute limit reached", 0.8, state,
                                  opportunity=opportunity)

        # ---------------------------------------------------------------- B. destination
        ranked = self._rank(now, cur, state)
        if not ranked:
            why = "only one usable camera" if len(self.reg.usable()) <= 1 else (
                "all alternatives cooling down or unsuitable")
            return self._decision(now, DirectorAction.HOLD, None, why, 0.7, state, opportunity=opportunity)
        best, best_score, best_parts = ranked[0]
        stay = self._stay_value(cur, state, tos, dur_p)
        forced = overdue or best_parts.get("forced_return", 0) > 0
        # A strong musical event lowers the bar for leaving the current shot.
        margin = self.p.hysteresis * max(0.0, 1.0 - trig)
        if not forced and best_score < stay + margin:
            return self._decision(now, DirectorAction.HOLD, best.name,
                                  f"current shot still fits; best alternative {best.name} not clearly better",
                                  0.6, state, opportunity=opportunity,
                                  scores=tuple((c.name, round(s, 3)) for c, s, _ in ranked))
        reasons.insert(0, f"min length satisfied ({tos:.0f}s)")
        if best_parts.get("forced_return"):
            reasons.append(f"back to {best.name} after {self.consecutive_inserts} inserts")
        elif self.reg.is_anchor(best):
            reasons.append(f"back to anchor {best.name}")
        elif best_parts.get("musical", 0) >= 0.3:
            reasons.append(f"{best.role.value.lower()} suits {state.label.lower()}")
        if best.cooldown > 0 and not self.reg.is_anchor(best):
            reasons.append("cooldown passed")
        confidence = min(1.0, opportunity / max(self.p.cut_threshold, 1e-6) * 0.5 + 0.3)
        if trig_ev is not None:
            confidence = max(confidence, trig_ev.confidence)
        scores = tuple((c.name, round(s, 3)) for c, s, _ in ranked)

        # ---------------------------------------------------------------- C. timing
        timing, target_t = self._timing(now, snap)
        cid = next_correlation_id()
        reason = "; ".join(reasons)
        if timing == TimingMode.IMMEDIATE or target_t - self.s.latency_offset <= now:
            return self._switch_now(now, best.name, reason, confidence, opportunity, state,
                                    timing=timing, target_t=target_t or now, cid=cid, scores=scores)
        self.pending = PendingSwitch(best.name, target_t - self.s.latency_offset, now, timing, reason,
                                     opportunity, confidence, cid, trig_ev is not None, scores)
        return self._decision(now, DirectorAction.SCHEDULE, best.name,
                              f"{reason}; waiting for next {timing.value}", confidence, state,
                              opportunity=opportunity, timing=timing, target_t=target_t, cid=cid,
                              scores=scores)

    # ------------------------------------------------------------------ helpers
    def _handle_pending(self, now: float, snap, state: MusicState) -> DirectorDecisionEvent:
        p = self.pending
        target = self.reg.get(p.target)
        cancel = None
        if target is None or not target.usable or now < self._failed_until.get(p.target, 0):
            cancel = f"target {p.target} became unavailable"
        elif state == MusicState.NO_SIGNAL and not self.reg.is_anchor(target):
            cancel = "audio signal lost while waiting"
        elif now - p.created > self.p.max_sync_wait + 1.0:
            cancel = "beat sync window expired"
        elif now < self.override_until:
            cancel = "manual override"
        if cancel:
            self.pending = None
            return self._decision(now, DirectorAction.CANCEL, p.target, cancel, p.confidence, state,
                                  cid=p.correlation_id)
        if now >= p.fire_at:
            self.pending = None
            return self._switch_now(now, p.target, p.reason, p.confidence, p.opportunity, state,
                                    timing=p.timing, target_t=p.fire_at + self.s.latency_offset,
                                    cid=p.correlation_id, scores=p.scores)
        return self._decision(now, DirectorAction.HOLD, p.target,
                              f"switch to {p.target} scheduled on {p.timing.value}", p.confidence, state,
                              cid=p.correlation_id, target_t=p.fire_at + self.s.latency_offset)

    def _scale(self, cam: Camera) -> float:
        return self.p.master_duration_scale if self.reg.is_anchor(cam) else self.p.insert_duration_scale

    def _sample_target(self, cam: Camera) -> float:
        sc = self._scale(cam)
        mn, mean, mx = cam.min_duration * sc, cam.mean_duration * sc, cam.max_duration * sc
        sigma = self.p.duration_spread
        if sigma <= 0:
            return mean
        # lognormal with the requested mean
        mu = math.log(mean) - sigma ** 2 / 2
        v = self.rng.lognormvariate(mu, sigma)
        return min(max(v, mn + 0.25 * (mean - mn)), mx)

    @staticmethod
    def _duration_pressure(tos: float, min_d: float, target: float, max_d: float) -> float:
        if tos <= min_d:
            return 0.0
        if tos <= target:
            x = (tos - min_d) / max(target - min_d, 1e-6)
            return x ** 2                      # gentle at first, ~1 at the planned length
        return 1.0 + (tos - target) / max(max_d - target, 1e-6)

    def _music_trigger(self, snap: Optional[MusicSnapshot]) -> tuple[float, Optional[MusicEvent]]:
        best, best_ev = 0.0, None
        for e in self._triggers:
            if e.confidence < self.p.musical_cut_confidence:
                continue
            v = EVENT_WEIGHT[e.kind] * e.confidence * self.p.music_trigger_gain
            if v > best:
                best, best_ev = v, e
        return best, best_ev

    def _gap_ok(self, now: float) -> bool:
        return not self.cut_times or now - self.cut_times[-1] >= self.p.min_cut_gap

    def _rate_ok(self, now: float) -> bool:
        recent = sum(1 for t in self.cut_times if now - t < 60.0)
        return recent < self.p.max_cuts_per_minute

    def _rank(self, now: float, cur: Camera, state: MusicState) -> list[tuple[Camera, float, dict]]:
        anchor = self.reg.anchor()
        out = []
        prev = self.history[-2].camera if len(self.history) >= 2 else None
        prev2 = self.history[-3].camera if len(self.history) >= 3 else None
        last_insert = next((s for s in reversed(self.history)
                            if self.reg.get(s.camera) is not None and self.reg.get(s.camera) is not anchor
                            and s.camera != cur.name), None)
        for c in self.reg.usable():
            if c.name == cur.name or now < self._failed_until.get(c.name, 0):
                continue
            if not c.allows(state.value):
                continue
            is_anchor = c is anchor
            if not is_anchor and now - c.last_used_end < c.cooldown:
                continue
            parts: dict[str, float] = {}
            if is_anchor:
                k = self.consecutive_inserts / max(1, self.p.max_consecutive_inserts)
                parts["editorial"] = 0.35 + 0.45 * min(1.0, k)
                if self.consecutive_inserts >= self.p.max_consecutive_inserts:
                    parts["forced_return"] = 2.0
            else:
                parts["editorial"] = 0.3 if self.reg.is_anchor(cur) else 0.0
            parts["musical"] = MUSICAL_FIT[c.role].get(state, 0.0)
            parts["visual"] = float(self.visual_fit(c))
            parts["preference"] = 0.25 * (c.weight - 1.0) + 0.02 * (c.priority - 5)
            parts["variety"] = 0.3 * min(1.0, (now - c.last_used_end) / 120.0) if not is_anchor else 0.0
            rep = 0.0
            if prev == c.name and prev2 == cur.name:
                rep += 0.6                      # A-B-A-B ping-pong
            if not is_anchor and last_insert is not None and last_insert.camera == c.name:
                # same insert again around the master; fades out after a minute
                since = now - (last_insert.end if last_insert.end is not None else now)
                rep += 0.25 * max(0.0, 1.0 - since / 60.0)
            parts["repetition"] = -rep
            parts["jitter"] = self.rng.uniform(0, self.p.variety_jitter)
            out.append((c, sum(parts.values()), parts))
        out.sort(key=lambda x: (-x[1], x[0].name))
        return out

    def _stay_value(self, cur: Camera, state: MusicState, tos: float, dur_p: float) -> float:
        v = MUSICAL_FIT[cur.role].get(state, 0.0) + float(self.visual_fit(cur))
        v -= 0.5 * max(0.0, dur_p - 0.5)       # the longer we overstay, the less staying is worth
        return v

    def _timing(self, now: float, snap: Optional[MusicSnapshot]) -> tuple[TimingMode, float]:
        if snap is None:
            return TimingMode.IMMEDIATE, now
        b = snap.beat
        if b.period <= 0 or b.confidence < self.p.sync_confidence:
            return TimingMode.IMMEDIATE, now
        earliest = now + self.s.latency_offset + 0.01
        limit = now + self.p.max_sync_wait
        if self.p.prefer_downbeat and b.downbeat_confidence >= self.p.downbeat_confidence:
            t = b.next_downbeat_after(earliest, self.s.beats_per_bar)
            if 0 < t <= limit:
                return TimingMode.DOWNBEAT, t
        t = b.next_beat_after(earliest)
        if 0 < t <= limit:
            return TimingMode.BEAT, t
        return TimingMode.IMMEDIATE, now

    def _switch_now(self, now: float, target: str, reason: str, confidence: float, opportunity: float,
                    state: Optional[MusicState], timing: TimingMode = TimingMode.IMMEDIATE,
                    target_t: float = 0.0, cid: int = 0, scores: tuple = ()) -> DirectorDecisionEvent:
        cid = cid or next_correlation_id()
        prev = self.current
        tos = now - self.shot_start
        self._start_shot(target, now, reason)
        self.cut_times.append(now)
        d = self._decision(now, DirectorAction.SWITCH, target, reason, confidence, state,
                           opportunity=opportunity, timing=timing, target_t=target_t or now, cid=cid,
                           scores=scores, current=prev, tos=tos)
        return d

    def _start_shot(self, scene: Optional[str], now: float, reason: str) -> None:
        if self.history and self.history[-1].end is None:
            self.history[-1].end = now
        old = self.reg.get(self.current)
        if old is not None:
            old.last_used_end = now
            old.airtime += now - self.shot_start
        self.current = scene
        self.shot_start = now
        cam = self.reg.get(scene)
        if cam is None:
            self.shot_target = 30.0
        else:
            cam.uses += 1
            self.shot_target = self._sample_target(cam)
            if self.reg.is_anchor(cam):
                self.consecutive_inserts = 0
            else:
                self.consecutive_inserts += 1
        if scene is not None:
            self.history.append(Shot(scene, now, None, reason))
            if len(self.history) > 500:
                del self.history[:100]
        self._triggers.clear()                 # an event is consumed by at most one cut

    def _decision(self, now: float, action: DirectorAction, candidate: Optional[str], reason: str,
                  confidence: float, state: Optional[MusicState], opportunity: float = 0.0,
                  timing: Optional[TimingMode] = None, target_t: float = 0.0, cid: int = 0,
                  scores: tuple = (), priority: int = 0, current: Optional[str] = None,
                  tos: Optional[float] = None) -> DirectorDecisionEvent:
        return DirectorDecisionEvent(
            t=now, action=action, current_camera=current if current is not None else self.current,
            candidate_camera=candidate, reason=reason, confidence=round(float(confidence), 3),
            time_on_shot=round(tos if tos is not None else now - self.shot_start, 3),
            music_state=state or MusicState.NO_SIGNAL, opportunity=round(opportunity, 3),
            timing=timing, target_t=target_t, priority=priority, correlation_id=cid, scores=scores)
