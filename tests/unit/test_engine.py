"""Director engine (0.1.3 editorial model), driven by synthetic music snapshots (no audio)."""

import dataclasses
import math
import statistics

import pytest

from dj_director.config.presets import PRESETS
from dj_director.config.schema import CameraConfig, CameraRole
from dj_director.core.events import (BeatInfo, DirectorAction, EMPTY_BEAT, MusicEvent, MusicFeatures,
                                     MusicSnapshot, MusicState, TimingMode)
from dj_director.director.cameras import CameraRegistry
from dj_director.director.engine import DirectorEngine, EngineSettings, VisualNovelty

FEAT = MusicFeatures(-14, -16, -20, -25, 4, 0.1, 0, 0, 0, 1, 1500)
PERIOD = 60 / 124
NAMES = {"M": CameraRole.MASTER, "D": CameraRole.DETAIL, "S": CameraRole.SIDE, "B": CameraRole.BEHIND}


def cams(n=3, **over):
    out = [CameraConfig(name, role=role, is_master=(name == "M")) for name, role in list(NAMES.items())[:n]]
    for name, kw in over.items():
        for c in out:
            if c.scene_name == name:
                for k, v in kw.items():
                    setattr(c, k, v)
    return out


def snap(t, state=MusicState.STABLE_GROOVE, beat_conf=0.9, db_conf=0.0, since=0.0):
    if beat_conf > 0:
        k = math.floor(t / PERIOD)
        beat = BeatInfo(124, beat_conf, PERIOD, k * PERIOD, (k + 1) * PERIOD, k, k % 4 if db_conf else -1,
                        db_conf)
    else:
        beat = EMPTY_BEAT
    return MusicSnapshot(t, state, 0.8, since, MusicState.STABLE_GROOVE, FEAT, beat, (), 0, True)


def make(n=3, preset="natural", seed=1, cams_over=None, visual=None, issue=None, hint=None, params=None,
         **settings):
    reg = CameraRegistry.from_configs(cams(n, **(cams_over or {})))
    p = dataclasses.replace(PRESETS[preset], **(params or {}))
    return DirectorEngine(reg, p, EngineSettings(**settings), seed=seed, visual_activity=visual,
                          visual_issue=issue, reveal_hint=hint)


def run(engine, seconds, state_fn=None, beat_conf=0.9, events_fn=None, dt=0.05, start=0.0):
    decisions = []
    t = start
    while t < start + seconds:
        st = state_fn(t) if state_fn else MusicState.STABLE_GROOVE
        evs = events_fn(t) if events_fn else ()
        decisions.append(engine.tick(t, snap(t, st, beat_conf), evs))
        t = round(t + dt, 6)
    return decisions


def switches(decisions):
    return [d for d in decisions if d.action == DirectorAction.SWITCH]


def shots(decisions, end):
    sw = switches(decisions)
    return [(a.candidate_camera, b.t - a.t) for a, b in zip(sw, sw[1:])] + \
        ([(sw[-1].candidate_camera, end - sw[-1].t)] if sw else [])


def coverage_of(decisions, end, engine):
    acc = {}
    for cam, dur in shots(decisions, end):
        cls = engine.class_of(engine.reg.get(cam))
        acc[cls] = acc.get(cls, 0.0) + dur
    tot = sum(acc.values())
    return {k: v / tot for k, v in acc.items()}


# ----------------------------------------------------------------------------- basics
def test_initial_shot_is_master():
    d = make().tick(0.0, snap(0.0))
    assert d.action == DirectorAction.SWITCH and d.candidate_camera == "M" and "initial" in d.reason


def test_minimum_lengths_respected():
    for preset in PRESETS:
        e = make(4, preset)
        ds = run(e, 900)
        for cam, dur in shots(ds, 900)[:-1]:
            assert dur >= e.spec(e.reg.get(cam)).min - 1e-6, (preset, cam, dur)


