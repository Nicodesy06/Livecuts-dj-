"""Generate the documentation screenshots (docs/images) with the real application.

The app runs exactly as shipped, connected to the bundled fake OBS WebSocket
server (dj_director.obs.fake_server) and fed with the synthetic test set
(dj_director.simulation.synth) in real time, so the dashboard shows genuine
analysis and director decisions. Only two things are staged and labelled as
examples in the docs: the audio-device list (the build machine has no sound
card, so a typical Behringer UCA222 setup is listed) and the result table of
the automatic source detection.

    QT_QPA_PLATFORM=offscreen python scripts/make_screenshots.py [--live-seconds 75]
Requires the dev extra (websockets).
"""

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["DJ_DIRECTOR_HOME"] = tempfile.mkdtemp(prefix="djd-shots-")

import numpy as np
import soundfile as sf
from PySide6.QtWidgets import QApplication, QScrollArea

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"

# --- staged device list (documented as an example) -------------------------------------------
import dj_director.audio.backends as backends
from dj_director.audio.backends import DeviceInfo

EXAMPLE_DEVICES = [
    DeviceInfo(1, "Microfono (USB Audio CODEC )", "Windows WASAPI", 2, 48000.0),
    DeviceInfo(2, "Microphone Array (Realtek(R) Audio)", "Windows WASAPI", 2, 48000.0),
    DeviceInfo(3, "Microfono (USB Audio CODEC )", "MME", 2, 44100.0),
]
EXAMPLE_LOOPBACK = [DeviceInfo(9, "Altoparlanti (USB Audio CODEC ) [Loopback]", "WASAPI loopback", 2,
                               48000.0, "loopback")]
backends.list_input_devices = lambda: list(EXAMPLE_DEVICES)
backends.list_loopback_devices = lambda: list(EXAMPLE_LOOPBACK)
backends.loopback_available = lambda: True

from dj_director.audio import probe  # noqa: E402
from dj_director.config.manager import ConfigManager  # noqa: E402
from dj_director.config.schema import AppConfig, CameraConfig, CameraRole  # noqa: E402
from dj_director.obs.fake_server import FakeObsServer  # noqa: E402
from dj_director.simulation.synth import MixMove, apply_mixer, generate  # noqa: E402
from dj_director.ui import i18n, settings_pages, wizard  # noqa: E402
from dj_director.ui import theme as T  # noqa: E402

for mod in (settings_pages, wizard):          # modules imported the functions by name
    mod.list_input_devices = backends.list_input_devices
    mod.list_loopback_devices = backends.list_loopback_devices
settings_pages.loopback_available = backends.loopback_available

SCENES = ["Cam Master", "Cam Mixer", "Cam Laterale", "Cam Dietro", "Cam Pubblico", "Inizio a breve"]


def pump(app, seconds):
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.processEvents()
        time.sleep(0.02)


def save(widget, name):
    path = OUT / f"{name}.png"
    widget.grab().save(str(path), "PNG", 9)
    print("  ", path.relative_to(ROOT), f"{path.stat().st_size // 1024} KB")


def scroll_to(page, fraction):
    area = page.findChild(QScrollArea)
    if area is not None:
        bar = area.verticalScrollBar()
        bar.setValue(int(bar.maximum() * fraction))


def demo_verdict(ctrl) -> None:
    """Show what the slow eye's editorial advice looks like (the demo has no model running)."""
    from dj_director.visual.judge import CameraJudgement, JudgeResult, SceneJudge
    j = SceneJudge(None, lambda: [], lambda: {})
    cams = {c.scene_name: CameraJudgement(True, 0.5, "", "") for c in ctrl.config.cameras}
    cams["Cam Pubblico"] = CameraJudgement(True, 0.9, "", "pubblico con le mani alzate", 0.8, "crowd_peak")
    j.result = JudgeResult(time.monotonic(), cams, "Cam Pubblico", "", 2.1, "qwen3-vl:4b", "Cam Pubblico", None,
                           "il pubblico ha le mani alzate, cambio netto dal mixer", ctrl.engine.current)
    j.state = "running"
    ctrl.vision.judge = j


