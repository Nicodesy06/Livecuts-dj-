"""Director presets.

Presets are parameter sets for the *same* engine, not separate behaviours.
Natural is the default: master shots generally 20-50 s, Detail inserts 6-10 s
(hard cap 14 s), Side/Behind inserts 10-18 s (hard cap 22 s), and a coverage
budget of 70 % master / 15 % detail / 15 % side over a 5-minute sliding window.
All presets share the same coverage targets; they differ in pace.
"""

from __future__ import annotations

import dataclasses

from dj_director.config.schema import DirectorConfig, DirectorParams

PRESETS: dict[str, DirectorParams] = {
    "minimal": DirectorParams(
        cut_threshold=1.0,
        master_duration_scale=1.4,
        insert_duration_scale=1.0,
        duration_spread=0.8,
        min_cut_gap=8.0,
        max_cuts_per_minute=3.0,
        max_consecutive_inserts=1,
        music_trigger_gain=0.6,
        musical_cut_confidence=0.65,
        hysteresis=0.35,
        variety_jitter=0.1,
        max_phrase_wait=12.0,
        coverage_gain=0.6,
    ),
    "natural": DirectorParams(),
    "dynamic": DirectorParams(
        cut_threshold=0.85,
        master_duration_scale=0.75,
        insert_duration_scale=0.9,
        duration_spread=1.0,
        min_cut_gap=3.0,
        max_cuts_per_minute=9.0,
        max_consecutive_inserts=2,
        music_trigger_gain=1.3,
        musical_cut_confidence=0.5,
        hysteresis=0.18,
        variety_jitter=0.2,
        max_phrase_wait=5.0,
        coverage_gain=1.2,
    ),
}

PRESET_DESCRIPTIONS = {
    "minimal": "Very conservative. Long shots, few inserts, returns to the master quickly.",
    "natural": "Balanced, musical, professional. Recommended for most DJ sets.",
    "dynamic": "More variation and shorter shots, still never cutting on every beat.",
}


def resolve_params(cfg: DirectorConfig) -> DirectorParams:
    base = PRESETS.get(cfg.preset, PRESETS["natural"])
    return dataclasses.replace(base, **cfg.overrides)
