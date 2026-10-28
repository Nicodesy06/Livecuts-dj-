"""DJ-set editing grammar by musical context (0.4 Director 2.0, P2)."""

import dataclasses

from dj_director.config.presets import PRESETS
from dj_director.config.schema import CameraConfig, CameraRole, ShotSize, Subject, ViewAngle
from dj_director.core.events import MusicEvent, MusicState, TimingMode
from dj_director.director.cameras import CameraRegistry
from dj_director.director.engine import DirectorEngine, EngineSettings
from tests.unit.test_engine import PERIOD, make, run, snap, switches


def engine(params=None, cfgs=None, seed=1):
    cfgs = cfgs or [CameraConfig("M", role=CameraRole.MASTER, is_master=True),
                    CameraConfig("D", role=CameraRole.DETAIL),
                    CameraConfig("S", role=CameraRole.SIDE),
                    CameraConfig("C", role=CameraRole.CROWD)]
    p = dataclasses.replace(PRESETS["natural"], **(params or {}))
    return DirectorEngine(CameraRegistry.from_configs(cfgs), p, EngineSettings(), seed=seed)


def test_stable_groove_master_dominant_inserts_without_waiting_for_a_drop():
    e = engine()
    ds = run(e, 900)
    sw = switches(ds)
    entries = [d for d in sw if d.reason.startswith("entry: editorial")]
    assert entries, "inserts happen in a stable groove without any drop"
    gaps = [b.t - a.t for a, b in zip(entries, entries[1:])]
    assert len({round(g) for g in gaps}) > 1                       # regular but not periodic
    assert all(d.timing in (TimingMode.PHRASE, TimingMode.DOWNBEAT, TimingMode.BEAT) for d in entries)


def test_breakdown_has_fewer_unmotivated_cuts():
    sparse = lambda t: MusicState.SPARSE
    with_grammar = len(switches(run(engine(), 1200, state_fn=sparse)))
    without = len(switches(run(engine({"contextual_grammar": False}), 1200, state_fn=sparse)))
    assert with_grammar <= 0.85 * without


def test_breakdown_prefers_atmosphere_and_performer_shots():
    cfgs = [CameraConfig("M", role=CameraRole.MASTER, is_master=True),
            CameraConfig("D", role=CameraRole.DETAIL, shot_size=ShotSize.CLOSE, subject=Subject.HANDS),
            CameraConfig("B", role=CameraRole.BEHIND, shot_size=ShotSize.WIDE, subject=Subject.ENVIRONMENT)]
    ds = run(engine(cfgs=cfgs), 1200, state_fn=lambda t: MusicState.SPARSE)
    seq = [d.candidate_camera for d in switches(ds) if d.reason.startswith("entry")]
    assert seq and seq.count("B") > seq.count("D")


def test_drop_prefers_crowd_or_wide_and_never_a_detail():
    e = engine()
    assert e.reveal_camera().name == "C"                           # CROWD before SIDE
    e.tick(0, snap(0))
    run(e, 14, start=0.05)
    ev = MusicEvent(14.0, "reentry", 0.9)
    sw = switches(run(e, 3, state_fn=lambda t: MusicState.REENTRY,
                      events_fn=lambda t: (ev,) if t == 14.0 else (), start=14.0))
    assert sw and sw[0].candidate_camera == "C"
    assert all(d.candidate_camera != "D" for d in sw)


def test_drop_cut_is_never_more_than_half_a_beat_late():
    e = engine()
    e.tick(0, snap(0))
    run(e, 14, start=0.05)
    t_ev = 14.0
    ev = MusicEvent(t_ev, "reentry", 0.9)
    ds = run(e, 2, state_fn=lambda t: MusicState.REENTRY, events_fn=lambda t: (ev,) if t == t_ev else (),
             start=t_ev)
    sw = switches(ds)
    assert sw and sw[0].t - t_ev <= 0.5 * PERIOD + 0.06


def test_build_up_hold_back_only_with_a_reliable_beat():
    reliable = run(engine(), 900, state_fn=lambda t: MusicState.INCREASING)
    shaky = run(engine(), 900, state_fn=lambda t: MusicState.INCREASING, beat_conf=0.2)
    assert all(d.candidate_camera != "C" for d in switches(reliable) if d.reason.startswith("entry"))
    assert any(d.candidate_camera == "C" for d in switches(shaky) if d.reason.startswith("entry"))


def test_uncertain_music_ignores_weak_events():
    e = engine()
    e.tick(0, snap(0))
    run(e, 14, start=0.05)
    weak = MusicEvent(14.0, "transition", 0.6)
    ds = run(e, 3, state_fn=lambda t: MusicState.UNCERTAIN, events_fn=lambda t: (weak,) if t == 14.0 else (),
             start=14.0)
    assert switches(ds) == []


def test_continuous_hands_do_not_extend_a_detail_beyond_its_maximum():
    e = make(3, visual=lambda cam: 0.9 if cam.name == "D" else 0.1)
    ds = run(e, 1800, state_fn=lambda t: MusicState.TRANSITION)
    sw = switches(ds)
    durs = [(a.candidate_camera, b.t - a.t) for a, b in zip(sw, sw[1:])]
    assert all(d <= e.p.detail_hard_cap + 0.05 for c, d in durs if c == "D")