def review_shot(ctrl) -> None:
    """The post-set review of the demo session (numbers and rule checks: no model in the demo)."""
    from dj_director.review.critic import review_session
    ctrl.stop_director()
    if ctrl._journal is not None:
        ctrl._journal.close()
    _review, html = review_session(ctrl.decision_log.path, None, ctrl.config.cameras, "it")
    # rendered by Playwright (Node), when available
    import subprocess
    js = ("const {chromium}=require('playwright');(async()=>{const b=await chromium.launch();"
          "const p=await b.newPage({viewport:{width:1240,height:900},colorScheme:'light'});"
          f"await p.goto({html.as_uri()!r});await p.waitForTimeout(500);"
          f"await p.screenshot({{path:{str(OUT / 'revisione.png')!r}}});await b.close();}})();")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    print(f"   {OUT / 'revisione.png'}" if r.returncode == 0 else f"review screenshot skipped: {r.stderr[:200]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-seconds", type=float, default=75)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    i18n.set_language("it")

    try:
        srv = FakeObsServer(port=4455, scenes=SCENES).start()     # the real default port, if free
    except OSError:
        srv = FakeObsServer(scenes=SCENES).start()
    srv.obs_version, srv.ws_version, srv.collection = "31.0.0", "5.5.0", "DJ Set"
    # synthetic camera stills: hands moving in the mixer region, crowd pulsing on the beat,
    # one camera out of focus (shows the quality veto)
    srv.scene_look = {"Cam Mixer": {"roi_motion": (0.25, 0.45, 0.5, 0.5), "motion": 0.8},
                      "Cam Pubblico": {"pulse_period": 60 / 124, "motion": 0.6},
                      "Cam Laterale": {"blur_at": time.monotonic() + 25}}     # loses focus after 25 s
    tr = generate(bpm=124)
    # the DJ kills the bass for 8 bars and brings it back (heard as a gesture by the director)
    beat = 60 / 124
    tr.audio = apply_mixer(tr.audio, tr.sr, [MixMove("low_kill", 40 * beat, 72 * beat)])
    wav_dir = Path(tempfile.mkdtemp(prefix="demo"))
    wav = wav_dir / "demo_dj_set.wav"
    sf.write(wav, tr.audio, tr.sr)

    cfg = AppConfig(first_run_done=True)
    cfg.obs.port = srv.port
    cfg.audio.backend, cfg.audio.file_path = "file", str(wav)
    cfg.cameras = [CameraConfig("Cam Master", role=CameraRole.MASTER, is_master=True),
                   CameraConfig("Cam Mixer", role=CameraRole.DETAIL),
                   CameraConfig("Cam Laterale", role=CameraRole.SIDE),
                   CameraConfig("Cam Dietro", role=CameraRole.BEHIND),
                   CameraConfig("Cam Pubblico", role=CameraRole.WIDE)]
    cfg.vision.rois = {"Cam Mixer": [0.25, 0.45, 0.5, 0.5]}
    cfg.vision.rate = "high"
    cfg.transitions.zoom = True
    cfg.transitions.special = True
    cfg.transitions.special_name = "Stinger DJ"
    ConfigManager().save(cfg)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(T.STYLESHEET)
    from dj_director.core.controller import AppController
    from dj_director.ui.main_window import MainWindow
    ctrl = AppController()
    ctrl.set_password("demo")
    srv.password = ""
    ctrl.start_services()
    win = MainWindow(ctrl)
    win.resize(1440, 920)
    win.show()
    pump(app, 2)

    def page(i):
        win.nav.setCurrentRow(i)
        pump(app, 0.3)
        win.poll()
        pump(app, 0.2)
        return win.pages[i][1]

    print("live session…")
    ctrl.start_director(shadow=False)
    pump(app, args.live_seconds)
    demo_verdict(ctrl)                       # example of the slow eye's advice (no model in the demo)
    page(0)
    save(win, "dashboard")

    page(1)
    save(win, "camere")

    p = page(2)
    results = [
        probe.ProbeResult(EXAMPLE_DEVICES[0], -14.2, -2.1, 0.86),
        probe.ProbeResult(EXAMPLE_DEVICES[1], -41.5, -28.0, 0.35),
        probe.ProbeResult(EXAMPLE_LOOPBACK[0], -120.0, -120.0, 0.0),
    ]
    p.detect._done(probe.rank(results), None)
    pump(app, 0.3)
    save(win, "audio")
    scroll_to(p, 0.55)
    pump(app, 0.3)
    save(win, "audio-rilevamento")
    scroll_to(p, 0)

    page(3)
    save(win, "obs")

    p = page(4)
    save(win, "regia")
    scroll_to(p, 0.55)
    pump(app, 0.3)
    save(win, "regia-calibrazione")
    scroll_to(p, 0)

    p = page(5)
    p.roi_cam.setCurrentText("Cam Mixer")
    pump(app, 1.0)
    win.poll()
    pump(app, 0.3)
    save(win, "visione")
    p.prov_buttons["local"].setChecked(True)
    pump(app, 1.5)
    scroll_to(p, 0.62)
    pump(app, 0.3)
    save(win, "visione-ai")
    p.prov_buttons["off"].setChecked(True)
    scroll_to(p, 0)

    p = page(6)
    p.preset.setCurrentIndex(p.preset.findData("natural"))
    p.run()
    t0 = time.time()
    while p.result is None and time.time() - t0 < 120:
        pump(app, 0.2)
    pump(app, 0.5)
    save(win, "simulazione")

    page(7)
    save(win, "decisioni")
    review_shot(ctrl)

    # --- setup wizard
    from dj_director.ui.wizard import SetupWizard
    w = SetupWizard(ctrl, win)
    w.resize(900, 680)
    w.show()
    pump(app, 0.5)
    save(w, "wizard-1-benvenuto")
    w.next()
    w.host.setText("127.0.0.1")
    w.port.setValue(srv.port)
    w.pw.setText("")
    w._test()
    pump(app, 2.0)
    save(w, "wizard-2-obs")
    w.next()
    pump(app, 0.5)
    save(w, "wizard-3-camere")
    w.next()
    pump(app, 0.5)
    w.detect._done(probe.rank(results), None)
    pump(app, 0.3)
    save(w, "wizard-4-audio")
    w.next()
    pump(app, 0.3)
    save(w, "wizard-5-stile")
    w.reject()

    ctrl.shutdown()
    srv.stop()
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
