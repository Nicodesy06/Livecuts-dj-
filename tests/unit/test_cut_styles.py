"""0.6 cut styles: light zoom between shots and the occasional custom OBS transition."""

import json
import time

import pytest

from dj_director.config.schema import AppConfig, TransitionsConfig, from_dict, to_dict
from dj_director.core.events import DirectorAction, DirectorDecisionEvent, MusicState, TimingMode
from dj_director.director.cut_style import CUT, SPECIAL, ZOOM, CutStyler
from dj_director.obs.controller import TransitionInfo, transition_info
from dj_director.obs.motion import (ease_in, ease_out, original_fields, pick_item, zoom_fields, zoom_timing)
from tests.unit.test_engine import make

FULL = {"positionX": 0.0, "positionY": 0.0, "rotation": 0.0, "scaleX": 1.0, "scaleY": 1.0, "alignment": 5,
        "boundsType": "OBS_BOUNDS_NONE", "sourceWidth": 1920.0, "sourceHeight": 1080.0, "width": 1920.0,
        "height": 1080.0}


def centre(t, f):
    w, h = 1920 * f.get("scaleX", 1), 1080 * f.get("scaleY", 1)
    return f["positionX"] + w / 2, f["positionY"] + h / 2


# ----------------------------------------------------------------------------- geometry
def test_zoom_scales_about_the_item_centre_and_never_shows_a_border():
    f = zoom_fields(FULL, 1.04)
    assert f["scaleX"] == pytest.approx(1.04) and f["scaleY"] == pytest.approx(1.04)
    assert centre(FULL, f) == pytest.approx((960.0, 540.0))
    assert f["positionX"] <= 0 and f["positionY"] <= 0                     # still covers the canvas
    assert zoom_fields(FULL, 1.0) == {"positionX": 0.0, "positionY": 0.0, "scaleX": 1.0, "scaleY": 1.0}


@pytest.mark.parametrize("align,pos", [(0, (960, 540)), (10, (1920, 1080)), (5, (0, 0))])
def test_zoom_respects_every_alignment(align, pos):
    t = dict(FULL, alignment=align, positionX=float(pos[0]), positionY=float(pos[1]))
    f = zoom_fields(t, 1.1)
    # the item's centre stays where it was, whatever point the position refers to
    off = {0: (0, 0), 10: (-1, -1), 5: (1, 1)}[align]
    cx = f["positionX"] + off[0] * 1920 * 1.1 / 2
    cy = f["positionY"] + off[1] * 1080 * 1.1 / 2
    assert (cx, cy) == pytest.approx((960.0, 540.0))


def test_zoom_with_bounds_and_refusals():
    b = dict(FULL, boundsType="OBS_BOUNDS_SCALE_INNER", boundsWidth=1920.0, boundsHeight=1080.0)
    f = zoom_fields(b, 1.05)
    assert f["boundsWidth"] == pytest.approx(2016.0) and "scaleX" not in f
    assert original_fields(b) == {"positionX": 0.0, "positionY": 0.0, "boundsWidth": 1920.0,
                                  "boundsHeight": 1080.0}
    assert zoom_fields(dict(FULL, rotation=90.0), 1.05) is None
    assert zoom_fields(dict(FULL, width=0, height=0, sourceWidth=0), 1.05) is None


def test_the_camera_is_the_largest_visible_item():
    cam = {"sceneItemId": 1, "sceneItemEnabled": True, "sceneItemTransform": FULL}
    logo = {"sceneItemId": 2, "sceneItemEnabled": True,
            "sceneItemTransform": dict(FULL, width=180.0, height=80.0)}
    hidden = {"sceneItemId": 3, "sceneItemEnabled": False,
              "sceneItemTransform": dict(FULL, width=4000.0, height=3000.0)}
    assert pick_item([logo, hidden, cam])["sceneItemId"] == 1
    assert pick_item([]) is None


def test_easing_and_timing():
    assert ease_in(0) == 0 and ease_in(1) == 1 and ease_out(0) == 0 and ease_out(1) == 1
    assert ease_in(0.5) < 0.5 < ease_out(0.5)                 # accelerate into the cut, decelerate out
    assert zoom_timing(60 / 124) == pytest.approx((0.2419, 0.4839), abs=1e-3)
    assert zoom_timing(0.0) == (0.3, 0.6) and zoom_timing(5.0) == (0.45, 0.9)


