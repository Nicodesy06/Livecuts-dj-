"""Director engine rules, driven by synthetic music snapshots (no audio)."""

import math
import statistics

import pytest

from dj_director.config.presets import PRESETS
from dj_director.config.schema import CameraConfig, CameraRole
from dj_director.core.events import (BeatInfo, DirectorAction, EMPTY_BEAT, MusicEvent, MusicFeatures,
                                     MusicSnapshot, MusicState, TimingMode)
from dj_director.director.cameras import CameraRegistry
from dj_director.director.engine import DirectorEngine, EngineSettings

FEAT = MusicFeatures(-14, -16, -20, -25, 4, 0.1, 0, 0, 0, 1, 1500)
PERIOD = 60 / 124


def cams(n=3, **over):
    roles = [CameraRole.MASTER, CameraRole.DETAIL, CameraRole.SIDE, CameraRole.WIDE]
    names = ["M", "D", "S", "W"]
    out = [CameraConfig(names[i], role=roles[i], is_master=(i == 0)) for i in range(n)]
    for name, kw in over.items():
        for c in out:
            if c.scene_name == name:
                for k, v in kw.items():
                    setattr(c, k, v)
    return out


def snap(t, state=MusicState.STABLE_GROOVE, beat_conf=0.9, db_conf=0.0, events=()):
    if beat_conf > 0:
        k = math.floor(t / PERIOD)
        beat = BeatInfo(124, beat_conf, PERIOD, k * PERIOD, (k + 1) * PERIOD, k, k % 4 if db_conf else -1,
                        db_conf)
    else:
        beat = EMPTY_BEAT
    return MusicSnapshot(t, state, 0.8, 0.0, MusicState.STABLE_GROOVE, FEAT, beat, tuple(events), 0, True)


def make(n=3, preset="natural", seed=1, cams_over=None, **settings):
    reg = CameraRegistry.from_configs(cams(n, **(cams_over or {})))
    return DirectorEngine(reg, PRESETS[preset], EngineSettings(**settings), seed=seed)


def run(engine, seconds, state_fn=None, beat_conf=0.9, events_fn=None, dt=0.05, start=0.0):
    decisions = []
    t = start
    while t < start + seconds:
        st = state_fn(t) if state_fn else MusicState.STABLE_GROOVE
        evs = events_fn(t) if events_fn else ()
        d = engine.tick(t, snap(t, st, beat_conf), evs)
        decisions.append(d)
        t = round(t + dt, 6)
    return decisions


def switches(decisions):
    return [d for d in decisions if d.action == DirectorAction.SWITCH]


def test_initial_shot_is_master():
    e = make()
    d = e.tick(0.0, snap(0.0))
    assert d.action == DirectorAction.SWITCH and d.candidate_camera == "M" and "initial" in d.reason


def test_minimum_duration_always_respected():
    for preset in PRESETS:
        e = make(4, preset)
        ds = run(e, 900)
        prev = None
        for d in switches(ds):
            if prev is not None:
                cam = e.reg.get(prev.candidate_camera)
                scale = e.p.master_duration_scale if cam.is_master else e.p.insert_duration_scale
                assert d.t - prev.t >= cam.min_duration * scale - 1e-6
            prev = d


def test_hold_is_a_normal_decision_with_reasons():
    e = make()
    ds = run(e, 30)
    holds = [d for d in ds if d.action == DirectorAction.HOLD]
    assert len(holds) > len(ds) * 0.9
    assert all(d.reason for d in holds)


def test_not_a_fixed_timer():
    e = make(4)
    sw = switches(run(e, 1200))
    gaps = [b.t - a.t for a, b in zip(sw, sw[1:])]
    assert statistics.pstdev(gaps) / statistics.fmean(gaps) > 0.25


def test_master_dominant_but_not_exclusive():
    e = make(3)
    sw = switches(run(e, 1800))
    on = {}
    for a, b in zip(sw, sw[1:]):
        on[a.candidate_camera] = on.get(a.candidate_camera, 0) + b.t - a.t
    total = sum(on.values())
    assert 0.5 < on["M"] / total < 0.9
    assert on.get("D", 0) > 0 and on.get("S", 0) > 0