def test_hold_is_a_normal_decision_with_reasons():
    ds = run(make(), 30)
    holds = [d for d in ds if d.action == DirectorAction.HOLD]
    assert len(holds) > len(ds) * 0.9 and all(d.reason for d in holds)


# ----------------------------------------------------------------------------- duration grammar
def test_duration_grammar_detail_side_master():
    e = make(4)
    ds = run(e, 2400)
    by = {}
    for cam, dur in shots(ds, 2400)[1:-1]:
        by.setdefault(e.class_of(e.reg.get(cam)), []).append(dur)
    det, side, mas = by["detail"], by["side"], by["master"]
    assert max(det) <= e.spec(e.reg.get("D")).cap and max(side) <= e.spec(e.reg.get("S")).cap
    assert 6.0 <= statistics.median(det) <= 10.5
    assert 10.0 <= statistics.median(side) <= 18.5
    assert 20.0 <= statistics.median(mas) <= 50.0


def test_hard_caps_configurable_and_clamped():
    e = make(3, cams_over={"D": {"hard_cap": 40.0}, "S": {"hard_cap": 5.0}})
    assert e.spec(e.reg.get("D")).cap == 15.0            # clamped into 12-15
    assert e.spec(e.reg.get("S")).cap == 20.0            # clamped into 20-25
    e.p = dataclasses.replace(e.p, detail_hard_cap=12.0)
    assert make(3).spec(make(3).reg.get("D")).cap == 14.0
    e2 = make(3)
    e2.p = dataclasses.replace(e2.p, detail_hard_cap=12.0)
    assert e2.spec(e2.reg.get("D")).cap == 12.0
    assert e2.spec(e2.reg.get("D")).pref_max <= 11.0


def test_hard_cap_never_exceeded_even_with_continuity():
    # constant novelty spikes on the detail camera + transition state: continuity is maximal
    e = make(3, visual=lambda cam: 1.0 if cam.name == "D" else 0.0)
    ds = run(e, 1800, state_fn=lambda t: MusicState.TRANSITION)
    for cam, dur in shots(ds, 1800)[:-1]:
        sp = e.spec(e.reg.get(cam))
        assert dur <= sp.cap + 1e-6
        if sp.cls != "master":
            assert dur <= sp.pref_max + PERIOD * 4 + 0.3      # continuity stops at the preferred max


def test_master_has_no_rigid_maximum():
    e = make(2, cams_over={"D": {"enabled": False}})
    ds = run(e, 400)
    assert len(switches(ds)) == 1 and e.current == "M"


# ----------------------------------------------------------------------------- coverage budget
def test_coverage_budget_converges_without_chasing():
    e = make(4)
    ds = run(e, 3600)
    cov = coverage_of(ds, 3600, e)
    assert abs(cov["master"] - 0.70) <= 0.05, cov
    assert abs(cov["side"] - 0.15) <= 0.05, cov
    assert cov["detail"] >= 0.08, cov
    # soft: never more than the cut-rate cap, never an editorial entry before the preferred dwell
    sw = switches(ds)
    for i, d in enumerate(sw):
        assert sum(1 for x in sw[: i + 1] if d.t - x.t < 60) <= PRESETS["natural"].max_cuts_per_minute + 1
        if d.reason.startswith("entry: editorial"):
            assert d.time_on_shot >= e.spec(e.reg.get("M")).pref_min - 1e-6


def test_coverage_targets_redistribute_without_detail_camera():
    e = make(4, cams_over={"D": {"enabled": False}})
    t = e.coverage_targets()
    assert set(t) == {"master", "side"} and abs(t["master"] - 0.70 / 0.85) < 1e-9


def test_coverage_window_slides():
    e = make(3)
    run(e, 1200)
    cov = e.coverage(1200.0, prior=False)
    assert abs(sum(cov.values()) - 1.0) < 1e-6
    e.p = dataclasses.replace(e.p, coverage_window=60.0)
    short = e.coverage(1200.0, prior=False)
    assert short != cov                    # only the last minute counts now


