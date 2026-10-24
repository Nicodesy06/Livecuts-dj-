import numpy as np

from dj_director.core.events import MusicState
from dj_director.simulation.synth import Section, generate
from tests.conftest import run_pipeline


def _section(tr, name):
    return next((a, b) for n, a, b in tr.sections if n == name)


def test_breakdown_and_drop_detected(synth_124):
    p, events, _ = run_pipeline(synth_124.audio, synth_124.sr)
    sparse = [e.t for e in events if e.kind == "sparse_start"]
    reentry = [e for e in events if e.kind == "reentry"]
    for name in ("breakdown", "breakdown2"):
        a, b = _section(synth_124, name)
        assert any(a <= t <= a + 4 for t in sparse), (name, sparse)
    for name in ("drop", "drop2"):
        a, _ = _section(synth_124, name)
        assert any(abs(e.t - a) < 1.0 for e in reentry), (name, [e.t for e in reentry])
    assert len(reentry) == 2                  # no invented drops
    assert all(0 < e.confidence <= 0.95 for e in reentry)


def test_volume_increase_alone_is_not_a_drop():
    """The same groove getting 12 dB louder must not produce a re-entry event."""
    tr = generate(bpm=124, sections=[Section("g", 48, ("kick", "hat", "clap", "bass"))])
    gain = np.ones_like(tr.audio)
    n = len(gain)
    gain[: n // 2] = 0.25
    gain[n // 2: n // 2 + tr.sr * 2] = np.linspace(0.25, 1, tr.sr * 2)
    p, events, _ = run_pipeline((tr.audio * gain).astype(np.float32), tr.sr)
    assert not [e for e in events if e.kind == "reentry"]


def test_transition_events_are_rare(synth_124):
    _, events, _ = run_pipeline(synth_124.audio, synth_124.sr)
    tr = [e for e in events if e.kind == "transition"]
    minutes = len(synth_124.audio) / synth_124.sr / 60
    assert len(tr) / minutes < 4             # spectral novelty, not every bar


def test_stable_groove_is_reported(synth_124):
    _, _, states = run_pipeline(synth_124.audio, synth_124.sr)
    groove = [s for t, s, b in states if 50 < t < 75]
    assert groove.count(MusicState.STABLE_GROOVE) > len(groove) * 0.5


def test_states_have_confidence_and_since(synth_124):
    p, _, _ = run_pipeline(synth_124.audio[: synth_124.sr * 60], synth_124.sr)
    s = p.snapshot()
    assert 0 <= s.state_confidence <= 1 and s.state_since <= s.t and s.warmed_up
