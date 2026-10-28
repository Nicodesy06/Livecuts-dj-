"""Before / after: the previous release's director vs the current one, on the same material.

The "before" engine is the real code of a git ref (default ``v0.3.0``), checked
out in a temporary worktree and run in its own process. Both engines get the
same audio, the same cameras (framing metadata is simply ignored by older
releases) and the same preset and seed. Every metric is computed by the
current code, so the comparison uses one yardstick.

    python scripts/compare_engines.py                       # synthetic 12.8-min mix, framed cameras
    python scripts/compare_engines.py set.wav --cameras default --preset natural
    python scripts/compare_engines.py --before v0.1.3 --json out.json

What it can show: rule violations (jump cuts, axis crossings, caps, A-B-A-B,
minimum lengths), rhythm and time distribution. What it cannot show: whether
the result *looks* professional - that needs watching real recordings.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CHILD = r"""
import json, sys
import numpy as np, soundfile as sf
from dj_director.config.schema import CameraConfig, DirectorConfig, from_dict
from dj_director.simulation.replay import run_replay
spec = json.loads(sys.stdin.read())
audio, sr = sf.read(spec["audio"], dtype="float32")
if audio.ndim > 1:
    audio = audio.mean(axis=1)
cams = [from_dict(CameraConfig, c) for c in spec["cameras"]]
r = run_replay(audio, sr, cams, DirectorConfig(preset=spec["preset"]), seed=spec["seed"], baselines=False)
print(json.dumps([[s.camera, s.start, s.end, s.reason] for s in r.shots]))
"""


def run_before(ref: str, audio_path: Path, cams: list[dict], preset: str, seed: int) -> list:
    with tempfile.TemporaryDirectory(prefix="djd-before-") as tmp:
        wt = Path(tmp) / "wt"
        subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach", str(wt), ref], check=True,
                       capture_output=True)
        try:
            env = {"PYTHONPATH": str(wt / "src"), "QT_QPA_PLATFORM": "offscreen", "PATH": "/usr/bin:/bin"}
            import os
            env = {**os.environ, **env}
            out = subprocess.run([sys.executable, "-c", CHILD], input=json.dumps(
                {"audio": str(audio_path), "cameras": cams, "preset": preset, "seed": seed}),
                capture_output=True, text=True, env=env, cwd=tmp, check=True)
        finally:
            subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(wt)], capture_output=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def main() -> int:
    import dataclasses

    import numpy as np
    import soundfile as sf

    from dj_director.config.presets import resolve_params
    from dj_director.config.schema import DirectorConfig, to_dict
    from dj_director.director.cameras import CameraRegistry
    from dj_director.director.continuity import sequence_report
    from dj_director.director.engine import MIX_SHOT_RANGE, DirectorEngine, EngineSettings
    from dj_director.simulation.metrics import ShotRecord, compute_metrics
    from dj_director.simulation.replay import default_cameras, framed_cameras, run_replay
    from dj_director.simulation.synth import generate_mix

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("audio", nargs="?", help="WAV/FLAC set (default: synthetic 12.8-min mix)")
    ap.add_argument("--before", default="v0.3.0")
    ap.add_argument("--cameras", choices=["framed", "default"], default="framed")
    ap.add_argument("--preset", default="natural")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--json")
    a = ap.parse_args()

    tmpdir = Path(tempfile.mkdtemp(prefix="djd-cmp-"))
    if a.audio:
        audio_path = Path(a.audio).resolve()
        audio, sr = sf.read(str(audio_path), dtype="float32")
        audio = audio.mean(axis=1) if audio.ndim > 1 else audio
        label = audio_path.name
    else:
        mix = generate_mix()
        audio, sr = mix.audio, mix.sr
        audio_path = tmpdir / "mix.wav"
        sf.write(str(audio_path), audio, sr)
        label = "synthetic mix"
    cams = framed_cameras() if a.cameras == "framed" else default_cameras(4)

    # yardstick: the current definitions of every limit, for both engines
    reg = CameraRegistry.from_configs(cams)
    eng = DirectorEngine(reg, resolve_params(DirectorConfig(preset=a.preset)), EngineSettings())
    specs = {c.name: eng.spec(c) for c in reg}
    lo, hi = MIX_SHOT_RANGE
    yard = dict(min_durations={n: s.min for n, s in specs.items()}, cooldowns={c.name: c.cooldown for c in reg},
                classes={n: s.cls for n, s in specs.items()}, caps={n: s.cap for n, s in specs.items()},
                targets=eng.coverage_targets(), window=eng.p.coverage_window,
                gesture_cap=min(max(eng.p.mix_shot_cap, lo), hi))
    master = reg.master().name
    cams_by_name = {c.name: c for c in reg}

    def measure(name: str, shots: list[ShotRecord]):
        m = compute_metrics(name, shots, master, yard["min_durations"], yard["cooldowns"], None, (), None,
                            yard["classes"], yard["caps"], yard["targets"], yard["window"], yard["gesture_cap"])
        return dataclasses.replace(m, **sequence_report([s.camera for s in shots], cams_by_name))

    before_raw = run_before(a.before, audio_path, [to_dict(c) for c in cams], a.preset, a.seed)
    before = measure(f"{a.before}", [ShotRecord(c, s, e, r) for c, s, e, r in before_raw])
    r = run_replay(audio, sr, cams, DirectorConfig(preset=a.preset), seed=a.seed, baselines=False)
    after = measure("current", r.shots)

    keys = ["cuts_per_minute", "master_share", "median_shot_s", "jump_cuts", "axis_crossings",
            "repeated_transitions", "abab_patterns", "hard_cap_violations", "min_duration_violations",
            "cooldown_violations", "repeated_intervals", "coverage_max_deviation"]
    print(f"{label} · cameras: {a.cameras} · preset {a.preset} · seed {a.seed}")
    print("metric".ljust(26) + before.name.rjust(12) + after.name.rjust(12))
    for k in keys:
        print(k.ljust(26) + str(getattr(before, k)).rjust(12) + str(getattr(after, k)).rjust(12))
    for cls in ("master", "detail", "side"):
        print(f"coverage_{cls}".ljust(26) + str(before.coverage.get(cls, "-")).rjust(12)
              + str(after.coverage.get(cls, "-")).rjust(12))
    if a.json:
        Path(a.json).write_text(json.dumps({"before": dataclasses.asdict(before), "after": dataclasses.asdict(after),
                                            "before_shots": before_raw,
                                            "after_shots": [[s.camera, s.start, s.end, s.reason] for s in r.shots]},
                                           indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