# ----------------------------------------------------------------------------- entry / exit
def test_entries_and_exits_are_separate_decisions():
    e = make(4)
    ds = run(e, 1800)
    for d in switches(ds)[1:]:
        if d.current_camera == "M":
            assert d.reason.startswith("entry:"), d.reason
        else:
            assert d.reason.startswith("exit:"), d.reason


def test_entries_land_on_metrical_boundaries_when_beat_is_reliable():
    e = make(3)
    ds = run(e, 1200)
    entries = [d for d in ds if d.action in (DirectorAction.SCHEDULE, DirectorAction.SWITCH)
               and d.current_camera == "M" and d.reason.startswith("entry:")]
    assert entries
    for d in entries:
        assert d.timing in (TimingMode.PHRASE, TimingMode.DOWNBEAT, TimingMode.BEAT)
        k = d.target_t / PERIOD
        assert abs(k - round(k)) < 1e-6


def test_unreliable_beat_avoids_false_precision():
    ds = run(make(3), 900, beat_conf=0.1)
    assert not [d for d in ds if d.action == DirectorAction.SCHEDULE]
    assert all(d.timing == TimingMode.IMMEDIATE for d in switches(ds))


def test_stable_music_keeps_editorial_cuts_without_repetitive_intervals():
    e = make(4)
    ds = run(e, 1200)
    sw = switches(ds)
    assert len(sw) >= 30                                   # an active direction
    entries = [d for d in sw if d.reason.startswith("entry:")]
    assert entries and all("editorial" in d.reason for d in entries)
    gaps = [b.t - a.t for a, b in zip(sw, sw[1:])]
    assert statistics.pstdev(gaps) / statistics.fmean(gaps) > 0.25
    repeated = sum(1 for g1, g2 in zip(gaps, gaps[1:]) if abs(g1 - g2) < 0.5)
    assert repeated <= len(gaps) * 0.1


def test_editorial_cuts_can_be_disabled():
    e = make(3)
    e.p = dataclasses.replace(e.p, editorial_cuts=False)
    assert len(switches(run(e, 600))) == 1


def test_no_abab_ping_pong():
    for n in (3, 4):
        seq = [d.candidate_camera for d in switches(run(make(n, "dynamic"), 1800))]
        assert not any(seq[i] == seq[i + 2] and seq[i + 1] == seq[i + 3] for i in range(len(seq) - 3)), seq


def test_cooldown_respected():
    e = make(3, cams_over={"D": {"cooldown": 90.0}})
    sw = switches(run(e, 1800))
    ends = {}
    for a, b in zip(sw, sw[1:]):
        if a.candidate_camera == "D":
            ends[a.t] = b.t
    starts = sorted(ends)
    for s0, s1 in zip(starts, starts[1:]):
        assert s1 - ends[s0] >= 90 - 1e-6


def test_disabled_camera_never_used():
    assert all(d.candidate_camera != "D" for d in switches(run(make(3, cams_over={"D": {"enabled": False}}), 900)))


def test_single_camera_never_switches_after_initial():
    e = make(1)
    ds = run(e, 300)
    assert len(switches(ds)) == 1 and any("only one usable camera" in d.reason for d in ds)


def test_max_consecutive_inserts():
    e = make(4, "natural")
    seq = [d.candidate_camera for d in switches(run(e, 1800))]
    run_len = best = 0
    for s in seq:
        run_len = 0 if s == "M" else run_len + 1
        best = max(best, run_len)
    assert best <= PRESETS["natural"].max_consecutive_inserts


def test_presets_change_pace():
    rate = {p: len(switches(run(make(4, p), 1500))) for p in PRESETS}
    assert rate["minimal"] < rate["natural"] < rate["dynamic"]


