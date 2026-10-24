"""Entry points: GUI (default) and command-line tools.

    dj-director                    launch the desktop application
    dj-director simulate FILE      offline replay of an audio file (or --synthetic)
    dj-director devices            list audio input / loopback devices
    dj-director obs-test           test the OBS WebSocket connection
    dj-director synth OUT.wav      write the synthetic test set to a WAV file
    dj-director fake-obs           run a fake OBS WebSocket server (needs 'websockets')
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _cmd_simulate(a: argparse.Namespace) -> int:
    from dj_director.config.manager import ConfigManager
    from dj_director.config.schema import DirectorConfig
    from dj_director.simulation.replay import default_cameras, format_report, load_audio, run_replay
    from dj_director.simulation.synth import generate

    beat_times, sections = None, []
    if a.synthetic or not a.file:
        tr = generate(bpm=a.bpm)
        audio, sr, beat_times = tr.audio, tr.sr, tr.beat_times
        sections = [s for _, s, _ in tr.sections[1:]]
        src = f"synthetic set @ {a.bpm} BPM"
    else:
        audio, sr = load_audio(a.file)
        src = a.file
    cfg = ConfigManager().load() if a.use_config else None
    cams = cfg.cameras if cfg and cfg.cameras else default_cameras(a.cameras)
    director = cfg.director if cfg else DirectorConfig()
    if a.preset:
        director.preset = a.preset
    analysis = cfg.analysis if cfg else None
    r = run_replay(audio, sr, cams, director, analysis, seed=a.seed, beat_times=beat_times,
                   section_starts=sections, log_holds=a.holds)
    print(f"Source: {src}  ({r.duration:.0f}s, preset {director.preset}, {len(cams)} cameras)\n")
    print(format_report(r))
    print("\nTimeline:")
    for s in r.shots:
        print(f"  {s.start:7.1f}s  {s.duration:5.1f}s  {s.camera:20s} [{s.timing or '-':9s}] {s.reason}")
    if a.json:
        r.export_json(Path(a.json))
        print(f"\nJSON written to {a.json}")
    if a.csv:
        r.export_csv(Path(a.csv))
        print(f"CSV written to {a.csv}")
    return 0


def _cmd_devices(a: argparse.Namespace) -> int:
    from dj_director.audio.backends import list_input_devices, list_loopback_devices, loopback_available
    devs = list_input_devices()
    print("Input devices:" if devs else "No input devices found (is PortAudio available?)")
    for d in devs:
        print(f"  [{d.index:2d}] {d.label}  default {d.default_sample_rate:.0f} Hz")
    if loopback_available():
        print("WASAPI loopback devices:")
        for d in list_loopback_devices():
            print(f"  [{d.index:2d}] {d.label}")
    else:
        print("WASAPI loopback: unavailable (Windows + PyAudioWPatch required)")
    return 0


def _cmd_obs_test(a: argparse.Namespace) -> int:
    from dj_director.obs.controller import ObsController
    ok, msg, scenes = ObsController.test_connection(a.host, a.port, a.password)
    print(("OK: " if ok else "FAILED: ") + msg)
    for s in scenes:
        print("  scene:", s)
    return 0 if ok else 1


def _cmd_synth(a: argparse.Namespace) -> int:
    import soundfile as sf
    from dj_director.simulation.synth import generate
    tr = generate(bpm=a.bpm)
    sf.write(a.out, tr.audio, tr.sr)
    meta = Path(a.out).with_suffix(".json")
    meta.write_text(json.dumps({"bpm": tr.bpm, "sections": tr.sections,
                                "beats": [round(b, 4) for b in tr.beat_times]}, indent=1))
    print(f"wrote {a.out} ({len(tr.audio) / tr.sr:.0f}s) and {meta}")
    return 0


def _cmd_fake_obs(a: argparse.Namespace) -> int:
    from dj_director.obs import fake_server
    sys.argv = [sys.argv[0], "--port", str(a.port), "--password", a.password]
    fake_server.main()
    return 0


def _cmd_selftest(a: argparse.Namespace) -> int:
    """Headless verification of a build: imports, DSP, director, OBS client, Qt. Exit 0 = OK."""
    import os
    import tempfile
    import time
    report = []

    def check(name, fn):
        t0 = time.perf_counter()
        try:
            detail = fn() or ""
            report.append(f"OK    {name} ({(time.perf_counter() - t0) * 1000:.0f} ms) {detail}")
            return True
        except Exception as e:
            report.append(f"FAIL  {name}: {e!r}")
            return False

    def dsp():
        from dj_director.simulation.replay import run_replay
        from dj_director.simulation.synth import Section, generate
        tr = generate(bpm=126, sections=[Section("g", 24, ("kick", "hat", "clap", "bass"))])
        r = run_replay(tr.audio, tr.sr, baselines=False)
        bpm = r.states[-1].bpm
        assert abs(bpm - 126) < 1.5, f"bpm {bpm}"
        assert r.shots, "no shots"
        return f"bpm={bpm:.1f} shots={len(r.shots)}"

    def audio_io():
        import sounddevice as sd
        import soundfile as sf
        n = len([d for d in sd.query_devices() if d["max_input_channels"] > 0])
        return f"PortAudio ok, {n} input devices, libsndfile {sf.__libsndfile_version__}"

    def config():
        os.environ["DJ_DIRECTOR_HOME"] = tempfile.mkdtemp()
        from dj_director.config.manager import ConfigManager
        from dj_director.config.secrets import SecretStore
        m = ConfigManager()
        m.save(m.load())
        s = SecretStore()
        s.set("t", "x")
        assert s.get("t") == "x"
        return "secrets " + ("DPAPI" if s.protected else "unprotected (non-Windows)")

    def obs_client():
        from dj_director.obs.controller import ObsController
        ok, msg, _ = ObsController.test_connection("127.0.0.1", 1, "", timeout=0.5)
        assert not ok
        return "client handles unreachable OBS"

    def qt():
        from PySide6.QtWidgets import QApplication, QLabel
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        from dj_director.ui import theme
        app.setStyleSheet(theme.STYLESHEET)
        w = QLabel("selftest")
        w.show()
        app.processEvents()
        return f"Qt {__import__('PySide6').__version__}"

    ok = all([check("config + secrets", config), check("audio libraries", audio_io),
              check("analysis + director", dsp), check("OBS client", obs_client), check("Qt GUI", qt)])
    text = "\n".join(report) + ("\nSELFTEST PASSED" if ok else "\nSELFTEST FAILED")
    print(text)
    if a.out:
        Path(a.out).write_text(text, "utf-8")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dj-director", description="DJ Director AI")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--version", action="version", version=f"DJ Director AI {__import__('dj_director').__version__}")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("simulate", help="offline replay of an audio file through the director")
    s.add_argument("file", nargs="?")
    s.add_argument("--synthetic", action="store_true")
    s.add_argument("--bpm", type=float, default=124.0)
    s.add_argument("--preset", choices=["minimal", "natural", "dynamic"])
    s.add_argument("--cameras", type=int, default=3)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--use-config", action="store_true", help="use cameras/settings from the saved config")
    s.add_argument("--holds", action="store_true", help="include HOLD decisions in exports")
    s.add_argument("--json")
    s.add_argument("--csv")
    sub.add_parser("devices", help="list audio devices")
    o = sub.add_parser("obs-test", help="test OBS WebSocket connection")
    o.add_argument("--host", default="127.0.0.1")
    o.add_argument("--port", type=int, default=4455)
    o.add_argument("--password", default="")
    y = sub.add_parser("synth", help="write the synthetic test set to a WAV file")
    y.add_argument("out")
    y.add_argument("--bpm", type=float, default=124.0)
    t = sub.add_parser("selftest", help="headless verification of an installation/build")
    t.add_argument("--out", help="also write the report to this file")
    f = sub.add_parser("fake-obs", help="run a fake OBS WebSocket server for testing")
    f.add_argument("--port", type=int, default=4455)
    f.add_argument("--password", default="")
    a = ap.parse_args(argv)
    from dj_director.diagnostics.logging_setup import setup_logging
    if a.cmd is None:
        return main_gui(verbose=a.verbose)
    setup_logging(a.verbose)
    return {"simulate": _cmd_simulate, "devices": _cmd_devices, "obs-test": _cmd_obs_test,
            "synth": _cmd_synth, "fake-obs": _cmd_fake_obs, "selftest": _cmd_selftest}[a.cmd](a)


def main_gui(verbose: bool = False) -> int:
    from dj_director.diagnostics.logging_setup import setup_logging
    setup_logging(verbose)
    from dj_director.ui.main_window import run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
