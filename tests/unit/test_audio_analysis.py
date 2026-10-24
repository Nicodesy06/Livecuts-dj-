"""Audio input + analysis behaviour on controlled signals."""

import time

import numpy as np
import pytest
import soundfile as sf

from dj_director.config.schema import AnalysisConfig, AudioConfig
from dj_director.core.events import MusicState
from dj_director.simulation.synth import Section, generate
from tests.conftest import run_pipeline

SR = 44100


def test_silence_is_no_signal_and_no_beat():
    p, events, _ = run_pipeline(np.zeros(SR * 15, dtype=np.float32), SR)
    snap = p.snapshot()
    assert snap.state == MusicState.NO_SIGNAL
    assert snap.beat.confidence == 0.0
    assert events == []


def test_weak_signal_below_threshold_is_no_signal():
    rng = np.random.default_rng(0)
    tr = generate(bpm=124)
    weak = (tr.audio[: SR * 20] * 10 ** (-70 / 20)).astype(np.float32)    # ~-73 dBFS
    p, _, _ = run_pipeline(weak, SR)
    assert p.snapshot().state == MusicState.NO_SIGNAL


def test_noise_has_low_beat_confidence_and_no_reentry():
    rng = np.random.default_rng(3)
    noise = (rng.standard_normal(SR * 30) * 0.1).astype(np.float32)
    p, events, _ = run_pipeline(noise, SR)
    snap = p.snapshot()
    assert snap.state != MusicState.NO_SIGNAL
    assert snap.beat.confidence < 0.3
    assert not [e for e in events if e.kind == "reentry"]


def test_irregular_onsets_give_low_confidence():
    rng = np.random.default_rng(5)
    x = np.zeros(SR * 30, dtype=np.float32)
    click = np.hanning(400).astype(np.float32)
    for t in np.cumsum(rng.uniform(0.15, 0.9, 80)):
        i = int(t * SR)
        if i + 400 < len(x):
            x[i:i + 400] += click * 0.6
    p, _, _ = run_pipeline(x, SR)
    assert p.snapshot().beat.confidence < 0.35


@pytest.mark.parametrize("bpm", [124.0, 128.0, 140.0, 172.0])
def test_stable_tempo_within_one_percent(bpm):
    tr = generate(bpm=bpm, sections=[Section("g", 40, ("kick", "hat", "clap", "bass"))])
    p, _, _ = run_pipeline(tr.audio, tr.sr)
    b = p.snapshot().beat
    assert abs(b.bpm / bpm - 1) < 0.01, b.bpm
    assert b.confidence > 0.6


def test_half_double_time_resolved_with_genre_prior():
    tr = generate(bpm=90.0, sections=[Section("g", 40, ("kick", "hat", "clap", "bass"))])
    p, _, _ = run_pipeline(tr.audio, tr.sr, AnalysisConfig(prior_bpm=95))
    assert abs(p.snapshot().beat.bpm / 90.0 - 1) < 0.01


def test_tempo_does_not_oscillate(synth_124):
    p, _, states = run_pipeline(synth_124.audio, synth_124.sr)
    confident = [b.bpm for t, s, b in states if b.confidence > 0.5 and t > 10]
    assert confident and max(confident) - min(confident) < 1.0


def test_tempo_change_is_followed():
    a = generate(bpm=124.0, sections=[Section("g", 24, ("kick", "hat", "clap", "bass"))])
    b = generate(bpm=130.0, sections=[Section("g", 24, ("kick", "hat", "clap", "bass"))], seed=2)
    p, _, _ = run_pipeline(np.concatenate([a.audio, b.audio]), SR)
    assert abs(p.snapshot().beat.bpm - 130.0) < 1.3


def test_beat_phase_aligned_with_ground_truth(synth_124):
    p, _, _ = run_pipeline(synth_124.audio, synth_124.sr)
    bt = np.array(p.beat_times)
    late = bt[bt > 20]
    err = np.array([np.min(np.abs(synth_124.beat_times - x)) for x in late])
    assert np.median(err) < 0.03


def test_beat_confidence_drops_in_beatless_breakdown(synth_124):
    p, _, states = run_pipeline(synth_124.audio, synth_124.sr)
    # breakdown2 (pad only) is 170.3 - 185.8 s in the synthetic set
    in_bd = [b.confidence for t, s, b in states if 176 < t < 185]
    assert in_bd and max(in_bd) < 0.3
    # tempo is kept (coasting), not replaced by a noise estimate
    assert all(abs(b.bpm - 124) < 1.5 for t, s, b in states if 176 < t < 185)


def test_dropout_gap_is_tolerated():
    tr = generate(bpm=124, sections=[Section("g", 24, ("kick", "hat", "clap", "bass"))])
    from dj_director.music.analyzer import AnalysisPipeline
    p = AnalysisPipeline(SR, AnalysisConfig(), lambda s: s / SR)
    pos = 0
    while pos < len(tr.audio):
        p.push(tr.audio[pos:pos + 512], pos)
        pos += 512
        if pos == 512 * 1000:
            pos += 4096          # simulate lost samples (ring overrun)
    assert p.gaps == 1
    assert abs(p.snapshot().beat.bpm - 124) < 1.5


def test_file_backend_manager_status_and_clipping(tmp_path):
    from dj_director.audio.manager import AudioInputManager
    x = np.zeros(SR * 3, dtype=np.float32)
    x[SR:SR + 2000] = 1.0          # full-scale -> clipping
    x[SR + 2000:] = 0.3 * np.sin(np.arange(len(x) - SR - 2000) / SR * 2 * np.pi * 220)
    path = tmp_path / "clip.wav"
    sf.write(path, x, SR, subtype="FLOAT")
    m = AudioInputManager(AudioConfig(backend="file", file_path=str(path), file_loop=False))
    m.start()
    try:
        clipped = present = False
        t0 = time.time()
        while time.time() - t0 < 3.0:
            st = m.status()
            clipped |= st.clipping
            present |= st.signal_present
            time.sleep(0.05)
        assert st.sample_rate == SR and st.backend == "file"
        assert clipped and present
    finally:
        m.stop()


def test_missing_device_reports_error_without_crashing():
    from dj_director.audio.manager import AudioInputManager
    m = AudioInputManager(AudioConfig(backend="input", device_name="Definitely Not A Device"))
    m.start()
    try:
        st = m.status()
        assert not st.running and "not found" in st.error
    finally:
        m.stop()
