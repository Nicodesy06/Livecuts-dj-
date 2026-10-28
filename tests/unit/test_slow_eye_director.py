"""0.5: the slow eye as a director (edit context, editorial verdict, motion) and the post-set review."""

import ast
import json
from pathlib import Path

import numpy as np
import pytest

from dj_director.config.schema import AppConfig, CameraConfig, CameraRole, ShotSize, Subject, ViewAngle
from dj_director.review import critic
from dj_director.review.critic import load_session, measure, review_session, suggest
from dj_director.review.journal import FrameJournal, frames_dir
from dj_director.ui import i18n
from dj_director.ui.i18n import IT, tr_reason
from dj_director.visual.interface import EditorHint
from dj_director.visual.judge import CameraJudgement, JudgeResult, SceneJudge, Tile, build_prompt, parse_result
from dj_director.visual.providers import JUDGE_SCHEMA, ProviderError
from dj_director.visual.vision import VisionService, framing_text
from tests.unit.test_engine import make, run, shots, switches
from tests.unit.test_vision import FakeProvider, feed, frame

TILES = [Tile("Cam M", "MASTER", frame(), "wide, dj, front", 0.05, 0.0),
         Tile("Cam D", "DETAIL", frame(), "close, hands, top", 0.62, 0.5),
         Tile("Cam C", "CROWD", frame()), Tile("Cam X", "SIDE", None)]


def cam_item(n, usable=True, interest=0.5, composition=0.7, moment="none"):
    return {"n": n, "usable": usable, "interest": interest, "composition": composition, "moment": moment,
            "issue": "" if usable else "blocked", "what": "x"}


# ----------------------------------------------------------------------------- verdict
def test_schema_asks_for_an_editorial_verdict():
    props = JUDGE_SCHEMA["properties"]
    assert {"hold", "next_pick", "drop_pick", "reason", "note"} <= set(JUDGE_SCHEMA["required"])
    item = props["cameras"]["items"]
    assert {"composition", "moment"} <= set(item["required"])
    assert "hands_on_mixer" in item["properties"]["moment"]["enum"]


def test_parse_editorial_verdict_validates_everything():
    data = {"cameras": [cam_item(1, composition=3.0, moment="dance"), cam_item(2, moment="hands_on_mixer"),
                        cam_item(3, usable=False)],
            "hold": False, "next_pick": 2, "drop_pick": 3, "reason": "hands on the filter; now", "note": "n"}
    r = parse_result(data, TILES, 5.0, 1.0, "m", on_air="Cam M")
    assert r.cameras["Cam M"].composition == 1.0 and r.cameras["Cam M"].moment == "none"   # clamped / unknown
    assert r.cameras["Cam D"].moment == "hands_on_mixer"
    assert r.next_pick == "Cam D" and r.hold is None and r.drop_pick is None   # drop pick not usable
    assert ";" not in r.reason                        # never splits the engine's reason text
    r = parse_result({**data, "next_pick": 3}, TILES, 5.0, 1.0, "m", on_air="Cam M")
    assert r.next_pick is None                        # unusable pick dropped
    r = parse_result({**data, "next_pick": 1}, TILES, 5.0, 1.0, "m", on_air="Cam M")
    assert r.hold == "Cam M" and r.next_pick is None  # "the best shot is on air" = stay on it
    r = parse_result({**data, "hold": True, "next_pick": 0}, TILES, 5.0, 1.0, "m", on_air="Cam X")
    assert r.hold is None                             # no picture of what is on air: no hold
    old = parse_result({"cameras": [{"n": 1, "usable": True, "interest": 0.4, "issue": "", "what": "x"}],
                        "drop_pick": 0, "note": ""}, TILES, 0, 0, "m")
    assert old.cameras["Cam M"].composition == 0.6 and old.next_pick is None      # older answers still parse


def test_prompt_gives_the_edit_context_and_motion():
    ctx = {"state": "Stable groove", "bpm": 124, "beat_conf": 0.9, "gesture": "cutting an EQ band",
           "on_air": "Cam M", "on_air_for": 33.0, "on_air_window": "30-50 s",
           "recent": [("Cam D", 9.0), ("Cam M", 41.0), ("Cam C", 12.0)], "next": "cut to Cam D in 1.2 s",
           "coverage": "master 71%, detail 12%, side 17%"}
    p = build_prompt(TILES, ctx, "it")
    assert "1. Cam M (master; wide, dj, front) - motion still (0.05)" in p
    assert "2. Cam D (detail; close, hands, top) - motion strong (0.62), pulsing on the beat" in p
    assert "4. Cam X (side) - no picture" in p
    assert "On air: Cam M for 33 s (planned 30-50 s)." in p
    assert "Recent shots, oldest first: Cam D 9s, Cam M 41s, Cam C 12s." in p
    assert "working the mixer: cutting an EQ band" in p and "Already planned: cut to Cam D" in p
    assert "Italian" in p and "'reason'" in p


