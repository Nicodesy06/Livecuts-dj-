"""Shot continuity, framing metadata and the shot planner (0.4 Director 2.0)."""

import dataclasses

import pytest

from dj_director.config.presets import PRESETS
from dj_director.config.schema import (CameraConfig, CameraRole, ScreenDirection, ShotSize, Subject, ViewAngle,
                                       from_dict, to_dict)
from dj_director.director.cameras import Camera, CameraRegistry
from dj_director.director.continuity import assess, is_bridge, sequence_report
from dj_director.director.engine import DirectorEngine, EngineSettings
from tests.unit.test_engine import run, snap, switches

S, J, V, D = ShotSize, Subject, ViewAngle, ScreenDirection


def cam(name, role=CameraRole.SIDE, **kw):
    return Camera.from_config(CameraConfig(name, role=role, **kw))


# ----------------------------------------------------------------------------- rules
def test_unknown_metadata_contributes_nothing():
    c = assess(cam("A"), cam("B"))
    assert c.penalty == 0 and c.bonus == 0 and c.notes == []


def test_same_visual_group_is_near_identical():
    c = assess(cam("A", visual_group="front"), cam("B", visual_group="front"))
    assert c.jump_cut and "same visual group" in c.notes


@pytest.mark.parametrize("a,b,jump,note", [
    (S.WIDE, S.WIDE, True, "same camera, same scale (jump cut)"),
    (S.WIDE, S.MEDIUM, True, "same camera, small scale change (jump-cut risk)"),
    (S.WIDE, S.CLOSE, False, "same camera, clear scale change"),
])
def test_crops_of_one_source_are_scale_variations(a, b, jump, note):
    c = assess(cam("W", shot_size=a, source_group="cam1"), cam("M", shot_size=b, source_group="cam1"))
    assert c.jump_cut == jump and note in c.notes
    assert "different angle" not in c.notes            # a crop is not a new perspective


def test_similar_framing_from_another_camera_is_a_jump_cut_risk():
    a = cam("A", shot_size=S.MEDIUM, subject=J.DJ, view_angle=V.FRONT)
    b = cam("B", shot_size=S.MEDIUM, subject=J.DJ, view_angle=V.FRONT_RIGHT)    # 45 deg: different enough
    c = cam("C", shot_size=S.MEDIUM, subject=J.DJ, view_angle=V.FRONT)
    assert not assess(a, b).jump_cut
    assert assess(a, c).jump_cut and "similar angle and scale (jump-cut risk)" in assess(a, c).notes


def test_axis_crossing_needs_a_bridge_but_is_not_a_rigid_rule():
    front = cam("F", shot_size=S.MEDIUM, subject=J.DJ, view_angle=V.FRONT)
    back = cam("B", shot_size=S.MEDIUM, subject=J.DJ, view_angle=V.BACK)
    top = cam("T", role=CameraRole.OVERHEAD, shot_size=S.CLOSE, subject=J.HANDS, view_angle=V.TOP)
    crowd = cam("C", shot_size=S.WIDE, subject=J.CROWD, view_angle=V.BACK)
    assert "crosses the axis front-back" in assess(front, back).notes
    assert is_bridge(top) and is_bridge(crowd)
    assert "axis change through a bridge" in assess(front, crowd).notes          # wide crowd shot
    assert assess(front, back, editorial_exception=True).penalty < assess(front, back).penalty
    assert assess(top, back).penalty == 0                                        # coming from overhead


def test_screen_direction_flip_and_meaningful_differences():
    l = cam("L", shot_size=S.MEDIUM, subject=J.DJ, view_angle=V.LEFT, screen_direction=D.RIGHT)
    r = cam("R", shot_size=S.MEDIUM, subject=J.DJ, view_angle=V.RIGHT, screen_direction=D.LEFT)
    assert "screen direction flips" in assess(l, r).notes
    wide = cam("W", shot_size=S.WIDE, subject=J.DJ, view_angle=V.FRONT)
    hands = cam("H", shot_size=S.CLOSE, subject=J.HANDS, view_angle=V.RIGHT)
    good = assess(wide, hands)
    assert good.penalty == 0 and good.bonus > 0.3
    assert {"clear scale change", "different angle", "different subject"} <= set(good.notes)


def test_repeating_the_same_transition_is_penalised():
    a, b = cam("A"), cam("B")
    assert "repeats the same transition" in assess(a, b, ["A", "B", "M", "A", "B", "M", "A"]).notes
    assert assess(a, b, ["A", "B", "M", "A"]).penalty == 0


def test_sequence_report_counts_problems():
    cams = {c.name: c for c in (cam("W", shot_size=S.WIDE, source_group="c1"),
                                cam("M", shot_size=S.MEDIUM, source_group="c1"),
                                cam("H", shot_size=S.CLOSE, subject=J.HANDS))}
    rep = sequence_report(["W", "M", "W", "H", "W"], cams)
    assert rep["jump_cuts"] == 2


