"""Scene awareness (0.2): fast eye, mosaic, slow-eye parsing/providers, vision service, MIDI probe."""

import json
import math
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import numpy as np
import pytest

from dj_director.config.schema import AppConfig, CameraConfig, CameraRole
from dj_director.sensors.midi_probe import ProbeResult, describe, probe
from dj_director.visual import imaging
from dj_director.visual.fast import FastVisualAnalyzer
from dj_director.visual.judge import JudgeResult, SceneJudge, Tile, build_prompt, parse_result
from dj_director.visual.providers import JUDGE_SCHEMA, ClaudeProvider, OllamaProvider, ProviderError
from dj_director.visual.vision import VisionService

RNG = np.random.default_rng(7)
TEXTURE = RNG.integers(30, 220, size=(216, 384, 3)).astype(np.uint8)


def frame(brightness=1.0, block_x=None, roi_block=None, blur=False):
    img = TEXTURE.astype(np.float32)
    if blur:
        img[:] = img.mean(axis=(0, 1))
    if block_x is not None:
        img[60:120, block_x:block_x + 60] = 250
    if roi_block is not None:
        x, y = roi_block
        img[y:y + 30, x:x + 30] = 250
    return np.clip(img * brightness, 0, 255).astype(np.uint8)


def feed(fa, name, frames, t0=0.0, dt=0.25):
    t = t0
    for f in frames:
        fa.ingest(name, t, f)
        t += dt
    return t


# ----------------------------------------------------------------------------- fast eye
def test_black_camera_is_vetoed_only_after_it_persists():
    fa = FastVisualAnalyzer()
    t = feed(fa, "A", [frame()] * 8)
    t = feed(fa, "A", [frame(0.0)] * 3, t0=t)           # 0.75 s of black: not yet
    assert fa.issue("A", t) == ""
    t = feed(fa, "A", [frame(0.0)] * 6, t0=t)
    assert fa.issue("A", t) == "black frame"
    assert fa.activity("A", t) is None                   # no activity from an unusable shot
    t = feed(fa, "A", [frame(block_x=i * 10) for i in range(8)], t0=t)
    assert fa.issue("A", t) == ""                        # recovers


def test_a_strobe_flash_is_not_a_problem():
    fa = FastVisualAnalyzer()
    t = feed(fa, "A", [frame()] * 8)
    t = feed(fa, "A", [np.full_like(TEXTURE, 255)] * 2, t0=t)
    t = feed(fa, "A", [frame()] * 4, t0=t)
    assert fa.issue("A", t) == ""


def test_frozen_image_is_detected():
    fa = FastVisualAnalyzer()
    still = frame()
    t = feed(fa, "A", [frame(block_x=i * 5) for i in range(8)])
    t = feed(fa, "A", [still] * 30, t0=t)               # 7.5 s of identical frames
    assert fa.issue("A", t) == "frozen image"


def test_blur_is_relative_to_the_camera_own_sharpness():
    fa = FastVisualAnalyzer()
    t = feed(fa, "A", [frame(block_x=i * 5) for i in range(20)])
    assert fa.issue("A", t) == ""
    t = feed(fa, "A", [frame(blur=True)] * 10, t0=t)
    assert fa.issue("A", t) == "blurred or blocked"


def test_movement_is_measured_inside_the_region_of_interest():
    fa = FastVisualAnalyzer()
    fa.set_rois({"D": [0.0, 0.0, 0.3, 0.3]})
    # movement only outside the region: activity stays ~0
    t = feed(fa, "D", [frame(roi_block=(250 + (i % 2) * 40, 150)) for i in range(10)])
    assert fa.activity("D", t) < 0.05
    # movement inside the region (the mixer): activity rises
    t = feed(fa, "D", [frame(roi_block=(10 + (i % 2) * 40, 10)) for i in range(10)], t0=t)
    assert fa.activity("D", t) > 0.3
    assert fa.status("D", t).roi == (0.0, 0.0, 0.3, 0.3)