def test_judge_passes_what_is_on_air_to_the_verdict():
    ans = {"cameras": [cam_item(1, moment="crowd_peak")], "hold": True, "next_pick": 0, "drop_pick": 0,
           "reason": "crowd jumping", "note": ""}
    j = SceneJudge(FakeProvider(ans), lambda: TILES, lambda: {"on_air": "Cam M"}, interval=5)
    r = j.look_once()
    assert r.hold == "Cam M" and r.on_air == "Cam M" and r.reason == "crowd jumping"


# ----------------------------------------------------------------------------- vision service
def _service(clock, influence=1.0):
    cfg = AppConfig(cameras=[CameraConfig("M", role=CameraRole.MASTER, is_master=True, shot_size=ShotSize.WIDE,
                                          subject=Subject.DJ, view_angle=ViewAngle.FRONT, source_group="cam1"),
                             CameraConfig("D", role=CameraRole.DETAIL, crop_zoom=1.5, source_group="cam1")])
    cfg.vision.ai_influence = influence
    return VisionService(cfg, lambda: "", lambda: "", lambda: [("M", "MASTER"), ("D", "DETAIL")],
                         lambda: {}, clock=clock)


def test_framing_text_and_tiles_carry_metadata_and_motion():
    assert framing_text(CameraConfig("x")) == ""
    now = {"t": 0.0}
    vs = _service(lambda: now["t"])
    now["t"] = feed(vs.fast, "M", [frame(block_x=i * 12) for i in range(12)])
    tiles = {t.scene: t for t in vs._tiles()}
    assert tiles["M"].framing == "wide, dj, front, camera cam1"
    assert tiles["D"].framing == "camera cam1, crop x1.5"
    assert tiles["M"].motion is not None and tiles["M"].motion > 0 and tiles["D"].motion is None


def _with_verdict(vs, t, **kw):
    j = SceneJudge(FakeProvider(), lambda: [], lambda: {})
    cams = {"M": CameraJudgement(True, 0.5, "", "", 0.9, "crowd_peak"), "D": CameraJudgement(True, 0.5, "", "")}
    j.result = JudgeResult(t, cams, None, "", 1.0, "m", **kw)
    vs.judge = j


def test_editor_hint_is_fresh_weighted_and_never_points_at_a_vetoed_camera():
    now = {"t": 0.0}
    vs = _service(lambda: now["t"])
    t = feed(vs.fast, "D", [frame(block_x=i * 10) for i in range(10)])
    now["t"] = t
    assert vs.editor_hint() is None                               # no slow eye
    _with_verdict(vs, t, next_pick="D", reason="hands on the mixer")
    assert vs.editor_hint() == EditorHint("D", None, "hands on the mixer", 1.0)
    assert vs.score("M") == pytest.approx(0.6 * 0.1 + 0.3 * 0.3 + 0.1)      # composition and moment count
    vs.cfg.vision.ai_influence = 0.0
    assert vs.editor_hint() is None                               # advice switched off: vetoes only
    vs.cfg.vision.ai_influence = 1.6
    now["t"] = feed(vs.fast, "D", [frame(0.0)] * 10, t0=t)        # the picked camera goes black
    _with_verdict(vs, now["t"], next_pick="D", reason="x")
    assert vs.editor_hint() is None
    now["t"] += 60
    assert vs.editor_hint() is None                               # stale verdict


# ----------------------------------------------------------------------------- engine
def test_ai_pick_is_one_argument_among_others():
    base = [d.candidate_camera for d in switches(run(make(4), 900))]
    e = make(4)
    e.editor_hint = lambda: EditorHint("B", None, "crowd hands up", 1.0)
    ds = run(e, 900)
    seq = [d.candidate_camera for d in switches(ds)]
    assert seq.count("B") > base.count("B")
    assert "D" in seq and seq.count("M") >= len(seq) // 2 - 1    # coverage and the master still rule
    assert any("AI suggests it: crowd hands up" in d.reason for d in switches(ds))
    off = make(4)
    off.editor_hint = lambda: EditorHint("B", None, "x", 0.0)
    assert [d.candidate_camera for d in switches(run(off, 900))] == base