# ----------------------------------------------------------------------------- musical opportunities
def test_reentry_triggers_entry_after_master_minimum():
    e = make(4, params={"drop_reveal": False})
    e.tick(0, snap(0))
    run(e, 14, start=0.05)                     # past the master minimum (12 s), before the preferred dwell
    assert e.current == "M"
    ev = MusicEvent(14.0, "reentry", 0.9)
    ds = run(e, 3, state_fn=lambda t: MusicState.REENTRY, events_fn=lambda t: (ev,) if t == 14.0 else (),
             start=14.0)
    acts = [d for d in ds if d.action in (DirectorAction.SWITCH, DirectorAction.SCHEDULE)]
    assert acts and acts[0].reason.startswith("entry: reentry")


def test_low_confidence_event_is_ignored():
    e = make(4)
    e.tick(0, snap(0))
    run(e, 14, start=0.05)
    ev = MusicEvent(14.0, "reentry", 0.2)
    ds = run(e, 2, events_fn=lambda t: (ev,) if t == 14.0 else (), start=14.0)
    assert not [d for d in ds if d.action in (DirectorAction.SWITCH, DirectorAction.SCHEDULE)]


def test_drop_returns_from_detail_to_master_early():
    e = make(3, params={"drop_reveal": False})
    e.tick(0, snap(0))
    e.force("D", 1.0, "test")
    run(e, 5.5, start=1.05)                    # detail minimum is 5 s
    assert e.current == "D"
    ev = MusicEvent(6.6, "reentry", 0.9)
    ds = run(e, 2, state_fn=lambda t: MusicState.REENTRY, events_fn=lambda t: (ev,) if t == 6.6 else (),
             start=6.6)
    sw = switches(ds)
    assert sw and sw[0].candidate_camera == "M" and sw[0].reason.startswith("exit: reentry")


# ----------------------------------------------------------------------------- drop grammar (0.2)
def _drop_on_master(e, t_drop=14.0, conf=0.9):
    e.tick(0, snap(0))
    run(e, t_drop, start=0.05)
    ev = MusicEvent(t_drop, "reentry", conf)
    return run(e, 3, state_fn=lambda t: MusicState.REENTRY, events_fn=lambda t: (ev,) if t == t_drop else (),
               start=t_drop)


def test_drop_opens_on_the_reveal_shot_on_the_beat():
    e = make(4)                                # no WIDE: automatic reveal = BEHIND
    assert e.reveal_camera().name == "B"
    ds = _drop_on_master(e)
    sw = switches(ds)
    assert sw and sw[0].candidate_camera == "B" and sw[0].reason.startswith("entry: drop reveal on B")
    # on the next beat when it is close, at once otherwise: a drop cut is never late
    assert sw[0].timing in (TimingMode.BEAT, TimingMode.DOWNBEAT, TimingMode.IMMEDIATE)
    assert sw[0].t - 14.0 <= 0.5 * PERIOD + 0.06


def test_drop_reveal_uses_the_chosen_scene():
    e = make(4, drop_scene="S")
    sw = switches(_drop_on_master(e))
    assert sw and sw[0].candidate_camera == "S"


def test_drop_reveal_follows_the_slow_eye_hint_when_no_scene_is_chosen():
    e = make(4, hint=lambda: "S")
    assert e.reveal_camera().name == "S"
    e2 = make(4, hint=lambda: "S", drop_scene="B")
    assert e2.reveal_camera().name == "B"           # an explicit choice wins


def test_drop_from_a_detail_insert_opens_on_the_reveal():
    e = make(4)
    e.tick(0, snap(0))
    e.force("D", 1.0, "test")
    run(e, 5.2, start=1.05)                    # past the detail minimum (5 s): a hard rule, also for drops
    ev = MusicEvent(6.3, "reentry", 0.9)
    ds = run(e, 2, state_fn=lambda t: MusicState.REENTRY, events_fn=lambda t: (ev,) if t == 6.3 else (),
             start=6.3)
    sw = switches(ds)
    assert sw and sw[0].candidate_camera == "B" and sw[0].reason.startswith("exit: drop reveal on B")


