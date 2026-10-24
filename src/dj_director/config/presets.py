"""Director presets.

Presets are parameter sets for the *same* engine, not separate behaviours.
Natural is the default and targets the "discreet professional director" feel:
long master shots (typically 30-90 s), short inserts, cuts mostly anchored to
musical boundaries, no forced variety.
"""

from __future__ import annotations

import dataclasses

from dj_director.config.schema import DirectorConfig, DirectorParams

PRESETS: dict[str, DirectorParams] = {
    "minimal": DirectorParams(
        cut_threshold=1.0,
        master_duration_scale=1.6,
        insert_duration_scale=1.15,
        duration_spread=0.3,
        min_cut_gap=8.0,
        max_cuts_per_minute=3.0,
        max_consecutive_inserts=1,
        music_trigger_gain=0.6,
        musical_cut_confidence=0.65,
        hysteresis=0.35,
        variety_jitter=0.1,
    ),
    "natural": DirectorParams(),
    "dynamic": DirectorParams(
        cut_threshold=0.8,
        master_duration_scale=0.6,
        insert_duration_scale=0.8,
        duration_spread=0.4,
        min_cut_gap=3.0,
        max_cuts_per_minute=9.0,
        max_consecutive_inserts=3,
        music_trigger_gain=1.3,
        musical_cut_confidence=0.5,
        hysteresis=0.18,
        variety_jitter=0.2,
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
