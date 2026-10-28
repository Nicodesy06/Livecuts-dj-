"""End-to-end: real-time file audio -> analysis -> director -> fake OBS."""

import time

import pytest
import soundfile as sf

pytest.importorskip("websockets")

from dj_director.config.manager import ConfigManager
from dj_director.config.schema import AppConfig, CameraConfig, CameraRole
from dj_director.core.controller import AppController, DirectorStatus
from dj_director.obs.fake_server import FakeObsServer
from dj_director.simulation.synth import Section, generate


def wait(cond, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def env(tmp_path, isolated_home):
    srv = FakeObsServer(scenes=["Cam M", "Cam D", "Cam S", "BRB"]).start()
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
    cfg.director.overrides = {"min_cut_gap": 1.0, "max_cuts_per_minute": 30.0}
    cfg.director.override_seconds = 4.0
    ConfigManager().save(cfg)
    ctrl = AppController()
    ctrl.start_services()
    assert wait(lambda: ctrl.obs.connected)
    yield ctrl, srv
    ctrl.shutdown()
    srv.stop()


def switch_requests(srv):
    return [r for r in srv.requests if r[0] == "SetCurrentProgramScene"]


def test_live_director_switches_obs(env):
    ctrl, srv = env
    ctrl.start_director(shadow=False)
    assert wait(lambda: ctrl.status == DirectorStatus.LIVE)
    assert wait(lambda: len(switch_requests(srv)) >= 2, 15)
    assert srv.program in ("Cam M", "Cam D", "Cam S")


def test_shadow_never_commands_obs(env):
    ctrl, srv = env
    ctrl.start_director(shadow=True)
    assert wait(lambda: ctrl.status == DirectorStatus.SHADOW)
    time.sleep(8)
    assert switch_requests(srv) == []
    assert [d for d in ctrl.decisions if d.action.value == "SWITCH"]   # decisions still made


def test_stop_halts_new_switches_promptly(env):
    ctrl, srv = env
    ctrl.start_director(shadow=False)
    assert wait(lambda: len(switch_requests(srv)) >= 1, 15)
    ctrl.stop_director()
    assert wait(lambda: ctrl.status == DirectorStatus.OFF, 1)
    n = len(switch_requests(srv))
    time.sleep(5)
    assert len(switch_requests(srv)) == n


def test_manual_change_is_respected(env):
    ctrl, srv = env
    ctrl.start_director(shadow=False)
    assert wait(lambda: ctrl.status == DirectorStatus.LIVE)
    time.sleep(1.0)
    n = len(switch_requests(srv))
    srv.operator_switch("Cam S")
    assert wait(lambda: ctrl.snapshot().override_active, 2)
    time.sleep(3.0)                                     # override window is 4 s
    assert len(switch_requests(srv)) == n and srv.program == "Cam S"
    assert wait(lambda: len(switch_requests(srv)) > n, 12)   # resumes afterwards


def test_unmanaged_scene_pauses_until_resume(env):
    ctrl, srv = env
    ctrl.start_director(shadow=False)
    assert wait(lambda: ctrl.status == DirectorStatus.LIVE)
    srv.operator_switch("BRB")
    time.sleep(6)
    assert srv.program == "BRB"
    ctrl.resume()
    assert wait(lambda: srv.program != "BRB", 10)


def test_obs_disconnect_arms_and_recovers(env):
    ctrl, srv = env
    ctrl.start_director(shadow=False)
    assert wait(lambda: ctrl.status == DirectorStatus.LIVE)
    srv.refuse_connections = True            # OBS "closed"
    srv.disconnect_all()
    assert wait(lambda: ctrl.status == DirectorStatus.ARMED, 3)
    n = len(switch_requests(srv))
    time.sleep(2)
    assert len(switch_requests(srv)) == n    # no commands while OBS is away
    srv.refuse_connections = False           # OBS "restarted"
    assert wait(lambda: ctrl.status == DirectorStatus.LIVE, 20)


def test_scene_rename_updates_persisted_config(env):
    ctrl, srv = env
    srv.rename_scene("Cam D", "Cam Decks")
    assert wait(lambda: any(c.scene_name == "Cam Decks" for c in ctrl.config.cameras))
    # the director thread saves right after updating memory: wait for the file, don't race it
    assert wait(lambda: any(c.scene_name == "Cam Decks" for c in ConfigManager().load().cameras))


def test_force_master(env):
    ctrl, srv = env
    ctrl.start_director(shadow=False)
    assert wait(lambda: ctrl.status == DirectorStatus.LIVE)
    srv.operator_switch("Cam D")
    time.sleep(0.3)
    ctrl.force_master()
    assert wait(lambda: srv.program == "Cam M", 3)


def test_calibration_alternates_cameras_on_beat(env):
    ctrl, srv = env
    ctrl.start_calibration("Cam M", "Cam D", every_beats=4)
    assert wait(lambda: ctrl.status == DirectorStatus.CALIBRATING)
    assert wait(lambda: len(switch_requests(srv)) >= 3, 25)
    progs = [srv._uuid(n) for n in ("Cam M", "Cam D")]
    sent = [r[1].get("sceneUuid") for r in switch_requests(srv)]
    assert set(sent) <= set(progs) and sent[0] != sent[1]
    ctrl.set_latency_offset(120)
    ctrl.stop_director()
    assert wait(lambda: ctrl.status == DirectorStatus.OFF)
    assert ConfigManager().load().director.latency_offset_ms == 120


# ----------------------------------------------------------------------------- P0: OBS state synchronisation
from dj_director.core.switching import SwitchStatus  # noqa: E402


def test_manual_change_while_obs_was_away_is_respected_on_reconnect(env):
    ctrl, srv = env
    ctrl.start_director(shadow=False)
    assert wait(lambda: ctrl.status == DirectorStatus.LIVE)
    srv.refuse_connections = True
    srv.disconnect_all()
    assert wait(lambda: ctrl.status == DirectorStatus.ARMED, 3)
    target = "Cam S" if ctrl.engine.current != "Cam S" else "Cam D"
    srv.program = target                      # the operator switched in OBS meanwhile
    srv.refuse_connections = False
    assert wait(lambda: ctrl.status == DirectorStatus.LIVE, 20)
    assert wait(lambda: any(d.action.value == "OVERRIDE" for d in ctrl.decisions), 3)
    n = len(switch_requests(srv))
    time.sleep(2.5)                           # override (4 s in the test config) is respected
    assert len(switch_requests(srv)) == n and srv.program == target


def test_switch_states_and_confirmed_airtime(env):
    ctrl, srv = env
    ctrl.start_director(shadow=False)
    assert wait(lambda: len(switch_requests(srv)) >= 3, 20)
    time.sleep(0.5)
    recs = ctrl.switches.recent(20)
    assert recs and all(r.status in (SwitchStatus.CONFIRMED, SwitchStatus.CANCELLED) for r in recs[:-1])
    snap = ctrl.snapshot()
    total = sum(sec for _, sec in snap.airtime)
    assert snap.program == srv.program
    assert total > 3 and dict(snap.airtime).keys() <= {"Cam M", "Cam D", "Cam S"}


def test_failed_switch_is_not_counted_as_airtime(env):
    ctrl, srv = env
    srv.fail_switches = True                  # OBS refuses every cut
    ctrl.start_director(shadow=False)
    assert wait(lambda: ctrl.switches.last_problem() is not None, 20)
    time.sleep(1.0)
    snap = ctrl.snapshot()
    assert snap.switch_problem.status == SwitchStatus.FAILED
    assert dict(snap.airtime).keys() <= {"Cam M"}            # only what OBS really showed
    assert ctrl.engine.current in (None, "Cam M")             # rolled back to what is on air