# ----------------------------------------------------------------------------- transitions
def test_transition_change_point():
    st = {"transitionName": "S", "transitionKind": "obs_stinger_transition", "transitionDuration": 1200,
          "transitionSettings": {"transition_point": 450, "tp_type": 0}}
    assert transition_info(st).lead == pytest.approx(0.45)
    st["transitionSettings"] = {"transition_point": 30, "tp_type": 1}
    assert transition_info(st, fps=60).point_ms == pytest.approx(500.0)
    assert transition_info({"transitionName": "F", "transitionKind": "fade_transition",
                            "transitionDuration": 400}).point_ms == 200
    assert transition_info({"transitionName": "C", "transitionKind": "cut_transition"}).lead == 0


def test_config_roundtrip_and_validation():
    cfg = AppConfig(transitions=TransitionsConfig(zoom=True, zoom_amount=0.06, special=True,
                                                  special_name="Stinger DJ", special_every=10))
    assert from_dict(AppConfig, to_dict(cfg)).transitions == cfg.transitions
    issues = []
    bad = from_dict(TransitionsConfig, {"zoom_amount": 0.5, "zoom_style": "spin", "special_every": 1}, issues)
    assert bad.zoom_amount == 0.10 and bad.zoom_style == "through" and bad.special_every == 3 and len(issues) == 3
    assert from_dict(AppConfig, {"obs": {}}).transitions == TransitionsConfig()          # old configs


# ----------------------------------------------------------------------------- styler
def dec(reason="entry: editorial, master 30s of ~30s", timing=TimingMode.BEAT):
    return DirectorDecisionEvent(0.0, DirectorAction.SCHEDULE, "M", "D", reason, 0.8, 30.0,
                                 MusicState.STABLE_GROOVE, timing=timing)


def styles(styler, decisions, wait=1.0, lead=0.45):
    out = []
    for d in decisions:
        s = styler.plan(d, wait, lead)
        styler.on_cut(s)
        out.append(s)
    return out


def test_special_about_once_every_n_cuts_and_zoom_on_the_others():
    st = CutStyler(TransitionsConfig(zoom=True, special=True, special_name="S", special_every=25,
                                     special_on_moments=False))
    seq = styles(st, [dec()] * 100)
    idx = [i for i, s in enumerate(seq) if s == SPECIAL]
    assert idx == [24, 49, 74, 99] and set(seq) == {ZOOM, SPECIAL}


def test_special_prefers_musical_moments_after_sixty_percent():
    st = CutStyler(TransitionsConfig(special=True, special_name="S", special_every=25))
    ds = [dec()] * 16 + [dec("entry: drop reveal on Cam W (0.90)")] + [dec()] * 30
    seq = styles(st, ds)
    assert seq.index(SPECIAL) == 16                        # the drop, not cut 25
    st = CutStyler(TransitionsConfig(special=True, special_name="S", special_every=25))
    seq = styles(st, [dec()] * 5 + [dec("x", TimingMode.PHRASE)] + [dec()] * 20)
    assert seq.index(SPECIAL) == 24                        # too early for a moment: waits for the count


def test_special_never_on_gestures_or_emergencies_and_only_when_on_time():
    cfg = TransitionsConfig(zoom=True, special=True, special_name="S", special_every=5)
    st = CutStyler(cfg)
    st.since_special = 10
    assert st.plan(dec("entry: DJ cuts the lows (0.85)"), 1.0, 0.4) == ZOOM
    assert st.plan(dec("exit: shot unusable (black frame); back to anchor M"), 1.0, 0.4) == CUT
    st.since_special = 4
    assert st.plan(dec(), 0.2, 0.4) == ZOOM                # not enough time to land on the beat
    st.since_special = 7
    assert st.plan(dec(), 0.2, 0.4) == SPECIAL             # 1.5 x the count: allowed even if late
    assert st.plan(dec(), 1.0, None) == ZOOM               # transition not read from OBS yet
    assert CutStyler(TransitionsConfig(zoom=True)).plan(dec(), 0.05, None) == CUT   # no time to set up
    assert CutStyler(TransitionsConfig()).plan(dec(), 1.0, 0.1) == CUT              # both off