# ----------------------------------------------------------------------------- metadata in the config
def test_metadata_roundtrip_and_old_configs_load():
    c = CameraConfig("CAM1 Medium", role=CameraRole.CROWD, shot_size=S.MEDIUM, subject=J.CROWD,
                     view_angle=V.BACK_LEFT, visual_group="g", source_group="cam1",
                     screen_direction=D.AWAY, crop_zoom=1.5)
    back = from_dict(CameraConfig, to_dict(c))
    assert back == c
    old = from_dict(CameraConfig, {"scene_name": "X", "role": "SIDE", "mean_duration": 9.0})
    assert old.shot_size == S.UNKNOWN and old.view_angle == V.UNKNOWN and old.source_group == ""
    issues = []
    bad = from_dict(CameraConfig, {"scene_name": "Y", "shot_size": "HUGE", "crop_zoom": 40}, issues)
    assert bad.shot_size == S.UNKNOWN and bad.crop_zoom == 8.0 and len(issues) == 2


# ----------------------------------------------------------------------------- engine with metadata
def framed_engine(params=None, seed=1):
    cfgs = [CameraConfig("W", role=CameraRole.MASTER, is_master=True, shot_size=S.WIDE, subject=J.DJ,
                         view_angle=V.FRONT, source_group="cam1"),
            CameraConfig("Wm", role=CameraRole.SIDE, shot_size=S.MEDIUM, subject=J.DJ, view_angle=V.FRONT,
                         source_group="cam1"),
            CameraConfig("H", role=CameraRole.DETAIL, shot_size=S.CLOSE, subject=J.HANDS, view_angle=V.TOP,
                         source_group="cam2"),
            CameraConfig("L", role=CameraRole.SIDE, shot_size=S.MEDIUM, subject=J.DJ, view_angle=V.LEFT,
                         source_group="cam3")]
    p = dataclasses.replace(PRESETS["natural"], **(params or {}))
    return DirectorEngine(CameraRegistry.from_configs(cfgs), p, EngineSettings(), seed=seed)


def test_planner_avoids_jump_cuts_when_better_alternatives_exist():
    e = framed_engine()
    ds = run(e, 1800)
    seq = [d.candidate_camera for d in switches(ds)]
    assert "Wm" not in seq                                   # the 1-step crop of the master is avoided
    old = framed_engine({"continuity_weight": 0.0, "contextual_grammar": False})
    seq_old = [d.candidate_camera for d in switches(run(old, 1800))]
    assert "Wm" in seq_old                                   # the old engine did cut to it


def test_jump_cut_is_still_possible_when_it_is_the_only_option():
    cfgs = [CameraConfig("W", role=CameraRole.MASTER, is_master=True, shot_size=S.WIDE, source_group="cam1"),
            CameraConfig("Wm", role=CameraRole.SIDE, shot_size=S.MEDIUM, source_group="cam1")]
    e = DirectorEngine(CameraRegistry.from_configs(cfgs), PRESETS["natural"], EngineSettings(), seed=1)
    seq = [d.candidate_camera for d in switches(run(e, 600))]
    assert "Wm" in seq                                       # never stuck: no alternative exists


def test_every_cut_carries_a_why_what_when_how_long_plan():
    e = framed_engine()
    sw = switches(run(e, 600))
    assert sw
    for d in sw[1:]:
        plan = dict(d.plan)
        assert set(plan) == {"why", "what", "when", "how_long"}
        assert d.candidate_camera in plan["what"] and plan["how_long"].endswith(("maximum", "s"))


def test_selection_is_reproducible():
    a = [(d.t, d.candidate_camera) for d in switches(run(framed_engine(seed=4), 900))]
    b = [(d.t, d.candidate_camera) for d in switches(run(framed_engine(seed=4), 900))]
    assert a == b


def test_works_with_one_two_three_cameras_without_any_metadata():
    for n in (1, 2, 3):
        cfgs = [CameraConfig(f"C{i}", role=(CameraRole.MASTER if i == 0 else CameraRole.SIDE), is_master=i == 0)
                for i in range(n)]
        e = DirectorEngine(CameraRegistry.from_configs(cfgs), PRESETS["natural"], EngineSettings(), seed=2)
        ds = run(e, 600)
        assert switches(ds)[0].candidate_camera == "C0"
        if n == 1:
            assert len(switches(ds)) == 1
        else:
            assert len(switches(ds)) > 4


# ----------------------------------------------------------------------------- digital crops (P4)
def test_crop_validation_warnings():
    from dj_director.config.framing import framing_warnings
    good = [CameraConfig("CAM1 W", shot_size=S.WIDE, source_group="cam1"),
            CameraConfig("CAM1 C", shot_size=S.CLOSE, source_group="cam1", crop_zoom=1.8)]
    assert framing_warnings(good) == []
    bad = [CameraConfig("A", shot_size=S.WIDE, source_group="cam1"),
           CameraConfig("B", shot_size=S.WIDE, source_group="cam1", crop_zoom=1.5),
           CameraConfig("C", source_group="cam1", crop_zoom=1.2),
           CameraConfig("Z", crop_zoom=3.0),
           CameraConfig("H", role=CameraRole.DETAIL, crop_zoom=1.4, source_group="cam2"),
           CameraConfig("off", crop_zoom=5.0, enabled=False)]
    w = " | ".join(framing_warnings(bad))
    assert "same scale (WIDE)" in w and "set the scale of every crop" in w
    assert "Z: digital crop without a physical camera" in w and "Z: digital zoom x3.0" in w
    assert "H: cropped detail" in w and "off" not in w