def test_no_abab_ping_pong():
    e = make(3, "dynamic")
    seq = [d.candidate_camera for d in switches(run(e, 1800))]
    inserts = [s for s in seq if s != "M"]
    abab = sum(1 for i in range(len(seq) - 3) if seq[i] == seq[i + 2] and seq[i + 1] == seq[i + 3]
               and seq[i] != "M" and seq[i + 1] != "M")
    assert abab == 0


def test_cooldown_respected():
    e = make(3, "dynamic", cams_over={"D": {"cooldown": 60.0}})
    sw = switches(run(e, 1200))
    d_times = [(d.t) for d in sw if d.candidate_camera == "D"]
    # end of each D shot -> next D start must be >= 60 s
    ends = {}
    for a, b in zip(sw, sw[1:]):
        if a.candidate_camera == "D":
            ends[a.t] = b.t
    starts = sorted(d_times)
    for s0, s1 in zip(starts, starts[1:]):
        assert s1 - ends[s0] >= 60 - 1e-6


def test_disabled_camera_never_used():
    e = make(3, cams_over={"D": {"enabled": False}})
    assert all(d.candidate_camera != "D" for d in switches(run(e, 900)))


def test_single_camera_never_switches_after_initial():
    e = make(1)
    ds = run(e, 300)
    assert len(switches(ds)) == 1
    assert any("only one usable camera" in d.reason for d in ds)


def test_current_camera_disappears_returns_to_anchor():
    e = make(3)
    run(e, 60)
    while e.current == "M":
        run(e, 5, start=e.history[-1].start + 1000)
    lost = e.current
    e.reg.set_available([c for c in ("M", "D", "S") if c != lost])
    d = e.tick(5000.0, snap(5000.0))
    assert d.action == DirectorAction.SWITCH and d.candidate_camera == "M" and "unavailable" in d.reason


def test_pending_switch_cancelled_when_target_missing():
    e = make(3)
    e.tick(0, snap(0))
    t = 0.05
    while e.pending is None and t < 600:
        e.tick(t, snap(t))
        t += 0.05
    assert e.pending is not None
    target = e.pending.target
    e.reg.set_available([c for c in ("M", "D", "S") if c != target])
    d = e.tick(t, snap(t))
    assert d.action == DirectorAction.CANCEL and "unavailable" in d.reason


def test_confident_beat_schedules_on_beat_time():
    e = make(3)
    ds = run(e, 600)
    sched = [d for d in ds if d.action == DirectorAction.SCHEDULE]
    assert sched
    for d in sched:
        assert d.timing in (TimingMode.BEAT, TimingMode.DOWNBEAT)
        k = d.target_t / PERIOD
        assert abs(k - round(k)) < 1e-6          # lands exactly on the predicted beat grid
    for d in switches(ds)[1:]:
        assert d.timing in (TimingMode.BEAT, TimingMode.DOWNBEAT)


def test_uncertain_beat_avoids_false_precision():
    e = make(3)
    ds = run(e, 600, beat_conf=0.1)
    assert not [d for d in ds if d.action == DirectorAction.SCHEDULE]
    assert all(d.timing == TimingMode.IMMEDIATE for d in switches(ds))


def test_reentry_event_triggers_cut_when_allowed():
    e = make(4)
    e.tick(0, snap(0))
    run(e, 25, start=0.05)                     # master on air for 25 s (> min, < planned)
    assert e.current == "M"
    ev = MusicEvent(25.0, "reentry", 0.9)
    ds = run(e, 3, state_fn=lambda t: MusicState.REENTRY, events_fn=lambda t: (ev,) if t == 25.0 else (),
             start=25.0)
    acts = [d for d in ds if d.action in (DirectorAction.SWITCH, DirectorAction.SCHEDULE)]
    assert acts and "reentry" in acts[0].reason


def test_low_confidence_event_is_ignored():
    e = make(4)
    e.tick(0, snap(0))
    run(e, 12, start=0.05)
    ev = MusicEvent(12.0, "reentry", 0.2)
    ds = run(e, 2, events_fn=lambda t: (ev,) if t == 12.0 else (), start=12.0)
    assert not [d for d in ds if d.action in (DirectorAction.SWITCH, DirectorAction.SCHEDULE)]