def test_engine_sends_a_scheduled_cut_earlier():
    from tests.unit.test_engine import snap
    e = make(3)
    t = 0.0
    while t < 120:
        d = e.tick(t, snap(t))
        if d.action == DirectorAction.SCHEDULE:
            p = e.pending
            before = p.fire_at
            e.advance_pending(p.correlation_id + 1, 0.4, t)          # another cut: untouched
            assert p.fire_at == before
            e.advance_pending(p.correlation_id, 0.4, t)
            assert p.fire_at == pytest.approx(max(t, before - 0.4))
            return
        t = round(t + 0.05, 6)
    pytest.fail("no scheduled cut")


# ----------------------------------------------------------------------------- against the fake OBS
pytest.importorskip("websockets")


@pytest.fixture
def obs():
    from dj_director.obs.fake_server import FakeObsServer
    srv = FakeObsServer(scenes=["A", "B", "C"]).start()
    yield srv
    srv.stop()


def wait(cond, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_animator_zooms_only_the_camera_and_always_puts_it_back(obs, tmp_path):
    from dj_director.obs.motion import ZoomAnimator
    restore = tmp_path / "zoom_restore.json"
    z = ZoomAnimator("127.0.0.1", obs.port, lambda: "", restore)
    z.start()
    try:
        assert wait(lambda: z.state == "running")
        orig = dict(obs.scene_items("B")[0]["sceneItemTransform"])
        z.prepare("B", 0.05)                                        # off air: ready at +5 %
        assert wait(lambda: obs.scene_items("B")[0]["sceneItemTransform"]["scaleX"] == pytest.approx(1.05))
        assert json.loads(restore.read_text())["B"]["transform"]["scaleX"] == 1.0   # crash safety
        z.push("A", 0.05, 0.25)
        z.settle("B", 0.3)
        assert wait(lambda: obs.scene_items("B")[0]["sceneItemTransform"]["scaleX"] == 1.0, 3)
        assert wait(lambda: obs.scene_items("A")[0]["sceneItemTransform"]["scaleX"] == pytest.approx(1.05), 3)
        z.reset("A", 0.1)
        assert wait(lambda: obs.scene_items("A")[0]["sceneItemTransform"]["scaleX"] == 1.0, 3)
        assert obs.scene_items("B")[0]["sceneItemTransform"] == orig
        steps = [f["scaleX"] for s, i, f in obs.transform_log if s == "A"]
        assert len(steps) >= 5 and max(steps) == pytest.approx(1.05)          # an animation, not a jump
        assert all(i == 1 for _, i, _ in obs.transform_log)                    # the logo never moves
        assert not restore.exists()
    finally:
        z.stop()


def test_a_zoom_left_by_a_crash_is_repaired_at_the_next_start(obs, tmp_path):
    from dj_director.obs.motion import ZoomAnimator
    restore = tmp_path / "zoom_restore.json"
    obs.scene_items("C")[0]["sceneItemTransform"].update(scaleX=1.07, scaleY=1.07, positionX=-67.2)
    restore.write_text(json.dumps({"C": {"sceneItemId": 1, "transform": {"positionX": 0.0, "positionY": 0.0,
                                                                         "scaleX": 1.0, "scaleY": 1.0}}}))
    z = ZoomAnimator("127.0.0.1", obs.port, lambda: "", restore)
    z.start()
    try:
        assert wait(lambda: obs.scene_items("C")[0]["sceneItemTransform"]["scaleX"] == 1.0)
        assert wait(lambda: not restore.exists())
    finally:
        z.stop()


def test_custom_transition_only_for_its_cut(obs):
    from dj_director.config.schema import ObsConfig
    from dj_director.obs.controller import ObsController
    obs.transition = "Fade"
    c = ObsController(ObsConfig(port=obs.port), lambda: "")
    results = []
    c.on_switch_result = results.append
    c.start()
    try:
        assert wait(lambda: c.can_switch())
        assert "Stinger DJ" in c.transitions()
        info = c.probe_transition("Stinger DJ")
        assert info.lead == pytest.approx(0.45) and obs.transition == "Fade"      # put back after reading
        assert c.transition == "Fade" and c.transition_ms == 300
        c.switch_program("B", 1, info)
        assert wait(lambda: len(results) == 1)
        c.switch_program("C", 2)                                   # a normal cut right after
        assert wait(lambda: len(results) == 2)
        assert obs.program_transitions == [("B", "Stinger DJ"), ("C", "Fade")]
        c.switch_program("A", 3, info)
        assert wait(lambda: len(results) == 3) and wait(lambda: obs.transition == "Fade", 3)
    finally:
        c.stop()
    assert obs.transition == "Fade"