def test_drop_reveal_never_completes_a_ping_pong():
    e = make(3)                                # M, D, S: the automatic reveal is S
    e.tick(0, snap(0))
    e.force("S", 1.0, "test")
    e.force("M", 12.0, "test")                 # history ... S, M: S again now would give M-S-M-S
    run(e, 14, start=12.05)
    ev = MusicEvent(26.1, "reentry", 0.9)
    ds = run(e, 3, state_fn=lambda t: MusicState.REENTRY, events_fn=lambda t: (ev,) if t == 26.1 else (),
             start=26.1)
    sw = switches(ds)
    assert sw and sw[0].candidate_camera != "S"


def test_reveal_shot_is_held_back_during_the_build_up():
    e = make(4)
    ds = run(e, 900, state_fn=lambda t: MusicState.INCREASING)
    entries = [d for d in switches(ds) if d.reason.startswith("entry")]
    assert entries and all(d.candidate_camera != "B" for d in entries)


# ----------------------------------------------------------------------------- DJ gestures (0.3)
def run_g(engine, seconds, gesture_fn, events_fn=None, start=0.0, dt=0.05):
    """Like run(), with the snapshot's mix_gesture driven by gesture_fn(t)."""
    out, t = [], start
    while t < start + seconds:
        evs = events_fn(t) if events_fn else ()
        sn = dataclasses.replace(snap(t), mix_gesture=gesture_fn(t))
        out.append(engine.tick(t, sn, evs))
        t = round(t + dt, 6)
    return out


HANDS = lambda cam: 0.6 if cam.name == "D" else 0.0          # the mixer shot shows the hands moving


def _cut_on_master(e, t_cut=14.0, detail="low: -30 dB"):
    e.tick(0, snap(0))
    run(e, t_cut, start=0.05)
    assert e.current == "M"
    return MusicEvent(t_cut, "eq_cut", 0.85, detail)


def test_dj_cutting_the_bass_cuts_to_the_mixer_on_the_beat():
    e = make(4, visual=HANDS)                  # picture confirms the hands are working
    ev = _cut_on_master(e)
    ds = run_g(e, 3, lambda t: "eq_cut:low", lambda t: (ev,) if t == 14.0 else (), start=14.0)
    sw = switches(ds)
    assert sw and sw[0].candidate_camera == "D" and sw[0].reason.startswith("entry: DJ cuts the lows")
    assert sw[0].timing in (TimingMode.BEAT, TimingMode.DOWNBEAT)
    assert dict(sw[0].plan)["why"].startswith("entry: DJ cuts the lows")


def test_audio_gesture_alone_does_not_cut_a_fresh_master():
    e = make(4)                                # no picture: the spectral event alone is not proof
    ev = _cut_on_master(e)
    ds = run_g(e, 3, lambda t: "eq_cut:low", lambda t: (ev,) if t == 14.0 else (), start=14.0)
    assert switches(ds) == []


def test_audio_gesture_raises_the_mixer_shot_once_the_master_has_had_its_time():
    e = make(4)
    ev = _cut_on_master(e, t_cut=29.0)         # heard just before the master's dwell is complete
    ds = run_g(e, 5, lambda t: "eq_cut:low", lambda t: (ev,) if t == 29.0 else (), start=29.0)
    sw = switches(ds)
    assert sw and sw[0].candidate_camera == "D" and sw[0].reason.startswith("entry: DJ cuts the lows")


def test_mixer_shot_is_held_while_the_gesture_lasts_never_beyond_the_ordinary_detail_max():
    e = make(4, visual=HANDS)
    ev = _cut_on_master(e)
    ds = run_g(e, 40, lambda t: "eq_cut:low", lambda t: (ev,) if t == 14.0 else (), start=14.0)
    sw = switches(ds)
    assert sw[0].candidate_camera == "D"
    held = sw[1].t - sw[0].t
    assert 10.0 <= held <= e.p.detail_hard_cap + 0.05, held     # continuous gesture: still <= 14 s