def test_stale_camera_gives_no_information_and_no_veto():
    fa = FastVisualAnalyzer()
    t = feed(fa, "A", [frame(0.0)] * 10)
    assert fa.issue("A", t) == "black frame"
    assert fa.issue("A", t + 10) == "" and fa.activity("A", t + 10) is None


def test_movement_on_the_beat_is_recognised():
    period = 60 / 124
    fa = FastVisualAnalyzer()
    fa.beat_period = period
    t, dt = 0.0, 1 / 7
    for i in range(70):                    # 10 s at 7 fps: a block that jumps on every beat
        phase = (t % period) / period
        fa.ingest("W", t, frame(block_x=60 if phase < 0.3 else 260))
        t += dt
    on_beat = fa.coherence("W", t)
    fb = FastVisualAnalyzer()
    fb.beat_period = period
    rng = np.random.default_rng(3)
    t2 = 0.0
    for i in range(70):                    # random movement, unrelated to the beat
        fb.ingest("W", t2, frame(block_x=int(rng.integers(0, 300))))
        t2 += dt
    assert on_beat > 0.5 and fb.coherence("W", t2) < on_beat * 0.6


# ----------------------------------------------------------------------------- imaging
def test_mosaic_numbers_tiles_and_marks_missing_pictures():
    m = imaging.mosaic([frame(), None, frame()], 192, 108)
    assert m.shape == (2 * 108 + 4, 2 * 192 + 4, 3)
    assert (m[5:9, 5:9] == 0).all() or (m[8:12, 8:12] == (255, 220, 0)).all()    # stamp in the corner
    jpg = imaging.encode(m, "JPG")
    img = imaging.decode_data_uri("data:image/jpg;base64," + __import__("base64").b64encode(jpg).decode())
    assert imaging.to_rgb(img).shape == m.shape


# ----------------------------------------------------------------------------- slow eye
TILES = [Tile("Cam M", "MASTER", frame()), Tile("Cam D", "DETAIL", frame()), Tile("Cam W", "WIDE", None)]


def test_parse_result_validates_and_clamps():
    data = {"cameras": [{"n": 1, "usable": True, "interest": 1.7, "issue": "", "what": "DJ"},
                        {"n": 2, "usable": False, "interest": 0.5, "issue": "blocked by a person", "what": "?"},
                        {"n": 3, "usable": True, "interest": 0.9, "issue": "", "what": "no picture sent"},
                        {"n": 9, "usable": True, "interest": 0.9, "issue": "", "what": "bogus"}],
            "drop_pick": 2, "note": "busy booth"}
    r = parse_result(data, TILES, 10.0, 1.2, "m")
    assert set(r.cameras) == {"Cam M", "Cam D"}
    assert r.cameras["Cam M"].interest == 1.0
    assert not r.cameras["Cam D"].usable and r.cameras["Cam D"].issue == "blocked by a person"
    assert r.drop_pick is None                       # the pick was not usable
    with pytest.raises(ProviderError):
        parse_result("nope", TILES, 0, 0, "m")


def test_prompt_lists_cameras_and_music_context():
    p = build_prompt(TILES, {"state": "Increasing activity", "bpm": 126, "on_air": "Cam M", "since_drop": 30},
                     "it")
    assert "1. Cam M (master)" in p and "3. Cam W (wide) - no picture" in p
    assert "126 BPM" in p and "Italian" in p


class FakeProvider:
    model = "fake"

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls = answer, error, 0

    def judge(self, image, system, prompt, timeout):
        self.calls += 1
        assert image[:2] == b"\xff\xd8"              # a JPEG mosaic
        if self.error:
            raise ProviderError(self.error)
        return self.answer


