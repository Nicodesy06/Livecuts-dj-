"""Italian UI: every UI string has a translation; engine reasons translate fully."""

import ast
from pathlib import Path

import pytest

from dj_director.config.presets import PRESET_DESCRIPTIONS
from dj_director.config.schema import DirectorConfig
from dj_director.core.events import MusicState
from dj_director.ui import i18n
from dj_director.ui.i18n import IT, tr, tr_reason, untranslated_fragments

UI = Path(i18n.__file__).parent


def _literal_keys():
    keys = set()
    for f in UI.glob("*.py"):
        for n in ast.walk(ast.parse(f.read_text("utf-8"))):
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "tr" and n.args:
                a = n.args[0]
                if isinstance(a, ast.Constant):
                    keys.add(a.value)
                elif isinstance(a, ast.Call) and isinstance(getattr(a.func, "value", None), ast.Dict):
                    keys |= {v.value for v in a.func.value.values + a.args[1:] if isinstance(v, ast.Constant)}
    return keys


@pytest.fixture(autouse=True)
def italian():
    i18n.set_language("it")
    yield
    i18n.set_language("en")


def test_every_ui_literal_is_translated():
    missing = sorted(k for k in _literal_keys() if k not in IT)
    assert not missing, missing


def test_dynamic_labels_translated():
    from dj_director.audio.devices import _HINTS
    from dj_director.ui.settings_pages import PARAM_LABELS, CamerasPage
    from dj_director.ui.source_detect import KIND_LABEL
    from dj_director.ui.tools_pages import METRIC_ROWS
    dynamic = ([v[0] for v in PARAM_LABELS.values()] + [n for _, n in METRIC_ROWS] + CamerasPage.COLS
               + list(KIND_LABEL.values()) + list(PRESET_DESCRIPTIONS.values()) + [h for h in _HINTS.values() if h]
               + ["Minimal", "Natural", "Dynamic", "connected", "connecting", "disconnected", "auth_failed"])
    from dj_director.ui.vision_page import ISSUE_LABELS, RATE_LABELS, VisionPage
    from dj_director.visual.providers import CLOUD_MODELS, LOCAL_MODELS
    dynamic += (VisionPage.COLS + list(RATE_LABELS.values()) + list(ISSUE_LABELS.values())
                + [m.pros for m in LOCAL_MODELS + CLOUD_MODELS] + [m.cons for m in LOCAL_MODELS + CLOUD_MODELS]
                + ["Off", "Local (offline, on this PC)", "Cloud (online, Claude API)", "Vision",
                   "MIDI inputs found but none could be opened (in use by another program?)."])
    assert [k for k in dynamic if k not in IT] == []


def test_placeholders_preserved():
    import re
    for en, it in IT.items():
        assert re.findall(r"\{[^}]*\}", en) == re.findall(r"\{[^}]*\}", it), en


def test_english_fallback():
    i18n.set_language("en")
    assert tr("Stop calibration") == "Stop calibration"
    assert tr_reason("cooldown passed") == "cooldown passed"


def test_all_engine_reasons_translate(synth_124):
    from dj_director.simulation.replay import run_replay
    from tests.unit.test_engine import make, run
    reasons = set()
    for preset in ("minimal", "natural", "dynamic"):
        r = run_replay(synth_124.audio, synth_124.sr, director=DirectorConfig(preset=preset), seed=3,
                       baselines=False, log_holds=True)
        reasons |= {d.reason for d in r.decisions}
    e = make(1)
    reasons |= {d.reason for d in run(e, 30)}
    e = make(3, override_policy="timed")
    run(e, 10)
    reasons.add(e.on_external_change("Cam X", 10.0).reason)
    reasons |= {d.reason for d in run(e, 5, start=10.05)}
    # 0.1.3 scenarios: visual novelty, continuity, drop return, hard cap, chains, revalidation
    from dj_director.core.events import MusicEvent, MusicState
    from tests.unit.test_engine import snap
    level = {"v": 0.1}
    e = make(4, "dynamic", visual=lambda cam: level["v"] if cam.name == "D" else 0.1)
    reasons |= {d.reason for d in run(e, 20)}
    level["v"] = 0.95
    reasons |= {d.reason for d in run(e, 600, state_fn=lambda t: MusicState.TRANSITION if t % 90 < 30
                                       else MusicState.STABLE_GROOVE, start=20.0)}
    e = make(3)
    e.tick(0, snap(0))
    e.force("D", 1.0, "test")
    ev = MusicEvent(7.0, "reentry", 0.9)
    reasons |= {d.reason for d in run(e, 30, events_fn=lambda t: (ev,) if t == 7.0 else (), start=1.05)}
    e = make(3)
    e.tick(0, snap(0))
    e.force("S", 1.0, "test")
    reasons |= {d.reason for d in run(e, 40, beat_conf=0.1, start=1.05)}
    e = make(3, cams_over={"D": {"allowed_states": ["stable_groove"]}, "S": {"allowed_states": ["stable_groove"]}})
    run(e, 40)
    reasons |= {d.reason for d in run(e, 60, state_fn=lambda t: MusicState.SPARSE, start=40.0)}
    # 0.2 scenarios: drop reveal (entry and exit), quality veto on air and on the master
    for start_on in (None, "D"):
        e = make(4)
        e.tick(0, snap(0))
        if start_on:
            e.force(start_on, 1.0, "test")
        ev = MusicEvent(14.0 if not start_on else 4.0, "reentry", 0.9)
        reasons |= {d.reason for d in run(e, 20, events_fn=lambda t, ev=ev: (ev,) if t == ev.t else (),
                                          start=1.05)}
    for issue in ("black frame", "frozen image", "AI: camera pointing at the ceiling"):
        e = make(4, issue=lambda cam, i=issue: i if cam.name in ("M", "S") else "")
        reasons |= {d.reason for d in run(e, 60)}
    bad = {f for r in reasons for f in untranslated_fragments(r)}
    assert not bad, bad
    assert "rivelazione sul drop" in tr_reason("entry: drop reveal on Cam Wide (0.90)")
    assert "inquadratura" in tr_reason("shot length 30s of ~32s planned")


def test_states_translated():
    assert all(i18n.tr_state(s) != s.label for s in MusicState)
