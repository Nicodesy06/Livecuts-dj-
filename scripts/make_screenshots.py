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
from dj_director.simulation.synth import generate  # noqa: E402
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
    tr = generate(bpm=124)
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
    p.preset.setCurrentIndex(p.preset.findData("natural"))
    p.run()
    t0 = time.time()
    while p.result is None and time.time() - t0 < 120:
        pump(app, 0.2)
    pump(app, 0.5)
    save(win, "simulazione")

    page(6)
    save(win, "decisioni")

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