def test_judge_stores_a_result_that_expires():
    ans = {"cameras": [{"n": 1, "usable": True, "interest": 0.8, "issue": "", "what": "hands up"}],
           "drop_pick": 1, "note": "crowd jumping"}
    j = SceneJudge(FakeProvider(ans), lambda: TILES, lambda: {}, interval=5)
    r = j.look_once()
    assert r.drop_pick == "Cam M" and j.latest(r.t) is r and j.latest(r.t + j.ttl + 1) is None


def test_judge_errors_back_off_without_raising():
    prov = FakeProvider(error="Ollama not reachable")
    j = SceneJudge(prov, lambda: TILES, lambda: {}, interval=3)
    j.start()
    try:
        for _ in range(40):
            if j.state == "error":
                break
            threading.Event().wait(0.05)
        assert j.state == "error" and "Ollama" in j.error and j.latest(0) is None
    finally:
        j.stop()


# ----------------------------------------------------------------------------- providers
class _Ollama(BaseHTTPRequestHandler):
    think_supported = False
    chats = []

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/version":
            self._send(200, {"version": "0.12.9"})
        elif self.path == "/api/tags":
            self._send(200, {"models": [{"name": "qwen3-vl:4b"}, {"name": "moondream:latest"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/pull":
            lines = [{"status": "pulling manifest"}, {"status": "downloading", "total": 100, "completed": 40},
                     {"status": "downloading", "total": 100, "completed": 100}, {"status": "success"}]
            self._send(200, b"\n".join(json.dumps(x).encode() for x in lines), "application/x-ndjson")
        elif self.path == "/api/chat":
            _Ollama.chats.append(body)
            if "think" in body and not _Ollama.think_supported:
                self._send(400, {"error": "\"qwen\" does not support thinking"})
                return
            if body["model"] == "missing":
                self._send(404, {"error": "model not found"})
                return
            assert body["format"] == JUDGE_SCHEMA and body["messages"][1]["images"]
            content = json.dumps({"cameras": [], "drop_pick": 0, "note": "ok"})
            self._send(200, {"message": {"role": "assistant", "content": content}})
        else:
            self._send(404, {"error": "not found"})


@pytest.fixture
def ollama():
    srv = HTTPServer(("127.0.0.1", 0), _Ollama)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    _Ollama.chats = []
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_ollama_status_models_and_download(ollama):
    o = OllamaProvider(ollama)
    assert o.version() == "0.12.9"
    names = o.installed()
    assert o.has_model("qwen3-vl:4b", names) and o.has_model("moondream", names)
    assert not o.has_model("gemma3:4b", names)
    seen = []
    o.pull("gemma3:4b", lambda st, f: seen.append((st, f)))
    assert seen[-1][0] == "success" and (("downloading", 1.0) in seen)


def test_ollama_judge_retries_without_think_and_parses(ollama):
    o = OllamaProvider(ollama, "qwen3-vl:4b")
    out = o.judge(b"\xff\xd8jpeg", "sys", "prompt", timeout=5)
    assert out["note"] == "ok"
    assert "think" in _Ollama.chats[0] and "think" not in _Ollama.chats[-1]
    with pytest.raises(ProviderError, match="not installed"):
        OllamaProvider(ollama, "missing").judge(b"\xff\xd8", "s", "p", 5)


def test_ollama_absent_is_reported_not_raised():
    o = OllamaProvider("http://127.0.0.1:9")
    assert o.version(0.5) is None and o.installed(0.5) == []
    with pytest.raises(ProviderError):
        o.judge(b"x", "s", "p", 1)


def test_claude_provider_request_shape(monkeypatch):
    pytest.importorskip("anthropic")
    with pytest.raises(ProviderError, match="key"):
        ClaudeProvider("")
    p = ClaudeProvider("sk-test", "claude-opus-5-5")
    calls = {}

    class Msgs:
        def create(self, **kw):
            calls.update(kw)
            return SimpleNamespace(stop_reason="end_turn", content=[
                SimpleNamespace(type="text", text=json.dumps({"cameras": [], "drop_pick": 0, "note": "n"}))])

    p.client = SimpleNamespace(with_options=lambda **o: SimpleNamespace(beta=SimpleNamespace(messages=Msgs())))
    assert p.judge(b"\xff\xd8", "sys", "prompt", 10)["note"] == "n"
    assert calls["model"] == "claude-opus-5-5" and calls["fallbacks"] == "default"
    assert calls["output_config"]["format"]["schema"] == JUDGE_SCHEMA
    img = calls["messages"][0]["content"][0]
    assert img["type"] == "image" and img["source"]["media_type"] == "image/jpeg"

    class Refuse:
        def create(self, **kw):
            return SimpleNamespace(stop_reason="refusal", content=[])
    p.client = SimpleNamespace(with_options=lambda **o: SimpleNamespace(beta=SimpleNamespace(messages=Refuse())))
    with pytest.raises(ProviderError, match="declined"):
        p.judge(b"\xff\xd8", "sys", "prompt", 10)


# ----------------------------------------------------------------------------- vision service
def _service(clock):
    cfg = AppConfig(cameras=[CameraConfig("M", role=CameraRole.MASTER, is_master=True),
                             CameraConfig("D", role=CameraRole.DETAIL)])
    return VisionService(cfg, lambda: "", lambda: "", lambda: [("M", "MASTER"), ("D", "DETAIL")],
                         lambda: {}, clock=clock)


def test_service_combines_fast_veto_and_slow_verdict():
    now = {"t": 0.0}
    vs = _service(lambda: now["t"])
    t = feed(vs.fast, "D", [frame(0.0)] * 10)
    now["t"] = t
    assert vs.issue("D") == "black frame" and vs.score("D") == -1.0
    assert vs.score("M") is None                      # no information at all
    j = SceneJudge(FakeProvider(), lambda: [], lambda: {})
    j.result = JudgeResult(t, {"M": SimpleNamespace(usable=True, interest=0.9, issue="", what=""),
                               "D": SimpleNamespace(usable=False, interest=0.0, issue="ceiling", what="")},
                           "M", "note")
    vs.judge = j
    assert vs.score("M") == pytest.approx(0.6 * 0.5)
    t2 = feed(vs.fast, "D", [frame(block_x=i * 10) for i in range(10)], t0=t)
    now["t"] = t2
    j.result = JudgeResult(t2, j.result.cameras, "M", "note")
    assert vs.issue("D") == "AI: ceiling" and vs.reveal_hint() == "M"


# ----------------------------------------------------------------------------- MIDI probe
def test_midi_message_description():
    assert describe(0xB4, 31, 64) == "ch5 CC 31 = 64"
    assert describe(0x90, 11, 127) == "ch1 note on 11 vel 127"
    assert describe(0xF0, 0, 0).startswith("system")


def test_midi_probe_without_inputs_reports_honestly():
    import sys
    if sys.platform == "win32":
        pytest.skip("real MIDI devices may be present")
    r = probe(0.1)
    assert r.devices == [] and "No MIDI input" in r.summary()
    assert "does not send MIDI" in ProbeResult(["XDJ-RX3"], 30).summary()


def test_lights_flashing_on_the_beat_are_recognised():
    period = 60 / 124
    fa = FastVisualAnalyzer()
    fa.beat_period = period
    t = 0.0
    for i in range(80):                    # 10 s at 8 fps, brightness pulsing with the beat
        fa.ingest("W", t, frame(brightness=0.75 + 0.25 * math.cos(2 * math.pi * t / period),
                                block_x=(i * 37) % 300))
        t += 1 / 8
    assert fa.coherence("W", t) > 0.6
    fa.beat_period = period * 1.37         # a different tempo: no longer "on the beat"
    assert fa.coherence("W", t) < 0.3


def test_service_starts_when_enabled_later_and_restarts_on_changes(monkeypatch):
    started = []

    class FakeGrabber:
        def __init__(self, *a, **kw):
            self.rate = kw.get("rate")
            started.append(self)

        def start(self):
            pass

        def stop(self):
            pass
    monkeypatch.setattr("dj_director.visual.vision.FrameGrabber", FakeGrabber)
    import copy
    vs = _service(lambda: 0.0)
    cfg = copy.deepcopy(vs.cfg)
    vs.reconfigure(cfg)                            # nothing changed, but not running yet: starts
    assert len(started) == 1 and vs.grabber is started[0]
    vs.reconfigure(copy.deepcopy(cfg))             # unchanged and running: untouched
    assert len(started) == 1
    cfg2 = copy.deepcopy(cfg)
    cfg2.vision.rate = "high"
    vs.reconfigure(cfg2)                           # changed: restarted with the new rate
    assert len(started) == 2 and vs.grabber.rate == "high"
    cfg3 = copy.deepcopy(cfg2)
    cfg3.vision.enabled = False
    vs.reconfigure(cfg3)
    assert vs.grabber is None


# ----------------------------------------------------------------------------- quality gate (0.4)
def test_quality_states_unknown_healthy_failed():
    fa = FastVisualAnalyzer()
    assert fa.quality("A", 0.0) == "UNKNOWN"                       # never seen: not "good"
    t = feed(fa, "A", [frame(block_x=i * 10) for i in range(4)])
    assert fa.quality("A", t) == "UNKNOWN"                         # still learning
    t = feed(fa, "A", [frame(block_x=i * 10) for i in range(12)], t0=t)
    assert fa.quality("A", t) == "HEALTHY"
    t = feed(fa, "A", [frame(0.0)] * 10, t0=t)
    assert fa.quality("A", t) == "FAILED"
    assert fa.quality("A", t + 10) == "UNKNOWN"                    # stale


def test_camera_being_moved_is_detected_but_people_and_strobes_are_not():
    moving = FastVisualAnalyzer()
    t = feed(moving, "A", [frame(block_x=50)] * 12)
    t = feed(moving, "A", [np.roll(frame(block_x=50), 6 * (i + 1), axis=1) for i in range(10)], t0=t)
    assert moving.issue("A", t) == "camera moving"
    crowd = FastVisualAnalyzer()
    rng = np.random.default_rng(5)
    t = feed(crowd, "C", [frame(block_x=int(rng.integers(0, 300)), roi_block=(int(rng.integers(0, 340)),
                                                                              int(rng.integers(0, 180))))
                          for _ in range(30)])
    assert crowd.issue("C", t) == ""
    strobe = FastVisualAnalyzer()
    t = feed(strobe, "S", [frame(1.0 if i % 2 else 0.3, block_x=40) for i in range(30)])
    assert strobe.issue("S", t) == ""


def test_dark_club_lighting_is_not_a_black_or_blurred_camera():
    fa = FastVisualAnalyzer()
    t = feed(fa, "A", [frame(block_x=i * 5) for i in range(20)])
    t = feed(fa, "A", [frame(0.12, block_x=i * 5) for i in range(20)], t0=t)    # lights down
    assert fa.issue("A", t) == "" and fa.quality("A", t) == "HEALTHY"


def test_mixer_region_blocked_is_reported():
    fa = FastVisualAnalyzer()
    fa.set_rois({"D": [0.0, 0.0, 0.4, 0.4]})
    t = feed(fa, "D", [frame(roi_block=(10 + (i % 2) * 40, 10)) for i in range(16)])
    assert fa.issue("D", t) == ""
    covered = []
    for i in range(12):                               # someone stands in front of the mixer only
        f = frame(block_x=200 + (i % 2) * 20)
        f[:86, :153] = 90
        covered.append(f)
    t = feed(fa, "D", covered, t0=t)
    assert fa.issue("D", t) == "mixer not visible"
