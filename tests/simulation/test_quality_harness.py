"""Editorial quality harness: music-aware director vs timer and random switchers."""

import json

import pytest

from dj_director.config.schema import DirectorConfig
from dj_director.simulation.replay import default_cameras, format_report, run_replay


@pytest.fixture(scope="module")
def results(synth_124):
    secs = [s for _, s, _ in synth_124.sections[1:]]
    out = {}
    for preset in ("minimal", "natural", "dynamic"):
        out[preset] = run_replay(synth_124.audio, synth_124.sr, default_cameras(3),
                                 DirectorConfig(preset=preset), seed=11,
                                 beat_times=synth_124.beat_times, section_starts=secs)
    return out


def test_no_rule_violations(results):
    for r in results.values():
        m = r.metrics
        assert m.abab_patterns == 0
        assert m.min_duration_violations == 0
        assert m.cooldown_violations == 0


def test_calmer_than_baselines(results):
    r = results["natural"]
    assert r.metrics.cuts_per_minute < r.baselines["timer-12s"].cuts_per_minute
    assert r.metrics.cuts_per_minute < r.baselines["random"].cuts_per_minute
    assert r.metrics.duration_cv > r.baselines["timer-12s"].duration_cv   # not a metronome


def test_master_is_the_anchor(results):
    assert 0.55 <= results["natural"].metrics.master_share <= 0.9
    assert results["minimal"].metrics.master_share >= results["dynamic"].metrics.master_share


def test_beat_synced_cuts_land_on_beats(results):
    m = results["natural"].metrics
    assert m.beat_synced_cuts >= 3
    assert m.beat_alignment_median_ms is not None and m.beat_alignment_median_ms < 30


def test_replay_is_deterministic(synth_124):
    a = run_replay(synth_124.audio[: synth_124.sr * 120], synth_124.sr, seed=5, baselines=False)
    b = run_replay(synth_124.audio[: synth_124.sr * 120], synth_124.sr, seed=5, baselines=False)
    assert [(s.camera, s.start) for s in a.shots] == [(s.camera, s.start) for s in b.shots]


def test_exports(results, tmp_path):
    r = results["natural"]
    r.export_json(tmp_path / "r.json")
    r.export_csv(tmp_path / "r.csv")
    data = json.loads((tmp_path / "r.json").read_text())
    assert data["shots"] and data["metrics"]["cuts"] == len(data["shots"]) - 1
    assert (tmp_path / "r.csv").read_text().startswith("t,action")
    print("\n" + format_report(r))
