"""Scene awareness against the fake OBS: stills over obs-websocket, veto end-to-end."""

import time

import pytest
import soundfile as sf

pytest.importorskip("websockets")

from dj_director.config.manager import ConfigManager
from dj_director.config.schema import AppConfig, CameraConfig, CameraRole
from dj_director.core.controller import AppController
from dj_director.obs.fake_server import FakeObsServer
from dj_director.simulation.synth import Section, generate
from dj_director.visual.frames import FrameGrabber


def wait(cond, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_grabber_reads_every_scene_and_favours_the_priority_camera():
    srv = FakeObsServer(scenes=["A", "B", "C"]).start()
    got = {}
    g = FrameGrabber("127.0.0.1", srv.port, lambda: "", lambda: ["A", "B", "C", "Missing"],
                     lambda name, t, rgb: got.setdefault(name, []).append(rgb.shape), priority=lambda: "C",
                     rate="high")
    try:
        g.start()
        assert wait(lambda: sum(len(v) for v in got.values()) >= 24, 10)
    finally:
        g.stop()
        srv.stop()
    assert set(got) == {"A", "B", "C"}
    assert all(s == (216, 384, 3) for v in got.values() for s in v)
    assert len(got["C"]) > len(got["A"]) + len(got["B"])        # 60 % of the stills
    kinds = {r[0] for r in srv.requests}
    assert kinds == {"GetSourceScreenshot"}                      # read-only, nothing else


def test_grabber_stops_on_wrong_password():
    srv = FakeObsServer(password="secret").start()
    g = FrameGrabber("127.0.0.1", srv.port, lambda: "wrong", lambda: ["Cam Master"], lambda *a: None)
    try:
        g.start()
        assert wait(lambda: g.state == "auth_failed", 5)
    finally:
        g.stop()
        srv.stop()


@pytest.fixture
def env(tmp_path, isolated_home):
    srv = FakeObsServer(scenes=["Cam M", "Cam D", "Cam S"]).start()
    srv.scene_look["Cam S"] = {"black": True}
    tr = generate(bpm=124, sections=[Section("g", 64, ("kick", "hat", "clap", "bass"))])
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
    cfg.vision.rate = "high"
    ConfigManager().save(cfg)
    ctrl = AppController()
    ctrl.start_services()
    assert wait(lambda: ctrl.obs.connected)
    yield ctrl, srv
    ctrl.shutdown()
    srv.stop()


def test_black_camera_is_never_put_on_air(env):
    ctrl, srv = env
    assert wait(lambda: ctrl.vision.issue("Cam S") == "black frame", 15)
    assert ctrl.vision.issue("Cam D") == "" and ctrl.vision.issue("Cam M") == ""
    ctrl.start_director(shadow=False)
    switches = lambda: [r for r in srv.requests if r[0] == "SetCurrentProgramScene"]
    assert wait(lambda: len(switches()) >= 4, 40)
    bad_uuid = srv._uuid("Cam S")
    assert all(r[1].get("sceneUuid") != bad_uuid and r[1].get("sceneName") != "Cam S" for r in switches())
    snap = ctrl.snapshot()
    assert any(c.name == "Cam S" and c.issue == "black frame" for c in snap.vision.cameras)
