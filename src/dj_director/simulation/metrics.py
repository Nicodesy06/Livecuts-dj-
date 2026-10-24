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

    def as_dict(self) -> dict:
        return asdict(self)


def compute_metrics(name: str, shots: Sequence[ShotRecord], master: Optional[str],
                    min_durations: dict[str, float], cooldowns: dict[str, float],
                    beat_times: Optional[np.ndarray] = None,
                    section_starts: Sequence[float] = (),
                    phrase_times: Optional[np.ndarray] = None) -> EditorialMetrics:
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
        share_by_camera={k: round(v, 3) for k, v in share.items()})


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
