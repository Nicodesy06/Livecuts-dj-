"""Offline, deterministic replay: audio -> analysis -> director -> virtual OBS.

Runs faster than real time on a :class:`SimClock` using exactly the same
pipeline and engine classes as the live application. Used by the GUI
"Simulation" page, the CLI ``simulate`` command and the quality harness.
"""

from __future__ import annotations

import csv
import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np

from dj_director.config.presets import resolve_params
from dj_director.config.schema import (AnalysisConfig, CameraConfig, CameraRole, DirectorConfig,
                                       to_dict)
from dj_director.core.events import DirectorAction, DirectorDecisionEvent
from dj_director.director.cameras import CameraRegistry
from dj_director.director.continuity import sequence_report
from dj_director.director.engine import MIX_SHOT_RANGE, DirectorEngine, EngineSettings
from dj_director.music.analyzer import AnalysisPipeline
from dj_director.simulation.metrics import (EditorialMetrics, ShotRecord, compute_metrics,
                                            random_baseline, timer_baseline)

TICK = 0.05   # director tick in simulation, s (live uses the same)


def default_cameras(n: int = 3) -> list[CameraConfig]:
    roles = [CameraRole.MASTER, CameraRole.DETAIL, CameraRole.SIDE, CameraRole.BEHIND,
             CameraRole.WIDE]
    names = ["Cam Master", "Cam Mixer Detail", "Cam Side", "Cam Behind", "Cam Wide"]
    return [CameraConfig(scene_name=names[i], role=roles[i], is_master=(i == 0))
            for i in range(min(n, len(roles)))]


def framed_cameras() -> list[CameraConfig]:
    """A realistic 4-camera booth with framing metadata and one digital crop (5 OBS scenes).

    CAM1 is a front camera used twice: a wide master and a medium crop (same source).
    """
    from dj_director.config.schema import ScreenDirection, ShotSize, Subject, ViewAngle
    S, J, V = ShotSize, Subject, ViewAngle
    return [
        CameraConfig("CAM1 Wide", role=CameraRole.MASTER, is_master=True, shot_size=S.WIDE, subject=J.DJ,
                     view_angle=V.FRONT, source_group="cam1"),
        CameraConfig("CAM1 Medium", role=CameraRole.SIDE, shot_size=S.MEDIUM, subject=J.DJ, view_angle=V.FRONT,
                     source_group="cam1", crop_zoom=1.6),
        CameraConfig("CAM2 Mixer", role=CameraRole.DETAIL, shot_size=S.CLOSE, subject=J.HANDS,
                     view_angle=V.TOP, source_group="cam2"),
        CameraConfig("CAM3 Side", role=CameraRole.SIDE, shot_size=S.MEDIUM, subject=J.DJ, view_angle=V.LEFT,
                     source_group="cam3", screen_direction=ScreenDirection.RIGHT),
        CameraConfig("CAM4 Behind", role=CameraRole.BEHIND, shot_size=S.WIDE, subject=J.CROWD,
                     view_angle=V.BACK, source_group="cam4"),
    ]


@dataclass
class StatePoint:
    t: float
    state: str
    confidence: float
    bpm: float
    beat_confidence: float
    level_db: float


@dataclass
class SimResult:
    shots: list[ShotRecord]
    decisions: list[DirectorDecisionEvent]
    states: list[StatePoint]
    events: list[dict]
    metrics: EditorialMetrics
    baselines: dict[str, EditorialMetrics] = field(default_factory=dict)
    duration: float = 0.0

    def export_json(self, path: Path) -> None:
        data = {
            "duration": self.duration,
            "metrics": self.metrics.as_dict(),
            "baselines": {k: v.as_dict() for k, v in self.baselines.items()},
            "shots": [dataclasses.asdict(s) for s in self.shots],
            "decisions": [_decision_dict(d) for d in self.decisions],
            "music_events": self.events,
            "states": [dataclasses.asdict(s) for s in self.states],
        }
        Path(path).write_text(json.dumps(data, indent=1), "utf-8")

    def export_csv(self, path: Path) -> None:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["t", "action", "current", "candidate", "reason", "confidence", "music_state",
                        "opportunity", "timing", "target_t", "time_on_shot", "correlation_id"])
            for d in self.decisions:
                w.writerow([f"{d.t:.3f}", d.action.value, d.current_camera, d.candidate_camera,
                            d.reason, d.confidence, d.music_state.value, d.opportunity,
                            d.timing.value if d.timing else "", f"{d.target_t:.3f}",
                            d.time_on_shot, d.correlation_id])


def _decision_dict(d: DirectorDecisionEvent) -> dict:
    out = to_dict(dataclasses.asdict(d))
    out["action"] = d.action.value
    out["music_state"] = d.music_state.value
    out["timing"] = d.timing.value if d.timing else None
    return out


def load_audio(path: str) -> tuple[np.ndarray, int]:
    import soundfile as sf
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    return data.mean(axis=1), int(sr)