def test_ai_hold_lets_a_shot_breathe_but_never_beyond_its_window():
    e = make(4)
    e.editor_hint = lambda: EditorHint(None, e.current, "DJ gesture", 1.0)
    ds = run(e, 900)
    base = shots(run(make(4), 900), 900)
    held = shots(ds, 900)
    for cam, dur in held[:-1]:
        sp = e.spec(e.reg.get(cam))
        assert dur <= sp.pref_max + 1.0 and dur <= sp.cap
    mean = lambda xs, c: np.mean([d for n, d in xs[:-1] if n == c])
    assert mean(held, "M") > mean(base, "M") and mean(held, "S") > mean(base, "S")
    assert any("continuity: AI: DJ gesture" in d.reason for d in ds)


def test_a_failing_hint_never_breaks_the_engine():
    e = make(3)
    e.editor_hint = lambda: 1 / 0
    assert len(switches(run(e, 300))) > 4


def test_ai_reasons_translate():
    i18n.set_language("it")
    try:
        assert tr_reason("entry: editorial, master 30s of ~30s (master 82%/70%); AI suggests it: mani sul mixer") \
            .endswith("suggerita dall'AI: mani sul mixer")
        assert tr_reason("insert 9s of ~17s planned (preferred 10-17s); continuity: AI: il pubblico salta") \
            .endswith("continuità: AI: il pubblico salta")
    finally:
        i18n.set_language("en")


# ----------------------------------------------------------------------------- post-set review
def _session(tmp_path, n=8, frames=True):
    """A decision log like the app writes it, with the stills of each cut."""
    log = tmp_path / "decisions-20261028-220000.jsonl"
    journal = FrameJournal(log)
    seq = ["Cam M", "Cam D", "Cam M", "Cam D", "Cam M", "Cam C", "Cam M", "Cam W"][:n]
    recs, t, prev = [], 100.0, None
    for i, scene in enumerate(seq, 1):
        recs.append({"kind": "DirectorDecision", "t": t, "action": "SWITCH", "current_camera": prev,
                     "candidate_camera": scene, "reason": "entry: editorial; cooldown passed",
                     "music_state": "stable_groove", "correlation_id": i})
        recs.append({"kind": "DirectorDecision", "t": t + 1, "action": "HOLD", "current_camera": scene,
                     "candidate_camera": None, "reason": "x", "music_state": "stable_groove", "correlation_id": 0})
        if frames:
            b, a = journal.save(i, frame(0.3) if prev else None, frame(0.6))
            recs.append({"kind": "ShotFrames", "t": t, "correlation_id": i, "scene": scene, "previous": prev,
                         "before": b, "after": a})
        prev = scene
        t += 2.0 if i == 3 else 12.0
    recs.append({"kind": "DirectorDecision", "t": t, "action": "OVERRIDE", "current_camera": prev,
                 "candidate_camera": "Cam M", "reason": "manual change", "music_state": "stable_groove"})
    log.write_text("\n".join(json.dumps(r) for r in recs) + "\nnot json\n", "utf-8")
    journal.close()
    return log


def test_journal_writes_small_stills_next_to_the_log(tmp_path):
    log = tmp_path / "decisions-x.jsonl"
    j = FrameJournal(log)
    b, a = j.save(7, None, frame(0.5))
    j.close()
    assert b == "" and a == "decisions-x-frames/0007-after.jpg"
    assert (tmp_path / a).read_bytes()[:2] == b"\xff\xd8" and frames_dir(log).is_dir()


def test_session_loads_cuts_durations_and_rule_checks(tmp_path):
    rv = load_session(_session(tmp_path))
    measure(rv, [CameraConfig("Cam D", shot_size=ShotSize.WIDE, source_group="c"),
                 CameraConfig("Cam M", shot_size=ShotSize.WIDE, source_group="c")])
    assert [c.scene for c in rv.cuts] == ["Cam M", "Cam D", "Cam M", "Cam D", "Cam M", "Cam C", "Cam M", "Cam W",
                                          "Cam M"]
    assert rv.cuts[-1].manual and rv.cuts[0].previous is None and rv.cuts[0].prev_duration == 0
    assert rv.cuts[3].prev_duration == pytest.approx(2.0) and "very short previous shot" in rv.cuts[3].flags
    assert "A-B-A-B" in rv.cuts[3].flags
    assert "jump cut (framing metadata)" in rv.cuts[1].flags          # same camera, same scale
    assert rv.metrics["cuts"] == 9 and rv.metrics["manual"] == 1
    assert rv.cuts[1].after.endswith("0002-after.jpg") and rv.cuts[1].before.endswith("0002-before.jpg")