def test_presets_change_pace():
    rate = {}
    for preset in PRESETS:
        cuts = [len(switches(run(make(4, preset, seed=s), 1200))) for s in (1, 2, 3)]
        rate[preset] = sum(cuts)
    assert rate["minimal"] < rate["natural"] < rate["dynamic"]


def test_cuts_per_minute_cap():
    for preset, p in PRESETS.items():
        sw = switches(run(make(4, preset), 900))
        for i, d in enumerate(sw):
            window = [x for x in sw[: i + 1] if d.t - x.t < 60]
            assert len(window) <= p.max_cuts_per_minute + 1   # +1: the cut itself


def test_max_consecutive_inserts():
    e = make(4, "natural")
    seq = [d.candidate_camera for d in switches(run(e, 1800))]
    run_len = best = 0
    for s in seq:
        run_len = 0 if s == "M" else run_len + 1
        best = max(best, run_len)
    assert best <= PRESETS["natural"].max_consecutive_inserts


def test_deterministic_with_same_seed():
    a = [(d.t, d.candidate_camera) for d in switches(run(make(4, seed=42), 900))]
    b = [(d.t, d.candidate_camera) for d in switches(run(make(4, seed=42), 900))]
    c = [(d.t, d.candidate_camera) for d in switches(run(make(4, seed=43), 900))]
    assert a == b and a != c


def test_manual_override_timed():
    e = make(3, override_policy="timed", override_seconds=30)
    run(e, 20)
    d = e.on_external_change("S", 20.0)
    assert d.action == DirectorAction.OVERRIDE and e.current == "S"
    ds = run(e, 29.9, start=20.05)
    assert not switches(ds)
    assert any("manual override" in x.reason for x in ds)
    ds = run(e, 200, start=50.0)
    assert switches(ds)                        # resumes afterwards


def test_manual_override_until_resume():
    e = make(3, override_policy="until_resume")
    run(e, 10)
    e.on_external_change("D", 10.0)
    assert not switches(run(e, 400, start=10.05))
    e.clear_override()
    assert switches(run(e, 200, start=410.0))


def test_manual_change_to_unmanaged_scene_pauses_regardless_of_policy():
    e = make(3, override_policy="timed", override_seconds=5)
    run(e, 10)
    d = e.on_external_change("Be Right Back", 10.0)
    assert "unmanaged" in d.reason
    assert not switches(run(e, 300, start=10.05))


def test_manual_override_adopt_respects_min_length_of_new_shot():
    e = make(3, override_policy="adopt")
    run(e, 10)
    e.on_external_change("D", 10.0)
    ds = run(e, 4, start=10.05)
    assert not switches(ds)


def test_no_signal_returns_to_master_and_holds():
    e = make(3)
    run(e, 0.1)
    e.force("D", 1.0, "test")
    ds = run(e, 30, state_fn=lambda t: MusicState.NO_SIGNAL, beat_conf=0, start=1.05)
    sw = switches(ds)
    assert len(sw) == 1 and sw[0].candidate_camera == "M" and "no audio" in sw[0].reason
    assert e.current == "M"


def test_no_signal_hold_policy():
    e = make(3, no_signal_policy="hold")
    run(e, 0.1)
    e.force("D", 1.0, "test")
    assert not switches(run(e, 30, state_fn=lambda t: MusicState.NO_SIGNAL, beat_conf=0, start=1.05))


def test_switch_failure_reverts_state():
    e = make(3)
    e.tick(0, snap(0))
    e.force("D", 20.0, "x")
    e.on_switch_failed("D", "M", 20.1)
    assert e.current == "M"
    # the failed camera is avoided for a while
    d = e.tick(20.2, snap(20.2))
    assert d.candidate_camera != "D" or d.action == DirectorAction.HOLD


def test_reasons_reflect_actual_logic():
    e = make(3)
    for d in switches(run(e, 900))[1:]:
        assert "min length satisfied" in d.reason