def test_mixer_shot_cap_can_be_raised_to_15_20s():
    e = make(4, visual=HANDS, params={"mix_shot_cap": 20.0})
    ev = _cut_on_master(e)
    ds = run_g(e, 40, lambda t: "eq_cut:low", lambda t: (ev,) if t == 14.0 else (), start=14.0)
    sw = switches(ds)
    held = sw[1].t - sw[0].t
    assert 14.0 <= held <= 20.0 + 0.05, held


def test_bass_coming_back_opens_the_reveal_shot():
    e = make(4, visual=HANDS)
    ev = _cut_on_master(e)
    back = MusicEvent(24.0, "eq_return", 0.95, "bass back in 0.2 s")
    ds = run_g(e, 12, lambda t: "eq_cut:low" if t < 24.0 else "",
               lambda t: (ev,) if t == 14.0 else (back,) if t == 24.0 else (), start=14.0)
    sw = switches(ds)
    assert [d.candidate_camera for d in sw[:2]] == ["D", "B"]
    assert sw[1].reason.startswith("exit: drop reveal on B")


def test_mixer_shot_ends_soon_after_the_dj_lets_go():
    e = make(4, visual=HANDS)
    ev = _cut_on_master(e)
    ds = run_g(e, 20, lambda t: "eq_cut:low" if t < 20.0 else "", lambda t: (ev,) if t == 14.0 else (),
               start=14.0)
    sw = switches(ds)
    assert sw[0].candidate_camera == "D" and len(sw) >= 2
    assert sw[1].t - sw[0].t < 12.0                      # released ~2 s after the gesture, not at 15-20 s


def test_gesture_during_another_insert_moves_to_the_mixer():
    e = make(4)
    e.tick(0, snap(0))
    e.force("S", 1.0, "test")
    run(e, 8.0, start=1.05)                              # past the side minimum (7 s)
    ev = MusicEvent(9.1, "filter", 0.8, "high-pass: -25 dB")
    ds = run_g(e, 2, lambda t: "filter:low", lambda t: (ev,) if t == 9.1 else (), start=9.1)
    sw = switches(ds)
    assert sw and sw[0].candidate_camera == "D" and "high-pass filter" in sw[0].reason


def test_mixer_camera_can_be_chosen_and_gestures_switched_off():
    e = make(4, mixer_scene="B")
    assert e.mixer_camera().name == "B"
    e2 = make(4, params={"mix_gestures": False})
    ev = _cut_on_master(e2)
    ds = run_g(e2, 3, lambda t: "eq_cut:low", lambda t: (ev,) if t == 14.0 else (), start=14.0)
    assert not any("DJ" in d.reason for d in ds)


# ----------------------------------------------------------------------------- visual veto (0.2)
def test_vetoed_camera_is_never_chosen():
    e = make(4, issue=lambda cam: "black frame" if cam.name == "S" else "")
    ds = run(e, 1200)
    assert all(d.candidate_camera != "S" for d in switches(ds))
    assert len(switches(ds)) > 10


def test_vetoed_shot_on_air_is_left_quickly():
    bad = {"v": ""}
    e = make(3, issue=lambda cam: bad["v"] if cam.name == "S" else "")
    e.tick(0, snap(0))
    e.force("S", 1.0, "test")
    run(e, 2.0, start=1.05)
    bad["v"] = "frozen image"
    ds = run(e, 3.0, start=3.05)
    sw = switches(ds)
    assert sw and sw[0].candidate_camera == "M" and "shot unusable (frozen image)" in sw[0].reason
    assert sw[0].t - 3.05 <= 1.5


def test_unusable_master_is_avoided_until_it_recovers():
    bad = {"v": "blurred or blocked"}
    e = make(4, issue=lambda cam: bad["v"] if cam.name == "M" else "")
    e.tick(0, snap(0))
    ds = run(e, 60, start=0.05)
    sw = switches(ds)
    assert sw and sw[0].reason.startswith("entry: master shot unusable")
    assert all(d.candidate_camera != "M" for d in sw)
    bad["v"] = ""
    ds = run(e, 60, start=60.05)
    assert any(d.candidate_camera == "M" for d in switches(ds))