class ReviewProvider:
    model = "fake-editor"

    def __init__(self, fail_after=None):
        self.calls, self.fail_after, self.prompts = 0, fail_after, []

    def judge(self, image, system, prompt, timeout, schema=None):
        assert schema is critic.REVIEW_SCHEMA and image[:2] == b"\xff\xd8" and "senior multicamera editor" in system
        self.calls += 1
        self.prompts.append(prompt)
        if self.fail_after is not None and self.calls > self.fail_after:
            raise ProviderError("Ollama not reachable")
        n = prompt.count("\nCut ") + prompt.startswith("Cut ")
        cuts = [{"k": k, "verdict": "bad" if k % 2 else "good", "problems": ["jump_cut", "nonsense"],
                 "comment": "troppo simile"} for k in range(1, n + 1)]
        return {"cuts": cuts + [{"k": 99, "verdict": "good", "problems": [], "comment": ""}], "note": "ok"}


def test_review_with_the_ai_editor_writes_a_report(tmp_path):
    prov = ReviewProvider()
    i18n.set_language("it")
    try:
        rv, out = review_session(_session(tmp_path), prov, language="it")
    finally:
        i18n.set_language("en")
    judged = rv.reviewed()
    assert prov.calls == 2 and len(judged) == 7                       # 7 director cuts with a previous shot
    assert all(c.problems == ("jump_cut",) for c in judged if c.verdict == "bad")
    assert "Cut 1 at 0:12: Cam M (on air 12 s) -> Cam D (then on air 12 s)" in prov.prompts[0]
    assert "Automatic checks: very short previous shot, A-B-A-B" in prov.prompts[0]
    assert any("quasi uguali" in s for s in rv.suggestions)          # suggestions in the UI language
    html = out.read_text("utf-8")
    assert out.name == "decisions-20261028-220000-review.html"
    assert "Revisione della serata" in html and "da evitare" in html and "0002-after.jpg" in html
    assert "fake-editor" in html and "troppo simile" in html


def test_review_survives_a_failing_ai_and_works_without_it(tmp_path):
    rv, out = review_session(_session(tmp_path), ReviewProvider(fail_after=1))
    assert rv.ai_error == "Ollama not reachable" and len(rv.reviewed()) == 6 and out.exists()
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    rv, out = review_session(_session(tmp_path / "x", frames=False), ReviewProvider())
    assert rv.reviewed() == [] and rv.model == "fake-editor" and out.exists()
    rv, _ = review_session(_session(tmp_path / "y"), None)
    assert rv.model == "" and rv.suggestions


def test_suggestions_are_deterministic_and_name_the_camera():
    rv = critic.Review(Path("x.jsonl"), [], 600.0)
    for i in range(6):
        c = critic.Cut(i + 1, i * 30.0, "Cam Side" if i % 2 else "Cam M", "Cam M", "", "stable_groove")
        if i % 2:
            c.problems = ("bad_framing",)
        rv.cuts.append(c)
    rv.metrics = {"cuts_per_minute": 2.5, "share": {"Cam M": 0.6, "Cam Side": 0.4}}
    out = suggest(rv)
    assert out == suggest(rv) and len(out) == 1 and "Cam Side" in out[0]
    rv.cuts = rv.cuts[:1]
    rv.cuts[0].problems = ()
    assert suggest(rv) == ["No recurring problem found."]


def test_every_review_string_is_translated():
    src = Path(critic.__file__).read_text("utf-8")
    keys = {n.args[0].value for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "tr" and n.args
            and isinstance(n.args[0], ast.Constant)}
    keys |= set(critic.VERDICT_LABEL.values()) | set(critic.PROBLEM_LABEL.values())
    keys |= {"very short previous shot", "A-B-A-B", "jump cut (framing metadata)",
             "front-back jump (framing metadata)"}
    assert sorted(k for k in keys if k not in IT) == []