def run_replay(audio: np.ndarray, sr: int, cameras: Sequence[CameraConfig] | None = None,
               director: DirectorConfig | None = None, analysis: AnalysisConfig | None = None,
               seed: int = 7, beat_times: Optional[np.ndarray] = None,
               section_starts: Sequence[float] = (), baselines: bool = True,
               phrase_times: Optional[np.ndarray] = None,
               log_holds: bool = False,
               progress: Optional[Callable[[float], bool]] = None) -> SimResult:
    """Run the full chain on ``audio``. ``progress(frac)`` may return False to abort."""
    cameras = list(cameras or default_cameras())
    director = director or DirectorConfig()
    analysis = analysis or AnalysisConfig()
    reg = CameraRegistry.from_configs([c for c in cameras if c.enabled])
    settings = EngineSettings(beats_per_bar=analysis.beats_per_bar,
                              latency_offset=director.latency_offset_ms / 1000.0,
                              override_policy=director.override_policy,
                              override_seconds=director.override_seconds,
                              no_signal_policy=director.no_signal_policy)
    engine = DirectorEngine(reg, resolve_params(director), settings, seed=seed)
    pipe = AnalysisPipeline(sr, analysis, lambda s: s / sr)

    decisions: list[DirectorDecisionEvent] = []
    states: list[StatePoint] = []
    music_events: list[dict] = []
    shots: list[ShotRecord] = []
    block = int(sr * TICK)
    next_state_sample = 0.0
    last_hold_reason = None
    total = len(audio) / sr
    for i in range(0, len(audio), block):
        pipe.push(audio[i:i + block], i)
        now = (i + block) / sr
        evs = pipe.state.drain_events()
        for e in evs:
            music_events.append({"t": round(e.t, 3), "kind": e.kind, "confidence": round(e.confidence, 3),
                                 "detail": e.detail})
        snap = pipe.snapshot(tuple(evs))
        d = engine.tick(now, snap, evs)
        if d.action == DirectorAction.SWITCH:
            if shots:
                shots[-1].end = now
            shots.append(ShotRecord(d.candidate_camera, now, total, d.reason,
                                    d.timing.value if d.timing else "", d.target_t))
        if d.action != DirectorAction.HOLD or (log_holds and d.reason != last_hold_reason):
            decisions.append(d)
        last_hold_reason = d.reason if d.action == DirectorAction.HOLD else None
        if now >= next_state_sample:
            next_state_sample += 1.0
            states.append(StatePoint(round(now, 2), snap.state.value, round(snap.state_confidence, 3),
                                     round(snap.beat.bpm, 2), round(snap.beat.confidence, 3),
                                     round(snap.features.rms_db, 1)))
        if progress is not None and i % (block * 40) == 0 and progress(i / len(audio)) is False:
            break
    if shots:
        shots[-1].end = max(shots[-1].start, min(total, now))

    master = reg.master().name if reg.master() else None
    specs = {c.name: engine.spec(c) for c in reg}
    mins = {n: sp.min for n, sp in specs.items()}
    cools = {c.name: c.cooldown for c in reg}
    classes = {n: sp.cls for n, sp in specs.items()}
    caps = {n: sp.cap for n, sp in specs.items()}
    lo, hi = MIX_SHOT_RANGE
    gcap = min(max(engine.p.mix_shot_cap, lo), hi)
    targets = engine.coverage_targets()
    metrics = compute_metrics("music-aware", shots, master, mins, cools, beat_times, section_starts,
                              phrase_times, classes, caps, targets, engine.p.coverage_window, gcap)
    cams = {c.name: c for c in reg}

    def with_continuity(m, seq):
        return dataclasses.replace(m, **sequence_report([s.camera for s in seq], cams))
    metrics = with_continuity(metrics, shots)
    result = SimResult(shots, decisions, states, music_events, metrics, duration=total)
    if baselines:
        names = [c.name for c in reg]
        tb, rb = timer_baseline(names, total), random_baseline(names, total, seed)
        result.baselines["timer-12s"] = with_continuity(compute_metrics(
            "timer-12s", tb, master, mins, cools, None, section_starts,
            phrase_times, classes, caps, targets, engine.p.coverage_window, gcap), tb)
        result.baselines["random"] = with_continuity(compute_metrics(
            "random", rb, master, mins, cools, None, section_starts,
            phrase_times, classes, caps, targets, engine.p.coverage_window, gcap), rb)
    return result


def format_report(r: SimResult) -> str:
    rows = [r.metrics] + list(r.baselines.values())
    keys = ["cuts_per_minute", "master_share", "mean_shot_s", "median_shot_s", "shortest_shot_s",
            "longest_shot_s", "duration_cv", "abab_patterns", "min_duration_violations",
            "cooldown_violations", "max_consecutive_inserts", "beat_synced_cuts",
            "beat_alignment_median_ms", "cuts_near_section_change", "cuts_on_phrase_grid",
            "hard_cap_violations", "repeated_intervals", "coverage_max_deviation", "jump_cuts",
            "axis_crossings", "repeated_transitions"]
    w = 26
    lines = ["metric".ljust(w) + "".join(m.name.rjust(14) for m in rows)]
    for k in keys:
        lines.append(k.ljust(w) + "".join(str(getattr(m, k)).rjust(14) for m in rows))
    for cls in ("master", "detail", "side"):
        lines.append(f"coverage_{cls}".ljust(w) + "".join(str(m.coverage.get(cls, "-")).rjust(14) for m in rows))
        lines.append(f"median_{cls}_s".ljust(w) + "".join(str(m.median_by_class.get(cls, "-")).rjust(14)
                                                          for m in rows))
        lines.append(f"max_{cls}_s".ljust(w) + "".join(str(m.max_by_class.get(cls, "-")).rjust(14) for m in rows))
    return "\n".join(lines)
