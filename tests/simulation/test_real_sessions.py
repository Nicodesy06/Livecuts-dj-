"""Editorial invariants on long sessions: real recordings (if provided) and a synthetic DJ mix.

Real recordings are never committed. Put WAV/FLAC files in tests/sessions/ or point
DJD_SESSIONS_DIR to a folder; without files the real-session test is skipped.
"""

import os
from pathlib import Path

import pytest

from dj_director.config.schema import DirectorConfig
from dj_director.simulation.replay import default_cameras, format_report, framed_cameras, load_audio, run_replay
from dj_director.simulation.synth import generate_mix

AUDIO_EXT = {".wav", ".flac", ".ogg", ".aiff", ".aif"}


def _session_files():
    dirs = [Path(__file__).parents[1] / "sessions"]
    if os.environ.get("DJD_SESSIONS_DIR"):
        dirs.insert(0, Path(os.environ["DJD_SESSIONS_DIR"]))
    files = []
    for d in dirs:
        if d.is_dir():
            files += sorted(p for p in d.iterdir() if p.suffix.lower() in AUDIO_EXT)
    return files


def check_invariants(r, label, master_band=(0.62, 0.78)):
    m = r.metrics
    print(f"\n=== {label} ({r.duration / 60:.1f} min)\n" + format_report(r))
    assert m.hard_cap_violations == 0
    assert m.min_duration_violations == 0
    assert m.cooldown_violations == 0
    assert m.abab_patterns == 0
    assert m.repeated_intervals <= max(1, int(m.cuts * 0.1))
    assert m.cuts_per_minute >= 1.0                       # an active direction
    if r.duration >= 600:
        lo, hi = master_band
        assert lo <= m.coverage.get("master", 0) <= hi, m.coverage
        assert m.coverage.get("side", 0) <= 0.25, m.coverage


@pytest.mark.parametrize("path", _session_files() or [pytest.param(None, marks=pytest.mark.skip(
    reason="no real recordings: add WAV/FLAC files to tests/sessions/ or set DJD_SESSIONS_DIR"))],
    ids=lambda p: p.name if p else "none")
def test_real_session(path):
    audio, sr = load_audio(str(path))
    r = run_replay(audio, sr, default_cameras(4), DirectorConfig(), seed=1, baselines=False)
    check_invariants(r, path.name)


@pytest.fixture(scope="module")
def mix():
    return generate_mix()


@pytest.mark.parametrize("preset", ["minimal", "natural", "dynamic"])
def test_long_synthetic_session(mix, preset):
    r = run_replay(mix.audio, mix.sr, default_cameras(4), DirectorConfig(preset=preset), seed=11,
                   beat_times=mix.beat_times, baselines=False)
    # Minimal is deliberately conservative (longer master dwell, weaker budget): it is allowed to sit
    # above the 70 % target; Natural and Dynamic must stay within 70 % +/- 8 points.
    band = (0.62, 0.85) if preset == "minimal" else (0.62, 0.78)
    check_invariants(r, f"synthetic mix, {preset}", band)
    m = r.metrics
    assert m.coverage_max_deviation is not None and m.coverage_max_deviation <= 0.13
    assert m.beat_alignment_median_ms is not None and m.beat_alignment_median_ms < 30
    assert m.max_by_class["detail"] <= 14.0 and m.max_by_class["side"] <= 23.0


def test_long_session_with_framing_metadata_and_digital_crops(mix):
    """Five OBS scenes from four cameras (one front camera cropped twice): no avoidable jump cut."""
    r = run_replay(mix.audio, mix.sr, framed_cameras(), DirectorConfig(preset="natural"), seed=11,
                   beat_times=mix.beat_times, baselines=False)
    check_invariants(r, "synthetic mix, framed cameras")
    m = r.metrics
    assert m.jump_cuts == 0 and m.axis_crossings == 0 and m.repeated_transitions == 0
    # the master is always the home: every insert run is followed by a return to it
    seq = [s.camera for s in r.shots]
    runs, cur = [], 0
    for cam in seq:
        cur = 0 if cam == "CAM1 Wide" else cur + 1
        runs.append(cur)
    assert max(runs) <= 2
