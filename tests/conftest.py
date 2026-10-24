import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from dj_director.simulation.synth import generate


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own config/log directory."""
    monkeypatch.setenv("DJ_DIRECTOR_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture(scope="session")
def synth_124():
    return generate(bpm=124.0, sr=44100, seed=1)


def run_pipeline(audio, sr, cfg=None, block=512):
    from dj_director.config.schema import AnalysisConfig
    from dj_director.music.analyzer import AnalysisPipeline
    p = AnalysisPipeline(sr, cfg or AnalysisConfig(), lambda s: s / sr)
    events = []
    states = []
    for i in range(0, len(audio), block):
        p.push(audio[i:i + block], i)
        events.extend(p.state.drain_events())
        if (i // block) % 20 == 0:
            states.append((p.last_t, p.state.state, p.beat_info()))
    return p, events, states
