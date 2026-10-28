"""DJ gestures heard in the audio (0.3): EQ / isolator kills, filters, bass coming back."""

import pytest

from dj_director.config.schema import AnalysisConfig, DirectorConfig
from dj_director.music.analyzer import AnalysisPipeline
from dj_director.simulation.replay import default_cameras, run_replay
from dj_director.simulation.synth import MixMove, Section, apply_mixer, generate

GROOVE = [Section("g", 64, ("kick", "hat", "clap", "bass", "pad"))]


@pytest.fixture(scope="module")
def groove():
    return generate(sections=GROOVE, bpm=124)


def events_of(audio, sr):
    p = AnalysisPipeline(sr, AnalysisConfig(), lambda s: s / sr)
    out, gestures = [], []
    for i in range(0, len(audio), 2048):
        p.push(audio[i:i + 2048], i)
        out += [e for e in p.state.drain_events() if e.kind in ("eq_cut", "filter", "eq_return")]
        gestures.append((p.last_t, p.snapshot().mix_gesture))
    return out, gestures


def kinds(evs):
    return [(e.kind, e.detail.split(":")[0]) for e in evs]


def test_plain_groove_has_no_gesture(groove):
    evs, _ = events_of(groove.audio, groove.sr)
    assert evs == []


def test_low_kill_and_bass_return_are_heard(groove):
    audio = apply_mixer(groove.audio, groove.sr, [MixMove("low_kill", 30.0, 45.0)])
    evs, gestures = events_of(audio, groove.sr)
    assert [e.kind for e in evs] == ["eq_cut", "eq_return"]
    cut, back = evs[0], evs[1]
    assert cut.kind == "eq_cut" and 30.0 < cut.t < 31.5 and cut.confidence >= 0.6
    assert back.kind == "eq_return" and 45.0 < back.t < 46.5 and back.confidence >= 0.8
    during = [g for t, g in gestures if 33 < t < 44]
    after = [g for t, g in gestures if 48 < t < 60]
    assert during and all(g == "eq_cut:low" for g in during)      # the gesture is followed while it lasts
    assert after and all(g == "" for g in after)


def test_high_kill_and_filters_are_heard(groove):
    for move, kind, side in ((MixMove("high_kill", 30, 40, 3000), "eq_cut", "high"),
                             (MixMove("lp_sweep", 30, 38, 12000, 400), "filter", "low-pass"),
                             (MixMove("hp_sweep", 30, 38, 40, 2000), None, "low")):
        evs, gestures = events_of(apply_mixer(groove.audio, groove.sr, [move]), groove.sr)
        first = evs[0]
        assert 30 < first.t < 35 and first.kind in (("eq_cut", "filter") if kind is None else (kind,))
        assert first.detail.startswith(side) or kind is None
        assert evs[-1].kind == "eq_return"
        if move.kind == "hp_sweep":           # a kill that keeps climbing is followed as a filter
            assert any(g == "filter:low" for t, g in gestures if 36 < t < 38)


def test_a_track_emptying_by_itself_is_not_a_dj_gesture():
    track = generate(bpm=124)                  # breakdowns: kick and bass leave, pads / claps change
    evs, _ = events_of(track.audio, track.sr)
    assert evs == []


def test_replay_mixer_shot_then_reveal_when_the_bass_comes_back():
    tr = generate(sections=[Section("g", 96, ("kick", "hat", "clap", "bass", "pad"))], bpm=124)
    beat = 60 / 124
    # the DJ kills the bass at bar 9 (master past its minimum, mixer camera unused) for 6 bars:
    # the bass returns while the mixer shot is still inside its 14 s maximum
    cut_at, back_at = 32 * beat, 56 * beat
    audio = apply_mixer(tr.audio, tr.sr, [MixMove("low_kill", cut_at, back_at)])
    r = run_replay(audio, tr.sr, default_cameras(4), DirectorConfig(preset="natural"), seed=3, baselines=False)
    mixer = [s for s in r.shots if s.reason.startswith(("entry: DJ cuts the lows", "exit: DJ cuts the lows"))]
    assert mixer, [(round(s.start), s.camera, s.reason[:40]) for s in r.shots]
    m = mixer[0]
    assert m.camera == "Cam Mixer Detail" and cut_at < m.start < cut_at + 2.5
    nxt = r.shots[r.shots.index(m) + 1]
    assert nxt.camera == "Cam Behind" and nxt.reason.startswith("exit: drop reveal on Cam Behind")
    assert back_at < nxt.start < back_at + 1.5
    assert r.metrics.hard_cap_violations == 0 and r.metrics.min_duration_violations == 0
