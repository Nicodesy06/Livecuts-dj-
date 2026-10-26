"""Editorial quality metrics and baseline directors.

These numbers describe *behaviour* (pace, balance, repetition, rule
violations, beat alignment). They do not measure artistic quality on their
own; human review of the resulting timeline is still required.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import asdict, dataclass, field
from typing import Optional, Sequence

import numpy as np


@dataclass
class ShotRecord:
    camera: str
    start: float
    end: float
    reason: str = ""
    timing: str = ""
    target_t: float = 0.0

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class EditorialMetrics:
    name: str
    duration_s: float
    cuts: int
    cuts_per_minute: float
    master_share: float
    mean_shot_s: float
    median_shot_s: float
    shortest_shot_s: float
    longest_shot_s: float
    duration_cv: float               # coefficient of variation (0 = identical intervals)
    abab_patterns: int
    min_duration_violations: int
    cooldown_violations: int
    max_consecutive_inserts: int
    beat_synced_cuts: int
    beat_alignment_median_ms: Optional[float]
    cuts_near_section_change: int    # cuts within 2 s after a ground-truth section boundary
    cuts_on_phrase_grid: int = 0     # cuts within 60 ms of a ground-truth 4-bar line
    share_by_camera: dict[str, float] = field(default_factory=dict)
    coverage: dict[str, float] = field(default_factory=dict)          # share per coverage class
    coverage_max_deviation: Optional[float] = None   # worst |rolling share - target| (sliding window)
    hard_cap_violations: int = 0
    median_by_class: dict[str, float] = field(default_factory=dict)
    max_by_class: dict[str, float] = field(default_factory=dict)
    repeated_intervals: int = 0      # consecutive cut intervals within 0.5 s of each other

    def as_dict(self) -> dict:
        return asdict(self)


def compute_metrics(name: str, shots: Sequence[ShotRecord], master: Optional[str],
                    min_durations: dict[str, float], cooldowns: dict[str, float],
                    beat_times: Optional[np.ndarray] = None,
                    section_starts: Sequence[float] = (),
                    phrase_times: Optional[np.ndarray] = None,
                    classes: Optional[dict[str, str]] = None,
                    caps: Optional[dict[str, float]] = None,
                    targets: Optional[dict[str, float]] = None,
                    window: float = 300.0, gesture_cap: Optional[float] = None) -> EditorialMetrics:
    shots = [s for s in shots if s.duration > 0]
    if not shots:
        return EditorialMetrics(name, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, None, 0)
    total = shots[-1].end - shots[0].start
    durs = [s.duration for s in shots]
    cuts = len(shots) - 1
    seq = [s.camera for s in shots]
    abab = sum(1 for i in range(len(seq) - 3)
               if seq[i] == seq[i + 2] and seq[i + 1] == seq[i + 3])
    # the final shot is truncated by the end of the set: exclude it from min-length checks
    min_viol = sum(1 for s in shots[:-1] if s.duration + 1e-6 < min_durations.get(s.camera, 0))
    last_end: dict[str, float] = {}
    cool_viol = 0
    run = best_run = 0
    for s in shots:
        if s.camera != master:
            if s.camera in last_end and s.start - last_end[s.camera] < cooldowns.get(s.camera, 0) - 1e-6:
                cool_viol += 1
            run += 1
            best_run = max(best_run, run)
        else:
            run = 0
        last_end[s.camera] = s.end
    share: dict[str, float] = {}
    for s in shots:
        share[s.camera] = share.get(s.camera, 0.0) + s.duration / total
    synced = [s for s in shots[1:] if s.timing in ("beat", "downbeat", "phrase")]
    align = None
    if beat_times is not None and len(beat_times) and synced:
        errs = [float(np.min(np.abs(beat_times - s.target_t))) * 1000 for s in synced]
        align = float(np.median(errs))
    near = 0
    for s in shots[1:]:
        if any(0 <= s.start - b <= 2.0 for b in section_starts):
            near += 1
    on_grid = 0
    if phrase_times is not None and len(phrase_times):
        on_grid = sum(1 for s in shots[1:] if float(np.min(np.abs(phrase_times - (s.target_t or s.start)))) < 0.06)
    cov: dict[str, float] = {}
    med: dict[str, float] = {}
    mx: dict[str, float] = {}
    dev = None
    cap_viol = 0
    if classes:
        by_cls: dict[str, list[float]] = {}
        for s in shots:
            c = classes.get(s.camera, "other")
            cov[c] = cov.get(c, 0.0) + s.duration / total
            by_cls.setdefault(c, []).append(s.duration)
        for c, ds in by_cls.items():
            inner = ds[1:-1] if len(ds) > 2 else ds     # first/last shots are truncated by the set
            med[c] = round(statistics.median(inner), 1)
            mx[c] = round(max(inner), 1)
        cov = {k: round(v, 3) for k, v in cov.items()}
        if targets and total >= window * 1.5:
            worst = 0.0
            t = shots[0].start + window
            while t <= shots[-1].end:
                acc: dict[str, float] = {}
                for s in shots:
                    o = min(s.end, t) - max(s.start, t - window)
                    if o > 0:
                        acc[classes.get(s.camera, "other")] = acc.get(classes.get(s.camera, "other"), 0) + o
                for k, tgt in targets.items():
                    worst = max(worst, abs(acc.get(k, 0.0) / window - tgt))
                t += 30.0
            dev = round(worst, 3)
    if caps:
        def cap(s: ShotRecord) -> float:
            # a mixer shot opened by a DJ gesture has its own cap (mix_shot_cap)
            if gesture_cap and s.reason.startswith(("entry: DJ ", "exit: DJ ")):
                return gesture_cap
            return caps.get(s.camera, float("inf"))
        cap_viol = sum(1 for s in shots[:-1] if s.duration > cap(s) + 0.25)
    gaps = [b.start - a.start for a, b in zip(shots, shots[1:])]
    repeated = sum(1 for g1, g2 in zip(gaps, gaps[1:]) if abs(g1 - g2) < 0.5)
    mean = statistics.fmean(durs)
    return EditorialMetrics(
        name=name, duration_s=round(total, 1), cuts=cuts,
        cuts_per_minute=round(cuts / (total / 60), 2) if total > 0 else 0,
        master_share=round(share.get(master, 0.0), 3) if master else 0.0,
        mean_shot_s=round(mean, 1), median_shot_s=round(statistics.median(durs), 1),
        shortest_shot_s=round(min(durs[:-1] or durs), 1), longest_shot_s=round(max(durs), 1),
        duration_cv=round(statistics.pstdev(durs) / mean, 3) if mean else 0.0,
        abab_patterns=abab, min_duration_violations=min_viol, cooldown_violations=cool_viol,
        max_consecutive_inserts=best_run, beat_synced_cuts=len(synced),
        beat_alignment_median_ms=None if align is None else round(align, 1),
        cuts_near_section_change=near, cuts_on_phrase_grid=on_grid,
        share_by_camera={k: round(v, 3) for k, v in share.items()},
        coverage=cov, coverage_max_deviation=dev, hard_cap_violations=cap_viol,
        median_by_class=med, max_by_class=mx, repeated_intervals=repeated)


# --------------------------------------------------------------------------- baselines
def timer_baseline(cameras: Sequence[str], total: float, interval: float = 12.0) -> list[ShotRecord]:
    """Fixed-interval round-robin: what we explicitly do NOT want."""
    shots, t, i = [], 0.0, 0
    while t < total:
        end = min(total, t + interval)
        shots.append(ShotRecord(cameras[i % len(cameras)], t, end, "timer"))
        t, i = end, i + 1
    return shots


def random_baseline(cameras: Sequence[str], total: float, seed: int = 1,
                    lo: float = 3.0, hi: float = 20.0) -> list[ShotRecord]:
    """Random camera, random duration: the 'slideshow switcher'."""
    rng = random.Random(seed)
    shots, t, cur = [], 0.0, None
    while t < total:
        cur = rng.choice([c for c in cameras if c != cur] or list(cameras))
        end = min(total, t + rng.uniform(lo, hi))
        shots.append(ShotRecord(cur, t, end, "random"))
        t = end
    return shots