# ----------------------------------------------------------------------------- visual novelty
def test_continuous_hand_presence_is_not_a_trigger():
    e = make(3, visual=lambda cam: 0.85 if cam.name == "D" else 0.1)
    ds = run(e, 1200)
    assert not [d for d in ds if "visual novelty" in d.reason]


def test_activity_rise_above_baseline_triggers_detail_entry():
    level = {"v": 0.15}
    e = make(3, visual=lambda cam: level["v"] if cam.name == "D" else 0.1)
    e.tick(0, snap(0))
    run(e, 15, start=0.05)                     # master past its minimum; baseline learned at 0.15
    level["v"] = 0.9                           # the DJ starts working the mixer
    ds = run(e, 4, start=15.0)
    acts = [d for d in ds if d.action in (DirectorAction.SWITCH, DirectorAction.SCHEDULE)]
    assert acts and acts[0].candidate_camera == "D" and "visual novelty on D" in acts[0].reason


def test_presence_habituates_and_cannot_extend_detail():
    n = VisualNovelty()
    n.update(0.0, 0.1)
    vals = [n.update(t / 20, 0.9) for t in range(1, 20 * 20)]
    assert vals[0] > 0.9 and max(vals[-100:]) == 0.0          # zero within seconds of steady activity
    e = make(3, visual=lambda cam: 0.9 if cam.name == "D" else 0.1)
    ds = run(e, 1800)
    for cam, dur in shots(ds, 1800)[:-1]:
        if cam == "D":
            assert dur <= e.spec(e.reg.get("D")).pref_max + PERIOD * 4 + 0.3


# ----------------------------------------------------------------------------- revalidation
def _until_pending(e, t=0.05, limit=600):
    e.tick(0, snap(0))
    while e.pending is None and t < limit:
        e.tick(t, snap(t))
        t = round(t + 0.05, 6)
    assert e.pending is not None
    return t


def test_pending_cancelled_when_target_missing():
    e = make(3)
    t = _until_pending(e)
    target = e.pending.target
    e.reg.set_available([c for c in ("M", "D", "S") if c != target])
    d = e.tick(t, snap(t))
    assert d.action == DirectorAction.CANCEL and "unavailable" in d.reason


def test_pending_cancelled_on_signal_loss():
    e = make(3)
    t = _until_pending(e)
    d = e.tick(t, snap(t, MusicState.NO_SIGNAL, beat_conf=0))
    assert d.action == DirectorAction.CANCEL and "signal lost" in d.reason


def test_pending_cancelled_when_target_no_longer_suits_state():
    e = make(3, cams_over={"D": {"allowed_states": ["stable_groove"]}, "S": {"allowed_states": ["stable_groove"]}})
    t = _until_pending(e)
    d = e.tick(t, snap(t, MusicState.SPARSE))
    assert d.action == DirectorAction.CANCEL and "no longer suits" in d.reason


def test_exit_pending_fires_before_hard_cap():
    e = make(3)
    e.tick(0, snap(0))
    e.force("D", 1.0, "test")
    ds = run(e, 30, start=1.05)
    sw = switches(ds)
    assert sw and sw[0].t - 1.0 <= e.spec(e.reg.get("D")).cap


# ----------------------------------------------------------------------------- determinism
def test_deterministic_with_same_seed_and_seed_matters():
    a = [(d.t, d.candidate_camera) for d in switches(run(make(4, seed=42), 900))]
    b = [(d.t, d.candidate_camera) for d in switches(run(make(4, seed=42), 900))]
    c = [(d.t, d.candidate_camera) for d in switches(run(make(4, seed=43), 900))]
    assert a == b and a != c


def test_no_random_module_used():
    import inspect
    import dj_director.director.engine as eng
    src = inspect.getsource(eng)
    assert "import random" not in src and "from random" not in src


