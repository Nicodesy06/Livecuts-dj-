"""0.6 end-to-end: the app zooms between shots and now and then uses the custom transition (fake OBS)."""

import json
import time

import pytest
import soundfile as sf

pytest.importorskip("websockets")

from dj_director.config.manager import ConfigManager
from dj_director.config.schema import AppConfig, CameraConfig, CameraRole
from dj_director.core.controller import AppController
from dj_director.obs.fake_server import FakeObsServer
from dj_director.simulation.synth import Section, generate


def wait(cond, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def app(tmp_path, isolated_home):
    srv = FakeObsServer(scenes=["Cam M", "Cam D", "Cam S"]).start()
    tr = generate(bpm=124, sections=[Section("g", 96, ("kick", "hat", "clap", "bass"))])
    wav = tmp_path / "set.wav"
    sf.write(wav, tr.audio, tr.sr)
    cfg = AppConfig(first_run_done=True)
    cfg.obs.port = srv.port
    cfg.audio.backend, cfg.audio.file_path = "file", str(wav)
    fast = dict(min_duration=1.0, mean_duration=2.0, max_duration=3.0, cooldown=0.0)
    cfg.cameras = [CameraConfig("Cam M", role=CameraRole.MASTER, is_master=True, **fast),
                   CameraConfig("Cam D", role=CameraRole.DETAIL, **fast),
                   CameraConfig("Cam S", role=CameraRole.SIDE, **fast)]
    cfg.director.preset = "dynamic"
    cfg.director.overrides = {"min_cut_gap": 1.0, "max_cuts_per_minute": 30.0, "master_duration_scale": 0.3}
    cfg.vision.enabled = False
    cfg.transitions.zoom = True
    cfg.transitions.zoom_amount = 0.05
    cfg.transitions.special = True
    cfg.transitions.special_name = "Stinger DJ"
    cfg.transitions.special_every = 3
    ConfigManager().save(cfg)
    ctrl = AppController()
    ctrl.start_services()
    assert wait(lambda: ctrl.obs.connected)
    yield ctrl, srv
    ctrl.shutdown()
    srv.stop()


def test_zoom_and_occasional_custom_transition_end_to_end(app):
    ctrl, srv = app
    ctrl.start_director(shadow=False)
    cuts = lambda: [t for t in srv.program_transitions]
    assert wait(lambda: len(cuts()) >= 9, 60)
    ctrl.stop_director()
    assert wait(lambda: ctrl.motion.zoomed() == [], 5)
    used = [t for _, t in cuts()]
    assert "Stinger DJ" in used and "Cut" in used
    assert used.count("Stinger DJ") <= len(used) // 2                  # now and then, not every cut
    assert srv.transition == "Cut"                                       # the user's transition is back
    # the zoom animated the cameras and left every scene exactly as it found it
    assert len(srv.transform_log) > 20 and all(i == 1 for _, i, _ in srv.transform_log)
    assert max(f.get("scaleX", 1.0) for _, _, f in srv.transform_log) == pytest.approx(1.05)
    by_scene = {}
    for scene, _, f in srv.transform_log:
        by_scene.setdefault(scene, []).append(f["scaleX"])
    rising = lambda xs: any(1.0 < a < b < c for a, b, c in zip(xs, xs[1:], xs[2:]))
    falling = lambda xs: any(c < b < a for a, b, c in zip(xs, xs[1:], xs[2:]) if a <= 1.05)
    assert any(rising(xs) for xs in by_scene.values())                 # outgoing shot pushes in
    assert any(falling(xs) for xs in by_scene.values())                # incoming shot settles
    for scene in ("Cam M", "Cam D", "Cam S"):
        t = srv.scene_items(scene)[0]["sceneItemTransform"]
        assert (t["scaleX"], t["positionX"]) == (1.0, 0.0), scene
    log = [json.loads(line) for line in ctrl.decision_log.path.read_text("utf-8").splitlines()]
    assert {r["style"] for r in log if r["kind"] == "CutStyle"} == {"zoom", "special"}


def test_shadow_mode_never_touches_obs(app):
    ctrl, srv = app
    ctrl.start_director(shadow=True)
    time.sleep(12)
    ctrl.stop_director()
    assert srv.transform_log == [] and srv.program_transitions == [] and srv.transition == "Cut"
