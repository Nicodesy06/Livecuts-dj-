import time

import pytest

pytest.importorskip("websockets")

from dj_director.config.schema import ObsConfig
from dj_director.core.events import SceneSwitchConfirmed, SceneSwitchFailed
from dj_director.obs.client import ObsClient, ObsError
from dj_director.obs.controller import ObsController
from dj_director.obs.fake_server import FakeObsServer


def wait(cond, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def server():
    s = FakeObsServer(password="pw", scenes=["Cam Master", "Cam Detail", "Cam Side", "BRB"]).start()
    yield s
    s.stop()


@pytest.fixture
def ctrl(server):
    c = ObsController(ObsConfig(port=server.port, request_timeout=1.0, connect_timeout=2.0), lambda: "pw")
    c.results, c.external, c.renames = [], [], []
    c.on_switch_result = c.results.append
    c.on_external_change = c.external.append
    c.on_scene_renamed = lambda o, n: c.renames.append((o, n))
    c.start()
    assert wait(lambda: c.connected)
    yield c
    c.stop()


def test_connect_reads_scenes_and_program(ctrl):
    st = ctrl.status()
    assert st.connected and st.scenes == ("Cam Master", "Cam Detail", "Cam Side", "BRB")
    assert st.program_scene == "Cam Master" and st.obs_version.startswith("31")


def test_wrong_password_stops_retrying(server):
    c = ObsController(ObsConfig(port=server.port), lambda: "wrong")
    c.start()
    try:
        assert wait(lambda: c.state == "auth_failed")
        n = len(server.requests)
        time.sleep(1.5)
        assert c.state == "auth_failed" and not c.connected and len(server.requests) == n
    finally:
        c.stop()


def test_unreachable_obs_does_not_block(server):
    c = ObsController(ObsConfig(port=1, connect_timeout=0.5), lambda: "")
    t0 = time.time()
    c.start()
    assert time.time() - t0 < 0.5           # start() never blocks
    assert wait(lambda: c.state == "disconnected" and c.error)
    c.stop()


def test_request_timeout(server):
    cl = ObsClient()
    cl.connect("127.0.0.1", server.port, "pw", 2.0)
    server.response_delay = 1.0
    with pytest.raises(TimeoutError):
        cl.request("GetVersion", timeout=0.3)
    cl.close()


def test_switch_success_uses_uuid_and_targets_program(ctrl, server):
    assert ctrl.switch_program("Cam Side", 7)
    assert wait(lambda: ctrl.results)
    r = ctrl.results[0]
    assert isinstance(r, SceneSwitchConfirmed) and r.correlation_id == 7 and r.round_trip_ms >= 0
    assert server.program == "Cam Side"
    assert server.requests[-1][0] == "SetCurrentProgramScene" and "sceneUuid" in server.requests[-1][1]
    time.sleep(0.2)
    assert ctrl.external == []               # our own change is not an override


def test_switch_failure_reported(ctrl, server):
    server.fail_switches = True
    assert ctrl.switch_program("Cam Detail", 3)
    assert wait(lambda: ctrl.results)
    assert isinstance(ctrl.results[0], SceneSwitchFailed)


def test_switch_to_unknown_scene_refused_locally(ctrl, server):
    n = len(server.requests)
    assert not ctrl.switch_program("Nope", 1)
    assert len(server.requests) == n


def test_external_change_detected(ctrl, server):
    server.operator_switch("Cam Detail")
    assert wait(lambda: ctrl.external)
    assert ctrl.external[0].scene == "Cam Detail"


def test_rename_and_removal_tracked(ctrl, server):
    server.rename_scene("Cam Side", "Cam Side L")
    assert wait(lambda: ctrl.renames == [("Cam Side", "Cam Side L")])
    assert "Cam Side L" in ctrl.status().scenes
    server.remove_scene("BRB")
    assert wait(lambda: "BRB" not in ctrl.status().scenes)


def test_studio_mode_preview_untouched():
    s = FakeObsServer(scenes=["A", "B"], studio_mode=True).start()
    c = ObsController(ObsConfig(port=s.port), lambda: "")
    c.on_switch_result = lambda e: None
    c.start()
    try:
        assert wait(lambda: c.connected)
        assert c.status().studio_mode
        c.switch_program("B", 1)
        assert wait(lambda: s.program == "B")
        assert s.preview == "A"
        assert all(r[0] != "SetCurrentPreviewScene" for r in s.requests)
    finally:
        c.stop()
        s.stop()


def test_reconnect_after_drop_and_obs_restart(server):
    c = ObsController(ObsConfig(port=server.port), lambda: "pw")
    c.start()
    try:
        assert wait(lambda: c.connected)
        server.refuse_connections = True
        server.disconnect_all()
        assert wait(lambda: not c.connected, 2)
        assert not c.switch_program("Cam Side", 1)      # no commands while disconnected
        server.refuse_connections = False
        assert wait(lambda: c.connected, 20)
        assert c.scenes_valid                            # scenes re-validated after reconnect
    finally:
        c.stop()


def test_obs_shutdown_during_session():
    s = FakeObsServer(scenes=["A", "B"]).start()
    c = ObsController(ObsConfig(port=s.port, connect_timeout=0.5), lambda: "")
    c.start()
    try:
        assert wait(lambda: c.connected)
        s.stop()
        assert wait(lambda: not c.connected, 3)
        assert c.state in ("disconnected", "connecting")
    finally:
        c.stop()


# ----------------------------------------------------------------------------- P0: request lifecycle
from dj_director.core.events import ProgramChanged, SceneSwitchCancelled  # noqa: E402


def test_queued_command_superseded_by_a_newer_cut_is_cancelled(ctrl, server):
    server.response_delay = 0.4                    # first command in flight, the next ones queue up
    ctrl.switch_program("Cam Detail", 1)
    time.sleep(0.1)
    ctrl.switch_program("Cam Side", 2)             # queued
    ctrl.switch_program("BRB", 3)                  # replaces 2 before it is sent
    assert wait(lambda: len(ctrl.results) >= 3, 5)
    by_id = {r.correlation_id: r for r in ctrl.results}
    assert isinstance(by_id[2], SceneSwitchCancelled) and "superseded" in by_id[2].reason
    assert isinstance(by_id[1], SceneSwitchConfirmed) and isinstance(by_id[3], SceneSwitchConfirmed)
    assert [d.get("sceneUuid") for t, d in server.requests if t == "SetCurrentProgramScene"] == [
        server._uuid("Cam Detail"), server._uuid("BRB")]       # "Cam Side" was never sent


def test_manual_change_cancels_the_queued_cut(ctrl, server):
    server.response_delay = 0.4
    ctrl.switch_program("Cam Detail", 1)
    time.sleep(0.1)
    ctrl.switch_program("Cam Side", 2)             # still queued when the operator acts
    ctrl.cancel_queued("manual change in OBS")
    assert wait(lambda: any(isinstance(r, SceneSwitchCancelled) for r in ctrl.results), 3)
    time.sleep(0.8)
    sent = [d.get("sceneUuid") for t, d in server.requests if t == "SetCurrentProgramScene"]
    assert server._uuid("Cam Side") not in sent


def test_timeout_then_late_apply_is_not_mistaken_for_the_operator(server):
    c = ObsController(ObsConfig(port=server.port, request_timeout=0.3, connect_timeout=2.0), lambda: "pw")
    c.results, c.external, c.programs = [], [], []
    c.on_switch_result = c.results.append
    c.on_external_change = c.external.append
    c.on_program_changed = c.programs.append
    c.start()
    try:
        assert wait(lambda: c.connected)
        server.response_delay = 0.8                # OBS answers after our timeout, but does switch
        c.switch_program("Cam Side", 5)
        assert wait(lambda: c.results, 3)
        assert isinstance(c.results[0], SceneSwitchFailed) and "timed out" in c.results[0].error
        assert wait(lambda: any(p.scene == "Cam Side" for p in c.programs), 3)
        late = [p for p in c.programs if p.scene == "Cam Side"][0]
        assert late.ours and c.external == []
    finally:
        server.response_delay = 0.0
        c.stop()


def test_missed_program_event_is_reconciled_by_polling(ctrl, server):
    server.silent_switch("Cam Detail")             # Program changed, event lost
    assert wait(lambda: ctrl.external, 8)
    assert ctrl.external[0].scene == "Cam Detail" and ctrl.status().program_scene == "Cam Detail"