# ----------------------------------------------------------------------------- manual override & safety
def test_manual_override_timed():
    e = make(3, override_policy="timed", override_seconds=30)
    run(e, 20)
    d = e.on_external_change("S", 20.0)
    assert d.action == DirectorAction.OVERRIDE and e.current == "S"
    ds = run(e, 29.9, start=20.05)
    assert not switches(ds) and any("manual override" in x.reason for x in ds)
    assert switches(run(e, 200, start=50.0))


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
    assert "unmanaged" in e.on_external_change("Be Right Back", 10.0).reason
    assert not switches(run(e, 300, start=10.05))


def test_manual_override_adopt_respects_min_length_of_new_shot():
    e = make(3, override_policy="adopt")
    run(e, 10)
    e.on_external_change("D", 10.0)
    assert not switches(run(e, 4, start=10.05))


def test_no_signal_returns_to_master_and_holds():
    e = make(3)
    run(e, 0.1)
    e.force("D", 1.0, "test")
    sw = switches(run(e, 30, state_fn=lambda t: MusicState.NO_SIGNAL, beat_conf=0, start=1.05))
    assert len(sw) == 1 and sw[0].candidate_camera == "M" and "no audio" in sw[0].reason


def test_no_signal_hold_policy():
    e = make(3, no_signal_policy="hold")
    run(e, 0.1)
    e.force("D", 1.0, "test")
    assert not switches(run(e, 30, state_fn=lambda t: MusicState.NO_SIGNAL, beat_conf=0, start=1.05))


def test_switch_failure_restores_previous_shot():
    e = make(3)
    e.tick(0, snap(0))
    run(e, 20, start=0.05)
    start_before = e.shot_start
    e.force("D", 20.0, "x")
    e.on_switch_failed("D", "M", 20.1)
    assert e.current == "M" and e.shot_start == start_before and e.shot_spec.cls == "master"
    d = e.tick(20.2, snap(20.2))
    assert d.candidate_camera != "D" or d.action == DirectorAction.HOLD


# ----------------------------------------------------------------------------- phrase timing
def _with_drop(engine, t_drop=14.0):
    engine.tick(0, snap(0))
    run(engine, t_drop - 0.05, start=0.05)
    ev = MusicEvent(t_drop, "reentry", 0.9)
    engine.tick(t_drop, snap(t_drop, MusicState.REENTRY), (ev,))


def test_editorial_entry_waits_for_phrase_boundary_after_drop():
    e = make(3)
    _with_drop(e, 14.0)
    anchor = round(14.0 / PERIOD) * PERIOD
    ds = run(e, 400, start=14.05)
    sched = [d for d in ds if d.action == DirectorAction.SCHEDULE and d.timing == TimingMode.PHRASE]
    assert sched
    for d in sched:
        n = (d.target_t - anchor) / (16 * PERIOD)
        assert abs(n - round(n)) < 1e-6 and "phrase boundary" in d.reason
        assert d.target_t - d.t <= PRESETS["natural"].max_phrase_wait + 1e-6


def test_no_phrase_timing_without_a_detected_drop():
    assert not [d for d in run(make(3), 600) if d.timing == TimingMode.PHRASE]


def test_phrase_grid_invalidated_by_tempo_change():
    e = make(3)
    _with_drop(e, 14.0)
    faster = BeatInfo(130, 0.9, 60 / 130, 19.9, 20.3, 1, -1, 0.0)
    s_new = MusicSnapshot(20.0, MusicState.STABLE_GROOVE, 0.8, 0, MusicState.STABLE_GROOVE, FEAT, faster)
    assert e.next_phrase_boundary(20.0, s_new) == 0.0 and e.phrase_anchor == 0.0


def test_phrase_sync_can_be_disabled():
    e = make(3)
    e.p = dataclasses.replace(e.p, phrase_sync=False)
    _with_drop(e, 14.0)
    assert not [d for d in run(e, 400, start=14.05) if d.timing == TimingMode.PHRASE]
